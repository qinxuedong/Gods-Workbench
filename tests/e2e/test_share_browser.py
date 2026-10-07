"""匿名访客真实浏览器验收：媒体、评论、审批、下载及刷新读回。"""
import asyncio
import importlib.util
import json
import socket
import threading
import time
from pathlib import Path

import pytest
import uvicorn
from fastapi.testclient import TestClient
from gw.api.app import create_app
from playwright_support import open_playwright

ROOT = Path(__file__).resolve().parents[2]


def test_share_guest_media_comment_approval_download_and_reload(monkeypatch, tmp_path):
    browser_api = pytest.importorskip("playwright.sync_api")
    monkeypatch.setenv("GW_AUTH_MODE", "local_account")
    monkeypatch.setenv("GW_LOCAL_AUTH_DB", str(tmp_path / "auth.sqlite3"))
    spec = importlib.util.spec_from_file_location("share_fixtures", ROOT / "tests/contracts/assets/test_phase12_share_closure.py")
    helpers = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helpers)
    with TestClient(create_app(), base_url="http://localhost", client=("127.0.0.1", 50000), headers={"Origin": "http://localhost"}) as client:
        setup = client.post("/api/asset-auth/local/setup", json={"username": "share_owner", "password": "Test2026"})
        assert setup.status_code == 201
        asset_id = helpers._asset(client)
        payload = helpers._png_1x1()
        helpers._attach_file(client, asset_id, "tiny.png", payload, tmp_path / "media", monkeypatch)
        token, _ = helpers._share(client, [asset_id], password="visitor-test", can_download=True)
        landing = client.get("/share/" + token)
        assert landing.status_code == 200
        assert landing.headers["referrer-policy"] == "no-referrer"
        assert landing.headers["cache-control"] == "private, no-store"
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    base = f"http://127.0.0.1:{listener.getsockname()[1]}"
    server = uvicorn.Server(uvicorn.Config(create_app(), log_level="error", lifespan="off"))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
    thread.start()
    evidence = {"page_errors": [], "scope": "匿名访客；真实HTTP与本地图片"}
    try:
        deadline = time.monotonic() + 10
        while not server.started and thread.is_alive() and time.monotonic() < deadline:
            time.sleep(0.05)
        assert server.started
        with open_playwright(browser_api) as pw:
            try:
                browser = pw.chromium.launch(channel="chrome", headless=True)
            except browser_api.Error as exc:
                pytest.skip(f"Chrome不可启动：{type(exc).__name__}")
            try:
                context = browser.new_context(viewport={"width": 1440, "height": 1000}, accept_downloads=True)
                context.route("**/*", lambda route: route.continue_() if route.request.url.startswith(base + "/") else route.abort())
                page = context.new_page()
                page.on("pageerror", lambda error: evidence["page_errors"].append(type(error).__name__))
                page.on("dialog", lambda dialog: dialog.accept("请保留细节"))
                page.goto(base + "/share/" + token, wait_until="networkidle")
                page.locator('#shareAccessForm input[name="password"]').fill("visitor-test")
                page.locator('#shareAccessForm button[type="submit"]').click()
                page.wait_for_function("() => document.querySelector('#shareMedia')?.naturalWidth === 1")
                assert page.locator("#shareMedia").get_attribute("src").startswith("blob:")
                text = '真实访客意见 <img src=x onerror="window.__share_xss=1">'
                page.locator('#shareCommentForm textarea[name="body"]').fill(text)
                page.locator('#shareCommentForm input[name="guest_name"]').fill("浏览器访客")
                with page.expect_response(lambda r: r.url.endswith("/comments") and r.request.method == "POST") as commented:
                    page.locator('#shareCommentForm button[type="submit"]').click()
                assert commented.value.status == 201
                page.get_by_text(text, exact=True).wait_for()
                with page.expect_response(lambda r: r.url.endswith("/approvals") and r.request.method == "PUT") as approved:
                    page.locator('[data-approval="changes_requested"]').click()
                assert approved.value.status == 200
                page.get_by_text("请保留细节", exact=True).wait_for()
                with page.expect_download() as download:
                    page.locator("[data-download]").click()
                downloaded = tmp_path / "guest-download.png"
                download.value.save_as(downloaded)
                assert downloaded.read_bytes() == payload
                page.reload(wait_until="networkidle")
                page.locator('#shareAccessForm input[name="password"]').fill("visitor-test")
                page.locator('#shareAccessForm button[type="submit"]').click()
                page.get_by_text(text, exact=True).wait_for()
                page.get_by_text("请保留细节", exact=True).wait_for()
                page.wait_for_function("() => document.querySelector('#shareMedia')?.naturalWidth === 1")
                assert page.evaluate("window.__share_xss === undefined")
                assert context.cookies() == [], "匿名分享不应创建私有账户会话"
                assert not evidence["page_errors"]
                evidence.update(blob_preview=True, download_bytes_equal=True, comment_reload=True,
                                approval_reload=True, text_only=True, anonymous=True)
                page.screenshot(path=str(tmp_path / "share-browser.png"), full_page=True)
                context.close()
            finally:
                browser.close()
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        listener.close()
        (tmp_path / "share-browser-evidence.json").write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")


