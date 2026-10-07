"""god-canvas 统一服务层（切片 B+C：普通画布 + 智能画布）契约测试。"""

import json
import pytest
from fastapi.testclient import TestClient

from gw.core.errors import (
    CanvasVersionConflictException,
    CleanroomException,
    ForbiddenException,
    UnauthorizedException,
)
from gw.god_canvas.godmap import parse_godmap_content
from gw.god_canvas.models import (
    CanvasCreateRequest,
    CanvasMode,
    CanvasNode,
    CanvasTopologyUpdateRequest,
    NodePosition,
)
from gw.god_canvas.service import GodCanvasService
from gw.god_canvas.tasks import SmartCanvasRunMode, SmartCanvasTaskRequest


def test_god_canvas_classic_crud_and_cas():
    """测试 god-canvas 普通拓扑生命周期与 CAS 409 并发冲突。"""
    service = GodCanvasService(seed_golden_fixture=True)

    # 1. 验证初始种子画布 cv-0001
    canvases = service.list_canvases("prj-0001")
    assert len(canvases) == 1
    assert canvases[0].canvas_id == "cv-0001"
    assert canvases[0].version == 1

    top = service.get_topology("cv-0001")
    assert len(top.nodes) == 2
    assert len(top.connections) == 1
    assert top.nodes[0].entity_id == "nd-0001"
    assert top.nodes[1].entity_id == "nd-0002"

    # 2. 创建新画布
    res = service.create_canvas(
        CanvasCreateRequest(
            project_id="prj-0001",
            title="测试画布 2",
            mode=CanvasMode.CLASSIC,
        )
    )
    new_cid = res.canvas_id
    assert res.version == 1

    # 3. 正常更新拓扑（CAS 匹配）
    update_res = service.update_topology(
        new_cid,
        CanvasTopologyUpdateRequest(
            expected_version=1,
            nodes=[
                CanvasNode(entity_id="node-x", kind="process", position=NodePosition(x=10, y=20)),
            ],
            connections=[],
        ),
    )
    assert update_res.version == 2
    new_top = service.get_topology(new_cid)
    assert len(new_top.nodes) == 1
    assert new_top.nodes[0].entity_id == "node-x"

    # 4. CAS 冲突更新（版本过期）
    with pytest.raises(CanvasVersionConflictException) as excinfo:
        service.update_topology(
            new_cid,
            CanvasTopologyUpdateRequest(
                expected_version=1,  # 真实已为 2
                nodes=[],
                connections=[],
            ),
        )
    assert excinfo.value.status_code == 409
    assert excinfo.value.code == "CANVAS_VERSION_CONFLICT"
    assert excinfo.value.extra["expected_version"] == 1
    assert excinfo.value.extra["current_version"] == 2
    assert excinfo.value.extra["canvas_id"] == new_cid


def test_god_canvas_godmap_and_json_import_export():
    """测试工作流导入导出与拓扑校验。"""
    service = GodCanvasService(seed_golden_fixture=True)

    # 导出 godmap
    godmap_str = service.export_workflow("cv-0001", export_format="godmap")
    doc = parse_godmap_content(godmap_str)
    assert doc.format == "godmap"
    assert len(doc.payload.nodes) == 2

    # 导出 json
    json_str = service.export_workflow("cv-0001", export_format="json")
    json_data = json.loads(json_str)
    assert json_data["canvas_id"] == "cv-0001"
    assert len(json_data["nodes"]) == 2

    # 画布级引用在两种导出格式中都必须保留；资源内嵌尚未进入洁净最小切片
    service.update_topology(
        "cv-0001",
        CanvasTopologyUpdateRequest(
            expected_version=1,
            nodes=service.get_topology("cv-0001").nodes,
            connections=service.get_topology("cv-0001").connections,
            references={"asset_id": "asset-0001"},
        ),
    )
    referenced_godmap = parse_godmap_content(service.export_workflow("cv-0001", export_format="godmap"))
    assert referenced_godmap.payload.references == {"asset_id": "asset-0001"}
    with pytest.raises(CleanroomException) as resource_error:
        service.export_workflow("cv-0001", export_format="json", include_resources=True)
    assert resource_error.value.code == "UNSUPPORTED_OPTION"

    # 导入非法数据测试
    with pytest.raises(CleanroomException) as excinfo:
        service.import_workflow("cv-0001", content="not valid json", expected_version=2, file_format="json")
    assert excinfo.value.status_code == 400

    bad_godmap = json.dumps(
        {
            "format": "godmap",
            "version": "1.0",
            "payload": {
                "canvas_id": "cv-0001",
                "nodes": [{"entity_id": "nd-only", "kind": "input"}],
                "connections": [{"connection_id": "ln-bad", "from": "nd-only", "to": "nd-missing"}],
            },
        }
    )
    with pytest.raises(CleanroomException) as godmap_error:
        service.import_workflow("cv-0001", content=bad_godmap, expected_version=2, file_format="godmap")
    assert godmap_error.value.code == "INVALID_TOPOLOGY"


