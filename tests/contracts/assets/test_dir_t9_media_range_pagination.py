"""QIN-246 DIR-T9：媒体 Range、本地素材分页和后台重索引契约。"""

import threading
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from gw.api.app import create_app
from gw.asset_library import repository as local_repo
from gw.asset_registry import index_jobs
from gw.asset_registry import repository as registry_repo


BASE = "/api/asset-registry"
AUTH = {"Authorization": "Bearer index-owner", "X-User-Role": "editor"}


@pytest.fixture
def dir_t9_runtime(tmp_path, monkeypatch):
    root = tmp_path / "allowed"
    root.mkdir()
    monkeypatch.setenv("GW_AUTH_MODE", "local")
    monkeypatch.setenv("GW_ALLOWED_ROOTS", str(root))
    monkeypatch.setenv("GW_DATA_DIR", str(tmp_path / "data"))
    if index_jobs._default:
        index_jobs._default.shutdown()
    index_jobs._default = None
    yield root
    if index_jobs._default:
        index_jobs._default.shutdown()
    index_jobs._default = None


def _create_media_version(client, path):
    imported = client.post(
        BASE + "/assets/import",
        headers=AUTH,
        json={"items": [{"name": path.name, "kind": "image"}]},
    )
    assert imported.status_code == 200, imported.text
    asset_id = imported.json()["asset_ids"][0]
    version_response = client.post(
        f"{BASE}/assets/{asset_id}/image-versions",
        headers=AUTH,
        json={"source_path": str(path)},
    )
    assert version_response.status_code == 200, version_response.text
    version_id = version_response.json()["version"]["version_id"]
    return asset_id, version_id


def test_media_routes_stream_single_ranges_and_reject_invalid_ranges(
    dir_t9_runtime, monkeypatch
):
    media = dir_t9_runtime / "range-fixture.png"
    payload = bytes(range(256)) * 32
    media.write_bytes(payload)

    with TestClient(create_app()) as client:
        asset_id, version_id = _create_media_version(client, media)
        urls = [
            f"{BASE}/assets/{asset_id}/media",
            f"{BASE}/assets/{asset_id}/image-versions/{version_id}/media",
        ]

        def forbidden_read_bytes(_path):
            raise AssertionError("媒体路由不得将整文件读入内存")

        monkeypatch.setattr(Path, "read_bytes", forbidden_read_bytes)
        for url in urls:
            ranged = client.get(url, headers={**AUTH, "Range": "bytes=4-8"})
            assert ranged.status_code == 206, ranged.text
            assert ranged.content == payload[4:9]
            assert ranged.headers["content-range"] == f"bytes 4-8/{len(payload)}"
            assert ranged.headers["content-length"] == "5"
            assert ranged.headers["accept-ranges"] == "bytes"
            assert ranged.headers["cache-control"] == "private"

            suffix = client.get(url, headers={**AUTH, "Range": "bytes=-4"})
            assert suffix.status_code == 206, suffix.text
            assert suffix.content == payload[-4:]
            assert suffix.headers["content-range"] == (
                f"bytes {len(payload) - 4}-{len(payload) - 1}/{len(payload)}"
            )

            invalid = client.get(
                url, headers={**AUTH, "Range": f"bytes={len(payload)}-"}
            )
            assert invalid.status_code == 416, invalid.text
            assert invalid.headers["content-range"] == f"bytes */{len(payload)}"
            assert invalid.headers["accept-ranges"] == "bytes"
            assert invalid.headers["cache-control"] == "private"

            full = client.get(url, headers=AUTH)
            assert full.status_code == 200, full.text
            assert full.content == payload
            assert full.headers["content-length"] == str(len(payload))
            assert full.headers["accept-ranges"] == "bytes"
            assert full.headers["cache-control"] == "private"


def test_local_assets_cursor_pages_are_stable_opaque_and_filter_bound(
    dir_t9_runtime
):
    names = ["05.png", "01.png", "03.png", "02.png", "04.png"]
    paths = []
    for name in names:
        path = dir_t9_runtime / name
        path.write_bytes(b"image")
        paths.append(str(path))
    video = dir_t9_runtime / "movie.mp4"
    video.write_bytes(b"video")
    local_repo.index_local_files(paths, "image")
    local_repo.index_local_files([str(video)], "video")

    with TestClient(create_app()) as client:
        first_response = client.get(
            "/api/local-assets", headers=AUTH, params={"kind": "image", "limit": 2}
        )
        assert first_response.status_code == 200, first_response.text
        first = first_response.json()
        assert first["total"] == first["count"] == 5
        assert first["limit"] == 2
        assert first["has_more"] is True
        assert len(first["items"]) == 2
        cursor = first["next_cursor"]
        assert isinstance(cursor, str) and len(cursor) > 8
        assert cursor != "2"

        repeated = client.get(
            "/api/local-assets",
            headers=AUTH,
            params={"kind": "image", "limit": 2, "cursor": cursor},
        )
        assert repeated.status_code == 200, repeated.text
        assert repeated.json()["items"] == client.get(
            "/api/local-assets",
            headers=AUTH,
            params={"kind": "image", "limit": 2, "cursor": cursor},
        ).json()["items"]

        mismatched = client.get(
            "/api/local-assets",
            headers=AUTH,
            params={"kind": "video", "limit": 2, "cursor": cursor},
        )
        assert mismatched.status_code == 400, mismatched.text
        assert mismatched.json()["detail"]["code"] == "INVALID_CURSOR"

        names_seen = [item["display_path"] for item in first["items"]]
        while cursor:
            page_response = client.get(
                "/api/local-assets",
                headers=AUTH,
                params={"kind": "image", "limit": 2, "cursor": cursor},
            )
            assert page_response.status_code == 200, page_response.text
            page = page_response.json()
            names_seen.extend(item["display_path"] for item in page["items"])
            cursor = page["next_cursor"]
            assert page["has_more"] is bool(cursor)

        assert names_seen == sorted(names)
        assert len(names_seen) == len(set(names_seen)) == 5


