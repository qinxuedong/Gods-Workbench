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

"""画布闭环领域服务（Phase 10E）。

严格对齐 ``docs/contracts/CANVAS-CLOSURE-INTERFACE-CATALOG.yaml``（version: p10e-frozen-1）。

三条硬口径：

1. **零伪造**：无真实来源的集合一律返回空数组并如实标记 ``not_integrated``，
   禁止预置演示条目、禁止编造文件树、禁止编造视频进度。
2. **fail-closed**：视频渲染、画布素材打包下载、素材挂接、共享文件夹目录扫描与导入
   在本阶段**均无真实后端**，一律返回 503 + 固定错误码，绝不返回假成功。
3. **CAS 与稳定 ID**：所有写操作校验 ``expected_version``，冲突返回 409；
   ID 使用 ``canvas_id`` / ``folder_id``（``fold_NNNN`` 确定性序号）/ ``video_task_id`` / ``asset_id``。
"""

from __future__ import annotations

import copy
import threading
import time
from typing import Any, Dict, List, Optional

from gw.canvas_closure.models import (
    CANVAS_ASSET_ATTACH_NOT_INTEGRATED,
    CANVAS_ASSET_DOWNLOAD_NOT_INTEGRATED,
    DATA_STATUS_NOT_INTEGRATED,
    DATA_STATUS_OK,
    GAP_ASSET_INDEX_SOURCE,
    GAP_SHARED_FOLDER_TREE,
    GAP_VIDEO_RENDERER,
    SHARED_FOLDER_IMPORT_NOT_INTEGRATED,
    SHARED_FOLDER_NOT_FOUND,
    VIDEO_RENDERER_NOT_INTEGRATED,
    VIDEO_TASK_NOT_FOUND,
)
from gw.core.errors import CleanroomException
from gw.god_canvas.models import CanvasCreateRequest, CanvasMode
from gw.god_canvas.service import (
    GodCanvasService,
    default_god_canvas_service,
)

#: 画布素材索引的分类，固定三类，不新增臆造分类。
CANVAS_ASSET_CATEGORIES = (
    ("smart", "智能画布"),
    ("classic", "普通画布"),
    ("reference", "参考画布"),
)


