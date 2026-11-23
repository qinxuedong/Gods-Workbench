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

"""QIN-644 资产扫描与缩略图解码并发收敛及节流边界测试。"""

from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from gw.api.app import create_app
from gw.asset_registry import index_jobs as jobs
from gw.core.errors import CleanroomException
from gw.media import repository as media_repo

AUTH = {"Authorization": "Bearer qin-644-owner", "X-User-Role": "editor"}


def test_preview_all_workers_scale_conservatively_with_cpu_count(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """默认并发随 CPU 核数保守收敛，显式传参仍受 PREVIEW_ALL_MAX_WORKERS 封顶。"""
    assert media_repo.PREVIEW_ALL_DEFAULT_WORKERS == 4
    assert media_repo.PREVIEW_ALL_MAX_WORKERS == 8

    for cpus, expected in ((2, 1), (4, 1), (8, 2), (12, 3), (24, 4), (64, 4)):
        monkeypatch.setattr(media_repo.os, "cpu_count", lambda c=cpus: c)
        assert media_repo._preview_all_workers({}) == expected

    assert media_repo._preview_all_workers({"workers": 99}) == 8
    assert media_repo._preview_all_workers({"workers": 0}) == 1


def test_live_preview_decode_is_bounded_by_semaphore_and_releases_on_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """实时冷解码受信号量保护；并发下峰值不超过上限且异常时必释放。"""
    sample = tmp_path / "sample.png"
    sample.write_bytes(b"placeholder")

    active = 0
    peak = 0
    lock = threading.Lock()

    def slow_thumbnail(path: Path, width: int, *, image_format: str = "png") -> bytes:
        nonlocal active, peak
        with lock:
            active += 1
            if active > peak:
                peak = active
        try:
            time.sleep(0.03)
            if width == 999:
                raise CleanroomException(503, "SIMULATED_DECODE_ERROR", "模拟解码失败")
            return b"thumb-bytes"
        finally:
            with lock:
                active -= 1

    monkeypatch.setattr(media_repo, "_thumbnail_bytes", slow_thumbnail)
    monkeypatch.setattr(media_repo, "_PREVIEW_DECODE_CONCURRENCY", 2)
    monkeypatch.setattr(
        media_repo, "_PREVIEW_DECODE_SEMAPHORE", threading.BoundedSemaphore(2)
    )

    with ThreadPoolExecutor(max_workers=6) as pool:
        futures = [
            pool.submit(
                media_repo._live_preview_bytes,
                sample,
                256,
                video=False,
                image_format="webp",
            )
            for _ in range(6)
        ]
        results = [f.result() for f in futures]

    assert len(results) == 6
    assert 1 <= peak <= 2

    # 异常路径必须释放信号量，后续调用不能死锁。
    for _ in range(4):
        with pytest.raises(CleanroomException):
            media_repo._live_preview_bytes(sample, 999, video=False, image_format="webp")

    data, mime = media_repo._live_preview_bytes(
        sample, 256, video=False, image_format="webp"
    )
    assert data == b"thumb-bytes"
    assert mime == "image/webp"


def test_ffmpeg_preview_commands_enforce_single_thread(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """缩略图抽帧与 JXL 编解码子进程命令必须携带 -threads 1。"""
    recorded_cmds: list[list[str]] = []
    source = tmp_path / "clip.mp4"
    source.write_bytes(b"fake-video")

    def fake_run(args: list[str], **kwargs):
        recorded_cmds.append(list(args))
        # _video_frame_bytes 要求落盘目标文件存在
        if args and not args[-1].startswith("pipe:"):
            Path(args[-1]).write_bytes(b"jpeg-bytes")
        return SimpleNamespace(returncode=0, stdout=b"png-or-jxl-bytes", stderr=b"")

    monkeypatch.setattr(media_repo, "_tool", lambda name: "ffmpeg")
    monkeypatch.setattr(
        media_repo,
        "resolve_ffmpeg_tool",
        lambda name: SimpleNamespace(path="ffmpeg"),
    )
    monkeypatch.setattr(media_repo.subprocess, "run", fake_run)

    media_repo._video_frame_bytes(source, 256)
    media_repo._decode_jxl_source(source)
    media_repo._encode_jxl_thumbnail(b"png-bytes")

    assert len(recorded_cmds) >= 3
    for cmd in recorded_cmds:
        assert "-threads" in cmd
        idx = cmd.index("-threads")
        assert cmd[idx + 1] == "1"


def test_index_scan_throttles_periodically_without_losing_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """大目录扫描每 SCAN_THROTTLE_EVERY 个文件让出时间片，且不丢任何文件。"""
    monkeypatch.setenv("GW_AUTH_MODE", "local")
    monkeypatch.setenv("GW_DATA_DIR", str(tmp_path / "gw-data"))
    roots = tmp_path / "roots"
    roots.mkdir()
    monkeypatch.setenv("GW_ALLOWED_ROOTS", str(roots))
    if jobs._default:
        jobs._default.shutdown()
    jobs._default = None

    img = Image.new("RGB", (16, 16), (10, 20, 30))
    total_files = jobs.SCAN_THROTTLE_EVERY + 5
    for i in range(total_files):
        img.save(roots / f"item_{i:03d}.png")

    sleep_calls: list[float] = []
    orig_sleep = time.sleep

    def spy_sleep(sec: float) -> None:
        if abs(sec - jobs.SCAN_THROTTLE_SECONDS) < 1e-6:
            sleep_calls.append(sec)
        orig_sleep(sec)

    monkeypatch.setattr(jobs.time, "sleep", spy_sleep)

    try:
        with TestClient(create_app()) as client:
            resp = client.post(
                "/api/asset-registry/reindex",
                headers={**AUTH, "Idempotency-Key": "qin-644-scan-throttle"},
                json={"background": True, "hash_files": False},
            )
            assert resp.status_code == 202, resp.text
            job_id = resp.json()["job_id"]
            deadline = time.monotonic() + 10
            job = None
            while time.monotonic() < deadline:
                job = client.get(
                    f"/api/asset-registry/workspace-jobs/{job_id}", headers=AUTH
                ).json()["job"]
                if job["status"] in jobs.TERMINAL:
                    break
                orig_sleep(0.02)
            assert job is not None and job["status"] == "succeeded"
            assert job["result"]["indexed"] == total_files
            assert len(sleep_calls) >= 1
    finally:
        if jobs._default:
            jobs._default.shutdown()
        jobs._default = None
