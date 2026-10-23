from decimal import Decimal

import pytest

from gw.agent_episode.scoring import (
    SCORING_RULE_VERSION,
    approval_matches_current_review,
    calculate_weighted_score,
    decimal_score,
    evaluate_score_gate,
)
from gw.agent_runtime.models import (
    ApprovalDecision,
    ApprovalRecord,
    ArtifactLifecycle,
    ArtifactRef,
    DimensionScore,
    Identifier,
    ReviewConclusion,
    ReviewRecord,
    StageId,
    Version,
)


def test_decimal_score_conversion():
    assert decimal_score("9.5") == Decimal("9.5")
    assert decimal_score(9) == Decimal("9")
    assert decimal_score(9.5) == Decimal("9.5")
    assert decimal_score(Decimal("9.5")) == Decimal("9.5")

def test_decimal_score_range_validation():
    with pytest.raises(ValueError, match="dimension score must be in the inclusive range 0..10"):
        decimal_score("-0.1")
    with pytest.raises(ValueError, match="dimension score must be in the inclusive range 0..10"):
        decimal_score("10.1")
    with pytest.raises(ValueError, match="dimension score must be in the inclusive range 0..10"):
        decimal_score("inf")
    with pytest.raises(ValueError, match="dimension score must be in the inclusive range 0..10"):
        decimal_score("nan")
    with pytest.raises(ValueError, match="dimension score must be a finite decimal number"):
        decimal_score("invalid")

def test_calculate_weighted_score():
    stage = StageId.brief
    scores = {
        "goal_and_boundaries": 9,
        "constraint_completeness": "8.5",
        "executability": Decimal("9.0"),
        "acceptance_criteria": 9.5
    }
    exact_scores, weighted = calculate_weighted_score(stage, scores)

    assert exact_scores["goal_and_boundaries"] == Decimal("9")
    assert exact_scores["constraint_completeness"] == Decimal("8.5")
    assert exact_scores["executability"] == Decimal("9.0")
    assert exact_scores["acceptance_criteria"] == Decimal("9.5")

    # 9*0.30 + 8.5*0.30 + 9*0.25 + 9.5*0.15 = 2.70 + 2.55 + 2.25 + 1.425 = 8.925
    assert weighted == Decimal("8.925")

def test_calculate_weighted_score_mismatch():
    stage = StageId.brief
    with pytest.raises(ValueError, match="dimension set mismatch"):
        calculate_weighted_score(stage, {"goal_and_boundaries": 9})
    with pytest.raises(ValueError, match="dimension set mismatch"):
        calculate_weighted_score(stage, {
            "goal_and_boundaries": 9,
            "constraint_completeness": 9,
            "executability": 9,
            "acceptance_criteria": 9,
            "extra_dim": 9,
        })

def test_calculate_weighted_score_weight_constraints():
    stage = StageId.brief
    scores = {
        "goal_and_boundaries": 9,
        "constraint_completeness": "8.5",
        "executability": Decimal("9.0"),
        "acceptance_criteria": 9.5
    }
    with pytest.raises(ValueError, match="score weights must sum to exactly one"):
        calculate_weighted_score(stage, scores, weights={
            "goal_and_boundaries": "0.3",
            "constraint_completeness": "0.3",
            "executability": "0.3",
            "acceptance_criteria": "0.2" # sums to 1.1
        })
    with pytest.raises(ValueError, match="score weights must be finite positive decimals"):
        calculate_weighted_score(stage, scores, weights={
            "goal_and_boundaries": "0.5",
            "constraint_completeness": "0.5",
            "executability": "-0.1",
            "acceptance_criteria": "0.1"
        })

def test_evaluate_score_gate():
    stage = StageId.brief
    scores = {
        "goal_and_boundaries": 9,
        "constraint_completeness": "9.5",
        "executability": Decimal("9.0"),
        "acceptance_criteria": 9.5
    }
    # Weighted score: 9*0.3 + 9.5*0.3 + 9*0.25 + 9.5*0.15 = 2.7 + 2.85 + 2.25 + 1.425 = 9.225
    result = evaluate_score_gate(stage, scores, program_passed=True, artifact_current=True)
    assert result.stage == stage
    assert result.weighted_score == "9.225"
    assert result.score_passed is True
    assert result.automatic_pass is True
    assert result.low_score_override_eligible is False

    # Below threshold (e.g. 8.0 overall)
    scores_low = {
        "goal_and_boundaries": 8,
        "constraint_completeness": "8",
        "executability": Decimal("8.0"),
        "acceptance_criteria": 8
    }
    result_low = evaluate_score_gate(stage, scores_low, program_passed=True, artifact_current=True)
    assert result_low.score_passed is False
    assert result_low.automatic_pass is False
    assert result_low.low_score_override_eligible is True

    # Bad flag combis
    result_not_current = evaluate_score_gate(stage, scores, program_passed=True, artifact_current=False)
    assert result_not_current.automatic_pass is False

    result_prog_fail = evaluate_score_gate(stage, scores, program_passed=False, artifact_current=True)
    assert result_prog_fail.automatic_pass is False

