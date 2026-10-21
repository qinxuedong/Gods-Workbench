"""C10-T1 可复现规模阶梯与本机硬件基准。

本测试只生成隔离的确定性清单，并在当前机器上记录文件读取、分页、结构化
筛选、缩略图读取和派生哈希的观察值。它不写入应用数据，也不设置通过阈值。
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
import time
from collections.abc import Callable
from datetime import UTC, datetime, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[3]
EVIDENCE_ROOT = REPO_ROOT / ".local" / "qa" / "C10-evidence"
FIXTURE_SEED = "c10-t1-scale-v001"
SCALE_LEVELS = (64, 256, 1024)
PAGE_SIZE = 64


def _canonical_json(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _records(count: int) -> list[dict[str, Any]]:
    """返回固定顺序、固定字段的资产记录，便于他人按 seed 重建。"""
    return [
        {
            "asset_id": f"asset_c10t1_{index:06d}",
            "kind": "image" if index % 4 else "video",
            "name": f"c10t1-asset-{index:06d}",
            "size_bytes": 4096 + (index % 17) * 257,
            "tags": [f"scale-{index % 8:02d}", "benchmark"],
            "thumbnail_key": f"thumb_c10t1_{index:06d}",
        }
        for index in range(count)
    ]


def _fixture_id(records: list[dict[str, Any]]) -> str:
    digest = hashlib.sha256(_canonical_json(records)).hexdigest()[:20]
    return f"fixture_c10t1_{digest}"


def _write_fixture(root: Path, records: list[dict[str, Any]]) -> tuple[Path, Path]:
    root.mkdir(parents=True, exist_ok=True)
    manifest = root / "manifest.json"
    manifest.write_bytes(_canonical_json({"seed": FIXTURE_SEED, "assets": records}))
    thumbnail_root = root / "thumbnails"
    thumbnail_root.mkdir()
    for record in records:
        payload = hashlib.sha256(record["asset_id"].encode("ascii")).digest()
        (thumbnail_root / f"{record['thumbnail_key']}.bin").write_bytes(payload)
    return manifest, thumbnail_root


def _timed(operation: Callable[[], Any]) -> tuple[Any, float]:
    started = time.perf_counter()
    value = operation()
    return value, round((time.perf_counter() - started) * 1000, 4)


def _command_version(command: str) -> dict[str, Any]:
    executable = shutil.which(command)
    if executable is None:
        return {"available": False}
    try:
        result = subprocess.run(
            [executable, "--version"],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return {
            "available": True,
            "executable": executable,
            "error": type(exc).__name__,
        }
    output = (result.stdout or result.stderr).strip().splitlines()
    return {
        "available": True,
        "executable": executable,
        "version": output[0][:200] if output else "",
        "returncode": result.returncode,
    }


def _git_sha() -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    return result.stdout.strip() if result.returncode == 0 else "unknown"


def _hardware_profile() -> dict[str, Any]:
    return {
        "captured_at_utc": datetime.now(UTC).isoformat(),
        "os": {
            "system": platform.system(),
            "release": platform.release(),
            "version": platform.version(),
            "machine": platform.machine(),
            "processor": platform.processor(),
        },
        "python": sys.version,
        "cpu_count": os.cpu_count(),
        "tools": {
            "python": {"available": True, "version": platform.python_version()},
            "pytest": _command_version("pytest"),
            "node": _command_version("node"),
            "chrome": _command_version("chrome"),
            "msedge": _command_version("msedge"),
            "firefox": _command_version("firefox"),
        },
        "workbench_service": {
            "status": "not_started",
            "reason": "C10-T1 uses an isolated file fixture and does not touch a running service",
        },
    }


def _write_evidence(name: str, payload: dict[str, Any]) -> None:
    EVIDENCE_ROOT.mkdir(parents=True, exist_ok=True)
    (EVIDENCE_ROOT / name).write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def test_c10_scale_ladder_manifest_is_reproducible(tmp_path: Path) -> None:
    """规模、字段和 fixture id 固定，其他执行者可按 seed 重建。"""
    largest = _records(max(SCALE_LEVELS))
    levels = []
    for count in SCALE_LEVELS:
        records = largest[:count]
        fixture_id = _fixture_id(records)
        assert len(records) == count
        assert len({record["asset_id"] for record in records}) == count
        assert fixture_id == _fixture_id(_records(count))
        levels.append(
            {
                "count": count,
                "fixture_id": fixture_id,
                "records_sha256": hashlib.sha256(_canonical_json(records)).hexdigest(),
            }
        )

    manifest = {
        "task": "QIN-191 / C10-T1",
        "fixture_seed": FIXTURE_SEED,
        "generator": {
            "record_order": "asset_id ascending",
            "schema": [
                "asset_id",
                "kind",
                "name",
                "size_bytes",
                "tags",
                "thumbnail_key",
            ],
            "page_size": PAGE_SIZE,
        },
        "scale_levels": levels,
        "thresholds": {"status": "pending_user_confirmation", "values": None},
    }
    assert manifest["thresholds"]["values"] is None
    assert "达标" not in json.dumps(manifest, ensure_ascii=False)
    _write_evidence("c10-t1-fixture-manifest.json", manifest)
    assert tmp_path.exists()


def test_c10_benchmark_records_observations_without_thresholds(tmp_path: Path) -> None:
    """在当前硬件上采集冷/热代理读、分页、搜索、缩略图和派生观察值。"""
    largest = _records(max(SCALE_LEVELS))
    observations: list[dict[str, Any]] = []
    for count in SCALE_LEVELS:
        records = largest[:count]
        fixture_root = tmp_path / f"scale_{count:06d}"
        manifest_path, thumbnail_root = _write_fixture(fixture_root, records)

        cold_raw, cold_ms = _timed(
            lambda: json.loads(manifest_path.read_text(encoding="utf-8"))
        )
        hot_raw, hot_ms = _timed(
            lambda: json.loads(manifest_path.read_text(encoding="utf-8"))
        )
        assert cold_raw == hot_raw
        loaded = cold_raw["assets"]
        inventory_count, inventory_ms = _timed(lambda: len(loaded))
        page, page_ms = _timed(lambda: loaded[:PAGE_SIZE])
        search_result, search_ms = _timed(
            lambda: [record for record in loaded if "scale-03" in record["tags"]]
        )
        thumb_path = thumbnail_root / f"{loaded[-1]['thumbnail_key']}.bin"
        thumb_bytes, thumbnail_ms = _timed(lambda: thumb_path.read_bytes())
        derived, derived_ms = _timed(
            lambda: [
                hashlib.sha256(_canonical_json(record)).hexdigest() for record in loaded
            ]
        )

        assert inventory_count == count
        assert len(page) == min(PAGE_SIZE, count)
        assert search_result
        assert len(thumb_bytes) == 32
        assert len(derived) == count
        observations.append(
            {
                "count": count,
                "fixture_id": _fixture_id(records),
                "cache_conditions": {
                    "cold": "first read after fixture write; OS cache eviction not claimed",
                    "hot": "immediate repeated read in the same process",
                },
                "operations": {
                    "manifest_read_cold_ms": cold_ms,
                    "manifest_read_hot_ms": hot_ms,
                    "inventory_ms": inventory_ms,
                    "page_ms": page_ms,
                    "search_ms": search_ms,
                    "thumbnail_ms": thumbnail_ms,
                    "derived_hash_ms": derived_ms,
                },
                "result_counts": {
                    "inventory": inventory_count,
                    "page": len(page),
                    "search": len(search_result),
                    "derived": len(derived),
                },
            }
        )

    report = {
        "task": "QIN-191 / C10-T1",
        "commit_sha": _git_sha(),
        "hardware": _hardware_profile(),
        "dataset": {
            "fixture_seed": FIXTURE_SEED,
            "scale_levels": list(SCALE_LEVELS),
            "page_size": PAGE_SIZE,
            "generator": "deterministic JSON records plus SHA-256 thumbnail bytes",
        },
        "observations": observations,
        "thresholds": {
            "status": "pending_user_confirmation",
            "values": None,
            "note": "只记录本机观察值；规模、p95、首屏等阈值待秦先生确认",
        },
    }
    serialized = json.dumps(report, ensure_ascii=False)
    assert "达标" not in serialized and "通过" not in serialized
    _write_evidence("c10-t1-benchmark-report.json", report)
    assert (EVIDENCE_ROOT / "c10-t1-benchmark-report.json").is_file()
