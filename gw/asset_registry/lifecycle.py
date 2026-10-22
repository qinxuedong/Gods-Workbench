# -*- coding: utf-8 -*-
"""DIR-T6 单文件生命周期：操作日志、物理重命名、回收与恢复。"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import threading
import uuid
from pathlib import Path
from typing import Any, Dict

from gw.core import storage
from gw.core.errors import CleanroomException


JOURNAL_RELATIVE = "data/journal/asset-file-operations.jsonl"
_JOURNAL_LOCK = threading.RLock()
_RECYCLER_RELATIVE = "registry_uploads/god-recycler"
_RECYCLER_LOCK = threading.RLock()
_RECOVERY_CHECKED = False
_RECOVERY_LOCK = threading.Lock()


def _journal_path() -> Path:
    return storage.data_root() / JOURNAL_RELATIVE


def _read_journal() -> list[dict[str, Any]]:
    path = _journal_path()
    if not path.exists():
        return []
    records: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            try:
                value = json.loads(line)
            except (ValueError, TypeError):
                continue
            if isinstance(value, dict):
                records.append(value)
    return records


def _append_journal(record: Dict[str, Any]) -> None:
    path = _journal_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with _JOURNAL_LOCK:
        with path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
            handle.flush()
            os.fsync(handle.fileno())


def _replace_journal(records: list[dict[str, Any]]) -> None:
    path = _journal_path()
    temporary = path.with_name(path.name + ".rewrite")
    with temporary.open("w", encoding="utf-8", newline="\n") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def _update_journal(op_id: str, **changes: Any) -> None:
    with _JOURNAL_LOCK:
        records = _read_journal()
        matched = False
        for record in records:
            if record.get("op_id") == op_id:
                record.update(changes)
                matched = True
        if matched:
            _replace_journal(records)


def get_operation(op_id: str) -> Dict[str, Any]:
    _recover_once()
    for record in reversed(_read_journal()):
        if record.get("op_id") == op_id:
            return dict(record)
    raise CleanroomException(404, "FILE_OPERATION_NOT_FOUND", "文件操作日志不存在")


def recover_pending() -> int:
    """启动扫描 pending；无法证明半程状态的操作转人工修复。"""
    recovered = 0
    with _JOURNAL_LOCK:
        records = _read_journal()
        changed = False
        for record in records:
            if record.get("state") != "pending":
                continue
            record.update(
                {
                    "state": "manual_review",
                    "error_code": "FILE_OPERATION_PENDING",
                    "error_message": "进程中断结果无法安全判定；请人工核对文件后处理",
                }
            )
            recovered += 1
            changed = True
        if changed:
            _replace_journal(records)
    return recovered


def _recover_once() -> None:
    """每次首读入口都执行；pending 通常代表上次进程中断，必须重估。"""
    recover_pending()


def _fingerprint(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _local_file(asset_id: str) -> Path:
    from gw.asset_registry import repository as repo

    return _local_file_from_record(repo, repo.get_asset(asset_id))


def _local_file_from_record(repo: Any, item: Dict[str, Any]) -> Path:
    path = None
    asset_id = str(item.get("asset_id") or item.get("id") or "")
    root_id = item.get("root_id")
    display = item.get("display_path")
    if display:
        path = repo._resolve_display(
            str(display), root_id=str(root_id) if root_id else None
        )
    else:
        raw = repo.state().read()
        versions = sorted(
            raw.get("image_versions", {}).get(asset_id, {}).values(),
            key=lambda record: record.get("version_id", ""),
            reverse=True,
        )
        for record in versions:
            display = record.get("display_path")
            if not display:
                continue
            try:
                version_root_id = record.get("root_id") or root_id
                path = repo._resolve_display(
                    str(display),
                    root_id=(
                        str(version_root_id) if version_root_id else None
                    ),
                )
                break
            except CleanroomException:
                continue
    if path is None:
        raise CleanroomException(
            409, "LOCAL_FILE_NOT_REGISTERED", "素材未登记可操作的本地文件"
        )
    return path


def _identity(path: Path) -> Dict[str, str]:
    from gw.asset_library.repository import _local_root_identity

    return {
        "root_id": _local_root_identity(path),
        "canonical_relpath": storage.relative_display(path),
    }


def _file_record(path: Path) -> Dict[str, Any]:
    """生成可恢复路径身份；数据根内产物用相对产物路径表示。"""
    try:
        relative = path.resolve().relative_to(storage.data_root().resolve()).as_posix()
    except ValueError:
        relative = None
    if relative is not None:
        return {"storage_path": relative}
    return _identity(path)


def _next_id(prefix: str) -> str:
    return prefix + storage.now_iso().replace("-", "").replace(":", "") + "_" + uuid.uuid4().hex[:6]


def _safe_file_name(raw_name: Any) -> str:
    value = str(raw_name or "").strip()
    if (
        not value
        or value in {".", ".."}
        or any(char in value for char in '\\/:*?"<>|\r\n\t')
        or any(ord(char) < 32 for char in value)
        or value.endswith((" ", "."))
    ):
        raise CleanroomException(400, "INVALID_PATH", "重命名文件名不合法")
    return value


def rename_file(asset_id: str, payload: Dict[str, Any], actor: Any) -> Dict[str, Any]:
    """同步执行单文件重命名，先落 pending 日志，成功后更新登记与索引。"""
    from gw.asset_library.repository import (
        _local_root_identity,
        _local_state,
        _remove_local_index_path_best_effort,
    )
    from gw.asset_registry import repository as repo

    expected = payload.get("expected_version")
    if type(expected) is not int or expected < 1:
        raise CleanroomException(
            400, "INVALID_REQUEST", "expected_version 必须为正整数"
        )
    source = _local_file(asset_id)
    new_name = _safe_file_name(payload.get("new_name"))
    target = source.with_name(new_name)
    if target.exists():
        raise CleanroomException(409, "FILE_TARGET_EXISTS", "目标文件已存在")
    source_identity = _identity(source)
    target_identity = _identity(target)
    op_id = _next_id("fop_")
    operation = {
        "op_id": op_id,
        "type": "rename",
        "asset_id": asset_id,
        "actor": str(getattr(actor, "subject", "") or getattr(actor, "role", "")),
        "source": source_identity,
        "target": target_identity,
        "fingerprint": _fingerprint(source),
        "state": "pending",
        "started_at": storage.now_iso(),
        "error_code": None,
        "error_message": None,
    }
    _append_journal(operation)
    try:
        from gw.asset_library.repository import move_local_file

        move_local_file(str(source), str(target))
        updated = repo.rename_asset_file(
            asset_id,
            expected,
            target.name,
            target_identity["canonical_relpath"],
        )
    except Exception as exc:
        code = exc.code if isinstance(exc, CleanroomException) else "FILE_RENAME_FAILED"
        message = str(exc) if isinstance(exc, CleanroomException) else "文件重命名失败"
        _update_journal(
            op_id,
            state="failed",
            finished_at=storage.now_iso(),
            error_code=code,
            error_message=message,
        )
        raise
    _update_journal(
        op_id,
        state="done",
        finished_at=storage.now_iso(),
        result={"display_path": target_identity["canonical_relpath"]},
    )
    return {
        **updated,
        "asset_id": asset_id,
        "operation": get_operation(op_id),
        "data_status": "ok",
        "data_gaps": [],
    }


def _recycler_dir(kind: str, op_id: str) -> Path:
    date = storage.now_iso()[:10]
    safe_kind = "".join(ch for ch in kind if ch.isalnum() or ch == "-") or "other"
    return storage.resolve_output_directory(
        f"{_RECYCLER_RELATIVE}/{safe_kind}/{date}/{op_id}", must_exist=False
    )


def recycle_asset(
    asset_id: str, delete_result: Dict[str, Any], actor: Any
) -> Dict[str, Any]:
    """把原文件、manifest 与已登记派生物物理移入受控回收目录。"""
    from gw.asset_library.repository import (
        _local_root_identity,
        _local_state,
        _remove_local_index_path_best_effort,
    )
    from gw.asset_registry import repository as repo

    entry_id = str(delete_result.get("recycle_entry_id") or "")
    raw = repo.state().read()
    entry = raw.get("recycle_bin", {}).get(entry_id)
    if not entry:
        raise CleanroomException(404, "RECYCLE_ENTRY_NOT_FOUND", "回收站条目不存在")
    asset = dict(entry.get("payload") or {})
    if not asset.get("display_path"):
        return None
    source = _local_file_from_record(repo, asset)
    fingerprint = _fingerprint(source)
    op_id = _next_id("fop_")
    directory = _recycler_dir(str(asset.get("kind") or "other"), op_id)
    directory.mkdir(parents=True, exist_ok=True)
    original_target = directory / ("original" + source.suffix.lower())
    if original_target.exists():
        raise CleanroomException(409, "RECYCLE_TARGET_EXISTS", "回收目标路径已存在")
    plan: list[tuple[Path, Path]] = [(source, original_target)]
    metadata = asset.get("metadata") if isinstance(asset.get("metadata"), dict) else {}
    derived_records = metadata.get("derived_assets") if isinstance(metadata.get("derived_assets"), dict) else {}
    for name, record in derived_records.items():
        try:
            derived_root_id = record.get("root_id") or asset.get("root_id")
            candidate = storage.resolve_within_roots(
                str(record.get("path") or ""),
                root_id=str(derived_root_id) if derived_root_id else None,
            )
        except CleanroomException:
            continue
        if not _is_allowed_root_path(candidate):
            # 服务端托管产物归数据目录治理，不随用户本地源文件进回收目录。
            continue
        if not candidate.is_file():
            continue
        derived_target = directory / "derived" / name
        if derived_target.exists():
            raise CleanroomException(409, "RECYCLE_TARGET_EXISTS", "回收目标路径已存在")
        plan.append((candidate, derived_target))
    derived: list[dict[str, Any]] = []
    for plan_source, plan_target in plan[1:]:
        derived.append(
            {
                "name": plan_target.name,
                "path": plan_target.name,
                "source": _file_record(plan_source),
                "recycler_path": plan_target.relative_to(storage.data_root()).as_posix(),
            }
        )

    operation = {
        "op_id": op_id,
        "type": "delete",
        "asset_id": asset_id,
        "actor": str(getattr(actor, "subject", "") or getattr(actor, "role", "")),
        "source": _file_record(source),
        "target": {"recycler_dir": directory.relative_to(storage.data_root()).as_posix()},
        "fingerprint": fingerprint,
        "state": "pending",
        "started_at": storage.now_iso(),
        "error_code": None,
        "error_message": None,
    }
    _append_journal(operation)
    moved_pairs: list[tuple[Path, Path]] = []
    try:
        for plan_source, plan_target in plan:
            plan_target.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(plan_source), str(plan_target))
            moved_pairs.append((plan_source, plan_target))
            try:
                root_identity = _local_root_identity(plan_source)
                _remove_local_index_path_best_effort(
                    _local_state(), storage.relative_display(plan_source), root_identity
                )
            except CleanroomException:
                # 存储产物不在素材允许根内，本就不持有本机素材索引。
                pass
    except Exception as exc:
        for plan_source, plan_target in reversed(moved_pairs):
            if plan_target.exists() and not plan_source.exists():
                try:
                    shutil.move(str(plan_target), str(plan_source))
                except OSError:
                    pass
        code = exc.code if isinstance(exc, CleanroomException) else "RECYCLE_FAILED"
        message = str(exc) if isinstance(exc, CleanroomException) else "文件回收失败"
        _update_journal(
            op_id,
            state="failed",
            finished_at=storage.now_iso(),
            error_code=code,
            error_message=message,
        )
        raise

    derived_paths: list[str] = []
    derived_directory = directory / "derived"
    derived_files = sorted(derived_directory.iterdir(), key=lambda item: item.name) if derived_directory.exists() else []
    for path in derived_files:
        if not path.is_file():
            continue
        derived_paths.append(path.relative_to(storage.data_root()).as_posix())

    manifest = {
        "op_id": op_id,
        "asset_id": asset_id,
        "kind": asset.get("kind") or "other",
        "original": {
            **_identity(source),
            "fingerprint": fingerprint,
            "filename": source.name,
            "recycler_path": original_target.relative_to(storage.data_root()).as_posix(),
        },
        "derived": derived,
        "created_at": storage.now_iso(),
    }
    manifest_path = directory / "manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    recycler = {
        "original_file": manifest["original"]["recycler_path"],
        "manifest": manifest_path.relative_to(storage.data_root()).as_posix(),
        "derived": derived_paths,
    }

    def mutate(raw_state: Dict[str, Any]) -> bool:
        recycle_entry = raw_state.get("recycle_bin", {}).get(entry_id)
        if recycle_entry is None:
            raise CleanroomException(
                404, "RECYCLE_ENTRY_NOT_FOUND", "回收站条目不存在"
            )
        recycle_entry["manifest"] = manifest
        recycle_entry["recycler"] = recycler
        repo._bump(raw_state)
        return True

    repo.state().mutate(mutate)
    record = {
        "op_id": op_id,
        "type": "delete",
        "asset_id": asset_id,
        "actor": str(getattr(actor, "subject", "") or getattr(actor, "role", "")),
        "source": _file_record(source),
        "target": {
            "recycler_dir": directory.relative_to(storage.data_root()).as_posix()
        },
        "fingerprint": fingerprint,
        "state": "done",
        "started_at": manifest["created_at"],
        "finished_at": storage.now_iso(),
        "recycler": recycler,
        "error_code": None,
        "error_message": None,
    }
    _update_journal(
        op_id,
        state="done",
        started_at=manifest["created_at"],
        finished_at=record["finished_at"],
        recycler=recycler,
    )
    return get_operation(op_id)


def restore_asset_files(
    entry_id: str, resolution: str, actor: Any, context_version: int
) -> Dict[str, Any]:
    """按 manifest 恢复原文件与派生物，路径冲突必须显式选择后缀。"""
    from gw.asset_library.repository import (
        _local_root_identity,
        _local_state,
        _remove_local_index_path_best_effort,
    )
    from gw.asset_registry import repository as repo

    if resolution not in {"reject", "suffix"}:
        raise CleanroomException(400, "INVALID_REQUEST", "恢复策略不合法")
    raw = repo.state().read()
    entry = raw.get("recycle_bin", {}).get(entry_id)
    if entry is None:
        raise CleanroomException(404, "RECYCLE_ENTRY_NOT_FOUND", "回收站条目不存在")
    manifest = entry.get("manifest")
    if manifest is None:
        return repo.restore_from_recycle(
            entry_id, actor, expected_version=context_version
        )
    if not isinstance(manifest, dict):
        raise CleanroomException(409, "RESTORE_MANIFEST_MISSING", "回收条目缺少恢复清单")
    op_id = _next_id("fop_")
    original = dict(manifest.get("original") or {})
    source = storage.resolve_output_path(str(original.get("recycler_path") or ""))
    try:
        if _fingerprint(source) != str(original.get("fingerprint") or ""):
            raise ValueError
    except (OSError, ValueError) as exc:
        raise CleanroomException(500, "RESTORE_MANIFEST_INVALID", "回收清单与文件不一致")
    root = _allowed_root_for_identity(str(original.get("root_id") or ""))
    original_target = root / str(original.get("canonical_relpath") or "")
    target = original_target
    suffix_applied = False
    if target.exists():
        if resolution == "reject":
            raise CleanroomException(409, "RESTORE_PATH_CONFLICT", "原路径已存在")
        suffix_applied = True
        suffix_index = 1
        while True:
            candidate = target.with_name(f"{target.stem} ({suffix_index}){target.suffix}")
            if not candidate.exists():
                target = candidate
                break
            suffix_index += 1
    target.parent.mkdir(parents=True, exist_ok=True)
    restore_plan: list[tuple[Path, Path]] = []
    for item in manifest.get("derived", []):
        plan_source = storage.resolve_output_path(str(item.get("recycler_path") or ""))
        item_source = dict(item.get("source") or {})
        storage_path = item_source.get("storage_path")
        if storage_path is not None:
            plan_target = storage.resolve_output_path(
                str(item.get("recycler_path") or ""), must_exist=False
            )
            plan_target.parent.mkdir(parents=True, exist_ok=True)
        else:
            derived_root = _allowed_root_for_identity(str(item_source.get("root_id") or ""))
            plan_target = (
                derived_root / str(item_source.get("canonical_relpath") or "").lstrip("/\\")
            ).resolve()
            plan_target.relative_to(derived_root)
        if plan_target.exists() and suffix_applied:
            suffix_index = 1
            while True:
                candidate = plan_target.with_name(
                    f"{plan_target.stem} ({suffix_index}){plan_target.suffix}"
                )
                if not candidate.exists():
                    plan_target = candidate
                    break
                suffix_index += 1
        restore_plan.append((plan_source, plan_target))
    operation = {
        "op_id": op_id,
        "type": "restore",
        "asset_id": str(entry.get("asset_id") or manifest.get("asset_id") or ""),
        "actor": str(getattr(actor, "subject", "") or getattr(actor, "role", "")),
        "source": {
            "recycler_path": original.get("recycler_path"),
            "canonical_relpath": original.get("canonical_relpath"),
        },
        "target": _identity(target),
        "fingerprint": _fingerprint(source),
        "state": "pending",
        "started_at": storage.now_iso(),
        "resolution": resolution,
        "suffix_applied": suffix_applied,
        "registry_expected_version": context_version,
        "error_code": None,
        "error_message": None,
    }
    _append_journal(operation)
    moved_pairs: list[tuple[Path, Path]] = []
    try:
        for _, plan_target in restore_plan:
            if plan_target.exists():
                raise CleanroomException(
                    409, "RESTORE_PATH_CONFLICT", "派生物恢复路径已存在"
                )
        for plan_source, plan_target in restore_plan:
            if not plan_source.exists() or str(plan_source) == str(plan_target):
                continue
            shutil.move(str(plan_source), str(plan_target))
            moved_pairs.append((plan_source, plan_target))
            if not _is_allowed_root_path(plan_target):
                continue
            try:
                root_identity = _local_root_identity(plan_target)
                _remove_local_index_path_best_effort(
                    _local_state(), storage.relative_display(plan_target), root_identity
                )
            except CleanroomException:
                # 派生物只在回收产物目录间移动时不涉及素材索引。
                pass
        # 回收产物源路径不在素材允许根内，必须走产物移动而非本地素材移动。
        shutil.move(str(source), str(target))
        updated = repo.restore_from_recycle(
            entry_id, actor, expected_version=context_version
        )
    except Exception as exc:
        for moved_source, moved_target in reversed(moved_pairs):
            if moved_target.exists() and not moved_source.exists():
                try:
                    shutil.move(str(moved_target), str(moved_source))
                except Exception:
                    pass
        code = exc.code if isinstance(exc, CleanroomException) else "FILE_RESTORE_FAILED"
        message = str(exc) if isinstance(exc, CleanroomException) else "文件恢复失败"
        _update_journal(
            op_id,
            state="failed",
            finished_at=storage.now_iso(),
            error_code=code,
            error_message=message,
        )
        raise
    _update_journal(
        op_id,
        state="done",
        finished_at=storage.now_iso(),
        result={"display_path": _identity(target)["canonical_relpath"]},
    )
    return {
        **updated,
        "asset_id": entry.get("asset_id"),
        "operation": get_operation(op_id),
        "suffix_applied": suffix_applied,
        "display_path": _identity(target)["canonical_relpath"],
        "data_status": "ok",
        "data_gaps": [],
    }


def _is_allowed_root_path(path: Path) -> bool:
    return any(
        path.resolve().is_relative_to(root.resolve())
        for root in storage.allowed_roots()
    )


def _allowed_root_for_identity(root_id: str) -> Path:
    """按回收清单中的不可逆根身份找到恢复目标允许根。"""
    from gw.asset_library.repository import _local_root_identity

    if not root_id:
        raise CleanroomException(500, "RESTORE_MANIFEST_INVALID", "回收清单缺少允许根身份")
    for root in storage.allowed_roots():
        if _local_root_identity(root) == root_id:
            return root
    raise CleanroomException(409, "RESTORE_ROOT_UNAVAILABLE", "原文件所在允许根当前不可用")
