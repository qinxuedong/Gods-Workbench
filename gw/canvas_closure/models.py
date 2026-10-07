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

"""画布闭环契约模型（Phase 10E，Pydantic v2）。

严格对齐 ``docs/contracts/CANVAS-CLOSURE-INTERFACE-CATALOG.yaml``（version: p10e-frozen-1）。

字段口径：

- 稳定 ID 一律使用 ``canvas_id`` / ``folder_id`` / ``video_task_id`` / ``asset_id``，
  禁止 pid / cid / id 等别名作为生产实体；``id`` 仅作为**前端既有读取面**的
  兼容投影，由服务端显式导出并在契约 ``decisions.frontend_compat`` 中声明；
- 无真实来源的集合一律返回空数组 + ``data_status: not_integrated`` + ``data_gaps``，
  绝不预置演示条目；
- 视频渲染、画布素材打包下载、共享文件夹导入、素材注册表挂接在本阶段**均无真实后端**，
  模型层不提供任何可承载假进度 / 假文件 / 假素材的字段。
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

#: 数据可用性口径；无真实来源时必须如实标记，不得谎报 ok。
DATA_STATUS_OK = "ok"
DATA_STATUS_NOT_INTEGRATED = "not_integrated"

#: 明确的缺口说明文案（data_gaps）。
GAP_ASSET_INDEX_SOURCE = "canvas_asset_index_source_not_integrated"
GAP_SHARED_FOLDER_TREE = "shared_folder_tree_scan_not_integrated"
GAP_VIDEO_RENDERER = "video_renderer_source_not_integrated"

#: 本阶段 fail-closed 错误码（无真实后端时必须 503）。
VIDEO_RENDERER_NOT_INTEGRATED = "VIDEO_RENDERER_NOT_INTEGRATED"
CANVAS_ASSET_DOWNLOAD_NOT_INTEGRATED = "CANVAS_ASSET_DOWNLOAD_NOT_INTEGRATED"
CANVAS_ASSET_ATTACH_NOT_INTEGRATED = "CANVAS_ASSET_ATTACH_NOT_INTEGRATED"
SHARED_FOLDER_IMPORT_NOT_INTEGRATED = "SHARED_FOLDER_IMPORT_NOT_INTEGRATED"

#: 目标不存在错误码。
SHARED_FOLDER_NOT_FOUND = "SHARED_FOLDER_NOT_FOUND"
VIDEO_TASK_NOT_FOUND = "VIDEO_TASK_NOT_FOUND"

#: 请求语义错误码。
INVALID_CANVAS_OPERATION = "INVALID_CANVAS_OPERATION"
INVALID_SHARED_FOLDER_PATH = "INVALID_SHARED_FOLDER_PATH"
EXPECTED_VERSION_REQUIRED = "EXPECTED_VERSION_REQUIRED"

#: ``POST /api/canvases/{canvas_id}/touch`` 允许的操作。
CANVAS_TOUCH_OPERATIONS = ("archive", "unarchive")


class CanvasMetaRequest(BaseModel):
    """``POST /api/canvases/{canvas_id}/meta`` 请求体。

    ``expected_version`` 与前端既有别名 ``expected_revision`` 等价；
    ``base_updated_at`` 为前端乐观并发附加字段，本阶段仅登记不参与判定。
    """

    model_config = ConfigDict(extra="ignore")

    title: Optional[str] = Field(None, min_length=1, description="画布标题")
    entity_id: Optional[str] = Field(None, min_length=1, description="绑定的实体 ID")
    project_id: Optional[str] = Field(None, min_length=1, description="所属项目 ID")
    project: Optional[str] = Field(None, min_length=1, description="前端既有别名，等价 project_id")
    icon: Optional[str] = None
    board_x: Optional[float] = None
    board_y: Optional[float] = None
    expected_version: Optional[int] = Field(None, ge=1, description="画布 CAS 期望版本")
    expected_revision: Optional[int] = Field(None, ge=1, description="前端既有别名，等价 expected_version")
    base_updated_at: Optional[float] = None

    def resolved_version(self) -> Optional[int]:
        """返回调用方给出的 CAS 期望版本（两个别名取其一）。"""
        return self.expected_version if self.expected_version is not None else self.expected_revision


class CanvasMetaResponse(BaseModel):
    """画布元信息更新后的返回包装。"""

    canvas: Dict[str, Any]


class CanvasTouchRequest(BaseModel):
    """``POST /api/canvases/{canvas_id}/touch`` 可选请求体（携带 CAS 期望版本）。"""

    model_config = ConfigDict(extra="ignore")

    expected_version: Optional[int] = Field(None, ge=1)


class CanvasTouchResponse(BaseModel):
    """画布归档 / 解归档后的返回包装。"""

    canvas: Dict[str, Any]


class CanvasPurgeResponse(BaseModel):
    """画布彻底删除结果。"""

    canvas_id: str
    version: int
    purged: bool


class CanvasTrashResponse(BaseModel):
    """回收站 / 归档列表响应；无内容时必须是空数组。"""

    canvases: List[Dict[str, Any]] = Field(default_factory=list)
    data_status: str = DATA_STATUS_OK
    data_gaps: List[str] = Field(default_factory=list)


class CanvasAssetIndexResponse(BaseModel):
    """``GET /api/canvas-assets`` 响应：画布索引 + 真实素材条目。

    素材条目需要素材注册表（本阶段未接入），因此 ``items`` 必须为空并如实标记缺口。
    """

    categories: List[Dict[str, Any]] = Field(default_factory=list)
    canvases: List[Dict[str, Any]] = Field(default_factory=list)
    items: List[Dict[str, Any]] = Field(default_factory=list)
    data_status: str = DATA_STATUS_NOT_INTEGRATED
    data_gaps: List[str] = Field(default_factory=list)


class CanvasAssetDownloadRequest(BaseModel):
    """``POST /api/canvas-assets/download`` 请求体。"""

    model_config = ConfigDict(extra="ignore")

    asset_ids: List[str] = Field(default_factory=list)
    filename: Optional[str] = None


class CanvasAssetAttachRequest(BaseModel):
    """``POST /api/canvases/assets`` 请求体（把素材挂接到画布）。"""

    model_config = ConfigDict(extra="ignore")

    asset_ids: List[str] = Field(default_factory=list)
    canvas_id: Optional[str] = None
    new_title: Optional[str] = None
    new_kind: Optional[str] = None
    expected_version: Optional[int] = Field(None, ge=1)


class ReferenceCanvasCreateRequest(BaseModel):
    """``POST /api/reference-canvases`` 请求体（参考画布保存）。"""

    model_config = ConfigDict(extra="ignore")

    canvas_id: Optional[str] = None
    title: str = Field(..., min_length=1, description="参考画布标题")
    items: List[Dict[str, Any]] = Field(default_factory=list)
    annotations: List[Dict[str, Any]] = Field(default_factory=list)
    drawings: List[Dict[str, Any]] = Field(default_factory=list)
    groups: List[Dict[str, Any]] = Field(default_factory=list)
    viewport: Dict[str, Any] = Field(default_factory=dict)
    expected_version: Optional[int] = Field(None, ge=1)


class ReferenceCanvasResponse(BaseModel):
    """参考画布保存结果包装。"""

    canvas: Dict[str, Any]


class ReferenceCanvasListResponse(BaseModel):
    """参考画布列表响应；无内容时必须是空数组。"""

    canvases: List[Dict[str, Any]] = Field(default_factory=list)
    data_status: str = DATA_STATUS_OK
    data_gaps: List[str] = Field(default_factory=list)


class SharedFolderCreateRequest(BaseModel):
    """``POST /api/shared-folders`` 请求体。"""

    model_config = ConfigDict(extra="ignore")

    path: str = Field(..., min_length=1, description="项目目录内的相对路径")
    name: Optional[str] = None

    @field_validator("path")
    @classmethod
    def _normalize_path(cls, value: str) -> str:
        """拒绝绝对路径与上跳路径，其余统一为正斜杠形式。"""
        normalized = value.strip().replace("\\", "/")
        if not normalized:
            raise ValueError("共享文件夹路径不能为空")
        if normalized.startswith("/") or normalized.startswith("//"):
            raise ValueError("共享文件夹路径必须是项目目录内的相对路径")
        if re.match(r"^[A-Za-z]:", normalized):
            raise ValueError("共享文件夹路径不得包含盘符")
        if normalized.startswith("~"):
            raise ValueError("共享文件夹路径不得使用用户主目录简写")
        segments = [segment for segment in normalized.split("/") if segment not in ("", ".")]
        if any(segment == ".." for segment in segments):
            raise ValueError("共享文件夹路径不得包含上跳片段")
        return "/".join(segments)


class SharedFolderResponse(BaseModel):
    """单个共享文件夹返回包装。"""

    folder: Dict[str, Any]


class SharedFolderListResponse(BaseModel):
    """共享文件夹列表响应；无内容时必须是空数组。"""

    folders: List[Dict[str, Any]] = Field(default_factory=list)
    revision: int = Field(..., ge=1)
    data_status: str = DATA_STATUS_OK
    data_gaps: List[str] = Field(default_factory=list)


class SharedFolderTreeResponse(BaseModel):
    """共享文件夹树响应。

    本阶段**未接入**真实目录扫描，因此树必须为空并如实标记 not_integrated；
    绝不编造文件 / 子目录条目。
    """

    folder: Dict[str, Any]
    tree: Dict[str, Any]
    data_status: str = DATA_STATUS_NOT_INTEGRATED
    data_gaps: List[str] = Field(default_factory=list)


class SharedFolderDeleteResponse(BaseModel):
    """共享文件夹移除结果。"""

    deleted_folder_id: str
    revision: int = Field(..., ge=1)


class VideoTaskListResponse(BaseModel):
    """视频任务列表响应；无真实渲染后端时必须是空数组。"""

    video_tasks: List[Dict[str, Any]] = Field(default_factory=list)
    data_status: str = DATA_STATUS_NOT_INTEGRATED
    data_gaps: List[str] = Field(default_factory=list)
