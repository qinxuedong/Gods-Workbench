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

"""收集箱（QIN-547）：统一的默认收集目录。

边界与不变量：

- 收集箱本身是**真实磁盘文件夹**，默认 ``data_root()/inbox``，位置可在设置里改；
- 改位置时可选「迁移已有文件」；同一位置重复提交是幂等的（不产生重复数据）；
- 列表同时呈现三类来源：收集箱本地文件、遗留 ``registry_uploads/collect_inbox`` 收集文件、
  托管资产迁移后的 URL 条目；
- 迁移托管资产按 ``asset_id`` 记账，可重复执行；外部 URL 保留为 URL 条目，
  仅 ``file://`` 或绝对路径且真实存在、位于已挂载素材根内的本地文件才会进收集箱；
- 迁移**不移动、不删除源文件**（硬链接优先、跨卷回退复制）：托管资产记录里的 url
  仍指向原路径，``shutil.move`` 会让这些记录变成死链；
- 迁移失败**不入账**，下次调用会重试，不会因为一次失败就被永久跳过；
- 迁移不删除任何托管资产 JSON 记录，也不改 ``collector.store_content`` 的受管落盘路径；
- 主体隔离：迁移记录与「已查看」时间点按 ``sha256(owner)`` 分账，遗留收集目录只列
  当前主体的子目录，避免把别人的收集结果展示出来。
"""

from __future__ import annotations

import hashlib
import os
import shutil
import threading
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote, unquote, urlsplit

from gw.asset_library.repository import classify_name
from gw.core import storage
from gw.core.asset_visibility import current_asset_owner
from gw.core.errors import CleanroomException

NS_INBOX = "asset_inbox"
INBOX_DIRNAME = "inbox"
#: 单个收集文件上限，与本地素材上传保持一致。
MAX_INBOX_UPLOAD_BYTES = 32 * 1024 * 1024
#: 遗留浏览器收集目录（相对 data_root）；只读展示，不搬迁，避免破坏受管回读契约。
LEGACY_COLLECT_PARTS = ("registry_uploads", "collect_inbox")
#: GNS-2026 要求物理落盘名纯 ASCII，用户原名只作为展示名保存在映射表里。
PHYSICAL_NAME_PREFIX = "inbox_"
DISPLAY_NAMES_KEY = "display_names"

_lock = threading.RLock()


def _default_raw() -> dict[str, Any]:
    return {
        "version": 2,
        "path": None,
        "last_viewed_at": None,
        "last_viewed_epoch": None,
        "views": {},
        "migrated": {},
        DISPLAY_NAMES_KEY: {},
    }


def owner_digest() -> str | None:
    """当前请求主体的稳定摘要；没有主体上下文时返回 None。"""
    owner = current_asset_owner.get()
    if not owner:
        return None
    return hashlib.sha256(str(owner).encode("utf-8")).hexdigest()


#: 旧内部名保留为别名，既有调用点不需要一次全量改名。
_owner_digest = owner_digest


def _state() -> storage.JsonState:
    return storage.JsonState(NS_INBOX, _default_raw)


# ---------------------------------------------------------------------------
# 位置解析
# ---------------------------------------------------------------------------


def default_inbox_root() -> Path:
    """默认收集箱位置：数据根目录下的真实文件夹。"""
    return storage.data_root() / INBOX_DIRNAME


def inbox_root(raw: dict[str, Any] | None = None) -> Path:
    """当前生效的收集箱目录（未配置时回落默认位置）。"""
    payload = raw if raw is not None else _state().read()
    configured = str(payload.get("path") or "").strip()
    return Path(configured) if configured else default_inbox_root()


