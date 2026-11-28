# -*- coding: utf-8 -*-
# Copyright 2026 qinxuedong (Gods-Workbench Authors)
# SPDX-License-Identifier: FSL-1.1-Apache-2.0
#
# Licensed under the Functional Source License, Version 1.1, Apache 2.0
# Future License (the "License"); you may not use this file except in
# compliance with the License. You may obtain a copy of the License at
#     https://fsl.software/
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""QIN-619（#748）：疑似重复素材按 size 筛候选后计算完整 SHA 契约测试。

覆盖 AC1–AC14、GW-DoD-v1 异步/进程/持久化风险项及拟议限速默认阈值。
所有夹具路径均使用 pytest tmp_path，不含本机绝对路径、盘符或真实素材。
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import hashlib
import os
from pathlib import Path
import threading
import time

import pytest
from fastapi.testclient import TestClient

from gw.api.app import create_app
from gw.asset_registry import collector, dedup_confirm, governance, index_jobs, repository as repo
from gw.core import storage
from gw.core.errors import CleanroomException

ADMIN = {"Authorization": "Bearer qin619-admin", "X-User-Role": "admin"}
EDITOR = {"Authorization": "Bearer qin619-editor", "X-User-Role": "editor"}
BASE = "/api/asset-registry"


@pytest.fixture(autouse=True)
def dedup_runtime(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    sources_dir = tmp_path / "sources"
    data_dir.mkdir(parents=True, exist_ok=True)
    sources_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("GW_AUTH_MODE", "local")
    monkeypatch.setenv("GW_DATA_DIR", str(data_dir))
    monkeypatch.setenv("GW_ALLOWED_ROOTS", str(sources_dir))
    dedup_confirm.shutdown()
    dedup_confirm._default = None
    if index_jobs._default:
        index_jobs._default.shutdown()
    index_jobs._default = None
    yield sources_dir, data_dir
    dedup_confirm.shutdown()
    dedup_confirm._default = None
    if index_jobs._default:
        index_jobs._default.shutdown()
    index_jobs._default = None


def _register_disk_asset(
    root: Path,
    relpath: str,
    content: bytes,
    *,
    sha256: str | None = None,
    hash_mode: str | None = None,
    sparse_size: int | None = None,
    head_bytes: bytes | None = None,
    mid_bytes: bytes | None = None,
    tail_bytes: bytes | None = None,
) -> tuple[str, Path]:
    target = root / relpath
    target.parent.mkdir(parents=True, exist_ok=True)
    if sparse_size is not None:
        with target.open("wb") as handle:
            handle.truncate(sparse_size)
            if head_bytes:
                handle.seek(0)
                handle.write(head_bytes)
            if mid_bytes:
                handle.seek(sparse_size // 2)
                handle.write(mid_bytes)
            if tail_bytes:
                handle.seek(max(0, sparse_size - len(tail_bytes)))
                handle.write(tail_bytes)
    else:
        target.write_bytes(content)

    st = target.lstat()
    created = repo.import_assets(
        [{"name": target.name, "kind": "video", "size_bytes": int(st.st_size)}],
        "indexed",
        None,
    )
    asset_id = created["assets"][0]["asset_id"]
    root_id = storage.root_identity_id(root)

    def mutate(raw):
        item = raw["assets"][asset_id]
        item["root_id"] = root_id
        item["storage_root_id"] = root_id
        item["display_path"] = relpath
        item["canonical_relpath"] = storage.canonical_relpath(relpath)
        item["size_bytes"] = int(st.st_size)
        if sha256 is not None:
            item["sha256"] = sha256
        if hash_mode is not None:
            item["hash_mode"] = hash_mode
        return raw

    repo.state().mutate(mutate)
    return asset_id, target


def _wait_dedup_job(client: TestClient, job_id: str, timeout: float = 15.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        resp = client.get(f"{BASE}/workspace-jobs/{job_id}", headers=ADMIN)
        assert resp.status_code == 200, resp.text
        job = resp.json()["job"]
        if job["status"] in dedup_confirm.TERMINAL:
            return job
        time.sleep(0.02)
    pytest.fail(f"查重确认任务 {job_id} 超时未终结")


def test_ac1_ac2_ac4_size_bucket_filters_unique_and_confirms_true_duplicates(dedup_runtime):
    """AC1/AC2/AC4/AC6：唯一大小跳过完整 SHA 计算；同 size 计算完整 SHA 后仅真重复标 confirmed，且零自动删除。"""
    sources, _ = dedup_runtime
    same_payload = b"same-content-block-" * 32
    diff_payload = b"diff-content-block-" * 32  # 与 same_payload 字节长度相同但内容不同
    assert len(same_payload) == len(diff_payload)
    unique_payload = b"unique-size-file-should-not-hash"

    dup_a, path_a = _register_disk_asset(sources, "dup_a.mp4", same_payload)
    dup_b, path_b = _register_disk_asset(sources, "dup_b.mp4", same_payload)
    other_same_size, path_c = _register_disk_asset(sources, "other_same_size.mp4", diff_payload)
    unique_id, path_unique = _register_disk_asset(sources, "unique.mp4", unique_payload)

    touched_paths: list[str] = []
    svc = dedup_confirm.service()
    svc.configure_test_hooks(read_chunk_hook=lambda path, _off: touched_paths.append(Path(path).name))

    with TestClient(create_app()) as client:
        # AC1：提交前 3 个同 size 文件应生成 1 组疑似（size_candidate），且后端禁止提前确认
        before = client.get(f"{BASE}/governance/suggestions", headers=ADMIN).json()
        dups_before = before["suggestions"]["duplicate_files"]
        assert len(dups_before) == 1
        assert dups_before[0]["dedup_status"] == "suspected"
        assert dups_before[0]["match_basis"] == "size_candidate"

        rejected = client.post(
            f"{BASE}/governance/suggestions/confirm",
            headers=ADMIN,
            json={"suggestion_id": dups_before[0]["suggestion_id"], "expected_version": before["revision"]},
        )
        assert rejected.status_code == 409
        assert rejected.json()["detail"]["code"] == "DEDUP_NOT_CONFIRMED"

        # 提交确认任务
        submitted = client.post(
            f"{BASE}/governance/dedup-confirm-jobs",
            headers={**ADMIN, "Idempotency-Key": "qin619-ac1"},
            json={},
        )
        assert submitted.status_code == 202, submitted.text
        job = _wait_dedup_job(client, submitted.json()["job_id"])
        assert job["status"] == "succeeded", job
        assert job["result"]["unique_size_skipped"] == 1
        assert job["result"]["verified_full_sha"] == 3

        # 唯一 size 文件绝未被读取计算完整 SHA
        assert "unique.mp4" not in touched_paths
        assert {"dup_a.mp4", "dup_b.mp4", "other_same_size.mp4"} <= set(touched_paths)

        # AC2：同内容组标为 confirmed；不同内容的同 size 文件不再留组
        after = client.get(f"{BASE}/governance/suggestions", headers=ADMIN).json()
        dups_after = after["suggestions"]["duplicate_files"]
        assert len(dups_after) == 1
        assert dups_after[0]["dedup_status"] == "confirmed"
        assert dups_after[0]["match_basis"] == "full_sha256"
        assert set(dups_after[0]["asset_ids"]) == {dup_a, dup_b}
        assert dups_after[0]["content_hash"] == hashlib.sha256(same_payload).hexdigest()

        # AC4：全部源文件与资产均未被删除或归档
        assert all(p.is_file() for p in (path_a, path_b, path_c, path_unique))
        raw = repo.state().read()
        assert raw["assets"][unique_id].get("full_sha256_cache") is None
        assert all(not raw["assets"][aid]["archived"] for aid in (dup_a, dup_b, other_same_size, unique_id))

        # 已确认组现在可以建立 duplicate 关系
        confirmed_resp = client.post(
            f"{BASE}/governance/suggestions/confirm",
            headers=ADMIN,
            json={"suggestion_id": dups_after[0]["suggestion_id"], "expected_version": after["revision"]},
        )
        assert confirmed_resp.status_code == 200, confirmed_resp.text


def test_ac3_sampled_never_upgrades_and_divergent_full_sha_clears_false_positive(dedup_runtime):
    """AC3：sampled 不得写成 full 或确认；首尾相同中段不同的超大文件核验后解除误报且保留 sampled。"""
    sources, _ = dedup_runtime
    # 构造两个 > 4MiB 稀疏或缩小测试文件（直接构造 16MiB 稀疏文件在默认 512MiB 之上也只需截断；此处用真实 512MiB+1 稀疏文件）
    oversize = index_jobs._HASH_LIMIT_BYTES + 4096
    head = b"SAME-HEAD-" * 64
    tail = b"SAME-TAIL-" * 64
    # 采样摘要相同，但中段内容不同
    id_a, path_a = _register_disk_asset(
        sources, "big_a.bin", b"", sparse_size=oversize, head_bytes=head, mid_bytes=b"MID-AAA", tail_bytes=tail
    )
    id_b, path_b = _register_disk_asset(
        sources, "big_b.bin", b"", sparse_size=oversize, head_bytes=head, mid_bytes=b"MID-BBB", tail_bytes=tail
    )

    with TestClient(create_app()) as client:
        # 开启 hash_files=True 扫描：应写入 sampled（不写 full）
        scan = client.post(
            f"{BASE}/reindex",
            headers={**ADMIN, "Idempotency-Key": "qin619-scan-sampled"},
            json={"background": True, "hash_files": True},
        )
        assert scan.status_code == 202
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            st = client.get(f"{BASE}/workspace-jobs/{scan.json()['job_id']}", headers=ADMIN).json()["job"]
            if st["status"] in index_jobs.TERMINAL:
                assert st["status"] == "succeeded"
                break
            time.sleep(0.02)

        raw = repo.state().read()
        assert raw["assets"][id_a]["hash_mode"] == "sampled"
        assert raw["assets"][id_b]["hash_mode"] == "sampled"
        assert raw["assets"][id_a]["sha256"] == raw["assets"][id_b]["sha256"]
        sampled_digest = raw["assets"][id_a]["sha256"]

        # 确认前：只标 suspected（sampled_sha256），绝不标 confirmed
        before = client.get(f"{BASE}/governance/suggestions", headers=ADMIN).json()
        entry = before["suggestions"]["duplicate_files"][0]
        assert entry["dedup_status"] == "suspected"
        assert entry["match_basis"] == "sampled_sha256"

        # 跑完整 SHA 确认：为了加速 512MiB 稀疏零读取，临时调高本次任务吞吐（仍受真实流式读取校验）
        submitted = client.post(
            f"{BASE}/governance/dedup-confirm-jobs",
            headers={**ADMIN, "Idempotency-Key": "qin619-oversize-confirm"},
            json={"max_read_bytes_per_sec": 0, "chunk_size_bytes": 4 * 1024 * 1024},
        )
        assert submitted.status_code == 202
        job = _wait_dedup_job(client, submitted.json()["job_id"], timeout=30.0)
        assert job["status"] == "succeeded"

        # 完整 SHA 不同，不再保留误报重复组，同时保留原有 sampled hash_mode 与 shot_source_key 稳定
        after = client.get(f"{BASE}/governance/suggestions", headers=ADMIN).json()
        assert after["suggestions"]["duplicate_files"] == []
        raw_after = repo.state().read()
        assert raw_after["assets"][id_a]["hash_mode"] == "sampled"
        assert raw_after["assets"][id_a]["sha256"] == sampled_digest
        assert raw_after["assets"][id_a]["full_sha256_cache"]["sha256"] != raw_after["assets"][id_b]["full_sha256_cache"]["sha256"]
        assert repo.shot_source_key(raw_after["assets"][id_a]) == f"sha256:{sampled_digest}"


def test_ac5_cache_invalidation_on_change_mid_read_and_new_same_size_arrival(dedup_runtime):
    """AC5：读中修改不提交脏结果并失效旧缓存；文件变更后旧确认失效；新同 size 文件加入被发现。"""
    sources, _ = dedup_runtime
    payload = b"ac5-content-12345678" * 16
    id_a, path_a = _register_disk_asset(sources, "a.bin", payload)
    id_b, path_b = _register_disk_asset(sources, "b.bin", payload)

    with TestClient(create_app()) as client:
        job = _wait_dedup_job(
            client,
            client.post(f"{BASE}/governance/dedup-confirm-jobs", headers={**ADMIN, "Idempotency-Key": "ac5-1"}, json={}).json()["job_id"],
        )
        assert job["status"] == "succeeded"

        # GET 不探测物理文件，快照确认态持续到下一次显式后台核验。
        time.sleep(0.02)
        path_a.write_bytes(b"ac5-modified-2345678" * 16)
        suggestions = client.get(f"{BASE}/governance/suggestions", headers=ADMIN).json()
        assert suggestions["suggestions"]["duplicate_files"][0]["dedup_status"] == "confirmed"

        # 加入第 3 个同 size 文件且与 b 内容相同 → 显式后台任务发现新文件；读中修改 a 时，a 失败不写脏缓存，b/c 仍有效。
        id_c, path_c = _register_disk_asset(sources, "c.bin", payload)
        svc = dedup_confirm.service()
        injected_once = {"done": False}

        def tamper_mid_read(path: Path, offset: int) -> None:
            if Path(path).name == "a.bin" and not injected_once["done"]:
                injected_once["done"] = True
                time.sleep(0.02)
                Path(path).write_bytes(b"tampered-mid-read-xy" * 16)

        svc.configure_test_hooks(chunk_size_bytes=1024, read_chunk_hook=tamper_mid_read)
        job2 = _wait_dedup_job(
            client,
            client.post(f"{BASE}/governance/dedup-confirm-jobs", headers={**ADMIN, "Idempotency-Key": "ac5-2"}, json={}).json()["job_id"],
        )
        assert job2["status"] == "failed"
        assert job2["error_code"] == "DEDUP_FILE_ERRORS"
        raw = repo.state().read()
        assert raw["assets"][id_a].get("full_sha256_cache") is None
        assert raw["assets"][id_c]["full_sha256_cache"]["verified"] is True


def test_ac6_default_index_remains_non_full_sha(dedup_runtime):
    """AC6：默认索引提交保持 hash_files=False，不进行全库完整 SHA 普查。"""
    sources, _ = dedup_runtime
    (sources / "one.txt").write_bytes(b"no-full-scan")
    with TestClient(create_app()) as client:
        resp = client.post(
            f"{BASE}/reindex",
            headers={**ADMIN, "Idempotency-Key": "ac6-default"},
            json={"background": True},
        )
        assert resp.status_code == 202
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            st = client.get(f"{BASE}/workspace-jobs/{resp.json()['job_id']}", headers=ADMIN).json()["job"]
            if st["status"] in index_jobs.TERMINAL:
                break
            time.sleep(0.02)
    assets = list(repo.state().read()["assets"].values())
    assert len(assets) == 1
    assert "sha256" not in assets[0]
    assert "full_sha256_cache" not in assets[0]


def test_ac7_rate_limit_and_concurrency_caps_are_enforced(dedup_runtime):
    """AC7：验证拟议默认值与可注入聚合读带宽、并发工作线程上限（含越限可观测耗时与并发探针）。"""
    assert dedup_confirm.DEFAULT_MAX_READ_BYTES_PER_SEC == 20 * 1024 * 1024
    assert dedup_confirm.DEFAULT_MAX_CONCURRENCY == 2
    assert dedup_confirm.DEFAULT_CHUNK_SIZE_BYTES == 1024 * 1024

    sources, _ = dedup_runtime
    payload = b"R" * (64 * 1024)  # 64 KiB × 4 文件 = 256 KiB 总读取
    for i in range(4):
        _register_disk_asset(sources, f"rate_{i}.bin", payload)

    svc = dedup_confirm.service()
    active_lock = threading.Lock()
    active_now = {"count": 0, "peak": 0}

    def slow_hook(_path: Path, _off: int) -> None:
        with active_lock:
            active_now["count"] += 1
            active_now["peak"] = max(active_now["peak"], active_now["count"])
        time.sleep(0.02)
        with active_lock:
            active_now["count"] -= 1

    svc.configure_test_hooks(read_chunk_hook=slow_hook)

    with TestClient(create_app()) as client:
        t0 = time.monotonic()
        resp = client.post(
            f"{BASE}/governance/dedup-confirm-jobs",
            headers={**ADMIN, "Idempotency-Key": "ac7-rate"},
            json={
                "max_read_bytes_per_sec": 256 * 1024,  # 256 KiB/s
                "max_concurrency": 2,
                "chunk_size_bytes": 32 * 1024,
            },
        )
        assert resp.status_code == 202
        job = _wait_dedup_job(client, resp.json()["job_id"])
        elapsed = time.monotonic() - t0
        assert job["status"] == "succeeded"
        # 256 KiB 在 256 KiB/s 限速 + 64 KiB 初始桶下至少耗费 ~0.7s（远高于无限制时的 <0.1s）
        assert elapsed >= 0.55, f"聚合读带宽限速未生效，耗时仅 {elapsed:.3f}s"
        assert svc._peak_active_readers <= 2
        assert active_now["peak"] <= 2


def test_ac8_pause_stops_reads_and_resumes_from_file_checkpoint_without_version_mix(dedup_runtime):
    """AC8：暂停后停止新增读、保留已完成文件级 checkpoint；若暂停期文件变更则重算，不混用版本。"""
    sources, _ = dedup_runtime
    p1 = b"A" * (16 * 1024)
    p2 = b"B" * (16 * 1024)
    id_a, path_a = _register_disk_asset(sources, "cp_a.bin", p1)
    id_b, path_b = _register_disk_asset(sources, "cp_b.bin", p1)
    id_c, path_c = _register_disk_asset(sources, "cp_c.bin", p2)
    id_d, path_d = _register_disk_asset(sources, "cp_d.bin", p2)

    svc = dedup_confirm.service()
    seen_reads: list[str] = []
    pause_gate = threading.Event()
    resume_gate = threading.Event()

    def pause_hook(path: Path, offset: int) -> None:
        seen_reads.append(Path(path).name)
        if Path(path).name == "cp_c.bin" and offset == 0 and not pause_gate.is_set():
            pause_gate.set()
            resume_gate.wait(timeout=5.0)

    svc.configure_test_hooks(read_chunk_hook=pause_hook)

    with TestClient(create_app()) as client:
        sub = client.post(
            f"{BASE}/governance/dedup-confirm-jobs",
            headers={**ADMIN, "Idempotency-Key": "ac8-pause"},
            json={"max_concurrency": 1, "chunk_size_bytes": 8 * 1024, "max_read_bytes_per_sec": 0},
        )
        assert sub.status_code == 202
        job_id = sub.json()["job_id"]
        assert pause_gate.wait(timeout=5.0), "未走到 cp_c.bin 读取断点"

        detail = client.get(f"{BASE}/workspace-jobs/{job_id}", headers=ADMIN).json()["job"]
        paused = client.post(
            f"{BASE}/workspace-jobs/{job_id}/pause",
            headers=ADMIN,
            json={"expected_version": detail["version"]},
        )
        assert paused.status_code == 200
        assert paused.json()["job"]["status"] == "paused"
        resume_gate.set()
        time.sleep(0.15)
        reads_at_pause = len(seen_reads)
        time.sleep(0.15)
        assert len(seen_reads) == reads_at_pause, "暂停后不得继续读取新文件块"

        # 确认前两个已完成项已写盘且不重算；但假设我们把 cp_a.bin 修改了，恢复时应发现版本变了并重算 cp_a.bin，而 cp_b.bin 不重算！
        time.sleep(0.02)
        path_a.write_bytes(b"Z" * (16 * 1024))
        seen_after_resume: list[str] = []
        svc.configure_test_hooks(read_chunk_hook=lambda path, _off: seen_after_resume.append(Path(path).name))

        cur = client.get(f"{BASE}/workspace-jobs/{job_id}", headers=ADMIN).json()["job"]
        resumed = client.post(
            f"{BASE}/workspace-jobs/{job_id}/resume",
            headers=ADMIN,
            json={"expected_version": cur["version"]},
        )
        assert resumed.status_code == 200
        done = _wait_dedup_job(client, job_id)
        assert done["status"] == "succeeded"

        # cp_b.bin 已完成且版本未变 → 恢复后绝不重算；cp_a.bin 版本变了 + 未完成文件 → 重算
        assert "cp_b.bin" not in seen_after_resume
        assert "cp_a.bin" in seen_after_resume
        assert "cp_c.bin" in seen_after_resume
        assert "cp_d.bin" in seen_after_resume
        raw = repo.state().read()
        assert raw["assets"][id_a]["full_sha256_cache"]["sha256"] == hashlib.sha256(b"Z" * (16 * 1024)).hexdigest()
        assert raw["assets"][id_b]["full_sha256_cache"]["sha256"] == hashlib.sha256(p1).hexdigest()
        assert raw["assets"][id_c]["full_sha256_cache"]["sha256"] == hashlib.sha256(p2).hexdigest()
        assert raw["assets"][id_d]["full_sha256_cache"]["sha256"] == hashlib.sha256(p2).hexdigest()


def test_ac9_ac13_restart_does_not_auto_run_and_cold_reads_consistent_state(dedup_runtime):
    """AC9/AC13：进程重启默认不自动抢跑后台任务，收敛为 interrupted，冷读一致且需显式 retry 恢复。"""
    sources, _ = dedup_runtime
    payload = b"restart-recovery-" * 64
    id_a, _ = _register_disk_asset(sources, "r1.bin", payload)
    id_b, _ = _register_disk_asset(sources, "r2.bin", payload)

    # 先通过真实 API 提交一次并取消，获得真实认证主体的持久化任务壳；随后改回 running 模拟进程崩溃前状态
    with TestClient(create_app()) as client:
        sub = client.post(
            f"{BASE}/governance/dedup-confirm-jobs",
            headers={**ADMIN, "Idempotency-Key": "ac9-seed"},
            json={},
        )
        assert sub.status_code == 202
        job_id = sub.json()["job_id"]
        _wait_dedup_job(client, job_id)

    svc = dedup_confirm.service()
    svc.shutdown()
    dedup_confirm._default = None

    def reset_to_crashed_running(raw):
        job = raw["jobs"][job_id]
        job["status"] = "running"
        job["phase"] = "hashing"
        job["result"] = None
        job["error_code"] = None
        for cand in job["_candidates"]:
            cand["file_state"] = "pending"
            cand["sha256"] = None
        return raw

    dedup_confirm._DedupJobState(
        dedup_confirm.JOB_STORE_NAMESPACE,
        lambda: {"sequence": 0, "generation": 0, "jobs": {}, "keys": {}},
    ).mutate(reset_to_crashed_running)

    def clear_cache(raw):
        for aid in (id_a, id_b):
            raw["assets"][aid].pop("full_sha256_cache", None)
        return raw

    repo.state().mutate(clear_cache)

    # 冷启动新实例：不得自动跑，状态必须转 interrupted
    cold = dedup_confirm.service()
    read_calls: list[str] = []
    cold.configure_test_hooks(read_chunk_hook=lambda p, _o: read_calls.append(Path(p).name))
    time.sleep(0.15)
    assert read_calls == [], "重启后默认不得自动抢占磁盘计算完整 SHA"

    with TestClient(create_app()) as client:
        detail = client.get(f"{BASE}/workspace-jobs/{job_id}", headers=ADMIN).json()
        assert detail["job"]["status"] == "interrupted"
        assert detail["job"]["error_code"] == "DEDUP_PROCESS_INTERRUPTED"
        assert detail["actions"]["retry"] is True

        # 显式 retry 恢复（携带 expected_version）
        retried = client.post(
            f"{BASE}/workspace-jobs/{job_id}/retry",
            headers=ADMIN,
            json={"expected_version": detail["job"]["version"]},
        )
        assert retried.status_code == 200
        done = _wait_dedup_job(client, job_id)
        assert done["status"] == "succeeded"
        assert set(read_calls) == {"r1.bin", "r2.bin"}


@pytest.mark.parametrize("slow_ms", [1500, 5000])
def test_ac10_hot_paths_not_blocked_by_slow_dedup_reads(dedup_runtime, slow_ms):
    """AC10：确认哈希遇 1500ms 与 5000ms 受控慢读时，资产导入与目录浏览热路径不阻塞。"""
    sources, _ = dedup_runtime
    payload = b"slow-read-" * 64
    _register_disk_asset(sources, "slow_a.bin", payload)
    _register_disk_asset(sources, "slow_b.bin", payload)

    svc = dedup_confirm.service()
    reading_started = threading.Event()
    release_slow = threading.Event()

    def slow_read(_path: Path, _off: int) -> None:
        reading_started.set()
        release_slow.wait(timeout=slow_ms / 1000.0)

    svc.configure_test_hooks(read_chunk_hook=slow_read)

    with TestClient(create_app()) as client:
        sub = client.post(
            f"{BASE}/governance/dedup-confirm-jobs",
            headers={**ADMIN, "Idempotency-Key": f"ac10-slow-{slow_ms}"},
            json={},
        )
        assert sub.status_code == 202
        assert reading_started.wait(timeout=3.0)

        t0 = time.monotonic()
        imported = client.post(
            f"{BASE}/assets/import",
            headers=EDITOR,
            json={"items": [{"name": f"hot_import_{slow_ms}.txt", "kind": "document", "size_bytes": 12}]},
        )
        listed = client.get(f"{BASE}/assets", headers=EDITOR)
        hot_elapsed = time.monotonic() - t0
        release_slow.set()

        assert imported.status_code == 200, imported.text
        assert listed.status_code == 200, listed.text
        # 热路径绝不被 1500ms / 5000ms 慢读阻塞
        assert hot_elapsed < (slow_ms / 1000.0) * 0.5, f"热路径耗时过长: {hot_elapsed:.3f}s (slow={slow_ms}ms)"
        _wait_dedup_job(client, sub.json()["job_id"])


def test_ac11_ac12_observability_projection_and_stale_generation_discard(dedup_runtime):
    """AC11/AC12：确认任务经 workspace-jobs 与 /api/observability/tasks 可观测；取消后旧代际写入被丢弃。"""
    sources, _ = dedup_runtime
    payload = b"gen-cancel-" * 32
    id_a, _ = _register_disk_asset(sources, "g1.bin", payload)
    id_b, _ = _register_disk_asset(sources, "g2.bin", payload)

    svc = dedup_confirm.service()
    hook_entered = threading.Event()
    hook_release = threading.Event()

    def block_first(_path: Path, _off: int) -> None:
        hook_entered.set()
        hook_release.wait(timeout=5.0)

    svc.configure_test_hooks(read_chunk_hook=block_first)

    with TestClient(create_app()) as client:
        sub = client.post(
            f"{BASE}/governance/dedup-confirm-jobs",
            headers={**ADMIN, "Idempotency-Key": "ac12-cancel"},
            json={},
        )
        assert sub.status_code == 202
        job_id = sub.json()["job_id"]
        assert hook_entered.wait(timeout=3.0)

        cur = client.get(f"{BASE}/workspace-jobs/{job_id}", headers=ADMIN).json()["job"]
        gen_old = cur["generation"]
        cancelled = client.post(
            f"{BASE}/workspace-jobs/{job_id}/cancel",
            headers=ADMIN,
            json={"expected_version": cur["version"]},
        )
        assert cancelled.status_code == 200
        assert cancelled.json()["job"]["status"] == "cancelled"
        hook_release.set()

        # 模拟旧代际迟到提交：必须被单调代际门禁拒绝，不写入缓存
        committed = svc._commit_verified_file(
            job_id, 1, gen_old, id_a, hashlib.sha256(payload).hexdigest(), (sources / "g1.bin").lstat()
        )
        assert committed is False
        assert repo.state().read()["assets"][id_a].get("full_sha256_cache") is None

        # AC11：观测中心 tasks 查询兼容 canceled/cancelled 并透出 asset.dedup.confirm
        obs = client.get("/api/observability/tasks?status=canceled", headers=ADMIN)
        assert obs.status_code == 200
        matched = [item for item in obs.json()["items"] if item["job_id"] == job_id]
        assert len(matched) == 1
        assert matched[0]["job_type"] == dedup_confirm.JOB_TYPE_DEDUP_CONFIRM
        assert matched[0]["status"] == "cancelled"


def test_ac14_worker_lock_cold_hot_stale_pid_reuse_unresponsive_and_exit_cleanup(tmp_path):
    """AC14：冷启动/热启动/死锁与损坏锁唯一赢家恢复/PID复用/存活无响应拒绝删锁/双阶段退出清理。"""
    lock_root = tmp_path / "lock_data"
    lock_root.mkdir()
    now_ns = [1_000_000_000_000]

    # 1. 冷启动与 starting / running 退出清理
    lock1 = dedup_confirm.DedupWorkerLock(
        lock_root,
        process_inspector=lambda pid: dedup_confirm.ProcessIdentity(True, 11111 if pid == os.getpid() else None),
        clock_ns=lambda: now_ns[0],
    )
    acquired = lock1.acquire("job_dedup_00000001", 1)
    assert acquired["status"] == "starting"
    assert (lock_root / dedup_confirm.LOCK_FILENAME).is_file()
    # 热启动：健康实例在位，第二个实例不得抢占
    lock2 = dedup_confirm.DedupWorkerLock(
        lock_root,
        process_inspector=lambda _pid: dedup_confirm.ProcessIdentity(True, 11111),
        clock_ns=lambda: now_ns[0] + 1_000_000_000,
    )
    with pytest.raises(dedup_confirm.DedupLockError) as hot_err:
        lock2.acquire("job_dedup_00000002", 1)
    assert hot_err.value.code == "DEDUP_WORKER_ALREADY_RUNNING"

    # starting 态退出清理
    lock1.release()
    assert not (lock_root / dedup_confirm.LOCK_FILENAME).exists()

    # running 态退出清理
    lock1.acquire("job_dedup_00000001", 1)
    lock1.mark_running()
    lock1.release()
    assert not (lock_root / dedup_confirm.LOCK_FILENAME).exists()

    # 2. 持有者存活但心跳超时无响应 → 绝不删锁、绝不接管，明确报错
    holder = dedup_confirm.DedupWorkerLock(
        lock_root,
        process_inspector=lambda _pid: dedup_confirm.ProcessIdentity(True, 22222),
        clock_ns=lambda: now_ns[0],
        heartbeat_ttl_seconds=1.0,
    )
    holder_payload = holder.acquire("job_dedup_00000001", 1)
    holder.mark_running()
    contender = dedup_confirm.DedupWorkerLock(
        lock_root,
        process_inspector=lambda _pid: dedup_confirm.ProcessIdentity(True, 22222),
        clock_ns=lambda: now_ns[0] + 5_000_000_000,
        heartbeat_ttl_seconds=1.0,
    )
    with pytest.raises(dedup_confirm.DedupLockError) as unresp_err:
        contender.acquire("job_dedup_00000002", 1)
    assert unresp_err.value.code == "DEDUP_WORKER_UNRESPONSIVE"
    assert (lock_root / dedup_confirm.LOCK_FILENAME).is_file()
    holder.release()

    # 3. PID 复用（pid 存活但 start_time_ns 不匹配）→ 判定原持有者已死，并发恢复仅 1 个赢家
    stale_pid = 424242
    (lock_root / dedup_confirm.LOCK_FILENAME).write_text(
        '{"lock_version":1,"instance_id":"dead_old","pid":424242,"process_start_time_ns":100,"status":"running","heartbeat_at_ns":1}',
        encoding="utf-8",
    )

    def pid_reused_inspector(pid: int) -> dedup_confirm.ProcessIdentity:
        if pid == stale_pid:
            return dedup_confirm.ProcessIdentity(True, 999999)  # 不同启动时间 = PID 已复用
        return dedup_confirm.ProcessIdentity(True, 55555)

    winners: list[str] = []
    errors: list[str] = []

    def try_recover(idx: int) -> None:
        w = dedup_confirm.DedupWorkerLock(
            lock_root,
            process_inspector=pid_reused_inspector,
            clock_ns=lambda: now_ns[0],
        )
        try:
            w.acquire(f"job_dedup_0000001{idx}", 1)
            winners.append(w.instance_id)
            time.sleep(0.05)
            w.release()
        except dedup_confirm.DedupLockError as exc:
            errors.append(exc.code)

    with ThreadPoolExecutor(max_workers=2) as pool:
        f1 = pool.submit(try_recover, 1)
        f2 = pool.submit(try_recover, 2)
        f1.result()
        f2.result()

    assert len(winners) == 1, f"并发恢复赢家必须唯一，实际 winners={winners}, errors={errors}"
    assert len(errors) == 1

    # 4. 锁损坏也可恢复
    (lock_root / dedup_confirm.LOCK_FILENAME).write_text("{corrupted-json", encoding="utf-8")
    recovered = dedup_confirm.DedupWorkerLock(lock_root, clock_ns=lambda: now_ns[0])
    recovered.acquire("job_dedup_00000099", 1)
    recovered.release()


def test_collector_collect_sha256_is_read_only_reused_without_mutation(dedup_runtime):
    """只读复用 collector 的 collect_sha256，不阻塞收集且不反向改写 collect_sha256。"""
    from gw.core.asset_visibility import asset_owner_key
    from gw.core.auth import require_authenticated

    sources, _ = dedup_runtime
    payload = b"collector-reused-payload-" * 16
    owner_key = asset_owner_key(require_authenticated(ADMIN["Authorization"], ADMIN["X-User-Role"]))
    res = collector.store_content(payload, "collected.png", owner=owner_key)
    aid_collect = res["asset"]["asset_id"]
    collect_digest = res["asset"]["metadata"]["collect_sha256"]
    id_disk, _ = _register_disk_asset(sources, "disk_copy.png", payload)

    svc = dedup_confirm.service()
    read_names: list[str] = []
    svc.configure_test_hooks(read_chunk_hook=lambda p, _o: read_names.append(Path(p).name))

    with TestClient(create_app()) as client:
        sub = client.post(
            f"{BASE}/governance/dedup-confirm-jobs",
            headers={**ADMIN, "Idempotency-Key": "collector-reuse"},
            json={},
        )
        assert sub.status_code == 202
        job = _wait_dedup_job(client, sub.json()["job_id"])
        assert job["status"] == "succeeded"
        assert job["result"]["reused_cached_sha"] == 1
        assert job["result"]["verified_full_sha"] == 1
        # collected 文件未重复读盘
        assert all(name == "disk_copy.png" for name in read_names)
        # 形成 confirmed 查重组且未改写 collect_sha256
        sug = client.get(f"{BASE}/governance/suggestions", headers=ADMIN).json()
        dups = sug["suggestions"]["duplicate_files"]
        assert len(dups) == 1 and dups[0]["dedup_status"] == "confirmed"
        assert set(dups[0]["asset_ids"]) == {aid_collect, id_disk}
        assert repo.state().read()["assets"][aid_collect]["metadata"]["collect_sha256"] == collect_digest


def test_r1_fix1_paused_job_survives_restart_and_resumes_to_succeeded(dedup_runtime):
    """R1 必改 1：pause → 进程重启保留 paused 且不自动跑 → 调用 resume 重新派发线程续算成功。"""
    sources, _ = dedup_runtime
    p1 = b"R1P1-" * 2048
    p2 = b"R1P2-" * 2048
    id_a, _ = _register_disk_asset(sources, "rp_a.bin", p1)
    id_b, _ = _register_disk_asset(sources, "rp_b.bin", p1)
    id_c, _ = _register_disk_asset(sources, "rp_c.bin", p2)
    id_d, _ = _register_disk_asset(sources, "rp_d.bin", p2)

    svc = dedup_confirm.service()
    hit_c = threading.Event()
    release_c = threading.Event()

    def pause_on_c(path: Path, offset: int) -> None:
        if Path(path).name == "rp_c.bin" and offset == 0 and not hit_c.is_set():
            hit_c.set()
            release_c.wait(timeout=5.0)

    svc.configure_test_hooks(read_chunk_hook=pause_on_c)

    with TestClient(create_app()) as client:
        sub = client.post(
            f"{BASE}/governance/dedup-confirm-jobs",
            headers={**ADMIN, "Idempotency-Key": "r1-fix1"},
            json={"max_concurrency": 1, "chunk_size_bytes": 4096, "max_read_bytes_per_sec": 0},
        )
        assert sub.status_code == 202
        job_id = sub.json()["job_id"]
        assert hit_c.wait(timeout=5.0)
        cur = client.get(f"{BASE}/workspace-jobs/{job_id}", headers=ADMIN).json()["job"]
        paused = client.post(
            f"{BASE}/workspace-jobs/{job_id}/pause",
            headers=ADMIN,
            json={"expected_version": cur["version"]},
        )
        assert paused.status_code == 200
        assert paused.json()["job"]["status"] == "paused"
        release_c.set()

    # 模拟进程重启：销毁内存服务并冷启动新实例
    svc.shutdown()
    dedup_confirm._default = None
    cold = dedup_confirm.service()
    cold_reads: list[str] = []
    cold.configure_test_hooks(read_chunk_hook=lambda p, _o: cold_reads.append(Path(p).name))
    time.sleep(0.15)
    assert cold_reads == [], "重启恢复后 paused 任务不得自动抢跑"

    with TestClient(create_app()) as client:
        detail = client.get(f"{BASE}/workspace-jobs/{job_id}", headers=ADMIN).json()["job"]
        assert detail["status"] == "paused"
        assert detail["progress"]["processed"] >= 2

        resumed = client.post(
            f"{BASE}/workspace-jobs/{job_id}/resume",
            headers=ADMIN,
            json={"expected_version": detail["version"]},
        )
        assert resumed.status_code == 200
        assert resumed.json()["job"]["status"] == "running"

        done = _wait_dedup_job(client, job_id)
        assert done["status"] == "succeeded", done
        # rp_a / rp_b 已在重启前完成，重启 resume 后不得重复读盘
        assert "rp_a.bin" not in cold_reads
        assert "rp_b.bin" not in cold_reads
        assert "rp_c.bin" in cold_reads and "rp_d.bin" in cold_reads
        for aid, raw_bytes in ((id_a, p1), (id_b, p1), (id_c, p2), (id_d, p2)):
            assert repo.state().read()["assets"][aid]["full_sha256_cache"]["sha256"] == hashlib.sha256(raw_bytes).hexdigest()


def test_r1_fix2_operate_pause_preserves_concurrent_worker_checkpoint(dedup_runtime):
    """R1 必改 2：operate(pause) 与工作线程 _commit_verified_file 交错时不得覆写丢失单文件 checkpoint。"""
    sources, _ = dedup_runtime
    payload = b"R1F2-" * 2048
    id_a, path_a = _register_disk_asset(sources, "race_a.bin", payload)
    id_b, path_b = _register_disk_asset(sources, "race_b.bin", payload)

    svc = dedup_confirm.service()
    before_pause_mutate = threading.Event()
    allow_pause_mutate = threading.Event()

    with TestClient(create_app()) as client:
        # 构造受控窗口：在 operate(pause) 读出 pre_job 之后、mutate_state 之前，插入工作线程提交 race_a
        first_hook_done = threading.Event()
        release_worker = threading.Event()

        def hold_first(path: Path, _offset: int) -> None:
            if not first_hook_done.is_set():
                first_hook_done.set()
                release_worker.wait(timeout=5.0)

        svc.configure_test_hooks(read_chunk_hook=hold_first)
        sub = client.post(
            f"{BASE}/governance/dedup-confirm-jobs",
            headers={**ADMIN, "Idempotency-Key": "r1-fix2"},
            json={"max_concurrency": 1, "chunk_size_bytes": 16 * 1024, "max_read_bytes_per_sec": 0},
        )
        assert sub.status_code == 202
        job_id = sub.json()["job_id"]
        assert first_hook_done.wait(timeout=5.0)

        orig_mutate = svc.store.mutate

        def racing_mutate(fn):
            # 当 operate(pause) 准备调用 mutate_state 时，先让 _commit_verified_file 提交 race_a
            if getattr(fn, "__name__", "") == "mutate_state" and not before_pause_mutate.is_set():
                before_pause_mutate.set()
                allow_pause_mutate.wait(timeout=5.0)
            return orig_mutate(fn)

        svc.store.mutate = racing_mutate

        pause_res: list[int] = []

        def call_pause() -> None:
            cur = client.get(f"{BASE}/workspace-jobs/{job_id}", headers=ADMIN).json()["job"]
            resp = client.post(
                f"{BASE}/workspace-jobs/{job_id}/pause",
                headers=ADMIN,
                json={"expected_version": cur["version"]},
            )
            pause_res.append(resp.status_code)

        t_pause = threading.Thread(target=call_pause)
        t_pause.start()
        assert before_pause_mutate.wait(timeout=5.0)

        # 在 pause 的 pre_job 读取后、mutate_state 前，模拟已通过行 1327 的工作线程原子写入 race_a 的 verified checkpoint（直接走 store.mutate，与真实线程序号一致）
        digest_a = hashlib.sha256(payload).hexdigest()
        st_a = path_a.lstat()

        def commit_worker_checkpoint(raw):
            cur = raw["jobs"][job_id]
            for item in cur.get("_candidates", []):
                if item.get("asset_id") == id_a:
                    item["file_state"] = "verified"
                    item["sha256"] = digest_a
                    item["expected_size_bytes"] = int(st_a.st_size)
                    item["expected_mtime_ns"] = int(st_a.st_mtime_ns)
            cur["processed_candidates"] = 1
            cur["updated_at"] = storage.now_iso()

        orig_mutate(commit_worker_checkpoint)
        allow_pause_mutate.set()
        t_pause.join(timeout=5.0)
        release_worker.set()

        assert pause_res == [200]
        persisted = svc.store.read()["jobs"][job_id]
        assert persisted["status"] == "paused"
        assert persisted["processed_candidates"] == 1
        cand_map = {c["asset_id"]: c for c in persisted["_candidates"]}
        assert cand_map[id_a]["file_state"] == "verified", "operate(pause) 绝不能抹掉并发提交的 verified checkpoint"
        assert cand_map[id_b]["asset_id"] == id_b


def test_r1_fix3_multi_batch_tail_file_changed_is_persisted_and_recomputed(dedup_runtime):
    """R1 必改 3：> max_concurrency*2 候选时，尾部原已 cached 文件中途被磁盘篡改，必须落盘重算而不提前成功。"""
    sources, _ = dedup_runtime
    # 设为同一大小 8192 字节，5 个候选、max_concurrency=1（批大小 2）
    old_tail = b"T" * 8192
    new_tail = b"N" * 8192
    common = b"M" * 8192
    ids = []
    paths = []
    for idx in range(4):
        aid, p = _register_disk_asset(sources, f"batch_{idx}.bin", common)
        ids.append(aid)
        paths.append(p)
    tail_id, tail_path = _register_disk_asset(sources, "batch_4_tail.bin", old_tail)

    # 给尾部第 5 个预先绑定有效的旧 full_sha256_cache，使初始进入候选时 file_state="reused"
    old_tail_digest = hashlib.sha256(old_tail).hexdigest()
    repo.state().mutate(
        lambda raw: raw["assets"][tail_id].update(
            {
                "sha256": old_tail_digest,
                "hash_mode": "full",
                "full_sha256_cache": dedup_confirm.build_full_sha256_cache(
                    old_tail_digest, tail_path.lstat(), source="dedup_confirm"
                ),
            }
        )
        or raw
    )

    svc = dedup_confirm.service()
    tail_mutated = threading.Event()
    read_names: list[str] = []

    def tamper_tail_during_first_batch(path: Path, offset: int) -> None:
        name = Path(path).name
        read_names.append(name)
        if name == "batch_0.bin" and offset == 0 and not tail_mutated.is_set():
            tail_mutated.set()
            time.sleep(0.02)
            tail_path.write_bytes(new_tail)

    svc.configure_test_hooks(read_chunk_hook=tamper_tail_during_first_batch)

    with TestClient(create_app()) as client:
        sub = client.post(
            f"{BASE}/governance/dedup-confirm-jobs",
            headers={**ADMIN, "Idempotency-Key": "r1-fix3"},
            json={"max_concurrency": 1, "chunk_size_bytes": 16 * 1024, "max_read_bytes_per_sec": 0},
        )
        assert sub.status_code == 202
        done = _wait_dedup_job(client, sub.json()["job_id"])
        assert done["status"] == "succeeded"
        assert "batch_4_tail.bin" in read_names, "尾部文件变更后必须落盘回退 pending 并在后续批次重算"
        assert repo.state().read()["assets"][tail_id]["full_sha256_cache"]["sha256"] == hashlib.sha256(new_tail).hexdigest()


def test_r1_fix4_deleted_disk_file_invalidates_confirmed_cache_and_blocks_confirm(dedup_runtime, monkeypatch):
    """GET 零磁盘探测；显式后台核验发现删盘后失效缓存并阻止确认。"""
    sources, _ = dedup_runtime
    payload = b"delete-after-confirm-" * 64
    id_a, path_a = _register_disk_asset(sources, "del_a.mp4", payload)
    id_b, path_b = _register_disk_asset(sources, "del_b.mp4", payload)

    with TestClient(create_app()) as client:
        sub = client.post(
            f"{BASE}/governance/dedup-confirm-jobs",
            headers={**ADMIN, "Idempotency-Key": "r1-fix4"},
            json={},
        )
        assert sub.status_code == 202
        done = _wait_dedup_job(client, sub.json()["job_id"])
        assert done["status"] == "succeeded"

        before = client.get(f"{BASE}/governance/suggestions", headers=ADMIN).json()
        confirmed_entry = before["suggestions"]["duplicate_files"][0]
        assert confirmed_entry["dedup_status"] == "confirmed"

        # 删除文件后，GET 仅按快照读取；删盘到显式核验之间允许短暂保留 confirmed。
        path_b.unlink()
        assert id_a != id_b

        original_is_file = Path.is_file
        original_lstat = Path.lstat
        disk_probe_paths = {path_a, path_b}
        with monkeypatch.context() as patch:
            def forbidden_resolve(*_args, **_kwargs):
                pytest.fail("GET suggestions must not resolve per-asset disk paths")

            def track_is_file(path):
                assert path not in disk_probe_paths, "GET suggestions must not probe asset is_file"
                return original_is_file(path)

            def track_lstat(path, *args, **kwargs):
                assert path not in disk_probe_paths, "GET suggestions must not probe asset lstat"
                return original_lstat(path, *args, **kwargs)

            patch.setattr(dedup_confirm, "resolve_asset_media_path", forbidden_resolve)
            patch.setattr(Path, "is_file", track_is_file)
            patch.setattr(Path, "lstat", track_lstat)
            after = client.get(f"{BASE}/governance/suggestions", headers=ADMIN).json()

        assert after["suggestions"]["duplicate_files"]
        assert after["suggestions"]["duplicate_files"][0]["dedup_status"] == "confirmed"
        revision_before_refresh = repo.readonly_registry_snapshot()["revision"]
        assert repo.state().read()["assets"][id_b]["full_sha256_cache"]
        _candidates, _skipped, missing_cache_ids = dedup_confirm.service()._build_candidates()
        assert id_b in missing_cache_ids, "显式核验必须把删盘旧缓存交给后台失效"

        # 显式后台任务负责发现文件缺失、清除旧确认缓存并推进 revision。
        sub = client.post(
            f"{BASE}/governance/dedup-confirm-jobs",
            headers={**ADMIN, "Idempotency-Key": "r1-fix4-deleted-refresh"},
            json={},
        )
        assert sub.status_code == 202
        done = _wait_dedup_job(client, sub.json()["job_id"])
        assert done["status"] == "succeeded"
        assert repo.state().read()["assets"][id_b].get("full_sha256_cache") is None
        assert repo.readonly_registry_snapshot()["revision"] > revision_before_refresh

        refreshed = client.get(f"{BASE}/governance/suggestions", headers=ADMIN).json()
        dups_after = refreshed["suggestions"]["duplicate_files"]
        assert dups_after and all(item.get("dedup_status") != "confirmed" for item in dups_after)

        old_rejected = client.post(
            f"{BASE}/governance/suggestions/confirm",
            headers=ADMIN,
            json={
                "suggestion_id": confirmed_entry["suggestion_id"],
                "expected_version": refreshed["revision"],
            },
        )
        assert old_rejected.status_code == 404

        suspected_rejected = client.post(
            f"{BASE}/governance/suggestions/confirm",
            headers=ADMIN,
            json={
                "suggestion_id": dups_after[0]["suggestion_id"],
                "expected_version": refreshed["revision"],
            },
        )
        assert suspected_rejected.status_code == 409
        assert suspected_rejected.json()["detail"]["code"] == "DEDUP_NOT_CONFIRMED"


def test_r1_fix5_concurrent_jobs_serialize_and_pause_over_ttl_survives(dedup_runtime):
    """R1 必改 5：并发多个 202 任务有序排队不撞锁失败；pause 超过 30s TTL 释放锁且恢复仍成功。"""
    sources, _ = dedup_runtime
    p1 = b"S1-" * 2048
    p2 = b"S2-" * 2048
    a1, _ = _register_disk_asset(sources, "ser_1a.bin", p1)
    a2, _ = _register_disk_asset(sources, "ser_1b.bin", p1)
    b1, _ = _register_disk_asset(sources, "ser_2a.bin", p2)
    b2, _ = _register_disk_asset(sources, "ser_2b.bin", p2)

    svc = dedup_confirm.service()
    svc.configure_test_hooks(read_chunk_hook=lambda _p, _o: time.sleep(0.02))

    with TestClient(create_app()) as client:
        # 1. 同进程连发两个合法 202 请求：不撞锁失败，先后全部完成
        r1 = client.post(
            f"{BASE}/governance/dedup-confirm-jobs",
            headers={**ADMIN, "Idempotency-Key": "r1-fix5-q1"},
            json={"asset_ids": [a1, a2]},
        )
        r2 = client.post(
            f"{BASE}/governance/dedup-confirm-jobs",
            headers={**ADMIN, "Idempotency-Key": "r1-fix5-q2"},
            json={"asset_ids": [b1, b2]},
        )
        assert r1.status_code == 202 and r2.status_code == 202
        j1 = _wait_dedup_job(client, r1.json()["job_id"])
        j2 = _wait_dedup_job(client, r2.json()["job_id"])
        assert j1["status"] == "succeeded", j1
        assert j2["status"] == "succeeded", j2

        # 2. pause 释放锁后即使期间执行了其他任务，原任务 resume 仍成功且绝不撞锁/无响应
        c1_p = b"PT-" * 2048
        ca, _ = _register_disk_asset(sources, "pt_a.bin", c1_p)
        cb, _ = _register_disk_asset(sources, "pt_b.bin", c1_p)
        hit_pt = threading.Event()
        release_pt = threading.Event()

        def hook_pt(path: Path, offset: int) -> None:
            if Path(path).name == "pt_a.bin" and offset == 0 and not hit_pt.is_set():
                hit_pt.set()
                release_pt.wait(timeout=5.0)

        svc = dedup_confirm.service()
        svc.configure_test_hooks(read_chunk_hook=hook_pt)
        sub_p = client.post(
            f"{BASE}/governance/dedup-confirm-jobs",
            headers={**ADMIN, "Idempotency-Key": "r1-fix5-ttl"},
            json={"asset_ids": [ca, cb], "max_concurrency": 1, "chunk_size_bytes": 4096, "max_read_bytes_per_sec": 0},
        )
        assert sub_p.status_code == 202
        jid_p = sub_p.json()["job_id"]
        assert hit_pt.wait(timeout=5.0)
        cur = client.get(f"{BASE}/workspace-jobs/{jid_p}", headers=ADMIN).json()["job"]
        assert client.post(
            f"{BASE}/workspace-jobs/{jid_p}/pause",
            headers=ADMIN,
            json={"expected_version": cur["version"]},
        ).status_code == 200
        release_pt.set()
        time.sleep(0.15)
        # paused 后主动释放跨进程锁与槽位，同进程新任务仍可执行；随后原任务 resume 照常完成
        assert not (svc.root / dedup_confirm.LOCK_FILENAME).exists()

        svc.configure_test_hooks(read_chunk_hook=lambda _p, _o: None)
        other = client.post(
            f"{BASE}/governance/dedup-confirm-jobs",
            headers={**ADMIN, "Idempotency-Key": "r1-fix5-other"},
            json={"asset_ids": [a1, a2]},
        )
        assert other.status_code == 202
        assert _wait_dedup_job(client, other.json()["job_id"])["status"] == "succeeded"

        cur2 = client.get(f"{BASE}/workspace-jobs/{jid_p}", headers=ADMIN).json()["job"]
        assert client.post(
            f"{BASE}/workspace-jobs/{jid_p}/resume",
            headers=ADMIN,
            json={"expected_version": cur2["version"]},
        ).status_code == 200
        assert _wait_dedup_job(client, jid_p)["status"] == "succeeded"
