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

"""MVP2-T4 前段工具注册契约：注册、幂等、注入调用与返回值清洗（issue #368）。"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest
import yaml

from gw.agent_runtime.adapters.pipeline_tools import (
    PipelineToolServices,
    is_absolute_path_string,
    register_pipeline_tools,
    sanitize_tool_result,
)
from gw.agent_runtime.errors import DomainError
from gw.agent_runtime.models import ErrorCode, ToolCostState, ToolRiskLevel
from gw.agent_runtime.policy import CapabilityRegistry

# 伪造的服务返回值标记（仅用于断言清洗器剔除了凭据类字段，非真实凭据）。
_FAKE_KEY_VALUE = "sanitizer-marker-value-should-not-appear"
_FAKE_AUTH_VALUE = "test-auth-header-value-should-not-appear"


class RecordingStageService:
    """fake 注入服务：记录调用参数并返回带绝对路径与凭据字段的原始结果。"""

    def __init__(self, result: dict[str, Any] | None = None) -> None:
        self.calls: list[dict[str, Any]] = []
        self.result = result or {
            "stage": "full_script",
            "artifact_id": "art_001",
            "content_path": "/home/user/secret/output.md",
            "provider_api_key": _FAKE_KEY_VALUE,
        }

    async def generate_stage_candidate(
        self,
        *,
        run_id: str,
        stage: str,
        idempotency_key: str,
        instructions: str | None,
    ) -> dict[str, Any]:
        self.calls.append(
            {
                "run_id": run_id,
                "stage": stage,
                "idempotency_key": idempotency_key,
                "instructions": instructions,
            }
        )
        return dict(self.result)


@pytest.fixture
def registry() -> CapabilityRegistry:
    return CapabilityRegistry()


def _services(*result: dict[str, Any]) -> tuple[PipelineToolServices, RecordingStageService]:
    service = RecordingStageService(*result)
    return PipelineToolServices(stage_candidate=service), service


def _payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "run_id": "run-001",
        "stage": "full_script",
        "idempotency_key": "idem-0001",
    }
    payload.update(overrides)
    return payload


def test_register_pipeline_tools_registers_script_and_storyboard_with_annotations(
    registry,
):
    """AC1：工具按票面注册且 schema 通过 Draft 2020-12 校验；标注与票面一致。"""
    services, _ = _services()
    register_pipeline_tools(registry, services=services)

    script = registry.get_tool("script_generate")
    storyboard = registry.get_tool("storyboard_generate")
    assert script.risk_level is ToolRiskLevel.medium
    assert script.cost_state is ToolCostState.metered
    assert script.idempotency_supported is True
    assert storyboard.risk_level is ToolRiskLevel.medium
    assert storyboard.cost_state is ToolCostState.metered
    assert script.input_schema["properties"]["stage"]["enum"] == [
        "full_script",
        "scene_script",
    ]
    assert storyboard.input_schema["properties"]["stage"]["enum"] == ["storyboard_text"]


def test_animatic_generate_is_not_registered_without_public_entry(registry):
    """票面返工项 4：master 无样片生成入口 → animatic_generate 不得注册。"""
    services, _ = _services()
    register_pipeline_tools(registry, services=services)
    with pytest.raises(DomainError) as excinfo:
        registry.get_tool("animatic_generate")
    assert excinfo.value.code is ErrorCode.CAPABILITY_UNSUPPORTED


def test_handler_invokes_injected_service_with_recorded_arguments():
    """AC2：handler 调用注入服务并透传参数，不触发真实 provider。"""
    services, fake = _services()
    registry = CapabilityRegistry()
    register_pipeline_tools(registry, services=services)
    handler = registry.get_tool("script_generate").handler

    result = asyncio.run(handler(_payload(instructions="更紧凑的节奏")))
    assert fake.calls == [
        {
            "run_id": "run-001",
            "stage": "full_script",
            "idempotency_key": "idem-0001",
            "instructions": "更紧凑的节奏",
        }
    ]
    assert result["artifact_id"] == "art_001"


def test_duplicate_idempotency_key_triggers_single_underlying_call():
    """AC3：同一 idempotency_key 重复调用只触发一次底层服务调用。"""
    services, fake = _services()
    registry = CapabilityRegistry()
    register_pipeline_tools(registry, services=services)
    handler = registry.get_tool("script_generate").handler

    first = asyncio.run(handler(_payload()))
    second = asyncio.run(handler(_payload()))
    assert len(fake.calls) == 1
    assert first == second


def test_storyboard_and_script_do_not_share_idempotency_memo():
    """AC3（边界）：同名幂等键在两个工具之间互不吞调用。"""
    services, fake = _services()
    registry = CapabilityRegistry()
    register_pipeline_tools(registry, services=services)
    script = registry.get_tool("script_generate").handler
    storyboard = registry.get_tool("storyboard_generate").handler

    asyncio.run(script(_payload(stage="full_script")))
    asyncio.run(storyboard(_payload(stage="storyboard_text")))
    assert len(fake.calls) == 2


def test_invalid_tool_arguments_are_rejected():
    """票面 schema：缺必填/超限 instructions 一律 VALIDATION_FAILED。"""
    services, fake = _services()
    registry = CapabilityRegistry()
    register_pipeline_tools(registry, services=services)
    handler = registry.get_tool("script_generate").handler

    with pytest.raises(DomainError) as excinfo:
        asyncio.run(handler({"run_id": "run-001", "stage": "full_script"}))
    assert excinfo.value.code is ErrorCode.VALIDATION_FAILED
    with pytest.raises(DomainError):
        asyncio.run(handler(_payload(instructions="x" * 4001)))
    assert fake.calls == []


def test_unknown_tool_raises_capability_unsupported():
    """AC4：未注册工具抛 CAPABILITY_UNSUPPORTED。"""
    registry = CapabilityRegistry()
    with pytest.raises(DomainError) as excinfo:
        registry.get_tool("unknown")
    assert excinfo.value.code is ErrorCode.CAPABILITY_UNSUPPORTED


@pytest.mark.parametrize(
    "candidate",
    [
        "/home/user/private/output.md",
        "C:\\Users\\someone\\output.md",
        "C:/Users/someone/output.md",
        "\\\\server\\share\\output.md",
        "//server/share/output.md",
    ],
)
def test_path_sanitizer_rejects_posix_drive_and_unc_styles(candidate: str):
    """AC5 负例：POSIX 绝对路径、Windows 盘符与 UNC 风格字符串全部被识别脱敏。"""
    assert is_absolute_path_string(candidate) is True
    cleaned = sanitize_tool_result({"content_path": candidate})
    assert cleaned["content_path"] == "<redacted-absolute-path>"


def test_sanitize_tool_result_strips_credentials_and_keeps_safe_fields():
    """AC5：凭据类字段整体剔除，安全字段与相对路径保留。"""
    cleaned = sanitize_tool_result(
        {
            "stage": "full_script",
            "display_path": "relative/output.md",
            "provider_api_key": _FAKE_KEY_VALUE,
            "authorization": _FAKE_AUTH_VALUE,
            "notes": ["plain text", "C:\\Windows\\temp"],
        }
    )
    assert "provider_api_key" not in cleaned
    assert "authorization" not in cleaned
    assert cleaned["display_path"] == "relative/output.md"
    assert cleaned["stage"] == "full_script"
    assert cleaned["notes"] == ["plain text", "<redacted-absolute-path>"]


def test_handler_output_never_contains_absolute_paths_or_credentials():
    """AC5 端到端：服务返回带路径与凭据字段，handler 输出已清洗。"""
    services, _ = _services()
    registry = CapabilityRegistry()
    register_pipeline_tools(registry, services=services)
    handler = registry.get_tool("script_generate").handler

    result = asyncio.run(handler(_payload()))
    assert result["content_path"] == "<redacted-absolute-path>"
    assert "provider_api_key" not in result
    assert _FAKE_KEY_VALUE not in str(result)


def test_catalog_tools_section_matches_tool_registrations():
    """接口目录：tools 字段统一为 risk_level / cost_state / input_schema。"""
    catalog_path = (
        Path(__file__).resolve().parents[3]
        / "docs/contracts/AGENT-WORKBENCH-INTERFACE-CATALOG.yaml"
    )
    catalog = yaml.safe_load(catalog_path.read_text(encoding="utf-8"))
    entries = {item["name"]: item for item in catalog["tools"]}
    assert set(entries) == {"script_generate", "storyboard_generate"}
    for entry in entries.values():
        assert entry["risk_level"] == "medium"
        assert entry["cost_state"] == "metered"
        assert entry["idempotency_supported"] is True
        assert entry["input_schema"]["type"] == "object"
    assert entries["script_generate"]["input_schema"]["properties"]["stage"]["enum"] == [
        "full_script",
        "scene_script",
    ]
    assert entries["storyboard_generate"]["input_schema"]["properties"]["stage"][
        "enum"
    ] == ["storyboard_text"]
