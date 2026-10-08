"""Host-owned AgentRun application facade and project authorization adapter."""
from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from typing import Any

from gw.agent_episode.exports import ExportFormatMismatch, supported_export_formats
from gw.core.auth import AuthContext
from gw.core.errors import CleanroomException
from gw.projects_hub.service import ProjectsService, owner_key_for_context

from .errors import DomainError
from .models import (
    AgentRun,
    ApprovalDecision,
    ApprovalRecord,
    ArtifactLifecycle,
    ArtifactRef,
    ApplyConfigRequest,
    ClarificationAnswerRequest,
    ClarificationRead,
    ConfigSnapshot,
    CreateRunRequest,
    ErrorCode,
    EventPage,
    Identifier,
    RequestContext,
    ReviewCandidateRead,
    ReviewDecisionRequest,
    RollbackRequest,
    StageId,
    TextExportFormat,
    VersionedMutation,
)
from .ports import ExportPayload


def encode_auth_context(auth: AuthContext) -> Identifier:
    """Carry trusted role/domain in the opaque authorization_version field."""
    return Identifier(json.dumps({
        "identity_domain": auth.identity_domain,
        "role": auth.role,
    }, ensure_ascii=True, sort_keys=True, separators=(",", ":")))


def decode_auth_context(context: RequestContext) -> AuthContext:
    try:
        raw = json.loads(context.actor.authorization_version.root)
        role = raw["role"]
        domain = raw["identity_domain"]
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise DomainError(ErrorCode.NOT_AUTHORIZED, "The trusted actor context is invalid.", request_id=context.request_id.root) from exc
    mode = "oidc" if isinstance(domain, str) and domain.startswith("oidc:") else "local_account"
    return AuthContext(role=role, subject=context.actor.actor_id.root, mode=mode, identity_domain=domain)


class HostIdentityProjectAdapter:
    """Resolve project ownership from the host's current account identity."""

    _ARCHIVED_READ_ACTIONS = {"agent.run.read", "agent.run.export"}

    def __init__(self, projects: ProjectsService) -> None:
        self.projects = projects

    async def resolve_context(self, credential_ref: Identifier) -> RequestContext:
        raise DomainError(ErrorCode.NOT_AUTHORIZED, "Agent requests require the host's authenticated Cookie session.", request_id=credential_ref.root)

    async def authorize(
        self, context: RequestContext, action: Identifier, resource_refs: Sequence[Identifier],
    ) -> bool:
        if not action.root.startswith("agent."):
            return False
        if not await self.project_is_available(context):
            return False
        auth = decode_auth_context(context)
        try:
            project = self.projects.get_owned_project(
                context.project_id.root,
                owner_key_for_context(auth),
            )
        except CleanroomException:
            return False
        if project.archived_at is not None:
            return action.root in self._ARCHIVED_READ_ACTIONS
        return True

    async def project_is_available(self, context: RequestContext) -> bool:
        auth = decode_auth_context(context)
        try:
            project = self.projects.get_owned_project(
                context.project_id.root,
                owner_key_for_context(auth),
            )
        except CleanroomException:
            return False
        return project.deleted_at is None


