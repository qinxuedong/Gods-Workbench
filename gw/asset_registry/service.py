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

"""Phase 11 B3 素材注册表的洁净室服务。

本块先建立可审计的注册表边界，而不是伪造文件/媒体/远程索引能力：
- 仅返回进程内可证明的空注册表、空回收站、空关系和空版本集合；
- 所有会读取或写入真实文件、媒体、远程资源、索引、压缩包的操作显式 503；
- 不创建演示素材，不扫描本机路径，不执行外部程序或网络请求；
- 所有响应携带 data_status/data_gaps，便于前端保留显式降级。
"""

from __future__ import annotations

import copy
import json
import threading
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from gw.core.errors import CleanroomException

DATA_STATUS_OK = "ok"
DATA_STATUS_NOT_INTEGRATED = "not_integrated"
ASSET_REGISTRY_NOT_INTEGRATED = "ASSET_REGISTRY_NOT_INTEGRATED"
ASSET_NOT_FOUND = "ASSET_NOT_FOUND"
JOB_NOT_FOUND = "WORKSPACE_JOB_NOT_FOUND"
ASSET_ALREADY_EXISTS = "ASSET_ALREADY_EXISTS"
RECYCLE_ENTRY_NOT_FOUND = "RECYCLE_ENTRY_NOT_FOUND"
ASSET_RESTORE_CONFLICT = "ASSET_RESTORE_CONFLICT"
ASSET_RESTORE_PARTIAL_FAILURE = "ASSET_RESTORE_PARTIAL_FAILURE"

_GAP_REGISTRY_SOURCE = "asset_registry_source_not_connected"
_GAP_MEDIA_SOURCE = "media_storage_not_connected"
_GAP_REMOTE_SOURCE = "remote_asset_source_not_connected"
_GAP_INDEX_SOURCE = "asset_index_not_connected"

_STRUCTURED_KINDS = {"image", "video", "audio", "document", "model", "font", "other"}
_STRUCTURED_FILTER_FIELDS = {"kind", "project_id", "tag_id", "created_at", "status"}
_STRUCTURED_SORTS = {"newest", "oldest", "name", "asset_id"}
_STRUCTURED_INCLUDES = {"facets", "preview"}


def _structured_error(code: str, message: str) -> None:
    raise CleanroomException(400, code, message)


def _json_or_csv(value: Any, *, field: str) -> Any:
    """读取 GET 参数里的 JSON 数组/对象，同时兼容简短逗号分隔写法。"""
    if not isinstance(value, str):
        return value
    raw = value.strip()
    if not raw:
        return []
    if raw[0] in "[{":
        try:
            return json.loads(raw)
        except (TypeError, ValueError):
            _structured_error("UNSUPPORTED_FILTER_VALUE", f"{field} 不是有效 JSON")
    return [part.strip() for part in raw.split(",") if part.strip()]


def _string_list(value: Any, *, field: str, allowed: Optional[set[str]] = None) -> List[str]:
    values = _json_or_csv(value, field=field)
    if values is None:
        return []
    if not isinstance(values, list):
        _structured_error("UNSUPPORTED_FILTER_VALUE", f"{field} 必须是字符串数组")
    result: List[str] = []
    for item in values:
        if not isinstance(item, str) or not item.strip():
            _structured_error("UNSUPPORTED_FILTER_VALUE", f"{field} 包含无效值")
        clean = item.strip()
        if allowed is not None and clean not in allowed:
            _structured_error("UNSUPPORTED_FILTER_VALUE", f"{field} 不支持值 {clean}")
        if clean not in result:
            result.append(clean)
    return sorted(result)


def parse_utc_datetime(value: Any, *, field: str = "created_at") -> datetime:
    """解析带时区的 UTC RFC3339 时间；拒绝本地时间和无效时间。"""
    if not isinstance(value, str) or not value.strip():
        _structured_error("INVALID_TIME_RANGE", f"{field} 必须是带时区的 RFC3339 时间")
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except (TypeError, ValueError):
        _structured_error("INVALID_TIME_RANGE", f"{field} 不是有效的 RFC3339 时间")
    if parsed.tzinfo is None:
        _structured_error("INVALID_TIME_RANGE", f"{field} 必须包含时区")
    return parsed.astimezone(timezone.utc)