def _resolve_configured_dir(raw_path: Any) -> Path:
    text = str(raw_path or "").strip()
    if not text:
        raise CleanroomException(400, "INVALID_PATH", "收集箱位置不能为空")
    candidate = Path(text).expanduser()
    if not candidate.is_absolute():
        raise CleanroomException(400, "INVALID_PATH", "收集箱位置必须是绝对路径")
    try:
        resolved = candidate.resolve(strict=False)
    except (OSError, RuntimeError, ValueError):
        raise CleanroomException(400, "INVALID_PATH", "收集箱位置无法解析") from None
    if resolved.exists() and not resolved.is_dir():
        raise CleanroomException(400, "INVALID_PATH", "收集箱位置必须是文件夹")
    return resolved


def _ensure_dir(path: Path) -> Path:
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError:
        raise CleanroomException(
            503, "INBOX_DIRECTORY_UNAVAILABLE", "收集箱目录不可写"
        ) from None
    return path


def _ascii_suffix(name: Any) -> str:
    """只保留纯 ASCII 字母数字扩展名，其余一律丢弃。"""
    suffix = Path(str(name or "").replace("\\", "/")).suffix.lower()
    if not suffix or len(suffix) > 11:
        return ""
    body = suffix[1:]
    if not body.isascii() or not body.isalnum():
        return ""
    return suffix


def _physical_name(name: Any) -> str:
    """GNS-2026：物理落盘名纯 ASCII，用户原名不进入磁盘名。"""
    return f"{PHYSICAL_NAME_PREFIX}{uuid.uuid4().hex[:12]}{_ascii_suffix(name)}"


def _unique_physical(root: Path, name: Any) -> str:
    candidate = _physical_name(name)
    while (root / candidate).exists():
        candidate = _physical_name(name)
    return candidate


def _display_name(raw: dict[str, Any], relative: str) -> str:
    """物理名对应的展示名；没有映射时回落到物理名本身。"""
    names = raw.get(DISPLAY_NAMES_KEY)
    if isinstance(names, dict):
        value = str(names.get(relative) or "").strip()
        if value:
            return value
    return Path(relative).name


def _unique_display(raw: dict[str, Any], name: Any, extra: dict[str, str] | None = None) -> str:
    """展示名去重：同名素材显示为 ``shot (2).png``，磁盘上各用各自的 ASCII 物理名。"""
    base = Path(str(name or "").replace("\\", "/")).name.strip()
    if base in {"", ".", ".."}:
        base = "collected.bin"
    names = raw.get(DISPLAY_NAMES_KEY)
    taken = {str(value) for value in names.values()} if isinstance(names, dict) else set()
    if extra:
        taken.update(str(value) for value in extra.values())
    if base not in taken:
        return base
    stem, suffix = os.path.splitext(base)
    index = 2
    while f"{stem} ({index}){suffix}" in taken:
        index += 1
    return f"{stem} ({index}){suffix}"


def _is_within(child: Path, parent: Path) -> bool:
    try:
        child.relative_to(parent)
    except ValueError:
        return False
    return True


# ---------------------------------------------------------------------------
# 列表与角标
# ---------------------------------------------------------------------------


def _timestamp(value: Any) -> float:
    text = str(value or "").strip()
    if not text:
        return 0.0
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


def _iter_files(root: Path) -> list[Path]:
    """递归枚举真实普通文件；跳过软链接，绝不跟随到收集箱之外。

    根目录不可读时失败关闭（503），不把「读不到」伪装成「没有文件」。
    """
    if not root.is_dir():
        return []
    try:
        root_entries = list(os.scandir(root))
    except OSError:
        raise CleanroomException(
            503, "INBOX_DIRECTORY_UNAVAILABLE", "收集箱目录不可读"
        ) from None
    files: list[Path] = []
    stack = [root]
    while stack:
        current = stack.pop()
        if current == root:
            entries = root_entries
        else:
            try:
                entries = list(os.scandir(current))
            except OSError:
                # 单个子目录损坏只影响自身，不牵连整个收集箱。
                continue
        for entry in entries:
            try:
                if entry.is_symlink():
                    continue
                if entry.is_dir(follow_symlinks=False):
                    stack.append(Path(entry.path))
                elif entry.is_file(follow_symlinks=False):
                    files.append(Path(entry.path))
            except OSError:
                continue
    return sorted(files, key=lambda path: str(path).casefold())


