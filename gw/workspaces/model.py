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

"""工作区与共享映射的数据契约（QIN-628）。

本模块只定义数据形状与稳定 ID 派生，不执行 IO，也不依赖任何具体存储实现。
所有对外标识均为 ASCII，符合全局命名规范。
"""

from __future__ import annotations

import hashlib
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field

STATE_NAMESPACE = "workspaces"
STATE_SCHEMA_VERSION = 1

MODE_LOCAL = "local"
MODE_NAS = "nas"

WORKSPACE_LOCAL_ID = "ws_local_01"
WORKSPACE_NAS_ID = "ws_nas_01"
LOCAL_EXECUTION_MACHINE_ID = "machine_local_01"

CAPABILITY_READ = "read"
CAPABILITY_READ_WRITE = "read_write"

VERIFICATION_VERIFIED = "verified"
VERIFICATION_UNAVAILABLE = "unavailable"
VERIFICATION_PENDING = "pending"

COMPATIBILITY_COMPATIBLE = "compatible"
COMPATIBILITY_PENDING = "pending"
COMPATIBILITY_INCOMPATIBLE = "incompatible"

LEASE_SECONDS = 300


class WorkspaceMode(str, Enum):
    """工作区模式：独立本机或连接 NAS。"""

    local = MODE_LOCAL
    nas = MODE_NAS


class MappingVerification(BaseModel):
    """映射在某执行机器上的最近一次核验结果。"""

    model_config = ConfigDict(extra="forbid")

    status: str = VERIFICATION_PENDING
    verified_at: str | None = None
    checked_capability: str = CAPABILITY_READ_WRITE
    reason: str = ""


class WorkspaceDefinition(BaseModel):
    """可用工作区的稳定定义。"""

    model_config = ConfigDict(extra="forbid")

    workspace_id: str
    mode: WorkspaceMode
    display_name: str
    data_namespace: str


class WorkspaceMapping(BaseModel):
    """一个逻辑来源在执行机器上的访问路径映射。"""

    model_config = ConfigDict(extra="forbid")

    mapping_id: str
    workspace_id: str
    logical_source_id: str
    root_identity_id: str
    execution_machine_id: str
    access_path: str
    capability: str
    verification: MappingVerification
    expected_version: int


class WriterGuard(BaseModel):
    """单写者租约，防止同一客户端并发写入多个工作区。"""

    model_config = ConfigDict(extra="forbid")

    client_instance_id: str
    lease_expires_at: str


class CompatibilityState(BaseModel):
    """QIN-626 兼容契约检查的可测试状态。"""

    model_config = ConfigDict(extra="forbid")

    status: str
    detail: str


class WorkspaceSnapshot(BaseModel):
    """工作区真值快照，同时作为 QIN-629 消费的上下文契约。"""

    model_config = ConfigDict(extra="forbid")

    schema_version: int
    revision: int
    active_workspace_id: str
    workspaces: list[WorkspaceDefinition]
    mappings: list[WorkspaceMapping]
    writer_guard: WriterGuard | None = None
    compatibility: CompatibilityState
    execution_machine_id: str = LOCAL_EXECUTION_MACHINE_ID
    result_root_label: str = ""


class WorkspaceSwitchRequest(BaseModel):
    """切换当前写入工作区；必须携带 CAS 期望版本。"""

    model_config = ConfigDict(extra="forbid")

    workspace_id: str = Field(min_length=1, max_length=128)
    expected_version: int = Field(ge=1)
    client_instance_id: str | None = Field(default=None, max_length=128)


class MappingVerifyRequest(BaseModel):
    """只探测执行端对某路径的实际读/写能力，不写入任何状态。"""

    model_config = ConfigDict(extra="forbid")

    access_path: str = Field(min_length=1, max_length=4096)
    execution_machine_id: str = Field(default=LOCAL_EXECUTION_MACHINE_ID, max_length=128)
    capability: str = Field(default=CAPABILITY_READ_WRITE, pattern="^(read|read_write)$")


class MappingCreateRequest(MappingVerifyRequest):
    """登记或刷新一条共享映射；不复制素材，也不新增来源登记。"""

    workspace_id: str | None = Field(default=None, max_length=128)
    root_identity_id: str | None = Field(default=None, max_length=128)
    expected_version: int = Field(ge=1)


def derive_logical_source_id(root_identity_id: str, canonical_relpath: str) -> str:
    """从稳定根身份与相对路径派生逻辑来源 ID，不直接暴露本地路径。"""
    payload = f"{root_identity_id}|{canonical_relpath}".encode()
    return "src_" + hashlib.sha256(payload).hexdigest()[:24]


def default_snapshot() -> dict:
    """生成未初始化的双工作区快照，默认只激活独立本机工作区。"""
    return {
        "schema_version": STATE_SCHEMA_VERSION,
        "revision": 1,
        "active_workspace_id": WORKSPACE_LOCAL_ID,
        "workspaces": [
            {
                "workspace_id": WORKSPACE_LOCAL_ID,
                "mode": MODE_LOCAL,
                "display_name": "独立本机工作区",
                "data_namespace": "workspace_local_01",
            },
            {
                "workspace_id": WORKSPACE_NAS_ID,
                "mode": MODE_NAS,
                "display_name": "连接 NAS 工作区",
                "data_namespace": "workspace_nas_01",
            },
        ],
        "mappings": [],
        "writer_guard": None,
        "compatibility": {
            "status": COMPATIBILITY_PENDING,
            "detail": "QIN-626 兼容契约尚未落地，连接 NAS 工作区暂不可用",
        },
    }
