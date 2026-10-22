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

"""God-Canvas 的 System 1 Gate 节点契约与毫秒级分支求值。"""

from __future__ import annotations

import math
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

GATE_NODE_KIND = "gate"
GATE_IN_PORT_ID = "in"
_GATE_OPERATORS = {
    ">=": lambda score, threshold: score >= threshold,
    "<=": lambda score, threshold: score <= threshold,
    "==": lambda score, threshold: score == threshold,
}


class GateEvaluationError(ValueError):
    """Gate 条件、决策结果或拓扑触发链无效。"""


class GateCondition(BaseModel):
    """System 1 决策到 Gate 出口的稳定规则。"""

    model_config = ConfigDict(extra="ignore")

    decision_type: Literal["boolean", "choice", "scoring"]
    options: tuple[str, ...] = Field(default_factory=tuple)
    score_id: str | None = None
    operator: Literal[">=", "<=", "=="] = ">="
    threshold: float = 70.0

    @field_validator("options", mode="before")
    @classmethod
    def _normalize_options(cls, value: Any) -> tuple[str, ...]:
        if value is None:
            return ()
        if isinstance(value, str) or not isinstance(value, Sequence):
            raise GateEvaluationError("choice options 必须是数组")
        options: list[str] = []
        for item in value:
            option_id = str(item).strip()
            if not option_id:
                raise GateEvaluationError("choice 选项必须有非空 id")
            options.append(option_id)
        if len(options) != len(set(options)):
            raise GateEvaluationError("choice 选项 id 必须唯一")
        return tuple(options)

    @field_validator("score_id", mode="before")
    @classmethod
    def _normalize_score_id(cls, value: Any) -> str | None:
        return str(value).strip() if value is not None else None

    @field_validator("threshold")
    @classmethod
    def _validate_threshold(cls, value: float) -> float:
        if not math.isfinite(float(value)):
            raise GateEvaluationError("threshold 必须是有限数字")
        return float(value)


@dataclass(frozen=True)
class GateEvaluation:
    """一次 Gate 分支求值及其下游触发证据。"""

    branch: str
    triggered_node_ids: tuple[str, ...]
    triggered_connection_ids: tuple[str, ...]
    elapsed_ms: float
    decision: Mapping[str, Any]


def normalize_gate_condition(value: Mapping[str, Any] | GateCondition | None) -> GateCondition:
    """将拓扑节点中的 condition 字段收敛为稳定规则。"""

    if value is None:
        return GateCondition(decision_type="boolean")
    if isinstance(value, GateCondition):
        return value
    if not isinstance(value, Mapping):
        raise GateEvaluationError("condition 必须是对象")
    try:
        return GateCondition.model_validate(dict(value))
    except ValidationError as exc:
        raise GateEvaluationError(f"condition 无效: {exc}") from exc


def gate_out_ports(condition: Mapping[str, Any] | GateCondition | None) -> list[dict[str, str]]:
    """按 Gate 条件生成 true/false 或选项出口，前端与拓扑共用同一契约。"""

    normalized = normalize_gate_condition(condition)
    branches = (
        ["true", "false"] if normalized.decision_type == "boolean" else list(normalized.options)
    )
    return [{"port_id": branch, "data_type": "CONDITION"} for branch in branches]


def gate_ports(condition: Mapping[str, Any] | GateCondition | None) -> list[dict[str, str]]:
    """生成 Gate 节点的输入与全部条件输出端口。"""

    return [
        {"port_id": GATE_IN_PORT_ID, "data_type": "CONDITION"},
        *gate_out_ports(condition),
    ]


def _mapping(value: Any) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise GateEvaluationError("决策结果和拓扑元素必须是对象")
    return value


def _connection_value(connection: Any, key: str) -> Any:
    if isinstance(connection, Mapping):
        if key == "from_node":
            return connection.get("from_node", connection.get("from"))
        if key == "to_node":
            return connection.get("to_node", connection.get("to"))
        return connection.get(key)
    if key == "from_node":
        return getattr(connection, "from_node", getattr(connection, "from", None))
    if key == "to_node":
        return getattr(connection, "to_node", getattr(connection, "to", None))
    return getattr(connection, key, None)


