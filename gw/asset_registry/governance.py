# -*- coding: utf-8 -*-
# Copyright 2026 qinxuedong (Gods-Workbench Authors)
# SPDX-License-Identifier: FSL-1.1-Apache-2.0
#
# Licensed under the Functional Source License, Version 1.1, Apache 2.0
# Future License (the "License"); you may not use this file except in
# compliance with the License. You may obtain a copy of the License at
#     https://fsl.software/
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
import os
from collections import Counter, OrderedDict
from concurrent.futures import Future
from copy import deepcopy
from difflib import SequenceMatcher
from pathlib import Path
import sqlite3
import struct
import threading
import unicodedata
from typing import Any, Dict, List, Optional, Sequence, Tuple

from gw.asset_registry import dedup_confirm, repository as repo
from gw.asset_registry.governance_rules import (
    NamingRules,
    SimilarNameGroup,
    find_duplicate_groups,
    normalize_filename,
    validate_name,
)
from gw.asset_registry.service import DATA_STATUS_NOT_INTEGRATED
from gw.core import storage
from gw.core.asset_visibility import asset_visible, current_asset_owner, current_auth_context
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
_GOVERNANCE_CACHE_LIMIT = 8
_GOVERNANCE_CACHE: OrderedDict[Tuple[int, str, str], Dict[str, Any]] = OrderedDict()
_GOVERNANCE_INFLIGHT: Dict[Tuple[int, str, str], Future] = {}
_GOVERNANCE_CACHE_LOCK = threading.Lock()

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


