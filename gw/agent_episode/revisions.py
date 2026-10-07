"""Content-revision response checks and evidence-only rollback proposals."""
from __future__ import annotations

from dataclasses import dataclass

from pydantic import Field

from gw.agent_runtime.models import ArtifactSourceRef, StageId

from .models import EpisodeModel, ReviewFinding, RevisionResponse, STAGE_ORDER


@dataclass(frozen=True, slots=True)
class RevisionResponseCheck:
    passed: bool
    missing_finding_ids: tuple[str, ...] = ()
    unexpected_finding_ids: tuple[str, ...] = ()
    duplicate_response_ids: tuple[str, ...] = ()


def validate_revision_responses(
    findings: list[ReviewFinding], responses: list[RevisionResponse]
) -> RevisionResponseCheck:
    """Require a distinct, explicit response for every returned finding."""
    expected = {finding.finding_id for finding in findings}
    response_ids = [response.finding_id for response in responses]
    actual = set(response_ids)
    duplicates = tuple(sorted(value for value in actual if response_ids.count(value) > 1))
    missing = tuple(sorted(expected - actual))
    unexpected = tuple(sorted(actual - expected))
    return RevisionResponseCheck(
        passed=not (missing or unexpected or duplicates),
        missing_finding_ids=missing,
        unexpected_finding_ids=unexpected,
        duplicate_response_ids=duplicates,
    )


class RollbackEvidence(EpisodeModel):
    finding_id: str = Field(min_length=1)
    location: str = Field(min_length=1)
    description: str = Field(min_length=1)
    source_artifact_ref: ArtifactSourceRef


class RollbackProposal(EpisodeModel):
    """A proposal only; the runtime owns authorization, limits, and CAS."""

    from_stage: StageId
    to_stage: StageId
    reason: str = Field(min_length=1)
    reviewed_artifact_ref: ArtifactSourceRef
    evidence: list[RollbackEvidence] = Field(min_length=1)
    source_refs: list[ArtifactSourceRef] = Field(min_length=1)
    requires_runtime_validation: bool = True


def propose_rollback(
    *,
    from_stage: StageId,
    to_stage: StageId,
    reason: str,
    findings: list[ReviewFinding],
    reviewed_artifact_ref: ArtifactSourceRef,
    source_refs: list[ArtifactSourceRef],
) -> RollbackProposal:
    """Create a bounded upstream proposal and require traceable evidence."""
    if STAGE_ORDER.index(to_stage) >= STAGE_ORDER.index(from_stage):
        raise ValueError("rollback target must be a strictly earlier episode stage")
    if not reason.strip() or not findings or not source_refs:
        raise ValueError("rollback proposals require a reason, at least one finding, and source version refs")
    evidence = [
        RollbackEvidence(
            finding_id=finding.finding_id,
            location=finding.location,
            description=finding.description,
            source_artifact_ref=reviewed_artifact_ref,
        )
        for finding in findings
    ]
    return RollbackProposal(
        from_stage=from_stage,
        to_stage=to_stage,
        reason=reason,
        reviewed_artifact_ref=reviewed_artifact_ref,
        evidence=evidence,
        source_refs=source_refs,
    )


__all__ = [
    "RevisionResponseCheck",
    "RollbackEvidence",
    "RollbackProposal",
    "propose_rollback",
    "validate_revision_responses",
]
