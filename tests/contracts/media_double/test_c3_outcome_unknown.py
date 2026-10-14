"""C3 副作用未知不重试（AC5/AC6）：超时 → outcome_unknown，轮询 / collect / 同键重放都不再提交或查询。"""
from __future__ import annotations

import pytest

from gw.god_workflow import platforms
from media_doubles import HEADERS, TASKS, TEST_API_KEY, execute, prepare_workflow


def _follow_up(client, job_id, workflow, target, key):
    polled = client.get(f"{TASKS}/{job_id}", headers=HEADERS)
    collected = client.post(f"{TASKS}/{job_id}/collect", headers=HEADERS)
    replay = execute(client, workflow, target, key=key)
    return polled, collected, replay


def _assert_unknown_is_stable(client, first, workflow, target, key, asset_counts):
    assert first.status_code == 202, first.text
    task = first.json()
    assert task["status"] == "outcome_unknown"
    assert task["remote_task_id"] is None
    assert task["messages"] == ["WORKFLOW_PROVIDER_TIMEOUT"]
    before = asset_counts()
    polled, collected, replay = _follow_up(client, task["job_id"], workflow, target, key)
    assert polled.status_code == 200 and polled.json()["status"] == "outcome_unknown"
    assert collected.status_code == 409, collected.text
    assert collected.json()["detail"]["code"] == "EXECUTION_NOT_COMPLETED"
    assert replay.status_code == 202 and replay.json()["idempotent_replay"] is True
    assert replay.json()["job_id"] == task["job_id"] and replay.json()["status"] == "outcome_unknown"
    assert asset_counts() == before


@pytest.mark.parametrize("fake_rh", ["submit_timeout"], indirect=True)
def test_rh_submit_timeout_is_outcome_unknown_and_never_resubmitted(media_client, fake_media, asset_counts):
    """C3-MD-UNKNOWN-01"""
    _, workflow = prepare_workflow(media_client)
    first = execute(media_client, workflow, "rh_cloud", key="unknown-rh")
    _assert_unknown_is_stable(media_client, first, workflow, "rh_cloud", "unknown-rh", asset_counts)
    assert fake_media.rh.count("upload_rh_input") == 1
    assert fake_media.rh.count("create_rh_cloud_task") == 1
    assert fake_media.rh.count("query_rh_task_outputs") == 0


@pytest.mark.parametrize("fake_comfy", ["submit_timeout"], indirect=True)
def test_comfy_submit_timeout_poll_and_collect_do_not_resubmit(media_client, fake_media, asset_counts):
    """C3-MD-UNKNOWN-02"""
    _, workflow = prepare_workflow(media_client)
    first = execute(media_client, workflow, "local_comfy", key="unknown-comfy")
    _assert_unknown_is_stable(media_client, first, workflow, "local_comfy", "unknown-comfy", asset_counts)
    assert fake_media.comfy.count("upload_input") == 1
    assert fake_media.comfy.count("submit") == 1
    assert fake_media.comfy.count("history") == 0


@pytest.mark.parametrize("fake_rh", ["upload_timeout"], indirect=True)
def test_upload_timeout_before_submit_is_failed_not_unknown(media_client, fake_media):
    """C3-MD-UPLOAD-FAIL-01：上传阶段失败时还没开始提交，记为 failed；create 0 次。"""
    _, workflow = prepare_workflow(media_client)
    first = execute(media_client, workflow, "rh_cloud", key="upload-timeout")
    assert first.status_code == 202, first.text
    assert first.json()["status"] == "failed"
    assert first.json()["messages"] == ["RH_INPUT_UPLOAD_TIMEOUT"]
    assert first.json()["remote_task_id"] is None
    assert fake_media.rh.count("upload_rh_input") == 1
    assert fake_media.rh.count("create_rh_cloud_task") == 0
    replay = execute(media_client, workflow, "rh_cloud", key="upload-timeout")
    assert replay.json()["idempotent_replay"] is True and fake_media.rh.count("upload_rh_input") == 1


