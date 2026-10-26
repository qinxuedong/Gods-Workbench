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

"""本地账户首次设置与密码登录；与 OIDC 授权码接口相互隔离。"""

from ipaddress import ip_address

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, SecretStr

from gw.core import local_accounts, session
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
