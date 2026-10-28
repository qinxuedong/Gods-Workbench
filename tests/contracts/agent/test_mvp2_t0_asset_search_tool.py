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

"""MVP2-T0 Agent 素材检索工具契约测试（QIN-439 / GitHub #364）。

覆盖 AC1~AC6：注册与 schema 校验、多命中相对 ``asset_id`` 排序、空库不抛
错、检索器异常 fail closed 不泄露路径、输出零绝对路径、全程零网络调用。
"""

from __future__ import annotations

import re
import socket
from pathlib import Path
from typing import Any

import pytest

import gw.core.sqlite_vec_engine as sqlite_vec_engine
from gw.agent_runtime.adapters.asset_search_tool import (
    ASSET_SEARCH_INPUT_SCHEMA,
    register_asset_search_tool,
)
from gw.agent_runtime.errors import DomainError
from gw.agent_runtime.models import ErrorCode, Identifier, ToolCostState, ToolRiskLevel
from gw.agent_runtime.policy import CapabilityRegistry
from gw.asset_registry.hybrid_search import HybridAssetSearcher
from gw.core.sqlite_vec_engine import SQLiteVecEngine

_ACTOR = Identifier("actor-asset-search-test")

#: 三条夹具文档；「海」同时命中前两条，专门用于验收多命中排序（AC2）。
_FIXTURE_DOCUMENTS = [
    {"asset_id": "a-001", "title": "海边日落", "content": "潮汐与海浪的镜头"},
    {"asset_id": "a-002", "title": "蓝天大海", "content": "晴朗的天空与大白云"},
    {"asset_id": "a-003", "title": "室内对白", "content": "房间内的对话"},
]


def _assert_no_absolute_paths(data: object) -> None:
    """递归断言结构中任何字符串都不以 ``/`` 或盘符形式携带绝对路径。"""
    if isinstance(data, str):
        assert not data.startswith("/"), f"字符串以 '/' 开头: {data}"
        assert not re.match(r"^[a-zA-Z]:[\\/]", data), f"字符串含 Windows 盘符路径: {data}"
    elif isinstance(data, dict):
        for key, value in data.items():
            _assert_no_absolute_paths(key)
            _assert_no_absolute_paths(value)
    elif isinstance(data, (list, tuple, set)):
        for item in data:
            _assert_no_absolute_paths(item)


@pytest.fixture
def block_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """AC6：整个测试期间禁止 DNS 解析与显式出站连接。

    Windows 的事件循环初始化会自建回环管道，不能按套接字构造拦截；任何真实
    网络调用（urllib/httpx/requests 等）都必经 ``getaddrinfo`` 或显式
    ``create_connection``，在此层断言既不误伤基础设施，又能拦住全部出站行为。
    """

    def _forbidden(*args: object, **kwargs: object) -> object:
        raise AssertionError("asset_search 工具执行期间发起了网络调用")

    monkeypatch.setattr(socket, "getaddrinfo", _forbidden)
    monkeypatch.setattr(socket, "gethostbyname", _forbidden)
    monkeypatch.setattr(socket, "gethostbyname_ex", _forbidden)
    monkeypatch.setattr(socket, "create_connection", _forbidden)


