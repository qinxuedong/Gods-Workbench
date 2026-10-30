"""DIR-T1 浏览器验收：新建资产库、等待索引并按来源筛选。"""

from __future__ import annotations

import re
import time
from pathlib import Path

from playwright.sync_api import TimeoutError as PlaywrightTimeout
from playwright.sync_api import expect
from workflow_support import goto_ready, settings_browser


def _reindex_post(response) -> bool:
    path = response.url.split("?", 1)[0].rstrip("/")
    return response.request.method == "POST" and path.endswith("/api/asset-registry/reindex")


def _storage_settings_get(response) -> bool:
    return response.request.method == "GET" and "/api/storage-settings" in response.url


def _dump_confirm_failure(vault, errors, traffic) -> str:
    status_text = ""
    index_state = ""
    path_value = "<missing>"
    try:
        status = vault.locator("#assetStatus")
        if status.count():
            status_text = status.inner_text(timeout=2000)
            index_state = status.get_attribute("data-gw-index-state") or ""
    except Exception as exc:
        status_text = f"<unreadable: {exc}>"
    try:
        path = vault.locator("#boardCreatePath")
        if path.count():
            path_value = path.input_value(timeout=2000)
        else:
            path_value = "<input missing>"
    except Exception as exc:
        path_value = f"<unreadable: {exc}>"
    return (
        f"status={status_text!r} index_state={index_state!r} path={path_value!r} "
        f"errors={errors!r} traffic={traffic!r}"
    )


