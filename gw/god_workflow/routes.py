"""Workflow parsing and per-principal document storage API."""
from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import threading
import uuid
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlsplit

import httpx
from fastapi import APIRouter, Header, Request, status
from fastapi.responses import FileResponse

from gw.core.auth import AuthContext, require_authenticated, require_edit_access
from gw.core.errors import CleanroomException, VersionConflictException
from gw.core.runtime_paths import RuntimePathError, resolve_runtime_paths
from gw.god_workflow import registry
from gw.god_workflow.layout import compute_layout
from gw.god_workflow.models import WorkflowSource
from gw.god_workflow.clients import PROVIDERS, get_provider, unavailable
from gw.god_workflow.parser import (MAX_UPLOAD_BYTES, MAX_PNG_TEXT_BYTES, WorkflowParseError,
                                   parse_workflow, parse_upload, png_available)

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
_STORAGE_LOCK = threading.RLock()


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
    except (ValueError, AttributeError):
        raise CleanroomException(404, "WORKFLOW_NOT_FOUND", "工作流不存在") from None
    return _principal_root(context) / f"{parsed}.json"


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
    try:
        return int(expected)
    except (TypeError, ValueError):
        raise CleanroomException(400, "INVALID_VERSION", "expected_revision 必须为整数") from None


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


def _summary(document: dict[str, Any], workflow_id: str) -> dict[str, Any]:
    return {"workflow_id": workflow_id, "name": document.get("name", "workflow"),
            "source": document.get("source"), "folder_id": document.get("folder_id"), "version": document.get("version", 1),
            "node_count": len(document.get("nodes", [])),
            "connection_count": len(document.get("connections", [])),
            "document": {"workflow_id": workflow_id, "name": document.get("name", "workflow"),
                         "source": document.get("source"), "folder_id": document.get("folder_id"), "version": document.get("version", 1),
                         "node_count": len(document.get("nodes", [])),
                         "connection_count": len(document.get("connections", []))}}


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
    _auth(authorization, x_user_role, True)
    body = await _json_body(request, allow_empty=True)
    workflow_id = body.get("workflow_id") if isinstance(body, dict) else None
    if not isinstance(workflow_id, str):
        raise CleanroomException(400, "INVALID_PROVIDER_REFERENCE", "workflow_id is required")
    payload = await get_provider(provider).fetch(workflow_id)
    document = _parse_payload(payload, provider, body.get("name", "workflow"))
    return {"workflow": document.as_dict(), "data_status": "ok", "data_gaps": []}


@router.post("/tasks/execute", status_code=status.HTTP_202_ACCEPTED)
async def execute_task(request: Request, authorization: Optional[str] = Header(None), x_user_role: str = Header("editor", alias="X-User-Role")):
    _auth(authorization, x_user_role, True)
    raise unavailable("WORKFLOW_EXECUTOR_UNAVAILABLE", "No workflow execution adapter is configured")


@router.get("/tasks/{task_id}")
def task_status(task_id: str, authorization: Optional[str] = Header(None), x_user_role: str = Header("editor", alias="X-User-Role")):
    _auth(authorization, x_user_role, False)
    raise unavailable("WORKFLOW_EXECUTOR_UNAVAILABLE", "No workflow execution adapter is configured")


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
    for path in _principal_root(context).glob("*.json"):
        try:
            if _load(path).get("folder_id") == folder_id:
                return True
        except CleanroomException:
            continue
    return False


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
    with _STORAGE_LOCK:
        _atomic_write(_path(context, workflow_id), stored)
    # 仅允许写入当前主体目录；不要生成未隔离的公共副本。
    return {"name": name, "workflow_id": workflow_id, "version": 1, "workflow": stored,
            "document": _summary(stored, workflow_id)["document"], "data_status": "ok", "data_gaps": []}


@router.get("/documents")
def list_documents(authorization: Optional[str] = Header(None), x_user_role: str = Header("editor", alias="X-User-Role")):
    context = _auth(authorization, x_user_role, False)
    documents = []
    with _STORAGE_LOCK:
        for path in sorted(_principal_root(context).glob("*.json")):
            try:
                item = _load(path)
                documents.append(_summary(item, path.stem))
            except CleanroomException:
                continue
    # ``documents`` remains the old list-of-names field; summaries are additive.
    return {"documents": [item["name"] for item in documents], "document_summaries": documents,
            "data_status": "ok", "data_gaps": []}


