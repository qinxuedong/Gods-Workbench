"""画布契约的项目真源夹具；不把无归属黄金任务公开到HTTP。"""

import pytest

from gw.api import routes_god_canvas, routes_projects
from gw.core.auth import require_authenticated
from gw.god_canvas.service import GodCanvasService
from gw.projects_hub import service as projects
from gw.projects_hub.models import ProjectCreateRequest


@pytest.fixture(autouse=True)
def owned_canvas_project(request, isolated_data_dir, monkeypatch):
    if "c5" in request.node.name or "c5" in str(request.node.fspath):
        return
    context = require_authenticated("Bearer cleanroom-test", "editor")
    source = projects.ProjectsService(seed_golden_fixture=False)
    project = source.create_project(
        ProjectCreateRequest(name="画布契约真实归属项目", project_type="other"),
        owner_key=projects.owner_key_for_context(context),
    )
    assert project.project_id == "prj-0001"
    monkeypatch.setattr(projects, "default_projects_service", source)
    monkeypatch.setattr(routes_projects, "default_projects_service", source)
    monkeypatch.setattr(
        routes_god_canvas,
        "default_god_canvas_service",
        GodCanvasService(seed_golden_fixture=True),
    )
