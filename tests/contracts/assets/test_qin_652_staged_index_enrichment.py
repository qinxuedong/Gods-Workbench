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

"""QIN-652：快扫入库 + 后台双通道富化的分阶段索引契约。

票面 AC：
- AC1：目录扫描与基础条目入库优先完成，富化不阻塞首屏可见；
- AC2：视频探测与哈希受控并发（PM 裁决：视频 1 / 哈希 2），既有去重与
  采样哈希、ComfyUI 元数据、进度/事件语义完整保留；
- AC3：并发/分阶段单测，不引入重复索引或数据竞争。

设计表（issue #812 评论）：同一 `asset.index` job 内
`scanning → indexed → enriching → completed`；`progress` 保持三键，
富化进度走 job 顶层 `enrich`。
"""

import hashlib
import threading
import time

import pytest
from fastapi.testclient import TestClient

from gw.api.app import create_app
from gw.asset_registry import index_jobs as jobs
from gw.asset_registry import repository as repo

BASE = "/api/asset-registry"
AUTH = {"Authorization": "Bearer qin652-owner", "X-User-Role": "editor"}


@pytest.fixture(autouse=True)
def index_runtime(tmp_path, monkeypatch):
    root = tmp_path / "sources"
    root.mkdir()
    monkeypatch.setenv("GW_AUTH_MODE", "local")
    monkeypatch.setenv("GW_DATA_DIR", str(tmp_path / "gw-data"))
    monkeypatch.setenv("GW_ALLOWED_ROOTS", str(root))
    monkeypatch.delenv("GW_INDEX_ENRICH_CONCURRENCY", raising=False)
    if jobs._default:
        jobs._default.shutdown()
    jobs._default = None
    yield root
    if jobs._default:
        jobs._default.shutdown()
    jobs._default = None


def _read_job(client, job_id):
    return client.get(f"{BASE}/workspace-jobs/{job_id}", headers=AUTH).json()["job"]


def _wait_job(client, job_id, deadline_seconds=25):
    deadline = time.monotonic() + deadline_seconds
    while time.monotonic() < deadline:
        job = _read_job(client, job_id)
        if job["status"] in jobs.TERMINAL:
            return job
        time.sleep(0.02)
    pytest.fail("后台索引任务未及时结束")


def _submit(client, key, **payload):
    return client.post(
        BASE + "/reindex",
        headers={**AUTH, "Idempotency-Key": key},
        json={"background": True, **payload},
    )


def _assets():
    return repo.state().read()["assets"]


def _make_files(root, count, suffix=".txt"):
    for index in range(count):
        (root / f"asset-{index}{suffix}").write_bytes(
            f"qin-652 payload {index}".encode()
        )


def _scanner(monkeypatch):
    """不落盘的最小扫描器：直接调 _scan 生成单阶段基线（QIN-564 同构）。"""
    scanner = object.__new__(jobs.IndexJobs)
    scanner.condition = threading.Condition(threading.RLock())
    monkeypatch.setattr(scanner, "_checkpoint", lambda *_args: {})
    monkeypatch.setattr(scanner, "_save", lambda _job: None)
    return scanner


def _legacy_request(root):
    return {
        "kind": "reindex",
        "roots": ["probe"],
        "scopes": [
            {"root_id": "probe", "prefix": "", "storage_root_id": "probe"}
        ],
        "hash_files": True,
        "max_files": 1000,
    }


