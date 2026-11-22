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
    from gw.projects_hub import service as projects_service_module
    from gw.projects_hub.service import ProjectsService

    mem_projects = ProjectsService()
    monkeypatch.setattr(projects_service_module, "default_projects_service", mem_projects)
    monkeypatch.setattr(routes_projects, "default_projects_service", mem_projects)
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
    """AC1: 空库冷启动下，带有效票据可信兑换自动创建 local-owner、交付恢复码并全权限直入。"""
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
        body = response.json()
        assert body["authenticated"] is True
        # F1: 首次空库直入必须原子生成并交付 8 个恢复码，供可信证明失效时恢复
        assert "recovery_codes" in body
        recovery_codes = body["recovery_codes"]
        assert len(recovery_codes) == 8
        for code in recovery_codes:
            assert code.startswith("RC-")

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

    # F1: 数据库中确认已持久化 8 个可用恢复码
    saved_codes = read_table("local_recovery_codes")
    assert len(saved_codes) == 8
    assert all(row["used_at"] is None for row in saved_codes)

    # 模拟二次启动（已有绑定），不再重复返回恢复码
    ticket2 = issue_ticket()
    with make_client() as client:
        res2 = client.post(BOOTSTRAP_PATH, json={"ticket": ticket2})
        assert res2.status_code == 200
        assert res2.json()["recovery_codes"] == []


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
    """AC2: 已有多个账户时，优先接管 admin 账户；原账户 salt/hash/role/恢复码逐字段不可变；既有项目读写正常。"""
    from gw.core import local_accounts

    # 1. 创建一个普通编辑者和一个管理员
    with local_accounts.database() as db:
        local_accounts._insert_admin_row(db, "editor_user", "EditorPass2026")
        db.execute("UPDATE local_users SET role='editor' WHERE username='editor_user'")
        admin_uid = local_accounts._insert_admin_row(db, "chief_admin", "AdminPass2026")
        local_accounts._issue_recovery_codes(db, admin_uid)

    # 2. 在接管前以管理员身份创建一个既有项目数据
    with make_client() as client:
        # 管理员密码登录以建立初始数据
        login_res = client.post(
            "/api/asset-auth/local/login",
            json={"username": "chief_admin", "password": "AdminPass2026"},
        )
        assert login_res.status_code == 200
        create_res = client.post(
            "/api/asset-registry/projects",
            json={"name": "接管前已有历史项目", "project_type": "film"},
        )
        assert create_res.status_code == 201
        created_proj_id = create_res.json()["project"]["project_id"]
        client.cookies.clear()

    # F5: 接管前对 local_users 与 local_recovery_codes 做完整快照
    users_before = read_table("local_users")
    recovery_before = read_table("local_recovery_codes")

    # 3. 执行受信任直入兑换
    ticket = issue_ticket()
    with make_client() as client:
        response = client.post(BOOTSTRAP_PATH, json={"ticket": ticket})
        assert response.status_code == 200, response.text
        assert response.json()["recovery_codes"] == []

        # 验证当前直入身份为 chief_admin (admin 角色优先)
        status = client.get("/api/asset-auth/status").json()
        assert status["authenticated"] is True
        assert status["principal"]["username"] == "chief_admin"
        assert status["principal"]["role"] == "admin"

        # F5: 验证已有项目可读（列表查询包含该项目）且可写（PATCH 更新成功）
        proj_list = client.get("/api/asset-registry/projects")
        assert proj_list.status_code == 200
        found = [p for p in proj_list.json()["projects"] if p["project_id"] == created_proj_id]
        assert len(found) == 1
        assert found[0]["name"] == "接管前已有历史项目"

        proj_patch = client.patch(
            f"/api/asset-registry/projects/{created_proj_id}",
            json={"expected_version": 1, "description": "接管后成功更新描述"},
        )
        assert proj_patch.status_code == 200, proj_patch.text
        assert proj_patch.json()["project"]["version"] == 2

        proj_list2 = client.get("/api/asset-registry/projects")
        assert proj_list2.status_code == 200
        found2 = [p for p in proj_list2.json()["projects"] if p["project_id"] == created_proj_id]
        assert len(found2) == 1
        assert found2[0]["description"] == "接管后成功更新描述"

    # F5: 验证数据库中原有账户与恢复码逐字段不可变
    users_after = read_table("local_users")
    assert users_after == users_before
    recovery_after = read_table("local_recovery_codes")
    assert recovery_after == recovery_before


