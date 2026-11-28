"""QIN-676：HTTP 观测单写者与健康检查非阻塞契约。"""

from __future__ import annotations

import asyncio
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import anyio
import pytest
from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient


def test_qin676_middleware_returns_while_worker_write_is_blocked(
    monkeypatch, tmp_path
):
    """AC3：HTTP middleware 提交采样后不等待慢磁盘，并由 lifespan 清理线程。"""
    from gw.api.app import create_app
    from gw.observability import telemetry

    started = threading.Event()
    release = threading.Event()
    writer_threads: list[int] = []
    request_threads: list[int] = []

    def slow_persist(_payload):
        writer_threads.append(threading.get_ident())
        started.set()
        release.wait(timeout=3)

    async def observed_route():
        request_threads.append(threading.get_ident())
        return {"ok": True}

    monkeypatch.setattr(telemetry, "_persist_http_payload", slow_persist)
    with telemetry._HTTP_LOCK:
        telemetry._HTTP_PENDING.clear()
        telemetry._HTTP_LAST_FLUSH = 0.0

    app = create_app()
    app.add_api_route("/api/qin-676-observation-probe", observed_route, methods=["GET"])
    writer = None
    try:
        with TestClient(app) as client:
            writer = app.state.http_observation_writer
            try:
                with ThreadPoolExecutor(max_workers=1) as executor:
                    response_future = executor.submit(
                        client.get, "/api/qin-676-observation-probe"
                    )
                    assert started.wait(timeout=2), "观测 worker 未开始慢写盘"
                    response = response_future.result(timeout=0.2)
                    assert response.status_code == 200
            finally:
                release.set()
            assert writer_threads
            assert writer_threads[0] != request_threads[0]
    finally:
        release.set()

    assert writer is not None
    assert writer.is_alive is False
    assert app.state.http_observation_writer is None
    assert not list(Path(tmp_path).rglob("*.tmp"))


def test_qin676_flush_barrier_and_idempotent_worker_stop(monkeypatch):
    """AC3：flush 等待队列落盘，写入异常不杀 worker，stop 幂等。"""
    from gw.observability import telemetry

    calls = 0
    failed = threading.Event()
    persisted = threading.Event()

    def persist(_payload):
        nonlocal calls
        calls += 1
        if calls == 1:
            failed.set()
            raise OSError("simulated telemetry write failure")
        persisted.set()

    monkeypatch.setattr(telemetry, "_persist_http_payload", persist)
    with telemetry._HTTP_LOCK:
        telemetry._HTTP_PENDING.clear()
        telemetry._HTTP_LAST_FLUSH = 0.0
    writer = telemetry.start_http_observation_writer()
    try:
        telemetry.record_http_observation(
            "/api/first", response_bytes=10, duration_ms=2.0
        )
        assert failed.wait(timeout=2)
        telemetry.record_http_observation(
            "/api/second", response_bytes=20, duration_ms=4.0
        )
        telemetry.flush_http_samples()
        assert persisted.is_set(), "flush barrier 应在返回前等待写入"
        assert writer.is_alive
    finally:
        telemetry.stop_http_observation_writer(writer)
        telemetry.stop_http_observation_writer(writer)
    assert not writer.is_alive
    assert not any(
        thread.name == telemetry.HTTP_OBSERVATION_WRITER_THREAD
        for thread in threading.enumerate()
    )


