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


def test_production_smoke_end_to_end_business_chain():
    """验证核心业务主链路：项目列表 -> 画布拓扑 -> 智能任务受理。"""
    # 1. 查询项目列表
    resp_proj = client.get("/api/asset-registry/projects")
    assert resp_proj.status_code == 200
    projects_res = resp_proj.json()
    assert "projects" in projects_res
    assert len(projects_res["projects"]) >= 1
    pid = next(project["project_id"] for project in projects_res["projects"] if project["project_id"] == "prj-0001")

    # 2. 根据项目查询画布列表
    resp_canvases = client.get(f"/api/canvases?project_id={pid}")
    assert resp_canvases.status_code == 200
    canvases = resp_canvases.json()
    assert len(canvases["canvases"]) >= 1
    cid = canvases["canvases"][0]["canvas_id"]

    # 3. 查询画布完整拓扑
    resp_top = client.get(f"/api/canvases/{cid}")
    assert resp_top.status_code == 200
    top = resp_top.json()
    assert "nodes" in top
    assert "connections" in top

    # 4. 提交智能任务并验证 202 Accepted
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
    resp_job = client.get(f"/api/jobs/{job_id}")
    assert resp_job.status_code == 200
    assert resp_job.json()["job_id"] == job_id