class _ShareResponseGate:
    """在真实 ASGI 响应发送前暂停一个指定 HTTP 响应体。"""

    def __init__(self, app):
        self.app = app
        self._lock = threading.Lock()
        self._target_path = None
        self.started = threading.Event()
        self.release = threading.Event()
        self.completed = threading.Event()
        self.request_counts = {}
        self.delayed_statuses = []

    def delay_next(self, path):
        with self._lock:
            self._target_path = path
            self.started.clear()
            self.release.clear()
            self.completed.clear()

    def count_suffix(self, suffix):
        with self._lock:
            return sum(count for path, count in self.request_counts.items() if path.endswith(suffix))

    def count_prefix(self, prefix):
        with self._lock:
            return sum(count for path, count in self.request_counts.items() if path.startswith(prefix))

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return
        path = scope.get("path", "")
        with self._lock:
            self.request_counts[path] = self.request_counts.get(path, 0) + 1
            should_delay = path == self._target_path
            if should_delay:
                self._target_path = None
        response_status = None
        delayed = False

        async def gated_send(message):
            nonlocal response_status, delayed
            if message["type"] == "http.response.start":
                response_status = message["status"]
            if should_delay and not delayed and message["type"] == "http.response.body":
                delayed = True
                self.started.set()
                await asyncio.to_thread(self.release.wait, 15)
                self.delayed_statuses.append(response_status)
            try:
                await send(message)
            except (BrokenPipeError, ConnectionResetError, OSError, RuntimeError):
                # 页面已 Abort 时浏览器可提前关闭 HTTP 流；服务端处理结果仍已生成。
                if not should_delay:
                    raise
            finally:
                if should_delay and message["type"] == "http.response.body" and not message.get("more_body", False):
                    self.completed.set()

        await self.app(scope, receive, gated_send)


def _share_browser_fixture(monkeypatch, tmp_path):
    monkeypatch.setenv("GW_AUTH_MODE", "local_account")
    monkeypatch.setenv("GW_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("GW_VIDEO_DATA_DIR", str(tmp_path / "video"))
    monkeypatch.setenv("GW_LOCAL_AUTH_DB", str(tmp_path / "auth.sqlite3"))
    media_root = tmp_path / "media"
    monkeypatch.setenv("GW_ALLOWED_ROOTS", str(media_root))
    spec = importlib.util.spec_from_file_location(
        "share_lifecycle_fixtures", ROOT / "tests/contracts/assets/test_phase12_share_closure.py"
    )
    helpers = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helpers)
    with TestClient(
        create_app(), base_url="http://localhost", client=("127.0.0.1", 50000),
        headers={"Origin": "http://localhost"},
    ) as client:
        setup = client.post("/api/asset-auth/local/setup", json={"username": "share_lifecycle_owner", "password": "Test2026"})
        assert setup.status_code == 201
        asset_id = helpers._asset(client)
        payload = helpers._png_1x1()
        helpers._attach_file(client, asset_id, "tiny.png", payload, media_root, monkeypatch)
        token, _ = helpers._share(client, [asset_id], password="visitor-test", can_download=True)
        landing = client.get("/share/" + token)
        assert landing.status_code == 200
    return create_app(), token, payload


