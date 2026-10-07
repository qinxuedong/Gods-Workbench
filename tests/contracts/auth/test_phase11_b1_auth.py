# -*- coding: utf-8 -*-
"""Phase 11 B1 认证、团队与操作授权契约回归。"""
import json
from pathlib import Path

from fastapi.testclient import TestClient

from gw.api.app import app
from gw.core import audit as audit_log
from gw.core.auth_management import _Approval, store


def client():
    store.reset_for_tests()
    audit_log.reset_audit_log()
    return TestClient(app)


def headers(role="admin", token="phase11-test"):
    return {"Authorization": f"Bearer {token}", "X-User-Role": role}


def test_b1_boundary_fixture_matches_contract():
    fixture_path = Path(__file__).resolve().parents[3] / "docs" / "fixtures" / "phase11-b1-boundary.json"
    fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
    boundary = fixture["cleanroom_boundary"]
    assert fixture["module"] == "asset_auth"
    assert fixture["contract_version"] == "phase11-b1-1"
    assert boundary["bootstrap_default"] == "closed"
    assert boundary["token_plaintext_storage"] is False
    assert boundary["token_plaintext_audit"] is False
    assert boundary["stable_ids"] is True
    assert fixture["error_statuses"] == {"unauthenticated": 401, "forbidden": 403, "conflict": 409}


def test_b1_management_routes_are_registered():
    paths = set(app.openapi()["paths"])
    expected = {
        "/api/asset-auth/bootstrap", "/api/asset-auth/users", "/api/asset-auth/users/{user_id}",
        "/api/asset-auth/teams", "/api/asset-auth/teams/{team_id}",
        "/api/asset-auth/teams/{team_id}/members", "/api/asset-auth/teams/{team_id}/members/{user_id}",
        "/api/asset-auth/operation-approvals", "/api/asset-auth/operation-approvals/{approval_id}",
        "/api/asset-auth/tokens",
    }
    assert expected <= paths


def test_bootstrap_is_fail_closed_until_explicit_window(monkeypatch):
    c = client()
    monkeypatch.delenv("GW_AUTH_BOOTSTRAP_ENABLED", raising=False)
    monkeypatch.delenv("GW_AUTH_BOOTSTRAP_WINDOW", raising=False)
    assert c.post("/api/asset-auth/bootstrap", json={"display_name": "关闭", "role": "admin"}).status_code in {403, 503}
    monkeypatch.setenv("GW_AUTH_BOOTSTRAP_ENABLED", "true")
    assert c.post("/api/asset-auth/bootstrap", json={"display_name": "根用户", "role": "admin"}).status_code == 201
    assert c.post("/api/asset-auth/bootstrap", json={"display_name": "重复", "role": "admin"}).status_code == 409


def test_b1_auth_and_crud_cas_flow(monkeypatch):
    c = client(); h = headers()
    monkeypatch.setenv("GW_AUTH_BOOTSTRAP_ENABLED", "1")
    assert c.get("/api/asset-auth/users").status_code == 401
    r = c.post("/api/asset-auth/bootstrap", json={"display_name": "根用户", "role": "admin"})
    assert r.status_code == 201
    root = r.json()["user"]
    assert root["user_id"].startswith("usr-")

    r = c.post("/api/asset-auth/users", headers=h, json={"display_name": "编辑者", "role": "editor"})
    assert r.status_code == 201
    user = r.json()["user"]
    uid, version = user["user_id"], user["version"]
    assert c.patch(f"/api/asset-auth/users/{uid}", headers=h, json={"display_name": "新名", "expected_version": 999}).status_code == 409
    assert c.patch(f"/api/asset-auth/users/{uid}", headers=h, json={"display_name": "新名", "expected_version": version}).status_code == 200
    assert c.patch(f"/api/asset-auth/users/{uid}", headers=h, json={"display_name": "新名2", "expected_version": version}).status_code == 409

    r = c.post("/api/asset-auth/teams", headers=h, json={"name": "团队 A"})
    assert r.status_code == 201
    team = r.json()["team"]; tid, tv = team["team_id"], team["version"]
    r = c.put(f"/api/asset-auth/teams/{tid}/members", headers=h, json={"user_id": uid, "role": "editor", "expected_version": tv})
    assert r.status_code == 200
    membership = r.json()["membership"]
    # 第一次写入后的旧版本重放同角色请求不再增加版本。
    replay = c.put(f"/api/asset-auth/teams/{tid}/members", headers=h, json={"user_id": uid, "role": "editor", "expected_version": tv})
    assert replay.status_code == 200
    assert replay.json()["membership"]["version"] == membership["version"]
    tv = membership["version"]
    assert c.request("DELETE", f"/api/asset-auth/teams/{tid}/members/{uid}", headers=h, json={"expected_version": tv}).status_code == 204
    assert c.request("DELETE", f"/api/asset-auth/teams/{tid}", headers=h, json={"expected_version": tv + 1}).status_code == 204


