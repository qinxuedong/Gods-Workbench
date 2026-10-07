"""Deterministic structural, identifier, provenance, and scope validation."""
from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

from pydantic import ValidationError

from gw.agent_runtime.models import ArtifactLifecycle, ArtifactRef, ProgramCheck, StageId

from .models import (
    BriefContent,
    DeliveryCheckContent,
    EpisodeInputArtifact,
    EpisodeProgramValidation,
    FullScriptContent,
    OutlineCharactersContent,
    STAGE_CONTENT_MODELS,
    STAGE_INPUTS,
    SceneScriptContent,
    StoryboardTextContent,
)


_APPROX_TEN_MINUTES = re.compile(
    r"(?:(?:约|大约|将近|差不多)?\s*(?:10|十)\s*分钟(?:左右|上下)?|十来分钟|about\s+10\s*(?:minutes?|mins?)|around\s+10\s*(?:minutes?|mins?)|approximately\s+10\s*(?:minutes?|mins?)|~\s*10\s*(?:minutes?|mins?))",
    re.IGNORECASE,
)


def _check(check_id: str, passed: bool, message: str) -> ProgramCheck:
    return ProgramCheck(check_id=check_id, passed=passed, message=message[:512])


def _plain(value: Any) -> Any:
    return value.model_dump(mode="python") if hasattr(value, "model_dump") else value


def _unique(values: Sequence[str]) -> bool:
    return len(values) == len(set(values))


def input_set_issues(
    stage: StageId, inputs: Sequence[EpisodeInputArtifact]
) -> list[str]:
    """Require exactly the declared, effective source stages for this boundary."""
    by_stage: dict[StageId, EpisodeInputArtifact] = {}
    for item in inputs:
        if item.stage in by_stage:
            return [f"Duplicate input stage: {item.stage.value}."]
        by_stage[item.stage] = item
        if item.artifact.lifecycle is not ArtifactLifecycle.valid:
            return [f"Input {item.stage.value} is not an effective valid artifact version."]
    expected = set(STAGE_INPUTS[stage])
    if set(by_stage) != expected:
        missing = sorted(item.value for item in expected - set(by_stage))
        extra = sorted(item.value for item in set(by_stage) - expected)
        return [f"Input stage boundary mismatch; missing={missing}, extra={extra}."]
    return []


def _payload_model(stage: StageId, payload: Mapping[str, Any] | Any) -> Any:
    if hasattr(payload, "model_dump"):
        payload = payload.model_dump(mode="python")
    return STAGE_CONTENT_MODELS[stage].model_validate(payload)


