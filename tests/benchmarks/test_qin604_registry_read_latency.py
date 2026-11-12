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

"""QIN-604 AC0 / AC5 后端读延迟基准（可复现、无本地路径依赖）。

与 Issue #714 贴出的 AC0 基线使用同一套测量口径：两档规模 × 两个端点的
p50/p95 + 「点一次文件夹」期间的 ``asset_registry`` 解析次数。正式改前/改后
对照表由同一台机器上的同一套脚本产出并贴 Issue/PR；本用例提供 CI 可复现的
等价实现与结构断言（文件未变时解析次数 ≤ 1）。

默认用确定性造数，CI 无需本机真实注册表；设置 ``GW_QIN604_REAL_REGISTRY``
可改用真实注册表只读副本。设置 ``GW_QIN604_REPORT_DIR`` 会落盘 JSON 报告，
``GW_QIN604_FULL=1`` 切换到 24 并发 × 10 轮的完整采样。
性能阈值属产品裁决，本用例不设通过线，避免硬件差异造成 CI 抖动。
"""

from __future__ import annotations

import io
import json
import math
import os
import platform
import statistics
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from gw.api.app import create_app
from gw.asset_registry import repository as asset_repo
from gw.core import storage
from gw.media import repository as media_repo

AUTH = {"Authorization": "Bearer cleanroom-test", "X-User-Role": "editor"}
PREVIEW_ID = "qin604_bench_probe"
FULL = os.environ.get("GW_QIN604_FULL") == "1"
CONCURRENCY = 24 if FULL else 8
ROUNDS = 10 if FULL else 3
PARSE_ITERATIONS = 30 if FULL else 12
THUMBNAILS_PER_CLICK = 24
SCALE_ASSETS = 5000


def _percentile(samples: list[float], q: float) -> float:
    ordered = sorted(samples)
    return ordered[math.ceil(len(ordered) * q) - 1]


def _stats(samples: list[float]) -> dict[str, Any]:
    return {
        "n": len(samples),
        "p50_ms": round(_percentile(samples, 0.50), 3),
        "p95_ms": round(_percentile(samples, 0.95), 3),
        "min_ms": round(min(samples), 3),
        "mean_ms": round(statistics.fmean(samples), 3),
    }


def _png(color: tuple[int, int, int] = (12, 34, 56)) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (320, 180), color).save(buffer, format="PNG")
    return buffer.getvalue()


def _asset(asset_id: str, source: Path) -> dict[str, Any]:
    return {
        "asset_id": asset_id,
        "id": asset_id,
        "name": f"{asset_id}.png",
        "kind": "image",
        "display_path": str(source),
        "hidden": False,
        "archived": False,
        "version": 1,
        "metadata": {},
        "tags": ["benchmark"],
        "relations": [],
        "url": "",
        "identity_status": "confirmed",
        "created_at": "2026-11-12T00:00:00Z",
        "updated_at": "2026-11-12T00:00:00Z",
    }


def _fixtures(tmp_path: Path) -> dict[str, dict[str, Any]]:
    source = tmp_path / "roots" / "bench-source.png"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_bytes(_png())
    real_source = os.environ.get("GW_QIN604_REAL_REGISTRY")
    if real_source:
        real = json.loads(Path(real_source).read_text(encoding="utf-8"))
        real["assets"] = dict(real["assets"])
    else:
        real = asset_repo._blank()
        real["assets"] = {
            f"bench_{index:05d}": _asset(f"bench_{index:05d}", source)
            for index in range(1005)
        }
        real["revision"] = 7
    real["assets"][PREVIEW_ID] = _asset(PREVIEW_ID, source)

    scaled = dict(real)
    scaled["assets"] = {
        f"bench_scale_{index:05d}": _asset(f"bench_scale_{index:05d}", source)
        for index in range(SCALE_ASSETS)
    }
    scaled["assets"][PREVIEW_ID] = _asset(PREVIEW_ID, source)
    scaled["revision"] = int(real.get("revision", 1)) + 1
    return {"real": real, "5000": scaled}


def _write_registry(payload: dict[str, Any]) -> None:
    storage.save("asset_registry", payload)
    asset_repo._REGISTRY_SNAPSHOT_CACHE = None
    asset_repo._SNAPSHOT_PATHS_CACHE = None


