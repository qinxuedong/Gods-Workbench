"""DIR-T7 已确认目录别名的离线重连契约。"""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from gw.api.app import create_app
from gw.asset_registry import index_jobs, repository as registry_repo
from gw.core import storage


BASE = "/api/asset-registry"
AUTH = {"Authorization": "Bearer cleanroom-test", "X-User-Role": "editor"}
SOURCE_ID = "local_dir_t7_reconnect_source"


@pytest.fixture()
def registered_source_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    allowed_root = tmp_path / "allowed-root"
    allowed_root.mkdir()
    alias_path = tmp_path / "confirmed-external-alias"
    alias_path.mkdir()
    monkeypatch.setenv("GW_ALLOWED_ROOTS", str(allowed_root))
    if index_jobs._default:
        index_jobs._default.shutdown()
    index_jobs._default = None
    yield allowed_root, alias_path
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


def _submit_reindex(client: TestClient, key: str, source_id: str | None = None) -> dict:
    payload = {"background": True, "hash_files": False, "max_files": 100}
    if source_id is not None:
        payload["roots"] = [source_id]
    response = client.post(
        BASE + "/reindex",
        headers={**AUTH, "Idempotency-Key": key},
        json=payload,
    )
    assert response.status_code == 202, response.text
    return _wait(client, response.json()["job_id"])


@pytest.mark.parametrize(
    "offline_allowed_root", [False, True], ids=["source-offline", "allowed-root-offline"]
)
def test_reindex_reconnects_confirmed_external_alias_without_changing_identity(
    registered_source_paths, monkeypatch, offline_allowed_root
):
    allowed_root, alias_path = registered_source_paths
    source_path = allowed_root / "library"
    source_path.mkdir()
    (source_path / "clip.txt").write_text("registered source", encoding="utf-8")
    (alias_path / "clip.txt").write_text("confirmed alias media", encoding="utf-8")

    # 该测试模拟用户确认时文件系统判定两个访问路径指向同一目录。
    monkeypatch.setattr(os.path, "samefile", lambda _left, _right: True)

    with TestClient(create_app()) as client:
        settings = client.get("/api/storage-settings", headers=AUTH)
        assert settings.status_code == 200, settings.text
        registered = client.post(
            BASE + "/reindex",
            headers={**AUTH, "Idempotency-Key": "dir-t7-reconnect-register"},
            json={
                "background": True,
                "hash_files": False,
                "max_files": 100,
                "register_root": {
                    "id": SOURCE_ID,
                    "name": "重连来源",
                    "path": str(source_path),
                    "expected_version": settings.json()["revision"],
                },
            },
        )
        assert registered.status_code == 202, registered.text
        initial_job = _wait(client, registered.json()["job_id"])
        assert initial_job["status"] == "succeeded", initial_job

        original_root_id = storage.root_identity_id(allowed_root.resolve())
        initial_assets = registry_repo.state().read()["assets"]
        original_asset = next(
            asset
            for asset in initial_assets.values()
            if asset.get("root_id") == original_root_id
            and asset.get("canonical_relpath") == "library/clip.txt"
        )
        original_asset_id = original_asset["asset_id"]

        registry_revision = registry_repo.state().read()["revision"]
        confirmed = client.post(
            BASE + "/root-aliases/confirm",
            headers=AUTH,
            json={
                "path": str(alias_path),
                "storage_root_id": SOURCE_ID,
                "expected_version": registry_revision,
            },
        )
        assert confirmed.status_code == 200, confirmed.text
        binding = registry_repo.state().read()["root_aliases"][SOURCE_ID]
        assert binding["root_id"] == original_root_id
        assert binding["canonical_relpath"] == "library"
        assert str(alias_path.resolve()) in binding["aliases"]

        # 登记路径离线，已确认的外部 alias 仍可访问。
        offline_path = (
            allowed_root.with_name("allowed-root-offline")
            if offline_allowed_root
            else allowed_root / "library-offline"
        )
        if offline_allowed_root:
            allowed_root.rename(offline_path)
        else:
            source_path.rename(offline_path)
        try:
            reconnected_job = _submit_reindex(
                client, "dir-t7-reconnect-after-offline", SOURCE_ID
            )
            assert reconnected_job["status"] == "succeeded", reconnected_job
            assert reconnected_job["result"]["scanned"] == 1

            assets = registry_repo.state().read()["assets"]
            same_asset = assets[original_asset_id]
            assert same_asset["root_id"] == original_root_id
            assert same_asset["storage_root_id"] == SOURCE_ID
            assert same_asset["canonical_relpath"] == "library/clip.txt"
            assert same_asset["display_path"] == "library/clip.txt"
            matching_locations = [
                asset
                for asset in assets.values()
                if asset.get("canonical_relpath") == "library/clip.txt"
            ]
            assert [asset["root_id"] for asset in matching_locations] == [
                original_root_id
            ]

            media = client.get(
                f"{BASE}/assets/{original_asset_id}/media", headers=AUTH
            )
            assert media.status_code == 200, media.text
            assert media.content == b"confirmed alias media"

            facets = client.get(BASE + "/facets", headers=AUTH)
            assert facets.status_code == 200, facets.text
            same_source = next(
                root for root in facets.json()["roots"] if root["id"] == SOURCE_ID
            )
            assert same_source["availability"] == "available"
            listed = client.get(
                BASE + "/assets", headers=AUTH, params={"root_id": SOURCE_ID}
            )
            assert listed.status_code == 200, listed.text
            listed_asset = next(
                item for item in listed.json()["assets"]
                if item["asset_id"] == original_asset_id
            )
            assert listed_asset["root_availability"] == "available"
        finally:
            if offline_allowed_root:
                offline_path.rename(allowed_root)
            else:
                offline_path.rename(source_path)
