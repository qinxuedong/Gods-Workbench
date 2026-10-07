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

"""Gods-Workbench 三态运行路径解析与安全校验。

本模块只负责解析和校验路径，不创建目录、不打开数据库，也不导入 FastAPI。
调用方必须先一次性取得 :class:`RuntimePaths`，完成全部校验后才允许创建目录或
导入应用。这样不会出现“先创建一根目录，后来才发现另一根路径非法”的半初始化状态。
"""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path, PureWindowsPath
import stat
from typing import Mapping, Optional


RUNTIME_MODE_ENV = "GW_RUNTIME_MODE"
DATA_DIR_ENV = "GW_DATA_DIR"
LOCAL_AUTH_DB_ENV = "GW_LOCAL_AUTH_DB"
VIDEO_DATA_DIR_ENV = "GW_VIDEO_DATA_DIR"

MODE_TEST = "test"
MODE_DEV = "dev"
MODE_PROD = "prod"
_VALID_MODES = frozenset({MODE_TEST, MODE_DEV, MODE_PROD})

# Windows FILE_ATTRIBUTE_REPARSE_POINT。Linux/macOS 没有该字段，但保留判断可让
# 同一份代码在 Windows Junction 与 POSIX 符号链接上都失败关闭。
_REPARSE_POINT = 0x0400


class RuntimePathError(ValueError):
    """运行路径配置不合法；对象不携带调用方原始路径，避免错误回显敏感信息。"""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


@dataclass(frozen=True)
class RuntimePaths:
    """已经完成三根路径校验的不可变运行时快照。"""

    mode: str
    repo_root: Path
    runtime_root: Path
    data_root: Path
    auth_db: Path
    video_root: Path

    # 兼容调用方常见的显式命名，但不引入短别名。
    @property
    def json_root(self) -> Path:
        return self.data_root

    @property
    def local_auth_db(self) -> Path:
        return self.auth_db

    @property
    def video_data_root(self) -> Path:
        return self.video_root


# ---------------------------------------------------------------------------
# 纯路径判定
# ---------------------------------------------------------------------------


def _fail(code: str, message: str) -> None:
    raise RuntimePathError(code, message)


def _absolute_path(raw: object, *, code: str) -> Path:
    """严格接受绝对路径；拒绝 ``C:foo``、``\\foo`` 和普通相对路径。"""
    if not isinstance(raw, (str, os.PathLike)):
        _fail(code, "运行路径必须是绝对路径")
    text = os.fspath(raw)
    if not isinstance(text, str) or not text.strip() or "\x00" in text:
        _fail(code, "运行路径必须是绝对路径")
    # Path 在当前平台负责实际解析；PureWindowsPath 补充识别 Windows 驱动器格式。
    candidate = Path(text)
    windows = PureWindowsPath(text)
    if not (candidate.is_absolute() or windows.is_absolute()):
        _fail(code, "运行路径必须是绝对路径")
    # 在非 Windows 上不把 C:\\... 当作本地 POSIX 路径继续拼接。
    if os.name != "nt" and windows.is_absolute() and not candidate.is_absolute():
        _fail(code, "当前平台不接受 Windows 专用运行路径")
    return candidate


def _is_reparse(info: os.stat_result) -> bool:
    return bool(getattr(info, "st_file_attributes", 0) & _REPARSE_POINT)


def _check_no_links(path: Path, *, code: str) -> None:
    """检查现有的每个路径段，避免 resolve 把越界链接伪装成正常目录。"""
    # Path.parts 的第一项在 Windows 可能是 ``C:\\`` 或 UNC 根；从根后逐段检查。
    current = Path(path.anchor) if path.anchor else Path()
    parts = path.parts[1:] if path.anchor else path.parts
    for part in parts:
        if not part:
            continue
        current = current / part
        try:
            info = current.lstat()
        except FileNotFoundError:
            # 后续尚不存在的段不能形成现有链接；最终 resolve(strict=False) 仍会校验。
            continue
        except (OSError, RuntimeError):
            _fail(code, "运行路径无法安全核验")
        if stat.S_ISLNK(info.st_mode) or _is_reparse(info):
            _fail(code, "运行路径不允许符号链接或 Junction")


