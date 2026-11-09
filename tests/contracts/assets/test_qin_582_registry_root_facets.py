# -*- coding: utf-8 -*-
"""QIN-582（GitHub #674）：来源根挂载探测的请求内复用与有界缓存契约。

夹具全部内存态：注册表、允许根状态与挂载探测都是可控替身，不访问真实素材库。
覆盖 AC1 请求内去重 / 有界缓存、AC2 延迟、AC3 语义与失效、AC4 并发一致性。
"""

from __future__ import annotations

import copy
import statistics
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from gw.asset_registry import index_jobs
from gw.asset_registry import repository as repo
from gw.core import storage
from gw.settings.service import default_storage_settings_service

ROOT_COUNT = 5
ASSET_COUNT = 1000
PROBE_COST_SECONDS = 0.01


class _ReadOnlyState:
    """只读内存状态；与 ``JsonState.read`` 同形，但不落盘。"""

    def __init__(self, data: dict) -> None:
        self.data = data

    def read(self) -> dict:
        return self.data


class _RootProbe:
    """可控挂载探测：计数 + 固定开销，用于放大重复探测链的差异。"""

    def __init__(self) -> None:
        self.calls = 0
        self.available = True
        self._lock = threading.Lock()

    def __call__(self, raw_path: str, *, timeout: float = 0.75) -> dict:
        with self._lock:
            self.calls += 1
            available = self.available
        time.sleep(PROBE_COST_SECONDS)
        return {
            "available": available,
            "reason": "" if available else "MOUNT_OFFLINE",
            "message": "" if available else "目录暂时离线",
        }

    def reset(self) -> None:
        with self._lock:
            self.calls = 0


def _asset(asset_id: str, source_id: str) -> dict:
    return {
        "asset_id": asset_id,
        "name": f"素材-{asset_id}",
        "kind": "image",
        "storage_root_id": source_id,
        "root_id": source_id,
        "metadata": {},
        "created_at": "2026-11-09T00:00:00Z",
    }


@pytest.fixture()
def roots_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict:
    """1000 条资产 / 5 个来源别名；所有磁盘探测与状态读取都换成可控替身。"""
    index_jobs._ROOT_FACETS_CACHE.update(key=None, value=None, expires_at=0.0)
    monkeypatch.setattr(index_jobs, "ROOT_FACETS_CACHE_TTL_SECONDS", 30.0)

    raw = repo._blank()
    raw["assets"] = {
        f"a{index:04d}": _asset(f"a{index:04d}", f"root-{index % ROOT_COUNT}")
        for index in range(ASSET_COUNT)
    }
    raw["root_aliases"] = {}
    monkeypatch.setattr(repo, "state", lambda: _ReadOnlyState(raw))
    monkeypatch.setattr(repo, "readonly_registry_snapshot", lambda: raw)

    allowed_state = {
        "schema_version": 1,
        "revision": 1,
        "roots": [
            {
                "id": f"root-{index}",
                "path": str(tmp_path / f"root-{index}"),
                "active": True,
                "aliases": [],
            }
            for index in range(ROOT_COUNT)
        ],
        "system_aliases": {},
    }
    monkeypatch.setattr(
        storage, "_allowed_roots_state", lambda: copy.deepcopy(allowed_state)
    )

    settings_state = {
        "revision": 1,
        "configured": True,
        "dirs": {},
        "defaults": {},
        "extra_local_entries": [],
        "local_library_names": {},
        "local_library_order": [],
        "local_library_read_only": {},
    }
    monkeypatch.setattr(
        default_storage_settings_service._state, "read", lambda: dict(settings_state)
    )

    aliases = {}
    for index in range(ROOT_COUNT):
        root_path = tmp_path / f"root-{index}"
        root_path.mkdir()
        source_id = f"root-{index}"
        aliases[source_id] = {
            "root_id": source_id,
            "root": root_path,
            "prefix": "",
            "storage_root_id": source_id,
            "name": f"测试库-{index}",
            "availability": "available",
            "availability_reason": "",
            "availability_message": "",
        }
    counts = {"roots": 0, "root_facets": 0}
    counts_lock = threading.Lock()

    def counted_roots():
        with counts_lock:
            counts["roots"] += 1
        return {}, copy.deepcopy(aliases)

    monkeypatch.setattr(index_jobs, "_roots", counted_roots)

    original_root_facets = index_jobs.root_facets

    def counted_root_facets():
        with counts_lock:
            counts["root_facets"] += 1
        return original_root_facets()

    monkeypatch.setattr(index_jobs, "root_facets", counted_root_facets)

    probe = _RootProbe()
    monkeypatch.setattr(storage, "probe_mount", probe)

    def reset_counters() -> None:
        with counts_lock:
            counts.update(roots=0, root_facets=0)
        probe.reset()

    return {
        "raw": raw,
        "aliases": aliases,
        "allowed_state": allowed_state,
        "settings_state": settings_state,
        "counts": counts,
        "probe": probe,
        "reset": reset_counters,
    }


