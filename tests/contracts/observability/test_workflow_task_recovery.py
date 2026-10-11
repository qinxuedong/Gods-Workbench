"""工作流查询故障不能伪造执行终态，修复配置后续查同一个远端任务。"""
from __future__ import annotations

import asyncio
import io

import httpx
import pytest
from fastapi.testclient import TestClient

from gw.api.app import create_app
from gw.core.errors import CleanroomException
from gw.god_workflow import platforms, registry, routes


HEADERS = {"Authorization": "Bearer workflow-recovery", "Origin": "http://testserver"}


@pytest.fixture
def client():
    with TestClient(create_app()) as value:
        yield value


def cloud_task(target="rh_cloud"):
    context = routes._auth(HEADERS["Authorization"], "editor", True)
    task = registry.create_task(context, workflow_id="saved-workflow", prompt_id="remote-one", target=target)
    return context, task


def successful_collection(monkeypatch):
    calls = []

    async def query(remote_task_id, api_key, **kwargs):
        calls.append((remote_task_id, api_key))
        return {"status": "SUCCESS", "outputs": [{"fileName": "result.png"}]}

    async def collect(context, task_id, outputs):
        return [{"asset_id": "stable-output", "registered": True}]

    async def forbidden(*args, **kwargs):
        pytest.fail("续查/回收不能再次生成或误调本地引擎")

    monkeypatch.setattr(platforms, "query_rh_task_outputs", query)
    monkeypatch.setattr(routes, "_collect_rh_task_outputs", collect)
    monkeypatch.setattr(platforms, "create_rh_cloud_task", forbidden)
    monkeypatch.setattr(routes.comfy_executor, "history", forbidden)
    return calls


def test_missing_cloud_key_preserves_task_and_recovers_same_remote_id(client, monkeypatch):
    context, task = cloud_task()
    before = registry.load_task(context, task["job_id"])
    calls = successful_collection(monkeypatch)
    key = {"value": ""}
    monkeypatch.setattr(registry, "credential_value", lambda *args: key["value"])
    url = "/api/god_workflow/tasks/" + task["job_id"]
    missing = client.get(url, headers=HEADERS)
    assert missing.status_code == 503, missing.text
    assert registry.load_task(context, task["job_id"]) == before
    assert calls == []
    key["value"] = "test-only-key"
    recovered = client.get(url, headers=HEADERS)
    assert recovered.status_code == 200, recovered.text
    assert recovered.json()["status"] == "completed"
    assert recovered.json()["job_id"] == task["job_id"]
    assert recovered.json()["remote_task_id"] == "remote-one"
    assert calls == [("remote-one", "test-only-key")]


@pytest.mark.parametrize("upstream_status", ["UNKNOWN", "unexpected", None])
def test_unknown_cloud_status_preserves_ledger_for_later_query(client, monkeypatch, upstream_status):
    context, task = cloud_task()
    before = registry.load_task(context, task["job_id"])
    monkeypatch.setattr(registry, "credential_value", lambda *args: "test-only-key")

    async def unknown(*args, **kwargs):
        return {"status": upstream_status, "outputs": []}

    monkeypatch.setattr(platforms, "query_rh_task_outputs", unknown)
    url = "/api/god_workflow/tasks/" + task["job_id"]
    response = client.get(url, headers=HEADERS)
    assert response.status_code == 502, response.text
    assert registry.load_task(context, task["job_id"]) == before
    calls = successful_collection(monkeypatch)
    recovered = client.get(url, headers=HEADERS)
    assert recovered.json()["status"] == "completed"
    assert calls == [("remote-one", "test-only-key")]


@pytest.mark.parametrize("target", ["rh_cloud", "runninghub"])
def test_collection_retry_routes_cloud_alias_and_is_idempotent(client, monkeypatch, target):
    context, task = cloud_task(target)
    registry.update_task(context, task["job_id"], status="failed", execution_status="completed",
                         collection_status="partial_failed")
    monkeypatch.setattr(registry, "credential_value", lambda *args: "test-only-key")
    calls = successful_collection(monkeypatch)
    url = "/api/god_workflow/tasks/" + task["job_id"] + "/collect"
    foreign = client.post(url, headers={**HEADERS, "Authorization": "Bearer other-user"})
    assert foreign.status_code == 404
    first = client.post(url, headers=HEADERS)
    assert first.status_code == 200, first.text
    assert first.json()["status"] == "completed"
    second = client.post(url, headers=HEADERS)
    assert second.json()["outputs"] == first.json()["outputs"]
    assert calls == [("remote-one", "test-only-key")]


