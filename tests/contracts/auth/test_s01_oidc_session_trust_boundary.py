# -*- coding: utf-8 -*-
"""S-01 回归：OIDC 会话与信任根失效边界。

背景（2026-09-28 独立技术审计观察，**未**证实为可外部利用的攻击链）
-------------------------------------------------------------------
审计观察到：``GW_AUTH_MODE=oidc`` 且运行期配置缺失（``oidc_ready=false``）时，
``core/auth.py`` 的 Cookie 分支仍可能仅凭"内存里还有一条存活会话"返回可用的
``AuthContext``，从而使受保护读端点不再失败关闭。本条按观察收口为**失败关闭**，
不主张已证实的外部攻击链。

本文件固定的边界
----------------
1. 会话必须绑定**发行者**与**配置代际**；绑定字段缺失的会话一律按失效处理。
2. ``issuer``/``audience`` 为空或 ``oidc_ready=false`` 时，不得返回可用 AuthContext，
   且必须撤销旧会话（重复请求不得再次命中同一条会话）。
3. 撤销必须留脱敏审计（``auth.session.revoked``），且不得记录 Cookie 值。
4. 人工注入的内存会话在未就绪配置下**不能**得到项目列表 200。
5. OIDC Cookie 写路径与 ``local_account`` 同口径 Origin 门禁（缺失来源即拒绝）。
6. 401/403 契约语义与既有错误码不变。

证据边界：本文件证明的是**代码路径的失败关闭语义**，不代表已接入生产 IdP、
不代表生产就绪，也不改变仓库的 NOT AUTHORIZED 发布状态。
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
SRC_PATH = REPO_ROOT
if str(SRC_PATH) not in sys.path:
    sys.path.insert(0, str(SRC_PATH))

from gw.core import audit as audit_log  # noqa: E402
from gw.core import config as gw_config  # noqa: E402
from gw.core import session as session_store  # noqa: E402
from gw.core.errors import (  # noqa: E402
    ForbiddenException,
    UnauthorizedException,
)

ISSUER = "https://idp.example.test"
AUDIENCE = "gods-workbench"
JWKS_URL = "https://idp.example.test/jwks.json"

# 契约错误码直接取自生产常量，**不**在测试里手抄字面量：
# 手抄会在同形字（西里尔字母混入拉丁字母）上写错，反而逼迫生产代码去迎合笔误。
UNAUTHORIZED_CODE = UnauthorizedException().code
FORBIDDEN_CODE = ForbiddenException().code

# 人工注入的内存会话：模拟进程内遗留会话（登录成功时曾绑定当时的信任根）。
INJECTED_ROLE = "admin"


@pytest.fixture()
def clean_env(monkeypatch, tmp_path):
    """清空 GW_* 环境变量与全部内存态（会话 + 审计），保证用例互不污染。

    ``GW_DATA_DIR`` / ``GW_VIDEO_DATA_DIR`` 必须显式重新指向本用例的临时目录：
    ``tests/conftest.py`` 的自动隔离夹具用的是 monkeypatch，而本夹具为了彻底清空
    ``GW_*`` 会 unset 掉它们（含 conftest 刚设的值）。若不同时补回**绝对**路径，
    视频服务会以 "视频数据目录必须为绝对路径" 503 抢在认证之前返回，
    从而掩盖真实的认证状态码（会把 201 观测成 503/401）。
    """
    for name in list(os.environ):
        if name.startswith("GW_"):
            monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("GW_DATA_DIR", str(tmp_path / "gw-data"))
    monkeypatch.setenv("GW_RUNTIME_MODE", "test")
    monkeypatch.setenv("GW_DATA_DIR", str(tmp_path / "gw-data"))
    monkeypatch.setenv("GW_LOCAL_AUTH_DB", str(tmp_path / "gw-auth" / "auth.sqlite3"))
    monkeypatch.setenv("GW_VIDEO_DATA_DIR", str(tmp_path / "gw-video"))
    monkeypatch.delenv("GW_ALLOWED_ROOTS", raising=False)
    gw_config.reset_runtime_auth_config_cache()
    gw_config.reset_discovery_cache()
    session_store.reset_stores()
    audit_log.reset_audit_log()
    yield monkeypatch
    gw_config.reset_runtime_auth_config_cache()
    gw_config.reset_discovery_cache()
    session_store.reset_stores()
    audit_log.reset_audit_log()


def _configure_oidc(env, *, issuer: str = ISSUER, audience: str = AUDIENCE) -> None:
    env.setenv(gw_config.AUTH_MODE_ENV, gw_config.AUTH_MODE_OIDC)
    env.setenv(gw_config.ISSUER_ENV, issuer)
    env.setenv(gw_config.AUDIENCE_ENV, audience)
    env.setenv(gw_config.JWKS_URL_ENV, JWKS_URL)
    gw_config.reset_runtime_auth_config_cache()


def _break_config(env, *, drop_issuer: bool = True, drop_audience: bool = True) -> None:
    """把已经就绪的配置改成未就绪（issuer / audience 被清空）。"""
    if drop_issuer:
        env.delenv(gw_config.ISSUER_ENV, raising=False)
    if drop_audience:
        env.delenv(gw_config.AUDIENCE_ENV, raising=False)
    gw_config.reset_runtime_auth_config_cache()


def _inject_session(*, issuer: str = ISSUER, audience: str = AUDIENCE, generation: str | None = None, **overrides):
    """直接向内存会话库注入一条会话（不走登录流程，模拟遗留/被注入会话）。"""
    principal = {
        "username": "injected-user",
        "display_name": "injected-user",
        "role": INJECTED_ROLE,
        "groups": ["gw-admin"],
        "issuer": issuer,
        "audience": audience,
        "config_generation": gw_config.auth_config_generation() if generation is None else generation,
    }
    principal.update(overrides)
    return session_store.create_session(principal)


def _app():
    from gw.api.app import create_app

    return create_app()


def _events(name: str):
    return [item for item in audit_log.list_auth_events() if item["event"] == name]


# ---------------------------------------------------------------------------
# 1. 纯判据：会话信任根失效原因必须是封闭集合
# ---------------------------------------------------------------------------

def test_bound_session_under_ready_config_has_no_failure(clean_env):
    """配置就绪且会话绑定当前信任根时，不得报失效。"""
    from gw.core.auth import oidc_session_trust_failure_reason

    _configure_oidc(clean_env)
    runtime = gw_config.load_runtime_auth_config()
    assert runtime.ready is True
    bound = {
        "issuer": ISSUER,
        "config_generation": gw_config.auth_config_generation(),
    }
    assert oidc_session_trust_failure_reason(bound, runtime) is None


def test_unready_config_reports_oidc_not_ready(clean_env):
    """oidc_ready=false 时，即使会话字段齐全也必须判为失效。"""
    from gw.core.auth import oidc_session_trust_failure_reason

    _configure_oidc(clean_env)
    bound = {"issuer": ISSUER, "config_generation": gw_config.auth_config_generation()}
    _break_config(clean_env)
    runtime = gw_config.load_runtime_auth_config()
    assert runtime.ready is False
    assert oidc_session_trust_failure_reason(bound, runtime) == "oidc_not_ready"


def test_empty_issuer_or_audience_is_not_ready(clean_env):
    """issuer 或 audience 为空不得被当作"宽松匹配"放行。"""
    from gw.core.auth import oidc_session_trust_failure_reason

    _configure_oidc(clean_env)
    runtime = gw_config.load_runtime_auth_config()
    generation = gw_config.auth_config_generation()

    # 运行期 issuer 非空但会话绑定为空：必须判为发行者失配（不得因空值而通过）。
    assert oidc_session_trust_failure_reason(
        {"issuer": "", "config_generation": generation}, runtime
    ) == "issuer_mismatch"
    # 运行期就绪但会话缺少绑定字段：一律失效，不放行。
    assert oidc_session_trust_failure_reason({}, runtime) == "issuer_mismatch"


def test_issuer_change_reports_issuer_mismatch(clean_env):
    """信任根发行者变更后，旧会话必须判为发行者失配。"""
    from gw.core.auth import oidc_session_trust_failure_reason

    _configure_oidc(clean_env)
    runtime = gw_config.load_runtime_auth_config()
    stale = {
        "issuer": "https://old-idp.example.test",
        "config_generation": gw_config.auth_config_generation(),
    }
    assert oidc_session_trust_failure_reason(stale, runtime) == "issuer_mismatch"


def test_generation_change_reports_trust_root_changed(clean_env):
    """配置代际变化（信任根相关环境变量改动）后，旧会话必须失效。"""
    from gw.core.auth import oidc_session_trust_failure_reason

    _configure_oidc(clean_env)
    stale_generation = gw_config.auth_config_generation()
    # 改动信任根相关变量（受众）使代际变化，但配置依旧就绪。
    _configure_oidc(clean_env, audience="gods-workbench-rotated")
    runtime = gw_config.load_runtime_auth_config()
    assert runtime.ready is True
    stale = {"issuer": ISSUER, "config_generation": stale_generation}
    assert oidc_session_trust_failure_reason(stale, runtime) == "trust_root_changed"


def test_missing_generation_is_treated_as_changed(clean_env):
    """会话缺少配置代际绑定时一律失效，不得按"无约束"放行。"""
    from gw.core.auth import oidc_session_trust_failure_reason

    _configure_oidc(clean_env)
    runtime = gw_config.load_runtime_auth_config()
    assert oidc_session_trust_failure_reason(
        {"issuer": ISSUER}, runtime
    ) == "trust_root_changed"


def test_failure_reasons_are_closed_vocabulary(clean_env):
    """**失效**原因必须落在封闭集合内，避免任意文本进入审计 reason。

    注意口径：``oidc_session_trust_failure_reason`` 只在会话**失效**时返回原因，
    就绪且绑定正确时返回 ``None``（表示"无失效"），因此 ``None`` 不属于原因集合。
    本用例只对**确定失效**的三种输入收集原因。
    """
    from gw.core.auth import oidc_session_trust_failure_reason

    allowed = {"oidc_not_ready", "issuer_mismatch", "trust_root_changed"}
    _configure_oidc(clean_env)
    runtime = gw_config.load_runtime_auth_config()
    generation = gw_config.auth_config_generation()

    # 对照组：就绪 + 绑定正确 -> 不是失效，必须返回 None。
    assert oidc_session_trust_failure_reason(
        {"issuer": ISSUER, "config_generation": generation}, runtime
    ) is None

    # 三种确定失效的输入，原因必须全部落在封闭集合内。
    observed = {
        oidc_session_trust_failure_reason(
            {"issuer": "https://other.example.test", "config_generation": generation}, runtime
        ),
        oidc_session_trust_failure_reason(
            {"issuer": ISSUER, "config_generation": "deadbeef"}, runtime
        ),
        oidc_session_trust_failure_reason({"issuer": "", "config_generation": ""}, runtime),
    }
    assert observed <= allowed, observed
    assert None not in observed, observed

    # 未就绪配置同样必须给出封闭集合内的原因。
    _break_config(clean_env)
    unready_runtime = gw_config.load_runtime_auth_config()
    assert oidc_session_trust_failure_reason(
        {"issuer": ISSUER, "config_generation": generation}, unready_runtime
    ) in allowed


# ---------------------------------------------------------------------------
# 2. 行为级：未就绪配置下人工内存会话不得得到项目列表 200
# ---------------------------------------------------------------------------

def test_injected_session_cannot_list_projects_when_config_unready(clean_env):
    """人工注入的内存会话，在 oidc_ready=false 时不能得到项目列表 200。

    这是审计观察的回归核心：配置失效后不得再凭"内存里还有一条存活会话"放行。
    """
    _configure_oidc(clean_env)
    session_id = _inject_session()
    _break_config(clean_env)

    with TestClient(_app()) as api:
        response = api.get(
            "/api/projects",
            headers={"Cookie": f"{session_store.SESSION_COOKIE_NAME}={session_id}"},
        )
    assert response.status_code == 401, response.text
    assert response.json()["detail"]["code"] == UNAUTHORIZED_CODE


def test_injected_session_is_revoked_not_merely_denied(clean_env):
    """失效会话必须被撤销：第一次拒绝后会话记录不得再存活（不得反复复用）。"""
    _configure_oidc(clean_env)
    session_id = _inject_session()
    _break_config(clean_env)
    assert session_store.get_session(session_id) is not None

    with TestClient(_app()) as api:
        first = api.get(
            "/api/projects",
            headers={"Cookie": f"{session_store.SESSION_COOKIE_NAME}={session_id}"},
        )
        second = api.get(
            "/api/projects",
            headers={"Cookie": f"{session_store.SESSION_COOKIE_NAME}={session_id}"},
        )

    assert (first.status_code, second.status_code) == (401, 401)
    assert session_store.get_session(session_id) is None, "失效会话未被撤销"


def test_status_endpoint_rejects_injected_session_when_config_unready(clean_env):
    """/status 必须如实显示未认证，且不把注入会话当成已登录。"""
    _configure_oidc(clean_env)
    session_id = _inject_session()
    _break_config(clean_env)

    with TestClient(_app()) as api:
        body = api.get(
            "/api/asset-auth/status",
            headers={"Cookie": f"{session_store.SESSION_COOKIE_NAME}={session_id}"},
        ).json()
    assert body["authenticated"] is False
    assert body["principal"] is None
    assert body["oidc_ready"] is False


def test_issuer_swap_revokes_session_and_fails_closed(clean_env):
    """信任根发行者被换成另一 IdP 后，旧会话必须 401 且被撤销。"""
    _configure_oidc(clean_env)
    session_id = _inject_session()
    _configure_oidc(clean_env, issuer="https://rotated-idp.example.test")

    with TestClient(_app()) as api:
        response = api.get(
            "/api/projects",
            headers={"Cookie": f"{session_store.SESSION_COOKIE_NAME}={session_id}"},
        )
    assert response.status_code == 401, response.text
    assert session_store.get_session(session_id) is None


def test_rotation_to_unready_then_back_does_not_revive_session(clean_env):
    """配置坏掉再修好，已撤销的会话不得复活（不得靠重启配置"救活"旧会话）。"""
    _configure_oidc(clean_env)
    session_id = _inject_session()
    _break_config(clean_env)

    with TestClient(_app()) as api:
        assert api.get(
            "/api/projects",
            headers={"Cookie": f"{session_store.SESSION_COOKIE_NAME}={session_id}"},
        ).status_code == 401

    # 把配置改回就绪：会话已撤销，不得凭原 Cookie 恢复访问。
    _configure_oidc(clean_env)
    with TestClient(_app()) as api:
        response = api.get(
            "/api/projects",
            headers={"Cookie": f"{session_store.SESSION_COOKIE_NAME}={session_id}"},
        )
    assert response.status_code == 401, response.text
    assert session_store.get_session(session_id) is None


def test_revocation_is_audited_without_credentials(clean_env):
    """撤销必须留脱敏审计：reason 在封闭集合内，且不得出现会话 ID / Cookie 值。"""
    _configure_oidc(clean_env)
    session_id = _inject_session()
    _break_config(clean_env)

    with TestClient(_app()) as api:
        api.get(
            "/api/projects",
            headers={"Cookie": f"{session_store.SESSION_COOKIE_NAME}={session_id}"},
        )

    revoked = _events(audit_log.EVENT_SESSION_REVOKED)
    assert revoked, "撤销会话必须留痕"
    assert revoked[-1]["outcome"] == audit_log.OUTCOME_DENIED
    assert revoked[-1]["reason"] == "oidc_not_ready"
    blob = json.dumps(audit_log.list_auth_events(), ensure_ascii=False)
    assert session_id not in blob, "审计记录不得包含会话标识原文"
    assert "gw_session" not in blob, "审计记录不得包含 Cookie 名或值"


def test_ready_config_still_accepts_bound_session(clean_env):
    """修复不得误伤正常路径：配置就绪且会话绑定当前信任根时读写仍正常。"""
    _configure_oidc(clean_env)
    session_id = _inject_session()
    # 注意：这里必须是**请求头**（含 Cookie 与 Origin），不是 cookie 字典。
    # 早前版本把 {"gw_session": sid} 直接展开进 headers，浏览器端读到的并不是
    # Cookie 头，导致请求确实未携带会话 -> 401，看起来像"修复误伤正常路径"。
    headers = {
        "Cookie": f"{session_store.SESSION_COOKIE_NAME}={session_id}",
        "Origin": "http://testserver",
    }

    with TestClient(_app()) as api:
        listing = api.get("/api/projects", headers=headers)
        created = api.post(
            "/api/asset-registry/projects",
            json={"name": "S-01 正常路径项目", "project_type": "film"},
            headers=headers,
        )
    assert listing.status_code == 200, listing.text
    assert created.status_code == 201, created.text
    assert session_store.get_session(session_id) is not None, "就绪配置下会话不应被误撤销"


def test_role_boundary_is_unchanged_for_bound_session(clean_env):
    """角色边界语义不得被本次修复改变：readonly 仍 403 而非 401。"""
    _configure_oidc(clean_env)
    session_id = session_store.create_session(
        {
            "username": "readonly-user",
            "role": "readonly",
            "groups": ["gw-readonly"],
            "issuer": ISSUER,
            "audience": AUDIENCE,
            "config_generation": gw_config.auth_config_generation(),
        }
    )
    with TestClient(_app()) as api:
        response = api.post(
            "/api/asset-registry/projects",
            json={"name": "只读不应写入", "project_type": "film"},
            headers={
                "Cookie": f"{session_store.SESSION_COOKIE_NAME}={session_id}",
                "Origin": "http://testserver",
            },
        )
    assert response.status_code == 403, response.text
    assert response.json()["detail"]["code"] == FORBIDDEN_CODE


def test_hashed_bearer_path_is_unaffected_by_session_binding(clean_env):
    """Bearer 令牌路径不受本修复影响：配置缺失时仍 401（不回落本地信任）。"""
    from gw.core.auth import require_authenticated
    from gw.core.errors import UnauthorizedException

    clean_env.setenv(gw_config.AUTH_MODE_ENV, gw_config.AUTH_MODE_OIDC)
    gw_config.reset_runtime_auth_config_cache()
    with pytest.raises(UnauthorizedException):
        require_authenticated("Bearer anything-at-all", "admin")


# ---------------------------------------------------------------------------
# 3. OIDC Cookie 写路径的 Origin 门禁（与 local_account 同口径）
# ---------------------------------------------------------------------------

def test_oidc_write_without_origin_is_rejected(clean_env):
    """OIDC Cookie 写操作缺失 Origin 时一律 403（与 local_account 同口径）。"""
    _configure_oidc(clean_env)
    session_id = _inject_session()

    with TestClient(_app()) as api:
        response = api.post(
            "/api/asset-registry/projects",
            json={"name": "缺失来源不应写入", "project_type": "film"},
            headers={"Cookie": f"{session_store.SESSION_COOKIE_NAME}={session_id}"},
        )
    assert response.status_code == 403, response.text
    assert response.json()["detail"]["code"] == "CSRF_ORIGIN_REJECTED"


def test_oidc_write_with_cross_site_fetch_metadata_is_rejected(clean_env):
    """显式跨站标记（Sec-Fetch-Site: cross-site）必须被拒绝，即使 Origin 自述同源。"""
    _configure_oidc(clean_env)
    session_id = _inject_session()

    with TestClient(_app()) as api:
        response = api.post(
            "/api/asset-registry/projects",
            json={"name": "跨站标记不应写入", "project_type": "film"},
            headers={
                "Cookie": f"{session_store.SESSION_COOKIE_NAME}={session_id}",
                "Origin": "http://testserver",
                "Sec-Fetch-Site": "cross-site",
            },
        )
    assert response.status_code == 403, response.text
    assert response.json()["detail"]["code"] == "CSRF_ORIGIN_REJECTED"


def test_oidc_write_with_foreign_origin_is_rejected(clean_env):
    """异源 Origin 必须被拒绝。"""
    _configure_oidc(clean_env)
    session_id = _inject_session()

    with TestClient(_app()) as api:
        response = api.post(
            "/api/asset-registry/projects",
            json={"name": "异源不应写入", "project_type": "film"},
            headers={
                "Cookie": f"{session_store.SESSION_COOKIE_NAME}={session_id}",
                "Origin": "https://evil.example.test",
            },
        )
    assert response.status_code == 403, response.text
    assert response.json()["detail"]["code"] == "CSRF_ORIGIN_REJECTED"


def test_oidc_same_origin_write_still_succeeds(clean_env):
    """同源写操作不得被门禁误伤。"""
    _configure_oidc(clean_env)
    session_id = _inject_session()

    with TestClient(_app()) as api:
        response = api.post(
            "/api/asset-registry/projects",
            json={"name": "同源应写入", "project_type": "film"},
            headers={
                "Cookie": f"{session_store.SESSION_COOKIE_NAME}={session_id}",
                "Origin": "http://testserver",
                "Sec-Fetch-Site": "same-origin",
            },
        )
    assert response.status_code == 201, response.text


def test_oidc_read_requests_are_not_subject_to_origin_gate(clean_env):
    """读请求（GET）不要求 Origin：只读端点不得因浏览器省略 Origin 而失效。"""
    _configure_oidc(clean_env)
    session_id = _inject_session()

    with TestClient(_app()) as api:
        response = api.get(
            "/api/projects",
            headers={"Cookie": f"{session_store.SESSION_COOKIE_NAME}={session_id}"},
        )
    assert response.status_code == 200, response.text


# ---------------------------------------------------------------------------
# 4. 契约语义不变（401/403/409/202 与错误码）
# ---------------------------------------------------------------------------

def test_unready_config_still_returns_401_contract(clean_env):
    """未就绪配置下的拒绝仍是 401 + 既有错误码，不引入新状态码。"""
    _configure_oidc(clean_env)
    session_id = _inject_session()
    _break_config(clean_env)

    with TestClient(_app()) as api:
        response = api.get(
            "/api/projects",
            headers={"Cookie": f"{session_store.SESSION_COOKIE_NAME}={session_id}"},
        )
    assert response.status_code == 401
    assert response.json()["detail"]["code"] == UNAUTHORIZED_CODE


def test_login_still_returns_503_when_oidc_not_ready(clean_env):
    """显式启用 OIDC 但未就绪时仍返回 503 OIDC_NOT_CONFIGURED。"""
    clean_env.setenv(gw_config.AUTH_MODE_ENV, gw_config.AUTH_MODE_OIDC)
    with TestClient(_app()) as api:
        response = api.post("/api/asset-auth/login")
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "OIDC_NOT_CONFIGURED"


def test_logout_still_returns_204_idempotently(clean_env):
    """登出仍幂等 204，不受会话绑定判定影响。

    登出是**认证端点**，不以有效 Cookie 会话为前提（会话可能已失效），
    因此它在 Origin 门禁的豁免集合内，无 ``Origin`` 也必须可用；
    否则会话一旦失效用户就再也无法登出。
    """
    from gw.api.app import CSRF_ORIGIN_EXEMPT_PATHS

    assert "/api/asset-auth/logout" in CSRF_ORIGIN_EXEMPT_PATHS, "登出必须豁免 Origin 门禁"

    _configure_oidc(clean_env)
    session_id = _inject_session()

    with TestClient(_app()) as api:
        first = api.post(
            "/api/asset-auth/logout",
            headers={"Cookie": f"{session_store.SESSION_COOKIE_NAME}={session_id}"},
        )
        second = api.post(
            "/api/asset-auth/logout",
            headers={"Cookie": f"{session_store.SESSION_COOKIE_NAME}={session_id}"},
        )
    assert (first.status_code, second.status_code) == (204, 204)


def test_auth_endpoints_are_exempt_from_origin_gate(clean_env):
    """登录/登出端点不得被 Cookie 写路径的 Origin 门禁锁死（无 Origin 仍可用）。"""
    from gw.api.app import CSRF_ORIGIN_EXEMPT_PATHS

    for path in ("/api/asset-auth/login", "/api/asset-auth/logout"):
        assert path in CSRF_ORIGIN_EXEMPT_PATHS, path + " 必须豁免 Origin 门禁"

    _configure_oidc(clean_env)
    with TestClient(_app()) as api:
        # 无 Origin 的登录发起：未配置 client_id/redirect_uri -> 503（流程配置缺失），
        # 关键断言是**不是 403**（未被 Origin 门禁拦下）。
        response = api.post("/api/asset-auth/login")
    assert response.status_code != 403, response.text


def test_business_write_routes_are_not_exempt_from_origin_gate(clean_env):
    """业务写接口不得出现在豁免集合内（门禁只对 OIDC 认证端点开口）。"""
    from gw.api.app import CSRF_ORIGIN_EXEMPT_PATHS

    for candidate in (
        "/api/asset-registry/projects",
        "/api/canvases",
        "/api/settings",
    ):
        assert candidate not in CSRF_ORIGIN_EXEMPT_PATHS, candidate + " 不得豁免 Origin 门禁"


def test_local_account_write_gate_is_not_loosened_by_oidc_exemptions(clean_env):
    """``local_account`` 的写路径门禁不得被 S-01 的 OIDC 豁免集合放宽。

    既有行为（tests/contracts/auth/test_local_account_login.py）要求本地账户的初始化与
    登出同样受同源门禁约束；豁免只应作用于 OIDC 分支。
    """
    clean_env.setenv(gw_config.AUTH_MODE_ENV, gw_config.AUTH_MODE_LOCAL_ACCOUNT)
    for name in list(os.environ):
        if name.startswith("GW_OIDC_"):
            clean_env.delenv(name, raising=False)
    gw_config.reset_runtime_auth_config_cache()

    with TestClient(_app()) as api:
        logout_no_origin = api.post("/api/asset-auth/logout")
        setup_evil_origin = api.post(
            "/api/asset-auth/local/setup",
            json={"username": "owner", "password": "Test2026"},
            headers={"Origin": "http://evil.test"},
        )
    assert logout_no_origin.status_code == 403, logout_no_origin.text
    assert setup_evil_origin.status_code == 403, setup_evil_origin.text


def test_two_middlewares_share_one_trust_predicate():
    """判据只有一处实现：``routes_auth._oidc_session_still_bound`` 必须委托 core.auth。

    防止 ``/status``、``/login`` 与鉴权路径各写一套判据后再次漂移。
    断言范围**只限该判据函数体本身**：登录成功时绑定代际（``_principal_from_identity``）
    本来就必须调用 ``auth_config_generation``，不得被本条误伤。
    """
    source = (
        REPO_ROOT / "gw" / "api" / "routes_auth.py"
    ).read_text(encoding="utf-8")
    start_at = source.find("def _oidc_session_still_bound")
    assert start_at != -1
    stop_at = source.find("\ndef ", start_at + 1)
    region = source[start_at : stop_at if stop_at != -1 else len(source)]
    assert "oidc_session_trust_failure_reason" in region, (
        "/status 与 /login 的会话判据必须委托 core.auth 的唯一实现"
    )
    assert "auth_config_generation" not in region, (
        "不得在本模块重复实现代际比较（应委托 core.auth 的唯一判据）"
    )
    assert "getattr(runtime" not in region, "不得在本模块重复解析运行期 issuer/audience"



def test_core_auth_does_not_reintroduce_unsafe_session_shortcut():
    """安全反向断言：``core/auth.py`` 不得再出现裸 `return AuthContext` 于未校验会话分支。"""
    source = (
        REPO_ROOT / "gw" / "core" / "auth.py"
    ).read_text(encoding="utf-8")
    anchor = source.find("if runtime.mode == AUTH_MODE_OIDC:")
    assert anchor != -1
    region = source[anchor : anchor + 4000]
    assert "oidc_session_trust_failure_reason" in region, (
        "Cookie 分支必须先做信任根判定，再返回 AuthContext"
    )
    assert "session_store.get_current_principal()" in region
