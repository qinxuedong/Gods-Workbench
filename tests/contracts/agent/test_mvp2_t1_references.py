"""MVP2-T1 参考策展会话模型与 API 契约测试。"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from gw.agent_episode.references import SQLiteReferenceRepository
from gw.agent_runtime.storage.sqlite import SQLiteDatabase
from gw.api.app import create_app


@pytest.fixture
def temp_db(tmp_path: Path) -> SQLiteDatabase:
    db_file = tmp_path / "test_references.sqlite3"
    return SQLiteDatabase(db_file, trusted_root=tmp_path)


@pytest.fixture
def client(temp_db: SQLiteDatabase) -> TestClient:
    app = create_app()
    app.state.reference_repository = SQLiteReferenceRepository(temp_db)
    return TestClient(app)


def test_ac1_crud_roundtrip_and_idempotency(client: TestClient):
    session_id = "session-test-001"
    project_id = "proj-mvp2-01"

    # 1. 初始 GET -> 404 (AC3)
    get_res = client.get(f"/api/agent/sessions/{session_id}/references")
    assert get_res.status_code == 404
    assert get_res.json()["detail"]["code"] == "SESSION_NOT_FOUND"

    # 2. 新建参考项 (PUT version 0 -> 1)
    put_payload_1 = {
        "project_id": project_id,
        "decision": "selected",
        "note": "初选视角剧照",
        "order": 0,
        "expected_version": 0,
    }
    put_res_1 = client.put(
        f"/api/agent/sessions/{session_id}/references/asset-101",
        json=put_payload_1,
    )
    assert put_res_1.status_code == 200
    data_1 = put_res_1.json()
    assert data_1["session_id"] == session_id
    assert data_1["project_id"] == project_id
    assert data_1["version"] == 1
    assert len(data_1["items"]) == 1
    item_1 = data_1["items"][0]
    assert item_1["asset_id"] == "asset-101"
    assert item_1["decision"] == "selected"
    assert item_1["note"] == "初选视角剧照"
    assert item_1["order"] == 0

    # 3. GET 读取校验
    get_res_1 = client.get(f"/api/agent/sessions/{session_id}/references")
    assert get_res_1.status_code == 200
    assert get_res_1.json() == data_1

    # 4. 修改已有项 (PUT version 1 -> 2)
    put_payload_2 = {
        "project_id": project_id,
        "decision": "rejected",
        "note": "色调不符，放弃",
        "order": 1,
        "expected_version": 1,
    }
    put_res_2 = client.put(
        f"/api/agent/sessions/{session_id}/references/asset-101",
        json=put_payload_2,
    )
    assert put_res_2.status_code == 200
    data_2 = put_res_2.json()
    assert data_2["version"] == 2
    assert len(data_2["items"]) == 1
    assert data_2["items"][0]["decision"] == "rejected"
    assert data_2["items"][0]["note"] == "色调不符，放弃"
    assert data_2["items"][0]["order"] == 1

    # 5. 追加第二个参考项 (PUT version 2 -> 3)
    put_payload_3 = {
        "project_id": project_id,
        "decision": "selected",
        "note": "替代镜头",
        "order": 2,
        "expected_version": 2,
    }
    put_res_3 = client.put(
        f"/api/agent/sessions/{session_id}/references/asset-102",
        json=put_payload_3,
    )
    assert put_res_3.status_code == 200
    data_3 = put_res_3.json()
    assert data_3["version"] == 3
    assert len(data_3["items"]) == 2

    # 6. 相同值重复 PUT -> 幂等，version 保持为 3
    put_res_repeat = client.put(
        f"/api/agent/sessions/{session_id}/references/asset-102",
        json={
            "project_id": project_id,
            "decision": "selected",
            "note": "替代镜头",
            "order": 2,
            "expected_version": 3,
        },
    )
    assert put_res_repeat.status_code == 200
    data_repeat = put_res_repeat.json()
    assert data_repeat["version"] == 3
    assert data_repeat == data_3


def test_ac2_version_conflict_returns_409(client: TestClient):
    session_id = "session-version-conflict"
    project_id = "proj-mvp2-02"

    # 先建 1 条，此时版本为 1
    put_res = client.put(
        f"/api/agent/sessions/{session_id}/references/asset-201",
        json={
            "project_id": project_id,
            "decision": "pending",
            "note": "待定素材",
            "order": 0,
            "expected_version": 0,
        },
    )
    assert put_res.status_code == 200
    assert put_res.json()["version"] == 1

    # 用错误的 expected_version=0 提交 -> 409
    conflict_res = client.put(
        f"/api/agent/sessions/{session_id}/references/asset-201",
        json={
            "project_id": project_id,
            "decision": "selected",
            "note": "尝试改动",
            "order": 0,
            "expected_version": 0,
        },
    )
    assert conflict_res.status_code == 409
    detail = conflict_res.json()["detail"]
    assert detail["code"] == "VERSION_CONFLICT"
    assert detail["expected_version"] == 0
    assert detail["current_version"] == 1


def test_ac3_session_not_found_and_fail_closed_project_context(client: TestClient):
    # 1. 会话不存在 GET -> 404
    get_res = client.get("/api/agent/sessions/non-existent-session/references")
    assert get_res.status_code == 404
    assert get_res.json()["detail"]["code"] == "SESSION_NOT_FOUND"

    # 2. PUT 缺失 / 空 project_id -> 400
    bad_put = client.put(
        "/api/agent/sessions/session-fail-closed/references/asset-301",
        json={
            "project_id": "",
            "decision": "selected",
            "note": "无项目",
            "order": 0,
            "expected_version": 0,
        },
    )
    assert bad_put.status_code == 400

    # 3. 初始化绑项目 proj-A
    bind_res = client.put(
        "/api/agent/sessions/session-bound/references/asset-301",
        json={
            "project_id": "proj-A",
            "decision": "selected",
            "note": "绑定项目 A",
            "order": 0,
            "expected_version": 0,
        },
    )
    assert bind_res.status_code == 200

    # 4. 用不同项目 proj-B 提交 -> 409 fail closed
    mismatch_res = client.put(
        "/api/agent/sessions/session-bound/references/asset-301",
        json={
            "project_id": "proj-B",
            "decision": "selected",
            "note": "企图跨项目修改",
            "order": 0,
            "expected_version": 1,
        },
    )
    assert mismatch_res.status_code == 409
    assert mismatch_res.json()["detail"]["code"] == "PROJECT_MISMATCH"


def test_ac4_durable_recovery_across_repo_reinstantiation(tmp_path: Path):
    db_file = tmp_path / "restart_test.sqlite3"
    session_id = "session-restart-01"
    project_id = "proj-restart"

    # 1. 第一个 repo 实例，写入 2 条记录
    repo1 = SQLiteReferenceRepository(
        SQLiteDatabase(db_file, trusted_root=tmp_path)
    )
    set1 = repo1.put_reference_item(
        session_id=session_id,
        asset_id="asset-restart-1",
        project_id=project_id,
        decision="selected",
        note="重启前记录 1",
        order=0,
        expected_version=0,
    )
    assert set1.version == 1

    set2 = repo1.put_reference_item(
        session_id=session_id,
        asset_id="asset-restart-2",
        project_id=project_id,
        decision="rejected",
        note="重启前记录 2",
        order=1,
        expected_version=1,
    )
    assert set2.version == 2

    # 2. 模拟重启：丢弃 repo1，用同一 sqlite 文件重建 repo2
    repo2 = SQLiteReferenceRepository(
        SQLiteDatabase(db_file, trusted_root=tmp_path)
    )
    recovered = repo2.get_reference_set(session_id)
    assert recovered is not None
    assert recovered.session_id == session_id
    assert recovered.project_id == project_id
    assert recovered.version == 2
    assert len(recovered.items) == 2
    assert recovered.items[0].asset_id == "asset-restart-1"
    assert recovered.items[0].decision == "selected"
    assert recovered.items[1].asset_id == "asset-restart-2"
    assert recovered.items[1].decision == "rejected"


def test_ac5_response_contains_no_absolute_paths(client: TestClient):
    session_id = "session-ac5-test"
    project_id = "proj-ac5"

    put_res = client.put(
        f"/api/agent/sessions/{session_id}/references/asset-501",
        json={
            "project_id": project_id,
            "decision": "selected",
            "note": "检查绝对路径",
            "order": 0,
            "expected_version": 0,
        },
    )
    assert put_res.status_code == 200
    raw_put_text = put_res.text
    assert os.sep + "home" not in raw_put_text
    assert os.sep + "tmp" not in raw_put_text
    assert "C:\\" not in raw_put_text

    get_res = client.get(f"/api/agent/sessions/{session_id}/references")
    assert get_res.status_code == 200
    raw_get_text = get_res.text
    assert os.sep + "home" not in raw_get_text
    assert os.sep + "tmp" not in raw_get_text
    assert "C:\\" not in raw_get_text
