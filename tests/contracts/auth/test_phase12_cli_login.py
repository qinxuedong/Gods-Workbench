# -*- coding: utf-8 -*-
"""Dreamina CLI闭环：只调用仓库外受控Python夹具，不访问真实账户。"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from gw.api.app import create_app
from gw.core import cli_login, cli_runtime
from gw.core.cli_login import LoginController

GOVERNOR = {"Authorization": "Bearer dreamina-governor-a", "X-User-Role": "governor"}
OTHER_GOVERNOR = {"Authorization": "Bearer dreamina-governor-b", "X-User-Role": "governor"}
EDITOR = {"Authorization": "Bearer dreamina-editor", "X-User-Role": "editor"}


def _configure_cli(tmp_path: Path, monkeypatch, timeout: float = 5.0) -> LoginController:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("GW_CLI_EXECUTION", "1")
    monkeypatch.setenv("GW_DREAMINA_VERIFICATION_HOSTS", "auth.example.test")
    monkeypatch.setattr(cli_runtime, "find_cli", lambda protocol: sys.executable if protocol == "jimeng" else None)
    controller = LoginController(login_timeout_seconds=timeout)
    monkeypatch.setattr(cli_runtime, "login_controller", controller)
    return controller


def _script(tmp_path: Path, name: str, source: str) -> Path:
    path = tmp_path / name
    path.write_text(source, encoding="utf-8")
    return path


def _wait_status(client: TestClient, headers=GOVERNOR, timeout: float = 3.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        response = client.get("/api/jimeng/login/status", headers=headers)
        assert response.status_code == 200, response.text
        state = response.json()
        if not state["running"]:
            return state
        time.sleep(0.025)
    raise AssertionError("受控 CLI 夹具未在等待期限内结束")


def test_candidate_and_balance_argv_are_narrow_and_fixed():
    assert tuple(cli_runtime.CLI_CANDIDATES["jimeng"]) == ("dreamina",)
    assert cli_runtime.JIMENG_ARGV["credit"] == ("user_credit",)
    assert cli_runtime.JIMENG_ARGV["login"] == ("login",)
    assert cli_runtime.JIMENG_ARGV["logout"] == ("logout",)


@pytest.mark.parametrize("candidate,expected", [
    ("verification_uri: https://auth.example.test/device\nuser_code: ABCD-1234", ("https://auth.example.test/device", "ABCD-1234")),
    ("visit https://auth.example.test/device\nuser_code: ABCD-1234", (None, None)),
    ("verification_uri: https://other.example.test/device\nuser_code: ABCD-1234", (None, None)),
    ("verification_uri: https://auth.example.test/device?token=x\nuser_code: ABCD-1234", (None, None)),
    ("verification_uri: https://user@auth.example.test/device\nuser_code: ABCD-1234", (None, None)),
    ("verification_uri_complete: https://auth.example.test/device\nuser_code: ABCD-1234", (None, None)),
])
def test_only_named_allowlisted_https_authorization_fields_are_parsed(monkeypatch, candidate, expected):
    monkeypatch.setenv("GW_DREAMINA_VERIFICATION_HOSTS", "auth.example.test")
    assert cli_login.parse_authorization_material(candidate.encode("utf-8")) == expected


def test_initial_and_restarted_observation_are_unknown_and_never_cached(tmp_path, monkeypatch):
    controller = _configure_cli(tmp_path, monkeypatch)
    with TestClient(create_app()) as client:
        response = client.get("/api/jimeng/login/status", headers=GOVERNOR)
        assert response.status_code == 200
        first = response.json()
        assert first["state"] == "unknown" and first["logged_in"] is None
        assert first["result_unknown"] is True and first["running"] is False
        assert first["verification_uri"] is None and first["user_code"] is None
        assert response.headers["cache-control"].startswith("no-store")
        assert response.headers["pragma"] == "no-cache"
    restarted = LoginController()
    monkeypatch.setattr(cli_runtime, "login_controller", restarted)
    with TestClient(create_app()) as client:
        after_restart = client.get("/api/jimeng/status", headers=GOVERNOR).json()
        assert after_restart["state"] == "unknown"
        assert after_restart["logged_in"] is None
    controller.shutdown()
    restarted.shutdown()


def test_governance_permissions_cover_shared_account_operations(tmp_path, monkeypatch):
    controller = _configure_cli(tmp_path, monkeypatch)
    try:
        with TestClient(create_app()) as client:
            assert client.get("/api/jimeng/login/status").status_code == 401
            assert client.get("/api/jimeng/credit", headers=EDITOR).status_code == 403
            assert client.post("/api/jimeng/login/start", headers=EDITOR).status_code == 403
            assert client.post("/api/jimeng/logout", headers=EDITOR).status_code == 403
            # 即使治理角色不同，主体绑定后也不可读取上一主体的授权材料。
            _script(tmp_path, "login", "import time\nprint('verification_uri: https://auth.example.test/device', flush=True)\nprint('user_code: ABCD-1234', flush=True)\ntime.sleep(0.5)\n")
            started = client.post("/api/jimeng/login/start", headers=GOVERNOR)
            assert started.status_code == 200, started.text
            assert client.get("/api/jimeng/login/status", headers=OTHER_GOVERNOR).status_code == 403
            assert client.get("/api/jimeng/status", headers=OTHER_GOVERNOR).status_code == 403
            assert client.post("/api/jimeng/login/start", headers=OTHER_GOVERNOR).status_code == 409
    finally:
        controller.shutdown()


def test_login_is_idempotent_parses_only_safe_fields_and_clears_terminal_material(tmp_path, monkeypatch):
    controller = _configure_cli(tmp_path, monkeypatch)
    invocation = tmp_path / "invocations.txt"
    _script(tmp_path, "login", (
        "import pathlib, time\n"
        f"pathlib.Path({str(invocation)!r}).open('a', encoding='utf-8').write('start\\n')\n"
        "print('device_code: PRIVATE_DEVICE_SECRET', flush=True)\n"
        "print('https://untrusted.example.test/not-a-field', flush=True)\n"
        "print('verification_uri: https://auth.example.test/device', flush=True)\n"
        "print('user_code: ABCD-1234', flush=True)\n"
        "time.sleep(0.35)\n"
        "print('cookie: PRIVATE_COOKIE_SECRET', flush=True)\n"
        "time.sleep(0.1)\n"
    ))
    try:
        with TestClient(create_app()) as client:
            started = client.post("/api/jimeng/login/start", headers=GOVERNOR)
            assert started.status_code == 200, started.text
            assert "PRIVATE_DEVICE_SECRET" not in started.text and "PRIVATE_COOKIE_SECRET" not in started.text
            assert "raw" not in started.json() and "qr_url" not in started.json()
            repeated = client.post("/api/jimeng/login/start", headers=GOVERNOR)
            assert repeated.status_code == 200, repeated.text
            deadline = time.monotonic() + 1.0
            while not invocation.exists() and time.monotonic() < deadline:
                time.sleep(0.01)
            assert invocation.read_text(encoding="utf-8").splitlines() == ["start"]
            observed = client.get("/api/jimeng/login/status", headers=GOVERNOR)
            assert observed.status_code == 200
            if observed.json()["running"]:
                assert observed.json()["verification_uri"] == "https://auth.example.test/device"
                assert observed.json()["user_code"] == "ABCD-1234"
                assert observed.headers["cache-control"].startswith("no-store")
            final = _wait_status(client)
            assert final["logged_in"] is True
            assert final["last_operation"] == "login"
            assert final["observed_at"] and final["source"] == "last_operation"
            assert final["verification_uri"] is None and final["user_code"] is None
            assert final["returncode"] == 0
    finally:
        controller.shutdown()


def test_nonzero_login_after_success_is_unknown_not_false(tmp_path, monkeypatch):
    controller = _configure_cli(tmp_path, monkeypatch)
    login_file = tmp_path / "login"
    try:
        login_file.write_text("import time\ntime.sleep(0.12)\n", encoding="utf-8")
        with TestClient(create_app()) as client:
            first = client.post("/api/jimeng/login/start", headers=GOVERNOR)
            assert first.status_code == 200
            successful = _wait_status(client)
            assert successful["logged_in"] is True
            login_file.write_text("import sys\nprint('PRIVATE_FAIL_OUTPUT')\nsys.exit(9)\n", encoding="utf-8")
            failed_start = client.post("/api/jimeng/login/start", headers=GOVERNOR)
            assert failed_start.status_code == 200
            failed = _wait_status(client)
            assert failed["last_operation"] == "login"
            assert failed["last_operation_failed"] is True
            assert failed["logged_in"] is None and failed["result_unknown"] is True
            assert failed["state"] == "operation_failed"
            assert failed["returncode"] == 9
            assert "PRIVATE_FAIL_OUTPUT" not in failed_start.text
    finally:
        controller.shutdown()


def test_timeout_and_unterminated_oversized_output_are_actively_reaped(tmp_path, monkeypatch):
    controller = _configure_cli(tmp_path, monkeypatch, timeout=0.12)
    try:
        _script(tmp_path, "login", "import time\nprint('verification_uri: https://auth.example.test/device')\nprint('user_code: ABCD-1234', flush=True)\ntime.sleep(30)\n")
        with TestClient(create_app()) as client:
            assert client.post("/api/jimeng/login/start", headers=GOVERNOR).status_code == 200
            timed_out = _wait_status(client)
            assert timed_out["state"] == "result_unknown"
            assert timed_out["logged_in"] is None and timed_out["result_unknown"] is True
            assert timed_out["verification_uri"] is None and timed_out["user_code"] is None
            assert timed_out["returncode"] is not None and controller._process is None

        oversized = LoginController(login_timeout_seconds=5)
        monkeypatch.setattr(cli_runtime, "login_controller", oversized)
        _script(tmp_path, "login", "import os,time\nos.write(1, b'x' * 65537)\ntime.sleep(30)\n")
        with TestClient(create_app()) as client:
            assert client.post("/api/jimeng/login/start", headers=GOVERNOR).status_code == 200
            result = _wait_status(client)
            assert result["state"] == "output_limit_exceeded"
            assert result["logged_in"] is None and result["result_unknown"] is True
            assert result["verification_uri"] is None and result["user_code"] is None
            assert result["returncode"] is not None and oversized._process is None
        oversized.shutdown()
    finally:
        controller.shutdown()


def test_logout_conflicts_with_running_login_and_success_is_the_only_false_observation(tmp_path, monkeypatch):
    controller = _configure_cli(tmp_path, monkeypatch)
    try:
        _script(tmp_path, "login", "import time\ntime.sleep(0.5)\n")
        with TestClient(create_app()) as client:
            started = client.post("/api/jimeng/login/start", headers=GOVERNOR)
            assert started.status_code == 200
            conflict = client.post("/api/jimeng/logout", headers=GOVERNOR)
            assert conflict.status_code == 409
            assert conflict.json()["detail"]["code"] == "CLI_OPERATION_IN_PROGRESS"
            assert controller._process is not None and controller._process.poll() is None
            _wait_status(client)
            _script(tmp_path, "logout", "print('logout fixture complete')\n")
            response = client.post("/api/jimeng/logout", headers=GOVERNOR)
            assert response.status_code == 200, response.text
            body = response.json()
            assert body["logged_in"] is False and body["last_operation"] == "logout"
            observed = client.get("/api/jimeng/status", headers=GOVERNOR).json()
            assert observed["logged_in"] is False and observed["state"] == "logged_out"
            assert observed["verification_uri"] is None and observed["user_code"] is None
            assert response.headers["cache-control"].startswith("no-store")
    finally:
        controller.shutdown()


def test_immediate_login_exit_records_only_the_observed_returncode(tmp_path, monkeypatch):
    controller = _configure_cli(tmp_path, monkeypatch)
    _script(tmp_path, "login", "import sys\nprint('PRIVATE_LOGIN_OUTPUT')\nsys.exit(0)\n")
    try:
        with TestClient(create_app()) as client:
            started = client.post("/api/jimeng/login/start", headers=GOVERNOR)
            assert started.status_code == 200, started.text
            final = _wait_status(client)
            assert final["running"] is False and final["returncode"] == 0
            assert final["logged_in"] is True and final["last_operation"] == "login"
            assert final["verification_uri"] is None and final["user_code"] is None
            assert "PRIVATE_LOGIN_OUTPUT" not in started.text
    finally:
        controller.shutdown()


def test_application_shutdown_reaps_the_exact_managed_process(tmp_path, monkeypatch):
    controller = _configure_cli(tmp_path, monkeypatch)
    _script(tmp_path, "login", "import time\ntime.sleep(30)\n")
    with TestClient(create_app()) as client:
        response = client.post("/api/jimeng/login/start", headers=GOVERNOR)
        assert response.status_code == 200
        process = controller._process
        assert process is not None and process.poll() is None
    assert process.poll() is not None
    assert controller._process is None


def test_credit_uses_real_fixed_user_credit_and_never_marks_login(tmp_path, monkeypatch):
    controller = _configure_cli(tmp_path, monkeypatch)
    _script(tmp_path, "user_credit", "import json\nprint(json.dumps({'user_credit': 17}))\n")
    try:
        with TestClient(create_app()) as client:
            response = client.get("/api/jimeng/credit", headers=GOVERNOR)
            assert response.status_code == 200, response.text
            assert response.json()["command"] == "user_credit"
            assert response.json()["returncode"] == 0
            assert response.json()["raw"] == {"user_credit": 17}
            status = client.get("/api/jimeng/login/status", headers=GOVERNOR).json()
            assert status["logged_in"] is None and status["state"] == "unknown"
            assert response.headers["cache-control"].startswith("no-store")
    finally:
        controller.shutdown()


def test_old_generation_callback_cannot_overwrite_current_state_and_windows_hides_console(monkeypatch):
    controller = LoginController()
    monkeypatch.setattr(cli_login.os, "name", "nt")
    monkeypatch.setattr(cli_login.subprocess, "CREATE_NO_WINDOW", 0x08000000, raising=False)
    assert cli_login._popen_options()["creationflags"] == 0x08000000
    # 代次旧回调即便迟到，也不能覆盖当前观测或发布授权材料。
    current_process = object()
    with controller._lock:
        controller._generation = 2
        controller._owner = "subject-a"
        controller._process = current_process
        controller._state = {
            **controller._unknown_state(), "running": True, "last_operation": "login",
            "state": "running", "logged_in": None, "result_unknown": True,
        }
    monkeypatch.setenv("GW_DREAMINA_VERIFICATION_HOSTS", "auth.example.test")
    controller._publish_material(1, object(), b"verification_uri: https://auth.example.test/device\nuser_code: ABCD-1234")
    controller._finalize(1, object(), 0)
    assert controller._state["running"] is True and controller._state["logged_in"] is None
    controller._closed = True


def test_frontend_has_confirmation_serial_poll_and_page_leave_cleanup():
    root = Path(__file__).resolve().parents[3]
    html = (root / "web/embeds/api-settings.html").read_text(encoding="utf-8")
    script = (root / "web/js/modules/api-settings.js").read_text(encoding="utf-8")
    assert "服务器本机共享账户" in html
    assert "jimengSharedActionConfirmed('登录')" in script
    assert "jimengSharedActionConfirmed('退出登录')" in script
    assert "jimengPollAbort" in script and "new AbortController()" in script
    assert "window.addEventListener('pagehide', stopJimengWaitingOnPageLeave)" in script
    assert "scheduleJimengPoll(generation)" in script
    assert "qr_url" not in script and "jimeng-qr-img" not in script
    assert "data.running === true" in script
    assert "登录状态未知" in script or "账户状态未知" in script
