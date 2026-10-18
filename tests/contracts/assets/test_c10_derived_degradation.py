"""C10-T3 后台派生、索引失败与离线依赖降级契约。"""
import time

import pytest
from fastapi.testclient import TestClient

from gw.api.app import create_app
from gw.asset_registry import index_jobs as jobs
from gw.asset_registry import media_probe

BASE = "/api/asset-registry"
AUTH = {"Authorization": "Bearer index-owner", "X-User-Role": "editor"}


@pytest.fixture(autouse=True)
def index_runtime(tmp_path, monkeypatch):
    monkeypatch.setenv("GW_AUTH_MODE", "local")
    monkeypatch.setenv("GW_DATA_DIR", str(tmp_path / "gw-data"))
    root = tmp_path / "sources"
    root.mkdir()
    (root / "source.txt").write_text("真实索引内容", encoding="utf-8")
    monkeypatch.setenv("GW_ALLOWED_ROOTS", str(root))
    if jobs._default:
        jobs._default.shutdown()
    jobs._default = None
    yield root
    if jobs._default:
        jobs._default.shutdown()
    jobs._default = None


def submit(client, payload=None, key="c10-t3", kind="reindex"):
    return client.post(
        f"{BASE}/{kind}",
        headers={**AUTH, "Idempotency-Key": key},
        json={"background": True, **(payload or {})},
    )


def read(client, job_id):
    return client.get(f"{BASE}/workspace-jobs/{job_id}", headers=AUTH)


def wait(client, job_id):
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        body = read(client, job_id).json()["job"]
        if body["status"] in jobs.TERMINAL:
            return body
        time.sleep(0.02)
    pytest.fail("后台索引未在期限内终结")


def test_index_job_status_has_truthful_next_action_for_pending_success_and_failure(index_runtime):
    with TestClient(create_app()) as client:
        accepted = submit(client)
        assert accepted.status_code == 202, accepted.text
        accepted_job = accepted.json()["job"]
        assert accepted_job["status"] in {"queued", "running"}
        assert accepted_job["data_status"] == "degraded"
        assert accepted_job["data_gaps"] == ["INDEX_PENDING"]
        assert accepted_job["next_action"] == "poll_job"

        succeeded = wait(client, accepted.json()["job_id"])
        assert succeeded["status"] == "succeeded"
        assert succeeded["data_status"] == "ok"
        assert succeeded["data_gaps"] == []
        assert succeeded["next_action"] == "review_result"
        assert succeeded["result_unknown"] is False

        (index_runtime / "second.txt").write_text("第二个来源", encoding="utf-8")
        failed_id = submit(client, {"max_files": 1}, key="c10-t3-failure").json()["job_id"]
        failed = wait(client, failed_id)
        assert failed["status"] == "failed"
        assert failed["error_code"] == "INDEX_SCAN_LIMIT"
        assert failed["data_status"] == "degraded"
        assert failed["data_gaps"] == ["INDEX_SCAN_LIMIT"]
        assert failed["next_action"] == "retry_index"
        assert failed["result_unknown"] is False


def test_interrupted_and_unknown_jobs_require_reconciliation():
    base = {
        "job_id": "job_index_00000001",
        "status": "interrupted",
        "error_code": "INDEX_PROCESS_INTERRUPTED",
        "scanned": 2,
        "error_count": 0,
    }
    interrupted = jobs.IndexJobs.public(base)
    assert interrupted["data_status"] == "degraded"
    assert interrupted["data_gaps"] == ["INDEX_PROCESS_INTERRUPTED"]
    assert interrupted["next_action"] == "reconcile_index_job"
    assert interrupted["result_unknown"] is True

    unknown = jobs.IndexJobs.public({**base, "status": "mystery", "error_code": None})
    assert unknown["data_status"] == "degraded"
    assert unknown["data_gaps"] == ["INDEX_STATUS_UNKNOWN"]
    assert unknown["next_action"] == "reconcile_index_job"
    assert unknown["result_unknown"] is True


def test_missing_source_returns_explicit_unavailable_error(index_runtime, monkeypatch):
    monkeypatch.setenv("GW_ALLOWED_ROOTS", "")
    with TestClient(create_app()) as client:
        response = submit(client, key="c10-t3-no-source")
    assert response.status_code == 503
    detail = response.json()["detail"]
    assert detail["code"] == "INDEX_SOURCE_NOT_AVAILABLE"
    assert detail["data_status"] == "not_integrated"
    assert detail["unavailable"] is True


def test_missing_media_dependency_is_fail_closed(index_runtime, monkeypatch):
    video = index_runtime / "offline.mp4"
    video.write_bytes(b"not a real video; the dependency gate must fire first")
    with TestClient(create_app()) as client:
        accepted = submit(client, key="c10-t3-video")
        assert accepted.status_code == 202, accepted.text
        assert wait(client, accepted.json()["job_id"])["status"] == "succeeded"
        assets = client.get(f"{BASE}/assets", headers=AUTH).json()["assets"]
        asset_id = next(item["asset_id"] for item in assets if item["name"] == video.name)

        monkeypatch.setattr(media_probe.shutil, "which", lambda name: None)
        response = client.get(f"{BASE}/assets/{asset_id}/video/frame?at=0", headers=AUTH)

    assert response.status_code == 503
    detail = response.json()["detail"]
    assert detail["code"] == "FFMPEG_NOT_AVAILABLE"
    assert detail["data_status"] == "not_integrated"
    assert detail["unavailable"] is True
    assert "output_name" not in response.text
