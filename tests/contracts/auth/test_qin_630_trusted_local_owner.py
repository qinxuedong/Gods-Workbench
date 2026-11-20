"""受信本机首启、一次性票据与权限边界回归（QIN-630 / GitHub #765）。"""

import os
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from gw.api.app import app

PASSWORD = "Test2026"
BOOTSTRAP_PATH = "/api/asset-auth/local/trusted-bootstrap"


@pytest.fixture
def trusted_env(monkeypatch):
    """受信本机模式的隔离环境：本地账户模式 + 受信本机开关打开。"""
    from gw.api import routes_projects
    from gw.projects_hub.service import ProjectsService

    monkeypatch.setattr(routes_projects, "default_projects_service", ProjectsService())
    monkeypatch.setenv("GW_AUTH_MODE", "local_account")
    monkeypatch.setenv("GW_TRUSTED_LOCAL", "1")
    yield


def make_client(base_url: str = "http://localhost", client_addr=("127.0.0.1", 50000)):
    """构造一个指定 base_url 与来源地址的测试客户端。"""
    return TestClient(
        app,
        base_url=base_url,
        client=client_addr,
        headers={"Origin": base_url},
    )


def issue_ticket():
    from gw.core import trusted_local

    return trusted_local.issue_bootstrap_ticket()


def auth_db_path() -> str:
    return os.environ["GW_LOCAL_AUTH_DB"]


def read_table(name: str) -> list[dict]:
    """直接读账户库，断言落盘事实（不依赖 HTTP 表述）。"""
    connection = sqlite3.connect(auth_db_path())
    connection.row_factory = sqlite3.Row
    try:
        rows = connection.execute(f"SELECT * FROM {name}").fetchall()
        return [dict(row) for row in rows]
    finally:
        connection.close()


def test_bootstrap_establishes_owner_and_keeps_existing_permission_model(trusted_env):
    """首启：一次性票据兑换成本机所有者会话，权限判定一行未改。"""
    ticket = issue_ticket()
    with make_client() as client:
        response = client.post(BOOTSTRAP_PATH, json={"ticket": ticket})
        assert response.status_code == 200, response.text
        assert "httponly" in response.headers["set-cookie"].lower()
        state = client.get("/api/asset-auth/status").json()
        assert state["authenticated"] is True
        assert state["principal"]["role"] == "admin"
        assert state["setup_required"] is False
        assert state["trusted_local"]["established"] is True
        assert state["trusted_local"]["mode"] == "trusted_local"
        # 既有业务写接口仍按既有角色判定放行
        assert (
            client.post(
                "/api/asset-registry/projects",
                json={"name": "本机所有者项目", "project_type": "film"},
            ).status_code
            == 201
        )
    owners = read_table("trusted_local_owners")
    assert len(owners) == 1
    assert owners[0]["device_fingerprint"]
    # 本机所有者是既有本地账户体系里的管理员，不新增角色
    assert [row["role"] for row in read_table("local_users")] == ["admin"]


def test_bootstrap_ticket_is_single_use(trusted_env):
    """同一张票据只能兑换一次；重放返回 401 且不新建所有者。"""
    ticket = issue_ticket()
    with make_client() as client:
        assert client.post(BOOTSTRAP_PATH, json={"ticket": ticket}).status_code == 200
        replay = client.post(BOOTSTRAP_PATH, json={"ticket": ticket})
        assert replay.status_code == 401, replay.text
        assert replay.json()["detail"]["code"] == "TRUSTED_LOCAL_BOOTSTRAP_INVALID"
    assert len(read_table("trusted_local_owners")) == 1


def test_tampered_ticket_is_rejected_without_creating_accounts(trusted_env):
    """签名被改坏的票据一律拒绝，且不得留下任何账户。"""
    ticket = issue_ticket()
    body, signature = ticket.split(".", 1)
    flipped = ("A" if signature[0] != "A" else "B") + signature[1:]
    with make_client() as client:
        response = client.post(BOOTSTRAP_PATH, json={"ticket": f"{body}.{flipped}"})
        assert response.status_code == 401, response.text
        assert response.json()["detail"]["code"] == "TRUSTED_LOCAL_BOOTSTRAP_INVALID"
        state = client.get("/api/asset-auth/status").json()
        assert state["authenticated"] is False
        assert state["principal"] is None
    assert read_table("local_users") == []
    assert read_table("trusted_local_owners") == []


