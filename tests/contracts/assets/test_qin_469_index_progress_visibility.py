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


def _slow_hash(monkeypatch):
    """把哈希放慢到每个 0.3s，模拟小样本里单个文件耗时数秒的慢探针。"""
    original_hash = jobs.IndexJobs._hash

    def slow_hash(self, *args):
        digest = original_hash(self, *args)
        time.sleep(HASH_DELAY_SECONDS)
        return digest

    monkeypatch.setattr(jobs.IndexJobs, "_hash", slow_hash)


def test_scanned_progress_advances_while_running(ingest_runtime, monkeypatch):
    """AC1/AC2：小样本慢文件在运行中即可读到递增的非零 scanned。"""
    _slow_hash(monkeypatch)
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


def test_progress_save_contention_does_not_fail_ingest(ingest_runtime, monkeypatch):
    """节流落盘变频繁后，状态盘瞬时不可写不得被记成素材文件错误而整单失败。

    状态盘用 tmp + os.replace 原子替换；Windows 上轮询进度的并发读与替换相撞会抛
    PermissionError(OSError)。节流把落盘频率从"几乎不写"提到每 0.2s，若让它冒泡到
    扫描的 per-file 错误处理，一次抖动就足以让整批入库以 INDEX_FILE_ERRORS 失败。
    """
    original_save = jobs.IndexJobs._save
    calls = {"count": 0}

    def flaky_save(self, job):
        calls["count"] += 1
        if job.get("phase") == "scanning" and calls["count"] % 2 == 0:
            raise OSError(13, "state store busy")
        return original_save(self, job)

    monkeypatch.setattr(jobs.IndexJobs, "_save", flaky_save)
    _slow_hash(monkeypatch)
    observed = []
    with TestClient(create_app()) as client:
        created = _submit(client, "qin-469-save-contention", hash_files=True)
        assert created.status_code == 202, created.text
        job_id = created.json()["job_id"]
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            job = _read_job(client, job_id)
            if job["status"] in jobs.TERMINAL:
                break
            observed.append(job["progress"]["scanned"])
            time.sleep(0.01)
        final = _wait_job(client, job_id)

    assert final["status"] == "succeeded", final
    assert final["progress"] == {
        "scanned": FILE_COUNT,
        "error_count": 0,
        "phase": "completed",
    }
    assert any(0 < scanned < FILE_COUNT for scanned in observed), (
        f"抖动下仍应能观察到递增进度，仅观测到: {sorted(set(observed))}"
    )
