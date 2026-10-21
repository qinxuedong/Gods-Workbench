"""画布持久化的重启、并发、失败关闭与真实主体隔离契约。"""

import json
import os
import sqlite3
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from gw.api import routes_canvas_closure, routes_god_canvas
from gw.api.app import app
from gw.canvas_closure.service import CanvasClosureService
from gw.core import local_accounts
from gw.core.auth import AuthContext, require_authenticated
from gw.core.errors import CleanroomException
from gw.core.storage import data_root
from gw.god_canvas.models import (
    CanvasCreateRequest,
    CanvasNode,
    CanvasPort,
    CanvasTopologyUpdateRequest,
    SubCanvasType,
)
from gw.god_canvas.service import GodCanvasService
from gw.god_canvas.tasks import SmartCanvasTaskRequest
from gw.projects_hub import service as projects
from gw.projects_hub.models import ProjectCreateRequest


@pytest.fixture
def persistent_canvas():
    source = GodCanvasService(seed_golden_fixture=True, persistent=True)
    assert source.list_canvases("prj-0001") == []
    assert source.list_jobs() == []
    context = require_authenticated("Bearer cleanroom-test", "editor")
    created = source.create_canvas(
        CanvasCreateRequest(
            project_id="prj-0001",
            title="持久画布",
            sub_canvas_type="storyboard",
            initial_payload={
                "nodes": [
                    {
                        "entity_id": "node-1",
                        "kind": "text_prompt",
                        "ports": [{"port_id": "out-1", "data_type": "TEXT"}],
                    }
                ]
            },
        ),
        owner_key=projects.owner_key_for_context(context),
    )
    return source, created.canvas_id, context


def test_topology_job_lifecycle_and_sequence_survive_new_process(persistent_canvas):
    source, cid, context = persistent_canvas
    job = source.submit_smart_task(
        cid,
        SmartCanvasTaskRequest(expected_version=1, entry_nodes=["node-1"]),
        "Bearer cleanroom-test",
    )
    source.update_job_state(job.job_id, "running")
    source.update_job_state(job.job_id, "paused")
    source.touch_canvas(cid, "archive", expected_version=1)
    code = """
import json
from gw.god_canvas.service import GodCanvasService
from gw.god_canvas.models import CanvasCreateRequest
s=GodCanvasService(seed_golden_fixture=False,persistent=True)
t=s.get_topology('cv-0001');j=s.get_job('job-0001')
c=s.create_canvas(CanvasCreateRequest(project_id='prj-0001',title='第二张'))
print(json.dumps({'version':t.version,'kind':t.nodes[0].kind,'port':t.nodes[0].ports[0].data_type,'job':j.state,'new_id':c.canvas_id,'archived':bool(s.ensure_lifecycle('cv-0001')['archived_at']),'sub_canvas':s.get_canvas('cv-0001').sub_canvas_type}))
"""
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=Path(__file__).resolve().parents[3],
        env=os.environ.copy(),
        capture_output=True,
        text=True,
        check=True,
    )
    assert json.loads(result.stdout) == {
        "version": 2,
        "kind": "text_prompt",
        "port": "TEXT",
        "job": "paused",
        "new_id": "cv-0002",
        "archived": True,
        "sub_canvas": "storyboard",
    }
    assert (
        json.loads(
            (data_root() / "canvases" / (cid + ".json")).read_text(encoding="utf-8")
        )["version"]
        == 2
    )
    assert (
        GodCanvasService(persistent=True).get_owned_job(job.job_id, context).state
        == "paused"
    )


def test_two_service_instances_contend_on_one_cas(persistent_canvas):
    _, cid, _ = persistent_canvas
    services = [GodCanvasService(persistent=True), GodCanvasService(persistent=True)]

    def update(source):
        try:
            return source.update_topology(
                cid,
                CanvasTopologyUpdateRequest(
                    expected_version=1,
                    nodes=[CanvasNode(entity_id="new", kind="image_asset")],
                ),
            ).version
        except CleanroomException as exc:
            assert exc.code == "CANVAS_VERSION_CONFLICT"
            return exc.status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(update, services)) == [2, 409]
    assert GodCanvasService(persistent=True).get_topology(cid).version == 2


