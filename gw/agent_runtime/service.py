"""Framework-neutral run orchestration, approvals, quota transitions, and recovery-safe execution."""
from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

from gw.agent_runtime.errors import DomainError
from gw.agent_runtime.events import make_event
from gw.agent_runtime.graph import LangGraphRuntimeExecutor
from gw.agent_runtime.models import (
    AgentRun,
    ApprovalDecision,
    ApprovalRecord,
    ArtifactLifecycle,
    ArtifactRef,
    ArtifactVersionInput,
    CallLimits,
    BudgetDecision,
    ConfigSnapshot,
    ClarificationRead,
    ClarificationQuestion,
    CreateRunRequest,
    ErrorCode,
    Identifier,
    InvocationRecord,
    LeaseGrant,
    InvocationLimits,
    ModelInvocationRequest,
    ModelInvocationResult,
    TextExportFormat,
    ReviewConclusion,
    ReviewDecisionRequest,
    ReviewRecord,
    RunEvent,
    RunStatus,
    PausedReason,
    UtcDateTime,
    StageAttempt,
    AttemptStatus,
    StageId,
    AttemptKind,
)
from gw.agent_runtime.policy import (
    CapabilityRegistry,
    ORCHESTRATION_ACTIONS,
    OrchestrationProposal,
    RuntimePolicy,
    STAGE_INDEX,
    STAGE_ORDER,
)
from gw.agent_runtime.repository import RuntimeRunRepository, RuntimeArtifactRepository, _fingerprint, utc_now
from gw.agent_runtime.ports import (
    ArtifactExportPort,
    CheckpointBackend,
    ConfigurationPort,
    ExportPayload,
    InvocationDispatcherPort,
)


_KEEP = object()


def _authorizer_accepts_run_id(authorizer: Any) -> bool:
    """Detect the durable run-context callback without breaking legacy 2-arg hooks."""
    try:
        signature = inspect.signature(authorizer)
    except (TypeError, ValueError):
        # Unknown callables use the current 3-argument contract. A failed secure
        # authorization is preferable to silently dropping the run binding.
        return True
    try:
        signature.bind(None, None, None)
    except TypeError:
        return False
    return True

# Explicit lifecycle guards for user-visible mutations. Terminal states are
# deliberately absent from resume/review/rollback/configuration operations.
MUTATION_ALLOWED_STATES = {
    "start": frozenset({RunStatus.queued}),
    "execute_stage": frozenset({RunStatus.running}),
    "pause": frozenset({RunStatus.queued, RunStatus.running, RunStatus.waiting_review}),
    "resume": frozenset({RunStatus.paused}),
    "cancel": frozenset({RunStatus.queued, RunStatus.running, RunStatus.waiting_review, RunStatus.paused}),
    "apply_configuration": frozenset({RunStatus.paused}),
    "rollback": frozenset({RunStatus.running, RunStatus.waiting_review, RunStatus.paused}),
    "review_decision": frozenset({RunStatus.waiting_review}),
}


@dataclass(frozen=True, slots=True)
class ProposedAction:
    """Untrusted orchestrator proposal; policy/registry owns authorization."""

    kind: str
    capability: str
    arguments: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class _ReleasedReviewEvidence:
    review: ReviewRecord
    artifact: ArtifactRef
    approval: ApprovalRecord | None
    content: str


class _LeaseSession:
    """Renew a stage lease while a model/capability call is waiting."""

    def __init__(self, repository: RuntimeRunRepository, lease: LeaseGrant, duration: int) -> None:
        self.repository = repository
        self.lease = lease
        self.duration = duration
        self.lost = False
        self._stopping = asyncio.Event()
        self._renew_lock = asyncio.Lock()
        self._task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        self._task = asyncio.create_task(self._heartbeat())

    async def assert_valid(self) -> LeaseGrant:
        async with self._renew_lock:
            if self.lost:
                raise DomainError(ErrorCode.REVISION_CONFLICT, "The execution lease was lost; no further side effect is allowed.", request_id=self.lease.run_id.root)
            renewed = await self.repository.renew_lease(self.lease, self.duration)
            if renewed is None:
                self.lost = True
                raise DomainError(ErrorCode.REVISION_CONFLICT, "The execution lease expired or was fenced; no further side effect is allowed.", request_id=self.lease.run_id.root)
            self.lease = renewed
            return renewed

    async def stop(self) -> None:
        self._stopping.set()
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def _heartbeat(self) -> None:
        try:
            while not self._stopping.is_set():
                await asyncio.sleep(max(0.1, self.duration / 3))
                if self._stopping.is_set():
                    return
                await self.assert_valid()
        except asyncio.CancelledError:
            raise
        except Exception:
            self.lost = True


