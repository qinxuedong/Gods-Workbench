import shutil
import time
from typing import Any

import pytest
from gw.core import storage
from gw.observability import telemetry


def test_record_http_observation_and_flush(monkeypatch: pytest.MonkeyPatch, tmp_path: pytest.TempPathFactory) -> None:
    """Test HTTP observation aggregation and flushing with tmp_path isolation."""
    monkeypatch.setattr(telemetry, "_HTTP_PENDING", {})

    # Set LAST_FLUSH to future so it accumulates and does not flush
    now = time.time()
    monkeypatch.setattr(telemetry, "_HTTP_LAST_FLUSH", now + 1000.0)

    # Isolate storage to tmp_path
    monkeypatch.setattr(storage, "data_root", lambda: tmp_path)

    telemetry.record_http_observation("/api/foo", response_bytes=1000, duration_ms=50.0)
    telemetry.record_http_observation("/api/bar", response_bytes=2000, duration_ms=100.0)

    assert telemetry._HTTP_PENDING["_count"] == 2.0
    assert telemetry._HTTP_PENDING["asset_response_bytes"] == 3000.0
    assert telemetry._HTTP_PENDING["_duration_total_ms"] == 150.0

    telemetry.flush_http_samples()

    assert telemetry._HTTP_PENDING == {}

    # Verify it flushed to actual tmp_path
    raw = telemetry._samples_state().read()
    assert raw.get("samples") is not None

    points = list(raw["samples"].values())
    assert len(points) == 1
    assert points[0]["values"]["asset_response_bytes"] == 3000.0
    assert points[0]["values"]["asset_search_duration_ms"] == 150.0 / 2.0


def test_load_series_window_filtering(monkeypatch: pytest.MonkeyPatch, tmp_path: pytest.TempPathFactory) -> None:
    """Test load_series correctly filters points using isolated storage."""
    monkeypatch.setattr(storage, "data_root", lambda: tmp_path)

    def inject_samples() -> None:
        def mutate(raw: dict[str, Any]) -> dict[str, Any]:
            raw["samples"] = {
                "100": {"ts_ms": 100, "values": {"cpu_percent": 10.0}},
                "200": {"ts_ms": 200, "values": {"cpu_percent": 20.0}},
                "300": {"ts_ms": 300, "values": {"cpu_percent": 30.0}},
                "400": {"ts_ms": 400, "values": {"other": 40.0}},
            }
            return raw
        telemetry._samples_state().mutate(mutate)

    inject_samples()

    assert telemetry.load_series("unknown", None, None) == []

    series = telemetry.load_series("cpu_percent", 150, 250)
    assert len(series) == 1
    assert series[0]["ts_ms"] == 200
    assert series[0]["value"] == 20.0

    series2 = telemetry.load_series("cpu_percent", None, 150)
    assert len(series2) == 1
    assert series2[0]["ts_ms"] == 100


def test_source_registry_shape() -> None:
    """Test source_registry returns expected shape."""
    registry = telemetry.source_registry()
    assert isinstance(registry, list)
    assert len(registry) > 0
    for item in registry:
        assert "source_id" in item
        assert "name" in item
        assert "kind" in item
        assert "path" in item
        assert "data_status" in item


def test_hardware_telemetry_none_fallback(monkeypatch: pytest.MonkeyPatch, tmp_path: pytest.TempPathFactory) -> None:
    """Test none fallback paths for hardware telemetry and GPU when unavailable."""
    monkeypatch.setattr(storage, "data_root", lambda: tmp_path)

    # Force psutil unavailable
    monkeypatch.setattr(telemetry, "_psutil", lambda: None)
    assert telemetry.hardware_telemetry() is None

    # Force nvidia-smi unavailable via shutil.which
    monkeypatch.setattr(shutil, "which", lambda cmd: None)
    assert telemetry.gpu_telemetry() is None

    # record_sample should return empty dict when hardware is none
    assert telemetry.record_sample() == {}
