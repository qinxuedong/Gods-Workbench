#!/usr/bin/env python3
"""构建 Windows 安装版后端载荷、随包清单与 Tauri 发布配置。

本脚本只做构建期工作：
- 校验官方 CPython embeddable 归档与哈希锁 wheel；
- 在隔离 staging 目录拼装 backend/，不写用户数据；
- 生成 bundle-manifest.json、runtime-inventory.json 与 Tauri 发布配置；
- 扫描禁用内容并校验安装目录与数据根互不包含。

不签名、不上商店、不发布、不迁移用户数据。
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import zipfile
from email.parser import Parser
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[3]
PACKAGING = Path(__file__).resolve().parent
LOCK_PATH = PACKAGING / "runtime-lock.json"
COMPAT_BASELINE = "cc47d8d4ac74adc87f073127886e13e71ffbcd3aef3248a11bb957d55c4086e2"
PRODUCT_VERSION_RE = re.compile(r"^(?P<core>\d+\.\d+\.\d+)(?:-(?P<suffix>[0-9A-Za-z.-]+))?$")
BUILD_ID_RE = re.compile(r"^[0-9A-Za-z][0-9A-Za-z._-]{0,127}$")
GIT_COMMIT_RE = re.compile(r"^[0-9a-f]{7,40}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
DATA_DIR_NAME = "GodsWorkbench"
UPGRADE_PS1 = PACKAGING / "nsis" / "upgrade.ps1"
HOOKS_NSH = PACKAGING / "nsis" / "hooks.nsh"


class BuildError(RuntimeError):
    pass


def load_lock() -> dict[str, Any]:
    return json.loads(LOCK_PATH.read_text(encoding="utf-8"))


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_json(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def validate_product_version(value: str) -> str:
    match = PRODUCT_VERSION_RE.fullmatch(value.strip())
    if match is None:
        raise BuildError(f"产品版本格式非法: {value!r}")
    return value.strip()


def validate_build_id(value: str) -> str:
    if BUILD_ID_RE.fullmatch(value) is None:
        raise BuildError(f"build_id 格式非法: {value!r}")
    return value


def validate_git_commit(value: str) -> str:
    if GIT_COMMIT_RE.fullmatch(value) is None:
        raise BuildError("git_commit 必须是 7-40 位小写十六进制")
    return value


def resolve_git_commit(explicit: str | None) -> str:
    if explicit:
        return validate_git_commit(explicit.strip())
    result = subprocess.run(
        ["git", "-C", str(ROOT), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=10,
        check=False,
    )
    if result.returncode != 0:
        raise BuildError("无法解析 git_commit，请显式提供 --git-commit")
    return validate_git_commit(result.stdout.strip())


def normalize_for_compare(path: Path) -> Path:
    resolved = path.resolve(strict=False)
    text = os.path.normcase(str(resolved))
    return Path(text.rstrip("\\/"))


def paths_non_overlapping(left: Path, right: Path) -> bool:
    left_path = normalize_for_compare(left)
    right_path = normalize_for_compare(right)
    if left_path == right_path:
        return False
    try:
        left_path.relative_to(right_path)
        return False
    except ValueError:
        pass
    try:
        right_path.relative_to(left_path)
        return False
    except ValueError:
        pass
    return True


def production_data_root() -> Path:
    local_app_data = os.environ.get("LOCALAPPDATA", "").strip()
    if not local_app_data or not Path(local_app_data).is_absolute():
        raise BuildError("无法确定数据根：LOCALAPPDATA 不可用")
    return Path(local_app_data) / DATA_DIR_NAME


def assert_staging_not_in_data_root(staging: Path, install_dir: Path | None) -> None:
    data_root = production_data_root()
    if not paths_non_overlapping(staging, data_root):
        raise BuildError("staging 目录与生产数据根存在包含关系")
    if install_dir is not None and not paths_non_overlapping(install_dir, data_root):
        raise BuildError("安装目录与生产数据根存在包含关系")


def verify_runtime_archive(archive: Path, lock: dict[str, Any]) -> str:
    expected = str(lock["cpython"]["sha256"]).lower()
    if SHA256_RE.fullmatch(expected) is None:
        raise BuildError("runtime-lock 中的 CPython SHA256 非法")
    actual = sha256_file(archive)
    if actual != expected:
        raise BuildError("CPython 运行时归档哈希与 runtime-lock 不一致")
    return actual


def download_runtime(lock: dict[str, Any], destination: Path) -> Path:
    url = str(lock["cpython"]["url"])
    if not url.startswith("https://www.python.org/"):
        raise BuildError("CPython 下载地址不在 python.org 官方域名下")
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_suffix(destination.suffix + ".part")
    with urllib.request.urlopen(url, timeout=60) as response, partial.open("wb") as handle:
        shutil.copyfileobj(response, handle)
    verify_runtime_archive(partial, lock)
    partial.replace(destination)
    return destination


def lock_hashes() -> set[str]:
    lock_file = ROOT / str(load_lock()["wheel_lock"])
    if not lock_file.is_file():
        raise BuildError("找不到 requirements.lock.hashes")
    hashes: set[str] = set()
    for line in lock_file.read_text(encoding="utf-8").splitlines():
        for match in re.finditer(r"--hash=sha256:([0-9a-f]{64})", line):
            hashes.add(match.group(1))
    if not hashes:
        raise BuildError("哈希锁未包含任何 wheel 哈希")
    return hashes


def download_wheels(lock: dict[str, Any], wheelhouse: Path) -> None:
    platform = lock["wheel_platform"]
    lock_file = ROOT / str(lock["wheel_lock"])
    wheelhouse.mkdir(parents=True, exist_ok=True)
    command = [
        sys.executable,
        "-m",
        "pip",
        "download",
        "--require-hashes",
        "--only-binary=:all:",
        "--dest",
        str(wheelhouse),
        "--platform",
        str(platform["platform"]),
        "--python-version",
        str(platform["python_version"]),
        "--implementation",
        str(platform["implementation"]),
        "--abi",
        str(platform["abi"]),
        "-r",
        str(lock_file),
    ]
    result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", check=False)
    if result.returncode != 0:
        raise BuildError(f"哈希锁 wheel 下载失败: {result.stderr[-1200:]}")


def verify_wheelhouse(wheelhouse: Path) -> list[Path]:
    allowed = lock_hashes()
    wheels = sorted(wheelhouse.glob("*.whl"))
    if not wheels:
        raise BuildError("wheelhouse 为空，拒绝生成安装载荷")
    for wheel in wheels:
        digest = sha256_file(wheel)
        if digest not in allowed:
            raise BuildError(f"wheel 未出现在哈希锁中: {wheel.name}")
    for stray in wheelhouse.iterdir():
        if stray.is_file() and stray.suffix.lower() != ".whl":
            raise BuildError(f"wheelhouse 存在非 wheel 文件: {stray.name}")
    return wheels


def extract_runtime(archive: Path, python_dir: Path) -> None:
    with zipfile.ZipFile(archive) as bundle:
        for member in bundle.infolist():
            target = python_dir / member.filename
            if not target.resolve().is_relative_to(python_dir.resolve()):
                raise BuildError("CPython 归档包含越界路径")
        bundle.extractall(python_dir)
    pth_files = sorted(python_dir.glob("python*._pth"))
    if len(pth_files) != 1:
        raise BuildError("CPython embeddable 缺少唯一 _pth 文件")
    pth = pth_files[0]
    entries = [
        line.strip()
        for line in pth.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    entries = [entry for entry in entries if entry not in {"..", "Lib\\site-packages"}]
    entries.extend(["..", "Lib\\site-packages", "import site"])
    pth.write_text("\r\n".join(entries) + "\r\n", encoding="utf-8")


def install_wheels(wheels: Iterable[Path], site_packages: Path) -> None:
    site_packages.mkdir(parents=True, exist_ok=True)
    for wheel in wheels:
        with zipfile.ZipFile(wheel) as bundle:
            for member in bundle.infolist():
                if member.filename.endswith(".data/scripts/") or "/.data/scripts/" in member.filename:
                    continue
                target = site_packages / member.filename
                if not target.resolve().is_relative_to(site_packages.resolve()):
                    raise BuildError(f"wheel 包含越界路径: {wheel.name}")
            bundle.extractall(site_packages)


def copy_source_tree(staging_backend: Path, lock: dict[str, Any]) -> None:
    for relative in lock["source_paths"]:
        source = ROOT / relative
        if not source.exists():
            raise BuildError(f"缺少随包源码: {relative}")
        target = staging_backend / relative
        if source.is_dir():
            shutil.copytree(
                source,
                target,
                ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo", ".DS_Store"),
            )
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
    for relative in lock["helper_scripts"]:
        source = ROOT / relative
        if not source.is_file():
            raise BuildError(f"缺少助手脚本: {relative}")
        target = staging_backend / "tools" / source.name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)


def copy_license_files(staging_backend: Path, lock: dict[str, Any], python_dir: Path) -> list[str]:
    licenses_root = staging_backend / "licenses"
    licenses_root.mkdir(parents=True, exist_ok=True)
    copied: list[str] = []
    for relative in lock["license_files"]:
        source = ROOT / relative
        if not source.is_file():
            raise BuildError(f"缺少许可文件: {relative}")
        target = licenses_root / "Gods-Workbench" / source.name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        copied.append(target.relative_to(staging_backend).as_posix())
    cpython_license = python_dir / "LICENSE.txt"
    if not cpython_license.is_file():
        raise BuildError("CPython 运行时缺少 LICENSE.txt")
    target = licenses_root / f"CPython-{lock['cpython']['version']}" / "LICENSE.txt"
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(cpython_license, target)
    copied.append(target.relative_to(staging_backend).as_posix())
    site_packages = python_dir / "Lib" / "site-packages"
    for dist_info in sorted(site_packages.glob("*.dist-info")):
        license_dirs = [path for path in dist_info.iterdir() if path.is_dir() and path.name.lower() in {"license", "licenses"}]
        license_files = [
            path
            for path in dist_info.iterdir()
            if path.is_file() and path.name.upper().startswith(("LICENSE", "COPYING", "NOTICE"))
        ]
        candidates = license_dirs + license_files
        if not candidates:
            continue
        target_dir = licenses_root / dist_info.name
        for candidate in candidates:
            if candidate.is_dir():
                shutil.copytree(candidate, target_dir / candidate.name, dirs_exist_ok=True)
            else:
                target_dir.mkdir(parents=True, exist_ok=True)
                shutil.copy2(candidate, target_dir / candidate.name)
        copied.append(target_dir.relative_to(staging_backend).as_posix())
    return sorted(set(copied))


def _dist_info_metadata(dist_info: Path) -> dict[str, str]:
    metadata_path = dist_info / "METADATA"
    if not metadata_path.is_file():
        return {}
    message = Parser().parsestr(metadata_path.read_text(encoding="utf-8", errors="replace"))
    license_classifiers = [
        str(value).split("::")[-1].strip()
        for key, value in message.items()
        if key.lower() == "classifier" and str(value).startswith("License ::")
    ]
    project_urls = message.get_all("Project-URL") or []
    source = str(message.get("Home-page") or "").strip()
    if not source and project_urls:
        source = str(project_urls[0]).split(",", 1)[-1].strip()
    return {
        "name": str(message.get("Name") or "").strip(),
        "version": str(message.get("Version") or "").strip(),
        "license": str(message.get("License-Expression") or message.get("License") or "").strip()
        or ", ".join(license_classifiers),
        "author": str(
            message.get("Author")
            or message.get("Maintainer")
            or message.get("Author-email")
            or ""
        ).strip(),
        "source": source,
    }


def write_runtime_notices(
    staging_backend: Path,
    lock: dict[str, Any],
    python_dir: Path,
    license_paths: list[str],
) -> str:
    licenses_root = staging_backend / "licenses"
    site_packages = python_dir / "Lib" / "site-packages"
    rows: list[tuple[str, str, str, str, str, str]] = []
    for dist_info in sorted(site_packages.glob("*.dist-info")):
        metadata = _dist_info_metadata(dist_info)
        name = metadata.get("name") or dist_info.name.split("-")[0]
        version = metadata.get("version") or "unknown"
        license_name = metadata.get("license") or "见包内许可文件"
        author = metadata.get("author") or "见包内元数据"
        source = metadata.get("source") or f"https://pypi.org/project/{name}/{version}/"
        reference = licenses_root / dist_info.name
        if reference.is_dir():
            license_ref = f"`backend/licenses/{dist_info.name}/`"
        else:
            license_ref = "包内 `dist-info/METADATA`"
        rows.append((name, version, license_name, author, source, license_ref))
    lines = [
        "# Windows 安装版内置 Python 运行时组件清单",
        "",
        "本文件由安装构建自动生成，只登记实际随包组件，不构成法律合规结论。",
        f"CPython 运行时版本：{html.escape(str(lock['cpython']['version']))} "
        f"（Python-2.0；完整许可见 `backend/licenses/CPython-{html.escape(str(lock['cpython']['version']))}/LICENSE.txt`）。",
        "wheel 精确版本、SHA-256 与许可文件路径同时记录在 `backend/runtime-inventory.json`。",
        "",
        "| 组件 | 版本 | 许可（元数据） | 版权 / 维护者 | 来源 | 随包许可引用 |",
        "| :--- | :--- | :--- | :--- | :--- | :--- |",
    ]
    for name, version, license_name, author, source, license_ref in rows:
        lines.append(
            "| "
            + " | ".join(
                html.escape(value).replace("|", "\\|")
                for value in (name, version, license_name, author, source, license_ref)
            )
            + " |"
        )
    lines.extend(
        [
            "",
            "明确不随包：ComfyUI、FFmpeg / ffprobe 可执行文件、AI 模型权重与视频素材。",
            "随包源文件 `LICENSE` 位于 `backend/licenses/Gods-Workbench/LICENSE`。",
            "",
        ]
    )
    notices_path = licenses_root / "RUNTIME-COMPONENTS.md"
    notices_path.write_text(chr(10).join(lines), encoding="utf-8")
    relative = notices_path.relative_to(staging_backend).as_posix()
    if relative not in license_paths:
        license_paths.append(relative)
    return relative


def build_runtime_inventory(
    lock: dict[str, Any],
    archive_sha256: str,
    wheels: Iterable[Path],
    license_paths: list[str],
    notices_path: str,
) -> dict[str, Any]:
    components = []
    for wheel in sorted(wheels):
        components.append(
            {
                "name": wheel.name.split("-")[0],
                "wheel": wheel.name,
                "sha256": sha256_file(wheel),
                "size": wheel.stat().st_size,
            }
        )
    return {
        "schema_version": 1,
        "cpython": {
            "version": lock["cpython"]["version"],
            "platform": lock["cpython"]["platform"],
            "url": lock["cpython"]["url"],
            "sha256": archive_sha256,
            "license": lock["cpython"]["license"],
        },
        "wheel_lock": lock["wheel_lock"],
        "wheel_platform": lock["wheel_platform"],
        "wheels": components,
        "license_files": sorted(set(license_paths)),
        "notices": notices_path,
        "excluded_components": ["ComfyUI", "FFmpeg", "models", "AI provider binaries"],
    }


def scan_forbidden(staging: Path, lock: dict[str, Any]) -> None:
    segments = {str(item).lower() for item in lock["forbidden_segments"]}
    suffixes = {str(item).lower() for item in lock["forbidden_suffixes"]}
    for path in staging.rglob("*"):
        if not path.is_file():
            continue
        lower_name = path.name.lower()
        lower_parts = {part.lower() for part in path.relative_to(staging).parts}
        if lower_parts & segments or lower_name in segments:
            raise BuildError(f"随包内容包含禁用组件: {path.relative_to(staging)}")
        if path.suffix.lower() in suffixes:
            raise BuildError(f"随包内容包含禁用资源: {path.relative_to(staging)}")


def write_manifest(
    staging_backend: Path,
    version: str,
    build_id: str,
    git_commit: str,
    runtime_inventory_sha256: str,
) -> dict[str, Any]:
    manifest = {
        "schema_version": 1,
        "product_version": version,
        "build_id": build_id,
        "git_commit": git_commit,
        "capability_profile": "gw-compat-v1",
        "capability_digest": COMPAT_BASELINE,
        "runtime_inventory_sha256": runtime_inventory_sha256,
    }
    (staging_backend / "bundle-manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return manifest


def write_release_config(output: Path, staging_backend: Path, version: str) -> Path:
    if not UPGRADE_PS1.is_file() or not HOOKS_NSH.is_file():
        raise BuildError("缺少 NSIS 升级钩子或 PowerShell 自检脚本")
    config = {
        "version": version,
        "bundle": {
            "active": True,
            "targets": ["nsis"],
            "icon": [],
            "resources": {
                str(staging_backend): "backend",
                str(UPGRADE_PS1): "packaging/upgrade.ps1",
            },
            "windows": {
                "nsis": {
                    "installerHooks": str(HOOKS_NSH),
                    "installMode": "currentUser",
                }
            },
        },
    }
    config_path = output / "tauri.release.conf.json"
    config_path.write_text(json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return config_path


def run_self_check(staging_backend: Path, version: str, skip: bool) -> dict[str, Any] | None:
    if skip:
        return None
    python = staging_backend / "python" / "python.exe"
    if not python.is_file():
        raise BuildError("staging 缺少私有 Python")
    environment = dict(os.environ)
    environment["GW_RUNTIME_MODE"] = "prod"
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    result = subprocess.run(
        [str(python), "run.py", "--prod", "--self-check", "--expect-version", version],
        cwd=staging_backend,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
        env=environment,
        check=False,
    )
    if result.returncode != 0:
        raise BuildError(f"安装载荷自检失败: {result.stderr.strip()[-1200:]}")
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise BuildError("安装载荷自检输出不是 JSON") from exc


def build(args: argparse.Namespace) -> dict[str, Any]:
    version = validate_product_version(args.version)
    build_id = validate_build_id(args.build_id)
    git_commit = resolve_git_commit(args.git_commit)
    lock = load_lock()
    staging_root = (args.output / "staging").resolve()
    if staging_root.exists():
        shutil.rmtree(staging_root)
    staging_backend = staging_root / "backend"
    python_dir = staging_backend / "python"
    python_dir.mkdir(parents=True)
    assert_staging_not_in_data_root(staging_root, args.install_dir)

    archive = Path(args.runtime_archive).resolve() if args.runtime_archive else None
    if archive is None:
        archive = download_runtime(lock, staging_root.parent / "python-embed.zip")
    archive_sha256 = verify_runtime_archive(archive, lock)
    extract_runtime(archive, python_dir)

    wheelhouse = Path(args.wheelhouse).resolve() if args.wheelhouse else staging_root.parent / "wheelhouse"
    if args.download_wheels:
        download_wheels(lock, wheelhouse)
    wheels = verify_wheelhouse(wheelhouse)
    install_wheels(wheels, python_dir / "Lib" / "site-packages")

    copy_source_tree(staging_backend, lock)
    license_paths = copy_license_files(staging_backend, lock, python_dir)
    notices_path = write_runtime_notices(staging_backend, lock, python_dir, license_paths)
    inventory = build_runtime_inventory(lock, archive_sha256, wheels, license_paths, notices_path)
    inventory_sha256 = hashlib.sha256(canonical_json(inventory).encode("utf-8")).hexdigest()
    (staging_backend / "runtime-inventory.json").write_text(
        json.dumps(inventory, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    manifest = write_manifest(staging_backend, version, build_id, git_commit, inventory_sha256)
    scan_forbidden(staging_root, lock)
    self_check = run_self_check(staging_backend, version, args.skip_self_check)
    config_path = write_release_config(args.output, staging_backend, version)

    evidence = {
        "product_version": version,
        "build_id": build_id,
        "app_version": f"{version}+gw.{build_id}",
        "git_commit": git_commit,
        "manifest": manifest,
        "runtime_inventory_sha256": inventory_sha256,
        "runtime_inventory": inventory,
        "self_check": self_check,
        "staging_backend_sha256": {
            path.relative_to(staging_root).as_posix(): sha256_file(path)
            for path in sorted(staging_root.rglob("*"))
            if path.is_file()
        },
        "tauri_release_config": str(config_path),
    }
    (args.output / "release-evidence.json").write_text(
        json.dumps(evidence, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return evidence


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="构建神工坊 Windows 安装版载荷")
    parser.add_argument("--version", required=True, help="产品版本 X.Y.Z 或 X.Y.Z-suffix")
    parser.add_argument("--build-id", required=True, help="唯一构建标识")
    parser.add_argument("--output", type=Path, required=True, help="构建输出目录")
    parser.add_argument("--runtime-archive", help="官方 CPython embeddable 归档；缺省则下载")
    parser.add_argument("--wheelhouse", help="已下载的 wheel 目录；缺省为输出目录下 wheelhouse")
    parser.add_argument("--download-wheels", action="store_true", help="按哈希锁下载 Windows wheel")
    parser.add_argument("--git-commit", help="显式 git 提交；缺省读取当前 HEAD")
    parser.add_argument("--install-dir", type=Path, help="可选：构建期安装目录，用于互不包含校验")
    parser.add_argument("--skip-self-check", action="store_true", help="仅用于无 Python 运行的构建测试")
    args = parser.parse_args(argv)
    args.output = args.output.resolve()
    args.output.mkdir(parents=True, exist_ok=True)
    try:
        evidence = build(args)
    except BuildError as exc:
        print(f"构建失败: {exc}", file=sys.stderr)
        return 1
    print(
        json.dumps(
            {
                "ok": True,
                "product_version": evidence["product_version"],
                "app_version": evidence["app_version"],
                "tauri_release_config": evidence["tauri_release_config"],
                "runtime_inventory_sha256": evidence["runtime_inventory_sha256"],
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
