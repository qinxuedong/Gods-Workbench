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

"""按 OpenFilm timeline_manifest 纯函数构建并执行本地 FFmpeg 母带导出。"""

from __future__ import annotations

import hashlib
import json
import math
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any, Mapping, Sequence

from gw.core.errors import CleanroomException


WIDTH = 1920
HEIGHT = 1080
FRAME_RATE = 24
_AUDIO_KINDS = {"audio", "music", "dialogue", "bgm"}


def _fail(status: int, code: str, message: str) -> None:
    raise CleanroomException(status, code, message)


def _number(value: Any, *, field: str, default: float | None = None) -> float:
    if value is None and default is not None:
        return default
    if isinstance(value, bool):
        _fail(400, "INVALID_TIMELINE_MANIFEST", f"{field} 必须为有限数值")
    try:
        number = float(value)
    except (TypeError, ValueError):
        _fail(400, "INVALID_TIMELINE_MANIFEST", f"{field} 必须为有限数值")
    if not math.isfinite(number):
        _fail(400, "INVALID_TIMELINE_MANIFEST", f"{field} 必须为有限数值")
    return number


def _frames(seconds: float) -> int:
    return int(math.floor(seconds * FRAME_RATE + 0.5))


def _time(frames: int) -> str:
    return f"{frames / FRAME_RATE:.8f}".rstrip("0").rstrip(".")


