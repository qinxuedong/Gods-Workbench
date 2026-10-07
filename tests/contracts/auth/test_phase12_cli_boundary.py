# -*- coding: utf-8 -*-
"""CLI受控边界：只运行隔离的Python夹具，不调用真实厂商CLI或登录账户。"""
from pathlib import Path
import sys
import pytest
from fastapi.testclient import TestClient
from gw.api.app import create_app
from gw.core import cli_runtime
from gw.core.cli_login import LoginController

EDITOR = {"Authorization": "Bearer cleanroom-test", "X-User-Role": "editor"}
GOVERNOR = {"Authorization": "Bearer cleanroom-cli-governor", "X-User-Role": "governor"}


@pytest.mark.parametrize("path", ["/api/codex/help", "/api/gemini-cli/help", "/api/jimeng/help"])
def test_cli_help_real_safe_executable_uses_fixed_arguments(path, monkeypatch):
    monkeypatch.setenv("GW_CLI_EXECUTION", "1")
    monkeypatch.setattr(cli_runtime, "find_cli", lambda protocol: sys.executable)
    with TestClient(create_app()) as client:
        # sys.executable --help 是安全的本地子进程；客户端command不进入argv。
        response = client.post(path, headers=EDITOR, json={"command": "unsafe-client-input"})
        assert response.status_code == 200, response.text
        data = response.json()
        assert data["command"] == "--help" and data["returncode"] == 0
        assert "usage:" in data["text"].lower()
        assert "unsafe-client-input" not in data["text"]


@pytest.mark.parametrize("operation,path,method,script_name", [
    ("credit", "/api/jimeng/credit", "GET", "user_credit"),
    ("logout", "/api/jimeng/logout", "POST", "logout"),
])
def test_cli_short_command_nonzero_fails_without_leaking_output(operation, path, method, script_name, tmp_path, monkeypatch):
    monkeypatch.setenv("GW_CLI_EXECUTION", "1")
    monkeypatch.setattr(cli_runtime, "find_cli", lambda protocol: sys.executable)
    monkeypatch.setattr(cli_runtime, "login_controller", LoginController())
    monkeypatch.chdir(tmp_path)
    # Python把固定子命令当作脚本路径；没有真实Dreamina进程、凭据或网络。
    (tmp_path / script_name).write_text("import sys; print('fixture-private-error'); sys.exit(7)", encoding="utf-8")
    with TestClient(create_app()) as client:
        response = client.request(method, path, headers=GOVERNOR)
        assert response.status_code == 503, response.text
        assert response.json()["detail"]["code"] == "CLI_COMMAND_FAILED"
        assert response.json()["detail"]["returncode"] == 7
        assert response.json()["detail"]["result_unknown"] is True
        assert response.json()["detail"]["data_status"] == "failed"
        assert "fixture-private-error" not in response.text


def test_cli_disabled_start_is_fail_closed_and_does_not_spawn(tmp_path, monkeypatch):
    monkeypatch.delenv("GW_CLI_EXECUTION", raising=False)
    monkeypatch.setattr(cli_runtime, "find_cli", lambda protocol: pytest.fail("未启用时不得查找或启动CLI"))
    monkeypatch.setattr(cli_runtime, "login_controller", LoginController())
    with TestClient(create_app()) as client:
        response = client.post("/api/jimeng/login/start", headers=GOVERNOR)
        assert response.status_code == 503
        assert response.json()["detail"]["code"] == "JIMENG_LOGIN_NOT_INTEGRATED"
        assert "data_status" in response.json()["detail"]
        assert "logged_in" not in response.text and "qr_url" not in response.text
