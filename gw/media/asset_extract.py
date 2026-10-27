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

"""镜头级纯提取：切点、内存关键帧与技术元数据，全程不落盘、不入库。"""

from __future__ import annotations

import json
import math
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from gw.core.errors import CleanroomException
from gw.media.keyframe_extractor import decode_keyframe_to_memory
from gw.media.scene_detector import detect_scenes

_PROCESS_TIMEOUT_SECONDS = 120


def _fail(status: int, code: str, message: str) -> None:
    raise CleanroomException(status, code, message)


@dataclass(frozen=True)
class ShotExtraction:
    """单个镜头：起止区间与镜头起点解码出的内存关键帧（PNG bytes）。"""

    start_sec: float
    end_sec: float
    keyframe: bytes


@dataclass(frozen=True)
class VideoExtraction:
    """整段视频的纯提取结果；不写索引、资产库或任何落盘路径。

    ``cut_points_sec`` 为镜头切点秒列表（单调递增，首个恒为 0）；
    ``metadata`` 携带 ffprobe 原值：codec、fps、width、height、duration、has_audio。
    """

    cut_points_sec: tuple[float, ...]
    shots: tuple[ShotExtraction, ...]
    metadata: dict[str, Any]


def _parse_frame_rate(raw: Any) -> float:
    if isinstance(raw, str) and raw:
        numerator, _, denominator = raw.partition("/")
        try:
            rate = float(numerator) / float(denominator or 1)
        except (TypeError, ValueError, ZeroDivisionError):
            rate = float("nan")
        if math.isfinite(rate) and rate > 0:
            return rate
    _fail(503, "MEDIA_PROBE_FAILED", "视频帧率缺失或无效")


def _probe_metadata(path: Path) -> dict[str, Any]:
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        _fail(503, "FFMPEG_NOT_AVAILABLE", "本地媒体处理依赖不可用")
    try:
        result = subprocess.run(
            [
                ffprobe,
                "-v",
                "error",
                "-show_entries",
                "stream=codec_name,codec_type,width,height,r_frame_rate",
                "-show_entries",
                "format=duration",
                "-of",
                "json",
                str(path),
            ],
            capture_output=True,
            text=True,
            timeout=_PROCESS_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        _fail(503, "MEDIA_PROCESS_FAILED", "本地媒体处理进程失败")
    if result.returncode != 0:
        _fail(503, "MEDIA_PROBE_FAILED", "无法读取视频技术元数据")
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError:
        _fail(503, "MEDIA_PROBE_FAILED", "无法读取视频技术元数据")
    streams = payload.get("streams")
    if not isinstance(streams, list):
        _fail(503, "MEDIA_PROBE_FAILED", "无法读取视频技术元数据")
    video_streams = [
        stream
        for stream in streams
        if isinstance(stream, dict) and stream.get("codec_type") == "video"
    ]
    if not video_streams:
        _fail(503, "MEDIA_PROBE_FAILED", "视频流不存在或无法读取")
    primary = video_streams[0]
    codec = primary.get("codec_name")
    if not isinstance(codec, str) or not codec:
        _fail(503, "MEDIA_PROBE_FAILED", "视频编码缺失或无效")
    width = primary.get("width")
    height = primary.get("height")
    if (
        not isinstance(width, int)
        or width <= 0
        or not isinstance(height, int)
        or height <= 0
    ):
        _fail(503, "MEDIA_PROBE_FAILED", "视频分辨率缺失或无效")
    fmt = payload.get("format")
    duration_raw = fmt.get("duration") if isinstance(fmt, dict) else None
    try:
        duration = float(duration_raw)
    except (TypeError, ValueError):
        _fail(503, "MEDIA_PROBE_FAILED", "无法读取视频时长")
    if not math.isfinite(duration) or duration <= 0:
        _fail(503, "MEDIA_PROBE_FAILED", "无法读取视频时长")
    return {
        "codec": codec,
        "fps": _parse_frame_rate(primary.get("r_frame_rate")),
        "width": width,
        "height": height,
        "duration": duration,
        "has_audio": any(
            isinstance(stream, dict) and stream.get("codec_type") == "audio"
            for stream in streams
        ),
    }


def extract(video_path: str | Path) -> VideoExtraction:
    """对一段视频做镜头级纯提取，返回切点、每镜头内存关键帧与技术元数据。

    复用 ``detect_scenes`` 获得物理镜头区间，切点即各区间起点；关键帧经
    ``ffmpeg -f image2pipe`` 解码进内存；技术元数据取 ffprobe 原值。整个
    过程不写索引、资产库或任何落盘路径；工具缺失、文件损坏或解码失败时
    按领域异常失败关闭。
    """
    scenes = detect_scenes(video_path)
    path = Path(video_path)
    metadata = _probe_metadata(path)
    shots = []
    for scene in scenes:
        frame = decode_keyframe_to_memory(path, scene["start_sec"])
        shots.append(
            ShotExtraction(
                start_sec=float(scene["start_sec"]),
                end_sec=float(scene["end_sec"]),
                keyframe=frame,
            )
        )
    return VideoExtraction(
        cut_points_sec=tuple(float(scene["start_sec"]) for scene in scenes),
        shots=tuple(shots),
        metadata=metadata,
    )


__all__ = ["ShotExtraction", "VideoExtraction", "extract"]
