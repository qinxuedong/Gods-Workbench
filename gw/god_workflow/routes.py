"""Workflow parsing and per-principal document storage API."""
from __future__ import annotations

import asyncio
import ipaddress
import socket
import hashlib
import json
import os
import re
import tempfile
import threading
import uuid
import time
import math
from pathlib import Path
from typing import Any, Optional
from urllib.parse import quote, urlsplit

import httpx
from fastapi import APIRouter, Header, Request, status
from fastapi.responses import FileResponse

from gw.core.auth import AuthContext, require_authenticated, require_edit_access
from gw.core.errors import CleanroomException, VersionConflictException
from gw.core.runtime_paths import RuntimePathError, resolve_runtime_paths
from gw.god_workflow import comfy_executor
from gw.god_workflow import platforms
from gw.god_workflow import registry
from gw.god_workflow import asset_hub_bridge
from gw.god_workflow.layout import compute_layout
from gw.god_workflow.models import WorkflowSource
from gw.god_workflow.clients import PROVIDERS, get_provider, unavailable
from gw.god_workflow.parser import (MAX_UPLOAD_BYTES, MAX_PNG_TEXT_BYTES, WorkflowParseError,
                                   parse_workflow, parse_upload, png_available,
                                   build_topology_from_public_detail, extract_source_graph)
from gw.god_workflow.exporter import export_to_comfy_litegraph
from gw.god_workflow.compiler import compile_prompt

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


def _principal_root(context: AuthContext) -> Path:
    root = _workflow_root() / registry.principal_digest(context.identity_domain, context.subject)
    root.mkdir(parents=True, exist_ok=True)
    return root


def _path(context: AuthContext, workflow_id: str) -> Path:
    try:
        parsed = uuid.UUID(workflow_id)
        filename = f"{parsed}.json"
    except (ValueError, AttributeError):
        cleaned = re.sub(r'[^a-zA-Z0-9_\-]', '', str(workflow_id or ''))
        if not cleaned or cleaned.casefold() in _PRIVATE_METADATA_NAMES:
            raise CleanroomException(404, "WORKFLOW_NOT_FOUND", "工作流不存在") from None
        filename = f"{cleaned}.json"
    return _principal_root(context) / filename


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
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CleanroomException(500, "WORKFLOW_STORAGE_ERROR", "工作流存储内容不可读取") from exc


def _stored_document_items(context: AuthContext):
    """工作流与私有清单共目录，列表和目录操作仅遍历真实文档。"""
    for path in sorted(_principal_root(context).glob("*.json")):
        if path.stem.casefold() in _PRIVATE_METADATA_NAMES:
            continue
        try:
            document = _load(path)
        except CleanroomException:
            continue
        if isinstance(document, dict) and isinstance(document.get("nodes"), list):
            yield path, document


def _summary(document: dict[str, Any], workflow_id: str) -> dict[str, Any]:
    st = document.get("source_type") or document.get("source")
    return {"workflow_id": workflow_id, "name": document.get("name", "workflow"),
            "source": document.get("source"), "source_type": st, "source_url": document.get("source_url"),
            "folder_id": document.get("folder_id") or "parsed_default", "version": document.get("version", 1),
            "node_count": len(document.get("nodes", [])),
            "connection_count": len(document.get("connections", [])),
            "can_run_locally": bool(document.get("env_diff", {}).get("can_run_locally", False)),
            "document": {"workflow_id": workflow_id, "name": document.get("name", "workflow"),
                         "source": document.get("source"), "source_type": st, "source_url": document.get("source_url"),
                         "folder_id": document.get("folder_id") or "parsed_default", "version": document.get("version", 1),
                         "node_count": len(document.get("nodes", [])),
                         "connection_count": len(document.get("connections", []))}}



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
    project = projects.default_projects_service.get_owned_project(project_id, projects.owner_key_for_context(context))
    if project.archived_at or project.deleted_at:
        raise CleanroomException(404, "PROJECT_NOT_FOUND", "来源项目不可访问")
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
        return parse_workflow(request_payload, source=source, name=name)
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


