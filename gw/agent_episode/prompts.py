"""New, versioned text-only prompt pack for the episode plugin.

Every creator and reviewer prompt is authored here for this isolated module.
Nothing is imported from a host prompt library; adapters must pin the returned
ID/version/hash through the shared PromptRepository before invoking a model.
"""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from typing import Literal

from pydantic import Field, create_model

from gw.agent_runtime.models import PromptTemplate, PromptTemplateRef, StageId

from .models import (
    EpisodeModel,
    IndependentReviewOutput,
    OpaqueText,
    ReviewDimension,
    ReviewFinding,
    RevisionResponse,
    STAGE_CONTENT_MODELS,
)
from .revisions import RollbackProposal
from .scoring import DIMENSION_DESCRIPTIONS, SCORING_RULE_VERSION


PROMPT_PACK_VERSION = "1.2.0"
PROMPT_PACK_ID = "gw.episode.text.v1"
Role = Literal["creator", "reviewer"]


_STAGE_CREATOR_INSTRUCTIONS: dict[StageId, str] = {
    StageId.brief: (
        "Create a concise, decision-ready creative brief from the user's goal. "
        "Capture objective, audience, format, constraints, and testable acceptance criteria. "
        "If an approximate ten-minute request lacks an explicit tolerance or estimation method, "
        "leave those parameters null and ask clear questions; do not choose values on the user's behalf."
    ),
    StageId.outline_characters: (
        "Create a causal story outline and character profiles from the supplied brief only. "
        "Use as many outline sections and characters as the premise needs; do not force a fixed act count. "
        "Give stable IDs to sections and characters and reference only those IDs."
    ),
    StageId.full_script: (
        "Write the complete script, not a synopsis or a sample. Use the approved brief and outline/person "
        "version as the only creative sources. Include every scene required for the whole story and keep "
        "scene, character, and outline references traceable. Do not assume a platform or aspect ratio."
    ),
    StageId.scene_script: (
        "Break down the entire supplied approved full-script version into its scenes without omitting, "
        "adding, or reordering source scene IDs. Preserve the story and add useful textual scene direction. "
        "Do not create images, video, sound, or production jobs."
    ),
    StageId.storyboard_text: (
        "Create a text-only storyboard from the approved full script and its scene breakdown. Choose a "
        "variable number of shots appropriate to the material; there is no fixed shot-count limit. "
        "Give each shot a unique ID and cite a valid source scene and character IDs. Do not generate media."
    ),
    StageId.delivery_check: (
        "Prepare a final text delivery check against the exact supplied versions of all five prior stages. "
        "List the exact artifact/version pairs you checked, summarize completeness and consistency, and "
        "record concrete findings. Do not claim that media was made, publish anything, or invent a version."
    ),
}

_STAGE_REVIEWER_INSTRUCTIONS: dict[StageId, str] = {
    StageId.brief: "Independently assess whether the brief is clear, complete, actionable, and appropriately asks for missing constraints.",
    StageId.outline_characters: "Independently assess fit to the brief, causal structure, character motivation/relations, and scope without assuming a fixed act count.",
    StageId.full_script: "Independently assess the complete script against its exact brief and outline sources; distinguish completeness, causality, dialogue, pacing, and shootability.",
    StageId.scene_script: "Independently assess the scene breakdown against the exact complete-script version and verify continuity and coverage.",
    StageId.storyboard_text: "Independently assess textual shot coverage, continuity, pacing, and production information. Do not reward or require a fixed number of shots.",
    StageId.delivery_check: "Independently assess the final text checklist and exact source versions; do not infer external publishing or media generation.",
}


@dataclass(frozen=True, slots=True)
class PromptSpec:
    stage: StageId
    role: Role
    template: PromptTemplate


def _template_body(stage: StageId, role: Role) -> str:
    template_id = f"episode.text.{stage.value}.{role}"
    if role == "creator":
        task = _STAGE_CREATOR_INSTRUCTIONS[stage]
        contract = STAGE_CONTENT_MODELS[stage].__name__
        return (
            f"episode-template={template_id}; version={PROMPT_PACK_VERSION}\n"
            "You are the text creator for a single Gods-Workbench episode stage.\n"
            f"Task: {task}\n"
            f"Return exactly one JSON object with `artifact_content` matching the supplied {contract} JSON Schema, "
            "and `revision_responses` as one explicit response for each supplied prior finding. "
            "When there are no findings, return an empty response array. Do not claim a fix absent from artifact_content; "
            "explain findings you cannot address. Do not return markdown fences, hidden reasoning, an overall score, "
            "or a pass decision. Use only supplied inputs and preserve exact source version IDs. "
            "When `human_revision_feedback` is present, treat its bound rejection reason as the user's revision direction; "
            "address it in the artifact when consistent with the original goal and program constraints."
        )
    descriptions = DIMENSION_DESCRIPTIONS[stage]
    dimension_text = "\n".join(
        f"- {key}: {description}" for key, description in descriptions.items()
    )
    return (
        f"episode-template={template_id}; version={PROMPT_PACK_VERSION}\n"
        "You are an independent reviewer for one Gods-Workbench episode stage. "
        "This is a fresh review call, not a continuation of the creator conversation.\n"
        f"Task: {_STAGE_REVIEWER_INSTRUCTIONS[stage]}\n"
        f"Scoring rule: {SCORING_RULE_VERSION}. Score each dimension from 0 to 10.\n"
        f"Dimensions:\n{dimension_text}\n"
        "Return only the requested JSON object. Report specific findings with resolvable JSON Pointer or JSONPath locations and actionable fixes. "
        "A sound artifact may have zero findings; do not invent a minimum defect count. "
        "When `human_revision_feedback` is present, assess whether the exact requested change is reflected in the artifact; "
        "the reason itself is not evidence that the change was made or is correct. "
        "If an upstream version must be reconsidered, you may return a structured rollback_proposal with exact evidence refs; "
        "the proposal is not an action and the runtime decides whether to execute it. "
        "Do not provide an overall score or pass field: the program computes those. "
        "Do not reveal hidden reasoning."
    )


