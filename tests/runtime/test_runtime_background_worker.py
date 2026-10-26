import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from gw.agent_runtime.errors import DomainError
from gw.agent_runtime.models import (
    AgentRun,
    ErrorCode,
    Identifier,
    RunStatus,
    Version,
)
from gw.agent_runtime.worker import RuntimeBackgroundWorker

class FakeLock:
    async def __aenter__(self) -> None:
        pass

    async def __aexit__(self, exc_type: object, exc_val: object, exc_tb: object) -> None:
        pass

class FakeIntegration:
    def __init__(self) -> None:
        self.run_lock = FakeLock()
        self.repository = MagicMock()
        self.service = MagicMock()
        self.get_binding = MagicMock()
        self.authorize_background = AsyncMock()

def create_agent_run(status: RunStatus = RunStatus.queued) -> AgentRun:
    now = datetime.now(timezone.utc)
    return AgentRun(
        schema_version=1,
        run_id=Identifier("r-1"),
        project_id=Identifier("p-1"),
        status=status,
        current_stage=None,
        version=Version(1),
        config_snapshot_id=Identifier("c-1"),
        paused_reason=None,
        created_at=now,
        updated_at=now,
    )

@pytest.mark.asyncio
async def test_start_stop() -> None:
    integration = FakeIntegration()
    worker = RuntimeBackgroundWorker(integration, poll_seconds=0.01)

    # Provide a mock run_once that uses an Event to signal it ran
    run_once_called = asyncio.Event()

    async def mock_run_once() -> None:
        run_once_called.set()
        # Keep it yielding to simulate running state
        await asyncio.sleep(0.001)

    worker.run_once = AsyncMock(side_effect=mock_run_once)

    await worker.start()

    # Wait until run_once is definitely hit
    await asyncio.wait_for(run_once_called.wait(), timeout=1.0)

    assert worker._task is not None and not worker._task.done()
    await worker.stop()
    assert worker._task is None

@pytest.mark.asyncio
async def test_run_once_success() -> None:
    integration = FakeIntegration()
    worker = RuntimeBackgroundWorker(integration)

    queued_run = create_agent_run(RunStatus.queued)

    # scan_recoverable returns one run, then no runs to stop the loop
    integration.repository.scan_recoverable = AsyncMock(
        side_effect=[([queued_run], Identifier("cursor1")), ([], None)]
    )

    worker._advance = AsyncMock()

    await worker.run_once()

    assert integration.repository.scan_recoverable.call_count == 2
    worker._advance.assert_awaited_once_with(queued_run)

@pytest.mark.asyncio
async def test_run_once_ignores_other_statuses() -> None:
    integration = FakeIntegration()
    worker = RuntimeBackgroundWorker(integration)

    succeeded_run = create_agent_run(RunStatus.succeeded)

    integration.repository.scan_recoverable = AsyncMock(return_value=([succeeded_run], None))
    worker._advance = AsyncMock()

    await worker.run_once()
    worker._advance.assert_not_awaited()

@pytest.mark.asyncio
async def test_run_once_catches_advance_exception() -> None:
    integration = FakeIntegration()
    worker = RuntimeBackgroundWorker(integration)

    queued_run = create_agent_run(RunStatus.queued)

    integration.repository.scan_recoverable = AsyncMock(return_value=([queued_run], None))
    worker._advance = AsyncMock(side_effect=Exception("advance error"))

    # Should not raise exception
    await worker.run_once()
    worker._advance.assert_awaited_once_with(queued_run)

@pytest.mark.asyncio
async def test_advance_no_binding() -> None:
    integration = FakeIntegration()
    worker = RuntimeBackgroundWorker(integration)

    queued_run = create_agent_run(RunStatus.queued)

    integration.get_binding.return_value = None
    worker._pause_for_authorization = AsyncMock()

    await worker._advance(queued_run)

    integration.get_binding.assert_called_once_with("r-1")
    worker._pause_for_authorization.assert_awaited_once_with(
        queued_run, "Run identity binding is unavailable."
    )
    integration.service.start_run.assert_not_called()

@pytest.mark.asyncio
async def test_advance_unauthorized() -> None:
    integration = FakeIntegration()
    worker = RuntimeBackgroundWorker(integration)

    queued_run = create_agent_run(RunStatus.queued)

    integration.get_binding.return_value = {"actor_id": "a-1"}
    integration.authorize_background.return_value = False
    worker._pause_for_authorization = AsyncMock()

    await worker._advance(queued_run)

    integration.authorize_background.assert_awaited_once_with(
        Identifier("p-1"), Identifier("a-1"), Identifier("r-1")
    )
    worker._pause_for_authorization.assert_awaited_once_with(
        queued_run, "The authenticated session or project permission is no longer valid."
    )

