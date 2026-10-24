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

import contextlib
import hashlib
import json
import ntpath
import os
import shutil
import stat
import tempfile
import threading
import time
import unicodedata
from collections.abc import Callable, Iterator, Mapping
from pathlib import Path
from typing import Any

from gw.core.errors import CleanroomException
from gw.core.runtime_paths import (
    MODE_TEST,
    RuntimePathError,
    RuntimePaths,
    resolve_runtime_paths,
)

DATA_DIR_ENV = "GW_DATA_DIR"
ALLOWED_ROOTS_ENV = "GW_ALLOWED_ROOTS"
DEFAULT_FOLDER = "GodsWorkbench"
BACKUP_FORMAT = "gods-workbench-runtime-backup-v1"
_BACKUP_ROOTS = frozenset({"data", "auth", "video"})

_LOCK = threading.RLock()
_NAMESPACE_LOCKS: dict[str, threading.RLock] = {}


class RuntimeBackupError(RuntimeError):
    """运行时备份、恢复或回滚失败；错误不携带原始路径。"""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


def data_root() -> Path:
    """返回当前运行态已通过三根路径校验的 JSON 数据根。"""
    try:
        return resolve_runtime_paths().data_root
    except RuntimePathError as exc:
        raise CleanroomException(400, exc.code, str(exc)) from None


def allowed_roots() -> list[Path]:
    """返回允许访问的本机文件根目录清单；未配置即为空（等价于禁止一切本机文件访问）。"""
    raw = os.environ.get(ALLOWED_ROOTS_ENV, "").strip()
    if not raw:
        return []
    roots: list[Path] = []
    for item in raw.split(os.pathsep):
        item = item.strip()
        if not item:
            continue
        candidate = Path(item).expanduser()
        if not candidate.is_absolute():
            raise CleanroomException(400, "INVALID_ALLOWED_ROOT", "GW_ALLOWED_ROOTS 只接受绝对路径")
        roots.append(candidate.resolve())
    return roots


def canonical_relpath(raw_path: str, *, windows: bool | None = None) -> str:
    """生成根目录内相对路径的稳定身份键。"""
    if not isinstance(raw_path, str) or not raw_path:
        raise CleanroomException(400, "INVALID_PATH", "相对路径参数不合法")
    value = unicodedata.normalize("NFC", raw_path.replace("\\", "/"))
    if value.startswith("/") or ntpath.splitdrive(value)[0]:
        raise CleanroomException(400, "INVALID_PATH", "相对路径参数不合法")
    components = value.split("/")
    if any(component in {"", ".", ".."} for component in components):
        raise CleanroomException(400, "INVALID_PATH", "相对路径参数不合法")
    use_casefold = os.name == "nt" if windows is None else windows
    return value.casefold() if use_casefold else value


def root_identity_id(root: Path) -> str:
    """返回兼容既有注册表的允许根身份 ID。"""
    value = os.path.normcase(str(root))
    return "root_" + hashlib.sha256(value.encode()).hexdigest()[:24]


def location_identity(path: Path) -> tuple[str, str]:
    """返回路径所属最深允许根的稳定 root_id 与 canonical_relpath。"""
    resolved = Path(path).resolve()
    matches = []
    for root in allowed_roots():
        try:
            matches.append((root, resolved.relative_to(root)))
        except ValueError:
            continue
    if not matches:
        raise CleanroomException(403, "PATH_OUTSIDE_ALLOWED_ROOTS", "目标路径不在允许根目录内")
    root, relative = max(matches, key=lambda item: len(item[0].parts))
    canonical = "" if str(relative) == "." else canonical_relpath(relative.as_posix())
    return root_identity_id(root), canonical