def test_evaluate_score_gate_threshold_validation():
    stage = StageId.brief
    scores = {
        "goal_and_boundaries": 9,
        "constraint_completeness": "9.5",
        "executability": Decimal("9.0"),
        "acceptance_criteria": 9.5
    }
    with pytest.raises(ValueError, match="threshold must be in the inclusive range 0..10"):
        evaluate_score_gate(stage, scores, program_passed=True, artifact_current=True, threshold=Decimal("-0.1"))
    with pytest.raises(ValueError, match="threshold must be in the inclusive range 0..10"):
        evaluate_score_gate(stage, scores, program_passed=True, artifact_current=True, threshold=Decimal("10.1"))


def _create_mock_artifact() -> ArtifactRef:
    return ArtifactRef(
        schema_version=1,
        artifact_id=Identifier("art-1"),
        version_id=Identifier("ver-1"),
        revision=Version(1),
        content_hash="0000000000000000000000000000000000000000000000000000000000000000",
        content_ref=Identifier("ref-1"),
        parent_version_ids=[],
        source_refs=[],
        lifecycle=ArtifactLifecycle.valid,
        created_at="2023-01-01T00:00:00Z"
    )

def _create_mock_review(artifact: ArtifactRef, rule_version: str = SCORING_RULE_VERSION) -> ReviewRecord:
    return ReviewRecord(
        schema_version=1,
        review_id=Identifier("rev-1"),
        run_id=Identifier("run-1"),
        artifact=artifact,
        rule_version=Identifier(rule_version),
        dimension_scores={
            "goal_and_boundaries": DimensionScore(score=9.0, evidence=["x"]),
            "constraint_completeness": DimensionScore(score=9.5, evidence=["x"]),
            "executability": DimensionScore(score=9.0, evidence=["x"]),
            "acceptance_criteria": DimensionScore(score=9.5, evidence=["x"]),
        },
        overall_score=9.225,
        program_validation_passed=True,
        program_checks=[],
        conclusion=ReviewConclusion.pass_,
        created_at="2023-01-01T00:00:00Z"
    )

def _create_mock_approval(decision: ApprovalDecision = ApprovalDecision.approve) -> ApprovalRecord:
    return ApprovalRecord(
        schema_version=1,
        approval_id=Identifier("app-1"),
        run_id=Identifier("run-1"),
        review_id=Identifier("rev-1"),
        artifact_version_id=Identifier("ver-1"),
        decision=decision,
        actor_id=Identifier("actor-1"),
        reason="looks good",
        expected_version=Version(1),
        created_at="2023-01-01T00:00:00Z"
    )


def test_approval_matches_current_review():
    stage = StageId.brief
    scores = {
        "goal_and_boundaries": 9,
        "constraint_completeness": "9.5",
        "executability": Decimal("9.0"),
        "acceptance_criteria": 9.5
    }
    score_result = evaluate_score_gate(stage, scores, program_passed=True, artifact_current=True)

    artifact = _create_mock_artifact()
    review = _create_mock_review(artifact)
    approval = _create_mock_approval()

    # Should pass
    assert approval_matches_current_review(
        stage, approval, review, artifact, score_result, artifact_current=True
    ) is True

    # Bad artifact lifecycle
    bad_artifact = artifact.model_copy(update={"lifecycle": ArtifactLifecycle.stale})
    assert approval_matches_current_review(
        stage, approval, review, bad_artifact, score_result, artifact_current=True
    ) is False

    # Not current
    assert approval_matches_current_review(
        stage, approval, review, artifact, score_result, artifact_current=False
    ) is False

    # Mismatch rule version
    bad_review = review.model_copy(update={"rule_version": Identifier("other-v1")})
    assert approval_matches_current_review(
        stage, approval, bad_review, artifact, score_result, artifact_current=True
    ) is False

    # Mismatch dimension score
    bad_review_scores = review.model_copy(update={
        "overall_score": 9.0, # modified
        "dimension_scores": {
            "goal_and_boundaries": DimensionScore(score=8.0, evidence=["x"]),
            "constraint_completeness": DimensionScore(score=9.5, evidence=["x"]),
            "executability": DimensionScore(score=9.0, evidence=["x"]),
            "acceptance_criteria": DimensionScore(score=9.5, evidence=["x"]),
        }
    })
    assert approval_matches_current_review(
        stage, approval, bad_review_scores, artifact, score_result, artifact_current=True
    ) is False
