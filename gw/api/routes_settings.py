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

"""设置页 API 路由实现（Phase 10D）。

严格对齐 ``docs/contracts/SETTINGS-INTERFACE-CATALOG.yaml``（version: p10d-frozen-1）的
8 条归一化路径 / 13 个方法：

- ``GET/PATCH  /api/storage-settings``              存储设置读取 / 保存（CAS revision）
- ``GET/PUT    /api/providers``                     平台集合读取 / 整体替换（CAS revision）
- ``POST       /api/providers/fetch-models``        拉取模型（本阶段 fail-closed）
- ``POST       /api/providers/probe-async``         协议探测（本阶段 fail-closed）
- ``POST       /api/providers/test-connection``     连通性验证（本阶段 fail-closed）
- ``GET/POST   /api/asset-registry/asset-structures``        结构集合读取 / 创建
- ``GET/PATCH/DELETE /api/asset-registry/asset-structures/{structure_id}``  单结构读写删
- ``PATCH      /api/asset-registry/asset-structures/{structure_id}/current`` 切换当前

边界：其余相邻端点（``/api/asset-registry/assets*``、``/api/local-assets*``、
``/api/storage-files*``、``/api/asset-registry/reindex``、``/api/asset-registry/project-directory*``
等）仍属 KNOWN_UNIMPLEMENTED，未获契约授权，本模块**不得**顺带实现。
"""

from typing import Any, Dict, List, Optional, Union

from fastapi import APIRouter, Body, Header, Query, status

from gw.core.auth import require_authenticated, require_edit_access
from gw.settings.models import (
    AssetStructureCreateRequest,
    AssetStructureCurrentRequest,
    AssetStructureDeleteResponse,
    AssetStructureListResponse,
    AssetStructureResponse,
    AssetStructureUpdateRequest,
    ProviderSnapshot,
    StorageSettingsPatchRequest,
    StorageSettingsSnapshot,
)
from gw.settings import probes as provider_probes
from gw.core.errors import CleanroomException
from gw.settings.service import (
    default_asset_structure_service,
    default_provider_service,
    default_storage_settings_service,
    raise_probe_not_integrated,
)

router = APIRouter(tags=["settings"])


# ---------------------------------------------------------------------------
# 存储设置
# ---------------------------------------------------------------------------


@router.get(
    "/api/storage-settings",
    response_model=StorageSettingsSnapshot,
    summary="读取存储设置",
    status_code=status.HTTP_200_OK,
)
def get_storage_settings(
    authorization: Optional[str] = Header(None),
    x_user_role: str = Header("editor", alias="X-User-Role", description="用户角色权限"),
):
    """返回存储设置真值；无真实来源时如实标记未配置，不编造根目录/容量/路径。"""
    require_authenticated(authorization, x_user_role)
    return default_storage_settings_service.get_snapshot()


@router.patch(
    "/api/storage-settings",
    response_model=StorageSettingsSnapshot,
    summary="保存存储设置",
    status_code=status.HTTP_200_OK,
)
def patch_storage_settings(
    payload: StorageSettingsPatchRequest,
    authorization: Optional[str] = Header(None),
    x_user_role: str = Header("editor", alias="X-User-Role", description="用户角色权限"),
):
    """保存存储设置；要求写权限，revision 不一致返回 409 VERSION_CONFLICT。"""
    require_edit_access(authorization, x_user_role)
    return default_storage_settings_service.patch(payload)


# ---------------------------------------------------------------------------
# 模型平台（provider）
# ---------------------------------------------------------------------------


@router.get(
    "/api/providers",
    response_model=ProviderSnapshot,
    summary="读取模型平台列表",
    status_code=status.HTTP_200_OK,
)
def list_providers(
    authorization: Optional[str] = Header(None),
    x_user_role: str = Header("editor", alias="X-User-Role", description="用户角色权限"),
):
    """返回平台集合真值；默认必须为空数组，绝不预置任何厂商条目。"""
    require_authenticated(authorization, x_user_role)
    return default_provider_service.get_snapshot()


