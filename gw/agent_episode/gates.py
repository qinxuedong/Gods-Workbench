"""Agent gate state machine and persistence for MVP2 production chain gates.

Covers gate creation, state transitions, operation history logging,
persistence in SQLiteDatabase, optimistic locking, and tool requirement checks.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from gw.agent_runtime.errors import DomainError
from gw.agent_runtime.models import (
    ErrorCode,
    GateHistoryEntry,
    GateKind,
    GateRecord,
    GateState,
    Identifier,
    NonEmptyText,
    ToolCostState,
    UtcDateTime,
    Version,
)
from gw.agent_runtime.storage.sqlite import SQLiteDatabase

VALID_TRANSITIONS: dict[GateState, set[GateState]] = {
    GateState.pending: {
        GateState.approved,
        GateState.changes_requested,
        GateState.rolled_back,
    },
    GateState.changes_requested: {
        GateState.pending,
    },
    GateState.approved: set(),
    GateState.rolled_back: set(),
}


def _ensure_tables(db: SQLiteDatabase) -> None:
    with db.connect() as connection:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS gates (
                gate_id TEXT PRIMARY KEY,
                run_id TEXT NOT NULL,
                kind TEXT NOT NULL,
                state TEXT NOT NULL,
                version INTEGER NOT NULL CHECK(version >= 0),
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS ix_gates_run ON gates(run_id, kind);

            CREATE TABLE IF NOT EXISTS gate_history (
                history_id INTEGER PRIMARY KEY AUTOINCREMENT,
                gate_id TEXT NOT NULL REFERENCES gates(gate_id) ON DELETE CASCADE,
                from_state TEXT NOT NULL,
                to_state TEXT NOT NULL,
                actor_id TEXT NOT NULL,
                reason TEXT NOT NULL,
                at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS ix_gate_history_gate ON gate_history(gate_id);
            """
        )


