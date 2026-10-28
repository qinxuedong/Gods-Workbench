# -*- coding: utf-8 -*-
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

"""QIN-460（MVP2-T5a）I2V 工具注册、付费门禁与幂等契约。"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest
import yaml
from jsonschema import Draft202012Validator

from gw.agent_episode.gates import GateKind, GateState, GateStore
from gw.agent_runtime.adapters.pipeline_tools import (
    PipelineToolServices,
    register_pipeline_tools,
)
from gw.agent_runtime.errors import DomainError
from gw.agent_runtime.models import ErrorCode, Identifier, ToolCostState, ToolRiskLevel
from gw.agent_runtime.policy import CapabilityRegistry
from gw.agent_runtime.storage.sqlite import SQLiteDatabase


class RecordingStageService:
    async def generate_stage_candidate(self, **kwargs: Any) -> dict[str, Any]:
        return dict(kwargs)


class RecordingVideoTaskService:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def create_generation(
        self, payload: dict[str, Any], key_value: str | None, context: Any
    ) -> dict[str, Any]:
        self.calls.append(
            {"payload": payload, "key_value": key_value, "context": context}
        )
        return {
            "job_id": "job_001",
            "artifact_path": "/<asset-path>/generated.mp4",
            "provider_api_key": "fake-key-marker",
        }


@pytest.fixture
def registry() -> CapabilityRegistry:
    return CapabilityRegistry()


@pytest.fixture
def gate_store(tmp_path: Path) -> GateStore:
    return GateStore(
        SQLiteDatabase(tmp_path / "gates.sqlite3", trusted_root=tmp_path)
    )


def _payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "run_id": "run-001",
        "idempotency_key": "idem-0001",
        "provider_id": "provider-test",
        "model": "video-model-test",
        "prompt": "A short test scene.",
        "duration": 5,
        "aspect_ratio": "16:9",
        "production_context": {
            "project_id": "project-001",
            "canvas_id": "canvas-001",
            "entity_id": "entity-001",
        },
    }
    payload.update(overrides)
    return payload


def _create_gate(
    store: GateStore, *, gate_id: str = "gate-001", state: GateState = GateState.pending
) -> None:
    record = store.create(
        gate_id=Identifier(gate_id),
        run_id=Identifier("run-001"),
        kind=GateKind.i2v_submit,
        request_id="create-gate",
    )
    if state is GateState.approved:
        store.transition(
            gate_id=record.gate_id,
            target=GateState.approved,
            actor_id=Identifier("user-001"),
            reason="批准 I2V 提交。",
            expected_version=0,
            request_id="approve-gate",
        )


def _register_i2v(
    registry: CapabilityRegistry,
    *,
    gate_store: GateStore | None,
    video_service: RecordingVideoTaskService | None = None,
    context: Any | None = None,
) -> RecordingVideoTaskService | None:
    service = video_service or RecordingVideoTaskService()
    register_pipeline_tools(
        registry,
        services=PipelineToolServices(
            stage_candidate=RecordingStageService(),
            video_task=service,
            gate_store=gate_store,
            video_context=context if context is not None else object(),
        ),
    )
    return service


def test_i2v_tool_is_registered_with_schema_risk_cost_and_permission(registry):
    _register_i2v(registry, gate_store=None)

    tool = registry.get_tool("i2v_generate")
    Draft202012Validator.check_schema(dict(tool.input_schema))
    assert Draft202012Validator(dict(tool.input_schema)).is_valid(_payload())
    assert tool.input_schema["additionalProperties"] is False
    assert tool.risk_level is ToolRiskLevel.high
    assert tool.cost_state is ToolCostState.metered
    assert tool.idempotency_supported is True
    assert tool.required_permissions == (Identifier("video.generate"),)


def test_i2v_requires_a_unique_approved_gate_before_calling_service(
    registry, gate_store
):
    service = _register_i2v(registry, gate_store=gate_store)
    _create_gate(gate_store)

    with pytest.raises(DomainError) as excinfo:
        asyncio.run(registry.get_tool("i2v_generate").handler(_payload()))

    assert excinfo.value.code is ErrorCode.MANUAL_REVIEW_REQUIRED
    assert service is not None
    assert service.calls == []


def test_missing_or_ambiguous_i2v_gate_fails_closed(registry, gate_store):
    service = _register_i2v(registry, gate_store=gate_store)
    _create_gate(gate_store, gate_id="gate-001", state=GateState.approved)
    _create_gate(gate_store, gate_id="gate-002")

    with pytest.raises(DomainError) as excinfo:
        asyncio.run(registry.get_tool("i2v_generate").handler(_payload()))

    assert excinfo.value.code is ErrorCode.MANUAL_REVIEW_REQUIRED
    assert service is not None
    assert service.calls == []


def test_approved_gate_calls_only_injected_service_and_sanitizes_result(
    registry, gate_store
):
    context = object()
    service = RecordingVideoTaskService()
    _register_i2v(
        registry,
        gate_store=gate_store,
        video_service=service,
        context=context,
    )
    _create_gate(gate_store, state=GateState.approved)

    result = asyncio.run(registry.get_tool("i2v_generate").handler(_payload()))

    assert service.calls == [
        {
            "payload": {
                "provider_id": "provider-test",
                "model": "video-model-test",
                "prompt": "A short test scene.",
                "duration": 5,
                "aspect_ratio": "16:9",
                "production_context": {
                    "project_id": "project-001",
                    "canvas_id": "canvas-001",
                    "entity_id": "entity-001",
                },
            },
            "key_value": "idem-0001",
            "context": context,
        }
    ]
    assert result["job_id"] == "job_001"
    assert result["artifact_path"] == "<redacted-absolute-path>"
    assert "provider_api_key" not in result
    assert "fake-key-marker" not in str(result)


def test_same_run_and_idempotency_key_calls_injected_service_once(
    registry, gate_store
):
    service = RecordingVideoTaskService()
    _register_i2v(registry, gate_store=gate_store, video_service=service)
    _create_gate(gate_store, state=GateState.approved)
    handler = registry.get_tool("i2v_generate").handler

    first = asyncio.run(handler(_payload()))
    second = asyncio.run(handler(_payload()))

    assert len(service.calls) == 1
    assert first == second


def test_i2v_schema_rejects_credentials_and_unknown_fields(registry, gate_store):
    service = _register_i2v(registry, gate_store=gate_store)
    _create_gate(gate_store, state=GateState.approved)

    with pytest.raises(DomainError) as excinfo:
        asyncio.run(
            registry.get_tool("i2v_generate").handler(
                _payload(api_key="must-not-be-accepted")
            )
        )

    assert excinfo.value.code is ErrorCode.VALIDATION_FAILED
    assert service is not None
    assert service.calls == []


def test_i2v_tool_is_documented_in_interface_catalog(registry):
    _register_i2v(registry, gate_store=None)
    tool = registry.get_tool("i2v_generate")
    catalog_path = (
        Path(__file__).resolve().parents[3]
        / "docs/contracts/AGENT-WORKBENCH-INTERFACE-CATALOG.yaml"
    )
    catalog = yaml.safe_load(catalog_path.read_text(encoding="utf-8"))
    entries = {item["name"]: item for item in catalog["tools"]}
    entry = entries["i2v_generate"]

    assert entry["risk_level"] == "high"
    assert entry["cost_state"] == "metered"
    assert entry["idempotency_supported"] is True
    assert entry["required_permissions"] == ["video.generate"]
    assert entry["input_schema"] == dict(tool.input_schema)
