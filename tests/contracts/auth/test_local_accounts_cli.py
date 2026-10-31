"""本地账户初始化 CLI 契约。"""

import getpass

import pytest


@pytest.fixture
def local_accounts_store(monkeypatch, tmp_path):
    monkeypatch.setenv("GW_AUTH_MODE", "local_account")
    monkeypatch.setenv("GW_LOCAL_AUTH_DB", str(tmp_path / "auth.sqlite3"))
    from gw.core import local_accounts

    return local_accounts


def test_setup_admin_creates_first_admin_and_prints_one_time_codes(
    local_accounts_store, monkeypatch, capsys
):
    from gw.core import local_accounts_cli

    answers = iter(["Owner"])
    passwords = iter(["Secure2026", "Secure2026"])
    monkeypatch.setattr("builtins.input", lambda _prompt="": next(answers))
    monkeypatch.setattr(getpass, "getpass", lambda _prompt="": next(passwords))

    created = []
    create_first_admin = local_accounts_store.create_first_admin

    def record_session(username, password):
        principal, token = create_first_admin(username, password)
        created.append(token)
        return principal, token

    monkeypatch.setattr(local_accounts_store, "create_first_admin", record_session)

    assert local_accounts_cli.main(["setup-admin"]) == 0
    output = capsys.readouterr().out
    recovery_codes = [line for line in output.splitlines() if line.startswith("RC-")]

    assert "管理员创建成功" in output
    assert local_accounts_store.list_accounts() == ["owner"]
    assert len(recovery_codes) == 8
    assert len(set(recovery_codes)) == 8
    assert local_accounts_store.get_session(created[0]) is None


def test_setup_admin_refuses_existing_admin_before_prompting(
    local_accounts_store, monkeypatch, capsys
):
    local_accounts_store.create_first_admin("owner", "Secure2026")
    from gw.core import local_accounts_cli

    monkeypatch.setattr(
        "builtins.input", lambda _prompt="": pytest.fail("不应再次提示账号")
    )
    monkeypatch.setattr(
        getpass, "getpass", lambda _prompt="": pytest.fail("不应再次提示密码")
    )

    assert local_accounts_cli.main(["setup-admin"]) == 1
    assert "已存在" in capsys.readouterr().out
    assert local_accounts_store.list_accounts() == ["owner"]


def test_setup_admin_rejects_weak_password_without_creating_account(
    local_accounts_store, monkeypatch, capsys
):
    from gw.core import local_accounts_cli

    monkeypatch.setattr("builtins.input", lambda _prompt="": "owner")
    passwords = iter(["weakpass", "weakpass"])
    monkeypatch.setattr(getpass, "getpass", lambda _prompt="": next(passwords))

    assert local_accounts_cli.main(["setup-admin"]) == 1
    assert "密码" in capsys.readouterr().out
    assert local_accounts_store.list_accounts() == []


def test_setup_admin_requires_local_account_mode(
    local_accounts_store, monkeypatch, capsys
):
    from gw.core import local_accounts_cli

    monkeypatch.setenv("GW_AUTH_MODE", "oidc")
    monkeypatch.setattr(
        "builtins.input", lambda _prompt="": pytest.fail("不应提示账号")
    )

    assert local_accounts_cli.main(["setup-admin"]) == 1
    assert "GW_AUTH_MODE" in capsys.readouterr().out
    assert local_accounts_store.list_accounts() == []