class GateManager:
    """Manages persistent gate records, transitions, history, and optimistic locking."""

    def __init__(self, db: SQLiteDatabase) -> None:
        self.db = db
        _ensure_tables(self.db)

    def create_gate(
        self,
        gate_id: str | Identifier,
        run_id: str | Identifier,
        kind: str | GateKind,
        *,
        initial_state: GateState = GateState.pending,
    ) -> GateRecord:
        g_id = gate_id.root if isinstance(gate_id, Identifier) else gate_id
        r_id = run_id.root if isinstance(run_id, Identifier) else run_id
        g_kind = kind.value if isinstance(kind, GateKind) else kind
        now_str = datetime.now(timezone.utc).isoformat()

        with self.db.connect() as conn:
            row = conn.execute(
                "SELECT gate_id FROM gates WHERE gate_id = ?", (g_id,)
            ).fetchone()
            if row:
                raise DomainError(
                    ErrorCode.IDEMPOTENCY_CONFLICT,
                    f"Gate with gate_id '{g_id}' already exists.",
                    request_id=g_id,
                )
            conn.execute(
                "INSERT INTO gates (gate_id, run_id, kind, state, version, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (g_id, r_id, g_kind, initial_state.value, 0, now_str),
            )
        return self.get_gate(g_id)

    def get_gate(self, gate_id: str | Identifier) -> GateRecord:
        g_id = gate_id.root if isinstance(gate_id, Identifier) else gate_id
        with self.db.connect() as conn:
            gate_row = conn.execute(
                "SELECT gate_id, run_id, kind, state, version FROM gates WHERE gate_id = ?",
                (g_id,),
            ).fetchone()
            if not gate_row:
                raise DomainError(
                    ErrorCode.VALIDATION_FAILED,
                    f"Gate '{g_id}' not found.",
                    request_id=g_id,
                )
            history_rows = conn.execute(
                "SELECT from_state, to_state, actor_id, reason, at FROM gate_history WHERE gate_id = ? ORDER BY history_id ASC",
                (g_id,),
            ).fetchall()

        history: list[GateHistoryEntry] = []
        for h_row in history_rows:
            history.append(
                GateHistoryEntry(
                    **{
                        "from": GateState(h_row["from_state"]),
                        "to": GateState(h_row["to_state"]),
                        "actor_id": Identifier(root=h_row["actor_id"]),
                        "reason": NonEmptyText(root=h_row["reason"]),
                        "at": UtcDateTime(root=datetime.fromisoformat(h_row["at"])),
                    }
                )
            )

        return GateRecord(
            gate_id=Identifier(root=gate_row["gate_id"]),
            run_id=Identifier(root=gate_row["run_id"]),
            kind=GateKind(gate_row["kind"]),
            state=GateState(gate_row["state"]),
            version=Version(root=gate_row["version"]),
            history=history,
        )

    def transition(
        self,
        gate_id: str | Identifier,
        to_state: GateState,
        actor_id: str | Identifier,
        reason: str | NonEmptyText,
        *,
        expected_version: int | Version | None = None,
    ) -> GateRecord:
        g_id = gate_id.root if isinstance(gate_id, Identifier) else gate_id
        a_id = actor_id.root if isinstance(actor_id, Identifier) else actor_id
        r_text = reason.root if isinstance(reason, NonEmptyText) else reason

        with self.db.connect() as conn:
            gate_row = conn.execute(
                "SELECT gate_id, state, version FROM gates WHERE gate_id = ?",
                (g_id,),
            ).fetchone()
            if not gate_row:
                raise DomainError(
                    ErrorCode.VALIDATION_FAILED,
                    f"Gate '{g_id}' not found.",
                    request_id=g_id,
                )

            current_state = GateState(gate_row["state"])
            current_version = gate_row["version"]

            if expected_version is not None:
                exp_v = (
                    expected_version.root
                    if isinstance(expected_version, Version)
                    else expected_version
                )
                if exp_v != current_version:
                    raise DomainError(
                        ErrorCode.REVISION_CONFLICT,
                        f"Gate version conflict: expected {exp_v}, found {current_version}.",
                        request_id=g_id,
                    )

            allowed = VALID_TRANSITIONS.get(current_state, set())
            if to_state not in allowed:
                raise DomainError(
                    ErrorCode.VALIDATION_FAILED,
                    f"Invalid transition from state '{current_state}' to '{to_state}'.",
                    request_id=g_id,
                )

            new_version = current_version + 1
            now_dt = datetime.now(timezone.utc)
            now_str = now_dt.isoformat()

            cursor = conn.execute(
                "UPDATE gates SET state = ?, version = ? WHERE gate_id = ? AND version = ?",
                (to_state.value, new_version, g_id, current_version),
            )
            if cursor.rowcount == 0:
                raise DomainError(
                    ErrorCode.REVISION_CONFLICT,
                    "Concurrent modification detected during gate transition.",
                    request_id=g_id,
                )

            conn.execute(
                "INSERT INTO gate_history (gate_id, from_state, to_state, actor_id, reason, at) VALUES (?, ?, ?, ?, ?, ?)",
                (g_id, current_state.value, to_state.value, a_id, r_text, now_str),
            )

        return self.get_gate(g_id)


def require_gate_for_tool(registration: Any, gate_state: GateState | str) -> None:
    """Fail closed for non-free tools when gate is not approved."""
    cost_state = getattr(registration, "cost_state", None)
    state_val = gate_state if isinstance(gate_state, GateState) else GateState(gate_state)

    if cost_state is not ToolCostState.free:
        if state_val is not GateState.approved:
            req_id = getattr(getattr(registration, "tool_id", None), "root", "tool")
            raise DomainError(
                ErrorCode.MANUAL_REVIEW_REQUIRED,
                f"Gate state '{state_val}' is not approved for tool requiring budget/cost approval.",
                request_id=str(req_id),
            )


__all__ = [
    "GateManager",
    "require_gate_for_tool",
]
