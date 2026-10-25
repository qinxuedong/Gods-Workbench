"""QIN-374 Host 白名单、安全响应头和关闭 CORS 的回归测试。"""

import os
import socket
import threading
import time
from contextlib import contextmanager

import pytest
import uvicorn
from fastapi.testclient import TestClient
from playwright_support import open_playwright

from gw.api.app import create_app
from gw.core.config import resolve_allowed_hosts

SECURITY_HEADERS = {
    "x-frame-options": "SAMEORIGIN",
    "x-content-type-options": "nosniff",
    "referrer-policy": "strict-origin-when-cross-origin",
}


def test_allowed_hosts_include_loopback_defaults_and_configured_hosts(monkeypatch):
    monkeypatch.setenv("GW_RUNTIME_MODE", "prod")
    monkeypatch.setenv("GW_ALLOWED_HOSTS", " NAS.Example. , 192.168.0.199 ")

    assert resolve_allowed_hosts() == frozenset(
        {"localhost", "127.0.0.1", "nas.example", "192.168.0.199"}
    )


def test_test_runtime_keeps_testclient_host_compatibility(monkeypatch):
    monkeypatch.setenv("GW_RUNTIME_MODE", "test")
    monkeypatch.delenv("GW_ALLOWED_HOSTS", raising=False)

    assert {"testserver", "attacker.test"} <= resolve_allowed_hosts()


