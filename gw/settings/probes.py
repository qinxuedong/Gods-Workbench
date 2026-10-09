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
- `fetch-models` 按声明协议读取真实模型目录，兼容版本路径与Gemini分页；
- `test-connection` 真实往返并记录**实测**延迟（毫秒），绝不伪造数字；
- `probe-async` 保留兼容地址，同步请求结束后登记可回读的终态历史记录；
- 凭据（api key / token / password 一类）**只用不存**：不进落盘快照、不进响应；
- 出网安全：公网要求HTTPS；Provider明确配置的局域网字面IP允许HTTP，域名内网/链路本地/保留网段仍拒绝；
- 失败关闭：httpx 缺失 → 503 `PROVIDER_PROBE_NOT_INTEGRATED`；网络/上游失败 → 503
  `PROVIDER_PROBE_FAILED`，不返回任何伪造模型列表、连通性结论或延迟数字。
"""

from __future__ import annotations

import ipaddress
import socket
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse, urlencode

from gw.core import storage
from gw.core.errors import CleanroomException

NS_PROBES = "provider_probes"
PROBE_TIMEOUT_SECONDS = 20.0

#: 本机联调主机；局域网字面IP仅由Provider调用方显式准入。
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
LAN_NETWORKS = tuple(ipaddress.ip_network(value) for value in
                     ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "fc00::/7"))


def is_lan_provider_url(url: str) -> bool:
    """仅字面局域网IP；不把域名解析结果当成用户明确选择的内网目标。"""
    try:
        host = urlparse(url).hostname or ""
        if "%" in host:
            return False
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    return any(address.version == network.version and address in network for network in LAN_NETWORKS)

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
    return (normalized in {"key", "keys"} or normalized.replace("_", "").endswith(("key", "keys"))
            or any(marker in normalized for marker in _CREDENTIAL_MARKERS))


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


def guard_provider_url(url: str, *, allow_lan: bool = False) -> str:
    """默认拒绝内网；配置与探测可明确准入局域网字面IP，资产下载保持原边界。"""
    clean = str(url or "").strip()
    if not clean:
        raise CleanroomException(400, "INVALID_REQUEST", "缺少 base_url")
    try:
        parsed = urlparse(clean)
        port = parsed.port
        if parsed.username is not None or parsed.password is not None or parsed.query or parsed.fragment:
            raise ValueError()
    except ValueError:
        raise CleanroomException(400, "INVALID_URL", "地址不能包含账户、查询参数、片段或无效端口") from None
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise CleanroomException(400, "INVALID_URL", "base_url 必须是 http/https 绝对地址")
    host = parsed.hostname.strip().lower()
    if host in LOOPBACK_HOSTS:
        # 仅本机联调允许明文 http。
        return clean
    if allow_lan and is_lan_provider_url(clean):
        return clean
    port = port or (443 if parsed.scheme == "https" else 80)
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
        raise CleanroomException(403, "URL_NOT_ALLOWED", "公网 Provider 地址必须使用 https")
    return clean


def _models_url(base_url: str, protocol: str = "openai") -> str:
    """根地址补版本段；已含版本段的地址只补models。"""
    base = base_url.rstrip("/")
    if base.endswith("/models"):
        return base
    path = urlparse(base).path.rstrip("/")
    versions = ("/v1beta", "/v1alpha", "/v1") if protocol == "gemini" else ("/v1",)
    if not any(path.endswith(version) for version in versions):
        base += "/v1beta" if protocol == "gemini" else "/v1"
    return base + "/models"


def _headers(token: Optional[str], protocol: str = "openai") -> Dict[str, str]:
    headers = {"Accept": "application/json", "User-Agent": "Gods-Workbench/cleanroom"}
    if token:
        if protocol == "gemini":
            headers["x-goog-api-key"] = token
        else:
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


def _parse_models(body: Any, token: Optional[str] = None, protocol: str = "openai") -> List[str]:
    """从上游真实响应解析模型 ID 列表；结构不符时返回空列表，不编造。"""
    items = None
    if protocol == "gemini":
        items = body.get("models") if isinstance(body, dict) else None
        if not isinstance(items, list):
            return []
    elif isinstance(body, dict):
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
        if protocol == "gemini" and model.startswith("models/"):
            model = model.removeprefix("models/")
        if model not in seen and not (token and token in model):
            seen.add(model)
            ordered.append(model)
    return ordered


def _probe_payload(payload: Dict[str, Any]) -> Tuple[str, str, Optional[str]]:
    protocol = str((payload or {}).get("protocol") or "openai").strip().lower()
    if protocol == "ai platform":
        protocol = "openai"
    if protocol in {"volcengine", "codex", "gemini-cli", "jimeng"}:
        _unavailable("PROVIDER_MODEL_DISCOVERY_NOT_INTEGRATED", "/api/providers",
                     "该协议未接入HTTP模型目录发现；请手动配置模型，CLI状态由专用面板查询")
    if protocol not in {"openai", "apimart", "grok", "gemini"}:
        raise CleanroomException(400, "UNSUPPORTED_PROVIDER_PROTOCOL", "不支持的模型目录协议")
    from gw.settings.execution_config import normalize_provider_base_url
    base_url = normalize_provider_base_url(str((payload or {}).get("base_url") or ""), protocol)
    token = _credentials(payload)
    guarded = guard_provider_url(base_url, allow_lan=True)
    if not token and isinstance((payload or {}).get("provider_id"), str):
        from gw.settings.service import ProviderService
        service = ProviderService()
        provider_id = payload["provider_id"]
        snapshot = service.get_snapshot()
        saved = next((item for item in snapshot.providers if item["provider_id"] == provider_id), None)
        if saved is not None:
            # 已存秘密只能发到其所属平台的已存地址；编辑地址后必须保存或输入新凭据。
            if guarded.rstrip("/") != normalize_provider_base_url(saved["base_url"], saved["protocol"]) or not saved["enabled"]:
                raise CleanroomException(400, "PROVIDER_CONFIG_MISMATCH", "地址或启用状态与已保存配置不一致，未发送已有密钥")
            token = service.credential(provider_id, expected_version=snapshot.revision)
    return guarded, protocol, token


def _request_listing(payload: Dict[str, Any], endpoint: str):
    """一次同步目录观察；Gemini分页使用同源URL，不跟随重定向。"""
    base_url, protocol, token = _probe_payload(payload)
    httpx = _httpx()
    url = _models_url(base_url, protocol)
    started = time.perf_counter()
    all_models = []
    seen_pages = set()
    client_options = {"timeout": PROBE_TIMEOUT_SECONDS, "follow_redirects": False}
    if is_lan_provider_url(base_url):
        client_options["trust_env"] = False
    with httpx.Client(**client_options) as client:
        next_url = url
        for page in range(8):
            try:
                response = client.get(next_url, headers=_headers(token, protocol))
            except Exception:
                _unavailable("PROVIDER_PROBE_FAILED", endpoint, "连接上游模型接口失败，未确认目录或生成能力")
            try:
                body = response.json()
            except Exception:
                body = None
            if not 200 <= response.status_code < 300:
                all_models = []
                break
            models = _parse_models(body, token, protocol)
            if not models:
                all_models = []
                break
            all_models.extend(models)
            next_page = body.get("nextPageToken") if protocol == "gemini" and isinstance(body, dict) else None
            if not next_page:
                break
            if not isinstance(next_page, str) or len(next_page) > 4096 or next_page in seen_pages or (token and token in next_page) or page == 7:
                _unavailable("PROVIDER_PROBE_FAILED", endpoint, "上游分页结果异常或超出限制，未返回不完整的模型目录")
            seen_pages.add(next_page)
            next_url = url + "?" + urlencode({"pageToken": next_page})
    latency = int(round((time.perf_counter() - started) * 1000))
    models = list(dict.fromkeys(all_models))
    message = ("模型目录已可见；分类为建议，生成权限和接口尚未验证。" if models else
               "上游模型目录返回HTTP %d；没有可解析模型，未确认配置可用。" % response.status_code)
    return protocol, response.status_code, models, latency, message


def _listing_fields(protocol: str, models: List[str]) -> Dict[str, Any]:
    return {"protocol": protocol, "all": models, "total": len(models),
            "image_models": [m for m in models if _classify(m) == "image"],
            "chat_models": [m for m in models if _classify(m) == "chat"],
            "video_models": [m for m in models if _classify(m) == "video"],
            "generation_verified": False, "protocol_verified": False,
            "classification_source": "model_id_hint"}


def fetch_models(payload: Dict[str, Any]) -> Dict[str, Any]:
    """真实拉取上游模型列表；网络失败 503，绝不返回伪造列表。"""
    protocol, status_code, models, latency, message = _request_listing(payload, "/api/providers/fetch-models")
    if not models:
        _unavailable("PROVIDER_PROBE_FAILED", "/api/providers/fetch-models",
                     message)
    return {
        "ok": True,
        **_listing_fields(protocol, models),
        "image_request_mode": str((payload or {}).get("image_request_mode") or ""),
        "status_code": status_code,
        "message": message,
        "model_names": {},
        "data_status": "ok",
        "data_gaps": ["generation_not_verified"],
    }


def test_connection(payload: Dict[str, Any]) -> Dict[str, Any]:
    """真实往返并记录实测延迟；失败 503，绝不伪造连通性或延迟数字。"""
    protocol, status_code, models, latency_ms, message = _request_listing(payload, "/api/providers/test-connection")
    if not models:
        _unavailable("PROVIDER_PROBE_FAILED", "/api/providers/test-connection",
                     message)
    return {
        "ok": True,
        **_listing_fields(protocol, models),
        "status": status_code,
        "latency_ms": latency_ms,
        "image_request_mode": str((payload or {}).get("image_request_mode") or ""),
        "model_count": len(models),
        "message": message,
        "data_status": "ok",
        "data_gaps": ["generation_not_verified"],
    }


def _probe_state() -> storage.JsonState:
    return storage.JsonState(NS_PROBES, lambda: {"revision": 1, "sequence": 0, "jobs": {}})


def probe_async(payload: Dict[str, Any]) -> Dict[str, Any]:
    """兼容旧地址的同步目录探测；落盘的是已完成结果，无后台排队或轮询。"""
    protocol, status_code, models, latency, message = _request_listing(payload, "/api/providers/probe-async")
    # 兼容raw字段名，但只持久化已解析的真实模型ID；任意上游字段及嵌套秘密不透传。
    safe_raw = {"data": [{"id": model} for model in models]}
    ok = bool(models)

    def mutate(raw: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
        raw["sequence"] = int(raw.get("sequence", 0)) + 1
        job_id = "pjob_%04d" % raw["sequence"]
        record = {
            "job_id": job_id,
            "status": "succeeded" if ok else "failed",
            "ok": ok,
            "protocol": protocol,
            "status_code": status_code,
            "execution_mode": "synchronous",
            "completed": True,
            "generation_verified": False,
            "protocol_verified": False,
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
        "status_code": status_code,
        "execution_mode": "synchronous",
        "completed": True,
        "generation_verified": False,
        "protocol_verified": False,
        "message": message,
        "raw": safe_raw,
        "image_request_mode": str((payload or {}).get("image_request_mode") or ""),
        "data_status": "ok" if ok else "degraded",
        "data_gaps": ["generation_not_verified"] if ok else ["model_listing_failed", "generation_not_verified"],
    }


def get_probe_job(job_id: str) -> Dict[str, Any]:
    """读取同步探测历史；不存在404，不返回伪造状态。"""
    job = _probe_state().read().get("jobs", {}).get(str(job_id))
    if not job:
        raise CleanroomException(404, "PROVIDER_PROBE_JOB_NOT_FOUND", "探测任务不存在")
    task = dict(job)
    task.setdefault("execution_mode", "synchronous")
    task.setdefault("completed", task.get("status") in {"succeeded", "failed"})
    task.setdefault("generation_verified", False)
    task.setdefault("protocol_verified", False)
    return {"task": task, "job_id": task["job_id"], "status": task["status"],
            "data_status": "ok", "data_gaps": []}
