"""System One 的轻量循环去偏与概率归一化算法。"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import TypeVar


T = TypeVar("T")
NumericValues = Mapping[str, float] | Sequence[float]


def cyclic_shift(values: Sequence[T], offset: int) -> tuple[T, ...]:
    """循环移动选项顺序，正负偏移都按选项数量归一。"""
    items = tuple(values)
    if not items:
        return ()
    shift = offset % len(items)
    return items[shift:] + items[:shift]


def cyclic_permutations(options: Sequence[T], *, include_reverse: bool = True) -> tuple[tuple[T, ...], ...]:
    """生成全部循环位移，可选加入反向顺序的循环位移。"""
    items = tuple(options)
    rotations = [cyclic_shift(items, offset) for offset in range(len(items))]
    if include_reverse:
        reversed_items = tuple(reversed(items))
        rotations.extend(cyclic_shift(reversed_items, offset) for offset in range(len(items)))
    return tuple(dict.fromkeys(rotations))


def _finite_non_negative(value: float) -> float:
    if isinstance(value, bool):
        raise ValueError("概率或分数必须是有限的非负数")
    try:
        numeric = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("概率或分数必须是有限的非负数") from exc
    if not math.isfinite(numeric) or numeric < 0:
        raise ValueError("概率或分数必须是有限的非负数")
    return numeric


def normalize_probabilities(values: NumericValues) -> dict[str, float] | tuple[float, ...]:
    """将非负权重归一化；全零输入转为均匀分布。"""
    if isinstance(values, Mapping):
        items: list[tuple[str, float]] = []
        seen: set[str] = set()
        for key, value in values.items():
            normalized_key = str(key).strip()
            if not normalized_key or normalized_key in seen:
                raise ValueError("选项 id 必须非空且唯一")
            seen.add(normalized_key)
            items.append((normalized_key, _finite_non_negative(value)))
        if not items:
            raise ValueError("至少需要一个选项")
        total = sum(value for _, value in items)
        if not math.isfinite(total):
            raise ValueError("概率或分数总和必须是有限数")
        if total == 0:
            share = 1.0 / len(items)
            return {key: share for key, _ in items}
        return {key: value / total for key, value in items}

    if isinstance(values, (str, bytes)):
        raise ValueError("概率或分数必须是数字序列")
    items = tuple(_finite_non_negative(value) for value in values)
    if not items:
        raise ValueError("至少需要一个选项")
    total = sum(items)
    if not math.isfinite(total):
        raise ValueError("概率或分数总和必须是有限数")
    if total == 0:
        return tuple(1.0 / len(items) for _ in items)
    return tuple(value / total for value in items)


def debias_probability_runs(runs: Sequence[NumericValues]) -> dict[str, float] | tuple[float, ...]:
    """平均多轮选项乱序结果并归一化，抵消位置偏好。"""
    if isinstance(runs, (str, bytes)) or not runs:
        raise ValueError("至少需要一轮决策结果")

    first = runs[0]
    if isinstance(first, Mapping):
        keys = tuple(str(key).strip() for key in first)
        if not keys or any(not key for key in keys) or len(set(keys)) != len(keys):
            raise ValueError("每轮决策必须包含唯一的非空选项 id")
        totals = {key: 0.0 for key in keys}
        for run in runs:
            if not isinstance(run, Mapping) or {str(key).strip() for key in run} != set(keys):
                raise ValueError("每轮决策必须包含同一组选项")
            normalized = normalize_probabilities({str(key).strip(): value for key, value in run.items()})
            if not isinstance(normalized, dict):
                raise ValueError("每轮决策结果格式不一致")
            for key in keys:
                totals[key] += normalized[key]
        averaged = {key: value / len(runs) for key, value in totals.items()}
        result = normalize_probabilities(averaged)
        if not isinstance(result, dict):
            raise ValueError("去偏结果格式不一致")
        return result

    if isinstance(first, (str, bytes, Mapping)):
        raise ValueError("每轮决策必须使用相同的数字序列格式")
    width = len(first)
    if width == 0:
        raise ValueError("至少需要一个选项")
    totals = [0.0] * width
    for run in runs:
        if isinstance(run, (str, bytes, Mapping)) or len(run) != width:
            raise ValueError("每轮决策必须包含相同数量的选项")
        normalized = normalize_probabilities(run)
        if not isinstance(normalized, tuple):
            raise ValueError("每轮决策结果格式不一致")
        for index, value in enumerate(normalized):
            totals[index] += value
    result = normalize_probabilities(tuple(value / len(runs) for value in totals))
    if not isinstance(result, tuple):
        raise ValueError("去偏结果格式不一致")
    return result


def position_bias_drift(runs: Sequence[NumericValues]) -> float:
    """返回归一化去偏结果中最大与最小选项概率的差，单位为百分点。"""
    result = debias_probability_runs(runs)
    probabilities = tuple(result.values()) if isinstance(result, Mapping) else result
    return (max(probabilities) - min(probabilities)) * 100.0


__all__ = [
    "cyclic_permutations",
    "cyclic_shift",
    "debias_probability_runs",
    "normalize_probabilities",
    "position_bias_drift",
]
