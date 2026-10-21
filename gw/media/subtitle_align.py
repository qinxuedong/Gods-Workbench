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

"""C15-T2 词级 WebVTT 字幕生成。"""

from __future__ import annotations

from html import escape
from typing import Any, Iterable, Mapping

from gw.core.errors import CleanroomException
from gw.media.audio_timing import normalize_word_timestamps


def _fail(code: str, message: str) -> None:
    raise CleanroomException(400, code, message)


def _milliseconds(value: float) -> int:
    return int(round(float(value) * 1000))


def _format_timestamp(milliseconds: int) -> str:
    hours, remainder = divmod(milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    seconds, millis = divmod(remainder, 1000)
    if hours:
        return f"{hours:02d}:{minutes:02d}:{seconds:02d}.{millis:03d}"
    return f"{minutes:02d}:{seconds:02d}.{millis:03d}"


def generate_webvtt(
    word_timestamps: Iterable[Mapping[str, Any]] | Mapping[str, Any],
    *,
    word_separator: str = " ",
) -> str:
    """把单段词级时间戳转换为动态高亮 WebVTT。

    每个词的起始点作为 cue 内时间戳；输入可以使用秒或毫秒字段，并沿用
    C13 的递增、非重叠及有限值校验。WebVTT 精度为毫秒，无法表示的时间轴会
    失败关闭，避免不同词的时间戳量化成同一时刻。
    """
    if not isinstance(word_separator, str) or "\n" in word_separator or "\r" in word_separator:
        _fail("INVALID_SUBTITLE_SEPARATOR", "word_separator 必须是单行文本")
    words = normalize_word_timestamps(word_timestamps)
    if any("\n" in item["word"] or "\r" in item["word"] for item in words):
        _fail("INVALID_SUBTITLE_WORD", "每个词不能包含换行符")

    cue_start = _milliseconds(words[0]["start"])
    cue_end = _milliseconds(words[-1]["end"])
    if cue_end <= cue_start:
        _fail("INVALID_WEBVTT_TIMESTAMPS", "WebVTT cue 至少需要 1 毫秒时长")

    payload = [escape(words[0]["word"], quote=False)]
    previous_marker = cue_start
    for word in words[1:]:
        marker = _milliseconds(word["start"])
        if marker <= previous_marker or marker >= cue_end:
            _fail(
                "INVALID_WEBVTT_TIMESTAMPS",
                "词级时间戳经毫秒精度转换后必须严格递增且位于 cue 内",
            )
        payload.append(f"<{_format_timestamp(marker)}>")
        payload.append(word_separator)
        payload.append(escape(word["word"], quote=False))
        previous_marker = marker

    cue = (
        f"{_format_timestamp(cue_start)} --> {_format_timestamp(cue_end)}\n"
        f"{''.join(payload)}"
    )
    return (
        "WEBVTT\n\n"
        "STYLE\n"
        "::cue(:past) { color: yellow; }\n"
        "::cue(:future) { color: white; }\n\n"
        f"{cue}\n"
    )


__all__ = ["generate_webvtt"]
