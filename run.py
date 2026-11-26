# Copyright 2026 qinxuedong (Gods-Workbench Authors)
# SPDX-License-Identifier: FSL-1.1-Apache-2.0
#
# Licensed under the Functional Source License, Version 1.1, Apache 2.0
# Future License (the "License"); you may not use this file except in
# compliance with the License. You may obtain a copy of the License at
#     https://fsl.software/
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
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import threading
import time
import uuid
import webbrowser
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from ipaddress import ip_address
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit
from urllib.request import urlopen

import uvicorn

if sys.platform == "win32" and hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from gw.core.runtime_paths import (
    MODE_DEV,
    MODE_PROD,
    MODE_TEST,
    RuntimePathError,
    _canonical,
    _check_no_links,
    _inside,
    paths_non_overlapping,
    resolve_runtime_paths,
)

ROOT_DIR = Path(__file__).resolve().parent
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 2077
LEGACY_DATA_DIR_NAME = "GodsWorkbenchClear"
PRODUCTION_DATA_DIR_NAME = "GodsWorkbench"
MIGRATION_RECEIPT_NAME = "migration-receipt.json"
BUNDLE_MANIFEST_NAME = "bundle-manifest.json"
CAPABILITY_PROFILE = "gw-compat-v1"
VERSION_MARKER = "+gw."
REQUIRED_OPENAPI_OPERATIONS = (
    "GET /api/asset-auth/status",
    "GET /api/asset-registry/projects",
    "GET /api/canvases",
    "GET /healthz",
    "PATCH /api/asset-registry/projects/{project_id}",
    "PATCH /api/canvases/{canvas_id}",
    "POST /api/asset-auth/local/login",
    "POST /api/asset-auth/local/setup",
    "POST /api/asset-registry/projects",
    "POST /api/canvases",
)
_PRODUCT_VERSION_RE = re.compile(r"^(?:v)?(\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?)$")
_MANIFEST_BUILD_ID_RE = re.compile(r"^[0-9A-Za-z][0-9A-Za-z._-]{0,127}$")
_GIT_DESCRIBE_RE = re.compile(r"^[0-9A-Za-z][0-9A-Za-z._+-]*$")


@dataclass(frozen=True)
class VersionInfo:
    """一次版本解析的不可变结果；未知版本不携带来源标记。"""

    product_version: str
    provenance: str
    app_version: str
    source: str
    manifest: dict[str, object] | None = None
    error: str = ""

    @property
    def trusted_marker(self) -> bool:
        return VERSION_MARKER in self.app_version and self.source != "unknown"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="启动 Gods-Workbench 单进程服务")
    parser.add_argument(
        "--prod",
        action="store_true",
        help="使用 LOCALAPPDATA/GodsWorkbench 引导指针或生产数据根",
    )
    parser.add_argument("--test", action="store_true", help="使用显式 GW_DATA_DIR/GW_LOCAL_AUTH_DB/GW_VIDEO_DATA_DIR 测试路径")
    parser.add_argument("--open-browser", action="store_true", help="健康检查就绪后打开首页")
    parser.add_argument("--host", help="监听网络地址；默认 127.0.0.1，设为 0.0.0.0 可供局域网设备访问")
    parser.add_argument("--port", type=int, help="监听端口；默认 2077，可用于同时运行第二个实例")
    parser.add_argument("--reset", metavar="TARGET", nargs="?", help="显式清理开发沙盒内的目标路径")
    parser.add_argument("--migrate", action="store_true", help="一次性迁移旧 GodsWorkbenchClear 数据到生产数据根")
    parser.add_argument("--legacy-root", metavar="PATH", help="迁移源目录（默认 LOCALAPPDATA\\GodsWorkbenchClear）")
    parser.add_argument("--target-root", metavar="PATH", help="迁移目标目录（默认 LOCALAPPDATA\\GodsWorkbench）")
    parser.add_argument("--version", action="store_true", help="输出产品版本与来源后退出")
    parser.add_argument("--self-check", action="store_true", help="校验安装运行时、版本与能力基线后退出（不创建账户）")
    parser.add_argument("--expect-version", metavar="VERSION", help="与 --self-check 配合，要求产品版本精确匹配")
    return parser


def _unknown_version(error: str) -> VersionInfo:
    return VersionInfo(
        product_version="unknown",
        provenance="unknown",
        app_version="unknown",
        source="unknown",
        error=error,
    )


def _manifest_path() -> Path:
    override = os.environ.get("GW_BUNDLE_MANIFEST", "").strip()
    return Path(override) if override else ROOT_DIR / BUNDLE_MANIFEST_NAME


