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

"""项目中心真源、项目生命周期与阶段门状态服务。"""

from __future__ import annotations

import copy
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import threading
from typing import Any, Callable, Dict, Iterator, List, Optional

from gw.core import storage
from gw.core.errors import CleanroomException, VersionConflictException
from gw.projects_hub.models import (
    ProjectCreateRequest,
    ProjectItem,
    ProjectMutationResult,
    ProjectType,
    ProjectUpdateRequest,
)

_SCHEMA_VERSION = 1
_PROJECTS_FILE = "projects.json"
_RESERVATIONS_FILE = "project_id_reservations.json"
_OWNER_KEY_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_NAMESPACE_PATTERN = _OWNER_KEY_PATTERN
_IDEMPOTENCY_CHARS = re.compile(r"^[A-Za-z0-9_:.\-]{8,128}$")
_GATE_CODE_CHARS = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_GATE_STATES = frozenset({"pending", "approved", "changes_requested"})
_GOVERNANCE_ROLES = frozenset({"admin", "governor"})
_GATE_WRITER_ROLES = _GOVERNANCE_ROLES | frozenset({"editor"})

_PROJECT_LOCKS_GUARD = threading.Lock()
_PROJECT_LOCKS: Dict[str, threading.RLock] = {}


def _now_iso() -> str:
    """生成标准 ISO 8601 UTC 时间戳字符串。"""
    return datetime.now(timezone.utc).isoformat()


def owner_key_for_context(context: Any) -> str:
    """把已认证的稳定身份域与主体转换为不含明文主体的内部键。"""
    domain = str(getattr(context, "identity_domain", "") or "").strip()
    subject = str(getattr(context, "subject", "") or "").strip()
    if not domain or not subject:
        raise CleanroomException(403, "FORBIDDEN", "当前认证身份不具备稳定项目主体")
    return hashlib.sha256((domain + "\0" + subject).encode("utf-8")).hexdigest()


def _blank_projects_state() -> Dict[str, Any]:
    return {
        "schema_version": _SCHEMA_VERSION,
        "revision": 1,
        "projects": {},
        "owners": {},
        "gates": {},
        "idempotency": {},
    }


def _canonical_root() -> Path:
    """返回规范化绝对数据根，不把该路径写入或回显到项目数据。"""
    try:
        root = storage.data_root().expanduser().resolve(strict=False)
    except (OSError, RuntimeError, ValueError) as exc:
        raise CleanroomException(503, "PROJECT_STATE_UNAVAILABLE", "项目数据根目录不可用") from exc
    if not root.is_absolute():
        raise CleanroomException(503, "PROJECT_STATE_UNAVAILABLE", "项目数据根目录不可用")
    return root


def _lock_for(root: Path) -> threading.RLock:
    key = os.path.normcase(os.path.normpath(str(root)))
    with _PROJECT_LOCKS_GUARD:
        lock = _PROJECT_LOCKS.get(key)
        if lock is None:
            lock = threading.RLock()
            _PROJECT_LOCKS[key] = lock
        return lock


def _state_error(code: str, message: str) -> CleanroomException:
    return CleanroomException(503, code, message)


