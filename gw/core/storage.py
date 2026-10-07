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

"""洁净室通用本地持久化与路径安全基础设施（Phase 12）。

设计取舍：
- 单实例落盘 JSON（原子替换），不引入数据库与多实例分布式一致性；重启可恢复。
- 所有对外可见的写入都必须经过本模块，便于统一审计与并发控制。
- 路径访问必须显式声明允许根目录，越界一律失败关闭，且不得回显调用方原始路径。

证据边界：本模块只保证**单进程/单实例**一致性；多 worker 并发写属部署方职责，
文档必须如实标注该限制。
"""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, List, Optional

from gw.core.errors import CleanroomException
from gw.core.runtime_paths import RuntimePathError, resolve_runtime_paths

DATA_DIR_ENV = "GW_DATA_DIR"
ALLOWED_ROOTS_ENV = "GW_ALLOWED_ROOTS"
DEFAULT_FOLDER = "GodsWorkbench"

_LOCK = threading.RLock()
_NAMESPACE_LOCKS: Dict[str, threading.RLock] = {}


def data_root() -> Path:
    """返回当前运行态已通过三根路径校验的 JSON 数据根。"""
    try:
        return resolve_runtime_paths().data_root
    except RuntimePathError as exc:
        raise CleanroomException(400, exc.code, str(exc)) from None


def allowed_roots() -> List[Path]:
    """返回允许访问的本机文件根目录清单；未配置即为空（等价于禁止一切本机文件访问）。"""
    raw = os.environ.get(ALLOWED_ROOTS_ENV, "").strip()
    if not raw:
        return []
    roots: List[Path] = []
    for item in raw.split(os.pathsep):
        item = item.strip()
        if not item:
            continue
        candidate = Path(item).expanduser()
        if not candidate.is_absolute():
            raise CleanroomException(400, "INVALID_ALLOWED_ROOT", "GW_ALLOWED_ROOTS 只接受绝对路径")
        roots.append(candidate.resolve())
    return roots


def resolve_within_roots(raw_path: str) -> Path:
    """把调用方给出的路径解析到允许根目录内；越界或未配置根目录一律失败关闭。

    安全约束：
    - 必须已配置 GW_ALLOWED_ROOTS，否则不提供任何本机文件能力；
    - 解析后（含符号链接）必须落在某个允许根目录内，否则 403；
    - 错误信息**不得**回显调用方原始路径。
    """
    roots = allowed_roots()
    if not roots:
        raise CleanroomException(
            403,
            "LOCAL_FILE_ACCESS_NOT_ADMITTED",
            "本机文件访问未获准入（未配置允许根目录）",
        )
    if not isinstance(raw_path, str) or not raw_path.strip():
        raise CleanroomException(400, "INVALID_PATH", "路径参数不合法")
    try:
        candidate = Path(raw_path).expanduser()
        if not candidate.is_absolute():
            candidate = (roots[0] / candidate)
        resolved = candidate.resolve()
    except (OSError, RuntimeError, ValueError):
        raise CleanroomException(400, "INVALID_PATH", "路径参数不合法")
    for root in roots:
        try:
            resolved.relative_to(root)
        except ValueError:
            continue
        return resolved
    raise CleanroomException(403, "PATH_OUTSIDE_ALLOWED_ROOTS", "目标路径不在允许根目录内")


def relative_display(path: Path) -> str:
    """把绝对路径降级为相对允许根的展示串；找不到允许根时返回文件名，不回显绝对路径。"""
    for root in allowed_roots():
        try:
            return path.resolve().relative_to(root).as_posix()
        except ValueError:
            continue
    return path.name



# 只有服务端明确产物目录可以经通用下载/清理接口访问；根目录状态和视频ACL目录不准入。
OUTPUT_NAMESPACES = frozenset({"ai_uploads", "media_output", "registry_uploads", "local_assets"})


def _output_parts(raw_path: str) -> List[str]:
    from pathlib import PureWindowsPath
    raw = str(raw_path or "")
    pieces = raw.replace("\\", "/").split("/")
    if (not raw or len(raw) > 2048 or ":" in raw or any(ord(ch) < 32 for ch in raw)
            or not pieces or pieces[0] not in OUTPUT_NAMESPACES
            or any(part in {"", ".", ".."} or part.endswith((" ", "."))
                   or PureWindowsPath(part).is_reserved() for part in pieces)):
        raise CleanroomException(403, "OUTPUT_PATH_NOT_ALLOWED", "目标不是已准入的存储产物")
    return pieces