@router.get("/documents/{workflow_id}")
def get_document(workflow_id: str, authorization: Optional[str] = Header(None), x_user_role: str = Header("editor", alias="X-User-Role")):
    context = _auth(authorization, x_user_role, False)
    with _STORAGE_LOCK:
        item = _load(_path(context, workflow_id))
    return {"workflow_id": workflow_id, "workflow": item, "document": _summary(item, workflow_id)["document"],
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
        try:
            expected = int(expected)
        except (TypeError, ValueError):
            raise CleanroomException(400, "INVALID_VERSION", "expected_version 必须为整数") from None
        current_version = int(current.get("version", 1))
        if expected != current_version:
            raise VersionConflictException(expected, current_version)
        payload = body.get("payload", body.get("workflow", body))
        name = body.get("name", current.get("name", "workflow"))
        document = _parse_payload(payload, body.get("source", current.get("source", "comfyui")), name)
        stored = document.as_dict()
        stored.update({"workflow_id": workflow_id, "version": current_version + 1,
                       "folder_id": body.get("folder_id", current.get("folder_id"))})
        if stored["folder_id"] is not None:
            _load_folder(context, str(stored["folder_id"]))
        _atomic_write(path, stored)
    return {"workflow_id": workflow_id, "version": current_version + 1, "workflow": stored,
            "document": _summary(stored, workflow_id)["document"], "data_status": "ok", "data_gaps": []}


@router.put("/documents/{workflow_id}")
async def update_document(workflow_id: str, request: Request, authorization: Optional[str] = Header(None), x_user_role: str = Header("editor", alias="X-User-Role")):
    return await _update_document(workflow_id, request, authorization, x_user_role)


@router.patch("/documents/{workflow_id}")
async def patch_document(workflow_id: str, request: Request, authorization: Optional[str] = Header(None), x_user_role: str = Header("editor", alias="X-User-Role")):
    return await _update_document(workflow_id, request, authorization, x_user_role)


@router.delete("/documents/{workflow_id}")
async def delete_document(workflow_id: str, request: Request, authorization: Optional[str] = Header(None), x_user_role: str = Header("editor", alias="X-User-Role")):
    context = _auth(authorization, x_user_role, True)
    body = await _json_body(request)
    if not isinstance(body, dict):
        raise CleanroomException(400, "INVALID_REQUEST", "请求体必须是 JSON 对象")
    with _STORAGE_LOCK:
        path = _path(context, workflow_id)
        current = _load(path)
        expected = body.get("expected_version", request.query_params.get("expected_version"))
        try:
            expected = int(expected)
        except (TypeError, ValueError):
            raise CleanroomException(400, "INVALID_VERSION", "expected_version 必须为整数") from None
        current_version = int(current.get("version", 1))
        if expected != current_version:
            raise VersionConflictException(expected, current_version)
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
        try:
            expected = int(expected)
        except (TypeError, ValueError):
            raise CleanroomException(400, "INVALID_VERSION", "expected_version 必须为整数") from None
        current_version = int(current.get("version", 1))
        if expected != current_version:
            raise VersionConflictException(expected, current_version)
        current["name"] = str(body.get("name", "")).strip() or current.get("name", "workflow")
        current["version"] = current_version + 1
        _atomic_write(path, current)
    return {"workflow_id": workflow_id, "version": current["version"], "workflow": current,
            "document": _summary(current, workflow_id)["document"], "data_status": "ok", "data_gaps": []}


@router.get("/diagnostics")
def diagnostics(authorization: Optional[str] = Header(None), x_user_role: str = Header("editor", alias="X-User-Role")):
    _auth(authorization, x_user_role, False)
    providers = {provider: {"configured": get_provider(provider).configured,
                           "status": "available" if get_provider(provider).configured else "unavailable"}
                 for provider in PROVIDERS}
    return {"data_status": "partial", "diagnostics": {"providers": providers,
            "executor": {"status": "unavailable", "code": "WORKFLOW_EXECUTOR_UNAVAILABLE"},
            "storage": {"status": "available"}}, "data_gaps": ["executor not configured"]}


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
    return await registry.store_upload(context, request)


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
    return FileResponse(path, media_type=media_type, headers={"Cache-Control": "private, no-store"})


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


@router.post("/open-in-comfy/{workflow_id}")
async def open_in_comfy(workflow_id: str, authorization: Optional[str] = Header(None), x_user_role: str = Header("editor", alias="X-User-Role")):
    _auth(authorization, x_user_role, True)
    get_document(workflow_id, authorization, x_user_role)
    raise unavailable("WORKFLOW_EXECUTOR_UNAVAILABLE", "ComfyUI bridge is not configured")


@router.get("/comfy-bridge/sync-status")
def comfy_sync_status(authorization: Optional[str] = Header(None), x_user_role: str = Header("editor", alias="X-User-Role")):
    _auth(authorization, x_user_role, False)
    return {"revisions": {}, "synced_now": [], "data_status": "empty", "data_gaps": ["ComfyUI bridge is not configured"]}


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
    """Best-effort ``(model_type, filename)`` pairs from node inputs/raw payload.

    Only values that genuinely look like a model filename are collected; nothing
    is invented when a node carries no such field.
    """
    models: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for node in document.get("nodes", []):
        for source in (node.get("inputs"), node.get("raw_payload")):
            if not isinstance(source, dict):
                continue
            for field_name, value in source.items():
                if not isinstance(value, str) or not _MODEL_SUFFIX.search(value):
                    continue
                pair = (str(field_name), value)
                if pair in seen:
                    continue
                seen.add(pair)
                models.append(pair)
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


def _compare_local_document(document: dict[str, Any]) -> dict[str, Any]:
    """Standardized local-comparison shape; ``local_online`` is never guessed."""
    class_ids = _node_class_ids(document)
    models = _referenced_models(document)
    configured = os.getenv("GW_COMFYUI_URL", "").strip()
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
    return _compare_local_document(document)


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
    node.setdefault("inputs", {})[field] = value if has_value else node.get("inputs", {}).get(field)

    widgets = node.get("widgets")
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
    for update in body.get("widget_updates", []):
        for node in nodes:
            if str(node.get("id")) == str(update.get("node_id")):
                _apply_widget_update(node, update)
    request._body = json.dumps({"workflow": current, "expected_version": body.get("expected_version", current.get("version", 1)), "source": current.get("source", "canvas")}).encode()
    return (await _update_document(workflow_id, request, authorization, x_user_role))["workflow"]


@router.put("/item/{workflow_id}/folder")
async def mature_move(workflow_id: str, request: Request, authorization: Optional[str] = Header(None), x_user_role: str = Header("editor", alias="X-User-Role")):
    return (await patch_document(workflow_id, request, authorization, x_user_role))["workflow"]


@router.post("/parse-link")
async def mature_parse_link(request: Request, authorization: Optional[str] = Header(None), x_user_role: str = Header("editor", alias="X-User-Role")):
    _auth(authorization, x_user_role, True)
    body = await _json_body(request)
    if not isinstance(body, dict) or not isinstance(body.get("url_or_text"), str):
        raise CleanroomException(400, "INVALID_PROVIDER_REFERENCE", "url_or_text is required")
    reference = body["url_or_text"].strip()
    if reference.startswith(("http://", "https://")):
        raise unavailable("WORKFLOW_PROVIDER_UNAVAILABLE", "Provider URL parsing is not configured")
    try:
        payload = json.loads(reference)
    except (TypeError, json.JSONDecodeError):
        raise CleanroomException(400, "INVALID_WORKFLOW", "url_or_text must contain workflow JSON") from None
    document = _parse_payload(payload, "canvas", "workflow")
    return document.as_dict()


@router.post("/parse-upload")
async def mature_parse_upload(request: Request, authorization: Optional[str] = Header(None), x_user_role: str = Header("editor", alias="X-User-Role")):
    return await upload(request, authorization, x_user_role)


@router.post("/execute")
async def mature_execute(request: Request, authorization: Optional[str] = Header(None), x_user_role: str = Header("editor", alias="X-User-Role")):
    return await execute_task(request, authorization, x_user_role)


def _promoted_params(document: dict[str, Any]) -> list[dict[str, Any]]:
    """Every promoted ``(node, field)`` pair, in stable document order."""
    params: list[dict[str, Any]] = []
    for node in document.get("nodes", []):
        node_id = str(node.get("id"))
        widgets = node.get("widgets")
        if not isinstance(widgets, list):
            continue
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
    payload: dict[str, Any] = {}
    for node in document.get("nodes", []):
        inputs = dict(node.get("inputs") or {})
        widgets = node.get("widgets")
        if isinstance(widgets, list):
            for widget in widgets:
                if isinstance(widget, dict) and widget.get("name") is not None:
                    inputs[str(widget["name"])] = widget.get("value")
        payload[str(node.get("id"))] = {"class_type": node.get("kind") or "node", "inputs": inputs}
    return payload


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
        if expected is not None:
            try:
                expected = int(expected)
            except (TypeError, ValueError):
                raise CleanroomException(400, "INVALID_VERSION", "expected_version 必须为整数") from None
            if expected != current_version:
                raise VersionConflictException(expected, current_version)
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
        _atomic_write(path, current)
    return {"workflow_id": workflow_id, "version": current["version"], "workflow": current,
            "document": _summary(current, workflow_id)["document"], "data_status": "ok", "data_gaps": []}


@router.get("/capabilities")
def capabilities(authorization: Optional[str] = Header(None), x_user_role: str = Header("editor", alias="X-User-Role")):
    _auth(authorization, x_user_role, False)
    providers = {provider: ("configured" if get_provider(provider).configured else "unavailable") for provider in PROVIDERS}
    return {"providers": providers,
            "implemented": ["parse", "upload-json", "upload-png-text", "provider-fetch-gateway", "task-contract", "canvas-contract", "canvas-export", "runtime-isolated-storage"],
            "partial": ["provider-fetch-gateway"],
            "todo": ["provider-specific authentication/response contracts", "workflow execution and polling", "canvas binary export"],
            "data_status": "partial", "data_gaps": ["executor not configured", "provider-specific contracts not admitted", "PNG parsing requires Pillow"]}
