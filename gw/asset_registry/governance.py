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

"""MVP1-T5 治理建议清单服务（只建议 + 确认记录）。

职责边界（AC5/AC6）：
- 建议输出只读：命名、相似名称、查重（文件哈希/镜头内容哈希/向量近重）与
  受控词表打标建议全部由真实登记数据推导，不虚构、不访问源文件内容；
- 确认显式写入：用户逐条确认后才通过注册表既有写入口落字段，全部携带
  expected_version CAS；确认动作本身以只追加审计记录留痕；
- 系统永不自动删除：任何路径都不会删除资产、关系或源文件。

打标词表遵循 `.local/docs/notes/CINEMATOGRAPHY-METADATA-SPECIFICATION.md`，
代码侧受控枚举取自 `gw.cinematography.schema`。
"""

from __future__ import annotations

import hashlib
import json
import math
import sqlite3
import struct
from typing import Any, Dict, List, Optional, Sequence, Tuple

from gw.asset_registry import repository as repo
from gw.asset_registry.governance_rules import (
    NamingRules,
    find_duplicate_groups,
    group_similar_names,
    validate_name,
)
from gw.asset_registry.service import DATA_STATUS_NOT_INTEGRATED
from gw.core import storage
from gw.core.asset_visibility import asset_visible
from gw.core.errors import CleanroomException

NS_GOVERNANCE = "asset_governance"

# 命名建议规则：与仓库全局命名规范的分隔符/三位版本号口径一致；
# 仅作为建议校验，不用于落盘文件名改写。
DEFAULT_NAMING_RULES = NamingRules(
    prefix=None,
    separator="_",
    version_suffix_pattern=r"_v\d+$",
    suggested_version_suffix="v001",
)

# 向量近重阈值：索引写入前均已归一化，点积即余弦相似度。
NEAR_DUPLICATE_SIMILARITY = 0.98
MAX_VECTOR_PAIR_CANDIDATES = 200
MAX_SUGGESTION_ENTRIES = 200

# 受控词表同义映射：把自由标签归一到 ASC 工业代号（仅建议，不自动改写）。
# 中文标准术语取自摄制元数据规范第 2.1/2.3/2.4/2.7 节。
_TAG_SYNONYMS: Dict[str, str] = {}
for _code, _synonyms in (
    ("EWS", ("极远景", "大远景", "远景", "extreme wide shot")),
    ("WS", ("全景", "全身景", "wide shot", "full shot")),
    ("MS", ("中景", "medium shot")),
    ("MCU", ("中特写", "胸景", "medium close-up", "medium close up")),
    ("CU", ("特写", "close-up", "close up")),
    ("ECU", ("大特写", "微距特写", "extreme close-up")),
    ("STATIC", ("固定镜头", "固定", "static")),
    ("PAN", ("横摇镜头", "横摇", "pan")),
    ("TILT", ("俯仰摇", "俯仰", "tilt")),
    ("DOLLY_IN", ("前推", "推近", "dolly in")),
    ("DOLLY_OUT", ("后拉", "拉远", "dolly out")),
    ("TRUCK_LEFT", ("左横移", "truck left")),
    ("TRUCK_RIGHT", ("右横移", "truck right")),
    ("PEDESTAL_UP", ("升镜头", "升起", "pedestal up")),
    ("PEDESTAL_DOWN", ("降镜头", "降下", "pedestal down")),
    ("ARC", ("弧形环绕", "环绕", "arc")),
    ("WHIP_PAN", ("甩镜头", "甩", "whip pan")),
    ("ULTRA_WIDE", ("超广角", "ultra wide", "ultra-wide")),
    ("WIDE", ("广角", "wide")),
    ("STANDARD", ("标准焦段", "standard")),
    ("PORTRAIT", ("人像焦段", "portrait")),
    ("TELEPHOTO", ("长焦", "远摄", "telephoto")),
    ("DAY", ("日景", "白天", "day")),
    ("NIGHT", ("夜景", "夜晚", "night")),
    ("GOLDEN_HOUR", ("黄金时刻", "golden hour")),
    ("MAGIC_HOUR", ("魔法时刻", "magic hour")),
    ("DAWN", ("黎明", "dawn")),
    ("DUSK", ("黄昏", "dusk")),
):
    for _synonym in _synonyms:
        _TAG_SYNONYMS.setdefault(_synonym.casefold(), _code)


