"""Host composition bridge between the text episode plugin and AgentRuntime."""
from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from gw.agent_episode.exports import EpisodeArtifactExporter
from gw.agent_episode.models import (
    EpisodeInputArtifact,
    EpisodeStageRequest,
    HumanRevisionFeedback,
    ReviewFinding,
    RevisionResponse,
    STAGE_INPUTS,
)
from gw.agent_episode.prompts import ORCHESTRATOR_TEMPLATE, PROMPT_CATALOG
from gw.agent_episode.workflow import EpisodeTextPlugin
from gw.agent_runtime.models import (
    ArtifactRef,
    ArtifactVersionInput,
    ConfigSnapshot,
    FinishReason,
    Identifier,
    InvocationLimits,
    MessageRole,
    ModelCapabilities,
    ModelInvocationRequest,
    ModelInvocationResult,
    ModelMessage,
    PromptTemplate,
    PromptTemplateRef,
    StageId,
)
from gw.agent_runtime.policy import OrchestrationProposal, ReviewDraft
from gw.agent_runtime.ports import ModelGateway, PromptRepository
from gw.agent_runtime.repository import RuntimeArtifactRepository, RuntimeRunRepository


class EpisodePromptRepository(PromptRepository):
    """Resolve only the repository-owned, versioned text episode prompt pack."""

    async def resolve_template_ref(
        self, project_id: Identifier, stage: StageId, purpose: Identifier,
    ) -> PromptTemplateRef | None:
        role = purpose.root.rsplit(".", 1)[-1]
        spec = PROMPT_CATALOG.get((stage, role))
        return spec.ref if spec else None

    async def get_template(self, template_id: Identifier, version: Identifier) -> PromptTemplate | None:
        if template_id == ORCHESTRATOR_TEMPLATE.ref.template_id and version == ORCHESTRATOR_TEMPLATE.ref.version:
            return ORCHESTRATOR_TEMPLATE
        for spec in PROMPT_CATALOG.values():
            if spec.ref.template_id == template_id and spec.ref.version == version:
                return spec
        return None


class EpisodeRuntimeBridge:
    """Adapt the episode plugin to durable runtime repositories and a typed gateway."""

    def __init__(
        self,
        artifacts: RuntimeArtifactRepository,
        run_repository: RuntimeRunRepository,
        gateway: ModelGateway,
        prompts: PromptRepository | None = None,
    ) -> None:
        self.artifacts = artifacts
        self.run_repository = run_repository
        self.gateway = gateway
        self.prompts = prompts or EpisodePromptRepository()
        self.orchestrator = EpisodeOrchestrator()

    async def describe_capabilities(self, model_ref: Identifier) -> ModelCapabilities:
        return await self.gateway.describe_capabilities(model_ref)

    async def invoke(self, request: ModelInvocationRequest) -> ModelInvocationResult:
        return await self.gateway.invoke(request)

    async def write(
        self, *, stage: str, input: Mapping[str, Any], runtime_context: Mapping[str, Any],
    ) -> ArtifactVersionInput:
        request = await self._stage_request(stage, input, runtime_context=runtime_context)
        profile = ConfigSnapshot.model_validate(input["config_snapshot"])
        return await self._plugin(profile).create_candidate(request, runtime_context=runtime_context)

    async def review(
        self, *, stage: str, artifact: ArtifactRef, content: str,
        input: Mapping[str, Any], runtime_context: Mapping[str, Any],
    ) -> ReviewDraft:
        request = await self._stage_request(stage, input, runtime_context=runtime_context)
        profile = ConfigSnapshot.model_validate(input["config_snapshot"])
        return await self._plugin(profile).review_candidate(
            request, artifact, content, runtime_context=runtime_context,
        )

    def _plugin(self, profile: ConfigSnapshot) -> EpisodeTextPlugin:
        return EpisodeTextPlugin(
            model_gateway=self,
            prompt_repository=self.prompts,
            artifact_repository=self.artifacts,
            config_snapshot=profile,
        )

    async def _stage_request(
        self, stage: str, input: Mapping[str, Any], *, runtime_context: Mapping[str, Any],
    ) -> EpisodeStageRequest:
        run_id = Identifier(input["run_id"])
        stage_id = StageId(stage)
        state = await self.run_repository.read_runtime_state(run_id)
        if state is None:
            raise ValueError("Durable AgentRun state is unavailable.")
        stored = await self.artifacts.list_run_artifacts(run_id)
        by_version = {ref.version_id.root: ref for ref in stored}
        current = state.get("current_artifacts_by_stage", {})
        selected: dict[StageId, ArtifactRef] = {}
        for required in STAGE_INPUTS[stage_id]:
            version_id = current.get(required.value)
            ref = by_version.get(version_id) if isinstance(version_id, str) else None
            if ref is None:
                raise ValueError(f"Required effective {required.value} artifact is unavailable.")
            selected[required] = ref

        attempt_id = Identifier(input["stage_attempt_id"])
        attempts = await self.run_repository.get_attempts(run_id)
        attempt = next((item for item in attempts if item.stage_attempt_id == attempt_id), None)
        if attempt is None:
            raise ValueError("Durable stage attempt is unavailable.")
        previous_review = input.get("previous_review")
        parent_version = None
        if isinstance(previous_review, Mapping):
            artifact = previous_review.get("artifact")
            if isinstance(artifact, Mapping) and isinstance(artifact.get("version_id"), str):
                parent_version = artifact["version_id"]

        run = await self.run_repository.read_run_by_id(run_id)
        if run is None:
            raise ValueError("Durable AgentRun record is unavailable.")
        previous_findings: list[ReviewFinding] = []
        previous_responses: list[RevisionResponse] = []
        if isinstance(previous_review, Mapping):
            previous_findings = [ReviewFinding.model_validate(item) for item in previous_review.get("findings", [])]
            previous_responses = [RevisionResponse.model_validate(item) for item in previous_review.get("revision_responses", [])]
        answers = runtime_context.get("clarification_answers", input.get("clarification_answers", {}))
        raw_feedback = input.get("human_revision_feedback")
        human_feedback = HumanRevisionFeedback.model_validate(raw_feedback) if isinstance(raw_feedback, Mapping) else None
        return EpisodeStageRequest(
            run_id=run_id.root,
            project_id=run.project_id.root,
            request_id=f"agent:{attempt_id.root}",
            stage_attempt_id=attempt_id.root,
            stage=stage_id,
            attempt_kind=attempt.kind,
            attempt_number=attempt.attempt_number,
            artifact_id=f"agent:{run_id.root}:{stage_id.value}",
            parent_version_id=parent_version,
            user_goal=state.get("user_goal") if stage_id is StageId.brief else None,
            inputs=[EpisodeInputArtifact(stage=source_stage, artifact=ref) for source_stage, ref in selected.items()],
            revision_findings=previous_findings,
            revision_responses=previous_responses,
            human_revision_feedback=human_feedback,
            clarification_answers=answers,
        )

    async def orchestrate(
        self, *, stage: str, input: Mapping[str, Any], runtime_context: Mapping[str, Any],
    ) -> OrchestrationProposal:
        return await self.orchestrator(stage=stage, input=input, runtime_context=runtime_context)