def test_registry_cursor_pages_keep_root_and_sort_filters_bound(dir_t9_runtime):
    with TestClient(create_app()) as client:
        imported = client.post(
            BASE + "/assets/import",
            headers=AUTH,
            json={
                "items": [
                    {"name": f"asset-{name}.png", "kind": "image"}
                    for name in ("e", "a", "d", "b", "c")
                ]
                + [{"name": "other-root.png", "kind": "image"}]
            },
        )
        assert imported.status_code == 200, imported.text
        asset_ids = imported.json()["asset_ids"]

        def assign_roots(raw):
            for asset_id in asset_ids[:-1]:
                raw["assets"][asset_id]["storage_root_id"] = "root-main"
            raw["assets"][asset_ids[-1]]["storage_root_id"] = "root-other"

        registry_repo.state().mutate(assign_roots)
        query = {"root_id": "root-main", "category": "image", "sort": "name", "limit": 2}
        first_response = client.get(BASE + "/assets", headers=AUTH, params=query)
        assert first_response.status_code == 200, first_response.text
        first = first_response.json()
        assert first["has_more"] is True
        assert first["next_cursor"] and first["next_cursor"] != "2"

        next_query = {**query, "cursor": first["next_cursor"]}
        second_response = client.get(BASE + "/assets", headers=AUTH, params=next_query)
        assert second_response.status_code == 200, second_response.text
        second = second_response.json()
        repeated = client.get(BASE + "/assets", headers=AUTH, params=next_query)
        assert repeated.status_code == 200, repeated.text
        assert repeated.json()["assets"] == second["assets"]

        wrong_root = client.get(
            BASE + "/assets",
            headers=AUTH,
            params={**query, "root_id": "root-other", "cursor": first["next_cursor"]},
        )
        assert wrong_root.status_code == 400, wrong_root.text
        assert wrong_root.json()["detail"]["code"] == "INVALID_CURSOR"

        third_response = client.get(
            BASE + "/assets",
            headers=AUTH,
            params={**query, "cursor": second["next_cursor"]},
        )
        assert third_response.status_code == 200, third_response.text
        third = third_response.json()
        assert third["has_more"] is False
        assert third["next_cursor"] is None
        names = [item["name"] for page in (first, second, third) for item in page["assets"]]
        assert names == ["asset-a.png", "asset-b.png", "asset-c.png", "asset-d.png", "asset-e.png"]


def _wait_for_job(client, job_id):
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        response = client.get(
            f"{BASE}/workspace-jobs/{job_id}", headers=AUTH
        )
        assert response.status_code == 200, response.text
        job = response.json()["job"]
        if job["status"] in index_jobs.TERMINAL:
            return job
        time.sleep(0.02)
    pytest.fail("后台重索引任务未在期限内结束")


def test_reindex_defaults_to_background_and_reports_scan_and_completion(
    dir_t9_runtime, monkeypatch
):
    (dir_t9_runtime / "indexed.txt").write_text("fixture", encoding="utf-8")
    scan_entered = threading.Event()
    release_scan = threading.Event()
    original_scan = index_jobs.IndexJobs._scan
    original_sync_scan = registry_repo._scan_allowed_roots
    sync_scan_calls = []

    def guarded_sync_scan():
        sync_scan_calls.append(threading.current_thread().name)
        return original_sync_scan()

    def controlled_worker_scan(self, job_id, attempt, roots, request):
        scan_entered.set()
        if not release_scan.wait(5):
            raise AssertionError("测试没有释放后台扫描")
        return original_scan(self, job_id, attempt, roots, request)

    monkeypatch.setattr(registry_repo, "_scan_allowed_roots", guarded_sync_scan)
    monkeypatch.setattr(index_jobs.IndexJobs, "_scan", controlled_worker_scan)
    try:
        with TestClient(create_app(), raise_server_exceptions=False) as client:
            try:
                accepted = client.post(
                    BASE + "/reindex",
                    headers=AUTH,
                    json={},
                )
                assert accepted.status_code == 202, accepted.text
                body = accepted.json()
                assert body["job_id"]
                assert body["job"]["status"] in {"queued", "running"}
                assert body["poll_hint"]["url"].endswith(body["job_id"])
                assert sync_scan_calls == []
                assert scan_entered.wait(3)

                running = client.get(
                    f"{BASE}/workspace-jobs/{body['job_id']}", headers=AUTH
                ).json()["job"]
                assert running["status"] == "running"
                assert running["progress"]["phase"] == "scanning"

                release_scan.set()
                completed = _wait_for_job(client, body["job_id"])
                assert completed["status"] == "succeeded", completed
                assert completed["progress"]["phase"] == "completed"
                assert completed["result"]["scanned"] == 1
            finally:
                release_scan.set()
    finally:
        release_scan.set()


def test_reindex_rejects_sync_mode_without_scanning(dir_t9_runtime, monkeypatch):
    sync_scan_calls = []
    monkeypatch.setattr(
        registry_repo,
        "_scan_allowed_roots",
        lambda: sync_scan_calls.append(threading.current_thread().name) or [],
    )
    with TestClient(create_app()) as client:
        rejected = client.post(
            BASE + "/reindex", headers=AUTH, json={"background": False}
        )
    assert rejected.status_code == 400, rejected.text
    assert rejected.json()["detail"]["code"] == "INVALID_REQUEST"
    assert sync_scan_calls == []
