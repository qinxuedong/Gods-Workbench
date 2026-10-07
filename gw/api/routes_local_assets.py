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

"""Phase 12 本地素材与存储文件真实接口。

真实数据源：`gw.asset_library.repository`（GW_ALLOWED_ROOTS 锚定 + 单实例 JSON 索引）。
未配置允许根目录时统一失败关闭，不读取请求中的任意路径。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Header, Query, Request, status

from gw.asset_library import repository as repo
from gw.core.auth import require_authenticated, require_edit_access

router = APIRouter(tags=["local-assets-b4"])


async def _body(request: Request) -> Dict[str, Any]:
    try:
        payload = await request.json()
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _read(authorization: Optional[str], x_user_role: str):
    return require_authenticated(authorization, x_user_role)


def _write(authorization: Optional[str], x_user_role: str):
    return require_edit_access(authorization, x_user_role)


def _ids(payload: Dict[str, Any], key: str = "paths") -> List[str]:
    value = payload.get(key) or []
    return [str(item) for item in value] if isinstance(value, list) else []


@router.get("/api/local-assets", status_code=status.HTTP_200_OK)
def list_local_assets(
    kind: Optional[str] = Query(None),
    authorization: Optional[str] = Header(None),
    x_user_role: str = Header("editor", alias="X-User-Role"),
):
    """列出真实本地素材索引；无条目返回空集合。"""
    _read(authorization, x_user_role)
    return repo.list_local_assets(kind)


@router.post("/api/local-assets/upload", status_code=status.HTTP_201_CREATED)
async def upload_local_assets(
    request: Request,
    authorization: Optional[str] = Header(None),
    x_user_role: str = Header("editor", alias="X-User-Role"),
):
    """真实写入允许根目录内的目标文件并登记索引。"""
    _write(authorization, x_user_role)
    payload = await _body(request)
    target = str(payload.get("path") or "")
    return repo.upload_and_index_local_asset(
        target,
        str(payload.get("content_base64") or ""),
        str(payload.get("kind") or "auto"),
    )


@router.post("/api/local-assets/delete", status_code=status.HTTP_200_OK)
async def delete_local_assets(
    request: Request,
    authorization: Optional[str] = Header(None),
    x_user_role: str = Header("editor", alias="X-User-Role"),
):
    """真实删除允许根目录内的文件。"""
    _write(authorization, x_user_role)
    payload = await _body(request)
    return repo.delete_local_files(_ids(payload))


@router.post("/api/local-assets/move", status_code=status.HTTP_200_OK)
async def move_local_assets(
    request: Request,
    authorization: Optional[str] = Header(None),
    x_user_role: str = Header("editor", alias="X-User-Role"),
):
    """在允许根目录内真实移动文件。"""
    _write(authorization, x_user_role)
    payload = await _body(request)
    return repo.move_local_file(str(payload.get("source") or ""), str(payload.get("target") or ""))


@router.patch("/api/local-assets/items", status_code=status.HTTP_200_OK)
async def update_local_asset(
    request: Request,
    authorization: Optional[str] = Header(None),
    x_user_role: str = Header("editor", alias="X-User-Role"),
):
    """更新本地素材索引条目（重命名/改分类）。"""
    _write(authorization, x_user_role)
    payload = await _body(request)
    return repo.update_local_entry(str(payload.get("asset_id") or ""), payload)


@router.post("/api/local-assets/folders", status_code=status.HTTP_201_CREATED)
async def create_local_asset_folder(
    request: Request,
    authorization: Optional[str] = Header(None),
    x_user_role: str = Header("editor", alias="X-User-Role"),
):
    """在数据目录内创建真实文件夹。"""
    _write(authorization, x_user_role)
    payload = await _body(request)
    return repo.create_local_folder(str(payload.get("name") or ""))


@router.patch("/api/local-assets/folders", status_code=status.HTTP_200_OK)
async def update_local_asset_folder(
    request: Request,
    authorization: Optional[str] = Header(None),
    x_user_role: str = Header("editor", alias="X-User-Role"),
):
    """重命名数据目录内的真实文件夹。"""
    _write(authorization, x_user_role)
    payload = await _body(request)
    return repo.rename_local_folder(str(payload.get("name") or ""), str(payload.get("new_name") or ""))


@router.post("/api/local-assets/classify", status_code=status.HTTP_200_OK)
async def classify_local_assets(
    request: Request,
    authorization: Optional[str] = Header(None),
    x_user_role: str = Header("editor", alias="X-User-Role"),
):
    """对真实登记条目做确定性分类。"""
    _write(authorization, x_user_role)
    payload = await _body(request)
    ids = payload.get("asset_ids") or []
    return repo.classify_local_entries([str(x) for x in ids] if isinstance(ids, list) else [])


@router.post("/api/local-assets/caption", status_code=status.HTTP_200_OK)
async def caption_local_assets(
    request: Request,
    authorization: Optional[str] = Header(None),
    x_user_role: str = Header("editor", alias="X-User-Role"),
):
    """为真实素材生成确定性说明（基于文件名与元数据，不做语义猜测）。"""
    _write(authorization, x_user_role)
    payload = await _body(request)
    ids = payload.get("asset_ids") or []
    return repo.caption_local_entries([str(x) for x in ids] if isinstance(ids, list) else [])


@router.patch("/api/local-assets/caption", status_code=status.HTTP_200_OK)
async def save_local_asset_caption(
    request: Request,
    authorization: Optional[str] = Header(None),
    x_user_role: str = Header("editor", alias="X-User-Role"),
):
    """保存人工说明文本到真实索引。"""
    _write(authorization, x_user_role)
    payload = await _body(request)
    return repo.save_local_caption(str(payload.get("asset_id") or ""), str(payload.get("caption") or ""))


@router.get("/api/storage-files", status_code=status.HTTP_200_OK)
def list_storage_files(
    authorization: Optional[str] = Header(None),
    x_user_role: str = Header("editor", alias="X-User-Role"),
):
    """列出数据目录内的真实存储文件与允许根目录配置状态。"""
    _read(authorization, x_user_role)
    return repo.storage_files()


@router.post("/api/storage-files/delete", status_code=status.HTTP_200_OK)
async def delete_storage_files(
    request: Request,
    authorization: Optional[str] = Header(None),
    x_user_role: str = Header("editor", alias="X-User-Role"),
):
    """删除数据目录内的真实存储产物。"""
    _write(authorization, x_user_role)
    payload = await _body(request)
    return repo.delete_storage_files(_ids(payload, "names"))
