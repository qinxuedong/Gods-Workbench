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

"""MVP2-T1b 参考策展写路径契约测试（QIN-464 / GitHub #422）。

覆盖 AC1～AC5：增改查往返与同值幂等、``expected_version`` 冲突 409、项目上下文
缺失/不符 fail closed、同一 SQLite 重建后完整恢复、响应零绝对路径；另覆盖未认证
401、备注与排序入参约束、进程重启后续写。夹具数据在本文件内构造，不访问网络，
也不触碰真实运行态数据。
"""

from __future__ import annotations

import re
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
    ReferenceStore,
    ReferenceWriteRequest,
)
from gw.agent_runtime.storage.sqlite import SQLiteDatabase
from gw.api.app import create_app
from gw.api.routes_agent_references import get_reference_store

ROOT = Path(__file__).resolve().parents[3]
CATALOG = ROOT / "docs/contracts/AGENT-WORKBENCH-INTERFACE-CATALOG.yaml"
AUTH = {"Authorization": "Bearer cleanroom-test", "X-User-Role": "editor"}
ENDPOINT = "/api/agent/sessions/{session_id}/references/{asset_id}"
READ_ENDPOINT = "/api/agent/sessions/{session_id}/references"
SESSION_ID = "ags-0001"
PROJECT_ID = "prj-0001"
OTHER_PROJECT_ID = "prj-0002"


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


def _put(
    client: TestClient,
    *,
    asset_id: str,
    expected_version: int = 1,
    project_id: str = PROJECT_ID,
    decision: str = ReferenceDecision.selected.value,
    note: str | None = None,
    order: int = 0,
    session_id: str = SESSION_ID,
    headers: dict[str, str] | None = AUTH,
    payload: dict[str, Any] | None = None,
):
    """统一构造 PUT 请求；``payload`` 给定时原样发送，用于入参非法用例。"""
    body = payload
    if body is None:
        body = {
            "project_id": project_id,
            "decision": decision,
            "note": note,
            "order": order,
            "expected_version": expected_version,
        }
    return client.put(
        ENDPOINT.format(session_id=session_id, asset_id=asset_id),
        json=body,
        headers=headers,
    )


@pytest.fixture
def store(tmp_path: Path) -> ReferenceStore:
    """每个用例独占一个 SQLite 文件，绝不触碰真实运行态数据。"""
    return ReferenceStore(
        SQLiteDatabase(tmp_path / "references.sqlite3", trusted_root=tmp_path)
    )


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    """夹具台账文件路径，供重建（模拟重启）用例复用。"""
    return tmp_path / "references.sqlite3"


@pytest.fixture
def client(store: ReferenceStore):
    """把读写端点注入到用例自己的台账上，隔离真实数据根。"""
    app = create_app()
    app.dependency_overrides[get_reference_store] = lambda: store
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def test_upsert_then_read_roundtrip(client: TestClient, store: ReferenceStore) -> None:
    """AC1：新增条目后 PUT 与 GET 往返一致，且按 ``order`` 升序回读。"""
    store.create_session(session_id=SESSION_ID, project_id=PROJECT_ID)

    first = _put(client, asset_id="ast-002", order=1, decision="pending", expected_version=1)
    second = _put(
        client,
        asset_id="ast-001",
        order=0,
        decision="selected",
        note="开场参考",
        expected_version=2,
    )

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["version"] == 2  # 初始 1 → 首次写入后 2
    assert second.json()["version"] == 3  # 每次实质变更推进版本号

    body = client.get(READ_ENDPOINT.format(session_id=SESSION_ID), headers=AUTH).json()
    assert [item["asset_id"] for item in body["items"]] == ["ast-001", "ast-002"]
    assert [item["decision"] for item in body["items"]] == ["selected", "pending"]
    assert body["items"][0]["note"] == "开场参考"
    assert body["items"][1]["note"] is None
    assert body["version"] == 3
    _assert_no_absolute_paths(body)


def test_repeated_identical_put_is_idempotent(
    client: TestClient, store: ReferenceStore
) -> None:
    """AC1：同值重复 PUT 幂等 —— version 与 updated_at 都不变。"""
    store.create_session(session_id=SESSION_ID, project_id=PROJECT_ID)

    first = _put(client, asset_id="ast-001", order=0, note="保留", expected_version=1)
    # 第二次用**写入后**的当前版本重放，仍属同值重复写入。
    second = _put(client, asset_id="ast-001", order=0, note="保留", expected_version=2)

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["version"] == 2
    assert second.json() == first.json()


