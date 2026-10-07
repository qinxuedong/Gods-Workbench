# -*- coding: utf-8 -*-
"""Phase 9G：真实第三方 OP（panva/oidc-provider）互操作契约测试。

与 ``test_phase9d_oidc_login_flow.py`` 的区别在于 **对端不是自研测试桩**：
本用例启动 npm 生态的第三方 OpenID Provider 实现 ``oidc-provider``，
按它真实的 discovery / JWKS / 授权 / 交互 / 令牌语义完成一次授权码 + PKCE 登录，
从而验证本仓实现能对接「非本仓写的」IdP（互操作，而非自洽）。

运行前提（缺任一项即 ``skip``，不影响默认 CI）：

- 本机可执行 ``node``；
- 能解析到 ``oidc-provider`` 包。可用 ``GW_OIDC_PROVIDER_MODULE_DIR``
  指向包含 ``node_modules/oidc-provider`` 的目录，或在运行环境里直接
  ``npm install oidc-provider``。

启动方式（示例）：

    set GW_OIDC_PROVIDER_MODULE_DIR=%TEMP%\\gw-idp-node
    python -m pytest -q tests/contracts/auth/test_phase9g_real_op_interop.py

证据边界：本用例验证**第三方 OP 软件**的互操作，不等于接入任何真实生产
IdP（无真实 client_id / 用户目录 / TLS / 撤销策略），也不构成发布授权。
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
from contextlib import ExitStack
from pathlib import Path
from urllib.parse import parse_qs, urljoin, urlparse

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
SRC_PATH = REPO_ROOT
if str(SRC_PATH) not in sys.path:
    sys.path.insert(0, str(SRC_PATH))

CLIENT_ID = "gw-interop-client"
ACCOUNT_ID = "interop-user"
GROUPS = "gw-editor"

# 文档（CLEANROOM-STATUS / TASK-NOTES / TASKS / P9-ACCEPTANCE-AUDIT）声称对端为 9.12.2。
# 必须显式断言，否则本机 `npm install oidc-provider` 解析出的版本可能静默漂移，
# 让「已验证 9.12.2」这一结论失去事实基础。
EXPECTED_OIDC_PROVIDER_VERSION = "9.12.2"

# 第三方 OP 桩：直接使用 oidc-provider 的真实路由与交互语义（纯 ASCII 源码）。
_OP_SOURCE = r"""
import http from 'node:http';
import { createRequire } from 'node:module';

const require = createRequire(import.meta.url);
const providerModule = require('oidc-provider');
const Provider = providerModule.default || providerModule.Provider || providerModule;

const port = Number(process.argv[2]);
const redirectUri = process.argv[3];
const clientId = process.argv[4];
const accountId = process.argv[5];
const groups = process.argv[6].split(',');
const issuer = `http://127.0.0.1:${port}`;

const provider = new Provider(issuer, {
  clients: [{
    client_id: clientId,
    redirect_uris: [redirectUri],
    grant_types: ['authorization_code'],
    response_types: ['code'],
    token_endpoint_auth_method: 'none',
    id_token_signed_response_alg: 'RS256',
  }],
  pkce: { required: () => true, methods: ['S256'] },
  features: { devInteractions: { enabled: false } },
  claims: { openid: ['sub', 'groups'] },
  scopes: ['openid', 'profile', 'email'],
  findAccount: (ctx, id) => ({
    accountId: id,
    async claims() { return { sub: id, groups }; },
  }),
  cookies: { keys: ['gw-interop-fixture-key-not-a-secret-000000'] },
});

const server = http.createServer(async (req, res) => {
  const url = new URL(req.url, issuer);
  if (url.pathname.startsWith('/interaction/')) {
    try {
      if (req.method === 'POST') {
        const details = await provider.interactionDetails(req, res);
        const result = { login: { accountId } };
        if (details.prompt && details.prompt.name === 'consent') {
          const grant = new provider.Grant({ accountId, clientId });
          grant.addOIDCScope(details.params.scope || 'openid');
          grant.addOIDCClaims(['sub', 'groups']);
          result.consent = { grantId: await grant.save() };
        }
        await provider.interactionFinished(req, res, result, { mergeWithLastSubmission: false });
        return;
      }
      res.writeHead(200, { 'content-type': 'text/html' });
      res.end('<!doctype html><html><body></body></html>');
      return;
    } catch (err) {
      res.writeHead(500, { 'content-type': 'text/plain' });
      res.end('interaction error: ' + err.message);
      return;
    }
  }
  return provider.callback()(req, res);
});

