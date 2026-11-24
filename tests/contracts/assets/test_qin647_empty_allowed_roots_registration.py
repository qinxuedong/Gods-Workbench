"""QIN-647 空允许根下添加资源文件夹原子授权契约。"""

import time

import pytest
from fastapi.testclient import TestClient

from gw.api.app import create_app
from gw.asset_registry import index_jobs as jobs
from gw.core import storage
from gw.settings.service import (
    default_storage_allowed_roots_service,
    default_storage_settings_service,
)

BASE = "/api/asset-registry"
ADMIN = {"Authorization": "Bearer qin647-owner", "X-User-Role": "admin"}
EDITOR = {"Authorization": "Bearer qin647-owner", "X-User-Role": "editor"}


@pytest.fixture(autouse=True)
def empty_allowed_roots_runtime(monkeypatch):
    monkeypatch.setenv("GW_AUTH_MODE", "local")
    monkeypatch.delenv("GW_ALLOWED_ROOTS", raising=False)
    if jobs._default:
        jobs._default.shutdown()
    jobs._default = None
    yield
    if jobs._default:
        jobs._default.shutdown()
    jobs._default = None


def _register(client, path, *, source_id="qin647_source", key="register", auth=ADMIN, authorize_root=True):
    settings = client.get("/api/storage-settings", headers=auth)
    assert settings.status_code == 200, settings.text
    return client.post(
        BASE + "/reindex",
        headers={**auth, "Idempotency-Key": key},
        json={
            "background": True,
            "hash_files": True,
            "max_files": 250000,
            "roots": [source_id],
            "register_root": {
                "id": source_id,
                "name": "QIN-647 测试来源",
                "path": str(path),
                "expected_version": settings.json()["revision"],
                "authorize_root": authorize_root,
            },
        },
    )


def _wait(client, job_id, timeout=8):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        response = client.get(BASE + "/workspace-jobs/" + job_id, headers=ADMIN)
        assert response.status_code == 200, response.text
        job = response.json()["job"]
        if job["status"] in jobs.TERMINAL:
            return job
        time.sleep(0.02)
    pytest.fail("QIN-647 索引任务未按时终结")


def test_empty_allowed_roots_admin_authorize_root_succeeds(tmp_path):
    assert storage.allowed_roots() == []
    folder = tmp_path / "first-library"
    folder.mkdir()
    (folder / "clip.txt").write_text("空允许根原子登记素材", encoding="utf-8")

    with TestClient(create_app()) as client:
        accepted = _register(client, folder, key="empty-roots-admin-ok")
        assert accepted.status_code == 202, accepted.text
        job_id = accepted.json()["job_id"]
        completed = _wait(client, job_id)
        assert completed["status"] == "succeeded"
        assert completed["result"]["indexed"] == 1

    key = storage.directory_path_key(str(folder))
    assert any(
        item.get("id") == "qin647_source"
        and item.get("path")
        and storage.directory_path_key(str(item["path"])) == key
        for item in default_storage_settings_service.get_snapshot().local_libraries
    )
    assert any(
        item.source == "persistent"
        and item.active
        and storage.directory_path_key(item.path) == key
        for item in default_storage_allowed_roots_service.get_snapshot().roots
    )


def test_empty_allowed_roots_non_admin_forbidden(tmp_path):
    assert storage.allowed_roots() == []
    folder = tmp_path / "non-admin-library"
    folder.mkdir()

    with TestClient(create_app()) as client:
        resp = _register(client, folder, key="empty-roots-editor-deny", auth=EDITOR)
        assert resp.status_code == 403, resp.text
        assert resp.json()["detail"]["code"] == "FORBIDDEN"

    assert storage.allowed_roots() == []
    assert default_storage_settings_service.get_snapshot().local_libraries == []


def test_empty_allowed_roots_without_authorize_root_keeps_not_admitted(tmp_path):
    assert storage.allowed_roots() == []
    folder = tmp_path / "no-authorize-library"
    folder.mkdir()

    with TestClient(create_app()) as client:
        resp = _register(
            client,
            folder,
            key="empty-roots-no-authorize",
            auth=ADMIN,
            authorize_root=False,
        )
        assert resp.status_code == 403, resp.text
        assert resp.json()["detail"]["code"] == "LOCAL_FILE_ACCESS_NOT_ADMITTED"

    assert storage.allowed_roots() == []


@pytest.mark.parametrize("forbidden_path", ["/", "C:\\"])
def test_empty_allowed_roots_still_rejects_system_directories(forbidden_path):
    assert storage.allowed_roots() == []
    with TestClient(create_app()) as client:
        resp = _register(client, forbidden_path, key=f"sys-deny-{forbidden_path}")
        assert resp.status_code == 403, resp.text
        assert resp.json()["detail"]["code"] == "RESOURCE_FOLDER_NOT_PERMITTED"

    assert storage.allowed_roots() == []


def test_empty_allowed_roots_retry_also_admits_local_file_access(monkeypatch, tmp_path):
    assert storage.allowed_roots() == []
    folder = tmp_path / "retry-library"
    folder.mkdir()
    (folder / "retry.txt").write_text("重试路径空允许根登记", encoding="utf-8")

    original_scan = jobs.IndexJobs._scan

    def fail_first_scan(self, *args):
        raise RuntimeError("模拟首次扫描瞬时失败")

    monkeypatch.setattr(jobs.IndexJobs, "_scan", fail_first_scan)
    with TestClient(create_app()) as client:
        accepted = _register(client, folder, key="empty-roots-retry-1")
        assert accepted.status_code == 202, accepted.text
        job_id = accepted.json()["job_id"]
        failed = _wait(client, job_id)
        assert failed["status"] == "failed"
        assert storage.allowed_roots() == []
        assert default_storage_settings_service.get_snapshot().local_libraries == []

        monkeypatch.setattr(jobs.IndexJobs, "_scan", original_scan)
        retried = client.post(
            BASE + f"/workspace-jobs/{job_id}/retry",
            headers={**ADMIN, "Idempotency-Key": "empty-roots-retry-2"},
            json={"expected_version": failed["version"]},
        )
        assert retried.status_code == 200, retried.text
        completed = _wait(client, job_id)
        assert completed["status"] == "succeeded"
        assert completed["result"]["indexed"] == 1

    key = storage.directory_path_key(str(folder))
    assert any(
        item.get("id") == "qin647_source"
        and item.get("path")
        and storage.directory_path_key(str(item["path"])) == key
        for item in default_storage_settings_service.get_snapshot().local_libraries
    )
    assert any(
        item.source == "persistent"
        and item.active
        and storage.directory_path_key(item.path) == key
        for item in default_storage_allowed_roots_service.get_snapshot().roots
    )
