import json
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

import pytest

from gw.agent_episode.runtime_bridge import EpisodeOrchestrator, EpisodeRuntimeBridge
from gw.agent_runtime.models import (
    AgentRun,
    ArtifactRef,
    ArtifactVersionInput,
    CallLimits,
    ConfigSnapshot,
    FinishReason,
    Identifier,
    InvocationLimits,
    MessageRole,
    Mode,
    ModelCapabilities,
    ModelInvocationRequest,
    ModelInvocationResult,
)


class FakeRunRepository:
    def __init__(self, state_val: Mapping[str, Any] | None = None, run_val: AgentRun | None = None, attempts: list | None = None):
        self.state_val = state_val
        self.run_val = run_val
        self.attempts = attempts or []

    async def read_runtime_state(self, run_id: Identifier) -> Mapping[str, Any] | None:
        return self.state_val

    async def read_run_by_id(self, run_id: Identifier) -> AgentRun | None:
        return self.run_val

    async def get_attempts(self, run_id: Identifier) -> list:
        return self.attempts

class FakeArtifactRepository:
    def __init__(self, list_run_artifacts_val: list[ArtifactRef] | None = None):
        self.list_run_artifacts_val = list_run_artifacts_val or []

    async def list_run_artifacts(self, run_id: Identifier) -> list[ArtifactRef]:
        return self.list_run_artifacts_val

class FakeGateway:
    def __init__(self, invoke_result: ModelInvocationResult | None = None, capabilities: ModelCapabilities | None = None):
        self.invoke_result = invoke_result
        self.capabilities = capabilities

    async def describe_capabilities(self, model_ref: Identifier) -> ModelCapabilities:
        if self.capabilities:
            return self.capabilities
        return ModelCapabilities(
            model_ref=model_ref,
            supported_roles=[MessageRole.system, MessageRole.user],
            structured_output=True,
            tool_calls=False,
            streaming=False,
            multimodal=False,
            lookup=False,
            cancellation=False,
            usage_reporting=False,
            max_context_tokens=None
        )

    async def invoke(self, request: ModelInvocationRequest) -> ModelInvocationResult:
        if self.invoke_result:
            return self.invoke_result
        return ModelInvocationResult(
            content="{}",
            tool_calls=[],
            finish_reason=FinishReason.stop,
            upstream_request_id=None,
            usage=None,
            usage_unavailable_reason=None
        )

@pytest.mark.anyio
async def test_describe_capabilities():
    gateway = FakeGateway()
    bridge = EpisodeRuntimeBridge(FakeArtifactRepository(), FakeRunRepository(), gateway)
    caps = await bridge.describe_capabilities(Identifier("test-model"))
    assert caps.structured_output is True

@pytest.mark.anyio
async def test_invoke():
    gateway = FakeGateway(invoke_result=ModelInvocationResult(
        content="res",
        tool_calls=[],
        finish_reason=FinishReason.stop,
        upstream_request_id=None,
        usage=None,
        usage_unavailable_reason=None
    ))
    bridge = EpisodeRuntimeBridge(FakeArtifactRepository(), FakeRunRepository(), gateway)
    req = ModelInvocationRequest(
        invocation_id=Identifier("1"),
        idempotency_key=Identifier("1"),
        model_ref=Identifier("m"),
        messages=[{"role": "user", "content": "hi"}],
        output_schema=None,
        tools=[],
        limits=InvocationLimits(max_output_tokens=100, timeout_seconds=10)
    )
    res = await bridge.invoke(req)
    assert res.content == "res"

@pytest.mark.anyio
async def test_orchestrator_valid_call():
    orc = EpisodeOrchestrator()
    config = ConfigSnapshot(
        schema_version=1,
        config_snapshot_id=Identifier("1"),
        mode=Mode.approval,
        scoring_rule_version=Identifier("1"),
        model_ref=Identifier("test-model"),
        prompt_refs=[],
        call_limits=CallLimits(max_output_tokens=100, timeout_seconds=10),
        effective_from_version=1,
        created_at=datetime.now(UTC)
    )
    limits = CallLimits(max_output_tokens=100, timeout_seconds=10)

    async def mock_dispatch(role, req, format_repair=False):
        return ModelInvocationResult(
            content=json.dumps({"action": "clarify", "capability": "writer", "target_stage": "brief", "question": "What?"}),
            tool_calls=[],
            finish_reason=FinishReason.stop,
            upstream_request_id=None,
            usage=None,
            usage_unavailable_reason=None
        )
    async def mock_guard(role):
        pass

    ctx = {
        "config_snapshot": config,
        "call_limits": limits,
        "dispatch_model": mock_dispatch,
        "assert_dispatch_allowed": mock_guard
    }

    proposal = await orc(stage="brief", input={"registered_capabilities": ["writer"]}, runtime_context=ctx)
    assert proposal.action == "clarify"
    assert proposal.capability == "writer"
    assert proposal.target_stage.value == "brief"
    assert proposal.question == "What?"

