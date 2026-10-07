# -*- coding: utf-8 -*-
"""Phase 9I：真实外部 IdP（生产级端点）接线核验（opt-in，默认 skip）。

与既有用例的分工
----------------
- ``test_oidc_runtime_wiring.py`` / ``test_phase9d_oidc_login_flow.py``：本仓自建 IdP 桩，
  验证配置解析、失败关闭与本地 HTTP 全链路（可离线、进默认门禁）。
- ``test_phase9g_real_op_interop.py``：第三方 OP 软件（npm ``oidc-provider``）本地实例，
  验证与「非本仓实现」的 OP 互操作（需显式安装，未安装即 skip）。
- **本文件**：直接对接**真实外部 IdP 的生产端点**（如 Google / Microsoft 官方 discovery），
  验证本仓 ``core/config.py`` 的 discovery → ``jwks_uri`` → JWKS → 授权 URL 构造
  与运行期接线在真实上游元数据下成立。

启用方式（默认不跑，避免污染离线门禁与外部依赖）::

    set GW_REAL_IDP_ISSUER=https://accounts.google.com
    set GW_REAL_IDP_ENDPOINT_HOSTS=accounts.google.com,www.googleapis.com,oauth2.googleapis.com
    python -m pytest -q tests/contracts/auth/test_phase9i_real_idp_wiring.py

证据边界（**必须随结论一并声明**）
----------------------------------
- 本用例**不**执行真实用户登录：没有真实 ``client_id``、没有用户目录授权、没有真实
  authorization code 换取 id_token；因此**不能**证明生产登录可用。
- 只读拉取公开元数据与公钥（JWKS），**不提交**任何令牌、密钥或客户端凭据。
- 真实外部 IdP 接线仍需部署方提供 client_id / redirect_uri / 用户组映射并完成人工批准。
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
SRC_PATH = REPO_ROOT
if str(SRC_PATH) not in sys.path:
    sys.path.insert(0, str(SRC_PATH))

REAL_IDP_ISSUER_ENV = "GW_REAL_IDP_ISSUER"
REAL_IDP_HOSTS_ENV = "GW_REAL_IDP_ENDPOINT_HOSTS"
SWITCH_ENV_KEYS = (
    "GW_AUTH_MODE",
    "GW_OIDC_ISSUER",
    "GW_OIDC_AUDIENCE",
    "GW_OIDC_CLIENT_ID",
    "GW_OIDC_REDIRECT_URI",
    "GW_OIDC_JWKS_URL",
    "GW_OIDC_GROUPS_CLAIM",
    "GW_OIDC_ENDPOINT_HOSTS",
    "GW_OIDC_LEEWAY_SECONDS",
    REAL_IDP_ISSUER_ENV,
    REAL_IDP_HOSTS_ENV,
)

PROBE_CLIENT_ID = "gw-real-idp-probe-client"
PROBE_REDIRECT_URI = "http://127.0.0.1:2077/api/asset-auth/callback"


def _real_idp_target():
    issuer = os.environ.get(REAL_IDP_ISSUER_ENV, "").strip()
    hosts = os.environ.get(REAL_IDP_HOSTS_ENV, "").strip()
    if not issuer or not hosts:
        pytest.skip(
            "未设置 " + REAL_IDP_ISSUER_ENV + " / " + REAL_IDP_HOSTS_ENV + "，跳过真实外部 IdP 接线核验"
        )
    return issuer, hosts


@pytest.fixture
def real_idp_env(monkeypatch):
    """按真实 IdP 目标切换运行期配置；用例结束后逐字还原 GW_* 环境变量。"""
    issuer, hosts = _real_idp_target()
    original = {key: os.environ[key] for key in SWITCH_ENV_KEYS if key in os.environ}
    for key in SWITCH_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("GW_AUTH_MODE", "oidc")
    monkeypatch.setenv("GW_OIDC_ISSUER", issuer)
    monkeypatch.setenv("GW_OIDC_AUDIENCE", PROBE_CLIENT_ID)
    monkeypatch.setenv("GW_OIDC_CLIENT_ID", PROBE_CLIENT_ID)
    monkeypatch.setenv("GW_OIDC_REDIRECT_URI", PROBE_REDIRECT_URI)
    monkeypatch.setenv("GW_OIDC_GROUPS_CLAIM", "groups")
    monkeypatch.setenv("GW_OIDC_ENDPOINT_HOSTS", hosts)
    monkeypatch.setenv(REAL_IDP_ISSUER_ENV, issuer)
    monkeypatch.setenv(REAL_IDP_HOSTS_ENV, hosts)

    from gw.core import config as gw_config

    gw_config.reset_runtime_auth_config_cache()
    gw_config.reset_discovery_cache()
    try:
        yield {"issuer": issuer, "hosts": hosts, "original": original}
    finally:
        gw_config.reset_runtime_auth_config_cache()
        gw_config.reset_discovery_cache()


def _write_evidence(name: str, payload: dict) -> Path:
    """把真实上游只读证据写到临时目录（不进入仓库）。"""
    import tempfile

    target = Path(tempfile.gettempdir()) / "gw-real-idp-evidence"
    target.mkdir(parents=True, exist_ok=True)
    path = target / name
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def test_real_idp_discovery_issuer_matches_and_jwks_is_public(real_idp_env):
    """真实上游 discovery 必须自述 issuer 一致，JWKS 只含公开材料。"""
    from gw.core import config as gw_config

    issuer = real_idp_env["issuer"]
    document = gw_config.load_discovery_document(issuer)
    assert document.get("issuer") == issuer, "discovery 文档 issuer 必须与配置 issuer 逐字一致"

    jwks_url = gw_config.resolve_jwks_url(issuer, "")
    assert gw_config.is_allowed_jwks_url(jwks_url), "jwks_uri 不在部署方显式白名单内"
    assert urlparse(jwks_url).scheme == "https", "真实外部 IdP 的 jwks_uri 必须是 HTTPS"

    jwks = gw_config.fetch_jwks(gw_config.JwksEndpointConfig(url=jwks_url))
    keys = [k for k in (jwks.get("keys") or []) if isinstance(k, dict)]
    assert keys, "真实上游 JWKS 未返回任何密钥"
    assert any(k.get("kty") == "RSA" for k in keys), "真实上游 JWKS 未返回 RSA 公钥"
    assert not any("d" in k for k in keys), "JWKS 不得包含私钥材料"
    assert all(k.get("kid") for k in keys), "每把密钥都应有 kid，供轮换与精确定位"

    _write_evidence(
        "discovery.json",
        {
            "issuer": issuer,
            "discovery_issuer": document.get("issuer"),
            "jwks_uri": jwks_url,
            "authorization_endpoint": document.get("authorization_endpoint"),
            "token_endpoint": document.get("token_endpoint"),
            "id_token_signing_alg_values_supported": document.get("id_token_signing_alg_values_supported"),
            "jwks_key_count": len(keys),
            "jwks_kids": [k.get("kid") for k in keys],
            "jwks_has_private_material": False,
        },
    )


def test_real_idp_endpoints_are_trusted_and_https(real_idp_env):
    """authorization / token 端点必须可解析、在白名单内且为 HTTPS。"""
    from gw.core import config as gw_config

    issuer = real_idp_env["issuer"]
    resolved = {}
    for name in ("authorization_endpoint", "token_endpoint"):
        endpoint = gw_config.resolve_endpoint(issuer, name)
        assert endpoint.startswith("https://"), name + " 必须是 HTTPS"
        assert gw_config.is_trusted_endpoint(endpoint, issuer), name + " 未被白名单判定为可信端点"
        resolved[name] = endpoint
    _write_evidence("endpoints.json", resolved)


def test_real_idp_authorization_url_uses_pkce_and_leaks_no_secret(real_idp_env):
    """真实 authorization_endpoint 上构造的授权 URL 必须 PKCE S256 且不含任何密钥。"""
    from gw.core import config as gw_config
    from gw.core.oidc import build_authorization_url, generate_nonce, generate_pkce_pair, generate_state

    issuer = real_idp_env["issuer"]
    authorization_endpoint = gw_config.resolve_endpoint(issuer, "authorization_endpoint")
    state = generate_state()
    nonce = generate_nonce()
    _, code_challenge = generate_pkce_pair()
    url = build_authorization_url(
        authorization_endpoint=authorization_endpoint,
        client_id=PROBE_CLIENT_ID,
        redirect_uri=PROBE_REDIRECT_URI,
        state=state,
        nonce=nonce,
        code_challenge=code_challenge,
        scope="openid profile email",
    )
    query = parse_qs(urlparse(url).query)
    assert query.get("response_type") == ["code"]
    assert query.get("code_challenge_method") == ["S256"]
    assert query.get("state") == [state] and query.get("nonce") == [nonce]
    assert query.get("client_id") == [PROBE_CLIENT_ID]
    for forbidden in ("client_secret", "access_token", "id_token", "refresh_token", "code_verifier"):
        assert forbidden not in query, forbidden + " 不得出现在授权 URL 中"
    assert url.startswith("https://"), "真实 IdP 授权端点必须是 HTTPS"
    _write_evidence("authorization_url_shape.json", {"endpoint": authorization_endpoint, "params": sorted(query)})


def test_real_idp_runtime_wiring_is_ready_and_fails_closed(real_idp_env):
    """运行期：oidc_ready=true、/login 200；缺失/伪造凭据与提权头一律 401。"""
    from fastapi.testclient import TestClient

    from gw.core import config as gw_config
    from gw.api.app import create_app

    runtime = gw_config.load_runtime_auth_config()
    assert runtime.mode == "oidc"
    assert runtime.ready is True, "真实外部 IdP 配置应使运行期就绪"
    assert runtime.login_ready is True, "client_id / redirect_uri 齐备时应可发起登录"

    client = TestClient(create_app())
    health = client.get("/healthz")
    assert health.status_code == 200
    assert health.json().get("auth_mode") == "oidc"
    assert health.json().get("oidc_ready") is True
    assert health.json().get("release_authorized") is False, "发布授权不得被本用例改变"

    login = client.post("/api/asset-auth/login")
    assert login.status_code == 200, "登录发起应返回真实 IdP 授权 URL"
    payload = login.json()
    assert payload.get("authorization_url", "").startswith("https://")
    assert "gw_oidc_flow" in login.cookies, "应写入一次性流程 Cookie"

    body = {"name": "real-idp-probe", "project_type": "film"}
    cases = {
        "no_credential": {},
        "forged_bearer": {"Authorization": "Bearer not-a-real-token"},
        "role_header_only": {"X-User-Role": "governor"},
        "local_legacy_credential": {"Authorization": "Bearer invalid", "X-User-Role": "governor"},
    }
    observed = {}
    for label, headers in cases.items():
        response = client.post("/api/asset-registry/projects", json=body, headers=headers)
        observed[label] = response.status_code
        assert response.status_code == 401, label + " 必须失败关闭为 401，实际 " + str(response.status_code)
    _write_evidence(
        "runtime_fail_closed.json",
        {
            "issuer": real_idp_env["issuer"],
            "auth_mode": runtime.mode,
            "oidc_ready": runtime.ready,
            "login_ready": runtime.login_ready,
            "login_status": login.status_code,
            "write_status_by_case": observed,
        },
    )


def test_real_idp_is_explicit_opt_in_not_default(monkeypatch):
    """未显式配置时必须保持 local_account 默认，证明真实外部 IdP 是显式 opt-in。

    注意：本用例**不**依赖 real_idp_env fixture（否则未配置真实 IdP 时会被 skip），
    也**不**直接 pop 真实环境变量（否则会污染同一 pytest 会话中的其它 OIDC 用例）。
    """
    from gw.core import config as gw_config

    for key in SWITCH_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    gw_config.reset_runtime_auth_config_cache()
    gw_config.reset_discovery_cache()
    runtime = gw_config.load_runtime_auth_config()
    assert runtime.mode == "local_account", "未显式配置时认证模式必须保持 local_account"