@pytest.fixture
def fts_only_engine(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> SQLiteVecEngine:
    """确定性单轨夹具：强制向量扩展不可用，检索只走 FTS5。"""
    monkeypatch.setattr(sqlite_vec_engine, "_load_sqlite_vec", lambda _db: False)
    engine = SQLiteVecEngine(tmp_path / "mvp2_t0_search.sqlite3", dimensions=3)
    for document in _FIXTURE_DOCUMENTS:
        engine.upsert_document(
            asset_id=document["asset_id"],
            title=document["title"],
            content=document["content"],
        )
    return engine


def _registered_registry(searcher: Any) -> CapabilityRegistry:
    registry = CapabilityRegistry()
    register_asset_search_tool(registry, searcher)
    return registry


@pytest.mark.anyio
async def test_ac1_registration_schema_and_duplicate(block_network: None) -> None:
    """AC1：注册成功、登记字段正确、schema 逐字匹配；重复注册抛 ValueError。"""
    registry = _registered_registry(object())

    tool = registry.get_tool("asset_search")
    assert tool.name == "asset_search"
    assert tool.risk_level is ToolRiskLevel.low
    assert tool.cost_state is ToolCostState.free
    assert tool.idempotency_supported is True
    assert tool.required_permissions == ()
    assert tool.input_schema == ASSET_SEARCH_INPUT_SCHEMA

    with pytest.raises(ValueError, match="unique"):
        register_asset_search_tool(registry, object())


@pytest.mark.anyio
async def test_ac2_multi_hit_ordering_relative_to_asset_id(
    fts_only_engine: SQLiteVecEngine, block_network: None
) -> None:
    """AC2：3 条文档夹具中查询命中 ≥2 条，断言按 (-score, asset_id) 排序。"""
    searcher = HybridAssetSearcher(fts_only_engine)
    result = await _registered_registry(searcher).invoke_tool(
        "asset_search", _ACTOR, {"query": "海"}
    )

    assert isinstance(result, dict)
    items = result["items"]
    # 排序验收前提：查询必须真实命中至少 2 条不同文档。
    hit_ids = [item["asset_id"] for item in items]
    assert len(hit_ids) >= 2, f"查询须命中至少 2 条文档才能验收排序，实际 {hit_ids}"
    assert set(hit_ids) == {"a-001", "a-002"}
    # 相对 asset_id 排序：主键 score 降序，平分时按 asset_id 升序（与检索器一致）。
    expected_order = [
        item["asset_id"]
        for item in sorted(items, key=lambda entry: (-entry["score"], entry["asset_id"]))
    ]
    assert hit_ids == expected_order
    # 词面 rank 1 的 a-001 必须排在 rank 2 的 a-002 之前。
    assert items[0]["asset_id"] == "a-001"
    assert items[0]["lexical_rank"] == 1
    assert items[1]["lexical_rank"] == 2
    assert items[0]["vector_rank"] is None
    assert isinstance(items[0]["score"], float)
    assert isinstance(result["vector_track_degraded"], bool)
    _assert_no_absolute_paths(result)


@pytest.mark.anyio
async def test_ac3_empty_database_returns_empty_items(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, block_network: None
) -> None:
    """AC3：空库返回 ``{"items": [], "vector_track_degraded": ...}``，不抛错。"""
    monkeypatch.setattr(sqlite_vec_engine, "_load_sqlite_vec", lambda _db: False)
    engine = SQLiteVecEngine(tmp_path / "mvp2_t0_empty.sqlite3", dimensions=3)
    try:
        result = await _registered_registry(HybridAssetSearcher(engine)).invoke_tool(
            "asset_search", _ACTOR, {"query": "不存在的素材"}
        )

        assert result["items"] == []
        assert isinstance(result["vector_track_degraded"], bool)
        _assert_no_absolute_paths(result)
    finally:
        engine.close()


@pytest.mark.anyio
async def test_ac4_fail_closed_on_searcher_exception(block_network: None) -> None:
    """AC4：检索器抛异常时 fail closed 为 DomainError，不泄露异常原文中的路径。"""

    class FailingSearcher:
        def search(self, query: str, *, limit: int = 20) -> Any:
            raise RuntimeError(
                "Database error at /var/lib/secret/data.db or C:\\secret\\data.db"
            )

    registry = _registered_registry(FailingSearcher())

    with pytest.raises(DomainError) as exc_info:
        await registry.invoke_tool("asset_search", _ACTOR, {"query": "测试"})

    error = exc_info.value
    assert error.code is ErrorCode.UPSTREAM_UNAVAILABLE
    # 底层异常只保留类型链，原文（含路径）不进入对外错误消息。
    assert "/var/lib/secret" not in str(error)
    assert "C:\\secret" not in str(error)
    assert error.message == "The asset search upstream is temporarily unavailable."
    assert error.request_id == "tool:asset_search"
    assert isinstance(error.__cause__, RuntimeError)
    _assert_no_absolute_paths(error.message)
    _assert_no_absolute_paths(error.request_id)


@pytest.mark.anyio
async def test_ac5_output_has_no_absolute_paths(
    fts_only_engine: SQLiteVecEngine, block_network: None
) -> None:
    """AC5：输出中任何字符串值都不含绝对路径（盘符与 ``/`` 开头均拒绝）。"""
    result = await _registered_registry(HybridAssetSearcher(fts_only_engine)).invoke_tool(
        "asset_search", _ACTOR, {"query": "海浪", "limit": 5}
    )

    assert result["items"], "夹具查询应至少命中一条文档"
    _assert_no_absolute_paths(result)


@pytest.mark.anyio
async def test_ac6_no_network_during_search(
    fts_only_engine: SQLiteVecEngine, block_network: None
) -> None:
    """AC6：检索全程无任何网络调用（DNS/出站连接即断言失败）。"""
    result = await _registered_registry(HybridAssetSearcher(fts_only_engine)).invoke_tool(
        "asset_search", _ACTOR, {"query": "海"}
    )

    assert {item["asset_id"] for item in result["items"]} == {"a-001", "a-002"}


@pytest.mark.anyio
async def test_handler_direct_call_rejects_invalid_arguments(
    fts_only_engine: SQLiteVecEngine,
) -> None:
    """绕过 registry schema 校验直调 handler 时，参数错误同样 fail closed。"""
    tool = _registered_registry(HybridAssetSearcher(fts_only_engine)).get_tool(
        "asset_search"
    )

    for bad_arguments in ({"query": "   "}, {"query": "海", "limit": 0},
                          {"query": "海", "limit": 51}, {"query": "海", "limit": True}):
        with pytest.raises(DomainError) as exc_info:
            await tool.handler(bad_arguments)
        assert exc_info.value.code is ErrorCode.VALIDATION_FAILED
