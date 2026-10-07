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
from fastapi import APIRouter, Header, Query, Request, Response, status

from gw.core.auth import require_edit_access
from gw.core.errors import UnauthorizedException
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
from gw.god_canvas.tasks import SmartCanvasTaskRequest, SmartCanvasTaskResponse
from gw.projects_hub.models import CasVersionRequest

router = APIRouter(prefix="/api/canvases", tags=["god-canvas"])
jobs_router = APIRouter(prefix="/api/jobs", tags=["god-canvas-jobs"])


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
def get_smart_job_status(job_id: str):
    """根据 job_id 查询智能画布任务状态。"""
    return default_god_canvas_service.get_job(job_id)
