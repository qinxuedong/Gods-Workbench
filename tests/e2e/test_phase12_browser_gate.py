"""Phase 12 认证浏览器门禁的隔离、安全与反假绿契约。"""
from __future__ import annotations

import ast
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


browser_gate = _load(ROOT / "tools" / "phase12_authenticated_browser.py", "phase12_authenticated_browser_test")
smoke = browser_gate.load_smoke_module()


def _passing_rows():
    page = "projects.html"
    probe = {
        "page": page,
        "auth_http_status": 200,
        "authenticated": True,
        "principal_role": "admin",
        "business_api_http_status": 200,
        "business_api_payload_valid": True,
        "gpu_health_http_status": 200,
        "gpu": {"state": "unavailable", "status": "not_integrated"},
        "gpu_ui": {"matches_backend": True},
        "page_errors": [],
    }
    e2e = {"pages": [{"page": page, "page_errors": [], "static_asset_errors": []}], "failures": []}
    events = [
        {"phase": "authenticated_page_probe", "page": page, "path": browser_gate.AUTH_STATUS_PATH,
         "status": 200, "authenticated": True},
        {"phase": "authenticated_page_probe", "page": page, "path": browser_gate.HEALTH_PATH, "status": 200},
        {"phase": "authenticated_page_probe", "page": page, "path": "/api/observability/tasks", "status": 200},
    ]
    return [page], e2e, [probe], events


def test_existing_e2e_context_factory_is_opt_in_and_preserves_default():
    calls = []

    class Browser:
        def new_context(self, **kwargs):
            calls.append(("default", kwargs))
            return "default-context"

    browser = Browser()
    assert smoke._new_context(browser, None, viewport={"width": 10}) == "default-context"
    seen = []

    def factory(got_browser, **kwargs):
        seen.append((got_browser, kwargs))
        return "authenticated-context"

    assert smoke._new_context(browser, factory, viewport={"width": 20}) == "authenticated-context"
    assert calls == [("default", {"viewport": {"width": 10}})]
    assert seen == [(browser, {"viewport": {"width": 20}})]


def test_server_environment_is_isolated_and_real_cli_is_disabled(tmp_path):
    source = {
        "PATH": "C:/Windows/System32",
        "SYSTEMROOT": "C:/Windows",
        "ProgramFiles": "C:/Program Files",
        "ProgramW6432": "C:/Program Files",
        "GW_AUTH_MODE": "oidc",
        "GW_LOCAL_AUTH_DB": "C:/real/auth.sqlite3",
        "GW_CLI_EXECUTION": "1",
        "OPENAI_API_KEY": "must-not-leak",
        "HTTP_PROXY": "https://proxy.invalid",
    }
    env = browser_gate.isolated_server_environment(source, 43121, tmp_path)
    assert env["GW_AUTH_MODE"] == "local_account"
    assert env["GW_PORT"] == "43121"
    assert env["GW_CLI_EXECUTION"] == "0"
    assert env["GW_LOCAL_AUTH_DB"] == str((tmp_path / "auth.sqlite3").resolve())
    assert env["GW_DATA_DIR"] == str((tmp_path / "data").resolve())
    assert "OPENAI_API_KEY" not in env
    assert "HTTP_PROXY" not in env
    # NVML 依赖系统程序目录，测试隔离不得人为把可用硬件变成缺失。
    assert env["ProgramFiles"] == source["ProgramFiles"]
    assert env["ProgramW6432"] == source["ProgramW6432"]


def test_gpu_summary_accepts_real_values_or_explicit_unavailable_only():
    real = browser_gate.summarize_gpu({
        "checks": [{"name": "gpu_telemetry", "status": "ok", "metrics": {
            "gpu_count": 1, "gpu_utilization_percent": 25.5, "gpu_memory_percent": 60.0,
        }}]
    })
    unavailable = browser_gate.summarize_gpu({
        "checks": [{"name": "gpu_telemetry", "status": "not_integrated"}]
    })
    invalid = browser_gate.summarize_gpu({
        "checks": [{"name": "gpu_telemetry", "status": "ok"}]
    })
    assert real["state"] == "real" and real["source"] == "nvidia-smi"
    assert unavailable == {"state": "unavailable", "status": "not_integrated"}
    assert invalid["state"] == "invalid"


def test_authenticated_business_200_and_gpu_degradation_can_pass():
    pages, e2e, probes, events = _passing_rows()
    assert browser_gate.validate_gate(pages, e2e, probes, events, ["mutant-detected"]) == []


def test_all_401_or_missing_business_200_cannot_be_swallowed_as_pass():
    pages, e2e, probes, events = _passing_rows()
    probes[0].update({
        "auth_http_status": 401,
        "authenticated": False,
        "business_api_http_status": 401,
        "business_api_payload_valid": False,
        "gpu_health_http_status": 401,
        "gpu": {"state": "invalid", "status": "unauthorized"},
        "gpu_ui": {"matches_backend": False},
    })
    events = [
        {"phase": "e2e", "page": "projects.html", "path": browser_gate.AUTH_STATUS_PATH,
         "status": 401, "authenticated": False},
        {"phase": "e2e", "page": "projects.html", "path": browser_gate.BUSINESS_PATH.split("?", 1)[0],
         "status": 401},
    ]
    failures = browser_gate.validate_gate(pages, e2e, probes, events, ["mutant-detected"])
    assert any("未证明真实管理员会话 200" in item for item in failures)
    assert any("受保护业务 API 未返回有效 200" in item for item in failures)
    assert any("仍有 API 返回 401" in item for item in failures)


