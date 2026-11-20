# Copyright 2026 Gods-Workbench Authors
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""QIN-627 数据位置指针、迁移、锁与恢复矩阵契约测试。"""

from __future__ import annotations

import errno
import hashlib
import json
import os
import sqlite3
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from gw.core import storage
from gw.core.errors import CleanroomException
from gw.core.runtime_paths import (
    PROD_DATA_LOCATION_INVALID,
    RuntimePathError,
    data_location_pointer_path,
    read_data_location_pointer,
    resolve_runtime_paths,
)
from gw.core.write_window import (
    STATE_OPEN,
    STATE_READ_ONLY,
    get_write_window,
    reset_write_window,
)
from gw.settings import data_location as service


REPO_ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture
def prod_home(tmp_path, monkeypatch):
    """把生产态 LOCALAPPDATA 指向 tmp，并重置写窗口与迁移单例。"""
    local = tmp_path / "localappdata"
    runtime_root = local / "GodsWorkbench"
    for sub in ("data", "auth", "video"):
        (runtime_root / sub).mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("GW_RUNTIME_MODE", "prod")
    monkeypatch.setenv("LOCALAPPDATA", str(local))
    for name in ("GW_DATA_DIR", "GW_LOCAL_AUTH_DB", "GW_VIDEO_DATA_DIR"):
        monkeypatch.delenv(name, raising=False)
    reset_write_window()
    with service._STATE_LOCK:
        service._ACTIVE.update({"record": None, "cancel": None, "thread": None})
    yield {"local": local, "runtime_root": runtime_root}
    reset_write_window()
    with service._STATE_LOCK:
        service._ACTIVE.update({"record": None, "cancel": None, "thread": None})


def _seed(runtime_root: Path, *, models: bool = True) -> dict[str, Path]:
    data = runtime_root / "data"
    (data / "agent").mkdir(parents=True, exist_ok=True)
    (data / "projects.json").write_text('{"projects": []}', encoding="utf-8")
    agent_db = data / "agent" / "runtime.sqlite3"
    connection = sqlite3.connect(agent_db)
    connection.execute("CREATE TABLE runs (run_id TEXT PRIMARY KEY)")
    connection.execute("INSERT INTO runs VALUES ('r1')")
    connection.commit()
    connection.close()
    auth = runtime_root / "auth" / "auth.sqlite3"
    connection = sqlite3.connect(auth)
    connection.execute("CREATE TABLE local_users (user_id TEXT PRIMARY KEY)")
    connection.commit()
    connection.close()
    artifacts = runtime_root / "video" / "artifacts"
    artifacts.mkdir(parents=True, exist_ok=True)
    (artifacts / "a.bin").write_bytes(b"artifact-bytes")
    work = runtime_root / "video" / "work"
    work.mkdir(parents=True, exist_ok=True)
    (work / "temp.bin").write_bytes(b"temp")
    models_dir = data / "models"
    if models:
        models_dir.mkdir(parents=True, exist_ok=True)
        (models_dir / "model.bin").write_bytes(b"model-bytes")
    return {
        "projects": data / "projects.json",
        "agent_db": agent_db,
        "artifacts": artifacts / "a.bin",
        "work": work / "temp.bin",
        "models": models_dir / "model.bin",
    }


def _wait_status(migration_id: str, timeout: float = 15.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        record = service.migration_status(migration_id)
        if record.get("status") in {"completed", "failed", "cancelled", "rolled_back"}:
            return record
        time.sleep(0.05)
    raise AssertionError(f"迁移未在 {timeout}s 内结束: {record}")


def _start(target: Path, *, expected: int = 1) -> dict:
    return service.start_migration(str(target), expected_version=expected)


# ---------------------------------------------------------------------------
# 指针解析与失败关闭
# ---------------------------------------------------------------------------


def test_pointer_missing_keeps_default_layout(prod_home):
    paths = resolve_runtime_paths()
    assert paths.data_location is None
    assert paths.data_root == prod_home["runtime_root"] / "data"
    assert paths.auth_db == prod_home["runtime_root"] / "auth" / "auth.sqlite3"
    assert paths.video_root == prod_home["runtime_root"] / "video"


def test_pointer_redirects_three_roots(prod_home, tmp_path):
    location = tmp_path / "external" / "gwdata"
    location.mkdir(parents=True)
    service._write_pointer(
        data_location=location,
        revision=2,
        previous_location=prod_home["runtime_root"],
        last_migration={"id": "m1", "status": "completed"},
    )
    paths = resolve_runtime_paths()
    assert paths.data_location == location
    assert paths.data_root == location / "data"
    assert paths.auth_db == location / "auth" / "auth.sqlite3"
    assert paths.video_root == location / "video"


def test_corrupted_pointer_fails_closed(prod_home):
    pointer = data_location_pointer_path(prod_home["runtime_root"])
    pointer.write_text("{not-json", encoding="utf-8")
    with pytest.raises(RuntimePathError) as exc:
        resolve_runtime_paths()
    assert exc.value.code == PROD_DATA_LOCATION_INVALID
    with pytest.raises(RuntimePathError):
        read_data_location_pointer(prod_home["runtime_root"])


def test_pointer_inside_repository_is_rejected(prod_home):
    location = REPO_ROOT / "temp" / "qin627-pointer"
    location.mkdir(parents=True, exist_ok=True)
    service._write_pointer(
        data_location=location,
        revision=2,
        previous_location=prod_home["runtime_root"],
        last_migration=None,
    )
    with pytest.raises(RuntimePathError) as exc:
        resolve_runtime_paths()
    assert exc.value.code == "PROD_PATH_IN_REPOSITORY"


def test_broken_pointer_symlink_is_rejected(prod_home, tmp_path):
    pointer = data_location_pointer_path(prod_home["runtime_root"])
    target = tmp_path / "pointer.json"
    target.write_text(json.dumps({"schema_version": 1, "revision": 2, "data_location": str(tmp_path / "x")}), encoding="utf-8")
    try:
        pointer.symlink_to(target)
    except (OSError, NotImplementedError):
        pytest.skip("当前环境不允许创建符号链接")
    with pytest.raises(RuntimePathError) as exc:
        read_data_location_pointer(prod_home["runtime_root"])
    assert exc.value.code == PROD_DATA_LOCATION_INVALID


# ---------------------------------------------------------------------------
# 预检
# ---------------------------------------------------------------------------


def test_check_target_rejects_relative_nonempty_and_overlap(prod_home, tmp_path):
    _seed(prod_home["runtime_root"])
    with pytest.raises(CleanroomException) as exc:
        service.check_target("relative/path")
    assert exc.value.code == "DATA_LOCATION_NOT_ABSOLUTE"

    occupied = tmp_path / "occupied"
    occupied.mkdir()
    (occupied / "keep.txt").write_text("x", encoding="utf-8")
    with pytest.raises(CleanroomException) as exc:
        service.check_target(str(occupied))
    assert exc.value.code == "TARGET_NOT_EMPTY"

    inside = prod_home["runtime_root"] / "nested"
    inside.mkdir()
    with pytest.raises(CleanroomException) as exc:
        service.check_target(str(inside))
    assert exc.value.code == "TARGET_OVERLAP"


def test_check_target_reports_insufficient_space(prod_home, tmp_path, monkeypatch):
    _seed(prod_home["runtime_root"])
    monkeypatch.setattr(service, "_anchored_free_bytes", lambda target: 1)
    with pytest.raises(CleanroomException) as exc:
        service.check_target(str(tmp_path / "target"))
    assert exc.value.code == "INSUFFICIENT_SPACE"


def test_check_target_rejects_non_writable(prod_home, tmp_path, monkeypatch):
    _seed(prod_home["runtime_root"])

    def boom(target):
        raise CleanroomException(400, "NOT_WRITABLE", "目标目录不可写")

    monkeypatch.setattr(service, "_write_probe", boom)
    with pytest.raises(CleanroomException) as exc:
        service.check_target(str(tmp_path / "target"))
    assert exc.value.code == "NOT_WRITABLE"


# ---------------------------------------------------------------------------
# 迁移主路径与模型 prune
# ---------------------------------------------------------------------------


def test_migration_executes_and_models_are_pruned(prod_home, tmp_path):
    paths = _seed(prod_home["runtime_root"])
    target = tmp_path / "target" / "gwdata"
    record = _start(target)
    finished = _wait_status(record["migration_id"])
    assert finished["status"] == "completed"
    assert finished["verified_files"] > 0
    assert finished["cache_models_default_skip"] is True

    window = get_write_window()
    assert window.state == STATE_READ_ONLY

    pointer = service.pointer_state()
    assert pointer["present"] is True
    assert pointer["revision"] == 2
    assert Path(pointer["data_location"]) == target
    assert Path(pointer["previous_location"]) == prod_home["runtime_root"]

    assert (target / "data" / "projects.json").is_file()
    assert not (target / "data" / "models").exists()
    assert not (target / "video" / "work").exists()
    assert (target / "auth" / "auth.sqlite3").is_file()
    assert (target / "video" / "artifacts" / "a.bin").is_file()
    manifest = json.loads((target / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["format"] == service.MIGRATION_FORMAT
    reasons = {item["reason"] for item in manifest["excluded"]}
    assert "MODEL_CACHE_NOT_SELECTED" in reasons
    assert "VIDEO_WORKDIR_NOT_MIGRATED" in reasons

    # 旧位置必须原样保留，素材与模型不被静默移动。
    assert paths["models"].read_bytes() == b"model-bytes"
    assert paths["artifacts"].read_bytes() == b"artifact-bytes"
    assert paths["work"].read_bytes() == b"temp"


def test_migration_can_include_models_when_selected(prod_home, tmp_path):
    _seed(prod_home["runtime_root"])
    target = tmp_path / "target-models"
    record = service.start_migration(
        str(target), expected_version=1, categories=["user_data", "cache", "models"]
    )
    finished = _wait_status(record["migration_id"])
    assert finished["status"] == "completed"
    assert (target / "data" / "models" / "model.bin").is_file()


def test_read_only_window_blocks_json_and_sqlite_writes(prod_home, tmp_path):
    _seed(prod_home["runtime_root"])
    record = _start(tmp_path / "target-ro")
    _wait_status(record["migration_id"])
    assert get_write_window().state == STATE_READ_ONLY
    from gw.core.write_window import WriteWindowClosed

    with pytest.raises(WriteWindowClosed) as exc:
        storage.save("qin627_ro_probe", {"x": 1})
    assert exc.value.code == "DATA_LOCATION_RESTART_REQUIRED"
    # 只读窗口内以只读连接读取旧库仍可用。
    old_db = prod_home["runtime_root"] / "data" / "agent" / "runtime.sqlite3"
    from gw.core.write_window import open_sqlite

    connection = open_sqlite(old_db, timeout=10)
    try:
        assert connection.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 1
    finally:
        connection.close()


def test_cold_start_reads_new_location(prod_home, tmp_path):
    _seed(prod_home["runtime_root"])
    target = tmp_path / "target-cold"
    record = _start(target)
    _wait_status(record["migration_id"])
    env = dict(os.environ)
    env["GW_RUNTIME_MODE"] = "prod"
    env["LOCALAPPDATA"] = str(prod_home["local"])
    code = (
        "from gw.core.runtime_paths import resolve_runtime_paths;"
        "p=resolve_runtime_paths();print(p.data_root)"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
    assert Path(result.stdout.strip()) == target / "data"


def test_inflight_write_is_drained_before_copy(prod_home, tmp_path):
    paths = _seed(prod_home["runtime_root"])
    target = tmp_path / "target-drain"
    window = get_write_window()
    entered = threading.Event()
    release = threading.Event()
    finished = threading.Event()

    def writer():
        with window.request_write():
            entered.set()
            release.wait(10)
            paths["projects"].write_text('{"projects": [{"id": "p-new"}]}', encoding="utf-8")
        finished.set()

    thread = threading.Thread(target=writer, daemon=True)
    thread.start()
    assert entered.wait(5)
    record = _start(target)
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        status = service.migration_status(record["migration_id"])
        if status["status"] in {"quiescing", "checking_space"}:
            break
        time.sleep(0.02)
    # 排空未完成前不得进入复制；写入完成后才允许继续。
    time.sleep(0.2)
    assert finished.is_set() is False
    assert service.migration_status(record["migration_id"])["status"] in {
        "quiescing",
        "checking_space",
    }
    release.set()
    assert finished.wait(5)
    finished_record = _wait_status(record["migration_id"], timeout=20)
    assert finished_record["status"] == "completed"
    copied = json.loads((target / "data" / "projects.json").read_text(encoding="utf-8"))
    assert copied == {"projects": [{"id": "p-new"}]}


def test_cancel_keeps_pointer_and_cleans_stage(prod_home, tmp_path, monkeypatch):
    _seed(prod_home["runtime_root"])
    target = tmp_path / "target-cancel"
    started = {"record": None}

    def slow_copy(files, stage, record, cancel):
        started["record"] = record
        while not cancel.is_set():
            time.sleep(0.02)
        raise service._Cancelled()

    monkeypatch.setattr(service, "_copy_files", slow_copy)
    record = _start(target)
    deadline = time.monotonic() + 5
    while started["record"] is None and time.monotonic() < deadline:
        time.sleep(0.02)
    service.cancel_migration(record["migration_id"])
    finished = _wait_status(record["migration_id"])
    assert finished["status"] == "cancelled"
    assert get_write_window().state == STATE_OPEN
    assert service.pointer_state()["revision"] == 1
    assert not any(target.glob(service.STAGE_PREFIX + "*")) if target.exists() else True


def test_verify_failure_rolls_back_without_pointer_change(prod_home, tmp_path, monkeypatch):
    _seed(prod_home["runtime_root"])
    target = tmp_path / "target-fail"

    def corrupt(files, stage, manifest, cancel):
        raise CleanroomException(409, "VERIFY_FAILED", "复制文件校验和不一致")

    monkeypatch.setattr(service, "_verify", corrupt)
    record = _start(target)
    finished = _wait_status(record["migration_id"])
    assert finished["status"] == "failed"
    assert finished["error_code"] == "VERIFY_FAILED"
    assert get_write_window().state == STATE_OPEN
    assert service.pointer_state()["revision"] == 1
    assert not any(target.glob(service.STAGE_PREFIX + "*")) if target.exists() else True


def test_source_instability_fails_after_attempts(prod_home, tmp_path, monkeypatch):
    _seed(prod_home["runtime_root"])
    monkeypatch.setattr(service, "_source_changed", lambda files: True)
    record = _start(tmp_path / "target-unstable")
    finished = _wait_status(record["migration_id"], timeout=20)
    assert finished["status"] == "failed"
    assert finished["error_code"] == "SOURCE_UNSTABLE"
    assert get_write_window().state == STATE_OPEN


def test_migration_busy_when_background_job_active(prod_home, tmp_path):
    _seed(prod_home["runtime_root"])
    window = get_write_window()
    window.register_host("fake-busy", lambda: 1)
    try:
        with pytest.raises(CleanroomException) as exc:
            service.start_migration(str(tmp_path / "target-busy"), expected_version=1)
        assert exc.value.code == "MIGRATION_BUSY"
        assert window.state == STATE_OPEN
    finally:
        window.unregister_host("fake-busy")


def test_version_conflict_is_rejected(prod_home, tmp_path):
    _seed(prod_home["runtime_root"])
    with pytest.raises(CleanroomException) as exc:
        service.start_migration(str(tmp_path / "target-version"), expected_version=9)
    assert exc.value.code == "DATA_LOCATION_VERSION_CONFLICT"


def test_rollback_returns_pointer_to_previous_location(prod_home, tmp_path):
    _seed(prod_home["runtime_root"])
    target = tmp_path / "target-rollback"
    record = _start(target)
    _wait_status(record["migration_id"])
    result = service.rollback()
    assert result["status"] == "rolled_back"
    pointer = service.pointer_state()
    assert Path(pointer["data_location"]) == prod_home["runtime_root"]
    assert Path(pointer["previous_location"]) == target
    assert get_write_window().state == STATE_READ_ONLY


# ---------------------------------------------------------------------------
# 恢复矩阵
# ---------------------------------------------------------------------------


def _write_recovery_files(root: Path, files: dict[str, bytes]) -> None:
    for relative, data in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)


def _recovery_manifest(migration_id: str, files: dict[str, bytes]) -> dict:
    entries = []
    for relative, data in sorted(files.items()):
        entries.append(
            {
                "path": relative,
                "size": len(data),
                "sha256": hashlib.sha256(data).hexdigest(),
                "category": "cache" if relative.startswith("video/") else "user_data",
            }
        )
    return {
        "format": service.MIGRATION_FORMAT,
        "migration_id": migration_id,
        "created_at": service._now(),
        "freeze_token": "recovery-token",
        "total_bytes": sum(len(data) for data in files.values()),
        "files": entries,
        "excluded": [],
        "verified_bytes": sum(len(data) for data in files.values()),
    }


def _receipt_entry(root: Path, relative: str, kind: str) -> dict:
    info = (root / relative).lstat()
    entry = {
        "kind": kind,
        "path": relative,
        "dev": int(info.st_dev),
        "ino": int(info.st_ino),
        "ctime_ns": int(info.st_ctime_ns),
    }
    if kind == "file":
        entry["mtime_ns"] = int(info.st_mtime_ns)
        entry["size"] = int(info.st_size)
    return entry


def _write_promotion_receipt(
    root: Path,
    migration_id: str,
    files: list[str],
    *,
    extra_entries: list[dict] | None = None,
) -> Path:
    parents: set[str] = set()
    for relative in files:
        parts = relative.split("/")
        for index in range(1, len(parts)):
            parents.add("/".join(parts[:index]))
    entries = [_receipt_entry(root, relative, "dir") for relative in sorted(parents)]
    entries.extend(_receipt_entry(root, relative, "file") for relative in sorted(files))
    if extra_entries:
        entries.extend(extra_entries)
    path = root / service._receipt_name(migration_id)
    lines = [
        json.dumps(
            {
                "format": service.PROMOTION_RECEIPT_FORMAT,
                "migration_id": migration_id,
                "created_at": service._now(),
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    ]
    lines.extend(
        json.dumps(entry, ensure_ascii=False, sort_keys=True) for entry in entries
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _write_promoting_journal(target: Path, migration_id: str) -> None:
    service._write_journal(
        {
            "schema_version": service.JOURNAL_SCHEMA_VERSION,
            "migration_id": migration_id,
            "status": "promoting",
            "phase": "promoting",
            "target": str(target),
            "base_revision": 1,
            "created_at": service._now(),
        }
    )


def test_recovery_cleans_stage_for_copying_journal(prod_home, tmp_path):
    target = tmp_path / "target-recover"
    stage = target / (service.STAGE_PREFIX + "abc123")
    stage.mkdir(parents=True)
    (stage / "partial.bin").write_bytes(b"partial")
    service._write_journal(
        {
            "schema_version": service.JOURNAL_SCHEMA_VERSION,
            "migration_id": "abc123",
            "status": "copying",
            "phase": "copying",
            "target": str(target),
            "base_revision": 1,
            "created_at": service._now(),
        }
    )
    result = service.recover_on_startup()
    assert result["action"] == "cleaned_stage"
    assert not stage.exists()
    assert service.read_journal()["status"] == "failed"


def test_recovery_marks_completed_when_pointer_advanced(prod_home, tmp_path):
    target = tmp_path / "target-promoted"
    target.mkdir()
    (target / "data").mkdir()
    service._write_pointer(
        data_location=target,
        revision=2,
        previous_location=prod_home["runtime_root"],
        last_migration={"id": "promo1", "status": "completed"},
    )
    service._write_journal(
        {
            "schema_version": service.JOURNAL_SCHEMA_VERSION,
            "migration_id": "promo1",
            "status": "promoting",
            "phase": "promoting",
            "target": str(target),
            "base_revision": 1,
            "created_at": service._now(),
        }
    )
    result = service.recover_on_startup()
    assert result["action"] == "marked_completed"
    assert service.read_journal()["status"] == "completed"


def test_recovery_removes_only_owned_files_and_preserves_target_user_file(prod_home, tmp_path):
    migration_id = "promo-preserve"
    target = tmp_path / "target-preserve"
    target.mkdir()
    stage = target / (service.STAGE_PREFIX + migration_id)
    stage.mkdir()
    files = {
        "data/projects.json": b'{"projects": []}',
        "auth/auth.sqlite3": b"auth-store",
    }
    _write_recovery_files(target, {"data/projects.json": files["data/projects.json"]})
    _write_recovery_files(stage, {"auth/auth.sqlite3": files["auth/auth.sqlite3"]})
    manifest = _recovery_manifest(migration_id, files)
    service._atomic_write_json(target / "manifest.json", manifest)
    service._atomic_write_json(stage / "manifest.json", manifest)
    _write_promotion_receipt(target, migration_id, ["data/projects.json"])
    user_file = target / "user-file.txt"
    user_file.write_text("user-owned", encoding="utf-8")
    _write_promoting_journal(target, migration_id)

    result = service.recover_on_startup()

    assert result["action"] == "removed_owned_preserved_unrelated"
    assert result["preserved"] == 1
    assert not (target / "data" / "projects.json").exists()
    assert user_file.read_text(encoding="utf-8") == "user-owned"
    assert not stage.exists()
    assert not (target / "manifest.json").exists()
    journal = service.read_journal()
    assert journal["status"] == "failed"
    assert journal["error_code"] == "SWITCH_FAILED"
    assert "保留" in journal["message"]


def test_recovery_preserves_unrelated_file_inside_promoted_directory(prod_home, tmp_path):
    migration_id = "promo-nested"
    target = tmp_path / "target-nested"
    target.mkdir()
    stage = target / (service.STAGE_PREFIX + migration_id)
    stage.mkdir()
    files = {
        "data/projects.json": b'{"projects": []}',
        "auth/auth.sqlite3": b"auth-store",
    }
    _write_recovery_files(target, {"data/projects.json": files["data/projects.json"]})
    _write_recovery_files(stage, {"auth/auth.sqlite3": files["auth/auth.sqlite3"]})
    manifest = _recovery_manifest(migration_id, files)
    service._atomic_write_json(target / "manifest.json", manifest)
    service._atomic_write_json(stage / "manifest.json", manifest)
    _write_promotion_receipt(target, migration_id, ["data/projects.json"])
    nested_user_file = target / "data" / "notes.txt"
    nested_user_file.write_text("do not delete", encoding="utf-8")
    _write_promoting_journal(target, migration_id)

    result = service.recover_on_startup()

    assert result["action"] == "removed_owned_preserved_unrelated"
    assert not (target / "data" / "projects.json").exists()
    assert nested_user_file.read_text(encoding="utf-8") == "do not delete"
    assert (target / "data").is_dir()
    assert not stage.exists()


def test_recovery_preserves_modified_manifest_path_when_hash_does_not_match(prod_home, tmp_path):
    migration_id = "promo-modified"
    target = tmp_path / "target-modified"
    target.mkdir()
    stage = target / (service.STAGE_PREFIX + migration_id)
    stage.mkdir()
    files = {
        "data/projects.json": b'{"projects": []}',
        "auth/auth.sqlite3": b"auth-store",
    }
    _write_recovery_files(target, {"data/projects.json": b'{"projects": ["user-edit"]}'})
    _write_recovery_files(stage, {"auth/auth.sqlite3": files["auth/auth.sqlite3"]})
    manifest = _recovery_manifest(migration_id, files)
    service._atomic_write_json(target / "manifest.json", manifest)
    service._atomic_write_json(stage / "manifest.json", manifest)
    _write_promotion_receipt(target, migration_id, ["data/projects.json"])
    _write_promoting_journal(target, migration_id)

    result = service.recover_on_startup()

    assert result["action"] == "preserved_unverified"
    assert result["reason"] == "no_verified_target_items"
    assert (target / "data" / "projects.json").read_text(encoding="utf-8") == '{"projects": ["user-edit"]}'
    assert not (target / "auth").exists()
    assert not stage.exists()


def test_recovery_uses_stage_manifest_when_target_manifest_is_missing(prod_home, tmp_path):
    migration_id = "promo-stage-manifest"
    target = tmp_path / "target-stage-manifest"
    target.mkdir()
    stage = target / (service.STAGE_PREFIX + migration_id)
    stage.mkdir()
    files = {
        "data/projects.json": b'{"projects": []}',
        "auth/auth.sqlite3": b"auth-store",
    }
    _write_recovery_files(target, {"data/projects.json": files["data/projects.json"]})
    _write_recovery_files(stage, {"auth/auth.sqlite3": files["auth/auth.sqlite3"]})
    manifest = _recovery_manifest(migration_id, files)
    service._atomic_write_json(stage / "manifest.json", manifest)
    _write_promotion_receipt(target, migration_id, ["data/projects.json"])
    user_file = target / "user-file.txt"
    user_file.write_text("user-owned", encoding="utf-8")
    _write_promoting_journal(target, migration_id)

    result = service.recover_on_startup()

    assert result["action"] == "removed_owned_preserved_unrelated"
    assert not (target / "data" / "projects.json").exists()
    assert user_file.read_text(encoding="utf-8") == "user-owned"
    assert not stage.exists()


def test_recovery_cleans_partial_promote_and_removes_empty_target(prod_home, tmp_path):
    migration_id = "promo-partial"
    target = tmp_path / "target-partial"
    target.mkdir()
    stage = target / (service.STAGE_PREFIX + migration_id)
    stage.mkdir()
    files = {
        "data/projects.json": b'{"projects": []}',
        "auth/auth.sqlite3": b"auth-store",
    }
    _write_recovery_files(target, {"data/projects.json": files["data/projects.json"]})
    _write_recovery_files(stage, {"auth/auth.sqlite3": files["auth/auth.sqlite3"]})
    manifest = _recovery_manifest(migration_id, files)
    service._atomic_write_json(target / "manifest.json", manifest)
    service._atomic_write_json(stage / "manifest.json", manifest)
    _write_promotion_receipt(target, migration_id, ["data/projects.json"])
    _write_promoting_journal(target, migration_id)

    result = service.recover_on_startup()

    assert result["action"] == "removed_final"
    assert result["preserved"] == 0
    assert not stage.exists()
    assert not any(target.iterdir())


def test_recovery_without_manifest_preserves_target_and_is_idempotent(prod_home, tmp_path):
    migration_id = "promo-unknown"
    target = tmp_path / "target-unknown"
    target.mkdir()
    (target / "data").mkdir()
    owned_looking = target / "data" / "projects.json"
    owned_looking.write_text("{}", encoding="utf-8")
    _write_promoting_journal(target, migration_id)

    result = service.recover_on_startup()

    assert result["action"] == "preserved_unverified"
    assert owned_looking.is_file()
    journal = service.read_journal()
    assert journal["status"] == "failed"
    assert journal["error_code"] == "RECOVERY_OWNERSHIP_UNKNOWN"

    second = service.recover_on_startup()

    assert second["action"] == "none"
    assert owned_looking.is_file()


def test_promote_refuses_to_overwrite_concurrent_target_file(prod_home, tmp_path):
    migration_id = "promo-concurrent"
    target = tmp_path / "target-concurrent"
    target.mkdir()
    stage = target / (service.STAGE_PREFIX + migration_id)
    (stage / "data").mkdir(parents=True)
    payload = b'{"projects": []}'
    (stage / "data" / "projects.json").write_bytes(payload)
    manifest = _recovery_manifest(migration_id, {"data/projects.json": payload})
    (target / "data").mkdir()
    user_file = target / "data" / "projects.json"
    user_file.write_text("concurrent user content", encoding="utf-8")

    with pytest.raises(CleanroomException) as exc:
        service._promote(stage, target, manifest)

    assert exc.value.code == "SWITCH_FAILED"
    assert user_file.read_text(encoding="utf-8") == "concurrent user content"


def test_promoted_target_scan_rejects_concurrent_new_file(prod_home, tmp_path):
    migration_id = "promo-scan"
    target = tmp_path / "target-scan"
    target.mkdir()
    stage = target / (service.STAGE_PREFIX + migration_id)
    stage.mkdir()
    files = {"data/projects.json": b'{"projects": []}'}
    _write_recovery_files(target, files)
    manifest = _recovery_manifest(migration_id, files)
    service._atomic_write_json(target / "manifest.json", manifest)
    receipt = _write_promotion_receipt(target, migration_id, ["data/projects.json"])
    (target / "user-file.txt").write_text("concurrent", encoding="utf-8")

    with pytest.raises(CleanroomException) as exc:
        service._verify_promoted_target(target, stage, manifest, receipt.name)

    assert exc.value.code == "SWITCH_FAILED"
    assert (target / "user-file.txt").read_text(encoding="utf-8") == "concurrent"


def test_concurrent_target_file_aborts_switch_and_is_preserved(
    prod_home, tmp_path, monkeypatch
):
    _seed(prod_home["runtime_root"])
    target = tmp_path / "target-concurrent-live"
    original_promote = service._promote

    def promote_then_user_file(stage, destination, manifest):
        original_promote(stage, destination, manifest)
        (destination / "user-file.txt").write_text("concurrent", encoding="utf-8")

    monkeypatch.setattr(service, "_promote", promote_then_user_file)
    record = _start(target)
    finished = _wait_status(record["migration_id"])

    assert finished["status"] == "failed"
    assert finished["error_code"] == "SWITCH_FAILED"
    assert "保留" in finished["message"]
    assert (target / "user-file.txt").read_text(encoding="utf-8") == "concurrent"
    assert not (target / "data" / "projects.json").exists()
    assert not (target / "manifest.json").exists()
    assert service.pointer_state()["revision"] == 1
    assert get_write_window().state == STATE_OPEN


def test_recovery_preserves_identical_file_without_creation_record(prod_home, tmp_path):
    """文件已创建但收据未记录的中断窗口：同名同内容也必须保留。"""
    migration_id = "promo-unrecorded-identical"
    target = tmp_path / "target-unrecorded"
    target.mkdir()
    stage = target / (service.STAGE_PREFIX + migration_id)
    stage.mkdir()
    payload = b'{"record": "user-created"}'
    _write_recovery_files(target, {"data/record.json": payload})
    manifest = _recovery_manifest(migration_id, {"data/record.json": payload})
    service._atomic_write_json(target / "manifest.json", manifest)
    service._atomic_write_json(stage / "manifest.json", manifest)
    receipt_path = target / service._receipt_name(migration_id)
    receipt_path.write_text(
        json.dumps(
            {
                "format": service.PROMOTION_RECEIPT_FORMAT,
                "migration_id": migration_id,
                "created_at": service._now(),
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    _write_promoting_journal(target, migration_id)

    result = service.recover_on_startup()

    assert result["action"] == "preserved_unverified"
    assert result["reason"] == "no_recorded_target_items"
    assert (target / "data" / "record.json").read_bytes() == payload
    assert not (target / "manifest.json").exists()
    assert not receipt_path.exists()
    journal = service.read_journal()
    assert journal["status"] == "failed"
    assert journal["error_code"] == "RECOVERY_OWNERSHIP_UNKNOWN"

    second = service.recover_on_startup()
    assert second["action"] == "none"
    assert (target / "data" / "record.json").read_bytes() == payload


def test_recovery_preserves_same_bytes_after_file_identity_changed(prod_home, tmp_path):
    """内容相同但文件身份已变：收据无法证明归属，必须保留。"""
    migration_id = "promo-identity-changed"
    target = tmp_path / "target-identity"
    target.mkdir()
    stage = target / (service.STAGE_PREFIX + migration_id)
    stage.mkdir()
    payload = b'{"projects": []}'
    _write_recovery_files(target, {"data/projects.json": payload})
    manifest = _recovery_manifest(migration_id, {"data/projects.json": payload})
    service._atomic_write_json(target / "manifest.json", manifest)
    service._atomic_write_json(stage / "manifest.json", manifest)
    _write_promotion_receipt(target, migration_id, ["data/projects.json"])
    replacement = target / "data" / "projects.json.new"
    replacement.write_bytes(payload)
    os.replace(replacement, target / "data" / "projects.json")
    _write_promoting_journal(target, migration_id)

    result = service.recover_on_startup()

    assert result["action"] == "preserved_unverified"
    assert result["reason"] == "no_verified_target_items"
    assert (target / "data" / "projects.json").read_bytes() == payload
    assert not stage.exists()


def test_promote_refuses_directory_file_conflict_without_overwrite(prod_home, tmp_path):
    """目标同名位置是用户目录时，切换失败且目录内容原样保留。"""
    migration_id = "promo-dir-conflict"
    target = tmp_path / "target-dir-conflict"
    target.mkdir()
    stage = target / (service.STAGE_PREFIX + migration_id)
    (stage / "data").mkdir(parents=True)
    payload = b'{"projects": []}'
    (stage / "data" / "projects.json").write_bytes(payload)
    manifest = _recovery_manifest(migration_id, {"data/projects.json": payload})
    (target / "data").mkdir()
    conflict = target / "data" / "projects.json"
    conflict.mkdir()
    (conflict / "user.txt").write_text("keep", encoding="utf-8")

    with pytest.raises(CleanroomException) as exc:
        service._promote(stage, target, manifest)

    assert exc.value.code == "SWITCH_FAILED"
    assert conflict.is_dir()
    assert (conflict / "user.txt").read_text(encoding="utf-8") == "keep"


def test_same_path_same_content_concurrent_file_survives_failed_switch(
    prod_home, tmp_path, monkeypatch
):
    """复审场景：用户先建同名同内容文件，迁移失败后恢复不得删除。"""
    _seed(prod_home["runtime_root"])
    source = prod_home["runtime_root"] / "data" / "projects.json"
    target = tmp_path / "target-same-content"
    original_promote = service._promote

    def promote_with_identical_user_file(stage, destination, manifest):
        user_data = destination / "data"
        user_data.mkdir(parents=True)
        (user_data / "projects.json").write_bytes(source.read_bytes())
        return original_promote(stage, destination, manifest)

    monkeypatch.setattr(service, "_promote", promote_with_identical_user_file)
    record = _start(target)
    finished = _wait_status(record["migration_id"])

    assert finished["status"] == "failed"
    assert finished["error_code"] == "SWITCH_FAILED"
    assert "保留" in finished["message"]
    assert (target / "data" / "projects.json").read_bytes() == source.read_bytes()
    assert not (target / "auth").exists()
    assert not (target / "manifest.json").exists()
    assert not list(target.glob(service.RECEIPT_PREFIX + "*.jsonl"))
    assert service.pointer_state()["revision"] == 1
    assert service.current_location() == prod_home["runtime_root"]
    assert source.is_file()
    assert get_write_window().state == STATE_OPEN


# ---------------------------------------------------------------------------
# 双层锁
# ---------------------------------------------------------------------------


def test_stale_lock_with_dead_pid_is_recovered(prod_home):
    root = prod_home["runtime_root"] / "data"
    lock_path = root / ".gw-data.lock"
    lock_path.write_text(
        json.dumps({"host": "some-host", "pid": 99999999, "pid_start_time": 1}),
        encoding="utf-8",
    )
    lock = storage.acquire_data_dir_lock(root)
    identity = storage.read_lock_identity(lock_path)
    assert identity["pid"] == os.getpid()
    lock.release()
    storage._HELD_DATA_LOCKS.clear()


def test_lock_unsupported_filesystem_maps_error(prod_home, monkeypatch):
    root = prod_home["runtime_root"] / "data"
    storage._HELD_DATA_LOCKS.clear()

    def unsupported(handle):
        raise OSError(errno.ENOLCK, "no locks")

    monkeypatch.setattr(storage, "_lock_handle", unsupported)
    with pytest.raises(storage.DataDirLockedError) as exc:
        storage.acquire_data_dir_lock(root)
    assert exc.value.code == "LOCK_UNSUPPORTED"


def test_live_holder_is_not_taken_over(prod_home, tmp_path):
    root = tmp_path / "lock-holder"
    root.mkdir()
    lock_path = root / ".gw-data.lock"
    script = (
        "import sys,time;"
        "sys.path.insert(0, r'%s');"
        "from pathlib import Path;"
        "from gw.core.storage import _acquire_lock_file;"
        "_lock = _acquire_lock_file(Path(sys.argv[1]), label='holder');"
        "print('ready', flush=True);"
        "time.sleep(8)"
    ) % str(REPO_ROOT)
    process = subprocess.Popen(
        [sys.executable, "-c", script, str(lock_path)],
        cwd=str(REPO_ROOT),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        assert process.stdout.readline().strip() == "ready"
        storage._HELD_DATA_LOCKS.clear()
        with pytest.raises(storage.DataDirLockedError) as exc:
            storage.acquire_data_dir_lock(root)
        assert exc.value.code == "LOCK_HELD"
        # 失败不得删除或改写对方的锁文件。
        assert storage.read_lock_identity(lock_path)["pid"] == process.pid
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:  # pragma: no cover
            process.kill()
ADMIN = {"X-User-Role": "admin", "Authorization": "Bearer cleanroom-test"}


@pytest.fixture()
def api_client(tmp_path, monkeypatch):
    from gw.api.app import create_app
    from gw.core.runtime_paths import resolve_runtime_paths

    reset_write_window()
    runtime_root = resolve_runtime_paths().runtime_root
    for name in ("runtime-root.json", "data-location-journal.json"):
        (runtime_root / name).unlink(missing_ok=True)
    with TestClient(create_app()) as client:
        yield client
    reset_write_window()


def test_data_location_api_reports_four_categories(api_client):
    response = api_client.get("/api/data-location", headers=ADMIN)
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["pointer"]["revision"] == 1
    assert payload["keep_user_data"] is True
    assert [item["key"] for item in payload["categories"]] == [
        "user_data",
        "cache",
        "models",
        "asset_originals",
    ]
    assert payload["categories"][2]["default_migrated"] is False
    assert payload["write_window"]["state"] == STATE_OPEN


def test_data_location_api_check_does_not_write_pointer(api_client, tmp_path):
    target = tmp_path / "api-target"
    response = api_client.post(
        "/api/data-location/check", headers=ADMIN, json={"target": str(target)}
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["models_default_skip"] is True
    assert body["writable"] is True
    # 预检不是迁移：指针版本保持不变，也不创建目标目录。
    assert api_client.get("/api/data-location", headers=ADMIN).json()["pointer"]["revision"] == 1
    assert not target.exists()

