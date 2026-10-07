"""真实Chrome点击后台索引、暂停/恢复、读回真实扫描结果。"""
import json
import socket
import threading
import time
import pytest
import uvicorn
from gw.api.app import create_app
from gw.asset_registry import index_jobs, repository as repo
from playwright_support import open_playwright


def test_index_job_browser_real_file_and_controls(monkeypatch, tmp_path):
    browser_api = pytest.importorskip("playwright.sync_api")
    monkeypatch.setenv("GW_AUTH_MODE", "local_account")
    monkeypatch.setenv("GW_LOCAL_AUTH_DB", str(tmp_path / "auth.sqlite3"))
    source = tmp_path / "source"
    source.mkdir()
    (source / "浏览器来源.txt").write_text("后台索引实际文件", encoding="utf-8")
    monkeypatch.setenv("GW_ALLOWED_ROOTS", str(source))
    if index_jobs._default:
        index_jobs._default.shutdown()
    index_jobs._default = None
    # 只控制执行时序，扫描/提交仍使用真实实现，不伪造任务或成功响应。
    entered, release = threading.Event(), threading.Event()
    original = index_jobs.IndexJobs._scan
    def held(self, *args):
        entered.set()
        assert release.wait(30)
        return original(self, *args)
    monkeypatch.setattr(index_jobs.IndexJobs, "_scan", held)
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    base = f"http://127.0.0.1:{listener.getsockname()[1]}"
    server = uvicorn.Server(uvicorn.Config(create_app(), log_level="error", lifespan="off"))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
    thread.start()
    evidence = {"page_errors": []}
    try:
        end = time.monotonic() + 10
        while not server.started and thread.is_alive() and time.monotonic() < end:
            time.sleep(.05)
        assert server.started
        with open_playwright(browser_api) as pw:
            browser = pw.chromium.launch(channel="chrome", headless=True)
            try:
                context = browser.new_context(viewport={"width":1440,"height":1000})
                context.route("**/*", lambda route: route.continue_() if route.request.url.startswith(base + "/") else route.abort())
                response = context.request.post(base + "/api/asset-auth/local/setup", data={"username":"index_browser", "password":"Test2026"}, headers={"Origin":base})
                assert response.status == 201, response.text()
                page = context.new_page()
                page.on("pageerror", lambda error: evidence["page_errors"].append(str(error)))
                page.goto(base + "/static/task-center.html?embedded=1&view=tasks", wait_until="networkidle")
                with page.expect_response(lambda r: r.url.endswith("/api/asset-registry/reindex") and r.request.method == "POST") as submitted:
                    page.locator("[data-start-index]").click()
                response = submitted.value
                assert response.status == 202, response.text()
                job_id = response.json()["job_id"]
                assert entered.wait(3)
                pause = page.locator('[data-run-task-action="pause"]')
                pause.wait_for()
                with page.expect_response(lambda r: r.url.endswith("/pause")) as paused:
                    pause.click()
                assert paused.value.status == 200
                page.locator('[data-run-task-action="resume"]').wait_for()
                release.set()
                assert repo.state().read()["assets"] == {}
                with page.expect_response(lambda r: r.url.endswith("/resume")) as resumed:
                    page.locator('[data-run-task-action="resume"]').click()
                assert resumed.value.status == 200
                page.wait_for_function("() => document.querySelector('#taskDetailBody')?.textContent.includes('实际执行结果')", timeout=15000)
                assert '"indexed": 1' in page.locator("#taskDetailBody").inner_text()
                page.reload(wait_until="networkidle")
                page.locator("#taskDetailBody").get_by_text("实际执行结果", exact=True).wait_for(timeout=15000)
                assert len(repo.state().read()["assets"]) == 1
                assert not evidence["page_errors"], evidence
                evidence.update(job_id=job_id, real_indexed=1, paused=True, resumed=True, reload_readback=True)
                page.screenshot(path=str(tmp_path / "index-browser.png"), full_page=True)
                (tmp_path / "index-browser.json").write_text(json.dumps(evidence,ensure_ascii=False,indent=2),encoding="utf-8")
            finally:
                browser.close()
    finally:
        release.set()
        server.should_exit = True
        thread.join(10)
        if index_jobs._default:
            index_jobs._default.shutdown()
        index_jobs._default = None
