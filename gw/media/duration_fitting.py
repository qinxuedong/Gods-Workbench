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

"""C14-T1 I2V 控时长契约与纯函数时序适配。

本模块只负责校验 I2V 请求和计算媒体时长变换，不读取文件、不启动 FFmpeg、
不联网，也不保存凭据。C14-T2 的执行器可以把本模块返回的 JSON 交给实际宿主，
C14-T4 再用真实媒体探测结果调用 ``validate_duration_match``。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from copy import deepcopy
from math import isfinite
from numbers import Real
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from gw.core.errors import CleanroomException

DEFAULT_DURATION_TOLERANCE_SECONDS = 0.1
MIN_TARGET_DURATION_SECONDS = 0.1
MAX_TARGET_DURATION_SECONDS = 120.0
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


def _finite_number(value: Any, *, field: str, code: str) -> float:
    """解析不接受 bool、NaN 或 Infinity 的有限实数。"""
    if isinstance(value, bool) or not isinstance(value, Real):
        _fail(code, f"{field} 必须是有限数字")
    result = float(value)
    if not isfinite(result):
        _fail(code, f"{field} 必须是有限数字")
    return round(result, _ROUND_DIGITS)


def _duration(
    value: Any,
    *,
    field: str,
    code: str,
    minimum: float = 0.0,
    maximum: float | None = None,
) -> float:
    result = _finite_number(value, field=field, code=code)
    if result <= minimum:
        _fail(code, f"{field} 必须大于 {minimum:g}")
    if maximum is not None and result > maximum:
        _fail(code, f"{field} 不能超过 {maximum:g} 秒")
    return result


def _required_text(value: Any, *, field: str, code: str) -> str:
    if not isinstance(value, str) or not value.strip():
        _fail(code, f"{field} 必须是非空字符串")
    return value.strip()


def _format_seconds(value: float) -> str:
    """生成 URL media fragment 使用的稳定秒数文本。"""
    formatted = f"{float(value):.{_ROUND_DIGITS}f}".rstrip("0").rstrip(".")
    return formatted or "0"


def _normalize_camera_ctrl(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    if not isinstance(value, Mapping):
        _fail("INVALID_CAMERA_CTRL", "camera_ctrl 必须是对象")
    result = deepcopy(dict(value))
    trajectory = result.get("trajectory")
    if trajectory is None:
        return result
    if isinstance(trajectory, (str, bytes)) or not isinstance(trajectory, Sequence):
        _fail("INVALID_CAMERA_CTRL", "camera_ctrl.trajectory 必须是数组")
    normalized: list[dict[str, Any]] = []
    previous_at = -_EPSILON
    for index, point in enumerate(trajectory):
        if not isinstance(point, Mapping):
            _fail("INVALID_CAMERA_CTRL", f"trajectory 第 {index + 1} 项必须是对象")
        item = deepcopy(dict(point))
        if "at" not in item:
            _fail("INVALID_CAMERA_CTRL", f"trajectory 第 {index + 1} 项缺少 at")
        at = _finite_number(
            item["at"], field="camera_ctrl.trajectory.at", code="INVALID_CAMERA_CTRL"
        )
        if at < 0 or at + _EPSILON < previous_at:
            _fail(
                "INVALID_CAMERA_CTRL", "camera_ctrl.trajectory.at 必须从 0 开始单调递增"
            )
        for axis in ("x", "y", "z", "yaw", "pitch", "roll"):
            if axis in item:
                item[axis] = _finite_number(
                    item[axis],
                    field=f"camera_ctrl.trajectory.{axis}",
                    code="INVALID_CAMERA_CTRL",
                )
        item["at"] = at
        previous_at = at
        normalized.append(item)
    result["trajectory"] = normalized
    return result


def _normalize_lora(value: Any) -> list[dict[str, Any]] | None:
    if value is None:
        return None
    if isinstance(value, Mapping):
        values: list[Any] = [value]
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        values = list(value)
    else:
        _fail("INVALID_LORA", "lora 必须是对象或对象数组")

    normalized: list[dict[str, Any]] = []
    for index, item in enumerate(values):
        if not isinstance(item, Mapping):
            _fail("INVALID_LORA", f"lora 第 {index + 1} 项必须是对象")
        result = deepcopy(dict(item))
        name = result.get("name", result.get("model"))
        if not isinstance(name, str) or not name.strip():
            _fail("INVALID_LORA", f"lora 第 {index + 1} 项缺少 name/model")
        result["name"] = name.strip()
        if "weight" not in result:
            result["weight"] = 1.0
        result["weight"] = _finite_number(
            result["weight"], field="lora.weight", code="INVALID_LORA"
        )
        normalized.append(result)
    return normalized


def validate_target_duration(value: Any) -> float:
    """校验并规范化模型请求中的 ``target_duration``。"""
    return _duration(
        value,
        field="target_duration",
        code="INVALID_TARGET_DURATION",
        minimum=MIN_TARGET_DURATION_SECONDS - _EPSILON,
        maximum=MAX_TARGET_DURATION_SECONDS,
    )


def validate_source_duration(value: Any) -> float:
    """校验生成媒体的实际秒数。实际媒体不在此处读取。"""
    return _duration(value, field="source_duration", code="INVALID_SOURCE_DURATION")


def calculate_speed_ratio(source_duration: Any, target_duration: Any) -> float:
    """计算 ``setpts`` 时间缩放因子：``target / source``。

    例如 5 秒素材适配到 4 秒目标时返回 0.8；实际播放速率为其倒数 1.25。
    """
    source = validate_source_duration(source_duration)
    target = validate_target_duration(target_duration)
    return round(target / source, _ROUND_DIGITS)


def build_media_fragment(media_url: str, target_duration: Any) -> str:
    """把媒体地址规范为 ``#t=0,T``，替换已有 fragment。"""
    if not isinstance(media_url, str):
        _fail("INVALID_MEDIA_URL", "media_url 必须是字符串")
    target = validate_target_duration(target_duration)
    parsed = urlsplit(media_url.strip())
    return urlunsplit(
        (
            parsed.scheme,
            parsed.netloc,
            parsed.path,
            parsed.query,
            f"t=0,{_format_seconds(target)}",
        )
    )


