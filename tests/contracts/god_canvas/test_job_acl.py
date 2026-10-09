"""R0：使用真实项目真源与本地账户Cookie验证内存任务边界。"""
import json
import sqlite3
import pytest
from fastapi.testclient import TestClient
from gw.api.app import app
from gw.api import routes_god_canvas, routes_observability
from gw.god_canvas.service import GodCanvasService
from gw.god_canvas.models import CanvasCreateRequest, CanvasTopologyUpdateRequest, CanvasNode
from gw.projects_hub import service as projects
from gw.projects_hub.models import ProjectCreateRequest
from gw.core.auth import AuthContext
from gw.core import audit, local_accounts
from gw.observability.service import ObservabilityService

PASSWORD = "CanvasTest2026"

@pytest.fixture
def cookie_env(monkeypatch, tmp_path):
    monkeypatch.setenv("GW_AUTH_MODE", "local_account")
    db_path = tmp_path / "auth.sqlite3"
    monkeypatch.setenv("GW_LOCAL_AUTH_DB", str(db_path))
    source = projects.ProjectsService(seed_golden_fixture=False)
    canvas = GodCanvasService(seed_golden_fixture=True)
    monkeypatch.setattr(projects, "default_projects_service", source)
    monkeypatch.setattr(routes_god_canvas, "default_god_canvas_service", canvas)
    observer = ObservabilityService(canvas_service=canvas, projects_service=source)
    monkeypatch.setattr(routes_observability, "default_observability_service", observer)
    audit.reset_audit_log()
    with TestClient(app, base_url="http://localhost", client=("127.0.0.1", 50000), headers={"Origin": "http://localhost"}) as client:
        response = client.post("/api/asset-auth/local/setup", json={"username": "owner", "password": PASSWORD})
        assert response.status_code == 201, response.text
        owner_cookie = client.cookies.get("gw_session")
        with sqlite3.connect(db_path) as db:
            owner_id = db.execute("SELECT user_id FROM local_users WHERE username='owner'").fetchone()[0]
            salt = b"1234567890123456"
            db.execute("INSERT INTO local_users VALUES (?, ?, ?, ?, ?)", ("usr-other", "other", salt, local_accounts.password_hash(PASSWORD, salt), "admin"))
        key = projects.owner_key_for_context(AuthContext(role="admin", subject=owner_id, identity_domain="local_account"))
        project = source.create_project(ProjectCreateRequest(name="Cookie真实项目", project_type="other"), owner_key=key)
        item = canvas.create_canvas(CanvasCreateRequest(project_id=project.project_id, title="权限画布"))
        canvas.update_topology(item.canvas_id, CanvasTopologyUpdateRequest(expected_version=1, nodes=[CanvasNode(entity_id="node-owned", kind="input")], connections=[]))
        yield client, source, canvas, project.project_id, item.canvas_id, owner_cookie, db_path


def submit(client, canvas_id):
    return client.post(f"/api/canvases/{canvas_id}/tasks", json={"entry_nodes": ["node-owned"]})


def test_anonymous_seed_and_forged_headers_hidden(cookie_env):
    client, _, canvas, _, cid, _, _ = cookie_env
    client.cookies.clear()
    assert client.get("/api/jobs/job-0001").status_code == 401
    assert client.get("/api/jobs/job-0001", headers={"Authorization": "Bearer forged", "X-User-Role": "admin"}).status_code == 401
    assert submit(client, cid).status_code == 401
    assert canvas.get_job("job-0001").job_id == "job-0001"
    assert len(canvas.list_jobs()) == 1


