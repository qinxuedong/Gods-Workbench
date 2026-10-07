"""Typed episode-text payloads layered over the shared runtime contracts.

This package deliberately models text artifacts only. Execution state, storage,
leases, CAS transitions, and HTTP records remain owned by ``agent_runtime``.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from gw.agent_runtime.models import (
    ApprovalRecord,
    ArtifactRef,
    AttemptKind,
    ProgramCheck,
    ReviewRecord,
    StageAttempt,
    StageId,
)


OpaqueText = Annotated[str, Field(min_length=1, max_length=20_000)]
OpaqueId = Annotated[str, Field(min_length=1, max_length=256, pattern=r"^\S(?:.*\S)?$")]


class EpisodeModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class DurationRequest(EpisodeModel):
    target_minutes: float | None = Field(ge=0.1)
    tolerance_minutes: float | None = Field(ge=0)
    estimation_method: Literal["dialogue_estimate", "word_count_estimate", "user_defined"] | None


class BriefContent(EpisodeModel):
    creative_goal: OpaqueText
    intended_audience: OpaqueText | None = None
    format_description: OpaqueText
    constraints: list[OpaqueText] = Field(default_factory=list)
    acceptance_criteria: list[OpaqueText] = Field(min_length=1)
    duration_request: DurationRequest | None
    clarifying_questions: list[OpaqueText] = Field(default_factory=list)


class OutlineSection(EpisodeModel):
    section_id: OpaqueId
    title: OpaqueText
    summary: OpaqueText
    causal_purpose: OpaqueText | None = None


class CharacterSheet(EpisodeModel):
    character_id: OpaqueId
    name: OpaqueText
    motivation: OpaqueText
    description: OpaqueText
    arc: OpaqueText | None = None


class CharacterRelationship(EpisodeModel):
    from_character_id: OpaqueId
    to_character_id: OpaqueId
    description: OpaqueText


class OutlineCharactersContent(EpisodeModel):
    source_brief_version_id: OpaqueId
    logline: OpaqueText
    sections: list[OutlineSection] = Field(min_length=1)
    characters: list[CharacterSheet] = Field(default_factory=list)
    relationships: list[CharacterRelationship] = Field(default_factory=list)


class FullScriptScene(EpisodeModel):
    scene_id: OpaqueId
    title: OpaqueText
    time: OpaqueText
    location: OpaqueText
    content: OpaqueText
    character_ids: list[OpaqueId] = Field(default_factory=list)
    outline_section_ids: list[OpaqueId] = Field(default_factory=list)


class FullScriptContent(EpisodeModel):
    source_brief_version_id: OpaqueId
    source_outline_version_id: OpaqueId
    title: OpaqueText
    scenes: list[FullScriptScene] = Field(min_length=1)


class SceneScriptScene(EpisodeModel):
    scene_id: OpaqueId
    title: OpaqueText
    time: OpaqueText
    location: OpaqueText
    content: OpaqueText
    character_ids: list[OpaqueId] = Field(default_factory=list)


class SceneScriptContent(EpisodeModel):
    source_full_script_version_id: OpaqueId
    scenes: list[SceneScriptScene] = Field(min_length=1)


class StoryboardShot(EpisodeModel):
    shot_id: OpaqueId
    scene_id: OpaqueId
    description: OpaqueText
    camera: OpaqueText | None = None
    movement: OpaqueText | None = None
    duration_seconds: float | None = Field(default=None, gt=0)
    character_ids: list[OpaqueId] = Field(default_factory=list)


class StoryboardTextContent(EpisodeModel):
    source_full_script_version_id: OpaqueId
    source_scene_script_version_id: OpaqueId
    shots: list[StoryboardShot] = Field(min_length=1)


class DeliveryArtifactRef(EpisodeModel):
    stage: StageId
    artifact_id: OpaqueId
    version_id: OpaqueId


class DeliveryCheckContent(EpisodeModel):
    checked_artifacts: list[DeliveryArtifactRef] = Field(min_length=1)
    verified_requirements: list[OpaqueText] = Field(min_length=1)
    findings: list[OpaqueText] = Field(default_factory=list)
    summary: OpaqueText


class ReviewDimension(EpisodeModel):
    score: Annotated[Decimal, Field(ge=0, le=10)]
    evidence: list[OpaqueText] = Field(min_length=1)


class ReviewFinding(EpisodeModel):
    finding_id: OpaqueId
    location: OpaqueText
    severity: Literal["low", "medium", "high", "critical"]
    category: Literal["must_fix", "optional"]
    description: OpaqueText
    required_fix: OpaqueText


class IndependentReviewOutput(EpisodeModel):
    dimension_scores: dict[str, ReviewDimension]
    findings: list[ReviewFinding] = Field(default_factory=list)
    summary: OpaqueText
    rollback_proposal: dict[str, Any] | None = None


class EpisodeInputArtifact(EpisodeModel):
    stage: StageId
    artifact: ArtifactRef


class RevisionResponse(EpisodeModel):
    finding_id: OpaqueId
    response: OpaqueText


class HumanRevisionFeedback(EpisodeModel):
    approval_id: OpaqueId
    review_id: OpaqueId
    artifact_version_id: OpaqueId
    actor_id: OpaqueId
    reason: OpaqueText


class EpisodeStageRequest(EpisodeModel):
    run_id: OpaqueId
    project_id: OpaqueId
    request_id: OpaqueId
    stage_attempt_id: OpaqueId
    stage: StageId
    attempt_kind: AttemptKind = AttemptKind.initial
    attempt_number: int = Field(ge=1)
    artifact_id: OpaqueId
    parent_version_id: OpaqueId | None = None
    user_goal: OpaqueText | None = None
    inputs: list[EpisodeInputArtifact] = Field(default_factory=list)
    revision_findings: list[ReviewFinding] = Field(default_factory=list)
    revision_responses: list[RevisionResponse] = Field(default_factory=list)
    human_revision_feedback: HumanRevisionFeedback | None = None
    clarification_answers: dict[str, str] = Field(default_factory=dict)


class EpisodeProgramValidation(EpisodeModel):
    checks: list[ProgramCheck]

    @property
    def passed(self) -> bool:
        return all(check.passed for check in self.checks)


class EpisodeScoreResult(EpisodeModel):
    stage: StageId
    dimension_scores: dict[str, str]
    weighted_score: str
    threshold: str = "9"
    score_passed: bool
    program_passed: bool
    artifact_current: bool
    automatic_pass: bool
    low_score_override_eligible: bool


class EpisodeStageResult(EpisodeModel):
    artifact: ArtifactRef
    attempt: StageAttempt
    review: ReviewRecord
    program_validation: EpisodeProgramValidation
    score: EpisodeScoreResult
    creator_invocation_id: OpaqueId
    reviewer_invocation_id: OpaqueId
    prompt_refs: list[str]


class EpisodeApprovalEvidence(EpisodeModel):
    artifact: ArtifactRef
    review: ReviewRecord
    approval: ApprovalRecord


STAGE_CONTENT_MODELS: dict[StageId, type[EpisodeModel]] = {
    StageId.brief: BriefContent,
    StageId.outline_characters: OutlineCharactersContent,
    StageId.full_script: FullScriptContent,
    StageId.scene_script: SceneScriptContent,
    StageId.storyboard_text: StoryboardTextContent,
    StageId.delivery_check: DeliveryCheckContent,
}

STAGE_ORDER: tuple[StageId, ...] = (
    StageId.brief,
    StageId.outline_characters,
    StageId.full_script,
    StageId.scene_script,
    StageId.storyboard_text,
    StageId.delivery_check,
)

# The final two stages intentionally read the complete, approved script version;
# storyboard also receives its textual scene breakdown. This is a data boundary,
# not an execution graph owned by this package.
STAGE_INPUTS: dict[StageId, tuple[StageId, ...]] = {
    StageId.brief: (),
    StageId.outline_characters: (StageId.brief,),
    StageId.full_script: (StageId.brief, StageId.outline_characters),
    StageId.scene_script: (StageId.full_script,),
    StageId.storyboard_text: (StageId.full_script, StageId.scene_script),
    StageId.delivery_check: (
        StageId.brief,
        StageId.outline_characters,
        StageId.full_script,
        StageId.scene_script,
        StageId.storyboard_text,
    ),
}

__all__ = [
    "BriefContent",
    "CharacterRelationship",
    "CharacterSheet",
    "DeliveryArtifactRef",
    "DeliveryCheckContent",
    "DurationRequest",
    "EpisodeInputArtifact",
    "EpisodeProgramValidation",
    "EpisodeScoreResult",
    "EpisodeStageRequest",
    "EpisodeStageResult",
    "FullScriptContent",
    "FullScriptScene",
    "HumanRevisionFeedback",
    "IndependentReviewOutput",
    "OutlineCharactersContent",
    "OutlineSection",
    "ReviewFinding",
    "ReviewDimension",
    "RevisionResponse",
    "STAGE_CONTENT_MODELS",
    "STAGE_INPUTS",
    "STAGE_ORDER",
    "SceneScriptContent",
    "SceneScriptScene",
    "StoryboardShot",
    "StoryboardTextContent",
]