def normalize_directory_path(raw_path: str) -> str:
    """规范化一个用户配置的绝对目录路径，不推断相对路径根。"""
    if not isinstance(raw_path, str):
        raise CleanroomException(400, "INVALID_PATH", "目录必须是绝对路径")
    value = unicodedata.normalize("NFC", raw_path.strip())
    if not value or any(ord(char) < 32 for char in value):
        raise CleanroomException(400, "INVALID_PATH", "目录必须是绝对路径")

    windows_drive, windows_tail = ntpath.splitdrive(value)
    windows_absolute = bool(
        windows_drive
        and (
            windows_drive.startswith(("\\\\", "//"))
            and len(windows_drive.lstrip("\\/").split("\\" if "\\" in windows_drive else "/")) >= 2
            or windows_tail.startswith(("\\", "/"))
        )
    )
    if os.path.isabs(value):
        return os.path.normpath(value)
    if windows_absolute:
        return ntpath.normpath(value)
    raise CleanroomException(400, "INVALID_PATH", "目录必须是绝对路径")


def directory_path_key(raw_path: str) -> str:
    """生成路径比较键；Windows 盘符与 UNC 使用 Unicode casefold。"""
    normalized = normalize_directory_path(raw_path)
    if ntpath.splitdrive(normalized)[0]:
        return ntpath.normcase(normalized).casefold()
    return os.path.normcase(normalized).casefold()


def validate_directory_path(raw_path: str) -> str:
    """校验目录在当前主机存在且可读、可写、可遍历，并返回规范路径。"""
    normalized = normalize_directory_path(raw_path)
    if ntpath.splitdrive(normalized)[0] and os.name != "nt" and not os.path.isabs(normalized):
        raise CleanroomException(503, "INVALID_PATH", "目录在当前主机不可访问")
    candidate = Path(normalized)
    try:
        if not candidate.is_dir():
            raise CleanroomException(400, "INVALID_PATH", "目录不存在或不可访问")
        if not os.access(candidate, os.R_OK | os.W_OK | os.X_OK):
            raise CleanroomException(400, "INVALID_PATH", "目录不存在或不可访问")
    except (OSError, RuntimeError, ValueError) as exc:
        raise CleanroomException(503, "INVALID_PATH", "目录暂时不可访问") from exc
    return normalized


def resolve_within_roots(raw_path: str, *, root_id: str | None = None) -> Path:
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
    root_by_id = {root_identity_id(root): root for root in roots}
    if root_id is not None:
        selected_root = root_by_id.get(str(root_id))
        if selected_root is None:
            raise CleanroomException(404, "ROOT_NOT_FOUND", "素材来源根不存在")
    else:
        selected_root = None
    try:
        candidate = Path(raw_path).expanduser()
        if not candidate.is_absolute():
            if selected_root is None:
                if ".." in candidate.parts:
                    raise CleanroomException(
                        403, "PATH_OUTSIDE_ALLOWED_ROOTS", "目标路径不在允许根目录内"
                    )
                raise CleanroomException(
                    409, "ROOT_ID_REQUIRED", "解析相对路径必须指定 root_id"
                )
            candidate = selected_root / candidate
        resolved = candidate.resolve()
    except (OSError, RuntimeError, ValueError) as e:
        raise CleanroomException(400, "INVALID_PATH", "路径参数不合法") from e
    candidates = [selected_root] if selected_root is not None else roots
    matches = []
    for root in candidates:
        try:
            resolved.relative_to(root)
        except ValueError:
            continue
        matches.append(root)
    if matches:
        return resolved
    raise CleanroomException(403, "PATH_OUTSIDE_ALLOWED_ROOTS", "目标路径不在允许根目录内")


def coerce_root_id(root_id: str | None) -> str | None:
    """把请求或索引记录里的根身份归一为 ``root_identity_id`` 形态。

    既有本地素材索引使用完整 SHA-256 摘要作为 root_id；这里把它映射到当前
    允许根的 ``root_<24hex>`` 身份。空值返回 None；无法识别的值原样返回，
    由 ``resolve_within_roots`` 以 404 ROOT_NOT_FOUND 失败关闭。
    """
    if root_id is None:
        return None
    value = str(root_id).strip()
    if not value:
        return None
    for root in allowed_roots():
        if value == root_identity_id(root):
            return value
        digest = hashlib.sha256(os.path.normcase(str(root)).encode("utf-8")).hexdigest()
        if value == digest:
            return root_identity_id(root)
    return value


