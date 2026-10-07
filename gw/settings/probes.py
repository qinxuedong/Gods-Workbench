# -*- coding: utf-8 -*-
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

"""Provider 真实探测服务（Phase 12 A4）。

真实数据源与边界：
- `fetch-models` 真实 `GET {base_url}/v1/models`，模型列表来自上游响应原文；
- `test-connection` 真实往返并记录**实测**延迟（毫秒），绝不伪造数字；
- `probe-async` 真实请求后登记可回读的探测任务（`pjob_NNNN` + poll_hint）；
- 凭据（api key / token / password 一类）**只用不存**：不进落盘快照、不进响应；
- 出网安全：非本机地址必须 `https://`；拒绝内网/回环/链路本地/保留网段（s3rver-side req forgery）；
- 失败关闭：httpx 缺失 → 503 `PROVIDER_PROBE_NOT_INTEGRATED`；网络/上游失败 → 503
  `PROVIDER_PROBE_FAILED`，不返回任何伪造模型列表、连通性结论或延迟数字。
"""

from __future__ import annotations

import ipaddress
import socket
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

from gw.core import storage
from gw.core.errors import CleanroomException

NS_PROBES = "provider_probes"

#: 允许以明文 http 访问的本机联调主机；其余主机必须 https。
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})

#: 凭据类字段（小写、去分隔符后比对），只用不存。
_CREDENTIAL_MARKERS = (
    "api_keys", "apikeys", "api_key", "apikey", "access_key", "secret",
    "token", "password", "credential",
    "authorization", "auth", "wallet", "private_key",
)

#: 模型分类关键字；分类由上游返回的真实模型 ID 推导，不凭空生成条目。
_IMAGE_MARKERS = ("image", "imagen", "dall", "flux", "seedream", "nano-banana",
                  "stable-diffusion", "sdxl", "midjourney", "wanx", "kolors")
_VIDEO_MARKERS = ("video", "veo", "sora", "kling", "seedance", "runway", "pika",
                  "luma", "minimax-hailuo", "wan2")
_CHAT_MARKERS = ("gpt", "chat", "claude", "gemini", "qwen", "deepseek", "glm",
                 "llama", "mistral", "grok", "moonshot", "ernie")


def _is_credential_key(key: str) -> bool:
    normalized = str(key or "").strip().casefold().replace("-", "_").replace(" ", "_")
    if not normalized:
        return False
    return any(marker in normalized for marker in _CREDENTIAL_MARKERS)


def strip_credentials(value: Any) -> Any:
    """递归剥离凭据字段；返回新对象，绝不修改入参。"""
    if isinstance(value, dict):
        return {k: strip_credentials(v) for k, v in value.items() if not _is_credential_key(k)}
    if isinstance(value, list):
        return [strip_credentials(item) for item in value]
    return value


def _unavailable(code: str, endpoint: str, message: str) -> None:
    raise CleanroomException(
        status_code=503,
        code=code,
        message=message,
        extra={"endpoint": endpoint, "unavailable": True, "data_status": "not_integrated"},
        expose_extra_fields={"endpoint", "unavailable", "data_status"},
    )


def _httpx():
    try:
        import httpx  # type: ignore
    except Exception:
        _unavailable("PROVIDER_PROBE_NOT_INTEGRATED", "/api/providers",
                     "未安装 httpx，Provider 探测能力不可用")
    return httpx


def _credentials(payload: Dict[str, Any]) -> Optional[str]:
    """从请求体取凭据（`api_keys` / token / authorization）；只用不存，不落盘不回显。"""
    for key in ("api_keys", "apikey", "api_key", "key", "token", "authorization"):
        value = (payload or {}).get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
        if isinstance(value, (list, tuple)):
            for item in value:
                if isinstance(item, str) and item.strip():
                    return item.strip()
    return None


