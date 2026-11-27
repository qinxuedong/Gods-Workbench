"""QIN-675：治理建议 5K 快照性能、缓存隔离与向量候选回归。"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import hashlib
import math
import random
import struct
import threading
import time

import pytest

from gw.asset_registry import dedup_confirm, governance, repository as repo
from gw.asset_registry.governance_rules import group_similar_names


def _synthetic_snapshot(count: int, revision: int) -> dict:
    assets = {}
    for index in range(count):
        token = hashlib.sha256(index.to_bytes(4, "little")).hexdigest()[:24]
        name = f"asset_{token}.mp4"
        asset_id = f"ast_qin675_{index:05d}"
        assets[asset_id] = {
            "asset_id": asset_id,
            "name": name,
            "display_path": f"nas/root/{name}",
            "size_bytes": index + 1,
            "archived": False,
            "metadata": {},
        }
    return {"revision": revision, "assets": assets, "relations": [], "tags": {}}


def _empty_index_snapshot() -> dict:
    return {
        "shot_duplicate_groups": [],
        "vector_duplicate_pairs": [],
        "index_available": False,
        "vector_available": False,
    }


def test_qin675_five_thousand_snapshot_cold_and_hot_budget(monkeypatch, tmp_path):
    """5K 冷算 <300ms、热缓存 <20ms，且建议 GET 不碰任何资产物理路径。"""
    snapshot = _synthetic_snapshot(5000, 675501)
    monkeypatch.setattr(repo, "readonly_registry_snapshot", lambda: snapshot)
    monkeypatch.setattr(repo, "_removed_root_ids", lambda: set())
    monkeypatch.setattr(governance.storage, "data_root", lambda: tmp_path)
    monkeypatch.setattr(governance, "_load_index_snapshot", _empty_index_snapshot)
    with governance._GOVERNANCE_CACHE_LOCK:
        governance._GOVERNANCE_CACHE.clear()

    def forbidden(*_args, **_kwargs):
        raise AssertionError("治理建议 GET 不得逐资产解析或探测物理路径")

    started = time.perf_counter()
    with monkeypatch.context() as patch:
        patch.setattr(dedup_confirm, "resolve_asset_media_path", forbidden)
        patch.setattr("pathlib.Path.is_file", forbidden)
        patch.setattr("pathlib.Path.lstat", forbidden)
        cold = governance.build_suggestions(visibility_key="qin675-benchmark-admin")
    cold_ms = (time.perf_counter() - started) * 1000

    started = time.perf_counter()
    hot = governance.build_suggestions(visibility_key="qin675-benchmark-admin")
    hot_ms = (time.perf_counter() - started) * 1000

    assert cold["revision"] == 675501
    assert hot["suggestions"] == cold["suggestions"]
    assert cold_ms < 300, f"5K 冷算预算超限：{cold_ms:.1f}ms"
    assert hot_ms < 20, f"5K 热缓存预算超限：{hot_ms:.1f}ms"


@pytest.mark.parametrize("prefix", ["asset_", "some_long_common_prefix_asset_"])
def test_qin675_sequential_names_cold_budget(monkeypatch, prefix):
    snapshot = _synthetic_snapshot(5000, 675504)
    for index, asset in enumerate(snapshot["assets"].values()):
        asset["name"] = f"{prefix}{index:04d}.png"
        asset["display_path"] = f"nas/root/{asset['name']}"
    monkeypatch.setattr(repo, "readonly_registry_snapshot", lambda: snapshot)
    monkeypatch.setattr(repo, "_removed_root_ids", lambda: set())
    monkeypatch.setattr(governance, "_load_index_snapshot", _empty_index_snapshot)
    with governance._GOVERNANCE_CACHE_LOCK:
        governance._GOVERNANCE_CACHE.clear()
    start = time.perf_counter()
    result = governance.build_suggestions(visibility_key="qin675-sequential")
    elapsed = (time.perf_counter() - start) * 1000
    assert result["suggestions"]["similar_names"]
    assert elapsed < 300, f"5K 顺序名称冷算超限：{elapsed:.1f}ms"


def test_qin675_cache_invalidates_for_index_and_removed_roots(monkeypatch, tmp_path):
    snapshot = _synthetic_snapshot(2, 675505)
    assets = list(snapshot["assets"].values())
    assets[0]["size_bytes"] = assets[1]["size_bytes"]
    assets[0]["root_id"] = "removed-root"
    roots = set()
    index = {"index_available": False}
    monkeypatch.setattr(repo, "readonly_registry_snapshot", lambda: snapshot)
    monkeypatch.setattr(repo, "_removed_root_ids", lambda: roots)
    monkeypatch.setattr(repo, "_is_removed_asset", lambda asset, ids: asset.get("root_id") in ids)
    monkeypatch.setattr(governance.storage, "data_root", lambda: tmp_path)
    monkeypatch.setattr(governance, "_load_index_snapshot", lambda: {**_empty_index_snapshot(), **index})
    with governance._GOVERNANCE_CACHE_LOCK:
        governance._GOVERNANCE_CACHE.clear()
    first = governance.build_suggestions(visibility_key="index-root")
    assert "search_index_unavailable" in first["data_gaps"]
    assert first["suggestions"]["duplicate_files"]
    (tmp_path / repo.ASSET_SEARCH_INDEX_FILENAME).write_bytes(b"index")
    index["index_available"] = True
    second = governance.build_suggestions(visibility_key="index-root")
    assert "search_index_unavailable" not in second["data_gaps"]
    roots.add("removed-root")
    third = governance.build_suggestions(visibility_key="index-root")
    assert not third["suggestions"]["duplicate_files"]


def test_qin675_cache_isolated_by_principal_and_revision(monkeypatch):
    snapshot = _synthetic_snapshot(2, 675502)
    monkeypatch.setattr(repo, "readonly_registry_snapshot", lambda: snapshot)
    monkeypatch.setattr(repo, "_removed_root_ids", lambda: set())
    monkeypatch.setattr(governance, "_load_index_snapshot", _empty_index_snapshot)
    with governance._GOVERNANCE_CACHE_LOCK:
        governance._GOVERNANCE_CACHE.clear()
    original = governance._build_suggestions_from_snapshot
    calls = 0

    def counted(raw):
        nonlocal calls
        calls += 1
        return original(raw)

    monkeypatch.setattr(governance, "_build_suggestions_from_snapshot", counted)
    governance.build_suggestions(visibility_key="principal-a:admin")
    governance.build_suggestions(visibility_key="principal-a:admin")
    governance.build_suggestions(visibility_key="principal-b:admin")
    snapshot["revision"] += 1
    governance.build_suggestions(visibility_key="principal-a:admin")
    assert calls == 3


def test_qin675_same_key_cold_build_is_single_flight(monkeypatch):
    snapshot = _synthetic_snapshot(2, 675503)
    monkeypatch.setattr(repo, "readonly_registry_snapshot", lambda: snapshot)
    monkeypatch.setattr(repo, "_removed_root_ids", lambda: set())
    monkeypatch.setattr(governance, "_load_index_snapshot", _empty_index_snapshot)
    with governance._GOVERNANCE_CACHE_LOCK:
        governance._GOVERNANCE_CACHE.clear()
    original = governance._build_suggestions_from_snapshot
    calls = 0
    calls_lock = threading.Lock()

    def delayed(raw):
        nonlocal calls
        with calls_lock:
            calls += 1
        time.sleep(0.05)
        return original(raw)

    monkeypatch.setattr(governance, "_build_suggestions_from_snapshot", delayed)
    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(
            pool.map(
                lambda _index: governance.build_suggestions(
                    visibility_key="principal-single-flight:admin"
                ),
                range(6),
            )
        )
    assert calls == 1
    assert all(item["revision"] == 675503 for item in results)


def test_qin675_fast_similar_name_groups_match_existing_rule():
    rng = random.Random(675)  # noqa: S311 - 仅生成固定回归样本。
    names = [
        "clip_01.mp4",
        "clip_01 (1).mp4",
        "clip_02.mp4",
        "CLIP_02.mp4",
        "镜头甲_v001.mov",
        "镜头甲_v002.mov",
        "a",
        "ab",
        "abc",
        "abd",
    ]
    for _ in range(80):
        names.append("".join(rng.choice("abcxyz0123") for _ in range(rng.randint(3, 18))) + ".mp4")
    assert governance._group_similar_display_paths(names) == group_similar_names(names)


def test_qin675_vector_duplicates_use_nearest_candidate_queries():
    vectors = [
        (1.0, 0.0),
        (0.9998, 0.02),
        (0.0, 1.0),
        (-1.0, 0.0),
    ]

    class FakeVectorDb:
        def __init__(self):
            self.queries = 0

        def execute(self, sql, params=()):
            if "JOIN c18_documents" in sql:
                rows = []
                for index, vector in enumerate(vectors, start=1):
                    rows.append(
                        {
                            "vector_rowid": index,
                            "asset_id": f"asset-{index}",
                            "embedding": struct.pack("<2f", *vector),
                        }
                    )
                return _FakeRows(rows)
            self.queries += 1
            query = struct.unpack("<2f", params[0])
            limit = params[1]
            nearest = []
            for rowid, vector in enumerate(vectors, start=1):
                distance = math.sqrt(sum((a - b) ** 2 for a, b in zip(query, vector, strict=True)))
                nearest.append({"rowid": rowid, "distance": distance})
            nearest.sort(key=lambda row: (row["distance"], row["rowid"]))
            return _FakeRows(nearest[:limit])

    class _FakeRows(list):
        def fetchall(self):
            return self

    class FakeEngine:
        def __init__(self):
            self._db = FakeVectorDb()

    engine = FakeEngine()
    pairs = governance._vector_near_duplicates(engine)
    assert pairs == [
        {"left_asset_id": "asset-1", "right_asset_id": "asset-2", "similarity": 0.9998}
    ]
    assert engine._db.queries == len(vectors)
