# Copyright 2026 qinxuedong (Gods-Workbench Authors)
# SPDX-License-Identifier: FSL-1.1-Apache-2.0
#
# Licensed under the Functional Source License, Version 1.1, Apache 2.0
# Future License (the "License"); you may not use this file except in
# compliance with the License. You may obtain a copy of the License at
#     https://fsl.software/
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""本地账户首次设置与密码登录；与 OIDC 授权码接口相互隔离。"""

import time
from ipaddress import ip_address

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, SecretStr

from gw.core import audit as audit_log
from gw.core import local_accounts, session, trusted_local
from gw.core.config import AUTH_MODE_LOCAL_ACCOUNT, load_runtime_auth_config
from gw.core.errors import CleanroomException

router = APIRouter(prefix="/api/asset-auth/local", tags=["本地账户"])


class Credentials(BaseModel):
    model_config = ConfigDict(extra="forbid")
    username: str = Field(min_length=3, max_length=64)
    password: SecretStr


class RecoveryReset(BaseModel):
    model_config = ConfigDict(extra="forbid")
    username: str = Field(min_length=3, max_length=64)
    recovery_code: str = Field(min_length=12, max_length=128)
    new_password: SecretStr


class RecoveryCodeRegeneration(BaseModel):
    model_config = ConfigDict(extra="forbid")
    current_password: SecretStr


class TrustedBootstrap(BaseModel):
    """受信本机一次性启动票据。

    票据只承载**短时一次性能力值**，不接受也不接受任何账号口令字段。
    """

    model_config = ConfigDict(extra="forbid")
    ticket: str = Field(min_length=8, max_length=2048)


def require_local_mode():
    if load_runtime_auth_config().mode != AUTH_MODE_LOCAL_ACCOUNT:
        raise CleanroomException(
            403, "LOCAL_ACCOUNT_DISABLED", "当前未启用本地账户登录"
        )


def _loopback(host: str) -> bool:
    if host == "localhost":
        return True
    try:
        return ip_address(host).is_loopback
    except ValueError:
        return False


def _session_response(
    request: Request,
    principal: dict,
    token: str,
    code: int,
    recovery_codes: list[str],
):
    # 登录成功后撤销浏览器原会话，不能利用固定旧 Cookie 获得新身份。
    local_accounts.delete_session(session.cookie_value(request.cookies, "session"))
    response = JSONResponse(
        {
            "authenticated": True,
            "principal": principal,
            "recovery_codes": recovery_codes,
        },
        status_code=code,
    )
    response.headers["Cache-Control"] = "no-store"
    response.set_cookie(
        session.session_cookie_name(),
        token,
        max_age=session.SESSION_COOKIE_MAX_AGE,
        httponly=True,
        samesite="strict",
        secure=request.url.scheme == "https",
        path="/",
    )
    return response


@router.post("/setup", status_code=201)
def setup(payload: Credentials, request: Request):
    require_local_mode()
    # 首次管理员只能由本机设置，且拒绝 DNS 重绑定到非回环主机名。
    if (
        not request.client
        or not _loopback(request.client.host)
        or not _loopback(request.url.hostname or "")
    ):
        raise CleanroomException(
            403,
            "LOCAL_SETUP_ONLY",
            "首次管理员只能在本机 localhost 或 127.0.0.1 页面创建",
        )
    principal, token = local_accounts.create_first_admin(
        payload.username, payload.password.get_secret_value()
    )
    recovery_codes = local_accounts.ensure_recovery_codes(principal["user_id"])
    return _session_response(request, principal, token, 201, recovery_codes)


@router.post("/login")
def login(payload: Credentials, request: Request):
    require_local_mode()
    principal, token = local_accounts.login(
        payload.username,
        payload.password.get_secret_value(),
        request.client.host if request.client else "unknown",
    )
    recovery_codes = local_accounts.ensure_recovery_codes(principal["user_id"])
    return _session_response(request, principal, token, 200, recovery_codes)


@router.post("/trusted-bootstrap")
def trusted_bootstrap(payload: TrustedBootstrap, request: Request):
    """用一次性启动票据兑换本机会话（受信本机模式）。

    身份凭据**只有**设备密钥签名的一次性票据；回环传输与回环 Host 只是
    纵深防御的附加约束，**不作为**身份判据。远程 / NAS / 反向代理 /
    DNS rebinding 场景都拿不到票据，因此不会自动成为本机 owner。

    兑换是原子的：消费票据、建立本机所有者、签发会话在同一事务内完成；
    任一步失败整体回滚，票据**不被消费**，可重试。
    """
    require_local_mode()
    if not trusted_local.trusted_local_enabled():
        raise CleanroomException(
            403, "TRUSTED_LOCAL_DISABLED", "受信本机模式未启用，已拒绝本次请求"
        )
    # 纵深防御：只接受回环传输 + 回环 Host，挡 DNS rebinding 与反向代理。
    if not _loopback(request.client.host if request.client else "") or not _loopback(
        request.url.hostname or ""
    ):
        raise CleanroomException(
            403,
            "TRUSTED_LOCAL_TRANSPORT_REJECTED",
            "只允许在本机回环地址完成本机身份握手",
        )
    try:
        claims = trusted_local.verify_bootstrap_ticket(payload.ticket)
    except CleanroomException as exc:
        audit_log.record_auth_event(
            audit_log.EVENT_LOGIN_DENIED,
            outcome=audit_log.OUTCOME_DENIED,
            reason=exc.code,
            auth_mode=AUTH_MODE_LOCAL_ACCOUNT,
        )
        raise
    jti = str(claims["jti"])
    fingerprint = str(claims["aud"])
    now = time.time()
    with local_accounts.database() as db:
        db.execute("BEGIN IMMEDIATE")
        if not local_accounts.consume_bootstrap_ticket(
            db,
            jti,
            now,
            device_fingerprint=fingerprint,
            expires_at=float(claims["exp"]),
        ):
            raise CleanroomException(
                401,
                "TRUSTED_LOCAL_BOOTSTRAP_INVALID",
                "启动票据已被使用或已过期，已拒绝本次请求",
            )
        principal, created, recovery_codes = local_accounts.ensure_trusted_local_owner(
            db, fingerprint, now=now
        )
        _, token = local_accounts.issue_session_for_user(db, principal["user_id"])
    if created:
        audit_log.record_management_event(
            audit_log.EVENT_BOOTSTRAP_COMPLETED,
            outcome=audit_log.OUTCOME_SUCCEEDED,
            reason="trusted_local_bootstrap",
            subject=principal["user_id"],
            role=principal["role"],
            auth_mode=AUTH_MODE_LOCAL_ACCOUNT,
            resource_id=principal["user_id"],
        )
    audit_log.record_auth_event(
        audit_log.EVENT_SESSION_ESTABLISHED,
        outcome=audit_log.OUTCOME_SUCCEEDED,
        reason="trusted_local_bootstrap",
        subject=principal["user_id"],
        role=principal["role"],
        auth_mode=AUTH_MODE_LOCAL_ACCOUNT,
    )
    trusted_local.clear_pending_ticket()
    return _session_response(request, principal, token, 200, recovery_codes)


@router.post("/reset-password")
def reset_password(payload: RecoveryReset, request: Request):
    require_local_mode()
    local_accounts.reset_password_with_recovery(
        payload.username,
        payload.recovery_code,
        payload.new_password.get_secret_value(),
        request.client.host if request.client else "unknown",
    )
    response = JSONResponse({"password_reset": True}, status_code=200)
    response.headers["Cache-Control"] = "no-store"
    response.delete_cookie(
        session.session_cookie_name(),
        path="/",
        samesite="strict",
        secure=request.url.scheme == "https",
    )
    return response


@router.post("/recovery-codes/regenerate")
def regenerate_recovery_codes(payload: RecoveryCodeRegeneration, request: Request):
    require_local_mode()
    token = session.cookie_value(request.cookies, "session")
    principal = local_accounts.get_session(token)
    if principal is None:
        raise CleanroomException(
            401, "AUTHENTICATION_REQUIRED", "请先使用本地账户登录"
        )
    recovery_codes = local_accounts.regenerate_recovery_codes(
        principal["user_id"], payload.current_password.get_secret_value()
    )
    response = JSONResponse({"recovery_codes": recovery_codes}, status_code=200)
    response.headers["Cache-Control"] = "no-store"
    return response
