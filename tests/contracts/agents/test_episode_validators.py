"""Tests for deterministic episode validators."""

import pytest

from gw.agent_runtime.models import (
    ArtifactLifecycle,
    ArtifactRef,
    ArtifactSourceRef,
    Identifier,
    Sha256Hex,
    StageId,
    UtcDateTime,
    Version,
)
from gw.agent_episode.models import EpisodeInputArtifact
from gw.agent_episode.validators import (
    input_set_issues,
    validate_approval_safety_inputs,
    validate_stage_payload,
)

def test_input_set_issues():
    # Helper to make artifacts
    def _make_ref(id_str):
        return ArtifactRef(
            schema_version=1,
            artifact_id=Identifier(id_str),
            version_id=Identifier(f"{id_str}_v1"),
            revision=Version(1),
            content_hash=Sha256Hex("0" * 64),
            content_ref=Identifier("ref"),
            parent_version_ids=[],
            source_refs=[],
            lifecycle=ArtifactLifecycle.valid,
            created_at=UtcDateTime("2024-01-01T00:00:00Z"),
        )

    # Empty inputs for a stage that requires them (e.g. outline_characters needs brief)
    assert "Input stage boundary mismatch" in input_set_issues(StageId.outline_characters, [])[0]

    # Duplicate inputs
    a = _make_ref("a")
    inputs = [
        EpisodeInputArtifact(stage=StageId.brief, artifact=a),
        EpisodeInputArtifact(stage=StageId.brief, artifact=a),
    ]
    assert "Duplicate input stage: brief" in input_set_issues(StageId.outline_characters, inputs)[0]

    # Stale/trashed input
    b = _make_ref("b")
    b.lifecycle = ArtifactLifecycle.trashed
    inputs2 = [
        EpisodeInputArtifact(stage=StageId.brief, artifact=b)
    ]
    assert "is not an effective valid artifact version" in input_set_issues(StageId.outline_characters, inputs2)[0]

    # Valid inputs
    valid_inputs = [
        EpisodeInputArtifact(stage=StageId.brief, artifact=_make_ref("c"))
    ]
    assert not input_set_issues(StageId.outline_characters, valid_inputs)

def test_validate_stage_payload():
    # Valid brief payload
    valid_brief_payload = {
        "creative_goal": "A goal",
        "format_description": "A format",
        "acceptance_criteria": ["Criteria 1"],
        "duration_request": {
            "target_minutes": 10,
            "tolerance_minutes": 2,
            "estimation_method": "word_count_estimate"
        },
        "clarifying_questions": []
    }
    content, validation = validate_stage_payload(
        StageId.brief,
        valid_brief_payload,
        input_payloads={},
        input_refs={},
        user_goal="I want a script."
    )
    assert validation.passed is True
    assert content.creative_goal == "A goal"

    # Invalid brief payload (schema mismatch: missing required fields or wrong type)
    invalid_brief_payload = {
        "duration_request": "10 minutes" # Should be an object
    }
    content_invalid, validation_invalid = validate_stage_payload(
        StageId.brief,
        invalid_brief_payload,
        input_payloads={},
        input_refs={},
        user_goal="I want a script."
    )
    assert content_invalid is None
    assert validation_invalid.passed is False
    assert any("duration_request" in check.message for check in validation_invalid.checks)

def test_validate_approval_safety_inputs():
    def _make_ref(source_refs=None, lifecycle=ArtifactLifecycle.valid):
        return ArtifactRef(
            schema_version=1,
            artifact_id=Identifier("a"),
            version_id=Identifier("a_v1"),
            revision=Version(1),
            content_hash=Sha256Hex("0" * 64),
            content_ref=Identifier("ref"),
            parent_version_ids=[],
            source_refs=source_refs or [],
            lifecycle=lifecycle,
            created_at=UtcDateTime("2024-01-01T00:00:00Z"),
        )

    # Valid approval safety inputs
    source_refs = [
        ArtifactSourceRef(artifact_id=Identifier("s1"), version_id=Identifier("v1"))
    ]
    expected_source_refs = [("s1", "v1")]
    ref = _make_ref(source_refs=source_refs)

    assert validate_approval_safety_inputs(
        ref, expected_source_refs=expected_source_refs, artifact_is_current=True
    ) is True

    # Reject: artifact_is_current=False
    assert validate_approval_safety_inputs(
        ref, expected_source_refs=expected_source_refs, artifact_is_current=False
    ) is False

    # Reject: mismatching expected_source_refs
    assert validate_approval_safety_inputs(
        ref, expected_source_refs=[("s2", "v2")], artifact_is_current=True
    ) is False

    # Reject: not valid lifecycle
    trashed_ref = _make_ref(source_refs=source_refs, lifecycle=ArtifactLifecycle.trashed)
    assert validate_approval_safety_inputs(
        trashed_ref, expected_source_refs=expected_source_refs, artifact_is_current=True
    ) is False