def resolve_request_path(raw_path: str, *, root_id: str | None = None) -> Path:
    """请求级路径解析：显式 root_id 优先；相对路径在单根部署下取唯一根。

    严格解析器 ``resolve_within_roots`` 仍然对无 root_id 的相对路径返回
    409 ROOT_ID_REQUIRED。本函数只在**恰好配置了一个允许根**时才为相对路径
    补上该唯一根，多根部署绝不猜测（不存在 ``roots[0]`` 回退），调用方必须
    从请求或索引记录透传 root_id。
    """
    selected = coerce_root_id(root_id)
    if selected is None and isinstance(raw_path, str) and raw_path.strip():
        roots = allowed_roots()
        if len(roots) == 1:
            try:
                relative = not Path(raw_path).expanduser().is_absolute()
            except (OSError, RuntimeError, ValueError):
                relative = False
            if relative:
                selected = root_identity_id(roots[0])
    return resolve_within_roots(raw_path, root_id=selected)


def resolve_asset_registration_root(raw_path: str) -> tuple[Path, str]:
    """校验资产库登记目录，并返回其允许根与相对前缀。

    登记只接受规范化后的绝对目录；不会像通用路径解析器那样把相对路径
    拼接到任何一个允许根。路径通过 DIR-T2 的存在性与访问权限校验后，
    必须落在某个 ``GW_ALLOWED_ROOTS`` 根目录内。
    """
    roots = allowed_roots()
    if not roots:
        raise CleanroomException(
            403,
            "LOCAL_FILE_ACCESS_NOT_ADMITTED",
            "请由管理员在服务端配置 GW_ALLOWED_ROOTS 后再登记素材库",
        )
    normalized = validate_directory_path(normalize_directory_path(raw_path))
    try:
        candidate = Path(normalized).resolve(strict=True)
    except (OSError, RuntimeError, ValueError):
        raise CleanroomException(400, "INVALID_PATH", "目录不存在或不可访问") from None
    matches = []
    for root in roots:
        try:
            relative = candidate.relative_to(root)
        except ValueError:
            continue
        matches.append((root, relative))
    if not matches:
        raise CleanroomException(
            403,
            "PATH_OUTSIDE_ALLOWED_ROOTS",
            "该素材目录不在服务器允许根目录内，请由管理员配置 GW_ALLOWED_ROOTS 后重试",
        )
    root, relative = max(matches, key=lambda item: len(item[0].parts))
    prefix = "" if str(relative) == "." else relative.as_posix()
    return root, prefix


def relative_display(path: Path) -> str:
    """把绝对路径降级为相对允许根的展示串；找不到允许根时返回文件名，不回显绝对路径。"""
    matches = []
    for root in allowed_roots():
        try:
            matches.append((root, path.resolve().relative_to(root)))
        except ValueError:
            continue
    if matches:
        _, relative = max(matches, key=lambda item: len(item[0].parts))
        return relative.as_posix()
    return path.name



# 只有服务端明确产物目录可以经通用下载/清理接口访问；根目录状态和视频ACL目录不准入。
OUTPUT_NAMESPACES = frozenset({"ai_uploads", "media_output", "registry_uploads", "local_assets"})


def _output_parts(raw_path: str) -> list[str]:
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


def iter_output_paths(namespace: str | None = None) -> Iterator[tuple[str, Path]]:
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


def file_for(namespace: str) -> Path:
    """返回指定命名空间快照落盘的绝对 JSON 路径。"""
    safe = "".join(ch for ch in namespace if ch.isalnum() or ch in "-_.")
    if not safe:
        raise CleanroomException(400, "INVALID_NAMESPACE", "存储命名空间不合法")
    return data_root() / (safe + ".json")


_file_for = file_for


