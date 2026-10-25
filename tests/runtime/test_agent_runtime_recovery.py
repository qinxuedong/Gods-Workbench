from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any

import pytest

from gw.agent_runtime.models import (
    AgentRun,
    ApprovalRecord,
    DispatchState,
    Identifier,
    InvocationRecord,
    LeaseGrant,
    PausedReason,
    ReviewRecord,
    RunEvent,
    RunStatus,
    StageAttempt,
    UtcDateTime,
    Version,
)
from gw.agent_runtime.recovery import RecoveryCoordinator


class FakeRepository:
    def __init__(self):
        self.runs = []
        self.invocations = []
        self.lease = None
        self.read_by_id = None
        self.commit_called = False
        self.released_leases = []

    async def scan_recoverable(
        self, cursor: Identifier | None, limit: int
    ) -> tuple[Sequence[AgentRun], Identifier | None]:
        return self.runs, None

    async def incomplete_invocations(self, run_id: Identifier) -> list[InvocationRecord]:
        return [i for i in self.invocations if i.run_id == run_id]

    async def claim_lease(self, run_id: Identifier, owner_id: Identifier, lease_seconds: int) -> LeaseGrant | None:
        return self.lease

    async def release_lease(self, lease: LeaseGrant) -> None:
        self.released_leases.append(lease)

    async def read_run_by_id(self, run_id: Identifier) -> AgentRun | None:
        return self.read_by_id

    async def commit_transition(
        self,
        run_id: Identifier,
        expected_revision: int,
        next_run: AgentRun,
        events: Sequence[RunEvent],
        counter_changes: Mapping[str, int],
        *,
        lease: LeaseGrant,
        idempotency_key: Identifier | None = None,
        request_fingerprint: str | None = None,
        runtime_state: Mapping[str, Any] | None = None,
        attempts: Sequence[StageAttempt] = (),
        reviews: Sequence[ReviewRecord] = (),
        approvals: Sequence[ApprovalRecord] = (),
        stale_version_ids: Sequence[Identifier] = (),
        idempotency_response: AgentRun | ApprovalRecord | None = None,
    ) -> AgentRun | ApprovalRecord:
        self.commit_called = True
        return next_run

def make_dummy_run(status: RunStatus, paused_reason: str | None = None) -> AgentRun:
    return AgentRun(
        schema_version=1,
        run_id=Identifier("run1"),
        project_id=Identifier("proj1"),
        status=status,
        current_stage=None,
        version=Version(1),
        config_snapshot_id=Identifier("cs1"),
        paused_reason=PausedReason(paused_reason) if paused_reason else None,
        created_at=UtcDateTime(datetime.now(UTC)),
        updated_at=UtcDateTime(datetime.now(UTC)),
    )

@pytest.mark.anyio
async def test_reconcile_waiting_review():
    repo = FakeRepository()
    coord = RecoveryCoordinator(repo, Identifier("owner1"))

    run = make_dummy_run(RunStatus.waiting_review)
    repo.runs.append(run)

    items = await coord.reconcile()
    assert len(items) == 1
    assert items[0].action == "preserve_manual_wait"
    assert items[0].status == RunStatus.waiting_review

@pytest.mark.anyio
async def test_reconcile_paused():
    repo = FakeRepository()
    coord = RecoveryCoordinator(repo, Identifier("owner1"))

    run = make_dummy_run(RunStatus.paused, paused_reason="Need tokens")
    repo.runs.append(run)

    items = await coord.reconcile()
    assert len(items) == 1
    assert items[0].action == "preserve_pause"
    assert items[0].status == RunStatus.paused
    assert items[0].reason == "Need tokens"