@pytest.mark.anyio
async def test_orchestrator_missing_context():
    orc = EpisodeOrchestrator()
    with pytest.raises(ValueError, match="requires a captured config and runtime-owned dispatch context"):
        await orc(stage="brief", input={}, runtime_context={})

@pytest.mark.anyio
async def test_write_missing_state():
    bridge = EpisodeRuntimeBridge(FakeArtifactRepository(), FakeRunRepository(state_val=None), FakeGateway())

    config = ConfigSnapshot(
        schema_version=1,
        config_snapshot_id=Identifier("1"),
        mode=Mode.approval,
        scoring_rule_version=Identifier("1"),
        model_ref=Identifier("test-model"),
        prompt_refs=[],
        call_limits=CallLimits(max_output_tokens=100, timeout_seconds=10),
        effective_from_version=1,
        created_at=datetime.now(UTC)
    )

    with pytest.raises(ValueError, match="Durable AgentRun state is unavailable"):
        await bridge.write(
            stage="brief",
            input={"config_snapshot": config.model_dump(mode="json"), "run_id": "r1"},
            runtime_context={}
        )

@pytest.mark.anyio
async def test_write_missing_artifact():
    bridge = EpisodeRuntimeBridge(FakeArtifactRepository(), FakeRunRepository(state_val={"current_artifacts_by_stage": {}}), FakeGateway())

    config = ConfigSnapshot(
        schema_version=1,
        config_snapshot_id=Identifier("1"),
        mode=Mode.approval,
        scoring_rule_version=Identifier("1"),
        model_ref=Identifier("test-model"),
        prompt_refs=[],
        call_limits=CallLimits(max_output_tokens=100, timeout_seconds=10),
        effective_from_version=1,
        created_at=datetime.now(UTC)
    )

    with pytest.raises(ValueError, match="Required effective .* artifact is unavailable"):
        await bridge.write(
            stage="outline_characters", # this requires brief artifact
            input={"config_snapshot": config.model_dump(mode="json"), "run_id": "r1"},
            runtime_context={}
        )

@pytest.mark.anyio
async def test_write_missing_attempt():
    state_val = {"current_artifacts_by_stage": {}}
    bridge = EpisodeRuntimeBridge(FakeArtifactRepository(), FakeRunRepository(state_val=state_val, attempts=[]), FakeGateway())

    config = ConfigSnapshot(
        schema_version=1,
        config_snapshot_id=Identifier("1"),
        mode=Mode.approval,
        scoring_rule_version=Identifier("1"),
        model_ref=Identifier("test-model"),
        prompt_refs=[],
        call_limits=CallLimits(max_output_tokens=100, timeout_seconds=10),
        effective_from_version=1,
        created_at=datetime.now(UTC)
    )

    with pytest.raises(ValueError, match="Durable stage attempt is unavailable"):
        await bridge.write(
            stage="brief",
            input={"config_snapshot": config.model_dump(mode="json"), "run_id": "r1", "stage_attempt_id": "a1"},
            runtime_context={}
        )

@pytest.mark.anyio
async def test_write_missing_run_record():
    state_val = {"current_artifacts_by_stage": {}}

    class DummyAttempt:
        stage_attempt_id = Identifier("a1")
        kind = "initial"
        attempt_number = 1

    bridge = EpisodeRuntimeBridge(FakeArtifactRepository(), FakeRunRepository(state_val=state_val, attempts=[DummyAttempt()]), FakeGateway())

    config = ConfigSnapshot(
        schema_version=1,
        config_snapshot_id=Identifier("1"),
        mode=Mode.approval,
        scoring_rule_version=Identifier("1"),
        model_ref=Identifier("test-model"),
        prompt_refs=[],
        call_limits=CallLimits(max_output_tokens=100, timeout_seconds=10),
        effective_from_version=1,
        created_at=datetime.now(UTC)
    )

    with pytest.raises(ValueError, match="Durable AgentRun record is unavailable"):
        await bridge.write(
            stage="brief",
            input={"config_snapshot": config.model_dump(mode="json"), "run_id": "r1", "stage_attempt_id": "a1"},
            runtime_context={}
        )