def _load_bundle_manifest(path: Path) -> tuple[dict[str, object] | None, str]:
    """读取并校验随包清单；清单存在但损坏时必须失败关闭，不回退到其它来源。"""
    if not path.is_file():
        return None, ""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None, "随包清单不可读取或不是合法 JSON"
    if not isinstance(payload, dict):
        return None, "随包清单必须是 JSON 对象"
    product_version = payload.get("product_version")
    build_id = payload.get("build_id")
    profile = payload.get("capability_profile")
    digest = payload.get("capability_digest")
    git_commit = payload.get("git_commit")
    match = _PRODUCT_VERSION_RE.fullmatch(str(product_version or ""))
    if match is None:
        return None, "随包清单缺少合法 product_version"
    payload["product_version"] = match.group(1)
    if not isinstance(build_id, str) or _MANIFEST_BUILD_ID_RE.fullmatch(build_id) is None:
        return None, "随包清单缺少合法 build_id"
    if profile != CAPABILITY_PROFILE:
        return None, "随包清单能力契约编号未知"
    if not isinstance(digest, str) or re.fullmatch(r"[0-9a-f]{64}", digest) is None:
        return None, "随包清单缺少合法 capability_digest"
    if not isinstance(git_commit, str) or re.fullmatch(r"[0-9a-f]{7,40}", git_commit) is None:
        return None, "随包清单缺少合法 git_commit"
    return payload, ""


