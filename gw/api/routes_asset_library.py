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

"""素材库 API 路由实现（Phase 10A）。

严格对齐 docs/contracts/ASSET-LIBRARY-INTERFACE-CATALOG.yaml（p10a-frozen-1）的 3 个端点：

- ``GET  /api/asset-library``             读取素材库树（空库返回 libraries: []）
- ``POST /api/asset-library/libraries``   创建素材库（CAS + 重名 409）
- ``POST /api/asset-library/categories``  创建分类（父库 CAS + 404 + 重名 409）

边界：其余 ``/api/asset-library/*`` 端点（items / libraries 明细 / categories 明细 /
workflows/upload）仍属 KNOWN_UNIMPLEMENTED，未获契约授权，本模块**不得**顺带实现。
"""

from typing import Optional

from fastapi import APIRouter, Header, status

from gw.asset_library.models import (
    AssetLibraryCreateRequest,
    AssetLibraryResponse,
    CategoryCreateRequest,
    CategoryCreateResponse,
    LibraryCreateResponse,
)
from gw.asset_library.service import default_asset_library_service
from gw.core.auth import require_authenticated, require_edit_access

router = APIRouter(prefix="/api/asset-library", tags=["asset-library"])


@router.get(
    "",
    response_model=AssetLibraryResponse,
    summary="读取素材库树",
    status_code=status.HTTP_200_OK,
)
def get_asset_library(
    authorization: Optional[str] = Header(None),
    x_user_role: str = Header("editor", alias="X-User-Role", description="用户角色权限"),
):
    """返回当前身份可见的素材库树；空库返回空数组，不伪造素材。"""
    require_authenticated(authorization, x_user_role)
    return AssetLibraryResponse(library=default_asset_library_service.get_snapshot())


@router.post(
    "/libraries",
    response_model=LibraryCreateResponse,
    summary="创建素材库",
    status_code=status.HTTP_201_CREATED,
)
def create_asset_library(
    payload: AssetLibraryCreateRequest,
    authorization: Optional[str] = Header(None),
    x_user_role: str = Header("editor", alias="X-User-Role", description="用户角色权限"),
):
    """创建素材库；要求写权限，重名或目录版本冲突失败关闭。"""
    require_edit_access(authorization, x_user_role)
    library, snapshot = default_asset_library_service.create_library(payload)
    return LibraryCreateResponse(library=library, asset_library=snapshot)


@router.post(
    "/categories",
    response_model=CategoryCreateResponse,
    summary="创建素材库分类",
    status_code=status.HTTP_201_CREATED,
)
def create_asset_library_category(
    payload: CategoryCreateRequest,
    authorization: Optional[str] = Header(None),
    x_user_role: str = Header("editor", alias="X-User-Role", description="用户角色权限"),
):
    """在指定素材库下创建分类；要求写权限，父库不存在或版本冲突失败关闭。"""
    require_edit_access(authorization, x_user_role)
    category, library = default_asset_library_service.create_category(payload)
    return CategoryCreateResponse(category=category, library=library)