@pytest.mark.anyio
async def test_write_happy_path(monkeypatch):
    state_val = {"current_artifacts_by_stage": {}}

    class DummyAttempt:
        stage_attempt_id = Identifier("a1")
        kind = "initial"
        attempt_number = 1

    class DummyRun:
        project_id = Identifier("p1")

    bridge = EpisodeRuntimeBridge(FakeArtifactRepository(), FakeRunRepository(state_val=state_val, run_val=DummyRun(), attempts=[DummyAttempt()]), FakeGateway())

    async def mock_create_candidate(self, req, runtime_context):
        return ArtifactVersionInput(
            artifact_id=Identifier("a"),
            parent_version_id=None,
            metadata={},
            idempotency_key=Identifier("k"),
            source_refs=[],
            content="res"
        )
    monkeypatch.setattr("gw.agent_episode.workflow.EpisodeTextPlugin.create_candidate", mock_create_candidate)

    config = ConfigSnapshot(
        schema_version=1,
        config_snapshot_id=Identifier("1"),
        mode=Mode.approval,
        scoring_rule_version=Identifier("1"),
        model_ref=Identifier("test-model"),
        prompt_refs=[],
        call_limits=CallLimits(max_output_tokens=100, timeout_seconds=10),
        effective_from_version=1,
        created_at=datetime.now(UTC)
    )

    res = await bridge.write(
        stage="brief",
        input={"config_snapshot": config.model_dump(mode="json"), "run_id": "r1", "stage_attempt_id": "a1"},
        runtime_context={}
    )

    assert res.content == "res"

@pytest.mark.anyio
async def test_review_happy_path(monkeypatch):
    state_val = {"current_artifacts_by_stage": {}}

    class DummyAttempt:
        stage_attempt_id = Identifier("a1")
        kind = "initial"
        attempt_number = 1

    class DummyRun:
        project_id = Identifier("p1")

    bridge = EpisodeRuntimeBridge(FakeArtifactRepository(), FakeRunRepository(state_val=state_val, run_val=DummyRun(), attempts=[DummyAttempt()]), FakeGateway())

    async def mock_review_candidate(self, req, artifact, content, runtime_context):
        from gw.agent_runtime.policy import ReviewDraft
        return ReviewDraft(
            dimension_scores={},
            program_checks=[],
            rule_version=Identifier("v"),
            dimension_score_decimals={},
            findings=[],
            revision_responses=[],
            rollback_proposal=None
        )
    monkeypatch.setattr("gw.agent_episode.workflow.EpisodeTextPlugin.review_candidate", mock_review_candidate)

    config = ConfigSnapshot(
        schema_version=1,
        config_snapshot_id=Identifier("1"),
        mode=Mode.approval,
        scoring_rule_version=Identifier("1"),
        model_ref=Identifier("test-model"),
        prompt_refs=[],
        call_limits=CallLimits(max_output_tokens=100, timeout_seconds=10),
        effective_from_version=1,
        created_at=datetime.now(UTC)
    )

    from gw.agent_runtime.models import StageId
    artifact = ArtifactRef(
        schema_version=1,
        artifact_id=Identifier("a"),
        version_id=Identifier("v"),
        revision=1,
        lifecycle="valid",
        content_hash="e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
        content_ref=Identifier("c"),
        parent_version_ids=[],
        source_refs=[],
        created_at=datetime.now(UTC),
    )
    # inject stage artificially to bypass pydantic validation extra_forbidden
    artifact.__dict__["stage"] = StageId.brief

    res = await bridge.review(
        stage="brief",
        artifact=artifact,
        content="res",
        input={"config_snapshot": config.model_dump(mode="json"), "run_id": "r1", "stage_attempt_id": "a1"},
        runtime_context={}
    )

    assert res.rule_version == Identifier("v")
