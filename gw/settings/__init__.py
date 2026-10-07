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

"""Gods-Workbench 设置页领域模块（Phase 10D）。

本模块承载设置页阶段的三类能力，全部依据冻结契约
``docs/contracts/SETTINGS-INTERFACE-CATALOG.yaml``（version: p10d-frozen-1）重新编写：

- ``StorageSettingsService``：存储/目录设置的内存快照与 CAS 版本；
- ``ProviderService``：模型平台（provider）集合的内存快照与 CAS 版本；
- ``AssetStructureService``：素材版本/组结构（structure_id）的集合与结构级 CAS。

洁净室边界：

- **零伪造**：无真实数据源时一律返回空集合 + 明确的未配置/未接入标记，
  绝不预置厂商条目、绝不编造根目录或厂商模型列表；
- **零凭据**：provider 凭据字段（key / secret / token / password 一类）一律不落库、不回显；
- **fail-closed**：本阶段无真实外网探测能力，探测端点如实返回
  ``PROVIDER_PROBE_NOT_INTEGRATED``，绝不伪造连通性成功；
- **存储口径**：进程内内存存储，重启即丢失、多 worker 不共享（与既有各阶段同口径）。
"""

from gw.settings.service import (
    AssetStructureService,
    ProviderService,
    StorageSettingsService,
    default_asset_structure_service,
    default_provider_service,
    default_storage_settings_service,
)

__all__ = [
    "AssetStructureService",
    "ProviderService",
    "StorageSettingsService",
    "default_asset_structure_service",
    "default_provider_service",
    "default_storage_settings_service",
]
