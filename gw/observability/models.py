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

"""观测契约模型（Phase 10B，Pydantic v2）。

严格对齐 docs/contracts/OBSERVABILITY-INTERFACE-CATALOG.yaml（version: p12-a4-1）。

字段口径：
- 稳定 ID 一律使用 job_id / project_id / entity_id / asset_id / canvas_id；
  事件使用 event_id，禁止 id/pid/cid 别名；
- 未接入数据源的响应必须携带 ``data_status="not_integrated"`` 与空集合；
- 查询参数一律回显在 ``applied_filters``，未生效的条件必须在 ``data_gaps`` 中说明。
"""

from __future__ import annotations

import re
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

from pydantic import BaseModel, ConfigDict, Field

from gw.core.errors import CleanroomException

# 分页默认与上限（与契约 catalog 一致）。
DEFAULT_LIMIT = 50
MAX_LIMIT = 200

_RANGE_PATTERN = re.compile(r"^(\d+)([mhdw])$")
_RANGE_UNIT_MS = {
    "m": 60_000,
    "h": 3_600_000,
    "d": 86_400_000,
    "w": 604_800_000,
}


class DataStatus(str, Enum):
    """数据可用性口径；未接入数据源必须是 NOT_INTEGRATED。"""

    OK = "ok"
    DEGRADED = "degraded"
    NOT_INTEGRATED = "not_integrated"


class AppliedFilters(BaseModel):
    """观测查询条件回显；服务层据此做真实过滤与时间窗解析。"""

    model_config = ConfigDict(extra="ignore")

    range: Optional[str] = Field(None, description="时间范围，如 15m/1h/24h/7d/all")
    limit: int = Field(DEFAULT_LIMIT, description="分页条数上限")
    status: Optional[str] = None
    source: Optional[str] = None
    level: Optional[str] = None
    project_id: Optional[str] = None
    job_id: Optional[str] = None
    entity_id: Optional[str] = None
    asset_id: Optional[str] = None
    canvas_id: Optional[str] = None
    stable_id: Optional[str] = None
    cursor: Optional[str] = None
    start_ms: Optional[int] = None
    end_ms: Optional[int] = None
    metrics: List[str] = Field(default_factory=list)
    event_id: Optional[str] = None

    def resolve_window(self, now_ms: int) -> Tuple[Optional[int], Optional[int]]:
        """把 range / start_ms / end_ms 解析为真实毫秒时间窗。

        非法取值失败关闭（400 INVALID_REQUEST），绝不静默忽略用户显式给定的时间条件。
        """
        if self.start_ms is not None or self.end_ms is not None:
            start = self.start_ms
            end = self.end_ms if self.end_ms is not None else now_ms
            if start is not None and end < start:
                raise CleanroomException(
                    status_code=400,
                    code="INVALID_REQUEST",
                    message="end_ms 不能早于 start_ms",
                )
            return start, end

        raw = (self.range or "").strip()
        if not raw or raw == "all":
            return None, None
        match = _RANGE_PATTERN.match(raw)
        if not match:
            raise CleanroomException(
                status_code=400,
                code="INVALID_REQUEST",
                message=f"无法识别的 range 取值：{raw}（支持 Nm/Nh/Nd/Nw 或 all）",
            )
        amount = int(match.group(1))
        if amount <= 0:
            raise CleanroomException(
                status_code=400,
                code="INVALID_REQUEST",
                message="range 的数值必须为正整数",
            )
        return now_ms - amount * _RANGE_UNIT_MS[match.group(2)], now_ms


class ResourceDescriptor(BaseModel):
    """``GET /api/observability`` 返回的资源索引条目。"""

    model_config = ConfigDict(extra="ignore")

    name: str
    method: str = "GET"
    path: str
    data_status: DataStatus
    description: str


class ObservabilityIndex(BaseModel):
    """``GET /api/observability`` 响应：本阶段已实现的观测资源索引。"""

    model_config = ConfigDict(extra="ignore")

    service: str = "observability"
    contract_version: str = "p12-a4-1"
    generated_at: str
    data_status: DataStatus
    data_gaps: List[str] = Field(default_factory=list)
    resources: List[ResourceDescriptor] = Field(default_factory=list)
    applied_filters: AppliedFilters


class ProjectCounts(BaseModel):
    """项目计数（来自 ProjectsService 当前内存状态）。"""

    model_config = ConfigDict(extra="ignore")

    total: int = Field(..., ge=0)
    active: int = Field(..., ge=0)
    archived: int = Field(..., ge=0)
    trashed: int = Field(..., ge=0)


