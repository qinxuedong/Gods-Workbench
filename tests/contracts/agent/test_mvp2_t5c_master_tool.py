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

"""QIN-462（MVP2-T5c）母带工具注册、master_output 门禁与幂等契约。"""

from __future__ import annotations

import asyncio
import json
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


class UnusedStageService:
    """占位注入；本票不调用剧本/分镜服务。"""

    async def generate_stage_candidate(self, **arguments):
        raise AssertionError("master tool must not call the stage service")


class RecordingMasterRenderService:
    """fake 母带渲染能力；真实 ffmpeg 进程与落盘路径由实现方管理。"""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.result: dict[str, Any] = {
            "job_id": "job_001",
            "output_path": "/fixture/output/master.mp4",
            "render_api_key": "fixture-secret-must-not-escape",
        }

    async def render_master(
        self,
        *,
        run_id: str,
        project_id: str,
        timeline_manifest: dict[str, Any],
        idempotency_key: str,
    ) -> dict[str, Any]:
        self.calls.append(
            {
                "run_id": run_id,
                "project_id": project_id,
                "timeline_manifest": timeline_manifest,
                "idempotency_key": idempotency_key,
            }
        )
        return dict(self.result)


@pytest.fixture()
def gate_store(tmp_path: Path) -> GateStore:
    return GateStore(
        SQLiteDatabase(tmp_path / "gates.sqlite3", trusted_root=tmp_path)
    )


def _create_gate(
    store: GateStore,
    *,
    gate_id: str = "gate-001",
    state: GateState = GateState.pending,
) -> None:
    store.create(
        gate_id=Identifier(gate_id),
        run_id=Identifier("run-001"),
        kind=GateKind.master_output,
        request_id="master-tool-test",
    )
    if state is not GateState.pending:
        store.transition(
            gate_id=Identifier(gate_id),
            target=state,
            actor_id=Identifier("user-001"),
            reason="master tool test setup",
            expected_version=0,
            request_id="master-tool-test",
        )


def _payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "run_id": "run-001",
        "project_id": "project-001",
        "timeline_manifest": {
            "tracks": [
                {
                    "track_id": "track-01",
                    "kind": "video",
                    "clips": [],
                }
            ]
        },
        "idempotency_key": "master-idem-0001",
    }
    payload.update(overrides)
    return payload


def _registered_tool(
    gate_store: GateStore | None,
) -> tuple[CapabilityRegistry, RecordingMasterRenderService]:
    registry = CapabilityRegistry()
    fake = RecordingMasterRenderService()
    services = PipelineToolServices(
        stage_candidate=UnusedStageService(),
        master_render=fake,
        gate_store=gate_store,
    )
    register_pipeline_tools(registry, services=services)
    return registry, fake


def test_master_render_is_registered_with_valid_schema_and_annotations():
    """AC1：注册 master_render，使用 Draft 2020-12 schema 与票面风险/成本。"""
    registry, _ = _registered_tool(gate_store=None)
    tool = registry.get_tool("master_render")

    Draft202012Validator.check_schema(dict(tool.input_schema))
    assert tool.risk_level is ToolRiskLevel.critical
    assert tool.cost_state is ToolCostState.free
    assert tool.idempotency_supported is True
    assert tool.input_schema["additionalProperties"] is False
    assert set(tool.input_schema["required"]) == {
        "run_id",
        "project_id",
        "timeline_manifest",
        "idempotency_key",
    }


def test_master_tool_is_not_registered_without_an_injected_capability():
    """母带渲染能力未注入时不注册空工具。"""
    registry = CapabilityRegistry()
    register_pipeline_tools(
        registry,
        services=PipelineToolServices(stage_candidate=UnusedStageService()),
    )

    with pytest.raises(DomainError) as excinfo:
        registry.get_tool("master_render")
    assert excinfo.value.code is ErrorCode.CAPABILITY_UNSUPPORTED


@pytest.mark.parametrize(
    "gate_state",
    [None, GateState.pending, GateState.changes_requested, GateState.rolled_back],
)
def test_master_tool_fails_closed_without_approved_master_output_gate(
    gate_store: GateStore, gate_state
):
    """AC2：cost 虽 free 但不可逆，master_output 门禁未批准一律拒绝且不触底层。"""
    if gate_state is not None:
        _create_gate(gate_store, state=gate_state)
    registry, fake = _registered_tool(gate_store)

    with pytest.raises(DomainError) as excinfo:
        asyncio.run(registry.get_tool("master_render").handler(_payload()))

    assert excinfo.value.code is ErrorCode.MANUAL_REVIEW_REQUIRED
    assert fake.calls == []


def test_master_tool_fails_closed_with_ambiguous_master_output_gates(
    gate_store: GateStore,
):
    """AC2：同 run 存在多条 master_output 门禁时视为未批准，fail closed。"""
    _create_gate(gate_store, gate_id="gate-001", state=GateState.approved)
    _create_gate(gate_store, gate_id="gate-002", state=GateState.pending)
    registry, fake = _registered_tool(gate_store)

    with pytest.raises(DomainError) as excinfo:
        asyncio.run(registry.get_tool("master_render").handler(_payload()))

    assert excinfo.value.code is ErrorCode.MANUAL_REVIEW_REQUIRED
    assert fake.calls == []