@router.post("/tasks/execute", status_code=status.HTTP_202_ACCEPTED)
async def execute_task(request: Request, authorization: Optional[str] = Header(None), x_user_role: str = Header("editor", alias="X-User-Role"), idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key")):
    """提交本地 ComfyUI 执行，返回真实 task_id。

    只走 ComfyUI 官方 ``POST /prompt``；未配置 ``GW_COMFYUI_URL`` 时返回标准
    503，不下发任何请求，也不伪造 task_id。
    """
    context = _auth(authorization, x_user_role, True)
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
        document = get_document(workflow_id, authorization, x_user_role)["workflow"]
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
        # 本地文档 ID 只用于归属与账本，不能冒充供应商的工作流 ID。
        stored_rh_id = document.get("external_workflow_id")
        if document.get("external_provider") not in (None, "runninghub") or document.get("source_type") == "liblib_link":
            stored_rh_id = None
        rh_wf_id = body.get("rh_workflow_id") or stored_rh_id
        if (isinstance(rh_wf_id, bool) or not isinstance(rh_wf_id, (str, int))
                or not str(rh_wf_id).strip() or len(str(rh_wf_id)) > 128):
            raise CleanroomException(400, "INVALID_REQUEST", "缺少真实 RunningHub 工作流标识")
        rh_wf_id = str(rh_wf_id).strip()
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
        node_info = [{"nodeId": node_id, "fieldName": field, "fieldValue": value}
                     for (node_id, field), value in cloud_values.items()]
    else:
        raise unavailable("WORKFLOW_EXECUTOR_UNAVAILABLE", "不支持的执行目标")
    source_context = _source_context(context, body.get("source_context", document.get("metadata", {}).get("source_context", {})))
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
                task = registry.update_task(context, task["job_id"], node_info_snapshot=node_info)
                submission_started = True
                result = await platforms.create_rh_cloud_task(str(rh_wf_id), node_info, api_key,
                    domain=snapshot["domain"])
                remote_id = result.get("taskId") or result.get("task_id")
            if remote_id:
                task = registry.update_task(context, task["job_id"], remote_task_id=str(remote_id),
                    prompt_id=str(remote_id), remote_id_missing=False, status="accepted",
                    execution_status="accepted", source_status="queued")
        except (CleanroomException, OSError) as exc:
            # 绑定写盘失败仍返回已预留公共ID，不因错误重发外部请求。
            task = registry.load_task(context, task["job_id"])
            task["messages"] = [exc.code if isinstance(exc, CleanroomException) else "TASK_LEDGER_WRITE_FAILED"]
            if not submission_started:
                task = registry.update_task(context, task["job_id"], status="failed", execution_status="failed",
                                            remote_id_missing=False, messages=task["messages"])
    return {**task, "poll_hint": f"/api/god_workflow/tasks/{task['job_id']}",
            "idempotent_replay": replay, "data_status": "ok",
            "data_gaps": ["remote_task_id_missing"] if task.get("remote_id_missing") else []}


@router.get("/tasks")
def list_workflow_tasks(authorization: Optional[str] = Header(None), x_user_role: str = Header("editor", alias="X-User-Role")):
    context = _auth(authorization, x_user_role, False)
    return {"tasks": registry.list_tasks(context), "data_status": "ok", "data_gaps": []}


@router.get("/tasks/{task_id}")
async def task_status(task_id: str, authorization: Optional[str] = Header(None), x_user_role: str = Header("editor", alias="X-User-Role")):
    """轮询真实任务状态；终态结果同步登记为素材。"""
    context = _auth(authorization, x_user_role, False)
    task = registry.load_task(context, task_id)
    if task.get("status") in {"completed", "failed", "cancelled", "outcome_unknown"}:
        return {**task, "data_status": "ok", "data_gaps": []}
    if task.get("remote_id_missing"):
        return {**task, "data_status": "ok", "data_gaps": ["remote_task_id_missing"]}

    target = str(task.get("target") or "local_comfy")
    if target in ("rh_cloud", "runninghub"):
        api_key = registry.credential_value("rh_api_key", context)
        if not api_key:
            task = registry.update_task(context, task_id, status="failed", source_status="failed", execution_status="failed", messages=["RunningHub API Key is not configured"])
            return {**task, "data_status": "ok", "data_gaps": []}
        domain = task.get("executor_snapshot", {}).get("domain") or os.environ.get("GW_RUNNINGHUB_DOMAIN", platforms.RUNNINGHUB_DEFAULT_DOMAIN)
        query_res = await platforms.query_rh_task_outputs(str(task.get("prompt_id") or ""), api_key, domain=domain)
        st = query_res.get("status")
        if st == "SUCCESS":
            assets = await _collect_rh_task_outputs(context, task_id, query_res.get("outputs", []))
            complete = bool(assets) and all(item.get("registered") for item in assets)
            task = registry.update_task(context, task_id, status="completed" if complete else "failed",
                                        source_status="SUCCESS", execution_status="completed",
                                        collection_status="completed" if complete else "partial_failed", outputs=assets)
            return {**task, "data_status": "ok", "data_gaps": ([] if complete else ["output_collection_incomplete"])}
        elif st == "RUNNING":
            task = registry.update_task(context, task_id, status="running", source_status="RUNNING", execution_status="running")
            return {**task, "progress": {"known": False, "value": None}, "data_status": "ok", "data_gaps": []}
        else:
            msg = query_res.get("message") or "RunningHub 任务执行失败"
            task = registry.update_task(context, task_id, status="failed", source_status="FAILED", execution_status="failed", messages=[msg])
            return {**task, "data_status": "ok", "data_gaps": []}

    history = await _comfy_call(context, comfy_executor.history, str(task.get("prompt_id") or ""), task=task)
    if not history["found"]:
        task = registry.update_task(context, task_id, status="running")
        return {**task, "progress": {"known": False, "value": None},
                "data_status": "ok", "data_gaps": []}

    summary = comfy_executor.summarize(history["entry"])
    assets = await _collect_task_outputs(context, task_id, summary["outputs"], task=task)
    complete = bool(assets) and all(item.get("registered") for item in assets)
    task = registry.update_task(context, task_id,
                                status="completed" if summary["status"] == "succeeded" and complete else "failed",
                                source_status=summary["status"],
                                execution_status="completed" if summary["status"] == "succeeded" else "failed",
                                collection_status="completed" if complete else "partial_failed",
                                outputs=assets, messages=summary["messages"])
    return {**task, "data_status": "ok", "data_gaps": []}


@router.post("/tasks/{task_id}/collect")
async def retry_task_collection(task_id: str, authorization: Optional[str] = Header(None), x_user_role: str = Header("editor", alias="X-User-Role")):
    """只重试回收已完成的远端结果，绝不再次提交生成。"""
    context = _auth(authorization, x_user_role, True)
    task = registry.load_task(context, task_id)
    if task.get("execution_status") != "completed":
        raise CleanroomException(409, "EXECUTION_NOT_COMPLETED", "执行尚未确认完成，不能回收")
    if task.get("collection_status") == "completed":
        return task
    if task.get("target") == "rh_cloud":
        api_key = registry.credential_value("rh_api_key", context)
        if not api_key:
            raise unavailable("WORKFLOW_EXECUTOR_UNAVAILABLE", "RunningHub API Key is not configured")
        result = await platforms.query_rh_task_outputs(task["remote_task_id"], api_key,
            domain=task.get("executor_snapshot", {}).get("domain") or os.environ.get("GW_RUNNINGHUB_DOMAIN", platforms.RUNNINGHUB_DEFAULT_DOMAIN))
        if result.get("status") != "SUCCESS":
            raise CleanroomException(409, "OUTPUTS_NOT_AVAILABLE", "远端结果尚不可回收")
        outputs = await _collect_rh_task_outputs(context, task_id, result.get("outputs", []))
    else:
        history = await _comfy_call(context, comfy_executor.history, task["remote_task_id"], task=task)
        if not history["found"]:
            raise CleanroomException(409, "OUTPUTS_NOT_AVAILABLE", "远端结果尚不可回收")
        outputs = await _collect_task_outputs(context, task_id, comfy_executor.summarize(history["entry"])["outputs"], task=task)
    complete = bool(outputs) and all(item.get("registered") for item in outputs)
    return registry.update_task(context, task_id, status="completed" if complete else "failed",
        collection_status="completed" if complete else "partial_failed", outputs=outputs)


@router.post("/tasks/{task_id}/cancel")
async def task_cancel(task_id: str, authorization: Optional[str] = Header(None), x_user_role: str = Header("editor", alias="X-User-Role")):
    """本地标记取消并尽力中断远端；不谎称远端已取消。"""
    context = _auth(authorization, x_user_role, True)
    task = registry.load_task(context, task_id)
    if task.get("status") in {"completed", "failed", "cancelled"} | comfy_executor.TERMINAL_STATES:
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


class _PublicOutputBackend:
    """在连接层解析并钉住所获准公网IP，TLS仍使用原始主机名，阻止DNS重绑定。"""
    def __init__(self, backend):
        self.backend = backend

    async def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):
        if isinstance(host, bytes):
            host = host.decode("ascii")
        infos = await asyncio.wait_for(asyncio.get_running_loop().getaddrinfo(
            host, port, type=socket.SOCK_STREAM), timeout=timeout or 30)
        addresses = [info[4][0] for info in infos]
        if not addresses or any(not ipaddress.ip_address(address).is_global for address in addresses):
            raise CleanroomException(400, "OUTPUT_ADDRESS_NOT_ALLOWED", "输出地址不是获准公网地址")
        return await self.backend.connect_tcp(host=addresses[0], port=port, timeout=timeout,
            local_address=local_address, socket_options=socket_options)

    async def connect_unix_socket(self, *args, **kwargs):
        raise CleanroomException(400, "OUTPUT_ADDRESS_NOT_ALLOWED", "输出不支持本地套接字")

    async def sleep(self, seconds):
        await self.backend.sleep(seconds)