def _canonical(raw: object, *, code: str) -> Path:
    candidate = _absolute_path(raw, code=code)
    _check_no_links(candidate, code=code)
    try:
        resolved = candidate.resolve(strict=False)
    except (OSError, RuntimeError, ValueError):
        _fail(code, "运行路径无法安全解析")
    # 再检查一次解析结果；若平台返回重解析点目标，不能越过原始安全边界。
    _check_no_links(resolved, code=code)
    return resolved


def _inside(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def _require_inside(path: Path, parent: Path, *, code: str, message: str) -> None:
    if not _inside(path, parent):
        _fail(code, message)


def _same_path(left: Path, right: Path) -> bool:
    return left == right


def _env_value(env: Mapping[str, str], name: str) -> Optional[str]:
    value = env.get(name)
    if value is None:
        return None
    value = str(value).strip()
    return value or None


def _read_override(
    env: Mapping[str, str],
    name: str,
    *,
    default: Path,
    mode: str,
) -> Path:
    raw = _env_value(env, name)
    if raw is None:
        return default
    return _canonical(raw, code="INVALID_RUNTIME_PATH")


def _validate_target_shape(paths: tuple[Path, Path, Path]) -> None:
    data_root, auth_db, video_root = paths
    for directory in (data_root, video_root):
        try:
            if directory.exists() and not directory.is_dir():
                _fail("INVALID_RUNTIME_PATH", "数据根必须是目录")
        except OSError:
            _fail("INVALID_RUNTIME_PATH", "数据根无法安全核验")
    try:
        if auth_db.exists() and not auth_db.is_file():
            _fail("INVALID_RUNTIME_PATH", "账户库路径必须是文件")
    except OSError:
        _fail("INVALID_RUNTIME_PATH", "账户库路径无法安全核验")


def _repo_root(raw: Optional[Path]) -> Path:
    value = raw if raw is not None else Path(__file__).resolve().parents[2]
    return _canonical(value, code="INVALID_REPO_ROOT")


def _localappdata(raw: Optional[Path], env: Mapping[str, str]) -> Path:
    value: object = raw if raw is not None else _env_value(env, "LOCALAPPDATA")
    if value is None:
        # 非 Windows 不存在 LOCALAPPDATA；安全策略是显式拒绝，而不是偷偷写入
        # HOME、TEMP 或仓库，调用方可在测试中显式传入模拟父目录。
        _fail("PRODUCTION_ROOT_UNAVAILABLE", "生产态必须显式提供 LOCALAPPDATA")
    return _canonical(value, code="INVALID_LOCALAPPDATA")


# ---------------------------------------------------------------------------
# 公开解析入口
# ---------------------------------------------------------------------------


def resolve_runtime_paths(
    mode: Optional[str] = None,
    *,
    repo_root: Optional[Path] = None,
    env: Optional[Mapping[str, str]] = None,
    localappdata: Optional[Path] = None,
) -> RuntimePaths:
    """一次性解析并校验测试、开发或生产三态的 data/auth/video 三根路径。

    该函数不会创建目录。测试态要求调用方显式传入三根环境变量；开发态和生产态
    只接受各自固定沙盒下的路径，生产态必须提供 ``LOCALAPPDATA``（可通过参数传入
    临时父目录）。所有校验完成后才返回快照。
    """
    source = env if env is not None else os.environ
    selected = (mode or _env_value(source, RUNTIME_MODE_ENV) or MODE_DEV).strip().lower()
    if selected not in _VALID_MODES:
        _fail("INVALID_RUNTIME_MODE", "运行态必须是 test、dev 或 prod")
    root = _repo_root(repo_root)

    if selected == MODE_DEV:
        runtime_root = _canonical(root / ".local" / "runtime-dev", code="INVALID_RUNTIME_PATH")
        defaults = (
            runtime_root / "data",
            runtime_root / "auth" / "auth.sqlite3",
            runtime_root / "video",
        )
        paths = (
            _read_override(source, DATA_DIR_ENV, default=defaults[0], mode=selected),
            _read_override(source, LOCAL_AUTH_DB_ENV, default=defaults[1], mode=selected),
            _read_override(source, VIDEO_DATA_DIR_ENV, default=defaults[2], mode=selected),
        )
        for path in paths:
            _require_inside(path, runtime_root, code="DEV_PATH_OUTSIDE_SANDBOX", message="开发态路径必须位于 .local/runtime-dev 内")
        if not (_same_path(paths[0], defaults[0]) and _same_path(paths[1], defaults[1]) and _same_path(paths[2], defaults[2])):
            _fail("DEV_PATH_NOT_ALLOWED", "开发态只允许固定三根运行路径")
    elif selected == MODE_PROD:
        local_root = _localappdata(localappdata, source)
        runtime_root = _canonical(local_root / "GodsWorkbench", code="INVALID_PRODUCTION_ROOT")
        defaults = (
            runtime_root / "data",
            runtime_root / "auth" / "auth.sqlite3",
            runtime_root / "video",
        )
        paths = (
            _read_override(source, DATA_DIR_ENV, default=defaults[0], mode=selected),
            _read_override(source, LOCAL_AUTH_DB_ENV, default=defaults[1], mode=selected),
            _read_override(source, VIDEO_DATA_DIR_ENV, default=defaults[2], mode=selected),
        )
        # 生产环境固定落到用户数据树，不允许通过变量把数据写回仓库或其它目录。
        for path in paths:
            _require_inside(path, runtime_root, code="PROD_PATH_OUTSIDE_SANDBOX", message="生产态路径必须位于 LOCALAPPDATA/GodsWorkbench 内")
        if not (_same_path(paths[0], defaults[0]) and _same_path(paths[1], defaults[1]) and _same_path(paths[2], defaults[2])):
            _fail("PROD_PATH_NOT_ALLOWED", "生产态只允许固定三根运行路径")
        for path in (runtime_root, *paths):
            if _inside(path, root):
                _fail("PROD_PATH_IN_REPOSITORY", "生产态路径不得位于源码仓库内")
    else:
        runtime_root = root / ".local" / "runtime-test"
        raw_values = (_env_value(source, DATA_DIR_ENV), _env_value(source, LOCAL_AUTH_DB_ENV), _env_value(source, VIDEO_DATA_DIR_ENV))
        if any(value is None for value in raw_values):
            _fail("TEST_PATHS_REQUIRED", "测试态必须显式提供 data、auth、video 三根路径")
        paths = tuple(_canonical(value, code="INVALID_RUNTIME_PATH") for value in raw_values)  # type: ignore[arg-type]
    _validate_target_shape(paths)
    return RuntimePaths(
        mode=selected,
        repo_root=root,
        runtime_root=runtime_root,
        data_root=paths[0],
        auth_db=paths[1],
        video_root=paths[2],
    )



# 易读别名；实现保持单一入口，避免三态校验口径漂移。
parse_runtime_paths = resolve_runtime_paths
runtime_paths = resolve_runtime_paths



__all__ = [
    "DATA_DIR_ENV",
    "LOCAL_AUTH_DB_ENV",
    "MODE_DEV",
    "MODE_PROD",
    "MODE_TEST",
    "RUNTIME_MODE_ENV",
    "RuntimePathError",
    "RuntimePaths",
    "VIDEO_DATA_DIR_ENV",
    "parse_runtime_paths",
    "resolve_runtime_paths",
    "runtime_paths",
]

