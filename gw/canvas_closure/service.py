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
from typing import Any

from gw.asset_registry.service import (
    AssetRegistryService,
    default_asset_registry_service,
)
from gw.canvas_closure.models import (
    CANVAS_ASSET_ATTACH_NOT_INTEGRATED,
    CANVAS_ASSET_DOWNLOAD_NOT_INTEGRATED,
    DATA_STATUS_NOT_INTEGRATED,
    DATA_STATUS_OK,
    GAP_ASSET_INDEX_SOURCE,
    GAP_CANVAS_ASSET_NODE_REFS_UNRESOLVED,
    GAP_SHARED_FOLDER_TREE,
    GAP_VIDEO_RENDERER,
    SHARED_FOLDER_IMPORT_NOT_INTEGRATED,
    SHARED_FOLDER_NOT_FOUND,
    VIDEO_RENDERER_NOT_INTEGRATED,
    VIDEO_TASK_NOT_FOUND,
)
from gw.core.errors import CanvasVersionConflictException, CleanroomException
from gw.god_canvas.models import CanvasCreateRequest, CanvasMode
from gw.god_canvas.service import (
    GodCanvasService,
    default_god_canvas_service,
)

#: 画布素材索引的分类，固定三类，不新增臆造分类。
#: 画布素材索引的分类，固定三类，不新增臆造分类。
CANVAS_ASSET_CATEGORIES = (
    ("smart", "智能画布"),
    ("classic", "普通画布"),
    ("reference", "参考画布"),
)


def _registry_media_url(record: dict[str, Any], asset_id: str) -> str:
    """按统一注册表投影口径推导媒体回读地址；无登记载体时留空不编造。

    与前端 ``normalizeRegistryAsset`` 同一规则：本机登记素材
    （``storage_root_id`` / ``stored_name``）经 ``/media`` 端点回读真实文件。
    """
    if record.get("is_remote"):
        return ""
    if record.get("public_url"):
        return str(record["public_url"])
    if record.get("url"):
        return str(record["url"])
    if record.get("storage_root_id") or record.get("stored_name"):
        return f"/api/asset-registry/assets/{asset_id}/media"
    return ""


def _registry_kind_label(kind: str) -> str:
    """注册表 kind → 前端画布资产视图的展示类型映射。

    前端画布资产视图只有 图片 / 视频 / 音频 / 文本 四类徽标；
    ``document``/``text`` → ``text``，其余非媒体类并入 ``image`` 桶（与前端默认一致）。
    """
    normalized = kind.strip().lower()
    if normalized == "video":
        return "video"
    if normalized == "audio":
        return "audio"
    if normalized in {"document", "text"}:
        return "text"
    return "image"


