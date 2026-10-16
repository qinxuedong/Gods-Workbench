"""Workflow parsing and per-principal document storage API."""
from __future__ import annotations

import asyncio
import base64
import ipaddress
import shutil
import socket
import hashlib
import json
import logging
import os
import re
import tempfile
import threading
import uuid
import time
import math
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional
from urllib.parse import quote, urljoin, urlsplit

import httpx
from fastapi import APIRouter, Header, Request, status
from fastapi.responses import FileResponse

from gw.core import audit
from gw.core.auth import EDIT_ROLES, AuthContext, require_authenticated, require_edit_access
from gw.core.errors import CleanroomException, VersionConflictException
from gw.core.runtime_paths import RuntimePathError, resolve_runtime_paths
from gw.god_workflow import comfy_executor
from gw.god_workflow import platforms
from gw.god_workflow import registry
from gw.god_workflow import asset_hub_bridge
from gw.god_workflow import browser_bridge
from gw.god_workflow.legacy_reparse import legacy_reparse_state
from gw.god_workflow.layout import compute_layout
from gw.god_workflow.models import WorkflowSource
from gw.god_workflow.clients import PROVIDERS, get_provider, unavailable
from gw.god_workflow.parser import (MAX_UPLOAD_BYTES, MAX_PNG_TEXT_BYTES, WorkflowParseError,
                                   parse_workflow, parse_upload, png_available,
                                   build_topology_from_public_detail, extract_source_graph,
                                   _api_link)
from gw.god_workflow.exporter import export_to_comfy_litegraph
from gw.god_workflow.compiler import compile_prompt, execution_fingerprint

logger = logging.getLogger(__name__)

#: 可执行真实抓取的平台适配器；其余 provider 仍走通用网关或返回不可用。
PROVIDER_ADAPTERS = ("runninghub", "liblib")
_PRIVATE_METADATA_NAMES = frozenset({"settings", "clipboard", "assets"})


async def _fetch_provider_bundle(provider: str, reference: str, context: AuthContext) -> dict[str, Any]:
    """按平台调用真实抓取通道；读取当前主体的有效凭据。"""
    if provider == "runninghub":
        bundle = await platforms.fetch_runninghub_bundle(
            reference,
            access_token=registry.credential_value("rh_access_token", context),
            api_key=registry.credential_value("rh_api_key", context),
        )
    elif provider == "liblib":
        bundle = await platforms.fetch_liblib_bundle(reference)
    else:
        raise CleanroomException(400, "INVALID_PROVIDER", "不支持的工作流平台")
    return {**bundle, "provider": provider}


def _document_from_bundle(bundle: dict[str, Any], name: Optional[str] = None):
    """把平台 bundle 规范化为 WorkflowDocument。

    优先使用带坐标的完整画布；否则回退到 API prompt；两者都缺失时用公开详情
    重建拓扑。名称优先取调用方指定，其次取 bundle 内名称。
    """
    canvas_json = bundle.get("canvas_json")
    api_json = bundle.get("api_json")
    detail = bundle.get("detail") or {}
    resolved_name = str(name or bundle.get("name") or detail.get("name") or "workflow")

    if isinstance(canvas_json, dict) and canvas_json:
        document = _parse_payload(canvas_json, "canvas", resolved_name)
    elif isinstance(api_json, dict) and api_json:
        document = _parse_payload(api_json, "comfyui", resolved_name)
    elif isinstance(detail, dict) and detail:
        try:
            source_graph = extract_source_graph(detail)
            synthesized = source_graph if source_graph is not None else build_topology_from_public_detail(detail)
            document = _parse_payload(synthesized, "canvas" if isinstance(synthesized.get("nodes"), list) else "comfyui", resolved_name)
        except Exception as exc:
            raise CleanroomException(502, "WORKFLOW_PROVIDER_INVALID_RESPONSE",
                                     f"从公开元数据重建工作流失败：{exc}") from None
    else:
        raise CleanroomException(502, "WORKFLOW_PROVIDER_INVALID_RESPONSE",
                                 "平台未返回可解析的工作流数据")

    document_as_dict = document.as_dict()
    nodes = document_as_dict.get("nodes", [])
    has_meaningful_positions = any(
        isinstance(n.get("position"), dict)
        and (n["position"].get("x", 0) != 0 or n["position"].get("y", 0) != 0)
        for n in nodes
    )
    if not has_meaningful_positions and nodes:
        positions = compute_layout(nodes, document_as_dict.get("connections", []))
        for n in nodes:
            nid = str(n.get("id"))
            if nid in positions:
                coord = positions[nid]
                pos = n.get("position")
                if not isinstance(pos, dict):
                    pos = {}
                pos["x"] = coord[0]
                pos["y"] = coord[1]
                n["position"] = pos

    source_url = bundle.get("source_url")
    if isinstance(source_url, str) and source_url:
        metadata = dict(document_as_dict.get("metadata") or {})
        metadata["source_url"] = source_url
        document_as_dict["metadata"] = metadata
    if bundle.get("workflow_id"):
        document_as_dict["external_workflow_id"] = str(bundle["workflow_id"])
        if bundle.get("provider"):
            document_as_dict["external_provider"] = bundle["provider"]
    return _DocumentView(document_as_dict)


class _DocumentView:
    """轻量视图：让调用方继续使用 ``.as_dict()`` 语义而不必改变既有代码。"""

    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload

    def as_dict(self) -> dict[str, Any]:
        return self._payload


def _public_preview_from_bundle(bundle: dict[str, Any]) -> Optional[dict[str, Any]]:
    """无原图时只展示公开清单，不生成节点实例、参数或可执行文档。"""
    if bundle.get("provider") != "runninghub" or bundle.get("canvas_json") or bundle.get("api_json"):
        return None
    detail = bundle.get("detail")
    if not isinstance(detail, dict) or extract_source_graph(detail) is not None:
        return None

    def names(key):
        values = detail.get(key)
        if not isinstance(values, list):
            return []
        return list(dict.fromkeys(value.strip()[:512] for value in values[:500]
                                  if isinstance(value, str) and value.strip()))

    node_types = list(dict.fromkeys(names("primitiveNodes") + names("customNodes")))
    models = names("usedModels")
    if not node_types and not models:
        return None
    count = detail.get("nodeCount")
    return {
        "data_completeness": "public_metadata_only", "data_status": "preview",
        "data_gaps": ["original_workflow_graph"],
        "public_preview": {
            "workflow_id": str(bundle.get("workflow_id") or ""),
            "name": str(detail.get("name") or "RunningHub 公开信息")[:512],
            "source_url": str(bundle.get("source_url") or ""),
            "node_types": node_types, "models": models,
            "published_node_count": count if type(count) is int and count >= 0 else None,
            "message": "公开信息预览：缺少原始拓扑、连线和参数，不能执行或导出为工作流。请配置 RH 登录 Token/API Key，或导入原始工作流 JSON。",
        },
    }

#: Media types the asset boundary is willing to serve back.
_WORKFLOW_MEDIA_TYPES = {
    "image/png", "image/jpeg", "image/jpg", "image/webp", "image/gif", "image/bmp",
    "video/mp4", "video/quicktime", "video/webm", "video/x-matroska",
}
#: Explicit, bounded local-ComfyUI probe boundary.  The URL is operator config
#: (GW_COMFYUI_URL) — never a caller-supplied host — and responses are capped.
_COMFY_PROBE_TIMEOUT = 5.0
_COMFY_PROBE_MAX_BYTES = 8 * 1024 * 1024

# The domain router is deliberately unprefixed so the application can expose the
# canonical ``god-workflow`` namespace and the HTTP compatibility namespace from
# the same implementation.  No handler or storage code is duplicated.
router = APIRouter(tags=["god-workflow"])
_STORAGE_LOCK = registry._STORAGE_LOCK


def _workflow_root() -> Path:
    try:
        root = resolve_runtime_paths().data_root / "workflow"
    except RuntimePathError as exc:
        raise CleanroomException(400, exc.code, str(exc)) from None
    root.mkdir(parents=True, exist_ok=True)
    return root


def _auth(authorization: Optional[str], role: str, write: bool) -> AuthContext:
    return require_edit_access(authorization, role) if write else require_authenticated(authorization, role)


def _workflow_write_auth(authorization: Optional[str], role: str) -> AuthContext:
    """认证工作流写入，并把只读项目资源按隐藏资源处理。"""
    context = require_authenticated(authorization, role)
    if context.role not in EDIT_ROLES:
        raise CleanroomException(404, "WORKFLOW_NOT_FOUND", "工作流不存在")
    return context


def _principal_root(context: AuthContext) -> Path:
    root = _workflow_root() / registry.principal_digest(context.identity_domain, context.subject)
    root.mkdir(parents=True, exist_ok=True)
    return root


def _path(context: AuthContext, workflow_id: str) -> Path:
    return _principal_root(context) / _workflow_filename(workflow_id)


def _workflow_filename(workflow_id: str) -> str:
    """生成规范存储文件名，不创建主体目录。"""
    try:
        parsed = uuid.UUID(workflow_id)
        filename = f"{parsed}.json"
    except (ValueError, AttributeError):
        cleaned = re.sub(r'[^a-zA-Z0-9_\-]', '', str(workflow_id or ''))
        if not cleaned or cleaned.casefold() in _PRIVATE_METADATA_NAMES:
            raise CleanroomException(404, "WORKFLOW_NOT_FOUND", "工作流不存在") from None
        filename = f"{cleaned}.json"
    return filename


def _folder_root(context: AuthContext) -> Path:
    root = _principal_root(context) / "folders"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _folder_path(context: AuthContext, folder_id: str) -> Path:
    try:
        parsed = uuid.UUID(folder_id)
    except (ValueError, AttributeError):
        raise CleanroomException(404, "FOLDER_NOT_FOUND", "目录不存在") from None
    return _folder_root(context) / f"{parsed}.json"


def _load_folder(context: AuthContext, folder_id: str) -> dict[str, Any]:
    path = _folder_path(context, folder_id)
    if not path.is_file():
        raise CleanroomException(404, "FOLDER_NOT_FOUND", "目录不存在")
    return _load(path)


def _folder_revision(body: dict[str, Any], request: Request) -> int:
    expected = body.get("expected_revision", request.query_params.get("expected_revision"))
    return _expected_version(expected)


def _check_folder_revision(folder: dict[str, Any], expected: int) -> None:
    current = int(folder.get("revision", 1))
    if expected != current:
        raise VersionConflictException(expected, current, "expected_revision 与当前目录 revision 不一致")


def _folder_tree(context: AuthContext) -> list[dict[str, Any]]:
    folders = []
    for path in sorted(_folder_root(context).glob("*.json")):
        try:
            folders.append(_load(path))
        except CleanroomException:
            continue
    by_parent: dict[str | None, list[dict[str, Any]]] = {}
    for item in folders:
        by_parent.setdefault(item.get("parent_id"), []).append({**item, "children": []})
    def build(parent: str | None) -> list[dict[str, Any]]:
        result = by_parent.get(parent, [])
        for item in result:
            item["children"] = build(item["folder_id"])
        return result
    return build(None)


def _atomic_write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.stem}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_name, path)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def _load(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise CleanroomException(404, "WORKFLOW_NOT_FOUND", "工作流不存在")
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
        # 旧本地导入把原生格式0.4转成CAS 0；只兼容这一已确证形状，读取不落盘。
        if (isinstance(document, dict) and type(document.get("version")) is int and document["version"] == 0
                and document.get("source_type") == "local_file" and document.get("source") == "canvas"
                and isinstance(document.get("metadata"), dict) and document["metadata"].get("version") == 0.4
                and isinstance(document.get("raw_payload"), dict) and document["raw_payload"].get("version") == 0.4
                and isinstance(document["raw_payload"].get("nodes"), list)):
            document["version"] = 1
        return document
    except (OSError, json.JSONDecodeError) as exc:
        raise CleanroomException(500, "WORKFLOW_STORAGE_ERROR", "工作流存储内容不可读取") from exc


def _workflow_project_id(document: dict[str, Any]) -> Optional[str]:
    if not isinstance(document, dict):
        return None
    metadata = document.get("metadata")
    source_context = metadata.get("source_context") if isinstance(metadata, dict) else None
    project_id = source_context.get("project_id") if isinstance(source_context, dict) else None
    return project_id if isinstance(project_id, str) and project_id else None


def _workflow_not_found() -> CleanroomException:
    return CleanroomException(404, "WORKFLOW_NOT_FOUND", "工作流不存在")


def _authorize_workflow_document(context: AuthContext, path: Path, document: dict[str, Any], *, write: bool) -> None:
    """定位工作流后应用项目权限。

    无项目上下文的旧文档仍只对所属主体可见；绑定项目后统一遵守项目读写策略。
    """
    project_id = _workflow_project_id(document)
    own_root = registry.principal_digest(context.identity_domain, context.subject)
    if write and context.role not in EDIT_ROLES:
        raise _workflow_not_found()
    if project_id is None:
        if path.parent.name != own_root:
            raise _workflow_not_found()
        return

    from gw.projects_hub import service as projects

    if write:
        projects.require_project_write_context(project_id, context)
        return
    try:
        projects.default_projects_service.get_readable_project(project_id, context)
    except CleanroomException as exc:
        # Resource visibility is fail-closed: an inaccessible project-bound
        # workflow must not reveal whether the workflow or project exists.
        if exc.status_code in (403, 404):
            raise _workflow_not_found() from None
        raise


def _resolve_workflow(context: AuthContext, workflow_id: str, *, write: bool) -> tuple[Path, dict[str, Any]]:
    """在所属主体目录或可读项目目录中解析工作流。"""
    filename = _workflow_filename(workflow_id)
    own_root = _principal_root(context)
    candidates = [own_root / filename]
    workflow_root = _workflow_root()
    if workflow_root.is_dir():
        for principal_root in sorted(workflow_root.iterdir(), key=lambda item: item.name):
            if not principal_root.is_dir() or principal_root == own_root:
                continue
            candidate = principal_root / filename
            if candidate.is_file():
                candidates.append(candidate)

    for path in candidates:
        if not path.is_file():
            continue
        try:
            document = _load(path)
            _authorize_workflow_document(context, path, document, write=write)
        except CleanroomException as exc:
            if exc.status_code == 403 and write and exc.code == "PROJECT_READ_ONLY":
                raise
            if exc.status_code in (403, 404) and exc.code in {
                "WORKFLOW_NOT_FOUND", "PROJECT_NOT_FOUND", "PROJECT_READ_FORBIDDEN"
            }:
                continue
            raise
        return path, document
    raise _workflow_not_found()


def _authorize_workflow_bridge(context: AuthContext) -> None:
    """要求至少存在一个当前主体可写的项目绑定工作流。"""
    workflow_root = _workflow_root()
    if not workflow_root.is_dir():
        raise _workflow_not_found()
    for principal_root in sorted(workflow_root.iterdir(), key=lambda item: item.name):
        if not principal_root.is_dir():
            continue
        for path in sorted(principal_root.glob("*.json")):
            if path.stem.casefold() in _PRIVATE_METADATA_NAMES:
                continue
            try:
                document = _load(path)
            except CleanroomException:
                continue
            if _workflow_project_id(document) is None:
                continue
            try:
                _authorize_workflow_document(context, path, document, write=True)
            except CleanroomException as exc:
                if exc.status_code == 403 and exc.code == "PROJECT_READ_ONLY":
                    raise
                if exc.status_code in (403, 404):
                    continue
                raise
            return
    # 旧版无项目上下文的文档只允许当前主体通过待处理回执重试桥接；
    # 没有回执时仍按项目资源隐藏规则返回 404。
    pending_root = _principal_root(context) / "asset-hub-pending"
    if any(pending_root.glob("*.json")):
        return
    raise _workflow_not_found()


def _stored_document_items(context: AuthContext):
    """遍历当前主体及其可读项目中的真实工作流文档。"""
    workflow_root = _workflow_root()
    if not workflow_root.is_dir():
        return
    for principal_root in sorted(workflow_root.iterdir(), key=lambda item: item.name):
        if not principal_root.is_dir():
            continue
        for path in sorted(principal_root.glob("*.json")):
            if path.stem.casefold() in _PRIVATE_METADATA_NAMES:
                continue
            try:
                document = _load(path)
                _authorize_workflow_document(context, path, document, write=False)
            except CleanroomException as exc:
                if exc.status_code in (403, 404):
                    continue
                raise
            if isinstance(document, dict) and isinstance(document.get("nodes"), list):
                yield path, document


def _reparse_flags(document: dict[str, Any]) -> dict[str, Any]:
    """读取时动态判定过期标记；只随响应透出，绝不写回存储。"""
    needs, reason = legacy_reparse_state(document)
    return {"needs_reparse": needs, "reparse_reason": reason}


def _summary(document: dict[str, Any], workflow_id: str) -> dict[str, Any]:
    st = document.get("source_type") or document.get("source")
    return {"workflow_id": workflow_id, "name": document.get("name", "workflow"),
            "source": document.get("source"), "source_type": st, "source_url": document.get("source_url"),
            "folder_id": document.get("folder_id") or "parsed_default", "version": document.get("version", 1),
            "node_count": len(document.get("nodes", [])),
            "connection_count": len(document.get("connections", [])),
            "can_run_locally": bool(document.get("env_diff", {}).get("can_run_locally", False)),
            **_reparse_flags(document),
            "document": {"workflow_id": workflow_id, "name": document.get("name", "workflow"),
                         "source": document.get("source"), "source_type": st, "source_url": document.get("source_url"),
                         "folder_id": document.get("folder_id") or "parsed_default", "version": document.get("version", 1),
                         "node_count": len(document.get("nodes", [])),
                         "connection_count": len(document.get("connections", [])),
                         **_reparse_flags(document)}}



def _expected_version(value):
    if type(value) is int and value > 0:
        return value
    if isinstance(value, str) and value.isascii() and value.isdecimal() and int(value) > 0:
        return int(value)
    raise CleanroomException(400, "INVALID_VERSION", "expected_version 必须为正整数")


def _source_context(context, value):
    """只引用当前主体仍有权访问的项目/画布，并记录实际版本。"""
    if not isinstance(value, dict):
        raise CleanroomException(400, "INVALID_REQUEST", "来源上下文必须为对象")
    project_id, canvas_id = value.get("project_id"), value.get("canvas_id")
    if project_id is None and canvas_id is None:
        return {}
    if not isinstance(project_id, str) or not project_id:
        raise CleanroomException(400, "INVALID_REQUEST", "画布来源必须同时指定 project_id")
    from gw.projects_hub import service as projects
    try:
        project = projects.require_project_write_context(project_id, context)
    except CleanroomException as exc:
        # 旧客户端只携带 project_id 时没有可执行画布上下文，保留其
        # 历史死链 404 语义；完整项目+画布来源仍返回只读门禁 403。
        if exc.status_code == 403 and canvas_id is None:
            raise CleanroomException(404, "PROJECT_NOT_FOUND", "来源项目不可访问") from None
        raise
    if value.get("project_version") is not None and _expected_version(value["project_version"]) != project.version:
        raise VersionConflictException(value["project_version"], project.version)
    result = {"project_id": project_id, "project_version": project.version}
    if canvas_id is not None:
        if not isinstance(canvas_id, str) or not canvas_id:
            raise CleanroomException(400, "INVALID_REQUEST", "canvas_id 无效")
        from gw.god_canvas.service import default_god_canvas_service
        canvas = default_god_canvas_service.get_canvas(canvas_id)
        if canvas.project_id != project_id or getattr(canvas, "archived_at", None) or getattr(canvas, "deleted_at", None):
            raise CleanroomException(404, "CANVAS_NOT_FOUND", "来源画布不可访问")
        if value.get("canvas_version") is not None and _expected_version(value["canvas_version"]) != canvas.version:
            raise VersionConflictException(value["canvas_version"], canvas.version)
        result.update({"canvas_id": canvas_id, "canvas_version": canvas.version})
    return result


async def _json_body(request: Request, *, allow_empty: bool = False) -> Any:
    try:
        raw = await request.body()
        if allow_empty and not raw.strip():
            return {}
        return json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError, ValueError):
        raise CleanroomException(400, "INVALID_REQUEST", "请求体必须是有效 JSON") from None