@pytest.mark.parametrize("body", [
    {"code": 802, "msg": "APIKEY_UNAUTHORIZED"},
    {"code": 500, "msg": "UNKNOWN_ERROR"},
    {"code": 987, "msg": "unexpected response"},
    {"code": 802, "msg": "RUNNING query was rejected"},
    {"code": 0, "data": {"unexpected": "shape"}},
    [],
])
def test_cloud_query_error_is_not_execution_failure(monkeypatch, body):
    async def reply(*args, **kwargs):
        return body

    monkeypatch.setattr(platforms, "_post_json", reply)
    with pytest.raises(CleanroomException) as error:
        asyncio.run(platforms.query_rh_task_outputs("remote-one", "test-only-key"))
    assert error.value.status_code == 502


@pytest.mark.parametrize("code,status", [(804, "RUNNING"), (813, "RUNNING"), (805, "FAILED")])
def test_cloud_confirmed_statuses_remain_supported(monkeypatch, code, status):
    async def reply(*args, **kwargs):
        return {"code": code, "msg": "provider state"}

    monkeypatch.setattr(platforms, "_post_json", reply)
    result = asyncio.run(platforms.query_rh_task_outputs("remote-one", "test-only-key"))
    assert result["status"] == status


@pytest.mark.parametrize("late_status", ["RUNNING", "FAILED"])
def test_late_poll_cannot_overwrite_completed_task(monkeypatch, late_status):
    """第二个查询先完成；第一个迟到回包不能把账本退回运行或失败。"""
    context, task = cloud_task()
    monkeypatch.setattr(registry, "credential_value", lambda *args: "test-only-key")
    successful_collection(monkeypatch)

    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()
        query_count = 0

        async def query(*args, **kwargs):
            nonlocal query_count
            query_count += 1
            if query_count == 1:
                entered.set()
                await release.wait()
                return {"status": late_status, "outputs": []}
            return {"status": "SUCCESS", "outputs": [{"fileName": "result.png"}]}

        monkeypatch.setattr(platforms, "query_rh_task_outputs", query)
        old_poll = asyncio.create_task(routes.task_status(task["job_id"], HEADERS["Authorization"], "editor"))
        await entered.wait()
        try:
            done = await routes.task_status(task["job_id"], HEADERS["Authorization"], "editor")
            assert done["status"] == "completed"
            before = registry.load_task(context, task["job_id"])
        finally:
            release.set()
        late = await old_poll
        assert late["status"] == "completed"
        assert registry.load_task(context, task["job_id"]) == before

    asyncio.run(scenario())


@pytest.mark.parametrize("invalid_output", [None, {}, {"fileName": "missing.png"}])
def test_mixed_cloud_outputs_keep_partial_failure_and_retry_without_duplicates(client, monkeypatch, invalid_output):
    """有效产物持久化，无效条目仍计为失败；回收重试保留同一素材ID。"""
    image = pytest.importorskip("PIL.Image")
    buffer = io.BytesIO()
    image.new("RGB", (2, 2), "white").save(buffer, format="PNG")
    content = buffer.getvalue()
    context, task = cloud_task()
    monkeypatch.setattr(registry, "credential_value", lambda *args: "test-only-key")
    monkeypatch.setenv("GW_WORKFLOW_OUTPUT_HOSTS", "approved.example")
    queries = []
    valid = {"fileUrl": "https://approved.example/result.png", "fileName": "result.png"}

    def download(request):
        assert request.url.host == "approved.example"
        return httpx.Response(200, content=content, headers={"content-type": "image/png"})

    async def query(*args, **kwargs):
        queries.append(args[0])
        return {"status": "SUCCESS", "outputs": [valid, invalid_output] if len(queries) == 1 else [valid]}

    async def forbidden(*args, **kwargs):
        raise AssertionError("回收重试不能重新生成")

    monkeypatch.setattr(routes, "_output_transport", lambda: httpx.MockTransport(download))
    monkeypatch.setattr(platforms, "query_rh_task_outputs", query)
    monkeypatch.setattr(platforms, "create_rh_cloud_task", forbidden)
    url = "/api/god_workflow/tasks/" + task["job_id"]
    partial = client.get(url, headers=HEADERS)
    assert partial.status_code == 200, partial.text
    assert partial.json()["status"] == "failed"
    assert partial.json()["execution_status"] == "completed"
    assert partial.json()["collection_status"] == "partial_failed"
    outputs = partial.json()["outputs"]
    assert len(outputs) == 2
    assert outputs[1] == {"registered": False, "code": "INVALID_OUTPUT_DESCRIPTOR"}
    asset_id = outputs[0]["asset_id"]
    original = client.get(outputs[0]["url"], headers=HEADERS)
    assert original.content == content
    recovered = client.post(url + "/collect", headers=HEADERS)
    assert recovered.status_code == 200, recovered.text
    assert recovered.json()["status"] == "completed"
    assert recovered.json()["outputs"][0]["asset_id"] == asset_id
    assert len(registry.list_assets(context)) == 1
    assert client.post(url + "/collect", headers=HEADERS).json()["outputs"] == recovered.json()["outputs"]
    assert queries == ["remote-one", "remote-one"]
