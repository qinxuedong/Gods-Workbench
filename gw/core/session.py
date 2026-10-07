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

"""服务端会话与 OIDC 授权流程状态存储。

设计要点（与 ``core/oidc.py``、``core/config.py`` 配套）：

- **服务端会话**：浏览器的 Cookie 只承载一个**不透明**会话标识，身份信息
  保存在服务端内存中；令牌原文永不写入 Cookie、响应体或日志。
- **授权码 + PKCE 流程状态**：``state`` / ``nonce`` / ``code_verifier`` 一次性，
  存在服务端内存中并有短 TTL；回调时按 ``state`` 取出并**立即删除**（防重放）。
- **进程内单实例**：当前实现为**单进程内存存储**，多实例/多 worker 部署需要
  外部共享存储（例如 Redis）。该边界在多实例部署前必须闭环，本模块不做伪装。

证据边界：本模块不发起任何网络请求，也不做令牌校验；令牌校验由
``core/oidc.verify_jwt`` 负责。
"""

from __future__ import annotations

import contextvars
import hashlib
import hmac
import os
import secrets
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Mapping, Optional

# 会话有效期与流程状态有效期（秒）。流程状态刻意更短：只在用户往返 IdP 期间有效。
SESSION_TTL_SECONDS = 8 * 60 * 60
# 会话**绝对**过期上限（秒）：自登录成功起算，滑动续期不得越过的硬上限。
# 修复 R6-7：原实现只有滑动过期，活跃会话可被无限续期（会话永不过期）。
SESSION_ABSOLUTE_MAX_SECONDS = 24 * 60 * 60
FLOW_STATE_TTL_SECONDS = 10 * 60
# 清理阈值：每次写入时顺手清理过期项，避免内存无界增长。
_MAX_SESSIONS = 4096
_MAX_FLOW_STATES = 4096

# 测试态保留冻结契约使用的旧名；开发态与生产态必须使用不同 Cookie 名，
# 防止同一浏览器在两种运行态之间串会话。常量仅供兼容旧测试和静态调用，
# 运行时读写一律通过下方函数按当前运行态解析。
SESSION_COOKIE_NAME = "gw_session"
FLOW_COOKIE_NAME = "gw_oidc_flow"
_RUNTIME_COOKIE_NAMES = {
    "test": (SESSION_COOKIE_NAME, FLOW_COOKIE_NAME),
    "dev": ("gw_session_dev", "gw_oidc_flow_dev"),
    "prod": ("gw_session_prod", "gw_oidc_flow_prod"),
}


def runtime_cookie_names(mode: Optional[str] = None) -> tuple[str, str]:
    """返回当前运行态的会话 Cookie 名和 OIDC 流程 Cookie 名。

    测试态继续使用冻结的 ``gw_session`` / ``gw_oidc_flow``，以避免改变既有
    契约；开发态和生产态使用各自独立名称。未知模式不猜测生产配置，降级到
    测试名只用于直接调用模块的旧测试，正式入口总是在导入应用前设置合法模式。
    """
    selected = (mode or os.environ.get("GW_RUNTIME_MODE") or "test").strip().lower()
    return _RUNTIME_COOKIE_NAMES.get(selected, _RUNTIME_COOKIE_NAMES["test"])


def session_cookie_name(mode: Optional[str] = None) -> str:
    """返回当前运行态的会话 Cookie 名。"""
    return runtime_cookie_names(mode)[0]


def flow_cookie_name(mode: Optional[str] = None) -> str:
    """返回当前运行态的一次性 OIDC 流程 Cookie 名。"""
    return runtime_cookie_names(mode)[1]


def cookie_value(cookies: Mapping[str, str], kind: str, mode: Optional[str] = None) -> Optional[str]:
    """只读取当前运行态 Cookie，不接受其它运行态的同名会话。"""
    if kind == "session":
        name = session_cookie_name(mode)
    elif kind == "flow":
        name = flow_cookie_name(mode)
    else:
        raise ValueError("Cookie 类型不合法")
    value = cookies.get(name)
    return value if isinstance(value, str) and value else None


