# -*- coding: utf-8 -*-
# Copyright 2026 Gods-Workbench Authors
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Dreamina 登录闭环的单进程受控子进程管理器。"""

from __future__ import annotations

import os
import re
import subprocess
import threading
import time
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Sequence, Tuple
from urllib.parse import urlsplit

from gw.core.errors import CleanroomException

MAX_OUTPUT_BYTES = 64 * 1024
LOGIN_TIMEOUT_SECONDS = 5 * 60
SHORT_COMMAND_TIMEOUT_SECONDS = 30
VERIFICATION_HOSTS_ENV = "GW_DREAMINA_VERIFICATION_HOSTS"

_FIELD_PATTERNS = {
    "verification_uri": re.compile(r"(?im)^\s*verification_uri\s*[:=]\s*(\S+)\s*$"),
    "user_code": re.compile(r"(?im)^\s*user_code\s*[:=]\s*(\S+)\s*$"),
}
_USER_CODE = re.compile(r"^[A-Za-z0-9-]{4,64}$")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _raise_cli_error(
    status_code: int,
    code: str,
    message: str,
    endpoint: str,
    *,
    result_unknown: Optional[bool] = None,
    returncode: Optional[int] = None,
    data_status: str = "failed",
) -> None:
    extra: Dict[str, Any] = {"endpoint": endpoint, "data_status": data_status}
    exposed = {"endpoint", "data_status"}
    if result_unknown is not None:
        extra["result_unknown"] = result_unknown
        exposed.add("result_unknown")
    if returncode is not None:
        extra["returncode"] = returncode
        exposed.add("returncode")
    raise CleanroomException(status_code, code, message, extra=extra, expose_extra_fields=exposed)


def _popen_options() -> Dict[str, Any]:
    """固定子进程 I/O 与平台选项，不经过 shell，也不弹出 Windows 控制台。"""
    options: Dict[str, Any] = {
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.STDOUT,
        "shell": False,
        "bufsize": 0,
        "close_fds": True,
    }
    if os.name == "nt":
        options["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    return options


def _terminate_owned(process: subprocess.Popen) -> None:
    """只终止本控制器持有的 Popen 实例，并等待其回收。"""
    try:
        if process.poll() is None:
            process.terminate()
    except (OSError, ProcessLookupError):
        pass
    try:
        process.wait(timeout=1.0)
        return
    except (subprocess.TimeoutExpired, OSError):
        pass
    try:
        if process.poll() is None:
            process.kill()
    except (OSError, ProcessLookupError):
        pass
    try:
        process.wait(timeout=2.0)
    except (subprocess.TimeoutExpired, OSError):
        pass


def run_limited_process(
    process: subprocess.Popen,
    timeout_seconds: float,
    max_bytes: int = MAX_OUTPUT_BYTES,
) -> Tuple[Optional[int], bytes, Optional[str]]:
    """按实际读取字节限制输出，并主动执行超时/超量进程回收。"""
    output = bytearray()
    output_lock = threading.Lock()
    stopped = threading.Event()
    overflow = threading.Event()
    read_failed = threading.Event()

    def drain() -> None:
        try:
            stream = process.stdout
            if stream is None:
                read_failed.set()
                return
            while not stopped.is_set():
                with output_lock:
                    remaining = max_bytes - len(output)
                # 多读一个字节即可证明越限，不会把无限输出读入内存。
                chunk = stream.read(min(4096, max(1, remaining + 1)))
                if not chunk:
                    break
                with output_lock:
                    if len(output) + len(chunk) > max_bytes:
                        overflow.set()
                        break
                    output.extend(chunk)
        except (OSError, ValueError):
            if not stopped.is_set():
                read_failed.set()

    reader = threading.Thread(target=drain, name="gw-cli-output-reader", daemon=True)
    reader.start()
    deadline = time.monotonic() + max(0.0, timeout_seconds)
    reason: Optional[str] = None
    returncode: Optional[int] = None
    while True:
        if overflow.is_set():
            reason = "output_limit"
            break
        if read_failed.is_set():
            reason = "read_failed"
            break
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            reason = "timeout"
            break
        try:
            returncode = process.wait(timeout=min(0.05, remaining))
            break
        except subprocess.TimeoutExpired:
            continue
        except OSError:
            reason = "wait_failed"
            break

    if reason is not None:
        _terminate_owned(process)
    else:
        try:
            returncode = process.wait(timeout=0)
        except (subprocess.TimeoutExpired, OSError):
            _terminate_owned(process)
            reason = "wait_failed"

    # 子进程已回收后管道通常立即 EOF；不等待锁或无界 join。
    stopped.set()
    reader.join(timeout=0.5)
    if reader.is_alive() and process.stdout is not None:
        try:
            process.stdout.close()
        except (OSError, ValueError):
            pass
        reader.join(timeout=0.2)
    # 子进程可能先退出、读取线程稍后才发现超量；终态后再核验一次竞态标记。
    if reason is None and overflow.is_set():
        reason = "output_limit"
    if reason is None and read_failed.is_set():
        reason = "read_failed"
    with output_lock:
        safe_output = bytes(output) if reason is None else b""
        output.clear()
    return returncode, safe_output, reason


def _allowed_hosts() -> set[str]:
    """仅接受部署方显式配置的 Dreamina 授权页主机。"""
    raw = os.environ.get(VERIFICATION_HOSTS_ENV, "")
    return {part.strip().rstrip(".").lower() for part in raw.split(",") if part.strip()}


def _validated_verification_uri(candidate: str) -> Optional[str]:
    if not candidate or len(candidate) > 2048 or any(ord(char) < 0x21 or ord(char) > 0x7E for char in candidate):
        return None
    if "\\" in candidate:
        return None
    try:
        parsed = urlsplit(candidate)
        port = parsed.port
    except ValueError:
        return None
    host = (parsed.hostname or "").rstrip(".").lower()
    if (
        parsed.scheme.lower() != "https"
        or not host
        or host not in _allowed_hosts()
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or port not in (None, 443)
        or (parsed.path and not parsed.path.startswith("/"))
        or "?" in candidate
        or "#" in candidate
    ):
        return None
    return candidate


def parse_authorization_material(output: bytes) -> Tuple[Optional[str], Optional[str]]:
    """只解析输出中明确命名的 verification_uri 与 user_code 字段。"""
    text = output.decode("utf-8", errors="replace")
    uri_match = _FIELD_PATTERNS["verification_uri"].search(text)
    code_match = _FIELD_PATTERNS["user_code"].search(text)
    verification_uri = _validated_verification_uri(uri_match.group(1)) if uri_match else None
    user_code = code_match.group(1).strip() if code_match else None
    if not user_code or not _USER_CODE.fullmatch(user_code):
        user_code = None
    # 只有 URI 与用户代码均通过校验才显示授权材料，避免部分/混淆输出被误导成登录入口。
    if verification_uri is None or user_code is None:
        return None, None
    return verification_uri, user_code


class LoginController:
    """在单服务进程内串行管理 Dreamina 登录/登出/余额命令。"""

    def __init__(self, login_timeout_seconds: float = LOGIN_TIMEOUT_SECONDS):
        self._lock = threading.RLock()
        self._login_timeout_seconds = login_timeout_seconds
        self._generation = 0
        self._closed = False
        self._owner: Optional[str] = None
        self._process: Optional[subprocess.Popen] = None
        self._timer: Optional[threading.Timer] = None
        self._termination_reason: Optional[str] = None
        self._state: Dict[str, Any] = self._unknown_state()

    @staticmethod
    def _unknown_state() -> Dict[str, Any]:
        return {
            "protocol": "jimeng",
            "running": False,
            "logged_in": None,
            "result_unknown": True,
            "last_operation_failed": False,
            "last_operation": None,
            "state": "unknown",
            "source": "none",
            "observed_at": None,
            "started_at": None,
            "returncode": None,
            "verification_uri": None,
            "user_code": None,
            "output_seen": False,
            "data_status": "not_integrated",
            "data_gaps": [],
        }

    def _snapshot_locked(self) -> Dict[str, Any]:
        data = dict(self._state)
        if not data.get("running"):
            data["verification_uri"] = None
            data["user_code"] = None
        return data

    def _check_owner_locked(self, actor_key: str, endpoint: str) -> None:
        if self._owner is not None and self._owner != actor_key:
            _raise_cli_error(403, "CLI_SUBJECT_MISMATCH", "该本机 CLI 操作仅对发起主体可见。", endpoint,
                             data_status="failed")

    def status(self, actor_key: str, endpoint: str = "/api/jimeng/login/status") -> Dict[str, Any]:
        with self._lock:
            self._check_owner_locked(actor_key, endpoint)
            return self._snapshot_locked()

    def _begin_locked(self, actor_key: str, operation: str, endpoint: str, idempotent_login: bool = False) -> Optional[int]:
        if self._closed:
            _raise_cli_error(503, "CLI_CONTROLLER_CLOSED", "CLI 控制器已关闭，未启动外部进程。", endpoint,
                             data_status="not_integrated")
        if self._state.get("running"):
            if actor_key != self._owner:
                _raise_cli_error(409, "CLI_OPERATION_IN_PROGRESS", "其他主体正在操作本机共享 CLI 账户。", endpoint,
                                 data_status="degraded", result_unknown=True)
            if idempotent_login and self._state.get("last_operation") == operation:
                return None
            _raise_cli_error(409, "CLI_OPERATION_IN_PROGRESS", "本机 CLI 账户已有操作正在进行。", endpoint,
                             data_status="degraded", result_unknown=True)
        self._generation += 1
        generation = self._generation
        self._owner = actor_key
        self._process = None
        self._timer = None
        self._termination_reason = None
        self._state = {
            "protocol": "jimeng",
            "running": True,
            "logged_in": None,
            "result_unknown": True,
            "last_operation_failed": False,
            "last_operation": operation,
            "state": "starting",
            "source": "last_operation",
            "observed_at": None,
            "started_at": _now_iso(),
            "returncode": None,
            "verification_uri": None,
            "user_code": None,
            "output_seen": False,
            "data_status": "degraded",
            "data_gaps": [],
        }
        return generation

    def _publish_material(self, generation: int, process: subprocess.Popen, output: bytes) -> None:
        uri, user_code = parse_authorization_material(output)
        with self._lock:
            if (
                generation == self._generation
                and process is self._process
                and self._state.get("running")
                and self._termination_reason is None
            ):
                self._state["output_seen"] = bool(output)
                if uri is not None and user_code is not None:
                    self._state["verification_uri"] = uri
                    self._state["user_code"] = user_code
                    self._state["state"] = "awaiting_authorization"
                    self._state["data_status"] = "ok"
                elif output and not self._state.get("verification_uri"):
                    self._state["state"] = "unrecognized_output"
                    self._state["data_status"] = "degraded"

    def _set_termination(self, generation: int, process: subprocess.Popen, reason: str) -> bool:
        with self._lock:
            if generation != self._generation or process is not self._process or not self._state.get("running"):
                return False
            if self._termination_reason is None:
                self._termination_reason = reason
                self._state["verification_uri"] = None
                self._state["user_code"] = None
                self._state["logged_in"] = None
                self._state["result_unknown"] = True
                self._state["state"] = "output_limit_exceeded" if reason == "output_limit" else "result_unknown"
                self._state["data_status"] = "degraded"
            return True

    def _on_login_timeout(self, generation: int, process: subprocess.Popen) -> None:
        if self._set_termination(generation, process, "timeout"):
            _terminate_owned(process)

    def _finalize(self, generation: int, process: subprocess.Popen, returncode: Optional[int]) -> None:
        with self._lock:
            if generation != self._generation or process is not self._process:
                return
            reason = self._termination_reason or ("shutdown" if self._closed else None)
            operation = str(self._state.get("last_operation") or "")
            timer = self._timer
            self._timer = None
            self._process = None
            self._termination_reason = None
            self._state["running"] = False
            self._state["verification_uri"] = None
            self._state["user_code"] = None
            self._state["returncode"] = returncode
            self._state["observed_at"] = _now_iso()
            self._state["source"] = "last_operation"
            if timer is not None:
                timer.cancel()
            if reason is not None:
                self._state["logged_in"] = None
                self._state["result_unknown"] = True
                self._state["last_operation_failed"] = True
                self._state["state"] = "output_limit_exceeded" if reason == "output_limit" else "result_unknown"
                self._state["data_status"] = "degraded"
            elif returncode == 0 and operation == "login":
                self._state["logged_in"] = True
                self._state["result_unknown"] = False
                self._state["last_operation_failed"] = False
                self._state["state"] = "logged_in"
                self._state["data_status"] = "ok"
            elif returncode == 0 and operation == "logout":
                self._state["logged_in"] = False
                self._state["result_unknown"] = False
                self._state["last_operation_failed"] = False
                self._state["state"] = "logged_out"
                self._state["data_status"] = "ok"
            elif returncode == 0:
                self._state["logged_in"] = None
                self._state["result_unknown"] = False
                self._state["last_operation_failed"] = False
                self._state["state"] = "unknown"
                self._state["data_status"] = "ok"
            else:
                # 非零 login/logout/credit 只表示本次失败，不能推断本机之前已登出。
                self._state["logged_in"] = None
                self._state["result_unknown"] = True
                self._state["last_operation_failed"] = True
                self._state["state"] = "operation_failed"
                self._state["data_status"] = "failed"

    def _record_start_failure(self, generation: int, operation: str) -> None:
        with self._lock:
            if generation != self._generation:
                return
            self._state.update({
                "running": False,
                "logged_in": None,
                "result_unknown": False,
                "last_operation_failed": True,
                "last_operation": operation,
                "state": "operation_failed",
                "source": "last_operation",
                "observed_at": _now_iso(),
                "returncode": None,
                "verification_uri": None,
                "user_code": None,
                "data_status": "failed",
            })
            self._process = None
            self._timer = None
            self._termination_reason = None

    def _attach_process(self, generation: int, process: subprocess.Popen) -> bool:
        with self._lock:
            if self._closed or generation != self._generation or not self._state.get("running"):
                valid = False
            else:
                valid = True
                self._process = process
                self._state["state"] = "running"
        if not valid:
            _terminate_owned(process)
            return False
        return True

    def start_login(self, actor_key: str, executable: str, endpoint: str = "/api/jimeng/login/start") -> Dict[str, Any]:
        with self._lock:
            generation = self._begin_locked(actor_key, "login", endpoint, idempotent_login=True)
            if generation is None:
                return self._snapshot_locked()
        try:
            process = subprocess.Popen([executable, "login"], **_popen_options())
        except OSError:
            self._record_start_failure(generation, "login")
            _raise_cli_error(503, "CLI_START_FAILED", "Dreamina CLI 无法启动，未回显本机输出。", endpoint,
                             result_unknown=False)
        if not self._attach_process(generation, process):
            self._record_start_failure(generation, "login")
            _raise_cli_error(503, "CLI_CONTROLLER_CLOSED", "CLI 控制器已关闭，子进程已回收。", endpoint,
                             data_status="not_integrated")
        timer = threading.Timer(self._login_timeout_seconds, self._on_login_timeout, args=(generation, process))
        timer.daemon = True
        worker = threading.Thread(target=self._drain_login, args=(generation, process),
                                  name="gw-dreamina-login-%d" % generation, daemon=True)
        with self._lock:
            if generation == self._generation and process is self._process and self._state.get("running"):
                self._timer = timer
                timer.start()
                worker.start()
            else:
                _terminate_owned(process)
                self._record_start_failure(generation, "login")
        return self.status(actor_key, endpoint)

    def _drain_login(self, generation: int, process: subprocess.Popen) -> None:
        output = bytearray()
        try:
            stream = process.stdout
            if stream is None:
                self._set_termination(generation, process, "read_failed")
            else:
                while True:
                    remaining = MAX_OUTPUT_BYTES - len(output)
                    chunk = stream.read(min(4096, max(1, remaining + 1)))
                    if not chunk:
                        break
                    if len(output) + len(chunk) > MAX_OUTPUT_BYTES:
                        self._set_termination(generation, process, "output_limit")
                        _terminate_owned(process)
                        break
                    output.extend(chunk)
                    self._publish_material(generation, process, bytes(output))
            try:
                returncode = process.wait()
            except OSError:
                returncode = None
            self._finalize(generation, process, returncode)
        except (OSError, ValueError):
            if self._set_termination(generation, process, "read_failed"):
                _terminate_owned(process)
            try:
                returncode = process.wait(timeout=2.0)
            except (subprocess.TimeoutExpired, OSError):
                _terminate_owned(process)
                returncode = process.poll()
            self._finalize(generation, process, returncode)
        finally:
            output.clear()

    def execute_short(
        self,
        actor_key: str,
        executable: str,
        operation: str,
        argv: Sequence[str],
        endpoint: str,
        timeout_seconds: float = SHORT_COMMAND_TIMEOUT_SECONDS,
    ) -> Tuple[Dict[str, Any], bytes]:
        """用相同代次/独占控制器执行余额或登出短命令。"""
        with self._lock:
            generation = self._begin_locked(actor_key, operation, endpoint)
        try:
            process = subprocess.Popen([executable, *argv], **_popen_options())
        except OSError:
            self._record_start_failure(generation, operation)
            _raise_cli_error(503, "CLI_START_FAILED", "Dreamina CLI 无法启动，未回显本机输出。", endpoint,
                             result_unknown=False)
        if not self._attach_process(generation, process):
            self._record_start_failure(generation, operation)
            _raise_cli_error(503, "CLI_CONTROLLER_CLOSED", "CLI 控制器已关闭，子进程已回收。", endpoint,
                             data_status="not_integrated")
        returncode, output, reason = run_limited_process(process, timeout_seconds)
        if reason is not None:
            # 登出操作超时/超量不证明账户退出；余额调用亦不伪造完整结果。
            self._set_termination(generation, process, reason)
        self._finalize(generation, process, returncode)
        state = self.status(actor_key, endpoint)
        if reason is not None:
            code = "CLI_COMMAND_TIMEOUT" if reason == "timeout" else "CLI_OUTPUT_LIMIT_EXCEEDED"
            _raise_cli_error(503, code, "CLI 操作结果未知；未自动重试且未回显本机输出。", endpoint,
                             result_unknown=True, returncode=returncode, data_status="degraded")
        if returncode != 0:
            _raise_cli_error(503, "CLI_COMMAND_FAILED", "CLI 以非零状态退出，不能据此推断登录状态。", endpoint,
                             result_unknown=True, returncode=returncode, data_status="failed")
        return state, output

    def shutdown(self) -> None:
        """应用进程退出时回收当前控制器确实持有的子进程。"""
        with self._lock:
            self._closed = True
            process = self._process
            timer = self._timer
            self._timer = None
            if self._state.get("running"):
                self._termination_reason = "shutdown"
                self._state.update({
                    "running": False,
                    "logged_in": None,
                    "result_unknown": True,
                    "last_operation_failed": True,
                    "state": "result_unknown",
                    "verification_uri": None,
                    "user_code": None,
                    "data_status": "degraded",
                    "observed_at": _now_iso(),
                })
        if timer is not None:
            timer.cancel()
        if process is not None:
            _terminate_owned(process)
        with self._lock:
            if self._process is process:
                self._process = None
                self._termination_reason = None
