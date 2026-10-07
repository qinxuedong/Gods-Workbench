"""本地 OIDC 第三方 OP 测试夹具生命周期回归。"""

from __future__ import annotations

import importlib.util
import io
import json
import os
import subprocess
import sys
import threading
import types
from pathlib import Path
from types import SimpleNamespace

import pytest

FIXTURE_PATH = Path(__file__).resolve().parents[1] / "contracts" / "auth" / "test_phase9g_real_op_interop.py"
_SPEC = importlib.util.spec_from_file_location("phase9g_real_op_fixture_under_test", FIXTURE_PATH)
assert _SPEC is not None and _SPEC.loader is not None
_fixture = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_fixture)


class _BlockingStdout:
    """模拟尚未写 ready 行的活动子进程输出管道。"""

    def __init__(self):
        self.released = threading.Event()
        self.closed = False

    def read1(self, _size):
        self.released.wait()
        return b""

    def close(self):
        self.closed = True
        self.released.set()


class _StickyStdout:
    """模拟关闭应用侧句柄也不能安全打断的 reader；由测试显式释放。"""

    def __init__(self):
        self.entered = threading.Event()
        self.release_event = threading.Event()
        self.closed = False

    def read1(self, _size):
        self.entered.set()
        self.release_event.wait()
        return b""

    def close(self):
        self.closed = True


class _FakeProcess:
    def __init__(self, stdout=None, stderr=None, returncode=None):
        self.stdout = stdout if stdout is not None else io.BytesIO()
        self.stderr = stderr if stderr is not None else io.BytesIO()
        self.returncode = returncode
        self.terminated = False
        self.killed = False
        self.waited = False

    def poll(self):
        return self.returncode

    def terminate(self):
        self.terminated = True
        self.returncode = 0
        if hasattr(self.stdout, "released"):
            self.stdout.released.set()
        if hasattr(self.stderr, "released"):
            self.stderr.released.set()

    def kill(self):
        self.killed = True
        self.returncode = -9
        if hasattr(self.stdout, "released"):
            self.stdout.released.set()
        if hasattr(self.stderr, "released"):
            self.stderr.released.set()

    def wait(self, timeout=None):
        self.waited = True
        if self.returncode is None:
            raise subprocess.TimeoutExpired(cmd="fake-node", timeout=timeout)
        return self.returncode


class _NeverReapedProcess:
    def __init__(self):
        self.terminate_calls = 0
        self.kill_calls = 0
        self.wait_calls = 0

    def poll(self):
        return None

    def terminate(self):
        self.terminate_calls += 1

    def kill(self):
        self.kill_calls += 1

    def wait(self, timeout=None):
        self.wait_calls += 1
        raise subprocess.TimeoutExpired(cmd="fake-node", timeout=timeout)


def _capture_state(max_bytes=None):
    limit = _fixture._NODE_STDOUT_MAX_BYTES if max_bytes is None else max_bytes
    return _fixture._new_node_capture_state(limit)


def _gw_environment():
    return {key: value for key, value in os.environ.items() if key.upper().startswith("GW_")}


def _restore_gw_environment(snapshot):
    for key in list(os.environ):
        if key.upper().startswith("GW_"):
            del os.environ[key]
    os.environ.update(snapshot)


def test_node_without_ready_times_out_and_its_pipe_reader_is_joined(tmp_path):
    process = _FakeProcess(stdout=_BlockingStdout())
    stdout_path = tmp_path / "stdout.spool"
    capture = _capture_state()
    _fixture._start_node_capture(process.stdout, stdout_path, capture, "test-node-stdout")

    with pytest.raises(TimeoutError, match="未输出 ready"):
        _fixture._read_node_startup_line(process, stdout_path, capture, timeout_seconds=0.02)

    cleanup_state = {"unsafe_cleanup": []}
    _fixture._stop_node_resources_or_record(process, [capture], cleanup_state)

    assert process.terminated is True
    assert process.waited is True
    assert capture["thread"].is_alive() is False
    assert process.stdout.closed is True
    assert cleanup_state["unsafe_cleanup"] == []


