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

"""QIN-469（MVP1-T7 BUG-F4）入库运行中进度可见契约。

回归点：小样本长耗时文件凑不满 25 项批次时 `progress.scanned` 全程为 0，
只有任务结束才一次性跳到总数，长任务无法观察进度。
"""

import time

import pytest
from fastapi.testclient import TestClient

from gw.api.app import create_app
from gw.asset_registry import index_jobs as jobs

BASE = "/api/asset-registry"
AUTH = {"Authorization": "Bearer ingest-owner", "X-User-Role": "editor"}
FILE_COUNT = 6
HASH_DELAY_SECONDS = 0.3


@pytest.fixture
def ingest_runtime(tmp_path, monkeypatch):
    monkeypatch.setenv("GW_AUTH_MODE", "local")
    monkeypatch.setenv("GW_DATA_DIR", str(tmp_path / "gw-data"))
    root = tmp_path / "sources"
    root.mkdir()
    for index in range(FILE_COUNT):
        (root / f"clip-{index}.txt").write_bytes(f"source {index}".encode())
    monkeypatch.setenv("GW_ALLOWED_ROOTS", str(root))
    if jobs._default:
        jobs._default.shutdown()
    jobs._default = None
    yield root
    if jobs._default:
        jobs._default.shutdown()
    jobs._default = None


def _read_job(client, job_id):
    return client.get(f"{BASE}/workspace-jobs/{job_id}", headers=AUTH).json()["job"]


def _wait_job(client, job_id, deadline_seconds=15):
    deadline = time.monotonic() + deadline_seconds
    while time.monotonic() < deadline:
        job = _read_job(client, job_id)
        if job["status"] in jobs.TERMINAL:
            return job
        time.sleep(0.02)
    pytest.fail("后台索引任务未及时结束")


def _submit(client, key, **payload):
    return client.post(
        f"{BASE}/reindex",
        headers={**AUTH, "Idempotency-Key": key},
        json={"background": True, **payload},
    )


def test_scanned_progress_advances_while_running(ingest_runtime, monkeypatch):
    """AC1/AC2：小样本慢文件在运行中即可读到递增的非零 scanned。"""
    original_hash = jobs.IndexJobs._hash

    def slow_hash(self, *args):
        digest = original_hash(self, *args)
        time.sleep(HASH_DELAY_SECONDS)
        return digest

    monkeypatch.setattr(jobs.IndexJobs, "_hash", slow_hash)
    observed = []
    with TestClient(create_app()) as client:
        created = _submit(client, "qin-469-scan-progress", hash_files=True)
        assert created.status_code == 202, created.text
        job_id = created.json()["job_id"]
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            job = _read_job(client, job_id)
            if job["status"] in jobs.TERMINAL:
                break
            observed.append((job["status"], job["progress"]["scanned"]))
            time.sleep(0.01)
        final = _wait_job(client, job_id)

    assert final["status"] == "succeeded", final
    assert final["progress"] == {
        "scanned": FILE_COUNT,
        "error_count": 0,
        "phase": "completed",
    }
    running = [scanned for status, scanned in observed if status == "running"]
    assert running, "未采到运行中的进度快照"
    assert any(0 < scanned < FILE_COUNT for scanned in running), (
        f"运行中未读到递增进度，仅观测到: {sorted(set(running))}"
    )


def test_progress_reports_only_known_counts(ingest_runtime):
    """未知进度不伪造：运行中只报已扫描数与阶段，不虚报未计算的 total。"""
    with TestClient(create_app()) as client:
        created = _submit(client, "qin-469-progress-shape")
        assert created.status_code == 202, created.text
        job_id = created.json()["job_id"]
        job = _read_job(client, job_id)
        assert set(job["progress"]) == {"scanned", "error_count", "phase"}
        assert job["progress"]["scanned"] >= 0
        assert _wait_job(client, job_id)["status"] == "succeeded"