def _blank() -> Dict[str, Any]:
    return {"revision": 1, "confirmations": []}


def state() -> storage.JsonState:
    """治理确认审计的独立命名空间；只追加，不参与注册表 CAS。"""
    return storage.JsonState(NS_GOVERNANCE, _blank)


def _suggestion_id(kind: str, *parts: Any) -> str:
    canonical = json.dumps(
        [kind, *(str(part) for part in parts)],
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return f"gs_{hashlib.sha256(canonical.encode('utf-8')).hexdigest()[:24]}"


def _registry_assets(raw: Dict[str, Any]) -> List[Dict[str, Any]]:
    """可见资产快照，按稳定 asset_id 排序保证建议清单可复现。"""
    assets = []
    for item in raw.get("assets", {}).values():
        record = dict(item)
        if not isinstance(record.get("metadata"), dict):
            record["metadata"] = {}
        if asset_visible(record):
            assets.append(record)
    assets.sort(key=lambda item: str(item.get("asset_id") or ""))
    return assets


def _asset_tag_names(raw: Dict[str, Any], asset: Dict[str, Any]) -> List[str]:
    """把资产登记的 tags 解析为标签名；兼容两种历史形态：
    import 路径直接写标签名字符串，add_tags 路径写 tag_id 引用。"""
    tags = raw.get("tags", {})
    names = []
    for tag_ref in asset.get("tags") or []:
        if not isinstance(tag_ref, str) or not tag_ref.strip():
            continue
        tag = tags.get(tag_ref)
        if isinstance(tag, dict) and str(tag.get("name") or "").strip():
            names.append(str(tag["name"]).strip())
        else:
            names.append(tag_ref.strip())
    return names


def _find_by_display_path(
    assets: Sequence[Dict[str, Any]], path: str
) -> Optional[Dict[str, Any]]:
    for asset in assets:
        current = str(asset.get("display_path") or asset.get("name") or "").strip()
        if current == path:
            return asset
    return None


def _asset_file_records(
    assets: Sequence[Dict[str, Any]],
) -> List[Tuple[str, int, str]]:
    """提取带真实 sha256 与体积的文件记录；缺哈希的资产不参与文件查重建议。"""
    records: List[Tuple[str, int, str]] = []
    for asset in assets:
        digest = str(asset.get("sha256") or "").strip()
        path = str(asset.get("display_path") or asset.get("name") or "").strip()
        size = asset.get("size_bytes")
        if not digest or not path:
            continue
        if isinstance(size, bool) or not isinstance(size, int) or size < 0:
            size = 0
        records.append((path, size, digest))
    return records


def _display_paths(assets: Sequence[Dict[str, Any]]) -> List[str]:
    paths = []
    for asset in assets:
        path = str(asset.get("display_path") or asset.get("name") or "").strip()
        if path:
            paths.append(path)
    return paths


def _tagging_suggestions(
    raw: Dict[str, Any], assets: Sequence[Dict[str, Any]]
) -> List[Dict[str, Any]]:
    """把自由标签映射到受控词表；已具备对应代号的资产不再重复建议。"""
    suggestions: List[Dict[str, Any]] = []
    for asset in assets:
        asset_id = str(asset.get("asset_id") or asset.get("id") or "").strip()
        if not asset_id:
            continue
        names = _asset_tag_names(raw, asset)
        existing = {name.casefold() for name in names}
        proposed: Dict[str, str] = {}
        for name in names:
            code = _TAG_SYNONYMS.get(name.casefold())
            if (
                code is None
                or code.casefold() in existing
                or code in proposed.values()
            ):
                continue
            proposed[name.casefold()] = code
        if not proposed:
            continue
        suggestions.append(
            {
                "suggestion_id": _suggestion_id(
                    "tagging", asset_id, *sorted(proposed.values())
                ),
                "kind": "tagging",
                "asset_id": asset_id,
                "suggested_tags": sorted(set(proposed.values())),
                "source_tags": sorted(proposed.keys()),
            }
        )
        if len(suggestions) >= MAX_SUGGESTION_ENTRIES:
            break
    return suggestions


def _vector_near_duplicates(engine: Any) -> List[Dict[str, Any]]:
    """对索引内单位向量做近重配对；只读访问，不修改引擎状态。

    引擎未暴露向量导出公共接口，这里在引擎已加载扩展的连接上做只读
    SELECT（同包受限使用）；向量在写入前已归一化，点积即相似度。
    """
    try:
        rows = engine._db.execute(
            "SELECT d.asset_id AS asset_id, v.embedding AS embedding "
            "FROM c18_vectors AS v JOIN c18_documents AS d ON d.row_id = v.rowid "
            "WHERE d.vector_hash IS NOT NULL"
        ).fetchall()
    except sqlite3.Error:
        return []
    vectors: List[Tuple[str, Tuple[float, ...]]] = []
    for row in rows:
        blob = row["embedding"]
        if not isinstance(blob, (bytes, bytearray)) or len(blob) % 4 != 0:
            continue
        values = struct.unpack(f"<{len(blob) // 4}f", bytes(blob))
        vectors.append((str(row["asset_id"]), values))
    pairs: List[Dict[str, Any]] = []
    for left in range(len(vectors)):
        left_id, left_vector = vectors[left]
        for right in range(left + 1, len(vectors)):
            right_id, right_vector = vectors[right]
            if len(left_vector) != len(right_vector):
                continue
            similarity = math.fsum(
                a * b for a, b in zip(left_vector, right_vector, strict=True)
            )
            if similarity >= NEAR_DUPLICATE_SIMILARITY:
                pairs.append(
                    {
                        "left_asset_id": left_id,
                        "right_asset_id": right_id,
                        "similarity": round(similarity, 6),
                    }
                )
                if len(pairs) >= MAX_VECTOR_PAIR_CANDIDATES:
                    return pairs
    return pairs


def _load_index_snapshot() -> Dict[str, Any]:
    """只读加载检索索引的镜头哈希组与向量近重候选；索引缺失/降级如实标注。"""
    snapshot: Dict[str, Any] = {
        "shot_duplicate_groups": [],
        "vector_duplicate_pairs": [],
        "index_available": False,
        "vector_available": False,
    }
    db_path = storage.data_root() / repo.ASSET_SEARCH_INDEX_FILENAME
    if not db_path.is_file():
        return snapshot
    from gw.core.sqlite_vec_engine import SQLiteVecEngine

    with SQLiteVecEngine(db_path) as engine:
        snapshot["index_available"] = True
        snapshot["vector_available"] = bool(engine.vector_enabled)
        snapshot["shot_duplicate_groups"] = [
            {
                "content_hash": group.content_hash,
                "members": [
                    {
                        "asset_id": member.asset_id,
                        "shot_index": member.shot_index,
                        "start_sec": member.start_sec,
                        "end_sec": member.end_sec,
                    }
                    for member in group.members
                ],
            }
            for group in engine.shot_duplicate_candidates(limit=50)
        ]
        if engine.vector_enabled:
            snapshot["vector_duplicate_pairs"] = _vector_near_duplicates(engine)
    return snapshot


def _duplicate_related_pairs(raw: Dict[str, Any]) -> set:
    """已登记 duplicate 关系的资产对集合；用于抑制已确认的查重建议。"""
    pairs: set = set()
    for relation in raw.get("relations", []):
        if str(relation.get("kind") or "") != "duplicate":
            continue
        left = str(relation.get("from_asset_id") or "")
        right = str(relation.get("right_asset_id") or relation.get("to_asset_id") or "")
        if left and right:
            pairs.add(frozenset((left, right)))
    return pairs


def build_suggestions() -> Dict[str, Any]:
    """生成完整建议清单；只读，不修改注册表或源文件。"""
    raw = repo.state().read()
    assets = _registry_assets(raw)
    related_pairs = _duplicate_related_pairs(raw)

    paths = _display_paths(assets)
    naming: List[Dict[str, Any]] = []
    for asset in assets:
        path = str(asset.get("display_path") or asset.get("name") or "").strip()
        if not path:
            continue
        basename = path.replace("\\", "/").rsplit("/", 1)[-1]
        assessment = validate_name(basename, DEFAULT_NAMING_RULES)
        if not assessment.violations:
            continue
        naming.append(
            {
                "suggestion_id": _suggestion_id("naming", assessment.name),
                "kind": "naming",
                "asset_id": str(asset.get("asset_id") or ""),
                "name": assessment.name,
                "violations": [
                    {"code": item.code, "message": item.message}
                    for item in assessment.violations
                ],
                "suggested_name": assessment.suggested_name,
            }
        )
        if len(naming) >= MAX_SUGGESTION_ENTRIES:
            break

    similar_names = [
        {
            "suggestion_id": _suggestion_id(
                "similar_names", group.normalized_name, len(group.members)
            ),
            "kind": "similar_names",
            "normalized_name": group.normalized_name,
            "members": list(group.members),
            "asset_ids": [
                str(_find_by_display_path(assets, path).get("asset_id") or "")
                if _find_by_display_path(assets, path) is not None
                else ""
                for path in group.members
            ],
        }
        for group in group_similar_names(paths)
    ]

    file_records = _asset_file_records(assets)
    duplicate_files = []
    for group in find_duplicate_groups(file_records):
        asset_ids = [
            str(_find_by_display_path(assets, member.path).get("asset_id") or "")
            if _find_by_display_path(assets, member.path) is not None
            else ""
            for member in group.members
        ]
        located = [item for item in asset_ids if item]
        if len(located) >= 2 and frozenset(located[:2]) in related_pairs:
            continue
        duplicate_files.append(
            {
                "suggestion_id": _suggestion_id("duplicate_files", group.content_hash),
                "kind": "duplicate_files",
                "content_hash": group.content_hash,
                "members": [
                    {"path": member.path, "size_bytes": member.size}
                    for member in group.members
                ],
                "asset_ids": asset_ids,
            }
        )

    index_snapshot = _load_index_snapshot()
    duplicate_shots = []
    for item in index_snapshot["shot_duplicate_groups"]:
        members = item.get("members") or []
        pair = frozenset(
            str(member.get("asset_id") or "") for member in members[:2]
        )
        if len(pair) == 2 and pair in related_pairs:
            continue
        duplicate_shots.append(
            {
                "suggestion_id": _suggestion_id("duplicate_shots", item["content_hash"]),
                "kind": "duplicate_shots",
                **item,
            }
        )
    vector_duplicates = []
    for item in index_snapshot["vector_duplicate_pairs"]:
        pair = frozenset(
            (str(item.get("left_asset_id") or ""), str(item.get("right_asset_id") or ""))
        )
        if pair in related_pairs:
            continue
        vector_duplicates.append(
            {
                "suggestion_id": _suggestion_id(
                    "vector_duplicates", item["left_asset_id"], item["right_asset_id"]
                ),
                "kind": "vector_duplicates",
                **item,
            }
        )
    tagging = _tagging_suggestions(raw, assets)

    suggestions = {
        "naming": naming,
        "similar_names": similar_names,
        "duplicate_files": duplicate_files,
        "duplicate_shots": duplicate_shots,
        "vector_duplicates": vector_duplicates,
        "tagging": tagging,
    }
    counts = {key: len(value) for key, value in suggestions.items()}
    data_gaps: List[str] = []
    if not index_snapshot["index_available"]:
        data_gaps.append("search_index_unavailable")
    elif not index_snapshot["vector_available"]:
        data_gaps.append("vector_track_degraded")
    return {
        "suggestions": suggestions,
        "counts": counts,
        "revision": int(raw.get("revision", 1)),
        "data_status": repo.DATA_STATUS_OK if not data_gaps else DATA_STATUS_NOT_INTEGRATED,
        "data_gaps": data_gaps,
    }


def find_suggestion(suggestion_id: str) -> Dict[str, Any]:
    """按稳定 suggestion_id 定位建议；不存在时抛受控错误。"""
    if not isinstance(suggestion_id, str) or not suggestion_id.strip():
        raise CleanroomException(400, "INVALID_REQUEST", "suggestion_id 不能为空")
    result = build_suggestions()
    for kind, entries in result["suggestions"].items():
        for entry in entries:
            if entry.get("suggestion_id") == suggestion_id:
                return {"kind": kind, **entry}
    raise CleanroomException(404, "SUGGESTION_NOT_FOUND", "治理建议不存在或已失效")


def _require_asset_ids(entry: Dict[str, Any], minimum: int = 2) -> List[str]:
    asset_ids = [str(item) for item in entry.get("asset_ids") or [] if str(item)]
    if len(asset_ids) < minimum:
        raise CleanroomException(400, "INVALID_REQUEST", "建议成员缺少可定位的资产")
    return asset_ids


def _apply_confirmation(entry: Dict[str, Any], expected_version: int) -> Dict[str, Any]:
    """把单条确认写入注册表既有写入口；不改源文件、不删除任何数据。"""
    kind = entry["kind"]
    if kind == "tagging":
        tags = [str(item) for item in entry.get("suggested_tags") or []]
        if not tags:
            raise CleanroomException(400, "INVALID_REQUEST", "打标建议缺少受控标签")
        asset_id = str(entry.get("asset_id") or "")
        if not asset_id:
            raise CleanroomException(400, "INVALID_REQUEST", "打标建议缺少资产")
        return repo.add_tags([asset_id], tags, expected_version)
    if kind == "duplicate_files":
        left, right = _require_asset_ids(entry)[:2]
        return repo.create_relation(left, right, "duplicate", expected_version)
    if kind == "duplicate_shots":
        members = entry.get("members") or []
        if len(members) < 2:
            raise CleanroomException(400, "INVALID_REQUEST", "镜头查重建议成员不足")
        return repo.create_relation(
            str(members[0]["asset_id"]),
            str(members[1]["asset_id"]),
            "duplicate",
            expected_version,
        )
    if kind == "vector_duplicates":
        left = str(entry.get("left_asset_id") or "")
        right = str(entry.get("right_asset_id") or "")
        if not left or not right:
            raise CleanroomException(400, "INVALID_REQUEST", "向量近重建议成员不完整")
        return repo.create_relation(left, right, "duplicate", expected_version)
    if kind == "naming":
        suggested = str(entry.get("suggested_name") or "").strip()
        asset_id = str(entry.get("asset_id") or "")
        if not suggested or not asset_id:
            raise CleanroomException(400, "INVALID_REQUEST", "命名建议不完整")
        return repo.update_asset(
            asset_id, {"name": suggested, "expected_version": expected_version}
        )
    if kind == "similar_names":
        asset_ids = _require_asset_ids(entry)
        canonical = str(entry.get("normalized_name") or "").strip()
        if not canonical:
            raise CleanroomException(400, "INVALID_REQUEST", "相似名建议缺少规范名")
        return repo.update_asset(
            asset_ids[0], {"name": canonical, "expected_version": expected_version}
        )
    raise CleanroomException(400, "INVALID_REQUEST", "不支持的治理建议类型")


def confirm_suggestion(
    suggestion_id: str,
    expected_version: int,
    *,
    operator: Optional[str] = None,
) -> Dict[str, Any]:
    """确认单条建议：写注册表字段并追加确认审计记录。"""
    entry = find_suggestion(suggestion_id)
    result = _apply_confirmation(entry, expected_version)
    record = {
        "suggestion_id": suggestion_id,
        "kind": entry["kind"],
        "confirmed_at": storage.now_iso(),
        "operator": operator,
        "revision_after": result.get("revision"),
    }

    def mutate(raw: Dict[str, Any]) -> Any:
        raw.setdefault("confirmations", []).append(record)
        raw["revision"] = int(raw.get("revision", 1)) + 1
        return raw

    state().mutate(mutate)
    return {
        "confirmation": record,
        "revision": result.get("revision"),
        "data_status": repo.DATA_STATUS_OK,
    }


def list_confirmations() -> Dict[str, Any]:
    """只读返回确认审计记录。"""
    raw = state().read()
    return {
        "confirmations": list(raw.get("confirmations", [])),
        "revision": int(raw.get("revision", 1)),
        "data_status": repo.DATA_STATUS_OK,
    }


__all__ = [
    "DEFAULT_NAMING_RULES",
    "NEAR_DUPLICATE_SIMILARITY",
    "NS_GOVERNANCE",
    "build_suggestions",
    "confirm_suggestion",
    "find_suggestion",
    "list_confirmations",
    "state",
]