def test_live_reader_pipe_is_not_closed_from_another_thread(tmp_path):
    stream = _StickyStdout()
    process = _FakeProcess(stdout=stream)
    stdout_path = tmp_path / "stdout.spool"
    capture = _capture_state()
    _fixture._start_node_capture(stream, stdout_path, capture, "test-sticky-node-stdout")
    assert stream.entered.wait(timeout=1)
    cleanup_state = {"unsafe_cleanup": []}

    try:
        with pytest.raises(RuntimeError, match="保留其管道避免跨线程 close"):
            _fixture._stop_node_resources_or_record(
                process,
                [capture],
                cleanup_state,
                timeout_seconds=0.01,
            )
        assert stream.closed is False
        assert capture["thread"].is_alive() is True
        assert cleanup_state["unsafe_cleanup"]
    finally:
        stream.release_event.set()
        capture["thread"].join(timeout=1)
        if not stream.closed:
            stream.close()
    assert capture["thread"].is_alive() is False


def test_node_early_exit_is_reported_and_reaped(tmp_path):
    process = _FakeProcess(stdout=io.BytesIO(), returncode=17)
    stdout_path = tmp_path / "stdout.spool"
    capture = _capture_state()
    _fixture._start_node_capture(process.stdout, stdout_path, capture, "test-node-stdout")
    assert capture["finished"].wait(timeout=1)

    with pytest.raises(RuntimeError, match="退出码 17"):
        _fixture._read_node_startup_line(process, stdout_path, capture, timeout_seconds=0.2)

    _fixture._stop_node_resources_or_record(process, [capture], {"unsafe_cleanup": []})
    assert process.waited is True
    assert capture["thread"].is_alive() is False


def test_node_stdout_spool_is_byte_bounded(tmp_path):
    process = _FakeProcess(stdout=io.BytesIO(b"ready-too-large"))
    stdout_path = tmp_path / "stdout.spool"
    capture = _capture_state(max_bytes=4)
    _fixture._start_node_capture(process.stdout, stdout_path, capture, "test-node-stdout")
    assert capture["finished"].wait(timeout=1)

    with pytest.raises(RuntimeError, match="超过 4 字节上限"):
        _fixture._read_node_startup_line(process, stdout_path, capture, timeout_seconds=0.2)

    assert stdout_path.stat().st_size <= 4
    _fixture._stop_node_resources_or_record(process, [capture], {"unsafe_cleanup": []})


def test_wrong_oidc_provider_version_is_rejected_before_popen(monkeypatch, tmp_path):
    provider_root = tmp_path / "provider"
    package_json = provider_root / "node_modules" / "oidc-provider" / "package.json"
    package_json.parent.mkdir(parents=True)
    package_json.write_text(json.dumps({"version": "9.12.1"}), encoding="utf-8")
    monkeypatch.setattr(_fixture.shutil, "which", lambda _name: sys.executable)
    monkeypatch.setattr(_fixture, "_provider_dir", lambda: provider_root)
    popen_calls = []
    monkeypatch.setattr(_fixture.subprocess, "Popen", lambda *args, **kwargs: popen_calls.append((args, kwargs)))

    with pytest.raises(RuntimeError, match="期望 9.12.2，实际 9.12.1"):
        next(_fixture.real_op.__wrapped__(tmp_path))

    assert popen_calls == []


def test_owned_python_child_escalates_from_terminate_to_kill():
    child = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(60)"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    class _UnresponsiveAdapter:
        def __init__(self, process):
            self.process = process
            self.terminate_called = False
            self.kill_called = False

        def poll(self):
            return self.process.poll()

        def terminate(self):
            self.terminate_called = True

        def kill(self):
            self.kill_called = True
            self.process.kill()

        def wait(self, timeout=None):
            return self.process.wait(timeout=timeout)

    owned = _UnresponsiveAdapter(child)
    try:
        _fixture._stop_owned_process(
            owned,
            "受控 Python 子进程",
            terminate_timeout_seconds=0.02,
            kill_timeout_seconds=2,
        )
        assert owned.terminate_called is True
        assert owned.kill_called is True
        assert child.poll() is not None
    finally:
        if child.poll() is None:
            child.kill()
        child.wait(timeout=2)


def test_process_that_survives_kill_and_wait_is_reported():
    process = _NeverReapedProcess()

    with pytest.raises(RuntimeError, match="terminate/kill 后仍未退出"):
        _fixture._stop_owned_process(
            process,
            "模拟 OP",
            terminate_timeout_seconds=0.01,
            kill_timeout_seconds=0.01,
        )

    assert process.terminate_calls == 1
    assert process.kill_calls == 1
    assert process.wait_calls == 2


