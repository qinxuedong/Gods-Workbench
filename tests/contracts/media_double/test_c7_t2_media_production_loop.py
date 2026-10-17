"""C7-T2 媒体最小闭环契约测试 (C7-AC2)。

验收标准：
C7-AC2：一个受控工作流参数在 ComfyUI 或 RunningHub 产生持久任务结果，统一登记资产并可回跳来源。
Proof：API JSON、fixture id、test log。
"""
from __future__ import annotations

import hashlib
from typing import Any

import pytest
from fastapi.testclient import TestClient

from media_doubles import (
    APPROVED_HOST,
    FIXED_PNG,
    FIXED_PNG_SHA256,
    HEADERS,
    TASKS,
    count_assets,
    execute,
    prepare_workflow,
)


def _create_project(client: TestClient, name: str = "c7-media-project") -> tuple[str, int]:
    res = client.post(
        "/api/asset-registry/projects",
        headers=HEADERS,
        json={"name": name, "project_type": "film"},
    )
    assert res.status_code == 201, res.text
    project = res.json()["project"]
    return project["project_id"], project["version"]


def _create_canvas(client: TestClient, project_id: str, title: str = "c7-canvas") -> str:
    res = client.post(
        "/api/canvases",
        headers=HEADERS,
        json={"project_id": project_id, "title": title, "mode": "classic"},
    )
    assert res.status_code == 201, res.text
    return res.json()["canvas"]["canvas_id"]


def _get_registry_asset(client: TestClient, asset_id: str) -> dict[str, Any]:
    res = client.get(f"/api/asset-registry/assets/{asset_id}", headers=HEADERS)
    assert res.status_code == 200, res.text
    return res.json()["asset"]


def test_c7_t2_comfyui_controlled_params_durable_task_and_unified_asset_loop(
    media_client, fake_media, asset_counts
):
    """C7-AC2 (ComfyUI)：受控参数执行 -> 持久任务 -> 终态轮询 -> 统一资产登记 -> 来源与双向回跳。"""
    # 1. 准备项目与受控工作流
    project_id, project_version = _create_project(media_client, "c7-t2-comfy-film")
    canvas_id = _create_canvas(media_client, project_id, "c7-comfy-canvas")
    input_asset, workflow = prepare_workflow(media_client)
    before_counts = asset_counts()

    controlled_node_info = [
        {"nodeId": "2", "fieldName": "filename_prefix", "fieldValue": "c7_comfy_output"},
    ]
    source_context = {
        "project_id": project_id,
        "project_version": str(project_version),
        "canvas_id": canvas_id,
    }

    # 2. 携带受控参数与来源上下文提交执行
    started = execute(
        media_client,
        workflow,
        "local_comfy",
        key="c7-t2-comfy-key-1",
        node_info_list=controlled_node_info,
        source_context=source_context,
    )
    assert started.status_code == 202, started.text
    start_body = started.json()
    job_id = start_body["job_id"]
    assert job_id, "必须返回稳定的 job_id"
    assert start_body["target"] == "local_comfy"
    assert start_body["remote_task_id"] == "fake-prompt-0001"

    # 3. 轮询持久任务至完成
    first_poll = media_client.get(f"{TASKS}/{job_id}", headers=HEADERS)
    assert first_poll.status_code == 200
    assert first_poll.json()["status"] == "running"

    second_poll = media_client.get(f"{TASKS}/{job_id}", headers=HEADERS)
    assert second_poll.status_code == 200
    done_task = second_poll.json()
    assert done_task["status"] == "completed"
    assert done_task["collection_status"] == "completed"
    assert len(done_task["outputs"]) >= 1

    registered_output = done_task["outputs"][0]
    assert registered_output["registered"] is True
    asset_id = registered_output["asset_id"]
    assert asset_id, "产物必须包含统一资产 asset_id"
    assert registered_output["url"] == f"/api/god_workflow/assets/{asset_id}/content"

    # 4. 资产中枢登记核验：底层 Asset Registry 与 Asset Library
    after_counts = asset_counts()
    assert after_counts["registry"] == before_counts["registry"] + 1
    assert after_counts["library_image"] == before_counts["library_image"] + 1
    assert asset_id in after_counts["registry_ids"]
    assert asset_id in after_counts["library_image_ids"]

    # 5. 统一资产详情与来源回跳元数据溯源
    asset = _get_registry_asset(media_client, asset_id)
    assert asset["task_id"] == job_id
    metadata = asset["metadata"]
    assert metadata["job_id"] == job_id
    assert metadata["task_id"] == job_id
    assert metadata["workflow_id"] == workflow["workflow_id"]
    assert metadata["workflow_version"] == workflow["version"]
    assert metadata["source_context"]["project_id"] == project_id
    assert metadata["source_context"]["canvas_id"] == canvas_id

    # 验证资产内容可正常按固定哈希取回
    content_res = media_client.get(f"/api/god_workflow/assets/{asset_id}/content", headers=HEADERS)
    assert content_res.status_code == 200
    assert hashlib.sha256(content_res.content).hexdigest() == FIXED_PNG_SHA256

    # 6. 重复回收幂等性验证（不增加重复资产副本）
    settled_counts = asset_counts()
    collect_res = media_client.post(f"{TASKS}/{job_id}/collect", headers=HEADERS)
    assert collect_res.status_code == 200
    assert asset_counts() == settled_counts


