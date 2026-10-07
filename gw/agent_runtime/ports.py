"""Typed, side-effect-free module ports for the C00-C10 handoff contracts.

Concrete host, model, storage, and vector-store adapters are deliberately owned by
separate implementation tasks and must not be imported from this module.
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from .models import (
    AgentRun,
    ApprovalRecord,
    ArtifactRef,
    BudgetReservationRequest,
    BudgetSettlement,
    ArtifactVersionInput,
    BudgetDecision,
    ConfigSnapshot,
    ClarificationRead,
    ClarificationAnswerRequest,
    ConfigurationValidation,
    CreateRunRequest,
    EventPage,
    HostJob,
    HostTaskRequest,
    HostToolCapabilities,
    Identifier,
    InvocationRecord,
    KnowledgeSearchRequest,
    KnowledgeSource,
    LeaseGrant,
    KnowledgeSearchResult,
    ModelCapabilities,
    ModelInvocationRequest,
    ModelInvocationResult,
    PromptTemplate,
    PromptTemplateRef,
    RequestContext,
    ReviewDecisionRequest,
    ReviewRecord,
    ReviewCandidateRead,
    RunEvent,
    StageId,
    TextExportFormat,
    VersionedMutation,
)


@dataclass(frozen=True, slots=True)
class ExportPayload:
    """Binary text export with server-selected metadata, outside JSON persistence."""

    content: bytes
    media_type: str
    filename: str
    artifact_version_id: Identifier
    review_id: Identifier


class ArtifactExportPort(Protocol):
    """Injected domain serializer after the runtime verifies exact release evidence."""

    async def export(
        self, artifact: ArtifactRef, content: str, review: ReviewRecord, *,
        is_current: bool, release: bool, approval: ApprovalRecord | None,
        format: TextExportFormat,
    ) -> ExportPayload: ...


class IdentityProjectPort(Protocol):
    """Resolve a trusted context and re-check authorization at each boundary."""

    async def resolve_context(self, credential_ref: Identifier) -> RequestContext: ...

    async def authorize(
        self,
        context: RequestContext,
        action: Identifier,
        resource_refs: Sequence[Identifier],
    ) -> bool: ...

    async def project_is_available(self, context: RequestContext) -> bool: ...


class ModelGateway(Protocol):
    """Capability discovery and one invocation; no provider is implied."""

    async def describe_capabilities(self, model_ref: Identifier) -> ModelCapabilities: ...

    async def invoke(self, request: ModelInvocationRequest) -> ModelInvocationResult: ...


class ModelInvocationLookupPort(Protocol):
    """Optional capability, implemented only when the provider supports it."""

    async def lookup(self, invocation_id: Identifier) -> ModelInvocationResult | None: ...

    async def cancel(self, invocation_id: Identifier) -> bool: ...


class InvocationDispatcherPort(Protocol):
    """Durable model boundary; known results replay and unknown sends never resend."""

    async def invoke(
        self, run_id: Identifier, request: ModelInvocationRequest, *,
        reservation: BudgetReservationRequest | None = None,
        lease: LeaseGrant, max_retries: int = 2,
        before_send: Callable[[], Awaitable[LeaseGrant]] | None = None,
    ) -> ModelInvocationResult: ...

    async def reserve_format_repair(
        self, run_id: Identifier, operation_id: Identifier, repair_id: Identifier,
        *, lease: LeaseGrant,
    ) -> None: ...


class PromptRepository(Protocol):
    """Fetch immutable templates by pinned ID/version/hash."""

    async def get_template(
        self, template_id: Identifier, version: Identifier
    ) -> PromptTemplate | None: ...

    async def resolve_template_ref(
        self, project_id: Identifier, stage: StageId, purpose: Identifier
    ) -> PromptTemplateRef | None: ...


class ArtifactRepository(Protocol):
    """Store immutable versions and explicit source/parent references."""

    async def create_version(self, version: ArtifactVersionInput) -> ArtifactRef: ...

    async def get_version(
        self, artifact_id: Identifier, version_id: Identifier
    ) -> ArtifactRef | None: ...

    async def read_content(self, artifact_id: Identifier, version_id: Identifier) -> str | None: ...

    async def list_versions(
        self, artifact_id: Identifier, cursor: Identifier | None, limit: int
    ) -> tuple[Sequence[ArtifactRef], Identifier | None]: ...

    async def move_to_trash(self, artifact_id: Identifier, expected_revision: int) -> int: ...

    async def restore(self, artifact_id: Identifier, expected_revision: int) -> int: ...


class RunRepository(Protocol):
    """Authoritative run state, CAS transitions, counters, and recoverability."""

    async def create_run(self, request: CreateRunRequest) -> AgentRun: ...

    async def read_run(self, project_id: Identifier, run_id: Identifier) -> AgentRun | None: ...

    async def record_invocation(self, record: InvocationRecord) -> InvocationRecord: ...

    async def read_invocation_result(
        self, invocation_id: Identifier
    ) -> ModelInvocationResult | None: ...

    async def commit_transition(
        self,
        run_id: Identifier,
        expected_revision: int,
        next_run: AgentRun,
        events: Sequence[RunEvent],
        counter_changes: Mapping[str, int],
        *,
        lease: LeaseGrant,
    ) -> AgentRun: ...

    async def claim_lease(
        self, run_id: Identifier, owner_id: Identifier, lease_seconds: int
    ) -> LeaseGrant | None: ...

    async def renew_lease(
        self, lease: LeaseGrant, lease_seconds: int
    ) -> LeaseGrant | None: ...

    async def release_lease(self, lease: LeaseGrant) -> bool: ...

    async def scan_recoverable(
        self, cursor: Identifier | None, limit: int
    ) -> tuple[Sequence[AgentRun], Identifier | None]: ...


class CheckpointBackend(Protocol):
    """LangGraph checkpoint seam; checkpoint state is not the business record."""

    async def save(
        self,
        run_id: Identifier,
        checkpoint: Mapping[str, Any],
        *,
        lease: LeaseGrant,
    ) -> Identifier: ...

    async def load(self, checkpoint_ref: Identifier) -> Mapping[str, Any] | None: ...


class KnowledgeRetriever(Protocol):
    """Re-check trusted project/actor scope and report disabled/unavailable states."""

    async def search(
        self, context: RequestContext, request: KnowledgeSearchRequest
    ) -> KnowledgeSearchResult: ...

    async def get_source(
        self,
        context: RequestContext,
        project_id: Identifier,
        document_id: Identifier,
        document_version: Identifier,
        chunk_id: Identifier,
    ) -> KnowledgeSource | None: ...


class HostTaskGateway(Protocol):
    """External task mapping; never substitute run IDs for actual host task IDs."""

    async def describe_tool(self, tool_id: Identifier) -> HostToolCapabilities | None: ...

    async def submit(self, request: HostTaskRequest) -> HostJob: ...

    async def get_job(self, host_job_id: Identifier) -> HostJob | None: ...

    async def cancel_job(self, host_job_id: Identifier) -> HostJob | None: ...


class ConfigurationPort(Protocol):
    """Versioned profiles; applying one is an explicit paused-run CAS."""

    async def get_profile(self, profile_version: Identifier) -> ConfigSnapshot | None: ...

    async def validate_profile(self, profile: ConfigSnapshot) -> ConfigurationValidation: ...

    async def apply_to_paused_run(
        self, run_id: Identifier, expected_revision: int, profile_version: Identifier
    ) -> ConfigSnapshot: ...


class BudgetPort(Protocol):
    """Optional ledger seam; unknown amounts remain unknown, never zero-settled."""

    async def reserve(self, request: BudgetReservationRequest) -> BudgetDecision: ...

    async def settle(self, settlement: BudgetSettlement) -> BudgetDecision: ...

    async def release_unspent(self, operation_id: Identifier) -> BudgetDecision: ...


class AgentUseCasePort(Protocol):
    """Framework-neutral AgentRunAPI facade; no HTTP framework or app assembly."""

    async def create_run(self, context: RequestContext, request: CreateRunRequest) -> AgentRun: ...

    async def get_run(self, context: RequestContext, run_id: Identifier) -> AgentRun | None: ...

    async def list_events(
        self, context: RequestContext, run_id: Identifier, cursor: Identifier | None, limit: int
    ) -> EventPage: ...

    async def pause_run(
        self, context: RequestContext, run_id: Identifier, mutation: VersionedMutation
    ) -> AgentRun: ...

    async def resume_run(
        self, context: RequestContext, run_id: Identifier, mutation: VersionedMutation
    ) -> AgentRun: ...

    async def cancel_run(
        self, context: RequestContext, run_id: Identifier, mutation: VersionedMutation
    ) -> AgentRun: ...

    async def submit_review_decision(
        self, context: RequestContext, request: ReviewDecisionRequest
    ) -> ApprovalRecord: ...

    async def apply_configuration(
        self,
        context: RequestContext,
        run_id: Identifier,
        mutation: VersionedMutation,
        profile_version: Identifier,
    ) -> AgentRun: ...

    async def list_artifacts(
        self, context: RequestContext, run_id: Identifier, cursor: Identifier | None, limit: int
    ) -> tuple[Sequence[ArtifactRef], Identifier | None]: ...

    async def export_approved_artifact(
        self, context: RequestContext, run_id: Identifier, artifact_version_id: Identifier
    ) -> bytes: ...

    async def read_review_candidate(
        self, context: RequestContext, run_id: Identifier, review_id: Identifier,
        artifact_version_id: Identifier,
    ) -> ReviewCandidateRead: ...

    async def read_clarifications(
        self, context: RequestContext, run_id: Identifier,
    ) -> ClarificationRead: ...

    async def submit_clarification(
        self, context: RequestContext, run_id: Identifier,
        request: ClarificationAnswerRequest,
    ) -> AgentRun: ...

    async def export_episode(
        self, context: RequestContext, run_id: Identifier, review_id: Identifier,
        artifact_version_id: Identifier, format: TextExportFormat,
    ) -> ExportPayload: ...


class EventPort(Protocol):
    """Append ordered events and return cursor-based pages without truncation."""

    async def append(self, event: RunEvent) -> None: ...

    async def read_events(
        self,
        run_id: Identifier,
        after_cursor: Identifier | None,
        limit: int,
    ) -> EventPage: ...