def test_public_auth_summary_never_contains_credentials_or_cookie_values():
    summary = browser_gate._public_auth_summary(
        {"setup_http_status": 201, "login_http_status": 200, "principal_role": "admin",
         "cookie_http_only": True, "cookie_hidden_from_document": True},
        204,
        0,
    )
    serialized = str(summary).lower()
    assert "password" not in serialized
    assert "cookie_value" not in serialized
    assert "token" not in serialized
    assert summary["browser_storage_state_written"] is False
    assert summary["server_session_rows_after_logout"] == 0


def test_ephemeral_port_helper_never_returns_default_2077():
    assert browser_gate.choose_ephemeral_port() != 2077


def test_all_reused_e2e_checks_route_context_creation_through_the_factory():
    tree = ast.parse((ROOT / "tools" / "frontend_e2e_smoke.py").read_text(encoding="utf-8"))
    expected = {
        "run_interactions", "run_topbar_accessibility", "run_shell_route_consistency",
        "run_shell_route_repeat_nav", "run_checks",
    }
    functions = {node.name: node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}
    assert expected <= functions.keys()
    for name in expected:
        function = functions[name]
        assert any(argument.arg == "context_factory" for argument in function.args.args)
        calls = [node for node in ast.walk(function) if isinstance(node, ast.Call)]
        assert any(isinstance(call.func, ast.Name) and call.func.id == "_new_context" for call in calls)
        assert not any(
            isinstance(call.func, ast.Attribute) and call.func.attr == "new_context"
            for call in calls
        )


def test_authenticated_context_factory_injects_http_only_cookie_in_memory():
    class Context:
        def __init__(self):
            self.cookies = []
            self.routes = []
            self.listeners = []
            self.pages = []

        def add_cookies(self, cookies):
            self.cookies.extend(cookies)

        def route(self, pattern, callback):
            self.routes.append((pattern, callback))

        def on(self, event, callback):
            self.listeners.append((event, callback))

    class Browser:
        def __init__(self):
            self.contexts = []

        def new_context(self, **kwargs):
            context = Context()
            context.options = kwargs
            self.contexts.append(context)
            return context

    browser = Browser()
    events = []
    phase = ["e2e"]
    factory = browser_gate.create_authenticated_context_factory(
        "http://127.0.0.1:43121", "opaque-test-session", events, phase
    )
    context = factory(browser, viewport={"width": 1600, "height": 1000})
    cookie = context.cookies[0]
    assert cookie["name"] == "gw_session"
    assert cookie["value"] == "opaque-test-session"
    assert cookie["httpOnly"] is True
    assert cookie["sameSite"] == "Strict"
    assert context.options["viewport"] == {"width": 1600, "height": 1000}
    assert not hasattr(context, "storage_state")


def test_artifact_path_rejects_repository_output_without_writing():
    target = ROOT / "should-not-be-created-by-phase12-gate"
    try:
        browser_gate.choose_artifacts_dir(str(target))
    except ValueError as exc:
        assert "仓库之外" in str(exc)
    else:
        raise AssertionError("门禁必须拒绝将截图/JSON写入仓库")
    assert not target.exists()


def test_api_summary_keeps_status_and_paths_but_not_response_bodies():
    rows = browser_gate.summarize_api_events(("agents.html",), [
        {"phase": "e2e", "page": "agents.html", "method": "POST",
         "path": "/api/chat/agent", "status": 503},
        {"phase": "authenticated_page_probe", "page": "agents.html", "method": "GET",
         "path": browser_gate.AUTH_STATUS_PATH, "status": 200, "authenticated": True},
    ])
    assert rows[0]["successful_paths"] == [browser_gate.AUTH_STATUS_PATH]
    assert rows[0]["failures"] == [{"phase": "e2e", "method": "POST", "path": "/api/chat/agent", "status": 503}]
    assert all("body" not in str(row).lower() for row in rows)


def test_team_workflow_requires_each_runtime_evidence_not_only_pass_label():
    result = {"passed": True, "seeded_messages": 75, "page_errors": [],
              "history_after_incremental_verified": True, "ui_send_verified": True,
              "reload_readback_verified": True, "text_only_rendering_verified": True}
    assert browser_gate.team_message_workflow_verified(result)
    assert not browser_gate.team_message_workflow_verified({"passed": True})
    for key in result:
        mutant = dict(result)
        mutant.pop(key)
        assert not browser_gate.team_message_workflow_verified(mutant), key
    assert not browser_gate.team_message_workflow_verified({**result, "page_errors": ["Error"]})
