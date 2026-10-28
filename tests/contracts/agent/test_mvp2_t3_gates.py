"""QIN-441（MVP2-T3）Agent 门禁状态机契约测试。

覆盖票面 AC1~AC5：合法/非法转移、expected_version 乐观锁、同一 SQLite
文件重建后的状态与历史恢复、转移历史追加、metered/unknown 工具 fail closed。
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta

import pytest

from gw.agent_episode.gates import (
    GateKind,
    GateRecord,
    GateState,
    GateStore,
    is_transition_allowed,
    require_gate_for_tool,
)
from gw.agent_runtime.errors import DomainError
from gw.agent_runtime.models import (
    ErrorCode,
    HostToolCapabilities,
    Identifier,
    ToolCostState,
    ToolRiskLevel,
)
from gw.agent_runtime.policy import ToolRegistration
from gw.agent_runtime.storage.sqlite import SQLiteDatabase


@pytest.fixture
def database(tmp_path):
    return SQLiteDatabase(tmp_path / "gates.sqlite3", trusted_root=tmp_path)


@pytest.fixture
def store(database):
    return GateStore(database)


def _unique_id() -> Identifier:
    return Identifier(uuid.uuid4().hex)


def _new_gate(store: GateStore, *, kind: GateKind = GateKind.script) -> GateRecord:
    return store.create(
        gate_id=_unique_id(),
        run_id=Identifier("run-" + uuid.uuid4().hex),
        kind=kind,
        request_id="req-create",
    )


def _registration(cost_state: ToolCostState) -> ToolRegistration:
    async def handler(arguments: Mapping[str, object]) -> object:
        return None

    return ToolRegistration(name="tool-" + uuid.uuid4().hex, handler=handler, cost_state=cost_state)


def _host_capabilities(cost_state: ToolCostState) -> HostToolCapabilities:
    return HostToolCapabilities(
        tool_id=Identifier("tool-" + uuid.uuid4().hex),
        name=Identifier("tool-" + uuid.uuid4().hex),
        description="测试工具。",
        input_schema={"type": "object"},
        required_permissions=[],
        risk_level=ToolRiskLevel.high,
        idempotency_supported=True,
        cancellation_supported=True,
        cost_state=cost_state,
        recovery_supported=True,
    )


# AC1：合法转移成功、非法转移抛 DomainError(VALIDATION_FAILED)。


def test_create_starts_pending_with_zero_version_and_empty_history(store):
    record = _new_gate(store)
    assert record.state is GateState.pending
    assert record.version.root == 0
    assert record.history == ()
    fetched = store.get(record.gate_id)
    assert fetched == record


def test_all_gate_kinds_are_supported(store):
    for kind in GateKind:
        record = _new_gate(store, kind=kind)
        assert record.kind is kind


def test_pending_can_request_changes_then_reapprove_after_rework(store):
    record = _new_gate(store)
    requested = store.transition(
        gate_id=record.gate_id,
        target=GateState.changes_requested,
        actor_id=Identifier("user-1"),
        reason="分镜景别需要调整。",
        expected_version=0,
        request_id="req-1",
    )
    assert requested.state is GateState.changes_requested
    assert requested.version.root == 1

    requeued = store.transition(
        gate_id=record.gate_id,
        target=GateState.pending,
        actor_id=Identifier("user-1"),
        reason="按反馈重做完成，再次请求确认。",
        expected_version=1,
        request_id="req-2",
    )
    assert requeued.state is GateState.pending
    assert requeued.version.root == 2

    approved = store.transition(
        gate_id=record.gate_id,
        target=GateState.approved,
        actor_id=Identifier("user-1"),
        reason="确认通过。",
        expected_version=2,
        request_id="req-3",
    )
    assert approved.state is GateState.approved
    assert approved.version.root == 3


def test_pending_can_be_rolled_back(store):
    record = _new_gate(store)
    rolled_back = store.transition(
        gate_id=record.gate_id,
        target=GateState.rolled_back,
        actor_id=Identifier("user-1"),
        reason="上游剧本退回，本门禁随之退回。",
        expected_version=0,
        request_id="req-rollback",
    )
    assert rolled_back.state is GateState.rolled_back


def test_terminal_states_reject_any_transition(store):
    for kind in (GateKind.script, GateKind.master_output):
        record = _new_gate(store, kind=kind)
        terminal = store.transition(
            gate_id=record.gate_id,
            target=GateState.approved,
            actor_id=Identifier("user-1"),
            reason="确认通过。",
            expected_version=0,
            request_id="req-approve",
        )
        assert terminal.state is GateState.approved
        for target in GateState:
            with pytest.raises(DomainError) as exc:
                store.transition(
                    gate_id=record.gate_id,
                    target=target,
                    actor_id=Identifier("user-1"),
                    reason="终态不允许再转移。",
                    expected_version=1,
                    request_id="req-terminal",
                )
            assert exc.value.code is ErrorCode.VALIDATION_FAILED


def test_changes_requested_cannot_jump_to_approved(store):
    record = _new_gate(store)
    store.transition(
        gate_id=record.gate_id,
        target=GateState.changes_requested,
        actor_id=Identifier("user-1"),
        reason="要求修改。",
        expected_version=0,
        request_id="req-cr",
    )
    with pytest.raises(DomainError) as exc:
        store.transition(
            gate_id=record.gate_id,
            target=GateState.approved,
            actor_id=Identifier("user-1"),
            reason="跳过重做直接批准应被拒绝。",
            expected_version=1,
            request_id="req-jump",
        )
    assert exc.value.code is ErrorCode.VALIDATION_FAILED


def test_missing_gate_transition_is_rejected(store):
    with pytest.raises(DomainError) as exc:
        store.transition(
            gate_id=_unique_id(),
            target=GateState.approved,
            actor_id=Identifier("user-1"),
            reason="门禁不存在。",
            expected_version=0,
            request_id="req-missing",
        )
    assert exc.value.code is ErrorCode.VALIDATION_FAILED


def test_duplicate_create_is_rejected(store):
    gate_id = _unique_id()
    store.create(
        gate_id=gate_id,
        run_id=Identifier("run-dup"),
        kind=GateKind.script,
        request_id="req-first",
    )
    with pytest.raises(DomainError) as exc:
        store.create(
            gate_id=gate_id,
            run_id=Identifier("run-dup"),
            kind=GateKind.script,
            request_id="req-second",
        )
    assert exc.value.code is ErrorCode.VALIDATION_FAILED


def test_transition_matrix_matches_the_ticket_state_machine():
    assert is_transition_allowed(GateState.pending, GateState.approved)
    assert is_transition_allowed(GateState.pending, GateState.changes_requested)
    assert is_transition_allowed(GateState.pending, GateState.rolled_back)
    assert is_transition_allowed(GateState.changes_requested, GateState.pending)
    assert not is_transition_allowed(GateState.changes_requested, GateState.approved)
    assert not is_transition_allowed(GateState.changes_requested, GateState.rolled_back)
    for terminal in (GateState.approved, GateState.rolled_back):
        for target in GateState:
            assert not is_transition_allowed(terminal, target)


# AC2：expected_version 乐观锁冲突被拒绝。


def test_stale_expected_version_is_rejected(store):
    record = _new_gate(store)
    store.transition(
        gate_id=record.gate_id,
        target=GateState.changes_requested,
        actor_id=Identifier("user-1"),
        reason="第一次转移。",
        expected_version=0,
        request_id="req-first",
    )
    with pytest.raises(DomainError) as exc:
        store.transition(
            gate_id=record.gate_id,
            target=GateState.pending,
            actor_id=Identifier("user-1"),
            reason="携带过期版本应被拒绝。",
            expected_version=0,
            request_id="req-stale",
        )
    assert exc.value.code is ErrorCode.REVISION_CONFLICT
    assert store.get(record.gate_id).state is GateState.changes_requested


# AC3：同一 SQLite 文件重建后状态与 history 完整恢复（模拟重启）。


def test_reopening_the_same_sqlite_file_restores_state_and_history(tmp_path):
    path = tmp_path / "gates.sqlite3"
    first_store = GateStore(SQLiteDatabase(path, trusted_root=tmp_path))
    record = _new_gate(first_store)
    first_store.transition(
        gate_id=record.gate_id,
        target=GateState.changes_requested,
        actor_id=Identifier("user-1"),
        reason="第一轮反馈。",
        expected_version=0,
        request_id="req-1",
    )
    first_store.transition(
        gate_id=record.gate_id,
        target=GateState.pending,
        actor_id=Identifier("user-1"),
        reason="按反馈重做完成，再次请求确认。",
        expected_version=1,
        request_id="req-2",
    )
    final = first_store.transition(
        gate_id=record.gate_id,
        target=GateState.approved,
        actor_id=Identifier("user-1"),
        reason="确认通过。",
        expected_version=2,
        request_id="req-3",
    )

    reopened_store = GateStore(SQLiteDatabase(path, trusted_root=tmp_path))
    restored = reopened_store.get(record.gate_id)
    assert restored == final
    assert restored.state is GateState.approved
    assert restored.version.root == 3
    assert [entry.to_state for entry in restored.history] == [
        GateState.changes_requested,
        GateState.pending,
        GateState.approved,
    ]
    assert reopened_store.list_for_run(record.run_id) == (restored,)


# AC4：每次转移追加 history 记录（操作日志）。


def test_every_transition_appends_history(store):
    record = _new_gate(store)
    first = store.transition(
        gate_id=record.gate_id,
        target=GateState.changes_requested,
        actor_id=Identifier("user-1"),
        reason="第一轮反馈。",
        expected_version=0,
        request_id="req-1",
    )
    second = store.transition(
        gate_id=record.gate_id,
        target=GateState.pending,
        actor_id=Identifier("user-2"),
        reason="重做完成。",
        expected_version=1,
        request_id="req-2",
    )

    assert len(first.history) == 1
    assert len(second.history) == 2
    first_entry, second_entry = second.history
    assert first_entry.from_state is GateState.pending
    assert first_entry.to_state is GateState.changes_requested
    assert first_entry.actor_id.root == "user-1"
    assert first_entry.reason == "第一轮反馈。"
    assert first_entry.at.root.utcoffset() == timedelta(0)
    assert second_entry.from_state is GateState.changes_requested
    assert second_entry.to_state is GateState.pending
    assert second_entry.actor_id.root == "user-2"
    persisted = store.get(record.gate_id)
    assert persisted.history == second.history


# AC5：metered/unknown 成本工具未批准即拒绝（fail closed）；free 工具不受限。


@pytest.mark.parametrize("cost_state", [ToolCostState.metered, ToolCostState.unknown])
@pytest.mark.parametrize(
    "gate_state", [None, GateState.pending, GateState.changes_requested, GateState.rolled_back]
)
def test_non_free_tools_require_an_approved_gate(cost_state, gate_state):
    with pytest.raises(DomainError) as exc:
        require_gate_for_tool(
            _registration(cost_state), gate_state, request_id="req-gate"
        )
    assert exc.value.code is ErrorCode.MANUAL_REVIEW_REQUIRED


@pytest.mark.parametrize("cost_state", [ToolCostState.metered, ToolCostState.unknown])
def test_non_free_tools_pass_through_an_approved_gate(cost_state):
    require_gate_for_tool(
        _registration(cost_state), GateState.approved, request_id="req-gate"
    )


@pytest.mark.parametrize(
    "gate_state", [None, GateState.pending, GateState.changes_requested, GateState.rolled_back]
)
def test_free_tools_are_not_restricted_by_gates(gate_state):
    require_gate_for_tool(
        _registration(ToolCostState.free), gate_state, request_id="req-gate"
    )


@pytest.mark.parametrize("cost_state", [ToolCostState.metered, ToolCostState.unknown])
def test_host_tool_capabilities_registrations_are_supported(cost_state):
    registration = _host_capabilities(cost_state)
    with pytest.raises(DomainError) as exc:
        require_gate_for_tool(registration, GateState.pending, request_id="req-gate")
    assert exc.value.code is ErrorCode.MANUAL_REVIEW_REQUIRED
    require_gate_for_tool(
        registration, GateState.approved, request_id="req-gate-approved"
    )


def test_utc_timestamps_are_stable_across_round_trip(store):
    record = _new_gate(store)
    before = datetime.now(UTC).replace(tzinfo=None)
    store.transition(
        gate_id=record.gate_id,
        target=GateState.approved,
        actor_id=Identifier("user-1"),
        reason="确认通过。",
        expected_version=0,
        request_id="req-1",
    )
    after = datetime.now(UTC).replace(tzinfo=None)
    entry = store.get(record.gate_id).history[0]
    observed = entry.at.root.astimezone(UTC).replace(tzinfo=None)
    assert before - timedelta(seconds=1) <= observed <= after + timedelta(seconds=1)
