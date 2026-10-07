"""Server-owned authorization, scoring, quota, and capability policy."""
from __future__ import annotations

import inspect
import math
import uuid
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation, localcontext
from enum import StrEnum
from typing import Any, Protocol

from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError as JsonSchemaValidationError

from gw.agent_runtime.errors import DomainError
from gw.agent_runtime.models import (
    ArtifactLifecycle,
    ArtifactRef,
    BudgetDecision,
    BudgetState,
    ConfigSnapshot,
    DimensionScore,
    ErrorCode,
    Identifier,
    ProgramCheck,
    ReviewConclusion,
    ReviewRecord,
    StageId,
    ToolCostState,
    ToolRiskLevel,
)
from gw.agent_runtime.repository import utc_now


STAGE_ORDER: tuple[StageId, ...] = (
    StageId.brief,
    StageId.outline_characters,
    StageId.full_script,
    StageId.scene_script,
    StageId.storyboard_text,
    StageId.delivery_check,
)
STAGE_INDEX = {stage.value: index for index, stage in enumerate(STAGE_ORDER)}


class ReviewGate(StrEnum):
    pass_ = "pass"
    revise = "revise"
    block = "block"


class PermissionAuthorizer(Protocol):
    async def __call__(self, actor_id: Identifier, required_permissions: Sequence[Identifier]) -> bool: ...


async def deny_by_default(actor_id: Identifier, required_permissions: Sequence[Identifier]) -> bool:
    """No capability can act until a trusted permission check is injected."""
    return not required_permissions


