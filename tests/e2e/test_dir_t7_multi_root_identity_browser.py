"""DIR-T7 浏览器验收：Z:/UNC 候选在用户确认前不得绑定或重建索引。"""

from __future__ import annotations

import json
from pathlib import Path

from playwright.sync_api import expect
from workflow_support import goto_ready, settings_browser


def test_dir_t7_alias_suggestion_waits_for_explicit_user_confirmation(
    monkeypatch, tmp_path: Path
):
    allowed = tmp_path / "allowed"
    candidate = allowed / "mapped-drive" / "library"
    candidate.mkdir(parents=True)
    (candidate / "clip.txt").write_text("browser alias candidate", encoding="utf-8")
    monkeypatch.setenv("GW_ALLOWED_ROOTS", str(allowed))

    with settings_browser(monkeypatch, tmp_path) as (page, base, errors, _):
        suggestion_requests = []
        confirmation_requests = []
        reindex_requests = []
        settings_responses = []
        failed_requests = []

        def capture_settings(response):
            if response.url.endswith("/api/storage-settings"):
                try:
                    settings_responses.append(response.json())
                except Exception:
                    settings_responses.append({"status": response.status})

        page.on("response", capture_settings)
        page.on("requestfailed", lambda request: failed_requests.append({
            "url": request.url,
            "failure": request.failure,
        }))

        def suggest(route):
            suggestion_requests.append(route.request.post_data_json)
            route.fulfill(
                status=200,
                content_type="application/json",
                body=json.dumps({
                    "revision": 7,
                    "candidates": [{
                        "storage_root_id": "existing_nas_source",
                        "name": "NAS 素材",
                        "path": r"\\server\share\library",
                    }]
                }, ensure_ascii=False),
            )

        def confirm(route):
            confirmation_requests.append(route.request.post_data_json)
            route.fulfill(
                status=200,
                content_type="application/json",
                body=json.dumps({
                    "bound": True,
                    "storage_root_id": "existing_nas_source",
                    "name": "NAS 素材",
                    "path": r"\\server\share\library",
                    "revision": 8,
                }, ensure_ascii=False),
            )

        def reindex(route):
            reindex_requests.append(route.request.post_data_json)
            if not confirmation_requests:
                route.fulfill(status=409, content_type="application/json", body='{"detail":{"code":"ALIAS_CONFIRMATION_REQUIRED","message":"需先确认"}}')
                return
            route.fulfill(
                status=202,
                content_type="application/json",
                body='{"job_id":"job_dir_t7_alias","poll_hint":{"url":"/api/asset-registry/workspace-jobs/job_dir_t7_alias"}}',
            )

        page.route("**/api/asset-registry/root-aliases/suggestions", suggest)
        page.route("**/api/asset-registry/root-aliases/confirm", confirm)
        page.route("**/api/asset-registry/reindex", reindex)
        page.route(
            "**/api/asset-registry/workspace-jobs/job_dir_t7_alias",
            lambda route: route.fulfill(
                status=200,
                content_type="application/json",
                body='{"job":{"job_id":"job_dir_t7_alias","status":"succeeded","result":{"scanned":1}}}',
            ),
        )

        goto_ready(page, base + "/static/pages/assets.html")
        vault = page.frame_locator("#assetVaultFrame")
        create_button = page.locator("#assetSidebarCreate")
        create_button.wait_for(state="visible")
        create_button.click()
        expect(vault.locator("#boardCreateName")).to_be_visible()
        assert vault.locator("[data-board-preset]").count() == 0
        vault.locator("#boardCreateName").fill("DIR-T7 别名库")
        vault.locator("#boardCreatePath").fill(str(candidate))
        path_input = vault.locator("#boardCreatePath")
        assert path_input.input_value() == str(candidate), (
            f"input={path_input.evaluate('(el) => el.outerHTML')!r}, "
            f"active={vault.locator(':focus').evaluate('(el) => el.id')!r}, "
            f"body={vault.locator('body').inner_text()!r}, errors={errors!r}"
        )
        assert vault.locator("[data-board-create-confirm]").is_visible()
        assert vault.locator("[data-board-create-confirm]").is_enabled()
        try:
            with page.expect_request(
                lambda request: request.url.endswith(
                    "/api/asset-registry/root-aliases/suggestions"
                )
                and request.method == "POST",
                timeout=10000,
            ) as suggestion:
                vault.locator("[data-board-create-confirm]").click()
        except Exception as exc:
            raise AssertionError(
                "root alias suggestion request did not start: "
                f"suggestions={suggestion_requests!r}, "
                f"settings_responses={settings_responses!r}, "
                f"failed_requests={failed_requests!r}, "
                f"create_path={vault.locator('#boardCreatePath').input_value()!r}, "
                f"create_button_visible={vault.locator('[data-board-create-confirm]').is_visible()}, "
                f"create_button_enabled={vault.locator('[data-board-create-confirm]').is_enabled()}, "
                f"status={vault.locator('#assetStatus').inner_text()!r}, "
                f"errors={errors!r}, "
                f"page={vault.locator('body').inner_text()!r}"
            ) from exc
        assert suggestion.value.post_data_json == {"path": str(candidate)}

        alias_confirm = vault.locator('[data-root-alias-confirm]')
        try:
            alias_confirm.wait_for(state="visible", timeout=10000)
        except Exception as exc:
            raise AssertionError(
                "root alias confirmation did not appear: "
                f"suggestions={suggestion_requests!r}, "
                f"settings_responses={settings_responses!r}, "
                f"failed_requests={failed_requests!r}, "
                f"status={vault.locator('#assetStatus').inner_text()!r}, "
                f"errors={errors!r}, "
                f"page={vault.locator('body').inner_text()!r}"
            ) from exc
        assert len(suggestion_requests) == 1
        assert suggestion_requests[0]["path"] == str(candidate)
        assert confirmation_requests == []
        assert reindex_requests == []
        assert "NAS 素材" in vault.locator("[aria-labelledby='rootAliasConfirmTitle']").inner_text()

        close_button = vault.locator("[data-root-alias-close]")
        assert close_button.is_visible()
        assert close_button.get_attribute("aria-label") == "关闭"
        close_button.click()
        assert not vault.locator("[aria-labelledby='rootAliasConfirmTitle']").is_visible()
        assert confirmation_requests == []
        assert reindex_requests == []

        with page.expect_request(
            lambda request: request.url.endswith(
                "/api/asset-registry/root-aliases/suggestions"
            )
            and request.method == "POST",
            timeout=10000,
        ) as retried_suggestion:
            vault.locator("[data-board-create-confirm]").click()
        assert retried_suggestion.value.post_data_json == {"path": str(candidate)}
        alias_confirm.wait_for(state="visible", timeout=10000)

        with page.expect_response(
            lambda response: response.url.endswith("/api/asset-registry/root-aliases/confirm")
            and response.request.method == "POST"
        ) as confirmed:
            alias_confirm.click()
        assert confirmed.value.status == 200
        assert confirmation_requests[0]["storage_root_id"] == "existing_nas_source"
        assert confirmation_requests[0]["path"] == str(candidate)
        assert confirmation_requests[0]["expected_version"] == 7
        assert errors == []