def test_registry_write_failure_does_not_commit_or_advance_version(
    persistent_canvas, monkeypatch
):
    source, cid, _ = persistent_canvas
    original = (data_root() / "canvases_registry.json").read_bytes()

    def fail(_):
        raise OSError("测试注入写失败")

    monkeypatch.setattr(source._state, "write", fail)
    with pytest.raises(CleanroomException) as failure:
        source.update_topology(
            cid, CanvasTopologyUpdateRequest(expected_version=1, nodes=[])
        )
    assert failure.value.code == "CANVAS_STORAGE_UNAVAILABLE"
    assert (data_root() / "canvases_registry.json").read_bytes() == original
    recovered = GodCanvasService(persistent=True).get_topology(cid)
    assert recovered.version == 1 and recovered.nodes[0].entity_id == "node-1"
    assert (
        json.loads(
            (data_root() / "canvases" / (cid + ".json")).read_text(encoding="utf-8")
        )["version"]
        == 1
    )


@pytest.mark.parametrize("corrupt", ["{broken", "{}", "[]", '{"schema":"unknown"}'])
def test_corrupt_registry_fails_closed_without_empty_overwrite(
    persistent_canvas, corrupt
):
    _, cid, _ = persistent_canvas
    path = data_root() / "canvases_registry.json"
    path.write_text(corrupt, encoding="utf-8")
    with pytest.raises(CleanroomException) as failure:
        GodCanvasService(persistent=True).get_topology(cid)
    assert failure.value.status_code == 503
    assert path.read_text(encoding="utf-8") == corrupt


def test_missing_projection_rebuilt_from_atomic_registry(persistent_canvas):
    _, cid, _ = persistent_canvas
    path = data_root() / "canvases" / (cid + ".json")
    path.unlink()
    assert GodCanvasService(persistent=True).get_topology(cid).version == 1
    assert json.loads(path.read_text(encoding="utf-8"))["canvas_id"] == cid


def test_invalid_utf8_projection_is_rebuilt_but_registry_is_not_overwritten(
    persistent_canvas,
):
    _, cid, _ = persistent_canvas
    path = data_root() / "canvases" / (cid + ".json")
    path.write_bytes(b"\xff\xfeinvalid")
    assert GodCanvasService(persistent=True).get_topology(cid).version == 1
    assert json.loads(path.read_text(encoding="utf-8"))["canvas_id"] == cid
    registry = data_root() / "canvases_registry.json"
    registry.write_bytes(b"\xff\xfeinvalid")
    with pytest.raises(CleanroomException) as failure:
        GodCanvasService(persistent=True).get_topology(cid)
    assert failure.value.code == "CANVAS_STORAGE_UNAVAILABLE"
    assert registry.read_bytes() == b"\xff\xfeinvalid"


def test_invalid_initial_topology_does_not_reserve_a_canvas(persistent_canvas):
    source, _, _ = persistent_canvas
    with pytest.raises(CleanroomException) as failure:
        source.create_canvas(
            CanvasCreateRequest(
                project_id="prj-0001",
                initial_payload={
                    "nodes": [],
                    "connections": [
                        {"connection_id": "bad", "from": "missing", "to": "missing"}
                    ],
                },
            )
        )
    assert failure.value.code == "INVALID_TOPOLOGY"
    assert len(GodCanvasService(persistent=True).list_canvases("prj-0001")) == 1
    assert (
        source.create_canvas(CanvasCreateRequest(project_id="prj-0001")).canvas_id
        == "cv-0002"
    )


