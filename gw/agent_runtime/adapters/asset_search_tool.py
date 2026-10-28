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

"""MVP2-T0：把现有混合检索封装为 Agent 只读工具 ``asset_search``。

工具只读、免费、幂等：查询经构造函数注入的 :class:`HybridAssetSearcher`
（或同签名替身）完成，返回候选参考素材的稳定 ``asset_id`` 与双轨排名。
检索器异常时 fail closed，抛出净化后的 :class:`DomainError`，不向调用方
泄露底层异常原文、数据库路径或任何绝对路径；模块导入期不连接任何索引库。
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from gw.agent_runtime.errors import DomainError
from gw.agent_runtime.models import ErrorCode, ToolCostState, ToolRiskLevel
from gw.agent_runtime.policy import CapabilityRegistry, ToolRegistration

#: 工具入参 schema；limit 上限收紧到 50（引擎本身允许 1~1000）。
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

#: DomainError 的稳定 request_id；固定常量，不携带查询内容或宿主信息。
_ASSET_SEARCH_REQUEST_ID = "tool:asset_search"


def _validation_error(message: str) -> DomainError:
    return DomainError(
        ErrorCode.VALIDATION_FAILED,
        message,
        request_id=_ASSET_SEARCH_REQUEST_ID,
    )


def register_asset_search_tool(
    registry: CapabilityRegistry, searcher: Any
) -> None:
    """把只读素材检索工具注册进 ``registry``。

    ``searcher`` 必须提供与 :class:`HybridAssetSearcher.search` 相同签名的
    ``search`` 方法；重复注册同名工具由注册表抛 ``ValueError``。
    """

    async def _handle_asset_search(arguments: Mapping[str, Any]) -> dict[str, Any]:
        query = arguments.get("query")
        if not isinstance(query, str) or not query.strip():
            raise _validation_error("The asset_search tool requires a non-empty query.")
        limit = arguments.get("limit", 10)
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 50:
            raise _validation_error(
                "The asset_search tool limit must be an integer between 1 and 50."
            )

        try:
            results = searcher.search(query, limit=limit)
        except Exception as exc:
            # Fail closed：底层异常只保留类型语义，原文可能携带数据库路径等
            # 敏感信息，绝不进入对外错误包。
            raise DomainError(
                ErrorCode.UPSTREAM_UNAVAILABLE,
                "The asset search upstream is temporarily unavailable.",
                request_id=_ASSET_SEARCH_REQUEST_ID,
            ) from exc

        items = [
            {
                "asset_id": result.asset_id,
                "score": result.score,
                "lexical_rank": result.lexical_rank,
                "vector_rank": result.vector_rank,
            }
            for result in results
        ]
        return {
            "items": items,
            "vector_track_degraded": bool(
                getattr(results, "vector_track_degraded", False)
            ),
        }

    registry.register_tool(
        ToolRegistration(
            name="asset_search",
            handler=_handle_asset_search,
            required_permissions=(),
            input_schema=ASSET_SEARCH_INPUT_SCHEMA,
            risk_level=ToolRiskLevel.low,
            cost_state=ToolCostState.free,
            idempotency_supported=True,
        )
    )
