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

"""资产审查服务边界与 C8 精确版本字段校验。

历史 B6 的 ``unavailable`` 入口继续保留给未接入的调用方；C8-T6 的真实落盘实现
使用本模块提供的纯校验和状态投影辅助函数，避免在路由和仓储中复制字段约定。
"""

from __future__ import annotations

from typing import Any, Mapping

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


def validate_version_binding(payload: Mapping[str, Any], *, require_source: bool = False) -> tuple[str, int]:
    """校验 C8 版本绑定的最小字段，不生成任何缺失 ID。"""
    if not isinstance(payload, Mapping):
        raise CleanroomException(400, "INVALID_REQUEST", "请求体必须是对象")
    asset_id = str(payload.get("asset_id") or "").strip()
    version_id = str(payload.get("version_id") or "").strip()
    if not asset_id or not version_id:
        raise CleanroomException(400, "INVALID_REQUEST", "C8 审阅必须携带 asset_id 和 version_id")
    if any(not value.isascii() or any(ch.isspace() for ch in value) for value in (asset_id, version_id)):
        raise CleanroomException(400, "INVALID_REQUEST", "asset_id 和 version_id 必须是无空格 ASCII 标识")
    try:
        version = int(payload.get("version"))
    except (TypeError, ValueError):
        raise CleanroomException(400, "INVALID_REQUEST", "version 必须是正整数") from None
    if version < 1:
        raise CleanroomException(400, "INVALID_REQUEST", "version 必须是正整数")
    if require_source:
        source = payload.get("source")
        if not isinstance(source, Mapping) or not str(source.get("provider") or "").strip():
            raise CleanroomException(400, "INVALID_REQUEST", "C8 审阅必须携带可核验 source.provider")
    elif payload.get("source") is not None and not isinstance(payload.get("source"), Mapping):
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
