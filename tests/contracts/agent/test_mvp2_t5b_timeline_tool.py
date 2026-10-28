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

"""MVP2-T5b 时间线工具注册、付费门禁与幂等契约（issue #419）。"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest
import yaml
from jsonschema import Draft202012Validator

from gw.agent_episode.gates import GateState
from gw.agent_runtime.adapters.pipeline_tools import (
    PipelineToolServices,
    register_pipeline_tools,
)
from gw.agent_runtime.errors import DomainError
from gw.agent_runtime.models import ErrorCode, ToolCostState, ToolRiskLevel
from gw.agent_runtime.policy import CapabilityRegistry


class UnusedStageService:
    """占位注入；本票不调用剧本/分镜服务。"""

    async def generate_stage_candidate(self, **arguments):
        raise AssertionError("timeline tool must not call the stage service")


class RecordingTimelineService:
    """fake 时间线能力，用于确认工具只经注入服务执行。"""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.result: dict[str, Any] = {
            "pipeline_id": "pipeline-001",
            "timeline_manifest": {"tracks": [{"track_id": "track-01", "clips": []}]},
            "output_path": "/fixture/output/timeline.json",
            "provider_token": "fixture-secret-must-not-escape",
        }

    async def compose_timeline(
        self,
        *,
        run_id: str,
        project_id: str,
        pipeline_id: str,
        timeline_manifest: dict[str, Any],
        idempotency_key: str,
    ) -> dict[str, Any]:
        self.calls.append(
            {
                "run_id": run_id,
                "project_id": project_id,
                "pipeline_id": pipeline_id,
                "timeline_manifest": timeline_manifest,
                "idempotency_key": idempotency_key,
            }
        )
        return dict(self.result)


def _payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "run_id": "run-001",
        "project_id": "project-001",
        "pipeline_id": "pipeline-001",
        "timeline_manifest": {
            "tracks": [
                {
                    "track_id": "track-01",
                    "kind": "video",
                    "clips": [],
                }
            ]
        },
        "idempotency_key": "timeline-idem-0001",
    }
    payload.update(overrides)
    return payload


def _registered_tool(
    *,
    cost_state: ToolCostState = ToolCostState.free,
    gate_lookup=None,
) -> tuple[CapabilityRegistry, RecordingTimelineService]:
    registry = CapabilityRegistry()
    fake = RecordingTimelineService()
    services = PipelineToolServices(
        stage_candidate=UnusedStageService(),
        timeline_compose=fake,
        timeline_cost_state=cost_state,
    )
    register_pipeline_tools(registry, services=services, gate_lookup=gate_lookup)
    return registry, fake


def test_timeline_compose_is_registered_with_valid_schema_and_annotations():
    """AC1：注册时间线工具，使用 Draft 2020-12 schema 与票面风险/成本。"""
    registry, _ = _registered_tool()
    tool = registry.get_tool("timeline_compose")

    Draft202012Validator.check_schema(dict(tool.input_schema))
    assert tool.risk_level is ToolRiskLevel.medium
    assert tool.cost_state is ToolCostState.free
    assert tool.idempotency_supported is True
    assert tool.input_schema["additionalProperties"] is False
    assert set(tool.input_schema["required"]) == {
        "run_id",
        "project_id",
        "pipeline_id",
        "timeline_manifest",
        "idempotency_key",
    }


def test_timeline_tool_is_not_registered_without_an_injected_capability():
    """没有既有时间线能力注入时不注册空工具。"""
    registry = CapabilityRegistry()
    register_pipeline_tools(
        registry,
        services=PipelineToolServices(stage_candidate=UnusedStageService()),
    )

    with pytest.raises(DomainError) as excinfo:
        registry.get_tool("timeline_compose")
    assert excinfo.value.code is ErrorCode.CAPABILITY_UNSUPPORTED


def test_free_timeline_tool_calls_only_fake_and_sanitizes_result():
    """AC2/AC4：free 不查询门禁，只调用注入 fake，且返回值无绝对路径或凭据。"""
    gate_lookups: list[str] = []
    registry, fake = _registered_tool(
        gate_lookup=lambda run_id: gate_lookups.append(run_id) or GateState.pending
    )
    handler = registry.get_tool("timeline_compose").handler

    result = asyncio.run(handler(_payload()))

    assert fake.calls == [_payload()]
    assert gate_lookups == []
    assert result["output_path"] == "<redacted-absolute-path>"
    assert "provider_token" not in result
    assert "fixture-secret-must-not-escape" not in str(result)


@pytest.mark.parametrize(
    "gate_state", [None, GateState.pending, GateState.changes_requested, GateState.rolled_back]
)
def test_non_free_timeline_tool_fails_closed_without_approved_gate(gate_state):
    """AC2：metered/unknown 成本未批准时拒绝，且不触发底层 fake。"""
    registry, fake = _registered_tool(
        cost_state=ToolCostState.unknown,
        gate_lookup=lambda _run_id: gate_state,
    )

    with pytest.raises(DomainError) as excinfo:
        asyncio.run(registry.get_tool("timeline_compose").handler(_payload()))

    assert excinfo.value.code is ErrorCode.MANUAL_REVIEW_REQUIRED
    assert fake.calls == []


def test_approved_non_free_timeline_tool_calls_fake():
    """AC2：unknown 成本在门禁批准后才调用注入 fake。"""
    registry, fake = _registered_tool(
        cost_state=ToolCostState.unknown,
        gate_lookup=lambda _run_id: GateState.approved,
    )

    result = asyncio.run(registry.get_tool("timeline_compose").handler(_payload()))

    assert len(fake.calls) == 1
    assert result["pipeline_id"] == "pipeline-001"


def test_same_idempotency_key_triggers_one_timeline_compose_call():
    """AC3：相同幂等键重复执行只触发一次底层时间线能力。"""
    registry, fake = _registered_tool()
    handler = registry.get_tool("timeline_compose").handler

    first = asyncio.run(handler(_payload()))
    second = asyncio.run(handler(_payload()))

    assert len(fake.calls) == 1
    assert first == second


def test_timeline_tool_rejects_missing_context_and_unknown_fields():
    """缺少显式 project/pipeline 上下文或传入额外字段时拒绝调用。"""
    registry, fake = _registered_tool()
    handler = registry.get_tool("timeline_compose").handler

    with pytest.raises(DomainError) as missing_context:
        asyncio.run(handler(_payload(project_id="")))
    assert missing_context.value.code is ErrorCode.VALIDATION_FAILED

    with pytest.raises(DomainError) as extra_field:
        asyncio.run(handler(_payload(unexpected="value")))
    assert extra_field.value.code is ErrorCode.VALIDATION_FAILED
    assert fake.calls == []


def test_timeline_tool_catalog_matches_registration():
    """接口目录记录 timeline_compose 的注册字段与输入 schema。"""
    catalog_path = (
        Path(__file__).resolve().parents[3]
        / "docs/contracts/AGENT-WORKBENCH-INTERFACE-CATALOG.yaml"
    )
    catalog = yaml.safe_load(catalog_path.read_text(encoding="utf-8"))
    entries = {item["name"]: item for item in catalog["tools"]}
    entry = entries["timeline_compose"]

    registry, _ = _registered_tool()
    tool = registry.get_tool("timeline_compose")
    assert entry["risk_level"] == tool.risk_level.value
    assert entry["cost_state"] == tool.cost_state.value
    assert entry["idempotency_supported"] is tool.idempotency_supported
    assert entry["input_schema"] == tool.input_schema


def test_github_issue_419_file_scope_and_test_hash_are_registered(repo_root: Path):
    """AC5：#419 文件集合与新增测试 SHA 均登记在 cleanroom metadata 中。"""
    metadata_path = repo_root / "tests/hygiene/fixtures/cleanroom_admission_metadata.json"
    metadata = yaml.safe_load(metadata_path.read_text(encoding="utf-8"))
    registrations = [
        item
        for item in metadata.get("authorized_issue_file_sets", [])
        if item.get("issue") == 419
    ]

    assert len(registrations) == 1
    assert registrations[0]["authorization_basis"] == "GitHub Issue #419, 2026-10-28"
    assert set(registrations[0]["files"]) == {
        "gw/agent_runtime/adapters/pipeline_tools.py",
        "docs/contracts/AGENT-WORKBENCH-INTERFACE-CATALOG.yaml",
        "tests/contracts/agent/test_mvp2_t5b_timeline_tool.py",
        "tests/hygiene/fixtures/cleanroom_admission_metadata.json",
        "tests/hygiene/test_cleanroom_hygiene.py",
    }

    test_path = "tests/contracts/agent/test_mvp2_t5b_timeline_tool.py"
    entries = [
        item
        for item in metadata.get("authorized_test_files", [])
        if item.get("path") == test_path
    ]
    assert len(entries) == 1
    assert entries[0]["authorization_basis"] == "QIN-461_Issue_419_2026-10-28"
    assert entries[0]["sha256"] == _canonical_sha256(repo_root / test_path)


def _canonical_sha256(path: Path) -> str:
    import hashlib

    contents = path.read_bytes().replace(b"\r\n", b"\n").replace(b"\r", b"\n")
    return hashlib.sha256(contents).hexdigest()
