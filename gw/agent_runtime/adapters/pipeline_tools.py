"""Agent tool registrations for pipeline capabilities (script / storyboard / i2v / timeline / master)."""

from __future__ import annotations

import inspect
import os
from collections.abc import Mapping
from typing import Any

from gw.agent_runtime.errors import DomainError
from gw.agent_runtime.models import ErrorCode, ToolCostState, ToolRiskLevel
from gw.agent_runtime.policy import CapabilityRegistry, ToolRegistration

SCRIPT_GENERATE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["run_id", "stage", "idempotency_key"],
    "properties": {
        "run_id": {"type": "string"},
        "stage": {"type": "string", "enum": ["full_script", "scene_script"]},
        "idempotency_key": {"type": "string", "minLength": 8},
        "instructions": {"type": "string", "maxLength": 4000},
    },
}

STORYBOARD_GENERATE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["run_id", "stage", "idempotency_key"],
    "properties": {
        "run_id": {"type": "string"},
        "stage": {"type": "string", "enum": ["storyboard_text"]},
        "idempotency_key": {"type": "string", "minLength": 8},
        "instructions": {"type": "string", "maxLength": 4000},
    },
}

I2V_GENERATE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["run_id", "idempotency_key", "image_asset_id", "prompt"],
    "properties": {
        "run_id": {"type": "string"},
        "idempotency_key": {"type": "string", "minLength": 8},
        "image_asset_id": {"type": "string"},
        "prompt": {"type": "string"},
        "model": {"type": "string"},
        "duration": {"type": "number"},
        "aspect_ratio": {"type": "string"},
    },
}

TIMELINE_COMPOSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["run_id", "idempotency_key"],
    "properties": {
        "run_id": {"type": "string"},
        "idempotency_key": {"type": "string", "minLength": 8},
        "tracks": {"type": "array"},
        "options": {"type": "object"},
    },
}

MASTER_RENDER_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["run_id", "idempotency_key"],
    "properties": {
        "run_id": {"type": "string"},
        "idempotency_key": {"type": "string", "minLength": 8},
        "manifest_id": {"type": "string"},
        "manifest": {"type": "object"},
        "asset_paths": {"type": "object"},
        "output_filename": {"type": "string"},
        "options": {"type": "object"},
    },
}


def _sanitize_response(data: Any, request_id: str) -> Any:
    """Ensure output contains no absolute paths or sensitive credentials."""
    if isinstance(data, str):
        if os.path.isabs(data):
            raise DomainError(
                ErrorCode.VALIDATION_FAILED,
                "Tool output contains absolute path",
                request_id=request_id,
            )
        return data
    if isinstance(data, dict):
        sanitized: dict[str, Any] = {}
        for key, val in data.items():
            if key in {"api_key", "secret", "token", "password", "credential"}:
                continue
            sanitized[key] = _sanitize_response(val, request_id)
        return sanitized
    if isinstance(data, list):
        return [_sanitize_response(item, request_id) for item in data]
    return data


class _IdempotentPipelineHandler:
    def __init__(
        self,
        tool_name: str,
        service: Any,
        *,
        gate_kind: str | None = None,
        gate_lookup: Any = None,
        cost_state: ToolCostState = ToolCostState.free,
    ) -> None:
        self.tool_name = tool_name
        self.service = service
        self.gate_kind = gate_kind
        self.gate_lookup = gate_lookup
        self.cost_state = cost_state
        self._cache: dict[str, Any] = {}

    async def __call__(self, kwargs: Mapping[str, Any]) -> Any:
        idempotency_key = kwargs.get("idempotency_key")
        run_id = kwargs.get("run_id")
        request_id = f"tool:{self.tool_name}:{idempotency_key or run_id or 'unknown'}"
        if not isinstance(idempotency_key, str) or len(idempotency_key) < 8:
            raise DomainError(
                ErrorCode.VALIDATION_FAILED,
                "Invalid idempotency key",
                request_id=request_id,
            )

        # Gate enforcement for mandatory gate_kind or non-free cost tools
        if self.gate_kind is not None:
            gate_approved = False
            if callable(self.gate_lookup):
                try:
                    gate_res = self.gate_lookup(run_id, self.gate_kind)
                    if hasattr(gate_res, "state"):
                        g_state = gate_res.state
                    else:
                        g_state = gate_res
                    val = getattr(g_state, "value", g_state)
                    if str(val).lower() == "approved":
                        gate_approved = True
                except Exception:
                    gate_approved = False

            if not gate_approved:
                raise DomainError(
                    ErrorCode.MANUAL_REVIEW_REQUIRED,
                    f"Gate check failed or not approved for tool {self.tool_name}",
                    request_id=request_id,
                )

        cache_key = f"{self.tool_name}:{idempotency_key}"
        if cache_key in self._cache:
            return self._cache[cache_key]

        payload = dict(kwargs)
        if hasattr(self.service, "create_generation"):
            res = self.service.create_generation(payload)
        elif hasattr(self.service, "render_master"):
            res = self.service.render_master(payload)
        elif hasattr(self.service, "compose"):
            res = self.service.compose(payload)
        elif hasattr(self.service, "generate"):
            res = self.service.generate(payload)
        elif callable(self.service):
            res = self.service(payload)
        else:
            raise DomainError(
                ErrorCode.UPSTREAM_UNAVAILABLE,
                f"Service for {self.tool_name} is not callable",
                request_id=request_id,
            )

        if inspect.isawaitable(res):
            result = await res
        else:
            result = res

        sanitized = _sanitize_response(result, request_id)
        self._cache[cache_key] = sanitized
        return sanitized