def test_changing_decision_note_or_order_bumps_version(
    client: TestClient, store: ReferenceStore
) -> None:
    """AC1：改决策/备注/排序任一字段都推进版本；改回原值同样算一次变更。"""
    store.create_session(session_id=SESSION_ID, project_id=PROJECT_ID)

    _put(client, asset_id="ast-001", order=0, decision="selected", note="A", expected_version=1)
    rejected = _put(
        client,
        asset_id="ast-001",
        order=0,
        decision="rejected",
        note="A",
        expected_version=2,
    )
    reordered = _put(
        client,
        asset_id="ast-001",
        order=2,
        decision="rejected",
        note="A",
        expected_version=3,
    )
    renamed = _put(
        client,
        asset_id="ast-001",
        order=2,
        decision="rejected",
        note="B",
        expected_version=4,
    )

    assert rejected.json()["version"] == 3
    assert reordered.json()["version"] == 4
    assert renamed.json()["version"] == 5
    assert reordered.json()["items"][0]["order"] == 2


def test_stale_expected_version_returns_409(client: TestClient, store: ReferenceStore) -> None:
    """AC2：``expected_version`` 与当前版本不符返回 409 ``VERSION_CONFLICT`` 且不落库。"""
    store.create_session(session_id=SESSION_ID, project_id=PROJECT_ID)
    _put(client, asset_id="ast-001", order=0, decision="selected")

    conflict = _put(client, asset_id="ast-002", order=1, expected_version=1)

    assert conflict.status_code == 409
    detail = conflict.json()["detail"]
    assert detail["code"] == "VERSION_CONFLICT"
    assert detail["expected_version"] == 1
    assert detail["current_version"] == 2

    body = client.get(READ_ENDPOINT.format(session_id=SESSION_ID), headers=AUTH).json()
    assert [item["asset_id"] for item in body["items"]] == ["ast-001"]
    assert body["version"] == 2


def test_missing_project_id_is_rejected(client: TestClient, store: ReferenceStore) -> None:
    """AC3：缺 ``project_id`` fail closed（400），不隐式创建会话或条目。"""
    store.create_session(session_id=SESSION_ID, project_id=PROJECT_ID)

    response = _put(
        client,
        asset_id="ast-001",
        expected_version=1,
        payload={"decision": "selected", "note": None, "order": 0},
    )

    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "INVALID_REQUEST"


def test_blank_project_id_is_rejected(client: TestClient, store: ReferenceStore) -> None:
    """AC3：空白 ``project_id`` 等同缺失，一律 fail closed。"""
    store.create_session(session_id=SESSION_ID, project_id=PROJECT_ID)

    response = _put(
        client,
        asset_id="ast-001",
        expected_version=1,
        payload={
            "project_id": "  ",
            "decision": "selected",
            "note": None,
            "order": 0,
            "expected_version": 1,
        },
    )

    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "PROJECT_CONTEXT_REQUIRED"


def test_project_mismatch_is_rejected(client: TestClient, store: ReferenceStore) -> None:
    """AC3：``project_id`` 与会话绑定不一致 → 409，数据保持原样。"""
    store.create_session(session_id=SESSION_ID, project_id=PROJECT_ID)
    _put(client, asset_id="ast-001", order=0, decision="selected")

    mismatched = _put(
        client,
        asset_id="ast-002",
        order=1,
        project_id=OTHER_PROJECT_ID,
        expected_version=2,
    )

    assert mismatched.status_code == 409
    assert mismatched.json()["detail"]["code"] == "PROJECT_CONTEXT_MISMATCH"

    body = client.get(READ_ENDPOINT.format(session_id=SESSION_ID), headers=AUTH).json()
    assert [item["asset_id"] for item in body["items"]] == ["ast-001"]
    assert body["version"] == 2


def test_unknown_session_is_not_created(client: TestClient) -> None:
    """AC3：会话不存在 → 404 ``SESSION_NOT_FOUND``，绝不隐式创建。"""
    response = _put(
        client,
        asset_id="ast-001",
        session_id="ags-missing",
        expected_version=1,
    )

    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "SESSION_NOT_FOUND"
    assert client.get(
        READ_ENDPOINT.format(session_id="ags-missing"), headers=AUTH
    ).status_code == 404


def test_unauthenticated_write_is_rejected(client: TestClient, store: ReferenceStore) -> None:
    """写端点沿用全站鉴权口径：未认证一律 401。"""
    store.create_session(session_id=SESSION_ID, project_id=PROJECT_ID)

    assert _put(client, asset_id="ast-001", expected_version=1, headers=None).status_code == 401