def test_c7_t2_runninghub_controlled_params_durable_task_and_unified_asset_loop(
    media_client, fake_media, asset_counts
):
    """C7-AC2 (RunningHub)：受控参数执行 -> 持久任务 -> 终态轮询 -> 统一资产登记 -> 来源与双向回跳。"""
    # 1. 准备项目与受控工作流
    project_id, project_version = _create_project(media_client, "c7-t2-rh-film")
    canvas_id = _create_canvas(media_client, project_id, "c7-rh-canvas")
    input_asset, workflow = prepare_workflow(media_client)
    before_counts = asset_counts()

    controlled_node_info = [
        {"nodeId": "1", "fieldName": "image", "fieldValue": input_asset["filename"]},
        {"nodeId": "2", "fieldName": "filename_prefix", "fieldValue": "c7_rh_output"},
    ]
    source_context = {
        "project_id": project_id,
        "project_version": str(project_version),
        "canvas_id": canvas_id,
    }

    # 2. 提交 RunningHub 执行
    started = execute(
        media_client,
        workflow,
        "rh_cloud",
        key="c7-t2-rh-key-1",
        node_info_list=controlled_node_info,
        source_context=source_context,
    )
    assert started.status_code == 202, started.text
    start_body = started.json()
    job_id = start_body["job_id"]
    assert job_id, "必须返回稳定的 job_id"
    assert start_body["target"] == "rh_cloud"
    assert start_body["remote_task_id"] == "fake-rh-task-0001"

    # 验证受控参数已透传给平台调度器
    create_calls = [args for name, args in fake_media.rh.calls if name == "create_rh_cloud_task"]
    assert len(create_calls) == 1
    assert create_calls[0]["node_info_list"][1] == controlled_node_info[1]
    assert create_calls[0]["node_info_list"][0]["nodeId"] == "1"
    assert create_calls[0]["node_info_list"][0]["fieldName"] == "image"
    assert create_calls[0]["node_info_list"][0]["fieldValue"].startswith("fake-rh/")

    # 3. 轮询持久任务至完成
    first_poll = media_client.get(f"{TASKS}/{job_id}", headers=HEADERS)
    assert first_poll.status_code == 200
    assert first_poll.json()["status"] == "running"

    second_poll = media_client.get(f"{TASKS}/{job_id}", headers=HEADERS)
    assert second_poll.status_code == 200
    done_task = second_poll.json()
    assert done_task["status"] == "completed"
    assert done_task["collection_status"] == "completed"
    assert len(done_task["outputs"]) >= 1

    registered_output = done_task["outputs"][0]
    assert registered_output["registered"] is True
    asset_id = registered_output["asset_id"]

    # 4. 资产中枢登记核验
    after_counts = asset_counts()
    assert after_counts["registry"] == before_counts["registry"] + 1
    assert after_counts["library_image"] == before_counts["library_image"] + 1
    assert asset_id in after_counts["registry_ids"]

    # 5. 溯源与回跳关系验证
    asset = _get_registry_asset(media_client, asset_id)
    assert asset["task_id"] == job_id
    metadata = asset["metadata"]
    assert metadata["job_id"] == job_id
    assert metadata["workflow_id"] == workflow["workflow_id"]
    assert metadata["source_context"]["project_id"] == project_id
    assert metadata["source_context"]["canvas_id"] == canvas_id


