"""Framework-neutral create/review use case over the frozen shared ports.

This module owns one text-stage operation. It does not build a graph, manage run
state, leases, retries, counters, approval transitions, or persistence policy.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from decimal import Decimal
from hashlib import sha256
from typing import Any, Mapping
from uuid import uuid4

from pydantic import ValidationError

from gw.agent_runtime.errors import DomainError
from gw.agent_runtime.models import (
    ArtifactLifecycle,
    ArtifactRef,
    ArtifactSourceRef,
    ArtifactVersionInput,
    AttemptStatus,
    CallLimits,
    ConfigSnapshot,
    DimensionScore,
    ErrorCode,
    FinishReason,
    Identifier,
    InvocationLimits,
    MessageRole,
    ModelInvocationRequest,
    ModelMessage,
    ProgramCheck,
    ReviewConclusion,
    ReviewRecord,
    StageAttempt,
    StageId,
)
from gw.agent_runtime.policy import ReviewDraft
from gw.agent_runtime.ports import ArtifactRepository, ModelGateway, PromptRepository

from .models import (
    EpisodeInputArtifact,
    EpisodeProgramValidation,
    EpisodeStageRequest,
    EpisodeStageResult,
    IndependentReviewOutput,
    ReviewFinding,
    RevisionResponse,
    STAGE_INPUTS,
    STAGE_ORDER,
)
from .prompts import CREATOR_OUTPUT_MODELS, PROMPT_CATALOG, output_schema, prompt_ref_key
from .revisions import RollbackProposal, validate_revision_responses
from .scoring import (
    SCORING_RULE_VERSION,
    STAGE_WEIGHTS,
    evaluate_score_gate,
)
from .validators import input_set_issues, review_finding_location_issues, validate_stage_payload


class EpisodeTextPlugin:
    """Create and independently review one episode text artifact version.

    Caller/runtime responsibilities: supply only approved effective inputs,
    enforce current-source CAS at commit time, choose mode/approval path, persist
    review decisions, apply retry/rollback limits, and own run events.
    """

    def __init__(
        self,
        *,
        model_gateway: ModelGateway,
        prompt_repository: PromptRepository,
        artifact_repository: ArtifactRepository,
        config_snapshot: ConfigSnapshot,
    ) -> None:
        self._models = model_gateway
        self._prompts = prompt_repository
        self._artifacts = artifact_repository
        self._config = config_snapshot

    async def create_candidate(
        self,
        request: EpisodeStageRequest,
        *,
        runtime_context: Mapping[str, Any],
    ) -> ArtifactVersionInput:
        """Create one candidate only; the runtime persists it before review."""
        inputs, input_refs, creator_prompt, reviewer_prompt, sources = await self._prepare(request)
        source_ref_by_stage = {stage: ref for stage, ref in zip(STAGE_INPUTS[request.stage], sources, strict=True)}
        feedback = self._feedback(request.revision_findings, request.revision_responses)
        create_context = self._stage_context(request, inputs, source_ref_by_stage, feedback)
        create_context["clarification_answers"] = dict(request.clarification_answers or runtime_context.get("clarification_answers", {}))
        creator_output, _ = await self._invoke_json(
            request,
            runtime_context=runtime_context,
            runtime_role="writer",
            prompt_role="creator",
            prompt_body=creator_prompt.body,
            user_content=json.dumps(create_context, ensure_ascii=False, sort_keys=True),
            schema=output_schema(request.stage, "creator"),
            validator=lambda payload: CREATOR_OUTPUT_MODELS[request.stage].model_validate(payload),
        )
        canonical_payload = creator_output.artifact_content.model_dump(mode="json")
        responses = [item.model_dump(mode="json") for item in creator_output.revision_responses]
        envelope = {
            "schema_version": 1,
            "stage": request.stage.value,
            "stage_attempt_id": request.stage_attempt_id,
            "content": canonical_payload,
            "source_refs": [ref.model_dump(mode="json") for ref in sources],
            "prompt_refs": [self._prompt_string(creator_prompt.ref), self._prompt_string(reviewer_prompt.ref)],
            "revision_responses": responses,
        }
        artifact_content = json.dumps(envelope, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return ArtifactVersionInput(
            artifact_id=request.artifact_id,
            parent_version_id=request.parent_version_id,
            content=artifact_content,
            metadata={
                "module": "gw.agent_episode",
                "stage": request.stage.value,
                "stage_attempt_id": request.stage_attempt_id,
                "prompt_refs": envelope["prompt_refs"],
                "schema_version": 1,
                "revision_responses": responses,
            },
            source_refs=sources,
            idempotency_key=f"episode:{request.stage_attempt_id}:artifact",
        )

    async def review_candidate(
        self,
        request: EpisodeStageRequest,
        artifact: ArtifactRef,
        content: str,
        *,
        runtime_context: Mapping[str, Any],
    ) -> ReviewDraft:
        """Review the exact durable candidate using a distinct runtime dispatch."""
        inputs, input_refs, creator_prompt, reviewer_prompt, sources = await self._prepare(request)
        try:
            envelope = json.loads(content, parse_float=Decimal)
            if not isinstance(envelope, dict) or envelope.get("schema_version") != 1:
                raise ValueError("unsupported artifact envelope")
            canonical_payload = envelope["content"]
            if envelope.get("stage") != request.stage.value or envelope.get("stage_attempt_id") != request.stage_attempt_id:
                raise ValueError("candidate stage or attempt binding mismatch")
            response_payload = envelope.get("revision_responses", [])
            if not isinstance(response_payload, list):
                raise ValueError("revision responses must be a list")
            revision_responses = [RevisionResponse.model_validate(item) for item in response_payload]
        except (ValueError, TypeError, KeyError, ValidationError, json.JSONDecodeError) as exc:
            raise self._validation_error("The persisted candidate envelope is invalid.", request) from exc

        source_ref_by_stage = {stage: ref for stage, ref in zip(STAGE_INPUTS[request.stage], sources, strict=True)}
        parsed_content, payload_validation = validate_stage_payload(
            request.stage,
            canonical_payload,
            input_payloads=inputs,
            input_refs=input_refs,
            user_goal=request.user_goal,
        )
        if parsed_content is not None:
            canonical_payload = parsed_content.model_dump(mode="json")
        response_check = validate_revision_responses(request.revision_findings, revision_responses)
        checks = list(payload_validation.checks)
        checks.append(ProgramCheck(
            check_id="episode.revision_response_coverage",
            passed=response_check.passed,
            message=("Each prior review finding has exactly one explicit revision response." if response_check.passed else
                     f"Revision responses are incomplete or ambiguous: missing={','.join(response_check.missing_finding_ids)}; "
                     f"unexpected={','.join(response_check.unexpected_finding_ids)}; duplicate={','.join(response_check.duplicate_response_ids)}."),
        ))
        previous_must_fix = [item for item in request.revision_findings if item.category == "must_fix"]

        stored = await self._artifacts.get_version(artifact.artifact_id, artifact.version_id)
        stored_content = await self._artifacts.read_content(artifact.artifact_id, artifact.version_id)
        expected_source_pairs = {(ref.artifact_id.root, ref.version_id.root) for ref in sources}
        actual_source_pairs = {(ref.artifact_id.root, ref.version_id.root) for ref in artifact.source_refs}
        artifact_current = (
            stored is not None
            and stored_content == content
            and stored.version_id.root == artifact.version_id.root
            and stored.artifact_id.root == request.artifact_id
            and stored.lifecycle is ArtifactLifecycle.valid
            and artifact.lifecycle is ArtifactLifecycle.valid
            and actual_source_pairs == expected_source_pairs
            and len(artifact.source_refs) == len(sources) == len(actual_source_pairs)
            and sha256(content.encode("utf-8")).hexdigest() == artifact.content_hash.root
            and sha256(content.encode("utf-8")).hexdigest() == stored.content_hash.root
        )
        if not artifact_current:
            checks.append(ProgramCheck(
                check_id="artifact.source_version_binding",
                passed=False,
                message="Stored candidate identity, lifecycle, source refs, or content hash did not match the submitted version.",
            ))

        review_context = self._stage_context(
            request,
            inputs,
            source_ref_by_stage,
            self._feedback(request.revision_findings, revision_responses),
        )
        review_context["clarification_answers"] = dict(request.clarification_answers or runtime_context.get("clarification_answers", {}))
        review_context.update({
            "artifact_version_id": artifact.version_id.root,
            "artifact": canonical_payload,
            "artifact_content_hash": artifact.content_hash.root,
        })
        review_output, _ = await self._invoke_json(
            request,
            runtime_context=runtime_context,
            runtime_role="reviewer",
            prompt_role="reviewer",
            prompt_body=reviewer_prompt.body,
            user_content=json.dumps(review_context, ensure_ascii=False, sort_keys=True),
            schema=output_schema(request.stage, "reviewer"),
            validator=IndependentReviewOutput.model_validate,
        )
        invalid_locations = review_finding_location_issues(canonical_payload, review_output.findings)
        if invalid_locations:
            raise self._validation_error("The independent review contained findings without resolvable artifact locations.", request)
        expected_dimensions = set(STAGE_WEIGHTS[request.stage])
        if set(review_output.dimension_scores) != expected_dimensions:
            raise self._validation_error("The independent review dimension set did not match the frozen stage contract.", request)

        previous_signatures = {self._finding_signature(item) for item in previous_must_fix}
        previous_ids = {item.finding_id for item in previous_must_fix}
        review_findings = [
            item.model_copy(update={"category": "must_fix"})
            if (item.finding_id in previous_ids or self._finding_signature(item) in previous_signatures) and item.category != "must_fix"
            else item
            for item in review_output.findings
        ]

        rollback_proposal = None
        if review_output.rollback_proposal is not None:
            try:
                proposal = RollbackProposal.model_validate(review_output.rollback_proposal)
                reviewed_ref = ArtifactSourceRef(artifact_id=artifact.artifact_id, version_id=artifact.version_id)
                if proposal.from_stage is not request.stage or proposal.reviewed_artifact_ref != reviewed_ref:
                    raise ValueError("rollback proposal does not bind the exact reviewed artifact")
                if STAGE_ORDER.index(proposal.to_stage) >= STAGE_ORDER.index(request.stage):
                    raise ValueError("rollback target must be an earlier stage")
                expected_sources = {(item.artifact_id.root, item.version_id.root) for item in sources}
                proposal_sources = {(item.artifact_id.root, item.version_id.root) for item in proposal.source_refs}
                if proposal_sources != expected_sources or len(proposal.source_refs) != len(expected_sources):
                    raise ValueError("rollback proposal source refs must match the exact stage input versions")
                allowed_findings = {item.finding_id: item for item in review_findings}
                for evidence in proposal.evidence:
                    finding = allowed_findings.get(evidence.finding_id)
                    if (
                        finding is None
                        or evidence.location != finding.location
                        or evidence.description != finding.description
                        or evidence.source_artifact_ref != reviewed_ref
                    ):
                        raise ValueError("rollback evidence must match exact findings and artifact refs from this review")
                rollback_proposal = proposal.model_dump(mode="json")
            except (TypeError, ValueError, ValidationError) as exc:
                raise self._validation_error("The rollback proposal did not bind this exact review and its findings.", request) from exc

        dimensions = {
            name: DimensionScore(score=float(value.score), evidence=value.evidence)
            for name, value in review_output.dimension_scores.items()
        }
        return ReviewDraft(
            dimension_scores=dimensions,
            dimension_score_decimals={name: str(value.score) for name, value in review_output.dimension_scores.items()},
            program_checks=checks,
            rule_version=self._config.scoring_rule_version,
            findings=[item.model_dump(mode="json") for item in review_findings],
            revision_responses=[item.model_dump(mode="json") for item in revision_responses],
            rollback_proposal=rollback_proposal,
        )

    async def run_stage(
        self,
        request: EpisodeStageRequest,
        *,
        runtime_context: Mapping[str, Any],
    ) -> EpisodeStageResult:
        """Single-call domain harness retained for contract tests; production uses graph roles separately."""
        artifact_input = await self.create_candidate(request, runtime_context=runtime_context)
        artifact = await self._artifacts.create_version(artifact_input)
        content = await self._artifacts.read_content(artifact.artifact_id, artifact.version_id)
        if content is None:
            raise self._validation_error("The candidate body was not durably stored.", request)
        draft = await self.review_candidate(request, artifact, content, runtime_context=runtime_context)
        decimal_scores = dict(draft.dimension_score_decimals)
        raw_weights = self._config.stage_score_weights.get(request.stage.value) or STAGE_WEIGHTS[request.stage]
        weights = {key: Decimal(str(value)) for key, value in raw_weights.items()}
        score_result = evaluate_score_gate(
            request.stage,
            decimal_scores,
            program_passed=all(item.passed for item in draft.program_checks),
            artifact_current=artifact.lifecycle is ArtifactLifecycle.valid,
            threshold=Decimal(str(self._config.pass_score)),
            weights=weights,
        )
        if any(item.get("category") == "must_fix" for item in draft.findings) and score_result.automatic_pass:
            score_result = score_result.model_copy(update={"automatic_pass": False})
        program_validation = EpisodeProgramValidation(checks=list(draft.program_checks))
        conclusion = ReviewConclusion.block if not program_validation.passed else (ReviewConclusion.pass_ if score_result.automatic_pass else ReviewConclusion.revise)
        review = ReviewRecord(
            schema_version=1,
            review_id=uuid4().hex,
            run_id=request.run_id,
            artifact=artifact,
            rule_version=draft.rule_version,
            dimension_scores=dict(draft.dimension_scores),
            program_validation_passed=program_validation.passed,
            program_checks=list(draft.program_checks),
            overall_score=float(score_result.weighted_score),
            overall_score_decimal=score_result.weighted_score,
            findings=list(draft.findings),
            revision_responses=list(draft.revision_responses),
            rollback_proposal=draft.rollback_proposal,
            conclusion=conclusion,
            created_at=datetime.now(timezone.utc),
        )
        sources = artifact_input.source_refs
        prompt_refs = artifact_input.metadata["prompt_refs"]
        attempt = StageAttempt(
            schema_version=1,
            stage_attempt_id=request.stage_attempt_id,
            run_id=request.run_id,
            stage=request.stage,
            kind=request.attempt_kind,
            attempt_number=request.attempt_number,
            input_artifact_version_ids=[ref.version_id for ref in sources],
            status=AttemptStatus.succeeded,
            invocation_ids=[Identifier(f"episode:{request.stage_attempt_id}:writer"), Identifier(f"episode:{request.stage_attempt_id}:reviewer")],
            output_artifact_version_id=artifact.version_id,
            review_id=review.review_id,
            created_at=datetime.now(timezone.utc),
        )
        return EpisodeStageResult(
            artifact=artifact,
            attempt=attempt,
            review=review,
            program_validation=program_validation,
            score=score_result,
            creator_invocation_id=f"episode:{request.stage_attempt_id}:writer",
            reviewer_invocation_id=f"episode:{request.stage_attempt_id}:reviewer",
            prompt_refs=list(prompt_refs),
        )

    async def _prepare(self, request: EpisodeStageRequest):
        problems = input_set_issues(request.stage, request.inputs)
        if problems:
            raise DomainError(
                ErrorCode.ARTIFACT_STALE if any("effective valid" in item for item in problems) else ErrorCode.VALIDATION_FAILED,
                problems[0], request_id=request.request_id,
            )
        if request.stage is StageId.brief and not request.user_goal:
            raise self._validation_error("A brief stage requires the user's original goal.", request)
        if request.attempt_kind.value != "initial" and request.parent_version_id is None:
            raise self._validation_error("A revision or rollback stage attempt must identify its parent artifact version.", request)
        human_feedback = request.human_revision_feedback
        if human_feedback is not None and (
            request.attempt_kind.value != "revision"
            or request.parent_version_id != human_feedback.artifact_version_id
        ):
            raise self._validation_error("Human rejection feedback must be bound to the exact parent version of a revision attempt.", request)
        if request.parent_version_id is not None and await self._artifacts.get_version(
            Identifier(request.artifact_id), Identifier(request.parent_version_id)
        ) is None:
            raise self._validation_error("The requested parent artifact version does not exist.", request)
        inputs: dict[Any, Any] = {}
        input_refs: dict[Any, ArtifactRef] = {}
        for item in request.inputs:
            inputs[item.stage] = await self._read_input(item, request)
            input_refs[item.stage] = item.artifact
        model_ref = self._config.model_ref
        if model_ref is None:
            raise DomainError(ErrorCode.CAPABILITY_UNSUPPORTED, "No model is pinned in the run configuration.", request_id=request.request_id)
        capabilities = await self._models.describe_capabilities(model_ref)
        supported = {role.value for role in capabilities.supported_roles}
        if "system" not in supported or "user" not in supported or not capabilities.structured_output:
            raise DomainError(ErrorCode.CAPABILITY_UNSUPPORTED, "The configured model must preserve system/user roles and structured JSON output.", request_id=request.request_id)
        creator_prompt = await self._resolve_prompt(request, "creator")
        reviewer_prompt = await self._resolve_prompt(request, "reviewer")
        source_by_stage = {
            stage: ArtifactSourceRef(artifact_id=input_refs[stage].artifact_id, version_id=input_refs[stage].version_id)
            for stage in STAGE_INPUTS[request.stage]
        }
        sources = [source_by_stage[stage] for stage in STAGE_INPUTS[request.stage]]
        return inputs, input_refs, creator_prompt, reviewer_prompt, sources

    @staticmethod
    def _feedback(findings: list[ReviewFinding], responses: list[RevisionResponse]) -> dict[str, Any]:
        return {
            "findings": [item.model_dump(mode="json") for item in findings],
            "responses": [item.model_dump(mode="json") for item in responses],
        }

    @staticmethod
    def _finding_signature(finding: ReviewFinding) -> tuple[str, str]:
        """A replacement ID cannot make the same located mandatory issue disappear."""
        location = " ".join(finding.location.strip().casefold().split())
        description = " ".join(finding.description.strip().casefold().split())
        return location, description

    @staticmethod
    def _stage_context(request, inputs, source_ref_by_stage, feedback) -> dict[str, Any]:
        return {
            "stage": request.stage.value,
            "user_goal": request.user_goal,
            "clarification_answers": dict(request.clarification_answers),
            "inputs": {stage.value: inputs[stage] for stage in STAGE_INPUTS[request.stage]},
            "input_refs": {stage.value: source_ref_by_stage[stage].model_dump(mode="json") for stage in STAGE_INPUTS[request.stage]},
            "revision_feedback": feedback,
            "human_revision_feedback": (
                request.human_revision_feedback.model_dump(mode="json")
                if request.human_revision_feedback is not None else None
            ),
        }

    async def _invoke_json(
        self,
        request: EpisodeStageRequest,
        *,
        runtime_context: Mapping[str, Any],
        runtime_role: str,
        prompt_role: str,
        prompt_body: str,
        user_content: str,
        schema: dict[str, object],
        validator,
    ):
        invocation_id, response = await self._invoke(
            request, runtime_context=runtime_context, runtime_role=runtime_role,
            prompt_role=prompt_role, prompt_body=prompt_body, user_content=user_content, schema=schema,
        )
        try:
            if response.content is None:
                raise ValueError("empty model content")
            parsed = validator(json.loads(response.content, parse_float=Decimal))
            return parsed, [invocation_id]
        except (ValueError, TypeError, ValidationError, json.JSONDecodeError) as first_error:
            limits = runtime_context.get("call_limits")
            if not isinstance(limits, CallLimits) or limits.max_format_repairs < 1:
                raise self._validation_error(f"The {prompt_role} returned output outside its structured contract.", request) from first_error
            repair_context = json.dumps({
                "original_user_context": json.loads(user_content),
                "invalid_output": response.content,
                "validation_error": str(first_error)[:800],
                "instruction": "Return the corrected JSON object matching the supplied output schema. Preserve all valid content and do not add explanation.",
            }, ensure_ascii=False, sort_keys=True)
            repair_id, repaired = await self._invoke(
                request, runtime_context=runtime_context, runtime_role=runtime_role,
                prompt_role=prompt_role, prompt_body=prompt_body, user_content=repair_context, schema=schema,
                format_repair=True,
            )
            try:
                if repaired.content is None:
                    raise ValueError("empty repaired content")
                parsed = validator(json.loads(repaired.content, parse_float=Decimal))
                return parsed, [invocation_id, repair_id]
            except (ValueError, TypeError, ValidationError, json.JSONDecodeError) as second_error:
                raise self._validation_error(f"The {prompt_role} output remained outside its structured contract after one format repair.", request) from second_error

    async def _read_input(
        self, item: EpisodeInputArtifact, request: EpisodeStageRequest
    ) -> Mapping[str, Any]:
        ref = item.artifact
        stored = await self._artifacts.get_version(ref.artifact_id, ref.version_id)
        if (
            stored is None
            or stored.version_id.root != ref.version_id.root
            or stored.artifact_id.root != ref.artifact_id.root
            or stored.revision.root != ref.revision.root
            or stored.lifecycle is not ArtifactLifecycle.valid
        ):
            raise DomainError(
                ErrorCode.ARTIFACT_STALE,
                f"The supplied {item.stage.value} artifact version is not effective.",
                request_id=request.request_id,
            )
        content = await self._artifacts.read_content(ref.artifact_id, ref.version_id)
        if content is None or sha256(content.encode("utf-8")).hexdigest() != stored.content_hash.root:
            raise DomainError(
                ErrorCode.VALIDATION_FAILED,
                f"The supplied {item.stage.value} artifact content failed its version hash check.",
                request_id=request.request_id,
            )
        try:
            document = json.loads(content)
        except json.JSONDecodeError as exc:
            raise DomainError(
                ErrorCode.VALIDATION_FAILED,
                f"The supplied {item.stage.value} artifact content is not valid episode JSON.",
                request_id=request.request_id,
            ) from exc
        if (
            not isinstance(document, dict)
            or document.get("schema_version") != 1
            or document.get("stage") != item.stage.value
            or not isinstance(document.get("content"), dict)
            or not isinstance(document.get("source_refs"), list)
        ):
            raise DomainError(
                ErrorCode.VALIDATION_FAILED,
                f"The supplied {item.stage.value} artifact does not match the episode text envelope.",
                request_id=request.request_id,
            )
        envelope_refs = document["source_refs"]
        try:
            if any(
                not isinstance(entry, dict)
                or set(entry) != {"artifact_id", "version_id"}
                for entry in envelope_refs
            ):
                raise ValueError("unknown source reference properties")
            envelope_pairs = {
                (entry["artifact_id"], entry["version_id"])
                for entry in envelope_refs
            }
        except (KeyError, TypeError, ValueError) as exc:
            raise DomainError(
                ErrorCode.VALIDATION_FAILED,
                f"The supplied {item.stage.value} artifact has malformed source references.",
                request_id=request.request_id,
            ) from exc
        stored_pairs = {
            (source.artifact_id.root, source.version_id.root)
            for source in stored.source_refs
        }
        if (
            len(envelope_pairs) != len(envelope_refs)
            or len(stored_pairs) != len(stored.source_refs)
            or envelope_pairs != stored_pairs
        ):
            raise DomainError(
                ErrorCode.VALIDATION_FAILED,
                f"The supplied {item.stage.value} artifact provenance does not match its shared artifact reference.",
                request_id=request.request_id,
            )
        return document["content"]

    async def _resolve_prompt(self, request: EpisodeStageRequest, role: str) -> Any:
        expected = PROMPT_CATALOG[(request.stage, role)]
        resolved = await self._prompts.resolve_template_ref(
            request.project_id,
            request.stage,
            Identifier(f"{request.stage.value}.{role}"),
        )
        if resolved is None or prompt_ref_key(resolved) != prompt_ref_key(expected.ref):
            raise DomainError(
                ErrorCode.VALIDATION_FAILED,
                "The stage prompt reference is not the frozen episode-text version.",
                request_id=request.request_id,
            )
        pinned = {prompt_ref_key(ref) for ref in self._config.prompt_refs}
        if prompt_ref_key(resolved) not in pinned:
            raise DomainError(
                ErrorCode.VALIDATION_FAILED,
                "The stage prompt is not pinned in this run's configuration snapshot.",
                request_id=request.request_id,
            )
        template = await self._prompts.get_template(resolved.template_id, resolved.version)
        if template is None or prompt_ref_key(template.ref) != prompt_ref_key(resolved):
            raise DomainError(
                ErrorCode.VALIDATION_FAILED,
                "The immutable stage prompt template could not be resolved.",
                request_id=request.request_id,
            )
        if (
            template.purpose.root != f"{request.stage.value}.{role}"
            or sha256(template.body.encode("utf-8")).hexdigest() != resolved.content_hash.root
        ):
            raise DomainError(
                ErrorCode.VALIDATION_FAILED,
                "The stage prompt body does not match its pinned hash and purpose.",
                request_id=request.request_id,
            )
        return template

    async def _invoke(
        self,
        request: EpisodeStageRequest,
        *,
        runtime_context: Mapping[str, Any],
        runtime_role: str,
        prompt_role: str,
        prompt_body: str,
        user_content: str,
        schema: dict[str, object],
        format_repair: bool = False,
    ) -> tuple[str, Any]:
        limits = runtime_context.get("call_limits")
        dispatch = runtime_context.get("dispatch_model")
        guard = runtime_context.get("assert_dispatch_allowed")
        if not isinstance(limits, CallLimits) or not callable(dispatch) or not callable(guard):
            raise DomainError(
                ErrorCode.CAPABILITY_UNSUPPORTED,
                "Episode model calls require the runtime's durable dispatcher and current-state guard.",
                request_id=request.request_id,
            )
        if self._config.model_ref is None:
            raise DomainError(ErrorCode.CAPABILITY_UNSUPPORTED, "No model is pinned in the run configuration.", request_id=request.request_id)
        digest = sha256(f"{request.run_id}:{request.stage_attempt_id}:{runtime_role}:{prompt_role}:{user_content}".encode("utf-8")).hexdigest()
        invocation_id = f"episode-call:{digest}"
        await guard(runtime_role)
        response = await dispatch(
            runtime_role,
            ModelInvocationRequest(
                invocation_id=invocation_id,
                idempotency_key=f"episode:{request.run_id}:{request.stage_attempt_id}:{runtime_role}:{digest}",
                model_ref=self._config.model_ref,
                messages=[
                    ModelMessage(role=MessageRole.system, content=prompt_body),
                    ModelMessage(role=MessageRole.user, content=user_content),
                ],
                output_schema=schema,
                tools=[],
                limits=InvocationLimits(
                    max_output_tokens=limits.max_output_tokens,
                    timeout_seconds=limits.timeout_seconds,
                ),
            ),
            format_repair=format_repair,
        )
        if response.finish_reason is not FinishReason.stop or response.tool_calls:
            raise DomainError(
                ErrorCode.CAPABILITY_UNSUPPORTED,
                f"The {prompt_role} invocation did not complete as a structured text response.",
                request_id=request.request_id,
            )
        return invocation_id, response

    @staticmethod
    def _prompt_string(ref: Any) -> str:
        return f"{ref.template_id.root}@{ref.version.root}#{ref.content_hash.root}"

    @staticmethod
    def _validation_error(message: str, request: EpisodeStageRequest) -> DomainError:
        return DomainError(ErrorCode.VALIDATION_FAILED, message, request_id=request.request_id)


__all__ = ["EpisodeTextPlugin"]
