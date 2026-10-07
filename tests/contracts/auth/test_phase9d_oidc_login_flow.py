# -*- coding: utf-8 -*-
"""Phase 9D：真实外部 IdP（OIDC 授权码 + PKCE）登录链路契约测试。

覆盖用户 2026-09-21 裁决「真实外部 IdP 接线」的**端到端真实 HTTP 链路**：

    浏览器 → POST /api/asset-auth/login（登记一次性 state/nonce/PKCE）
           → 跳转 IdP 授权端点
           → GET /api/asset-auth/callback（校验 state → 授权码换取代币
              → 校验 id_token → 建立服务端会话 Cookie）
           → 携带 Cookie 访问受保护端点（角色只来自 IdP 组声明）

本地 IdP 为真实 HTTP 服务（随机端口、RSA 运行时生成不落盘），提供
``/.well-known/openid-configuration``、``/jwks``、``/token`` 三个端点。

证据边界：本地 IdP 是**测试桩**，不等同真实生产 IdP 的完整 OIDC 语义；
本文件不证明已接入生产 IdP、生产就绪或发布授权。
"""

from __future__ import annotations

import base64
import json
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from fastapi.testclient import TestClient

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
SRC_PATH = REPO_ROOT
if str(SRC_PATH) not in sys.path:
    sys.path.insert(0, str(SRC_PATH))

from gw.core import config as gw_config  # noqa: E402
from gw.core import session as session_store  # noqa: E402

AUDIENCE = "gods-workbench"
CLIENT_ID = "gods-workbench-web"
KID = "login-key-1"


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


@pytest.fixture(scope="module")
def rsa_keypair():
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return private_key, private_key.public_key()