def _template(stage: StageId, role: Role) -> PromptTemplate:
    template_id = f"episode.text.{stage.value}.{role}"
    body = _template_body(stage, role)
    digest = sha256(body.encode("utf-8")).hexdigest()
    return PromptTemplate(
        ref=PromptTemplateRef(
            template_id=template_id,
            version=PROMPT_PACK_VERSION,
            content_hash=digest,
        ),
        purpose=f"{stage.value}.{role}",
        parameter_schema={
            "type": "object",
            "required": ["stage", "inputs", "input_refs", "revision_feedback", "human_revision_feedback"],
            "properties": {
                "stage": {"type": "string"},
                "user_goal": {"type": ["string", "null"]},
                "inputs": {"type": "object"},
                "input_refs": {"type": "object"},
                "artifact_version_id": {"type": "string"},
                "artifact": {"type": "object"},
                "revision_feedback": {"type": "object"},
                "human_revision_feedback": {
                    "anyOf": [
                        {
                            "type": "object",
                            "required": ["approval_id", "review_id", "artifact_version_id", "actor_id", "reason"],
                            "properties": {
                                "approval_id": {"type": "string"},
                                "review_id": {"type": "string"},
                                "artifact_version_id": {"type": "string"},
                                "actor_id": {"type": "string"},
                                "reason": {"type": "string"},
                            },
                            "additionalProperties": False,
                        },
                        {"type": "null"},
                    ]
                },
            },
            "additionalProperties": False,
        },
        body=body,
    )


PROMPT_CATALOG: dict[tuple[StageId, Role], PromptTemplate] = {
    (stage, role): _template(stage, role)
    for stage in StageId
    for role in ("creator", "reviewer")
}


CREATOR_OUTPUT_MODELS = {
    stage: create_model(
        f"{STAGE_CONTENT_MODELS[stage].__name__}CreatorOutput",
        __base__=EpisodeModel,
        artifact_content=(STAGE_CONTENT_MODELS[stage], ...),
        revision_responses=(list[RevisionResponse], ...),
    )
    for stage in StageId
}


ORCHESTRATOR_BODY = (
    "episode-template=episode.orchestrator; version=1.1.0\n"
    "Propose exactly one bounded action for the current episode run. Return JSON fields action, capability, target_stage, question. "
    "Allowed actions: clarify, select_capability, create, review, revise, rollback. Use only capabilities in registered_capabilities. "
    "When human_revision_feedback is present, prefer revise and preserve its exact user direction for the creator and reviewer. "
    "Do not claim an action has executed or decide authorization, score, quota, approval, or state transitions."
)
ORCHESTRATOR_TEMPLATE = PromptTemplate(
    ref=PromptTemplateRef(
        template_id="episode.orchestrator",
        version="1.1.0",
        content_hash=sha256(ORCHESTRATOR_BODY.encode("utf-8")).hexdigest(),
    ),
    purpose="episode.orchestrator",
    parameter_schema={
        "type": "object",
        "required": ["stage", "input", "registered_capabilities"],
        "properties": {
            "stage": {"type": "string"},
            "input": {"type": "object"},
            "registered_capabilities": {"type": "array", "items": {"type": "string"}},
        },
        "additionalProperties": False,
    },
    body=ORCHESTRATOR_BODY,
)


def prompt_ref(stage: StageId, role: Role) -> PromptTemplateRef:
    return PROMPT_CATALOG[(stage, role)].ref


def prompt_ref_key(ref: PromptTemplateRef) -> tuple[str, str, str]:
    return (ref.template_id.root, ref.version.root, ref.content_hash.root)


def output_schema(stage: StageId, role: Role) -> dict[str, object]:
    if role == "creator":
        return CREATOR_OUTPUT_MODELS[stage].model_json_schema()
    review_output_model = create_model(
        f"{stage.value.title()}ReviewOutput",
        __base__=EpisodeModel,
        dimension_scores=(dict[str, ReviewDimension], ...),
        findings=(list[ReviewFinding], Field(default_factory=list)),
        summary=(OpaqueText, ...),
        rollback_proposal=(RollbackProposal | None, None),
    )
    return review_output_model.model_json_schema()


__all__ = [
    "PROMPT_CATALOG",
    "PROMPT_PACK_ID",
    "PROMPT_PACK_VERSION",
    "ORCHESTRATOR_BODY",
    "ORCHESTRATOR_TEMPLATE",
    "PromptSpec",
    "output_schema",
    "prompt_ref",
    "prompt_ref_key",
]
