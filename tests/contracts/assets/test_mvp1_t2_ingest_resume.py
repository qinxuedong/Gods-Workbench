"""MVP1-T2 hashed source scan, retry safety, and local ingest CLI contracts."""

from argparse import ArgumentTypeError
import hashlib
import time
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from gw.api.app import create_app
from gw.asset_registry import index_jobs as jobs
from gw.asset_registry import repository as repo
from tools import asset_index_ingest as ingest_cli

BASE = "/api/asset-registry"
AUTH = {"Authorization": "Bearer ingest-owner", "X-User-Role": "editor"}


@pytest.fixture
def ingest_runtime(tmp_path, monkeypatch):
    monkeypatch.setenv("GW_AUTH_MODE", "local")
    monkeypatch.setenv("GW_DATA_DIR", str(tmp_path / "gw-data"))
    root = tmp_path / "sources"
    (root / "nested").mkdir(parents=True)
    (root / "one.txt").write_bytes(b"same content")
    (root / "nested" / "two.txt").write_bytes(b"another source")
    monkeypatch.setenv("GW_ALLOWED_ROOTS", str(root))
    if jobs._default:
        jobs._default.shutdown()
    jobs._default = None
    yield root
    if jobs._default:
        jobs._default.shutdown()
    jobs._default = None


