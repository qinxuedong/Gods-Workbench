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

"""数据位置迁移写窗口（QIN-627）。

本模块只做一件事：在「迁移开始 → 切换完成 → 重启」这一段时间里，保证
任一时刻只有一个可写根。它提供五层拦截的共同底座：

- L1 HTTP：:class:`WriteWindowMiddleware` 统一拦截非幂等方法；
- L2 JSON：``gw.core.storage.save`` 与项目/工作流直写模块调用 :func:`guard_write`；
- L3 SQLite：:func:`open_sqlite` 在窗口关闭时以只读 URI 打开连接；
- L4 文件缓存：派生缓存写入前查询 :func:`cache_writes_allowed`；
- L5 后台任务：:meth:`WriteWindow.register_host` 登记活动计数与停播回调。

状态机单向推进，任何异常都收敛回 ``open`` 或停在 ``read_only``，不存在
「两个可写根」。本模块不导入 FastAPI、不导入 storage，避免循环依赖。
"""

from __future__ import annotations

import contextlib
import json
import re
import sqlite3
import threading
import time
import uuid
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

STATE_OPEN = "open"
STATE_QUIESCING = "quiescing"
STATE_FROZEN = "frozen"
STATE_SWITCHING = "switching"
STATE_READ_ONLY = "read_only"

# 迁移自身状态端点：这些写操作在非 open 状态下仍需放行，否则窗口会自锁。
_DATA_LOCATION_ROLLBACK = "/api/data-location/rollback"
_DATA_LOCATION_MIGRATION_START = "/api/data-location/migrations"
_CANCEL_PATTERN = re.compile(r"^/api/data-location/migrations/[A-Za-z0-9_-]+/cancel$")
_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


class WriteWindowClosed(RuntimeError):
    """写窗口已关闭；调用方应把它映射为 503 而不是 500。"""

    def __init__(self, code: str, message: str, *, retry_after: int | None = None) -> None:
        self.code = code
        self.message = message
        self.retry_after = retry_after
        super().__init__(message)

    def as_detail(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.message}


class WriteWindowStateError(RuntimeError):
    """状态机推进不合法（例如在只读状态重新开始迁移）。"""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(message)


