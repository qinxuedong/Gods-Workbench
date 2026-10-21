"""C3 媒体闭环（AC3/AC4/AC5/AC10）：隔离替身跑完 上传 → 执行 → 轮询 → collect → 统一资产 → 来源回跳。

来源回跳按裁定 9a 只验后端：注册表资产含 task_id(=job_id)、metadata.workflow_id，
且 GET /api/god_workflow/tasks/{job_id} 的 outputs 含该 asset_id。
"""

from __future__ import annotations

import hashlib

import pytest
from fastapi.testclient import TestClient
from media_doubles import (
    FIXED_PNG_SHA256,
    HEADERS,
    TASKS,
    count_assets,
    execute,
    prepare_workflow,
)


def run_to_completion(client, workflow, target, key="loop-key"):
    started = execute(client, workflow, target, key=key)
    assert started.status_code == 202, started.text
    job_id = started.json()["job_id"]
    first = client.get(f"{TASKS}/{job_id}", headers=HEADERS)
    assert first.status_code == 200, first.text
    assert first.json()["status"] == "running"
    second = client.get(f"{TASKS}/{job_id}", headers=HEADERS)
    assert second.status_code == 200, second.text
    return started.json(), second.json()


def registry_asset(client, asset_id):
    response = client.get(f"/api/asset-registry/assets/{asset_id}", headers=HEADERS)
    assert response.status_code == 200, response.text
    return response.json()["asset"]


def assert_traceable(client, task, workflow, asset_id):
    """来源回跳：资产 → 任务 / 工作流；任务 → 资产。"""
    asset = registry_asset(client, asset_id)
    assert asset["task_id"] == task["job_id"]
    metadata = asset["metadata"]
    assert (
        metadata["task_id"] == task["job_id"] and metadata["job_id"] == task["job_id"]
    )
    assert metadata["workflow_id"] == workflow["workflow_id"]
    assert metadata["workflow_version"] == workflow["version"]
    readback = client.get(f"{TASKS}/{task['job_id']}", headers=HEADERS).json()
    assert asset_id in [item.get("asset_id") for item in readback["outputs"]]
    assert readback["workflow_id"] == workflow["workflow_id"]
    content = client.get(
        f"/api/god_workflow/assets/{asset_id}/content", headers=HEADERS
    )
    assert content.status_code == 200
    assert hashlib.sha256(content.content).hexdigest() == FIXED_PNG_SHA256


def test_rh_closed_loop_local_image_without_rh_id(
    media_client, fake_media, asset_counts
):
    """C3-MD-RH-SUCCESS-01"""
    input_asset, workflow = prepare_workflow(media_client)
    before = asset_counts()
    started, done = run_to_completion(media_client, workflow, "rh_cloud")
    assert fake_media.rh.count("upload_rh_input") == 1
    assert fake_media.rh.calls[0][1]["filename"].startswith(input_asset["asset_id"])
    create = [
        args for name, args in fake_media.rh.calls if name == "create_rh_cloud_task"
    ]
    assert (
        len(create) == 1 and create[0]["workflow_id"] is None
    )  # 没有真实 RH 标识就不编
    assert started["remote_task_id"] == "fake-rh-task-0001"
    assert done["status"] == "completed" and done["collection_status"] == "completed"
    assert len(done["outputs"]) == 1 and done["outputs"][0]["registered"] is True
    after = asset_counts()
    assert after["registry"] == before["registry"] + 1
    assert after["library_image"] == before["library_image"] + 1
    asset_id = done["outputs"][0]["asset_id"]
    assert asset_id in after["registry_ids"] and asset_id in after["library_image_ids"]
    collected = media_client.post(
        f"{TASKS}/{started['job_id']}/collect", headers=HEADERS
    )
    assert collected.status_code == 200, collected.text
    assert_traceable(media_client, started, workflow, asset_id)
    assert fake_media.network_attempts == []


def test_comfy_closed_loop(media_client, fake_media, asset_counts):
    """C3-MD-COMFY-SUCCESS-01"""
    _, workflow = prepare_workflow(media_client)
    before = asset_counts()
    started, done = run_to_completion(media_client, workflow, "local_comfy")
    assert (
        fake_media.comfy.count("upload_input") == 1
        and fake_media.comfy.count("submit") == 1
    )
    assert started["remote_task_id"] == "fake-prompt-0001"
    assert done["status"] == "completed", done
    after = asset_counts()
    assert after["registry"] == before["registry"] + 1
    assert after["library_image"] == before["library_image"] + 1
    assert_traceable(media_client, started, workflow, done["outputs"][0]["asset_id"])
    assert fake_media.rh.calls == []