server.listen(port, '127.0.0.1', () => {
  process.stdout.write(JSON.stringify({ ready: true, issuer }) + '\n');
});
"""


def _free_port() -> int:
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


def _provider_dir() -> Path | None:
    """定位可解析 ``oidc-provider`` 的 node 模块目录。"""
    candidates = []
    configured = os.environ.get("GW_OIDC_PROVIDER_MODULE_DIR", "").strip()
    if configured:
        candidates.append(Path(configured))
    candidates.append(REPO_ROOT)
    for base in candidates:
        if (base / "node_modules" / "oidc-provider" / "package.json").is_file():
            return base.resolve()
    return None


def _provider_version(package_json: Path) -> str:
    """启动任何 OP 进程前验证固定版本；不允许环境变量覆盖预期值。"""
    try:
        metadata = json.loads(package_json.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("无法读取第三方 OP 的固定版本元数据") from exc
    actual = metadata.get("version") if isinstance(metadata, dict) else None
    if actual != EXPECTED_OIDC_PROVIDER_VERSION:
        raise RuntimeError(
            f"第三方 OP 版本不匹配：期望 {EXPECTED_OIDC_PROVIDER_VERSION}，实际 {actual}"
        )
    return actual

_NODE_SYSTEM_ENV_ALLOWLIST = ("PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "TMPDIR")
_NODE_READY_TIMEOUT_SECONDS = 15
_NODE_STDOUT_MAX_BYTES = 64 * 1024
_NODE_STDERR_MAX_BYTES = 32 * 1024
_NODE_READY_MAX_LINE_BYTES = 16 * 1024
_APP_READY_TIMEOUT_SECONDS = 20
_CLEANUP_TIMEOUT_SECONDS = 5
_APP_THREAD_FAILURE_EXIT_CODE = 86


def _node_environment(module_dir: Path, source_environment=None) -> dict[str, str]:
    """只向本地 OP 子进程传递必要系统项与固定模块搜索路径。"""
    source = os.environ if source_environment is None else source_environment
    normalized = {str(key).upper(): value for key, value in source.items()}
    environment = {
        name: str(normalized[name])
        for name in _NODE_SYSTEM_ENV_ALLOWLIST
        if normalized.get(name)
    }
    environment["NODE_PATH"] = str(module_dir)
    return environment


def _new_node_capture_state(max_bytes: int) -> dict:
    return {
        "max_bytes": max_bytes,
        "bytes_written": 0,
        "overflow": False,
        "ready_seen": False,
        "finished": threading.Event(),
        "lock": threading.Lock(),
        "error": None,
        "thread": None,
        "stream": None,
    }


def _capture_node_stream(stream, spool_path: Path, capture_state: dict) -> None:
    """持续排空 OP 输出；临时 spool 有硬字节上限，超出部分只丢弃。"""
    read_chunk = getattr(stream, "read1", None) or stream.read
    try:
        with spool_path.open("wb", buffering=0) as spool:
            while True:
                chunk = read_chunk(8192)
                if not chunk:
                    break
                with capture_state["lock"]:
                    if capture_state["ready_seen"]:
                        continue
                    remaining = capture_state["max_bytes"] - capture_state["bytes_written"]
                    if remaining > 0:
                        kept = chunk[:remaining]
                        spool.write(kept)
                        capture_state["bytes_written"] += len(kept)
                    if len(chunk) > max(remaining, 0):
                        capture_state["overflow"] = True
    except Exception as exc:
        with capture_state["lock"]:
            capture_state["error"] = exc
    finally:
        capture_state["finished"].set()


def _start_node_capture(stream, spool_path: Path, capture_state: dict, thread_name: str) -> None:
    thread = threading.Thread(
        target=_capture_node_stream,
        args=(stream, spool_path, capture_state),
        name=thread_name,
        daemon=False,
    )
    capture_state["stream"] = stream
    capture_state["thread"] = thread
    thread.start()


def _read_node_startup_line(process, stdout_path: Path, capture_state: dict, timeout_seconds: float) -> str:
    """有限轮询有界 stdout spool；ready 行不能由不受限 readline 阻塞。"""
    deadline = time.monotonic() + timeout_seconds
    while True:
        with capture_state["lock"]:
            overflow = capture_state["overflow"]
            capture_error = capture_state["error"]
        if overflow:
            raise RuntimeError(f"第三方 OP stdout 超过 {capture_state['max_bytes']} 字节上限")
        if capture_error is not None:
            raise RuntimeError(f"读取第三方 OP stdout 失败：{capture_error!r}") from capture_error

        try:
            data = stdout_path.read_bytes()
        except FileNotFoundError:
            data = b""
        if len(data) > capture_state["max_bytes"]:
            raise RuntimeError(f"第三方 OP stdout 超过 {capture_state['max_bytes']} 字节上限")
        line_end = data.find(b"\n")
        if line_end >= 0:
            if line_end > _NODE_READY_MAX_LINE_BYTES:
                raise RuntimeError("第三方 OP ready 行超过长度上限")
            try:
                line = data[:line_end].decode("utf-8")
            except UnicodeDecodeError as exc:
                raise RuntimeError("第三方 OP ready 行不是 UTF-8") from exc
            with capture_state["lock"]:
                overflow = capture_state["overflow"]
                capture_state["ready_seen"] = True
            if overflow:
                raise RuntimeError(f"第三方 OP stdout 超过 {capture_state['max_bytes']} 字节上限")
            returncode = process.poll()
            if returncode is not None:
                raise RuntimeError(f"第三方 OP 在 ready 行后提前退出（退出码 {returncode}）")
            return line
        if len(data) > _NODE_READY_MAX_LINE_BYTES:
            raise RuntimeError("第三方 OP ready 行超过长度上限")

        if capture_state["finished"].is_set():
            try:
                data = stdout_path.read_bytes()
            except FileNotFoundError:
                data = b""
            line_end = data.find(b"\n")
            if line_end >= 0:
                if line_end > _NODE_READY_MAX_LINE_BYTES:
                    raise RuntimeError("第三方 OP ready 行超过长度上限")
                try:
                    line = data[:line_end].decode("utf-8")
                except UnicodeDecodeError as exc:
                    raise RuntimeError("第三方 OP ready 行不是 UTF-8") from exc
                with capture_state["lock"]:
                    overflow = capture_state["overflow"]
                    capture_state["ready_seen"] = True
                if overflow:
                    raise RuntimeError(f"第三方 OP stdout 超过 {capture_state['max_bytes']} 字节上限")
                returncode = process.poll()
                if returncode is not None:
                    raise RuntimeError(f"第三方 OP 在 ready 行后提前退出（退出码 {returncode}）")
                return line
            returncode = process.poll()
            if returncode is not None:
                raise RuntimeError(f"第三方 OP 在 ready 前退出（退出码 {returncode}）")
            raise RuntimeError("第三方 OP stdout 已关闭但未输出 ready 行")
        returncode = process.poll()
        if returncode is not None and capture_state["finished"].is_set():
            raise RuntimeError(f"第三方 OP 在 ready 前退出（退出码 {returncode}）")
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError(f"第三方 OP 在 {timeout_seconds:g}s 内未输出 ready 行")
        time.sleep(min(0.02, remaining))


def _stop_owned_process(
    process,
    process_name: str,
    terminate_timeout_seconds: float = _CLEANUP_TIMEOUT_SECONDS,
    kill_timeout_seconds: float = _CLEANUP_TIMEOUT_SECONDS,
) -> None:
    """只终止、升级 kill 并回收本夹具持有的 Popen。"""
    if process.poll() is None:
        try:
            process.terminate()
        except OSError:
            if process.poll() is None:
                raise
        try:
            process.wait(timeout=terminate_timeout_seconds)
        except subprocess.TimeoutExpired:
            if process.poll() is None:
                process.kill()
            try:
                process.wait(timeout=kill_timeout_seconds)
            except subprocess.TimeoutExpired as exc:
                raise RuntimeError(f"{process_name} 在 terminate/kill 后仍未退出") from exc
    else:
        process.wait(timeout=terminate_timeout_seconds)
    if process.poll() is None:
        raise RuntimeError(f"{process_name} 回收后仍处于运行状态")


def _stop_node_resources_or_record(
    process,
    capture_states: list[dict],
    cleanup_state: dict,
    timeout_seconds: float = _CLEANUP_TIMEOUT_SECONDS,
) -> None:
    """回收本夹具 OP 与 stdout/stderr reader；活 reader 的管道不跨线程 close。"""
    unsafe = []
    capture_errors = []
    try:
        _stop_owned_process(process, "第三方 OP")
    except Exception as exc:
        unsafe.append(f"第三方 OP: {exc!r}")

    live_streams = set()
    for capture_state in capture_states:
        thread = capture_state.get("thread")
        if thread is not None and thread.ident is not None:
            try:
                thread.join(timeout_seconds)
            except Exception as exc:
                unsafe.append(f"OP 输出 reader join 失败：{exc!r}")
            if thread.is_alive():
                stream = capture_state.get("stream")
                if stream is not None:
                    live_streams.add(id(stream))
                unsafe.append(f"OP 输出 reader {thread.name} 在有限 join 后仍存活；保留其管道避免跨线程 close")
        with capture_state["lock"]:
            capture_error = capture_state["error"]
        if capture_error is not None:
            capture_errors.append(f"OP 输出 reader 异常：{capture_error!r}")

    streams = (getattr(process, "stdout", None), getattr(process, "stderr", None))
    for stream in streams:
        if stream is None or id(stream) in live_streams or getattr(stream, "closed", False):
            continue
        try:
            stream.close()
        except Exception as exc:
            unsafe.append(f"关闭 OP 输出管道失败：{exc!r}")

    if unsafe:
        cleanup_state["unsafe_cleanup"].extend(unsafe)
    if unsafe or capture_errors:
        details = "; ".join(unsafe + capture_errors)
        raise RuntimeError(f"第三方 OP 资源清理未完整确认：{details}")


def _test_runtime_environment(tmp_path: Path, issuer: str, redirect_uri: str) -> dict[str, str]:
    """为单个用例创建仓外、相互独立的测试数据根。"""
    runtime_root = (Path(tmp_path) / "gw-runtime").resolve()
    repo_root = REPO_ROOT.resolve()
    try:
        runtime_root.relative_to(repo_root)
    except ValueError:
        pass
    else:
        raise RuntimeError(f"pytest 临时运行根不得位于仓库内：{runtime_root}")

    data_dir = runtime_root / "data"
    auth_dir = runtime_root / "auth"
    video_dir = runtime_root / "video"
    for directory in (data_dir, auth_dir, video_dir):
        directory.mkdir(parents=True, exist_ok=True)
    return {
        "GW_RUNTIME_MODE": "test",
        "GW_DATA_DIR": str(data_dir),
        "GW_LOCAL_AUTH_DB": str(auth_dir / "auth.sqlite3"),
        "GW_VIDEO_DATA_DIR": str(video_dir),
        "GW_AUTH_MODE": "oidc",
        "GW_OIDC_ISSUER": issuer,
        "GW_OIDC_AUDIENCE": CLIENT_ID,
        "GW_OIDC_CLIENT_ID": CLIENT_ID,
        "GW_OIDC_REDIRECT_URI": redirect_uri,
        "GW_OIDC_GROUPS_CLAIM": "groups",
    }


def _load_config_module():
    from gw.core import config as gw_config

    return gw_config


def _load_create_app():
    from gw.api.app import create_app

    return create_app


def _configure_application_environment(
    tmp_path: Path,
    issuer: str,
    redirect_uri: str,
    cleanup_state: dict,
):
    """应用导入前隔离 GW 配置；环境只在应用线程确认退出后恢复。"""
    test_environment = _test_runtime_environment(tmp_path, issuer, redirect_uri)
    cleanup_state["original_environment"] = {
        key: value for key, value in os.environ.items() if key.upper().startswith("GW_")
    }
    for key in list(os.environ):
        if key.upper().startswith("GW_"):
            del os.environ[key]
    os.environ.update(test_environment)
    gw_config = _load_config_module()
    cleanup_state["gw_config"] = gw_config
    gw_config.reset_runtime_auth_config_cache()
    gw_config.reset_discovery_cache()
    return _load_create_app()


def _finish_application_environment(cleanup_state: dict) -> None:
    """仅在全部资源已回收后恢复环境；泄漏风险时硬失败当前 pytest 进程。"""
    unsafe_cleanup = cleanup_state["unsafe_cleanup"]
    if unsafe_cleanup:
        sys.stderr.write(
            "OIDC 夹具无法证明资源已回收；为防止活线程/子进程观察恢复后的 GW 配置，"
            f"硬退出当前 pytest 进程，退出码 {_APP_THREAD_FAILURE_EXIT_CODE}："
            + "; ".join(unsafe_cleanup)
            + "\n"
        )
        sys.stderr.flush()
        os._exit(_APP_THREAD_FAILURE_EXIT_CODE)

    original_environment = cleanup_state.get("original_environment")
    if original_environment is None:
        return
    try:
        for key in list(os.environ):
            if key.upper().startswith("GW_"):
                del os.environ[key]
        os.environ.update(original_environment)
    finally:
        gw_config = cleanup_state.get("gw_config")
        if gw_config is not None:
            gw_config.reset_runtime_auth_config_cache()
            gw_config.reset_discovery_cache()


def _wait_for_app_health(
    requests_session,
    app_base: str,
    app_thread: threading.Thread,
    timeout_seconds: float | None = None,
) -> None:
    """用 trust_env=False 的 session 等待 loopback 健康响应，含总截止时间。"""
    if timeout_seconds is None:
        timeout_seconds = _APP_READY_TIMEOUT_SECONDS
    deadline = time.monotonic() + timeout_seconds
    last_error = "尚无健康响应"
    while time.monotonic() < deadline:
        if not app_thread.is_alive():
            raise RuntimeError("被测应用线程在健康检查前退出")
        try:
            request_timeout = min(0.5, max(0.001, deadline - time.monotonic()))
            response = requests_session.get(app_base + "/healthz", timeout=request_timeout)
            if response.status_code == 200:
                return
            last_error = f"/healthz 返回 HTTP {response.status_code}"
        except Exception as exc:
            last_error = f"/healthz 请求失败：{exc}"
        time.sleep(min(0.1, max(0, deadline - time.monotonic())))
    raise TimeoutError(f"被测应用在 {timeout_seconds:g}s 内未健康就绪：{last_error}")


def _stop_app_thread(
    server,
    app_thread: threading.Thread,
    cleanup_state: dict,
    timeout_seconds: float = _CLEANUP_TIMEOUT_SECONDS,
) -> None:
    """请求 Uvicorn 退出并有限 join；失败由最后回调硬终止当前 worker。"""
    try:
        server.should_exit = True
    except Exception as exc:
        cleanup_state["unsafe_cleanup"].append(f"无法请求应用线程退出：{exc!r}")
    if app_thread.ident is None:
        return
    try:
        app_thread.join(timeout_seconds)
    except Exception as exc:
        cleanup_state["unsafe_cleanup"].append(f"无法 join 应用线程：{exc!r}")
    if app_thread.is_alive():
        cleanup_state["unsafe_cleanup"].append("应用线程在有限 join 后仍存活")


def _stderr_excerpt(stderr_path: Path) -> str:
    try:
        return stderr_path.read_text(encoding="utf-8", errors="replace")[-600:]
    except OSError:
        return ""


@pytest.fixture(scope="function")
def real_op(tmp_path):
    """按单用例生命周期启动真实第三方 OP；缺少 node 或包时维持原有 skip。"""
    node_executable = shutil.which("node")
    if node_executable is None:
        pytest.skip("未安装 node，跳过第三方 OP 互操作用例")
    node_executable = str(Path(node_executable).resolve())
    base = _provider_dir()
    if base is None:
        pytest.skip("未找到 oidc-provider 包（可设置 GW_OIDC_PROVIDER_MODULE_DIR）")

    # 必须在创建 OP 子进程之前校验固定版本。
    provider_version = _provider_version(base / "node_modules" / "oidc-provider" / "package.json")
    import requests  # 延迟导入：缺依赖时按 skip 处理
    import uvicorn

    workdir = Path(tmp_path) / "oidc-provider"
    workdir.mkdir(parents=True, exist_ok=True)
    script = workdir / "real_op.mjs"
    script.write_text(_OP_SOURCE, encoding="utf-8")

    app_port = _free_port()
    app_base = f"http://127.0.0.1:{app_port}"
    redirect_uri = app_base + "/api/asset-auth/callback"
    op_port = _free_port()
    stdout_path = workdir / "node.stdout.spool"
    stderr_path = workdir / "node.stderr.spool"
    cleanup_state = {"unsafe_cleanup": [], "original_environment": None, "gw_config": None}

    with ExitStack() as cleanup:
        # 最后执行：只有应用线程、OP 进程和 reader 线程都确认回收后才恢复 GW_*。
        cleanup.callback(_finish_application_environment, cleanup_state)
        node_states = []
        process = subprocess.Popen(
            [node_executable, str(script), str(op_port), redirect_uri, CLIENT_ID, ACCOUNT_ID, GROUPS],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=str(workdir),
            env=_node_environment(base / "node_modules"),
        )
        # 进程一旦创建就立即登记所有权清理，之后任何启动错误都会先杀进程、再 join reader。
        cleanup.callback(_stop_node_resources_or_record, process, node_states, cleanup_state)
        stdout_capture = _new_node_capture_state(_NODE_STDOUT_MAX_BYTES)
        stderr_capture = _new_node_capture_state(_NODE_STDERR_MAX_BYTES)
        node_states.extend((stdout_capture, stderr_capture))
        _start_node_capture(process.stdout, stdout_path, stdout_capture, "gw-oidc-node-stdout")
        _start_node_capture(process.stderr, stderr_path, stderr_capture, "gw-oidc-node-stderr")
        node_stderr = stderr_path

        try:
            line = _read_node_startup_line(process, stdout_path, stdout_capture, _NODE_READY_TIMEOUT_SECONDS)
        except (RuntimeError, TimeoutError) as exc:
            detail = _stderr_excerpt(node_stderr)
            detail_suffix = f"：{detail}" if detail else ""
            pytest.fail(f"第三方 OP 启动失败：{exc}{detail_suffix}")

        info = json.loads(line)
        if info.get("ready") is not True or not info.get("issuer"):
            raise RuntimeError("第三方 OP ready 响应缺少 ready=true 或 issuer")
        issuer = str(info["issuer"])

        create_app = _configure_application_environment(tmp_path, issuer, redirect_uri, cleanup_state)
        app = create_app()
        server = uvicorn.Server(
            uvicorn.Config(
                app,
                host="127.0.0.1",
                port=app_port,
                log_level="error",
                timeout_graceful_shutdown=1,
            )
        )
        app_thread = threading.Thread(target=server.run, name="gw-oidc-test-app", daemon=False)
        cleanup.callback(_stop_app_thread, server, app_thread, cleanup_state)
        app_thread.start()
        requests_session = requests.Session()
        requests_session.trust_env = False
        cleanup.callback(requests_session.close)
        _wait_for_app_health(requests_session, app_base, app_thread)

        yield {
            "issuer": issuer,
            "app_base": app_base,
            "redirect_uri": redirect_uri,
            "provider_version": provider_version,
            "requests": requests,
        }


def _drive_authorization(session, issuer: str, auth_url: str, redirect_uri: str) -> str:
    """按浏览器语义走完第三方 OP 的交互链，返回带 code+state 的回调 URL。"""
    response = session.get(auth_url, timeout=10, allow_redirects=False)
    assert response.status_code == 303, (response.status_code, response.text[:300])
    current = urljoin(issuer, response.headers["location"])
    method = "POST"
    for _ in range(12):
        if method == "GET":
            response = session.get(current, timeout=10, allow_redirects=False)
        else:
            response = session.post(current, timeout=10, allow_redirects=False, data={})
        assert response.status_code == 303, (response.status_code, response.text[:300])
        location = urljoin(issuer, response.headers["location"])
        if location.startswith(redirect_uri):
            return location
        current = location
        method = "POST" if "/interaction/" in urlparse(location).path else "GET"
    raise AssertionError("第三方 OP 授权流程未回到 redirect_uri")


def test_real_third_party_op_end_to_end_login(real_op):
    """真实第三方 OP：完整授权码 + PKCE 登录 → 会话 → 角色来自 IdP 组声明。"""
    requests = real_op["requests"]
    session = requests.Session()

    status_before = session.get(real_op["app_base"] + "/api/asset-auth/status", timeout=5).json()
    assert status_before["authenticated"] is False

    login = session.post(real_op["app_base"] + "/api/asset-auth/login", timeout=10)
    assert login.status_code == 200, login.text
    authorization_url = login.json()["authorization_url"]
    params = parse_qs(urlparse(authorization_url).query)
    assert params["response_type"] == ["code"]
    assert params["code_challenge_method"] == ["S256"]
    assert params["client_id"] == [CLIENT_ID]

    callback = _drive_authorization(session, real_op["issuer"], authorization_url, real_op["redirect_uri"])
    callback_query = parse_qs(urlparse(callback).query)
    assert callback_query.get("code"), callback
    assert callback_query.get("state"), callback

    finished = session.get(callback, timeout=15, allow_redirects=False)
    assert finished.status_code == 302, finished.text
    assert "auth_error" not in (finished.headers.get("location") or "")
    assert "gw_session" in session.cookies

    status_after = session.get(real_op["app_base"] + "/api/asset-auth/status", timeout=5).json()
    assert status_after["authenticated"] is True, status_after
    # 角色只能来自 IdP 的 groups 声明，不得来自任何请求头。
    assert status_after["principal"]["role"] == "editor", status_after


def test_real_third_party_op_rejects_tampered_pkce(real_op):
    """篡改 code_verifier 后，第三方 OP 必须拒绝换码，本仓必须失败关闭（不建会话）。"""
    from gw.core import session as session_store

    requests = real_op["requests"]
    session = requests.Session()
    login = session.post(real_op["app_base"] + "/api/asset-auth/login", timeout=10).json()
    callback = _drive_authorization(session, real_op["issuer"], login["authorization_url"], real_op["redirect_uri"])
    state = parse_qs(urlparse(callback).query)["state"][0]
    flow = session_store.pop_flow_state(state)
    assert flow is not None
    session_store.create_flow_state(
        state=state,
        nonce=flow.nonce,
        code_verifier="A" * 64,
        redirect_uri=flow.redirect_uri,
    )

    response = session.get(callback, timeout=15, allow_redirects=False)
    assert response.status_code == 302
    auth_error = parse_qs(urlparse(response.headers.get("location") or "").query).get("auth_error")
    assert auth_error == ["token_exchange_failed"], auth_error
    assert "gw_session" not in session.cookies


def test_real_third_party_op_rejects_wrong_nonce(real_op):
    """nonce 不符时：第三方 OP 签发的合法 id_token 也必须被本仓拒绝。"""
    from gw.core import session as session_store

    requests = real_op["requests"]
    session = requests.Session()
    login = session.post(real_op["app_base"] + "/api/asset-auth/login", timeout=10).json()
    callback = _drive_authorization(session, real_op["issuer"], login["authorization_url"], real_op["redirect_uri"])
    state = parse_qs(urlparse(callback).query)["state"][0]
    flow = session_store.pop_flow_state(state)
    assert flow is not None
    session_store.create_flow_state(
        state=state,
        nonce="not-the-issued-nonce",
        code_verifier=flow.code_verifier,
        redirect_uri=flow.redirect_uri,
    )

    response = session.get(callback, timeout=15, allow_redirects=False)
    assert response.status_code == 302
    auth_error = parse_qs(urlparse(response.headers.get("location") or "").query).get("auth_error")
    assert auth_error == ["id_token_rejected"], auth_error
    assert "gw_session" not in session.cookies
