"""C5-T2 项目写门禁 HTTP 契约与 issue #42 回归。"""

from __future__ import annotations

import hashlib

from fastapi.testclient import TestClient


OWNER_TOKEN = "c5-t2-owner-token"
ADMIN_TOKEN = "c5-t2-admin-token"
GOVERNOR_TOKEN = "c5-t2-governor-token"
READER_TOKEN = "c5-t2-reader-token"

OWNER = {"Authorization": f"Bearer {OWNER_TOKEN}", "X-User-Role": "editor"}
ADMIN = {"Authorization": f"Bearer {ADMIN_TOKEN}", "X-User-Role": "admin"}
GOVERNOR = {"Authorization": f"Bearer {GOVERNOR_TOKEN}", "X-User-Role": "governor"}
READER = {"Authorization": f"Bearer {READER_TOKEN}", "X-User-Role": "editor"}


def _subject(token: str) -> str:
    return "local:" + hashlib.sha256(token.encode("utf-8")).hexdigest()[:32]


def _create_project(client: TestClient, *, gates: bool = False) -> str:
    body = {"name": "C5-T2 项目", "project_type": "film"}
    if gates:
        body.update({"client_request_id": "c5-t2-gate-001", "gates": [{"code": "P1", "name": "首审"}]})
    response = client.post("/api/asset-registry/projects", headers=OWNER, json=body)
    assert response.status_code == 201, response.text
    return response.json()["project"]["project_id"]


def _grant_and_revoke_reader(client: TestClient, project_id: str) -> None:
    response = client.post(
        f"/api/asset-registry/projects/{project_id}/read-grants",
        headers=OWNER,
        json={"subject": _subject(READER_TOKEN)},
    )
    assert response.status_code == 200, response.text
    response = client.post(
        f"/api/asset-registry/projects/{project_id}/read-grants/revoke",
        headers=OWNER,
        json={"subject": _subject(READER_TOKEN)},
    )
    assert response.status_code == 200, response.text


def test_project_patch_gate_hides_read_grantee_and_preserves_name(client: TestClient):
    project_id = _create_project(client)
    response = client.post(
        f"/api/asset-registry/projects/{project_id}/read-grants",
        headers=OWNER,
        json={"subject": _subject(READER_TOKEN)},
    )
    assert response.status_code == 200, response.text

    denied = client.patch(
        f"/api/asset-registry/projects/{project_id}",
        headers=READER,
        json={"name": "不应写入", "expected_version": 1},
    )
    assert denied.status_code == 404
    assert denied.json()["detail"]["code"] == "PROJECT_NOT_FOUND"
    current = client.get(f"/api/projects/{project_id}", headers=OWNER)
    assert current.status_code == 200
    assert current.json()["project"]["name"] == "C5-T2 项目"


def test_project_writers_use_cas_and_stale_version_remains_conflict(client: TestClient):
    project_id = _create_project(client)
    updated = client.patch(
        f"/api/asset-registry/projects/{project_id}",
        headers=OWNER,
        json={"name": "owner 修改", "expected_version": 1},
    )
    assert updated.status_code == 200, updated.text
    admin_update = client.patch(
        f"/api/asset-registry/projects/{project_id}",
        headers=ADMIN,
        json={"name": "admin 修改", "expected_version": 2},
    )
    assert admin_update.status_code == 200, admin_update.text
    governor_update = client.patch(
        f"/api/asset-registry/projects/{project_id}",
        headers=GOVERNOR,
        json={"name": "governor 修改", "expected_version": 3},
    )
    assert governor_update.status_code == 200, governor_update.text
    stale = client.patch(
        f"/api/asset-registry/projects/{project_id}",
        headers=OWNER,
        json={"name": "过期修改", "expected_version": 1},
    )
    assert stale.status_code == 409, stale.text
    assert stale.json()["detail"]["code"] == "VERSION_CONFLICT"


