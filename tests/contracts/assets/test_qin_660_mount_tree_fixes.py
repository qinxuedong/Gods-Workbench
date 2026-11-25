"""QIN-660（#822）左侧挂载目录过滤、递归计数与根身份解析契约测试。"""

import json
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from gw.api.app import create_app
from gw.asset_registry import index_jobs as jobs
from gw.asset_registry import repository as registry_repo
from gw.core import storage
from gw.settings.service import default_storage_settings_service

ADMIN = {"Authorization": "Bearer qin660-owner", "X-User-Role": "admin"}


@pytest.fixture(autouse=True)
def isolated_runtime(monkeypatch):
    monkeypatch.setenv("GW_AUTH_MODE", "local")
    if jobs._default:
        jobs._default.shutdown()
    jobs._default = None
    yield
    if jobs._default:
        jobs._default.shutdown()
    jobs._default = None


def test_browse_directories_excludes_system_and_dynamic_cache_dirs(tmp_path: Path):
    """AC2：目录浏览接口按目录段精确排除系统目录与动态 cache_folder，不误伤普通目录。"""
    root = tmp_path / "mount_root"
    (root / "god-recycler").mkdir(parents=True)
    (root / "god-cache").mkdir(parents=True)
    (root / ".gods-workbench").mkdir(parents=True)
    (root / "custom-thumb-cache").mkdir(parents=True)
    (root / "my-god-cache-backup").mkdir(parents=True)
    (root / "god-recycler-demo").mkdir(parents=True)
    (root / "audio").mkdir(parents=True)

    data_root = storage.data_root()
    data_root.mkdir(parents=True, exist_ok=True)
    (data_root / "media_settings.json").write_text(
        json.dumps({"thumbnails": {"cache_folder": "custom-thumb-cache"}}),
        encoding="utf-8",
    )

    result = default_storage_settings_service.browse_directories(str(root))
    names = {item["name"] for item in result["directories"]}

    assert "audio" in names
    assert "my-god-cache-backup" in names
    assert "god-recycler-demo" in names
    assert "god-recycler" not in names
    assert "god-cache" not in names
    assert ".gods-workbench" not in names
    assert "custom-thumb-cache" not in names


def test_folders_recursive_counts_for_empty_intermediate_ancestors(tmp_path: Path, monkeypatch):
    """AC2 & AC3：folders() 排除系统目录并为纯中间祖先目录输出准确递归 total_count。"""
    mount = tmp_path / "media_mount"
    mount.mkdir(parents=True)
    monkeypatch.setenv("GW_ALLOWED_ROOTS", str(mount))
    physical_id = storage.root_identity_id(mount)

    def _seed(raw):
        raw["assets"]["ast_qin660_1"] = {
            "asset_id": "ast_qin660_1",
            "name": "a1.wav",
            "kind": "audio",
            "storage_root_id": physical_id,
            "root_id": physical_id,
            "relative_path": "ep01/shots/sh001/a1.wav",
            "archived": False,
        }
        raw["assets"]["ast_qin660_2"] = {
            "asset_id": "ast_qin660_2",
            "name": "a2.png",
            "kind": "image",
            "storage_root_id": physical_id,
            "root_id": physical_id,
            "relative_path": "ep01/shots/sh002/a2.png",
            "archived": False,
        }
        raw["assets"]["ast_qin660_sys"] = {
            "asset_id": "ast_qin660_sys",
            "name": "trashed.png",
            "kind": "image",
            "storage_root_id": physical_id,
            "root_id": physical_id,
            "relative_path": "god-recycler/trashed.png",
            "archived": False,
        }
        return raw

    registry_repo.state().mutate(_seed)

    payload = registry_repo.folders(physical_id)
    by_path = {entry["path"]: entry for entry in payload["indexed_folders"]}
    totals_by_path = {entry["path"]: entry for entry in payload["folder_totals"]}

    assert "god-recycler" not in by_path
    assert "god-recycler" not in totals_by_path
    assert totals_by_path["ep01"]["total_count"] == 2
    assert totals_by_path["ep01/shots"]["total_count"] == 2
    assert by_path["ep01/shots/sh001"]["count"] == 1
    assert by_path["ep01/shots/sh001"]["total_count"] == 1