@dataclass(frozen=True, slots=True)
class ReviewDraft:
    """Untrusted review inputs; overall score/conclusion are always recomputed."""

    dimension_scores: Mapping[str, DimensionScore]
    program_checks: Sequence[ProgramCheck]
    rule_version: Identifier
    dimension_score_decimals: Mapping[str, str] = field(default_factory=dict)
    findings: Sequence[Mapping[str, Any]] = ()
    revision_responses: Sequence[Mapping[str, Any]] = ()
    rollback_proposal: Mapping[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class GateResult:
    gate: ReviewGate
    program_validation_passed: bool
    overall_score: float
    reason: str
    overall_score_decimal: str | None = None


@dataclass(frozen=True, slots=True)
class ToolRegistration:
    name: str
    handler: Callable[[Mapping[str, Any]], Awaitable[Any]]
    required_permissions: tuple[Identifier, ...] = ()
    input_schema: Mapping[str, Any] = field(default_factory=lambda: {"type": "object"})
    risk_level: ToolRiskLevel = ToolRiskLevel.low
    cost_state: ToolCostState = ToolCostState.free
    idempotency_supported: bool = True


@dataclass(frozen=True, slots=True)
class RoleRegistration:
    name: str
    handler: Callable[..., Awaitable[Any]]
    required_permissions: tuple[Identifier, ...] = ()
    proposal_safe: bool = False
    stage_writer: bool = False


@dataclass(frozen=True, slots=True)
class OrchestrationProposal:
    """Structured, untrusted planner action; runtime owns all state transitions."""

    action: str
    capability: str | None = None
    target_stage: StageId | None = None
    question: str | None = None


ORCHESTRATION_ACTIONS = frozenset({"clarify", "select_capability", "create", "review", "revise", "rollback"})


class CapabilityRegistry:
    """Explicit, dependency-injected roles/tools; no episode or host package lookup."""

    def __init__(self, authorizer: PermissionAuthorizer = deny_by_default) -> None:
        self._roles: dict[str, RoleRegistration] = {}
        self._tools: dict[str, ToolRegistration] = {}
        self.authorizer = authorizer

    def register_role(self, registration: RoleRegistration) -> None:
        if not registration.name or registration.name in self._roles:
            raise ValueError("Role name must be non-empty and unique.")
        self._roles[registration.name] = registration

    def register_tool(self, registration: ToolRegistration) -> None:
        if not registration.name or registration.name in self._tools:
            raise ValueError("Tool name must be non-empty and unique.")
        Draft202012Validator.check_schema(dict(registration.input_schema))
        self._tools[registration.name] = registration

    def get_role(self, name: str) -> RoleRegistration:
        try:
            return self._roles[name]
        except KeyError as exc:
            raise DomainError(ErrorCode.CAPABILITY_UNSUPPORTED, "The requested role is not registered.", request_id=f"role:{name}") from exc

    def get_tool(self, name: str) -> ToolRegistration:
        try:
            return self._tools[name]
        except KeyError as exc:
            raise DomainError(ErrorCode.CAPABILITY_UNSUPPORTED, "The requested tool is not registered.", request_id=f"tool:{name}") from exc

    @property
    def stage_writer_names(self) -> tuple[str, ...]:
        return tuple(sorted(name for name, role in self._roles.items() if role.stage_writer))

    async def invoke_role(self, name: str, actor_id: Identifier, *args: Any, **kwargs: Any) -> Any:
        role = self.get_role(name)
        if not await self.authorizer(actor_id, role.required_permissions):
            raise DomainError(ErrorCode.NOT_AUTHORIZED, "The actor is not authorized for this role.", request_id=actor_id.root)
        runtime_context = kwargs.pop("_runtime_context", None)
        if runtime_context is not None:
            try:
                parameters = inspect.signature(role.handler).parameters.values()
                accepts_context = any(p.name in {"runtime_context", "_runtime_context"} or p.kind is inspect.Parameter.VAR_KEYWORD for p in parameters)
            except (TypeError, ValueError):
                accepts_context = False
            if accepts_context:
                names = {p.name for p in inspect.signature(role.handler).parameters.values()}
                key = "_runtime_context" if "_runtime_context" in names and "runtime_context" not in names else "runtime_context"
                kwargs[key] = runtime_context
        return await role.handler(*args, **kwargs)

    async def invoke_tool(self, name: str, actor_id: Identifier, arguments: Mapping[str, Any], *, budget: BudgetDecision | None = None) -> Any:
        tool = self.get_tool(name)
        if not await self.authorizer(actor_id, tool.required_permissions):
            raise DomainError(ErrorCode.NOT_AUTHORIZED, "The actor is not authorized for this tool.", request_id=actor_id.root)
        try:
            Draft202012Validator(dict(tool.input_schema)).validate(dict(arguments))
        except JsonSchemaValidationError as exc:
            raise DomainError(ErrorCode.VALIDATION_FAILED, "Tool arguments do not match the registered input schema.", request_id=actor_id.root) from exc
        if tool.risk_level in {ToolRiskLevel.high, ToolRiskLevel.critical} and not tool.required_permissions:
            raise DomainError(ErrorCode.NOT_AUTHORIZED, "High-risk tools must declare explicit permissions.", request_id=actor_id.root)
        if not tool.idempotency_supported:
            raise DomainError(ErrorCode.CAPABILITY_UNSUPPORTED, "Non-idempotent tools require a dedicated durable invocation adapter before registration can be used.", request_id=actor_id.root)
        if tool.cost_state is not ToolCostState.free:
            if budget is None or budget.state is not BudgetState.allowed:
                raise DomainError(ErrorCode.QUOTA_EXCEEDED, "Tool budget is not explicitly authorized.", request_id=actor_id.root)
            if budget.amount is None and budget.reason is None:
                raise DomainError(ErrorCode.VALIDATION_FAILED, "Unknown budget amounts require an explicit reason.", request_id=actor_id.root)
            if budget.amount is not None and budget.currency is None:
                raise DomainError(ErrorCode.VALIDATION_FAILED, "A known budget amount must carry its currency.", request_id=actor_id.root)
            if tool.cost_state is ToolCostState.unknown and (budget.amount is not None or budget.reason is None):
                raise DomainError(ErrorCode.VALIDATION_FAILED, "Unknown tool cost requires a null amount and an explicit reason.", request_id=actor_id.root)
        return await tool.handler(arguments)


class RuntimePolicy:
    """Pure policy functions for frozen thresholds and conserved retry counters."""

    def __init__(
        self,
        score_weights: Mapping[str, float] | None = None,
        *,
        default_stage_score_weights: Mapping[str, Mapping[str, Decimal | str | float]] | None = None,
        registered_rule_version: Identifier | str | None = None,
    ) -> None:
        self.score_weights = dict(score_weights or {})
        self.default_stage_score_weights = {
            str(stage): self._decimal_weights(weights)
            for stage, weights in (default_stage_score_weights or {}).items()
        }
        self.registered_rule_version = (
            registered_rule_version.root
            if isinstance(registered_rule_version, Identifier)
            else registered_rule_version
        )
        for weights in [self.score_weights, *self.default_stage_score_weights.values()]:
            if any(not isinstance(value, (int, float, Decimal)) or not math.isfinite(float(value)) or value <= 0 for value in weights.values()):
                raise ValueError("Score weights must be positive numeric values.")
        if any(sum(weights.values(), Decimal(0)) != Decimal(1) for weights in self.default_stage_score_weights.values()):
            raise ValueError("Registered per-stage score weights must sum exactly to 1.")
        if self.default_stage_score_weights and set(self.default_stage_score_weights) != {stage.value for stage in STAGE_ORDER}:
            raise ValueError("Registered stage scoring weights must cover every runtime stage exactly.")

    REVISION_LIMIT = 8
    ROLLBACK_LIMIT = 3
    TECHNICAL_RETRY_LIMIT = 2
    FORMAT_REPAIR_LIMIT = 1
    PASS_SCORE = 9.0

    def validate_config(self, config: ConfigSnapshot) -> None:
        if config.call_limits.max_model_retries > self.TECHNICAL_RETRY_LIMIT or config.call_limits.max_format_repairs > self.FORMAT_REPAIR_LIMIT:
            raise DomainError(ErrorCode.VALIDATION_FAILED, "Configured retry limits cannot exceed the technical hard limits.", request_id=config.config_snapshot_id.root)
        if self.registered_rule_version is not None and config.scoring_rule_version.root != self.registered_rule_version:
            raise DomainError(ErrorCode.VALIDATION_FAILED, "The scoring rule version is not registered by this runtime.", request_id=config.config_snapshot_id.root)
        for stage, weights in config.stage_score_weights.items():
            if stage not in STAGE_INDEX:
                raise DomainError(ErrorCode.VALIDATION_FAILED, "The configuration names an unregistered stage for scoring.", request_id=config.config_snapshot_id.root)
            self._validated_config_weights(stage, weights, config.config_snapshot_id.root)

    def resolve_config(self, config: ConfigSnapshot) -> ConfigSnapshot:
        """Freeze the registered domain defaults into the run's captured snapshot."""
        self.validate_config(config)
        effective = {
            stage: dict(weights)
            for stage, weights in self.default_stage_score_weights.items()
        }
        effective.update({stage: dict(weights) for stage, weights in config.stage_score_weights.items()})
        payload = config.model_dump(mode="json")
        payload["stage_score_weights"] = {
            stage: {dimension: float(weight) for dimension, weight in weights.items()}
            for stage, weights in effective.items()
        }
        return ConfigSnapshot.model_validate(payload)

    def make_review(
        self,
        run_id: Identifier,
        artifact: ArtifactRef,
        draft: ReviewDraft,
        *,
        stage: StageId | None = None,
        config: ConfigSnapshot | None = None,
    ) -> tuple[ReviewRecord, GateResult]:
        if artifact.lifecycle is not ArtifactLifecycle.valid:
            raise DomainError(ErrorCode.ARTIFACT_STALE, "A stale or trashed artifact cannot be reviewed for release.", request_id=run_id.root)
        if not draft.dimension_scores:
            raise DomainError(ErrorCode.VALIDATION_FAILED, "At least one program-produced review dimension is required.", request_id=run_id.root)
        decimal_values: dict[str, Decimal] = {}
        if draft.dimension_score_decimals and set(draft.dimension_score_decimals) != set(draft.dimension_scores):
            raise DomainError(ErrorCode.VALIDATION_FAILED, "Exact decimal scores must name the same dimensions as the score records.", request_id=run_id.root)
        for name, score in draft.dimension_scores.items():
            if not isinstance(name, str) or not name:
                raise DomainError(ErrorCode.VALIDATION_FAILED, "Review dimension names must be non-empty.", request_id=run_id.root)
            try:
                raw = draft.dimension_score_decimals[name] if draft.dimension_score_decimals else str(score.score)
                if not isinstance(raw, str) or len(raw) > 4096:
                    raise InvalidOperation
                value = Decimal(raw)
            except (InvalidOperation, KeyError) as exc:
                raise DomainError(ErrorCode.VALIDATION_FAILED, "A review dimension score is not a valid decimal.", request_id=run_id.root) from exc
            if not value.is_finite() or value < 0 or value > 10:
                raise DomainError(ErrorCode.VALIDATION_FAILED, "A review dimension score must be between 0 and 10.", request_id=run_id.root)
            decimal_values[name] = value
        if config is not None and self.registered_rule_version is not None and config.scoring_rule_version.root != self.registered_rule_version:
            raise DomainError(ErrorCode.VALIDATION_FAILED, "The snapshot scoring rule is not registered.", request_id=run_id.root)
        if config is not None and self.registered_rule_version is not None and draft.rule_version.root != config.scoring_rule_version.root:
            raise DomainError(ErrorCode.VALIDATION_FAILED, "The review rule version does not match the captured configuration snapshot.", request_id=draft.rule_version.root)
        weights: Mapping[str, Decimal]
        if config is not None and stage is not None:
            configured = config.stage_score_weights.get(stage.value)
            weights = self._validated_config_weights(stage.value, configured, config.config_snapshot_id.root) if configured is not None else self.default_stage_score_weights.get(stage.value, {})
        elif self.score_weights:
            weights = {key: Decimal(str(value)) for key, value in self.score_weights.items()}
        else:
            weights = {}
        if weights:
            if set(weights) != set(decimal_values):
                raise DomainError(ErrorCode.VALIDATION_FAILED, "The registered stage scoring dimensions must match the review dimension set exactly.", request_id=run_id.root)
            weight_total = sum(weights.values(), Decimal(0))
            if weight_total <= 0 or (config is not None and stage is not None and weight_total != Decimal(1)):
                raise DomainError(ErrorCode.VALIDATION_FAILED, "Stage score weights must sum exactly to 1.", request_id=run_id.root)
            precision = self._calculation_precision(decimal_values, weights)
            with localcontext() as ctx:
                ctx.prec = precision
                exact_score = sum((decimal_values[name] * weight for name, weight in weights.items()), Decimal(0))
                if config is None or stage is None:
                    exact_score /= weight_total
        else:
            precision = self._calculation_precision(decimal_values, {})
            with localcontext() as ctx:
                ctx.prec = precision
                exact_score = sum(decimal_values.values(), Decimal(0)) / Decimal(len(decimal_values))
        exact_score_text = self._decimal_text(exact_score)
        score = float(exact_score)
        pass_score = Decimal(str(config.pass_score)) if config is not None else Decimal(str(self.PASS_SCORE))
        checks_for_program = [item for item in draft.program_checks if item.check_id.root != "episode.must_fix_findings_resolved"]
        passed = bool(draft.program_checks) and all(item.passed for item in checks_for_program)
        must_fix_open = any(str(item.get("category", "")) == "must_fix" for item in draft.findings)
        unresolved_content = must_fix_open or any(
            item.check_id.root == "episode.must_fix_findings_resolved" and not item.passed
            for item in draft.program_checks
        )
        conclusion = (
            ReviewConclusion.block if not passed else
            ReviewConclusion.pass_ if not unresolved_content and exact_score >= pass_score else
            ReviewConclusion.revise
        )
        review = ReviewRecord(
            schema_version=1,
            review_id=uuid.uuid4().hex,
            run_id=run_id,
            artifact=artifact,
            rule_version=draft.rule_version,
            dimension_scores=dict(draft.dimension_scores),
            dimension_score_decimals={name: self._decimal_text(value) for name, value in decimal_values.items()},
            program_validation_passed=passed,
            program_checks=list(draft.program_checks),
            overall_score=score,
            overall_score_decimal=exact_score_text,
            findings=[dict(item) for item in draft.findings],
            revision_responses=[dict(item) for item in draft.revision_responses],
            rollback_proposal=dict(draft.rollback_proposal) if draft.rollback_proposal is not None else None,
            conclusion=conclusion,
            created_at=utc_now(),
        )
        gate = ReviewGate.pass_ if passed and not unresolved_content and exact_score >= pass_score else (ReviewGate.revise if passed else ReviewGate.block)
        message = "Program checks passed and weighted score meets the captured release threshold." if gate is ReviewGate.pass_ else ("Program checks failed; no override or approval can bypass them." if not passed else "Score is below the captured release threshold.")
        return review, GateResult(gate=gate, program_validation_passed=passed, overall_score=score, reason=message, overall_score_decimal=exact_score_text)

    def revision_count(self, state: Mapping[str, Any], stage: StageId) -> int:
        by_stage = state.get("revision_count_by_stage", {})
        value = by_stage.get(stage.value, 0) if isinstance(by_stage, Mapping) else 0
        return max(0, int(value))

    def rollback_count(self, state: Mapping[str, Any]) -> int:
        return max(0, int(state.get("rollback_count", 0)))

    def allow_revision(self, state: Mapping[str, Any], stage: StageId) -> bool:
        return self.revision_count(state, stage) < self.revision_limit(state)

    def allow_rollback(self, state: Mapping[str, Any], target: StageId) -> bool:
        return self.rollback_count(state) < self.rollback_limit(state) and self.allow_revision(state, target)

    def add_revision(self, state: Mapping[str, Any], stage: StageId) -> dict[str, Any]:
        updated = dict(state)
        counts = dict(updated.get("revision_count_by_stage", {}))
        current = int(counts.get(stage.value, 0))
        if current >= self.revision_limit(state):
            raise DomainError(ErrorCode.QUOTA_EXCEEDED, "The stage revision limit captured in the run configuration is exhausted.", request_id=stage.value)
        counts[stage.value] = current + 1
        updated["revision_count_by_stage"] = counts
        return updated

    def add_rollback(self, state: Mapping[str, Any], target: StageId) -> dict[str, Any]:
        if not self.allow_rollback(state, target):
            raise DomainError(ErrorCode.QUOTA_EXCEEDED, "The run rollback or target-stage revision limit is exhausted.", request_id=target.value)
        updated = self.add_revision(state, target)
        if self.rollback_count(state) >= self.rollback_limit(state):
            raise DomainError(ErrorCode.QUOTA_EXCEEDED, "The rollback limit captured in the run configuration is exhausted.", request_id=target.value)
        updated["rollback_count"] = self.rollback_count(state) + 1
        return updated

    def revision_limit(self, state: Mapping[str, Any]) -> int:
        return self._snapshot_limit(state, "revision_limit", self.REVISION_LIMIT)

    def rollback_limit(self, state: Mapping[str, Any]) -> int:
        return self._snapshot_limit(state, "rollback_limit", self.ROLLBACK_LIMIT)

    @staticmethod
    def _snapshot_limit(state: Mapping[str, Any], key: str, default: int) -> int:
        snapshot = state.get("config_snapshot")
        if isinstance(snapshot, ConfigSnapshot):
            return int(getattr(snapshot, key))
        if isinstance(snapshot, Mapping):
            try:
                return max(0, int(snapshot.get(key, default)))
            except (TypeError, ValueError):
                return default
        return default

    @staticmethod
    def _decimal_weights(weights: Mapping[str, Decimal | str | float]) -> dict[str, Decimal]:
        result = {str(name): Decimal(str(value)) for name, value in weights.items()}
        if any(not value.is_finite() or value <= 0 for value in result.values()):
            raise ValueError("Score weights must be positive finite decimals.")
        return result

    def _validated_config_weights(self, stage: str, raw: Mapping[str, Any], request_id: str) -> dict[str, Decimal]:
        weights = self._decimal_weights(raw)
        if sum(weights.values(), Decimal(0)) != Decimal(1):
            raise DomainError(ErrorCode.VALIDATION_FAILED, "Stage score weights must sum exactly to 1.", request_id=request_id)
        defaults = self.default_stage_score_weights.get(stage)
        if defaults is not None and set(weights) != set(defaults):
            raise DomainError(ErrorCode.VALIDATION_FAILED, "Configured stage dimensions do not match the registered scoring dimensions.", request_id=request_id)
        return weights

    @staticmethod
    def _decimal_text(value: Decimal) -> str:
        if value.is_zero():
            return "0"
        return format(value, "f")

    @staticmethod
    def _calculation_precision(scores: Mapping[str, Decimal], weights: Mapping[str, Decimal]) -> int:
        """Keep finite decimal products/sums exact, including scores beyond 28 digits."""
        products: list[tuple[int, int]] = []
        if weights:
            for name, score in scores.items():
                weight = weights.get(name)
                if weight is None or score.is_zero():
                    continue
                digits = max(1, len(score.as_tuple().digits)) + max(1, len(weight.as_tuple().digits))
                exponent = score.as_tuple().exponent + weight.as_tuple().exponent
                adjusted = exponent + digits - 1
                products.append((adjusted, exponent))
        else:
            products = [
                (value.adjusted(), value.as_tuple().exponent)
                for value in scores.values()
                if not value.is_zero()
            ]
        if not products:
            precision = 64
        else:
            highest = max(adjusted for adjusted, _ in products)
            lowest_quantum = min(exponent for _, exponent in products)
            carry = len(str(max(1, len(products))))
            precision = max(64, highest - lowest_quantum + carry + 4)
            if not weights:
                # Division by a non-power-of-ten count may be repeating. Keep
                # the legacy unweighted compatibility path well above the
                # precision needed to compare ordinary thresholds.
                precision = max(precision, 128)
        if precision > 4096:
            raise DomainError(ErrorCode.VALIDATION_FAILED, "Review decimal precision exceeds the runtime safety bound.", request_id="review")
        return precision

    def next_stage(self, current: StageId) -> StageId | None:
        index = STAGE_ORDER.index(current)
        return STAGE_ORDER[index + 1] if index + 1 < len(STAGE_ORDER) else None

    def validate_override(self, review: ReviewRecord, artifact: ArtifactRef, *, pass_score: Decimal | str | float | None = None) -> None:
        if artifact.lifecycle is not ArtifactLifecycle.valid or review.artifact.version_id.root != artifact.version_id.root:
            raise DomainError(ErrorCode.ARTIFACT_STALE, "Override must bind to the exact current artifact version.", request_id=review.review_id.root)
        if not review.program_validation_passed:
            raise DomainError(ErrorCode.VALIDATION_FAILED, "Override cannot bypass deterministic program validation.", request_id=review.review_id.root)
        threshold = Decimal(str(pass_score)) if pass_score is not None else Decimal(str(self.PASS_SCORE))
        exact_score = Decimal(review.overall_score_decimal or str(review.overall_score))
        if review.conclusion is not ReviewConclusion.revise or exact_score >= threshold:
            raise DomainError(ErrorCode.VALIDATION_FAILED, "Override is reserved for program-valid reviews below the score threshold.", request_id=review.review_id.root)

    def consume_technical_retry(self, state: Mapping[str, Any], invocation_key: str) -> dict[str, Any]:
        updated = dict(state)
        counters = dict(updated.get("technical_retries_by_operation", {}))
        count = int(counters.get(invocation_key, 0))
        if count >= self.TECHNICAL_RETRY_LIMIT:
            raise DomainError(ErrorCode.QUOTA_EXCEEDED, "The technical retry limit is exhausted.", request_id=invocation_key)
        counters[invocation_key] = count + 1
        updated["technical_retries_by_operation"] = counters
        return updated

    def consume_format_repair(self, state: Mapping[str, Any], invocation_key: str) -> dict[str, Any]:
        updated = dict(state)
        counters = dict(updated.get("format_repairs_by_operation", {}))
        count = int(counters.get(invocation_key, 0))
        if count >= self.FORMAT_REPAIR_LIMIT:
            raise DomainError(ErrorCode.QUOTA_EXCEEDED, "The format repair limit is exhausted.", request_id=invocation_key)
        counters[invocation_key] = count + 1
        updated["format_repairs_by_operation"] = counters
        return updated

    def require_budget(self, decision: BudgetDecision | None, *, request_id: str) -> None:
        if decision is None or decision.state is not BudgetState.allowed:
            raise DomainError(ErrorCode.QUOTA_EXCEEDED, "An explicit allowed budget decision is required.", request_id=request_id)
        if decision.amount is None and decision.reason is None:
            raise DomainError(ErrorCode.VALIDATION_FAILED, "Unknown budget must use a null amount and a reason.", request_id=request_id)
        if decision.amount is not None and decision.currency is None:
            raise DomainError(ErrorCode.VALIDATION_FAILED, "A known budget amount must carry its currency.", request_id=request_id)
