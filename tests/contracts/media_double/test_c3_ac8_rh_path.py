"""C3-AC8 替身彩排（RH 专属路径）：真实供应商验收由秦先生本人执行（cut 2026-10-13 10:38，团队不碰 key）。

本文件只用现有媒体替身（media_doubles + conftest），把 RH 路径的关键接线锁定为契约：
monkeypatch gw.god_workflow.platforms 的三个模块属性（upload_rh_input / create_rh_cloud_task /
query_rh_task_outputs）；GW_RUNNINGHUB_API_KEY 由 conftest media_env setenv 测试值、走真实读取逻辑：
本地输入自动上传 → create_rh_cloud_task → GET /tasks/{id} 轮询 → collect 登记统一资产
（task_id + metadata.workflow_id）→ GET 任务 outputs 含 asset_id → 重复 collect 不重复登记，全程零真实网络。
不重复 test_c3_media_closed_loop.py 的失败 / 部分回收 / 重启 / capabilities 矩阵。
"""

from __future__ import annotations

from media_doubles import (
    FIXED_PNG_SHA256,
    HEADERS,
    TASKS,
    TEST_API_KEY,
    execute,
    prepare_workflow,
)

from gw.god_workflow import platforms


def test_ac8_rh_path_upload_create_poll_collect_registers_unified_asset(
    media_client, fake_rh, no_network, asset_counts, monkeypatch
):
    """C3-AC8-RH-PATH-01：调用顺序、key 来源、轮询状态机与统一资产登记（裁定 9a）。"""
    # 三个平台函数确实是替身在模块属性上的 monkeypatch，路由层走真实代码。
    assert platforms.upload_rh_input.__self__ is fake_rh
    assert platforms.create_rh_cloud_task.__self__ is fake_rh
    assert platforms.query_rh_task_outputs.__self__ is fake_rh
    # 替身记录不落 key；用包装捕获实参，证明 key 从 GW_RUNNINGHUB_API_KEY 经真实读取逻辑传入。
    captured: dict[str, str] = {}
    original_upload, original_create, original_query = (
        fake_rh.upload_rh_input,
        fake_rh.create_rh_cloud_task,
        fake_rh.query_rh_task_outputs,
    )

    async def capture_upload(
        payload,
        filename,
        content_type,
        api_key,
        domain=platforms.RUNNINGHUB_DEFAULT_DOMAIN,
    ):
        captured["upload"] = api_key
        return await original_upload(payload, filename, content_type, api_key, domain)

    async def capture_create(
        workflow_id,
        node_info_list,
        api_key,
        domain=platforms.RUNNINGHUB_DEFAULT_DOMAIN,
        **kwargs,
    ):
        captured["create"] = api_key
        return await original_create(
            workflow_id, node_info_list, api_key, domain, **kwargs
        )

    async def capture_query(
        task_id, api_key, domain=platforms.RUNNINGHUB_DEFAULT_DOMAIN
    ):
        captured.setdefault("query", api_key)
        return await original_query(task_id, api_key, domain)

    monkeypatch.setattr(platforms, "upload_rh_input", capture_upload)
    monkeypatch.setattr(platforms, "create_rh_cloud_task", capture_create)
    monkeypatch.setattr(platforms, "query_rh_task_outputs", capture_query)

    input_asset, workflow = prepare_workflow(media_client)
    before = asset_counts()
    started = execute(media_client, workflow, "rh_cloud", key="ac8-rh-path-1")
    assert started.status_code == 202, started.text
    started = started.json()

    # 1. 本地输入自动上传：真实字节、按资产 ID 命名，先于建任务；key 一律来自环境变量测试值。
    assert captured == {"upload": TEST_API_KEY, "create": TEST_API_KEY}
    upload, create = fake_rh.calls[0], fake_rh.calls[1]
    assert upload[0] == "upload_rh_input" and create[0] == "create_rh_cloud_task"
    assert upload[1]["sha256"] == FIXED_PNG_SHA256
    assert upload[1]["filename"].startswith(input_asset["asset_id"])
    assert upload[1]["domain"] == platforms.RUNNINGHUB_DEFAULT_DOMAIN
    assert create[1]["workflow_id"] is None  # 本地图没有 RH 标识就不编造 workflowId
    uploaded_fields = {
        (entry["nodeId"], entry["fieldName"]): entry["fieldValue"]
        for entry in create[1]["node_info_list"]
    }
    assert (
        "fake-rh/input-0001.png" in uploaded_fields.values()
    )  # 上传得到的远端名回填进建任务参数
    assert started["remote_task_id"] == "fake-rh-task-0001"

    # 2. GET 轮询：第一查 RUNNING（不登记资产），第二查 SUCCESS 并当场回收登记。
    first = media_client.get(f"{TASKS}/{started['job_id']}", headers=HEADERS)
    assert first.status_code == 200, first.text
    assert first.json()["status"] == "running"
    assert captured.get("query") == TEST_API_KEY  # 轮询查询同样用环境变量里的测试 key
    assert asset_counts() == before  # RUNNING 轮询不产生资产
    second = media_client.get(f"{TASKS}/{started['job_id']}", headers=HEADERS).json()
    assert (
        second["status"] == "completed" and second["collection_status"] == "completed"
    )
    assert [item["registered"] for item in second["outputs"]] == [True]
    assert fake_rh.names() == [
        "upload_rh_input",
        "create_rh_cloud_task",
        "query_rh_task_outputs",
        "query_rh_task_outputs",
    ]

    # 3. 统一资产：task_id = job_id、metadata.workflow_id 可回跳；任务 outputs 含 asset_id。
    asset_id = second["outputs"][0]["asset_id"]
    asset = media_client.get(
        f"/api/asset-registry/assets/{asset_id}", headers=HEADERS
    ).json()["asset"]
    assert asset["task_id"] == started["job_id"]
    assert asset["metadata"]["workflow_id"] == workflow["workflow_id"]
    assert asset["metadata"]["workflow_version"] == workflow["version"]
    readback = media_client.get(f"{TASKS}/{started['job_id']}", headers=HEADERS).json()
    assert asset_id in [item["asset_id"] for item in readback["outputs"]]
    after = asset_counts()
    assert after["registry"] == before["registry"] + 1
    assert after["library_image"] == before["library_image"] + 1
    assert no_network == []  # 全程没有替身之外的真实网络