class CanvasClosureService:
    """画布闭环服务：画布元信息 / 回收站、参考画布、画布素材索引、视频任务。

    本服务**不**自建画布存储：所有画布实体读写都委托给同一个
    ``GodCanvasService`` 实例（默认单例），避免画布列表与回收站视图分裂。
    """

    def __init__(
        self,
        canvas_service: GodCanvasService | None = None,
        shared_folder_service: SharedFolderService | None = None,
        asset_registry_service: AssetRegistryService | None = None,
    ):
        #: 视频任务本阶段无渲染后端，恒为空；保留显式空集合以免被误填演示数据。
        self._video_tasks: dict[str, dict[str, Any]] = {}
        self._canvas_service = canvas_service or default_god_canvas_service
        #: 共享文件夹登记共享同一份进程内单例，避免路由层与服务层登记分裂。
        self._shared_folder_service = (
            shared_folder_service
            if shared_folder_service is not None
            else default_shared_folder_service
        )
        # 统一回收只编排已登记的稳定资产快照；不接管素材文件或外部服务。
        self._asset_registry_service = (
            asset_registry_service or default_asset_registry_service
        )
        self._unified_recycle: dict[str, dict[str, Any]] = {}
        self._unified_recycle_sequence = 0
        # 只保存显式幂等键对应的本地恢复事实，重放不再触发第二次副作用。
        self._unified_idempotent_results: dict[str, dict[str, Any]] = {}

    # ------------------------------------------------------------------
    # 画布元信息 / 生命周期
    # ------------------------------------------------------------------

    def update_canvas_meta(
        self,
        canvas_id: str,
        patch: dict[str, Any],
        expected_version: int | None = None,
    ) -> dict[str, Any]:
        """更新画布元信息（标题 / 实体绑定 / 项目归属等）。"""
        return self._canvas_service.update_canvas_meta(
            canvas_id, patch, expected_version=expected_version
        )

    def touch_canvas(
        self,
        canvas_id: str,
        operation: str,
        expected_version: int | None = None,
    ) -> dict[str, Any]:
        """归档 / 解归档画布。"""
        return self._canvas_service.touch_canvas(
            canvas_id, operation, expected_version=expected_version
        )

    def move_to_trash(
        self,
        canvas_id: str,
        expected_version: int | None = None,
        *,
        asset_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        """把画布移入回收站（软删除，可恢复）。"""
        if asset_ids is not None:
            return self.recycle_with_references(
                canvas_id,
                asset_ids=asset_ids,
                expected_version=expected_version,
            )
        return self._canvas_service.move_to_trash(
            canvas_id, expected_version=expected_version
        )

    def restore_from_trash(
        self,
        canvas_id: str,
        expected_version: int | None = None,
        *,
        recycle_entry_id: str | None = None,
    ) -> dict[str, Any]:
        """从回收站恢复画布。"""
        if recycle_entry_id is not None:
            return self.restore_recycled(
                recycle_entry_id, expected_version=expected_version
            )
        return self._canvas_service.restore_from_trash(
            canvas_id, expected_version=expected_version
        )

    def purge_canvas(
        self, canvas_id: str, expected_version: int | None = None
    ) -> dict[str, Any]:
        """彻底删除画布；仅允许回收站中的画布。"""
        return self._canvas_service.purge_canvas(
            canvas_id, expected_version=expected_version
        )

    def list_trash(self, view: str = "deleted") -> dict[str, Any]:
        """回收站 / 归档列表；无内容时 ``canvases`` 必须是空数组。"""
        canvases = self._canvas_service.list_trash(view=view)
        return {
            "canvases": canvases,
            "data_status": DATA_STATUS_OK,
            "data_gaps": [],
        }

    # ------------------------------------------------------------------
    # C8 统一回收 / 恢复
    # ------------------------------------------------------------------

    @staticmethod
    def _partial_failure(
        asset_id: str, code: str, *, c9: bool = False
    ) -> dict[str, str]:
        failure = {
            "asset_id": asset_id,
            "code": code,
            "next_step": "刷新资产来源后重试",
        }
        if c9:
            failure["next_action"] = "restore_reference"
        return failure

    def _next_unified_recycle_id(self) -> str:
        self._unified_recycle_sequence += 1
        return f"u_rcy_{self._unified_recycle_sequence:04d}"

    @staticmethod
    def _now_iso() -> str:
        return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    @staticmethod
    def _preserved_subjects(
        references: dict[str, Any],
        *,
        canvas_id: str,
        assets: list[dict[str, Any]] | None = None,
    ) -> list[dict[str, str]]:
        """只投影调用方已提供或快照中已存在的稳定 ID。"""
        allowed = (
            "project_id",
            "entity_id",
            "episode_id",
            "scene_id",
            "shot_id",
            "canvas_id",
            "job_id",
            "asset_id",
            "version_id",
            "review_id",
            "decision_id",
            "delivery_id",
        )
        subjects: list[dict[str, str]] = []

        def add(key: str, value: Any) -> None:
            if value is None or isinstance(value, (dict, list, tuple, set)):
                return
            item = {key: str(value)}
            if item not in subjects:
                subjects.append(item)

        add("canvas_id", canvas_id)
        for key in allowed:
            add(key, references.get(key))
        for asset in assets or []:
            add("asset_id", asset.get("asset_id"))
            add("version_id", asset.get("version_id"))
        return subjects

    @classmethod
    def _recovery_projection(
        cls,
        *,
        operation: str,
        entry_id: str,
        state: str,
        preserved_subjects: list[dict[str, str]],
        failed_scopes: list[str] | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        return {
            "operation": operation,
            "recycle_entry_id": entry_id,
            "state": state,
            "preserved_subjects": copy.deepcopy(preserved_subjects),
            "failed_scopes": list(failed_scopes or []),
            "idempotency_key": idempotency_key or f"{operation}:{entry_id}",
            "occurred_at": cls._now_iso(),
        }

    def recycle_entry(self, entry_id: str) -> dict[str, Any]:
        """读取统一回收条目，供部分失败后的安全重试与证据回读。"""
        entry = self._unified_recycle.get(entry_id)
        if entry is None:
            raise CleanroomException(
                404, "UNIFIED_RECYCLE_ENTRY_NOT_FOUND", "统一回收条目不存在"
            )
        return copy.deepcopy(entry)

    def recycle_with_references(
        self,
        canvas_id: str,
        asset_ids: list[str] | None = None,
        expected_version: int | None = None,
        *,
        references: dict[str, Any] | None = None,
        idempotency_key: str | None = None,
        asset_expected_version: int | None = None,
    ) -> dict[str, Any]:
        """原子编排画布与统一资产回收，保留精确来源和引用快照。

        资产缺失时在画布变更前明确返回部分失败；任一已完成资产删除后画布 CAS
        失败，会尽力按原回收条目恢复资产，避免留下半程成功。
        """
        if (
            idempotency_key
            and f"recycle:{idempotency_key}" in self._unified_idempotent_results
        ):
            return copy.deepcopy(
                self._unified_idempotent_results[f"recycle:{idempotency_key}"]
            )
        ids = list(dict.fromkeys(str(asset_id) for asset_id in (asset_ids or [])))
        canvas = self._canvas_service.get_canvas_meta(canvas_id)
        # 先做现有画布 CAS 入参和版本校验，避免资产已回收后才发现画布冲突。
        self._canvas_service._require_version(expected_version)
        if expected_version is not None and canvas["version"] != expected_version:
            raise CanvasVersionConflictException(
                expected_version, canvas["version"], canvas_id
            )
        snapshots: dict[str, dict[str, Any]] = {}
        failures: list[dict[str, str]] = []
        for asset_id in ids:
            try:
                snapshots[asset_id] = self._asset_registry_service.get_asset(asset_id)
            except CleanroomException as exc:
                failures.append(
                    self._partial_failure(
                        asset_id,
                        exc.code,
                        c9=bool(references or idempotency_key),
                    )
                )
        if failures:
            if references or idempotency_key:
                recovery = self._recovery_projection(
                    operation="recycle",
                    entry_id="",
                    state="partially_recovered",
                    preserved_subjects=[{"canvas_id": canvas_id}],
                    failed_scopes=["asset"],
                    idempotency_key=idempotency_key or f"recycle:{canvas_id}",
                )
                raise CleanroomException(
                    409,
                    "UNIFIED_RECYCLE_PARTIAL_FAILURE",
                    "统一回收未执行：部分资产来源不可用",
                    extra={
                        "partial_failures": failures,
                        "closure_status": "blocked",
                        "recovery": recovery,
                        "next_action": "restore_reference",
                    },
                )
            raise CleanroomException(
                409,
                "UNIFIED_RECYCLE_PARTIAL_FAILURE",
                "统一回收未执行：部分资产来源不可用",
                extra={"partial_failures": failures},
            )

        reference_payload = self._canvas_service.get_reference_payload(canvas_id) or {}
        reference_snapshot: dict[str, Any] = (
            copy.deepcopy(references) if isinstance(references, dict) else {}
        )
        reference_snapshot["canvas_id"] = canvas_id
        reference_snapshot["canvas_version"] = canvas["version"]
        if len(ids) == 1:
            reference_snapshot["source_ref"] = copy.deepcopy(
                snapshots[ids[0]].get("source_ref") or {}
            )
        elif ids:
            reference_snapshot["source_refs"] = {
                asset_id: copy.deepcopy(snapshots[asset_id].get("source_ref") or {})
                for asset_id in ids
            }
        deleted_assets: list[dict[str, str]] = []
        asset_expected = asset_expected_version
        try:
            for asset_id in ids:
                deleted = self._asset_registry_service.delete_asset(
                    asset_id,
                    expected_version=asset_expected,
                    idempotency_key=(
                        f"{idempotency_key}:asset:{asset_id}"
                        if idempotency_key
                        else None
                    ),
                )
                deleted_assets.append(
                    {"asset_id": asset_id, "entry_id": deleted["entry_id"]}
                )
                if asset_expected is not None:
                    asset_expected = deleted.get("revision")
            trashed = self._canvas_service.move_to_trash(
                canvas_id, expected_version=expected_version
            )
        except Exception as exc:
            rollback_failures: list[dict[str, str]] = []
            for deleted in reversed(deleted_assets):
                try:
                    self._asset_registry_service.restore_from_recycle(
                        deleted["entry_id"]
                    )
                except CleanroomException as rollback_error:
                    rollback_failures.append(
                        self._partial_failure(deleted["asset_id"], rollback_error.code)
                    )
            if rollback_failures:
                raise CleanroomException(
                    500,
                    "UNIFIED_RECYCLE_PARTIAL_FAILURE",
                    "统一回收失败且资产补偿未完成",
                    extra={"partial_failures": rollback_failures},
                ) from exc
            raise

        entry_id = self._next_unified_recycle_id()
        recovery_key = idempotency_key or f"recycle:{entry_id}"
        preserved_subjects = self._preserved_subjects(
            reference_snapshot,
            canvas_id=canvas_id,
            assets=[snapshots[asset_id] for asset_id in ids],
        )
        recovery = self._recovery_projection(
            operation="recycle",
            entry_id=entry_id,
            state="recovery_required",
            preserved_subjects=preserved_subjects,
            idempotency_key=recovery_key,
        )
        entry = {
            "entry_id": entry_id,
            "kind": "unified",
            "canvas_id": canvas_id,
            "canvas_snapshot": copy.deepcopy(trashed),
            "reference_payload": copy.deepcopy(reference_payload),
            "references": copy.deepcopy(reference_snapshot),
            "assets": copy.deepcopy(deleted_assets),
            "preserved_subjects": copy.deepcopy(preserved_subjects),
            "idempotency_key": recovery_key,
            "closure_status": "recovery_required",
            "recovery": copy.deepcopy(recovery),
            "next_action": "restore_reference",
        }
        self._unified_recycle[entry_id] = entry
        result = {
            "status": "completed",
            "recycle_entry_id": entry_id,
            "canvas": copy.deepcopy(trashed),
            "assets": copy.deepcopy(deleted_assets),
            "references": copy.deepcopy(reference_snapshot),
            "reference_payload": copy.deepcopy(reference_payload),
            "closure_status": "recovery_required",
            "blocker": None,
            "recovery": copy.deepcopy(recovery),
            "next_action": "restore_reference",
            "data_status": DATA_STATUS_OK,
            "data_gaps": [],
        }
        if idempotency_key:
            self._unified_idempotent_results[f"recycle:{idempotency_key}"] = (
                copy.deepcopy(result)
            )
        return result

    # 任务书使用“统一回收”语义；保留清晰别名，避免新建第二套状态机。
    delete_and_recycle = recycle_with_references
    recycle_canvas_with_assets = recycle_with_references

    def restore_recycled(
        self,
        entry_id: str,
        expected_version: int | None = None,
        *,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """恢复统一回收条目，保持画布、资产 ID、来源和引用快照不变。"""
        if (
            idempotency_key
            and f"restore:{idempotency_key}" in self._unified_idempotent_results
        ):
            return copy.deepcopy(
                self._unified_idempotent_results[f"restore:{idempotency_key}"]
            )
        entry = self._unified_recycle.get(entry_id)
        if entry is None:
            raise CleanroomException(
                404, "UNIFIED_RECYCLE_ENTRY_NOT_FOUND", "统一回收条目不存在"
            )

        current = self._canvas_service.get_canvas_meta(entry["canvas_id"])
        self._canvas_service._require_version(expected_version)
        if current.get("deleted_at") is None:
            raise CleanroomException(
                409, "UNIFIED_RECYCLE_CONFLICT", "画布不在回收站中"
            )
        if expected_version is not None and current["version"] != expected_version:
            raise CanvasVersionConflictException(
                expected_version, current["version"], entry["canvas_id"]
            )

        # 先做所有资产恢复预检，冲突时画布保持在回收站中。
        for asset in entry["assets"]:
            try:
                self._asset_registry_service.assert_recycle_entry_restorable(
                    asset["entry_id"]
                )
            except CleanroomException as exc:
                recovery_key = idempotency_key or f"restore:{entry_id}"
                preserved_subjects = copy.deepcopy(
                    entry.get("preserved_subjects") or []
                )
                recovery = self._recovery_projection(
                    operation="restore",
                    entry_id=entry_id,
                    state="partially_recovered",
                    preserved_subjects=preserved_subjects,
                    failed_scopes=["asset"],
                    idempotency_key=recovery_key,
                )
                raise CleanroomException(
                    409 if exc.status_code == 409 else exc.status_code,
                    "UNIFIED_RECYCLE_PARTIAL_FAILURE",
                    "统一恢复未执行：部分资产无法恢复",
                    extra={
                        "partial_failures": [
                            self._partial_failure(
                                asset["asset_id"],
                                exc.code,
                                c9=bool(idempotency_key),
                            )
                        ],
                        "closure_status": "partially_recovered",
                        "recovery": recovery,
                        "next_action": "retry_recovery",
                    },
                ) from exc

        restored_canvas = self._canvas_service.restore_from_trash(
            entry["canvas_id"], expected_version=expected_version
        )
        restored_assets: list[str] = []
        try:
            for asset in entry["assets"]:
                self._asset_registry_service.restore_from_recycle(
                    asset["entry_id"],
                    idempotency_key=(
                        f"{idempotency_key}:asset:{asset['asset_id']}"
                        if idempotency_key
                        else None
                    ),
                )
                restored_assets.append(asset["asset_id"])
        except Exception as exc:
            # 发生意外部分失败时把画布退回回收站，并保留统一条目供人工处理。
            try:
                self._canvas_service.move_to_trash(
                    entry["canvas_id"], expected_version=restored_canvas["version"]
                )
            except Exception:
                pass
            raise CleanroomException(
                500,
                "UNIFIED_RECYCLE_PARTIAL_FAILURE",
                "统一恢复失败，已暴露部分结果",
                extra={
                    "partial_failures": [
                        {
                            "asset_id": asset["asset_id"],
                            "code": getattr(exc, "code", "RESTORE_FAILED"),
                            "next_step": "保留回收条目并人工核对后重试",
                        }
                    ],
                    "closure_status": "partially_recovered",
                    "recovery": self._recovery_projection(
                        operation="restore",
                        entry_id=entry_id,
                        state="partially_recovered",
                        preserved_subjects=copy.deepcopy(
                            entry.get("preserved_subjects") or []
                        ),
                        failed_scopes=["asset"],
                        idempotency_key=idempotency_key or f"restore:{entry_id}",
                    ),
                    "next_action": "retry_recovery",
                },
            ) from exc

        del self._unified_recycle[entry_id]
        recovery_key = idempotency_key or f"restore:{entry_id}"
        recovery = self._recovery_projection(
            operation="restore",
            entry_id=entry_id,
            state="completed",
            preserved_subjects=copy.deepcopy(entry.get("preserved_subjects") or []),
            idempotency_key=recovery_key,
        )
        result = {
            "status": "completed",
            "recycle_entry_id": entry_id,
            "canvas": copy.deepcopy(restored_canvas),
            "asset_ids": restored_assets,
            "references": copy.deepcopy(entry["references"]),
            "reference_payload": copy.deepcopy(entry["reference_payload"]),
            "closure_status": "closed",
            "blocker": None,
            "recovery": recovery,
            "next_action": None,
            "data_status": DATA_STATUS_OK,
            "data_gaps": [],
        }
        if idempotency_key:
            self._unified_idempotent_results[f"restore:{idempotency_key}"] = (
                copy.deepcopy(result)
            )
        return result

    restore_from_unified_recycle = restore_recycled

    # ------------------------------------------------------------------
    # 参考画布
    # ------------------------------------------------------------------

    def list_reference_canvases(self) -> dict[str, Any]:
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
        items: list[dict[str, Any]],
        annotations: list[dict[str, Any]],
        drawings: list[dict[str, Any]],
        groups: list[dict[str, Any]],
        viewport: dict[str, Any],
        canvas_id: str | None = None,
        expected_version: int | None = None,
        owner_key: str | None = None,
    ) -> dict[str, Any]:
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
            self._canvas_service._require_version(expected_version)
            existing = self._canvas_service.get_canvas(canvas_id)
            if (self._canvas_service.ensure_lifecycle(canvas_id) or {}).get(
                "kind"
            ) != "reference":
                raise CleanroomException(
                    status_code=409,
                    code="CANVAS_KIND_MISMATCH",
                    message="目标画布不是参考画布，拒绝覆盖",
                )
            updated = self._canvas_service.update_canvas_meta(
                canvas_id,
                {"title": title, "reference_payload": payload},
                expected_version=expected_version
                if expected_version is not None
                else existing.version,
            )
            return {"canvas": updated}

        if self._canvas_service.persistent:
            # 种类、主体和参考载荷随新画布一次提交，不残留半初始化记录。
            created = self._canvas_service.create_canvas(
                CanvasCreateRequest(
                    project_id="default",
                    title=title,
                    sub_canvas_type="reference",
                    initial_payload={"references": {"reference_canvas": payload}},
                ),
                owner_key=owner_key,
                canvas_kind="reference",
            )
            return {"canvas": self._canvas_service.get_canvas_meta(created.canvas_id)}

        created = self._canvas_service.create_canvas(
            CanvasCreateRequest(
                project_id="default",
                title=title,
                mode=CanvasMode.CLASSIC,
            ),
            owner_key=owner_key,
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

    def get_canvas_assets(self) -> dict[str, Any]:
        """返回画布资产索引（QIN-567 素材索引接线）。

        ``canvases`` 来自真实画布服务；``items`` 由画布产物 join 素材注册表：

        - 智能画布 / 普通画布：拓扑节点 ``refs.asset_id`` join 素材注册表；
          未登记的引用不编造素材，跳过并如实标记解析缺口；
        - 参考画布：``reference_payload.items`` 的真实保存条目
          （asset_id / name / media_kind / url）；
        - 注册表媒体回读地址与统一注册表投影同口径：仅对登记了
          ``storage_root_id`` / ``stored_name`` 的本机素材拼接 ``/media`` 回读地址。

        接线完成后缺口标记不再恒为 ``canvas_asset_index_source_not_integrated``。
        """
        lifecycle = self._canvas_service.list_lifecycle()
        registry_index = self._registry_asset_index()
        items: list[dict[str, Any]] = []
        unresolved = 0
        for entry in lifecycle:
            canvas_id = str(entry.get("canvas_id") or "")
            if not canvas_id:
                continue
            per_canvas_items, per_canvas_unresolved = self._canvas_payload_asset_items(
                canvas_id=canvas_id,
                canvas_kind=str(entry.get("kind") or "classic"),
                canvas_title=str(entry.get("title") or ""),
                registry_index=registry_index,
            )
            items.extend(per_canvas_items)
            unresolved += per_canvas_unresolved
        categories = []
        for category_id, name in CANVAS_ASSET_CATEGORIES:
            categories.append(
                {
                    "id": category_id,
                    "name": name,
                    "count": len(
                        [
                            entry
                            for entry in lifecycle
                            if entry.get("kind") == category_id
                        ]
                    ),
                }
            )
        return {
            "categories": categories,
            "canvases": lifecycle,
            "items": items,
            "data_status": DATA_STATUS_OK,
            "data_gaps": [GAP_CANVAS_ASSET_NODE_REFS_UNRESOLVED] if unresolved else [],
        }

    # ------------------------------------------------------------------
    # 共享文件夹
    # ------------------------------------------------------------------

    def _registry_asset_index(self) -> dict[str, dict[str, Any]]:
        """读取素材注册表（json-state 仓库）的资产索引；只读、零伪造。

        仅收录通过统一可见性校验的登记资产；仓库不可读时异常如实上抛，
        不静默降级为空索引（避免把注册表故障伪装成「没有画布资产」）。
        """
        from gw.asset_registry import repository as registry_repo
        from gw.core.asset_visibility import asset_visible

        raw = registry_repo.state().read()
        return {
            asset_id: record
            for asset_id, record in raw.get("assets", {}).items()
            if asset_visible(record)
        }

    def _canvas_payload_asset_items(
        self,
        *,
        canvas_id: str,
        canvas_kind: str,
        canvas_title: str,
        registry_index: dict[str, dict[str, Any]],
    ) -> tuple[list[dict[str, Any]], int]:
        """提取单个画布的资产条目与未解析引用数（QIN-567）。

        - smart / classic：逐拓扑节点 ``refs.asset_id`` join 注册表；
        - reference：逐 ``reference_payload.items`` 取真实保存字段；
        - 未登记的节点引用不编造素材，跳过并计入未解析数。
        """
        items: list[dict[str, Any]] = []
        unresolved = 0
        if canvas_kind == "reference":
            payload = self._canvas_service.get_reference_payload(canvas_id) or {}
            for position, ref_entry in enumerate(payload.get("items") or []):
                if not isinstance(ref_entry, dict):
                    continue
                item = self._reference_item_projection(
                    entry=ref_entry,
                    canvas_id=canvas_id,
                    canvas_title=canvas_title,
                    position=position,
                    registry_index=registry_index,
                )
                if item is not None:
                    items.append(item)
            return items, unresolved

        try:
            topology = self._canvas_service.get_topology(canvas_id)
        except CleanroomException as exc:
            # 没有拓扑的画布（如仅存元信息的归档画布）不含素材引用，如实跳过。
            if exc.status_code != 404:
                raise
            return items, unresolved
        for node in topology.nodes:
            asset_id = ""
            if node.refs is not None:
                asset_id = str(node.refs.asset_id or "")
            if not asset_id:
                continue
            record = registry_index.get(asset_id) if asset_id else None
            if record is None:
                unresolved += 1
                continue
            items.append(
                {
                    "id": f"{canvas_id}::{node.entity_id}",
                    "asset_id": asset_id,
                    "canvas_id": canvas_id,
                    "canvas_title": canvas_title,
                    "canvas_kind": canvas_kind,
                    "name": str(record.get("name") or ""),
                    "url": _registry_media_url(record, asset_id),
                    "kind": _registry_kind_label(str(record.get("kind") or "")),
                    "source": "canvas_node",
                }
            )
        return items, unresolved

    def _reference_item_projection(
        self,
        *,
        entry: dict[str, Any],
        canvas_id: str,
        canvas_title: str,
        position: int,
        registry_index: dict[str, dict[str, Any]],
    ) -> dict[str, str] | None:
        """把参考画布保存条目投影为画布资产索引条目；无可展示事实时返回 None。

        参考画布条目本身携带真实保存的 name / url / media_kind；
        携带 asset_id 且能在注册表解析时，用注册表记录回填缺失的展示字段。
        """
        asset_id = str(entry.get("asset_id") or "").strip()
        name = str(entry.get("name") or "").strip()
        url = str(entry.get("url") or entry.get("source_url") or "").strip()
        record = registry_index.get(asset_id) if asset_id else None
        if record is not None:
            name = name or str(record.get("name") or "")
            url = url or _registry_media_url(record, asset_id)
        if not name and not url:
            # 仅有内部引用键、没有任何可展示事实的条目不进索引，避免编造。
            return None
        raw_kind = str(
            entry.get("media_kind") or (record.get("kind") if record else "") or ""
        )
        key = str(entry.get("reference_key") or entry.get("id") or "")
        if not key:
            key = str(position)
        return {
            "id": f"{canvas_id}::ref::{key}",
            "asset_id": asset_id,
            "canvas_id": canvas_id,
            "canvas_title": canvas_title,
            "canvas_kind": "reference",
            "name": name,
            "url": url,
            "kind": _registry_kind_label(raw_kind),
            "source": "reference_item",
        }

    def list_shared_folders(self) -> dict[str, Any]:
        """列出已登记的共享文件夹；无内容时必须是空数组。"""
        return self._shared_folder_service.list_folders()

    def create_shared_folder(
        self, path: str, name: str | None = None
    ) -> dict[str, Any]:
        """登记共享文件夹（仅登记项目目录内的相对路径，不扫描、不复制）。"""
        return self._shared_folder_service.create_folder(path=path, name=name)

    def delete_shared_folder(self, folder_id: str) -> dict[str, Any]:
        """移除共享文件夹登记。"""
        return self._shared_folder_service.delete_folder(folder_id)

    def get_shared_folder_tree(self, folder_id: str) -> dict[str, Any]:
        """读取共享文件夹树。

        本阶段**未接入**真实目录扫描（存储根目录仍未配置，见 Phase 10D
        ``configured: false``），因此必须 fail-closed，绝不返回空的假目录树。
        """
        self._shared_folder_service.get_folder(folder_id)
        raise CleanroomException(
            status_code=503,
            code="SHARED_FOLDER_TREE_NOT_INTEGRATED",
            message="共享文件夹目录扫描尚未接入，本阶段不返回目录树",
            extra={
                "data_status": DATA_STATUS_NOT_INTEGRATED,
                "data_gaps": [GAP_SHARED_FOLDER_TREE],
            },
        )

    # ------------------------------------------------------------------
    # 视频任务（本阶段 fail-closed）
    # ------------------------------------------------------------------

    def list_video_tasks(self) -> dict[str, Any]:
        """列出视频任务；无真实渲染后端时必须是空数组。"""
        return {
            "video_tasks": list(self._video_tasks.values()),
            "data_status": DATA_STATUS_NOT_INTEGRATED,
            "data_gaps": [GAP_VIDEO_RENDERER],
        }

    def get_video_task(self, video_task_id: str) -> dict[str, Any]:
        """查询单个视频任务；不存在必须 404，禁止编造进度。"""
        task = self._video_tasks.get(video_task_id)
        if task is None:
            raise CleanroomException(
                status_code=404,
                code=VIDEO_TASK_NOT_FOUND,
                message=f"视频任务 {video_task_id} 不存在",
            )
        return task

    def create_video_task(self) -> dict[str, Any]:
        """创建视频任务：无渲染后端，必须 fail-closed。"""
        raise CleanroomException(
            status_code=503,
            code=VIDEO_RENDERER_NOT_INTEGRATED,
            message="视频渲染后端尚未接入，本阶段不接收视频任务",
            extra={
                "data_status": DATA_STATUS_NOT_INTEGRATED,
                "data_gaps": [GAP_VIDEO_RENDERER],
            },
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
            extra={
                "data_status": DATA_STATUS_NOT_INTEGRATED,
                "data_gaps": [GAP_ASSET_INDEX_SOURCE],
            },
        )

    @staticmethod
    def raise_canvas_asset_attach_not_integrated() -> None:
        """素材挂接画布需要素材注册表，本阶段未接入，必须 fail-closed。"""
        raise CleanroomException(
            status_code=503,
            code=CANVAS_ASSET_ATTACH_NOT_INTEGRATED,
            message="素材注册表尚未接入，本阶段不执行素材挂接",
            extra={
                "data_status": DATA_STATUS_NOT_INTEGRATED,
                "data_gaps": [GAP_ASSET_INDEX_SOURCE],
            },
        )

    @staticmethod
    def raise_shared_folder_import_not_integrated() -> None:
        """共享文件夹导入需要真实文件复制与素材库写入，本阶段未接入。"""
        raise CleanroomException(
            status_code=503,
            code=SHARED_FOLDER_IMPORT_NOT_INTEGRATED,
            message="共享文件夹导入尚未接入，本阶段不复制任何文件",
            extra={
                "data_status": DATA_STATUS_NOT_INTEGRATED,
                "data_gaps": [GAP_SHARED_FOLDER_TREE],
            },
        )


class SharedFolderService:
    """共享文件夹登记服务（进程内内存，确定性 folder_id）。"""

    def __init__(self):
        self._lock = threading.Lock()
        self._folders: dict[str, dict[str, Any]] = {}
        self._order: list[str] = []
        self._sequence = 0
        self._revision = 1

    @staticmethod
    def _next_id(sequence: int) -> str:
        """确定性序号 ID；禁止随机数 / uuid / 时间戳。"""
        return f"fold_{sequence:04d}"

    def list_folders(self) -> dict[str, Any]:
        """返回已登记文件夹；无内容时必须是空数组。"""
        with self._lock:
            folders = [
                copy.deepcopy(self._folders[folder_id]) for folder_id in self._order
            ]
            return {
                "folders": folders,
                "revision": self._revision,
                "data_status": DATA_STATUS_OK,
                "data_gaps": [],
            }

    def get_folder(self, folder_id: str) -> dict[str, Any]:
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

    def create_folder(self, path: str, name: str | None = None) -> dict[str, Any]:
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

    def delete_folder(self, folder_id: str) -> dict[str, Any]:
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
