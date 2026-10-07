"""Approved-version text exports: Markdown script, CSV storyboard, JSON manifest."""
from __future__ import annotations

import csv
import io
import json
import re
from decimal import Decimal, InvalidOperation
from collections.abc import Mapping
from hashlib import sha256
from typing import Any

from gw.agent_runtime.models import (
    ApprovalRecord,
    ArtifactLifecycle,
    ArtifactRef,
    ReviewRecord,
    StageId,
    TextExportFormat,
)
from gw.agent_runtime.ports import ExportPayload

from .models import DeliveryCheckContent, EpisodeScoreResult, FullScriptContent, STAGE_CONTENT_MODELS, StoryboardTextContent
from .scoring import SCORING_RULE_VERSION, approval_matches_current_review


class ExportRejected(ValueError):
    """Raised when an export is not bound to a current, safe approved version."""


class ExportFormatMismatch(ExportRejected):
    """A supported business format was requested for the wrong episode stage."""


class EpisodeArtifactExporter:
    """Runtime-injected serializer for a release already verified by the runtime.

    It consumes persisted review evidence (including exact decimal score and
    captured scoring rule) and never manufactures an approval for auto-release.
    """

    async def export(
        self,
        artifact: ArtifactRef,
        content: str,
        review: ReviewRecord,
        *,
        is_current: bool,
        release: bool,
        approval: ApprovalRecord | None,
        format: TextExportFormat,
    ) -> ExportPayload:
        if artifact.lifecycle is not ArtifactLifecycle.valid or not is_current or not release:
            raise ExportRejected("stale, non-current, or unreleased artifacts cannot be exported")
        if review.artifact.artifact_id.root != artifact.artifact_id.root or review.artifact.version_id.root != artifact.version_id.root:
            raise ExportRejected("review is not bound to the exact artifact version")
        if not review.program_validation_passed:
            raise ExportRejected("program-invalid artifacts cannot be exported")
        if sha256(content.encode("utf-8")).hexdigest() != artifact.content_hash.root:
            raise ExportRejected("artifact content does not match its immutable content hash")
        if approval is not None and (
            approval.run_id.root != review.run_id.root
            or approval.review_id.root != review.review_id.root
            or approval.artifact_version_id.root != artifact.version_id.root
            or approval.decision.value not in {"approve", "override"}
        ):
            raise ExportRejected("approval does not match the exact released artifact and review")
        if review.overall_score_decimal is None:
            raise ExportRejected("the exact persisted review score is unavailable")
        try:
            score = Decimal(review.overall_score_decimal)
            envelope = json.loads(content)
            stage = StageId(envelope["stage"])
            raw_payload = envelope["content"]
        except (InvalidOperation, ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
            raise ExportRejected("content or its exact review score is invalid") from exc
        if not score.is_finite() or score < 0 or score > 10:
            raise ExportRejected("the persisted review score is outside the supported range")
        if envelope.get("schema_version") != 1 or not isinstance(raw_payload, dict):
            raise ExportRejected("content is not a supported episode text artifact")
        try:
            payload = STAGE_CONTENT_MODELS[stage].model_validate(raw_payload)
        except (KeyError, ValueError, TypeError) as exc:
            raise ExportRejected("content does not match its frozen episode stage schema") from exc

        version = re.sub(r"[^A-Za-z0-9._-]", "-", artifact.version_id.root)[:64] or "version"
        score_text = str(score)
        if format is TextExportFormat.markdown:
            if stage is not StageId.full_script:
                raise ExportFormatMismatch("Markdown export requires a full_script artifact")
            script = FullScriptContent.model_validate(payload.model_dump(mode="json"))
            release_meta = f"approval_id={approval.approval_id.root}" if approval else "release=automatic"
            metadata = (
                f"<!-- artifact_id={artifact.artifact_id.root}; artifact_version_id={artifact.version_id.root}; "
                f"{release_meta}; score={score_text}; rule={review.rule_version.root} -->"
            )
            lines = [f"# {script.title}", "", metadata, ""]
            for scene in script.scenes:
                lines.extend([
                    f"## {scene.title}", "", f"**场景ID：** `{scene.scene_id}`  ",
                    f"**时间：** {scene.time}  ", f"**地点：** {scene.location}", "",
                    scene.content, "",
                ])
            body = ("\n".join(lines).rstrip() + "\n").encode("utf-8")
            media_type, extension, stem = "text/markdown; charset=utf-8", "md", "full-script"
        elif format is TextExportFormat.csv:
            if stage is not StageId.storyboard_text:
                raise ExportFormatMismatch("CSV export requires a storyboard_text artifact")
            storyboard = StoryboardTextContent.model_validate(payload.model_dump(mode="json"))
            stream = io.StringIO(newline="")
            writer = csv.writer(stream, lineterminator="\r\n", quoting=csv.QUOTE_MINIMAL)
            writer.writerow([
                "artifact_id", "artifact_version_id", "score_rule_version", "weighted_score",
                "shot_id", "scene_id", "description", "camera", "movement", "duration_seconds", "character_ids",
            ])
            for shot in storyboard.shots:
                writer.writerow([
                    _csv_safe(artifact.artifact_id.root), _csv_safe(artifact.version_id.root),
                    _csv_safe(review.rule_version.root), _csv_safe(score_text), _csv_safe(shot.shot_id),
                    _csv_safe(shot.scene_id), _csv_safe(shot.description),
                    _csv_safe(shot.camera or ""), _csv_safe(shot.movement or ""),
                    _csv_safe(shot.duration_seconds), _csv_safe(", ".join(story_character_id for story_character_id in shot.character_ids)),
                ])
            body = ("\ufeff" + stream.getvalue()).encode("utf-8")
            media_type, extension, stem = "text/csv; charset=utf-8", "csv", "storyboard"
        elif format is TextExportFormat.json:
            if stage is not StageId.delivery_check:
                raise ExportFormatMismatch("JSON export requires a delivery_check artifact")
            document = {
                "schema_version": 1,
                "stage": stage.value,
                "artifact": artifact.model_dump(mode="json"),
                "approval": approval.model_dump(mode="json") if approval else None,
                "review": review.model_dump(mode="json"),
                "score": {"weighted_score": score_text, "rule_version": review.rule_version.root},
                "content": payload.model_dump(mode="json"),
            }
            body = (json.dumps(document, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")
            media_type, extension, stem = "application/json", "json", "delivery-check"
        else:
            raise ExportRejected("unsupported episode export format")
        return ExportPayload(
            content=body,
            media_type=media_type,
            filename=f"episode-{stem}-{version}.{extension}",
            artifact_version_id=artifact.version_id,
            review_id=review.review_id,
        )


def _decode_artifact(
    artifact: ArtifactRef,
    content: str,
    *,
    approval: ApprovalRecord,
    review: ReviewRecord,
    score_result: EpisodeScoreResult,
    artifact_current: bool,
) -> tuple[StageId, Mapping[str, Any]]:
    if artifact.lifecycle is not ArtifactLifecycle.valid or not artifact_current:
        raise ExportRejected("stale or non-current artifact versions cannot be exported")
    if sha256(content.encode("utf-8")).hexdigest() != artifact.content_hash.root:
        raise ExportRejected("artifact content does not match its immutable content hash")
    if approval.run_id.root != review.run_id.root:
        raise ExportRejected("approval and review belong to different runs")
    if approval.artifact_version_id.root != artifact.version_id.root:
        raise ExportRejected("approval is not bound to the exact artifact version")
    if approval.review_id.root != review.review_id.root:
        raise ExportRejected("approval is not bound to the supplied review")
    if review.artifact.version_id.root != artifact.version_id.root:
        raise ExportRejected("review is not bound to the exact artifact version")
    if review.rule_version.root != SCORING_RULE_VERSION:
        raise ExportRejected("review rule version is not the accepted episode scoring contract")
    if not review.program_validation_passed:
        raise ExportRejected("program-invalid artifacts cannot be approved or exported")
    try:
        document = json.loads(content)
        stage = StageId(document["stage"])
        payload = document["content"]
    except (ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        raise ExportRejected("content is not a supported episode text artifact") from exc
    if document.get("schema_version") != 1 or not isinstance(payload, dict):
        raise ExportRejected("content is not a supported episode text artifact")
    if not approval_matches_current_review(
        stage,
        approval,
        review,
        artifact,
        score_result,
        artifact_current=artifact_current,
    ):
        raise ExportRejected("approval does not match a current, program-valid exact-score review")
    return stage, payload


def _metadata_comment(
    artifact: ArtifactRef,
    approval: ApprovalRecord,
    score_result: EpisodeScoreResult,
) -> str:
    return (
        f"<!-- artifact_id={artifact.artifact_id.root}; "
        f"artifact_version_id={artifact.version_id.root}; approval_id={approval.approval_id.root}; "
        f"score={score_result.weighted_score}; rule={SCORING_RULE_VERSION} -->"
    )


def export_markdown(
    artifact: ArtifactRef,
    content: str,
    *,
    approval: ApprovalRecord,
    review: ReviewRecord,
    score_result: EpisodeScoreResult,
    artifact_current: bool,
) -> bytes:
    stage, payload = _decode_artifact(
        artifact,
        content,
        approval=approval,
        review=review,
        score_result=score_result,
        artifact_current=artifact_current,
    )
    if stage is not StageId.full_script:
        raise ExportRejected("Markdown script export requires an approved full_script artifact")
    script = FullScriptContent.model_validate(payload)
    lines = [f"# {script.title}", "", _metadata_comment(artifact, approval, score_result), ""]
    for scene in script.scenes:
        lines.extend(
            [
                f"## {scene.title}",
                "",
                f"**场景ID：** `{scene.scene_id}`  ",
                f"**时间：** {scene.time}  ",
                f"**地点：** {scene.location}",
                "",
                scene.content,
                "",
            ]
        )
    return ("\n".join(lines).rstrip() + "\n").encode("utf-8")


def _csv_safe(value: Any) -> str:
    """Prefix cells that could become spreadsheet formulas after whitespace."""
    text = "" if value is None else str(value)
    index = 0
    while index < len(text):
        char = text[index]
        if char.isspace() or ord(char) < 0x20 or ord(char) == 0x7F or char == "\ufeff":
            index += 1
            continue
        break
    if index < len(text) and text[index] in "=+-@":
        return "'" + text[index:]
    return text


def export_csv(
    artifact: ArtifactRef,
    content: str,
    *,
    approval: ApprovalRecord,
    review: ReviewRecord,
    score_result: EpisodeScoreResult,
    artifact_current: bool,
) -> bytes:
    stage, payload = _decode_artifact(
        artifact,
        content,
        approval=approval,
        review=review,
        score_result=score_result,
        artifact_current=artifact_current,
    )
    if stage is not StageId.storyboard_text:
        raise ExportRejected("CSV export requires an approved storyboard_text artifact")
    storyboard = StoryboardTextContent.model_validate(payload)
    stream = io.StringIO(newline="")
    writer = csv.writer(stream, lineterminator="\r\n", quoting=csv.QUOTE_MINIMAL)
    writer.writerow(
        [
            "artifact_id",
            "artifact_version_id",
            "score_rule_version",
            "weighted_score",
            "shot_id",
            "scene_id",
            "description",
            "camera",
            "movement",
            "duration_seconds",
            "character_ids",
        ]
    )
    for shot in storyboard.shots:
        writer.writerow(
            [
                _csv_safe(artifact.artifact_id.root),
                _csv_safe(artifact.version_id.root),
                _csv_safe(SCORING_RULE_VERSION),
                _csv_safe(score_result.weighted_score),
                _csv_safe(shot.shot_id),
                _csv_safe(shot.scene_id),
                _csv_safe(shot.description),
                _csv_safe(shot.camera),
                _csv_safe(shot.movement),
                _csv_safe(shot.duration_seconds),
                _csv_safe(", ".join(shot.character_ids)),
            ]
        )
    # UTF-8 BOM makes Chinese columns readable in common spreadsheet tools; the
    # cells are still independently protected against formula injection.
    return ("\ufeff" + stream.getvalue()).encode("utf-8")


def export_json(
    artifact: ArtifactRef,
    content: str,
    *,
    approval: ApprovalRecord,
    review: ReviewRecord,
    score_result: EpisodeScoreResult,
    artifact_current: bool,
) -> bytes:
    stage, payload = _decode_artifact(
        artifact,
        content,
        approval=approval,
        review=review,
        score_result=score_result,
        artifact_current=artifact_current,
    )
    if stage is not StageId.delivery_check:
        raise ExportRejected("JSON delivery export requires an approved delivery_check artifact")
    delivery = DeliveryCheckContent.model_validate(payload)
    document = {
        "schema_version": 1,
        "stage": stage.value,
        "artifact": artifact.model_dump(mode="json"),
        "approval": approval.model_dump(mode="json"),
        "review": review.model_dump(mode="json"),
        "score": score_result.model_dump(mode="json"),
        "content": delivery.model_dump(mode="json"),
    }
    return (json.dumps(document, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")


__all__ = ["ExportFormatMismatch", "ExportRejected", "export_csv", "export_json", "export_markdown"]
