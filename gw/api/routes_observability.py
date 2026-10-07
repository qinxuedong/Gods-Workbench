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

"""观测 API 路由实现（Phase 10B；契约版本 p12-a4-1）。

严格对齐 docs/contracts/OBSERVABILITY-INTERFACE-CATALOG.yaml（p12-a4-1），
只实现下列 8 个 **GET** 端点：

- ``GET /api/observability``               资源索引
- ``GET /api/observability/overview``      真实项目/任务计数总览
- ``GET /api/observability/series``        指标采样序列（observability_samples，按真实采样求值）
- ``GET /api/observability/events``        已脱敏认证审计事件
- ``GET /api/observability/tasks``         真实内存任务投影
- ``GET /api/observability/health``        本进程组件可用性（如实，不谎报 ok）
- ``GET /api/observability/sources``       数据源注册表（真实登记本进程来源路径）
- ``GET /api/observability/asset-volumes`` 素材体积索引（真实遍历 GW_ALLOWED_ROOTS）

边界：本模块**不得**实现任何其它 ``/api/*`` 端点（含 ``/api/asset-library/*``
的 items/libraries 明细）；已真实接入的数据源按真实求值返回，
仅当数据源本身不可求值时才返回空 + ``data_status=not_integrated``。

认证：全部端点要求认证，未认证返回 401 且错误码复用 ``core.errors.UnauthorizedException``；
只读角色（readonly/reviewer）可读，不做写权限要求。
"""

from typing import Any, Dict, Optional

from fastapi import APIRouter, Header, Query, Request, status

from gw.core.auth import require_authenticated
from gw.observability.models import (
    AppliedFilters,
    ObservabilityHealth,
    ObservabilityIndex,
    ObservabilityListResponse,
    ObservabilityOverview,
    ObservabilitySeries,
)
from gw.observability.service import default_observability_service, parse_filters

router = APIRouter(prefix="/api/observability", tags=["observability"])

#: 本模块声明的查询参数白名单（用于文档与测试的反向断言）。
OBSERVABILITY_QUERY_PARAMS = (
    "range",
    "limit",
    "status",
    "source",
    "level",
    "project_id",
    "job_id",
    "entity_id",
    "asset_id",
    "canvas_id",
    "stable_id",
    "cursor",
    "start_ms",
    "end_ms",
    "metrics",
    "event_id",
)


def _collect_query(request: Request) -> Dict[str, Any]:
    """把原始查询串收集为映射；未识别参数由服务层按已知键取用，不会导致 500。"""
    return {key: value for key, value in request.query_params.items()}


def _filters(
    request: Request,
    *,
    range: Optional[str] = None,
    limit: Optional[int] = None,
    status_value: Optional[str] = None,
    source: Optional[str] = None,
    level: Optional[str] = None,
    project_id: Optional[str] = None,
    job_id: Optional[str] = None,
    entity_id: Optional[str] = None,
    asset_id: Optional[str] = None,
    canvas_id: Optional[str] = None,
    stable_id: Optional[str] = None,
    cursor: Optional[str] = None,
    start_ms: Optional[str] = None,
    end_ms: Optional[str] = None,
    metrics: Optional[str] = None,
    event_id: Optional[str] = None,
) -> AppliedFilters:
    """构造过滤条件：显式声明的参数覆盖原始查询串中的同名项。"""
    params = _collect_query(request)
    overrides = {
        "range": range,
        "limit": limit,
        "status": status_value,
        "source": source,
        "level": level,
        "project_id": project_id,
        "job_id": job_id,
        "entity_id": entity_id,
        "asset_id": asset_id,
        "canvas_id": canvas_id,
        "stable_id": stable_id,
        "cursor": cursor,
        "start_ms": start_ms,
        "end_ms": end_ms,
        "metrics": metrics,
        "event_id": event_id,
    }
    for key, value in overrides.items():
        if value is not None:
            params[key] = value
    return parse_filters(params)


@router.get(
    "",
    response_model=ObservabilityIndex,
    summary="观测资源索引",
    status_code=status.HTTP_200_OK,
)
def get_observability_index(
    request: Request,
    range: Optional[str] = Query(None, description="时间范围：15m/1h/24h/7d/all"),
    authorization: Optional[str] = Header(None),
    x_user_role: str = Header("editor", alias="X-User-Role", description="用户角色权限"),
):
    """返回本阶段已实现的观测资源索引；仅读能力，只读角色可用。"""
    require_authenticated(authorization, x_user_role)
    return default_observability_service.build_index(_filters(request, range=range))


