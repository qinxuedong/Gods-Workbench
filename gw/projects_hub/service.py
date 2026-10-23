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
import hashlib
import json
import os
import re
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

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
_READ_ACCESS_MODES = frozenset({"legacy_open", "explicit"})
_READ_AUDIT_EVENTS = frozenset(
    {
        "project.read_granted",
        "project.read_revoked",
        "project.read_mode_explicit",
    }
)
_READ_AUDIT_CAP = 1000
_READ_KEY_PREFIX = re.compile(r"^[0-9a-f]{12}$")
_READ_KEY_PREFIX_LEN = 12

_PROJECT_LOCKS_GUARD = threading.Lock()
_PROJECT_LOCKS: dict[str, threading.RLock] = {}


def _now_iso() -> str:
    """生成标准 ISO 8601 UTC 时间戳字符串。"""
    return datetime.now(UTC).isoformat()


def owner_key_for_context(context: Any) -> str:
    """把已认证的稳定身份域与主体转换为不含明文主体的内部键。"""
    domain = str(getattr(context, "identity_domain", "") or "").strip()
    subject = str(getattr(context, "subject", "") or "").strip()
    if not domain or not subject:
        raise CleanroomException(403, "FORBIDDEN", "当前认证身份不具备稳定项目主体")
    return hashlib.sha256((domain + "\0" + subject).encode("utf-8")).hexdigest()


def owner_key_for_identity(identity_domain: str | None, subject: str | None) -> str:
    """按 (身份域, 主体) 计算 owner_key；与 owner_key_for_context 同一口径。"""
    domain = str(identity_domain or "").strip()
    subject = str(subject or "").strip()
    if not domain or not subject:
        raise CleanroomException(400, "INVALID_REQUEST", "身份域与主体不能为空")
    return hashlib.sha256((domain + "\0" + subject).encode("utf-8")).hexdigest()


def _blank_projects_state() -> dict[str, Any]:
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
        raise CleanroomException(
            503, "PROJECT_STATE_UNAVAILABLE", "项目数据根目录不可用"
        ) from exc
    if not root.is_absolute():
        raise CleanroomException(
            503, "PROJECT_STATE_UNAVAILABLE", "项目数据根目录不可用"
        )
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


def _atomic_write(path: Path, value: dict[str, Any]) -> None:
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
        raise _state_error(
            "PROJECT_STATE_WRITE_FAILED", "项目状态写入失败，已拒绝发布"
        ) from exc


def _read_json(path: Path, *, code: str, label: str) -> dict[str, Any]:
    try:
        with path.open("r", encoding="utf-8") as handle:
            value = json.load(handle)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise _state_error(code, f"{label}不可读取或已损坏，系统已失败关闭") from exc
    if not isinstance(value, dict):
        raise _state_error(code, f"{label}结构不合法，系统已失败关闭")
    return value


def _validate_reservations(value: dict[str, Any]) -> None:
    if value.get("schema_version") != _SCHEMA_VERSION:
        raise _state_error(
            "PROJECT_RESERVATIONS_CORRUPT", "项目 ID 预留状态版本不受支持"
        )
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


def _gate_sequence(gate_id: str) -> int | None:
    match = re.fullmatch(r"gate-p-[0-9a-f]{64}-([1-9][0-9]*)", gate_id)
    return int(match.group(1)) if match else None


def _project_sequence(project_id: str, namespace: str) -> int | None:
    match = re.fullmatch(
        r"prj-p-" + re.escape(namespace) + r"-([1-9][0-9]*)", project_id
    )
    return int(match.group(1)) if match else None


