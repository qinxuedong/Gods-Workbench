"""C3 幂等与 CAS 安全回归（AC6）：经 /tasks/execute、带输入素材，替身计数证明无第二次副作用。

已有覆盖：test_rh_workflow_contract.py::test_r0_idempotent_replay_and_conflict（local_comfy、无输入素材）、
observability/test_workflow_rh_direct.py（走 /execute 别名）。本文件补齐：RH 经 /tasks/execute、
带输入素材时上传也只发生 1 次、断言 idempotent_replay。
"""
from __future__ import annotations

import pytest

from media_doubles import TASKS, execute, prepare_workflow

pytestmark = pytest.mark.usefixtures("fake_media")


def _task_count(client):
    from media_doubles import HEADERS
    response = client.get(TASKS, headers=HEADERS)
    assert response.status_code == 200, response.text
    return len(response.json()["tasks"])


def test_same_idempotency_key_rh_tasks_execute_submits_once(media_client, fake_media):
    """C3-MD-IDEM-01"""
    _, workflow = prepare_workflow(media_client)
    first = execute(media_client, workflow, "rh_cloud", key="idem-rh")
    second = execute(media_client, workflow, "rh_cloud", key="idem-rh")
    assert first.status_code == 202 and second.status_code == 202, (first.text, second.text)
    assert first.json()["idempotent_replay"] is False
    assert second.json()["idempotent_replay"] is True
    assert second.json()["job_id"] == first.json()["job_id"]
    assert second.json()["remote_task_id"] == first.json()["remote_task_id"] == "fake-rh-task-0001"
    assert fake_media.rh.count("upload_rh_input") == 1
    assert fake_media.rh.count("create_rh_cloud_task") == 1
    assert _task_count(media_client) == 1
    assert fake_media.network_attempts == []


def test_same_idempotency_key_comfy_uploads_once(media_client, fake_media):
    """C3-MD-IDEM-02"""
    _, workflow = prepare_workflow(media_client)
    first = execute(media_client, workflow, "local_comfy", key="idem-comfy")
    second = execute(media_client, workflow, "local_comfy", key="idem-comfy")
    assert first.status_code == 202 and second.status_code == 202, (first.text, second.text)
    assert second.json()["idempotent_replay"] is True
    assert second.json()["job_id"] == first.json()["job_id"]
    assert first.json()["remote_task_id"] == "fake-prompt-0001"
    assert fake_media.comfy.count("upload_input") == 1
    assert fake_media.comfy.count("submit") == 1
    assert fake_media.rh.calls == []


@pytest.mark.parametrize("target", ["rh_cloud", "local_comfy"])
def test_same_key_different_body_is_409_without_side_effect(media_client, fake_media, target):
    """C3-MD-IDEM-MISMATCH-01"""
    _, workflow = prepare_workflow(media_client)
    assert execute(media_client, workflow, target, key="idem-mismatch").status_code == 202
    calls_before = list(fake_media.rh.calls) + list(fake_media.comfy.calls)
    conflict = execute(media_client, workflow, target, key="idem-mismatch", client_id="different-body")
    assert conflict.status_code == 409, conflict.text
    assert conflict.json()["detail"]["code"] == "IDEMPOTENCY_CONFLICT"
    assert list(fake_media.rh.calls) + list(fake_media.comfy.calls) == calls_before
    assert _task_count(media_client) == 1


@pytest.mark.parametrize("target", ["rh_cloud", "local_comfy"])
def test_stale_expected_version_is_409_before_reserve(media_client, fake_media, target):
    """C3-MD-CAS-409-01：CAS 检查在 reserve_task 之前，没有账本、没有上传和提交。"""
    _, workflow = prepare_workflow(media_client)
    stale = execute(media_client, workflow, target, key="idem-cas", expected_version=workflow["version"] + 7)
    assert stale.status_code == 409, stale.text
    assert _task_count(media_client) == 0
    assert fake_media.rh.calls == [] and fake_media.comfy.calls == []
