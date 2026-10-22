"""DIR-T11 浏览器验收：未归属入口失败恢复与重复选择 ZIP。"""

from __future__ import annotations

import io
import zipfile

import pytest

from workflow_support import goto_ready, settings_browser


@pytest.mark.parametrize("failure_mode", ["http_500", "disconnect"])
def test_unassigned_entry_stays_visible_and_retries_after_temporary_failure(
    failure_mode, monkeypatch, tmp_path
):
    with settings_browser(monkeypatch, tmp_path) as (page, base, errors, _):
        state = {"mode": "ok"}

        def unassigned_response(route):
            if state["mode"] == "http_500":
                route.fulfill(
                    status=500,
                    content_type="application/json",
                    body='{"detail":{"code":"INTERNAL_ERROR","message":"暂时不可用"}}',
                )
            elif state["mode"] == "disconnect":
                route.abort()
            else:
                route.fulfill(
                    status=200,
                    content_type="application/json",
                    body='{"assets":[],"total":0,"revision":0}',
                )

        page.route("**/api/assets/unassigned*", unassigned_response)
        goto_ready(page, base + "/static/pages/assets.html")
        entry = page.locator("#btnUnassignedInbox")
        entry.wait_for(state="visible")

        state["mode"] = failure_mode
        result = page.evaluate("async () => await window.GWUnassignedInbox.refreshEntry()")

        assert result == "error"
        assert entry.is_visible()
        status = page.locator("[data-unassigned-entry-status]")
        status.wait_for(state="visible")
        assert "重试" in status.inner_text()
        retry = status.locator("[data-unassigned-retry]")
        assert retry.is_visible()

        state["mode"] = "ok"
        retry.click()
        status.wait_for(state="detached")
        assert entry.is_visible()
        assert page.evaluate("window.GWUnassignedInbox.snapshot.total") == 0
        assert errors == []


@pytest.mark.parametrize("status_code", [401, 403])
def test_unassigned_entry_hides_only_on_auth_denial(status_code, monkeypatch, tmp_path):
    with settings_browser(monkeypatch, tmp_path) as (page, base, errors, _):
        def denied(route):
            route.fulfill(
                status=status_code,
                content_type="application/json",
                body='{"detail":{"code":"AUTH_DENIED","message":"无权限"}}',
            )

        page.route("**/api/assets/unassigned*", denied)
        goto_ready(page, base + "/static/pages/assets.html")
        entry = page.locator("#btnUnassignedInbox")
        entry.wait_for(state="hidden")
        assert not entry.is_visible()
        assert page.locator("[data-unassigned-entry-status]").count() == 0
        assert errors == []


def test_archive_download_has_unique_stable_names_for_duplicate_selection(
    monkeypatch, tmp_path
):
    root = tmp_path / "assets"
    first_dir = root / "first"
    second_dir = root / "second"
    first_dir.mkdir(parents=True)
    second_dir.mkdir(parents=True)
    (first_dir / "same.txt").write_bytes(b"first")
    (second_dir / "same.txt").write_bytes(b"second")
    monkeypatch.setenv("GW_ALLOWED_ROOTS", str(root))

    with settings_browser(monkeypatch, tmp_path) as (page, base, errors, _):
        goto_ready(page, base + "/static/pages/assets.html")
        api = page.context.request
        asset_ids = []
        for relative_path in ("first/same.txt", "second/same.txt"):
            created = api.post(
                base + "/api/asset-registry/assets/import",
                data={"items": [{"name": "same.txt", "kind": "document"}]},
                headers={"Origin": base},
            )
            assert created.status == 200, created.text()
            asset_id = created.json()["asset_ids"][0]
            asset_ids.append(asset_id)
            updated = api.patch(
                base + f"/api/asset-registry/assets/{asset_id}",
                data={"display_path": relative_path},
                headers={"Origin": base},
            )
            assert updated.status == 200, updated.text()

        url = base + "/api/asset-registry/assets/archive"
        payload = {"asset_ids": [asset_ids[0], asset_ids[0], asset_ids[1]]}
        first_export = api.post(url, data=payload, headers={"Origin": base})
        assert first_export.status == 200, first_export.text()
        with zipfile.ZipFile(io.BytesIO(first_export.body())) as archive:
            names = archive.namelist()
            assert len(names) == len(set(names)) == 2
            assert names[0] == "same.txt"
            assert asset_ids[1] in names[1]
            assert archive.read(names[0]) == b"first"
            assert archive.read(names[1]) == b"second"

        second_export = api.post(url, data=payload, headers={"Origin": base})
        assert second_export.status == 200, second_export.text()
        with zipfile.ZipFile(io.BytesIO(second_export.body())) as archive:
            assert archive.namelist() == names
        assert errors == []
