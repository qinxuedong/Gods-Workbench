"""C5-T1 permission matrix for C5-AC1/AC7/AC8/AC9 HTTP contracts."""
from __future__ import annotations

import pytest

from tests.contracts.projects.c5_permission_fixtures import headers, make_fixture

PRINCIPALS = (
    "anonymous",
    "owner",
    "admin",
    "governor",
    "revoked_editor",
    "cross_project_editor",
)
STATES = ("active", "archived", "deleted", "unknown")
WRITERS = ("owner", "admin", "governor")
NON_WRITERS = ("revoked_editor", "cross_project_editor")

RENAME_CASES = []
for _state in STATES:
    for _principal in PRINCIPALS:
        _marks = []
        if _principal in NON_WRITERS and _state != "unknown":
            _reason = (
                "C5-T2: non-writer rename must be 404 PROJECT_NOT_FOUND (#42)"
                if _state == "active"
                else "C5-T2: non-writer on archived/deleted project must be 404, not 403"
            )
            _marks.append(pytest.mark.xfail(strict=True, reason=_reason))
        elif _principal in WRITERS and _state in ("archived", "deleted"):
            _marks.append(pytest.mark.xfail(
                strict=True,
                reason="C5-T2: writer on archived/deleted project must get 403 PROJECT_READ_ONLY (now FORBIDDEN)",
            ))
        RENAME_CASES.append(pytest.param(_principal, _state, marks=_marks))


def envelope(response, code):
    body = response.json()
    assert isinstance(body.get("detail"), dict)
    assert {"code", "message"} <= set(body["detail"])
    assert body["detail"]["code"] == code


@pytest.mark.parametrize("principal,state", RENAME_CASES)
def test_project_rename_matrix(client, principal, state):
    """Project rename obeys lifecycle and fail-closed visibility (C5-AC1/AC7)."""
    fx = make_fixture(client)
    pid = fx.create_project()
    if state == "archived":
        fx.archive(pid)
    elif state == "deleted":
        fx.trash(pid)
    elif state == "unknown":
        pid = "missing-project"
    target = fx.project(pid) if state != "unknown" else {"version": 1, "name": "C5 project"}
    response = client.patch(
        f"/api/asset-registry/projects/{pid}",
        headers={} if principal == "anonymous" else headers(principal),
        json={"name": "renamed", "expected_version": target["version"]},
    )
    if principal == "anonymous":
        assert response.status_code == 401
        envelope(response, "UNAUTHORIZED")
    elif state == "unknown":
        assert response.status_code == 404
        envelope(response, "PROJECT_NOT_FOUND")
    elif principal in NON_WRITERS:
        assert response.status_code == 404
        envelope(response, "PROJECT_NOT_FOUND")
        assert "version" not in response.text
    elif state in ("archived", "deleted"):
        assert response.status_code == 403
        envelope(response, "PROJECT_READ_ONLY")
    else:
        assert response.status_code == 200
        assert fx.project(pid)["name"] == "renamed"
    if state != "unknown" and response.status_code != 200:
        after = fx.project(pid)
        assert after["name"] == "C5 project"
        assert after["version"] == target["version"]


@pytest.mark.parametrize(
    "principal",
    (
        pytest.param("revoked_editor", marks=pytest.mark.xfail(strict=True, reason="C5-T2: project route CAS ordering")),
        pytest.param("cross_project_editor", marks=pytest.mark.xfail(strict=True, reason="C5-T2: project route CAS ordering")),
    ),
)
def test_cas_authorization_precedes_version_check(client, principal):
    """Unauthorized CAS requests must hide version details (C5-AC7)."""
    fx = make_fixture(client)
    pid = fx.create_project()
    before = fx.project(pid)
    response = client.patch(
        f"/api/asset-registry/projects/{pid}",
        headers=headers(principal),
        json={"name": "leak", "expected_version": 999},
    )
    assert response.status_code == 404
    envelope(response, "PROJECT_NOT_FOUND")
    assert "version" not in response.text
    assert fx.project(pid) == before


def test_writer_stale_version_conflicts_without_persisting(client):
    """Writers receive a CAS conflict without mutation (C5-AC7)."""
    fx = make_fixture(client)
    pid = fx.create_project()
    before = fx.project(pid)
    response = client.patch(
        f"/api/asset-registry/projects/{pid}",
        headers=headers("owner"),
        json={"name": "leak", "expected_version": 999},
    )
    assert response.status_code == 409
    assert fx.project(pid) == before


def test_canvas_project_context_and_patch(client):
    """Canvas create and valid CAS topology patch must pass (C5-AC8)."""
    fx = make_fixture(client)
    pid = fx.create_project()
    created = client.post(
        "/api/canvases", headers=headers("owner"), json={"project_id": pid, "title": "canvas"}
    )
    assert created.status_code in (200, 201)
    payload = created.json().get("canvas", created.json())
    cid = payload.get("id") or payload.get("canvas_id")
    assert cid
    patched = client.patch(
        f"/api/canvases/{cid}",
        headers=headers("owner"),
        json={"expected_version": payload.get("version", 1), "nodes": [], "connections": []},
    )
    assert patched.status_code in (200, 204)


@pytest.mark.xfail(strict=True, reason="C5-T4: missing-project create should fail closed")
def test_canvas_missing_project_create_fail_closed(client):
    """Missing project creation is a C5-AC8 fail-closed contract gap."""
    bad = client.post(
        "/api/canvases", headers=headers("owner"), json={"project_id": "missing-project", "title": "bad"}
    )
    assert bad.status_code == 404
    envelope(bad, "PROJECT_NOT_FOUND")


def test_workflow_project_context_fail_closed(client):
    """Workflow source context rejects missing projects (C5-AC9)."""
    fx = make_fixture(client)
    pid = fx.create_project()
    good = client.post(
        "/api/god_workflow/documents?name=c5",
        headers=headers("owner"),
        json={"metadata": {"source_context": {"project_id": pid}}, "nodes": []},
    )
    assert good.status_code in (200, 201)
    bad = client.post(
        "/api/god_workflow/documents?name=bad",
        headers=headers("owner"),
        json={"metadata": {"source_context": {"project_id": "missing-project"}}, "nodes": []},
    )
    assert bad.status_code == 404
    envelope(bad, "PROJECT_NOT_FOUND")


def test_workflow_project_context_rejects_revoked_editor(client):
    """A revoked editor cannot attach workflow context to a project (C5-AC9)."""
    fx = make_fixture(client)
    pid = fx.create_project()
    response = client.post(
        "/api/god_workflow/documents?name=restricted",
        headers=headers("revoked_editor"),
        json={"metadata": {"source_context": {"project_id": pid}}, "nodes": []},
    )
    assert response.status_code == 404
    envelope(response, "PROJECT_NOT_FOUND")
