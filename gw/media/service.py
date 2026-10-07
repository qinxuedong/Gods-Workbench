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

"""Phase 11 B5 媒体处理洁净室服务边界。

当前仓库没有经过准入的媒体存储、缩略图、波形、转码或远程图片后端。
本服务只提供统一失败关闭，不读取本机文件、不联网、不执行外部进程，
也不伪造媒体 URL、任务 ID、进度或预计完成时间。
"""

from __future__ import annotations

from typing import Any

from gw.core.errors import CleanroomException

DATA_STATUS_NOT_INTEGRATED = "not_integrated"


def unavailable(endpoint: str, capability: str, code: str = "MEDIA_NOT_INTEGRATED") -> None:
    """抛出带有受控边界元数据的媒体能力未接入错误。"""
    raise CleanroomException(
        status_code=503,
        code=code,
        message=f"{capability}尚未接入，已失败关闭",
        extra={
            "endpoint": endpoint,
            "unavailable": True,
            "data_status": DATA_STATUS_NOT_INTEGRATED,
        },
        expose_extra_fields={"endpoint", "unavailable", "data_status"},
    )


def unavailable_detail(endpoint: str, capability: str, code: str = "MEDIA_NOT_INTEGRATED") -> dict[str, Any]:
    """返回结构化说明，供非 HTTP 调用方记录边界；不生成媒体或任务数据。"""
    return {
        "endpoint": endpoint,
        "capability": capability,
        "code": code,
        "unavailable": True,
        "data_status": DATA_STATUS_NOT_INTEGRATED,
        "data_gaps": ["media_backend_not_connected"],
    }
