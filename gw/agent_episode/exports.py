"""Approved-version text exports: Markdown script, CSV storyboard, JSON manifest."""

from __future__ import annotations

import csv
import io
import json
import re
from collections.abc import Mapping
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from hashlib import sha256
from typing import Any, Literal

from pydantic import Field, model_validator

from gw.agent_runtime.models import (
    ApprovalRecord,
    ArtifactLifecycle,
    ArtifactRef,
    ReviewRecord,
    StageId,
    TextExportFormat,
)
from gw.agent_runtime.ports import ExportPayload
from gw.agent_runtime.templates import (
    BREAKDOWN_TEMPLATE,
    BREAKDOWN_TEMPLATE_ID,
    BREAKDOWN_TEMPLATE_VERSION,
)

from .models import (
    STAGE_CONTENT_MODELS,
    DeliveryCheckContent,
    EpisodeModel,
    EpisodeScoreResult,
    FullScriptContent,
    OpaqueId,
    OpaqueText,
    StoryboardTextContent,
)
from .scoring import SCORING_RULE_VERSION, approval_matches_current_review


class BreakdownCategory(StrEnum):
    """好莱坞八大制片拆解分类代码。"""

    CAST = "01_CAST"
    EXTRAS = "02_EXTRAS"
    PROPS = "03_PROPS"
    WARDROBE = "04_WARDROBE"
    VFX = "05_VFX"
    SFX = "06_SFX"
    LOCATION = "07_LOCATION"
    VEHICLES = "08_VEHICLES"


class ShotSize(StrEnum):
    """ASC 六级标准景别。"""

    EWS = "EWS"
    WS = "WS"
    MS = "MS"
    MCU = "MCU"
    CU = "CU"
    ECU = "ECU"


class CameraAngle(StrEnum):
    """ASC 摄影机视角与高度。"""

    EYE_LEVEL = "EYE_LEVEL"
    LOW_ANGLE = "LOW_ANGLE"
    HIGH_ANGLE = "HIGH_ANGLE"
    GODS_EYE = "GODS_EYE"
    DUTCH_ANGLE = "DUTCH_ANGLE"
    GROUND_LEVEL = "GROUND_LEVEL"


class CameraMovement(StrEnum):
    """ASC 十一种受控运镜代码。"""

    STATIC = "STATIC"
    PAN = "PAN"
    TILT = "TILT"
    DOLLY_IN = "DOLLY_IN"
    DOLLY_OUT = "DOLLY_OUT"
    TRUCK_LEFT = "TRUCK_LEFT"
    TRUCK_RIGHT = "TRUCK_RIGHT"
    PEDESTAL_UP = "PEDESTAL_UP"
    PEDESTAL_DOWN = "PEDESTAL_DOWN"
    ARC = "ARC"
    WHIP_PAN = "WHIP_PAN"


class FocalLengthTier(StrEnum):
    """ASC 五档焦段类别。"""

    ULTRA_WIDE = "ULTRA_WIDE"
    WIDE = "WIDE"
    STANDARD = "STANDARD"
    PORTRAIT = "PORTRAIT"
    TELEPHOTO = "TELEPHOTO"


class CompositionRule(StrEnum):
    """受控构图代码，避免把构图要求写成不可执行的形容词。"""

    RULE_OF_THIRDS = "RULE_OF_THIRDS"
    CENTER_SYMMETRY = "CENTER_SYMMETRY"
    GOLDEN_RATIO = "GOLDEN_RATIO"
    LEADING_LINES = "LEADING_LINES"
    FRAME_IN_FRAME = "FRAME_IN_FRAME"
    LOOK_ROOM = "LOOK_ROOM"


class SceneSettingType(StrEnum):
    INT = "INT"
    EXT = "EXT"
    INT_EXT = "INT/EXT"


class LightingPhase(StrEnum):
    DAY = "DAY"
    NIGHT = "NIGHT"
    GOLDEN_HOUR = "GOLDEN_HOUR"
    MAGIC_HOUR = "MAGIC_HOUR"
    DAWN = "DAWN"
    DUSK = "DUSK"
    CONTINUOUS = "CONTINUOUS"
    MOMENTS_LATER = "MOMENTS_LATER"


class WeatherAtmosphere(StrEnum):
    CLEAR = "CLEAR"
    OVERCAST = "OVERCAST"
    HEAVY_RAIN = "HEAVY_RAIN"
    BLIZZARD = "BLIZZARD"
    DENSE_FOG = "DENSE_FOG"
    SMOKE_HAZE = "SMOKE_HAZE"


