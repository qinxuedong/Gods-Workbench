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

"""C17-T3：Gate 节点拓扑 CAS、System 1 分支求值与下游触发契约。"""

import asyncio
from concurrent.futures import ThreadPoolExecutor

import pytest

from gw.core.errors import CleanroomException
from gw.decision.router import DecisionRouter, LocalDecisionBackend
from gw.god_canvas.gate_node import (
    GATE_NODE_KIND,
    GateCondition,
    GateEvaluationError,
    evaluate_gate_node,
    gate_out_ports,
    normalize_gate_condition,
)
from gw.god_canvas.models import CanvasTopologyUpdateRequest
from gw.god_canvas.service import GodCanvasService


def _decision(payload: dict):
    return asyncio.run(DecisionRouter(LocalDecisionBackend()).decide(payload))


def _install_gate_topology(service: GodCanvasService, condition: dict | None = None):
    gate_condition = condition or {"decision_type": "boolean"}
    nodes = [
        {
            "entity_id": "system-entry",
            "kind": "text_prompt",
            "position": {"x": 40, "y": 80},
            "ports": [{"port_id": "out", "data_type": "TEXT"}],
        },
        {
            "entity_id": "semantic-gate",
            "kind": GATE_NODE_KIND,
            "title": "Gate / Semantic Branch",
            "position": {"x": 260, "y": 80},
            "condition": gate_condition,
            "ports": [
                {"port_id": "in", "data_type": "TEXT"},
                {"port_id": "true", "data_type": "TEXT"},
                {"port_id": "false", "data_type": "TEXT"},
            ],
        },
        {
            "entity_id": "cinematic-node",
            "kind": "shot_frame",
            "position": {"x": 500, "y": 40},
            "ports": [{"port_id": "in", "data_type": "TEXT"}],
        },
        {
            "entity_id": "memo-node",
            "kind": "text_memo",
            "position": {"x": 500, "y": 160},
            "ports": [{"port_id": "in", "data_type": "TEXT"}],
        },
    ]
    connections = [
        {"connection_id": "wire-entry", "from": "system-entry", "to": "semantic-gate"},
        {
            "connection_id": "wire-true",
            "from": "semantic-gate",
            "to": "cinematic-node",
            "from_port": (
                "cinematic"
                if condition and condition.get("decision_type") == "choice"
                else "true"
            ),
        },
        {
            "connection_id": "wire-false",
            "from": "semantic-gate",
            "to": "memo-node",
            "from_port": "false",
        },
    ]
    result = service.update_topology(
        "cv-0001",
        CanvasTopologyUpdateRequest(
            expected_version=1, nodes=nodes, connections=connections
        ),
    )
    return result.version, nodes, connections


@pytest.fixture
def canvas_service():
    return GodCanvasService(seed_golden_fixture=True)


def test_gate_topology_persists_with_canonical_ports_and_cas(canvas_service):
    version, nodes, _ = _install_gate_topology(canvas_service)

    assert version == 2
    topology = canvas_service.get_topology("cv-0001")
    gate = topology.nodes[1]
    assert gate.kind == GATE_NODE_KIND
    assert gate.condition == {"decision_type": "boolean"}
    assert [port.port_id for port in gate.ports or []] == ["in", "true", "false"]
    assert {node.entity_id for node in topology.nodes} == {
        node["entity_id"] for node in nodes
    }


def test_gate_topology_rejects_stale_cas_version(canvas_service):
    _install_gate_topology(canvas_service)

    with pytest.raises(CleanroomException) as conflict:
        canvas_service.update_topology(
            "cv-0001",
            CanvasTopologyUpdateRequest(expected_version=1, nodes=[], connections=[]),
        )

    assert conflict.value.status_code == 409
    assert conflict.value.code == "CANVAS_VERSION_CONFLICT"
    assert canvas_service.get_topology("cv-0001").version == 2


def test_concurrent_gate_topology_updates_allow_only_one_writer(canvas_service):
    _install_gate_topology(canvas_service)

    def update(_: int) -> object:
        try:
            return canvas_service.update_topology(
                "cv-0001",
                CanvasTopologyUpdateRequest(
                    expected_version=2,
                    nodes=canvas_service.get_topology("cv-0001").nodes,
                    connections=canvas_service.get_topology("cv-0001").connections,
                ),
            ).version
        except CleanroomException as exc:
            return exc.status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(update, range(2))) == [3, 409]


def test_boolean_gate_routes_true_branch_within_20ms(canvas_service):
    _, nodes, connections = _install_gate_topology(canvas_service)
    decision = _decision({"decision_type": "boolean", "value": True})

    evaluation = evaluate_gate_node(
        nodes[1], decision, connections, nodes=nodes, started_at=None
    )

    assert evaluation.branch == "true"
    assert evaluation.triggered_node_ids == ("cinematic-node",)
    assert evaluation.triggered_connection_ids == ("wire-true",)
    assert 0 <= evaluation.elapsed_ms < 20


def test_boolean_gate_routes_false_branch(canvas_service):
    _, nodes, connections = _install_gate_topology(canvas_service)
    decision = _decision({"decision_type": "boolean", "value": False})

    evaluation = evaluate_gate_node(nodes[1], decision, connections, nodes=nodes)

    assert evaluation.branch == "false"
    assert evaluation.triggered_node_ids == ("memo-node",)


def test_choice_gate_routes_highest_probability_option(canvas_service):
    _, nodes, connections = _install_gate_topology(
        canvas_service,
        {"decision_type": "choice", "options": ["cinematic", "memo"]},
    )
    decision = _decision(
        {
            "decision_type": "choice",
            "options": ["cinematic", "memo"],
            "probabilities": {"memo": 0.2, "cinematic": 0.8},
        }
    )

    evaluation = evaluate_gate_node(nodes[1], decision, connections, nodes=nodes)

    assert evaluation.branch == "cinematic"
    assert evaluation.triggered_node_ids == ("cinematic-node",)


def test_scoring_gate_applies_fuzzy_threshold(canvas_service):
    condition = {
        "decision_type": "scoring",
        "score_id": "intent",
        "operator": ">=",
        "threshold": 70,
    }
    _, nodes, connections = _install_gate_topology(canvas_service, condition)
    decision = _decision(
        {"decision_type": "scoring", "scores": {"intent": 82.5}}
    )

    evaluation = evaluate_gate_node(nodes[1], decision, connections, nodes=nodes)

    assert evaluation.branch == "true"
    assert evaluation.triggered_node_ids == ("cinematic-node",)


def test_gate_condition_builds_stable_output_ports():
    boolean_ports = gate_out_ports(GateCondition(decision_type="boolean"))
    choice_ports = gate_out_ports(
        GateCondition(decision_type="choice", options=("cinematic", "memo"))
    )

    assert [port["port_id"] for port in boolean_ports] == ["true", "false"]
    assert [port["port_id"] for port in choice_ports] == ["cinematic", "memo"]


def test_gate_evaluation_rejects_incompatible_system_one_result(canvas_service):
    _, nodes, connections = _install_gate_topology(canvas_service)
    decision = _decision({"decision_type": "boolean", "value": True})

    with pytest.raises(GateEvaluationError, match="decision_type"):
        evaluate_gate_node(nodes[1], decision | {"decision_type": "choice"}, connections)


def test_gate_condition_normalization_rejects_unknown_rule():
    with pytest.raises(GateEvaluationError, match="operator"):
        normalize_gate_condition(
            {"decision_type": "scoring", "operator": "approximately", "threshold": 1}
        )
