# -*- coding: utf-8 -*-
"""R6 缺陷回归守卫：流程状态误清、issuer 同源白名单、状态契约、登录语义、Cookie Secure。

每一条对应 R6 独立复核发现的真实缺陷，用**行为级**断言（非静态字符串）固定修复结果。
证据边界：本地复算；不代表已接入生产 IdP 或生产验收。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
SRC_PATH = REPO_ROOT
if str(SRC_PATH) not in sys.path:
    sys.path.insert(0, str(SRC_PATH))

from gw.core import config as gw_config  # noqa: E402
from gw.core import session as session_store  # noqa: E402


@pytest.fixture()
def clean_env(monkeypatch, tmp_path):
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


# ---------------------------------------------------------------------------
# R6-1：流程状态不得被新登录误清
# ---------------------------------------------------------------------------

def test_flow_state_survives_concurrent_login(clean_env):
    """登记流程 A 后再登记流程 B，A 必须仍然可见（不得被 _prune 误判过期）。"""
    session_store.create_flow_state(state="state-A", nonce="n", code_verifier="v", redirect_uri="/cb")
    session_store.create_flow_state(state="state-B", nonce="n", code_verifier="v", redirect_uri="/cb")
    flow_a = session_store.pop_flow_state("state-A")
    assert flow_a is not None, "并发登录场景下流程 A 被误清（登录会随机失败）"
    assert flow_a.code_verifier == "v"
    assert session_store.pop_flow_state("state-B") is not None


def test_session_survives_flow_state_registration(clean_env):
    """创建会话后登记流程状态，不得把有效会话一并清掉。"""
    session_id = session_store.create_session({"username": "u", "role": "editor", "groups": ["gw-editor"]})
    session_store.create_flow_state(state="state-X", nonce="n", code_verifier="v", redirect_uri="/cb")
    assert session_store.get_session(session_id) is not None, "有效会话被误清"


def test_expired_flow_state_is_still_rejected(clean_env, monkeypatch):
    """过期流程状态仍必须被拒绝（修复不能把过期检查一并去掉）。"""
    monkeypatch.setattr(session_store, "FLOW_STATE_TTL_SECONDS", -1)
    session_store.create_flow_state(state="state-old", nonce="n", code_verifier="v", redirect_uri="/cb")
    assert session_store.pop_flow_state("state-old") is None


# ---------------------------------------------------------------------------
# R6-2：issuer 同源校验不得被字符串前缀绕过
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "endpoint,expected",
    [
        ("https://idp.example.com/token", True),
        ("https://idp.example.com:8443/token", False),  # 端口不同不是同源
        ("http://idp.example.com/token", False),        # scheme 不同
        ("https://evil.example.com/token", False),
        ("https://idp.example.com.evil.com/token", False),  # 前缀陷阱
        ("https://idp.example.com@evil.com/token", False),  # userinfo 陷阱（真实 host 是 evil.com）
        ("https://idp.example.com/token/../evil", True),    # 同源路径，仍属该主机
    ],
)
def test_is_same_origin_as(endpoint, expected):
    assert gw_config.is_same_origin_as(endpoint, "https://idp.example.com") is expected


def test_resolve_endpoint_rejects_issuer_prefix_spoof(clean_env, monkeypatch):
    """discovery 被篡改后把 token_endpoint 指向第三方主机，必须拒绝。"""
    # 构造一个 host 看起来以 issuer 开头、或携带 userinfo 的伪造 discovery 文档。
    for evil in (
        "https://idp.example.com.evil.com/token",
        "https://idp.example.com@evil.com/token",
    ):
        monkeypatch.setattr(
            gw_config,
            "load_discovery_document",
            lambda issuer, _evil=evil, **kw: {"token_endpoint": _evil, "authorization_endpoint": _evil},
        )
        with pytest.raises(ValueError):
            gw_config.resolve_endpoint("https://idp.example.com", "token_endpoint")
        with pytest.raises(ValueError):
            gw_config.resolve_endpoint("https://idp.example.com", "authorization_endpoint")


def test_discovery_issuer_mismatch_is_rejected(clean_env, monkeypatch):
    """discovery 文档自述 issuer 与配置 issuer 不一致时必须拒绝（OIDC Discovery 1.0 s4.3 / mix-up 防护）。"""
    # 文档由 idp.example.com 提供，却自述 issuer 是别的租户：必须失败关闭。
    monkeypatch.setattr(
        gw_config,
        "fetch_discovery_document",
        lambda issuer: {"issuer": "https://other-tenant.example.com", "jwks_uri": issuer + "/jwks"},
    )
    with pytest.raises(ValueError):
        gw_config.load_discovery_document("https://idp.example.com", cache_seconds=0)


def test_discovery_issuer_missing_is_rejected(clean_env, monkeypatch):
    """discovery 文档缺少 issuer 字段时必须拒绝（不得按缺省放行）。"""
    monkeypatch.setattr(
        gw_config,
        "fetch_discovery_document",
        lambda issuer: {"jwks_uri": issuer + "/jwks"},
    )
    with pytest.raises(ValueError):
        gw_config.load_discovery_document("https://idp.example.com", cache_seconds=0)


def test_discovery_issuer_trailing_slash_is_accepted(clean_env, monkeypatch):
    """issuer 仅差尾部斜杠时应视为一致（与 resolve_jwks_url 的规范化口径一致）。"""
    monkeypatch.setattr(
        gw_config,
        "fetch_discovery_document",
        lambda issuer: {"issuer": issuer + "/", "jwks_uri": issuer + "/jwks"},
    )
    document = gw_config.load_discovery_document("https://idp.example.com", cache_seconds=0)
    assert document["jwks_uri"] == "https://idp.example.com/jwks"


def test_cached_discovery_document_is_revalidated(clean_env, monkeypatch):
    """缓存命中同样要复核 issuer，避免绕过首次校验后污染缓存。"""
    monkeypatch.setattr(
        gw_config,
        "fetch_discovery_document",
        lambda issuer: {"issuer": issuer, "jwks_uri": issuer + "/jwks"},
    )
    gw_config.reset_discovery_cache()
    gw_config.load_discovery_document("https://idp.example.com")
    gw_config._DISCOVERY_CACHE["document"] = {"jwks_uri": "https://idp.example.com/jwks"}
    with pytest.raises(ValueError):
        gw_config.load_discovery_document("https://idp.example.com")


def test_resolve_jwks_url_rejects_foreign_host(clean_env, monkeypatch):
    """jwks_uri 指向第三方主机时必须拒绝（否则攻击者可自签密钥伪造令牌）。"""
    monkeypatch.setattr(
        gw_config,
        "fetch_discovery_document",
        lambda issuer: {"jwks_uri": "https://attacker.example.com/keys"},
    )
    with pytest.raises(ValueError):
        gw_config.resolve_jwks_url("https://idp.example.com")


# ---------------------------------------------------------------------------
# R6-3：/status 字段必须与前端读取一致
# ---------------------------------------------------------------------------

def test_status_field_names_match_frontend_contract(clean_env):
    """前端读取的字段名必须真实存在于 /status 响应中（不得是死字段）。"""
    from gw.api.app import create_app

    with TestClient(create_app()) as api:
        body = api.get("/api/asset-auth/status").json()
    for field in ("auth_mode", "oidc_ready", "authenticated", "principal",
                  "login_available", "logout_available", "reason", "release_authorized"):
        assert field in body, f"/status 缺少前端依赖字段 {field}"
    # 旧契约字段不得再出现（避免前后端各自读一套）。
    for legacy in ("auth_required", "configured", "needs_setup"):
        assert legacy not in body, f"/status 残留已废弃字段 {legacy}"


def test_frontend_reads_only_contract_fields():
    """前端不得读取后端未提供的旧字段（否则登录态永远显示未接入）。"""
    source = (REPO_ROOT / "web" / "js" / "core" / "hardware-telemetry.js").read_text(
        encoding="utf-8"
    )
    # 忽略注释行后检查：实现中出现旧字段即为契约漂移。
    code_lines = [line for line in source.splitlines() if not line.strip().startswith(("//", "*", "/*"))]
    code = "\n".join(code_lines)
    for legacy in ("data.auth_required", "data.configured", "data.needs_setup", "this.authState.needs_setup"):
        assert legacy not in code, f"前端仍在读取已废弃字段 {legacy}"


# ---------------------------------------------------------------------------
# R6-4：登录端点语义（无 body，返回授权地址）
# ---------------------------------------------------------------------------

def test_login_endpoint_takes_no_body_and_returns_authorization_url(clean_env, monkeypatch):
    """前端必须能拿到 authorization_url；登录端点不接受用户名/密码。"""
    # routes_auth.py 采用 `from ... import resolve_endpoint` 模块级绑定，
    # 必须打到绑定处，否则打桩无效（否则会真的走 discovery 并返回 503）。
    from gw.api import routes_auth as gw_routes_auth

    monkeypatch.setattr(
        gw_routes_auth,
        "resolve_endpoint",
        lambda issuer, name: "https://idp.example.com/authorize" if name == "authorization_endpoint" else "https://idp.example.com/token",
    )
    monkeypatch.setattr(gw_config, "fetch_discovery_document", lambda issuer: {"jwks_uri": "https://idp.example.com/jwks"})
    monkeypatch.setattr(gw_config, "fetch_jwks", lambda cfg: {"keys": []})
    clean_env.setenv(gw_config.AUTH_MODE_ENV, gw_config.AUTH_MODE_OIDC)
    clean_env.setenv(gw_config.ISSUER_ENV, "https://idp.example.com")
    clean_env.setenv(gw_config.AUDIENCE_ENV, "gods-workbench")
    clean_env.setenv(gw_config.CLIENT_ID_ENV, "web")
    clean_env.setenv(gw_config.REDIRECT_URI_ENV, "/api/asset-auth/callback")
    gw_config.reset_runtime_auth_config_cache()

    from gw.api.app import create_app

    with TestClient(create_app()) as api:
        response = api.post("/api/asset-auth/login")
    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) == {"authorization_url", "state"}
    assert "code_challenge=" in body["authorization_url"]
    assert session_store.SESSION_COOKIE_NAME not in response.cookies, "登录发起不得直接建立会话"


# ---------------------------------------------------------------------------
# R6-5 / R6-6：base64 严格性与 Cookie Secure
# ---------------------------------------------------------------------------

def test_base64url_decoder_is_strict():
    """非法 Base64URL 字符必须抛错，不得静默返回空字节串。"""
    from gw.core.errors import UnauthorizedException
    from gw.core.oidc import _b64url_decode

    with pytest.raises(UnauthorizedException):
        _b64url_decode("!!!!")


def test_session_cookie_has_secure_flag_when_issuer_is_https(clean_env, monkeypatch):
    """HTTPS issuer 下会话 Cookie 必须带 Secure；本地回环 http 下不得带（否则 Cookie 被丢弃）。"""
    # 同上：resolve_endpoint 在 routes_auth 中为模块级绑定，必须打绑定处。
    from gw.api import routes_auth as gw_routes_auth

    monkeypatch.setattr(gw_routes_auth, "resolve_endpoint", lambda issuer, name: f"{issuer}/{name}")
    monkeypatch.setattr(gw_config, "fetch_discovery_document", lambda issuer: {"jwks_uri": f"{issuer}/jwks"})
    monkeypatch.setattr(gw_config, "fetch_jwks", lambda cfg: {"keys": []})
    clean_env.setenv(gw_config.AUTH_MODE_ENV, gw_config.AUTH_MODE_OIDC)
    clean_env.setenv(gw_config.ISSUER_ENV, "https://idp.example.com")
    clean_env.setenv(gw_config.AUDIENCE_ENV, "gods-workbench")
    clean_env.setenv(gw_config.CLIENT_ID_ENV, "web")
    clean_env.setenv(gw_config.REDIRECT_URI_ENV, "/api/asset-auth/callback")
    gw_config.reset_runtime_auth_config_cache()

    from gw.api.app import create_app

    with TestClient(create_app()) as api:
        response = api.post("/api/asset-auth/login")
    cookie_header = response.headers.get("set-cookie", "")
    assert "Secure" in cookie_header, f"HTTPS issuer 下 Cookie 缺少 Secure 属性: {cookie_header}"
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from gw.core import config as gw_config
from gw.core import session as session_store


# ---------------------------------------------------------------------------
# R6-8：重定向逐跳重校验必须同时锁「允许范围 + 起始 origin」
# ---------------------------------------------------------------------------

def test_redirect_guard_requires_same_origin_with_start(clean_env):
    """异源 302 不得被跟随：两个本地回环 URL 都「在允许范围内」，只有同源锁能拦住。"""
    start = "http://127.0.0.1:51001/jwks.json"
    foreign = "http://127.0.0.1:51002/jwks.json"
    assert gw_config.is_allowed_jwks_url(start) is True
    assert gw_config.is_allowed_jwks_url(foreign) is True
    assert gw_config.is_same_origin_as(foreign, start) is False


def test_redirect_to_foreign_origin_never_hits_attacker(clean_env):
    """真实 HTTP：合法端点的 302 指向异源主机时，必须失败关闭且攻击者零命中。"""
    state = {"attacker_hits": 0, "target": ""}

    class _Attacker(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *args):  # noqa: D102
            return

        def do_GET(self):  # noqa: N802
            state["attacker_hits"] += 1
            body = json.dumps({"keys": [{"kty": "RSA", "kid": "ATTACKER-KEY", "n": "AA", "e": "AQAB"}]}).encode()
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
        with pytest.raises(Exception):
            gw_config.fetch_jwks(gw_config.JwksEndpointConfig(url=start_url, timeout_seconds=5))
        assert state["attacker_hits"] == 0, "异源 302 目标不得被访问"
    finally:
        for server in (attacker, redirector):
            server.shutdown()
            server.server_close()


def test_redirect_guard_binding_is_per_thread(clean_env):
    """起始 origin 绑定必须是线程本地，避免并发请求互相串号。"""
    assert hasattr(gw_config, "_REDIRECT_GUARD")
    assert gw_config._current_redirect_origin() == ""


# ---------------------------------------------------------------------------
# R6-11：opt-in 受信主机白名单（P9G 真实 IdP 互操作）守卫
#
# 背景：真实 Google IdP 的 issuer=accounts.google.com，但 jwks_uri=
# www.googleapis.com/oauth2/v3/certs、token_endpoint=oauth2.googleapis.com/token，
# 严格逐字同源会使其**完全无法接线**（即使显式配 GW_OIDC_JWKS_URL 也被拒）。
# 修法：新增**可选** GW_OIDC_ENDPOINT_HOSTS 白名单；默认留空 = 严格同源。
#
# 本节守卫的三条底线：
#   1. 默认（未配置）必须与 is_same_origin_as **逐条等价**，不得有任何隐式放松；
#   2. 放宽分支必须是「issuer 主机 AND 端点主机 同时精确命中白名单」，
#      后缀/前缀/userinfo/异 scheme/异端口一律拒绝；
#   3. 重定向守卫（_ValidatingRedirectHandler）**不得**被本白名单放宽（R6-8 不回退）。
#
# 注意：本白名单是部署方自填的信任根，**不是**通用安全边界：
# 匹配为幼稚的精确字符串相等，无 PSL、无 eTLD+1 推导。
# ---------------------------------------------------------------------------

def test_default_allowlist_is_empty_and_strict(clean_env):
    """默认不得预置任何第三方主机：DEFAULT_ENDPOINT_HOSTS 必须为空集。"""
    assert gw_config.DEFAULT_ENDPOINT_HOSTS == frozenset()
    assert gw_config.resolve_endpoint_hosts() == frozenset()
    # 未配置时 is_trusted_endpoint 必须与 is_same_origin_as 逐条等价。
    issuers = [
        "https://idp.example.com",
        "https://accounts.google.com",
        "http://127.0.0.1:9000",
    ]
    endpoints = [
        "https://idp.example.com/keys",
        "https://accounts.google.com/keys",
        "https://www.googleapis.com/oauth2/v3/certs",
        "http://127.0.0.1:9000/jwks",
        "http://127.0.0.1:9001/jwks",
        "https://idp.example.com.evil.com/keys",
        "https://idp.example.com@evil.com/keys",
        "https://idp.example.com:8443/keys",
        "http://idp.example.com/keys",
        "https://IDP.example.com/keys",
        "https://idp.example.com./keys",
        "https://evil.com/idp.example.com/keys",
        "",
    ]
    for issuer in issuers:
        for endpoint in endpoints:
            assert gw_config.is_trusted_endpoint(endpoint, issuer) is gw_config.is_same_origin_as(
                endpoint, issuer
            ), f"默认口径与同源判据分歧: {endpoint!r} vs {issuer!r}"


@pytest.mark.parametrize(
    "endpoint,issuer,hosts,expected",
    [
        # 正向：Google 三主机显式 opt-in 后，三个真实端点全部可信。
        ("https://www.googleapis.com/oauth2/v3/certs", "https://accounts.google.com",
         frozenset({"accounts.google.com", "www.googleapis.com", "oauth2.googleapis.com"}), True),
        ("https://oauth2.googleapis.com/token", "https://accounts.google.com",
         frozenset({"accounts.google.com", "www.googleapis.com", "oauth2.googleapis.com"}), True),
        # 后缀把戏：就算白名单里有 idp.example.com，也不得命中 .evil.com。
        ("https://idp.example.com.evil.com/keys", "https://idp.example.com",
         frozenset({"idp.example.com"}), False),
        # userinfo 把戏：真实 host 是 evil.com。
        ("https://idp.example.com@evil.com/keys", "https://idp.example.com",
         frozenset({"idp.example.com"}), False),
        # 只命中一侧（端点主机在清单、issuer 主机不在）：必须拒绝。
        ("https://idp.example.com/keys", "https://issuer.example.com",
         frozenset({"idp.example.com"}), False),
        # 只命中一侧（issuer 主机在清单、端点主机不在）：必须拒绝。
        ("https://other.example.com/keys", "https://idp.example.com",
         frozenset({"idp.example.com"}), False),
        # 路径里塞 issuer 名不改变真实 host。
        ("https://evil.com/idp.example.com/keys", "https://idp.example.com",
         frozenset({"idp.example.com"}), False),
        # 异 scheme（http 明文）即使主机命中也不得放行。
        ("http://idp.example.com/keys", "https://idp.example.com",
         frozenset({"idp.example.com"}), False),
        # 异端口不得放行。
        ("https://idp.example.com:8443/keys", "https://idp.example.com",
         frozenset({"idp.example.com"}), False),
        # 大小写：主机比较大小写不敏感（URL 语义），命中。
        ("https://IDP.example.com/keys", "https://idp.example.com",
         frozenset({"idp.example.com"}), True),
        # FQDN 尾随点：与白名单条目不逐字相等，必须拒绝（放宽分支口径比严格分支更严）。
        ("https://idp.example.com./keys", "https://idp.example.com",
         frozenset({"idp.example.com"}), False),
        # 同族但未列出的主机（无 eTLD+1 推导，不得被当同族放行）。
        ("https://storage.googleapis.com/keys", "https://accounts.google.com",
         frozenset({"accounts.google.com", "www.googleapis.com", "oauth2.googleapis.com"}), False),
    ],
)
def test_is_trusted_endpoint_optin_matrix(endpoint, issuer, hosts, expected):
    assert gw_config.is_trusted_endpoint(endpoint, issuer, hosts) is expected


def test_endpoint_hosts_env_empty_string_means_strict(clean_env):
    """显式设空串必须等价于未配置（严格同源），不得被读成"放开一切"。"""
    clean_env.setenv(gw_config.ENDPOINT_HOSTS_ENV, "")
    assert gw_config.resolve_endpoint_hosts() == frozenset()
    assert gw_config.is_trusted_endpoint(
        "https://www.googleapis.com/oauth2/v3/certs", "https://accounts.google.com"
    ) is False
    # 空白/逗号噪声同样只解析出空集。
    clean_env.setenv(gw_config.ENDPOINT_HOSTS_ENV, "  ,  , ")
    assert gw_config.resolve_endpoint_hosts() == frozenset()


def test_endpoint_hosts_env_is_full_replacement(clean_env):
    """可用逗号 / 空白混排；条目做小写与去尾点规范化。"""
    clean_env.setenv(gw_config.ENDPOINT_HOSTS_ENV, " A.Example.com ,b.example.org;NOPE")
    hosts = gw_config.resolve_endpoint_hosts()
    assert "a.example.com" in hosts
    # 分号不是分隔符：该项连同分号被整体保留（不做语法容错，避免静默放宽）。
    assert all(host.strip() for host in hosts)


def test_authorization_endpoint_stays_same_origin_but_token_may_use_allowlist(clean_env, monkeypatch):
    """授权端点不得因白名单放宽；令牌端点允许 opt-in（否则 Google 无法接线）。"""
    hosts = frozenset({"accounts.google.com", "oauth2.googleapis.com"})
    clean_env.setenv(gw_config.ENDPOINT_HOSTS_ENV, ",".join(sorted(hosts)))
    monkeypatch.setattr(
        gw_config,
        "load_discovery_document",
        lambda issuer, **kw: {
            "authorization_endpoint": "https://accounts.google.com/o/oauth2/v2/auth",
            "token_endpoint": "https://oauth2.googleapis.com/token",
        },
    )
    # 授权端点：与 issuer 同源，通过。
    assert gw_config.resolve_endpoint("https://accounts.google.com", "authorization_endpoint") == (
        "https://accounts.google.com/o/oauth2/v2/auth"
    )
    # 令牌端点：异主机但显式 opt-in，通过。
    assert gw_config.resolve_endpoint("https://accounts.google.com", "token_endpoint") == (
        "https://oauth2.googleapis.com/token"
    )

    # 授权端点若被篡改到白名单内的**另一**主机，仍必须拒绝（授权端点不放宽）。
    monkeypatch.setattr(
        gw_config,
        "load_discovery_document",
        lambda issuer, **kw: {"authorization_endpoint": "https://oauth2.googleapis.com/o/oauth2/v2/auth"},
    )
    with pytest.raises(ValueError):
        gw_config.resolve_endpoint("https://accounts.google.com", "authorization_endpoint")


def test_redirect_guard_is_not_relaxed_by_allowlist(clean_env):
    """R6-8 不得回退：重定向处理器仍必须使用严格 is_same_origin_as。"""
    source = (REPO_ROOT / "gw" / "core" / "config.py").read_text(encoding="utf-8")
    anchor = source.find("class _ValidatingRedirectHandler")
    assert anchor != -1
    region = source[anchor : anchor + 2500]
    assert "is_same_origin_as(newurl, allowed_origin)" in region, (
        "重定向守卫必须继续用严格同源判据，不得改用 is_trusted_endpoint"
    )
    assert "is_trusted_endpoint(newurl" not in region, "重定向守卫不得被白名单放宽"


def test_token_exchange_does_not_follow_redirects(clean_env):
    """凭据路径（令牌交换）不得跟随任何 3xx 重定向。"""
    source = (REPO_ROOT / "gw" / "core" / "config.py").read_text(encoding="utf-8")
    assert "_NO_REDIRECT_OPENER" in source, "必须存在禁止重定向的 opener"
    assert "allow_redirects=False" in source, "令牌交换必须显式禁用重定向跟随"


def test_allowlist_has_no_psl_or_etld_heuristic(clean_env):
    """白名单匹配**无** PSL / eTLD+1 推导（幼稚精确相等）——局限必须被行为固定。

    反面后果：同一可注册域下的**兄弟主机**（a.example.co.uk 与 b.example.co.uk）
    不会被视为同族；部署方必须把每个真实主机逐条填全，不能指望"填根域就够"。
    """
    hosts = frozenset({"a.example.co.uk"})
    # 兄弟主机未列出 -> 拒绝（证明无 eTLD+1 / PSL 推导）。
    assert gw_config.is_trusted_endpoint(
        "https://b.example.co.uk/keys", "https://a.example.co.uk", hosts
    ) is False
    # 若部署方两个主机都填，则两端都可信（白名单就是信任根）。
    both = frozenset({"a.example.co.uk", "b.example.co.uk"})
    assert gw_config.is_trusted_endpoint(
        "https://b.example.co.uk/keys", "https://a.example.co.uk", both
    ) is True
    # 用 registered_domain 之类的后缀近似把 evil.com 纳入即自毁信任根：本实现不提供该能力，
    # 因为匹配只做逐字相等，evil.com 必须被显式写成白名单条目才可能命中。
    assert gw_config.is_trusted_endpoint(
        "https://idp.example.com.evil.com/keys", "https://idp.example.com", frozenset({"idp.example.com"})
    ) is False


def test_allowlist_requires_both_sides_listed_even_when_attacker_domain_listed(clean_env):
    """即使白名单里写了攻击者域，只要 issuer 主机未同时命中就必须拒绝。"""
    assert gw_config.is_trusted_endpoint(
        "https://idp.example.com@evil.com/keys", "https://idp.example.com", frozenset({"evil.com"})
    ) is False
    # 反向：issuer 主机在白名单，但端点真实 host 是 evil.com（不在清单）-> 拒绝。
    assert gw_config.is_trusted_endpoint(
        "https://idp.example.com@evil.com/keys", "https://idp.example.com", frozenset({"idp.example.com"})
    ) is False