class CanvasClosureService:
    """画布闭环服务：画布元信息 / 回收站、参考画布、画布素材索引、视频任务。

    本服务**不**自建画布存储：所有画布实体读写都委托给同一个
    ``GodCanvasService`` 实例（默认单例），避免画布列表与回收站视图分裂。
    """

    def __init__(
        self,
        canvas_service: Optional[GodCanvasService] = None,
        shared_folder_service: Optional["SharedFolderService"] = None,
    ):
        #: 视频任务本阶段无渲染后端，恒为空；保留显式空集合以免被误填演示数据。
        self._video_tasks: Dict[str, Dict[str, Any]] = {}
        self._canvas_service = canvas_service or default_god_canvas_service
        #: 共享文件夹登记共享同一份进程内单例，避免路由层与服务层登记分裂。
        self._shared_folder_service = (
            shared_folder_service
            if shared_folder_service is not None
            else default_shared_folder_service
        )

    # ------------------------------------------------------------------
    # 画布元信息 / 生命周期
    # ------------------------------------------------------------------

    def update_canvas_meta(
        self,
        canvas_id: str,
        patch: Dict[str, Any],
        expected_version: Optional[int] = None,
    ) -> Dict[str, Any]:
        """更新画布元信息（标题 / 实体绑定 / 项目归属等）。"""
        return self._canvas_service.update_canvas_meta(
            canvas_id, patch, expected_version=expected_version
        )

    def touch_canvas(
        self,
        canvas_id: str,
        operation: str,
        expected_version: Optional[int] = None,
    ) -> Dict[str, Any]:
        """归档 / 解归档画布。"""
        return self._canvas_service.touch_canvas(
            canvas_id, operation, expected_version=expected_version
        )

    def move_to_trash(self, canvas_id: str, expected_version: Optional[int] = None) -> Dict[str, Any]:
        """把画布移入回收站（软删除，可恢复）。"""
        return self._canvas_service.move_to_trash(canvas_id, expected_version=expected_version)

    def restore_from_trash(self, canvas_id: str, expected_version: Optional[int] = None) -> Dict[str, Any]:
        """从回收站恢复画布。"""
        return self._canvas_service.restore_from_trash(canvas_id, expected_version=expected_version)

    def purge_canvas(self, canvas_id: str) -> Dict[str, Any]:
        """彻底删除画布；仅允许回收站中的画布。"""
        return self._canvas_service.purge_canvas(canvas_id)

    def list_trash(self, view: str = "deleted") -> Dict[str, Any]:
        """回收站 / 归档列表；无内容时 ``canvases`` 必须是空数组。"""
        canvases = self._canvas_service.list_trash(view=view)
        return {
            "canvases": canvases,
            "data_status": DATA_STATUS_OK,
            "data_gaps": [],
        }

    # ------------------------------------------------------------------
    # 参考画布
    # ------------------------------------------------------------------

    def list_reference_canvases(self) -> Dict[str, Any]:
        """列出参考画布；无内容时必须是空数组，不预置演示画布。"""
        entries = [
            entry
            for entry in self._canvas_service.list_lifecycle()
            if entry.get("kind") == "reference"
        ]
        return {"canvases": entries, "data_status": DATA_STATUS_OK, "data_gaps": []}

    def save_reference_canvas(
        self,
        title: str,
        items: List[Dict[str, Any]],
        annotations: List[Dict[str, Any]],
        drawings: List[Dict[str, Any]],
        groups: List[Dict[str, Any]],
        viewport: Dict[str, Any],
        canvas_id: Optional[str] = None,
        expected_version: Optional[int] = None,
    ) -> Dict[str, Any]:
        """保存参考画布（新建或覆盖）。

        本方法只登记**调用方提交的**画布内容，绝不编造素材 URL / 缩略图 / 尺寸。
        """
        payload = {
            "items": copy.deepcopy(items),
            "annotations": copy.deepcopy(annotations),
            "drawings": copy.deepcopy(drawings),
            "groups": copy.deepcopy(groups),
            "viewport": copy.deepcopy(viewport),
            "saved_at": int(time.time()),
        }

        if canvas_id:
            existing = self._canvas_service.get_canvas(canvas_id)
            if (self._canvas_service.ensure_lifecycle(canvas_id) or {}).get("kind") != "reference":
                raise CleanroomException(
                    status_code=409,
                    code="CANVAS_KIND_MISMATCH",
                    message="目标画布不是参考画布，拒绝覆盖",
                )
            updated = self._canvas_service.update_canvas_meta(
                canvas_id,
                {"title": title, "reference_payload": payload},
                expected_version=expected_version if expected_version is not None else existing.version,
            )
            return {"canvas": updated}

        created = self._canvas_service.create_canvas(
            CanvasCreateRequest(
                project_id="default",
                title=title,
                mode=CanvasMode.CLASSIC,
            )
        )
        new_canvas_id = created.canvas_id
        self._canvas_service.set_canvas_kind(new_canvas_id, "reference")
        updated = self._canvas_service.update_canvas_meta(
            new_canvas_id,
            {"reference_payload": payload},
            expected_version=created.version,
        )
        return {"canvas": updated}

    # ------------------------------------------------------------------
    # 画布素材索引
    # ------------------------------------------------------------------

    def get_canvas_assets(self) -> Dict[str, Any]:
        """返回画布资产索引。

        ``canvases`` 来自真实画布服务；``items`` 需要素材注册表（本阶段未接入），
        因此**必须为空**并如实标记 ``data_gaps``，绝不编造素材条目。
        """
        lifecycle = self._canvas_service.list_lifecycle()
        categories = []
        for category_id, name in CANVAS_ASSET_CATEGORIES:
            categories.append(
                {
                    "id": category_id,
                    "name": name,
                    "count": len([entry for entry in lifecycle if entry.get("kind") == category_id]),
                }
            )
        return {
            "categories": categories,
            "canvases": lifecycle,
            "items": [],
            "data_status": DATA_STATUS_NOT_INTEGRATED,
            "data_gaps": [GAP_ASSET_INDEX_SOURCE],
        }

    # ------------------------------------------------------------------
    # 共享文件夹
    # ------------------------------------------------------------------

    def list_shared_folders(self) -> Dict[str, Any]:
        """列出已登记的共享文件夹；无内容时必须是空数组。"""
        return self._shared_folder_service.list_folders()

    def create_shared_folder(self, path: str, name: Optional[str] = None) -> Dict[str, Any]:
        """登记共享文件夹（仅登记项目目录内的相对路径，不扫描、不复制）。"""
        return self._shared_folder_service.create_folder(path=path, name=name)

    def delete_shared_folder(self, folder_id: str) -> Dict[str, Any]:
        """移除共享文件夹登记。"""
        return self._shared_folder_service.delete_folder(folder_id)

    def get_shared_folder_tree(self, folder_id: str) -> Dict[str, Any]:
        """读取共享文件夹树。

        本阶段**未接入**真实目录扫描（存储根目录仍未配置，见 Phase 10D
        ``configured: false``），因此必须 fail-closed，绝不返回空的假目录树。
        """
        self._shared_folder_service.get_folder(folder_id)
        raise CleanroomException(
            status_code=503,
            code="SHARED_FOLDER_TREE_NOT_INTEGRATED",
            message="共享文件夹目录扫描尚未接入，本阶段不返回目录树",
            extra={"data_status": DATA_STATUS_NOT_INTEGRATED, "data_gaps": [GAP_SHARED_FOLDER_TREE]},
        )

    # ------------------------------------------------------------------
    # 视频任务（本阶段 fail-closed）
    # ------------------------------------------------------------------

    def list_video_tasks(self) -> Dict[str, Any]:
        """列出视频任务；无真实渲染后端时必须是空数组。"""
        return {
            "video_tasks": list(self._video_tasks.values()),
            "data_status": DATA_STATUS_NOT_INTEGRATED,
            "data_gaps": [GAP_VIDEO_RENDERER],
        }

    def get_video_task(self, video_task_id: str) -> Dict[str, Any]:
        """查询单个视频任务；不存在必须 404，禁止编造进度。"""
        task = self._video_tasks.get(video_task_id)
        if task is None:
            raise CleanroomException(
                status_code=404,
                code=VIDEO_TASK_NOT_FOUND,
                message=f"视频任务 {video_task_id} 不存在",
            )
        return task

    def create_video_task(self) -> Dict[str, Any]:
        """创建视频任务：无渲染后端，必须 fail-closed。"""
        raise CleanroomException(
            status_code=503,
            code=VIDEO_RENDERER_NOT_INTEGRATED,
            message="视频渲染后端尚未接入，本阶段不接收视频任务",
            extra={"data_status": DATA_STATUS_NOT_INTEGRATED, "data_gaps": [GAP_VIDEO_RENDERER]},
        )

    # ------------------------------------------------------------------
    # 未接入能力（显式 fail-closed，禁止假成功）
    # ------------------------------------------------------------------

    @staticmethod
    def raise_canvas_asset_download_not_integrated() -> None:
        """画布素材打包下载无真实文件后端，必须 fail-closed。"""
        raise CleanroomException(
            status_code=503,
            code=CANVAS_ASSET_DOWNLOAD_NOT_INTEGRATED,
            message="画布素材打包下载尚未接入，本阶段不生成压缩包",
            extra={"data_status": DATA_STATUS_NOT_INTEGRATED, "data_gaps": [GAP_ASSET_INDEX_SOURCE]},
        )

    @staticmethod
    def raise_canvas_asset_attach_not_integrated() -> None:
        """素材挂接画布需要素材注册表，本阶段未接入，必须 fail-closed。"""
        raise CleanroomException(
            status_code=503,
            code=CANVAS_ASSET_ATTACH_NOT_INTEGRATED,
            message="素材注册表尚未接入，本阶段不执行素材挂接",
            extra={"data_status": DATA_STATUS_NOT_INTEGRATED, "data_gaps": [GAP_ASSET_INDEX_SOURCE]},
        )

    @staticmethod
    def raise_shared_folder_import_not_integrated() -> None:
        """共享文件夹导入需要真实文件复制与素材库写入，本阶段未接入。"""
        raise CleanroomException(
            status_code=503,
            code=SHARED_FOLDER_IMPORT_NOT_INTEGRATED,
            message="共享文件夹导入尚未接入，本阶段不复制任何文件",
            extra={"data_status": DATA_STATUS_NOT_INTEGRATED, "data_gaps": [GAP_SHARED_FOLDER_TREE]},
        )

