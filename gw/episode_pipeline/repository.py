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
- 单进程/单实例一致性；多 worker 并发写属部署方职责。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from gw.core import storage
from gw.core.errors import CleanroomException, VersionConflictException

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


def state() -> storage.JsonState:
    return storage.JsonState(NS, _blank)


def _blank() -> Dict[str, Any]:
    return {"revision": 1, "pipelines": {}, "stages": {}, "seq": 0}


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


def _pipeline_view(raw: Dict[str, Any], item: Dict[str, Any]) -> Dict[str, Any]:
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
