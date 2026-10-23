"""DIR-T7 来源筛选必须跟随资产的新登记库路径。"""

from __future__ import annotations

import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from gw.api.app import create_app
from gw.asset_registry import index_jobs, repository as registry_repo
from gw.core import storage
from gw.core.errors import CleanroomException


BASE = "/api/asset-registry"
AUTH = {"Authorization": "Bearer cleanroom-test", "X-User-Role": "editor"}


@pytest.fixture()
def registered_sources(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    allowed_root = tmp_path / "allowed"
    source_a_path = allowed_root / "source-a"
    source_b_path = allowed_root / "source-b"
    source_a_path.mkdir(parents=True)
    source_b_path.mkdir()
    (source_a_path / "clip.txt").write_text("source A", encoding="utf-8")
    monkeypatch.setenv("GW_ALLOWED_ROOTS", str(allowed_root))
    if index_jobs._default:
        index_jobs._default.shutdown()
    index_jobs._default = None
    yield allowed_root, source_a_path, source_b_path
    if index_jobs._default:
        index_jobs._default.shutdown()
    index_jobs._default = None


def _wait_for_index(client: TestClient, job_id: str) -> dict:
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        response = client.get(BASE + f"/workspace-jobs/{job_id}", headers=AUTH)
        assert response.status_code == 200, response.text
        job = response.json()["job"]
        if job["status"] in index_jobs.TERMINAL:
            return job
        time.sleep(0.02)
    pytest.fail("后台索引未在期限内终结")


def _register_source(client: TestClient, source_id: str, path: Path) -> dict:
    settings = client.get("/api/storage-settings", headers=AUTH)
    assert settings.status_code == 200, settings.text
    accepted = client.post(
        BASE + "/reindex",
        headers={**AUTH, "Idempotency-Key": f"dir-t7-register-{source_id}"},
        json={
            "background": True,
            "hash_files": False,
            "max_files": 100,
            "register_root": {
                "id": source_id,
                "name": source_id,
                "path": str(path),
                "expected_version": settings.json()["revision"],
            },
        },
    )
    assert accepted.status_code == 202, accepted.text
    job = _wait_for_index(client, accepted.json()["job_id"])
    assert job["status"] == "succeeded", job
    return job


def test_patch_display_path_reassigns_asset_to_matching_registered_source(
    registered_sources,
):
    allowed_root, source_a_path, source_b_path = registered_sources

    with TestClient(create_app()) as client:
        _register_source(client, "local_dir_t7_source_a", source_a_path)
        _register_source(client, "local_dir_t7_source_b", source_b_path)

        root_id = storage.root_identity_id(allowed_root.resolve())
        source_a = client.get(
            BASE + "/assets",
            headers=AUTH,
            params={"root_id": "local_dir_t7_source_a"},
        )
        assert source_a.status_code == 200, source_a.text
        assert source_a.json()["total"] == 1
        asset = source_a.json()["assets"][0]
        assert asset["root_id"] == root_id
        assert asset["storage_root_id"] == "local_dir_t7_source_a"
        assert asset["canonical_relpath"] == "source-a/clip.txt"
        (source_a_path / "clip.txt").rename(source_b_path / "clip.txt")

        updated = client.patch(
            f"{BASE}/assets/{asset['asset_id']}",
            headers=AUTH,
            json={
                "root_id": root_id,
                "display_path": "source-b/clip.txt",
                "expected_version": registry_repo.state().read()["revision"],
            },
        )

        assert updated.status_code == 200, updated.text
        moved_asset = updated.json()["asset"]
        assert moved_asset["root_id"] == root_id
        assert moved_asset["canonical_relpath"] == "source-b/clip.txt"
        assert moved_asset["storage_root_id"] == "local_dir_t7_source_b"

        source_a_after = client.get(
            BASE + "/assets",
            headers=AUTH,
            params={"root_id": "local_dir_t7_source_a"},
        )
        source_b_after = client.get(
            BASE + "/assets",
            headers=AUTH,
            params={"root_id": "local_dir_t7_source_b"},
        )
        assert source_a_after.status_code == 200, source_a_after.text
        assert source_b_after.status_code == 200, source_b_after.text
        assert source_a_after.json()["total"] == 0
        assert [item["asset_id"] for item in source_b_after.json()["assets"]] == [
            asset["asset_id"]
        ]


def test_reassigning_to_duplicate_registered_prefix_fails_closed(monkeypatch):
    aliases = {
        source_id: {
            "root_id": "root_dir_t7_shared",
            "prefix": "shared-library",
            "storage_root_id": source_id,
        }
        for source_id in ("local_dir_t7_source_a", "local_dir_t7_source_b")
    }
    monkeypatch.setattr(index_jobs, "_roots", lambda: ({}, aliases))

    with pytest.raises(CleanroomException) as raised:
        registry_repo._storage_source_for_location(
            "root_dir_t7_shared", "shared-library/clip.txt", "local_dir_t7_source_c"
        )

    assert raised.value.code == "ASSET_SOURCE_AMBIGUOUS"
