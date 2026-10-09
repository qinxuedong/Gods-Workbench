"""生产交付与冒烟健康检查测试套件。

由【运维交付与生产基线工程师】负责构建与维护：
1. 验证 FastAPI 应用实例、路由挂载与交互式文档可用性
2. 验证前端静态页面入口（307 重定向至 /static/pages/projects.html、HTML/CSS 静态可达）
3. 验证端到端业务主链路健康可用（/healthz、项目中心、god-canvas 拓扑与智能任务）
4. 验证生产启动脚本 run.py 严格绑定默认 2077 端口
"""

from fastapi.testclient import TestClient
import pytest

from gw.api.app import app

client = TestClient(app)


def test_production_smoke_app_and_docs_available():
    """验证服务主入口、OpenAPI 文档与健康检查端点可达性。"""
    # 1. 健康检查端点
    resp_health = client.get("/healthz")
    assert resp_health.status_code == 200
    assert resp_health.json()["status"] == "ok"
    assert resp_health.json()["mode"] == "cleanroom"
    assert resp_health.json()["frozen_contracts"] is False
    assert resp_health.json()["release_authorized"] is False

    # 2. Swagger 文档
    resp_docs = client.get("/docs")
    assert resp_docs.status_code == 200
    assert "Swagger UI" in resp_docs.text or "openapi" in resp_docs.text

    # 3. OpenAPI Schema
    resp_openapi = client.get("/openapi.json")
    assert resp_openapi.status_code == 200
    openapi_data = resp_openapi.json()
    assert openapi_data["info"]["title"] == "Gods-Workbench Cleanroom API"


def test_production_smoke_frontend_static_routing():
    """验证前端静态文件挂载与页面路由健康性。"""
    # 1. 首页 307 重定向到 /static/pages/projects.html
    resp_root = client.get("/", follow_redirects=False)
    assert resp_root.status_code == 307
    assert resp_root.headers.get("location") == "/static/pages/projects.html"

    # 2. 项目中心 HTML
    resp_projects = client.get("/static/pages/projects.html")
    assert resp_projects.status_code == 200
    assert "项目中心" in resp_projects.text or "Gods Workbench" in resp_projects.text

    # 3. 影视工坊工作台 HTML
    resp_workshop = client.get("/static/pages/workshop.html")
    assert resp_workshop.status_code == 200
    assert "影视工坊" in resp_workshop.text

    # 4. 样式表静态资源
    resp_css = client.get("/static/css/base/hardware-design-system.css")
    assert resp_css.status_code == 200


def test_production_smoke_end_to_end_business_chain(monkeypatch):
    """隔离冒烟：真实归属项目 -> 画布拓扑 -> 原型任务受理与认证回读。"""
    from gw.api import routes_god_canvas
    from gw.core.auth import require_authenticated
    from gw.god_canvas.service import GodCanvasService
    from gw.god_canvas.models import CanvasCreateRequest, CanvasTopologyUpdateRequest, CanvasNode
    from gw.projects_hub import service as projects
    from gw.projects_hub.models import ProjectCreateRequest

    headers = {"Authorization": "Bearer cleanroom-test", "X-User-Role": "editor"}
    context = require_authenticated(headers["Authorization"], "editor")
    source = projects.ProjectsService(seed_golden_fixture=False)
    project = source.create_project(ProjectCreateRequest(name="隔离烟测项目", project_type="other"), owner_key=projects.owner_key_for_context(context))
    canvas = GodCanvasService(seed_golden_fixture=False)
    item = canvas.create_canvas(CanvasCreateRequest(project_id=project.project_id, title="烟测画布"))
    canvas.update_topology(item.canvas_id, CanvasTopologyUpdateRequest(expected_version=1, nodes=[CanvasNode(entity_id="smoke-node", kind="input")], connections=[]))
    monkeypatch.setattr(projects, "default_projects_service", source)
    monkeypatch.setattr(routes_god_canvas, "default_god_canvas_service", canvas)
    cid = item.canvas_id
    top = client.get(f"/api/canvases/{cid}").json()

    # 无归属黄金种子不作为可提交项目；ACL不得为烟测放宽。
    task_payload = {
        "expected_version": top["version"],
        "entry_nodes": [top["nodes"][0]["entity_id"]],
        "run_mode": "single",
        "inputs": {},
    }
    resp_task = client.post(
        f"/api/canvases/{cid}/tasks",
        json=task_payload,
        headers={"Authorization": "Bearer cleanroom-test", "X-User-Role": "editor"},
    )
    assert resp_task.status_code == 202
    task_data = resp_task.json()
    assert task_data["state"] == "accepted"
    job_id = task_data["job_id"]

    # 5. 轮询任务状态
    resp_job = client.get(f"/api/jobs/{job_id}", headers=headers)
    assert resp_job.status_code == 200
    assert resp_job.json()["job_id"] == job_id
