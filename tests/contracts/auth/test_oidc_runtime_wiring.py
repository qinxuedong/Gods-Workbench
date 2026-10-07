# -*- coding: utf-8 -*-
"""外部 IdP（OIDC）运行期接线契约测试。

覆盖点：
1. 默认 ``GW_AUTH_MODE`` 未设置时使用 ``local_account`` 本地账户会话；
2. ``GW_AUTH_MODE=oidc`` 且配置缺失/非法时**失败关闭**（401），不回落到本地信任；
3. ``GW_AUTH_MODE=oidc`` 且配置齐全时，角色只来自 IdP 组声明，``X-User-Role`` 被忽略；
4. JWKS 端点 scheme 白名单（仅 HTTPS 或本地回环）；
5. 未识别模式一律失败关闭；
6. **真实 HTTP E2E**：起一个本地 OIDC IdP（HTTPS 由 ``http://127.0.0.1`` 回环例外覆盖），
   通过 ``GW_OIDC_ISSUER`` discovery 自动解析 ``jwks_uri``，走完整
   ``FastAPI -> require_edit_access -> verify_jwt -> JWKS`` 链路；
7. JWKS 拉取的 TTL 缓存**跨请求**生效（同一进程内不重复打 IdP）。

证据边界：本文件只证明本地接线、失败关闭语义与真实 HTTP 链路，
不代表已接入生产 IdP、生产就绪或发布授权。
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

import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from fastapi.testclient import TestClient

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
SRC_PATH = REPO_ROOT
if str(SRC_PATH) not in sys.path:
    sys.path.insert(0, str(SRC_PATH))

from gw.core import config as gw_config  # noqa: E402
from gw.core.errors import ForbiddenException, UnauthorizedException  # noqa: E402

ISSUER = "https://idp.example.test"
AUDIENCE = "gods-workbench"
KID = "wire-key-1"


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


def _make_token(private_key, claims: dict) -> str:
    header = {"alg": "RS256", "typ": "JWT", "kid": KID}
    header_segment = _b64url(json.dumps(header, separators=(",", ":")).encode("utf-8"))
    payload_segment = _b64url(json.dumps(claims, separators=(",", ":")).encode("utf-8"))
    signing_input = f"{header_segment}.{payload_segment}".encode("ascii")
    signature = private_key.sign(signing_input, padding.PKCS1v15(), hashes.SHA256())
    return f"{header_segment}.{payload_segment}.{_b64url(signature)}"


def _claims(issuer: str = ISSUER, audience: str = AUDIENCE, **overrides) -> dict:
    now = time.time()
    base = {
        "iss": issuer,
        "aud": audience,
        "sub": "user-0001",
        "iat": now - 10,
        "exp": now + 300,
        "groups": ["gw-editor"],
    }
    base.update(overrides)
    return base


@pytest.fixture()
def clean_env(monkeypatch, tmp_path):
    """清空所有 GW_* 环境变量并重置运行期缓存，保证用例之间互不污染。"""
    for name in list(os.environ):
        if name.startswith("GW_"):
            monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("GW_RUNTIME_MODE", "test")
    monkeypatch.setenv("GW_DATA_DIR", str(tmp_path / "gw-data"))
    monkeypatch.setenv("GW_LOCAL_AUTH_DB", str(tmp_path / "gw-auth" / "auth.sqlite3"))
    monkeypatch.setenv("GW_VIDEO_DATA_DIR", str(tmp_path / "gw-video"))
    gw_config.reset_runtime_auth_config_cache()
    yield monkeypatch
    gw_config.reset_runtime_auth_config_cache()


# ---------------------------------------------------------------------------
# 基础语义
# ---------------------------------------------------------------------------

def test_default_mode_is_local_account(clean_env):
    """未设置 GW_AUTH_MODE 时使用本地账户模式。"""
    assert gw_config.resolve_auth_mode() == gw_config.AUTH_MODE_LOCAL_ACCOUNT
    runtime = gw_config.load_runtime_auth_config()
    assert runtime.mode == gw_config.AUTH_MODE_LOCAL_ACCOUNT
    assert runtime.ready is False


def test_unknown_mode_fails_closed(clean_env):
    """未识别的模式取值一律按 oidc 处理并失败关闭，不放行请求。"""
    clean_env.setenv(gw_config.AUTH_MODE_ENV, "totally-bogus")
    assert gw_config.resolve_auth_mode() == gw_config.AUTH_MODE_OIDC
    runtime = gw_config.load_runtime_auth_config()
    assert runtime.ready is False


def test_oidc_mode_without_issuer_fails_closed(clean_env):
    """启用 oidc 但缺少 issuer/audience 时，运行时配置不可用。"""
    clean_env.setenv(gw_config.AUTH_MODE_ENV, gw_config.AUTH_MODE_OIDC)
    clean_env.setenv(gw_config.AUDIENCE_ENV, AUDIENCE)
    clean_env.setenv(gw_config.JWKS_URL_ENV, "https://idp.example.test/jwks.json")
    runtime = gw_config.load_runtime_auth_config()
    assert runtime.ready is False
    assert "缺失" in runtime.reason


def test_oidc_jwks_url_scheme_whitelist(clean_env):
    """JWKS 端点只允许 HTTPS 或本地回环 http。"""
    assert gw_config.is_allowed_jwks_url("https://idp.example.test/jwks.json") is True
    assert gw_config.is_allowed_jwks_url("http://127.0.0.1:9999/jwks.json") is True
    assert gw_config.is_allowed_jwks_url("http://idp.example.test/jwks.json") is False
    assert gw_config.is_allowed_jwks_url("file:///etc/passwd") is False
    assert gw_config.is_allowed_jwks_url("") is False


def test_discovery_unreachable_fails_closed(clean_env):
    """缺省 JWKS URL 时走 discovery；discovery 不可达必须失败关闭，不得放行。"""
    clean_env.setenv(gw_config.AUTH_MODE_ENV, gw_config.AUTH_MODE_OIDC)
    clean_env.setenv(gw_config.ISSUER_ENV, "http://127.0.0.1:9/oidc")
    clean_env.setenv(gw_config.AUDIENCE_ENV, AUDIENCE)
    runtime = gw_config.load_runtime_auth_config()
    assert runtime.ready is False
    assert runtime.reason


def test_oidc_mode_with_full_config_is_ready(clean_env, jwks):
    """配置齐全时运行时配置可用，且不走本地信任。"""
    clean_env.setenv(gw_config.AUTH_MODE_ENV, gw_config.AUTH_MODE_OIDC)
    clean_env.setenv(gw_config.ISSUER_ENV, ISSUER)
    clean_env.setenv(gw_config.AUDIENCE_ENV, AUDIENCE)
    clean_env.setenv(gw_config.JWKS_URL_ENV, "https://idp.example.test/jwks.json")
    runtime = gw_config.load_runtime_auth_config()
    assert runtime.ready is True
    assert runtime.oidc.enabled is True
    assert runtime.oidc.issuer == ISSUER
    assert runtime.oidc.audience == AUDIENCE
    assert runtime.jwks_url == "https://idp.example.test/jwks.json"


def test_oidc_mode_ignores_user_role_header(clean_env, rsa_keypair, jwks, monkeypatch):
    """oidc 模式下角色只来自 IdP 声明；请求头 X-User-Role 不得提权。"""
    from gw.core import auth as gw_auth

    private_key, _ = rsa_keypair
    clean_env.setenv(gw_config.AUTH_MODE_ENV, gw_config.AUTH_MODE_OIDC)
    clean_env.setenv(gw_config.ISSUER_ENV, ISSUER)
    clean_env.setenv(gw_config.AUDIENCE_ENV, AUDIENCE)
    clean_env.setenv(gw_config.JWKS_URL_ENV, "https://idp.example.test/jwks.json")

    monkeypatch.setattr(gw_config, "fetch_jwks", lambda _config: jwks)

    token = _make_token(private_key, _claims(groups=["gw-readonly"]))
    # 请求头声称 admin，但 IdP 只授予 readonly -> 必须拒绝写操作。
    context = gw_auth.require_authenticated(f"Bearer {token}", "admin")
    assert context.role == "readonly"
    assert context.mode == gw_config.AUTH_MODE_OIDC
    with pytest.raises(ForbiddenException):
        gw_auth.require_edit_access(f"Bearer {token}", "admin")


def test_oidc_mode_rejects_invalid_token(clean_env, monkeypatch):
    """oidc 模式下无效令牌一律 401，且不回落到本地行为。"""
    from gw.core import auth as gw_auth

    clean_env.setenv(gw_config.AUTH_MODE_ENV, gw_config.AUTH_MODE_OIDC)
    clean_env.setenv(gw_config.ISSUER_ENV, ISSUER)
    clean_env.setenv(gw_config.AUDIENCE_ENV, AUDIENCE)
    clean_env.setenv(gw_config.JWKS_URL_ENV, "https://idp.example.test/jwks.json")
    with pytest.raises(UnauthorizedException):
        gw_auth.require_authenticated("Bearer not-a-real-token", "admin")


def test_oidc_mode_rejects_missing_config_before_token_check(clean_env):
    """配置缺失时即使令牌格式合法也直接 401。"""
    from gw.core import auth as gw_auth

    clean_env.setenv(gw_config.AUTH_MODE_ENV, gw_config.AUTH_MODE_OIDC)
    with pytest.raises(UnauthorizedException):
        gw_auth.require_authenticated("Bearer anything", "admin")


def test_jwks_fetcher_cache_is_reused_within_ttl(clean_env, jwks, monkeypatch):
    """同一 fetcher 在 TTL 内多次调用只打一次网络。"""
    calls = {"n": 0}

    def counting_fetch(_config):
        calls["n"] += 1
        return jwks

    monkeypatch.setattr(gw_config, "fetch_jwks", counting_fetch)
    fetcher = gw_config.build_jwks_fetcher(gw_config.JwksEndpointConfig(url="https://idp.example.test/jwks.json"))
    for _ in range(4):
        fetcher()
    assert calls["n"] == 1


def test_runtime_config_cache_reuses_oidc_config(clean_env, jwks, monkeypatch):
    """运行期配置缓存必须让 JWKS TTL 缓存跨请求生效（不得每请求新建）。"""
    calls = {"n": 0}

    def counting_fetch(_config):
        calls["n"] += 1
        return jwks

    monkeypatch.setattr(gw_config, "fetch_jwks", counting_fetch)
    clean_env.setenv(gw_config.AUTH_MODE_ENV, gw_config.AUTH_MODE_OIDC)
    clean_env.setenv(gw_config.ISSUER_ENV, ISSUER)
    clean_env.setenv(gw_config.AUDIENCE_ENV, AUDIENCE)
    clean_env.setenv(gw_config.JWKS_URL_ENV, "https://idp.example.test/jwks.json")

    for _ in range(5):
        runtime = gw_config.load_runtime_auth_config()
        runtime.oidc.jwks_fetcher()
    assert calls["n"] == 1


def test_runtime_config_cache_invalidated_by_env_change(clean_env, jwks, monkeypatch):
    """环境变量变化后缓存必须失效并重建配置。"""
    monkeypatch.setattr(gw_config, "fetch_jwks", lambda _config: jwks)
    clean_env.setenv(gw_config.AUTH_MODE_ENV, gw_config.AUTH_MODE_OIDC)
    clean_env.setenv(gw_config.ISSUER_ENV, ISSUER)
    clean_env.setenv(gw_config.AUDIENCE_ENV, AUDIENCE)
    clean_env.setenv(gw_config.JWKS_URL_ENV, "https://idp.example.test/jwks.json")
    first = gw_config.load_runtime_auth_config()
    clean_env.setenv(gw_config.LEEWAY_ENV, "5")
    second = gw_config.load_runtime_auth_config()
    assert first.oidc.leeway_seconds == 60
    assert second.oidc.leeway_seconds == 5


# ---------------------------------------------------------------------------
# 真实 HTTP E2E：本地 OIDC IdP（discovery + JWKS）+ FastAPI 全链路
# ---------------------------------------------------------------------------

@pytest.fixture()
def isolated_projects_service():
    """隔离全局项目服务状态，避免真实 E2E 写入污染其他用例的黄金夹具断言。"""
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
    """起一个真实 HTTP 的本地 OIDC IdP，提供 discovery 与 JWKS 两个端点。"""
    _, public_key = rsa_keypair
    counters = {"discovery": 0, "jwks": 0}
    issuer_holder = {}

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
            if self.path == "/.well-known/openid-configuration":
                counters["discovery"] += 1
                self._send_json(
                    {
                        "issuer": issuer_holder["issuer"],
                        "jwks_uri": issuer_holder["issuer"] + "/jwks",
                        "response_types_supported": ["code"],
                        "subject_types_supported": ["public"],
                        "id_token_signing_alg_values_supported": ["RS256"],
                    }
                )
                return
            if self.path == "/jwks":
                counters["jwks"] += 1
                self._send_json(jwks)
                return
            self.send_response(404)
            self.send_header("Content-Length", "0")
            self.end_headers()

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    port = server.server_address[1]
    issuer_holder["issuer"] = f"http://127.0.0.1:{port}"
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield {"issuer": issuer_holder["issuer"], "counters": counters}
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_real_http_idp_end_to_end(clean_env, local_idp, rsa_keypair, isolated_projects_service):
    """真实 HTTP：discovery 自动解析 jwks_uri -> JWKS -> FastAPI 写端点鉴权。

    断言：
    - 合法 editor 令牌可完成写操作（201）；
    - 请求头 ``X-User-Role: governor`` 不能提权（IdP 只给 editor）；
    - IdP 令牌为 readonly 时写操作 403；
    - 无效令牌 401；
    - discovery 只拉一次、JWKS 只拉一次（TTL 缓存跨请求生效）。
    """
    from gw.api.app import create_app

    private_key, _ = rsa_keypair
    issuer = local_idp["issuer"]
    clean_env.setenv(gw_config.AUTH_MODE_ENV, gw_config.AUTH_MODE_OIDC)
    clean_env.setenv(gw_config.ISSUER_ENV, issuer)
    clean_env.setenv(gw_config.AUDIENCE_ENV, AUDIENCE)
    # 故意不设置 GW_OIDC_JWKS_URL，强制走 discovery。
    clean_env.delenv(gw_config.JWKS_URL_ENV, raising=False)
    gw_config.reset_runtime_auth_config_cache()

    with TestClient(create_app()) as api:
        # /healthz 如实暴露模式与就绪状态
        health = api.get("/healthz").json()
        assert health["auth_mode"] == "oidc"
        assert health["oidc_ready"] is True
        assert health["release_authorized"] is False

        editor_token = _make_token(private_key, _claims(issuer=issuer, groups=["gw-editor"]))
        created = api.post(
            "/api/asset-registry/projects",
            json={"name": "真实 IdP E2E 项目", "project_type": "film", "description": "由本地 OIDC IdP 令牌创建"},
            headers={"Authorization": f"Bearer {editor_token}", "X-User-Role": "governor"},
        )
        assert created.status_code == 201, created.text

        readonly_token = _make_token(private_key, _claims(issuer=issuer, groups=["gw-readonly"]))
        forbidden = api.post(
            "/api/asset-registry/projects",
            json={"name": "只读令牌不应写入", "project_type": "film"},
            headers={"Authorization": f"Bearer {readonly_token}", "X-User-Role": "governor"},
        )
        assert forbidden.status_code == 403, forbidden.text

        unauthorized = api.post(
            "/api/asset-registry/projects",
            json={"name": "无效令牌不应写入", "project_type": "film"},
            headers={"Authorization": "Bearer not-a-real-token", "X-User-Role": "governor"},
        )
        assert unauthorized.status_code == 401, unauthorized.text

    assert local_idp["counters"]["discovery"] >= 1
    assert local_idp["counters"]["jwks"] == 1, local_idp["counters"]


def test_real_http_idp_role_mapping_rejects_unknown_group(clean_env, local_idp, rsa_keypair, isolated_projects_service):
    """真实 HTTP：IdP 未映射组必须 401（无组不授权，不得静默降级）。"""
    from gw.api.app import create_app

    private_key, _ = rsa_keypair
    issuer = local_idp["issuer"]
    clean_env.setenv(gw_config.AUTH_MODE_ENV, gw_config.AUTH_MODE_OIDC)
    clean_env.setenv(gw_config.ISSUER_ENV, issuer)
    clean_env.setenv(gw_config.AUDIENCE_ENV, AUDIENCE)
    clean_env.delenv(gw_config.JWKS_URL_ENV, raising=False)
    gw_config.reset_runtime_auth_config_cache()

    token = _make_token(private_key, _claims(issuer=issuer, groups=["unmapped-group"]))
    with TestClient(create_app()) as api:
        response = api.post(
            "/api/asset-registry/projects",
            json={"name": "未映射组不应写入", "project_type": "film"},
            headers={"Authorization": f"Bearer {token}"},
        )
    assert response.status_code == 401, response.text


# ---------------------------------------------------------------------------
# D5/D6/D7 回归：重定向白名单、失败自愈、密钥轮换
# ---------------------------------------------------------------------------


def test_redirect_to_non_whitelisted_host_is_rejected(clean_env):
    """D5：首跳合法但 302 指向白名单外主机时必须失败关闭，不得自动跟随。"""
    payload = {"keys": [{"kty": "RSA", "kid": "bypass", "n": "AA", "e": "AQAB"}]}
    state = {"inner_hits": 0, "target": ""}

    class _Inner(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *args):  # noqa: D102
            return

        def do_GET(self):  # noqa: N802
            state["inner_hits"] += 1
            body = json.dumps(payload).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    class _Outer(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *args):  # noqa: D102
            return

        def do_GET(self):  # noqa: N802
            self.send_response(302)
            self.send_header("Location", state["target"])
            self.send_header("Content-Length", "0")
            self.end_headers()

    # 绑定 0.0.0.0 使其在 0.0.0.0:PORT 上真实可达：这样若代码真的跟随了重定向，
    # 白名单外目标一定会被命中（否则用例会因「连不上」而假通过）。
    inner = ThreadingHTTPServer(("0.0.0.0", 0), _Inner)
    state["target"] = f"http://0.0.0.0:{inner.server_address[1]}/jwks.json"
    outer = ThreadingHTTPServer(("127.0.0.1", 0), _Outer)
    for server in (inner, outer):
        threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        start_url = f"http://127.0.0.1:{outer.server_address[1]}/jwks.json"
        assert gw_config.is_allowed_jwks_url(start_url) is True
        assert gw_config.is_allowed_jwks_url(state["target"]) is False
        with pytest.raises(Exception):
            gw_config.fetch_jwks(gw_config.JwksEndpointConfig(url=start_url, timeout_seconds=5))
        assert state["inner_hits"] == 0, "白名单外目标不得被访问（禁止自动跟随重定向）"
    finally:
        for server in (inner, outer):
            server.shutdown()
            server.server_close()


def test_discovery_failure_recovers_without_process_restart(clean_env, monkeypatch):
    """D6：discovery 瞬时失败不得被永久固化；恢复后同一进程内应重新可用。"""
    monkeypatch.setattr(gw_config, "NEGATIVE_CACHE_SECONDS", 0)
    clean_env.setenv(gw_config.AUTH_MODE_ENV, gw_config.AUTH_MODE_OIDC)
    clean_env.setenv(gw_config.ISSUER_ENV, "http://127.0.0.1:9/oidc")
    clean_env.setenv(gw_config.AUDIENCE_ENV, AUDIENCE)
    first = gw_config.load_runtime_auth_config()
    assert first.ready is False

    monkeypatch.setattr(
        gw_config, "resolve_jwks_url",
        lambda issuer, explicit="": "https://idp.example.test/jwks.json",
    )
    second = gw_config.load_runtime_auth_config()
    assert second.ready is True, "瞬时 discovery 故障后应自愈，无需重启进程"


def test_unknown_kid_forces_refresh_bypassing_ttl(clean_env, monkeypatch, jwks):
    """D7：TTL 窗口内出现未知 kid（密钥轮换）时，受控刷新必须绕过 TTL。"""
    calls = {"n": 0}

    def counting_fetch(_config):
        calls["n"] += 1
        return jwks

    monkeypatch.setattr(gw_config, "fetch_jwks", counting_fetch)
    fetcher = gw_config.build_jwks_fetcher(
        gw_config.JwksEndpointConfig(url="https://idp.example.test/jwks.json", cache_seconds=300)
    )
    first = fetcher()
    assert calls["n"] == 1
    # TTL 内常规读取仍命中缓存……
    assert fetcher() is first
    assert calls["n"] == 1
    # ……但未知 kid 触发的受控刷新必须真正外呼。
    force_refresh = getattr(fetcher, "force_refresh", None)
    assert callable(force_refresh)
    force_refresh()
    assert calls["n"] == 2


def test_verify_jwt_accepts_rotated_kid_within_ttl_window(clean_env, monkeypatch, rsa_keypair):
    """D7（端到端）：未知 kid 的令牌应触发受控刷新并被接受，而非被 TTL 缓存误拒。"""
    from gw.core import oidc as gw_oidc

    private_key, public_key = rsa_keypair
    numbers = public_key.public_numbers()
    rotated_kid = "rotated-key-2"
    old_doc = {"keys": [{"kty": "RSA", "use": "sig", "alg": "RS256", "kid": "old-key", "n": "AA", "e": "AQAB"}]}
    rotated_doc = {
        "keys": [
            {
                "kty": "RSA",
                "use": "sig",
                "alg": "RS256",
                "kid": rotated_kid,
                "n": _b64url(numbers.n.to_bytes((numbers.n.bit_length() + 7) // 8, "big")),
                "e": _b64url(numbers.e.to_bytes((numbers.e.bit_length() + 7) // 8, "big")),
            }
        ]
    }
    state = {"phase": "old", "calls": 0}

    def switching_fetch(_config):
        state["calls"] += 1
        return rotated_doc if state["phase"] == "rotated" else old_doc

    clean_env.setenv(gw_config.AUTH_MODE_ENV, gw_config.AUTH_MODE_OIDC)
    clean_env.setenv(gw_config.ISSUER_ENV, ISSUER)
    clean_env.setenv(gw_config.AUDIENCE_ENV, AUDIENCE)
    clean_env.setenv(gw_config.JWKS_URL_ENV, "https://idp.example.test/jwks.json")

    monkeypatch.setattr(gw_config, "fetch_jwks", switching_fetch)
    runtime = gw_config.load_runtime_auth_config()
    assert runtime.ready is True
    # 先用旧文档填满 TTL 缓存（不含 rotated_kid）
    assert gw_oidc._find_jwk(runtime.oidc.jwks_fetcher(), rotated_kid) is None
    assert state["calls"] == 1

    # 模拟 IdP 完成密钥轮换
    state["phase"] = "rotated"
    header = {"alg": "RS256", "typ": "JWT", "kid": rotated_kid}
    header_segment = _b64url(json.dumps(header, separators=(",", ":")).encode("utf-8"))
    payload_segment = _b64url(json.dumps(_claims(), separators=(",", ":")).encode("utf-8"))
    signing_input = f"{header_segment}.{payload_segment}".encode("ascii")
    signature = private_key.sign(signing_input, padding.PKCS1v15(), hashes.SHA256())
    rotated_token = f"{header_segment}.{payload_segment}.{_b64url(signature)}"

    identity = gw_oidc.verify_jwt(rotated_token, runtime.oidc)
    assert identity.subject == "user-0001"
    assert identity.role == "editor"
    assert state["calls"] == 2, "未知 kid 必须触发一次绕过 TTL 的受控刷新"


def test_negative_cache_default_is_bounded(clean_env):
    """O1：负缓存出厂值必须有界，避免瞬时故障被长时间固化成不可用。"""
    assert isinstance(gw_config.NEGATIVE_CACHE_SECONDS, int)
    assert 0 < gw_config.NEGATIVE_CACHE_SECONDS <= 60, (
        f"负缓存出厂值应为一个短且有界的秒数，实际为 {gw_config.NEGATIVE_CACHE_SECONDS}"
    )
    assert isinstance(gw_config.FORCE_REFRESH_MIN_SECONDS, int)
    assert gw_config.FORCE_REFRESH_MIN_SECONDS > 0, "强刷限流窗口必须为正，避免被随机 kid 触发自 DoS"


def test_redirect_within_whitelist_is_followed(clean_env):
    """O2：**同源**重定向（同一 IdP 换路径）应被允许，只拒绝越界或异源跳转。

    R6-8 更正：原用例把「同源」误解为「同一白名单内」，用**不同端口**的第二台
    服务器验证跟随，这恰好放行了「合法端点 302 到异源主机」的攻击路径。
    现改为在**同一台**服务器内从 /jwks 302 到 /keys（真正同源）。
    """
    payload = {"keys": [{"kty": "RSA", "kid": "ok", "n": "AA", "e": "AQAB"}]}
    state = {"jwks_hits": 0, "keys_hits": 0}

    class _SameOrigin(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *args):  # noqa: D102
            return

        def do_GET(self):  # noqa: N802
            if self.path == "/jwks":
                state["jwks_hits"] += 1
                self.send_response(302)
                self.send_header("Location", "/keys")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            state["keys_hits"] += 1
            body = json.dumps(payload).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), _SameOrigin)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        base = f"http://127.0.0.1:{server.server_address[1]}"
        doc = gw_config.fetch_jwks(gw_config.JwksEndpointConfig(url=f"{base}/jwks", timeout_seconds=5))
        assert [k.get("kid") for k in doc["keys"]] == ["ok"]
        assert state["jwks_hits"] == 1
        assert state["keys_hits"] == 1, "同源换路径的重定向应被跟随"
    finally:
        server.shutdown()
        server.server_close()


def test_redirect_to_foreign_origin_is_blocked(clean_env):
    """R6-8：合法端点的 302 不得把 JWKS 拉到**异源**主机（否则可用攻击者公钥伪造令牌）。"""
    payload = {"keys": [{"kty": "RSA", "kid": "ATTACKER-KEY", "n": "AA", "e": "AQAB"}]}
    state = {"attacker_hits": 0, "target": ""}

    class _Attacker(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *args):  # noqa: D102
            return

        def do_GET(self):  # noqa: N802
            state["attacker_hits"] += 1
            body = json.dumps(payload).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    class _Redirector(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *args):  # noqa: D102
            return

        def do_GET(self):  # noqa: N802
            self.send_response(302)
            self.send_header("Location", state["target"])
            self.send_header("Content-Length", "0")
            self.end_headers()

    attacker = ThreadingHTTPServer(("127.0.0.1", 0), _Attacker)
    state["target"] = f"http://127.0.0.1:{attacker.server_address[1]}/jwks.json"
    redirector = ThreadingHTTPServer(("127.0.0.1", 0), _Redirector)
    for server in (attacker, redirector):
        threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        start_url = f"http://127.0.0.1:{redirector.server_address[1]}/jwks.json"
        # 两个 URL 都各自「在允许范围内」，只有同源锁能区分它们。
        assert gw_config.is_allowed_jwks_url(start_url) is True
        assert gw_config.is_allowed_jwks_url(state["target"]) is True
        assert gw_config.is_same_origin_as(state["target"], start_url) is False
        with pytest.raises(Exception):
            gw_config.fetch_jwks(gw_config.JwksEndpointConfig(url=start_url, timeout_seconds=5))
        assert state["attacker_hits"] == 0, "异源 302 目标不得被访问（否则可用攻击者公钥验签）"
    finally:
        for server in (attacker, redirector):
            server.shutdown()
            server.server_close()