def test_ac1_snapshot_is_queryable_while_enrichment_is_still_running(
    index_runtime, monkeypatch
):
    """AC1：基础条目全部入库且可查询时，富化尚未完成（未持 sha256）。"""
    _make_files(index_runtime, 6)
    gate = threading.Event()
    original = jobs.IndexJobs._enrich_hash_value

    def gated(self, task, job_id, attempt):
        assert gate.wait(8), "富化未被放行"
        return original(self, task, job_id, attempt)

    monkeypatch.setattr(jobs.IndexJobs, "_enrich_hash_value", gated)
    with TestClient(create_app()) as client:
        created = _submit(client, "qin-652-snapshot", hash_files=True)
        assert created.status_code == 202, created.text
        job_id = created.json()["job_id"]
        deadline = time.monotonic() + 10
        observed = None
        while time.monotonic() < deadline:
            job = _read_job(client, job_id)
            if job["status"] == "running" and job.get("enrich"):
                observed = job
                break
            if job["status"] in jobs.TERMINAL:
                pytest.fail("任务在富化被阻塞期间提前终结")
            time.sleep(0.01)
        assert observed is not None, "未采到快照提交后的运行中状态"
        assert observed["progress"]["phase"] in {"indexed", "enriching"}
        assert observed["enrich"]["pending"] > 0
        snapshot = _assets()
        assert len(snapshot) == 6
        assert all("sha256" not in asset for asset in snapshot.values())
        assert all("video_segments" not in (asset.get("metadata") or {})
                   for asset in snapshot.values())
        gate.set()
        final = _wait_job(client, job_id)

    assert final["status"] == "succeeded", final
    assert final["progress"] == {
        "scanned": 6,
        "error_count": 0,
        "phase": "completed",
    }
    assert final["result"]["hashed"] == 6
    assert final["result"]["enrich"]["video_concurrency"] == 1
    assert final["result"]["enrich"]["hash_concurrency"] == 2
    assert all(asset.get("sha256") for asset in _assets().values())


def test_ac2_enriched_assets_match_single_phase_scan(index_runtime, monkeypatch):
    """AC2：同夹具对拍，富化后条目与单阶段扫描逐字段等价。"""
    _make_files(index_runtime, 4)
    scanner = _scanner(monkeypatch)
    legacy_items = jobs.IndexJobs._scan(
        scanner, "probe", 1, {"probe": index_runtime}, _legacy_request(index_runtime)
    )
    legacy_by_name = {item["name"]: item for item in legacy_items}
    assert len(legacy_by_name) == 4

    with TestClient(create_app()) as client:
        created = _submit(client, "qin-652-equivalence", hash_files=True)
        assert created.status_code == 202, created.text
        final = _wait_job(client, created.json()["job_id"])
    assert final["status"] == "succeeded", final

    staged_by_name = {asset["name"]: asset for asset in _assets().values()}
    assert set(staged_by_name) == set(legacy_by_name)
    for name, legacy in legacy_by_name.items():
        staged = staged_by_name[name]
        for field in (
            "display_path",
            "canonical_relpath",
            "size_bytes",
            "modified_at",
            "sha256",
            "hash_mode",
        ):
            assert staged.get(field) == legacy.get(field), (name, field)
        assert staged["sha256"] == hashlib.sha256(
            (index_runtime / name).read_bytes()
        ).hexdigest()
        assert staged.get("full_sha256_cache", {}).get("sha256") == staged["sha256"]
        assert not (staged.get("metadata") or {}).get("video_segments")
        assert "duration" not in staged


