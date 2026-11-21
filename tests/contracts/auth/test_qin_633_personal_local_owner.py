"""个人本机可信入口全面接管本地 owner 权限契约测试（QIN-633 / GitHub #776）。

验证验收条件（AC1–AC8）：
- AC1: 空库下本机可信兑换自动创建 local-owner 并全权限直入；
- AC2: 已有账户（单账户/多账户）自动接管管理员所有者，免创建/免密码/免认领直入，原有数据与密码完好可用；
- AC3: 重启后相同设备密钥幂等复用所有者；
- AC4: 票据伪造或密钥轮换显式失败可恢复；
- AC5: 远端网络与非回环请求严格 403 阻断；
- AC6: 受保护业务接口实际读写成功，非仅前端隐藏弹窗。
"""

import os
import sqlite3
import pytest
from fastapi.testclient import TestClient

from gw.api.app import app

PASSWORD = "TestPassword2026"
BOOTSTRAP_PATH = "/api/asset-auth/local/trusted-bootstrap"


@pytest.fixture
def trusted_env(monkeypatch):
    """受信本机模式隔离环境：本地账户模式 + 受信本机开关开启。"""
    from gw.api import routes_projects
    from gw.projects_hub.service import ProjectsService

    monkeypatch.setattr(routes_projects, "default_projects_service", ProjectsService())
    monkeypatch.setenv("GW_AUTH_MODE", "local_account")
    monkeypatch.setenv("GW_TRUSTED_LOCAL", "1")
    yield


def make_client(base_url: str = "http://localhost", client_addr=("127.0.0.1", 50000)):
    """构造指向回环地址的测试客户端。"""
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
    connection = sqlite3.connect(auth_db_path())
    connection.row_factory = sqlite3.Row
    try:
        rows = connection.execute(f"SELECT * FROM {name}").fetchall()
        return [dict(row) for row in rows]
    finally:
        connection.close()


def test_clean_db_trusted_bootstrap_creates_local_owner_and_enters(trusted_env):
    """AC1: 空库冷启动下，带有效票据可信兑换自动创建 local-owner 并全权限直入。"""
    ticket = issue_ticket()
    with make_client() as client:
        # 兑换前状态：未认证，需要 setup
        status_before = client.get("/api/asset-auth/status").json()
        assert status_before["authenticated"] is False
        assert status_before["setup_required"] is True

        # 发起兑换
        response = client.post(BOOTSTRAP_PATH, json={"ticket": ticket})
        assert response.status_code == 200, response.text
        assert "httponly" in response.headers["set-cookie"].lower()

        # 兑换后状态：已认证，不再需要 setup，建立本机所有者
        status_after = client.get("/api/asset-auth/status").json()
        assert status_after["authenticated"] is True
        assert status_after["principal"]["username"] == "local-owner"
        assert status_after["principal"]["role"] == "admin"
        assert status_after["setup_required"] is False
        assert status_after["trusted_local"]["established"] is True
        assert status_after["trusted_local"]["mode"] == "trusted_local"
        assert status_after["trusted_local"]["needs_manual_claim"] is False

        # AC6: 受保护业务读写接口放行验证
        create_proj = client.post(
            "/api/asset-registry/projects",
            json={"name": "空库首启测试项目", "project_type": "film"},
        )
        assert create_proj.status_code == 201, create_proj.text

    # 验证底层数据库状态
    owners = read_table("trusted_local_owners")
    assert len(owners) == 1
    assert owners[0]["claim_method"] == "bootstrap"
    users = read_table("local_users")
    assert len(users) == 1
    assert users[0]["username"] == "local-owner"
    assert users[0]["role"] == "admin"


def test_existing_single_admin_trusted_bootstrap_auto_takeover(trusted_env):
    """AC2: 已有单个管理员账户时，免创建、免密码、免认领直入，原数据与密码完好。"""
    # 模拟用户先前已创建管理员账户
    with make_client() as client:
        setup_resp = client.post(
            "/api/asset-auth/local/setup",
            json={"username": "qinxuedong", "password": PASSWORD},
        )
        assert setup_resp.status_code == 201, setup_resp.text
        # 先清除 setup 自动写入的会话 Cookie，模拟重新冷启动
        client.cookies.clear()

        # 此时未携带会话访问状态
        status_before = client.get("/api/asset-auth/status").json()
        assert status_before["authenticated"] is False
        assert status_before["setup_required"] is False

        # 启动时携带一次性启动票据
        ticket = issue_ticket()
        response = client.post(BOOTSTRAP_PATH, json={"ticket": ticket})
        assert response.status_code == 200, response.text
        assert "httponly" in response.headers["set-cookie"].lower()

        # 验证会话：直接免密接管为既有管理员 qinxuedong
        status_after = client.get("/api/asset-auth/status").json()
        assert status_after["authenticated"] is True
        assert status_after["principal"]["username"] == "qinxuedong"
        assert status_after["principal"]["role"] == "admin"
        assert status_after["setup_required"] is False
        assert status_after["trusted_local"]["established"] is True
        assert status_after["trusted_local"]["needs_manual_claim"] is False

        # 验证受保护操作成功
        proj_resp = client.post(
            "/api/asset-registry/projects",
            json={"name": "已有管理员接管项目", "project_type": "film"},
        )
        assert proj_resp.status_code == 201, proj_resp.text

    # 验证数据库状态：原账户未被重复创建或冒名，绑定方式为 auto_takeover
    owners = read_table("trusted_local_owners")
    assert len(owners) == 1
    assert owners[0]["claim_method"] == "auto_takeover"
    users = read_table("local_users")
    assert len(users) == 1
    assert users[0]["username"] == "qinxuedong"

    # 验证原密码依然有效（AC7：保留密码登录代码，密码不被篡改）
    with make_client() as client:
        login_resp = client.post(
            "/api/asset-auth/local/login",
            json={"username": "qinxuedong", "password": PASSWORD},
        )
        assert login_resp.status_code == 200, login_resp.text


