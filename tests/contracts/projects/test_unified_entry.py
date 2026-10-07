"""Workbench 统一入口与根级兼容入口契约测试。"""

import json
import re
from pathlib import Path
from urllib.parse import urlencode, urlsplit

import pytest
from playwright_support import open_playwright

from fastapi.testclient import TestClient

from gw.api.app import app


ROOT = Path(__file__).resolve().parents[3]
STATIC = ROOT / "web"


LEGACY_REDIRECTS = {
    "/static/asset-manager.html": ("/static/pages/assets.html", ("project_id", "pipeline_id", "asset_id")),
    "/static/api-settings.html": ("/static/pages/settings.html", ("section", "project_id")),
    "/static/canvas-list.html": ("/static/pages/storyboard.html", ("project_id", "entity_id", "canvas_id", "view", "filter", "layout")),
    "/static/episode-pipeline.html": ("/static/pages/workshop.html", ("project_id", "pipeline_id", "step", "agent")),
    "/static/task-center.html": ("/static/pages/collab.html", ("view", "project_id")),
    "/static/governance.html": ("/static/pages/projects.html", ("openTrash", "project_id")),
}


_CONTEXT = {
    "project_id": "prj-test",
    "pipeline_id": "ep-test",
    "asset_id": "asset-test",
    "section": "api-settings",
    "entity_id": "entity-test",
    "canvas_id": "canvas-test",
    "filter": "all",
    "layout": "overview",
    "step": "script",
    "agent": "script-03",
    "view": "tasks",
    "openTrash": "1",
    "ignored": "drop",
}


def test_legacy_user_entries_redirect_to_pages_and_preserve_context():
    """根级业务入口必须重定向到新页面，并保留各自白名单上下文参数。"""
    with TestClient(app) as client:
        for source, (target, keys) in LEGACY_REDIRECTS.items():
            context = dict(_CONTEXT)
            if source.endswith("canvas-list.html"):
                context["view"] = "canvas"
            response = client.get(
                source + "?" + "&".join(f"{key}={value}" for key, value in context.items()),
                follow_redirects=False,
            )
            assert response.status_code == 307
            location = response.headers["location"]
            expected_query = urlencode([(key, context[key]) for key in keys])
            expected_location = f"{target}?{expected_query}" if expected_query else target
            assert location == expected_location


def test_embedded_legacy_slices_remain_available_to_workbench():
    """Workbench 内部 iframe 使用 embedded=1 时必须继续拿到原始切片。"""
    with TestClient(app) as client:
        for source in LEGACY_REDIRECTS:
            response = client.get(f"{source}?embedded=1", follow_redirects=False)
            assert response.status_code == 200
            assert "<html" in response.text.lower()
            assert response.headers.get("location") is None


def test_public_asset_share_deep_link_remains_anonymous_boundary():
    """公共分享页是匿名深链例外，不得被统一入口重定向。"""
    with TestClient(app) as client:
        response = client.get("/static/asset-share.html?token=share-test", follow_redirects=False)
    assert response.status_code == 200
    assert response.headers.get("location") is None
    assert "/static/v2/" not in response.text


def test_workbench_user_surface_has_no_legacy_external_open_links():
    """Workbench 用户界面不得再通过新窗口或普通导航打开根级业务页面。"""
    files = [
        *sorted((STATIC / "pages").glob("*.html")),
        STATIC / "js" / "controllers" / "assets-controller.js",
        STATIC / "js" / "controllers" / "home-controller.js",
        STATIC / "js" / "controllers" / "agents-controller.js",
        STATIC / "js" / "controllers" / "collab-controller.js",
        STATIC / "js" / "modules" / "asset-manager.js",
        STATIC / "js" / "modules" / "episode-pipeline.js",
        STATIC / "js" / "core" / "hardware-telemetry.js",
    ]
    legacy = r"/static/(?:asset-manager|api-settings|canvas-list|episode-pipeline|task-center|governance)\.html"
    opening = re.compile(r"(?:href|src|location\\.href|window\\.open|dataset\\.src|targetUrl|new URL)\s*[^\n]*" + legacy)
    for path in files:
        text = path.read_text(encoding="utf-8")
        for line in text.splitlines():
            if 'target="_blank"' in line:
                assert not re.search(legacy, line), f"仍有独立业务入口: {path}: {line}"
            if re.search(legacy, line) and opening.search(line):
                assert "embedded=1" in line or "embeddedCanvasRoute" in line, f"普通用户入口仍直达 legacy 页面: {path}: {line}"


def test_legacy_asset_manager_fallback_merges_query_parameters_safely():
    """资产管理器回退 iframe 必须保留 embedded=1，且不能拼接出双问号。"""
    text = (STATIC / "js" / "modules" / "asset-manager.js").read_text(encoding="utf-8")
    assert "new URL(source, window.location.origin)" in text
    assert "targetUrl.searchParams.set('project_id'" in text
    assert "targetUrl.searchParams.set('pipeline_id'" in text
    assert "`${source}${query ? `?${query}` : ''}`" not in text