@pytest.mark.parametrize("target", ["rh_cloud", "local_comfy"])
def test_collect_twice_asset_count_unchanged(
    media_client, fake_media, asset_counts, target
):
    """C3-MD-COLLECT-TWICE-01：数两个存储，不只比较 outputs。"""
    _, workflow = prepare_workflow(media_client)
    started, done = run_to_completion(media_client, workflow, target)
    settled = asset_counts()
    for _ in range(2):
        again = media_client.post(
            f"{TASKS}/{started['job_id']}/collect", headers=HEADERS
        )
        assert again.status_code == 200, again.text
        assert again.json()["outputs"] == done["outputs"]
    assert asset_counts() == settled
    assert (
        fake_media.rh.count("create_rh_cloud_task") + fake_media.comfy.count("submit")
        == 1
    )


@pytest.mark.parametrize(
    "fake_rh,fake_comfy,target",
    [("fail_remote", "success", "rh_cloud"), ("success", "fail_remote", "local_comfy")],
    indirect=["fake_rh", "fake_comfy"],
)
def test_remote_failure_registers_nothing(
    media_client, fake_media, asset_counts, target
):
    """C3-MD-FAIL-01：终态 failed，资产数 +0，没有演示数据。"""
    _, workflow = prepare_workflow(media_client)
    before = asset_counts()
    _, done = run_to_completion(media_client, workflow, target)
    assert done["status"] == "failed"
    assert done["execution_status"] == "failed"
    assert all(not item.get("registered") for item in done["outputs"])
    assert asset_counts() == before


@pytest.mark.parametrize(
    "fake_rh,fake_comfy,target",
    [
        ("partial_outputs", "success", "rh_cloud"),
        ("success", "partial_outputs", "local_comfy"),
    ],
    indirect=["fake_rh", "fake_comfy"],
)
def test_partial_outputs_retry_no_duplicates(
    media_client, fake_media, asset_counts, target
):
    """C3-MD-PARTIAL-01：一份合法、一份不合法 → partial；重试后合法那份只登记 1 次。"""
    _, workflow = prepare_workflow(media_client)
    before = asset_counts()
    started, partial = run_to_completion(media_client, workflow, target)
    assert partial["status"] == "failed" and partial["execution_status"] == "completed"
    assert partial["collection_status"] == "partial_failed"
    good = [item for item in partial["outputs"] if item.get("registered")]
    assert len(good) == 1
    retried = media_client.post(f"{TASKS}/{started['job_id']}/collect", headers=HEADERS)
    assert retried.status_code == 200, retried.text
    assert retried.json()["status"] == "completed"
    assert [item["asset_id"] for item in retried.json()["outputs"]] == [
        good[0]["asset_id"]
    ]
    after = asset_counts()
    assert after["registry"] == before["registry"] + 1
    assert after["library_image"] == before["library_image"] + 1


def test_capabilities_reports_not_configured_without_keys(
    media_client, monkeypatch, fake_media
):
    """C3-MD-CAP-01（AC10）：未配置时如实报告缺口，不用演示数据兜底。"""
    monkeypatch.delenv("GW_RUNNINGHUB_API_KEY", raising=False)
    monkeypatch.delenv("GW_COMFYUI_URL", raising=False)
    response = media_client.get("/api/god_workflow/capabilities", headers=HEADERS)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["data_status"] == "partial"
    assert "local_comfy_not_configured" in body["data_gaps"]
    assert "rh_cloud_not_configured" in body["data_gaps"]
    assert body["executors"] == {
        "local_comfy": "unavailable",
        "rh_cloud": "unavailable",
    }
    _, workflow = prepare_workflow(media_client)
    refused = execute(media_client, workflow, "rh_cloud", key="cap-refused")
    assert refused.status_code == 503
    assert fake_media.rh.calls == [] and fake_media.comfy.calls == []


def test_restart_keeps_task_and_asset_relation(media_client, fake_media, media_env):
    """C3-MD-RESTART-01（AC5）：同一数据目录重建 create_app()——同进程模拟重启，不是跨进程。"""
    from gw.api.app import create_app

    _, workflow = prepare_workflow(media_client)
    started, done = run_to_completion(media_client, workflow, "rh_cloud")
    asset_id = done["outputs"][0]["asset_id"]
    before = count_assets(media_client)
    media_client.__exit__(None, None, None)
    with TestClient(create_app()) as restarted:
        task = restarted.get(f"{TASKS}/{started['job_id']}", headers=HEADERS)
        assert task.status_code == 200, task.text
        assert task.json()["status"] == "completed"
        assert task.json()["remote_task_id"] == "fake-rh-task-0001"
        assert count_assets(restarted) == before
        assert_traceable(restarted, started, workflow, asset_id)
    assert fake_media.rh.count("create_rh_cloud_task") == 1