def _group_similar_display_paths(names: Sequence[str], similarity_threshold: float = 0.9) -> Tuple[SimilarNameGroup, ...]:
    """以四元组重叠的必要条件剪枝，并保持原 SequenceMatcher 判定结果。"""
    candidates = []
    gram_frequency: Counter[str] = Counter()
    repeated_grams = set()
    for value in names:
        if not value:
            continue
        normalized = normalize_filename(value)
        if normalized:
            gram_values = [normalized[offset:offset + 4] for offset in range(max(0, len(normalized) - 3))]
            grams = set(gram_values)
            gram_counts = Counter(gram_values) if len(grams) != len(gram_values) else None
            candidates.append((value, normalized, grams, gram_counts))
            if gram_counts is not None:
                repeated_grams.update(gram for gram, count in gram_counts.items() if count > 1)
            gram_frequency.update(grams)
    if len(candidates) < 2:
        return ()

    parents = list(range(len(candidates)))

    def find(index: int) -> int:
        while parents[index] != index:
            parents[index] = parents[parents[index]]
            index = parents[index]
        return index

    def union(left: int, right: int) -> None:
        left_root = find(left)
        right_root = find(right)
        if left_root != right_root:
            parents[max(left_root, right_root)] = min(left_root, right_root)

    frequent_grams = {gram for gram, frequency in gram_frequency.items() if frequency > 8}
    frequent_gram_order = sorted(frequent_grams, key=gram_frequency.__getitem__, reverse=True)

    prior_by_length: Dict[int, List[int]] = {}
    prior_length_masks: Dict[int, int] = {}
    prior_gram_masks_by_length: Dict[int, Dict[str, int]] = {}
    prior_count_masks_by_length: Dict[int, Dict[str, List[int]]] = {}

    def add_count_mask(bit_planes: List[int], mask: int) -> None:
        bit_index = 0
        while mask:
            if bit_index == len(bit_planes):
                bit_planes.append(mask)
                break
            carry = bit_planes[bit_index] & mask
            bit_planes[bit_index] ^= mask
            mask = carry
            bit_index += 1

    def mask_at_least(bit_planes: Sequence[int], threshold: int, valid_mask: int) -> int:
        greater = 0
        equal = valid_mask
        for bit_index in range(max(len(bit_planes), threshold.bit_length()) - 1, -1, -1):
            plane = bit_planes[bit_index] if bit_index < len(bit_planes) else 0
            if threshold & (1 << bit_index):
                equal &= plane
            else:
                greater |= equal & plane
                equal &= ~plane
        return greater | equal

    for index, (_, normalized, grams, gram_counts) in enumerate(candidates):
        length = len(normalized)
        lower = max(1, math.ceil(length * similarity_threshold / (2 - similarity_threshold)))
        upper = math.floor(length * (2 - similarity_threshold) / similarity_threshold)
        possible_lengths = [n for n in prior_by_length if lower <= n <= upper]
        # q=4：一个匹配块至少贡献 len(block)-3 个四元组；块数最多为两串未匹配
        # 字符数之和 + 1，据此得到不漏报的必要重叠下界。
        required_by_length = {
            other_length: max(
                0,
                math.ceil(
                    (3.5 * similarity_threshold - 3) * (length + other_length) - 3 - 1e-9
                ),
            )
            for other_length in possible_lengths
        }
        if required_by_length:
            minimum_required = min(required_by_length.values())
            common_grams = grams & frequent_grams
            omitted_common = set()
            common_upper_bound = 0
            omission_budget = minimum_required // 2
            for gram in frequent_gram_order:
                if gram not in common_grams:
                    continue
                count = gram_counts[gram] if gram_counts is not None else 1
                if common_upper_bound + count <= omission_budget:
                    omitted_common.add(gram)
                    common_upper_bound += count

            matcher: Optional[SequenceMatcher] = None
            for other_length, required in required_by_length.items():
                if required == 0:
                    continue
                adjusted_required = required - common_upper_bound
                valid_mask = prior_length_masks.get(other_length, 0)
                if adjusted_required <= 0:
                    candidate_mask = valid_mask
                else:
                    bit_planes: List[int] = []
                    postings = prior_gram_masks_by_length.get(other_length, {})
                    count_postings = prior_count_masks_by_length.get(other_length, {})
                    for gram in grams:
                        if gram in omitted_common:
                            continue
                        query_count = gram_counts[gram] if gram_counts is not None else 1
                        if gram in repeated_grams and query_count > 1:
                            levels = count_postings.get(gram, ())
                            masks = levels[:query_count]
                        else:
                            mask = postings.get(gram, 0)
                            masks = (mask,) if mask else ()
                        for mask in masks:
                            add_count_mask(bit_planes, mask)
                    candidate_mask = mask_at_least(bit_planes, adjusted_required, valid_mask)

                while candidate_mask:
                    bit = candidate_mask & -candidate_mask
                    candidate_mask ^= bit
                    other = bit.bit_length() - 1
                    if omitted_common:
                        other_grams = candidates[other][2]
                        other_counts = candidates[other][3]
                        shared = sum(
                            min(
                                gram_counts[gram] if gram_counts is not None else 1,
                                other_counts[gram] if other_counts is not None else 1,
                            )
                            for gram in grams & other_grams
                        )
                        if shared < required:
                            continue

                    left_name = candidates[other][1]
                    if left_name == normalized:
                        union(other, index)
                        continue
                    if matcher is None:
                        matcher = SequenceMatcher(None, left_name, normalized, autojunk=False)
                    else:
                        matcher.set_seq1(left_name)
                    if matcher.quick_ratio() >= similarity_threshold and matcher.ratio() >= similarity_threshold:
                        union(other, index)

            # 极短名称没有足够四元组，按长度过滤直接比较。
            for other_length, required in required_by_length.items():
                if required == 0:
                    for other in prior_by_length[other_length]:
                        left_name = candidates[other][1]
                        if left_name == normalized:
                            union(other, index)
                            continue
                        if matcher is None:
                            matcher = SequenceMatcher(None, left_name, normalized, autojunk=False)
                        else:
                            matcher.set_seq1(left_name)
                        if matcher.quick_ratio() >= similarity_threshold and matcher.ratio() >= similarity_threshold:
                            union(other, index)

        prior_by_length.setdefault(length, []).append(index)
        candidate_bit = 1 << index
        prior_length_masks[length] = prior_length_masks.get(length, 0) | candidate_bit
        gram_masks = prior_gram_masks_by_length.setdefault(length, {})
        count_masks = prior_count_masks_by_length.setdefault(length, {})
        for gram in grams:
            gram_masks[gram] = gram_masks.get(gram, 0) | candidate_bit
            if gram in repeated_grams:
                count = gram_counts[gram] if gram_counts is not None else 1
                levels = count_masks.setdefault(gram, [])
                if len(levels) < count:
                    levels.extend([0] * (count - len(levels)))
                for level in range(count):
                    levels[level] |= candidate_bit

    grouped: Dict[int, List[Tuple[str, str]]] = {}
    for index, (value, normalized, _, _) in enumerate(candidates):
        grouped.setdefault(find(index), []).append((value, normalized))
    result = []
    for members in grouped.values():
        if len(members) < 2:
            continue
        normalized_name = min(item[1] for item in members)
        paths = tuple(sorted((item[0] for item in members), key=lambda value: (unicodedata.normalize("NFKC", value).casefold(), value)))
        result.append(SimilarNameGroup(normalized_name, paths))
    return tuple(sorted(result, key=lambda group: (group.normalized_name, group.members)))