@router.get(
    "/overview",
    response_model=ObservabilityOverview,
    summary="运行总览",
    status_code=status.HTTP_200_OK,
)
def get_observability_overview(
    request: Request,
    range: Optional[str] = Query(None, description="时间范围：15m/1h/24h/7d/all"),
    limit: Optional[int] = Query(None, ge=1, description="分页条数（本端点仅回显）"),
    authorization: Optional[str] = Header(None),
    x_user_role: str = Header("editor", alias="X-User-Role", description="用户角色权限"),
):
    """项目/任务计数来自真实进程内服务；未接入指标（p95 延迟）为 null 并登记在 data_gaps。"""
    require_authenticated(authorization, x_user_role)
    return default_observability_service.overview(
        _filters(request, range=range, limit=limit)
    )


@router.get(
    "/series",
    response_model=ObservabilitySeries,
    summary="指标序列",
    status_code=status.HTTP_200_OK,
)
def get_observability_series(
    request: Request,
    range: Optional[str] = Query(None, description="时间范围：15m/1h/24h/7d/all"),
    metrics: Optional[str] = Query(None, description="逗号分隔的指标名，如 asset_response_bytes"),
    start_ms: Optional[str] = Query(None, description="窗口起始时间戳（毫秒）"),
    end_ms: Optional[str] = Query(None, description="窗口结束时间戳（毫秒）"),
    authorization: Optional[str] = Header(None),
    x_user_role: str = Header("editor", alias="X-User-Role", description="用户角色权限"),
):
    """读取真实采样存储；样本不足 2 点返回 degraded，绝不插值伪造波形。"""
    require_authenticated(authorization, x_user_role)
    return default_observability_service.series(
        _filters(request, range=range, metrics=metrics, start_ms=start_ms, end_ms=end_ms)
    )


@router.get(
    "/events",
    response_model=ObservabilityListResponse,
    summary="审计事件流",
    status_code=status.HTTP_200_OK,
)
def get_observability_events(
    request: Request,
    range: Optional[str] = Query(None, description="时间范围：15m/1h/24h/7d/all"),
    limit: Optional[int] = Query(None, ge=1, description="分页条数（1..200）"),
    status_value: Optional[str] = Query(None, alias="status", description="事件状态过滤"),
    source: Optional[str] = Query(None, description="来源过滤（当前仅 core.audit）"),
    level: Optional[str] = Query(None, description="级别过滤：info/warn"),
    project_id: Optional[str] = Query(None, description="项目过滤（审计事件无该关联）"),
    job_id: Optional[str] = Query(None, description="任务过滤（审计事件无该关联）"),
    entity_id: Optional[str] = Query(None, description="实体过滤（审计事件无该关联）"),
    asset_id: Optional[str] = Query(None, description="素材过滤（审计事件无该关联）"),
    canvas_id: Optional[str] = Query(None, description="画布过滤（审计事件无该关联）"),
    stable_id: Optional[str] = Query(None, description="稳定 ID 过滤（审计事件无该关联）"),
    event_id: Optional[str] = Query(None, description="指定事件标识"),
    cursor: Optional[str] = Query(None, description="分页游标"),
    start_ms: Optional[str] = Query(None, description="窗口起始时间戳（毫秒）"),
    end_ms: Optional[str] = Query(None, description="窗口结束时间戳（毫秒）"),
    authorization: Optional[str] = Header(None),
    x_user_role: str = Header("editor", alias="X-User-Role", description="用户角色权限"),
):
    """只返回真实脱敏审计事件；无事件返回空数组，无法求值的过滤条件返回空 + degraded。"""
    require_authenticated(authorization, x_user_role)
    return default_observability_service.events(
        _filters(
            request,
            range=range,
            limit=limit,
            status_value=status_value,
            source=source,
            level=level,
            project_id=project_id,
            job_id=job_id,
            entity_id=entity_id,
            asset_id=asset_id,
            canvas_id=canvas_id,
            stable_id=stable_id,
            event_id=event_id,
            cursor=cursor,
            start_ms=start_ms,
            end_ms=end_ms,
        )
    )


