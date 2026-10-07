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

"""Phase 11 B4 素材库/本地素材能力的洁净室边界。

本切片只暴露已经能够证明的空态读取；任何涉及本机文件、分类后台、内容解析、
上传、移动、删除或存储管理的副作用均显式失败关闭。禁止猜测文件路径或伪造资产。
"""

from __future__ import annotations

from typing import Any

from gw.core.errors import CleanroomException

DATA_STATUS_EMPTY = "empty"
DATA_STATUS_NOT_INTEGRATED = "not_integrated"


def unavailable(endpoint: str, capability: str, code: str = "ASSET_LIBRARY_NOT_INTEGRATED") -> None:
    """抛出包含受控能力边界的 503 错误。"""
    raise CleanroomException(
        status_code=503,
        code=code,
        message=f"{capability} 尚未接入，已失败关闭",
        extra={
            "endpoint": endpoint,
            "unavailable": True,
            "data_status": DATA_STATUS_NOT_INTEGRATED,
        },
        expose_extra_fields={"endpoint", "unavailable", "data_status"},
    )


def empty_response(endpoint: str, **fields: Any) -> dict[str, Any]:
    """构造不伪造数据的可读空态。"""
    return {
        **fields,
        "data_status": DATA_STATUS_EMPTY,
        "data_gaps": ["asset_library_source_not_connected"],
        "endpoint": endpoint,
    }


def not_integrated_response(endpoint: str, **fields: Any) -> dict[str, Any]:
    """对单次读取返回明确的未接入状态，而不是伪造实体。"""
    return {
        **fields,
        "data_status": DATA_STATUS_NOT_INTEGRATED,
        "data_gaps": ["asset_library_source_not_connected"],
        "endpoint": endpoint,
        "unavailable": True,
    }
