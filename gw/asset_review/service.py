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

"""资产审查服务边界与 C8 精确版本字段校验。

历史 B6 的 ``unavailable`` 入口继续保留给未接入的调用方；C8-T6 的真实落盘实现
使用本模块提供的纯校验和状态投影辅助函数，避免在路由和仓储中复制字段约定。
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime, timezone
from typing import Any

from gw.core.errors import CleanroomException

DATA_STATUS_NOT_INTEGRATED = "not_integrated"
ERROR_CODE = "ASSET_REVIEW_NOT_INTEGRATED"

# C8-T6 的字段约定集中在服务边界，仓储只负责持久化和 CAS。
C8_VERSION_FIELDS = (
    "project_id",
    "entity_id",
    "episode_id",
    "scene_id",
    "shot_id",
    "canvas_id",
    "job_id",
    "asset_id",
    "version_id",
    "version",
    "media_type",
    "source",
)
C8_ARTIFACT_FIELDS = (
    "text_artifact_id",
    "settings_artifact_id",
    "media_artifact_id",
)


def is_version_bound_payload(payload: Mapping[str, Any] | None) -> bool:
    """识别 C8 精确版本请求，同时保留旧 B6 客户端的兼容路径。"""
    if not isinstance(payload, Mapping):
        return False
    return any(
        payload.get(key) not in (None, "")
        for key in ("version_id", "project_id", "shot_id", "job_id")
    )


def validate_version_binding(
    payload: Mapping[str, Any], *, require_source: bool = False
) -> tuple[str, int]:
    """校验 C8 版本绑定的最小字段，不生成任何缺失 ID。"""
    if not isinstance(payload, Mapping):
        raise CleanroomException(400, "INVALID_REQUEST", "请求体必须是对象")
    asset_id = str(payload.get("asset_id") or "").strip()
    version_id = str(payload.get("version_id") or "").strip()
    if not asset_id or not version_id:
        raise CleanroomException(
            400, "INVALID_REQUEST", "C8 审阅必须携带 asset_id 和 version_id"
        )
    if any(
        not value.isascii() or any(ch.isspace() for ch in value)
        for value in (asset_id, version_id)
    ):
        raise CleanroomException(
            400, "INVALID_REQUEST", "asset_id 和 version_id 必须是无空格 ASCII 标识"
        )
    try:
        version = int(payload.get("version"))
    except (TypeError, ValueError):
        raise CleanroomException(
            400, "INVALID_REQUEST", "version 必须是正整数"
        ) from None
    if version < 1:
        raise CleanroomException(400, "INVALID_REQUEST", "version 必须是正整数")
    if require_source:
        source = payload.get("source")
        if (
            not isinstance(source, Mapping)
            or not str(source.get("provider") or "").strip()
        ):
            raise CleanroomException(
                400, "INVALID_REQUEST", "C8 审阅必须携带可核验 source.provider"
            )
    elif payload.get("source") is not None and not isinstance(
        payload.get("source"), Mapping
    ):
        raise CleanroomException(400, "INVALID_REQUEST", "source 必须是对象")
    return version_id, version


def decision_from_payload(payload: Mapping[str, Any]) -> str:
    """兼容旧 status 字段，同时返回 C8 的持久化 decision 值。"""
    raw = str(payload.get("decision") or payload.get("status") or "").strip()
    if raw == "rejected":
        raw = "changes_requested"
    return raw


def next_action_for_decision(decision: str) -> str:
    if decision == "approved":
        return "deliver"
    if decision == "changes_requested":
        return "regenerate_version"
    return "review"


# C9-T5 将 Provider/审阅状态映射到同一份可回读的收口投影。
C9_CLOSURE_STATUSES = frozenset(
    {"open", "blocked", "recovery_required", "partially_recovered", "closed", "unknown"}
)
C9_NEXT_ACTIONS = frozenset(
    {
        "review",
        "regenerate_version",
        "restore_reference",
        "retry_recovery",
        "wait_for_provider",
        "reconcile_unknown_job",
        "reread_current_version",
        "resolve_permission",
        "inspect_persisted_state",
        "deliver",
    }
)

_PROVIDER_STATE_MAP = {
    "pending": ("blocked", "PROVIDER_PENDING", "wait_for_provider"),
    "waiting": ("blocked", "PROVIDER_PENDING", "wait_for_provider"),
    "queued": ("blocked", "PROVIDER_PENDING", "wait_for_provider"),
    "accepted": ("blocked", "PROVIDER_PENDING", "wait_for_provider"),
    "running": ("blocked", "PROVIDER_PENDING", "wait_for_provider"),
    "failed": ("blocked", "PROVIDER_FAILED", "regenerate_version"),
    "error": ("blocked", "PROVIDER_FAILED", "regenerate_version"),
    "unknown": ("unknown", "PROVIDER_UNKNOWN", "reconcile_unknown_job"),
    "outcome_unknown": ("unknown", "PROVIDER_UNKNOWN", "reconcile_unknown_job"),
}
_PROVIDER_STATE_KEYS = (
    "provider_status",
    "provider_state",
    "execution_status",
    "generation_status",
    "task_status",
)


def now_iso() -> str:
    """返回稳定的 UTC ISO 时间，写入 blocker 快照。"""
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def provider_state(value: Mapping[str, Any] | None) -> str | None:
    """从请求或持久对象识别 Provider 等待、失败和未知状态。"""
    if not isinstance(value, Mapping):
        return None
    if value.get("result_unknown") is True:
        return "unknown"
    candidates = [value.get(key) for key in _PROVIDER_STATE_KEYS]
    # 兼容只传 status 的 Provider/任务响应，但不把审阅 approved 等状态误判为 Provider 状态。
    candidates.append(value.get("status"))
    for raw in candidates:
        normalized = str(raw or "").strip().lower().replace("-", "_").replace(" ", "_")
        if normalized in _PROVIDER_STATE_MAP:
            return normalized
    return None


def blocker(
    item: Mapping[str, Any],
    *,
    code: str,
    scope: str,
    message: str,
    retryable: bool,
    observed_at: str | None = None,
) -> dict[str, Any]:
    """构造 C9 固定形状 blocker，只复制已有稳定 ID。"""
    subject: dict[str, Any] = {}
    for key in (
        "review_id",
        "delivery_id",
        "project_id",
        "shot_id",
        "job_id",
        "asset_id",
        "version_id",
        "version",
    ):
        if item.get(key) is not None:
            subject[key] = item[key]
    return {
        "code": code,
        "scope": scope,
        "subject": subject,
        "message": message,
        "retryable": bool(retryable),
        "observed_at": observed_at or now_iso(),
    }


def closure_projection(
    item: Mapping[str, Any],
    *,
    kind: str = "review",
    observed_at: str | None = None,
) -> dict[str, Any]:
    """根据当前事实生成 C9 ``closure_status/blocker/next_action`` 投影。"""
    existing_blocker = item.get("blocker")
    existing_status = item.get("closure_status")
    if existing_blocker is not None or existing_status in C9_CLOSURE_STATUSES:
        action = item.get("next_action")
        if existing_status == "closed" and existing_blocker is None:
            action = None
        return {
            "closure_status": existing_status or "blocked",
            "blocker": dict(existing_blocker)
            if isinstance(existing_blocker, Mapping)
            else None,
            "recovery": dict(item["recovery"])
            if isinstance(item.get("recovery"), Mapping)
            else item.get("recovery"),
            "next_action": (
                None
                if action is None and existing_status == "closed"
                else action
                if action in C9_NEXT_ACTIONS
                else (action or "inspect_persisted_state")
            ),
        }

    provider = provider_state(item)
    if provider:
        status, code, action = _PROVIDER_STATE_MAP[provider]
        return {
            "closure_status": status,
            "blocker": blocker(
                item,
                code=code,
                scope="job" if item.get("job_id") else kind,
                message={
                    "PROVIDER_PENDING": "Provider 仍在等待，尚未形成可审阅结果。",
                    "PROVIDER_FAILED": "Provider 执行失败，尚未形成可交付结果。",
                    "PROVIDER_UNKNOWN": "Provider 结果未知，禁止盲目重试副作用。",
                }[code],
                retryable=code == "PROVIDER_PENDING",
                observed_at=observed_at,
            ),
            "recovery": None,
            "next_action": action,
        }

    review_status = str(item.get("review_status") or "").strip().lower()
    status = (
        str(item.get("status") or item.get("delivery_status") or "").strip().lower()
    )
    if review_status in {"approval_invalidated", "superseded"} or status in {
        "approval_invalidated",
        "superseded",
    }:
        return {
            "closure_status": "blocked",
            "blocker": blocker(
                item,
                code="APPROVAL_INVALIDATED",
                scope="delivery" if kind == "delivery" else "review",
                message="当前资产已有新版本，原批准不能用于审阅或交付。",
                retryable=False,
                observed_at=observed_at,
            ),
            "recovery": None,
            "next_action": "review",
        }
    if review_status == "changes_requested" or status == "rejected":
        return {
            "closure_status": "blocked",
            "blocker": blocker(
                item,
                code="INVALID_STATE_TRANSITION",
                scope=kind,
                message="审阅要求修改，必须先生成并审阅新版本。",
                retryable=False,
                observed_at=observed_at,
            ),
            "recovery": None,
            "next_action": "regenerate_version",
        }
    if item.get("delivery_status") == "delivered" or status == "delivered":
        return {
            "closure_status": "closed",
            "blocker": None,
            "recovery": None,
            "next_action": None,
        }
    if status == "approved" or review_status == "approved":
        return {
            "closure_status": "open",
            "blocker": None,
            "recovery": None,
            "next_action": "deliver",
        }
    return {
        "closure_status": "open",
        "blocker": None,
        "recovery": None,
        "next_action": "review",
    }


def apply_closure_projection(
    item: dict[str, Any],
    *,
    kind: str = "review",
    observed_at: str | None = None,
) -> dict[str, Any]:
    """将当前事实投影落回记录，保证刷新/重启返回同一 blocker 快照。"""
    # 普通状态推进需要重新推导，不能被上一轮的 ``blocker`` 粘住；版本失效会显式写入 blocker 后再回读。
    source = dict(item)
    source.pop("closure_status", None)
    source.pop("blocker", None)
    source.pop("recovery", None)
    item.update(closure_projection(source, kind=kind, observed_at=observed_at))
    return item


def unavailable(
    endpoint: str,
    capability: str = "资产审查与交付能力",
    code: str = ERROR_CODE,
) -> None:
    """以契约化 503 结束尚未接入的审查与交付能力。"""
    raise CleanroomException(
        status_code=503,
        code=code,
        message=f"{capability}尚未接入已批准的数据后端",
        extra={
            "endpoint": endpoint,
            "unavailable": True,
            "data_status": DATA_STATUS_NOT_INTEGRATED,
        },
        expose_extra_fields={"endpoint", "unavailable", "data_status"},
    )


def unavailable_detail(
    endpoint: str,
    capability: str = "资产审查与交付能力",
    code: str = ERROR_CODE,
) -> dict[str, Any]:
    """返回供非 HTTP 调用方记录的结构化不可用说明，不生成业务数据。"""
    return {
        "endpoint": endpoint,
        "capability": capability,
        "code": code,
        "unavailable": True,
        "data_status": DATA_STATUS_NOT_INTEGRATED,
        "data_gaps": ["asset_review_backend_not_connected"],
    }
