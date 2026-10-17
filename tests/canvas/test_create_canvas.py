"""QIN-197 独立画布在持久本地账户模式下的创建回归。"""

from fastapi.testclient import TestClient


def test_local_account_can_create_default_canvas(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("GW_RUNTIME_MODE", "test")
    monkeypatch.setenv("GW_AUTH_MODE", "local_account")
    monkeypatch.setenv("GW_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("GW_LOCAL_AUTH_DB", str(tmp_path / "auth.sqlite3"))
    monkeypatch.setenv("GW_VIDEO_DATA_DIR", str(tmp_path / "video"))

    from gw.api import routes_asset_registry, routes_god_canvas, routes_projects
    from gw.api.app import create_app
    from gw.god_canvas import service as canvas_service_module
    from gw.god_canvas.service import GodCanvasService
    from gw.projects_hub import service as projects_service_module
    from gw.projects_hub.service import ProjectsService

    projects = ProjectsService(seed_golden_fixture=False, persistent=True)
    canvas = GodCanvasService(seed_golden_fixture=False, persistent=True)
    monkeypatch.setattr(projects_service_module, "default_projects_service", projects)
    monkeypatch.setattr(routes_projects, "default_projects_service", projects)
    monkeypatch.setattr(routes_asset_registry, "default_projects_service", projects)
    monkeypatch.setattr(canvas_service_module, "default_god_canvas_service", canvas)
    monkeypatch.setattr(canvas_service_module, "default_canvas_service", canvas)
    monkeypatch.setattr(routes_god_canvas, "default_god_canvas_service", canvas)

    with TestClient(
        create_app(),
        base_url="http://localhost",
        client=("127.0.0.1", 50000),
        headers={"Origin": "http://localhost"},
    ) as client:
        setup = client.post(
            "/api/asset-auth/local/setup",
            json={"username": "qin197_owner", "password": "Qin197Test2026"},
        )
        assert setup.status_code == 201, setup.text

        response = client.post(
            "/api/canvases",
            json={"project_id": "default", "title": "独立画布回归", "mode": "smart"},
        )

    assert response.status_code == 201, response.text
    assert response.json()["canvas"]["canvas_id"].startswith("cv-")
