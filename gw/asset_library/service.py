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

"""素材库服务层实现（Phase 10A 冻结契约 + Phase 12 真实落盘）。

严格遵循 docs/contracts/ASSET-LIBRARY-INTERFACE-CATALOG.yaml：

- 稳定 ID：library_id / category_id / asset_id；
- 库级 CAS：创建分类校验父库 expected_version，创建素材库校验顶层目录版本；
- 重名（trim 后大小写不敏感）返回 409 DUPLICATE_LIBRARY_NAME / DUPLICATE_CATEGORY_NAME；
- 空库返回 libraries: [] 且 active_library_id: null，绝不返回演示或伪造素材；
- 成功后：新对象 version=1，父库 version += 1，顶层目录 version += 1。

Phase 12 变更：默认模式把整棵素材库树落到单实例 JSON（重启可恢复）；
`seed_golden_fixture=True` 仍走内存隔离，供用例互不干扰。两模式**共用同一套业务逻辑**。

证据边界：单实例；多 worker 并发写属部署方职责，未闭环。
"""

from __future__ import annotations

import copy
from datetime import datetime, timezone
import threading
from typing import Any, Dict, List, Optional, Tuple

from gw.asset_library.models import (
    AssetItem,
    AssetLibraryCreateRequest,
    AssetLibrarySnapshot,
    CategoryCreateRequest,
    CategoryItem,
    LibraryItem,
)
from gw.core import storage
from gw.core.errors import CleanroomException, VersionConflictException

NS_LIBRARY = "asset_library"


def _now_iso() -> str:
    """生成标准 ISO 8601 UTC 时间戳字符串。"""
    return datetime.now(timezone.utc).isoformat()


def _name_key(value: str) -> str:
    """重名比较键：trim 后大小写不敏感。"""
    return (value or "").strip().casefold()


def _default_raw() -> Dict[str, Any]:
    return {"directory_version": 1, "active_library_id": None, "libraries": {},
            "sequences": {"library": 0, "category": 0, "asset": 0}}


def _seeded_raw() -> Dict[str, Any]:
    timestamp = "2026-09-22T06:00:00Z"
    category = {
        "category_id": "category_image", "library_id": "library_default", "name": "图片",
        "type": "image", "version": 1, "created_at": timestamp, "updated_at": timestamp, "items": [],
    }
    library = {
        "library_id": "library_default", "name": "默认资产库", "version": 2,
        "created_at": timestamp, "updated_at": timestamp, "categories": [category],
    }
    return {"directory_version": 2, "active_library_id": "library_default",
            "libraries": {"library_default": library},
            "sequences": {"library": 1, "category": 1, "asset": 0}}


