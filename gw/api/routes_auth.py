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

"""认证与外部 IdP（OIDC Authorization Code + PKCE）路由实现。

端点为前端已引用但本仓此前未实现的 **认证面**，本轮按用户裁决「真实外部 IdP 接线」补齐：

- ``GET  /api/asset-auth/status``    当前会话与认证就绪状态（如实暴露，绝不伪造已登录）
- ``POST /api/asset-auth/login``     发起授权码 + PKCE 登录，返回 IdP 授权地址
- ``GET  /api/asset-auth/callback``  IdP 回调：校验 state、换取代币、校验 id_token、写会话 Cookie
- ``POST /api/asset-auth/logout``    清除服务端会话与 Cookie

安全口径（与 ``core/oidc.py`` / ``core/session.py`` 配套）：

- 只支持**公共客户端 + PKCE S256**，不接收、不读取、不记录 client secret；
- 会话由**服务端内存**保存，Cookie 只承载不透明会话标识（HttpOnly + SameSite=Lax）；
- 令牌原文**绝不**写入 Cookie、响应体、URL 或日志；交换成功后只保留最小身份上下文；
- 配置缺失、state 失配、签名/iss/aud/exp 校验失败一律**失败关闭**，不回落本地信任。

证据边界：本模块实现真实 OIDC 授权码流程的**代码路径**；是否接入真实生产 IdP
取决于部署方提供的 ``GW_OIDC_*`` 配置，本仓不包含任何真实 issuer 或客户端凭据。
"""

from __future__ import annotations

from typing import Any, Dict, Optional
from urllib.parse import urlencode, urlparse

from fastapi import APIRouter, Query, Request, Response, status
from fastapi.responses import JSONResponse, RedirectResponse

from gw.core import session as session_store
from gw.core import local_accounts
from gw.core.config import (
    AUTH_MODE_OIDC,
    AUTH_MODE_LOCAL_ACCOUNT,
    exchange_authorization_code,
    load_runtime_auth_config,
    resolve_endpoint,
)
from gw.core import audit as audit_log
from gw.core.errors import CleanroomException
from gw.core.oidc import (
    UnauthorizedException as _OidcUnauthorized,
    build_authorization_url,
    generate_nonce,
    generate_pkce_pair,
    generate_state,
    verify_jwt,
)

router = APIRouter(prefix="/api/asset-auth", tags=["asset-auth"])

# 登录成功后的应用落点；固定内部路径，禁止把用户重定向到外部站点。
DEFAULT_POST_LOGIN_REDIRECT = "/static/pages/index.html"
_FLOW_COOKIE_MAX_AGE = 600
_SESSION_COOKIE_MAX_AGE = 8 * 60 * 60

# 回调失败原因必须落在**封闭集合**内再入账：``?error=`` 由请求方控制，
# 若原样写入审计 ``reason``，任何外部文本都能污染审计记录（伪造事件语义）。
# 集合取自 RFC 6749 §4.1.2.1 的标准错误码 + 本仓自有的流程失败标记。
_CALLBACK_FAILURE_REASONS = frozenset(
    {
        # RFC 6749 §4.1.2.1 标准授权错误
        "invalid_request",
        "unauthorized_client",
        "access_denied",
        "unsupported_response_type",
        "invalid_scope",
        "server_error",
        "temporarily_unavailable",
        "interaction_required",
        "login_required",
        "consent_required",
        # 本仓自有流程失败标记
        "invalid_callback",
        "state_mismatch",
        "state_expired",
        "oidc_unavailable",
        "token_exchange_failed",
        "missing_id_token",
        "id_token_rejected",
    }
)
# 未命中封闭集合时的统一入账标记（不丢弃事实，但拒绝外部文本）。
_CALLBACK_FAILURE_FALLBACK = "unrecognized_failure"


class OidcNotConfiguredException(CleanroomException):
    """503：授权码流程未配置（缺少 client_id / redirect_uri 或 discovery 不可用）。"""

    def __init__(self, message: str = "外部 IdP 未配置，已拒绝登录"):
        super().__init__(status_code=503, code="OIDC_NOT_CONFIGURED", message=message)


class OidcAlreadyAuthenticatedException(CleanroomException):
    """409：当前会话已认证，无需重复发起登录。"""

    def __init__(self, message: str = "当前会话已登录，无需重复发起授权"):
        super().__init__(status_code=409, code="OIDC_ALREADY_AUTHENTICATED", message=message)


