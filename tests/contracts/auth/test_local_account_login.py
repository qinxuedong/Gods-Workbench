"""本地账户数据库登录与权限边界回归。"""
import sqlite3
import pytest
from fastapi.testclient import TestClient
from gw.api.app import app

PASSWORD = "Test2026"

@pytest.fixture
def local_client(monkeypatch, tmp_path):
    from gw.api import routes_projects
    from gw.projects_hub.service import ProjectsService
    # 避免本地登录写操作污染其他契约用例的黄金项目单例。
    monkeypatch.setattr(routes_projects, "default_projects_service", ProjectsService())
    monkeypatch.setenv("GW_AUTH_MODE", "local_account")
    monkeypatch.setenv("GW_LOCAL_AUTH_DB", str(tmp_path / "auth.sqlite3"))
    with TestClient(app, base_url="http://localhost", client=("127.0.0.1", 50000), headers={"Origin": "http://localhost"}) as client:
        yield client


def setup(client):
    return client.post("/api/asset-auth/local/setup", json={"username": "owner", "password": PASSWORD})


def test_first_admin_login_business_write_and_logout(local_client):
    client = local_client
    assert client.get("/api/asset-auth/status").json()["setup_required"] is True
    forged = {"Authorization": "Bearer fabricated", "X-User-Role": "admin"}
    assert client.post("/api/asset-registry/projects", json={"name": "未授权", "project_type": "film"}, headers=forged).status_code == 401
    response = setup(client)
    assert response.status_code == 201, response.text
    assert "httponly" in response.headers["set-cookie"].lower()
    assert PASSWORD not in response.text
    state = client.get("/api/asset-auth/status").json()
    assert state["authenticated"] is True and state["principal"]["role"] == "admin"
    assert client.post("/api/asset-registry/projects", json={"name": "本地登录测试", "project_type": "film"}).status_code == 201
    assert setup(client).status_code == 409
    assert client.post("/api/asset-auth/logout").status_code == 204
    assert client.get("/api/asset-auth/status").json()["authenticated"] is False
    assert client.post("/api/asset-auth/local/login", json={"username": "owner", "password": "错误密码"}).status_code == 401
    assert client.post("/api/asset-auth/local/login", json={"username": "owner", "password": PASSWORD}).status_code == 200


def test_database_persists_and_contains_no_plain_secrets(local_client, monkeypatch):
    import os
    client = local_client
    assert setup(client).status_code == 201
    token = client.cookies.get("gw_session")
    data = open(os.environ["GW_LOCAL_AUTH_DB"], "rb").read()
    assert PASSWORD.encode() not in data and token.encode() not in data
    with TestClient(app, base_url="http://localhost", client=("127.0.0.1", 50000)) as reopened:
        reopened.cookies.set("gw_session", token)
        assert reopened.get("/api/asset-auth/status").json()["authenticated"] is True
    assert client.post("/api/asset-auth/logout").status_code == 204
    client.cookies.set("gw_session", token)
    assert client.get("/api/asset-auth/status").json()["authenticated"] is False


def test_local_auth_rejects_cross_origin_and_invalid_setup(local_client):
    client = local_client
    assert client.post("/api/asset-auth/local/setup", json={"username": "owner", "password": PASSWORD}, headers={"Origin": "http://evil.test"}).status_code == 403
    response = client.post("/api/asset-auth/local/setup", json={"username": "owner", "password": "short"})
    assert response.status_code == 400 and "short" not in response.text
    assert setup(client).status_code == 201
    assert client.post("/api/asset-registry/projects", json={"name": "跨站", "project_type": "film"}, headers={"Origin": "http://evil.test"}).status_code == 403
    assert client.post("/api/asset-auth/logout", headers={"Origin": "null"}).status_code == 403


