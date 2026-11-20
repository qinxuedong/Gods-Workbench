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

"""本地数据位置生命周期（QIN-627）。

职责边界：

- 引导指针 ``runtime-root.json`` 的读写与迁移日志 ``data-location-journal.json``；
- 目标预检（绝对路径/链接/系统目录/仓库重叠/空目录/空间/写探针）；
- 冻结窗口内的复制、SQLite 一致性快照、双端 SHA-256 校验与原子切换；
- 成功后的全局只读、失败/崩溃后的恢复矩阵与回退。

本模块直接读写指针与日志（不经 ``storage.save``），因为迁移自身必须能在
写窗口关闭时完成切换；用户数据的写入由 ``gw.core.write_window`` 的五层拦截负责。
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import stat
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from gw.core.errors import CleanroomException
from gw.core.runtime_paths import (
    DATA_LOCATION_SCHEMA_VERSION,
    MODE_TEST,
    PROD_DATA_LOCATION_INVALID,
    RuntimePathError,
    RuntimePaths,
    data_location_pointer_path,
    read_data_location_pointer,
    resolve_runtime_paths,
)
from gw.core.write_window import (
    STATE_OPEN,
    STATE_READ_ONLY,
    WriteWindowClosed,
    get_write_window,
)

POINTER_SCHEMA_VERSION = DATA_LOCATION_SCHEMA_VERSION
JOURNAL_SCHEMA_VERSION = 1
MIGRATION_FORMAT = "gods-workbench-data-location-v1"
STAGE_PREFIX = ".gw-stage-"
CATEGORY_USER_DATA = "user_data"
CATEGORY_CACHE = "cache"
CATEGORY_MODELS = "models"
DEFAULT_CATEGORIES = (CATEGORY_USER_DATA, CATEGORY_CACHE)
ALL_CATEGORIES = (CATEGORY_USER_DATA, CATEGORY_CACHE, CATEGORY_MODELS)
SPACE_HEADROOM_RATIO = 1.10
SPACE_HEADROOM_BYTES = 256 * 1024 * 1024
DRAIN_TIMEOUT_SECONDS = 5.0
COPY_ATTEMPTS = 3

_REPARSE_POINT = 0x0400
_STATE_LOCK = threading.RLock()
_ACTIVE: dict[str, Any] = {"record": None, "cancel": None, "thread": None}


def _fail(status: int, code: str, message: str) -> None:
    raise CleanroomException(status, code, message)


def _paths() -> RuntimePaths:
    return resolve_runtime_paths()


def bootstrap_root() -> Path:
    return _paths().runtime_root


def current_location() -> Path:
    """指针存在时以指针为准，否则回落到默认数据位置（引导目录）。"""
    paths = _paths()
    return paths.data_location if paths.data_location is not None else paths.runtime_root


def journal_path() -> Path:
    return bootstrap_root() / "data-location-journal.json"


# ---------------------------------------------------------------------------
# 指针与日志（原子写，绕过写窗口）
# ---------------------------------------------------------------------------


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        with open(path, encoding="utf-8") as handle:
            payload = json.load(handle)
    except FileNotFoundError:
        return None
    except (OSError, json.JSONDecodeError, TypeError):
        return None
    return payload if isinstance(payload, dict) else None


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    with open(temporary, "w", encoding="utf-8", newline="\n") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=1, sort_keys=True)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def pointer_state() -> dict[str, Any]:
    """返回当前指针视图；缺失时视为 revision 0 + 默认位置。"""
    root = bootstrap_root()
    try:
        payload = read_data_location_pointer(root)
    except RuntimePathError as exc:
        raise CleanroomException(500, exc.code, str(exc)) from None
    if payload is None:
        location = _paths()
        default = location.runtime_root
        return {
            "present": False,
            "schema_version": POINTER_SCHEMA_VERSION,
            "revision": 1,
            "data_location": str(default),
            "previous_location": None,
            "last_migration": None,
        }
    return {
        "present": True,
        "schema_version": int(payload.get("schema_version") or POINTER_SCHEMA_VERSION),
        "revision": int(payload.get("revision") or 1),
        "data_location": str(payload.get("data_location") or ""),
        "previous_location": payload.get("previous_location"),
        "last_migration": payload.get("last_migration"),
    }


def _write_pointer(
    *,
    data_location: Path,
    revision: int,
    previous_location: Path | None,
    last_migration: dict[str, Any] | None,
) -> None:
    payload = {
        "schema_version": POINTER_SCHEMA_VERSION,
        "revision": int(revision),
        "data_location": str(data_location),
        "previous_location": str(previous_location) if previous_location else None,
        "last_migration": last_migration,
        "keep_user_data": True,
        "updated_at": _now(),
    }
    _atomic_write_json(data_location_pointer_path(bootstrap_root()), payload)


def read_journal() -> dict[str, Any] | None:
    return _read_json(journal_path())


def _write_journal(record: dict[str, Any]) -> dict[str, Any]:
    record["updated_at"] = _now()
    _atomic_write_json(journal_path(), record)
    return record


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


# ---------------------------------------------------------------------------
# 路径与预检
# ---------------------------------------------------------------------------


def _inside(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def _reject_links(path: Path) -> None:
    current = Path(path.anchor) if path.anchor else Path()
    parts = path.parts[1:] if path.anchor else path.parts
    for part in parts:
        if not part:
            continue
        current = current / part
        try:
            info = current.lstat()
        except FileNotFoundError:
            continue
        except OSError:
            _fail(400, "REJECTED_TARGET", "目标路径无法安全核验")
        if stat.S_ISLNK(info.st_mode) or (
            getattr(info, "st_file_attributes", 0) & _REPARSE_POINT
        ):
            _fail(400, "REJECTED_TARGET", "目标路径不允许符号链接或 Junction")


def _validate_target(target: Path) -> Path:
    from gw.core import storage

    if not target.is_absolute():
        _fail(400, "DATA_LOCATION_NOT_ABSOLUTE", "数据位置必须是绝对路径")
    _reject_links(target)
    try:
        resolved = target.resolve(strict=False)
    except (OSError, RuntimeError, ValueError):
        _fail(400, "REJECTED_TARGET", "目标路径无法安全解析")
    repo_root = _paths().repo_root
    if _inside(resolved, repo_root) or _inside(repo_root, resolved):
        _fail(400, "REJECTED_TARGET", "数据位置不得与源码仓库重叠")
    reason = storage.describe_resource_folder_rejection(str(resolved))
    if reason:
        _fail(400, "REJECTED_TARGET", f"数据位置不可用：{reason}")
    if resolved.exists():
        if not resolved.is_dir():
            _fail(400, "REJECTED_TARGET", "数据位置必须是目录")
        try:
            if any(resolved.iterdir()):
                _fail(409, "TARGET_NOT_EMPTY", "目标目录必须为空目录或尚不存在")
        except OSError:
            _fail(400, "REJECTED_TARGET", "目标目录无法安全读取")
    return resolved


def _overlap(target: Path) -> bool:
    location = current_location().resolve(strict=False)
    return (
        _inside(target, location)
        or _inside(location, target)
        or target == location
    )


def _anchored_free_bytes(target: Path) -> int:
    anchor = target
    while not anchor.exists():
        parent = anchor.parent
        if parent == anchor:
            break
        anchor = parent
    try:
        return int(shutil.disk_usage(anchor).free)
    except OSError:
        _fail(400, "REJECTED_TARGET", "无法读取目标磁盘空间")
    return 0


def _write_probe(target: Path) -> None:
    parent = target if target.exists() else target.parent
    try:
        parent.mkdir(parents=True, exist_ok=True)
    except OSError:
        _fail(400, "NOT_WRITABLE", "目标目录不可写")
    probe = parent / (".gw-write-probe-" + uuid.uuid4().hex[:8])
    try:
        with open(probe, "wb") as handle:
            handle.write(b"\0" * 1024)
            handle.flush()
            os.fsync(handle.fileno())
    except OSError:
        _fail(400, "NOT_WRITABLE", "目标目录不可写")
    finally:
        try:
            probe.unlink()
        except OSError:
            pass


# ---------------------------------------------------------------------------
# 迁移计划（walker 层 prune，模型默认不读不复制）
# ---------------------------------------------------------------------------


def _skip_dir(name: str, relative: str, categories: set[str]) -> str | None:
    """返回排除原因；``None`` 表示继续遍历。"""
    posix = relative.replace("\\", "/")
    if posix == "data/models" and CATEGORY_MODELS not in categories:
        return "MODEL_CACHE_NOT_SELECTED"
    if posix == "video/work":
        return "VIDEO_WORKDIR_NOT_MIGRATED"
    if posix.startswith("data/models/") and CATEGORY_MODELS not in categories:
        return "MODEL_CACHE_NOT_SELECTED"
    if posix.startswith("video/work/"):
        return "VIDEO_WORKDIR_NOT_MIGRATED"
    return None


def _category_of(relative: str) -> str:
    if relative.startswith("video/"):
        return CATEGORY_CACHE
    return CATEGORY_USER_DATA


def _plan(location: Path, categories: set[str]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """返回 ``(files, excluded)``；目录层直接 prune，不读取被排除目录内文件。"""
    files: list[dict[str, Any]] = []
    excluded: list[dict[str, Any]] = []
    roots = [("data", location / "data")]
    auth = location / "auth" / "auth.sqlite3"
    if auth.exists():
        files.append(_file_entry(auth, "auth/auth.sqlite3", CATEGORY_USER_DATA))
    for video_name in ("artifacts", "proxies", "work"):
        directory = location / "video" / video_name
        if directory.is_dir():
            roots.append((f"video/{video_name}", directory))
    video_db = location / "video" / "video_tasks.sqlite3"
    if video_db.exists():
        files.append(_file_entry(video_db, "video/video_tasks.sqlite3", CATEGORY_CACHE))
    for relative_root, absolute_root in roots:
        if not absolute_root.is_dir():
            continue
        stack: list[tuple[Path, str]] = [(absolute_root, relative_root)]
        while stack:
            directory, relative = stack.pop()
            try:
                entries = sorted(os.scandir(directory), key=lambda item: item.name)
            except OSError:
                raise CleanroomException(409, "SOURCE_UNSTABLE", "源目录无法读取") from None
            for entry in entries:
                child_relative = f"{relative}/{entry.name}"
                reason = _skip_dir(entry.name, child_relative, categories)
                if reason:
                    try:
                        size = sum(
                            item.stat().st_size
                            for item in Path(entry.path).rglob("*")
                            if item.is_file()
                        )
                    except OSError:
                        size = 0
                    excluded.append({"path": child_relative, "reason": reason, "bytes": size})
                    continue
                if entry.is_dir(follow_symlinks=False):
                    stack.append((Path(entry.path), child_relative))
                elif entry.is_file(follow_symlinks=False):
                    files.append(
                        _file_entry(
                            Path(entry.path), child_relative, _category_of(child_relative)
                        )
                    )
                else:
                    raise CleanroomException(
                        409, "SOURCE_UNSTABLE", "源数据包含链接或特殊文件，已拒绝迁移"
                    )
    return files, excluded


def _file_entry(path: Path, relative: str, category: str) -> dict[str, Any]:
    try:
        info = path.stat()
    except OSError:
        raise CleanroomException(409, "SOURCE_UNSTABLE", "源文件无法读取") from None
    return {
        "path": relative,
        "source": path,
        "size": int(info.st_size),
        "mtime_ns": int(info.st_mtime_ns),
        "category": category,
    }


def _plan_total(files: list[dict[str, Any]]) -> int:
    return sum(int(item["size"]) for item in files)


def _selected_categories(raw: list[str] | None) -> set[str]:
    if raw is None:
        return set(DEFAULT_CATEGORIES)
    unknown = {item for item in raw if item not in ALL_CATEGORIES}
    if unknown:
        _fail(400, "INVALID_REQUEST", "迁移类别不受支持")
    categories = {str(item) for item in raw}
    if CATEGORY_USER_DATA not in categories:
        _fail(400, "INVALID_REQUEST", "用户数据与索引必须随迁移一起移动")
    return categories


# ---------------------------------------------------------------------------
# 复制 / 校验 / 切换
# ---------------------------------------------------------------------------


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _checkpoint_databases(location: Path, files: list[dict[str, Any]]) -> None:
    for item in files:
        if not item["path"].endswith(".sqlite3"):
            continue
        source = Path(item["source"])
        try:
            connection = sqlite3.connect(str(source), timeout=10)
            connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            connection.close()
        except sqlite3.Error:
            raise CleanroomException(409, "SOURCE_UNSTABLE", "活动库无法完成 WAL 检查点") from None


def _copy_sqlite(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        source_connection = sqlite3.connect(str(source), timeout=10)
        try:
            destination_connection = sqlite3.connect(str(destination))
            try:
                source_connection.backup(destination_connection)
            finally:
                destination_connection.close()
        finally:
            source_connection.close()
    except sqlite3.Error:
        raise CleanroomException(409, "VERIFY_FAILED", "SQLite 一致性快照失败") from None


def _copy_files(
    files: list[dict[str, Any]],
    stage: Path,
    record: dict[str, Any],
    cancel: threading.Event,
) -> None:
    for item in files:
        if cancel.is_set():
            raise _Cancelled()
        relative = item["path"]
        destination = stage / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        if relative.endswith(".sqlite3"):
            _copy_sqlite(Path(item["source"]), destination)
        else:
            try:
                shutil.copy2(item["source"], destination)
            except OSError:
                raise CleanroomException(409, "VERIFY_FAILED", "数据文件复制失败") from None
        item["sha256"] = _sha256(destination)
        record["copied_bytes"] = int(record.get("copied_bytes") or 0) + int(item["size"])
        record["phase"] = "copying"


def _verify(
    files: list[dict[str, Any]], stage: Path, manifest: dict[str, Any], cancel: threading.Event
) -> None:
    expected = {entry["path"]: entry for entry in manifest["files"]}
    for item in files:
        if cancel.is_set():
            raise _Cancelled()
        relative = item["path"]
        destination = stage / relative
        if not destination.is_file():
            raise CleanroomException(409, "VERIFY_FAILED", "目标缺少清单内文件")
        if _sha256(destination) != item["sha256"]:
            raise CleanroomException(409, "VERIFY_FAILED", "复制文件校验和不一致")
        item["verified_bytes"] = int(item["size"])
        manifest["verified_bytes"] = int(manifest.get("verified_bytes") or 0) + int(
            item["size"]
        )
    for path in stage.rglob("*"):
        relative = path.relative_to(stage).as_posix()
        if path.is_file() and relative not in expected and relative != "manifest.json":
            raise CleanroomException(409, "VERIFY_FAILED", "目标出现清单外文件")
    for relative, entry in expected.items():
        if not relative.endswith(".sqlite3"):
            continue
        try:
            connection = sqlite3.connect(str(stage / relative), timeout=10)
            check = connection.execute("PRAGMA integrity_check").fetchone()
            connection.close()
        except sqlite3.Error:
            raise CleanroomException(409, "VERIFY_FAILED", "SQLite 副本完整性检查失败") from None
        if not check or check[0] != "ok":
            raise CleanroomException(409, "VERIFY_FAILED", "SQLite 副本完整性检查失败")
        entry["integrity"] = "ok"


def _source_changed(files: list[dict[str, Any]]) -> bool:
    for item in files:
        try:
            info = Path(item["source"]).stat()
        except OSError:
            return True
        if int(info.st_size) != int(item["size"]) or int(info.st_mtime_ns) != int(
            item["mtime_ns"]
        ):
            return True
    return False


def _promote(stage: Path, target: Path) -> None:
    target.mkdir(parents=True, exist_ok=True)
    for child in sorted(stage.iterdir(), key=lambda item: item.name):
        destination = target / child.name
        if destination.exists():
            raise CleanroomException(409, "SWITCH_FAILED", "目标出现冲突条目")
        try:
            os.replace(child, destination)
        except OSError:
            if child.is_dir():
                shutil.copytree(child, destination)
                shutil.rmtree(child, ignore_errors=True)
            else:
                shutil.copy2(child, destination)
                child.unlink()
    try:
        stage.rmdir()
    except OSError:
        pass


def _remove_stage(target: Path, migration_id: str) -> None:
    stage = target / (STAGE_PREFIX + migration_id)
    if stage.is_dir():
        shutil.rmtree(stage, ignore_errors=True)


class _Cancelled(Exception):
    pass


# ---------------------------------------------------------------------------
# 迁移入口与状态
# ---------------------------------------------------------------------------


def _public_migration(record: dict[str, Any] | None) -> dict[str, Any] | None:
    if not record:
        return None
    view = {
        key: record.get(key)
        for key in (
            "migration_id",
            "status",
            "phase",
            "target",
            "source_location",
            "categories",
            "total_bytes",
            "copied_bytes",
            "verified_files",
            "verified_bytes",
            "cache_models_default_skip",
            "excluded",
            "error_code",
            "message",
            "created_at",
            "updated_at",
            "restart_required",
        )
    }
    total = int(record.get("total_bytes") or 0)
    copied = int(record.get("copied_bytes") or 0)
    view["percent"] = round(copied * 100.0 / total, 1) if total else 0.0
    return view


def _excluded_summary(excluded: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {"path": item.get("path"), "reason": item.get("reason"), "bytes": item.get("bytes")}
        for item in excluded
    ]


def status_snapshot() -> dict[str, Any]:
    window = get_write_window()
    try:
        pointer = pointer_state()
        pointer_error = None
    except CleanroomException as exc:
        pointer = None
        pointer_error = {"code": exc.code, "message": exc.message}
    location = current_location()
    disk = {}
    try:
        disk = {
            "free_bytes": int(shutil.disk_usage(location).free),
            "total_bytes": int(shutil.disk_usage(location).total),
        }
    except OSError:
        disk = {"free_bytes": None, "total_bytes": None}
    with _STATE_LOCK:
        record = _ACTIVE["record"]
    return {
        "data_location": str(location),
        "bootstrap_root": str(bootstrap_root()),
        "pointer": pointer,
        "pointer_error": pointer_error,
        "categories": [
            {
                "key": CATEGORY_USER_DATA,
                "label": "用户数据与索引",
                "path": str(location / "data"),
                "default_migrated": True,
            },
            {
                "key": CATEGORY_CACHE,
                "label": "缓存输出",
                "path": str(location / "video"),
                "default_migrated": True,
            },
            {
                "key": CATEGORY_MODELS,
                "label": "模型缓存",
                "path": str(location / "data" / "models"),
                "default_migrated": False,
            },
            {
                "key": "asset_originals",
                "label": "素材原件",
                "path": None,
                "default_migrated": False,
                "note": "由资源文件夹持有，永不迁移、永不复制",
            },
        ],
        "disk": disk,
        "write_window": window.snapshot(),
        "active_jobs": window.active_jobs(),
        "migration": _public_migration(record),
        "keep_user_data": True,
        "restart_hint": restart_hint(),
    }


def restart_hint() -> dict[str, Any]:
    window = get_write_window()
    pointer = _read_json(data_location_pointer_path(bootstrap_root())) or {}
    required = window.state == STATE_READ_ONLY
    return {
        "required": required,
        "state": window.state,
        "data_location": pointer.get("data_location"),
        "previous_location": pointer.get("previous_location"),
        "message": (
            "数据位置已切换，重启后生效；当前为只读状态"
            if required
            else "当前无需重启"
        ),
    }


def check_target(target: str, categories: list[str] | None = None) -> dict[str, Any]:
    resolved = _validate_target(Path(target))
    if _overlap(resolved):
        _fail(409, "TARGET_OVERLAP", "目标不得与当前数据位置互相包含")
    selected = _selected_categories(categories)
    location = current_location()
    files, excluded = _plan(location, selected)
    required = int(_plan_total(files) * SPACE_HEADROOM_RATIO) + SPACE_HEADROOM_BYTES
    free = _anchored_free_bytes(resolved)
    if free < required:
        _fail(
            507,
            "INSUFFICIENT_SPACE",
            "目标空间不足，需要至少 %d 字节可用" % required,
        )
    _write_probe(resolved)
    return {
        "target": str(resolved),
        "required_bytes": required,
        "free_bytes": free,
        "categories": sorted(selected),
        "files": len(files),
        "excluded": _excluded_summary(excluded),
        "writable": True,
        "models_default_skip": CATEGORY_MODELS not in selected,
    }


def start_migration(
    target: str, expected_version: int, categories: list[str] | None = None
) -> dict[str, Any]:
    window = get_write_window()
    pointer = pointer_state()
    if int(expected_version) != int(pointer["revision"]):
        raise CleanroomException(409, "DATA_LOCATION_VERSION_CONFLICT", "数据位置版本冲突，请刷新后重试")
    if window.state != STATE_OPEN:
        raise WriteWindowClosed(
            "DATA_LOCATION_RESTART_REQUIRED" if window.state == STATE_READ_ONLY else "DATA_LOCATION_QUIESCING",
            "当前写窗口不允许开始新的迁移",
        )
    with _STATE_LOCK:
        if _ACTIVE["record"] is not None and _ACTIVE["record"].get("status") not in {
            "completed",
            "failed",
            "cancelled",
            "rolled_back",
        }:
            _fail(409, "MIGRATION_BUSY", "已有迁移在进行中")
    active = window.active_jobs()
    if active:
        raise CleanroomException(
            409,
            "MIGRATION_BUSY",
            "存在活动后台任务，请先完成或取消后再迁移",
        )
    resolved = _validate_target(Path(target))
    if _overlap(resolved):
        _fail(409, "TARGET_OVERLAP", "目标不得与当前数据位置互相包含")
    selected = _selected_categories(categories)
    location = current_location()
    files, excluded = _plan(location, selected)
    total = _plan_total(files)
    required = int(total * SPACE_HEADROOM_RATIO) + SPACE_HEADROOM_BYTES
    free = _anchored_free_bytes(resolved)
    if free < required:
        _fail(507, "INSUFFICIENT_SPACE", "目标空间不足，需要至少 %d 字节可用" % required)
    _write_probe(resolved)

    migration_id = uuid.uuid4().hex
    record: dict[str, Any] = {
        "migration_id": migration_id,
        "status": "planned",
        "phase": "planned",
        "target": str(resolved),
        "source_location": str(location),
        "categories": sorted(selected),
        "total_bytes": total,
        "copied_bytes": 0,
        "verified_files": 0,
        "verified_bytes": 0,
        "cache_models_default_skip": CATEGORY_MODELS not in selected,
        "excluded": _excluded_summary(excluded),
        "base_revision": int(pointer["revision"]),
        "generation": window.generation + 1,
        "freeze_token": None,
        "error_code": None,
        "message": None,
        "restart_required": False,
        "created_at": _now(),
    }
    _write_journal(record)
    cancel = threading.Event()
    thread = threading.Thread(
        target=_run_migration,
        args=(record, files, resolved, cancel),
        name="gw-data-location",
        daemon=True,
    )
    with _STATE_LOCK:
        _ACTIVE.update({"record": record, "cancel": cancel, "thread": thread})
    thread.start()
    return _public_migration(record) or {}


def _mark(
    record: dict[str, Any],
    *,
    status: str,
    phase: str | None = None,
    error_code: str | None = None,
    message: str | None = None,
) -> None:
    record["status"] = status
    record["phase"] = phase or status
    record["error_code"] = error_code
    record["message"] = message
    _write_journal(record)


def _run_migration(
    record: dict[str, Any],
    files: list[dict[str, Any]],
    target: Path,
    cancel: threading.Event,
) -> None:
    window = get_write_window()
    migration_id = record["migration_id"]
    stage = target / (STAGE_PREFIX + migration_id)
    pointer_committed = False
    try:
        _mark(record, status="checking_space", phase="checking_space")
        token = window.begin_quiescing(reason=migration_id)
        record["freeze_token"] = token
        _mark(record, status="quiescing", phase="quiescing")
        if not window.wait_drained(timeout=DRAIN_TIMEOUT_SECONDS):
            window.reset_open(reason="drain_timeout")
            _mark(record, status="failed", error_code="SOURCE_UNSTABLE", message="在飞写未能在 5 秒内排空")
            return
        if cancel.is_set():
            window.reset_open(reason="cancelled")
            _mark(record, status="cancelled", phase="cancelled")
            return
        # 排空后重新取一次清单：在飞请求可能新建/改写了文件，计划期快照已过时。
        fresh_files, fresh_excluded = _plan(
            current_location(), set(record.get("categories") or [])
        )
        files[:] = fresh_files
        record["excluded"] = _excluded_summary(fresh_excluded)
        record["total_bytes"] = _plan_total(fresh_files)
        # 进入 QUIESCING 后新任务已被拦截；此时再确认没有宿主仍在执行，
        # 避免「预检时归零、冻结前又启动」的竞态导致边复制边写。
        active = window.active_jobs()
        if active:
            window.reset_open(reason="active_jobs")
            _mark(
                record,
                status="failed",
                error_code="MIGRATION_BUSY",
                message="后台任务未在冻结前结束：" + ", ".join(sorted(active)),
            )
            return
        window.mark_frozen()
        _checkpoint_databases(current_location(), files)
        _mark(record, status="copying", phase="copying")
        attempt = 0
        while True:
            attempt += 1
            try:
                stage.mkdir(parents=True, exist_ok=False)
                record["copied_bytes"] = 0
                _copy_files(files, stage, record, cancel)
                if _source_changed(files):
                    raise CleanroomException(409, "SOURCE_UNSTABLE", "冻结后源数据发生变化")
                _mark(record, status="verifying", phase="verifying")
                manifest = {
                    "format": MIGRATION_FORMAT,
                    "migration_id": migration_id,
                    "created_at": record["created_at"],
                    "freeze_token": record.get("freeze_token"),
                    "total_bytes": record["total_bytes"],
                    "files": [
                        {
                            "path": item["path"],
                            "size": int(item["size"]),
                            "sha256": item.get("sha256"),
                            "category": item["category"],
                        }
                        for item in files
                    ],
                    "excluded": record["excluded"],
                    "verified_bytes": 0,
                }
                record["verified_files"] = 0
                _verify(files, stage, manifest, cancel)
                record["verified_files"] = len(files)
                record["verified_bytes"] = int(manifest["verified_bytes"])
                _atomic_write_json(stage / "manifest.json", manifest)
                break
            except CleanroomException as exc:
                shutil.rmtree(stage, ignore_errors=True)
                if exc.code == "SOURCE_UNSTABLE" and attempt < COPY_ATTEMPTS and not cancel.is_set():
                    time.sleep(0.2)
                    continue
                raise
        window.mark_switching()
        _mark(record, status="promoting", phase="promoting")
        _promote(stage, target)
        pointer = pointer_state()
        _write_pointer(
            data_location=target,
            revision=int(pointer["revision"]) + 1,
            previous_location=current_location(),
            last_migration={
                "id": migration_id,
                "status": "completed",
                "phase": "completed",
                "verified_files": record["verified_files"],
                "verified_bytes": record["verified_bytes"],
                "excluded_categories": [
                    item.get("reason") for item in record["excluded"] if item.get("reason")
                ],
            },
        )
        pointer_committed = True
        record["restart_required"] = True
        # 先进入只读再落 completed，保证外部观察到 completed 时写窗口已经关闭。
        window.mark_read_only(reason="migration_completed")
        _mark(record, status="completed", phase="completed")
    except _Cancelled:
        shutil.rmtree(stage, ignore_errors=True)
        window.reset_open(reason="cancelled")
        _mark(record, status="cancelled", phase="cancelled")
    except CleanroomException as exc:
        shutil.rmtree(stage, ignore_errors=True)
        if not pointer_committed:
            window.reset_open(reason="migration_failed")
            _mark(record, status="failed", error_code=exc.code, message=exc.message)
        else:  # pragma: no cover - 指针已提交后只可能停在只读
            window.mark_read_only(reason="pointer_committed")
            _mark(record, status="completed", phase="completed")
    except Exception:  # pragma: no cover - 兜底不吞掉状态
        shutil.rmtree(stage, ignore_errors=True)
        if not pointer_committed:
            window.reset_open(reason="unexpected")
            _mark(record, status="failed", error_code="SWITCH_FAILED", message="迁移发生未预期错误")
        else:
            window.mark_read_only(reason="pointer_committed")
            _mark(record, status="completed", phase="completed")
    finally:
        with _STATE_LOCK:
            thread = _ACTIVE.get("thread")
            if thread is threading.current_thread():
                _ACTIVE["cancel"] = None


def migration_status(migration_id: str) -> dict[str, Any]:
    with _STATE_LOCK:
        record = _ACTIVE["record"]
    if record is None or record.get("migration_id") != migration_id:
        journal = read_journal()
        if journal is None or journal.get("migration_id") != migration_id:
            _fail(404, "MIGRATION_NOT_FOUND", "迁移任务不存在")
        return _public_migration(journal) or {}
    return _public_migration(record) or {}


def cancel_migration(migration_id: str) -> dict[str, Any]:
    with _STATE_LOCK:
        record = _ACTIVE["record"]
        cancel = _ACTIVE["cancel"]
    if record is None or record.get("migration_id") != migration_id:
        _fail(404, "MIGRATION_NOT_FOUND", "迁移任务不存在")
    if record.get("status") not in {"planned", "checking_space", "quiescing", "copying", "verifying"}:
        _fail(409, "MIGRATION_BUSY", "当前阶段不允许取消")
    if cancel is not None:
        cancel.set()
    return _public_migration(record) or {}


def rollback() -> dict[str, Any]:
    window = get_write_window()
    if window.state != STATE_READ_ONLY:
        _fail(409, "ROLLBACK_REFUSED", "仅只读窗口（切换后重启前）允许回退")
    pointer = pointer_state()
    previous = pointer.get("previous_location")
    if not previous:
        _fail(409, "ROLLBACK_REFUSED", "没有可回退的上一个数据位置")
    previous_path = Path(str(previous))
    if not (previous_path / "data").is_dir() or not (
        previous_path / "auth" / "auth.sqlite3"
    ).is_file():
        _fail(409, "ROLLBACK_REFUSED", "上一个数据位置不完整，拒绝回退")
    current = Path(str(pointer["data_location"]))
    _write_pointer(
        data_location=previous_path,
        revision=int(pointer["revision"]) + 1,
        previous_location=current,
        last_migration={
            "id": (pointer.get("last_migration") or {}).get("id"),
            "status": "rolled_back",
            "phase": "rolled_back",
        },
    )
    journal = read_journal() or {}
    journal.update(
        {
            "schema_version": JOURNAL_SCHEMA_VERSION,
            "migration_id": journal.get("migration_id") or (pointer.get("last_migration") or {}).get("id") or "",
            "status": "rolled_back",
            "phase": "rolled_back",
            "error_code": "ROLLED_BACK",
            "message": "已回退到上一个数据位置，重启后生效",
            "restart_required": True,
        }
    )
    _write_journal(journal)
    return {
        "status": "rolled_back",
        "data_location": str(previous_path),
        "previous_location": str(current),
        "restart_required": True,
        "write_window": window.snapshot(),
    }


def recover_on_startup() -> dict[str, Any]:
    """按 journal 阶段 + 指针 revision + stage/final 事实收敛，绝不产生两个可写根。"""
    result: dict[str, Any] = {"action": "none"}
    journal = read_journal()
    if journal is None:
        return result
    status = str(journal.get("status") or "")
    target = Path(str(journal.get("target") or "")) if journal.get("target") else None
    migration_id = str(journal.get("migration_id") or "")
    base_revision = int(journal.get("base_revision") or 0)
    pointer = pointer_state()
    revision = int(pointer["revision"])
    if status in {"completed", "rolled_back"}:
        return result
    if status in {"planned", "checking_space", "quiescing", "copying", "verifying"}:
        if target is not None and migration_id:
            _remove_stage(target, migration_id)
        journal.update({"status": "failed", "phase": "failed", "error_code": "MIGRATION_INTERRUPTED"})
        _write_journal(journal)
        return {"action": "cleaned_stage", "migration_id": migration_id}
    if status == "promoting":
        if revision > base_revision:
            journal.update({"status": "completed", "phase": "completed"})
            _write_journal(journal)
            return {"action": "marked_completed", "migration_id": migration_id}
        if target is not None and target.is_dir():
            for child in sorted(target.iterdir(), key=lambda item: item.name):
                shutil.rmtree(child, ignore_errors=True) if child.is_dir() else child.unlink(
                    missing_ok=True
                )
        journal.update({"status": "failed", "phase": "failed", "error_code": "SWITCH_FAILED"})
        _write_journal(journal)
        return {"action": "removed_final", "migration_id": migration_id}
    if status == "failed" and target is not None and migration_id:
        _remove_stage(target, migration_id)
    return result


def validate_pointer_or_raise(exc: RuntimePathError) -> None:  # pragma: no cover - 兼容钩子
    if exc.code == PROD_DATA_LOCATION_INVALID:
        raise CleanroomException(500, exc.code, str(exc)) from None
    raise exc


__all__ = [
    "ALL_CATEGORIES",
    "CATEGORY_CACHE",
    "CATEGORY_MODELS",
    "CATEGORY_USER_DATA",
    "DEFAULT_CATEGORIES",
    "MODE_TEST",
    "bootstrap_root",
    "cancel_migration",
    "check_target",
    "current_location",
    "journal_path",
    "migration_status",
    "pointer_state",
    "read_journal",
    "recover_on_startup",
    "restart_hint",
    "rollback",
    "start_migration",
    "status_snapshot",
    "validate_pointer_or_raise",
]
