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

"""工作区执行上下文传播（QIN-628）。

真实项目/索引写入不能只检查工作区设置页的状态。本模块把「当前客户端 +
当前工作区 + 执行端映射 + 结果落点」随请求和后台任务传播，并提供基于该
落点的 JSON 状态容器，使写入落点与用户看到的执行端一致。
"""

from __future__ import annotations

import json
import os
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar, Token
from pathlib import Path
from typing import Any

from gw.core import storage
from gw.core.errors import CleanroomException

CLIENT_COOKIE_NAME = "gw_workspace_client"
CLIENT_HEADER_NAME = "x-gw-workspace-client"

_client_instance_id: ContextVar[str | None] = ContextVar(
    "gw_workspace_client_instance_id", default=None
)
_execution_context: ContextVar[dict[str, Any] | None] = ContextVar(
    "gw_workspace_execution_context", default=None
)
_ROOT_LOCKS_GUARD = threading.Lock()
_ROOT_LOCKS: dict[tuple[str, str], threading.RLock] = {}


def set_client_instance_id(value: str | None) -> Token[str | None]:
    return _client_instance_id.set(value or None)


def reset_client_instance_id(token: Token[str | None]) -> None:
    try:
        _client_instance_id.reset(token)
    except (ValueError, LookupError):
        _client_instance_id.set(None)


def current_client_instance_id() -> str | None:
    return _client_instance_id.get()


def current_execution_context() -> dict[str, Any] | None:
    value = _execution_context.get()
    return dict(value) if value else None


def set_execution_context(context: dict[str, Any] | None) -> Token[dict[str, Any] | None]:
    return _execution_context.set(dict(context) if context else None)


def reset_execution_context(token: Token[dict[str, Any] | None]) -> None:
    try:
        _execution_context.reset(token)
    except (ValueError, LookupError):
        _execution_context.set(None)


@contextmanager
def bind_execution_context(context: dict[str, Any] | None) -> Iterator[dict[str, Any] | None]:
    token = _execution_context.set(dict(context) if context else None)
    try:
        yield current_execution_context()
    finally:
        try:
            _execution_context.reset(token)
        except (ValueError, LookupError):
            _execution_context.set(None)


JOB_CONTEXT_KEYS = (
    "workspace_id",
    "workspace_mode",
    "data_namespace",
    "execution_machine_id",
    "result_root_label",
    "result_root",
    "logical_source_id",
    "mapping_id",
    "client_instance_id",
)


def context_from_job(job: dict[str, Any] | None) -> dict[str, Any] | None:
    """从持久化任务记录恢复执行上下文；旧任务没有工作区字段时返回 None。"""
    if not job or not job.get("workspace_id"):
        return None
    return {key: job.get(key) for key in JOB_CONTEXT_KEYS if job.get(key) is not None}


def _lock_for(root: Path, namespace: str) -> threading.RLock:
    key = (os.path.normcase(os.path.normpath(str(root))), namespace)
    with _ROOT_LOCKS_GUARD:
        lock = _ROOT_LOCKS.get(key)
        if lock is None:
            lock = threading.RLock()
            _ROOT_LOCKS[key] = lock
        return lock


def _safe_filename(namespace: str) -> str:
    safe = "".join(ch for ch in namespace if ch.isalnum() or ch in "-_.")
    if not safe:
        raise CleanroomException(400, "INVALID_NAMESPACE", "存储命名空间不合法")
    return safe + ".json"


class WorkspaceJsonState(storage.JsonState):
    """绑定到指定工作区结果根的 JsonState，不修改全局 storage 单例。"""

    def __init__(
        self,
        root: Path,
        namespace: str,
        default_factory: Callable[[], Any],
    ) -> None:
        super().__init__(namespace, default_factory)
        self._root = Path(root)

    @property
    def root(self) -> Path:
        return self._root

    def _path(self) -> Path:
        return self._root / _safe_filename(self.namespace)

    def read(self) -> Any:
        path = self._path()
        if not path.exists():
            return self._default_factory()
        try:
            with path.open("r", encoding="utf-8") as handle:
                value = json.load(handle)
        except (OSError, UnicodeError, json.JSONDecodeError):
            return self._default_factory()
        return value

    def write(self, payload: Any) -> None:
        path = self._path()
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(path.name + ".tmp")
        with temporary.open("w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=1, sort_keys=True)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)

    def mutate(self, mutator: Callable[[Any], Any]) -> Any:
        with _lock_for(self._root, self.namespace):
            state = self.read()
            result = mutator(state)
            self.write(state)
            return result


def json_state(
    root: Path | str,
    namespace: str,
    default_factory: Callable[[], Any],
) -> WorkspaceJsonState:
    return WorkspaceJsonState(Path(root), namespace, default_factory)


def execution_root() -> Path | None:
    context = current_execution_context()
    if not context or not context.get("result_root"):
        return None
    return Path(str(context["result_root"]))
