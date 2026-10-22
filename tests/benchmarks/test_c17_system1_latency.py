"""C17-T5：System One 决策路由与延迟、资源基准。"""

from __future__ import annotations

import json
import os
import platform
import time
from math import ceil
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient
import psutil

import gw.api.routes_decision as decision_routes
from gw.decision.router import DecisionRouter, KevDecisionBackend, LocalDecisionBackend


def _nearest_rank_percentile(samples: list[float], quantile: float) -> float:
    if not samples:
        raise ValueError("至少需要一个延迟样本")
    if not 0 < quantile <= 1:
        raise ValueError("分位数必须在 (0, 1] 范围内")
    return sorted(samples)[ceil(len(samples) * quantile) - 1]


def _run_local_route_benchmark(monkeypatch, *, iterations: int) -> dict[str, object]:
    monkeypatch.setattr(
        decision_routes, "default_decision_router", DecisionRouter(backend=LocalDecisionBackend())
    )
    app = FastAPI()
    app.include_router(decision_routes.router)
    payload = {
        "type": "choice",
        "provider": "local",
        "options": [{"id": "alpha"}, {"id": "beta"}],
        "probabilities": {"alpha": 0.2, "beta": 0.8},
    }
    process = psutil.Process()
    rss_before = process.memory_info().rss
    rss_peak = rss_before
    process_time_before = time.process_time()
    latencies_ms: list[float] = []
    correct_responses = 0

    with TestClient(app) as client:
        for _ in range(5):
            response = client.post("/v1/systemone", json=payload)
            assert response.status_code == 200, response.text
        for _ in range(iterations):
            started = time.perf_counter_ns()
            response = client.post("/v1/systemone", json=payload)
            elapsed_ns = time.perf_counter_ns() - started
            assert response.status_code == 200, response.text
            result = response.json()
            correct_responses += result["provider"] == "local" and result["result"]["choice_id"] == "beta"
            latencies_ms.append(elapsed_ns / 1_000_000)
            rss_peak = max(rss_peak, process.memory_info().rss)

    rss_after = process.memory_info().rss
    report: dict[str, object] = {
        "measurement_scope": "FastAPI TestClient + LocalDecisionBackend; precomputed request probabilities, no model inference or NAS call",
        "provider": "local",
        "iterations": iterations,
        "correct_responses": correct_responses,
        "response_accuracy": correct_responses / iterations,
        "latency_ms": {
            "p50": _nearest_rank_percentile(latencies_ms, 0.50),
            "p95": _nearest_rank_percentile(latencies_ms, 0.95),
            "max": max(latencies_ms),
        },
        "process_cpu_seconds": time.process_time() - process_time_before,
        "process_rss_before_bytes": rss_before,
        "process_rss_peak_sampled_bytes": rss_peak,
        "process_rss_after_bytes": rss_after,
        "process_rss_delta_bytes": rss_after - rss_before,
        "host": {
            "os": platform.platform(),
            "architecture": platform.machine(),
            "python": platform.python_version(),
        },
        "nas_ac5_status": "not_measured",
        "ac5_status": "not_evaluated_by_local_precomputed-input benchmark",
    }
    evidence_dir = os.getenv("GW_C17_EVIDENCE_DIR")
    if evidence_dir:
        destination = Path(evidence_dir)
        destination.mkdir(parents=True, exist_ok=True)
        (destination / "c17-local-route-baseline.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    return report


def test_systemone_can_select_configured_nas_kev_backend(monkeypatch) -> None:
    endpoint = "http://nas.local/v1/systemone"
    requests: list[tuple[str, dict[str, object]]] = []

    def transport(url: str, payload: dict[str, object]) -> dict[str, object]:
        requests.append((url, payload))
        return {
            "answers": {
                "decision": {"probabilities": {"alpha": 0.7, "beta": 0.3}}
            }
        }

    monkeypatch.setattr(
        decision_routes,
        "default_decision_router",
        DecisionRouter(kev_backend=KevDecisionBackend(endpoint, transport=transport)),
    )
    app = FastAPI()
    app.include_router(decision_routes.router)

    response = TestClient(app).post(
        "/v1/systemone",
        json={
            "type": "choice",
            "provider": "kev-cpu",
            "options": [{"id": "alpha"}, {"id": "beta"}],
        },
    )

    assert response.status_code == 200, response.text
    assert response.json()["provider"] == "kev-cpu"
    assert response.json()["result"]["choice_id"] == "alpha"
    assert len(requests) == 1


def test_systemone_falls_back_locally_and_retries_nas_after_recovery(monkeypatch) -> None:
    calls = 0

    def transport(_url: str, _payload: dict[str, object]) -> dict[str, object]:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise ConnectionError("NAS offline")
        return {
            "answers": {
                "decision": {"probabilities": {"alpha": 0.8, "beta": 0.2}}
            }
        }

    monkeypatch.setattr(
        decision_routes,
        "default_decision_router",
        DecisionRouter(
            backend=LocalDecisionBackend(),
            kev_backend=KevDecisionBackend(
                "http://nas.local/v1/systemone", transport=transport
            ),
        ),
    )
    app = FastAPI()
    app.include_router(decision_routes.router)
    client = TestClient(app)
    request = {
        "type": "choice",
        "provider": "kev-cpu",
        "options": [{"id": "alpha"}, {"id": "beta"}],
        "probabilities": {"alpha": 0.1, "beta": 0.9},
    }

    fallback = client.post("/v1/systemone", json=request)
    recovered = client.post("/v1/systemone", json=request)

    assert fallback.status_code == 200, fallback.text
    assert fallback.json()["provider"] == "local"
    assert fallback.json()["result"]["choice_id"] == "beta"
    assert recovered.status_code == 200, recovered.text
    assert recovered.json()["provider"] == "kev-cpu"
    assert recovered.json()["result"]["choice_id"] == "alpha"
    assert calls == 2


def test_systemone_keeps_backend_unavailable_when_local_result_is_missing(monkeypatch) -> None:
    def transport(_url: str, _payload: dict[str, object]) -> dict[str, object]:
        raise ConnectionError("NAS offline")

    monkeypatch.setattr(
        decision_routes,
        "default_decision_router",
        DecisionRouter(
            backend=LocalDecisionBackend(),
            kev_backend=KevDecisionBackend(
                "http://nas.local/v1/systemone", transport=transport
            ),
        ),
    )
    app = FastAPI()
    app.include_router(decision_routes.router)

    response = TestClient(app).post(
        "/v1/systemone",
        json={
            "type": "scoring",
            "provider": "kev-cpu",
            "options": [{"id": "clarity"}],
        },
    )

    assert response.status_code == 503, response.text
    assert response.json()["detail"]["code"] == "DECISION_BACKEND_UNAVAILABLE"


def test_latency_percentile_uses_nearest_rank() -> None:
    assert _nearest_rank_percentile(list(range(1, 21)), 0.95) == 19


def test_local_route_baseline_reports_measurement_boundary(monkeypatch) -> None:
    report = _run_local_route_benchmark(monkeypatch, iterations=200)

    assert report["provider"] == "local"
    assert report["iterations"] == 200
    assert report["correct_responses"] == 200
    assert report["process_rss_after_bytes"] > 0
    assert report["nas_ac5_status"] == "not_measured"
    assert "precomputed" in report["measurement_scope"]
