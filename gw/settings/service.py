# -*- coding: utf-8 -*-
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

"""设置页服务层实现（Phase 10D）。

遵循 ``docs/contracts/SETTINGS-INTERFACE-CATALOG.yaml`` 的当前配置治理契约：

- 稳定 ID：结构体 ``structure_id = strc_NNNN``（确定性序号，禁止随机数/uuid）；
  provider 标识一律来自调用方提交，服务端**绝不自造**厂商条目；
- CAS：存储设置与 provider 集合用 ``revision``，结构体用结构级 ``version``；
  写操作冲突一律 409（存储/平台用 VERSION_CONFLICT，结构体用 STRUCTURE_VERSION_CONFLICT）；
- **零伪造**：无真实来源时 storage-settings 返回 ``configured: false`` + ``data_gaps``，
  providers 返回 ``providers: []``；结构成员不编造素材元数据；
- **凭据边界**：provider 输入由严格DTO验证，显式更新的秘密仅加密落盘，
  不回显、不写环境文件；未知凭据别名拒绝；
- **fail-closed**：探测由probes模块执行；缺失依赖或上游失败如实返回503，
  不伪造模型列表、连通性成功或延迟数字。

证据边界：ProviderService与StorageSettingsService使用三態隔离的原子JSON，
配置支持重启恢复并仅保证单进程一致性；素材结构服务仍为内存实现。
"""

from __future__ import annotations

import copy
import json
import os
import re
import stat
import threading
from collections.abc import Iterable, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from gw.core import storage
from gw.core.errors import CleanroomException
from gw.settings import credentials
from gw.settings.models import (
    DATA_STATUS_NOT_CONFIGURED,
    DATA_STATUS_OK,
    GAP_MEMBER_ASSET_METADATA,
    GAP_PROVIDER_REGISTRY_SOURCE,
    GAP_STORAGE_SETTINGS_SOURCE,
    PROVIDER_PROBE_NOT_INTEGRATED,
    AssetStructureCreateRequest,
    AssetStructureCurrentRequest,
    AssetStructureItem,
    AssetStructureListResponse,
    AssetStructureMember,
    AssetStructureUpdateRequest,
    ProviderEntryRequest,
    ProviderSnapshot,
    StorageAllowedRoot,
    StorageAllowedRootDeleteRequest,
    StorageAllowedRootRequest,
    StorageAllowedRootsSnapshot,
    StorageSettingsPatchRequest,
    StorageSettingsSnapshot,
)

#: 结构类型白名单；其余取值一律 400 失败关闭。
ALLOWED_STRUCTURE_KINDS = frozenset({"version", "group"})
DIRECTORY_BROWSE_LIMIT = 100

#: 凭据类字段名（小写比对）；命中即剥离，绝不落库或回显。
_CREDENTIAL_KEY_MARKERS = (
    "api_key",
    "apikey",
    "access_key",
    "secret",
    "token",
    "password",
    "credential",
    "wallet",
    "private_key",
)

#: provider 条目中允许对外回显的字段白名单形状之外，其余原样保留但先剥离凭据字段。
_CREDENTIAL_EXACT_KEYS = frozenset(
    {
        "key",
        "keys",
        "auth",
        "authorization",
    }
)


def _now_iso() -> str:
    """生成标准 ISO 8601 UTC 时间戳字符串。"""
    return datetime.now(timezone.utc).isoformat()


def _is_credential_key(key: str) -> bool:
    """判断字段名是否为凭据类字段（小写、去除分隔符后比对）。"""
    normalized = str(key or "").strip().casefold().replace("-", "_").replace(" ", "_")
    if not normalized:
        return False
    if normalized in _CREDENTIAL_EXACT_KEYS:
        return True
    return any(marker in normalized for marker in _CREDENTIAL_KEY_MARKERS)


def strip_credential_fields(value: Any) -> Any:
    """递归剥离凭据类字段；返回新对象，绝不修改入参。

    零凭据口径：任何形如 ``api_key`` / ``access_key`` / ``token`` / ``secret`` /
    ``password`` 的字段都不落库、不进入响应。
    """
    if isinstance(value, dict):
        cleaned: dict[str, Any] = {}
        for key, item in value.items():
            if _is_credential_key(key):
                continue
            cleaned[key] = strip_credential_fields(item)
        return cleaned
    if isinstance(value, list):
        return [strip_credential_fields(item) for item in value]
    return copy.deepcopy(value)


def _require_positive_version(expected_version: int | None, label: str) -> None:
    """写请求必须提供正整数 expected_version；缺失与非法值分别返回 400。"""
    if expected_version is None:
        raise CleanroomException(
            status_code=400,
            code="EXPECTED_VERSION_REQUIRED",
            message=f"{label}写入必须携带 expected_version",
        )
    if not isinstance(expected_version, int) or isinstance(expected_version, bool) or expected_version < 1:
        raise CleanroomException(
            status_code=400,
            code="INVALID_REQUEST",
            message=f"{label} expected_version 必须为正整数",
        )


def _resolve_expected_version(payload: Any) -> int | None:
    """读取写请求的 CAS 期望版本。"""
    return getattr(payload, "expected_version", None)


def _conflict(
    code: str,
    expected_version: int,
    current_version: int,
    message: str,
) -> CleanroomException:
    """构造标准 409 冲突错误包（含 expected/current 版本便于调用方自愈）。"""
    return CleanroomException(
        status_code=409,
        code=code,
        message=message,
        extra={"expected_version": expected_version, "current_version": current_version},
    )


def _normalize_asset_ids(raw: Any, label: str) -> list[str]:
    """校验并规范化 asset_ids：至少 2 个、去重后不得为空、禁止重复项。"""
    if not isinstance(raw, (list, tuple)):
        raise CleanroomException(
            status_code=400,
            code="INVALID_REQUEST",
            message=f"{label} 必须为数组",
        )
    ids = [str(item or "").strip() for item in raw]
    if any(not item for item in ids):
        raise CleanroomException(
            status_code=400,
            code="INVALID_REQUEST",
            message=f"{label} 不能包含空标识",
        )
    if len(ids) != len(set(ids)):
        raise CleanroomException(
            status_code=400,
            code="INVALID_REQUEST",
            message=f"{label} 不能包含重复项",
        )
    if len(ids) < 2:
        raise CleanroomException(
            status_code=400,
            code="INVALID_REQUEST",
            message=f"{label} 至少需要 2 个成员",
        )
    return ids


