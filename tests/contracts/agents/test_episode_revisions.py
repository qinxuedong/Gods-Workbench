"""Tests for episode revision helpers."""

import pytest

from gw.agent_episode.models import ReviewFinding, RevisionResponse
from gw.agent_episode.revisions import propose_rollback, validate_revision_responses
from gw.agent_runtime.models import ArtifactSourceRef, StageId


def test_validate_revision_responses_success():
    """Ensure exact match between findings and responses passes."""
    findings = [
        ReviewFinding(
            finding_id="f1",
            location="sec 1",
            severity="low",
            category="optional",
            description="d1",
            required_fix="r1",
        ),
        ReviewFinding(
            finding_id="f2",
            location="sec 2",
            severity="high",
            category="must_fix",
            description="d2",
            required_fix="r2",
        ),
    ]
    responses = [
        RevisionResponse(finding_id="f1", response="r1"),
        RevisionResponse(finding_id="f2", response="r2"),
    ]

    check = validate_revision_responses(findings, responses)
    assert check.passed is True
    assert not check.missing_finding_ids
    assert not check.unexpected_finding_ids
    assert not check.duplicate_response_ids


def test_validate_revision_responses_mismatch():
    """Ensure missing, unexpected, and duplicate responses are correctly identified."""
    findings = [
        ReviewFinding(
            finding_id="f1",
            location="sec 1",
            severity="low",
            category="optional",
            description="d1",
            required_fix="r1",
        ),
        ReviewFinding(
            finding_id="f2",
            location="sec 2",
            severity="high",
            category="must_fix",
            description="d2",
            required_fix="r2",
        ),
    ]
    responses = [
        RevisionResponse(finding_id="f1", response="r1"),
        RevisionResponse(finding_id="f1", response="duplicate r1"),
        RevisionResponse(finding_id="f3", response="unexpected r3"),
    ]

    check = validate_revision_responses(findings, responses)
    assert check.passed is False
    assert check.missing_finding_ids == ("f2",)
    assert check.unexpected_finding_ids == ("f3",)
    assert check.duplicate_response_ids == ("f1",)


def test_propose_rollback_success():
    """Test successful creation of a rollback proposal."""
    findings = [
        ReviewFinding(
            finding_id="f1",
            location="sec 1",
            severity="high",
            category="must_fix",
            description="critical error",
            required_fix="fix it",
        )
    ]
    reviewed_ref = ArtifactSourceRef(artifact_id="art1", version_id="v1")
    source_refs = [
        ArtifactSourceRef(artifact_id="src1", version_id="sv1"),
    ]

    proposal = propose_rollback(
        from_stage=StageId.scene_script,
        to_stage=StageId.brief,
        reason="rewrite needed",
        findings=findings,
        reviewed_artifact_ref=reviewed_ref,
        source_refs=source_refs,
    )

    assert proposal.from_stage == StageId.scene_script
    assert proposal.to_stage == StageId.brief
    assert proposal.reason == "rewrite needed"
    assert proposal.reviewed_artifact_ref == reviewed_ref
    assert proposal.source_refs == source_refs
    assert len(proposal.evidence) == 1
    assert proposal.evidence[0].finding_id == "f1"
    assert proposal.evidence[0].source_artifact_ref == reviewed_ref


def test_propose_rollback_invalid_stage():
    """Ensure proposing rollback to a later or same stage raises an error."""
    findings = [
        ReviewFinding(
            finding_id="f1",
            location="sec 1",
            severity="high",
            category="must_fix",
            description="critical error",
            required_fix="fix it",
        )
    ]
    reviewed_ref = ArtifactSourceRef(artifact_id="art1", version_id="v1")
    source_refs = [
        ArtifactSourceRef(artifact_id="src1", version_id="sv1"),
    ]

    # Rollback to later stage
    with pytest.raises(ValueError, match="rollback target must be a strictly earlier episode stage"):
        propose_rollback(
            from_stage=StageId.brief,
            to_stage=StageId.scene_script,
            reason="invalid rollback",
            findings=findings,
            reviewed_artifact_ref=reviewed_ref,
            source_refs=source_refs,
        )

    # Rollback to same stage
    with pytest.raises(ValueError, match="rollback target must be a strictly earlier episode stage"):
        propose_rollback(
            from_stage=StageId.brief,
            to_stage=StageId.brief,
            reason="invalid rollback",
            findings=findings,
            reviewed_artifact_ref=reviewed_ref,
            source_refs=source_refs,
        )


def test_propose_rollback_missing_fields():
    """Ensure missing required fields raise an error."""
    findings = [
        ReviewFinding(
            finding_id="f1",
            location="sec 1",
            severity="high",
            category="must_fix",
            description="critical error",
            required_fix="fix it",
        )
    ]
    reviewed_ref = ArtifactSourceRef(artifact_id="art1", version_id="v1")
    source_refs = [
        ArtifactSourceRef(artifact_id="src1", version_id="sv1"),
    ]

    # Missing reason
    with pytest.raises(ValueError, match="rollback proposals require a reason, at least one finding, and source version refs"):
        propose_rollback(
            from_stage=StageId.scene_script,
            to_stage=StageId.brief,
            reason="   ",
            findings=findings,
            reviewed_artifact_ref=reviewed_ref,
            source_refs=source_refs,
        )

    # Missing findings
    with pytest.raises(ValueError, match="rollback proposals require a reason, at least one finding, and source version refs"):
        propose_rollback(
            from_stage=StageId.scene_script,
            to_stage=StageId.brief,
            reason="valid reason",
            findings=[],
            reviewed_artifact_ref=reviewed_ref,
            source_refs=source_refs,
        )

    # Missing source_refs
    with pytest.raises(ValueError, match="rollback proposals require a reason, at least one finding, and source version refs"):
        propose_rollback(
            from_stage=StageId.scene_script,
            to_stage=StageId.brief,
            reason="valid reason",
            findings=findings,
            reviewed_artifact_ref=reviewed_ref,
            source_refs=[],
        )
