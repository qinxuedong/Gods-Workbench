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
import time
import threading
from typing import Any, Dict, List, Optional

from gw.core.auth import AuthContext, require_edit_access
from gw.core.errors import (
    CanvasVersionConflictException,
    CleanroomException,
)
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
    SmartCanvasRunMode,
    SmartCanvasTaskRequest,
    SmartCanvasTaskResponse,
    TaskStatus,
)


class GodCanvasService:
    """god-canvas 统一拓扑与智能任务服务。"""

    def __init__(self, seed_golden_fixture: bool = True):
        self._lock = threading.Lock()
        self._canvases: Dict[str, CanvasItem] = {}
        self._topologies: Dict[str, CanvasTopology] = {}
        self._jobs: Dict[str, SmartCanvasTaskResponse] = {}
        # 黄金种子无归属：仅内部夹具可读，HTTP不得公开。
        self._job_owners: Dict[str, Dict[str, str]] = {}
        self._lifecycle: Dict[str, Dict[str, Any]] = {}
        self._seq = 1 if seed_golden_fixture else 0
        self._job_seq = 1 if seed_golden_fixture else 0

        if seed_golden_fixture:
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

    # ------------------------------------------------------------------
    # 1. 普通画布拓扑管理能力 (Classic Topology Management)
    # ------------------------------------------------------------------

    def list_canvases(self, project_id: str) -> List[CanvasItem]:
        """返回指定项目可见的画布集合。"""
        with self._lock:
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

    def ensure_lifecycle(self, canvas_id: str) -> Dict[str, Any]:
        """返回目标画布的生命周期记录；不存在时按活跃画布惰性建立。"""
        with self._lock:
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

    def list_lifecycle(self) -> List[Dict[str, Any]]:
        """返回全部画布的生命周期投影（含 canvas_id，供观测 / 画布资产索引读取）。"""
        with self._lock:
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

    def list_trash(self, view: str = "deleted") -> List[Dict[str, Any]]:
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

    def set_canvas_kind(self, canvas_id: str, kind: str) -> Dict[str, Any]:
        """设置画布类型（classic / smart / reference）；仅接受白名单取值。"""
        if kind not in {"classic", "smart", "reference"}:
            raise CleanroomException(
                status_code=400,
                code="INVALID_REQUEST",
                message="kind 仅支持 classic / smart / reference",
            )
        with self._lock:
            record = self._lifecycle.get(canvas_id)
            if record is None:
                raise CleanroomException(
                    status_code=404,
                    code="CANVAS_NOT_FOUND",
                    message=f"画布 {canvas_id} 不存在",
                )
            record["kind"] = kind
            record["updated_at"] = int(time.time())
            return copy.deepcopy(record)

    def update_canvas_meta(
        self,
        canvas_id: str,
        patch: Dict[str, Any],
        expected_version: Optional[int] = None,
    ) -> Dict[str, Any]:
        """更新画布元信息；CAS 不一致严格返回 409 CANVAS_VERSION_CONFLICT。"""
        with self._lock:
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
            item.version += 1
            top = self._topologies.get(canvas_id)
            if top is not None:
                top.version = item.version
                if patch.get("reference_payload") is not None:
                    references = dict(top.references or {})
                    references["reference_canvas"] = copy.deepcopy(patch["reference_payload"])
                    top.references = references
            record["updated_at"] = int(time.time())
            return self._project_canvas(canvas_id)

    def touch_canvas(
        self,
        canvas_id: str,
        operation: str,
        expected_version: Optional[int] = None,
    ) -> Dict[str, Any]:
        """归档 / 解归档画布；只做状态翻转，不改内容，且必须递增 version。"""
        if operation not in ("archive", "unarchive"):
            raise CleanroomException(
                status_code=400,
                code="INVALID_CANVAS_OPERATION",
                message="operation 仅支持 archive 或 unarchive",
            )
        with self._lock:
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

    def move_to_trash(self, canvas_id: str, expected_version: Optional[int] = None) -> Dict[str, Any]:
        """把画布移入回收站（软删除）；不删除拓扑，可 restore。"""
        return self._set_deleted(canvas_id, deleted=True, expected_version=expected_version)

    def restore_from_trash(self, canvas_id: str, expected_version: Optional[int] = None) -> Dict[str, Any]:
        """从回收站恢复画布；与既有的 ``restore_canvas`` 语义一致。"""
        return self._set_deleted(canvas_id, deleted=False, expected_version=expected_version)

    def _set_deleted(
        self,
        canvas_id: str,
        deleted: bool,
        expected_version: Optional[int] = None,
    ) -> Dict[str, Any]:
        with self._lock:
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

    def purge_canvas(self, canvas_id: str) -> Dict[str, Any]:
        """彻底删除回收站中的画布；未入回收站一律拒绝，禁止越权直删。"""
        with self._lock:
            item = self._canvases.get(canvas_id)
            if item is None:
                raise CleanroomException(
                    status_code=404,
                    code="CANVAS_NOT_FOUND",
                    message=f"画布 {canvas_id} 不存在",
                )
            record = self._lifecycle.get(canvas_id) or {}
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
            return {"canvas_id": canvas_id, "version": version, "purged": True}

    def get_reference_payload(self, canvas_id: str) -> Optional[Dict[str, Any]]:
        """读取参考画布载荷（存放于拓扑 references.reference_canvas）。"""
        with self._lock:
            top = self._topologies.get(canvas_id)
            if top is None or not top.references:
                return None
            payload = top.references.get("reference_canvas")
            return copy.deepcopy(payload) if isinstance(payload, dict) else None

    def _project_canvas(self, canvas_id: str) -> Dict[str, Any]:
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
        with self._lock:
            item = self._canvases.get(canvas_id)
            if not item:
                raise CleanroomException(status_code=404, code="CANVAS_NOT_FOUND", message=f"画布 {canvas_id} 不存在")
            return copy.deepcopy(item)

    def get_topology(self, canvas_id: str) -> CanvasTopology:
        """获取指定画布的完整拓扑结构。"""
        with self._lock:
            top = self._topologies.get(canvas_id)
            if not top:
                raise CleanroomException(status_code=404, code="CANVAS_NOT_FOUND", message=f"画布 {canvas_id} 不存在")
            return copy.deepcopy(top)

    def create_canvas(self, payload: CanvasCreateRequest) -> CanvasMutationResult:
        """创建新画布并分配初始版本 1。"""
        with self._lock:
            self._seq += 1
            cid = f"cv-{self._seq:04d}"
            item = CanvasItem(
                canvas_id=cid,
                title=payload.title,
                project_id=payload.project_id,
                version=1,
                mode=payload.mode,
            )
            self._canvases[cid] = item
            now = int(time.time())
            self._lifecycle[cid] = {
                "kind": payload.mode.value,
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
                initial_topology = CanvasTopology(
                    canvas_id=cid,
                    version=1,
                    nodes=payload.initial_payload.get("nodes", []),
                    connections=payload.initial_payload.get("connections", []),
                    references=payload.initial_payload.get("references"),
                )
                nodes = initial_topology.nodes
                connections = initial_topology.connections
                references = initial_topology.references

            self._topologies[cid] = CanvasTopology(
                canvas_id=cid,
                version=1,
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
        with self._lock:
            item = self._canvases.get(canvas_id)
            top = self._topologies.get(canvas_id)
            if not item or not top:
                raise CleanroomException(status_code=404, code="CANVAS_NOT_FOUND", message=f"画布 {canvas_id} 不存在")

            if top.version != payload.expected_version:
                raise CanvasVersionConflictException(
                    expected_version=payload.expected_version,
                    current_version=top.version,
                    canvas_id=canvas_id,
                )

            top.nodes = copy.deepcopy(payload.nodes)
            top.connections = copy.deepcopy(payload.connections)
            top.references = copy.deepcopy(payload.references)
            top.version += 1
            item.version = top.version

            return CanvasMutationResult(canvas_id=canvas_id, version=top.version)

    def restore_canvas(self, canvas_id: str, expected_version: int) -> CanvasMutationResult:
        """恢复画布；按章程强制 CAS：版本不一致严格返回 409 CANVAS_VERSION_CONFLICT。"""
        with self._lock:
            item = self._canvases.get(canvas_id)
            top = self._topologies.get(canvas_id)
            if not item or not top:
                raise CleanroomException(status_code=404, code="CANVAS_NOT_FOUND", message=f"画布 {canvas_id} 不存在")

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
        with self._lock:
            item = self._canvases.get(canvas_id)
            top = self._topologies.get(canvas_id)
            if not item or not top:
                raise CleanroomException(status_code=404, code="CANVAS_NOT_FOUND", message=f"画布 {canvas_id} 不存在")

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
                )

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
            raise CleanroomException(status_code=400, code="UNSUPPORTED_FORMAT", message="仅支持 json 或 godmap 导出")
        if include_resources:
            raise CleanroomException(status_code=400, code="UNSUPPORTED_OPTION", message="当前洁净切片不内嵌外部资源")
        top = self.get_topology(canvas_id)
        if export_format == "godmap":
            return export_to_godmap(top)
        return top.model_dump_json(by_alias=True, indent=2)

    @staticmethod
    def _rekey_insert(existing_nodes, existing_connections, imported_nodes, imported_connections):
        """为 insert 导入的冲突 ID 分配稳定后缀并重写连线端点。"""
        node_ids = {node.entity_id for node in existing_nodes}
        connection_ids = {connection.connection_id for connection in existing_connections}
        node_map: Dict[str, str] = {}
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
            connection.from_node = node_map.get(connection.from_node, connection.from_node)
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
        authorization: Optional[str] = None,
        user_role: str = "editor",
    ) -> SmartCanvasTaskResponse:
        """发起智能画布任务，返回 202 Accepted 及稳定 job_id。"""
        # 服务层也保留写权限边界，避免绕过 HTTP 路由直接提交任务。
        try:
            context = require_edit_access(authorization, user_role)
        except CleanroomException as exc:
            self._audit_job_access("submit", exc.status_code)
            raise

        with self._lock:
            item = self._canvases.get(canvas_id)
            top = self._topologies.get(canvas_id)
            if not item or not top:
                raise CleanroomException(status_code=404, code="CANVAS_NOT_FOUND", message=f"画布 {canvas_id} 不存在")

            try:
                owner_key = self._check_project_access(item.project_id, context)
            except CleanroomException as exc:
                self._audit_job_access("submit", exc.status_code, context)
                raise

            # CAS 校验
            if payload.expected_version is not None and top.version != payload.expected_version:
                raise CanvasVersionConflictException(
                    expected_version=payload.expected_version,
                    current_version=top.version,
                    canvas_id=canvas_id,
                )

            node_ids = {node.entity_id for node in top.nodes}
            missing_entry_nodes = [node_id for node_id in payload.entry_nodes if node_id not in node_ids]
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
            self._job_owners[job_id] = {"owner_key": owner_key, "canvas_id": canvas_id, "project_id": item.project_id}
            self._audit_job_access("submit", 202, context)
            return task_resp

    @staticmethod
    def _audit_job_access(operation: str, status_code: int, context: Optional[AuthContext] = None) -> None:
        """只记录操作、结果和认证模式，不记录Cookie、输入、路径或资源存在性。"""
        from gw.projects_hub.service import owner_key_for_context
        logging.getLogger("gw.audit").info("canvas_job_access %s", json.dumps({
            "operation": operation, "status_code": status_code,
            "auth_mode": context.mode if context else None,
            "subject_key": owner_key_for_context(context) if context and context.subject and context.identity_domain else None,
        }, sort_keys=True))

    @staticmethod
    def _check_project_access(project_id: str, context: AuthContext) -> str:
        from gw.projects_hub import service as projects
        owner_key = projects.owner_key_for_context(context)
        project = projects.default_projects_service.get_owned_project(project_id, owner_key)
        if project.deleted_at or project.archived_at:
            raise CleanroomException(404, "JOB_NOT_FOUND", "任务不存在或不可访问")
        return owner_key

    def get_owned_job(self, job_id: str, context: AuthContext) -> SmartCanvasTaskResponse:
        """HTTP/观测专用：主体侧表不能替代每次读取的项目真源ACL。"""
        from gw.projects_hub.service import owner_key_for_context
        try:
            owner_key = owner_key_for_context(context)
            with self._lock:
                binding = self._job_owners.get(job_id)
                if not binding or binding["owner_key"] != owner_key or job_id not in self._jobs:
                    raise CleanroomException(404, "JOB_NOT_FOUND", "任务不存在或不可访问")
                self._check_project_access(binding["project_id"], context)
                item = self._canvases.get(binding["canvas_id"])
                if item is None or item.project_id != binding["project_id"]:
                    raise CleanroomException(404, "JOB_NOT_FOUND", "任务不存在或不可访问")
                result = copy.deepcopy(self._jobs[job_id])
        except CleanroomException as exc:
            self._audit_job_access("query", exc.status_code, context)
            raise
        self._audit_job_access("query", 200, context)
        return result

    def list_owned_jobs(self, context: AuthContext) -> List[SmartCanvasTaskResponse]:
        """观测列表使用与单任务相同的当前项目权限，不公开种子。"""
        with self._lock:
            job_ids = list(self._jobs)
        result = []
        for job_id in job_ids:
            try:
                result.append(self.get_owned_job(job_id, context))
            except CleanroomException as exc:
                if exc.status_code != 404:
                    raise
        return result

    def list_jobs(self) -> List[SmartCanvasTaskResponse]:
        """只读返回当前进程内全部任务快照（供观测层读取，不暴露内部字典）。

        本方法是**纯读取**投影：返回深拷贝，调用方无法借返回值修改服务内部状态。
        证据边界：任务表为进程内内存，重启即丢失、多 worker 不共享。
        """
        with self._lock:
            return [copy.deepcopy(job) for job in self._jobs.values()]

    def get_job(self, job_id: str) -> SmartCanvasTaskResponse:
        """查询任务执行状态。"""
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                raise CleanroomException(status_code=404, code="JOB_NOT_FOUND", message=f"任务 {job_id} 不存在")
            return copy.deepcopy(job)

    def update_job_state(
        self,
        job_id: str,
        state: str,
        result: Optional[Dict[str, Any]] = None,
        error: Optional[str] = None,
    ) -> SmartCanvasTaskResponse:
        """推进任务状态机状态（线程安全）。"""
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                raise CleanroomException(status_code=404, code="JOB_NOT_FOUND", message=f"任务 {job_id} 不存在")

            # 终端状态不可再变更
            allowed_transitions = {
                "accepted": {"running", "cancelled"},
                "running": {"waiting_review", "paused", "completed", "failed", "cancelled"},
                "waiting_review": {"running", "paused", "completed", "failed", "cancelled"},
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
default_god_canvas_service = GodCanvasService(seed_golden_fixture=True)
default_canvas_service = default_god_canvas_service