def _basename(path: str) -> str:
    return path.replace("\\", "/").rsplit("/", 1)[-1]


def _naming_subject(asset: Dict[str, Any]) -> str:
    """命名建议对照的字符串：有登记路径时用文件名，否则用逻辑名。"""
    path = str(asset.get("display_path") or "").strip()
    if path:
        return _basename(path)
    return str(asset.get("name") or "").strip()


def _naming_already_resolved(asset: Dict[str, Any], suggested_name: Optional[str]) -> bool:
    """逻辑名已合规，或已经等于建议名时，不再重复提出命名建议。

    确认只写入 name，不改 display_path，也不改源文件。若仍只看路径文件名，
    同一条建议会在确认后无限再生。
    """
    logical = str(asset.get("name") or "").strip()
    if not logical:
        return False
    if suggested_name and logical == suggested_name:
        return True
    return not validate_name(logical, DEFAULT_NAMING_RULES).violations


def _distinct_member_asset_ids(members: Sequence[Any]) -> List[str]:
    """按出现顺序折叠镜头成员，只保留不同 asset_id。"""
    asset_ids: List[str] = []
    for member in members:
        if not isinstance(member, dict):
            continue
        asset_id = str(member.get("asset_id") or "").strip()
        if asset_id and asset_id not in asset_ids:
            asset_ids.append(asset_id)
    return asset_ids


def _reject_self_relation(left: str, right: str) -> None:
    """查重确认不得登记 from=to。"""
    if str(left).strip() and str(left).strip() == str(right).strip():
        raise CleanroomException(400, "INVALID_REQUEST", "查重建议不能关联同一资产")


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
    """通过 sqlite-vec 最近邻候选找近重，避免 Python 端枚举所有向量对。"""
    try:
        rows = engine._db.execute(
            "SELECT v.rowid AS vector_rowid, d.asset_id AS asset_id, v.embedding AS embedding "
            "FROM c18_vectors AS v JOIN c18_documents AS d ON d.row_id = v.rowid "
            "WHERE d.vector_hash IS NOT NULL ORDER BY v.rowid"
        ).fetchall()
    except sqlite3.Error:
        return []
    vectors: List[Tuple[int, str, Tuple[float, ...], bytes]] = []
    for row in rows:
        blob = row["embedding"]
        if not isinstance(blob, (bytes, bytearray)) or len(blob) % 4 != 0:
            continue
        values = struct.unpack(f"<{len(blob) // 4}f", bytes(blob))
        vectors.append((int(row["vector_rowid"]), str(row["asset_id"]), values, bytes(blob)))

    row_to_index = {rowid: index for index, (rowid, _, _, _) in enumerate(vectors)}
    pairs: List[Dict[str, Any]] = []
    # 只需保留输出上限内的靠前候选。若当前还未收满 200 对，先前索引最多已有
    # 199 个符合对；加上自身后，最近 401 条足以覆盖当前可输出的 200 个后续邻居。
    neighbor_limit = min(len(vectors), MAX_VECTOR_PAIR_CANDIDATES * 2 + 1)
    for left, (_, left_id, left_vector, query_blob) in enumerate(vectors):
        try:
            nearest = engine._db.execute(
                "SELECT rowid, distance FROM c18_vectors "
                "WHERE embedding MATCH ? ORDER BY distance ASC LIMIT ?",
                (query_blob, neighbor_limit),
            ).fetchall()
        except (sqlite3.Error, OverflowError, ValueError):
            # 索引扩展在单次查询故障时保留旧的确定性计算行为。
            nearest = []
            candidates = range(left + 1, len(vectors))
        else:
            candidates = sorted(
                {
                    row_to_index[int(row["rowid"])]
                    for row in nearest
                    if int(row["rowid"]) in row_to_index
                    and row_to_index[int(row["rowid"])] > left
                }
            )
        for right in candidates:
            _, right_id, right_vector, _ = vectors[right]
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