def test_approved_master_output_gate_calls_fake_and_sanitizes_result(
    gate_store: GateStore,
):
    """AC2：唯一 master_output 门禁 approved 后调用注入 fake，返回值脱敏。"""
    _create_gate(gate_store, state=GateState.approved)
    registry, fake = _registered_tool(gate_store)

    result = asyncio.run(registry.get_tool("master_render").handler(_payload()))

    assert len(fake.calls) == 1
    assert fake.calls[0]["run_id"] == "run-001"
    assert fake.calls[0]["project_id"] == "project-001"
    assert result["output_path"] == "<redacted-absolute-path>"
    assert "render_api_key" not in result
    assert "fixture-secret-must-not-escape" not in str(result)


def test_same_idempotency_key_triggers_one_master_render_call(gate_store: GateStore):
    """AC3：相同幂等键重复执行只触发一次底层母带渲染。"""
    _create_gate(gate_store, state=GateState.approved)
    registry, fake = _registered_tool(gate_store)
    handler = registry.get_tool("master_render").handler

    first = asyncio.run(handler(_payload()))
    second = asyncio.run(handler(_payload()))

    assert len(fake.calls) == 1
    assert first == second


def test_master_tool_never_launches_real_ffmpeg_process(
    gate_store: GateStore, monkeypatch: pytest.MonkeyPatch
):
    """AC4：真实 ffmpeg 进程被 mock 拦截；工具只经注入 fake 执行，不触网络与密钥。"""

    def _no_real_process(*_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("master tool must not launch a real ffmpeg process")

    monkeypatch.setattr("gw.media.ffmpeg_master.subprocess.run", _no_real_process)
    monkeypatch.setattr("gw.media.ffmpeg_master.render_master_mp4", _no_real_process)
    _create_gate(gate_store, state=GateState.approved)
    registry, fake = _registered_tool(gate_store)

    result = asyncio.run(registry.get_tool("master_render").handler(_payload()))

    assert len(fake.calls) == 1
    assert result["job_id"] == "job_001"


def test_master_tool_rejects_missing_context_and_unknown_fields(
    gate_store: GateStore,
):
    """缺少显式 project 上下文或传入额外字段时拒绝调用。"""
    _create_gate(gate_store, state=GateState.approved)
    registry, fake = _registered_tool(gate_store)
    handler = registry.get_tool("master_render").handler

    with pytest.raises(DomainError) as missing_context:
        asyncio.run(handler(_payload(project_id="")))
    assert missing_context.value.code is ErrorCode.VALIDATION_FAILED

    with pytest.raises(DomainError) as extra_field:
        asyncio.run(handler(_payload(unexpected="value")))
    assert extra_field.value.code is ErrorCode.VALIDATION_FAILED
    assert fake.calls == []


def test_master_tool_catalog_matches_registration():
    """接口目录记录 master_render 的注册字段与输入 schema。"""
    catalog_path = (
        Path(__file__).resolve().parents[3]
        / "docs/contracts/AGENT-WORKBENCH-INTERFACE-CATALOG.yaml"
    )
    catalog = yaml.safe_load(catalog_path.read_text(encoding="utf-8"))
    entries = {item["name"]: item for item in catalog["tools"]}
    entry = entries["master_render"]

    registry, _ = _registered_tool(gate_store=None)
    tool = registry.get_tool("master_render")
    assert entry["risk_level"] == tool.risk_level.value
    assert entry["cost_state"] == tool.cost_state.value
    assert entry["idempotency_supported"] is tool.idempotency_supported
    assert entry["input_schema"] == tool.input_schema


def test_github_issue_420_file_scope_is_registered_in_shard(repo_root: Path):
    """AC5：#420 文件集按 QIN-465 分片结构登记，新测试文件进入 accepted_test_files。"""
    shard_path = (
        repo_root / "tests/hygiene/fixtures/admissions/issue-420.json"
    )
    shard = json.loads(shard_path.read_text(encoding="utf-8"))

    assert shard["schema"] == "gw.cleanroom-admission-shard/v1"
    assert shard["issue"] == 420
    assert shard["authorization_basis"] == "GitHub Issue #420, 2026-10-29"
    assert set(shard["files"]) == {
        "gw/agent_runtime/adapters/pipeline_tools.py",
        "docs/contracts/AGENT-WORKBENCH-INTERFACE-CATALOG.yaml",
        "tests/contracts/agent/test_mvp2_t5c_master_tool.py",
        "tests/hygiene/fixtures/cleanroom_admission_metadata.json",
        "tests/hygiene/test_cleanroom_hygiene.py",
        "tests/hygiene/fixtures/admissions/issue-420.json",
    }
    accepted = {
        item["path"]: item for item in shard.get("accepted_test_files", [])
    }
    assert accepted["tests/contracts/agent/test_mvp2_t5c_master_tool.py"][
        "admission"
    ] == "accepted"
