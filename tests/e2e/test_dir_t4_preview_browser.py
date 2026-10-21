"""QIN-241：工作流缩略图、空白点击和已加载预览复用。"""

from __future__ import annotations

import json
from typing import Any

import pytest

from workflow_support import goto_ready, settings_browser


IMAGE_ASSET_ID = "dir-t4-image-fixture"
WORKFLOW_ASSET_ID = "dir-t4-workflow-fixture"
WORKFLOW_URL = "/static/pages/workflow.html?id=dir-t4-flow-fixture"
PNG_BYTES = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    "0000000d49444154789c636000020000050001a5f645400000000049454e44ae426082"
)


def test_dir_t4_registry_blank_click_keeps_workflow_preview_and_reuses_loaded_image(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    workflow_requests: list[str] = []
    media_requests: list[str] = []
    with settings_browser(monkeypatch, tmp_path) as (page, base, errors, _):
        def inject_assets(route: Any) -> None:
            response = route.fetch()
            payload = response.json()
            items = payload.get("items", [])
            fixtures = [
                {
                    "id": IMAGE_ASSET_ID,
                    "asset_id": IMAGE_ASSET_ID,
                    "name": "dir-t4-preview.png",
                    "kind": "image",
                    "url": "/media-fixture.png",
                    "size": len(PNG_BYTES),
                    "tags": [],
                    "relations": [],
                    "versions": [],
                    "metadata": {},
                    "created_at": "2026-10-21T00:00:00Z",
                },
                {
                    "id": WORKFLOW_ASSET_ID,
                    "asset_id": WORKFLOW_ASSET_ID,
                    "name": "workflow-preview.png",
                    "kind": "image",
                    "url": WORKFLOW_URL,
                    "size": 48,
                    "tags": [],
                    "relations": [],
                    "versions": [],
                    "metadata": {},
                    "created_at": "2026-10-21T00:00:00Z",
                },
            ]
            for fixture in fixtures:
                if not any(item.get("id") == fixture["id"] for item in items):
                    items.append(fixture)
            payload["total"] = len(items)
            route.fulfill(response=response, body=json.dumps(payload, ensure_ascii=False))

        page.context.route("**/api/asset-registry/assets?*", inject_assets)
        page.context.route(
            "**/media-fixture.png",
            lambda route: route.fulfill(status=200, content_type="image/png", body=PNG_BYTES),
        )

        def capture_request(request: Any) -> None:
            if "/static/pages/workflow.html?id=dir-t4-flow-fixture" in request.url:
                workflow_requests.append(request.url)
            if "/media" in request.url:
                media_requests.append(request.url)

        page.on("request", capture_request)
        goto_ready(page, base + "/static/embeds/asset-manager.html")
        page.locator("[data-registry-card]").first.wait_for(state="visible", timeout=15000)
        simple_toggle = page.locator("[data-registry-simple-toggle]")
        if simple_toggle.get_attribute("aria-pressed") == "true":
            simple_toggle.click()
        image_card = page.locator(f'[data-registry-card="{IMAGE_ASSET_ID}"]')
        workflow_card = page.locator(f'[data-registry-card="{WORKFLOW_ASSET_ID}"]')
        image_card.wait_for(state="visible", timeout=15000)
        workflow_card.wait_for(state="visible", timeout=15000)

        workflow_thumb = workflow_card.locator("img[data-registry-preview-src], img[src]")
        assert workflow_thumb.count() == 0
        assert workflow_card.locator(".workflow-file-icon").count() == 1
        assert workflow_requests == []

        loaded_image = image_card.locator("img[data-registry-preview-loaded='true']")
        page.wait_for_function(
            """id => {
                const card = [...document.querySelectorAll('[data-registry-card]')]
                    .find(node => node.dataset.registryCard === id);
                const image = card?.querySelector('img[data-registry-preview-loaded="true"]');
                return Boolean(image && image.complete && image.naturalWidth > 0);
            }""",
            arg=IMAGE_ASSET_ID,
            timeout=15000,
        )
        loaded_image.evaluate("image => { window.__dirT4LoadedPreview = image; }")
        assert len(media_requests) == 1

        workflow_card.dblclick()
        detail = page.locator(".asset-detail-viewer")
        detail.wait_for(state="visible", timeout=5000)
        assert detail.locator(".asset-detail-stage-content .workflow-file-icon").count() == 1
        media_count_before_detail_click = len(media_requests)
        stage = detail.locator(".asset-detail-stage-content")
        preview_node = stage.locator(".workflow-file-icon")
        preview_node.evaluate("node => { window.__dirT4WorkflowPreview = node; }")
        stage.evaluate(
            """element => {
                const event = type => new PointerEvent(type, {
                    bubbles:true, pointerType:'mouse', button:0, clientX:1, clientY:1,
                });
                element.dispatchEvent(event('pointerdown'));
                element.dispatchEvent(event('pointerup'));
                element.dispatchEvent(new MouseEvent('click', {bubbles:true}));
            }"""
        )
        assert page.evaluate("() => Boolean(window.__dirT4WorkflowPreview?.isConnected)") is True
        assert detail.locator(".asset-detail-stage-content .workflow-file-icon").count() == 1
        assert len(media_requests) == media_count_before_detail_click
        assert workflow_requests == []

        detail.locator("[data-detail-viewer-close]").first.click()
        detail.wait_for(state="detached", timeout=5000)

        scroll = page.locator(".registry-assets-scroll")
        bounds = scroll.bounding_box()
        assert bounds is not None
        image_card.click()
        assert "active" in (image_card.get_attribute("class") or "")
        page.mouse.click(bounds["x"] + bounds["width"] - 8, bounds["y"] + bounds["height"] - 8)
        assert "active" not in (image_card.get_attribute("class") or "")
        assert loaded_image.evaluate("image => image.isConnected") is True

        card_bounds = image_card.bounding_box()
        assert card_bounds is not None
        page.mouse.move(bounds["x"] + bounds["width"] - 16, bounds["y"] + bounds["height"] - 16)
        page.mouse.down()
        page.mouse.move(card_bounds["x"] + card_bounds["width"] / 2, card_bounds["y"] + card_bounds["height"] / 2, steps=6)
        page.mouse.up()
        assert "selected" in (image_card.get_attribute("class") or "")

        media_count_before_rerender = len(media_requests)
        page.locator('[data-registry-layout="list"]').click()
        assert page.evaluate(
            """id => {
                const previous = window.__dirT4LoadedPreview;
                const card = [...document.querySelectorAll('[data-registry-card]')]
                    .find(node => node.dataset.registryCard === id);
                const current = card?.querySelector('img[data-registry-preview-loaded="true"]');
                return Boolean(previous && current === previous && current.isConnected);
            }""",
            arg=IMAGE_ASSET_ID,
        ) is True
        assert len(media_requests) == media_count_before_rerender
        assert workflow_requests == []
        assert errors == []