def test_existing_account_is_never_taken_over_and_ticket_survives_failure(trusted_env):
    """已有账户默认需人工认领：兑换失败不接管、不消费票据。"""
    with make_client() as client:
        created = client.post(
            "/api/asset-auth/local/setup", json={"username": "owner", "password": PASSWORD}
        )
        assert created.status_code == 201, created.text
    ticket = issue_ticket()
    with make_client() as client:
        rejected = client.post(BOOTSTRAP_PATH, json={"ticket": ticket})
        assert rejected.status_code == 409, rejected.text
        assert rejected.json()["detail"]["code"] == "TRUSTED_LOCAL_CLAIM_REQUIRED"
        state = client.get("/api/asset-auth/status").json()
        assert state["trusted_local"]["needs_manual_claim"] is True
        assert state["trusted_local"]["established"] is False
    # 既有账户未被改动，也没有静默建立第二个所有者
    assert [row["username"] for row in read_table("local_users")] == ["owner"]
    assert read_table("trusted_local_owners") == []
    # 失败事务整体回滚：票据未被消费，认领成功后同一张票据仍可兑换
    assert read_table("trusted_local_bootstrap_tickets")[0]["consumed_at"] is None


def test_claim_requires_admin_authentication_and_keeps_ticket_usable(
    trusted_env, monkeypatch
):
    """认领必须先过管理员认证；认证失败不改数据。"""
    from gw.core import local_accounts_cli

    with make_client() as client:
        created = client.post(
            "/api/asset-auth/local/setup", json={"username": "owner", "password": PASSWORD}
        )
        assert created.status_code == 201, created.text
    ticket = issue_ticket()
    with make_client() as client:
        assert client.post(BOOTSTRAP_PATH, json={"ticket": ticket}).status_code == 409

    monkeypatch.setattr("getpass.getpass", lambda *args, **kwargs: "错误密码")
    assert local_accounts_cli.main(["trusted-claim", "--username", "owner"]) == 1
    assert read_table("trusted_local_owners") == []

    monkeypatch.setattr("getpass.getpass", lambda *args, **kwargs: PASSWORD)
    assert local_accounts_cli.main(["trusted-claim", "--username", "owner"]) == 0
    owners = read_table("trusted_local_owners")
    assert len(owners) == 1
    assert owners[0]["claim_method"] == "password_auth"

    with make_client() as client:
        redeemed = client.post(BOOTSTRAP_PATH, json={"ticket": ticket})
        assert redeemed.status_code == 200, redeemed.text
        state = client.get("/api/asset-auth/status").json()
        assert state["trusted_local"]["established"] is True


def test_unclaim_removes_binding_without_deleting_accounts(trusted_env, monkeypatch):
    """解绑只解除绑定，不删除账户与项目数据。"""
    from gw.core import local_accounts_cli

    with make_client() as client:
        created = client.post(
            "/api/asset-auth/local/setup", json={"username": "owner", "password": PASSWORD}
        )
        assert created.status_code == 201, created.text
    monkeypatch.setattr("getpass.getpass", lambda *args, **kwargs: PASSWORD)
    assert local_accounts_cli.main(["trusted-claim", "--username", "owner"]) == 0
    assert local_accounts_cli.main(["trusted-unclaim", "--username", "owner"]) == 0
    assert read_table("trusted_local_owners") == []
    assert [row["username"] for row in read_table("local_users")] == ["owner"]


