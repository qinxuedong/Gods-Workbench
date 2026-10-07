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

"""设置页契约模型（Phase 10D，Pydantic v2）。

严格对齐 ``docs/contracts/SETTINGS-INTERFACE-CATALOG.yaml``（version: p10d-frozen-1）。

字段口径：

- 稳定 ID 一律使用 ``structure_id`` / ``provider_id`` / ``revision``，
  禁止 pid / cid / sid 等别名；
- 结构体成员仅登记 ``asset_id`` 与 ``sort_order``；素材注册表未接入时
  **不写** ``asset`` 元数据（宁缺毋滥，绝不编造成员状态）；
- 探测端点无真实出网能力，模型层不存在模型列表 / 延迟数字等可伪造字段。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field

#: 数据可用性口径；无真实来源时必须如实标记，不得谎报 ok。
DATA_STATUS_OK = "ok"
DATA_STATUS_NOT_CONFIGURED = "not_configured"

#: 明确的缺口说明文案（data_gaps）。
GAP_STORAGE_SETTINGS_SOURCE = "storage_settings_source_not_integrated"
GAP_PROVIDER_REGISTRY_SOURCE = "provider_registry_source_not_integrated"
GAP_MEMBER_ASSET_METADATA = "member_asset_metadata_not_integrated"

#: 本阶段探测端点统一错误码（fail-closed）。
PROVIDER_PROBE_NOT_INTEGRATED = "PROVIDER_PROBE_NOT_INTEGRATED"


class StorageSettingsSnapshot(BaseModel):
    """``GET/PATCH /api/storage-settings`` 响应：存储设置真值快照。"""

    model_config = ConfigDict(extra="ignore")

    configured: bool = Field(..., description="是否已有真实配置来源")
    revision: int = Field(..., ge=1, description="存储设置 CAS 版本号")
    dirs: Dict[str, Any] = Field(default_factory=dict, description="目录映射；未配置时为空对象")
    defaults: Dict[str, Any] = Field(default_factory=dict, description="默认目录建议；无来源时为空对象")
    extra_local_dirs: List[str] = Field(default_factory=list, description="附加本地目录路径列表")
    extra_local_entries: List[Dict[str, Any]] = Field(default_factory=list, description="附加本地来源条目")
    local_library_names: Dict[str, str] = Field(default_factory=dict, description="本地素材库显示名")
    local_library_order: List[str] = Field(default_factory=list, description="本地素材库排序")
    local_libraries: List[Dict[str, Any]] = Field(default_factory=list, description="本地素材库投影")
    data_status: str = Field(DATA_STATUS_NOT_CONFIGURED, description="ok 或 not_configured")
    data_gaps: List[str] = Field(default_factory=list, description="未接入 / 未配置原因")


class StorageSettingsPatchRequest(BaseModel):
    """``PATCH /api/storage-settings`` 请求体；未给出的字段保持不变。"""

    model_config = ConfigDict(extra="ignore")

    dirs: Optional[Dict[str, Any]] = None
    extra_local_entries: Optional[List[Dict[str, Any]]] = None
    local_library_names: Optional[Dict[str, str]] = None
    local_library_order: Optional[List[str]] = None
    expected_version: Optional[int] = Field(None, ge=1, description="CAS 期望版本；缺省表示不校验")
    expected_revision: Optional[int] = Field(None, ge=1, description="前端既有别名，等价 expected_version")


class ProviderSnapshot(BaseModel):
    """``GET/PUT /api/providers`` 响应：模型平台集合快照。"""

    model_config = ConfigDict(extra="ignore")

    providers: List[Dict[str, Any]] = Field(default_factory=list, description="provider 条目；默认必须为空数组")
    revision: int = Field(..., ge=1, description="provider 集合 CAS 版本号")
    configured: bool = Field(..., description="是否已有真实 provider 配置")
    data_status: str = Field(DATA_STATUS_NOT_CONFIGURED, description="ok 或 not_configured")
    data_gaps: List[str] = Field(default_factory=list, description="未接入 / 未配置原因")


class ProviderPutRequest(BaseModel):
    """``PUT /api/providers`` 请求体包装。

    前端既有的调用面是「外层直接发送数组」，因此路由层接受裸数组；
    本模型仅用于内部规范化与单测，不要求前端改变调用方式。
    """

    model_config = ConfigDict(extra="forbid")

    providers: List[Dict[str, Any]] = Field(default_factory=list)


class AssetStructureMember(BaseModel):
    """结构成员；仅登记稳定 asset_id 与顺序，不编造资源元数据。"""

    model_config = ConfigDict(extra="ignore")

    asset_id: str = Field(..., description="成员素材稳定标识")
    sort_order: int = Field(..., ge=0, description="成员展示顺序")


class AssetStructureItem(BaseModel):
    """版本/组结构实体；``structure_id`` 为确定性前缀序号。"""

    model_config = ConfigDict(extra="ignore")

    structure_id: str = Field(..., description="稳定结构标识（strc_NNNN）")
    kind: str = Field(..., description="结构类型：version 或 group")
    current_asset_id: str = Field(..., description="当前代表素材标识")
    version: int = Field(..., ge=1, description="结构级 CAS 版本号")
    created_at: str = Field(..., description="创建时间（ISO 8601 UTC）")
    updated_at: str = Field(..., description="最近更新时间（ISO 8601 UTC）")
    members: List[AssetStructureMember] = Field(default_factory=list, description="成员集合")


class AssetStructureListResponse(BaseModel):
    """``GET /api/asset-registry/asset-structures`` 响应。"""

    model_config = ConfigDict(extra="ignore")

    items: List[AssetStructureItem] = Field(default_factory=list, description="结构集合")
    total: int = Field(0, ge=0, description="结构总数")
    revision: int = Field(..., ge=1, description="结构集合 CAS 版本号")
    data_status: str = Field(DATA_STATUS_OK, description="ok 或 not_configured")
    data_gaps: List[str] = Field(default_factory=list, description="未接入原因")


class AssetStructureCreateRequest(BaseModel):
    """``POST /api/asset-registry/asset-structures`` 请求体。"""

    model_config = ConfigDict(extra="forbid")

    kind: str = Field(..., min_length=1, description="version 或 group")
    asset_ids: List[str] = Field(..., description="成员素材标识；至少 2 个且不重复")
    current_asset_id: Optional[str] = Field(None, description="当前代表素材；必须是成员之一")
    expected_version: Optional[int] = Field(None, ge=1, description="集合 CAS 期望版本；缺省表示不校验")


class AssetStructureUpdateRequest(BaseModel):
    """``PATCH /api/asset-registry/asset-structures/{structure_id}`` 请求体。"""

    model_config = ConfigDict(extra="forbid")

    asset_ids: Optional[List[str]] = None
    current_asset_id: Optional[str] = None
    expected_version: Optional[int] = Field(None, ge=1, description="目标结构 CAS 期望版本；缺省表示不校验")


class AssetStructureCurrentRequest(BaseModel):
    """``PATCH .../{structure_id}/current`` 请求体。"""

    model_config = ConfigDict(extra="forbid")

    current_asset_id: str = Field(..., min_length=1, description="新的当前代表素材；必须是成员之一")
    expected_version: Optional[int] = Field(None, ge=1, description="目标结构 CAS 期望版本；缺省表示不校验")


class AssetStructureResponse(BaseModel):
    """单结构响应包装（创建 / 读取 / 更新 / 切换当前）。"""

    model_config = ConfigDict(extra="ignore")

    structure: AssetStructureItem


class AssetStructureDeleteResponse(BaseModel):
    """``DELETE .../{structure_id}`` 响应：删除结果与集合最新版本。"""

    model_config = ConfigDict(extra="ignore")

    deleted_structure_id: str
    revision: int = Field(..., ge=1)
