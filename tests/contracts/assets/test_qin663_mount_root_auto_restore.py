"""QIN-663 重新添加已移除挂载文件夹自动恢复允许根契约测试。"""

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
ADMIN = {"Authorization": "Bearer qin663-owner", "X-User-Role": "admin"}
EDITOR = {"Authorization": "Bearer qin663-owner", "X-User-Role": "editor"}


@pytest.fixture(autouse=True)
def isolated_mount_restore_runtime(monkeypatch):
    monkeypatch.setenv("GW_AUTH_MODE", "local")
    monkeypatch.delenv("GW_ALLOWED_ROOTS", raising=False)
    if jobs._default:
        jobs._default.shutdown()
    jobs._default = None
    yield
    if jobs._default:
        jobs._default.shutdown()
    jobs._default = None


def _register(
    client,
    path,
    *,
    source_id="qin663_source",
    name="QIN-663 测试来源",
    key="register",
    auth=ADMIN,
    authorize_root=True,
):
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
                "name": name,
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
    pytest.fail("QIN-663 索引任务未按时终结")


def _remove_mount_root(client, folder_path):
    roots_resp = client.get("/api/storage-settings/roots", headers=ADMIN)
    assert roots_resp.status_code == 200, roots_resp.text
    payload = roots_resp.json()
    target_key = storage.directory_path_key(str(folder_path))
    target_root = next(
        (
            item for item in payload["roots"]
            if item.get("active")
            and item.get("path")
            and storage.directory_path_key(str(item["path"])) == target_key
        ),
        None,
    )
    assert target_root is not None, payload
    del_resp = client.request(
        "DELETE",
        f"/api/storage-settings/roots/{target_root['id']}",
        headers=ADMIN,
        json={"expected_version": payload["revision"]},
    )
    assert del_resp.status_code == 200, del_resp.text
    return target_root["id"]


def test_readd_removed_mount_root_auto_restores_and_indexes(tmp_path):
    folder = tmp_path / "vault-folder"
    folder.mkdir()
    (folder / "clip.txt").write_text("重新添加自动恢复允许根", encoding="utf-8")

    with TestClient(create_app()) as client:
        first = _register(client, folder, source_id="qin663_first", key="qin663-add-1")
        assert first.status_code == 202, first.text
        assert _wait(client, first.json()["job_id"])["status"] == "succeeded"

        root_id = _remove_mount_root(client, folder)
        assert not any(
            item.active and item.id == root_id
            for item in default_storage_allowed_roots_service.get_snapshot().roots
        )
        assert not any(
            item.get("path")
            for item in default_storage_settings_service.get_snapshot().local_libraries
        )
        (folder / "clip2.txt").write_text("重新添加后新增素材", encoding="utf-8")

        readded = _register(
            client,
            folder,
            source_id="qin663_readded",
            key="qin663-add-2",
            authorize_root=True,
        )
        assert readded.status_code == 202, readded.text
        completed = _wait(client, readded.json()["job_id"])
        assert completed["status"] == "succeeded"
        assert completed["result"]["indexed"] == 1

    key = storage.directory_path_key(str(folder))
    assert any(
        item.id == root_id and item.active and storage.directory_path_key(item.path) == key
        for item in default_storage_allowed_roots_service.get_snapshot().roots
    )
    assert any(
        item.get("path") and storage.directory_path_key(str(item["path"])) == key
        for item in default_storage_settings_service.get_snapshot().local_libraries
    )


def test_readd_removed_mount_subdirectory_auto_restores_parent_root(tmp_path):
    parent = tmp_path / "parent-vault"
    sub = parent / "sub-folder"
    sub.mkdir(parents=True)
    (sub / "shot.txt").write_text("子目录重新添加恢复父允许根", encoding="utf-8")

    with TestClient(create_app()) as client:
        first = _register(client, parent, source_id="qin663_parent", key="qin663-sub-1")
        assert first.status_code == 202, first.text
        assert _wait(client, first.json()["job_id"])["status"] == "succeeded"

        root_id = _remove_mount_root(client, parent)
        (sub / "new-shot.txt").write_text("子目录新增素材", encoding="utf-8")

        readded = _register(
            client,
            sub,
            source_id="qin663_sub",
            name="QIN-663 子目录",
            key="qin663-sub-2",
            authorize_root=True,
        )
        assert readded.status_code == 202, readded.text
        completed = _wait(client, readded.json()["job_id"])
        assert completed["status"] == "succeeded"
        assert completed["result"]["indexed"] == 1

    assert any(
        item.id == root_id and item.active
        for item in default_storage_allowed_roots_service.get_snapshot().roots
    )
    sub_key = storage.directory_path_key(str(sub))
    assert any(
        item.get("id") == "qin663_sub"
        and item.get("path")
        and storage.directory_path_key(str(item["path"])) == sub_key
        for item in default_storage_settings_service.get_snapshot().local_libraries
    )


