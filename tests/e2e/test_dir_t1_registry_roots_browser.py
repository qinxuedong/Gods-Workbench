"""DIR-T1 浏览器验收：新建资产库、等待索引并按来源筛选。"""

from __future__ import annotations

import time
from pathlib import Path

from workflow_support import goto_ready, settings_browser


def test_dir_t1_create_library_index_and_filter_from_browser(monkeypatch, tmp_path: Path):
    allowed = tmp_path / "允许根"
    library = allowed / "浏览器测试素材库"
    library.mkdir(parents=True)
    (library / "浏览器素材-甲.txt").write_text("浏览器真实索引", encoding="utf-8")
    monkeypatch.setenv("GW_ALLOWED_ROOTS", str(allowed))

    with settings_browser(monkeypatch, tmp_path) as (page, base, errors, _):
        bad_asset_responses = []
        page.on("response", lambda response: bad_asset_responses.append(response.url)
                if "/api/asset-registry/assets?" in response.url and response.status == 400 else None)

        goto_ready(page, base + "/static/pages/assets.html")
        vault = page.frame_locator("#assetVaultFrame")
        vault.locator("[data-board-create]").wait_for(state="visible")
        vault.locator("[data-board-create]").click()
        vault.locator('[data-board-preset="other"]').click()
        vault.locator("#boardCreateName").fill("DIR-T1 中文素材库")
        vault.locator("#boardCreatePath").fill(str(library))
        with page.expect_response(lambda response: response.url.endswith("/api/asset-registry/reindex")
                                  and response.request.method == "POST") as registration:
            vault.locator("[data-board-create-confirm]").click()
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

        vault.locator('[data-tab]').filter(has_text="DIR-T1 中文素材库").click()
        vault.locator("[data-registry-card]").filter(has_text="浏览器素材-甲.txt").wait_for(timeout=15000)

        vault.locator('[data-tab="registry"]').click()
        source_filter = vault.locator(f'[data-registry-root="{root_id}"]')
        source_filter.wait_for(state="visible", timeout=15000)
        with page.expect_response(lambda item: "/api/asset-registry/assets?" in item.url
                                  and f"root_id={root_id}" in item.url) as filtered:
            source_filter.click()
        assert filtered.value.status == 200, filtered.value.text()
        vault.locator("[data-registry-card]").filter(has_text="浏览器素材-甲.txt").wait_for(timeout=15000)
        assert bad_asset_responses == []
        assert errors == []