def test_c7_t2_cross_tenant_isolation_and_project_context_guard(media_client, fake_media):
    """跨主体租户隔离与未授权项目上下文拦截。"""
    project_id, _ = _create_project(media_client, "c7-t2-isolated-project")
    _, workflow = prepare_workflow(media_client)

    other_tenant_headers = {
        "Authorization": "Bearer other-tenant-token",
        "Origin": "http://testserver",
        "X-User-Role": "editor",
    }

    # 跨租户无权访问该项目上下文发起执行
    denied = execute(
        media_client,
        workflow,
        "local_comfy",
        headers=other_tenant_headers,
        source_context={"project_id": project_id},
    )
    assert denied.status_code in {403, 404}


def test_c7_t2_durable_task_lookup_and_source_provenance_preservation(media_client, fake_media):
    """任务与资产双向可查，刷新/通过稳定 ID 回跳无需文件名猜测。"""
    project_id, project_version = _create_project(media_client, "c7-t2-lookup-proj")
    canvas_id = _create_canvas(media_client, project_id, "c7-lookup-canvas")
    _, workflow = prepare_workflow(media_client)

    started = execute(
        media_client,
        workflow,
        "local_comfy",
        key="c7-t2-lookup-key",
        source_context={"project_id": project_id, "canvas_id": canvas_id},
    )
    assert started.status_code == 202, started.text
    job_id = started.json()["job_id"]

    # 轮询两次至终态
    media_client.get(f"{TASKS}/{job_id}", headers=HEADERS)
    task_res = media_client.get(f"{TASKS}/{job_id}", headers=HEADERS).json()
    assert task_res["status"] == "completed"
    asset_id = task_res["outputs"][0]["asset_id"]

    # 1. 列表查询包含该任务
    list_res = media_client.get(TASKS, headers=HEADERS)
    assert list_res.status_code == 200
    task_ids = [t.get("job_id") or t.get("task_id") for t in list_res.json().get("tasks", [])]
    assert job_id in task_ids

    # 2. 单任务查询准确
    direct_res = media_client.get(f"{TASKS}/{job_id}", headers=HEADERS)
    assert direct_res.status_code == 200
    direct_body = direct_res.json()
    assert direct_body["job_id"] == job_id
    assert direct_body["workflow_id"] == workflow["workflow_id"]
    assert direct_body["outputs"][0]["asset_id"] == asset_id

    # 3. 资产反查任务与来源上下文一致
    asset = _get_registry_asset(media_client, asset_id)
    assert asset["task_id"] == job_id
    assert asset["metadata"]["workflow_id"] == workflow["workflow_id"]
    assert asset["metadata"]["source_context"]["project_id"] == project_id
    assert asset["metadata"]["source_context"]["canvas_id"] == canvas_id