def test_owner_cross_account_and_unowned_seed(cookie_env):
    client, _, canvas, _, cid, owner_cookie, _ = cookie_env
    assert client.get("/api/jobs/job-0001").status_code == 404
    accepted = submit(client, cid)
    assert accepted.status_code == 202, accepted.text
    job_id = accepted.json()["job_id"]
    own = client.get(f"/api/jobs/{job_id}")
    assert own.status_code == 200 and own.json()["prototype"] is True
    assert owner_cookie not in own.text and "owner_key" not in own.text
    assert client.post("/api/asset-auth/logout").status_code == 204
    assert client.post("/api/asset-auth/local/login", json={"username": "other", "password": PASSWORD}).status_code == 200
    before = len(canvas.list_jobs())
    assert client.get(f"/api/jobs/{job_id}").status_code == 404
    assert submit(client, cid).status_code == 404
    assert len(canvas.list_jobs()) == before
    response = client.get("/api/observability/tasks")
    assert response.status_code == 200 and job_id not in response.text and "job-0001" not in response.text
    overview = client.get("/api/observability/overview")
    assert overview.json()["jobs"]["total"] == 0


@pytest.mark.parametrize("lifecycle", ["archive", "delete"])
def test_current_project_lifecycle_rechecked(cookie_env, lifecycle):
    client, source, _, pid, cid, _, _ = cookie_env
    accepted = submit(client, cid)
    assert accepted.status_code == 202
    job_id = accepted.json()["job_id"]
    if lifecycle == "archive":
        source.archive_project(pid, expected_version=1)
    else:
        source.archive_project(pid, expected_version=1)
        source.move_to_trash(pid, expected_version=2)
    assert client.get(f"/api/jobs/{job_id}").status_code == 404
    assert submit(client, cid).status_code == 404
    assert job_id not in client.get("/api/observability/tasks").text


@pytest.mark.parametrize("role", ["reviewer", "readonly", "editor", "admin"])
def test_cookie_database_role_not_header_and_audit(cookie_env, role, caplog):
    caplog.set_level("INFO", logger="gw.audit")
    client, _, canvas, _, cid, token, db_path = cookie_env
    with sqlite3.connect(db_path) as db:
        db.execute("UPDATE local_users SET role=? WHERE username='owner'", (role,))
    response = client.post(f"/api/canvases/{cid}/tasks", json={"entry_nodes": ["node-owned"]}, headers={"X-User-Role": "admin"})
    assert response.status_code == (202 if role in {"editor", "admin"} else 403)
    serialized = caplog.text
    assert "canvas_job_access" in serialized
    assert token not in serialized and PASSWORD not in serialized
    assert f'"status_code": {response.status_code}' in serialized
    assert len(canvas.list_jobs()) == (2 if role in {"editor", "admin"} else 1)


def test_job_binding_does_not_override_project_source_owner(cookie_env):
    client, source, canvas, pid, cid, _, _ = cookie_env
    accepted = submit(client, cid)
    assert accepted.status_code == 202
    job_id = accepted.json()["job_id"]
    # 模拟真源owner被治理流程撤销，保留旧任务侧表以检验逐次复核。
    with source._operation() as (root, state, _):
        state["owners"][pid] = projects.owner_key_for_context(AuthContext(role="admin", subject="usr-other", identity_domain="local_account"))
        source._commit_state(root, state)
    assert client.get(f"/api/jobs/{job_id}").status_code == 404
    assert submit(client, cid).status_code == 404
    assert job_id not in client.get("/api/observability/tasks").text
    assert canvas.get_job(job_id).job_id == job_id


def test_readonly_query_and_expired_invalid_cookie(cookie_env, caplog):
    client, _, _, _, cid, token, db_path = cookie_env
    caplog.set_level("INFO", logger="gw.audit")
    accepted = submit(client, cid)
    assert accepted.status_code == 202
    job_id = accepted.json()["job_id"]
    with sqlite3.connect(db_path) as db:
        db.execute("UPDATE local_users SET role='readonly' WHERE username='owner'")
    assert client.get(f"/api/jobs/{job_id}", headers={"X-User-Role": "admin"}).status_code == 200
    with sqlite3.connect(db_path) as db:
        db.execute("UPDATE local_sessions SET expires_at=0")
    assert client.get(f"/api/jobs/{job_id}").status_code == 401
    client.cookies.clear()
    client.cookies.set("gw_session", "invalid-test-cookie")
    assert client.get(f"/api/jobs/{job_id}").status_code == 401
    assert '"status_code": 401' in caplog.text
    assert token not in caplog.text and "invalid-test-cookie" not in caplog.text
