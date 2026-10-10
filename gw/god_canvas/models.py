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

"""画布核心领域与契约模型。

严格对齐 docs/contracts/CANVAS-INTERFACE-CATALOG.yaml 与 docs/fixtures 夹具规范。
"""

from enum import Enum
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, ConfigDict, Field, model_validator


class CanvasMode(str, Enum):
    """画布运行模式。"""

    CLASSIC = "classic"
    SMART = "smart"


class NodePosition(BaseModel):
    """节点在画布上的坐标位置。"""

    x: float
    y: float


class NodeReferences(BaseModel):
    """节点引用的外部实体。"""

    model_config = ConfigDict(extra="allow")

    asset_id: Optional[str] = None
    job_id: Optional[str] = None


class CanvasNode(BaseModel):
    """画布拓扑节点实体。"""

    model_config = ConfigDict(extra="allow")

    entity_id: str = Field(..., description="稳定节点标识")
    kind: str = Field(..., description="节点类型，例如 input, output, process")
    position: Optional[NodePosition] = Field(None, description="节点坐标")
    refs: Optional[NodeReferences] = Field(default_factory=NodeReferences, description="节点关联引用")


class CanvasConnection(BaseModel):
    """画布拓扑连线实体。"""

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    connection_id: str = Field(..., description="稳定连线标识")
    from_node: str = Field(..., alias="from", description="源节点 entity_id")
    to_node: str = Field(..., alias="to", description="目标节点 entity_id")


class CanvasTopology(BaseModel):
    """画布完整拓扑结构（对齐 canvas-workflow-minimal.json 夹具）。"""

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    canvas_id: str = Field(..., description="画布标识")
    version: int = Field(1, description="CAS 版本号")
    nodes: List[CanvasNode] = Field(default_factory=list, description="节点列表")
    connections: List[CanvasConnection] = Field(default_factory=list, description="连接列表")
    references: Optional[Dict[str, Any]] = Field(None, description="画布级别全局引用")

    @model_validator(mode="after")
    def validate_graph(self):
        """验证节点/连线稳定 ID 唯一且连线端点存在。"""
        node_ids = [node.entity_id for node in self.nodes]
        if len(node_ids) != len(set(node_ids)):
            raise ValueError("节点 entity_id 必须唯一")
        connection_ids = [connection.connection_id for connection in self.connections]
        if len(connection_ids) != len(set(connection_ids)):
            raise ValueError("连线 connection_id 必须唯一")
        known_nodes = set(node_ids)
        for connection in self.connections:
            if connection.from_node not in known_nodes or connection.to_node not in known_nodes:
                raise ValueError("连线端点必须引用现有节点 entity_id")
        return self


class CanvasItem(BaseModel):
    """画布元信息项。"""

    model_config = ConfigDict(extra="ignore")

    canvas_id: str
    title: str
    project_id: str
    version: int
    mode: CanvasMode


class CanvasListResponse(BaseModel):
    """画布列表响应体。"""

    canvases: List[CanvasItem] = Field(default_factory=list)


class CanvasCreateRequest(BaseModel):
    """创建画布请求体。"""

    model_config = ConfigDict(extra="ignore")

    project_id: str = Field(default="default", description="所属项目 ID")
    title: str = Field(default="未命名智能画布", min_length=1, description="画布标题")
    mode: CanvasMode = Field(default=CanvasMode.SMART, description="画布模式")
    initial_payload: Optional[Dict[str, Any]] = Field(None, description="可选初始拓扑")

    @model_validator(mode="before")
    @classmethod
    def normalize_create_payload(cls, data: Any) -> Any:
        if isinstance(data, dict):
            pid = data.get("project_id") or data.get("project") or "default"
            raw_title = str(data.get("title") or "").strip()
            title = raw_title if raw_title else "未命名智能画布"
            raw_mode = data.get("mode") or data.get("kind") or "smart"
            mode = "smart" if str(raw_mode).lower() in {"smart", "classic"} else raw_mode
            data["project_id"] = pid
            data["title"] = title
            data["mode"] = mode
        return data


class CanvasMutationResult(BaseModel):
    """画布创建/保存返回结构。"""

    canvas_id: str
    version: int


class CanvasMutationResponse(BaseModel):
    """画布变更统一外层包装。"""

    canvas: CanvasMutationResult


class CanvasTopologyUpdateRequest(BaseModel):
    """更新画布拓扑请求体（带 CAS expected_version）。"""

    model_config = ConfigDict(extra="forbid")

    expected_version: int = Field(..., description="期望 CAS 版本")
    nodes: List[CanvasNode] = Field(default_factory=list, description="最新节点集合")
    connections: List[CanvasConnection] = Field(default_factory=list, description="最新连线集合")
    references: Optional[Dict[str, Any]] = Field(None, description="画布级别全局引用")

    @model_validator(mode="after")
    def validate_graph(self):
        """验证更新载荷不会写入悬挂连线或重复 ID。"""
        node_ids = [node.entity_id for node in self.nodes]
        if len(node_ids) != len(set(node_ids)):
            raise ValueError("节点 entity_id 必须唯一")
        connection_ids = [connection.connection_id for connection in self.connections]
        if len(connection_ids) != len(set(connection_ids)):
            raise ValueError("连线 connection_id 必须唯一")
        known_nodes = set(node_ids)
        for connection in self.connections:
            if connection.from_node not in known_nodes or connection.to_node not in known_nodes:
                raise ValueError("连线端点必须引用现有节点 entity_id")
        return self


class CanvasImportReport(BaseModel):
    """工作流导入结果报告。"""

    nodes_imported: int
    connections_imported: int
    warnings: List[str] = Field(default_factory=list)


class CanvasImportResponse(BaseModel):
    """工作流导入成功响应体。"""

    canvas: CanvasMutationResult
    import_report: CanvasImportReport


class CanvasExportRequest(BaseModel):
    """工作流导出请求。"""

    model_config = ConfigDict(extra="forbid")

    format: str = Field("json", description="导出格式：json | godmap")
    include_resources: bool = Field(False, description="是否内嵌资源描述")


class CanvasExportResponse(BaseModel):
    """历史文件 URL 响应模型；当前最小路由直接返回原始内容。"""

    file_url: str
    content_type: str
