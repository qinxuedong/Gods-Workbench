"""真实浏览器验证头像入口；无 Chrome/Playwright 的环境明确跳过。"""

from urllib.parse import urlsplit

import pytest
from fastapi.testclient import TestClient
from playwright_support import open_playwright

from gw.api.app import app


def test_avatar_opens_auth_modal_and_discloses_local_mode(monkeypatch):
    browser_api = pytest.importorskip("playwright.sync_api")
    monkeypatch.setenv("GW_AUTH_MODE", "local")
    with TestClient(app) as client, open_playwright(browser_api) as pw:
        try:
            browser = pw.chromium.launch(channel="chrome", headless=True)
        except browser_api.Error as exc:
            pytest.skip(f"Chrome 不可启动：{exc}")
        try:
            page = browser.new_page()

            def serve(route):
                parsed = urlsplit(route.request.url)
                if parsed.hostname != "testserver":
                    route.abort()
                    return
                path = parsed.path + ("?" + parsed.query if parsed.query else "")
                response = client.request(route.request.method, path, content=route.request.post_data)
                route.fulfill(status=response.status_code, body=response.content,
                              headers={"content-type": response.headers.get("content-type", "text/plain")})

            # 以当前应用响应加载真实页面，不访问外部 IdP 或用户生产服务。
            page.route("**/*", serve)
            page.goto("http://testserver/static/pages/projects.html")
            page.wait_for_function("window.HardwareDeck && HardwareDeck.authState.auth_mode === 'local'")
            for activation in ("click", "Enter", " "):
                avatar = page.locator(".hw-avatar-keycap").first
                if activation == "click":
                    avatar.click()
                else:
                    avatar.focus()
                    page.keyboard.press("Space" if activation == " " else activation)
                page.wait_for_selector("#accountModal.open")
                assert page.locator("#hwLoginSubmitBtn").is_disabled()
                assert page.locator('[data-hw-auth-login="disabled"]').is_visible()
                page.evaluate("HardwareDeck.closeModal('accountModal')")

            # 替换头像后仍需响应，覆盖动态 shell 重建 DOM 的场景。
            page.evaluate("const avatar = document.querySelector('.hw-avatar-keycap'); avatar.replaceWith(avatar.cloneNode(true));")
            page.locator(".hw-avatar-keycap").first.click()
            page.wait_for_selector("#accountModal.open")
        finally:
            browser.close()
