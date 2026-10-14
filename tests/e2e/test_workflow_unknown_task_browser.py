"""C4-AC15：结果未知任务同一错误最多自动弹出一次；任务面板列出当前工作流多条任务（新→旧），点击才展开。"""
import json
import os
from pathlib import Path
from urllib.parse import quote, urlsplit

from workflow_support import goto_ready, settings_browser

UNKNOWN = "cccccccc-unknown-0001"
DONE = "aaaaaaaa-done-0002"
OTHER = "bbbbbbbb-otherwf-0003"


def _tasks(workflow_id):
    return [
        {"job_id": DONE, "task_id": DONE, "workflow_id": workflow_id, "status": "completed", "target": "local_comfy",
         "created_at": "2026-10-14T01:00:00+00:00", "outputs": [], "messages": ["已完成"]},
        {"job_id": OTHER, "task_id": OTHER, "workflow_id": "wf-other", "status": "completed", "target": "rh_cloud",
         "created_at": "2026-10-14T03:00:00+00:00", "outputs": [], "messages": []},
        {"job_id": UNKNOWN, "task_id": UNKNOWN, "workflow_id": workflow_id, "status": "outcome_unknown",
         "error_code": "RH_OUTCOME_UNKNOWN", "target": "rh_cloud", "created_at": "2026-10-14T02:00:00+00:00",
         "outputs": [], "messages": ["外部请求可能已产生副作用，请人工核实"]},
    ]


def _shot(page, name):
    # 仅在 QA 显式指定目录时截图，常规测试不落盘。
    folder = os.environ.get("GW_QA_SHOT_DIR")
    if folder:
        Path(folder).mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(Path(folder) / name))


def test_outcome_unknown_task_pops_once_then_list_click_to_expand(monkeypatch, tmp_path):
    with settings_browser(monkeypatch, tmp_path) as (page, base, errors, _):
        created = page.context.request.post(base + "/api/god_workflow/documents?name=" + quote("unknown"),
            data={"1": {"class_type": "LoadImage", "inputs": {"image": "a.png"}}}, headers={"Origin": base})
        assert created.status == 201, created.text()
        wid = created.json()["workflow_id"]
        tasks = _tasks(wid)

        def fulfil(route):
            path = urlsplit(route.request.url).path
            if path == "/api/god_workflow/tasks":
                body = {"tasks": tasks}
            else:
                body = next(t for t in tasks if path.endswith("/" + t["job_id"]))
            route.fulfill(status=200, content_type="application/json", body=json.dumps(body))
        page.route("**/api/god_workflow/tasks**", fulfil)
        url = base + "/static/pages/workflow.html?id=" + wid
        modal = page.locator("#taskModal")
        goto_ready(page, url)
        page.wait_for_function("() => document.getElementById('taskModal')?.classList.contains('open')")
        _shot(page, "1-first-visit.png")
        page.keyboard.press("Escape")
        page.wait_for_function("() => !document.getElementById('taskModal').classList.contains('open')")
        for attempt in range(2):
            # 等到恢复流程真正拿到任务列表后再断言“没有弹出”。
            with page.expect_response(lambda r: urlsplit(r.url).path == "/api/god_workflow/tasks"):
                goto_ready(page, url)
            page.wait_for_load_state("networkidle")
            _shot(page, f"2-revisit-{attempt + 1}.png")
            assert "open" not in (modal.get_attribute("class") or ""), "已知悉的结果未知任务不应再次自动弹出"
        # 打开任务面板：当前工作流的任务新→旧列出，结果未知不自动展开。
        page.wait_for_function("(id) => typeof state !== 'undefined' && state.currentWorkflow?.workflow_id === id", arg=wid)
        with page.expect_response(lambda r: urlsplit(r.url).path == "/api/god_workflow/tasks"):
            page.locator("#btnViewTaskDrawer").click()
        rows = page.locator("#workflowTaskList [data-task-row]")
        rows.first.wait_for()
        assert rows.evaluate_all("els => els.map(e => e.dataset.taskRow)") == [UNKNOWN, DONE]
        first = rows.nth(0)
        assert first.locator("[data-task-short-id]").inner_text() == UNKNOWN[:8]
        assert first.locator("[data-task-executor]").inner_text() == "RH"
        assert rows.nth(1).locator("[data-task-executor]").inner_text() == "ComfyUI"
        assert first.locator("[data-task-time]").inner_text().startswith("2026-10-14")
        assert first.locator('[data-task-badge="outcome_unknown"]').count() == 1
        assert "Job ID" not in page.locator("#taskModalBody").inner_text(), "打开面板不应自动展开结果未知详情"
        _shot(page, "3-task-panel-list.png")
        # 点击结果未知行才展开详情。
        first.click()
        page.locator("#taskModalBody").get_by_text("Job ID: " + UNKNOWN).wait_for()
        assert first.get_attribute("aria-expanded") == "true"
        _shot(page, "4-click-unknown-expand.png")
        # 点击另一行展开对应任务。
        rows.nth(1).click()
        page.locator("#taskModalBody").get_by_text("Job ID: " + DONE).wait_for()
        _shot(page, "5-click-other-expand.png")
        assert errors == []