def test_ac2_video_channel_is_serial_and_hash_channel_is_bounded(
    index_runtime, monkeypatch
):
    """PM 裁决：视频镜头检测并发恒为 1，文件哈希并发为 2（真实运行时观测）。"""
    for index in range(4):
        (index_runtime / f"clip-{index}.mp4").write_bytes(b"fake-mp4")
    (index_runtime / "still.txt").write_bytes(b"still")

    counter = {"video": 0, "video_max": 0, "hash": 0, "hash_max": 0}
    lock = threading.Lock()

    def fake_video(path):
        with lock:
            counter["video"] += 1
            counter["video_max"] = max(counter["video_max"], counter["video"])
        try:
            time.sleep(0.08)
            return (
                {
                    "video_segments": [{"start_sec": 0.0, "end_sec": 1.0}],
                    "video_metadata": {
                        "scene_detection_status": "complete",
                        "keyframe_status": "decoded_in_memory",
                        "duration_sec": 1.0,
                    },
                },
                1.0,
            )
        finally:
            with lock:
                counter["video"] -= 1

    original_hash = jobs.IndexJobs._hash

    def tracked_hash(self, *args):
        with lock:
            counter["hash"] += 1
            counter["hash_max"] = max(counter["hash_max"], counter["hash"])
        try:
            time.sleep(0.05)
            return original_hash(self, *args)
        finally:
            with lock:
                counter["hash"] -= 1

    monkeypatch.setattr(jobs, "_video_index_data", fake_video)
    monkeypatch.setattr(jobs.IndexJobs, "_hash", tracked_hash)

    with TestClient(create_app()) as client:
        created = _submit(client, "qin-652-concurrency", hash_files=True)
        assert created.status_code == 202, created.text
        final = _wait_job(client, created.json()["job_id"])
    assert final["status"] == "succeeded", final
    assert counter["video_max"] == 1, counter
    assert counter["hash_max"] == 2, counter
    assert final["result"]["enrich"]["video_concurrency"] == 1
    assert final["result"]["enrich"]["hash_concurrency"] == 2
    assert final["result"]["hashed"] == 5
    videos = [
        asset for asset in _assets().values() if asset["name"].endswith(".mp4")
    ]
    assert len(videos) == 4
    assert all(
        (asset.get("metadata") or {}).get("video_metadata", {}).get(
            "scene_detection_status"
        ) == "complete"
        for asset in videos
    )


def test_ac3_batches_and_repeated_reindex_never_duplicate_assets(
    index_runtime, monkeypatch
):
    """AC3：跨批次与重复 reindex 不产生重复 asset_id / 不重复计数。"""
    _make_files(index_runtime, 24)  # > ENRICH_BATCH_SIZE(20)，至少两批
    with TestClient(create_app()) as client:
        first = _submit(client, "qin-652-batch-first", hash_files=True)
        assert first.status_code == 202, first.text
        job = _wait_job(client, first.json()["job_id"])
        assert job["status"] == "succeeded", job
        assert job["result"]["indexed"] == 24
        ids = {asset["asset_id"] for asset in _assets().values()}
        assert len(_assets()) == 24 == len(ids)

        second = _submit(client, "qin-652-batch-second", hash_files=True)
        assert second.status_code == 202, second.text
        repeated = _wait_job(client, second.json()["job_id"])
        assert repeated["status"] == "succeeded", repeated
        assert repeated["result"]["indexed"] == 0
        assert repeated["result"]["hashed"] == 24
        assert {asset["asset_id"] for asset in _assets().values()} == ids
        assert len(_assets()) == 24


def test_ac2_enrichment_pause_resume_and_cancel_keep_snapshot(index_runtime, monkeypatch):
    """富化阶段可暂停/恢复；取消后已入库快照保留且 job 为 cancelled。"""
    _make_files(index_runtime, 3)
    gate = threading.Event()
    original = jobs.IndexJobs._enrich_hash_value

    def gated(self, task, job_id, attempt):
        assert gate.wait(8), "富化未被放行"
        return original(self, task, job_id, attempt)

    monkeypatch.setattr(jobs.IndexJobs, "_enrich_hash_value", gated)
    with TestClient(create_app()) as client:
        created = _submit(client, "qin-652-pause", hash_files=True)
        assert created.status_code == 202, created.text
        job_id = created.json()["job_id"]
        deadline = time.monotonic() + 10
        running = None
        while time.monotonic() < deadline:
            job = _read_job(client, job_id)
            if job["status"] == "running" and job.get("enrich"):
                running = job
                break
            time.sleep(0.01)
        assert running is not None
        paused = client.post(
            f"{BASE}/workspace-jobs/{job_id}/pause",
            headers=AUTH,
            json={"expected_version": running["version"]},
        )
        assert paused.status_code == 200, paused.text
        assert paused.json()["job"]["status"] == "paused"
        resumed = client.post(
            f"{BASE}/workspace-jobs/{job_id}/resume",
            headers=AUTH,
            json={"expected_version": paused.json()["job"]["version"]},
        )
        assert resumed.status_code == 200, resumed.text
        gate.set()
        assert _wait_job(client, job_id)["status"] == "succeeded"

    assert len(_assets()) == 3