def test_existing_only_non_admin_rejects_and_requires_admin_login(trusted_env):
    """F2: 仅存在非 admin 账户时，按 PM 裁决选项 A 显式 409 拒绝，绝不静默绑定 editor。"""
    from gw.core import local_accounts

    # 仅创建一个 editor 账户
    with local_accounts.database() as db:
        local_accounts._insert_admin_row(db, "only_editor", "EditorPass2026")
        db.execute("UPDATE local_users SET role='editor' WHERE username='only_editor'")

    users_before = read_table("local_users")
    ticket = issue_ticket()
    with make_client() as client:
        response = client.post(BOOTSTRAP_PATH, json={"ticket": ticket})
        assert response.status_code == 409
        detail = response.json()["detail"]
        assert detail["code"] == "TRUSTED_LOCAL_ADMIN_REQUIRED"

        # 认证状态依然为未认证，且未建立任何所有者绑定
        status = client.get("/api/asset-auth/status").json()
        assert status["authenticated"] is False

    # 数据库完全未被篡改，也没有新建绑定
    assert read_table("local_users") == users_before
    assert read_table("trusted_local_owners") == []


def test_expired_ticket_rejected_without_state_change(trusted_env):
    """F4: 真实过期票据提交兑换返回 401，且票据未被消费、数据库无残留变更。"""
    import time
    from gw.core import trusted_local

    # 签发一张在过去时间戳签发且在当前时间已过期的真实有效签名票据
    past_now = time.time() - 3600.0
    expired_ticket = trusted_local.issue_bootstrap_ticket(now=past_now)

    with make_client() as client:
        response = client.post(BOOTSTRAP_PATH, json={"ticket": expired_ticket})
        assert response.status_code == 401
        assert response.json()["detail"]["code"] == "TRUSTED_LOCAL_BOOTSTRAP_EXPIRED"

        status = client.get("/api/asset-auth/status").json()
        assert status["authenticated"] is False

    # 票据未被消费，数据库无用户与所有者产生
    tickets = read_table("trusted_local_bootstrap_tickets")
    assert len(tickets) == 1
    assert tickets[0]["consumed_at"] is None
    assert read_table("local_users") == []
    assert read_table("trusted_local_owners") == []


def test_bootstrap_rollback_on_failure_leaves_no_traces(trusted_env, monkeypatch):
    """F1: 空库首启中若在创建恢复码与所有者期间发生异常，整个事务原子回滚，无残留。"""
    from gw.core import local_accounts

    ticket = issue_ticket()

    # 模拟在用户创建后、恢复码生成过程中抛出异常
    def failing_issue_recovery_codes(db, user_id):
        raise RuntimeError("模拟恢复码生成故障回滚")

    monkeypatch.setattr(local_accounts, "_issue_recovery_codes", failing_issue_recovery_codes)

    with make_client() as client:
        with pytest.raises(RuntimeError, match="模拟恢复码生成故障回滚"):
            client.post(BOOTSTRAP_PATH, json={"ticket": ticket})

    # 验证事务完全原子回滚：无用户、无恢复码、无所有者
    assert read_table("local_users") == []
    assert read_table("local_recovery_codes") == []
    assert read_table("trusted_local_owners") == []
    # 票据未被消费
    tickets = read_table("trusted_local_bootstrap_tickets")
    assert len(tickets) == 1
    assert tickets[0]["consumed_at"] is None
    assert tickets[0]["consumed_at"] is None


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
