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

"""智能画布任务编排与状态模型。

对齐 run_smart_canvas_task 接口契约（仅 202 Accepted）与 202 黄金夹具；
受理响应必须携带稳定 job_id 与 poll_hint，终态查询时 poll_hint 清空为 None。
"""

from enum import Enum
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, ConfigDict, Field


class SmartCanvasRunMode(str, Enum):
    """智能画布任务执行模式。"""

    SINGLE = "single"
    CASCADE = "cascade"


class SmartCanvasTaskRequest(BaseModel):
    """发起智能画布任务请求体。"""

    model_config = ConfigDict(extra="forbid")

    expected_version: Optional[int] = Field(None, description="当前期望 CAS 版本")
    entry_nodes: List[str] = Field(..., min_length=1, description="入口节点 entity_id 集合")
    run_mode: SmartCanvasRunMode = Field(default=SmartCanvasRunMode.SINGLE, description="执行模式")
    inputs: Dict[str, Any] = Field(default_factory=dict, description="执行输入载荷")


class TaskStatus(str, Enum):
    """任务状态。"""

    ACCEPTED = "accepted"
    RUNNING = "running"
    WAITING_REVIEW = "waiting_review"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class SmartCanvasTaskResponse(BaseModel):
    """智能画布任务返回结构（对齐 202 Accepted 契约）。

202 受理响应必须携带 poll_hint；终态查询（completed/failed/cancelled）时 poll_hint 清空。
"""

    model_config = ConfigDict(extra="allow")

    job_id: str = Field(..., description="异步任务稳定标识")
    state: str = Field(..., description="任务状态")
    poll_hint: Optional[str] = Field(None, description="任务轮询或状态查询路径；202 受理时必填，终态清空")
    result: Optional[Dict[str, Any]] = Field(None, description="任务完成结果")
    error: Optional[str] = Field(None, description="任务失败原因")