def load(namespace: str, default: Any = None) -> Any:
    """读取命名空间快照；文件不存在或损坏时返回默认值，不抛出。"""
    path = _file_for(namespace)
    with _namespace_lock(namespace):
        if not path.exists():
            return default
        try:
            with open(path, encoding="utf-8") as handle:
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

    @property
    def file_path(self) -> Path:
        """返回当前命名空间落盘的绝对文件路径。"""
        return file_for(self._namespace)

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


def _backup_fail(code: str, message: str) -> None:
    raise RuntimeBackupError(code, message)


def _lexists(path: Path) -> bool:
    try:
        path.lstat()
    except FileNotFoundError:
        return False
    except (OSError, RuntimeError):
        _backup_fail("BACKUP_PATH_UNSAFE", "运行时备份路径无法安全核验")
    return True


def _reject_links(path: Path) -> None:
    """拒绝备份边界上的符号链接和 Windows 重解析点。"""
    current = Path(path.anchor) if path.anchor else Path()
    parts = path.parts[1:] if path.anchor else path.parts
    for part in parts:
        if not part:
            continue
        current = current / part
        try:
            info = current.lstat()
        except FileNotFoundError:
            continue
        except (OSError, RuntimeError):
            _backup_fail("BACKUP_PATH_UNSAFE", "运行时备份路径无法安全核验")
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x0400:
            _backup_fail("BACKUP_PATH_LINK", "运行时备份不允许符号链接或重解析点")


def _canonical_backup_path(raw: object) -> Path:
    if not isinstance(raw, (str, os.PathLike)):
        _backup_fail("BACKUP_PATH_INVALID", "运行时备份路径必须是绝对路径")
    candidate = Path(os.fspath(raw))
    if not candidate.is_absolute():
        _backup_fail("BACKUP_PATH_INVALID", "运行时备份路径必须是绝对路径")
    _reject_links(candidate)
    try:
        resolved = candidate.resolve(strict=False)
    except (OSError, RuntimeError, ValueError):
        _backup_fail("BACKUP_PATH_INVALID", "运行时备份路径无法安全解析")
    _reject_links(resolved)
    return resolved


