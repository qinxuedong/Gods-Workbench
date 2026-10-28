"""MVP1 试点报告字段契约：AC1 指标块的收敛、缺失与非法输入处理。"""

import json

import pytest

from tools import asset_index_ingest as ingest_cli

JOB = {
    "status": "succeeded",
    "created_at": "2026-10-28T01:00:00Z",
    "updated_at": "2026-10-28T01:01:00Z",
    "progress": {"scanned": 20, "error_count": 0},
    "result": {"scanned": 20, "indexed": 20, "updated": 0},
}

MEASUREMENTS = {
    "read_throughput_mb_per_second": 1680.8123456,
    "shots_ingested_per_minute": 53.6894999,
    "retrieval_latency_p50_ms": 22.10500,
    "retrieval_latency_p95_ms": 30.92500,
    "gpu_name": "  NVIDIA GeForce RTX 5090  ",
    "gpu_peak_memory_mib": 8328,
}


def _report(measurements=None):
    return ingest_cli.make_report(
        "job_index_00000001",
        JOB,
        rate_limit=None,
        night_window=None,
        measurements=measurements,
    )


def test_report_carries_every_ac1_field_as_null_without_measurements():
    """未提供测量文件时字段齐全但为 null，不允许用推算值冒充实测。"""
    metrics = _report()["pilot_metrics"]

    assert tuple(metrics) == ingest_cli.PILOT_METRIC_KEYS
    assert set(metrics.values()) == {None}


def test_report_keeps_measured_values_with_three_decimals_and_trimmed_gpu_name():
    """传入测量结果时按三位小数收敛，GPU 型号去空白。"""
    metrics = _report(MEASUREMENTS)["pilot_metrics"]

    assert metrics["read_throughput_mb_per_second"] == 1680.812
    assert metrics["shots_ingested_per_minute"] == 53.689
    assert metrics["retrieval_latency_p50_ms"] == 22.105
    assert metrics["retrieval_latency_p95_ms"] == 30.925
    assert metrics["gpu_name"] == "NVIDIA GeForce RTX 5090"
    assert metrics["gpu_peak_memory_mib"] == 8328


def test_report_drops_unknown_keys_and_non_numeric_measurements():
    """白名单外的键不进入报告；布尔、负数、NaN、字符串数字一律视为缺项。"""
    metrics = _report(
        {
            "read_throughput_mb_per_second": "1680.8",
            "shots_ingested_per_minute": -1,
            "retrieval_latency_p50_ms": float("nan"),
            "retrieval_latency_p95_ms": True,
            "gpu_name": "   ",
            "gpu_peak_memory_mib": None,
            "source_path": "占位路径不应出现",
        }
    )["pilot_metrics"]

    assert set(metrics.values()) == {None}
    assert "source_path" not in metrics
    assert "占位路径不应出现" not in json.dumps(
        _report({**MEASUREMENTS}), ensure_ascii=False
    )


def test_report_keeps_legacy_throughput_fields_untouched():
    """新增指标块不改变既有吞吐字段口径。"""
    report = _report(MEASUREMENTS)

    assert report["throughput_files_per_minute"] == 20.0
    assert report["elapsed_seconds"] == 60.0
    assert report["status"] == "succeeded"


def test_load_measurements_reads_json_object(tmp_path):
    """合法 JSON 对象原样返回，供报告按白名单取值。"""
    path = tmp_path / "measurements.json"
    path.write_text(json.dumps(MEASUREMENTS), encoding="utf-8")

    assert ingest_cli.load_measurements(str(path)) == MEASUREMENTS
    assert ingest_cli.load_measurements(None) is None


def test_load_measurements_rejects_missing_invalid_and_non_object_files(tmp_path):
    """读取失败按使用错误处理，且错误信息不回显本机路径。"""
    missing = tmp_path / "absent.json"
    broken = tmp_path / "broken.json"
    broken.write_text("{not json", encoding="utf-8")
    array = tmp_path / "array.json"
    array.write_text("[1, 2, 3]", encoding="utf-8")

    with pytest.raises(ingest_cli.IngestError) as missing_exc:
        ingest_cli.load_measurements(str(missing))
    with pytest.raises(ingest_cli.IngestError) as broken_exc:
        ingest_cli.load_measurements(str(broken))
    with pytest.raises(ingest_cli.IngestError) as array_exc:
        ingest_cli.load_measurements(str(array))

    assert str(missing_exc.value) == "无法读取测量文件：No such file or directory"
    assert "测量文件不是合法 JSON" in str(broken_exc.value)
    assert "顶层必须是 JSON 对象" in str(array_exc.value)
    for exc in (missing_exc, broken_exc, array_exc):
        assert str(tmp_path) not in str(exc.value)


def test_cli_exposes_measurements_option_defaulting_to_absent():
    """命令行默认不读取测量文件，保持既有调用行为。"""
    parser = ingest_cli.build_parser()

    assert parser.parse_args(["--token", "t"]).measurements is None
    assert (
        parser.parse_args(["--token", "t", "--measurements", "m.json"]).measurements
        == "m.json"
    )
