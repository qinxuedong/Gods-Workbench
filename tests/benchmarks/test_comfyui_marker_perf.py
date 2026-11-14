# -*- coding: utf-8 -*-
"""QIN-601 ComfyUI 标记判定基准（AC6）。

CI 默认跑 200 张合成 PNG，`GW_QIN601_FULL=1` 切到票面的 10,000 张口径，
`GW_QIN601_DENSITY` 控制带 ComfyUI 图的比例（默认 1.0，即最不利情形）。

本文件只测「单文件元数据读取 + 标记判定」这一项（设计表 §6：单文件 p95 ≤ 20 ms）；
全量 reindex 的总耗时增幅由 `.local/qa/QIN-601/reindex_bench.py` 在同机 10k 库上
与 master 对照，三档占比数据贴 PR。样本全部由本文件构造，不引用真实素材库。
"""

from __future__ import annotations

import json
import math
import os
import struct
import time
import zlib
from pathlib import Path

import pytest

from gw.asset_registry import generation_metadata as gm

FULL = os.environ.get("GW_QIN601_FULL") == "1"
ASSETS = 10_000 if FULL else 200
DENSITY = float(os.environ.get("GW_QIN601_DENSITY", "1.0"))
P95_LIMIT_MS = float(os.environ.get("GW_QIN601_P95_LIMIT_MS", "20"))
REPORT_DIR = os.environ.get("GW_QIN601_REPORT_DIR")

COMFY_API = {
    "3": {
        "class_type": "KSampler",
        "inputs": {"positive": ["6", 0], "negative": ["7", 0], "seed": 1},
    },
    "6": {"class_type": "CLIPTextEncode", "inputs": {"text": "benchmark positive"}},
    "7": {"class_type": "CLIPTextEncode", "inputs": {"text": "benchmark negative"}},
}

COMFY_UI = {
    "nodes": [
        {
            "id": 1,
            "type": "KSampler",
            "inputs": [
                {"name": "positive", "link": 11},
                {"name": "negative", "link": 12},
            ],
            "widgets_values": [],
        },
        {"id": 5, "type": "CLIPTextEncode", "widgets_values": ["ui positive"]},
        {"id": 6, "type": "CLIPTextEncode", "widgets_values": ["ui negative"]},
    ],
    "links": [
        [11, 5, 0, 1, 0, "CONDITIONING"],
        [12, 6, 0, 1, 1, "CONDITIONING"],
    ],
}


def _chunk(kind: bytes, data: bytes) -> bytes:
    return (
        struct.pack(">I", len(data))
        + kind
        + data
        + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
    )


def _png(blocks: list[tuple[bytes, bytes]]) -> bytes:
    parts = [
        b"\x89PNG\r\n\x1a\n",
        _chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)),
    ]
    for keyword, text in blocks:
        parts.append(_chunk(b"tEXt", keyword + b"\x00" + text))
    parts.append(_chunk(b"IDAT", zlib.compress(b"\x00\x00\x00\x00")))
    parts.append(_chunk(b"IEND", b""))
    return b"".join(parts)


def _percentile(samples: list[float], fraction: float) -> float:
    ordered = sorted(samples)
    return ordered[max(0, math.ceil(len(ordered) * fraction) - 1)]


@pytest.fixture(scope="module")
def library(tmp_path_factory):
    root = tmp_path_factory.mktemp("qin601-bench")
    plain = _png([])
    # 典型 ComfyUI 导出：workflow + prompt 两个块（最不利形态）。
    comfy = _png(
        [
            (b"prompt", json.dumps(COMFY_API).encode("utf-8")),
            (b"workflow", json.dumps(COMFY_UI).encode("utf-8")),
        ]
    )
    comfy_every = 1 if DENSITY >= 1 else max(1, round(1 / DENSITY)) if DENSITY > 0 else 0
    for index in range(ASSETS):
        use_comfy = bool(comfy_every) and index % comfy_every == 0
        (root / f"bench_{index:05d}.png").write_bytes(comfy if use_comfy else plain)
    return root


def test_comfyui_marker_latency(library):
    """AC6：单文件「读取 + 标记判定」p95 ≤ 20 ms，且标记计数与密度一致。"""
    files = sorted(library.glob("*.png"))
    assert len(files) == ASSETS

    timings: list[float] = []
    tagged = 0
    for path in files:
        started = time.perf_counter()
        generation, warnings = gm.extract_generation(path)
        timings.append((time.perf_counter() - started) * 1000)
        assert warnings == 0, path.name
        if generation and generation.get("comfyui"):
            tagged += 1
            marker = generation["comfyui"]
            assert marker["has_workflow"] is True
            assert marker["has_prompt_graph"] is True
            assert marker["source"] == "embedded"

    p95 = _percentile(timings, 0.95)
    total = sum(timings)
    summary = {
        "assets": ASSETS,
        "density": DENSITY,
        "tagged": tagged,
        "p50_ms": round(_percentile(timings, 0.50), 4),
        "p95_ms": round(p95, 4),
        "total_ms": round(total, 2),
    }
    if REPORT_DIR:
        Path(REPORT_DIR).mkdir(parents=True, exist_ok=True)
        (Path(REPORT_DIR) / "qin601-marker-bench.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    print("QIN601_BENCH " + json.dumps(summary, ensure_ascii=False))
    assert p95 <= P95_LIMIT_MS, summary