class OidcFlowFailedException(CleanroomException):
    """400：授权回调参数缺失或 state 失配（防重放 / 防 CSRF）。"""

    def __init__(self, message: str = "授权回调校验失败，已拒绝本次登录"):
        super().__init__(status_code=400, code="OIDC_FLOW_INVALID", message=message)


def _oidc_session_still_bound(principal: Dict[str, Any], runtime) -> bool:
    """会话是否仍绑定当前发行者、受众与配置代际。

    判定口径统一收口在 ``core.auth.oidc_session_trust_failure_reason``：
    发行者或受众为空、运行期未就绪、绑定发行者不一致、配置代际变化，
    都视为信任根失效。不把「内存里还有一条会话」当成已认证。

    保留本薄封装是为了让 ``/status`` 与 ``/login`` 的语义与本模块既有调用点不变，
    同时避免在两处各写一套漂移的判据。
    """
    from gw.core.auth import oidc_session_trust_failure_reason

    return oidc_session_trust_failure_reason(dict(principal), runtime) is None


def _principal_from_identity(identity, runtime) -> Dict[str, Any]:
    """把校验通过的身份压成最小会话主体（不保留令牌原文与完整 claims）。

    会话绑定当时的发行者与配置代际。信任根变更后，旧会话不得继续使用。
    """
    from gw.core.config import auth_config_generation

    return {
        "username": identity.subject,
        "display_name": identity.subject,
        "role": identity.role,
        "groups": list(identity.groups),
        "issuer": str(getattr(runtime.oidc, "issuer", "") or ""),
        "audience": str(getattr(runtime.oidc, "audience", "") or ""),
        "config_generation": auth_config_generation(),
    }


def _issuer_uses_https() -> bool:
    """判断当前 issuer 是否走 HTTPS；用于决定 Cookie 是否加 Secure 属性。

    本地回环联调（``http://127.0.0.1``）时不能加 Secure，否则浏览器丢弃 Cookie；
    HTTPS 部署下必须加，避免会话 Cookie 在明文信道上被嗅探。
    """
    try:
        runtime = load_runtime_auth_config()
        issuer = getattr(getattr(runtime, "oidc", None), "issuer", "") or ""
        return urlparse(issuer).scheme.lower() == "https"
    except Exception:
        return False


def _set_cookie(response: Response, name: str, value: str, max_age: int) -> None:
    """统一写入 HttpOnly 会话 Cookie（SameSite=Lax，HTTPS 下附加 Secure）。"""
    response.set_cookie(
        key=name,
        value=value,
        max_age=max_age,
        httponly=True,
        samesite="lax",
        secure=_issuer_uses_https(),
        path="/",
    )


def _clear_cookie(response: Response, name: str) -> None:
    """清除 Cookie（登出 / 回调收尾）。"""
    response.delete_cookie(key=name, path="/")


# ---------------------------------------------------------------------------
# 状态查询
# ---------------------------------------------------------------------------

@router.get("/status", summary="认证与会话状态", status_code=status.HTTP_200_OK)
def auth_status(request: Request):
    """如实返回当前认证状态；未认证时 ``principal`` 必须为 null。"""
    gw_session = session_store.cookie_value(request.cookies, "session")
    runtime = load_runtime_auth_config()
    if runtime.mode == AUTH_MODE_LOCAL_ACCOUNT:
        principal = local_accounts.get_session(gw_session)
        return JSONResponse({
            "auth_mode": runtime.mode, "oidc_ready": False,
            "authenticated": principal is not None, "principal": principal,
            "setup_required": local_accounts.setup_required(),
            "login_available": True, "logout_available": principal is not None,
            "reason": "", "release_authorized": False,
        }, headers={"Cache-Control": "no-store"})
    principal = session_store.get_session(gw_session)
    is_oidc = runtime.mode == AUTH_MODE_OIDC
    if is_oidc and principal is not None and not _oidc_session_still_bound(principal, runtime):
        # 配置失效或信任根已变：撤销旧会话，状态如实显示未认证。
        session_store.delete_session(gw_session)
        principal = None
    login_available = bool(is_oidc and runtime.ready and runtime.login_ready)
    if is_oidc and not runtime.ready:
        reason = runtime.reason or "外部 IdP 配置不可用"
    elif is_oidc and not runtime.login_ready:
        reason = runtime.reason or "授权码流程配置缺失"
    else:
        reason = ""
    return {
        "auth_mode": runtime.mode,
        "oidc_ready": bool(runtime.ready),
        "authenticated": principal is not None,
        "principal": principal,
        "login_available": login_available,
        "logout_available": principal is not None,
        "reason": reason,
        "release_authorized": False,
    }