def _parse_payload(request_payload: Any, source: str, name: str):
    try:
        parsed = parse_workflow(request_payload, source=source, name=name)
        # 用户导入的元数据不能冒充服务端验证过的宿主编译缓存。
        if isinstance(parsed.metadata, dict):
            parsed.metadata.pop('resolved_api', None)
        return parsed
    except WorkflowParseError as exc:
        raise CleanroomException(400, "INVALID_WORKFLOW", str(exc)) from None


@router.get("")
def workflow_root(authorization: Optional[str] = Header(None), x_user_role: str = Header("editor", alias="X-User-Role")):
    """返回统一工作流 API 的能力入口，供前端动态端点发现使用。"""
    return capabilities(authorization, x_user_role)


@router.get("/sources")
def sources(authorization: Optional[str] = Header(None), x_user_role: str = Header("editor", alias="X-User-Role")):
    _auth(authorization, x_user_role, False)
    return {"sources": [source.value for source in WorkflowSource], "data_status": "ok", "data_gaps": []}


@router.post("/parse", status_code=status.HTTP_200_OK)
async def parse(request: Request, authorization: Optional[str] = Header(None), x_user_role: str = Header("editor", alias="X-User-Role")):
    _auth(authorization, x_user_role, True)
    payload = await _json_body(request)
    document = _parse_payload(payload, request.query_params.get("source", "comfyui"), request.query_params.get("name", "workflow"))
    return {"workflow": document.as_dict(), "data_status": "ok", "data_gaps": []}


@router.post("/upload", status_code=status.HTTP_200_OK)
async def upload(request: Request, authorization: Optional[str] = Header(None), x_user_role: str = Header("editor", alias="X-User-Role")):
    _auth(authorization, x_user_role, True)
    content_type = request.headers.get("content-type", "").split(";", 1)[0].lower()
    raw = await request.body()
    if content_type == "multipart/form-data":
        try:
            form = await request.form()
            upload_file = form.get("file")
            if upload_file is None or not hasattr(upload_file, "read"):
                raise CleanroomException(400, "INVALID_WORKFLOW_UPLOAD", "multipart 请求缺少 file 字段")
            raw = await upload_file.read()
            content_type = str(getattr(upload_file, "content_type", "") or "application/octet-stream").split(";", 1)[0].lower()
            name = str(getattr(upload_file, "filename", "workflow") or "workflow")
        except CleanroomException:
            raise
        except Exception as exc:
            raise CleanroomException(400, "INVALID_WORKFLOW_UPLOAD", "multipart 上传不可读取") from exc
    else:
        name = request.query_params.get("name", "workflow")
    if len(raw) > MAX_UPLOAD_BYTES:
        raise CleanroomException(413, "WORKFLOW_UPLOAD_TOO_LARGE", "上传文件超过大小限制")
    try:
        document = parse_upload(raw, content_type, source=request.query_params.get("source", "comfyui"), name=name)
    except WorkflowParseError as exc:
        raise CleanroomException(400, "INVALID_WORKFLOW_UPLOAD", str(exc)) from None
    return {"workflow": document.as_dict(), "data_status": "ok", "data_gaps": []}


@router.post("/providers/{provider}/fetch", status_code=status.HTTP_200_OK)
async def provider_fetch(provider: str, request: Request, authorization: Optional[str] = Header(None), x_user_role: str = Header("editor", alias="X-User-Role")):
    """按真实平台协议抓取工作流。

    凭据来自当前主体设置或环境变量回退，解析请求不携带密钥。
    """
    context = _auth(authorization, x_user_role, True)
    if provider not in PROVIDER_ADAPTERS:
        raise CleanroomException(400, "INVALID_PROVIDER", "不支持的工作流平台")
    body = await _json_body(request, allow_empty=True)
    body = body if isinstance(body, dict) else {}
    reference = body.get("workflow_id", body.get("url_or_text"))
    if not isinstance(reference, str) or not reference.strip():
        raise CleanroomException(400, "INVALID_PROVIDER_REFERENCE", "必须提供工作流链接或 ID")
    bundle = await _fetch_provider_bundle(provider, reference.strip(), context)
    document = _document_from_bundle(bundle, body.get("name"))
    return {"workflow": document.as_dict(), "workflow_id": bundle.get("workflow_id"),
            "source_url": bundle.get("source_url"), "data_status": "ok", "data_gaps": []}


def _comfy_endpoint(context: AuthContext, task: Optional[dict] = None):
    snapshot = (task or {}).get("executor_snapshot")
    fallback = comfy_executor.get_endpoint()
    if isinstance(snapshot, dict) and "base_url" in snapshot:
        return comfy_executor.ComfyEndpoint(snapshot["base_url"], snapshot.get("timeout", fallback.timeout))
    settings = registry.read_settings(context)
    return comfy_executor.ComfyEndpoint(str(settings.get("comfy_url") or fallback.base_url).strip(), fallback.timeout)


async def _comfy_call(context: AuthContext, method, *args, task=None, **kwargs):
    with comfy_executor.using_endpoint(_comfy_endpoint(context, task)):
        return await method(*args, **kwargs)


async def _native_schemas(context: AuthContext, document: dict, *, for_bindings: bool = False) -> dict:
    """只有缺少 API 图字段映射时才读取当前主体配置的真实节点 Schema。"""
    schemas = deepcopy(document.get("metadata", {}).get("node_schemas") or {})
    missing = {node.get("kind") or node.get("class_type") for node in document.get("nodes", [])
               if ((not isinstance((node.get("raw_payload") or {}).get("inputs"), list)
                    and any(not isinstance(value, (list, tuple)) for value in (node.get("inputs") or {}).values()))
                   or (for_bindings and node.get("metadata", {}).get("input_assets") and node.get("widgets")))
               and not isinstance(schemas.get(node.get("kind") or node.get("class_type")), dict)}
    if missing:
        if not _comfy_endpoint(context).configured:
            raise CleanroomException(422, "NODE_SCHEMA_REQUIRED", "API 图转原生图需要真实节点 Schema，请先配置并连接 ComfyUI")
        probe = await _comfy_call(context, comfy_executor.probe)
        schemas.update(probe.get("object_info") or {})
        if any(not isinstance(schemas.get(kind), dict) for kind in missing):
            raise CleanroomException(422, "NODE_SCHEMA_REQUIRED", "未取得所需节点的真实 Schema，原文档及素材绑定保持不变")
    return schemas


def _native_definition_gaps(document: dict) -> list[str]:
    """原生参数值可以原样读取；参数定义仅认文件已有真实 Schema 或宿主定义。"""
    schemas = document.get('metadata', {}).get('node_schemas') or {}
    return sorted({node.get('kind') or node.get('class_type') for node in document.get('nodes', [])
                   if (node.get('kind') or node.get('class_type')) not in {'Note', 'MarkdownNote'}
                   and isinstance((node.get('raw_payload') or {}).get('inputs'), list)
                   and isinstance(node.get('widgets'), list) and node['widgets']
                   and not isinstance(schemas.get(node.get('kind') or node.get('class_type')), dict)})


def _definition_cache_path(context: AuthContext) -> Path:
    endpoint = _comfy_endpoint(context).base_url
    return _principal_root(context) / 'node-definitions' / (hashlib.sha256(endpoint.encode()).hexdigest() + '.json')


async def _resolve_definitions_in_workbench(context: AuthContext, document: dict) -> bool:
    """先用主体/端点隔离的已验证缓存；在线时直接查定义，不打开或启动宿主。"""
    if not _native_definition_gaps(document):
        try:
            compile_prompt(document, document.get('metadata', {}).get('node_schemas'))
            return False
        except CleanroomException:
            return True
    schemas = deepcopy(document.get('metadata', {}).get('node_schemas') or {})
    path = _definition_cache_path(context)
    try:
        cached = json.loads(path.read_text(encoding='utf-8')) if path.is_file() else {}
    except (OSError, ValueError):
        cached = {}
    if isinstance(cached, dict) and cached.get('source') == 'comfyui_object_info' and isinstance(cached.get('schemas'), dict):
        schemas.update({kind: deepcopy(schema) for kind, schema in cached['schemas'].items() if isinstance(schema, dict)})
        document.setdefault('metadata', {})['schema_source'] = 'verified_cache'
    document.setdefault('metadata', {})['node_schemas'] = schemas
    if _native_definition_gaps(document) and _comfy_endpoint(context).configured:
        control = _comfy_operation(context, 'status')
        if control.get('highlight'):
            try:
                probe = await _comfy_call(context, comfy_executor.probe)
            except CleanroomException:
                return True
            actual = probe.get('object_info') or {}
            if probe.get('online') and isinstance(actual, dict):
                schemas.update({kind: deepcopy(schema) for kind, schema in actual.items() if isinstance(schema, dict)})
                document['metadata']['node_schemas'] = {kind: schemas[kind] for kind in _node_class_ids(document) if kind in schemas}
                document['metadata']['schema_source'] = 'comfyui_object_info'
                with _STORAGE_LOCK:
                    _atomic_write(path, {'source': 'comfyui_object_info', 'schemas': schemas, 'observed_at': time.time()})
    if _native_definition_gaps(document):
        return True
    try:
        compile_prompt(document, schemas)
    except CleanroomException as error:
        return True
    return False


def _preserve_native_bindings(current: dict, stored: dict, schemas: dict) -> None:
    """宿主元数据不建立素材权限；只迁移同节点、同字段且值未改变的原绑定。"""
    original_nodes = {str(node["id"]): node for node in current.get("nodes", [])}
    for node in stored.get("nodes", []):
        metadata = node.setdefault("metadata", {})
        metadata.pop("input_assets", None)
        original = original_nodes.get(str(node["id"]))
        if not original or original.get("kind") != node.get("kind"):
            continue
        bindings = original.get("metadata", {}).get("input_assets") or {}
        if not bindings:
            continue
        old_maps = {}
        new_maps = {}
        old_prompt = compile_prompt({"nodes": [original]}, schemas, field_maps=old_maps)
        new_prompt = compile_prompt({"nodes": [node]}, schemas, field_maps=new_maps)
        if str(original["id"]) not in old_prompt or str(node["id"]) not in new_prompt:
            raise CleanroomException(422, "UNSUPPORTED_WIDGET_MAPPING", "不能确认原生回存的素材字段，原文档保持不变")
        old_inputs = old_prompt[str(original["id"])]["inputs"]
        new_inputs = new_prompt[str(node["id"])]["inputs"]
        connected_fields = set()
        for link in stored.get("connections", []):
            if str(link["target"]) != str(node["id"]):
                continue
            field = str(link.get("target_slot") or "0")
            ports = (node.get("raw_payload") or {}).get("inputs")
            if field.isdigit() and isinstance(ports, list):
                slot = int(field)
                if slot >= len(ports) or not ports[slot].get("name"):
                    raise CleanroomException(422, "INVALID_WORKFLOW", "原生回存的输入槽位无效")
                field = ports[slot]["name"]
            connected_fields.add(field)
        for field, asset_id in bindings.items():
            actual_field = old_maps[str(original["id"])].get(field, field)
            # 宿主连线优先于旧位置参数，上传旧素材不能覆盖新拓扑。
            if actual_field in connected_fields:
                continue
            if actual_field not in old_inputs or actual_field not in new_inputs:
                raise CleanroomException(422, "UNSUPPORTED_WIDGET_MAPPING", "原生图遗漏已绑定素材的参数，原文档保持不变")
            if old_inputs[actual_field] == new_inputs[actual_field]:
                saved_field = next((name for name, actual in new_maps[str(node["id"])].items() if actual == actual_field), actual_field)
                metadata.setdefault("input_assets", {})[saved_field] = asset_id


