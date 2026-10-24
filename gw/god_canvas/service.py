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

"""god-canvas 核心服务层洁净实现。

统一整合普通画布拓扑读写（切片 B）与智能画布任务编排及状态机（切片 C）。
严格对齐 BEHAVIOR-SPEC-CANVAS.md、BEHAVIOR-SPEC-SMART-CANVAS.md 以及 CANVAS-INTERFACE-CATALOG.yaml 契约规范。
"""

import copy
import json
import logging
import os
import re
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from gw.core.auth import AuthContext, require_authenticated
from gw.core.errors import (
    CanvasVersionConflictException,
    CleanroomException,
    ForbiddenException,
)
from gw.core.storage import JsonState, data_root, now_iso
from gw.god_canvas.godmap import export_to_godmap, parse_godmap_content
from gw.god_canvas.models import (
    CanvasConnection,
    CanvasCreateRequest,
    CanvasImportReport,
    CanvasImportResponse,
    CanvasItem,
    CanvasMode,
    CanvasMutationResult,
    CanvasNode,
    CanvasTopology,
    CanvasTopologyUpdateRequest,
    NodePosition,
    NodeReferences,
)
from gw.god_canvas.tasks import (
    SmartCanvasTaskRequest,
    SmartCanvasTaskResponse,
    TaskStatus,
)

_PERSISTENCE_LOCK = threading.RLock()


