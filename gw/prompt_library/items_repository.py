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

"""Phase 12 A3 提示词条目的真实落盘仓库。

命名空间：``prompt_library``（core.storage.JsonState 原子落盘，重启可恢复）。

稳定 ID 口径：复用既有提示词库 ID 家族前缀，条目为 ``pitem_NNNN``
（既有库 ``plib_NNNN``、分类 ``pcat_NNNN``），**不自创第二套 ID 体系**。

持久化边界：库/分类树落盘到 ``prompt_library_tree``，条目仍落盘到 ``prompt_library``；
创建/更新时必须验证真实父库与分类引用。既有孤儿条目不会被删除或自动挂接到新库，
查询会在 ``data_gaps`` 中标记其历史引用缺口。本模块只保存调用方提交的字段，
**不生成**任何提示词文本，不注入演示数据；多 worker 并发写属部署方职责。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from gw.core import storage
from gw.core.errors import CleanroomException, VersionConflictException
from gw.prompt_library import coordination

NS = "prompt_library"
DATA_STATUS_OK = "ok"

_ITEM_TEXT_FIELDS = ("text", "positive", "negative", "scene")


def state() -> storage.JsonState:
    return storage.JsonState(NS, _blank)


def _blank() -> Dict[str, Any]:
    return {"revision": 1, "items": {}}


def _bump(raw: Dict[str, Any]) -> int:
    raw["revision"] = int(raw.get("revision") or 1) + 1
    return raw["revision"]


def _cas(raw: Dict[str, Any], expected: Optional[int], label: str) -> None:
    if expected is None:
        return
    if int(expected) != int(raw.get("revision") or 1):
        raise VersionConflictException(
            expected_version=int(expected),
            current_version=int(raw.get("revision") or 1),
            message="%s版本冲突，请重新读取后重试" % label,
        )


def _view(item: Dict[str, Any]) -> Dict[str, Any]:
    """对外条目视图：只回显真实存储字段，不补默认文本。"""
    view = {
        "item_id": item["item_id"],
        "library_id": item.get("library_id") or "",
        "category_id": item.get("category_id"),
        "name": item.get("name") or "",
        "tags": list(item.get("tags") or []),
        "version": item.get("version"),
        "created_at": item.get("created_at"),
        "updated_at": item.get("updated_at"),
    }
    for field in _ITEM_TEXT_FIELDS:
        if field in item:
            view[field] = item[field]
    return view


TREE_NS = "prompt_library_tree"


def _tree_state() -> Dict[str, Any]:
    return storage.JsonState(TREE_NS, lambda: {"libraries": {}}).read()


def _validate_parent_refs(library_id: str, category_id: Optional[str] = None) -> None:
    """条目只能引用真实父库；分类必须真实存在并属于该父库。"""
    libraries = _tree_state().get("libraries", {})
    library = libraries.get(library_id)
    if library is None:
        raise CleanroomException(404, "LIBRARY_NOT_FOUND", "提示词库不存在")
    if category_id:
        categories = library.get("categories", [])
        if not any(category.get("category_id") == category_id for category in categories):
            raise CleanroomException(404, "CATEGORY_NOT_FOUND", "提示词分类不存在或不属于该提示词库")


def _reference_gaps(items: List[Dict[str, Any]]) -> List[Dict[str, str]]:
    libraries = _tree_state().get("libraries", {})
    gaps: List[Dict[str, str]] = []
    for item in items:
        library_id = str(item.get("library_id") or "")
        library = libraries.get(library_id)
        if library is None:
            gaps.append({"item_id": str(item.get("item_id") or ""), "code": "LIBRARY_NOT_FOUND"})
            continue
        category_id = item.get("category_id")
        if category_id and not any(
            category.get("category_id") == category_id for category in library.get("categories", [])
        ):
            gaps.append({"item_id": str(item.get("item_id") or ""), "code": "CATEGORY_NOT_FOUND"})
    return gaps


def list_items(library_id: Optional[str] = None) -> Dict[str, Any]:
    with coordination.PROMPT_LIBRARY_DOMAIN_LOCK:
        raw = state().read()
        items = [dict(v) for v in raw["items"].values()]
        if library_id:
            items = [item for item in items if item.get("library_id") == library_id]
        items.sort(key=lambda item: item["item_id"])
        return {
            "prompt_items": [_view(item) for item in items],
            "items": [_view(item) for item in items],
            "revision": raw["revision"],
            "data_status": DATA_STATUS_OK,
            "data_gaps": _reference_gaps(items),
        }


def create_item(payload: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(payload, dict):
        raise CleanroomException(400, "INVALID_REQUEST", "请求体必须是对象")
    name = str(payload.get("name") or "").strip()
    if not name:
        raise CleanroomException(400, "INVALID_REQUEST", "条目名称不能为空")
    library_id = str(payload.get("library_id") or "").strip()
    if not library_id:
        raise CleanroomException(400, "INVALID_REQUEST", "library_id 不能为空")
    category_id = str(payload.get("category_id") or "").strip() or None
    def mutate(raw: Dict[str, Any]) -> Dict[str, Any]:
        expected = payload.get("expected_version")
        _cas(raw, (int(expected) if expected is not None else None), "提示词条目集合")
        item_id = storage.next_sequence("pitem_", list(raw["items"].keys()))
        now = storage.now_iso()
        record = {
            "item_id": item_id,
            "library_id": library_id,
            "category_id": category_id,
            "name": name,
            "tags": [str(tag) for tag in (payload.get("tags") or [])],
            "version": 1,
            "created_at": now,
            "updated_at": now,
        }
        for field in _ITEM_TEXT_FIELDS:
            if payload.get(field) is not None:
                record[field] = str(payload.get(field))
        raw["items"][item_id] = record
        _bump(raw)
        return _view(record)

    with coordination.PROMPT_LIBRARY_DOMAIN_LOCK:
        _validate_parent_refs(library_id, category_id)
        item = state().mutate(mutate)
    return {"prompt_item": item, "item": item}


def update_item(item_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(payload, dict):
        raise CleanroomException(400, "INVALID_REQUEST", "请求体必须是对象")

    def mutate(raw: Dict[str, Any]) -> Dict[str, Any]:
        record = raw["items"].get(item_id)
        if record is None:
            raise CleanroomException(404, "ITEM_NOT_FOUND", "提示词条目不存在")
        expected = payload.get("expected_version")
        if expected is not None and int(expected) != int(record.get("version") or 1):
            raise VersionConflictException(int(expected), int(record.get("version") or 1), "提示词条目版本冲突，请重新读取后重试")
        if payload.get("name") is not None:
            text = str(payload.get("name")).strip()
            if not text:
                raise CleanroomException(400, "INVALID_REQUEST", "条目名称不能为空")
            record["name"] = text
        if payload.get("category_id") is not None:
            category_id = str(payload.get("category_id") or "").strip() or None
            _validate_parent_refs(str(record.get("library_id") or ""), category_id)
            record["category_id"] = category_id
        else:
            _validate_parent_refs(
                str(record.get("library_id") or ""),
                str(record.get("category_id") or "") or None,
            )
        if payload.get("tags") is not None:
            record["tags"] = [str(tag) for tag in (payload.get("tags") or [])]
        for field in _ITEM_TEXT_FIELDS:
            if payload.get(field) is not None:
                record[field] = str(payload.get(field))
        record["version"] = int(record.get("version") or 1) + 1
        record["updated_at"] = storage.now_iso()
        _bump(raw)
        return _view(record)

    with coordination.PROMPT_LIBRARY_DOMAIN_LOCK:
        item = state().mutate(mutate)
    return {"prompt_item": item, "item": item}


def delete_item(item_id: str, expected_version: Optional[int]) -> Dict[str, Any]:
    def mutate(raw: Dict[str, Any]) -> Dict[str, Any]:
        record = raw["items"].get(item_id)
        if record is None:
            raise CleanroomException(404, "ITEM_NOT_FOUND", "提示词条目不存在")
        _cas(raw, expected_version, "提示词条目集合")
        del raw["items"][item_id]
        _bump(raw)
        return {"deleted": item_id, "item_id": item_id, "revision": raw["revision"]}

    with coordination.PROMPT_LIBRARY_DOMAIN_LOCK:
        return state().mutate(mutate)


def bulk_delete(ids: List[str], expected_version: Optional[int]) -> Dict[str, Any]:
    if not isinstance(ids, list) or not ids:
        raise CleanroomException(400, "INVALID_REQUEST", "ids 不能为空")

    def mutate(raw: Dict[str, Any]) -> Dict[str, Any]:
        _cas(raw, expected_version, "提示词条目集合")
        deleted: List[str] = []
        for item_id in ids:
            if str(item_id) in raw["items"]:
                del raw["items"][str(item_id)]
                deleted.append(str(item_id))
        _bump(raw)
        return {"deleted": deleted, "deleted_count": len(deleted), "revision": raw["revision"]}

    with coordination.PROMPT_LIBRARY_DOMAIN_LOCK:
        return state().mutate(mutate)