def _duplicate_file_suggestions(
    raw: Dict[str, Any],
    assets: Sequence[Dict[str, Any]],
    related_pairs: set,
) -> List[Dict[str, Any]]:
    """构造文件查重建议：严格区分完整 SHA 已确认(confirmed)与疑似(suspected)。

    规则（QIN-619 AC1~AC5）：
    - 只有全部成员拥有「已验真且文件版本匹配」的完整 SHA-256 且摘要相等时标为 ``confirmed``；
    - ``sampled``、未知/缺失 ``hash_mode``、缓存失效或仅同 ``size_bytes`` 最多标为 ``suspected``；
    - 若两个文件都已算出完整 SHA 且不相等，绝不因相同 ``sampled`` 或相同 ``size_bytes`` 共组。
    """
    removed_ids = repo._removed_root_ids()
    records: List[Dict[str, Any]] = []
    for asset in assets:
        if (
            asset.get("archived")
            or repo._hidden_builtin(asset)
            or asset.get("root_availability") == "removed"
            or (removed_ids and repo._is_removed_asset(asset, removed_ids))
        ):
            continue
        asset_id = str(asset.get("asset_id") or asset.get("id") or "").strip()
        path_label = str(asset.get("display_path") or asset.get("name") or "").strip()
        if not asset_id or not path_label:
            continue
        raw_size = asset.get("size_bytes")
        size = (
            None
            if isinstance(raw_size, bool) or not isinstance(raw_size, int) or raw_size < 0
            else int(raw_size)
        )
        cache = asset.get("full_sha256_cache")
        full_sha = None
        if cache is not None and dedup_confirm.cache_matches_asset_and_stat(cache, asset):
            recorded_mtime = asset.get("mtime_ns")
            cached_mtime = cache.get("mtime_ns") if isinstance(cache, dict) else None
            if (
                isinstance(recorded_mtime, int)
                and not isinstance(recorded_mtime, bool)
                and isinstance(cached_mtime, int)
                and recorded_mtime != cached_mtime
            ):
                full_sha = None
            else:
                full_sha = dedup_confirm.normalize_sha256_hex(cache.get("sha256"))
        if not full_sha:
            metadata = asset.get("metadata")
            collect_sha = (
                dedup_confirm.normalize_sha256_hex(metadata.get("collect_sha256"))
                if isinstance(metadata, dict)
                else ""
            )
            is_collect_inbox = (
                str(asset.get("display_path") or "").startswith("registry_uploads/collect_inbox/")
                or str(asset.get("stored_name") or "").startswith("registry_uploads/collect_inbox/")
            )
            if collect_sha and is_collect_inbox:
                full_sha = collect_sha
        # GET 仅认快照内的完整哈希证据；普通文件变化由显式后台核验收敛。
        effective_size = size
        legacy_sha = dedup_confirm.normalize_sha256_hex(asset.get("sha256"))
        hash_mode = str(asset.get("hash_mode") or "").strip().lower()
        records.append(
            {
                "asset_id": asset_id,
                "path": path_label,
                "path_key": unicodedata.normalize("NFKC", path_label.replace("\\", "/")).casefold(),
                "size_bytes": effective_size if effective_size is not None else 0,
                "has_valid_size": effective_size is not None,
                "full_sha": full_sha,
                "legacy_sha": legacy_sha,
                "hash_mode": hash_mode,
            }
        )

    suggestions: List[Dict[str, Any]] = []
    covered_pair_sets: set[frozenset[str]] = set()

    def _pair_already_related(ids: Sequence[str]) -> bool:
        located = [item for item in ids if item]
        return len(located) >= 2 and frozenset(located[:2]) in related_pairs

    def _sort_members(items: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
        return sorted(
            items,
            key=lambda item: (
                unicodedata.normalize("NFKC", item["path"]).casefold(),
                item["path"],
                item["size_bytes"],
                item["asset_id"],
            ),
        )

    # 1) 已确认组：按已验真完整 SHA-256 聚合
    by_full: Dict[str, List[Dict[str, Any]]] = {}
    for rec in records:
        if rec["full_sha"]:
            by_full.setdefault(rec["full_sha"], []).append(rec)
    for digest in sorted(by_full):
        members = _sort_members(by_full[digest])
        if len({m["path_key"] for m in members}) < 2:
            continue
        asset_ids = [m["asset_id"] for m in members]
        if _pair_already_related(asset_ids):
            covered_pair_sets.add(frozenset(asset_ids))
            continue
        covered_pair_sets.add(frozenset(asset_ids))
        suggestions.append(
            {
                "suggestion_id": _suggestion_id("duplicate_files", digest),
                "kind": "duplicate_files",
                "content_hash": digest,
                "dedup_status": "confirmed",
                "match_basis": "full_sha256",
                "size_bytes": members[0]["size_bytes"],
                "verified_count": len(members),
                "member_count": len(members),
                "members": [
                    {
                        "asset_id": m["asset_id"],
                        "path": m["path"],
                        "size_bytes": m["size_bytes"],
                        "verified": True,
                    }
                    for m in members
                ],
                "asset_ids": asset_ids,
            }
        )

    # 2) 疑似组 A：采样哈希 / 未本地验真历史 sha256（绝不允许完整 SHA 不等的成员共组）
    by_legacy: Dict[str, List[Dict[str, Any]]] = {}
    for rec in records:
        if rec["legacy_sha"]:
            by_legacy.setdefault(rec["legacy_sha"], []).append(rec)
    for digest in sorted(by_legacy):
        bucket = _sort_members(by_legacy[digest])
        if len({m["path_key"] for m in bucket}) < 2:
            continue
        pending_members = [m for m in bucket if not m["full_sha"]]
        full_hashes_in_bucket = {m["full_sha"] for m in bucket if m["full_sha"]}
        # 若桶内全部成员都有完整 SHA（无论相等与否）：
        # 相等的已在第 1 步输出为 confirmed；不等的证明内容不同，不再保留为疑似。
        if not pending_members:
            continue
        # 若桶内存在多种不同的完整 SHA 且还有未算完整 SHA 的成员，只把未算完者单独或与单一代表聚合，
        # 避免把已知内容不同的两个已验证文件混在同一条建议里。
        if len(full_hashes_in_bucket) > 1:
            candidate_groups = [pending_members]
        else:
            candidate_groups = [bucket]
        for group_members in candidate_groups:
            if len({m["path_key"] for m in group_members}) < 2:
                continue
            asset_ids = [m["asset_id"] for m in group_members]
            pair_key = frozenset(asset_ids)
            if pair_key in covered_pair_sets or _pair_already_related(asset_ids):
                continue
            covered_pair_sets.add(pair_key)
            has_sampled = any(m["hash_mode"] == "sampled" for m in group_members)
            basis = "sampled_sha256" if has_sampled else "unverified_legacy"
            suggestions.append(
                {
                    "suggestion_id": _suggestion_id("duplicate_files", "suspected", basis, digest),
                    "kind": "duplicate_files",
                    "content_hash": digest,
                    "dedup_status": "suspected",
                    "match_basis": basis,
                    "size_bytes": group_members[0]["size_bytes"],
                    "verified_count": sum(1 for m in group_members if m["full_sha"]),
                    "member_count": len(group_members),
                    "members": [
                        {
                            "asset_id": m["asset_id"],
                            "path": m["path"],
                            "size_bytes": m["size_bytes"],
                            "verified": bool(m["full_sha"]),
                        }
                        for m in group_members
                    ],
                    "asset_ids": asset_ids,
                }
            )

    # 3) 疑似组 B：同 size_bytes 候选（尚缺完整 SHA 确认者，避免漏掉新加入的同 size 文件）
    by_size: Dict[int, List[Dict[str, Any]]] = {}
    for rec in records:
        if rec["has_valid_size"]:
            by_size.setdefault(rec["size_bytes"], []).append(rec)
    for size in sorted(by_size):
        bucket = _sort_members(by_size[size])
        if len({m["path_key"] for m in bucket}) < 2:
            continue
        pending_members = [m for m in bucket if not m["full_sha"]]
        if not pending_members:
            # 桶内全部成员均已完成完整 SHA 计算：相等者已 confirmed，不等者已确认不重复
            continue
        # 仅当桶内尚有成员未算完完整 SHA 时提出 size 候选；
        # 若已有不同已知 full_sha，与待算成员拆组，避免把已知内容不同的两个文件共组。
        distinct_full = sorted({m["full_sha"] for m in bucket if m["full_sha"]})
        if len(distinct_full) > 1:
            subgroups = [pending_members] if len({m["path_key"] for m in pending_members}) >= 2 else []
            if not subgroups and len(pending_members) == 1:
                subgroups = [bucket]
        else:
            subgroups = [bucket]
        for group_members in subgroups:
            if len({m["path_key"] for m in group_members}) < 2:
                continue
            asset_ids = [m["asset_id"] for m in group_members]
            pair_key = frozenset(asset_ids)
            if pair_key in covered_pair_sets or _pair_already_related(asset_ids):
                continue
            covered_pair_sets.add(pair_key)
            suggestions.append(
                {
                    "suggestion_id": _suggestion_id("duplicate_files", "size", size, *asset_ids),
                    "kind": "duplicate_files",
                    "content_hash": "",
                    "dedup_status": "suspected",
                    "match_basis": "size_candidate",
                    "size_bytes": size,
                    "verified_count": sum(1 for m in group_members if m["full_sha"]),
                    "member_count": len(group_members),
                    "members": [
                        {
                            "asset_id": m["asset_id"],
                            "path": m["path"],
                            "size_bytes": m["size_bytes"],
                            "verified": bool(m["full_sha"]),
                        }
                        for m in group_members
                    ],
                    "asset_ids": asset_ids,
                }
            )

    return suggestions


def _build_suggestions_from_snapshot(raw: Dict[str, Any]) -> Dict[str, Any]:
    """从注册表只读快照生成完整建议清单。"""
    assets = _registry_assets(raw)
    related_pairs = _duplicate_related_pairs(raw)

    paths = _display_paths(assets)
    naming: List[Dict[str, Any]] = []
    for asset in assets:
        subject = _naming_subject(asset)
        if not subject:
            continue
        assessment = validate_name(subject, DEFAULT_NAMING_RULES)
        if not assessment.violations or _naming_already_resolved(
            asset, assessment.suggested_name
        ):
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

    asset_by_display_path: Dict[str, Dict[str, Any]] = {}
    for asset in assets:
        asset_by_display_path.setdefault(
            str(asset.get("display_path") or asset.get("name") or "").strip(), asset
        )
    similar_names = [
        {
            "suggestion_id": _suggestion_id(
                "similar_names", group.normalized_name, len(group.members)
            ),
            "kind": "similar_names",
            "normalized_name": group.normalized_name,
            "members": list(group.members),
            "asset_ids": [
                str(asset_by_display_path[path].get("asset_id") or "")
                if path in asset_by_display_path
                else ""
                for path in group.members
            ],
        }
        for group in _group_similar_display_paths(paths)
    ]

    duplicate_files = _duplicate_file_suggestions(raw, assets, related_pairs)

    index_snapshot = _load_index_snapshot()
    duplicate_shots = []
    for item in index_snapshot["shot_duplicate_groups"]:
        members = item.get("members") or []
        asset_ids = _distinct_member_asset_ids(members)
        # 同一资产的多镜头不能互为 duplicate；至少两个不同资产才建议。
        if len(asset_ids) < 2:
            continue
        pair = frozenset(asset_ids[:2])
        if pair in related_pairs:
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
        left_id = str(item.get("left_asset_id") or "")
        right_id = str(item.get("right_asset_id") or "")
        if not left_id or not right_id or left_id == right_id:
            continue
        pair = frozenset((left_id, right_id))
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
        "dedup_summary": {
            "confirmed_groups": sum(
                1 for item in duplicate_files if item.get("dedup_status") == "confirmed"
            ),
            "suspected_groups": sum(
                1 for item in duplicate_files if item.get("dedup_status") != "confirmed"
            ),
        },
        "revision": int(raw.get("revision", 1)),
        "data_status": repo.DATA_STATUS_OK if not data_gaps else DATA_STATUS_NOT_INTEGRATED,
        "data_gaps": data_gaps,
    }


