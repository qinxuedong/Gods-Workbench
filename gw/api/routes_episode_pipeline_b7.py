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

"""Phase 12 A3 剧集流水线 API（真实落盘）。

数据来源：``gw.episode_pipeline.repository``
（core.storage.JsonState 命名空间 ``episode_pipeline``），重启可恢复。

阶段 start/complete/cancel 仍只推进状态机；C8-T2 的单镜头入口复用既有
ComfyUI/RunningHub 工作流执行器，并把真实任务和资产回写到流水线快照。
单实例一致性，多 worker 并发写属部署方职责。
"""

from __future__ import annotations

import json
from typing import Any, Dict, Optional

from fastapi import APIRouter, Header, Request, status

from gw.core.auth import require_authenticated, require_edit_access
from gw.core.errors import CleanroomException
from gw.episode_pipeline import repository as repo
from gw.god_workflow import routes as workflow_routes

router = APIRouter(tags=["episode-pipeline-b7"])


def _read(authorization: str | None, role: str):
    return require_authenticated(authorization, role)


def _write(authorization: str | None, role: str):
    return require_edit_access(authorization, role)


async def _json(request: Request) -> dict[str, Any]:
    try:
        payload = await request.json()
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _internal_workflow_request(payload: dict[str, Any]) -> Request:
    """把已认证的单镜头请求交给现有工作流 handler，不复制执行逻辑。"""
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    sent = False

    async def receive():
        nonlocal sent
        if sent:
            return {"type": "http.request", "body": b"", "more_body": False}
        sent = True
        return {"type": "http.request", "body": raw, "more_body": False}

    scope = {
        "type": "http",
        "method": "POST",
        "path": "/api/god_workflow/tasks/execute",
        "raw_path": b"/api/god_workflow/tasks/execute",
        "root_path": "",
        "scheme": "http",
        "query_string": b"",
        "headers": [],
        "client": ("episode-pipeline", 0),
        "server": ("episode-pipeline", 80),
    }
    return Request(scope, receive)


@router.get("/api/episode-pipelines", status_code=status.HTTP_200_OK)
def list_pipelines(
    project_id: str | None = None,
    authorization: str | None = Header(None),
    x_user_role: str = Header("editor", alias="X-User-Role"),
):
    """按项目读取流水线集合；空集合返回 pipelines: []。"""
    _read(authorization, x_user_role)
    return repo.list_pipelines(project_id)


@router.post("/api/episode-pipelines", status_code=status.HTTP_201_CREATED)
async def create_pipeline(
    request: Request,
    authorization: str | None = Header(None),
    x_user_role: str = Header("editor", alias="X-User-Role"),
):
    """创建流水线并建立 4 个阶段（script/assets/video/audio_compose）。"""
    _write(authorization, x_user_role)
    return repo.create_pipeline(await _json(request))


@router.get("/api/episode-pipelines/{pipeline_id}", status_code=status.HTTP_200_OK)
def get_pipeline(
    pipeline_id: str,
    authorization: str | None = Header(None),
    x_user_role: str = Header("editor", alias="X-User-Role"),
):
    """读取单个流水线；不存在 404。"""
    _read(authorization, x_user_role)
    return repo.get_pipeline(pipeline_id)


@router.post(
    "/api/episode-pipelines/{pipeline_id}/stages/{stage_id}/start",
    status_code=status.HTTP_200_OK,
)
async def start_stage(
    pipeline_id: str,
    stage_id: str,
    request: Request,
    authorization: str | None = Header(None),
    x_user_role: str = Header("editor", alias="X-User-Role"),
):
    """把阶段推进到 running；非法流转 409，CAS 冲突 409。"""
    _write(authorization, x_user_role)
    return repo.start_stage(pipeline_id, stage_id, await _json(request))


@router.post(
    "/api/episode-pipelines/{pipeline_id}/stages/{stage_id}/complete",
    status_code=status.HTTP_200_OK,
)
async def complete_stage(
    pipeline_id: str,
    stage_id: str,
    request: Request,
    authorization: str | None = Header(None),
    x_user_role: str = Header("editor", alias="X-User-Role"),
):
    """把阶段推进到 completed/failed；未启动直接完成 409。"""
    _write(authorization, x_user_role)
    return repo.complete_stage(pipeline_id, stage_id, await _json(request))


@router.post(
    "/api/episode-pipelines/{pipeline_id}/stages/{stage_id}/cancel",
    status_code=status.HTTP_200_OK,
)
async def cancel_stage(
    pipeline_id: str,
    stage_id: str,
    request: Request,
    authorization: str | None = Header(None),
    x_user_role: str = Header("editor", alias="X-User-Role"),
):
    """取消阶段；已终态 409。"""
    _write(authorization, x_user_role)
    return repo.cancel_stage(pipeline_id, stage_id, await _json(request))


@router.get(
    "/api/episode-pipelines/{pipeline_id}/timeline-manifest",
    status_code=status.HTTP_200_OK,
    operation_id="get_timeline_manifest_hyphen",
)
@router.get(
    "/api/episode-pipelines/{pipeline_id}/timeline_manifest",
    status_code=status.HTTP_200_OK,
    operation_id="get_timeline_manifest_snake",
)
@router.get(
    "/api/episode-pipelines/{pipeline_id}/timeline",
    status_code=status.HTTP_200_OK,
    operation_id="get_timeline_manifest_legacy",
)
def get_timeline_manifest(
    pipeline_id: str,
    authorization: str | None = Header(None),
    x_user_role: str = Header("editor", alias="X-User-Role"),
):
    """读取 OpenFilm timeline_manifest；数据来自 episode_pipeline 落盘命名空间。"""
    _read(authorization, x_user_role)
    return repo.get_timeline_manifest(pipeline_id)


