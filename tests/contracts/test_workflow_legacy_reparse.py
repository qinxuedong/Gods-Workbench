"""C4-AC2 旧前端压平槽位（LEGACY_RH_LINK_SLOTS）的识别与离线重解析契约。

背景：PR #22 之前，旧前端 normalizeMatureWorkflow 保存时优先取数字 to_slot/from_slot，
把 RH link（source_type == "rh_link"）导入的 API 格式工作流里本应为输入参数名的
连线槽位压成数字 0，而 raw_payload（原始来源快照）仍保留正确命名槽位。

本文件固定四件事：
1. 纯函数判定（legacy_reparse_state）：只对可核验的 rh_link 过期数据判真，
   非 rh_link、无原始来源、槽位正确或用户合法改线的工作流一律不误判；
2. GET 单个工作流与列表只透出 needs_reparse/reparse_reason 标记，绝不改写存储；
3. POST reparse 用服务端解析逻辑从 raw_payload 离线重解析，写入新版本（+1），
   原版本作为历史版本可读回；expected_version 过期 409；无法解析时明确报错不改数据；
4. 全部断言基于持久化后回读（fixture 见 fixtures/runninghub/workflow_legacy_*.json）。
"""
from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from gw.api.app import create_app
from gw.core.errors import CleanroomException
from gw.god_workflow import routes
from gw.god_workflow.legacy_reparse import (REPARSE_REASON_LEGACY_RH_LINK_SLOTS,
                                            legacy_reparse_state)
from gw.god_workflow.parser import parse_workflow


FIXTURE_ID = "legacy_rh_link_slots_2108837566177959938"
FIXTURE = Path(__file__).parent / "fixtures" / "runninghub" / f"workflow_{FIXTURE_ID}.json"
HEADERS = {"Authorization": "Bearer legacy-reparse-user", "X-User-Role": "editor", "Origin": "http://testserver"}
OTHER_HEADERS = {**HEADERS, "Authorization": "Bearer other-legacy-user"}

#: 重解析后应恢复的命名槽位（raw_payload 中输入参数名为准）。
EXPECTED_NAMED_SLOTS = {
    ("9", "10"): ("0", "clip"),
    ("9", "11"): ("0", "clip"),
    ("9", "12"): ("0", "model"),
    ("10", "12"): ("0", "positive"),
    ("11", "12"): ("0", "negative"),
    ("13", "12"): ("0", "latent_image"),
}


@pytest.fixture
def client():
    with TestClient(create_app()) as value:
        yield value


@pytest.fixture
def legacy_document() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def _seed(document: dict) -> Path:
    """把给定文档直接写入当前主体存储，模拟 #22 前旧前端保存的落盘形态。"""
    context = routes._auth(HEADERS["Authorization"], "editor", True)
    path = routes._path(context, FIXTURE_ID)
    routes._atomic_write(path, document)
    return path


def _saved_slots(document: dict) -> dict[tuple[str, str], tuple[str, str]]:
    return {(link["source"], link["target"]): (link["source_slot"], link["target_slot"])
            for link in document["connections"]}


# ---------------------------------------------------------------------------
# 纯函数判定
# ---------------------------------------------------------------------------
def test_legacy_fixture_document_is_flagged(legacy_document):
    """#22 前旧保存形态：命名槽位被压成 0、raw_payload 命名槽位与 links 不一致。"""
    assert _saved_slots(legacy_document)["10", "12"] == ("0", "0")
    assert legacy_reparse_state(legacy_document) == (True, REPARSE_REASON_LEGACY_RH_LINK_SLOTS)


def test_slots_already_correct_are_not_flagged(legacy_document):
    """槽位正确（例如 #22 后保存或已重解析）的工作流不得误判。"""
    document = deepcopy(legacy_document)
    document["connections"] = [
        {"id": f"r{i}", "source": source, "target": target, "source_slot": slots[0], "target_slot": slots[1]}
        for i, ((source, target), slots) in enumerate(sorted(EXPECTED_NAMED_SLOTS.items()))
    ]
    assert legacy_reparse_state(document) == (False, None)