def _visibility_cache_key(explicit_key: Optional[str]) -> str:
    if explicit_key is not None:
        return str(explicit_key)
    auth = current_auth_context.get()
    role = str(getattr(auth, "role", "") or "")
    owner = str(current_asset_owner.get() or "")
    return f"{owner}:{role}"


def build_suggestions(*, visibility_key: Optional[str] = None) -> Dict[str, Any]:
    """生成建议；按注册表版本与可见主体缓存，并合并同键并发冷算。"""
    raw = repo.readonly_registry_snapshot()
    runtime_identity = "\0".join(
        os.environ.get(name, "")
        for name in (
            storage.RUNTIME_MODE_ENV,
            storage.DATA_DIR_ENV,
            storage.LOCAL_AUTH_DB_ENV,
            storage.VIDEO_DATA_DIR_ENV,
            "LOCALAPPDATA",
        )
    )
    runtime_scope = hashlib.sha256(runtime_identity.encode("utf-8")).hexdigest()[:24]
    key = (
        int(raw.get("revision", 1)),
        _visibility_cache_key(visibility_key),
        runtime_scope,
    )
    with _GOVERNANCE_CACHE_LOCK:
        cached = _GOVERNANCE_CACHE.get(key)
        if cached is not None:
            _GOVERNANCE_CACHE.move_to_end(key)
            return deepcopy(cached)
        pending = _GOVERNANCE_INFLIGHT.get(key)
        if pending is None:
            pending = Future()
            _GOVERNANCE_INFLIGHT[key] = pending
            is_owner = True
        else:
            is_owner = False
    if not is_owner:
        return deepcopy(pending.result())

    try:
        result = _build_suggestions_from_snapshot(raw)
    except BaseException as exc:
        with _GOVERNANCE_CACHE_LOCK:
            _GOVERNANCE_INFLIGHT.pop(key, None)
            pending.set_exception(exc)
        raise
    with _GOVERNANCE_CACHE_LOCK:
        _GOVERNANCE_CACHE[key] = result
        _GOVERNANCE_CACHE.move_to_end(key)
        while len(_GOVERNANCE_CACHE) > _GOVERNANCE_CACHE_LIMIT:
            _GOVERNANCE_CACHE.popitem(last=False)
        _GOVERNANCE_INFLIGHT.pop(key, None)
        pending.set_result(result)
    return deepcopy(result)


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
        if entry.get("dedup_status") != "confirmed":
            raise CleanroomException(
                409,
                "DEDUP_NOT_CONFIRMED",
                "疑似重复素材尚未完成完整 SHA 核验，请先完成完整 SHA 确认后再建立重复关联",
            )
        left, right = _require_asset_ids(entry)[:2]
        _reject_self_relation(left, right)
        raw = repo.state().read()
        left_rec = raw.get("assets", {}).get(left)
        right_rec = raw.get("assets", {}).get(right)
        if not isinstance(left_rec, dict) or not isinstance(right_rec, dict):
            raise CleanroomException(
                409,
                "DEDUP_NOT_CONFIRMED",
                "待确认重复关系的资产已不存在或已失效",
            )
        left_path = dedup_confirm.resolve_asset_media_path(raw, left, left_rec)
        right_path = dedup_confirm.resolve_asset_media_path(raw, right, right_rec)
        left_sha = dedup_confirm.trusted_full_sha256(left_rec, path=left_path)
        right_sha = dedup_confirm.trusted_full_sha256(right_rec, path=right_path)
        if not left_sha or not right_sha or left_sha != right_sha:
            raise CleanroomException(
                409,
                "DEDUP_NOT_CONFIRMED",
                "完整 SHA 确认缓存已失效或不一致，请重新核验完整 SHA",
            )
        return repo.create_relation(left, right, "duplicate", expected_version)
    if kind == "duplicate_shots":
        members = entry.get("members") or []
        if len(members) < 2:
            raise CleanroomException(400, "INVALID_REQUEST", "镜头查重建议成员不足")
        asset_ids = _distinct_member_asset_ids(members)
        if len(asset_ids) < 2:
            raise CleanroomException(
                400, "INVALID_REQUEST", "镜头查重建议不能关联同一资产"
            )
        return repo.create_relation(
            asset_ids[0], asset_ids[1], "duplicate", expected_version
        )
    if kind == "vector_duplicates":
        left = str(entry.get("left_asset_id") or "")
        right = str(entry.get("right_asset_id") or "")
        if not left or not right:
            raise CleanroomException(400, "INVALID_REQUEST", "向量近重建议成员不完整")
        _reject_self_relation(left, right)
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
        raise CleanroomException(
            400, "INVALID_REQUEST", "相似名建议仅提示，不提供确认写入"
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