def test_node_environment_is_an_explicit_minimum_allowlist(tmp_path):
    source_environment = {
        "PATH": r"C:\Program Files\nodejs",
        "SYSTEMROOT": r"C:\Windows",
        "TEMP": r"C:\Temp",
        "GW_OIDC_PROVIDER_MODULE_DIR": r"C:\private\provider",
        "GW_OIDC_ISSUER": "https://real-idp.invalid",
        "GW_RUNTIME_MODE": "production",
        "HTTPS_PROXY": "http://proxy.invalid:8080",
        "HTTP_PROXY": "http://proxy.invalid:8080",
        "ALL_PROXY": "socks5://proxy.invalid:1080",
        "NO_PROXY": "real-idp.invalid",
        "NODE_OPTIONS": "--require C:\\private\\inject.js",
        "NODE_EXTRA_CA_CERTS": r"C:\private\ca.pem",
        "NODE_TLS_REJECT_UNAUTHORIZED": "0",
        "OIDC_ISSUER": "https://real-idp.invalid",
    }

    child_environment = _fixture._node_environment(tmp_path / "node_modules", source_environment)

    assert child_environment == {
        "PATH": source_environment["PATH"],
        "SYSTEMROOT": source_environment["SYSTEMROOT"],
        "TEMP": source_environment["TEMP"],
        "NODE_PATH": str(tmp_path / "node_modules"),
    }


def test_runtime_data_is_isolated_before_application_import_and_restored(monkeypatch, tmp_path):
    monkeypatch.delenv("GW_RUNTIME_MODE", raising=False)
    issuer = "http://127.0.0.1:43121"
    redirect_uri = "http://127.0.0.1:43122/api/asset-auth/callback"
    monkeypatch.setenv("GW_SENTINEL_PROVIDER_SECRET", "preserve-in-parent-only")
    reset_calls = []
    config = SimpleNamespace(
        reset_runtime_auth_config_cache=lambda: reset_calls.append("runtime"),
        reset_discovery_cache=lambda: reset_calls.append("discovery"),
    )

    def load_config():
        assert os.environ["GW_RUNTIME_MODE"] == "test"
        assert os.environ["GW_AUTH_MODE"] == "oidc"
        assert os.environ["GW_OIDC_ISSUER"] == issuer
        assert "GW_SENTINEL_PROVIDER_SECRET" not in os.environ
        for name in ("GW_DATA_DIR", "GW_LOCAL_AUTH_DB", "GW_VIDEO_DATA_DIR"):
            path = Path(os.environ[name])
            assert path.is_absolute()
            assert not path.is_relative_to(_fixture.REPO_ROOT.resolve())
        assert Path(os.environ["GW_DATA_DIR"]).is_dir()
        assert Path(os.environ["GW_LOCAL_AUTH_DB"]).parent.is_dir()
        assert Path(os.environ["GW_VIDEO_DATA_DIR"]).is_dir()
        return config

    def load_app():
        assert "GW_SENTINEL_PROVIDER_SECRET" not in os.environ
        assert Path(os.environ["GW_DATA_DIR"]).is_absolute()
        return lambda: "fake-app"

    monkeypatch.setattr(_fixture, "_load_config_module", load_config)
    monkeypatch.setattr(_fixture, "_load_create_app", load_app)
    cleanup_state = {"unsafe_cleanup": [], "original_environment": None, "gw_config": None}
    try:
        create_app = _fixture._configure_application_environment(
            tmp_path, issuer, redirect_uri, cleanup_state
        )
        assert create_app() == "fake-app"
        assert "GW_SENTINEL_PROVIDER_SECRET" not in os.environ
    finally:
        _fixture._finish_application_environment(cleanup_state)

    assert os.environ["GW_SENTINEL_PROVIDER_SECRET"] == "preserve-in-parent-only"
    assert "GW_RUNTIME_MODE" not in os.environ
    assert reset_calls == ["runtime", "discovery", "runtime", "discovery"]