def _tree_digest(root):
    return {
        path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _read_job(client, job_id):
    return client.get(f"{BASE}/workspace-jobs/{job_id}", headers=AUTH).json()["job"]


def _wait_job(client, job_id):
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        job = _read_job(client, job_id)
        if job["status"] in jobs.TERMINAL:
            return job
        time.sleep(0.02)
    pytest.fail("后台索引任务未及时结束")


def _submit(client, key):
    return client.post(
        f"{BASE}/reindex",
        headers={**AUTH, "Idempotency-Key": key},
        json={"background": True, "hash_files": True},
    )


def test_hash_retry_is_idempotent_and_keeps_source_tree_unchanged(
    ingest_runtime, monkeypatch
):
    before = _tree_digest(ingest_runtime)
    original_scan = jobs.IndexJobs._scan
    calls = 0

    def interrupt_once(self, *args):
        nonlocal calls
        calls += 1
        files = original_scan(self, *args)
        if calls == 1:
            raise RuntimeError("injected interruption after hashed scan")
        return files

    monkeypatch.setattr(jobs.IndexJobs, "_scan", interrupt_once)
    with TestClient(create_app()) as client:
        created = _submit(client, "mvp1-t2-retry")
        assert created.status_code == 202, created.text
        job_id = created.json()["job_id"]
        failed = _wait_job(client, job_id)
        assert failed["status"] == "failed"
        assert failed["error_code"] == "INDEX_EXECUTION_FAILED"

        retry = client.post(
            f"{BASE}/workspace-jobs/{job_id}/retry",
            headers=AUTH,
            json={"expected_version": failed["version"]},
        )
        assert retry.status_code == 200, retry.text
        completed = _wait_job(client, job_id)
        assert completed["status"] == "succeeded"
        assert completed["attempt"] == 2
        assert completed["progress"] == {
            "scanned": 2,
            "error_count": 0,
            "phase": "completed",
        }
        assert completed["result"]["indexed"] == 2

        repeated = _submit(client, "mvp1-t2-same-content")
        assert repeated.status_code == 202, repeated.text
        duplicate = _wait_job(client, repeated.json()["job_id"])
        assert duplicate["status"] == "succeeded"
        assert duplicate["result"]["indexed"] == 0
        assets = list(repo.state().read()["assets"].values())
        assert len(assets) == 2
        assert {asset["sha256"] for asset in assets} == {
            hashlib.sha256(b"same content").hexdigest(),
            hashlib.sha256(b"another source").hexdigest(),
        }
        assert _tree_digest(ingest_runtime) == before

        changed_source = ingest_runtime / "one.txt"
        changed_source.write_bytes(b"updated content")
        changed_snapshot = _tree_digest(ingest_runtime)
        updated_job = _submit(client, "mvp1-t2-changed-content")
        assert updated_job.status_code == 202, updated_job.text
        updated = _wait_job(client, updated_job.json()["job_id"])
        assert updated["status"] == "succeeded"
        assert updated["result"]["indexed"] == 0
        assert updated["result"]["updated"] == 1
        current_assets = list(repo.state().read()["assets"].values())
        assert len(current_assets) == 2
        changed_asset = next(asset for asset in current_assets if asset["name"] == "one.txt")
        assert changed_asset["sha256"] == hashlib.sha256(b"updated content").hexdigest()

    assert _tree_digest(ingest_runtime) == changed_snapshot


def test_cli_submits_hashed_job_and_reports_throughput(monkeypatch):
    calls = []
    created_at = "2026-10-27T00:00:00+00:00"
    completed_at = "2026-10-27T00:01:00+00:00"
    final_job = {
        "job_id": "job_index_test",
        "status": "succeeded",
        "attempt": 1,
        "version": 2,
        "created_at": created_at,
        "updated_at": completed_at,
        "progress": {"scanned": 2, "error_count": 0, "phase": "completed"},
        "result": {"scanned": 2, "indexed": 2, "updated": 0},
        "error_code": None,
    }

    def fake_request(base_url, token, role, method, path, *, payload=None, idempotency_key=None):
        calls.append((base_url, token, role, method, path, payload, idempotency_key))
        if method == "POST" and path == f"{BASE}/reindex":
            return {"job_id": final_job["job_id"], "job": dict(final_job)}
        assert method == "GET"
        return {"job": dict(final_job), "actions": {}}

    monkeypatch.setattr(ingest_cli, "_api_request", fake_request)
    args = ingest_cli.build_parser().parse_args(
        ["--token", "test-token", "--root", "nas", "--idempotency-key", "t2-cli"]
    )

    report = ingest_cli.run_job(args)

    assert calls[0][3:] == (
        "POST",
        f"{BASE}/reindex",
        {"background": True, "hash_files": True, "roots": ["nas"]},
        "t2-cli",
    )
    assert report["status"] == "succeeded"
    assert report["scanned"] == 2 and report["indexed"] == 2
    assert report["throughput_files_per_minute"] == 2.0


def test_cli_retries_interrupted_job_after_restart(monkeypatch):
    calls = []
    terminal = {
        "job_id": "job_index_resume",
        "status": "succeeded",
        "attempt": 2,
        "version": 2,
        "created_at": "2026-10-27T00:00:00+00:00",
        "updated_at": "2026-10-27T00:01:00+00:00",
        "progress": {"scanned": 1, "error_count": 0, "phase": "completed"},
        "result": {"scanned": 1, "indexed": 0, "updated": 0},
        "error_code": None,
    }
    interrupted = {
        **terminal,
        "status": "interrupted",
        "attempt": 1,
        "version": 4,
        "error_code": "INDEX_PROCESS_INTERRUPTED",
    }
    queued = {
        **terminal,
        "status": "queued",
        "attempt": 2,
        "version": 1,
        "progress": {"scanned": 0, "error_count": 0, "phase": "queued"},
        "result": None,
    }

    def fake_request(base_url, token, role, method, path, *, payload=None, idempotency_key=None):
        calls.append((method, path, payload))
        if method == "GET":
            if len([call for call in calls if call[0] == "GET"]) == 1:
                return {"job": interrupted, "actions": {"retry": True}}
            return {"job": terminal, "actions": {}}
        assert path.endswith("/retry")
        assert payload == {"expected_version": 4}
        return {"job": queued, "actions": {}}

    monkeypatch.setattr(ingest_cli, "_api_request", fake_request)
    args = ingest_cli.build_parser().parse_args(
        ["--token", "test-token", "--job-id", "job_index_resume"]
    )

    report = ingest_cli.run_job(args)

    assert report["status"] == "succeeded" and report["attempt"] == 2
    assert calls[0][1].endswith("/job_index_resume")
    assert any(path.endswith("/job_index_resume/retry") for _, path, _ in calls)


def test_cli_rate_limit_pauses_and_resumes_job(monkeypatch):
    clock = [0.0]
    monkeypatch.setattr(ingest_cli.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(ingest_cli.time, "sleep", lambda seconds: clock.__setitem__(0, clock[0] + seconds))
    calls = []
    base_job = {
        "job_id": "job_index_rate",
        "attempt": 1,
        "created_at": "2026-10-27T00:00:00+00:00",
        "updated_at": "2026-10-27T00:01:00+00:00",
        "error_code": None,
    }
    running = {
        **base_job,
        "status": "running",
        "version": 2,
        "progress": {"scanned": 25, "error_count": 0},
    }
    paused = {**running, "status": "paused", "version": 3}
    resumed = {**running, "version": 4}
    completed = {
        **running,
        "status": "succeeded",
        "version": 5,
        "result": {"scanned": 25, "indexed": 25, "updated": 0},
        "progress": {"scanned": 25, "error_count": 0},
    }
    reads = iter(
        [
            {"job": running, "actions": {"pause": True}},
            {"job": paused, "actions": {"resume": True}},
            {"job": completed, "actions": {}},
        ]
    )

    def fake_request(base_url, token, role, method, path, *, payload=None, idempotency_key=None):
        calls.append((method, path))
        if path.endswith("/reindex"):
            return {"job_id": base_job["job_id"], "job": {**base_job, "status": "running", "version": 1, "progress": {"scanned": 0}}}
        if method == "GET":
            return next(reads)
        if path.endswith("/pause"):
            return {"job": paused, "actions": {"resume": True}}
        if path.endswith("/resume"):
            return {"job": resumed, "actions": {}}
        raise AssertionError(path)

    monkeypatch.setattr(ingest_cli, "_api_request", fake_request)
    args = ingest_cli.build_parser().parse_args(
        ["--token", "test-token", "--rate-limit", "60", "--poll-interval", "30"]
    )

    report = ingest_cli.run_job(args)

    assert report["status"] == "succeeded"
    assert any(path.endswith("/pause") for _, path in calls)
    assert any(path.endswith("/resume") for _, path in calls)
    assert clock[0] == 25


def test_night_window_and_rate_pacing_helpers():
    window = ingest_cli.parse_night_window("22:00-06:00")
    assert window == (22 * 60, 6 * 60)
    assert ingest_cli.window_is_open(
        datetime(2026, 10, 27, 23, 0, tzinfo=timezone.utc), window
    )
    assert ingest_cli.window_is_open(
        datetime(2026, 10, 27, 5, 59, tzinfo=timezone.utc), window
    )
    assert not ingest_cli.window_is_open(
        datetime(2026, 10, 27, 12, 0, tzinfo=timezone.utc), window
    )
    assert ingest_cli.rate_wait_seconds(25, 60, 10) == 15
    assert ingest_cli.rate_wait_seconds(25, 60, 25) == 0
    with pytest.raises(ArgumentTypeError, match="起止时间不能相同"):
        ingest_cli.parse_night_window("22:00-22:00")
