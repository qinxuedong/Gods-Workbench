"""工作流浏览器隔离夹具，不依赖其他专项尚未提交的测试模块。"""
from contextlib import contextmanager
import socket
import threading
import time

import pytest
import uvicorn

from gw.api import routes_settings
from gw.api.app import create_app
from gw.settings.service import ProviderService
from playwright_support import open_playwright


@contextmanager
def settings_browser(monkeypatch, tmp_path):
    browser_api = pytest.importorskip("playwright.sync_api")
    monkeypatch.setenv("GW_AUTH_MODE", "local_account")
    for field in ("GW_PROVIDER_RUNTIME_JSON", "GW_CHAT_API_KEY", "GW_CHAT_BASE_URL", "GW_CLI_EXECUTION"):
        monkeypatch.delenv(field, raising=False)
    monkeypatch.setattr(routes_settings, "default_provider_service", ProviderService())
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    base = f"http://127.0.0.1:{listener.getsockname()[1]}"
    server = uvicorn.Server(uvicorn.Config(create_app(), log_level="error", lifespan="off"))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 10
        while not server.started and thread.is_alive() and time.monotonic() < deadline:
            time.sleep(.05)
        assert server.started, "隔离测试服务器未启动"
        with open_playwright(browser_api) as pw:
            browser = pw.chromium.launch(channel="chrome", headless=True)
            try:
                context = browser.new_context(viewport={"width": 1800, "height": 1000})
                context.route("**/*", lambda route: route.continue_()
                              if route.request.url.startswith(base + "/") else route.abort())
                setup = context.request.post(base + "/api/asset-auth/local/setup",
                    data={"username": "settings_fixture", "password": "Test2026"},
                    headers={"Origin": base})
                assert setup.status == 201, setup.text()
                page, errors = context.new_page(), []
                page.on("pageerror", lambda error: errors.append(str(error)))
                yield page, base, errors, []
            finally:
                browser.close()
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        listener.close()
        assert not thread.is_alive(), "隔离测试服务器未退出"