def _output_transport():
    import httpcore
    from httpcore._backends.auto import AutoBackend
    transport = httpx.AsyncHTTPTransport(retries=0, trust_env=False)
    transport._pool = httpcore.AsyncConnectionPool(network_backend=_PublicOutputBackend(AutoBackend()))
    return transport


async def _collect_rh_task_outputs(context: AuthContext, task_id: str,
                                   outputs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """把 RunningHub 云端输出文件下载落到运行时数据根并登记为素材。"""
    collected: list[dict[str, Any]] = []
    if len(outputs) > 32:
        return [{"registered": False, "code": "OUTPUT_COUNT_EXCEEDED"}]
    for item in outputs:
        if not isinstance(item, dict):
            continue
        url = str(item.get("fileUrl") or item.get("url") or "")
        filename = str(item.get("fileName") or item.get("filename") or "")
        if not url:
            continue
        if not filename:
            filename = url.split("?")[0].rsplit("/", 1)[-1] or f"rh_output_{len(collected)+1}.png"
        try:
            parts = urlsplit(url)
            allowed_hosts = {host.strip().lower() for host in os.getenv("GW_WORKFLOW_OUTPUT_HOSTS", "").split(",") if host.strip()}
            if (parts.scheme != "https" or parts.hostname not in allowed_hosts or parts.username or parts.password
                    or parts.port not in (None, 443) or parts.fragment):
                raise CleanroomException(400, "OUTPUT_URL_NOT_ALLOWED", "输出来源未获准")
            async with httpx.AsyncClient(timeout=30.0, follow_redirects=False, trust_env=False, transport=_output_transport()) as client:
                async with client.stream("GET", url) as resp:
                    if resp.status_code != 200:
                        raise CleanroomException(502, "DOWNLOAD_FAILED", "输出下载失败或发生重定向")
                    content_buffer = bytearray()
                    async for chunk in resp.aiter_bytes():
                        content_buffer.extend(chunk)
                        if len(content_buffer) > registry.MAX_ASSET_BYTES:
                            raise CleanroomException(502, "OUTPUT_TOO_LARGE", "输出超过大小限制")
                    content = bytes(content_buffer)
                    media_type = resp.headers.get("content-type") or "application/octet-stream"
        except CleanroomException as exc:
            collected.append({"filename": filename, "registered": False, "code": exc.code})
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
        item = _load(_path(context, workflow_id))
    wf_data = dict(item)
    wf_data["actuator_params"] = _promoted_params(wf_data)
    return {"workflow_id": workflow_id, "workflow": wf_data, "document": _summary(item, workflow_id)["document"],
            "data_status": "ok", "data_gaps": []}


async def _update_document(workflow_id: str, request: Request, authorization: Optional[str], x_user_role: str):
    context = _auth(authorization, x_user_role, True)
    body = await _json_body(request)
    if not isinstance(body, dict):
        raise CleanroomException(400, "INVALID_REQUEST", "请求体必须是 JSON 对象")
    with _STORAGE_LOCK:
        path = _path(context, workflow_id)
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
        stored.update({"workflow_id": workflow_id, "version": current_version + 1,
                       "folder_id": body.get("folder_id", current.get("folder_id"))})
        stored["asset_id"] = asset_hub_bridge._workflow_asset_id(context, workflow_id)
        stored.setdefault("metadata", {})["source_context"] = _source_context(context, body.get("source_context", stored.get("metadata", {}).get("source_context", {})))
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
    context = _auth(authorization, x_user_role, True)
    body = await _json_body(request)
    if not isinstance(body, dict):
        raise CleanroomException(400, "INVALID_REQUEST", "请求体必须是 JSON 对象")
    with _STORAGE_LOCK:
        path = _path(context, workflow_id)
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
    return {"workflow_id": workflow_id, "deleted": True, "data_status": "ok", "data_gaps": []}


@router.post("/documents/{workflow_id}/rename")
async def rename_document(workflow_id: str, request: Request, authorization: Optional[str] = Header(None), x_user_role: str = Header("editor", alias="X-User-Role")):
    context = _auth(authorization, x_user_role, True)
    body = await _json_body(request)
    if not isinstance(body, dict):
        raise CleanroomException(400, "INVALID_REQUEST", "请求体必须是 JSON 对象")
    with _STORAGE_LOCK:
        path = _path(context, workflow_id)
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
    context = _auth(authorization, x_user_role, True)
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


_PENDING_COMFY_COMMAND: dict[str, dict[str, Any]] = {}
def _require_comfy_admin(context):
    if context.role != "admin":
        raise CleanroomException(403, "FORBIDDEN", "CF 启动配置及启停仅限管理员")


def _comfy_operation(context, action, token=None):
    from .comfy_control import service
    settings = registry.read_settings(context)
    url = str(settings.get("comfy_url") or os.getenv("GW_COMFYUI_URL") or "http://127.0.0.1:8188").strip()
    root = str(settings.get("comfy_root_dir") or os.getenv("GW_COMFYUI_ROOT_DIR") or "")
    principal = registry.principal_digest(context.identity_domain, context.subject)
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
async def open_in_comfy(workflow_id: Optional[str] = None, authorization: Optional[str] = Header(None), x_user_role: str = Header("editor", alias="X-User-Role")):
    """准备在本地 ComfyUI 中打开工作流；无工作流时直接拉起启动并打开 ComfyUI 根地址。"""
    context = _auth(authorization, x_user_role, True)

    document = None
    if workflow_id:
        document = get_document(workflow_id, authorization, x_user_role)["workflow"]

    settings = registry.read_settings(context)
    comfy_url = _comfy_endpoint(context).base_url
    if not comfy_url:
        raise unavailable('WORKFLOW_EXECUTOR_UNAVAILABLE', '请先配置 ComfyUI 地址')
    comfy_root = settings.get("comfy_root_dir") or os.environ.get("GW_COMFYUI_ROOT_DIR") or ""

    # 与顶栏共用受控生命周期；普通用户可以打开在线服务但不能隐式启动。
    control = _comfy_operation(context, "status")
    if not control.get("highlight"):
        raise unavailable('WORKFLOW_EXECUTOR_UNAVAILABLE', 'ComfyUI 未在线，请先使用全站 CF 控件启动或连接服务')
    online = bool(control.get("highlight"))
    launched = bool(control.get("launched"))
    effective_root = comfy_root

    safe_name = ""
    safe_filename = ""
    saved_path_str = ""
    write_error = None
    native_graph = export_to_comfy_litegraph(document) if document else None

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
        open_url = f"{base_url}/?rh_workflow={quote(safe_filename)}&rh_wf_id={quote(workflow_id)}&rh_uuid={quote(comfy_uuid)}"
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
    _auth(authorization, x_user_role, True)
    body = await _json_body(request)
    if not isinstance(body, dict):
        raise CleanroomException(400, "INVALID_REQUEST", "请求体必须是 JSON 对象")
    workflow_id = body.get("workflow_id")
    raw_workflow = body.get("workflow")
    if not isinstance(workflow_id, str) or not isinstance(raw_workflow, dict):
        raise CleanroomException(400, "INVALID_REQUEST", "缺少 workflow_id 或 workflow 数据")

    current = get_document(workflow_id, authorization, x_user_role)["workflow"]
    parsed_doc = _parse_payload(raw_workflow, "canvas", current.get("name", "workflow"))
    updated_dict = parsed_doc.as_dict()
    updated_dict["workflow_id"] = workflow_id
    updated_dict["folder_id"] = current.get("folder_id")

    expected = body.get("expected_version")
    request._body = json.dumps({"workflow": updated_dict, "expected_version": expected, "source": current.get("source", "canvas")}).encode()
    res = await _update_document(workflow_id, request, authorization, x_user_role)
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
    return {**document, "revision": int(document.get("version", 1))}


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
    res = await _update_document(workflow_id, request, authorization, x_user_role)
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
    return {**stored, **asset_hub_bridge.registration_health(registration)}


@router.post("/parse-upload")
async def mature_parse_upload(request: Request, authorization: Optional[str] = Header(None), x_user_role: str = Header("editor", alias="X-User-Role")):
    context = _auth(authorization, x_user_role, True)
    res = await upload(request, authorization, x_user_role)
    wf_data = res.get("workflow", {})
    wf_id = wf_data.get("workflow_id") or str(uuid.uuid4())
    wf_data["workflow_id"] = wf_id
    wf_data["asset_id"] = asset_hub_bridge._workflow_asset_id(context, wf_id)
    wf_data.setdefault("folder_id", "parsed_default")
    wf_data.setdefault("version", 1)
    wf_data["source_type"] = "local_file"
    with _STORAGE_LOCK:
        _atomic_write(_path(context, wf_id), wf_data)
    registration = asset_hub_bridge.register_workflow_to_asset_hub(wf_id, wf_data.get("name", "workflow"), document=wf_data, context=context)
    return {**wf_data, **asset_hub_bridge.registration_health(registration)}


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
def mature_export(workflow_id: str, request: Request, format: str = "json",
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
        return export_to_comfy_litegraph(document)
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
    with _STORAGE_LOCK:
        path = _path(context, workflow_id)
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
        current["asset_id"] = asset_hub_bridge._workflow_asset_id(context, workflow_id)
        _atomic_write(path, current)
    registration = asset_hub_bridge.register_workflow_to_asset_hub(workflow_id, current.get("name"), document=current, context=context)
    return {"workflow_id": workflow_id, "version": current["version"], "workflow": current,
            "document": _summary(current, workflow_id)["document"], **asset_hub_bridge.registration_health(registration)}


@router.get("/capabilities")
def capabilities(authorization: Optional[str] = Header(None), x_user_role: str = Header("editor", alias="X-User-Role")):
    context = _auth(authorization, x_user_role, False)
    providers = {provider: ("configured" if get_provider(provider).configured else "unavailable") for provider in PROVIDERS}
    executors = {"local_comfy": "configured" if _comfy_endpoint(context).base_url else "unavailable",
                 "rh_cloud": "configured" if registry.credential_value('rh_api_key', context) else "unavailable"}
    gaps = ['native_host_acceptance_pending', 'supplier_acceptance_pending']
    gaps += [name + '_not_configured' for name, value in executors.items() if value == 'unavailable']
    if not png_available():
        gaps.append('image_metadata_parser_unavailable')
    return {"providers": providers,
            "executors": executors,
            "implemented": ["parse", "upload-json", "upload-png-text", "upload-webp-exif", "provider-fetch-gateway", "task-contract", "canvas-contract", "canvas-export", "runtime-isolated-storage", "workflow execution and polling", "input-upload", "asset-registration-recovery", "psd-original-download"],
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