def _local_items(
    root: Path,
    source: str,
    raw: dict[str, Any] | None = None,
    prefix: str = "",
) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    payload = raw or {}
    for path in _iter_files(root):
        try:
            relative = path.relative_to(root).as_posix()
            stat = path.stat()
        except (OSError, ValueError):
            continue
        display = _display_name(payload, relative) if source == "inbox" else path.name
        location = relative if not prefix else f"{prefix}/{relative}"
        items.append(
            {
                "id": f"{source}:{relative}",
                "source": source,
                "name": display,
                "file": location,
                "kind": classify_name(display),
                "size_bytes": stat.st_size,
                "modified_at": int(stat.st_mtime),
                "_epoch": stat.st_mtime,
                "url": None,
                "asset_id": None,
            }
        )
    return items


def _url_items(raw: dict[str, Any]) -> list[dict[str, Any]]:
    """托管资产迁移后保留在收集箱里的 URL 条目（本地文件已迁走的记录不再重复展示）。"""
    items: list[dict[str, Any]] = []
    viewer = _owner_digest()
    for asset_id, record in sorted(raw.get("migrated", {}).items()):
        if not isinstance(record, dict) or record.get("file"):
            continue
        # 迁移记录按主体分账：别人的记录既不计角标也不展示（缺 owner 的是升级前的老账，照旧可见）。
        record_owner = record.get("owner")
        if record_owner and viewer and record_owner != viewer:
            continue
        items.append(
            {
                "id": f"asset:{asset_id}",
                "source": "asset_library",
                "name": str(record.get("name") or asset_id),
                "file": None,
                "kind": str(record.get("kind") or "url"),
                "size_bytes": 0,
                "modified_at": int(_timestamp(record.get("migrated_at"))),
                "_epoch": _timestamp(record.get("migrated_at")),
                "url": record.get("url"),
                "asset_id": asset_id,
            }
        )
    return items


def _legacy_owner_root() -> Path | None:
    """遗留收集目录按 ``sha256(owner)`` 分主体；只返回当前主体的子目录。

    没有请求主体上下文时返回 None：宁可少列，也不能把别人的收集结果展示出来。
    """
    digest = _owner_digest()
    if not digest:
        return None
    return storage.data_root().joinpath(*LEGACY_COLLECT_PARTS, digest)