def guard_provider_url(url: str) -> str:
    """校验 Provider base_url：只允许 https 或本机明文 http；拒绝内网/保留网段。"""
    clean = str(url or "").strip()
    if not clean:
        raise CleanroomException(400, "INVALID_REQUEST", "缺少 base_url")
    parsed = urlparse(clean)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise CleanroomException(400, "INVALID_URL", "base_url 必须是 http/https 绝对地址")
    host = parsed.hostname.strip().lower()
    if host in LOOPBACK_HOSTS:
        # 仅本机联调允许明文 http。
        return clean
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    try:
        infos = socket.getaddrinfo(host, port)
    except socket.gaierror:
        raise CleanroomException(400, "INVALID_URL", "base_url 主机名无法解析")
    for info in infos:
        try:
            ip = ipaddress.ip_address(info[4][0])
        except ValueError:
            raise CleanroomException(400, "INVALID_URL", "解析出的地址不合法")
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
            raise CleanroomException(403, "SSRF_BLOCKED", "该地址属于受限网段，已拒绝请求")
    if parsed.scheme == "http":
        raise CleanroomException(403, "URL_NOT_ALLOWED", "非本机 Provider 地址必须使用 https")
    return clean


def _models_url(base_url: str) -> str:
    return base_url.rstrip("/") + "/v1/models"


def _headers(token: Optional[str]) -> Dict[str, str]:
    headers = {"Accept": "application/json", "User-Agent": "Gods-Workbench/cleanroom"}
    if token:
        headers["Authorization"] = "Bearer %s" % token
    return headers


def _classify(model_id: str) -> str:
    text = str(model_id or "").lower()
    if any(marker in text for marker in _VIDEO_MARKERS):
        return "video"
    if any(marker in text for marker in _IMAGE_MARKERS):
        return "image"
    if any(marker in text for marker in _CHAT_MARKERS):
        return "chat"
    return "other"


def _parse_models(body: Any) -> List[str]:
    """从上游真实响应解析模型 ID 列表；结构不符时返回空列表，不编造。"""
    items = None
    if isinstance(body, dict):
        for key in ("data", "models", "result", "items"):
            if isinstance(body.get(key), list):
                items = body[key]
                break
    elif isinstance(body, list):
        items = body
    if items is None:
        return []
    models: List[str] = []
    for item in items:
        if isinstance(item, str) and item.strip():
            models.append(item.strip())
        elif isinstance(item, dict):
            for key in ("id", "model", "name"):
                value = item.get(key)
                if isinstance(value, str) and value.strip():
                    models.append(value.strip())
                    break
    # 去重且保持上游顺序。
    seen = set()
    ordered = []
    for model in models:
        if model not in seen:
            seen.add(model)
            ordered.append(model)
    return ordered


def _detect_protocol(base_url: str, declared: str, models: List[str], body: Any) -> str:
    declared = str(declared or "").strip()
    text = ("%s %s" % (base_url, " ".join(models))).lower()
    if "apimart" in text or (isinstance(body, dict) and body.get("task_id")):
        return "APIMart"
    if declared:
        return declared
    return "AI Platform"


def _probe_payload(payload: Dict[str, Any]) -> Tuple[str, str, Optional[str]]:
    base_url = str((payload or {}).get("base_url") or "").strip()
    token = _credentials(payload)
    guarded = guard_provider_url(base_url)
    return guarded, str((payload or {}).get("protocol") or ""), token


def fetch_models(payload: Dict[str, Any]) -> Dict[str, Any]:
    """真实拉取上游模型列表；网络失败 503，绝不返回伪造列表。"""
    base_url, declared, token = _probe_payload(payload)
    httpx = _httpx()
    url = _models_url(base_url)
    try:
        with httpx.Client(timeout=20.0, follow_redirects=False) as client:
            response = client.get(url, headers=_headers(token))
    except Exception:
        _unavailable("PROVIDER_PROBE_FAILED", "/api/providers/fetch-models",
                     "连接上游模型接口失败，未返回任何模型列表")
    if response.status_code >= 400:
        _unavailable("PROVIDER_PROBE_FAILED", "/api/providers/fetch-models",
                     "上游模型接口返回错误状态 %d" % response.status_code)
    try:
        body = response.json()
    except Exception:
        body = None
    models = _parse_models(body)
    if not models:
        _unavailable("PROVIDER_PROBE_FAILED", "/api/providers/fetch-models",
                     "上游未返回可解析的模型列表，未编造任何模型条目")
    image = [m for m in models if _classify(m) == "image"]
    chat = [m for m in models if _classify(m) == "chat"]
    video = [m for m in models if _classify(m) == "video"]
    return {
        "ok": True,
        "protocol": _detect_protocol(base_url, declared, models, body),
        "image_request_mode": str((payload or {}).get("image_request_mode") or ""),
        "status_code": response.status_code,
        "all": models,
        "total": len(models),
        "image_models": image,
        "chat_models": chat,
        "video_models": video,
        "model_names": {},
        "data_status": "ok",
        "data_gaps": [],
    }


