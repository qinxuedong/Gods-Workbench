"""C4-AC15：结果未知任务同一错误最多自动弹出一次，之后只在任务面板显示徽标。"""
import json
import os
from pathlib import Path
from urllib.parse import urlsplit

from workflow_support import goto_ready, settings_browser

TASK = {"job_id": "job-unknown-1", "task_id": "job-unknown-1", "status": "outcome_unknown",
        "error_code": "RH_OUTCOME_UNKNOWN", "target": "rh_cloud", "outputs": [],
        "messages": ["外部请求可能已产生副作用，请人工核实"]}


def _shot(page, name):
    # 仅在 QA 显式指定目录时截图，常规测试不落盘。
    folder = os.environ.get("GW_QA_SHOT_DIR")
    if folder:
        Path(folder).mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(Path(folder) / name))


def test_outcome_unknown_task_pops_once_then_badge_only(monkeypatch, tmp_path):
    with settings_browser(monkeypatch, tmp_path) as (page, base, errors, _):
        def tasks(route):
            path = urlsplit(route.request.url).path
            body = {"tasks": [TASK]} if path.endswith("/tasks") else TASK
            route.fulfill(status=200, content_type="application/json", body=json.dumps(body))
        page.route("**/api/god_workflow/tasks**", tasks)
        modal = page.locator("#taskModal")
        goto_ready(page, base + "/static/pages/workflow.html")
        page.wait_for_function("() => document.getElementById('taskModal')?.classList.contains('open')")
        _shot(page, "1-first-visit.png")
        page.keyboard.press("Escape")
        page.wait_for_function("() => !document.getElementById('taskModal').classList.contains('open')")
        for attempt in range(2):
            # 等到恢复流程真正拿到任务列表后再断言“没有弹出”。
            with page.expect_response(lambda r: urlsplit(r.url).path == "/api/god_workflow/tasks"):
                goto_ready(page, base + "/static/pages/workflow.html")
            page.wait_for_load_state("networkidle")
            _shot(page, f"2-revisit-{attempt + 1}.png")
            assert "open" not in (modal.get_attribute("class") or ""), "已知悉的结果未知任务不应再次自动弹出"
        # 打开任务面板：只显示状态标记，不自动展开错误详情。
        with page.expect_response(lambda r: urlsplit(r.url).path == "/api/god_workflow/tasks"):
            page.locator("#btnViewTaskDrawer").click()
        expand = page.locator('#taskModalBody [data-task-expand="job-unknown-1"]')
        expand.wait_for()
        assert page.locator('#taskModalBody [data-task-badge="outcome_unknown"]').count() == 1
        assert "Job ID" not in page.locator("#taskModalBody").inner_text(), "打开面板不应自动展开错误详情"
        assert page.locator('#workflowTaskSelect option[data-status="outcome_unknown"]').count() == 1
        _shot(page, "3-task-panel-collapsed.png")
        # 用户点击该任务才展开详情。
        expand.click()
        page.locator("#taskModalBody").get_by_text("Job ID: job-unknown-1").wait_for()
        _shot(page, "4-click-to-expand.png")
        assert errors == []