class RuntimeAgentUseCase:
    """Application facade that binds host project authorization to runtime ports."""

    def __init__(
        self,
        service: Any,
        identity: HostIdentityProjectAdapter,
        profiles: dict[str, ConfigSnapshot],
    ) -> None:
        self.service = service
        self.identity = identity
        self.profiles = profiles

    async def create_run(self, context: RequestContext, request: CreateRunRequest) -> AgentRun:
        if request.project_id != context.project_id:
            raise DomainError(ErrorCode.NOT_AUTHORIZED, "The requested project is not available in the authenticated context.", request_id=request.request_id.root)
        await self._authorize(context, "agent.run.create", [context.project_id])
        return await self.service.create_run(request)

    async def get_run(self, context: RequestContext, run_id: Identifier) -> AgentRun | None:
        return await self._find_authorized_run(context, run_id, "agent.run.read")

    async def list_events(self, context: RequestContext, run_id: Identifier, cursor: Identifier | None, limit: int) -> EventPage:
        if await self._find_authorized_run(context, run_id, "agent.run.read") is None:
            raise self._not_found(context.request_id.root)
        return await self.service.list_events(run_id, cursor, limit)

    async def pause_run(self, context: RequestContext, run_id: Identifier, mutation: VersionedMutation) -> AgentRun:
        run = await self._required_run(context, run_id, "agent.run.pause", mutation.request_id.root)
        return await self.service.pause_run(run.project_id, run_id, expected_version=mutation.expected_version.root, idempotency_key=mutation.idempotency_key)

    async def resume_run(self, context: RequestContext, run_id: Identifier, mutation: VersionedMutation) -> AgentRun:
        run = await self._required_run(context, run_id, "agent.run.resume", mutation.request_id.root)
        return await self.service.resume_run(run.project_id, run_id, expected_version=mutation.expected_version.root, idempotency_key=mutation.idempotency_key)

    async def cancel_run(self, context: RequestContext, run_id: Identifier, mutation: VersionedMutation) -> AgentRun:
        run = await self._required_run(context, run_id, "agent.run.cancel", mutation.request_id.root)
        return await self.service.cancel_run(run.project_id, run_id, expected_version=mutation.expected_version.root, idempotency_key=mutation.idempotency_key)

    async def submit_review_decision(self, context: RequestContext, request: ReviewDecisionRequest) -> ApprovalRecord | AgentRun:
        await self._required_run(context, request.run_id, "agent.review.decide", request.request_id.root)
        if request.decision is ApprovalDecision.override:
            await self._authorize(context, "agent.review.override", [context.project_id, request.run_id])
        return await self.service.submit_review_decision(request, actor_id=context.actor.actor_id, pause_on_exhaustion=True)

    async def apply_configuration(self, context: RequestContext, run_id: Identifier, mutation: VersionedMutation, profile_version: Identifier) -> AgentRun:
        run = await self._required_run(context, run_id, "agent.run.configure", mutation.request_id.root)
        profile = self.profiles.get(profile_version.root)
        if profile is None:
            raise DomainError(ErrorCode.CAPABILITY_UNSUPPORTED, "The immutable configuration snapshot is unavailable.", request_id=mutation.request_id.root)
        return await self.service.apply_configuration(run.project_id, run_id, expected_version=mutation.expected_version.root, idempotency_key=mutation.idempotency_key, profile=profile)

    async def list_artifacts(self, context: RequestContext, run_id: Identifier, cursor: Identifier | None, limit: int) -> tuple[Sequence[ArtifactRef], Identifier | None]:
        run = await self._required_run(context, run_id, "agent.run.read", context.request_id.root)
        values = await self.service.artifacts.list_run_artifacts(run.run_id)
        start = 0
        if cursor is not None:
            found = next((index for index, item in enumerate(values) if item.version_id == cursor), None)
            if found is None:
                raise DomainError(ErrorCode.VALIDATION_FAILED, "The artifact cursor is not part of this run.", request_id=context.request_id.root)
            start = found + 1
        page = list(values[start:start + limit])
        next_cursor = Identifier(page[-1].version_id.root) if page and start + len(page) < len(values) else None
        return page, next_cursor

    async def export_approved_artifact(self, context: RequestContext, run_id: Identifier, artifact_version_id: Identifier) -> bytes:
        await self._required_run(context, run_id, "agent.run.export", context.request_id.root)
        review = await self.service.repository.latest_review_for_artifact(run_id, artifact_version_id)
        if review is None:
            raise DomainError(ErrorCode.MANUAL_REVIEW_REQUIRED, "The exact artifact version has no current review.", request_id=context.request_id.root)
        return await self.service.export_approved_artifact(run_id, review.review_id, artifact_version_id)

    async def export_episode(self, context: RequestContext, run_id: Identifier, review_id: Identifier, artifact_version_id: Identifier, format: TextExportFormat) -> ExportPayload:
        await self._required_run(context, run_id, "agent.run.export", context.request_id.root)
        try:
            return await self.service.export_reviewed_artifact(run_id, review_id, artifact_version_id, format)
        except ExportFormatMismatch as exc:
            raise DomainError(ErrorCode.VALIDATION_FAILED, str(exc), request_id=context.request_id.root) from exc

    async def export_default_payload(self, context: RequestContext, run_id: Identifier, artifact_version_id: Identifier) -> ExportPayload | None:
        await self._required_run(context, run_id, "agent.run.export", context.request_id.root)
        review = await self.service.repository.latest_review_for_artifact(run_id, artifact_version_id)
        if review is None:
            raise DomainError(ErrorCode.MANUAL_REVIEW_REQUIRED, "The exact artifact version has no current review.", request_id=context.request_id.root)
        content = await self.service.artifacts.read_content(review.artifact.artifact_id, artifact_version_id)
        if content is None or hashlib.sha256(content.encode("utf-8")).hexdigest() != review.artifact.content_hash.root:
            raise DomainError(ErrorCode.ARTIFACT_STALE, "The exact artifact body is unavailable or failed its immutable hash check.", request_id=context.request_id.root)
        stage = self._review_stage(content)
        try:
            parsed_stage = StageId(stage)
        except ValueError:
            return None
        supported = supported_export_formats(parsed_stage)
        if not supported:
            return None
        return await self.service.export_reviewed_artifact(run_id, review.review_id, artifact_version_id, supported[0])

    async def read_review_candidate(self, context: RequestContext, run_id: Identifier, review_id: Identifier, artifact_version_id: Identifier) -> ReviewCandidateRead:
        run = await self._required_run(context, run_id, "agent.run.read", context.request_id.root)
        reviews = await self.service.repository.get_reviews(run_id)
        review = next((item for item in reversed(reviews) if item.review_id == review_id and item.artifact.version_id == artifact_version_id), None)
        if review is None:
            raise DomainError(ErrorCode.ARTIFACT_STALE, "The exact review and artifact version are unavailable.", request_id=context.request_id.root)
        stored = await self.service.artifacts.get_version(review.artifact.artifact_id, artifact_version_id)
        if stored is None or stored.artifact_id != review.artifact.artifact_id or stored.content_hash != review.artifact.content_hash or stored.lifecycle is ArtifactLifecycle.trashed:
            raise DomainError(ErrorCode.ARTIFACT_STALE, "The exact reviewed artifact body is unavailable.", request_id=context.request_id.root)
        content = await self.service.artifacts.read_content(review.artifact.artifact_id, artifact_version_id)
        if content is None or hashlib.sha256(content.encode("utf-8")).hexdigest() != review.artifact.content_hash.root:
            raise DomainError(ErrorCode.ARTIFACT_STALE, "The exact reviewed artifact body failed its immutable hash check.", request_id=context.request_id.root)
        state = await self.service.repository.read_runtime_state(run_id) or {}
        pending = state.get("pending_review")
        pending_match = isinstance(pending, Mapping) and pending.get("review_id") == review_id.root and pending.get("artifact_version_id") == artifact_version_id.root
        latest = state.get("current_artifacts_by_stage", {}).get(self._review_stage(content))
        is_current = stored.lifecycle is ArtifactLifecycle.valid and (latest == artifact_version_id.root or pending_match)
        stale_reason = "artifact_version_stale" if stored.lifecycle is ArtifactLifecycle.stale else None if is_current else "superseded_or_historical"
        can_decide = bool(is_current and pending_match and run.status.value == "waiting_review" and review.program_validation_passed)
        return ReviewCandidateRead(review=review, artifact=review.artifact, content=content, is_current=is_current, stale_reason=stale_reason, can_decide=can_decide)

    @staticmethod
    def _review_stage(content: str) -> str:
        try:
            stage = json.loads(content).get("stage")
            return stage if isinstance(stage, str) else ""
        except (ValueError, TypeError):
            return ""

    async def read_clarifications(self, context: RequestContext, run_id: Identifier) -> ClarificationRead:
        await self._required_run(context, run_id, "agent.run.read", context.request_id.root)
        return await self.service.read_clarifications(run_id)

    async def submit_clarification(self, context: RequestContext, run_id: Identifier, request: ClarificationAnswerRequest) -> AgentRun:
        run = await self._required_run(context, run_id, "agent.run.clarify", request.request_id.root)
        return await self.service.submit_clarification(run.project_id, run_id, expected_version=request.expected_version.root, idempotency_key=request.idempotency_key, actor_id=context.actor.actor_id, answers=request.answers)

    async def request_rollback(self, context: RequestContext, run_id: Identifier, *, target_stage: StageId, mutation: VersionedMutation) -> AgentRun:
        run = await self._required_run(context, run_id, "agent.run.rollback", mutation.request_id.root)
        return await self.service.request_rollback(run.project_id, run_id, target_stage=target_stage, expected_version=mutation.expected_version.root, idempotency_key=mutation.idempotency_key, pause_on_exhaustion=True)

    async def _find_authorized_run(self, context: RequestContext, run_id: Identifier, action: str) -> AgentRun | None:
        run = await self.service.repository.read_run_by_id(run_id)
        if run is None or run.project_id != context.project_id:
            return None
        await self._authorize(context, action, [run.project_id, run.run_id])
        return run

    async def _required_run(self, context: RequestContext, run_id: Identifier, action: str, request_id: str) -> AgentRun:
        run = await self._find_authorized_run(context, run_id, action)
        if run is None:
            raise self._not_found(request_id)
        return run

    async def _authorize(self, context: RequestContext, action: str, resources: Sequence[Identifier]) -> None:
        if not await self.identity.project_is_available(context):
            raise self._not_found(context.request_id.root)
        if not await self.identity.authorize(context, Identifier(action), resources):
            raise DomainError(ErrorCode.NOT_AUTHORIZED, "The authenticated actor is not authorized for this action.", request_id=context.request_id.root)

    @staticmethod
    def _not_found(request_id: str) -> DomainError:
        return DomainError(ErrorCode.PROJECT_UNAVAILABLE, "The run is unavailable in the authenticated project.", request_id=request_id)


__all__ = ["HostIdentityProjectAdapter", "RuntimeAgentUseCase", "decode_auth_context", "encode_auth_context"]
