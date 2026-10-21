"""RH完整图直传与可信创建状态；全部网络/存储隔离。"""

import json

import pytest
from fastapi.testclient import TestClient

from gw.api.app import create_app
from gw.core.errors import CleanroomException
from gw.god_workflow import platforms

HEADERS = {
    "Authorization": "Bearer rh-direct",
    "Origin": "http://testserver",
    "Idempotency-Key": "one-direct-run",
}
PROMPT = {
    "1": {
        "class_type": "EmptyImage",
        "inputs": {"width": 64, "height": 64, "batch_size": 1, "color": 0},
    },
    "2": {
        "class_type": "SaveImage",
        "inputs": {"images": ["1", 0], "filename_prefix": "test"},
    },
}


@pytest.mark.parametrize(
    "result",
    [
        "accepted",
        "rejected",
        "timeout",
        "no-id",
        "unexpected",
        " 0 ",
        True,
        None,
        "",
        804,
        813,
        1000,
        1005,
        1006,
        1010,
        1011,
        1012,
        1504,
        9999,
    ],
)
def test_local_workflow_is_uploaded_once_and_creation_result_is_persisted(
    monkeypatch, result
):
    monkeypatch.setenv("GW_RUNNINGHUB_API_KEY", "never-expose-test-key")
    calls = []

    async def post(url, body, headers):
        calls.append(body)
        assert url.endswith("/task/openapi/create") and "workflowId" not in body
        assert json.loads(body["workflow"])["2"]["inputs"]["images"] == ["1", 0]
        if result == "timeout":
            raise CleanroomException(
                504, "WORKFLOW_PROVIDER_TIMEOUT", "bounded timeout"
            )
        if result not in ("accepted", "rejected", "no-id"):
            return {"code": result, "data": {}}
        return (
            {"code": 0, "data": {"taskId": "real-rh-task"}}
            if result == "accepted"
            else (
                {"code": 812, "msg": "never-expose-test-key"}
                if result == "rejected"
                else {"code": 0, "data": {}}
            )
        )

    monkeypatch.setattr(platforms, "_post_json", post)
    with TestClient(create_app()) as client:
        saved = client.post(
            "/api/god_workflow/documents", headers=HEADERS, json=PROMPT
        ).json()
        workflow_id = saved["workflow_id"]
        body = {"workflow_id": workflow_id, "expected_version": 1, "target": "rh_cloud"}
        first = client.post("/api/god_workflow/execute", headers=HEADERS, json=body)
        assert first.status_code == 202, first.text
        task = first.json()
        assert task["status"] == {"accepted": "accepted", "rejected": "failed"}.get(
            result, "outcome_unknown"
        )
        replay = client.post("/api/god_workflow/execute", headers=HEADERS, json=body)
        assert replay.json()["job_id"] == task["job_id"] and len(calls) == 1
        if result == "accepted":
            # 本例不轮询真实云端；只验证已绑定的真实返回ID。
            assert task["remote_task_id"] == "real-rh-task"
        else:
            readback = client.get(
                "/api/god_workflow/tasks/" + task["job_id"], headers=HEADERS
            )
            assert readback.status_code == 200
            assert readback.json()["messages"] == task["messages"] and task["messages"]
        assert "never-expose-test-key" not in first.text


def test_original_public_graph_avoids_api_cache_and_does_not_start_task(monkeypatch):
    import asyncio

    calls = []
    graph = {
        "version": 0.4,
        "nodes": [{"id": 1, "type": "Actual", "widgets_values": ["real"]}],
        "links": [],
    }

    async def post(url, body, headers):
        calls.append(url)
        assert url.endswith(("getDetail", "/detail"))
        return {
            "code": 0,
            "data": {"name": "original", "workflowContent": json.dumps(graph)},
        }

    monkeypatch.setattr(platforms, "_post_json", post)
    bundle = asyncio.run(
        platforms.fetch_runninghub_bundle(
            "https://www.runninghub.cn/workflow/2108837566177959938", api_key="not-used"
        )
    )
    assert bundle["canvas_json"] == graph and len(calls) == 1


def test_metadata_only_first_detail_continues_to_full_original_source(monkeypatch):
    import asyncio

    calls = []

    async def post(url, body, headers):
        calls.append(url)
        return (
            {"code": 0, "data": {"name": "metadata"}}
            if url.endswith("getDetail")
            else {"code": 0, "data": {"name": "full", "prompt": json.dumps(PROMPT)}}
        )

    monkeypatch.setattr(platforms, "_post_json", post)
    bundle = asyncio.run(platforms.fetch_runninghub_bundle("2108837566177959938"))
    assert bundle["canvas_json"] == PROMPT and len(calls) == 2


def test_source_domain_never_forwards_credentials_to_lookalike_host(monkeypatch):
    import asyncio

    calls = []

    async def post(url, body, headers):
        calls.append(url)
        assert url.startswith("https://www.runninghub.cn/"), (
            "仅允许真实平台主机接收凭据"
        )
        return {"code": 0, "data": {"workflowContent": PROMPT}}

    monkeypatch.setattr(platforms, "_post_json", post)
    asyncio.run(
        platforms.fetch_runninghub_bundle(
            "https://runninghub.cn.evil.test/editor/2108837566177959938",
            api_key="isolated-key",
        )
    )
    assert calls and all("evil.test" not in url for url in calls)


def test_full_graph_keeps_real_rh_template_for_required_parameter_compatibility(
    monkeypatch,
):
    import asyncio

    async def post(url, body, headers):
        assert body["workflowId"] == "2104915887789797378"
        assert json.loads(body["workflow"]) == PROMPT
        return {"code": 0, "data": {"taskId": "actual-task-id"}}

    monkeypatch.setattr(platforms, "_post_json", post)
    result = asyncio.run(
        platforms.create_rh_cloud_task(
            "2104915887789797378", [], "test-key", workflow=PROMPT
        )
    )
    assert result["taskId"] == "actual-task-id"