def test_b1_token_is_returned_once_and_not_stored_plaintext():
    c = client(); h = headers()
    r = c.post("/api/asset-auth/tokens", headers=h, json={"name": "automation", "scopes": ["read"]})
    assert r.status_code == 201
    payload = r.json()["token"]
    assert payload["token_id"].startswith("tok-")
    raw = payload["token"]
    assert raw
    assert all(raw not in str(v.__dict__) for v in store.tokens.values())
    assert all(raw not in str(event) for event in audit_log.list_auth_events())
    assert any(event["event"] == audit_log.EVENT_TOKEN_CREATED for event in audit_log.list_auth_events())


def test_b1_approval_pagination_and_validation_errors():
    c = client(); h = headers()
    assert c.get("/api/asset-auth/operation-approvals", headers=h).status_code == 200
    for index in range(3):
        approval_id = f"ap-{index:02d}"
        store.approvals[approval_id] = _Approval(approval_id=approval_id, owner_subject=None)
    first = c.get("/api/asset-auth/operation-approvals?limit=2", headers=h)
    assert first.status_code == 200
    assert len(first.json()["approvals"]) == 2
    assert first.json()["next_cursor"]
    second = c.get(f"/api/asset-auth/operation-approvals?limit=2&cursor={first.json()['next_cursor']}", headers=h)
    assert second.status_code == 200
    assert len(second.json()["approvals"]) == 1
    assert c.put("/api/asset-auth/operation-approvals/ap-404", headers=h, json={"decision": "approved", "expected_version": 1}).status_code == 404
    assert c.put("/api/asset-auth/operation-approvals/ap-00", headers=h, json={"decision": "other", "expected_version": 1}).status_code == 400
    assert c.post("/api/asset-auth/users", headers=h, json={"display_name": "坏角色", "role": "root"}).status_code == 422
    assert c.get("/api/asset-auth/teams", headers={**h, "X-User-Role": "readonly"}).status_code == 200
    assert c.post("/api/asset-auth/teams", headers={**h, "X-User-Role": "readonly"}, json={"name": "禁止"}).status_code == 403


def test_b1_management_writes_are_audited():
    c = client(); h = headers()
    r = c.post("/api/asset-auth/users", headers=h, json={"display_name": "成员"})
    uid = r.json()["user"]["user_id"]
    r = c.post("/api/asset-auth/teams", headers=h, json={"name": "审计团队"})
    team = r.json()["team"]
    c.put(f"/api/asset-auth/teams/{team['team_id']}/members", headers=h, json={"user_id": uid, "expected_version": 1})
    events = audit_log.list_auth_events()
    names = {event["event"] for event in events}
    assert {audit_log.EVENT_USER_CREATED, audit_log.EVENT_TEAM_CREATED, audit_log.EVENT_MEMBER_UPDATED} <= names


def test_b1_bootstrap_is_closed_by_default(monkeypatch):
    monkeypatch.delenv("GW_AUTH_BOOTSTRAP_ENABLED", raising=False)
    monkeypatch.delenv("GW_AUTH_BOOTSTRAP_WINDOW", raising=False)
    c = client()
    assert c.post("/api/asset-auth/bootstrap", json={"display_name": "关闭", "role": "admin"}).status_code == 403


def test_b1_invalid_decision_and_audit_and_member_idempotency(monkeypatch):
    monkeypatch.setenv("GW_AUTH_BOOTSTRAP_ENABLED", "true")
    from gw.core import audit
    audit.reset_audit_log()
    c = client(); h = headers()
    assert c.post("/api/asset-auth/bootstrap", json={"display_name": "审计根", "role": "admin"}).status_code == 201
    user = c.post("/api/asset-auth/users", headers=h, json={"display_name": "成员", "role": "editor"}).json()["user"]
    team = c.post("/api/asset-auth/teams", headers=h, json={"name": "审计团队"}).json()["team"]
    tid, version = team["team_id"], team["version"]
    first = c.put(f"/api/asset-auth/teams/{tid}/members", headers=h, json={"user_id": user["user_id"], "role": "editor", "expected_version": version})
    assert first.status_code == 200
    replay = c.put(f"/api/asset-auth/teams/{tid}/members", headers=h, json={"user_id": user["user_id"], "role": "editor", "expected_version": version})
    assert replay.status_code == 200
    assert c.put("/api/asset-auth/operation-approvals/ap-404", headers=h, json={"decision": "garbage", "expected_version": 1}).status_code in (400, 422)
    events = audit.list_auth_events()
    assert any(e["event"] == audit.EVENT_TEAM_CREATED for e in events)
    assert any(e["event"] == audit.EVENT_MEMBER_UPDATED for e in events)


def test_b1_approval_cursor_is_validated():
    c = client(); h = headers()
    assert c.get("/api/asset-auth/operation-approvals?cursor=bad", headers=h).status_code == 422



def test_b1_oidc_bootstrap_requires_existing_authentication(monkeypatch):
    monkeypatch.setenv("GW_AUTH_MODE", "oidc")
    monkeypatch.setenv("GW_AUTH_BOOTSTRAP_ENABLED", "true")
    c = client()
    response = c.post("/api/asset-auth/bootstrap", json={"display_name": "匿名 OIDC", "role": "admin"})
    assert response.status_code == 401