def _measure_scale(name: str, payload: dict[str, Any], client: TestClient) -> dict[str, Any]:
    _write_registry(payload)
    media_repo.patch_thumbnail_settings(
        {
            "mode": "source",
            "directory": "",
            "cache_folder": "god-cache",
            "width": 256,
            "format": "webp",
        }
    )
    media_repo.reset_preview_cache_stats()
    result: dict[str, Any] = {"asset_count": len(payload["assets"])}

    storage.load("asset_registry")
    parse_samples: list[float] = []
    for _ in range(PARSE_ITERATIONS):
        started = time.perf_counter()
        value = storage.load("asset_registry")
        parse_samples.append((time.perf_counter() - started) * 1000)
        assert len(value["assets"]) == len(payload["assets"])
    result["single_parse_ms"] = _stats(parse_samples)

    original_load = storage.load
    counter = {"total": 0}
    phase = {"name": "unknown"}

    def counting_load(namespace: str, default: Any = None) -> Any:
        if namespace == "asset_registry":
            counter["total"] += 1
        return original_load(namespace, default)

    storage.load = counting_load
    try:
        sequence = [
            ("status", lambda: client.get("/api/asset-registry/status", headers=AUTH)),
            ("folders", lambda: client.get("/api/asset-registry/folders", headers=AUTH)),
            ("assets", lambda: client.get("/api/asset-registry/assets", headers=AUTH, params={"limit": "60"})),
        ]
        for _ in range(5):
            sequence.append(
                ("project_count", lambda: client.get(
                    "/api/asset-registry/assets", headers=AUTH,
                    params={"limit": "1", "project_id": "__all__"},
                ))
            )
        for _ in range(THUMBNAILS_PER_CLICK):
            sequence.append(
                ("media_preview", lambda: client.get(
                    "/api/media-preview", headers=AUTH,
                    params={"asset_id": PREVIEW_ID, "w": "256"},
                ))
            )
        for label, action in sequence:
            phase["name"] = label
            response = action()
            assert response.status_code == 200, (label, response.status_code, response.text[:200])
    finally:
        storage.load = original_load
    result["folder_click"] = {
        "request_count": len(sequence),
        "asset_registry_parse_count": counter["total"],
    }

    assets_params = {"limit": "60", "view": "grid"}

    def timed_assets(_: int) -> float:
        started = time.perf_counter()
        response = client.get("/api/asset-registry/assets", headers=AUTH, params=assets_params)
        elapsed = (time.perf_counter() - started) * 1000
        assert response.status_code == 200
        return elapsed

    for _ in range(3):
        timed_assets(0)
    with ThreadPoolExecutor(max_workers=CONCURRENCY) as pool:
        result["assets_endpoint"] = {
            **_stats(list(pool.map(timed_assets, range(CONCURRENCY * ROUNDS)))),
            "concurrency": CONCURRENCY,
        }

    preview_params = {"asset_id": PREVIEW_ID, "w": "256"}
    for _ in range(3):
        assert client.get("/api/media-preview", headers=AUTH, params=preview_params).status_code == 200

    def timed_preview(_: int) -> float:
        started = time.perf_counter()
        response = client.get("/api/media-preview", headers=AUTH, params=preview_params)
        elapsed = (time.perf_counter() - started) * 1000
        assert response.status_code == 200
        return elapsed

    with ThreadPoolExecutor(max_workers=CONCURRENCY) as pool:
        result["media_preview_endpoint"] = {
            **_stats(list(pool.map(timed_preview, range(CONCURRENCY * ROUNDS)))),
            "concurrency": CONCURRENCY,
        }
    return result


def test_qin604_registry_read_latency_report(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    data_root = tmp_path / "gw-data"
    roots = tmp_path / "roots"
    data_root.mkdir()
    roots.mkdir(exist_ok=True)
    monkeypatch.setenv("GW_RUNTIME_MODE", "test")
    monkeypatch.setenv("GW_AUTH_MODE", "local")
    monkeypatch.setenv("GW_DATA_DIR", str(data_root))
    monkeypatch.setenv("GW_LOCAL_AUTH_DB", str(tmp_path / "gw-auth" / "auth.sqlite3"))
    monkeypatch.setenv("GW_VIDEO_DATA_DIR", str(tmp_path / "gw-video"))
    monkeypatch.setenv("GW_ALLOWED_ROOTS", str(roots))
    monkeypatch.setenv("GW_DISABLE_AGENT_INTEGRATION", "1")

    fixtures = _fixtures(tmp_path)
    report: dict[str, Any] = {
        "label": "repository-benchmark",
        "host": {"os": platform.platform(), "python": platform.python_version()},
        "method": {
            "parse_iterations": PARSE_ITERATIONS,
            "concurrency": CONCURRENCY,
            "rounds": ROUNDS,
            "thumbnails_per_click": THUMBNAILS_PER_CLICK,
            "percentile": "nearest-rank",
        },
        "scales": {},
    }
    with TestClient(create_app()) as client:
        for name in ("real", "5000"):
            report["scales"][name] = _measure_scale(name, fixtures[name], client)

    # 结构断言（与硬件无关）：文件未变时整段 32 请求只允许 1 次注册表解析。
    for name, scale in report["scales"].items():
        assert scale["folder_click"]["request_count"] == 32, name
        assert scale["folder_click"]["asset_registry_parse_count"] <= 1, (
            "%s 档解析次数应 ≤1，实际 %s" % (name, scale["folder_click"]["asset_registry_parse_count"])
        )
        assert scale["assets_endpoint"]["n"] == CONCURRENCY * ROUNDS
        assert scale["media_preview_endpoint"]["n"] == CONCURRENCY * ROUNDS

    destination = os.environ.get("GW_QIN604_REPORT_DIR")
    if destination:
        folder = Path(destination)
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "qin604-repository-benchmark.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    print(json.dumps(report, ensure_ascii=False, indent=2))