def test_qin676_full_queue_falls_back_to_memory_without_blocking(monkeypatch):
    """AC3：队列满及 flush 等待时，请求不等待磁盘且样本最终排空。"""
    from gw.observability import telemetry

    started = threading.Event()
    flush_started = threading.Event()
    release = threading.Event()
    persisted_counts: list[float] = []

    def slow_persist(payload):
        persisted_counts.append(payload.get("_count", 0.0))
        if not started.is_set():
            started.set()
            release.wait(timeout=3)

    monkeypatch.setattr(telemetry, "_persist_http_payload", slow_persist)
    monkeypatch.setattr(telemetry, "HTTP_OBSERVATION_QUEUE_CAPACITY", 1)
    monkeypatch.setattr(telemetry, "HTTP_FLUSH_INTERVAL_SECONDS", 0.0)
    original_flush = telemetry._HttpObservationWriter.flush

    def tracked_flush(writer, payload=None):
        flush_started.set()
        return original_flush(writer, payload)

    monkeypatch.setattr(telemetry._HttpObservationWriter, "flush", tracked_flush)
    with telemetry._HTTP_LOCK:
        telemetry._HTTP_PENDING.clear()
        telemetry._HTTP_LAST_FLUSH = 0.0
    writer = telemetry.start_http_observation_writer()
    try:
        telemetry.record_http_observation(
            "/api/queue-first", response_bytes=1, duration_ms=1.0
        )
        assert started.wait(timeout=2), "观测 worker 未开始慢写盘"
        telemetry.record_http_observation(
            "/api/queue-second", response_bytes=1, duration_ms=1.0
        )
        with ThreadPoolExecutor(max_workers=1) as executor:
            flush_future = executor.submit(telemetry.flush_http_samples)
            assert flush_started.wait(timeout=2), "flush barrier 未开始等待队列"
            assert not flush_future.done(), "队列满且磁盘阻塞时 flush 应等待 barrier"
            started_at = time.perf_counter()
            telemetry.record_http_observation(
                "/api/queue-third", response_bytes=1, duration_ms=1.0
            )
            assert (time.perf_counter() - started_at) < 0.2
            with telemetry._HTTP_LOCK:
                assert telemetry._HTTP_PENDING["_count"] == 1
            release.set()
            flush_future.result(timeout=2)
    finally:
        release.set()
        telemetry.stop_http_observation_writer(writer)
    assert sum(persisted_counts) == 3


def test_qin676_healthz_is_responsive_when_anyio_threadpool_is_full():
    """AC4：同步线程池全占用时，/healthz 仍在 200ms 内异步响应。"""
    from gw.api.app import create_app

    started = threading.Event()
    release = threading.Event()

    def slow_sync_route():
        started.set()
        release.wait(timeout=3)
        return {"ok": True}

    app = create_app()
    app.add_api_route("/api/qin-676-sync-pool-probe", slow_sync_route, methods=["GET"])

    async def exercise() -> None:
        async with app.router.lifespan_context(app):
            limiter = anyio.to_thread.current_default_thread_limiter()
            original_tokens = limiter.total_tokens
            limiter.total_tokens = 1
            try:
                transport = ASGITransport(app=app)
                async with AsyncClient(
                    transport=transport,
                    base_url="http://127.0.0.1",
                    timeout=0.35,
                ) as client:
                    blocked = asyncio.create_task(
                        client.get(
                            "/api/qin-676-sync-pool-probe",
                            headers={"Authorization": "Bearer cleanroom-test"},
                        )
                    )
                    assert await asyncio.to_thread(started.wait, 2)
                    started_at = time.perf_counter()
                    health = await client.get("/healthz")
                    elapsed_ms = (time.perf_counter() - started_at) * 1000
                    assert health.status_code == 200
                    assert elapsed_ms <= 200, f"/healthz 用时 {elapsed_ms:.1f}ms"
                    assert set(health.json()) == {
                        "status",
                        "mode",
                        "auth_mode",
                        "oidc_ready",
                        "frozen_contracts",
                        "release_authorized",
                    }
                    assert health.json()["status"] == "ok"
                    release.set()
                    assert (await asyncio.wait_for(blocked, timeout=2)).status_code == 200
            finally:
                release.set()
                limiter.total_tokens = original_tokens

    try:
        asyncio.run(exercise())
    finally:
        release.set()
    assert not any(
        thread.name == "gw-http-observation-writer"
        for thread in threading.enumerate()
    )
