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

"""Phase 12 素材注册表 API（真实落盘数据源）。

真实数据源：`gw.asset_registry.repository`（core.storage.JsonState 命名空间
`asset_registry`）+ 允许根目录内的真实本机文件 + ffmpeg/ffprobe 真实媒体处理。

安全口径：
- 认证先于参数校验，未认证一律 401；
- 本机文件访问仅限 `GW_ALLOWED_ROOTS`，未配置即 403 失败关闭，不回显原始绝对路径；
- 外部依赖（ffmpeg / 允许根目录 / 可读取媒体）缺失一律 503，禁止伪造数据。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Body, Depends, File, Form, Header, Query, Request, Response, status, UploadFile
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse

from gw.asset_registry import media_probe, repository as repo, index_jobs
from gw.asset_registry.service import (
    ASSET_REGISTRY_NOT_INTEGRATED,
    DATA_STATUS_NOT_INTEGRATED,
    DATA_STATUS_OK,
    default_asset_registry_service,
)
from gw.core.auth import require_authenticated, require_edit_access, require_governance_access
from gw.core.errors import CleanroomException
from gw.projects_hub.service import default_projects_service, owner_key_for_context

from gw.core.asset_visibility import asset_request_scope

router = APIRouter(prefix="/api/asset-registry", tags=["asset-registry"], dependencies=[Depends(asset_request_scope)])

MAX_IMPORT_BYTES = 64 * 1024 * 1024


def _read_auth(authorization: Optional[str], x_user_role: str):
    """统一读取权限检查。"""
    return require_authenticated(authorization, x_user_role)


def _edit_auth(authorization: Optional[str], x_user_role: str):
    """统一编辑权限检查。"""
    return require_edit_access(authorization, x_user_role)


def _governance_auth(authorization: Optional[str], x_user_role: str):
    """统一治理权限检查。"""
    return require_governance_access(authorization, x_user_role)


def _payload(payload: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    return payload if isinstance(payload, dict) else {}


def _ids(payload: Dict[str, Any], key: str = "asset_ids") -> List[str]:
    value = payload.get(key) or []
    return [str(item) for item in value] if isinstance(value, list) else []


def _version(payload: Dict[str, Any]) -> Optional[int]:
    raw = payload.get("expected_version")
    return int(raw) if raw is not None else None


async def _json_body(request: Request) -> Dict[str, Any]:
    """宽容读取 JSON 请求体；空体或非对象等价于空字典。"""
    try:
        payload = await request.json()
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _ascii_filename(name: str, fallback: str = "export") -> str:
    """HTTP 头只能承载 latin-1；中文名降级为 ASCII 回退名，原始名走 filename*。"""
    clean = "".join(ch for ch in (name or "") if ch not in '\\/:*?"<>|\r\n').strip()
    ascii_only = clean.encode("ascii", "ignore").decode("ascii").strip(" ._-")
    return ascii_only or fallback


def _content_disposition(name: str, fallback: str, suffix: str) -> str:
    """RFC 5987 编码附件名，避免中文名触发 latin-1 头编码错误。"""
    from urllib.parse import quote
    clean = "".join(ch for ch in (name or "") if ch not in '\\/:*?"<>|\r\n').strip() or fallback
    return "attachment; filename=\"%s%s\"; filename*=UTF-8''%s%s" % (
        _ascii_filename(clean, fallback), suffix, quote(clean, safe=""), suffix)


def _pdf_response(asset_ids: List[str], name: str) -> Response:
    """把素材清单导出为合法 PDF 字节流。"""
    content, exported = repo.export_pdf_bundle(asset_ids)
    return Response(
        content=content,
        media_type="application/pdf",
        headers={
            "Content-Disposition": _content_disposition(name, "export", ".pdf"),
            "X-PDF-Exported": str(exported),
            "X-PDF-Skipped": "0",
        },
    )


# ---------------------------------------------------------------------------
# 只读真值接口
# ---------------------------------------------------------------------------


@router.get("", summary="读取素材注册表总览", status_code=status.HTTP_200_OK)
def get_registry_root(
    authorization: Optional[str] = Header(None),
    x_user_role: str = Header("editor", alias="X-User-Role"),
):
    _read_auth(authorization, x_user_role)
    raw = repo.state().read()
    from gw.core.asset_visibility import asset_visible
    return {
        "registry": "asset-registry",
        "revision": raw["revision"],
        "assets": [dict(a) for a in raw["assets"].values() if asset_visible(a)],
        "features": repo.features_snapshot()["features"],
        "backend": "json-state",
        "data_status": DATA_STATUS_OK,
        "data_gaps": [],
    }


@router.get("/status", summary="读取素材注册表状态", status_code=status.HTTP_200_OK)
def get_registry_status(
    authorization: Optional[str] = Header(None),
    x_user_role: str = Header("editor", alias="X-User-Role"),
):
    actor = _read_auth(authorization, x_user_role)
    raw = repo.state().read()
    from gw.core.asset_visibility import asset_visible
    visible_count = sum(asset_visible(asset) for asset in raw['assets'].values())
    snapshot = repo.features_snapshot()
    return {
        "ready": True,
        "backend": "json-state",
        "revision": raw["revision"],
        "assets_count": visible_count,
        "jobs": index_jobs.service().listing(actor),
        "overview": {
            "assets": visible_count,
            "projects": len({e.get("project_id") for e in raw["project_entities"].values()}),
            "canvases": 0,
            "roots": [],
            "features": [
                {"id": key, "name": FEATURE_NAMES.get(key, key), "enabled": bool(value),
                 "data_status": DATA_STATUS_OK}
                for key, value in snapshot["features"].items()
            ],
            "index_automation": snapshot["index_automation"],
        },
        "features": [
            {"id": key, "name": FEATURE_NAMES.get(key, key), "enabled": bool(value)}
            for key, value in snapshot["features"].items()
        ],
        "data_status": DATA_STATUS_OK,
        "data_gaps": [],
    }


FEATURE_NAMES = {
    "thumbnail": "自动缩略图",
    "video_storyboard": "视频九宫格缩略图",
    "metadata": "元数据解析",
    "fulltext": "全文检索",
    "font": "字体识别",
    "model": "模型识别",
    "index_automation": "索引自动化",
}


@router.get("/assets", summary="分页读取素材登记", status_code=status.HTTP_200_OK)
def list_registry_assets(
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    cursor: Optional[str] = Query(None),
    sort: Optional[str] = Query(None),
    view: Optional[str] = Query(None),
    query: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
    project_id: Optional[str] = Query(None),
    kind: Optional[str] = Query(None),
    category: Optional[str] = Query(None),
    tag: Optional[str] = Query(None),
    include_tags: Optional[str] = Query(None),
    exclude_tags: Optional[str] = Query(None),
    root_id: Optional[str] = Query(None),
    collection_id: Optional[str] = Query(None),
    recent_days: Optional[int] = Query(None),
    archived: Optional[bool] = Query(None),
    authorization: Optional[str] = Header(None),
    x_user_role: str = Header("editor", alias="X-User-Role"),
):
    _read_auth(authorization, x_user_role)
    return repo.list_assets(limit, offset, query or search, kind, archived, cursor=cursor, sort=sort,
                            view=view, category=category, tag=tag, project_id=project_id,
                            include_tags=include_tags, exclude_tags=exclude_tags, root_id=root_id,
                            collection_id=collection_id, recent_days=recent_days)


@router.get("/assets/{asset_id}", summary="读取素材登记详情", status_code=status.HTTP_200_OK)
def get_registry_asset(
    asset_id: str,
    authorization: Optional[str] = Header(None),
    x_user_role: str = Header("editor", alias="X-User-Role"),
):
    _read_auth(authorization, x_user_role)
    return {"asset": repo.get_asset(asset_id), "data_status": DATA_STATUS_OK}


@router.get("/assets/{asset_id}/image-versions", summary="读取素材图片版本", status_code=status.HTTP_200_OK)
def list_image_versions(
    asset_id: str,
    limit: int = Query(500, ge=1, le=500),
    authorization: Optional[str] = Header(None),
    x_user_role: str = Header("editor", alias="X-User-Role"),
):
    _read_auth(authorization, x_user_role)
    return repo.list_image_versions(asset_id)


@router.get("/assets/{asset_id}/image-versions/{version_id}/media", summary="读取图片版本媒体", status_code=status.HTTP_200_OK)
def get_image_version_media(
    asset_id: str,
    version_id: str,
    authorization: Optional[str] = Header(None),
    x_user_role: str = Header("editor", alias="X-User-Role"),
):
    _read_auth(authorization, x_user_role)
    path = repo.media_file(asset_id, version_id)
    if path is None:
        repo._unavailable(
            "/api/asset-registry/assets/{asset_id}/image-versions/{version_id}/media",
            "MEDIA_NOT_AVAILABLE", "该版本没有可读取的媒体文件")
    return Response(content=path.read_bytes(), media_type=_media_type(path),
                    headers={"X-Media-Display": _header_safe(repo.storage.relative_display(path))})


@router.get("/assets/{asset_id}/media", summary="读取素材媒体", status_code=status.HTTP_200_OK)
def get_asset_media(
    asset_id: str,
    authorization: Optional[str] = Header(None),
    x_user_role: str = Header("editor", alias="X-User-Role"),
):
    context = _read_auth(authorization, x_user_role)
    path = repo.media_file(asset_id)
    if path is None:
        record = repo.get_asset(asset_id)
        if record.get("metadata", {}).get("workflow_owner"):
            from gw.god_workflow import registry as workflow_registry
            resolved = workflow_registry.resolve_asset_file(context, asset_id)
            if resolved:
                private_path, asset = resolved
                return FileResponse(private_path, media_type=asset["content_type"])
        repo._unavailable("/api/asset-registry/assets/{asset_id}/media",
                          "MEDIA_NOT_AVAILABLE", "该素材没有可读取的媒体文件")
    return Response(content=path.read_bytes(), media_type=_media_type(path),
                    headers={"X-Media-Display": _header_safe(repo.storage.relative_display(path))})


def _header_safe(value: str) -> str:
    """把展示串降级为 HTTP 头可承载的 latin-1 内容。"""
    return (value or "").encode("ascii", "replace").decode("ascii")


def _media_type(path) -> str:
    suffix = path.suffix.lower()
    return {
        ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
        ".gif": "image/gif", ".webp": "image/webp", ".bmp": "image/bmp",
        ".svg": "image/svg+xml", ".mp4": "video/mp4", ".webm": "video/webm",
        ".mov": "video/quicktime", ".mp3": "audio/mpeg", ".wav": "audio/wav",
        ".pdf": "application/pdf", ".txt": "text/plain; charset=utf-8",
    }.get(suffix, "application/octet-stream")


@router.get("/assets/{asset_id}/video/frame", summary="读取视频帧", status_code=status.HTTP_200_OK)
def get_video_frame(
    asset_id: str,
    at: Optional[float] = Query(None, ge=0),
    authorization: Optional[str] = Header(None),
    x_user_role: str = Header("editor", alias="X-User-Role"),
):
    _read_auth(authorization, x_user_role)
    return media_probe.extract_frame(asset_id, float(at or 0))


@router.get("/assets/{asset_id}/video/storyboard", summary="读取视频分镜", status_code=status.HTTP_200_OK)
def get_video_storyboard(
    asset_id: str,
    version: Optional[str] = Query(None),
    authorization: Optional[str] = Header(None),
    x_user_role: str = Header("editor", alias="X-User-Role"),
):
    _read_auth(authorization, x_user_role)
    return media_probe.storyboard(asset_id, version)


@router.get("/facets", summary="读取素材筛选维度", status_code=status.HTTP_200_OK)
def get_registry_facets(
    authorization: Optional[str] = Header(None),
    x_user_role: str = Header("editor", alias="X-User-Role"),
):
    _read_auth(authorization, x_user_role)
    return repo.facets()


@router.get("/folders", summary="读取素材文件夹登记", status_code=status.HTTP_200_OK)
def get_registry_folders(
    root_id: Optional[str] = Query(None),
    authorization: Optional[str] = Header(None),
    x_user_role: str = Header("editor", alias="X-User-Role"),
):
    _read_auth(authorization, x_user_role)
    return repo.folders(root_id)


@router.get("/governance/overview", summary="读取治理回收概览", status_code=status.HTTP_200_OK)
def get_governance_overview(
    authorization: Optional[str] = Header(None),
    x_user_role: str = Header("editor", alias="X-User-Role"),
):
    _governance_auth(authorization, x_user_role)
    return repo.governance_overview()


@router.get("/governance/cascade-preview", summary="读取级联影响预览", status_code=status.HTTP_200_OK)
def get_cascade_preview(
    target_type: str = Query(..., min_length=1),
    target_id: str = Query(..., min_length=1),
    authorization: Optional[str] = Header(None),
    x_user_role: str = Header("editor", alias="X-User-Role"),
):
    _governance_auth(authorization, x_user_role)
    if target_type != "asset":
        # 画布/项目域不在本块范围：如实返回空影响集，不伪造跨域关系。
        return {"target_type": target_type, "target_id": target_id,
                "impact": {"assets": [], "projects": [], "canvases": [], "total": 0},
                "data_status": DATA_STATUS_NOT_INTEGRATED,
                "data_gaps": ["governance_store_not_connected"]}
    result = repo.cascade_preview(target_id)
    return {
        "target_type": target_type, "target_id": target_id,
        "impact": {"assets": [target_id], "projects": result["projects"],
                   "canvases": [], "total": len(result["projects"]) + 1},
        "data_status": DATA_STATUS_OK, "data_gaps": [],
    }


@router.get("/preferences/team", summary="读取团队素材偏好", status_code=status.HTTP_200_OK)
def get_team_preferences(
    authorization: Optional[str] = Header(None),
    x_user_role: str = Header("editor", alias="X-User-Role"),
):
    _read_auth(authorization, x_user_role)
    return repo.team_preferences()


@router.get("/presets", summary="读取素材预设", status_code=status.HTTP_200_OK)
def get_registry_presets(
    authorization: Optional[str] = Header(None),
    x_user_role: str = Header("editor", alias="X-User-Role"),
):
    _read_auth(authorization, x_user_role)
    return repo.list_presets()


@router.get("/project-directory-templates", summary="读取项目目录模板", status_code=status.HTTP_200_OK)
def get_project_directory_templates(
    project_type: Optional[str] = Query(None),
    authorization: Optional[str] = Header(None),
    x_user_role: str = Header("editor", alias="X-User-Role"),
):
    _read_auth(authorization, x_user_role)
    result = repo.list_directory_templates(project_type)
    return {**result, "templates": [_template_view(item, {}) for item in result["templates"]]}


@router.get("/recycle-bin", summary="读取素材回收站", status_code=status.HTTP_200_OK)
def get_recycle_bin(
    authorization: Optional[str] = Header(None),
    x_user_role: str = Header("editor", alias="X-User-Role"),
):
    _read_auth(authorization, x_user_role)
    raw = repo.state().read()
    from gw.core.asset_visibility import asset_visible
    visible = [dict(value) for value in raw['recycle_bin'].values() if value.get('kind') != 'asset' or asset_visible(value.get('payload', {}))]
    return {"items": visible,
            "assets": [value for value in visible if value.get("kind") == "asset"],
            "projects": [], "canvases": [],
            "revision": raw["revision"], "data_status": DATA_STATUS_OK, "data_gaps": []}


@router.get("/remote-assets", summary="读取远程素材登记", status_code=status.HTTP_200_OK)
def get_remote_assets(
    authorization: Optional[str] = Header(None),
    x_user_role: str = Header("editor", alias="X-User-Role"),
):
    _read_auth(authorization, x_user_role)
    return repo.list_remote_assets()


@router.get("/workspace-jobs/{job_id}", summary="读取工作区任务", status_code=status.HTTP_200_OK)
def get_workspace_job(
    job_id: str,
    authorization: Optional[str] = Header(None),
    x_user_role: str = Header("editor", alias="X-User-Role"),
):
    actor = _read_auth(authorization, x_user_role)
    return index_jobs.service().detail(job_id, actor)

# ---------------------------------------------------------------------------
# 写入端点：真实落盘，写后可读回
# ---------------------------------------------------------------------------


@router.post("/assets/import", summary="导入素材登记", status_code=status.HTTP_200_OK)
async def import_registry_assets(
    request: Request,
    authorization: Optional[str] = Header(None),
    x_user_role: str = Header("editor", alias="X-User-Role"),
):
    """导入素材：JSON 批量登记，或 multipart 单文件真实落盘到数据目录。

    真实文件写入数据目录（不是素材目录），文件名由服务端生成，不信任调用方路径。
    """
    _edit_auth(authorization, x_user_role)
    content_type = (request.headers.get("content-type") or "").lower()
    if "multipart/form-data" in content_type:
        form = await request.form()
        upload = form.get("file")
        if upload is None or not hasattr(upload, "read"):
            raise CleanroomException(400, "INVALID_REQUEST", "缺少 file 上传字段")
        data = await upload.read()
        if not data:
            raise CleanroomException(400, "INVALID_REQUEST", "上传文件为空")
        if len(data) > MAX_IMPORT_BYTES:
            raise CleanroomException(413, "PAYLOAD_TOO_LARGE", "上传文件超过 64MB 上限")
        name = str(form.get("name") or getattr(upload, "filename", "") or "未命名素材").strip()
        result = repo.import_assets([{"name": name, "kind": _kind_from_name(name),
                                      "size_bytes": len(data)}], "upload", None)
        asset_id = result["asset_ids"][0]
        target = repo.stored_upload_path(asset_id, name)

        def mutate(raw: Dict[str, Any]) -> Any:
            item = raw["assets"][asset_id]
            item["display_path"] = None
            item["stored_name"] = target.name
            item["size_bytes"] = len(data)
            return raw
        repo.state().mutate(mutate)
        target.write_bytes(data)
        asset = repo.get_asset(asset_id)
        return {"asset": asset, "assets": [asset], "asset_ids": [asset_id],
                "revision": repo.state().read()["revision"],
                "data_status": DATA_STATUS_OK, "data_gaps": []}
    payload = await _json_body(request)
    items = payload.get("items")
    if not isinstance(items, list) or not items:
        if payload.get("name"):
            items = [payload]
        else:
            raise CleanroomException(400, "INVALID_REQUEST", "缺少 items 或 name")
    return repo.import_assets(items, str(payload.get("source_kind") or "manual"), _version(payload))


def _kind_from_name(name: str) -> str:
    """按扩展名判定素材种类；未命中即为 unknown，不猜测。"""
    suffix = "." + name.rsplit(".", 1)[-1].lower() if "." in name else ""
    if suffix in {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".svg", ".tif", ".tiff"}:
        return "image"
    if suffix in {".mp4", ".mov", ".mkv", ".avi", ".webm", ".m4v", ".flv", ".wmv"}:
        return "video"
    if suffix in {".mp3", ".wav", ".flac", ".aac", ".ogg", ".m4a"}:
        return "audio"
    if suffix in {".txt", ".md", ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".csv"}:
        return "document"
    return "unknown"


@router.post("/assets/archive", summary="打包下载所选素材", status_code=status.HTTP_200_OK)
def archive_registry_assets(
    payload: Optional[Dict[str, Any]] = Body(None),
    authorization: Optional[str] = Header(None),
    x_user_role: str = Header("editor", alias="X-User-Role"),
):
    """前端「打包下载」入口：把所选素材的真实本地文件打包为 zip。

    无任何可打包文件时 503（不返回空假包）；越界/未配置根目录同样失败关闭。
    """
    _read_auth(authorization, x_user_role)
    body = _payload(payload)
    asset_ids = _ids(body)
    if not asset_ids:
        raise CleanroomException(400, "INVALID_REQUEST", "缺少 asset_ids")
    content = repo.export_archive(asset_ids)
    name = str(body.get("name") or "assets")
    return Response(content=content, media_type="application/zip",
                    headers={"Content-Disposition": _content_disposition(name, "assets", ".zip"),
                             "X-Archive-Filename": _ascii_filename(name, "assets") + ".zip"})


@router.post("/assets/export-pdf", summary="导出素材 PDF", status_code=status.HTTP_200_OK)
def export_registry_pdf(
    payload: Optional[Dict[str, Any]] = Body(None),
    authorization: Optional[str] = Header(None),
    x_user_role: str = Header("editor", alias="X-User-Role"),
):
    _read_auth(authorization, x_user_role)
    body = _payload(payload)
    return _pdf_response(_ids(body), str(body.get("name") or "资产导出"))


@router.post("/assets/archive-bundle", include_in_schema=False)
def export_registry_archive(
    payload: Optional[Dict[str, Any]] = Body(None),
    authorization: Optional[str] = Header(None),
    x_user_role: str = Header("editor", alias="X-User-Role"),
):
    """把所选素材的本地文件真实打包为 zip（无可用文件即 503）。"""
    _read_auth(authorization, x_user_role)
    body = _payload(payload)
    content = repo.export_archive(_ids(body))
    return Response(content=content, media_type="application/zip",
                    headers={"Content-Disposition": 'attachment; filename="assets.zip"'})


@router.post("/assets/relations", summary="建立素材关系", status_code=status.HTTP_200_OK)
def create_asset_relation(
    payload: Optional[Dict[str, Any]] = Body(None),
    authorization: Optional[str] = Header(None),
    x_user_role: str = Header("editor", alias="X-User-Role"),
):
    _edit_auth(authorization, x_user_role)
    body = _payload(payload)
    asset_ids = _ids(body)
    if len(asset_ids) >= 2:
        return repo.related_assets(asset_ids, str(body.get("relation_type") or body.get("kind") or "related"),
                                   _version(body))
    from_asset_id = str(body.get("from_asset_id") or "")
    to_asset_id = str(body.get("to_asset_id") or "")
    if not from_asset_id or not to_asset_id:
        raise CleanroomException(400, "INVALID_REQUEST", "缺少 asset_ids 或 from_asset_id/to_asset_id")
    return repo.create_relation(from_asset_id, to_asset_id,
                                str(body.get("relation_type") or body.get("kind") or "related"),
                                _version(body))


@router.post("/assets/resolve-reference", summary="解析素材引用", status_code=status.HTTP_200_OK)
def resolve_asset_reference(
    payload: Optional[Dict[str, Any]] = Body(None),
    authorization: Optional[str] = Header(None),
    x_user_role: str = Header("editor", alias="X-User-Role"),
):
    _read_auth(authorization, x_user_role)
    body = _payload(payload)
    reference = str(body.get("asset_id") or "")
    if not reference:
        raise CleanroomException(400, "INVALID_REQUEST", "缺少 asset_id")
    return repo.resolve_reference(reference)


@router.post("/assets/tags", summary="批量添加标签", status_code=status.HTTP_200_OK)
def add_asset_tags(
    payload: Optional[Dict[str, Any]] = Body(None),
    authorization: Optional[str] = Header(None),
    x_user_role: str = Header("editor", alias="X-User-Role"),
):
    _edit_auth(authorization, x_user_role)
    body = _payload(payload)
    names = body.get("names") or body.get("tags") or []
    names = [str(n) for n in names] if isinstance(names, list) else []
    result = repo.add_tags(_ids(body), names, _version(body))
    return {**result, "assets": repo.assets_snapshot(_ids(body))}


@router.patch("/assets/{asset_id}", summary="更新素材登记", status_code=status.HTTP_200_OK)
def update_registry_asset(
    asset_id: str,
    payload: Optional[Dict[str, Any]] = Body(None),
    authorization: Optional[str] = Header(None),
    x_user_role: str = Header("editor", alias="X-User-Role"),
):
    _edit_auth(authorization, x_user_role)
    return repo.update_asset(asset_id, _payload(payload))


@router.delete("/assets/{asset_id}", summary="删除素材（移入回收站）", status_code=status.HTTP_200_OK)
def delete_registry_asset(
    asset_id: str,
    authorization: Optional[str] = Header(None),
    x_user_role: str = Header("editor", alias="X-User-Role"),
):
    context = _edit_auth(authorization, x_user_role)
    return repo.delete_asset(asset_id, None, context)


@router.post("/assets/{asset_id}/image-versions", summary="创建图片版本", status_code=status.HTTP_200_OK)
def create_image_version(
    asset_id: str,
    payload: Optional[Dict[str, Any]] = Body(None),
    authorization: Optional[str] = Header(None),
    x_user_role: str = Header("editor", alias="X-User-Role"),
):
    _edit_auth(authorization, x_user_role)
    body = _payload(payload)
    return repo.create_image_version(
        asset_id, str(body.get("source_path") or ""), _version(body),
        edit=body.get("edit") if isinstance(body.get("edit"), dict) else None,
        canvas_id=str(body.get("canvas_id") or "") or None,
    )


@router.patch("/assets/{asset_id}/image-versions/{version_id}", summary="更新图片版本", status_code=status.HTTP_200_OK)
def update_image_version(
    asset_id: str,
    version_id: str,
    payload: Optional[Dict[str, Any]] = Body(None),
    authorization: Optional[str] = Header(None),
    x_user_role: str = Header("editor", alias="X-User-Role"),
):
    _edit_auth(authorization, x_user_role)
    body = _payload(payload)
    hidden = body.get("hidden")
    return repo.update_image_version(asset_id, version_id, body.get("label"),
                                     _version(body), None if hidden is None else bool(hidden))


@router.delete("/assets/{asset_id}/image-versions/{version_id}", summary="删除图片版本", status_code=status.HTTP_200_OK)
def delete_image_version(
    asset_id: str,
    version_id: str,
    authorization: Optional[str] = Header(None),
    x_user_role: str = Header("editor", alias="X-User-Role"),
):
    _edit_auth(authorization, x_user_role)
    return repo.delete_image_version(asset_id, version_id)


@router.post("/assets/{asset_id}/open-local", summary="本机打开前路径准入校验", status_code=status.HTTP_200_OK)
def open_asset_local(
    asset_id: str,
    authorization: Optional[str] = Header(None),
    x_user_role: str = Header("editor", alias="X-User-Role"),
):
    """只做路径准入校验，不启动任何外部程序（不调用 explorer/start/subprocess）。"""
    _edit_auth(authorization, x_user_role)
    return repo.open_local(asset_id)


@router.delete("/assets/{asset_id}/relations/{related_asset_id}", summary="删除素材关系", status_code=status.HTTP_200_OK)
def delete_asset_relation(
    asset_id: str,
    related_asset_id: str,
    authorization: Optional[str] = Header(None),
    x_user_role: str = Header("editor", alias="X-User-Role"),
):
    _edit_auth(authorization, x_user_role)
    return repo.delete_relation(asset_id, related_asset_id)


@router.delete("/assets/{asset_id}/tags/{tag_id}", summary="删除素材标签", status_code=status.HTTP_200_OK)
def delete_asset_tag(
    asset_id: str,
    tag_id: str,
    authorization: Optional[str] = Header(None),
    x_user_role: str = Header("editor", alias="X-User-Role"),
):
    _edit_auth(authorization, x_user_role)
    result = repo.delete_tag(asset_id, tag_id)
    asset = repo.get_asset(asset_id)
    return {**result, "asset": asset, "assets": [asset]}


@router.post("/assets/{asset_id}/video/clip", summary="真实剪辑视频片段", status_code=status.HTTP_200_OK)
def create_video_clip(
    asset_id: str,
    payload: Optional[Dict[str, Any]] = Body(None),
    authorization: Optional[str] = Header(None),
    x_user_role: str = Header("editor", alias="X-User-Role"),
):
    _edit_auth(authorization, x_user_role)
    body = _payload(payload)
    start = body.get("start_seconds", body.get("start", 0)) or 0
    end = body.get("end_seconds", body.get("end"))
    if end is None:
        raise CleanroomException(400, "INVALID_REQUEST", "缺少 end_seconds")
    return media_probe.clip(asset_id, float(start), float(end), _version(body))


@router.post("/folders", summary="创建素材文件夹", status_code=status.HTTP_200_OK)
def create_registry_folder(
    payload: Optional[Dict[str, Any]] = Body(None),
    authorization: Optional[str] = Header(None),
    x_user_role: str = Header("editor", alias="X-User-Role"),
):
    _edit_auth(authorization, x_user_role)
    body = _payload(payload)
    result = repo.create_folder(str(body.get("name") or ""), body.get("parent"), _version(body))
    return {**result, "folders": repo.folders(body.get("root_id"))["folders"]}

# ---------------------------------------------------------------------------
# 治理 / 索引
# ---------------------------------------------------------------------------


@router.post("/governance/asset-trash/{entry_id}/restore", summary="恢复回收站条目", status_code=status.HTTP_200_OK)
def restore_asset_trash(
    entry_id: str,
    payload: Optional[Dict[str, Any]] = Body(None),
    authorization: Optional[str] = Header(None),
    x_user_role: str = Header("editor", alias="X-User-Role"),
):
    context = _governance_auth(authorization, x_user_role)
    return repo.restore_asset_trash(entry_id, context)


@router.post("/governance/assets/{asset_id}/restore", summary="恢复已归档素材", status_code=status.HTTP_200_OK)
def restore_asset(
    asset_id: str,
    payload: Optional[Dict[str, Any]] = Body(None),
    authorization: Optional[str] = Header(None),
    x_user_role: str = Header("editor", alias="X-User-Role"),
):
    context = _governance_auth(authorization, x_user_role)
    return repo.restore_asset(asset_id, context)


@router.post("/governance/audit-outbox/reconcile", summary="重放审计 outbox", status_code=status.HTTP_200_OK)
def reconcile_audit_outbox(
    authorization: Optional[str] = Header(None),
    x_user_role: str = Header("editor", alias="X-User-Role"),
):
    _governance_auth(authorization, x_user_role)
    return repo.reconcile_audit_outbox()


@router.post("/governance/canvases/purge-expired", summary="清理过期画布（记录式）", status_code=status.HTTP_200_OK)
def purge_expired_canvases(
    payload: Optional[Dict[str, Any]] = Body(None),
    authorization: Optional[str] = Header(None),
    x_user_role: str = Header("editor", alias="X-User-Role"),
):
    """画布域不在本块范围：只记录真实操作条目，不伪造画布状态或删除画布。"""
    _governance_auth(authorization, x_user_role)
    body = _payload(payload)
    days = int(body.get("retention_days") or 30)
    result = repo.purge_expired_canvases(days)
    return {**result, "data_gaps": ["canvas_store_not_connected"]}


@router.post("/governance/canvases/{canvas_id}/restore", summary="恢复画布（记录式）", status_code=status.HTTP_200_OK)
def restore_canvas(
    canvas_id: str,
    payload: Optional[Dict[str, Any]] = Body(None),
    authorization: Optional[str] = Header(None),
    x_user_role: str = Header("editor", alias="X-User-Role"),
):
    """画布域不在本块范围：只记录真实操作条目，不伪造画布状态。"""
    _governance_auth(authorization, x_user_role)
    result = repo.restore_canvas(canvas_id)
    return {**result, "data_gaps": ["canvas_store_not_connected"]}


@router.post("/governance/operations", summary="登记治理操作", status_code=status.HTTP_200_OK)
def execute_governance_operation(
    payload: Optional[Dict[str, Any]] = Body(None),
    authorization: Optional[str] = Header(None),
    x_user_role: str = Header("editor", alias="X-User-Role"),
):
    _governance_auth(authorization, x_user_role)
    body = _payload(payload)
    name = str(body.get("operation") or body.get("name") or "").strip()
    if not name:
        raise CleanroomException(400, "INVALID_REQUEST", "缺少 operation")
    return repo.record_operation(name, body)


@router.post("/index/sync", summary="同步素材索引统计", status_code=status.HTTP_200_OK)
def sync_asset_index(
    idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key"),
    payload: Optional[Dict[str, Any]] = Body(None),
    authorization: Optional[str] = Header(None),
    x_user_role: str = Header("editor", alias="X-User-Role"),
):
    actor = _edit_auth(authorization, x_user_role)
    body = _payload(payload)
    if body.get("background") is True:
        return JSONResponse(index_jobs.service().submit("sync", body, actor, idempotency_key), status_code=202)
    return repo.index_sync()


@router.post("/reindex", summary="重建素材索引", status_code=status.HTTP_200_OK)
def reindex_registry(
    idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key"),
    payload: Optional[Dict[str, Any]] = Body(None),
    authorization: Optional[str] = Header(None),
    x_user_role: str = Header("editor", alias="X-User-Role"),
):
    actor = _edit_auth(authorization, x_user_role)
    body = _payload(payload)
    if body.get("background") is True:
        return JSONResponse(index_jobs.service().submit("reindex", body, actor, idempotency_key), status_code=202)
    return repo.reindex(_version(_payload(payload)))


# ---------------------------------------------------------------------------
# 配置：偏好 / 能力开关 / 索引自动化
# ---------------------------------------------------------------------------


@router.patch("/preferences/team", summary="更新团队素材偏好", status_code=status.HTTP_200_OK)
def update_team_preferences(
    payload: Optional[Dict[str, Any]] = Body(None),
    authorization: Optional[str] = Header(None),
    x_user_role: str = Header("editor", alias="X-User-Role"),
):
    _edit_auth(authorization, x_user_role)
    return repo.update_team_preferences(_payload(payload))


@router.patch("/settings/features/{feature_id}", summary="更新素材能力开关", status_code=status.HTTP_200_OK)
def update_registry_feature(
    feature_id: str,
    payload: Optional[Dict[str, Any]] = Body(None),
    authorization: Optional[str] = Header(None),
    x_user_role: str = Header("editor", alias="X-User-Role"),
):
    _edit_auth(authorization, x_user_role)
    body = _payload(payload)
    result = repo.update_feature(feature_id, bool(body.get("enabled")))
    snapshot = repo.features_snapshot()
    feature = {"id": feature_id, "name": FEATURE_NAMES.get(feature_id, feature_id),
               "enabled": result["enabled"], "data_status": DATA_STATUS_OK}
    return {**result, "feature": feature,
            "features": [{"id": key, "name": FEATURE_NAMES.get(key, key), "enabled": bool(value)}
                         for key, value in snapshot["features"].items()]}


@router.patch("/settings/index-automation", summary="更新索引自动化配置", status_code=status.HTTP_200_OK)
def update_index_automation(
    payload: Optional[Dict[str, Any]] = Body(None),
    authorization: Optional[str] = Header(None),
    x_user_role: str = Header("editor", alias="X-User-Role"),
):
    _edit_auth(authorization, x_user_role)
    body = _payload(payload)
    interval = body.get("interval_minutes")
    return repo.update_index_automation(
        None if body.get("enabled") is None else bool(body.get("enabled")),
        None if interval is None else int(interval), _version(body))


# ---------------------------------------------------------------------------
# 预设 / 目录模板
# ---------------------------------------------------------------------------


@router.post("/presets", summary="创建素材预设", status_code=status.HTTP_200_OK)
def create_registry_preset(
    payload: Optional[Dict[str, Any]] = Body(None),
    authorization: Optional[str] = Header(None),
    x_user_role: str = Header("editor", alias="X-User-Role"),
):
    _edit_auth(authorization, x_user_role)
    body = _payload(payload)
    result = repo.create_preset(str(body.get("name") or ""), body.get("definition") or body.get("payload") or {},
                                _version(body))
    preset = dict(result["preset"])
    preset["id"] = preset["preset_id"]
    return {**result, "preset": preset}


@router.delete("/presets/{preset_id}", summary="删除素材预设", status_code=status.HTTP_200_OK)
def delete_registry_preset(
    preset_id: str,
    authorization: Optional[str] = Header(None),
    x_user_role: str = Header("editor", alias="X-User-Role"),
):
    _edit_auth(authorization, x_user_role)
    return repo.delete_preset(preset_id)


@router.post("/project-directory-templates", summary="创建项目目录模板", status_code=status.HTTP_200_OK)
def create_directory_template(
    payload: Optional[Dict[str, Any]] = Body(None),
    authorization: Optional[str] = Header(None),
    x_user_role: str = Header("editor", alias="X-User-Role"),
):
    _edit_auth(authorization, x_user_role)
    body = _payload(payload)
    result = repo.create_directory_template(body)
    return {**result, "template": _template_view(result["template"], body)}


@router.patch("/project-directory-templates/{template_id}", summary="更新项目目录模板", status_code=status.HTTP_200_OK)
def update_directory_template(
    template_id: str,
    payload: Optional[Dict[str, Any]] = Body(None),
    authorization: Optional[str] = Header(None),
    x_user_role: str = Header("editor", alias="X-User-Role"),
):
    _edit_auth(authorization, x_user_role)
    body = _payload(payload)
    result = repo.update_directory_template(template_id, body)
    return {**result, "template": _template_view(result["template"], body)}


@router.post("/project-directory-templates/{template_id}/archive", summary="归档项目目录模板", status_code=status.HTTP_200_OK)
def archive_directory_template(
    template_id: str,
    payload: Optional[Dict[str, Any]] = Body(None),
    authorization: Optional[str] = Header(None),
    x_user_role: str = Header("editor", alias="X-User-Role"),
):
    _edit_auth(authorization, x_user_role)
    body = _payload(payload)
    result = repo.archive_directory_template(template_id, body.get("archived", True), body.get("expected_version"))
    return {**result, "template": _template_view(result["template"], body)}


@router.post("/project-directory-templates/{template_id}/default", summary="设为默认目录模板", status_code=status.HTTP_200_OK)
def set_default_directory_template(
    template_id: str,
    payload: Optional[Dict[str, Any]] = Body(None),
    authorization: Optional[str] = Header(None),
    x_user_role: str = Header("editor", alias="X-User-Role"),
):
    _edit_auth(authorization, x_user_role)
    body = _payload(payload)
    result = repo.set_default_directory_template(template_id, body.get("expected_version"))
    return {**result, "template": _template_view(result["template"], body)}


def _template_view(record: Dict[str, Any], request_body: Dict[str, Any]) -> Dict[str, Any]:
    """目录模板对外视图：补齐前端使用的字段名，不改变落盘结构。"""
    view = dict(record)
    view["id"] = record["template_id"]
    folders = list(record.get("folders") or [])
    view["directory_tree"] = folders
    view["folder_count"] = len(folders)
    view["is_archived"] = bool(record.get("archived"))
    view["version"] = int(record.get("version", 1))
    view["slot_mapping"] = record.get("slot_mapping") or {}
    view["usage_count"] = int(record.get("usage_count") or 0)
    return view

# ---------------------------------------------------------------------------
# 项目实体 / 项目门 / 项目回收
# ---------------------------------------------------------------------------


@router.post("/projects/{project_id}/entities", summary="创建项目实体", status_code=status.HTTP_200_OK)
def create_project_entity(
    project_id: str,
    payload: Optional[Dict[str, Any]] = Body(None),
    authorization: Optional[str] = Header(None),
    x_user_role: str = Header("editor", alias="X-User-Role"),
):
    _edit_auth(authorization, x_user_role)
    body = _payload(payload)
    result = repo.create_project_entity(project_id, str(body.get("name") or ""),
                                        str(body.get("entity_type") or "generic"), body)
    return {**result, "entity": _entity_view(result["entity"])}


@router.patch("/project-entities/{entity_id}", summary="更新项目实体", status_code=status.HTTP_200_OK)
def update_project_entity(
    entity_id: str,
    payload: Optional[Dict[str, Any]] = Body(None),
    authorization: Optional[str] = Header(None),
    x_user_role: str = Header("editor", alias="X-User-Role"),
):
    _edit_auth(authorization, x_user_role)
    body = _payload(payload)
    if body.get("state") is not None and body.get("status") is None:
        body["status"] = body["state"]
    result = repo.update_project_entity(entity_id, body)
    return {**result, "entity": _entity_view(result["entity"])}


@router.post("/projects/{project_id}/assets", summary="关联项目素材", status_code=status.HTTP_200_OK)
def link_project_assets(
    project_id: str,
    payload: Optional[Dict[str, Any]] = Body(None),
    authorization: Optional[str] = Header(None),
    x_user_role: str = Header("editor", alias="X-User-Role"),
):
    _edit_auth(authorization, x_user_role)
    body = _payload(payload)
    asset_ids = _ids(body) or ([str(body["asset_id"])] if body.get("asset_id") else [])
    if not asset_ids:
        raise CleanroomException(400, "INVALID_REQUEST", "缺少 asset_ids")
    result = repo.link_project_assets(project_id, asset_ids, body)
    return {**result, "assets": repo.assets_snapshot(asset_ids)}


@router.get("/projects/{project_id}/gates", summary="按权限发现项目阶段门", status_code=status.HTTP_200_OK)
def list_project_gates(
    project_id: str,
    authorization: Optional[str] = Header(None),
    x_user_role: str = Header("editor", alias="X-User-Role"),
):
    """owner与治理角色可读；其他已认证主体只得到空集合，不泄露门或owner。"""
    context = _read_auth(authorization, x_user_role)
    gates = repo.project_gates(
        project_id,
        owner_key=owner_key_for_context(context),
        role=context.role,
    )["gates"]
    return {"project_id": project_id, "gates": gates, "data_status": DATA_STATUS_OK}


@router.get("/project-gates/{gate_id}", summary="读取项目阶段门", status_code=status.HTTP_200_OK)
def get_project_gate(
    gate_id: str,
    authorization: Optional[str] = Header(None),
    x_user_role: str = Header("editor", alias="X-User-Role"),
):
    """详情对无权主体隐藏为同一404。"""
    context = _read_auth(authorization, x_user_role)
    result = repo.get_project_gate(
        gate_id,
        owner_key=owner_key_for_context(context),
        role=context.role,
    )
    return {"gate": result["gate"], "data_status": DATA_STATUS_OK}


@router.patch("/project-gates/{gate_id}", summary="更新项目门", status_code=status.HTTP_200_OK)
def update_project_gate(
    gate_id: str,
    payload: Optional[Dict[str, Any]] = Body(None),
    authorization: Optional[str] = Header(None),
    x_user_role: str = Header("editor", alias="X-User-Role"),
):
    """owner或治理角色按门自身版本执行状态迁移；项目生命周期屏障在域锁内校验。"""
    context = _read_auth(authorization, x_user_role)
    body = _payload(payload)
    if body.get("status") is not None and body.get("state") is None:
        body["state"] = body["status"]
    result = repo.update_project_gate(
        gate_id,
        body,
        owner_key=owner_key_for_context(context),
        role=context.role,
    )
    return {"gate": result["gate"], "data_status": DATA_STATUS_OK}

@router.post("/project-recycle/{project_id}/restore", summary="恢复回收站项目（CAS）", status_code=status.HTTP_200_OK)
def restore_project_recycle(
    project_id: str,
    payload: Optional[Dict[str, Any]] = Body(None),
    authorization: Optional[str] = Header(None),
    x_user_role: str = Header("editor", alias="X-User-Role"),
):
    """治理权限下接入项目中心恢复状态机，未知项目404、过期版本409。"""
    _governance_auth(authorization, x_user_role)
    return repo.restore_project_recycle(project_id, _payload(payload).get("expected_version"))


@router.post("/recycle-bin/{entry_id}/restore", summary="恢复回收站条目", status_code=status.HTTP_200_OK)
def restore_recycle_entry(
    entry_id: str,
    authorization: Optional[str] = Header(None),
    x_user_role: str = Header("editor", alias="X-User-Role"),
):
    context = _governance_auth(authorization, x_user_role)
    return repo.restore_from_recycle(entry_id, context)


def _entity_view(record: Dict[str, Any]) -> Dict[str, Any]:
    view = dict(record)
    view["id"] = record["entity_id"]
    view["status"] = record.get("status") or record.get("state") or "pending"
    view["code"] = record.get("code") or record["entity_id"]
    return view


# ---------------------------------------------------------------------------
# 远程素材 / 工作区任务
# ---------------------------------------------------------------------------


@router.post("/remote-assets", summary="登记远程素材", status_code=status.HTTP_200_OK)
def create_remote_asset(
    payload: Optional[Dict[str, Any]] = Body(None),
    authorization: Optional[str] = Header(None),
    x_user_role: str = Header("editor", alias="X-User-Role"),
):
    _edit_auth(authorization, x_user_role)
    body = _payload(payload)
    url = str(body.get("url") or "").strip()
    if not url:
        raise CleanroomException(400, "INVALID_REQUEST", "缺少 url")
    result = repo.create_remote_asset(url, str(body.get("name") or ""), _version(body))
    record = dict(result["remote_asset"])
    record["id"] = record["asset_id"]
    record["kind"] = str(body.get("kind") or "remote")
    return {**result, "remote_asset": record, "assets": [record]}


@router.delete("/remote-assets/{asset_id}", summary="删除远程素材", status_code=status.HTTP_200_OK)
def delete_remote_asset(
    asset_id: str,
    authorization: Optional[str] = Header(None),
    x_user_role: str = Header("editor", alias="X-User-Role"),
):
    _edit_auth(authorization, x_user_role)
    return repo.delete_remote_asset(asset_id)


@router.post("/workspace-jobs/{job_id}/{action}", summary="操作工作区任务", status_code=status.HTTP_200_OK)
def operate_workspace_job(
    job_id: str,
    action: str,
    payload: Optional[Dict[str, Any]] = Body(None),
    authorization: Optional[str] = Header(None),
    x_user_role: str = Header("editor", alias="X-User-Role"),
):
    actor = _edit_auth(authorization, x_user_role)
    return index_jobs.service().operate(job_id, action, _payload(payload), actor)


__all__ = ["router"]
