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

"""持久 OIDC 会话与短期授权流程状态存储。

浏览器 Cookie 只承载不透明会话标识；现有 auth SQLite 数据库只保存其
SHA-256 指纹和最小身份上下文。OIDC ``state`` / ``nonce`` / ``code_verifier``
仍存在内存中并有短 TTL，回调时按 ``state`` 取出并立即删除（防重放）。

本模块不发起网络请求，也不做令牌校验；令牌校验由 ``core/oidc.verify_jwt``
负责。
"""

from __future__ import annotations

import contextvars
import hashlib
import hmac
import json
import os
import secrets
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

# 持久 Cookie 的最大 32 位秒数；服务端会话本身没有空闲或绝对过期时间。
SESSION_COOKIE_MAX_AGE = 2_147_483_647
FLOW_STATE_TTL_SECONDS = 10 * 60
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


def runtime_cookie_names(mode: str | None = None) -> tuple[str, str]:
    """返回当前运行态的会话 Cookie 名和 OIDC 流程 Cookie 名。

    测试态继续使用冻结的 ``gw_session`` / ``gw_oidc_flow``，以避免改变既有
    契约；开发态和生产态使用各自独立名称。未知模式不猜测生产配置，降级到
    测试名只用于直接调用模块的旧测试，正式入口总是在导入应用前设置合法模式。
    """
    selected = (mode or os.environ.get("GW_RUNTIME_MODE") or "test").strip().lower()
    return _RUNTIME_COOKIE_NAMES.get(selected, _RUNTIME_COOKIE_NAMES["test"])


def session_cookie_name(mode: str | None = None) -> str:
    """返回当前运行态的会话 Cookie 名。"""
    return runtime_cookie_names(mode)[0]


def flow_cookie_name(mode: str | None = None) -> str:
    """返回当前运行态的一次性 OIDC 流程 Cookie 名。"""
    return runtime_cookie_names(mode)[1]


def cookie_value(
    cookies: Mapping[str, str], kind: str, mode: str | None = None
) -> str | None:
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
class _FlowState:
    """一条授权码流程记录；回调时一次性消费。"""

    nonce: str
    code_verifier: str
    redirect_uri: str
    created_at: float
    metadata: dict[str, Any] = field(default_factory=dict)


_FLOW_STATES: dict[str, _FlowState] = {}


def _prune(now: float) -> None:
    """清理过期流程状态并限制其内存占用。"""
    flow_deadline = now - FLOW_STATE_TTL_SECONDS
    expired_flows = [
        key for key, value in _FLOW_STATES.items() if value.created_at <= flow_deadline
    ]
    for key in expired_flows:
        _FLOW_STATES.pop(key, None)

    if len(_FLOW_STATES) > _MAX_FLOW_STATES:
        for key in sorted(_FLOW_STATES, key=lambda k: _FLOW_STATES[k].created_at)[
            : len(_FLOW_STATES) // 2
        ]:
            _FLOW_STATES.pop(key, None)


def reset_stores() -> None:
    """清空测试态的持久 OIDC 会话与短期流程状态。"""
    _FLOW_STATES.clear()
    if (os.environ.get("GW_RUNTIME_MODE") or "test").strip().lower() != "test":
        return
    try:
        from gw.core.local_accounts import database, database_path

        if database_path().exists():
            with database() as db:
                db.execute("DELETE FROM oidc_sessions")
    except RuntimeError:
        # 测试清理在运行时路径尚未配置时只需清空进程内流程状态。
        return


def _session_fingerprint(session_id: str) -> str:
    return hashlib.sha256(session_id.encode("utf-8")).hexdigest()


def _stored_principal(token_hash: str) -> dict[str, Any] | None:
    from gw.core.local_accounts import database

    with database() as db:
        row = db.execute(
            "SELECT principal_json FROM oidc_sessions WHERE token_hash=?",
            (token_hash,),
        ).fetchone()
    if row is None:
        return None
    try:
        principal = json.loads(row["principal_json"])
    except (TypeError, ValueError):
        return None
    return principal if isinstance(principal, dict) else None


# ---------------------------------------------------------------------------
# 服务端会话
# ---------------------------------------------------------------------------


