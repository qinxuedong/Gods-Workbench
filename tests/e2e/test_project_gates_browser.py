"""真实浏览器验证立项表单创建阶段门并读回服务端真实状态与版本。"""
import socket
import threading
import time
import pytest
import uvicorn

from gw.api.app import create_app
from gw.api import routes_asset_registry, routes_projects
from gw.projects_hub import service as projects_module
from gw.projects_hub.service import ProjectsService
from gw.video_tasks import service as video_module
from playwright_support import open_playwright

def test_browser_creates_and_reads_back_project_gates(monkeypatch, tmp_path):
    browser_api = pytest.importorskip("playwright.sync_api")
    monkeypatch.setenv("GW_AUTH_MODE", "local_account")
    monkeypatch.setenv("GW_LOCAL_AUTH_DB", str(tmp_path / "auth.sqlite3"))
    monkeypatch.setenv("GW_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("GW_VIDEO_DATA_DIR", str(tmp_path / "video"))
    monkeypatch.setenv("GW_ALLOWED_ROOTS", "")
    monkeypatch.setenv("GW_CLI_EXECUTION", "0")

    project_service = ProjectsService(seed_golden_fixture=False, persistent=True)
    for module in (projects_module, routes_projects, routes_asset_registry, video_module):
        monkeypatch.setattr(module, "default_projects_service", project_service)

    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    base = f"http://127.0.0.1:{listener.getsockname()[1]}"
    server = uvicorn.Server(uvicorn.Config(create_app(), log_level="error", lifespan="off"))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 10
        while not server.started and thread.is_alive() and time.monotonic() < deadline:
            time.sleep(0.05)
        assert server.started
        with open_playwright(browser_api) as pw:
            try:
                browser = pw.chromium.launch(channel="chrome", headless=True)
            except browser_api.Error as exc:
                pytest.skip(f"Chrome unavailable: {type(exc).__name__}")
            try:
                context = browser.new_context(viewport={"width": 1440, "height": 1000})
                context.route("**/*", lambda route: route.continue_() if route.request.url.startswith(base + "/") else route.abort())
                page = context.new_page()
                page_errors = []
                responses = []
                page.on("pageerror", lambda error: page_errors.append(str(error)))
                page.on("response", lambda response: responses.append(response))
                page.goto(base + "/static/pages/projects.html", wait_until="domcontentloaded")
                page.wait_for_selector("#localUsername")
                page.fill("#localUsername", "stage_gate_browser_owner")
                page.fill("#localPassword", "StageGate2026")
                page.fill("#localPasswordConfirm", "StageGate2026")
                with page.expect_navigation():
                    page.click("#localLoginSubmit")
                page.wait_for_function("window.HardwareDeck?.authState?.authenticated === true")

                page.locator('button[onclick*="newProjectModal"]').first.click()
                page.fill("#newProjectNameInput", "Browser stage gate project")
                page.select_option("#newProjectTypeSelect", "series")
                page.fill("#newProjectDescInput", "Isolated real browser contract")
                page.fill("#newProjectGatesInput", "script|Script review\nlayout|Layout review")
                page.locator("#newProjectModal button[type=submit]").click()
                page.wait_for_function("document.querySelector('#workbenchToastContainer')?.innerText.includes('v1')", timeout=15000)

                post = next(item for item in responses if item.request.method == "POST" and item.url.endswith("/api/asset-registry/projects"))
                gate_readback = next(item for item in responses if item.request.method == "GET" and item.url.endswith("/gates"))
                assert post.status == 201
                assert gate_readback.status == 200
                gates = gate_readback.json()["gates"]
                assert [(gate["code"], gate["name"], gate["state"], gate["version"]) for gate in gates] == [
                    ("script", "Script review", "pending", 1),
                    ("layout", "Layout review", "pending", 1),
                ]
                toast = page.locator("#workbenchToastContainer").inner_text()
                assert "Script review" in toast and "pending" not in toast and "v1" in toast
                assert page.evaluate("sessionStorage.getItem('gw.pending-project-create')") is None
                assert page.locator("#newProjectGatesInput").first.input_value() == ""
                assert not page_errors, page_errors
                context.close()
            finally:
                browser.close()
    finally:
        server.should_exit = True
        thread.join(timeout=5)
        if thread.is_alive():
            raise AssertionError("test server failed to stop")
