# -*- coding: utf-8 -*-
# Copyright 2026 Gods-Workbench Authors
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Phase 12 A3 剧集流水线的真实落盘仓库。

命名空间：``episode_pipeline``（core.storage.JsonState 原子落盘，重启可恢复）。

稳定 ID：pipeline ``ep-NNNN``、stage ``stg-NNNN``。

证据边界（必须在契约与报告中如实写明）：
- 阶段推进**只推进状态**，不触发任何真实渲染、不调用外部模型、不生成媒体；
- C8-T2 单镜头记录复用既有 ComfyUI/RunningHub 执行器，缺少真实任务或资产身份时保持等待/未知；
- 单进程/单实例一致性；多 worker 并发写属部署方职责。
"""

from __future__ import annotations

import hashlib
import json
import math
from typing import Any, Dict, List, Mapping, Optional

from gw.core import storage
from gw.core.errors import CleanroomException, VersionConflictException
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

NS = "episode_pipeline"
DATA_STATUS_OK = "ok"

STAGE_KEYS = ("script", "assets", "video", "audio_compose")
STAGE_LABELS = {
    "script": "剧本创作",
    "assets": "资产创作",
    "video": "分镜脚本",
    "audio_compose": "音频合成",
}
STAGE_STATUS = ("pending", "running", "completed", "cancelled", "failed")

SHOT_STATUS = ("submitting", "queued", "running", "waiting", "completed", "failed", "unknown")
SHOT_ACTIVE_STATUS = frozenset({"submitting", "queued", "running", "waiting"})
SHOT_NEXT_ACTION = {
    "submitting": "submit_generation",
    "queued": "poll_job",
    "running": "poll_job",
    "waiting": "configure_executor",
    "completed": "review_source",
    "failed": "retry_generation",
    "unknown": "reconcile_provider",
}


def state() -> storage.JsonState:
    return storage.JsonState(NS, _blank)


def _blank() -> Dict[str, Any]:
    return {
        "revision": 1,
        "pipelines": {},
        "stages": {},
        "shots": {},
        "timeline_manifests": {},
        "seq": 0,
        "shot_seq": 0,
        "timeline_seq": 0,
    }


def _bump(raw: Dict[str, Any]) -> int:
    raw["revision"] = int(raw.get("revision") or 1) + 1
    return raw["revision"]


def _cas(raw: Dict[str, Any], expected: Optional[int], label: str) -> None:
    if expected is None:
        return
    if int(expected) != int(raw.get("revision") or 1):
        raise VersionConflictException(
            expected_version=int(expected),
            current_version=int(raw.get("revision") or 1),
            message="%s版本冲突，请重新读取后重试" % label,
        )


def _stage_view(item: Dict[str, Any], prompt_item_ids: Dict[str, str]) -> Dict[str, Any]:
    key = item["stage_key"]
    return {
        "stage_id": item["stage_id"],
        "stage": key,
        "label": STAGE_LABELS.get(key, key),
        "status": item.get("status"),
        "version": item.get("version"),
        "result": dict(item.get("result") or {}),
        "production_context": dict(item.get("production_context") or {}),
        "prompt_item_id": prompt_item_ids.get(key),
        "started_at": item.get("started_at"),
        "finished_at": item.get("finished_at"),
        "updated_at": item.get("updated_at"),
    }


def _ensure_shot_state(raw: Dict[str, Any]) -> None:
    """兼容 C8-T2 加入前已有的 episode_pipeline 快照。"""
    if not isinstance(raw.get("shots"), dict):
        raw["shots"] = {}
    if not isinstance(raw.get("shot_seq"), int):
        raw["shot_seq"] = 0


def _shot_view(item: Dict[str, Any]) -> Dict[str, Any]:
    status = str(item.get("status") or "unknown")
    if status not in SHOT_STATUS:
        status = "unknown"
    result = {
        "project_id": item.get("project_id") or "",
        "shot_id": item.get("shot_id") or "",
        "status": status,
        "next_action": item.get("next_action") or SHOT_NEXT_ACTION.get(status, "reconcile_provider"),
        "target": item.get("target") or "",
        "workflow_id": item.get("workflow_id") or "",
        "version": int(item.get("version") or 1),
        "data_gaps": list(item.get("data_gaps") or []),
        "created_at": item.get("created_at"),
        "updated_at": item.get("updated_at"),
    }
    for field in ("entity_id", "episode_id", "scene_id", "canvas_id", "job_id", "remote_task_id", "asset_id", "version_id"):
        value = item.get(field)
        if value not in (None, ""):
            result[field] = value
    source = item.get("source")
    if isinstance(source, dict) and source:
        result["source"] = dict(source)
    error = item.get("error")
    if isinstance(error, dict) and error:
        result["error"] = dict(error)
    return result


def _latest_shot_records(raw: Dict[str, Any], pipeline_id: str) -> List[Dict[str, Any]]:
    _ensure_shot_state(raw)
    latest: Dict[str, Dict[str, Any]] = {}
    for item in raw["shots"].values():
        if item.get("pipeline_id") != pipeline_id:
            continue
        shot_id = str(item.get("shot_id") or "")
        prior = latest.get(shot_id)
        if prior is None or int(item.get("sequence") or 0) > int(prior.get("sequence") or 0):
            latest[shot_id] = item
    return sorted(latest.values(), key=lambda item: str(item.get("shot_id") or ""))


def _pipeline_view(raw: Dict[str, Any], item: Dict[str, Any]) -> Dict[str, Any]:
    _ensure_shot_state(raw)
    stages = [
        _stage_view(v, item.get("prompt_item_ids") or {})
        for v in raw["stages"].values() if v.get("pipeline_id") == item["pipeline_id"]
    ]
    order = {key: index for index, key in enumerate(STAGE_KEYS)}
    stages.sort(key=lambda s: order.get(s["stage"], 99))
    return {
        "pipeline_id": item["pipeline_id"],
        "project_id": item.get("project_id") or "",
        "title": item.get("title") or "",
        "seed": item.get("seed") or "",
        "prompt_library_id": item.get("prompt_library_id") or "",
        "prompt_item_ids": dict(item.get("prompt_item_ids") or {}),
        "production_context": dict(item.get("production_context") or {}),
        "stages": stages,
        "shots": [_shot_view(shot) for shot in _latest_shot_records(raw, item["pipeline_id"])],
        "version": item.get("version"),
        "created_at": item.get("created_at"),
        "updated_at": item.get("updated_at"),
    }


def list_pipelines(project_id: Optional[str]) -> Dict[str, Any]:
    raw = state().read()
    items = [dict(v) for v in raw["pipelines"].values()]
    if project_id:
        items = [item for item in items if item.get("project_id") == project_id]
    items.sort(key=lambda item: item["pipeline_id"])
    return {
        "pipelines": [_pipeline_view(raw, item) for item in items],
        "revision": raw["revision"],
        "data_status": DATA_STATUS_OK,
        "data_gaps": [],
    }


def create_pipeline(payload: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(payload, dict):
        raise CleanroomException(400, "INVALID_REQUEST", "请求体必须是对象")
    if not str(payload.get("project_id") or "").strip():
        raise CleanroomException(400, "INVALID_REQUEST", "project_id 不能为空")

    def mutate(raw: Dict[str, Any]) -> Dict[str, Any]:
        _ensure_shot_state(raw)
        raw["seq"] = int(raw.get("seq") or 0) + 1
        pipeline_id = "ep-%04d" % raw["seq"]
        stage_seq = sum(1 for _ in raw["stages"].values())
        now = storage.now_iso()
        prompt_item_ids = payload.get("prompt_item_ids") or {}
        if not isinstance(prompt_item_ids, dict):
            prompt_item_ids = {}
        record = {
            "pipeline_id": pipeline_id,
            "project_id": str(payload.get("project_id")),
            "title": str(payload.get("title") or "未命名剧集"),
            "seed": str(payload.get("seed") or ""),
            "prompt_library_id": str(payload.get("prompt_library_id") or ""),
            "prompt_item_ids": {k: str(v) for k, v in prompt_item_ids.items()},
            "production_context": dict(payload.get("production_context") or {}),
            "version": 1,
            "created_at": now,
            "updated_at": now,
        }
        raw["pipelines"][pipeline_id] = record
        for key in STAGE_KEYS:
            stage_seq += 1
            stage_id = "stg-%04d" % stage_seq
            raw["stages"][stage_id] = {
                "stage_id": stage_id,
                "pipeline_id": pipeline_id,
                "stage_key": key,
                "status": "pending",
                "version": 1,
                "result": {},
                "production_context": {},
                "started_at": None,
                "finished_at": None,
                "created_at": now,
                "updated_at": now,
            }
        _bump(raw)
        return _pipeline_view(raw, record)

    return state().mutate(mutate)


def get_pipeline(pipeline_id: str) -> Dict[str, Any]:
    raw = state().read()
    item = raw["pipelines"].get(pipeline_id)
    if item is None:
        raise CleanroomException(404, "PIPELINE_NOT_FOUND", "剧集流水线不存在")
    pipeline = _pipeline_view(raw, item)
    return {"pipeline": pipeline, "revision": raw["revision"]}


# ---------------------------------------------------------------------------
# C12-T3 OpenFilm timeline_manifest
# ---------------------------------------------------------------------------


def _timeline_identifier(value: str, field_name: str) -> str:
    """校验时间线清单中的稳定 ASCII 长标识。"""
    if not isinstance(value, str):
        raise ValueError(f"{field_name} 必须是字符串")
    value = value.strip()
    if not value or len(value) > 128 or not value.isascii() or any(ch.isspace() for ch in value):
        raise ValueError(f"{field_name} 必须是非空 ASCII 标识")
    return value


class TimelineClip(BaseModel):
    """OpenFilm 轻量时间线中的单镜头片段。"""

    model_config = ConfigDict(extra="allow")

    shot_id: str = Field(..., min_length=1, max_length=128)
    asset_id: str = Field(..., min_length=1, max_length=128)
    at: float = Field(..., ge=0)
    duration: float = Field(..., gt=0)
    overrides: Dict[str, Any] = Field(default_factory=dict)

    @field_validator("shot_id", "asset_id")
    @classmethod
    def validate_identifier(cls, value: str, info):
        return _timeline_identifier(value, info.field_name)

    @field_validator("at", "duration")
    @classmethod
    def validate_finite_time(cls, value: float, info):
        if not math.isfinite(value):
            raise ValueError(f"{info.field_name} 必须是有限数字")
        return value

    @field_validator("overrides")
    @classmethod
    def validate_overrides(cls, value: Dict[str, Any]):
        if not isinstance(value, dict):
            raise ValueError("overrides 必须是对象")
        return value


class TimelineTrack(BaseModel):
    """OpenFilm 时间线轨道；当前首片以静态图 video 轨为主。"""

    model_config = ConfigDict(extra="allow")

    track_id: str = Field(..., min_length=1, max_length=128)
    kind: str = Field(default="video", min_length=1, max_length=32)
    clips: List[TimelineClip] = Field(default_factory=list)

    @field_validator("track_id")
    @classmethod
    def validate_track_identifier(cls, value: str):
        return _timeline_identifier(value, "track_id")

    @field_validator("kind")
    @classmethod
    def validate_kind(cls, value: str):
        value = value.strip().lower()
        if not value or not value.isascii() or any(ch.isspace() for ch in value):
            raise ValueError("kind 必须是非空 ASCII 标识")
        return value


class TimelineManifest(BaseModel):
    """可持久化的 OpenFilm timeline_manifest 数据模型。"""

    model_config = ConfigDict(extra="allow")

    manifest_id: str = Field(..., min_length=1, max_length=128)
    pipeline_id: str = Field(..., min_length=1, max_length=128)
    project_id: str = Field(..., min_length=1, max_length=128)
    schema_version: str = Field(default="openfilm.timeline_manifest.v1", min_length=1, max_length=64)
    version: int = Field(default=1, ge=1)
    tracks: List[TimelineTrack] = Field(..., min_length=1)
    metadata: Dict[str, Any] = Field(default_factory=dict)
    created_at: Optional[str] = None
    updated_at: Optional[str] = None

    @field_validator("manifest_id", "pipeline_id", "project_id")
    @classmethod
    def validate_manifest_identifier(cls, value: str, info):
        return _timeline_identifier(value, info.field_name)

    @field_validator("schema_version")
    @classmethod
    def validate_schema_version(cls, value: str):
        if not value.isascii() or any(ch.isspace() for ch in value):
            raise ValueError("schema_version 必须是 ASCII 标识")
        return value


def _ensure_timeline_state(raw: Dict[str, Any]) -> None:
    """兼容 C12-T3 之前已落盘的 episode_pipeline 快照。"""
    if not isinstance(raw.get("timeline_manifests"), dict):
        raw["timeline_manifests"] = {}
    if not isinstance(raw.get("timeline_seq"), int):
        raw["timeline_seq"] = 0


def _timeline_response(raw: Dict[str, Any], manifest: Dict[str, Any]) -> Dict[str, Any]:
    """统一时间线响应，同时保留顶层字段方便现有前端逐步接入。"""
    clean = dict(manifest)
    response = {
        "timeline_manifest": clean,
        "manifest": clean,
        "revision": int(raw.get("revision") or 1),
        "data_status": DATA_STATUS_OK,
        "data_gaps": [],
    }
    response.update(clean)
    return response


def _timeline_payload(payload: Mapping[str, Any]) -> tuple[Dict[str, Any], Any]:
    """兼容直接 body 与 {timeline_manifest, expected_version} 两种输入。"""
    if not isinstance(payload, Mapping):
        raise CleanroomException(400, "INVALID_TIMELINE_MANIFEST", "timeline_manifest 请求体必须是对象")
    outer = dict(payload)
    nested = outer.get("timeline_manifest")
    if not isinstance(nested, Mapping):
        nested = outer.get("manifest")
    data = dict(nested) if isinstance(nested, Mapping) else dict(outer)
    expected = outer.get("expected_version", data.pop("expected_version", None))
    tracks = data.get("tracks")
    if tracks is None:
        clips = data.get("clips")
        if clips is None:
            clips = data.get("shots")
        if clips is not None:
            tracks = [{"track_id": "video-01", "kind": "video", "clips": clips}]
            data["tracks"] = tracks
    if not isinstance(data.get("tracks"), list) or not data["tracks"]:
        raise CleanroomException(400, "INVALID_TIMELINE_MANIFEST", "timeline_manifest 至少需要一个轨道")
    normalized_tracks = []
    for index, track in enumerate(data["tracks"], start=1):
        if not isinstance(track, Mapping):
            raise CleanroomException(400, "INVALID_TIMELINE_MANIFEST", "轨道必须是对象")
        item = dict(track)
        item.setdefault("track_id", f"track-{index:02d}")
        if "clips" not in item and "segments" in item:
            item["clips"] = item.pop("segments")
        normalized_tracks.append(item)
    data["tracks"] = normalized_tracks
    return data, expected


def _validated_timeline(
    raw: Dict[str, Any], pipeline_id: str, payload: Mapping[str, Any], *, version: int, manifest_id: str,
    now: str,
) -> Dict[str, Any]:
    pipeline = raw.get("pipelines", {}).get(pipeline_id)
    if pipeline is None:
        raise CleanroomException(404, "PIPELINE_NOT_FOUND", "剧集流水线不存在")
    data, _ = _timeline_payload(payload)
    supplied_pipeline = data.get("pipeline_id")
    if supplied_pipeline not in (None, "", pipeline_id):
        raise CleanroomException(400, "INVALID_TIMELINE_MANIFEST", "pipeline_id 与路径不一致")
    supplied_project = data.get("project_id")
    project_id = str(pipeline.get("project_id") or "")
    if supplied_project not in (None, "", project_id):
        raise CleanroomException(403, "PROJECT_CONTEXT_MISMATCH", "时间线项目与流水线项目不一致")
    data["manifest_id"] = str(data.get("manifest_id") or manifest_id)
    data["pipeline_id"] = pipeline_id
    data["project_id"] = project_id
    data["version"] = version
    data["created_at"] = data.get("created_at") or now
    data["updated_at"] = now
    try:
        model = TimelineManifest.model_validate(data)
    except ValidationError:
        raise CleanroomException(400, "INVALID_TIMELINE_MANIFEST", "timeline_manifest 结构不合法") from None
    tracks = model.tracks
    track_ids = [track.track_id for track in tracks]
    shot_ids = [clip.shot_id for track in tracks for clip in track.clips]
    if len(track_ids) != len(set(track_ids)) or len(shot_ids) != len(set(shot_ids)):
        raise CleanroomException(400, "INVALID_TIMELINE_MANIFEST", "轨道与 shot_id 必须唯一")
    return model.model_dump(mode="json", exclude_none=True)


def _required_timeline_version(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise CleanroomException(400, "INVALID_VERSION", "expected_version 必须为正整数")
    return value


def create_timeline_manifest(pipeline_id: str, payload: Mapping[str, Any]) -> Dict[str, Any]:
    """创建并持久化一个 OpenFilm timeline_manifest。"""
    def mutate(raw: Dict[str, Any]) -> Dict[str, Any]:
        _ensure_timeline_state(raw)
        if pipeline_id not in raw.get("pipelines", {}):
            raise CleanroomException(404, "PIPELINE_NOT_FOUND", "剧集流水线不存在")
        existing_id = next(
            (key for key, value in raw["timeline_manifests"].items() if value.get("pipeline_id") == pipeline_id),
            None,
        )
        if existing_id is not None:
            raise CleanroomException(409, "TIMELINE_MANIFEST_EXISTS", "该流水线已有 timeline_manifest")
        raw["timeline_seq"] = int(raw.get("timeline_seq") or 0) + 1
        manifest_id = "timeline-%04d" % raw["timeline_seq"]
        now = storage.now_iso()
        manifest = _validated_timeline(raw, pipeline_id, payload, version=1, manifest_id=manifest_id, now=now)
        raw["timeline_manifests"][manifest_id] = manifest
        _bump(raw)
        return _timeline_response(raw, manifest)

    return state().mutate(mutate)


def get_timeline_manifest(pipeline_id: str) -> Dict[str, Any]:
    """读取流水线的 timeline_manifest；不存在时明确 404。"""
    raw = state().read()
    _ensure_timeline_state(raw)
    if pipeline_id not in raw.get("pipelines", {}):
        raise CleanroomException(404, "PIPELINE_NOT_FOUND", "剧集流水线不存在")
    manifest = next(
        (value for value in raw["timeline_manifests"].values() if value.get("pipeline_id") == pipeline_id),
        None,
    )
    if manifest is None:
        raise CleanroomException(404, "TIMELINE_MANIFEST_NOT_FOUND", "timeline_manifest 不存在")
    return _timeline_response(raw, manifest)


def update_timeline_manifest(pipeline_id: str, payload: Mapping[str, Any]) -> Dict[str, Any]:
    """以 expected_version CAS 更新现有 timeline_manifest。"""
    data, expected = _timeline_payload(payload)
    if expected is None:
        raise CleanroomException(400, "EXPECTED_VERSION_REQUIRED", "更新 timeline_manifest 必须携带 expected_version")
    expected_int = _required_timeline_version(expected)

    def mutate(raw: Dict[str, Any]) -> Dict[str, Any]:
        _ensure_timeline_state(raw)
        if pipeline_id not in raw.get("pipelines", {}):
            raise CleanroomException(404, "PIPELINE_NOT_FOUND", "剧集流水线不存在")
        manifest = next(
            (value for value in raw["timeline_manifests"].values() if value.get("pipeline_id") == pipeline_id),
            None,
        )
        if manifest is None:
            raise CleanroomException(404, "TIMELINE_MANIFEST_NOT_FOUND", "timeline_manifest 不存在")
        current = int(manifest.get("version") or 1)
        if expected_int != current:
            raise VersionConflictException(expected_int, current, "timeline_manifest 版本冲突，请重新读取后重试")
        now = storage.now_iso()
        updated = _validated_timeline(
            raw, pipeline_id, data, version=current + 1,
            manifest_id=str(manifest.get("manifest_id") or ""), now=now,
        )
        raw["timeline_manifests"][updated["manifest_id"]] = updated
        _bump(raw)
        return _timeline_response(raw, updated)

    return state().mutate(mutate)


# 便于仓储调用方使用更语义化的名称；仍共享同一 JSON 命名空间与 CAS 实现。
initialize_timeline_manifest = create_timeline_manifest
list_timeline_manifest = get_timeline_manifest


def _find_stage(raw: Dict[str, Any], pipeline_id: str, stage_id: str) -> Dict[str, Any]:
    if pipeline_id not in raw["pipelines"]:
        raise CleanroomException(404, "PIPELINE_NOT_FOUND", "剧集流水线不存在")
    for item in raw["stages"].values():
        if item.get("pipeline_id") != pipeline_id:
            continue
        # 前端以阶段 key 调用，契约用 stage_id，二者都接受。
        if item.get("stage_id") == stage_id or item.get("stage_key") == stage_id:
            return item
    raise CleanroomException(404, "STAGE_NOT_FOUND", "流水线阶段不存在")


def _touch(raw: Dict[str, Any], pipeline: Dict[str, Any]) -> None:
    pipeline["updated_at"] = storage.now_iso()
    pipeline["version"] = int(pipeline.get("version") or 1) + 1


def start_stage(pipeline_id: str, stage_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    payload = payload if isinstance(payload, dict) else {}

    def mutate(raw: Dict[str, Any]) -> Dict[str, Any]:
        stage = _find_stage(raw, pipeline_id, stage_id)
        expected = payload.get("expected_version")
        if expected is not None and int(expected) != int(stage.get("version") or 1):
            raise VersionConflictException(int(expected), int(stage.get("version") or 1), "阶段版本冲突，请重新读取后重试")
        if stage.get("status") == "running":
            raise CleanroomException(409, "INVALID_STATE_TRANSITION", "阶段已在运行")
        if stage.get("status") in ("completed", "cancelled"):
            raise CleanroomException(409, "INVALID_STATE_TRANSITION", "阶段已终态，不能重新启动")
        stage["status"] = "running"
        stage["started_at"] = storage.now_iso()
        stage["finished_at"] = None
        stage["version"] = int(stage.get("version") or 1) + 1
        _touch(raw, raw["pipelines"][pipeline_id])
        _bump(raw)
        return {"stage": _stage_view(stage, raw["pipelines"][pipeline_id].get("prompt_item_ids") or {})}

    return state().mutate(mutate)


def complete_stage(pipeline_id: str, stage_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    payload = payload if isinstance(payload, dict) else {}

    def mutate(raw: Dict[str, Any]) -> Dict[str, Any]:
        stage = _find_stage(raw, pipeline_id, stage_id)
        if stage.get("status") == "pending":
            raise CleanroomException(409, "INVALID_STATE_TRANSITION", "阶段尚未启动，不能直接完成")
        if stage.get("status") in ("completed", "cancelled"):
            raise CleanroomException(409, "INVALID_STATE_TRANSITION", "阶段已终态")
        requested = str(payload.get("status") or "completed")
        if requested not in ("completed", "failed"):
            raise CleanroomException(400, "INVALID_REQUEST", "status 只接受 completed/failed")
        stage["status"] = requested
        stage["result"] = {
            "status": requested,
            "output_asset_ids": list(payload.get("output_asset_ids") or []),
            "output_text": str(payload.get("output_text") or ""),
            "source_job_id": str(payload.get("source_job_id") or ""),
            "message": str(payload.get("message") or ""),
        }
        stage["finished_at"] = storage.now_iso()
        stage["version"] = int(stage.get("version") or 1) + 1
        _touch(raw, raw["pipelines"][pipeline_id])
        _bump(raw)
        return {"stage": _stage_view(stage, raw["pipelines"][pipeline_id].get("prompt_item_ids") or {})}

    return state().mutate(mutate)


def cancel_stage(pipeline_id: str, stage_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    payload = payload if isinstance(payload, dict) else {}

    def mutate(raw: Dict[str, Any]) -> Dict[str, Any]:
        stage = _find_stage(raw, pipeline_id, stage_id)
        expected = payload.get("expected_version")
        if expected is not None and int(expected) != int(stage.get("version") or 1):
            raise VersionConflictException(int(expected), int(stage.get("version") or 1), "阶段版本冲突，请重新读取后重试")
        if stage.get("status") in ("completed", "cancelled"):
            raise CleanroomException(409, "INVALID_STATE_TRANSITION", "阶段已终态，不能取消")
        stage["status"] = "cancelled"
        stage["result"] = {"status": "cancelled", "message": str(payload.get("message") or "")}
        stage["finished_at"] = storage.now_iso()
        stage["version"] = int(stage.get("version") or 1) + 1
        _touch(raw, raw["pipelines"][pipeline_id])
        _bump(raw)
        return {"stage": _stage_view(stage, raw["pipelines"][pipeline_id].get("prompt_item_ids") or {})}

    return state().mutate(mutate)


def _idempotency_key(value: Any) -> str:
    key = str(value or "").strip()
    if not key or len(key) > 128 or any(ord(char) < 0x21 or ord(char) > 0x7E for char in key):
        raise CleanroomException(400, "IDEMPOTENCY_KEY_REQUIRED", "必须提供有效的 Idempotency-Key")
    return key


def _shot_identifier(value: Any) -> str:
    shot_id = str(value or "").strip()
    if not shot_id or len(shot_id) > 128 or not shot_id.isascii() or any(char.isspace() for char in shot_id):
        raise CleanroomException(400, "INVALID_SHOT_ID", "shot_id 必须是非空 ASCII 标识")
    return shot_id


def _request_fingerprint(payload: Dict[str, Any]) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _shot_record(raw: Dict[str, Any], pipeline_id: str, shot_id: str, *, generation_id: Optional[str] = None) -> Dict[str, Any]:
    _ensure_shot_state(raw)
    candidates = [
        item for item in raw["shots"].values()
        if item.get("pipeline_id") == pipeline_id and item.get("shot_id") == shot_id
        and (generation_id is None or item.get("generation_id") == generation_id)
    ]
    if not candidates:
        raise CleanroomException(404, "SHOT_NOT_FOUND", "镜头生成记录不存在")
    return max(candidates, key=lambda item: int(item.get("sequence") or 0))


def _shot_source(target: str, workflow_id: str, output: Dict[str, Any], asset_id: str) -> Dict[str, Any]:
    source_url = output.get("url") or output.get("content_url")
    if not isinstance(source_url, str) or not source_url.strip():
        source_url = f"/api/god_workflow/assets/{asset_id}/content"
    source = {"provider": target, "source_url": source_url}
    if workflow_id:
        source["workflow_artifact_id"] = workflow_id
    return source


def _task_state(task: Dict[str, Any]) -> tuple[str, str]:
    status = str(task.get("status") or task.get("execution_status") or "").strip().lower()
    if status in {"accepted", "queued", "pending"}:
        return "queued", "poll_job"
    if status in {"running", "processing"}:
        return "running", "poll_job"
    if status in {"completed", "succeeded", "success"}:
        return "completed", "review_source"
    if status in {"failed", "cancelled", "canceled", "abandoned"}:
        return ("failed" if status == "failed" else "failed"), "retry_generation"
    if status in {"outcome_unknown", "unknown", "interrupted"}:
        return "unknown", "reconcile_provider"
    return "waiting", "poll_job"


def _registered_output(task: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    outputs = task.get("outputs")
    if not isinstance(outputs, list):
        return None
    for output in outputs:
        if not isinstance(output, dict) or not output.get("registered"):
            continue
        asset_id = str(output.get("asset_id") or "").strip()
        if asset_id:
            return output
    return None


def reserve_single_shot(
    pipeline_id: str,
    shot_id: str,
    payload: Dict[str, Any],
    idempotency_key: Any,
    *,
    actor_key: str = "",
) -> Dict[str, Any]:
    """原子预留一条镜头生成意图，确保外部副作用前已有可恢复账本。"""
    if not isinstance(payload, dict):
        raise CleanroomException(400, "INVALID_REQUEST", "请求体必须是 JSON 对象")
    shot_id = _shot_identifier(shot_id)
    key = _idempotency_key(idempotency_key)
    fingerprint = _request_fingerprint(payload)
    source = payload.get("source_context") or {}
    if not isinstance(source, dict):
        raise CleanroomException(400, "INVALID_REQUEST", "来源上下文必须是对象")

    def mutate(raw: Dict[str, Any]) -> Dict[str, Any]:
        _ensure_shot_state(raw)
        pipeline = raw["pipelines"].get(pipeline_id)
        if pipeline is None:
            raise CleanroomException(404, "PIPELINE_NOT_FOUND", "剧集流水线不存在")
        for item in raw["shots"].values():
            if item.get("idempotency_key") != key:
                continue
            if item.get("request_fingerprint") != fingerprint:
                raise CleanroomException(409, "IDEMPOTENCY_CONFLICT", "幂等键已用于不同请求")
            if item.get("pipeline_id") != pipeline_id or item.get("shot_id") != shot_id:
                raise CleanroomException(409, "IDEMPOTENCY_CONFLICT", "幂等键已用于另一镜头")
            if actor_key and item.get("actor_key") and item.get("actor_key") != actor_key:
                raise CleanroomException(404, "SHOT_NOT_FOUND", "镜头生成记录不存在")
            return {
                "shot": _shot_view(item),
                "generation_id": item["generation_id"],
                # 幂等重放绝不重试不确定的外部副作用；原请求仍在提交时
                # 保留当前账本，调用方可按 next_action 继续读取或人工协调。
                "needs_submit": False,
                "idempotent_replay": True,
            }

        source_project = str(source.get("project_id") or pipeline.get("project_id") or "")
        if source_project != str(pipeline.get("project_id") or ""):
            raise CleanroomException(403, "PROJECT_CONTEXT_MISMATCH", "镜头来源项目与流水线项目不一致")

        expected = payload.get("expected_version")
        if expected is not None:
            try:
                expected_int = int(expected)
            except (TypeError, ValueError):
                raise CleanroomException(400, "INVALID_VERSION", "expected_version 必须为正整数") from None
            if expected_int <= 0:
                raise CleanroomException(400, "INVALID_VERSION", "expected_version 必须为正整数")
            if expected_int != int(pipeline.get("version") or 1):
                raise VersionConflictException(expected_int, int(pipeline.get("version") or 1), "流水线版本冲突，请重新读取后重试")

        latest = _latest_shot_records(raw, pipeline_id)
        prior = next((item for item in latest if item.get("shot_id") == shot_id), None)
        if prior is not None and prior.get("status") in SHOT_ACTIVE_STATUS:
            raise CleanroomException(409, "SHOT_GENERATION_IN_PROGRESS", "该镜头已有生成任务，请使用原幂等键查询")

        raw["shot_seq"] = int(raw.get("shot_seq") or 0) + 1
        sequence = raw["shot_seq"]
        version = 1 + max(
            [int(item.get("version") or 0) for item in raw["shots"].values()
             if item.get("pipeline_id") == pipeline_id and item.get("shot_id") == shot_id] or [0]
        )
        now = storage.now_iso()
        generation_id = "shot-%04d" % sequence
        target = str(payload.get("target") or "local_comfy").strip().lower()
        if target == "comfyui":
            target = "local_comfy"
        elif target == "runninghub":
            target = "rh_cloud"
        if target not in {"local_comfy", "rh_cloud"}:
            raise CleanroomException(400, "INVALID_TARGET", "不支持的执行目标")
        item = {
            "generation_id": generation_id,
            "sequence": sequence,
            "pipeline_id": pipeline_id,
            "project_id": source_project,
            "entity_id": str(source.get("entity_id") or payload.get("entity_id") or ""),
            "episode_id": str(source.get("episode_id") or payload.get("episode_id") or ""),
            "scene_id": str(source.get("scene_id") or payload.get("scene_id") or ""),
            "canvas_id": str(source.get("canvas_id") or payload.get("canvas_id") or ""),
            "shot_id": shot_id,
            "status": "submitting",
            "next_action": "submit_generation",
            "target": target,
            "workflow_id": str(payload.get("workflow_id") or ""),
            "version": version,
            "idempotency_key": key,
            "request_fingerprint": fingerprint,
            "actor_key": actor_key,
            "data_gaps": [],
            "error": {},
            "created_at": now,
            "updated_at": now,
        }
        raw["shots"][generation_id] = item
        _touch(raw, pipeline)
        _bump(raw)
        return {"shot": _shot_view(item), "generation_id": generation_id, "needs_submit": True, "idempotent_replay": False}

    return state().mutate(mutate)


def bind_single_shot(pipeline_id: str, shot_id: str, task: Dict[str, Any]) -> Dict[str, Any]:
    """把执行器返回的真实任务/资产绑定到镜头；不从缺失字段推造成功。"""
    if not isinstance(task, dict):
        raise CleanroomException(502, "WORKFLOW_PROVIDER_INVALID_RESPONSE", "执行器响应无效")

    def mutate(raw: Dict[str, Any]) -> Dict[str, Any]:
        item = _shot_record(raw, pipeline_id, _shot_identifier(shot_id))
        pipeline = raw["pipelines"][pipeline_id]
        job_id = str(task.get("job_id") or task.get("task_id") or "").strip()
        if job_id and item.get("job_id") and item.get("job_id") != job_id:
            raise CleanroomException(409, "IDEMPOTENCY_CONFLICT", "任务已绑定到另一 job_id")
        if item.get("status") in {"completed", "failed", "unknown"} and not job_id:
            return {"shot": _shot_view(item), "data_status": "ok", "data_gaps": list(item.get("data_gaps") or [])}

        status, next_action = _task_state(task)
        if not job_id:
            status, next_action = "unknown", "reconcile_provider"
        output = _registered_output(task)
        if status == "completed" and output is None:
            status, next_action = "waiting", "collect_asset"
        item["status"] = status
        item["next_action"] = next_action
        if job_id:
            item["job_id"] = job_id
        remote_id = str(task.get("remote_task_id") or task.get("prompt_id") or "").strip()
        if remote_id:
            item["remote_task_id"] = remote_id
        target = str(task.get("target") or item.get("target") or "")
        if target:
            item["target"] = target
        gaps = task.get("data_gaps")
        item["data_gaps"] = [str(gap) for gap in gaps if str(gap).strip()] if isinstance(gaps, list) else []
        messages = task.get("messages")
        remote_id_missing = bool(task.get("remote_id_missing")) or "remote_task_id_missing" in item["data_gaps"]
        if remote_id_missing:
            status, next_action = "unknown", "reconcile_provider"
        if not job_id and "job_id_missing" not in item["data_gaps"]:
            item["data_gaps"].append("job_id_missing")
        if remote_id_missing and "remote_task_id_missing" not in item["data_gaps"]:
            item["data_gaps"].append("remote_task_id_missing")
        item["status"] = status
        item["next_action"] = next_action
        if status in {"failed", "unknown", "waiting"}:
            code = str(task.get("error_code") or (messages[0] if isinstance(messages, list) and messages else ""))
            if code:
                item["error"] = {"code": code, "message": "任务状态需要人工处理"}

        # 资产只能绑定在有稳定本地 job_id、已确认完成且输出已登记时。
        # 缺少远端身份或处于失败/未知态的输出不能升级为成功资产。
        if output is not None and job_id and status == "completed":
            asset_id = str(output.get("asset_id") or "").strip()
            item["asset_id"] = asset_id
            item["version_id"] = "%s-v%03d" % (asset_id, int(item.get("version") or 1))
            item["source"] = _shot_source(target, str(item.get("workflow_id") or task.get("workflow_id") or ""), output, asset_id)

        item["updated_at"] = storage.now_iso()
        _touch(raw, pipeline)
        _bump(raw)
        return {"shot": _shot_view(item), "data_status": "ok", "data_gaps": list(item.get("data_gaps") or [])}

    return state().mutate(mutate)


def mark_single_shot_failure(pipeline_id: str, shot_id: str, code: str, message: str) -> Dict[str, Any]:
    """记录执行器未接入、失败或未知，供轮询/刷新显示下一步。"""
    def mutate(raw: Dict[str, Any]) -> Dict[str, Any]:
        item = _shot_record(raw, pipeline_id, _shot_identifier(shot_id))
        pipeline = raw["pipelines"][pipeline_id]
        code_value = str(code or "WORKFLOW_EXECUTION_FAILED")
        if code_value in {"WORKFLOW_EXECUTOR_UNAVAILABLE", "NODE_SCHEMA_REQUIRED", "VIDEO_PROVIDER_NOT_CONFIGURED"}:
            status, next_action = "waiting", "configure_executor"
        elif code_value in {"WORKFLOW_SUBMIT_RESULT_UNKNOWN", "OUTCOME_UNKNOWN_NO_REMOTE_ID", "RH_TASK_STATUS_UNKNOWN"}:
            status, next_action = "unknown", "reconcile_provider"
        else:
            status, next_action = "failed", "retry_generation"
        item.update({
            "status": status,
            "next_action": next_action,
            "data_gaps": [code_value],
            "error": {"code": code_value, "message": str(message or "任务未完成")[:300]},
            "updated_at": storage.now_iso(),
        })
        _touch(raw, pipeline)
        _bump(raw)
        return {"shot": _shot_view(item), "data_status": "degraded", "data_gaps": [code_value]}
    return state().mutate(mutate)


def get_single_shot(pipeline_id: str, shot_id: str) -> Dict[str, Any]:
    raw = state().read()
    if pipeline_id not in raw.get("pipelines", {}):
        raise CleanroomException(404, "PIPELINE_NOT_FOUND", "剧集流水线不存在")
    item = _shot_record(raw, pipeline_id, _shot_identifier(shot_id))
    return {"shot": _shot_view(item), "revision": raw.get("revision", 1), "data_status": "ok", "data_gaps": list(item.get("data_gaps") or [])}
