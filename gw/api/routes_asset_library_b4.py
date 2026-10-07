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

"""Phase 12 素材库与本地素材的真实接口实现。

真实数据源：`gw.asset_library.service`（单实例 JSON 落盘素材库树）
与 `gw.asset_library.repository`（本机文件、内容版本、分类、本地索引）。

安全口径：
- 认证必须先于参数校验，未认证一律 401，不泄漏参数细节；
- 本机文件访问仅限 `GW_ALLOWED_ROOTS`，未配置即 403 失败关闭，且不回显原始路径；
- 依赖缺失（未配置根目录、无真实文件）保持失败关闭，不伪造数据。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Header, Request, Response, status

from gw.asset_library import repository as repo
from gw.asset_library.service import default_asset_library_service as library_service
from gw.core.auth import require_authenticated, require_edit_access
from gw.core.errors import CleanroomException

router = APIRouter(tags=["asset-library-b4"])


async def _body(request: Request) -> Dict[str, Any]:
    """宽容读取 JSON 请求体；空体或非对象等价于空字典。"""
    try:
        payload = await request.json()
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _read(authorization: Optional[str], x_user_role: str):
    """读权限：未认证一律 401。"""
    return require_authenticated(authorization, x_user_role)


def _write(authorization: Optional[str], x_user_role: str):
    """写权限：未认证 401，只读角色 403。"""
    return require_edit_access(authorization, x_user_role)


def _required(request: Request, name: str) -> str:
    """从查询串读取必填参数；缺失或有空白一律 400 INVALID_REQUEST。"""
    value = request.query_params.get(name)
    if value is None or not value.strip():
        raise CleanroomException(400, "INVALID_REQUEST", "缺少必填参数：%s" % name)
    return value


def _optional_int(request: Request, name: str) -> Optional[int]:
    """从查询串读取可选正整数参数；非法一律 400。"""
    raw = request.query_params.get(name)
    if raw is None or not raw.strip():
        return None
    try:
        value = int(raw)
    except ValueError:
        raise CleanroomException(400, "INVALID_REQUEST", "参数 %s 必须是整数" % name)
    if value < 1:
        raise CleanroomException(400, "INVALID_REQUEST", "参数 %s 必须为正整数" % name)
    return value


def _ids(payload: Dict[str, Any], key: str = "asset_ids") -> List[str]:
    value = payload.get(key) or []
    return [str(item) for item in value] if isinstance(value, list) else []


# ---------------------------------------------------------------------------
# 分类提示词与后台任务
# ---------------------------------------------------------------------------

@router.get("/api/asset-classification-prompt", status_code=status.HTTP_200_OK)
def get_asset_classification_prompt(
    authorization: Optional[str] = Header(None),
    x_user_role: str = Header("editor", alias="X-User-Role"),
):
    """读取落盘的真实分类提示词。"""
    _read(authorization, x_user_role)
    return repo.get_classification_prompt()


@router.patch("/api/asset-classification-prompt", status_code=status.HTTP_200_OK)
async def update_asset_classification_prompt(
    request: Request,
    authorization: Optional[str] = Header(None),
    x_user_role: str = Header("editor", alias="X-User-Role"),
):
    """更新分类提示词并落盘。"""
    _write(authorization, x_user_role)
    payload = await _body(request)
    return repo.set_classification_prompt(str(payload.get("prompt") or ""))


@router.post("/api/asset-classification/background", status_code=status.HTTP_200_OK)
async def start_asset_classification_background(
    request: Request,
    authorization: Optional[str] = Header(None),
    x_user_role: str = Header("editor", alias="X-User-Role"),
):
    """启动真实分类任务，返回真实 job_id 与 poll_hint。"""
    _write(authorization, x_user_role)
    payload = await _body(request)
    ids = _ids(payload)
    result = library_service.classify_items(ids)
    return repo.record_classification_job(result.get("results", []), len(ids))


@router.delete("/api/asset-classification/background", status_code=status.HTTP_200_OK)
def stop_asset_classification_background(
    authorization: Optional[str] = Header(None),
    x_user_role: str = Header("editor", alias="X-User-Role"),
):
    """停止当前分类任务并置为 cancelled。"""
    _write(authorization, x_user_role)
    return repo.stop_classification_job()


@router.get("/api/asset-classification/jobs/{job_id}", status_code=status.HTTP_200_OK)
def get_asset_classification_job(
    job_id: str,
    authorization: Optional[str] = Header(None),
    x_user_role: str = Header("editor", alias="X-User-Role"),
):
    """查询真实分类任务状态；不存在返回 404。"""
    _read(authorization, x_user_role)
    return repo.get_classification_job(job_id)


# ---------------------------------------------------------------------------
# 素材内容与版本
# ---------------------------------------------------------------------------

@router.get("/api/asset-content", status_code=status.HTTP_200_OK)
def get_asset_content(
    request: Request,
    authorization: Optional[str] = Header(None),
    x_user_role: str = Header("editor", alias="X-User-Role"),
):
    """读取真实素材内容；未创建时返回显式空态。"""
    _read(authorization, x_user_role)
    return repo.get_content(_required(request, "asset_id"))


@router.patch("/api/asset-content", status_code=status.HTTP_200_OK)
async def update_asset_content(
    request: Request,
    authorization: Optional[str] = Header(None),
    x_user_role: str = Header("editor", alias="X-User-Role"),
):
    """写入真实素材内容并生成新版本。"""
    _write(authorization, x_user_role)
    asset_id = _required(request, "asset_id")
    payload = await _body(request)
    return repo.set_content(asset_id, str(payload.get("content") or ""))


@router.get("/api/asset-content/pdf", status_code=status.HTTP_200_OK)
def get_asset_content_pdf(
    request: Request,
    authorization: Optional[str] = Header(None),
    x_user_role: str = Header("editor", alias="X-User-Role"),
):
    """用纯标准库生成真实合法 PDF 字节流。"""
    _read(authorization, x_user_role)
    asset_id = _required(request, "asset_id")
    from urllib.parse import quote
    return Response(content=repo.content_pdf(asset_id), media_type="application/pdf",
                    headers={"Content-Disposition": "inline; filename=content.pdf; filename*=UTF-8''" + quote(asset_id, safe="") + ".pdf"})


@router.get("/api/asset-content/versions", status_code=status.HTTP_200_OK)
def get_asset_content_versions(
    request: Request,
    authorization: Optional[str] = Header(None),
    x_user_role: str = Header("editor", alias="X-User-Role"),
):
    """列出真实版本历史（当前版 + 历史版）。"""
    _read(authorization, x_user_role)
    return repo.list_versions(_required(request, "asset_id"))


@router.get("/api/asset-content/versions/{version_id}", status_code=status.HTTP_200_OK)
def get_asset_content_version(
    version_id: str,
    request: Request,
    authorization: Optional[str] = Header(None),
    x_user_role: str = Header("editor", alias="X-User-Role"),
):
    """读取指定版本；不存在返回 404。"""
    _read(authorization, x_user_role)
    return repo.get_version(_required(request, "asset_id"), version_id)


@router.patch("/api/asset-content/versions/{version_id}", status_code=status.HTTP_200_OK)
async def update_asset_content_version(
    version_id: str,
    request: Request,
    authorization: Optional[str] = Header(None),
    x_user_role: str = Header("editor", alias="X-User-Role"),
):
    """修改指定版本内容。"""
    _write(authorization, x_user_role)
    asset_id = _required(request, "asset_id")
    payload = await _body(request)
    return repo.update_version(asset_id, version_id, str(payload.get("content") or ""))


@router.delete("/api/asset-content/versions/{version_id}", status_code=status.HTTP_200_OK)
def delete_asset_content_version(
    version_id: str,
    request: Request,
    authorization: Optional[str] = Header(None),
    x_user_role: str = Header("editor", alias="X-User-Role"),
):
    """删除历史版本；当前版本不可删除。"""
    _write(authorization, x_user_role)
    return repo.delete_version(_required(request, "asset_id"), version_id)


@router.post("/api/asset-content/versions/{version_id}/restore", status_code=status.HTTP_200_OK)
def restore_asset_content_version(
    version_id: str,
    request: Request,
    authorization: Optional[str] = Header(None),
    x_user_role: str = Header("editor", alias="X-User-Role"),
):
    """把历史版本恢复为当前版（新增版本号，不改写历史）。"""
    _write(authorization, x_user_role)
    return repo.restore_version(_required(request, "asset_id"), version_id)


# ---------------------------------------------------------------------------
# 本机文件（严格锚定允许根目录）
# ---------------------------------------------------------------------------

@router.get("/api/asset-file-info", status_code=status.HTTP_200_OK)
def get_asset_file_info(
    request: Request,
    authorization: Optional[str] = Header(None),
    x_user_role: str = Header("editor", alias="X-User-Role"),
):
    """返回允许根目录内真实文件元数据；越界 403。"""
    _read(authorization, x_user_role)
    return repo.file_info(_required(request, "path"))


@router.post("/api/asset-file-reveal", status_code=status.HTTP_200_OK)
async def reveal_asset_file(
    request: Request,
    authorization: Optional[str] = Header(None),
    x_user_role: str = Header("editor", alias="X-User-Role"),
):
    """只做路径准入校验，不启动外部文件管理器。"""
    _write(authorization, x_user_role)
    payload = await _body(request)
    return repo.reveal_file(str(payload.get("path") or ""))


# ---------------------------------------------------------------------------
# 素材库写操作
# ---------------------------------------------------------------------------

@router.patch("/api/asset-library/categories/{category_id}", status_code=status.HTTP_200_OK)
async def rename_asset_library_category(
    category_id: str,
    request: Request,
    authorization: Optional[str] = Header(None),
    x_user_role: str = Header("editor", alias="X-User-Role"),
):
    """重命名分类（CAS）。"""
    _write(authorization, x_user_role)
    payload = await _body(request)
    return library_service.rename_category(category_id, str(payload.get("name") or ""), payload.get("expected_version"))


@router.delete("/api/asset-library/categories/{category_id}", status_code=status.HTTP_200_OK)
def delete_asset_library_category(
    category_id: str,
    request: Request,
    authorization: Optional[str] = Header(None),
    x_user_role: str = Header("editor", alias="X-User-Role"),
):
    """删除分类（CAS）。"""
    _write(authorization, x_user_role)
    return library_service.delete_category(category_id, _optional_int(request, "expected_version"))


@router.post("/api/asset-library/items/batch", status_code=status.HTTP_201_CREATED)
async def create_asset_library_items_batch(
    request: Request,
    authorization: Optional[str] = Header(None),
    x_user_role: str = Header("editor", alias="X-User-Role"),
):
    """批量创建素材条目，返回真实 asset_id。"""
    _write(authorization, x_user_role)
    return library_service.create_items(await _body(request))


@router.post("/api/asset-library/items/classify", status_code=status.HTTP_200_OK)
async def classify_asset_library_items(
    request: Request,
    authorization: Optional[str] = Header(None),
    x_user_role: str = Header("editor", alias="X-User-Role"),
):
    """对真实条目做确定性扩展名归类并写回。"""
    _write(authorization, x_user_role)
    return library_service.classify_items(_ids(await _body(request)))


@router.post("/api/asset-library/items/delete", status_code=status.HTTP_200_OK)
async def delete_asset_library_items(
    request: Request,
    authorization: Optional[str] = Header(None),
    x_user_role: str = Header("editor", alias="X-User-Role"),
):
    """批量删除素材条目。"""
    _write(authorization, x_user_role)
    return library_service.delete_items(_ids(await _body(request)))


@router.post("/api/asset-library/items/move", status_code=status.HTTP_200_OK)
async def move_asset_library_items(
    request: Request,
    authorization: Optional[str] = Header(None),
    x_user_role: str = Header("editor", alias="X-User-Role"),
):
    """批量移动素材条目到目标分类。"""
    _write(authorization, x_user_role)
    payload = await _body(request)
    return library_service.move_items(_ids(payload), str(payload.get("category_id") or ""))


@router.patch("/api/asset-library/items/{item_id}", status_code=status.HTTP_200_OK)
async def update_asset_library_item(
    item_id: str,
    request: Request,
    authorization: Optional[str] = Header(None),
    x_user_role: str = Header("editor", alias="X-User-Role"),
):
    """修改素材条目（名称/所属分类，CAS）。"""
    _write(authorization, x_user_role)
    return library_service.update_item(item_id, await _body(request))


@router.delete("/api/asset-library/items/{item_id}", status_code=status.HTTP_200_OK)
def delete_asset_library_item(
    item_id: str,
    request: Request,
    authorization: Optional[str] = Header(None),
    x_user_role: str = Header("editor", alias="X-User-Role"),
):
    """删除单个素材条目。"""
    _write(authorization, x_user_role)
    return library_service.delete_item(item_id, _optional_int(request, "expected_version"))


@router.post("/api/asset-library/items/{item_id}/avatar-status", status_code=status.HTTP_200_OK)
def check_asset_avatar_status(
    item_id: str,
    authorization: Optional[str] = Header(None),
    x_user_role: str = Header("editor", alias="X-User-Role"),
):
    """查询素材头像登记状态（依据真实落盘登记，不做外部平台调用）。"""
    _write(authorization, x_user_role)
    return repo.avatar_status(item_id)


@router.post("/api/asset-library/items/{item_id}/register-avatar", status_code=status.HTTP_200_OK)
async def register_asset_avatar(
    item_id: str,
    request: Request,
    authorization: Optional[str] = Header(None),
    x_user_role: str = Header("editor", alias="X-User-Role"),
):
    """登记素材头像（本地登记，不调用外部平台）。"""
    _write(authorization, x_user_role)
    payload = await _body(request)
    return repo.register_avatar(item_id, str(payload.get("avatar_path") or ""))


@router.patch("/api/asset-library/libraries/{library_id}", status_code=status.HTTP_200_OK)
async def rename_asset_library(
    library_id: str,
    request: Request,
    authorization: Optional[str] = Header(None),
    x_user_role: str = Header("editor", alias="X-User-Role"),
):
    """重命名素材库（CAS）。"""
    _write(authorization, x_user_role)
    payload = await _body(request)
    return library_service.rename_library(library_id, str(payload.get("name") or ""), payload.get("expected_version"))


@router.delete("/api/asset-library/libraries/{library_id}", status_code=status.HTTP_200_OK)
def delete_asset_library(
    library_id: str,
    request: Request,
    authorization: Optional[str] = Header(None),
    x_user_role: str = Header("editor", alias="X-User-Role"),
):
    """删除素材库（CAS）。"""
    _write(authorization, x_user_role)
    return library_service.delete_library(library_id, _optional_int(request, "expected_version"))


@router.post("/api/asset-library/workflows/upload", status_code=status.HTTP_200_OK)
async def upload_asset_library_workflows(
    request: Request,
    authorization: Optional[str] = Header(None),
    x_user_role: str = Header("editor", alias="X-User-Role"),
):
    """导入工作流文件（真实解码 + 落盘到允许根目录）。"""
    _write(authorization, x_user_role)
    payload = await _body(request)
    return repo.save_upload(str(payload.get("path") or ""), str(payload.get("content_base64") or ""))