def _validate_projects_state(
    value: dict[str, Any], reservations: dict[str, Any]
) -> None:
    if (
        value.get("schema_version") != _SCHEMA_VERSION
        or type(value.get("revision")) is not int
        or value["revision"] < 1
    ):
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
            raise _state_error(
                "PROJECT_STATE_CORRUPT", "项目公开模型包含禁止序列化的内部字段"
            )
        try:
            item = ProjectItem.model_validate(raw_item)
        except Exception as exc:
            raise _state_error(
                "PROJECT_STATE_CORRUPT", "项目记录不符合冻结模型"
            ) from exc
        if item.project_id != project_id:
            raise _state_error("PROJECT_STATE_CORRUPT", "项目主键与记录不一致")
        sequence = _project_sequence(project_id, reservations["namespace"])
        if sequence is None:
            raise _state_error(
                "PROJECT_STATE_CORRUPT", "项目 ID 不符合持久命名空间契约"
            )
        max_project_sequence = max(max_project_sequence, sequence)
        if not isinstance(
            owners.get(project_id), str
        ) or not _OWNER_KEY_PATTERN.fullmatch(owners[project_id]):
            raise _state_error("PROJECT_STATE_CORRUPT", "项目owner键结构不合法")
    if max_project_sequence > reservations["project_sequence"]:
        raise _state_error("PROJECT_STATE_CORRUPT", "项目快照超出已预留序号")

    max_gate_sequence = 0
    for gate_id, gate in gates.items():
        if (
            not isinstance(gate_id, str)
            or not isinstance(gate, dict)
            or gate.get("gate_id") != gate_id
        ):
            raise _state_error("PROJECT_STATE_CORRUPT", "项目门记录结构不合法")
        sequence = _gate_sequence(gate_id)
        if (
            sequence is None
            or not isinstance(gate.get("project_id"), str)
            or gate["project_id"] not in projects
        ):
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

    # C4-AC7/AC8：读授权与审计为可选顶层键；缺省按 legacy_open 处理，出现时必须结构完整。
    read_access = value.get("read_access")
    if read_access is not None:
        if not isinstance(read_access, dict):
            raise _state_error("PROJECT_STATE_CORRUPT", "项目读授权记录结构不合法")
        for project_id, record in read_access.items():
            if not isinstance(project_id, str) or project_id not in projects:
                raise _state_error(
                    "PROJECT_STATE_CORRUPT", "项目读授权记录关联未知项目"
                )
            if (
                not isinstance(record, dict)
                or record.get("mode") not in _READ_ACCESS_MODES
            ):
                raise _state_error("PROJECT_STATE_CORRUPT", "项目读授权模式不合法")
            if type(record.get("version")) is not int or record["version"] < 1:
                raise _state_error("PROJECT_STATE_CORRUPT", "项目读授权版本不合法")
            for field in ("grants", "revoked"):
                keys = record.get(field)
                if not isinstance(keys, list) or any(
                    not isinstance(key, str) or not _OWNER_KEY_PATTERN.fullmatch(key)
                    for key in keys
                ):
                    raise _state_error(
                        "PROJECT_STATE_CORRUPT", "项目读授权键结构不合法"
                    )

    read_audit = value.get("read_audit")
    if read_audit is not None:
        if not isinstance(read_audit, list) or len(read_audit) > _READ_AUDIT_CAP:
            raise _state_error("PROJECT_STATE_CORRUPT", "项目读授权审计集合不合法")
        for entry in read_audit:
            if not isinstance(entry, dict) or set(entry) != {
                "event",
                "project_id",
                "actor",
                "target",
                "at",
            }:
                raise _state_error(
                    "PROJECT_STATE_CORRUPT", "项目读授权审计条目结构不合法"
                )
            if (
                entry["event"] not in _READ_AUDIT_EVENTS
                or entry["project_id"] not in projects
                or not isinstance(entry["at"], str)
                or not isinstance(entry["actor"], str)
                or not _READ_KEY_PREFIX.fullmatch(entry["actor"])
                or not (
                    entry["target"] is None
                    or (
                        isinstance(entry["target"], str)
                        and _READ_KEY_PREFIX.fullmatch(entry["target"])
                    )
                )
            ):
                raise _state_error(
                    "PROJECT_STATE_CORRUPT", "项目读授权审计条目字段不合法"
                )


