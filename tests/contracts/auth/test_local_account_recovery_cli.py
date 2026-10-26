"""本机密码恢复命令契约。"""

import pytest
from fastapi.testclient import TestClient

from gw.api.app import app
from gw.core.errors import CleanroomException


@pytest.fixture
def local_auth_db(monkeypatch, tmp_path):
    monkeypatch.setenv("GW_AUTH_MODE", "local_account")
    monkeypatch.setenv("GW_LOCAL_AUTH_DB", str(tmp_path / "auth.sqlite3"))
    from gw.core import local_accounts

    principal, old_session = local_accounts.create_first_admin("owner", "Test2026")
    old_codes = local_accounts.ensure_recovery_codes(principal["user_id"])
    return local_accounts, old_codes, old_session


def test_cli_lists_accounts_resets_password_and_reissues_codes(
    local_auth_db, monkeypatch, capsys
):
    local_accounts, old_codes, old_session = local_auth_db
    import getpass

    from gw.core import local_accounts_cli

    answers = iter(["owner"])
    passwords = iter(["Reset2026", "Reset2026"])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(answers))
    monkeypatch.setattr(getpass, "getpass", lambda prompt="": next(passwords))

    assert local_accounts_cli.main(["reset-password"]) == 0
    output = capsys.readouterr().out
    assert "owner" in output
    new_codes = [line.strip() for line in output.splitlines() if line.startswith("RC-")]
    assert len(new_codes) == 8 and len(set(new_codes)) == 8
    assert not set(old_codes).intersection(new_codes)
    assert local_accounts.login("owner", "Reset2026", "cli-test")
    assert local_accounts.get_session(old_session) is None
    with pytest.raises(CleanroomException):
        local_accounts.login("owner", "Test2026", "cli-old-password")

    with TestClient(
        app,
        base_url="http://localhost",
        client=("127.0.0.1", 50000),
        headers={"Origin": "http://localhost"},
    ) as client:
        # 本机命令不是 HTTP 路由，网络请求没有可调用的 CLI reset endpoint。
        assert client.post(
            "/api/asset-auth/local/cli-reset-password",
            json={"username": "owner", "new_password": "Other2026"},
        ).status_code == 404


def test_cli_refuses_unknown_account_and_password_mismatch(local_auth_db, monkeypatch):
    import getpass

    from gw.core import local_accounts_cli

    monkeypatch.setattr("builtins.input", lambda prompt="": "missing")
    monkeypatch.setattr(getpass, "getpass", lambda prompt="": "Reset2026")
    assert local_accounts_cli.main(["reset-password"]) == 1

    monkeypatch.setattr("builtins.input", lambda prompt="": "owner")
    passwords = iter(["Reset2026", "Other2026"])
    monkeypatch.setattr(getpass, "getpass", lambda prompt="": next(passwords))
    assert local_accounts_cli.main(["reset-password"]) == 1
