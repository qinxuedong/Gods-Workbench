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

"""洁净室 API 的最小认证与角色边界。

本模块只实现接口层需要的边界检查，不伪造任何外部身份提供商：

- ``local``（仅显式开发/测试）：接受 Bearer 凭据作为本地/测试会话输入，角色由请求头
  ``X-User-Role`` 提供。该模式**不验证外部身份**，仅用于本地开发与测试，
  不得用于生产。
- ``oidc``：接入真实外部身份提供商。Bearer 令牌必须通过 ``core.oidc`` 的
  RS256 签名与 ``iss`` / ``aud`` / ``exp`` 校验，角色**只**由 IdP 组声明映射得出；
  请求头中的 ``X-User-Role`` 在该模式下被完全忽略，防止角色越权。

``local_account``（默认）使用 SQLite 账户与服务端会话，忽略客户端角色头；
外部身份部署仍可显式设置 ``GW_AUTH_MODE=oidc`` 并提供完整 OIDC 配置；
配置缺失时一律失败关闭（拒绝请求），不静默降级为本地通行。
"""

import hashlib
from dataclasses import dataclass
from typing import Any, Mapping, Optional

from gw.core import audit as audit_log
from gw.core.config import AUTH_MODE_LOCAL, AUTH_MODE_OIDC, AUTH_MODE_LOCAL_ACCOUNT, load_runtime_auth_config
from gw.core.errors import ForbiddenException, UnauthorizedException

KNOWN_ROLES = frozenset({"admin", "governor", "editor", "reviewer", "readonly"})
EDIT_ROLES = frozenset({"admin", "governor", "editor"})
GOVERNANCE_ROLES = frozenset({"admin", "governor"})


@dataclass(frozen=True)
class AuthContext:
    """请求认证后的最小上下文，不保存凭据原文。"""

    role: str
    subject: Optional[str] = None
    mode: str = AUTH_MODE_LOCAL_ACCOUNT
    identity_domain: Optional[str] = None
    display_name: Optional[str] = None


def oidc_session_trust_failure_reason(principal: Mapping[str, Any], runtime: Any) -> Optional[str]:
    """判定一条 OIDC 服务端会话当前是否仍处在可用信任根之内。

    返回 ``None`` 表示会话仍绑定当前信任根、可以继续使用；否则返回**封闭集合**内的
    失效原因，调用方必须撤销该会话并失败关闭。

    判定口径（任一条不成立即失效）：

    - 运行期配置必须就绪（``runtime.ready``，即 mode=oidc 且 issuer/audience/JWKS 齐备）；
    - 发行者与受众都不得为空（空值不得被当作"宽松匹配"）；
    - 会话建立时绑定的 ``issuer`` 必须与当前运行期 issuer 逐字相同；
    - 会话建立时绑定的 ``config_generation`` 必须与当前配置代际相同，
      即信任根相关环境变量（issuer / audience / jwks / 端点主机等）任一变化都会作废旧会话。

    S-01 证据边界：本函数只把"未就绪配置下仍返回可用 AuthContext"这一**观察**收口为
    失败关闭，不主张已证实的外部攻击链。会话记录缺失绑定字段（例如攻击者或旧版本
    直接注入的内存会话）一律按失效处理，不放行。
    """
    from gw.core.config import auth_config_generation  # 延迟导入，避免循环依赖

    if not getattr(runtime, "ready", False):
        return "oidc_not_ready"
    oidc_config = getattr(runtime, "oidc", None)
    issuer = str(getattr(oidc_config, "issuer", "") or "").strip()
    audience = str(getattr(oidc_config, "audience", "") or "").strip()
    if not issuer or not audience:
        return "oidc_not_ready"
    if str(principal.get("issuer") or "").strip() != issuer:
        return "issuer_mismatch"
    bound_generation = str(principal.get("config_generation") or "").strip()
    if not bound_generation or bound_generation != auth_config_generation():
        return "trust_root_changed"
    return None


