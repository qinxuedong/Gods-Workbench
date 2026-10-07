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

"""Phase 12 A3 提示词条目 API（真实落盘）。

数据来源：``gw.prompt_library.items_repository``
（core.storage.JsonState 命名空间 ``prompt_library``），条目重启可恢复。

证据边界（如实声明）：库/分类目录由 ``PromptLibraryService`` 持久化到
``prompt_library_tree``，条目由本模块落盘到 ``prompt_library``；遗留孤儿条目仅保留原始父引用，
通过 ``data_gaps`` 标明，不自动绑定。本模块不生成任何提示词文本。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Header, Query, Request, status

from gw.core.auth import require_authenticated, require_edit_access
from gw.prompt_library import items_repository as repo

router = APIRouter(prefix="/api/prompt-libraries", tags=["prompt-library-items-b8"])


def _read(authorization: Optional[str], role: str):
    return require_authenticated(authorization, role)


def _write(authorization: Optional[str], role: str):
    return require_edit_access(authorization, role)


async def _json(request: Request) -> Dict[str, Any]:
    try:
        payload = await request.json()
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


@router.get("/items", status_code=status.HTTP_200_OK)
def list_items(
    library_id: Optional[str] = None,
    authorization: Optional[str] = Header(None),
    x_user_role: str = Header("editor", alias="X-User-Role"),
):
    """读取提示词条目集合；空集合返回 items: []，不伪造任何文本。"""
    _read(authorization, x_user_role)
    return repo.list_items(library_id)


@router.post("/items", status_code=status.HTTP_201_CREATED)
async def create_item(
    request: Request,
    authorization: Optional[str] = Header(None),
    x_user_role: str = Header("editor", alias="X-User-Role"),
):
    """创建条目；稳定 ID ``pitem_NNNN``（复用既有前缀），写后可 GET 读回。"""
    _write(authorization, x_user_role)
    return repo.create_item(await _json(request))


@router.patch("/items/{item_id}", status_code=status.HTTP_200_OK)
async def update_item(
    item_id: str,
    request: Request,
    authorization: Optional[str] = Header(None),
    x_user_role: str = Header("editor", alias="X-User-Role"),
):
    """更新条目；不存在 404，条目级 CAS 冲突 409。"""
    _write(authorization, x_user_role)
    return repo.update_item(item_id, await _json(request))


@router.delete("/items/{item_id}", status_code=status.HTTP_200_OK)
def delete_item(
    item_id: str,
    expected_version: Optional[int] = Query(None, ge=1),
    authorization: Optional[str] = Header(None),
    x_user_role: str = Header("editor", alias="X-User-Role"),
):
    """删除条目；删除后 GET 不再返回该条目。"""
    _write(authorization, x_user_role)
    return repo.delete_item(item_id, expected_version)


@router.post("/items/delete", status_code=status.HTTP_200_OK)
async def bulk_delete_items(
    request: Request,
    authorization: Optional[str] = Header(None),
    x_user_role: str = Header("editor", alias="X-User-Role"),
):
    """批量删除条目；只删除真实存在的 ID，禁止静默伪造成删除成功。"""
    _write(authorization, x_user_role)
    payload = await _json(request)
    ids: List[str] = payload.get("ids") or []
    if not isinstance(ids, list):
        ids = []
    return repo.bulk_delete(ids, payload.get("expected_version"))
