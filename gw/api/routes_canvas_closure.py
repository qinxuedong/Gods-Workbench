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

"""画布闭环 API 路由实现（Phase 10E）。

对齐 ``docs/contracts/CANVAS-CLOSURE-INTERFACE-CATALOG.yaml``（version: p10e-frozen-1）。

覆盖能力域：

- 画布素材索引与打包下载（``/api/canvas-assets*``）
- 参考画布（``/api/reference-canvases``）
- 共享文件夹登记与浏览（``/api/shared-folders*``）
- 视频任务改由 ``video_tasks`` 契约路由提供（不属于本模块）
- 画布元信息 / 归档 / 回收站 / 素材挂接（``/api/canvases*``）

硬口径：

- 画布素材打包下载、素材挂接、共享文件夹目录扫描与导入**均无真实后端**，
  一律 503 + 固定 ``NOT_INTEGRATED`` 错误码，绝不返回假成功 / 假进度；
- 无来源的集合一律空数组 + ``data_status: not_integrated`` + ``data_gaps``；
- 写操作校验 ``expected_version``，冲突 409；读需认证，写需写权限；
- 本模块只注册本阶段契约授权的端点，相邻未授权端点保持 404/405。

注意：``/api/canvases/trash`` 必须在 ``routes_god_canvas`` 的 ``/{canvas_id}`` 之前匹配，
因此 ``api/app.py`` 中本 router 必须**先于** ``god_canvas_router`` 注册。
"""

from typing import Optional

from fastapi import APIRouter, Body, Header, Query, status

from gw.canvas_closure.models import (
    CanvasAssetAttachRequest,
    CanvasAssetDownloadRequest,
    CanvasAssetIndexResponse,
    CanvasMetaRequest,
    CanvasMetaResponse,
    CanvasPurgeResponse,
    CanvasTouchRequest,
    CanvasTouchResponse,
    CanvasTrashResponse,
    ReferenceCanvasCreateRequest,
    ReferenceCanvasListResponse,
    ReferenceCanvasResponse,
    SharedFolderCreateRequest,
    SharedFolderDeleteResponse,
    SharedFolderListResponse,
    SharedFolderResponse,
    VideoTaskListResponse,
)
from gw.canvas_closure.service import (
    default_canvas_closure_service as closure,
    default_shared_folder_service as shared_folders,
)
from gw.core.auth import require_authenticated, require_edit_access

router = APIRouter(tags=["canvas-closure"])


# ---------------------------------------------------------------------------
# 画布素材索引
# ---------------------------------------------------------------------------


@router.get(
    "/api/canvas-assets",
    response_model=CanvasAssetIndexResponse,
    summary="读取画布素材索引",
    status_code=status.HTTP_200_OK,
)
def get_canvas_assets(
    authorization: Optional[str] = Header(None),
    x_user_role: str = Header("editor", alias="X-User-Role", description="用户角色权限"),
):
    """返回画布索引；素材条目需素材注册表，本阶段为空并如实标记缺口。"""
    require_authenticated(authorization, x_user_role)
    return closure.get_canvas_assets()


@router.post(
    "/api/canvas-assets/download",
    summary="打包下载画布素材",
    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
)
def download_canvas_assets(
    payload: CanvasAssetDownloadRequest = Body(default=CanvasAssetDownloadRequest()),
    authorization: Optional[str] = Header(None),
    x_user_role: str = Header("editor", alias="X-User-Role", description="用户角色权限"),
):
    """无真实文件后端：如实 503 fail-closed，绝不返回伪造压缩包。"""
    require_edit_access(authorization, x_user_role)
    closure.raise_canvas_asset_download_not_integrated()


# ---------------------------------------------------------------------------
# 参考画布
# ---------------------------------------------------------------------------


@router.get(
    "/api/reference-canvases",
    response_model=ReferenceCanvasListResponse,
    summary="读取参考画布列表",
    status_code=status.HTTP_200_OK,
)
def list_reference_canvases(
    authorization: Optional[str] = Header(None),
    x_user_role: str = Header("editor", alias="X-User-Role", description="用户角色权限"),
):
    """返回参考画布集合；无内容时必须为空数组，不预置演示画布。"""
    require_authenticated(authorization, x_user_role)
    return closure.list_reference_canvases()


