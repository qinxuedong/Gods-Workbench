"""真实服务与 Chrome 验证本地账号登录；运行期数据库仅存于测试临时目录。"""

import os
from pathlib import Path
import socket
import subprocess
import sys
import time
from http.client import HTTPConnection, HTTPException
import json

import pytest
from playwright_support import open_playwright


ROOT = Path(__file__).resolve().parents[2]


def local_health_ready(port):
    """先绑定不同源端口再连接，防止服务尚未监听时Windows TCP连接到自身。"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(1)
        sock.bind(("127.0.0.1", 0))
        if sock.getsockname()[1] == port:
            return False
        connection = HTTPConnection("127.0.0.1", port, timeout=1)
        try:
            sock.connect(("127.0.0.1", port))
            connection.sock = sock
            connection.request("GET", "/healthz")
            response = connection.getresponse()
            payload = json.loads(response.read(4096))
            return response.status == 200 and payload.get("status") == "ok" and payload.get("mode") == "cleanroom"
        except (OSError, HTTPException, ValueError):
            return False
        finally:
            connection.close()


def start_owned_server(command, env, log, port):
    """启动失败时也回收本函数创建的子进程，避免句柄尚未返回就泄漏服务。"""
    # 只接受本次子进程绑定完成后的消息；先发TCP探针可能抢占尚未监听的目标端口。
    log.flush()
    log_path = Path(log.name)
    log_offset = log_path.stat().st_size
    listener_message = f"Uvicorn running on http://127.0.0.1:{port} ".encode()
    child = subprocess.Popen(command, env=env, cwd=ROOT, stdout=log, stderr=log,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    try:
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            if child.poll() is not None:
                raise AssertionError(f"测试服务提前退出；日志：{log_path}")
            if listener_message in log_path.read_bytes()[log_offset:] and local_health_ready(port):
                return child
            time.sleep(0.1)
        raise AssertionError("测试服务未就绪")
    except BaseException:
        if child.poll() is None:
            child.terminate()
            child.wait(timeout=5)
        raise


def test_local_account_browser_setup_login_restart_logout(tmp_path):
    browser_api = pytest.importorskip("playwright.sync_api")
    with open_playwright(browser_api) as pw:
        try:
            browser = pw.chromium.launch(channel="chrome", headless=True)
        except browser_api.Error as exc:
            pytest.skip(f"Chrome 不可启动：{exc}")
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        base = f"http://127.0.0.1:{port}"
        env = os.environ.copy()
        env.update(
            GW_RUNTIME_MODE="test",
            GW_AUTH_MODE="local_account",
            GW_DATA_DIR=str(tmp_path / "data"),
            GW_LOCAL_AUTH_DB=str(tmp_path / "auth" / "auth.sqlite3"),
            GW_VIDEO_DATA_DIR=str(tmp_path / "video"),
            GW_PORT=str(port),
            GW_HOST="127.0.0.1",
            GW_RELOAD="false",
            PYTHONPATH=str(ROOT),
        )
        command = [sys.executable, str(ROOT / "run.py"), "--test"]
        process = None
        log = (tmp_path / "server.log").open("wb")

        def start_server():
            return start_owned_server(command, env, log, port)

        try:
            process = start_server()
            page = browser.new_page()
            page.route("**/*", lambda route: route.continue_() if route.request.url.startswith(base + "/") else route.abort())
            page.goto(base + "/static/pages/projects.html")
            page.wait_for_selector("#localUsername")
            page.screenshot(path=str(tmp_path / "local-account-setup.png"))
            password = "Test2026"
            page.fill("#localUsername", "browser_owner")
            page.fill("#localPassword", password)
            page.fill("#localPasswordConfirm", password)
            with page.expect_navigation():
                page.click("#localLoginSubmit")
            page.wait_for_function("HardwareDeck.authState.authenticated === true")
            assert page.evaluate("HardwareDeck.authState.principal.role") == "admin"
            assert "gw_session" not in page.evaluate("document.cookie")
            status = page.evaluate("""async () => (await fetch('/api/asset-registry/projects', {
                method: 'POST', headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({name: '登录权限浏览器测试', project_type: 'film'})
            })).status""")
            assert status == 201

            # 完全重启应用进程，同一数据库仍能恢复账户与现有登录会话。
            process.terminate()
            process.wait(timeout=5)
            process = start_server()
            page.reload()
            page.wait_for_function("HardwareDeck.authState.authenticated === true")
            page.locator('.hw-avatar-keycap').first.click()
            assert "本地账户数据库" in page.locator('#accountModalDialog').inner_text()
            page.get_by_role('button', name='退出登录', exact=True).click()
            page.wait_for_selector('#localPassword')
            assert page.locator('#localPasswordConfirm').count() == 0
            page.fill('#localUsername', 'browser_owner')
            page.fill('#localPassword', 'Wrong-password-2026!')
            page.click('#localLoginSubmit')
            page.wait_for_selector('#localLoginError:not(.hidden)')
            page.fill('#localPassword', password)
            with page.expect_navigation():
                page.click('#localLoginSubmit')
            page.wait_for_function("HardwareDeck.authState.authenticated === true")
            # 退出后，业务接口必须立即恢复为未认证。
            assert page.evaluate("async () => (await fetch('/api/asset-auth/logout', {method: 'POST'})).status") == 204
            assert page.evaluate("async () => (await fetch('/api/asset-auth/users')).status") == 401
        finally:
            browser.close()
            if process and process.poll() is None:
                process.terminate()
                process.wait(timeout=5)
            log.close()


def test_health_probe_rejects_self_connect_before_connecting(monkeypatch):
    class BoundSocket:
        connected = False
        closed = False
        def __enter__(self): return self
        def __exit__(self, *args): self.closed = True
        def settimeout(self, value): pass
        def bind(self, address): pass
        def getsockname(self): return ("127.0.0.1", 12345)
        def connect(self, address):
            self.connected = True
            raise AssertionError("不能向自身端口发起连接")
    bound = BoundSocket()
    monkeypatch.setattr(socket, "socket", lambda *args, **kwargs: bound)
    assert local_health_ready(12345) is False
    assert not bound.connected and bound.closed


def test_startup_exception_reaps_only_the_owned_child(monkeypatch, tmp_path):
    class Child:
        stopped = False
        waited = False
        def poll(self): return None
        def terminate(self): self.stopped = True
        def wait(self, timeout): self.waited = True
    child = Child()
    def started_child(*args, **kwargs):
        kwargs["stdout"].write(b"Uvicorn running on http://127.0.0.1:12345 (Press CTRL+C to quit)\n")
        kwargs["stdout"].flush()
        return child
    monkeypatch.setattr(subprocess, "Popen", started_child)
    def broken_probe(port):
        raise RuntimeError("fixture-startup-exception")
    monkeypatch.setitem(start_owned_server.__globals__, "local_health_ready", broken_probe)
    with (tmp_path / "server.log").open("wb") as log:
        with pytest.raises(RuntimeError, match="fixture-startup-exception"):
            start_owned_server([], {}, log, 12345)
    assert child.stopped and child.waited


@pytest.mark.parametrize("prior_listener_log", [False, True])
def test_startup_probe_waits_for_current_child_listener(tmp_path, monkeypatch, prior_listener_log):
    """真实子进程中强制早探针占用候选端口；旧启动日志不能充当本次监听证明。"""
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
    env = os.environ.copy()
    env.update(
        GW_RUNTIME_MODE="test", GW_AUTH_MODE="local_account",
        GW_DATA_DIR=str(tmp_path / "data"),
        GW_LOCAL_AUTH_DB=str(tmp_path / "auth" / "auth.sqlite3"),
        GW_VIDEO_DATA_DIR=str(tmp_path / "video"),
        GW_PORT=str(port), GW_HOST="127.0.0.1", GW_RELOAD="false", PYTHONPATH=str(ROOT),
    )
    # 延迟的仍是真实 run.py；只让绑定前窗口可复现，不替代应用或健康端点。
    command = [sys.executable, "-c", "import runpy,time; time.sleep(0.3); runpy.run_path('run.py', run_name='__main__')", "--test"]
    log_path = tmp_path / "server.log"
    ready = f"Uvicorn running on http://127.0.0.1:{port} ".encode()
    log_path.write_bytes(ready + b"(prior child)\n" if prior_listener_log else b"")
    offset = log_path.stat().st_size
    real_probe = local_health_ready
    early_probe_claims = []

    def probe_with_forced_unbound_port_collision(target):
        if ready not in log_path.read_bytes()[offset:]:
            # 模拟 bind(..., 0) 在监听前恰好选中目标端口的合法调度；不改系统端口配置。
            with socket.socket() as contender:
                contender.bind(("127.0.0.1", target))
                early_probe_claims.append(target)
                deadline = time.monotonic() + 10
                while b"[Errno" not in log_path.read_bytes()[offset:] and time.monotonic() < deadline:
                    time.sleep(0.02)
            return False
        return real_probe(target)

    monkeypatch.setitem(start_owned_server.__globals__, "local_health_ready", probe_with_forced_unbound_port_collision)
    child = None
    with log_path.open("ab") as log:
        try:
            child = start_owned_server(command, env, log, port)
            assert not early_probe_claims, "不得在当前子进程绑定前运行会分配源端口的健康探针"
            assert real_probe(port), "监听消息不能代替真实应用健康检查"
        finally:
            if child is not None and child.poll() is None:
                child.terminate()
                child.wait(timeout=5)