def test_role_and_expiration_come_from_database(local_client):
    import os
    client = local_client
    assert setup(client).status_code == 201
    with sqlite3.connect(os.environ["GW_LOCAL_AUTH_DB"]) as db:
        db.execute("UPDATE local_users SET role='readonly'")
    response = client.post("/api/asset-registry/projects", json={"name": "禁止提权", "project_type": "film"}, headers={"X-User-Role": "admin"})
    assert response.status_code == 403
    with sqlite3.connect(os.environ["GW_LOCAL_AUTH_DB"]) as db:
        db.execute("UPDATE local_sessions SET expires_at=0")
    assert client.get("/api/asset-auth/status").json()["authenticated"] is False


def test_login_throttled(local_client):
    client = local_client
    assert setup(client).status_code == 201
    client.post("/api/asset-auth/logout")
    codes = [client.post("/api/asset-auth/local/login", json={"username": "owner", "password": "wrong"}).status_code for _ in range(11)]
    assert 401 in codes and codes[-1] == 429


def test_setup_refuses_remote_client(local_client):
    with TestClient(app, base_url="http://localhost", client=("192.0.2.1", 40000), headers={"Origin": "http://localhost"}) as remote:
        assert setup(remote).status_code == 403

def test_setup_is_atomic_under_concurrent_attempts(local_client):
    from concurrent.futures import ThreadPoolExecutor
    from gw.core import local_accounts
    from gw.core.errors import CleanroomException

    def create(index):
        try:
            local_accounts.create_first_admin(f"owner{index}", PASSWORD)
            return 201
        except CleanroomException as exc:
            return exc.status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(create, range(2))) == [201, 409]


def test_login_rotates_session_and_missing_origin_is_rejected(local_client):
    client = local_client
    assert setup(client).status_code == 201
    old = client.cookies.get("gw_session")
    assert client.post("/api/asset-auth/local/login", json={"username": "owner", "password": PASSWORD}).status_code == 200
    assert client.cookies.get("gw_session") != old
    from gw.core.local_accounts import get_session
    assert get_session(old) is None
    client.headers.pop("Origin")
    assert client.post("/api/asset-auth/logout").status_code == 403


def test_local_endpoints_disabled_in_oidc(local_client, monkeypatch):
    monkeypatch.setenv("GW_AUTH_MODE", "oidc")
    monkeypatch.delenv("GW_OIDC_ISSUER", raising=False)
    assert setup(local_client).status_code == 403


def test_setup_rejects_rebound_hostname(local_client):
    with TestClient(app, base_url="http://attacker.test", client=("127.0.0.1", 40000), headers={"Origin": "http://attacker.test"}) as rebound:
        assert setup(rebound).status_code == 403

@pytest.mark.parametrize("password", ["abc1234", "abcdefgh", "12345678", "abcdefghijkl", "123456789012"])
def test_setup_rejects_short_or_single_class_password(local_client, password):
    response = local_client.post("/api/asset-auth/local/setup", json={"username": "owner", "password": password})
    assert response.status_code == 400
    assert local_client.get("/api/asset-auth/status").json()["setup_required"] is True


def test_setup_accepts_eight_letters_and_digits(local_client):
    response = local_client.post("/api/asset-auth/local/setup", json={"username": "owner", "password": "Test2026"})
    assert response.status_code == 201
    assert local_client.post("/api/asset-auth/logout").status_code == 204
    assert local_client.post("/api/asset-auth/local/login", json={"username": "owner", "password": "Test2026"}).status_code == 200

def test_existing_password_can_still_login(local_client):
    import os
    from gw.core.local_accounts import password_hash
    client = local_client
    assert setup(client).status_code == 201
    old_password = "以前的中文密码不含英文字母2026"
    with sqlite3.connect(os.environ["GW_LOCAL_AUTH_DB"]) as db:
        salt = db.execute("SELECT salt FROM local_users WHERE username='owner'").fetchone()[0]
        db.execute("UPDATE local_users SET password_hash=? WHERE username='owner'", (password_hash(old_password, salt),))
    client.post("/api/asset-auth/logout")
    assert client.post("/api/asset-auth/local/login", json={"username": "owner", "password": old_password}).status_code == 200