class CinematographyMetadata(EpisodeModel):
    """镜头必须携带的 ASC 结构化机位元数据。"""

    shot_size: ShotSize
    camera_angle: CameraAngle
    camera_movement: CameraMovement
    focal_length: FocalLengthTier
    composition: CompositionRule = CompositionRule.RULE_OF_THIRDS


class BreakdownElement(EpisodeModel):
    """一个可追踪的制片拆解元素。"""

    element_id: OpaqueId
    name: OpaqueText
    description: OpaqueText | None = None
    quantity: int = Field(default=1, ge=1)


class StructuredShot(EpisodeModel):
    """分镜镜头及其可执行元数据。"""

    shot_id: OpaqueId
    scene_id: OpaqueId
    description: OpaqueText
    metadata: CinematographyMetadata
    element_ids: list[OpaqueId] = Field(default_factory=list)
    character_ids: list[OpaqueId] = Field(default_factory=list)
    duration_seconds: float | None = Field(default=None, gt=0)


class SceneBreakdown(EpisodeModel):
    """分场拆解，八类元素始终以固定代码集合出现。"""

    scene_id: OpaqueId
    title: OpaqueText
    slugline: OpaqueText
    setting_type: SceneSettingType
    lighting_phase: LightingPhase
    weather: WeatherAtmosphere | None = None
    location: OpaqueText
    elements: dict[BreakdownCategory, list[BreakdownElement]]
    shots: list[StructuredShot] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_scene_contract(self) -> SceneBreakdown:
        expected = set(BreakdownCategory)
        if set(self.elements) != expected:
            raise ValueError(
                "elements must contain all eight breakdown categories exactly"
            )
        known_element_ids = {
            item.element_id for items in self.elements.values() for item in items
        }
        if len(known_element_ids) != sum(
            len(items) for items in self.elements.values()
        ):
            raise ValueError("element_id values must be unique within a scene")
        for shot in self.shots:
            if shot.scene_id != self.scene_id:
                raise ValueError("shot scene_id must match its containing scene_id")
            unknown_ids = set(shot.element_ids) - known_element_ids
            if unknown_ids:
                raise ValueError(
                    "shot element_ids must reference scene breakdown elements"
                )
        return self


class AgentBreakdownOutput(EpisodeModel):
    """文学/导演 Agent 的机器可读分场分镜拆解结果。"""

    schema_version: Literal[1] = 1
    template_id: str = BREAKDOWN_TEMPLATE_ID
    template_version: str = BREAKDOWN_TEMPLATE_VERSION
    source_full_script_version_id: OpaqueId
    source_scene_script_version_id: OpaqueId
    scenes: list[SceneBreakdown] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_document_contract(self) -> AgentBreakdownOutput:
        if self.template_id != BREAKDOWN_TEMPLATE_ID:
            raise ValueError("unsupported breakdown template_id")
        if self.template_version != BREAKDOWN_TEMPLATE_VERSION:
            raise ValueError("unsupported breakdown template_version")
        scene_ids = [scene.scene_id for scene in self.scenes]
        if len(scene_ids) != len(set(scene_ids)):
            raise ValueError("scene_id values must be unique")
        return self


class ExportRejected(ValueError):
    """Raised when an export is not bound to a current, safe approved version."""


class ExportFormatMismatch(ExportRejected):
    """A supported business format was requested for the wrong episode stage."""


def structured_breakdown_schema() -> dict[str, Any]:
    """返回 Agent 拆解输出的 Pydantic JSON Schema。"""

    return AgentBreakdownOutput.model_json_schema()


def parse_agent_breakdown(
    payload: AgentBreakdownOutput | Mapping[str, Any] | str | bytes,
) -> AgentBreakdownOutput:
    """校验 Agent 拆解 JSON 或映射并返回不可变语义模型。

    只接受 ``agent.breakdown`` 当前版本和八类完整元素集合；自然语言镜头
    描述无法绕过枚举字段校验。
    """

    if isinstance(payload, (str, bytes)):
        try:
            payload = json.loads(payload)
        except (TypeError, json.JSONDecodeError) as exc:
            raise ExportRejected("agent breakdown payload is not valid JSON") from exc
    try:
        return AgentBreakdownOutput.model_validate(payload)
    except (TypeError, ValueError) as exc:
        raise ExportRejected(
            "agent breakdown payload does not match its structured contract"
        ) from exc


def validate_agent_breakdown(
    payload: AgentBreakdownOutput | Mapping[str, Any] | str | bytes,
) -> AgentBreakdownOutput:
    """兼容调用方的语义别名，执行与 ``parse_agent_breakdown`` 相同的校验。"""

    return parse_agent_breakdown(payload)


