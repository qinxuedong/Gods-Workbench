"""Contract tests for MVP2-T5 render tools (I2V, timeline, master render with gate enforcement)."""

from __future__ import annotations

from typing import Any
import pytest
from jsonschema import Draft202012Validator

from gw.agent_runtime.adapters.pipeline_tools import (
    I2V_GENERATE_SCHEMA,
    MASTER_RENDER_SCHEMA,
    TIMELINE_COMPOSE_SCHEMA,
    register_render_tools,
)
from gw.agent_runtime.errors import DomainError
from gw.agent_runtime.models import ErrorCode
from gw.agent_runtime.policy import CapabilityRegistry


class FakeI2VService:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def create_generation(self, payload: dict[str, Any]) -> dict[str, Any]:
        self.calls.append(payload)
        return {
            "task_id": "i2v_task_123",
            "status": "queued",
            "model": payload.get("model", "default_i2v"),
            "video_url": "outputs/generated_i2v.mp4",
        }


class FakeTimelineService:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def compose(self, payload: dict[str, Any]) -> dict[str, Any]:
        self.calls.append(payload)
        return {
            "timeline_id": "timeline_456",
            "track_count": len(payload.get("tracks", [])),
            "status": "composed",
        }


class FakeMasterService:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.output_path: str = "renders/master_output.mp4"

    async def render_master(self, payload: dict[str, Any]) -> dict[str, Any]:
        self.calls.append(payload)
        return {
            "master_id": "master_789",
            "output_path": self.output_path,
            "status": "completed",
        }


@pytest.fixture
def fake_services() -> dict[str, Any]:
    return {
        "i2v": FakeI2VService(),
        "timeline": FakeTimelineService(),
        "master": FakeMasterService(),
    }


def test_ac1_schema_draft202012_validity():
    """AC1: Schemas for i2v_generate, timeline_compose, master_render must pass Draft 2020-12 check."""
    Draft202012Validator.check_schema(I2V_GENERATE_SCHEMA)
    Draft202012Validator.check_schema(TIMELINE_COMPOSE_SCHEMA)
    Draft202012Validator.check_schema(MASTER_RENDER_SCHEMA)

    registry = CapabilityRegistry()
    services = {
        "i2v": FakeI2VService(),
        "timeline": FakeTimelineService(),
        "master": FakeMasterService(),
    }
    register_render_tools(registry, services=services)

    assert registry.get_tool("i2v_generate") is not None
    assert registry.get_tool("timeline_compose") is not None
    assert registry.get_tool("master_render") is not None


@pytest.mark.anyio
async def test_ac2_i2v_gate_enforcement(fake_services):
    """AC2: I2V fails closed when gate is not approved; succeeds when approved."""
    registry = CapabilityRegistry()
    gates_state: dict[str, str] = {"i2v_submit": "pending"}

    def gate_lookup(run_id: str, kind: str) -> str:
        return gates_state.get(kind, "pending")

    register_render_tools(registry, services=fake_services, gate_lookup=gate_lookup)
    i2v_tool = registry.get_tool("i2v_generate")

    payload = {
        "run_id": "run_001",
        "idempotency_key": "idemp_i2v_12345",
        "image_asset_id": "asset_img_001",
        "prompt": "sunset video generation",
        "model": "fake_i2v_v1",
        "duration": 5.0,
    }

    # Unapproved gate should fail closed
    with pytest.raises(DomainError) as exc_info:
        await i2v_tool.handler(payload)
    assert exc_info.value.code == ErrorCode.MANUAL_REVIEW_REQUIRED
    assert len(fake_services["i2v"].calls) == 0

    # Approve gate and re-invoke
    gates_state["i2v_submit"] = "approved"
    result = await i2v_tool.handler(payload)
    assert result["status"] == "queued"
    assert len(fake_services["i2v"].calls) == 1
    assert fake_services["i2v"].calls[0]["prompt"] == "sunset video generation"


@pytest.mark.anyio
async def test_ac3_master_render_gate_and_no_abs_path(fake_services):
    """AC3: Master render rejected when master_output gate not approved; sanitized relative paths returned."""
    registry = CapabilityRegistry()
    gates_state: dict[str, str] = {"master_output": "pending"}

    def gate_lookup(run_id: str, kind: str) -> str:
        return gates_state.get(kind, "pending")

    register_render_tools(registry, services=fake_services, gate_lookup=gate_lookup)
    master_tool = registry.get_tool("master_render")

    payload = {
        "run_id": "run_001",
        "idempotency_key": "idemp_master_12345",
        "manifest_id": "man_123",
        "output_filename": "master_output.mp4",
    }

    # Pending gate -> MANUAL_REVIEW_REQUIRED
    with pytest.raises(DomainError) as exc_info:
        await master_tool.handler(payload)
    assert exc_info.value.code == ErrorCode.MANUAL_REVIEW_REQUIRED

    # Approve gate
    gates_state["master_output"] = "approved"
    res = await master_tool.handler(payload)
    assert res["status"] == "completed"
    assert res["output_path"] == "renders/master_output.mp4"

    # Absolute path returned by upstream service should trigger VALIDATION_FAILED
    fake_services["master"].output_path = "/tmp/abs_master.mp4"
    abs_payload = {
        "run_id": "run_001",
        "idempotency_key": "idemp_master_abs_67890",
        "manifest_id": "man_123",
    }
    with pytest.raises(DomainError) as exc_info:
        await master_tool.handler(abs_payload)
    assert exc_info.value.code == ErrorCode.VALIDATION_FAILED


@pytest.mark.anyio
async def test_timeline_compose_free_tool(fake_services):
    """Timeline compose is a free tool with no gate check required."""
    registry = CapabilityRegistry()

    def gate_lookup(run_id: str, kind: str) -> str:
        return "pending"

    register_render_tools(registry, services=fake_services, gate_lookup=gate_lookup)
    tl_tool = registry.get_tool("timeline_compose")

    payload = {
        "run_id": "run_001",
        "idempotency_key": "idemp_tl_123456",
        "tracks": [{"id": 1}],
    }

    res = await tl_tool.handler(payload)
    assert res["status"] == "composed"


@pytest.mark.anyio
async def test_ac5_unregistered_tool_raises_capability_unsupported():
    """AC5: Attempting to get an unregistered tool name raises CAPABILITY_UNSUPPORTED."""
    registry = CapabilityRegistry()
    with pytest.raises(DomainError) as exc_info:
        registry.get_tool("non_existent_render_tool")
    assert exc_info.value.code == ErrorCode.CAPABILITY_UNSUPPORTED


@pytest.mark.anyio
async def test_ac6_idempotency_key_deduplication(fake_services):
    """AC6: Repeated calls with identical idempotency_key trigger underlying service only once."""
    registry = CapabilityRegistry()
    gates_state: dict[str, str] = {"i2v_submit": "approved"}

    def gate_lookup(run_id: str, kind: str) -> str:
        return gates_state.get(kind, "approved")

    register_render_tools(registry, services=fake_services, gate_lookup=gate_lookup)
    i2v_tool = registry.get_tool("i2v_generate")

    payload = {
        "run_id": "run_001",
        "idempotency_key": "idemp_reuse_99999",
        "image_asset_id": "asset_001",
        "prompt": "idempotency test",
    }

    res1 = await i2v_tool.handler(payload)
    res2 = await i2v_tool.handler(payload)

    assert res1 == res2
    assert len(fake_services["i2v"].calls) == 1
