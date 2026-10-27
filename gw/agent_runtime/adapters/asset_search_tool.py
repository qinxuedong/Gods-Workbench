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

"""Adapter for registering asset_search tool into CapabilityRegistry."""

from __future__ import annotations

from typing import Any, Mapping

from gw.agent_runtime.errors import DomainError
from gw.agent_runtime.models import ErrorCode, ToolCostState, ToolRiskLevel
from gw.agent_runtime.policy import CapabilityRegistry, ToolRegistration

ASSET_SEARCH_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["query"],
    "properties": {
        "query": {
            "type": "string",
            "minLength": 1,
            "maxLength": 4096,
        },
        "limit": {
            "type": "integer",
            "minimum": 1,
            "maximum": 50,
            "default": 10,
        },
    },
}


def register_asset_search_tool(
    registry: CapabilityRegistry, searcher: Any
) -> None:
    """Register the asset_search tool into CapabilityRegistry."""

    async def _handle_asset_search(arguments: Mapping[str, Any]) -> dict[str, Any]:
        query = str(arguments["query"])
        limit = int(arguments.get("limit", 10))

        try:
            results = searcher.search(query, limit=limit)
        except Exception as exc:
            raise DomainError(
                ErrorCode.UPSTREAM_UNAVAILABLE,
                "Asset search query failed.",
                request_id="asset_search",
            ) from exc

        items = [
            {
                "asset_id": item.asset_id,
                "score": item.score,
                "lexical_rank": item.lexical_rank,
                "vector_rank": item.vector_rank,
            }
            for item in results
        ]
        return {
            "items": items,
            "vector_track_degraded": getattr(
                results, "vector_track_degraded", False
            ),
        }

    registration = ToolRegistration(
        name="asset_search",
        handler=_handle_asset_search,
        required_permissions=(),
        input_schema=ASSET_SEARCH_INPUT_SCHEMA,
        risk_level=ToolRiskLevel.low,
        cost_state=ToolCostState.free,
        idempotency_supported=True,
    )
    registry.register_tool(registration)
