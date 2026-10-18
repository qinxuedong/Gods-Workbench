"""C10-T4 隔离态备份、升级回滚与稳定引用契约。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from gw.core import storage
from gw.core.runtime_paths import MODE_DEV, MODE_TEST, resolve_runtime_paths


def _paths(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    data = tmp_path / "runtime-data"
    auth = tmp_path / "runtime-auth" / "auth.sqlite3"
    video = tmp_path / "runtime-video"
    env = {
        "GW_DATA_DIR": str(data),
        "GW_LOCAL_AUTH_DB": str(auth),
        "GW_VIDEO_DATA_DIR": str(video),
    }
    return resolve_runtime_paths(MODE_TEST, repo_root=repo, env=env)


def _seed(paths) -> dict[str, bytes]:
    paths.data_root.mkdir(parents=True)
    paths.video_root.mkdir(parents=True)
    paths.auth_db.parent.mkdir(parents=True)
    state = {
        "schema_version": 7,
        "project_id": "project_c10t4_001",
        "canvas_id": "canvas_c10t4_001",
        "asset_id": "asset_c10t4_001",
        "job_id": "job_c10t4_001",
        "references": {"canvas_id": "canvas_c10t4_001", "asset_id": "asset_c10t4_001"},
        "task": {"job_id": "job_c10t4_001", "status": "succeeded"},
    }
    data_bytes = json.dumps(state, ensure_ascii=False, sort_keys=True).encode("utf-8")
    (paths.data_root / "state.json").write_bytes(data_bytes)
    (paths.auth_db).write_bytes(b"auth-v7")
    (paths.video_root / "asset_c10t4_001.mp4").write_bytes(b"video-v7")
    return {
        "data/state.json": data_bytes,
        "auth/auth.sqlite3": b"auth-v7",
        "video/asset_c10t4_001.mp4": b"video-v7",
    }


def test_runtime_paths_expose_isolated_backup_root(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    assert paths.backup_root == paths.runtime_root / "backups"
    assert paths.mode == MODE_TEST


def test_backup_restore_preserves_ids_references_and_task_relation(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    expected = _seed(paths)
    backup = storage.create_runtime_backup(paths, destination=tmp_path / "backups", source_state_version=7)

    assert backup["status"] == "created"
    assert backup["backup_id"].startswith("backup_")
    assert backup["source_state_version"] == 7
    manifest = json.loads((Path(backup["path"]) / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["format"] == storage.BACKUP_FORMAT
    assert manifest["runtime_mode"] == MODE_TEST
    assert {item["path"] for item in manifest["files"]} == set(expected)
    assert all(item["sha256"] for item in manifest["files"])

    (paths.data_root / "state.json").write_text('{"schema_version": 99, "project_id": "mutated"}', encoding="utf-8")
    paths.auth_db.write_bytes(b"mutated-auth")
    (paths.video_root / "asset_c10t4_001.mp4").unlink()
    restored = storage.restore_runtime_backup(paths, backup)

    assert restored["status"] == "restored"
    assert restored["backup_id"] == backup["backup_id"]
    assert restored["files_restored"] == len(expected)
    assert restored["restore_duration_ms"] >= 0
    assert (paths.data_root / "state.json").read_bytes() == expected["data/state.json"]
    assert paths.auth_db.read_bytes() == expected["auth/auth.sqlite3"]
    assert (paths.video_root / "asset_c10t4_001.mp4").read_bytes() == expected["video/asset_c10t4_001.mp4"]


def test_upgrade_success_then_explicit_rollback_reports_measured_rpo_rto(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    _seed(paths)
    destination = tmp_path / "backups"

    def upgrade(current):
        payload = json.loads((current.data_root / "state.json").read_text(encoding="utf-8"))
        payload["schema_version"] = 8
        payload["task"]["status"] = "upgraded"
        (current.data_root / "state.json").write_text(json.dumps(payload), encoding="utf-8")
        return {"schema_version": 8}

    result = storage.upgrade_runtime(
        paths,
        upgrade,
        destination=destination,
        source_state_version=7,
        target_state_version=8,
    )
    assert result["status"] == "upgraded"
    assert result["backup"]["source_state_version"] == 7
    assert result["backup_duration_ms"] >= 0
    assert result["upgrade_duration_ms"] >= 0
    assert json.loads((paths.data_root / "state.json").read_text(encoding="utf-8"))["schema_version"] == 8

    rolled_back = storage.rollback_runtime(paths, result["backup"])
    restored = json.loads((paths.data_root / "state.json").read_text(encoding="utf-8"))
    assert rolled_back["status"] == "rolled_back"
    assert rolled_back["restore_duration_ms"] >= 0
    assert restored["schema_version"] == 7
    assert restored["references"]["asset_id"] == "asset_c10t4_001"
    assert restored["task"]["job_id"] == "job_c10t4_001"


def test_failed_upgrade_restores_previous_state_and_tamper_is_rejected(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    _seed(paths)
    destination = tmp_path / "backups"

    def broken_upgrade(current):
        (current.data_root / "state.json").write_text('{"schema_version": 999}', encoding="utf-8")
        raise RuntimeError("upgrade fixture failure")

    with pytest.raises(RuntimeError, match="upgrade fixture failure"):
        storage.upgrade_runtime(
            paths,
            broken_upgrade,
            destination=destination,
            source_state_version=7,
            target_state_version=999,
        )
    assert json.loads((paths.data_root / "state.json").read_text(encoding="utf-8"))["schema_version"] == 7

    good = storage.create_runtime_backup(paths, destination=destination, source_state_version=7)
    tampered = Path(good["path"]) / "data" / "state.json"
    tampered.write_text('{"schema_version": 1000}', encoding="utf-8")
    with pytest.raises(storage.RuntimeBackupError) as error:
        storage.restore_runtime_backup(paths, good)
    assert error.value.code == "BACKUP_INTEGRITY_FAILED"
    assert json.loads((paths.data_root / "state.json").read_text(encoding="utf-8"))["schema_version"] == 7


def test_backup_operations_fail_closed_outside_test_runtime(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    paths = resolve_runtime_paths(MODE_DEV, repo_root=repo, env={})
    with pytest.raises(storage.RuntimeBackupError) as error:
        storage.create_runtime_backup(paths)
    assert error.value.code == "BACKUP_TEST_ONLY"
