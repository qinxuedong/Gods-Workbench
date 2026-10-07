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

"""观测服务层实现（Phase 10B）。

数据来源（全部为**进程内真实状态**，无任何伪造）
------------------------------------------------
- 项目计数：``ProjectsService.list_projects`` 当前内存项目表；
- 任务计数与清单：``GodCanvasService`` 当前内存任务表（仅 ``list_jobs`` 只读投影）；
- 素材库可用性：``AssetLibraryService.get_snapshot`` 快照探测；
- 事件：``core.audit.list_auth_events`` 的**已脱敏**认证审计环形缓冲。

Phase 12 A4 已真实接入的数据源（硬件遥测 / 指标采样 / 数据源注册表 / 素材体积扫描）
------------------------------------------------------------------
按真实求值返回数据；仅当数据源本身不可求值（未配置根目录、psutil 缺失、采样存储不可读）时，
才返回**空集合** + ``data_status: "not_integrated"``，并在 ``data_gaps`` 中说明原因；
绝不使用 ``random``、常量曲线或任何演示数据伪造波形、任务或事件。

过滤语义（关键取舍）
--------------------
无法求值的过滤条件**不得静默忽略**：命中该情形时返回空集合、
``data_status="degraded"`` 并在 ``data_gaps`` 中写明「某某条件无法求值」。
例如审计事件不携带 ``project_id`` 关联，按 ``project_id`` 过滤即返回空而不是返回全部。

证据边界：本服务是**进程内内存**读取层，重启即丢失、多 worker 不共享；
不宣称满足任何合规留存要求，也不等于发布授权。
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from gw.asset_library.service import default_asset_library_service
from gw.core import audit as audit_log
from gw.core.errors import CleanroomException
from gw.god_canvas.service import default_god_canvas_service
from gw.observability import telemetry
from gw.observability.models import (
    DEFAULT_LIMIT,
    MAX_LIMIT,
    AppliedFilters,
    DataStatus,
    HealthCheck,
    JobCounts,
    ObservabilityEvent,
    ObservabilityHealth,
    ObservabilityIndex,
    ObservabilityListResponse,
    ObservabilityOverview,
    ObservabilitySeries,
    ObservabilityTask,
    OverviewSummary,
    ProjectCounts,
    ResourceDescriptor,
    SeriesMetric,
)
from gw.projects_hub.service import default_projects_service

CONTRACT_VERSION = "p12-a4-1"
SERVICE_NAME = "observability"
AUDIT_SOURCE = "core.audit"
CURSOR_PREFIX = "obs1:"

# 事件结果 → 对外状态口径（封闭映射，不引入自由文本）。
_OUTCOME_STATUS = {
    audit_log.OUTCOME_STARTED: "running",
    audit_log.OUTCOME_SUCCEEDED: "succeeded",
    audit_log.OUTCOME_DENIED: "denied",
    audit_log.OUTCOME_REJECTED: "rejected",
}
_WARN_STATUSES = frozenset({"denied", "rejected"})

# Phase 12 A4：hardware_telemetry / metrics_series / source_registry / asset_volume_index
# 均已真实接入。健康检查按下述**真实**口径逐项求值；无法求值时才标 not_integrated。
# 上列 GAP_* 文案仅描述「无法求值」成因，不再声称端点未接入。
_GAP_METRICS_EMPTY = "指标采样存储可读但当前无采样点，序列为空（真实空，非未接入）"
_GAP_METRICS_UNAVAILABLE = "指标采样存储不可读，时间序列能力降级"
_GAP_VOLUMES_UNCONFIGURED = "未配置 GW_ALLOWED_ROOTS，素材体积索引无可扫目录"

GAP_HARDWARE = "psutil 不可用，宿主机硬件遥测（CPU/内存/磁盘）无法求值"
GAP_GPU = "nvidia-smi 不可用或无 NVIDIA 设备，GPU 利用率/显存遥测无法求值"
GAP_SERIES = "指标采样存储不可读，时间序列无法求值"
GAP_SOURCES = "可观测性数据源注册表登记为空，无可列来源"
GAP_ASSET_VOLUMES = "未配置 GW_ALLOWED_ROOTS，素材体积索引无可扫目录"
GAP_AUDIT_UNAVAILABLE = "审计缓冲不可读，认证事件不可观测（如实降级，不伪装为空）"
GAP_LATENCY = "延迟直方图数据源未接入，p95 延迟不可计算，故为 null"

_ID_SCOPE_LABELS = (
    ("stable_id", "stable_id"),
    ("project_id", "project_id"),
    ("entity_id", "entity_id"),
    ("asset_id", "asset_id"),
    ("canvas_id", "canvas_id"),
)


def _now_ms() -> int:
    """当前时间（毫秒，UTC）。"""
    return int(time.time() * 1000)


def _now_iso() -> str:
    """当前时间（ISO 8601 UTC）。"""
    return datetime.now(timezone.utc).isoformat()


def _iso_from_epoch(seconds: float) -> str:
    """把审计记录的 epoch 秒转成 ISO 8601 UTC 字符串。"""
    return datetime.fromtimestamp(float(seconds), tz=timezone.utc).isoformat()


def _coerce_int(value: Optional[Any], field: str) -> Optional[int]:
    """把查询串里的整数值解析为 int；非法取值失败关闭（400），不静默忽略。"""
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        raise CleanroomException(status_code=400, code="INVALID_REQUEST", message=f"{field} 取值非法")
    if isinstance(value, int):
        return value
    text = str(value).strip()
    if not text:
        return None
    try:
        return int(text)
    except ValueError:
        raise CleanroomException(
            status_code=400,
            code="INVALID_REQUEST",
            message=f"{field} 必须是整数，实际收到：{text}",
        )


def _split_csv(value: Optional[str]) -> List[str]:
    """把逗号分隔的指标名解析为去重列表。"""
    if not value:
        return []
    seen: List[str] = []
    for item in str(value).split(","):
        token = item.strip()
        if token and token not in seen:
            seen.append(token)
    return seen


def encode_cursor(offset: int) -> str:
    """把分页偏移编码为不透明游标（不暴露内部结构）。"""
    raw = json.dumps({"offset": int(offset)}, separators=(",", ":")).encode("utf-8")
    return CURSOR_PREFIX + base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def decode_cursor(cursor: Optional[str]) -> int:
    """还原游标偏移；非法游标失败关闭（400），不静默从头分页。"""
    if not cursor:
        return 0
    text = str(cursor).strip()
    if not text.startswith(CURSOR_PREFIX):
        raise CleanroomException(status_code=400, code="INVALID_REQUEST", message="cursor 非法或已失效")
    payload = text[len(CURSOR_PREFIX):]
    padding = "=" * (-len(payload) % 4)
    try:
        decoded = base64.urlsafe_b64decode(payload + padding).decode("utf-8")
        offset = int(json.loads(decoded)["offset"])
    except (binascii.Error, ValueError, KeyError, TypeError, UnicodeDecodeError):
        raise CleanroomException(status_code=400, code="INVALID_REQUEST", message="cursor 非法或已失效")
    if offset < 0:
        raise CleanroomException(status_code=400, code="INVALID_REQUEST", message="cursor 非法或已失效")
    return offset


def _validate_limit(limit: Optional[Any]) -> int:
    """校验分页条数；超出允许区间一律 400。"""
    if limit is None or limit == "":
        return DEFAULT_LIMIT
    value = _coerce_int(limit, "limit")
    assert value is not None
    if value < 1 or value > MAX_LIMIT:
        raise CleanroomException(
            status_code=400,
            code="INVALID_REQUEST",
            message=f"limit 必须在 1..{MAX_LIMIT} 之间，实际收到：{value}",
        )
    return value


def parse_filters(params: Mapping[str, Any]) -> AppliedFilters:
    """把查询映射解析为强类型过滤条件；未识别参数被忽略但仍如实回显已知项。"""
    def pick(*names: str) -> Optional[str]:
        for name in names:
            value = params.get(name)
            if value is None:
                continue
            text = str(value).strip()
            if text:
                return text
        return None

    return AppliedFilters(
        range=pick("range"),
        limit=_validate_limit(params.get("limit")),
        status=pick("status"),
        source=pick("source"),
        level=pick("level"),
        project_id=pick("project_id"),
        job_id=pick("job_id"),
        entity_id=pick("entity_id"),
        asset_id=pick("asset_id"),
        canvas_id=pick("canvas_id"),
        stable_id=pick("stable_id"),
        cursor=pick("cursor"),
        start_ms=_coerce_int(params.get("start_ms"), "start_ms"),
        end_ms=_coerce_int(params.get("end_ms"), "end_ms"),
        metrics=_split_csv(params.get("metrics")),
        event_id=pick("event_id"),
    )


class ObservabilityService:
    """观测服务：只读取真实进程内状态，未接入的如实降级。"""

    def __init__(
        self,
        projects_service: Any = None,
        canvas_service: Any = None,
        asset_library_service: Any = None,
    ) -> None:
        # 允许注入以便测试隔离；默认指向与路由一致的全局单例。
        self._projects = projects_service if projects_service is not None else default_projects_service
        self._canvas = canvas_service if canvas_service is not None else default_god_canvas_service
        self._asset_library = (
            asset_library_service if asset_library_service is not None else default_asset_library_service
        )

    # ------------------------------------------------------------------
    # 资源索引
    # ------------------------------------------------------------------
    def build_index(self, filters: AppliedFilters) -> ObservabilityIndex:
        """返回本阶段已实现的观测资源索引（不含任何未实现端点声明）。"""
        resources = [
            ResourceDescriptor(
                name="overview", path="/api/observability/overview",
                data_status=DataStatus.OK, description="进程内项目与任务计数总览（未接入指标为 null）",
            ),
            ResourceDescriptor(
                name="series", path="/api/observability/series",
                data_status=DataStatus.DEGRADED, description="指标采样序列（observability_samples，按真实采样求值）",
            ),
            ResourceDescriptor(
                name="events", path="/api/observability/events",
                data_status=DataStatus.OK, description="已脱敏认证审计事件（core.audit 环形缓冲）",
            ),
            ResourceDescriptor(
                name="tasks", path="/api/observability/tasks",
                data_status=DataStatus.OK, description="god-canvas 进程内任务清单",
            ),
            ResourceDescriptor(
                name="health", path="/api/observability/health",
                data_status=DataStatus.DEGRADED, description="本进程组件可用性（含 psutil 硬件读数与采样存储）",
            ),
            ResourceDescriptor(
                name="sources", path="/api/observability/sources",
                data_status=DataStatus.OK, description="数据源注册表（真实登记本进程来源路径）",
            ),
            ResourceDescriptor(
                name="asset-volumes", path="/api/observability/asset-volumes",
                data_status=DataStatus.DEGRADED, description="素材体积索引（真实遍历 GW_ALLOWED_ROOTS；未配置为空 + not_integrated）",
            ),
        ]
        return ObservabilityIndex(
            service=SERVICE_NAME,
            contract_version=CONTRACT_VERSION,
            generated_at=_now_iso(),
            data_status=DataStatus.DEGRADED,
            data_gaps=[GAP_LATENCY],
            resources=resources,
            applied_filters=filters,
        )

    # ------------------------------------------------------------------
    # 总览
    # ------------------------------------------------------------------
    def overview(self, filters: AppliedFilters) -> ObservabilityOverview:
        """总览计数严格来自真实服务实例当前内存状态。"""
        project_counts, project_gap = self._project_counts()
        job_counts, job_gap = self._job_counts()

        gaps: List[str] = [GAP_LATENCY]
        if project_gap:
            gaps.append(project_gap)
        if job_gap:
            gaps.append(job_gap)

        failed = job_counts.by_status.get("failed", 0) + job_counts.by_status.get("interrupted", 0)
        summary = OverviewSummary(
            total=job_counts.total,
            failed=failed,
            queue_depth=job_counts.queue_depth,
            p95_latency_ms=None,
        )
        status = DataStatus.OK
        if project_gap or job_gap:
            status = DataStatus.DEGRADED
        return ObservabilityOverview(
            data_status=status,
            generated_at=_now_iso(),
            summary=summary,
            projects=project_counts,
            jobs=job_counts,
            data_gaps=gaps,
            applied_filters=filters,
        )

    def _project_counts(self) -> Tuple[ProjectCounts, Optional[str]]:
        """按公开读取接口统计项目；服务不可用时返回零计数 + 说明。"""
        try:
            active = self._projects.list_projects(archived=False, deleted=False)
            archived = self._projects.list_projects(archived=True, deleted=False)
            trashed = self._projects.list_projects(deleted=True)
        except Exception:
            return (
                ProjectCounts(total=0, active=0, archived=0, trashed=0),
                "ProjectsService 不可用，项目计数降级为 0（不代表真实为 0）",
            )
        return (
            ProjectCounts(
                total=len(active) + len(archived) + len(trashed),
                active=len(active),
                archived=len(archived),
                trashed=len(trashed),
            ),
            None,
        )

    def _job_snapshot(self) -> Tuple[List[Any], Optional[str]]:
        """读取 god-canvas 内存任务快照；不可用时返回空清单 + 说明。"""
        reader = getattr(self._canvas, "list_jobs", None)
        if reader is None:
            return [], "GodCanvasService 未提供只读任务快照能力，任务计数降级为 0（不代表真实为 0）"
        try:
            return list(reader()), None
        except Exception:
            return [], "GodCanvasService 任务快照读取失败，任务计数降级为 0（不代表真实为 0）"

    def _job_counts(self) -> Tuple[JobCounts, Optional[str]]:
        """按状态统计真实内存任务。"""
        jobs, gap = self._job_snapshot()
        by_status: Dict[str, int] = {}
        for job in jobs:
            state = str(getattr(job, "state", "") or "unknown")
            by_status[state] = by_status.get(state, 0) + 1
        return (
            JobCounts(
                total=len(jobs),
                by_status=by_status,
                queue_depth=by_status.get("accepted", 0),
            ),
            gap,
        )

    # ------------------------------------------------------------------
    # 指标序列（真实采样存储）
    # ------------------------------------------------------------------
    def series(self, filters: AppliedFilters) -> ObservabilitySeries:
        """读取真实采样存储；读取前落盘一次真实硬件采样，不足 2 点返回 degraded，绝不插值。"""
        # 未显式指定 metrics 时，默认返回全部真实白名单指标，避免「无参数=未接入」的误导。
        requested = list(filters.metrics) or list(telemetry.KNOWN_METRICS)
        # 观测系统自身即真实采样源：每次读取先写入一次真实 psutil 读数（psutil 缺失时不写）。
        if any(name in telemetry.KNOWN_METRICS for name in requested):
            try:
                telemetry.record_sample()
            except Exception:
                pass
        start_ms, end_ms = filters.resolve_window(_now_ms())
        metrics: List[SeriesMetric] = []
        series: Dict[str, List[Any]] = {}
        gaps: List[str] = []
        unknown = [name for name in requested if name not in telemetry.KNOWN_METRICS]
        for name in unknown:
            gaps.append("指标 %s 不在真实采样白名单内，未返回任何点" % name)
        for name in requested:
            if name not in telemetry.KNOWN_METRICS:
                metrics.append(SeriesMetric(metric=name, data_status=DataStatus.NOT_INTEGRATED, points=[]))
                continue
            points = telemetry.load_series(name, start_ms, end_ms)
            if len(points) < 2:
                metrics.append(SeriesMetric(metric=name, data_status=DataStatus.DEGRADED, points=points))
                gaps.append("指标 %s 真实采样不足 2 点，未渲染曲线（禁止插值）" % name)
            else:
                metrics.append(SeriesMetric(metric=name, data_status=DataStatus.OK, points=points))
                series[name] = points
        if not gaps and metrics and all(item.data_status == DataStatus.OK for item in metrics):
            overall = DataStatus.OK
        elif any(item.data_status == DataStatus.OK for item in metrics) or metrics:
            overall = DataStatus.DEGRADED
        else:
            overall = DataStatus.NOT_INTEGRATED
        if not gaps:
            gaps = []
        return ObservabilitySeries(
            data_status=overall,
            series=series,
            metrics=metrics,
            requested_metrics=requested,
            data_gap=("" if not gaps else gaps[0]),
            start_ms=start_ms,
            end_ms=end_ms,
            data_gaps=gaps,
            applied_filters=filters,
        )

    # ------------------------------------------------------------------
    # 事件（真实审计缓冲）
    # ------------------------------------------------------------------
    def events(self, filters: AppliedFilters) -> ObservabilityListResponse:
        """只投影真实审计记录；无法求值的过滤条件返回空 + degraded，不静默忽略。"""
        start_ms, end_ms = filters.resolve_window(_now_ms())
        gaps: List[str] = []
        blocked = self._id_scope_gaps(filters, subject="认证审计事件")
        if blocked:
            return ObservabilityListResponse(
                data_status=DataStatus.DEGRADED,
                items=[],
                total=0,
                limit=filters.limit,
                has_more=False,
                next_cursor=None,
                start_ms=start_ms,
                end_ms=end_ms,
                data_gaps=blocked,
                applied_filters=filters,
            )

        records = self._read_audit_records()
        if records is None:
            return ObservabilityListResponse(
                data_status=DataStatus.DEGRADED,
                items=[],
                total=0,
                limit=filters.limit,
                has_more=False,
                next_cursor=None,
                start_ms=start_ms,
                end_ms=end_ms,
                data_gaps=[GAP_AUDIT_UNAVAILABLE],
                applied_filters=filters,
            )
        projected = [self._project_event(index, record) for index, record in enumerate(records)]

        selected: List[ObservabilityEvent] = []
        for event, record in zip(projected, records):
            if filters.event_id and event.event_id != filters.event_id:
                continue
            if filters.status and event.status != filters.status.strip().lower():
                continue
            if filters.level and event.level != filters.level.strip().lower():
                continue
            if filters.source and event.source != filters.source.strip().lower():
                continue
            if start_ms is not None and float(record.get("at") or 0) * 1000 < start_ms:
                continue
            if end_ms is not None and float(record.get("at") or 0) * 1000 > end_ms:
                continue
            selected.append(event)

        page, has_more, next_cursor = self._paginate(selected, filters)
        return ObservabilityListResponse(
            data_status=DataStatus.OK,
            items=page,
            total=len(selected),
            limit=filters.limit,
            has_more=has_more,
            next_cursor=next_cursor,
            start_ms=start_ms,
            end_ms=end_ms,
            data_gaps=gaps,
            applied_filters=filters,
        )

    def _read_audit_records(self) -> Optional[List[Dict[str, Any]]]:
        """读取审计缓冲。

        返回 ``None`` 表示**读取失败**，与「读取成功但确实为空」严格区分：
        调用方必须把 ``None`` 映射为 degraded + data_gap，绝不静默当作 ok 返回空列表，
        否则会把「源不可用」伪造成「确实没有事件」。
        """
        try:
            return list(audit_log.list_auth_events())
        except Exception:
            return None

    def _project_event(self, index: int, record: Mapping[str, Any]) -> ObservabilityEvent:
        """把白名单审计记录投影为对外事件；事件标识为内容派生的稳定哈希。"""
        outcome = str(record.get("outcome") or "")
        status = _OUTCOME_STATUS.get(outcome, "unknown")
        seed = json.dumps(
            {
                "event": record.get("event"),
                "outcome": outcome,
                "reason": record.get("reason"),
                "subject": record.get("subject"),
                "role": record.get("role"),
                "auth_mode": record.get("auth_mode"),
                "at": record.get("at"),
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        digest = hashlib.sha256(seed.encode("utf-8")).hexdigest()[:16]
        return ObservabilityEvent(
            event_id=f"evt-{digest}",
            event_name=str(record.get("event") or "unknown"),
            level="warn" if status in _WARN_STATUSES else "info",
            status=status,
            source=AUDIT_SOURCE,
            message_safe=str(record.get("reason") or "") or "认证事件",
            timestamp=_iso_from_epoch(record.get("at") or 0),
            subject=str(record.get("subject") or "") or None,
            role=str(record.get("role") or "") or None,
            auth_mode=str(record.get("auth_mode") or "") or None,
        )

    # ------------------------------------------------------------------
    # 任务（真实内存任务表）
    # ------------------------------------------------------------------
    def tasks(self, filters: AppliedFilters) -> ObservabilityListResponse:
        """返回真实内存任务投影（当前状态快照，非历史查询）。

        过滤口径（**分档处理，绝不静默忽略**）：

        1. **作用域 ID 过滤**（project_id/entity_id/asset_id/canvas_id/stable_id）与
           任务投影不存在的维度（source/level）：无法求值 → 返回**空集合** +
           ``data_status=degraded`` 并披露。理由：无法验证就无法声称命中。
        2. **时间窗过滤**（range/start_ms/end_ms）：任务表**不记录任何时间戳**，
           因此它是「当前状态快照」而非历史序列。此时返回**真实任务**快照 +
           ``data_status=degraded``，并在 ``data_gaps`` 明确写出「时间条件未生效」
           （前端任务中心默认携带 range，若返回空会把真实任务误报为「窗口内无任务」）。

        证据边界：本端点不为任务补造时间戳，也不从画布反推 project_id 等关联。
        """
        scope_gaps = self._id_scope_gaps(filters, subject="god-canvas 任务", allow_job_id=True)
        for attribute, label in (("source", "source"), ("level", "level")):
            if getattr(filters, attribute):
                scope_gaps.append(
                    f"god-canvas 任务投影不记录 {label} 维度，按 {label} 过滤无法求值；"
                    "返回空结果而非忽略该过滤条件"
                )
        if scope_gaps:
            return ObservabilityListResponse(
                data_status=DataStatus.DEGRADED,
                items=[],
                total=0,
                limit=filters.limit,
                has_more=False,
                next_cursor=None,
                start_ms=filters.start_ms,
                end_ms=filters.end_ms,
                data_gaps=scope_gaps,
                applied_filters=filters,
            )

        window_requested = any(
            value is not None for value in (filters.start_ms, filters.end_ms)
        ) or bool(filters.range and filters.range.strip() != "all")

        jobs, reader_gap = self._job_snapshot()
        tasks = [self._project_task(job) for job in jobs]
        selected = [
            task
            for task in tasks
            if (not filters.status or task.status == filters.status.strip().lower())
            and (not filters.job_id or task.job_id == filters.job_id)
        ]
        gaps: List[str] = []
        if reader_gap:
            gaps.append(reader_gap)
        if window_requested:
            gaps.append(
                "god-canvas 任务不记录时间戳，range/start_ms/end_ms 未生效；"
                "本次返回的是当前状态快照（显式披露，非静默忽略时间条件）"
            )
        page, has_more, next_cursor = self._paginate(selected, filters)
        return ObservabilityListResponse(
            data_status=DataStatus.DEGRADED if gaps else DataStatus.OK,
            items=page,
            total=len(selected),
            limit=filters.limit,
            has_more=has_more,
            next_cursor=next_cursor,
            start_ms=filters.start_ms,
            end_ms=filters.end_ms,
            data_gaps=gaps,
            applied_filters=filters,
        )

    def _project_task(self, job: Any) -> ObservabilityTask:
        """把内存任务投影为对外结构；结果载荷只暴露「是否存在」。"""
        return ObservabilityTask(
            job_id=str(getattr(job, "job_id", "") or ""),
            status=str(getattr(job, "state", "") or "unknown"),
            poll_hint=getattr(job, "poll_hint", None),
            has_result=getattr(job, "result", None) is not None,
            error_safe=str(getattr(job, "error", "") or "") or None,
        )

    # ------------------------------------------------------------------
    # 健康
    # ------------------------------------------------------------------
    def health(self, filters: AppliedFilters) -> ObservabilityHealth:
        """如实反映组件可用性；未接入组件必须是 not_integrated，绝不无条件返回 ok。"""
        checks: List[HealthCheck] = []
        gaps: List[str] = []

        project_counts, project_gap = self._project_counts()
        if project_gap:
            checks.append(HealthCheck(name="projects", status="failed", message_safe=project_gap))
            gaps.append(project_gap)
        else:
            checks.append(
                HealthCheck(
                    name="projects",
                    status="ok",
                    message_safe=f"ProjectsService 可用，当前 {project_counts.total} 个项目（进程内内存）",
                )
            )

        jobs, job_gap = self._job_snapshot()
        if job_gap:
            checks.append(HealthCheck(name="canvas", status="failed", message_safe=job_gap))
            gaps.append(job_gap)
        else:
            checks.append(
                HealthCheck(
                    name="canvas",
                    status="ok",
                    message_safe=f"GodCanvasService 可用，当前 {len(jobs)} 个任务（进程内内存）",
                )
            )

        try:
            snapshot = self._asset_library.get_snapshot()
            library_count = len(getattr(snapshot, "libraries", []) or [])
            checks.append(
                HealthCheck(
                    name="asset_library",
                    status="ok",
                    message_safe=f"AssetLibraryService 可用，当前 {library_count} 个素材库（进程内内存）",
                )
            )
        except Exception:
            message = "AssetLibraryService 不可用，素材库能力降级"
            checks.append(HealthCheck(name="asset_library", status="failed", message_safe=message))
            gaps.append(message)

        records = self._read_audit_records()
        if records is None:
            checks.append(
                HealthCheck(
                    name="audit_buffer",
                    status="failed",
                    message_safe="审计缓冲不可读，认证事件不可观测",
                )
            )
            gaps.append(GAP_AUDIT_UNAVAILABLE)
        else:
            checks.append(
                HealthCheck(
                    name="audit_buffer",
                    status="ok",
                    message_safe=f"审计缓冲可读，当前缓存 {len(records)} 条已脱敏认证事件",
                )
            )

        if getattr(self._canvas, "_jobs", None) is None:
            checks.append(
                HealthCheck(name="job_store", status="unknown", message_safe="未发现可读的任务存储引用")
            )

        reading = telemetry.hardware_telemetry()
        if reading is None:
            checks.append(HealthCheck(name="hardware_telemetry", status="not_integrated",
                                      message_safe=GAP_HARDWARE))
            gaps.append(GAP_HARDWARE)
        else:
            checks.append(HealthCheck(
                name="hardware_telemetry",
                status="ok",
                message_safe="psutil 真实读数（CPU/内存/磁盘）",
                metrics=dict(reading),
            ))

        # GPU 遥测为可选真实来源：nvidia-smi 可用即接入，不可用才如实标未接入。
        gpu = telemetry.gpu_telemetry()
        if gpu is None:
            checks.append(HealthCheck(name="gpu_telemetry", status="not_integrated",
                                      message_safe=GAP_GPU))
            gaps.append(GAP_GPU)
        else:
            checks.append(HealthCheck(
                name="gpu_telemetry",
                status="ok",
                message_safe="nvidia-smi 真实读数（设备清单/利用率/显存）",
                metrics=dict(gpu),
            ))

        # metrics_series：先落盘一次真实采样（观测系统自身即采样源），再读存储；
        # 无点=degraded（真实为空），存储不可读=not_integrated。
        try:
            telemetry.record_sample()
        except Exception:
            pass
        try:
            metric_names = list(telemetry.KNOWN_METRICS)
            sample_total = sum(len(telemetry.load_series(name, None, None)) for name in metric_names)
        except Exception:
            checks.append(HealthCheck(name="metrics_series", status="not_integrated",
                                      message_safe=_GAP_METRICS_UNAVAILABLE))
            gaps.append(_GAP_METRICS_UNAVAILABLE)
        else:
            if sample_total > 0:
                checks.append(HealthCheck(
                    name="metrics_series", status="ok",
                    message_safe="指标采样存储可读，当前共 %d 个真实采样点" % sample_total,
                ))
            else:
                checks.append(HealthCheck(
                    name="metrics_series", status="degraded",
                    message_safe=_GAP_METRICS_EMPTY,
                ))
                gaps.append(_GAP_METRICS_EMPTY)

        # source_registry：真实登记本进程数据源。
        try:
            registered = telemetry.source_registry()
        except Exception:
            registered = []
        if registered:
            checks.append(HealthCheck(
                name="source_registry", status="ok",
                message_safe="已真实登记 %d 个数据源" % len(registered),
            ))
        else:
            checks.append(HealthCheck(name="source_registry", status="not_integrated",
                                      message_safe="数据源注册表为空"))
            gaps.append("数据源注册表为空")

        # asset_volume_index：遍历真实允许根目录；未配置=not_integrated（fail-closed）。
        try:
            volume = telemetry.scan_asset_volumes(limit=1)
        except Exception:
            volume = {"data_status": "not_integrated", "data_gaps": [_GAP_VOLUMES_UNCONFIGURED]}
        if volume.get("data_status") == "ok":
            checks.append(HealthCheck(name="asset_volume_index", status="ok",
                                      message_safe="素材体积索引可读（GW_ALLOWED_ROOTS）"))
        elif volume.get("data_status") == "degraded":
            checks.append(HealthCheck(name="asset_volume_index", status="degraded",
                                      message_safe="素材体积扫描部分失败，结果可能不完整"))
            gaps.extend(volume.get("data_gaps") or [])
        else:
            checks.append(HealthCheck(name="asset_volume_index", status="not_integrated",
                                      message_safe=_GAP_VOLUMES_UNCONFIGURED))
            gaps.append(_GAP_VOLUMES_UNCONFIGURED)

        if any(check.status == "failed" for check in checks):
            overall = "degraded"
            data_status = DataStatus.DEGRADED
        elif any(check.status in {"degraded", "not_integrated", "unknown"} for check in checks):
            overall = "partial"
            data_status = DataStatus.DEGRADED
        else:
            overall = "ok"
            data_status = DataStatus.OK

        return ObservabilityHealth(
            status=overall,
            data_status=data_status,
            checks=checks,
            buffered_audit_events=0 if records is None else len(records),
            checked_at=_now_iso(),
            data_gaps=gaps,
            applied_filters=filters,
        )

    # ------------------------------------------------------------------
    # 真实数据源注册表
    # ------------------------------------------------------------------
    def sources(self, filters: AppliedFilters) -> ObservabilityListResponse:
        """返回真实登记的数据源清单（与实现路径一一对应）。"""
        start_ms, end_ms = filters.resolve_window(_now_ms())
        items = telemetry.source_registry()
        return ObservabilityListResponse(
            data_status=DataStatus.OK,
            items=items,
            total=len(items),
            limit=filters.limit,
            has_more=False,
            next_cursor=None,
            start_ms=start_ms,
            end_ms=end_ms,
            data_gaps=[],
            applied_filters=filters,
        )

    def asset_volumes(self, filters: AppliedFilters) -> ObservabilityListResponse:
        """真实遍历允许根目录统计素材体积；未配置根目录为空 + not_integrated。"""
        start_ms, end_ms = filters.resolve_window(_now_ms())
        scan = telemetry.scan_asset_volumes(limit=filters.limit)
        items = scan["items"]
        status = DataStatus.OK if scan["data_status"] == "ok" else (
            DataStatus.NOT_INTEGRATED if scan["data_status"] == "not_integrated" else DataStatus.DEGRADED
        )
        return ObservabilityListResponse(
            data_status=status,
            items=items,
            total=len(items),
            limit=filters.limit,
            has_more=False,
            next_cursor=None,
            start_ms=start_ms,
            end_ms=end_ms,
            data_gaps=list(scan["data_gaps"]),
            applied_filters=filters,
        )

    def _empty_response(self, filters: AppliedFilters, gap: str) -> ObservabilityListResponse:
        start_ms, end_ms = filters.resolve_window(_now_ms())
        return ObservabilityListResponse(
            data_status=DataStatus.NOT_INTEGRATED,
            items=[],
            total=0,
            limit=filters.limit,
            has_more=False,
            next_cursor=None,
            start_ms=start_ms,
            end_ms=end_ms,
            data_gaps=[gap],
            applied_filters=filters,
        )

    # ------------------------------------------------------------------
    # 内部工具
    # ------------------------------------------------------------------
    @staticmethod
    def _id_scope_gaps(
        filters: AppliedFilters,
        *,
        subject: str,
        allow_job_id: bool = False,
    ) -> List[str]:
        """返回无法求值的稳定 ID 过滤条件说明；命中即应返回空集合。"""
        gaps: List[str] = []
        for attribute, label in _ID_SCOPE_LABELS:
            if getattr(filters, attribute):
                gaps.append(f"{subject}不携带 {label} 关联，按 {label} 过滤无法求值；返回空结果而非全部")
        if not allow_job_id and filters.job_id:
            gaps.append(f"{subject}不携带 job_id 关联，按 job_id 过滤无法求值；返回空结果而非全部")
        return gaps

    @staticmethod
    def _paginate(
        items: Sequence[Any],
        filters: AppliedFilters,
    ) -> Tuple[List[Any], bool, Optional[str]]:
        """基于不透明游标做真实分页，返回 (当前页, 是否还有更多, 下一页游标)。"""
        offset = decode_cursor(filters.cursor)
        page = list(items[offset : offset + filters.limit])
        next_offset = offset + len(page)
        has_more = next_offset < len(items)
        return page, has_more, encode_cursor(next_offset) if has_more else None


# 与既有领域服务同口径的全局单例。
default_observability_service = ObservabilityService()