@router.post(
    "/api/reference-canvases",
    response_model=ReferenceCanvasResponse,
    summary="保存参考画布",
    status_code=status.HTTP_201_CREATED,
)
def save_reference_canvas(
    payload: ReferenceCanvasCreateRequest,
    authorization: Optional[str] = Header(None),
    x_user_role: str = Header("editor", alias="X-User-Role", description="用户角色权限"),
):
    """保存参考画布；只登记调用方提交的内容，不编造素材元数据。"""
    require_edit_access(authorization, x_user_role)
    return closure.save_reference_canvas(
        title=payload.title,
        items=payload.items,
        annotations=payload.annotations,
        drawings=payload.drawings,
        groups=payload.groups,
        viewport=payload.viewport,
        canvas_id=payload.canvas_id,
        expected_version=payload.expected_version,
    )


# ---------------------------------------------------------------------------
# 共享文件夹
# ---------------------------------------------------------------------------


@router.get(
    "/api/shared-folders",
    response_model=SharedFolderListResponse,
    summary="读取共享文件夹登记",
    status_code=status.HTTP_200_OK,
)
def list_shared_folders(
    authorization: Optional[str] = Header(None),
    x_user_role: str = Header("editor", alias="X-User-Role", description="用户角色权限"),
):
    """返回已登记共享文件夹；无内容时必须为空数组。"""
    require_authenticated(authorization, x_user_role)
    return shared_folders.list_folders()


@router.post(
    "/api/shared-folders",
    response_model=SharedFolderResponse,
    summary="登记共享文件夹",
    status_code=status.HTTP_201_CREATED,
)
def create_shared_folder(
    payload: SharedFolderCreateRequest,
    authorization: Optional[str] = Header(None),
    x_user_role: str = Header("editor", alias="X-User-Role", description="用户角色权限"),
):
    """登记项目目录内的相对路径；只登记不扫描、不复制文件。"""
    require_edit_access(authorization, x_user_role)
    return shared_folders.create_folder(path=payload.path, name=payload.name)


@router.post(
    "/api/shared-folders/import",
    summary="导入共享文件夹素材",
    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
)
def import_shared_folder_items(
    payload: dict = Body(default_factory=dict),
    authorization: Optional[str] = Header(None),
    x_user_role: str = Header("editor", alias="X-User-Role", description="用户角色权限"),
):
    """无真实文件复制与素材库写入能力：如实 503 fail-closed。"""
    require_edit_access(authorization, x_user_role)
    closure.raise_shared_folder_import_not_integrated()


@router.get(
    "/api/shared-folders/{folder_id}/tree",
    summary="读取共享文件夹目录树",
    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
)
def get_shared_folder_tree(
    folder_id: str,
    authorization: Optional[str] = Header(None),
    x_user_role: str = Header("editor", alias="X-User-Role", description="用户角色权限"),
):
    """真实目录扫描未接入：目标不存在 404，存在则如实 503，绝不返回假目录树。"""
    require_authenticated(authorization, x_user_role)
    closure.get_shared_folder_tree(folder_id)


@router.delete(
    "/api/shared-folders/{folder_id}",
    response_model=SharedFolderDeleteResponse,
    summary="移除共享文件夹登记",
    status_code=status.HTTP_200_OK,
)
def delete_shared_folder(
    folder_id: str,
    authorization: Optional[str] = Header(None),
    x_user_role: str = Header("editor", alias="X-User-Role", description="用户角色权限"),
):
    """移除登记；不存在必须 404 SHARED_FOLDER_NOT_FOUND。"""
    require_edit_access(authorization, x_user_role)
    return shared_folders.delete_folder(folder_id)



# ---------------------------------------------------------------------------
# 画布元信息 / 归档 / 回收站 / 素材挂接
# ---------------------------------------------------------------------------


@router.get(
    "/api/canvases/trash",
    response_model=CanvasTrashResponse,
    summary="读取回收站或归档列表",
    status_code=status.HTTP_200_OK,
)
def list_canvas_trash(
    view: str = Query("deleted", description="deleted（回收站）或 archived（归档）"),
    authorization: Optional[str] = Header(None),
    x_user_role: str = Header("editor", alias="X-User-Role", description="用户角色权限"),
):
    """返回回收站 / 归档列表；无内容时必须是空数组，不返回演示条目。"""
    require_authenticated(authorization, x_user_role)
    if view not in ("deleted", "archived"):
        from gw.core.errors import CleanroomException

        raise CleanroomException(
            status_code=400,
            code="INVALID_REQUEST",
            message="view 仅支持 deleted 或 archived",
        )
    return closure.list_trash(view="deleted" if view == "deleted" else "archived")


