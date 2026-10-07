from __future__ import annotations

import os
from urllib.parse import urlencode
from pathlib import Path

import pytest
from fastapi.routing import APIRoute

from gw.api.app import app


LEGACY_PAGE_TARGETS = {
    "asset-manager": ("/static/pages/assets.html", ("project_id", "pipeline_id", "asset_id")),
    "api-settings": ("/static/pages/settings.html", ("section", "project_id")),
    "canvas-list": ("/static/pages/storyboard.html", ("project_id", "entity_id", "canvas_id", "view", "filter", "layout")),
    "episode-pipeline": ("/static/pages/workshop.html", ("project_id", "pipeline_id", "step", "agent")),
    "task-center": ("/static/pages/collab.html", ("view", "project_id")),
    "governance": ("/static/pages/projects.html", ("openTrash", "project_id")),
}


@pytest.mark.parametrize("legacy_name,target_and_keys", LEGACY_PAGE_TARGETS.items())
def test_root_legacy_pages_redirect_to_pages_with_exact_query_whitelist(client, legacy_name: str, target_and_keys):
    """根级旧页必须以307跳到确定的新页，且严格过滤查询参数。"""
    target, allowed_keys = target_and_keys
    values = {key: f"{key} value/&" for key in allowed_keys}
    values["ignored"] = "drop"
    query = urlencode(values)
    response = client.get(
        f"/static/{legacy_name}.html?{query}",
        follow_redirects=False,
    )
    expected_query = urlencode([(key, values[key]) for key in allowed_keys])
    expected_location = f"{target}?{expected_query}" if expected_query else target
    assert response.status_code == 307
    assert response.headers["location"] == expected_location


@pytest.mark.parametrize("legacy_name", LEGACY_PAGE_TARGETS)
def test_root_legacy_pages_stay_direct_when_embedded(client, legacy_name: str):
    """embedded=1 是 iframe 边界，不能被兼容重定向打断。"""
    response = client.get(f"/static/{legacy_name}.html?embedded=1", follow_redirects=False)
    assert response.status_code == 200
    assert response.headers.get("content-type", "").startswith("text/html")
    assert "<html" in response.text.lower()


def test_asset_share_page_and_public_share_shell_are_direct(client):
    """公开分享页和分享深链都直接返回页面外壳。"""
    direct = client.get("/static/asset-share.html", follow_redirects=False)
    deep_link = client.get("/share/share-token-example", follow_redirects=False)
    assert direct.status_code == 200
    assert deep_link.status_code == 200
    assert "<html" in direct.text.lower()
    assert "<html" in deep_link.text.lower()
    assert deep_link.headers["referrer-policy"] == "no-referrer"
    assert deep_link.headers["x-content-type-options"] == "nosniff"


def test_canvas_trash_route_is_registered_before_canvas_id_route():
    """固定回收站路径必须先于动态 canvas_id 路径注册。"""
    def flattened(routes):
        for route in routes:
            if type(route).__name__ == "_IncludedRouter":
                yield from flattened(route.original_router.routes)
            else:
                yield route

    routes = [route for route in flattened(app.routes) if isinstance(route, APIRoute)]
    trash_index = next(i for i, route in enumerate(routes) if route.path == "/api/canvases/trash")
    dynamic_index = next(i for i, route in enumerate(routes) if route.path == "/api/canvases/{canvas_id}")
    assert trash_index < dynamic_index


def test_pytest_data_roots_are_scoped_to_tmp_path(tmp_path: Path):
    """pytest 三个数据根都必须落在当前用例的临时目录内。"""
    for name in ("GW_DATA_DIR", "GW_LOCAL_AUTH_DB", "GW_VIDEO_DATA_DIR"):
        value = os.environ.get(name)
        assert value, f"缺少测试环境变量：{name}"
        resolved = Path(value).resolve()
        assert resolved == tmp_path.resolve() or tmp_path.resolve() in resolved.parents
