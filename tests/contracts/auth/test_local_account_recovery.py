"""本地账户恢复码重置与重新生成契约。"""

import sqlite3

import pytest
from fastapi.testclient import TestClient

from gw.api.app import app

PASSWORD = "Test2026"
RESET_PASSWORD = "Reset2026"


@pytest.fixture
def local_client(monkeypatch, tmp_path):
    from gw.api import routes_projects
    from gw.projects_hub.service import ProjectsService

    monkeypatch.setattr(routes_projects, "default_projects_service", ProjectsService())
    monkeypatch.setenv("GW_AUTH_MODE", "local_account")
    monkeypatch.setenv("GW_LOCAL_AUTH_DB", str(tmp_path / "auth.sqlite3"))
    with TestClient(
        app,
        base_url="http://localhost",
        client=("127.0.0.1", 50000),
        headers={"Origin": "http://localhost"},
    ) as client:
        yield client


def create_account(client):
    return client.post(
        "/api/asset-auth/local/setup",
        json={"username": "owner", "password": PASSWORD},
    )


def reset_with(client, code, password=RESET_PASSWORD):
    return client.post(
        "/api/asset-auth/local/reset-password",
        json={
            "username": "owner",
            "recovery_code": code,
            "new_password": password,
        },
    )


def test_setup_issues_one_time_recovery_codes_without_persisting_plaintext(
    local_client, monkeypatch
):
    import os

    response = create_account(local_client)
    assert response.status_code == 201, response.text
    codes = response.json()["recovery_codes"]
    assert len(codes) == 8 and len(set(codes)) == 8
    assert "no-store" in response.headers["cache-control"].lower()

    database = open(os.environ["GW_LOCAL_AUTH_DB"], "rb").read()
    assert all(code.encode() not in database for code in codes)
    with sqlite3.connect(os.environ["GW_LOCAL_AUTH_DB"]) as connection:
        rows = connection.execute(
            "SELECT code_id, salt, code_hash, used_at FROM local_recovery_codes"
        ).fetchall()
    assert len(rows) == 8
    assert all(code_id and len(salt) >= 16 and len(code_hash) == 32 for code_id, salt, code_hash, _ in rows)
    assert all(used_at is None for _, _, _, used_at in rows)


def test_recovery_reset_changes_password_consumes_code_and_revokes_all_sessions(
    local_client
):
    setup = create_account(local_client)
    assert setup.status_code == 201
    code = setup.json()["recovery_codes"][0]
    first_session = local_client.cookies.get("gw_session")

    with TestClient(
        app,
        base_url="http://localhost",
        client=("127.0.0.1", 50001),
        headers={"Origin": "http://localhost"},
    ) as second_session:
        login = second_session.post(
            "/api/asset-auth/local/login",
            json={"username": "owner", "password": PASSWORD},
        )
        assert login.status_code == 200
        assert second_session.cookies.get("gw_session") != first_session

        response = reset_with(second_session, code)
        assert response.status_code == 200, response.text
        assert response.headers["cache-control"].lower() == "no-store"
        assert local_client.get("/api/asset-auth/status").json()["authenticated"] is False
        assert (
            local_client.post(
                "/api/asset-registry/projects",
                json={"name": "旧会话", "project_type": "film"},
            ).status_code
            == 401
        )

        assert reset_with(second_session, code, "Other2026").status_code == 401
        assert (
            second_session.post(
                "/api/asset-auth/local/login",
                json={"username": "owner", "password": PASSWORD},
            ).status_code
            == 401
        )
        login = second_session.post(
            "/api/asset-auth/local/login",
            json={"username": "owner", "password": RESET_PASSWORD},
        )
        assert login.status_code == 200


def test_recovery_attempts_have_a_separate_persistent_rate_limit(local_client):
    assert create_account(local_client).status_code == 201
    responses = [
        reset_with(local_client, "RC-invalid-invalid").status_code
        for _ in range(11)
    ]
    assert responses[:10] == [401] * 10
    assert responses[10] == 429


def test_regeneration_requires_current_password_and_invalidates_old_codes(local_client):
    setup = create_account(local_client)
    old_code = setup.json()["recovery_codes"][0]

    unauthenticated = TestClient(
        app,
        base_url="http://localhost",
        client=("127.0.0.1", 50002),
        headers={"Origin": "http://localhost"},
    )
    try:
        assert (
            unauthenticated.post(
                "/api/asset-auth/local/recovery-codes/regenerate",
                json={"current_password": PASSWORD},
            ).status_code
            == 401
        )
    finally:
        unauthenticated.close()

    rejected = local_client.post(
        "/api/asset-auth/local/recovery-codes/regenerate",
        json={"current_password": "Wrong2026"},
    )
    assert rejected.status_code == 401
    generated = local_client.post(
        "/api/asset-auth/local/recovery-codes/regenerate",
        json={"current_password": PASSWORD},
    )
    assert generated.status_code == 200, generated.text
    new_codes = generated.json()["recovery_codes"]
    assert len(new_codes) == 8 and len(set(new_codes)) == 8
    assert old_code not in new_codes
    assert reset_with(local_client, old_code).status_code == 401


def test_existing_account_receives_codes_on_first_successful_login(local_client):
    from gw.core import local_accounts

    local_accounts.create_first_admin("owner", PASSWORD)
    first = local_client.post(
        "/api/asset-auth/local/login",
        json={"username": "owner", "password": PASSWORD},
    )
    assert first.status_code == 200
    assert len(first.json()["recovery_codes"]) == 8
    second = local_client.post(
        "/api/asset-auth/local/login",
        json={"username": "owner", "password": PASSWORD},
    )
    assert second.status_code == 200
    assert second.json()["recovery_codes"] == []


def test_no_http_password_reset_bypasses_recovery_code(local_client):
    assert create_account(local_client).status_code == 201
    bypass = local_client.post(
        "/api/asset-auth/local/reset-password",
        json={"username": "owner", "new_password": RESET_PASSWORD},
    )
    assert bypass.status_code == 400
    assert (
        local_client.post(
            "/api/asset-auth/local/login",
            json={"username": "owner", "password": PASSWORD},
        ).status_code
        == 200
    )
    assert local_client.post(
        "/api/asset-auth/local/cli-reset-password",
        json={"username": "owner", "new_password": RESET_PASSWORD},
    ).status_code == 404