def validate_stage_payload(
    stage: StageId,
    payload: Mapping[str, Any] | Any,
    *,
    input_payloads: Mapping[StageId, Mapping[str, Any] | Any],
    input_refs: Mapping[StageId, ArtifactRef],
    user_goal: str | None = None,
) -> tuple[Any | None, EpisodeProgramValidation]:
    """Validate JSON structure plus deterministic cross-artifact source rules.

    Review findings remain a different concern: a valid, complete artifact may
    have zero semantic defects, and no particular defect count is required.
    """
    checks: list[ProgramCheck] = []
    try:
        content = _payload_model(stage, payload)
    except ValidationError as exc:
        errors = exc.errors(include_url=False)
        for index, error in enumerate(errors, start=1):
            location = ".".join(str(part) for part in error.get("loc", ())) or "$"
            message = str(error.get("msg", "invalid value"))
            checks.append(_check(f"structure.{index}", False, f"{location}: {message}"))
        return None, EpisodeProgramValidation(checks=checks)
    except (TypeError, ValueError):
        checks.append(_check("structure", False, "Artifact content must be a JSON object matching its stage schema."))
        return None, EpisodeProgramValidation(checks=checks)

    checks.append(_check("structure", True, "Stage payload matches the closed text-artifact schema."))
    exact_inputs = {stage_key: _plain(item) for stage_key, item in input_payloads.items()}
    refs = input_refs

    if stage is StageId.brief:
        brief: BriefContent = content
        goal = user_goal or ""
        approx_duration = bool(_APPROX_TEN_MINUTES.search(goal))
        duration = brief.duration_request
        duration_complete = bool(
            duration
            and duration.target_minutes is not None
            and duration.tolerance_minutes is not None
            and duration.estimation_method is not None
            and duration.tolerance_minutes >= 0
        )
        if approx_duration and not duration_complete:
            checks.append(
                _check(
                    "brief.duration_clarification",
                    False,
                    "Approximate ten-minute scope lacks explicit duration tolerance or estimation method; ask the user instead of choosing one.",
                )
            )
        else:
            checks.append(
                _check(
                    "brief.duration_clarification",
                    True,
                    "No unresolved approximate-ten-minute duration parameters were silently selected.",
                )
            )
        checks.append(
            _check(
                "brief.clarifications_resolved",
                not brief.clarifying_questions,
                "No unanswered clarifying questions remain."
                if not brief.clarifying_questions
                else "Brief contains unanswered questions and cannot advance until they are resolved.",
            )
        )

    elif stage is StageId.outline_characters:
        outline: OutlineCharactersContent = content
        brief_ref = refs[StageId.brief]
        checks.append(
            _check(
                "outline.source_brief_version",
                outline.source_brief_version_id == brief_ref.version_id.root,
                "Outline binds the exact current brief version."
                if outline.source_brief_version_id == brief_ref.version_id.root
                else "Outline source_brief_version_id does not match the supplied brief version.",
            )
        )
        character_ids = [character.character_id for character in outline.characters]
        section_ids = [section.section_id for section in outline.sections]
        checks.append(_check("outline.unique_character_ids", _unique(character_ids), "Character IDs are unique." if _unique(character_ids) else "Character IDs contain duplicates."))
        checks.append(_check("outline.unique_section_ids", _unique(section_ids), "Outline section IDs are unique." if _unique(section_ids) else "Outline section IDs contain duplicates."))
        known_characters = set(character_ids)
        relations_valid = all(
            relation.from_character_id in known_characters
            and relation.to_character_id in known_characters
            for relation in outline.relationships
        )
        checks.append(_check("outline.relationship_character_refs", relations_valid, "Every relationship references known characters." if relations_valid else "A relationship references an unknown character ID."))

    elif stage is StageId.full_script:
        script: FullScriptContent = content
        brief_ref = refs[StageId.brief]
        outline_ref = refs[StageId.outline_characters]
        checks.append(_check("script.source_brief_version", script.source_brief_version_id == brief_ref.version_id.root, "Full script binds the exact brief version." if script.source_brief_version_id == brief_ref.version_id.root else "Full script source brief version does not match."))
        checks.append(_check("script.source_outline_version", script.source_outline_version_id == outline_ref.version_id.root, "Full script binds the exact outline/person version." if script.source_outline_version_id == outline_ref.version_id.root else "Full script source outline version does not match."))
        scenes = [scene.scene_id for scene in script.scenes]
        checks.append(_check("script.unique_scene_ids", _unique(scenes), "Full-script scene IDs are unique." if _unique(scenes) else "Full-script scene IDs contain duplicates."))
        outline: OutlineCharactersContent = _payload_model(StageId.outline_characters, exact_inputs[StageId.outline_characters])
        known_characters = {character.character_id for character in outline.characters}
        known_sections = {section.section_id for section in outline.sections}
        characters_valid = all(set(scene.character_ids) <= known_characters for scene in script.scenes)
        checks.append(_check("script.character_refs", characters_valid, "All scene character IDs exist in the supplied outline." if characters_valid else "A full-script scene references an unknown character ID."))
        sections_valid = all(set(scene.outline_section_ids) <= known_sections for scene in script.scenes)
        checks.append(_check("script.outline_section_refs", sections_valid, "All outline section references exist." if sections_valid else "A full-script scene references an unknown outline section ID."))

    elif stage is StageId.scene_script:
        scene_script: SceneScriptContent = content
        full_ref = refs[StageId.full_script]
        checks.append(_check("scene_script.source_full_script_version", scene_script.source_full_script_version_id == full_ref.version_id.root, "Scene script binds the exact full-script version." if scene_script.source_full_script_version_id == full_ref.version_id.root else "Scene script source version does not match the supplied full script."))
        full_script: FullScriptContent = _payload_model(StageId.full_script, exact_inputs[StageId.full_script])
        source_scene_ids = {scene.scene_id for scene in full_script.scenes}
        result_scene_ids = [scene.scene_id for scene in scene_script.scenes]
        coverage_valid = len(result_scene_ids) == len(set(result_scene_ids)) and set(result_scene_ids) == source_scene_ids
        checks.append(_check("scene_script.scene_id_coverage", coverage_valid, "Each complete-script scene is represented exactly once." if coverage_valid else "Scene script has duplicate, unknown, or missing source scene IDs."))
        character_ids = {character_id for scene in full_script.scenes for character_id in scene.character_ids}
        character_refs_valid = all(set(scene.character_ids) <= character_ids for scene in scene_script.scenes)
        checks.append(_check("scene_script.character_refs", character_refs_valid, "Scene character references are present in the full script." if character_refs_valid else "A scene breakdown references a character absent from the full script."))

    elif stage is StageId.storyboard_text:
        storyboard: StoryboardTextContent = content
        full_ref = refs[StageId.full_script]
        scene_ref = refs[StageId.scene_script]
        checks.append(_check("storyboard.source_full_script_version", storyboard.source_full_script_version_id == full_ref.version_id.root, "Storyboard binds the exact full-script version." if storyboard.source_full_script_version_id == full_ref.version_id.root else "Storyboard source full-script version does not match."))
        checks.append(_check("storyboard.source_scene_script_version", storyboard.source_scene_script_version_id == scene_ref.version_id.root, "Storyboard binds the exact scene-script version." if storyboard.source_scene_script_version_id == scene_ref.version_id.root else "Storyboard source scene-script version does not match."))
        shot_ids = [shot.shot_id for shot in storyboard.shots]
        checks.append(_check("storyboard.unique_shot_ids", _unique(shot_ids), "Shot IDs are unique." if _unique(shot_ids) else "Shot IDs contain duplicates."))
        full_script: FullScriptContent = _payload_model(StageId.full_script, exact_inputs[StageId.full_script])
        scene_script: SceneScriptContent = _payload_model(StageId.scene_script, exact_inputs[StageId.scene_script])
        valid_scene_ids = {scene.scene_id for scene in full_script.scenes} & {scene.scene_id for scene in scene_script.scenes}
        scene_refs_valid = all(shot.scene_id in valid_scene_ids for shot in storyboard.shots)
        checks.append(_check("storyboard.scene_refs", scene_refs_valid, "Every shot points to a scene in both source scripts." if scene_refs_valid else "A shot references an unknown scene ID."))
        valid_character_ids = {character_id for scene in full_script.scenes for character_id in scene.character_ids}
        character_refs_valid = all(set(shot.character_ids) <= valid_character_ids for shot in storyboard.shots)
        checks.append(_check("storyboard.character_refs", character_refs_valid, "Every shot character ID exists in the complete script." if character_refs_valid else "A shot references an unknown character ID."))

    elif stage is StageId.delivery_check:
        delivery: DeliveryCheckContent = content
        expected = {
            stage_key: (input_ref.artifact_id.root, input_ref.version_id.root)
            for stage_key, input_ref in refs.items()
        }
        reported = {
            item.stage: (item.artifact_id, item.version_id)
            for item in delivery.checked_artifacts
        }
        refs_unique = len(reported) == len(delivery.checked_artifacts)
        checks.append(_check("delivery.unique_stage_refs", refs_unique, "Each checked stage appears once." if refs_unique else "Delivery contains duplicate stage references."))
        exact_versions = refs_unique and reported == expected
        detail = "Delivery references exactly the supplied stage artifact versions." if exact_versions else f"Delivery references differ from supplied versions; reported={reported}, expected={expected}."
        checks.append(_check("delivery.exact_approved_source_versions", exact_versions, detail))

    return content, EpisodeProgramValidation(checks=checks)