def test_reference_initialization_is_one_commit_and_rolls_back(
    persistent_canvas, monkeypatch
):
    source, _, _ = persistent_canvas
    closure = CanvasClosureService(canvas_service=source)
    writes = []
    original_write = source._state.write

    def record(value):
        writes.append(value)
        return original_write(value)

    monkeypatch.setattr(source._state, "write", record)
    created = closure.save_reference_canvas(
        "参考", [], [], [], [], {}, owner_key="test-owner"
    )
    assert len(writes) == 1 and created["canvas"]["kind"] == "reference"
    assert source.get_reference_payload(created["canvas"]["canvas_id"])["items"] == []

    def fail(_):
        raise OSError("测试提交失败")

    monkeypatch.setattr(source._state, "write", fail)
    with pytest.raises(CleanroomException):
        closure.save_reference_canvas(
            "失败参考", [], [], [], [], {}, owner_key="test-owner"
        )
    assert len(GodCanvasService(persistent=True).list_canvases("default")) == 1


def test_project_and_viewport_remain_consistent_across_move(persistent_canvas):
    source, cid, _ = persistent_canvas
    source.update_canvas_meta(cid, {"project_id": "default"}, expected_version=1)
    source = GodCanvasService(persistent=True)
    assert (
        source.get_topology(cid).project_id
        == source.get_canvas(cid).project_id
        == "default"
    )
    source.update_canvas_meta(cid, {"project_id": "prj-0001"}, expected_version=2)
    source.update_topology(
        cid,
        CanvasTopologyUpdateRequest(
            expected_version=3, nodes=[], viewport={"x": 12, "y": 24, "zoom": 1.5}
        ),
    )
    restarted = GodCanvasService(persistent=True)
    assert (
        restarted.get_topology(cid).project_id
        == restarted.get_canvas(cid).project_id
        == "prj-0001"
    )
    assert restarted.get_topology(cid).viewport == {"x": 12, "y": 24, "zoom": 1.5}


def test_persisted_mutations_require_cas_and_purge_keeps_sequence(persistent_canvas):
    source, cid, _ = persistent_canvas
    for action in [
        lambda: source.update_canvas_meta(cid, {"title": "覆盖"}),
        lambda: source.touch_canvas(cid, "archive"),
        lambda: source.move_to_trash(cid),
        lambda: source.purge_canvas(cid),
        lambda: source.submit_smart_task(
            cid, SmartCanvasTaskRequest(entry_nodes=["node-1"]), "Bearer cleanroom-test"
        ),
    ]:
        with pytest.raises(CleanroomException) as failure:
            action()
        assert failure.value.code == "EXPECTED_VERSION_REQUIRED"
    source.move_to_trash(cid, expected_version=1)
    restarted = GodCanvasService(persistent=True)
    assert restarted.list_trash()[0]["canvas_id"] == cid
    with pytest.raises(CleanroomException) as failure:
        restarted.purge_canvas(cid, expected_version=1)
    assert failure.value.status_code == 409
    restarted.purge_canvas(cid, expected_version=2)
    assert (
        GodCanvasService(persistent=True)
        .create_canvas(CanvasCreateRequest(project_id="prj-0001"))
        .canvas_id
        == "cv-0002"
    )


@pytest.mark.parametrize(
    "data_type", ["TEXT", "IMAGE", "VIDEO", "AUDIO", "MASK", "LATENT"]
)
def test_typed_ports_roundtrip(data_type):
    port = CanvasPort(port_id="in-1", data_type=data_type)
    assert port.model_dump(mode="json")["data_type"] == data_type
    with pytest.raises(ValidationError):
        CanvasNode(entity_id="a", kind="workflow_tool", ports=[port, port])