# ---------------------------------------------------------------------------
# 发起登录（授权码 + PKCE）
# ---------------------------------------------------------------------------

@router.post("/login", summary="发起授权码 + PKCE 登录", status_code=status.HTTP_200_OK)
def auth_login(request: Request):
    """构造 IdP 授权地址并登记一次性流程状态。

    失败关闭语义：OIDC 未启用 / 配置缺失 / discovery 不可达 → 503，绝不伪造登录。
    """
    gw_session = session_store.cookie_value(request.cookies, "session")
    runtime = load_runtime_auth_config()
    if runtime.mode != AUTH_MODE_OIDC or not runtime.ready:
        audit_log.record_auth_event(
            audit_log.EVENT_LOGIN_DENIED,
            outcome=audit_log.OUTCOME_DENIED,
            reason="oidc_not_ready",
            auth_mode=runtime.mode,
        )
        raise OidcNotConfiguredException(runtime.reason or "外部 IdP 未配置，已拒绝登录")
    if not runtime.login_ready:
        audit_log.record_auth_event(
            audit_log.EVENT_LOGIN_DENIED,
            outcome=audit_log.OUTCOME_DENIED,
            reason="client_config_missing",
            auth_mode=runtime.mode,
        )
        raise OidcNotConfiguredException("授权码流程配置缺失（client_id / redirect_uri）")
    existing = session_store.get_session(gw_session)
    if existing is not None and not _oidc_session_still_bound(existing, runtime):
        session_store.delete_session(gw_session)
        existing = None
    if existing is not None:
        audit_log.record_auth_event(
            audit_log.EVENT_LOGIN_ALREADY_AUTHENTICATED,
            outcome=audit_log.OUTCOME_DENIED,
            reason="session_already_authenticated",
            auth_mode=runtime.mode,
        )
        raise OidcAlreadyAuthenticatedException()

    try:
        authorization_endpoint = resolve_endpoint(runtime.oidc.issuer, "authorization_endpoint")
    except Exception:
        audit_log.record_auth_event(
            audit_log.EVENT_LOGIN_DENIED,
            outcome=audit_log.OUTCOME_DENIED,
            reason="authorization_endpoint_unavailable",
            auth_mode=runtime.mode,
        )
        raise OidcNotConfiguredException("IdP authorization_endpoint 不可用，已拒绝登录")

    state = generate_state()
    nonce = generate_nonce()
    code_verifier, code_challenge = generate_pkce_pair()
    try:
        authorization_url = build_authorization_url(
            authorization_endpoint=authorization_endpoint,
            client_id=runtime.client_id,
            redirect_uri=runtime.redirect_uri,
            state=state,
            nonce=nonce,
            code_challenge=code_challenge,
            scope=runtime.scopes,
        )
    except _OidcUnauthorized as exc:
        audit_log.record_auth_event(
            audit_log.EVENT_LOGIN_DENIED,
            outcome=audit_log.OUTCOME_DENIED,
            reason="authorization_url_invalid",
            auth_mode=runtime.mode,
        )
        raise OidcNotConfiguredException(str(exc.message))

    session_store.create_flow_state(
        state=state,
        nonce=nonce,
        code_verifier=code_verifier,
        redirect_uri=runtime.redirect_uri,
    )

    audit_log.record_auth_event(
        audit_log.EVENT_LOGIN_STARTED,
        outcome=audit_log.OUTCOME_STARTED,
        reason="authorization_redirect_issued",
        auth_mode=runtime.mode,
    )
    response = JSONResponse({"authorization_url": authorization_url, "state": state})
    _set_cookie(response, session_store.flow_cookie_name(), state, _FLOW_COOKIE_MAX_AGE)
    return response


# ---------------------------------------------------------------------------
# 回调
# ---------------------------------------------------------------------------

