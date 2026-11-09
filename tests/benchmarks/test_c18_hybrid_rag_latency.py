"""C18-T4：SQLite-vec / FTS5 混合检索契约与延迟基准。"""

from __future__ import annotations

import json
import logging
import os
import platform
import random
import sqlite3
import time
from math import ceil
from pathlib import Path

import psutil
import pytest

import gw.core.sqlite_vec_engine as sqlite_vec_engine
from gw.asset_registry import repository as _asset_repo
from gw.asset_registry.hybrid_search import HybridAssetSearcher
from gw.core import storage as _storage
from gw.core.sqlite_vec_engine import SQLiteVecEngine


class _FailingKnnConnection:
    """在一次 KNN 查询或过大 LIMIT 时注入瞬时 SQLite 错误。"""

    def __init__(
        self,
        connection: sqlite3.Connection,
        *,
        failure: Exception | None = None,
        fail_limit_above: int | None = None,
    ) -> None:
        self.connection = connection
        self.failure = failure
        self.fail_limit_above = fail_limit_above

    def execute(self, sql: str, parameters: tuple[object, ...] = ()) -> object:
        if "FROM c18_vectors" in sql:
            limit = int(parameters[-1])
            should_fail = (
                self.failure is not None
                or self.fail_limit_above is not None
                and limit > self.fail_limit_above
            )
            if should_fail:
                if self.failure is not None:
                    failure = self.failure
                    self.failure = None
                    raise failure
                raise sqlite3.OperationalError("KNN LIMIT exceeds test boundary")
        return self.connection.execute(sql, parameters)

    def close(self) -> None:
        self.connection.close()


class _FailingVersionConnection:
    """在读取 sqlite-vec 版本时注入 SQLite 错误。"""

    def execute(self, sql: str, parameters: tuple[object, ...] = ()) -> object:
        if "vec_version()" in sql:
            raise sqlite3.OperationalError("version probe failed")
        raise AssertionError(f"unexpected SQL: {sql}")


def _nearest_rank_percentile(samples: list[float], quantile: float) -> float:
    if not samples:
        raise ValueError("至少需要一个延迟样本")
    if not 0 < quantile <= 1:
        raise ValueError("分位数必须在 (0, 1] 范围内")
    return sorted(samples)[ceil(len(samples) * quantile) - 1]


