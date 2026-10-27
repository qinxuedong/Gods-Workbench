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

"""SQLite-vec 与 FTS5 的轻量双轨召回与排序融合。

MVP1-T6 在资产级融合之上补镜头级下钻：先用同一套双轨召回选出候选资产，
再把候选资产下钻到已登记镜头（``metadata.video_segments``），用镜头标签与
摘要对查询词做词面加权，输出带起止时间戳的镜头命中，供播放器按需跳转。
"""

from __future__ import annotations

import math
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, overload

from gw.core.sqlite_vec_engine import (
    MAX_QUERY_LENGTH,
    MAX_SEARCH_LIMIT,
    SQLiteVecEngine,
)

#: 单次查询参与词面加权的最大词元数，避免超长查询拖慢下钻。
MAX_SHOT_QUERY_TOKENS = 64
#: 单镜头词面命中数达到该值即取得满额加权，防止长摘要无限拉分。
SHOT_LEXICAL_BONUS_CAP = 5

_CJK_RANGES = "\u3400-\u4dbf\u4e00-\u9fff"
_TOKEN_SPLIT = re.compile(rf"[^0-9a-zA-Z{_CJK_RANGES}]+")
_CJK_CHAR = re.compile(rf"[{_CJK_RANGES}]")


def shot_query_tokens(query: str) -> tuple[str, ...]:
    """把查询切成镜头词面加权用的词元。

    中文没有空格分词，因此中文片段取二元组（单字片段保留单字）；拉丁片段
    取长度 ≥2 的整词。词元去重且保序，总数不超过 ``MAX_SHOT_QUERY_TOKENS``。
    """
    if not isinstance(query, str):
        raise ValueError("query 必须是字符串")
    tokens: list[str] = []
    seen: set[str] = set()

    def _push(token: str) -> None:
        if not token or token in seen or len(tokens) >= MAX_SHOT_QUERY_TOKENS:
            return
        seen.add(token)
        tokens.append(token)

    for chunk in _TOKEN_SPLIT.split(query.casefold()):
        if not chunk:
            continue
        if _CJK_CHAR.search(chunk):
            if len(chunk) == 1:
                _push(chunk)
            for index in range(len(chunk) - 1):
                _push(chunk[index : index + 2])
        elif len(chunk) >= 2:
            _push(chunk)
    return tuple(tokens)


def shot_lexical_hits(
    tokens: Sequence[str], *, tags: Sequence[str], summary: str
) -> int:
    """统计词元在镜头标签与摘要中的命中个数（大小写不敏感的子串命中）。"""
    haystack = " ".join(
        [str(tag) for tag in tags if isinstance(tag, str)] + [str(summary or "")]
    ).casefold()
    if not haystack:
        return 0
    return sum(1 for token in tokens if token and token in haystack)


