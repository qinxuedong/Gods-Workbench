"""生产链关键步骤的用户确认门禁状态机（MVP2-T3）。

门禁以持久台账为真源：``GateRecord`` 存 SQLite，进程重启后状态与操作
历史完整恢复。与 ``agent_runtime`` 的 run 级恢复（recovery）和预算门禁
（policy）互补，不重复其职责；也未改动 lifecycle 的产物版本语义。
"""

from __future__ import annotations

import json
import sqlite3
from enum import StrEnum
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field

from gw.agent_runtime.errors import DomainError
from gw.agent_runtime.models import (
    ErrorCode,
    Identifier,
    ToolCostState,
    UtcDateTime,
    Version,
)
from gw.agent_runtime.repository import utc_now
from gw.agent_runtime.storage.sqlite import SQLiteDatabase

_HISTORY_MAX_REASON = 512


class GateKind(StrEnum):
    """需要用户确认的生产链关键步骤。"""

    script = "script"
    storyboard = "storyboard"
    animatic = "animatic"
    i2v_submit = "i2v_submit"
    master_output = "master_output"


class GateState(StrEnum):
    pending = "pending"
    approved = "approved"
    changes_requested = "changes_requested"
    rolled_back = "rolled_back"


# 显式白名单：pending 可确认/要求修改/退回；changes_requested 重做后回到
# pending 再次请求；approved 与 rolled_back 为终态，无任何出边。
_ALLOWED_TRANSITIONS: dict[GateState, frozenset[GateState]] = {
    GateState.pending: frozenset(
        {GateState.approved, GateState.changes_requested, GateState.rolled_back}
    ),
    GateState.changes_requested: frozenset({GateState.pending}),
    GateState.approved: frozenset(),
    GateState.rolled_back: frozenset(),
}


def is_transition_allowed(current: GateState, target: GateState) -> bool:
    """判断一次状态转移是否命中显式白名单；终态恒为 ``False``。"""
    return target in _ALLOWED_TRANSITIONS[current]


class GateTransitionRecord(BaseModel):
    """一次状态转移的不可变操作日志条目。"""

    model_config = ConfigDict(extra="forbid")

    from_state: GateState
    to_state: GateState
    actor_id: Identifier
    reason: str = Field(min_length=1, max_length=_HISTORY_MAX_REASON)
    at: UtcDateTime


class GateRecord(BaseModel):
    """单个门禁的持久状态；``version`` 供乐观锁 CAS 使用。"""

    model_config = ConfigDict(extra="forbid")

    gate_id: Identifier
    run_id: Identifier
    kind: GateKind
    state: GateState
    version: Version
    history: tuple[GateTransitionRecord, ...] = ()


class GateCostRegistration(Protocol):
    """工具注册的最小结构化视图：仅依赖 ``cost_state`` 判定是否受门禁约束。"""

    cost_state: ToolCostState


def require_gate_for_tool(
    registration: GateCostRegistration,
    gate_state: GateState | None,
    *,
    request_id: str,
) -> None:
    """工具执行前的门禁校验：metered/unknown 成本未批准即拒绝（fail closed）。

    ``free`` 工具不受门禁限制；``gate_state`` 为 ``None`` 表示尚未建立门禁，
    对非 free 工具一律视为未批准。
    """
    cost_state = getattr(registration, "cost_state", ToolCostState.free)
    if cost_state is ToolCostState.free:
        return
    if gate_state is not GateState.approved:
        raise DomainError(
            ErrorCode.MANUAL_REVIEW_REQUIRED,
            "A metered or unknown-cost tool requires an approved gate before execution.",
            request_id=request_id,
        )


def _history_json(history: tuple[GateTransitionRecord, ...]) -> str:
    return json.dumps(
        [entry.model_dump(mode="json") for entry in history],
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )


def _record_from_row(row: sqlite3.Row) -> GateRecord:
    history = tuple(
        GateTransitionRecord.model_validate(entry)
        for entry in json.loads(row["history_json"])
    )
    return GateRecord(
        gate_id=Identifier(row["gate_id"]),
        run_id=Identifier(row["run_id"]),
        kind=GateKind(row["kind"]),
        state=GateState(row["state"]),
        version=Version(row["version"]),
        history=history,
    )