def test_write_request_rejects_unknown_fields() -> None:
    """请求模型 ``extra="forbid"``，未知字段（含路径类字段）一律拒绝。"""
    with pytest.raises(ValidationError):
        ReferenceWriteRequest(
            project_id=PROJECT_ID,
            decision=ReferenceDecision.selected,
            note=None,
            order=0,
            expected_version=1,
            absolute_path="/srv/gw/references.sqlite3",
        )


def test_write_request_enforces_field_constraints() -> None:
    """备注 ≤ 500、``order`` ≥ 0、``expected_version`` ≥ 1。"""
    valid = {
        "project_id": PROJECT_ID,
        "decision": ReferenceDecision.selected,
        "note": None,
        "order": 0,
        "expected_version": 1,
    }
    assert ReferenceWriteRequest(**valid).decision is ReferenceDecision.selected

    with pytest.raises(ValidationError):
        ReferenceWriteRequest(**{**valid, "project_id": ""})
    with pytest.raises(ValidationError):
        ReferenceWriteRequest(**{**valid, "note": "x" * (NOTE_MAX_LENGTH + 1)})
    with pytest.raises(ValidationError):
        ReferenceWriteRequest(**{**valid, "order": -1})
    with pytest.raises(ValidationError):
        ReferenceWriteRequest(**{**valid, "expected_version": 0})
    with pytest.raises(ValidationError):
        ReferenceWriteRequest(**{**valid, "decision": "approved"})

    assert ReferenceWriteRequest(**{**valid, "note": "x" * NOTE_MAX_LENGTH}).note is not None


def test_write_request_rejects_extra_fields(client: TestClient, store: ReferenceStore) -> None:
    """HTTP 层同样拒绝额外字段，不静默丢弃。"""
    store.create_session(session_id=SESSION_ID, project_id=PROJECT_ID)

    response = _put(
        client,
        asset_id="ast-001",
        expected_version=1,
        payload={
            "project_id": PROJECT_ID,
            "decision": "selected",
            "note": None,
            "order": 0,
            "expected_version": 1,
            "absolute_path": "/srv/gw/references.sqlite3",
        },
    )

    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "INVALID_REQUEST"


def test_rebuilt_store_replays_write_and_recovers(
    tmp_path: Path, db_path: Path, client: TestClient, store: ReferenceStore
) -> None:
    """AC4：进程重启后同一 SQLite 文件重建台账，数据完整且可续写。"""
    store.create_session(session_id=SESSION_ID, project_id=PROJECT_ID)
    _put(client, asset_id="ast-001", order=0, decision="selected", note="保留")
    expected = store.get(SESSION_ID)
    assert expected is not None

    reopened = ReferenceStore(SQLiteDatabase(db_path, trusted_root=tmp_path))
    recovered = reopened.get(SESSION_ID)
    assert recovered is not None
    assert recovered.model_dump(mode="json") == expected.model_dump(mode="json")

    # 重建后的台账继续承接写路径：用当前版本幂等重放，版本号不变。
    app = create_app()
    app.dependency_overrides[get_reference_store] = lambda: reopened
    with TestClient(app) as restarted:
        replay = _put(
            restarted,
            asset_id="ast-001",
            order=0,
            decision="selected",
            note="保留",
            expected_version=recovered.version,
        )
        added = _put(
            restarted,
            asset_id="ast-002",
            order=1,
            decision="pending",
            expected_version=recovered.version,
        )
    app.dependency_overrides.clear()

    assert replay.status_code == 200
    assert replay.json()["version"] == recovered.version
    assert added.status_code == 200
    assert added.json()["version"] == recovered.version + 1
    assert [item["asset_id"] for item in added.json()["items"]] == ["ast-001", "ast-002"]


def test_write_response_contains_no_absolute_paths(
    client: TestClient, store: ReferenceStore
) -> None:
    """AC5：写响应结构与字段值都不含本地绝对路径。"""
    store.create_session(session_id=SESSION_ID, project_id=PROJECT_ID)

    body = _put(client, asset_id="ast-001", order=0, note="外部链接见项目资产库").json()

    _assert_no_absolute_paths(body)
    assert str(store.database.path) not in str(body)


def test_interface_catalog_declares_write_endpoint() -> None:
    """新 HTTP API 已登记到 Agent 接口目录，避免契约漂移。"""
    catalog: dict[str, Any] = yaml.safe_load(CATALOG.read_text(encoding="utf-8"))
    declared = {
        (entry["method"], entry["path"])
        for entry in catalog["interfaces"]
        if "method" in entry and "path" in entry
    }

    assert (
        "PUT",
        "/api/agent/sessions/{session_id}/references/{asset_id}",
    ) in declared
