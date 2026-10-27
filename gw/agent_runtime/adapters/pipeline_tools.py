"""Agent tool registrations for pipeline capabilities (script / storyboard)."""

from __future__ import annotations

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
    def __init__(self, tool_name: str, service: Any) -> None:
        self.tool_name = tool_name
        self.service = service
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

        cache_key = f"{self.tool_name}:{idempotency_key}"
        if cache_key in self._cache:
            return self._cache[cache_key]

        if hasattr(self.service, "generate"):
            result = await self.service.generate(dict(kwargs))
        elif callable(self.service):
            result = await self.service(dict(kwargs))
        else:
            raise DomainError(
                ErrorCode.UPSTREAM_UNAVAILABLE,
                f"Service for {self.tool_name} is not callable",
                request_id=request_id,
            )

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
