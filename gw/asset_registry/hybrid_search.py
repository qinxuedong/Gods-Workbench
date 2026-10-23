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

"""SQLite-vec 与 FTS5 的轻量双轨召回与排序融合。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from gw.core.sqlite_vec_engine import SQLiteVecEngine


@dataclass(frozen=True, slots=True)
class HybridSearchResult:
    """一条去重后的混合检索结果及来源轨道排名。"""

    asset_id: str
    score: float
    lexical_score: float | None
    vector_distance: float | None
    lexical_rank: int | None
    vector_rank: int | None


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
    ) -> list[HybridSearchResult]:
        """合并两轨排名；任一轨不可用时返回另一轨结果。"""
        limit = _positive_int(limit, "limit")
        if not isinstance(query, str):
            raise ValueError("query 必须是字符串")
        candidate_limit = _positive_int(
            candidate_limit if candidate_limit is not None else max(limit * 3, limit),
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

        if query_vector is not None:
            for rank, match in enumerate(
                self.engine.search_vectors(query_vector, limit=candidate_limit), start=1
            ):
                candidate = candidates.setdefault(match.asset_id, _Candidate(match.asset_id))
                candidate.score += 1.0 / (self.rrf_k + rank)
                candidate.vector_distance = match.distance
                candidate.vector_rank = rank

        ordered = sorted(candidates.values(), key=lambda item: (-item.score, item.asset_id))
        return [
            HybridSearchResult(
                asset_id=item.asset_id,
                score=item.score,
                lexical_score=item.lexical_score,
                vector_distance=item.vector_distance,
                lexical_rank=item.lexical_rank,
                vector_rank=item.vector_rank,
            )
            for item in ordered[:limit]
        ]
