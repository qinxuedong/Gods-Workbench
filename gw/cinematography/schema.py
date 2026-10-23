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

"""C12 影视工业元数据的 Pydantic v2 强类型契约。

枚举值直接采用 ``CINEMATOGRAPHY-METADATA-SPECIFICATION.md`` 中的工业
代号，避免把展示文案写入持久化数据。实体母库请求模型也放在这里，供
路由、Agent 导出和后续时间线模块复用。
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, field_validator


class _ContractModel(BaseModel):
    """所有公开契约拒绝未登记字段，防止模糊自然语言悄悄进入母库。"""

    model_config = ConfigDict(extra="forbid", use_enum_values=False)


class ShotSize(str, Enum):
    """按人体解剖切线定义的六级景别。"""

    EWS = "EWS"
    WS = "WS"
    MS = "MS"
    MCU = "MCU"
    CU = "CU"
    ECU = "ECU"


class CameraAngle(str, Enum):
    """摄影机视角与高度。"""

    EYE_LEVEL = "EYE_LEVEL"
    LOW_ANGLE = "LOW_ANGLE"
    HIGH_ANGLE = "HIGH_ANGLE"
    GODS_EYE = "GODS_EYE"
    DUTCH_ANGLE = "DUTCH_ANGLE"
    GROUND_LEVEL = "GROUND_LEVEL"


class CameraMovement(str, Enum):
    """ASC/SMPTE 受控运镜代码（共 11 种）。"""

    STATIC = "STATIC"
    PAN = "PAN"
    TILT = "TILT"
    DOLLY_IN = "DOLLY_IN"
    DOLLY_OUT = "DOLLY_OUT"
    TRUCK_LEFT = "TRUCK_LEFT"
    TRUCK_RIGHT = "TRUCK_RIGHT"
    PEDESTAL_UP = "PEDESTAL_UP"
    PEDESTAL_DOWN = "PEDESTAL_DOWN"
    ARC = "ARC"
    WHIP_PAN = "WHIP_PAN"


class FocalLengthTier(str, Enum):
    """五档工业焦段。"""

    ULTRA_WIDE = "ULTRA_WIDE"
    WIDE = "WIDE"
    STANDARD = "STANDARD"
    PORTRAIT = "PORTRAIT"
    TELEPHOTO = "TELEPHOTO"


class CompositionRule(str, Enum):
    """登记的构图法则。"""

    RULE_OF_THIRDS = "RULE_OF_THIRDS"
    CENTER_SYMMETRY = "CENTER_SYMMETRY"
    GOLDEN_RATIO = "GOLDEN_RATIO"
    LEADING_LINES = "LEADING_LINES"
    FRAME_IN_FRAME = "FRAME_IN_FRAME"
    LOOK_ROOM = "LOOK_ROOM"


class BreakdownCategory(str, Enum):
    """好莱坞八大拆解元素代码。"""

    CAST = "01_CAST"
    EXTRAS = "02_EXTRAS"
    PROPS = "03_PROPS"
    WARDROBE = "04_WARDROBE"
    VFX = "05_VFX"
    SFX = "06_SFX"
    LOCATION = "07_LOCATION"
    VEHICLES = "08_VEHICLES"


class SceneSettingType(str, Enum):
    INT = "INT"
    EXT = "EXT"
    INT_EXT = "INT/EXT"


class LightingPhase(str, Enum):
    DAY = "DAY"
    NIGHT = "NIGHT"
    GOLDEN_HOUR = "GOLDEN_HOUR"
    MAGIC_HOUR = "MAGIC_HOUR"
    DAWN = "DAWN"
    DUSK = "DUSK"
    CONTINUOUS = "CONTINUOUS"
    MOMENTS_LATER = "MOMENTS_LATER"


class WeatherAtmosphere(str, Enum):
    CLEAR = "CLEAR"
    OVERCAST = "OVERCAST"
    HEAVY_RAIN = "HEAVY_RAIN"
    BLIZZARD = "BLIZZARD"
    DENSE_FOG = "DENSE_FOG"
    SMOKE_HAZE = "SMOKE_HAZE"


class MasterEntityType(str, Enum):
    """C12-T1 首批母库实体类型。"""

    CHARACTER = "character"
    PROP = "prop"
    LOCATION = "location"


class CinematographyMetadata(_ContractModel):
    """镜头光学与美学元数据强类型对象。"""

    shot_size: ShotSize = Field(default=ShotSize.MS, description="标准解剖景别")
    camera_angle: CameraAngle = Field(
        default=CameraAngle.EYE_LEVEL, description="机位视角与高度"
    )
    camera_movement: CameraMovement = Field(
        default=CameraMovement.STATIC, description="机动运镜形式"
    )
    focal_length: FocalLengthTier = Field(
        default=FocalLengthTier.STANDARD, description="焦段分级"
    )
    composition: CompositionRule = Field(
        default=CompositionRule.RULE_OF_THIRDS, description="构图法则"
    )
    lighting_phase: LightingPhase | None = Field(default=None, description="光照时段")
    weather: WeatherAtmosphere | None = Field(default=None, description="天气大气介质")


def _clean_text(value: Any) -> str:
    if not isinstance(value, str):
        raise ValueError("必须是文本")
    value = value.strip()
    if not value:
        raise ValueError("不能为空")
    return value


def _clean_ascii_id(value: Any, field_name: str) -> str:
    value = _clean_text(value)
    if not value.isascii() or any(char.isspace() for char in value):
        raise ValueError(f"{field_name} 必须是非空 ASCII 标识")
    return value


class EntityCreatePayload(_ContractModel):
    """实体母库登记请求。"""

    project_id: str
    entity_type: MasterEntityType
    name: str
    description: str | None = None
    feature_base_prompt: str | None = Field(
        default=None,
        validation_alias=AliasChoices("feature_base_prompt", "base_prompt"),
    )
    lora_trigger_words: list[str] = Field(
        default_factory=list,
        validation_alias=AliasChoices("lora_trigger_words", "lora_triggers"),
    )
    lora_id: str | None = None
    lora_strength: float | None = Field(default=None, ge=0.0, le=2.0)
    cinematography: CinematographyMetadata = Field(
        default_factory=CinematographyMetadata
    )
    metadata: dict[str, Any] = Field(default_factory=dict)
    expected_version: int | None = Field(default=None, ge=1)

    @field_validator("project_id")
    @classmethod
    def validate_project_id(cls, value: str) -> str:
        return _clean_ascii_id(value, "project_id")

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        return _clean_text(value)

    @field_validator("description", "feature_base_prompt", "lora_id")
    @classmethod
    def normalize_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return value.strip() or None

    @field_validator("lora_trigger_words")
    @classmethod
    def normalize_lora_trigger_words(cls, values: list[str]) -> list[str]:
        normalized: list[str] = []
        for value in values:
            value = _clean_text(value)
            if value not in normalized:
                normalized.append(value)
        return normalized


class EntityPatchPayload(_ContractModel):
    """实体母库更新请求；所有写入必须带顶层 expected_version。"""

    expected_version: int = Field(..., ge=1)
    name: str | None = None
    description: str | None = None
    feature_base_prompt: str | None = Field(
        default=None,
        validation_alias=AliasChoices("feature_base_prompt", "base_prompt"),
    )
    lora_trigger_words: list[str] | None = Field(
        default=None,
        validation_alias=AliasChoices("lora_trigger_words", "lora_triggers"),
    )
    lora_id: str | None = None
    lora_strength: float | None = Field(default=None, ge=0.0, le=2.0)
    cinematography: CinematographyMetadata | None = None
    metadata: dict[str, Any] | None = None

    @field_validator("name")
    @classmethod
    def validate_optional_name(cls, value: str | None) -> str | None:
        return None if value is None else _clean_text(value)

    @field_validator("description", "feature_base_prompt", "lora_id")
    @classmethod
    def normalize_patch_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return value.strip() or None

    @field_validator("lora_trigger_words")
    @classmethod
    def normalize_patch_lora_words(cls, values: list[str] | None) -> list[str] | None:
        if values is None:
            return None
        normalized: list[str] = []
        for value in values:
            value = _clean_text(value)
            if value not in normalized:
                normalized.append(value)
        return normalized


class MasterCatalogEntity(_ContractModel):
    """实体母库持久化/响应模型。"""

    entity_id: str
    project_id: str
    entity_type: MasterEntityType
    name: str
    description: str | None = None
    feature_base_prompt: str | None = None
    lora_trigger_words: list[str] = Field(default_factory=list)
    lora_id: str | None = None
    lora_strength: float | None = Field(default=None, ge=0.0, le=2.0)
    cinematography: CinematographyMetadata = Field(
        default_factory=CinematographyMetadata
    )
    metadata: dict[str, Any] = Field(default_factory=dict)
    version: int = Field(default=1, ge=1)
    archived: bool = False
    created_at: str
    updated_at: str


# 便于调用方采用更短的语义名，同时保留正式规范名称。
EntityType = MasterEntityType
EntityCreate = EntityCreatePayload
EntityUpdate = EntityPatchPayload
EntityRecord = MasterCatalogEntity


__all__ = [
    "BreakdownCategory",
    "CameraAngle",
    "CameraMovement",
    "CinematographyMetadata",
    "CompositionRule",
    "EntityCreate",
    "EntityCreatePayload",
    "EntityPatchPayload",
    "EntityRecord",
    "EntityType",
    "EntityUpdate",
    "FocalLengthTier",
    "LightingPhase",
    "MasterCatalogEntity",
    "MasterEntityType",
    "SceneSettingType",
    "ShotSize",
    "WeatherAtmosphere",
]
