"""本轮洁净修复新增的来源、认证和拓扑边界回归测试。"""

import json

import pytest
from fastapi.testclient import TestClient

from gw.core.errors import CleanroomException
from gw.god_canvas.models import CanvasCreateRequest
from gw.god_canvas.service import GodCanvasService
from gw.god_canvas.tasks import SmartCanvasTaskRequest
from gw.projects_hub.models import ProjectCreateRequest, ProjectType
from gw.projects_hub.service import ProjectsService


AUTH = {"Authorization": "Bearer cleanroom-test"}
EDITOR = {**AUTH, "X-User-Role": "editor"}
GOVERNOR = {**AUTH, "X-User-Role": "governor"}
READONLY = {**AUTH, "X-User-Role": "readonly"}


def test_write_routes_require_session_and_role(client: TestClient):
    """验证写操作不会在缺少会话或权限不足时静默成功。"""
    payload = {"name": "认证边界测试项目", "project_type": "other"}

    missing = client.post("/api/asset-registry/projects", json=payload)
    assert missing.status_code == 401
    assert missing.json()["detail"]["code"] == "UNAUTHORIZED"

    readonly = client.post("/api/asset-registry/projects", json=payload, headers=READONLY)
    assert readonly.status_code == 403
    assert readonly.json()["detail"]["code"] == "FORBIDDEN"

    created = client.post("/api/asset-registry/projects", json=payload, headers=EDITOR)
    assert created.status_code == 201
    project_id = created.json()["project"]["project_id"]
    version = created.json()["project"]["version"]

    archive_without_session = client.request(
        "DELETE",
        f"/api/asset-registry/projects/{project_id}",
        json={"expected_version": version},
    )
    assert archive_without_session.status_code == 401

    archive_without_governance = client.request(
        "DELETE",
        f"/api/asset-registry/projects/{project_id}",
        json={"expected_version": version},
        headers=EDITOR,
    )
    assert archive_without_governance.status_code == 403

    archived = client.request(
        "DELETE",
        f"/api/asset-registry/projects/{project_id}",
        json={"expected_version": version},
        headers=GOVERNOR,
    )
    assert archived.status_code == 200
    assert archived.json()["project"]["archived_at"]

    canvas_payload = {"project_id": "prj-0001", "title": "认证边界测试画布", "mode": "classic"}
    canvas_missing = client.post("/api/canvases", json=canvas_payload)
    assert canvas_missing.status_code == 401
    canvas_readonly = client.post("/api/canvases", json=canvas_payload, headers=READONLY)
    assert canvas_readonly.status_code == 403


def test_invalid_topology_uses_standard_error_envelope(client: TestClient):
    """验证悬挂连线被拒绝且返回统一 400 错误包。"""
    response = client.patch(
        "/api/canvases/cv-0001",
        json={
            "expected_version": 1,
            "nodes": [{"entity_id": "nd-only", "kind": "input"}],
            "connections": [
                {"connection_id": "ln-invalid", "from": "nd-only", "to": "nd-missing"}
            ],
        },
        headers=EDITOR,
    )
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "INVALID_REQUEST"


def test_insert_import_rekeys_colliding_stable_ids():
    """验证 insert 导入不会覆盖现有稳定节点和连线 ID。"""
    service = GodCanvasService(seed_golden_fixture=True)
    content = json.dumps(
        {
            "canvas_id": "cv-0001",
            "version": 1,
            "nodes": [
                {"entity_id": "nd-0001", "kind": "input", "position": {"x": 10, "y": 10}},
                {"entity_id": "nd-0002", "kind": "output", "position": {"x": 200, "y": 10}},
            ],
            "connections": [{"connection_id": "ln-0001", "from": "nd-0001", "to": "nd-0002"}],
        }
    )
    result = service.import_workflow("cv-0001", content, merge_mode="insert", expected_version=1)
    assert result.canvas.version == 2
    topology = service.get_topology("cv-0001")
    entity_ids = [node.entity_id for node in topology.nodes]
    connection_ids = [connection.connection_id for connection in topology.connections]
    assert len(entity_ids) == len(set(entity_ids)) == 4
    assert len(connection_ids) == len(set(connection_ids)) == 2
    assert any(item.endswith("-import-1") for item in entity_ids)


def test_smart_task_requires_existing_entry_and_legal_transition():
    """验证智能任务入口节点和状态机边界。"""
    service = GodCanvasService(seed_golden_fixture=True)
    request = SmartCanvasTaskRequest(entry_nodes=["nd-0001"])
    with pytest.raises(CleanroomException) as missing:
        service.submit_smart_task(
            "cv-0001",
            SmartCanvasTaskRequest(entry_nodes=["nd-missing"]),
            authorization="Bearer cleanroom-test",
        )
    assert missing.value.code == "TASK_PRECONDITION_FAILED"
    task = service.submit_smart_task("cv-0001", request, authorization="Bearer cleanroom-test")
    with pytest.raises(CleanroomException) as illegal:
        service.update_job_state(task.job_id, "completed")
    assert illegal.value.code == "ILLEGAL_STATE_TRANSITION"


def test_empty_services_allocate_first_stable_ids_from_one():
    """验证没有黄金夹具种子时，稳定 ID 仍从 0001 开始。"""
    projects = ProjectsService(seed_golden_fixture=False)
    project = projects.create_project(ProjectCreateRequest(name="空服务项目", project_type=ProjectType.OTHER))
    assert project.project_id == "prj-0001"

    canvases = GodCanvasService(seed_golden_fixture=False)
    canvas = canvases.create_canvas(CanvasCreateRequest(project_id="prj-0001", title="空服务画布"))
    assert canvas.canvas_id == "cv-0001"