def test_unlisted_host_is_rejected_with_security_headers(monkeypatch):
    monkeypatch.setenv("GW_RUNTIME_MODE", "prod")
    monkeypatch.setenv("GW_AUTH_MODE", "local_account")
    monkeypatch.setenv("GW_HOST", "127.0.0.1")
    monkeypatch.setenv("GW_ALLOWED_HOSTS", "nas.example")

    response = TestClient(create_app()).get(
        "/healthz", headers={"Host": "evil.example"}
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "Invalid host header"
    assert_security_headers(response)


@pytest.mark.parametrize("host", ["localhost", "127.0.0.1", "nas.example"])
def test_allowed_host_reaches_health_endpoint(monkeypatch, host):
    monkeypatch.setenv("GW_RUNTIME_MODE", "prod")
    monkeypatch.setenv("GW_AUTH_MODE", "local_account")
    monkeypatch.setenv("GW_HOST", "127.0.0.1")
    monkeypatch.setenv("GW_ALLOWED_HOSTS", "nas.example")

    response = TestClient(create_app()).get(
        "/healthz", headers={"Host": f"{host}:8080"}
    )

    assert response.status_code == 200
    assert_security_headers(response)


@pytest.mark.parametrize("path", ["/healthz", "/missing-qin-374"])
def test_all_responses_include_security_headers(client, path):
    response = client.get(path)

    assert_security_headers(response)


def test_preflight_never_advertises_cross_origin_access(client):
    response = client.options(
        "/api/projects",
        headers={
            "Origin": "https://attacker.example",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type",
        },
    )

    assert "access-control-allow-origin" not in response.headers
    assert_security_headers(response)


def assert_security_headers(response):
    for header, expected in SECURITY_HEADERS.items():
        assert response.headers.get(header) == expected
    script_src = "'self' 'unsafe-inline'"
    if (
        os.environ.get("GW_RUNTIME_MODE", "").strip().lower() == "test"
        and os.environ.get("GW_TEST_ALLOW_CSP_UNSAFE_EVAL") == "1"
    ):
        script_src += " 'unsafe-eval'"
    assert response.headers.get("content-security-policy") == (
        "default-src 'self'; base-uri 'self'; object-src 'none'; "
        "frame-ancestors 'self'; form-action 'self'; "
        f"script-src {script_src}; style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data: blob:; font-src 'self' data:; "
        "media-src 'self' data: blob:; connect-src 'self'"
    )


def test_unsafe_eval_is_opt_in_for_tests_and_never_enabled_in_production(monkeypatch):
    monkeypatch.setenv("GW_RUNTIME_MODE", "test")
    monkeypatch.delenv("GW_TEST_ALLOW_CSP_UNSAFE_EVAL", raising=False)
    client = TestClient(create_app())

    strict_test_policy = client.get("/healthz").headers["content-security-policy"]
    assert "'unsafe-eval'" not in strict_test_policy
    assert "frame-ancestors 'self'" in strict_test_policy

    monkeypatch.setenv("GW_TEST_ALLOW_CSP_UNSAFE_EVAL", "1")
    test_policy = client.get("/healthz").headers["content-security-policy"]
    assert "'unsafe-eval'" in test_policy

    monkeypatch.setenv("GW_RUNTIME_MODE", "prod")
    production_policy = client.get("/healthz").headers["content-security-policy"]
    assert "'unsafe-eval'" not in production_policy


@contextmanager
def security_headers_browser(monkeypatch):
    browser_api = pytest.importorskip("playwright.sync_api")
    monkeypatch.setenv("GW_RUNTIME_MODE", "test")
    monkeypatch.setenv("GW_TEST_ALLOW_CSP_UNSAFE_EVAL", "1")
    monkeypatch.setenv("GW_AUTH_MODE", "local_account")
    monkeypatch.delenv("GW_ALLOWED_HOSTS", raising=False)
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    base = f"http://127.0.0.1:{listener.getsockname()[1]}"
    server = uvicorn.Server(
        uvicorn.Config(create_app(), log_level="error", lifespan="off")
    )
    thread = threading.Thread(
        target=server.run, kwargs={"sockets": [listener]}, daemon=True
    )
    thread.start()
    try:
        deadline = time.monotonic() + 10
        while not server.started and thread.is_alive() and time.monotonic() < deadline:
            time.sleep(0.05)
        assert server.started, "隔离测试服务器未启动"
        with open_playwright(browser_api) as pw:
            browser = pw.chromium.launch(channel="chrome", headless=True)
            try:
                context = browser.new_context()
                setup = context.request.post(
                    base + "/api/asset-auth/local/setup",
                    data={"username": "qin374_browser", "password": "Test2026"},
                    headers={"Origin": base},
                )
                assert setup.status == 201, setup.text()
                page = context.new_page()
                console_errors = []
                page_errors = []
                failed_responses = []
                requests = []
                page.on(
                    "console",
                    lambda message: console_errors.append(
                        f"{message.text} ({message.location})"
                    )
                    if message.type == "error"
                    else None,
                )
                page.on("pageerror", lambda error: page_errors.append(str(error)))
                page.on(
                    "response",
                    lambda response: failed_responses.append(
                        f"{response.status} {response.url}"
                    )
                    if response.status >= 400
                    else None,
                )
                page.on("request", lambda request: requests.append(request.url))
                yield page, base, console_errors, page_errors, failed_responses, requests
            finally:
                browser.close()
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        listener.close()
        assert not thread.is_alive(), "隔离测试服务器未退出"


def test_production_frontend_runs_under_csp_without_console_errors(monkeypatch):
    with security_headers_browser(monkeypatch) as (
        page,
        base,
        console_errors,
        page_errors,
        failed_responses,
        requests,
    ):
        page.route("**/favicon.ico", lambda route: route.fulfill(status=204, body=""))
        response = page.goto(
            base + "/static/pages/production.html", wait_until="networkidle"
        )

        assert response is not None and response.status == 200
        assert page.locator("body").get_attribute("class") is not None
        assert page.locator("script[src*='tailwindcss-cdn.js']").count() == 1
        assert console_errors == [], {
            "console": console_errors,
            "responses": failed_responses,
            "requests": requests,
        }
        assert page_errors == [], page_errors


def test_same_origin_settings_iframe_remains_available_under_csp(monkeypatch):
    with security_headers_browser(monkeypatch) as (
        page,
        base,
        console_errors,
        page_errors,
        failed_responses,
        requests,
    ):
        page.route("**/favicon.ico", lambda route: route.fulfill(status=204, body=""))
        response = page.goto(
            base + "/static/pages/settings.html?section=api-settings",
            wait_until="networkidle",
        )

        assert response is not None and response.status == 200
        frame = page.frame_locator("#iframeApiSettings")
        frame.locator("#providerList .empty").wait_for(state="attached")
        assert console_errors == [], {
            "console": console_errors,
            "responses": failed_responses,
            "requests": requests,
        }
        assert page_errors == [], page_errors
