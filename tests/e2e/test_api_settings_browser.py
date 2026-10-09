"""API设置页浏览器回归：隔离本地账户，不访问真实Provider或个人浏览器。"""

from contextlib import contextmanager
import json
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
def settings_browser(monkeypatch, tmp_path, *, populated=False):
    browser_api = pytest.importorskip("playwright.sync_api")
    monkeypatch.setenv("GW_AUTH_MODE", "local_account")
    monkeypatch.delenv("GW_PROVIDER_RUNTIME_JSON", raising=False)
    monkeypatch.delenv("GW_CHAT_API_KEY", raising=False)
    monkeypatch.delenv("GW_CHAT_BASE_URL", raising=False)
    monkeypatch.delenv("GW_CLI_EXECUTION", raising=False)
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
                if populated:
                    response = context.request.put(base + "/api/providers?expected_version=1",
                        data=[{"id": "browser-fixture", "name": "浏览器测试平台",
                               "base_url": "https://example.invalid/v1", "protocol": "openai",
                               "image_models": [], "chat_models": [], "video_models": []}],
                        headers={"Origin": base})
                    assert response.status == 200, response.text()
                page = context.new_page()
                errors = []
                writes = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.on("request", lambda request: writes.append(request.method)
                        if request.url.split("?", 1)[0].endswith("/api/providers") and request.method != "GET" else None)
                yield page, base, errors, writes
            finally:
                browser.close()
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        listener.close()
        assert not thread.is_alive(), "隔离测试服务器未退出"


def open_settings(page, base, entry):
    page.goto(base + entry, wait_until="networkidle")
    if entry.startswith("/static/pages/"):
        frame = page.frame_locator("#iframeApiSettings")
    else:
        frame = page
    frame.locator("#providerList .empty").wait_for(state="attached")
    return frame


ENTRIES = ["/static/api-settings.html?embedded=1", "/static/pages/settings.html?section=api-settings"]


@pytest.mark.parametrize('suffix', ('', '/v1', '/v1/'))
def test_openai_base_address_completes_once_before_save(suffix, monkeypatch, tmp_path):
    with settings_browser(monkeypatch, tmp_path, populated=True) as (page, base, errors, writes):
        page.goto(base + ENTRIES[0], wait_until='networkidle')
        page.locator('#providerList .provider-card').first.wait_for()
        page.locator('#baseInput').fill('http://192.168.0.199:3100'+suffix)
        page.locator('#keyInput').fill('synthetic-lan-browser-key')
        assert page.locator('#baseInput').input_value() == 'http://192.168.0.199:3100/v1'
        with page.expect_response(lambda response: response.request.method == 'PUT' and '/api/providers' in response.url) as saved:
            page.locator('.api-page-save-btn').click()
        assert saved.value.status == 200
        assert saved.value.json()['providers'][0]['base_url'] == 'http://192.168.0.199:3100/v1'
        page.reload(wait_until='networkidle')
        page.locator('#keyHint').filter(has_text='providers.json').wait_for()
        assert page.locator('#keyInput').input_value() == ''
        assert errors == [] and writes == ['PUT']


@pytest.mark.parametrize("entry", ENTRIES)
def test_empty_settings_shows_aligned_cards_without_recommendations(entry, monkeypatch, tmp_path):
    with settings_browser(monkeypatch, tmp_path) as (page, base, errors, writes):
        frame = open_settings(page, base, entry)
        assert frame.locator("#settingsContent").is_visible()
        assert frame.locator("#providerEmptyState").is_visible()
        assert not frame.locator("#providerEditor").is_visible()
        assert "尚未添加平台" in frame.locator("#providerList").inner_text()
        assert frame.locator("#openRecommendApiBtn, #recommendContent, #recommendApiOverlay, #providerOnboardingCard").count() == 0
        assert frame.locator("html").evaluate("() => typeof openRecommendApi") == "undefined"
        assert frame.locator("html").evaluate("() => typeof saveRecommendedApi") == "undefined"
        for width in (1800, 1280, 960):
            page.set_viewport_size({"width": width, "height": 1000})
            content = frame.locator(".layout > .content").bounding_box()
            sidebar = frame.locator(".layout > .sidebar").bounding_box()
            if frame.locator("html").evaluate("() => innerWidth") > 760:
                assert abs(content["y"] - sidebar["y"]) <= 1
                assert abs(content["height"] - sidebar["height"]) <= 1
                assert content["x"] > sidebar["x"] + sidebar["width"]
            assert frame.locator(".layout > .content").evaluate("el => getComputedStyle(el).borderRadius") == frame.locator(".layout > .sidebar").evaluate("el => getComputedStyle(el).borderRadius")
        assert writes == [] and errors == []


