"""真实Chrome显式发送到回环Provider，核对吞吐归属与无usage降级。"""
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import socket
import threading
import time

import pytest
import uvicorn
from gw.api.app import create_app
from playwright_support import open_playwright


def test_provider_metrics_browser_real_loopback(monkeypatch, tmp_path):
    browser_api = pytest.importorskip("playwright.sync_api")
    calls = []

    class Upstream(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            calls.append({"path": self.path, "model": body.get("model")})
            payload = {"choices": [{"message": {"content": "回环模型已响应"}}]}
            if body.get("model") == "measured-model":
                payload["usage"] = {"completion_tokens": 24}
            data = json.dumps(payload).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    upstream = ThreadingHTTPServer(("127.0.0.1", 0), Upstream)
    upstream_thread = threading.Thread(target=upstream.serve_forever, daemon=True)
    upstream_thread.start()
    monkeypatch.setenv("GW_AUTH_MODE", "local_account")
    monkeypatch.setenv("GW_LOCAL_AUTH_DB", str(tmp_path / "auth.sqlite3"))
    monkeypatch.setenv("GW_TEST_METRICS_SECRET", "isolated-test-not-a-credential")
    monkeypatch.setenv("GW_PROVIDER_RUNTIME_JSON", json.dumps({
        "browser-provider": {"base_url": f"http://127.0.0.1:{upstream.server_port}/v1",
            "api_key_env": "GW_TEST_METRICS_SECRET", "models": ["measured-model", "missing-usage-model"],
            "default_model": "measured-model"}}))
    monkeypatch.delenv("GW_CHAT_BASE_URL", raising=False)
    monkeypatch.delenv("GW_CHAT_API_KEY", raising=False)
    monkeypatch.delenv("GW_CHAT_MODEL", raising=False)
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    base = f"http://127.0.0.1:{listener.getsockname()[1]}"
    server = uvicorn.Server(uvicorn.Config(create_app(), log_level="error", lifespan="off"))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
    thread.start()
    evidence = {"page_errors": [], "scope": "受控回环HTTP协议，不是商业Provider现场验收"}
    try:
        deadline = time.monotonic() + 10
        while not server.started and thread.is_alive() and time.monotonic() < deadline:
            time.sleep(.05)
        assert server.started
        with open_playwright(browser_api) as pw:
            browser = pw.chromium.launch(channel="chrome", headless=True)
            try:
                context = browser.new_context(viewport={"width": 1600, "height": 1000})
                context.route("**/*", lambda route: route.continue_() if route.request.url.startswith(base + "/") else route.abort())
                setup = context.request.post(base + "/api/asset-auth/local/setup",
                    data={"username": "metrics_browser", "password": "Test2026"}, headers={"Origin": base})
                assert setup.status == 201, setup.text()
                page = context.new_page()
                page.on("pageerror", lambda error: evidence["page_errors"].append(str(error)))
                page.goto(base + "/static/pages/agents.html", wait_until="networkidle")
                page.wait_for_function("() => document.querySelector('#agentProviderSelect')?.value === 'browser-provider'")
                assert calls == [], "加载页面不得自动调用模型"
                assert "可能产生费用" in page.locator("#agentBillingNotice").inner_text()
                assert "尚未测量" in page.locator("#agentThroughputReadout").inner_text()
                page.once("dialog", lambda dialog: dialog.dismiss())
                page.locator("#agentPromptInput").fill("取消发送")
                page.locator("#agentSendButton").click()
                assert calls == [], "取消确认不得发出模型请求"
                page.once("dialog", lambda dialog: dialog.accept())
                with page.expect_response(lambda r: r.url.endswith("/api/chat/agent") and r.request.method == "POST") as pending:
                    page.locator("#agentSendButton").click()
                response = pending.value
                assert response.status == 200, response.text()
                metrics = response.json()["metrics"]
                assert metrics["output_tokens"] == 24
                assert metrics["provider_id"] == "browser-provider" and metrics["model"] == "measured-model"
                assert metrics["tokens_per_second"] == pytest.approx(24 / metrics["elapsed_seconds"], rel=1e-4)
                page.wait_for_function("() => document.querySelector('#agentThroughputReadout')?.textContent.includes('tokens/s')")
                title = page.locator("#agentThroughputReadout").get_attribute("title")
                assert "browser-provider" in title and "measured-model" in title and "不是解码速度" in title
                assert len(calls) == 1 and calls[0]["path"] == "/v1/chat/completions"
                page.screenshot(path=str(tmp_path / "provider-metrics-browser.png"), full_page=True)
                page.locator("#agentModelSelect").select_option("missing-usage-model")
                assert "需重新测量" in page.locator("#agentThroughputReadout").inner_text()
                assert len(calls) == 1
                page.locator("#agentPromptInput").fill("无usage响应")
                page.once("dialog", lambda dialog: dialog.accept())
                with page.expect_response(lambda r: r.url.endswith("/api/chat/agent") and r.request.method == "POST") as pending:
                    page.locator("#agentSendButton").click()
                missing = pending.value.json()["metrics"]
                assert missing["tokens_per_second"] is None and missing["output_tokens"] is None
                page.wait_for_function("() => document.querySelector('#agentThroughputReadout')?.textContent.includes('不可用')")
                assert "未使用文本长度估算" in page.locator("#agentThroughputReadout").get_attribute("title")
                page.reload(wait_until="networkidle")
                assert len(calls) == 2, "刷新不得重发计费请求"
                assert not evidence["page_errors"], evidence
                evidence.update(metrics=metrics, no_usage=missing, upstream_calls=calls,
                    no_request_on_load=True, cancelled_confirmation_no_request=True, reload_no_retry=True)
                (tmp_path / "provider-metrics-browser.json").write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
            finally:
                browser.close()
    finally:
        server.should_exit = True
        thread.join(10)
        listener.close()
        upstream.shutdown()
        upstream.server_close()
        upstream_thread.join(5)
