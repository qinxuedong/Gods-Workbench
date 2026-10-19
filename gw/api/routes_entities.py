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

"""C12-T1 Master Catalog REST 路由的稳定 API 导出。

实体域持有唯一的路由载体；本模块保留工单约定的导入路径，避免在既有
``gw/api`` 路由树中创建第二个平行承载文件。
"""

from gw.entities.repository import (
    ArchivePayload,
    archive_entity_endpoint as archive_entity,
    create_entity_endpoint as create_entity,
    get_entity_endpoint as get_entity,
    list_entities_endpoint as list_entities,
    router,
    update_entity_endpoint as update_entity,
)

__all__ = [
    "ArchivePayload",
    "archive_entity",
    "create_entity",
    "get_entity",
    "list_entities",
    "router",
    "update_entity",
]
