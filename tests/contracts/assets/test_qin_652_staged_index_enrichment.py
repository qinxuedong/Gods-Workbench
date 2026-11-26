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
import os
import threading
import time
from pathlib import Path

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
                        "keyframe_status": "pending",
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
        # 收尾窗口过后再回读：避免只在 _enrich 返回前采到 cancelled 的时序假绿。
        time.sleep(1.0)
        assert _read_job(client, job_id)["status"] == "cancelled"
        assert len(_assets()) == 2, "快照提交后的基础条目不得被取消回滚"


def test_ac2_cancelled_enrichment_is_not_projected_back_to_success(
    index_runtime, monkeypatch
):
    """B-1 回归：取消后跨过富化收尾 / 收据投影窗口，多次回读仍为 cancelled。

    审核官与 QA 在 head `c603705c` 上实测：`POST /cancel` 返回 cancelled 后
    约 0.21s 再读变成 `succeeded/completed`，原因是 `_enrich` 无条件
    `_finalize_enrich_result` 清除收据 `enrich_pending` 并写终态收据，
    `_committed_truth` 随即投影成功。本用例要求：收据保持快照态、
    `_Stopped` 不计入 `enrich.failed`、快照条目保留。
    """
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
        created = _submit(client, "qin-652-cancel-projection", hash_files=True)
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
        assert cancelled.json()["job"]["status"] == "cancelled"
        gate.set()
        observed = []
        for _ in range(8):
            time.sleep(0.25)
            observed.append(_read_job(client, job_id))
        statuses = {job["status"] for job in observed}
        assert statuses == {"cancelled"}, [
            (job["status"], job.get("progress", {}).get("phase")) for job in observed
        ]
        assert all(
            job.get("progress", {}).get("phase") != "completed" for job in observed
        )
        # 取消不是条目失败：_Stopped 只停池，不得计入 enrich.failed。
        assert observed[-1]["enrich"]["failed"] == 0, observed[-1]["enrich"]
        receipt = repo.state().read()["index_job_receipts"][
            f"{job_id}:{observed[-1]['attempt']}"
        ]
        assert receipt.get("enrich_pending") is True, "取消后收据必须保持快照态"
    assert len(_assets()) == 2, "取消不得回滚已入库快照条目"


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


# ----------------------------------------------------------------------
# 独立审核（comment 7154305645）FIX_REQUIRED 的回归用例：
#   R-1 同秒等长改写必须刷新哈希与镜头数据；
#   R-2 版本变化后旧富化结果失效，失败/取消后下一次 reindex 能自愈；
#   R-3 旧任务的迟到富化批次不得覆盖新版本；
#   R-4 并发上限是全局口径（跨两个索引任务）。
# ----------------------------------------------------------------------


def _asset(name):
    return next(asset for asset in _assets().values() if asset["name"] == name)


def _same_second_bump(path, base_ns):
    """把 mtime 调回基线所在的那一秒、但纳秒不同（模拟同秒等长改写）。"""
    within_second = base_ns % 1_000_000_000
    delta = 1_000 if within_second <= 999_999_000 - 1_000 else -1_000
    os.utime(path, ns=(base_ns, base_ns + delta))
    return path.stat()


