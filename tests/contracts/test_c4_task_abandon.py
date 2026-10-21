"""C4 任务放弃契约：结果未知且无远端 ID 的任务可标记放弃，且绝不重发。

覆盖 GET /tasks（含分页形态）与 GET /tasks/{id} 的派生 error_code，
POST /tasks/{id}/abandon 的成功/幂等/拒绝路径与审计事件，以及任务被放弃后
轮询、回收、取消全部按终态短路、不再触碰远端。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent / "media_double"))

from media_doubles import HEADERS, TASKS, FakeComfy, FakeRunningHub  # noqa: E402

from gw.core import audit  # noqa: E402
from gw.god_workflow import registry, routes  # noqa: E402

READONLY_HEADERS = {**HEADERS, "X-User-Role": "readonly"}
UNKNOWN_TASK_ID = "00000000-0000-0000-0000-000000000000"


@pytest.fixture
def client():
    from gw.api.app import create_app

    with TestClient(create_app()) as value:
        yield value


@pytest.fixture
def context():
    return routes._auth(HEADERS["Authorization"], "editor", True)


@pytest.fixture
def fake_executors(monkeypatch):
    fake_comfy = FakeComfy("success")
    fake_comfy.install(monkeypatch)
    fake_rh = FakeRunningHub("success")
    fake_rh.install(monkeypatch)
    return fake_comfy, fake_rh


@pytest.fixture
def clean_audit():
    audit.reset_audit_log()
    yield
    audit.reset_audit_log()


def unknown_task(context, *, workflow_id=None):
    return registry.create_task(
        context,
        workflow_id=workflow_id,
        prompt_id=None,
        target="rh_cloud",
        outcome_unknown=True,
    )


def abandon_events():
    return [
        event
        for event in audit.list_auth_events()
        if event["event"] == audit.EVENT_WORKFLOW_TASK_ABANDONED
    ]


# ---------------------------------------------------------------------------
# 派生 error_code
# ---------------------------------------------------------------------------
def test_unknown_no_remote_task_exposes_derived_error_code(client, context):
    task = unknown_task(context)
    listed = client.get(TASKS, headers=HEADERS)
    assert listed.status_code == 200, listed.text
    item = next(t for t in listed.json()["tasks"] if t["job_id"] == task["job_id"])
    assert item["error_code"] == "OUTCOME_UNKNOWN_NO_REMOTE_ID"
    paged = client.get(TASKS, params={"limit": "100"}, headers=HEADERS)
    assert paged.status_code == 200, paged.text
    paged_item = next(t for t in paged.json()["tasks"] if t["job_id"] == task["job_id"])
    assert paged_item["error_code"] == "OUTCOME_UNKNOWN_NO_REMOTE_ID"
    detail = client.get(f"{TASKS}/{task['job_id']}", headers=HEADERS)
    assert detail.status_code == 200, detail.text
    assert detail.json()["error_code"] == "OUTCOME_UNKNOWN_NO_REMOTE_ID"
    assert detail.json()["data_gaps"] == []


def test_unknown_with_remote_task_id_gets_plain_unknown_code(client, context):
    task = registry.create_task(
        context,
        workflow_id=None,
        prompt_id="rh-remote-9",
        target="rh_cloud",
        outcome_unknown=True,
    )
    detail = client.get(f"{TASKS}/{task['job_id']}", headers=HEADERS)
    assert detail.status_code == 200, detail.text
    assert detail.json()["error_code"] == "OUTCOME_UNKNOWN"


def test_non_terminal_task_error_code_is_none(client, context, fake_executors):
    task = registry.create_task(
        context, workflow_id=None, prompt_id="comfy-remote-1", target="local_comfy"
    )
    detail = client.get(f"{TASKS}/{task['job_id']}", headers=HEADERS)
    assert detail.status_code == 200, detail.text
    assert detail.json()["status"] == "running"
    assert detail.json()["error_code"] is None


# ---------------------------------------------------------------------------
# POST /tasks/{id}/abandon
# ---------------------------------------------------------------------------
def test_abandon_succeeds_is_idempotent_and_never_submits(
    client, context, fake_executors, clean_audit
):
    fake_comfy, fake_rh = fake_executors
    task = unknown_task(context)
    assert fake_comfy.names() == [] and fake_rh.names() == []
    response = client.post(f"{TASKS}/{task['job_id']}/abandon", headers=HEADERS)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "abandoned"
    assert body["error_code"] == "TASK_ABANDONED"
    assert body["execution_status"] == "abandoned"
    assert body["previous_status"] == "outcome_unknown"
    assert body["abandoned"] is True
    assert body["already_abandoned"] is False
    assert body["resubmitted"] is False
    assert body["data_status"] == "ok" and body["data_gaps"] == []
    # 放弃绝不触发提交或查询远端。
    assert fake_comfy.names() == [] and fake_rh.names() == []
    events = abandon_events()
    assert len(events) == 1
    assert events[0]["outcome"] == "succeeded"
    assert events[0]["resource_id"] == task["job_id"]
    assert events[0]["reason"] == "outcome_unknown_no_remote_id"
    assert events[0]["subject"] == context.subject
    assert events[0]["role"] == "editor"
    second = client.post(
        f"{TASKS}/{task['job_id']}/abandon", json={"reason": "again"}, headers=HEADERS
    )
    assert second.status_code == 200, second.text
    assert second.json()["already_abandoned"] is True
    assert second.json()["abandoned"] is True
    assert second.json()["resubmitted"] is False
    # 幂等重放不再记审计、不触发远端。
    assert len(abandon_events()) == 1
    assert fake_comfy.names() == [] and fake_rh.names() == []


def test_abandon_reason_is_clipped_and_persisted(client, context, clean_audit):
    task = unknown_task(context)
    response = client.post(
        f"{TASKS}/{task['job_id']}/abandon", json={"reason": "x" * 300}, headers=HEADERS
    )
    assert response.status_code == 200, response.text
    assert response.json()["abandon_reason"] == "x" * 200
    reloaded = registry.load_task(context, task["job_id"])
    assert reloaded["abandon_reason"] == "x" * 200
    assert reloaded["status"] == "abandoned"


@pytest.mark.parametrize("body", [{"reason": 123}, ["not", "an", "object"]])
def test_abandon_bad_body_is_invalid_request(client, context, clean_audit, body):
    task = unknown_task(context)
    response = client.post(
        f"{TASKS}/{task['job_id']}/abandon", json=body, headers=HEADERS
    )
    assert response.status_code == 400, response.text
    assert response.json()["detail"]["code"] == "INVALID_REQUEST"
    assert registry.load_task(context, task["job_id"])["status"] == "outcome_unknown"
    assert abandon_events() == []


def test_abandon_rejected_for_task_with_remote_id(client, context, clean_audit):
    task = registry.create_task(
        context, workflow_id=None, prompt_id="rh-remote-1", target="rh_cloud"
    )
    path = registry._task_path(context, task["job_id"])
    before = path.read_bytes()
    response = client.post(f"{TASKS}/{task['job_id']}/abandon", headers=HEADERS)
    assert response.status_code == 409, response.text
    assert response.json()["detail"]["code"] == "TASK_HAS_REMOTE_ID"
    assert path.read_bytes() == before
    events = abandon_events()
    assert len(events) == 1
    assert events[0]["outcome"] == "rejected"
    assert events[0]["reason"] == "TASK_HAS_REMOTE_ID"
    assert events[0]["resource_id"] == task["job_id"]


@pytest.mark.parametrize("stage", ["accepted", "completed"])
def test_abandon_rejected_when_not_outcome_unknown(client, context, clean_audit, stage):
    task = registry.create_task(
        context, workflow_id=None, prompt_id=None, target="local_comfy"
    )
    if stage == "completed":
        registry.update_task(
            context, task["job_id"], status="completed", execution_status="completed"
        )
    response = client.post(f"{TASKS}/{task['job_id']}/abandon", headers=HEADERS)
    assert response.status_code == 409, response.text
    assert response.json()["detail"]["code"] == "TASK_NOT_OUTCOME_UNKNOWN"
    assert registry.load_task(context, task["job_id"])["status"] == stage
    assert abandon_events()[0]["outcome"] == "rejected"


def test_abandon_unknown_task_is_404(client, clean_audit):
    response = client.post(f"{TASKS}/{UNKNOWN_TASK_ID}/abandon", headers=HEADERS)
    assert response.status_code == 404, response.text
    assert response.json()["detail"]["code"] == "TASK_NOT_FOUND"
    assert abandon_events() == []


def test_abandon_requires_edit_access(client, context, clean_audit):
    task = unknown_task(context)
    response = client.post(
        f"{TASKS}/{task['job_id']}/abandon", headers=READONLY_HEADERS
    )
    assert response.status_code == 403, response.text
    assert registry.load_task(context, task["job_id"])["status"] == "outcome_unknown"
    assert abandon_events() == []


def test_abandoned_task_is_terminal_for_poll_collect_cancel(
    client, context, fake_executors, clean_audit
):
    fake_comfy, fake_rh = fake_executors
    task = unknown_task(context)
    assert (
        client.post(f"{TASKS}/{task['job_id']}/abandon", headers=HEADERS).status_code
        == 200
    )
    polled = client.get(f"{TASKS}/{task['job_id']}", headers=HEADERS)
    assert polled.status_code == 200, polled.text
    assert polled.json()["status"] == "abandoned"
    assert polled.json()["error_code"] == "TASK_ABANDONED"
    collected = client.post(f"{TASKS}/{task['job_id']}/collect", headers=HEADERS)
    assert collected.status_code == 409, collected.text
    assert collected.json()["detail"]["code"] == "EXECUTION_NOT_COMPLETED"
    cancelled = client.post(f"{TASKS}/{task['job_id']}/cancel", headers=HEADERS)
    assert cancelled.status_code == 409, cancelled.text
    assert cancelled.json()["detail"]["code"] == "JOB_ALREADY_TERMINAL"
    # 终态任务轮询/回收/取消都不触碰远端。
    assert fake_comfy.names() == [] and fake_rh.names() == []