@router.put(
    "/api/providers",
    response_model=ProviderSnapshot,
    summary="整体保存模型平台列表",
    status_code=status.HTTP_200_OK,
)
def replace_providers(
    payload: Union[List[Any], Dict[str, Any], None] = Body(
        None, description="provider 数组（前端既有调用面）或 {\"providers\": [...]} 包装"
    ),
    expected_version: Optional[int] = Query(None, ge=1, description="provider 集合 CAS 期望版本；保存时必填"),
    authorization: Optional[str] = Header(None),
    x_user_role: str = Header("editor", alias="X-User-Role", description="用户角色权限"),
):
    """严格验证平台集合并原子保存元数据与密文；必填CAS，凭据原文不回显。"""
    require_edit_access(authorization, x_user_role)
    # 兼容 ``{"providers": [...]}`` 包装；裸数组仍是前端既有调用面。
    if isinstance(payload, dict):
        if set(payload) != {"providers"}:
            raise CleanroomException(400, "INVALID_REQUEST", "平台请求包装必须且只能包含providers数组")
        raw: Any = payload["providers"]
    else:
        raw = payload
    return default_provider_service.replace(raw, expected_version)


@router.post(
    "/api/providers/fetch-models",
    summary="真实拉取上游模型列表",
    status_code=status.HTTP_200_OK,
)
def fetch_provider_models(
    payload: Optional[dict] = Body(None),
    authorization: Optional[str] = Header(None),
    x_user_role: str = Header("editor", alias="X-User-Role", description="用户角色权限"),
):
    """按实际目录协议请求；根地址/版本地址均支持，不能据目录宣称生成能力。"""
    require_edit_access(authorization, x_user_role)
    return provider_probes.fetch_models(payload or {})


@router.post(
    "/api/providers/probe-async",
    summary="同步探测模型目录（旧地址兼容）",
    status_code=status.HTTP_200_OK,
)
def probe_provider_async(
    payload: Optional[dict] = Body(None),
    authorization: Optional[str] = Header(None),
    x_user_role: str = Header("editor", alias="X-User-Role", description="用户角色权限"),
):
    """请求完成后保存历史结果；兼容job_id/poll_hint，不存在后台排队。"""
    require_edit_access(authorization, x_user_role)
    return provider_probes.probe_async(payload or {})


@router.get(
    "/api/providers/probe-async/jobs/{job_id}",
    summary="读取已完成的目录探测记录",
    status_code=status.HTTP_200_OK,
)
def get_provider_probe_job(
    job_id: str,
    authorization: Optional[str] = Header(None),
    x_user_role: str = Header("editor", alias="X-User-Role", description="用户角色权限"),
):
    """读取旧地址下的已完成探测记录；不存在404，不承诺后台任务。"""
    require_authenticated(authorization, x_user_role)
    return provider_probes.get_probe_job(job_id)


@router.post(
    "/api/providers/test-connection",
    summary="真实验证上游连通性",
    status_code=status.HTTP_200_OK,
)
def test_provider_connection(
    payload: Optional[dict] = Body(None),
    authorization: Optional[str] = Header(None),
    x_user_role: str = Header("editor", alias="X-User-Role", description="用户角色权限"),
):
    """真实往返并记录实测延迟；失败 503，绝不伪造连通性或延迟数字。"""
    require_edit_access(authorization, x_user_role)
    return provider_probes.test_connection(payload or {})


# ---------------------------------------------------------------------------
# 素材版本/组结构
# ---------------------------------------------------------------------------


@router.get(
    "/api/asset-registry/asset-structures",
    response_model=AssetStructureListResponse,
    summary="读取素材结构集合",
    status_code=status.HTTP_200_OK,
)
def list_asset_structures(
    asset_ids: Optional[List[str]] = Query(None, description="按成员过滤（可重复）"),
    authorization: Optional[str] = Header(None),
    x_user_role: str = Header("editor", alias="X-User-Role", description="用户角色权限"),
):
    """返回结构集合；无结构时 items: []，不编造成员素材元数据。"""
    require_authenticated(authorization, x_user_role)
    return default_asset_structure_service.list_structures(asset_ids)