class AgentRuntimeService:
    """Run service using only injected generic roles/tools and storage ports.

    ``models.py`` and ``ports.py`` stay the sole public contract. SQLite adapters
    are development/test implementations; production adapters can implement the
    same ports without changing orchestration.
    """

    def __init__(
        self,
        repository: RuntimeRunRepository,
        artifacts: RuntimeArtifactRepository,
        registry: CapabilityRegistry,
        *,
        policy: RuntimePolicy | None = None,
        checkpoints: CheckpointBackend | None = None,
        configuration_port: ConfigurationPort | None = None,
        config_profiles: Mapping[str, ConfigSnapshot] | None = None,
        invocation_dispatcher: InvocationDispatcherPort | None = None,
        artifact_exporter: ArtifactExportPort | None = None,
        orchestrator_role: str | None = None,
        execution_authorizer: Any | None = None,
        owner_id: Identifier | None = None,
        lease_seconds: int = 120,
    ) -> None:
        self.repository = repository
        self.artifacts = artifacts
        self.registry = registry
        self.policy = policy or RuntimePolicy()
        self.checkpoints = checkpoints
        self.configuration_port = configuration_port
        self.config_profiles = dict(config_profiles or {})
        self.invocation_dispatcher = invocation_dispatcher
        self.artifact_exporter = artifact_exporter
        self.orchestrator_role = orchestrator_role
        self.execution_authorizer = execution_authorizer
        self._execution_authorizer_has_run_id = (
            _authorizer_accepts_run_id(execution_authorizer)
            if execution_authorizer is not None else False
        )
        self.owner_id = owner_id or Identifier(f"runtime:{uuid.uuid4().hex}")
        self.lease_seconds = lease_seconds
        self.graph = LangGraphRuntimeExecutor(registry, self.policy)

    async def create_run(self, request: CreateRunRequest) -> AgentRun:
        profile = None
        if self.configuration_port is not None:
            profile = await self.configuration_port.get_profile(request.config_snapshot_id)
        else:
            profile = self.config_profiles.get(request.config_snapshot_id.root)
        if profile is None:
            raise DomainError(ErrorCode.CAPABILITY_UNSUPPORTED, "The requested immutable configuration snapshot is unavailable.", request_id=request.request_id.root)
        self.policy.validate_config(profile)
        if profile.config_snapshot_id.root != request.config_snapshot_id.root or profile.mode is not request.mode:
            raise DomainError(ErrorCode.VALIDATION_FAILED, "The configuration snapshot does not match the requested run mode/id.", request_id=request.request_id.root)
        return await self.repository.create_run(request, config_snapshot=self.policy.resolve_config(profile))

    async def _execution_is_authorized(
        self,
        project_id: Identifier,
        actor_id: Identifier,
        run_id: Identifier,
    ) -> bool:
        authorizer = self.execution_authorizer
        if authorizer is None:
            return True
        if self._execution_authorizer_has_run_id:
            allowed = authorizer(project_id, actor_id, run_id)
        else:
            allowed = authorizer(project_id, actor_id)
        if inspect.isawaitable(allowed):
            allowed = await allowed
        return bool(allowed)

    @staticmethod
    def _active_config(state: Mapping[str, Any]) -> ConfigSnapshot:
        raw = state.get("config_snapshot")
        if isinstance(raw, ConfigSnapshot):
            return raw
        if not isinstance(raw, Mapping):
            raise DomainError(ErrorCode.VALIDATION_FAILED, "The durable run is missing its captured configuration snapshot.", request_id="config")
        return ConfigSnapshot.model_validate(raw)

    def _runtime_context(
        self,
        *,
        project_id: Identifier,
        run_id: Identifier,
        stage: StageId,
        attempt: StageAttempt | None,
        plan_identity: Identifier | None = None,
        expected_source_ids: Sequence[Identifier] | None = None,
        actor_id: Identifier,
        state: Mapping[str, Any],
        config: ConfigSnapshot,
        lease_session: _LeaseSession,
    ) -> dict[str, Any]:
        async def assert_dispatch_allowed(role_name: str) -> None:
            expected_inputs = attempt.input_artifact_version_ids if attempt is not None else (
                list(expected_source_ids) if expected_source_ids is not None else self._input_version_ids(state)
            )

            async def read_and_validate() -> None:
                run = await self.repository.read_run_by_id(run_id)
                if run is None or run.status is not RunStatus.running or run.current_stage is not stage:
                    raise DomainError(ErrorCode.REVISION_CONFLICT, "The run was paused, cancelled, or moved before the next side effect.", request_id=run_id.root)
                if run.config_snapshot_id.root != config.config_snapshot_id.root:
                    raise DomainError(ErrorCode.REVISION_CONFLICT, "The captured configuration changed before the next side effect.", request_id=run_id.root)
                current_state = await self._state(run_id)
                if attempt is not None and current_state.get("pending_attempt_id") != attempt.stage_attempt_id.root:
                    raise DomainError(ErrorCode.REVISION_CONFLICT, "The stage attempt is no longer the active pending attempt.", request_id=attempt.stage_attempt_id.root)
                active_refs = set(current_state.get("current_artifacts_by_stage", {}).values())
                active_refs.update(
                    item.get("version_id") for item in current_state.get("initial_artifact_refs", [])
                    if isinstance(item, Mapping) and isinstance(item.get("version_id"), str)
                )
                artifacts_by_version = {item.version_id.root: item for item in await self.artifacts.list_run_artifacts(run_id)}
                for version_id in expected_inputs:
                    current = artifacts_by_version.get(version_id.root)
                    if current is None or current.lifecycle is not ArtifactLifecycle.valid or version_id.root not in active_refs:
                        raise DomainError(ErrorCode.ARTIFACT_STALE, "A source artifact changed before the next side effect.", request_id=version_id.root)

            await lease_session.assert_valid()
            await read_and_validate()
            if not await self._execution_is_authorized(project_id, actor_id, run_id):
                raise DomainError(ErrorCode.NOT_AUTHORIZED, "The trusted identity adapter revoked this run's execute permission.", request_id=actor_id.root)
            registration = self.registry.get_role(role_name)
            if not await self.registry.authorizer(actor_id, registration.required_permissions):
                raise DomainError(ErrorCode.NOT_AUTHORIZED, "The current actor is no longer authorized for this role.", request_id=actor_id.root)
            # Role authorization and storage checks can yield while a host admin
            # revokes the session or project access. Recheck mutable run state,
            # then sample host identity last so no await separates the live
            # session check from the caller's next dispatch boundary.
            await lease_session.assert_valid()
            await read_and_validate()
            if not await self._execution_is_authorized(project_id, actor_id, run_id):
                raise DomainError(
                    ErrorCode.NOT_AUTHORIZED,
                    "The trusted identity adapter revoked this run's execute permission at dispatch time.",
                    request_id=actor_id.root,
                )

        async def dispatch_model(
            role_name: str,
            request: ModelInvocationRequest,
            *,
            format_repair: bool = False,
        ) -> ModelInvocationResult:
            if self.invocation_dispatcher is None:
                request_id = attempt.stage_attempt_id.root if attempt is not None else run_id.root
                raise DomainError(ErrorCode.CAPABILITY_UNSUPPORTED, "A durable invocation dispatcher is required for every model call.", request_id=request_id)
            await assert_dispatch_allowed(role_name)
            limit_values = config.call_limits
            bounded_payload = request.model_dump(mode="json")
            bounded_payload["limits"] = {
                "timeout_seconds": limit_values.timeout_seconds,
                "max_output_tokens": limit_values.max_output_tokens,
            }
            bounded = ModelInvocationRequest.model_validate(bounded_payload)
            parameters = bounded.model_dump(mode="json", exclude={"invocation_id", "idempotency_key"})
            canonical = json.dumps(parameters, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            fingerprint = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
            attempt_identity = plan_identity.root if plan_identity is not None else (
                attempt.stage_attempt_id.root if attempt is not None else f"planner:{fingerprint}"
            )
            base_material = f"{run_id.root}:{attempt_identity}:{role_name}"
            base_operation = Identifier("operation:" + hashlib.sha256(base_material.encode("utf-8")).hexdigest())
            call_material = f"{base_material}:{fingerprint}"
            stable_id = Identifier("call:" + hashlib.sha256(call_material.encode("utf-8")).hexdigest())
            bounded_payload = bounded.model_dump(mode="json")
            bounded_payload.update({"invocation_id": stable_id.root, "idempotency_key": stable_id.root})
            bounded = ModelInvocationRequest.model_validate(bounded_payload)
            if format_repair:
                if limit_values.max_format_repairs < 1:
                    raise DomainError(ErrorCode.QUOTA_EXCEEDED, "The captured configuration disables format repair.", request_id=base_operation.root)
                repair_id = Identifier(base_operation.root + ":format:1")
                await self.invocation_dispatcher.reserve_format_repair(run_id, base_operation, repair_id, lease=lease_session.lease)
            await assert_dispatch_allowed(role_name)
            return await self.invocation_dispatcher.invoke(
                run_id,
                bounded,
                lease=lease_session.lease,
                max_retries=limit_values.max_model_retries,
                before_send=lambda: self._guard_and_return_lease(assert_dispatch_allowed, role_name, lease_session),
            )

        return {
            "lease": lease_session.lease,
            "call_limits": config.call_limits,
            "assert_dispatch_allowed": assert_dispatch_allowed,
            "dispatch_model": dispatch_model,
            "config_snapshot": config,
            "clarification_answers": dict(state.get("clarification_answers", {})),
        }

    @staticmethod
    async def _guard_and_return_lease(guard, role_name: str, lease_session: _LeaseSession) -> LeaseGrant:
        await guard(role_name)
        return lease_session.lease

    async def _get_or_create_plan(
        self,
        *,
        project_id: Identifier,
        run: AgentRun,
        state: dict[str, Any],
        stage: StageId,
        attempt: StageAttempt | None,
        actor_id: Identifier,
        lease_session: _LeaseSession,
        writer_role: str,
        reviewer_role: str,
    ) -> tuple[AgentRun, dict[str, Any], OrchestrationProposal, Identifier]:
        if self.orchestrator_role is None:
            raise DomainError(ErrorCode.CAPABILITY_UNSUPPORTED, "No orchestration role is registered.", request_id=run.run_id.root)
        proposal_payload = state.get("pending_plan_proposal")
        if isinstance(proposal_payload, Mapping):
            proposal = self._proposal_from_payload(proposal_payload, run.run_id)
            return run, state, proposal, Identifier(state["pending_plan_id"])

        plan_input = state.get("pending_plan_input")
        plan_identity_raw = state.get("pending_plan_id")
        if not isinstance(plan_input, Mapping) or not isinstance(plan_identity_raw, str):
            plan_input = await self._planner_input(run, state, stage, attempt, actor_id, writer_role, reviewer_role)
            canonical = json.dumps(plan_input, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            sequence = int(state.get("plan_sequence", 0)) + 1
            plan_identity = Identifier("plan:" + hashlib.sha256(f"{run.run_id.root}:{sequence}:{canonical}".encode("utf-8")).hexdigest())
            state["plan_sequence"] = sequence
            state["pending_plan_id"] = plan_identity.root
            state["pending_plan_input"] = dict(plan_input)
            state["pending_plan_source_ids"] = list(plan_input.get("source_artifact_version_ids", []))
            run = self._next_run(run)
            event = make_event(run.run_id, run.version.root, "orchestration.plan.reserved", "A stable logical planner operation was persisted before dispatch.", stage=stage, stage_attempt_id=attempt.stage_attempt_id if attempt else None)
            reserve_key = Identifier("plan-reserve:" + plan_identity.root)
            reserve_fp = _fingerprint({"op": "plan-reserve", "plan_id": plan_identity.root, "input": plan_input})
            run = await self.repository.commit_transition(
                run.run_id, run.version.root - 1, run, [event], {}, lease=lease_session.lease,
                idempotency_key=reserve_key, request_fingerprint=reserve_fp, runtime_state=state,
            )
            state = await self._state(run.run_id)
            plan_identity_raw = plan_identity.root
        else:
            plan_identity = Identifier(plan_identity_raw)

        if not isinstance(plan_input, Mapping):
            plan_input = state.get("pending_plan_input")
        plan_identity = Identifier(plan_identity_raw)
        config = self._active_config(state)
        source_ids = [Identifier(item) for item in state.get("pending_plan_source_ids", [])]
        runtime_context = self._runtime_context(
            project_id=project_id,
            run_id=run.run_id,
            stage=stage,
            attempt=attempt,
            plan_identity=plan_identity,
            expected_source_ids=source_ids,
            actor_id=actor_id,
            state=state,
            config=config,
            lease_session=lease_session,
        )
        try:
            graph_result = await self.graph.propose_stage_action(
                stage=stage.value,
                actor_id=actor_id,
                orchestrator_role=self.orchestrator_role,
                execution_input=plan_input,
                runtime_context=runtime_context,
            )
        except DomainError:
            raise
        except Exception as exc:
            raise DomainError(ErrorCode.VALIDATION_FAILED, "The orchestrator output did not match the registered action contract.", request_id=plan_identity.root) from exc
        proposal = graph_result["orchestration_proposal"]
        latest = await self.repository.read_run_by_id(run.run_id)
        if latest is None or latest.status is not RunStatus.running:
            raise DomainError(ErrorCode.REVISION_CONFLICT, "The run changed before its planner decision could be recorded.", request_id=plan_identity.root)
        state = await self._state(run.run_id)
        state["pending_plan_proposal"] = self._proposal_payload(proposal)
        next_run = self._next_run(latest)
        event = make_event(run.run_id, next_run.version.root, "orchestration.proposal.recorded", "A durable planner result was recorded before policy execution.", stage=stage, stage_attempt_id=attempt.stage_attempt_id if attempt else None)
        result_key = Identifier("plan-result:" + plan_identity.root)
        result_fingerprint = _fingerprint({"op": "plan-result", "plan_id": plan_identity.root, "proposal": state["pending_plan_proposal"]})
        committed = await self.repository.commit_transition(
            run.run_id, latest.version.root, next_run, [event], {}, lease=lease_session.lease,
            idempotency_key=result_key, request_fingerprint=result_fingerprint, runtime_state=state,
        )
        return committed, await self._state(run.run_id), proposal, plan_identity

    @staticmethod
    def _current_human_revision_feedback(
        state: Mapping[str, Any],
        latest_review: ReviewRecord | None,
        stage: StageId,
    ) -> dict[str, str] | None:
        feedback = state.get("human_revision_feedback")
        if (
            not isinstance(feedback, Mapping)
            or state.get("human_revision_feedback_stage") != stage.value
            or latest_review is None
            or feedback.get("review_id") != latest_review.review_id.root
            or feedback.get("artifact_version_id") != latest_review.artifact.version_id.root
        ):
            return None
        keys = ("approval_id", "review_id", "artifact_version_id", "actor_id", "reason")
        if any(not isinstance(feedback.get(key), str) or not feedback[key] for key in keys):
            return None
        return {key: feedback[key] for key in keys}

    async def _planner_input(
        self,
        run: AgentRun,
        state: Mapping[str, Any],
        stage: StageId,
        attempt: StageAttempt | None,
        actor_id: Identifier,
        writer_role: str,
        reviewer_role: str,
    ) -> dict[str, Any]:
        capabilities: list[dict[str, str]] = []
        for role_name in self.registry.stage_writer_names:
            registration = self.registry.get_role(role_name)
            if await self.registry.authorizer(actor_id, registration.required_permissions):
                capabilities.append({"role": role_name, "description": "Registered stage-writing capability"})
        if not capabilities:
            for role_name in dict.fromkeys((writer_role,)):
                registration = self.registry.get_role(role_name)
                if registration.stage_writer and await self.registry.authorizer(actor_id, registration.required_permissions):
                    capabilities.append({"role": role_name, "description": "Registered stage-writing capability"})
        reviews = await self.repository.get_reviews(run.run_id)
        attempts = await self.repository.get_attempts(run.run_id)
        stage_outputs = {
            item.output_artifact_version_id.root
            for item in attempts if item.stage is stage and item.output_artifact_version_id is not None
        }
        latest_review = next((item for item in reversed(reviews) if item.artifact.version_id.root in stage_outputs), None)
        clarification = state.get("last_clarification") or {}
        upstream_ids = self._input_version_ids_before(state, stage)
        return {
            "user_goal": str(state.get("user_goal", "")),
            "registered_capabilities": capabilities,
            "clarification_answers": dict(state.get("clarification_answers", {})),
            "pending_question": clarification.get("questions", []) if isinstance(clarification, Mapping) else [],
            "source_artifact_version_ids": [item.root for item in upstream_ids],
            "previous_review": latest_review.model_dump(mode="json") if latest_review else None,
            "human_revision_feedback": self._current_human_revision_feedback(state, latest_review, stage),
            "attempt_kind": attempt.kind.value if attempt else None,
            "attempt_number": attempt.attempt_number if attempt else 1,
            "revision_count": self.policy.revision_count(state, stage),
            "config_snapshot_id": run.config_snapshot_id.root,
            "reviewer_role": reviewer_role,
        }

    @staticmethod
    def _proposal_payload(proposal: OrchestrationProposal) -> dict[str, Any]:
        return {
            "action": proposal.action,
            "capability": proposal.capability,
            "target_stage": proposal.target_stage.value if proposal.target_stage is not None else None,
            "question": proposal.question,
        }

    @staticmethod
    def _proposal_from_payload(payload: Mapping[str, Any], run_id: Identifier) -> OrchestrationProposal:
        if payload.get("action") not in ORCHESTRATION_ACTIONS:
            raise DomainError(ErrorCode.VALIDATION_FAILED, "The persisted planner action is invalid.", request_id=run_id.root)
        target = payload.get("target_stage")
        return OrchestrationProposal(
            action=str(payload["action"]),
            capability=payload.get("capability") if isinstance(payload.get("capability"), str) else None,
            target_stage=StageId(target) if isinstance(target, str) else None,
            question=payload.get("question") if isinstance(payload.get("question"), str) else None,
        )

    @staticmethod
    def _clear_plan(state: dict[str, Any]) -> None:
        for key in (
            "pending_plan_id", "pending_plan_input", "pending_plan_source_ids",
            "pending_plan_scope_hash", "pending_plan_proposal", "pending_plan_fingerprint",
        ):
            state.pop(key, None)

    async def _existing_attempt_candidate(self, attempt: StageAttempt) -> tuple[ArtifactRef, str] | None:
        version_id = attempt.output_artifact_version_id
        if version_id is None:
            return None
        artifact = next((item for item in await self.artifacts.list_run_artifacts(attempt.run_id) if item.version_id.root == version_id.root), None)
        if artifact is None or artifact.lifecycle is not ArtifactLifecycle.valid:
            raise DomainError(ErrorCode.ARTIFACT_STALE, "The pending attempt's saved candidate is no longer valid.", request_id=attempt.stage_attempt_id.root)
        if any(ref.version_id.root not in {item.root for item in attempt.input_artifact_version_ids} for ref in artifact.source_refs):
            raise DomainError(ErrorCode.ARTIFACT_STALE, "The candidate source references do not match its owning attempt.", request_id=version_id.root)
        content = await self.artifacts.read_content(artifact.artifact_id, artifact.version_id)
        if content is None or hashlib.sha256(content.encode("utf-8")).hexdigest() != artifact.content_hash.root:
            raise DomainError(ErrorCode.ARTIFACT_STALE, "The pending candidate content failed its durable hash check.", request_id=version_id.root)
        return artifact, content

    async def _validate_selected_writer(
        self,
        capability: str | None,
        actor_id: Identifier,
        project_id: Identifier,
        run_id: Identifier,
        stage: StageId,
        config: ConfigSnapshot,
        lease_session: _LeaseSession,
    ) -> str:
        if not capability:
            raise DomainError(ErrorCode.VALIDATION_FAILED, "A select_capability proposal must name a writer capability.", request_id=run_id.root)
        registration = self.registry.get_role(capability)
        if not registration.stage_writer:
            raise DomainError(ErrorCode.CAPABILITY_UNSUPPORTED, "The selected capability is not registered as a stage writer.", request_id=capability)
        if not await self._execution_is_authorized(project_id, actor_id, run_id):
            raise DomainError(ErrorCode.NOT_AUTHORIZED, "The trusted identity adapter revoked this run's execute permission.", request_id=actor_id.root)
        if not await self.registry.authorizer(actor_id, registration.required_permissions):
            raise DomainError(ErrorCode.NOT_AUTHORIZED, "The actor is no longer authorized for the selected writer capability.", request_id=actor_id.root)
        await lease_session.assert_valid()
        run = await self.repository.read_run_by_id(run_id)
        if run is None or run.status is not RunStatus.running or run.current_stage is not stage or run.config_snapshot_id.root != config.config_snapshot_id.root:
            raise DomainError(ErrorCode.REVISION_CONFLICT, "The run changed while the selected capability was being authorized.", request_id=run_id.root)
        return capability

    async def _pause_for_clarification(
        self,
        run: AgentRun,
        state: dict[str, Any],
        proposal: OrchestrationProposal,
        plan_identity: Identifier,
        lease_session: _LeaseSession,
    ) -> AgentRun:
        question = proposal.question.strip() if isinstance(proposal.question, str) else ""
        if not question or len(question) > 2000:
            raise DomainError(ErrorCode.VALIDATION_FAILED, "A clarification proposal must contain a question of 1–2000 characters.", request_id=plan_identity.root)
        question_id = "question:" + hashlib.sha256(f"{plan_identity.root}:{question}".encode("utf-8")).hexdigest()
        question_dto = ClarificationQuestion(question_id=question_id, text=question)
        state["clarification_record"] = {
            "questions": [question_dto.model_dump(mode="json")],
            "answers": {},
            "is_pending": True,
        }
        state["pending_clarification"] = True
        state["resume_status"] = RunStatus.running.value
        self._clear_plan(state)
        paused = self._next_run(run, status=RunStatus.paused, current_stage=run.current_stage, paused_reason="The orchestrator needs an authorized user clarification.")
        event = make_event(run.run_id, paused.version.root, "orchestration.clarification.requested", "The run paused with a persisted question; an authorized answer resumes the same goal.", stage=run.current_stage)
        return await self.repository.commit_transition(
            run.run_id, run.version.root, paused, [event], {}, lease=lease_session.lease,
            idempotency_key=Identifier("clarification-request:" + plan_identity.root),
            request_fingerprint=_fingerprint({"op": "clarification-request", "plan_id": plan_identity.root, "question": question}),
            runtime_state=state,
        )

    async def _accept_proposed_rollback(
        self,
        *,
        project_id: Identifier,
        run: AgentRun,
        state: dict[str, Any],
        stage: StageId,
        attempt: StageAttempt | None,
        proposal: OrchestrationProposal,
        plan_identity: Identifier,
        idempotency_key: Identifier,
        lease_session: _LeaseSession,
    ) -> AgentRun:
        if proposal.target_stage is None or STAGE_INDEX[proposal.target_stage.value] >= STAGE_INDEX[stage.value]:
            raise DomainError(ErrorCode.VALIDATION_FAILED, "Automatic rollback must target a strictly earlier stage.", request_id=plan_identity.root)
        attempts = await self.repository.get_attempts(run.run_id)
        stage_attempts = [item for item in attempts if item.stage is stage and item.review_id is not None]
        prior_review_id = stage_attempts[-1].review_id if stage_attempts else None
        reviews = await self.repository.get_reviews(run.run_id)
        review = next((item for item in reversed(reviews) if prior_review_id and item.review_id.root == prior_review_id.root), None)
        if review is None or not isinstance(review.rollback_proposal, Mapping):
            raise DomainError(ErrorCode.VALIDATION_FAILED, "Automatic rollback requires a persisted proposal attached to the latest exact review.", request_id=plan_identity.root)
        self._validate_rollback_evidence(review, review.rollback_proposal, stage, proposal.target_stage, plan_identity)
        current_artifact = await self._matching_artifact(review)
        if current_artifact is None:
            raise DomainError(ErrorCode.ARTIFACT_STALE, "The automatic rollback evidence is based on a stale reviewed artifact.", request_id=review.review_id.root)

        if not self.policy.allow_rollback(state, proposal.target_stage):
            state["resume_status"] = RunStatus.running.value
            state["blocked_operation"] = {"code": ErrorCode.QUOTA_EXCEEDED.value, "plan_id": plan_identity.root}
            self._clear_plan(state)
            paused = self._next_run(run, status=RunStatus.paused, current_stage=stage, paused_reason="Automatic rollback quota exhausted; increase the captured limit or choose another action.")
            event = make_event(run.run_id, paused.version.root, "run.paused.rollback_quota", "Rollback limits prevented the proposed mutation; no counter, attempt, or artifact changed.", stage=stage, review_id=review.review_id)
            return await self.repository.commit_transition(
                run.run_id, run.version.root, paused, [event], {}, lease=lease_session.lease,
                idempotency_key=Identifier("rollback-quota:" + plan_identity.root),
                request_fingerprint=_fingerprint({"op": "auto-rollback-quota", "plan_id": plan_identity.root}), runtime_state=state,
            )

        state = self.policy.add_rollback(state, proposal.target_stage)
        affected = await self.artifacts.get_downstream_artifacts(run.run_id, STAGE_INDEX, proposal.target_stage.value)
        affected_ids = [item.version_id for item in affected]
        attempts_to_write: list[StageAttempt] = []
        if attempt is not None and attempt.status in {AttemptStatus.queued, AttemptStatus.running, AttemptStatus.paused}:
            attempts_to_write.append(attempt.model_copy(update={"status": AttemptStatus.cancelled}))
        next_attempt = self._queued_attempt(
            run.run_id, proposal.target_stage, attempts, state,
            kind=AttemptKind.rollback, input_ids=self._input_version_ids_before(state, proposal.target_stage),
        )
        attempts_to_write.append(next_attempt)
        state.pop("pending_attempt_fingerprint", None)
        state["pending_attempt_id"] = next_attempt.stage_attempt_id.root
        state["current_artifacts_by_stage"] = {
            current_stage: version for current_stage, version in state.get("current_artifacts_by_stage", {}).items()
            if current_stage in STAGE_INDEX and STAGE_INDEX[current_stage] < STAGE_INDEX[proposal.target_stage.value]
        }
        affected_set = {item.root for item in affected_ids}
        state["released_versions"] = [item for item in state.get("released_versions", []) if item not in affected_set]
        state.pop("human_revision_feedback", None)
        state.pop("human_revision_feedback_stage", None)
        pending_review = state.get("pending_review")
        if isinstance(pending_review, Mapping) and pending_review.get("artifact_version_id") in affected_set:
            state.pop("pending_review", None)
        self._clear_plan(state)
        next_run = self._next_run(run, status=RunStatus.running, current_stage=proposal.target_stage, paused_reason=None)
        evidence = review.rollback_proposal.get("evidence", [])
        event = make_event(
            run.run_id, next_run.version.root, "run.rollback.accepted",
            "A review-bound evidence proposal passed runtime validation; its limits, pending attempt, and invalidations committed atomically.",
            stage=proposal.target_stage, stage_attempt_id=next_attempt.stage_attempt_id,
            artifact_version_ids=affected_ids, review_id=review.review_id,
        )
        return await self.repository.commit_transition(
            run.run_id, run.version.root, next_run, [event], {}, lease=lease_session.lease,
            idempotency_key=Identifier("auto-rollback:" + plan_identity.root),
            request_fingerprint=_fingerprint({"op": "auto-rollback", "plan_id": plan_identity.root, "target": proposal.target_stage.value}),
            runtime_state=state, attempts=attempts_to_write, stale_version_ids=affected_ids,
        )

    @staticmethod
    def _validate_rollback_evidence(
        review,
        payload: Mapping[str, Any],
        current_stage: StageId,
        target_stage: StageId,
        request_id: Identifier,
    ) -> None:
        try:
            from_stage = StageId(payload["from_stage"])
            to_stage = StageId(payload["to_stage"])
            reviewed_ref = payload["reviewed_artifact_ref"]
            evidence = payload["evidence"]
            source_refs = payload["source_refs"]
            if from_stage is not current_stage or to_stage is not target_stage or STAGE_INDEX[to_stage.value] >= STAGE_INDEX[from_stage.value]:
                raise ValueError("stage binding mismatch")
            if not isinstance(reviewed_ref, Mapping) or reviewed_ref.get("artifact_id") != review.artifact.artifact_id.root or reviewed_ref.get("version_id") != review.artifact.version_id.root:
                raise ValueError("reviewed artifact mismatch")
            if not isinstance(evidence, list) or not evidence or not isinstance(source_refs, list) or not source_refs:
                raise ValueError("evidence or source references are missing")
            findings = {
                str(item.get("finding_id")): item
                for item in review.findings if isinstance(item, Mapping) and item.get("finding_id")
            }
            for entry in evidence:
                if not isinstance(entry, Mapping):
                    raise ValueError("malformed finding evidence")
                finding = findings.get(str(entry.get("finding_id")))
                if finding is None or entry.get("location") != finding.get("location") or entry.get("description") != finding.get("description"):
                    raise ValueError("finding evidence does not match the exact review")
                source = entry.get("source_artifact_ref")
                if not isinstance(source, Mapping) or source.get("artifact_id") != review.artifact.artifact_id.root or source.get("version_id") != review.artifact.version_id.root:
                    raise ValueError("finding evidence does not bind the reviewed artifact")
            proposed_sources = {
                (item.get("artifact_id"), item.get("version_id"))
                for item in source_refs if isinstance(item, Mapping)
            }
            actual_sources = {(item.artifact_id.root, item.version_id.root) for item in review.artifact.source_refs}
            if not proposed_sources or proposed_sources != actual_sources:
                raise ValueError("proposal sources do not match the reviewed artifact provenance")
        except (KeyError, TypeError, ValueError) as exc:
            raise DomainError(ErrorCode.VALIDATION_FAILED, "Automatic rollback evidence did not bind to the exact persisted review and artifact.", request_id=request_id.root) from exc

    async def dispatch_proposal(
        self,
        actor_id: Identifier,
        proposal: ProposedAction,
        *,
        budget: BudgetDecision | None = None,
    ) -> Any:
        """Execute only a registered action, with authorization and budget gates."""
        if proposal.kind == "tool":
            return await self.registry.invoke_tool(proposal.capability, actor_id, proposal.arguments, budget=budget)
        if proposal.kind == "role":
            role = self.registry.get_role(proposal.capability)
            if not role.proposal_safe:
                raise DomainError(ErrorCode.CAPABILITY_UNSUPPORTED, "This role can run only through the fenced stage graph.", request_id=actor_id.root)
            if not await self.registry.authorizer(actor_id, role.required_permissions):
                raise DomainError(ErrorCode.NOT_AUTHORIZED, "The actor is not authorized for this role.", request_id=actor_id.root)
            return await role.handler(**dict(proposal.arguments))
        raise DomainError(ErrorCode.VALIDATION_FAILED, "The proposed action kind is not allowed.", request_id=actor_id.root)

    async def start_run(
        self,
        project_id: Identifier,
        run_id: Identifier,
        *,
        expected_version: int,
        idempotency_key: Identifier,
    ) -> AgentRun:
        fingerprint = _fingerprint({"op": "start", "project_id": project_id.root, "run_id": run_id.root, "expected_version": expected_version})
        replay = await self.repository.read_idempotent_result(run_id, idempotency_key, fingerprint)
        if replay is not None:
            return replay
        lease = await self._claim(run_id)
        try:
            run = await self._read_project_run(project_id, run_id)
            if run.version.root != expected_version:
                raise self._revision_conflict(idempotency_key)
            if run.status not in MUTATION_ALLOWED_STATES["start"]:
                raise DomainError(ErrorCode.REVISION_CONFLICT, "Only a queued run can be started.", request_id=idempotency_key.root)
            next_run = self._next_run(run, status=RunStatus.running, current_stage=STAGE_ORDER[0], paused_reason=None)
            state = await self._state(run_id)
            state["resume_status"] = RunStatus.running.value
            event = make_event(run_id, next_run.version.root, "run.started", "Run execution started under the captured configuration.", stage=STAGE_ORDER[0])
            return await self.repository.commit_transition(
                run_id, run.version.root, next_run, [event], {}, lease=lease,
                idempotency_key=idempotency_key, request_fingerprint=fingerprint, runtime_state=state,
            )
        finally:
            await self.repository.release_lease(lease)

    async def execute_current_stage(
        self,
        project_id: Identifier,
        run_id: Identifier,
        *,
        expected_version: int,
        idempotency_key: Identifier,
        actor_id: Identifier,
        writer_role: str = "writer",
        reviewer_role: str = "reviewer",
    ) -> AgentRun:
        """Execute one bounded stage attempt through the compiled LangGraph.

        Each candidate/review is committed with its attempt and run transition;
        revision or rollback attempts are queued before dispatch so a restart can
        resume them without resetting counters or losing their identity.
        """
        operation_fingerprint = _fingerprint({
            "op": "execute-stage", "run_id": run_id.root, "expected_version": expected_version,
            "actor_id": actor_id.root, "writer_role": writer_role, "reviewer_role": reviewer_role,
        })
        result_key = Identifier(f"finish:{idempotency_key.root}")
        replay = await self.repository.read_idempotent_result(run_id, result_key, operation_fingerprint)
        if replay is not None:
            return replay
        queue_key = Identifier(f"queue:{idempotency_key.root}")
        queue_fingerprint = _fingerprint({"op": "queue-stage", "request": operation_fingerprint})
        queued_replay = await self.repository.read_idempotent_result(run_id, queue_key, queue_fingerprint)

        lease = await self._claim(run_id)
        lease_session = _LeaseSession(self.repository, lease, self.lease_seconds)
        await lease_session.start()
        try:
            run = await self._read_project_run(project_id, run_id)
            state = await self._state(run_id)
            if queued_replay is None and run.version.root != expected_version:
                raise self._revision_conflict(idempotency_key)
            if run.status not in MUTATION_ALLOWED_STATES["execute_stage"] or run.current_stage is None:
                raise DomainError(ErrorCode.REVISION_CONFLICT, "The run is not ready to execute a stage.", request_id=idempotency_key.root)
            pending = await self.repository.incomplete_invocations(run_id)
            if pending:
                return await self._pause_unknown(run, state, lease_session.lease, pending, result_key, operation_fingerprint)

            attempts = await self.repository.get_attempts(run_id)
            attempt = self._pending_attempt(attempts, state, run.current_stage)
            plan_identity: Identifier | None = None
            proposal: OrchestrationProposal | None = None
            if self.orchestrator_role is not None:
                run, state, proposal, plan_identity = await self._get_or_create_plan(
                    project_id=project_id,
                    run=run,
                    state=state,
                    stage=run.current_stage,
                    attempt=attempt,
                    actor_id=actor_id,
                    lease_session=lease_session,
                    writer_role=writer_role,
                    reviewer_role=reviewer_role,
                )
                if proposal.action == "clarify":
                    return await self._pause_for_clarification(run, state, proposal, plan_identity, lease_session)
                if proposal.action == "select_capability":
                    writer_role = await self._validate_selected_writer(
                        proposal.capability, actor_id, project_id, run_id, run.current_stage,
                        self._active_config(state), lease_session,
                    )
                    state["selected_writer_role"] = writer_role
                elif proposal.action == "rollback":
                    return await self._accept_proposed_rollback(
                        project_id=project_id, run=run, state=state, stage=run.current_stage,
                        attempt=attempt, proposal=proposal, plan_identity=plan_identity,
                        idempotency_key=idempotency_key, lease_session=lease_session,
                    )
                elif proposal.action == "review" and (attempt is None or attempt.output_artifact_version_id is None):
                    raise DomainError(ErrorCode.VALIDATION_FAILED, "The planner requested review without a durable candidate artifact.", request_id=plan_identity.root)
                elif proposal.action == "revise" and attempt is None and not any(item.stage is run.current_stage and item.review_id for item in attempts):
                    raise DomainError(ErrorCode.VALIDATION_FAILED, "The planner requested revision without a prior stage review.", request_id=plan_identity.root)
                elif proposal.action not in {"select_capability", "create", "review", "revise"}:
                    raise DomainError(ErrorCode.VALIDATION_FAILED, "The planner action is not valid for this stage transition.", request_id=plan_identity.root)
                if proposal.action != "select_capability" and isinstance(state.get("selected_writer_role"), str):
                    # A persisted selection remains the active stage-writing
                    # capability across later stages and worker reconstruction.
                    writer_role = await self._validate_selected_writer(
                        state["selected_writer_role"], actor_id, project_id, run_id, run.current_stage,
                        self._active_config(state), lease_session,
                    )
            elif isinstance(state.get("selected_writer_role"), str):
                writer_role = state["selected_writer_role"]
            if attempt is None:
                if queued_replay is not None:
                    # A previously accepted queued operation must have its attempt in business storage.
                    raise RuntimeError("Queued idempotency record has no matching queued attempt.")
                attempt_number = 1 + sum(1 for item in attempts if item.stage is run.current_stage)
                kind = AttemptKind.initial if attempt_number == 1 else AttemptKind.revision
                if kind is AttemptKind.revision and not self.policy.allow_revision(state, run.current_stage):
                    raise DomainError(ErrorCode.QUOTA_EXCEEDED, "The stage revision limit in the captured snapshot is exhausted.", request_id=idempotency_key.root)
                if kind is AttemptKind.revision:
                    # A revision may be started after an operator increased a
                    # previously exhausted limit. In that case no earlier gate
                    # transition queued the attempt or consumed its slot, so
                    # reserve the allowance in the same CAS as this queue.
                    state = self.policy.add_revision(state, run.current_stage)
                attempt_id = Identifier(f"attempt:{hashlib.sha256((run_id.root + ':' + idempotency_key.root).encode()).hexdigest()}")
                if any(item.stage_attempt_id.root == attempt_id.root for item in attempts):
                    # The same request was already accepted; use its persisted record.
                    attempt = next(item for item in attempts if item.stage_attempt_id.root == attempt_id.root)
                else:
                    attempt = StageAttempt(
                        schema_version=1,
                        stage_attempt_id=attempt_id,
                        run_id=run_id,
                        stage=run.current_stage,
                        kind=kind,
                        attempt_number=attempt_number,
                        input_artifact_version_ids=self._input_version_ids(state),
                        status=AttemptStatus.queued,
                        invocation_ids=[],
                        output_artifact_version_id=None,
                        review_id=None,
                        created_at=utc_now(),
                    )
                    state["pending_attempt_id"] = attempt_id.root
                    state["pending_attempt_fingerprint"] = operation_fingerprint
                    state["selected_writer_role"] = writer_role
                    queued_run = self._next_run(run)
                    queued_event = make_event(run_id, queued_run.version.root, "attempt.queued", "A stage attempt was accepted before capability dispatch.", stage=run.current_stage, stage_attempt_id=attempt_id)
                    run = await self.repository.commit_transition(
                        run_id, run.version.root, queued_run, [queued_event], {}, lease=lease_session.lease,
                        idempotency_key=queue_key, request_fingerprint=queue_fingerprint,
                        runtime_state=state, attempts=[attempt],
                    )
            elif state.get("pending_attempt_fingerprint") not in (None, operation_fingerprint) and queued_replay is None:
                # A new caller may resume a previously accepted attempt only when it presents its current CAS version.
                if expected_version != run.version.root:
                    raise self._revision_conflict(idempotency_key)

            stage = run.current_stage
            if attempt.stage is not stage or attempt.status not in {AttemptStatus.queued, AttemptStatus.running}:
                raise DomainError(ErrorCode.REVISION_CONFLICT, "The pending stage attempt no longer matches the run.", request_id=idempotency_key.root)
            previous_reviews = await self.repository.get_reviews(run_id)
            latest_review = next((item for item in reversed(previous_reviews) if item.artifact.version_id.root in {x.output_artifact_version_id.root for x in attempts if x.stage is stage and x.output_artifact_version_id}), None)
            context = {
                "run_id": run_id.root,
                "stage": stage.value,
                "stage_attempt_id": attempt.stage_attempt_id.root,
                "user_goal": state.get("user_goal", ""),
                "input_artifact_version_ids": self._input_version_ids(state),
                "config_snapshot_id": run.config_snapshot_id.root,
                "config_snapshot": state.get("config_snapshot"),
                "revision_count": self.policy.revision_count(state, stage),
                "previous_review": latest_review.model_dump(mode="json") if latest_review else None,
                "human_revision_feedback": self._current_human_revision_feedback(state, latest_review, stage),
            }

            async def artifact_factory(candidate: ArtifactVersionInput) -> tuple[ArtifactRef, str]:
                metadata = dict(candidate.metadata)
                metadata.update({"run_id": run_id.root, "runtime_stage": stage.value, "stage_attempt_id": attempt.stage_attempt_id.root})
                candidate_payload = candidate.model_dump(mode="json")
                candidate_payload["metadata"] = metadata
                candidate_payload["idempotency_key"] = f"artifact:{attempt.stage_attempt_id.root}"
                safe = ArtifactVersionInput.model_validate(candidate_payload)
                artifact = await self.artifacts.create_version(safe)
                content = await self.artifacts.read_content(artifact.artifact_id, artifact.version_id)
                if content is None:
                    raise RuntimeError("The exact candidate content was not persisted.")
                latest = await self.repository.read_run_by_id(run_id)
                latest_state = await self._state(run_id)
                if latest is None or latest.status is not RunStatus.running or latest_state.get("pending_attempt_id") != attempt.stage_attempt_id.root:
                    raise DomainError(ErrorCode.REVISION_CONFLICT, "The run changed before the candidate reference could be checkpointed.", request_id=attempt.stage_attempt_id.root)
                current_attempt = next((item for item in await self.repository.get_attempts(run_id) if item.stage_attempt_id.root == attempt.stage_attempt_id.root), None)
                if current_attempt is None:
                    raise DomainError(ErrorCode.REVISION_CONFLICT, "The candidate has no durable stage-attempt owner.", request_id=attempt.stage_attempt_id.root)
                if current_attempt.output_artifact_version_id is None:
                    persisted_attempt = current_attempt.model_copy(update={"status": AttemptStatus.running, "output_artifact_version_id": artifact.version_id})
                    advanced_run = self._next_run(latest)
                    persisted_event = make_event(run_id, advanced_run.version.root, "artifact.candidate.persisted", "Candidate content and its owning attempt reference are durable before reviewer dispatch.", stage=stage, stage_attempt_id=attempt.stage_attempt_id, artifact_version_ids=[artifact.version_id])
                    await self.repository.commit_transition(
                        run_id, latest.version.root, advanced_run, [persisted_event], {}, lease=lease_session.lease,
                        runtime_state=latest_state, attempts=[persisted_attempt],
                    )
                return artifact, content

            config_snapshot = self._active_config(state)
            runtime_context = self._runtime_context(
                project_id=project_id,
                run_id=run_id,
                stage=stage,
                attempt=attempt,
                plan_identity=plan_identity,
                actor_id=actor_id,
                state=state,
                config=config_snapshot,
                lease_session=lease_session,
            )
            graph_state = await self.graph.execute_stage_once(
                stage=stage.value,
                actor_id=actor_id,
                writer_role=writer_role,
                reviewer_role=reviewer_role,
                execution_input=context,
                artifact_factory=artifact_factory,
                runtime_context=runtime_context,
                score_config=config_snapshot,
                existing_candidate=await self._existing_attempt_candidate(attempt),
            )
            # Retry reservations may have atomically advanced the run revision from an injected gateway.
            latest = await self.repository.read_run_by_id(run_id)
            if latest is None or latest.status is not RunStatus.running:
                raise DomainError(ErrorCode.REVISION_CONFLICT, "The run changed while its capability graph was executing.", request_id=idempotency_key.root)
            run = latest
            state = await self._state(run_id)
            artifact = graph_state["artifact"]
            review = graph_state["review"]
            gate = graph_state["gate"]
            completed_attempt = attempt.model_copy(update={
                "status": AttemptStatus.succeeded,
                "output_artifact_version_id": artifact.version_id,
                "review_id": review.review_id,
            })
            state.pop("pending_attempt_id", None)
            state.pop("pending_attempt_fingerprint", None)
            self._clear_plan(state)
            next_status = RunStatus.running
            next_stage: StageId | None = stage
            paused_reason: str | None = None
            records: list[StageAttempt] = [completed_attempt]
            events: list[RunEvent] = [make_event(
                run_id, run.version.root + 1, "review.completed", "Exact artifact version received a program-computed review.",
                stage=stage, stage_attempt_id=attempt.stage_attempt_id, artifact_version_ids=[artifact.version_id], review_id=review.review_id,
            )]
            mode = state.get("mode", "automatic")
            if gate.gate.value == "pass":
                if mode == "approval":
                    state["pending_review"] = {
                        "stage": stage.value, "review_id": review.review_id.root,
                        "artifact_version_id": artifact.version_id.root,
                        "stage_attempt_id": attempt.stage_attempt_id.root,
                    }
                    next_status = RunStatus.waiting_review
                    next_stage = stage
                    events.append(make_event(run_id, run.version.root + 1, "run.waiting_review", "A passing artifact is waiting for exact-version human approval.", stage=stage, stage_attempt_id=attempt.stage_attempt_id, artifact_version_ids=[artifact.version_id], review_id=review.review_id))
                else:
                    state.setdefault("current_artifacts_by_stage", {})[stage.value] = artifact.version_id.root
                    next_stage = self.policy.next_stage(stage)
                    if next_stage is None:
                        next_status = RunStatus.succeeded
                        state.setdefault("released_versions", []).append(artifact.version_id.root)
                    else:
                        state.setdefault("released_versions", []).append(artifact.version_id.root)
                    events.append(make_event(run_id, run.version.root + 1, "stage.gate.passed", "Program validation and score gate passed for this exact artifact version.", stage=stage, stage_attempt_id=attempt.stage_attempt_id, artifact_version_ids=[artifact.version_id], review_id=review.review_id))
            elif mode == "approval" and gate.program_validation_passed:
                # Low score may be explicitly overridden; program failures never wait for or accept override.
                state["pending_review"] = {
                    "stage": stage.value, "review_id": review.review_id.root,
                    "artifact_version_id": artifact.version_id.root,
                    "stage_attempt_id": attempt.stage_attempt_id.root,
                }
                next_status = RunStatus.waiting_review
                next_stage = stage
                events.append(make_event(run_id, run.version.root + 1, "run.waiting_review", "Program checks passed but the low score requires a human revision or override decision.", stage=stage, stage_attempt_id=attempt.stage_attempt_id, artifact_version_ids=[artifact.version_id], review_id=review.review_id))
            elif self.policy.allow_revision(state, stage):
                state = self.policy.add_revision(state, stage)
                revision_attempt = self._queued_attempt(run_id, stage, attempts, state, kind=AttemptKind.revision, input_ids=self._input_version_ids(state))
                state["pending_attempt_id"] = revision_attempt.stage_attempt_id.root
                next_stage = stage
                next_status = RunStatus.running
                records.append(revision_attempt)
                events.append(make_event(run_id, run.version.root + 1, "attempt.queued.revision", "A bounded revision attempt was queued after a failed gate; its counter was consumed atomically.", stage=stage, stage_attempt_id=revision_attempt.stage_attempt_id, artifact_version_ids=[artifact.version_id], review_id=review.review_id))
            else:
                next_stage = stage
                next_status = RunStatus.paused
                paused_reason = "Stage revision limit exhausted; human action is required."
                state["resume_status"] = RunStatus.running.value
                events.append(make_event(run_id, run.version.root + 1, "run.paused.quota", "Revision limit exhausted; no further attempt or artifact was created.", stage=stage, stage_attempt_id=attempt.stage_attempt_id, artifact_version_ids=[artifact.version_id], review_id=review.review_id))
            # Reviewer output is durable at the invocation layer before this point.
            # Revalidate its exact candidate and every role that contributed to the
            # decision at the last business-write boundary; a returned model result
            # is not authority to release after a pause, revocation, or source change.
            durable_attempt = next(
                (item for item in await self.repository.get_attempts(run_id)
                 if item.stage_attempt_id.root == attempt.stage_attempt_id.root),
                None,
            )
            if (
                durable_attempt is None
                or durable_attempt.output_artifact_version_id is None
                or durable_attempt.output_artifact_version_id.root != artifact.version_id.root
            ):
                raise DomainError(
                    ErrorCode.ARTIFACT_STALE,
                    "The review result no longer matches its durable stage candidate.",
                    request_id=attempt.stage_attempt_id.root,
                )
            current_candidate = await self._existing_attempt_candidate(durable_attempt)
            if (
                current_candidate is None
                or current_candidate[0].artifact_id.root != artifact.artifact_id.root
                or current_candidate[0].version_id.root != artifact.version_id.root
                or current_candidate[0].revision.root != artifact.revision.root
                or current_candidate[0].content_hash.root != artifact.content_hash.root
                or review.artifact.artifact_id.root != artifact.artifact_id.root
                or review.artifact.version_id.root != artifact.version_id.root
                or review.artifact.revision.root != artifact.revision.root
                or review.artifact.content_hash.root != artifact.content_hash.root
            ):
                raise DomainError(
                    ErrorCode.ARTIFACT_STALE,
                    "The reviewed artifact or its source versions are no longer the exact durable candidate.",
                    request_id=artifact.version_id.root,
                )

            dispatch_guard = runtime_context.get("assert_dispatch_allowed")
            if not callable(dispatch_guard):
                raise DomainError(
                    ErrorCode.CAPABILITY_UNSUPPORTED,
                    "A final runtime authorization guard is required before review commit.",
                    request_id=run_id.root,
                )
            roles_to_revalidate = [writer_role, reviewer_role]
            if plan_identity is not None and self.orchestrator_role is not None:
                roles_to_revalidate.append(self.orchestrator_role)
            for role_name in dict.fromkeys(roles_to_revalidate):
                await dispatch_guard(role_name)

            # The exact rejection context has now reached the planner, writer,
            # and reviewer. Retire it atomically with this completed revision so
            # a later stage cannot inherit an unrelated human instruction.
            state.pop("human_revision_feedback", None)
            state.pop("human_revision_feedback_stage", None)
            next_run = self._next_run(run, status=next_status, current_stage=next_stage, paused_reason=paused_reason)
            result = await self.repository.commit_transition(
                run_id, run.version.root, next_run, events, {}, lease=lease_session.lease,
                idempotency_key=result_key, request_fingerprint=operation_fingerprint,
                runtime_state=state, attempts=records, reviews=[review],
            )
            if self.checkpoints is not None:
                await self.checkpoints.save(run_id, {
                    "run_version": result.version.root,
                    "status": result.status.value,
                    "stage": result.current_stage.value if result.current_stage else None,
                    "attempt_id": attempt.stage_attempt_id.root,
                    "artifact_version_id": artifact.version_id.root,
                    "review_id": review.review_id.root,
                    "gate": gate.gate.value,
                }, lease=lease_session.lease)
            return result
        except DomainError as exc:
            if exc.code is ErrorCode.OUTCOME_UNKNOWN:
                latest = await self.repository.read_run_by_id(run_id)
                unresolved = await self.repository.incomplete_invocations(run_id)
                if latest is not None and latest.status is RunStatus.running and unresolved:
                    current_state = await self._state(run_id)
                    try:
                        active_lease = await lease_session.assert_valid()
                        return await self._pause_unknown(latest, current_state, active_lease, unresolved, result_key, operation_fingerprint)
                    except DomainError:
                        # Recovery will persist the pause if the lease was
                        # fenced while the upstream outcome was being recorded.
                        raise exc
            if exc.code in {ErrorCode.VALIDATION_FAILED, ErrorCode.QUOTA_EXCEEDED, ErrorCode.CAPABILITY_UNSUPPORTED, ErrorCode.NOT_AUTHORIZED}:
                latest = await self.repository.read_run_by_id(run_id)
                if latest is not None and latest.status is RunStatus.running:
                    current_state = await self._state(run_id)
                    current_state["resume_status"] = RunStatus.running.value
                    current_state["blocked_operation"] = {
                        "code": exc.code.value,
                        "request_id": exc.request_id,
                        "plan_id": current_state.get("pending_plan_id"),
                        "attempt_id": current_state.get("pending_attempt_id"),
                    }
                    paused = self._next_run(latest, status=RunStatus.paused, current_stage=latest.current_stage, paused_reason="A runtime action requires a changed configuration or operator decision.")
                    event = make_event(run_id, paused.version.root, "run.paused.runtime_action", "An invalid, unauthorized, or exhausted operation was durably paused; automatic ticks will not mint a new operation key.", stage=latest.current_stage, stage_attempt_id=Identifier(current_state["pending_attempt_id"]) if isinstance(current_state.get("pending_attempt_id"), str) else None)
                    paused_result = await self.repository.commit_transition(
                        run_id, latest.version.root, paused, [event], {}, lease=await lease_session.assert_valid(),
                        idempotency_key=Identifier("runtime-failure:" + idempotency_key.root),
                        request_fingerprint=_fingerprint({"op": "runtime-failure", "request": operation_fingerprint, "code": exc.code.value}),
                        runtime_state=current_state,
                    )
                    # Persist the safe pause for background recovery, while
                    # still returning an authorization failure to a direct
                    # caller that attempted the revoked operation.
                    if exc.code is ErrorCode.NOT_AUTHORIZED:
                        raise
                    return paused_result
            raise
        finally:
            await lease_session.stop()
            await self.repository.release_lease(lease_session.lease)

    async def submit_review_decision(
        self,
        request: ReviewDecisionRequest,
        *,
        actor_id: Identifier,
        pause_on_exhaustion: bool = True,
    ) -> ApprovalRecord | AgentRun:
        fingerprint = _fingerprint({
            "request": request.model_dump(mode="json"), "actor_id": actor_id.root,
            "pause_on_exhaustion": pause_on_exhaustion,
        })
        replay = await self.repository.read_idempotent_result(request.run_id, request.idempotency_key, fingerprint)
        if replay is not None:
            return replay
        lease = await self._claim(request.run_id)
        try:
            run = await self.repository.read_run_by_id(request.run_id)
            if run is None or run.version.root != request.expected_version.root:
                raise self._revision_conflict(request.request_id)
            if run.status not in MUTATION_ALLOWED_STATES["review_decision"] or run.current_stage is None:
                raise DomainError(ErrorCode.MANUAL_REVIEW_REQUIRED, "The run has no pending human review.", request_id=request.request_id.root)
            state = await self._state(request.run_id)
            pending = state.get("pending_review")
            if not isinstance(pending, Mapping) or pending.get("review_id") != request.review_id.root or pending.get("artifact_version_id") != request.artifact_version_id.root or pending.get("stage") != run.current_stage.value:
                raise DomainError(ErrorCode.ARTIFACT_STALE, "The decision does not match the current review and artifact versions.", request_id=request.request_id.root)
            reviews = await self.repository.get_reviews(request.run_id)
            review = next((item for item in reversed(reviews) if item.review_id.root == request.review_id.root), None)
            if review is None or review.artifact.version_id.root != request.artifact_version_id.root:
                raise DomainError(ErrorCode.ARTIFACT_STALE, "The referenced review is unavailable or belongs to another artifact version.", request_id=request.request_id.root)
            artifact = await self._matching_artifact(review)
            if artifact is None:
                raise DomainError(ErrorCode.ARTIFACT_STALE, "The exact reviewed artifact version or revision is no longer current.", request_id=request.request_id.root)
            approval = ApprovalRecord(
                schema_version=1,
                approval_id=uuid.uuid4().hex,
                run_id=request.run_id,
                review_id=request.review_id,
                artifact_version_id=request.artifact_version_id,
                decision=request.decision,
                actor_id=actor_id,
                reason=request.reason,
                expected_version=request.expected_version,
                created_at=utc_now(),
            )
            events: list[RunEvent] = []
            attempts: list[StageAttempt] = []
            next_status: RunStatus
            next_stage: StageId | None
            paused_reason: str | None = None
            if request.decision in {ApprovalDecision.approve, ApprovalDecision.override}:
                state.pop("human_revision_feedback", None)
                state.pop("human_revision_feedback_stage", None)
                pass_score = self._active_config(state).pass_score
                exact_score = Decimal(review.overall_score_decimal or str(review.overall_score))
                if request.decision is ApprovalDecision.override:
                    self.policy.validate_override(review, artifact, pass_score=pass_score)
                elif not (review.program_validation_passed and review.conclusion is ReviewConclusion.pass_ and exact_score >= Decimal(str(pass_score))):
                    raise DomainError(ErrorCode.VALIDATION_FAILED, "Approval cannot bypass the program or score gate; low scores require a valid override.", request_id=request.request_id.root)
                state.setdefault("current_artifacts_by_stage", {})[run.current_stage.value] = artifact.version_id.root
                state.setdefault("released_versions", []).append(artifact.version_id.root)
                state.pop("pending_review", None)
                next_stage = self.policy.next_stage(run.current_stage)
                next_status = RunStatus.succeeded if next_stage is None else RunStatus.running
                events.append(make_event(request.run_id, run.version.root + 1, "approval.accepted", "Authorized approval is bound to the exact reviewed artifact version.", stage=run.current_stage, stage_attempt_id=Identifier(pending["stage_attempt_id"]), artifact_version_ids=[artifact.version_id], review_id=review.review_id, approval_id=approval.approval_id))
            else:
                state["human_revision_feedback"] = {
                    "approval_id": approval.approval_id.root,
                    "review_id": review.review_id.root,
                    "artifact_version_id": artifact.version_id.root,
                    "actor_id": actor_id.root,
                    "reason": approval.reason.root,
                }
                state["human_revision_feedback_stage"] = run.current_stage.value
                if not self.policy.allow_revision(state, run.current_stage):
                    if not pause_on_exhaustion:
                        raise DomainError(ErrorCode.QUOTA_EXCEEDED, "The stage revision limit is exhausted; request was rejected without state changes.", request_id=request.request_id.root)
                    state["resume_status"] = RunStatus.running.value
                    next_status = RunStatus.paused
                    next_stage = run.current_stage
                    paused_reason = "Stage revision limit exhausted after review rejection."
                    state.pop("pending_review", None)
                    state.pop("pending_attempt_id", None)
                    state.pop("pending_attempt_fingerprint", None)
                    events.append(make_event(request.run_id, run.version.root + 1, "run.paused.quota", "Review rejection could not queue a revision; state-only pause committed.", stage=run.current_stage, review_id=review.review_id, approval_id=approval.approval_id))
                else:
                    state = self.policy.add_revision(state, run.current_stage)
                    revision_attempt = self._queued_attempt(request.run_id, run.current_stage, await self.repository.get_attempts(request.run_id), state, kind=AttemptKind.revision, input_ids=self._input_version_ids(state))
                    attempts.append(revision_attempt)
                    state["pending_attempt_id"] = revision_attempt.stage_attempt_id.root
                    state.pop("pending_review", None)
                    next_status = RunStatus.running
                    next_stage = run.current_stage
                    events.append(make_event(request.run_id, run.version.root + 1, "approval.rejected.revision_queued", "Authorized rejection queued a bounded revision attempt.", stage=run.current_stage, stage_attempt_id=revision_attempt.stage_attempt_id, artifact_version_ids=[artifact.version_id], review_id=review.review_id, approval_id=approval.approval_id))
            next_run = self._next_run(run, status=next_status, current_stage=next_stage, paused_reason=paused_reason)
            committed = await self.repository.commit_transition(
                request.run_id, run.version.root, next_run, events, {}, lease=lease,
                idempotency_key=request.idempotency_key, request_fingerprint=fingerprint,
                runtime_state=state, attempts=attempts, approvals=[approval],
                idempotency_response=approval if next_status is not RunStatus.paused else None,
            )
            return approval if committed == next_run and next_status is not RunStatus.paused else committed
        finally:
            await self.repository.release_lease(lease)

    async def request_rollback(
        self,
        project_id: Identifier,
        run_id: Identifier,
        *,
        target_stage: StageId,
        expected_version: int,
        idempotency_key: Identifier,
        pause_on_exhaustion: bool = True,
    ) -> AgentRun:
        fingerprint = _fingerprint({
            "op": "rollback", "run_id": run_id.root, "target_stage": target_stage.value,
            "expected_version": expected_version, "pause_on_exhaustion": pause_on_exhaustion,
        })
        replay = await self.repository.read_idempotent_result(run_id, idempotency_key, fingerprint)
        if replay is not None:
            return replay
        lease = await self._claim(run_id)
        try:
            run = await self._read_project_run(project_id, run_id)
            if run.version.root != expected_version:
                raise self._revision_conflict(idempotency_key)
            if run.status not in MUTATION_ALLOWED_STATES["rollback"]:
                raise DomainError(ErrorCode.REVISION_CONFLICT, "Rollback is not allowed from this run state.", request_id=idempotency_key.root)
            if run.current_stage is None or STAGE_INDEX[target_stage.value] >= STAGE_INDEX[run.current_stage.value]:
                raise DomainError(ErrorCode.VALIDATION_FAILED, "Rollback must target a strictly earlier stage.", request_id=idempotency_key.root)
            state = await self._state(run_id)
            if not self.policy.allow_rollback(state, target_stage):
                if not pause_on_exhaustion:
                    raise DomainError(ErrorCode.QUOTA_EXCEEDED, "Rollback or target-stage revision limit exhausted; no state was changed.", request_id=idempotency_key.root)
                state["resume_status"] = RunStatus.running.value
                paused = self._next_run(run, status=RunStatus.paused, current_stage=run.current_stage, paused_reason="Rollback or target-stage revision limit exhausted.")
                event = make_event(run_id, paused.version.root, "run.paused.quota", "Rollback quota exhausted; state and event committed without counters or artifacts.", stage=run.current_stage)
                return await self.repository.commit_transition(run_id, run.version.root, paused, [event], {}, lease=lease, idempotency_key=idempotency_key, request_fingerprint=fingerprint, runtime_state=state)
            state = self.policy.add_rollback(state, target_stage)
            affected = await self.artifacts.get_downstream_artifacts(run_id, STAGE_INDEX, target_stage.value)
            all_attempts = await self.repository.get_attempts(run_id)
            attempts_to_update = [
                item.model_copy(update={"status": AttemptStatus.cancelled})
                for item in all_attempts
                if item.status in {AttemptStatus.queued, AttemptStatus.running, AttemptStatus.waiting_review, AttemptStatus.paused}
                and STAGE_INDEX[item.stage.value] >= STAGE_INDEX[target_stage.value]
            ]
            attempt = self._queued_attempt(run_id, target_stage, all_attempts, state, kind=AttemptKind.rollback, input_ids=self._input_version_ids_before(state, target_stage))
            state["pending_attempt_id"] = attempt.stage_attempt_id.root
            state.pop("pending_attempt_fingerprint", None)
            state.pop("pending_review", None)
            state.pop("human_revision_feedback", None)
            state.pop("human_revision_feedback_stage", None)
            state.pop("blocked_plan_id", None)
            state.pop("blocked_operation", None)
            self._clear_plan(state)
            state["resume_status"] = RunStatus.running.value
            state["current_artifacts_by_stage"] = {
                stage: version for stage, version in state.get("current_artifacts_by_stage", {}).items()
                if stage in STAGE_INDEX and STAGE_INDEX[stage] < STAGE_INDEX[target_stage.value]
            }
            state["released_versions"] = [version for version in state.get("released_versions", []) if version not in {item.version_id.root for item in affected}]
            next_run = self._next_run(run, status=RunStatus.running, current_stage=target_stage, paused_reason=None)
            event = make_event(
                run_id, next_run.version.root, "run.rollback.accepted",
                "Rollback counters, target attempt, and all affected artifact invalidations were committed atomically.",
                stage=target_stage, stage_attempt_id=attempt.stage_attempt_id,
                artifact_version_ids=[item.version_id for item in affected],
            )
            return await self.repository.commit_transition(
                run_id, run.version.root, next_run, [event], {}, lease=lease,
                idempotency_key=idempotency_key, request_fingerprint=fingerprint,
                runtime_state=state, attempts=[*attempts_to_update, attempt], stale_version_ids=[item.version_id for item in affected],
            )
        finally:
            await self.repository.release_lease(lease)

    async def pause_run(
        self,
        project_id: Identifier,
        run_id: Identifier,
        *,
        expected_version: int,
        idempotency_key: Identifier,
        reason: str = "Paused by an authorized user.",
    ) -> AgentRun:
        fingerprint = _fingerprint({"op": "pause", "run_id": run_id.root, "expected_version": expected_version, "reason": reason})
        replay = await self.repository.read_idempotent_result(run_id, idempotency_key, fingerprint)
        if replay is not None:
            return replay
        lease = await self._claim(run_id)
        try:
            run = await self._read_project_run(project_id, run_id)
            if run.version.root != expected_version:
                raise self._revision_conflict(idempotency_key)
            if run.status not in MUTATION_ALLOWED_STATES["pause"]:
                raise DomainError(ErrorCode.REVISION_CONFLICT, "This run cannot be paused from its current state.", request_id=idempotency_key.root)
            state = await self._state(run_id)
            state["resume_status"] = run.status.value
            paused = self._next_run(run, status=RunStatus.paused, current_stage=run.current_stage, paused_reason=reason)
            event = make_event(run_id, paused.version.root, "run.paused", "Run pause state and event committed atomically.", stage=run.current_stage)
            return await self.repository.commit_transition(run_id, run.version.root, paused, [event], {}, lease=lease, idempotency_key=idempotency_key, request_fingerprint=fingerprint, runtime_state=state)
        finally:
            await self.repository.release_lease(lease)

    async def resume_run(
        self,
        project_id: Identifier,
        run_id: Identifier,
        *,
        expected_version: int,
        idempotency_key: Identifier,
    ) -> AgentRun:
        fingerprint = _fingerprint({"op": "resume", "run_id": run_id.root, "expected_version": expected_version})
        replay = await self.repository.read_idempotent_result(run_id, idempotency_key, fingerprint)
        if replay is not None:
            return replay
        lease = await self._claim(run_id)
        try:
            run = await self._read_project_run(project_id, run_id)
            if run.version.root != expected_version:
                raise self._revision_conflict(idempotency_key)
            if run.status not in MUTATION_ALLOWED_STATES["resume"]:
                raise DomainError(ErrorCode.REVISION_CONFLICT, "Only a paused run can be resumed.", request_id=idempotency_key.root)
            unresolved = await self.repository.incomplete_invocations(run_id)
            if unresolved:
                raise DomainError(ErrorCode.OUTCOME_UNKNOWN, "An already-sent invocation remains unresolved; resumption will not resend it.", request_id=idempotency_key.root)
            state = await self._state(run_id)
            if state.get("pending_clarification"):
                raise DomainError(ErrorCode.REVISION_CONFLICT, "This run is waiting for the requested clarification; submit answers before resuming.", request_id=idempotency_key.root)
            blocked = state.get("blocked_operation")
            if isinstance(blocked, Mapping) and blocked.get("code") != ErrorCode.NOT_AUTHORIZED.value:
                raise DomainError(ErrorCode.MANUAL_REVIEW_REQUIRED, "A failed runtime operation needs a changed configuration or an explicit operator decision before resumption.", request_id=idempotency_key.root)
            attempts = await self.repository.get_attempts(run_id)
            pending_review_is_current = await self._has_current_pending_review(run, state)
            active_attempt = self._pending_attempt(attempts, state, run.current_stage) if run.current_stage else None
            prior_stage_attempts = sum(1 for item in attempts if item.stage is run.current_stage) if run.current_stage else 0
            if (
                run.current_stage is not None
                and active_attempt is None
                and not pending_review_is_current
                and prior_stage_attempts > 0
                and not self.policy.allow_revision(state, run.current_stage)
            ):
                raise DomainError(ErrorCode.QUOTA_EXCEEDED, "No attempt can be resumed until the captured revision limit is explicitly increased.", request_id=idempotency_key.root)
            target = RunStatus.waiting_review if pending_review_is_current else RunStatus(state.get("resume_status", RunStatus.running.value))
            if target not in {RunStatus.queued, RunStatus.running, RunStatus.waiting_review}:
                target = RunStatus.running
            if target is RunStatus.waiting_review and not pending_review_is_current:
                target = RunStatus.running
            resumed = self._next_run(run, status=target, current_stage=run.current_stage, paused_reason=None)
            state["resume_status"] = target.value
            event = make_event(run_id, resumed.version.root, "run.resumed", "Run resumed from durable business state without resetting counters or configuration.", stage=run.current_stage)
            return await self.repository.commit_transition(run_id, run.version.root, resumed, [event], {}, lease=lease, idempotency_key=idempotency_key, request_fingerprint=fingerprint, runtime_state=state)
        finally:
            await self.repository.release_lease(lease)

    async def read_clarifications(self, run_id: Identifier) -> ClarificationRead:
        state = await self._state(run_id)
        record = state.get("clarification_record")
        if not isinstance(record, Mapping):
            return ClarificationRead(questions=[], answers={}, is_pending=False)
        return ClarificationRead(
            questions=[ClarificationQuestion.model_validate(item) for item in record.get("questions", [])],
            answers=dict(record.get("answers", {})),
            is_pending=bool(record.get("is_pending", False)),
        )

    async def submit_clarification(
        self,
        project_id: Identifier,
        run_id: Identifier,
        *,
        expected_version: int,
        idempotency_key: Identifier,
        actor_id: Identifier,
        answers: Mapping[str, str],
    ) -> AgentRun:
        fingerprint = _fingerprint({
            "op": "submit-clarification", "project_id": project_id.root, "run_id": run_id.root,
            "expected_version": expected_version, "actor_id": actor_id.root,
            "answers": dict(sorted(answers.items())),
        })
        replay = await self.repository.read_idempotent_result(run_id, idempotency_key, fingerprint)
        if replay is not None:
            return replay
        lease = await self._claim(run_id)
        try:
            run = await self._read_project_run(project_id, run_id)
            if run.version.root != expected_version:
                raise self._revision_conflict(idempotency_key)
            if run.status is not RunStatus.paused:
                raise DomainError(ErrorCode.REVISION_CONFLICT, "Clarification answers are accepted only while the run is paused for clarification.", request_id=idempotency_key.root)
            state = await self._state(run_id)
            record = state.get("clarification_record")
            if not state.get("pending_clarification") or not isinstance(record, Mapping) or not record.get("is_pending"):
                raise DomainError(ErrorCode.REVISION_CONFLICT, "The run has no pending clarification question.", request_id=idempotency_key.root)
            questions = [ClarificationQuestion.model_validate(item) for item in record.get("questions", [])]
            expected_ids = {item.question_id for item in questions}
            if set(answers) != expected_ids or any(not isinstance(value, str) or not value.strip() for value in answers.values()):
                raise DomainError(ErrorCode.VALIDATION_FAILED, "Answer every pending clarification exactly once with non-empty text.", request_id=idempotency_key.root)
            saved_answers = {key: value.strip() for key, value in answers.items()}
            state["clarification_record"] = {
                "questions": [item.model_dump(mode="json") for item in questions],
                "answers": saved_answers,
                "is_pending": False,
            }
            state["last_clarification"] = state["clarification_record"]
            state["clarification_answers"] = {**state.get("clarification_answers", {}), **saved_answers}
            state.pop("pending_clarification", None)
            self._clear_plan(state)
            state["resume_status"] = RunStatus.running.value
            resumed = self._next_run(run, status=RunStatus.running, current_stage=run.current_stage, paused_reason=None)
            event = make_event(run_id, resumed.version.root, "clarification.answered", "Authorized answers were persisted with the run transition; orchestration can continue from the same goal.", stage=run.current_stage)
            return await self.repository.commit_transition(
                run_id, run.version.root, resumed, [event], {}, lease=lease,
                idempotency_key=idempotency_key, request_fingerprint=fingerprint, runtime_state=state,
            )
        finally:
            await self.repository.release_lease(lease)

    async def cancel_run(
        self,
        project_id: Identifier,
        run_id: Identifier,
        *,
        expected_version: int,
        idempotency_key: Identifier,
    ) -> AgentRun:
        fingerprint = _fingerprint({"op": "cancel", "run_id": run_id.root, "expected_version": expected_version})
        replay = await self.repository.read_idempotent_result(run_id, idempotency_key, fingerprint)
        if replay is not None:
            return replay
        lease = await self._claim(run_id)
        try:
            run = await self._read_project_run(project_id, run_id)
            if run.version.root != expected_version:
                raise self._revision_conflict(idempotency_key)
            if run.status not in MUTATION_ALLOWED_STATES["cancel"]:
                raise DomainError(ErrorCode.REVISION_CONFLICT, "The run is already terminal.", request_id=idempotency_key.root)
            cancelled = self._next_run(run, status=RunStatus.cancelled, current_stage=run.current_stage, paused_reason=None)
            event = make_event(run_id, cancelled.version.root, "run.cancelled", "Run cancelled; no further stage dispatch is allowed.", stage=run.current_stage)
            return await self.repository.commit_transition(run_id, run.version.root, cancelled, [event], {}, lease=lease, idempotency_key=idempotency_key, request_fingerprint=fingerprint)
        finally:
            await self.repository.release_lease(lease)

    async def apply_configuration(
        self,
        project_id: Identifier,
        run_id: Identifier,
        *,
        expected_version: int,
        idempotency_key: Identifier,
        profile: ConfigSnapshot,
    ) -> AgentRun:
        fingerprint = _fingerprint({"op": "apply-config", "run_id": run_id.root, "expected_version": expected_version, "profile": profile.model_dump(mode="json")})
        replay = await self.repository.read_idempotent_result(run_id, idempotency_key, fingerprint)
        if replay is not None:
            return replay
        profile = self.policy.resolve_config(profile)
        lease = await self._claim(run_id)
        try:
            run = await self._read_project_run(project_id, run_id)
            if run.version.root != expected_version:
                raise self._revision_conflict(idempotency_key)
            if run.status not in MUTATION_ALLOWED_STATES["apply_configuration"]:
                raise DomainError(ErrorCode.REVISION_CONFLICT, "Configuration can only be explicitly applied to a paused run.", request_id=idempotency_key.root)
            state = await self._state(run_id)
            unresolved = await self.repository.incomplete_invocations(run_id)
            if unresolved:
                raise DomainError(ErrorCode.OUTCOME_UNKNOWN, "Configuration cannot supersede an invocation with an unresolved provider outcome.", request_id=idempotency_key.root)
            current = self._active_config(state)
            if profile.config_snapshot_id.root == run.config_snapshot_id.root and profile != current:
                raise DomainError(ErrorCode.VALIDATION_FAILED, "A changed configuration must use a new immutable snapshot id.", request_id=idempotency_key.root)
            scoring_changed = (
                current.scoring_rule_version.root != profile.scoring_rule_version.root
                or Decimal(str(current.pass_score)) != Decimal(str(profile.pass_score))
                or current.stage_score_weights != profile.stage_score_weights
            )
            generation_changed = (
                current.model_ref != profile.model_ref
                or current.prompt_refs != profile.prompt_refs
            )
            next_run = self._next_run(run, config_snapshot_id=profile.config_snapshot_id)
            applied_payload = profile.model_dump(mode="json")
            applied_payload["effective_from_version"] = next_run.version.root
            applied = ConfigSnapshot.model_validate(applied_payload)
            state["config_snapshot_id"] = applied.config_snapshot_id.root
            state["config_snapshot"] = applied.model_dump(mode="json")
            state["mode"] = applied.mode.value
            self._clear_plan(state)
            state.pop("blocked_plan_id", None)
            state.pop("blocked_operation", None)
            events = [make_event(run_id, next_run.version.root, "config.applied", "A validated immutable configuration snapshot was explicitly applied while paused.", stage=run.current_stage)]
            attempts_to_update: list[StageAttempt] = []
            stale_refs: list[Identifier] = []
            if scoring_changed or generation_changed:
                all_attempts = await self.repository.get_attempts(run_id)
                generated_versions = {
                    item.output_artifact_version_id.root
                    for item in all_attempts
                    if item.output_artifact_version_id is not None and item.stage.value in STAGE_INDEX
                }
                generated_versions.update(state.get("current_artifacts_by_stage", {}).values())
                generated_versions.update(state.get("released_versions", []))
                stored_artifacts = {item.version_id.root: item for item in await self.artifacts.list_run_artifacts(run_id)}
                stale_refs = [
                    stored_artifacts[version].version_id
                    for version in sorted(generated_versions)
                    if version in stored_artifacts and stored_artifacts[version].lifecycle is ArtifactLifecycle.valid
                ]
                for item in all_attempts:
                    if item.status in {AttemptStatus.queued, AttemptStatus.running, AttemptStatus.waiting_review, AttemptStatus.paused}:
                        attempts_to_update.append(item.model_copy(update={"status": AttemptStatus.cancelled}))
                state["current_artifacts_by_stage"] = {}
                state["released_versions"] = []
                state.pop("pending_review", None)
                state.pop("human_revision_feedback", None)
                state.pop("human_revision_feedback_stage", None)
                state["resume_status"] = RunStatus.running.value
                state.pop("pending_attempt_id", None)
                state.pop("pending_attempt_fingerprint", None)
                for key in ("pending_plan_id", "pending_plan_input", "pending_plan_scope_hash", "pending_plan_proposal", "pending_plan_fingerprint", "blocked_plan_id", "blocked_operation"):
                    state.pop(key, None)
                next_run = self._next_run(run, status=RunStatus.paused, current_stage=StageId.brief, paused_reason=run.paused_reason, config_snapshot_id=profile.config_snapshot_id)
                applied_payload["effective_from_version"] = next_run.version.root
                applied = ConfigSnapshot.model_validate(applied_payload)
                state["config_snapshot_id"] = applied.config_snapshot_id.root
                state["config_snapshot"] = applied.model_dump(mode="json")
                events.append(make_event(run_id, next_run.version.root, "config.revalidation.required", "Scoring or generation rules changed; old artifacts were staled and work restarts at brief under the new snapshot.", stage=StageId.brief, artifact_version_ids=stale_refs))
            return await self.repository.commit_transition(
                run_id, run.version.root, next_run, events, {}, lease=lease,
                idempotency_key=idempotency_key, request_fingerprint=fingerprint,
                runtime_state=state, attempts=attempts_to_update, stale_version_ids=stale_refs,
            )
        finally:
            await self.repository.release_lease(lease)

    async def export_approved_artifact(self, run_id: Identifier, review_id: Identifier, artifact_version_id: Identifier) -> bytes:
        state = await self._state(run_id)
        reviews = await self.repository.get_reviews(run_id)
        review = next((item for item in reversed(reviews) if item.review_id.root == review_id.root and item.artifact.version_id.root == artifact_version_id.root), None)
        if review is None or not review.program_validation_passed:
            raise DomainError(ErrorCode.ARTIFACT_STALE, "Only a current program-valid reviewed artifact can be exported.", request_id=run_id.root)
        current_artifact = await self._matching_artifact(review)
        if current_artifact is None:
            raise DomainError(ErrorCode.ARTIFACT_STALE, "The reviewed artifact lifecycle or revision is stale.", request_id=run_id.root)
        current = self._active_config(state)
        is_current = artifact_version_id.root in state.get("current_artifacts_by_stage", {}).values()
        exact_score = Decimal(review.overall_score_decimal or str(review.overall_score))
        if self.policy.registered_rule_version is not None and review.rule_version.root != current.scoring_rule_version.root:
            raise DomainError(ErrorCode.ARTIFACT_STALE, "The review used a scoring rule that is not current for this run.", request_id=run_id.root)
        if not is_current:
            raise DomainError(ErrorCode.ARTIFACT_STALE, "The reviewed artifact is historical or no longer current.", request_id=run_id.root)
        if state.get("mode") == "automatic":
            if review.conclusion is not ReviewConclusion.pass_ or exact_score < Decimal(str(current.pass_score)) or artifact_version_id.root not in state.get("released_versions", []):
                raise DomainError(ErrorCode.MANUAL_REVIEW_REQUIRED, "The exact artifact version has not passed its current automatic release gate.", request_id=run_id.root)
        elif not await self.repository.is_approved(run_id, review_id, artifact_version_id):
            raise DomainError(ErrorCode.MANUAL_REVIEW_REQUIRED, "The exact review and artifact version do not have an effective approval.", request_id=run_id.root)
        content = await self.artifacts.read_content(review.artifact.artifact_id, review.artifact.version_id)
        if content is None:
            raise DomainError(ErrorCode.ARTIFACT_STALE, "The artifact body is no longer available.", request_id=run_id.root)
        return content.encode("utf-8")

    async def export_reviewed_artifact(
        self,
        run_id: Identifier,
        review_id: Identifier,
        artifact_version_id: Identifier,
        format: TextExportFormat,
    ) -> ExportPayload:
        """Build a domain export only after the runtime proves exact release authority."""
        if self.artifact_exporter is None:
            raise DomainError(ErrorCode.CAPABILITY_UNSUPPORTED, "No business export capability is registered.", request_id=run_id.root)
        evidence = await self._resolve_released_review_evidence(run_id, review_id, artifact_version_id)
        payload = await self.artifact_exporter.export(
            evidence.artifact,
            evidence.content,
            evidence.review,
            is_current=True,
            release=True,
            approval=evidence.approval,
            format=format,
        )
        if payload.artifact_version_id.root != artifact_version_id.root or payload.review_id.root != review_id.root:
            raise DomainError(ErrorCode.VALIDATION_FAILED, "The export capability returned a payload bound to a different artifact or review.", request_id=run_id.root)
        return payload

    async def export_formats_for_review(
        self,
        run_id: Identifier,
        review_id: Identifier,
        artifact_version_id: Identifier,
    ) -> tuple[TextExportFormat, ...]:
        """Report formats without invoking the serializer or weakening its gate."""
        if self.artifact_exporter is None:
            return ()
        from gw.agent_episode.exports import (
            ExportRejected,
            parse_episode_text_artifact,
            supported_export_formats,
        )

        try:
            evidence = await self._resolve_released_review_evidence(run_id, review_id, artifact_version_id)
        except DomainError:
            return ()

        matching_attempts = [
            attempt
            for attempt in await self.repository.get_attempts(run_id)
            if attempt.run_id.root == run_id.root
            and attempt.status is AttemptStatus.succeeded
            and attempt.review_id is not None
            and attempt.review_id.root == review_id.root
            and attempt.output_artifact_version_id is not None
            and attempt.output_artifact_version_id.root == artifact_version_id.root
        ]
        if len(matching_attempts) != 1:
            return ()
        try:
            stage, _ = parse_episode_text_artifact(
                evidence.content,
                expected_stage=matching_attempts[0].stage,
            )
        except ExportRejected:
            return ()
        return supported_export_formats(stage)

    async def _resolve_released_review_evidence(
        self,
        run_id: Identifier,
        review_id: Identifier,
        artifact_version_id: Identifier,
    ) -> _ReleasedReviewEvidence:
        """Resolve the shared exact-version gate used by export and capability reads."""
        reviews = await self.repository.get_reviews(run_id)
        review = next((
            item for item in reversed(reviews)
            if item.run_id.root == run_id.root
            and item.review_id.root == review_id.root
            and item.artifact.version_id.root == artifact_version_id.root
        ), None)
        if review is None:
            raise DomainError(ErrorCode.ARTIFACT_STALE, "The requested review does not bind to this artifact version.", request_id=run_id.root)
        if not review.program_validation_passed or review.overall_score_decimal is None:
            raise DomainError(ErrorCode.ARTIFACT_STALE, "Only a program-valid review with an exact persisted score can be exported.", request_id=run_id.root)
        try:
            exact_score = Decimal(review.overall_score_decimal)
        except (InvalidOperation, ValueError, TypeError) as exc:
            raise DomainError(ErrorCode.ARTIFACT_STALE, "The exact persisted review score is invalid.", request_id=run_id.root) from exc
        if not exact_score.is_finite() or exact_score < 0 or exact_score > 10:
            raise DomainError(ErrorCode.ARTIFACT_STALE, "The exact persisted review score is outside the supported range.", request_id=run_id.root)

        current_artifact = await self._matching_artifact(review)
        if current_artifact is None:
            raise DomainError(ErrorCode.ARTIFACT_STALE, "The reviewed artifact lifecycle or revision is stale.", request_id=run_id.root)
        state = await self._state(run_id)
        is_current = artifact_version_id.root in state.get("current_artifacts_by_stage", {}).values()
        approval = await self.repository.get_approval_for_artifact(run_id, review_id, artifact_version_id)
        active_config = self._active_config(state)
        if self.policy.registered_rule_version is not None and review.rule_version.root != active_config.scoring_rule_version.root:
            raise DomainError(ErrorCode.ARTIFACT_STALE, "The review used a scoring rule that is not current for this run.", request_id=run_id.root)
        if not is_current:
            raise DomainError(ErrorCode.ARTIFACT_STALE, "The reviewed artifact is historical or no longer current.", request_id=run_id.root)

        approval_matches = bool(
            approval is not None
            and approval.run_id.root == run_id.root
            and approval.review_id.root == review_id.root
            and approval.artifact_version_id.root == artifact_version_id.root
            and approval.decision.value in {"approve", "override"}
        )
        if state.get("mode") == "automatic":
            released = (
                artifact_version_id.root in state.get("released_versions", [])
                and review.conclusion is ReviewConclusion.pass_
                and exact_score >= Decimal(str(active_config.pass_score))
            )
            approval = None
        else:
            released = state.get("mode") == "approval" and approval_matches
        if not released:
            raise DomainError(ErrorCode.MANUAL_REVIEW_REQUIRED, "The exact review and artifact version have not been released.", request_id=run_id.root)

        content = await self.artifacts.read_content(current_artifact.artifact_id, current_artifact.version_id)
        if content is None or hashlib.sha256(content.encode("utf-8")).hexdigest() != current_artifact.content_hash.root:
            raise DomainError(ErrorCode.ARTIFACT_STALE, "The exact artifact body is unavailable or failed its immutable hash check.", request_id=run_id.root)
        return _ReleasedReviewEvidence(
            review=review,
            artifact=current_artifact,
            approval=approval,
            content=content,
        )

    async def list_events(self, run_id: Identifier, cursor: Identifier | None = None, limit: int = 100):
        return await self.repository.read_events(run_id, cursor, limit)

    async def _matching_artifact(self, review) -> ArtifactRef | None:
        """Resolve persisted lifecycle/revision, not the immutable review-time snapshot alone."""
        snapshot = review.artifact
        stored = await self.artifacts.get_version(snapshot.artifact_id, snapshot.version_id)
        if stored is None or stored.lifecycle is not ArtifactLifecycle.valid:
            return None
        if stored.revision.root != snapshot.revision.root or stored.content_hash.root != snapshot.content_hash.root:
            return None
        return stored

    async def _has_current_pending_review(self, run: AgentRun, state: Mapping[str, Any]) -> bool:
        """Only restore waiting_review when its exact review, attempt, and artifact remain durable."""
        pending = state.get("pending_review")
        if run.current_stage is None or not isinstance(pending, Mapping):
            return False
        if pending.get("stage") != run.current_stage.value:
            return False
        review_id = pending.get("review_id")
        artifact_version_id = pending.get("artifact_version_id")
        attempt_id = pending.get("stage_attempt_id")
        if not all(isinstance(value, str) and value for value in (review_id, artifact_version_id, attempt_id)):
            return False
        attempts = await self.repository.get_attempts(run.run_id)
        attempt = next((item for item in attempts if item.stage_attempt_id.root == attempt_id), None)
        if (
            attempt is None
            or attempt.stage is not run.current_stage
            or attempt.status is not AttemptStatus.succeeded
            or attempt.review_id is None
            or attempt.review_id.root != review_id
            or attempt.output_artifact_version_id is None
            or attempt.output_artifact_version_id.root != artifact_version_id
        ):
            return False
        reviews = await self.repository.get_reviews(run.run_id)
        review = next((item for item in reversed(reviews) if item.review_id.root == review_id), None)
        if review is None or review.artifact.version_id.root != artifact_version_id:
            return False
        return await self._matching_artifact(review) is not None

    async def _pause_unknown(
        self,
        run: AgentRun,
        state: dict[str, Any],
        lease: LeaseGrant,
        pending: Sequence[InvocationRecord],
        idempotency_key: Identifier,
        fingerprint: str,
    ) -> AgentRun:
        paused = self._next_run(run, status=RunStatus.paused, current_stage=run.current_stage, paused_reason="Invocation result unknown; reconcile the provider before any retry.")
        state["resume_status"] = run.status.value
        state["dispatch_unknown"] = sorted({record.invocation_id.root for record in pending})
        event = make_event(run.run_id, paused.version.root, "run.paused.unknown_outcome", "A sent call has no durable result; no duplicate dispatch was attempted.", stage=run.current_stage)
        return await self.repository.commit_transition(run.run_id, run.version.root, paused, [event], {}, lease=lease, idempotency_key=idempotency_key, request_fingerprint=fingerprint, runtime_state=state)

    async def _claim(self, run_id: Identifier) -> LeaseGrant:
        lease = await self.repository.claim_lease(run_id, self.owner_id, self.lease_seconds)
        if lease is None:
            raise DomainError(ErrorCode.REVISION_CONFLICT, "Another runtime worker currently owns the run lease.", request_id=run_id.root)
        return lease

    async def _read_project_run(self, project_id: Identifier, run_id: Identifier) -> AgentRun:
        run = await self.repository.read_run(project_id, run_id)
        if run is None:
            raise DomainError(ErrorCode.PROJECT_UNAVAILABLE, "The run is unavailable in the requested project.", request_id=run_id.root)
        return run

    async def _state(self, run_id: Identifier) -> dict[str, Any]:
        state = await self.repository.read_runtime_state(run_id)
        if state is None:
            raise DomainError(ErrorCode.PROJECT_UNAVAILABLE, "Runtime state is unavailable.", request_id=run_id.root)
        return state

    @staticmethod
    def _revision_conflict(request_id: Identifier) -> DomainError:
        return DomainError(ErrorCode.REVISION_CONFLICT, "The run version changed; reload state before deciding again.", request_id=request_id.root)

    @staticmethod
    def _next_run(
        run: AgentRun,
        *,
        status: RunStatus | object = _KEEP,
        current_stage: StageId | None | object = _KEEP,
        paused_reason: str | None | object = _KEEP,
        config_snapshot_id: Identifier | object = _KEEP,
    ) -> AgentRun:
        return run.model_copy(update={
            "status": run.status if status is _KEEP else status,
            "current_stage": run.current_stage if current_stage is _KEEP else current_stage,
            "version": run.version.__class__(run.version.root + 1),
            "paused_reason": run.paused_reason if paused_reason is _KEEP else (PausedReason(paused_reason) if isinstance(paused_reason, str) else paused_reason),
            "config_snapshot_id": run.config_snapshot_id if config_snapshot_id is _KEEP else config_snapshot_id,
            "updated_at": UtcDateTime(utc_now()),
        })

    @staticmethod
    def _input_version_ids(state: Mapping[str, Any]) -> list[Identifier]:
        ids: list[Identifier] = []
        seen: set[str] = set()
        for item in state.get("initial_artifact_refs", []):
            version = item.get("version_id") if isinstance(item, Mapping) else None
            if isinstance(version, str) and version not in seen:
                ids.append(Identifier(version)); seen.add(version)
        current = state.get("current_artifacts_by_stage", {})
        if isinstance(current, Mapping):
            for stage in STAGE_ORDER:
                version = current.get(stage.value)
                if isinstance(version, str) and version not in seen:
                    ids.append(Identifier(version)); seen.add(version)
        return ids

    def _input_version_ids_before(self, state: Mapping[str, Any], target: StageId) -> list[Identifier]:
        before = dict(state)
        current = state.get("current_artifacts_by_stage", {})
        if isinstance(current, Mapping):
            before["current_artifacts_by_stage"] = {
                stage: version for stage, version in current.items()
                if stage in STAGE_INDEX and STAGE_INDEX[stage] < STAGE_INDEX[target.value]
            }
        return self._input_version_ids(before)

    @staticmethod
    def _pending_attempt(attempts: Sequence[StageAttempt], state: Mapping[str, Any], stage: StageId) -> StageAttempt | None:
        pending_id = state.get("pending_attempt_id")
        if isinstance(pending_id, str):
            candidate = next((item for item in attempts if item.stage_attempt_id.root == pending_id), None)
            if candidate is not None:
                return candidate
        return next((item for item in reversed(attempts) if item.stage is stage and item.status in {AttemptStatus.queued, AttemptStatus.running}), None)

    @staticmethod
    def _queued_attempt(
        run_id: Identifier,
        stage: StageId,
        attempts: Sequence[StageAttempt],
        state: Mapping[str, Any],
        *,
        kind: AttemptKind,
        input_ids: Sequence[Identifier],
    ) -> StageAttempt:
        same_stage = [item for item in attempts if item.stage is stage]
        return StageAttempt(
            schema_version=1,
            stage_attempt_id=uuid.uuid4().hex,
            run_id=run_id,
            stage=stage,
            kind=kind,
            attempt_number=len(same_stage) + 1,
            input_artifact_version_ids=list(input_ids),
            status=AttemptStatus.queued,
            invocation_ids=[],
            output_artifact_version_id=None,
            review_id=None,
            created_at=utc_now(),
        )
