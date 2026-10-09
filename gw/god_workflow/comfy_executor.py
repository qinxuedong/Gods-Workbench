"""本地 ComfyUI 真实执行闭环。

只用 ComfyUI 官方稳定接口，不发明任何 provider 专属协议：

- ``POST /prompt``            提交 API prompt，返回 ``prompt_id``
- ``GET  /history/{prompt_id}`` 轮询执行结果
- ``POST /interrupt``         请求取消当前执行
- ``GET  /view``              取回输出文件字节
- ``GET  /object_info``       环境对比（节点/模型可用性）

边界与失败语义：

- 只接受显式配置的 ``GW_COMFYUI_URL``；未配置即返回标准 503，不猜测本地端口。
- 断开重定向、禁用环境代理、限制响应大小与超时，复用与网关一致的失败关闭口径。
- 输出文件先落到运行时数据根并经素材登记后才允许标记成功；绝不写源码仓库。
- 不支持取消远端排队任务时如实返回 ``remote_cancelled=false``，不谎称已取消。
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Optional
from urllib.parse import urlsplit

import httpx

from gw.core.errors import CleanroomException

MAX_COMFY_BYTES = 16 * 1024 * 1024
MAX_VIEW_BYTES = 32 * 1024 * 1024
DEFAULT_TIMEOUT = 15.0
_endpoint_override: ContextVar[Optional["ComfyEndpoint"]] = ContextVar("workflow_comfy_endpoint", default=None)

#: 终态集合：到达任一状态后不再轮询。
TERMINAL_STATES = frozenset({"succeeded", "failed", "canceled", "interrupted"})


def unavailable(code: str, message: str) -> CleanroomException:
    return CleanroomException(503, code, message, {"unavailable": True}, {"unavailable"})


@dataclass(frozen=True)
class ComfyEndpoint:
    """显式准入的本地 ComfyUI 端点配置。"""

    base_url: str
    timeout: float = DEFAULT_TIMEOUT

    @property
    def configured(self) -> bool:
        parts = urlsplit(self.base_url)
        return bool(parts.scheme in ("http", "https") and parts.hostname
                    and not parts.username and not parts.password
                    and not parts.query and not parts.fragment
                    and 0 < self.timeout <= 600)


def get_endpoint() -> ComfyEndpoint:
    """按调用时环境解析配置，保持与三态运行隔离一致。"""
    selected = _endpoint_override.get()
    if selected is not None:
        return selected
    try:
        timeout = float(os.getenv("GW_COMFYUI_TIMEOUT_SECONDS", str(DEFAULT_TIMEOUT)))
    except ValueError:
        timeout = 0.0
    return ComfyEndpoint(os.getenv("GW_COMFYUI_URL", "").strip(), timeout)


@contextmanager
def using_endpoint(endpoint: ComfyEndpoint):
    """只在当前异步调用链绑定端点，避免主体配置污染进程环境。"""
    token = _endpoint_override.set(endpoint)
    try:
        yield
    finally:
        _endpoint_override.reset(token)


def require_endpoint() -> ComfyEndpoint:
    endpoint = get_endpoint()
    if not endpoint.configured:
        raise unavailable("WORKFLOW_EXECUTOR_UNAVAILABLE",
                          "Local ComfyUI endpoint is not configured (GW_COMFYUI_URL)")
    return endpoint


async def _request_json(method: str, path: str, *, payload: Optional[dict[str, Any]] = None,
                        timeout: float = DEFAULT_TIMEOUT) -> Any:
    endpoint = require_endpoint()
    url = f"{endpoint.base_url.rstrip('/')}{path}"
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(timeout), follow_redirects=False,
                                     trust_env=False) as client:
            response = await client.request(method, url, json=payload,
                                            headers={"Accept": "application/json"})
            content = response.content
            if len(content) > MAX_COMFY_BYTES:
                raise CleanroomException(502, "WORKFLOW_PROVIDER_RESPONSE_TOO_LARGE",
                                         "ComfyUI response exceeds the size limit")
            if response.status_code >= 500:
                raise CleanroomException(502, "WORKFLOW_PROVIDER_ERROR", "ComfyUI rejected the request")
            return response.status_code, content
    except httpx.TimeoutException:
        raise CleanroomException(504, "WORKFLOW_PROVIDER_TIMEOUT", "ComfyUI request timed out") from None
    except httpx.HTTPError:
        raise CleanroomException(502, "WORKFLOW_PROVIDER_ERROR", "ComfyUI request failed") from None


def _decode(content: bytes) -> Any:
    try:
        return json.loads(content)
    except (ValueError, UnicodeDecodeError):
        raise CleanroomException(502, "WORKFLOW_PROVIDER_INVALID_RESPONSE",
                                 "ComfyUI returned invalid JSON") from None


async def probe() -> dict[str, Any]:
    """探测本地 ComfyUI 是否在线；不编造在线状态。"""
    endpoint = get_endpoint()
    if not endpoint.configured:
        return {"online": False, "status": "unconfigured", "reason": "GW_COMFYUI_URL is not set"}
    try:
        status_code, content = await _request_json("GET", "/object_info", timeout=min(endpoint.timeout, 10.0))
    except CleanroomException as exc:
        return {"online": False, "status": "unreachable", "reason": exc.code}
    if status_code != 200:
        return {"online": False, "status": "unreachable", "reason": f"HTTP {status_code}"}
    info = _decode(content)
    nodes = info if isinstance(info, dict) else {}
    return {"online": True, "status": "online", "node_count": len(nodes),
            "object_info": nodes}


async def submit(prompt: dict[str, Any], *, client_id: Optional[str] = None) -> dict[str, Any]:
    """提交 API prompt，返回真实 ``prompt_id``。"""
    if not isinstance(prompt, dict) or not prompt:
        raise CleanroomException(400, "INVALID_WORKFLOW", "API prompt must be a non-empty object")
    body: dict[str, Any] = {"prompt": prompt}
    if client_id:
        body["client_id"] = str(client_id)
    status_code, content = await _request_json("POST", "/prompt", payload=body)
    if status_code == 400:
        detail = _decode(content)
        message = "ComfyUI rejected the prompt"
        if isinstance(detail, dict):
            error = detail.get("error")
            if isinstance(error, dict) and error.get("message"):
                message = str(error["message"])
        raise CleanroomException(400, "INVALID_WORKFLOW", message)
    if status_code not in (200, 201):
        raise CleanroomException(502, "WORKFLOW_PROVIDER_ERROR", "ComfyUI rejected the submission")
    data = _decode(content)
    prompt_id = data.get("prompt_id") if isinstance(data, dict) else None
    if not isinstance(prompt_id, str) or not prompt_id.strip():
        raise CleanroomException(502, "WORKFLOW_PROVIDER_INVALID_RESPONSE",
                                 "ComfyUI response is missing prompt_id")
    return {"prompt_id": prompt_id.strip(),
            "number": data.get("number"),
            "node_errors": data.get("node_errors") or {}}


async def history(prompt_id: str) -> dict[str, Any]:
    """读取一次执行记录；缺失即仍排队或执行中。"""
    if not isinstance(prompt_id, str) or not prompt_id.strip():
        raise CleanroomException(400, "INVALID_REQUEST", "prompt_id is required")
    status_code, content = await _request_json("GET", f"/history/{prompt_id.strip()}")
    if status_code != 200:
        raise CleanroomException(502, "WORKFLOW_PROVIDER_ERROR", "ComfyUI history request failed")
    data = _decode(content)
    entry = data.get(prompt_id.strip()) if isinstance(data, dict) else None
    return {"found": isinstance(entry, dict), "entry": entry if isinstance(entry, dict) else None}


async def upload_input(payload: bytes, filename: str, content_type: str, *, subfolder: str) -> str:
    """把获准主体素材传到配置的引擎输入目录，使用真实返回名称。"""
    endpoint = require_endpoint()
    if not payload or len(payload) > MAX_VIEW_BYTES:
        raise CleanroomException(400, "INVALID_ASSET_UPLOAD", "输入素材为空或超过限制")
    try:
        async with httpx.AsyncClient(timeout=endpoint.timeout, follow_redirects=False, trust_env=False) as client:
            response = await client.post(f"{endpoint.base_url.rstrip('/')}/upload/image",
                files={"image": (filename, payload, content_type)},
                data={"type": "input", "overwrite": "false", "subfolder": subfolder})
        if response.status_code != 200 or len(response.content) > MAX_COMFY_BYTES:
            raise CleanroomException(502, "INPUT_UPLOAD_FAILED", "ComfyUI 未确认输入素材上传")
        data = _decode(response.content)
        name = data.get("name") if isinstance(data, dict) else None
        folder = data.get("subfolder", "") if isinstance(data, dict) else ""
        if (not isinstance(name, str) or not name or any(part in name for part in ("/", "\\", ".."))
                or not isinstance(folder, str) or ".." in folder or folder.startswith(("/", "\\"))):
            raise CleanroomException(502, "INPUT_UPLOAD_FAILED", "ComfyUI 返回了无效输入名称")
        return f"{folder}/{name}" if folder else name
    except httpx.HTTPError:
        raise CleanroomException(502, "INPUT_UPLOAD_FAILED", "ComfyUI 输入上传失败") from None


def summarize(entry: Optional[dict[str, Any]]) -> dict[str, Any]:
    """把 history 条目映射为统一任务状态，不伪造进度百分比。"""
    if not isinstance(entry, dict):
        return {"status": "running", "outputs": [], "messages": [], "progress": {"known": False, "value": None}}
    outputs: list[dict[str, Any]] = []
    for node_id, node_output in (entry.get("outputs") or {}).items():
        if not isinstance(node_output, dict):
            continue
        for kind, items in node_output.items():
            if not isinstance(items, list):
                continue
            for item in items:
                if isinstance(item, dict) and item.get("filename"):
                    outputs.append({"node_id": str(node_id), "kind": str(kind), **item})
    status = entry.get("status") or {}
    messages = status.get("messages") if isinstance(status, dict) else []
    failed = bool(isinstance(status, dict) and status.get("status_str") == "error")
    return {"status": "failed" if failed else "succeeded",
            "outputs": outputs,
            "messages": messages if isinstance(messages, list) else [],
            "progress": {"known": False, "value": None}}


async def interrupt() -> dict[str, Any]:
    """请求取消当前执行。

    ComfyUI 的 ``/interrupt`` 只中断当前正在执行的提示，无法保证已经排队或
    已经产生的计费被取消，因此 ``remote_cancelled`` 如实反映服务端响应。
    """
    status_code, _content = await _request_json("POST", "/interrupt", payload={})
    return {"remote_cancelled": status_code in (200, 202),
            "remote_may_continue_or_bill": status_code not in (200, 202),
            "http_status": status_code}


async def fetch_output(filename: str, *, subfolder: str = "", folder_type: str = "output") -> tuple[bytes, str]:
    """取回输出文件字节与媒体类型。"""
    if not isinstance(filename, str) or not filename.strip():
        raise CleanroomException(400, "INVALID_REQUEST", "filename is required")
    endpoint = require_endpoint()
    url = f"{endpoint.base_url.rstrip('/')}/view"
    params = {"filename": filename.strip(), "type": folder_type}
    if subfolder:
        params["subfolder"] = subfolder
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(endpoint.timeout), follow_redirects=False,
                                     trust_env=False) as client:
            async with client.stream("GET", url, params=params) as response:
                if response.status_code != 200:
                    raise CleanroomException(502, "WORKFLOW_PROVIDER_ERROR", "ComfyUI could not return the output file")
                content = bytearray()
                async for chunk in response.aiter_bytes():
                    content.extend(chunk)
                    if len(content) > MAX_VIEW_BYTES:
                        raise CleanroomException(502, "WORKFLOW_PROVIDER_RESPONSE_TOO_LARGE",
                                                 "ComfyUI output exceeds the size limit")
                media_type = response.headers.get("content-type", "application/octet-stream").split(";", 1)[0]
                return bytes(content), media_type
    except httpx.TimeoutException:
        raise CleanroomException(504, "WORKFLOW_PROVIDER_TIMEOUT", "ComfyUI output request timed out") from None
    except httpx.HTTPError:
        raise CleanroomException(502, "WORKFLOW_PROVIDER_ERROR", "ComfyUI output request failed") from None