@pytest.mark.parametrize("entry", ENTRIES)
def test_existing_platform_opens_parameter_card_without_recommendations(entry, monkeypatch, tmp_path):
    with settings_browser(monkeypatch, tmp_path, populated=True) as (page, base, errors, writes):
        page.goto(base + entry, wait_until="networkidle")
        frame = page.frame_locator("#iframeApiSettings") if entry.startswith("/static/pages/") else page
        frame.locator("#providerList .provider-card").first.wait_for()
        assert frame.locator("#settingsContent").is_visible()
        assert frame.locator("#providerEditor").is_visible()
        assert not frame.locator("#providerEmptyState").is_visible()
        assert frame.locator("#openRecommendApiBtn, #recommendContent, #recommendApiOverlay").count() == 0
        frame.locator("#providerList .provider-card").first.click()
        assert frame.locator("#imageModelList .empty").is_visible()
        assert writes == [] and errors == []


def test_embedded_save_failure_is_visible_and_preserves_input(monkeypatch, tmp_path):
    with settings_browser(monkeypatch, tmp_path, populated=True) as (page, base, errors, writes):
        page.goto(base + ENTRIES[1], wait_until="networkidle")
        frame = page.frame_locator("#iframeApiSettings")
        frame.locator("#providerList .provider-card").first.click()
        frame.locator("#nameInput").fill("失败后应保留的名称")

        def fail_write(route):
            if route.request.method == "PUT":
                route.fulfill(status=503, content_type="application/json",
                    body=json.dumps({"detail": {"code": "FIXTURE_FAILURE", "message": "受控保存失败"}}))
            else:
                route.continue_()

        page.route("**/api/providers*", fail_write)
        with page.expect_response(lambda response: response.request.method == "PUT"
                                  and "/api/providers" in response.url):
            frame.locator(".api-page-save-btn").click()
        frame.locator("#status").filter(has_text="服务暂时不可用").wait_for(state="attached")
        assert frame.locator("#status").is_visible(), "嵌入态必须显示保存失败"
        assert frame.locator("#status").get_attribute("role") == "status"
        assert frame.locator("#nameInput").input_value() == "失败后应保留的名称"
        assert writes == ["PUT"]
        assert errors == []


def test_empty_settings_add_platform_initializes_model_states(monkeypatch, tmp_path):
    with settings_browser(monkeypatch, tmp_path) as (page, base, errors, writes):
        frame = open_settings(page, base, ENTRIES[0])
        frame.get_by_role("button", name="新增平台", exact=True).click()
        assert frame.locator("#settingsContent").is_visible()
        assert not frame.locator("#providerEmptyState").is_visible()
        for kind in ("image", "chat", "video"):
            assert frame.locator(f"#{kind}ModelList .empty").is_visible()
        assert writes == [], "新增平台草稿不得自动保存"
        assert errors == []


def test_browser_saves_key_with_version_then_reloads_without_echo(monkeypatch, tmp_path):
    with settings_browser(monkeypatch, tmp_path, populated=True) as (page, base, errors, writes):
        page.goto(base + ENTRIES[0], wait_until="networkidle")
        frame = page
        frame.locator("#providerList .provider-card").first.wait_for()
        frame.locator("#keyInput").fill("synthetic-browser-secret")
        frame.locator("#nameInput").fill("已持久化的平台")
        with page.expect_response(lambda response: response.request.method == "PUT"
                                  and "/api/providers" in response.url) as saved:
            frame.locator(".api-page-save-btn").click()
        assert saved.value.status == 200
        assert "expected_version=2" in saved.value.url
        assert "synthetic-browser-secret" not in saved.value.text()
        frame.locator("#keyHint").filter(has_text="已加密保存").wait_for()
        assert frame.locator("#keyInput").input_value() == ""
        assert "尚未验证" in frame.locator("#status").inner_text()
        page.reload(wait_until="networkidle")
        assert frame.locator("#nameInput").input_value() == "已持久化的平台"
        assert frame.locator("#keyInput").input_value() == ""
        assert "已加密保存" in frame.locator("#keyHint").inner_text()
        assert writes == ["PUT"] and errors == []


def test_browser_conflict_preserves_name_and_unsaved_secret(monkeypatch, tmp_path):
    with settings_browser(monkeypatch, tmp_path, populated=True) as (page, base, errors, writes):
        page.goto(base + ENTRIES[0], wait_until="networkidle")
        frame = page
        frame.locator("#providerList .provider-card").first.wait_for()
        frame.locator("#nameInput").fill("待解决的本地编辑")
        frame.locator("#keyInput").fill("synthetic-unsaved-secret")
        response = page.context.request.put(base + "/api/providers?expected_version=2",
            data=[{"id": "browser-fixture", "name": "另一窗口的编辑"}], headers={"Origin": base})
        assert response.status == 200
        with page.expect_response(lambda response: response.request.method == "PUT"
                                  and "/api/providers" in response.url) as rejected:
            frame.locator(".api-page-save-btn").click()
        assert rejected.value.status == 409
        frame.locator("#status").filter(has_text="当前输入已保留").wait_for()
        assert frame.locator("#nameInput").input_value() == "待解决的本地编辑"
        assert frame.locator("#keyInput").input_value() == "synthetic-unsaved-secret"
        assert not frame.locator(".api-page-save-btn").is_disabled()
        assert ProviderService().get_snapshot().providers[0]["name"] == "另一窗口的编辑"
        assert ProviderService().credential("browser-fixture") == ""
        assert writes == ["PUT"] and errors == []