def _checked_output_path(raw_path: str, *, must_exist: bool, directory: bool = False) -> Path:
    """同时检查原始组件与解析结果，不把链接目标重新解释为白名单目录。"""
    import stat
    parts = _output_parts(raw_path)
    if not directory and len(parts) < 2:
        raise CleanroomException(403, "OUTPUT_PATH_NOT_ALLOWED", "目标不是普通产物文件")
    base = data_root().resolve()
    current = base
    exists = True
    for index, part in enumerate(parts):
        current = current / part
        try:
            info = current.lstat()
        except FileNotFoundError:
            exists = False
            continue
        except OSError:
            raise CleanroomException(403, "OUTPUT_PATH_NOT_ALLOWED", "无法安全核实产物路径") from None
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise CleanroomException(403, "OUTPUT_PATH_NOT_ALLOWED", "产物路径不允许链接或重解析点")
        require_directory = index < len(parts) - 1 or directory
        if not (stat.S_ISDIR(info.st_mode) if require_directory else stat.S_ISREG(info.st_mode)):
            raise CleanroomException(403, "OUTPUT_PATH_NOT_ALLOWED", "产物路径类型不符合准入要求")
    try:
        resolved = current.resolve()
        resolved.relative_to(base / parts[0])
    except (ValueError, OSError, RuntimeError):
        raise CleanroomException(403, "OUTPUT_PATH_NOT_ALLOWED", "目标不在原始准入产物目录内") from None
    if must_exist and not exists:
        raise CleanroomException(404, "FILE_NOT_FOUND", "产物不存在")
    return resolved


def resolve_output_path(raw_path: str, *, must_exist: bool = True) -> Path:
    """通用产物只接收相对数据根路径，内部状态即使存在也不得被下载或删除。"""
    return _checked_output_path(raw_path, must_exist=must_exist)


def resolve_output_directory(raw_path: str, *, must_exist: bool = True) -> Path:
    """产物生成和定向缓存操作使用同一组件/重解析点边界。"""
    return _checked_output_path(raw_path, must_exist=must_exist, directory=True)


def iter_output_paths(namespace: Optional[str] = None) -> Iterator[tuple[str, Path]]:
    """先检查目录组件再遍历，绝不先递归进入Junction后才过滤结果。"""
    roots = [namespace] if namespace in OUTPUT_NAMESPACES else sorted(OUTPUT_NAMESPACES)
    if namespace is not None and namespace not in OUTPUT_NAMESPACES:
        return
    pending = list(roots)
    while pending:
        relative = pending.pop()
        try:
            directory = _checked_output_path(relative, must_exist=True, directory=True)
            with os.scandir(directory) as entries:
                children = sorted(entries, key=lambda entry: entry.name)
            for entry in children:
                child = relative + "/" + entry.name
                try:
                    # is_dir只用来选择校验分支；任何链接/重解析点都由共同准入函数拒绝。
                    if entry.is_dir(follow_symlinks=False):
                        _checked_output_path(child, must_exist=True, directory=True)
                        pending.append(child)
                    else:
                        yield child, resolve_output_path(child)
                except (CleanroomException, OSError):
                    continue
        except (CleanroomException, OSError):
            continue


def _namespace_lock(namespace: str) -> threading.RLock:
    with _LOCK:
        lock = _NAMESPACE_LOCKS.get(namespace)
        if lock is None:
            lock = threading.RLock()
            _NAMESPACE_LOCKS[namespace] = lock
        return lock


def _file_for(namespace: str) -> Path:
    safe = "".join(ch for ch in namespace if ch.isalnum() or ch in "-_.")
    if not safe:
        raise CleanroomException(400, "INVALID_NAMESPACE", "存储命名空间不合法")
    return data_root() / (safe + ".json")


def load(namespace: str, default: Any = None) -> Any:
    """读取命名空间快照；文件不存在或损坏时返回默认值，不抛出。"""
    path = _file_for(namespace)
    with _namespace_lock(namespace):
        if not path.exists():
            return default
        try:
            with open(path, "r", encoding="utf-8") as handle:
                return json.load(handle)
        except (OSError, json.JSONDecodeError):
            return default


def save(namespace: str, payload: Any) -> None:
    """原子写入命名空间快照：先写临时文件再替换，避免半截文件。"""
    path = _file_for(namespace)
    with _namespace_lock(namespace):
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        with open(tmp, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=1, sort_keys=True)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)


class JsonState:
    """命名空间内的 JSON 状态容器：读改写全程持锁，写入原子落盘。"""

    def __init__(self, namespace: str, default_factory: Callable[[], Any]) -> None:
        self._namespace = namespace
        self._default_factory = default_factory

    @property
    def namespace(self) -> str:
        return self._namespace

    def read(self) -> Any:
        value = load(self._namespace, None)
        if value is None:
            return self._default_factory()
        return value

    def write(self, payload: Any) -> None:
        save(self._namespace, payload)

    def mutate(self, mutator: Callable[[Any], Any]) -> Any:
        """在命名空间锁内完成读-改-写，返回 mutator 结果。"""
        with _namespace_lock(self._namespace):
            state = self.read()
            result = mutator(state)
            self.write(state)
            return result


def now_iso() -> str:
    """统一 ISO 8601 UTC 时间戳（秒精度），供审计与排序使用。"""
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def next_sequence(prefix: str, existing: Iterator[str]) -> str:
    """按既有稳定 ID 推导下一个确定性序号 ID，禁止随机数/uuid。"""
    max_seen = 0
    for item in existing:
        if not isinstance(item, str) or not item.startswith(prefix):
            continue
        tail = item[len(prefix):]
        if tail.isdigit():
            max_seen = max(max_seen, int(tail))
    return "%s%04d" % (prefix, max_seen + 1)