def test_non_rh_link_documents_are_never_flagged(legacy_document):
    """同样被压平的连线，只要不是 rh_link 来源就不属于本过期类别。"""
    for source_type in (None, "local_file", "liblib_link"):
        document = deepcopy(legacy_document)
        document["source_type"] = source_type
        assert legacy_reparse_state(document) == (False, None)


def test_rh_link_canvas_source_with_native_numeric_slots_is_not_flagged():
    """画布格式 RH 导入的槽位本来就是数字行号：与 raw_payload 一致即正常。"""
    graph = {"nodes": [{"id": 1, "type": "Source", "pos": [0, 0], "inputs": [],
                        "outputs": [{"name": "out", "type": "IMAGE"}]},
                       {"id": 2, "type": "Sink", "pos": [50, 50],
                        "inputs": [{"name": "images", "type": "IMAGE", "link": 5}], "outputs": []}],
             "links": [[5, 1, 0, 2, 0, "IMAGE"]]}
    document = parse_workflow(graph, source="canvas").as_dict()
    document.update(source_type="rh_link",
                    source_url="https://www.runninghub.cn/workflow/2108837566177959938")
    assert document["connections"][0]["target_slot"] == "0"
    assert legacy_reparse_state(document) == (False, None)


@pytest.mark.parametrize("raw_payload", [None, {}, "not-a-graph", {"nodes": "broken"}])
def test_unverifiable_raw_payload_is_not_flagged(legacy_document, raw_payload):
    """没有可核验的原始来源就不能下结论（也无法重解析），保持不标记。"""
    document = deepcopy(legacy_document)
    document["raw_payload"] = raw_payload
    assert legacy_reparse_state(document) == (False, None)


def test_user_rewire_to_another_named_input_is_not_flagged(legacy_document):
    """用户合法改线：保存的是命名槽位（即使与 raw_payload 不同）不是压平损坏。"""
    document = deepcopy(legacy_document)
    document["connections"] = [
        {"id": f"r{i}", "source": source, "target": target, "source_slot": slots[0], "target_slot": slots[1]}
        for i, ((source, target), slots) in enumerate(sorted(EXPECTED_NAMED_SLOTS.items()))
    ]
    rewired = next(link for link in document["connections"]
                   if (link["source"], link["target"]) == ("9", "12"))
    rewired["target_slot"] = "negative"
    assert legacy_reparse_state(document) == (False, None)


def test_partial_crush_is_still_flagged(legacy_document):
    """只有部分连线被压平（其余正确）也必须识别，重解析可整体恢复。"""
    document = deepcopy(legacy_document)
    correct = next(link for link in document["connections"]
                   if (link["source"], link["target"]) == ("10", "12"))
    correct["target_slot"] = "positive"
    assert legacy_reparse_state(document) == (True, REPARSE_REASON_LEGACY_RH_LINK_SLOTS)


# ---------------------------------------------------------------------------
# 读取标记：只透出，不落盘
# ---------------------------------------------------------------------------
def test_reads_expose_reparse_flag_without_rewriting_storage(client, legacy_document):
    path = _seed(legacy_document)
    before = path.read_bytes()
    item = client.get(f"/api/god_workflow/item/{FIXTURE_ID}", headers=HEADERS)
    assert item.status_code == 200, item.text
    assert item.json()["needs_reparse"] is True
    assert item.json()["reparse_reason"] == "LEGACY_RH_LINK_SLOTS"
    document = client.get(f"/api/god_workflow/documents/{FIXTURE_ID}", headers=HEADERS)
    assert document.status_code == 200, document.text
    body = document.json()
    assert body["needs_reparse"] is True and body["reparse_reason"] == "LEGACY_RH_LINK_SLOTS"
    assert body["document"]["needs_reparse"] is True
    assert body["workflow"]["connections"] == legacy_document["connections"]
    assert path.read_bytes() == before, "读取路径不得改写存储"