class EpisodeOrchestrator:
    """Structured planner; proposed actions never mutate runtime state directly."""

    async def __call__(
        self, *, stage: str, input: Mapping[str, Any], runtime_context: Mapping[str, Any],
    ) -> OrchestrationProposal:
        config = runtime_context.get("config_snapshot")
        limits = runtime_context.get("call_limits")
        dispatch = runtime_context.get("dispatch_model")
        guard = runtime_context.get("assert_dispatch_allowed")
        if (not isinstance(config, ConfigSnapshot) or config.model_ref is None
                or limits is None or not callable(dispatch) or not callable(guard)):
            raise ValueError("The planner requires a captured config and runtime-owned dispatch context.")
        await guard("orchestrator")
        available = input.get("registered_capabilities", ["writer", "reviewer"])
        available_roles = {
            item.get("role") if isinstance(item, Mapping) else item
            for item in available
            if isinstance(item, str) or isinstance(item, Mapping) and isinstance(item.get("role"), str)
        }
        context = {
            "stage": stage,
            "input": dict(input),
            "registered_capabilities": list(available),
            "clarification_answers": dict(runtime_context.get("clarification_answers", {})),
        }
        schema = {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["clarify", "select_capability", "create", "review", "revise", "rollback"]},
                "capability": {"type": ["string", "null"]},
                "target_stage": {"type": ["string", "null"], "enum": [item.value for item in StageId] + [None]},
                "question": {"type": ["string", "null"]},
            },
            "required": ["action", "capability", "target_stage", "question"],
            "additionalProperties": False,
        }
        result = await dispatch(
            "orchestrator",
            ModelInvocationRequest(
                invocation_id="episode-planner-pending",
                idempotency_key="episode-planner-pending",
                model_ref=config.model_ref,
                messages=[
                    ModelMessage(role=MessageRole.system, content=ORCHESTRATOR_TEMPLATE.body),
                    ModelMessage(role=MessageRole.user, content=json.dumps(context, ensure_ascii=False, sort_keys=True)),
                ],
                output_schema=schema,
                tools=[],
                limits=InvocationLimits(max_output_tokens=limits.max_output_tokens, timeout_seconds=limits.timeout_seconds),
            ),
        )
        if result.content is None or result.finish_reason is not FinishReason.stop or result.tool_calls:
            raise ValueError("The planner did not return a complete structured proposal.")
        try:
            payload = json.loads(result.content)
            if not isinstance(payload, dict) or set(payload) != {"action", "capability", "target_stage", "question"}:
                raise ValueError("Planner proposal has unexpected fields.")
            target = StageId(payload["target_stage"]) if payload["target_stage"] is not None else None
            proposal = OrchestrationProposal(
                action=payload["action"], capability=payload["capability"],
                target_stage=target, question=payload["question"],
            )
            if proposal.action == "clarify" and (not isinstance(proposal.question, str) or not proposal.question.strip()):
                raise ValueError("Clarification proposals require a non-empty question.")
            if proposal.action in {"select_capability", "create", "revise", "review"} and proposal.capability is not None and proposal.capability not in available_roles:
                raise ValueError("Planner proposed an unregistered capability.")
            return proposal
        except (ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
            raise ValueError("The planner output did not match the episode action contract.") from exc


__all__ = ["EpisodeArtifactExporter", "EpisodeOrchestrator", "EpisodePromptRepository", "EpisodeRuntimeBridge"]
