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

"""提示词库服务层实现（Phase 10C）。

严格遵循 docs/contracts/PROMPT-LIBRARY-INTERFACE-CATALOG.yaml（p12-a3-2）：

- 稳定 ID：library_id（plib_NNNN）/ category_id（pcat_NNNN），确定性且不含随机数；
- CAS：创建库校验顶层目录 version；创建分类校验父库 version；
  重命名/删除库校验目标库 version；重命名/删除分类校验目标分类 version；
- 重名（trim 后大小写不敏感）返回 409 DUPLICATE_LIBRARY_NAME / DUPLICATE_CATEGORY_NAME；
- 删除仍含分类的提示词库返回 409 LIBRARY_NOT_EMPTY，**禁止静默级联删除**；
- 空目录返回 libraries: [] 且 active_library_id: null；
- **零伪造**：本服务不产生任何提示词文本（positive/negative/scene 与条目均不存在），
  种子只注入结构（空库 / 空分类），绝不注入演示或随机内容。

证据边界：提示词库树（库、分类、活动库、目录版本与 ID 高水位）使用
``core.storage.JsonState`` 命名空间 ``prompt_library_tree`` 落盘；同一数据目录重启可恢复，
同进程父子写通过共享域锁串行；多 worker / 多进程并发写一致性仍属部署方职责。历史条目命名空间不删除；首次升级读取其父 ID
高水位，防止新父库/分类复用旧 ID 并误接收遗留条目。
"""

from __future__ import annotations

import copy
from datetime import datetime, timezone
import threading
from typing import Dict, List, Optional, Tuple

from gw.core import storage
from gw.core.errors import CleanroomException, VersionConflictException
from gw.prompt_library import coordination
from gw.prompt_library.models import (
    PromptCategoryCreateRequest,
    PromptCategoryItem,
    PromptCategoryRenameRequest,
    PromptLibraryCreateRequest,
    PromptLibraryItem,
    PromptLibraryRenameRequest,
    PromptLibrarySnapshot,
)


def _now_iso() -> str:
    """生成标准 ISO 8601 UTC 时间戳字符串。"""
    return datetime.now(timezone.utc).isoformat()


def _name_key(value: str) -> str:
    """重名比较键：trim 后大小写不敏感。"""
    return value.strip().casefold()


def _require_name(raw: str, label: str) -> str:
    """校验并规范化名称；全空白直接失败关闭，避免写入无意义结构。"""
    name = (raw or "").strip()
    if not name:
        raise CleanroomException(
            status_code=400,
            code="INVALID_REQUEST",
            message=f"{label}不能为空或全空白",
        )
    return name


def _require_positive_version(expected_version: Optional[int], label: str) -> None:
    """expected_version 若提供必须为正整数；否则 400。"""
    if expected_version is not None and (not isinstance(expected_version, int) or expected_version < 1):
        raise CleanroomException(
            status_code=400,
            code="INVALID_REQUEST",
            message=f"{label} expected_version 必须为正整数",
        )


def _assert_expected_version(expected_version: Optional[int], current_version: int, label: str) -> None:
    """CAS 校验：期望值与当前值不一致即 409，禁止静默覆盖。"""
    if expected_version is None:
        return
    if expected_version != current_version:
        raise VersionConflictException(
            expected_version=expected_version,
            current_version=current_version,
            message=f"{label}版本冲突，请重新读取后重试",
        )


NS_PROMPT_TREE = "prompt_library_tree"
NS_PROMPT_ITEMS = "prompt_library"


def _blank_prompt_tree() -> Dict[str, object]:
    return {
        "schema_version": 1,
        "directory_version": 1,
        "active_library_id": None,
        "libraries": {},
        "library_sequence": 0,
        "category_sequence": 0,
    }


def _sequence_for(prefix: str, values: List[str]) -> int:
    maximum = 0
    for value in values:
        tail = str(value or "").removeprefix(prefix)
        if tail.isdigit():
            maximum = max(maximum, int(tail))
    return maximum


