"""Deterministic, decimal-based six-stage episode scoring and approval gates."""
from __future__ import annotations

from decimal import Decimal, InvalidOperation, localcontext
from typing import Mapping

from gw.agent_runtime.models import (
    ApprovalDecision,
    ApprovalRecord,
    ArtifactLifecycle,
    ArtifactRef,
    ReviewConclusion,
    ReviewRecord,
    StageId,
)

from .models import EpisodeScoreResult


SCORING_RULE_VERSION = "episode-text-scoring-v1.0.0"
PASS_THRESHOLD = Decimal("9")

STAGE_WEIGHTS: dict[StageId, dict[str, Decimal]] = {
    StageId.brief: {
        "goal_and_boundaries": Decimal("0.30"),
        "constraint_completeness": Decimal("0.30"),
        "executability": Decimal("0.25"),
        "acceptance_criteria": Decimal("0.15"),
    },
    StageId.outline_characters: {
        "brief_alignment": Decimal("0.25"),
        "causal_structure": Decimal("0.30"),
        "character_motivation_and_relations": Decimal("0.30"),
        "scope_fit": Decimal("0.15"),
    },
    StageId.full_script: {
        "setting_consistency": Decimal("0.20"),
        "causal_completeness": Decimal("0.25"),
        "character_and_dialogue": Decimal("0.25"),
        "pace_and_duration": Decimal("0.20"),
        "shootability": Decimal("0.10"),
    },
    StageId.scene_script: {
        "fidelity": Decimal("0.35"),
        "scene_completeness_and_continuity": Decimal("0.25"),
        "action_and_dialogue": Decimal("0.25"),
        "production_feasibility": Decimal("0.15"),
    },
    StageId.storyboard_text: {
        "script_coverage": Decimal("0.30"),
        "shot_expression_and_continuity": Decimal("0.30"),
        "pace_and_duration": Decimal("0.20"),
        "production_information": Decimal("0.20"),
    },
    StageId.delivery_check: {
        "completeness": Decimal("0.30"),
        "cross_artifact_consistency": Decimal("0.35"),
        "brief_conformance": Decimal("0.25"),
        "version_provenance": Decimal("0.10"),
    },
}

DIMENSION_DESCRIPTIONS: dict[StageId, dict[str, str]] = {
    StageId.brief: {
        "goal_and_boundaries": "目标清晰，范围与排除项明确",
        "constraint_completeness": "约束及必要参数完整；未知项应提问而非猜测",
        "executability": "信息足以指导后续创作和判断",
        "acceptance_criteria": "验收条件可识别且可追踪",
    },
    StageId.outline_characters: {
        "brief_alignment": "大纲与经确认的简报目标一致",
        "causal_structure": "事件具有清楚、连贯的因果推进",
        "character_motivation_and_relations": "人物动机、关系和变化有依据",
        "scope_fit": "结构体量适合明确的需求，不套固定幕数",
    },
    StageId.full_script: {
        "setting_consistency": "设定与已批准简报、大纲和人物一致",
        "causal_completeness": "完整剧本的叙事因果链完整",
        "character_and_dialogue": "人物行为、动机与对白可信且有辨识度",
        "pace_and_duration": "节奏及估时方法与已确认需求相符",
        "shootability": "场景动作和文字表达可供拍摄执行",
    },
    StageId.scene_script: {
        "fidelity": "分场忠实覆盖当前完整剧本版本",
        "scene_completeness_and_continuity": "场景完整、顺序与连续性清楚",
        "action_and_dialogue": "场景内动作和对白可定位、可理解",
        "production_feasibility": "制作信息具体且不擅自扩展媒体执行",
    },
    StageId.storyboard_text: {
        "script_coverage": "文字分镜可追溯覆盖当前完整剧本",
        "shot_expression_and_continuity": "镜头表达连贯，景别或动作信息清楚",
        "pace_and_duration": "镜头节奏与明确时长需求相适配",
        "production_information": "必要文字制作信息充分，不生成媒体",
    },
    StageId.delivery_check: {
        "completeness": "要求的文字产物均存在且可交付",
        "cross_artifact_consistency": "不同版本间人物、场景与叙事信息一致",
        "brief_conformance": "交付内容符合已确认的需求",
        "version_provenance": "准确版本及其上游来源可追溯",
    },
}


def decimal_score(value: Decimal | int | float | str) -> Decimal:
    """Convert through text to avoid importing binary-float rounding artifacts."""
    try:
        result = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError("dimension score must be a finite decimal number") from exc
    if not result.is_finite() or result < 0 or result > 10:
        raise ValueError("dimension score must be in the inclusive range 0..10")
    return result


def _decimal_text(value: Decimal) -> str:
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return "0" if text in {"", "-0"} else text


