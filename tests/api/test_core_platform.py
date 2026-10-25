import shutil

import pytest

from gw.core.errors import CleanroomException
from gw.core.platform import (
    AI_UPLOAD_NOT_INTEGRATED,
    CHAT_NOT_INTEGRATED,
    CLI_HELP_NOT_INTEGRATED,
    JIMENG_CREDIT_NOT_INTEGRATED,
    JIMENG_LOGIN_NOT_INTEGRATED,
    _find_cli_path,
    app_info,
    cli_status,
    reject_chat,
    reject_cli_help,
    reject_jimeng_credit,
    reject_jimeng_login,
    reject_upload,
)


def test_find_cli_path(monkeypatch):
    def mock_which(name):
        if name == "found_cmd":
            return "/usr/bin/found_cmd"
        return None
    monkeypatch.setattr(shutil, "which", mock_which)

    assert _find_cli_path(["missing_cmd"]) is None
    assert _find_cli_path(["missing_cmd", "found_cmd"]) == "/usr/bin/found_cmd"

def test_cli_status_installed(monkeypatch):
    def mock_which(name):
        return f"/mock/path/{name}"
    monkeypatch.setattr(shutil, "which", mock_which)
    monkeypatch.setattr("gw.core.platform.cli_execution_enabled", lambda: True)

    status = cli_status("codex")
    assert status["protocol"] == "codex"
    assert status["installed"] is True
    assert status["path"].startswith("/mock/path/")
    assert status["execution_enabled"] is True
    assert status["data_status"] == "observed"
    assert "cli_execution_disabled" not in status["data_gaps"]

def test_cli_status_not_installed(monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda name: None)
    monkeypatch.setattr("gw.core.platform.cli_execution_enabled", lambda: False)

    status = cli_status("jimeng")
    assert status["protocol"] == "jimeng"
    assert status["installed"] is False
    assert status["path"] is None
    assert status["execution_enabled"] is False
    assert status["data_status"] == "not_integrated"
    assert "cli_execution_disabled" in status["data_gaps"]

def test_app_info(monkeypatch):
    class MockAuth:
        mode = "test_mode"
    monkeypatch.setattr("gw.core.platform.load_runtime_auth_config", lambda: MockAuth())
    info = app_info()
    assert isinstance(info, dict)
    assert "name" in info
    assert "version" in info

def test_reject_upload():
    with pytest.raises(CleanroomException) as excinfo:
        reject_upload()
    assert excinfo.value.code == AI_UPLOAD_NOT_INTEGRATED
    assert excinfo.value.status_code == 503
    assert excinfo.value.extra["endpoint"] == "/api/ai/upload"
    assert excinfo.value.message == "附件上传尚未接入真实资产存储，已拒绝请求；文件内容未被读取或保存。"

def test_reject_chat():
    with pytest.raises(CleanroomException) as excinfo:
        reject_chat("/fake/endpoint")
    assert excinfo.value.code == CHAT_NOT_INTEGRATED
    assert excinfo.value.status_code == 503
    assert excinfo.value.extra["endpoint"] == "/fake/endpoint"
    assert excinfo.value.message == "对话 Provider 尚未完成准入，已按洁净室口径拒绝执行；消息未发送到外部服务。"

def test_reject_cli_help():
    with pytest.raises(CleanroomException) as excinfo:
        reject_cli_help("/fake/help")
    assert excinfo.value.code == CLI_HELP_NOT_INTEGRATED
    assert excinfo.value.status_code == 503
    assert excinfo.value.extra["endpoint"] == "/fake/help"
    assert excinfo.value.message == "CLI 帮助执行尚未完成准入，已拒绝执行命令；未启动外部进程。"

def test_reject_jimeng_credit():
    with pytest.raises(CleanroomException) as excinfo:
        reject_jimeng_credit()
    assert excinfo.value.code == JIMENG_CREDIT_NOT_INTEGRATED
    assert excinfo.value.status_code == 503
    assert excinfo.value.extra["endpoint"] == "/api/jimeng/credit"
    assert excinfo.value.message == "即梦余额查询尚未接入真实 CLI 会话，未返回伪造余额。"

def test_reject_jimeng_login():
    with pytest.raises(CleanroomException) as excinfo:
        reject_jimeng_login("/fake/login")
    assert excinfo.value.code == JIMENG_LOGIN_NOT_INTEGRATED
    assert excinfo.value.status_code == 503
    assert excinfo.value.extra["endpoint"] == "/fake/login"
    assert excinfo.value.message == "即梦 CLI 登录尚未完成准入，未创建或修改任何登录会话。"