def build_agent_breakdown(
    payload: AgentBreakdownOutput | Mapping[str, Any],
) -> AgentBreakdownOutput:
    """将 Agent 结构化输出归一化为可导出的契约模型。"""

    return parse_agent_breakdown(payload)


def export_agent_breakdown(
    payload: AgentBreakdownOutput | Mapping[str, Any],
) -> bytes:
    """导出稳定排序、UTF-8 的机器可读分场分镜 JSON。"""

    document = parse_agent_breakdown(payload)
    return (
        json.dumps(
            document.model_dump(mode="json"),
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
        )
        + "\n"
    ).encode("utf-8")


def export_structured_breakdown(
    payload: AgentBreakdownOutput | Mapping[str, Any],
) -> bytes:
    """结构化拆解导出的兼容别名。"""

    return export_agent_breakdown(payload)


_STAGE_EXPORT_FORMATS: dict[StageId, tuple[TextExportFormat, ...]] = {
    StageId.full_script: (TextExportFormat.markdown,),
    StageId.storyboard_text: (TextExportFormat.csv,),
    StageId.delivery_check: (TextExportFormat.json,),
}


def supported_export_formats(stage: StageId) -> tuple[TextExportFormat, ...]:
    """Return the formats the episode serializer supports for one stage."""
    return _STAGE_EXPORT_FORMATS.get(stage, ())


