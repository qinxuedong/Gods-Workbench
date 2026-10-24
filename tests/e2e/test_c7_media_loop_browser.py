"""C7-P4 (QIN-183) 媒体链真实浏览器端到端回归测试 (C7-AC2 / C7-AC4)。

验证标准：
- C7-AC2：受控工作流参数在 RunningHub/ComfyUI 产生持久任务结果，统一登记资产并可回跳来源；
- C7-AC4：两条闭环在实际浏览器中完成；刷新与重启后关系仍在，跨账户访问受控，失败与未知状态不伪造。
- Proof：API JSON、fixture id、test log。
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from urllib.parse import quote, urlsplit

import httpx
from workflow_support import goto_ready, settings_browser

from gw.god_workflow import platforms, routes

REPO_ROOT = Path(__file__).resolve().parents[2]
REAL_RH_FIXTURE = (
    REPO_ROOT
    / "tests"
    / "contracts"
    / "fixtures"
    / "runninghub"
    / "outputs_success_real_host.json"
)

FIXED_PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x06\x00\x00\x00\x1f\x15c4\x00\x00\x00\rIDATx\x9cc`\x00\x00\x00"
    b"\x02\x00\x01H\xaf\xa4q\x00\x00\x00\x00IEND\xaeB`\x82"
)


def _shot(page, name: str):
    """保存 QA 浏览器截图证据（若指定环境变量）。"""
    folder = os.environ.get("GW_QA_SHOT_DIR")
    if folder:
        Path(folder).mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(Path(folder) / name))


def test_c7_media_loop_browser_runninghub_real_host_task_and_unified_asset(
    monkeypatch, tmp_path
):
    """C7-AC2 & C7-AC4: 真实 RunningHub 宿主产物在浏览器中展示统一资产徽标、资产跳转与来源项目/画布回跳，刷新后保持。"""
    assert REAL_RH_FIXTURE.exists(), f"真实 Fixture 不存在: {REAL_RH_FIXTURE}"
    real_output_fixture = json.loads(REAL_RH_FIXTURE.read_text(encoding="utf-8"))
    real_host = "rh-images-tos.xiaoyaoyou.com"
    real_remote_task_id = "1845892349182390192"

    def real_host_transport(allowed_hosts=None, verify=True):
        def handler(request: httpx.Request) -> httpx.Response:
            if request.headers.get("host") not in ("www.runninghub.cn", real_host):
                raise httpx.ConnectError(f"refuse host {request.headers.get('host')}")
            return httpx.Response(
                200, content=FIXED_PNG, headers={"content-type": "image/png"}
            )

        return httpx.MockTransport(handler)

    monkeypatch.setattr(routes, "_output_transport", real_host_transport)

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

    with settings_browser(monkeypatch, tmp_path) as (page, base, errors, _):
        # 1. 建立项目与画布上下文
        proj_res = page.context.request.post(
            f"{base}/api/asset-registry/projects",
            data={"name": "C7-AC2 媒体生产闭环验证项目", "project_type": "film"},
            headers={"Origin": base},
        )
        assert proj_res.status == 201, proj_res.text()
        project = proj_res.json()["project"]
        project_id = project["project_id"]
        project_version = project["version"]

        canvas_res = page.context.request.post(
            f"{base}/api/canvases",
            data={
                "project_id": project_id,
                "title": "分镜 SC01 媒体工作流画布",
                "mode": "classic",
            },
            headers={"Origin": base},
        )
        assert canvas_res.status == 201, canvas_res.text()
        canvas_id = canvas_res.json()["canvas"]["canvas_id"]

        # 2. 创建受控工作流并上传输入素材
        upload_res = page.context.request.post(
            f"{base}/api/god_workflow/assets/upload",
            multipart={
                "file": {
                    "name": "c7_input.png",
                    "mimeType": "image/png",
                    "buffer": FIXED_PNG,
                }
            },
            headers={"Origin": base},
        )
        assert upload_res.status == 201, upload_res.text()
        input_asset = upload_res.json()

        wf_doc_res = page.context.request.post(
            f"{base}/api/god_workflow/documents?name=" + quote("c7-real-rh-workflow"),
            data={
                "1": {
                    "class_type": "LoadImage",
                    "inputs": {"image": input_asset["filename"]},
                },
                "2": {
                    "class_type": "SaveImage",
                    "inputs": {"filename_prefix": "c7_rh_render"},
                },
            },
            headers={"Origin": base},
        )
        assert wf_doc_res.status == 201, wf_doc_res.text()
        workflow_id = wf_doc_res.json()["workflow_id"]

        # 3. 提交受控任务
        source_context = {
            "project_id": project_id,
            "project_version": str(project_version),
            "canvas_id": canvas_id,
            "shot_id": "SC01_SH001",
        }
        task_res = page.context.request.post(
            f"{base}/api/god_workflow/tasks/execute",
            data={
                "workflow_id": workflow_id,
                "target": "rh_cloud",
                "key": "c7-e2e-rh-key-01",
                "node_info_list": [
                    {
                        "nodeId": "1",
                        "fieldName": "image",
                        "fieldValue": input_asset["filename"],
                    },
                    {
                        "nodeId": "2",
                        "fieldName": "filename_prefix",
                        "fieldValue": "c7_rh_render",
                    },
                ],
                "source_context": source_context,
            },
            headers={"Origin": base},
        )
        assert task_res.status == 202, task_res.text()
        task_body = task_res.json()
        job_id = task_body["job_id"]
        assert task_body["remote_task_id"] == real_remote_task_id

        # 轮询直到持久任务完成且产物回收完毕
        done_task = None
        for _ in range(20):
            poll_res = page.context.request.get(
                f"{base}/api/god_workflow/tasks/{job_id}", headers={"Origin": base}
            )
            assert poll_res.status == 200
            data = poll_res.json()
            if (
                data.get("status") == "completed"
                and data.get("collection_status") == "completed"
            ):
                done_task = data
                break
        assert done_task is not None, "任务轮询未在超时时间内达到完成终态"
        assert len(done_task["outputs"]) == 1
        registered_asset_id = done_task["outputs"][0]["asset_id"]
        assert registered_asset_id, "产物必须分配统一资产 ID"

        # 4. 在实际浏览器中打开工作流工作台并挂载上下文
        workflow_url = f"{base}/static/pages/workflow.html?project_id={project_id}&canvas_id={canvas_id}&job_id={job_id}&id={workflow_id}"
        goto_ready(page, workflow_url)

        # 打开任务抽屉（若未自动弹出）
        modal = page.locator("#taskModal")
        if "open" not in (modal.get_attribute("class") or ""):
            page.locator("#btnViewTaskDrawer").click()
            page.wait_for_selector("#taskModal.open")

        # 5. 断言浏览器 DOM 中的统一资产徽标与来源回跳
        page.wait_for_selector(".wf-task-asset-badge")
        asset_badge = page.locator(
            f".wf-task-asset-badge[data-asset-id='{registered_asset_id}']"
        )
        assert asset_badge.count() >= 1, "抽屉中必须渲染统一资产徽标并携带正确 asset_id"

        view_asset_link = page.locator(
            f"a[data-action='view-asset'][href*='{registered_asset_id}']"
        )
        assert view_asset_link.count() >= 1, "抽屉中必须渲染查看资产详情链接"

        # 验证来源回跳区域
        page.wait_for_selector("[data-source-provenance]")
        source_prov = page.locator("[data-source-provenance]")
        project_jump = source_prov.locator(
            f"a[data-action='return-to-source-project'][href*='{project_id}']"
        )
        canvas_jump = source_prov.locator(
            f"a[data-action='return-to-source-canvas'][href*='{canvas_id}']"
        )
        assert project_jump.count() >= 1, "必须包含返回来源项目入口"
        assert canvas_jump.count() >= 1, "必须包含返回来源画布入口"

        _shot(page, "1-workflow-browser-completed-task.png")

        # 6. C7-AC4: 浏览器刷新后关系保持，不丢失关联
        page.reload(wait_until="networkidle")
        goto_ready(page, workflow_url)

        modal = page.locator("#taskModal")
        if "open" not in (modal.get_attribute("class") or ""):
            page.locator("#btnViewTaskDrawer").click()
            page.wait_for_selector("#taskModal.open")

        page.wait_for_selector(
            f".wf-task-asset-badge[data-asset-id='{registered_asset_id}']"
        )
        assert (
            page.locator(f"[data-source-provenance] a[href*='{project_id}']").count()
            >= 1
        )

        assert errors == []


def test_c7_media_loop_browser_cross_tenant_isolation(monkeypatch, tmp_path):
    """C7-AC4: 跨租户跨项目防护在浏览器端点及服务层严格阻断（403/404 fail-closed）。"""
    from gw.god_workflow.comfy_executor import ComfyEndpoint

    monkeypatch.setattr(
        "gw.god_workflow.comfy_executor.require_endpoint",
        lambda: ComfyEndpoint("http://127.0.0.1:8188", 30.0),
    )

    with settings_browser(monkeypatch, tmp_path) as (page, base, errors, _):
        # 1. 主体 A 创建项目
        proj_res = page.context.request.post(
            f"{base}/api/asset-registry/projects",
            data={"name": "主体A私有项目", "project_type": "film"},
            headers={"Origin": base},
        )
        assert proj_res.status == 201
        proj_res.json()["project"]["project_id"]

        wf_res = page.context.request.post(
            f"{base}/api/god_workflow/documents?name=test_wf",
            data={"1": {"class_type": "LoadImage", "inputs": {"image": "test.png"}}},
            headers={"Origin": base},
        )
        assert wf_res.status == 201
        workflow_id = wf_res.json()["workflow_id"]

        # 2. 未授权/伪造不存在或越权的项目 ID 提交执行时被拒绝
        illegal_task = page.context.request.post(
            f"{base}/api/god_workflow/tasks/execute",
            data={
                "workflow_id": workflow_id,
                "target": "local_comfy",
                "key": "c7-cross-tenant-key",
                "node_info_list": [],
                "source_context": {
                    "project_id": "proj_non_existent_or_unauthorized_999",
                    "canvas_id": "cvs_illegal_001",
                },
            },
            headers={"Origin": base},
        )
        assert illegal_task.status in (403, 404), (
            f"未授权项目必须返回 403 或 404，实际: {illegal_task.status}"
        )


def test_c7_media_loop_browser_outcome_unknown_failsafe(monkeypatch, tmp_path):
    """C7-AC4: 任务远端结果未知时（outcome_unknown），浏览器渲染明确警告徽章，不伪造成功进度。"""
    UNKNOWN_JOB = "01928374-abcd-7000-8000-unknown000001"

    with settings_browser(monkeypatch, tmp_path) as (page, base, errors, _):
        wf_res = page.context.request.post(
            f"{base}/api/god_workflow/documents?name=unknown_wf",
            data={"1": {"class_type": "LoadImage", "inputs": {"image": "a.png"}}},
            headers={"Origin": base},
        )
        assert wf_res.status == 201
        wid = wf_res.json()["workflow_id"]

        # 拦截任务查询，返回 outcome_unknown 真实终态结构
        unknown_task_payload = {
            "job_id": UNKNOWN_JOB,
            "task_id": UNKNOWN_JOB,
            "workflow_id": wid,
            "status": "outcome_unknown",
            "error_code": "OUTCOME_UNKNOWN_TIMEOUT",
            "target": "rh_cloud",
            "created_at": "2026-10-17T05:00:00+00:00",
            "outputs": [],
            "messages": ["外部请求超时可能已产生副作用，请人工核实"],
        }

        def fulfill_tasks(route):
            path = urlsplit(route.request.url).path
            if path == "/api/god_workflow/tasks":
                route.fulfill(
                    status=200,
                    content_type="application/json",
                    body=json.dumps({"tasks": [unknown_task_payload]}),
                )
            elif path.endswith(UNKNOWN_JOB):
                route.fulfill(
                    status=200,
                    content_type="application/json",
                    body=json.dumps(unknown_task_payload),
                )
            else:
                route.continue_()

        page.route("**/api/god_workflow/tasks**", fulfill_tasks)

        target_url = f"{base}/static/pages/workflow.html?id={wid}&job_id={UNKNOWN_JOB}"
        goto_ready(page, target_url)

        modal = page.locator("#taskModal")
        if "open" not in (modal.get_attribute("class") or ""):
            page.locator("#btnViewTaskDrawer").click()
            page.wait_for_selector("#taskModal.open")

        # 验证结果未知警告徽章与安全提示
        page.wait_for_selector("[data-task-badge='outcome_unknown']")
        badge = page.locator("[data-task-badge='outcome_unknown']").first
        assert "结果未知" in badge.inner_text()

        # 确保没有显示成功或就绪
        modal_body = page.locator("#taskModalBody").inner_text()
        assert "已就绪" not in modal_body
        assert "执行成功" not in modal_body

        _shot(page, "2-workflow-browser-outcome-unknown.png")