def test_list_endpoints_expose_reparse_flag(client, legacy_document):
    _seed(legacy_document)
    for url in ("/api/god_workflow/documents", "/api/god_workflow/list"):
        listed = client.get(url, headers=HEADERS)
        assert listed.status_code == 200, listed.text
        entries = (listed.json().get("document_summaries")
                   or listed.json().get("items"))
        entry = next(value for value in entries if value["workflow_id"] == FIXTURE_ID)
        assert entry["needs_reparse"] is True
        assert entry["reparse_reason"] == "LEGACY_RH_LINK_SLOTS"


def test_normal_document_reads_are_not_flagged(client):
    created = client.post("/api/god_workflow/documents", headers=HEADERS,
                          json={"1": {"class_type": "LoadImage", "inputs": {"image": "a.png"}}})
    assert created.status_code == 201, created.text
    workflow_id = created.json()["workflow_id"]
    body = client.get(f"/api/god_workflow/documents/{workflow_id}", headers=HEADERS).json()
    assert body["needs_reparse"] is False and body["reparse_reason"] is None
    listed = client.get("/api/god_workflow/list", headers=HEADERS).json()["items"]
    assert next(value for value in listed if value["workflow_id"] == workflow_id)["needs_reparse"] is False


# ---------------------------------------------------------------------------
# 重解析接口
# ---------------------------------------------------------------------------
def test_reparse_requires_expected_version(client, legacy_document):
    _seed(legacy_document)
    for body in ({}, {"expected_version": None}, {"expected_version": "1.0"}, {"expected_version": 0}):
        response = client.post(f"/api/god_workflow/workflows/{FIXTURE_ID}/reparse", headers=HEADERS, json=body)
        assert response.status_code == 400, response.text
        assert response.json()["detail"]["code"] == "INVALID_VERSION"


def test_reparse_version_conflict_returns_409_and_keeps_file(client, legacy_document):
    path = _seed(legacy_document)
    before = path.read_bytes()
    response = client.post(f"/api/god_workflow/workflows/{FIXTURE_ID}/reparse",
                           headers=HEADERS, json={"expected_version": 1})
    assert response.status_code == 409, response.text
    detail = response.json()["detail"]
    assert detail["code"] == "VERSION_CONFLICT"
    assert detail["expected_version"] == 1 and detail["current_version"] == 2
    assert path.read_bytes() == before
    assert client.get(f"/api/god_workflow/workflows/{FIXTURE_ID}/versions", headers=HEADERS).json()["versions"] == []


def test_reparse_writes_new_version_and_keeps_original_readable(client, legacy_document):
    """核心契约：离线重解析 → 版本 +1、命名槽位恢复、原版本历史可读回。"""
    path = _seed(legacy_document)
    response = client.post(f"/api/god_workflow/workflows/{FIXTURE_ID}/reparse",
                           headers=HEADERS, json={"expected_version": 2})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["reparse_performed"] is True
    assert body["version"] == 3
    assert body["needs_reparse"] is False and body["reparse_reason"] is None

    stored = json.loads(path.read_text(encoding="utf-8"))
    assert stored["version"] == 3
    assert _saved_slots(stored) == EXPECTED_NAMED_SLOTS
    assert stored["raw_payload"] == legacy_document["raw_payload"]
    assert stored["source_type"] == "rh_link"
    assert stored["source_url"] == legacy_document["source_url"]
    assert stored["workflow_id"] == FIXTURE_ID
    assert stored["metadata"]["needs_host_resolution"] is False

    read_back = client.get(f"/api/god_workflow/item/{FIXTURE_ID}", headers=HEADERS).json()
    assert read_back["version"] == 3 and read_back["needs_reparse"] is False
    assert _saved_slots(read_back) == EXPECTED_NAMED_SLOTS

    versions = client.get(f"/api/god_workflow/workflows/{FIXTURE_ID}/versions", headers=HEADERS)
    assert versions.status_code == 200, versions.text
    assert [entry["version"] for entry in versions.json()["versions"]] == [2]

    original = client.get(f"/api/god_workflow/workflows/{FIXTURE_ID}/versions/2", headers=HEADERS)
    assert original.status_code == 200, original.text
    assert original.json()["workflow"] == legacy_document
    assert original.json()["needs_reparse"] is True

    missing = client.get(f"/api/god_workflow/workflows/{FIXTURE_ID}/versions/3", headers=HEADERS)
    assert missing.status_code == 404
    assert missing.json()["detail"]["code"] == "WORKFLOW_VERSION_NOT_FOUND"
    zero = client.get(f"/api/god_workflow/workflows/{FIXTURE_ID}/versions/0", headers=HEADERS)
    assert zero.status_code == 400 and zero.json()["detail"]["code"] == "INVALID_VERSION"