def test_review_same_second_same_size_rewrite_refreshes_enrichment(
    index_runtime, monkeypatch
):
    """R-1：秒级 modified_at 与大小都相同，但内容已改写 → 不得复用旧结果。

    旧实现仅比 `size_bytes` + 秒级 `modified_at`，同秒等长改写会被判成
    「未变化」而跳过哈希与视频探测；这里用 `mtime_ns` 版本指纹区分。
    """

    text = index_runtime / "same-size.txt"
    first_text = b"payload-AAAA"
    second_text = b"payload-BBBB"
    assert len(first_text) == len(second_text)
    text.write_bytes(first_text)

    video = index_runtime / "same-size.mp4"
    first_video = b"video-AAAA"
    second_video = b"video-BBBB"
    assert len(first_video) == len(second_video)
    video.write_bytes(first_video)

    def fake_video(path):
        raw = Path(path).read_bytes()
        duration = 10.0 if raw.endswith(b"AAAA") else 20.0
        return (
            {
                "video_segments": [{"start_sec": 0.0, "end_sec": duration}],
                "video_metadata": {
                    "scene_detection_status": "complete",
                    "keyframe_status": "pending",
                    "duration_sec": duration,
                },
            },
            duration,
        )

    monkeypatch.setattr(jobs, "_video_index_data", fake_video)
    text_before = text.stat()
    video_before = video.stat()

    with TestClient(create_app()) as client:
        created = _submit(client, "qin-652-r1-first", hash_files=True)
        assert created.status_code == 202, created.text
        assert _wait_job(client, created.json()["job_id"])["status"] == "succeeded"
        before_modified = {name: _asset(name)["modified_at"] for name in ("same-size.txt", "same-size.mp4")}
        assert _asset("same-size.txt")["sha256"] == hashlib.sha256(first_text).hexdigest()

        text.write_bytes(second_text)
        text_after = _same_second_bump(text, text_before.st_mtime_ns)
        video.write_bytes(second_video)
        video_after = _same_second_bump(video, video_before.st_mtime_ns)
        # 前提：旧判据看到的仍是「同一个秒」，只有纳秒级指纹变了。
        assert int(text_after.st_mtime) == int(text_before.st_mtime)
        assert int(video_after.st_mtime) == int(video_before.st_mtime)
        assert text_after.st_size == text_before.st_size

        second = _submit(client, "qin-652-r1-second", hash_files=True)
        assert second.status_code == 202, second.text
        final = _wait_job(client, second.json()["job_id"])

    assert final["status"] == "succeeded", final
    assert final["result"]["enrich"]["skipped"] == 0, final["result"]["enrich"]
    refreshed_text = _asset("same-size.txt")
    refreshed_video = _asset("same-size.mp4")
    # 秒级 modified_at 意外滚到下一秒时用例前提失效，先显式守住前提。
    assert refreshed_text["modified_at"] == before_modified["same-size.txt"]
    assert refreshed_video["modified_at"] == before_modified["same-size.mp4"]
    assert refreshed_text["sha256"] == hashlib.sha256(second_text).hexdigest()
    assert refreshed_video["sha256"] == hashlib.sha256(second_video).hexdigest()
    assert refreshed_video["duration"] == 20.0
    assert refreshed_video["metadata"]["video_metadata"]["duration_sec"] == 20.0


def test_review_changed_version_retries_after_enrich_failure(index_runtime, monkeypatch):
    """R-2：版本变化后旧结果必须失效，富化失败也不得让下次 reindex 误跳过。"""

    text = index_runtime / "doc.txt"
    video = index_runtime / "clip.mp4"
    text.write_bytes(b"version-one")
    video.write_bytes(b"clip-one")

    fail = {"on": False}
    original_hash = jobs.IndexJobs._enrich_hash_value

    def flaky_hash(self, task, job_id, attempt):
        if fail["on"]:
            raise RuntimeError("hash channel down")
        return original_hash(self, task, job_id, attempt)

    def flaky_video(path):
        if fail["on"]:
            raise RuntimeError("probe channel down")
        return (
            {
                "video_segments": [{"start_sec": 0.0, "end_sec": 7.0}],
                "video_metadata": {
                    "width": 640,
                    "height": 360,
                    "frame_rate": 25.0,
                    "scene_detection_status": "complete",
                    "keyframe_status": "pending",
                    "duration_sec": 7.0,
                },
            },
            7.0,
        )

    monkeypatch.setattr(jobs.IndexJobs, "_enrich_hash_value", flaky_hash)
    monkeypatch.setattr(jobs, "_video_index_data", flaky_video)

    with TestClient(create_app()) as client:
        first = _submit(client, "qin-652-r2-first", hash_files=True)
        assert first.status_code == 202, first.text
        assert _wait_job(client, first.json()["job_id"])["status"] == "succeeded"
        assert _asset("doc.txt")["sha256"] == hashlib.sha256(b"version-one").hexdigest()
        assert _asset("clip.mp4")["duration"] == 7.0
        assert _asset("clip.mp4")["frame_rate"] == 25.0

        # 两个文件都改写（长度变化 → 版本指纹必然不同），随后富化全线失败。
        text.write_bytes(b"version-two-longer")
        video.write_bytes(b"clip-two-longer")
        fail["on"] = True
        second = _submit(client, "qin-652-r2-second", hash_files=True)
        assert second.status_code == 202, second.text
        failed_run = _wait_job(client, second.json()["job_id"])

        assert failed_run["status"] == "succeeded", failed_run
        assert failed_run["result"]["enrich"]["failed"] == 3, failed_run["result"]["enrich"]
        stale_text = _asset("doc.txt")
        stale_video = _asset("clip.mp4")
        # 旧版本结果必须已失效：否则下次 reindex 会把「新文件信息 + 旧结果」
        # 判成未变化，旧哈希/镜头数据永远不再刷新。
        assert "sha256" not in stale_text, stale_text.get("sha256")
        assert "hash_mode" not in stale_text
        assert "sha256" not in stale_video
        assert "duration" not in stale_video
        assert "video_metadata" not in (stale_video.get("metadata") or {})
        # 顶层详情字段同属「上一份内容」的结论，必须一并失效。
        for stale_field in ("width", "height", "frame_rate"):
            assert stale_field not in stale_video, stale_video.get(stale_field)
        assert stale_text["scan_version"]["size_bytes"] == len(b"version-two-longer")

        fail["on"] = False
        third = _submit(client, "qin-652-r2-third", hash_files=True)
        assert third.status_code == 202, third.text
        healed = _wait_job(client, third.json()["job_id"])

    assert healed["status"] == "succeeded", healed
    assert healed["result"]["enrich"]["failed"] == 0
    assert healed["result"]["enrich"]["skipped"] == 0, healed["result"]["enrich"]
    healed_text = _asset("doc.txt")
    healed_video = _asset("clip.mp4")
    assert healed_text["sha256"] == hashlib.sha256(b"version-two-longer").hexdigest()
    assert healed_video["sha256"] == hashlib.sha256(b"clip-two-longer").hexdigest()
    assert healed_video["duration"] == 7.0
    assert healed_video["metadata"]["video_metadata"]["scene_detection_status"] == "complete"