class WriteWindow:
    """进程内唯一的写窗口状态机 + 在飞写计数 + 后台宿主注册表。"""

    def __init__(self) -> None:
        self._condition = threading.Condition(threading.RLock())
        self._state = STATE_OPEN
        self._generation = 0
        self._freeze_token: str | None = None
        self._inflight = 0
        self._hosts: dict[str, dict[str, Any]] = {}
        self._last_reason = ""

    # -- 只读属性 ---------------------------------------------------------

    @property
    def state(self) -> str:
        with self._condition:
            return self._state

    @property
    def generation(self) -> int:
        with self._condition:
            return self._generation

    @property
    def freeze_token(self) -> str | None:
        with self._condition:
            return self._freeze_token

    @property
    def inflight(self) -> int:
        with self._condition:
            return self._inflight

    @property
    def last_reason(self) -> str:
        with self._condition:
            return self._last_reason

    def snapshot(self) -> dict[str, Any]:
        with self._condition:
            return {
                "state": self._state,
                "generation": self._generation,
                "freeze_token": self._freeze_token,
                "inflight": self._inflight,
                "read_only": self._state != STATE_OPEN,
                "reason": self._last_reason,
            }

    def writes_blocked(self) -> bool:
        return self.state != STATE_OPEN

    # -- 后台宿主注册表（L5） --------------------------------------------

    def register_host(
        self,
        name: str,
        active_count: Callable[[], int],
        *,
        quiesce: Callable[[], None] | None = None,
    ) -> None:
        with self._condition:
            self._hosts[name] = {
                "active_count": active_count,
                "quiesce": quiesce,
            }

    def unregister_host(self, name: str) -> None:
        with self._condition:
            self._hosts.pop(name, None)

    def registered_hosts(self) -> list[str]:
        with self._condition:
            return sorted(self._hosts)

    def active_jobs(self) -> dict[str, int]:
        with self._condition:
            hosts = list(self._hosts.items())
        result: dict[str, int] = {}
        for name, spec in hosts:
            try:
                count = int(spec["active_count"]())
            except Exception:  # pragma: no cover - 计数异常不阻断窗口判定
                count = 0
            if count > 0:
                result[name] = count
        return result

    def total_active(self) -> int:
        return sum(self.active_jobs().values())

    # -- 状态推进 ---------------------------------------------------------

    def begin_quiescing(self, *, reason: str = "") -> str:
        """进入静默阶段；要求当前为 open，返回 freeze_token。"""
        with self._condition:
            if self._state != STATE_OPEN:
                raise WriteWindowStateError(
                    "WRITE_WINDOW_BUSY", "已有迁移或只读窗口在进行中，不能重复开始"
                )
            self._generation += 1
            self._freeze_token = uuid.uuid4().hex
            self._state = STATE_QUIESCING
            self._last_reason = reason or "migration"
            hosts = list(self._hosts.items())
            token = self._freeze_token
        failures: list[str] = []
        for name, spec in hosts:
            quiesce = spec.get("quiesce")
            if quiesce is None:
                continue
            try:
                quiesce()
            except Exception as exc:  # 宿主取消失败必须 abort，不能带着活动任务复制
                failures.append(f"{name}:{type(exc).__name__}")
        if failures:
            self.reset_open(reason="host_quiesce_failed")
            raise WriteWindowStateError(
                "HOST_QUIESCE_FAILED", "后台任务停播失败：" + ", ".join(failures)
            )
        return token

    def wait_drained(self, *, timeout: float = 5.0, interval: float = 0.1) -> bool:
        """等待在飞写归零；超时返回 False，由调用方决定失败并回 open。"""
        deadline = time.monotonic() + max(timeout, 0.0)
        with self._condition:
            while self._inflight > 0:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                self._condition.wait(timeout=min(interval, remaining))
            return True

    def mark_frozen(self) -> None:
        self._transition(STATE_QUIESCING, STATE_FROZEN)

    def mark_switching(self) -> None:
        self._transition(STATE_FROZEN, STATE_SWITCHING)

    def mark_read_only(self, *, reason: str = "") -> None:
        self._transition(STATE_SWITCHING, STATE_READ_ONLY, reason=reason)

    def reset_open(self, *, reason: str = "") -> None:
        with self._condition:
            self._state = STATE_OPEN
            self._freeze_token = None
            self._last_reason = reason
            self._condition.notify_all()

    def _transition(self, expected: str, target: str, *, reason: str = "") -> None:
        with self._condition:
            if self._state != expected:
                raise WriteWindowStateError(
                    "WRITE_WINDOW_STATE_INVALID",
                    f"写窗口状态必须为 {expected} 才能进入 {target}，当前为 {self._state}",
                )
            self._state = target
            if target == STATE_READ_ONLY:
                self._freeze_token = None
            if reason:
                self._last_reason = reason
            self._condition.notify_all()

    # -- 写入守卫 ---------------------------------------------------------

    def guard_write(self) -> None:
        """非 open 状态一律拒绝；错误码按阶段区分。"""
        with self._condition:
            state = self._state
        if state == STATE_OPEN:
            return
        if state == STATE_QUIESCING:
            raise WriteWindowClosed(
                "DATA_LOCATION_QUIESCING", "数据位置迁移准备中，写操作已暂停", retry_after=2
            )
        raise WriteWindowClosed(
            "DATA_LOCATION_RESTART_REQUIRED",
            "数据位置已切换，重启后生效；当前为只读状态",
        )

    @contextlib.contextmanager
    def request_write(self) -> Iterator[None]:
        """登记一次 HTTP 写请求的存活期，供排空逻辑等待。"""
        self.guard_write()
        with self._condition:
            self._inflight += 1
        try:
            yield
        finally:
            with self._condition:
                self._inflight = max(0, self._inflight - 1)
                self._condition.notify_all()


_WINDOW = WriteWindow()


def get_write_window() -> WriteWindow:
    return _WINDOW


def reset_write_window() -> WriteWindow:
    """测试辅助：把进程内单例重置为 open。"""
    global _WINDOW
    _WINDOW = WriteWindow()
    return _WINDOW


def guard_write() -> None:
    _WINDOW.guard_write()


def cache_writes_allowed() -> bool:
    return _WINDOW.state == STATE_OPEN


def writes_blocked() -> bool:
    return _WINDOW.writes_blocked()