def test_non_loopback_client_and_host_are_rejected(trusted_env, monkeypatch):
    """回环传输与回环 Host 是纵深防御：远程来源拿不到本机身份。"""
    ticket = issue_ticket()
    with make_client(client_addr=("10.0.0.5", 50000)) as remote_client:
        remote = remote_client.post(BOOTSTRAP_PATH, json={"ticket": ticket})
        assert remote.status_code == 403, remote.text
        assert remote.json()["detail"]["code"] == "TRUSTED_LOCAL_TRANSPORT_REJECTED"
    # 非回环 Host：即使通过既有 Host 白名单，本票纵深防御仍拒绝
    monkeypatch.setenv("GW_ALLOWED_HOSTS", "gw-remote.test")
    with make_client(base_url="http://gw-remote.test") as remote_host_client:
        remote_host = remote_host_client.post(BOOTSTRAP_PATH, json={"ticket": ticket})
        assert remote_host.status_code == 403, remote_host.text
        assert remote_host.json()["detail"]["code"] == "TRUSTED_LOCAL_TRANSPORT_REJECTED"
    # 白名单之外的 Host：既有主机门禁先行挡下，同样不会建立所有者
    with make_client(base_url="http://example.com") as unknown_host_client:
        unknown = unknown_host_client.post(BOOTSTRAP_PATH, json={"ticket": ticket})
        assert unknown.status_code == 400, unknown.text
    assert read_table("trusted_local_owners") == []
    # 票据未被远程尝试消费
    assert read_table("trusted_local_bootstrap_tickets")[0]["consumed_at"] is None


def test_trusted_local_switch_is_only_a_feature_flag(trusted_env, monkeypatch):
    """开关关掉即拒绝；开关本身永远不是身份凭据。"""
    ticket = issue_ticket()
    monkeypatch.setenv("GW_TRUSTED_LOCAL", "0")
    with make_client() as client:
        response = client.post(BOOTSTRAP_PATH, json={"ticket": ticket})
        assert response.status_code == 403, response.text
        assert response.json()["detail"]["code"] == "TRUSTED_LOCAL_DISABLED"
    assert read_table("local_users") == []


def test_status_block_is_additive_and_never_leaks_secrets(trusted_env):
    """状态端点只增不改；不泄露票据、密钥、路径与主体名。"""
    ticket = issue_ticket()
    with make_client() as client:
        before = client.get("/api/asset-auth/status").json()
        assert before["trusted_local"]["enabled"] is True
        assert before["trusted_local"]["established"] is False
        assert before["trusted_local"]["mode"] == "uninitialized"
        assert before["setup_required"] is True
        assert client.post(BOOTSTRAP_PATH, json={"ticket": ticket}).status_code == 200
        after = client.get("/api/asset-auth/status").json()
    body = repr(after)
    assert ticket not in body
    assert "trusted_local.key" not in body
    # 受信本机块本身不携带主体名、票据、设备指纹或路径
    trusted_block = repr(after["trusted_local"])
    assert "local-owner" not in trusted_block
    assert "aud" not in trusted_block
    assert "jti" not in trusted_block


def test_unauthenticated_requests_still_fail_with_401(trusted_env):
    """本机自动进入不放松既有鉴权：未登录的业务写操作仍是 401。"""
    ticket = issue_ticket()
    with make_client() as client:
        assert (
            client.post(
                "/api/asset-registry/projects",
                json={"name": "未授权", "project_type": "film"},
                headers={"Authorization": "Bearer fabricated"},
            ).status_code
            == 401
        )
        assert client.post(BOOTSTRAP_PATH, json={"ticket": ticket}).status_code == 200
    # 换一个全新客户端（无 Cookie）仍然 401
    with make_client() as anonymous:
        assert (
            anonymous.post(
                "/api/asset-registry/projects",
                json={"name": "未授权", "project_type": "film"},
            ).status_code
            == 401
        )


def test_device_key_is_created_with_restricted_permissions(trusted_env):
    """设备密钥落在受控目录，权限不合格即失败关闭。"""
    from gw.core import trusted_local

    key_path = trusted_local.device_key_path()
    trusted_local.load_or_create_device_key()
    assert key_path.exists()
    assert key_path.name == "trusted_local.key"
    assert key_path.parent == Path(os.path.dirname(auth_db_path()))
    trusted_local.verify_file_permissions(key_path)
    assert len(key_path.read_bytes()) == trusted_local.DEVICE_KEY_BYTES