class ProjectsService:
    """项目服务；测试可显式使用内存模式，生产实例默认读取单一持久快照。"""

    def __init__(self, seed_golden_fixture: bool = True, *, persistent: bool = False):
        self._persistent = bool(persistent)
        self._lock = threading.RLock()
        self._projects: dict[str, ProjectItem] = {}
        self._owners: dict[str, str] = {}
        self._gates: dict[str, dict[str, Any]] = {}
        self._idempotency: dict[str, dict[str, Any]] = {}
        self._read_access: dict[str, dict[str, Any]] = {}
        self._read_audit: list[dict[str, Any]] = []
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
    def _operation(
        self,
    ) -> Iterator[tuple[Path | None, dict[str, Any], dict[str, Any] | None]]:
        """让同一数据根的项目与门读写共用一个进程内域锁。"""
        if self._persistent:
            root = _canonical_root()
            with _lock_for(root):
                state, reservations = self._load_persistent_state(root)
                yield root, state, reservations
            return
        with self._lock:
            yield None, self._memory_state(), None

    def _memory_state(self) -> dict[str, Any]:
        return {
            "schema_version": _SCHEMA_VERSION,
            "revision": 1,
            "projects": {
                key: item.model_dump(mode="json")
                for key, item in self._projects.items()
            },
            "owners": copy.deepcopy(self._owners),
            "gates": copy.deepcopy(self._gates),
            "idempotency": copy.deepcopy(self._idempotency),
            "read_access": copy.deepcopy(self._read_access),
            "read_audit": copy.deepcopy(self._read_audit),
        }

    def _load_persistent_state(
        self, root: Path
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        reservation_path = root / _RESERVATIONS_FILE
        projects_path = root / _PROJECTS_FILE
        reservation_exists = reservation_path.exists()
        projects_exists = projects_path.exists()

        if not reservation_exists and projects_exists:
            raise _state_error(
                "PROJECT_RESERVATIONS_MISSING", "项目 ID 预留状态丢失，系统已失败关闭"
            )
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
            state = _read_json(
                projects_path, code="PROJECT_STATE_CORRUPT", label="项目快照"
            )
            _validate_projects_state(state, reservations)
            if not reservations["projects_initialized"]:
                reservations = dict(reservations)
                reservations["projects_initialized"] = True
                _atomic_write(reservation_path, reservations)
        else:
            if reservations["projects_initialized"]:
                raise _state_error(
                    "PROJECT_STATE_MISSING", "已初始化的项目快照丢失，系统已失败关闭"
                )
            state = _blank_projects_state()
            _atomic_write(projects_path, state)
            reservations = dict(reservations)
            reservations["projects_initialized"] = True
            _atomic_write(reservation_path, reservations)
        return state, reservations

    def _commit_state(self, root: Path | None, state: dict[str, Any]) -> None:
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
        self._read_access = copy.deepcopy(state.get("read_access") or {})
        self._read_audit = copy.deepcopy(state.get("read_audit") or [])

    def _persist_reservations(self, root: Path, reservations: dict[str, Any]) -> None:
        _atomic_write(root / _RESERVATIONS_FILE, reservations)

    @staticmethod
    def _clean_gate_inputs(payload: ProjectCreateRequest) -> list[dict[str, str]]:
        raw_gates = payload.gates or []
        if not isinstance(raw_gates, list) or len(raw_gates) > 32:
            raise CleanroomException(400, "INVALID_REQUEST", "阶段门数量必须为0至32项")
        normalized: list[dict[str, str]] = []
        seen = set()
        for raw in raw_gates:
            if not isinstance(raw, dict) or set(raw) != {"code", "name"}:
                raise CleanroomException(
                    400, "INVALID_REQUEST", "阶段门必须仅包含code与name"
                )
            code_value = raw.get("code")
            name_value = raw.get("name")
            if not isinstance(code_value, str) or not isinstance(name_value, str):
                raise CleanroomException(
                    400, "INVALID_REQUEST", "阶段门code与name必须为文本"
                )
            code = code_value.strip()
            name = name_value.strip()
            if not _GATE_CODE_CHARS.fullmatch(code):
                raise CleanroomException(
                    400, "INVALID_REQUEST", "阶段门code长度或格式不合法"
                )
            if not 1 <= len(name) <= 120:
                raise CleanroomException(400, "INVALID_REQUEST", "阶段门name长度不合法")
            canonical_code = code.lower()
            if canonical_code in seen:
                raise CleanroomException(400, "INVALID_REQUEST", "阶段门code不能重复")
            seen.add(canonical_code)
            normalized.append({"code": canonical_code, "name": name})
        request_id = payload.client_request_id
        if request_id is not None:
            if not isinstance(request_id, str) or not _IDEMPOTENCY_CHARS.fullmatch(
                request_id
            ):
                raise CleanroomException(
                    400, "INVALID_REQUEST", "client_request_id长度或格式不合法"
                )
        if normalized and not request_id:
            raise CleanroomException(
                400, "INVALID_REQUEST", "包含阶段门的项目创建必须提供client_request_id"
            )
        return normalized

    @staticmethod
    def _request_digest(
        payload: ProjectCreateRequest, gates: list[dict[str, str]]
    ) -> str:
        normalized = {
            "name": payload.name,
            "project_type": payload.project_type.value,
            "description": payload.description,
            "start_at": payload.start_at,
            "due_at": payload.due_at,
            "gates": gates,
        }
        encoded = json.dumps(
            normalized, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()

    @staticmethod
    def _idempotency_key(owner_key: str, client_request_id: str) -> str:
        return hashlib.sha256(
            (owner_key + "\0" + client_request_id).encode("utf-8")
        ).hexdigest()

    @staticmethod
    def _require_owner_key(owner_key: str | None) -> str:
        if not isinstance(owner_key, str) or not _OWNER_KEY_PATTERN.fullmatch(
            owner_key
        ):
            raise CleanroomException(
                400, "PROJECT_OWNER_REQUIRED", "创建项目需要服务端认证的稳定owner"
            )
        return owner_key

    def list_projects(
        self,
        archived: bool | None = False,
        deleted: bool | None = None,
        *,
        viewer_owner_key: str | None = None,
        viewer_role: str = "",
    ) -> list[ProjectItem]:
        """根据归档与回收站过滤条件返回项目列表；提供查看者身份时过滤其不可读项目。"""
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
                if (
                    viewer_owner_key is not None or viewer_role
                ) and not self._list_read_decision(
                    state, item.project_id, viewer_owner_key, viewer_role
                ):
                    continue
                results.append(item)
            return sorted(results, key=lambda item: item.project_id, reverse=True)

    def get_project(self, project_id: str) -> ProjectItem:
        """读取项目字段，不在普通项目响应中序列化owner、门或幂等记录。"""
        with self._operation() as (_, state, __):
            raw = state["projects"].get(project_id)
            if raw is None:
                raise CleanroomException(
                    404, "PROJECT_NOT_FOUND", f"项目 {project_id} 不存在"
                )
            return ProjectItem.model_validate(raw)

    def get_owned_project(self, project_id: str, owner_key: str | None) -> ProjectItem:
        """只在稳定主体仍是项目真源owner时返回项目，隐藏其他项目的存在性。"""
        with self._operation() as (_, state, __):
            raw = state["projects"].get(project_id)
            if raw is None or state["owners"].get(project_id) != owner_key:
                raise CleanroomException(404, "PROJECT_NOT_FOUND", "项目不存在")
            return ProjectItem.model_validate(raw)

    def require_project_write_access(
        self, project_id: str, *, owner_key: str | None, role: str
    ) -> ProjectItem:
        """校验项目写权限；非项目写者统一隐藏项目存在性。写拒绝路径无副作用且不复制或持久化 state。"""
        with self._operation() as (_, state, __):
            raw = state["projects"].get(project_id)
            if raw is None:
                raise CleanroomException(404, "PROJECT_NOT_FOUND", "项目不存在")
            item = ProjectItem.model_validate(raw)
            if not self._read_decision(state, project_id, owner_key, role):
                raise CleanroomException(404, "PROJECT_NOT_FOUND", "项目不存在")
            if not self._validate_owner_access(state, project_id, owner_key, role):
                raise CleanroomException(404, "PROJECT_NOT_FOUND", "项目不存在")
            if item.archived_at is not None or item.deleted_at is not None:
                raise CleanroomException(
                    403, "PROJECT_READ_ONLY", "归档或回收站项目为只读状态"
                )
            return item

    def project_id_for_gate(self, gate_id: str) -> str:
        """按项目门稳定 ID 解析所属项目；未知门沿用项目门 404 契约。"""
        with self._operation() as (_, state, __):
            gate = state["gates"].get(gate_id)
            if gate is None:
                raise CleanroomException(404, "PROJECT_GATE_NOT_FOUND", "项目门不存在")
            return str(gate["project_id"])

    # ---------------------------------------------------------------------------
    # C4-AC7/AC8 显式读授权（owner 决定模型）
    # ---------------------------------------------------------------------------

    @staticmethod
    def _list_read_decision(
        state: dict[str, Any],
        project_id: str,
        owner_key: str | None,
        role: str,
    ) -> bool:
        """项目列表对缺少读授权记录的存量项目保持 legacy-open 可见性。"""
        record = (state.get("read_access") or {}).get(project_id)
        if record is None:
            return True
        return ProjectsService._read_decision(state, project_id, owner_key, role)

    @staticmethod
    def _read_decision(
        state: dict[str, Any],
        project_id: str,
        owner_key: str | None,
        role: str,
    ) -> bool:
        """读权限判定：全局治理角色与项目owner恒可读；legacy_open 除显式撤销外开放；
        explicit 模式仅授权名单可读。"""
        if str(role or "").lower() in _GOVERNANCE_ROLES:
            return True
        if owner_key and state["owners"].get(project_id) == owner_key:
            return True
        record = (state.get("read_access") or {}).get(project_id)
        if record is None or record.get("mode") == "legacy_open":
            revoked = (record or {}).get("revoked") or []
            return not (owner_key and owner_key in revoked)
        return bool(owner_key) and owner_key in ((record or {}).get("grants") or [])

    @staticmethod
    def _grants_view(project_id: str, record: dict[str, Any]) -> dict[str, Any]:
        """对外视图：owner_key 只暴露 12 位前缀，绝不回显完整键或明文主体。"""
        return {
            "project_id": project_id,
            "mode": record["mode"],
            "grants": [
                key[:_READ_KEY_PREFIX_LEN] for key in record.get("grants") or []
            ],
            "revoked": [
                key[:_READ_KEY_PREFIX_LEN] for key in record.get("revoked") or []
            ],
            "version": int(record.get("version") or 1),
        }

    def _effective_read_record(
        self, state: dict[str, Any], project_id: str
    ) -> dict[str, Any]:
        record = (state.get("read_access") or {}).get(project_id)
        if record is None:
            return {"mode": "legacy_open", "grants": [], "revoked": [], "version": 1}
        return record

    @staticmethod
    def _append_read_audit(
        state: dict[str, Any],
        event: str,
        project_id: str,
        actor_owner_key: str,
        target_owner_key: str | None,
    ) -> None:
        """审计只落 owner_key 12 位前缀；超出容量丢弃最旧条目。"""
        entries = state.setdefault("read_audit", [])
        entries.append(
            {
                "event": event,
                "project_id": project_id,
                "actor": actor_owner_key[:_READ_KEY_PREFIX_LEN],
                "target": (
                    target_owner_key[:_READ_KEY_PREFIX_LEN]
                    if target_owner_key
                    else None
                ),
                "at": _now_iso(),
            }
        )
        if len(entries) > _READ_AUDIT_CAP:
            del entries[: len(entries) - _READ_AUDIT_CAP]

    @classmethod
    def _require_read_grant_manager(
        cls,
        state: dict[str, Any],
        project_id: str,
        owner_key: str | None,
        role: str,
    ) -> None:
        """只有项目owner与全局 admin 可管理读授权；非owner治理角色同样 403。"""
        if str(role or "").lower() == "admin":
            return
        if owner_key and state["owners"].get(project_id) == owner_key:
            return
        raise CleanroomException(
            403, "FORBIDDEN", "仅项目owner或管理员可管理项目读授权"
        )

    def get_readable_project(self, project_id: str, context: Any) -> ProjectItem:
        """按读授权语义读取项目：未知项目 404，存在但不可读 403 PROJECT_READ_FORBIDDEN。"""
        with self._operation() as (_, state, __):
            raw = state["projects"].get(project_id)
            if raw is None:
                raise CleanroomException(
                    404, "PROJECT_NOT_FOUND", f"项目 {project_id} 不存在"
                )
            owner_key = None
            role = str(getattr(context, "role", "") or "")
            if context is not None:
                try:
                    owner_key = owner_key_for_context(context)
                except CleanroomException:
                    owner_key = None
            if not self._read_decision(state, project_id, owner_key, role):
                raise CleanroomException(
                    403, "PROJECT_READ_FORBIDDEN", "无当前项目读权限"
                )
            return ProjectItem.model_validate(raw)

    def can_read_project(
        self, project_id: str, *, owner_key: str | None, role: str
    ) -> bool:
        """判断给定主体是否可读项目；未知项目一律 False。"""
        with self._operation() as (_, state, __):
            if project_id not in state["projects"]:
                return False
            return self._read_decision(state, project_id, owner_key, role)

    def list_read_grants(
        self,
        project_id: str,
        *,
        owner_key: str | None,
        role: str,
    ) -> dict[str, Any]:
        """owner/admin 读取项目读授权视图（脱敏键前缀）。"""
        with self._operation() as (_, state, __):
            if project_id not in state["projects"]:
                raise CleanroomException(404, "PROJECT_NOT_FOUND", "项目不存在")
            self._require_read_grant_manager(state, project_id, owner_key, role)
            return self._grants_view(
                project_id, self._effective_read_record(state, project_id)
            )

    def _change_read_access(
        self,
        project_id: str,
        *,
        actor_owner_key: str | None,
        actor_role: str,
        change: Callable[[dict[str, Any], str], tuple | None],
    ) -> dict[str, Any]:
        """在项目域锁内校验管理者身份并应用读授权变更；无实际变化时不写盘、不改版本。"""
        if not actor_owner_key:
            raise CleanroomException(403, "FORBIDDEN", "当前认证身份不具备稳定项目主体")
        with self._operation() as (root, state, __):
            if project_id not in state["projects"]:
                raise CleanroomException(404, "PROJECT_NOT_FOUND", "项目不存在")
            self._require_read_grant_manager(
                state, project_id, actor_owner_key, actor_role
            )
            record = copy.deepcopy(self._effective_read_record(state, project_id))
            event = change(record, state["owners"][project_id])
            if event is None:
                return self._grants_view(project_id, record)
            applied_event, target_key = event
            record["version"] = int(record.get("version") or 0) + 1
            next_state = copy.deepcopy(state)
            next_state.setdefault("read_access", {})[project_id] = record
            self._append_read_audit(
                next_state, applied_event, project_id, actor_owner_key, target_key
            )
            next_state["revision"] = int(next_state.get("revision") or 1) + 1
            self._commit_state(root, next_state)
            return self._grants_view(project_id, record)

    @staticmethod
    def _require_target(target_owner_key: Any) -> None:
        if not isinstance(target_owner_key, str) or not _OWNER_KEY_PATTERN.fullmatch(
            target_owner_key
        ):
            raise CleanroomException(400, "INVALID_REQUEST", "目标主体身份不合法")

    def grant_read(
        self,
        project_id: str,
        target_owner_key: str,
        *,
        actor_owner_key: str | None,
        actor_role: str,
    ) -> dict[str, Any]:
        """把目标主体加入授权名单，并从撤销名单移除；owner 不可被授权。幂等。"""
        self._require_target(target_owner_key)

        def change(record: dict[str, Any], owner: str) -> tuple | None:
            if target_owner_key == owner:
                raise CleanroomException(
                    400, "INVALID_REQUEST", "项目owner恒可读，不能授权或撤销"
                )
            if (
                target_owner_key in record["grants"]
                and target_owner_key not in record["revoked"]
            ):
                return None
            if target_owner_key not in record["grants"]:
                record["grants"].append(target_owner_key)
            record["revoked"] = [k for k in record["revoked"] if k != target_owner_key]
            return ("project.read_granted", target_owner_key)

        return self._change_read_access(
            project_id,
            actor_owner_key=actor_owner_key,
            actor_role=actor_role,
            change=change,
        )

    def revoke_read(
        self,
        project_id: str,
        target_owner_key: str,
        *,
        actor_owner_key: str | None,
        actor_role: str,
    ) -> dict[str, Any]:
        """把目标主体移出授权名单并加入撤销名单；幂等；owner 不可被撤销。"""
        self._require_target(target_owner_key)

        def change(record: dict[str, Any], owner: str) -> tuple | None:
            if target_owner_key == owner:
                raise CleanroomException(
                    400, "INVALID_REQUEST", "项目owner恒可读，不能授权或撤销"
                )
            if (
                target_owner_key in record["revoked"]
                and target_owner_key not in record["grants"]
            ):
                return None
            record["grants"] = [k for k in record["grants"] if k != target_owner_key]
            if target_owner_key not in record["revoked"]:
                record["revoked"].append(target_owner_key)
            return ("project.read_revoked", target_owner_key)

        return self._change_read_access(
            project_id,
            actor_owner_key=actor_owner_key,
            actor_role=actor_role,
            change=change,
        )

    def set_read_mode_explicit(
        self, project_id: str, *, actor_owner_key: str | None, actor_role: str
    ) -> dict[str, Any]:
        """把项目切到 explicit 模式（单向，不可切回）；已处于 explicit 时幂等。"""

        def change(record: dict[str, Any], owner: str) -> tuple | None:
            if record["mode"] == "explicit":
                return None
            record["mode"] = "explicit"
            return ("project.read_mode_explicit", None)

        return self._change_read_access(
            project_id,
            actor_owner_key=actor_owner_key,
            actor_role=actor_role,
            change=change,
        )

    def _get_source_owner_key(self, project_id: str) -> str:
        """在项目域锁及完整真源校验内读取内部owner键，不序列化到普通API响应。"""
        with self._operation() as (_, state, __):
            if project_id not in state["projects"]:
                raise CleanroomException(404, "PROJECT_NOT_FOUND", "项目不存在")
            owner_key = state["owners"].get(project_id)
            if not isinstance(owner_key, str) or not _OWNER_KEY_PATTERN.fullmatch(
                owner_key
            ):
                raise _state_error(
                    "PROJECT_STATE_CORRUPT", "项目owner真源不合法，系统已失败关闭"
                )
            return owner_key

    def create_project(
        self,
        payload: ProjectCreateRequest,
        *,
        owner_key: str | None = None,
        before_publish: Callable[[str], None] | None = None,
    ) -> ProjectMutationResult:
        """预留不可复用项目 ID，执行发布前回调，再原子提交项目、owner、读授权、门与幂等快照。"""
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
            idem_key = (
                self._idempotency_key(effective_owner, request_id)
                if request_id
                else None
            )
            if idem_key:
                existing = state["idempotency"].get(idem_key)
                if existing is not None:
                    if existing["request_digest"] != digest:
                        raise CleanroomException(
                            409,
                            "IDEMPOTENCY_CONFLICT",
                            "相同client_request_id已用于不同项目请求",
                        )
                    return ProjectMutationResult(
                        project_id=existing["project_id"], version=1
                    )

            if root is not None and reservations is not None:
                next_reservations = dict(reservations)
                next_reservations["project_sequence"] += 1
                project_sequence = next_reservations["project_sequence"]
                first_gate_sequence = next_reservations["gate_sequence"] + 1
                next_reservations["gate_sequence"] += len(gates)
                self._persist_reservations(root, next_reservations)
                namespace = next_reservations["namespace"]
                project_id = f"prj-p-{namespace}-{project_sequence}"
                gate_ids = [
                    f"gate-p-{namespace}-{first_gate_sequence + offset}"
                    for offset in range(len(gates))
                ]
            else:
                self._seq += 1
                project_id = f"prj-{self._seq:04d}"
                first_gate_sequence = self._gate_seq + 1
                self._gate_seq += len(gates)
                gate_ids = [
                    f"gate-test-{first_gate_sequence + offset:04d}"
                    for offset in range(len(gates))
                ]

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
            # C4-AC7/AC8：本变更之后创建的项目一律 explicit 模式，授权名单为空。
            next_state.setdefault("read_access", {})[project_id] = {
                "mode": "explicit",
                "grants": [],
                "revoked": [],
                "version": 1,
            }
            # C4-AC7：新建项目一律 explicit 读授权（仅 owner + admin/governor + 授权名单）。
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

    def update_project(
        self, project_id: str, payload: ProjectUpdateRequest
    ) -> ProjectMutationResult:
        """根据 CAS 乐观锁更新项目，并只在完整快照成功后发布。"""
        with self._operation() as (root, state, __):
            raw = state["projects"].get(project_id)
            if raw is None:
                raise CleanroomException(
                    404, "PROJECT_NOT_FOUND", f"项目 {project_id} 不存在"
                )
            item = ProjectItem.model_validate(raw)
            if item.version != payload.expected_version:
                raise VersionConflictException(
                    expected_version=payload.expected_version,
                    current_version=item.version,
                )
            if item.archived_at is not None or item.deleted_at is not None:
                raise CleanroomException(403, "FORBIDDEN", "归档或回收站项目为只读状态")

            fields_set = payload.model_fields_set
            next_start = payload.start_at if "start_at" in fields_set else item.start_at
            next_due = payload.due_at if "due_at" in fields_set else item.due_at
            if (
                next_start is not None
                and next_due is not None
                and next_due < next_start
            ):
                raise CleanroomException(
                    400, "INVALID_SCHEDULE", "due_at 不能早于 start_at"
                )
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

    def _lifecycle(
        self, project_id: str, expected_version: int, operation: str
    ) -> ProjectMutationResult:
        with self._operation() as (root, state, __):
            raw = state["projects"].get(project_id)
            if raw is None:
                raise CleanroomException(
                    404, "PROJECT_NOT_FOUND", f"项目 {project_id} 不存在"
                )
            item = ProjectItem.model_validate(raw)
            if item.version != expected_version:
                raise VersionConflictException(
                    expected_version=expected_version, current_version=item.version
                )
            if operation == "archive":
                if item.deleted_at is not None or item.archived_at is not None:
                    raise CleanroomException(
                        409, "LIFECYCLE_CONFLICT", "项目当前不在活跃状态"
                    )
                item.archived_at = _now_iso()
            elif operation == "unarchive":
                if item.deleted_at is not None or item.archived_at is None:
                    raise CleanroomException(
                        409, "LIFECYCLE_CONFLICT", "项目当前不是已归档状态"
                    )
                item.archived_at = None
            elif operation == "trash":
                if item.deleted_at is not None or item.archived_at is None:
                    raise CleanroomException(
                        409, "LIFECYCLE_CONFLICT", "只有已归档项目可以进入回收站"
                    )
                item.deleted_at = _now_iso()
            elif operation == "restore":
                if item.deleted_at is None:
                    raise CleanroomException(
                        409, "LIFECYCLE_CONFLICT", "项目不在回收站"
                    )
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

    def archive_project(
        self, project_id: str, expected_version: int
    ) -> ProjectMutationResult:
        """归档项目（CAS 校验）。"""
        return self._lifecycle(project_id, expected_version, "archive")

    def unarchive_project(
        self, project_id: str, expected_version: int
    ) -> ProjectMutationResult:
        """解归档项目（CAS 校验）。"""
        return self._lifecycle(project_id, expected_version, "unarchive")

    def move_to_trash(
        self, project_id: str, expected_version: int
    ) -> ProjectMutationResult:
        """移入回收站（CAS 校验）。"""
        return self._lifecycle(project_id, expected_version, "trash")

    def restore_from_trash(
        self, project_id: str, expected_version: int
    ) -> ProjectMutationResult:
        """从回收站恢复（CAS 校验）。"""
        return self._lifecycle(project_id, expected_version, "restore")

    @staticmethod
    def _validate_owner_access(
        state: dict[str, Any], project_id: str, owner_key: str | None, role: str
    ) -> bool:
        return str(role or "").lower() in _GOVERNANCE_ROLES or bool(
            owner_key and state["owners"].get(project_id) == owner_key
        )

    def list_project_gates(
        self,
        project_id: str,
        *,
        owner_key: str | None,
        role: str,
    ) -> list[dict[str, Any]]:
        """按项目owner或治理角色发现阶段门；无权发现返回空集合。"""
        with self._operation() as (_, state, __):
            if project_id not in state["projects"]:
                raise CleanroomException(404, "PROJECT_NOT_FOUND", "项目不存在")
            if not self._validate_owner_access(state, project_id, owner_key, role):
                return []
            records = [
                copy.deepcopy(gate)
                for gate in state["gates"].values()
                if gate["project_id"] == project_id
            ]
            return sorted(
                records,
                key=lambda gate: (
                    _gate_sequence(gate["gate_id"]) or 0,
                    gate["gate_id"],
                ),
            )

    def get_project_gate(
        self,
        gate_id: str,
        *,
        owner_key: str | None,
        role: str,
    ) -> dict[str, Any]:
        """读取单个门；未知与无权统一隐藏为404。"""
        with self._operation() as (_, state, __):
            gate = state["gates"].get(gate_id)
            if gate is None or not self._validate_owner_access(
                state, gate["project_id"], owner_key, role
            ):
                raise CleanroomException(404, "PROJECT_GATE_NOT_FOUND", "项目门不存在")
            return copy.deepcopy(gate)

    def update_project_gate(
        self,
        gate_id: str,
        payload: dict[str, Any],
        *,
        owner_key: str | None,
        role: str,
    ) -> dict[str, Any]:
        """在与项目归档/回收共用的域锁内执行门权限、只读检查与CAS。"""
        with self._operation() as (root, state, __):
            gate = state["gates"].get(gate_id)
            if gate is None or not self._validate_owner_access(
                state, gate["project_id"], owner_key, role
            ):
                raise CleanroomException(404, "PROJECT_GATE_NOT_FOUND", "项目门不存在")
            if str(role or "").lower() not in _GATE_WRITER_ROLES:
                raise CleanroomException(
                    403, "FORBIDDEN", "当前账户为只读角色，不能更新项目门"
                )
            project = ProjectItem.model_validate(state["projects"][gate["project_id"]])
            if project.archived_at is not None or project.deleted_at is not None:
                raise CleanroomException(
                    403, "PROJECT_READ_ONLY", "归档或回收站项目门为只读状态"
                )

            expected_version = payload.get("expected_version")
            if type(expected_version) is not int or expected_version < 1:
                raise CleanroomException(
                    400, "INVALID_REQUEST", "expected_version必须为正整数"
                )
            if gate["version"] != expected_version:
                raise VersionConflictException(
                    expected_version=expected_version, current_version=gate["version"]
                )

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
                raise CleanroomException(
                    409, "INVALID_GATE_TRANSITION", "项目门状态迁移不允许"
                )

            note = payload.get("note", "")
            if note is None:
                note = ""
            if not isinstance(note, str) or len(note) > 1000:
                raise CleanroomException(400, "INVALID_REQUEST", "note长度或类型不合法")
            if current_state == "approved" and target_state == "changes_requested":
                if str(role or "").lower() not in _GOVERNANCE_ROLES:
                    raise CleanroomException(
                        403, "FORBIDDEN", "只有治理角色可以撤回已批准门"
                    )
                if not note.strip():
                    raise CleanroomException(
                        400, "INVALID_REQUEST", "撤回已批准门必须填写说明"
                    )

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


def require_project_write_context(project_id: str, context: Any) -> ProjectItem:
    """以认证上下文执行统一项目写门禁。"""
    return default_projects_service.require_project_write_access(
        project_id,
        owner_key=owner_key_for_context(context),
        role=str(getattr(context, "role", "") or ""),
    )