def test_c7_t2_runninghub_real_host_fixture_execution_and_unified_asset(
    media_client, fake_media, asset_counts, monkeypatch
):
    """C7-AC2 真实宿主证据锚定：使用 RunningHub 真实产物 fixture (outputs_success_real_host.json)、
    真实工作流 ID 2108837566177959938 及真实远端任务 ID 格式 1845892349182390192，
    验证真实产物主机 rh-images-tos.xiaoyaoyou.com 的下载白名单校验与统一资产中枢登记。
    """
    import json
    from pathlib import Path
    import httpx
    from gw.god_workflow import platforms, routes

    real_remote_task_id = "1845892349182390192"
    real_host = "rh-images-tos.xiaoyaoyou.com"

    fixture_path = Path(__file__).resolve().parents[1] / "fixtures" / "runninghub" / "outputs_success_real_host.json"
    assert fixture_path.exists(), f"真实 Fixture 不存在: {fixture_path}"
    real_output_fixture = json.loads(fixture_path.read_text(encoding="utf-8"))

    # 配置获准主机及 Mock 传输层支持真实产物主机
    monkeypatch.setenv("GW_WORKFLOW_OUTPUT_HOSTS", f"{APPROVED_HOST},{real_host}")

    def real_host_transport(allowed_hosts=None, verify=True):
        def handler(request: httpx.Request) -> httpx.Response:
            if request.headers.get("host") not in (APPROVED_HOST, real_host):
                raise httpx.ConnectError(f"refuse host {request.headers.get('host')}")
            return httpx.Response(200, content=FIXED_PNG, headers={"content-type": "image/png"})
        return httpx.MockTransport(handler)

    monkeypatch.setattr(routes, "_output_transport", real_host_transport)

    # 替换 fake_media 的返回以锚定真实 Fixture 数据
    async def create_real_task(*args, **kwargs):
        return {"taskId": real_remote_task_id}

    query_count = 0
    async def query_real_task_outputs(task_id, api_key, domain=None):
        nonlocal query_count
        query_count += 1
        if query_count == 1:
            return {"status": "RUNNING", "outputs": [], "raw": {}}
        return {
            "status": "SUCCESS",
            "outputs": [
                {
                    "fileUrl": real_output_fixture["data"][0]["fileUrl"],
                    "fileName": "example-real-host.png",
                }
            ],
            "raw": real_output_fixture,
        }

    monkeypatch.setattr(platforms, "create_rh_cloud_task", create_real_task)
    monkeypatch.setattr(platforms, "query_rh_task_outputs", query_real_task_outputs)

    # 1. 准备项目、画布与受控工作流
    project_id, project_version = _create_project(media_client, "c7-t2-real-rh-project")
    canvas_id = _create_canvas(media_client, project_id, "c7-real-rh-canvas")
    input_asset, workflow = prepare_workflow(media_client)
    before_counts = asset_counts()

    controlled_node_info = [
        {"nodeId": "1", "fieldName": "image", "fieldValue": input_asset["filename"]},
        {"nodeId": "2", "fieldName": "filename_prefix", "fieldValue": "c7_real_rh_output"},
    ]
    source_context = {
        "project_id": project_id,
        "project_version": str(project_version),
        "canvas_id": canvas_id,
        "shot_id": "SC01_SH001",
    }

    # 2. 提交执行
    started = execute(
        media_client,
        workflow,
        "rh_cloud",
        key="c7-t2-real-rh-key-1",
        node_info_list=controlled_node_info,
        source_context=source_context,
    )
    assert started.status_code == 202, started.text
    start_body = started.json()
    job_id = start_body["job_id"]
    assert start_body["remote_task_id"] == real_remote_task_id

    # 3. 轮询持久任务至完成
    first_poll = media_client.get(f"{TASKS}/{job_id}", headers=HEADERS)
    assert first_poll.status_code == 200
    assert first_poll.json()["status"] == "running"

    second_poll = media_client.get(f"{TASKS}/{job_id}", headers=HEADERS)
    assert second_poll.status_code == 200
    done_task = second_poll.json()
    assert done_task["status"] == "completed"
    assert done_task["collection_status"] == "completed"
    assert len(done_task["outputs"]) == 1

    registered_output = done_task["outputs"][0]
    assert registered_output["registered"] is True
    asset_id = registered_output["asset_id"]
    assert asset_id

    # 4. 验证统一资产中枢元数据与来源溯源
    after_counts = asset_counts()
    assert after_counts["registry"] == before_counts["registry"] + 1
    assert asset_id in after_counts["registry_ids"]

    asset = _get_registry_asset(media_client, asset_id)
    assert asset["task_id"] == job_id
    assert asset["metadata"]["source_context"]["project_id"] == project_id
    assert asset["metadata"]["source_context"]["canvas_id"] == canvas_id
    assert done_task["source_context"]["project_id"] == project_id
    assert done_task["source_context"]["canvas_id"] == canvas_id

    # 5. 验证资产二进制可按固定哈希拉取
    content_res = media_client.get(f"/api/god_workflow/assets/{asset_id}/content", headers=HEADERS)
    assert content_res.status_code == 200
    assert hashlib.sha256(content_res.content).hexdigest() == FIXED_PNG_SHA256