def calculate_weighted_score(
    stage: StageId,
    dimension_scores: Mapping[str, Decimal | int | float | str],
    *,
    weights: Mapping[str, Decimal | int | float | str] | None = None,
) -> tuple[dict[str, Decimal], Decimal]:
    """Return exact dimension values and an unrounded weighted score."""
    active_weights = weights or STAGE_WEIGHTS[stage]
    if set(dimension_scores) != set(active_weights):
        missing = sorted(set(active_weights) - set(dimension_scores))
        extra = sorted(set(dimension_scores) - set(active_weights))
        raise ValueError(f"dimension set mismatch; missing={missing}, extra={extra}")
    exact_weights = {name: Decimal(str(weight)) for name, weight in active_weights.items()}
    if any(not value.is_finite() or value <= 0 for value in exact_weights.values()):
        raise ValueError("score weights must be finite positive decimals")
    if sum(exact_weights.values(), Decimal("0")) != Decimal("1"):
        raise ValueError("score weights must sum to exactly one")
    exact_scores = {name: decimal_score(dimension_scores[name]) for name in exact_weights}
    products = [(exact_scores[name], weight) for name, weight in exact_weights.items()]
    max_adjusted = max(
        (score.adjusted() + weight.adjusted() + 1)
        for score, weight in products
        if score != 0
    ) if any(score != 0 for score, _ in products) else 0
    min_exponent = min(
        score.as_tuple().exponent + weight.as_tuple().exponent
        for score, weight in products
    )
    precision = max(28, max_adjusted - min_exponent + len(str(len(products))) + 2)
    with localcontext() as context:
        context.prec = precision
        weighted = sum(
            (score * weight for score, weight in products),
            Decimal("0"),
        )
    return exact_scores, weighted


def evaluate_score_gate(
    stage: StageId,
    dimension_scores: Mapping[str, Decimal | int | float | str],
    *,
    program_passed: bool,
    artifact_current: bool,
    threshold: Decimal = PASS_THRESHOLD,
    weights: Mapping[str, Decimal | int | float | str] | None = None,
) -> EpisodeScoreResult:
    """Compute the automatic gate; model-supplied overall/pass fields are ignored."""
    if not threshold.is_finite() or threshold < 0 or threshold > 10:
        raise ValueError("threshold must be in the inclusive range 0..10")
    exact_scores, weighted = calculate_weighted_score(stage, dimension_scores, weights=weights)
    score_passed = weighted >= threshold
    automatic_pass = score_passed and program_passed and artifact_current
    return EpisodeScoreResult(
        stage=stage,
        dimension_scores={key: _decimal_text(value) for key, value in exact_scores.items()},
        weighted_score=_decimal_text(weighted),
        threshold=_decimal_text(threshold),
        score_passed=score_passed,
        program_passed=program_passed,
        artifact_current=artifact_current,
        automatic_pass=automatic_pass,
        low_score_override_eligible=(not score_passed and program_passed and artifact_current),
    )


def approval_matches_current_review(
    stage: StageId,
    approval: ApprovalRecord,
    review: ReviewRecord,
    artifact: ArtifactRef,
    score_result: EpisodeScoreResult,
    *,
    artifact_current: bool,
) -> bool:
    """Validate exact-version approval using the unrounded score snapshot.

    The shared ReviewRecord has numeric float fields for interoperability. The
    plugin's exact ``EpisodeScoreResult`` travels with the review use-case result
    so approval never has to reconstruct an edge score from rounded floats.
    Neither a high model score nor an override can bypass deterministic checks.
    """
    try:
        exact_threshold = decimal_score(score_result.threshold)
    except ValueError:
        return False
    if (
        artifact.lifecycle is not ArtifactLifecycle.valid
        or not artifact_current
        or not score_result.artifact_current
        or not score_result.program_passed
        or not review.program_validation_passed
        or score_result.stage is not stage
        or exact_threshold != PASS_THRESHOLD
        or approval.run_id.root != review.run_id.root
        or review.artifact.version_id.root != artifact.version_id.root
        or approval.artifact_version_id.root != artifact.version_id.root
        or approval.review_id.root != review.review_id.root
        or review.rule_version.root != SCORING_RULE_VERSION
    ):
        return False
    try:
        exact_scores, weighted = calculate_weighted_score(stage, score_result.dimension_scores)
        recorded_weight = decimal_score(score_result.weighted_score)
        recomputed_pass = weighted >= PASS_THRESHOLD
        dimensions_match = set(review.dimension_scores) == set(exact_scores) == set(score_result.dimension_scores)
        numeric_record_matches = dimensions_match and all(
            float(exact_scores[key]) == review.dimension_scores[key].score
            for key in exact_scores
        ) and float(weighted) == review.overall_score
    except (KeyError, ValueError, InvalidOperation):
        return False
    if (
        weighted != recorded_weight
        or recomputed_pass != score_result.score_passed
        or score_result.automatic_pass != (recomputed_pass and score_result.program_passed and score_result.artifact_current)
        or not numeric_record_matches
    ):
        return False
    if approval.decision is ApprovalDecision.approve:
        return recomputed_pass and review.conclusion is ReviewConclusion.pass_
    if approval.decision is ApprovalDecision.override:
        return not recomputed_pass and review.conclusion is ReviewConclusion.revise
    return False


__all__ = [
    "DIMENSION_DESCRIPTIONS",
    "PASS_THRESHOLD",
    "SCORING_RULE_VERSION",
    "STAGE_WEIGHTS",
    "approval_matches_current_review",
    "calculate_weighted_score",
    "decimal_score",
    "evaluate_score_gate",
]