_JSONPATH_TOKEN = re.compile(r"\.([A-Za-z_][A-Za-z0-9_-]*)|\[(\d+)\]|\[['\"]([^'\"\\]+)['\"]\]")


def _location_resolves(payload: Any, location: str) -> bool:
    if location == "$" or location == "/":
        return True
    if location.startswith("/"):
        tokens = [part.replace("~1", "/").replace("~0", "~") for part in location[1:].split("/")]
    elif location.startswith("$"):
        path = location[1:]
        tokens = []
        cursor = 0
        for match in _JSONPATH_TOKEN.finditer(path):
            if match.start() != cursor:
                return False
            tokens.append(match.group(1) or match.group(2) or match.group(3))
            cursor = match.end()
        if cursor != len(path):
            return False
    else:
        return False
    current = payload
    for token in tokens:
        if isinstance(current, Mapping):
            if token not in current:
                return False
            current = current[token]
        elif isinstance(current, list):
            if not token.isdigit():
                return False
            item_index = int(token)
            if item_index >= len(current):
                return False
            current = current[item_index]
        else:
            return False
    return True


def review_finding_location_issues(payload: Mapping[str, Any], findings: Sequence[Any]) -> tuple[str, ...]:
    """Return finding IDs whose JSON Pointer/JSONPath cannot locate the artifact."""
    return tuple(
        finding.finding_id
        for finding in findings
        if not _location_resolves(payload, finding.location)
    )


def validate_approval_safety_inputs(
    artifact: ArtifactRef,
    *,
    expected_source_refs: Sequence[tuple[str, str]],
    artifact_is_current: bool,
) -> bool:
    """Reusable structural freshness test before any approval/override decision."""
    actual = {(ref.artifact_id.root, ref.version_id.root) for ref in artifact.source_refs}
    return (
        artifact.lifecycle is ArtifactLifecycle.valid
        and artifact_is_current
        and actual == set(expected_source_refs)
    )


__all__ = [
    "input_set_issues",
    "validate_approval_safety_inputs",
    "validate_stage_payload",
]