def _is_inside(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def _ensure_test_runtime(paths: RuntimePaths) -> None:
    if not isinstance(paths, RuntimePaths) or paths.mode != MODE_TEST:
        _backup_fail("BACKUP_TEST_ONLY", "备份与回滚只允许在隔离测试态执行")


def _runtime_sources(paths: RuntimePaths) -> tuple[tuple[str, Path, bool], ...]:
    sources = (
        ("data", _canonical_backup_path(paths.data_root), True),
        ("auth", _canonical_backup_path(paths.auth_db), False),
        ("video", _canonical_backup_path(paths.video_root), True),
    )
    for index, (_left_name, left, _left_directory) in enumerate(sources):
        for _right_name, right, _right_directory in sources[index + 1:]:
            if _is_inside(left, right) or _is_inside(right, left):
                _backup_fail("BACKUP_PATH_OVERLAP", "运行时来源目录不得相互重叠")
    return sources


def _ensure_source_shape(name: str, source: Path, directory: bool) -> None:
    if not _lexists(source):
        return
    info = source.lstat()
    if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x0400:
        _backup_fail("BACKUP_SOURCE_LINK", "运行时来源不允许符号链接或重解析点")
    if directory and not stat.S_ISDIR(info.st_mode):
        _backup_fail("BACKUP_SOURCE_SHAPE", "运行时目录来源类型不正确")
    if not directory and not stat.S_ISREG(info.st_mode):
        _backup_fail("BACKUP_SOURCE_SHAPE", "运行时账户库来源类型不正确")


def _source_files(name: str, source: Path, directory: bool) -> list[tuple[str, Path]]:
    """返回来源文件和备份内相对路径，并在遍历时拒绝链接。"""
    _ensure_source_shape(name, source, directory)
    if not _lexists(source):
        return []
    if not directory:
        return [(f"{name}/{source.name}", source)]
    files: list[tuple[str, Path]] = []
    pending = [source]
    while pending:
        current = pending.pop()
        try:
            children = sorted(current.iterdir(), key=lambda item: item.name)
        except (OSError, RuntimeError):
            _backup_fail("BACKUP_SOURCE_UNREADABLE", "运行时来源无法读取")
        for child in children:
            try:
                info = child.lstat()
            except (OSError, RuntimeError):
                _backup_fail("BACKUP_SOURCE_UNREADABLE", "运行时来源无法读取")
            if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x0400:
                _backup_fail("BACKUP_SOURCE_LINK", "运行时来源不允许符号链接或重解析点")
            if stat.S_ISDIR(info.st_mode):
                pending.append(child)
            elif stat.S_ISREG(info.st_mode):
                try:
                    relative = child.relative_to(source).as_posix()
                except ValueError:
                    _backup_fail("BACKUP_SOURCE_SHAPE", "运行时来源路径不在准入根内")
                files.append((f"{name}/{relative}", child))
            else:
                _backup_fail("BACKUP_SOURCE_SHAPE", "运行时来源包含不支持的文件类型")
    return sorted(files)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except (OSError, RuntimeError):
        _backup_fail("BACKUP_SOURCE_UNREADABLE", "运行时来源无法读取")
    return digest.hexdigest()


def _backup_destination(paths: RuntimePaths, destination: object | None) -> Path:
    target = _canonical_backup_path(destination if destination is not None else paths.backup_root)
    for _name, source, _directory in _runtime_sources(paths):
        if _is_inside(target, source) or _is_inside(source, target):
            _backup_fail("BACKUP_PATH_OVERLAP", "备份目录不得与运行时来源重叠")
    try:
        target.mkdir(parents=True, exist_ok=True)
    except OSError:
        _backup_fail("BACKUP_PATH_UNAVAILABLE", "备份目录不可用")
    if not target.is_dir():
        _backup_fail("BACKUP_PATH_SHAPE", "备份目录必须是目录")
    return target


def _remove_path(path: Path) -> None:
    if not _lexists(path):
        return
    info = path.lstat()
    if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x0400:
        _backup_fail("BACKUP_PATH_LINK", "运行时备份不允许符号链接或重解析点")
    try:
        if stat.S_ISDIR(info.st_mode):
            shutil.rmtree(path)
        else:
            path.unlink()
    except OSError:
        _backup_fail("BACKUP_PATH_UNAVAILABLE", "运行时备份路径无法清理")


def _atomic_json(path: Path, payload: Mapping[str, Any]) -> None:
    temporary = path.with_name(path.name + ".tmp")
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as handle:
            json.dump(dict(payload), handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except OSError:
        _remove_path(temporary)
        _backup_fail("BACKUP_WRITE_FAILED", "运行时备份清单写入失败")


def create_runtime_backup(
    paths: RuntimePaths,
    destination: object | None = None,
    *,
    source_state_version: int | str | None = None,
    target_state_version: int | str | None = None,
    metadata: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """在隔离测试态复制 data/auth/video 并生成带哈希清单的备份。"""
    _ensure_test_runtime(paths)
    target = _backup_destination(paths, destination)
    started = time.perf_counter()
    created_at = now_iso()
    seed = f"{created_at}:{time.time_ns()}:{paths.data_root}:{paths.auth_db}:{paths.video_root}".encode()
    backup_id = "backup_" + hashlib.sha256(seed).hexdigest()[:20]
    final = target / backup_id
    staging = target / f".{backup_id}.staging"
    if _lexists(final) or _lexists(staging):
        _backup_fail("BACKUP_ALREADY_EXISTS", "运行时备份编号已存在")
    files: list[dict[str, Any]] = []
    try:
        staging.mkdir(parents=True)
        for root_name, source, is_directory in _runtime_sources(paths):
            for relative, original in _source_files(root_name, source, is_directory):
                target_file = staging / relative
                target_file.parent.mkdir(parents=True, exist_ok=True)
                try:
                    shutil.copy2(original, target_file)
                except OSError:
                    _backup_fail("BACKUP_COPY_FAILED", "运行时来源复制失败")
                files.append({
                    "path": relative,
                    "size": target_file.stat().st_size,
                    "sha256": _sha256(target_file),
                })
        manifest: dict[str, Any] = {
            "format": BACKUP_FORMAT,
            "schema_version": 1,
            "backup_id": backup_id,
            "created_at": created_at,
            "runtime_mode": paths.mode,
            "source_state_version": source_state_version,
            "target_state_version": target_state_version,
            "roots": {"data": "data", "auth": "auth", "video": "video"},
            "files": sorted(files, key=lambda item: item["path"]),
            "metadata": dict(metadata or {}),
        }
        _atomic_json(staging / "manifest.json", manifest)
        os.replace(staging, final)
    except RuntimeBackupError:
        _remove_path(staging)
        raise
    except OSError:
        _remove_path(staging)
        _backup_fail("BACKUP_WRITE_FAILED", "运行时备份写入失败")
    elapsed = round((time.perf_counter() - started) * 1000, 4)
    return {
        "status": "created",
        "backup_id": backup_id,
        "path": str(final),
        "manifest": manifest,
        "source_state_version": source_state_version,
        "target_state_version": target_state_version,
        "files": len(files),
        "backup_duration_ms": elapsed,
        "rpo_ms": 0.0,
    }


def _backup_path(backup: object) -> Path:
    if isinstance(backup, Mapping):
        backup = backup.get("path")
    return _canonical_backup_path(backup)


def _load_backup_manifest(backup: object) -> tuple[Path, dict[str, Any]]:
    directory = _backup_path(backup)
    manifest_path = directory / "manifest.json"
    _reject_links(manifest_path)
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        _backup_fail("BACKUP_MANIFEST_INVALID", "运行时备份清单无效")
    if (not isinstance(payload, dict) or payload.get("format") != BACKUP_FORMAT
            or payload.get("schema_version") != 1):
        _backup_fail("BACKUP_MANIFEST_INVALID", "运行时备份清单版本不受支持")
    backup_id = str(payload.get("backup_id") or "")
    if not backup_id.startswith("backup_") or directory.name != backup_id:
        _backup_fail("BACKUP_MANIFEST_INVALID", "运行时备份编号无效")
    if payload.get("runtime_mode") != MODE_TEST:
        _backup_fail("BACKUP_TEST_ONLY", "备份清单不是隔离测试态产物")
    entries = payload.get("files")
    if not isinstance(entries, list):
        _backup_fail("BACKUP_MANIFEST_INVALID", "运行时备份文件清单无效")
    seen: set[str] = set()
    expected: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict):
            _backup_fail("BACKUP_MANIFEST_INVALID", "运行时备份文件清单无效")
        relative = str(entry.get("path") or "")
        pieces = relative.replace("\\", "/").split("/")
        if len(pieces) < 2 or pieces[0] not in _BACKUP_ROOTS or any(part in {"", ".", ".."} for part in pieces):
            _backup_fail("BACKUP_MANIFEST_INVALID", "运行时备份文件路径无效")
        if relative in seen or not isinstance(entry.get("size"), int) or not isinstance(entry.get("sha256"), str):
            _backup_fail("BACKUP_MANIFEST_INVALID", "运行时备份文件清单无效")
        seen.add(relative)
        expected.add(relative)
        source = directory / Path(*pieces)
        _reject_links(source)
        try:
            info = source.stat()
        except (OSError, RuntimeError):
            _backup_fail("BACKUP_INTEGRITY_FAILED", "运行时备份文件缺失")
        if not stat.S_ISREG(info.st_mode) or info.st_size != entry["size"] or _sha256(source) != entry["sha256"]:
            _backup_fail("BACKUP_INTEGRITY_FAILED", "运行时备份文件校验失败")
    for root_name in _BACKUP_ROOTS:
        root = directory / root_name
        if not _lexists(root):
            continue
        _reject_links(root)
        for candidate in root.rglob("*"):
            if candidate.is_file():
                relative = candidate.relative_to(directory).as_posix()
                if relative not in expected:
                    _backup_fail("BACKUP_INTEGRITY_FAILED", "运行时备份包含未登记文件")
    return directory, payload


def _copy_manifest_to_stage(directory: Path, manifest: Mapping[str, Any], stage: Path) -> int:
    count = 0
    for entry in manifest["files"]:
        pieces = str(entry["path"]).split("/")
        source = directory.joinpath(*pieces)
        target = stage.joinpath(*pieces)
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            shutil.copy2(source, target)
        except OSError:
            _backup_fail("BACKUP_COPY_FAILED", "运行时备份恢复暂存失败")
        if target.stat().st_size != entry["size"] or _sha256(target) != entry["sha256"]:
            _backup_fail("BACKUP_INTEGRITY_FAILED", "运行时备份恢复暂存校验失败")
        count += 1
    return count


def _restore_commit(paths: RuntimePaths, stage: Path, backup_id: str) -> None:
    targets = (
        (paths.data_root, stage / "data", True),
        (paths.auth_db, stage / "auth" / paths.auth_db.name, False),
        (paths.video_root, stage / "video", True),
    )
    old_paths: list[tuple[Path, Path]] = []
    new_paths: list[Path] = []
    try:
        for target, staged, is_directory in targets:
            target = _canonical_backup_path(target)
            staged = _canonical_backup_path(staged)
            _ensure_source_shape("target", target, is_directory)
            target.parent.mkdir(parents=True, exist_ok=True)
            old = target.with_name(f".{target.name}.gw-old-{backup_id}")
            if _lexists(old):
                _remove_path(old)
            if _lexists(target):
                os.replace(target, old)
                old_paths.append((target, old))
            if _lexists(staged):
                os.replace(staged, target)
                new_paths.append(target)
            elif is_directory:
                target.mkdir(parents=True, exist_ok=True)
                new_paths.append(target)
        for _target, old in old_paths:
            _remove_path(old)
    except Exception:
        for target in reversed(new_paths):
            with contextlib.suppress(RuntimeBackupError):
                _remove_path(target)
        for target, old in reversed(old_paths):
            if _lexists(old) and not _lexists(target):
                os.replace(old, target)
        raise


def restore_runtime_backup(paths: RuntimePaths, backup: object) -> dict[str, Any]:
    """校验并恢复隔离测试态备份，恢复前后不触碰开发或生产根。"""
    _ensure_test_runtime(paths)
    directory, manifest = _load_backup_manifest(backup)
    started = time.perf_counter()
    stage = Path(tempfile.mkdtemp(prefix=".gw-restore-"))
    try:
        for root_name in _BACKUP_ROOTS:
            (stage / root_name).mkdir(parents=True, exist_ok=True)
        files_restored = _copy_manifest_to_stage(directory, manifest, stage)
        _restore_commit(paths, stage, str(manifest["backup_id"]))
    except RuntimeBackupError:
        raise
    except (OSError, RuntimeError):
        _backup_fail("BACKUP_RESTORE_FAILED", "运行时备份恢复失败")
    finally:
        _remove_path(stage)
    elapsed = round((time.perf_counter() - started) * 1000, 4)
    return {
        "status": "restored",
        "backup_id": manifest["backup_id"],
        "path": str(directory),
        "source_state_version": manifest.get("source_state_version"),
        "target_state_version": manifest.get("target_state_version"),
        "files_restored": files_restored,
        "restore_duration_ms": elapsed,
        "rpo_ms": 0.0,
        "rto_ms": elapsed,
    }


def upgrade_runtime(
    paths: RuntimePaths,
    upgrader: Callable[[RuntimePaths], Any],
    *,
    destination: object | None = None,
    source_state_version: int | str | None = None,
    target_state_version: int | str | None = None,
    metadata: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """先备份再执行隔离升级；升级异常时自动恢复同一备份。"""
    if not callable(upgrader):
        _backup_fail("UPGRADE_CALLBACK_INVALID", "升级操作必须是可调用对象")
    backup = create_runtime_backup(
        paths,
        destination,
        source_state_version=source_state_version,
        target_state_version=target_state_version,
        metadata=metadata,
    )
    started = time.perf_counter()
    try:
        result = upgrader(paths)
    except Exception:
        try:
            restore_runtime_backup(paths, backup)
        except Exception as rollback_error:
            raise RuntimeBackupError("UPGRADE_ROLLBACK_FAILED", "升级失败且自动回滚未完成") from rollback_error
        raise
    elapsed = round((time.perf_counter() - started) * 1000, 4)
    return {
        "status": "upgraded",
        "backup": backup,
        "upgrade_result": result,
        "backup_duration_ms": backup["backup_duration_ms"],
        "upgrade_duration_ms": elapsed,
        "rpo_ms": 0.0,
    }


def rollback_runtime(paths: RuntimePaths, backup: object) -> dict[str, Any]:
    """把隔离测试态恢复到指定备份，并保留可审计的回滚耗时。"""
    result = restore_runtime_backup(paths, backup)
    result["status"] = "rolled_back"
    return result


# 兼容任务描述中的简短动作名；实现仍集中在带 runtime 前缀的函数。
backup_runtime = create_runtime_backup
restore_runtime = restore_runtime_backup


def next_sequence(prefix: str, existing: Iterator[str]) -> str:
    """按既有稳定 ID 推导下一个确定性序号 ID，禁止随机数/uuid。"""
    max_seen = 0
    for item in existing:
        if not isinstance(item, str) or not item.startswith(prefix):
            continue
        tail = item[len(prefix):]
        if tail.isdigit():
            max_seen = max(max_seen, int(tail))
    return f"{prefix}{max_seen + 1:04d}"




class DataDirLockedError(RuntimeError):
    """同一数据目录已被另一个神工坊进程独占。"""


class DataDirLock:
    """数据目录独占文件锁句柄；进程退出时由操作系统自动释放。"""

    def __init__(self, path: Path, handle: Any) -> None:
        self.path = path
        self._handle = handle

    def release(self) -> None:
        if self._handle is None:
            return
        try:
            if os.name == "nt":
                import msvcrt
                self._handle.seek(0)
                msvcrt.locking(self._handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(self._handle.fileno(), fcntl.LOCK_UN)
        finally:
            self._handle.close()
            self._handle = None


_HELD_DATA_LOCKS: dict[str, DataDirLock] = {}


def is_data_dir_lock_held(root: Path) -> bool:
    """本进程是否已持有该数据目录的锁。"""
    key = str(Path(root).resolve())
    with _LOCK:
        held = _HELD_DATA_LOCKS.get(key)
        return held is not None and held._handle is not None


def acquire_data_dir_lock(root: Path) -> DataDirLock:
    """对数据目录加跨进程独占锁；已被别的进程占用时立即失败，不等待、不共享。

    同一进程重复获取（run.py 预检 + 应用 lifespan）返回已持有的句柄。
    """
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    key = str(root.resolve())
    with _LOCK:
        held = _HELD_DATA_LOCKS.get(key)
        if held is not None and held._handle is not None:
            return held
    path = root / ".gw-data.lock"
    handle = open(path, "a+", encoding="utf-8")
    try:
        if os.name == "nt":
            import msvcrt
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        raise DataDirLockedError(f"数据目录已被另一个神工坊进程占用: {root}") from None
    handle.seek(0)
    handle.truncate()
    handle.write(str(os.getpid()))
    handle.flush()
    lock = DataDirLock(path, handle)
    with _LOCK:
        _HELD_DATA_LOCKS[key] = lock
    return lock