def test_application_import_failure_restores_environment(monkeypatch, tmp_path):
    monkeypatch.delenv("GW_RUNTIME_MODE", raising=False)
    monkeypatch.setenv("GW_SENTINEL_PROVIDER_SECRET", "restore-me")
    reset_calls = []
    config = SimpleNamespace(
        reset_runtime_auth_config_cache=lambda: reset_calls.append("runtime"),
        reset_discovery_cache=lambda: reset_calls.append("discovery"),
    )
    monkeypatch.setattr(_fixture, "_load_config_module", lambda: config)

    def fail_app_import():
        assert os.environ["GW_RUNTIME_MODE"] == "test"
        assert Path(os.environ["GW_LOCAL_AUTH_DB"]).is_absolute()
        raise ImportError("synthetic app import failure")

    monkeypatch.setattr(_fixture, "_load_create_app", fail_app_import)
    cleanup_state = {"unsafe_cleanup": [], "original_environment": None, "gw_config": None}
    try:
        with pytest.raises(ImportError, match="synthetic app import failure"):
            _fixture._configure_application_environment(
                tmp_path, "http://127.0.0.1:43001", "http://127.0.0.1:43002/callback", cleanup_state
            )
    finally:
        _fixture._finish_application_environment(cleanup_state)

    assert os.environ["GW_SENTINEL_PROVIDER_SECRET"] == "restore-me"
    assert "GW_RUNTIME_MODE" not in os.environ
    assert reset_calls == ["runtime", "discovery", "runtime", "discovery"]


def test_app_factory_failure_still_restores_environment(monkeypatch, tmp_path):
    monkeypatch.delenv("GW_RUNTIME_MODE", raising=False)
    monkeypatch.setenv("GW_SENTINEL_PROVIDER_SECRET", "restore-me-too")
    reset_calls = []
    config = SimpleNamespace(
        reset_runtime_auth_config_cache=lambda: reset_calls.append("runtime"),
        reset_discovery_cache=lambda: reset_calls.append("discovery"),
    )

    def failing_create_app():
        assert os.environ["GW_RUNTIME_MODE"] == "test"
        raise RuntimeError("synthetic app startup failure")

    monkeypatch.setattr(_fixture, "_load_config_module", lambda: config)
    monkeypatch.setattr(_fixture, "_load_create_app", lambda: failing_create_app)
    cleanup_state = {"unsafe_cleanup": [], "original_environment": None, "gw_config": None}
    try:
        create_app = _fixture._configure_application_environment(
            tmp_path, "http://127.0.0.1:43003", "http://127.0.0.1:43004/callback", cleanup_state
        )
        with pytest.raises(RuntimeError, match="synthetic app startup failure"):
            create_app()
    finally:
        _fixture._finish_application_environment(cleanup_state)

    assert os.environ["GW_SENTINEL_PROVIDER_SECRET"] == "restore-me-too"
    assert "GW_RUNTIME_MODE" not in os.environ
    assert reset_calls == ["runtime", "discovery", "runtime", "discovery"]


def test_app_health_deadline_is_finite_and_worker_can_be_released():
    stopped = threading.Event()
    app_thread = threading.Thread(target=stopped.wait, daemon=True)
    app_thread.start()

    class OfflineRequests:
        @staticmethod
        def get(_url, timeout):
            assert 0 < timeout <= 0.02
            raise OSError("offline mock")

    try:
        with pytest.raises(TimeoutError, match="0.02s"):
            _fixture._wait_for_app_health(
                OfflineRequests,
                "http://127.0.0.1:43123",
                app_thread,
                timeout_seconds=0.02,
            )
    finally:
        stopped.set()
        app_thread.join(timeout=0.2)
    assert not app_thread.is_alive()


def test_app_thread_refusing_exit_fail_stops_before_environment_restore(monkeypatch):
    previous_environment = _gw_environment()
    monkeypatch.setenv("GW_RUNTIME_MODE", "isolated-test")
    stopped = threading.Event()
    app_thread = threading.Thread(target=stopped.wait, daemon=True)
    app_thread.start()
    cleanup_state = {
        "unsafe_cleanup": [],
        "original_environment": {"GW_PARENT_SENTINEL": "must-not-restore-yet"},
        "gw_config": None,
    }
    server = SimpleNamespace(should_exit=False)

    class _HardStop(BaseException):
        pass

    def hard_stop(code):
        raise _HardStop(code)

    try:
        _fixture._stop_app_thread(server, app_thread, cleanup_state, timeout_seconds=0.01)
        assert "应用线程在有限 join 后仍存活" in cleanup_state["unsafe_cleanup"]
        monkeypatch.setattr(_fixture.os, "_exit", hard_stop)
        with pytest.raises(_HardStop) as exc_info:
            _fixture._finish_application_environment(cleanup_state)
        assert exc_info.value.args == (_fixture._APP_THREAD_FAILURE_EXIT_CODE,)
        assert os.environ["GW_RUNTIME_MODE"] == "isolated-test"
        assert "GW_PARENT_SENTINEL" not in os.environ
    finally:
        stopped.set()
        app_thread.join(timeout=0.2)
        _restore_gw_environment(previous_environment)
    assert not app_thread.is_alive()