@router.post("/tasks/execute", status_code=status.HTTP_202_ACCEPTED)
async def execute_task(request: Request, authorization: Optional[str] = Header(None), x_user_role: str = Header("editor", alias="X-User-Role"), idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key")):
    """提交本地 ComfyUI 执行，返回真实 task_id。

    只走 ComfyUI 官方 ``POST /prompt``；未配置 ``GW_COMFYUI_URL`` 时返回标准
    503，不下发任何请求，也不伪造 task_id。
    """
    context = _workflow_write_auth(authorization, x_user_role)
    body = await _json_body(request, allow_empty=True)
    if not isinstance(body, dict):
        raise CleanroomException(400, "INVALID_REQUEST", "请求体必须是 JSON 对象")

    request_fingerprint = hashlib.sha256(json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
    existing = registry.find_idempotent_task(context, idempotency_key or "", request_fingerprint)
    if existing is not None:
        return {**existing, "poll_hint": f"/api/god_workflow/tasks/{existing['job_id']}", "idempotent_replay": True,
                "data_status": "ok", "data_gaps": []}
    workflow_id = body.get("workflow_id")
    document = {}
    widget_fields = {}
    if workflow_id is not None and (not isinstance(workflow_id, str) or not workflow_id):
        raise CleanroomException(400, "INVALID_REQUEST", "workflow_id 必须为非空字符串")
    if isinstance(workflow_id, str) and workflow_id:
        if "source_context" in body:
            # 完整项目+画布来源是写入项目上下文的动作，严格执行写门禁。
            # 仅 project_id 的旧调用先读回文档，再由来源校验保留死链 404。
            source_value = body.get("source_context")
            strict_source = isinstance(source_value, dict) and "canvas_id" in source_value
            _workflow_path, document = _resolve_workflow(context, workflow_id, write=strict_source)
        else:
            # 兼容旧客户端：未携带来源时不把文档元数据当执行来源，
            # 但项目绑定文档仍必须由项目owner本人执行，避免读授权越权运行。
            _workflow_path, document = _resolve_workflow(context, workflow_id, write=False)
            project_id = _workflow_project_id(document)
            if project_id is not None:
                from gw.projects_hub import service as projects
                projects.default_projects_service.get_owned_project(
                    project_id, projects.owner_key_for_context(context)
                )
        if "expected_version" in body and body["expected_version"] != document["version"]:
            raise VersionConflictException(body["expected_version"], document["version"])
        schemas = document.get("metadata", {}).get("node_schemas")
        needs_schema = any(isinstance(node.get("widgets"), list) and any(not isinstance(value, dict) for value in node["widgets"])
                           for node in document.get("nodes", []))
        if needs_schema and not schemas and str(body.get("target") or "local_comfy") in ("local_comfy", "comfyui"):
            probe = await _comfy_call(context, comfy_executor.probe)
            if not probe.get("online"):
                raise unavailable("WORKFLOW_EXECUTOR_UNAVAILABLE", "本地节点 Schema 不可读取；请检查 ComfyUI 配置或导入 API JSON")
            schemas = probe.get("object_info")
        widget_fields = {}
        prompt = compile_prompt(document, schemas, field_maps=widget_fields)
    else:
        candidate = body.get("prompt", body.get("workflow"))
        prompt = candidate if isinstance(candidate, dict) else {}
    if not prompt:
        raise CleanroomException(400, "INVALID_WORKFLOW", "缺少可执行的 API prompt")
    # 深拷贝后覆盖，拒绝未知字段，不改变保存的工作流。
    prompt = json.loads(json.dumps(prompt))
    node_info = body.get("node_info_list", body.get("nodeInfoList"))
    if node_info is None:
        node_info = _node_info_list(document) if isinstance(workflow_id, str) and workflow_id else []
    if not isinstance(node_info, list):
        raise CleanroomException(400, "INVALID_REQUEST", "参数覆盖必须为数组")
    target = str(body.get("target") or "local_comfy").lower()
    if target in ("local_comfy", "comfyui"):
        target = "local_comfy"
        endpoint = _comfy_endpoint(context)
        with comfy_executor.using_endpoint(endpoint):
            comfy_executor.require_endpoint()
        for item in node_info:
            if not isinstance(item, dict):
                raise CleanroomException(400, "INVALID_REQUEST", "参数覆盖格式错误")
            node = prompt.get(str(item.get("nodeId")))
            field = widget_fields.get(str(item.get("nodeId")), {}).get(item.get("fieldName"), item.get("fieldName")) if workflow_id else item.get("fieldName")
            if not isinstance(node, dict) or not isinstance(node.get("inputs"), dict) or field not in node["inputs"]:
                raise CleanroomException(400, "INVALID_REQUEST", "参数覆盖节点或字段不存在")
            node["inputs"][field] = item.get("fieldValue")
    elif target in ("rh_cloud", "runninghub"):
        target = "rh_cloud"
        api_key = registry.credential_value("rh_api_key", context)
        if not api_key:
            raise unavailable("WORKFLOW_EXECUTOR_UNAVAILABLE", "RunningHub API Key is not configured")
        # 始终直传当前完整图；已有真实RH标识兼容平台必填校验，不使用本地或Lib ID冒充。
        stored_rh_id = document.get('external_workflow_id') if document.get('external_provider') == 'runninghub' else None
        if (stored_rh_id is None and document.get('external_provider') is None
                and document.get('source_type') == 'rh_link'):
            legacy_source = re.fullmatch(r'https?://(?:www\.)?runninghub\.(?:cn|ai)/workflow/(\d{10,25})(?:[/?#].*)?',
                                         str(document.get('source_url') or ''), re.IGNORECASE)
            if legacy_source:
                stored_rh_id = legacy_source.group(1)
        candidate_rh_id = body.get('rh_workflow_id') or stored_rh_id
        transport_workflow_id = (str(candidate_rh_id) if type(candidate_rh_id) in (str, int)
                                 and re.fullmatch(r'\d{10,25}', str(candidate_rh_id)) else None)
        # 保存图上的全部标量参数都参与运行；提升仅决定界面展示，不决定生效范围。
        cloud_values = {(node_id, field): value for node_id, node in prompt.items()
                        for field, value in node.get("inputs", {}).items() if not isinstance(value, (dict, list))}
        for entry in node_info:
            if not isinstance(entry, dict):
                raise CleanroomException(400, "INVALID_REQUEST", "参数覆盖格式错误")
            node_id = str(entry.get("nodeId"))
            field = widget_fields.get(node_id, {}).get(entry.get("fieldName"), entry.get("fieldName"))
            if (node_id, field) not in cloud_values:
                raise CleanroomException(400, "INVALID_REQUEST", "参数覆盖节点或字段不存在")
            cloud_values[node_id, field] = entry.get("fieldValue")
        for (node_id, field), value in cloud_values.items():
            prompt[node_id]['inputs'][field] = value
        node_info = [{"nodeId": node_id, "fieldName": field, "fieldValue": value}
                     for (node_id, field), value in cloud_values.items()]
    else:
        raise unavailable("WORKFLOW_EXECUTOR_UNAVAILABLE", "不支持的执行目标")
    # 在任何素材上传前核对实际编译图，虚拟节点或未知字段不能产生外部副作用。
    for node in (document.get("nodes", []) if workflow_id else []):
        for field in node.get("metadata", {}).get("input_assets", {}):
            node_id = str(node["id"])
            actual_field = widget_fields.get(node_id, {}).get(field, field)
            compiled_node = prompt.get(node_id)
            if not isinstance(compiled_node, dict) or actual_field not in compiled_node.get("inputs", {}):
                raise CleanroomException(422, "INPUT_ASSET_FIELD_UNRESOLVED", "输入素材字段无法映射到实际执行图，请补齐节点定义")
    # 执行来源只认请求显式携带的来源上下文；文档元数据里的旧来源不作为执行依据。
    source_context = _source_context(context, body.get("source_context") or {})
    # 原子预留必须先于任何外部副作用；未绑定ID的账本一律未知且禁止自动重发。
    task, replay = registry.reserve_task(context, workflow_id=workflow_id, target=target,
                                         node_count=len(prompt), idempotency_key=idempotency_key,
                                         request_fingerprint=request_fingerprint)
    if not replay:
        submission_started = False
        try:
            snapshot = ({"base_url": endpoint.base_url, "timeout": endpoint.timeout} if target == "local_comfy"
                        else {"domain": os.environ.get("GW_RUNNINGHUB_DOMAIN", platforms.RUNNINGHUB_DEFAULT_DOMAIN)})
            task = registry.update_task(context, task["job_id"], executor_snapshot=snapshot,
                                        source_context=source_context, **source_context,
                                        workflow_version=document.get("version") if workflow_id else None,
                                        prompt_snapshot=prompt, node_info_snapshot=node_info)
            if target == "local_comfy":
                for node in (document.get("nodes", []) if workflow_id else []):
                    for field, asset_id in node.get("metadata", {}).get("input_assets", {}).items():
                        resolved = registry.resolve_asset_file(context, str(asset_id))
                        if resolved is None:
                            raise CleanroomException(404, "INPUT_ASSET_NOT_FOUND", "输入素材已不存在或无权读取")
                        path, asset = resolved
                        remote_name = await _comfy_call(context, comfy_executor.upload_input, path.read_bytes(),
                            str(asset_id) + asset.get("extension", ""), asset["content_type"],
                            subfolder=registry.principal_digest(context.identity_domain, context.subject), task=task)
                        actual_field = widget_fields.get(str(node["id"]), {}).get(field, field)
                        prompt[str(node["id"])]["inputs"][actual_field] = remote_name
                task = registry.update_task(context, task["job_id"], prompt_snapshot=prompt)
                submission_started = True
                result = await _comfy_call(context, comfy_executor.submit, prompt, client_id=body.get("client_id"), task=task)
                remote_id = result.get("prompt_id")
            else:
                for node in (document.get("nodes", []) if workflow_id else []):
                    for field, asset_id in node.get("metadata", {}).get("input_assets", {}).items():
                        resolved = registry.resolve_asset_file(context, str(asset_id))
                        if resolved is None:
                            raise CleanroomException(404, "INPUT_ASSET_NOT_FOUND", "输入素材已不存在或无权读取")
                        path, asset = resolved
                        remote_name = await platforms.upload_rh_input(path.read_bytes(),
                            str(asset_id) + asset.get("extension", ""), asset["content_type"], api_key, domain=snapshot["domain"])
                        actual_field = widget_fields.get(str(node["id"]), {}).get(field, field)
                        entry = next((value for value in node_info if value["nodeId"] == str(node["id"]) and value["fieldName"] == actual_field), None)
                        if entry is None:
                            raise CleanroomException(400, "INVALID_REQUEST", "输入素材绑定的字段不存在")
                        entry["fieldValue"] = remote_name
                        prompt[str(node['id'])]['inputs'][actual_field] = remote_name
                task = registry.update_task(context, task["job_id"], node_info_snapshot=node_info, prompt_snapshot=prompt,
                                            submission_mode='full_workflow')
                submission_started = True
                result = await platforms.create_rh_cloud_task(transport_workflow_id, node_info, api_key,
                    domain=snapshot["domain"], workflow=prompt)
                remote_id = result.get("taskId") or result.get("task_id")
            if remote_id:
                task = registry.update_task(context, task["job_id"], remote_task_id=str(remote_id),
                    prompt_id=str(remote_id), remote_id_missing=False, status="accepted",
                    execution_status="accepted", source_status="queued")
            else:
                task = registry.update_task(context, task['job_id'], messages=['RH_TASK_ID_MISSING' if target == 'rh_cloud' else 'COMFY_TASK_ID_MISSING'])
        except (CleanroomException, OSError) as exc:
            # 绑定写盘失败仍返回已预留公共ID，不因错误重发外部请求。
            task = registry.load_task(context, task["job_id"])
            task["messages"] = [exc.code if isinstance(exc, CleanroomException) else "TASK_LEDGER_WRITE_FAILED"]
            if isinstance(exc, CleanroomException) and exc.code == 'RH_TASK_CREATE_REJECTED':
                provider_code = exc.extra.get('provider_code')
                if isinstance(provider_code, str) and re.fullmatch(r'-?\d{1,10}|UNKNOWN', provider_code):
                    task['messages'].append('RunningHub 错误码: ' + provider_code)
            if not submission_started or (isinstance(exc, CleanroomException) and exc.code == 'RH_TASK_CREATE_REJECTED'):
                task = registry.update_task(context, task["job_id"], status="failed", execution_status="failed",
                                            remote_id_missing=False, messages=task["messages"])
            else:
                task = registry.update_task(context, task['job_id'], messages=task['messages'])
        except Exception as exc:
            # 提交阶段的意外异常不能升级为 HTTP 500；只记录异常类型，绝不记录密钥或异常正文。
            logger.warning("工作流提交内部错误 target=%s exception=%s", target, type(exc).__name__)
            task = registry.load_task(context, task["job_id"])
            diagnostic = "RH_SUBMIT_INTERNAL_ERROR" if target == "rh_cloud" else "COMFY_SUBMIT_INTERNAL_ERROR"
            task["messages"] = [diagnostic, "exception: " + type(exc).__name__]
            if not submission_started:
                task = registry.update_task(context, task["job_id"], status="failed", execution_status="failed",
                                            remote_id_missing=False, messages=task["messages"])
            else:
                task = registry.update_task(context, task["job_id"], messages=task["messages"])
    return {**task, "poll_hint": f"/api/god_workflow/tasks/{task['job_id']}",
            "idempotent_replay": replay, "data_status": "ok",
            "data_gaps": ["remote_task_id_missing"] if task.get("remote_id_missing") else []}


def _encode_task_cursor(created_at: str, task_id: str) -> str:
    """游标不透明：base64url（无填充）的紧凑 JSON {"c": created_at, "t": task_id}。"""
    payload = json.dumps({"c": created_at, "t": task_id}, separators=(",", ":"))
    return base64.urlsafe_b64encode(payload.encode("utf-8")).decode("ascii").rstrip("=")


def _decode_task_cursor(cursor: str) -> tuple[str, str]:
    if not cursor or len(cursor) > 512:
        raise CleanroomException(400, "INVALID_CURSOR", "分页游标无效")
    try:
        padded = cursor.translate(str.maketrans("-_", "+/"))
        padded += "=" * (-len(padded) % 4)
        obj = json.loads(base64.b64decode(padded, validate=True).decode("utf-8"))
    except ValueError:  # binascii.Error / UnicodeDecodeError / JSONDecodeError 均是 ValueError
        raise CleanroomException(400, "INVALID_CURSOR", "分页游标无效") from None
    if not isinstance(obj, dict) or not isinstance(obj.get("c"), str) or not isinstance(obj.get("t"), str):
        raise CleanroomException(400, "INVALID_CURSOR", "分页游标无效")
    return obj["c"], obj["t"]


def _authorize_task_scope(context: AuthContext, task: dict[str, Any], *, write: bool) -> None:
    """对任务读写操作应用任务中保存的项目上下文。"""
    source_context = task.get("source_context")
    project_id = source_context.get("project_id") if isinstance(source_context, dict) else None
    if not isinstance(project_id, str) or not project_id:
        return
    from gw.projects_hub import service as projects
    if write:
        projects.require_project_write_context(project_id, context)
        return
    try:
        projects.default_projects_service.get_readable_project(project_id, context)
    except CleanroomException as exc:
        if exc.status_code in (403, 404):
            raise CleanroomException(404, "TASK_NOT_FOUND", "任务不存在") from None
        raise


@router.get("/tasks")
def list_workflow_tasks(authorization: Optional[str] = Header(None), x_user_role: str = Header("editor", alias="X-User-Role"),
                        workflow_id: Optional[str] = None, limit: Optional[str] = None, cursor: Optional[str] = None):
    context = _auth(authorization, x_user_role, False)
    if workflow_id is None and limit is None and cursor is None:
        return {"tasks": registry.list_tasks(context), "data_status": "ok", "data_gaps": []}
    page_limit = 20
    if limit is not None:
        # 只接受 ASCII 十进制数字，杜绝 "+5"/"1e2"/全角数字被静默解释。
        if not re.fullmatch(r"[0-9]+", limit) or int(limit) < 1:
            raise CleanroomException(400, "INVALID_LIMIT", "limit 必须为正整数")
        page_limit = min(int(limit), 100)
    after = None
    if cursor is not None:
        after = _decode_task_cursor(cursor)
    page, next_after = registry.list_tasks_page(context, workflow_id=workflow_id, limit=page_limit, after=after)
    return {"tasks": page, "workflow_id": workflow_id,
            "next_cursor": _encode_task_cursor(*next_after) if next_after is not None else None,
            "has_more": next_after is not None, "data_status": "ok", "data_gaps": []}


@router.get("/tasks/{task_id}")
async def task_status(task_id: str, authorization: Optional[str] = Header(None), x_user_role: str = Header("editor", alias="X-User-Role")):
    """轮询真实任务状态；终态结果同步登记为素材。"""
    context = _auth(authorization, x_user_role, False)
    task = registry.load_task(context, task_id)
    _authorize_task_scope(context, task, write=False)
    if task.get("status") in {"completed", "failed", "cancelled", "outcome_unknown", "abandoned"}:
        return {**task, "data_status": "ok", "data_gaps": []}
    if task.get("remote_id_missing"):
        return {**task, "data_status": "ok", "data_gaps": ["remote_task_id_missing"]}

    target = str(task.get("target") or "local_comfy")
    if target in ("rh_cloud", "runninghub"):
        api_key = registry.credential_value("rh_api_key", context)
        if not api_key:
            # 配置缺失只阻断查询，不能推断已发出的远端执行失败。
            raise unavailable("WORKFLOW_EXECUTOR_UNAVAILABLE", "RunningHub API Key is not configured")
        domain = task.get("executor_snapshot", {}).get("domain") or os.environ.get("GW_RUNNINGHUB_DOMAIN", platforms.RUNNINGHUB_DEFAULT_DOMAIN)
        query_res = await platforms.query_rh_task_outputs(str(task.get("prompt_id") or ""), api_key, domain=domain)
        st = query_res.get("status")
        if st == "SUCCESS":
            assets = await _collect_rh_task_outputs(context, task_id, query_res.get("outputs", []), platform=target)
            complete = bool(assets) and all(item.get("registered") for item in assets)
            task = registry.update_task(context, task_id, status="completed" if complete else "failed",
                                        only_if_active=True,
                                        source_status="SUCCESS", execution_status="completed",
                                        collection_status="completed" if complete else "partial_failed", outputs=assets,
                                        messages=[] if complete else _collection_messages(assets))
            return {**task, "data_status": "ok", "data_gaps": ([] if complete else ["output_collection_incomplete"])}
        elif st == "RUNNING":
            task = registry.update_task(context, task_id, only_if_active=True,
                                        status="running", source_status="RUNNING", execution_status="running")
            return {**task, "progress": {"known": False, "value": None}, "data_status": "ok", "data_gaps": []}
        elif st == "FAILED":
            msg = query_res.get("message") or "RunningHub 任务执行失败"
            task = registry.update_task(context, task_id, only_if_active=True,
                                        status="failed", source_status="FAILED", execution_status="failed", messages=[msg])
            return {**task, "data_status": "ok", "data_gaps": []}
        # 未确认的回包保留任务身份及原状态，修复查询条件后可续查。
        raise CleanroomException(502, "RH_TASK_STATUS_UNKNOWN", "RunningHub 未返回可确认的任务状态，请稍后续查")

    history = await _comfy_call(context, comfy_executor.history, str(task.get("prompt_id") or ""), task=task)
    if not history["found"]:
        task = registry.update_task(context, task_id, only_if_active=True, status="running")
        return {**task, "progress": {"known": False, "value": None},
                "data_status": "ok", "data_gaps": []}

    summary = comfy_executor.summarize(history["entry"])
    summary_messages = summary["messages"] if isinstance(summary.get("messages"), list) else []
    if summary["status"] != "succeeded":
        # 远端执行失败：history 里残留的输出/错误条目不是产物，一律不下载、不登记。
        task = registry.update_task(context, task_id,
                                    only_if_active=True,
                                    status="failed",
                                    source_status=summary["status"],
                                    execution_status="failed",
                                    outputs=[],
                                    messages=["COMFY_REMOTE_EXECUTION_FAILED",
                                              *_execution_error_lines(summary_messages)])
        return {**task, "data_status": "ok", "data_gaps": []}
    assets = await _collect_task_outputs(context, task_id, summary["outputs"], task=task)
    complete = bool(assets) and all(item.get("registered") for item in assets)
    messages = list(summary_messages) if complete else [*summary_messages, *_collection_messages(assets)]
    task = registry.update_task(context, task_id,
                                only_if_active=True,
                                status="completed" if summary["status"] == "succeeded" and complete else "failed",
                                source_status=summary["status"],
                                execution_status="completed",
                                collection_status="completed" if complete else "partial_failed",
                                outputs=assets, messages=messages)
    return {**task, "data_status": "ok", "data_gaps": []}


@router.post("/tasks/{task_id}/collect")
async def retry_task_collection(task_id: str, authorization: Optional[str] = Header(None), x_user_role: str = Header("editor", alias="X-User-Role")):
    """只重试回收已完成的远端结果，绝不再次提交生成。"""
    context = _auth(authorization, x_user_role, True)
    task = registry.load_task(context, task_id)
    _authorize_task_scope(context, task, write=True)
    if task.get("execution_status") != "completed":
        raise CleanroomException(409, "EXECUTION_NOT_COMPLETED", "执行尚未确认完成，不能回收")
    if task.get("collection_status") == "completed":
        return task
    if task.get("target") in ("rh_cloud", "runninghub"):
        api_key = registry.credential_value("rh_api_key", context)
        if not api_key:
            raise unavailable("WORKFLOW_EXECUTOR_UNAVAILABLE", "RunningHub API Key is not configured")
        result = await platforms.query_rh_task_outputs(task["remote_task_id"], api_key,
            domain=task.get("executor_snapshot", {}).get("domain") or os.environ.get("GW_RUNNINGHUB_DOMAIN", platforms.RUNNINGHUB_DEFAULT_DOMAIN))
        if result.get("status") != "SUCCESS":
            raise CleanroomException(409, "OUTPUTS_NOT_AVAILABLE", "远端结果尚不可回收")
        outputs = await _collect_rh_task_outputs(context, task_id, result.get("outputs", []),
                                                 platform=str(task.get("target") or "rh_cloud"))
    else:
        history = await _comfy_call(context, comfy_executor.history, task["remote_task_id"], task=task)
        if not history["found"]:
            raise CleanroomException(409, "OUTPUTS_NOT_AVAILABLE", "远端结果尚不可回收")
        outputs = await _collect_task_outputs(context, task_id, comfy_executor.summarize(history["entry"])["outputs"], task=task)
    complete = bool(outputs) and all(item.get("registered") for item in outputs)
    if task.get("target") in ("rh_cloud", "runninghub"):
        messages = [] if complete else _collection_messages(outputs)
    else:
        existing = task.get("messages") if isinstance(task.get("messages"), list) else []
        kept = [item for item in existing if not _is_collection_line(item)]
        messages = kept if complete else [*kept, *_collection_messages(outputs)]
    return registry.update_task(context, task_id, status="completed" if complete else "failed",
        collection_status="completed" if complete else "partial_failed", outputs=outputs, messages=messages)


@router.post("/tasks/{task_id}/abandon")
async def abandon_task(task_id: str, request: Request, authorization: Optional[str] = Header(None), x_user_role: str = Header("editor", alias="X-User-Role")):
    """把结果未知且无远端 ID 的任务标记为放弃；本地标记，绝不重发或新建远端任务。"""
    context = _auth(authorization, x_user_role, True)
    body = await _json_body(request, allow_empty=True)
    if not isinstance(body, dict):
        raise CleanroomException(400, "INVALID_REQUEST", "请求体必须是 JSON 对象")
    task = registry.load_task(context, task_id)
    _authorize_task_scope(context, task, write=True)
    reason = body.get("reason", "")
    if not isinstance(reason, str):
        raise CleanroomException(400, "INVALID_REQUEST", "reason 必须为字符串")
    reason = reason[:200]
    try:
        task, changed = registry.abandon_task(context, task_id, reason=reason)
    except CleanroomException as exc:
        if exc.status_code == 409:
            audit.record_management_event(audit.EVENT_WORKFLOW_TASK_ABANDONED, outcome=audit.OUTCOME_REJECTED,
                                          reason=exc.code, subject=context.subject, role=context.role,
                                          resource_id=task_id)
        raise
    if changed:
        audit.record_management_event(audit.EVENT_WORKFLOW_TASK_ABANDONED, outcome=audit.OUTCOME_SUCCEEDED,
                                      reason=reason or "outcome_unknown_no_remote_id",
                                      subject=context.subject, role=context.role, resource_id=task_id)
    return {**task, "abandoned": True, "already_abandoned": not changed, "resubmitted": False,
            "data_status": "ok", "data_gaps": []}


@router.post("/tasks/{task_id}/cancel")
async def task_cancel(task_id: str, authorization: Optional[str] = Header(None), x_user_role: str = Header("editor", alias="X-User-Role")):
    """本地标记取消并尽力中断远端；不谎称远端已取消。"""
    context = _auth(authorization, x_user_role, True)
    task = registry.load_task(context, task_id)
    _authorize_task_scope(context, task, write=True)
    if task.get("status") in {"completed", "failed", "cancelled", "abandoned"} | comfy_executor.TERMINAL_STATES:
        raise CleanroomException(409, "JOB_ALREADY_TERMINAL", "任务已处于终态")
    target = str(task.get("target") or "local_comfy")
    if target in ("rh_cloud", "runninghub"):
        # 不支持远端取消，保持真实执行状态，不以本地标记冒充终态。
        task = registry.update_task(context, task_id, remote_cancelled=False,
                                    messages=["RunningHub remote cancellation is not supported; remote work may continue or bill"])
        return {**task, "remote_cancelled": False, "remote_may_continue_or_bill": True,
                "cancel_supported": False, "data_status": "ok", "data_gaps": ["remote_cancel_unsupported"]}
    # /interrupt 是全局动作，不能确认目标任务且可能中断其他主体；失败关闭。
    return {**task, "remote_cancelled": False, "cancel_supported": False,
            "remote_may_continue_or_bill": True, "data_status": "ok",
            "data_gaps": ["task_scoped_cancel_unsupported"]}



_COLLECTION_LINE = re.compile(
    r"^(?:OUTPUT_COLLECTION_INCOMPLETE: \d+/\d+ registered|OUTPUT_LIST_EMPTY|[A-Z][A-Z0-9_]*(?: (?:host|file)=\S+)?)$"
)


def _is_collection_line(message: object) -> bool:
    """回收诊断行，用于完成后清掉过期提示、失败重试时去重。"""
    return isinstance(message, str) and bool(_COLLECTION_LINE.fullmatch(message))


def _collection_messages(outputs: list[dict[str, Any]]) -> list[str]:
    """回收未完成时的任务消息。只带错误码和主机名或文件名，不带查询串或完整 URL。"""
    items = outputs if isinstance(outputs, list) else []
    registered = sum(1 for item in items if isinstance(item, dict) and item.get("registered"))
    messages = [f"OUTPUT_COLLECTION_INCOMPLETE: {registered}/{len(items)} registered"]
    if not items:
        messages.append("OUTPUT_LIST_EMPTY")
        return messages
    seen: set[str] = set()
    for item in items:
        if not isinstance(item, dict) or item.get("registered"):
            continue
        code = str(item.get("code") or "OUTPUT_COLLECTION_FAILED")
        host = item.get("host")
        filename = item.get("filename")
        if isinstance(host, str) and host and "?" not in host and "/" not in host:
            line = f"{code} host={host}"
        elif isinstance(filename, str) and filename and "?" not in filename:
            line = f"{code} file={filename}"
        else:
            line = code
        if line in seen:
            continue
        seen.add(line)
        messages.append(line)
        if len(messages) >= 10:
            break
    return messages


def _execution_error_lines(summary_messages: list[Any]) -> list[str]:
    """从 ComfyUI status.messages 摘出 execution_error 摘要行：白名单字段、限长、去重。"""
    lines: list[str] = []
    for message in summary_messages:
        if not (isinstance(message, (list, tuple)) and len(message) == 2
                and message[0] == "execution_error" and isinstance(message[1], dict)):
            continue
        data = message[1]
        parts = ["execution_error"]
        for key in ("node_type", "node_id", "exception_type", "exception_message"):
            value = data.get(key)
            if isinstance(value, str) and value.strip():
                parts.append(f"{key}={' '.join(value.split())[:200]}")
        line = " ".join(parts)
        if line != "execution_error" and line not in lines:
            lines.append(line)
        if len(lines) >= 10:
            break
    return lines


async def _collect_task_outputs(context: AuthContext, task_id: str,
                                outputs: list[dict[str, Any]], *, task=None) -> list[dict[str, Any]]:
    """把 ComfyUI 输出文件落到运行时数据根并登记为素材。

    单个文件取回失败只跳过该条目并记录诊断，不把整个任务伪装成成功。
    """
    collected: list[dict[str, Any]] = []
    if len(outputs) > 32:
        return [{"registered": False, "code": "OUTPUT_COUNT_EXCEEDED"}]
    for item in outputs:
        filename = str(item.get("filename") or "")
        if not filename:
            continue
        try:
            payload, media_type = await _comfy_call(context, comfy_executor.fetch_output,
                filename, subfolder=str(item.get("subfolder") or ""),
                folder_type=str(item.get("type") or "output"), task=task)
        except CleanroomException as exc:
            collected.append({"filename": filename, "registered": False, "code": exc.code})
            continue
        try:
            asset = registry.store_bytes(context, payload, filename, media_type, task_id=task_id)
            registered = asset_hub_bridge.register_output_to_asset_hub(asset, context=context)
            if registered.get("error"):
                collected.append({**asset, "registered": False, "code": registered["error"]})
                continue
        except CleanroomException as exc:
            collected.append({"filename": filename, "registered": False, "code": exc.code})
            continue
        except OSError:
            collected.append({"filename": filename, "registered": False, "code": "OUTPUT_REGISTRATION_FAILED"})
            continue
        collected.append({"filename": filename, "registered": True, **asset})
    return collected


#: Clash fake-ip 保留网段（RFC 2544 基准测试）：非全局地址，仅白名单产物主机可借道。
_FAKE_IP_NETWORK = ipaddress.ip_network("198.18.0.0/15")


def _output_address_allowed(host: str, address: str, allowed_hosts: frozenset[str]) -> bool:
    """纯判定：单个解析地址是否可用于产物下载连接，不发起任何网络请求。

    全局公网地址直接放行；Clash fake-ip（198.18.0.0/15）不是全局地址，
    仅当主机在产物白名单内时放行；IPv4-mapped IPv6（::ffff:a.b.c.d）先还原为
    IPv4 再判定；6to4/NAT64 等其它内嵌形式不还原，非全局一律拒绝。
    回环/私网/链路本地/多播/保留/未指定地址及 IPv6 等价形式永不放行。
    """
    try:
        parsed = ipaddress.ip_address(address)
    except ValueError:
        return False
    if isinstance(parsed, ipaddress.IPv6Address) and parsed.ipv4_mapped is not None:
        parsed = parsed.ipv4_mapped
    # 多播（224.0.0.0/4、ff00::/8）在 Python 里 is_global 可为 True，必须先显式拒绝。
    if parsed.is_multicast:
        return False
    if parsed.is_global:
        return True
    if isinstance(parsed, ipaddress.IPv4Address) and parsed in _FAKE_IP_NETWORK:
        return host.lower().rstrip(".") in allowed_hosts
    # 其余非全局地址（回环/私网/链路本地/保留/未指定等）一律拒绝。
    return False


#: RunningHub 官方产物主机默认白名单：仅当 GW_WORKFLOW_OUTPUT_HOSTS 未配置（或空白）时
#: 生效；一旦显式配置该环境变量，即完全替换默认值，不追加。
RH_DEFAULT_OUTPUT_HOSTS = frozenset({"rh-images-tos.xiaoyaoyou.com", "rh-images.xiaoyaoyou.com"})

#: 产物下载手动跟随的重定向步数上限（1 次初始请求 + 3 次 30x）。
_OUTPUT_MAX_REDIRECTS = 3

#: 重定向上需要重新校验并重新钉定的跳转状态码。
_OUTPUT_REDIRECT_CODES = frozenset({301, 302, 303, 307, 308})


def _output_allowed_hosts(platform: str) -> frozenset[str]:
    """按平台解析产物主机白名单：环境变量显式配置时完全覆盖，否则仅 RunningHub 有内置默认。"""
    configured = frozenset(host.strip().lower() for host in os.getenv("GW_WORKFLOW_OUTPUT_HOSTS", "").split(",") if host.strip())
    if configured:
        return configured
    if platform in ("runninghub", "rh_cloud"):
        return RH_DEFAULT_OUTPUT_HOSTS
    return frozenset()


async def _getaddrinfo(host: str, port: int) -> list[tuple]:
    """产物下载专用 DNS 解析入口；独立模块级函数便于测试按跳替换与计数。"""
    return await asyncio.get_running_loop().getaddrinfo(host, port, type=socket.SOCK_STREAM)


async def _resolve_pinned(host: str, port: int, allowed_hosts: frozenset[str]) -> str:
    """解析一次、全部结果校验、钉住首个获准地址，阻止 DNS 重绑定与部分记录污染。"""
    infos = await _getaddrinfo(host, port)
    addresses = [info[4][0] for info in infos]
    if not addresses or any(not _output_address_allowed(host, address, allowed_hosts) for address in addresses):
        raise CleanroomException(400, "OUTPUT_ADDRESS_NOT_ALLOWED", "输出地址不是获准公网地址")
    return addresses[0]


def _output_transport(allowed_hosts: frozenset[str] | None = None, verify: bool = True) -> httpx.AsyncHTTPTransport:
    """产物下载传输层：默认开启证书校验，不读取代理环境。

    连接目标 IP 由 _resolve_pinned 在发请求前钉定并写入 URL，本函数不再改写连接层；
    allowed_hosts 参数仅为兼容测试替身签名而保留。
    """
    return httpx.AsyncHTTPTransport(retries=0, trust_env=False, verify=verify)


def _output_url_allowed(parts, allowed_hosts: frozenset[str]) -> bool:
    """URL 层校验：仅接受获准主机的 HTTPS 默认端口或 443，禁止 userinfo 与 fragment。"""
    return (parts.scheme == "https" and parts.hostname in allowed_hosts and not parts.username
            and not parts.password and parts.port in (None, 443) and not parts.fragment)


def _pinned_target(url: str, pinned_ip: str) -> str:
    """把已获准 URL 重写为指向钉住地址的请求 URL（IPv6 加方括号），路径与查询串原样保留。"""
    parts = urlsplit(url)
    host = f"[{pinned_ip}]" if ":" in pinned_ip else pinned_ip
    return f"https://{host}{parts.path or '/'}" + (f"?{parts.query}" if parts.query else "")


async def _download_pinned_output(url: str, allowed_hosts: frozenset[str]) -> tuple[bytes, str]:
    """逐跳校验、解析并钉住地址后下载，手动跟随至多 3 次 30x 重定向。

    每一跳都重新执行完整 URL 校验并重新解析钉定，杜绝获准主机借 30x 引流到未获准
    地址；TLS SNI 与证书校验始终使用原始主机名（请求扩展 sni_hostname + Host 头），
    证书校验保持默认开启。
    """
    current = url
    async with httpx.AsyncClient(timeout=30.0, follow_redirects=False, trust_env=False,
                                  transport=_output_transport(allowed_hosts)) as client:
        for hop in range(_OUTPUT_MAX_REDIRECTS + 1):
            parts = urlsplit(current)
            if not _output_url_allowed(parts, allowed_hosts):
                raise CleanroomException(400, "OUTPUT_URL_NOT_ALLOWED", "输出来源未获准")
            hostname = parts.hostname or ""
            pinned_ip = await _resolve_pinned(hostname, parts.port or 443, allowed_hosts)
            async with client.stream("GET", _pinned_target(current, pinned_ip),
                                      headers={"Host": hostname},
                                      extensions={"sni_hostname": hostname}) as resp:
                if resp.status_code in _OUTPUT_REDIRECT_CODES:
                    location = resp.headers.get("location")
                    if hop >= _OUTPUT_MAX_REDIRECTS or not location:
                        raise CleanroomException(502, "DOWNLOAD_FAILED", "输出下载失败或发生重定向")
                    current = urljoin(current, location)
                    continue
                if resp.status_code != 200:
                    raise CleanroomException(502, "DOWNLOAD_FAILED", "输出下载失败或发生重定向")
                content_buffer = bytearray()
                async for chunk in resp.aiter_bytes():
                    content_buffer.extend(chunk)
                    if len(content_buffer) > registry.MAX_ASSET_BYTES:
                        raise CleanroomException(502, "OUTPUT_TOO_LARGE", "输出超过大小限制")
                return bytes(content_buffer), resp.headers.get("content-type") or "application/octet-stream"
    raise CleanroomException(502, "DOWNLOAD_FAILED", "输出下载失败或发生重定向")


async def _collect_rh_task_outputs(context: AuthContext, task_id: str,
                                   outputs: list[dict[str, Any]], *,
                                   platform: str = "rh_cloud") -> list[dict[str, Any]]:
    """把 RunningHub 云端输出文件下载落到运行时数据根并登记为素材。"""
    collected: list[dict[str, Any]] = []
    if len(outputs) > 32:
        return [{"registered": False, "code": "OUTPUT_COUNT_EXCEEDED"}]
    # 同一份白名单同时用于 URL 校验与解析钉定（fake-ip 借道只认它）。
    allowed_hosts = _output_allowed_hosts(platform)
    for item in outputs:
        if not isinstance(item, dict):
            collected.append({"registered": False, "code": "INVALID_OUTPUT_DESCRIPTOR"})
            continue
        url = str(item.get("fileUrl") or item.get("url") or "")
        filename = str(item.get("fileName") or item.get("filename") or "")
        if not url:
            collected.append({"registered": False, "code": "INVALID_OUTPUT_DESCRIPTOR"})
            continue
        if not filename:
            filename = url.split("?")[0].rsplit("/", 1)[-1] or f"rh_output_{len(collected)+1}.png"
        parts = None
        try:
            if not allowed_hosts:
                raise CleanroomException(400, "OUTPUT_HOSTS_NOT_CONFIGURED", "未配置产物主机白名单")
            parts = urlsplit(url)
            if not _output_url_allowed(parts, allowed_hosts):
                raise CleanroomException(400, "OUTPUT_URL_NOT_ALLOWED", "输出来源未获准")
            content, media_type = await _download_pinned_output(url, allowed_hosts)
        except CleanroomException as exc:
            failed = {"filename": filename, "registered": False, "code": exc.code}
            hostname = parts.hostname if parts is not None else None
            if exc.code in {"OUTPUT_URL_NOT_ALLOWED", "OUTPUT_HOSTS_NOT_CONFIGURED"} and isinstance(hostname, str) and hostname:
                failed["host"] = hostname
            collected.append(failed)
            continue
        except Exception:
            collected.append({"filename": filename, "registered": False, "code": "DOWNLOAD_ERROR"})
            continue
        try:
            asset = registry.store_bytes(context, content, filename, media_type, task_id=task_id)
            registered = asset_hub_bridge.register_output_to_asset_hub(asset, context=context)
            if registered.get("error"):
                collected.append({**asset, "registered": False, "code": registered["error"]})
                continue
        except Exception:
            collected.append({"filename": filename, "registered": False, "code": "OUTPUT_REGISTRATION_FAILED"})
            continue
        collected.append({"filename": filename, "registered": True, **asset})
    return collected



@router.get("/canvas/contract")
def canvas_contract(authorization: Optional[str] = Header(None), x_user_role: str = Header("editor", alias="X-User-Role")):
    _auth(authorization, x_user_role, False)
    return {"contract": {"version": 1, "workflow": "normalized WorkflowDocument", "export_formats": ["json"]}, "data_status": "ok", "data_gaps": []}


@router.post("/canvas/export")
async def canvas_export(request: Request, authorization: Optional[str] = Header(None), x_user_role: str = Header("editor", alias="X-User-Role")):
    _auth(authorization, x_user_role, True)
    body = await _json_body(request)
    payload = body.get("workflow", body) if isinstance(body, dict) else body
    document = _parse_payload(payload, "canvas", body.get("name", "workflow") if isinstance(body, dict) else "workflow")
    exported = document.as_dict()
    return {"format": "json", "workflow": exported, "data_status": "ok", "data_gaps": []}


def _folder_has_document(context: AuthContext, folder_id: str) -> bool:
    return any(document.get("folder_id") == folder_id for _, document in _stored_document_items(context))


@router.get("/folders")
def list_folders(authorization: Optional[str] = Header(None), x_user_role: str = Header("editor", alias="X-User-Role")):
    context = _auth(authorization, x_user_role, False)
    with _STORAGE_LOCK:
        items = [_load(path) for path in sorted(_folder_root(context).glob("*.json"))]
    return {"folders": items, "data_status": "ok", "data_gaps": []}


@router.post("/folders", status_code=status.HTTP_201_CREATED)
async def create_folder(request: Request, authorization: Optional[str] = Header(None), x_user_role: str = Header("editor", alias="X-User-Role")):
    context = _auth(authorization, x_user_role, True)
    body = await _json_body(request)
    if not isinstance(body, dict):
        raise CleanroomException(400, "INVALID_REQUEST", "请求体必须是 JSON 对象")
    parent_id = body.get("parent_id")
    with _STORAGE_LOCK:
        if parent_id is not None:
            _load_folder(context, str(parent_id))
        folder_id = str(uuid.uuid4())
        folder = {"folder_id": folder_id, "name": str(body.get("name", "新目录")).strip() or "新目录",
                  "parent_id": str(parent_id) if parent_id is not None else None, "revision": 1}
        _atomic_write(_folder_path(context, folder_id), folder)
    return {"folder": folder, "data_status": "ok", "data_gaps": []}


@router.get("/folders/tree")
def folder_tree(authorization: Optional[str] = Header(None), x_user_role: str = Header("editor", alias="X-User-Role")):
    context = _auth(authorization, x_user_role, False)
    with _STORAGE_LOCK:
        tree = _folder_tree(context)
    return {"tree": tree, "data_status": "ok", "data_gaps": []}


@router.get("/folders/{folder_id}")
def get_folder(folder_id: str, authorization: Optional[str] = Header(None), x_user_role: str = Header("editor", alias="X-User-Role")):
    context = _auth(authorization, x_user_role, False)
    with _STORAGE_LOCK:
        folder = _load_folder(context, folder_id)
    return {"folder": folder, "data_status": "ok", "data_gaps": []}


async def _apply_folder_update(folder_id: str, request: Request, authorization: Optional[str], x_user_role: str):
    """Rename/move a folder under revision CAS; shared by PATCH and PUT."""
    context = _auth(authorization, x_user_role, True)
    body = await _json_body(request)
    if not isinstance(body, dict):
        raise CleanroomException(400, "INVALID_REQUEST", "请求体必须是 JSON 对象")
    with _STORAGE_LOCK:
        folder = _load_folder(context, folder_id)
        _check_folder_revision(folder, _folder_revision(body, request))
        if "name" in body:
            folder["name"] = str(body["name"]).strip() or folder["name"]
        if "parent_id" in body:
            target = body.get("parent_id")
            if target is not None:
                target = str(target)
                if target == folder_id:
                    raise CleanroomException(409, "FOLDER_CYCLE", "目录不能移动到自身")
                _load_folder(context, target)
                cursor = target
                while cursor is not None:
                    if cursor == folder_id:
                        raise CleanroomException(409, "FOLDER_CYCLE", "目录不能移动到其子目录")
                    cursor = _load_folder(context, cursor).get("parent_id")
            folder["parent_id"] = target
        folder["revision"] = int(folder.get("revision", 1)) + 1
        _atomic_write(_folder_path(context, folder_id), folder)
    return {"folder": folder, "data_status": "ok", "data_gaps": []}


@router.patch("/folders/{folder_id}")
async def patch_folder(folder_id: str, request: Request, authorization: Optional[str] = Header(None), x_user_role: str = Header("editor", alias="X-User-Role")):
    return await _apply_folder_update(folder_id, request, authorization, x_user_role)


@router.put("/folders/{folder_id}")
async def put_folder(folder_id: str, request: Request, authorization: Optional[str] = Header(None), x_user_role: str = Header("editor", alias="X-User-Role")):
    """PUT shares PATCH semantics, including the revision CAS."""
    return await _apply_folder_update(folder_id, request, authorization, x_user_role)


@router.delete("/folders/{folder_id}")
async def delete_folder(folder_id: str, request: Request, authorization: Optional[str] = Header(None), x_user_role: str = Header("editor", alias="X-User-Role")):
    context = _auth(authorization, x_user_role, True)
    body = await _json_body(request)
    with _STORAGE_LOCK:
        folder = _load_folder(context, folder_id)
        _check_folder_revision(folder, _folder_revision(body, request))
        child_exists = any(item.get("parent_id") == folder_id for item in (_load(path) for path in _folder_root(context).glob("*.json")))
        document_exists = _folder_has_document(context, folder_id)
        if child_exists or document_exists:
            raise CleanroomException(409, "FOLDER_NOT_EMPTY", "目录不为空")
        _folder_path(context, folder_id).unlink()
    return {"folder_id": folder_id, "deleted": True, "data_status": "ok", "data_gaps": []}


@router.post("/folders/{folder_id}/move")
async def move_folder(folder_id: str, request: Request, authorization: Optional[str] = Header(None), x_user_role: str = Header("editor", alias="X-User-Role")):
    context = _auth(authorization, x_user_role, True)
    body = await _json_body(request)
    if not isinstance(body, dict):
        raise CleanroomException(400, "INVALID_REQUEST", "请求体必须是 JSON 对象")
    target = body.get("parent_id")
    with _STORAGE_LOCK:
        folder = _load_folder(context, folder_id)
        _check_folder_revision(folder, _folder_revision(body, request))
        if target is not None:
            target = str(target)
            if target == folder_id:
                raise CleanroomException(409, "FOLDER_CYCLE", "目录不能移动到自身")
            _load_folder(context, target)
            cursor = target
            while cursor is not None:
                if cursor == folder_id:
                    raise CleanroomException(409, "FOLDER_CYCLE", "目录不能移动到其子目录")
                cursor = _load_folder(context, cursor).get("parent_id")
        folder["parent_id"] = target
        folder["revision"] = int(folder.get("revision", 1)) + 1
        _atomic_write(_folder_path(context, folder_id), folder)
    return {"folder": folder, "data_status": "ok", "data_gaps": []}


@router.post("/documents", status_code=status.HTTP_201_CREATED)
async def save_document(request: Request, authorization: Optional[str] = Header(None), x_user_role: str = Header("editor", alias="X-User-Role")):
    context = _auth(authorization, x_user_role, True)
    name = request.query_params.get("name", "workflow")
    payload = await _json_body(request)
    document = _parse_payload(payload, request.query_params.get("source", "comfyui"), name)
    workflow_id = str(uuid.uuid4())
    stored = document.as_dict()
    folder_id = payload.get("folder_id") if isinstance(payload, dict) else None
    if folder_id is not None:
        with _STORAGE_LOCK:
            _load_folder(context, str(folder_id))
    stored.update({"workflow_id": workflow_id, "version": 1, "folder_id": str(folder_id) if folder_id is not None else None})
    stored["asset_id"] = asset_hub_bridge._workflow_asset_id(context, workflow_id)
    stored.setdefault("metadata", {})["source_context"] = _source_context(context, stored.get("metadata", {}).get("source_context", {}))
    with _STORAGE_LOCK:
        _atomic_write(_path(context, workflow_id), stored)
    registration = asset_hub_bridge.register_workflow_to_asset_hub(workflow_id, name, document=stored, context=context)
    # 仅允许写入当前主体目录；不要生成未隔离的公共副本。
    return {"name": name, "workflow_id": workflow_id, "version": 1, "workflow": stored,
            "document": _summary(stored, workflow_id)["document"], **asset_hub_bridge.registration_health(registration)}


@router.get("/documents")
def list_documents(authorization: Optional[str] = Header(None), x_user_role: str = Header("editor", alias="X-User-Role")):
    context = _auth(authorization, x_user_role, False)
    documents = []
    with _STORAGE_LOCK:
        for path, item in _stored_document_items(context):
            documents.append(_summary(item, path.stem))
    # ``documents`` remains the old list-of-names field; summaries are additive.
    return {"documents": [item["name"] for item in documents], "document_summaries": documents,
            "data_status": "ok", "data_gaps": []}


@router.get("/documents/{workflow_id}")
def get_document(workflow_id: str, authorization: Optional[str] = Header(None), x_user_role: str = Header("editor", alias="X-User-Role")):
    context = _auth(authorization, x_user_role, False)
    with _STORAGE_LOCK:
        _path_for_read, item = _resolve_workflow(context, workflow_id, write=False)
    wf_data = dict(item)
    wf_data["actuator_params"] = _promoted_params(wf_data)
    return {"workflow_id": workflow_id, "workflow": wf_data, "document": _summary(item, workflow_id)["document"],
            **_reparse_flags(item),
            "data_status": "ok", "data_gaps": []}


async def _update_document(workflow_id: str, request: Request, authorization: Optional[str], x_user_role: str,
                           *, native_sync: bool = False, schemas: Optional[dict] = None, host_prompt=None,
                           host_fields=None, allow_stale_source_repair: bool = False):
    context = _workflow_write_auth(authorization, x_user_role)
    body = await _json_body(request)
    if not isinstance(body, dict):
        raise CleanroomException(400, "INVALID_REQUEST", "请求体必须是 JSON 对象")
    # 权限门禁必须先于 expected_version，避免隐藏资源泄露版本信息。
    readonly_source_repair = False
    try:
        path, _ = _resolve_workflow(context, workflow_id, write=True)
    except CleanroomException as exc:
        if not (allow_stale_source_repair and exc.status_code == 403 and exc.code == "PROJECT_READ_ONLY"):
            raise
        path, _ = _resolve_workflow(context, workflow_id, write=False)
        readonly_source_repair = True
    with _STORAGE_LOCK:
        current = _load(path)
        expected = body.pop("expected_version", request.query_params.get("expected_version"))
        expected = _expected_version(expected)
        current_version = int(current.get("version", 1))
        if expected != current_version:
            raise VersionConflictException(expected, current_version)
        # 元数据操作只修改目标文档，绝不使用当前打开的另一份图替换它。
        payload = body.get("payload", body.get("workflow", current))
        name = body.get("name", current.get("name", "workflow"))
        document = _parse_payload(payload, body.get("source", current.get("source", "comfyui")), name)
        stored = {**current, **document.as_dict()}
        cached_prompt = current.get('metadata', {}).get('resolved_api')
        if isinstance(cached_prompt, dict) and cached_prompt.get('fingerprint') == execution_fingerprint(stored):
            stored.setdefault('metadata', {})['resolved_api'] = deepcopy(cached_prompt)
        if native_sync:
            # 来源由服务端当前 CAS 版本保留，不能被宿主图中随带的元数据替换。
            stored.setdefault("metadata", {})["source_context"] = deepcopy(current.get("metadata", {}).get("source_context", {}))
            stored["metadata"]["node_schemas"] = deepcopy(schemas or {})
            _preserve_native_bindings(current, stored, schemas or {})
            if host_prompt is not None:
                stored['metadata']['resolved_api'] = {'source': 'comfyui_graph_to_prompt', 'prompt': deepcopy(host_prompt),
                    'field_maps': deepcopy(host_fields or {}), 'fingerprint': execution_fingerprint(stored)}
                stored['metadata']['needs_host_resolution'] = False
        stored.update({"workflow_id": workflow_id, "version": current_version + 1,
                       "folder_id": body.get("folder_id", current.get("folder_id"))})
        stored["asset_id"] = current.get("asset_id") or asset_hub_bridge._workflow_asset_id(context, workflow_id)
        explicit_source = "source_context" in body
        raw_source = body.get("source_context") if explicit_source else stored.get("metadata", {}).get("source_context", {})
        try:
            source_context = {} if readonly_source_repair and not explicit_source else _source_context(context, raw_source)
        except CleanroomException as exc:
            # 显式来源无法访问仍按原错误拒绝；继承自文档元数据的旧来源只是陈旧数据，
            # 死链只做自愈清理不阻断参数保存等常规写入；execute 的显式来源 ACL 不变。
            if explicit_source or exc.status_code != 404 or exc.code not in ("PROJECT_NOT_FOUND", "CANVAS_NOT_FOUND"):
                raise
            if exc.code == "CANVAS_NOT_FOUND":
                try:  # 画布已失效但项目仍可访问时保留项目级来源
                    source_context = _source_context(context, {"project_id": raw_source["project_id"]})
                except CleanroomException:
                    source_context = {}
            else:
                source_context = {}
        stored.setdefault("metadata", {})["source_context"] = source_context
        if stored["folder_id"] not in (None, "parsed_default"):
            _load_folder(context, str(stored["folder_id"]))
        _atomic_write(path, stored)
    registration = asset_hub_bridge.register_workflow_to_asset_hub(workflow_id, name, document=stored, context=context)
    return {"workflow_id": workflow_id, "version": current_version + 1, "workflow": stored,
            "document": _summary(stored, workflow_id)["document"], **asset_hub_bridge.registration_health(registration)}


@router.put("/documents/{workflow_id}")
@router.put("/item/{workflow_id}")
async def update_document(workflow_id: str, request: Request, authorization: Optional[str] = Header(None), x_user_role: str = Header("editor", alias="X-User-Role")):
    return await _update_document(workflow_id, request, authorization, x_user_role)


@router.patch("/documents/{workflow_id}")
async def patch_document(workflow_id: str, request: Request, authorization: Optional[str] = Header(None), x_user_role: str = Header("editor", alias="X-User-Role")):
    return await _update_document(workflow_id, request, authorization, x_user_role)


@router.delete("/documents/{workflow_id}")
@router.delete("/item/{workflow_id}")
async def delete_document(workflow_id: str, request: Request, authorization: Optional[str] = Header(None), x_user_role: str = Header("editor", alias="X-User-Role")):
    context = _workflow_write_auth(authorization, x_user_role)
    body = await _json_body(request)
    if not isinstance(body, dict):
        raise CleanroomException(400, "INVALID_REQUEST", "请求体必须是 JSON 对象")
    path, _ = _resolve_workflow(context, workflow_id, write=True)
    with _STORAGE_LOCK:
        current = _load(path)
        expected = body.get("expected_version", request.query_params.get("expected_version"))
        expected = _expected_version(expected)
        current_version = int(current.get("version", 1))
        if expected != current_version:
            raise VersionConflictException(expected, current_version)
        removed = asset_hub_bridge.remove_workflow_from_asset_hub(context, workflow_id)
        if removed.get("error"):
            raise CleanroomException(503, removed["error"], "资产投影清理未完成，文档保留，可重试删除")
        path.unlink()
        shutil.rmtree(_workflow_versions_root(context, workflow_id, document_path=path), ignore_errors=True)
    return {"workflow_id": workflow_id, "deleted": True, "data_status": "ok", "data_gaps": []}


@router.post("/documents/{workflow_id}/rename")
async def rename_document(workflow_id: str, request: Request, authorization: Optional[str] = Header(None), x_user_role: str = Header("editor", alias="X-User-Role")):
    context = _workflow_write_auth(authorization, x_user_role)
    body = await _json_body(request)
    if not isinstance(body, dict):
        raise CleanroomException(400, "INVALID_REQUEST", "请求体必须是 JSON 对象")
    path, _ = _resolve_workflow(context, workflow_id, write=True)
    with _STORAGE_LOCK:
        current = _load(path)
        expected = body.get("expected_version", request.query_params.get("expected_version"))
        expected = _expected_version(expected)
        current_version = int(current.get("version", 1))
        if expected != current_version:
            raise VersionConflictException(expected, current_version)
        current["name"] = str(body.get("name", "")).strip() or current.get("name", "workflow")
        current["version"] = current_version + 1
        _atomic_write(path, current)
    registration = asset_hub_bridge.register_workflow_to_asset_hub(workflow_id, current["name"], document=current, context=context)
    return {"workflow_id": workflow_id, "version": current["version"], "workflow": current,
            "document": _summary(current, workflow_id)["document"], **asset_hub_bridge.registration_health(registration)}


def _workflow_versions_root(context: AuthContext, workflow_id: str, *, document_path: Optional[Path] = None) -> Path:
    """历史版本目录：与文档同主体隔离；子目录不参与文档枚举（仅遍历顶层 *.json）。"""
    base = document_path.parent if document_path is not None else _principal_root(context)
    return base / "workflow_versions" / _workflow_filename(workflow_id).removesuffix(".json")


def _merge_user_node_data(nodes: Any, previous_nodes: Any) -> None:
    """把旧文档的用户态数据按 (id, kind) 合并回重解析出的新节点。

    重解析只修复结构（命名槽位连线、输入集合、控件结构保持新结果权威），
    用户在旧文档上的编辑原样回填：画布位置、标量输入值、控件 value/promoted，
    以及 input_assets / widget_promotions 元数据。
    """
    if not isinstance(nodes, list) or not isinstance(previous_nodes, list):
        return
    previous: dict[tuple[str, str], dict[str, Any]] = {}
    for old in previous_nodes:
        if isinstance(old, dict):
            previous.setdefault((str(old.get("id")), str(old.get("kind"))), old)
    for node in nodes:
        if not isinstance(node, dict):
            continue
        old = previous.get((str(node.get("id")), str(node.get("kind"))))
        if old is None:
            continue
        old_position = old.get("position")
        if isinstance(old_position, dict):
            node["position"] = deepcopy(old_position)
        new_inputs, old_inputs = node.get("inputs"), old.get("inputs")
        if isinstance(new_inputs, dict) and isinstance(old_inputs, dict):
            for name, value in old_inputs.items():
                # 连线引用与解析结果覆盖不了的新输入名都保持新结构，只回填标量。
                if _api_link(value) is not None or name not in new_inputs:
                    continue
                if _api_link(new_inputs[name]) is None:
                    new_inputs[name] = deepcopy(value)
        old_widgets = old.get("widgets")
        if isinstance(old_widgets, list):
            if not isinstance(node.get("widgets"), list):
                node["widgets"] = deepcopy(old_widgets)
            else:
                named = {widget.get("name"): widget for widget in old_widgets
                         if isinstance(widget, dict) and isinstance(widget.get("name"), str)}
                for widget in node["widgets"]:
                    old_widget = named.get(widget.get("name")) if isinstance(widget, dict) else None
                    if old_widget is None:
                        continue
                    if "value" in old_widget:
                        widget["value"] = deepcopy(old_widget["value"])
                    if "promoted" in old_widget:
                        widget["promoted"] = bool(old_widget["promoted"])
        old_metadata = old.get("metadata")
        if isinstance(old_metadata, dict):
            new_metadata = node.get("metadata")
            if not isinstance(new_metadata, dict):
                new_metadata = {}
                node["metadata"] = new_metadata
            for key in ("input_assets", "widget_promotions"):
                if key in old_metadata:
                    new_metadata[key] = deepcopy(old_metadata[key])


def _positions_all_at_origin(nodes: Any) -> bool:
    """所有节点都缺位置或停在 (0,0) 时需要整体重排。"""
    if not isinstance(nodes, list) or not nodes:
        return False
    for node in nodes:
        position = node.get("position") if isinstance(node, dict) else None
        if isinstance(position, dict) and (position.get("x") or position.get("y")):
            return False
    return True


def _assign_layout_positions(nodes: list[Any], connections: Any) -> None:
    """按分层布局落位，沿用文档其余位置的 {x, y} 字典格式。"""
    coordinates = compute_layout(nodes, connections if isinstance(connections, list) else [])
    for node in nodes:
        coord = coordinates.get(str(node.get("id")))
        if coord is None:
            continue
        position = dict(node["position"]) if isinstance(node.get("position"), dict) else {}
        position["x"], position["y"] = coord[0], coord[1]
        node["position"] = position


@router.post("/workflows/{workflow_id}/reparse", status_code=status.HTTP_200_OK)
@router.post("/documents/{workflow_id}/reparse", status_code=status.HTTP_200_OK)
async def reparse_document(workflow_id: str, request: Request, authorization: Optional[str] = Header(None), x_user_role: str = Header("editor", alias="X-User-Role")):
    """从已保存的原始来源（raw_payload）离线重解析并写入新版本。

    只读本地已保存数据，绝不访问网络；``expected_version`` 过期返回 409，
    无法重新解析时返回明确错误码且不修改任何数据。原版本先落入历史目录
    再写新版本，因此中断后原数据仍完整可读回。
    """
    context = _workflow_write_auth(authorization, x_user_role)
    body = await _json_body(request, allow_empty=True)
    body = body if isinstance(body, dict) else {}
    expected = _expected_version(body.get("expected_version", request.query_params.get("expected_version")))
    path, _ = _resolve_workflow(context, workflow_id, write=True)
    with _STORAGE_LOCK:
        current = _load(path)
        current_version = int(current.get("version", 1))
        if expected != current_version:
            raise VersionConflictException(expected, current_version)
        needs, _reason = legacy_reparse_state(current)
        if not needs:
            return {"workflow_id": workflow_id, "version": current_version, "workflow": current,
                    "document": _summary(current, workflow_id)["document"],
                    "reparse_performed": False, "actuator_params": _promoted_params(current),
                    **_reparse_flags(current),
                    "data_status": "ok", "data_gaps": []}
        graph = extract_source_graph(current.get("raw_payload"))
        if graph is None:
            raise CleanroomException(422, "REPARSE_SOURCE_UNAVAILABLE",
                                     "保存的原始来源缺少可重新解析的源图，未做任何修改") from None
        try:
            parsed = _parse_payload(deepcopy(graph), current.get("source") or "comfyui", current.get("name") or "workflow")
        except CleanroomException as exc:
            raise CleanroomException(422, "REPARSE_SOURCE_UNAVAILABLE",
                                     f"原始来源无法重新解析（{exc.code}），未做任何修改") from None
        parsed_dict = parsed.as_dict()
        stored = {**current, **parsed_dict}
        # 解析结果覆盖不了的既有元数据（来源上下文、宿主 Schema 等）原样保留；
        # 过期的宿主编译缓存指纹不再成立，必须丢弃。
        stored["metadata"] = {**(current.get("metadata") or {}), **(parsed_dict.get("metadata") or {})}
        stored["metadata"].pop("resolved_api", None)
        # 重解析只修复结构，用户在旧文档上的编辑（位置、标量参数、控件、资产/提升）
        # 按 (id, kind) 合并回来；整体没有有效位置时再按拓扑布局落位。
        _merge_user_node_data(stored.get("nodes"), current.get("nodes"))
        if _positions_all_at_origin(stored.get("nodes")):
            _assign_layout_positions(stored["nodes"], stored.get("connections"))
        stored.update({"workflow_id": workflow_id, "version": current_version + 1,
                       "asset_id": current.get("asset_id") or asset_hub_bridge._workflow_asset_id(context, workflow_id)})
        _atomic_write(_workflow_versions_root(context, workflow_id, document_path=path) / f"{current_version}.json", deepcopy(current))
        _atomic_write(path, stored)
    registration = asset_hub_bridge.register_workflow_to_asset_hub(workflow_id, stored.get("name", "workflow"), document=stored, context=context)
    return {"workflow_id": workflow_id, "version": stored["version"], "workflow": stored,
            "document": _summary(stored, workflow_id)["document"], "reparse_performed": True,
            "actuator_params": _promoted_params(stored),
            **_reparse_flags(stored), **asset_hub_bridge.registration_health(registration)}


@router.get("/workflows/{workflow_id}/versions")
def list_workflow_versions(workflow_id: str, authorization: Optional[str] = Header(None), x_user_role: str = Header("editor", alias="X-User-Role")):
    """列出经 reparse 保留的历史版本（新版本在前）。"""
    context = _auth(authorization, x_user_role, False)
    document_path, _ = _resolve_workflow(context, workflow_id, write=False)
    versions_root = _workflow_versions_root(context, workflow_id, document_path=document_path)
    # 与 _load 的缺失文档语义一致：主文档与历史目录都不存在就是未找到，而非空列表。
    if not document_path.is_file() and not any(versions_root.glob("*.json")):
        raise CleanroomException(404, "WORKFLOW_NOT_FOUND", "工作流不存在")
    versions = []
    for path in versions_root.glob("*.json"):
        if path.stem.isascii() and path.stem.isdecimal():
            archived_at = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat()
            versions.append({"version": int(path.stem), "archived_at": archived_at})
    versions.sort(key=lambda item: item["version"], reverse=True)
    return {"workflow_id": workflow_id, "versions": versions, "data_status": "ok", "data_gaps": []}


@router.get("/workflows/{workflow_id}/versions/{version}")
def get_workflow_version(workflow_id: str, version: str, authorization: Optional[str] = Header(None), x_user_role: str = Header("editor", alias="X-User-Role")):
    """读回指定历史版本的完整原文档。"""
    context = _auth(authorization, x_user_role, False)
    parsed_version = _expected_version(version)
    document_path, _ = _resolve_workflow(context, workflow_id, write=False)
    path = _workflow_versions_root(context, workflow_id, document_path=document_path) / f"{parsed_version}.json"
    if not path.is_file():
        raise CleanroomException(404, "WORKFLOW_VERSION_NOT_FOUND", "该历史版本不存在") from None
    document = _load(path)
    return {"workflow_id": workflow_id, "version": parsed_version, "workflow": document,
            **_reparse_flags(document), "data_status": "ok", "data_gaps": []}


@router.get("/diagnostics")
def diagnostics(authorization: Optional[str] = Header(None), x_user_role: str = Header("editor", alias="X-User-Role")):
    _auth(authorization, x_user_role, False)
    providers = {provider: {"configured": get_provider(provider).configured,
                           "status": "available" if get_provider(provider).configured else "unavailable"}
                 for provider in PROVIDERS}
    return {"data_status": "partial", "diagnostics": {"providers": providers,
            "executor": {"status": "unavailable", "code": "WORKFLOW_EXECUTOR_UNAVAILABLE"},
            "storage": {"status": "available"}}, "data_gaps": ["executor not configured"]}


@router.post("/asset-hub/reconcile")
def reconcile_asset_hub(authorization: Optional[str] = Header(None), x_user_role: str = Header("editor", alias="X-User-Role")):
    context = _workflow_write_auth(authorization, x_user_role)
    _authorize_workflow_bridge(context)
    return asset_hub_bridge.reconcile_pending(context)


@router.get("/assets")
def workflow_assets(authorization: Optional[str] = Header(None), x_user_role: str = Header("editor", alias="X-User-Role")):
    return workflow_assets_compat(authorization, x_user_role)


@router.get("/assets/list")
def workflow_assets_compat(authorization: Optional[str] = Header(None), x_user_role: str = Header("editor", alias="X-User-Role")):
    context = _auth(authorization, x_user_role, False)
    items = registry.list_assets(context)
    return {"items": items, "data_status": "ok" if items else "empty", "data_gaps": []}


@router.post("/assets/upload", status_code=status.HTTP_201_CREATED)
async def workflow_asset_upload(request: Request, authorization: Optional[str] = Header(None), x_user_role: str = Header("editor", alias="X-User-Role")):
    context = _auth(authorization, x_user_role, True)
    asset = await registry.store_upload(context, request)
    registration = asset_hub_bridge.register_output_to_asset_hub(asset, context=context)
    return {**asset, **asset_hub_bridge.registration_health(registration)}


@router.get("/assets/{asset_id}/content")
def workflow_asset_content(asset_id: str, authorization: Optional[str] = Header(None), x_user_role: str = Header("editor", alias="X-User-Role")):
    context = _auth(authorization, x_user_role, False)
    try:
        parsed = uuid.UUID(asset_id)
    except (ValueError, AttributeError):
        raise CleanroomException(404, "ASSET_NOT_FOUND", "素材不存在") from None
    resolved = registry.resolve_asset_file(context, str(parsed))
    if resolved is None:
        raise CleanroomException(404, "ASSET_NOT_FOUND", "素材不存在")
    path, item = resolved
    media_type = str(item.get("content_type") or "application/octet-stream")
    if media_type not in _WORKFLOW_MEDIA_TYPES:
        media_type = "application/octet-stream"
    return FileResponse(path, media_type=media_type,
                        filename=item.get("filename") if item.get("media_type") == "document" else None,
                        headers={"Cache-Control": "private, no-store"})


def _is_rh_like(text: str) -> bool:
    candidate = (text or "").strip()
    if not candidate:
        return False
    if re.search(r"runninghub\.(cn|ai)", candidate, re.IGNORECASE):
        return True
    if re.search(r"liblib\.(art|tv|ai)", candidate, re.IGNORECASE):
        return True
    if candidate.startswith("{") and ('"nodes"' in candidate or '"class_type"' in candidate):
        return True
    return bool(re.fullmatch(r"\d{15,22}", candidate))


@router.get("/clipboard")
def workflow_clipboard(request: Request, authorization: Optional[str] = Header(None), x_user_role: str = Header("editor", alias="X-User-Role")):
    """Per-principal clipboard history.

    ``?text=`` records the caller's current clipboard snapshot so the server keeps
    a real recent list instead of a static empty one; a plain GET only reads.
    """
    context = _auth(authorization, x_user_role, False)
    latest_text = (request.query_params.get("text") or "").strip()
    if latest_text:
        registry.record_clipboard(context, [{"text": latest_text, "is_rh_link": _is_rh_like(latest_text)}])
    body = registry.read_clipboard(context)
    body.update({"data_status": "ok" if body["items"] else "empty", "data_gaps": []})
    return body


#: ComfyUI 地址配置入口的真实前端路径：workflow 页「RunningHub 凭证与本地
#: ComfyUI 环境设置」弹窗（web/pages/workflow.html 的 inputComfyUrl）。
COMFY_SETTINGS_PATH = "/static/pages/workflow.html"

_PENDING_COMFY_COMMAND: dict[str, dict[str, Any]] = {}
def _require_comfy_admin(context):
    if context.role != "admin":
        raise CleanroomException(403, "FORBIDDEN", "CF 启动配置及启停仅限管理员")


def _comfy_operation(context, action, token=None):
    from .comfy_control import service
    settings = registry.read_settings(context)
    url = str(settings.get("comfy_url") or os.getenv("GW_COMFYUI_URL") or "").strip()
    root = str(settings.get("comfy_root_dir") or os.getenv("GW_COMFYUI_ROOT_DIR") or "")
    principal = registry.principal_digest(context.identity_domain, context.subject)
    if not url:
        # 未配置地址时如实报告，不猜测本地默认端口，也不发起任何探测。
        return {"ok": False, "status": "unconfigured", "highlight": False,
                "managed": False, "can_stop": False, "code": "COMFY_HOST_NOT_CONFIGURED",
                "message": "本地 ComfyUI 服务地址未配置", "settings_path": COMFY_SETTINGS_PATH}
    return service.operate(action, url, root, principal, token)


@router.post("/comfy-control")
async def comfy_control(request: Request, authorization: Optional[str] = Header(None), x_user_role: str = Header("editor", alias="X-User-Role")):
    from starlette.concurrency import run_in_threadpool
    context = _auth(authorization, x_user_role, False)
    body = await _json_body(request)
    if not isinstance(body, dict):
        raise CleanroomException(400, "INVALID_REQUEST", "请求体必须是对象")
    action = str(body.get("action") or "status").lower()
    if action != "status":
        _require_comfy_admin(context)
    return await run_in_threadpool(_comfy_operation, context, action, body.get("confirmation_token"))


@router.get("/comfy-control")
def comfy_control_status(authorization: Optional[str] = Header(None), x_user_role: str = Header("editor", alias="X-User-Role")):
    return _comfy_operation(_auth(authorization, x_user_role, False), "status")


@router.post("/open-in-comfy")
@router.post("/open-in-comfy/{workflow_id}")
async def open_in_comfy(request: Request, workflow_id: Optional[str] = None, authorization: Optional[str] = Header(None), x_user_role: str = Header("editor", alias="X-User-Role")):
    """将工作流导入已在线的本地 ComfyUI；服务启停仅由全站 CF 控件负责。"""
    context = _auth(authorization, x_user_role, True)
    # 旧版页面适配的升级开关只认显式 true；查询串或 JSON 体均可携带，其余一律视为不升级。
    try:
        body = await _json_body(request, allow_empty=True)
    except CleanroomException:
        body = None
    adapter_upgrade = (isinstance(body, dict) and body.get("adapter_upgrade") is True) \
        or request.query_params.get("adapter_upgrade", "").lower() == "true"

    document = None
    if workflow_id:
        _workflow_path, document = _resolve_workflow(context, workflow_id, write=True)

    settings = registry.read_settings(context)
    comfy_url = _comfy_endpoint(context).base_url
    if not comfy_url:
        # 未配置不是服务不可用（503），而是需要操作者先补配置的前置条件（409）。
        raise CleanroomException(409, 'COMFY_HOST_NOT_CONFIGURED', '请先在设置中配置本地 ComfyUI 服务地址',
                                 {'settings_path': COMFY_SETTINGS_PATH}, {'settings_path'})
    comfy_root = settings.get("comfy_root_dir") or os.environ.get("GW_COMFYUI_ROOT_DIR") or ""

    # 与顶栏共用受控生命周期；普通用户可以打开在线服务但不能隐式启动。
    control = _comfy_operation(context, "status")
    if not control.get("highlight"):
        if control.get("status") == "starting":
            raise unavailable('COMFY_START_PENDING', 'ComfyUI 正在启动，请等待右上角 CF 确认运行后再导入')
        raise unavailable('COMFY_HOST_UNREACHABLE', 'ComfyUI 未在线，请先使用全站 CF 控件启动或连接服务')
    online = bool(control.get("highlight"))
    launched = bool(control.get("launched"))
    effective_root = comfy_root

    safe_name = ""
    safe_filename = ""
    saved_path_str = ""
    write_error = None
    native_graph = None
    workflow_format = 'native'
    if document:
        original_nodes = (document.get('raw_payload') or {}).get('nodes')
        if (isinstance(original_nodes, list) and all(isinstance(node, dict) and node.get('type')
                and isinstance(node.get('inputs', []), list) for node in original_nodes)):
            native_graph = export_to_comfy_litegraph(document)
        else:
            # API 图由宿主实际节点工厂恢复参数与端口；不在工作台猜位置或端口类型。
            native_graph = _api_prompt_payload(document)
            workflow_format = 'api'
    # 原生 JSON 直接交给项目页面适配；服务启动、API 编译均不在导入路径中。
    browser_adapter = None
    if document and workflow_id:
        if urlsplit(comfy_url).hostname not in {'127.0.0.1', 'localhost', '::1'}:
            raise CleanroomException(422, 'COMFY_ADAPTER_LOCAL_REQUIRED', '自动页面适配仅支持本机 ComfyUI，不向远端安装目录写入')
        with _STORAGE_LOCK:
            browser_adapter = browser_bridge.prepare(comfy_root, upgrade=adapter_upgrade)

    if document and workflow_id:
        principal = registry.principal_digest(context.identity_domain, context.subject)
        graph_text = json.dumps(native_graph, ensure_ascii=False, indent=2)
        graph_hash = hashlib.sha256(graph_text.encode()).hexdigest()[:16]
        safe_filename = f"GW_{principal}_{workflow_id}_{graph_hash}.json"

        if effective_root and Path(effective_root).exists():
            base_p = Path(effective_root)
            candidates = [
                base_p / "ComfyUI" / "user" / "default" / "workflows",
                base_p / "user" / "default" / "workflows",
                base_p / "ComfyUI" / "my_workflows",
                base_p / "my_workflows",
            ]
            wf_dir = next((p for p in candidates if p.exists()), candidates[1] if not (base_p / 'ComfyUI').exists() else candidates[0])
            try:
                target = (wf_dir / safe_filename).resolve()
                if not target.is_relative_to(base_p.resolve()) or not base_p.is_absolute():
                    raise OSError('外部工作流路径越界')
                target.parent.mkdir(parents=True, exist_ok=True)
                try:
                    with target.open('x', encoding='utf-8') as stream:
                        stream.write(graph_text)
                except FileExistsError:
                    if target.read_text(encoding='utf-8') != graph_text:
                        raise OSError('同名宿主文件已被修改，禁止覆盖') from None
                saved_path_str = str(target)
            except OSError:
                write_error = 'COMFY_WORKFLOW_WRITE_FAILED'

        global _PENDING_COMFY_COMMAND
        comfy_uuid = str(document.get("metadata", {}).get("comfy_uuid") or workflow_id)
        principal = registry.principal_digest(context.identity_domain, context.subject)
        _PENDING_COMFY_COMMAND[principal] = {
            "command": "open_workflow",
            "workflow_id": workflow_id,
            "workflow_file": safe_filename,
            "comfy_uuid": comfy_uuid,
            "timestamp": time.time(),
        }
        base_url = comfy_url.rstrip("/")
        open_url = f"{base_url}/?gw_workflow={quote(workflow_id)}"
    else:
        open_url = comfy_url.rstrip("/")

    return {
        "ok": True,
        "online": online,
        "launched": launched,
        "workflow_id": workflow_id,
        "workflow_name": document.get("name") if document else None,
        "workflow_file": safe_filename or None,
        "saved_path": saved_path_str,
        "workflow_graph": native_graph,
        "workflow_format": workflow_format,
        "browser_adapter": browser_adapter,
        "resolve_definitions": (workflow_format == 'api' or bool(_native_definition_gaps(document))
                                or bool(document.get('metadata', {}).get('needs_host_resolution'))) if document else False,
        "expected_version": document.get("version") if document else None,
        "bridge_connected": False,
        "open_url": open_url,
        "data_status": "partial" if workflow_id or not online else "ok",
        "data_gaps": ([write_error] if write_error else []) + (["native_load_not_confirmed"] if workflow_id else []) + ([] if online else ["ComfyUI service is offline"]),
    }


@router.get("/comfy-bridge/poll")
def comfy_bridge_poll(authorization: Optional[str] = Header(None), x_user_role: str = Header("editor", alias="X-User-Role")):
    """ComfyUI 前端扩展轮询待执行指令；必须通过当前主体认证。"""
    context = _auth(authorization, x_user_role, False)
    principal = registry.principal_digest(context.identity_domain, context.subject)
    cmd = _PENDING_COMFY_COMMAND.pop(principal, None)
    if cmd is not None and time.time() - float(cmd.get("timestamp", 0)) < 60:
        get_document(cmd["workflow_id"], authorization, x_user_role)
        return cmd
    return {"command": "idle"}


@router.post("/comfy-bridge/sync-saved")
async def comfy_bridge_sync_saved(request: Request, authorization: Optional[str] = Header(None), x_user_role: str = Header("editor", alias="X-User-Role")):
    """接收来自 ComfyUI 拦截保存的最新工作流回传，原子更新工作台文档。"""
    context = _auth(authorization, x_user_role, True)
    body = await _json_body(request)
    if not isinstance(body, dict):
        raise CleanroomException(400, "INVALID_REQUEST", "请求体必须是 JSON 对象")
    workflow_id = body.get("workflow_id")
    raw_workflow = body.get("workflow")
    if not isinstance(workflow_id, str) or not isinstance(raw_workflow, dict):
        raise CleanroomException(400, "INVALID_REQUEST", "缺少 workflow_id 或 workflow 数据")

    _workflow_path, current = _resolve_workflow(context, workflow_id, write=True)
    expected = _expected_version(body.get("expected_version"))
    if expected != int(current.get("version", 1)):
        raise VersionConflictException(expected, int(current.get("version", 1)))
    parsed_doc = _parse_payload(raw_workflow, "canvas", current.get("name", "workflow"))
    updated_dict = parsed_doc.as_dict()
    schemas = current.get("metadata", {}).get("node_schemas") or {}
    host_prompt, host_fields = None, None
    if body.get('resolve_definitions') is True:
        # 定义从当前主体配置的实际宿主取得，不信任浏览器随图附送的 Schema。
        probe = await _comfy_call(context, comfy_executor.probe)
        actual = probe.get('object_info') or {}
        supplied_prompt = body.get('api_prompt')
        if supplied_prompt is not None:
            if not isinstance(supplied_prompt, dict) or not supplied_prompt:
                raise CleanroomException(422, 'INVALID_WORKFLOW', '宿主未返回可执行图，原 JSON 已保留')
            _parse_payload(supplied_prompt, 'comfyui', current.get('name', 'workflow'))
        virtual_ids = body.get('virtual_node_ids') or []
        if not isinstance(virtual_ids, list) or any(not isinstance(node_id, str) for node_id in virtual_ids):
            raise CleanroomException(422, 'INVALID_WORKFLOW', '宿主虚拟节点标识无效')
        required_classes = [node.get('class_type') for node in supplied_prompt.values()] if supplied_prompt else []
        required_classes += [node.get('kind') for node in updated_dict.get('nodes', [])
                             if str(node.get('id')) not in virtual_ids and (node.get('raw_payload') or {}).get('mode', 0) not in (2, 4)]
        missing = [kind for kind in required_classes
                   if kind not in {'Note', 'MarkdownNote', 'Reroute'} and not isinstance(actual.get(kind), dict)]
        if not probe.get('online') or missing:
            raise CleanroomException(422, 'NODE_SCHEMA_REQUIRED', 'ComfyUI 缺少该图的真实节点定义，原 JSON 已保留，请补齐缺失节点后重试')
        schemas = {**schemas, **{kind: deepcopy(actual[kind]) for kind in _node_class_ids(updated_dict)
                                if isinstance(actual.get(kind), dict)}}
        if supplied_prompt:
            host_prompt = supplied_prompt
            host_fields = body.get('field_maps') or {}
            if not isinstance(host_fields, dict) or any(not isinstance(fields, dict)
                    or node_id not in host_prompt or any(not isinstance(field, str) or field not in host_prompt[node_id]['inputs']
                    for field in fields.values()) for node_id, fields in host_fields.items()):
                raise CleanroomException(422, 'INVALID_WORKFLOW', '宿主参数映射不匹配，原 JSON 已保留')
    new_nodes = {str(node["id"]): node for node in updated_dict.get("nodes", [])}
    binding_nodes = [node for node in current.get("nodes", [])
                     if node.get("metadata", {}).get("input_assets")
                     and str(node["id"]) in new_nodes
                     and node.get("kind") == new_nodes[str(node["id"])].get("kind")]
    if binding_nodes:
        schemas = await _native_schemas(context, {**current, "nodes": binding_nodes}, for_bindings=True)
    updated_dict["workflow_id"] = workflow_id
    updated_dict["folder_id"] = current.get("folder_id")

    expected = body.get("expected_version")
    request._body = json.dumps({"workflow": updated_dict, "expected_version": expected, "source": current.get("source", "canvas")}).encode()
    res = await _update_document(workflow_id, request, authorization, x_user_role, native_sync=True, schemas=schemas,
                                 host_prompt=host_prompt, host_fields=host_fields)
    return {"synced": True, "workflow_id": workflow_id, "version": res["version"], "data_status": "ok", "data_gaps": []}


@router.get("/comfy-bridge/sync-status")
def comfy_sync_status(authorization: Optional[str] = Header(None), x_user_role: str = Header("editor", alias="X-User-Role")):
    documents = list_documents(authorization, x_user_role)["document_summaries"]
    revisions = {item["workflow_id"]: item["version"] for item in documents}
    return {"revisions": revisions, "synced_now": [], "data_status": "ok", "bridge_active": False,
            "data_gaps": ["native_bridge_not_connected"]}


def _node_class_ids(document: dict[str, Any]) -> list[str]:
    seen: list[str] = []
    for node in document.get("nodes", []):
        kind = node.get("kind") or node.get("class_type")
        if kind is None:
            continue
        text = str(kind)
        if text not in seen:
            seen.append(text)
    return seen


_MODEL_SUFFIX = re.compile(r"\.(safetensors|ckpt|pt|pth|bin)$", re.IGNORECASE)


def _referenced_models(document: dict[str, Any]) -> list[tuple[str, str]]:
    """提取输入、widgets_values 与原始载荷中的模型文件名。"""
    models: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    def visit(value: Any, field_name: str) -> None:
        if isinstance(value, str) and _MODEL_SUFFIX.search(value):
            pair = (field_name, value)
            if pair not in seen:
                seen.add(pair); models.append(pair)
        elif isinstance(value, (list, tuple)):
            for item in value: visit(item, field_name)
        elif isinstance(value, dict):
            for key, item in value.items(): visit(item, str(key))
    for node in document.get("nodes", []):
        visit(node.get("inputs"), "input")
        visit(node.get("widgets_values"), "widget")
        visit(node.get("raw_payload"), "raw")
    return models


def _comfy_probe_message(*, missing_nodes: list[str], missing_models: list[str],
                         local_online: bool, unreachable: bool) -> str:
    if unreachable:
        return "本地 ComfyUI 未配置或不可达，无法对比本地节点与模型"
    if not missing_nodes and not missing_models:
        return "本地环境已具备该工作流所需的全部节点与模型"
    return "本地环境缺少部分节点或模型，建议改用 RunningHub 云端运行"


def _iter_object_info_options(value: Any):
    """Yield candidate filename strings from one ``/object_info`` input spec.

    ComfyUI nests enum options as ``[[...filenames...], {config}]``; a bare list
    of strings is also accepted.  Non-string leaves are ignored.
    """
    if isinstance(value, str):
        yield value
        return
    if not isinstance(value, (list, tuple)):
        return
    for item in value:
        if isinstance(item, str):
            yield item
        elif isinstance(item, (list, tuple)):
            for nested in item:
                if isinstance(nested, str):
                    yield nested


def _model_present(object_info: dict[str, Any], filename: str) -> bool:
    """True when ``/object_info`` advertises ``filename`` in any enum option."""
    needle = filename.replace("\\", "/").rsplit("/", 1)[-1].casefold()
    for spec in object_info.values():
        if not isinstance(spec, dict):
            continue
        inputs = spec.get("input")
        if not isinstance(inputs, dict):
            continue
        for group in inputs.values():
            if not isinstance(group, dict):
                continue
            for value in group.values():
                for option in _iter_object_info_options(value):
                    if option.replace("\\", "/").rsplit("/", 1)[-1].casefold() == needle:
                        return True
    return False


def _probe_comfy_url(object_info_url: str) -> Optional[dict[str, Any]]:
    """GET the configured ``/object_info``; ``None`` when unreachable/invalid."""
    try:
        with httpx.Client(timeout=httpx.Timeout(_COMFY_PROBE_TIMEOUT), follow_redirects=False, trust_env=False) as client:
            with client.stream("GET", object_info_url, headers={"Accept": "application/json"}) as response:
                if response.status_code != 200:
                    return None
                content = bytearray()
                for chunk in response.iter_bytes():
                    content.extend(chunk)
                    if len(content) > _COMFY_PROBE_MAX_BYTES:
                        return None
        payload = json.loads(content)
    except (httpx.HTTPError, ValueError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _compare_local_document(document: dict[str, Any], configured_url: Optional[str] = None) -> dict[str, Any]:
    """Standardized local-comparison shape; ``local_online`` is never guessed."""
    class_ids = _node_class_ids(document)
    models = _referenced_models(document)
    configured = (configured_url if configured_url is not None else os.getenv("GW_COMFYUI_URL", "")).strip()
    if not configured:
        return {
            "local_online": False,
            "local_configured": False,
            "missing_nodes": class_ids,
            "missing_models": [{"model_type": kind, "filename": name} for kind, name in models],
            "installed_nodes_count": 0,
            "required_nodes_count": len(class_ids),
            "installed_models_count": 0,
            "required_models_count": len(models),
            "recommended_target": "rh_cloud",
            "device_name": None,
            "vram_summary": None,
            "status": "unconfigured",
            "diagnostic_message": _comfy_probe_message(
                missing_nodes=class_ids, missing_models=[name for _, name in models],
                local_online=False, unreachable=True),
        }

    parts = urlsplit(configured)
    if parts.scheme not in ("http", "https") or not parts.hostname or parts.username or parts.password:
        return {
            "local_online": False,
            "local_configured": False,
            "missing_nodes": class_ids,
            "missing_models": [{"model_type": kind, "filename": name} for kind, name in models],
            "installed_nodes_count": 0,
            "required_nodes_count": len(class_ids),
            "installed_models_count": 0,
            "required_models_count": len(models),
            "recommended_target": "rh_cloud",
            "device_name": None,
            "vram_summary": None,
            "status": "invalid_configuration",
            "diagnostic_message": "GW_COMFYUI_URL 配置不合法，无法对比本地环境",
        }

    payload = _probe_comfy_url(f"{configured.rstrip('/')}/object_info")
    if payload is None:
        return {
            "local_online": False,
            "local_configured": True,
            "missing_nodes": class_ids,
            "missing_models": [{"model_type": kind, "filename": name} for kind, name in models],
            "installed_nodes_count": 0,
            "required_nodes_count": len(class_ids),
            "installed_models_count": 0,
            "required_models_count": len(models),
            "recommended_target": "rh_cloud",
            "device_name": None,
            "vram_summary": None,
            "status": "unreachable",
            "diagnostic_message": _comfy_probe_message(
                missing_nodes=class_ids, missing_models=[name for _, name in models],
                local_online=False, unreachable=True),
        }

    if not payload or not class_ids:
        return {"local_online": bool(payload), "local_configured": True,
                "status": "unknown", "missing_nodes": [], "missing_models": [],
                "required_nodes_count": len(class_ids), "required_models_count": len(models),
                "installed_nodes_count": None, "installed_models_count": None,
                "recommended_target": None,
                "diagnostic_message": "节点清单或工作流为空，无法确认环境就绪"}
    available = set(payload.keys())
    missing_nodes = [class_id for class_id in class_ids if class_id not in available]
    missing_models = [
        {"model_type": kind, "filename": name}
        for kind, name in models
        if not _model_present(payload, name)
    ]
    return {
        "local_online": True,
        "local_configured": True,
        "missing_nodes": missing_nodes,
        "missing_models": missing_models,
        "installed_nodes_count": len(class_ids) - len(missing_nodes),
        "required_nodes_count": len(class_ids),
        "installed_models_count": len(models) - len(missing_models),
        "required_models_count": len(models),
        "recommended_target": "local_comfy" if not missing_nodes and not missing_models else "rh_cloud",
        "device_name": None,
        "vram_summary": None,
        "status": "compared",
        "diagnostic_message": _comfy_probe_message(
            missing_nodes=missing_nodes, missing_models=[item["filename"] for item in missing_models],
            local_online=True, unreachable=False),
    }


@router.post("/compare-local/{workflow_id}")
def compare_local(workflow_id: str, authorization: Optional[str] = Header(None), x_user_role: str = Header("editor", alias="X-User-Role")):
    """Truthful local ComfyUI comparison; never fabricates node/model inventory."""
    document = get_document(workflow_id, authorization, x_user_role)["workflow"]
    context = _auth(authorization, x_user_role, False)
    configured = registry.read_settings(context).get("comfy_url") or os.getenv("GW_COMFYUI_URL", "")
    return _compare_local_document(document, configured)


@router.get("/settings")
def workflow_settings(authorization: Optional[str] = Header(None), x_user_role: str = Header("editor", alias="X-User-Role")):
    """Per-principal settings probe; secrets are returned masked only."""
    context = _auth(authorization, x_user_role, False)
    return registry.read_settings(context)


@router.post("/settings")
async def update_workflow_settings(request: Request, authorization: Optional[str] = Header(None), x_user_role: str = Header("editor", alias="X-User-Role")):
    context = _auth(authorization, x_user_role, True)
    body = await _json_body(request)
    if not isinstance(body, dict):
        raise CleanroomException(400, "INVALID_REQUEST", "请求体必须是 JSON 对象")
    if any(key in body for key in ("comfy_root_dir", "comfy_url")):
        _require_comfy_admin(context)
    registry.update_settings(context, body)
    return registry.read_settings(context)


@router.get("/list")
def mature_list(authorization: Optional[str] = Header(None), x_user_role: str = Header("editor", alias="X-User-Role")):
    """Mature-controller list shape; uses the canonical document store."""
    body = list_documents(authorization, x_user_role)
    return {"items": body["document_summaries"], "folders": list_folders(authorization, x_user_role)["folders"],
            "data_status": body["data_status"], "data_gaps": body["data_gaps"]}


@router.get("/item/{workflow_id}")
def mature_item(workflow_id: str, authorization: Optional[str] = Header(None), x_user_role: str = Header("editor", alias="X-User-Role")):
    document = get_document(workflow_id, authorization, x_user_role)["workflow"]
    # ``revision`` mirrors the document ``version`` so callers can drive CAS
    # updates without a second round-trip.
    return {**document, "revision": int(document.get("version", 1)), **_reparse_flags(document)}


@router.put("/item/{workflow_id}/name")
async def mature_rename(workflow_id: str, request: Request, authorization: Optional[str] = Header(None), x_user_role: str = Header("editor", alias="X-User-Role")):
    return (await rename_document(workflow_id, request, authorization, x_user_role))["workflow"]


def _apply_widget_update(node: dict[str, Any], update: dict[str, Any]) -> None:
    """Apply one widget update to a stored node, mirroring frontend semantics.

    ``widgets`` is usually a list of ``{name, value, promoted, …}`` dicts, but the
    canonical store also accepts a mapping and primitive values; all shapes must
    round-trip so promotion survives a reload.
    """
    field = str(update.get("field_name"))
    has_value = "value" in update
    value = update.get("value")
    promoted = update.get("promoted")
    widgets = node.get("widgets")
    positional = re.fullmatch(r"widget_(\d+)", field)
    old_value = (widgets[int(positional[1])] if positional and isinstance(widgets, list) and int(positional[1]) < len(widgets)
                 else node.get("inputs", {}).get(field))
    if update.get("asset_id"):
        node.setdefault("metadata", {}).setdefault("input_assets", {})[field] = str(update["asset_id"])
    elif has_value and value != old_value:
        node.get("metadata", {}).get("input_assets", {}).pop(field, None)
    if positional and isinstance(widgets, list):
        index = int(positional[1])
        if index >= len(widgets):
            raise CleanroomException(400, "INVALID_REQUEST", "原生节点参数序号不存在")
        if has_value:
            widgets[index] = value
        if promoted is not None:
            node.setdefault("metadata", {}).setdefault("widget_promotions", {})[field] = bool(promoted)
        return
    node.setdefault("inputs", {})[field] = value if has_value else node.get("inputs", {}).get(field)

    if isinstance(widgets, list):
        for widget in widgets:
            if not isinstance(widget, dict) or widget.get("name") != field:
                continue
            if has_value:
                widget["value"] = value
            if promoted is not None:
                widget["promoted"] = bool(promoted)
            return
        entry: dict[str, Any] = {"name": field}
        if has_value:
            entry["value"] = value
        if promoted is not None:
            entry["promoted"] = bool(promoted)
        widgets.append(entry)
        return
    if isinstance(widgets, dict) and field in widgets:
        current = widgets[field]
        if isinstance(current, dict):
            if has_value:
                current["value"] = value
            if promoted is not None:
                current["promoted"] = bool(promoted)
        elif has_value:
            widgets[field] = value
        return
    if promoted is not None or has_value:
        entry = {"name": field}
        if has_value:
            entry["value"] = value
        if promoted is not None:
            entry["promoted"] = bool(promoted)
        node["widgets"] = [entry]


@router.put("/item/{workflow_id}/params")
async def mature_params(workflow_id: str, request: Request, authorization: Optional[str] = Header(None), x_user_role: str = Header("editor", alias="X-User-Role")):
    """Persist mature-controller widget updates **and** promotion marks.

    ``promoted`` is stored on the node widget itself so a later read through
    ``GET /item/{id}`` can round-trip the extraction list.
    """
    body = await _json_body(request)
    if not isinstance(body, dict):
        raise CleanroomException(400, "INVALID_REQUEST", "请求体必须是 JSON 对象")
    current = get_document(workflow_id, authorization, x_user_role)["workflow"]
    nodes = current.get("nodes", [])
    updates = body.get("widget_updates", [])
    if not isinstance(updates, list) or any(not isinstance(update, dict) or "field_name" not in update for update in updates):
        raise CleanroomException(400, "INVALID_REQUEST", "参数更新必须为对象数组")
    context = _auth(authorization, x_user_role, True)
    positions = body.get("positions", [])
    if not isinstance(positions, list):
        raise CleanroomException(400, "INVALID_REQUEST", "节点位置必须为对象数组")
    for update in updates:
        if update.get("asset_id") and registry.resolve_asset_file(context, str(update["asset_id"])) is None:
            raise CleanroomException(404, "ASSET_NOT_FOUND", "输入素材不存在")
        node = next((value for value in nodes if str(value.get("id")) == str(update.get("node_id"))), None)
        if node is None or not isinstance(update["field_name"], str) or not update["field_name"]:
            raise CleanroomException(400, "INVALID_REQUEST", "参数节点或字段无效")
        _apply_widget_update(node, update)
    for update in positions:
        if not isinstance(update, dict):
            raise CleanroomException(400, "INVALID_REQUEST", "节点位置必须为对象")
        node = next((value for value in nodes if str(value.get("id")) == str(update.get("node_id"))), None)
        if node is None or any(type(update.get(axis)) not in (int, float) or not math.isfinite(update[axis]) for axis in ("x", "y")):
            raise CleanroomException(400, "INVALID_REQUEST", "节点或位置无效")
        node["position"] = {**node.get("position", {}), "x": update["x"], "y": update["y"]}
    request._body = json.dumps({"workflow": current, "expected_version": body.get("expected_version"), "source": current.get("source", "canvas")}).encode()
    res = await _update_document(
        workflow_id, request, authorization, x_user_role,
        allow_stale_source_repair=True,
    )
    wf_data = dict(res["workflow"])
    wf_data["actuator_params"] = _promoted_params(wf_data)
    return wf_data


@router.put("/item/{workflow_id}/folder")
async def mature_move(workflow_id: str, request: Request, authorization: Optional[str] = Header(None), x_user_role: str = Header("editor", alias="X-User-Role")):
    body = await _json_body(request)
    if not isinstance(body, dict) or "folder_id" not in body:
        raise CleanroomException(400, "INVALID_REQUEST", "缺少目标文件夹")
    # 兼容旧客户端，即使携带了错误 payload，也只允许移动元数据。
    request._body = json.dumps({"folder_id": body["folder_id"], "expected_version": body.get("expected_version")}).encode()
    return (await patch_document(workflow_id, request, authorization, x_user_role))["workflow"]


@router.post("/parse-link")
async def mature_parse_link(request: Request, authorization: Optional[str] = Header(None), x_user_role: str = Header("editor", alias="X-User-Role")):
    """从公开链接解析工作流并原子落盘至当前用户的主存储目录。

    识别 RunningHub / LiblibAI 域名后走对应平台通道；纯 JSON 文本仍按画布解析。
    凭据读取当前主体的本地配置或环境变量回退，解析请求不携带密钥。
    """
    context = _auth(authorization, x_user_role, True)
    body = await _json_body(request)
    if not isinstance(body, dict) or not isinstance(body.get("url_or_text"), str):
        raise CleanroomException(400, "INVALID_PROVIDER_REFERENCE", "url_or_text is required")
    reference = body["url_or_text"].strip()
    target_folder = body.get("folder_id") or None
    if target_folder not in (None, "parsed_default"):
        _load_folder(context, str(target_folder))

    if not reference:
        raise CleanroomException(400, "EMPTY_INPUT", "请输入工作流链接或原始工作流 JSON")
    is_json_text = reference.startswith(("{", "["))
    if not is_json_text and platforms.is_runninghub_reference(reference):
        bundle = await _fetch_provider_bundle("runninghub", reference, context)
        preview = _public_preview_from_bundle(bundle)
        if preview is not None:
            # 预览不进入文档/资产存储，已有工作流与用户编辑均保持原版本。
            return preview
        document = _document_from_bundle(bundle, body.get("name"))
        wf_id = bundle.get("workflow_id") or str(uuid.uuid4())
        source_type = "rh_link"
    elif not is_json_text and platforms.is_liblib_reference(reference):
        bundle = await _fetch_provider_bundle("liblib", reference, context)
        document = _document_from_bundle(bundle, body.get("name"))
        wf_id = bundle.get("workflow_id") or str(uuid.uuid4())
        source_type = "liblib_link"
    elif reference.startswith(("http://", "https://")):
        raise CleanroomException(400, "INVALID_PROVIDER_REFERENCE",
                                 "仅支持 RunningHub 与 LiblibAI 工作流链接")
    else:
        try:
            payload = json.loads(reference)
        except (TypeError, json.JSONDecodeError):
            raise CleanroomException(400, "INVALID_WORKFLOW", "url_or_text must contain workflow JSON") from None
        document = _parse_payload(payload, "canvas", body.get("name", "粘贴的工作流"))
        wf_id = str(uuid.uuid4())
        source_type = "local_file"

    stored = document.as_dict()
    stored["workflow_id"] = wf_id
    stored["asset_id"] = asset_hub_bridge._workflow_asset_id(context, wf_id)
    stored["folder_id"] = target_folder
    stored["version"] = 1
    stored["source_type"] = source_type
    stored["source_url"] = reference
    needs_resolution = await _resolve_definitions_in_workbench(context, stored)
    stored.setdefault('metadata', {})['needs_host_resolution'] = needs_resolution

    with _STORAGE_LOCK:
        path = _path(context, wf_id)
        if path.exists():
            current = _load(path)
            expected = body.get("expected_version")
            if not isinstance(expected, int) or isinstance(expected, bool):
                raise CleanroomException(409, "VERSION_CONFLICT", "此工作流已存在；重新导入必须携带原文档版本")
            if expected != int(current.get("version", 1)):
                raise VersionConflictException(expected, int(current.get("version", 1)))
            stored["version"] = expected + 1
            stored["folder_id"] = body.get("folder_id", current.get("folder_id"))
        _atomic_write(path, stored)

    registration = asset_hub_bridge.register_workflow_to_asset_hub(wf_id, stored.get("name", "workflow"), document=stored, context=context)
    return {**stored, 'needs_definition_resolution': needs_resolution,
            **asset_hub_bridge.registration_health(registration)}


@router.post("/parse-upload")
async def mature_parse_upload(request: Request, authorization: Optional[str] = Header(None), x_user_role: str = Header("editor", alias="X-User-Role")):
    context = _auth(authorization, x_user_role, True)
    res = await upload(request, authorization, x_user_role)
    wf_data = res.get("workflow", {})
    wf_id = wf_data.get("workflow_id") or str(uuid.uuid4())
    wf_data["workflow_id"] = wf_id
    wf_data["asset_id"] = asset_hub_bridge._workflow_asset_id(context, wf_id)
    wf_data.setdefault("folder_id", "parsed_default")
    # 文档CAS从1起步；Comfy格式版本保留在metadata/raw_payload，不作为修订号。
    wf_data["version"] = 1
    wf_data["source_type"] = "local_file"
    wf_data.setdefault('metadata', {}).pop('resolved_api', None)
    needs_resolution = await _resolve_definitions_in_workbench(context, wf_data)
    wf_data.setdefault('metadata', {})['needs_host_resolution'] = needs_resolution
    with _STORAGE_LOCK:
        path = _path(context, wf_id)
        if path.exists():
            raise CleanroomException(409, "VERSION_CONFLICT", "此工作流已存在；请通过编辑接口携带原文档版本更新")
        _atomic_write(path, wf_data)
    registration = asset_hub_bridge.register_workflow_to_asset_hub(wf_id, wf_data.get("name", "workflow"), document=wf_data, context=context)
    return {**wf_data, 'needs_definition_resolution': needs_resolution,
            **asset_hub_bridge.registration_health(registration)}


@router.post("/item/{workflow_id}/resolve-definitions")
async def resolve_workflow_definitions(workflow_id: str, request: Request,
                                       authorization: Optional[str] = Header(None), x_user_role: str = Header('editor', alias='X-User-Role')):
    """启动后先重试内部真实定义适配；只有仍不足时才需要宿主页面。"""
    context = _auth(authorization, x_user_role, True)
    body = await _json_body(request)
    expected = _expected_version(body.get('expected_version') if isinstance(body, dict) else None)
    path, _ = _resolve_workflow(context, workflow_id, write=True)
    with _STORAGE_LOCK:
        current = deepcopy(_load(path))
        if current.get('version') != expected:
            raise VersionConflictException(expected, current.get('version'))
    updated = deepcopy(current)
    needs_resolution = await _resolve_definitions_in_workbench(context, updated)
    updated.setdefault('metadata', {})['needs_host_resolution'] = needs_resolution
    with _STORAGE_LOCK:
        latest = _load(path)
        if latest.get('version') != expected:
            raise VersionConflictException(expected, latest.get('version'))
        if updated != current:
            updated['version'] = expected + 1
            _atomic_write(path, updated)
    registration = asset_hub_bridge.register_workflow_to_asset_hub(workflow_id, updated.get('name', 'workflow'), document=updated, context=context)
    return {'workflow': updated, 'needs_definition_resolution': needs_resolution,
            **asset_hub_bridge.registration_health(registration)}


@router.post("/execute", status_code=status.HTTP_202_ACCEPTED)
async def mature_execute(request: Request, authorization: Optional[str] = Header(None), x_user_role: str = Header("editor", alias="X-User-Role"), idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key")):
    return await execute_task(request, authorization, x_user_role, idempotency_key)


def _promoted_params(document: dict[str, Any]) -> list[dict[str, Any]]:
    """Every promoted ``(node, field)`` pair, in stable document order."""
    params: list[dict[str, Any]] = []
    for node in document.get("nodes", []):
        node_id = str(node.get("id"))
        widgets = node.get("widgets")
        if not isinstance(widgets, list):
            continue
        for field, promoted in node.get("metadata", {}).get("widget_promotions", {}).items():
            if promoted and re.fullmatch(r"widget_\d+", field):
                index = int(field.split("_")[1])
                if index < len(widgets):
                    params.append({"node_id": node_id, "field_name": field, "field_value": widgets[index],
                                   "class_type": node.get("kind"), "node_title": node.get("title"), "value_type": None})
        for widget in widgets:
            if not isinstance(widget, dict) or not widget.get("promoted"):
                continue
            params.append({
                "node_id": node_id,
                "field_name": str(widget.get("name")),
                "field_value": widget.get("value"),
                "class_type": node.get("kind"),
                "node_title": node.get("title"),
                "value_type": widget.get("value_type", widget.get("field_type")),
            })
    return params


def _node_info_list(document: dict[str, Any], params: Optional[list[dict[str, Any]]] = None) -> list[dict[str, Any]]:
    """RunningHub-style parameter list: ``nodeId``/``fieldName``/``fieldValue``."""
    selected = params if params is not None else _promoted_params(document)
    return [
        {"nodeId": str(item["node_id"]), "fieldName": str(item["field_name"]), "fieldValue": item.get("field_value")}
        for item in selected
    ]


def _api_prompt_payload(document: dict[str, Any]) -> dict[str, Any]:
    """ComfyUI API-format prompt map: ``{node_id: {class_type, inputs}}``."""
    return compile_prompt(document, document.get("metadata", {}).get("node_schemas"))


def _canvas_contract(workflow_id: str, authorization: Optional[str], x_user_role: str) -> dict[str, Any]:
    """Lightweight contract: promoted params only, no node/connection topology."""
    document = get_document(workflow_id, authorization, x_user_role)["workflow"]
    params = _promoted_params(document)
    return {
        "contract": {"version": 1, "workflow": "normalized WorkflowDocument", "export_formats": ["json"]},
        "workflow_id": workflow_id,
        "name": document.get("name"),
        "version": document.get("version", 1),
        "actuator_params": params,
        "node_info_list": _node_info_list(document, params),
        "data_status": "ok",
        "data_gaps": [],
    }


@router.get("/canvas-contract/{workflow_id}")
def mature_canvas_contract(workflow_id: str, authorization: Optional[str] = Header(None), x_user_role: str = Header("editor", alias="X-User-Role")):
    return _canvas_contract(workflow_id, authorization, x_user_role)


@router.get("/export/{workflow_id}")
async def mature_export(workflow_id: str, request: Request, format: str = "json",
                  authorization: Optional[str] = Header(None), x_user_role: str = Header("editor", alias="X-User-Role")):
    """Format-branching export.

    ``json`` returns the complete stored document (back-compat).  ``api`` returns
    the ComfyUI API prompt map, ``node_info_list`` the RunningHub-style parameter
    list, and ``canvas_contract`` the lightweight promoted-params-only contract.
    """
    document = get_document(workflow_id, authorization, x_user_role)["workflow"]
    selected = (format or "json").lower()
    if selected == "json":
        return document
    if selected in ("litegraph", "comfyui"):
        context = _auth(authorization, x_user_role, False)
        return export_to_comfy_litegraph(document, await _native_schemas(context, document))
    if selected == "api":
        return _api_prompt_payload(document)
    if selected == "node_info_list":
        return _node_info_list(document)
    if selected == "canvas_contract":
        return _canvas_contract(workflow_id, authorization, x_user_role)
    raise CleanroomException(400, "INVALID_EXPORT_FORMAT", "不支持的导出格式")


@router.post("/auto-layout/{workflow_id}", status_code=status.HTTP_200_OK)
async def mature_auto_layout(workflow_id: str, request: Request,
                             authorization: Optional[str] = Header(None), x_user_role: str = Header("editor", alias="X-User-Role")):
    """Real Sugiyama-style layered layout, persisted under version CAS.

    Layering is forward longest-path, terminal nodes are pulled back to their
    inputs, rows are ordered by bidirectional barycenter, and unconnected notes
    are packed into their own block.  Positions are written back to the stored
    document, so this is a mutating operation, not a read-only echo.
    """
    context = _auth(authorization, x_user_role, True)
    body = await _json_body(request, allow_empty=True)
    expected = body.get("expected_version", request.query_params.get("expected_version")) if isinstance(body, dict) else None
    path, _ = _resolve_workflow(context, workflow_id, write=True)
    with _STORAGE_LOCK:
        current = _load(path)
        current_version = int(current.get("version", 1))
        expected = _expected_version(expected)
        if expected != current_version:
            raise VersionConflictException(expected, current_version)
        dimensions = body.get("node_dimensions", {}) if isinstance(body, dict) else {}
        if not isinstance(dimensions, dict):
            raise CleanroomException(400, "INVALID_REQUEST", "节点尺寸必须为对象")
        for node in current.get("nodes", []):
            measured = dimensions.get(str(node.get("id")))
            if measured is not None:
                if not isinstance(measured, dict) or any(type(measured.get(axis)) not in (int, float) or not math.isfinite(measured[axis]) or not 0 < measured[axis] <= 100000 for axis in ("width", "height")):
                    raise CleanroomException(400, "INVALID_REQUEST", "节点尺寸无效")
                node["position"] = {**node.get("position", {}), **measured}
        positions = compute_layout(current.get("nodes", []), current.get("connections", []))
        for node in current.get("nodes", []):
            coordinates = positions.get(str(node.get("id")))
            if coordinates is None:
                continue
            position = node.get("position")
            if not isinstance(position, dict):
                position = {}
            position["x"] = coordinates[0]
            position["y"] = coordinates[1]
            node["position"] = position
        current["version"] = current_version + 1
        current["asset_id"] = current.get("asset_id") or asset_hub_bridge._workflow_asset_id(context, workflow_id)
        _atomic_write(path, current)
    registration = asset_hub_bridge.register_workflow_to_asset_hub(workflow_id, current.get("name"), document=current, context=context)
    return {"workflow_id": workflow_id, "version": current["version"], "workflow": current,
            "document": _summary(current, workflow_id)["document"], **asset_hub_bridge.registration_health(registration)}


@router.get("/capabilities")
def capabilities(authorization: Optional[str] = Header(None), x_user_role: str = Header("editor", alias="X-User-Role")):
    context = _auth(authorization, x_user_role, False)
    providers = {provider: ("configured" if get_provider(provider).configured else "unavailable") for provider in PROVIDERS}
    comfy_configured = bool(_comfy_endpoint(context).base_url)
    executors = {"local_comfy": "configured" if comfy_configured else "unavailable",
                 "rh_cloud": "configured" if registry.credential_value('rh_api_key', context) else "unavailable"}
    gaps = ['native_host_acceptance_pending', 'supplier_acceptance_pending']
    gaps += [name + '_not_configured' for name, value in executors.items() if value == 'unavailable']
    if not png_available():
        gaps.append('image_metadata_parser_unavailable')
    return {"providers": providers,
            "executors": executors,
            # 只如实报告配置状态；此处不做阻塞网络探测，在线与否由 /comfy-control 查询。
            "comfy_host": "configured" if comfy_configured else "not_configured",
            "implemented": ["parse", "upload-json", "upload-png-text", "upload-webp-exif", "provider-fetch-gateway", "task-contract", "canvas-contract", "canvas-export", "runtime-isolated-storage", "workflow execution and polling", "input-upload", "asset-registration-recovery", "psd-original-download", "legacy-rh-link-slot-reparse"],
            "partial": ["provider-fetch-gateway", "native-comfy-bridge", "native-schema-conversion"],
            "todo": ["native ComfyUI host acceptance", "real supplier acceptance", "canvas binary export"],
            "data_status": "partial", "data_gaps": gaps}


@router.post("/browse_directory")
async def browse_directory(request: Request, authorization: Optional[str] = Header(None), x_user_role: str = Header("editor", alias="X-User-Role")):
    """调用本地系统对话框选取目录（支持 Windows 原生对话框并返回绝对路径）。"""
    _auth(authorization, x_user_role, False)
    body = {}
    try:
        body = await request.json()
    except Exception:
        body = {}
    initial_dir = body.get("initial_dir", "") if isinstance(body, dict) else ""
    title = body.get("title", "选择本地 ComfyUI 目录") if isinstance(body, dict) else "选择本地 ComfyUI 目录"

    selected = ""
    # 优先尝试使用标准库 tkinter
    try:
        import tkinter
        from tkinter import filedialog
        root = tkinter.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        raw_res = filedialog.askdirectory(initialdir=initial_dir or None, title=title, parent=root)
        root.destroy()
        if raw_res:
            selected = str(raw_res).replace("/", "\\")
    except Exception:
        pass

    # 降级尝试 PowerShell FolderBrowserDialog（原生 Windows 支持）
    if not selected and os.name == "nt":
        try:
            import subprocess
            escaped_title = str(title).replace("'", "''")
            ps_script = (
                "Add-Type -AssemblyName System.Windows.Forms; "
                "$f = New-Object System.Windows.Forms.FolderBrowserDialog; "
                f"$f.Description = '{escaped_title}'; "
            )
            if initial_dir and os.path.exists(initial_dir):
                escaped_init = str(initial_dir).replace("'", "''")
                ps_script += f"$f.SelectedPath = '{escaped_init}'; "
            ps_script += "if ($f.ShowDialog() -eq 'OK') { [Console]::Write($f.SelectedPath) }"
            res = subprocess.run(["powershell", "-Sta", "-Command", ps_script], capture_output=True, text=True, timeout=30)
            if res.returncode == 0 and res.stdout.strip():
                selected = res.stdout.strip()
        except Exception:
            pass

    return {"status": "ok", "selected_dir": selected}
