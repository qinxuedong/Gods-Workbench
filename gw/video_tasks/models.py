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

"""视频任务模型定义。"""

from typing import Literal
from pydantic import BaseModel, ConfigDict, Field


class ProductionContext(BaseModel):
    model_config = ConfigDict(extra="forbid")
    project_id: str = Field(..., min_length=1, max_length=128)
    canvas_id: str = Field(..., min_length=1, max_length=128)
    entity_id: str = Field(..., min_length=1, max_length=128)


class VideoGenerationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    prompt: str = Field(..., min_length=1, max_length=20_000)
    provider_id: str = Field(..., min_length=1, max_length=128)
    model: str = Field(..., min_length=1, max_length=256)
    duration: float = Field(..., gt=0, le=120)
    aspect_ratio: Literal["16:9", "9:16", "1:1"]
    production_context: ProductionContext


class VideoExportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    asset_ids: list[str] = Field(..., min_length=1, max_length=8)
    project_id: str = Field(..., min_length=1, max_length=128)
    canvas_id: str = Field(..., min_length=1, max_length=128)
    entity_id: str = Field(..., min_length=1, max_length=128)
    preset: Literal["h264_720p_30fps", "h264_1080p_30fps"] = "h264_720p_30fps"


class ProjectAssetAuthorizationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    asset_id: str = Field(..., min_length=1, max_length=128)


class VideoProjectClaimRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    project_id: str = Field(..., min_length=1, max_length=128)
