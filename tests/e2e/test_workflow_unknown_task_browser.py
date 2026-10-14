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


def test_outcome_unknown_task_never_pops_on_view_switch_and_list_click_to_expand(monkeypatch, tmp_path):
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
        for attempt in range(3):
            # r6：结果未知任务在切换界面时首次也不弹出；等到恢复流程真正拿到任务列表后再断言“没有弹出”。
            with page.expect_response(lambda r: urlsplit(r.url).path == "/api/god_workflow/tasks"):
                goto_ready(page, url)
            page.wait_for_load_state("networkidle")
            _shot(page, f"2-revisit-{attempt + 1}.png")
            assert "open" not in (modal.get_attribute("class") or ""), "结果未知任务与其他工作流运行中任务都不应自动弹出"
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


def test_real_backend_paging_loads_all_130_tasks_without_duplicates(monkeypatch, tmp_path):
    """真实后端联调：当前工作流 130 条 + 其他工作流 5 条（含运行中），「加载更多」逐页取全且无重复。
    后端尚未提供分页（无 next_cursor）时跳过；后端分页由 PR #35 提供。"""
    import uuid
    from datetime import datetime, timedelta, timezone
    import pytest
    from gw.god_workflow import registry
    with settings_browser(monkeypatch, tmp_path) as (page, base, errors, _):
        ids = []
        for name in ("current", "other"):
            created = page.context.request.post(base + "/api/god_workflow/documents?name=" + name,
                data={"1": {"class_type": "LoadImage", "inputs": {"image": "a.png"}}}, headers={"Origin": base})
            assert created.status == 201, created.text()
            ids.append(created.json()["workflow_id"])
        current, other = ids
        # 同一主体的任务账本与文档同根；直接写入确定性夹具，不触发任何外部提交。
        tasks_root = next((tmp_path / "gw-data/workflow").glob(f"*/{current}.json")).parent / "tasks"
        tasks_root.mkdir(parents=True, exist_ok=True)
        base_time = datetime(2026, 10, 1, tzinfo=timezone.utc)
        expected = []
        for i in range(130):
            job = str(uuid.uuid4())
            # 每 10 条共用一个时间戳，验证同秒任务分页次序稳定、无重复无遗漏。
            stamp = (base_time + timedelta(seconds=i // 10)).isoformat()
            registry._atomic_write(tasks_root / f"{job}.json", {"job_id": job, "task_id": job, "workflow_id": current,
                "status": "completed", "target": "local_comfy" if i % 2 else "rh_cloud", "created_at": stamp,
                "updated_at": stamp, "outputs": [], "messages": []})
            expected.append((stamp, job))
        for i in range(5):
            job = str(uuid.uuid4())
            registry._atomic_write(tasks_root / f"{job}.json", {"job_id": job, "task_id": job, "workflow_id": other,
                "status": "running" if i == 0 else "completed", "target": "rh_cloud",
                "created_at": (base_time + timedelta(days=1, seconds=i)).isoformat(), "outputs": [], "messages": []})
        probe = page.context.request.get(base + f"/api/god_workflow/tasks?workflow_id={current}&limit=1")
        if probe.status != 200 or "next_cursor" not in probe.json():
            pytest.skip("后端尚未提供任务分页（PR #35 未合入）")
        expected_order = [job for _, job in sorted(expected, reverse=True)]
        with page.expect_response(lambda r: urlsplit(r.url).path == "/api/god_workflow/tasks"):
            goto_ready(page, base + "/static/pages/workflow.html?id=" + current)
        page.wait_for_load_state("networkidle")
        assert "open" not in (page.locator("#taskModal").get_attribute("class") or ""), "其他工作流运行中任务不应弹出"
        page.wait_for_function("(id) => typeof state !== 'undefined' && state.currentWorkflow?.workflow_id === id", arg=current)
        with page.expect_response(lambda r: urlsplit(r.url).path == "/api/god_workflow/tasks"):
            page.locator("#btnViewTaskDrawer").click()
        rows = page.locator("#workflowTaskList [data-task-row]")
        rows.first.wait_for()
        assert rows.count() == 20
        _shot(page, "9-real-backend-page1.png")
        more = page.locator("#workflowTaskList [data-task-load-more]")
        clicks = 0
        while more.count():
            before = rows.count()
            with page.expect_response(lambda r: "cursor=" in r.url and r.status == 200):
                more.click()
            page.wait_for_function("(n) => document.querySelectorAll('#workflowTaskList [data-task-row]').length > n", arg=before)
            clicks += 1
        loaded = rows.evaluate_all("els => els.map(e => e.dataset.taskRow)")
        _shot(page, "10-real-backend-all-130.png")
        assert clicks == 6
        assert len(loaded) == 130
        assert len(set(loaded)) == 130, "加载更多出现重复任务"
        assert loaded == expected_order
        assert errors == []


def _seed_task(tasks_root, workflow_id, *, status, remote_task_id=None, created_at):
    import uuid
    from gw.god_workflow import registry
    job = str(uuid.uuid4())
    registry._atomic_write(tasks_root / f"{job}.json", {"job_id": job, "task_id": job, "workflow_id": workflow_id,
        "status": status, "remote_task_id": remote_task_id, "prompt_id": remote_task_id, "target": "rh_cloud",
        "created_at": created_at, "updated_at": created_at, "outputs": [], "messages": [], "remote_id_missing": not remote_task_id})
    return job


def test_real_backend_abandon_then_reload_never_pops(monkeypatch, tmp_path):
    """真实后端：无远端 ID 的结果未知任务可放弃，刷新后不弹出且显示「已放弃」；有远端 ID 的不显示放弃。
    后端尚未提供放弃接口（PR #35 未合入）时跳过。"""
    import pytest
    with settings_browser(monkeypatch, tmp_path) as (page, base, errors, _):
        created = page.context.request.post(base + "/api/god_workflow/documents?name=abandon",
            data={"1": {"class_type": "LoadImage", "inputs": {"image": "a.png"}}}, headers={"Origin": base})
        assert created.status == 201, created.text()
        wid = created.json()["workflow_id"]
        tasks_root = next((tmp_path / "gw-data/workflow").glob(f"*/{wid}.json")).parent / "tasks"
        tasks_root.mkdir(parents=True, exist_ok=True)
        no_remote = _seed_task(tasks_root, wid, status="outcome_unknown", created_at="2026-10-14T02:00:00+00:00")
        with_remote = _seed_task(tasks_root, wid, status="outcome_unknown", remote_task_id="rh-123",
                                 created_at="2026-10-14T01:00:00+00:00")
        detail = page.context.request.get(base + f"/api/god_workflow/tasks/{no_remote}").json()
        if detail.get("error_code") != "OUTCOME_UNKNOWN_NO_REMOTE_ID":
            pytest.skip("后端尚未提供 error_code / 放弃接口（PR #35 未合入）")
        page.on("dialog", lambda dialog: dialog.accept())
        url = base + "/static/pages/workflow.html?id=" + wid
        modal = page.locator("#taskModal")
        with page.expect_response(lambda r: urlsplit(r.url).path == "/api/god_workflow/tasks"):
            goto_ready(page, url)
        page.wait_for_load_state("networkidle")
        assert "open" not in (modal.get_attribute("class") or ""), "切换界面不应自动弹出结果未知任务"
        page.wait_for_function("(id) => typeof state !== 'undefined' && state.currentWorkflow?.workflow_id === id", arg=wid)
        with page.expect_response(lambda r: urlsplit(r.url).path == "/api/god_workflow/tasks"):
            page.locator("#btnViewTaskDrawer").click()
        row = page.locator(f'#workflowTaskList [data-task-row="{no_remote}"]')
        row.wait_for()
        assert "Job ID" not in page.locator("#taskModalBody").inner_text(), "打开面板不应自动展开结果未知详情"
        # 有远端 ID 的结果未知任务不提供放弃。
        page.locator(f'#workflowTaskList [data-task-row="{with_remote}"]').click()
        page.locator("#taskModalBody").get_by_text("Job ID: " + with_remote).wait_for()
        assert page.locator("#taskModalBody [data-task-abandon]").count() == 0
        row.click()
        abandon = page.locator(f'#taskModalBody [data-task-abandon="{no_remote}"]')
        abandon.wait_for()
        _shot(page, "11-abandon-button.png")
        with page.expect_response(lambda r: r.url.endswith(f"/tasks/{no_remote}/abandon") and r.status == 200):
            abandon.click()
        page.locator("#taskModalBody").get_by_text("已放弃 (Job ID: " + no_remote).wait_for()
        page.locator(f'#workflowTaskList [data-task-row="{no_remote}"] [data-task-badge="abandoned"]').wait_for()
        _shot(page, "12-abandoned.png")
        assert page.context.request.get(base + f"/api/god_workflow/tasks/{no_remote}").json()["status"] == "abandoned"
        for _ in range(2):
            with page.expect_response(lambda r: urlsplit(r.url).path == "/api/god_workflow/tasks"):
                page.reload(wait_until="networkidle")
            page.wait_for_load_state("networkidle")
            assert "open" not in (modal.get_attribute("class") or ""), "已放弃任务刷新后不应弹出"
        _shot(page, "13-reload-no-pop.png")
        with page.expect_response(lambda r: urlsplit(r.url).path == "/api/god_workflow/tasks"):
            page.locator("#btnViewTaskDrawer").click()
        page.locator(f'#workflowTaskList [data-task-row="{no_remote}"][data-status="abandoned"]').wait_for()
        assert errors == []
