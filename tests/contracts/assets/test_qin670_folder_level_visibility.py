"""QIN-670 文件夹粒度素材显隐、祖先继承、检索过滤与持久化兼容合同。"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from gw.api import routes_settings
from gw.api.app import create_app
from gw.asset_registry import index_jobs, repository
from gw.core import storage
from gw.core.sqlite_vec_engine import SQLiteVecEngine
from gw.settings import service as settings_service
from gw.settings.models import StorageSettingsPatchRequest
from gw.settings.service import StorageSettingsService

EDITOR = {"Authorization": "Bearer cleanroom-test", "X-User-Role": "editor"}
VIEWER = {"Authorization": "Bearer cleanroom-test", "X-User-Role": "viewer"}
ADMIN = {"Authorization": "Bearer cleanroom-test", "X-User-Role": "admin"}


@pytest.fixture()
def folder_visibility_runtime(monkeypatch, tmp_path: Path):
    root = tmp_path / "mounted"
    shots = root / "shots"
    sc01 = shots / "sc01"
    other = root / "other"
    sibling = tmp_path / "mounted-sibling"
    for path in (root, shots, sc01, other, sibling):
        path.mkdir(parents=True)
    monkeypatch.setenv("GW_ALLOWED_ROOTS", f"{root}{os.pathsep}{sibling}")
    service = StorageSettingsService()
    monkeypatch.setattr(settings_service, "default_storage_settings_service", service)
    monkeypatch.setattr(routes_settings, "default_storage_settings_service", service)
    storage._ALLOWED_ROOTS_STATE_CACHE = None
    storage._ALLOWED_ROOTS_STATE_PATH_CACHE = None
    index_jobs.invalidate_root_facets_cache()
    with TestClient(create_app()) as client:
        yield client, service, root, shots, sc01, other, sibling
    index_jobs.invalidate_root_facets_cache()


def _seed_folder_assets(root: Path, sibling: Path) -> tuple[str, str]:
    root_id = storage.root_identity_id(root)
    sibling_id = storage.root_identity_id(sibling)

    def seed(raw):
        records = (
            ("ast_qin670_shots_root", root_id, "shots/clip_parent.mp4"),
            ("ast_qin670_sc01", root_id, "shots/sc01/shot_001.mp4"),
            ("ast_qin670_other", root_id, "other/keep_me.mp4"),
            ("ast_qin670_sibling", sibling_id, "shots/sibling_keep.mp4"),
        )
        for asset_id, rid, relpath in records:
            raw["assets"][asset_id] = {
                "asset_id": asset_id,
                "name": f"needle670-{asset_id}.mp4",
                "kind": "video",
                "root_id": rid,
                "storage_root_id": rid,
                "canonical_relpath": relpath,
                "relative_path": relpath,
                "display_path": relpath,
                "archived": False,
                "is_remote": False,
                "tags": ["needle670"],
                "metadata": {"video_segments": []},
            }
        return raw

    repository.state().mutate(seed)
    return root_id, sibling_id


def test_folder_level_visibility_inheritance_and_persistence(folder_visibility_runtime):
    client, service, root, shots, sc01, other, sibling = folder_visibility_runtime
    root_id, sibling_id = _seed_folder_assets(root, sibling)

    # 1) 非法路径（.. / 绝对路径）与无权限角色均被拒绝
    bad_rel = client.patch(
        "/api/storage-settings",
        headers=EDITOR,
        json={
            "hidden_asset_root_id": root_id,
            "hidden_asset_folder_path": "../outside",
            "hidden_asset_hidden": True,
            "expected_version": 1,
        },
    )
    assert bad_rel.status_code == 400
    assert bad_rel.json()["detail"]["code"] == "INVALID_PATH"

    denied = client.patch(
        "/api/storage-settings",
        headers=VIEWER,
        json={
            "hidden_asset_root_id": root_id,
            "hidden_asset_folder_path": "shots/sc01",
            "hidden_asset_hidden": True,
            "expected_version": 1,
        },
    )
    assert denied.status_code == 403

    # 2) 先独立隐藏子目录 shots/sc01
    hide_sc01 = client.patch(
        "/api/storage-settings",
        headers=EDITOR,
        json={
            "hidden_asset_root_id": root_id,
            "hidden_asset_folder_path": "shots/sc01",
            "hidden_asset_hidden": True,
            "expected_version": 1,
        },
    )
    assert hide_sc01.status_code == 200, hide_sc01.text
    assert hide_sc01.json()["hidden_asset_root_keys"] == [storage.directory_path_key(str(sc01))]

    # 仅 shots/sc01 被隐藏，shots 直接文件、other、sibling 仍可见
    visible_ids = {item["asset_id"] for item in client.get("/api/asset-registry/assets", headers=EDITOR).json()["items"]}
    assert visible_ids == {"ast_qin670_shots_root", "ast_qin670_other", "ast_qin670_sibling"}

    # 3) 再隐藏父目录 shots：递归覆盖 shots 与 shots/sc01
    hide_shots = client.patch(
        "/api/storage-settings",
        headers=EDITOR,
        json={
            "hidden_asset_root_id": root_id,
            "hidden_asset_folder_path": "shots",
            "hidden_asset_hidden": True,
            "expected_version": 2,
        },
    )
    assert hide_shots.status_code == 200, hide_shots.text
    visible_ids = {item["asset_id"] for item in client.get("/api/asset-registry/assets", headers=EDITOR).json()["items"]}
    assert visible_ids == {"ast_qin670_other", "ast_qin670_sibling"}

    # 4) AC3：父级 shots 隐藏时，子级 shots/sc01 不能单独恢复显示（409 FOLDER_ANCESTOR_HIDDEN）
    blocked_unhide = client.patch(
        "/api/storage-settings",
        headers=EDITOR,
        json={
            "hidden_asset_root_id": root_id,
            "hidden_asset_folder_path": "shots/sc01",
            "hidden_asset_hidden": False,
            "expected_version": 3,
        },
    )
    assert blocked_unhide.status_code == 409
    assert blocked_unhide.json()["detail"]["code"] == "FOLDER_ANCESTOR_HIDDEN"

    # 5) AC5：folders() 仍保留真实 indexed_folders 与 folder_totals，同时返回 self_hidden_paths
    folders_resp = client.get(f"/api/asset-registry/folders?root_id={root_id}", headers=EDITOR)
    assert folders_resp.status_code == 200, folders_resp.text
    folders_data = folders_resp.json()
    totals_map = {entry["path"]: entry["total_count"] for entry in folders_data["folder_totals"]}
    assert totals_map["shots"] == 2
    assert totals_map["shots/sc01"] == 1
    assert totals_map["other"] == 1
    assert "shots" in [p.lower() for p in folders_data["self_hidden_paths"]]
    assert "shots/sc01" in [p.lower() for p in folders_data["self_hidden_paths"]]
    assert folders_data["root_assets_hidden"] is False

    # 6) AC3 & AC6：恢复父级 shots 后，子级 shots/sc01 因自身仍隐藏而继续保持隐藏；冷读新实例一致
    unhide_shots = client.patch(
        "/api/storage-settings",
        headers=EDITOR,
        json={
            "hidden_asset_root_id": root_id,
            "hidden_asset_folder_path": "shots",
            "hidden_asset_hidden": False,
            "expected_version": 3,
        },
    )
    assert unhide_shots.status_code == 200, unhide_shots.text
    cold_snapshot = StorageSettingsService().get_snapshot()
    assert cold_snapshot.hidden_asset_root_keys == [storage.directory_path_key(str(sc01))]

    visible_ids = {item["asset_id"] for item in client.get("/api/asset-registry/assets", headers=EDITOR).json()["items"]}
    assert visible_ids == {"ast_qin670_shots_root", "ast_qin670_other", "ast_qin670_sibling"}

    # 7) 仅当子级 shots/sc01 也恢复显示后，才重新可见（且无需重索引）
    unhide_sc01 = client.patch(
        "/api/storage-settings",
        headers=EDITOR,
        json={
            "hidden_asset_root_id": root_id,
            "hidden_asset_folder_path": "shots/sc01",
            "hidden_asset_hidden": False,
            "expected_version": 4,
        },
    )
    assert unhide_sc01.status_code == 200, unhide_sc01.text
    assert unhide_sc01.json()["hidden_asset_root_keys"] == []
    visible_ids = {item["asset_id"] for item in client.get("/api/asset-registry/assets", headers=EDITOR).json()["items"]}
    assert visible_ids == {
        "ast_qin670_shots_root",
        "ast_qin670_sc01",
        "ast_qin670_other",
        "ast_qin670_sibling",
    }


def test_folder_visibility_shots_search_and_purge_cleanup(folder_visibility_runtime, monkeypatch):
    client, service, root, shots, sc01, other, sibling = folder_visibility_runtime
    root_id, sibling_id = _seed_folder_assets(root, sibling)

    # 隐藏 mounted/shots，同时在 sibling 上也隐藏一个目录验证 purge 不误伤
    saved = client.patch(
        "/api/storage-settings",
        headers=EDITOR,
        json={
            "hidden_asset_root_id": root_id,
            "hidden_asset_folder_path": "shots",
            "hidden_asset_hidden": True,
            "expected_version": 1,
        },
    )
    assert saved.status_code == 200

    # /search/shots 必须在召回与 limit=1 截断前剔除隐藏子文件夹下的资产
    db_path = storage.data_root() / repository.ASSET_SEARCH_INDEX_FILENAME
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with SQLiteVecEngine(db_path) as engine:
        engine.batch_upsert_documents(
            [
                {"asset_id": "ast_qin670_sc01", "title": "sc01", "content": "needle670"},
                {"asset_id": "ast_qin670_shots_root", "title": "shots", "content": "needle670"},
                {"asset_id": "ast_qin670_other", "title": "other", "content": "needle670"},
            ]
        )
    shot_search = client.get(
        "/api/asset-registry/search/shots",
        headers=EDITOR,
        params={"q": "needle670", "limit": 1},
    )
    assert shot_search.status_code == 200, shot_search.text
    assert [item["asset_id"] for item in shot_search.json()["items"]] == ["ast_qin670_other"]

    # 补写一个同前缀兄弟根 sibling 的子目录隐藏键 + root 下多层子目录键，验证 purge_root 精准清理
    sibling_key = storage.directory_path_key(str(sibling / "shots"))
    sc01_key = storage.directory_path_key(str(sc01))
    service.patch(
        StorageSettingsPatchRequest(
            hidden_asset_root_keys=[
                storage.directory_path_key(str(shots)),
                sc01_key,
                sibling_key,
            ],
            expected_version=2,
        )
    )
    service.purge_entries({root_id}, [str(root)])
    remaining = StorageSettingsService().get_snapshot().hidden_asset_root_keys
    assert remaining == [sibling_key]
