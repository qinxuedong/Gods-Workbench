"""Contract tests for MVP2-T4 pipeline tool registration."""

from __future__ import annotations

from typing import Any
import pytest
from jsonschema import Draft202012Validator

from gw.agent_runtime.adapters.pipeline_tools import (
    register_pipeline_tools,
    SCRIPT_GENERATE_SCHEMA,
    STORYBOARD_GENERATE_SCHEMA,
)
from gw.agent_runtime.errors import DomainError
from gw.agent_runtime.models import ErrorCode, ToolCostState, ToolRiskLevel
from gw.agent_runtime.policy import CapabilityRegistry


class FakePipelineService:
    def __init__(self, response: Any = None) -> None:
        self.calls: list[dict[str, Any]] = []
        self.response = response or {"status": "success", "artifact_id": "art_12345"}

    async def generate(self, kwargs: dict[str, Any]) -> Any:
        self.calls.append(kwargs)
        return self.response


@pytest.mark.anyio
async def test_ac1_schema_draft_2020_12_validation() -> None:
    Draft202012Validator.check_schema(SCRIPT_GENERATE_SCHEMA)
    Draft202012Validator.check_schema(STORYBOARD_GENERATE_SCHEMA)

    registry = CapabilityRegistry()
    fake_script = FakePipelineService()
    fake_storyboard = FakePipelineService()

    register_pipeline_tools(
        registry,
        services={
            "script": fake_script,
            "storyboard": fake_storyboard,
        },
    )

    script_tool = registry.get_tool("script_generate")
    assert script_tool.risk_level == ToolRiskLevel.medium
    assert script_tool.cost_state == ToolCostState.metered
    assert script_tool.idempotency_supported is True

    sb_tool = registry.get_tool("storyboard_generate")
    assert sb_tool.risk_level == ToolRiskLevel.medium
    assert sb_tool.cost_state == ToolCostState.metered
    assert sb_tool.idempotency_supported is True


@pytest.mark.anyio
async def test_ac2_handler_calls_injected_service() -> None:
    registry = CapabilityRegistry()
    fake_script = FakePipelineService({"status": "ok", "script_id": "s1"})
    register_pipeline_tools(registry, services={"script": fake_script})

    tool = registry.get_tool("script_generate")
    payload = {
        "run_id": "run_001",
        "stage": "full_script",
        "idempotency_key": "idempotency_key_1234",
        "instructions": "Make it exciting",
    }
    result = await tool.handler(payload)

    assert result == {"status": "ok", "script_id": "s1"}
    assert len(fake_script.calls) == 1
    assert fake_script.calls[0] == payload


@pytest.mark.anyio
async def test_ac3_idempotency_deduplication() -> None:
    registry = CapabilityRegistry()
    fake_script = FakePipelineService({"status": "ok", "version": 1})
    register_pipeline_tools(registry, services={"script": fake_script})

    tool = registry.get_tool("script_generate")
    payload = {
        "run_id": "run_001",
        "stage": "full_script",
        "idempotency_key": "same_idempotency_key_1234",
    }

    res1 = await tool.handler(payload)
    res2 = await tool.handler(payload)

    assert res1 == res2
    assert len(fake_script.calls) == 1


@pytest.mark.anyio
async def test_ac4_unknown_tool_raises_capability_unsupported() -> None:
    registry = CapabilityRegistry()
    with pytest.raises(DomainError) as exc_info:
        registry.get_tool("unknown")

    assert exc_info.value.code == ErrorCode.CAPABILITY_UNSUPPORTED


@pytest.mark.anyio
async def test_ac5_returns_no_absolute_path_or_credentials() -> None:
    registry = CapabilityRegistry()
    fake_service_path = FakePipelineService({"output_path": "/absolute/path/to/file.txt"})
    fake_service_cred = FakePipelineService({"api_key": "secret_key_123", "data": "value"})

    register_pipeline_tools(registry, services={"script": fake_service_path, "storyboard": fake_service_cred})

    script_tool = registry.get_tool("script_generate")
    with pytest.raises(DomainError) as exc_info:
        await script_tool.handler({
            "run_id": "run_1",
            "stage": "scene_script",
            "idempotency_key": "idempotency_key_12345",
        })
    assert exc_info.value.code == ErrorCode.VALIDATION_FAILED

    sb_tool = registry.get_tool("storyboard_generate")
    res = await sb_tool.handler({
        "run_id": "run_1",
        "stage": "storyboard_text",
        "idempotency_key": "idempotency_key_12345",
    })
    assert "api_key" not in res
    assert res == {"data": "value"}
