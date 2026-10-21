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

"""C13-T1 对白定长与时序自动锁定。

这个模块只处理已经由 TTS/ASR 适配器返回的结构化结果，不在客户端保存凭据，
也不在模块内联网或调用外部进程。它把音频时长和词级时间戳规范化为可审计的
Auto-Timing 记录，再以 ``expected_version`` CAS 生成新的 ``timeline_manifest``
快照，供上层仓储原子落盘。
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, MutableMapping, Sequence
from copy import deepcopy
from dataclasses import dataclass
from math import isfinite
from numbers import Real
from typing import Any

from gw.core.errors import CleanroomException, VersionConflictException

DEFAULT_PAD_SECONDS = 0.2
_ROUND_DIGITS = 6
_EPSILON = 1e-6


def _fail(
    code: str,
    message: str,
    *,
    status_code: int = 400,
    extra: Mapping[str, Any] | None = None,
) -> None:
    """以项目统一错误包失败关闭。"""
    raise CleanroomException(status_code, code, message, extra=dict(extra or {}))


def _number(value: Any, field: str) -> float:
    """解析有限实数；bool 和 NaN/Infinity 均拒绝。"""
    if isinstance(value, bool) or not isinstance(value, Real):
        _fail("INVALID_AUTO_TIMING", f"{field} 必须是有限数字")
    result = float(value)
    if not isfinite(result):
        _fail("INVALID_AUTO_TIMING", f"{field} 必须是有限数字")
    return result


def _stable(value: float) -> float:
    """把 JSON 浮点值固定到足够精确且可复现的小数位。"""
    return round(float(value), _ROUND_DIGITS)


def _positive_duration(value: Any, *, code: str, field: str) -> float:
    result = _number(value, field)
    if result <= 0:
        _fail(code, f"{field} 必须大于 0")
    return _stable(result)


def _pad(value: Any) -> float:
    result = _number(DEFAULT_PAD_SECONDS if value is None else value, "pad")
    if result < 0:
        _fail("INVALID_PAD", "pad 不能为负数")
    return _stable(result)


def _seconds(value: Any, *, field: str, milliseconds: bool = False) -> float:
    result = _number(value, field)
    if milliseconds:
        result /= 1000.0
    if result < 0:
        _fail("INVALID_AUDIO_TIMESTAMPS", f"{field} 不能为负数")
    return _stable(result)


def extract_audio_duration(source: Any) -> float:
    """从 TTS/媒体适配器结果读取音频秒数。

    支持直接传入秒数，或传入带 ``duration``/``duration_ms`` 的 JSON 对象。
    模块不自行探测本地文件，避免把未准入的媒体或供应商调用伪装成真实结果。
    """
    if isinstance(source, Mapping):
        for key in ("duration", "duration_seconds", "audio_duration", "duration_s"):
            if key in source:
                return _positive_duration(
                    source[key], code="INVALID_AUDIO_DURATION", field=key
                )
        for key in ("duration_ms", "durationMs", "audio_duration_ms"):
            if key in source:
                return _positive_duration(
                    _number(source[key], key) / 1000.0,
                    code="INVALID_AUDIO_DURATION",
                    field=key,
                )
        format_data = source.get("format")
        if isinstance(format_data, Mapping):
            return extract_audio_duration(format_data)
        _fail("INVALID_AUDIO_DURATION", "TTS 结果缺少音频时长")
    return _positive_duration(
        source, code="INVALID_AUDIO_DURATION", field="audio_duration"
    )


def normalize_word_timestamps(
    timestamps: Iterable[Mapping[str, Any]] | Mapping[str, Any],
    *,
    audio_duration: Any | None = None,
    allow_empty: bool = False,
) -> list[dict[str, Any]]:
    """规范化词级时间戳并验证时间轴单调递增。"""
    if isinstance(timestamps, Mapping):
        timestamps = timestamps.get("words")  # type: ignore[assignment]
    if timestamps is None or isinstance(timestamps, (str, bytes)):
        _fail("INVALID_AUDIO_TIMESTAMPS", "词级时间戳必须是数组")
    try:
        items = list(timestamps)
    except TypeError:
        _fail("INVALID_AUDIO_TIMESTAMPS", "词级时间戳必须是数组")
    if not items and not allow_empty:
        _fail("INVALID_AUDIO_TIMESTAMPS", "词级时间戳不能为空")

    limit = None if audio_duration is None else extract_audio_duration(audio_duration)
    normalized: list[dict[str, Any]] = []
    previous_end = 0.0
    for index, item in enumerate(items):
        if not isinstance(item, Mapping):
            _fail("INVALID_AUDIO_TIMESTAMPS", f"第 {index + 1} 个词的时间戳必须是对象")
        word = item.get("word", item.get("text", item.get("token")))
        if not isinstance(word, str) or not word.strip():
            _fail("INVALID_AUDIO_TIMESTAMPS", f"第 {index + 1} 个词缺少文本")
        start_key = (
            "start_ms"
            if "start_ms" in item
            else "startMs"
            if "startMs" in item
            else "start"
        )
        end_key = (
            "end_ms" if "end_ms" in item else "endMs" if "endMs" in item else "end"
        )
        if start_key not in item or end_key not in item:
            _fail("INVALID_AUDIO_TIMESTAMPS", f"第 {index + 1} 个词缺少 start/end")
        start = _seconds(
            item[start_key],
            field=start_key,
            milliseconds=start_key.lower().endswith("ms"),
        )
        end = _seconds(
            item[end_key], field=end_key, milliseconds=end_key.lower().endswith("ms")
        )
        if end <= start or start + _EPSILON < previous_end:
            _fail(
                "INVALID_AUDIO_TIMESTAMPS", "词级时间戳必须按时间递增且 end 大于 start"
            )
        if limit is not None and end > limit + _EPSILON:
            _fail("INVALID_AUDIO_TIMESTAMPS", "词级时间戳不能超出音频时长")
        normalized.append({"word": word.strip(), "start": start, "end": end})
        previous_end = end
    return normalized


def calculate_shot_duration(
    audio_duration: Any, pad: Any = DEFAULT_PAD_SECONDS
) -> float:
    """按 ``T_shot = T_audio + pad`` 计算镜头物理时长。"""
    duration = extract_audio_duration(audio_duration)
    return _stable(duration + _pad(pad))


@dataclass(frozen=True)
class WordTimestamp:
    """可序列化的单词时间戳值对象。"""

    word: str
    start: float
    end: float

    def as_dict(self) -> dict[str, Any]:
        return {"word": self.word, "start": self.start, "end": self.end}


@dataclass(frozen=True)
class ShotTiming:
    """单镜头 Auto-Timing 计算结果。"""

    shot_id: str
    audio_duration: float
    pad: float
    target_duration: float
    word_timestamps: tuple[dict[str, Any], ...]
    audio_asset_id: str | None = None
    fixture_id: str | None = None

    @property
    def duration(self) -> float:
        return self.target_duration

    def as_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "shot_id": self.shot_id,
            "audio_duration": self.audio_duration,
            "pad": self.pad,
            "target_duration": self.target_duration,
            "duration": self.target_duration,
            "word_timestamps": [dict(item) for item in self.word_timestamps],
        }
        if self.audio_asset_id is not None:
            result["audio_asset_id"] = self.audio_asset_id
        if self.fixture_id is not None:
            result["fixture_id"] = self.fixture_id
        return result


def calculate_timing(
    *,
    shot_id: str,
    audio_duration: Any | None = None,
    audio_metadata: Any | None = None,
    pad: Any = DEFAULT_PAD_SECONDS,
    word_timestamps: Iterable[Mapping[str, Any]] | Mapping[str, Any] | None = None,
    audio_asset_id: str | None = None,
    fixture_id: str | None = None,
) -> dict[str, Any]:
    """生成可写回时间线的单镜头 Auto-Timing 记录。"""
    if not isinstance(shot_id, str) or not shot_id.strip():
        _fail("INVALID_SHOT_ID", "shot_id 必须是非空字符串")
    if audio_duration is None:
        audio_duration = audio_metadata
    duration = extract_audio_duration(audio_duration)
    words = normalize_word_timestamps(word_timestamps or [], audio_duration=duration)
    pad_value = _pad(pad)
    if audio_asset_id is not None and (
        not isinstance(audio_asset_id, str) or not audio_asset_id.strip()
    ):
        _fail("INVALID_AUDIO_ASSET_ID", "audio_asset_id 必须是非空字符串")
    if fixture_id is not None and (
        not isinstance(fixture_id, str) or not fixture_id.strip()
    ):
        _fail("INVALID_FIXTURE_ID", "fixture_id 必须是非空字符串")
    return ShotTiming(
        shot_id=shot_id.strip(),
        audio_duration=duration,
        pad=pad_value,
        target_duration=_stable(duration + pad_value),
        word_timestamps=tuple(words),
        audio_asset_id=audio_asset_id.strip() if audio_asset_id is not None else None,
        fixture_id=fixture_id.strip() if fixture_id is not None else None,
    ).as_dict()


def _timing_records(timings: Any) -> list[dict[str, Any]]:
    """接受单条、数组或 shot_id 映射，统一为记录数组。"""
    if isinstance(timings, Mapping):
        if "shot_id" in timings:
            items: list[Any] = [timings]
        else:
            items = [
                {
                    "shot_id": key,
                    **(
                        dict(value)
                        if isinstance(value, Mapping)
                        else {"duration": value}
                    ),
                }
                for key, value in timings.items()
            ]
    elif isinstance(timings, Sequence) and not isinstance(timings, (str, bytes)):
        items = list(timings)
    else:
        _fail("INVALID_AUTO_TIMING", "timings 必须是记录数组或 shot_id 映射")

    records: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in items:
        if isinstance(item, ShotTiming):
            item = item.as_dict()
        if not isinstance(item, Mapping):
            _fail("INVALID_AUTO_TIMING", "timing 记录必须是对象")
        shot_id = item.get("shot_id")
        if not isinstance(shot_id, str) or not shot_id.strip():
            _fail("INVALID_SHOT_ID", "timing 记录缺少 shot_id")
        shot_id = shot_id.strip()
        if shot_id in seen:
            _fail("DUPLICATE_SHOT_TIMING", f"shot_id {shot_id} 重复")
        seen.add(shot_id)
        audio_duration: float | None = None
        if item.get("audio_duration") is not None:
            audio_duration = extract_audio_duration(item["audio_duration"])
        elif item.get("audio_metadata") is not None:
            audio_duration = extract_audio_duration(item["audio_metadata"])

        raw_duration = item.get("target_duration", item.get("duration"))
        if raw_duration is None and audio_duration is not None:
            duration = calculate_shot_duration(
                audio_duration, item.get("pad", DEFAULT_PAD_SECONDS)
            )
        else:
            duration = _positive_duration(
                raw_duration, code="INVALID_TARGET_DURATION", field="target_duration"
            )
            if audio_duration is not None:
                expected_duration = calculate_shot_duration(
                    audio_duration,
                    item.get("pad", DEFAULT_PAD_SECONDS),
                )
                if abs(duration - expected_duration) > _EPSILON:
                    _fail(
                        "INVALID_TARGET_DURATION",
                        "target_duration 必须等于 audio_duration + pad",
                    )
        record = dict(item)
        record["shot_id"] = shot_id
        record["target_duration"] = duration
        record["duration"] = duration
        if audio_duration is not None:
            record["audio_duration"] = audio_duration
        if "word_timestamps" in record:
            record["word_timestamps"] = normalize_word_timestamps(
                record["word_timestamps"],
                audio_duration=audio_duration,
            )
        records.append(record)
    if not records:
        _fail("INVALID_AUTO_TIMING", "至少需要一条 timing 记录")
    return records


def _track_kind(track: Mapping[str, Any]) -> str:
    return str(track.get("kind") or "video").strip().lower()


def _reflow_video_tracks(
    manifest: MutableMapping[str, Any],
    *,
    reflow: bool = True,
) -> dict[str, dict[str, Any]]:
    """按轨道顺序重算视频片段 at，并返回 shot_id 到片段的索引。"""
    shot_index: dict[str, dict[str, Any]] = {}
    for track in manifest.get("tracks", []):
        if not isinstance(track, MutableMapping):
            _fail("INVALID_TIMELINE_MANIFEST", "轨道必须是对象")
        clips = track.get("clips")
        if not isinstance(clips, list):
            _fail("INVALID_TIMELINE_MANIFEST", "轨道 clips 必须是数组")
        timed_track = _track_kind(track) in {"video", "image", "picture"}
        first_at = 0.0
        if (
            timed_track
            and clips
            and isinstance(clips[0], Mapping)
            and clips[0].get("at") is not None
        ):
            first_at = _seconds(clips[0]["at"], field="at")
        cursor = first_at
        for clip in clips:
            if not isinstance(clip, MutableMapping):
                _fail("INVALID_TIMELINE_MANIFEST", "clip 必须是对象")
            shot_id = clip.get("shot_id")
            if not isinstance(shot_id, str) or not shot_id.strip():
                _fail("INVALID_TIMELINE_MANIFEST", "clip 缺少 shot_id")
            shot_id = shot_id.strip()
            if timed_track and shot_id in shot_index:
                _fail("INVALID_TIMELINE_MANIFEST", f"shot_id {shot_id} 必须唯一")
            if timed_track:
                shot_index[shot_id] = clip
                clip_duration = _positive_duration(
                    clip.get("duration"),
                    code="INVALID_TIMELINE_MANIFEST",
                    field="duration",
                )
                if reflow:
                    clip["at"] = _stable(cursor)
                    cursor += clip_duration
                else:
                    _seconds(clip.get("at"), field="at")
    return shot_index


def apply_auto_timing_to_manifest(
    manifest: Mapping[str, Any],
    timings: Any,
    *,
    expected_version: Any | None = None,
    reflow: bool = True,
) -> dict[str, Any]:
    """以 CAS 方式把 Auto-Timing 结果写入 manifest 快照。

    返回深拷贝，输入不会被修改；上层可把返回值交给现有 timeline 仓储的原子
    ``update_timeline_manifest``。任何一条记录失败时都不会返回部分更新结果。
    """
    if not isinstance(manifest, Mapping):
        _fail("INVALID_TIMELINE_MANIFEST", "timeline_manifest 必须是对象")
    if (
        isinstance(expected_version, bool)
        or not isinstance(expected_version, int)
        or expected_version < 1
    ):
        _fail(
            "EXPECTED_VERSION_REQUIRED",
            "更新 timeline_manifest 必须携带正整数 expected_version",
        )
    current = manifest.get("version")
    if isinstance(current, bool) or not isinstance(current, int) or current < 1:
        _fail("INVALID_VERSION", "timeline_manifest.version 必须是正整数")
    if expected_version != current:
        raise VersionConflictException(
            expected_version, current, "timeline_manifest 版本冲突，请重新读取后重试"
        )
    if not isinstance(manifest.get("tracks"), list) or not manifest["tracks"]:
        _fail("INVALID_TIMELINE_MANIFEST", "timeline_manifest 至少需要一个轨道")

    records = _timing_records(timings)
    updated = deepcopy(dict(manifest))
    # 先建立索引并校验完整旧 manifest，保证后续出错时没有部分结果暴露。
    shot_index = _reflow_video_tracks(updated, reflow=False)
    for record in records:
        shot_id = str(record["shot_id"])
        if shot_id not in shot_index:
            _fail("SHOT_NOT_FOUND", f"时间线中不存在镜头 {shot_id}", status_code=404)

    for record in records:
        clip = shot_index[str(record["shot_id"])]
        clip["duration"] = float(record["target_duration"])
        clip["target_duration"] = float(record["target_duration"])
        if record.get("audio_asset_id"):
            clip["audio_asset_id"] = str(record["audio_asset_id"])

    if reflow:
        shot_index = _reflow_video_tracks(updated, reflow=True)
    if not isinstance(updated.get("metadata"), MutableMapping):
        updated["metadata"] = {}
    auto_timing = updated["metadata"].setdefault("auto_timing", {})
    if not isinstance(auto_timing, MutableMapping):
        _fail("INVALID_TIMELINE_MANIFEST", "metadata.auto_timing 必须是对象")
    for record in records:
        auto_timing[str(record["shot_id"])] = deepcopy(dict(record))
    updated["version"] = current + 1
    return updated


def apply_auto_timing(*args: Any, **kwargs: Any) -> dict[str, Any]:
    """简短别名，供服务层调用。"""
    return apply_auto_timing_to_manifest(*args, **kwargs)


def update_timeline_manifest(*args: Any, **kwargs: Any) -> dict[str, Any]:
    """与现有时间线仓储语义一致的别名。"""
    return apply_auto_timing_to_manifest(*args, **kwargs)


class AutoTimingService:
    """无状态 Auto-Timing 服务门面；不负责凭据、网络或落盘。"""

    calculate_shot_duration = staticmethod(calculate_shot_duration)
    normalize_word_timestamps = staticmethod(normalize_word_timestamps)
    extract_audio_duration = staticmethod(extract_audio_duration)
    calculate_timing = staticmethod(calculate_timing)
    apply = staticmethod(apply_auto_timing_to_manifest)


__all__ = [
    "AutoTimingService",
    "DEFAULT_PAD_SECONDS",
    "ShotTiming",
    "WordTimestamp",
    "apply_auto_timing",
    "apply_auto_timing_to_manifest",
    "calculate_shot_duration",
    "calculate_timing",
    "extract_audio_duration",
    "normalize_word_timestamps",
    "update_timeline_manifest",
]