@router.post(
    "/api/asset-registry/asset-structures",
    response_model=AssetStructureResponse,
    summary="创建素材结构",
    status_code=status.HTTP_201_CREATED,
)
def create_asset_structure(
    payload: AssetStructureCreateRequest,
    authorization: Optional[str] = Header(None),
    x_user_role: str = Header("editor", alias="X-User-Role", description="用户角色权限"),
):
    """创建版本/组结构；要求写权限，集合 CAS 冲突返回 409 STRUCTURE_VERSION_CONFLICT。"""
    require_edit_access(authorization, x_user_role)
    structure = default_asset_structure_service.create_structure(payload)
    return AssetStructureResponse(structure=structure)


@router.get(
    "/api/asset-registry/asset-structures/{structure_id}",
    response_model=AssetStructureResponse,
    summary="读取单个素材结构",
    status_code=status.HTTP_200_OK,
)
def get_asset_structure(
    structure_id: str,
    authorization: Optional[str] = Header(None),
    x_user_role: str = Header("editor", alias="X-User-Role", description="用户角色权限"),
):
    """返回目标结构真值；不存在必须 404 STRUCTURE_NOT_FOUND。"""
    require_authenticated(authorization, x_user_role)
    return AssetStructureResponse(structure=default_asset_structure_service.get_structure(structure_id))


@router.patch(
    "/api/asset-registry/asset-structures/{structure_id}",
    response_model=AssetStructureResponse,
    summary="更新素材结构",
    status_code=status.HTTP_200_OK,
)
def update_asset_structure(
    structure_id: str,
    payload: AssetStructureUpdateRequest,
    authorization: Optional[str] = Header(None),
    x_user_role: str = Header("editor", alias="X-User-Role", description="用户角色权限"),
):
    """更新结构成员或当前素材；要求写权限，结构 CAS 冲突返回 409 STRUCTURE_VERSION_CONFLICT。"""
    require_edit_access(authorization, x_user_role)
    structure = default_asset_structure_service.update_structure(structure_id, payload)
    return AssetStructureResponse(structure=structure)


@router.patch(
    "/api/asset-registry/asset-structures/{structure_id}/current",
    response_model=AssetStructureResponse,
    summary="切换结构当前素材",
    status_code=status.HTTP_200_OK,
)
def set_asset_structure_current(
    structure_id: str,
    payload: AssetStructureCurrentRequest,
    authorization: Optional[str] = Header(None),
    x_user_role: str = Header("editor", alias="X-User-Role", description="用户角色权限"),
):
    """切换当前代表素材；要求写权限，结构 CAS 冲突返回 409 STRUCTURE_VERSION_CONFLICT。"""
    require_edit_access(authorization, x_user_role)
    structure = default_asset_structure_service.set_current(structure_id, payload)
    return AssetStructureResponse(structure=structure)


@router.delete(
    "/api/asset-registry/asset-structures/{structure_id}",
    response_model=AssetStructureDeleteResponse,
    summary="删除素材结构",
    status_code=status.HTTP_200_OK,
)
def delete_asset_structure(
    structure_id: str,
    expected_version: Optional[int] = Query(None, ge=1, description="目标结构 CAS 期望版本；缺省表示不校验"),
    authorization: Optional[str] = Header(None),
    x_user_role: str = Header("editor", alias="X-User-Role", description="用户角色权限"),
):
    """删除结构；要求写权限，结构 CAS 冲突返回 409 STRUCTURE_VERSION_CONFLICT。"""
    require_edit_access(authorization, x_user_role)
    deleted_id, revision = default_asset_structure_service.delete_structure(structure_id, expected_version)
    return AssetStructureDeleteResponse(deleted_structure_id=deleted_id, revision=revision)