class SharedFolderService:
    """共享文件夹登记服务（进程内内存，确定性 folder_id）。"""

    def __init__(self):
        self._lock = threading.Lock()
        self._folders: Dict[str, Dict[str, Any]] = {}
        self._order: List[str] = []
        self._sequence = 0
        self._revision = 1

    @staticmethod
    def _next_id(sequence: int) -> str:
        """确定性序号 ID；禁止随机数 / uuid / 时间戳。"""
        return f"fold_{sequence:04d}"

    def list_folders(self) -> Dict[str, Any]:
        """返回已登记文件夹；无内容时必须是空数组。"""
        with self._lock:
            folders = [copy.deepcopy(self._folders[folder_id]) for folder_id in self._order]
            return {
                "folders": folders,
                "revision": self._revision,
                "data_status": DATA_STATUS_OK,
                "data_gaps": [],
            }

    def get_folder(self, folder_id: str) -> Dict[str, Any]:
        """读取目标文件夹登记；不存在必须 404。"""
        with self._lock:
            folder = self._folders.get(folder_id)
            if folder is None:
                raise CleanroomException(
                    status_code=404,
                    code=SHARED_FOLDER_NOT_FOUND,
                    message=f"共享文件夹 {folder_id} 不存在",
                )
            return copy.deepcopy(folder)

    def create_folder(self, path: str, name: Optional[str] = None) -> Dict[str, Any]:
        """登记共享文件夹；同一路径重复登记返回既有登记，不重复占位。"""
        with self._lock:
            for folder_id in self._order:
                if self._folders[folder_id]["path"] == path:
                    return {"folder": copy.deepcopy(self._folders[folder_id])}
            self._sequence += 1
            folder_id = self._next_id(self._sequence)
            now = int(time.time())
            folder = {
                "folder_id": folder_id,
                "id": folder_id,
                "name": name or path.rsplit("/", 1)[-1],
                "path": path,
                "created_at": now,
                "updated_at": now,
            }
            self._folders[folder_id] = folder
            self._order.append(folder_id)
            self._revision += 1
            return {"folder": copy.deepcopy(folder)}

    def delete_folder(self, folder_id: str) -> Dict[str, Any]:
        """移除登记；不存在必须 404。"""
        with self._lock:
            if folder_id not in self._folders:
                raise CleanroomException(
                    status_code=404,
                    code=SHARED_FOLDER_NOT_FOUND,
                    message=f"共享文件夹 {folder_id} 不存在",
                )
            del self._folders[folder_id]
            self._order.remove(folder_id)
            self._revision += 1
            return {"deleted_folder_id": folder_id, "revision": self._revision}


# 兼容别名与单例实例
# 注意：共享文件夹单例必须先于画布闭环单例创建，后者构造时会引用前者。
default_shared_folder_service = SharedFolderService()
default_canvas_closure_service = CanvasClosureService()
