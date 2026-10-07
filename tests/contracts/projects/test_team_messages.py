"""团队纯文本消息 HTTP 契约与持久化边界回归。"""
from __future__ import annotations

import sqlite3

import pytest
from fastapi.testclient import TestClient

from gw.api import routes_auth_management
from gw.api.app import app
from gw.core.auth_management import AuthManagementStore, store
from gw.core.team_messages import DATABASE_FILENAME


@pytest.fixture
def api_client(monkeypatch, tmp_path):
    """每个用例只使用独立临时数据目录。"""
    monkeypatch.setenv("GW_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("GW_AUTH_MODE", "local")
    monkeypatch.delenv("GW_AUTH_BOOTSTRAP_ENABLED", raising=False)
    store.reset_for_tests()
    with TestClient(app) as client:
        yield client
    store.reset_for_tests()


def auth(token: str, role: str = "editor") -> dict[str, str]:
    return {"Authorization": f"Bearer {token}", "X-User-Role": role}


def bind(client: TestClient, token: str, role: str = "editor") -> dict:
    response = client.post("/api/asset-auth/identity-binding", headers=auth(token, role), json={})
    assert response.status_code == 201, response.text
    return response.json()


def current_team(client: TestClient, team_id: str, token: str = "owner-token") -> dict:
    response = client.get("/api/asset-auth/teams", headers=auth(token, "admin"))
    assert response.status_code == 200, response.text
    return next(team for team in response.json()["teams"] if team["team_id"] == team_id)


def create_team(client: TestClient, token: str = "owner-token") -> dict:
    bind(client, token, "admin")
    response = client.post("/api/asset-auth/teams", headers=auth(token, "admin"), json={"name": "协作团队"})
    assert response.status_code == 201, response.text
    return response.json()["team"]


def add_member(client: TestClient, team: dict, token: str, role: str = "editor") -> dict:
    user = bind(client, token, role)
    response = client.put(
        f"/api/asset-auth/teams/{team['team_id']}/members",
        headers=auth("owner-token", "admin"),
        json={"user_id": user["user_id"], "role": role, "expected_version": current_team(client, team["team_id"])["version"]},
    )
    assert response.status_code == 200, response.text
    return user


def post_message(client: TestClient, team_id: str, token: str, text: str, request_id: str = "req-1", role: str = "editor"):
    return client.post(
        f"/api/asset-auth/teams/{team_id}/messages",
        headers=auth(token, role),
        json={"text": text, "client_request_id": request_id},
    )


@pytest.fixture
def local_account_client(monkeypatch, tmp_path):
    """本地账户会话沿真实认证上下文绑定团队身份。"""
    monkeypatch.setenv("GW_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("GW_AUTH_MODE", "local_account")
    monkeypatch.setenv("GW_LOCAL_AUTH_DB", str(tmp_path / "accounts.sqlite3"))
    monkeypatch.delenv("GW_AUTH_BOOTSTRAP_ENABLED", raising=False)
    store.reset_for_tests()
    with TestClient(
        app,
        base_url="http://localhost",
        client=("127.0.0.1", 50000),
        headers={"Origin": "http://localhost"},
    ) as client:
        yield client
    store.reset_for_tests()


def test_local_account_admin_can_explicitly_bind_and_use_team_messages(local_account_client: TestClient):
    client = local_account_client
    setup = client.post("/api/asset-auth/local/setup", json={"username": "owner", "password": "Test2026"})
    assert setup.status_code == 201, setup.text
    principal = client.get("/api/asset-auth/status").json()["principal"]

    binding = client.get("/api/asset-auth/identity-binding")
    assert binding.status_code == 200 and binding.json() == {"bound": False, "user_id": None}
    created_binding = client.post("/api/asset-auth/identity-binding", json={})
    assert created_binding.status_code == 201, created_binding.text
    assert created_binding.json()["user_id"] == principal["user_id"]
    assert created_binding.json()["role"] == "admin"
    assert client.post("/api/asset-auth/identity-binding", json={}).status_code == 200

    team_response = client.post("/api/asset-auth/teams", json={"name": "本地管理员团队"}, headers={"X-User-Role": "readonly"})
    assert team_response.status_code == 201, team_response.text
    team_id = team_response.json()["team"]["team_id"]
    message = client.post(
        f"/api/asset-auth/teams/{team_id}/messages",
        json={"text": "本地会话消息", "client_request_id": "local-account-1"},
    )
    assert message.status_code == 201, message.text
    assert message.json()["author_user_id"] == principal["user_id"]


def test_members_are_isolated_to_their_actual_teams(api_client: TestClient):
    first_team = create_team(api_client)
    second_response = api_client.post(
        "/api/asset-auth/teams",
        headers=auth("owner-token", "admin"),
        json={"name": "另一协作团队"},
    )
    assert second_response.status_code == 201
    second_team = second_response.json()["team"]
    first_member = add_member(api_client, first_team, "first-member-token")
    second_member = add_member(api_client, second_team, "second-member-token")

    sent = post_message(api_client, first_team["team_id"], "first-member-token", "仅属于第一团队", "first-only")
    assert sent.status_code == 201
    assert api_client.get(
        f"/api/asset-auth/teams/{first_team['team_id']}/messages", headers=auth("second-member-token")
    ).status_code == 404
    assert api_client.get(
        f"/api/asset-auth/teams/{second_team['team_id']}/messages", headers=auth("first-member-token")
    ).status_code == 404
    visible = api_client.get("/api/asset-auth/teams", headers=auth("first-member-token"))
    assert [team["team_id"] for team in visible.json()["teams"]] == [first_team["team_id"]]
    assert first_member["user_id"] != second_member["user_id"]


def test_team_owner_identity_is_preserved_across_store_restart(api_client: TestClient, monkeypatch):
    owner = bind(api_client, "owner-token", "admin")
    team_response = api_client.post("/api/asset-auth/teams", headers=auth("owner-token", "admin"), json={"name": "所有者团队"})
    team = team_response.json()["team"]
    monkeypatch.setattr(routes_auth_management, "store", AuthManagementStore())
    with TestClient(app) as restarted:
        listing = restarted.get("/api/asset-auth/teams", headers=auth("owner-token", "admin"))
        assert [item["team_id"] for item in listing.json()["teams"]] == [team["team_id"]]
        removal = restarted.request(
            "DELETE",
            f"/api/asset-auth/teams/{team['team_id']}/members/{owner['user_id']}",
            headers=auth("owner-token", "admin"),
            json={"expected_version": team["version"]},
        )
    assert removal.status_code == 409


def test_team_reviewer_and_global_readonly_roles_cannot_send(api_client: TestClient):
    team = create_team(api_client)
    reviewer = add_member(api_client, team, "reviewer-token", "reviewer")
    editor_reviewer = bind(api_client, "editor-reviewer-token", "editor")
    editor_reviewer_membership = api_client.put(
        f"/api/asset-auth/teams/{team['team_id']}/members",
        headers=auth("owner-token", "admin"),
        json={"user_id": editor_reviewer["user_id"], "role": "reviewer", "expected_version": current_team(api_client, team["team_id"])["version"]},
    )
    readonly = bind(api_client, "readonly-token", "readonly")
    membership = api_client.put(
        f"/api/asset-auth/teams/{team['team_id']}/members",
        headers=auth("owner-token", "admin"),
        json={"user_id": readonly["user_id"], "role": "editor", "expected_version": current_team(api_client, team["team_id"])["version"]},
    )
    assert membership.status_code == 200
    assert editor_reviewer_membership.status_code == 200
    assert api_client.get(
        f"/api/asset-auth/teams/{team['team_id']}/messages", headers=auth("reviewer-token", "reviewer")
    ).status_code == 200
    assert post_message(api_client, team["team_id"], "reviewer-token", "只读", "reviewer-1", "reviewer").status_code == 403
    assert api_client.get(
        f"/api/asset-auth/teams/{team['team_id']}/messages", headers=auth("editor-reviewer-token", "editor")
    ).status_code == 200
    assert post_message(api_client, team["team_id"], "editor-reviewer-token", "只读", "editor-reviewer-1").status_code == 403
    assert api_client.get(
        f"/api/asset-auth/teams/{team['team_id']}/messages", headers=auth("readonly-token", "readonly")
    ).status_code == 200
    assert post_message(api_client, team["team_id"], "readonly-token", "只读", "readonly-1", "readonly").status_code == 403
    assert reviewer["role"] == "reviewer"


def test_team_message_and_identity_binding_routes_are_published(api_client: TestClient):
    paths = app.openapi()["paths"]
    assert "/api/asset-auth/identity-binding" in paths
    assert "/api/asset-auth/teams/{team_id}/messages" in paths
    assert {"get", "post"} <= set(paths["/api/asset-auth/teams/{team_id}/messages"])


def test_message_identity_is_server_bound_and_payload_cannot_forge_author(api_client: TestClient):
    team = create_team(api_client)
    unbound = api_client.get(f"/api/asset-auth/teams/{team['team_id']}/messages", headers=auth("guest-token"))
    assert unbound.status_code == 403

    forged_binding = api_client.post(
        "/api/asset-auth/identity-binding",
        headers=auth("guest-token"),
        json={"user_id": "somebody-else", "role": "admin"},
    )
    assert forged_binding.status_code in {400, 422}

    member = add_member(api_client, team, "member-token")
    created = post_message(
        api_client,
        team["team_id"],
        "member-token",
        "<script>alert(1)</script> 纯文本",
        request_id="msg-1",
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["author_user_id"] == member["user_id"]
    assert body["team_id"] == team["team_id"]
    assert body["message_id"] == f"msg-{team['team_id']}-1"
    assert body["sequence"] == 1
    assert body["text"] == "<script>alert(1)</script> 纯文本"
    assert "owner_subject" not in body and "identity_domain" not in body


def test_message_idempotency_checks_current_permission_before_replay(api_client: TestClient):
    team = create_team(api_client)
    member = add_member(api_client, team, "member-token")
    first = post_message(api_client, team["team_id"], "member-token", "同一内容", "same-key")
    replay = post_message(api_client, team["team_id"], "member-token", "同一内容", "same-key")
    conflict = post_message(api_client, team["team_id"], "member-token", "不同内容", "same-key")
    assert first.status_code == 201
    assert replay.status_code == 200 and replay.json()["message_id"] == first.json()["message_id"]
    assert replay.json()["replayed"] is True
    assert conflict.status_code == 409
    assert conflict.json()["detail"]["code"] == "MESSAGE_IDEMPOTENCY_CONFLICT"

    membership = api_client.get(f"/api/asset-auth/teams/{team['team_id']}/messages", headers=auth("member-token"))
    assert membership.status_code == 200
    version = current_team(api_client, team["team_id"])["version"]
    removed = api_client.request(
        "DELETE",
        f"/api/asset-auth/teams/{team['team_id']}/members/{member['user_id']}",
        headers=auth("owner-token", "admin"),
        json={"expected_version": version},
    )
    assert removed.status_code == 204
    denied_replay = post_message(api_client, team["team_id"], "member-token", "同一内容", "same-key")
    denied_read = api_client.get(f"/api/asset-auth/teams/{team['team_id']}/messages", headers=auth("member-token"))
    assert denied_replay.status_code == 404
    assert denied_read.status_code == 404


def test_team_message_cursor_is_monotonic_paged_and_team_bound(api_client: TestClient):
    team = create_team(api_client)
    add_member(api_client, team, "member-token")
    for index in range(3):
        response = post_message(api_client, team["team_id"], "member-token", f"消息-{index}", f"req-{index}")
        assert response.status_code == 201

    first = api_client.get(f"/api/asset-auth/teams/{team['team_id']}/messages?limit=2", headers=auth("member-token"))
    assert first.status_code == 200
    assert [m["sequence"] for m in first.json()["messages"]] == [1, 2]
    assert first.json()["next_after_sequence"] == 2
    assert first.json()["next_cursor"]
    second = api_client.get(
        f"/api/asset-auth/teams/{team['team_id']}/messages?limit=2&after_sequence={first.json()['next_after_sequence']}",
        headers=auth("member-token"),
    )
    assert [m["sequence"] for m in second.json()["messages"]] == [3]
    assert second.json()["next_after_sequence"] == 3

    mismatch = api_client.get(
        f"/api/asset-auth/teams/other-team/messages?cursor={first.json()['next_cursor']}",
        headers=auth("member-token"),
    )
    assert mismatch.status_code == 400
    both = api_client.get(
        f"/api/asset-auth/teams/{team['team_id']}/messages?cursor={first.json()['next_cursor']}&after_sequence=2",
        headers=auth("member-token"),
    )
    assert both.status_code == 400


def test_message_readback_survives_store_reconstruction(api_client: TestClient, monkeypatch):
    team = create_team(api_client)
    add_member(api_client, team, "member-token")
    created = post_message(api_client, team["team_id"], "member-token", "重启读回", "persist-1")
    assert created.status_code == 201

    # 用新 store 实例模拟服务重启，读写仍通过 HTTP 合约完成。
    monkeypatch.setattr(routes_auth_management, "store", AuthManagementStore())
    with TestClient(app) as restarted:
        response = restarted.get(f"/api/asset-auth/teams/{team['team_id']}/messages", headers=auth("member-token"))
    assert response.status_code == 200, response.text
    assert [message["text"] for message in response.json()["messages"]] == ["重启读回"]


def test_existing_corrupt_database_fails_closed_without_bootstrap(api_client: TestClient, tmp_path):
    database = tmp_path / DATABASE_FILENAME
    database.write_bytes(b"not a sqlite database")
    response = api_client.get("/api/asset-auth/identity-binding", headers=auth("admin-token", "admin"))
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "TEAM_MESSAGE_STORE_UNAVAILABLE"
    assert database.read_bytes() == b"not a sqlite database"


def test_sqlite_message_insert_failure_is_reported_as_503_without_partial_write(api_client: TestClient, tmp_path):
    team = create_team(api_client)
    add_member(api_client, team, "member-token")
    database = tmp_path / DATABASE_FILENAME
    connection = sqlite3.connect(database)
    try:
        connection.execute(
            "CREATE TRIGGER reject_team_message BEFORE INSERT ON messages "
            "BEGIN SELECT RAISE(ABORT, 'injected write failure'); END"
        )
        connection.commit()
    finally:
        connection.close()

    failed = post_message(api_client, team["team_id"], "member-token", "不能落盘", "failure-1")
    assert failed.status_code == 503
    assert failed.json()["detail"]["code"] == "TEAM_MESSAGE_STORE_UNAVAILABLE"
    readback = api_client.get(f"/api/asset-auth/teams/{team['team_id']}/messages", headers=auth("member-token"))
    assert readback.status_code == 200
    assert readback.json()["messages"] == []


def test_read_response_exposes_current_write_permission_without_granting_it(api_client):
    team = create_team(api_client)
    add_member(api_client, team, "reader-token", "readonly")
    add_member(api_client, team, "writer-token", "editor")
    path = f"/api/asset-auth/teams/{team['team_id']}/messages"
    assert api_client.get(path, headers=auth("owner-token", "admin")).json()["can_send"] is True
    assert api_client.get(path, headers=auth("writer-token", "editor")).json()["can_send"] is True
    assert api_client.get(path, headers=auth("reader-token", "editor")).json()["can_send"] is False
    assert api_client.get(path, headers=auth("writer-token", "readonly")).json()["can_send"] is False
    assert post_message(api_client, team["team_id"], "reader-token", "不能写入").status_code == 403