class JobCounts(BaseModel):
    """任务计数（来自 GodCanvasService 当前内存任务表）。"""

    model_config = ConfigDict(extra="ignore")

    total: int = Field(..., ge=0)
    by_status: Dict[str, int] = Field(default_factory=dict)
    queue_depth: int = Field(..., ge=0, description="等待执行（state=accepted）的真实任务数")


class OverviewSummary(BaseModel):
    """总览摘要；未接入的指标必须为 null 且登记在 data_gaps。"""

    model_config = ConfigDict(extra="ignore")

    total: int = Field(..., ge=0)
    failed: int = Field(..., ge=0)
    queue_depth: int = Field(..., ge=0)
    p95_latency_ms: Optional[float] = Field(None, description="未接入延迟直方图数据源时为 null")


class ObservabilityOverview(BaseModel):
    """``GET /api/observability/overview`` 响应。"""

    model_config = ConfigDict(extra="ignore")

    data_status: DataStatus
    generated_at: str
    summary: OverviewSummary
    projects: ProjectCounts
    jobs: JobCounts
    data_gaps: List[str] = Field(default_factory=list)
    applied_filters: AppliedFilters


class SeriesMetric(BaseModel):
    """单个指标序列的可用性声明；本阶段无真实序列。"""

    model_config = ConfigDict(extra="ignore")

    metric: str
    data_status: DataStatus
    points: List[Any] = Field(default_factory=list)


class ObservabilitySeries(BaseModel):
    """``GET /api/observability/series`` 响应：无真实数据源时为空 + not_integrated。"""

    model_config = ConfigDict(extra="ignore")

    data_status: DataStatus
    series: Dict[str, List[Any]] = Field(default_factory=dict)
    metrics: List[SeriesMetric] = Field(default_factory=list)
    requested_metrics: List[str] = Field(default_factory=list)
    data_gap: str
    start_ms: Optional[int] = None
    end_ms: Optional[int] = None
    data_gaps: List[str] = Field(default_factory=list)
    applied_filters: AppliedFilters


class ObservabilityEvent(BaseModel):
    """认证审计事件投影；字段全部来自 core.audit 的白名单脱敏记录。"""

    model_config = ConfigDict(extra="ignore")

    event_id: str = Field(..., description="进程内审计缓冲位置派生的稳定事件标识")
    event_name: str
    level: str
    status: str
    source: str
    message_safe: str
    timestamp: str
    subject: Optional[str] = None
    role: Optional[str] = None
    auth_mode: Optional[str] = None


class ObservabilityTask(BaseModel):
    """真实内存任务投影；未记录字段一律不出现。"""

    model_config = ConfigDict(extra="ignore")

    job_id: str
    status: str
    poll_hint: Optional[str] = None
    has_result: bool = False
    error_safe: Optional[str] = None


class ObservabilityListResponse(BaseModel):
    """事件 / 任务 / 来源 / 素材体积的统一列表包装（分页字段同口径）。"""

    model_config = ConfigDict(extra="ignore")

    data_status: DataStatus
    items: List[Any] = Field(default_factory=list)
    total: int = Field(0, ge=0)
    limit: int = Field(DEFAULT_LIMIT, ge=1)
    has_more: bool = False
    next_cursor: Optional[str] = None
    start_ms: Optional[int] = None
    end_ms: Optional[int] = None
    data_gaps: List[str] = Field(default_factory=list)
    applied_filters: AppliedFilters


class HealthCheck(BaseModel):
    """单组件健康条目；未接入组件必须是 not_integrated，不得谎报 ok。

    ``metrics`` 只在有真实读数时出现（例如 hardware_telemetry 的 psutil 数值）；
    无真实来源的组件不得填充该字段。
    """

    model_config = ConfigDict(extra="ignore")

    name: str
    status: str
    message_safe: str
    metrics: Optional[Dict[str, Any]] = None


class ObservabilityHealth(BaseModel):
    """``GET /api/observability/health`` 响应：如实反映本进程组件可用性。"""

    model_config = ConfigDict(extra="ignore")

    status: str
    data_status: DataStatus
    checks: List[HealthCheck] = Field(default_factory=list)
    buffered_audit_events: int = Field(0, ge=0)
    checked_at: str
    data_gaps: List[str] = Field(default_factory=list)
    applied_filters: AppliedFilters
