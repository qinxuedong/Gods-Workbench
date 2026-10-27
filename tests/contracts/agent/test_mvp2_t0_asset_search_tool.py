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

"""MVP2-T0 Agent 素材检索工具契约测试。"""

from __future__ import annotations

import re
import socket
from pathlib import Path

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


def _assert_no_absolute_paths(data: object) -> None:
    """递归断言数据结构中的任何字符串不包含 Unix/Windows 绝对路径。"""
    if isinstance(data, str):
        assert not data.startswith("/"), f"字符串以 '/' 开头: {data}"
        assert not re.match(r"^[a-zA-Z]:[\\/]", data), f"字符串包含 Windows 盘符路径: {data}"
    elif isinstance(data, dict):
        for key, value in data.items():
            _assert_no_absolute_paths(key)
            _assert_no_absolute_paths(value)
    elif isinstance(data, (list, tuple, set)):
        for item in data:
            _assert_no_absolute_paths(item)


@pytest.fixture
def block_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """AC6: 阻止任何网络调用。"""
    original_socket = socket.socket

    def _forbidden_socket(
        family: int = socket.AF_INET, *args: object, **kwargs: object
    ) -> socket.socket:
        if family in (socket.AF_INET, socket.AF_INET6):
            raise AssertionError("在 asset_search 工具执行期间尝试了网络调用")
        return original_socket(family, *args, **kwargs)

    monkeypatch.setattr(socket, "socket", _forbidden_socket)


@pytest.mark.anyio
async def test_ac1_tool_registration(block_network: None) -> None:
    """AC1: 工具注册成功，schema 通过校验，重复注册抛 ValueError。"""
    registry = CapabilityRegistry()
    searcher = object()

    register_asset_search_tool(registry, searcher)

    tool = registry.get_tool("asset_search")
    assert tool.name == "asset_search"
    assert tool.risk_level == ToolRiskLevel.low
    assert tool.cost_state == ToolCostState.free
    assert tool.idempotency_supported is True
    assert tool.input_schema == ASSET_SEARCH_INPUT_SCHEMA

    with pytest.raises(ValueError, match="unique"):
        register_asset_search_tool(registry, searcher)


@pytest.mark.anyio
async def test_ac2_search_three_documents_ordering(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, block_network: None
) -> None:
    """AC2: 夹具库（3 条文档）中查询命中项返回正确 asset_id 顺序。"""
    monkeypatch.setattr(sqlite_vec_engine, "_load_sqlite_vec", lambda _db: False)
    engine = SQLiteVecEngine(tmp_path / "search.sqlite3", dimensions=3)
    try:
        engine.upsert_document(
            asset_id="a-001", title="海边日落", content="潮汐与海浪的镜头"
        )
        engine.upsert_document(
            asset_id="a-002", title="蓝天大海", content="晴朗的天空与大白云"
        )
        engine.upsert_document(
            asset_id="a-003", title="室内对白", content="房间内的对话"
        )

        searcher = HybridAssetSearcher(engine)
        registry = CapabilityRegistry()
        register_asset_search_tool(registry, searcher)

        actor_id = Identifier("actor-test")
        result = await registry.invoke_tool(
            "asset_search", actor_id, {"query": "海浪"}
        )

        assert isinstance(result, dict)
        assert "items" in result
        assert "vector_track_degraded" in result
        assert isinstance(result["vector_track_degraded"], bool)

        items = result["items"]
        assert len(items) >= 1
        assert items[0]["asset_id"] == "a-001"
        assert items[0]["lexical_rank"] == 1
        assert isinstance(items[0]["score"], float)

        _assert_no_absolute_paths(result)
    finally:
        engine.close()


@pytest.mark.anyio
async def test_ac3_empty_database(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, block_network: None
) -> None:
    """AC3: 空库返回 {"items":[], "vector_track_degraded": ...}，不抛错。"""
    monkeypatch.setattr(sqlite_vec_engine, "_load_sqlite_vec", lambda _db: False)
    engine = SQLiteVecEngine(tmp_path / "empty.sqlite3", dimensions=3)
    try:
        searcher = HybridAssetSearcher(engine)
        registry = CapabilityRegistry()
        register_asset_search_tool(registry, searcher)

        actor_id = Identifier("actor-test")
        result = await registry.invoke_tool(
            "asset_search", actor_id, {"query": "不存在素材"}
        )

        assert result["items"] == []
        assert isinstance(result["vector_track_degraded"], bool)
        _assert_no_absolute_paths(result)
    finally:
        engine.close()


@pytest.mark.anyio
async def test_ac4_fail_closed_on_searcher_exception(
    block_network: None,
) -> None:
    """AC4: searcher 抛异常时 fail closed：返回 DomainError 不泄露路径。"""

    class FailingSearcher:
        def search(self, query: str, *, limit: int = 20) -> None:
            raise RuntimeError(
                "Database error at /var/lib/secret/data.db or C:\\secret\\data.db"
            )

    registry = CapabilityRegistry()
    register_asset_search_tool(registry, FailingSearcher())

    actor_id = Identifier("actor-test")
    with pytest.raises(DomainError) as exc_info:
        await registry.invoke_tool("asset_search", actor_id, {"query": "测试"})

    err = exc_info.value
    assert err.code == ErrorCode.UPSTREAM_UNAVAILABLE
    assert err.message == "Asset search query failed."
    assert err.request_id == "asset_search"
    assert "/var/lib/secret" not in str(err)
    assert "C:\\secret" not in str(err)
    _assert_no_absolute_paths(err.message)
    _assert_no_absolute_paths(err.request_id)


@pytest.mark.anyio
async def test_ac5_no_absolute_paths_in_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, block_network: None
) -> None:
    """AC5: 输出中任何字符串值不含绝对路径。"""
    monkeypatch.setattr(sqlite_vec_engine, "_load_sqlite_vec", lambda _db: False)
    engine = SQLiteVecEngine(tmp_path / "ac5.sqlite3", dimensions=3)
    try:
        engine.upsert_document(
            asset_id="a-100", title="测试素材", content="内容"
        )
        searcher = HybridAssetSearcher(engine)
        registry = CapabilityRegistry()
        register_asset_search_tool(registry, searcher)

        actor_id = Identifier("actor-test")
        result = await registry.invoke_tool(
            "asset_search", actor_id, {"query": "测试", "limit": 5}
        )

        _assert_no_absolute_paths(result)
    finally:
        engine.close()
