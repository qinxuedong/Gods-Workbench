"""C12-T2 分场分镜 Agent 结构化输出模板。

模板只描述可被机器校验的输出边界；具体 JSON Schema 由导出模块中的
Pydantic 契约生成，避免模板与运行时模型出现两份不一致的字段定义。
"""

from __future__ import annotations

from types import MappingProxyType
from typing import Any


BREAKDOWN_TEMPLATE_ID = "agent.breakdown"
BREAKDOWN_TEMPLATE_VERSION = "1.0.0"

BREAKDOWN_CATEGORIES: tuple[str, ...] = (
    "01_CAST",
    "02_EXTRAS",
    "03_PROPS",
    "04_WARDROBE",
    "05_VFX",
    "06_SFX",
    "07_LOCATION",
    "08_VEHICLES",
)

ASC_METADATA_ENUMS: MappingProxyType[str, tuple[str, ...]] = MappingProxyType(
    {
        "shot_size": ("EWS", "WS", "MS", "MCU", "CU", "ECU"),
        "camera_angle": (
            "EYE_LEVEL",
            "LOW_ANGLE",
            "HIGH_ANGLE",
            "GODS_EYE",
            "DUTCH_ANGLE",
            "GROUND_LEVEL",
        ),
        "camera_movement": (
            "STATIC",
            "PAN",
            "TILT",
            "DOLLY_IN",
            "DOLLY_OUT",
            "TRUCK_LEFT",
            "TRUCK_RIGHT",
            "PEDESTAL_UP",
            "PEDESTAL_DOWN",
            "ARC",
            "WHIP_PAN",
        ),
        "focal_length": ("ULTRA_WIDE", "WIDE", "STANDARD", "PORTRAIT", "TELEPHOTO"),
    }
)

BREAKDOWN_PROMPT = (
    "Return exactly one JSON object matching the agent.breakdown v1 contract. "
    "Every scene must include all eight production-element keys: "
    + ", ".join(BREAKDOWN_CATEGORIES)
    + ". Every shot must provide ASC values for shot_size, camera_angle, "
    "camera_movement, and focal_length using enum codes only; do not return "
    "natural-language substitutes such as 特写, 拉远, or 长焦."
)

# This is intentionally a small, JSON-serialisable manifest for prompt/catalog
# discovery. The authoritative field validation remains the Pydantic model.
BREAKDOWN_TEMPLATE: dict[str, Any] = {
    "template_id": BREAKDOWN_TEMPLATE_ID,
    "version": BREAKDOWN_TEMPLATE_VERSION,
    "schema_version": 1,
    "required_categories": list(BREAKDOWN_CATEGORIES),
    "asc_metadata": {key: list(values) for key, values in ASC_METADATA_ENUMS.items()},
    "prompt": BREAKDOWN_PROMPT,
}


__all__ = [
    "ASC_METADATA_ENUMS",
    "BREAKDOWN_CATEGORIES",
    "BREAKDOWN_PROMPT",
    "BREAKDOWN_TEMPLATE",
    "BREAKDOWN_TEMPLATE_ID",
    "BREAKDOWN_TEMPLATE_VERSION",
]