def test_ac2_cancel_during_enrichment_keeps_indexed_entries(index_runtime, monkeypatch):
    gate = threading.Event()
    entered = threading.Event()
    original = jobs.IndexJobs._enrich_hash_value

    def blocked(self, task, job_id, attempt):
        entered.set()
        gate.wait(8)
        return original(self, task, job_id, attempt)

    monkeypatch.setattr(jobs.IndexJobs, "_enrich_hash_value", blocked)
    _make_files(index_runtime, 2)
    with TestClient(create_app()) as client:
        created = _submit(client, "qin-652-cancel", hash_files=True)
        assert created.status_code == 202, created.text
        job_id = created.json()["job_id"]
        assert entered.wait(8), "富化未启动"
        running = _read_job(client, job_id)
        assert running.get("enrich")
        cancelled = client.post(
            f"{BASE}/workspace-jobs/{job_id}/cancel",
            headers=AUTH,
            json={"expected_version": running["version"]},
        )
        assert cancelled.status_code == 200, cancelled.text
        gate.set()
        final = _wait_job(client, job_id)
        assert final["status"] == "cancelled", final
        assert len(_assets()) == 2, "快照提交后的基础条目不得被取消回滚"


def test_ac2_video_failure_keeps_asset_and_job_succeeds(index_runtime, monkeypatch):
    """单视频探测失败：条目保留 + unavailable/reason_code，job 仍 succeeded。"""
    (index_runtime / "broken.mp4").write_bytes(b"fake-mp4")
    (index_runtime / "ok.txt").write_bytes(b"ok")

    def unavailable(path):
        return (
            {
                "video_segments": [],
                "video_metadata": {
                    "scene_detection_status": "unavailable",
                    "keyframe_status": "unavailable",
                    "reason_code": "SCENE_DETECTION_FAILED",
                    "source_url": None,
                },
            },
            None,
        )

    monkeypatch.setattr(jobs, "_video_index_data", unavailable)
    with TestClient(create_app()) as client:
        created = _submit(client, "qin-652-video-failure", hash_files=True)
        assert created.status_code == 202, created.text
        final = _wait_job(client, created.json()["job_id"])
    assert final["status"] == "succeeded", final
    assert final["result"]["enrich"]["failed"] == 0
    broken = next(
        asset for asset in _assets().values() if asset["name"] == "broken.mp4"
    )
    assert broken["metadata"]["video_metadata"]["reason_code"] == (
        "SCENE_DETECTION_FAILED"
    )
    assert broken.get("sha256"), "视频失败不得影响同条目哈希补齐"


def test_regression_progress_shape_and_result_counts(index_runtime):
    """回归护栏：progress 三键、hash/generation 计数口径、终态收据去标记。"""
    _make_files(index_runtime, 2)
    with TestClient(create_app()) as client:
        created = _submit(client, "qin-652-shape", hash_files=True)
        assert created.status_code == 202, created.text
        job_id = created.json()["job_id"]
        first = _read_job(client, job_id)
        assert set(first["progress"]) == {"scanned", "error_count", "phase"}
        final = _wait_job(client, job_id)
    assert final["status"] == "succeeded", final
    assert set(final["progress"]) == {"scanned", "error_count", "phase"}
    assert final["result"]["hashed"] == 2
    assert final["result"]["hash_sampled_oversize"] == 0
    assert set(final["result"]["generation"]) == {
        "scanned_images",
        "with_metadata",
        "warnings",
        "truncated",
        "comfyui_tagged",
    }
    assert "enrich_pending" not in final["result"]
    receipt = repo.state().read()["index_job_receipts"][
        f"{job_id}:{final['attempt']}"
    ]
    assert "enrich_pending" not in receipt
    assert receipt["hashed"] == 2