def test_dir_t1_create_library_index_and_filter_from_browser(monkeypatch, tmp_path: Path):
    allowed = tmp_path / "允许根"
    library = allowed / "浏览器测试素材库"
    library.mkdir(parents=True)
    (library / "浏览器素材-甲.txt").write_text("浏览器真实索引", encoding="utf-8")
    monkeypatch.setenv("GW_ALLOWED_ROOTS", str(allowed))

    with settings_browser(monkeypatch, tmp_path) as (page, base, errors, _):
        bad_asset_responses = []
        bootstrap = {"facets": False}
        page.on(
            "response",
            lambda response: bad_asset_responses.append(response.url)
            if "/api/asset-registry/assets?" in response.url and response.status == 400
            else None,
        )
        page.on(
            "response",
            lambda response: bootstrap.__setitem__("facets", True)
            if "/api/asset-registry/facets" in response.url
            else None,
        )
        goto_ready(page, base + "/static/pages/assets.html")
        vault = page.frame_locator("#assetVaultFrame")
        page.locator('#assetCategories [data-asset-board-create]').wait_for(state="visible")
        # 首屏会在一次 networkidle 之后再延迟请求 facets。确认建库前先等这轮结束，
        # 否则 storage-settings / reindex 会排在同步扫描后面，30s 内等不到 POST 响应。
        deadline = time.monotonic() + 30
        while not bootstrap["facets"] and time.monotonic() < deadline:
            page.wait_for_timeout(100)
        assert bootstrap["facets"], "首屏 /api/asset-registry/facets 未返回"
        page.wait_for_load_state("networkidle", timeout=20_000)

        page.locator('#assetCategories [data-asset-board-create]').click()
        vault.locator('[data-board-preset="other"]').click()
        vault.locator("#boardCreateName").fill("DIR-T1 中文素材库")
        vault.locator("#boardCreatePath").fill(str(library))
        expect(vault.locator("#boardCreateName")).to_have_value("DIR-T1 中文素材库")
        expect(vault.locator("#boardCreatePath")).to_have_value(str(library))

        traffic = []

        def _track(response):
            url = response.url
            if "/api/storage-settings" in url or "/api/asset-registry/reindex" in url:
                traffic.append(f"{response.request.method} {response.status} {url}")

        def _track_failed(request):
            url = request.url
            if "/api/storage-settings" in url or "/api/asset-registry/reindex" in url:
                traffic.append(f"FAILED {request.method} {url} {request.failure}")

        page.on("response", _track)
        page.on("requestfailed", _track_failed)
        frame_handle = page.locator("#assetVaultFrame").element_handle()
        frame = frame_handle.content_frame() if frame_handle else None
        assert frame is not None, "资产库 iframe 未挂载"
        try:
            # Playwright 的 Frame 没有 expect_response，由 page 接收 iframe 内的响应。
            # 确认点击落在当前 frame，避免 iframe 重载后点到另一份文档。
            with page.expect_response(_storage_settings_get, timeout=60_000) as settings_got, page.expect_response(_reindex_post, timeout=60_000) as registration:
                frame.locator("[data-board-create-confirm]").click()
        except PlaywrightTimeout as exc:
            raise AssertionError(
                "确认建库后未观察到 storage-settings 或 reindex 响应。"
                + _dump_confirm_failure(vault, errors, traffic)
            ) from exc
        assert settings_got.value.ok, settings_got.value.status
        response = registration.value
        assert response.status == 202, response.text()
        payload = response.request.post_data_json
        root_id = payload["register_root"]["id"]
        assert payload["register_root"]["path"] == str(library)
        job_id = response.json()["job_id"]
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            job_response = page.context.request.get(base + f"/api/asset-registry/workspace-jobs/{job_id}")
            assert job_response.status == 200, job_response.text()
            job = job_response.json()["job"]
            if job["status"] in {"succeeded", "failed", "cancelled", "interrupted"}:
                break
            page.wait_for_timeout(100)
        assert job["status"] == "succeeded", job

        indexed = page.context.request.get(base + f"/api/asset-registry/assets?root_id={root_id}")
        assert indexed.status == 200, indexed.text()
        asset_ids = [item["asset_id"] for item in indexed.json()["assets"]]
        assert [item["name"] for item in indexed.json()["assets"]] == ["浏览器素材-甲.txt"]
        # 页面若启动在项目上下文，关联同一真实素材，避免项目范围掩盖来源筛选结果。
        projects = page.context.request.get(base + "/api/asset-registry/projects")
        assert projects.status == 200, projects.text()
        for project in projects.json().get("projects", []):
            linked = page.context.request.post(
                base + f"/api/asset-registry/projects/{project['project_id']}/assets",
                data={"asset_ids": asset_ids},
                headers={"Origin": base},
            )
            assert linked.status == 200, linked.text()

        # 建库完成时默认项目范围内的空列表可能已写入查询缓存，且发生在关联之前。
        # 刷新会清掉这份缓存；再打开看板才会按已关联的项目重新请求。
        with page.expect_response(lambda item: "/api/asset-registry/assets?" in item.url, timeout=20000):
            vault.locator("[data-refresh-assets]").click()
        overview_url = page.url
        overview_stats = vault.locator(".registry-overview-stats")
        overview_toolbar = vault.locator(".registry-toolbar")
        expect(overview_stats).to_be_visible()
        expect(overview_toolbar).to_be_visible()
        position = lambda locator: locator.evaluate("element => { const rect = element.getBoundingClientRect(); return {x:rect.x,y:rect.y}; }")

        def wait_for_fixed_rows(expected, board_id: str, viewport: str):
            try:
                frame.wait_for_function(
                    """({expected, boardId}) => {
                        const root = document.querySelector('#assetManagerRoot');
                        const rows = [
                            root?.querySelector(':scope > .registry-overview-shell'),
                            root?.querySelector(':scope > .registry-toolbar')
                        ];
                        if (!root || !rows.every(Boolean) || !root.classList.contains(`asset-manager-root-page-${boardId}`)) return false;
                        return rows.every((element, index) => {
                            const rect = element.getBoundingClientRect();
                            return Math.abs(rect.x - expected[index].x) <= 1 && Math.abs(rect.y - expected[index].y) <= 1;
                        });
                    }""",
                    arg={"expected": expected, "boardId": board_id},
                    timeout=5000,
                )
            except PlaywrightTimeout as exc:
                layout = vault.locator("#assetManagerRoot").evaluate("""root => ({
                    className:root.className,
                    columns:getComputedStyle(root).gridTemplateColumns,
                    rows:[...root.querySelectorAll(':scope > .registry-overview-shell, :scope > .registry-toolbar')]
                        .map(element => ({className:element.className,rect:element.getBoundingClientRect().toJSON()}))
                })""")
                raise AssertionError(f"{viewport}切换 {board_id} 后固定栏位置变化：{layout}") from exc

        overview_stats_box = position(overview_stats)
        overview_toolbar_box = position(overview_toolbar)
        page.locator('#assetCategories [data-asset-board]').filter(has_text="DIR-T1 中文素材库").click()
        expect(vault.locator(".registry-overview-stats")).to_be_visible()
        expect(vault.locator(".registry-toolbar")).to_be_visible()
        expect(vault.locator("#assetManagerRoot")).to_have_class(re.compile("asset-manager-root-library"))
        assert page.url == overview_url, "切换资源库不应导航离开当前页面"
        custom_stats_box = position(vault.locator(".registry-overview-stats"))
        custom_toolbar_box = position(vault.locator(".registry-toolbar"))
        assert (custom_stats_box["x"], custom_stats_box["y"]) == (overview_stats_box["x"], overview_stats_box["y"])
        assert (custom_toolbar_box["x"], custom_toolbar_box["y"]) == (overview_toolbar_box["x"], overview_toolbar_box["y"])
        vault.locator("[data-registry-card]").filter(has_text="浏览器素材-甲.txt").wait_for(timeout=15000)

        page.locator('#assetCategories [data-asset-board="registry"]').click()
        source_filter = vault.locator(f'[data-registry-root="{root_id}"]')
        source_filter.wait_for(state="visible", timeout=15000)
        with page.expect_response(lambda item: "/api/asset-registry/assets?" in item.url
                                  and f"root_id={root_id}" in item.url) as filtered:
            source_filter.click()
        assert filtered.value.status == 200, filtered.value.text()
        vault.locator("[data-registry-card]").filter(has_text="浏览器素材-甲.txt").wait_for(timeout=15000)
        fixed_url = page.url
        fixed_stats = vault.locator("#assetManagerRoot > .registry-overview-shell")
        fixed_toolbar = vault.locator("#assetManagerRoot > .registry-toolbar")
        expect(fixed_stats).to_be_visible()
        expect(fixed_toolbar).to_be_visible()
        reference_boxes = (position(fixed_stats), position(fixed_toolbar))
        for board_id in ("registry", "scripts", "projects", "assets", "workflows", "prompts", "canvas-assets", "local", "governance"):
            page.locator(f'#assetCategories [data-asset-board="{board_id}"]').click()
            expect(vault.locator("#assetManagerRoot")).to_have_class(re.compile(f"asset-manager-root-page-{board_id}"))
            expect(fixed_stats).to_be_visible()
            expect(fixed_toolbar).to_be_visible()
            assert page.url == fixed_url, f"切换 {board_id} 不应导航离开当前页面"
            wait_for_fixed_rows(reference_boxes, board_id, "桌面")
        page.set_viewport_size({"width": 500, "height": 900})
        expect(fixed_stats).to_be_visible()
        expect(fixed_toolbar).to_be_visible()
        narrow_reference = tuple(
            locator.evaluate("element => { const rect = element.getBoundingClientRect(); return {x:rect.x,y:rect.y}; }")
            for locator in (fixed_stats, fixed_toolbar)
        )
        for board_id in ("registry", "scripts", "projects", "assets", "workflows", "prompts", "canvas-assets", "local", "governance"):
            page.locator(f'#assetCategories [data-asset-board="{board_id}"]').click()
            expect(fixed_stats).to_be_visible()
            expect(fixed_toolbar).to_be_visible()
            assert page.url == fixed_url, f"窄屏切换 {board_id} 不应导航离开当前页面"
            expect(vault.locator("#assetManagerRoot")).to_have_class(re.compile(f"asset-manager-root-page-{board_id}"))
            wait_for_fixed_rows(narrow_reference, board_id, "窄屏")
        assert bad_asset_responses == []
        assert errors == []