def _p95(samples: list[float]) -> float:
    ordered = sorted(samples)
    return ordered[max(0, (len(ordered) * 95 + 99) // 100 - 1)]


def _timed(fn, rounds: int = 20) -> list[float]:
    samples = []
    for _ in range(rounds):
        started = time.perf_counter()
        fn()
        samples.append((time.perf_counter() - started) * 1000)
    return samples


# --------------------------------------------------------------------------
# AC1：请求内复用 + 缓存命中零探测
# --------------------------------------------------------------------------

def test_list_assets_only_resolves_roots_once(roots_env: dict) -> None:
    env = roots_env
    env["reset"]()
    first = repo.list_assets(100, 0, None, None, None)
    assert first["total"] == ASSET_COUNT
    assert len(first["assets"]) == 100
    # 改造前一次列表请求要跑 3 次 root_facets（3 遍完整探测链）。
    assert env["counts"]["root_facets"] == 1
    assert env["counts"]["roots"] == 1
    assert env["probe"].calls == ROOT_COUNT

    env["reset"]()
    second = repo.list_assets(100, 0, None, None, None)
    assert second["total"] == ASSET_COUNT
    assert env["counts"]["root_facets"] == 1  # count 仍每次实时重算
    assert env["counts"]["roots"] == 0        # 缓存命中，零次路径解析
    assert env["probe"].calls == 0            # 缓存命中，零次磁盘探测


def test_facets_request_uses_cached_snapshot(roots_env: dict) -> None:
    env = roots_env
    env["reset"]()
    first = repo.facets()
    assert len(first["roots"]) == ROOT_COUNT
    assert env["counts"]["roots"] == 1
    env["reset"]()
    repo.facets()
    assert env["counts"]["roots"] == 0
    assert env["probe"].calls == 0


def test_source_display_names_accepts_shared_facets(roots_env: dict) -> None:
    env = roots_env
    facets = index_jobs.root_facets()
    env["reset"]()
    names = repo._source_display_names(facets)
    assert names["root-0"] == "测试库-0"
    assert env["counts"]["root_facets"] == 0
    assert env["counts"]["roots"] == 0


# --------------------------------------------------------------------------
# AC1 / AC3：count 实时、缓存失效、TTL
# --------------------------------------------------------------------------

def test_facet_counts_stay_live_on_cache_hit(roots_env: dict) -> None:
    env = roots_env
    before = {item["id"]: item["count"] for item in index_jobs.root_facets()}
    env["raw"]["assets"]["extra"] = _asset("extra", "root-0")
    env["reset"]()
    after = {item["id"]: item["count"] for item in index_jobs.root_facets()}
    assert after["root-0"] == before["root-0"] + 1
    assert env["counts"]["roots"] == 0
    assert env["probe"].calls == 0


def test_allowed_roots_revision_invalidates_cache(roots_env: dict) -> None:
    env = roots_env
    assert all(item["availability"] == "available" for item in index_jobs.root_facets())
    env["allowed_state"]["revision"] = 2
    env["probe"].available = False
    env["reset"]()
    facets = index_jobs.root_facets()
    assert env["counts"]["roots"] == 1, "允许根 revision 变化必须立即重算，不等 TTL"
    assert all(item["availability"] == "offline" for item in facets)


def test_storage_settings_revision_invalidates_cache(roots_env: dict) -> None:
    env = roots_env
    index_jobs.root_facets()
    env["settings_state"]["revision"] = 2
    env["reset"]()
    index_jobs.root_facets()
    assert env["counts"]["roots"] == 1


def test_ttl_expiry_reprobes_mount(roots_env: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    env = roots_env
    monkeypatch.setattr(index_jobs, "ROOT_FACETS_CACHE_TTL_SECONDS", 0.0)
    env["reset"]()
    index_jobs.root_facets()
    index_jobs.root_facets()
    assert env["counts"]["roots"] == 2, "TTL 到期必须重新探测，避免离线根永久显示在线"


# --------------------------------------------------------------------------
# AC2：延迟预算与相对基线
# --------------------------------------------------------------------------

def test_warm_latency_beats_uncached_baseline(
    roots_env: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    env = roots_env
    repo.list_assets(100, 0, None, None, None)  # 预热
    warm = _timed(lambda: repo.list_assets(100, 0, None, None, None))
    assert _p95(warm) <= 200.0, f"预热后 p95 应 ≤200ms，实测 {_p95(warm):.1f}ms"

    # 同夹具下的改造前基线：TTL=0 强制每次请求重跑完整探测链，即「无缓存」口径。
    # 缓存一旦失效，warm 与基线会趋同，倍率断言必红（AC6 负向验证闸门）。
    monkeypatch.setattr(index_jobs, "ROOT_FACETS_CACHE_TTL_SECONDS", 0.0)
    index_jobs._ROOT_FACETS_CACHE.update(key=None, value=None, expires_at=0.0)
    baseline = _timed(lambda: repo.list_assets(100, 0, None, None, None))
    ratio = statistics.median(baseline) / max(statistics.median(warm), 0.001)
    assert ratio >= 5.0, f"相对基线应提速 ≥5 倍，实测 {ratio:.1f} 倍"


# --------------------------------------------------------------------------
# AC3 / AC4：停用根语义、并发一致性
# --------------------------------------------------------------------------

def test_removed_root_ids_without_probe_chain(roots_env: dict) -> None:
    env = roots_env
    env["allowed_state"]["roots"][0]["active"] = False
    env["reset"]()
    removed = repo._removed_root_ids()
    assert removed == {"root-0"}
    assert env["probe"].calls == 0, "停用根判定不得触发磁盘探测"
    page = repo.list_assets(100, 0, None, None, None)
    assert page["total"] == ASSET_COUNT - ASSET_COUNT // ROOT_COUNT


def test_concurrent_first_screen_is_single_flight(roots_env: dict) -> None:
    env = roots_env
    env["reset"]()
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(
            pool.map(lambda _: repo.list_assets(100, 0, None, None, None), range(8))
        )
    assert env["counts"]["roots"] == 1, "8 并发只应解析一次来源根"
    assert env["probe"].calls == ROOT_COUNT, "single-flight 不得让并发抢光探测槽"
    assert all(result["total"] == ASSET_COUNT for result in results)
    availability = {
        asset.get("root_availability")
        for result in results
        for asset in result["assets"]
    }
    assert availability == {"available"}
