# -*- coding: utf-8 -*-
"""QIN-598 资源文件夹移出 / 清除索引 / 注销来源统一级联契约测试。"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from gw.api import routes_settings as routes
from gw.api.app import create_app
from gw.asset_registry import index_jobs, repository as asset_repo
from gw.core import storage
from gw.core.errors import CleanroomException
from gw.media import repository as media_repo
from gw.settings import service as settings_service
from gw.settings.service import (
    AssetStructureService,
    ProviderService,
    StorageAllowedRootsService,
    StorageSettingsService,
)

ADMIN = {"X-User-Role": "admin", "Authorization": "Bearer cleanroom-test"}


def _sha256_tree(root: Path) -> dict[str, str]:
    hashes: dict[str, str] = {}
    for file_path in sorted(p for p in root.rglob("*") if p.is_file()):
        hashes[str(file_path.relative_to(root))] = hashlib.sha256(
            file_path.read_bytes()
        ).hexdigest()
    return hashes


@pytest.fixture()
def client(monkeypatch, tmp_path: Path) -> TestClient:
    monkeypatch.setenv("GW_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("GW_ALLOWED_ROOTS", str(tmp_path / "sys_root"))
    (tmp_path / "sys_root").mkdir(parents=True, exist_ok=True)
    storage_svc = StorageSettingsService()
    roots_svc = StorageAllowedRootsService()
    monkeypatch.setattr(settings_service, "default_storage_settings_service", storage_svc)
    monkeypatch.setattr(settings_service, "default_storage_allowed_roots_service", roots_svc)
    monkeypatch.setattr(routes, "default_storage_settings_service", storage_svc)
    monkeypatch.setattr(routes, "default_storage_allowed_roots_service", roots_svc)
    monkeypatch.setattr(routes, "default_provider_service", ProviderService())
    monkeypatch.setattr(
        routes, "default_asset_structure_service", AssetStructureService()
    )
    with TestClient(create_app()) as test_client:
        yield test_client


def _register_folder_and_assets(
    client: TestClient, folder: Path, source_id: str = "local_extra_alpha"
) -> tuple[str, list[str]]:
    folder.mkdir(parents=True, exist_ok=True)
    files = [
        folder / "shot_a.png",
        folder / "sub" / "clip_b.mp4",
    ]
    files[1].parent.mkdir(parents=True, exist_ok=True)
    files[0].write_bytes(b"PNGDATA-ALPHA-001")
    files[1].write_bytes(b"MP4DATA-BETA-002")

    roots_rev = client.get("/api/storage-settings/roots", headers=ADMIN).json()["revision"]
    root_resp = client.post(
        "/api/storage-settings/roots",
        headers=ADMIN,
        json={"path": str(folder), "expected_version": roots_rev},
    )
    assert root_resp.status_code == 200, root_resp.text
    root_id = next(
        item["id"]
        for item in root_resp.json()["roots"]
        if Path(item["path"]) == folder
    )

    storage_resp = client.get("/api/storage-settings", headers=ADMIN)
    assert storage_resp.status_code == 200
    rev = storage_resp.json()["revision"]
    put_resp = client.patch(
        "/api/storage-settings",
        headers=ADMIN,
        json={
            "dirs": storage_resp.json()["dirs"],
            "extra_local_entries": [{"id": source_id, "path": str(folder), "name": "Alpha"}],
            "local_library_names": {source_id: "Alpha"},
            "local_library_order": [source_id],
            "expected_version": rev,
        },
    )
    assert put_resp.status_code == 200, put_resp.text

    asset_ids = ["ast_q598_root_only", "ast_q598_source_only"]

    def _seed_raw(raw: dict) -> None:
        raw["assets"].update(
            {
                "ast_q598_root_only": asset_repo._plain_asset(
                    "ast_q598_root_only",
                    "shot_a.png",
                    "image",
                    root_id=root_id,
                    storage_root_id=root_id,
                    canonical_relpath="shot_a.png",
                    display_path="shot_a.png",
                ),
                "ast_q598_source_only": asset_repo._plain_asset(
                    "ast_q598_source_only",
                    "clip_b.mp4",
                    "video",
                    root_id=root_id,
                    storage_root_id=source_id,
                    canonical_relpath="sub/clip_b.mp4",
                    display_path="sub/clip_b.mp4",
                ),
            }
        )
        raw.setdefault("root_aliases", {})[source_id] = {
            "root_id": root_id,
            "aliases": [str(folder)],
        }
        raw.setdefault("folders", {})[f"{source_id}:sub"] = {
            "parent_id": source_id,
            "folder": "sub",
        }
        raw.setdefault("relations", []).append(
            {"from_asset_id": "ast_q598_root_only", "to_asset_id": "ast_q598_source_only"}
        )
        raw.setdefault("identity_repair_queue", []).append("ast_q598_source_only")

    asset_repo.state().mutate(_seed_raw)
    return root_id, asset_ids


def test_ac1_ac4_purge_clears_double_key_search_caches_and_keeps_source_files(
    client: TestClient, tmp_path: Path, monkeypatch
):
    """AC1/AC4：purge 双键清理注册表、搜索索引与缩略图/分镜缓存，源文件 SHA-256 零改动。"""
    folder = tmp_path / "vault_purge"
    root_id, asset_ids = _register_folder_and_assets(client, folder)
    before_hashes = _sha256_tree(folder)

    # 写入真实 SQLite 检索文档与模拟缩略图/分镜缓存删除追踪
    asset_repo.rebuild_search_index()
    assert asset_repo.search_index_status()["documents"] == len(asset_ids)
    deleted_cache_calls: list[tuple[str, dict]] = []
    monkeypatch.setattr(
        media_repo,
        "delete_outputs",
        lambda kind, payload: deleted_cache_calls.append((kind, dict(payload))) or {"deleted": payload.get("asset_ids", []), "failed": []},
    )

    resp = client.request(
        "DELETE",
        f"/api/storage-settings/roots/{root_id}/purge",
        headers=ADMIN,
        json={"expected_version": 2},
    )
    assert resp.status_code == 200, resp.text
    assert all(r["id"] != root_id for r in resp.json()["roots"])

    raw = asset_repo.state().read()
    assert "ast_q598_root_only" not in raw["assets"]
    assert "ast_q598_source_only" not in raw["assets"]
    assert "local_extra_alpha" not in raw.get("root_aliases", {})
    assert not any(k.startswith("local_extra_alpha:") for k in raw.get("folders", {}))
    assert not raw.get("relations")
    assert "ast_q598_source_only" not in raw.get("identity_repair_queue", {})
    assert not raw.get("purging_roots")

    # 校验双类缓存都按全部候选 asset_ids 调用清理
    assert {kind for kind, _ in deleted_cache_calls} == {"thumbnails", "storyboards"}
    for _, payload in deleted_cache_calls:
        assert set(payload["asset_ids"]) == set(asset_ids)

    # 校验 SQLite 搜索文档已清零
    assert asset_repo.search_index_status()["documents"] == 0

    # 校验 storage_settings 中的额外来源和已移除记录一并清理
    sdata = client.get("/api/storage-settings", headers=ADMIN).json()
    assert not any(e["id"] == "local_extra_alpha" for e in sdata.get("extra_local_entries", []))
    assert not any(e["id"] == "local_extra_alpha" for e in sdata.get("removed_extra_local_entries", []))

    # AC4：原始磁盘文件与 SHA-256 完全不变
    assert _sha256_tree(folder) == before_hashes


def test_ac1_purge_precheck_cas_before_writes_and_crash_replay(
    client: TestClient, tmp_path: Path, monkeypatch
):
    """AC1：CAS 预检在写墓碑/清缓存前拦截；缓存失败保留墓碑与资产并可重放。"""
    folder = tmp_path / "vault_crash"
    root_id, asset_ids = _register_folder_and_assets(client, folder, source_id="local_extra_crash")
    before_hashes = _sha256_tree(folder)

    # 1. Step 0 CAS 冲突不得触发任何 cache 删除或墓碑写入
    called: list[str] = []
    monkeypatch.setattr(
        media_repo,
        "delete_outputs",
        lambda kind, payload: called.append(kind) or {"deleted": [], "failed": []},
    )
    conflict = client.request(
        "DELETE",
        f"/api/storage-settings/roots/{root_id}/purge",
        headers=ADMIN,
        json={"expected_version": 999},
    )
    assert conflict.status_code == 409
    assert conflict.json()["detail"]["code"] == "VERSION_CONFLICT"
    assert called == []
    assert asset_repo.purging_root_markers() == {}

    # 2. Step 2 缓存清理失败：写墓碑但保留 assets，snapshot 暴露 PURGE_INCOMPLETE，恢复被拒
    def fail_thumbs(kind: str, payload: dict):
        if kind == "thumbnails":
            return {"deleted": [], "failed": payload.get("asset_ids", [])}
        return {"deleted": payload.get("asset_ids", []), "failed": []}

    monkeypatch.setattr(media_repo, "delete_outputs", fail_thumbs)
    fail_resp = client.request(
        "DELETE",
        f"/api/storage-settings/roots/{root_id}/purge",
        headers=ADMIN,
        json={"expected_version": 2},
    )
    assert fail_resp.status_code == 503
    assert fail_resp.json()["detail"]["code"] == "CACHE_DELETE_FAILED"
    assert root_id in asset_repo.purging_root_markers()
    assert set(asset_ids).issubset(set(asset_repo.state().read()["assets"].keys()))

    snap = client.get("/api/storage-settings/roots", headers=ADMIN).json()
    target_row = next(r for r in snap["roots"] if r["id"] == root_id)
    assert target_row["active"] is False
    assert target_row["availability_reason"] == "PURGE_INCOMPLETE"

    readd = client.post(
        "/api/storage-settings/roots",
        headers=ADMIN,
        json={"path": str(folder), "expected_version": snap["revision"]},
    )
    assert readd.status_code == 409
    assert readd.json()["detail"]["code"] == "PURGE_IN_PROGRESS"

    # 3. 修复故障后重放 purge 成功并清理墓碑
    monkeypatch.setattr(
        media_repo,
        "delete_outputs",
        lambda kind, payload: {"deleted": payload.get("asset_ids", []), "failed": []},
    )
    replay = client.request(
        "DELETE",
        f"/api/storage-settings/roots/{root_id}/purge",
        headers=ADMIN,
        json={"expected_version": snap["revision"]},
    )
    assert replay.status_code == 200, replay.text
    assert root_id not in asset_repo.purging_root_markers()
    assert not any(aid in asset_repo.state().read()["assets"] for aid in asset_ids)
    assert _sha256_tree(folder) == before_hashes


def test_ac2_remove_and_restore_dual_sync_rollback_and_reindex(
    client: TestClient, tmp_path: Path, monkeypatch
):
    """AC2/AC4：remove/restore 同步两份配置并在失败时补偿回滚；restore 触发 reindex。"""
    folder = tmp_path / "vault_sync"
    root_id, _ = _register_folder_and_assets(client, folder, source_id="local_extra_sync")
    before_hashes = _sha256_tree(folder)

    # 1. Step 2 失败时补偿回滚 allowed_roots
    orig_move = StorageSettingsService.move_entries_to_removed
    monkeypatch.setattr(
        routes.default_storage_settings_service,
        "move_entries_to_removed",
        lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    with pytest.raises(RuntimeError, match="boom"):
        client.request(
            "DELETE",
            f"/api/storage-settings/roots/{root_id}",
            headers=ADMIN,
            json={"expected_version": 2},
        )
    roots_after_fail = client.get("/api/storage-settings/roots", headers=ADMIN).json()
    assert next(r for r in roots_after_fail["roots"] if r["id"] == root_id)["active"] is True

    # 2. 正常移出 -> 同步迁入 removed_extra_local_entries
    monkeypatch.setattr(
        routes.default_storage_settings_service,
        "move_entries_to_removed",
        orig_move.__get__(routes.default_storage_settings_service, StorageSettingsService),
    )
    rev_now = roots_after_fail["revision"]
    ok_remove = client.request(
        "DELETE",
        f"/api/storage-settings/roots/{root_id}",
        headers=ADMIN,
        json={"expected_version": rev_now},
    )
    assert ok_remove.status_code == 200, ok_remove.text
    sdata = client.get("/api/storage-settings", headers=ADMIN).json()
    assert not any(e["id"] == "local_extra_sync" for e in sdata["extra_local_entries"])
    raw_storage = routes.default_storage_settings_service._state.read()
    assert any(e["id"] == "local_extra_sync" for e in raw_storage.get("removed_extra_local_entries") or [])

    # 3. 恢复失败时回滚 allowed_roots
    monkeypatch.setattr(
        routes.default_storage_settings_service,
        "restore_removed_entries",
        lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("restore_boom")),
    )
    rev_removed = ok_remove.json()["revision"]
    with pytest.raises(RuntimeError, match="restore_boom"):
        client.post(
            f"/api/storage-settings/roots/{root_id}/restore",
            headers=ADMIN,
            json={"expected_version": rev_removed},
        )
    snap_after_bad_restore = client.get("/api/storage-settings/roots", headers=ADMIN).json()
    assert next(r for r in snap_after_bad_restore["roots"] if r["id"] == root_id)["active"] is False

    # 4. 正常恢复 -> 回流 extra_local_entries 并按全部活跃身份提交 reindex
    orig_restore = StorageSettingsService.restore_removed_entries
    monkeypatch.setattr(
        routes.default_storage_settings_service,
        "restore_removed_entries",
        orig_restore.__get__(routes.default_storage_settings_service, StorageSettingsService),
    )
    submitted_roots: list[list[str]] = []
    monkeypatch.setattr(
        index_jobs.service(),
        "submit",
        lambda job_type, payload, auth, key=None: submitted_roots.append(list(payload.get("roots") or [])) or {"job_id": "job_restore_1"},
    )
    ok_restore = client.post(
        f"/api/storage-settings/roots/{root_id}/restore",
        headers=ADMIN,
        json={"expected_version": snap_after_bad_restore["revision"]},
    )
    assert ok_restore.status_code == 200, ok_restore.text
    sdata2 = client.get("/api/storage-settings", headers=ADMIN).json()
    assert any(e["id"] == "local_extra_sync" for e in sdata2["extra_local_entries"])
    raw_storage2 = routes.default_storage_settings_service._state.read()
    assert not any(e["id"] == "local_extra_sync" for e in raw_storage2.get("removed_extra_local_entries") or [])
    assert submitted_roots
    assert root_id in submitted_roots[0]
    assert "local_extra_sync" in submitted_roots[0]
    assert _sha256_tree(folder) == before_hashes


def test_ac3_ac4_unregister_source_and_cancel_roots_stops_indexing(
    client: TestClient, tmp_path: Path, monkeypatch
):
    """AC3/AC4：注销来源与 cancel_roots 立即阻断在途/并发索引写入，源文件零损坏。"""
    folder = tmp_path / "vault_unreg"
    root_id, _ = _register_folder_and_assets(client, folder, source_id="local_extra_unreg")
    before_hashes = _sha256_tree(folder)

    cancelled: list[set[str]] = []
    orig_cancel = index_jobs.service().cancel_roots
    monkeypatch.setattr(
        index_jobs.service(),
        "cancel_roots",
        lambda target_ids, target_paths=None: (
            cancelled.append(set(target_ids)),
            orig_cancel(target_ids, target_paths),
        )[1],
    )

    # 通过 PATCH /api/storage-settings 注销 extra_local_entries 中的来源
    sdata = client.get("/api/storage-settings", headers=ADMIN).json()
    put_resp = client.patch(
        "/api/storage-settings",
        headers=ADMIN,
        json={
            "dirs": sdata["dirs"],
            "extra_local_entries": [],
            "local_library_names": {},
            "local_library_order": [],
            "expected_version": sdata["revision"],
        },
    )
    assert put_resp.status_code == 200, put_resp.text
    assert cancelled and "local_extra_unreg" in cancelled[0]
    assert "local_extra_unreg" not in index_jobs._roots()

    # 将另一来源移出资源库，验证 _removed_root_ids 覆盖 removed_extra_local_entries，且 _commit 锁内阻断迟到扫描写入
    folder_b = tmp_path / "vault_move_stop"
    root_id_b, _ = _register_folder_and_assets(client, folder_b, source_id="local_extra_move_stop")
    # 在资产上仅挂来源 ID 以验证 removed_extra_local_entries 双键兜底
    asset_repo.state().mutate(
        lambda raw: raw["assets"]["ast_q598_source_only"].update(
            {"root_id": "local_extra_move_stop", "storage_root_id": "local_extra_move_stop"}
        )
    )
    roots_snap = client.get("/api/storage-settings/roots", headers=ADMIN).json()
    rem_resp = client.request(
        "DELETE",
        f"/api/storage-settings/roots/{root_id_b}",
        headers=ADMIN,
        json={"expected_version": roots_snap["revision"]},
    )
    assert rem_resp.status_code == 200, rem_resp.text
    assert "local_extra_move_stop" in asset_repo._removed_root_ids()

    listed = asset_repo.list_assets(
        limit=50,
        offset=0,
        query=None,
        kind=None,
        archived=None,
        structured=True,
        scope=None,
        text="",
        filters={"status": {"asset": ["active"]}},
    )
    listed_ids = {item["asset_id"] for item in listed["items"]}
    assert "ast_q598_source_only" not in listed_ids

    # 模拟 _commit 遇到已被移除的根目录：应抛 INDEX_ROOTS_CHANGED 且不写入任何迟到结果
    mgr = index_jobs.service()
    stale_job = {
        "job_id": "job_stale",
        "attempt": 1,
        "status": "running",
        "phase": "scanning",
        "_actor": {"mode": index_jobs.load_runtime_auth_config().mode, "role": "admin"},
        "_request": {
            "kind": "reindex",
            "roots": [root_id_b],
            "scopes": [
                {
                    "root_id": root_id_b,
                    "prefix": "",
                    "storage_root_id": "local_extra_move_stop",
                }
            ],
        },
    }
    with pytest.raises(CleanroomException) as exc_info:
        mgr._commit(
            stale_job,
            [
                {
                    "root_id": root_id_b,
                    "storage_root_id": "local_extra_move_stop",
                    "canonical_relpath": "late_file.png",
                    "name": "late_file.png",
                    "kind": "image",
                    "size_bytes": 10,
                }
            ],
        )
    assert exc_info.value.code == "INDEX_ROOTS_CHANGED"
    raw_after_commit = asset_repo.state().read()
    assert not any(
        item.get("canonical_relpath") == "late_file.png"
        for item in raw_after_commit.get("assets", {}).values()
    )
    assert _sha256_tree(folder) == before_hashes


def test_ac5_system_root_readonly_on_remove_restore_purge(
    client: TestClient, tmp_path: Path
):
    """AC5：系统根在 remove / restore / purge 入口均返回 403 SYSTEM_ROOT_READ_ONLY。"""
    sys_root_id = storage.root_identity_id(tmp_path / "sys_root")
    for method, suffix in [
        ("DELETE", ""),
        ("POST", "/restore"),
        ("DELETE", "/purge"),
    ]:
        resp = client.request(
            method,
            f"/api/storage-settings/roots/{sys_root_id}{suffix}",
            headers=ADMIN,
            json={"expected_version": 1},
        )
        assert resp.status_code == 403, f"{method} {suffix} -> {resp.text}"
        assert resp.json()["detail"]["code"] == "SYSTEM_ROOT_READ_ONLY"