def test_reparse_is_idempotent_after_success_and_stale_versions_rejected(client, legacy_document):
    _seed(legacy_document)
    first = client.post(f"/api/god_workflow/workflows/{FIXTURE_ID}/reparse",
                        headers=HEADERS, json={"expected_version": 2})
    assert first.status_code == 200 and first.json()["version"] == 3
    stale = client.post(f"/api/god_workflow/workflows/{FIXTURE_ID}/reparse",
                        headers=HEADERS, json={"expected_version": 2})
    assert stale.status_code == 409 and stale.json()["detail"]["code"] == "VERSION_CONFLICT"
    again = client.post(f"/api/god_workflow/workflows/{FIXTURE_ID}/reparse",
                        headers=HEADERS, json={"expected_version": 3})
    assert again.status_code == 200, again.text
    assert again.json()["reparse_performed"] is False
    assert again.json()["version"] == 3 and again.json()["needs_reparse"] is False


def test_reparse_without_recoverable_source_returns_explicit_error(client, legacy_document, monkeypatch):
    """无法重新解析时返回明确错误码，且不静默修改任何数据。"""
    path = _seed(legacy_document)
    before = path.read_bytes()

    def broken(payload, source, name):
        raise CleanroomException(400, "INVALID_WORKFLOW", "原始来源无法解析")

    monkeypatch.setattr(routes, "_parse_payload", broken)
    response = client.post(f"/api/god_workflow/workflows/{FIXTURE_ID}/reparse",
                           headers=HEADERS, json={"expected_version": 2})
    assert response.status_code == 422, response.text
    detail = response.json()["detail"]
    assert detail["code"] == "REPARSE_SOURCE_UNAVAILABLE"
    assert path.read_bytes() == before
    assert client.get(f"/api/god_workflow/workflows/{FIXTURE_ID}/versions", headers=HEADERS).json()["versions"] == []


def test_reparse_and_history_are_principal_scoped(client, legacy_document):
    _seed(legacy_document)
    other_item = client.get(f"/api/god_workflow/item/{FIXTURE_ID}", headers=OTHER_HEADERS)
    assert other_item.status_code == 404
    other_versions = client.get(f"/api/god_workflow/workflows/{FIXTURE_ID}/versions", headers=OTHER_HEADERS)
    assert other_versions.status_code == 200
    assert other_versions.json()["versions"] == []
    other_reparse = client.post(f"/api/god_workflow/workflows/{FIXTURE_ID}/reparse",
                                headers=OTHER_HEADERS, json={"expected_version": 2})
    assert other_reparse.status_code == 404


def test_delete_document_cleans_up_version_history(client, legacy_document):
    _seed(legacy_document)
    reparsed = client.post(f"/api/god_workflow/workflows/{FIXTURE_ID}/reparse",
                           headers=HEADERS, json={"expected_version": 2})
    assert reparsed.status_code == 200, reparsed.text
    deleted = client.request("DELETE", f"/api/god_workflow/documents/{FIXTURE_ID}",
                             headers=HEADERS, json={"expected_version": 3})
    assert deleted.status_code == 200, deleted.text
    assert client.get(f"/api/god_workflow/workflows/{FIXTURE_ID}/versions", headers=HEADERS).json()["versions"] == []
    assert client.get(f"/api/god_workflow/workflows/{FIXTURE_ID}/versions/2", headers=HEADERS).status_code == 404
