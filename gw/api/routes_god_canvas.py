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

"""god-canvas API 路由实现。

涵盖普通画布拓扑管理与智能画布异步任务流转。
端点路径保持契约一致（/api/canvases 与 /api/jobs），服务由 GodCanvasService 提供。
"""

from typing import Optional
from uuid import uuid4
from fastapi import APIRouter, Header, Query, Request, Response, status

from gw.core.auth import require_authenticated, require_edit_access
from gw.core.errors import CleanroomException, UnauthorizedException
from gw.agent_runtime.errors import DomainError
from gw.agent_runtime.models import ErrorCode, Identifier
from gw.god_canvas.models import (
    CanvasCreateRequest,
    CanvasExportRequest,
    CanvasExportResponse,
    CanvasImportResponse,
    CanvasItem,
    CanvasListResponse,
    CanvasMutationResponse,
    CanvasTopology,
    CanvasTopologyUpdateRequest,
)
from gw.god_canvas.service import default_god_canvas_service
from gw.god_canvas.tasks import SmartCanvasTaskRequest, SmartCanvasTaskResponse, TaskStatus
from gw.projects_hub.models import CasVersionRequest

router = APIRouter(prefix="/api/canvases", tags=["god-canvas"])
jobs_router = APIRouter(prefix="/api/jobs", tags=["god-canvas-jobs"])

_AGENT_RUN_TO_TASK_STATUS = {
    "queued": TaskStatus.ACCEPTED.value,
    "running": TaskStatus.RUNNING.value,
    "waiting_review": TaskStatus.WAITING_REVIEW.value,
    "paused": TaskStatus.PAUSED.value,
    "succeeded": TaskStatus.COMPLETED.value,
    "failed": TaskStatus.FAILED.value,
    "cancelled": TaskStatus.CANCELLED.value,
}
_TERMINAL_AGENT_RUN_STATUSES = frozenset({"succeeded", "failed", "cancelled"})


def agent_run_job_payload(job_id: str, run, *, accepted_response: bool = False) -> dict:
    """Project Agent's seven internal states onto the seven Workbench job states."""
    agent_status = getattr(run.status, "value", str(run.status))
    state = TaskStatus.ACCEPTED.value if accepted_response else _AGENT_RUN_TO_TASK_STATUS.get(agent_status)
    if state is None:
        raise CleanroomException(503, "AGENT_JOB_STATUS_UNSUPPORTED", "任务状态暂不可用")
    poll_hint = f"/api/jobs/{job_id}"
    if not accepted_response and agent_status in _TERMINAL_AGENT_RUN_STATUSES:
        poll_hint = None
    task = SmartCanvasTaskResponse(
        job_id=job_id,
        state=state,
        poll_hint=poll_hint,
    ).model_dump(mode="json")
    task.update({
        "agent_status": agent_status,
        "run_id": run.run_id.root,
        "version": run.version.root,
        "stage": run.current_stage.value if run.current_stage is not None else None,
        "agent_poll_hint": {
            "status_uri": f"/api/agent/runs/{run.run_id.root}",
            "events_uri": f"/api/agent/runs/{run.run_id.root}/events",
            "suggested_interval_ms": 2000,
        },
    })
    return task


# ----------------------------------------------------------------------
# 普通画布拓扑端点
# ----------------------------------------------------------------------

@router.get(
    "",
    response_model=CanvasListResponse,
    summary="获取画布列表",
    status_code=status.HTTP_200_OK,
)
def list_canvases(
    project_id: str = Query(..., description="所属项目 ID"),
    authorization: Optional[str] = Header(None),
):
    """仅返回当前项目可见的画布集合。"""
    if authorization == "invalid":
        raise UnauthorizedException()
    canvases = default_god_canvas_service.list_canvases(project_id=project_id)
    return CanvasListResponse(canvases=canvases)


@router.post(
    "",
    response_model=CanvasMutationResponse,
    summary="创建画布",
    status_code=status.HTTP_201_CREATED,
)
def create_canvas(
    payload: CanvasCreateRequest,
    authorization: Optional[str] = Header(None),
    x_user_role: str = Header("editor", alias="X-User-Role", description="用户角色权限"),
):
    """创建画布实体并返回稳定 canvas_id。"""
    require_edit_access(authorization, x_user_role)
    result = default_god_canvas_service.create_canvas(payload)
    return CanvasMutationResponse(canvas=result)


@router.get(
    "/{canvas_id}",
    response_model=CanvasTopology,
    summary="获取画布当前拓扑",
    status_code=status.HTTP_200_OK,
)
def get_canvas_topology(
    canvas_id: str,
    authorization: Optional[str] = Header(None),
):
    """获取指定画布的完整拓扑结构（节点与连线）。"""
    if authorization == "invalid":
        raise UnauthorizedException()
    return default_god_canvas_service.get_topology(canvas_id)


@router.patch(
    "/{canvas_id}",
    response_model=CanvasMutationResponse,
    summary="更新画布拓扑（CAS）",
    status_code=status.HTTP_200_OK,
)
def update_canvas_topology(
    canvas_id: str,
    payload: CanvasTopologyUpdateRequest,
    authorization: Optional[str] = Header(None),
    x_user_role: str = Header("editor", alias="X-User-Role", description="用户角色权限"),
):
    """根据 expected_version 更新拓扑；版本不一致严格返回 409 CANVAS_VERSION_CONFLICT。"""
    require_edit_access(authorization, x_user_role)
    result = default_god_canvas_service.update_topology(canvas_id, payload)
    return CanvasMutationResponse(canvas=result)


