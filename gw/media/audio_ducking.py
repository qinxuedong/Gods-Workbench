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

"""基于对话音频生成背景音乐闪避增益包络。"""

from __future__ import annotations

import math
from collections.abc import Iterable
from itertools import islice
from numbers import Integral

from gw.core.errors import CleanroomException

_FRAME_SECONDS = 0.01
_DIALOGUE_THRESHOLD = 0.01  # -40 dBFS，输入样本为 [-1, 1] 浮点 PCM。
_DUCK_GAIN_DB = -12.0
_ATTACK_SECONDS = 0.2
_RELEASE_SECONDS = 0.3


def _fail(code: str, message: str) -> None:
    raise CleanroomException(400, code, message)


def build_ducking_envelope(
    samples: Iterable[float], sample_rate: int
) -> list[dict[str, float]]:
    """从归一化对话 PCM 生成以秒和 dB 表示的平滑增益控制点。

    每个约 10 ms 的帧使用 RMS 与 -40 dBFS 门限比较。检测到对话时，
    背景音乐增益在 200 ms 内降至 -12 dB；对话停止后在 300 ms 内恢复。
    输出点为 ``{"at": 秒, "gain_db": dB}``，可直接供音频渲染器应用于 BGM。
    """
    if (
        isinstance(sample_rate, bool)
        or not isinstance(sample_rate, Integral)
        or sample_rate <= 0
    ):
        _fail("INVALID_SAMPLE_RATE", "sample_rate 必须是正整数")

    if isinstance(samples, (str, bytes)) or not isinstance(samples, Iterable):
        _fail("INVALID_AUDIO_SAMPLES", "samples 必须是归一化浮点 PCM 可迭代对象")

    rate = int(sample_rate)
    frame_size = max(1, int(round(rate * _FRAME_SECONDS)))
    attack_step = abs(_DUCK_GAIN_DB) * frame_size / (rate * _ATTACK_SECONDS)
    release_step = abs(_DUCK_GAIN_DB) * frame_size / (rate * _RELEASE_SECONDS)
    gain_db = 0.0
    envelope: list[dict[str, float]] = []
    iterator = iter(samples)
    frame_start = 0

    while True:
        raw_frame = list(islice(iterator, frame_size))
        if not raw_frame:
            break
        frame: list[float] = []
        for sample in raw_frame:
            if isinstance(sample, bool):
                _fail("INVALID_AUDIO_SAMPLES", "音频样本必须是 [-1, 1] 内的有限数值")
            try:
                value = float(sample)
            except (TypeError, ValueError, OverflowError):
                _fail("INVALID_AUDIO_SAMPLES", "音频样本必须是 [-1, 1] 内的有限数值")
            if not math.isfinite(value) or not -1.0 <= value <= 1.0:
                _fail("INVALID_AUDIO_SAMPLES", "音频样本必须是 [-1, 1] 内的有限数值")
            frame.append(value)

        rms = math.sqrt(sum(sample * sample for sample in frame) / len(frame))
        if rms >= _DIALOGUE_THRESHOLD:
            gain_db = max(_DUCK_GAIN_DB, gain_db - attack_step)
        else:
            gain_db = min(0.0, gain_db + release_step)

        envelope.append(
            {
                "at": round(frame_start / rate, 3),
                "gain_db": round(gain_db, 3),
            }
        )
        frame_start += len(frame)

    if not envelope:
        _fail("EMPTY_AUDIO_SAMPLES", "samples 不能为空")

    return envelope


__all__ = ["build_ducking_envelope"]