def test_project_bound_entity_asset_and_gate_writes_share_gate(client: TestClient):
    project_id = _create_project(client, gates=True)
    _grant_and_revoke_reader(client, project_id)

    entity = client.post(
        f"/api/asset-registry/projects/{project_id}/entities",
        headers=READER,
        json={"name": "不应创建"},
    )
    assert entity.status_code == 404
    assert entity.json()["detail"]["code"] == "PROJECT_NOT_FOUND"
    created_entity = client.post(
        f"/api/asset-registry/projects/{project_id}/entities",
        headers=OWNER,
        json={"name": "owner 创建"},
    )
    assert created_entity.status_code == 200, created_entity.text

    asset = client.post(
        "/api/asset-registry/assets/import",
        headers=OWNER,
        files={"file": ("c5-t2.txt", b"fixture", "text/plain")},
    )
    assert asset.status_code == 200, asset.text
    asset_id = asset.json()["asset_ids"][0]
    linked = client.post(
        f"/api/asset-registry/projects/{project_id}/assets",
        headers=READER,
        json={"asset_ids": [asset_id]},
    )
    assert linked.status_code == 404
    assert linked.json()["detail"]["code"] == "PROJECT_NOT_FOUND"
    owner_linked = client.post(
        f"/api/asset-registry/projects/{project_id}/assets",
        headers=OWNER,
        json={"asset_ids": [asset_id]},
    )
    assert owner_linked.status_code == 200, owner_linked.text

    gates = client.get(f"/api/asset-registry/projects/{project_id}/gates", headers=OWNER)
    assert gates.status_code == 200, gates.text
    gate_id = gates.json()["gates"][0]["gate_id"]
    gate = client.patch(
        f"/api/asset-registry/project-gates/{gate_id}",
        headers=READER,
        json={"state": "approved", "expected_version": 1},
    )
    assert gate.status_code == 404
    assert gate.json()["detail"]["code"] == "PROJECT_NOT_FOUND"
    owner_gate = client.patch(
        f"/api/asset-registry/project-gates/{gate_id}",
        headers=OWNER,
        json={"state": "approved", "expected_version": 1},
    )
    assert owner_gate.status_code == 200, owner_gate.text


def test_archived_project_hides_nonwriter_and_is_readonly_for_writer(client: TestClient):
    project_id = _create_project(client)
    archived = client.request(
        "DELETE",
        f"/api/asset-registry/projects/{project_id}",
        headers=OWNER,
        json={"expected_version": 1},
    )
    assert archived.status_code == 200, archived.text
    denied = client.patch(
        f"/api/asset-registry/projects/{project_id}",
        headers=OWNER,
        json={"name": "不可写", "expected_version": 2},
    )
    assert denied.status_code == 403
    assert denied.json()["detail"]["code"] == "PROJECT_READ_ONLY"


def test_project_write_gate_authenticates_before_project_lookup(client: TestClient):
    response = client.patch(
        "/api/asset-registry/projects/unknown-project",
        json={"name": "匿名", "expected_version": 1},
    )
    assert response.status_code == 401


def test_legacy_open_project_list_is_visible_until_subject_is_revoked(client: TestClient):
    """O2 列表回归：legacy-open 对普通主体可见，撤销只隐藏该主体。"""
    from gw.api import routes_projects

    project_id = _create_project(client)
    service = routes_projects.default_projects_service
    with service._operation() as (root, state, _):
        state.setdefault("read_access", {}).pop(project_id, None)
        service._commit_state(root, state)

    visible = client.get("/api/asset-registry/projects", headers=READER)
    assert visible.status_code == 200
    assert any(item["project_id"] == project_id for item in visible.json()["projects"])

    _grant_and_revoke_reader(client, project_id)
    hidden = client.get("/api/asset-registry/projects", headers=READER)
    assert hidden.status_code == 200
    assert all(item["project_id"] != project_id for item in hidden.json()["projects"])