def parse_episode_text_artifact(
    content: str,
    *,
    expected_stage: StageId | None = None,
) -> tuple[StageId, EpisodeModel]:
    """Validate a frozen stage payload without generating a business export."""
    try:
        envelope = json.loads(content)
        stage = StageId(envelope["stage"])
        raw_payload = envelope["content"]
    except (ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
        raise ExportRejected(
            "content is not a supported episode text artifact"
        ) from exc
    if expected_stage is not None and stage is not expected_stage:
        raise ExportRejected("artifact stage does not match its durable stage attempt")
    if envelope.get("schema_version") != 1 or not isinstance(raw_payload, dict):
        raise ExportRejected("content is not a supported episode text artifact")
    try:
        payload = STAGE_CONTENT_MODELS[stage].model_validate(raw_payload)
    except (KeyError, ValueError, TypeError) as exc:
        raise ExportRejected(
            "content does not match its frozen episode stage schema"
        ) from exc
    return stage, payload


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
        if (
            artifact.lifecycle is not ArtifactLifecycle.valid
            or not is_current
            or not release
        ):
            raise ExportRejected(
                "stale, non-current, or unreleased artifacts cannot be exported"
            )
        if (
            review.artifact.artifact_id.root != artifact.artifact_id.root
            or review.artifact.version_id.root != artifact.version_id.root
        ):
            raise ExportRejected("review is not bound to the exact artifact version")
        if not review.program_validation_passed:
            raise ExportRejected("program-invalid artifacts cannot be exported")
        if sha256(content.encode("utf-8")).hexdigest() != artifact.content_hash.root:
            raise ExportRejected(
                "artifact content does not match its immutable content hash"
            )
        if approval is not None and (
            approval.run_id.root != review.run_id.root
            or approval.review_id.root != review.review_id.root
            or approval.artifact_version_id.root != artifact.version_id.root
            or approval.decision.value not in {"approve", "override"}
        ):
            raise ExportRejected(
                "approval does not match the exact released artifact and review"
            )
        if review.overall_score_decimal is None:
            raise ExportRejected("the exact persisted review score is unavailable")
        try:
            score = Decimal(review.overall_score_decimal)
        except (InvalidOperation, ValueError, TypeError) as exc:
            raise ExportRejected(
                "content or its exact review score is invalid"
            ) from exc
        if not score.is_finite() or score < 0 or score > 10:
            raise ExportRejected(
                "the persisted review score is outside the supported range"
            )
        stage, payload = parse_episode_text_artifact(content)
        if format not in supported_export_formats(stage):
            raise ExportFormatMismatch(
                "The requested export format is not supported for this episode stage"
            )

        version = (
            re.sub(r"[^A-Za-z0-9._-]", "-", artifact.version_id.root)[:64] or "version"
        )
        score_text = str(score)
        if format is TextExportFormat.markdown:
            script = FullScriptContent.model_validate(payload.model_dump(mode="json"))
            release_meta = (
                f"approval_id={approval.approval_id.root}"
                if approval
                else "release=automatic"
            )
            metadata = (
                f"<!-- artifact_id={artifact.artifact_id.root}; artifact_version_id={artifact.version_id.root}; "
                f"{release_meta}; score={score_text}; rule={review.rule_version.root} -->"
            )
            lines = [f"# {script.title}", "", metadata, ""]
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
            body = ("\n".join(lines).rstrip() + "\n").encode("utf-8")
            media_type, extension, stem = (
                "text/markdown; charset=utf-8",
                "md",
                "full-script",
            )
        elif format is TextExportFormat.csv:
            storyboard = StoryboardTextContent.model_validate(
                payload.model_dump(mode="json")
            )
            stream = io.StringIO(newline="")
            writer = csv.writer(
                stream, lineterminator="\r\n", quoting=csv.QUOTE_MINIMAL
            )
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
                        _csv_safe(review.rule_version.root),
                        _csv_safe(score_text),
                        _csv_safe(shot.shot_id),
                        _csv_safe(shot.scene_id),
                        _csv_safe(shot.description),
                        _csv_safe(shot.camera or ""),
                        _csv_safe(shot.movement or ""),
                        _csv_safe(shot.duration_seconds),
                        _csv_safe(
                            ", ".join(
                                story_character_id
                                for story_character_id in shot.character_ids
                            )
                        ),
                    ]
                )
            body = ("\ufeff" + stream.getvalue()).encode("utf-8")
            media_type, extension, stem = "text/csv; charset=utf-8", "csv", "storyboard"
        elif format is TextExportFormat.json:
            document = {
                "schema_version": 1,
                "stage": stage.value,
                "artifact": artifact.model_dump(mode="json"),
                "approval": approval.model_dump(mode="json") if approval else None,
                "review": review.model_dump(mode="json"),
                "score": {
                    "weighted_score": score_text,
                    "rule_version": review.rule_version.root,
                },
                "content": payload.model_dump(mode="json"),
            }
            body = (
                json.dumps(document, ensure_ascii=False, sort_keys=True, indent=2)
                + "\n"
            ).encode("utf-8")
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
        raise ExportRejected(
            "stale or non-current artifact versions cannot be exported"
        )
    if sha256(content.encode("utf-8")).hexdigest() != artifact.content_hash.root:
        raise ExportRejected(
            "artifact content does not match its immutable content hash"
        )
    if approval.run_id.root != review.run_id.root:
        raise ExportRejected("approval and review belong to different runs")
    if approval.artifact_version_id.root != artifact.version_id.root:
        raise ExportRejected("approval is not bound to the exact artifact version")
    if approval.review_id.root != review.review_id.root:
        raise ExportRejected("approval is not bound to the supplied review")
    if review.artifact.version_id.root != artifact.version_id.root:
        raise ExportRejected("review is not bound to the exact artifact version")
    if review.rule_version.root != SCORING_RULE_VERSION:
        raise ExportRejected(
            "review rule version is not the accepted episode scoring contract"
        )
    if not review.program_validation_passed:
        raise ExportRejected("program-invalid artifacts cannot be approved or exported")
    try:
        document = json.loads(content)
        stage = StageId(document["stage"])
        payload = document["content"]
    except (ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        raise ExportRejected(
            "content is not a supported episode text artifact"
        ) from exc
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
        raise ExportRejected(
            "approval does not match a current, program-valid exact-score review"
        )
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
        raise ExportRejected(
            "Markdown script export requires an approved full_script artifact"
        )
    script = FullScriptContent.model_validate(payload)
    lines = [
        f"# {script.title}",
        "",
        _metadata_comment(artifact, approval, score_result),
        "",
    ]
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
        raise ExportRejected(
            "JSON delivery export requires an approved delivery_check artifact"
        )
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
    return (
        json.dumps(document, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    ).encode("utf-8")


__all__ = [
    "AgentBreakdownOutput",
    "BreakdownCategory",
    "BreakdownElement",
    "CameraAngle",
    "CameraMovement",
    "CinematographyMetadata",
    "CompositionRule",
    "ExportFormatMismatch",
    "ExportRejected",
    "FocalLengthTier",
    "LightingPhase",
    "SceneBreakdown",
    "SceneSettingType",
    "ShotSize",
    "StructuredShot",
    "WeatherAtmosphere",
    "BREAKDOWN_TEMPLATE",
    "BREAKDOWN_TEMPLATE_ID",
    "BREAKDOWN_TEMPLATE_VERSION",
    "build_agent_breakdown",
    "export_agent_breakdown",
    "export_csv",
    "export_json",
    "export_markdown",
    "export_structured_breakdown",
    "parse_agent_breakdown",
    "structured_breakdown_schema",
    "validate_agent_breakdown",
]