def _finite_seconds(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        seconds = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return seconds if math.isfinite(seconds) else None


def normalize_shot_records(segments: Any) -> list[dict[str, Any]]:
    """把登记的镜头段落收敛成可检索镜头行（与 MVP1-T1 索引契约同序）。

    只保留起止时间戳合法的段落，序号按收敛后顺序从 0 连续编号；非法段落
    直接跳过，不伪造缺省时间戳，也不改变源数据。
    """
    if not isinstance(segments, (list, tuple)):
        return []
    records: list[dict[str, Any]] = []
    for segment in segments:
        if not isinstance(segment, Mapping):
            continue
        start_sec = _finite_seconds(segment.get("start_sec"))
        end_sec = _finite_seconds(segment.get("end_sec"))
        if start_sec is None or end_sec is None:
            continue
        if start_sec < 0 or end_sec <= start_sec:
            continue
        raw_tags = segment.get("tags")
        tags = [
            tag.strip() for tag in raw_tags if isinstance(tag, str) and tag.strip()
        ] if isinstance(raw_tags, (list, tuple)) else []
        records.append(
            {
                "shot_index": len(records),
                "start_sec": start_sec,
                "end_sec": end_sec,
                "tags": tuple(tags),
                "summary": str(segment.get("summary") or "").strip(),
                "content_hash": (
                    str(segment["content_hash"])
                    if isinstance(segment.get("content_hash"), str)
                    else None
                ),
            }
        )
    return records


@dataclass(frozen=True, slots=True)
class ShotSearchHit:
    """一条镜头级（或无可下钻镜头时的资产级）检索命中。

    ``granularity`` 为 ``shot`` 时 ``start_sec``/``end_sec`` 必有值，前端据此
    把播放器跳到命中时间戳；为 ``asset`` 时表示该资产没有登记镜头，只能整条打开。
    """

    asset_id: str
    granularity: str
    score: float
    asset_score: float
    shot_index: int | None
    start_sec: float | None
    end_sec: float | None
    lexical_hits: int
    tags: tuple[str, ...]
    summary: str
    content_hash: str | None


@dataclass(frozen=True, slots=True)
class ShotSearchResults:
    """镜头级检索结果集与向量轨降级状态。"""

    hits: tuple[ShotSearchHit, ...]
    vector_track_degraded: bool


@dataclass(frozen=True, slots=True)
class HybridSearchResult:
    """一条去重后的混合检索结果及来源轨道排名。"""

    asset_id: str
    score: float
    lexical_score: float | None
    vector_distance: float | None
    lexical_rank: int | None
    vector_rank: int | None
    vector_track_degraded: bool


class HybridSearchResults(list[HybridSearchResult]):
    """兼容列表操作的结果集合，并保留空结果上的轨道降级状态。"""

    __slots__ = ("vector_track_degraded",)

    def __init__(
        self,
        results: Sequence[HybridSearchResult],
        *,
        vector_track_degraded: bool,
    ) -> None:
        super().__init__(results)
        self.vector_track_degraded = vector_track_degraded

    @overload
    def __getitem__(self, index: int) -> HybridSearchResult: ...

    @overload
    def __getitem__(self, index: slice) -> HybridSearchResults: ...

    def __getitem__(
        self, index: int | slice
    ) -> HybridSearchResult | HybridSearchResults:
        if isinstance(index, slice):
            sliced: list[HybridSearchResult] = super().__getitem__(index)
            return HybridSearchResults(
                sliced, vector_track_degraded=self.vector_track_degraded
            )
        return super().__getitem__(index)


@dataclass(slots=True)
class _Candidate:
    asset_id: str
    score: float = 0.0
    lexical_score: float | None = None
    vector_distance: float | None = None
    lexical_rank: int | None = None
    vector_rank: int | None = None


def _positive_int(value: int, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"{name} 必须是正整数")
    return value


def _bounded_search_limit(value: int, name: str) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not 1 <= value <= MAX_SEARCH_LIMIT
    ):
        raise ValueError(f"{name} 必须在 1 到 {MAX_SEARCH_LIMIT} 之间")
    return value


