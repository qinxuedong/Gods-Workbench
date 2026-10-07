# -*- coding: utf-8 -*-
"""Phase 11 B2 平台、项目兼容路径与 AI/CLI 回归契约测试。"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from gw.api.app import create_app
from gw.core import platform as platform_service

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
CATALOG = REPO_ROOT / "docs" / "contracts" / "PLATFORM-INTERFACE-CATALOG.yaml"
FIXTURES = REPO_ROOT / "docs" / "fixtures"
AUTH = {"Authorization": "Bearer cleanroom-test", "X-User-Role": "editor"}
GOVERNOR = {"Authorization": "Bearer cli-governance-test", "X-User-Role": "governor"}
READONLY = {"Authorization": "Bearer cleanroom-test", "X-User-Role": "readonly"}

EXPECTED_PAIRS = {
    ("POST", "/api/ai/upload"),
    ("GET", "/api/app-info"),
    ("GET", "/api/chat/config"),
    ("POST", "/api/chat"),
    ("POST", "/api/chat/agent"),
    ("POST", "/api/codex/help"),
    ("GET", "/api/codex/status"),
    ("POST", "/api/gemini-cli/help"),
    ("GET", "/api/gemini-cli/status"),
    ("GET", "/api/jimeng/credit"),
    ("POST", "/api/jimeng/help"),
    ("POST", "/api/jimeng/login/start"),
    ("GET", "/api/jimeng/login/status"),
    ("POST", "/api/jimeng/logout"),
    ("GET", "/api/jimeng/status"),
    ("GET", "/api/projects"),
    ("GET", "/api/projects/{project_id}"),
}


@pytest.fixture()
def api_client(tmp_path, monkeypatch) -> TestClient:
    """每个用例使用隔离运行根与黄金项目，不依赖本机开发数据。"""
    monkeypatch.setenv("GW_RUNTIME_MODE", "test")
    monkeypatch.setenv("GW_AUTH_MODE", "local")
    monkeypatch.setenv("GW_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("GW_LOCAL_AUTH_DB", str(tmp_path / "auth" / "auth.sqlite3"))
    monkeypatch.setenv("GW_VIDEO_DATA_DIR", str(tmp_path / "video"))

    from gw.api import routes_projects
    from gw.projects_hub.service import ProjectsService

    monkeypatch.setattr(routes_projects, "default_projects_service", ProjectsService())
    with TestClient(create_app()) as client:
        yield client


def _fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def test_catalog_declares_exact_b2_surface():
    """契约声明恰好覆盖 B2 与 Phase 12 R3 的配置读取接口。"""
    pairs = set(re.findall(r"method:\s*(\w+)\s*\n\s*path:\s*(\S+)", CATALOG.read_text(encoding="utf-8")))
    assert pairs == EXPECTED_PAIRS


def test_b2_fixture_examples_are_present():
    # Phase 2 黄金夹具清单属于冻结输入，本阶段只新增独立 B2 夹具，不改写清单哈希。
    for name in (
        "phase11-b2-app-info.json",
        "phase11-b2-chat-not-integrated.json",
        "phase11-b2-cli-observation.json",
        "phase11-b2-project-compat-list.json",
    ):
        assert (FIXTURES / name).is_file()


def test_app_info_is_truthful_and_authenticated(api_client: TestClient):
    assert api_client.get("/api/app-info").status_code == 401
    response = api_client.get("/api/app-info", headers=AUTH)
    assert response.status_code == 200
    data = response.json()
    assert data["runtime"] == "cleanroom"
    assert data["version"] == _fixture("phase11-b2-app-info.json")["version"]
    assert data["release_authorized"] is True
    assert data["data_status"] == "ok"


def test_cli_status_observes_path_without_execution(api_client: TestClient, monkeypatch):
    from gw.core import cli_runtime
    from gw.core.cli_login import LoginController
    monkeypatch.setattr(platform_service, "_find_cli_path", lambda candidates: None)
    monkeypatch.setattr(cli_runtime, "login_controller", LoginController())
    for path, protocol in (("/api/codex/status", "codex"), ("/api/gemini-cli/status", "gemini-cli")):
        response = api_client.get(path, headers=AUTH)
        assert response.status_code == 200
        data = response.json()
        assert data["protocol"] == protocol
        assert data["installed"] is False
        assert data["path"] is None
        assert data["version"] is None
        assert data["execution_enabled"] is False
        assert data["data_status"] == "not_integrated"
    jimeng = api_client.get("/api/jimeng/status", headers=GOVERNOR)
    assert jimeng.status_code == 200
    data = jimeng.json()
    assert data["protocol"] == "jimeng"
    assert data["logged_in"] is None and data["running"] is False
    assert data["state"] == "unknown" and data["data_status"] == "not_integrated"


def test_jimeng_login_status_starts_unknown_and_never_fakes_qr(api_client: TestClient, monkeypatch):
    from gw.core import cli_runtime
    from gw.core.cli_login import LoginController
    monkeypatch.setattr(platform_service, "_find_cli_path", lambda candidates: None)
    monkeypatch.setattr(cli_runtime, "login_controller", LoginController())
    response = api_client.get("/api/jimeng/login/status", headers=GOVERNOR)
    assert response.status_code == 200
    data = response.json()
    assert data["logged_in"] is None
    assert data["result_unknown"] is True
    assert data["running"] is False
    assert data["state"] == "unknown"
    assert data["verification_uri"] is None and data["user_code"] is None
    assert "qr_url" not in data and "raw" not in data
    assert response.headers["cache-control"].startswith("no-store")


def test_chat_configuration_is_read_only_and_authenticated(api_client: TestClient, monkeypatch):
    """配置状态 GET 要求认证且不访问 Provider 网络。"""
    monkeypatch.delenv("GW_PROVIDER_RUNTIME_JSON", raising=False)
    monkeypatch.delenv("GW_CHAT_BASE_URL", raising=False)
    monkeypatch.delenv("GW_CHAT_API_KEY", raising=False)
    assert api_client.get("/api/chat/config").status_code == 401
    response = api_client.get("/api/chat/config", headers=READONLY)
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["configured"] is False
    assert data["providers"] == []
    serialized = json.dumps(data, ensure_ascii=False)
    assert "api_key" not in serialized and "api_key_env" not in serialized and "base_url" not in serialized


def test_external_actions_fail_closed_without_leaking_payload(api_client: TestClient, monkeypatch):
    """GW_CLI_EXECUTION 未启用时 CLI 与对话必须 503，且不回显 payload。"""
    monkeypatch.delenv("GW_CLI_EXECUTION", raising=False)
    calls = [
        ("post", "/api/chat", {"json": {"message": "绝密提示词"}}),
        ("post", "/api/chat/agent", {"json": {"message": "绝密提示词", "agent_id": "a"}}),
        ("post", "/api/codex/help", {"json": {"command": "--help"}}),
        ("post", "/api/gemini-cli/help", {"json": {"command": "--help"}}),
        ("get", "/api/jimeng/credit", {}),
        ("post", "/api/jimeng/help", {"json": {"command": "--help"}}),
        ("post", "/api/jimeng/login/start", {}),
        ("post", "/api/jimeng/logout", {}),
    ]
    expected = {
        "/api/chat": "CHAT_NOT_INTEGRATED",
        "/api/chat/agent": "CHAT_NOT_INTEGRATED",
        "/api/codex/help": "CLI_HELP_NOT_INTEGRATED",
        "/api/gemini-cli/help": "CLI_HELP_NOT_INTEGRATED",
        "/api/jimeng/credit": "JIMENG_CREDIT_NOT_INTEGRATED",
        "/api/jimeng/help": "CLI_HELP_NOT_INTEGRATED",
        "/api/jimeng/login/start": "JIMENG_LOGIN_NOT_INTEGRATED",
        "/api/jimeng/logout": "JIMENG_LOGIN_NOT_INTEGRATED",
    }
    for method, path, kwargs in calls:
        headers = GOVERNOR if path in {"/api/jimeng/credit", "/api/jimeng/login/start", "/api/jimeng/logout"} else AUTH
        response = getattr(api_client, method)(path, headers=headers, **kwargs)
        assert response.status_code == 503, (method, path, response.text)
        detail = response.json()["detail"]
        assert detail["code"] == expected[path]
        assert detail["endpoint"] == path
        assert detail["unavailable"] is True
        assert detail["data_status"] == "not_integrated"
        assert "绝密提示词" not in response.text


def test_ai_upload_persists_real_file(api_client: TestClient):
    """上传必须真实落盘并可经 download-output 回读。"""
    import os
    response = api_client.post(
        "/api/ai/upload", headers=AUTH,
        files={"files": ("note.txt", b"cleanroom-upload", "text/plain")},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["count"] == 1
    record = body["files"][0]
    assert record["file_id"] == "upl_0001"
    assert record["size_bytes"] == len(b"cleanroom-upload")
    real = os.path.join(os.environ["GW_DATA_DIR"], record["display_path"])
    assert os.path.isfile(real)
    fetched = api_client.get("/api/download-output", headers=AUTH,
                             params={"path": record["display_path"]})
    assert fetched.status_code == 200
    assert fetched.content == b"cleanroom-upload"


def test_cli_help_executes_when_gated(api_client: TestClient, monkeypatch):
    """GW_CLI_EXECUTION=1 且命令存在时真实执行固定 --help，返回真实输出。"""
    monkeypatch.setenv("GW_CLI_EXECUTION", "1")
    monkeypatch.setattr("gw.core.cli_runtime.find_cli", lambda protocol: "C:/fake/codex.exe")
    captured = {}

    class Done:
        returncode = 0
        stdout = "Usage: codex [options]"
        stderr = ""

    def fake_run(message):
        return Done()

    def fake_run_real(argv, **kwargs):
        captured["argv"] = argv
        return Done()

    import gw.core.cli_runtime as runtime
    monkeypatch.setattr(runtime, "find_cli", lambda protocol: "C:/fake/codex.exe")
    monkeypatch.setattr(runtime.subprocess, "run", fake_run_real)
    response = api_client.post("/api/codex/help", headers=AUTH, json={"command": "rm -rf /"})
    assert response.status_code == 200, response.text
    assert response.json()["text"] == "Usage: codex [options]"
    # 调用方命令文本绝不进入 argv；argv 只有固定参数。
    assert captured["argv"] == ["C:/fake/codex.exe", "--help"]


def test_external_actions_require_authentication(api_client: TestClient):
    assert api_client.post("/api/chat", json={"message": "x"}).status_code == 401
    assert api_client.get("/api/jimeng/status").status_code == 401
    assert api_client.post("/api/jimeng/login/start").status_code == 401


def test_readonly_does_not_get_external_write_execution(api_client: TestClient):
    response = api_client.post("/api/chat", json={"message": "x"}, headers=READONLY)
    assert response.status_code == 403
    response = api_client.post("/api/jimeng/login/start", headers=READONLY)
    assert response.status_code == 403
    response = api_client.get("/api/jimeng/credit", headers=AUTH)
    assert response.status_code == 403
    upload = api_client.post("/api/ai/upload", headers=READONLY,
                             files={"files": ("x.txt", b"x", "text/plain")})
    assert upload.status_code == 403


def test_compat_project_paths_share_projects_hub_truth(api_client: TestClient):
    response = api_client.get("/api/projects", headers=AUTH)
    assert response.status_code == 200
    projects = response.json()["projects"]
    fixture_projects = _fixture("phase11-b2-project-compat-list.json")["projects"]
    assert projects
    assert projects[0]["project_id"] == fixture_projects[0]["project_id"]
    assert projects[0]["version"] == fixture_projects[0]["version"]
    project_id = projects[0]["project_id"]
    detail = api_client.get(f"/api/projects/{project_id}", headers=AUTH)
    assert detail.status_code == 200
    assert detail.json()["project"]["project_id"] == project_id
    missing = api_client.get("/api/projects/does-not-exist", headers=AUTH)
    assert missing.status_code == 404
    assert missing.json()["detail"]["code"] == "PROJECT_NOT_FOUND"


def test_compat_project_paths_require_authentication(api_client: TestClient):
    assert api_client.get("/api/projects").status_code == 401
    assert api_client.get("/api/projects/prj-0001").status_code == 401
