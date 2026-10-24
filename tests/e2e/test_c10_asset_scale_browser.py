"""C10-T5：资产体验的真实浏览器收口与阈值冻结提案。

该文件只新增浏览器验收，不修改生产实现。真实浏览器用例必须显式设置
``GW_C10_REAL_E2E=1``；没有显式开启或本机没有可用 Chrome 时跳过，不能把
环境缺失记录成产品通过。报告只保存观察值，阈值始终等待用户确认。
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import subprocess
from pathlib import Path
from typing import Any

import pytest
from workflow_support import goto_ready, settings_browser

REPO_ROOT = Path(__file__).resolve().parents[2]
EVIDENCE_ROOT = REPO_ROOT / ".local" / "qa" / "C10-evidence"
PNG_BYTES = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)


def _real_run_enabled(environ: dict[str, str] | None = None) -> bool:
    """真实浏览器门禁必须由操作者显式开启。"""
    env = os.environ if environ is None else environ
    return str(env.get("GW_C10_REAL_E2E", "")).strip().lower() in {"1", "true", "yes"}


def _evidence_dir() -> Path:
    configured = str(os.environ.get("GW_C10_EVIDENCE_DIR", "")).strip()
    path = Path(configured).expanduser() if configured else EVIDENCE_ROOT
    if not path.is_absolute():
        path = (REPO_ROOT / path).resolve()
    path.mkdir(parents=True, exist_ok=True)
    return path


def _write_json(path: Path, payload: object) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _git_sha() -> str:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    return result.stdout.strip() if result.returncode == 0 else "unknown"


def _api_json(
    request: Any, base: str, method: str, path: str, payload: object | None = None
):
    headers = {"Origin": base}
    kwargs: dict[str, object] = {"headers": headers}
    if payload is not None:
        headers["Content-Type"] = "application/json"
        kwargs["data"] = json.dumps(payload, ensure_ascii=False)
    response = getattr(request, method.lower())(base + path, **kwargs)
    try:
        body = response.json()
    except ValueError:
        body = {"raw": response.text()}
    return response, body


def _response_summary(response: Any, body: object) -> dict[str, object]:
    return {"status": response.status, "body": body}


def test_c10_real_browser_gate_requires_explicit_opt_in(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("GW_C10_REAL_E2E", raising=False)
    assert _real_run_enabled() is False


def test_c10_thresholds_are_never_asserted_by_browser_gate() -> None:
    """阈值提案只能保留待确认状态，不允许测试自行签署。"""
    proposal = {
        "task": "QIN-195 / C10-T5",
        "thresholds": {
            "status": "pending_user_confirmation",
            "values": None,
            "note": "只记录观察值，待秦先生确认规模、p95、首屏、RPO/RTO 与试运行期限",
        },
    }
    serialized = json.dumps(proposal, ensure_ascii=False)
    assert proposal["thresholds"]["values"] is None
    assert "达标" not in serialized
    assert "通过" not in serialized


def test_c10_real_asset_browser_measurement(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """真实浏览器覆盖清单、分页、搜索、缩略图和错误降级。"""
    if not _real_run_enabled():
        pytest.skip("C10 真实浏览器默认关闭；设置 GW_C10_REAL_E2E=1 后才会运行")
    pytest.importorskip("playwright.sync_api")

    media_root = tmp_path / "media"
    media_root.mkdir()
    preview_source = media_root / "c10t5-preview.png"
    preview_source.write_bytes(PNG_BYTES)
    monkeypatch.setenv("GW_ALLOWED_ROOTS", str(media_root))

    responses: list[dict[str, object]] = []
    screenshots: list[str] = []
    with settings_browser(monkeypatch, tmp_path) as (page, base, errors, _):

        def capture(response: Any) -> None:
            if (
                "/api/asset-registry/assets" not in response.url
                or response.request.method != "GET"
            ):
                return
            try:
                body = response.json()
            except Exception:
                return
            if isinstance(body, dict):
                responses.append(
                    {
                        "url": response.url,
                        "status": response.status,
                        "offset": body.get("offset"),
                        "limit": body.get("limit"),
                        "total": body.get("total"),
                        "has_more": body.get("has_more"),
                        "next_cursor_present": bool(body.get("next_cursor")),
                        "item_count": len(body.get("items", []))
                        if isinstance(body.get("items"), list)
                        else None,
                    }
                )

        page.context.on("response", capture)
        request = page.context.request
        items = [
            {
                "name": f"c10t5-asset-{index:03d}.png",
                "kind": "image",
                "size_bytes": 1000 + index,
            }
            for index in range(101)
        ]
        items[0]["name"] = preview_source.name
        imported_response, imported = _api_json(
            request, base, "POST", "/api/asset-registry/assets/import", {"items": items}
        )
        assert imported_response.status == 200, imported
        asset_ids = imported.get("asset_ids")
        assert isinstance(asset_ids, list) and len(asset_ids) == len(items)
        patched_response, patched = _api_json(
            request,
            base,
            "PATCH",
            f"/api/asset-registry/assets/{asset_ids[0]}",
            {"display_path": preview_source.name},
        )
        assert patched_response.status == 200, patched

        goto_ready(page, base + "/static/pages/assets.html")
        frame = page.frame_locator("#assetVaultFrame")
        search = frame.locator("#registrySearch")
        search.wait_for(state="visible", timeout=15000)
        cards = frame.locator("[data-registry-card]")
        cards.first.wait_for(state="visible", timeout=15000)
        initial_count = cards.count()
        assert initial_count > 0
        initial_shot = _evidence_dir() / "c10-t5-assets-initial.png"
        page.screenshot(path=str(initial_shot), full_page=True)
        screenshots.append(initial_shot.name)

        scroll = frame.locator(".registry-assets-scroll")
        scroll.evaluate(
            "(node) => { node.scrollTop = node.scrollHeight; node.dispatchEvent(new Event('scroll', {bubbles: true})); }"
        )
        page.wait_for_timeout(1200)
        assert any(
            entry["status"] == 200 and entry["has_more"] is True for entry in responses
        ), responses
        paged = next(
            (entry for entry in reversed(responses) if entry["offset"] == 100), None
        )
        if paged is None:
            # The browser may prefetch with a cursor instead of exposing offset 100.
            assert any(entry["next_cursor_present"] for entry in responses), responses
        pagination_shot = _evidence_dir() / "c10-t5-assets-pagination.png"
        page.screenshot(path=str(pagination_shot), full_page=True)
        screenshots.append(pagination_shot.name)

        search.fill("c10t5-asset-100")
        page.wait_for_timeout(500)
        result_card = frame.locator("[data-registry-card]").filter(
            has_text="c10t5-asset-100"
        )
        result_card.first.wait_for(state="visible", timeout=15000)
        assert result_card.count() >= 1
        search_shot = _evidence_dir() / "c10-t5-assets-search.png"
        page.screenshot(path=str(search_shot), full_page=True)
        screenshots.append(search_shot.name)

        search.fill(preview_source.name)
        page.wait_for_timeout(500)
        preview_card = frame.locator("[data-registry-card]").filter(
            has_text=preview_source.name
        )
        preview_card.first.wait_for(state="visible", timeout=15000)
        preview_card.first.click()
        frame.locator(".registry-detail-panel").wait_for(state="visible", timeout=10000)
        assert frame.locator(".detail-media img").count() >= 1
        preview_shot = _evidence_dir() / "c10-t5-assets-thumbnail.png"
        page.screenshot(path=str(preview_shot), full_page=True)
        screenshots.append(preview_shot.name)

        missing_id = asset_ids[1]
        missing_media, missing_body = _api_json(
            request, base, "GET", f"/api/asset-registry/assets/{missing_id}/media"
        )
        assert missing_media.status == 404, missing_body
        assert isinstance(missing_body, dict)
        detail = missing_body.get("detail", {})
        assert isinstance(detail, dict)
        assert detail.get("code") == "MEDIA_NOT_AVAILABLE"
        degraded_shot = _evidence_dir() / "c10-t5-assets-degraded.png"
        page.screenshot(path=str(degraded_shot), full_page=True)
        screenshots.append(degraded_shot.name)

        assert not errors, errors

    report = {
        "task": "QIN-195 / C10-T5",
        "commit_sha": _git_sha(),
        "browser": {"engine": "Chrome via Playwright", "real_opt_in": True},
        "fixture": {
            "seed": "c10-t5-browser-v001",
            "asset_count": len(items),
            "asset_ids_sha256": hashlib.sha256(
                json.dumps(asset_ids, ensure_ascii=False, separators=(",", ":")).encode(
                    "utf-8"
                )
            ).hexdigest(),
        },
        "observations": {
            "initial_dom_card_count": initial_count,
            "asset_api_responses": responses,
            "missing_media": _response_summary(missing_media, missing_body),
            "page_errors": errors,
        },
        "screenshots": screenshots,
        "thresholds": {
            "status": "pending_user_confirmation",
            "values": None,
            "note": "只记录本机观察值；阈值冻结需秦先生确认",
        },
    }
    serialized = json.dumps(report, ensure_ascii=False)
    assert "达标" not in serialized and "通过" not in serialized
    _write_json(_evidence_dir() / "c10-t5-browser-report.json", report)