def test_normal_app_cleanup_sets_exit_and_joins_thread():
    stopped = threading.Event()

    class FakeServer:
        def __init__(self):
            self._should_exit = False

        @property
        def should_exit(self):
            return self._should_exit

        @should_exit.setter
        def should_exit(self, value):
            self._should_exit = value
            if value:
                stopped.set()

    server = FakeServer()
    app_thread = threading.Thread(target=stopped.wait, daemon=False)
    app_thread.start()
    _fixture._stop_app_thread(server, app_thread, {"unsafe_cleanup": []}, timeout_seconds=0.2)

    assert server.should_exit is True
    assert not app_thread.is_alive()


def _prepare_fixture(monkeypatch, tmp_path, health_ok=True, app_failure=None):
    provider_root = tmp_path / "provider"
    package_json = provider_root / "node_modules" / "oidc-provider" / "package.json"
    package_json.parent.mkdir(parents=True)
    package_json.write_text(json.dumps({"version": "9.12.2"}), encoding="utf-8")
    monkeypatch.setattr(_fixture.shutil, "which", lambda _name: sys.executable)
    monkeypatch.setattr(_fixture, "_provider_dir", lambda: provider_root)
    monkeypatch.setattr(_fixture, "_APP_READY_TIMEOUT_SECONDS", 0.02)

    process = _FakeProcess(
        stdout=io.BytesIO(b'{"ready":true,"issuer":"http://127.0.0.1:43199"}\n'),
        stderr=io.BytesIO(b""),
    )
    popen_calls = []

    def fake_popen(*args, **kwargs):
        popen_calls.append((args, kwargs))
        return process

    monkeypatch.setattr(_fixture.subprocess, "Popen", fake_popen)
    reset_calls = []
    config = SimpleNamespace(
        reset_runtime_auth_config_cache=lambda: reset_calls.append("runtime"),
        reset_discovery_cache=lambda: reset_calls.append("discovery"),
    )
    monkeypatch.setattr(_fixture, "_load_config_module", lambda: config)
    app_import_calls = []

    def load_create_app():
        app_import_calls.append(os.environ.get("GW_RUNTIME_MODE"))
        if app_failure == "import":
            raise ImportError("synthetic app import failure")

        def create_app():
            assert os.environ["GW_RUNTIME_MODE"] == "test"
            assert os.environ["GW_OIDC_ISSUER"] == "http://127.0.0.1:43199"
            if app_failure == "factory":
                raise RuntimeError("synthetic app factory failure")
            return "fake-app"

        return create_app

    monkeypatch.setattr(_fixture, "_load_create_app", load_create_app)
    sessions = []

    class FakeSession:
        def __init__(self):
            self.trust_env = True
            self.closed = False
            self.urls = []
            sessions.append(self)

        def get(self, url, timeout):
            self.urls.append((url, timeout))
            if not health_ok:
                raise OSError("synthetic loopback health failure")
            return SimpleNamespace(status_code=200)

        def close(self):
            self.closed = True

    fake_requests = SimpleNamespace(Session=FakeSession)
    fake_uvicorn_servers = []

    class FakeConfig:
        def __init__(self, app, **kwargs):
            self.app = app
            self.kwargs = kwargs

    class FakeServer:
        def __init__(self, config):
            self.config = config
            self._should_exit = False
            self.release = threading.Event()
            self.run_thread = None
            fake_uvicorn_servers.append(self)

        @property
        def should_exit(self):
            return self._should_exit

        @should_exit.setter
        def should_exit(self, value):
            self._should_exit = value
            if value:
                self.release.set()

        def run(self):
            self.run_thread = threading.current_thread()
            self.release.wait()

    fake_uvicorn = SimpleNamespace(Config=FakeConfig, Server=FakeServer)
    monkeypatch.setitem(sys.modules, "requests", fake_requests)
    monkeypatch.setitem(sys.modules, "uvicorn", fake_uvicorn)
    return SimpleNamespace(
        process=process,
        popen_calls=popen_calls,
        reset_calls=reset_calls,
        app_import_calls=app_import_calls,
        sessions=sessions,
        servers=fake_uvicorn_servers,
        requests=fake_requests,
        uvicorn=fake_uvicorn,
    )


