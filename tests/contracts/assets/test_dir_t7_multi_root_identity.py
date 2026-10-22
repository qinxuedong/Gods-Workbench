"""DIR-T7 多根位置身份、媒体回读和 Z:/UNC 确认绑定契约。"""

from __future__ import annotations

import base64
import json
import os
import time
import unicodedata
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from gw.api.app import create_app
from gw.asset_library import repository as local_asset_repo
from gw.asset_registry import index_jobs, repository as registry_repo
from gw.asset_registry.media_probe import _local_video
from gw.core import storage
from gw.core.errors import CleanroomException


BASE = "/api/asset-registry"
AUTH = {"Authorization": "Bearer cleanroom-test", "X-User-Role": "editor"}


@pytest.fixture()
def two_roots(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    first = tmp_path / "根甲"
    second = tmp_path / "根乙"
    first.mkdir()
    second.mkdir()
    monkeypatch.setenv("GW_ALLOWED_ROOTS", f"{first}{os.pathsep}{second}")
    if index_jobs._default:
        index_jobs._default.shutdown()
    index_jobs._default = None
    yield first, second
    if index_jobs._default:
        index_jobs._default.shutdown()
    index_jobs._default = None


def _wait(client: TestClient, job_id: str) -> dict:
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        response = client.get(BASE + f"/workspace-jobs/{job_id}", headers=AUTH)
        assert response.status_code == 200, response.text
        job = response.json()["job"]
        if job["status"] in index_jobs.TERMINAL:
            return job
        time.sleep(0.02)
    pytest.fail("后台索引未在期限内终结")


def _start_reindex(client: TestClient, *, key: str = "dir-t7-reindex") -> dict:
    response = client.post(
        BASE + "/reindex",
        headers={**AUTH, "Idempotency-Key": key},
        json={"background": True, "hash_files": False, "max_files": 100},
    )
    assert response.status_code == 202, response.text
    return _wait(client, response.json()["job_id"])


def test_relative_path_resolution_requires_root_id_when_multiple_roots(two_roots):
    first, second = two_roots
    (first / "same.mp4").write_bytes(b"first")
    (second / "same.mp4").write_bytes(b"second")
    roots, _ = index_jobs._roots()
    root_ids = {path.resolve(): root_id for root_id, path in roots.items()}

    with pytest.raises(CleanroomException) as missing:
        storage.resolve_within_roots("same.mp4")
    assert missing.value.code == "ROOT_ID_REQUIRED"

    resolved = storage.resolve_within_roots(
        "same.mp4", root_id=root_ids[second.resolve()]
    )
    assert resolved == (second / "same.mp4").resolve()


def test_reindex_keeps_duplicate_relative_paths_and_cursor_location(two_roots):
    first, second = two_roots
    decomposed_name = unicodedata.normalize("NFD", "café") + ".txt"
    (first / decomposed_name).write_text("甲", encoding="utf-8")
    (second / decomposed_name).write_text("乙", encoding="utf-8")

    with TestClient(create_app()) as client:
        job = _start_reindex(client)
        assert job["status"] == "succeeded", job
        assert job["result"]["indexed"] == 2

        first_page = client.get(
            BASE + "/assets", headers=AUTH,
            params={"sort": "asset_id", "limit": 1},
        )
        assert first_page.status_code == 200, first_page.text
        body = first_page.json()
        assert body["has_more"] is True
        cursor = json.loads(
            base64.urlsafe_b64decode(body["next_cursor"] + "=" * (-len(body["next_cursor"]) % 4))
        )
        assert cursor["last_location"]["root_id"] == body["assets"][0]["root_id"]
        assert cursor["last_location"]["canonical_relpath"] == body["assets"][0]["canonical_relpath"]

        second_page = client.get(
            BASE + "/assets", headers=AUTH,
            params={"sort": "asset_id", "limit": 1, "cursor": body["next_cursor"]},
        )
        assert second_page.status_code == 200, second_page.text
        assets = body["assets"] + second_page.json()["assets"]
        assert len({item["root_id"] for item in assets}) == 2
        assert {item["canonical_relpath"] for item in assets} == {"café.txt"}
        assert {item["display_path"] for item in assets} == {decomposed_name}


def test_media_readback_selects_asset_root_not_first_root(two_roots):
    first, second = two_roots
    (first / "clip.mp4").write_bytes(b"first-root")
    (second / "clip.mp4").write_bytes(b"second-root")
    roots, _ = index_jobs._roots()
    root_ids = {path.resolve(): root_id for root_id, path in roots.items()}
    asset = registry_repo._plain_asset(
        "ast_dir_t7_second",
        "clip.mp4",
        "video",
        root_id=root_ids[second.resolve()],
        storage_root_id=root_ids[second.resolve()],
        display_path="clip.mp4",
        canonical_relpath="clip.mp4",
    )
    registry_repo.state().mutate(
        lambda raw: raw.setdefault("assets", {}).update({asset["asset_id"]: asset})
    )

    assert _local_video(asset["asset_id"]) == (second / "clip.mp4").resolve()


def test_local_inventory_keeps_same_relpath_under_separate_roots(two_roots):
    first, second = two_roots
    first_file = first / "same.txt"
    second_file = second / "same.txt"
    first_file.write_text("甲", encoding="utf-8")
    second_file.write_text("乙", encoding="utf-8")

    result = local_asset_repo.index_local_files([str(first_file), str(second_file)])
    assert result["count"] == 2
    entries = list(local_asset_repo._local_state().read()["entries"].values())
    assert len(entries) == 2
    assert len({entry["_root_identity"] for entry in entries}) == 2
    assert {entry["canonical_relpath"] for entry in entries} == {"same.txt"}

    first_page = local_asset_repo.list_local_assets(limit=1)
    second_page = local_asset_repo.list_local_assets(
        limit=1, cursor=first_page["next_cursor"]
    )
    assert first_page["has_more"] is True
    assert second_page["items"]


def test_reindex_marks_unresolvable_legacy_identity_for_manual_repair(two_roots):
    first, second = two_roots
    (first / "legacy.txt").write_text("甲", encoding="utf-8")
    (second / "legacy.txt").write_text("乙", encoding="utf-8")
    legacy = registry_repo._plain_asset(
        "ast_dir_t7_legacy", "legacy.txt", "indexed", display_path="legacy.txt"
    )
    registry_repo.state().mutate(
        lambda raw: raw.setdefault("assets", {}).update({legacy["asset_id"]: legacy})
    )

    with TestClient(create_app()) as client:
        job = _start_reindex(client, key="dir-t7-legacy")
        assert job["status"] == "succeeded", job
        raw = registry_repo.state().read()
        migrated = raw["assets"][legacy["asset_id"]]
        assert migrated["identity_status"] == "ambiguous"
        assert legacy["asset_id"] in raw["identity_repair_queue"]
        assert migrated["name"] == "legacy.txt"


def test_confirmed_alias_maps_two_names_to_one_registered_source(two_roots, monkeypatch):
    first, second = two_roots
    source_path = first / "library"
    alias_path = second / "library"
    source_path.mkdir()
    alias_path.mkdir()
    (source_path / "clip.txt").write_text("one logical source", encoding="utf-8")
    (alias_path / "clip.txt").write_text("one logical source", encoding="utf-8")
    monkeypatch.setattr(os.path, "samefile", lambda left, right: True)

    with TestClient(create_app()) as client:
        settings = client.get("/api/storage-settings", headers=AUTH)
        assert settings.status_code == 200, settings.text
        created = client.post(
            BASE + "/reindex",
            headers={**AUTH, "Idempotency-Key": "dir-t7-register-source"},
            json={
                "background": True,
                "hash_files": False,
                "max_files": 100,
                "register_root": {
                    "id": "local_dir_t7_source",
                    "name": "测试来源",
                    "path": str(source_path),
                    "expected_version": settings.json()["revision"],
                },
            },
        )
        assert created.status_code == 202, created.text
        assert _wait(client, created.json()["job_id"])["status"] == "succeeded"

        suggestions = client.post(
            BASE + "/root-aliases/suggestions",
            headers=AUTH,
            json={"path": str(alias_path)},
        )
        assert suggestions.status_code == 200, suggestions.text
        candidates = suggestions.json()["candidates"]
        assert [item["storage_root_id"] for item in candidates] == ["local_dir_t7_source"]
        assert registry_repo.state().read().get("root_aliases", {}) == {}

        confirmed = client.post(
            BASE + "/root-aliases/confirm",
            headers=AUTH,
            json={"path": str(alias_path), "storage_root_id": "local_dir_t7_source"},
        )
        assert confirmed.status_code == 200, confirmed.text
        aliases = registry_repo.state().read()["root_aliases"]
        assert str(alias_path.resolve()) in aliases["local_dir_t7_source"]["aliases"]
        assert confirmed.json()["bound"] is True


def test_windows_canonical_relpath_normalizes_case_nfc_and_separators():
    raw = unicodedata.normalize("NFD", "Café\\MEDIA/Clip.MP4")
    assert storage.canonical_relpath(raw, windows=True) == "café/media/clip.mp4"


@pytest.mark.parametrize(
    "raw",
    ["a/../b", "a/./b", "./a", "../a", "a//b", "a\\\\b", "a/"],
)
def test_canonical_relpath_rejects_dot_and_empty_components(raw):
    with pytest.raises(CleanroomException) as rejected:
        storage.canonical_relpath(raw, windows=True)
    assert rejected.value.code == "INVALID_PATH"


def test_offline_root_keeps_asset_and_reconnects_with_same_identity(two_roots):
    first, _ = two_roots
    (first / "offline.mp4").write_bytes(b"indexed before outage")
    root_id = storage.root_identity_id(first.resolve())

    with TestClient(create_app()) as client:
        initial = _start_reindex(client, key="dir-t7-online")
        assert initial["status"] == "succeeded", initial
        assert initial["result"]["indexed"] == 1

        before = registry_repo.state().read()
        asset = next(
            item for item in before["assets"].values()
            if item.get("root_id") == root_id and item.get("display_path") == "offline.mp4"
        )
        asset_id = asset["asset_id"]
        registry_repo.state().mutate(
            lambda raw: raw["assets"][asset_id].update(
                {"thumbnail_path": "synthetic/cache/thumbnail.jpg", "last_index_status": "indexed"}
            )
        )

        detached = first.with_name("detached-root")
        first.rename(detached)
        try:
            facets = client.get(BASE + "/facets", headers=AUTH)
            assert facets.status_code == 200, facets.text
            offline_root = next(item for item in facets.json()["roots"] if item["id"] == root_id)
            assert offline_root["availability"] == "offline"

            listed = client.get(BASE + "/assets", headers=AUTH, params={"root_id": root_id})
            assert listed.status_code == 200, listed.text
            offline_asset = next(item for item in listed.json()["assets"] if item["asset_id"] == asset_id)
            assert offline_asset["root_availability"] == "offline"
            assert offline_asset["thumbnail_path"] == "synthetic/cache/thumbnail.jpg"
            assert offline_asset["last_index_status"] == "indexed"

            failed_reindex = _start_reindex(client, key="dir-t7-offline")
            assert failed_reindex["status"] == "failed"
            assert failed_reindex["error_code"] == "INDEX_ROOT_UNAVAILABLE"
            assert asset_id in registry_repo.state().read()["assets"]
        finally:
            detached.rename(first)

        recovered = _start_reindex(client, key="dir-t7-recovered")
        assert recovered["status"] == "succeeded", recovered
        assert recovered["result"]["indexed"] == 0
        final_list = client.get(BASE + "/assets", headers=AUTH, params={"root_id": root_id})
        assert final_list.status_code == 200, final_list.text
        final_asset = next(item for item in final_list.json()["assets"] if item["asset_id"] == asset_id)
        assert final_asset["root_id"] == root_id
        assert final_asset["canonical_relpath"] == "offline.mp4"
        assert final_asset["root_availability"] == "available"
        assert final_asset["thumbnail_path"] == "synthetic/cache/thumbnail.jpg"
        assert len([item for item in final_list.json()["assets"] if item["canonical_relpath"] == "offline.mp4"]) == 1
