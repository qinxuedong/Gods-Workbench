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

"""通过 FFmpeg 场景分数识别视频中的物理镜头边界。"""

from __future__ import annotations

import json
import math
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

from gw.core.errors import CleanroomException

_VIDEO_SUFFIXES = frozenset(
    {".mp4", ".mov", ".mkv", ".avi", ".webm", ".m4v", ".flv", ".wmv"}
)
_SCENE_TIME = re.compile(r"\bpts_time:([-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)")
_PROCESS_TIMEOUT_SECONDS = 120


def _fail(status: int, code: str, message: str) -> None:
    raise CleanroomException(status, code, message)


def _video_path(video_path: str | Path) -> Path:
    try:
        path = Path(video_path)
    except (TypeError, ValueError):
        _fail(400, "INVALID_VIDEO_PATH", "视频路径无效")
    if path.suffix.lower() not in _VIDEO_SUFFIXES or not path.is_file():
        _fail(400, "VIDEO_NOT_AVAILABLE", "视频文件不存在或格式不受支持")
    return path


def _tool(name: str) -> str:
    path = shutil.which(name)
    if not path:
        _fail(503, "FFMPEG_NOT_AVAILABLE", "本地媒体处理依赖不可用")
    return path


def _run(args: list[str]) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=_PROCESS_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        _fail(503, "MEDIA_PROCESS_FAILED", "本地媒体处理进程失败")


def _video_metadata(path: Path) -> dict[str, Any]:
    result = _run(
        [
            _tool("ffprobe"),
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=codec_name,width,height:format=duration,bit_rate",
            "-of",
            "json",
            str(path),
        ]
    )
    try:
        payload = json.loads(result.stdout)
        stream = payload["streams"][0]
        duration = float(payload["format"]["duration"])
        width = int(stream["width"])
        height = int(stream["height"])
        codec = str(stream["codec_name"]).strip()
        raw_bit_rate = payload.get("format", {}).get("bit_rate")
        try:
            bit_rate = int(raw_bit_rate) if raw_bit_rate is not None else None
        except (TypeError, ValueError, OverflowError):
            bit_rate = None
    except (IndexError, KeyError, TypeError, ValueError, OverflowError):
        _fail(503, "MEDIA_PROBE_FAILED", "无法读取视频技术元数据")
    if (
        result.returncode != 0
        or not math.isfinite(duration)
        or duration <= 0
        or width <= 0
        or height <= 0
        or not codec
    ):
        _fail(503, "MEDIA_PROBE_FAILED", "无法读取视频技术元数据")
    return {
        "duration_sec": duration,
        "width": width,
        "height": height,
        "codec": codec,
        "bit_rate": bit_rate,
        "container": path.suffix.lower().lstrip("."),
        "source_url": None,
    }


def detect_scenes(
    video_path: str | Path, threshold: float = 0.3
) -> list[dict[str, Any]]:
    """检测硬切边界并返回连续的镜头元数据区间。

    FFmpeg 的 ``select=gt(scene,threshold)`` 产生真实切点；时间统一量化到
    毫秒。``tags`` 保持空列表，不根据像素内容伪造语义标签，缩略图由提取器补齐。
    """
    if isinstance(threshold, bool):
        _fail(
            400, "INVALID_SCENE_THRESHOLD", "scene threshold 必须是 [0, 1) 内的有限数值"
        )
    try:
        score = float(threshold)
    except (TypeError, ValueError, OverflowError):
        _fail(
            400, "INVALID_SCENE_THRESHOLD", "scene threshold 必须是 [0, 1) 内的有限数值"
        )
    if not math.isfinite(score) or not 0 <= score < 1:
        _fail(
            400, "INVALID_SCENE_THRESHOLD", "scene threshold 必须是 [0, 1) 内的有限数值"
        )

    path = _video_path(video_path)
    metadata = _video_metadata(path)
    duration = metadata["duration_sec"]
    threshold_text = f"{score:.6f}".rstrip("0").rstrip(".")
    scene_filter = f"select='gt(scene,{threshold_text})',showinfo"
    result = _run(
        [
            _tool("ffmpeg"),
            "-hide_banner",
            "-i",
            str(path),
            "-map",
            "0:v:0",
            "-vf",
            scene_filter,
            "-an",
            "-f",
            "null",
            "-",
        ]
    )
    if result.returncode != 0:
        _fail(503, "SCENE_DETECTION_FAILED", "FFmpeg 无法检测视频镜头")

    duration_ms = int(round(duration * 1000))
    cut_times_ms: set[int] = set()
    for match in _SCENE_TIME.finditer(result.stderr or ""):
        try:
            at = float(match.group(1))
        except ValueError:
            continue
        if math.isfinite(at):
            at_ms = int(round(at * 1000))
            if 0 < at_ms < duration_ms:
                cut_times_ms.add(at_ms)

    boundaries_ms = [0, *sorted(cut_times_ms), duration_ms]
    return [
        {
            "start_sec": round(start_ms / 1000, 3),
            "end_sec": round(end_ms / 1000, 3),
            "duration_sec": round((end_ms - start_ms) / 1000, 3),
            "width": metadata["width"],
            "height": metadata["height"],
            "codec": metadata["codec"],
            "bit_rate": metadata["bit_rate"],
            "container": metadata["container"],
            "source_url": metadata["source_url"],
            "tags": [],
            "thumbnail": None,
        }
        for start_ms, end_ms in zip(boundaries_ms, boundaries_ms[1:], strict=False)
        if end_ms > start_ms
    ]


__all__ = ["detect_scenes"]