def test_rename_and_delete_folder_accepts_storage_root_id(tmp_path: Path, monkeypatch):
    """AC5：重命名与删除子文件夹传入 storage_root_id 时映射为物理 root_id，不报 ROOT_NOT_FOUND。"""
    sys_root = tmp_path / "sys_root"
    sys_root.mkdir(parents=True)
    mount = tmp_path / "mount_lib"
    sub = mount / "audio_clips"
    sub.mkdir(parents=True)
    monkeypatch.setenv("GW_ALLOWED_ROOTS", str(sys_root))
    physical_mount_id = storage.root_identity_id(mount)
    data_dir = storage.data_root()
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "allowed_roots.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "revision": 1,
                "roots": [
                    {
                        "id": physical_mount_id,
                        "path": str(mount),
                        "aliases": [],
                        "active": True,
                    }
                ],
                "system_aliases": {},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    storage._ALLOWED_ROOTS_STATE_CACHE = None
    storage._ALLOWED_ROOTS_STATE_PATH_CACHE = None

    app = create_app()
    with TestClient(app) as client:
        assert client.get("/api/asset-registry/assets", headers=ADMIN).status_code == 200
        settings_resp = client.get("/api/storage-settings", headers=ADMIN)
        assert settings_resp.status_code == 200
        rev = settings_resp.json()["revision"]

        storage_root_id = "lib_custom_qin660"
        patch_resp = client.patch(
            "/api/storage-settings",
            headers=ADMIN,
            json={
                "expected_version": rev,
                "extra_local_entries": [
                    {
                        "id": storage_root_id,
                        "name": "自定义来源",
                        "path": str(mount),
                    }
                ],
            },
        )
        assert patch_resp.status_code == 200, patch_resp.text

        reindex_resp = client.post(
            "/api/asset-registry/reindex",
            headers={**ADMIN, "Idempotency-Key": "qin660-reindex"},
            json={"background": True, "roots": [storage_root_id]},
        )
        assert reindex_resp.status_code in (200, 202), reindex_resp.text
        job_id = reindex_resp.json().get("job_id")
        if job_id:
            deadline = time.monotonic() + 8
            while time.monotonic() < deadline:
                jr = client.get(f"/api/asset-registry/workspace-jobs/{job_id}", headers=ADMIN)
                if jr.status_code == 200 and jr.json().get("status") in {"completed", "failed"}:
                    break
                time.sleep(0.05)

        ver_resp = client.get("/api/local-assets/organize/version", headers=ADMIN)
        assert ver_resp.status_code == 200, ver_resp.text
        org_ver = ver_resp.json()["version"]

        # 传入 storage_root_id 进行重命名
        rename_resp = client.patch(
            "/api/local-assets/folders",
            headers=ADMIN,
            json={
                "path": str(sub),
                "name": "audio_renamed",
                "root_id": storage_root_id,
                "expected_version": org_ver,
            },
        )
        assert rename_resp.status_code == 200, rename_resp.text
        renamed_path = mount / "audio_renamed"
        assert renamed_path.is_dir()

        # 传入 storage_root_id 进行删除
        org_ver2 = rename_resp.json().get("version", org_ver + 1)
        delete_resp = client.post(
            "/api/local-assets/folders/delete",
            headers=ADMIN,
            json={
                "path": str(renamed_path),
                "root_id": storage_root_id,
                "expected_version": org_ver2,
            },
        )
        assert delete_resp.status_code == 200, delete_resp.text
        assert not renamed_path.exists()

        # 未知根 ID 仍保持 fail-closed 404 ROOT_NOT_FOUND
        bad_resp = client.post(
            "/api/local-assets/folders/delete",
            headers=ADMIN,
            json={
                "path": str(mount / "missing"),
                "root_id": "nonexistent_root_id_qin660",
                "expected_version": delete_resp.json().get("version", org_ver2 + 1),
            },
        )
        assert bad_resp.status_code == 404
        assert bad_resp.json()["detail"]["code"] == "ROOT_NOT_FOUND"
