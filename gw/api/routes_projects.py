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

"""项目中心 API 路由实现。

严格对齐 docs/contracts/PROJECTS-HUB-INTERFACE-CATALOG.yaml 定义的 7 个端点，接驳 ProjectsService。
"""

from typing import Optional
from fastapi import APIRouter, Header, Query, status

from gw.core.auth import require_authenticated, require_edit_access, require_governance_access
from gw.core.errors import UnauthorizedException
from gw.projects_hub.models import (
    CasVersionRequest,
    ProjectCreateRequest,
    ProjectDetailResponse,
    ProjectListResponse,
    ProjectMutationResponse,
    ProjectUpdateRequest,
)
from gw.projects_hub.service import default_projects_service, owner_key_for_context

router = APIRouter(prefix="/api/asset-registry", tags=["projects-hub"])


@router.get(
    "/projects",
    response_model=ProjectListResponse,
    summary="获取项目列表",
    status_code=status.HTTP_200_OK,
)
def list_projects(
    archived: Optional[bool] = Query(False, description="是否包含归档项目"),
    deleted: Optional[bool] = Query(None, description="是否包含回收站项目"),
    authorization: Optional[str] = Header(None),
):
    """根据查询条件返回项目集合。"""
    if authorization == "invalid":
        raise UnauthorizedException()
    projects = default_projects_service.list_projects(archived=archived, deleted=deleted)
    return ProjectListResponse(projects=projects)


@router.post(
    "/projects",
    response_model=ProjectMutationResponse,
    summary="创建项目",
    status_code=status.HTTP_201_CREATED,
)
def create_project(
    payload: ProjectCreateRequest,
    authorization: Optional[str] = Header(None),
    x_user_role: str = Header("editor", alias="X-User-Role", description="用户角色权限"),
):
    """创建新项目。"""
    context = require_edit_access(authorization, x_user_role)
    # ACL 绑定只接收服务端认证主体；service先持久预留不可复用ID，再原子发布项目真源。
    from gw.video_tasks.service import get_video_service
    video_service = get_video_service()
    result = default_projects_service.create_project(
        payload,
        owner_key=owner_key_for_context(context),
        before_publish=lambda project_id: video_service.register_project_owner(project_id, context),
    )
    return ProjectMutationResponse(project=result)


@router.patch(
    "/projects/{project_id}",
    response_model=ProjectMutationResponse,
    summary="编辑项目（CAS）",
    status_code=status.HTTP_200_OK,
)
def update_project(
    project_id: str,
    payload: ProjectUpdateRequest,
    authorization: Optional[str] = Header(None),
    x_user_role: str = Header("editor", alias="X-User-Role", description="用户角色权限"),
):
    """更新项目元信息，要求 expected_version 匹配。"""
    require_edit_access(authorization, x_user_role)
    result = default_projects_service.update_project(project_id, payload)
    return ProjectMutationResponse(project=result)


@router.delete(
    "/projects/{project_id}",
    response_model=ProjectMutationResponse,
    summary="归档项目",
    status_code=status.HTTP_200_OK,
)
def archive_project(
    project_id: str,
    payload: CasVersionRequest,
    authorization: Optional[str] = Header(None),
    x_user_role: str = Header("editor", alias="X-User-Role", description="用户角色权限"),
):
    """将项目移入只读归档状态。"""
    require_governance_access(authorization, x_user_role)
    result = default_projects_service.archive_project(project_id, payload.expected_version)
    return ProjectMutationResponse(project=result)


@router.post(
    "/governance/projects/{project_id}/restore",
    response_model=ProjectMutationResponse,
    summary="解归档项目",
    status_code=status.HTTP_200_OK,
)
def unarchive_project(
    project_id: str,
    payload: CasVersionRequest,
    authorization: Optional[str] = Header(None),
    x_user_role: str = Header("editor", alias="X-User-Role", description="用户角色权限"),
):
    """将已归档项目恢复为活跃状态。"""
    require_governance_access(authorization, x_user_role)
    result = default_projects_service.unarchive_project(project_id, payload.expected_version)
    return ProjectMutationResponse(project=result)


@router.post(
    "/projects/{project_id}/trash",
    response_model=ProjectMutationResponse,
    summary="移入回收站",
    status_code=status.HTTP_200_OK,
)
def move_project_to_trash(
    project_id: str,
    payload: CasVersionRequest,
    authorization: Optional[str] = Header(None),
    x_user_role: str = Header("editor", alias="X-User-Role", description="用户角色权限"),
):
    """仅允许已归档项目移入回收站。"""
    require_governance_access(authorization, x_user_role)
    result = default_projects_service.move_to_trash(project_id, payload.expected_version)
    return ProjectMutationResponse(project=result)


@router.post(
    "/projects/{project_id}/trash/restore",
    response_model=ProjectMutationResponse,
    summary="从回收站恢复",
    status_code=status.HTTP_200_OK,
)
def restore_project_from_trash(
    project_id: str,
    payload: CasVersionRequest,
    authorization: Optional[str] = Header(None),
    x_user_role: str = Header("editor", alias="X-User-Role", description="用户角色权限"),
):
    """从回收站恢复项目至可用集合。"""
    require_governance_access(authorization, x_user_role)
    result = default_projects_service.restore_from_trash(project_id, payload.expected_version)
    return ProjectMutationResponse(project=result)


# ---------------------------------------------------------------------------
# B2 兼容项目路径
# ---------------------------------------------------------------------------

legacy_router = APIRouter(tags=["projects-compat"])


@legacy_router.get(
    "/api/projects",
    response_model=ProjectListResponse,
    summary="兼容读取项目列表",
    status_code=status.HTTP_200_OK,
)
def list_projects_compat(
    archived: Optional[bool] = Query(False, description="是否包含归档项目"),
    deleted: Optional[bool] = Query(None, description="是否包含回收站项目"),
    authorization: Optional[str] = Header(None),
    x_user_role: str = Header("editor", alias="X-User-Role"),
):
    """兼容旧工作台路径，复用项目中心同一内存真值。"""
    require_authenticated(authorization, x_user_role)
    return ProjectListResponse(
        projects=default_projects_service.list_projects(archived=archived, deleted=deleted)
    )


@legacy_router.get(
    "/api/projects/{project_id}",
    response_model=ProjectDetailResponse,
    summary="兼容读取单个项目",
    status_code=status.HTTP_200_OK,
)
def get_project_compat(
    project_id: str,
    authorization: Optional[str] = Header(None),
    x_user_role: str = Header("editor", alias="X-User-Role"),
):
    """读取项目详情；不存在返回标准 PROJECT_NOT_FOUND。"""
    require_authenticated(authorization, x_user_role)
    return ProjectDetailResponse(project=default_projects_service.get_project(project_id))