def _canonical_datetime(value: Any, *, field: str) -> str:
    return parse_utc_datetime(value, field=field).isoformat().replace("+00:00", "Z")


def normalize_asset_query(
    *,
    scope: Any = None,
    text: Any = None,
    filters: Any = None,
    sort: Optional[str] = None,
    limit: Any = 50,
    include: Any = None,
) -> Dict[str, Any]:
    """规范化 C11 资产查询，未知字段和值一律显式失败。"""
    scopes = _string_list(scope or ["asset"], field="scope")
    if not scopes:
        _structured_error("UNSUPPORTED_FILTER_VALUE", "scope 不能为空")
    if any(value != "asset" for value in scopes):
        _structured_error("UNSUPPORTED_FILTER_VALUE", "当前资产入口只支持 asset scope")

    if text is None:
        clean_text = ""
    elif isinstance(text, str):
        clean_text = text.strip()
    else:
        _structured_error("UNSUPPORTED_FILTER_VALUE", "text 必须是字符串")

    raw_filters = _json_or_csv(filters or {}, field="filters")
    if raw_filters is None:
        raw_filters = {}
    if not isinstance(raw_filters, dict):
        _structured_error("UNSUPPORTED_FILTER_VALUE", "filters 必须是对象")
    unknown = set(raw_filters) - _STRUCTURED_FILTER_FIELDS
    if unknown:
        _structured_error("UNSUPPORTED_FILTER_VALUE", f"不支持过滤字段: {sorted(unknown)[0]}")

    normalized: Dict[str, Any] = {}
    if "kind" in raw_filters:
        normalized["kind"] = _string_list(raw_filters.get("kind"), field="kind", allowed=_STRUCTURED_KINDS)
    if "project_id" in raw_filters:
        normalized["project_id"] = _string_list(raw_filters.get("project_id"), field="project_id")
    if "tag_id" in raw_filters:
        normalized["tag_id"] = _string_list(raw_filters.get("tag_id"), field="tag_id")
    if "created_at" in raw_filters:
        created = raw_filters.get("created_at")
        if not isinstance(created, dict) or set(created) - {"from", "to"}:
            _structured_error("INVALID_TIME_RANGE", "created_at 只接受 from/to")
        if not created.get("from") and not created.get("to"):
            _structured_error("INVALID_TIME_RANGE", "created_at 至少需要 from 或 to")
        window: Dict[str, str] = {}
        if created.get("from") is not None:
            window["from"] = _canonical_datetime(created["from"], field="created_at.from")
        if created.get("to") is not None:
            window["to"] = _canonical_datetime(created["to"], field="created_at.to")
        if "from" in window and "to" in window and window["from"] >= window["to"]:
            _structured_error("INVALID_TIME_RANGE", "created_at.from 必须早于 created_at.to")
        normalized["created_at"] = window
    if "status" in raw_filters:
        status = raw_filters.get("status")
        if not isinstance(status, dict) or set(status) - {"asset"}:
            _structured_error("UNSUPPORTED_FILTER_VALUE", "status 必须明确指定 asset 域")
        normalized["status"] = {
            "asset": _string_list(status.get("asset"), field="status.asset", allowed={"active", "archived"})
        }

    clean_sort = str(sort or "newest").strip()
    if clean_sort not in _STRUCTURED_SORTS:
        _structured_error("UNSUPPORTED_SORT", f"不支持的资产排序 {clean_sort}")
    if type(limit) is not int or not 1 <= limit <= 200:
        _structured_error("INVALID_LIMIT", "结构化查询 limit 必须在 1 到 200 之间")
    includes = _string_list(include or [], field="include", allowed=_STRUCTURED_INCLUDES)
    return {
        "scope": scopes,
        "text": clean_text,
        "filters": normalized,
        "sort": clean_sort,
        "limit": limit,
        "include": includes,
    }


