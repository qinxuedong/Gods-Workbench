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

"""FCPXML / CMX3600 EDL 时间线纯函数序列化器。"""

from __future__ import annotations

import math
import xml.etree.ElementTree as ET
from collections.abc import Mapping
from typing import Any

from gw.core.errors import CleanroomException

_AUDIO_KINDS = {"audio", "music", "dialogue", "bgm"}
_VIDEO_KINDS = {"video", "image"}


def _fail(status: int, code: str, message: str) -> None:
    raise CleanroomException(status, code, message)


def _validate_number(value: Any, name: str, min_val: float | None = None) -> float:
    if value is None or isinstance(value, bool):
        _fail(400, "INVALID_TIMELINE_MANIFEST", f"{name} 必须为有效数值")
    try:
        val = float(value)
    except (TypeError, ValueError):
        _fail(400, "INVALID_TIMELINE_MANIFEST", f"{name} 必须为有效数值")
    if not math.isfinite(val):
        _fail(400, "INVALID_TIMELINE_MANIFEST", f"{name} 必须为有限数值")
    if min_val is not None and val < min_val:
        _fail(400, "INVALID_TIMELINE_MANIFEST", f"{name} 不能小于 {min_val}")
    return val


def seconds_to_timecode(
    seconds: float, fps: float = 24.0, drop_frame: bool = False
) -> str:
    """将秒数转换为 SMPTE 时间码字符串 (HH:MM:SS:FF 或 HH:MM:SS;FF)。"""
    sec = _validate_number(seconds, "seconds", min_val=0.0)
    rate = _validate_number(fps, "fps", min_val=0.001)

    if abs(rate - 23.976) < 0.05:
        nominal_fps = 24
    elif abs(rate - 29.97) < 0.05:
        nominal_fps = 30
    elif abs(rate - 59.94) < 0.05:
        nominal_fps = 60
    else:
        nominal_fps = int(round(rate))

    if nominal_fps <= 0:
        _fail(400, "INVALID_FRAME_RATE", "帧率计算结果必须大于零")

    frame_num = int(math.floor(sec * nominal_fps + 1e-6))

    if drop_frame:
        if nominal_fps not in (30, 60):
            _fail(400, "INVALID_FRAME_RATE", "仅 29.97/59.94 或 30/60 fps 支持 Drop-frame 时间码")
        drop_frames = 2 if nominal_fps == 30 else 4
        frames_per_10min = nominal_fps * 600 - drop_frames * 9
        frames_per_min = nominal_fps * 60 - drop_frames

        d = frame_num // frames_per_10min
        m = frame_num % frames_per_10min
        adjusted = frame_num + (drop_frames * 9) * d + drop_frames * (m // frames_per_min)

        ff = adjusted % nominal_fps
        ss = (adjusted // nominal_fps) % 60
        mm = (adjusted // (nominal_fps * 60)) % 60
        hh = adjusted // (nominal_fps * 3600)
        sep = ";"
    else:
        ff = frame_num % nominal_fps
        ss = (frame_num // nominal_fps) % 60
        mm = (frame_num // (nominal_fps * 60)) % 60
        hh = frame_num // (nominal_fps * 3600)
        sep = ":"

    return f"{hh:02d}:{mm:02d}:{ss:02d}{sep}{ff:02d}"


def _validate_and_extract_clips(
    timeline_manifest: Mapping[str, Any],
) -> list[dict[str, Any]]:
    if not isinstance(timeline_manifest, Mapping):
        _fail(400, "INVALID_TIMELINE_MANIFEST", "timeline_manifest 必须是对象")
    tracks = timeline_manifest.get("tracks")
    if not isinstance(tracks, list):
        _fail(400, "INVALID_TIMELINE_MANIFEST", "timeline_manifest.tracks 必须是数组")

    extracted: list[dict[str, Any]] = []
    audio_track_counter = 0

    for track in tracks:
        if not isinstance(track, Mapping):
            _fail(400, "INVALID_TIMELINE_MANIFEST", "时间线轨道结构不合法")
        kind = str(track.get("kind") or "").strip().lower()
        clips = track.get("clips")
        if not isinstance(clips, list):
            _fail(400, "INVALID_TIMELINE_MANIFEST", "时间线 clips 必须是数组")

        if kind in _AUDIO_KINDS:
            audio_track_counter += 1
            track_code = "A" if audio_track_counter == 1 else f"A{audio_track_counter}"
        elif kind in _VIDEO_KINDS:
            track_code = "V"
        else:
            track_code = "V"

        for clip in clips:
            if not isinstance(clip, Mapping):
                _fail(400, "INVALID_TIMELINE_MANIFEST", "时间线片段结构不合法")
            asset_id = str(clip.get("asset_id") or clip.get("clip_id") or "").strip()
            if not asset_id:
                _fail(400, "INVALID_TIMELINE_MANIFEST", "片段缺少 asset_id")

            at = _validate_number(clip.get("at"), "clip.at", min_val=0.0)
            duration = _validate_number(clip.get("duration"), "clip.duration", min_val=0.001)
            in_point = _validate_number(
                clip.get("in_point", clip.get("source_in", 0.0)),
                "clip.in_point",
                min_val=0.0,
            )
            name = str(clip.get("name") or asset_id).strip()

            extracted.append(
                {
                    "asset_id": asset_id,
                    "name": name,
                    "kind": kind,
                    "track_code": track_code,
                    "at": at,
                    "duration": duration,
                    "in_point": in_point,
                }
            )

    return extracted


def export_edl(
    timeline_manifest: Mapping[str, Any],
    title: str = "TIMELINE",
    fps: float = 24.0,
    drop_frame: bool = False,
) -> str:
    """将 OpenFilm timeline_manifest 序列化为 CMX3600 EDL 格式字符串。"""
    clips = _validate_and_extract_clips(timeline_manifest)
    _validate_number(fps, "fps", min_val=0.001)

    fcm_str = "DROP FRAME" if drop_frame else "NON-DROP FRAME"
    lines = [
        f"TITLE: {title}",
        f"FCM: {fcm_str}",
        "",
    ]

    clips_sorted = sorted(clips, key=lambda c: (c["at"], c["track_code"]))

    for idx, c in enumerate(clips_sorted, start=1):
        rec_in = seconds_to_timecode(c["at"], fps, drop_frame)
        rec_out = seconds_to_timecode(c["at"] + c["duration"], fps, drop_frame)
        src_in = seconds_to_timecode(c["in_point"], fps, drop_frame)
        src_out = seconds_to_timecode(c["in_point"] + c["duration"], fps, drop_frame)

        reel = c["asset_id"][:8].upper().ljust(8)
        track_code = c["track_code"].ljust(5)

        lines.append(
            f"{idx:03d}  {reel} {track_code} C        {src_in} {src_out} {rec_in} {rec_out}"
        )
        lines.append(f"* FROM CLIP: {c['name']}")
        lines.append("")

    return "\n".join(lines)


def _fcpxml_time(seconds: float, fps: float) -> str:
    sec = float(seconds)
    if sec == 0.0:
        return "0s"
    if abs(fps - 23.976) < 0.05:
        frames = int(round(sec * (24000 / 1001)))
        return f"{frames * 1001}/24000s"
    if abs(fps - 29.97) < 0.05:
        frames = int(round(sec * (30000 / 1001)))
        return f"{frames * 1001}/30000s"
    if abs(fps - 59.94) < 0.05:
        frames = int(round(sec * (60000 / 1001)))
        return f"{frames * 1001}/60000s"

    int_fps = int(round(fps))
    frames = int(round(sec * int_fps))
    return f"{frames}/{int_fps}s"


def export_fcpxml(
    timeline_manifest: Mapping[str, Any],
    title: str = "TIMELINE",
    fps: float = 24.0,
    drop_frame: bool = False,
) -> str:
    """将 OpenFilm timeline_manifest 序列化为 FCPXML (v1.8) 格式 XML 字符串。"""
    clips = _validate_and_extract_clips(timeline_manifest)
    rate = _validate_number(fps, "fps", min_val=0.001)

    if abs(rate - 23.976) < 0.05:
        frame_duration = "1001/24000s"
        fmt_name = "FFVideoFormat1080p2398"
    elif abs(rate - 29.97) < 0.05:
        frame_duration = "1001/30000s"
        fmt_name = "FFVideoFormat1080p2997"
    elif abs(rate - 59.94) < 0.05:
        frame_duration = "1001/60000s"
        fmt_name = "FFVideoFormat1080p5994"
    else:
        int_fps = int(round(rate))
        frame_duration = f"1/{int_fps}s"
        fmt_name = f"FFVideoFormat1080p{int_fps}"

    total_duration = max((c["at"] + c["duration"] for c in clips), default=0.0)

    root = ET.Element("fcpxml", version="1.8")
    resources = ET.SubElement(root, "resources")
    ET.SubElement(
        resources,
        "format",
        id="r1",
        name=fmt_name,
        frameDuration=frame_duration,
        width="1920",
        height="1080",
    )

    asset_map: dict[str, str] = {}
    res_counter = 2
    for c in clips:
        aid = c["asset_id"]
        if aid not in asset_map:
            rid = f"r{res_counter}"
            res_counter += 1
            asset_map[aid] = rid
            ET.SubElement(
                resources,
                "asset",
                id=rid,
                name=aid,
                src=f"file:///{aid}",
                duration=_fcpxml_time(c["duration"], rate),
                format="r1",
            )

    library = ET.SubElement(root, "library")
    event = ET.SubElement(library, "event", name=title)
    project = ET.SubElement(event, "project", name=title)
    tc_format = "DF" if drop_frame else "NDF"
    sequence = ET.SubElement(
        project,
        "sequence",
        format="r1",
        duration=_fcpxml_time(total_duration, rate),
        tcStart="0s",
        tcFormat=tc_format,
    )
    spine = ET.SubElement(sequence, "spine")

    video_clips = [c for c in clips if c["kind"] in _VIDEO_KINDS or c["track_code"] == "V"]
    audio_clips = [c for c in clips if c["kind"] in _AUDIO_KINDS and c["track_code"] != "V"]

    video_clips_sorted = sorted(video_clips, key=lambda c: c["at"])
    audio_clips_sorted = sorted(audio_clips, key=lambda c: c["at"])

    current_time = 0.0
    for vc in video_clips_sorted:
        if vc["at"] > current_time:
            gap_dur = vc["at"] - current_time
            ET.SubElement(
                spine,
                "gap",
                name="Gap",
                offset=_fcpxml_time(current_time, rate),
                duration=_fcpxml_time(gap_dur, rate),
                start="0s",
            )
            current_time = vc["at"]

        ET.SubElement(
            spine,
            "asset-clip",
            name=vc["name"],
            ref=asset_map[vc["asset_id"]],
            offset=_fcpxml_time(vc["at"], rate),
            duration=_fcpxml_time(vc["duration"], rate),
            start=_fcpxml_time(vc["in_point"], rate),
        )
        current_time = vc["at"] + vc["duration"]

    if not video_clips_sorted and audio_clips_sorted:
        for ac in audio_clips_sorted:
            ET.SubElement(
                spine,
                "asset-clip",
                name=ac["name"],
                ref=asset_map[ac["asset_id"]],
                offset=_fcpxml_time(ac["at"], rate),
                duration=_fcpxml_time(ac["duration"], rate),
                start=_fcpxml_time(ac["in_point"], rate),
            )
    elif audio_clips_sorted:
        for ac in audio_clips_sorted:
            lane = "-1" if ac["track_code"] == "A" else "-2"
            ET.SubElement(
                spine,
                "asset-clip",
                name=ac["name"],
                ref=asset_map[ac["asset_id"]],
                offset=_fcpxml_time(ac["at"], rate),
                duration=_fcpxml_time(ac["duration"], rate),
                start=_fcpxml_time(ac["in_point"], rate),
                lane=lane,
            )

    ET.indent(root, space="  ")
    xml_body = ET.tostring(root, encoding="utf-8").decode("utf-8")
    return f'<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE fcpxml>\n{xml_body}\n'
