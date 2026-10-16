"""C4-L3 项目写接口按项目读写授权校验。"""

from __future__ import annotations

import hashlib

from fastapi.testclient import TestClient

from gw.api import routes_projects


OWNER_TOKEN = "c4-l3-owner-token"
OTHER_TOKEN = "c4-l3-other-token"
GOV_TOKEN = "c4-l3-governor-token"
ADMIN_TOKEN = "c4-l3-admin-token"

OWNER = {"Authorization": f"Bearer {OWNER_TOKEN}", "X-User-Role": "editor"}
OTHER = {"Authorization": f"Bearer {OTHER_TOKEN}", "X-User-Role": "editor"}
GOV = {"Authorization": f"Bearer {GOV_TOKEN}", "X-User-Role": "governor"}
ADMIN = {"Authorization": f"Bearer {ADMIN_TOKEN}", "X-User-Role": "admin"}


def _subject(token: str) -> str:
    return "local:" + hashlib.sha256(token.encode("utf-8")).hexdigest()[:32]


def _owner_key(token: str) -> str:
    return hashlib.sha256(("local_dev\0" + _subject(token)).encode("utf-8")).hexdigest()


def _create_project(client: TestClient, name: str = "C4-L3 项目") -> str:
    response = client.post(
        "/api/asset-registry/projects",
        headers=OWNER,
        json={"name": name, "project_type": "film"},
    )
    assert response.status_code == 201, response.text
    return response.json()["project"]["project_id"]


def _grant(client: TestClient, project_id: str, token: str) -> None:
    response = client.post(
        f"/api/asset-registry/projects/{project_id}/read-grants",
        headers=OWNER,
        json={"subject": _subject(token)},
    )
    assert response.status_code == 200, response.text


def _revoke(client: TestClient, project_id: str, token: str) -> None:
    response = client.post(
        f"/api/asset-registry/projects/{project_id}/read-grants/revoke",
        headers=OWNER,
        json={"subject": _subject(token)},
    )
    assert response.status_code == 200, response.text


def _patch(client: TestClient, project_id: str, headers: dict, name: str, version: int = 1):
    return client.patch(
        f"/api/asset-registry/projects/{project_id}",
        headers=headers,
        json={"name": name, "expected_version": version},
    )


def _project(client: TestClient, project_id: str, headers: dict = OWNER) -> dict:
    response = client.get(f"/api/projects/{project_id}", headers=headers)
    assert response.status_code == 200, response.text
    return response.json()["project"]


def _audit() -> list[dict]:
    service = routes_projects.default_projects_service
    with service._operation() as (_, state, __):
        return list(state.get("read_audit") or [])


def _make_legacy(project_id: str) -> None:
    service = routes_projects.default_projects_service
    with service._operation() as (root, state, __):
        state.setdefault("read_access", {}).pop(project_id, None)
        service._commit_state(root, state)


def test_revoked_reader_patch_is_hidden_and_does_not_mutate(client: TestClient):
    project_id = _create_project(client)
    _grant(client, project_id, OTHER_TOKEN)
    _revoke(client, project_id, OTHER_TOKEN)

    denied = _patch(client, project_id, OTHER, "不应写入")

    assert denied.status_code == 404, denied.text
    assert denied.json()["detail"]["code"] == "PROJECT_NOT_FOUND"
    assert _project(client, project_id)["name"] == "C4-L3 项目"
    entry = _audit()[-1]
    assert entry["event"] == "project.write_denied"
    assert entry["actor"] == _owner_key(OTHER_TOKEN)[:12]
    assert OTHER_TOKEN not in str(entry)
    assert _subject(OTHER_TOKEN) not in str(entry)


def test_reader_without_write_access_is_hidden(client: TestClient):
    project_id = _create_project(client)
    _grant(client, project_id, OTHER_TOKEN)

    denied = _patch(client, project_id, OTHER, "读者不应写入")

    assert denied.status_code == 404, denied.text
    assert denied.json()["detail"]["code"] == "PROJECT_NOT_FOUND"
    assert _project(client, project_id)["name"] == "C4-L3 项目"
    assert _audit()[-1]["event"] == "project.write_denied"


def test_owner_and_admin_can_patch_project(client: TestClient):
    project_id = _create_project(client)

    owner_result = _patch(client, project_id, OWNER, "owner 修改")
    admin_result = _patch(client, project_id, ADMIN, "admin 修改", version=2)

    assert owner_result.status_code == 200, owner_result.text
    assert admin_result.status_code == 200, admin_result.text
    assert _project(client, project_id)["name"] == "admin 修改"


def test_governor_can_patch_after_its_read_grant_is_revoked(client: TestClient):
    project_id = _create_project(client)
    _grant(client, project_id, GOV_TOKEN)
    _revoke(client, project_id, GOV_TOKEN)

    result = _patch(client, project_id, GOV, "governor 修改")

    assert result.status_code == 200, result.text
    assert _project(client, project_id)["name"] == "governor 修改"


def test_legacy_open_project_remains_readable_but_write_still_requires_owner_or_governance(client: TestClient):
    project_id = _create_project(client)
    _make_legacy(project_id)

    readable = client.get(f"/api/projects/{project_id}", headers=OTHER)
    denied = _patch(client, project_id, OTHER, "legacy 不应写入")

    assert readable.status_code == 200, readable.text
    assert denied.status_code == 404, denied.text
    assert denied.json()["detail"]["code"] == "PROJECT_NOT_FOUND"
    assert _project(client, project_id)["name"] == "C4-L3 项目"