def test_ac8_rh_collect_guard_and_repeat_never_duplicates(
    media_client, fake_rh, no_network, asset_counts
):
    """C3-AC8-RH-COLLECT-01：collect 门禁（409 且零远端查询）+ 重复回收零查询、零重复资产。"""
    _, workflow = prepare_workflow(media_client)
    started = execute(media_client, workflow, "rh_cloud", key="ac8-rh-collect-1")
    assert started.status_code == 202, started.text
    job_id = started.json()["job_id"]

    # 1. 执行未确认完成时 collect 拒绝，且不触发任何远端查询（裁定 5）。
    guarded = media_client.post(f"{TASKS}/{job_id}/collect", headers=HEADERS)
    assert guarded.status_code == 409, guarded.text
    assert guarded.json()["detail"]["code"] == "EXECUTION_NOT_COMPLETED"
    assert fake_rh.count("query_rh_task_outputs") == 0

    # 2. 轮询到完成后，重复 collect 幂等：输出一致、不再查询、上传与建任务各一次、资产数不变。
    first = media_client.get(f"{TASKS}/{job_id}", headers=HEADERS)
    assert first.status_code == 200 and first.json()["status"] == "running"
    done = media_client.get(f"{TASKS}/{job_id}", headers=HEADERS).json()
    assert done["status"] == "completed" and done["collection_status"] == "completed"
    settled = asset_counts()
    outputs = done["outputs"]
    for _ in range(3):
        again = media_client.post(f"{TASKS}/{job_id}/collect", headers=HEADERS)
        assert again.status_code == 200, again.text
        assert again.json()["outputs"] == outputs
    assert fake_rh.count("query_rh_task_outputs") == 2
    assert (
        fake_rh.count("upload_rh_input") == 1
        and fake_rh.count("create_rh_cloud_task") == 1
    )
    assert asset_counts() == settled
    assert no_network == []