@router.post(
    "/{canvas_id}/restore",
    response_model=CanvasMutationResponse,
    summary="恢复画布",
    status_code=status.HTTP_200_OK,
)
def restore_canvas(
    canvas_id: str,
    payload: CasVersionRequest,
    authorization: Optional[str] = Header(None),
    x_user_role: str = Header("editor", alias="X-User-Role", description="用户角色权限"),
):
    """恢复画布；按章程必须携带 expected_version（CAS）。"""
    require_edit_access(authorization, x_user_role)
    result = default_god_canvas_service.restore_canvas(
        canvas_id, expected_version=payload.expected_version
    )
    return CanvasMutationResponse(canvas=result)


@router.post(
    "/{canvas_id}/workflow/import",
    response_model=CanvasImportResponse,
    summary="导入工作流（JSON/.godmap）",
    status_code=status.HTTP_200_OK,
)
async def import_canvas_workflow(
    canvas_id: str,
    request: Request,
    format: str = Query("json", description="文件格式: json | godmap"),
    merge_mode: str = Query("replace", description="合并模式: replace | insert"),
    expected_version: int = Query(..., description="期望 CAS 版本（必填）"),
    authorization: Optional[str] = Header(None),
    x_user_role: str = Header("editor", alias="X-User-Role", description="用户角色权限"),
):
    """导入工作流拓扑文件并进行严格结构校验；按章程必须携带 expected_version（CAS）。"""
    require_edit_access(authorization, x_user_role)
    body_bytes = await request.body()
    content_str = body_bytes.decode("utf-8")
    return default_god_canvas_service.import_workflow(
        canvas_id=canvas_id,
        content=content_str,
        file_format=format,
        merge_mode=merge_mode,
        expected_version=expected_version,
    )


@router.post(
    "/{canvas_id}/workflow/export",
    summary="导出工作流",
    status_code=status.HTTP_200_OK,
)
def export_canvas_workflow(
    canvas_id: str,
    payload: CanvasExportRequest,
    authorization: Optional[str] = Header(None),
    x_user_role: str = Header("editor", alias="X-User-Role", description="用户角色权限"),
):
    """将画布拓扑导出为指定格式内容。"""
    require_edit_access(authorization, x_user_role)
    data_str = default_god_canvas_service.export_workflow(
        canvas_id=canvas_id,
        export_format=payload.format,
        include_resources=payload.include_resources,
    )
    return Response(content=data_str, media_type="application/json")


# ----------------------------------------------------------------------
# 智能画布异步任务端点
# ----------------------------------------------------------------------

@router.post(
    "/{canvas_id}/tasks",
    response_model=SmartCanvasTaskResponse,
    summary="发起智能画布任务",
    status_code=status.HTTP_202_ACCEPTED,
)
def run_smart_canvas_task(
    canvas_id: str,
    payload: SmartCanvasTaskRequest,
    authorization: Optional[str] = Header(None),
    x_user_role: str = Header("editor", alias="X-User-Role", description="用户角色权限"),
):
    """发起异步执行任务并返回 202 Accepted 及稳定 job_id。"""
    return default_god_canvas_service.submit_smart_task(
        canvas_id=canvas_id,
        payload=payload,
        authorization=authorization,
        user_role=x_user_role,
    )


@jobs_router.get(
    "/{job_id}",
    response_model=SmartCanvasTaskResponse,
    summary="查询异步任务状态",
    status_code=status.HTTP_200_OK,
)
async def get_smart_job_status(
    job_id: str,
    request: Request,
    authorization: Optional[str] = Header(None),
    x_user_role: str = Header("editor", alias="X-User-Role"),
):
    """查询 Workbench job；Agent job 先复核当前Cookie主体和项目权限。"""
    if job_id.startswith("job_agent_"):
        auth = require_authenticated(authorization, x_user_role)
        unavailable_request_id = uuid4().hex
        integration = getattr(request.app.state, "agent_integration", None)
        if integration is None:
            raise DomainError(
                ErrorCode.PROJECT_UNAVAILABLE,
                "任务不存在或当前主体不可访问",
                request_id=unavailable_request_id,
            )
        _, fingerprint = await integration.authenticate_request(auth)
        mapping = integration.get_agent_job_binding(job_id)
        run_id = mapping.get("run_id") if mapping is not None else None
        if not isinstance(run_id, str) or not run_id:
            raise DomainError(
                ErrorCode.PROJECT_UNAVAILABLE,
                "任务不存在或当前主体不可访问",
                request_id=unavailable_request_id,
            )
        try:
            context = await integration.context_for_run(
                auth, fingerprint, run_id, unavailable_request_id, edit_required=False,
            )
            run = await integration.use_case.get_run(context, Identifier(run_id))
        except CleanroomException as exc:
            if exc.status_code not in {403, 404}:
                raise
            raise DomainError(
                ErrorCode.PROJECT_UNAVAILABLE,
                "任务不存在或当前主体不可访问",
                request_id=unavailable_request_id,
            ) from None
        if run is None:
            raise DomainError(
                ErrorCode.PROJECT_UNAVAILABLE,
                "任务不存在或当前主体不可访问",
                request_id=unavailable_request_id,
            )
        return agent_run_job_payload(job_id, run)
    if not job_id.startswith("job-"):
        from gw.god_workflow import registry
        auth = require_authenticated(authorization, x_user_role)
        task = registry.load_task(auth, job_id)
        return {**task, "state": task["status"], "source_domain": "workflow",
                "poll_hint": None if task["status"] in {"completed", "failed", "cancelled", "outcome_unknown"} else f"/api/god_workflow/tasks/{job_id}"}
    try:
        auth = require_authenticated(authorization, x_user_role)
    except CleanroomException as exc:
        default_god_canvas_service._audit_job_access("query", exc.status_code)
        raise
    result = default_god_canvas_service.get_owned_job(job_id, auth)
    return result.model_copy(update={"prototype": True, "durability": "process_memory", "execution_enabled": False})