def open_sqlite(path: str | Path, *, write: bool = True, **kwargs: Any) -> sqlite3.Connection:
    """统一的 SQLite 连接工厂。

    窗口关闭时写连接退化为 ``mode=ro`` + ``PRAGMA query_only=1``；此时任何写语句
    都会抛出 ``sqlite3.OperationalError``，由上层按 503 处理。读连接行为不变。
    """
    if isinstance(path, str) and path == ":memory:":
        return sqlite3.connect(path, **kwargs)
    blocked = write and _WINDOW.writes_blocked()
    if not blocked:
        return sqlite3.connect(path, **kwargs)
    absolute = Path(path)
    if not absolute.is_absolute():
        raise ValueError("SQLite 数据库路径必须是绝对路径")
    uri = absolute.as_uri() + "?mode=ro"
    connection = sqlite3.connect(uri, uri=True, **kwargs)
    try:
        connection.execute("PRAGMA query_only=1")
    except sqlite3.Error:  # pragma: no cover - 只读连接极少数驱动不支持
        connection.close()
        raise
    return connection


# ---------------------------------------------------------------------------
# L1：ASGI 中间件
# ---------------------------------------------------------------------------


def _blocked_response(state: str) -> tuple[str, str, int | None]:
    if state == STATE_QUIESCING:
        return (
            "DATA_LOCATION_QUIESCING",
            "数据位置迁移准备中，写操作已暂停，请稍后重试",
            2,
        )
    return (
        "DATA_LOCATION_RESTART_REQUIRED",
        "数据位置已切换，重启后生效；当前为只读状态",
        None,
    )


def decide_write(method: str, path: str, state: str) -> tuple[str, str, int | None] | None:
    """返回 ``None`` 表示放行；否则返回 503 错误三元组。"""
    method = method.upper()
    if method in _SAFE_METHODS:
        return None
    if state == STATE_OPEN:
        return None
    if method == "POST" and path == _DATA_LOCATION_MIGRATION_START:
        return _blocked_response(state)
    if method == "POST" and _CANCEL_PATTERN.match(path):
        if state in (STATE_QUIESCING, STATE_FROZEN, STATE_SWITCHING):
            return None
    if method == "POST" and path == _DATA_LOCATION_ROLLBACK and state == STATE_READ_ONLY:
        return None
    return _blocked_response(state)


class WriteWindowMiddleware:
    """纯 ASGI 中间件：非 open 状态下统一拒绝写方法，并对在飞写计数。"""

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return
        method = str(scope.get("method") or "GET").upper()
        path = str(scope.get("path") or "")
        window = get_write_window()
        state = window.state
        decision = decide_write(method, path, state)
        if decision is None:
            if state == STATE_OPEN and method not in _SAFE_METHODS:
                with window.request_write():
                    await self.app(scope, receive, send)
            else:
                await self.app(scope, receive, send)
            return
        code, message, retry_after = decision
        payload = json.dumps(
            {"detail": {"code": code, "message": message}}, ensure_ascii=False
        ).encode("utf-8")
        headers = [
            (b"content-type", b"application/json; charset=utf-8"),
            (b"content-length", str(len(payload)).encode("ascii")),
        ]
        if retry_after is not None:
            headers.append((b"retry-after", str(retry_after).encode("ascii")))
        await send(
            {
                "type": "http.response.start",
                "status": 503,
                "headers": headers,
            }
        )
        await send({"type": "http.response.body", "body": payload})


def install_write_window_middleware(app: Any) -> None:
    """把写窗口中间件包到最外层；重复调用保持幂等。"""
    if getattr(app.state, "write_window_middleware_installed", False):
        return
    app.add_middleware(WriteWindowMiddleware)
    app.state.write_window_middleware_installed = True


__all__ = [
    "STATE_FROZEN",
    "STATE_OPEN",
    "STATE_QUIESCING",
    "STATE_READ_ONLY",
    "STATE_SWITCHING",
    "WriteWindow",
    "WriteWindowClosed",
    "WriteWindowMiddleware",
    "WriteWindowStateError",
    "cache_writes_allowed",
    "decide_write",
    "get_write_window",
    "guard_write",
    "install_write_window_middleware",
    "open_sqlite",
    "reset_write_window",
    "writes_blocked",
]