class GateStore:
    """``GateRecord`` 的 SQLite 持久台账。

    复用 ``SQLiteDatabase`` 的连接工厂与路径隔离；表结构在本模块内
    ``CREATE TABLE IF NOT EXISTS``，不改动共享的 sqlite.py 建表脚本。
    同一文件重建实例即可恢复全部状态与历史（模拟重启）。
    """

    def __init__(self, database: SQLiteDatabase | str | None = None) -> None:
        self.database = (
            database if isinstance(database, SQLiteDatabase) else SQLiteDatabase(database)
        )
        self._initialize()

    def _initialize(self) -> None:
        with self.database.connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS agent_gates (
                    gate_id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    state TEXT NOT NULL,
                    version INTEGER NOT NULL CHECK(version >= 0),
                    history_json TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS ix_agent_gates_run
                    ON agent_gates(run_id, gate_id);
                """
            )

    def create(
        self,
        *,
        gate_id: Identifier,
        run_id: Identifier,
        kind: GateKind,
        request_id: str,
    ) -> GateRecord:
        """新建门禁，初始 ``pending``/version 0/空历史；重复 id 拒绝。"""
        record = GateRecord(
            gate_id=gate_id,
            run_id=run_id,
            kind=kind,
            state=GateState.pending,
            version=Version(0),
        )
        connection = self.database.transaction()
        try:
            connection.execute(
                "INSERT INTO agent_gates(gate_id, run_id, kind, state, version, history_json)"
                " VALUES(?, ?, ?, ?, ?, ?)",
                (
                    record.gate_id.root,
                    record.run_id.root,
                    record.kind.value,
                    record.state.value,
                    record.version.root,
                    _history_json(record.history),
                ),
            )
            self.database.close_commit(connection)
            return record
        except sqlite3.IntegrityError as exc:
            self.database.close_rollback(connection)
            raise DomainError(
                ErrorCode.VALIDATION_FAILED,
                "A gate with this id already exists.",
                request_id=request_id,
            ) from exc
        except Exception:
            self.database.close_rollback(connection)
            raise

    def get(self, gate_id: Identifier) -> GateRecord | None:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT gate_id, run_id, kind, state, version, history_json"
                " FROM agent_gates WHERE gate_id=?",
                (gate_id.root,),
            ).fetchone()
        return _record_from_row(row) if row is not None else None

    def list_for_run(self, run_id: Identifier) -> tuple[GateRecord, ...]:
        with self.database.connect() as connection:
            rows = connection.execute(
                "SELECT gate_id, run_id, kind, state, version, history_json"
                " FROM agent_gates WHERE run_id=? ORDER BY gate_id",
                (run_id.root,),
            ).fetchall()
        return tuple(_record_from_row(row) for row in rows)

    def transition(
        self,
        *,
        gate_id: Identifier,
        target: GateState,
        actor_id: Identifier,
        reason: str,
        expected_version: int,
        request_id: str,
    ) -> GateRecord:
        """在写事务内执行 CAS 状态转移；每次转移追加一条历史记录。"""
        connection = self.database.transaction()
        try:
            row = connection.execute(
                "SELECT gate_id, run_id, kind, state, version, history_json"
                " FROM agent_gates WHERE gate_id=?",
                (gate_id.root,),
            ).fetchone()
            if row is None:
                raise DomainError(
                    ErrorCode.VALIDATION_FAILED,
                    "The gate does not exist.",
                    request_id=request_id,
                )
            current = _record_from_row(row)
            if expected_version != current.version.root:
                raise DomainError(
                    ErrorCode.REVISION_CONFLICT,
                    "The gate version changed; reload state before deciding again.",
                    request_id=request_id,
                )
            if not is_transition_allowed(current.state, target):
                raise DomainError(
                    ErrorCode.VALIDATION_FAILED,
                    "Gate transition "
                    f"{current.state.value} -> {target.value} is not allowed.",
                    request_id=request_id,
                )
            entry = GateTransitionRecord(
                from_state=current.state,
                to_state=target,
                actor_id=actor_id,
                reason=reason,
                at=UtcDateTime(utc_now()),
            )
            updated = current.model_copy(
                update={
                    "state": target,
                    "version": Version(current.version.root + 1),
                    "history": (*current.history, entry),
                }
            )
            connection.execute(
                "UPDATE agent_gates SET state=?, version=?, history_json=?"
                " WHERE gate_id=? AND version=?",
                (
                    updated.state.value,
                    updated.version.root,
                    _history_json(updated.history),
                    gate_id.root,
                    current.version.root,
                ),
            )
            self.database.close_commit(connection)
            return updated
        except Exception:
            self.database.close_rollback(connection)
            raise


__all__ = [
    "GateCostRegistration",
    "GateKind",
    "GateRecord",
    "GateState",
    "GateStore",
    "GateTransitionRecord",
    "is_transition_allowed",
    "require_gate_for_tool",
]