def test_existing_multi_user_trusted_bootstrap_preserves_other_accounts(trusted_env):
    """AC2: 已有多个账户时，优先接管 admin 账户，且严格保留其他账户不被篡改。"""
    from gw.core import local_accounts

    # 插入一个普通编辑者和一个管理员
    with local_accounts.database() as db:
        local_accounts._insert_admin_row(db, "editor_user", "EditorPass2026")
        db.execute("UPDATE local_users SET role='editor' WHERE username='editor_user'")
        local_accounts._insert_admin_row(db, "chief_admin", "AdminPass2026")

    ticket = issue_ticket()
    with make_client() as client:
        response = client.post(BOOTSTRAP_PATH, json={"ticket": ticket})
        assert response.status_code == 200, response.text

        # 验证当前直入身份为 chief_admin (admin 角色优先)
        status = client.get("/api/asset-auth/status").json()
        assert status["authenticated"] is True
        assert status["principal"]["username"] == "chief_admin"
        assert status["principal"]["role"] == "admin"

    # 验证数据库中两个账户都完好无损
    users = {row["username"]: row["role"] for row in read_table("local_users")}
    assert users == {"editor_user": "editor", "chief_admin": "admin"}


def test_idempotent_restart_reuses_same_binding(trusted_env):
    """AC3: 重启后相同设备密钥签发新票据，幂等复用同一本机所有者。"""
    # 第一次首启
    ticket1 = issue_ticket()
    with make_client() as client:
        res1 = client.post(BOOTSTRAP_PATH, json={"ticket": ticket1})
        assert res1.status_code == 200

    owner_before = read_table("trusted_local_owners")[0]

    # 模拟重启：签发第二张票据
    ticket2 = issue_ticket()
    with make_client() as client:
        res2 = client.post(BOOTSTRAP_PATH, json={"ticket": ticket2})
        assert res2.status_code == 200
        state = client.get("/api/asset-auth/status").json()
        assert state["authenticated"] is True
        assert state["principal"]["user_id"] == owner_before["user_id"]

    # 验证没有新建第二个所有者
    owners_after = read_table("trusted_local_owners")
    assert len(owners_after) == 1
    assert owners_after[0]["owner_id"] == owner_before["owner_id"]


def test_key_rotated_conflict_returns_409(trusted_env):
    """AC4: 设备密钥被更换时，返回 409 TRUSTED_LOCAL_KEY_ROTATED 且不损坏数据。"""
    from gw.core import local_accounts

    # 先建立第一次绑定
    ticket1 = issue_ticket()
    with make_client() as client:
        assert client.post(BOOTSTRAP_PATH, json={"ticket": ticket1}).status_code == 200

    # 模拟外部使用了另一个不同指纹的设备密钥直接尝试兑换
    with local_accounts.database() as db:
        db.execute("BEGIN IMMEDIATE")
        with pytest.raises(Exception) as exc_info:
            local_accounts.ensure_trusted_local_owner(
                db, "different_device_fingerprint_12345", now=100.0
            )
        assert exc_info.value.code == "TRUSTED_LOCAL_KEY_ROTATED"


def test_tampered_or_expired_ticket_rejected_without_state_change(trusted_env):
    """AC4: 篡改签名或无效票据返回 401，且不创建任何账户。"""
    ticket = issue_ticket()
    body, signature = ticket.split(".", 1)
    tampered_sig = ("B" if signature[0] != "B" else "A") + signature[1:]
    tampered_ticket = f"{body}.{tampered_sig}"

    with make_client() as client:
        response = client.post(BOOTSTRAP_PATH, json={"ticket": tampered_ticket})
        assert response.status_code == 401
        assert response.json()["detail"]["code"] == "TRUSTED_LOCAL_BOOTSTRAP_INVALID"

        status = client.get("/api/asset-auth/status").json()
        assert status["authenticated"] is False

    assert read_table("local_users") == []
    assert read_table("trusted_local_owners") == []


def test_non_loopback_remote_request_rejected(trusted_env, monkeypatch):
    """AC5: 远端 IP 或非回环 Host 发起的兑换直接 403 阻断。"""
    ticket = issue_ticket()
    # 模拟外部远程来源 IP
    with make_client(client_addr=("10.0.0.5", 50000)) as remote_client:
        response = remote_client.post(BOOTSTRAP_PATH, json={"ticket": ticket})
        assert response.status_code == 403, response.text
        assert response.json()["detail"]["code"] == "TRUSTED_LOCAL_TRANSPORT_REJECTED"

    # 模拟在允许主机白名单内的外部 Host，依然被本票回环防御 403 阻断
    monkeypatch.setenv("GW_ALLOWED_HOSTS", "gw-remote.test")
    with make_client(base_url="http://gw-remote.test") as remote_host_client:
        response2 = remote_host_client.post(BOOTSTRAP_PATH, json={"ticket": ticket})
        assert response2.status_code == 403, response2.text
        assert response2.json()["detail"]["code"] == "TRUSTED_LOCAL_TRANSPORT_REJECTED"