@pytest.mark.parametrize("fake_rh", ["no_remote_id"], indirect=True)
def test_no_remote_id_is_not_fabricated(media_client, fake_media):
    """C3-MD-NOREMOTE-01"""
    _, workflow = prepare_workflow(media_client)
    first = execute(media_client, workflow, "rh_cloud", key="no-remote")
    assert first.status_code == 202, first.text
    task = first.json()
    assert task["status"] == "outcome_unknown"
    assert task["remote_task_id"] is None and task["prompt_id"] in (None, "")
    assert task["data_gaps"] == ["remote_task_id_missing"]
    assert "RH_TASK_ID_MISSING" in task["messages"]
    polled = media_client.get(f"{TASKS}/{task['job_id']}", headers=HEADERS)
    assert polled.json()["remote_task_id"] is None
    assert fake_media.rh.count("create_rh_cloud_task") == 1
    assert fake_media.rh.count("query_rh_task_outputs") == 0


@pytest.mark.parametrize("exc_type", [UnicodeEncodeError, TypeError])
def test_rh_submit_internal_error_is_outcome_unknown_and_not_resent(media_client, fake_media, monkeypatch, exc_type):
    """C3-MD-UNKNOWN-03：提交已开始后的意外异常返回 202，保持 outcome_unknown，轮询不再提交。"""
    calls = {"create": 0}

    async def create_rh_cloud_task(*args, **kwargs):
        calls["create"] += 1
        if exc_type is UnicodeEncodeError:
            raise UnicodeEncodeError("ascii", "x", 0, 1, "ordinal not in range(128)")
        raise TypeError("not serializable")

    monkeypatch.setattr(platforms, "create_rh_cloud_task", create_rh_cloud_task)
    _, workflow = prepare_workflow(media_client)
    first = execute(media_client, workflow, "rh_cloud", key="internal-" + exc_type.__name__)
    assert first.status_code == 202, first.text
    task = first.json()
    assert task["status"] == "outcome_unknown"
    assert "RH_SUBMIT_INTERNAL_ERROR" in task["messages"]
    assert "exception: " + exc_type.__name__ in task["messages"]
    assert TEST_API_KEY not in first.text
    polled = media_client.get(f"{TASKS}/{task['job_id']}", headers=HEADERS)
    assert polled.status_code == 200 and polled.json()["status"] == "outcome_unknown"
    assert calls["create"] == 1
    assert fake_media.rh.count("query_rh_task_outputs") == 0
    assert fake_media.rh.count("create_rh_cloud_task") == 0


def test_unexpected_error_before_rh_submit_is_failed(media_client, fake_media, monkeypatch):
    """C3-MD-UNKNOWN-04：提交开始前的意外异常记为 failed，不再向远端创建任务。"""
    async def upload_rh_input(*args, **kwargs):
        raise TypeError("upload broke")

    monkeypatch.setattr(platforms, "upload_rh_input", upload_rh_input)
    _, workflow = prepare_workflow(media_client)
    first = execute(media_client, workflow, "rh_cloud", key="pre-submit-type")
    assert first.status_code == 202, first.text
    task = first.json()
    assert task["status"] == "failed"
    assert task["remote_id_missing"] is False
    assert "remote_task_id_missing" not in task["data_gaps"]
    assert "RH_SUBMIT_INTERNAL_ERROR" in task["messages"]
    assert "exception: TypeError" in task["messages"]
    assert fake_media.rh.count("create_rh_cloud_task") == 0
    assert fake_media.rh.count("upload_rh_input") == 0


@pytest.mark.parametrize("fake_rh", ["success", "submit_timeout", "no_remote_id"], indirect=True)
def test_test_key_never_leaks(media_client, fake_media, media_env):
    """C3-MD-NOLEAK-01：测试 key 不出现在响应体和持久化任务文件里。"""
    _, workflow = prepare_workflow(media_client)
    first = execute(media_client, workflow, "rh_cloud", key="no-leak")
    polled = media_client.get(f"{TASKS}/{first.json()['job_id']}", headers=HEADERS)
    listed = media_client.get(TASKS, headers=HEADERS)
    for response in (first, polled, listed):
        assert TEST_API_KEY not in response.text
    files = [path for path in media_env.rglob("*") if path.is_file()]
    assert any("tasks" in path.parts for path in files)
    for path in files:
        assert TEST_API_KEY.encode() not in path.read_bytes(), path