@router.get("/callback", summary="IdP 授权回调")
def auth_callback(
    request: Request,
    code: Optional[str] = Query(None),
    state: Optional[str] = Query(None),
    error: Optional[str] = Query(None),
):
    """校验 state → 换取代币 → 校验 id_token → 建立会话并重定向回应用页。

    任何失败都**不放行登录**，而是重定向回应用页并携带 ``auth_error`` 标记。
    """
    gw_oidc_flow = session_store.cookie_value(request.cookies, "flow")
    gw_session = session_store.cookie_value(request.cookies, "session")
    runtime = load_runtime_auth_config()
    app_target = DEFAULT_POST_LOGIN_REDIRECT

    def fail(reason_code: str) -> RedirectResponse:
        # 审计只记「失败类别」，绝不记录授权码、state、nonce 或令牌原文。
        # 且类别必须先收敛到封闭集合：``?error=`` 由请求方控制，原样入账会污染审计记录。
        audit_reason = (
            reason_code if reason_code in _CALLBACK_FAILURE_REASONS else _CALLBACK_FAILURE_FALLBACK
        )
        audit_log.record_auth_event(
            audit_log.EVENT_CALLBACK_REJECTED,
            outcome=audit_log.OUTCOME_REJECTED,
            reason=audit_reason,
            auth_mode=runtime.mode,
        )
        target = f"{app_target}?{urlencode({'auth_error': reason_code})}"
        response = RedirectResponse(url=target, status_code=status.HTTP_302_FOUND)
        _clear_cookie(response, session_store.flow_cookie_name())
        return response

    if error:
        return fail(str(error))
    if not code or not state:
        return fail("invalid_callback")
    # CSRF / 重放：URL 中的 state 必须与浏览器持有的流程 Cookie 逐字一致。
    if not gw_oidc_flow or gw_oidc_flow != state:
        return fail("state_mismatch")
    flow = session_store.pop_flow_state(state)
    if flow is None:
        return fail("state_expired")
    if runtime.mode != AUTH_MODE_OIDC or not runtime.ready:
        return fail("oidc_unavailable")

    try:
        token_endpoint = resolve_endpoint(runtime.oidc.issuer, "token_endpoint")
        token_document = exchange_authorization_code(
            token_endpoint=token_endpoint,
            code=code,
            code_verifier=flow.code_verifier,
            client_id=runtime.client_id,
            redirect_uri=flow.redirect_uri,
        )
    except Exception:
        return fail("token_exchange_failed")

    id_token = token_document.get("id_token")
    if not isinstance(id_token, str) or not id_token.strip():
        return fail("missing_id_token")
    try:
        identity = verify_jwt(id_token, runtime.oidc, expected_nonce=flow.nonce)
    except _OidcUnauthorized:
        return fail("id_token_rejected")

    session_id = session_store.create_session(_principal_from_identity(identity, runtime))
    audit_log.record_auth_event(
        audit_log.EVENT_SESSION_ESTABLISHED,
        outcome=audit_log.OUTCOME_SUCCEEDED,
        reason="authorization_code_exchanged",
        subject=identity.subject,
        role=identity.role,
        auth_mode=runtime.mode,
    )
    response = RedirectResponse(url=app_target, status_code=status.HTTP_302_FOUND)
    _set_cookie(response, session_store.session_cookie_name(), session_id, _SESSION_COOKIE_MAX_AGE)
    _clear_cookie(response, session_store.flow_cookie_name())
    return response


# ---------------------------------------------------------------------------
# 登出
# ---------------------------------------------------------------------------

@router.post("/logout", summary="登出并清除会话", status_code=status.HTTP_204_NO_CONTENT)
def auth_logout(request: Request):
    """删除服务端会话并清除 Cookie；未登录时同样幂等返回 204。"""
    gw_session = session_store.cookie_value(request.cookies, "session")
    deleted = (local_accounts.delete_session(gw_session)
               if load_runtime_auth_config().mode == AUTH_MODE_LOCAL_ACCOUNT
               else session_store.delete_session(gw_session))
    audit_log.record_auth_event(
        audit_log.EVENT_LOGOUT,
        outcome=audit_log.OUTCOME_SUCCEEDED,
        reason="session_deleted" if deleted else "no_active_session",
        auth_mode=load_runtime_auth_config().mode,
    )
    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    _clear_cookie(response, session_store.session_cookie_name())
    _clear_cookie(response, session_store.flow_cookie_name())
    return response


