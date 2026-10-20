"""System One 的轻量 AnyJev 循环轮换与概率归一化算法。"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import TypeVar


T = TypeVar("T")
NumericValues = Mapping[str, float] | Sequence[float]


def cyclic_shift(values: Sequence[T], offset: int) -> tuple[T, ...]:
    """按位置循环移动选项，保留原有相对顺序。"""
    items = tuple(values)
    if not items:
        return ()
    step = offset % len(items)
    return items[step:] + items[:step]


def cyclic_permutations(options: Sequence[T], *, include_reverse: bool = True) -> tuple[tuple[T, ...], ...]:
    """生成 AnyJev 使用的循环移位（可选反向）排列集合。"""
    items = tuple(options)
    rotations = [cyclic_shift(items, offset) for offset in range(len(items))]
    if include_reverse:
        reversed_items = tuple(reversed(items))
        rotations.extend(cyclic_shift(reversed_items, offset) for offset in range(len(items)))
    return tuple(dict.fromkeys(rotations))


def _finite_non_negative(value: float) -> float:
    numeric = float(value)
    if not math.isfinite(numeric) or numeric < 0:
        raise ValueError("概率或分数必须是有限的非负数")
    return numeric


def normalize_probabilities(values: NumericValues) -> dict[str, float] | tuple[float, ...]:
    """把分数归一化为概率；全零输入采用均匀分布，避免模型输出退化。"""
    if isinstance(values, Mapping):
        items = [(str(key), _finite_non_negative(value)) for key, value in values.items()]
        if not items:
            raise ValueError("至少需要一个选项")
        total = sum(value for _, value in items)
        if total == 0:
            share = 1 / len(items)
            return {key: share for key, _ in items}
        return {key: value / total for key, value in items}

    items = [_finite_non_negative(value) for value in values]
    if not items:
        raise ValueError("至少需要一个选项")
    total = sum(items)
    if total == 0:
        return tuple(1 / len(items) for _ in items)
    return tuple(value / total for value in items)


def debias_probability_runs(runs: Sequence[NumericValues]) -> dict[str, float] | tuple[float, ...]:
    """平均多轮置乱结果，再归一化为稳定的无位置偏置概率。"""
    if not runs:
        raise ValueError("至少需要一轮决策结果")

    first = runs[0]
    if isinstance(first, Mapping):
        keys = tuple(str(key) for key in first.keys())
        totals = {key: 0.0 for key in keys}
        for run in runs:
            if not isinstance(run, Mapping) or {str(key) for key in run} != set(keys):
                raise ValueError("每轮决策必须包含同一组选项")
            normalized = normalize_probabilities({str(key): value for key, value in run.items()})
            assert isinstance(normalized, dict)
            for key in keys:
                totals[key] += normalized[key]
        averaged = {key: value / len(runs) for key, value in totals.items()}
        result = normalize_probabilities(averaged)
        assert isinstance(result, dict)
        return result

    width = len(first)
    if width == 0:
        raise ValueError("至少需要一个选项")
    totals = [0.0] * width
    for run in runs:
        if isinstance(run, Mapping) or len(run) != width:
            raise ValueError("每轮决策必须包含相同数量的选项")
        normalized = normalize_probabilities(run)
        assert isinstance(normalized, tuple)
        for index, value in enumerate(normalized):
            totals[index] += value
    result = normalize_probabilities(tuple(value / len(runs) for value in totals))
    assert isinstance(result, tuple)
    return result


def position_bias_drift(runs: Sequence[NumericValues]) -> float:
    """返回去偏结果最大概率差，单位为百分点。"""
    result = debias_probability_runs(runs)
    probabilities = tuple(result.values()) if isinstance(result, Mapping) else result
    return (max(probabilities) - min(probabilities)) * 100


__all__ = [
    "cyclic_permutations",
    "cyclic_shift",
    "debias_probability_runs",
    "normalize_probabilities",
    "position_bias_drift",
]
