# -*- coding: utf-8 -*-
"""Phase 9Q：认证路径审计落点（D12 修复）契约测试。

背景：独立复核发现 ``core/session.py`` / ``api/routes_auth.py`` / ``core/oidc.py`` /
``core/auth.py`` / ``api/app.py`` 中 ``logging`` / ``audit`` 命中数为 **0**，
登录成功、登出、state 失配、id_token 被拒、角色映射失败等认证事件**无审计落点**。
本用例把「认证事件必须留痕」以及「审计绝不记录凭据」变成持续门禁。

覆盖：

1. 审计记录只含**白名单字段**，且事件名 / 结果必须落在封闭集合内；
2. 登录被拒（OIDC 未配置 / 已登录 / 端点不可用）→ ``auth.login.denied`` /
   ``auth.login.already_authenticated``；
3. 登录发起成功 → ``auth.login.started``；
4. 回调 state 失配 → ``auth.callback.rejected``（reason=state_mismatch）；
5. 回调成功 → ``auth.session.established``（含 sub 与角色）；
6. 登出 → ``auth.logout``；
7. 令牌被拒 / 角色未授权 → ``auth.token.rejected`` / ``auth.role.rejected``；
8. **反向断言**：任何一条记录都不得包含令牌原文、授权码、state、nonce 或 Cookie 值。

证据边界：本地 IdP 为测试桩；本用例证明的是**代码路径有审计落点**，
不等于持久化审计库、多实例一致或合规留存，更不等于发布授权。
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
from urllib.parse import parse_qs

import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from fastapi.testclient import TestClient

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
SRC_PATH = REPO_ROOT
if str(SRC_PATH) not in sys.path:
    sys.path.insert(0, str(SRC_PATH))

from gw.core import audit as audit_log  # noqa: E402
from gw.core import config as gw_config  # noqa: E402
from gw.core import session as session_store  # noqa: E402

AUDIENCE = "gods-workbench-audit"
CLIENT_ID = "gods-workbench-audit-web"
KID = "audit-key-1"

# 审计记录允许出现的**全部**字段（白名单）。多一个字段即视为泄漏风险。
ALLOWED_RECORD_KEYS = {"event", "outcome", "reason", "subject", "role", "auth_mode", "at"}


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
def local_idp(rsa_keypair, jwks):
    """真实 HTTP 本地 IdP 测试桩：discovery + JWKS + token 端点。"""
    holder = {"issuer": "", "groups": ["gw-editor"], "nonce": "", "token_calls": 0}

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *args):  # noqa: D102 - 静默测试日志
            return

        def _send_json(self, payload: dict) -> None:
            body = json.dumps(payload).encode("utf-8")
            self.send_response(200)
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
                "sub": "audit-user-1",
                "iat": now - 10,
                "exp": now + 300,
                "nonce": holder["nonce"],
                "groups": holder["groups"],
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
    """清空 GW_* 环境变量与全部内存态（含审计缓冲），保证用例互不污染。"""
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
    audit_log.reset_audit_log()
    yield monkeypatch
    gw_config.reset_runtime_auth_config_cache()
    gw_config.reset_discovery_cache()
    session_store.reset_stores()
    audit_log.reset_audit_log()


def _configure_oidc(clean_env, issuer: str) -> None:
    clean_env.setenv(gw_config.AUTH_MODE_ENV, gw_config.AUTH_MODE_OIDC)
    clean_env.setenv(gw_config.ISSUER_ENV, issuer)
    clean_env.setenv(gw_config.AUDIENCE_ENV, AUDIENCE)
    clean_env.setenv(gw_config.CLIENT_ID_ENV, CLIENT_ID)
    clean_env.setenv(gw_config.REDIRECT_URI_ENV, "/api/asset-auth/callback")
    gw_config.reset_runtime_auth_config_cache()
    gw_config.reset_discovery_cache()


def _app():
    from gw.api.app import create_app

    return create_app()


def _events(name=None):
    records = audit_log.list_auth_events()
    if name is None:
        return records
    return [item for item in records if item["event"] == name]


# ---------------------------------------------------------------------------
# 1. 模块自身的白名单与封闭集合
# ---------------------------------------------------------------------------

def test_audit_records_only_whitelisted_fields(clean_env):
    """记录字段必须严格等于白名单；调用方无法夹带任意对象。"""
    record = audit_log.record_auth_event(
        audit_log.EVENT_LOGOUT,
        outcome=audit_log.OUTCOME_SUCCEEDED,
        reason="session_deleted",
        subject="user-1",
        role="editor",
        auth_mode="oidc",
    )
    assert set(record) == ALLOWED_RECORD_KEYS, record
    assert set(audit_log.list_auth_events()[0]) == ALLOWED_RECORD_KEYS


def test_audit_rejects_unknown_event_and_outcome(clean_env):
    """事件名 / 结果拼写漂移必须在写入前失败，而不是静默记脏账。"""
    with pytest.raises(ValueError):
        audit_log.record_auth_event("auth.not.a.real.event", outcome=audit_log.OUTCOME_SUCCEEDED)
    with pytest.raises(ValueError):
        audit_log.record_auth_event(audit_log.EVENT_LOGOUT, outcome="maybe")
    assert audit_log.auth_event_count() == 0


def test_audit_clips_oversized_fields(clean_env):
    """超长取值必须被截断，避免攻击者用超长字段放大内存占用。"""
    oversized = "x" * 10000
    record = audit_log.record_auth_event(
        audit_log.EVENT_TOKEN_REJECTED,
        outcome=audit_log.OUTCOME_REJECTED,
        reason=oversized,
    )
    assert len(record["reason"]) <= 256, "reason 未截断"


def test_audit_buffer_is_bounded(clean_env):
    """环形缓冲必须有界：写入超过容量后只保留最近 N 条。"""
    for _ in range(audit_log.AUDIT_MAX_EVENTS + 50):
        audit_log.record_auth_event(audit_log.EVENT_LOGOUT, outcome=audit_log.OUTCOME_SUCCEEDED)
    assert audit_log.auth_event_count() == audit_log.AUDIT_MAX_EVENTS


# ---------------------------------------------------------------------------
# 2. 登录发起路径的审计
# ---------------------------------------------------------------------------

def test_login_not_configured_records_denied(clean_env):
    """显式启用 OIDC 但未配置时登录 503，且必须留下 denied 审计。"""
    clean_env.setenv(gw_config.AUTH_MODE_ENV, gw_config.AUTH_MODE_OIDC)
    with TestClient(_app()) as api:
        response = api.post("/api/asset-auth/login")
    assert response.status_code == 503
    denied = _events(audit_log.EVENT_LOGIN_DENIED)
    assert denied, "登录被拒必须留痕"
    assert denied[-1]["outcome"] == audit_log.OUTCOME_DENIED
    assert denied[-1]["reason"] == "oidc_not_ready"


def test_login_started_is_audited(clean_env, local_idp):
    """登录发起成功必须留痕，且记录里不得有 state / nonce。"""
    _configure_oidc(clean_env, local_idp["issuer"])
    with TestClient(_app()) as api:
        response = api.post("/api/asset-auth/login")
    assert response.status_code == 200
    started = _events(audit_log.EVENT_LOGIN_STARTED)
    assert len(started) == 1, started
    state = response.json()["state"]
    assert state not in json.dumps(started, ensure_ascii=False)


def test_login_while_authenticated_records_already_authenticated(clean_env, local_idp):
    """已登录时重复发起登录 409，且必须留痕。"""
    _configure_oidc(clean_env, local_idp["issuer"])
    with TestClient(_app()) as api:
        login = api.post("/api/asset-auth/login").json()
        state = login["state"]
        nonce = parse_qs(login["authorization_url"].split("?", 1)[1])["nonce"][0]
        local_idp["holder"]["nonce"] = nonce
        api.get(
            "/api/asset-auth/callback",
            params={"code": "audit-code-1", "state": state},
            follow_redirects=False,
        )
        again = api.post("/api/asset-auth/login")
    assert again.status_code == 409
    assert _events(audit_log.EVENT_LOGIN_ALREADY_AUTHENTICATED), "重复登录必须留痕"


# ---------------------------------------------------------------------------
# 3. 回调路径的审计
# ---------------------------------------------------------------------------

def test_callback_state_mismatch_records_rejected(clean_env, local_idp):
    """state 失配必须留痕，且 reason 为 state_mismatch。"""
    _configure_oidc(clean_env, local_idp["issuer"])
    with TestClient(_app()) as api:
        api.post("/api/asset-auth/login")
        response = api.get(
            "/api/asset-auth/callback",
            params={"code": "x", "state": "attacker-supplied-state"},
            follow_redirects=False,
        )
    assert "auth_error=state_mismatch" in response.headers["location"]
    rejected = _events(audit_log.EVENT_CALLBACK_REJECTED)
    assert rejected, "state 失配必须留痕"
    assert rejected[-1]["reason"] == "state_mismatch"


def test_callback_id_token_rejected_is_audited(clean_env, local_idp):
    """id_token 被拒（错误 nonce）必须留痕。"""
    _configure_oidc(clean_env, local_idp["issuer"])
    local_idp["holder"]["nonce"] = "not-the-issued-nonce"
    with TestClient(_app()) as api:
        login = api.post("/api/asset-auth/login").json()
        response = api.get(
            "/api/asset-auth/callback",
            params={"code": "audit-code-2", "state": login["state"]},
            follow_redirects=False,
        )
    assert "auth_error=id_token_rejected" in response.headers["location"]
    rejected = _events(audit_log.EVENT_CALLBACK_REJECTED)
    assert rejected[-1]["reason"] == "id_token_rejected"


def test_session_established_records_subject_and_role_without_credentials(clean_env, local_idp):
    """登录成功必须留痕（含 sub 与角色），且**不得**记录任何凭据。"""
    _configure_oidc(clean_env, local_idp["issuer"])
    local_idp["holder"]["groups"] = ["gw-editor"]
    with TestClient(_app()) as api:
        login = api.post("/api/asset-auth/login").json()
        state = login["state"]
        nonce = parse_qs(login["authorization_url"].split("?", 1)[1])["nonce"][0]
        local_idp["holder"]["nonce"] = nonce
        response = api.get(
            "/api/asset-auth/callback",
            params={"code": "audit-code-3", "state": state},
            follow_redirects=False,
        )
        session_cookie = response.cookies.get(session_store.SESSION_COOKIE_NAME)
    assert response.status_code == 302
    assert "auth_error" not in (response.headers.get("location") or "")

    established = _events(audit_log.EVENT_SESSION_ESTABLISHED)
    assert len(established) == 1, established
    assert established[0]["subject"] == "audit-user-1"
    assert established[0]["role"] == "editor"

    # 反向断言：任何记录都不得出现授权码 / state / nonce / 会话 Cookie 值。
    blob = json.dumps(audit_log.list_auth_events(), ensure_ascii=False)
    for forbidden in ("audit-code-3", state, nonce, session_cookie or "gw_session-absent"):
        assert forbidden not in blob, "审计记录中出现凭据类取值: " + forbidden


# ---------------------------------------------------------------------------
# 4. 登出与令牌 / 角色路径
# ---------------------------------------------------------------------------

def test_logout_is_audited(clean_env, local_idp):
    """登出必须留痕，并区分「确实删除了会话」与「幂等空登出」。"""
    _configure_oidc(clean_env, local_idp["issuer"])
    with TestClient(_app()) as api:
        login = api.post("/api/asset-auth/login").json()
        nonce = parse_qs(login["authorization_url"].split("?", 1)[1])["nonce"][0]
        local_idp["holder"]["nonce"] = nonce
        api.get(
            "/api/asset-auth/callback",
            params={"code": "audit-code-4", "state": login["state"]},
            follow_redirects=False,
        )
        logout = api.post("/api/asset-auth/logout")
        idempotent = api.post("/api/asset-auth/logout")
    assert logout.status_code == 204 and idempotent.status_code == 204
    reasons = [item["reason"] for item in _events(audit_log.EVENT_LOGOUT)]
    assert "session_deleted" in reasons, reasons
    assert "no_active_session" in reasons, reasons


def test_forged_token_is_audited_and_rejected(clean_env):
    """OIDC 模式下伪造 Bearer 必须 401 且留痕；审计不得记录令牌原文。"""
    clean_env.setenv(gw_config.AUTH_MODE_ENV, gw_config.AUTH_MODE_OIDC)
    clean_env.setenv(gw_config.ISSUER_ENV, "https://idp.example.com")
    clean_env.setenv(gw_config.AUDIENCE_ENV, AUDIENCE)
    # 显式给出同源 JWKS URL：避免运行期配置阶段产生真实外呼。
    clean_env.setenv(gw_config.JWKS_URL_ENV, "https://idp.example.com/jwks")
    gw_config.reset_runtime_auth_config_cache()

    from gw.core.errors import UnauthorizedException
    from gw.core.auth import require_authenticated

    forged = "not-a-real-token"
    with pytest.raises(UnauthorizedException):
        require_authenticated("Bearer " + forged, None)

    rejected = _events(audit_log.EVENT_TOKEN_REJECTED)
    assert rejected, "令牌被拒必须留痕"
    assert rejected[-1]["outcome"] in {audit_log.OUTCOME_REJECTED, audit_log.OUTCOME_DENIED}
    assert forged not in json.dumps(audit_log.list_auth_events(), ensure_ascii=False)


def test_unknown_session_role_is_audited(clean_env):
    """会话角色不在已知集合时必须拒绝并留痕。

    S-01（2026-09-28）更新：``oidc_session_trust_failure_reason`` 已把
    "会话未绑定发行者与配置代际" 收口为**确定性失效**，并且它先于角色校验执行。
    因此本用例改为给出**就绪且已绑定**的信任根，只把角色置为未知值，
    以便继续专门验证"未知角色必须留痕"这一条，而不是被信任根判定抢先拒绝。
    """
    clean_env.setenv(gw_config.AUTH_MODE_ENV, gw_config.AUTH_MODE_OIDC)
    clean_env.setenv(gw_config.ISSUER_ENV, "https://idp.example.test")
    clean_env.setenv(gw_config.AUDIENCE_ENV, "gods-workbench")
    clean_env.setenv(gw_config.JWKS_URL_ENV, "https://idp.example.test/jwks.json")
    gw_config.reset_runtime_auth_config_cache()

    from gw.core.errors import ForbiddenException
    from gw.core.auth import require_authenticated

    token = session_store.set_current_principal(
        {
            "username": "user-x",
            "role": "root",
            "issuer": "https://idp.example.test",
            "config_generation": gw_config.auth_config_generation(),
        }
    )
    try:
        with pytest.raises(ForbiddenException):
            require_authenticated(None, None)
    finally:
        session_store.reset_current_principal(token)

    rejected = _events(audit_log.EVENT_ROLE_REJECTED)
    assert rejected, "角色未授权必须留痕"
    assert rejected[-1]["role"] == "root"


def test_unbound_session_is_rejected_before_role_check(clean_env):
    """S-01 追加：未绑定信任根的会话必须在**角色校验之前**就失败关闭。

    与上一条互为正反面证据：未绑定 -> 信任根失效拒绝（401）；
    已绑定但角色未知 -> 角色拒绝（403）。
    """
    clean_env.setenv(gw_config.AUTH_MODE_ENV, gw_config.AUTH_MODE_OIDC)
    clean_env.setenv(gw_config.ISSUER_ENV, "https://idp.example.test")
    clean_env.setenv(gw_config.AUDIENCE_ENV, "gods-workbench")
    clean_env.setenv(gw_config.JWKS_URL_ENV, "https://idp.example.test/jwks.json")
    gw_config.reset_runtime_auth_config_cache()

    from gw.core.errors import UnauthorizedException
    from gw.core.auth import require_authenticated

    # 缺 issuer / config_generation：即使配置就绪也必须拒绝。
    token = session_store.set_current_principal({"username": "user-y", "role": "admin"})
    try:
        with pytest.raises(UnauthorizedException):
            require_authenticated(None, None)
    finally:
        session_store.reset_current_principal(token)

    revoked = _events(audit_log.EVENT_SESSION_REVOKED)
    assert revoked, "未绑定信任根的会话必须留下撤销审计"
    assert revoked[-1]["reason"] in {"issuer_mismatch", "trust_root_changed", "oidc_not_ready"}


# ---------------------------------------------------------------------------
# Phase 9R 独立复核发现（2026-09-22 追加）
# ---------------------------------------------------------------------------

def test_callback_error_query_is_confined_to_closed_vocabulary(clean_env):
    """回调 ``?error=`` 由请求方控制，不得把任意文本写进审计 ``reason``。

    独立复核实测（修复前）：``GET /api/asset-auth/callback?error=<任意文本>``
    会把该文本**原样**写入 ``auth.callback.rejected`` 的 ``reason``，
    与 ``core/audit.py`` 宣称的「封闭事件集 / 白名单字段」口径冲突
    （外部可借审计伪造事件语义）。

    修复口径：未命中封闭集合的取值统一记为 ``unrecognized_failure``；
    RFC 6749 §4.1.2.1 标准错误码与本仓自有失败标记**原样保留**。
    """
    from fastapi.testclient import TestClient

    from gw.api.app import create_app

    clean_env.setenv(gw_config.AUTH_MODE_ENV, gw_config.AUTH_MODE_OIDC)
    gw_config.reset_runtime_auth_config_cache()
    audit_log.reset_audit_log()

    client = TestClient(create_app())
    response = client.get("/api/asset-auth/callback?error=password_reset_completed_by_admin")
    assert response.status_code in (200, 302)

    injected = "password_reset_completed_by_admin"
    events = _events(audit_log.EVENT_CALLBACK_REJECTED)
    assert events, "回调失败必须留痕"
    assert events[-1]["reason"] == "unrecognized_failure", (
        "请求方控制的文本不得原样入账，实际: " + repr(events[-1]["reason"])
    )
    assert injected not in json.dumps(audit_log.list_auth_events(), ensure_ascii=False), (
        "审计不得保留请求方注入的任意文本"
    )


def test_callback_standard_error_code_is_preserved(clean_env):
    """RFC 6749 §4.1.2.1 标准错误码必须原样保留，不能被收敛成兜底标记。"""
    from fastapi.testclient import TestClient

    from gw.api.app import create_app

    clean_env.setenv(gw_config.AUTH_MODE_ENV, gw_config.AUTH_MODE_OIDC)
    gw_config.reset_runtime_auth_config_cache()
    audit_log.reset_audit_log()

    client = TestClient(create_app())
    response = client.get("/api/asset-auth/callback?error=access_denied")
    assert response.status_code in (200, 302)

    events = _events(audit_log.EVENT_CALLBACK_REJECTED)
    assert events, "回调失败必须留痕"
    assert events[-1]["reason"] == "access_denied", (
        "标准错误码应原样保留，实际: " + repr(events[-1]["reason"])
    )


def test_callback_internal_failure_marker_is_preserved(clean_env, local_idp):
    """本仓自有流程失败标记（state_mismatch 等）必须原样保留。"""
    from fastapi.testclient import TestClient

    from gw.api.app import create_app

    gw_config.reset_runtime_auth_config_cache()
    audit_log.reset_audit_log()

    client = TestClient(create_app())
    # 无流程 Cookie -> state_mismatch（本仓自有标记，须保留）。
    response = client.get("/api/asset-auth/callback?code=abc&state=def")
    assert response.status_code in (200, 302)

    events = _events(audit_log.EVENT_CALLBACK_REJECTED)
    assert events, "回调失败必须留痕"
    assert events[-1]["reason"] == "state_mismatch", (
        "本仓自有失败标记应原样保留，实际: " + repr(events[-1]["reason"])
    )