class PromptLibraryService:
    """提示词库持久化与 CAS 状态服务。"""

    def __init__(self, seed_structural_fixture: bool = False) -> None:
        self._lock = threading.Lock()
        self._seed_structural_fixture = seed_structural_fixture
        self._loaded_root: Optional[str] = None
        self._directory_version = 1
        self._active_library_id: Optional[str] = None
        self._libraries: Dict[str, PromptLibraryItem] = {}
        self._library_seq = 0
        self._category_seq = 0

    def _legacy_id_highwaters(self) -> Tuple[int, int]:
        """读取升级前只落盘条目中的父 ID，仅用作高水位，不猜测恢复父对象。"""
        raw = storage.JsonState(NS_PROMPT_ITEMS, lambda: {"items": {}}).read()
        library_ids, category_ids = [], []
        for item in raw.get("items", {}).values():
            library_ids.append(str(item.get("library_id") or ""))
            category_ids.append(str(item.get("category_id") or ""))
        return _sequence_for("plib_", library_ids), _sequence_for("pcat_", category_ids)

    def _ensure_loaded_locked(self) -> None:
        root = str(storage.data_root().resolve())
        if self._loaded_root == root:
            return

        state_path = storage.data_root() / (NS_PROMPT_TREE + ".json")
        state = storage.JsonState(NS_PROMPT_TREE, _blank_prompt_tree)
        raw = state.read()
        legacy_library_seq, legacy_category_seq = self._legacy_id_highwaters()
        if state_path.exists():
            if not isinstance(raw, dict) or raw.get("schema_version") != 1:
                raise CleanroomException(503, "PROMPT_LIBRARY_STATE_INVALID", "提示词库持久化数据格式不受支持")
            try:
                libraries = {
                    key: PromptLibraryItem.model_validate(value)
                    for key, value in raw.get("libraries", {}).items()
                }
                active_library_id = raw.get("active_library_id")
                if active_library_id is not None and active_library_id not in libraries:
                    raise ValueError("活动提示词库不存在")
                self._directory_version = max(1, int(raw.get("directory_version", 1)))
                self._active_library_id = active_library_id
                self._libraries = libraries
                self._library_seq = max(
                    int(raw.get("library_sequence", 0) or 0),
                    _sequence_for("plib_", list(libraries.keys())),
                    legacy_library_seq,
                )
                categories = [
                    category.category_id
                    for library in libraries.values()
                    for category in library.categories
                ]
                self._category_seq = max(
                    int(raw.get("category_sequence", 0) or 0),
                    _sequence_for("pcat_", categories),
                    legacy_category_seq,
                )
            except Exception as exc:
                raise CleanroomException(503, "PROMPT_LIBRARY_STATE_INVALID", "提示词库持久化数据无法读取") from exc
        else:
            self._directory_version = 1
            self._active_library_id = None
            self._libraries = {}
            self._library_seq = legacy_library_seq
            self._category_seq = legacy_category_seq
            if self._seed_structural_fixture:
                self._seed()
                self._library_seq = max(self._library_seq, legacy_library_seq)
                self._category_seq = max(self._category_seq, legacy_category_seq)

        self._loaded_root = root
        if not state_path.exists() and self._seed_structural_fixture:
            self._persist_locked()

    def _persist_locked(self) -> None:
        payload = {
            "schema_version": 1,
            "directory_version": self._directory_version,
            "active_library_id": self._active_library_id,
            "libraries": {
                library_id: item.model_dump(mode="json")
                for library_id, item in self._libraries.items()
            },
            "library_sequence": self._library_seq,
            "category_sequence": self._category_seq,
        }
        storage.JsonState(NS_PROMPT_TREE, _blank_prompt_tree).write(payload)

    # ------------------------------------------------------------------
    # 查询
    # ------------------------------------------------------------------
    def get_snapshot(self) -> PromptLibrarySnapshot:
        """返回提示词库顶层目录快照（深拷贝，避免调用方改写内部状态）。"""
        with coordination.PROMPT_LIBRARY_DOMAIN_LOCK, self._lock:
            self._ensure_loaded_locked()
            return self._build_snapshot_locked()

    def _build_snapshot_locked(self) -> PromptLibrarySnapshot:
        libraries = [copy.deepcopy(self._libraries[key]) for key in sorted(self._libraries)]
        return PromptLibrarySnapshot(
            version=self._directory_version,
            active_library_id=self._active_library_id,
            libraries=libraries,
        )

    # ------------------------------------------------------------------
    # 提示词库：创建 / 重命名 / 删除
    # ------------------------------------------------------------------
    def create_library(
        self, payload: PromptLibraryCreateRequest
    ) -> Tuple[PromptLibraryItem, PromptLibrarySnapshot]:
        """创建提示词库；目录 CAS 冲突或重名失败关闭。"""
        with coordination.PROMPT_LIBRARY_DOMAIN_LOCK, self._lock:
            self._ensure_loaded_locked()
            _assert_expected_version(payload.expected_version, self._directory_version, "提示词库目录")
            name = _require_name(payload.name, "提示词库名称")
            self._assert_unique_library_name_locked(name)

            self._library_seq += 1
            library_id = f"plib_{self._library_seq:04d}"
            timestamp = _now_iso()
            item = PromptLibraryItem(
                library_id=library_id,
                name=name,
                version=1,
                created_at=timestamp,
                updated_at=timestamp,
                categories=[],
            )
            self._libraries[library_id] = item
            # 空目录创建首个库时自动激活；已有激活库时保持不变
            if self._active_library_id is None:
                self._active_library_id = library_id
            self._directory_version += 1
            self._persist_locked()
            return copy.deepcopy(item), self._build_snapshot_locked()

    def rename_library(
        self, library_id: str, payload: PromptLibraryRenameRequest
    ) -> Tuple[PromptLibraryItem, PromptLibrarySnapshot]:
        """重命名提示词库；目标不存在、重名或 CAS 冲突失败关闭。"""
        with coordination.PROMPT_LIBRARY_DOMAIN_LOCK, self._lock:
            self._ensure_loaded_locked()
            target = self._libraries.get(library_id)
            if target is None:
                raise self._library_not_found(library_id)
            _assert_expected_version(payload.expected_version, target.version, f"提示词库 {library_id}")

            name = _require_name(payload.name, "提示词库名称")
            self._assert_unique_library_name_locked(name, skip_library_id=library_id)

            target.name = name
            target.version += 1
            target.updated_at = _now_iso()
            self._directory_version += 1
            self._persist_locked()
            return copy.deepcopy(target), self._build_snapshot_locked()

    def delete_library(self, library_id: str, expected_version: Optional[int]) -> PromptLibrarySnapshot:
        """删除提示词库；非空库（仍有分类）返回 409 LIBRARY_NOT_EMPTY，禁止静默级联。"""
        with coordination.PROMPT_LIBRARY_DOMAIN_LOCK, self._lock:
            self._ensure_loaded_locked()
            _require_positive_version(expected_version, f"提示词库 {library_id}")
            target = self._libraries.get(library_id)
            if target is None:
                raise self._library_not_found(library_id)
            _assert_expected_version(expected_version, target.version, f"提示词库 {library_id}")

            stored_items = storage.JsonState(NS_PROMPT_ITEMS, lambda: {"items": {}}).read().get("items", {})
            has_items = any(item.get("library_id") == library_id for item in stored_items.values())
            if target.categories or has_items:
                raise CleanroomException(
                    status_code=409,
                    code="LIBRARY_NOT_EMPTY",
                    message=f"提示词库 {library_id} 仍包含分类或条目，请先移除引用后再删除",
                )

            del self._libraries[library_id]
            self._directory_version += 1
            if self._active_library_id == library_id:
                self._active_library_id = next(iter(sorted(self._libraries)), None)
            self._persist_locked()
            return self._build_snapshot_locked()

    # ------------------------------------------------------------------
    # 提示词分类：创建 / 重命名 / 删除
    # ------------------------------------------------------------------
    def create_category(
        self, payload: PromptCategoryCreateRequest
    ) -> Tuple[PromptCategoryItem, PromptLibrarySnapshot]:
        """在指定提示词库下创建分类；父库不存在或父库 CAS 冲突失败关闭。"""
        with coordination.PROMPT_LIBRARY_DOMAIN_LOCK, self._lock:
            self._ensure_loaded_locked()
            parent = self._libraries.get(payload.library_id)
            if parent is None:
                raise self._library_not_found(payload.library_id)
            _assert_expected_version(payload.expected_version, parent.version, f"提示词库 {parent.library_id}")

            name = _require_name(payload.name, "分类名称")
            self._assert_unique_category_name_locked(parent, name)

            self._category_seq += 1
            category_id = f"pcat_{self._category_seq:04d}"
            timestamp = _now_iso()
            category = PromptCategoryItem(
                category_id=category_id,
                library_id=parent.library_id,
                name=name,
                version=1,
                created_at=timestamp,
                updated_at=timestamp,
            )
            parent.categories.append(category)
            parent.version += 1
            parent.updated_at = timestamp
            self._directory_version += 1
            self._persist_locked()
            return copy.deepcopy(category), self._build_snapshot_locked()

    def rename_category(
        self, category_id: str, payload: PromptCategoryRenameRequest
    ) -> Tuple[PromptCategoryItem, PromptLibrarySnapshot]:
        """重命名分类；目标不存在、同库重名或 CAS 冲突失败关闭。"""
        with coordination.PROMPT_LIBRARY_DOMAIN_LOCK, self._lock:
            self._ensure_loaded_locked()
            parent, target = self._locate_category_locked(category_id)
            _assert_expected_version(payload.expected_version, target.version, f"分类 {category_id}")

            name = _require_name(payload.name, "分类名称")
            self._assert_unique_category_name_locked(parent, name, skip_category_id=category_id)

            target.name = name
            target.version += 1
            target.updated_at = _now_iso()
            parent.version += 1
            parent.updated_at = target.updated_at
            self._directory_version += 1
            self._persist_locked()
            return copy.deepcopy(target), self._build_snapshot_locked()

    def delete_category(self, category_id: str, expected_version: Optional[int]) -> PromptLibrarySnapshot:
        """删除分类；目标分类 CAS 冲突失败关闭。"""
        with coordination.PROMPT_LIBRARY_DOMAIN_LOCK, self._lock:
            self._ensure_loaded_locked()
            _require_positive_version(expected_version, f"分类 {category_id}")
            parent, target = self._locate_category_locked(category_id)
            _assert_expected_version(expected_version, target.version, f"分类 {category_id}")

            stored_items = storage.JsonState(NS_PROMPT_ITEMS, lambda: {"items": {}}).read().get("items", {})
            has_items = any(item.get("category_id") == category_id for item in stored_items.values())
            if has_items:
                raise CleanroomException(
                    status_code=409,
                    code="CATEGORY_NOT_EMPTY",
                    message=f"分类 {category_id} 仍被提示词条目引用，请先移除引用后再删除",
                )
            parent.categories = [item for item in parent.categories if item.category_id != category_id]
            parent.version += 1
            parent.updated_at = _now_iso()
            self._directory_version += 1
            self._persist_locked()
            return self._build_snapshot_locked()

    # ------------------------------------------------------------------
    # 内部断言与定位
    # ------------------------------------------------------------------
    def _locate_category_locked(self, category_id: str) -> Tuple[PromptLibraryItem, PromptCategoryItem]:
        for library in self._libraries.values():
            for category in library.categories:
                if category.category_id == category_id:
                    return library, category
        raise CleanroomException(
            status_code=404,
            code="CATEGORY_NOT_FOUND",
            message=f"提示词分类 {category_id} 不存在",
        )

    def _library_not_found(self, library_id: str) -> CleanroomException:
        return CleanroomException(
            status_code=404,
            code="LIBRARY_NOT_FOUND",
            message=f"提示词库 {library_id} 不存在",
        )

    def _assert_unique_library_name_locked(self, name: str, skip_library_id: Optional[str] = None) -> None:
        key = _name_key(name)
        for library_id, item in self._libraries.items():
            if library_id == skip_library_id:
                continue
            if _name_key(item.name) == key:
                raise CleanroomException(
                    status_code=409,
                    code="DUPLICATE_LIBRARY_NAME",
                    message=f"提示词库名称已存在：{name}",
                )

    def _assert_unique_category_name_locked(
        self, parent: PromptLibraryItem, name: str, skip_category_id: Optional[str] = None
    ) -> None:
        key = _name_key(name)
        for item in parent.categories:
            if item.category_id == skip_category_id:
                continue
            if _name_key(item.name) == key:
                raise CleanroomException(
                    status_code=409,
                    code="DUPLICATE_CATEGORY_NAME",
                    message=f"分类名称已存在：{name}",
                )

    # ------------------------------------------------------------------
    # 黄金夹具种子（仅结构：空库 + 空分类，绝不注入提示词文本）
    # ------------------------------------------------------------------
    def _seed(self) -> None:
        """注入结构性基线（docs/fixtures/prompt-library-with-empty-category.json）。

        仅包含库与分类的**空结构**：不含 items，也不含任何 positive/negative/scene
        提示词文本；种子中的稳定 ID 为确定性常量，便于跨用例复现。
        """
        timestamp = "2026-09-22T07:00:00Z"
        category = PromptCategoryItem(
            category_id="pcat_default",
            library_id="plib_default",
            name="通用",
            version=1,
            created_at=timestamp,
            updated_at=timestamp,
        )
        library = PromptLibraryItem(
            library_id="plib_default",
            name="默认提示词库",
            version=2,
            created_at=timestamp,
            updated_at=timestamp,
            categories=[category],
        )
        self._libraries[library.library_id] = library
        self._active_library_id = library.library_id
        self._library_seq = 1
        self._category_seq = 1
        self._directory_version = 2


# 全局单例服务实例：默认空目录，绝不预置任何伪造提示词内容。
default_prompt_library_service = PromptLibraryService(seed_structural_fixture=False)
