"""Crash recovery driven by business state and the durable invocation ledger."""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from gw.agent_runtime.events import make_event
from gw.agent_runtime.models import AgentRun, Identifier, InvocationRecord, LeaseGrant, RunStatus, PausedReason, UtcDateTime
from gw.agent_runtime.repository import RuntimeRunRepository, utc_now


@dataclass(frozen=True, slots=True)
class RecoveryItem:
    run_id: Identifier
    status: RunStatus
    action: str
    unresolved_invocation_ids: tuple[Identifier, ...] = ()
    reason: str | None = None


class RecoveryCoordinator:
    """Reconcile durable ledgers without treating LangGraph checkpoints as truth."""

    def __init__(self, repository: RuntimeRunRepository, owner_id: Identifier, *, lease_seconds: int = 60) -> None:
        self.repository = repository
        self.owner_id = owner_id
        self.lease_seconds = lease_seconds

    async def reconcile(self, *, limit: int = 100) -> list[RecoveryItem]:
        runs, _cursor = await self.repository.scan_recoverable(None, limit)
        results: list[RecoveryItem] = []
        for run in runs:
            unresolved = await self.repository.incomplete_invocations(run.run_id)
            unresolved_ids = tuple(item.invocation_id for item in unresolved)
            if run.status is RunStatus.waiting_review:
                results.append(RecoveryItem(run.run_id, run.status, "preserve_manual_wait", unresolved_ids))
                continue
            if run.status is RunStatus.paused:
                results.append(RecoveryItem(run.run_id, run.status, "preserve_pause", unresolved_ids, run.paused_reason.root if run.paused_reason else None))
                continue
            if unresolved_ids:
                paused = await self._pause_unknown(run, unresolved)
                results.append(RecoveryItem(run.run_id, paused.status, "pause_unknown_outcome", unresolved_ids, "External result is unresolved; automatic resend is forbidden."))
                continue
            if run.status in {RunStatus.queued, RunStatus.running}:
                results.append(RecoveryItem(run.run_id, run.status, "resume_from_business_state"))
        return results

    async def _pause_unknown(self, run: AgentRun, unresolved: list[InvocationRecord]) -> AgentRun:
        lease = await self.repository.claim_lease(run.run_id, self.owner_id, self.lease_seconds)
        if lease is None:
            current = await self.repository.read_run_by_id(run.run_id)
            return current or run
        latest = await self.repository.read_run_by_id(run.run_id)
        if latest is None or latest.version.root != run.version.root:
            await self.repository.release_lease(lease)
            return latest or run
        now = utc_now()
        paused = latest.model_copy(update={
            "status": RunStatus.paused,
            "paused_reason": PausedReason("Invocation outcome unknown; reconcile externally before retrying."),
            "version": latest.version.__class__(latest.version.root + 1),
            "updated_at": UtcDateTime(now),
        })
        event = make_event(
            latest.run_id,
            paused.version.root,
            "run.paused.unknown_outcome",
            "An already-dispatched call has no durable result; no automatic retry was sent.",
            stage=latest.current_stage,
        )
        fingerprint = "unknown-outcome:" + ",".join(sorted(item.invocation_id.root for item in unresolved))
        try:
            return await self.repository.commit_transition(
                latest.run_id,
                latest.version.root,
                paused,
                [event],
                {},
                lease=lease,
                idempotency_key=Identifier(f"recovery:{fingerprint}"),
                request_fingerprint=fingerprint,
            )
        finally:
            await self.repository.release_lease(lease)
