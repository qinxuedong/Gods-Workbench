# Copyright 2026 Gods-Workbench Authors
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""视频执行服务：New API 远端生成与白名单 FFmpeg 本地渲染。"""
from __future__ import annotations
import concurrent.futures
import http.client
import ipaddress
import json
import math
import os
from pathlib import Path
import shutil
import socket
import subprocess
import tempfile
import threading
import time
from typing import Any, Dict, Optional, Tuple
from urllib.parse import quote, urljoin, urlsplit

from gw.asset_registry import repository as asset_repo
from gw.core import storage as core_storage
from gw.core.errors import CleanroomException
from gw.god_canvas.service import default_god_canvas_service
from gw.projects_hub.service import default_projects_service
from gw.settings import execution_config
from .store import VideoTaskStore, actor_key as auth_actor_key

VIDEO_SUFFIXES = frozenset({".mp4", ".mov", ".mkv", ".avi", ".webm", ".m4v"})
RATIO_DIMENSIONS = {"16:9": (1280, 720), "9:16": (720, 1280), "1:1": (1024, 1024)}
EXPORT_PRESETS = {"h264_720p_30fps": (1280, 720, 30), "h264_1080p_30fps": (1920, 1080, 30)}
MAX_VIDEO_BYTES = 512 * 1024 * 1024
MAX_REMOTE_WAIT = 30 * 60


def _err(code: str, message: str, status: int = 503) -> CleanroomException:
    return CleanroomException(status, code, message)


def _request_key(value: Optional[str]) -> str:
    key = str(value or "").strip()
    if not key or len(key) > 128 or any(ord(char) < 0x21 or ord(char) > 0x7E for char in key):
        raise _err("IDEMPOTENCY_KEY_REQUIRED", "必须提供有效的 Idempotency-Key", 400)
    return key


def _job_view(row: Dict[str, Any]) -> Dict[str, Any]:
    result = {"job_id": row["job_id"], "operation": row["operation"], "status": row["status"],
        "poll_hint": f"/api/video-tasks/{row['job_id']}", "project_id": row["project_id"],
        "canvas_id": row["canvas_id"], "entity_id": row["entity_id"],
        "provider_id": row.get("provider_id"), "model": row.get("model"),
        "result_unknown": bool(row.get("result_unknown")), "error": None,
        "created_at": row["created_at"], "updated_at": row["updated_at"],
        "remote_cancelled": False, "remote_may_continue_or_bill": bool(row.get("remote_may_continue"))}
    if row.get("error_code"):
        result["error"] = {"code": row["error_code"], "message": row.get("error_message") or "视频任务失败"}
    artifact = row.get("artifact")
    if row["status"] == "succeeded" and artifact:
        result.update({"asset_id": artifact["asset_id"], "asset_ids": [artifact["asset_id"]],
            "content_url": f"/api/video-tasks/{row['job_id']}/content",
            "videos": [f"/api/video-tasks/{row['job_id']}/content"]})
        try:
            result["media"] = json.loads(artifact.get("media_json") or "{}")
        except (TypeError, json.JSONDecodeError):
            result["media"] = {}
    return result


def _public_ip(address: str) -> bool:
    try:
        parsed = ipaddress.ip_address(address)
        return parsed.is_global and not parsed.is_multicast and not parsed.is_unspecified
    except ValueError:
        return False


class _PinnedHTTP(http.client.HTTPConnection):
    def __init__(self, host: str, address: str, port: int, timeout: float):
        super().__init__(host, port=port, timeout=timeout)
        self.address = address
    def connect(self):
        self.sock = socket.create_connection((self.address, self.port), self.timeout)


class _PinnedHTTPS(http.client.HTTPSConnection):
    def __init__(self, host: str, address: str, port: int, timeout: float):
        super().__init__(host, port=port, timeout=timeout)
        self.address = address
    def connect(self):
        raw = socket.create_connection((self.address, self.port), self.timeout)
        self.sock = self._context.wrap_socket(raw, server_hostname=self.host)


def _public_addresses(host: str, port: int) -> list[str]:
    try:
        addresses = {item[4][0] for item in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)}
    except OSError:
        raise _err("VIDEO_DOWNLOAD_FAILED", "视频产物域名解析失败", 502) from None
    if not addresses or any(not _public_ip(address) for address in addresses):
        raise _err("VIDEO_DOWNLOAD_BLOCKED", "视频产物地址解析到非公网网络，已拒绝访问", 403)
    return sorted(addresses)


def download_provider_artifact(url: str, destination: Path, *, allow_private_for_test: bool = False) -> int:
    """拉取非可信上游 URL；钉住 DNS 解析 IP、逐跳复验且不携带上游凭据。"""
    started = time.monotonic()
    current = url
    redirects = 0
    size = 0
    created = False
    complete = False
    try:
        with destination.open("xb") as output:
            created = True
            while True:
                if time.monotonic() - started > 60:
                    raise _err("VIDEO_DOWNLOAD_TIMEOUT", "视频产物下载超时", 504)
                try:
                    parsed = urlsplit(current)
                    if parsed.scheme not in {"https", "http"} or not parsed.hostname or parsed.username or parsed.password or parsed.fragment:
                        raise _err("VIDEO_DOWNLOAD_BLOCKED", "视频产物 URL 不符合安全策略", 403)
                    if parsed.scheme != "https" and not allow_private_for_test:
                        raise _err("VIDEO_DOWNLOAD_BLOCKED", "生产视频产物仅允许 HTTPS", 403)
                    host = parsed.hostname.rstrip(".").lower()
                    port = parsed.port or (443 if parsed.scheme == "https" else 80)
                    if allow_private_for_test:
                        addresses = sorted({item[4][0] for item in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)})
                    else:
                        addresses = _public_addresses(host, port)
                    if not addresses:
                        raise _err("VIDEO_DOWNLOAD_BLOCKED", "视频产物地址不可用", 403)
                    path = parsed.path or "/"
                    if parsed.query:
                        path += "?" + parsed.query
                    timeout = max(1.0, 60 - (time.monotonic() - started))
                    conn = (_PinnedHTTPS(host, addresses[0], port, timeout) if parsed.scheme == "https"
                            else _PinnedHTTP(host, addresses[0], port, timeout))
                    # 明确不附带 Provider Bearer/环境变量密钥。
                    conn.request("GET", path, headers={"Accept": "video/*,application/octet-stream", "User-Agent": "Gods-Workbench-Video/1.0"})
                    response = conn.getresponse()
                    if response.status in {301, 302, 303, 307, 308}:
                        location = response.getheader("Location")
                        response.read(4096)
                        conn.close()
                        redirects += 1
                        if not location or redirects > 3:
                            raise _err("VIDEO_REDIRECT_BLOCKED", "视频产物重定向无效或超过限制", 403)
                        current = urljoin(current, location)
                        continue
                    if response.status != 200:
                        response.read(4096)
                        conn.close()
                        raise _err("VIDEO_DOWNLOAD_FAILED", "Provider 视频产物下载失败", 502)
                    length = response.getheader("Content-Length")
                    if length:
                        try:
                            if int(length) < 0 or int(length) > MAX_VIDEO_BYTES:
                                raise _err("VIDEO_DOWNLOAD_TOO_LARGE", "视频产物超过大小上限", 413)
                        except ValueError:
                            raise _err("VIDEO_DOWNLOAD_FAILED", "视频产物长度无效", 502) from None
                    while True:
                        if time.monotonic() - started > 60:
                            raise _err("VIDEO_DOWNLOAD_TIMEOUT", "视频产物下载超时", 504)
                        block = response.read(64 * 1024)
                        if not block:
                            break
                        size += len(block)
                        if size > MAX_VIDEO_BYTES:
                            raise _err("VIDEO_DOWNLOAD_TOO_LARGE", "视频产物超过大小上限", 413)
                        output.write(block)
                    conn.close()
                    complete = True
                    return size
                except CleanroomException:
                    raise
                except (OSError, http.client.HTTPException, ValueError):
                    raise _err("VIDEO_DOWNLOAD_FAILED", "视频产物下载失败", 502) from None
    finally:
        # 仅清理由本次请求成功创建的部分下载文件，不碰预先存在的目标。
        if created and not complete:
            try:
                destination.unlink(missing_ok=True)
            except OSError:
                pass

class VideoTaskService:
    """视频任务调度与执行。"""
    def __init__(self, store: Optional[VideoTaskStore] = None, *, allow_private_artifacts_for_test: bool = False):
        self.store = store or VideoTaskStore()
        self.allow_private_artifacts_for_test = allow_private_artifacts_for_test
        self.executor = concurrent.futures.ThreadPoolExecutor(max_workers=2, thread_name_prefix="gw-video")
        self._scheduled: set[str] = set()
        self._lock = threading.Lock()
        for job in self.store.recoverable():
            try:
                self._require_project_binding(job["project_id"], job["actor_key"])
            except CleanroomException as exc:
                # queued 状态先原子转入 running，再终止恢复，避免缺少快照的作业下次启动继续入队。
                if job.get("status") != "running" and self.store.mark_running(job["job_id"]) is None:
                    continue
                self.store.mark_interrupted(
                    job["job_id"], exc.code, "项目真源或owner关联不可验证，已停止自动恢复"
                )
                continue
            self.schedule(job["job_id"])

    @staticmethod
    def actor(context: Any) -> str:
        return auth_actor_key(context)

    def register_project_owner(self, project_id: str, context: Any) -> None:
        owner = self.actor(context)
        # 创建流程在发布前调用；此处来源owner由真实认证主体确定，执行仍需等完整真源发布。
        self.store.register_project_owner(project_id, owner, owner)

    def _project_source_owner_key(self, project_id: str) -> str:
        """从完整项目真源读取owner；只供服务端准入使用，不回显摘要。"""
        try:
            return default_projects_service._get_source_owner_key(project_id)
        except CleanroomException as exc:
            if exc.code == "PROJECT_NOT_FOUND":
                raise _err("VIDEO_PROJECT_NOT_FOUND", "项目不存在或当前主体无权访问", 404) from None
            raise _err("VIDEO_PROJECT_SOURCE_UNAVAILABLE", "项目真源无法验证，已拒绝视频操作") from None
        except Exception:
            raise _err("VIDEO_PROJECT_SOURCE_UNAVAILABLE", "项目真源无法验证，已拒绝视频操作") from None

    def _require_project_binding(self, project_id: str, owner: str) -> str:
        """以当前项目真源owner、视频ACL受权主体和请求主体三方匹配准入。"""
        source_owner_key = self._project_source_owner_key(project_id)
        try:
            self.store.require_project_owner(project_id, owner, source_owner_key)
        except CleanroomException as exc:
            if exc.code == "PROJECT_SOURCE_MISMATCH":
                raise _err("VIDEO_PROJECT_SOURCE_UNAVAILABLE", "视频授权来源与项目真源owner不匹配，已拒绝视频操作") from None
            raise
        return source_owner_key

    def claim_project(self, project_id: str, context: Any) -> None:
        if str(getattr(context, "role", "")) not in {"admin", "governor"}:
            raise _err("FORBIDDEN", "无项目生命周期治理权限", 403)
        owner = self.actor(context)
        source_owner_key = self._project_source_owner_key(project_id)
        self.store.claim_unowned_project(
            project_id, owner, owner, source_owner_key=source_owner_key
        )

    def _validate_context(self, owner: str, project_id: str, canvas_id: str, entity_id: str) -> None:
        self._require_project_binding(project_id, owner)
        canvas = default_god_canvas_service.get_canvas(canvas_id)
        if canvas.project_id != project_id:
            raise _err("VIDEO_CONTEXT_MISMATCH", "画布不属于指定项目", 403)
        found = any(node.entity_id == entity_id for node in default_god_canvas_service.get_topology(canvas_id).nodes)
        try:
            raw = asset_repo.state().read()
            entity = raw.get("project_entities", {}).get(entity_id)
            found = found or bool(entity and entity.get("project_id") == project_id)
        except Exception:
            raise _err("VIDEO_CONTEXT_UNAVAILABLE", "项目实体关系不可读取") from None
        if not found:
            raise _err("VIDEO_ENTITY_NOT_FOUND", "项目画布中不存在该实体", 404)

    def _resolve_asset(
        self, asset_id: str, *, project_id: Optional[str] = None,
        actor: Optional[str] = None, source_owner_key: Optional[str] = None,
    ) -> Path:
        if project_id and actor and source_owner_key:
            artifact_path = self.store.owned_video_artifact_path(
                project_id, asset_id, actor, source_owner_key
            )
            if artifact_path:
                path = Path(artifact_path).resolve()
                if path.parent != self.store.artifact_root.resolve() or not path.is_file():
                    raise _err("VIDEO_ARTIFACT_UNAVAILABLE", "项目视频产物不可读取")
                return path
        try:
            item = asset_repo.get_asset(asset_id)
        except CleanroomException:
            raise
        except Exception:
            raise _err("VIDEO_ASSET_UNAVAILABLE", "素材注册表暂不可用") from None
        display_path = item.get("display_path")
        if not display_path:
            raise _err("VIDEO_ASSET_NOT_LOCAL", "素材未登记本地媒体文件", 409)
        path = core_storage.resolve_within_roots(str(display_path))
        if not path.is_file() or path.suffix.lower() not in VIDEO_SUFFIXES:
            raise _err("VIDEO_ASSET_NOT_VIDEO", "素材不是可读取的本地视频", 409)
        return path

    def authorize_asset(self, project_id: str, asset_id: str, context: Any) -> Dict[str, Any]:
        owner = self.actor(context)
        source_owner_key = self._require_project_binding(project_id, owner)
        path = self._resolve_asset(
            asset_id, project_id=project_id, actor=owner, source_owner_key=source_owner_key
        )
        self.store.authorize_asset(project_id, asset_id, owner, source_owner_key)
        return {"project_id": project_id, "asset_id": asset_id, "display_name": path.name, "authorized": True}

    def _authorized_asset_path(self, asset_id: str, project_id: str, actor: str) -> Path:
        source_owner_key = self._require_project_binding(project_id, actor)
        if not self.store.asset_is_authorized(project_id, asset_id, actor, source_owner_key):
            raise _err("VIDEO_ASSET_ACCESS_REQUIRED", "素材尚未授权给该项目", 403)
        artifact_path = self.store.owned_video_artifact_path(
            project_id, asset_id, actor, source_owner_key
        )
        if artifact_path:
            return self._resolve_asset(
                asset_id, project_id=project_id, actor=actor, source_owner_key=source_owner_key
            )
        item = asset_repo.get_asset(asset_id)
        bound_project = item.get("project_id")
        if bound_project and str(bound_project) != project_id:
            raise _err("VIDEO_ASSET_PROJECT_MISMATCH", "素材登记的项目归属不匹配", 403)
        return self._resolve_asset(asset_id)

    def create_generation(self, payload: Dict[str, Any], key_value: Optional[str], context: Any) -> Dict[str, Any]:
        owner = self.actor(context)
        production = payload["production_context"]
        self._validate_context(owner, production["project_id"], production["canvas_id"], production["entity_id"])
        if payload["duration"] <= 0 or payload["duration"] > 120:
            raise _err("VIDEO_DURATION_UNSUPPORTED", "视频时长必须大于 0 且不超过 120 秒", 422)
        if payload["aspect_ratio"] not in RATIO_DIMENSIONS:
            raise _err("VIDEO_ASPECT_RATIO_UNSUPPORTED", "不支持该视频宽高比", 422)
        key = _request_key(key_value)
        try:
            provider = execution_config.resolve_provider(payload["provider_id"], capability="video")
        except execution_config.RuntimeConfigError:
            raise _err("VIDEO_PROVIDER_MISCONFIGURED", "视频 Provider 配置无效") from None
        if not provider:
            raise _err("VIDEO_PROVIDER_NOT_CONFIGURED", "未配置支持 newapi_video 的视频 Provider")
        if payload["model"] not in provider.models:
            raise _err("VIDEO_MODEL_UNSUPPORTED", "所选视频模型未在该 Provider 配置中允许", 422)
        if not execution_config.provider_api_key(provider):
            raise _err("VIDEO_PROVIDER_CREDENTIALS_MISSING", "视频 Provider 凭据未配置")
        normalized = dict(payload)
        normalized["duration"] = float(payload["duration"])
        row, created = self.store.create_job(actor=owner, operation="video-generation", key=key, request=normalized,
            project_id=production["project_id"], canvas_id=production["canvas_id"], entity_id=production["entity_id"],
            provider_id=provider.provider_id, model=payload["model"])
        if created:
            self.schedule(row["job_id"])
        return _job_view(row)

    def create_export(self, payload: Dict[str, Any], key_value: Optional[str], context: Any) -> Dict[str, Any]:
        owner = self.actor(context)
        self._validate_context(owner, payload["project_id"], payload["canvas_id"], payload["entity_id"])
        if len(set(payload["asset_ids"])) != len(payload["asset_ids"]):
            raise _err("VIDEO_ASSET_DUPLICATE", "素材列表不得重复", 422)
        for asset_id in payload["asset_ids"]:
            self._authorized_asset_path(asset_id, payload["project_id"], owner)
        if payload["preset"] not in EXPORT_PRESETS:
            raise _err("VIDEO_PRESET_UNSUPPORTED", "不支持该导出预设", 422)
        key = _request_key(key_value)
        row, created = self.store.create_job(actor=owner, operation="video-export", key=key, request=payload,
            project_id=payload["project_id"], canvas_id=payload["canvas_id"], entity_id=payload["entity_id"])
        if created:
            self.schedule(row["job_id"])
        return _job_view(row)

    def schedule(self, job_id: str) -> None:
        with self._lock:
            if job_id in self._scheduled:
                return
            self._scheduled.add(job_id)
        self.executor.submit(self._run_job, job_id)

    def _run_job(self, job_id: str) -> None:
        try:
            row = self.store.mark_running(job_id)
            if row is None:
                row = self.store.get_by_id(job_id)
                if not row or row["status"] != "running" or not row.get("upstream_task_id"):
                    return
            self._require_project_binding(row["project_id"], row["actor_key"])
            request = json.loads(row["request_json"])
            if row["operation"] == "video-generation":
                self._run_generation(row, request)
            elif row["operation"] == "video-export":
                self._run_export(row, request)
            else:
                self.store.mark_failed(job_id, "VIDEO_OPERATION_INVALID", "视频任务类型无效")
        except CleanroomException as exc:
            current = self.store.get_by_id(job_id)
            if current and current["status"] == "running" and exc.code in {
                "VIDEO_PROJECT_NOT_FOUND", "VIDEO_PROJECT_SOURCE_UNAVAILABLE"
            }:
                self.store.mark_interrupted(job_id, exc.code, "项目真源或owner关联不可验证，已停止视频执行")
                return
            # 已知远端引用查询失败时保留 running；进程重启可恢复查询而不会重建/重收费。
            if current and current["status"] == "running" and current.get("upstream_task_id") and exc.code == "VIDEO_PROVIDER_UNAVAILABLE":
                return
            self.store.mark_failed(job_id, exc.code, exc.message)
        except Exception:
            self.store.mark_failed(job_id, "VIDEO_TASK_FAILED", "视频任务执行失败")
        finally:
            with self._lock:
                self._scheduled.discard(job_id)

    @staticmethod
    def _provider(provider_id: str, model: str):
        try:
            provider = execution_config.resolve_provider(provider_id, capability="video")
        except execution_config.RuntimeConfigError:
            raise _err("VIDEO_PROVIDER_MISCONFIGURED", "视频 Provider 配置无效") from None
        if not provider or model not in provider.models:
            raise _err("VIDEO_PROVIDER_NOT_CONFIGURED", "视频 Provider 或模型配置不可用")
        token = execution_config.provider_api_key(provider)
        if not token:
            raise _err("VIDEO_PROVIDER_CREDENTIALS_MISSING", "视频 Provider 凭据未配置")
        return provider, token

    @staticmethod
    def _provider_url(provider: Any, path: str) -> str:
        try:
            return execution_config.api_endpoint_url(provider.base_url, path)
        except Exception:
            raise _err("VIDEO_PROVIDER_MISCONFIGURED", "视频 Provider 地址无效") from None

    @staticmethod
    def _remote_json(method: str, url: str, token: str, body: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        try:
            import httpx
            response = httpx.request(method, url,
                headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
                json=body, timeout=30 if method == "POST" else 20, follow_redirects=False)
        except Exception:
            if method == "POST":
                raise _err("VIDEO_CREATE_RESULT_UNKNOWN", "视频创建请求结果未知；已停止自动重试，请使用原幂等键查询", 504) from None
            raise _err("VIDEO_PROVIDER_UNAVAILABLE", "视频 Provider 查询失败", 502) from None
        if response.status_code < 200 or response.status_code >= 300:
            if method == "POST" and (response.status_code == 408 or response.status_code >= 500):
                raise _err("VIDEO_CREATE_RESULT_UNKNOWN", "Provider 创建响应不确定；已停止自动重提", 504)
            if method == "GET" and (response.status_code in {408, 429} or response.status_code >= 500):
                raise _err("VIDEO_PROVIDER_UNAVAILABLE", "视频 Provider 查询暂不可用", 502)
            raise _err("VIDEO_PROVIDER_REJECTED", "视频 Provider 返回非成功状态", 502)
        try:
            data = response.json()
        except Exception:
            if method == "POST":
                raise _err("VIDEO_CREATE_RESULT_UNKNOWN", "Provider 创建响应无法确认任务 ID；已停止自动重提", 504) from None
            raise _err("VIDEO_PROVIDER_RESPONSE_INVALID", "视频 Provider 响应无效", 502) from None
        if not isinstance(data, dict):
            if method == "POST":
                raise _err("VIDEO_CREATE_RESULT_UNKNOWN", "Provider 创建响应无法确认任务 ID；已停止自动重提", 504)
            raise _err("VIDEO_PROVIDER_RESPONSE_INVALID", "视频 Provider 响应结构无效", 502)
        return data

    def _run_generation(self, row: Dict[str, Any], request: Dict[str, Any]) -> None:
        self._require_project_binding(row["project_id"], row["actor_key"])
        provider, token = self._provider(row["provider_id"], row["model"])
        upstream_id = row.get("upstream_task_id")
        if not upstream_id:
            width, height = RATIO_DIMENSIONS[request["aspect_ratio"]]
            body = {"model": request["model"], "prompt": request["prompt"], "duration": request["duration"],
                    "width": width, "height": height, "fps": 30, "metadata": request["production_context"]}
            # 事务屏障前再次读取项目真源；缺失或owner不符时绝不越过远端计费POST边界。
            self._require_project_binding(row["project_id"], row["actor_key"])
            # 事务屏障先持久化派发意图；若取消先完成，任务状态已不是 running，不再调用远端 POST。
            if not self.store.begin_remote_create(row["job_id"]):
                return
            try:
                created = self._remote_json("POST", self._provider_url(provider, "/v1/video/generations"), token, body)
            except CleanroomException as exc:
                if exc.code == "VIDEO_CREATE_RESULT_UNKNOWN":
                    self.store.mark_interrupted(row["job_id"], exc.code, exc.message, unknown=True)
                    return
                raise
            upstream_id = created.get("task_id")
            if not isinstance(upstream_id, str) or not upstream_id or len(upstream_id) > 512:
                # 创建已被 Provider 接收但没有可恢复引用，不能把未知结果当成普通失败重提。
                self.store.mark_interrupted(row["job_id"], "VIDEO_CREATE_RESULT_UNKNOWN",
                                            "Provider 未返回可恢复 task_id；已停止自动重提", unknown=True)
                return
            # 若取消在 Provider 响应前提交，仍保存迟到 task_id 供人工追踪，但不继续轮询/发布。
            if not self.store.set_upstream_task(row["job_id"], upstream_id):
                return
        poll_url = self._provider_url(provider, "/v1/video/generations/" + quote(upstream_id, safe=""))
        deadline = time.monotonic() + MAX_REMOTE_WAIT
        while time.monotonic() < deadline:
            current = self.store.get_by_id(row["job_id"])
            if not current or current["status"] != "running":
                return
            try:
                state = self._remote_json("GET", poll_url, token)
            except CleanroomException as exc:
                if exc.code != "VIDEO_PROVIDER_UNAVAILABLE":
                    raise
                # 已有稳定上游 task_id，只重试只读查询；绝不重新提交计费创建请求。
                time.sleep(2)
                continue
            # 只读轮询也可能迟到；取消后不能据迟到响应继续下载或发布。
            current = self.store.get_by_id(row["job_id"])
            if not current or current["status"] != "running":
                return
            state_name = str(state.get("status") or "").lower()
            if state_name in {"queued", "pending", "in_progress", "running"}:
                time.sleep(2)
                continue
            if state_name == "failed":
                self.store.mark_failed(row["job_id"], "VIDEO_PROVIDER_FAILED", "远端视频生成失败")
                return
            if state_name != "completed":
                self.store.mark_failed(row["job_id"], "VIDEO_PROVIDER_STATE_INVALID", "Provider 返回未知视频状态")
                return
            result_url = state.get("url")
            if not isinstance(result_url, str) or not result_url:
                self.store.mark_failed(row["job_id"], "VIDEO_PROVIDER_RESPONSE_INVALID", "生成完成但缺少视频产物地址")
                return
            temporary = self.store.work_root / (row["job_id"] + ".download")
            temporary.unlink(missing_ok=True)
            try:
                self._require_project_binding(row["project_id"], row["actor_key"])
                download_provider_artifact(result_url, temporary, allow_private_for_test=self.allow_private_artifacts_for_test)
                media = self._probe(temporary)
                current = self.store.get_by_id(row["job_id"])
                if not current or current["status"] != "running":
                    return
                self._require_project_binding(row["project_id"], row["actor_key"])
                self.store.complete_success(row["job_id"], temporary, media)
                return
            finally:
                temporary.unlink(missing_ok=True)
        self.store.mark_interrupted(row["job_id"], "VIDEO_POLL_TIMEOUT", "等待上游视频完成超时；保留上游引用，不重新创建")

    def _run_export(self, row: Dict[str, Any], request: Dict[str, Any]) -> None:
        self._require_project_binding(row["project_id"], row["actor_key"])
        paths = [self._authorized_asset_path(asset_id, request["project_id"], row["actor_key"])
                 for asset_id in request["asset_ids"]]
        width, height, fps = EXPORT_PRESETS[request["preset"]]
        ffmpeg, ffprobe = shutil.which("ffmpeg"), shutil.which("ffprobe")
        if not ffmpeg or not ffprobe:
            raise _err("VIDEO_FFMPEG_UNAVAILABLE", "本机 ffmpeg/ffprobe 不可用")
        for path in paths:
            self._probe(path)
        temporary = self.store.work_root / (row["job_id"] + ".part.mp4")
        temporary.unlink(missing_ok=True)
        command = [ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error", "-protocol_whitelist", "file,pipe"]
        for path in paths:
            command.extend(["-i", str(path)])
        scale = f"scale={width}:{height}:force_original_aspect_ratio=decrease,pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,setsar=1"
        if len(paths) > 1:
            # concat 前统一尺寸、帧率与像素格式，允许不同规格素材稳定合并。
            labels = "".join(f"[v{index}]" for index in range(len(paths)))
            filters = [f"[{index}:v:0]fps={fps},{scale},format=yuv420p[v{index}]"
                       for index in range(len(paths))]
            filters.append(f"{labels}concat=n={len(paths)}:v=1:a=0[v]")
            command.extend(["-filter_complex", ";".join(filters), "-map", "[v]"])
        else:
            command.extend(["-map", "0:v:0", "-vf", f"{scale},fps={fps}"])
        command.extend(["-an", "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p",
                        "-r", str(fps), "-movflags", "+faststart", str(temporary)])
        process = None
        try:
            process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            self.store.set_process(row["job_id"], process)
            # 关闭「取消先于进程登记」窗口：登记后再核对持久状态，避免取消后仍长期渲染。
            current = self.store.get_by_id(row["job_id"])
            if not current or current["status"] != "running":
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=3)
                return
            try:
                process.wait(timeout=600)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
                raise _err("VIDEO_FFMPEG_TIMEOUT", "本地视频渲染超时", 504)
            current = self.store.get_by_id(row["job_id"])
            if current and current["status"] == "canceled":
                return
            if process.returncode != 0 or not temporary.is_file() or temporary.stat().st_size <= 0:
                raise _err("VIDEO_FFMPEG_FAILED", "本地 FFmpeg 渲染失败", 502)
            media = self._probe(temporary)
            current = self.store.get_by_id(row["job_id"])
            if current and current["status"] == "running":
                self._require_project_binding(row["project_id"], row["actor_key"])
                self.store.complete_success(row["job_id"], temporary, media)
        finally:
            self.store.clear_process(row["job_id"])
            if process and process.poll() is None:
                process.kill()
                process.wait(timeout=5)
            temporary.unlink(missing_ok=True)

    @staticmethod
    def _probe(path: Path) -> Dict[str, Any]:
        ffprobe = shutil.which("ffprobe")
        if not ffprobe:
            raise _err("VIDEO_FFPROBE_UNAVAILABLE", "本机 ffprobe 不可用")
        try:
            done = subprocess.run([ffprobe, "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)],
                                  capture_output=True, timeout=30, check=False)
        except (OSError, subprocess.TimeoutExpired):
            raise _err("VIDEO_FFPROBE_FAILED", "ffprobe 媒体验证失败", 502) from None
        if done.returncode:
            raise _err("VIDEO_MEDIA_INVALID", "媒体文件未通过 ffprobe 校验", 422)
        try:
            data = json.loads(done.stdout.decode("utf-8"))
            video = next(stream for stream in data.get("streams", []) if stream.get("codec_type") == "video")
            width, height = int(video["width"]), int(video["height"])
            duration = float(video.get("duration") or data.get("format", {}).get("duration"))
        except (StopIteration, KeyError, TypeError, ValueError, json.JSONDecodeError):
            raise _err("VIDEO_MEDIA_INVALID", "媒体文件缺少有效视频流或时长", 422) from None
        if width < 1 or height < 1 or not math.isfinite(duration) or duration <= 0:
            raise _err("VIDEO_MEDIA_INVALID", "媒体文件元数据无效", 422)
        return {"width": width, "height": height, "duration_seconds": duration,
                "video_codec": str(video.get("codec_name") or "unknown")}

    def cancel(self, job_id: str, context: Any) -> Dict[str, Any]:
        actor = self.actor(context)
        # 既有任务由持久actor隔离；项目真源故障不能阻断原主体止损。
        prior = self.store.get_owned_job(job_id, actor)
        result = self.store.cancel(job_id, actor)
        if prior["operation"] == "video-export":
            process = self.store.process_for(job_id)
            if process and process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=3)
        return _job_view(result)

    def get_job(self, job_id: str, context: Any) -> Dict[str, Any]:
        actor = self.actor(context)
        # 状态与计费风险可观测性独立于当前项目可用性，产物读取另行严格授权。
        row = self.store.get_owned_job(job_id, actor)
        return _job_view(row)

    def list_jobs(self, context: Any, limit: int = 50) -> Dict[str, Any]:
        actor = self.actor(context)
        rows = self.store.list_jobs(actor, limit)
        return {"items": [_job_view(row) for row in rows], "data_status": "ok"}

    def open_artifact(self, job_id: str, context: Any) -> Tuple[Path, Dict[str, Any]]:
        actor = self.actor(context)
        row = self.store.get_owned_job(job_id, actor)
        self._require_project_binding(row["project_id"], actor)
        if row["status"] != "succeeded" or not row.get("artifact"):
            raise _err("VIDEO_ARTIFACT_NOT_READY", "视频产物尚未完成或不可用", 409)
        artifact = row["artifact"]
        path = Path(artifact["path"]).resolve()
        if path.parent != self.store.artifact_root.resolve() or not path.is_file():
            raise _err("VIDEO_ARTIFACT_UNAVAILABLE", "视频产物不可读取")
        return path, artifact


_default_service: Optional[VideoTaskService] = None
_default_lock = threading.Lock()


def get_video_service() -> VideoTaskService:
    global _default_service
    if _default_service is None:
        with _default_lock:
            if _default_service is None:
                _default_service = VideoTaskService()
    return _default_service