def _atomic_write(path: Path, value: Dict[str, Any]) -> None:
    """以同目录临时文件和原子替换写入一个快照。"""
    temporary = path.with_name(path.name + ".tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with temporary.open("w", encoding="utf-8", newline="\n") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=1, sort_keys=True)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except (OSError, TypeError, ValueError) as exc:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
        raise _state_error("PROJECT_STATE_WRITE_FAILED", "项目状态写入失败，已拒绝发布") from exc


def _read_json(path: Path, *, code: str, label: str) -> Dict[str, Any]:
    try:
        with path.open("r", encoding="utf-8") as handle:
            value = json.load(handle)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise _state_error(code, f"{label}不可读取或已损坏，系统已失败关闭") from exc
    if not isinstance(value, dict):
        raise _state_error(code, f"{label}结构不合法，系统已失败关闭")
    return value


def _validate_reservations(value: Dict[str, Any]) -> None:
    if value.get("schema_version") != _SCHEMA_VERSION:
        raise _state_error("PROJECT_RESERVATIONS_CORRUPT", "项目 ID 预留状态版本不受支持")
    namespace = value.get("namespace")
    project_sequence = value.get("project_sequence")
    gate_sequence = value.get("gate_sequence")
    initialized = value.get("projects_initialized")
    if (
        not isinstance(namespace, str)
        or not _NAMESPACE_PATTERN.fullmatch(namespace)
        or type(project_sequence) is not int
        or project_sequence < 0
        or type(gate_sequence) is not int
        or gate_sequence < 0
        or type(initialized) is not bool
    ):
        raise _state_error("PROJECT_RESERVATIONS_CORRUPT", "项目 ID 预留状态结构不合法")


def _gate_sequence(gate_id: str) -> Optional[int]:
    match = re.fullmatch(r"gate-p-[0-9a-f]{64}-([1-9][0-9]*)", gate_id)
    return int(match.group(1)) if match else None


def _project_sequence(project_id: str, namespace: str) -> Optional[int]:
    match = re.fullmatch(r"prj-p-" + re.escape(namespace) + r"-([1-9][0-9]*)", project_id)
    return int(match.group(1)) if match else None


def _validate_projects_state(value: Dict[str, Any], reservations: Dict[str, Any]) -> None:
    if value.get("schema_version") != _SCHEMA_VERSION or type(value.get("revision")) is not int or value["revision"] < 1:
        raise _state_error("PROJECT_STATE_CORRUPT", "项目快照版本或修订号不合法")
    for key in ("projects", "owners", "gates", "idempotency"):
        if not isinstance(value.get(key), dict):
            raise _state_error("PROJECT_STATE_CORRUPT", "项目快照结构不合法")
    projects = value["projects"]
    owners = value["owners"]
    gates = value["gates"]
    idempotency = value["idempotency"]
    if set(owners) != set(projects):
        raise _state_error("PROJECT_STATE_CORRUPT", "项目与owner真源不一致")

    max_project_sequence = 0
    for project_id, raw_item in projects.items():
        if not isinstance(project_id, str) or not isinstance(raw_item, dict):
            raise _state_error("PROJECT_STATE_CORRUPT", "项目记录结构不合法")
        if any(key in raw_item for key in ("owner_key", "gates", "idempotency")):
            raise _state_error("PROJECT_STATE_CORRUPT", "项目公开模型包含禁止序列化的内部字段")
        try:
            item = ProjectItem.model_validate(raw_item)
        except Exception as exc:
            raise _state_error("PROJECT_STATE_CORRUPT", "项目记录不符合冻结模型") from exc
        if item.project_id != project_id:
            raise _state_error("PROJECT_STATE_CORRUPT", "项目主键与记录不一致")
        sequence = _project_sequence(project_id, reservations["namespace"])
        if sequence is None:
            raise _state_error("PROJECT_STATE_CORRUPT", "项目 ID 不符合持久命名空间契约")
        max_project_sequence = max(max_project_sequence, sequence)
        if not isinstance(owners.get(project_id), str) or not _OWNER_KEY_PATTERN.fullmatch(owners[project_id]):
            raise _state_error("PROJECT_STATE_CORRUPT", "项目owner键结构不合法")
    if max_project_sequence > reservations["project_sequence"]:
        raise _state_error("PROJECT_STATE_CORRUPT", "项目快照超出已预留序号")

    max_gate_sequence = 0
    for gate_id, gate in gates.items():
        if not isinstance(gate_id, str) or not isinstance(gate, dict) or gate.get("gate_id") != gate_id:
            raise _state_error("PROJECT_STATE_CORRUPT", "项目门记录结构不合法")
        sequence = _gate_sequence(gate_id)
        if sequence is None or not isinstance(gate.get("project_id"), str) or gate["project_id"] not in projects:
            raise _state_error("PROJECT_STATE_CORRUPT", "项目门关联或 ID 不合法")
        if (
            not isinstance(gate.get("code"), str)
            or not isinstance(gate.get("name"), str)
            or gate.get("state") not in _GATE_STATES
            or type(gate.get("version")) is not int
            or gate["version"] < 1
            or not isinstance(gate.get("created_at"), str)
        ):
            raise _state_error("PROJECT_STATE_CORRUPT", "项目门字段不合法")
        max_gate_sequence = max(max_gate_sequence, sequence)
    if max_gate_sequence > reservations["gate_sequence"]:
        raise _state_error("PROJECT_STATE_CORRUPT", "项目门快照超出已预留序号")

    for key, record in idempotency.items():
        if (
            not isinstance(key, str)
            or not _OWNER_KEY_PATTERN.fullmatch(key)
            or not isinstance(record, dict)
            or not isinstance(record.get("request_digest"), str)
            or not _OWNER_KEY_PATTERN.fullmatch(record["request_digest"])
            or record.get("project_id") not in projects
        ):
            raise _state_error("PROJECT_STATE_CORRUPT", "创建幂等记录结构不合法")


class ProjectsService:
    """项目服务；测试可显式使用内存模式，生产实例默认读取单一持久快照。"""

    def __init__(self, seed_golden_fixture: bool = True, *, persistent: bool = False):
        self._persistent = bool(persistent)
        self._lock = threading.RLock()
        self._projects: Dict[str, ProjectItem] = {}
        self._owners: Dict[str, str] = {}
        self._gates: Dict[str, Dict[str, Any]] = {}
        self._idempotency: Dict[str, Dict[str, Any]] = {}
        self._seq = 1 if seed_golden_fixture else 0
        self._gate_seq = 0

        if seed_golden_fixture and not self._persistent:
            self._projects["prj-0001"] = ProjectItem(
                project_id="prj-0001",
                name="示例项目 A",
                project_type=ProjectType.FILM,
                stage="production",
                progress=68.0,
                scenes=12,
                shots=48,
                description="洁净室契约验证基准项目",
                start_at=1780000000000,
                due_at=1781200000000,
                version=3,
                archived_at=None,
                deleted_at=None,
                updated_at=_now_iso(),
            )

    @contextmanager
    def _operation(self) -> Iterator[tuple[Optional[Path], Dict[str, Any], Optional[Dict[str, Any]]]]:
        """让同一数据根的项目与门读写共用一个进程内域锁。"""
        if self._persistent:
            root = _canonical_root()
            with _lock_for(root):
                state, reservations = self._load_persistent_state(root)
                yield root, state, reservations
            return
        with self._lock:
            yield None, self._memory_state(), None

    def _memory_state(self) -> Dict[str, Any]:
        return {
            "schema_version": _SCHEMA_VERSION,
            "revision": 1,
            "projects": {key: item.model_dump(mode="json") for key, item in self._projects.items()},
            "owners": copy.deepcopy(self._owners),
            "gates": copy.deepcopy(self._gates),
            "idempotency": copy.deepcopy(self._idempotency),
        }

    def _load_persistent_state(self, root: Path) -> tuple[Dict[str, Any], Dict[str, Any]]:
        reservation_path = root / _RESERVATIONS_FILE
        projects_path = root / _PROJECTS_FILE
        reservation_exists = reservation_path.exists()
        projects_exists = projects_path.exists()

        if not reservation_exists and projects_exists:
            raise _state_error("PROJECT_RESERVATIONS_MISSING", "项目 ID 预留状态丢失，系统已失败关闭")
        if reservation_exists:
            reservations = _read_json(
                reservation_path,
                code="PROJECT_RESERVATIONS_CORRUPT",
                label="项目 ID 预留状态",
            )
            _validate_reservations(reservations)
        else:
            canonical = os.path.normcase(os.path.normpath(str(root)))
            namespace = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
            reservations = {
                "schema_version": _SCHEMA_VERSION,
                "namespace": namespace,
                "project_sequence": 0,
                "gate_sequence": 0,
                "projects_initialized": False,
            }
            _atomic_write(reservation_path, reservations)

        if projects_exists:
            state = _read_json(projects_path, code="PROJECT_STATE_CORRUPT", label="项目快照")
            _validate_projects_state(state, reservations)
            if not reservations["projects_initialized"]:
                reservations = dict(reservations)
                reservations["projects_initialized"] = True
                _atomic_write(reservation_path, reservations)
        else:
            if reservations["projects_initialized"]:
                raise _state_error("PROJECT_STATE_MISSING", "已初始化的项目快照丢失，系统已失败关闭")
            state = _blank_projects_state()
            _atomic_write(projects_path, state)
            reservations = dict(reservations)
            reservations["projects_initialized"] = True
            _atomic_write(reservation_path, reservations)
        return state, reservations

    def _commit_state(self, root: Optional[Path], state: Dict[str, Any]) -> None:
        if root is not None:
            if self._persistent:
                _atomic_write(root / _PROJECTS_FILE, state)
                return
        self._projects = {
            project_id: ProjectItem.model_validate(raw)
            for project_id, raw in state["projects"].items()
        }
        self._owners = copy.deepcopy(state["owners"])
        self._gates = copy.deepcopy(state["gates"])
        self._idempotency = copy.deepcopy(state["idempotency"])

    def _persist_reservations(self, root: Path, reservations: Dict[str, Any]) -> None:
        _atomic_write(root / _RESERVATIONS_FILE, reservations)

    @staticmethod
    def _clean_gate_inputs(payload: ProjectCreateRequest) -> List[Dict[str, str]]:
        raw_gates = payload.gates or []
        if not isinstance(raw_gates, list) or len(raw_gates) > 32:
            raise CleanroomException(400, "INVALID_REQUEST", "阶段门数量必须为0至32项")
        normalized: List[Dict[str, str]] = []
        seen = set()
        for raw in raw_gates:
            if not isinstance(raw, dict) or set(raw) != {"code", "name"}:
                raise CleanroomException(400, "INVALID_REQUEST", "阶段门必须仅包含code与name")
            code_value = raw.get("code")
            name_value = raw.get("name")
            if not isinstance(code_value, str) or not isinstance(name_value, str):
                raise CleanroomException(400, "INVALID_REQUEST", "阶段门code与name必须为文本")
            code = code_value.strip()
            name = name_value.strip()
            if not _GATE_CODE_CHARS.fullmatch(code):
                raise CleanroomException(400, "INVALID_REQUEST", "阶段门code长度或格式不合法")
            if not 1 <= len(name) <= 120:
                raise CleanroomException(400, "INVALID_REQUEST", "阶段门name长度不合法")
            canonical_code = code.lower()
            if canonical_code in seen:
                raise CleanroomException(400, "INVALID_REQUEST", "阶段门code不能重复")
            seen.add(canonical_code)
            normalized.append({"code": canonical_code, "name": name})
        request_id = payload.client_request_id
        if request_id is not None:
            if not isinstance(request_id, str) or not _IDEMPOTENCY_CHARS.fullmatch(request_id):
                raise CleanroomException(400, "INVALID_REQUEST", "client_request_id长度或格式不合法")
        if normalized and not request_id:
            raise CleanroomException(400, "INVALID_REQUEST", "包含阶段门的项目创建必须提供client_request_id")
        return normalized

    @staticmethod
    def _request_digest(payload: ProjectCreateRequest, gates: List[Dict[str, str]]) -> str:
        normalized = {
            "name": payload.name,
            "project_type": payload.project_type.value,
            "description": payload.description,
            "start_at": payload.start_at,
            "due_at": payload.due_at,
            "gates": gates,
        }
        encoded = json.dumps(normalized, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()

    @staticmethod
    def _idempotency_key(owner_key: str, client_request_id: str) -> str:
        return hashlib.sha256((owner_key + "\0" + client_request_id).encode("utf-8")).hexdigest()

    @staticmethod
    def _require_owner_key(owner_key: Optional[str]) -> str:
        if not isinstance(owner_key, str) or not _OWNER_KEY_PATTERN.fullmatch(owner_key):
            raise CleanroomException(400, "PROJECT_OWNER_REQUIRED", "创建项目需要服务端认证的稳定owner")
        return owner_key

    def list_projects(
        self,
        archived: Optional[bool] = False,
        deleted: Optional[bool] = None,
    ) -> List[ProjectItem]:
        """根据归档与回收站过滤条件返回项目列表。"""
        with self._operation() as (_, state, __):
            results = []
            for raw in state["projects"].values():
                item = ProjectItem.model_validate(raw)
                is_deleted = item.deleted_at is not None
                is_archived = item.archived_at is not None
                if deleted is True:
                    if not is_deleted:
                        continue
                elif is_deleted:
                    continue
                if deleted is not True:
                    if archived is True and not is_archived:
                        continue
                    if archived is False and is_archived:
                        continue
                results.append(item)
            return sorted(results, key=lambda item: item.project_id, reverse=True)

    def get_project(self, project_id: str) -> ProjectItem:
        """读取项目字段，不在普通项目响应中序列化owner、门或幂等记录。"""
        with self._operation() as (_, state, __):
            raw = state["projects"].get(project_id)
            if raw is None:
                raise CleanroomException(404, "PROJECT_NOT_FOUND", f"项目 {project_id} 不存在")
            return ProjectItem.model_validate(raw)

    def get_owned_project(self, project_id: str, owner_key: Optional[str]) -> ProjectItem:
        """只在稳定主体仍是项目真源owner时返回项目，隐藏其他项目的存在性。"""
        with self._operation() as (_, state, __):
            raw = state["projects"].get(project_id)
            if raw is None or state["owners"].get(project_id) != owner_key:
                raise CleanroomException(404, "PROJECT_NOT_FOUND", "项目不存在")
            return ProjectItem.model_validate(raw)

    def _get_source_owner_key(self, project_id: str) -> str:
        """在项目域锁及完整真源校验内读取内部owner键，不序列化到普通API响应。"""
        with self._operation() as (_, state, __):
            if project_id not in state["projects"]:
                raise CleanroomException(404, "PROJECT_NOT_FOUND", "项目不存在")
            owner_key = state["owners"].get(project_id)
            if not isinstance(owner_key, str) or not _OWNER_KEY_PATTERN.fullmatch(owner_key):
                raise _state_error("PROJECT_STATE_CORRUPT", "项目owner真源不合法，系统已失败关闭")
            return owner_key

    def create_project(
        self,
        payload: ProjectCreateRequest,
        *,
        owner_key: Optional[str] = None,
        before_publish: Optional[Callable[[str], None]] = None,
    ) -> ProjectMutationResult:
        """先持久预留不可复用ID，再执行ACL绑定，最后原子发布项目、owner、门与幂等快照。"""
        gates = self._clean_gate_inputs(payload)
        effective_owner = owner_key
        if self._persistent:
            effective_owner = self._require_owner_key(owner_key)
        elif effective_owner is None:
            effective_owner = hashlib.sha256(b"memory-test-owner").hexdigest()
        else:
            effective_owner = self._require_owner_key(effective_owner)
        digest = self._request_digest(payload, gates)
        request_id = payload.client_request_id

        with self._operation() as (root, state, reservations):
            idem_key = self._idempotency_key(effective_owner, request_id) if request_id else None
            if idem_key:
                existing = state["idempotency"].get(idem_key)
                if existing is not None:
                    if existing["request_digest"] != digest:
                        raise CleanroomException(409, "IDEMPOTENCY_CONFLICT", "相同client_request_id已用于不同项目请求")
                    return ProjectMutationResult(project_id=existing["project_id"], version=1)

            if root is not None and reservations is not None:
                next_reservations = dict(reservations)
                next_reservations["project_sequence"] += 1
                project_sequence = next_reservations["project_sequence"]
                first_gate_sequence = next_reservations["gate_sequence"] + 1
                next_reservations["gate_sequence"] += len(gates)
                self._persist_reservations(root, next_reservations)
                namespace = next_reservations["namespace"]
                project_id = f"prj-p-{namespace}-{project_sequence}"
                gate_ids = [f"gate-p-{namespace}-{first_gate_sequence + offset}" for offset in range(len(gates))]
            else:
                self._seq += 1
                project_id = f"prj-{self._seq:04d}"
                first_gate_sequence = self._gate_seq + 1
                self._gate_seq += len(gates)
                gate_ids = [f"gate-test-{first_gate_sequence + offset:04d}" for offset in range(len(gates))]

            item = ProjectItem(
                project_id=project_id,
                name=payload.name,
                project_type=payload.project_type,
                stage="planning",
                progress=0.0,
                scenes=0,
                shots=0,
                description=payload.description,
                start_at=payload.start_at,
                due_at=payload.due_at,
                version=1,
                archived_at=None,
                deleted_at=None,
                updated_at=_now_iso(),
            )
            if before_publish is not None:
                before_publish(project_id)

            next_state = copy.deepcopy(state)
            next_state["projects"][project_id] = item.model_dump(mode="json")
            next_state["owners"][project_id] = effective_owner
            for gate_id, gate_input in zip(gate_ids, gates):
                next_state["gates"][gate_id] = {
                    "gate_id": gate_id,
                    "project_id": project_id,
                    "code": gate_input["code"],
                    "name": gate_input["name"],
                    "state": "pending",
                    "version": 1,
                    "created_at": _now_iso(),
                    "updated_at": None,
                    "note": None,
                }
            if idem_key:
                next_state["idempotency"][idem_key] = {
                    "request_digest": digest,
                    "project_id": project_id,
                }
            next_state["revision"] += 1
            self._commit_state(root, next_state)
            return ProjectMutationResult(project_id=project_id, version=item.version)

    def update_project(self, project_id: str, payload: ProjectUpdateRequest) -> ProjectMutationResult:
        """根据 CAS 乐观锁更新项目，并只在完整快照成功后发布。"""
        with self._operation() as (root, state, __):
            raw = state["projects"].get(project_id)
            if raw is None:
                raise CleanroomException(404, "PROJECT_NOT_FOUND", f"项目 {project_id} 不存在")
            item = ProjectItem.model_validate(raw)
            if item.version != payload.expected_version:
                raise VersionConflictException(expected_version=payload.expected_version, current_version=item.version)
            if item.archived_at is not None or item.deleted_at is not None:
                raise CleanroomException(403, "FORBIDDEN", "归档或回收站项目为只读状态")

            fields_set = payload.model_fields_set
            next_start = payload.start_at if "start_at" in fields_set else item.start_at
            next_due = payload.due_at if "due_at" in fields_set else item.due_at
            if next_start is not None and next_due is not None and next_due < next_start:
                raise CleanroomException(400, "INVALID_SCHEDULE", "due_at 不能早于 start_at")
            for field in ("name", "stage", "scenes", "shots", "progress"):
                value = getattr(payload, field)
                if value is not None:
                    setattr(item, field, float(value) if field == "progress" else value)
            if "description" in fields_set:
                item.description = payload.description
            if "start_at" in fields_set:
                item.start_at = payload.start_at
            if "due_at" in fields_set:
                item.due_at = payload.due_at
            item.version += 1
            item.updated_at = _now_iso()
            next_state = copy.deepcopy(state)
            next_state["projects"][project_id] = item.model_dump(mode="json")
            next_state["revision"] += 1
            self._commit_state(root, next_state)
            return ProjectMutationResult(project_id=project_id, version=item.version)

    def _lifecycle(self, project_id: str, expected_version: int, operation: str) -> ProjectMutationResult:
        with self._operation() as (root, state, __):
            raw = state["projects"].get(project_id)
            if raw is None:
                raise CleanroomException(404, "PROJECT_NOT_FOUND", f"项目 {project_id} 不存在")
            item = ProjectItem.model_validate(raw)
            if item.version != expected_version:
                raise VersionConflictException(expected_version=expected_version, current_version=item.version)
            if operation == "archive":
                if item.deleted_at is not None or item.archived_at is not None:
                    raise CleanroomException(409, "LIFECYCLE_CONFLICT", "项目当前不在活跃状态")
                item.archived_at = _now_iso()
            elif operation == "unarchive":
                if item.deleted_at is not None or item.archived_at is None:
                    raise CleanroomException(409, "LIFECYCLE_CONFLICT", "项目当前不是已归档状态")
                item.archived_at = None
            elif operation == "trash":
                if item.deleted_at is not None or item.archived_at is None:
                    raise CleanroomException(409, "LIFECYCLE_CONFLICT", "只有已归档项目可以进入回收站")
                item.deleted_at = _now_iso()
            elif operation == "restore":
                if item.deleted_at is None:
                    raise CleanroomException(409, "LIFECYCLE_CONFLICT", "项目不在回收站")
                item.deleted_at = None
                item.archived_at = None
            else:
                raise RuntimeError("未知项目生命周期操作")
            item.version += 1
            item.updated_at = _now_iso()
            next_state = copy.deepcopy(state)
            next_state["projects"][project_id] = item.model_dump(mode="json")
            next_state["revision"] += 1
            self._commit_state(root, next_state)
            return ProjectMutationResult(
                project_id=project_id,
                version=item.version,
                archived_at=item.archived_at,
                deleted_at=item.deleted_at,
            )

    def archive_project(self, project_id: str, expected_version: int) -> ProjectMutationResult:
        """归档项目（CAS 校验）。"""
        return self._lifecycle(project_id, expected_version, "archive")

    def unarchive_project(self, project_id: str, expected_version: int) -> ProjectMutationResult:
        """解归档项目（CAS 校验）。"""
        return self._lifecycle(project_id, expected_version, "unarchive")

    def move_to_trash(self, project_id: str, expected_version: int) -> ProjectMutationResult:
        """移入回收站（CAS 校验）。"""
        return self._lifecycle(project_id, expected_version, "trash")

    def restore_from_trash(self, project_id: str, expected_version: int) -> ProjectMutationResult:
        """从回收站恢复（CAS 校验）。"""
        return self._lifecycle(project_id, expected_version, "restore")

    @staticmethod
    def _validate_owner_access(state: Dict[str, Any], project_id: str, owner_key: Optional[str], role: str) -> bool:
        return str(role or "").lower() in _GOVERNANCE_ROLES or bool(
            owner_key and state["owners"].get(project_id) == owner_key
        )

    def list_project_gates(
        self,
        project_id: str,
        *,
        owner_key: Optional[str],
        role: str,
    ) -> List[Dict[str, Any]]:
        """按项目owner或治理角色发现阶段门；无权发现返回空集合。"""
        with self._operation() as (_, state, __):
            if project_id not in state["projects"]:
                raise CleanroomException(404, "PROJECT_NOT_FOUND", "项目不存在")
            if not self._validate_owner_access(state, project_id, owner_key, role):
                return []
            records = [copy.deepcopy(gate) for gate in state["gates"].values() if gate["project_id"] == project_id]
            return sorted(records, key=lambda gate: (_gate_sequence(gate["gate_id"]) or 0, gate["gate_id"]))

    def get_project_gate(
        self,
        gate_id: str,
        *,
        owner_key: Optional[str],
        role: str,
    ) -> Dict[str, Any]:
        """读取单个门；未知与无权统一隐藏为404。"""
        with self._operation() as (_, state, __):
            gate = state["gates"].get(gate_id)
            if gate is None or not self._validate_owner_access(state, gate["project_id"], owner_key, role):
                raise CleanroomException(404, "PROJECT_GATE_NOT_FOUND", "项目门不存在")
            return copy.deepcopy(gate)

    def update_project_gate(
        self,
        gate_id: str,
        payload: Dict[str, Any],
        *,
        owner_key: Optional[str],
        role: str,
    ) -> Dict[str, Any]:
        """在与项目归档/回收共用的域锁内执行门权限、只读检查与CAS。"""
        with self._operation() as (root, state, __):
            gate = state["gates"].get(gate_id)
            if gate is None or not self._validate_owner_access(state, gate["project_id"], owner_key, role):
                raise CleanroomException(404, "PROJECT_GATE_NOT_FOUND", "项目门不存在")
            if str(role or "").lower() not in _GATE_WRITER_ROLES:
                raise CleanroomException(403, "FORBIDDEN", "当前账户为只读角色，不能更新项目门")
            project = ProjectItem.model_validate(state["projects"][gate["project_id"]])
            if project.archived_at is not None or project.deleted_at is not None:
                raise CleanroomException(403, "PROJECT_READ_ONLY", "归档或回收站项目门为只读状态")

            expected_version = payload.get("expected_version")
            if type(expected_version) is not int or expected_version < 1:
                raise CleanroomException(400, "INVALID_REQUEST", "expected_version必须为正整数")
            if gate["version"] != expected_version:
                raise VersionConflictException(expected_version=expected_version, current_version=gate["version"])

            target_state = payload.get("state")
            if not isinstance(target_state, str) or target_state not in _GATE_STATES:
                raise CleanroomException(400, "INVALID_REQUEST", "项目门状态不合法")
            current_state = gate["state"]
            if target_state == current_state:
                return copy.deepcopy(gate)
            allowed = {
                "pending": {"approved", "changes_requested"},
                "changes_requested": {"pending"},
                "approved": {"changes_requested"},
            }
            if target_state not in allowed[current_state]:
                raise CleanroomException(409, "INVALID_GATE_TRANSITION", "项目门状态迁移不允许")

            note = payload.get("note", "")
            if note is None:
                note = ""
            if not isinstance(note, str) or len(note) > 1000:
                raise CleanroomException(400, "INVALID_REQUEST", "note长度或类型不合法")
            if current_state == "approved" and target_state == "changes_requested":
                if str(role or "").lower() not in _GOVERNANCE_ROLES:
                    raise CleanroomException(403, "FORBIDDEN", "只有治理角色可以撤回已批准门")
                if not note.strip():
                    raise CleanroomException(400, "INVALID_REQUEST", "撤回已批准门必须填写说明")

            next_state = copy.deepcopy(state)
            next_gate = next_state["gates"][gate_id]
            next_gate["state"] = target_state
            if "note" in payload:
                next_gate["note"] = note
            next_gate["version"] += 1
            next_gate["updated_at"] = _now_iso()
            next_state["revision"] += 1
            self._commit_state(root, next_state)
            return copy.deepcopy(next_gate)


# 生产API单例使用持久真源；黄金项目仅由显式内存测试实例注入。
default_projects_service = ProjectsService(seed_golden_fixture=False, persistent=True)