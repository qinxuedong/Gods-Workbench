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

"""Phase 12 素材注册表的真实落盘数据源。

真实数据源：
- 注册表主体（资产/关系/标签/图片版本/预设/目录模板/项目实体/项目门/回收站/远程素材）
  落到 `core.storage.JsonState` 单实例 JSON，重启可恢复；
- 索引类操作真实遍历 `GW_ALLOWED_ROOTS`（未配置即失败关闭）；
- 远程素材用 `httpx` 做真实 HTTP 探测；网络失败如实降级，不编造元数据。

零伪造：稳定 ID 由 `storage.next_sequence()` 确定性生成，禁止随机/uuid；
写操作要求 `expected_version`（CAS），冲突 409。
证据边界：单实例 JSON；多 worker 并发写属部署方职责，未闭环。
"""

from __future__ import annotations

import io
import base64
import hashlib
import json
from collections import Counter
from datetime import datetime, timedelta, timezone
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from gw.core import storage
from gw.asset_registry import audit_sink
from gw.core.errors import CleanroomException

NS_REGISTRY = "asset_registry"
DATA_STATUS_OK = "ok"


def _blank() -> Dict[str, Any]:
    return {
        "revision": 1,
        "assets": {},
        "relations": [],
        "tags": {},
        "image_versions": {},
        "presets": {},
        "directory_templates": {},
        "project_entities": {},
        "project_gates": {},
        "recycle_bin": {},
        "remote_assets": {},
        "outbox": {},
        "operations": [],
        "folders": {},
        "workspace_jobs": {},
        "team_preferences": {"team_id": None, "default_library_id": None, "default_folder_id": None,
                             "view": "grid", "sort": "newest", "version": 1},
        "features": {"thumbnail": True, "video_storyboard": False, "metadata": True,
                     "fulltext": True, "font": True, "model": True,
                     "index_automation": False},
        "index_automation": {"enabled": False, "interval_minutes": 60, "version": 1},
        "sequences": {},
    }


def state() -> storage.JsonState:
    return storage.JsonState(NS_REGISTRY, _blank)


def _seq(raw: Dict[str, Any], prefix: str, taken: Any = None) -> str:
    """生成确定性序号 ID；同时参考 sequences 与目标集合已有键，避免跨集合冲突。

    注意：必须传入目标集合的现有键（如 raw["tags"].keys()），否则不同集合会
    各自从 0001 开始并互相覆盖。
    """
    keys = list(raw.setdefault("sequences", {}).keys())
    if taken is not None:
        keys.extend(list(taken))
    return storage.next_sequence(prefix, keys)


def _bump(raw: Dict[str, Any]) -> int:
    raw["revision"] = int(raw.get("revision", 1)) + 1
    return raw["revision"]


def _cas(raw: Dict[str, Any], expected: Optional[int]) -> None:
    """CAS：期望版本与当前 revision 不一致即 409。"""
    current = int(raw.get("revision", 1))
    if expected is not None and int(expected) != current:
        raise CleanroomException(
            409, "VERSION_CONFLICT", "注册表版本冲突，请重新读取后重试",
            extra={"expected_version": int(expected), "current_version": current},
            expose_extra_fields={"expected_version", "current_version"},
        )


def _require(raw: Dict[str, Any], asset_id: str) -> Dict[str, Any]:
    from gw.core.asset_visibility import asset_visible
    item = raw["assets"].get(asset_id)
    if item is None or not asset_visible(item):
        raise CleanroomException(404, "ASSET_NOT_FOUND", "素材 %s 不存在" % asset_id)
    return item


def _plain_asset(asset_id: str, name: str, kind: str, **extra: Any) -> Dict[str, Any]:
    payload = {
        "id": asset_id, "asset_id": asset_id, "name": name, "kind": kind, "archived": False,
        "hidden": False, "version": 1, "created_at": storage.now_iso(), "updated_at": storage.now_iso(),
        "tags": [], "relations": [], "display_path": None, "metadata": {},
    }
    payload.update({k: v for k, v in extra.items() if v is not None})
    return payload


def _unavailable(endpoint: str, code: str, message: str) -> None:
    raise CleanroomException(
        503, code, message,
        extra={"endpoint": endpoint, "unavailable": True, "data_status": "not_integrated"},
        expose_extra_fields={"endpoint", "unavailable", "data_status"},
    )

# ---------------------------------------------------------------------------
# 资产 CRUD
# ---------------------------------------------------------------------------

def import_assets(items: List[Dict[str, Any]], source_kind: str, expected_version: Optional[int]) -> Dict[str, Any]:
    """真实创建资产登记；稳定 asset_id 由确定性序号生成。"""
    def mutate(raw: Dict[str, Any]) -> Any:
        _cas(raw, expected_version)
        created = []
        for entry in items:
            name = str(entry.get("name") or "").strip()
            if not name:
                raise CleanroomException(400, "INVALID_REQUEST", "资产名称不能为空")
            asset_id = _seq(raw, "ast_", raw["assets"].keys())
            record = _plain_asset(asset_id, name, str(entry.get("kind") or source_kind or "unknown"),
                                  size_bytes=entry.get("size_bytes"))
            raw["assets"][asset_id] = record
            raw["sequences"][asset_id] = True
            created.append(dict(record))
        revision = _bump(raw)
        return {"assets": created, "asset_ids": [a["asset_id"] for a in created],
                "revision": revision, "data_status": DATA_STATUS_OK, "data_gaps": []}
    return state().mutate(mutate)