def register_pipeline_tools(
    registry: CapabilityRegistry,
    *,
    services: Mapping[str, Any],
) -> None:
    """Register pipeline capabilities (script_generate, storyboard_generate) to registry.

    Note: animatic_generate is not registered as no backend capability entry point exists.
    """
    if "script" in services:
        script_handler = _IdempotentPipelineHandler("script_generate", services["script"])
        registry.register_tool(
            ToolRegistration(
                name="script_generate",
                handler=script_handler,
                input_schema=SCRIPT_GENERATE_SCHEMA,
                risk_level=ToolRiskLevel.medium,
                cost_state=ToolCostState.metered,
                idempotency_supported=True,
            )
        )

    if "storyboard" in services:
        sb_handler = _IdempotentPipelineHandler("storyboard_generate", services["storyboard"])
        registry.register_tool(
            ToolRegistration(
                name="storyboard_generate",
                handler=sb_handler,
                input_schema=STORYBOARD_GENERATE_SCHEMA,
                risk_level=ToolRiskLevel.medium,
                cost_state=ToolCostState.metered,
                idempotency_supported=True,
            )
        )


def register_render_tools(
    registry: CapabilityRegistry,
    *,
    services: Mapping[str, Any],
    gate_lookup: Any = None,
) -> None:
    """Register render & post-production capabilities (i2v_generate, timeline_compose, master_render)."""
    if "i2v" in services:
        i2v_handler = _IdempotentPipelineHandler(
            "i2v_generate",
            services["i2v"],
            gate_kind="i2v_submit",
            gate_lookup=gate_lookup,
            cost_state=ToolCostState.metered,
        )
        registry.register_tool(
            ToolRegistration(
                name="i2v_generate",
                handler=i2v_handler,
                input_schema=I2V_GENERATE_SCHEMA,
                risk_level=ToolRiskLevel.high,
                cost_state=ToolCostState.metered,
                idempotency_supported=True,
            )
        )

    if "timeline" in services:
        timeline_handler = _IdempotentPipelineHandler(
            "timeline_compose",
            services["timeline"],
            gate_kind=None,
            gate_lookup=gate_lookup,
            cost_state=ToolCostState.free,
        )
        registry.register_tool(
            ToolRegistration(
                name="timeline_compose",
                handler=timeline_handler,
                input_schema=TIMELINE_COMPOSE_SCHEMA,
                risk_level=ToolRiskLevel.medium,
                cost_state=ToolCostState.free,
                idempotency_supported=True,
            )
        )

    if "master" in services:
        master_handler = _IdempotentPipelineHandler(
            "master_render",
            services["master"],
            gate_kind="master_output",
            gate_lookup=gate_lookup,
            cost_state=ToolCostState.free,
        )
        registry.register_tool(
            ToolRegistration(
                name="master_render",
                handler=master_handler,
                input_schema=MASTER_RENDER_SCHEMA,
                risk_level=ToolRiskLevel.critical,
                cost_state=ToolCostState.free,
                idempotency_supported=True,
            )
        )