def _not_integrated(endpoint: str, *, code: str = ASSET_REGISTRY_NOT_INTEGRATED, gap: str = _GAP_REGISTRY_SOURCE) -> None:
    """对未获准入的副作用统一失败关闭。"""
    raise CleanroomException(
        status_code=503,
        code=code,
        message="素材注册表该能力尚未接入真实数据源，已拒绝执行；未读取或写入外部资源。",
        extra={
            "endpoint": endpoint,
            "unavailable": True,
            "data_status": DATA_STATUS_NOT_INTEGRATED,
            "data_gaps": [gap],
        },
        expose_extra_fields={"endpoint", "unavailable", "data_status"},
    )


def _not_found(code: str, message: str) -> None:
    """返回可区分的资源不存在错误。"""
    raise CleanroomException(status_code=404, code=code, message=message)


def _truthful_meta(*, gaps: Optional[List[str]] = None) -> Dict[str, Any]:
    """构造统一的可用性元数据，不把空集合误报为成功抓取。"""
    return {
        "data_status": DATA_STATUS_OK if not gaps else DATA_STATUS_NOT_INTEGRATED,
        "data_gaps": list(gaps or []),
    }


def _now_iso() -> str:
    """返回可回读的 UTC 事实时间，不依赖外部时钟服务。"""
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class AssetRegistryService:
    """素材注册表的最小进程内真值。

    当前不从旧仓、数据库、本地目录或远程服务读取资产，因此初始集合严格为空。
    这样可以支持列表/状态/治理页面的确定性空态，同时让真实资源操作保持 503。
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._revision = 1
        # 仅保存调用方明确登记的内存快照；不扫描文件系统，也不把空态伪装成真实来源。
        self._assets: Dict[str, Dict[str, Any]] = {}
        self._recycle_entries: Dict[str, Dict[str, Any]] = {}
        self._recycle_sequence = 0
        # 只缓存显式幂等键对应的本地事实；不以键推断或重放外部副作用。
        self._idempotent_results: Dict[str, Dict[str, Any]] = {}
        self._team_preferences: Dict[str, Any] = {
            "team_id": None,
            "default_library_id": None,
            "default_folder_id": None,
            "view": "grid",
            "sort": "newest",
            "version": 1,
        }

    def _meta(self) -> Dict[str, Any]:
        return {"revision": self._revision, **_truthful_meta()}

    def root_snapshot(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "registry": "asset-registry",
                "revision": self._revision,
                "assets": [copy.deepcopy(item) for item in self._assets.values()],
                "features": self._features(),
                "backend": "cleanroom-memory",
                **_truthful_meta(),
            }

    def status(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "ready": True,
                "backend": "cleanroom-memory",
                "revision": self._revision,
                "assets_count": len(self._assets),
                "jobs": [],
                "overview": {"assets": len(self._assets), "projects": 0, "canvases": 0},
                "features": self._features(),
                **_truthful_meta(),
            }

    @staticmethod
    def _features() -> List[Dict[str, Any]]:
        return [
            {"id": "asset_registry", "name": "素材登记", "enabled": False, "data_status": DATA_STATUS_NOT_INTEGRATED},
            {"id": "media_preview", "name": "媒体预览", "enabled": False, "data_status": DATA_STATUS_NOT_INTEGRATED},
            {"id": "remote_assets", "name": "远程素材", "enabled": False, "data_status": DATA_STATUS_NOT_INTEGRATED},
            {"id": "index_automation", "name": "索引自动化", "enabled": False, "data_status": DATA_STATUS_NOT_INTEGRATED},
        ]

    def list_assets(
        self,
        *,
        limit: int = 100,
        offset: int = 0,
        cursor: Optional[str] = None,
        structured: bool = False,
        scope: Any = None,
        text: Any = None,
        filters: Any = None,
        sort: Optional[str] = None,
        include: Any = None,
        **_: Any,
    ) -> Dict[str, Any]:
        # 集合只包含调用方显式登记的内存快照，不生成演示数据。
        if structured:
            normalized = normalize_asset_query(
                scope=scope,
                text=text,
                filters=filters,
                sort=sort,
                limit=limit,
                include=include,
            )
            if offset < 0:
                raise CleanroomException(status_code=400, code="INVALID_OFFSET", message="offset 不能为负数")
            if cursor is not None:
                try:
                    cursor_offset = int(cursor)
                except (TypeError, ValueError):
                    raise CleanroomException(400, "INVALID_CURSOR", "分页游标无效或不属于当前查询") from None
                if cursor_offset < 0 or (offset and offset != cursor_offset):
                    raise CleanroomException(400, "INVALID_CURSOR", "分页游标无效或不属于当前查询")
                offset = cursor_offset
            with self._lock:
                values = [copy.deepcopy(item) for item in self._assets.values()]
                needle = normalized["text"].casefold()
                if needle:
                    values = [
                        item for item in values
                        if needle in str(item.get("name") or "").casefold()
                        or needle in str(item.get("asset_id") or "").casefold()
                    ]
                filters_map = normalized["filters"]
                if filters_map.get("kind"):
                    values = [item for item in values if str(item.get("kind") or "other") in filters_map["kind"]]
                if filters_map.get("project_id"):
                    projects = set(filters_map["project_id"])
                    values = [item for item in values if projects.intersection(
                        set(item.get("project_ids") or [])
                        | ({item["project_id"]} if item.get("project_id") else set())
                    )]
                if filters_map.get("tag_id"):
                    tags = set(filters_map["tag_id"])
                    values = [item for item in values if tags.intersection(set(item.get("tags") or []))]
                if filters_map.get("status", {}).get("asset"):
                    allowed_status = set(filters_map["status"]["asset"])
                    values = [item for item in values if
                              ("archived" if item.get("archived") else "active") in allowed_status]
                if "created_at" in filters_map:
                    window = filters_map["created_at"]
                    start = parse_utc_datetime(window["from"]) if window.get("from") else None
                    end = parse_utc_datetime(window["to"]) if window.get("to") else None
                    selected = []
                    for item in values:
                        try:
                            created = parse_utc_datetime(item.get("created_at"))
                        except CleanroomException:
                            continue
                        if start is not None and created < start:
                            continue
                        if end is not None and created >= end:
                            continue
                        selected.append(item)
                    values = selected
                values.sort(key=lambda item: str(item.get("asset_id") or ""))
                if normalized["sort"] in {"newest", "oldest"}:
                    values.sort(key=lambda item: str(item.get("created_at") or ""),
                                reverse=normalized["sort"] == "newest")
                elif normalized["sort"] == "name":
                    values.sort(key=lambda item: str(item.get("name") or "").casefold())
                total = len(values)
                page = values[offset:offset + limit]
                has_more = offset + len(page) < total
                next_cursor = str(offset + len(page)) if has_more else None
                projected = []
                for item in page:
                    projected_item = copy.deepcopy(item)
                    projected_item.pop("id", None)
                    projected_item["entity_type"] = "asset"
                    projected_item["tag_id"] = list(item.get("tags") or [])
                    projected_item["status"] = "archived" if item.get("archived") else "active"
                    projected.append(projected_item)
                return {
                    "assets": projected,
                    "items": projected,
                    "total": total,
                    "total_known": True,
                    "limit": limit,
                    "offset": offset,
                    "has_more": has_more,
                    "next_cursor": next_cursor,
                    "revision": self._revision,
                    "page": {
                        "limit": limit,
                        "has_more": has_more,
                        "next_cursor": next_cursor,
                        "collection_revision": self._revision,
                    },
                    "applied_filters": {
                        "scope": normalized["scope"],
                        "sort": normalized["sort"],
                        **({"text": normalized["text"]} if normalized["text"] else {}),
                        **filters_map,
                    },
                    "facets": None,
                    "data_status": "complete",
                    "data_gaps": [],
                }
        if limit < 1 or limit > 500:
            raise CleanroomException(status_code=400, code="INVALID_LIMIT", message="limit 必须在 1 到 500 之间")
        if offset < 0:
            raise CleanroomException(status_code=400, code="INVALID_OFFSET", message="offset 不能为负数")
        with self._lock:
            values = [copy.deepcopy(item) for item in self._assets.values()]
            page = values[offset : offset + limit]
            return {
                "assets": page,
                "total": len(values),
                "limit": limit,
                "offset": offset,
                "next_cursor": str(offset + limit) if offset + limit < len(values) else None,
                "revision": self._revision,
                **_truthful_meta(),
            }

    def get_asset(self, asset_id: str) -> Dict[str, Any]:
        # 没有登记的 asset_id 必须明确 404，不伪造一个空详情。
        with self._lock:
            item = self._assets.get(asset_id)
            if item is None:
                _not_found(ASSET_NOT_FOUND, f"素材 {asset_id} 不存在")
            return copy.deepcopy(item)

    def register_asset(
        self,
        asset_id: str,
        *,
        source_ref: Optional[Dict[str, Any]] = None,
        references: Optional[Dict[str, Any]] = None,
        metadata: Optional[Dict[str, Any]] = None,
        payload: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """登记一个由真实上游提交的稳定资产快照。

        该入口只接收调用方已确认的 ID 与来源，不读取本机文件或远程服务；它为
        C8 的统一回收契约提供可回放的最小资产真源。
        """
        asset_id = str(asset_id or "").strip()
        if not asset_id:
            raise CleanroomException(400, "INVALID_REQUEST", "asset_id 不能为空")
        with self._lock:
            if asset_id in self._assets:
                raise CleanroomException(409, ASSET_ALREADY_EXISTS, f"素材 {asset_id} 已登记")
            item: Dict[str, Any] = {
                "id": asset_id,
                "asset_id": asset_id,
                "version": 1,
                "source_ref": copy.deepcopy(source_ref) if isinstance(source_ref, dict) else {},
                "references": copy.deepcopy(references) if isinstance(references, dict) else {},
                "metadata": copy.deepcopy(metadata) if isinstance(metadata, dict) else {},
            }
            if isinstance(payload, dict):
                item.update(copy.deepcopy(payload))
            # 资产 ID 是唯一真源，调用方 payload 不能覆盖它。
            item["id"] = asset_id
            item["asset_id"] = asset_id
            item["version"] = int(item.get("version") or 1)
            self._assets[asset_id] = item
            self._revision += 1
            return {"asset": copy.deepcopy(item), "revision": self._revision, **_truthful_meta()}

    # 便于上层按领域语言调用；两者共享同一实现，不生成第二套状态。
    register = register_asset
    upsert_asset = register_asset

    def _assert_expected_version(self, expected_version: Optional[int]) -> None:
        if expected_version is None:
            return
        if type(expected_version) is not int or expected_version < 1:
            raise CleanroomException(400, "EXPECTED_VERSION_REQUIRED", "资产回收操作必须携带正整数 expected_version")
        if expected_version != self._revision:
            raise CleanroomException(
                409,
                "VERSION_CONFLICT",
                "注册表版本冲突，请重新读取后重试",
                extra={"expected_version": expected_version, "current_version": self._revision},
                expose_extra_fields={"expected_version", "current_version"},
            )

    def _next_recycle_id(self) -> str:
        self._recycle_sequence += 1
        return f"rcy_{self._recycle_sequence:04d}"

    @staticmethod
    def _preserved_subjects(asset: Dict[str, Any]) -> List[Dict[str, str]]:
        """从资产快照提取已存在的稳定引用，不按数组位置生成 ID。"""
        subject: Dict[str, str] = {"asset_id": str(asset.get("asset_id") or asset.get("id") or "")}
        version_id = asset.get("version_id")
        if version_id:
            subject["version_id"] = str(version_id)
        return [subject] if subject["asset_id"] else []

    @staticmethod
    def _recovery_projection(
        *,
        operation: str,
        entry_id: str,
        state: str,
        preserved_subjects: List[Dict[str, str]],
        failed_scopes: Optional[List[str]] = None,
        idempotency_key: Optional[str] = None,
    ) -> Dict[str, Any]:
        return {
            "operation": operation,
            "recycle_entry_id": entry_id,
            "state": state,
            "preserved_subjects": copy.deepcopy(preserved_subjects),
            "failed_scopes": list(failed_scopes or []),
            "idempotency_key": idempotency_key or f"{operation}:{entry_id}",
            "occurred_at": _now_iso(),
        }

    def recycle_entry(self, entry_id: str) -> Dict[str, Any]:
        """读取单个回收条目；未知状态明确 404，不返回空成功。"""
        with self._lock:
            entry = self._recycle_entries.get(entry_id)
            if entry is None:
                raise CleanroomException(404, RECYCLE_ENTRY_NOT_FOUND, "回收站条目不存在")
            return copy.deepcopy(entry)

    def assert_recycle_entry_restorable(self, entry_id: str) -> Dict[str, Any]:
        """恢复前置检查，保证跨服务恢复失败时不先改动另一侧。"""
        with self._lock:
            entry = self._recycle_entries.get(entry_id)
            if entry is None:
                raise CleanroomException(404, RECYCLE_ENTRY_NOT_FOUND, "回收站条目不存在")
            asset_id = str(entry.get("asset_id") or "")
            if not asset_id or not isinstance(entry.get("payload"), dict):
                raise CleanroomException(
                    409,
                    ASSET_RESTORE_PARTIAL_FAILURE,
                    "回收条目快照不完整，无法安全恢复",
                    extra={"partial_failures": [{"entry_id": entry_id, "code": ASSET_RESTORE_PARTIAL_FAILURE}]},
                )
            if asset_id in self._assets:
                raise CleanroomException(409, ASSET_RESTORE_CONFLICT, f"恢复目标资产 {asset_id} 已存在")
            return copy.deepcopy(entry)

    def delete_asset(
        self,
        asset_id: str,
        expected_version: Optional[int] = None,
        *,
        idempotency_key: Optional[str] = None,
    ) -> Dict[str, Any]:
        """删除登记快照并进入可恢复回收站，保留来源与引用。"""
        with self._lock:
            if idempotency_key and idempotency_key in self._idempotent_results:
                return copy.deepcopy(self._idempotent_results[idempotency_key])
            self._assert_expected_version(expected_version)
            if asset_id not in self._assets:
                _not_found(ASSET_NOT_FOUND, f"素材 {asset_id} 不存在")
            item = copy.deepcopy(self._assets[asset_id])
            entry_id = self._next_recycle_id()
            entry = {
                "entry_id": entry_id,
                "kind": "asset",
                "asset_id": asset_id,
                "payload": item,
                "source_ref": copy.deepcopy(item.get("source_ref") or {}),
                "references": copy.deepcopy(item.get("references") or {}),
                "version_id": item.get("version_id"),
                "idempotency_key": idempotency_key or f"recycle:{entry_id}",
                "occurred_at": _now_iso(),
            }
            del self._assets[asset_id]
            self._recycle_entries[entry_id] = entry
            self._revision += 1
            result = {
                "entry_id": entry_id,
                "asset_id": asset_id,
                "deleted": True,
                "recycle_entry_id": entry_id,
                "references": copy.deepcopy(entry["references"]),
                "source_ref": copy.deepcopy(entry["source_ref"]),
                "revision": self._revision,
                "closure_status": "recovery_required",
                "blocker": None,
                "recovery": self._recovery_projection(
                    operation="recycle",
                    entry_id=entry_id,
                    state="recovery_required",
                    preserved_subjects=self._preserved_subjects(item),
                    idempotency_key=idempotency_key or f"recycle:{entry_id}",
                ),
                "next_action": "restore_reference",
                **_truthful_meta(),
            }
            if idempotency_key:
                self._idempotent_results[idempotency_key] = copy.deepcopy(result)
            return result

    def restore_from_recycle(
        self,
        entry_id: str,
        expected_version: Optional[int] = None,
        *,
        idempotency_key: Optional[str] = None,
    ) -> Dict[str, Any]:
        """恢复资产快照；同一条目重复恢复不重复登记。"""
        with self._lock:
            if idempotency_key and idempotency_key in self._idempotent_results:
                return copy.deepcopy(self._idempotent_results[idempotency_key])
            self._assert_expected_version(expected_version)
            entry = self.assert_recycle_entry_restorable(entry_id)
            asset_id = str(entry["asset_id"])
            self._assets[asset_id] = copy.deepcopy(entry["payload"])
            del self._recycle_entries[entry_id]
            self._revision += 1
            result = {
                "entry_id": entry_id,
                "asset_id": asset_id,
                "restored": True,
                "asset": copy.deepcopy(self._assets[asset_id]),
                "references": copy.deepcopy(self._assets[asset_id].get("references") or {}),
                "source_ref": copy.deepcopy(self._assets[asset_id].get("source_ref") or {}),
                "revision": self._revision,
                "closure_status": "closed",
                "blocker": None,
                "recovery": self._recovery_projection(
                    operation="restore",
                    entry_id=entry_id,
                    state="completed",
                    preserved_subjects=self._preserved_subjects(self._assets[asset_id]),
                    idempotency_key=idempotency_key or f"restore:{entry_id}",
                ),
                "next_action": None,
                **_truthful_meta(),
            }
            if idempotency_key:
                self._idempotent_results[idempotency_key] = copy.deepcopy(result)
            return result

    restore_recycle_entry = restore_from_recycle

    def image_versions(self, asset_id: str, *, limit: int = 500) -> Dict[str, Any]:
        if limit < 1 or limit > 500:
            raise CleanroomException(status_code=400, code="INVALID_LIMIT", message="limit 必须在 1 到 500 之间")
        self.get_asset(asset_id)
        return {"versions": [], "asset_id": asset_id, **_truthful_meta()}

    def facets(self) -> Dict[str, Any]:
        return {"kinds": [], "categories": [], "tags": [], "collections": [], "roots": [], **_truthful_meta()}

    def folders(self, root_id: Optional[str] = None) -> Dict[str, Any]:
        return {"folders": [], "root_id": root_id, "revision": self._revision, **_truthful_meta()}

    def preferences(self) -> Dict[str, Any]:
        with self._lock:
            return {"preferences": copy.deepcopy(self._team_preferences), **self._meta()}

    def recycle_bin(self) -> Dict[str, Any]:
        with self._lock:
            items = [copy.deepcopy(item) for item in self._recycle_entries.values()]
            return {
                "items": items,
                "assets": [item for item in items if item.get("kind") == "asset"],
                "projects": [],
                "canvases": [],
                "revision": self._revision,
                **_truthful_meta(),
            }

    def governance_overview(self) -> Dict[str, Any]:
        return {
            "assets": [],
            "projects": [],
            "canvases": [],
            "outbox": {"pending": 0, "failed": 0, "remaining": 0, "replayed": 0},
            "revision": self._revision,
            **_truthful_meta(),
        }

    def templates(self, project_type: Optional[str] = None) -> Dict[str, Any]:
        return {"templates": [], "project_type": project_type, "revision": self._revision, **_truthful_meta()}

    def workspace_job(self, job_id: str) -> Dict[str, Any]:
        _not_found(JOB_NOT_FOUND, f"工作区任务 {job_id} 不存在")

    def assert_feature_id(self, feature_id: str) -> None:
        known = {item["id"] for item in self._features()}
        if feature_id not in known:
            _not_found("FEATURE_NOT_FOUND", f"素材注册表能力 {feature_id} 不存在")


# 单例只保存非敏感、进程内可重建状态。
default_asset_registry_service = AssetRegistryService()


__all__ = [
    "ASSET_ALREADY_EXISTS",
    "ASSET_REGISTRY_NOT_INTEGRATED",
    "ASSET_NOT_FOUND",
    "ASSET_RESTORE_CONFLICT",
    "ASSET_RESTORE_PARTIAL_FAILURE",
    "DATA_STATUS_NOT_INTEGRATED",
    "DATA_STATUS_OK",
    "JOB_NOT_FOUND",
    "normalize_asset_query",
    "parse_utc_datetime",
    "RECYCLE_ENTRY_NOT_FOUND",
    "AssetRegistryService",
    "default_asset_registry_service",
    "_not_integrated",
]