def _all_items(raw: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    payload = raw if raw is not None else _state().read()
    # 只读接口也必须保证收集箱是真实文件夹；不可用时失败关闭，不伪装成空箱。
    root = _ensure_dir(inbox_root(payload))
    items = _local_items(root, "inbox", payload)
    legacy_root = _legacy_owner_root()
    if legacy_root is not None and legacy_root.resolve(strict=False) != root.resolve(strict=False):
        digest = legacy_root.name
        legacy_prefix = Path(*LEGACY_COLLECT_PARTS, digest).as_posix()
        items.extend(_local_items(legacy_root, "collect_inbox", payload, legacy_prefix))
    items.extend(_url_items(payload))
    # 【QIN-588】每个本地条目下发受控预览查询串（url 条目为 None）：前端零路径知识，
    # 预览 URL 全部由服务端授权的参数拼装；无主体时遗留条目不下发（失败关闭）。
    for item in items:
        item["preview_query"] = inbox_preview_query(item)
    items.sort(key=lambda item: (-int(item["modified_at"]), str(item["name"]).casefold()))
    return items


def _viewed_epoch(raw: dict[str, Any]) -> float:
    """当前主体最近一次「看过收集箱」的时间点；按主体分账，互不影响。"""
    digest = _owner_digest()
    if not digest:
        return float(raw.get("last_viewed_epoch") or 0)
    views = raw.get("views")
    views = views if isinstance(views, dict) else {}
    if digest in views:
        return float(views.get(digest) or 0)
    if not views:
        # 升级前的全局值：单主体时沿用，避免升级后角标凭空全亮。
        return float(raw.get("last_viewed_epoch") or 0)
    return 0.0


def _unread_count(items: list[dict[str, Any]], raw: dict[str, Any]) -> int:
    viewed = _viewed_epoch(raw)
    if viewed <= 0:
        return len(items)
    return sum(1 for item in items if float(item.get("_epoch") or 0) > viewed)


def _visible_migrated(raw: dict[str, Any]) -> dict[str, Any]:
    """当前主体可见的迁移记录（别人的记录不参与统计）。"""
    viewer = _owner_digest()
    records = raw.get("migrated")
    if not isinstance(records, dict):
        return {}
    if not viewer:
        return records
    return {
        asset_id: record
        for asset_id, record in records.items()
        if not isinstance(record, dict) or not record.get("owner") or record.get("owner") == viewer
    }


def settings_snapshot(raw: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = raw if raw is not None else _state().read()
    root = inbox_root(payload)
    items = _all_items(payload)
    return {
        "path": str(root),
        "default_path": str(default_inbox_root()),
        "is_default": not str(payload.get("path") or "").strip(),
        "exists": root.is_dir(),
        "count": len(items),
        "unread_count": _unread_count(items, payload),
        "last_viewed_at": payload.get("last_viewed_at"),
        "last_viewed_epoch": _viewed_epoch(payload) or None,
        "migrated_asset_count": len(_visible_migrated(payload)),
        "data_status": "ok" if items else "empty",
    }


def list_items(
    limit: int = 100,
    offset: int = 0,
    source: str | None = None,
) -> dict[str, Any]:
    if type(limit) is not int or not 1 <= limit <= 500:
        raise CleanroomException(400, "INVALID_LIMIT", "limit 必须为 1 到 500 的整数")
    if type(offset) is not int or offset < 0:
        raise CleanroomException(400, "INVALID_OFFSET", "offset 必须为非负整数")
    raw = _state().read()
    items = _all_items(raw)
    total = len(items)
    unread = _unread_count(items, raw)
    if source:
        items = [item for item in items if item["source"] == source]
    page = [
        {key: value for key, value in item.items() if not key.startswith("_")}
        for item in items[offset : offset + limit]
    ]
    return {
        "items": page,
        "count": len(items),
        "total": total,
        "unread_count": unread,
        "limit": limit,
        "offset": offset,
        "has_more": offset + limit < len(items),
        "source": source,
        "settings": settings_snapshot(raw),
        "data_status": "ok" if items else "empty",
    }


def mark_viewed() -> dict[str, Any]:
    """记录「已看过收集箱」时间点，角标按该时间点之后的新素材计算。

    时间点按主体分账保存，一个账号点开不会把另一个账号的角标一起清零。
    """

    def mutate(raw: dict[str, Any]) -> dict[str, Any]:
        epoch = time.time()
        raw["last_viewed_at"] = storage.now_iso()
        raw["last_viewed_epoch"] = epoch
        digest = _owner_digest()
        if digest:
            views = raw.get("views")
            views = views if isinstance(views, dict) else {}
            views[digest] = epoch
            raw["views"] = views
        return {
            "last_viewed_at": raw["last_viewed_at"],
            "last_viewed_epoch": epoch,
            "unread_count": 0,
        }

    return _state().mutate(mutate)


# ---------------------------------------------------------------------------
# 改位置（可选迁移已有文件）
# ---------------------------------------------------------------------------


def _move_into(target_root: Path, relative: str, source: Path) -> str:
    target = target_root / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        if target.is_file() and _same_content(source, target):
            return "skipped"
        return "conflict"
    shutil.move(str(source), str(target))
    return "moved"


def _same_content(left: Path, right: Path) -> bool:
    try:
        if left.stat().st_size != right.stat().st_size:
            return False
        return _digest(left) == _digest(right)
    except OSError:
        return False


def _digest(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def _prune_empty_dirs(root: Path) -> None:
    for path in sorted(_iter_files_shallow(root), key=lambda item: len(item.parts), reverse=True):
        try:
            path.rmdir()
        except OSError:
            continue


def _iter_files_shallow(root: Path) -> list[Path]:
    """收集箱内的全部子目录（由深到浅清理空目录）。"""
    if not root.is_dir():
        return []
    directories: list[Path] = []
    stack = [root]
    while stack:
        current = stack.pop()
        try:
            entries = list(os.scandir(current))
        except OSError:
            continue
        for entry in entries:
            try:
                if entry.is_dir(follow_symlinks=False) and not entry.is_symlink():
                    directories.append(Path(entry.path))
                    stack.append(Path(entry.path))
            except OSError:
                continue
    return directories


def _rollback_moves(moved: list[str], new_root: Path, old_root: Path) -> list[dict[str, str]]:
    """把本次已搬走的文件搬回旧位置；返回搬不回去的清单。"""
    failures: list[dict[str, str]] = []
    for relative in moved:
        source = new_root / relative
        target = old_root / relative
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(source), str(target))
        except OSError:
            failures.append({"file": relative, "code": "INBOX_ROLLBACK_FAILED"})
    return failures


def update_path(raw_path: Any, migrate: bool = True) -> dict[str, Any]:
    """修改收集箱位置；同一位置重复提交幂等，不产生重复数据。

    有文件搬不动时不切换 ``path``：否则留在旧目录的文件会从界面上消失。默认把
    已搬走的文件回滚回旧目录；回滚本身也失败时才切换（保住数据可达）。

    位置**不限制在** ``GW_ALLOWED_ROOTS`` 内：收集箱是用户自己的工作文件夹（可以放到
    任意可写磁盘），Issue #603 明确要求「位置可在设置里改」。搬家只发生在收集箱目录
    之间，源与目标都是收集箱自己管理的文件，不涉及素材根的越权读取。
    """
    with _lock:
        raw = _state().read()
        old_root = inbox_root(raw)
        new_root = _resolve_configured_dir(raw_path)
        if new_root == old_root.resolve(strict=False):
            return {
                **settings_snapshot(raw),
                "applied": True,
                "unchanged": True,
                "migrated": [],
                "skipped": [],
                "conflicts": [],
                "failed": [],
                "migrate_requested": bool(migrate),
            }
        if _is_within(new_root, old_root) or _is_within(old_root, new_root):
            raise CleanroomException(
                400, "INBOX_PATH_NESTED", "新位置不能与当前收集箱互为上下级目录"
            )
        _ensure_dir(new_root)
        moved: list[str] = []
        skipped: list[str] = []
        conflicts: list[str] = []
        failed: list[dict[str, str]] = []
        if migrate and old_root.is_dir():
            for source in _iter_files(old_root):
                try:
                    relative = source.relative_to(old_root).as_posix()
                except ValueError:
                    continue
                try:
                    outcome = _move_into(new_root, relative, source)
                except OSError:
                    failed.append({"file": relative, "code": "INBOX_MOVE_FAILED"})
                    continue
                if outcome == "moved":
                    moved.append(relative)
                elif outcome == "skipped":
                    skipped.append(relative)
                else:
                    conflicts.append(relative)
            _prune_empty_dirs(old_root)

        if failed or conflicts:
            rollback_failures = _rollback_moves(moved, new_root, old_root)
            failed = [*failed, *rollback_failures]
            if not rollback_failures:
                return {
                    **settings_snapshot(raw),
                    "applied": False,
                    "unchanged": False,
                    "migrated": [],
                    "rolled_back": moved,
                    "skipped": skipped,
                    "conflicts": conflicts,
                    "failed": failed,
                    "migrate_requested": bool(migrate),
                }

        def mutate(payload: dict[str, Any]) -> dict[str, Any]:
            payload["path"] = str(new_root)
            return payload

        raw = _state().mutate(mutate)
        snapshot = settings_snapshot(raw)
        return {
            **snapshot,
            "applied": True,
            "unchanged": False,
            "migrated": moved,
            "rolled_back": [],
            "skipped": skipped,
            "conflicts": conflicts,
            "failed": failed,
            "migrate_requested": bool(migrate),
        }


# ---------------------------------------------------------------------------
# 迁移托管资产
# ---------------------------------------------------------------------------


def _library_items() -> list[dict[str, Any]]:
    from gw.asset_library.service import default_asset_library_service

    return default_asset_library_service.iter_library_items()


def _inside_allowed_root(path: Path) -> bool:
    """迁移只允许搬运用户已挂载素材根内的文件，避免把系统或任意路径文件卷进收集箱。"""
    for root in storage.allowed_roots():
        try:
            canonical_root = root.resolve()
        except (OSError, RuntimeError, ValueError):
            continue
        try:
            path.relative_to(canonical_root)
        except ValueError:
            continue
        return True
    return False


def _local_file_from_url(url: Any) -> Path | None:
    """只认 ``file://`` 或绝对路径且真实存在、位于已挂载素材根内的普通文件。"""
    text = str(url or "").strip()
    if not text:
        return None
    candidate: Path
    if text.lower().startswith("file://"):
        raw_path = unquote(urlsplit(text).path)
        if os.name == "nt" and len(raw_path) > 3 and raw_path[0] == "/" and raw_path[2] == ":":
            raw_path = raw_path[1:]
        candidate = Path(raw_path)
    else:
        try:
            candidate = Path(text).expanduser()
        except (OSError, RuntimeError, ValueError):
            return None
        if not candidate.is_absolute():
            return None
    try:
        if candidate.is_symlink():
            return None
        resolved = candidate.resolve(strict=True)
    except (OSError, RuntimeError, ValueError):
        return None
    if not resolved.is_file() or not _inside_allowed_root(resolved):
        return None
    return resolved


def _transfer_into(target: Path, source: Path) -> str:
    """把源文件放进收集箱但**保留源文件**：硬链接优先（同卷不占额外空间），跨卷回退复制。

    不能用 ``shutil.move``：托管资产记录里的 url 仍指向原路径，搬走会让这些记录
    变成点不开的死链（审核 635-r1 指出）。
    """
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.link(source, target)
        return "linked"
    except OSError:
        pass
    shutil.copy2(source, target)
    return "copied"


def migrate_asset_library() -> dict[str, Any]:
    """把托管资产记录并入收集箱；按 asset_id 记账，重复执行不会多出数据。

    两条硬约束（审核 635-r1）：

    - 源文件保留：用硬链接/复制而不是 move，托管资产记录里的 url 不会变成死链；
    - 失败不入账：搬不动就 ``continue``，下次调用还会重试，不会被永久 skip。
    """
    with _lock:
        raw = _state().read()
        root = _ensure_dir(inbox_root(raw))
        viewer = _owner_digest()
        migrated = raw.setdefault("migrated", {})
        pending_names: dict[str, str] = {}
        moved: list[str] = []
        url_entries: list[str] = []
        skipped: list[str] = []
        failed: list[dict[str, str]] = []
        linked = 0
        copied = 0
        for item in _library_items():
            asset_id = str(item.get("asset_id") or "").strip()
            if not asset_id:
                continue
            if asset_id in migrated:
                skipped.append(asset_id)
                continue
            name = str(item.get("name") or asset_id).strip() or asset_id
            url = item.get("url")
            record: dict[str, Any] = {
                "asset_id": asset_id,
                "name": name,
                "url": url,
                "library_id": item.get("library_id"),
                "category_id": item.get("category_id"),
                "category_type": item.get("category_type"),
                "owner": viewer,
                "migrated_at": storage.now_iso(),
                "file": None,
                "kind": "url",
            }
            local_file = _local_file_from_url(url)
            if local_file is not None:
                relative = _unique_physical(root, local_file.name)
                try:
                    transfer = _transfer_into(root / relative, local_file)
                except OSError:
                    failed.append({"asset_id": asset_id, "code": "INBOX_MIGRATE_FAILED"})
                    # 失败不入账：留到下次重试，不能因为一次失败就被当成已迁移。
                    continue
                display = _unique_display(raw, name, pending_names)
                record.update(
                    {
                        "file": relative,
                        # 展示名沿用托管资产自己的名字；分类仍按源文件扩展名判定。
                        "kind": classify_name(local_file.name),
                        "transfer": transfer,
                    }
                )
                pending_names[relative] = display
                if transfer == "linked":
                    linked += 1
                else:
                    copied += 1
                moved.append(asset_id)
            if record["file"] is None:
                url_entries.append(asset_id)
                # 只有真正的访问地址才算链接条目；url 为空的托管记录标注为「仅记录」，
                # 避免在收集箱里显示成可点击却无地址的链接。
                if not str(record.get("url") or "").strip():
                    record["kind"] = "record"
            migrated[asset_id] = record

        def mutate(payload: dict[str, Any]) -> dict[str, Any]:
            payload.setdefault("migrated", {}).update(migrated)
            if pending_names:
                names = payload.get(DISPLAY_NAMES_KEY)
                names = names if isinstance(names, dict) else {}
                names.update(pending_names)
                payload[DISPLAY_NAMES_KEY] = names
            return payload

        raw = _state().mutate(mutate)
        snapshot = settings_snapshot(raw)
        return {
            "moved_files": len(moved),
            "linked": linked,
            "copied": copied,
            "url_entries": len(url_entries),
            "skipped": len(skipped),
            "failed": failed,
            "total_records": len(_visible_migrated(raw)),
            "settings": snapshot,
            "data_status": "partial" if failed else "ok",
        }


# ---------------------------------------------------------------------------
# 手动上传
# ---------------------------------------------------------------------------


def add_upload(filename: Any, content: bytes) -> dict[str, Any]:
    """把手动上传的素材直接写进收集箱（默认落点），不做索引登记。"""
    base = Path(str(filename or "").replace("\\", "/")).name.strip()
    if base in {"", ".", ".."}:
        raise CleanroomException(400, "INVALID_REQUEST", "上传文件名无效")
    if not content:
        raise CleanroomException(400, "INVALID_REQUEST", "上传内容为空")
    if len(content) > MAX_INBOX_UPLOAD_BYTES:
        raise CleanroomException(400, "CONTENT_TOO_LARGE", "上传内容超过上限")
    with _lock:
        raw = _state().read()
        root = _ensure_dir(inbox_root(raw))
        # GNS-2026：磁盘名纯 ASCII，用户原名只做展示名（映射表随状态落盘）。
        relative = _unique_physical(root, base)
        display = _unique_display(raw, base)
        target = root / relative
        partial = target.with_name(".gw-inbox-pending-" + target.name)
        try:
            with partial.open("wb") as output:
                output.write(content)
                output.flush()
                os.fsync(output.fileno())
            os.replace(partial, target)
        except OSError:
            partial.unlink(missing_ok=True)
            raise CleanroomException(503, "FILE_WRITE_FAILED", "收集箱写入失败") from None

        def mutate(payload: dict[str, Any]) -> dict[str, Any]:
            names = payload.get(DISPLAY_NAMES_KEY)
            names = names if isinstance(names, dict) else {}
            names[relative] = display
            payload[DISPLAY_NAMES_KEY] = names
            return payload

        _state().mutate(mutate)
        stat = target.stat()
        created = {
            "id": f"inbox:{relative}",
            "source": "inbox",
            "name": display,
            "file": relative,
            "kind": classify_name(display),
            "size_bytes": stat.st_size,
            "modified_at": int(stat.st_mtime),
            "url": None,
            "asset_id": None,
        }
        # 【QIN-588 AC1】上传响应同样携带受控预览查询串：上传后立即可出图。
        created["preview_query"] = inbox_preview_query(created)
        return created


# ---------------------------------------------------------------------------
# 受控预览解析（QIN-588）：给收集箱身份一条可出真实缩略图的媒体通道
# ---------------------------------------------------------------------------


def _preview_relative_parts(raw: Any) -> list[str] | None:
    """把预览参数解析为安全的相对路径段；结构不合法返回 None（失败关闭）。

    只接受 POSIX 相对路径：拒绝空段、``.``/``..`` 段、反斜杠、盘符与绝对路径；
    符号链接与穿越逃逸由 resolve 后的包含校验兜底。
    """
    text = str(raw or "").strip()
    if not text or "\\" in text or text.startswith("/") or text.endswith("/"):
        return None
    if len(text) > 2 and text[1] == ":":
        return None
    parts = text.split("/")
    for part in parts:
        if not part or part in {".", ".."}:
            return None
    return parts


def _resolve_within_dir(base: Path, raw: Any) -> Path | None:
    """把相对路径解析到 ``base`` 内的真实普通文件；越界/软链接/缺失一律 None。

    ``resolve(strict=True)`` 会把符号链接解析到真实位置：根内软链接指向根外时，
    解析结果落在外面，包含校验失败；即使根内子目录是软链接也一样。文件本身是
    软链接（即使指向根内）也拒绝，与 ``_iter_files`` 跳过软链接的口径一致。
    """
    parts = _preview_relative_parts(raw)
    if parts is None:
        return None
    try:
        base_resolved = base.resolve(strict=False)
        candidate = (base / Path(*parts)).resolve(strict=True)
    except (OSError, RuntimeError, ValueError):
        return None
    if candidate.is_symlink() or not candidate.is_file():
        return None
    if not _is_within(candidate, base_resolved):
        return None
    return candidate


def resolve_preview_request(payload: dict[str, Any] | None) -> Path | None:
    """【QIN-588】收集箱身份的受控预览解析：只认服务端边界内的真实文件。

    - ``inbox_file``：收集箱根内相对路径；resolve 后必须仍在根内，穿越与软链接
      逃逸都解析不出根，失败关闭。
    - ``collect_inbox_file``：遗留收集目录（``registry_uploads/collect_inbox/<sha256(owner)>/``）
      内相对路径。目录按当前主体 digest 拼装，客户端无法指定 digest：无主体 → 403
      失败关闭，跨主体 = 目标文件不存在 → 404。
    - 未携带受控参数 → 返回 None，回落既有 asset_id/path 链路。
    """
    payload = payload or {}
    if "inbox_file" in payload:
        return _resolve_within_dir(inbox_root(), payload.get("inbox_file"))
    if "collect_inbox_file" in payload:
        digest = owner_digest()
        if not digest:
            raise CleanroomException(
                403, "INBOX_OWNER_REQUIRED", "缺少请求主体，无法解析收集箱预览"
            )
        base = storage.data_root().joinpath(*LEGACY_COLLECT_PARTS, digest)
        return _resolve_within_dir(base, payload.get("collect_inbox_file"))
    return None


def inbox_preview_query(item: dict[str, Any]) -> str | None:
    """把收集箱条目映射为受控预览查询串；非本地文件返回 None。

    前端零路径知识：预览 URL 由服务端下发的查询串拼装，不回显绝对路径。
    """
    source = str(item.get("source") or "")
    relative = str(item.get("file") or "").strip()
    if not relative:
        return None
    if source == "inbox":
        return f"inbox_file={quote(relative, safe='')}"
    if source == "collect_inbox":
        prefix = Path(*LEGACY_COLLECT_PARTS).as_posix() + "/"
        digest = owner_digest()
        if not digest:
            return None
        digest_prefix = f"{prefix}{digest}/"
        if not relative.startswith(digest_prefix):
            return None
        return "collect_inbox_file=" + quote(relative[len(digest_prefix):], safe="")
    return None