@router.post(
    "/api/episode-pipelines/{pipeline_id}/timeline-manifest",
    status_code=status.HTTP_201_CREATED,
    operation_id="create_timeline_manifest_hyphen",
)
@router.post(
    "/api/episode-pipelines/{pipeline_id}/timeline_manifest",
    status_code=status.HTTP_201_CREATED,
    operation_id="create_timeline_manifest_snake",
)
@router.post(
    "/api/episode-pipelines/{pipeline_id}/timeline",
    status_code=status.HTTP_201_CREATED,
    operation_id="create_timeline_manifest_legacy",
)
async def create_timeline_manifest(
    pipeline_id: str,
    request: Request,
    authorization: str | None = Header(None),
    x_user_role: str = Header("editor", alias="X-User-Role"),
):
    """初始化轻量时间线清单；片段可携带 <img> 的 src 与 #t 覆盖属性。"""
    _write(authorization, x_user_role)
    return repo.create_timeline_manifest(pipeline_id, await _json(request))


@router.put(
    "/api/episode-pipelines/{pipeline_id}/timeline-manifest",
    status_code=status.HTTP_200_OK,
    operation_id="update_timeline_manifest_hyphen",
)
@router.put(
    "/api/episode-pipelines/{pipeline_id}/timeline_manifest",
    status_code=status.HTTP_200_OK,
    operation_id="update_timeline_manifest_snake",
)
@router.put(
    "/api/episode-pipelines/{pipeline_id}/timeline",
    status_code=status.HTTP_200_OK,
    operation_id="update_timeline_manifest_legacy",
)
async def update_timeline_manifest(
    pipeline_id: str,
    request: Request,
    authorization: str | None = Header(None),
    x_user_role: str = Header("editor", alias="X-User-Role"),
):
    """更新时间线清单；必须携带 expected_version 执行 CAS。"""
    _write(authorization, x_user_role)
    return repo.update_timeline_manifest(pipeline_id, await _json(request))


@router.post(
    "/api/episode-pipelines/{pipeline_id}/shots/{shot_id}/generate",
    status_code=status.HTTP_202_ACCEPTED,
)
async def generate_single_shot(
    pipeline_id: str,
    shot_id: str,
    request: Request,
    idempotency_key: str | None = Header(None, alias="Idempotency-Key"),
    authorization: str | None = Header(None),
    x_user_role: str = Header("editor", alias="X-User-Role"),
):
    """提交一个镜头到真实工作流执行器，并持久化来源绑定。"""
    context = _write(authorization, x_user_role)
    payload = await _json(request)
    pipeline = repo.get_pipeline(pipeline_id)["pipeline"]
    source = payload.get("source_context")
    if source is None:
        source = {}
    if isinstance(source, dict):
        source = dict(source)
        source.setdefault("project_id", pipeline.get("project_id") or "")
        source.setdefault("shot_id", shot_id)
        payload["source_context"] = source
    reserved = repo.reserve_single_shot(
        pipeline_id,
        shot_id,
        payload,
        idempotency_key,
        actor_key=f"{getattr(context, 'identity_domain', '')}:{getattr(context, 'subject', '')}",
    )
    if not reserved["needs_submit"]:
        return {
            "shot": reserved["shot"],
            "data_status": "ok",
            "data_gaps": reserved["shot"].get("data_gaps", []),
        }

    try:
        execution = await workflow_routes.execute_task(
            _internal_workflow_request(payload),
            authorization=authorization,
            x_user_role=x_user_role,
            idempotency_key=idempotency_key,
        )
        if not isinstance(execution, dict):
            raise CleanroomException(
                502, "WORKFLOW_PROVIDER_INVALID_RESPONSE", "执行器未返回任务对象"
            )
    except CleanroomException as exc:
        repo.mark_single_shot_failure(pipeline_id, shot_id, exc.code, exc.message)
        raise
    except Exception as exc:
        repo.mark_single_shot_failure(
            pipeline_id, shot_id, "WORKFLOW_SUBMIT_FAILED", type(exc).__name__
        )
        raise CleanroomException(
            503, "WORKFLOW_SUBMIT_FAILED", "工作流执行器提交失败，请读取镜头状态"
        ) from None

    return repo.bind_single_shot(pipeline_id, shot_id, execution)


@router.get(
    "/api/episode-pipelines/{pipeline_id}/shots/{shot_id}",
    status_code=status.HTTP_200_OK,
)
async def get_single_shot(
    pipeline_id: str,
    shot_id: str,
    authorization: str | None = Header(None),
    x_user_role: str = Header("editor", alias="X-User-Role"),
):
    """读取镜头生成快照；有真实 job_id 时只查询既有任务，不重新提交。"""
    _read(authorization, x_user_role)
    current = repo.get_single_shot(pipeline_id, shot_id)
    shot = current["shot"]
    if not shot.get("job_id") or shot.get("status") in {
        "completed",
        "failed",
        "unknown",
    }:
        return current
    try:
        execution = await workflow_routes.task_status(
            shot["job_id"], authorization=authorization, x_user_role=x_user_role
        )
    except CleanroomException as exc:
        if exc.status_code >= 500:
            return repo.mark_single_shot_failure(
                pipeline_id, shot_id, exc.code, exc.message
            )
        raise
    return repo.bind_single_shot(pipeline_id, shot_id, execution)