@router.get(
    "/tasks",
    response_model=ObservabilityListResponse,
    summary="任务清单",
    status_code=status.HTTP_200_OK,
)
def get_observability_tasks(
    request: Request,
    range: Optional[str] = Query(None, description="时间范围（任务无时间戳，故不支持）"),
    limit: Optional[int] = Query(None, ge=1, description="分页条数（1..200）"),
    status_value: Optional[str] = Query(None, alias="status", description="任务状态过滤"),
    job_id: Optional[str] = Query(None, description="任务标识过滤"),
    project_id: Optional[str] = Query(None, description="项目过滤（任务无该关联）"),
    entity_id: Optional[str] = Query(None, description="实体过滤（任务无该关联）"),
    asset_id: Optional[str] = Query(None, description="素材过滤（任务无该关联）"),
    canvas_id: Optional[str] = Query(None, description="画布过滤（任务无该关联）"),
    stable_id: Optional[str] = Query(None, description="稳定 ID 过滤（任务无该关联）"),
    cursor: Optional[str] = Query(None, description="分页游标"),
    start_ms: Optional[str] = Query(None, description="窗口起始时间戳（毫秒）"),
    end_ms: Optional[str] = Query(None, description="窗口结束时间戳（毫秒）"),
    authorization: Optional[str] = Header(None),
    x_user_role: str = Header("editor", alias="X-User-Role", description="用户角色权限"),
):
    """返回真实内存任务投影；无法求值的过滤条件返回空 + degraded，不静默忽略。"""
    require_authenticated(authorization, x_user_role)
    return default_observability_service.tasks(
        _filters(
            request,
            range=range,
            limit=limit,
            status_value=status_value,
            job_id=job_id,
            project_id=project_id,
            entity_id=entity_id,
            asset_id=asset_id,
            canvas_id=canvas_id,
            stable_id=stable_id,
            cursor=cursor,
            start_ms=start_ms,
            end_ms=end_ms,
        )
    )


@router.get(
    "/health",
    response_model=ObservabilityHealth,
    summary="组件健康",
    status_code=status.HTTP_200_OK,
)
def get_observability_health(
    request: Request,
    authorization: Optional[str] = Header(None),
    x_user_role: str = Header("editor", alias="X-User-Role", description="用户角色权限"),
):
    """如实反映本进程组件可用性；不可求值组件标 not_integrated，绝不无条件返回 ok。"""
    require_authenticated(authorization, x_user_role)
    return default_observability_service.health(_filters(request))


@router.get(
    "/sources",
    response_model=ObservabilityListResponse,
    summary="数据源清单",
    status_code=status.HTTP_200_OK,
)
def get_observability_sources(
    request: Request,
    range: Optional[str] = Query(None, description="时间范围：15m/1h/24h/7d/all"),
    limit: Optional[int] = Query(None, ge=1, description="分页条数（1..200）"),
    cursor: Optional[str] = Query(None, description="分页游标"),
    start_ms: Optional[str] = Query(None, description="窗口起始时间戳（毫秒）"),
    end_ms: Optional[str] = Query(None, description="窗口结束时间戳（毫秒）"),
    authorization: Optional[str] = Header(None),
    x_user_role: str = Header("editor", alias="X-User-Role", description="用户角色权限"),
):
    """返回真实登记的数据源注册表；登记为空才降级说明。"""
    require_authenticated(authorization, x_user_role)
    return default_observability_service.sources(
        _filters(request, range=range, limit=limit, cursor=cursor, start_ms=start_ms, end_ms=end_ms)
    )


@router.get(
    "/asset-volumes",
    response_model=ObservabilityListResponse,
    summary="素材体积",
    status_code=status.HTTP_200_OK,
)
def get_observability_asset_volumes(
    request: Request,
    range: Optional[str] = Query(None, description="时间范围：15m/1h/24h/7d/all"),
    limit: Optional[int] = Query(None, ge=1, description="分页条数（1..200）"),
    cursor: Optional[str] = Query(None, description="分页游标"),
    start_ms: Optional[str] = Query(None, description="窗口起始时间戳（毫秒）"),
    end_ms: Optional[str] = Query(None, description="窗口结束时间戳（毫秒）"),
    authorization: Optional[str] = Header(None),
    x_user_role: str = Header("editor", alias="X-User-Role", description="用户角色权限"),
):
    """真实遍历 GW_ALLOWED_ROOTS 统计素材体积；未配置根目录才返回空 + not_integrated。"""
    require_authenticated(authorization, x_user_role)
    return default_observability_service.asset_volumes(
        _filters(request, range=range, limit=limit, cursor=cursor, start_ms=start_ms, end_ms=end_ms)
    )