@pytest.mark.anyio
async def test_reconcile_unresolved_invocations_pauses():
    repo = FakeRepository()
    coord = RecoveryCoordinator(repo, Identifier("owner1"))

    run = make_dummy_run(RunStatus.running)
    repo.runs.append(run)

    inv = InvocationRecord(
        schema_version=1,
        invocation_id=Identifier("inv1"),
        run_id=Identifier("run1"),
        idempotency_key=Identifier("ik1"),
        request_fingerprint="0"*64,
        dispatch_state=DispatchState.sent,
        upstream_request_id=None,
        host_job_id=None,
        result_ref=None,
        usage=None,
        usage_unavailable_reason=None,
        created_at=UtcDateTime(datetime.now(UTC)),
        updated_at=UtcDateTime(datetime.now(UTC)),
    )
    repo.invocations.append(inv)

    # Needs a lease and a latest run to succeed in _pause_unknown
    lease = LeaseGrant(
        run_id=Identifier("run1"), generation=1,
        owner_id=Identifier("owner1"),
        expires_at=UtcDateTime(datetime.now(UTC))
    )
    repo.lease = lease
    repo.read_by_id = run

    items = await coord.reconcile()
    assert len(items) == 1
    assert items[0].action == "pause_unknown_outcome"
    assert items[0].status == RunStatus.paused
    assert repo.commit_called is True
    assert len(repo.released_leases) == 1

@pytest.mark.anyio
async def test_reconcile_unresolved_invocations_lease_fails():
    repo = FakeRepository()
    coord = RecoveryCoordinator(repo, Identifier("owner1"))

    run = make_dummy_run(RunStatus.running)
    repo.runs.append(run)

    inv = InvocationRecord(
        schema_version=1,
        invocation_id=Identifier("inv1"),
        run_id=Identifier("run1"),
        idempotency_key=Identifier("ik1"),
        request_fingerprint="0"*64,
        dispatch_state=DispatchState.sent,
        upstream_request_id=None,
        host_job_id=None,
        result_ref=None,
        usage=None,
        usage_unavailable_reason=None,
        created_at=UtcDateTime(datetime.now(UTC)),
        updated_at=UtcDateTime(datetime.now(UTC)),
    )
    repo.invocations.append(inv)

    # repo.lease is None
    repo.read_by_id = run

    items = await coord.reconcile()
    assert len(items) == 1
    assert items[0].action == "pause_unknown_outcome"
    # Should fallback to current/original state
    assert items[0].status == RunStatus.running
    assert repo.commit_called is False

@pytest.mark.anyio
async def test_reconcile_unresolved_invocations_stale_run():
    repo = FakeRepository()
    coord = RecoveryCoordinator(repo, Identifier("owner1"))

    run = make_dummy_run(RunStatus.running)
    repo.runs.append(run)

    inv = InvocationRecord(
        schema_version=1,
        invocation_id=Identifier("inv1"),
        run_id=Identifier("run1"),
        idempotency_key=Identifier("ik1"),
        request_fingerprint="0"*64,
        dispatch_state=DispatchState.sent,
        upstream_request_id=None,
        host_job_id=None,
        result_ref=None,
        usage=None,
        usage_unavailable_reason=None,
        created_at=UtcDateTime(datetime.now(UTC)),
        updated_at=UtcDateTime(datetime.now(UTC)),
    )
    repo.invocations.append(inv)

    lease = LeaseGrant(
        run_id=Identifier("run1"), generation=1,
        owner_id=Identifier("owner1"),
        expires_at=UtcDateTime(datetime.now(UTC))
    )
    repo.lease = lease
    # Stale run (different version)
    stale_run = make_dummy_run(RunStatus.running)
    stale_run = stale_run.model_copy(update={"version": Version(2)})
    repo.read_by_id = stale_run

    items = await coord.reconcile()
    assert len(items) == 1
    assert items[0].action == "pause_unknown_outcome"
    assert items[0].status == RunStatus.running # from latest/stale run
    assert repo.commit_called is False
    assert len(repo.released_leases) == 1

@pytest.mark.anyio
async def test_reconcile_resumes_running_or_queued():
    repo = FakeRepository()
    coord = RecoveryCoordinator(repo, Identifier("owner1"))

    run1 = make_dummy_run(RunStatus.queued)
    run2 = make_dummy_run(RunStatus.running)
    run2 = run2.model_copy(update={"run_id": Identifier("run2")})
    repo.runs.extend([run1, run2])

    items = await coord.reconcile()
    assert len(items) == 2
    assert items[0].action == "resume_from_business_state"
    assert items[1].action == "resume_from_business_state"