class AssetLibraryService:
    """素材库存储与 CAS 状态服务。默认落盘；夹具模式为内存隔离，业务逻辑共用。"""

    def __init__(self, seed_golden_fixture: bool = False) -> None:
        self._seed_fixture = bool(seed_golden_fixture)
        self._lock = threading.Lock()
        self._memory: Dict[str, Any] | None = _seeded_raw() if seed_golden_fixture else None

    # ------------------------------------------------------------------
    # 状态读写
    # ------------------------------------------------------------------
    def _read_raw(self) -> Dict[str, Any]:
        if self._seed_fixture:
            return copy.deepcopy(self._memory)
        return storage.JsonState(NS_LIBRARY, _default_raw).read()

    def _write_raw(self, raw: Dict[str, Any]) -> None:
        if self._seed_fixture:
            self._memory = copy.deepcopy(raw)
        else:
            storage.JsonState(NS_LIBRARY, _default_raw).write(raw)

    def _mutate(self, mutator) -> Any:
        """在锁内完成读-改-写；写入前不落盘，异常时不留下半成品。"""
        with self._lock:
            raw = self._read_raw()
            result = mutator(raw)
            self._write_raw(raw)
            return result

    # ------------------------------------------------------------------
    # 查询
    # ------------------------------------------------------------------
    def get_snapshot(self) -> AssetLibrarySnapshot:
        with self._lock:
            raw = self._read_raw()
            return self._snapshot(raw)

    def _snapshot(self, raw: Dict[str, Any]) -> AssetLibrarySnapshot:
        libraries = [LibraryItem.model_validate(raw["libraries"][key])
                     for key in sorted(raw.get("libraries", {}))]
        return AssetLibrarySnapshot(
            version=int(raw.get("directory_version", 1)),
            active_library_id=raw.get("active_library_id"),
            libraries=libraries,
        )

    # ------------------------------------------------------------------
    # 内部定位与断言
    # ------------------------------------------------------------------
    @staticmethod
    def _library(raw: Dict[str, Any], library_id: str) -> Dict[str, Any]:
        library = raw.get("libraries", {}).get(library_id)
        if library is None:
            raise CleanroomException(404, "LIBRARY_NOT_FOUND", "素材库 %s 不存在" % library_id)
        return library

    @staticmethod
    def _category(raw: Dict[str, Any], category_id: str) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        for library in raw.get("libraries", {}).values():
            for category in library.get("categories", []):
                if category.get("category_id") == category_id:
                    return library, category
        raise CleanroomException(404, "CATEGORY_NOT_FOUND", "分类 %s 不存在" % category_id)

    @staticmethod
    def _asset(raw: Dict[str, Any], asset_id: str) -> Tuple[Dict[str, Any], Dict[str, Any], Dict[str, Any]]:
        for library in raw.get("libraries", {}).values():
            for category in library.get("categories", []):
                for item in category.get("items", []):
                    if item.get("asset_id") == asset_id:
                        return library, category, item
        raise CleanroomException(404, "ASSET_NOT_FOUND", "素材 %s 不存在" % asset_id)

    @staticmethod
    def _check_version(expected: Optional[int], current: int) -> None:
        if expected is not None and int(expected) != int(current):
            raise VersionConflictException(
                expected_version=int(expected), current_version=int(current),
                message="版本冲突，请重新读取后重试",
            )

    @staticmethod
    def _next_seq(raw: Dict[str, Any], key: str) -> int:
        sequences = raw.setdefault("sequences", {})
        sequences[key] = int(sequences.get(key, 0)) + 1
        return sequences[key]

    @staticmethod
    def _touch(raw: Dict[str, Any]) -> None:
        raw["directory_version"] = int(raw.get("directory_version", 1)) + 1

    # ------------------------------------------------------------------
    # 创建
    # ------------------------------------------------------------------
    def create_library(self, payload: AssetLibraryCreateRequest) -> Tuple[LibraryItem, AssetLibrarySnapshot]:
        """创建素材库；重名或 CAS 冲突失败关闭。"""
        def mutator(raw: Dict[str, Any]) -> Any:
            self._check_version(payload.expected_version, int(raw.get("directory_version", 1)))
            name = payload.name.strip()
            if not name:
                raise CleanroomException(400, "INVALID_REQUEST", "素材库名称不能为空或全空白")
            for other in raw["libraries"].values():
                if _name_key(other["name"]) == _name_key(name):
                    raise CleanroomException(409, "DUPLICATE_LIBRARY_NAME", "素材库名称已存在：%s" % name)
            library_id = "library_%04d" % self._next_seq(raw, "library")
            stamp = _now_iso()
            raw["libraries"][library_id] = {
                "library_id": library_id, "name": name, "version": 1,
                "created_at": stamp, "updated_at": stamp, "categories": [],
            }
            if raw.get("active_library_id") is None:
                raw["active_library_id"] = library_id
            self._touch(raw)
            return raw["libraries"][library_id]
        item = self._mutate(mutator)
        return LibraryItem.model_validate(item), self.get_snapshot()

    def create_category(self, payload: CategoryCreateRequest) -> Tuple[CategoryItem, LibraryItem]:
        """在指定素材库下创建分类；父库不存在或版本冲突失败关闭。"""
        def mutator(raw: Dict[str, Any]) -> Any:
            parent = self._library(raw, payload.library_id)
            self._check_version(payload.expected_version, parent["version"])
            name = payload.name.strip()
            if not name:
                raise CleanroomException(400, "INVALID_REQUEST", "分类名称不能为空或全空白")
            for other in parent["categories"]:
                if _name_key(other["name"]) == _name_key(name):
                    raise CleanroomException(409, "DUPLICATE_CATEGORY_NAME", "分类名称已存在：%s" % name)
            category_id = "category_%04d" % self._next_seq(raw, "category")
            stamp = _now_iso()
            kind = payload.type.value if hasattr(payload.type, "value") else str(payload.type)
            category = {
                "category_id": category_id, "library_id": parent["library_id"], "name": name,
                "type": kind, "version": 1, "created_at": stamp, "updated_at": stamp, "items": [],
            }
            parent["categories"].append(category)
            parent["version"] += 1
            parent["updated_at"] = stamp
            self._touch(raw)
            return {"category": category, "library": parent}
        result = self._mutate(mutator)
        return CategoryItem.model_validate(result["category"]), LibraryItem.model_validate(result["library"])

    # ------------------------------------------------------------------
    # 素材库 / 分类 变更
    # ------------------------------------------------------------------
    def rename_library(self, library_id: str, name: str, expected_version: Optional[int]) -> LibraryItem:
        def mutator(raw: Dict[str, Any]) -> Any:
            library = self._library(raw, library_id)
            self._check_version(expected_version, library["version"])
            clean = (name or "").strip()
            if not clean:
                raise CleanroomException(400, "INVALID_REQUEST", "素材库名称不能为空")
            for other in raw["libraries"].values():
                if other["library_id"] != library_id and _name_key(other["name"]) == _name_key(clean):
                    raise CleanroomException(409, "DUPLICATE_LIBRARY_NAME", "素材库名称已存在")
            library["name"] = clean
            library["version"] += 1
            library["updated_at"] = _now_iso()
            self._touch(raw)
            return library
        return LibraryItem.model_validate(self._mutate(mutator))

    def delete_library(self, library_id: str, expected_version: Optional[int]) -> Dict[str, Any]:
        def mutator(raw: Dict[str, Any]) -> Any:
            library = self._library(raw, library_id)
            self._check_version(expected_version, library["version"])
            del raw["libraries"][library_id]
            if raw.get("active_library_id") == library_id:
                raw["active_library_id"] = sorted(raw["libraries"])[0] if raw["libraries"] else None
            self._touch(raw)
            return {"library_id": library_id, "deleted": True}
        return self._mutate(mutator)

    def rename_category(self, category_id: str, name: str, expected_version: Optional[int]) -> CategoryItem:
        def mutator(raw: Dict[str, Any]) -> Any:
            library, category = self._category(raw, category_id)
            self._check_version(expected_version, category["version"])
            clean = (name or "").strip()
            if not clean:
                raise CleanroomException(400, "INVALID_REQUEST", "分类名称不能为空")
            for other in library["categories"]:
                if other["category_id"] != category_id and _name_key(other["name"]) == _name_key(clean):
                    raise CleanroomException(409, "DUPLICATE_CATEGORY_NAME", "分类名称已存在")
            category["name"] = clean
            category["version"] += 1
            category["updated_at"] = _now_iso()
            library["version"] += 1
            self._touch(raw)
            return category
        return CategoryItem.model_validate(self._mutate(mutator))

    def delete_category(self, category_id: str, expected_version: Optional[int]) -> Dict[str, Any]:
        def mutator(raw: Dict[str, Any]) -> Any:
            library, category = self._category(raw, category_id)
            self._check_version(expected_version, category["version"])
            library["categories"] = [c for c in library["categories"] if c["category_id"] != category_id]
            library["version"] += 1
            self._touch(raw)
            return {"category_id": category_id, "deleted": True}
        return self._mutate(mutator)

    # ------------------------------------------------------------------
    # 素材条目
    # ------------------------------------------------------------------
    def create_items(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """批量创建素材条目，返回真实稳定 asset_id。"""
        def mutator(raw: Dict[str, Any]) -> Any:
            library = self._library(raw, str(payload.get("library_id") or ""))
            self._check_version(payload.get("expected_version"), library["version"])
            category_id = str(payload.get("category_id") or "")
            category = None
            for candidate in library["categories"]:
                if candidate["category_id"] == category_id:
                    category = candidate
                    break
            if category is None:
                raise CleanroomException(404, "CATEGORY_NOT_FOUND", "分类不存在")
            names = payload.get("names") or []
            if not isinstance(names, list) or not names:
                raise CleanroomException(400, "INVALID_REQUEST", "names 必须是非空数组")
            stamp = _now_iso()
            created = []
            for entry in names:
                name = str(entry).strip()
                if not name:
                    raise CleanroomException(400, "INVALID_REQUEST", "素材名称不能为空")
                asset_id = "asset_%04d" % self._next_seq(raw, "asset")
                item = AssetItem(
                    asset_id=asset_id, library_id=library["library_id"], category_id=category_id,
                    name=name, url=None, created_at=stamp,
                ).model_dump()
                category.setdefault("items", []).append(item)
                created.append(item)
            category["version"] += 1
            library["version"] += 1
            self._touch(raw)
            return {"items": created, "asset_ids": [item["asset_id"] for item in created]}
        return self._mutate(mutator)

    def update_item(self, asset_id: str, payload: Dict[str, Any]) -> AssetItem:
        def mutator(raw: Dict[str, Any]) -> Any:
            library, category, item = self._asset(raw, asset_id)
            self._check_version(payload.get("expected_version"), library["version"])
            if payload.get("name"):
                item["name"] = str(payload["name"]).strip()
            target_id = str(payload.get("category_id") or "")
            if target_id and target_id != category["category_id"]:
                target = None
                for candidate in library["categories"]:
                    if candidate["category_id"] == target_id:
                        target = candidate
                        break
                if target is None:
                    raise CleanroomException(404, "CATEGORY_NOT_FOUND", "目标分类不存在")
                category["items"] = [i for i in category["items"] if i["asset_id"] != asset_id]
                target.setdefault("items", []).append(item)
                item["category_id"] = target_id
                target["version"] += 1
            category["version"] += 1
            library["version"] += 1
            self._touch(raw)
            return item
        return AssetItem.model_validate(self._mutate(mutator))

    def delete_item(self, asset_id: str, expected_version: Optional[int] = None) -> Dict[str, Any]:
        def mutator(raw: Dict[str, Any]) -> Any:
            library, category, _ = self._asset(raw, asset_id)
            self._check_version(expected_version, library["version"])
            category["items"] = [i for i in category["items"] if i["asset_id"] != asset_id]
            category["version"] += 1
            library["version"] += 1
            self._touch(raw)
            return {"asset_id": asset_id, "deleted": True}
        return self._mutate(mutator)

    def delete_items(self, asset_ids: List[str]) -> Dict[str, Any]:
        def mutator(raw: Dict[str, Any]) -> Any:
            removed = []
            for asset_id in asset_ids:
                try:
                    library, category, _ = self._asset(raw, asset_id)
                except CleanroomException:
                    continue
                category["items"] = [i for i in category["items"] if i["asset_id"] != asset_id]
                category["version"] += 1
                library["version"] += 1
                removed.append(asset_id)
            if removed:
                self._touch(raw)
            return {"deleted": removed, "count": len(removed)}
        return self._mutate(mutator)

    def move_items(self, asset_ids: List[str], category_id: str) -> Dict[str, Any]:
        def mutator(raw: Dict[str, Any]) -> Any:
            moved = []
            for asset_id in asset_ids:
                try:
                    library, category, item = self._asset(raw, asset_id)
                except CleanroomException:
                    continue
                target = None
                for candidate in library["categories"]:
                    if candidate["category_id"] == category_id:
                        target = candidate
                        break
                if target is None:
                    raise CleanroomException(404, "CATEGORY_NOT_FOUND", "目标分类不存在")
                category["items"] = [i for i in category["items"] if i["asset_id"] != asset_id]
                target.setdefault("items", []).append(item)
                item["category_id"] = category_id
                target["version"] += 1
                library["version"] += 1
                moved.append(asset_id)
            if moved:
                self._touch(raw)
            return {"moved": moved, "count": len(moved)}
        return self._mutate(mutator)

    def classify_items(self, asset_ids: List[str]) -> Dict[str, Any]:
        """对真实条目做确定性扩展名归类并写回。"""
        from gw.asset_library.repository import classify_name

        def mutator(raw: Dict[str, Any]) -> Any:
            results = []
            for asset_id in asset_ids:
                try:
                    library, category, item = self._asset(raw, asset_id)
                except CleanroomException:
                    continue
                label = classify_name(str(item.get("name") or ""))
                item["classification"] = label
                category["version"] += 1
                library["version"] += 1
                results.append({"asset_id": asset_id, "classification": label})
            if results:
                self._touch(raw)
            return {"results": results, "count": len(results)}
        return self._mutate(mutator)


# 全局单例服务实例
default_asset_library_service = AssetLibraryService(seed_golden_fixture=False)