def test_workbench_composite_pages_mark_internal_iframe_boundaries():
    """资产、设置、画布和工坊的根级切片只允许作为内部 iframe 使用。"""
    checks = {
        "assets.html": "asset-manager.html?embedded=1",
        "settings.html": "api-settings.html?embedded=1",
        "storyboard.html": "embeddedCanvasRoute('/static/canvas-list.html",
        "workshop.html": "episode-pipeline.html?embedded=1",
    }
    for name, marker in checks.items():
        text = (STATIC / "pages" / name).read_text(encoding="utf-8")
        assert marker in text, f"缺少 Workbench 内部 iframe 边界: {name}"


def test_workbench_copy_uses_single_entry_language():
    """用户可见文案不得再暗示需要独立窗口或外部完整页。"""
    for name in ("index.html", "settings.html", "collab.html"):
        text = (STATIC / "pages" / name).read_text(encoding="utf-8")
        assert "独立窗口" not in text
        assert "完整视图 ↗" not in text

# 此表仅保留已登记的旧 URL 精确兼容，不构成新路径约定。
LEGACY_STATIC_REDIRECTS = {
    "/static/v2/agents.html": "/static/pages/agents.html",
    "/static/v2/assets.html": "/static/pages/assets.html",
    "/static/v2/collab.html": "/static/pages/collab.html",
    "/static/v2/index.html": "/static/pages/index.html",
    "/static/v2/production.html": "/static/pages/production.html",
    "/static/v2/projects.html": "/static/pages/projects.html",
    "/static/v2/settings.html": "/static/pages/settings.html",
    "/static/v2/storyboard.html": "/static/pages/storyboard.html",
    "/static/v2/workshop.html": "/static/pages/workshop.html",
    "/static/v2/css/project-date-range.css": "/static/css/pages/project-date-range.css",
    "/static/v2/js/agents-controller.js": "/static/js/controllers/agents-controller.js",
    "/static/v2/js/assets-controller.js": "/static/js/controllers/assets-controller.js",
    "/static/v2/js/collab-controller.js": "/static/js/controllers/collab-controller.js",
    "/static/v2/js/home-controller.js": "/static/js/controllers/home-controller.js",
    "/static/v2/js/production-controller.js": "/static/js/controllers/production-controller.js",
    "/static/v2/js/projects-controller.js": "/static/js/controllers/projects-controller.js",
    "/static/v2/js/storyboard-controller.js": "/static/js/controllers/storyboard-controller.js",
    "/static/v2/js/project-date-range.js": "/static/js/modules/project-date-range.js",
    "/static/v2/js/v2-shell.js": "/static/js/core/workbench-shell.js",
    "/static/js/v2-task-queue.js": "/static/js/core/task-queue.js",
}


def _serve_local_app(client, route):
    parsed = urlsplit(route.request.url)
    if parsed.hostname != "testserver":
        route.abort()
        return
    request_path = parsed.path + ("?" + parsed.query if parsed.query else "")
    response = client.request(
        route.request.method,
        request_path,
        content=route.request.post_data,
    )
    route.fulfill(
        status=response.status_code,
        body=response.content,
        headers={"content-type": response.headers.get("content-type", "text/plain")},
    )


def _launch_local_chrome(browser_api, playwright):
    try:
        return playwright.chromium.launch(channel="chrome", headless=True)
    except browser_api.Error as exc:
        pytest.skip(f"Chrome 不可启动：{exc}")


@pytest.mark.parametrize(
    ("legacy_path", "new_path"),
    LEGACY_STATIC_REDIRECTS.items(),
    ids=[path.removeprefix("/static/") for path in LEGACY_STATIC_REDIRECTS],
)
def test_static_v2_compat_redirects_are_temporary_until_old_clients_are_retired(legacy_path, new_path):
    """临时旧静态 URL 必须逐条跳到新资源；仅在旧客户端与受支持外链完成清退并经人工批准后删除本测试及兼容路由。"""
    with TestClient(app) as client:
        response = client.get(legacy_path + "?keep=one", follow_redirects=False)
        assert response.status_code == 307
        assert response.headers["location"] == new_path + "?keep=one"
        assert client.get(new_path).status_code == 200


def test_unlisted_static_v2_path_does_not_become_a_wildcard_compat_route():
    """旧前缀只准逐条映射；未知地址不得被兜底重定向。"""
    with TestClient(app) as client:
        response = client.get("/static/v2/not-registered.js", follow_redirects=False)
    assert response.status_code == 404
    assert response.headers.get("location") is None