def test_fts5_indexes_literal_text_and_replaces_documents(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """FTS5 使用字面查询，并在更新、删除时同步索引。"""
    monkeypatch.setattr(sqlite_vec_engine, "_load_sqlite_vec", lambda _db: False)
    engine = SQLiteVecEngine(tmp_path / "search.sqlite3", dimensions=3)
    try:
        assert engine.vector_enabled is False
        engine.upsert_document(
            asset_id="asset-ocean",
            title="海边日落",
            content="潮汐与海浪的镜头",
            embedding=[1.0, 0.0, 0.0],
        )
        engine.upsert_document(
            asset_id="asset-room",
            title="室内对白",
            content='包含引号 " OR AND 等符号的文本',
        )

        assert [hit.asset_id for hit in engine.search_fts("海浪", limit=5)] == [
            "asset-ocean"
        ]
        assert engine.search_fts("NOT (", limit=5) == []

        engine.upsert_document(
            asset_id="asset-ocean",
            title="更新后的日落",
            content="旧镜头已替换为群山",
        )
        assert engine.search_fts("海浪", limit=5) == []
        assert [hit.asset_id for hit in engine.search_fts("群山", limit=5)] == [
            "asset-ocean"
        ]

        engine.delete_document("asset-ocean")
        assert engine.search_fts("群山", limit=5) == []
    finally:
        engine.close()


def test_batch_upsert_matches_repeated_single_upserts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """批量写入的索引结果与逐条 upsert 一致，并按输入顺序返回结果。"""
    monkeypatch.setattr(sqlite_vec_engine, "_load_sqlite_vec", lambda _db: False)
    single_engine = SQLiteVecEngine(tmp_path / "single.sqlite3", dimensions=3)
    batch_engine = SQLiteVecEngine(tmp_path / "batch.sqlite3", dimensions=3)
    documents = [
        {"asset_id": "asset-ocean", "title": "海边日落", "content": "潮汐与海浪"},
        {"asset_id": "asset-mountain", "title": "雪山", "content": "山峰与云层"},
        {"asset_id": "asset-ocean", "title": "更新后的海岸", "content": "旧文本已替换"},
    ]
    try:
        single_results = [
            single_engine.upsert_document(**document) for document in documents
        ]

        batch_results = batch_engine.batch_upsert_documents(documents, batch_size=2)

        assert batch_results == single_results == [False, False, False]
        for query in ("海岸", "日落", "山峰", "旧文本"):
            assert [hit.asset_id for hit in batch_engine.search_fts(query)] == [
                hit.asset_id for hit in single_engine.search_fts(query)
            ]
    finally:
        single_engine.close()
        batch_engine.close()


def test_batch_upsert_rolls_back_only_the_batch_that_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """失败批次整体回滚，之前已提交的批次仍可检索。"""
    monkeypatch.setattr(sqlite_vec_engine, "_load_sqlite_vec", lambda _db: False)
    engine = SQLiteVecEngine(tmp_path / "batch-rollback.sqlite3", dimensions=3)
    documents = [
        {"asset_id": "asset-a", "title": "已提交资产一", "content": "第一批"},
        {"asset_id": "asset-b", "title": "已提交资产二", "content": "第一批"},
        {"asset_id": "asset-c", "title": "待回滚资产", "content": "当前批次"},
        {
            "asset_id": "asset-invalid",
            "title": "错误向量",
            "content": "触发失败",
            "embedding": [1.0, 0.0],
        },
    ]
    try:
        with pytest.raises(ValueError, match="维度"):
            engine.batch_upsert_documents(documents, batch_size=2)

        committed = engine.search_fts("第一批", limit=10)
        assert [hit.asset_id for hit in committed] == ["asset-a", "asset-b"]
        assert engine.search_fts("待回滚", limit=10) == []
        assert engine.search_fts("触发失败", limit=10) == []
    finally:
        engine.close()


def test_batch_upsert_commits_every_500_documents_by_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """默认批大小为 500，1001 条记录分 3 个事务提交。"""
    monkeypatch.setattr(sqlite_vec_engine, "_load_sqlite_vec", lambda _db: False)
    engine = SQLiteVecEngine(tmp_path / "batch-default-size.sqlite3", dimensions=3)
    statements: list[str] = []
    engine._db.set_trace_callback(statements.append)
    documents = [
        {"asset_id": f"asset-{index}", "title": f"素材 {index}", "content": "批量索引"}
        for index in range(1001)
    ]
    try:
        results = engine.batch_upsert_documents(documents)

        transactions = [
            statement
            for statement in statements
            if statement in {"BEGIN IMMEDIATE", "COMMIT"}
        ]
        assert len(results) == 1001
        assert transactions == ["BEGIN IMMEDIATE", "COMMIT"] * 3
    finally:
        engine.close()


def test_batch_upsert_matches_single_vector_results_when_extension_is_available(
    tmp_path: Path,
) -> None:
    """批量 upsert 的 FTS5 与 sqlite-vec 检索结果和逐条写入相同。"""
    pytest.importorskip("sqlite_vec", reason="当前运行时未安装 sqlite-vec 扩展")
    single_engine = SQLiteVecEngine(tmp_path / "single-vectors.sqlite3", dimensions=3)
    batch_engine = SQLiteVecEngine(tmp_path / "batch-vectors.sqlite3", dimensions=3)
    documents = [
        {
            "asset_id": "asset-ocean",
            "title": "海岸日落",
            "content": "潮汐海浪",
            "embedding": [1.0, 0.0, 0.0],
        },
        {
            "asset_id": "asset-mountain",
            "title": "雪山云层",
            "content": "山峰",
            "embedding": [0.0, 1.0, 0.0],
        },
    ]
    try:
        single_results = [
            single_engine.upsert_document(**document) for document in documents
        ]
        batch_results = batch_engine.batch_upsert_documents(documents)

        assert batch_results == single_results == [True, True]
        single_hits = single_engine.search_vectors([0.9, 0.1, 0.0], limit=2)
        batch_hits = batch_engine.search_vectors([0.9, 0.1, 0.0], limit=2)
        assert [(hit.asset_id, hit.distance) for hit in batch_hits] == [
            (hit.asset_id, hit.distance) for hit in single_hits
        ]
        assert [hit.asset_id for hit in batch_engine.search_fts("海浪")] == [
            hit.asset_id for hit in single_engine.search_fts("海浪")
        ]
    finally:
        single_engine.close()
        batch_engine.close()


def test_batch_upsert_rolls_back_vector_and_fts_indexes_together(
    tmp_path: Path,
) -> None:
    """失败批次不会留下孤立的 FTS5 文档或 sqlite-vec 向量。"""
    pytest.importorskip("sqlite_vec", reason="当前运行时未安装 sqlite-vec 扩展")
    engine = SQLiteVecEngine(tmp_path / "vector-batch-rollback.sqlite3", dimensions=3)
    try:
        assert engine.vector_enabled is True
        with pytest.raises(ValueError, match="维度"):
            engine.batch_upsert_documents(
                [
                    {
                        "asset_id": "asset-rolled-back",
                        "title": "回滚标记",
                        "content": "向量与全文必须一起撤回",
                        "embedding": [1.0, 0.0, 0.0],
                    },
                    {
                        "asset_id": "asset-invalid",
                        "title": "错误向量",
                        "content": "维度错误",
                        "embedding": [1.0, 0.0],
                    },
                ],
                batch_size=2,
            )

        assert engine.search_fts("回滚标记") == []
        assert engine.search_vectors([1.0, 0.0, 0.0], limit=10) == []
    finally:
        engine.close()


def test_hybrid_search_merges_and_deduplicates_both_tracks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """融合按双轨排名计算 RRF，重复资产只返回一次且顺序稳定。"""
    monkeypatch.setattr(sqlite_vec_engine, "_load_sqlite_vec", lambda _db: False)
    engine = SQLiteVecEngine(tmp_path / "hybrid.sqlite3", dimensions=3)
    try:
        engine.upsert_document(asset_id="asset-a", title="大海 大海", content="浪花")
        engine.upsert_document(asset_id="asset-b", title="海边", content="大海 夜色")
        engine.upsert_document(asset_id="asset-c", title="其他", content="无关")

        engine.search_vectors = lambda _vector, limit=20: [  # type: ignore[method-assign]
            sqlite_vec_engine.VectorMatch(asset_id="asset-b", distance=0.01),
            sqlite_vec_engine.VectorMatch(asset_id="asset-c", distance=0.02),
        ]
        searcher = HybridAssetSearcher(engine, rrf_k=60)

        hits = searcher.search("大海", query_vector=[1.0, 0.0, 0.0], limit=5)

        assert [hit.asset_id for hit in hits] == ["asset-b", "asset-a", "asset-c"]
        assert len({hit.asset_id for hit in hits}) == len(hits)
        assert hits[0].lexical_rank == 2
        assert hits[0].vector_rank == 1
        assert hits[0].vector_track_degraded is False
        assert hits[1].lexical_rank == 1
        assert hits[1].vector_rank is None
    finally:
        engine.close()


def test_hybrid_search_degrades_to_fts5_when_sqlite_vec_is_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """扩展缺失时仍提供关键词召回，且不伪报向量结果。"""
    monkeypatch.setattr(sqlite_vec_engine, "_load_sqlite_vec", lambda _db: False)
    engine = SQLiteVecEngine(tmp_path / "fallback.sqlite3", dimensions=3)
    try:
        engine.upsert_document(
            asset_id="asset-1",
            title="沙滩",
            content="大海与海风",
            embedding=[1.0, 0.0, 0.0],
        )
        hits = HybridAssetSearcher(engine).search(
            "大海", query_vector=[1.0, 0.0, 0.0], limit=5
        )

        assert engine.vector_enabled is False
        assert [hit.asset_id for hit in hits] == ["asset-1"]
        assert hits[0].lexical_rank == 1
        assert hits[0].vector_rank is None
        assert hits[0].vector_track_degraded is True
        sliced_hits = hits[:1]
        assert sliced_hits == hits
        assert sliced_hits.vector_track_degraded is True
    finally:
        engine.close()


def test_degraded_vector_search_still_validates_query_dimensions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """降级状态下向量查询仍拒绝错误维度。"""
    monkeypatch.setattr(sqlite_vec_engine, "_load_sqlite_vec", lambda _db: False)
    engine = SQLiteVecEngine(tmp_path / "degraded-dimensions.sqlite3", dimensions=3)
    try:
        assert engine.vector_enabled is False

        with pytest.raises(ValueError, match="维度"):
            engine.search_vectors([1.0, 0.0], limit=1)
    finally:
        engine.close()


def test_sqlite_vec_returns_nearest_neighbors_when_extension_is_available(
    tmp_path: Path,
) -> None:
    """在安装了 sqlite-vec 的验证环境中核实 vec0 的真实 KNN 路径。"""
    pytest.importorskip("sqlite_vec", reason="当前运行时未安装 sqlite-vec 扩展")
    engine = SQLiteVecEngine(tmp_path / "vectors.sqlite3", dimensions=3)
    try:
        assert engine.vector_enabled is True
        engine.upsert_document(
            asset_id="asset-near",
            title="近邻",
            content="语义向量目标",
            embedding=[1.0, 0.0, 0.0],
        )
        engine.upsert_document(
            asset_id="asset-far",
            title="远邻",
            content="另一个目标",
            embedding=[0.0, 1.0, 0.0],
        )

        matches = engine.search_vectors([0.9, 0.1, 0.0], limit=2)

        assert [match.asset_id for match in matches] == ["asset-near", "asset-far"]
        assert matches[0].distance < matches[1].distance
    finally:
        engine.close()


@pytest.mark.parametrize(
    "failure",
    [
        sqlite3.OperationalError("temporary busy"),
        OverflowError("temporary KNN overflow"),
    ],
    ids=["sqlite-error", "overflow-error"],
)
def test_hybrid_runtime_query_error_degrades_once_without_disabling_vector_track(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, failure: Exception
) -> None:
    """单次查询错误应告警并临时降级，下一次查询仍可走向量轨。"""
    pytest.importorskip("sqlite_vec", reason="当前运行时未安装 sqlite-vec 扩展")
    engine = SQLiteVecEngine(tmp_path / "transient.sqlite3", dimensions=3)
    try:
        engine.upsert_document(
            asset_id="asset-ocean",
            title="海边日落",
            content="大海与海浪",
            embedding=[1.0, 0.0, 0.0],
        )
        engine._db = _FailingKnnConnection(
            engine._db, failure=failure
        )  # type: ignore[assignment]
        searcher = HybridAssetSearcher(engine)

        with caplog.at_level(logging.WARNING):
            degraded = searcher.search(
                "大海", query_vector=[1.0, 0.0, 0.0], limit=5
            )

        assert engine.vector_enabled is True
        assert degraded and all(hit.vector_track_degraded for hit in degraded)
        assert any(record.levelno == logging.WARNING for record in caplog.records)

        recovered = searcher.search("大海", query_vector=[1.0, 0.0, 0.0], limit=5)
        assert engine.vector_enabled is True
        assert any(hit.vector_rank is not None for hit in recovered)
        assert all(not hit.vector_track_degraded for hit in recovered)
    finally:
        engine.close()


def test_hybrid_limit_1400_keeps_vector_track_usable(
    tmp_path: Path,
) -> None:
    """limit=1400 被拒绝后，向量轨仍可查询且不会被 SQLite 错误误关。"""
    pytest.importorskip("sqlite_vec", reason="当前运行时未安装 sqlite-vec 扩展")
    engine = SQLiteVecEngine(tmp_path / "large-limit.sqlite3", dimensions=3)
    try:
        engine.upsert_document(
            asset_id="asset-ocean",
            title="海边日落",
            content="大海与海浪",
            embedding=[1.0, 0.0, 0.0],
        )
        engine._db = _FailingKnnConnection(
            engine._db, fail_limit_above=1000
        )  # type: ignore[assignment]
        searcher = HybridAssetSearcher(engine)

        with pytest.raises(ValueError, match="limit"):
            searcher.search("大海", query_vector=[1.0, 0.0, 0.0], limit=1400)

        assert engine.vector_enabled is True
        assert engine.search_vectors([1.0, 0.0, 0.0], limit=1)
    finally:
        engine.close()


def test_limit_and_query_length_bounds_and_degraded_upsert_validation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """检索上限统一截断，非正数/超长查询拒绝，降级 upsert 仍校验维度。"""
    monkeypatch.setattr(sqlite_vec_engine, "_load_sqlite_vec", lambda _db: False)
    engine = SQLiteVecEngine(tmp_path / "bounds.sqlite3", dimensions=3)
    try:
        with pytest.raises(ValueError, match="维度"):
            engine.upsert_document(
                asset_id="bad-vector",
                title="不正确维度",
                content="仍须验证",
                embedding=[1.0, 0.0],
            )
        engine.upsert_document(
            asset_id="asset-ocean", title="海边", content="大海与海浪"
        )
        with pytest.raises(ValueError, match="limit"):
            engine.search_fts("大海", limit=0)
        with pytest.raises(ValueError, match="limit"):
            engine.search_vectors([1.0, 0.0, 0.0], limit=-1)
        with pytest.raises(ValueError, match="limit"):
            engine.search_fts("大海", limit=1400)
        with pytest.raises(ValueError, match="query"):
            engine.search_fts(
                "x" * (sqlite_vec_engine.MAX_QUERY_LENGTH + 1), limit=1
            )
        with pytest.raises(ValueError, match="query"):
            HybridAssetSearcher(engine).search(
                "x" * (sqlite_vec_engine.MAX_QUERY_LENGTH + 1)
            )

        observed_limits: list[int] = []
        original_search_fts = engine.search_fts

        def record_limit(query: str, *, limit: int = 20) -> list[object]:
            observed_limits.append(limit)
            return original_search_fts(query, limit=limit)

        engine.search_fts = record_limit  # type: ignore[method-assign]
        searcher = HybridAssetSearcher(engine)
        searcher.search("大海", limit=300)
        searcher.search("大海", limit=20, candidate_limit=1000)
        with pytest.raises(ValueError, match="candidate_limit"):
            searcher.search("大海", candidate_limit=0)
        searcher.search("大海", limit=334)
        searcher.search("大海", limit=1000)
        assert observed_limits == [900, 1000, 1000, 1000]
    finally:
        engine.close()


def test_vector_version_sqlite_error_logs_warning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """版本探测失败可诊断，且仍返回 None。"""
    monkeypatch.setattr(sqlite_vec_engine, "_load_sqlite_vec", lambda _db: False)
    engine = SQLiteVecEngine(tmp_path / "vector-version.sqlite3", dimensions=3)
    try:
        with monkeypatch.context() as patch:
            patch.setattr(engine, "_db", _FailingVersionConnection())
            patch.setattr(engine, "vector_enabled", True)
            with caplog.at_level(logging.WARNING, logger=sqlite_vec_engine.logger.name):
                assert engine.vector_version is None
                assert engine.vector_version is None
        warnings = [
            record.levelno == logging.WARNING
            and "version" in record.getMessage().lower()
            for record in caplog.records
        ]
        assert any(warnings)
        assert sum(warnings) == 1
    finally:
        engine.close()


def test_empty_hybrid_results_expose_vector_degraded_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """无关键词命中且向量轨降级时，空结果仍携带降级信号。"""
    monkeypatch.setattr(sqlite_vec_engine, "_load_sqlite_vec", lambda _db: False)
    engine = SQLiteVecEngine(tmp_path / "empty-degraded.sqlite3", dimensions=3)
    try:
        monkeypatch.setattr(engine, "search_fts", lambda _query, *, limit=20: [])

        def degraded_search(_query_vector, *, limit: int = 20) -> list[object]:
            engine._vector_search_state.degraded = True
            return []

        monkeypatch.setattr(engine, "search_vectors", degraded_search)
        results = HybridAssetSearcher(engine).search(
            "无匹配内容", query_vector=[1.0, 0.0, 0.0]
        )

        assert results == []
        assert results.vector_track_degraded is True
    finally:
        engine.close()


def test_c18_hybrid_search_latency_and_memory_report(tmp_path: Path) -> None:
    """以预计算向量测量本机 SQLite 检索路径，不把模型推理计入时延。"""
    pytest.importorskip("sqlite_vec", reason="当前运行时未安装 sqlite-vec 扩展")
    document_count = int(os.getenv("GW_C18_BENCHMARK_DOCUMENTS", "1000"))
    dimensions = 128
    iterations = 200
    rng = random.Random(18)  # noqa: S311 - 固定种子保证基准数据可复现
    process = psutil.Process()
    rss_before = process.memory_info().rss
    rss_peak = rss_before
    engine = SQLiteVecEngine(
        tmp_path / "benchmark.sqlite3", dimensions=dimensions
    )

    try:
        assert engine.vector_enabled is True
        vectors = [
            [rng.random() for _ in range(dimensions)]
            for _ in range(document_count)
        ]
        for index, vector in enumerate(vectors):
            engine.upsert_document(
                asset_id=f"asset-{index:05d}",
                title=f"素材 {index}",
                content=("大海 海浪 日落 " if index % 5 == 0 else "山林 室内 ")
                + f"编号 {index}",
                embedding=vector,
            )

        searcher = HybridAssetSearcher(engine)
        query_vector = vectors[0]
        for _ in range(10):
            searcher.search("大海", query_vector=query_vector, limit=10)

        fts_latencies_ms: list[float] = []
        hybrid_latencies_ms: list[float] = []
        for _ in range(iterations):
            started = time.perf_counter_ns()
            lexical_hits = engine.search_fts("大海", limit=10)
            fts_latencies_ms.append((time.perf_counter_ns() - started) / 1_000_000)
            assert lexical_hits

            started = time.perf_counter_ns()
            hybrid_hits = searcher.search(
                "大海", query_vector=query_vector, limit=10
            )
            hybrid_latencies_ms.append(
                (time.perf_counter_ns() - started) / 1_000_000
            )
            assert hybrid_hits
            assert any(hit.vector_rank is not None for hit in hybrid_hits)
            rss_peak = max(rss_peak, process.memory_info().rss)

        report = {
            "measurement_scope": "precomputed vectors; FTS5 query and sqlite-vec KNN only; no embedding inference or NAS call",
            "sqlite_vec_enabled": engine.vector_enabled,
            "documents": document_count,
            "dimensions": dimensions,
            "iterations": iterations,
            "latency_ms": {
                "fts5_p50": _nearest_rank_percentile(fts_latencies_ms, 0.50),
                "fts5_p95": _nearest_rank_percentile(fts_latencies_ms, 0.95),
                "hybrid_p50": _nearest_rank_percentile(hybrid_latencies_ms, 0.50),
                "hybrid_p95": _nearest_rank_percentile(hybrid_latencies_ms, 0.95),
                "hybrid_max": max(hybrid_latencies_ms),
            },
            "process_rss_before_bytes": rss_before,
            "process_rss_peak_sampled_bytes": rss_peak,
            "process_rss_after_bytes": process.memory_info().rss,
            "host": {
                "os": platform.platform(),
                "architecture": platform.machine(),
                "python": platform.python_version(),
            },
            "nas_status": "not_measured",
        }
        print("C18_BENCHMARK_JSON=" + json.dumps(report, ensure_ascii=False))
        evidence_dir = os.getenv("GW_C18_EVIDENCE_DIR")
        if evidence_dir:
            destination = Path(evidence_dir)
            destination.mkdir(parents=True, exist_ok=True)
            (destination / "c18-hybrid-rag-latency.json").write_text(
                json.dumps(report, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )

        assert report["latency_ms"]["fts5_p95"] < 10.0
        assert report["latency_ms"]["hybrid_p95"] < 50.0
    finally:
        engine.close()


# --- QIN-565：规模放大后的 /search/shots 延迟与召回等价回归 --------------------
#
# 基线（master，5,258 文档 / 15,774 镜头）：单请求把整份注册表 JSON 反序列化
# 十余次（每个候选资产详情各一次），p95≈1.5s（真机 A4 口径 3.0~3.6s）。
# 修复后同一请求只读一次注册表快照，并复用预收敛的词面字段。

QIN565_ASSET_COUNT = 5258
QIN565_SHOTS_PER_ASSET = 3
QIN565_QUERIES = (
    "海边日出 海浪 沙滩",
    "女主角 特写 回望",
    "霓虹 街道 雨夜 人群",
    "瀑布 森林 山峦 全景",
    "船舱 码头 车流",
    "红日破晓 海面 泛白",
    "浪花 礁石 碎裂",
    "对白 中景 室内",
    "雪地 足迹 中景",
    "实验室 特写 仪器",
    "晨光 山峦 全景",
    "夜景 车流 街道",
    "特写 眼神 女主角",
    "瀑布 水雾 慢门",
    "码头 集装箱 晨光",
    "人群 广场 全景",
    "雨夜 伞 霓虹",
    "船舱 仪表 特写",
    "森林 逆光 树影",
    "沙滩 脚印 潮汐",
)
QIN565_TAG_POOL = (
    "日出", "晨光", "大海", "海浪", "沙滩", "足迹", "特写", "中景",
    "全景", "夜景", "雨夜", "街道", "人群", "山峦", "森林", "瀑布",
)
QIN565_AUTH = {"Authorization": "Bearer cleanroom-test", "X-User-Role": "editor"}
QIN565_SHOT_API = "/api/asset-registry/search/shots"


def _qin565_registry(asset_count: int) -> dict:
    """构造可复现的大规模注册表，混入停用来源根/隐藏内置/空 metadata 等边界。"""
    rng = random.Random(565)  # noqa: S311 - 固定种子保证基准可复现
    assets: dict[str, dict] = {}
    for index in range(asset_count):
        asset_id = "asset_%05d" % index
        segments = []
        cursor = 0.0
        for shot in range(QIN565_SHOTS_PER_ASSET):
            start = cursor
            end = round(start + rng.uniform(2.0, 9.0), 3)
            cursor = end
            chosen = rng.sample(QIN565_TAG_POOL, 2)
            segments.append(
                {
                    "start_sec": start,
                    "end_sec": end,
                    "tags": chosen,
                    "summary": "%s与%s的镜头 %d" % (chosen[0], chosen[1], index),
                }
            )
        root_id = "root_removed" if index in (37, 83, 151) else "root_library"
        asset = {
            "asset_id": asset_id,
            "name": "ClipABC_%05d.mp4" % index if index % 7 == 0 else "素材_%05d.mp4" % index,
            "kind": "video",
            "display_path": "footage/%s/素材_%05d.mp4" % (root_id, index),
            "canonical_relpath": "footage/clip_%05d.mp4" % index,
            "storage_root_id": root_id,
            "root_id": root_id,
            "created_at": "2026-10-01T08:00:00Z",
            "tags": ["LOOP", "AbcClip"] if index == 29 else rng.sample(QIN565_TAG_POOL, 2),
            "metadata": (
                {}
                if index == 23
                else {"frame_rate": 25.0, "duration": cursor, "video_segments": segments}
            ),
            "version": 1,
        }
        if index == 11:
            asset["hidden"] = True
            asset["metadata"] = {"builtin": True, "video_segments": segments}
        assets[asset_id] = asset
    raw = _asset_repo._blank()
    raw["assets"] = assets
    return raw


def _qin565_persist_inactive_root(tmp_path: Path) -> None:
    """登记一条已停用来源根，让 removed 过滤在等价性用例中真实生效。"""
    _storage.save(
        "allowed_roots",
        {
            "schema_version": 1,
            "revision": 2,
            "roots": [
                {
                    "id": "root_removed",
                    "path": str(tmp_path / "offline_root"),
                    "active": False,
                    "aliases": [],
                }
            ],
            "system_aliases": {},
        },
    )


def _qin565_legacy_keyword_ids(query: str, limit: int) -> list[str]:
    """修复前 ``_keyword_recalled_assets`` 的口径：list_assets 全表扫描取 id。"""
    candidate_limit = min(max(limit * 3, limit), 100)
    page = _asset_repo.list_assets(candidate_limit, 0, query, None, None)
    items = page.get("items") if isinstance(page, dict) else None
    asset_ids: list[str] = []
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict):
            continue
        asset_id = str(item.get("asset_id") or item.get("id") or "").strip()
        if asset_id and asset_id not in asset_ids:
            asset_ids.append(asset_id)
    return asset_ids


def test_qin565_keyword_recall_matches_legacy_list_assets(tmp_path: Path) -> None:
    """AC2：词面召回集合与顺序逐句等价，停用来源根过滤口径也一致。"""
    raw = _qin565_registry(240)
    _storage.save(_asset_repo.NS_REGISTRY, raw)
    _qin565_persist_inactive_root(tmp_path)

    assert _asset_repo._removed_root_ids() == {"root_removed"}
    assert _asset_repo._removed_root_ids_for_search() == {"root_removed"}
    assert _asset_repo.keyword_recalled_asset_ids(raw, "素材", 20) and not (
        {"asset_00037", "asset_00083", "asset_00151"}
        & set(_asset_repo.keyword_recalled_asset_ids(raw, "素材", 20))
    )
    for query in (
        "素材", "ClipABC", "abc", "ABC", "影", "大海", "海浪", "日出 海浪",
        "特写", "不存在XYZ", "海", "LOOP", "1", "",
    ):
        assert _asset_repo.keyword_recalled_asset_ids(raw, query, 20) == (
            _qin565_legacy_keyword_ids(query, 20)
        ), query


def test_qin565_snapshot_and_root_cache_invalidate_on_write(tmp_path: Path) -> None:
    """注册表 / 允许根写入后，只读快照与停用根缓存必须立即失效。"""
    raw = _qin565_registry(10)
    _storage.save(_asset_repo.NS_REGISTRY, raw)
    assert len(_asset_repo.readonly_registry_snapshot()["assets"]) == 10
    # 同名字节数改写：只靠文件大小无法区分，必须按 mtime_ns / inode 失效。
    raw["assets"]["asset_00000"]["name"] = "ZZZZZZZ_00000.mp4"
    _storage.save(_asset_repo.NS_REGISTRY, raw)
    assert (
        _asset_repo.readonly_registry_snapshot()["assets"]["asset_00000"]["name"]
        == "ZZZZZZZ_00000.mp4"
    )

    _qin565_persist_inactive_root(tmp_path)
    assert _asset_repo._removed_root_ids_for_search() == {"root_removed"}
    _storage.save(
        "allowed_roots",
        {"schema_version": 1, "revision": 3, "roots": [], "system_aliases": {}},
    )
    assert _asset_repo._removed_root_ids_for_search() == set()


def test_qin565_shot_search_reads_registry_at_most_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AC3：单请求不得再逐候选资产反序列化整份注册表（确定性反回归闸门）。"""
    from fastapi.testclient import TestClient

    from gw.api.app import create_app

    _storage.save(_asset_repo.NS_REGISTRY, _qin565_registry(QIN565_ASSET_COUNT))
    _qin565_persist_inactive_root(tmp_path)
    _asset_repo.rebuild_search_index()
    client = TestClient(create_app())
    # 预热快照缓存后再计数，模拟稳态连续查询。
    warm = client.get(
        QIN565_SHOT_API, params={"q": QIN565_QUERIES[0]}, headers=QIN565_AUTH
    )
    assert warm.status_code == 200, warm.text

    original_load = _storage.load
    registry_reads: list[str] = []

    def counting_load(namespace: str, default: object = None) -> object:
        if namespace == _asset_repo.NS_REGISTRY:
            registry_reads.append(namespace)
        return original_load(namespace, default)

    monkeypatch.setattr(_storage, "load", counting_load)
    for query in QIN565_QUERIES[:5]:
        response = client.get(
            QIN565_SHOT_API, params={"q": query, "limit": 20}, headers=QIN565_AUTH
        )
        assert response.status_code == 200, response.text
    assert len(registry_reads) <= 1, registry_reads


def test_qin565_shot_search_latency_at_scale(tmp_path: Path) -> None:
    """AC1/AC3：20 句 × 3 轮同口径基准；默认设稳健回归阈值，严格模式钉 50ms。"""
    from fastapi.testclient import TestClient

    from gw.api.app import create_app

    _storage.save(_asset_repo.NS_REGISTRY, _qin565_registry(QIN565_ASSET_COUNT))
    _qin565_persist_inactive_root(tmp_path)
    rebuilt = _asset_repo.rebuild_search_index()
    assert rebuilt["documents"] >= 5000
    assert rebuilt["shots"] >= 15000
    client = TestClient(create_app())
    for query in QIN565_QUERIES:
        assert client.get(
            QIN565_SHOT_API, params={"q": query, "limit": 20}, headers=QIN565_AUTH
        ).status_code == 200

    rounds: list[dict] = []
    for round_index in range(3):
        samples: list[float] = []
        for query in QIN565_QUERIES:
            started = time.perf_counter_ns()
            response = client.get(
                QIN565_SHOT_API,
                params={"q": query, "limit": 20},
                headers=QIN565_AUTH,
            )
            samples.append((time.perf_counter_ns() - started) / 1_000_000)
            assert response.status_code == 200, response.text
        rounds.append(
            {
                "round": round_index + 1,
                "p50": _nearest_rank_percentile(samples, 0.50),
                "p95": _nearest_rank_percentile(samples, 0.95),
                "max": max(samples),
            }
        )
    report = {
        "measurement_scope": "GET /api/asset-registry/search/shots; 20 queries x 3 rounds; no embedding inference",
        "documents": rebuilt["documents"],
        "shots": rebuilt["shots"],
        "rounds": rounds,
        "strict_threshold_ms": 50.0,
        "regression_threshold_ms": 250.0,
        "host": {"os": platform.platform(), "python": platform.python_version()},
    }
    print("QIN565_BENCHMARK_JSON=" + json.dumps(report, ensure_ascii=False))
    evidence_dir = os.getenv("GW_C18_EVIDENCE_DIR")
    if evidence_dir:
        destination = Path(evidence_dir)
        destination.mkdir(parents=True, exist_ok=True)
        (destination / "qin565-shot-search-latency.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )

    threshold = (
        50.0
        if os.getenv("GW_QIN565_STRICT_LATENCY") == "1"
        else 250.0
    )
    assert max(round_data["p95"] for round_data in rounds) <= threshold


def test_percentile_uses_nearest_rank() -> None:
    assert _nearest_rank_percentile(list(range(1, 21)), 0.95) == 19