def create_session(principal: Mapping[str, Any]) -> str:
    """持久化身份上下文并返回不透明会话标识。

    ``principal`` 中的 ``issuer`` 与 ``config_generation`` 由调用方在登录成功时
    写入，表示该会话绑定的发行者与信任根代际。读取时由认证层核对，
    不一致则撤销；本函数不自行发明缺省信任根。
    """
    session_id = secrets.token_urlsafe(32)
    token_hash = _session_fingerprint(session_id)
    principal_json = json.dumps(dict(principal), ensure_ascii=False, separators=(",", ":"))
    from gw.core.local_accounts import database

    with database() as db:
        db.execute(
            "INSERT INTO oidc_sessions (token_hash, subject, principal_json) "
            "VALUES (?, ?, ?)",
            (token_hash, str(principal.get("username") or ""), principal_json),
        )
    return session_id


def get_session(session_id: str | None) -> dict[str, Any] | None:
    """按会话标识取回身份；缺失、撤销或未知一律返回 None（失败关闭）。"""
    if not session_id or not isinstance(session_id, str):
        return None
    return _stored_principal(_session_fingerprint(session_id))


def get_session_by_fingerprint(
    fingerprint: str, expected_subject: str
) -> dict[str, Any] | None:
    """按令牌指纹与预期主体解析持久化 OIDC 会话。"""
    value = str(fingerprint or "").strip().lower()
    if len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
        return None
    subject = str(expected_subject or "").strip()
    if not subject:
        return None
    principal = _stored_principal(value)
    if principal and hmac.compare_digest(str(principal.get("username") or ""), subject):
        return principal
    return None


def get_sessions_for_subject(subject: str) -> list[dict[str, Any]]:
    """读取主体的持久化 OIDC 身份信息，不返回会话令牌。"""
    subject_value = str(subject or "").strip()
    if not subject_value:
        return []
    from gw.core.local_accounts import database

    with database() as db:
        rows = db.execute(
            "SELECT principal_json FROM oidc_sessions WHERE subject=?",
            (subject_value,),
        ).fetchall()
    principals = []
    for row in rows:
        try:
            principal = json.loads(row["principal_json"])
        except (TypeError, ValueError):
            continue
        if not isinstance(principal, dict):
            continue
        if hmac.compare_digest(
            str(principal.get("username") or ""), subject_value
        ):
            principals.append(principal)
    return principals


def delete_session(session_id: str | None) -> bool:
    """删除会话（登出）；返回是否确实删除了一条记录。"""
    if not session_id or not isinstance(session_id, str):
        return False
    from gw.core.local_accounts import database

    with database() as db:
        return (
            db.execute(
                "DELETE FROM oidc_sessions WHERE token_hash=?",
                (_session_fingerprint(session_id),),
            ).rowcount
            > 0
        )


# ---------------------------------------------------------------------------
# 授权码 + PKCE 流程状态
# ---------------------------------------------------------------------------


def create_flow_state(
    *, state: str, nonce: str, code_verifier: str, redirect_uri: str
) -> None:
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


def pop_flow_state(state: str | None) -> _FlowState | None:
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

_CURRENT_PRINCIPAL: contextvars.ContextVar[dict[str, Any] | None] = (
    contextvars.ContextVar("gw_current_principal", default=None)
)


def set_current_principal(principal: Mapping[str, Any] | None):
    """写入当前请求的会话身份，返回可用于重置的 token。"""
    value = dict(principal) if principal else None
    return _CURRENT_PRINCIPAL.set(value)


def get_current_principal() -> dict[str, Any] | None:
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
    "SESSION_COOKIE_MAX_AGE",
    "SESSION_COOKIE_NAME",
    "cookie_value",
    "create_flow_state",
    "create_session",
    "delete_session",
    "flow_cookie_name",
    "get_current_principal",
    "get_session",
    "get_session_by_fingerprint",
    "get_sessions_for_subject",
    "pop_flow_state",
    "reset_current_principal",
    "reset_stores",
    "runtime_cookie_names",
    "session_cookie_name",
    "set_current_principal",
]
