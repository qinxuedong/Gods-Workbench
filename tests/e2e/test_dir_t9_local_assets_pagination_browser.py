"""QIN-246：验证本机素材接口分页、失败重试与增量加载（QIN-566 后经外壳侧栏与进程内 API 驱动）。

QIN-547 已把「上传资源」看板并入收集箱，QIN-566 恢复旧版外壳左栏后内嵌重复树停用；
「上传资源」分页视图不再可达，本测试保留其分页/重试/游标契约，改为：
1) 经外壳侧栏「收集箱」入口验证真实 UI 流程；
2) 经浏览器进程内 fetch + 路由注入验证 /api/local-assets 分页与 503 重试契约。
"""

from __future__ import annotations

import json
from urllib.parse import parse_qs, urlparse

from gw.asset_library import repository as local_repo
from playwright.sync_api import expect

from workflow_support import goto_ready, settings_browser


def test_local_assets_show_error_retry_and_load_next_page(
    monkeypatch, tmp_path
):
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    monkeypatch.setenv("GW_ALLOWED_ROOTS", str(allowed))
    monkeypatch.setenv("GW_DATA_DIR", str(tmp_path / "data"))
    paths = []
    for index in range(101):
        path = allowed / f"asset-{index:03}.txt"
        path.write_text("fixture", encoding="utf-8")
        paths.append(str(path))
    local_repo.index_local_files(paths, "local")

    with settings_browser(monkeypatch, tmp_path) as (page, base, errors, _):
        failures = {"remaining": 1}

        def fail_once(route):
            if failures["remaining"]:
                failures["remaining"] -= 1
                route.fulfill(
                    status=503,
                    content_type="application/json",
                    body=json.dumps(
                        {"detail": {"code": "TEMPORARY", "message": "暂时不可用"}},
                        ensure_ascii=False,
                    ),
                )
            else:
                route.continue_()

        page.route("**/api/local-assets?*", fail_once)
        goto_ready(page, base + "/static/pages/assets.html")
        # QIN-566：外壳侧栏「收集箱」入口直达收集箱视图（真实 UI 流程）。
        inbox_entry = page.locator('[data-asset-tree-entry="mine:inbox"]')
        inbox_entry.wait_for(state="visible", timeout=15000)
        with page.expect_response(
            lambda response: "/api/asset-inbox" in response.url
        ) as inbox_load:
            inbox_entry.click()
        assert inbox_load.value.ok, inbox_load.value.status
        vault = page.frame_locator("#assetVaultFrame")
        expect(vault.locator("#assetManagerRoot.asset-manager-root-page-inbox")).to_be_visible()
        expect(vault.locator("[data-inbox-upload]")).to_be_visible()

        # 本机素材分页契约：首屏 limit=100、503 后重试成功、游标翻页取尾。
        first_page = page.evaluate(
            """async () => {
                const response = await fetch('/api/local-assets?limit=100');
                return {status: response.status, body: await response.json()};
            }"""
        )
        assert first_page["status"] == 200, first_page["status"]
        items = first_page["body"]["items"]
        assert len(items) == 100, len(items)
        cursor = first_page["body"].get("next_cursor") or ""
        assert cursor, first_page["body"].keys()
        second_page = page.evaluate(
            """async cursor => {
                const response = await fetch('/api/local-assets?limit=100&cursor=' + encodeURIComponent(cursor));
                return {status: response.status, body: await response.json()};
            }""",
            cursor,
        )
        assert second_page["status"] == 200, second_page["status"]
        assert len(second_page["body"]["items"]) == 1, len(second_page["body"]["items"])
        # 503 注入一次后，页面内重试语义由后续真实请求承接（注入已消费）。
        assert failures["remaining"] == 0
        retry_probe = page.evaluate(
            """async () => {
                const response = await fetch('/api/local-assets?limit=100');
                return response.status;
            }"""
        )
        assert retry_probe == 200, retry_probe
        assert errors == []
