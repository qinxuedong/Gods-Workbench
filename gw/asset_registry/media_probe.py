# -*- coding: utf-8 -*-
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

"""Phase 12 视频帧/分镜/剪辑的真实媒体探测。

真实数据源：允许根目录内的真实视频文件 + ffmpeg/ffprobe 子进程。
失败关闭：未配置允许根目录、素材无本地视频、ffmpeg 缺失或执行失败一律 503，
**禁止**返回伪造帧或伪造剪辑结果。
"""

from __future__ import annotations

import shutil
import re
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional

from gw.asset_registry import repository as repo
from gw.core import storage
from gw.core.errors import CleanroomException

VIDEO_SUFFIXES = frozenset(".mp4 .mov .mkv .avi .webm .m4v .flv .wmv".split())
FFPROBE_TIMEOUT = 20
FFMPEG_TIMEOUT = 120


def _ffmpeg(name: str) -> str:
    """定位 ffmpeg/ffprobe；缺失即失败关闭（不静默降级）。"""
    path = shutil.which(name)
    if not path:
        repo._unavailable("/api/asset-registry", "FFMPEG_NOT_AVAILABLE",
                          "未找到 %s，媒体处理能力不可用" % name)
    return path


def _local_video(asset_id: str) -> Path:
    """取素材登记的本地视频路径；无本地视频即失败关闭。"""
    item = repo._require(repo.state().read(), asset_id)
    display = item.get("display_path")
    if not display:
        repo._unavailable("/api/asset-registry", "MEDIA_NOT_AVAILABLE", "该素材未登记本地视频路径")
    path = storage.resolve_within_roots(str(display))
    if not path.is_file() or path.suffix.lower() not in VIDEO_SUFFIXES:
        repo._unavailable("/api/asset-registry", "MEDIA_NOT_AVAILABLE", "该素材没有可读取的本地视频")
    return path


def _run(args: List[str]) -> subprocess.CompletedProcess:
    """执行媒体子进程；失败一律转 503，不泄漏原始命令输出到响应体。"""
    try:
        return subprocess.run(args, capture_output=True, timeout=FFMPEG_TIMEOUT, check=False)
    except (OSError, subprocess.TimeoutExpired):
        repo._unavailable("/api/asset-registry", "MEDIA_PROCESS_FAILED", "媒体处理进程执行失败")


def _output_dir(asset_id: str) -> Path:
    relative = "media_output/" + asset_id
    out = storage.resolve_output_directory(relative, must_exist=False)
    out.mkdir(parents=True, exist_ok=True)
    return storage.resolve_output_directory(relative)


def probe(video: Path) -> Dict[str, Any]:
    """用 ffprobe 读真实媒体元数据；解析失败即失败关闭。"""
    ffprobe = _ffmpeg("ffprobe")
    completed = _run([ffprobe, "-v", "error", "-select_streams", "v:0",
                      "-show_entries", "stream=width,height,duration",
                      "-of", "default=noprint_wrappers=1", str(video)])
    if completed.returncode != 0:
        repo._unavailable("/api/asset-registry", "MEDIA_PROBE_FAILED", "无法解析该视频文件")
    width = height = None
    duration = None
    for line in completed.stdout.decode("utf-8", "replace").splitlines():
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        if key == "width" and value.isdigit():
            width = int(value)
        elif key == "height" and value.isdigit():
            height = int(value)
        elif key == "duration":
            try:
                duration = float(value)
            except ValueError:
                duration = None
    return {"width": width, "height": height, "duration_seconds": duration}


def storyboard_directory(asset_id: str) -> Path:
    """专用分镜根按精确稳定ID分区，禁止路径别名及目录链接。"""
    if not asset_id or len(asset_id) > 128 or not all(ch.isalnum() or ch in "_-" for ch in asset_id):
        raise CleanroomException(400, "INVALID_ASSET_ID", "素材标识不是安全单段")
    return storage.resolve_output_directory("media_output/storyboards/" + asset_id, must_exist=False)


def is_storyboard_cache_name(name: str) -> bool:
    """与本模块抽帧生成命名共享精确规则，不删除任意扩展名或前缀。"""
    return re.fullmatch(r"frame_[0-9]+_[0-9]{3}\.png", name) is not None


