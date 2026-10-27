"""Contract tests for MVP2-T3 Agent gate state machine.

Covers AC1-AC5:
- AC1: Legal state transitions succeed; illegal state transitions raise DomainError.
- AC2: expected_version optimistic lock conflict is rejected.
- AC3: SQLite persistence recovers gate state and history intact after database re-initialization (simulating process restart).
- AC4: Transitions append history entries with operation logs.
- AC5: Metered/unknown cost tools are rejected when gate is not approved (fail closed); free tools are unconstrained.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from gw.agent_episode.gates import GateManager, require_gate_for_tool
from gw.agent_runtime.errors import DomainError
from gw.agent_runtime.models import (
    ErrorCode,
    GateKind,
    GateState,
    Identifier,
    ToolCostState,
)
from gw.agent_runtime.storage.sqlite import SQLiteDatabase


@pytest.fixture
def sqlite_db(tmp_path):
    db_path = tmp_path / "runtime.sqlite3"
    return SQLiteDatabase(db_path, trusted_root=tmp_path)


def test_ac1_legal_and_illegal_transitions(sqlite_db):
    mgr = GateManager(sqlite_db)
    gate = mgr.create_gate("g1", "r1", GateKind.script)
    assert gate.state == GateState.pending
    assert gate.version.root == 0

    # pending -> changes_requested
    gate = mgr.transition("g1", GateState.changes_requested, "user1", "Need character depth fix")
    assert gate.state == GateState.changes_requested
    assert gate.version.root == 1

    # changes_requested -> pending
    gate = mgr.transition("g1", GateState.pending, "agent1", "Revised script submitted")
    assert gate.state == GateState.pending
    assert gate.version.root == 2

    # pending -> approved
    gate = mgr.transition("g1", GateState.approved, "user1", "Looks great")
    assert gate.state == GateState.approved
    assert gate.version.root == 3

    # approved is terminal state -> any transition should fail
    with pytest.raises(DomainError) as exc_info:
        mgr.transition("g1", GateState.pending, "agent1", "Try reopening")
    assert exc_info.value.code == ErrorCode.VALIDATION_FAILED


def test_ac1_rollback_transition(sqlite_db):
    mgr = GateManager(sqlite_db)
    gate = mgr.create_gate("g2", "r1", GateKind.storyboard)

    # pending -> rolled_back
    gate = mgr.transition("g2", GateState.rolled_back, "user1", "Cancel storyboard")
    assert gate.state == GateState.rolled_back

    # rolled_back is terminal state
    with pytest.raises(DomainError) as exc_info:
        mgr.transition("g2", GateState.pending, "agent1", "Reopen")
    assert exc_info.value.code == ErrorCode.VALIDATION_FAILED


def test_ac2_optimistic_locking_conflict(sqlite_db):
    mgr = GateManager(sqlite_db)
    gate = mgr.create_gate("g3", "r1", GateKind.animatic)
    assert gate.version.root == 0

    # Wrong expected version raises REVISION_CONFLICT
    with pytest.raises(DomainError) as exc_info:
        mgr.transition("g3", GateState.approved, "user1", "Approve", expected_version=99)
    assert exc_info.value.code == ErrorCode.REVISION_CONFLICT

    # Correct expected version succeeds
    gate = mgr.transition("g3", GateState.approved, "user1", "Approve", expected_version=0)
    assert gate.version.root == 1


def test_ac3_persistence_and_restart_recovery(sqlite_db, tmp_path):
    mgr = GateManager(sqlite_db)
    mgr.create_gate("g4", "r1", GateKind.i2v_submit)
    mgr.transition("g4", GateState.changes_requested, "user1", "Change prompt")
    mgr.transition("g4", GateState.pending, "agent1", "New prompt ready")
    mgr.transition("g4", GateState.approved, "user1", "Prompt approved")

    # Re-instantiate SQLiteDatabase with same path (simulating process restart)
    db_path = sqlite_db.path
    new_db = SQLiteDatabase(db_path, trusted_root=tmp_path)
    new_mgr = GateManager(new_db)

    recovered_gate = new_mgr.get_gate("g4")
    assert recovered_gate.gate_id.root == "g4"
    assert recovered_gate.run_id.root == "r1"
    assert recovered_gate.kind == GateKind.i2v_submit
    assert recovered_gate.state == GateState.approved
    assert recovered_gate.version.root == 3
    assert len(recovered_gate.history) == 3


def test_ac4_history_logging(sqlite_db):
    mgr = GateManager(sqlite_db)
    mgr.create_gate("g5", "r1", GateKind.master_output)

    mgr.transition("g5", GateState.changes_requested, "user1", "Master needs audio sync")
    mgr.transition("g5", GateState.pending, "agent1", "Audio resynced")
    gate = mgr.transition("g5", GateState.approved, "user1", "Master approved")

    history = gate.history
    assert len(history) == 3

    assert history[0].from_state == GateState.pending
    assert history[0].to_state == GateState.changes_requested
    assert history[0].actor_id.root == "user1"
    assert history[0].reason.root == "Master needs audio sync"

    assert history[1].from_state == GateState.changes_requested
    assert history[1].to_state == GateState.pending
    assert history[1].actor_id.root == "agent1"
    assert history[1].reason.root == "Audio resynced"

    assert history[2].from_state == GateState.pending
    assert history[2].to_state == GateState.approved
    assert history[2].actor_id.root == "user1"
    assert history[2].reason.root == "Master approved"


def test_ac5_metered_and_free_tool_gate_requirements():
    free_tool = SimpleNamespace(
        tool_id=Identifier(root="search_tool"),
        cost_state=ToolCostState.free,
    )
    metered_tool = SimpleNamespace(
        tool_id=Identifier(root="render_tool"),
        cost_state=ToolCostState.metered,
    )
    unknown_tool = SimpleNamespace(
        tool_id=Identifier(root="experimental_tool"),
        cost_state=ToolCostState.unknown,
    )

    # Free tool allowed regardless of gate state
    require_gate_for_tool(free_tool, GateState.pending)
    require_gate_for_tool(free_tool, GateState.changes_requested)
    require_gate_for_tool(free_tool, GateState.approved)
    require_gate_for_tool(free_tool, GateState.rolled_back)

    # Metered tool rejected when gate is not approved
    with pytest.raises(DomainError) as exc_info:
        require_gate_for_tool(metered_tool, GateState.pending)
    assert exc_info.value.code == ErrorCode.MANUAL_REVIEW_REQUIRED

    with pytest.raises(DomainError) as exc_info:
        require_gate_for_tool(metered_tool, GateState.changes_requested)
    assert exc_info.value.code == ErrorCode.MANUAL_REVIEW_REQUIRED

    # Unknown cost tool rejected when gate is not approved
    with pytest.raises(DomainError) as exc_info:
        require_gate_for_tool(unknown_tool, GateState.pending)
    assert exc_info.value.code == ErrorCode.MANUAL_REVIEW_REQUIRED

    # Metered / Unknown tool allowed when gate is approved
    require_gate_for_tool(metered_tool, GateState.approved)
    require_gate_for_tool(unknown_tool, GateState.approved)