@pytest.mark.parametrize("late_endpoint", ["access", "comments"])
def test_share_pagehide_discards_late_http_responses_and_revalidates(monkeypatch, tmp_path, late_endpoint):
    """真实 Chrome 中迟到响应不能恢复旧票据；写请求已处理时只丢弃页面回填。"""
    browser_api = pytest.importorskip("playwright.sync_api")
    app, token, payload = _share_browser_fixture(monkeypatch, tmp_path)
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    base = f"http://127.0.0.1:{listener.getsockname()[1]}"
    gate = _ShareResponseGate(app)
    target_path = f"/api/public/shares/{token}/{late_endpoint}"
    gate.delay_next(target_path)
    server = uvicorn.Server(uvicorn.Config(gate, log_level="error", lifespan="off"))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
    thread.start()
    evidence = {
        "scope": "真实 Chrome 与真实 HTTP/ASGI；pagehide/pageshow 为受控事件派发，不代表实际 BFCache 命中",
        "late_endpoint": late_endpoint,
        "page_errors": [],
        "late_response_status": None,
        "media_requests_before_hide": None,
        "media_requests_after_late_response": None,
        "blob_urls_before_hide": None,
        "blob_urls_after_late_response": None,
        "metadata_requests_after_restore": None,
        "fresh_ticket_revalidation": False,
    }
    try:
        deadline = time.monotonic() + 10
        while not server.started and thread.is_alive() and time.monotonic() < deadline:
            time.sleep(0.05)
        assert server.started
        with open_playwright(browser_api) as pw:
            try:
                browser = pw.chromium.launch(channel="chrome", headless=True)
            except browser_api.Error as exc:
                pytest.skip(f"Chrome不可启动：{type(exc).__name__}")
            try:
                context = browser.new_context(viewport={"width": 1440, "height": 1000}, accept_downloads=True)
                context.route("**/*", lambda route: route.continue_() if route.request.url.startswith(base + "/") else route.abort())
                context.expose_function("shareGateStarted", lambda: gate.started.is_set())
                context.expose_function("shareGateCompleted", lambda: gate.completed.is_set())
                page = context.new_page()
                page.on("pageerror", lambda error: evidence["page_errors"].append(type(error).__name__))
                page.on("dialog", lambda dialog: dialog.accept("审批备注"))
                page.add_init_script("""window.__shareBlobs=[];window.__shareRevokes=[];const create=URL.createObjectURL.bind(URL);URL.createObjectURL=b=>{const url=create(b);window.__shareBlobs.push(url);return url};const revoke=URL.revokeObjectURL.bind(URL);URL.revokeObjectURL=url=>{window.__shareRevokes.push(url);return revoke(url)}""")
                page.goto(base + "/static/asset-share.html", wait_until="networkidle")
                assert "缺少分享令牌" in page.locator("#shareApp").inner_text()
                assert gate.count_prefix("/api/public/shares/") == 0, "无 token 直开不得发送公开分享请求"
                page.goto(base + "/share/" + token, wait_until="networkidle")
                page.evaluate("""() => {
                    window.__shareGateState = {started:false, completed:false};
                    const poll = async () => {
                        const [started, completed] = await Promise.all([
                            window.shareGateStarted(), window.shareGateCompleted()
                        ]);
                        window.__shareGateState = {started, completed};
                        if (!completed) requestAnimationFrame(poll);
                    };
                    requestAnimationFrame(poll);
                }""")

                if late_endpoint == "comments":
                    page.locator('#shareAccessForm input[name="password"]').fill("visitor-test")
                    page.locator('#shareAccessForm button[type="submit"]').click()
                    page.wait_for_function("() => document.querySelector('#shareMedia')?.naturalWidth === 1")
                    assert gate.count_suffix("/media") == 1
                    late_text = "迟到响应仍保留服务端写入"
                    page.locator('#shareCommentForm textarea[name="body"]').fill(late_text)
                    page.locator('#shareCommentForm input[name="guest_name"]').fill("生命周期访客")
                    page.locator('#shareCommentForm button[type="submit"]').click()
                else:
                    page.locator('#shareAccessForm input[name="password"]').fill("visitor-test")
                    page.locator('#shareAccessForm button[type="submit"]').click()

                page.wait_for_function("() => window.__shareGateState?.started === true", timeout=10_000)
                evidence["media_requests_before_hide"] = gate.count_suffix("/media")
                evidence["blob_urls_before_hide"] = page.evaluate("window.__shareBlobs.length")
                page.evaluate("window.dispatchEvent(new PageTransitionEvent('pagehide',{persisted:true}))")
                page.wait_for_function("() => document.querySelector('#shareApp')?.classList.contains('share-loading')")
                gate.release.set()
                page.wait_for_function("() => window.__shareGateState?.completed === true", timeout=10_000)

                assert page.locator("#shareCommentForm").count() == 0
                assert page.locator("#shareMedia").count() == 0
                assert page.locator("#shareApp").get_attribute("class") == "share-loading"
                evidence["media_requests_after_late_response"] = gate.count_suffix("/media")
                evidence["blob_urls_after_late_response"] = page.evaluate("window.__shareBlobs.length")
                assert evidence["media_requests_after_late_response"] == evidence["media_requests_before_hide"]
                assert evidence["blob_urls_after_late_response"] == evidence["blob_urls_before_hide"]
                assert gate.delayed_statuses, "受控响应没有到达真实后端处理完成阶段"
                evidence["late_response_status"] = gate.delayed_statuses[-1]
                assert evidence["late_response_status"] == (200 if late_endpoint == "access" else 201)

                # 用受控 pageshow 表示从冻结页恢复，明确要求重新读取元信息并重新获取票据。
                page.evaluate("window.dispatchEvent(new PageTransitionEvent('pageshow',{persisted:true}))")
                page.locator('#shareAccessForm input[name="password"]').wait_for()
                evidence["metadata_requests_after_restore"] = gate.count_suffix(f"/shares/{token}")
                assert evidence["metadata_requests_after_restore"] == 2
                assert gate.count_suffix("/access") == 1
                assert gate.count_suffix("/media") == evidence["media_requests_before_hide"]
                page.locator('#shareAccessForm input[name="password"]').fill("visitor-test")
                page.locator('#shareAccessForm button[type="submit"]').click()
                page.wait_for_function("() => document.querySelector('#shareMedia')?.naturalWidth === 1")
                assert gate.count_suffix("/access") == 2
                if late_endpoint == "comments":
                    page.get_by_text(late_text, exact=True).wait_for()
                evidence["fresh_ticket_revalidation"] = True
                assert not evidence["page_errors"]
                evidence["controlled_lifecycle_dispatch"] = True
                evidence["server_write_not_rolled_back"] = late_endpoint == "comments"
                evidence["blob_urls"] = page.evaluate("window.__shareBlobs.length")
                evidence["revoked_blob_urls"] = page.evaluate("window.__shareRevokes.length")
                context.close()
            finally:
                browser.close()
    finally:
        gate.release.set()
        server.should_exit = True
        thread.join(timeout=10)
        listener.close()
        (tmp_path / f"share-lifecycle-{late_endpoint}-evidence.json").write_text(
            json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8"
        )