def fit_video_duration(
    *,
    source_duration: Any,
    target_duration: Any,
    media_url: str | None = None,
) -> dict[str, Any]:
    """返回不改写源文件的时长适配计划。

    ``speed_ratio`` 是 FFmpeg ``setpts`` 的时间缩放因子，
    ``playback_rate`` 是其倒数。最终媒体片段仍以 ``#t=0,T`` 约束播放终点。
    """
    source = validate_source_duration(source_duration)
    target = validate_target_duration(target_duration)
    speed_ratio = round(target / source, _ROUND_DIGITS)
    playback_rate = round(source / target, _ROUND_DIGITS)
    fitted = round(source * speed_ratio, _ROUND_DIGITS)
    result: dict[str, Any] = {
        "source_duration": source,
        "target_duration": target,
        "trim_start": 0.0,
        "trim_end": target,
        "speed_ratio": speed_ratio,
        "setpts_factor": speed_ratio,
        "playback_rate": playback_rate,
        "fitted_duration": fitted,
        "duration_error": round(abs(fitted - target), _ROUND_DIGITS),
        "media_fragment": build_media_fragment(media_url or "", target),
    }
    return result


def validate_duration_match(
    *,
    actual_duration: Any,
    target_duration: Any,
    tolerance: Any = DEFAULT_DURATION_TOLERANCE_SECONDS,
) -> dict[str, Any]:
    """校验真实媒体时长是否在对白锁定目标的误差范围内。"""
    actual = validate_source_duration(actual_duration)
    target = validate_target_duration(target_duration)
    tolerance_seconds = _finite_number(
        tolerance, field="tolerance", code="INVALID_DURATION_TOLERANCE"
    )
    if tolerance_seconds < 0:
        _fail("INVALID_DURATION_TOLERANCE", "tolerance 不能为负数")
    error = round(abs(actual - target), _ROUND_DIGITS)
    if error > tolerance_seconds + _EPSILON:
        _fail(
            "DURATION_TOLERANCE_EXCEEDED",
            "实际媒体时长超出 target_duration 容差",
            status_code=422,
            extra={
                "actual_duration": actual,
                "target_duration": target,
                "error_seconds": error,
                "tolerance_seconds": tolerance_seconds,
            },
        )
    return {
        "actual_duration": actual,
        "target_duration": target,
        "error_seconds": error,
        "tolerance_seconds": tolerance_seconds,
        "within_tolerance": True,
    }


def build_i2v_request(
    *,
    shot_id: Any,
    image_asset_id: Any,
    target_duration: Any,
    prompt: Any,
    camera_ctrl: Any = None,
    lora: Any = None,
    fixture_id: Any = None,
) -> dict[str, Any]:
    """构造可交给 C14-T2 执行器的 I2V 请求 JSON。"""
    result: dict[str, Any] = {
        "operation": "i2v",
        "shot_id": _required_text(shot_id, field="shot_id", code="INVALID_SHOT_ID"),
        "image_asset_id": _required_text(
            image_asset_id,
            field="image_asset_id",
            code="INVALID_IMAGE_ASSET_ID",
        ),
        "target_duration": validate_target_duration(target_duration),
        "prompt": _required_text(prompt, field="prompt", code="INVALID_PROMPT"),
    }
    normalized_camera = _normalize_camera_ctrl(camera_ctrl)
    if normalized_camera is not None:
        result["camera_ctrl"] = normalized_camera
    normalized_lora = _normalize_lora(lora)
    if normalized_lora is not None:
        result["lora"] = normalized_lora
    if fixture_id is not None:
        result["fixture_id"] = _required_text(
            fixture_id, field="fixture_id", code="INVALID_FIXTURE_ID"
        )
    return result


# 兼容上层执行器和测试夹具使用的直观别名。
fit_duration = fit_video_duration
build_i2v_payload = build_i2v_request
calculate_playback_rate = lambda source, target: round(
    validate_source_duration(source) / validate_target_duration(target), _ROUND_DIGITS
)


__all__ = [
    "DEFAULT_DURATION_TOLERANCE_SECONDS",
    "MAX_TARGET_DURATION_SECONDS",
    "MIN_TARGET_DURATION_SECONDS",
    "build_i2v_payload",
    "build_i2v_request",
    "build_media_fragment",
    "calculate_playback_rate",
    "calculate_speed_ratio",
    "fit_duration",
    "fit_video_duration",
    "validate_duration_match",
    "validate_source_duration",
    "validate_target_duration",
]