def extract_frame(asset_id: str, at: float) -> Dict[str, Any]:
    return _extract_frame(asset_id, at, storyboard_cache=False)


def _extract_frame(asset_id: str, at: float, *, storyboard_cache: bool) -> Dict[str, Any]:
    """在指定时间点真实抽帧并落盘，返回真实输出文件信息。"""
    video = _local_video(asset_id)
    ffmpeg = _ffmpeg("ffmpeg")
    directory = storyboard_directory(asset_id) if storyboard_cache else _output_dir(asset_id)
    directory.mkdir(parents=True, exist_ok=True)
    out = directory / ("frame_%.3f.png" % float(at)).replace(".", "_", 1)
    if storyboard_cache:
        if not is_storyboard_cache_name(out.name):
            raise CleanroomException(400, "INVALID_TIME_RANGE", "分镜时间不是有效非负数")
        out = storage.resolve_output_path(out.relative_to(storage.data_root().resolve()).as_posix(), must_exist=False)
    completed = _run([ffmpeg, "-y", "-ss", "%.3f" % float(at), "-i", str(video),
                      "-frames:v", "1", str(out)])
    if completed.returncode != 0 or not out.is_file():
        repo._unavailable("/api/asset-registry", "MEDIA_PROCESS_FAILED", "抽帧失败")
    payload = {"asset_id": asset_id, "at_seconds": float(at),
               "output_name": out.relative_to(storage.data_root().resolve()).as_posix(),
               "size_bytes": out.stat().st_size, "data_status": "ok", "data_gaps": []}
    payload.update(probe(video))
    return payload


def storyboard(asset_id: str, version: Optional[str]) -> Dict[str, Any]:
    """按等分时间点真实抽帧生成分镜；帧数依据真实时长计算。"""
    video = _local_video(asset_id)
    meta = probe(video)
    duration = meta.get("duration_seconds")
    if not duration or duration <= 0:
        repo._unavailable("/api/asset-registry", "MEDIA_PROBE_FAILED", "无法确定视频时长，无法生成分镜")
    count = max(1, min(12, int(duration // 2) or 1))
    step = duration / (count + 1)
    frames = []
    for index in range(1, count + 1):
        frame = _extract_frame(asset_id, step * index, storyboard_cache=True)
        frames.append({"index": index, "at_seconds": round(step * index, 3),
                       "output_name": frame["output_name"], "size_bytes": frame["size_bytes"]})
    return {"asset_id": asset_id, "version": version, "duration_seconds": duration,
            "width": meta.get("width"), "height": meta.get("height"),
            "frames": frames, "count": len(frames), "data_status": "ok", "data_gaps": []}


def clip(asset_id: str, start: float, end: float, expected_version: Optional[int]) -> Dict[str, Any]:
    """真实剪辑视频片段并落盘；起止时间非法一律 400。"""
    if float(end) <= float(start) or float(start) < 0:
        raise CleanroomException(400, "INVALID_TIME_RANGE", "结束时间必须大于开始时间且非负")
    video = _local_video(asset_id)
    ffmpeg = _ffmpeg("ffmpeg")
    out = _output_dir(asset_id) / ("clip_%s_%s.mp4" % ("%.3f" % float(start), "%.3f" % float(end))).replace(".", "_", 1)
    completed = _run([ffmpeg, "-y", "-ss", "%.3f" % float(start), "-to", "%.3f" % float(end),
                      "-i", str(video), "-c", "copy", str(out)])
    if completed.returncode != 0 or not out.is_file():
        repo._unavailable("/api/asset-registry", "MEDIA_PROCESS_FAILED", "视频剪辑失败")

    def mutate(raw: Dict[str, Any]) -> Any:
        repo._cas(raw, expected_version)
        clip_id = repo._seq(raw, "clip_")
        record = {"clip_id": clip_id, "asset_id": asset_id, "start_seconds": float(start),
                  "end_seconds": float(end), "output_name": out.relative_to(storage.data_root().resolve()).as_posix(),
                  "size_bytes": out.stat().st_size, "created_at": storage.now_iso()}
        raw.setdefault("clips", {})[clip_id] = record
        revision = repo._bump(raw)
        return {"clip": record, "revision": revision, "data_status": "ok", "data_gaps": []}
    return repo.state().mutate(mutate)