def test_readd_removed_mount_root_non_admin_forbidden(tmp_path):
    folder = tmp_path / "non-admin-removed"
    folder.mkdir()
    (folder / "item.txt").write_text("非管理员禁止恢复", encoding="utf-8")

    with TestClient(create_app()) as client:
        first = _register(client, folder, key="qin663-nonadmin-1")
        assert first.status_code == 202, first.text
        assert _wait(client, first.json()["job_id"])["status"] == "succeeded"

        root_id = _remove_mount_root(client, folder)

        denied = _register(
            client,
            folder,
            source_id="qin663_editor_try",
            key="qin663-nonadmin-2",
            auth=EDITOR,
            authorize_root=True,
        )
        assert denied.status_code == 403, denied.text
        assert denied.json()["detail"]["code"] == "FORBIDDEN"

    assert any(
        item.id == root_id and not item.active
        for item in default_storage_allowed_roots_service.get_snapshot().roots
    )


def test_readd_removed_mount_root_without_authorize_root_denied(tmp_path):
    folder = tmp_path / "no-auth-removed"
    folder.mkdir()

    with TestClient(create_app()) as client:
        first = _register(client, folder, key="qin663-noauth-1")
        assert first.status_code == 202, first.text
        assert _wait(client, first.json()["job_id"])["status"] == "succeeded"

        root_id = _remove_mount_root(client, folder)

        denied = _register(
            client,
            folder,
            source_id="qin663_noauth_try",
            key="qin663-noauth-2",
            auth=ADMIN,
            authorize_root=False,
        )
        assert denied.status_code == 403, denied.text
        assert denied.json()["detail"]["code"] in {
            "PATH_OUTSIDE_ALLOWED_ROOTS",
            "LOCAL_FILE_ACCESS_NOT_ADMITTED",
        }

    assert any(
        item.id == root_id and not item.active
        for item in default_storage_allowed_roots_service.get_snapshot().roots
    )


@pytest.mark.parametrize("forbidden_path", ["/", "C:\\"])
def test_readd_system_directory_still_rejected(forbidden_path):
    with TestClient(create_app()) as client:
        resp = _register(client, forbidden_path, key=f"qin663-sys-{forbidden_path}")
        assert resp.status_code == 403, resp.text
        assert resp.json()["detail"]["code"] == "RESOURCE_FOLDER_NOT_PERMITTED"


def test_readd_removed_mount_root_retry_auto_restores(monkeypatch, tmp_path):
    folder = tmp_path / "retry-removed"
    folder.mkdir()
    (folder / "retry.txt").write_text("重试路径自动恢复允许根", encoding="utf-8")

    with TestClient(create_app()) as client:
        # 先建立一个允许根并移出
        seed = _register(client, folder, source_id="qin663_seed", key="qin663-retry-seed")
        assert seed.status_code == 202, seed.text
        assert _wait(client, seed.json()["job_id"])["status"] == "succeeded"
        root_id = _remove_mount_root(client, folder)

        # 移出后重新添加子目录，首次扫描失败，随后 retry 应可继续完成索引
        sub = folder / "sub"
        sub.mkdir()
        (sub / "sub.txt").write_text("子目录重试", encoding="utf-8")

        original_scan = jobs.IndexJobs._scan

        def fail_first_scan(self, *args):
            raise RuntimeError("模拟首次扫描瞬时失败")

        monkeypatch.setattr(jobs.IndexJobs, "_scan", fail_first_scan)
        accepted = _register(client, sub, source_id="qin663_retry_sub", key="qin663-retry-1")
        assert accepted.status_code == 202, accepted.text
        job_id = accepted.json()["job_id"]
        failed = _wait(client, job_id)
        assert failed["status"] == "failed"

        monkeypatch.setattr(jobs.IndexJobs, "_scan", original_scan)
        retried = client.post(
            BASE + f"/workspace-jobs/{job_id}/retry",
            headers={**ADMIN, "Idempotency-Key": "qin663-retry-2"},
            json={"expected_version": failed["version"]},
        )
        assert retried.status_code == 200, retried.text
        completed = _wait(client, job_id)
        assert completed["status"] == "succeeded"
        assert completed["result"]["indexed"] == 1

    assert any(
        item.id == root_id and item.active
        for item in default_storage_allowed_roots_service.get_snapshot().roots
    )