def test_god_canvas_smart_tasks_and_security_boundaries():
    """测试智能画布异步任务受理（202）、轮询、401 与 403 权限边界。"""
    service = GodCanvasService(seed_golden_fixture=True)

    # 1. 正常发起智能任务 -> 202 Accepted
    task_req = SmartCanvasTaskRequest(
        expected_version=1,
        entry_nodes=["nd-0001"],
        run_mode=SmartCanvasRunMode.SINGLE,
        inputs={"prompt": "生成镜头 A"},
    )
    task_resp = service.submit_smart_task("cv-0001", task_req, authorization="Bearer cleanroom-test", user_role="editor")
    assert task_resp.state == "accepted"
    assert task_resp.job_id.startswith("job-")
    assert task_resp.job_id != "job-0001"
    assert service.get_job("job-0001").state == "accepted"
    assert f"/api/jobs/{task_resp.job_id}" == task_resp.poll_hint

    # 2. 查询任务状态
    job = service.get_job(task_resp.job_id)
    assert job.job_id == task_resp.job_id
    assert job.state == "accepted"

    # 3. 鉴权失效 401 拦截
    with pytest.raises(UnauthorizedException) as exc_401:
        service.submit_smart_task("cv-0001", task_req, authorization="Bearer invalid", user_role="editor")
    assert exc_401.value.status_code == 401
    assert exc_401.value.code == "UNAUTHORIZED"

    # 4. 权限不足 403 拦截（只读降级）
    with pytest.raises(ForbiddenException) as exc_403:
        service.submit_smart_task("cv-0001", task_req, authorization="Bearer cleanroom-test", user_role="readonly")
    assert exc_403.value.status_code == 403
    assert exc_403.value.code == "FORBIDDEN"

    # 5. CAS 冲突 409 拦截
    conflict_req = SmartCanvasTaskRequest(
        expected_version=999,
        entry_nodes=["nd-0001"],
    )
    with pytest.raises(CanvasVersionConflictException) as exc_409:
        service.submit_smart_task("cv-0001", conflict_req, authorization="Bearer cleanroom-test", user_role="editor")
    assert exc_409.value.status_code == 409
    assert exc_409.value.code == "CANVAS_VERSION_CONFLICT"


def test_api_god_canvas_routes_integration(client: TestClient):
    """测试 API 层 god-canvas 端点与智能任务查询。"""
    # 1. 查询列表
    resp_list = client.get("/api/canvases?project_id=prj-0001")
    assert resp_list.status_code == 200
    assert len(resp_list.json()["canvases"]) >= 1

    # 2. 读取拓扑
    resp_top = client.get("/api/canvases/cv-0001")
    assert resp_top.status_code == 200
    assert resp_top.json()["canvas_id"] == "cv-0001"
    assert len(resp_top.json()["nodes"]) == 2

    # 3. 冲突更新 (409)
    resp_conflict = client.patch(
        "/api/canvases/cv-0001",
        json={"expected_version": 999, "nodes": [], "connections": []},
        headers={"Authorization": "Bearer cleanroom-test"},
    )
    assert resp_conflict.status_code == 409
    detail = resp_conflict.json()["detail"]
    assert detail["code"] == "CANVAS_VERSION_CONFLICT"
    assert detail["canvas_id"] == "cv-0001"

    # 4. 智能任务发起 (202 Accepted)
    resp_task = client.post(
        "/api/canvases/cv-0001/tasks",
        json={"entry_nodes": ["nd-0001"], "run_mode": "single", "inputs": {}},
        headers={"Authorization": "Bearer cleanroom-test", "X-User-Role": "editor"},
    )
    assert resp_task.status_code == 202
    task_data = resp_task.json()
    assert task_data["state"] == "accepted"
    job_id = task_data["job_id"]

    # 5. 查询任务状态
    resp_job = client.get(f"/api/jobs/{job_id}")
    assert resp_job.status_code == 200
    assert resp_job.json()["job_id"] == job_id
    assert resp_job.json()["state"] == "accepted"

    # 6. 未认证发起智能任务 (401)
    resp_unauth = client.post(
        "/api/canvases/cv-0001/tasks",
        json={"entry_nodes": ["nd-0001"]},
        headers={"authorization": "invalid"},
    )
    assert resp_unauth.status_code == 401
    assert resp_unauth.json()["detail"]["code"] == "UNAUTHORIZED"

    # 7. 权限不足发起智能任务 (403)
    resp_forbid = client.post(
        "/api/canvases/cv-0001/tasks",
        json={"entry_nodes": ["nd-0001"]},
        headers={"Authorization": "Bearer cleanroom-test", "X-User-Role": "readonly"},
    )
    assert resp_forbid.status_code == 403
    assert resp_forbid.json()["detail"]["code"] == "FORBIDDEN"