def test_workbench_shell_intercepts_static_pages_navigation_without_document_reload():
    """点击新 /static/pages/ 站内路由后应由 workbench-shell 无刷新加载，保留当前文档。"""
    browser_api = pytest.importorskip("playwright.sync_api")
    with TestClient(app) as client, open_playwright(browser_api) as playwright:
        browser = _launch_local_chrome(browser_api, playwright)
        try:
            page = browser.new_page()
            page.route("**/*", lambda route: _serve_local_app(client, route))
            document_requests = []
            page.on(
                "request",
                lambda request: document_requests.append(request.url)
                if request.resource_type == "document" and request.frame == page.main_frame
                else None,
            )
            page.add_init_script(
                "window.__gw_document_token = Math.random().toString(36);"
                "window.__gw_route_events = [];"
                "window.addEventListener('gw:route-loaded', event => window.__gw_route_events.push(event.detail.href));"
            )
            page.goto("http://testserver/static/pages/index.html")
            page.locator('a.nav-pill-btn[href^="projects.html"]').wait_for()
            original_document_token = page.evaluate("window.__gw_document_token")

            page.locator('a.nav-pill-btn[href^="projects.html"]').click()
            page.wait_for_function(
                "location.pathname.endsWith('/projects.html') && "
                "document.querySelector('#projectsGridViewContainer')"
            )

            assert page.evaluate("window.__gw_document_token") == original_document_token
            assert page.evaluate("window.__gw_route_events.length") == 1
            assert len(document_requests) == 1, "站内切页不应再发起 document 导航请求"
        finally:
            browser.close()


def test_view_mode_local_storage_migration_preserves_legacy_and_unrelated_keys():
    """两个视图键迁移都要读旧值、写新键、保留旧键，并且不改动其他存储键。"""
    browser_api = pytest.importorskip("playwright.sync_api")
    cases = (
        (
            "/static/pages/index.html",
            "v2_proj_view_mode",
            "workbench_proj_view_mode",
            "list",
            "WorkbenchHome",
        ),
        (
            "/static/pages/projects.html",
            "v2_projects_view_mode",
            "workbench_projects_view_mode",
            "table",
            "WorkbenchProjects",
        ),
    )
    with TestClient(app) as client, open_playwright(browser_api) as playwright:
        browser = _launch_local_chrome(browser_api, playwright)
        try:
            for page_path, old_key, new_key, value, controller in cases:
                context = browser.new_context()
                try:
                    seed = {
                        old_key: value,
                        "studio_theme": "dark",
                        "t3_unrelated_sentinel": "keep-exactly",
                    }
                    seed_script = (
                        "for (const [key, value] of Object.entries("
                        + json.dumps(seed)
                        + ")) localStorage.setItem(key, value);"
                    )
                    context.add_init_script(seed_script)
                    page = context.new_page()
                    page.route("**/*", lambda route: _serve_local_app(client, route))
                    page.goto("http://testserver" + page_path)
                    page.wait_for_function(f"Boolean(window.{controller})")
                    stored = page.evaluate(
                        "keys => Object.fromEntries(keys.map(key => [key, localStorage.getItem(key)]))",
                        [old_key, new_key, "studio_theme", "t3_unrelated_sentinel"],
                    )
                    assert stored == {
                        old_key: value,
                        new_key: value,
                        "studio_theme": "dark",
                        "t3_unrelated_sentinel": "keep-exactly",
                    }
                finally:
                    context.close()
        finally:
            browser.close()


def test_view_mode_local_storage_does_not_migrate_missing_or_invalid_legacy_values():
    """缺失或非法旧值不得触发迁移写入，也不得改写旧值。"""
    browser_api = pytest.importorskip("playwright.sync_api")
    cases = (
        ("/static/pages/index.html", "v2_proj_view_mode", "workbench_proj_view_mode", "WorkbenchHome"),
        ("/static/pages/projects.html", "v2_projects_view_mode", "workbench_projects_view_mode", "WorkbenchProjects"),
    )
    with TestClient(app) as client, open_playwright(browser_api) as playwright:
        browser = _launch_local_chrome(browser_api, playwright)
        try:
            for page_path, old_key, new_key, controller in cases:
                for legacy_value in (None, "unsupported-view"):
                    context = browser.new_context()
                    try:
                        init_script = (
                            "(() => {"
                            f"const legacyKey = {json.dumps(old_key)};"
                            f"const currentKey = {json.dumps(new_key)};"
                            f"const legacyValue = {json.dumps(legacy_value)};"
                            "const originalSetItem = Storage.prototype.setItem;"
                            "if (legacyValue !== null) originalSetItem.call(localStorage, legacyKey, legacyValue);"
                            "window.__gw_view_mode_writes = [];"
                            "Storage.prototype.setItem = function(key, value) {"
                            "if (key === legacyKey || key === currentKey) window.__gw_view_mode_writes.push([key, String(value)]);"
                            "return originalSetItem.call(this, key, value);"
                            "};"
                            "})();"
                        )
                        context.add_init_script(init_script)
                        page = context.new_page()
                        page.route("**/*", lambda route: _serve_local_app(client, route))
                        page.goto("http://testserver" + page_path)
                        page.wait_for_function(f"Boolean(window.{controller})")
                        stored = page.evaluate(
                            "keys => Object.fromEntries(keys.map(key => [key, localStorage.getItem(key)]))",
                            [old_key, new_key],
                        )
                        assert stored == {old_key: legacy_value, new_key: None}
                        writes = page.evaluate("window.__gw_view_mode_writes")
                        assert writes == []
                    finally:
                        context.close()
        finally:
            browser.close()
