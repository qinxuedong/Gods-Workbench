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

"""MVP2-T1a 参考策展会话模型与读 API 契约测试（QIN-463 / GitHub #421）。

覆盖 AC1～AC5：GET 往返与空集、会话不存在 404、同一 SQLite 重建后恢复、
响应零绝对路径、模型 ``extra="forbid"`` 与字段约束。夹具数据在本文件内直接
构造，不依赖 QIN-439 的素材检索能力，也不访问网络或真实用户数据。
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest
import yaml
from fastapi.testclient import TestClient
from pydantic import ValidationError

from gw.agent_episode.references import (
    NOTE_MAX_LENGTH,
    ReferenceDecision,
    ReferenceItem,
    ReferenceSet,
    ReferenceStore,
)
from gw.agent_runtime.errors import DomainError
from gw.agent_runtime.models import ErrorCode
from gw.agent_runtime.storage.sqlite import SQLiteDatabase
from gw.api.app import create_app
from gw.api.routes_agent_references import get_reference_store
from gw.core.runtime_paths import resolve_runtime_paths

ROOT = Path(__file__).resolve().parents[3]
CATALOG = ROOT / "docs/contracts/AGENT-WORKBENCH-INTERFACE-CATALOG.yaml"
AUTH = {"Authorization": "Bearer cleanroom-test", "X-User-Role": "editor"}
ENDPOINT = "/api/agent/sessions/{session_id}/references"
SESSION_ID = "ags-0001"
PROJECT_ID = "prj-0001"


def _assert_no_absolute_paths(data: object) -> None:
    """递归断言结构中任何字符串都不以 ``/`` 或盘符形式携带绝对路径。"""
    if isinstance(data, str):
        assert not data.startswith("/"), f"字符串以 '/' 开头: {data}"
        assert not re.match(r"^[a-zA-Z]:[\\/]", data), f"字符串含 Windows 盘符路径: {data}"
    elif isinstance(data, dict):
        for key, value in data.items():
            _assert_no_absolute_paths(key)
            _assert_no_absolute_paths(value)
    elif isinstance(data, (list, tuple)):
        for entry in data:
            _assert_no_absolute_paths(entry)


@pytest.fixture
def store(tmp_path: Path) -> ReferenceStore:
    """每个用例独占一个 SQLite 文件，绝不触碰真实运行态数据。"""
    return ReferenceStore(
        SQLiteDatabase(tmp_path / "references.sqlite3", trusted_root=tmp_path)
    )


@pytest.fixture
def client(store: ReferenceStore):
    """把读端点注入到用例自己的台账上，隔离真实数据根。"""
    app = create_app()
    app.dependency_overrides[get_reference_store] = lambda: store
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def _seed_item(
    store: ReferenceStore,
    *,
    asset_id: str,
    order: int,
    decision: str = ReferenceDecision.pending.value,
    note: str | None = None,
    updated_at: str = "2026-10-28T08:00:00+00:00",
    session_id: str = SESSION_ID,
) -> None:
    """按台账表结构写入夹具条目；写路径属 MVP2-T1b，本票不提供写方法。"""
    with store.database.connect() as connection:
        connection.execute(
            "INSERT INTO agent_reference_items"
            "(session_id, asset_id, decision, note, order_index, updated_at)"
            " VALUES(?, ?, ?, ?, ?, ?)",
            (session_id, asset_id, decision, note, order, updated_at),
        )


def test_get_returns_items_in_order(client: TestClient, store: ReferenceStore) -> None:
    """AC1：GET 往返正确，条目按 ``order`` 升序回读。"""
    store.create_session(session_id=SESSION_ID, project_id=PROJECT_ID)
    _seed_item(store, asset_id="ast-003", order=2, decision="rejected")
    _seed_item(store, asset_id="ast-001", order=0, decision="selected", note="开场参考")
    _seed_item(store, asset_id="ast-002", order=1)

    response = client.get(ENDPOINT.format(session_id=SESSION_ID), headers=AUTH)

    assert response.status_code == 200
    body = response.json()
    assert body["session_id"] == SESSION_ID
    assert body["project_id"] == PROJECT_ID
    assert body["version"] == 1
    assert [item["asset_id"] for item in body["items"]] == ["ast-001", "ast-002", "ast-003"]
    assert [item["decision"] for item in body["items"]] == ["selected", "pending", "rejected"]
    assert body["items"][0]["note"] == "开场参考"
    assert body["items"][1]["note"] is None
    assert datetime.fromisoformat(body["items"][0]["updated_at"]) == datetime.fromisoformat(
        "2026-10-28T08:00:00+00:00"
    )


def test_empty_session_returns_legal_empty_items(
    client: TestClient, store: ReferenceStore
) -> None:
    """AC1：空参考集的会话返回合法空 ``items``，而不是 404。"""
    store.create_session(session_id=SESSION_ID, project_id=PROJECT_ID)

    response = client.get(ENDPOINT.format(session_id=SESSION_ID), headers=AUTH)

    assert response.status_code == 200
    body = response.json()
    assert body["items"] == []
    assert body["version"] == 1


def test_unknown_session_returns_404(client: TestClient) -> None:
    """AC2：会话不存在返回 404 ``SESSION_NOT_FOUND``，且不隐式创建会话。"""
    response = client.get(ENDPOINT.format(session_id="ags-missing"), headers=AUTH)

    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "SESSION_NOT_FOUND"


def test_unauthenticated_request_is_rejected(client: TestClient, store: ReferenceStore) -> None:
    """数据端点沿用全站鉴权口径：未认证一律 401。"""
    store.create_session(session_id=SESSION_ID, project_id=PROJECT_ID)

    assert client.get(ENDPOINT.format(session_id=SESSION_ID)).status_code == 401


def test_rebuilt_repository_recovers_from_same_file(
    tmp_path: Path, store: ReferenceStore
) -> None:
    """AC3：用同一 SQLite 文件重建台账后，会话绑定、条目与版本号完整恢复。"""
    store.create_session(session_id=SESSION_ID, project_id=PROJECT_ID)
    _seed_item(store, asset_id="ast-001", order=0, decision="selected", note="保留")
    _seed_item(store, asset_id="ast-002", order=1, decision="pending")
    expected = store.get(SESSION_ID)
    assert expected is not None

    reopened = ReferenceStore(
        SQLiteDatabase(tmp_path / "references.sqlite3", trusted_root=tmp_path)
    )
    recovered = reopened.get(SESSION_ID)

    assert recovered is not None
    assert recovered.model_dump(mode="json") == expected.model_dump(mode="json")
    # 重建后的台账同样可应答 HTTP 读请求（模拟进程重启后的续读）。
    app = create_app()
    app.dependency_overrides[get_reference_store] = lambda: reopened
    with TestClient(app) as restarted:
        response = restarted.get(ENDPOINT.format(session_id=SESSION_ID), headers=AUTH)
    app.dependency_overrides.clear()

    assert response.status_code == 200
    assert [item["asset_id"] for item in response.json()["items"]] == ["ast-001", "ast-002"]


def test_response_contains_no_absolute_paths(client: TestClient, store: ReferenceStore) -> None:
    """AC4：响应结构与字段值都不含本地绝对路径。"""
    store.create_session(session_id=SESSION_ID, project_id=PROJECT_ID)
    _seed_item(store, asset_id="ast-001", order=0, note="外部链接见项目资产库")

    body = client.get(ENDPOINT.format(session_id=SESSION_ID), headers=AUTH).json()

    _assert_no_absolute_paths(body)
    assert str(store.database.path) not in str(body)


def test_models_forbid_unknown_fields() -> None:
    """AC5：模型一律 ``extra="forbid"``，未知字段拒绝而不是静默丢弃。"""
    with pytest.raises(ValidationError):
        ReferenceItem(
            asset_id="ast-001",
            order=0,
            updated_at="2026-10-28T08:00:00+00:00",
            absolute_path="/tmp/secret.sqlite3",
        )
    with pytest.raises(ValidationError):
        ReferenceSet(
            session_id=SESSION_ID,
            project_id=PROJECT_ID,
            items=[],
            version=1,
            workspace_root="D:/secret",
        )


def test_model_field_constraints() -> None:
    """AC5：``asset_id`` 非空、``note`` ≤500、``order`` ≥0、``version`` ≥1。"""
    valid = {
        "asset_id": "ast-001",
        "decision": ReferenceDecision.selected,
        "note": "参考" * 10,
        "order": 0,
        "updated_at": "2026-10-28T08:00:00+00:00",
    }
    assert ReferenceItem(**valid).decision is ReferenceDecision.selected

    with pytest.raises(ValidationError):
        ReferenceItem(**{**valid, "asset_id": ""})
    with pytest.raises(ValidationError):
        ReferenceItem(**{**valid, "note": "x" * (NOTE_MAX_LENGTH + 1)})
    with pytest.raises(ValidationError):
        ReferenceItem(**{**valid, "order": -1})
    with pytest.raises(ValidationError):
        ReferenceItem(**{**valid, "decision": "approved"})
    with pytest.raises(ValidationError):
        ReferenceSet(session_id=SESSION_ID, project_id=PROJECT_ID, items=[], version=0)

    assert ReferenceItem(**{**valid, "note": "x" * NOTE_MAX_LENGTH}).note is not None


def test_duplicate_session_registration_is_rejected(store: ReferenceStore) -> None:
    """会话登记不静默覆盖既有项目绑定。"""
    store.create_session(session_id=SESSION_ID, project_id=PROJECT_ID)

    with pytest.raises(DomainError) as excinfo:
        store.create_session(session_id=SESSION_ID, project_id="prj-0002")

    assert excinfo.value.code is ErrorCode.VALIDATION_FAILED
    unchanged = store.get(SESSION_ID)
    assert unchanged is not None
    assert unchanged.project_id == PROJECT_ID
    assert unchanged.version == 1


def test_default_store_follows_runtime_data_root() -> None:
    """未覆盖依赖时台账落在运行态数据根 ``/agent/`` 下（三态隔离）。

    会话号带随机后缀：开发沙盒台账跨次运行保留，避免重跑时撞重复登记。
    """
    seeded = get_reference_store()
    session_id = f"{SESSION_ID}-{uuid.uuid4().hex[:8]}"
    seeded.create_session(session_id=session_id, project_id=PROJECT_ID)
    data_root = resolve_runtime_paths().data_root

    with TestClient(create_app()) as client:
        response = client.get(ENDPOINT.format(session_id=session_id), headers=AUTH)

    assert response.status_code == 200
    assert Path(seeded.database.path) == (data_root / "agent" / "references.sqlite3").resolve()


def test_interface_catalog_declares_read_endpoint() -> None:
    """新 HTTP API 已登记到 Agent 接口目录，避免契约漂移。"""
    catalog: dict[str, Any] = yaml.safe_load(CATALOG.read_text(encoding="utf-8"))
    declared = {
        (entry["method"], entry["path"])
        for entry in catalog["interfaces"]
        if "method" in entry and "path" in entry
    }

    assert ("GET", "/api/agent/sessions/{session_id}/references") in declared
