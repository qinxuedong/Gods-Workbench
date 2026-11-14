# -*- coding: utf-8 -*-
"""QIN-600 生成元数据读取基准（AC7）。

CI 默认跑 200 张合成图保证可重复；`GW_QIN600_FULL=1` 切到票面的 10,000 张口径。
本文件只测「单文件元数据读取」这一项（票面 p95 ≤ 20 ms）；全量 reindex 的总耗时
增幅由 `.local/qa/QIN-600/reindex_bench.py` 在改前/改后同一台机器上对照，数据贴 PR。

样本全部由本文件构造，不引用真实素材库（AC9）。
"""

from __future__ import annotations

import json
import math
import os
import statistics
import struct
import time
import zlib
from pathlib import Path

import pytest

from gw.asset_registry import generation_metadata as gm

FULL = os.environ.get("GW_QIN600_FULL") == "1"
ASSETS = 10_000 if FULL else 200
BIG_BLOCKS = 50 if FULL else 5
P95_LIMIT_MS = float(os.environ.get("GW_QIN600_P95_LIMIT_MS", "20"))
REPORT_DIR = os.environ.get("GW_QIN600_REPORT_DIR")

A1111_TEXT = (
    b"benchmark prompt, cinematic lighting\n"
    b"Negative prompt: blurry\n"
    b"Steps: 28, Sampler: Euler a, CFG scale: 7, Seed: 1, Size: 1024x1024"
)


def _chunk(kind: bytes, data: bytes) -> bytes:
    return (
        struct.pack(">I", len(data))
        + kind
        + data
        + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
    )


def _png(text: bytes | None) -> bytes:
    parts = [
        b"\x89PNG\r\n\x1a\n",
        _chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)),
    ]
    if text is not None:
        parts.append(_chunk(b"tEXt", b"parameters\x00" + text))
    parts.append(_chunk(b"IDAT", zlib.compress(b"\x00\x00\x00\x00")))
    parts.append(_chunk(b"IEND", b""))
    return b"".join(parts)


def _percentile(samples: list[float], fraction: float) -> float:
    ordered = sorted(samples)
    return ordered[max(0, math.ceil(len(ordered) * fraction) - 1)]


@pytest.fixture(scope="module")
def library(tmp_path_factory):
    root = tmp_path_factory.mktemp("qin600-bench")
    plain = _png(None)
    with_text = _png(A1111_TEXT)
    big = _png(A1111_TEXT + b"\n" + b"x" * (gm.MAX_TEXT_BLOCK_BYTES + 1024))
    big_every = max(1, ASSETS // BIG_BLOCKS) if BIG_BLOCKS else 0
    for index in range(ASSETS):
        if BIG_BLOCKS and index % big_every == 0 and index // big_every < BIG_BLOCKS:
            payload = big
        elif index % 10 < 3:  # 30% 带文本块
            payload = with_text
        else:
            payload = plain
        (root / f"bench_{index:05d}.png").write_bytes(payload)
    return root


def test_generation_metadata_read_latency(library):
    """AC7：单文件元数据读取 p95 ≤ 20 ms，且不对无元数据文件写字段。"""
    files = sorted(library.glob("*.png"))
    assert len(files) == ASSETS

    timings: list[float] = []
    with_metadata = 0
    truncated = 0
    for path in files:
        started = time.perf_counter()
        generation, warnings = gm.extract_generation(path)
        timings.append((time.perf_counter() - started) * 1000)
        assert warnings == 0, path.name
        if generation is not None:
            with_metadata += 1
            if generation["truncated"]:
                truncated += 1

    report = {
        "scale": ASSETS,
        "full": FULL,
        "p50_ms": round(_percentile(timings, 0.50), 3),
        "p95_ms": round(_percentile(timings, 0.95), 3),
        "p99_ms": round(_percentile(timings, 0.99), 3),
        "max_ms": round(max(timings), 3),
        "mean_ms": round(statistics.fmean(timings), 3),
        "with_metadata": with_metadata,
        "truncated": truncated,
        "stored_limit_bytes": gm.MAX_STORED_PROMPT_BYTES,
        "read_limit_bytes": gm.MAX_TEXT_BLOCK_BYTES,
    }
    print("\n[qin600-bench] " + json.dumps(report, ensure_ascii=False))
    if REPORT_DIR:
        destination = Path(REPORT_DIR)
        destination.mkdir(parents=True, exist_ok=True)
        (destination / f"generation-read-{ASSETS}.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )

    # 规模校验：30% 带文本块（大块样本全部落在这一档），大块样本全部截断。
    assert with_metadata == ASSETS * 3 // 10
    assert truncated >= min(BIG_BLOCKS, ASSETS)
    # 读取上限与落盘上限分离，且落盘更长的那一档仍被截断。
    assert gm.MAX_STORED_PROMPT_BYTES < gm.MAX_TEXT_BLOCK_BYTES
    assert report["p95_ms"] <= P95_LIMIT_MS, (
        f"单文件读取 p95 {report['p95_ms']} ms 超过 {P95_LIMIT_MS} ms"
    )


def test_unsupported_suffix_is_skipped_without_reading(library, monkeypatch, tmp_path):
    """非图片后缀不打开文件、不产生 warning（AC4 边界）。"""
    note = tmp_path / "note.txt"
    note.write_text("not an image", encoding="utf-8")

    def explode(*_args, **_kwargs):
        raise AssertionError("非图片后缀不得打开文件")

    monkeypatch.setattr(Path, "open", explode)
    assert gm.extract_generation(note) == (None, 0)