def revoke_oidc_session(principal: Mapping[str, Any], reason: str) -> None:
    """撤销一条已失效的 OIDC 会话：删除记录、清空请求身份，并留下脱敏审计。

    ``reason`` 由调用方从封闭集合给出；审计只落事件类别与主体标识，
    绝不记录 Cookie 值、令牌原文或授权码。
    """
    from gw.core import session as session_store  # 延迟导入，避免循环依赖

    session_id = str(principal.get("session_id") or "").strip()
    if session_id:
        session_store.delete_session(session_id)
    session_store.set_current_principal(None)
    audit_log.record_auth_event(
        audit_log.EVENT_SESSION_REVOKED,
        outcome=audit_log.OUTCOME_DENIED,
        reason=reason,
        subject=str(principal.get("username") or "") or None,
        auth_mode=AUTH_MODE_OIDC,
    )


def _extract_bearer_token(authorization: Optional[str]) -> str:
    """从 Authorization 头提取 Bearer 令牌；缺失或格式错误一律拒绝。"""
    raw = (authorization or "").strip()
    scheme, separator, token = raw.partition(" ")
    if not separator or scheme.lower() != "bearer" or not token.strip():
        raise UnauthorizedException()
    return token.strip()


def require_authenticated(
    authorization: Optional[str],
    user_role: Optional[str] = "editor",
) -> AuthContext:
    """要求已认证会话并返回角色上下文。

    OIDC 模式下角色完全来自 IdP 声明；``user_role``（请求头）被忽略。
    本模式接受两种凭据，均**只**由服务端校验得出角色：

    1. 服务端会话：中间件已按 ``gw_session`` Cookie 解析出的会话身份
       （授权码 + PKCE 登录后的常规路径，见 ``api/routes_auth.py``）；
    2. ``Authorization: Bearer <id_token>``：便于服务间/脚本直连的令牌路径。

    两者都缺失或校验失败一律 401，绝不回落到本地信任。
    """
    runtime = load_runtime_auth_config()

    if runtime.mode == AUTH_MODE_LOCAL_ACCOUNT:
        from gw.core import session as session_store
        principal = session_store.get_current_principal()
        if not principal:
            raise UnauthorizedException(message="请先登录本地账户")
        role = str(principal.get("role") or "")
        if role not in KNOWN_ROLES:
            raise ForbiddenException(message="账户权限无效")
        return AuthContext(role=role, subject=principal["user_id"], mode=AUTH_MODE_LOCAL_ACCOUNT, identity_domain="local_account", display_name=str(principal.get("display_name") or principal.get("username") or ""))

    if runtime.mode == AUTH_MODE_OIDC:
        # 优先使用中间件写入的服务端会话身份（Cookie 承载不透明会话标识）。
        from gw.core import session as session_store  # 延迟导入

        principal = session_store.get_current_principal()
        if principal:
            # S-01：会话必须绑定发行者与配置代际。issuer/audience 为空或 oidc_ready=false 时，
            # **不得**仅因内存里仍有一条存活会话就返回可用 AuthContext；
            # 信任根变更或配置失效时撤销旧会话并失败关闭。
            trust_failure = oidc_session_trust_failure_reason(principal, runtime)
            if trust_failure is not None:
                revoke_oidc_session(principal, trust_failure)
                raise UnauthorizedException(message="外部 IdP 会话已失效，已拒绝请求")
            role = str(principal.get("role") or "").strip().lower()
            if role not in KNOWN_ROLES:
                audit_log.record_auth_event(
                    audit_log.EVENT_ROLE_REJECTED,
                    outcome=audit_log.OUTCOME_DENIED,
                    reason="session_role_unknown",
                    subject=str(principal.get("username") or "") or None,
                    role=role,
                    auth_mode=AUTH_MODE_OIDC,
                )
                raise ForbiddenException(message="会话角色不合法，已拒绝请求")
            issuer = str(getattr(runtime.oidc, "issuer", "") or "").strip()
            # Cookie 主体由服务端在已验签 OIDC 回调中写入 username=JWT sub。
            subject = str(principal.get("username") or "") or None
            return AuthContext(
                role=role,
                subject=subject,
                mode=AUTH_MODE_OIDC,
                identity_domain=f"oidc:{issuer}",
                display_name=str(principal.get("display_name") or subject or ""),
            )

    token = _extract_bearer_token(authorization)

    if runtime.mode == AUTH_MODE_OIDC:
        if not runtime.ready:
            # 配置缺失或非法：失败关闭，绝不回落到本地信任。
            audit_log.record_auth_event(
                audit_log.EVENT_TOKEN_REJECTED,
                outcome=audit_log.OUTCOME_DENIED,
                reason="oidc_not_ready",
                auth_mode=AUTH_MODE_OIDC,
            )
            raise UnauthorizedException(message="外部 IdP 未正确配置，已拒绝请求")
        from gw.core.oidc import verify_jwt  # 延迟导入

        try:
            identity = verify_jwt(token, runtime.oidc)
        except UnauthorizedException:
            # 审计只记「拒签」这一事实，绝不记录令牌原文或 claims。
            audit_log.record_auth_event(
                audit_log.EVENT_TOKEN_REJECTED,
                outcome=audit_log.OUTCOME_REJECTED,
                reason="token_verification_failed",
                auth_mode=AUTH_MODE_OIDC,
            )
            raise
        except Exception:
            # 校验器任何非预期异常都转为 401，避免泄漏内部细节。
            audit_log.record_auth_event(
                audit_log.EVENT_TOKEN_REJECTED,
                outcome=audit_log.OUTCOME_REJECTED,
                reason="token_verification_error",
                auth_mode=AUTH_MODE_OIDC,
            )
            raise UnauthorizedException(message="令牌校验失败，已拒绝请求")
        role = identity.role
        if role not in KNOWN_ROLES:
            audit_log.record_auth_event(
                audit_log.EVENT_ROLE_REJECTED,
                outcome=audit_log.OUTCOME_DENIED,
                reason="idp_role_not_authorized",
                subject=identity.subject,
                role=role,
                auth_mode=AUTH_MODE_OIDC,
            )
            raise ForbiddenException(message="IdP 角色映射未授权，已拒绝请求")
        return AuthContext(role=role, subject=identity.subject, mode=AUTH_MODE_OIDC, identity_domain=f"oidc:{runtime.oidc.issuer}", display_name=identity.subject)

    # 本地/测试模式：仅用于开发期，凭证内容不作真实性校验。
    if token.lower() in {"invalid", "expired"}:
        raise UnauthorizedException()
    role = (user_role or "editor").strip().lower()
    if role not in KNOWN_ROLES:
        raise ForbiddenException(message="未知用户角色，已拒绝请求")
    # 本地模式不暴露 Bearer 原文；用摘要作为进程内稳定主体，供资源范围过滤。
    subject = hashlib.sha256(token.encode("utf-8")).hexdigest()[:32]
    return AuthContext(role=role, subject=f"local:{subject}", mode=AUTH_MODE_LOCAL, identity_domain="local_dev", display_name=f"local:{subject}")


def require_edit_access(authorization: Optional[str], user_role: Optional[str] = "editor") -> AuthContext:
    """要求已认证且具备项目/画布写权限。"""
    context = require_authenticated(authorization, user_role)
    if context.role not in EDIT_ROLES:
        raise ForbiddenException(message="无当前资源写权限，已降级为只读")
    return context


def require_governance_access(
    authorization: Optional[str],
    user_role: Optional[str] = "editor",
) -> AuthContext:
    """要求已认证且具备项目生命周期治理权限。"""
    context = require_authenticated(authorization, user_role)
    if context.role not in GOVERNANCE_ROLES:
        raise ForbiddenException(message="无项目生命周期治理权限")
    return context
