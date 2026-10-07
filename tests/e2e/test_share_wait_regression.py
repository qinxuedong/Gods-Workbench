"""用本地 HTTP 验证 Playwright 条件等待会继续派发 route 回调。"""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import threading

import pytest

from playwright_support import open_playwright


class _GateServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, handler, log_path):
        super().__init__(address, handler)
        self.log_path = Path(log_path)
        self.started = threading.Event()
        self.release = threading.Event()
        self.finished = threading.Event()
        self.log_lock = threading.Lock()
        self.requests = []

    def record(self, text):
        with self.log_lock:
            with self.log_path.open("a", encoding="utf-8") as log:
                log.write(text + "\n")


class _GateHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_GET(self):
        self.server.record(f"request {self.path}")
        if self.path == "/":
            body = b"""<!doctype html><meta charset=utf-8><title>local route gate</title>
            <script>
            window.scheduleProbe = () => {
                setTimeout(() => fetch('/hold').then(r => r.text()).then(t => window.probeText = t), 250);
                return 'scheduled';
            };
            </script><body>local route gate</body>"""
            self.send_response(200)
        elif self.path == "/hold":
            self.server.requests.append(self.path)
            self.server.started.set()
            released = self.server.release.wait(5)
            self.server.finished.set()
            body = b"released" if released else b"gate timeout"
            self.send_response(200 if released else 504)
        else:
            body = b"not found"
            self.send_response(404)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        self.server.record("http " + (format % args))


def test_wait_for_function_pumps_local_http_route(tmp_path):
    """同步 API 条件等待继续派发 route，服务端收到并完成真实 HTTP 请求。"""
    browser_api = pytest.importorskip("playwright.sync_api")
    server = _GateServer(("127.0.0.1", 0), _GateHandler, tmp_path / "server.log")
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    route_urls = []
    route_lock = threading.Lock()
    browser = context = page = None

    def route_local(route):
        url = route.request.url
        if url.startswith(base + "/"):
            with route_lock:
                route_urls.append(url)
            server.record(f"route continue {url}")
            route.continue_()
        else:
            server.record(f"route abort external {url.split('/', 3)[2]}")
            route.abort()

    try:
        with open_playwright(browser_api) as playwright:
            browser = playwright.chromium.launch(channel="chrome", headless=True)
            try:
                context = browser.new_context()
                context.route("**/*", route_local)
                page = context.new_page()
                context.expose_function("shareGateStarted", lambda: server.started.is_set())
                page.goto(base + "/", wait_until="load")
                page.evaluate("""() => {
                    window.__shareGateStarted = false;
                    const poll = async () => {
                        window.__shareGateStarted = await window.shareGateStarted();
                        if (!window.__shareGateStarted) requestAnimationFrame(poll);
                    };
                    requestAnimationFrame(poll);
                }""")
                assert page.evaluate("() => window.scheduleProbe()") == "scheduled"

                # 同步谓词等待暴露绑定回写的状态，并持续驱动 Playwright 事件派发。
                page.wait_for_function("() => window.__shareGateStarted === true", timeout=5_000)
                with page.expect_response(lambda response: response.url == base + "/hold") as held_response:
                    server.release.set()
                assert held_response.value.status == 200
                page.wait_for_function("() => window.probeText === 'released'", timeout=5_000)
                assert server.finished.is_set()
                assert server.requests == ["/hold"]
                with route_lock:
                    assert any(url.endswith("/hold") for url in route_urls)
            finally:
                server.release.set()
                if context is not None:
                    context.close()
                browser.close()
                browser = None
    finally:
        server.release.set()
        server.shutdown()
        server.server_close()
        server_thread.join(timeout=5)