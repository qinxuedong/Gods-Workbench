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

"""素材库契约模型（Phase 10A）。

严格对齐 docs/contracts/ASSET-LIBRARY-INTERFACE-CATALOG.yaml（version: p10a-frozen-1）
与 docs/fixtures/asset-library-*.json 黄金夹具。

对外字段一律使用稳定 ID：library_id / category_id / asset_id，禁止 id/pid/cid 别名。
"""

from enum import Enum
from typing import List, Optional
from pydantic import BaseModel, ConfigDict, Field


class CategoryType(str, Enum):
    """素材分类类型枚举。"""

    IMAGE = "image"
    WORKFLOW = "workflow"


class AssetItem(BaseModel):
    """素材条目（本阶段只读投影，创建端点不产生条目）。"""

    model_config = ConfigDict(extra="ignore")

    asset_id: str = Field(..., description="稳定素材标识")
    library_id: str = Field(..., description="所属素材库标识")
    category_id: str = Field(..., description="所属分类标识")
    name: str = Field(..., description="素材名称")
    url: Optional[str] = Field(None, description="素材访问地址；无法确定时为 null，绝不伪造")
    created_at: str = Field(..., description="创建时间（ISO 8601 UTC）")


class CategoryItem(BaseModel):
    """素材分类实体。"""

    model_config = ConfigDict(extra="ignore")

    category_id: str = Field(..., description="稳定分类标识")
    library_id: str = Field(..., description="所属素材库标识")
    name: str = Field(..., description="分类名称")
    type: CategoryType = Field(..., description="分类类型")
    version: int = Field(..., ge=1, description="并发控制版本号（CAS）")
    created_at: str = Field(..., description="创建时间（ISO 8601 UTC）")
    updated_at: str = Field(..., description="最近更新时间（ISO 8601 UTC）")
    items: List[AssetItem] = Field(default_factory=list, description="分类内素材条目")


class LibraryItem(BaseModel):
    """素材库实体。"""

    model_config = ConfigDict(extra="ignore")

    library_id: str = Field(..., description="稳定素材库标识")
    name: str = Field(..., description="素材库名称")
    version: int = Field(..., ge=1, description="素材库 CAS 版本号")
    created_at: str = Field(..., description="创建时间（ISO 8601 UTC）")
    updated_at: str = Field(..., description="最近更新时间（ISO 8601 UTC）")
    categories: List[CategoryItem] = Field(default_factory=list, description="分类集合")


class AssetLibrarySnapshot(BaseModel):
    """素材库顶层目录快照。"""

    model_config = ConfigDict(extra="ignore")

    version: int = Field(..., ge=1, description="顶层目录 CAS 版本号")
    active_library_id: Optional[str] = Field(None, description="当前激活素材库标识；空库为 null")
    libraries: List[LibraryItem] = Field(default_factory=list, description="素材库集合")


class AssetLibraryResponse(BaseModel):
    """GET /api/asset-library 返回包装。"""

    library: AssetLibrarySnapshot


class AssetLibraryCreateRequest(BaseModel):
    """POST /api/asset-library/libraries 请求体。"""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., min_length=1, max_length=128, description="素材库名称")
    expected_version: Optional[int] = Field(None, ge=1, description="顶层目录 CAS 期望版本；缺省表示不校验")


class CategoryCreateRequest(BaseModel):
    """POST /api/asset-library/categories 请求体。"""

    model_config = ConfigDict(extra="forbid")

    library_id: str = Field(..., min_length=1, max_length=128, description="父素材库标识")
    name: str = Field(..., min_length=1, max_length=128, description="分类名称")
    type: CategoryType = Field(..., description="分类类型")
    expected_version: Optional[int] = Field(None, ge=1, description="父库 CAS 期望版本；缺省表示不校验")


class LibraryCreateResponse(BaseModel):
    """创建素材库成功返回（201）。"""

    library: LibraryItem
    asset_library: AssetLibrarySnapshot


class CategoryCreateResponse(BaseModel):
    """创建分类成功返回（201）。"""

    category: CategoryItem
    library: LibraryItem
