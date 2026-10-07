"""统一工作流领域包。

仅承载可在 Gods-Workbench 运行时内验证的工作流模型、解析与外部客户端边界。
外部服务调用不在此处伪造；未配置或未准入能力会明确返回不可用状态。
"""

from .models import WorkflowDocument, WorkflowNode, WorkflowConnection, WorkflowSource
from .parser import WorkflowParseError, parse_workflow

__all__ = [
    "WorkflowConnection",
    "WorkflowDocument",
    "WorkflowNode",
    "WorkflowParseError",
    "WorkflowSource",
    "parse_workflow",
]