def _tracks(manifest: Mapping[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if not isinstance(manifest, Mapping):
        _fail(400, "INVALID_TIMELINE_MANIFEST", "timeline_manifest 必须是对象")
    tracks = manifest.get("tracks")
    if not isinstance(tracks, list):
        _fail(400, "INVALID_TIMELINE_MANIFEST", "timeline_manifest.tracks 必须是数组")
    video: list[dict[str, Any]] = []
    audio: list[dict[str, Any]] = []
    for track in tracks:
        if not isinstance(track, Mapping):
            _fail(400, "INVALID_TIMELINE_MANIFEST", "时间线轨道结构不合法")
        kind = str(track.get("kind") or "").strip().lower()
        clips = track.get("clips", [])
        if not isinstance(clips, list):
            _fail(400, "INVALID_TIMELINE_MANIFEST", "时间线 clips 必须是数组")
        for raw in clips:
            if not isinstance(raw, Mapping):
                _fail(400, "INVALID_TIMELINE_MANIFEST", "时间线片段结构不合法")
            if kind in {"video", "image"}:
                clip = dict(raw)
                clip["_track_kind"] = kind
                video.append(clip)
            elif kind in _AUDIO_KINDS:
                clip = dict(raw)
                clip["_track_kind"] = kind
                audio.append(clip)
            elif kind in {"subtitle", "subtitles", "caption"}:
                if clips:
                    _fail(400, "UNSUPPORTED_SUBTITLE_TRACK", "母带导出暂不支持独立字幕轨")
    if not video:
        _fail(400, "EMPTY_VIDEO_TIMELINE", "时间线没有可导出的视频片段")
    return video, audio


def _clip(clip: Mapping[str, Any], asset_paths: Mapping[str, Path], *, track: str) -> dict[str, Any]:
    asset_id = str(clip.get("asset_id") or "").strip()
    path = asset_paths.get(asset_id)
    if not asset_id or path is None:
        _fail(404, "MEDIA_NOT_AVAILABLE", f"{track}片段素材未登记或不可用")
    at = _number(clip.get("at"), field="clip.at", default=0.0)
    duration = _number(clip.get("duration"), field="clip.duration")
    if at < 0 or duration <= 0:
        _fail(400, "INVALID_TIMELINE_MANIFEST", "片段 at 必须非负且 duration 必须大于零")
    overrides = clip.get("overrides") or {}
    if not isinstance(overrides, Mapping):
        _fail(400, "INVALID_TIMELINE_MANIFEST", "片段 overrides 必须是对象")
    if overrides.get("transition"):
        _fail(400, "UNSUPPORTED_TIMELINE_TRANSITION", "该转场类型尚未进入母带导出实现")
    if overrides.get("lut"):
        _fail(400, "UNSUPPORTED_TIMELINE_LUT", "时间线 LUT 需先转换为已登记的 LUT 资产")
    source_in = _number(
        clip.get("in_point", clip.get("source_in", overrides.get("in_point"))),
        field="clip.in_point",
        default=0.0,
    )
    if source_in < 0:
        _fail(400, "INVALID_TIMELINE_MANIFEST", "片段入点必须非负")
    at_frames = _frames(at)
    duration_frames = _frames(duration)
    if duration_frames < 1:
        _fail(400, "INVALID_TIMELINE_MANIFEST", "片段时长不足一帧")
    return {
        "asset_id": asset_id,
        "path": Path(path),
        "kind": str(clip.get("media_type") or clip.get("_track_kind") or track).lower(),
        "at_frames": at_frames,
        "duration_frames": duration_frames,
        "source_in": source_in,
        "overrides": dict(overrides),
    }


def _crop_filter(overrides: Mapping[str, Any]) -> str:
    crop = overrides.get("crop")
    if not crop:
        return ""
    if not isinstance(crop, Mapping):
        _fail(400, "INVALID_TIMELINE_MANIFEST", "crop 必须为对象")
    x = _number(crop.get("x"), field="crop.x", default=0.0)
    y = _number(crop.get("y"), field="crop.y", default=0.0)
    width = _number(crop.get("width"), field="crop.width", default=1.0)
    height = _number(crop.get("height"), field="crop.height", default=1.0)
    if x < 0 or y < 0 or width <= 0 or height <= 0 or x + width > 1 or y + height > 1:
        _fail(400, "INVALID_TIMELINE_MANIFEST", "crop 坐标必须位于 0 到 1 范围内")
    return f"crop=iw*{width:g}:ih*{height:g}:iw*{x:g}:ih*{y:g}"


def _validate_video_order(clips: Sequence[dict[str, Any]]) -> None:
    ordered = sorted(clips, key=lambda item: (item["at_frames"], item["asset_id"]))
    end = 0
    for item in ordered:
        if item["at_frames"] < end:
            _fail(400, "UNSUPPORTED_TIMELINE_OVERLAP", "视频片段存在重叠，无法按单轨顺序压制")
        end = item["at_frames"] + item["duration_frames"]


def build_ffmpeg_command(
    manifest: Mapping[str, Any],
    asset_paths: Mapping[str, Path],
    output_path: Path,
    *,
    ffmpeg_path: str = "ffmpeg",
    encoder: str = "libx264",
) -> list[str]:
    """纯函数：把无重叠时间线转换为固定 1080p/24fps 的 FFmpeg 命令。"""
    raw_video, raw_audio = _tracks(manifest)
    video = [_clip(item, asset_paths, track="视频") for item in raw_video]
    audio = [_clip(item, asset_paths, track="音频") for item in raw_audio]
    video.sort(key=lambda item: (item["at_frames"], item["asset_id"]))
    audio.sort(key=lambda item: (item["at_frames"], item["asset_id"]))
    _validate_video_order(video)

    inputs: list[str] = []
    for item in [*video, *audio]:
        if item["kind"] in {"image", "still", "photo"}:
            inputs.extend(["-loop", "1"])
        inputs.extend(["-i", os.fspath(item["path"])])

    filters: list[str] = []
    video_labels: list[str] = []
    cursor = 0
    segment_index = 0
    input_index = 0
    for item in video:
        start = item["at_frames"]
        if start > cursor:
            gap_label = f"gap{segment_index}"
            gap_seconds = _time(start - cursor)
            filters.append(
                f"color=c=black:s={WIDTH}x{HEIGHT}:r={FRAME_RATE}:d={gap_seconds},"
                f"format=yuv420p[{gap_label}]"
            )
            video_labels.append(f"[{gap_label}]")
            segment_index += 1
        duration = _time(item["duration_frames"])
        trim = f"trim=start={item['source_in']:.8f}:duration={duration},setpts=PTS-STARTPTS"
        crop_filter = _crop_filter(item["overrides"])
        chain = [trim]
        if crop_filter:
            chain.append(crop_filter)
        chain.extend([
            f"fps={FRAME_RATE}",
            f"scale={WIDTH}:{HEIGHT}:force_original_aspect_ratio=decrease",
            f"pad={WIDTH}:{HEIGHT}:(ow-iw)/2:(oh-ih)/2:color=black",
            "setsar=1",
            "format=yuv420p",
        ])
        label = f"v{segment_index}"
        filters.append(f"[{input_index}:v]{','.join(chain)}[{label}]")
        video_labels.append(f"[{label}]")
        cursor = start + item["duration_frames"]
        segment_index += 1
        input_index += 1

    video_duration = cursor
    concat_input = "".join(video_labels)
    if len(video_labels) == 1:
        filters.append(f"{concat_input}null[vout]")
    else:
        filters.append(f"{concat_input}concat=n={len(video_labels)}:v=1:a=0[vout]")

    audio_labels: list[str] = []
    for audio_index, item in enumerate(audio):
        start_ms = int(math.floor(item["at_frames"] * 1000 / FRAME_RATE + 0.5))
        duration = _time(item["duration_frames"])
        source_in = f"atrim=start={item['source_in']:.8f}:duration={duration}"
        delay = f"adelay={start_ms}|{start_ms}"
        label = f"a{audio_index}"
        filters.append(
            f"[{input_index}:a]{source_in},asetpts=PTS-STARTPTS,aresample=48000,"
            f"aformat=channel_layouts=stereo,{delay}[{label}]"
        )
        audio_labels.append(f"[{label}]")
        input_index += 1
    if audio_labels:
        audio_input = "".join(audio_labels)
        if len(audio_labels) == 1:
            filters.append(
                f"{audio_input}apad,atrim=duration={_time(video_duration)}[aout]"
            )
        else:
            filters.append(
                f"{audio_input}amix=inputs={len(audio_labels)}:duration=longest:"
                f"dropout_transition=0,apad,atrim=duration={_time(video_duration)}[aout]"
            )

    command = [ffmpeg_path, "-hide_banner", "-loglevel", "error", "-y", *inputs,
               "-filter_complex", ";".join(filters), "-map", "[vout]"]
    if audio_labels:
        command.extend(["-map", "[aout]", "-c:a", "aac", "-b:a", "192k"])
    command.extend(["-c:v", encoder])
    if encoder == "libx264":
        command.extend(["-preset", "medium", "-crf", "18", "-profile:v", "high"])
    else:
        command.extend(["-b:v", "20M", "-maxrate", "30M", "-bufsize", "60M"])
        if encoder == "h264_nvenc":
            command.extend(["-preset", "p5"])
        elif encoder == "h264_qsv":
            command.extend(["-preset", "medium"])
        elif encoder == "h264_amf":
            command.extend(["-quality", "quality"])
    command.extend(["-pix_fmt", "yuv420p", "-movflags", "+faststart", os.fspath(output_path)])
    return command


def _run(command: list[str], *, timeout: int) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(command, capture_output=True, timeout=timeout, check=False)


def _hardware_encoder(ffmpeg_path: str) -> str | None:
    try:
        result = _run([ffmpeg_path, "-hide_banner", "-encoders"], timeout=15)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    output = (result.stdout + result.stderr).decode("utf-8", errors="replace")
    for encoder in ("h264_nvenc", "h264_videotoolbox", "h264_qsv", "h264_amf"):
        if encoder in output:
            return encoder
    return None


def _probe_output(path: Path, ffprobe_path: str) -> dict[str, Any]:
    try:
        result = _run(
            [ffprobe_path, "-v", "error", "-count_frames", "-show_entries",
             "format=duration:stream=codec_type,codec_name,width,height,duration,nb_read_frames",
             "-of", "json", os.fspath(path)],
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired):
        _fail(503, "FFPROBE_UNAVAILABLE", "无法完成导出文件元数据核验")
    if result.returncode != 0:
        _fail(500, "MASTER_EXPORT_INVALID", "FFprobe 无法读取导出文件")
    try:
        metadata = json.loads(result.stdout.decode("utf-8"))
        streams = metadata.get("streams", [])
        video = next(item for item in streams if item.get("codec_type") == "video")
        audio = next((item for item in streams if item.get("codec_type") == "audio"), None)
        duration = float(video.get("duration") or metadata.get("format", {}).get("duration"))
        width = int(video["width"])
        height = int(video["height"])
        video_codec = str(video["codec_name"])
        frame_count = int(video.get("nb_read_frames") or 0)
        audio_codec = str(audio["codec_name"]) if audio else None
    except (ValueError, TypeError, KeyError, StopIteration, AttributeError):
        _fail(500, "MASTER_EXPORT_INVALID", "导出文件缺少有效的媒体流元数据")
    if duration <= 0 or frame_count < 1 or (width, height) != (WIDTH, HEIGHT) or video_codec != "h264":
        _fail(500, "MASTER_EXPORT_INVALID", "导出文件未达到 1080p H.264 母带基线")
    if audio:
        try:
            audio_duration = float(audio.get("duration") or metadata.get("format", {}).get("duration"))
        except (ValueError, TypeError):
            _fail(500, "MASTER_EXPORT_INVALID", "导出音轨缺少有效时长")
        if abs(duration - audio_duration) > 0.1:
            _fail(500, "MASTER_EXPORT_AV_SYNC_FAILED", "导出视频与音轨时长差超过 100 毫秒")
    return {
        "duration_seconds": duration,
        "width": width,
        "height": height,
        "video_codec": video_codec,
        "frame_count": frame_count,
        "audio_codec": audio_codec,
    }


def render_master_mp4(
    manifest: Mapping[str, Any],
    asset_paths: Mapping[str, Path],
    output_path: Path,
    *,
    ffmpeg_path: str | None = None,
    ffprobe_path: str | None = None,
    prefer_hardware: bool = True,
    timeout: int = 900,
) -> dict[str, Any]:
    """运行本地 FFmpeg，硬件编码失败时回退 CPU，并原子发布已核验 MP4。"""
    ffmpeg = ffmpeg_path or shutil.which("ffmpeg")
    ffprobe = ffprobe_path or shutil.which("ffprobe")
    if not ffmpeg or not ffprobe:
        _fail(503, "FFMPEG_UNAVAILABLE", "本机未安装 FFmpeg/FFprobe")
    raw_video, _raw_audio = _tracks(manifest)
    expected_clips = [_clip(item, asset_paths, track="视频") for item in raw_video]
    expected_clips.sort(key=lambda item: (item["at_frames"], item["asset_id"]))
    _validate_video_order(expected_clips)
    expected_frame_count = expected_clips[-1]["at_frames"] + expected_clips[-1]["duration_frames"]

    target = Path(output_path)
    partial = target.with_name(f"{target.stem}.part{target.suffix}")
    target.parent.mkdir(parents=True, exist_ok=True)
    encoders: list[str] = []
    if prefer_hardware:
        hardware = _hardware_encoder(ffmpeg)
        if hardware:
            encoders.append(hardware)
    encoders.append("libx264")
    last_error: CleanroomException | None = None
    for encoder in encoders:
        try:
            command = build_ffmpeg_command(
                manifest, asset_paths, partial,
                ffmpeg_path=ffmpeg, encoder=encoder,
            )
            try:
                result = _run(command, timeout=timeout)
            except subprocess.TimeoutExpired:
                _fail(504, "MASTER_EXPORT_TIMEOUT", "FFmpeg 母带压制超时")
            except OSError:
                _fail(503, "FFMPEG_UNAVAILABLE", "无法启动本机 FFmpeg")
            if result.returncode != 0 or not partial.is_file() or partial.stat().st_size == 0:
                _fail(500, "MASTER_EXPORT_FAILED", "FFmpeg 未能生成有效母带")
            metadata = _probe_output(partial, ffprobe)
            if metadata["frame_count"] != expected_frame_count:
                _fail(
                    500,
                    "MASTER_EXPORT_FRAME_COUNT_MISMATCH",
                    f"预期 {expected_frame_count} 帧，实际 {metadata['frame_count']} 帧",
                )
            os.replace(partial, target)
            content_hash = hashlib.sha256(target.read_bytes()).hexdigest()
            return {
                **metadata,
                "encoder": encoder,
                "hardware_accelerated": encoder != "libx264",
                "bytes": target.stat().st_size,
                "sha256": content_hash,
            }
        except CleanroomException as error:
            partial.unlink(missing_ok=True)
            if encoder == "libx264":
                raise
            last_error = error
    if last_error:
        raise last_error
    _fail(500, "MASTER_EXPORT_FAILED", "FFmpeg 未能生成有效母带")