@router.post(
    "/api/canvases/assets",
    summary="把素材挂接到画布",
    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
)
def attach_registry_assets_to_canvas(
    payload: CanvasAssetAttachRequest,
    authorization: Optional[str] = Header(None),
    x_user_role: str = Header("editor", alias="X-User-Role", description="用户角色权限"),
):
    """素材注册表未接入：如实 503 fail-closed，绝不伪造已挂接素材。"""
    require_edit_access(authorization, x_user_role)
    closure.raise_canvas_asset_attach_not_integrated()


@router.patch(
    "/api/canvases/{canvas_id}/meta",
    response_model=CanvasMetaResponse,
    summary="更新画布元信息（CAS，任务书声明方法别名）",
    status_code=status.HTTP_200_OK,
)
@router.post(
    "/api/canvases/{canvas_id}/meta",
    response_model=CanvasMetaResponse,
    summary="更新画布元信息（CAS，前端实测调用方法）",
    status_code=status.HTTP_200_OK,
)
def update_canvas_meta(
    canvas_id: str,
    payload: CanvasMetaRequest,
    authorization: Optional[str] = Header(None),
    x_user_role: str = Header("editor", alias="X-User-Role", description="用户角色权限"),
):
    """更新标题 / 实体绑定 / 项目归属；CAS 冲突严格 409 CANVAS_VERSION_CONFLICT。"""
    require_edit_access(authorization, x_user_role)
    patch = {
        "title": payload.title,
        "entity_id": payload.entity_id,
        "project_id": payload.project_id or payload.project,
        "icon": payload.icon,
        "board_x": payload.board_x,
        "board_y": payload.board_y,
    }
    return {"canvas": closure.update_canvas_meta(canvas_id, patch, expected_version=payload.resolved_version())}


@router.post(
    "/api/canvases/{canvas_id}/touch",
    response_model=CanvasTouchResponse,
    summary="归档 / 解归档画布（CAS）",
    status_code=status.HTTP_200_OK,
)
def touch_canvas(
    canvas_id: str,
    operation: str = Query(..., description="archive 或 unarchive"),
    payload: Optional[CanvasTouchRequest] = Body(default=None),
    expected_version: Optional[int] = Query(None, ge=1, description="画布 CAS 期望版本"),
    authorization: Optional[str] = Header(None),
    x_user_role: str = Header("editor", alias="X-User-Role", description="用户角色权限"),
):
    """归档 / 解归档；只翻转状态，不借 touch 绕过 CAS。"""
    require_edit_access(authorization, x_user_role)
    resolved = expected_version
    if resolved is None and payload is not None:
        resolved = payload.expected_version
    return {"canvas": closure.touch_canvas(canvas_id, operation, expected_version=resolved)}


@router.post(
    "/api/canvases/{canvas_id}/purge",
    response_model=CanvasPurgeResponse,
    summary="彻底删除回收站中的画布（任务书声明方法别名）",
    status_code=status.HTTP_200_OK,
)
@router.delete(
    "/api/canvases/{canvas_id}/purge",
    response_model=CanvasPurgeResponse,
    summary="彻底删除回收站中的画布（前端实测调用方法）",
    status_code=status.HTTP_200_OK,
)
def purge_canvas(
    canvas_id: str,
    authorization: Optional[str] = Header(None),
    x_user_role: str = Header("editor", alias="X-User-Role", description="用户角色权限"),
):
    """仅允许彻底删除回收站中的画布；未入回收站返回 409，禁止越权直删。"""
    require_edit_access(authorization, x_user_role)
    return closure.purge_canvas(canvas_id)


@router.delete(
    "/api/canvases/{canvas_id}",
    response_model=CanvasMetaResponse,
    summary="把画布移入回收站（软删除）",
    status_code=status.HTTP_200_OK,
)
def trash_canvas(
    canvas_id: str,
    expected_version: Optional[int] = Query(None, ge=1, description="画布 CAS 期望版本"),
    authorization: Optional[str] = Header(None),
    x_user_role: str = Header("editor", alias="X-User-Role", description="用户角色权限"),
):
    """软删除：移入回收站而非物理删除，可经 /restore 恢复。"""
    require_edit_access(authorization, x_user_role)
    return {"canvas": closure.move_to_trash(canvas_id, expected_version=expected_version)}