def test_connection(payload: Dict[str, Any]) -> Dict[str, Any]:
    """真实往返并记录实测延迟；失败 503，绝不伪造连通性或延迟数字。"""
    base_url, declared, token = _probe_payload(payload)
    httpx = _httpx()
    url = _models_url(base_url)
    started = time.perf_counter()
    try:
        with httpx.Client(timeout=20.0, follow_redirects=False) as client:
            response = client.get(url, headers=_headers(token))
    except Exception:
        _unavailable("PROVIDER_PROBE_FAILED", "/api/providers/test-connection",
                     "连接上游失败，未返回连通性结论或延迟数字")
    latency_ms = int(round((time.perf_counter() - started) * 1000))
    try:
        body = response.json()
    except Exception:
        body = None
    models = _parse_models(body) if response.status_code < 400 else []
    if response.status_code >= 400:
        _unavailable("PROVIDER_PROBE_FAILED", "/api/providers/test-connection",
                     "上游返回错误状态 %d，未返回连通性结论" % response.status_code)
    return {
        "ok": True,
        "status": response.status_code,
        "latency_ms": latency_ms,
        "protocol": _detect_protocol(base_url, declared, models, body),
        "image_request_mode": str((payload or {}).get("image_request_mode") or ""),
        "model_count": len(models),
        "all": models,
        "image_models": [m for m in models if _classify(m) == "image"],
        "chat_models": [m for m in models if _classify(m) == "chat"],
        "video_models": [m for m in models if _classify(m) == "video"],
        "message": "已与上游完成真实往返，模型数量来自上游响应。",
        "data_status": "ok",
        "data_gaps": [],
    }


def _probe_state() -> storage.JsonState:
    return storage.JsonState(NS_PROBES, lambda: {"revision": 1, "sequence": 0, "jobs": {}})


def probe_async(payload: Dict[str, Any]) -> Dict[str, Any]:
    """真实探测并登记可回读任务；返回稳定 pjob_NNNN 与 poll_hint。"""
    base_url, declared, token = _probe_payload(payload)
    httpx = _httpx()
    url = _models_url(base_url)
    try:
        with httpx.Client(timeout=20.0, follow_redirects=False) as client:
            response = client.get(url, headers=_headers(token))
    except Exception:
        _unavailable("PROVIDER_PROBE_FAILED", "/api/providers/probe-async",
                     "连接上游失败，未返回协议判定结果")
    try:
        body = response.json()
    except Exception:
        body = None
    models = _parse_models(body) if response.status_code < 400 else []
    protocol = _detect_protocol(base_url, declared, models, body)
    safe_raw = strip_credentials(body)
    ok = response.status_code < 400
    message = ("已与上游完成真实往返。" if ok
               else "上游返回错误状态 %d，未判定协议成功。" % response.status_code)

    def mutate(raw: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
        raw["sequence"] = int(raw.get("sequence", 0)) + 1
        job_id = "pjob_%04d" % raw["sequence"]
        record = {
            "job_id": job_id,
            "status": "succeeded" if ok else "failed",
            "ok": ok,
            "protocol": protocol,
            "status_code": response.status_code,
            "message": message,
            "raw": safe_raw,
            "created_at": storage.now_iso(),
            "finished_at": storage.now_iso(),
        }
        raw.setdefault("jobs", {})[job_id] = record
        raw["revision"] = int(raw.get("revision") or 1) + 1
        return job_id, dict(record)

    job_id, _ = _probe_state().mutate(mutate)
    return {
        "job_id": job_id,
        "poll_hint": "/api/providers/probe-async/jobs/%s" % job_id,
        "ok": ok,
        "protocol": protocol,
        "status_code": response.status_code,
        "message": message,
        "raw": safe_raw,
        "image_request_mode": str((payload or {}).get("image_request_mode") or ""),
        "data_status": "ok",
        "data_gaps": [],
    }


def get_probe_job(job_id: str) -> Dict[str, Any]:
    """读取 probe-async 登记的真实任务；不存在 404，不返回伪造状态。"""
    job = _probe_state().read().get("jobs", {}).get(str(job_id))
    if not job:
        raise CleanroomException(404, "PROVIDER_PROBE_JOB_NOT_FOUND", "探测任务不存在")
    return {"task": dict(job), "job_id": job["job_id"], "status": job["status"],
            "data_status": "ok", "data_gaps": []}