@pytest.mark.asyncio
async def test_advance_queued_to_running() -> None:
    integration = FakeIntegration()
    worker = RuntimeBackgroundWorker(integration)

    queued_run = create_agent_run(RunStatus.queued)
    running_run = create_agent_run(RunStatus.running)

    integration.get_binding.return_value = {"actor_id": "a-1"}
    integration.authorize_background.return_value = True
    integration.service.start_run = AsyncMock(return_value=running_run)
    integration.service.execute_current_stage = AsyncMock()

    await worker._advance(queued_run)

    integration.service.start_run.assert_awaited_once_with(
        Identifier("p-1"), Identifier("r-1"), expected_version=1, idempotency_key=Identifier("host-start:r-1")
    )
    integration.service.execute_current_stage.assert_awaited_once_with(
        Identifier("p-1"), Identifier("r-1"), expected_version=1, idempotency_key=Identifier("host-execute:r-1:1"), actor_id=Identifier("a-1"), writer_role="writer", reviewer_role="reviewer"
    )

@pytest.mark.asyncio
async def test_advance_queued_not_running() -> None:
    integration = FakeIntegration()
    worker = RuntimeBackgroundWorker(integration)

    queued_run = create_agent_run(RunStatus.queued)
    failed_run = create_agent_run(RunStatus.failed)

    integration.get_binding.return_value = {"actor_id": "a-1"}
    integration.authorize_background.return_value = True
    integration.service.start_run = AsyncMock(return_value=failed_run)
    integration.service.execute_current_stage = AsyncMock()

    await worker._advance(queued_run)

    integration.service.start_run.assert_awaited_once()
    integration.service.execute_current_stage.assert_not_awaited()

@pytest.mark.asyncio
async def test_advance_execute_stage_not_authorized() -> None:
    integration = FakeIntegration()
    worker = RuntimeBackgroundWorker(integration)

    running_run = create_agent_run(RunStatus.running)

    integration.get_binding.return_value = {"actor_id": "a-1"}
    integration.authorize_background.return_value = True
    integration.service.execute_current_stage = AsyncMock(side_effect=DomainError(
        ErrorCode.NOT_AUTHORIZED, "not auth", request_id="req-1"
    ))
    integration.repository.read_run_by_id = AsyncMock(return_value=running_run)
    worker._pause_for_authorization = AsyncMock()

    await worker._advance(running_run)

    worker._pause_for_authorization.assert_awaited_once_with(
        running_run, "The authenticated session or project permission was revoked during execution."
    )

@pytest.mark.asyncio
async def test_advance_execute_stage_other_error() -> None:
    integration = FakeIntegration()
    worker = RuntimeBackgroundWorker(integration)

    running_run = create_agent_run(RunStatus.running)

    integration.get_binding.return_value = {"actor_id": "a-1"}
    integration.authorize_background.return_value = True
    integration.service.execute_current_stage = AsyncMock(side_effect=DomainError(
        ErrorCode.VALIDATION_FAILED, "failed", request_id="req-1"
    ))

    with pytest.raises(DomainError) as exc_info:
        await worker._advance(running_run)
    assert exc_info.value.code == ErrorCode.VALIDATION_FAILED

@pytest.mark.asyncio
async def test_pause_for_authorization_success() -> None:
    integration = FakeIntegration()
    worker = RuntimeBackgroundWorker(integration)

    run = create_agent_run(RunStatus.queued)
    integration.service.pause_run = AsyncMock()

    await worker._pause_for_authorization(run, "reason")

    integration.service.pause_run.assert_awaited_once_with(
        Identifier("p-1"), Identifier("r-1"), expected_version=1, idempotency_key=Identifier("host-auth-pause:r-1:1"), reason="reason"
    )

@pytest.mark.asyncio
async def test_pause_for_authorization_error() -> None:
    integration = FakeIntegration()
    worker = RuntimeBackgroundWorker(integration)

    run = create_agent_run(RunStatus.queued)
    integration.service.pause_run = AsyncMock(side_effect=TypeError("error"))

    # Should gracefully return None without propagating TypeError
    result = await worker._pause_for_authorization(run, "reason")
    assert result is None