@pytest.fixture(scope="module")
def jwks(rsa_keypair) -> dict:
    _, public_key = rsa_keypair
    numbers = public_key.public_numbers()
    return {
        "keys": [
            {
                "kty": "RSA",
                "use": "sig",
                "alg": "RS256",
                "kid": KID,
                "n": _b64url(numbers.n.to_bytes((numbers.n.bit_length() + 7) // 8, "big")),
                "e": _b64url(numbers.e.to_bytes((numbers.e.bit_length() + 7) // 8, "big")),
            }
        ]
    }


def _make_id_token(private_key, claims: dict) -> str:
    header = {"alg": "RS256", "typ": "JWT", "kid": KID}
    header_segment = _b64url(json.dumps(header, separators=(",", ":")).encode("utf-8"))
    payload_segment = _b64url(json.dumps(claims, separators=(",", ":")).encode("utf-8"))
    signing_input = f"{header_segment}.{payload_segment}".encode("ascii")
    signature = private_key.sign(signing_input, padding.PKCS1v15(), hashes.SHA256())
    return f"{header_segment}.{payload_segment}.{_b64url(signature)}"


@pytest.fixture()
def isolated_projects_service():
    """隔离全局项目服务状态，避免 E2E 写入污染其他用例的黄金夹具断言。"""
    from gw.projects_hub.service import default_projects_service

    snapshot = dict(default_projects_service._projects)
    sequence_snapshot = default_projects_service._seq
    try:
        yield default_projects_service
    finally:
        default_projects_service._projects.clear()
        default_projects_service._projects.update(snapshot)
        default_projects_service._seq = sequence_snapshot


@pytest.fixture()
def local_idp(rsa_keypair, jwks):
    """起一个真实 HTTP 本地 IdP：discovery + JWKS + token 端点。"""
    holder = {"issuer": "", "groups": ["gw-editor"], "nonce": "", "token_calls": 0}

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *args):  # noqa: D102 - 静默测试日志
            return

        def _send_json(self, payload: dict, status_code: int = 200) -> None:
            body = json.dumps(payload).encode("utf-8")
            self.send_response(status_code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):  # noqa: N802 - BaseHTTPRequestHandler 约定
            issuer = holder["issuer"]
            if self.path == "/.well-known/openid-configuration":
                self._send_json(
                    {
                        "issuer": issuer,
                        "authorization_endpoint": issuer + "/authorize",
                        "token_endpoint": issuer + "/token",
                        "jwks_uri": issuer + "/jwks",
                        "response_types_supported": ["code"],
                        "subject_types_supported": ["public"],
                        "id_token_signing_alg_values_supported": ["RS256"],
                    }
                )
                return
            if self.path == "/jwks":
                self._send_json(jwks)
                return
            self.send_response(404)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def do_POST(self):  # noqa: N802 - BaseHTTPRequestHandler 约定
            if self.path != "/token":
                self.send_response(404)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            length = int(self.headers.get("Content-Length") or 0)
            form = parse_qs(self.rfile.read(length).decode("utf-8"))
            holder["token_calls"] += 1
            private_key, _ = rsa_keypair
            now = time.time()
            claims = {
                "iss": holder["issuer"],
                "aud": AUDIENCE,
                "sub": "user-e2e-1",
                "iat": now - 10,
                "exp": now + 300,
                "nonce": holder["nonce"],
                "groups": holder["groups"],
                # 回显授权码仅用于断言换码确实发生（非任何真实凭据）。
                "code_echo": (form.get("code") or [""])[0],
            }
            self._send_json(
                {
                    "access_token": "opaque-access-token-not-relevant",
                    "token_type": "Bearer",
                    "expires_in": 300,
                    "id_token": _make_id_token(private_key, claims),
                }
            )

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    holder["issuer"] = f"http://127.0.0.1:{server.server_address[1]}"
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield {"issuer": holder["issuer"], "holder": holder}
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@pytest.fixture()
def clean_env(monkeypatch, tmp_path):
    """清空 GW_* 环境变量与全部内存态，保证用例之间互不污染。"""
    for name in list(os.environ):
        if name.startswith("GW_"):
            monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("GW_RUNTIME_MODE", "test")
    monkeypatch.setenv("GW_DATA_DIR", str(tmp_path / "gw-data"))
    monkeypatch.setenv("GW_LOCAL_AUTH_DB", str(tmp_path / "gw-auth" / "auth.sqlite3"))
    monkeypatch.setenv("GW_VIDEO_DATA_DIR", str(tmp_path / "gw-video"))
    gw_config.reset_runtime_auth_config_cache()
    gw_config.reset_discovery_cache()
    session_store.reset_stores()
    yield monkeypatch
    gw_config.reset_runtime_auth_config_cache()
    gw_config.reset_discovery_cache()
    session_store.reset_stores()


def _configure_oidc(clean_env, issuer: str, *, with_client: bool = True) -> None:
    """启用 OIDC，并按需补齐授权码流程所需的客户端配置。"""
    clean_env.setenv(gw_config.AUTH_MODE_ENV, gw_config.AUTH_MODE_OIDC)
    clean_env.setenv(gw_config.ISSUER_ENV, issuer)
    clean_env.setenv(gw_config.AUDIENCE_ENV, AUDIENCE)
    if with_client:
        clean_env.setenv(gw_config.CLIENT_ID_ENV, CLIENT_ID)
        clean_env.setenv(gw_config.REDIRECT_URI_ENV, "/api/asset-auth/callback")
    gw_config.reset_runtime_auth_config_cache()
    gw_config.reset_discovery_cache()


def _app():
    from gw.api.app import create_app

    return create_app()


# ---------------------------------------------------------------------------
# 状态：绝不伪造已登录
# ---------------------------------------------------------------------------

def test_status_local_account_mode_reports_no_principal(clean_env):
    """默认 local_account 模式：未认证时 principal 必须为 null，且不得伪造管理员。"""
    with TestClient(_app()) as api:
        body = api.get("/api/asset-auth/status").json()
    assert body["auth_mode"] == "local_account"
    assert body["authenticated"] is False
    assert body["principal"] is None
    assert body["logout_available"] is False
    assert body["release_authorized"] is False


def test_status_oidc_without_client_config_is_not_login_ready(clean_env, local_idp):
    """OIDC 校验配置齐全但缺 client_id/redirect_uri 时：login_available=false 且有原因。"""
    _configure_oidc(clean_env, local_idp["issuer"], with_client=False)
    with TestClient(_app()) as api:
        body = api.get("/api/asset-auth/status").json()
    assert body["auth_mode"] == "oidc"
    assert body["oidc_ready"] is True
    assert body["login_available"] is False
    assert body["authenticated"] is False
    assert body["principal"] is None
    assert body["reason"]


# ---------------------------------------------------------------------------
# 发起登录
# ---------------------------------------------------------------------------

def test_login_fails_closed_when_oidc_not_configured(clean_env):
    """显式启用 OIDC 但未配置 IdP 时必须 503，不得伪造登录成功。"""
    clean_env.setenv(gw_config.AUTH_MODE_ENV, gw_config.AUTH_MODE_OIDC)
    with TestClient(_app()) as api:
        response = api.post("/api/asset-auth/login")
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "OIDC_NOT_CONFIGURED"


def test_login_returns_authorization_url_with_pkce_and_sets_flow_cookie(clean_env, local_idp):
    """登录入口返回带 PKCE 的授权地址，并下发一次性流程 Cookie。"""
    _configure_oidc(clean_env, local_idp["issuer"])
    with TestClient(_app()) as api:
        response = api.post("/api/asset-auth/login")
        assert response.status_code == 200, response.text
        body = response.json()
        url = body["authorization_url"]
        parsed = urlparse(url)
        query = parse_qs(parsed.query)
        assert parsed.path.endswith("/authorize")
        assert query["response_type"] == ["code"]
        assert query["client_id"] == [CLIENT_ID]
        assert query["code_challenge_method"] == ["S256"]
        assert query["code_challenge"][0]
        assert query["state"] == [body["state"]]
        assert query["nonce"][0]
        # 流程 Cookie 已下发（HttpOnly），且不含授权码/令牌。
        cookie_header = "; ".join(f"{k}={v}" for k, v in response.cookies.items())
        assert session_store.FLOW_COOKIE_NAME in cookie_header
        assert "id_token" not in response.text
        # 状态仍是未认证（登录只是发起，不是登录完成）。
        status_body = api.get("/api/asset-auth/status").json()
        assert status_body["authenticated"] is False
        assert status_body["principal"] is None


def test_login_twice_while_authenticated_is_conflict(clean_env, local_idp):
    """已建立会话后重复发起登录返回 409，避免会话被静默替换。"""
    _configure_oidc(clean_env, local_idp["issuer"])
    with TestClient(_app()) as api:
        login = api.post("/api/asset-auth/login").json()
        state = login["state"]
        nonce = parse_qs(urlparse(login["authorization_url"]).query)["nonce"][0]
        local_idp["holder"]["nonce"] = nonce
        callback = api.get(
            "/api/asset-auth/callback",
            params={"code": "auth-code-1", "state": state},
            follow_redirects=False,
        )
        assert callback.status_code == 302
        assert api.get("/api/asset-auth/status").json()["authenticated"] is True

        again = api.post("/api/asset-auth/login")
        assert again.status_code == 409
        assert again.json()["detail"]["code"] == "OIDC_ALREADY_AUTHENTICATED"


# ---------------------------------------------------------------------------
# 回调：state 校验、换码、id_token 校验、会话建立
# ---------------------------------------------------------------------------

def test_callback_rejects_state_mismatch(clean_env, local_idp):
    """回调 state 与流程 Cookie 不一致时必须失败关闭，且不下发会话 Cookie。"""
    _configure_oidc(clean_env, local_idp["issuer"])
    with TestClient(_app()) as api:
        api.post("/api/asset-auth/login")
        response = api.get(
            "/api/asset-auth/callback",
            params={"code": "auth-code-x", "state": "attacker-supplied-state"},
            follow_redirects=False,
        )
    assert response.status_code == 302
    assert "auth_error=state_mismatch" in response.headers["location"]
    assert session_store.SESSION_COOKIE_NAME not in response.cookies
    assert local_idp["holder"]["token_calls"] == 0, "state 失配时不得发起令牌交换"


def test_callback_happy_path_establishes_session_and_role_from_idp(clean_env, local_idp, isolated_projects_service):
    """完整链路：换码 → 校验 id_token → 会话 Cookie → 角色来自 IdP 组声明。"""
    _configure_oidc(clean_env, local_idp["issuer"])
    local_idp["holder"]["groups"] = ["gw-editor"]
    with TestClient(_app()) as api:
        login = api.post("/api/asset-auth/login").json()
        state = login["state"]
        nonce = parse_qs(urlparse(login["authorization_url"]).query)["nonce"][0]
        local_idp["holder"]["nonce"] = nonce

        callback = api.get(
            "/api/asset-auth/callback",
            params={"code": "auth-code-happy", "state": state},
            follow_redirects=False,
        )
        assert callback.status_code == 302, callback.text
        assert callback.headers["location"] == "/static/pages/index.html"
        assert session_store.SESSION_COOKIE_NAME in callback.cookies
        assert local_idp["holder"]["token_calls"] == 1

        status_body = api.get("/api/asset-auth/status").json()
        assert status_body["authenticated"] is True
        assert status_body["principal"]["role"] == "editor"
        assert status_body["principal"]["username"] == "user-e2e-1"
        assert status_body["principal"]["groups"] == ["gw-editor"]
        # 令牌原文绝不出现在状态响应中。
        assert "id_token" not in json.dumps(status_body)

        # 会话即可授权写操作，且**请求头 X-User-Role 不能提权**。
        # S-01：携带会话 Cookie 的写请求现在要求同源 Origin（与 local_account 同口径），
        # 这里补上与真实页面一致的 Origin，以继续验证"Cookie 会话授权写操作"这一条。
        created = api.post(
            "/api/asset-registry/projects",
            json={"name": "会话授权项目", "project_type": "film"},
            headers={"X-User-Role": "governor", "Origin": "http://testserver"},
        )
        assert created.status_code == 201, created.text
        project_id = created.json()["project"]["project_id"]

        # 治理操作需要 governor：会话角色为 editor，必须 403（未被请求头提权）。
        archived = api.request(
            "DELETE",
            f"/api/asset-registry/projects/{project_id}",
            json={"expected_version": created.json()["project"]["version"]},
            headers={"X-User-Role": "governor", "Origin": "http://testserver"},
        )
        assert archived.status_code == 403, archived.text


def test_callback_rejects_replayed_state(clean_env, local_idp):
    """同一 state 只能消费一次；重放必须失败关闭（防重放）。"""
    _configure_oidc(clean_env, local_idp["issuer"])
    with TestClient(_app()) as api:
        login = api.post("/api/asset-auth/login").json()
        state = login["state"]
        nonce = parse_qs(urlparse(login["authorization_url"]).query)["nonce"][0]
        local_idp["holder"]["nonce"] = nonce

        first = api.get(
            "/api/asset-auth/callback",
            params={"code": "auth-code-1", "state": state},
            follow_redirects=False,
        )
        assert first.status_code == 302
        assert session_store.SESSION_COOKIE_NAME in first.cookies

        api.cookies.clear()
        replay = api.get(
            "/api/asset-auth/callback",
            params={"code": "auth-code-1", "state": state},
            follow_redirects=False,
        )
    assert "auth_error=" in replay.headers["location"]
    assert session_store.SESSION_COOKIE_NAME not in replay.cookies


def test_callback_rejects_id_token_with_wrong_nonce(clean_env, local_idp):
    """id_token 的 nonce 与流程不符时不得建立会话（防 id_token 重放/注入）。"""
    _configure_oidc(clean_env, local_idp["issuer"])
    local_idp["holder"]["nonce"] = "attacker-nonce"
    with TestClient(_app()) as api:
        login = api.post("/api/asset-auth/login").json()
        response = api.get(
            "/api/asset-auth/callback",
            params={"code": "auth-code-1", "state": login["state"]},
            follow_redirects=False,
        )
    assert response.status_code == 302
    assert "auth_error=id_token_rejected" in response.headers["location"]
    assert session_store.SESSION_COOKIE_NAME not in response.cookies


def test_callback_rejects_unmapped_group(clean_env, local_idp):
    """IdP 未映射组：不得建立会话（无组不授权）。"""
    _configure_oidc(clean_env, local_idp["issuer"])
    local_idp["holder"]["groups"] = ["totally-unknown-group"]
    with TestClient(_app()) as api:
        login = api.post("/api/asset-auth/login").json()
        nonce = parse_qs(urlparse(login["authorization_url"]).query)["nonce"][0]
        local_idp["holder"]["nonce"] = nonce
        response = api.get(
            "/api/asset-auth/callback",
            params={"code": "auth-code-1", "state": login["state"]},
            follow_redirects=False,
        )
    assert "auth_error=id_token_rejected" in response.headers["location"]
    assert session_store.SESSION_COOKIE_NAME not in response.cookies


# ---------------------------------------------------------------------------
# 登出
# ---------------------------------------------------------------------------

def test_logout_clears_session_and_revokes_write_access(clean_env, local_idp, isolated_projects_service):
    """登出后会话失效：状态回到未认证，写操作 401。"""
    _configure_oidc(clean_env, local_idp["issuer"])
    with TestClient(_app()) as api:
        login = api.post("/api/asset-auth/login").json()
        nonce = parse_qs(urlparse(login["authorization_url"]).query)["nonce"][0]
        local_idp["holder"]["nonce"] = nonce
        api.get(
            "/api/asset-auth/callback",
            params={"code": "auth-code-1", "state": login["state"]},
            follow_redirects=False,
        )
        assert api.get("/api/asset-auth/status").json()["authenticated"] is True

        logout = api.post("/api/asset-auth/logout")
        assert logout.status_code == 204

        status_body = api.get("/api/asset-auth/status").json()
        assert status_body["authenticated"] is False
        assert status_body["principal"] is None

        after = api.post(
            "/api/asset-registry/projects",
            json={"name": "登出后不应写入", "project_type": "film"},
        )
        assert after.status_code == 401, after.text


def test_oidc_mode_without_session_rejects_write(clean_env, local_idp, isolated_projects_service):
    """OIDC 模式下无会话的写操作必须 401（不得因 X-User-Role 而放行）。"""
    _configure_oidc(clean_env, local_idp["issuer"])
    with TestClient(_app()) as api:
        response = api.post(
            "/api/asset-registry/projects",
            json={"name": "匿名不应写入", "project_type": "film"},
            headers={"X-User-Role": "admin"},
        )
    assert response.status_code == 401, response.text