def test_review_late_enrich_batch_cannot_overwrite_newer_version(
    index_runtime, monkeypatch
):
    """R-3：旧任务晚到的富化批次不得把资产改回扫描时的旧版本。"""

    text = index_runtime / "doc.txt"
    text.write_bytes(b"scan-one")
    (index_runtime / "other.txt").write_bytes(b"other")

    gate = threading.Event()
    entered = threading.Event()
    original_hash = jobs.IndexJobs._enrich_hash_value

    def blocked(self, task, job_id, attempt):
        if task["name"] == "doc.txt":
            entered.set()
            assert gate.wait(15), "富化未放行"
        return original_hash(self, task, job_id, attempt)

    monkeypatch.setattr(jobs.IndexJobs, "_enrich_hash_value", blocked)

    newer_version = {
        "size_bytes": len(b"scan-two"),
        "mtime_ns": 9876543210123456,
        "ino": 4242,
        "dev": 7,
    }
    newer_sha = hashlib.sha256(b"scan-two").hexdigest()

    with TestClient(create_app()) as client:
        created = _submit(client, "qin-652-r3", hash_files=True)
        assert created.status_code == 202, created.text
        job_id = created.json()["job_id"]
        assert entered.wait(10), "富化未启动"

        # 模拟并发的后一个 reindex 已把该资产推进到新版本（同 relpath、新内容）。
        def advance(raw):
            for asset in raw["assets"].values():
                if asset.get("name") == "doc.txt":
                    asset["sha256"] = newer_sha
                    asset["hash_mode"] = "full"
                    asset["size_bytes"] = newer_version["size_bytes"]
                    asset["scan_version"] = dict(newer_version)

        repo.state().mutate(advance)
        gate.set()
        final = _wait_job(client, job_id)

    assert final["status"] == "succeeded", final
    asset = _asset("doc.txt")
    assert asset["sha256"] == newer_sha, "旧批次的哈希覆盖了新版本"
    assert asset["size_bytes"] == newer_version["size_bytes"]
    assert asset["scan_version"] == newer_version


