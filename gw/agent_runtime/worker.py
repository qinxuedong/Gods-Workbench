"""Single-process durable AgentRun worker with run-bound authorization."""
from __future__ import annotations

import asyncio
import contextlib
from typing import Any, Callable

from gw.agent_runtime.errors import DomainError
from gw.agent_runtime.models import AgentRun, ErrorCode, Identifier, RunStatus


class RuntimeBackgroundWorker:
    """Advance queued/running runs only while their persisted identity is live."""

    def __init__(self, integration: Any, *, poll_seconds: float = 0.5) -> None:
        self.integration = integration
        self.poll_seconds = poll_seconds
        self._task: asyncio.Task[None] | None = None
        self._stopping = asyncio.Event()

    async def start(self) -> None:
        if self._task is None or self._task.done():
            self._stopping.clear()
            self._task = asyncio.create_task(self._run(), name="gw-agent-runtime-worker")

    async def stop(self) -> None:
        self._stopping.set()
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None

    async def run_once(self) -> None:
        cursor = None
        while True:
            # Do not observe a queued row between service persistence and its
            # trusted host binding. Creation holds this same lock until binding
            # and idempotency linkage are committed.
            async with self.integration.run_lock:
                runs, cursor = await self.integration.repository.scan_recoverable(cursor, 100)
            for run in runs:
                if run.status not in {RunStatus.queued, RunStatus.running}:
                    continue
                try:
                    await self._advance(run)
                except Exception:
                    # A later bounded pass may recover the durable state. Errors
                    # are intentionally not logged with prompts or provider data.
                    continue
            if cursor is None:
                break

    async def _advance(self, run: AgentRun) -> None:
        async with self.integration.run_lock:
            binding = self.integration.get_binding(run.run_id.root)
            if binding is None:
                await self._pause_for_authorization(run, "Run identity binding is unavailable.")
                return
            if not await self.integration.authorize_background(
                run.project_id, Identifier(binding["actor_id"]), run.run_id,
            ):
                await self._pause_for_authorization(run, "The authenticated session or project permission is no longer valid.")
                return

            current = run
            if current.status is RunStatus.queued:
                current = await self.integration.service.start_run(
                    current.project_id,
                    current.run_id,
                    expected_version=current.version.root,
                    idempotency_key=Identifier(f"host-start:{current.run_id.root}"),
                )
        if current.status is not RunStatus.running:
            return
        # Runtime service rechecks the run-bound session immediately before each
        # model dispatch and retry, after waiting for any external boundary.
        try:
            await self.integration.service.execute_current_stage(
                current.project_id,
                current.run_id,
                expected_version=current.version.root,
                idempotency_key=Identifier(f"host-execute:{current.run_id.root}:{current.version.root}"),
                actor_id=Identifier(binding["actor_id"]),
                writer_role="writer",
                reviewer_role="reviewer",
            )
        except DomainError as exc:
            if exc.code is ErrorCode.NOT_AUTHORIZED:
                latest = await self.integration.repository.read_run_by_id(current.run_id)
                if latest is not None:
                    await self._pause_for_authorization(latest, "The authenticated session or project permission was revoked during execution.")
                return
            raise

    async def _pause_for_authorization(self, run: AgentRun, reason: str) -> None:
        try:
            await self.integration.service.pause_run(
                run.project_id,
                run.run_id,
                expected_version=run.version.root,
                idempotency_key=Identifier(f"host-auth-pause:{run.run_id.root}:{run.version.root}"),
                reason=reason,
            )
        except (DomainError, TypeError):
            return

    async def _run(self) -> None:
        while not self._stopping.is_set():
            await self.run_once()
            try:
                await asyncio.wait_for(self._stopping.wait(), timeout=self.poll_seconds)
            except asyncio.TimeoutError:
                pass


__all__ = ["RuntimeBackgroundWorker"]