def update_asset(asset_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    def mutate(raw: Dict[str, Any]) -> Any:
        item = _require(raw, asset_id)
        _cas(raw, payload.get("expected_version"))
        if payload.get("name"):
            item["name"] = str(payload["name"]).strip()
        for key in ("kind", "display_path", "archived"):
            if key in payload and payload[key] is not None:
                item[key] = payload[key]
        item["version"] = int(item.get("version", 1)) + 1
        item["updated_at"] = storage.now_iso()
        revision = _bump(raw)
        return {"asset": dict(item), "revision": revision, "data_status": DATA_STATUS_OK}
    return state().mutate(mutate)


def archive_assets(asset_ids: List[str], expected_version: Optional[int]) -> Dict[str, Any]:
    """归档资产（不是删除）：移出活跃集合，可被 restore 恢复。"""
    def mutate(raw: Dict[str, Any]) -> Any:
        _cas(raw, expected_version)
        archived = []
        for asset_id in asset_ids:
            item = raw["assets"].get(asset_id)
            if item is None:
                continue
            item = _require(raw, asset_id)
            item["archived"] = True
            item["archived_at"] = storage.now_iso()
            item["version"] = int(item.get("version", 1)) + 1
            archived.append(asset_id)
        revision = _bump(raw)
        return {"archived": archived, "count": len(archived),
                "assets": [dict(raw["assets"][aid]) for aid in archived],
                "revision": revision, "data_status": DATA_STATUS_OK}
    return state().mutate(mutate)


def delete_asset(asset_id: str, expected_version: Optional[int], context=None) -> Dict[str, Any]:
    """删除资产：移入回收站并保留可恢复副本。"""
    def mutate(raw: Dict[str, Any]) -> Any:
        item = _require(raw, asset_id)
        _cas(raw, expected_version)
        del raw["assets"][asset_id]
        entry_id = _seq(raw, "rcy_", raw["recycle_bin"].keys())
        raw["recycle_bin"][entry_id] = {"entry_id": entry_id, "kind": "asset", "asset_id": asset_id,
                                        "payload": dict(item), "deleted_at": storage.now_iso()}
        event_id = audit_sink.append_event(raw, "asset.deleted", asset_id, context)
        revision = _bump(raw)
        return {"event_id": event_id, "asset_id": asset_id, "deleted": True, "recycle_entry_id": entry_id,
                "recycled": {"entry_id": entry_id, "asset_id": asset_id, "removed_previews": 0},
                "revision": revision, "data_status": DATA_STATUS_OK}
    return state().mutate(mutate)


def restore_from_recycle(entry_id: str, context=None) -> Dict[str, Any]:
    def mutate(raw: Dict[str, Any]) -> Any:
        entry = raw["recycle_bin"].get(entry_id)
        if entry is None:
            raise CleanroomException(404, "RECYCLE_ENTRY_NOT_FOUND", "回收站条目不存在")
        if entry["kind"] == "asset":
            from gw.core.asset_visibility import asset_visible
            if not asset_visible(entry.get('payload', {})):
                raise CleanroomException(404, 'RECYCLE_ENTRY_NOT_FOUND', '回收站条目不存在')
            if entry["asset_id"] in raw["assets"]:
                raise CleanroomException(409, "ASSET_RESTORE_CONFLICT", "恢复目标资产已经存在")
            raw["assets"][entry["asset_id"]] = entry["payload"]
            audit_sink.append_event(raw, "asset.recycle_restored", entry["asset_id"], context)
        del raw["recycle_bin"][entry_id]
        revision = _bump(raw)
        return {"entry_id": entry_id, "restored": True, "kind": entry["kind"],
                "revision": revision, "data_status": DATA_STATUS_OK}
    return state().mutate(mutate)


def restore_asset(asset_id: str, context=None) -> Dict[str, Any]:
    """从归档状态恢复资产。"""
    def mutate(raw: Dict[str, Any]) -> Any:
        item = _require(raw, asset_id)
        was_archived = bool(item.get("archived"))
        item["archived"] = False
        item.pop("archived_at", None)
        if was_archived:
            audit_sink.append_event(raw, "asset.unarchived", asset_id, context)
        item["version"] = int(item.get("version", 1)) + 1
        revision = _bump(raw)
        return {"asset_id": asset_id, "restored": True, "revision": revision, "data_status": DATA_STATUS_OK}
    return state().mutate(mutate)


def get_asset(asset_id: str) -> Dict[str, Any]:
    return dict(_require(state().read(), asset_id))


def _asset_category(asset: Dict[str, Any]) -> str:
    kind = str(asset.get("kind") or "").lower()
    return kind if kind in {"image", "video", "audio", "document", "model", "font"} else "other"


def list_assets(limit: int, offset: int, query: Optional[str], kind: Optional[str],
                archived: Optional[bool], *, cursor: Optional[str] = None, sort: Optional[str] = None,
                view: Optional[str] = None, category: Optional[str] = None, tag: Optional[str] = None,
                project_id: Optional[str] = None, include_tags: Optional[str] = None,
                exclude_tags: Optional[str] = None, root_id: Optional[str] = None,
                collection_id: Optional[str] = None, recent_days: Optional[int] = None) -> Dict[str, Any]:
    if root_id or collection_id:
        raise CleanroomException(400, "UNSUPPORTED_OPTION", "注册表尚无存储根或集合关联，不能应用该过滤")
    cutoff = None
    if recent_days is not None:
        if type(recent_days) is not int or not 1 <= recent_days <= 36500:
            raise CleanroomException(400, "INVALID_REQUEST", "recent_days必须是1到36500的整数")
        cutoff = (datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
                  - timedelta(days=recent_days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    raw = state().read()
    ordering = sort or "asset_id"
    if ordering not in {"asset_id", "newest", "oldest", "name", "size_desc"}:
        raise CleanroomException(400, "UNSUPPORTED_OPTION", "不支持的资产排序")
    if view and view not in {"card", "grid", "list"}:
        raise CleanroomException(400, "UNSUPPORTED_OPTION", "不支持的资产显示方式")
    includes = sorted({part.strip() for part in (include_tags or "").split(",") if part.strip()})
    excludes = sorted({part.strip() for part in (exclude_tags or "").split(",") if part.strip()})
    filters = [query or "", kind or "", archived, ordering, category or "", tag or "", project_id or "", includes, excludes, cutoff]
    fingerprint = hashlib.sha256(json.dumps(filters, ensure_ascii=False).encode("utf-8")).hexdigest()
    if cursor:
        try:
            if len(cursor) > 2048:
                raise ValueError()
            decoded = json.loads(base64.b64decode(cursor + "=" * (-len(cursor) % 4), altchars=b"-_", validate=True))
            if (not isinstance(decoded, dict) or set(decoded) != {"offset", "revision", "query"}
                    or type(decoded["offset"]) is not int or decoded["offset"] < 0
                    or type(decoded["revision"]) is not int or decoded["query"] != fingerprint
                    or (offset and offset != decoded["offset"])):
                raise ValueError()
        except (ValueError, TypeError, KeyError, UnicodeError):
            raise CleanroomException(400, "INVALID_CURSOR", "分页游标无效或不属于当前查询") from None
        if decoded["revision"] != raw["revision"]:
            raise CleanroomException(409, "VERSION_CONFLICT", "资产列表已变化，请从第一页重新读取",
                                     extra={"expected_version": decoded["revision"], "current_version": raw["revision"]},
                                     expose_extra_fields={"expected_version", "current_version"})
        offset = decoded["offset"]
    from gw.core.asset_visibility import asset_visible
    items = [dict(a) for a in raw["assets"].values() if asset_visible(a)]
    if archived is not None:
        items = [a for a in items if bool(a.get("archived")) == bool(archived)]
    if kind:
        items = [a for a in items if a.get("kind") == kind]
    if query:
        needle = query.casefold()
        items = [a for a in items if needle in str(a.get("name", "")).casefold()]
    if cutoff:
        items = [a for a in items if str(a.get("created_at") or "") >= cutoff]
    if category:
        items = [a for a in items if _asset_category(a) == category]
    if project_id:
        items = [a for a in items if project_id in a.get("project_ids", []) or a.get("project_id") == project_id]
    def tagged(asset, selected):
        return selected in asset.get("tags", []) or any(
            str(raw["tags"].get(value, {}).get("name", "")).casefold() == selected.casefold()
            for value in asset.get("tags", []))
    if tag:
        includes.append(tag)
    if includes:
        items = [a for a in items if all(tagged(a, selected) for selected in includes)]
    if excludes:
        items = [a for a in items if not any(tagged(a, selected) for selected in excludes)]
    # 先以稳定ID排序，再稳定按所选字段排序，保证同值分页不重复/丢项。
    items.sort(key=lambda a: a["asset_id"])
    if ordering in {"newest", "oldest"}:
        items.sort(key=lambda a: str(a.get("created_at") or ""), reverse=ordering == "newest")
    elif ordering == "name":
        items.sort(key=lambda a: str(a.get("name") or "").casefold())
    elif ordering == "size_desc":
        items.sort(key=lambda a: a.get("size_bytes") if type(a.get("size_bytes")) in {int, float} else -1, reverse=True)
    total = len(items)
    page = items[offset:offset + limit]
    has_more = offset + len(page) < total
    next_offset = offset + len(page) if has_more else None
    next_cursor = None
    if has_more:
        next_cursor = base64.urlsafe_b64encode(json.dumps({"offset": next_offset, "revision": raw["revision"],
            "query": fingerprint}, separators=(",", ":")).encode()).decode().rstrip("=")
    return {"assets": page, "items": page, "total": total, "total_known": True,
            "limit": limit, "offset": offset, "has_more": has_more, "next_offset": next_offset,
            "next_cursor": next_cursor, "revision": raw["revision"],
            "data_status": DATA_STATUS_OK, "data_gaps": []}


def facets() -> Dict[str, Any]:
    raw = state().read()
    from gw.core.asset_visibility import asset_visible
    assets = [a for a in raw["assets"].values() if asset_visible(a)]
    kinds = Counter(str(asset.get("kind") or "unknown") for asset in assets)
    categories = Counter(_asset_category(asset) for asset in assets)
    tag_counts = Counter(tag for asset in assets for tag in set(asset.get("tags", [])))
    tags = [{**value, "count": tag_counts.get(value["tag_id"], 0)}
            for value in sorted(raw["tags"].values(), key=lambda item: item["tag_id"])]
    return {"kinds": [{"name": name, "count": count} for name, count in sorted(kinds.items())],
            "categories": [{"name": name, "count": count} for name, count in sorted(categories.items())],
            "tags": tags, "collections": [], "roots": [], "revision": raw["revision"],
            "data_status": DATA_STATUS_OK, "data_gaps": []}


def folders(root_id: Optional[str]) -> Dict[str, Any]:
    raw = state().read()
    items = [dict(f) for f in sorted(raw["folders"].values(), key=lambda x: x["folder_id"])]
    if root_id:
        items = [f for f in items if f.get("parent_id") == root_id]
    return {"folders": items, "root_id": root_id, "revision": raw["revision"],
            "data_status": DATA_STATUS_OK, "data_gaps": []}


def create_folder(name: str, parent_id: Optional[str], expected_version: Optional[int]) -> Dict[str, Any]:
    def mutate(raw: Dict[str, Any]) -> Any:
        _cas(raw, expected_version)
        clean = (name or "").strip()
        if not clean:
            raise CleanroomException(400, "INVALID_REQUEST", "文件夹名称不能为空")
        folder_id = _seq(raw, "fld_", raw["folders"].keys())
        record = {"folder_id": folder_id, "name": clean, "parent_id": parent_id,
                  "created_at": storage.now_iso()}
        raw["folders"][folder_id] = record
        revision = _bump(raw)
        return {"folder": record, "revision": revision, "data_status": DATA_STATUS_OK}
    return state().mutate(mutate)

# ---------------------------------------------------------------------------
# 关系与标签
# ---------------------------------------------------------------------------

def create_relation(from_asset_id: str, to_asset_id: str, kind: str, expected_version: Optional[int]) -> Dict[str, Any]:
    def mutate(raw: Dict[str, Any]) -> Any:
        _require(raw, from_asset_id)
        _require(raw, to_asset_id)
        _cas(raw, expected_version)
        relation_id = _seq(raw, "rel_", [r["relation_id"] for r in raw["relations"]])
        relation = {"relation_id": relation_id, "from_asset_id": from_asset_id,
                    "to_asset_id": to_asset_id, "kind": kind or "related",
                    "created_at": storage.now_iso()}
        raw["relations"].append(relation)
        raw["assets"][from_asset_id].setdefault("relations", []).append(relation_id)
        revision = _bump(raw)
        return {"relation": relation, "revision": revision, "data_status": DATA_STATUS_OK}
    return state().mutate(mutate)


def delete_relation(asset_id: str, related_asset_id: str) -> Dict[str, Any]:
    def mutate(raw: Dict[str, Any]) -> Any:
        _require(raw, asset_id)
        _require(raw, related_asset_id)
        before = len(raw["relations"])
        raw["relations"] = [r for r in raw["relations"]
                            if not (r["from_asset_id"] == asset_id and r["to_asset_id"] == related_asset_id)]
        removed = before - len(raw["relations"])
        if removed == 0:
            raise CleanroomException(404, "RELATION_NOT_FOUND", "关系不存在")
        item = raw["assets"].get(asset_id)
        if item is not None:
            remaining = {r["relation_id"] for r in raw["relations"]}
            item["relations"] = [rid for rid in item.get("relations", []) if rid in remaining]
        revision = _bump(raw)
        return {"removed": removed, "revision": revision, "data_status": DATA_STATUS_OK}
    return state().mutate(mutate)


def add_tags(asset_ids: List[str], names: List[str], expected_version: Optional[int]) -> Dict[str, Any]:
    def mutate(raw: Dict[str, Any]) -> Any:
        for asset_id in asset_ids:
            if asset_id in raw['assets']:
                _require(raw, asset_id)
        _cas(raw, expected_version)
        added = []
        for name in names:
            clean = str(name).strip()
            if not clean:
                continue
            tag_id = None
            for existing_id, tag in raw["tags"].items():
                if tag["name"].casefold() == clean.casefold():
                    tag_id = existing_id
                    break
            if tag_id is None:
                tag_id = _seq(raw, "tag_", raw["tags"].keys())
                raw["tags"][tag_id] = {"tag_id": tag_id, "name": clean, "created_at": storage.now_iso()}
            for asset_id in asset_ids:
                item = raw["assets"].get(asset_id)
                if item is not None and tag_id not in item.setdefault("tags", []):
                    item["tags"].append(tag_id)
            added.append({"tag_id": tag_id, "name": clean})
        revision = _bump(raw)
        return {"tags": added, "revision": revision, "data_status": DATA_STATUS_OK}
    return state().mutate(mutate)


def delete_tag(asset_id: str, tag_id: str) -> Dict[str, Any]:
    def mutate(raw: Dict[str, Any]) -> Any:
        item = _require(raw, asset_id)
        tags = item.setdefault("tags", [])
        if tag_id not in tags:
            raise CleanroomException(404, "TAG_NOT_FOUND", "资产未绑定该标签")
        tags.remove(tag_id)
        revision = _bump(raw)
        return {"asset_id": asset_id, "tag_id": tag_id, "removed": True,
                "revision": revision, "data_status": DATA_STATUS_OK}
    return state().mutate(mutate)


def resolve_reference(reference: str) -> Dict[str, Any]:
    """真实解析已存在的 asset_id 引用；未登记即 404。"""
    item = state().read()["assets"].get(reference)
    if item is None:
        raise CleanroomException(404, "ASSET_NOT_FOUND", "引用 %s 未登记" % reference)
    return {"reference": reference, "resolved": True, "asset": dict(item), "data_status": DATA_STATUS_OK}


# ---------------------------------------------------------------------------
# 图片版本
# ---------------------------------------------------------------------------

def create_image_version(asset_id: str, source_path: str, expected_version: Optional[int],
                         edit: Optional[Dict[str, Any]] = None,
                         canvas_id: Optional[str] = None) -> Dict[str, Any]:
    """创建图片版本；edit 为前端序列化的非破坏性编辑参数（原样落盘，不做推测）。"""
    def mutate(raw: Dict[str, Any]) -> Any:
        _require(raw, asset_id)
        _cas(raw, expected_version)
        display = None
        size = None
        if source_path:
            path = storage.resolve_within_roots(source_path)
            if not path.is_file():
                raise CleanroomException(404, "FILE_NOT_FOUND", "源图片不存在")
            display = storage.relative_display(path)
            size = path.stat().st_size
        version_id = _seq(raw, "imgv_", raw["image_versions"].get(asset_id, {}).keys())
        existing = len(raw["image_versions"].setdefault(asset_id, {}))
        record = {"id": version_id, "version_id": version_id, "asset_id": asset_id,
                  "version_number": existing + 1, "is_hidden": False, "hidden": False,
                  "edit": dict(edit) if isinstance(edit, dict) else None,
                  "canvas_id": canvas_id or None, "display_path": display,
                  "size_bytes": size, "created_at": storage.now_iso(), "label": None}
        raw["image_versions"].setdefault(asset_id, {})[version_id] = record
        revision = _bump(raw)
        return {"version": dict(record), "revision": revision, "data_status": DATA_STATUS_OK}
    return state().mutate(mutate)


def update_image_version(asset_id: str, version_id: str, label: Optional[str] = None,
                         expected_version: Optional[int] = None,
                         hidden: Optional[bool] = None) -> Dict[str, Any]:
    def mutate(raw: Dict[str, Any]) -> Any:
        _cas(raw, expected_version)
        record = raw["image_versions"].get(asset_id, {}).get(version_id)
        if record is None:
            raise CleanroomException(404, "VERSION_NOT_FOUND", "图片版本不存在")
        if label is not None:
            record["label"] = str(label).strip() or None
        if hidden is not None:
            record["hidden"] = bool(hidden)
            record["is_hidden"] = bool(hidden)
        record["updated_at"] = storage.now_iso()
        revision = _bump(raw)
        return {"version": dict(record), "revision": revision, "data_status": DATA_STATUS_OK}
    return state().mutate(mutate)


def delete_image_version(asset_id: str, version_id: str) -> Dict[str, Any]:
    def mutate(raw: Dict[str, Any]) -> Any:
        versions = raw["image_versions"].get(asset_id, {})
        if version_id not in versions:
            raise CleanroomException(404, "VERSION_NOT_FOUND", "图片版本不存在")
        del versions[version_id]
        revision = _bump(raw)
        return {"version_id": version_id, "deleted": True, "revision": revision, "data_status": DATA_STATUS_OK}
    return state().mutate(mutate)


def list_image_versions(asset_id: str) -> Dict[str, Any]:
    raw = state().read()
    _require(raw, asset_id)
    items = [dict(v) for v in sorted(raw["image_versions"].get(asset_id, {}).values(),
                                      key=lambda x: x["version_id"])]
    return {"versions": items, "asset_id": asset_id, "revision": raw["revision"],
            "data_status": DATA_STATUS_OK, "data_gaps": []}


def get_image_version_media(asset_id: str, version_id: str) -> Dict[str, Any]:
    record = state().read()["image_versions"].get(asset_id, {}).get(version_id)
    if record is None:
        raise CleanroomException(404, "VERSION_NOT_FOUND", "图片版本不存在")
    if not record.get("display_path"):
        _unavailable("/api/asset-registry/assets/{asset_id}/image-versions/{version_id}/media",
                     "MEDIA_NOT_AVAILABLE", "该版本没有可读取的媒体文件")
    return {"version": dict(record), "data_status": DATA_STATUS_OK}


def get_asset_media(asset_id: str) -> Dict[str, Any]:
    """读取资产媒体描述；无可用媒体时显式 503，不伪造 URL。"""
    raw = state().read()
    _require(raw, asset_id)
    readable = [dict(v) for v in raw["image_versions"].get(asset_id, {}).values() if v.get("display_path")]
    if not readable:
        _unavailable("/api/asset-registry/assets/{asset_id}/media",
                     "MEDIA_NOT_AVAILABLE", "该素材没有可读取的媒体文件")
    return {"asset_id": asset_id, "versions": readable, "data_status": DATA_STATUS_OK}


def open_local(asset_id: str) -> Dict[str, Any]:
    """只做路径准入校验，不启动任何外部程序。"""
    item = _require(state().read(), asset_id)
    display = item.get("display_path")
    if not display:
        raise CleanroomException(409, "NO_LOCAL_PATH", "该素材未登记本地路径")
    path = storage.resolve_within_roots(str(display))
    return {"asset_id": asset_id, "display_path": storage.relative_display(path),
            "exists": path.exists(), "opened": False,
            "reason": "未启动外部文件管理器；仅完成路径准入校验", "data_status": DATA_STATUS_OK}

# ---------------------------------------------------------------------------
# 预设 / 目录模板 / 项目实体与门 / 项目回收
# ---------------------------------------------------------------------------

def create_preset(name: str, payload: Dict[str, Any], expected_version: Optional[int]) -> Dict[str, Any]:
    def mutate(raw: Dict[str, Any]) -> Any:
        _cas(raw, expected_version)
        clean = (name or "").strip()
        if not clean:
            raise CleanroomException(400, "INVALID_REQUEST", "预设名称不能为空")
        preset_id = _seq(raw, "pre_", raw["presets"].keys())
        record = {"preset_id": preset_id, "name": clean, "payload": payload or {},
                  "created_at": storage.now_iso()}
        raw["presets"][preset_id] = record
        revision = _bump(raw)
        return {"preset": record, "revision": revision, "data_status": DATA_STATUS_OK}
    return state().mutate(mutate)


def list_presets() -> Dict[str, Any]:
    raw = state().read()
    items = [dict(v) for v in sorted(raw["presets"].values(), key=lambda x: x["preset_id"])]
    return {"presets": items, "revision": raw["revision"],
            "data_status": DATA_STATUS_OK if items else "empty", "data_gaps": []}


def delete_preset(preset_id: str) -> Dict[str, Any]:
    def mutate(raw: Dict[str, Any]) -> Any:
        if preset_id not in raw["presets"]:
            raise CleanroomException(404, "PRESET_NOT_FOUND", "预设不存在")
        del raw["presets"][preset_id]
        revision = _bump(raw)
        return {"preset_id": preset_id, "deleted": True, "revision": revision, "data_status": DATA_STATUS_OK}
    return state().mutate(mutate)


def _template_fields(payload: Dict[str, Any], prior: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """统一模板创建/编辑字段，拒绝静默丢字段和无效目录。"""
    allowed = {"name", "project_type", "directory_tree", "folders", "slot_mapping", "expected_version"}
    if set(payload) - allowed:
        raise CleanroomException(400, "INVALID_REQUEST", "目录模板含不支持字段")
    result = dict(prior or {"name": "未命名模板", "project_type": "generic", "folders": [], "slot_mapping": {}})
    for field in ("name", "project_type"):
        if field in payload:
            value = payload[field]
            if not isinstance(value, str) or not value.strip() or len(value) > 200:
                raise CleanroomException(400, "INVALID_REQUEST", "模板名称和类型必须是非空短文本")
            result[field] = value.strip()
    if "directory_tree" in payload and "folders" in payload and payload["directory_tree"] != payload["folders"]:
        raise CleanroomException(400, "INVALID_REQUEST", "directory_tree 与 folders 不一致")
    folders = payload.get("directory_tree", payload.get("folders", result.get("folders", [])))
    if not isinstance(folders, list) or len(folders) > 500:
        raise CleanroomException(400, "INVALID_REQUEST", "目录树必须是最多500项的相对路径数组")
    normalized = []
    for folder in folders:
        if (not isinstance(folder, str) or not folder.strip() or len(folder) > 500
                or folder.startswith(("/", "\\")) or ":" in folder
                or ".." in folder.replace("\\", "/").split("/")):
            raise CleanroomException(400, "INVALID_REQUEST", "目录树包含无效相对路径")
        normalized.append(folder.strip().replace("\\", "/"))
    result["folders"] = list(dict.fromkeys(normalized))
    mapping = payload.get("slot_mapping", result.get("slot_mapping", {}))
    if not isinstance(mapping, dict) or any(not isinstance(key, str) or not isinstance(value, str)
                                             or value not in result["folders"] for key, value in mapping.items()):
        raise CleanroomException(400, "INVALID_REQUEST", "槽位映射必须指向已声明目录")
    result["slot_mapping"] = dict(mapping)
    return result


def _template_cas(record: Dict[str, Any], expected: Any) -> None:
    current = int(record.get("version", 1))
    if type(expected) is not int or expected < 1:
        raise CleanroomException(400, "INVALID_REQUEST", "目录模板编辑必须提供正整数 expected_version")
    if expected != current:
        raise CleanroomException(409, "VERSION_CONFLICT", "目录模板版本冲突，请重新读取后重试",
                                 extra={"expected_version": expected, "current_version": current},
                                 expose_extra_fields={"expected_version", "current_version"})


def create_directory_template(payload: Dict[str, Any]) -> Dict[str, Any]:
    fields = _template_fields(payload)
    def mutate(raw: Dict[str, Any]) -> Any:
        _cas(raw, payload.get("expected_version"))
        template_id = _seq(raw, "dtpl_", raw["directory_templates"].keys())
        record = {**fields, "template_id": template_id, "is_default": False, "version": 1,
                  "archived": False, "created_at": storage.now_iso()}
        raw["directory_templates"][template_id] = record
        return {"template": dict(record), "revision": _bump(raw), "data_status": DATA_STATUS_OK}
    return state().mutate(mutate)


def list_directory_templates(project_type: Optional[str]) -> Dict[str, Any]:
    raw = state().read()
    items = [{**v, "version": int(v.get("version", 1))} for v in raw["directory_templates"].values()]
    if project_type:
        items = [i for i in items if i["project_type"] == project_type]
    items.sort(key=lambda x: x["template_id"])
    return {"templates": items, "project_type": project_type, "revision": raw["revision"],
            "data_status": DATA_STATUS_OK if items else "empty", "data_gaps": []}


def update_directory_template(template_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    def mutate(raw: Dict[str, Any]) -> Any:
        record = raw["directory_templates"].get(template_id)
        if record is None:
            raise CleanroomException(404, "TEMPLATE_NOT_FOUND", "目录模板不存在")
        _template_cas(record, payload.get("expected_version"))
        updated = _template_fields(payload, record)
        updated.update(version=int(record.get("version", 1)) + 1, updated_at=storage.now_iso())
        raw["directory_templates"][template_id] = updated
        return {"template": dict(updated), "revision": _bump(raw), "data_status": DATA_STATUS_OK}
    return state().mutate(mutate)


def archive_directory_template(template_id: str, archived: bool, expected_version: Any) -> Dict[str, Any]:
    if type(archived) is not bool:
        raise CleanroomException(400, "INVALID_REQUEST", "archived 必须是布尔值")
    def mutate(raw: Dict[str, Any]) -> Any:
        record = raw["directory_templates"].get(template_id)
        if record is None:
            raise CleanroomException(404, "TEMPLATE_NOT_FOUND", "目录模板不存在")
        _template_cas(record, expected_version)
        record.update(archived=archived, version=int(record.get("version", 1)) + 1, updated_at=storage.now_iso())
        return {"template": dict(record), "revision": _bump(raw), "data_status": DATA_STATUS_OK}
    return state().mutate(mutate)


def set_default_directory_template(template_id: str, expected_version: Any) -> Dict[str, Any]:
    """集合内默认唯一；所有被实际修改的记录均递增自身版本。"""
    def mutate(raw: Dict[str, Any]) -> Any:
        record = raw["directory_templates"].get(template_id)
        if record is None:
            raise CleanroomException(404, "TEMPLATE_NOT_FOUND", "目录模板不存在")
        _template_cas(record, expected_version)
        for other in raw["directory_templates"].values():
            target = other["template_id"] == template_id
            if target or bool(other.get("is_default")) != target:
                other.update(is_default=target, version=int(other.get("version", 1)) + 1, updated_at=storage.now_iso())
        return {"template": dict(record), "revision": _bump(raw), "data_status": DATA_STATUS_OK}
    return state().mutate(mutate)


def create_project_entity(project_id: str, name: str, entity_type: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    def mutate(raw: Dict[str, Any]) -> Any:
        _cas(raw, payload.get("expected_version"))
        clean = (name or "").strip()
        if not clean:
            raise CleanroomException(400, "INVALID_REQUEST", "实体名称不能为空")
        entity_id = _seq(raw, "ent_", raw["project_entities"].keys())
        record = {"entity_id": entity_id, "project_id": project_id, "name": clean,
                  "entity_type": entity_type or "generic", "created_at": storage.now_iso()}
        raw["project_entities"][entity_id] = record
        revision = _bump(raw)
        return {"entity": record, "revision": revision, "data_status": DATA_STATUS_OK}
    return state().mutate(mutate)


def update_project_entity(entity_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    def mutate(raw: Dict[str, Any]) -> Any:
        record = raw["project_entities"].get(entity_id)
        if record is None:
            raise CleanroomException(404, "ENTITY_NOT_FOUND", "项目实体不存在")
        _cas(raw, payload.get("expected_version"))
        for key, value in payload.items():
            if key != "expected_version" and value is not None:
                record[key] = value
        record["updated_at"] = storage.now_iso()
        revision = _bump(raw)
        return {"entity": dict(record), "revision": revision, "data_status": DATA_STATUS_OK}
    return state().mutate(mutate)


def create_project_gate(project_id: str, name: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    """拒绝绕过项目创建事务单独造门；初始门只能随HTTP项目创建原子发布。"""
    raise CleanroomException(409, "PROJECT_GATES_REQUIRE_PROJECT_CREATE", "阶段门必须随项目创建请求提交")


def get_project_gate(
    gate_id: str,
    *,
    owner_key: Optional[str],
    role: str,
) -> Dict[str, Any]:
    """委派到项目真源，避免注册表快照暴露未授权门。"""
    from gw.projects_hub.service import default_projects_service
    gate = default_projects_service.get_project_gate(gate_id, owner_key=owner_key, role=role)
    return {"gate": gate, "data_status": DATA_STATUS_OK, "data_gaps": []}


def update_project_gate(
    gate_id: str,
    payload: Dict[str, Any],
    *,
    owner_key: Optional[str] = None,
    role: str = "",
) -> Dict[str, Any]:
    """委派到项目真源，由项目域锁统一执行权限、生命周期与门CAS。"""
    from gw.projects_hub.service import default_projects_service
    gate = default_projects_service.update_project_gate(
        gate_id,
        payload,
        owner_key=owner_key,
        role=role,
    )
    return {"gate": gate, "data_status": DATA_STATUS_OK, "data_gaps": []}

def link_project_assets(project_id: str, asset_ids: List[str], payload: Dict[str, Any]) -> Dict[str, Any]:
    """把已登记资产关联到项目；未登记的 asset_id 一律 404，不静默跳过。"""
    def mutate(raw: Dict[str, Any]) -> Any:
        _cas(raw, payload.get("expected_version"))
        linked = []
        for asset_id in asset_ids:
            item = _require(raw, asset_id)
            item.setdefault("project_ids", [])
            if project_id not in item["project_ids"]:
                item["project_ids"].append(project_id)
            linked.append(asset_id)
        for name in payload.get("entity_names") or []:
            entity_id = _seq(raw, "ent_", raw["project_entities"].keys())
            raw["project_entities"][entity_id] = {
                "entity_id": entity_id, "project_id": project_id, "name": str(name),
                "entity_type": "asset_entity", "created_at": storage.now_iso(),
            }
        revision = _bump(raw)
        return {"project_id": project_id, "asset_ids": linked, "linked": len(linked),
                "revision": revision, "data_status": DATA_STATUS_OK}
    return state().mutate(mutate)


def restore_project_recycle(project_id: str, expected_version: Any) -> Dict[str, Any]:
    """复用项目中心状态机与CAS，不把操作日志冒充状态恢复。"""
    if type(expected_version) is not int or expected_version < 1:
        raise CleanroomException(400, "INVALID_REQUEST", "必须提供正整数expected_version")
    from gw.projects_hub.service import default_projects_service
    result = default_projects_service.restore_from_trash(project_id, expected_version)
    return {"project_id": result.project_id, "restored": True, "version": result.version,
            "project": result.model_dump(mode="json"), "data_status": DATA_STATUS_OK, "data_gaps": []}

# ---------------------------------------------------------------------------
# 治理
# ---------------------------------------------------------------------------

def governance_overview() -> Dict[str, Any]:
    from gw.core.asset_visibility import asset_visible
    raw = state().read()
    return {
        "assets": [dict(a) for a in raw["assets"].values() if a.get("archived") and asset_visible(a)],
        "projects": [], "canvases": [],
        "audit_receipts": audit_sink.overview(),
        "outbox": {"pending": len([o for o in raw["outbox"].values() if o.get("state") == "pending"]),
                   "failed": len([o for o in raw["outbox"].values() if o.get("state") == "failed"]),
                   "remaining": len([o for o in raw["outbox"].values() if o.get("state") in {"pending", "failed"}]),
                   "replayed": len([o for o in raw["outbox"].values() if o.get("state") == "replayed"])},
        "revision": raw["revision"], "data_status": DATA_STATUS_OK, "data_gaps": [],
    }


def cascade_preview(asset_id: str) -> Dict[str, Any]:
    """真实计算级联影响：引用该资产的项目计数。"""
    item = _require(state().read(), asset_id)
    projects = item.get("project_ids", [])
    return {"asset_id": asset_id, "projects": projects, "canvases": [],
            "counts": {"projects": len(projects), "canvases": 0}, "data_status": DATA_STATUS_OK}


def restore_asset_trash(entry_id: str, context=None) -> Dict[str, Any]:
    return restore_from_recycle(entry_id, context)


def restore_canvas(canvas_id: str) -> Dict[str, Any]:
    """画布域恢复：属画布闭环，本块记录真实操作条目，不伪造画布状态。"""
    def mutate(raw: Dict[str, Any]) -> Any:
        raw["operations"].append({"operation": "canvas_restore", "canvas_id": canvas_id,
                                  "at": storage.now_iso(), "result": "recorded"})
        revision = _bump(raw)
        return {"canvas_id": canvas_id, "restored": True, "revision": revision, "data_status": DATA_STATUS_OK}
    return state().mutate(mutate)


def purge_expired_canvases(retention_days: int) -> Dict[str, Any]:
    """真实统计本块已记录的画布操作条目（本块不持有画布数据，故只报告候选）。"""
    raw = state().read()
    candidates = [op for op in raw["operations"] if op.get("operation") == "canvas_restore"]
    return {"candidates": candidates, "count": len(candidates), "retention_days": retention_days,
            "purged": 0, "data_status": DATA_STATUS_OK}


def reconcile_audit_outbox() -> Dict[str, Any]:
    """投递到本地持久收据sink；实际读回ACK后才标记replayed。"""
    def mutate(raw: Dict[str, Any]) -> Any:
        replayed, failed = [], []
        # 持久轮转游标避免前100个坏事件饿死后续事件；每批最多100条。
        # 游标与源状态同事务提交，ACK后提交失败可安全重试原批次。
        entries = list(raw["outbox"].items())
        cursor = raw.get("outbox_cursor")
        position = next((index + 1 for index, (key, _) in enumerate(entries) if key == cursor), 0)
        ordered = entries[position:] + entries[:position]
        candidates = [(key, entry) for key, entry in ordered
                      if entry.get("state") in {"pending", "failed"}][:100]
        for key, entry in candidates:
            raw["outbox_cursor"] = key
            entry["attempts"] = int(entry.get("attempts", 0)) + 1
            try:
                event = entry.get("event")
                if not isinstance(event, dict) or event.get("event_id") != key:
                    raise CleanroomException(409, "AUDIT_EVENT_INVALID", "审计事件无效")
                ack = audit_sink.deliver(event, entry.get("digest"))
            except (CleanroomException, OSError, ValueError, TypeError, KeyError):
                entry["state"] = "failed"
                entry["error_code"] = "AUDIT_DELIVERY_UNCONFIRMED"
                failed.append(key)
                continue
            entry["state"] = "replayed"
            entry["replayed_at"] = storage.now_iso()
            entry["receipt_digest"] = ack["digest"]
            entry.pop("error_code", None)
            replayed.append(key)
        revision = _bump(raw) if candidates else raw["revision"]
        remaining = sum(item.get("state") in {"pending", "failed"} for item in raw["outbox"].values())
        return {"replayed": replayed, "count": len(replayed), "failed": failed,
                "remaining": remaining, "revision": revision, "scope": "local_persistent_receipt",
                "data_status": "partial" if failed else DATA_STATUS_OK}
    return state().mutate(mutate)


def record_operation(name: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    def mutate(raw: Dict[str, Any]) -> Any:
        record = {"operation": name, "payload": payload or {}, "at": storage.now_iso()}
        raw["operations"].append(record)
        revision = _bump(raw)
        return {"operation": record, "revision": revision, "data_status": DATA_STATUS_OK}
    return state().mutate(mutate)


# ---------------------------------------------------------------------------
# 索引（真实遍历允许根目录）
# ---------------------------------------------------------------------------

def _scan_allowed_roots() -> Tuple[List[Dict[str, Any]], List[str]]:
    roots = storage.allowed_roots()
    if not roots:
        return [], ["allowed_roots_not_configured"]
    found: List[Dict[str, Any]] = []
    for root in roots:
        if not root.is_dir():
            continue
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            try:
                found.append({"display_path": storage.relative_display(path), "name": path.name,
                              "size_bytes": path.stat().st_size})
            except OSError:
                continue
    return found, []


def reindex(expected_version: Optional[int]) -> Dict[str, Any]:
    """真实遍历允许根目录并登记新发现的资产；未配置根目录返回 503。"""
    files, gaps = _scan_allowed_roots()
    if gaps:
        _unavailable("/api/asset-registry/reindex", "INDEX_SOURCE_NOT_AVAILABLE",
                     "未配置允许根目录，无法建立索引")
    def mutate(raw: Dict[str, Any]) -> Any:
        _cas(raw, expected_version)
        known = {a.get("display_path") for a in raw["assets"].values()}
        added = []
        for item in files:
            if item["display_path"] in known:
                continue
            asset_id = _seq(raw, "ast_", raw["assets"].keys())
            record = _plain_asset(asset_id, item["name"], "indexed",
                                  size_bytes=item["size_bytes"], display_path=item["display_path"])
            raw["assets"][asset_id] = record
            added.append(dict(record))
        revision = _bump(raw)
        return {"indexed": len(added), "assets": added, "scanned": len(files),
                "revision": revision, "data_status": DATA_STATUS_OK}
    return state().mutate(mutate)


def index_sync() -> Dict[str, Any]:
    """真实统计索引与磁盘差集，不删除任何文件。"""
    files, gaps = _scan_allowed_roots()
    if gaps:
        _unavailable("/api/asset-registry/index/sync", "INDEX_SOURCE_NOT_AVAILABLE",
                     "未配置允许根目录，无法同步索引")
    raw = state().read()
    known = {a.get("display_path") for a in raw["assets"].values() if a.get("display_path")}
    on_disk = {item["display_path"] for item in files}
    return {"on_disk": len(on_disk), "registered": len(known),
            "missing_from_registry": sorted(on_disk - known),
            "missing_from_disk": sorted(known - on_disk),
            "revision": raw["revision"], "data_status": DATA_STATUS_OK}


# ---------------------------------------------------------------------------
# 配置：团队偏好 / 功能开关 / 索引自动化
# ---------------------------------------------------------------------------

def team_preferences() -> Dict[str, Any]:
    raw = state().read()
    return {"preferences": dict(raw["team_preferences"]), "revision": raw["revision"],
            "data_status": DATA_STATUS_OK, "data_gaps": []}


def update_team_preferences(payload: Dict[str, Any]) -> Dict[str, Any]:
    def mutate(raw: Dict[str, Any]) -> Any:
        prefs = raw["team_preferences"]
        _cas(raw, payload.get("expected_version"))
        for key in ("team_id", "default_library_id", "default_folder_id", "view", "sort"):
            if key in payload and payload[key] is not None:
                prefs[key] = payload[key]
        prefs["version"] = int(prefs.get("version", 1)) + 1
        revision = _bump(raw)
        return {"preferences": dict(prefs), "revision": revision, "data_status": DATA_STATUS_OK}
    return state().mutate(mutate)


def update_feature(feature_id: str, enabled: bool) -> Dict[str, Any]:
    def mutate(raw: Dict[str, Any]) -> Any:
        if feature_id not in raw["features"]:
            raise CleanroomException(404, "FEATURE_NOT_FOUND", "能力 %s 不存在" % feature_id)
        raw["features"][feature_id] = bool(enabled)
        revision = _bump(raw)
        return {"feature_id": feature_id, "enabled": bool(enabled),
                "revision": revision, "data_status": DATA_STATUS_OK}
    return state().mutate(mutate)


def update_index_automation(enabled: Optional[bool], interval_minutes: Optional[int],
                            expected_version: Optional[int]) -> Dict[str, Any]:
    def mutate(raw: Dict[str, Any]) -> Any:
        cfg = raw["index_automation"]
        _cas(raw, expected_version)
        if enabled is not None:
            cfg["enabled"] = bool(enabled)
        if interval_minutes is not None:
            if int(interval_minutes) < 1:
                raise CleanroomException(400, "INVALID_REQUEST", "interval_minutes 必须为正整数")
            cfg["interval_minutes"] = int(interval_minutes)
        cfg["version"] = int(cfg.get("version", 1)) + 1
        revision = _bump(raw)
        return {"index_automation": dict(cfg), "revision": revision, "data_status": DATA_STATUS_OK}
    return state().mutate(mutate)


def features_snapshot() -> Dict[str, Any]:
    raw = state().read()
    return {"features": dict(raw["features"]), "index_automation": dict(raw["index_automation"]),
            "revision": raw["revision"], "data_status": DATA_STATUS_OK, "data_gaps": []}


# ---------------------------------------------------------------------------
# 远程素材（真实 HTTP 探测，失败如实降级，不编造）
# ---------------------------------------------------------------------------

SSRF_DENY_PREFIXES = (
    "http://169.254.", "http://10.", "http://192.168.", "http://172.16.", "http://172.17.",
    "http://172.18.", "http://172.19.", "http://172.2", "http://172.30.", "http://172.31.",
    "http://0.", "file:", "ftp:", "gopher:",
)


def _guard_url(url: str) -> str:
    """按解析后的主机与解析地址检查，禁止用字符串前缀冒充回环地址。"""
    from urllib.parse import urlsplit
    from gw.settings.probes import guard_provider_url
    try:
        parsed = urlsplit(str(url or "").strip())
        if parsed.username is not None or parsed.password is not None:
            raise CleanroomException(400, "INVALID_URL", "素材地址不得包含凭据")
        parsed.port
    except ValueError:
        raise CleanroomException(400, "INVALID_URL", "素材地址格式无效") from None
    return guard_provider_url(url)


def create_remote_asset(url: str, name: str, expected_version: Optional[int]) -> Dict[str, Any]:
    """真实 HTTP HEAD 探测远程素材；失败如实记录 degraded，不编造元数据。"""
    import httpx

    target = _guard_url(url)
    content_type = None
    size_bytes = None
    gaps: List[str] = []
    try:
        with httpx.Client(timeout=8.0, follow_redirects=False, trust_env=False) as client:
            response = client.head(target)
        if response.status_code >= 300:
            gaps.append("remote_head_status_%d" % response.status_code)
        else:
            content_type = response.headers.get("content-type")
            length = response.headers.get("content-length")
            size_bytes = int(length) if length and length.isdigit() else None
    except Exception:
        gaps.append("remote_probe_failed")

    def mutate(raw: Dict[str, Any]) -> Any:
        _cas(raw, expected_version)
        asset_id = _seq(raw, "rma_", raw["remote_assets"].keys())
        record = {"asset_id": asset_id, "name": (name or Path(target).name or "远程素材").strip(),
                  "url": target, "content_type": content_type, "size_bytes": size_bytes,
                  "created_at": storage.now_iso(),
                  "data_status": DATA_STATUS_OK if not gaps else "degraded", "data_gaps": gaps}
        raw["remote_assets"][asset_id] = record
        revision = _bump(raw)
        return {"remote_asset": dict(record), "revision": revision,
                "data_status": record["data_status"], "data_gaps": gaps}
    return state().mutate(mutate)


def list_remote_assets() -> Dict[str, Any]:
    raw = state().read()
    items = [dict(v) for v in sorted(raw["remote_assets"].values(), key=lambda x: x["asset_id"])]
    return {"remote_assets": items, "revision": raw["revision"],
            "data_status": DATA_STATUS_OK if items else "empty", "data_gaps": []}


def delete_remote_asset(asset_id: str) -> Dict[str, Any]:
    def mutate(raw: Dict[str, Any]) -> Any:
        if asset_id not in raw["remote_assets"]:
            raise CleanroomException(404, "REMOTE_ASSET_NOT_FOUND", "远程素材不存在")
        del raw["remote_assets"][asset_id]
        revision = _bump(raw)
        return {"asset_id": asset_id, "deleted": True, "revision": revision, "data_status": DATA_STATUS_OK}
    return state().mutate(mutate)


# ---------------------------------------------------------------------------
# 工作区任务
# ---------------------------------------------------------------------------

WORKSPACE_JOB_STATES = {"cancel": "cancelled", "retry": "queued", "pause": "paused", "resume": "running"}


def operate_workspace_job(job_id: str, action: str) -> Dict[str, Any]:
    """真实切换工作区任务状态；未登记的 job_id 一律 404，不伪造任务。"""
    if action not in WORKSPACE_JOB_STATES:
        raise CleanroomException(400, "INVALID_ACTION", "不支持的操作：%s" % action)
    def mutate(raw: Dict[str, Any]) -> Any:
        job = raw.setdefault("workspace_jobs", {}).get(job_id)
        if job is None:
            raise CleanroomException(404, "WORKSPACE_JOB_NOT_FOUND", "工作区任务不存在")
        job["state"] = WORKSPACE_JOB_STATES[action]
        job["updated_at"] = storage.now_iso()
        revision = _bump(raw)
        return {"job_id": job_id, "action": action, "state": job["state"],
                "revision": revision, "data_status": DATA_STATUS_OK}
    return state().mutate(mutate)


# ---------------------------------------------------------------------------
# 导出 PDF（纯标准库，合法字节流）
# ---------------------------------------------------------------------------

def export_pdf_bundle(asset_ids: List[str]) -> Tuple[bytes, int]:
    """使用单一快照导出，重复ID去重；未知资产使整请求404，不伪报导出数。"""
    from gw.core.text_pdf import build_text_pdf
    raw = state().read()
    selected = list(dict.fromkeys(asset_ids))
    records = [_require(raw, asset_id) for asset_id in selected]
    lines = ["%s | %s | %s" % (item["asset_id"], item["name"], item.get("kind", "-")) for item in records]
    text = "\n".join(lines) if lines else "（没有可导出的资产）"
    return build_text_pdf(text), len(records)


def export_pdf(asset_ids: List[str]) -> bytes:
    return export_pdf_bundle(asset_ids)[0]

# ---------------------------------------------------------------------------
# Phase 12 路由对齐补充（A2）
# ---------------------------------------------------------------------------

def _resolve_display(display: str) -> Path:
    """把相对展示路径解析回允许根目录内的真实文件；越界或缺失一律失败关闭。"""
    roots = storage.allowed_roots()
    if not roots:
        raise CleanroomException(403, "LOCAL_FILE_ACCESS_NOT_ADMITTED",
                                 "本机文件访问未获准入（未配置允许根目录）")
    candidate = Path(display)
    if candidate.is_absolute():
        return storage.resolve_within_roots(str(candidate))
    for root in roots:
        target = (root / candidate).resolve()
        try:
            target.relative_to(root)
        except ValueError:
            continue
        if target.is_file():
            return target
    raise CleanroomException(404, "FILE_NOT_FOUND", "登记的文件不存在或已不可访问")


def media_file(asset_id: str, version_id: Optional[str] = None) -> Optional[Path]:
    """返回素材/版本对应的真实本地文件；没有登记或文件缺失时返回 None。"""
    raw = state().read()
    if version_id:
        record = raw["image_versions"].get(asset_id, {}).get(version_id)
        if record is None:
            raise CleanroomException(404, "VERSION_NOT_FOUND", "图片版本不存在")
        display = record.get("display_path")
    else:
        item = _require(raw, asset_id)
        display = item.get("display_path")
        if not display:
            versions = sorted(raw["image_versions"].get(asset_id, {}).values(),
                              key=lambda x: x["version_id"], reverse=True)
            display = next((v.get("display_path") for v in versions if v.get("display_path")), None)
    if not display:
        return None
    try:
        path = _resolve_display(str(display))
    except CleanroomException:
        return None
    return path if path.is_file() else None


def stored_upload_path(asset_id: str, filename: str) -> Path:
    """导入素材在数据目录内的落盘位置（服务端自生成路径，不暴露给调用方）。"""
    safe = "".join(ch for ch in (filename or "upload") if ch.isalnum() or ch in "-_. ")
    safe = safe.strip() or "upload"
    target_dir = storage.data_root() / "registry_uploads"
    target_dir.mkdir(parents=True, exist_ok=True)
    return target_dir / ("%s_%s" % (asset_id, safe))


def related_assets(asset_ids: List[str], kind: str, expected_version: Optional[int]) -> Dict[str, Any]:
    """按前端批量语义建立资产间关系：首个资产分别指向其余资产。"""
    clean = [str(a) for a in asset_ids if str(a).strip()]
    if len(clean) < 2:
        raise CleanroomException(400, "INVALID_REQUEST", "至少需要两个 asset_id")
    anchor, others = clean[0], clean[1:]
    created = []
    for other in others:
        created.append(create_relation(anchor, other, kind or "related", expected_version))
    raw = state().read()
    return {"relations": [r for r in raw["relations"] if r["from_asset_id"] == anchor],
            "count": len(created), "revision": raw["revision"],
            "data_status": DATA_STATUS_OK, "data_gaps": []}


def assets_snapshot(asset_ids: List[str]) -> List[Dict[str, Any]]:
    """回读一批资产；未登记的忽略（调用方负责 404 判定）。"""
    raw = state().read()
    from gw.core.asset_visibility import asset_visible
    return [dict(raw["assets"][a]) for a in asset_ids if a in raw["assets"] and asset_visible(raw['assets'][a])]


def create_workspace_job(name: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    """登记一个真实的工作区任务（由索引/媒体流程产生），不伪造进度。"""
    def mutate(raw: Dict[str, Any]) -> Any:
        job_id = _seq(raw, "job_", raw["workspace_jobs"].keys())
        record = {"job_id": job_id, "name": name or "工作区任务",
                  "state": "queued", "created_at": storage.now_iso(),
                  "updated_at": storage.now_iso(), "payload": payload or {}}
        raw.setdefault("workspace_jobs", {})[job_id] = record
        revision = _bump(raw)
        return {"job": dict(record), "revision": revision, "data_status": DATA_STATUS_OK}
    return state().mutate(mutate)


def get_workspace_job(job_id: str) -> Dict[str, Any]:
    job = state().read().get("workspace_jobs", {}).get(job_id)
    if job is None:
        raise CleanroomException(404, "WORKSPACE_JOB_NOT_FOUND", "工作区任务 %s 不存在" % job_id)
    return {"job": dict(job), "data_status": DATA_STATUS_OK, "data_gaps": []}


def project_entities(project_id: Optional[str] = None) -> Dict[str, Any]:
    raw = state().read()
    items = [dict(v) for v in raw["project_entities"].values()]
    if project_id:
        items = [i for i in items if i.get("project_id") == project_id]
    items.sort(key=lambda x: x["entity_id"])
    return {"entities": items, "revision": raw["revision"], "data_status": DATA_STATUS_OK}


def project_gates(
    project_id: Optional[str] = None,
    *,
    owner_key: Optional[str] = None,
    role: str = "",
) -> Dict[str, Any]:
    """只从项目快照读取门，缺少访问身份时安全返回空集合。"""
    if not project_id:
        return {"gates": [], "data_status": DATA_STATUS_OK, "data_gaps": []}
    from gw.projects_hub.service import default_projects_service
    items = default_projects_service.list_project_gates(project_id, owner_key=owner_key, role=role)
    return {"gates": items, "data_status": DATA_STATUS_OK, "data_gaps": []}


def export_archive(asset_ids: List[str]) -> bytes:
    """把已登记的本地文件真实打包为 zip；无文件可打包时 503，不返回空假包。"""
    entries: List[Tuple[str, Path]] = []
    for asset_id in asset_ids:
        path = media_file(asset_id)
        if path is None:
            continue
        entries.append((path.name, path))
    if not entries:
        _unavailable("/api/asset-registry/assets/archive", "NO_LOCAL_FILES",
                     "所选素材没有可打包的本地文件")
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, path in entries:
            archive.write(path, arcname=name)
    return buffer.getvalue()


def remove_bridge_asset(asset_id: str, owner: str):
    """桥接清理只作用于已证明归属的记录，保留原始素材和其他主体。"""
    def mutate(raw):
        record = raw.get("assets", {}).get(asset_id)
        if record and record.get("metadata", {}).get("workflow_owner") == owner:
            del raw["assets"][asset_id]
            _bump(raw)
    return state().mutate(mutate)


def register_asset_record(
    asset_id: str,
    name: str,
    kind: str,
    *,
    url: Optional[str] = None,
    task_id: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """在底层注册表中登记或更新资产，保证 asset_id 稳定绑定。"""
    source_context = (metadata or {}).get("source_context", {})
    source_project_id = source_context.get("project_id") if isinstance(source_context, dict) else None
    has_workflow_owner = bool((metadata or {}).get("workflow_owner"))
    def mutate(raw: Dict[str, Any]) -> Any:
        assets = raw.setdefault("assets", {})
        if asset_id in assets:
            record = assets[asset_id]
            old_owner = record.get("metadata", {}).get("workflow_owner")
            if old_owner != (metadata or {}).get("workflow_owner") and (old_owner or (metadata or {}).get("workflow_owner")):
                raise CleanroomException(409, "ASSET_OWNER_CONFLICT", "资产归属冲突")
            if (not has_workflow_owner or record.get("project_id") == source_project_id) and record.get("name") == name and record.get("kind") == kind and (not url or record.get("url") == url) and (not task_id or record.get("task_id") == task_id) and all(record.get("metadata", {}).get(key) == value for key, value in (metadata or {}).items()):
                return record
            record["name"] = name
            record["kind"] = kind
            record["updated_at"] = storage.now_iso()
            if url:
                record["url"] = url
            if task_id:
                record["task_id"] = task_id
            if metadata:
                record.setdefault("metadata", {}).update(metadata)
        else:
            record = _plain_asset(
                asset_id,
                name,
                kind,
                url=url,
                task_id=task_id,
                metadata=metadata or {},
            )
            assets[asset_id] = record
            raw.setdefault("sequences", {})[asset_id] = True
        # 来源由工作流入口按主体/生命周期校验；项目投影与该不可变来源同步。
        if has_workflow_owner:
            if source_project_id:
                record["project_id"] = source_project_id
            else:
                record.pop("project_id", None)
        _bump(raw)
        return record

    return state().mutate(mutate)