def test_review_enrich_concurrency_is_global_across_jobs(index_runtime, monkeypatch):
    """R-4：两个索引任务并发时，全局仍只有 1 个视频探测、2 个哈希。"""

    for index in range(4):
        (index_runtime / f"clip-{index}.mp4").write_bytes(b"fake-mp4")
    for index in range(4):
        (index_runtime / f"still-{index}.txt").write_bytes(b"still")

    lock = threading.Lock()
    counter = {"video": 0, "video_max": 0, "hash": 0, "hash_max": 0}
    overlap = {"active": set(), "max": 0}

    def fake_video(path):
        with lock:
            counter["video"] += 1
            counter["video_max"] = max(counter["video_max"], counter["video"])
        try:
            time.sleep(0.2)
            return (
                {
                    "video_segments": [{"start_sec": 0.0, "end_sec": 1.0}],
                    "video_metadata": {
                        "scene_detection_status": "complete",
                        "keyframe_status": "pending",
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
            time.sleep(0.12)
            return original_hash(self, *args)
        finally:
            with lock:
                counter["hash"] -= 1

    original_enrich = jobs.IndexJobs._enrich

    def tracked_enrich(self, job, *args, **kwargs):
        with lock:
            overlap["active"].add(job["job_id"])
            overlap["max"] = max(overlap["max"], len(overlap["active"]))
        try:
            return original_enrich(self, job, *args, **kwargs)
        finally:
            with lock:
                overlap["active"].discard(job["job_id"])

    monkeypatch.setattr(jobs, "_video_index_data", fake_video)
    monkeypatch.setattr(jobs.IndexJobs, "_hash", tracked_hash)
    monkeypatch.setattr(jobs.IndexJobs, "_enrich", tracked_enrich)

    with TestClient(create_app()) as client:
        first = _submit(client, "qin-652-r4-a", hash_files=True)
        second = _submit(client, "qin-652-r4-b", hash_files=True)
        assert first.status_code == 202, first.text
        assert second.status_code == 202, second.text
        first_job = _wait_job(client, first.json()["job_id"])
        second_job = _wait_job(client, second.json()["job_id"])

    assert first_job["status"] == "succeeded", first_job
    assert second_job["status"] == "succeeded", second_job
    # 前提：两个任务的富化阶段确实重叠，否则并发断言会假绿。
    assert overlap["max"] == 2, overlap
    assert counter["video_max"] == 1, counter
    assert counter["hash_max"] == 2, counter
    assert counter["video"] == 0 and counter["hash"] == 0


def test_video_detail_fields_project_nested_metadata_for_ui():
    """详情面板读顶层字段：投影口径对缺字段/异常值保持安全。"""
    assert jobs.video_detail_fields(
        {
            "video_metadata": {
                "width": 1920,
                "height": 1080,
                "frame_rate": 30000 / 1001,
            }
        }
    ) == {"width": 1920, "height": 1080, "frame_rate": round(30000 / 1001, 6)}
    assert jobs.video_detail_fields({"video_metadata": {"width": 0, "height": 1080}}) == {}
    assert jobs.video_detail_fields({"video_metadata": {"width": "wide"}}) == {}
    assert jobs.video_detail_fields({"video_metadata": {"frame_rate": float("inf")}}) == {}
    assert jobs.video_detail_fields({"video_metadata": {}}) == {}
    assert jobs.video_detail_fields({}) == {}
    assert jobs.video_detail_fields(None) == {}


def test_video_index_data_records_real_frame_rate(monkeypatch):
    """帧率由 ffprobe 补齐；探测失败只缺该键，不影响其余元数据。"""
    from gw.asset_registry import media_probe
    from gw.media import scene_detector

    scenes = [
        {
            "start_sec": 0.0,
            "end_sec": 1.0,
            "width": 640,
            "height": 360,
            "codec": "h264",
            "bit_rate": 1000,
            "container": "mp4",
            "source_url": None,
            "tags": [],
            "thumbnail": None,
        }
    ]
    monkeypatch.setattr(scene_detector, "detect_scenes", lambda _path: scenes)
    monkeypatch.setattr(
        "gw.media.keyframe_extractor.decode_keyframe_to_memory",
        lambda *_args: (_ for _ in ()).throw(AssertionError("索引不得解码关键帧")),
    )

    def probed(_path):
        return {"width": 640, "height": 360, "duration_seconds": 1.0, "fps": 29.97}

    monkeypatch.setattr(media_probe, "probe", probed)
    metadata, _ = jobs._video_index_data(Path("clip.mp4"))
    assert metadata["video_metadata"]["frame_rate"] == 29.97
    assert metadata["video_metadata"]["keyframe_status"] == "pending"
    assert jobs.video_detail_fields(metadata) == {
        "width": 640,
        "height": 360,
        "frame_rate": 29.97,
    }

    def offline(_path):
        raise RuntimeError("ffprobe not available")

    monkeypatch.setattr(media_probe, "probe", offline)
    metadata, _ = jobs._video_index_data(Path("clip.mp4"))
    assert "frame_rate" not in metadata["video_metadata"]
    assert metadata["video_metadata"]["width"] == 640


def test_ac2_enriched_video_writes_detail_fields(index_runtime, monkeypatch):
    """富化完成后资产顶层带分辨率/帧率，详情面板不再显示「读取中」。"""
    (index_runtime / "clip.mp4").write_bytes(b"clip")

    def fake_video(_path):
        return (
            {
                "video_segments": [{"start_sec": 0.0, "end_sec": 2.0}],
                "video_metadata": {
                    "width": 320,
                    "height": 240,
                    "frame_rate": 24.0,
                    "scene_detection_status": "complete",
                    "keyframe_status": "pending",
                    "duration_sec": 2.0,
                },
            },
            2.0,
        )

    monkeypatch.setattr(jobs, "_video_index_data", fake_video)
    with TestClient(create_app()) as client:
        created = _submit(client, "qin-652-detail-fields", hash_files=False)
        assert created.status_code == 202, created.text
        final = _wait_job(client, created.json()["job_id"])
    assert final["status"] == "succeeded", final
    asset = _asset("clip.mp4")
    assert (asset["width"], asset["height"]) == (320, 240)
    assert asset["frame_rate"] == 24.0
    assert asset["metadata"]["video_metadata"]["width"] == 320


@pytest.mark.parametrize("name", ["doc.txt", "clip.mp4"])
@pytest.mark.parametrize("hash_files", [True, False])
def test_independent_old_snapshot_cannot_revert_newer_version(
    index_runtime, monkeypatch, name, hash_files
):
    path = index_runtime / name
    path.write_bytes(b"old")
    old_scan_ready = threading.Event()
    release_old_scan = threading.Event()
    original_scan = jobs.IndexJobs._scan
    seen = []

    def interleaved(self, job_id, attempt, roots, request):
        scanned = original_scan(self, job_id, attempt, roots, request)
        if not seen:
            seen.append(job_id)
            old_scan_ready.set()
            assert release_old_scan.wait(15)
        return scanned

    monkeypatch.setattr(jobs.IndexJobs, "_scan", interleaved)
    monkeypatch.setattr(jobs, "_video_index_data", lambda _path: (
        {"video_segments": [{"start_sec": 0, "end_sec": 7}],
         "video_metadata": {"scene_detection_status": "complete", "width": 320,
                            "height": 240, "frame_rate": 24.0}}, 7.0
    ))
    with TestClient(create_app()) as client:
        a = _submit(client, "audit-old", hash_files=hash_files)
        assert a.status_code == 202, a.text
        assert old_scan_ready.wait(10)
        try:
            path.write_bytes(b"new-content-longer")
            b = _submit(client, "audit-new", hash_files=hash_files)
            assert b.status_code == 202, b.text
            final_b = _wait_job(client, b.json()["job_id"])
            assert final_b["status"] == "succeeded", final_b
            newer_asset = _asset(name)
            if hash_files:
                assert newer_asset["sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
        finally:
            release_old_scan.set()
        _wait_job(client, a.json()["job_id"])
    observed = _asset(name)
    assert observed["size_bytes"] == path.stat().st_size, observed
    if hash_files:
        assert observed.get("sha256") == hashlib.sha256(path.read_bytes()).hexdigest(), observed
    assert observed["scan_version"] == newer_asset["scan_version"]
    if name.endswith(".mp4"):
        for key in ("duration", "width", "height", "frame_rate", "metadata"):
            assert observed[key] == newer_asset[key]


def test_reindex_accepts_current_file_with_older_mtime(index_runtime):
    path = index_runtime / "doc.txt"
    path.write_bytes(b"first")
    initial_time = path.stat().st_mtime_ns
    with TestClient(create_app()) as client:
        first = _submit(client, "mtime-first", hash_files=True)
        assert _wait_job(client, first.json()["job_id"])["status"] == "succeeded"
        path.write_bytes(b"replacement-with-older-timestamp")
        os.utime(path, ns=(initial_time - 5_000_000_000, initial_time - 5_000_000_000))
        second = _submit(client, "mtime-second", hash_files=True)
        assert _wait_job(client, second.json()["job_id"])["status"] == "succeeded"
    asset = _asset("doc.txt")
    assert asset["size_bytes"] == path.stat().st_size
    assert asset["sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
