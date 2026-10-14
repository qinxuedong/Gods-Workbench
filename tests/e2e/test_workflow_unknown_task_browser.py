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
        {"job_id": OTHER, "task_id": OTHER, "workflow_id": "wf-other", "status": "running", "target": "rh_cloud",
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
        # 首次弹出的必须是当前工作流的结果未知任务，而不是其他工作流更新的运行中任务。
        page.locator("#taskModalBody").get_by_text("Job ID: " + UNKNOWN).wait_for()
        assert page.evaluate("state.activeTaskId") == UNKNOWN
        _shot(page, "1-first-visit.png")
        page.keyboard.press("Escape")
        page.wait_for_function("() => !document.getElementById('taskModal').classList.contains('open')")
        for attempt in range(2):
            # 等到恢复流程真正拿到任务列表后再断言“没有弹出”。
            with page.expect_response(lambda r: urlsplit(r.url).path == "/api/god_workflow/tasks"):
                goto_ready(page, url)
            page.wait_for_load_state("networkidle")
            _shot(page, f"2-revisit-{attempt + 1}.png")
            assert "open" not in (modal.get_attribute("class") or ""), "已知悉的结果未知任务与其他工作流运行中任务都不应自动弹出"
            assert page.evaluate("state.activeTaskId || null") is None
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


def test_other_workflow_running_task_never_pops_and_paged_load_more(monkeypatch, tmp_path):
    """新后端契约（回显 workflow_id + next_cursor）下：其他工作流运行中任务不弹出；「加载更多」按 cursor 追加。"""
    with settings_browser(monkeypatch, tmp_path) as (page, base, errors, _):
        ids = []
        for name in ("current", "other"):
            created = page.context.request.post(base + "/api/god_workflow/documents?name=" + name,
                data={"1": {"class_type": "LoadImage", "inputs": {"image": "a.png"}}}, headers={"Origin": base})
            assert created.status == 201, created.text()
            ids.append(created.json()["workflow_id"])
        current, other = ids
        running_other = {"job_id": "dddddddd-running-other", "task_id": "dddddddd-running-other", "workflow_id": other,
                         "status": "running", "target": "rh_cloud", "created_at": "2026-10-14T09:00:00+00:00", "outputs": []}
        page1 = [{"job_id": f"eeeeeee{i}-page1", "task_id": f"eeeeeee{i}-page1", "workflow_id": current, "status": "completed",
                  "target": "local_comfy", "created_at": f"2026-10-14T0{8 - i}:00:00+00:00", "outputs": []} for i in range(2)]
        page2 = [{"job_id": "ffffffff-page2", "task_id": "ffffffff-page2", "workflow_id": current, "status": "failed",
                  "target": "rh_cloud", "created_at": "2026-10-14T01:00:00+00:00", "outputs": [], "messages": ["x"]}]
        seen = []

        def fulfil(route):
            parts = urlsplit(route.request.url)
            query = dict(p.split("=", 1) for p in parts.query.split("&") if "=" in p)
            seen.append(query)
            if parts.path == "/api/god_workflow/tasks":
                wid = query.get("workflow_id")
                if wid == other:
                    body = {"tasks": [running_other], "workflow_id": other, "next_cursor": None, "has_more": False}
                elif wid == current:
                    body = ({"tasks": page2, "workflow_id": current, "next_cursor": None, "has_more": False}
                            if query.get("cursor") == "p2" else
                            {"tasks": page1, "workflow_id": current, "next_cursor": "p2", "has_more": True})
                else:
                    body = {"tasks": [running_other, *page1, *page2]}
            else:
                body = next(t for t in [running_other, *page1, *page2] if parts.path.endswith("/" + t["job_id"]))
            route.fulfill(status=200, content_type="application/json", body=json.dumps(body))
        page.route("**/api/god_workflow/tasks**", fulfil)
        modal = page.locator("#taskModal")
        for _ in range(2):
            with page.expect_response(lambda r: urlsplit(r.url).path == "/api/god_workflow/tasks"):
                goto_ready(page, base + "/static/pages/workflow.html?id=" + current)
            page.wait_for_load_state("networkidle")
            assert "open" not in (modal.get_attribute("class") or ""), "其他工作流的运行中任务不应自动弹出"
        assert any(q.get("workflow_id") == current and q.get("limit") for q in seen)
        _shot(page, "6-other-running-no-pop.png")
        with page.expect_response(lambda r: urlsplit(r.url).path == "/api/god_workflow/tasks"):
            page.locator("#btnViewTaskDrawer").click()
        rows = page.locator("#workflowTaskList [data-task-row]")
        rows.first.wait_for()
        assert rows.count() == 2
        more = page.locator("#workflowTaskList [data-task-load-more]")
        _shot(page, "7-load-more-before.png")
        with page.expect_response(lambda r: "cursor=p2" in r.url):
            more.click()
        page.wait_for_function("() => document.querySelectorAll('#workflowTaskList [data-task-row]').length === 3")
        assert rows.evaluate_all("els => els.map(e => e.dataset.taskRow)") == ["eeeeeee0-page1", "eeeeeee1-page1", "ffffffff-page2"]
        assert more.count() == 0
        assert page.locator('#workflowTaskList [data-task-row="dddddddd-running-other"]').count() == 0
        _shot(page, "8-load-more-after.png")
        assert errors == []