@pytest.fixture
def persistent_http(monkeypatch, tmp_path):
    monkeypatch.setenv("GW_AUTH_MODE", "local_account")
    db = tmp_path / "auth" / "auth.sqlite3"
    monkeypatch.setenv("GW_LOCAL_AUTH_DB", str(db))
    source = projects.ProjectsService(seed_golden_fixture=False)
    canvas = GodCanvasService(seed_golden_fixture=False, persistent=True)
    monkeypatch.setattr(projects, "default_projects_service", source)
    monkeypatch.setattr(routes_god_canvas, "default_god_canvas_service", canvas)
    monkeypatch.setattr(
        routes_canvas_closure, "closure", CanvasClosureService(canvas_service=canvas)
    )
    password = "CanvasPersistence2026"
    with TestClient(
        app,
        base_url="http://localhost",
        client=("127.0.0.1", 50000),
        headers={"Origin": "http://localhost"},
    ) as client:
        assert (
            client.post(
                "/api/asset-auth/local/setup",
                json={"username": "owner", "password": password},
            ).status_code
            == 201
        )
        owner_cookie = client.cookies.get("gw_session")
        with sqlite3.connect(db) as conn:
            uid = conn.execute(
                "SELECT user_id FROM local_users WHERE username='owner'"
            ).fetchone()[0]
            salt = b"1234567890123456"
            conn.execute(
                "INSERT INTO local_users VALUES (?, ?, ?, ?, ?)",
                (
                    "usr-other",
                    "other",
                    salt,
                    local_accounts.password_hash(password, salt),
                    "admin",
                ),
            )
        owner = projects.owner_key_for_context(
            AuthContext(role="admin", subject=uid, identity_domain="local_account")
        )
        project = source.create_project(
            ProjectCreateRequest(name="真实归属", project_type="other"), owner_key=owner
        )
        response = client.post(
            "/api/canvases",
            json={"project_id": project.project_id, "title": "所有者画布"},
        )
        assert response.status_code == 201, response.text
        cid = response.json()["canvas"]["canvas_id"]
        yield client, canvas, source, project.project_id, cid, owner_cookie, password


def test_persistent_http_owner_anonymous_cross_account_and_reference(persistent_http):
    client, canvas, source, pid, cid, cookie, password = persistent_http
    assert client.get(f"/api/canvases/{cid}").status_code == 200
    reference = client.post("/api/reference-canvases", json={"title": "私有参考"})
    assert reference.status_code == 201, reference.text
    rid = reference.json()["canvas"]["canvas_id"]
    assert len(client.get("/api/reference-canvases").json()["canvases"]) == 1
    client.cookies.clear()
    assert client.get(f"/api/canvases/{cid}").status_code == 401
    assert client.get("/api/canvases", params={"project_id": pid}).status_code == 401
    assert (
        client.post(
            "/api/asset-auth/local/login",
            json={"username": "other", "password": password},
        ).status_code
        == 200
    )
    for method, url, payload in [
        ("GET", f"/api/canvases/{cid}", None),
        (
            "PATCH",
            f"/api/canvases/{cid}",
            {"expected_version": 1, "nodes": [], "connections": []},
        ),
        ("POST", f"/api/canvases/{cid}/meta", {"title": "劫持", "expected_version": 1}),
        (
            "POST",
            f"/api/canvases/{cid}/touch?operation=archive&expected_version=1",
            None,
        ),
        ("DELETE", f"/api/canvases/{cid}?expected_version=1", None),
        ("POST", f"/api/canvases/{cid}/purge?expected_version=1", None),
        ("POST", f"/api/canvases/{cid}/workflow/export", {"format": "json"}),
        ("POST", f"/api/canvases/{cid}/restore", {"expected_version": 1}),
        (
            "POST",
            "/api/reference-canvases",
            {"title": "劫持参考", "canvas_id": rid, "expected_version": 2},
        ),
    ]:
        response = client.request(method, url, json=payload)
        assert response.status_code == 404, (method, url, response.text)
    assert (
        client.post(
            "/api/canvases", json={"project_id": pid, "title": "跨账户"}
        ).status_code
        == 404
    )
    assert client.get("/api/reference-canvases").json()["canvases"] == []
    assert client.get("/api/canvas-assets").json()["canvases"] == []
    assert all(
        category["count"] == 0
        for category in client.get("/api/canvas-assets").json()["categories"]
    )
    assert client.get("/api/canvases/trash").json()["canvases"] == []
    client.cookies.clear()
    client.cookies.set("gw_session", cookie)
    assert client.get(f"/api/canvases/{cid}").json()["version"] == 1
    assert client.get(f"/api/canvases/{rid}").status_code == 200