class StorageSettingsService:
    """存储/目录设置的原子JSON快照与 CAS 版本服务。"""

    def __init__(self) -> None:
        self._state = storage.JsonState("storage_settings", self._empty_state)

    @staticmethod
    def _empty_state() -> dict[str, Any]:
        return {
            "revision": 1,
            "configured": False,
            "dirs": {},
            "defaults": {},
            "extra_local_entries": [],
            "removed_extra_local_entries": [],
            "local_library_names": {},
            "local_library_order": [],
            "local_library_read_only": {},
            "hidden_asset_root_keys": [],
        }

    # ------------------------------------------------------------------
    # 查询
    # ------------------------------------------------------------------
    def get_snapshot(self) -> StorageSettingsSnapshot:
        """返回存储设置真值快照；无真实来源时如实标记未配置。"""
        return self._build_snapshot(self._state.read())

    @staticmethod
    def _directory_browse_roots() -> list[dict[str, str]]:
        """返回服务器可浏览的文件系统根目录，不读取其文件内容。"""
        if os.name == "nt":
            try:
                import ctypes

                mask = ctypes.windll.kernel32.GetLogicalDrives()
            except (AttributeError, OSError):
                mask = 0
            roots = [
                f"{chr(ord('A') + index)}:\\"
                for index in range(26)
                if mask & (1 << index)
            ]
        else:
            roots = [os.path.abspath(os.sep)]
        result = []
        for root in roots:
            try:
                if os.path.isdir(root):
                    result.append({"name": root, "path": root})
            except OSError:
                continue
        return result[:DIRECTORY_BROWSE_LIMIT]

    @staticmethod
    def _is_directory_link(path: Path, info: Any | None = None) -> bool:
        """识别符号链接及 Windows junction/reparse point。"""
        stat_result = info or path.lstat()
        if stat.S_ISLNK(stat_result.st_mode):
            return True
        if os.name == "nt":
            reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
            if getattr(stat_result, "st_file_attributes", 0) & reparse_flag:
                return True
        is_junction = getattr(path, "is_junction", None)
        return bool(callable(is_junction) and is_junction())

    @staticmethod
    def _reject_directory_symlinks(path: Path) -> None:
        """拒绝路径中的链接组件，避免浏览器沿链接跳出目录树。"""
        current = Path(path.anchor)
        for component in path.parts[1:]:
            current = current / component
            info = current.lstat()
            if StorageSettingsService._is_directory_link(current, info):
                raise CleanroomException(400, "INVALID_PATH", "服务器目录浏览不允许经过符号链接")

    @staticmethod
    def _excluded_browse_directory_names() -> frozenset[str]:
        """返回目录浏览须自动隐藏的系统内部目录名集合（小写精确匹配）。"""
        excluded = {"god-recycler", "god-cache", ".gods-workbench"}
        try:
            media_path = storage.data_root() / "media_settings.json"
            if media_path.is_file():
                payload = json.loads(media_path.read_text(encoding="utf-8"))
                thumbnails = payload.get("thumbnails") if isinstance(payload, dict) else None
                cache_folder = str((thumbnails or {}).get("cache_folder") or "").strip()
                if cache_folder and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", cache_folder):
                    excluded.add(cache_folder.casefold())
        except Exception:
            pass
        return frozenset(excluded)

    def browse_directories(self, path: str | None = None) -> dict[str, Any]:
        """只读列出服务器目录；限制单次结果数并跳过不可访问的子项。"""
        if path is None or not str(path).strip():
            roots = self._directory_browse_roots()
            return {
                "path": None,
                "parent": None,
                "directories": roots,
                "limit": DIRECTORY_BROWSE_LIMIT,
                "truncated": len(roots) > DIRECTORY_BROWSE_LIMIT,
            }

        raw_path = str(path).strip()
        path_parts = raw_path.replace("\\", "/").split("/")
        if (
            len(raw_path) > 4096
            or "\x00" in raw_path
            or not os.path.isabs(raw_path)
            or ".." in path_parts
        ):
            raise CleanroomException(400, "INVALID_PATH", "服务器目录必须是无路径穿越的绝对路径")

        target = Path(raw_path)
        excluded_names = self._excluded_browse_directory_names()
        try:
            self._reject_directory_symlinks(target)
            resolved = target.resolve(strict=True)
            if not resolved.is_dir():
                raise CleanroomException(400, "INVALID_PATH", "所选路径不是目录")
            directories: list[dict[str, str]] = []
            truncated = False
            with os.scandir(resolved) as entries:
                for entry in entries:
                    try:
                        if not entry.is_dir(follow_symlinks=False):
                            continue
                        if entry.name.casefold() in excluded_names:
                            continue
                        child = Path(entry.path)
                        if self._is_directory_link(child):
                            continue
                        directories.append({"name": entry.name, "path": str(child)})
                        if len(directories) > DIRECTORY_BROWSE_LIMIT:
                            truncated = True
                            directories.pop()
                            break
                    except (OSError, PermissionError):
                        continue
        except CleanroomException:
            raise
        except FileNotFoundError as exc:
            raise CleanroomException(404, "DIRECTORY_NOT_FOUND", "服务器目录不存在") from exc
        except PermissionError as exc:
            raise CleanroomException(403, "DIRECTORY_INACCESSIBLE", "无权读取服务器目录") from exc
        except OSError as exc:
            raise CleanroomException(400, "INVALID_PATH", "服务器目录不可读取") from exc

        directories.sort(key=lambda item: (item["name"].casefold(), item["name"]))
        parent = resolved.parent
        return {
            "path": str(resolved),
            "parent": None if parent == resolved else str(parent),
            "directories": directories,
            "limit": DIRECTORY_BROWSE_LIMIT,
            "truncated": truncated,
        }

    @staticmethod
    def _build_snapshot(state: dict[str, Any]) -> StorageSettingsSnapshot:
        dirs = copy.deepcopy(state.get("dirs") or {})
        entries = copy.deepcopy(state.get("extra_local_entries") or [])
        configured = bool(state.get("configured", False))
        revision = int(state.get("revision", 1))
        names = dict(state.get("local_library_names") or {})
        order = list(state.get("local_library_order") or [])
        extra_dirs = [entry.get("path", "") for entry in entries]
        gaps = [] if configured else [GAP_STORAGE_SETTINGS_SOURCE]
        snapshot_data = dict(
            configured=configured,
            revision=revision,
            dirs=dirs,
            defaults=copy.deepcopy(state.get("defaults") or {}),
            extra_local_dirs=extra_dirs,
            extra_local_entries=entries,
            local_library_names=names,
            local_library_order=order,
            local_library_read_only=dict(state.get("local_library_read_only") or {}),
            local_libraries=StorageSettingsService._build_local_libraries(dirs, entries, names, configured),
            data_status=DATA_STATUS_OK if configured else DATA_STATUS_NOT_CONFIGURED,
            data_gaps=gaps,
        )
        if configured:
            snapshot_data["hidden_asset_root_keys"] = list(state.get("hidden_asset_root_keys") or [])
        return StorageSettingsSnapshot(**snapshot_data)

    @staticmethod
    def _build_local_libraries(
        dirs: dict[str, Any], entries: list[dict[str, Any]], names: dict[str, str], configured: bool
    ) -> list[dict[str, Any]]:
        """把本地目录与附加来源投影为前端直接可用的本地素材库列表。"""
        libraries: list[dict[str, Any]] = []
        local_path = str(dirs.get("local") or "").strip()
        if local_path or configured:
            libraries.append(
                {
                    "id": "local",
                    "kind": "local",
                    "name": names.get("local") or "本地上传",
                    "path": local_path,
                }
            )
        for index, entry in enumerate(entries):
            entry_id = str(entry.get("id") or entry.get("kind") or f"local_extra_{index}")
            libraries.append(
                {
                    "id": entry_id,
                    "kind": str(entry.get("kind") or entry_id),
                    "name": names.get(entry_id) or entry.get("name") or f"素材来源 {index + 1}",
                    "path": str(entry.get("path") or ""),
                }
            )
        return libraries

    # ------------------------------------------------------------------
    # 写入
    # ------------------------------------------------------------------
    def patch(self, payload: StorageSettingsPatchRequest) -> StorageSettingsSnapshot:
        """更新存储设置；CAS 冲突失败关闭，成功则 revision 递增。"""
        if payload.hidden_asset_root_id is not None or payload.hidden_asset_hidden is not None:
            raise CleanroomException(400, "INVALID_REQUEST", "挂载根显隐命令必须经由存储设置 API 解析")
        expected = _resolve_expected_version(payload)
        _require_positive_version(expected, "存储设置")
        removed_sources: list[tuple[str, str]] = []

        def update(state: dict[str, Any]) -> StorageSettingsSnapshot:
            nonlocal removed_sources
            revision = int(state.get("revision", 1))
            if expected != revision:
                raise _conflict(
                    "VERSION_CONFLICT",
                    expected,
                    revision,
                    f"存储设置版本冲突（期望 {expected}，当前 {revision}），请重新读取后重试",
                )
            dirs = copy.deepcopy(state.get("dirs") or {})
            old_entries = copy.deepcopy(state.get("extra_local_entries") or [])
            entries = copy.deepcopy(old_entries)
            names = dict(state.get("local_library_names") or {})
            order = list(state.get("local_library_order") or [])
            read_only = dict(state.get("local_library_read_only") or {})
            hidden_root_keys = list(state.get("hidden_asset_root_keys") or [])
            if payload.dirs is not None:
                dirs = self._normalize_directory_mapping(payload.dirs)
            if payload.extra_local_entries is not None:
                entries = self._normalize_extra_local_entries(payload.extra_local_entries)
                dirs["extra_local_dirs"] = [entry["path"] for entry in entries]
                new_ids = {str(entry.get("id") or "") for entry in entries if entry.get("id")}
                new_keys = set()
                for entry in entries:
                    if entry.get("path"):
                        try:
                            new_keys.add(storage.directory_path_key(str(entry["path"])))
                        except Exception:
                            pass
                for old in old_entries:
                    old_id = str(old.get("id") or "")
                    old_path = str(old.get("path") or "")
                    old_key = ""
                    if old_path:
                        try:
                            old_key = storage.directory_path_key(old_path)
                        except Exception:
                            old_key = ""
                    if old_id and old_id not in new_ids and (not old_key or old_key not in new_keys):
                        removed_sources.append((old_id, old_path))
            if payload.local_library_names is not None:
                names = {str(k): str(v) for k, v in payload.local_library_names.items()}
            if payload.local_library_order is not None:
                order = [str(item) for item in payload.local_library_order]
            if payload.local_library_read_only is not None:
                read_only = dict(payload.local_library_read_only)
            if payload.hidden_asset_root_keys is not None:
                hidden_root_keys = _normalize_hidden_asset_root_keys(payload.hidden_asset_root_keys)
            state.update({
                "dirs": dirs,
                "extra_local_entries": entries,
                "local_library_names": names,
                "local_library_order": order,
                "local_library_read_only": read_only,
                "hidden_asset_root_keys": hidden_root_keys,
                "configured": True,
                "revision": revision + 1,
            })
            return self._build_snapshot(state)

        snapshot = self._state.mutate(update)
        if removed_sources:
            _stop_index_jobs_and_invalidate_caches(
                {sid for sid, _ in removed_sources if sid},
                [path for _, path in removed_sources if path],
            )
        return snapshot

    def register_resource_folder_entry(
        self, entry: dict[str, Any], name: str, expected_version: int, operation_id: str
    ) -> StorageSettingsSnapshot:
        """为单次资源文件夹登记写入可精确补偿的来源归属。"""
        source_id = str(entry.get("id") or "").strip()
        path = storage.validate_mount(storage.normalize_directory_path(str(entry.get("path") or "")))

        def update(state: dict[str, Any]) -> StorageSettingsSnapshot:
            revision = int(state.get("revision", 1))
            if expected_version != revision:
                raise _conflict(
                    "VERSION_CONFLICT", expected_version, revision,
                    f"存储设置版本冲突（期望 {expected_version}，当前 {revision}），请重新读取后重试",
                )
            entries = copy.deepcopy(state.get("extra_local_entries") or [])
            if any(
                str(item.get("id") or "") == source_id
                or (item.get("path") and storage.directory_path_key(str(item["path"])) == storage.directory_path_key(path))
                for item in entries
            ):
                raise CleanroomException(409, "ASSET_ROOT_ALREADY_REGISTERED", "该目录已登记为素材来源")
            normalized_entry = copy.deepcopy(entry)
            normalized_entry["path"] = path
            entries.append(normalized_entry)
            names = dict(state.get("local_library_names") or {})
            names[source_id] = name
            order = [str(item) for item in state.get("local_library_order") or [] if str(item) != source_id]
            order.append(source_id)
            dirs = copy.deepcopy(state.get("dirs") or {})
            dirs["extra_local_dirs"] = [str(item.get("path") or "") for item in entries if item.get("path")]
            owners = dict(state.get("resource_folder_registration_owners") or {})
            owners[source_id] = operation_id
            registration_meta = dict(state.get("resource_folder_registration_meta") or {})
            registration_meta[operation_id] = {
                "configured_before": bool(state.get("configured", False)),
                "registered_revision": revision + 1,
            }
            state.update({
                "dirs": dirs,
                "extra_local_entries": entries,
                "local_library_names": names,
                "local_library_order": order,
                "resource_folder_registration_owners": owners,
                "resource_folder_registration_meta": registration_meta,
                "configured": True,
                "revision": revision + 1,
            })
            return self._build_snapshot(state)

        return self._state.mutate(update)

    def finish_resource_folder_registration(
        self, source_id: str, path: str, operation_id: str, *, rollback: bool
    ) -> bool:
        """清理登记标记；仅回滚仍由同一操作创建且身份未变的来源。"""
        changed = False
        target_key = storage.directory_path_key(path)

        def update(state: dict[str, Any]) -> None:
            nonlocal changed
            owners = dict(state.get("resource_folder_registration_owners") or {})
            if owners.get(source_id) != operation_id:
                return
            registration_meta = dict(state.get("resource_folder_registration_meta") or {})
            meta = registration_meta.pop(operation_id, {})
            entries = copy.deepcopy(state.get("extra_local_entries") or [])
            exact = next((item for item in entries if str(item.get("id") or "") == source_id), None)
            if exact is None or storage.directory_path_key(str(exact.get("path") or "")) != target_key:
                owners.pop(source_id, None)
                state["resource_folder_registration_owners"] = owners
                state["resource_folder_registration_meta"] = registration_meta
                changed = True
                return
            if rollback:
                revision = int(state.get("revision", 1))
                entries = [item for item in entries if str(item.get("id") or "") != source_id]
                dirs = copy.deepcopy(state.get("dirs") or {})
                dirs["extra_local_dirs"] = [str(item.get("path") or "") for item in entries if item.get("path")]
                names = dict(state.get("local_library_names") or {})
                names.pop(source_id, None)
                order = [str(item) for item in state.get("local_library_order") or [] if str(item) != source_id]
                read_only = dict(state.get("local_library_read_only") or {})
                read_only.pop(source_id, None)
                state.update({
                    "dirs": dirs,
                    "extra_local_entries": entries,
                    "local_library_names": names,
                    "local_library_order": order,
                    "local_library_read_only": read_only,
                })
                if revision == meta.get("registered_revision"):
                    state["configured"] = bool(meta.get("configured_before", False))
            owners.pop(source_id, None)
            state["resource_folder_registration_owners"] = owners
            state["resource_folder_registration_meta"] = registration_meta
            state["revision"] = int(state.get("revision", 1)) + 1
            changed = True

        self._state.mutate(update)
        return changed

    def move_entries_to_removed(
        self, target_ids: set[str], target_paths: list[str]
    ) -> list[dict[str, Any]]:
        """移出资源库时同步把匹配来源从活跃清单移入 removed_extra_local_entries。"""
        target_keys = _normalized_target_keys(target_paths)
        moved: list[dict[str, Any]] = []

        def update(state: dict[str, Any]) -> None:
            entries = copy.deepcopy(state.get("extra_local_entries") or [])
            removed = copy.deepcopy(state.get("removed_extra_local_entries") or [])
            remaining: list[dict[str, Any]] = []
            existing_removed_ids = {str(item.get("id") or "") for item in removed if item.get("id")}
            for entry in entries:
                if _entry_matches_targets(entry, target_ids, target_keys):
                    moved.append(copy.deepcopy(entry))
                    eid = str(entry.get("id") or "")
                    if eid and eid not in existing_removed_ids:
                        removed.append(copy.deepcopy(entry))
                        existing_removed_ids.add(eid)
                    elif not eid:
                        removed.append(copy.deepcopy(entry))
                else:
                    remaining.append(entry)
            if not moved:
                return
            dirs = copy.deepcopy(state.get("dirs") or {})
            dirs["extra_local_dirs"] = [str(item.get("path") or "") for item in remaining if item.get("path")]
            state["dirs"] = dirs
            state["extra_local_entries"] = remaining
            state["removed_extra_local_entries"] = removed
            state["revision"] = int(state.get("revision", 1)) + 1

        self._state.mutate(update)
        return moved

    def revert_moved_entries(self, moved_entries: list[dict[str, Any]]) -> None:
        """移出级联失败时的补偿回滚：将刚移出的来源条目放回活跃列表。"""
        if not moved_entries:
            return
        moved_ids = {str(item.get("id") or "") for item in moved_entries if item.get("id")}

        def update(state: dict[str, Any]) -> None:
            entries = copy.deepcopy(state.get("extra_local_entries") or [])
            removed = copy.deepcopy(state.get("removed_extra_local_entries") or [])
            existing_ids = {str(item.get("id") or "") for item in entries if item.get("id")}
            restored_any = False
            for item in moved_entries:
                eid = str(item.get("id") or "")
                if not eid or eid not in existing_ids:
                    entries.append(copy.deepcopy(item))
                    if eid:
                        existing_ids.add(eid)
                    restored_any = True
            next_removed = [
                item for item in removed if str(item.get("id") or "") not in moved_ids
            ]
            if not restored_any and len(next_removed) == len(removed):
                return
            dirs = copy.deepcopy(state.get("dirs") or {})
            dirs["extra_local_dirs"] = [str(item.get("path") or "") for item in entries if item.get("path")]
            state["dirs"] = dirs
            state["extra_local_entries"] = entries
            state["removed_extra_local_entries"] = next_removed
            state["revision"] = int(state.get("revision", 1)) + 1

        self._state.mutate(update)

    def restore_removed_entries(
        self, target_ids: set[str], target_paths: list[str]
    ) -> list[dict[str, Any]]:
        """恢复资源文件夹时把匹配条目从 removed_extra_local_entries 迁回活跃列表。"""
        target_keys = _normalized_target_keys(target_paths)
        restored: list[dict[str, Any]] = []

        def update(state: dict[str, Any]) -> None:
            entries = copy.deepcopy(state.get("extra_local_entries") or [])
            removed = copy.deepcopy(state.get("removed_extra_local_entries") or [])
            existing_ids = {str(item.get("id") or "") for item in entries if item.get("id")}
            existing_keys = _normalized_target_keys(
                [str(item.get("path") or "") for item in entries if item.get("path")]
            )
            remaining_removed: list[dict[str, Any]] = []
            changed = False
            for entry in removed:
                if _entry_matches_targets(entry, target_ids, target_keys):
                    eid = str(entry.get("id") or "")
                    epath = str(entry.get("path") or "")
                    ekeys = _normalized_target_keys([epath]) if epath else []
                    if (eid and eid in existing_ids) or any(k in existing_keys for k in ekeys):
                        changed = True
                        continue
                    entries.append(copy.deepcopy(entry))
                    restored.append(copy.deepcopy(entry))
                    if eid:
                        existing_ids.add(eid)
                    existing_keys.extend(ekeys)
                    changed = True
                else:
                    remaining_removed.append(entry)
            if not changed:
                return
            dirs = copy.deepcopy(state.get("dirs") or {})
            dirs["extra_local_dirs"] = [str(item.get("path") or "") for item in entries if item.get("path")]
            state["dirs"] = dirs
            state["extra_local_entries"] = entries
            state["removed_extra_local_entries"] = remaining_removed
            state["revision"] = int(state.get("revision", 1)) + 1

        self._state.mutate(update)
        return restored

    def purge_entries(self, target_ids: set[str], target_paths: list[str]) -> None:
        """清除索引时同步清理活跃与已移出列表中的全部关联来源与元数据。"""
        target_keys = _normalized_target_keys(target_paths)

        def update(state: dict[str, Any]) -> None:
            entries = copy.deepcopy(state.get("extra_local_entries") or [])
            removed = copy.deepcopy(state.get("removed_extra_local_entries") or [])
            purged_ids = set(target_ids)
            next_entries = []
            for entry in entries:
                if _entry_matches_targets(entry, target_ids, target_keys):
                    if entry.get("id"):
                        purged_ids.add(str(entry["id"]))
                else:
                    next_entries.append(entry)
            next_removed = []
            for entry in removed:
                if _entry_matches_targets(entry, target_ids, target_keys):
                    if entry.get("id"):
                        purged_ids.add(str(entry["id"]))
                else:
                    next_removed.append(entry)
            names = {
                k: v
                for k, v in (state.get("local_library_names") or {}).items()
                if str(k) not in purged_ids
            }
            order = [
                str(item)
                for item in (state.get("local_library_order") or [])
                if str(item) not in purged_ids
            ]
            read_only = {
                k: v
                for k, v in (state.get("local_library_read_only") or {}).items()
                if str(k) not in purged_ids
            }
            hidden_root_keys = [
                key
                for key in (state.get("hidden_asset_root_keys") or [])
                if not _path_matches_target_keys(str(key), target_keys)
            ]
            changed = (
                len(next_entries) != len(entries)
                or len(next_removed) != len(removed)
                or names != (state.get("local_library_names") or {})
                or order != (state.get("local_library_order") or [])
                or read_only != (state.get("local_library_read_only") or {})
                or hidden_root_keys != (state.get("hidden_asset_root_keys") or [])
            )
            if not changed:
                return
            dirs = copy.deepcopy(state.get("dirs") or {})
            dirs["extra_local_dirs"] = [str(item.get("path") or "") for item in next_entries if item.get("path")]
            state["dirs"] = dirs
            state["extra_local_entries"] = next_entries
            state["removed_extra_local_entries"] = next_removed
            state["local_library_names"] = names
            state["local_library_order"] = order
            state["local_library_read_only"] = read_only
            state["hidden_asset_root_keys"] = hidden_root_keys
            state["revision"] = int(state.get("revision", 1)) + 1

        self._state.mutate(update)

    @staticmethod
    def _normalize_directory_mapping(dirs: dict[str, Any]) -> dict[str, Any]:
        def normalize(value: Any, *, mount: bool = False) -> Any:
            if isinstance(value, str):
                if not value.strip():
                    return ""
                normalized = storage.normalize_directory_path(value)
                return storage.validate_mount(normalized) if mount else storage.validate_directory_path(normalized)
            if isinstance(value, list):
                return [normalize(item, mount=mount) for item in value]
            if value is None:
                return None
            raise CleanroomException(400, "INVALID_PATH", "目录必须是绝对路径")

        return {
            str(key): normalize(value, mount=str(key) in {"local", "extra_local_dirs"})
            for key, value in dirs.items()
        }

    @staticmethod
    def _normalize_extra_local_entries(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
        normalized: list[dict[str, Any]] = []
        for entry in entries:
            if not isinstance(entry, dict) or not isinstance(entry.get("path"), str):
                raise CleanroomException(400, "INVALID_PATH", "素材目录必须是绝对路径")
            path = storage.normalize_directory_path(entry["path"])
            normalized_entry = copy.deepcopy(entry)
            normalized_entry["path"] = storage.validate_mount(path)
            normalized.append(normalized_entry)
        return normalized


def _normalized_target_keys(paths: Iterable[str]) -> list[str]:
    keys: list[str] = []
    seen: set[str] = set()
    for raw_path in paths:
        text = str(raw_path or "").strip()
        if not text:
            continue
        try:
            key = storage.directory_path_key(text)
        except CleanroomException:
            key = text.replace("\\", "/").rstrip("/").casefold()
        if key and key not in seen:
            seen.add(key)
            keys.append(key)
    return keys


def _normalize_hidden_asset_root_keys(paths: Iterable[str]) -> list[str]:
    """显隐配置只存绝对目录比较键，并按平台路径规则去重。"""
    keys: list[str] = []
    seen: set[str] = set()
    for raw_path in paths:
        if not isinstance(raw_path, str) or not raw_path.strip():
            raise CleanroomException(400, "INVALID_PATH", "隐藏素材根必须是绝对目录路径")
        try:
            key = storage.directory_path_key(raw_path)
        except CleanroomException:
            raise CleanroomException(400, "INVALID_PATH", "隐藏素材根必须是绝对目录路径") from None
        if key not in seen:
            seen.add(key)
            keys.append(key)
    return keys


def _path_matches_target_keys(candidate_path: str, target_keys: list[str]) -> bool:
    text = str(candidate_path or "").strip()
    if not text or not target_keys:
        return False
    try:
        cand_key = storage.directory_path_key(text)
    except CleanroomException:
        cand_key = text.replace("\\", "/").rstrip("/").casefold()
    for tkey in target_keys:
        if cand_key == tkey:
            return True
        sep = "\\" if "\\" in tkey else "/"
        prefix = tkey.rstrip("\\/")
        if cand_key.startswith((prefix + sep) if prefix else sep):
            return True
    return False


def _entry_matches_targets(
    entry: dict[str, Any], target_ids: set[str], target_keys: list[str]
) -> bool:
    eid = str(entry.get("id") or "").strip()
    if eid and eid in target_ids:
        return True
    epath = str(entry.get("path") or "").strip()
    return _path_matches_target_keys(epath, target_keys)


_DEFAULT_CASCADE_HOOKS: dict[str, Any] = {
    "cancel_roots": lambda target_ids, target_paths=None: None,
    "invalidate_caches": lambda: None,
    "readonly_registry_snapshot": lambda: {},
    "purging_root_markers": lambda: {},
    "root_facets": lambda: [],
    "mark_root_purging": lambda root_id, target_ids, target_paths, primary_path="": None,
    "clear_root_purging": lambda root_id: None,
    "find_purge_candidate_asset_ids": lambda root_id, alias_ids=None: [],
    "purge_root_records": lambda root_id, alias_ids=None, strict_search=True: {},
    "delete_media_outputs": lambda kind, payload: {"deleted": [], "failed": []},
}


def register_root_cascade_hooks(**hooks: Any) -> None:
    """由上层装配模块注入资产注册表与媒体缓存的级联操作，避免 settings 反向依赖上层领域包。"""
    for key, fn in hooks.items():
        if key in _DEFAULT_CASCADE_HOOKS and callable(fn):
            _DEFAULT_CASCADE_HOOKS[key] = fn


def _stop_index_jobs_and_invalidate_caches(
    target_ids: set[str], target_paths: list[str]
) -> None:
    _DEFAULT_CASCADE_HOOKS["cancel_roots"](target_ids, target_paths)
    _DEFAULT_CASCADE_HOOKS["invalidate_caches"]()


def _deduplicate_root_records(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    for item in records:
        path = str(item.get("path") or "").strip()
        key = storage.directory_path_key(path) if path else str(item.get("id") or "")
        if key not in merged:
            entry = dict(item)
            entry["aliases"] = list(item.get("aliases") or [])
            merged[key] = entry
        else:
            if item.get("active"):
                merged[key]["active"] = True
                if path:
                    merged[key]["path"] = path
            for alias in item.get("aliases") or []:
                if alias not in merged[key]["aliases"]:
                    merged[key]["aliases"].append(alias)
            if item.get("network_mount"):
                merged[key]["network_mount"] = True
    return list(merged.values())


class StorageAllowedRootsService:
    """环境变量只读根与持久化允许根的 CAS 管理服务。"""

    def __init__(self) -> None:
        self._state = storage.JsonState("allowed_roots", self._empty_state)

    @staticmethod
    def _empty_state() -> dict[str, Any]:
        if (storage.data_root() / "allowed_roots.json").exists():
            raise CleanroomException(
                503,
                "ALLOWED_ROOTS_STORAGE_UNAVAILABLE",
                "允许根配置无法读取，未覆盖已有配置",
            )
        return {"schema_version": 1, "revision": 1, "roots": [], "system_aliases": {}}

    @staticmethod
    def _expected_version(state: dict[str, Any], expected: int) -> int:
        current = int(state.get("revision", 1))
        if expected != current:
            raise _conflict(
                "VERSION_CONFLICT",
                expected,
                current,
                f"允许根版本冲突（期望 {expected}，当前 {current}），请重新读取后重试",
            )
        return current

    @staticmethod
    def _path_status(path: str) -> dict[str, str | bool]:
        status = storage.probe_mount(path)
        return {
            "availability": "available" if status["available"] else "offline",
            "availability_reason": str(status["reason"] or ""),
            "availability_message": str(status["message"] or ""),
        }

    def _collect_root_cascade_targets(
        self,
        root_id: str,
        roots_state: dict[str, Any],
        purging_markers: dict[str, Any] | None = None,
    ) -> tuple[set[str], list[str], str]:
        """收集允许根关联的全部身份 ID（root_*、local_extra_*、facets aliases）与路径。"""
        target_ids: set[str] = {str(root_id).strip()}
        target_paths: list[str] = []
        primary_path = ""

        def add_path(raw: Any) -> None:
            text = str(raw or "").strip()
            if text and text not in target_paths:
                target_paths.append(text)

        records = _deduplicate_root_records(roots_state.get("roots", []))
        matched_record = next((item for item in records if item.get("id") == root_id), None)
        if matched_record is not None:
            primary_path = str(matched_record.get("path") or "")
            add_path(primary_path)
            for alias in matched_record.get("aliases") or []:
                add_path(alias)
        target_keys = _normalized_target_keys(target_paths)
        for item in records:
            ipath = str(item.get("path") or "")
            if ipath and _path_matches_target_keys(ipath, target_keys):
                if item.get("id"):
                    target_ids.add(str(item["id"]))
                add_path(ipath)
                for alias in item.get("aliases") or []:
                    add_path(alias)

        markers = purging_markers if purging_markers is not None else {}
        marker = markers.get(root_id)
        if isinstance(marker, dict):
            if not primary_path and marker.get("primary_path"):
                primary_path = str(marker["primary_path"])
            for tid in marker.get("target_ids") or []:
                if tid:
                    target_ids.add(str(tid))
            for tpath in marker.get("target_paths") or []:
                add_path(tpath)

        target_keys = _normalized_target_keys(target_paths)
        try:
            settings_state = default_storage_settings_service._state.read()
            for bucket in ("extra_local_entries", "removed_extra_local_entries"):
                for entry in settings_state.get(bucket) or []:
                    if not isinstance(entry, dict):
                        continue
                    if _entry_matches_targets(entry, target_ids, target_keys):
                        for key in ("id", "kind"):
                            val = str(entry.get(key) or "").strip()
                            if val:
                                target_ids.add(val)
                        add_path(entry.get("path"))
        except Exception:
            pass

        try:
            registry_snapshot = _DEFAULT_CASCADE_HOOKS["readonly_registry_snapshot"]()
            for source_id, bound in (registry_snapshot.get("root_aliases") or {}).items():
                if not isinstance(bound, dict):
                    continue
                bound_root_id = str(bound.get("root_id") or "")
                if str(source_id) in target_ids or (bound_root_id and bound_root_id in target_ids):
                    target_ids.add(str(source_id))
                    for alias in bound.get("aliases") or []:
                        add_path(alias)
        except Exception:
            pass

        try:
            for facet in _DEFAULT_CASCADE_HOOKS["root_facets"]():
                facet_ids = {
                    str(item)
                    for item in [facet.get("id"), *(facet.get("aliases") or [])]
                    if item
                }
                if facet_ids & target_ids:
                    target_ids.update(facet_ids)
        except Exception:
            pass

        return {item for item in target_ids if item}, target_paths, primary_path

    def active_identities_for_root(self, root_id: str) -> list[str]:
        """返回恢复/重扫时归属该允许根的活跃身份 ID 列表（含 local_extra_*）。"""
        roots_state = self._state.read()
        target_ids, target_paths, _ = self._collect_root_cascade_targets(root_id, roots_state)
        target_keys = _normalized_target_keys(target_paths)
        identities = [root_id]
        try:
            snapshot = default_storage_settings_service.get_snapshot()
            for entry in snapshot.extra_local_entries:
                if _entry_matches_targets(entry, target_ids, target_keys):
                    eid = str(entry.get("id") or "").strip()
                    if eid and eid not in identities:
                        identities.append(eid)
        except Exception:
            pass
        return identities

    def get_snapshot(self, *, probe: bool = True) -> StorageAllowedRootsSnapshot:
        state = self._state.read()
        try:
            purging_markers = _DEFAULT_CASCADE_HOOKS["purging_root_markers"]()
        except Exception:
            purging_markers = {}
        if not isinstance(purging_markers, dict):
            purging_markers = {}

        def _probing_status() -> dict[str, str]:
            return {
                "availability": "probing",
                "availability_reason": "",
                "availability_message": "正在检查目录连接…",
            }

        roots: list[StorageAllowedRoot] = []
        for path in storage.environment_allowed_roots():
            path_text = str(path)
            root_id = storage.root_identity_id(path)
            aliases = [
                str(item)
                for item in state.get("system_aliases", {}).get(root_id, [])
            ]
            candidates = (
                [
                    item
                    for item in storage.mount_alias_candidates(path_text)
                    if storage.directory_path_key(item["path"])
                    not in {storage.directory_path_key(alias) for alias in aliases}
                ]
                if probe
                else []
            )
            roots.append(
                StorageAllowedRoot(
                    id=root_id,
                    path=path_text,
                    source="system",
                    read_only=True,
                    aliases=aliases,
                    alias_candidates=candidates,
                    **(self._path_status(path_text) if probe else _probing_status()),
                )
            )
        clean_records = _deduplicate_root_records(state.get("roots", []))
        seen_persistent_ids: set[str] = set()
        for record in clean_records:
            rid = str(record.get("id") or "")
            seen_persistent_ids.add(rid)
            active = bool(record.get("active"))
            path_text = str(record.get("path") or "")
            if rid in purging_markers:
                active = False
                status = {
                    "availability": "removed",
                    "availability_reason": "PURGE_INCOMPLETE",
                    "availability_message": "上次清除索引未完成，请重试清除索引",
                }
                candidates = []
            elif active:
                aliases = [str(item) for item in record.get("aliases", [])]
                if probe:
                    status = self._path_status(path_text)
                    candidates = [
                        item
                        for item in storage.mount_alias_candidates(path_text)
                        if storage.directory_path_key(item["path"])
                        not in {storage.directory_path_key(alias) for alias in aliases}
                    ]
                else:
                    status = _probing_status()
                    candidates = []
            else:
                status = {
                    "availability": "removed",
                    "availability_reason": "MOUNT_ROOT_REMOVED",
                    "availability_message": "允许根已移除；已有索引与源文件保留",
                }
                candidates = []
            roots.append(
                StorageAllowedRoot(
                    id=rid,
                    path=path_text,
                    source="persistent",
                    read_only=False,
                    active=active,
                    aliases=[str(item) for item in record.get("aliases", [])],
                    alias_candidates=candidates,
                    **status,
                )
            )
        # 崩溃重启恢复投影：若 Step 4 删完 allowed_roots 后、清墓碑前退出，
        # 重启后仍在快照中暴露 PURGE_INCOMPLETE，支持再次触发幂等重放。
        for pid, pmeta in sorted(purging_markers.items()):
            if pid in seen_persistent_ids or not isinstance(pmeta, dict):
                continue
            roots.append(
                StorageAllowedRoot(
                    id=str(pid),
                    path=str(pmeta.get("primary_path") or ""),
                    source="persistent",
                    read_only=False,
                    active=False,
                    aliases=[],
                    alias_candidates=[],
                    availability="removed",
                    availability_reason="PURGE_INCOMPLETE",
                    availability_message="上次清除索引未完成，请重试清除索引",
                )
            )
        return StorageAllowedRootsSnapshot(revision=int(state.get("revision", 1)), roots=roots)

    def add_root(
        self, payload: StorageAllowedRootRequest, *, registration_operation_id: str | None = None
    ) -> StorageAllowedRootsSnapshot:
        # QIN-596：允许根写入口先做系统目录词法拒绝（先于 normalize——`C:`、
        # `\\NAS` 这类输入会被 normalize 先抛 400 INVALID_PATH，到不了清单）。
        rejection = storage.describe_resource_folder_rejection(payload.path)
        if rejection:
            raise CleanroomException(403, "RESOURCE_FOLDER_NOT_PERMITTED", rejection)
        normalized = storage.normalize_directory_path(payload.path)
        # QIN-596（审核 FIX_REQUIRED）：normalize 消解点段后对最终路径再执行
        # 一次清单策略——`C:/foo/../Windows` 规范化为 `C:\Windows` 必须被拒。
        normalized_rejection = storage.describe_resource_folder_rejection(normalized)
        if normalized_rejection:
            raise CleanroomException(403, "RESOURCE_FOLDER_NOT_PERMITTED", normalized_rejection)
        status = storage.probe_mount(normalized)
        if not status["available"]:
            code = str(status["reason"] or "MOUNT_OFFLINE")
            raise CleanroomException(503 if code == "MOUNT_OFFLINE" else 403, code, str(status["message"]))

        target_key = storage.directory_path_key(normalized)
        system_keys = {
            storage.directory_path_key(str(root))
            for root in storage.environment_allowed_roots()
        }
        if target_key in system_keys:
            raise CleanroomException(409, "ALLOWED_ROOT_ALREADY_REGISTERED", "该目录已登记为允许根")

        candidate_id = storage.root_identity_id(Path(normalized))
        purging_markers = _DEFAULT_CASCADE_HOOKS["purging_root_markers"]()
        if candidate_id in purging_markers or any(
            _path_matches_target_keys(normalized, _normalized_target_keys(meta.get("target_paths") or []))
            for meta in purging_markers.values()
            if isinstance(meta, dict)
        ):
            raise CleanroomException(
                409, "PURGE_IN_PROGRESS", "该资源文件夹上次清除索引未完成，请先完成清除索引"
            )

        reactivated_id: str | None = None

        def update(state: dict[str, Any]) -> None:
            nonlocal reactivated_id
            current = self._expected_version(state, payload.expected_version)
            state["roots"] = _deduplicate_root_records(state.get("roots", []))
            existing_active = None
            existing_inactive = None
            for record in state.get("roots", []):
                rec_keys = {storage.directory_path_key(str(record.get("path") or ""))}
                rec_keys.update(storage.directory_path_key(str(alias)) for alias in record.get("aliases", []))
                if target_key in rec_keys:
                    if record.get("active"):
                        existing_active = record
                        break
                    existing_inactive = record

            if existing_active is not None:
                raise CleanroomException(409, "ALLOWED_ROOT_ALREADY_REGISTERED", "该目录已登记为允许根")

            if existing_inactive is not None:
                if registration_operation_id:
                    raise CleanroomException(409, "MOUNT_ROOT_REMOVED", "已移除的允许根须先单独确认恢复")
                existing_inactive["active"] = True
                existing_inactive["path"] = normalized
                existing_inactive["network_mount"] = storage._is_network_path(normalized)
                reactivated_id = str(existing_inactive.get("id") or "")
            else:
                root_id = storage.root_identity_id(Path(normalized))
                record = {
                        "id": root_id,
                        "path": normalized,
                        "aliases": [],
                        "active": True,
                        "network_mount": storage._is_network_path(normalized),
                    }
                if registration_operation_id:
                    record["_resource_folder_registration_id"] = registration_operation_id
                state.setdefault("roots", []).append(record)
            state["revision"] = current + 1

        self._state.mutate(update)
        if reactivated_id:
            default_storage_settings_service.restore_removed_entries({reactivated_id}, [normalized])
        _DEFAULT_CASCADE_HOOKS["invalidate_caches"]()
        return self.get_snapshot()

    def finish_resource_folder_registration(
        self, root_id: str, path: str, operation_id: str, *, rollback: bool
    ) -> bool:
        """仅处理登记操作标记的允许根；不会移出来源或触碰素材文件。"""
        changed = False
        target_key = storage.directory_path_key(path)

        def update(state: dict[str, Any]) -> None:
            nonlocal changed
            records = _deduplicate_root_records(state.get("roots", []))
            for record in records:
                if (
                    str(record.get("id") or "") == root_id
                    and storage.directory_path_key(str(record.get("path") or "")) == target_key
                    and record.get("_resource_folder_registration_id") == operation_id
                ):
                    if rollback and record.get("active") and not record.get("aliases"):
                        records.remove(record)
                    else:
                        record.pop("_resource_folder_registration_id", None)
                    state["roots"] = records
                    state["revision"] = int(state.get("revision", 1)) + 1
                    changed = True
                    return

        self._state.mutate(update)
        if changed:
            _DEFAULT_CASCADE_HOOKS["invalidate_caches"]()
        return changed

    def remove_root(
        self, root_id: str, payload: StorageAllowedRootDeleteRequest
    ) -> StorageAllowedRootsSnapshot:
        system_ids = {storage.root_identity_id(path) for path in storage.environment_allowed_roots()}
        if root_id in system_ids:
            raise CleanroomException(403, "SYSTEM_ROOT_READ_ONLY", "环境变量系统根为只读，不能移除")
        if root_id in _DEFAULT_CASCADE_HOOKS["purging_root_markers"]():
            raise CleanroomException(
                409, "PURGE_IN_PROGRESS", "该资源文件夹上次清除索引未完成，请先完成清除索引"
            )

        initial_state = self._state.read()
        target_ids, target_paths, _ = self._collect_root_cascade_targets(root_id, initial_state)
        prev_aliases: list[str] = []
        mutated_revision: int | None = None

        def update(state: dict[str, Any]) -> None:
            nonlocal prev_aliases, mutated_revision
            current = self._expected_version(state, payload.expected_version)
            state["roots"] = _deduplicate_root_records(state.get("roots", []))
            record = next(
                (item for item in state.get("roots", []) if item.get("id") == root_id and item.get("active")),
                None,
            )
            if record is None:
                raise CleanroomException(404, "ROOT_NOT_FOUND", "允许根不存在或已移除")
            prev_aliases = [str(item) for item in record.get("aliases") or []]
            record["active"] = False
            record["aliases"] = []
            mutated_revision = current + 1
            state["revision"] = mutated_revision

        self._state.mutate(update)
        try:
            default_storage_settings_service.move_entries_to_removed(target_ids, target_paths)
        except Exception:
            # 补偿回滚：Step 2 写 storage_settings.json 失败时恢复允许根活跃态，不留半写入。
            def rollback(state: dict[str, Any]) -> None:
                if mutated_revision is not None and int(state.get("revision", 1)) != mutated_revision:
                    return
                rec = next((item for item in state.get("roots", []) if item.get("id") == root_id), None)
                if rec is not None:
                    rec["active"] = True
                    rec["aliases"] = prev_aliases
                    state["revision"] = int(state.get("revision", 1)) + 1

            try:
                self._state.mutate(rollback)
            except Exception:
                pass
            raise

        _stop_index_jobs_and_invalidate_caches(target_ids, target_paths)
        return self.get_snapshot()

    def restore_root(
        self, root_id: str, payload: StorageAllowedRootDeleteRequest
    ) -> StorageAllowedRootsSnapshot:
        system_ids = {storage.root_identity_id(path) for path in storage.environment_allowed_roots()}
        if root_id in system_ids:
            raise CleanroomException(403, "SYSTEM_ROOT_READ_ONLY", "环境变量系统根为只读")
        if root_id in _DEFAULT_CASCADE_HOOKS["purging_root_markers"]():
            raise CleanroomException(
                409, "PURGE_IN_PROGRESS", "该资源文件夹上次清除索引未完成，请先完成清除索引"
            )

        initial_state = self._state.read()
        target_ids, target_paths, _ = self._collect_root_cascade_targets(root_id, initial_state)
        restored_path: str | None = None
        mutated_revision: int | None = None

        def update(state: dict[str, Any]) -> None:
            nonlocal restored_path, mutated_revision
            current = self._expected_version(state, payload.expected_version)
            state["roots"] = _deduplicate_root_records(state.get("roots", []))
            record = next(
                (item for item in state.get("roots", []) if item.get("id") == root_id),
                None,
            )
            if record is None:
                raise CleanroomException(404, "ROOT_NOT_FOUND", "允许根不存在")
            if record.get("active"):
                raise CleanroomException(409, "ALLOWED_ROOT_ALREADY_ACTIVE", "该允许根已处于活跃状态")

            restored_path = str(record.get("path") or "")
            # QIN-596：恢复入口同样执行拒绝清单，防止本功能上线前移除的
            # 盘符根 / 系统目录经「恢复已移除允许根」静默回归。
            rejection = storage.describe_resource_folder_rejection(restored_path)
            if rejection:
                raise CleanroomException(403, "RESOURCE_FOLDER_NOT_PERMITTED", rejection)
            # QIN-596（审核 FIX_REQUIRED）：恢复前对 normalize 后的最终路径再执行
            # 一次清单策略（点段消解后命中清单的记录不得静默回归）。
            try:
                normalized_restored = storage.normalize_directory_path(restored_path)
            except CleanroomException:
                normalized_restored = ""
            if normalized_restored:
                normalized_rejection = storage.describe_resource_folder_rejection(normalized_restored)
                if normalized_rejection:
                    raise CleanroomException(403, "RESOURCE_FOLDER_NOT_PERMITTED", normalized_rejection)
            status = storage.probe_mount(restored_path)
            if not status["available"]:
                code = str(status["reason"] or "MOUNT_OFFLINE")
                raise CleanroomException(503 if code == "MOUNT_OFFLINE" else 403, code, str(status["message"]))

            record["active"] = True
            mutated_revision = current + 1
            state["revision"] = mutated_revision

        self._state.mutate(update)
        try:
            default_storage_settings_service.restore_removed_entries(target_ids, target_paths)
        except Exception:
            def rollback(state: dict[str, Any]) -> None:
                if mutated_revision is not None and int(state.get("revision", 1)) != mutated_revision:
                    return
                rec = next((item for item in state.get("roots", []) if item.get("id") == root_id), None)
                if rec is not None:
                    rec["active"] = False
                    state["revision"] = int(state.get("revision", 1)) + 1

            try:
                self._state.mutate(rollback)
            except Exception:
                pass
            raise

        _DEFAULT_CASCADE_HOOKS["invalidate_caches"]()
        return self.get_snapshot()

    def purge_root(
        self, root_id: str, payload: StorageAllowedRootDeleteRequest
    ) -> StorageAllowedRootsSnapshot:
        # Step 0（全部前置校验，写任何状态或墓碑前完成）
        system_ids = {storage.root_identity_id(path) for path in storage.environment_allowed_roots()}
        if root_id in system_ids:
            raise CleanroomException(403, "SYSTEM_ROOT_READ_ONLY", "环境变量系统根为只读，不能彻底删除")

        roots_state = self._state.read()
        self._expected_version(roots_state, payload.expected_version)
        deduped_roots = _deduplicate_root_records(roots_state.get("roots", []))
        record = next((item for item in deduped_roots if item.get("id") == root_id), None)
        purging_markers = _DEFAULT_CASCADE_HOOKS["purging_root_markers"]()
        if record is None and root_id not in purging_markers:
            raise CleanroomException(404, "ROOT_NOT_FOUND", "允许根不存在")

        target_ids, target_paths, primary_path = self._collect_root_cascade_targets(
            root_id, roots_state, purging_markers
        )

        # Step 1：持久化清除墓碑并立即取消相关在途索引任务
        _DEFAULT_CASCADE_HOOKS["mark_root_purging"](
            root_id, target_ids, target_paths, primary_path=primary_path
        )
        _stop_index_jobs_and_invalidate_caches(target_ids, target_paths)

        # Step 2：先清理缩略图与分镜缓存（失败不删 assets，保留重放时的资产 ID 线索）
        candidate_ids = _DEFAULT_CASCADE_HOOKS["find_purge_candidate_asset_ids"](root_id, target_ids)
        if candidate_ids:
            try:
                thumb_res = _DEFAULT_CASCADE_HOOKS["delete_media_outputs"]("thumbnails", {"asset_ids": candidate_ids})
                sb_res = _DEFAULT_CASCADE_HOOKS["delete_media_outputs"]("storyboards", {"asset_ids": candidate_ids})
            except CleanroomException:
                raise
            except Exception as exc:
                raise CleanroomException(
                    503, "CACHE_DELETE_FAILED", f"清除缩略图或分镜缓存失败：{exc}"
                ) from exc
            if (thumb_res or {}).get("failed") or (sb_res or {}).get("failed"):
                raise CleanroomException(
                    503,
                    "CACHE_DELETE_FAILED",
                    "部分缩略图或分镜缓存清除失败，请重试清除索引",
                )

        # Step 3：双键清理注册表与 SQLite 检索文档（strict_search=True，失败不吞错）
        _DEFAULT_CASCADE_HOOKS["purge_root_records"](root_id, alias_ids=target_ids, strict_search=True)

        # Step 4：清理来源登记与允许根配置，全部成功后清理墓碑
        default_storage_settings_service.purge_entries(target_ids, target_paths)

        target_keys = set(_normalized_target_keys(target_paths))

        def update(state: dict[str, Any]) -> None:
            current = self._expected_version(state, payload.expected_version)
            state["roots"] = _deduplicate_root_records(state.get("roots", []))
            remaining = []
            removed_any = False
            for item in state.get("roots", []):
                ipath = str(item.get("path") or "")
                ikey = ""
                if ipath:
                    try:
                        ikey = storage.directory_path_key(ipath)
                    except CleanroomException:
                        ikey = ""
                if item.get("id") == root_id or (ikey and ikey in target_keys):
                    removed_any = True
                else:
                    remaining.append(item)
            if removed_any:
                state["roots"] = remaining
                state["revision"] = current + 1

        self._state.mutate(update)
        _DEFAULT_CASCADE_HOOKS["clear_root_purging"](root_id)
        _DEFAULT_CASCADE_HOOKS["invalidate_caches"]()
        return self.get_snapshot()

    def confirm_alias(
        self, root_id: str, payload: StorageAllowedRootRequest
    ) -> StorageAllowedRootsSnapshot:
        normalized = storage.normalize_directory_path(payload.path)
        roots_state = self._state.read()
        self._expected_version(roots_state, payload.expected_version)
        record = next(
            (item for item in roots_state.get("roots", []) if item.get("id") == root_id and item.get("active")),
            None,
        )
        system_root = next(
            (path for path in storage.environment_allowed_roots() if storage.root_identity_id(path) == root_id),
            None,
        )
        if record is None and system_root is None:
            raise CleanroomException(404, "ROOT_NOT_FOUND", "允许根不存在或已移除")
        root_path = str(record.get("path") or "") if record else str(system_root)
        for path in (root_path, normalized):
            status = storage.probe_mount(path)
            if not status["available"]:
                code = str(status["reason"] or "MOUNT_OFFLINE")
                raise CleanroomException(503 if code == "MOUNT_OFFLINE" else 403, code, str(status["message"]))
        same_mount = storage.verify_mount_alias(root_path, normalized)
        if same_mount is None:
            raise CleanroomException(503, "MOUNT_OFFLINE", "挂载别名核验超时，路径暂时离线")
        if not same_mount:
            raise CleanroomException(409, "MOUNT_ALIAS_MISMATCH", "该路径未被系统证明为同一挂载点")

        def update(state: dict[str, Any]) -> None:
            current = self._expected_version(state, payload.expected_version)
            current_record = next(
                (item for item in state.get("roots", []) if item.get("id") == root_id and item.get("active")),
                None,
            )
            if current_record is None and not any(
                storage.root_identity_id(path) == root_id
                for path in storage.environment_allowed_roots()
            ):
                raise CleanroomException(404, "ROOT_NOT_FOUND", "允许根不存在或已移除")
            if current_record is None:
                aliases = state.setdefault("system_aliases", {}).setdefault(root_id, [])
            else:
                aliases = current_record.setdefault("aliases", [])
                if storage._is_network_path(normalized):
                    current_record["network_mount"] = True
            if not any(storage.directory_path_key(item) == storage.directory_path_key(normalized) for item in aliases):
                aliases.append(normalized)
                state["revision"] = current + 1

        self._state.mutate(update)
        return self.get_snapshot()


class ProviderService:
    """平台与密文单快照原子落盘；各实例共享命名空间CAS锁。"""

    _secrets = {
        "api_key": ("clear_key", "has_key"),
        "wallet_api_key": ("clear_wallet_key", "has_wallet_key"),
        "volcengine_access_key_id": ("clear_volcengine_access_key_id", "has_volcengine_access_key"),
        "volcengine_secret_access_key": ("clear_volcengine_secret_access_key", "has_volcengine_secret_key"),
    }

    def __init__(self) -> None:
        self._state = storage.JsonState("providers", self._empty_state)

    @staticmethod
    def _empty_state():
        # 通用JSON读器对损坏文件返回默认值；配置集合必须拒绝该降级，避免下一次保存清空。
        if (storage.data_root() / "providers.json").exists():
            raise CleanroomException(503, "PROVIDER_STORAGE_UNAVAILABLE", "平台配置文件无法读取，未覆盖已有配置")
        return {"schema_version": 1, "revision": 1, "providers": [], "credentials": {}}

    def _checked_state(self, state):
        if (not isinstance(state, dict) or state.get("schema_version") != 1
                or set(state) != {"schema_version", "revision", "providers", "credentials"}
                or type(state.get("revision")) is not int or state["revision"] < 1
                or not isinstance(state.get("providers"), list) or not isinstance(state.get("credentials"), dict)):
            raise CleanroomException(503, "PROVIDER_STORAGE_UNAVAILABLE", "平台配置快照格式无效")
        try:
            seen = set()
            for item in state["providers"]:
                model = ProviderEntryRequest.model_validate(item)
                provider_id = model.provider_id or model.id
                if provider_id in seen or any(key in item for key in self._secrets):
                    raise ValueError()
                seen.add(provider_id)
            if set(state["credentials"]) - seen:
                raise ValueError()
            for fields in state["credentials"].values():
                if not isinstance(fields, dict) or set(fields) - set(self._secrets):
                    raise ValueError()
                if any(not isinstance(value, str) or not value for value in fields.values()):
                    raise ValueError()
        except (ValidationError, ValueError, TypeError):
            raise CleanroomException(503, "PROVIDER_STORAGE_UNAVAILABLE", "平台配置快照格式无效") from None
        return state

    @staticmethod
    def _check_files():
        """读取与写入前拒绝目标/临时文件链接，避免通用JSON读器跟随链接。"""
        for name in ("providers.json", "providers.json.tmp"):
            path = storage.data_root() / name
            try:
                info = path.lstat()
            except FileNotFoundError:
                continue
            if (not stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode)
                    or getattr(info, "st_file_attributes", 0) & 0x400):
                raise CleanroomException(503, "PROVIDER_STORAGE_UNAVAILABLE", "平台配置文件不允许链接或异常类型")

    def get_snapshot(self) -> ProviderSnapshot:
        self._check_files()
        return self._snapshot(self._checked_state(self._state.read()))

    def _snapshot(self, state) -> ProviderSnapshot:
        public = copy.deepcopy(state["providers"])
        for item in public:
            provider_id = item["provider_id"]
            item["source"] = "settings"
            for field, (_, flag) in self._secrets.items():
                encrypted = state["credentials"].get(provider_id, {}).get(field)
                item[flag] = bool(encrypted and credentials.reveal(provider_id, field, encrypted))
        configured = bool(public)
        return ProviderSnapshot(
            providers=public,
            revision=state["revision"],
            configured=configured,
            data_status=DATA_STATUS_OK if configured else DATA_STATUS_NOT_CONFIGURED,
            data_gaps=[] if configured else [GAP_PROVIDER_REGISTRY_SOURCE],
        )

    def replace(self, raw_providers: Any, expected_version: int | None) -> ProviderSnapshot:
        """整体验证后持锁提交；空密钥保持、clear显式删除、非空密钥替换。"""
        _require_positive_version(expected_version, "provider 集合")
        if expected_version is None:
            raise CleanroomException(400, "EXPECTED_VERSION_REQUIRED", "保存平台配置必须携带expected_version")
        if not isinstance(raw_providers, list) or len(raw_providers) > 128:
            raise CleanroomException(
                status_code=400,
                code="INVALID_REQUEST",
                message="provider 请求体必须为数组",
            )
        normalized: list[ProviderEntryRequest] = []
        seen: set = set()
        for index, item in enumerate(raw_providers):
            if not isinstance(item, dict):
                raise CleanroomException(
                    status_code=400,
                    code="INVALID_REQUEST",
                    message=f"provider 第 {index + 1} 项必须为对象",
                )
            try:
                model = ProviderEntryRequest.model_validate(item)
            except ValidationError:
                # Pydantic异常包含input；不可把原文或字段值写入响应与日志。
                raise CleanroomException(400, "INVALID_REQUEST", f"平台第{index + 1}项字段或更新意图不合法") from None
            provider_id = model.provider_id or model.id
            if provider_id in seen:
                raise CleanroomException(
                    status_code=400,
                    code="INVALID_REQUEST",
                    message=f"provider_id 重复：{provider_id}",
                )
            seen.add(provider_id)
            normalized.append(model)

        def commit(state):
            self._checked_state(state)
            if expected_version != state["revision"]:
                raise _conflict(
                    "VERSION_CONFLICT",
                    expected_version,
                    state["revision"],
                    "平台集合已被修改，请保留编辑内容并重新读取后重试",
                )
            public, encrypted = [], {}
            excluded = set(self._secrets) | {value[0] for value in self._secrets.values()}
            for model in normalized:
                provider_id = model.provider_id or model.id
                fields = copy.deepcopy(state["credentials"].get(provider_id, {}))
                for field, (clear, _) in self._secrets.items():
                    if getattr(model, clear):
                        fields.pop(field, None)
                    elif (getattr(model, field) or "").strip():
                        fields[field] = credentials.protect(provider_id, field, getattr(model, field).strip())
                item = model.model_dump(exclude=excluded)
                item["id"] = item["provider_id"] = provider_id
                public.append(item)
                if fields:
                    encrypted[provider_id] = fields
            state.update(providers=public, credentials=encrypted, revision=state["revision"] + 1)
            return self._snapshot(state)

        try:
            self._check_files()
            return self._state.mutate(commit)
        except OSError:
            raise CleanroomException(503, "PROVIDER_STORAGE_UNAVAILABLE", "平台配置未能落盘，原配置保持不变") from None

    def credential(self, provider_id: str, field: str = "api_key", *, expected_version: int | None = None) -> str:
        """仅供执行服务按稳定ID读取；HTTP响应不得调用后回显原文。"""
        if field not in self._secrets:
            raise ValueError("凭据字段不受支持")
        self._check_files()
        state = self._checked_state(self._state.read())
        if expected_version is not None and state["revision"] != expected_version:
            raise _conflict("VERSION_CONFLICT", expected_version, state["revision"],
                            "平台配置已变化，未使用新密钥执行旧请求，请重新开始操作")
        encrypted = state["credentials"].get(provider_id, {}).get(field)
        return credentials.reveal(provider_id, field, encrypted) if encrypted else ""


class AssetStructureService:
    """素材版本/组结构（stable structure_id）的内存存储与 CAS 版本服务。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._revision = 1
        self._sequence = 0
        self._structures: dict[str, AssetStructureItem] = {}

    # ------------------------------------------------------------------
    # 查询
    # ------------------------------------------------------------------
    def list_structures(self, asset_ids: Sequence[str] | None = None) -> AssetStructureListResponse:
        """返回结构集合；给定 asset_ids 时按成员做真实过滤。"""
        wanted = {str(item or "").strip() for item in (asset_ids or []) if str(item or "").strip()}
        with self._lock:
            items = [copy.deepcopy(item) for item in self._sorted_structures_locked()]
        if wanted:
            items = [
                item for item in items
                if any(member.asset_id in wanted for member in item.members)
            ]
        return AssetStructureListResponse(
            items=items,
            total=len(items),
            revision=self._revision,
            data_status=DATA_STATUS_OK,
            data_gaps=[GAP_MEMBER_ASSET_METADATA],
        )

    def get_structure(self, structure_id: str) -> AssetStructureItem:
        """读取单个结构；不存在必须 404 FAIL-CLOSED。"""
        with self._lock:
            item = self._structures.get(str(structure_id or ""))
            if item is None:
                raise self._not_found(structure_id)
            return copy.deepcopy(item)

    def _sorted_structures_locked(self) -> list[AssetStructureItem]:
        return [self._structures[key] for key in sorted(self._structures)]

    @staticmethod
    def _not_found(structure_id: str) -> CleanroomException:
        return CleanroomException(
            status_code=404,
            code="STRUCTURE_NOT_FOUND",
            message=f"结构 {structure_id} 不存在",
        )

    @staticmethod
    def _next_id(sequence: int) -> str:
        return f"strc_{sequence:04d}"

    # ------------------------------------------------------------------
    # 写入
    # ------------------------------------------------------------------
    def create_structure(self, payload: AssetStructureCreateRequest) -> AssetStructureItem:
        """创建结构；集合 CAS 冲突失败关闭，成功则集合 revision 递增。"""
        kind = str(payload.kind or "").strip().lower()
        if kind not in ALLOWED_STRUCTURE_KINDS:
            raise CleanroomException(
                status_code=400,
                code="INVALID_REQUEST",
                message=f"结构类型不合法：{payload.kind}",
            )
        asset_ids = _normalize_asset_ids(payload.asset_ids, "asset_ids")
        current_asset_id = self._resolve_current(payload.current_asset_id, asset_ids)
        _require_positive_version(payload.expected_version, "结构集合")

        with self._lock:
            if payload.expected_version is not None and payload.expected_version != self._revision:
                raise _conflict(
                    "STRUCTURE_VERSION_CONFLICT",
                    payload.expected_version,
                    self._revision,
                    f"结构集合版本冲突（期望 {payload.expected_version}，当前 {self._revision}），请重新读取后重试",
                )
            self._sequence += 1
            now = _now_iso()
            structure = AssetStructureItem(
                structure_id=self._next_id(self._sequence),
                kind=kind,
                current_asset_id=current_asset_id,
                version=1,
                created_at=now,
                updated_at=now,
                members=self._build_members(asset_ids),
            )
            self._structures[structure.structure_id] = structure
            self._revision += 1
            return copy.deepcopy(structure)

    def update_structure(self, structure_id: str, payload: AssetStructureUpdateRequest) -> AssetStructureItem:
        """更新结构；结构级 CAS 冲突失败关闭，成功则结构 version 递增。"""
        if payload.asset_ids is None and payload.current_asset_id is None:
            raise CleanroomException(
                status_code=400,
                code="INVALID_REQUEST",
                message="更新结构至少需要 asset_ids 或 current_asset_id 之一",
            )
        expected = payload.expected_version
        _require_positive_version(expected, "结构")
        with self._lock:
            item = self._structures.get(str(structure_id or ""))
            if item is None:
                raise self._not_found(structure_id)
            if expected is not None and expected != item.version:
                raise _conflict(
                    "STRUCTURE_VERSION_CONFLICT",
                    expected,
                    item.version,
                    f"结构 {item.structure_id} 版本冲突（期望 {expected}，当前 {item.version}），请重新读取后重试",
                )
            asset_ids = (
                _normalize_asset_ids(payload.asset_ids, "asset_ids")
                if payload.asset_ids is not None
                else [member.asset_id for member in item.members]
            )
            requested_current = payload.current_asset_id if payload.current_asset_id is not None else item.current_asset_id
            current_asset_id = self._resolve_current(requested_current, asset_ids)
            updated = item.model_copy(
                update={
                    "current_asset_id": current_asset_id,
                    "version": item.version + 1,
                    "updated_at": _now_iso(),
                    "members": self._build_members(asset_ids),
                }
            )
            self._structures[item.structure_id] = updated
            return copy.deepcopy(updated)

    def set_current(self, structure_id: str, payload: AssetStructureCurrentRequest) -> AssetStructureItem:
        """切换当前代表素材；结构级 CAS 冲突失败关闭。"""
        expected = payload.expected_version
        _require_positive_version(expected, "结构")
        with self._lock:
            item = self._structures.get(str(structure_id or ""))
            if item is None:
                raise self._not_found(structure_id)
            if expected is not None and expected != item.version:
                raise _conflict(
                    "STRUCTURE_VERSION_CONFLICT",
                    expected,
                    item.version,
                    f"结构 {item.structure_id} 版本冲突（期望 {expected}，当前 {item.version}），请重新读取后重试",
                )
            member_ids = [member.asset_id for member in item.members]
            current_asset_id = self._resolve_current(payload.current_asset_id, member_ids)
            updated = item.model_copy(
                update={
                    "current_asset_id": current_asset_id,
                    "version": item.version + 1,
                    "updated_at": _now_iso(),
                }
            )
            self._structures[item.structure_id] = updated
            return copy.deepcopy(updated)

    def delete_structure(self, structure_id: str, expected_version: int | None) -> tuple[str, int]:
        """删除结构；结构级 CAS 冲突失败关闭，成功则集合 revision 递增。"""
        _require_positive_version(expected_version, "结构")
        with self._lock:
            item = self._structures.get(str(structure_id or ""))
            if item is None:
                raise self._not_found(structure_id)
            if expected_version is not None and expected_version != item.version:
                raise _conflict(
                    "STRUCTURE_VERSION_CONFLICT",
                    expected_version,
                    item.version,
                    f"结构 {item.structure_id} 版本冲突（期望 {expected_version}，当前 {item.version}），请重新读取后重试",
                )
            del self._structures[item.structure_id]
            self._revision += 1
            return item.structure_id, self._revision

    # ------------------------------------------------------------------
    # 内部工具
    # ------------------------------------------------------------------
    @staticmethod
    def _build_members(asset_ids: Iterable[str]) -> list[AssetStructureMember]:
        """按调用方给定顺序建立成员；不编造任何素材元数据。"""
        return [
            AssetStructureMember(asset_id=asset_id, sort_order=index)
            for index, asset_id in enumerate(asset_ids)
        ]

    @staticmethod
    def _resolve_current(raw_current: str | None, asset_ids: Sequence[str]) -> str:
        """解析当前代表素材；缺省取首个成员，非成员一律 400。"""
        requested = str(raw_current or "").strip()
        if not requested:
            return asset_ids[0]
        if requested not in set(asset_ids):
            raise CleanroomException(
                status_code=400,
                code="INVALID_REQUEST",
                message=f"current_asset_id 必须属于成员：{requested}",
            )
        return requested


def raise_probe_not_integrated(endpoint: str) -> None:
    """探测端点统一 fail-closed：本阶段无真实出网能力，如实返回未接入错误包。"""
    raise CleanroomException(
        status_code=503,
        code=PROVIDER_PROBE_NOT_INTEGRATED,
        message=(
            f"{endpoint} 在本阶段无真实外网探测能力，已按洁净室口径拒绝执行；"
            "未返回任何模型列表 / 连通性结论 / 延迟数字。"
        ),
        extra={"endpoint": endpoint, "unavailable": True},
    )


#: 进程内服务单例；设置 JSON 可重启恢复，结构服务仅进程内有效，不支持多 worker 共享。
default_storage_settings_service = StorageSettingsService()
default_storage_allowed_roots_service = StorageAllowedRootsService()
default_provider_service = ProviderService()
default_asset_structure_service = AssetStructureService()
