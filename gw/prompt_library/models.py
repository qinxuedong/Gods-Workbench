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

"""提示词库契约模型（Phase 10C，Pydantic v2）。

严格对齐 docs/contracts/PROMPT-LIBRARY-INTERFACE-CATALOG.yaml（version: p10c-frozen-1）。

字段口径：

- 稳定 ID 一律使用 library_id / category_id，禁止 id / pid / cid 等别名；
- 本阶段**不实现条目 CRUD**，因此模型里不存在 items / positive / negative / scene
  等提示词文本字段——没有任何真实文本来源，宁可不存在也不伪造；
- 空目录返回 libraries: [] 且 active_library_id: null。
"""

from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field


class PromptCategoryItem(BaseModel):
    """提示词分类实体；仅结构，不含任何提示词文本。"""

    model_config = ConfigDict(extra="ignore")

    category_id: str = Field(..., description="稳定分类标识")
    library_id: str = Field(..., description="所属提示词库标识")
    name: str = Field(..., description="分类名称")
    version: int = Field(..., ge=1, description="并发控制版本号（CAS）")
    created_at: str = Field(..., description="创建时间（ISO 8601 UTC）")
    updated_at: str = Field(..., description="最近更新时间（ISO 8601 UTC）")


class PromptLibraryItem(BaseModel):
    """提示词库实体；仅结构，不含任何条目与提示词文本。"""

    model_config = ConfigDict(extra="ignore")

    library_id: str = Field(..., description="稳定提示词库标识")
    name: str = Field(..., description="提示词库名称")
    version: int = Field(..., ge=1, description="提示词库 CAS 版本号")
    created_at: str = Field(..., description="创建时间（ISO 8601 UTC）")
    updated_at: str = Field(..., description="最近更新时间（ISO 8601 UTC）")
    categories: List[PromptCategoryItem] = Field(default_factory=list, description="分类集合")


class PromptLibrarySnapshot(BaseModel):
    """提示词库顶层目录快照。"""

    model_config = ConfigDict(extra="ignore")

    version: int = Field(..., ge=1, description="顶层目录 CAS 版本号")
    active_library_id: Optional[str] = Field(None, description="当前激活提示词库标识；空目录为 null")
    libraries: List[PromptLibraryItem] = Field(default_factory=list, description="提示词库集合")


class PromptLibraryCreateRequest(BaseModel):
    """POST /api/prompt-libraries 请求体。"""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., min_length=1, max_length=128, description="提示词库名称")
    expected_version: Optional[int] = Field(None, ge=1, description="顶层目录 CAS 期望版本；缺省表示不校验")


class PromptLibraryRenameRequest(BaseModel):
    """PATCH /api/prompt-libraries/{library_id} 请求体。"""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., min_length=1, max_length=128, description="新的提示词库名称")
    expected_version: Optional[int] = Field(None, ge=1, description="目标库 CAS 期望版本；缺省表示不校验")


class PromptCategoryCreateRequest(BaseModel):
    """POST /api/prompt-libraries/categories 请求体。"""

    model_config = ConfigDict(extra="forbid")

    library_id: str = Field(..., min_length=1, max_length=128, description="父提示词库标识")
    name: str = Field(..., min_length=1, max_length=128, description="分类名称")
    expected_version: Optional[int] = Field(None, ge=1, description="父库 CAS 期望版本；缺省表示不校验")


class PromptCategoryRenameRequest(BaseModel):
    """PATCH /api/prompt-libraries/categories/{category_id} 请求体。"""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., min_length=1, max_length=128, description="新的分类名称")
    expected_version: Optional[int] = Field(None, ge=1, description="目标分类 CAS 期望版本；缺省表示不校验")


class PromptLibraryResponse(BaseModel):
    """GET /api/prompt-libraries 返回包装。"""

    library: PromptLibrarySnapshot


class PromptLibraryCreateResponse(BaseModel):
    """创建提示词库成功返回（201）。"""

    prompt_library: PromptLibraryItem
    library: PromptLibrarySnapshot


class PromptLibraryRenameResponse(BaseModel):
    """重命名提示词库成功返回（200）。"""

    prompt_library: PromptLibraryItem
    library: PromptLibrarySnapshot


class PromptCategoryCreateResponse(BaseModel):
    """创建提示词分类成功返回（201）。"""

    category: PromptCategoryItem
    library: PromptLibrarySnapshot


class PromptCategoryRenameResponse(BaseModel):
    """重命名提示词分类成功返回（200）。"""

    category: PromptCategoryItem
    library: PromptLibrarySnapshot


class PromptLibraryDeleteResponse(BaseModel):
    """删除提示词库 / 分类成功返回（200）；统一回传最新目录快照。"""

    library: PromptLibrarySnapshot