class HybridAssetSearcher:
    """按 reciprocal rank fusion 合并关键词与向量召回。"""

    def __init__(self, engine: SQLiteVecEngine, *, rrf_k: int = 60) -> None:
        self.engine = engine
        self.rrf_k = _positive_int(rrf_k, "rrf_k")

    def search(
        self,
        query: str,
        *,
        query_vector: Sequence[float] | None = None,
        limit: int = 20,
        candidate_limit: int | None = None,
    ) -> HybridSearchResults:
        """合并两轨排名。

        limit/candidate_limit 不在 1~1000 时抛 ValueError；
        query 最长 4096 字符。任一轨暂时不可用时返回另一轨结果并标记向量降级。
        """
        limit = _bounded_search_limit(limit, "limit")
        if not isinstance(query, str):
            raise ValueError("query 必须是字符串")
        if len(query) > MAX_QUERY_LENGTH:
            raise ValueError(f"query 长度不得超过 {MAX_QUERY_LENGTH} 个字符")
        candidate_limit = _bounded_search_limit(
            candidate_limit
            if candidate_limit is not None
            else min(limit * 3, MAX_SEARCH_LIMIT),
            "candidate_limit",
        )

        candidates: dict[str, _Candidate] = {}
        for rank, match in enumerate(
            self.engine.search_fts(query, limit=candidate_limit), start=1
        ):
            candidate = candidates.setdefault(match.asset_id, _Candidate(match.asset_id))
            candidate.score += 1.0 / (self.rrf_k + rank)
            candidate.lexical_score = match.score
            candidate.lexical_rank = rank

        vector_track_degraded = False
        if query_vector is not None:
            for rank, match in enumerate(
                self.engine.search_vectors(query_vector, limit=candidate_limit), start=1
            ):
                candidate = candidates.setdefault(match.asset_id, _Candidate(match.asset_id))
                candidate.score += 1.0 / (self.rrf_k + rank)
                candidate.vector_distance = match.distance
                candidate.vector_rank = rank
            vector_track_degraded = self.engine.last_vector_search_degraded

        ordered = sorted(candidates.values(), key=lambda item: (-item.score, item.asset_id))
        return HybridSearchResults(
            [
                HybridSearchResult(
                    asset_id=item.asset_id,
                    score=item.score,
                    lexical_score=item.lexical_score,
                    vector_distance=item.vector_distance,
                    lexical_rank=item.lexical_rank,
                    vector_rank=item.vector_rank,
                    vector_track_degraded=vector_track_degraded,
                )
                for item in ordered[:limit]
            ],
            vector_track_degraded=vector_track_degraded,
        )

    def search_shots(
        self,
        query: str,
        *,
        query_vector: Sequence[float] | None = None,
        limit: int = 20,
        candidate_limit: int | None = None,
        asset_limit: int | None = None,
        shot_provider: Callable[[str], Sequence[Mapping[str, Any]]] | None = None,
        extra_asset_ids: Sequence[str] = (),
    ) -> ShotSearchResults:
        """把资产级双轨召回下钻为镜头级命中。

        候选资产仍由 ``search`` 决定（因此关键词/向量轨、降级与限流口径完全
        一致），随后按 ``shot_provider`` 提供的登记镜头逐个计算词面命中数：
        命中越多加权越高（满额 ``SHOT_LEXICAL_BONUS_CAP`` 个），保证单镜头
        分数落在 ``[asset_score, 2 * asset_score]``，不会盖过更强的候选资产。
        未登记镜头的资产退化为一条 ``granularity=asset`` 命中，不伪造时间戳。

        ``extra_asset_ids`` 用于补齐「镜头文本命中但资产文档未命中」的资产
        （检索文档只收资产级标签，镜头标签由注册表另行匹配）：这些候选以零
        分并入，靠镜头词面命中数在零分档内排序，不改变双轨召回的排序结果。
        """
        limit = _bounded_search_limit(limit, "limit")
        asset_limit = _bounded_search_limit(
            asset_limit if asset_limit is not None else limit, "asset_limit"
        )
        asset_results = self.search(
            query,
            query_vector=query_vector,
            limit=asset_limit,
            candidate_limit=candidate_limit,
        )
        recalled = [(result.asset_id, result) for result in asset_results]
        seen_ids = {asset_id for asset_id, _ in recalled}
        for asset_id in extra_asset_ids:
            if not isinstance(asset_id, str) or not asset_id or asset_id in seen_ids:
                continue
            seen_ids.add(asset_id)
            recalled.append((asset_id, None))
        tokens = shot_query_tokens(query)
        hits: list[ShotSearchHit] = []
        for asset_id, asset_result in recalled:
            asset_score = asset_result.score if asset_result is not None else 0.0
            records = (
                normalize_shot_records(shot_provider(asset_id))
                if shot_provider is not None
                else []
            )
            if not records:
                hits.append(
                    ShotSearchHit(
                        asset_id=asset_id,
                        granularity="asset",
                        score=asset_score,
                        asset_score=asset_score,
                        shot_index=None,
                        start_sec=None,
                        end_sec=None,
                        lexical_hits=0,
                        tags=(),
                        summary="",
                        content_hash=None,
                    )
                )
                continue
            for record in records:
                lexical_hits = shot_lexical_hits(
                    tokens, tags=record["tags"], summary=record["summary"]
                )
                bonus = 1.0 + min(lexical_hits, SHOT_LEXICAL_BONUS_CAP) / (
                    SHOT_LEXICAL_BONUS_CAP
                )
                hits.append(
                    ShotSearchHit(
                        asset_id=asset_id,
                        granularity="shot",
                        score=asset_score * bonus,
                        asset_score=asset_score,
                        shot_index=record["shot_index"],
                        start_sec=record["start_sec"],
                        end_sec=record["end_sec"],
                        lexical_hits=lexical_hits,
                        tags=record["tags"],
                        summary=record["summary"],
                        content_hash=record["content_hash"],
                    )
                )
        hits.sort(
            key=lambda hit: (
                -hit.score,
                # 零分档（镜头文本命中但资产文档未命中）内按镜头词面命中数排序，
                # 保证命中的镜头仍排在同资产其他镜头之前。
                -hit.lexical_hits,
                hit.asset_id,
                -1 if hit.shot_index is None else hit.shot_index,
            )
        )
        return ShotSearchResults(
            hits=tuple(hits[:limit]),
            vector_track_degraded=asset_results.vector_track_degraded,
        )
