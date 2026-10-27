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

"""资产注册表摄制元数据过滤契约。"""

from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from gw.cinematography.schema import (
    CameraMovement,
    FocalLengthTier,
    LightingPhase,
    MasterEntityType,
    ShotSize,
)


class AspectRatio(str, Enum):
    """C18 资产过滤器支持的三种画幅比。"""

    LANDSCAPE_16_9 = "16:9"
    PORTRAIT_9_16 = "9:16"
    CINEMASCOPE_2_39_1 = "2.39:1"


class CinematographyAssetFilters(BaseModel):
    """资产列表新增的摄制及实体母库筛选项。

    各字段内部按 OR 匹配，字段之间按 AND 组合。实体引用使用资产
    ``metadata.entity_ids``（或同名顶层字段）中的稳定 entity_id。
    多值字段接受数组或逗号分隔字符串，内部统一归一化为数组。
    """

    model_config = ConfigDict(extra="forbid")

    shot_size: list[ShotSize] | None = Field(default=None, description="ASC 六级景别")
    camera_movement: list[CameraMovement] | None = Field(default=None, description="11 种受控运镜")
    focal_length: list[FocalLengthTier] | None = Field(default=None, description="五档焦段")
    lighting_phase: list[LightingPhase] | None = Field(default=None, description="光照时段")
    aspect_ratio: list[AspectRatio] | None = Field(default=None, description="画幅比")
    entity_type: list[MasterEntityType] | None = Field(
        default=None, description="实体母库类型：角色、道具或场景（location）"
    )
    entity_id: list[str] | None = Field(default=None, description="实体母库稳定 ID")

    @field_validator(
        "shot_size",
        "camera_movement",
        "focal_length",
        "lighting_phase",
        "aspect_ratio",
        "entity_type",
        "entity_id",
        mode="before",
    )
    @classmethod
    def normalize_csv_values(cls, values):
        if isinstance(values, str):
            return [part.strip() for part in values.split(",") if part.strip()]
        return values

    @field_validator(
        "shot_size",
        "camera_movement",
        "focal_length",
        "lighting_phase",
        "aspect_ratio",
        "entity_type",
    )
    @classmethod
    def sort_unique_values(cls, values):
        if values is None:
            return None
        return sorted(set(values), key=lambda value: value.value)

    @field_validator("entity_id")
    @classmethod
    def normalize_entity_ids(cls, values: list[str] | None) -> list[str] | None:
        if values is None:
            return None
        normalized = set()
        for value in values:
            value = value.strip()
            if not value or not value.isascii() or any(char.isspace() for char in value):
                raise ValueError("entity_id 必须是非空 ASCII 标识")
            normalized.add(value)
        return sorted(normalized)


class ProducedAssetProvenance(BaseModel):
    """生产链成片/中间产物回写素材注册表时必须携带的来源链。

    来源链是产物可反查的唯一依据：缺失、字段为 None、``produced_by`` 不是
    ``agent_pipeline`` 或出现未登记字段时，一律拒绝回写（不写入注册表）。
    ``reference_asset_ids`` 允许为空列表（产物无上游参考素材），但字段本身必须出现。
    """

    model_config = ConfigDict(extra="forbid")

    project_id: str = Field(..., min_length=1, description="产出所属项目稳定 ID")
    task_id: str = Field(..., min_length=1, description="产出任务稳定 ID")
    artifact_version_id: str = Field(..., min_length=1, description="产物版本稳定 ID")
    reference_asset_ids: list[str] = Field(
        ..., description="上游参考素材 ID 列表（可为空列表，字段必填）"
    )
    shot_ids: list[str] = Field(..., description="产物涉及的镜头 ID 列表")
    produced_by: Literal["agent_pipeline"] = Field(..., description="产出方标识")

    @field_validator("project_id", "task_id", "artifact_version_id")
    @classmethod
    def reject_blank_identifier(cls, value: str) -> str:
        """空白标识符等同缺字段：不接受仅由空白组成的来源链标识。"""
        if not value.strip():
            raise ValueError("来源链标识不得为空白字符串")
        return value.strip()

    @field_validator("reference_asset_ids", "shot_ids")
    @classmethod
    def reject_blank_id_items(cls, values: list[str]) -> list[str]:
        """ID 列表项不得为空白；顺序按调用方给定保留，便于原样反查。"""
        for value in values:
            if not value.strip():
                raise ValueError("来源链 ID 不得为空白字符串")
        return [value.strip() for value in values]


__all__ = ["AspectRatio", "CinematographyAssetFilters", "ProducedAssetProvenance"]
