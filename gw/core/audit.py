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

"""认证与认证管理事件审计落点。

本模块仅提供进程内、有界、脱敏的审计缓冲；生产持久化与多实例一致性仍由部署方负责。
令牌原文、授权码、Cookie、code_verifier 等凭据永不进入事件记录。
"""
from __future__ import annotations

import json
import logging
import threading
import time
from collections import deque
from typing import Any, Deque, Dict, List, Optional

logger = logging.getLogger("gw.audit")
AUDIT_MAX_EVENTS = 2048
_MAX_FIELD_CHARS = 256

# 认证事件名的封闭集合。
EVENT_LOGIN_STARTED = "auth.login.started"
EVENT_LOGIN_DENIED = "auth.login.denied"
EVENT_LOGIN_ALREADY_AUTHENTICATED = "auth.login.already_authenticated"
EVENT_SESSION_ESTABLISHED = "auth.session.established"
EVENT_CALLBACK_REJECTED = "auth.callback.rejected"
EVENT_TOKEN_REJECTED = "auth.token.rejected"
EVENT_ROLE_REJECTED = "auth.role.rejected"
EVENT_SESSION_REVOKED = "auth.session.revoked"
EVENT_LOGOUT = "auth.logout"

# B1 管理写操作事件。事件只保存资源标识与脱敏主体，不接收令牌原文。
EVENT_BOOTSTRAP_COMPLETED = "auth.bootstrap.completed"
EVENT_USER_CREATED = "auth.user.created"
EVENT_USER_UPDATED = "auth.user.updated"
EVENT_USER_DELETED = "auth.user.deleted"
EVENT_TEAM_CREATED = "auth.team.created"
EVENT_TEAM_DELETED = "auth.team.deleted"
EVENT_MEMBER_UPDATED = "auth.member.updated"
EVENT_MEMBER_DELETED = "auth.member.deleted"
EVENT_APPROVAL_UPDATED = "auth.approval.updated"
EVENT_TOKEN_CREATED = "auth.token.created"

AUTH_EVENT_NAMES = frozenset(
    {
        EVENT_LOGIN_STARTED,
        EVENT_LOGIN_DENIED,
        EVENT_LOGIN_ALREADY_AUTHENTICATED,
        EVENT_SESSION_ESTABLISHED,
        EVENT_CALLBACK_REJECTED,
        EVENT_TOKEN_REJECTED,
        EVENT_ROLE_REJECTED,
        EVENT_SESSION_REVOKED,
        EVENT_LOGOUT,
    }
)
MANAGEMENT_EVENT_NAMES = frozenset(
    {
        EVENT_BOOTSTRAP_COMPLETED,
        EVENT_USER_CREATED,
        EVENT_USER_UPDATED,
        EVENT_USER_DELETED,
        EVENT_TEAM_CREATED,
        EVENT_TEAM_DELETED,
        EVENT_MEMBER_UPDATED,
        EVENT_MEMBER_DELETED,
        EVENT_APPROVAL_UPDATED,
        EVENT_TOKEN_CREATED,
    }
)
ALL_EVENT_NAMES = AUTH_EVENT_NAMES | MANAGEMENT_EVENT_NAMES

OUTCOME_STARTED = "started"
OUTCOME_SUCCEEDED = "succeeded"
OUTCOME_DENIED = "denied"
OUTCOME_REJECTED = "rejected"
AUTH_OUTCOMES = frozenset({OUTCOME_STARTED, OUTCOME_SUCCEEDED, OUTCOME_DENIED, OUTCOME_REJECTED})

_EVENTS: Deque[Dict[str, Any]] = deque(maxlen=AUDIT_MAX_EVENTS)
_LOCK = threading.Lock()


def _clip(value: Optional[str]) -> str:
    """把可选字符串裁剪为定长安全值；非字符串一律降级为空串。"""
    if not isinstance(value, str):
        return ""
    return value if len(value) <= _MAX_FIELD_CHARS else value[:_MAX_FIELD_CHARS]


def _append(record: Dict[str, Any]) -> Dict[str, Any]:
    with _LOCK:
        _EVENTS.append(record)
    if logger.isEnabledFor(logging.INFO):
        logger.info("auth_audit %s", json.dumps(record, ensure_ascii=False, sort_keys=True))
    return dict(record)


def _record(
    event: str,
    *,
    outcome: str,
    reason: str = "",
    subject: Optional[str] = None,
    role: Optional[str] = None,
    auth_mode: Optional[str] = None,
    resource_id: Optional[str] = None,
) -> Dict[str, Any]:
    if event not in ALL_EVENT_NAMES:
        raise ValueError("未知的认证事件名，已拒绝记录")
    if outcome not in AUTH_OUTCOMES:
        raise ValueError("未知的认证事件结果，已拒绝记录")
    # 严格字段白名单，不把调用方的任意对象（尤其是 token）写入日志。
    record: Dict[str, Any] = {
        "event": event,
        "outcome": outcome,
        "reason": _clip(reason),
        "subject": _clip(subject),
        "role": _clip(role),
        "auth_mode": _clip(auth_mode),
        "at": time.time(),
    }
    if event in MANAGEMENT_EVENT_NAMES:
        record["resource_id"] = _clip(resource_id)
    return _append(record)


def record_auth_event(
    event: str,
    *,
    outcome: str,
    reason: str = "",
    subject: Optional[str] = None,
    role: Optional[str] = None,
    auth_mode: Optional[str] = None,
) -> Dict[str, Any]:
    """记录认证事件；管理事件必须使用 ``record_management_event``。"""
    if event not in AUTH_EVENT_NAMES:
        raise ValueError("未知的认证事件名，已拒绝记录")
    return _record(event, outcome=outcome, reason=reason, subject=subject, role=role, auth_mode=auth_mode)


def record_management_event(
    event: str,
    *,
    outcome: str,
    reason: str = "",
    subject: Optional[str] = None,
    role: Optional[str] = None,
    auth_mode: Optional[str] = None,
    resource_id: Optional[str] = None,
) -> Dict[str, Any]:
    """记录 B1 管理写事件；参数中没有 token 原文入口。"""
    if event not in MANAGEMENT_EVENT_NAMES:
        raise ValueError("未知的认证管理事件名，已拒绝记录")
    return _record(
        event,
        outcome=outcome,
        reason=reason,
        subject=subject,
        role=role,
        auth_mode=auth_mode,
        resource_id=resource_id,
    )


def list_auth_events() -> List[Dict[str, Any]]:
    """按发生顺序返回当前进程内的脱敏事件快照。"""
    with _LOCK:
        return [dict(item) for item in _EVENTS]


def auth_event_count() -> int:
    """返回当前进程内已保留的事件条数。"""
    with _LOCK:
        return len(_EVENTS)


def reset_audit_log() -> None:
    """清空审计缓冲（供测试与重启语义使用）。"""
    with _LOCK:
        _EVENTS.clear()