def test_fixture_health_failure_cleans_node_and_app_and_restores_environment(monkeypatch, tmp_path):
    state = _prepare_fixture(monkeypatch, tmp_path, health_ok=False)
    monkeypatch.setenv("GW_SENTINEL_PROVIDER_SECRET", "restore-after-health-failure")
    monkeypatch.delenv("GW_RUNTIME_MODE", raising=False)

    with pytest.raises(TimeoutError, match="未健康就绪"):
        next(_fixture.real_op.__wrapped__(tmp_path))

    assert len(state.popen_calls) == 1
    assert state.popen_calls[0][1]["stdout"] == subprocess.PIPE
    assert state.popen_calls[0][1]["stderr"] == subprocess.PIPE
    assert state.process.terminated is True
    assert state.process.waited is True
    assert state.process.stdout.closed is True
    assert state.process.stderr.closed is True
    assert state.sessions and state.sessions[0].trust_env is False
    assert state.sessions[0].closed is True
    assert state.servers and state.servers[0].should_exit is True
    assert state.servers[0].run_thread is not None
    assert state.servers[0].run_thread.is_alive() is False
    assert os.environ["GW_SENTINEL_PROVIDER_SECRET"] == "restore-after-health-failure"
    assert "GW_RUNTIME_MODE" not in os.environ
    assert state.reset_calls == ["runtime", "discovery", "runtime", "discovery"]


def test_fixture_normal_yield_reaps_every_owned_resource(monkeypatch, tmp_path):
    state = _prepare_fixture(monkeypatch, tmp_path, health_ok=True)
    monkeypatch.setenv("GW_SENTINEL_PROVIDER_SECRET", "restore-after-success")
    monkeypatch.delenv("GW_RUNTIME_MODE", raising=False)
    fixture_generator = _fixture.real_op.__wrapped__(tmp_path)

    try:
        real_op = next(fixture_generator)
        assert real_op["provider_version"] == "9.12.2"
        assert real_op["issuer"] == "http://127.0.0.1:43199"
        assert state.sessions[0].trust_env is False
        assert state.sessions[0].closed is False
        assert state.process.poll() is None
    finally:
        fixture_generator.close()

    assert state.process.terminated is True
    assert state.process.waited is True
    assert state.process.stdout.closed is True
    assert state.process.stderr.closed is True
    assert state.sessions[0].closed is True
    assert state.servers[0].should_exit is True
    assert state.servers[0].run_thread.is_alive() is False
    assert os.environ["GW_SENTINEL_PROVIDER_SECRET"] == "restore-after-success"
    assert "GW_RUNTIME_MODE" not in os.environ
    assert state.reset_calls == ["runtime", "discovery", "runtime", "discovery"]


@pytest.mark.parametrize(
    ("failure_stage", "error_type", "error_text"),
    [
        ("import", ImportError, "synthetic app import failure"),
        ("factory", RuntimeError, "synthetic app factory failure"),
    ],
)
def test_fixture_import_and_factory_failures_reap_node_and_restore_environment(
    monkeypatch, tmp_path, failure_stage, error_type, error_text
):
    state = _prepare_fixture(monkeypatch, tmp_path, app_failure=failure_stage)
    monkeypatch.setenv("GW_SENTINEL_PROVIDER_SECRET", "restore-after-app-failure")
    monkeypatch.delenv("GW_RUNTIME_MODE", raising=False)

    with pytest.raises(error_type, match=error_text):
        next(_fixture.real_op.__wrapped__(tmp_path))

    assert state.process.terminated is True
    assert state.process.waited is True
    assert state.sessions == []
    assert os.environ["GW_SENTINEL_PROVIDER_SECRET"] == "restore-after-app-failure"
    assert "GW_RUNTIME_MODE" not in os.environ
    assert state.reset_calls == ["runtime", "discovery", "runtime", "discovery"]