class GodCanvasService:
    """god-canvas 统一拓扑与智能任务服务。"""

    def __init__(self, seed_golden_fixture: bool = True, *, persistent: bool = False):
        self._lock = threading.RLock()
        self.persistent = persistent
        # 构造不访问运行目录；真实快照只在请求/显式服务操作时读取。
        self._state = JsonState("canvases_registry", lambda: None)
        self._canvases: dict[str, CanvasItem] = {}
        self._topologies: dict[str, CanvasTopology] = {}
        self._jobs: dict[str, SmartCanvasTaskResponse] = {}
        # 黄金种子无归属：仅内部夹具可读，HTTP不得公开。
        self._job_owners: dict[str, dict[str, str]] = {}
        self._canvas_owners: dict[str, str] = {}
        self._lifecycle: dict[str, dict[str, Any]] = {}
        self._seq = 1 if seed_golden_fixture and not persistent else 0
        self._job_seq = 1 if seed_golden_fixture and not persistent else 0

        if seed_golden_fixture and not persistent:
            # 注入 docs/fixtures/canvas-workflow-minimal.json 黄金夹具种子
            cid = "cv-0001"
            pid = "prj-0001"
            self._canvases[cid] = CanvasItem(
                canvas_id=cid,
                title="基准工作流画布",
                project_id=pid,
                version=1,
                mode=CanvasMode.CLASSIC,
            )
            self._topologies[cid] = CanvasTopology(
                canvas_id=cid,
                version=1,
                nodes=[
                    CanvasNode(
                        entity_id="nd-0001",
                        kind="input",
                        position=NodePosition(x=120.0, y=80.0),
                        refs=NodeReferences(asset_id=None, job_id=None),
                    ),
                    CanvasNode(
                        entity_id="nd-0002",
                        kind="output",
                        position=NodePosition(x=360.0, y=80.0),
                        refs=NodeReferences(asset_id=None, job_id=None),
                    ),
                ],
                connections=[
                    CanvasConnection(
                        connection_id="ln-0001",
                        from_node="nd-0001",
                        to_node="nd-0002",
                    )
                ],
            )
            # 注入 docs/fixtures/canvas-task-accepted-202.json 黄金夹具任务种子
            self._jobs["job-0001"] = SmartCanvasTaskResponse(
                job_id="job-0001",
                state="accepted",
                poll_hint="/api/jobs/job-0001",
            )
            now = int(time.time())
            self._lifecycle[cid] = {
                "kind": "classic",
                "entity_id": None,
                "icon": None,
                "board_x": None,
                "board_y": None,
                "created_at": now,
                "updated_at": now,
                "deleted_at": None,
                "archived_at": None,
            }

    def _snapshot(self) -> dict[str, Any]:
        """注册快照为跨表提交真源，拓扑文件是可重建的读取投影。"""
        return {
            "schema": "gw.canvas-state/v001",
            "seq": self._seq,
            "job_seq": self._job_seq,
            "canvases": {
                k: v.model_dump(mode="json", by_alias=True)
                for k, v in self._canvases.items()
            },
            "topologies": {
                k: v.model_dump(mode="json", by_alias=True)
                for k, v in self._topologies.items()
            },
            "jobs": {k: v.model_dump(mode="json") for k, v in self._jobs.items()},
            "job_owners": copy.deepcopy(self._job_owners),
            "canvas_owners": copy.deepcopy(self._canvas_owners),
            "lifecycle": copy.deepcopy(self._lifecycle),
        }

    def _load_snapshot(self, state: dict[str, Any]) -> None:
        """验证全部表后才替换缓存；损坏状态不得伪装为空库。"""
        if not isinstance(state, dict) or state.get("schema") != "gw.canvas-state/v001":
            raise ValueError("未知画布存储格式")
        for name in (
            "canvases",
            "topologies",
            "jobs",
            "job_owners",
            "canvas_owners",
            "lifecycle",
        ):
            if not isinstance(state.get(name), dict):
                raise ValueError("画布状态表缺失")
        if any(
            type(state.get(k)) is not int or state[k] < 0 for k in ("seq", "job_seq")
        ):
            raise ValueError("画布序号不合法")
        canvases = {
            k: CanvasItem.model_validate(v) for k, v in state["canvases"].items()
        }
        topologies = {
            k: CanvasTopology.model_validate(v) for k, v in state["topologies"].items()
        }
        jobs = {
            k: SmartCanvasTaskResponse.model_validate(v)
            for k, v in state["jobs"].items()
        }
        if set(canvases) != set(topologies):
            raise ValueError("画布与拓扑不一致")
        for cid, item in canvases.items():
            if (
                not re.fullmatch(r"cv-[0-9]{4,}", cid)
                or item.canvas_id != cid
                or topologies[cid].canvas_id != cid
                or item.version != topologies[cid].version
            ):
                raise ValueError("画布标识或版本不一致")
            if item.version < 1 or (
                topologies[cid].project_id is not None
                and topologies[cid].project_id != item.project_id
            ):
                raise ValueError("画布项目或版本不一致")
        for jid, job in jobs.items():
            if (
                not re.fullmatch(r"job-[0-9]{4,}", jid)
                or job.job_id != jid
                or job.state not in {s.value for s in TaskStatus}
            ):
                raise ValueError("任务标识或状态不合法")
        if canvases and state["seq"] < max(int(cid[3:]) for cid in canvases):
            raise ValueError("画布序号回退")
        if jobs and state["job_seq"] < max(int(jid[4:]) for jid in jobs):
            raise ValueError("任务序号回退")
        if set(state["lifecycle"]) != set(canvases) or any(
            not isinstance(v, dict) for v in state["lifecycle"].values()
        ):
            raise ValueError("生命周期表不一致")
        if any(
            k not in canvases or not isinstance(v, str) or not v
            for k, v in state["canvas_owners"].items()
        ):
            raise ValueError("画布主体表不合法")
        for jid, binding in state["job_owners"].items():
            if (
                jid not in jobs
                or not isinstance(binding, dict)
                or any(
                    not isinstance(binding.get(k), str) or not binding[k]
                    for k in ("owner_key", "canvas_id", "project_id")
                )
            ):
                raise ValueError("任务归属表不合法")
        self._canvases, self._topologies, self._jobs = canvases, topologies, jobs
        self._job_owners = copy.deepcopy(state["job_owners"])
        self._canvas_owners = copy.deepcopy(state["canvas_owners"])
        self._lifecycle = copy.deepcopy(state["lifecycle"])
        self._seq, self._job_seq = state["seq"], state["job_seq"]

    @staticmethod
    def _check_storage_path(path: Path) -> None:
        """固定状态路径拒绝链接/重解析点，避免落盘越过数据根。"""
        if path.is_symlink() or (
            path.exists() and getattr(path.lstat(), "st_file_attributes", 0) & 0x400
        ):
            raise ValueError("画布存储不接受链接")

    def _sync_topology_views(self, state: dict[str, Any]) -> None:
        folder = data_root() / "canvases"
        self._check_storage_path(folder)
        if not state["topologies"]:
            return
        folder.mkdir(parents=True, exist_ok=True)
        for cid, payload in state["topologies"].items():
            path = folder / (cid + ".json")
            pending = folder / (cid + ".json.pending")
            self._check_storage_path(path)
            self._check_storage_path(pending)
            try:
                if json.loads(path.read_text(encoding="utf-8")) == payload:
                    continue
            except (FileNotFoundError, json.JSONDecodeError, UnicodeDecodeError):
                pass
            with pending.open("w", encoding="utf-8", newline="\n") as stream:
                json.dump(payload, stream, ensure_ascii=False, sort_keys=True)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(pending, path)

    @contextmanager
    def _transaction(self):
        """单进程多服务对象共享提交锁；失败恢复提交前状态，不自动重发任务。"""
        if not self.persistent:
            with self._lock:
                yield
            return
        with _PERSISTENCE_LOCK, self._lock:
            try:
                registry = data_root() / "canvases_registry.json"
                self._check_storage_path(registry)
                self._check_storage_path(registry.with_name(registry.name + ".tmp"))
                stored = self._state.read()
                if stored is None:
                    if registry.exists():
                        raise ValueError("画布注册快照损坏")
                    stored = {
                        "schema": "gw.canvas-state/v001",
                        "seq": 0,
                        "job_seq": 0,
                        **{
                            k: {}
                            for k in (
                                "canvases",
                                "topologies",
                                "jobs",
                                "job_owners",
                                "canvas_owners",
                                "lifecycle",
                            )
                        },
                    }
                self._load_snapshot(stored)
                self._sync_topology_views(stored)
            except (OSError, ValueError, TypeError, KeyError):
                raise CleanroomException(
                    503, "CANVAS_STORAGE_UNAVAILABLE", "画布存储不可用，未覆盖原状态"
                ) from None
            before = self._snapshot()
            try:
                yield
                after = self._snapshot()
                if after != before:
                    # 先写派生拓扑，最后原子提交全部表；未提交拓扑不会被作为真源读取。
                    self._sync_topology_views(after)
                    self._state.write(after)
            except (OSError, ValueError, TypeError, KeyError):
                self._load_snapshot(before)
                raise CleanroomException(
                    503, "CANVAS_STORAGE_UNAVAILABLE", "画布存储不可用，未覆盖原状态"
                ) from None
            except BaseException:
                self._load_snapshot(before)
                raise

    def authorize_project(self, project_id: str, context: AuthContext) -> str:
        """持久画布必须校验当前项目归属；独立参考画布另记主体。"""
        from gw.projects_hub.service import owner_key_for_context

        if project_id == "default":
            return owner_key_for_context(context)
        return self._check_project_access(project_id, context)

    def authorize_canvas(self, canvas_id: str, context: AuthContext) -> CanvasItem:
        """HTTP 入口复用同一项目真源，跨主体统一隐藏。"""
        from gw.projects_hub.service import owner_key_for_context

        with self._transaction():
            item = self._canvases.get(canvas_id)
            if item is None:
                raise CleanroomException(
                    404, "CANVAS_NOT_FOUND", "画布不存在或不可访问"
                )
            if self.persistent:
                owner = self.authorize_project(item.project_id, context)
                if (
                    item.project_id == "default"
                    and self._canvas_owners.get(canvas_id)
                    != owner_key_for_context(context)
                ) or (self._canvas_owners.get(canvas_id) not in (None, owner)):
                    raise CleanroomException(
                        404, "CANVAS_NOT_FOUND", "画布不存在或不可访问"
                    )
            return copy.deepcopy(item)

    @staticmethod
    def _project_read_allowed(project_id: str, context: AuthContext) -> bool:
        """项目读可见性统一交由项目真源判定；未知或不可读项目一律视为不可见。"""
        from gw.projects_hub import service as projects

        owner_key = None
        try:
            owner_key = projects.owner_key_for_context(context)
        except CleanroomException:
            owner_key = None
        return projects.default_projects_service.can_read_project(
            project_id,
            owner_key=owner_key,
            role=str(getattr(context, "role", "") or ""),
        )

    def _reference_canvas_owner_guard(
        self, canvas_id: str, context: AuthContext
    ) -> None:
        """无归属画布（project_id=default）只按登记主体隔离，不借用项目权限。"""
        from gw.projects_hub.service import owner_key_for_context

        try:
            owner_key = owner_key_for_context(context)
        except CleanroomException as e:
            raise CleanroomException(404, "CANVAS_NOT_FOUND", "画布不存在或不可访问") from e
        if self._canvas_owners.get(canvas_id) not in (None, owner_key):
            raise CleanroomException(404, "CANVAS_NOT_FOUND", "画布不存在或不可访问")

    def authorize_canvas_read(self, canvas_id: str, context: AuthContext) -> CanvasItem:
        """读门禁：项目绑定画布按项目读权限判定，不可读一律隐藏为 404 CANVAS_NOT_FOUND。"""
        with self._transaction():
            item = self._canvases.get(canvas_id)
            if item is None:
                raise CleanroomException(
                    404, "CANVAS_NOT_FOUND", "画布不存在或不可访问"
                )
            if item.project_id == "default":
                self._reference_canvas_owner_guard(canvas_id, context)
            else:
                if context.mode == "local_account":
                    owner = self.authorize_project(item.project_id, context)
                    if self._canvas_owners.get(canvas_id) not in (None, owner):
                        raise CleanroomException(
                            404, "CANVAS_NOT_FOUND", "画布不存在或不可访问"
                        )
                if not self._project_read_allowed(item.project_id, context):
                    raise CleanroomException(
                        404, "CANVAS_NOT_FOUND", "画布不存在或不可访问"
                    )
            return copy.deepcopy(item)

    def authorize_canvas_write(
        self, canvas_id: str, context: AuthContext
    ) -> CanvasItem:
        """写门禁：仅项目写者放行；无权写者 404 隐藏，只读项目 403 PROJECT_READ_ONLY。"""
        from gw.core.auth import EDIT_ROLES
        from gw.projects_hub.service import (
            owner_key_for_context,
            require_project_write_context,
        )

        with self._transaction():
            item = self._canvases.get(canvas_id)
            if item is None:
                raise CleanroomException(
                    404, "CANVAS_NOT_FOUND", "画布不存在或不可访问"
                )
            if item.project_id == "default":
                self._reference_canvas_owner_guard(canvas_id, context)
                if context.role not in EDIT_ROLES:
                    raise ForbiddenException(message="无当前资源写权限，已降级为只读")
                return copy.deepcopy(item)

            if context.role not in EDIT_ROLES:
                from gw.projects_hub import service as projects

                owner_key = None
                try:
                    owner_key = owner_key_for_context(context)
                except CleanroomException:
                    owner_key = None
                with projects.default_projects_service._operation() as (_, state, __):
                    is_owner = bool(
                        owner_key
                        and state.get("owners", {}).get(item.project_id) == owner_key
                    )
                if is_owner:
                    raise ForbiddenException(message="无当前资源写权限，已降级为只读")
                raise CleanroomException(
                    404, "CANVAS_NOT_FOUND", "画布不存在或不可访问"
                )

            if context.mode == "local_account":
                owner = self._check_project_access(item.project_id, context)
                if self._canvas_owners.get(canvas_id) not in (None, owner):
                    raise CleanroomException(
                        404, "CANVAS_NOT_FOUND", "画布不存在或不可访问"
                    )

            try:
                require_project_write_context(item.project_id, context)
            except CleanroomException as exc:
                if exc.status_code == 404:
                    raise CleanroomException(
                        404, "CANVAS_NOT_FOUND", "画布不存在或不可访问"
                    ) from None
                raise
            return copy.deepcopy(item)

    def _require_version(self, expected_version: int | None) -> None:
        if self.persistent and (
            type(expected_version) is not int or expected_version < 1
        ):
            raise CleanroomException(
                400,
                "EXPECTED_VERSION_REQUIRED",
                "画布修改必须携带正整数 expected_version",
            )

    # ------------------------------------------------------------------
    # 1. 普通画布拓扑管理能力 (Classic Topology Management)
    # ------------------------------------------------------------------

    def list_canvases(self, project_id: str) -> list[CanvasItem]:
        """返回指定项目可见的画布集合。"""
        with self._transaction():
            # 已入回收站或已归档的画布不在活跃列表；两者各有独立视图端点。
            return [
                copy.deepcopy(item)
                for canvas_id, item in self._canvases.items()
                if item.project_id == project_id
                and not (self._lifecycle.get(canvas_id) or {}).get("deleted_at")
                and not (self._lifecycle.get(canvas_id) or {}).get("archived_at")
            ]

    # ------------------------------------------------------------------
    # 生命周期投影（归档 / 回收站 / 元信息）
    #
    # 设计取舍：生命周期字段集中放在 ``_lifecycle`` 侧表，不写进 ``CanvasItem``，
    # 以免改变既有 /api/canvases 契约模型与历史夹具的序列化面。
    # 侧表键必须与 ``canvas_id`` 一致，禁止另起别名。
    # ------------------------------------------------------------------

    def ensure_lifecycle(self, canvas_id: str) -> dict[str, Any]:
        """返回目标画布的生命周期记录；不存在时按活跃画布惰性建立。"""
        with self._transaction():
            if canvas_id not in self._canvases:
                raise CleanroomException(
                    status_code=404,
                    code="CANVAS_NOT_FOUND",
                    message=f"画布 {canvas_id} 不存在",
                )
            record = self._lifecycle.get(canvas_id)
            if record is None:
                record = {
                    "kind": "classic",
                    "entity_id": None,
                    "icon": None,
                    "board_x": None,
                    "board_y": None,
                    "created_at": int(time.time()),
                    "updated_at": int(time.time()),
                    "deleted_at": None,
                    "archived_at": None,
                }
                self._lifecycle[canvas_id] = record
            return copy.deepcopy(record)

    def list_lifecycle(self) -> list[dict[str, Any]]:
        """返回全部画布的生命周期投影（含 canvas_id，供观测 / 画布资产索引读取）。"""
        with self._transaction():
            out = []
            for canvas_id, item in self._canvases.items():
                record = self._lifecycle.get(canvas_id) or {}
                out.append(
                    {
                        "canvas_id": canvas_id,
                        "title": item.title,
                        "project_id": item.project_id,
                        "version": item.version,
                        "mode": item.mode.value,
                        "kind": record.get("kind") or "classic",
                        "entity_id": record.get("entity_id"),
                        "icon": record.get("icon"),
                        "board_x": record.get("board_x"),
                        "board_y": record.get("board_y"),
                        "created_at": record.get("created_at"),
                        "updated_at": record.get("updated_at"),
                        "deleted_at": record.get("deleted_at"),
                        "archived_at": record.get("archived_at"),
                    }
                )
            return out

    def list_trash(self, view: str = "deleted") -> list[dict[str, Any]]:
        """返回回收站（已删除）或归档列表；无内容时必须是空数组。"""
        if view not in {"deleted", "archived"}:
            raise CleanroomException(
                status_code=400,
                code="INVALID_REQUEST",
                message="view 仅支持 deleted 或 archived",
            )
        field = "deleted_at" if view == "deleted" else "archived_at"
        out = []
        for entry in self.list_lifecycle():
            if not entry[field]:
                continue
            # 归档视图只取已归档；回收站视图只取已入回收站，两者互不冒充。
            if view == "deleted" and entry["archived_at"]:
                continue
            out.append(entry)
        return out

    def set_canvas_kind(self, canvas_id: str, kind: str) -> dict[str, Any]:
        """设置画布类型（classic / smart / reference）；仅接受白名单取值。"""
        if kind not in {"classic", "smart", "reference"}:
            raise CleanroomException(
                status_code=400,
                code="INVALID_REQUEST",
                message="kind 仅支持 classic / smart / reference",
            )
        with self._transaction():
            record = self._lifecycle.get(canvas_id)
            if record is None:
                raise CleanroomException(
                    status_code=404,
                    code="CANVAS_NOT_FOUND",
                    message=f"画布 {canvas_id} 不存在",
                )
            record["kind"] = kind
            self._canvases[canvas_id].kind = kind
            record["updated_at"] = int(time.time())
            return copy.deepcopy(record)

    def update_canvas_meta(
        self,
        canvas_id: str,
        patch: dict[str, Any],
        expected_version: int | None = None,
    ) -> dict[str, Any]:
        """更新画布元信息；CAS 不一致严格返回 409 CANVAS_VERSION_CONFLICT。"""
        self._require_version(expected_version)
        with self._transaction():
            item = self._canvases.get(canvas_id)
            if item is None:
                raise CleanroomException(
                    status_code=404,
                    code="CANVAS_NOT_FOUND",
                    message=f"画布 {canvas_id} 不存在",
                )
            if expected_version is not None and item.version != expected_version:
                raise CanvasVersionConflictException(
                    expected_version=expected_version,
                    current_version=item.version,
                    canvas_id=canvas_id,
                )
            record = self._lifecycle.setdefault(
                canvas_id,
                {
                    "kind": "classic",
                    "entity_id": None,
                    "icon": None,
                    "board_x": None,
                    "board_y": None,
                    "created_at": int(time.time()),
                    "updated_at": int(time.time()),
                    "deleted_at": None,
                    "archived_at": None,
                },
            )
            if patch.get("title"):
                item.title = patch["title"]
            if patch.get("project_id"):
                item.project_id = patch["project_id"]
            for field in ("entity_id", "icon", "board_x", "board_y", "kind"):
                if field in patch and patch[field] is not None:
                    record[field] = patch[field]
                    if field == "kind":
                        item.kind = patch[field]
            item.version += 1
            top = self._topologies.get(canvas_id)
            if top is not None:
                top.project_id = item.project_id
                top.version = item.version
                if patch.get("reference_payload") is not None:
                    references = dict(top.references or {})
                    references["reference_canvas"] = copy.deepcopy(
                        patch["reference_payload"]
                    )
                    top.references = references
            record["updated_at"] = int(time.time())
            return self._project_canvas(canvas_id)

    def touch_canvas(
        self,
        canvas_id: str,
        operation: str,
        expected_version: int | None = None,
    ) -> dict[str, Any]:
        """归档 / 解归档画布；只做状态翻转，不改内容，且必须递增 version。"""
        if operation not in ("archive", "unarchive"):
            raise CleanroomException(
                status_code=400,
                code="INVALID_CANVAS_OPERATION",
                message="operation 仅支持 archive 或 unarchive",
            )
        self._require_version(expected_version)
        with self._transaction():
            item = self._canvases.get(canvas_id)
            if item is None:
                raise CleanroomException(
                    status_code=404,
                    code="CANVAS_NOT_FOUND",
                    message=f"画布 {canvas_id} 不存在",
                )
            if expected_version is not None and item.version != expected_version:
                raise CanvasVersionConflictException(
                    expected_version=expected_version,
                    current_version=item.version,
                    canvas_id=canvas_id,
                )
            record = self._lifecycle.setdefault(
                canvas_id,
                {
                    "kind": "classic",
                    "entity_id": None,
                    "icon": None,
                    "board_x": None,
                    "board_y": None,
                    "created_at": int(time.time()),
                    "updated_at": int(time.time()),
                    "deleted_at": None,
                    "archived_at": None,
                },
            )
            record["archived_at"] = int(time.time()) if operation == "archive" else None
            record["updated_at"] = int(time.time())
            item.version += 1
            top = self._topologies.get(canvas_id)
            if top is not None:
                top.version = item.version
            return self._project_canvas(canvas_id)

    def move_to_trash(
        self, canvas_id: str, expected_version: int | None = None
    ) -> dict[str, Any]:
        """把画布移入回收站（软删除）；不删除拓扑，可 restore。"""
        return self._set_deleted(
            canvas_id, deleted=True, expected_version=expected_version
        )

    def restore_from_trash(
        self, canvas_id: str, expected_version: int | None = None
    ) -> dict[str, Any]:
        """从回收站恢复画布；与既有的 ``restore_canvas`` 语义一致。"""
        return self._set_deleted(
            canvas_id, deleted=False, expected_version=expected_version
        )

    def _set_deleted(
        self,
        canvas_id: str,
        deleted: bool,
        expected_version: int | None = None,
    ) -> dict[str, Any]:
        self._require_version(expected_version)
        with self._transaction():
            item = self._canvases.get(canvas_id)
            if item is None:
                raise CleanroomException(
                    status_code=404,
                    code="CANVAS_NOT_FOUND",
                    message=f"画布 {canvas_id} 不存在",
                )
            if expected_version is not None and item.version != expected_version:
                raise CanvasVersionConflictException(
                    expected_version=expected_version,
                    current_version=item.version,
                    canvas_id=canvas_id,
                )
            record = self._lifecycle.setdefault(
                canvas_id,
                {
                    "kind": "classic",
                    "entity_id": None,
                    "icon": None,
                    "board_x": None,
                    "board_y": None,
                    "created_at": int(time.time()),
                    "updated_at": int(time.time()),
                    "deleted_at": None,
                    "archived_at": None,
                },
            )
            record["deleted_at"] = int(time.time()) if deleted else None
            record["updated_at"] = int(time.time())
            item.version += 1
            top = self._topologies.get(canvas_id)
            if top is not None:
                top.version = item.version
            return self._project_canvas(canvas_id)

    def purge_canvas(
        self, canvas_id: str, expected_version: int | None = None
    ) -> dict[str, Any]:
        """彻底删除回收站中的画布；未入回收站一律拒绝，禁止越权直删。"""
        self._require_version(expected_version)
        with self._transaction():
            item = self._canvases.get(canvas_id)
            if item is None:
                raise CleanroomException(
                    status_code=404,
                    code="CANVAS_NOT_FOUND",
                    message=f"画布 {canvas_id} 不存在",
                )
            record = self._lifecycle.get(canvas_id) or {}
            if expected_version is not None and item.version != expected_version:
                raise CanvasVersionConflictException(
                    expected_version, item.version, canvas_id
                )
            if not record.get("deleted_at"):
                raise CleanroomException(
                    status_code=409,
                    code="CANVAS_NOT_IN_TRASH",
                    message="仅回收站中的画布可以彻底删除",
                )
            version = item.version
            del self._canvases[canvas_id]
            self._topologies.pop(canvas_id, None)
            self._lifecycle.pop(canvas_id, None)
            self._canvas_owners.pop(canvas_id, None)
            return {"canvas_id": canvas_id, "version": version, "purged": True}

    def get_reference_payload(self, canvas_id: str) -> dict[str, Any] | None:
        """读取参考画布载荷（存放于拓扑 references.reference_canvas）。"""
        with self._transaction():
            top = self._topologies.get(canvas_id)
            if top is None or not top.references:
                return None
            payload = top.references.get("reference_canvas")
            return copy.deepcopy(payload) if isinstance(payload, dict) else None

    def _project_canvas(self, canvas_id: str) -> dict[str, Any]:
        """生成画布生命周期投影。

        ``id`` / ``project`` 是前端既有读取面的兼容别名，服务端另有
        ``canvas_id`` / ``project_id`` 权威字段（见契约 decisions.frontend_compat）。
        """
        item = self._canvases[canvas_id]
        record = self._lifecycle.get(canvas_id) or {}
        return {
            "canvas_id": canvas_id,
            "id": canvas_id,
            "title": item.title,
            "project_id": item.project_id,
            "project": item.project_id,
            "version": item.version,
            "governance_version": item.version,
            "mode": item.mode.value,
            "kind": record.get("kind") or "classic",
            "entity_id": record.get("entity_id"),
            "icon": record.get("icon"),
            "board_x": record.get("board_x"),
            "board_y": record.get("board_y"),
            "created_at": record.get("created_at"),
            "updated_at": record.get("updated_at"),
            "deleted_at": record.get("deleted_at"),
            "archived_at": record.get("archived_at"),
        }

    def get_canvas(self, canvas_id: str) -> CanvasItem:
        """获取指定画布元信息。"""
        with self._transaction():
            item = self._canvases.get(canvas_id)
            if not item:
                raise CleanroomException(
                    status_code=404,
                    code="CANVAS_NOT_FOUND",
                    message=f"画布 {canvas_id} 不存在",
                )
            return copy.deepcopy(item)

    def get_topology(self, canvas_id: str) -> CanvasTopology:
        """获取指定画布的完整拓扑结构。"""
        with self._transaction():
            top = self._topologies.get(canvas_id)
            if not top:
                raise CleanroomException(
                    status_code=404,
                    code="CANVAS_NOT_FOUND",
                    message=f"画布 {canvas_id} 不存在",
                )
            return copy.deepcopy(top)

    def get_canvas_meta(self, canvas_id: str) -> dict[str, Any]:
        """返回同一提交快照中的元信息投影。"""
        with self._transaction():
            if canvas_id not in self._canvases:
                raise CleanroomException(404, "CANVAS_NOT_FOUND", "画布不存在")
            return copy.deepcopy(self._project_canvas(canvas_id))

    def create_canvas(
        self,
        payload: CanvasCreateRequest,
        *,
        owner_key: str | None = None,
        canvas_kind: str | None = None,
    ) -> CanvasMutationResult:
        """创建新画布并分配初始版本 1。"""
        if canvas_kind is not None and canvas_kind not in {
            "classic",
            "smart",
            "reference",
        }:
            raise CleanroomException(400, "INVALID_REQUEST", "画布类型不合法")
        with self._transaction():
            self._seq += 1
            cid = f"cv-{self._seq:04d}"
            item = CanvasItem(
                canvas_id=cid,
                title=payload.title,
                project_id=payload.project_id,
                version=1,
                mode=payload.mode,
                kind=canvas_kind or payload.mode.value,
                sub_canvas_type=payload.sub_canvas_type,
            )
            self._canvases[cid] = item
            if owner_key:
                self._canvas_owners[cid] = owner_key
            now = int(time.time())
            self._lifecycle[cid] = {
                "kind": canvas_kind or payload.mode.value,
                "entity_id": None,
                "icon": None,
                "board_x": None,
                "board_y": None,
                "created_at": now,
                "updated_at": now,
                "deleted_at": None,
                "archived_at": None,
            }

            nodes = []
            connections = []
            references = None
            if payload.initial_payload and isinstance(payload.initial_payload, dict):
                try:
                    initial_topology = CanvasTopology(
                        canvas_id=cid,
                        version=1,
                        nodes=payload.initial_payload.get("nodes", []),
                        connections=payload.initial_payload.get("connections", []),
                        references=payload.initial_payload.get("references"),
                    )
                except ValueError:
                    raise CleanroomException(
                        400, "INVALID_TOPOLOGY", "初始画布拓扑不合法"
                    ) from None
                nodes = initial_topology.nodes
                connections = initial_topology.connections
                references = initial_topology.references

            self._topologies[cid] = CanvasTopology(
                canvas_id=cid,
                project_id=payload.project_id,
                version=1,
                viewport=(payload.initial_payload or {}).get("viewport"),
                nodes=nodes,
                connections=connections,
                references=references,
            )
            return CanvasMutationResult(canvas_id=cid, version=1)

    def update_topology(
        self,
        canvas_id: str,
        payload: CanvasTopologyUpdateRequest,
    ) -> CanvasMutationResult:
        """基于 CAS 乐观锁更新画布拓扑。

        若 expected_version 与当前版本不一致，严格返回 409 CANVAS_VERSION_CONFLICT。
        """
        self._require_version(payload.expected_version)
        with self._transaction():
            item = self._canvases.get(canvas_id)
            top = self._topologies.get(canvas_id)
            if not item or not top:
                raise CleanroomException(
                    status_code=404,
                    code="CANVAS_NOT_FOUND",
                    message=f"画布 {canvas_id} 不存在",
                )

            if top.version != payload.expected_version:
                raise CanvasVersionConflictException(
                    expected_version=payload.expected_version,
                    current_version=top.version,
                    canvas_id=canvas_id,
                )

            top.nodes = copy.deepcopy(payload.nodes)
            top.connections = copy.deepcopy(payload.connections)
            top.references = copy.deepcopy(payload.references)
            if "viewport" in payload.model_fields_set:
                top.viewport = copy.deepcopy(payload.viewport)
            top.version += 1
            item.version = top.version

            return CanvasMutationResult(canvas_id=canvas_id, version=top.version)

    def restore_canvas(
        self, canvas_id: str, expected_version: int
    ) -> CanvasMutationResult:
        """恢复画布；按章程强制 CAS：版本不一致严格返回 409 CANVAS_VERSION_CONFLICT。"""
        self._require_version(expected_version)
        with self._transaction():
            item = self._canvases.get(canvas_id)
            top = self._topologies.get(canvas_id)
            if not item or not top:
                raise CleanroomException(
                    status_code=404,
                    code="CANVAS_NOT_FOUND",
                    message=f"画布 {canvas_id} 不存在",
                )

            if top.version != expected_version:
                raise CanvasVersionConflictException(
                    expected_version=expected_version,
                    current_version=top.version,
                    canvas_id=canvas_id,
                )

            top.version += 1
            item.version = top.version
            # 恢复必须同时清掉生命周期标记，否则画布仍会被列表当作用户已删除而隐藏。
            record = self._lifecycle.get(canvas_id)
            if record is not None:
                record["deleted_at"] = None
                record["archived_at"] = None
                record["updated_at"] = int(time.time())
            return CanvasMutationResult(canvas_id=canvas_id, version=top.version)

    def import_workflow(
        self,
        canvas_id: str,
        content: str,
        expected_version: int,
        file_format: str = "json",
        merge_mode: str = "replace",
    ) -> CanvasImportResponse:
        """导入工作流文件（严格支持 json 与 godmap 拓扑校验）；按章程强制 CAS。"""
        self._require_version(expected_version)
        with self._transaction():
            item = self._canvases.get(canvas_id)
            top = self._topologies.get(canvas_id)
            if not item or not top:
                raise CleanroomException(
                    status_code=404,
                    code="CANVAS_NOT_FOUND",
                    message=f"画布 {canvas_id} 不存在",
                )

            if top.version != expected_version:
                raise CanvasVersionConflictException(
                    expected_version=expected_version,
                    current_version=top.version,
                    canvas_id=canvas_id,
                )

            try:
                if file_format not in {"json", "godmap"}:
                    raise ValueError("仅支持 json 或 godmap 格式")
                if merge_mode not in {"replace", "insert"}:
                    raise ValueError("仅支持 replace 或 insert 合并模式")
                if file_format == "godmap":
                    doc = parse_godmap_content(content)
                    imported_topology = CanvasTopology(
                        canvas_id=canvas_id,
                        version=top.version,
                        nodes=doc.payload.nodes,
                        connections=doc.payload.connections,
                        references=doc.payload.references,
                    )
                else:
                    raw = json.loads(content)
                    if not isinstance(raw, dict) or "nodes" not in raw:
                        raise ValueError("缺少必填拓扑字段 nodes")
                    imported_topology = CanvasTopology.model_validate(raw)
                imported_nodes = imported_topology.nodes
                imported_connections = imported_topology.connections
                imported_references = imported_topology.references
            except Exception as e:
                raise CleanroomException(
                    status_code=400,
                    code="INVALID_TOPOLOGY",
                    message=f"工作流拓扑结构不合法: {str(e)}",
                ) from e

            if merge_mode == "replace":
                top.nodes = imported_nodes
                top.connections = imported_connections
                top.references = imported_references
            else:
                imported_nodes, imported_connections = self._rekey_insert(
                    top.nodes,
                    top.connections,
                    imported_nodes,
                    imported_connections,
                )
                top.nodes.extend(imported_nodes)
                top.connections.extend(imported_connections)

            top.version += 1
            item.version = top.version

            return CanvasImportResponse(
                canvas=CanvasMutationResult(canvas_id=canvas_id, version=top.version),
                import_report=CanvasImportReport(
                    nodes_imported=len(imported_nodes),
                    connections_imported=len(imported_connections),
                    warnings=[],
                ),
            )

    def export_workflow(
        self,
        canvas_id: str,
        export_format: str = "json",
        include_resources: bool = False,
    ) -> str:
        """导出画布拓扑为指定格式字符串。"""
        if export_format not in {"json", "godmap"}:
            raise CleanroomException(
                status_code=400,
                code="UNSUPPORTED_FORMAT",
                message="仅支持 json 或 godmap 导出",
            )
        if include_resources:
            raise CleanroomException(
                status_code=400,
                code="UNSUPPORTED_OPTION",
                message="当前洁净切片不内嵌外部资源",
            )
        top = self.get_topology(canvas_id)
        if export_format == "godmap":
            return export_to_godmap(top)
        return top.model_dump_json(by_alias=True, indent=2)

    @staticmethod
    def _rekey_insert(
        existing_nodes, existing_connections, imported_nodes, imported_connections
    ):
        """为 insert 导入的冲突 ID 分配稳定后缀并重写连线端点。"""
        node_ids = {node.entity_id for node in existing_nodes}
        connection_ids = {
            connection.connection_id for connection in existing_connections
        }
        node_map: dict[str, str] = {}
        rekeyed_nodes = []
        for node in copy.deepcopy(imported_nodes):
            original_id = node.entity_id
            candidate = original_id
            suffix = 1
            while candidate in node_ids:
                candidate = f"{original_id}-import-{suffix}"
                suffix += 1
            node_ids.add(candidate)
            node_map[original_id] = candidate
            node.entity_id = candidate
            rekeyed_nodes.append(node)

        rekeyed_connections = []
        for connection in copy.deepcopy(imported_connections):
            original_id = connection.connection_id
            candidate = original_id
            suffix = 1
            while candidate in connection_ids:
                candidate = f"{original_id}-import-{suffix}"
                suffix += 1
            connection_ids.add(candidate)
            connection.connection_id = candidate
            connection.from_node = node_map.get(
                connection.from_node, connection.from_node
            )
            connection.to_node = node_map.get(connection.to_node, connection.to_node)
            rekeyed_connections.append(connection)
        return rekeyed_nodes, rekeyed_connections

    # ------------------------------------------------------------------
    # 2. 智能画布异步任务管理能力 (Smart Canvas Task Management)
    # ------------------------------------------------------------------

    def submit_smart_task(
        self,
        canvas_id: str,
        payload: SmartCanvasTaskRequest,
        authorization: str | None = None,
        user_role: str = "editor",
    ) -> SmartCanvasTaskResponse:
        """发起智能画布任务，返回 202 Accepted 及稳定 job_id。"""
        # 服务层也保留写权限边界，避免绕过 HTTP 路由直接提交任务。
        try:
            context = require_authenticated(authorization, user_role)
        except CleanroomException as exc:
            self._audit_job_access("submit", exc.status_code)
            raise

        try:
            self.authorize_canvas_write(canvas_id, context)
        except CleanroomException as exc:
            self._audit_job_access("submit", exc.status_code, context)
            raise

        self._require_version(payload.expected_version)
        with self._transaction():
            item = self._canvases.get(canvas_id)
            top = self._topologies.get(canvas_id)
            if not item or not top:
                raise CleanroomException(
                    status_code=404,
                    code="CANVAS_NOT_FOUND",
                    message=f"画布 {canvas_id} 不存在",
                )

            from gw.projects_hub.service import owner_key_for_context

            owner_key = owner_key_for_context(context)

            # CAS 校验
            if (
                payload.expected_version is not None
                and top.version != payload.expected_version
            ):
                raise CanvasVersionConflictException(
                    expected_version=payload.expected_version,
                    current_version=top.version,
                    canvas_id=canvas_id,
                )

            node_ids = {node.entity_id for node in top.nodes}
            missing_entry_nodes = [
                node_id for node_id in payload.entry_nodes if node_id not in node_ids
            ]
            if missing_entry_nodes:
                raise CleanroomException(
                    status_code=409,
                    code="TASK_PRECONDITION_FAILED",
                    message=f"任务入口节点不存在: {', '.join(missing_entry_nodes)}",
                )

            # 生成稳定 job_id
            self._job_seq += 1
            job_id = f"job-{self._job_seq:04d}"
            poll_hint = f"/api/jobs/{job_id}"

            task_resp = SmartCanvasTaskResponse(
                job_id=job_id,
                state=TaskStatus.ACCEPTED.value,
                poll_hint=poll_hint,
            )
            self._jobs[job_id] = task_resp
            self._job_owners[job_id] = {
                "owner_key": owner_key,
                "canvas_id": canvas_id,
                "project_id": item.project_id,
            }
            self._audit_job_access("submit", 202, context)
            return task_resp

    @staticmethod
    def _audit_job_access(
        operation: str, status_code: int, context: AuthContext | None = None
    ) -> None:
        """只记录操作、结果和认证模式，不记录Cookie、输入、路径或资源存在性。"""
        from gw.projects_hub.service import owner_key_for_context

        logging.getLogger("gw.audit").info(
            "canvas_job_access %s",
            json.dumps(
                {
                    "operation": operation,
                    "status_code": status_code,
                    "auth_mode": context.mode if context else None,
                    "subject_key": owner_key_for_context(context)
                    if context and context.subject and context.identity_domain
                    else None,
                },
                sort_keys=True,
            ),
        )

    @staticmethod
    def _check_project_access(project_id: str, context: AuthContext) -> str:
        from gw.projects_hub import service as projects

        owner_key = projects.owner_key_for_context(context)
        if project_id == "default":
            return owner_key
        project = projects.default_projects_service.get_owned_project(
            project_id, owner_key
        )
        if project.deleted_at or project.archived_at:
            raise CleanroomException(404, "JOB_NOT_FOUND", "任务不存在或不可访问")
        return owner_key

    def get_owned_job(
        self, job_id: str, context: AuthContext
    ) -> SmartCanvasTaskResponse:
        """HTTP/观测专用：主体侧表不能替代每次读取的项目真源ACL。"""
        from gw.projects_hub.service import owner_key_for_context

        try:
            owner_key = owner_key_for_context(context)
            with self._transaction():
                binding = self._job_owners.get(job_id)
                if (
                    not binding
                    or binding["owner_key"] != owner_key
                    or job_id not in self._jobs
                ):
                    raise CleanroomException(
                        404, "JOB_NOT_FOUND", "任务不存在或不可访问"
                    )
                if self.persistent:
                    self.authorize_project(binding["project_id"], context)
                else:
                    self._check_project_access(binding["project_id"], context)
                item = self._canvases.get(binding["canvas_id"])
                if item is None or item.project_id != binding["project_id"]:
                    raise CleanroomException(
                        404, "JOB_NOT_FOUND", "任务不存在或不可访问"
                    )
                result = copy.deepcopy(self._jobs[job_id])
        except CleanroomException as exc:
            self._audit_job_access("query", exc.status_code, context)
            raise
        self._audit_job_access("query", 200, context)
        return result

    def recover_interrupted_jobs(self) -> list[str]:
        """启动恢复：上次进程遗留的 accepted/running 任务标为中断终态，不自动重发。"""
        interrupted: list[str] = []
        with self._transaction():
            stamp = now_iso()
            for job_id, job in self._jobs.items():
                if job.state in (TaskStatus.ACCEPTED.value, TaskStatus.RUNNING.value):
                    self._jobs[job_id] = SmartCanvasTaskResponse.model_validate(
                        {
                            **job.model_dump(mode="json"),
                            "state": TaskStatus.FAILED.value,
                            "error": "CANVAS_TASK_INTERRUPTED",
                            "poll_hint": None,
                            "interrupted_at": stamp,
                        }
                    )
                    interrupted.append(job_id)
        return interrupted

    def list_owned_jobs(self, context: AuthContext) -> list[SmartCanvasTaskResponse]:
        """观测列表使用与单任务相同的当前项目权限，不公开种子。"""
        with self._transaction():
            job_ids = list(self._jobs)
        result = []
        for job_id in job_ids:
            try:
                result.append(self.get_owned_job(job_id, context))
            except CleanroomException as exc:
                if exc.status_code != 404:
                    raise
        return result

    def list_jobs(self) -> list[SmartCanvasTaskResponse]:
        """只读返回当前进程内全部任务快照（供观测层读取，不暴露内部字典）。

        返回深拷贝，调用方无法借返回值修改服务内部状态。
        生产任务快照可重启回读；显式黄金夹具为内存模式，多 worker 不支持。
        """
        with self._transaction():
            return [copy.deepcopy(job) for job in self._jobs.values()]

    def get_job(self, job_id: str) -> SmartCanvasTaskResponse:
        """查询任务执行状态。"""
        with self._transaction():
            job = self._jobs.get(job_id)
            if not job:
                raise CleanroomException(
                    status_code=404,
                    code="JOB_NOT_FOUND",
                    message=f"任务 {job_id} 不存在",
                )
            return copy.deepcopy(job)

    def update_job_state(
        self,
        job_id: str,
        state: str,
        result: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> SmartCanvasTaskResponse:
        """推进任务状态机状态（线程安全）。"""
        with self._transaction():
            job = self._jobs.get(job_id)
            if not job:
                raise CleanroomException(
                    status_code=404,
                    code="JOB_NOT_FOUND",
                    message=f"任务 {job_id} 不存在",
                )

            # 终端状态不可再变更
            allowed_transitions = {
                "accepted": {"running", "cancelled"},
                "running": {
                    "waiting_review",
                    "paused",
                    "completed",
                    "failed",
                    "cancelled",
                },
                "waiting_review": {
                    "running",
                    "paused",
                    "completed",
                    "failed",
                    "cancelled",
                },
                "paused": {"running", "failed", "cancelled"},
            }
            terminal_states = {"completed", "failed", "cancelled"}
            if job.state in terminal_states:
                raise CleanroomException(
                    status_code=400,
                    code="ILLEGAL_STATE_TRANSITION",
                    message=f"任务 {job_id} 当前已处于终端状态 {job.state}，不可流转至 {state}",
                )

            if state not in allowed_transitions.get(job.state, set()):
                raise CleanroomException(
                    status_code=400,
                    code="ILLEGAL_STATE_TRANSITION",
                    message=f"任务 {job_id} 不允许从 {job.state} 流转至 {state}",
                )

            job.state = state
            job.result = copy.deepcopy(result)
            job.error = error
            if state in terminal_states:
                job.poll_hint = None
            return copy.deepcopy(job)

    def cancel_job(self, job_id: str) -> SmartCanvasTaskResponse:
        """取消未进入终端状态的任务。"""
        return self.update_job_state(job_id, state="cancelled")


# 兼容别名与单例实例
CanvasService = GodCanvasService
default_god_canvas_service = GodCanvasService(
    seed_golden_fixture=False, persistent=True
)
default_canvas_service = default_god_canvas_service