@dataclass
class _Session:
    """一条服务端会话记录；``principal`` 为经过校验的最小身份上下文。"""

    principal: Dict[str, Any]
    created_at: float
    expires_at: float
    # 绝对过期时间：登录成功时刻 + SESSION_ABSOLUTE_MAX_SECONDS，任何续期都不得越过。
    absolute_expires_at: float


@dataclass
class _FlowState:
    """一条授权码流程记录；回调时一次性消费。"""

    nonce: str
    code_verifier: str
    redirect_uri: str
    created_at: float
    metadata: Dict[str, Any] = field(default_factory=dict)


_SESSIONS: Dict[str, _Session] = {}
_FLOW_STATES: Dict[str, _FlowState] = {}


def _prune(now: float) -> None:
    """清理过期项；超过容量上限时丢弃最旧的一半，保证内存有界。

    注意：会话记录同时有滑动 ``expires_at`` 与硬上限 ``absolute_expires_at``，
    流程状态用 ``created_at`` + ``FLOW_STATE_TTL_SECONDS`` 的相对窗口；
    三者判定口径不同，不可混用同一属性比较，
    否则会把刚登记、仍在有效期内的流程状态误判为过期并清空（导致登录随机失败）。
    """
    # 会话必须同时满足「滑动窗口未到期」且「未越过绝对上限」；两者任一到期即清理。
    expired_sessions = [
        key
        for key, value in _SESSIONS.items()
        if value.expires_at <= now or value.absolute_expires_at <= now
    ]
    for key in expired_sessions:
        _SESSIONS.pop(key, None)
    flow_deadline = now - FLOW_STATE_TTL_SECONDS
    expired_flows = [key for key, value in _FLOW_STATES.items() if value.created_at <= flow_deadline]
    for key in expired_flows:
        _FLOW_STATES.pop(key, None)

    if len(_SESSIONS) > _MAX_SESSIONS:
        for key in sorted(_SESSIONS, key=lambda k: _SESSIONS[k].created_at)[: len(_SESSIONS) // 2]:
            _SESSIONS.pop(key, None)
    if len(_FLOW_STATES) > _MAX_FLOW_STATES:
        for key in sorted(_FLOW_STATES, key=lambda k: _FLOW_STATES[k].created_at)[: len(_FLOW_STATES) // 2]:
            _FLOW_STATES.pop(key, None)


def reset_stores() -> None:
    """清空会话与流程状态（供测试与重启语义使用）。"""
    _SESSIONS.clear()
    _FLOW_STATES.clear()


# ---------------------------------------------------------------------------
# 服务端会话
# ---------------------------------------------------------------------------

def create_session(principal: Mapping[str, Any]) -> str:
    """创建服务端会话并返回不透明会话标识。

    ``principal`` 中的 ``issuer`` 与 ``config_generation`` 由调用方在登录成功时
    写入，表示该会话绑定的发行者与信任根代际。读取时由认证层核对，
    不一致则撤销；本函数不自行发明缺省信任根。
    """
    now = time.time()
    _prune(now)
    session_id = secrets.token_urlsafe(32)
    _SESSIONS[session_id] = _Session(
        principal=dict(principal),
        created_at=now,
        expires_at=now + SESSION_TTL_SECONDS,
        absolute_expires_at=now + SESSION_ABSOLUTE_MAX_SECONDS,
    )
    return session_id


def get_session(session_id: Optional[str]) -> Optional[Dict[str, Any]]:
    """按会话标识取回身份；缺失、过期或未知一律返回 None（失败关闭）。"""
    if not session_id or not isinstance(session_id, str):
        return None
    session = _SESSIONS.get(session_id)
    if session is None:
        return None
    now = time.time()
    # 绝对上限优先：越过硬上限即失效，且不得再用滑动续期救活。
    if session.absolute_expires_at <= now:
        _SESSIONS.pop(session_id, None)
        return None
    if session.expires_at <= now:
        _SESSIONS.pop(session_id, None)
        return None
    # 滑动续期必须被绝对上限封顶，否则活跃会话可无限延长（R6-7）。
    session.expires_at = min(now + SESSION_TTL_SECONDS, session.absolute_expires_at)
    return dict(session.principal)


def get_session_by_fingerprint(fingerprint: str, expected_subject: str) -> Optional[Dict[str, Any]]:
    """Resolve a live in-memory session from its SHA-256 fingerprint only.

    Runtime records may retain the fingerprint for reauthorization, but never the
    opaque Cookie value. OIDC sessions are process-local, so after restart an old
    fingerprint has no match and the worker fails closed until a new trusted
    request rebinds that run.
    """
    value = str(fingerprint or "").strip().lower()
    if len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
        return None
    subject = str(expected_subject or "").strip()
    if not subject:
        return None
    for session_id in tuple(_SESSIONS):
        candidate = hashlib.sha256(session_id.encode("utf-8")).hexdigest()
        if not hmac.compare_digest(candidate, value):
            continue
        principal = get_session(session_id)
        if principal and hmac.compare_digest(str(principal.get("username") or ""), subject):
            return principal
    return None


def delete_session(session_id: Optional[str]) -> bool:
    """删除会话（登出）；返回是否确实删除了一条记录。"""
    if not session_id:
        return False
    return _SESSIONS.pop(session_id, None) is not None


# ---------------------------------------------------------------------------
# 授权码 + PKCE 流程状态
# ---------------------------------------------------------------------------

def create_flow_state(*, state: str, nonce: str, code_verifier: str, redirect_uri: str) -> None:
    """登记一条一次性流程状态；``state`` 由调用方生成（需为高熵随机值）。

    若同一 state 已存在则覆盖写入（调用方每次登录都应生成新的 state）。
    """
    if not state or not isinstance(state, str):
        raise ValueError("state 缺失，拒绝登记流程")
    now = time.time()
    _prune(now)
    _FLOW_STATES[state] = _FlowState(
        nonce=nonce,
        code_verifier=code_verifier,
        redirect_uri=redirect_uri,
        created_at=now,
    )


def pop_flow_state(state: Optional[str]) -> Optional[_FlowState]:
    """取出并**立即删除**流程状态（防重放）；未知或过期返回 None。"""
    if not state or not isinstance(state, str):
        return None
    flow = _FLOW_STATES.pop(state, None)
    if flow is None:
        return None
    if flow.created_at + FLOW_STATE_TTL_SECONDS <= time.time():
        return None
    return flow


# ---------------------------------------------------------------------------
# 当前请求身份（由中间件写入，供 core.auth 在 oidc 模式下读取）
# ---------------------------------------------------------------------------

_CURRENT_PRINCIPAL: contextvars.ContextVar[Optional[Dict[str, Any]]] = contextvars.ContextVar(
    "gw_current_principal", default=None
)


def set_current_principal(principal: Optional[Mapping[str, Any]]):
    """写入当前请求的会话身份，返回可用于重置的 token。"""
    value = dict(principal) if principal else None
    return _CURRENT_PRINCIPAL.set(value)


def get_current_principal() -> Optional[Dict[str, Any]]:
    """读取当前请求的会话身份；未设置返回 None。"""
    value = _CURRENT_PRINCIPAL.get()
    return dict(value) if value else None


def reset_current_principal(token) -> None:
    """重置当前请求身份（中间件收尾时必须调用，避免上下文泄漏）。"""
    try:
        _CURRENT_PRINCIPAL.reset(token)
    except (ValueError, LookupError):
        # 跨上下文重置失败时退化为清空，不向上抛出。
        _CURRENT_PRINCIPAL.set(None)


__all__ = [
    "FLOW_COOKIE_NAME",
    "FLOW_STATE_TTL_SECONDS",
    "SESSION_ABSOLUTE_MAX_SECONDS",
    "SESSION_COOKIE_NAME",
    "SESSION_TTL_SECONDS",
    "cookie_value",
    "create_flow_state",
    "create_session",
    "delete_session",
    "flow_cookie_name",
    "get_current_principal",
    "get_session",
    "get_session_by_fingerprint",
    "pop_flow_state",
    "reset_current_principal",
    "reset_stores",
    "runtime_cookie_names",
    "session_cookie_name",
    "set_current_principal",
]
