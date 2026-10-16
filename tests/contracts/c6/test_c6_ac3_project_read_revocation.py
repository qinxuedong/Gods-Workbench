"""C6-AC3：撤销项目读权限后的资源、分享链接与审计脱敏回归。"""
from __future__ import annotations

import hashlib
import json

from fastapi.testclient import TestClient

OWNER_TOKEN = "c6-ac3-owner"
READER_TOKEN = "c6-ac3-reader"
OWNER = {"Authorization": f"Bearer {OWNER_TOKEN}", "X-User-Role": "editor"}
READER = {"Authorization": f"Bearer {READER_TOKEN}", "X-User-Role": "editor"}
GRANTS = "/api/asset-registry/projects/{project_id}/read-grants"


def _subject(token: str) -> str:
    return "local:" + hashlib.sha256(token.encode("utf-8")).hexdigest()[:32]


def _create_project(client: TestClient) -> str:
    response = client.post(
        "/api/asset-registry/projects",
        headers=OWNER,
        json={"name": "C6 AC3 read revoke", "project_type": "film"},
    )
    assert response.status_code == 201, response.text
    return response.json()["project"]["project_id"]


def _create_asset(client: TestClient) -> str:
    response = client.post(
        "/api/asset-registry/assets/import",
        headers=OWNER,
        json={"items": [{"name": "c6-ac3.png", "kind": "image"}]},
    )
    assert response.status_code == 200, response.text
    return response.json()["asset_ids"][0]


def _grant(client: TestClient, project_id: str) -> None:
    response = client.post(
        GRANTS.format(project_id=project_id),
        headers=OWNER,
        json={"subject": _subject(READER_TOKEN)},
    )
    assert response.status_code == 200, response.text


def _revoke(client: TestClient, project_id: str):
    return client.post(
        GRANTS.format(project_id=project_id) + "/revoke",
        headers=OWNER,
        json={"subject": _subject(READER_TOKEN)},
    )


def _audit_entries() -> list[dict]:
    from gw.api import routes_projects

    service = routes_projects.default_projects_service
    with service._operation() as (_, state, __):
        return list(state.get("read_audit") or [])


def test_c6_ac3_revocation_blocks_project_resource_share_and_redacts_audit(client: TestClient):
    """撤销后资源返回 403/404，reader 创建的分享失效，审计不落完整主体。"""
    project_id = _create_project(client)
    asset_id = _create_asset(client)
    linked = client.post(
        f"/api/asset-registry/projects/{project_id}/assets",
        headers=OWNER,
        json={"asset_ids": [asset_id]},
    )
    assert linked.status_code == 200, linked.text
    _grant(client, project_id)

    visible = client.get(f"/api/projects/{project_id}", headers=READER)
    assert visible.status_code == 200, visible.text
    registry_projects = client.get("/api/asset-registry/projects", headers=READER)
    assert registry_projects.status_code == 200, registry_projects.text
    assert project_id in [item["project_id"] for item in registry_projects.json().get("projects", [])]

    share = client.post(
        "/api/asset-reviews/shares",
        headers=READER,
        json={"asset_ids": [asset_id], "title": "C6 AC3 share"},
    )
    assert share.status_code == 201, share.text
    share_token = share.json()["token"]
    assert client.get(f"/api/public/shares/{share_token}").status_code == 200

    revoked = _revoke(client, project_id)
    assert revoked.status_code == 200, revoked.text

    blocked_project = client.get(f"/api/projects/{project_id}", headers=READER)
    assert blocked_project.status_code in (403, 404), blocked_project.text
    blocked_registry_projects = client.get("/api/asset-registry/projects", headers=READER)
    assert blocked_registry_projects.status_code == 200, blocked_registry_projects.text
    assert project_id not in [
        item["project_id"] for item in blocked_registry_projects.json().get("projects", [])
    ]
    blocked_share = client.get(f"/api/public/shares/{share_token}")
    assert blocked_share.status_code == 404, blocked_share.text
    assert blocked_share.json()["detail"]["code"] == "SHARE_NOT_FOUND"

    audit = _audit_entries()
    assert audit
    serialized = json.dumps(audit, ensure_ascii=False)
    assert OWNER_TOKEN not in serialized
    assert READER_TOKEN not in serialized
    for entry in audit:
        if entry.get("owner_key"):
            assert len(entry["owner_key"]) == 12