def _git_version(repo_root: Path) -> tuple[str, str] | None:
    """只读执行 git describe；不经过 shell，超时或失败一律返回未知。"""
    if not (repo_root / ".git").exists():
        return None

    try:
        completed = subprocess.run(
            ["git", "-C", str(repo_root), "describe", "--tags", "--always", "--dirty"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=3,
            check=False,
            shell=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if completed.returncode != 0:
        return None
    describe = (completed.stdout or "").strip()
    if not describe or _GIT_DESCRIBE_RE.fullmatch(describe) is None:
        return None
    dirty = describe.endswith("-dirty")
    clean = describe[:-6] if dirty else describe
    sha_match = re.search(r"(?:^|g)([0-9a-f]{7,40})(?:-dirty)?$", describe)
    if sha_match is None:
        return None
    sha = sha_match.group(1)[:12]
    product = clean.lstrip("v")
    if dirty:
        product = f"{product}-dirty"
    return product, sha


def resolve_version(manifest_path: Path | None = None) -> VersionInfo:
    """按 manifest → 显式声明 → git → unknown 顺序解析真实产品版本。"""
    path = manifest_path if manifest_path is not None else _manifest_path()
    manifest, manifest_error = _load_bundle_manifest(path)
    if manifest_error:
        return _unknown_version(manifest_error)
    if manifest is not None:
        product = str(manifest["product_version"])
        build_id = str(manifest["build_id"])
        return VersionInfo(
            product_version=product,
            provenance=build_id,
            app_version=f"{product}{VERSION_MARKER}{build_id}",
            source="manifest",
            manifest=manifest,
        )

    declared = os.environ.get("GW_PRODUCT_VERSION", "").strip()
    if declared:
        match = _PRODUCT_VERSION_RE.fullmatch(declared)
        if match is None:
            return _unknown_version("GW_PRODUCT_VERSION 不是合法的 X.Y.Z 版本")
        product = match.group(1)
        return VersionInfo(
            product_version=product,
            provenance="declared",
            app_version=f"{product}{VERSION_MARKER}declared",
            source="declared",
        )

    git_version = _git_version(ROOT_DIR)
    if git_version is not None:
        product, sha = git_version
        return VersionInfo(
            product_version=product,
            provenance=f"git.{sha}",
            app_version=f"{product}{VERSION_MARKER}git.{sha}",
            source="git",
        )
    return _unknown_version("无法从 manifest、GW_PRODUCT_VERSION 或 git 确定版本")


def _apply_version_to_app(app: object, info: VersionInfo) -> bool:
    """把带来源标记的版本写入应用并失效 OpenAPI 缓存；非应用对象返回 False。"""
    try:
        app.version = info.app_version
        app.openapi_schema = None
    except (AttributeError, TypeError):
        return False
    middleware = getattr(app, "middleware", None)
    if not callable(middleware):
        return True

    @middleware("http")
    async def version_probe_no_store(request, call_next):
        response = await call_next(request)
        if request.url.path in {"/openapi.json", "/healthz"}:
            response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
            response.headers["Pragma"] = "no-cache"
            response.headers["Expires"] = "0"
        return response

    return True


def _openapi_operations(schema: dict[str, object]) -> set[str]:
    operations: set[str] = set()
    paths = schema.get("paths")
    if not isinstance(paths, dict):
        return operations
    for route, methods in paths.items():
        if not isinstance(methods, dict):
            continue
        for method in methods:
            if str(method).upper() in {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"}:
                operations.add(f"{str(method).upper()} {route}")
    return operations


def _paths_non_overlapping(install_root: Path, data_root: Path) -> bool:
    try:
        return paths_non_overlapping(install_root, data_root)
    except (OSError, RuntimeError, ValueError):
        return False


def _self_check(args: argparse.Namespace) -> int:
    """安装后自检：只读校验版本、能力和路径，不创建目录、账户或会话。"""
    info = resolve_version()
    if info.source != "manifest" or not info.trusted_marker:
        print(f"自检失败: {info.error or '未找到可信随包版本'}", file=sys.stderr, flush=True)
        return 1
    expected = (args.expect_version or "").strip()
    if expected:
        match = _PRODUCT_VERSION_RE.fullmatch(expected)
        if match is None or match.group(1) != info.product_version:
            print("自检失败: 安装版本与期望版本不一致", file=sys.stderr, flush=True)
            return 1

    mode = MODE_TEST if args.test else (MODE_PROD if args.prod else MODE_DEV)
    os.environ["GW_RUNTIME_MODE"] = mode
    try:
        paths = resolve_runtime_paths(mode, repo_root=ROOT_DIR)
    except RuntimePathError as exc:
        print(f"自检失败: {exc}", file=sys.stderr, flush=True)
        return 1
    effective_data_root = paths.data_location or paths.runtime_root
    if mode == MODE_PROD and (
        not _paths_non_overlapping(ROOT_DIR, paths.runtime_root)
        or not _paths_non_overlapping(ROOT_DIR, effective_data_root)
    ):
        print("自检失败: 安装目录与数据根存在包含关系", file=sys.stderr, flush=True)
        return 1

    previous_host = os.environ.get("GW_HOST")
    os.environ["GW_HOST"] = DEFAULT_HOST
    try:
        from gw.api.app import app
    except Exception as exc:  # noqa: BLE001 - 自检必须把导入失败转为非零退出
        print(f"自检失败: 后端应用无法导入（{type(exc).__name__}）", file=sys.stderr, flush=True)
        return 1
    finally:
        if previous_host is None:
            os.environ.pop("GW_HOST", None)
        else:
            os.environ["GW_HOST"] = previous_host

    if not _apply_version_to_app(app, info):
        print("自检失败: 后端应用不支持版本注入", file=sys.stderr, flush=True)
        return 1
    schema = app.openapi()
    if schema.get("info", {}).get("version") != info.app_version:
        print("自检失败: OpenAPI 版本与随包清单不一致", file=sys.stderr, flush=True)
        return 1
    missing = sorted(set(REQUIRED_OPENAPI_OPERATIONS) - _openapi_operations(schema))
    if missing:
        print(f"自检失败: 能力契约缺少 {len(missing)} 个必需端点", file=sys.stderr, flush=True)
        return 1
    manifest = info.manifest or {}
    result = {
        "ok": True,
        "mode": mode,
        "product_version": info.product_version,
        "app_version": info.app_version,
        "provenance": info.provenance,
        "capability_profile": manifest.get("capability_profile"),
        "capability_digest": manifest.get("capability_digest"),
        "effective_data_location": str(effective_data_root),
        "install_data_non_overlapping": mode != MODE_PROD
        or (
            _paths_non_overlapping(ROOT_DIR, paths.runtime_root)
            and _paths_non_overlapping(ROOT_DIR, effective_data_root)
        ),
    }
    print(json.dumps(result, ensure_ascii=False, sort_keys=True), flush=True)
    return 0


def _resolve_host(cli_host: str | None = None) -> str:
    """解析监听主机地址；优先级：CLI --host > 环境变量 GW_HOST > DEFAULT_HOST (127.0.0.1)。"""
    if cli_host is not None and str(cli_host).strip():
        return str(cli_host).strip()
    env_host = os.environ.get("GW_HOST")
    if env_host is not None and env_host.strip():
        return env_host.strip()
    return DEFAULT_HOST


def _get_lan_ip() -> str | None:
    """尝试获取本机局域网活跃 IPv4 地址；失败或无可用 IP 时优雅降级返回 None。"""
    import socket

    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("8.8.8.8", 80))
            candidate = sock.getsockname()[0]
            if candidate and not candidate.startswith("127.") and candidate != "0.0.0.0":
                return candidate
    except Exception:
        pass

    try:
        hostname = socket.gethostname()
        candidates = socket.gethostbyname_ex(hostname)[2]
        for ip in candidates:
            if not ip.startswith("127.") and ip != "0.0.0.0" and ":" not in ip:
                return ip
    except Exception:
        pass

    return None


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
            "created_at": datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
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


def _is_loopback_host(host: str) -> bool:
    """判断主机名是否回环地址。"""
    normalized = str(host or "").strip().lower().rstrip(".")
    if normalized == "localhost":
        return True
    try:
        return ip_address(normalized).is_loopback
    except ValueError:
        return False


def _trusted_local_browser_url(base_url: str) -> str:
    """受信本机浏览器唤起 URL 的最小接线（QIN-630 特批范围）。

    只在浏览器目标是**回环地址**时，把一次性启动票据放进 URL **片段**：

    - 片段不会随请求发给服务端（不进访问日志），也不会进入 Referer；
    - 设备长期密钥**不进入** URL，URL 里只有短时一次性票据；
    - 非回环地址与远程 / NAS 路径一律不注入；
    - 签发失败时保持原 URL 启动，不阻断服务。

    票据不写入 stdout、日志或任何响应体。
    """
    parts = urlsplit(base_url)
    if not _is_loopback_host(parts.hostname or ""):
        return base_url
    try:
        from gw.core import trusted_local

        if not trusted_local.trusted_local_enabled():
            return base_url
        ticket = trusted_local.issue_bootstrap_ticket()
    except Exception:
        # 便利功能失败不得影响服务启动：回落到普通登录路径。
        return base_url
    return urlunsplit(
        (parts.scheme, parts.netloc, parts.path, parts.query, f"gw_trusted={ticket}")
    )


def _open_browser_when_ready(url: str, health_url: str, timeout: float = 30.0) -> None:
    """后台等待 /healthz 后打开浏览器；失败只影响便利功能，不影响服务。"""
    def worker() -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                with urlopen(health_url, timeout=1.0) as response:  # noqa: S310  # local health check
                    if 200 <= response.status < 300:
                        webbrowser.open(url)
                        return
            except Exception:  # noqa: S110  # ignore health check failures
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
    if args.version and (args.self_check or args.migrate or args.reset is not None or args.open_browser):
        parser.error("--version 不得与其它执行动作同时使用")
    if args.expect_version and not args.self_check:
        parser.error("--expect-version 只能与 --self-check 一起使用")
    if args.self_check and (args.migrate or args.reset is not None or args.open_browser):
        parser.error("--self-check 不得与 --migrate、--reset 或 --open-browser 同时使用")
    if args.version:
        info = resolve_version()
        print(
            json.dumps(
                {
                    "product_version": info.product_version,
                    "app_version": info.app_version,
                    "provenance": info.provenance,
                    "source": info.source,
                    "error": info.error,
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
            flush=True,
        )
        return 0
    if args.self_check:
        return _self_check(args)
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

    host = _resolve_host(args.host)
    from gw.core.auth import validate_auth_mode_host
    from gw.core.config import resolve_auth_mode

    try:
        validate_auth_mode_host(resolve_auth_mode(), host)
    except ValueError as exc:
        parser.error(str(exc))

    try:
        paths = _prepare_paths(mode)
    except RuntimePathError as exc:
        parser.error(str(exc))

    from gw.core.storage import DataDirLockedError, acquire_data_dir_lock
    try:
        data_lock = acquire_data_dir_lock(paths.data_root)  # noqa: F841 进程存活期间持有
    except DataDirLockedError as exc:
        parser.error(str(exc))

    # 根路径验证和目录准备完成后才允许导入应用。
    from gw.api.app import app

    version_info = resolve_version()
    _apply_version_to_app(app, version_info)
    if version_info.error:
        print(f"版本来源不可用: {version_info.error}", file=sys.stderr, flush=True)

    try:
        port = args.port if args.port is not None else int(os.environ.get("GW_PORT", str(DEFAULT_PORT)))
    except ValueError:
        parser.error("GW_PORT 必须是整数")
    if not 1 <= port <= 65535:
        parser.error("端口必须在 1 到 65535 之间")

    browser_host = "127.0.0.1" if host == "0.0.0.0" else host
    browser_base_url = f"http://{browser_host}:{port}/static/pages/index.html"
    print("=" * 60, flush=True)
    print("  Gods' Workbench Cleanroom", flush=True)
    print(f"  运行态: {mode}", flush=True)
    print(f"  数据根: {paths.data_root}", flush=True)
    print(f"  账户库: {paths.auth_db}", flush=True)
    print(f"  视频根: {paths.video_root}", flush=True)
    print(f"  版本: {version_info.app_version}（来源 {version_info.source}）", flush=True)
    print(f"  服务地址: http://{host}:{port}", flush=True)
    if host == "0.0.0.0":
        lan_ip = _get_lan_ip()
        if lan_ip:
            print(f"  局域网访问地址: http://{lan_ip}:{port}", flush=True)
    print("=" * 60, flush=True)
    if args.open_browser:
        # 仅在真正唤起浏览器时才签发一次性票据；票据只出现在 URL 片段里。
        browser_url = _trusted_local_browser_url(browser_base_url)
        _open_browser_when_ready(browser_url, f"http://{browser_host}:{port}/healthz")
    uvicorn.run(app, host=host, port=port, workers=1, reload=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