def _resolve_branch(
    condition: GateCondition, decision: Mapping[str, Any], result: Mapping[str, Any]
) -> str:
    if decision.get("decision_type") != condition.decision_type:
        raise GateEvaluationError("decision_type 与 Gate condition 不一致")

    if condition.decision_type == "boolean":
        value = result.get("value")
        if not isinstance(value, bool):
            raise GateEvaluationError("boolean 决策必须返回布尔 value")
        return "true" if value else "false"

    if condition.decision_type == "choice":
        branch = result.get("choice_id")
        if not isinstance(branch, str) or not branch.strip():
            raise GateEvaluationError("choice 决策必须返回非空 choice_id")
        branch = branch.strip()
        if condition.options and branch not in condition.options:
            raise GateEvaluationError("choice 决策不在 Gate condition options 中")
        return branch

    scores = result.get("scores")
    if not isinstance(scores, Mapping) or not scores:
        raise GateEvaluationError("scoring 决策必须返回 scores")
    score_id = condition.score_id
    if score_id is None:
        if len(scores) != 1:
            raise GateEvaluationError("多路 scoring 决策必须指定 score_id")
        score_id = next(iter(scores))
    if score_id not in scores:
        raise GateEvaluationError("scoring 决策缺少 condition.score_id")
    try:
        score = float(scores[score_id])
    except (TypeError, ValueError, OverflowError) as exc:
        raise GateEvaluationError("scoring 分数必须是有限数字") from exc
    if not math.isfinite(score):
        raise GateEvaluationError("scoring 分数必须是有限数字")
    return "true" if _GATE_OPERATORS[condition.operator](score, condition.threshold) else "false"


def _connection_match(connection: Any, node_id: str, port_id: str | None) -> bool:
    if _connection_value(connection, "from_node") != node_id:
        return False
    from_port = _connection_value(connection, "from_port")
    if from_port in (None, ""):
        from_port = _connection_value(connection, "from_port_id")
    return port_id is None or str(from_port) == port_id


def evaluate_gate_node(
    node: Any,
    decision: Mapping[str, Any],
    connections: Sequence[Any],
    *,
    nodes: Sequence[Any] | None = None,
    started_at: float | None = None,
) -> GateEvaluation:
    """求值 Gate 分支，并沿连线递归收集需要触发的下游节点。"""

    node_map = _mapping(node)
    if node_map.get("kind") != GATE_NODE_KIND:
        raise GateEvaluationError("仅 kind=gate 节点可执行 Gate 求值")
    condition = normalize_gate_condition(node_map.get("condition"))
    protocol_version = decision.get("protocol_version")
    if protocol_version not in (None, "system-one.v1"):
        raise GateEvaluationError("决策协议版本不受支持")

    start = started_at if started_at is not None else time.perf_counter()
    result = _mapping(decision.get("result"))
    branch = _resolve_branch(condition, _mapping(decision), result)

    triggered_nodes: list[str] = []
    triggered_connections: list[str] = []
    queue: list[tuple[str, str | None]] = [(str(node_map["entity_id"]), branch)]
    seen_edges: set[tuple[str, str]] = set()
    seen_nodes = {str(node_map["entity_id"])}

    while queue:
        source_id, source_port = queue.pop(0)
        for connection in connections:
            if not _connection_match(connection, source_id, source_port):
                continue
            connection_id = str(_connection_value(connection, "connection_id"))
            target_id = str(_connection_value(connection, "to_node"))
            edge = (connection_id, target_id)
            if edge in seen_edges:
                continue
            seen_edges.add(edge)
            triggered_connections.append(connection_id)
            if target_id not in seen_nodes:
                seen_nodes.add(target_id)
                triggered_nodes.append(target_id)
            queue.append((target_id, None))

    if nodes is not None:
        known_ids = {
            str(_mapping(item).get("entity_id")) for item in nodes
        }
        unknown = set(triggered_nodes) - known_ids
        if unknown:
            raise GateEvaluationError("下游触发节点不在拓扑中")

    elapsed_ms = (time.perf_counter() - start) * 1000.0
    return GateEvaluation(
        branch=branch,
        triggered_node_ids=tuple(triggered_nodes),
        triggered_connection_ids=tuple(triggered_connections),
        elapsed_ms=elapsed_ms,
        decision=dict(decision),
    )


__all__ = [
    "GATE_IN_PORT_ID",
    "GATE_NODE_KIND",
    "GateCondition",
    "GateEvaluation",
    "GateEvaluationError",
    "evaluate_gate_node",
    "gate_out_ports",
    "gate_ports",
    "normalize_gate_condition",
]
