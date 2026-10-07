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

"""Gods-Workbench 单进程启动入口。

入口先解析并校验三态 data/auth/video 路径，再创建目录并导入 FastAPI 应用；
这样开发、生产和测试不会因导入顺序而误用另一态数据。
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import threading
import time
import uuid
from typing import Sequence
from urllib.request import urlopen
import webbrowser

import uvicorn

from gw.core.runtime_paths import (
    MODE_DEV,
    MODE_PROD,
    MODE_TEST,
    RuntimePathError,
    _canonical,
    _check_no_links,
    _inside,
    resolve_runtime_paths,
)

ROOT_DIR = Path(__file__).resolve().parent
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 2077
LEGACY_DATA_DIR_NAME = "GodsWorkbenchClear"
PRODUCTION_DATA_DIR_NAME = "GodsWorkbench"
MIGRATION_RECEIPT_NAME = "migration-receipt.json"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="启动 Gods-Workbench 单进程服务")
    parser.add_argument("--prod", action="store_true", help="使用 LOCALAPPDATA/GodsWorkbench 生产数据根")
    parser.add_argument("--test", action="store_true", help="使用显式 GW_DATA_DIR/GW_LOCAL_AUTH_DB/GW_VIDEO_DATA_DIR 测试路径")
    parser.add_argument("--open-browser", action="store_true", help="健康检查就绪后打开项目中心")
    parser.add_argument("--port", type=int, help="监听端口；默认 2077，可用于同时运行第二个实例")
    parser.add_argument("--reset", metavar="TARGET", nargs="?", help="显式清理开发沙盒内的目标路径")
    parser.add_argument("--migrate", action="store_true", help="一次性迁移旧 GodsWorkbenchClear 数据到生产数据根")
    parser.add_argument("--legacy-root", metavar="PATH", help="迁移源目录（默认 LOCALAPPDATA\\GodsWorkbenchClear）")
    parser.add_argument("--target-root", metavar="PATH", help="迁移目标目录（默认 LOCALAPPDATA\\GodsWorkbench）")
    return parser


def _reset_target(raw: str, repo_root: Path = ROOT_DIR) -> Path:
    """解析显式 reset 目标；只允许开发沙盒本身或其内部目录。"""
    if not isinstance(raw, str) or not raw.strip():
        raise RuntimePathError("RESET_TARGET_REQUIRED", "--reset 必须显式提供目标路径")
    runtime_root = _canonical(repo_root / ".local" / "runtime-dev", code="RESET_TARGET_INVALID")
    target = _canonical(raw, code="RESET_TARGET_INVALID")
    _check_no_links(target, code="RESET_TARGET_INVALID")
    if not _inside(target, runtime_root):
        raise RuntimePathError("RESET_TARGET_NOT_ALLOWED", "--reset 目标必须位于 .local/runtime-dev 内")
    if target.exists() and not target.is_dir():
        raise RuntimePathError("RESET_TARGET_INVALID", "--reset 目标必须是目录")
    if target.exists():
        try:
            _tree_has_links(target)
        except MigrationError as exc:
            raise RuntimePathError(exc.code, str(exc)) from None
    return target


def reset_runtime_target(raw: str, repo_root: Path = ROOT_DIR) -> Path:
    """完成一次独立 reset 命令；不导入应用，不触碰生产目录。"""
    target = _reset_target(raw, repo_root)
    if target.exists():
        shutil.rmtree(target)
    return target




class MigrationError(RuntimeError):
    """一次性数据迁移失败；失败时不产生成功回执。"""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


def _tree_has_links(root: Path) -> None:
    """拒绝迁移源树或 reset 目标树中的符号链接/Junction，避免越界复制或删除。"""
    if not root.exists():
        return
    try:
        root_info = root.lstat()
    except OSError as exc:
        raise MigrationError("MIGRATION_SOURCE_UNREADABLE", "源目录无法安全核验") from exc
    if stat.S_ISLNK(root_info.st_mode) or bool(getattr(root_info, "st_file_attributes", 0) & 0x0400):
        raise MigrationError("MIGRATION_LINK_NOT_ALLOWED", "迁移源不得包含符号链接或 Junction")
    if not root.is_dir():
        return
    for current, dirs, files in os.walk(root, topdown=True, followlinks=False):
        current_path = Path(current)
        for name in [*dirs, *files]:
            candidate = current_path / name
            try:
                info = candidate.lstat()
            except OSError as exc:
                raise MigrationError("MIGRATION_SOURCE_UNREADABLE", "源目录无法安全核验") from exc
            if stat.S_ISLNK(info.st_mode) or bool(getattr(info, "st_file_attributes", 0) & 0x0400):
                raise MigrationError("MIGRATION_LINK_NOT_ALLOWED", "迁移源不得包含符号链接或 Junction")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _file_manifest(root: Path) -> dict[str, dict[str, object]]:
    """生成相对路径、字节数和 SHA-256 清单，用于复制后的逐文件校验。"""
    result: dict[str, dict[str, object]] = {}
    if not root.exists():
        return result
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(root).as_posix()
        result[relative] = {"size": path.stat().st_size, "sha256": _sha256_file(path)}
    return result


def _copy_verified_tree(source: Path, target: Path) -> dict[str, dict[str, object]]:
    """把旧 data 目录复制到 staging，并核对每个文件的尺寸与哈希。"""
    if not source.is_dir():
        raise MigrationError("MIGRATION_SOURCE_INVALID", "旧 data 必须是目录")
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source, target, copy_function=shutil.copy2, symlinks=False)
    source_manifest = _file_manifest(source)
    target_manifest = _file_manifest(target)
    if source_manifest != target_manifest:
        raise MigrationError("MIGRATION_VERIFY_FAILED", "迁移后的 data 校验失败")
    return source_manifest


def _copy_verified_file(source: Path, target: Path) -> dict[str, object]:
    """复制旧账户库并核对字节数与哈希。"""
    if not source.is_file():
        raise MigrationError("MIGRATION_SOURCE_INVALID", "旧 auth.sqlite3 必须是文件")
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)
    source_meta = {"size": source.stat().st_size, "sha256": _sha256_file(source)}
    target_meta = {"size": target.stat().st_size, "sha256": _sha256_file(target)}
    if source_meta != target_meta:
        raise MigrationError("MIGRATION_VERIFY_FAILED", "迁移后的 auth.sqlite3 校验失败")
    return source_meta


def _mark_read_only(root: Path) -> None:
    """把旧目录中的文件和目录改为只读；任何失败都使迁移失败关闭。"""
    entries = sorted(root.rglob("*"), key=lambda item: len(item.parts), reverse=True)
    for path in [*entries, root]:
        try:
            mode = path.stat().st_mode
            if path.is_dir():
                path.chmod(mode & ~stat.S_IWUSR & ~stat.S_IWGRP & ~stat.S_IWOTH)
            else:
                path.chmod(mode & ~stat.S_IWUSR & ~stat.S_IWGRP & ~stat.S_IWOTH)
        except OSError as exc:
            raise MigrationError("MIGRATION_SOURCE_READONLY_FAILED", "旧目录无法切换为只读") from exc


def migrate_legacy_data(
    source_root: str | os.PathLike[str] | None = None,
    destination_root: str | os.PathLike[str] | None = None,
    *,
    repo_root: Path = ROOT_DIR,
) -> dict[str, object]:
    """一次性迁移旧 data/auth.sqlite3；视频临时库永远不复制。\n\n    先在目标父目录建立 staging 并逐文件校验，成功后以目录替换方式落地，\n    最后写入 ``migration-receipt.json``。源目录只改为只读，不删除、不改写内容。\n    """
    localappdata = os.environ.get("LOCALAPPDATA")
    if source_root is None:
        if not localappdata:
            raise MigrationError("MIGRATION_LOCALAPPDATA_REQUIRED", "迁移默认源需要 LOCALAPPDATA")
        source_root = Path(localappdata) / LEGACY_DATA_DIR_NAME
    if destination_root is None:
        if not localappdata:
            raise MigrationError("MIGRATION_LOCALAPPDATA_REQUIRED", "迁移默认目标需要 LOCALAPPDATA")
        destination_root = Path(localappdata) / PRODUCTION_DATA_DIR_NAME

    try:
        source = _canonical(source_root, code="MIGRATION_SOURCE_INVALID")
        destination = _canonical(destination_root, code="MIGRATION_TARGET_INVALID")
        repository = _canonical(repo_root, code="MIGRATION_TARGET_INVALID")
    except RuntimePathError as exc:
        raise MigrationError(exc.code, str(exc)) from None
    if _inside(destination, repository):
        raise MigrationError("MIGRATION_TARGET_IN_REPOSITORY", "迁移目标不得位于源码仓库内")
    if not source.exists() or not source.is_dir():
        raise MigrationError("MIGRATION_SOURCE_NOT_FOUND", "旧 GodsWorkbenchClear 目录不存在")
    _tree_has_links(source)
    if destination.exists():
        raise MigrationError("MIGRATION_TARGET_EXISTS", "迁移目标已存在；一次性迁移不会覆盖现有生产数据")

    data_source = source / "data"
    auth_source = source / "auth.sqlite3"
    if not data_source.exists() and not auth_source.exists():
        raise MigrationError("MIGRATION_NOTHING_TO_COPY", "旧目录中没有可迁移的 data 或 auth.sqlite3")
    parent = destination.parent
    parent.mkdir(parents=True, exist_ok=True)
    staging = parent / (f".{destination.name}.migration-{uuid.uuid4().hex}.staging")
    copied: dict[str, object] = {"data": False, "auth.sqlite3": False}
    manifests: dict[str, object] = {}
    try:
        staging.mkdir()
        if data_source.exists():
            if not data_source.is_dir():
                raise MigrationError("MIGRATION_SOURCE_INVALID", "旧 data 必须是目录")
            _tree_has_links(data_source)
            manifests["data"] = _copy_verified_tree(data_source, staging / "data")
            copied["data"] = True
        if auth_source.exists():
            if not auth_source.is_file():
                raise MigrationError("MIGRATION_SOURCE_INVALID", "旧 auth.sqlite3 必须是文件")
            manifests["auth.sqlite3"] = _copy_verified_file(auth_source, staging / "auth" / "auth.sqlite3")
            copied["auth.sqlite3"] = True
        _mark_read_only(source)
        receipt = {
            "schema_version": 1,
            "status": "success",
            "copied": copied,
            "excluded": ["video", "%TEMP%\\gods-workbench\\video"],
            "source_layout": "GodsWorkbenchClear",
            "destination_layout": "GodsWorkbench",
            "manifests": manifests,
            "created_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
            "source_preserved": True,
            "source_read_only": True,
        }
        receipt_path = staging / MIGRATION_RECEIPT_NAME
        with receipt_path.open("w", encoding="utf-8", newline="\n") as handle:
            json.dump(receipt, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(staging, destination)
        return receipt
    except MigrationError:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    except Exception as exc:
        shutil.rmtree(staging, ignore_errors=True)
        raise MigrationError("MIGRATION_FAILED", "迁移失败，未生成成功回执") from exc

def _prepare_paths(mode: str):
    """先完成三根校验，再创建目录；任何一根非法时不创建其它目录。"""
    paths = resolve_runtime_paths(mode, repo_root=ROOT_DIR)
    paths.data_root.mkdir(parents=True, exist_ok=True)
    paths.auth_db.parent.mkdir(parents=True, exist_ok=True)
    paths.video_root.mkdir(parents=True, exist_ok=True)
    # 创建后再次统一复核，防止目录创建阶段出现重解析点替换。
    return resolve_runtime_paths(mode, repo_root=ROOT_DIR)


def _open_browser_when_ready(url: str, health_url: str, timeout: float = 30.0) -> None:
    """后台等待 /healthz 后打开浏览器；失败只影响便利功能，不影响服务。"""
    def worker() -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                with urlopen(health_url, timeout=1.0) as response:
                    if 200 <= response.status < 300:
                        webbrowser.open(url)
                        return
            except Exception:
                pass
            time.sleep(0.25)
    threading.Thread(target=worker, name="gw-open-browser", daemon=True).start()


def main(argv: Sequence[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    raw_argv = list(argv) if argv is not None else None
    if raw_argv is None:
        import sys
        raw_argv = sys.argv[1:]
    if "--reset" in raw_argv and args.reset is None:
        parser.error("--reset 必须显式提供目标路径")
    if args.reset is not None and args.prod:
        parser.error("--reset 不得与 --prod 同时使用")
    if args.prod and args.test:
        parser.error("--prod 与 --test 不能同时使用")
    if args.migrate and (args.prod or args.test or args.reset is not None or args.open_browser or args.port is not None):
        parser.error("--migrate 不得与 --prod、--reset、--open-browser 或 --port 同时使用")
    if not args.migrate and (args.legacy_root or args.target_root):
        parser.error("--legacy-root/--target-root 只能与 --migrate 一起使用")
    if args.migrate:
        try:
            receipt = migrate_legacy_data(args.legacy_root, args.target_root, repo_root=ROOT_DIR)
        except MigrationError as exc:
            parser.error(str(exc))
        print(json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True))
        return 0

    mode = MODE_TEST if args.test else (MODE_PROD if args.prod else MODE_DEV)
    os.environ["GW_RUNTIME_MODE"] = mode
    if args.reset is not None:
        try:
            target = reset_runtime_target(args.reset, ROOT_DIR)
        except RuntimePathError as exc:
            parser.error(str(exc))
        print(f"已清理开发沙盒目标: {target}")
        return 0

    try:
        paths = _prepare_paths(mode)
    except RuntimePathError as exc:
        parser.error(str(exc))

    # 根路径验证和目录准备完成后才允许导入应用。
    from gw.api.app import app

    host = os.environ.get("GW_HOST", DEFAULT_HOST)
    try:
        port = args.port if args.port is not None else int(os.environ.get("GW_PORT", str(DEFAULT_PORT)))
    except ValueError:
        parser.error("GW_PORT 必须是整数")
    if not 1 <= port <= 65535:
        parser.error("端口必须在 1 到 65535 之间")
    browser_url = f"http://{host}:{port}/static/pages/projects.html"
    print("=" * 60)
    print("  Gods' Workbench Cleanroom")
    print(f"  运行态: {mode}")
    print(f"  数据根: {paths.data_root}")
    print(f"  账户库: {paths.auth_db}")
    print(f"  视频根: {paths.video_root}")
    print(f"  服务地址: http://{host}:{port}")
    print("=" * 60)
    if args.open_browser:
        _open_browser_when_ready(browser_url, f"http://{host}:{port}/healthz")
    uvicorn.run(app, host=host, port=port, workers=1, reload=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
