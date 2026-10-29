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

"""洁净室登记自检与修复脚本。

背景
----
Git 合并驱动只在**本地** ``git merge`` / ``git rebase`` 时生效；GitHub 网页端
合并与 CI 都不会调用它们。因此必须再有一道可在任意环境执行的自检：

- ``--check``：检查基础账本钉、逐条 sha256、行式白名单排序去重与结构合法性；
- ``--fix``：按工作树真实内容重算全部 ``path`` + ``sha256`` 条目，排序去重
  行式白名单，并重写基础账本钉。

用法::

    python3 scripts/fix-hygiene-registry.py --check
    python3 scripts/fix-hygiene-registry.py --fix
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path, PurePosixPath
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
DRIVER_DIR = REPO_ROOT / "scripts" / "git-merge-drivers"
if str(DRIVER_DIR) not in sys.path:
    sys.path.insert(0, str(DRIVER_DIR))

import merge_common  # noqa: E402

BASE_LEDGER_PATH = merge_common.BASE_LEDGER_PATH
PIN_FILE_PATH = merge_common.PIN_FILE_PATH
SHARD_DIR_PATH = "tests/hygiene/fixtures/admissions"

# 仅登记**行式**白名单文件；Python 集合形式的白名单不适用。
LINE_ALLOWLIST_FILES = ("tests/frontend/node-files.txt",)

FIX_COMMAND = "python3 scripts/fix-hygiene-registry.py --fix"


def _load_admission_module(repo_root: Path) -> Any:
    module_path = repo_root / "tests" / "hygiene" / "cleanroom_admission.py"
    spec = importlib.util.spec_from_file_location(
        "_gw_cleanroom_admission_for_fix", module_path
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"无法加载准入模块: {module_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _registry_json_paths(repo_root: Path) -> list[Path]:
    paths = [repo_root.joinpath(*BASE_LEDGER_PATH.split("/"))]
    shard_dir = repo_root.joinpath(*SHARD_DIR_PATH.split("/"))
    if shard_dir.is_dir():
        paths.extend(sorted(shard_dir.glob("issue-*.json")))
    return paths


def _iter_hashed_entries(payload: Any, label: str) -> list[tuple[str, dict[str, Any]]]:
    found: list[tuple[str, dict[str, Any]]] = []
    if isinstance(payload, dict):
        if isinstance(payload.get("path"), str) and isinstance(
            payload.get("sha256"), str
        ):
            found.append((label, payload))
        for key, value in payload.items():
            found.extend(_iter_hashed_entries(value, f"{label}.{key}"))
    elif isinstance(payload, list):
        for index, value in enumerate(payload):
            found.extend(_iter_hashed_entries(value, f"{label}[{index}]"))
    return found


def _split_header(lines: list[str]) -> list[str]:
    header: list[str] = []
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            header.append(line.rstrip())
            continue
        break
    return header


def _normalized_line_file(text: str) -> str:
    lines = text.splitlines()
    header = _split_header(lines)
    body = sorted(
        {
            line.strip()
            for line in lines
            if line.strip() and not line.strip().startswith("#")
        }
    )
    merged = [*header, *body]
    if not merged:
        return ""
    return "\n".join(merged) + "\n"


def _check_line_file(repo_root: Path, rel_path: str) -> list[str]:
    path = repo_root.joinpath(*rel_path.split("/"))
    if not path.is_file():
        return [f"{rel_path}: 行式白名单文件缺失"]
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    header = _split_header(lines)
    body = [line.strip() for line in lines if line.strip() and not line.strip().startswith("#")]
    problems: list[str] = []
    duplicates = sorted({line for line in body if body.count(line) > 1})
    if duplicates:
        problems.append(f"{rel_path}: 存在重复行 {duplicates}")
    if body != sorted(body):
        problems.append(f"{rel_path}: 行未按字典序排序")
    normalized = _normalized_line_file(text)
    if not text.endswith("\n") and normalized:
        problems.append(f"{rel_path}: 缺少结尾换行")
    expected_prefix = "\n".join(header)
    if header and not text.startswith(expected_prefix):
        problems.append(f"{rel_path}: 顶部注释块格式异常")
    return problems


def check_registry(repo_root: Path) -> list[str]:
    """返回所有问题；空列表表示登记与工作树一致。"""
    problems: list[str] = []
    admission = _load_admission_module(repo_root)

    base_path = repo_root.joinpath(*BASE_LEDGER_PATH.split("/"))
    if not base_path.is_file():
        return [f"缺少基础账本: {BASE_LEDGER_PATH}"]

    try:
        admission.load_admission_metadata(repo_root)
    except admission.AdmissionMetadataError as exc:
        problems.append(f"准入登记结构非法: {exc}")
    except (OSError, json.JSONDecodeError) as exc:
        problems.append(f"准入登记无法解析: {exc}")

    for path in _registry_json_paths(repo_root):
        rel_path = path.relative_to(repo_root).as_posix()
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            problems.append(f"{rel_path}: 无法解析 JSON（{exc}）")
            continue
        for label, entry in _iter_hashed_entries(payload, rel_path):
            target = repo_root.joinpath(*PurePosixPath(entry["path"]).parts)
            if not target.is_file():
                problems.append(f"{label}: 目标文件缺失，无法重算哈希: {entry['path']}")
                continue
            actual = merge_common.canonical_sha256(target)
            if entry["sha256"] != actual:
                problems.append(
                    f"{label}: 哈希不匹配 {entry['path']} "
                    f"(登记 {entry['sha256'][:12]}…, 实际 {actual[:12]}…)"
                )

    pin_file = repo_root.joinpath(*PIN_FILE_PATH.split("/"))
    if not pin_file.is_file():
        problems.append(f"缺少基础账本钉文件: {PIN_FILE_PATH}")
    else:
        match = merge_common._PIN_PATTERN.search(pin_file.read_text(encoding="utf-8"))
        if match is None:
            problems.append(
                f"{PIN_FILE_PATH}: 未找到 {merge_common.PIN_CONSTANT} 常量"
            )
        else:
            actual = merge_common.canonical_sha256(base_path)
            if match.group(2) != actual:
                problems.append(
                    f"{PIN_FILE_PATH}: {merge_common.PIN_CONSTANT} 与基础账本不一致 "
                    f"(登记 {match.group(2)[:12]}…, 实际 {actual[:12]}…)"
                )

    for rel_path in LINE_ALLOWLIST_FILES:
        problems.extend(_check_line_file(repo_root, rel_path))
    return problems


def _fix_json_hashes(repo_root: Path, path: Path) -> bool:
    payload = json.loads(path.read_text(encoding="utf-8"))
    changed = False
    for _label, entry in _iter_hashed_entries(payload, path.name):
        target = repo_root.joinpath(*PurePosixPath(entry["path"]).parts)
        if not target.is_file():
            continue
        actual = merge_common.canonical_sha256(target)
        if entry["sha256"] != actual:
            entry["sha256"] = actual
            changed = True
    if not changed:
        return False
    new_text = merge_common.dump_json(payload)
    if new_text != path.read_text(encoding="utf-8"):
        merge_common.write_text(path, new_text)
        return True
    return False


def _fix_line_file(repo_root: Path, rel_path: str) -> bool:
    path = repo_root.joinpath(*rel_path.split("/"))
    if not path.is_file():
        return False
    text = path.read_text(encoding="utf-8")
    normalized = _normalized_line_file(text)
    if normalized == text:
        return False
    merge_common.write_text(path, normalized)
    return True


def fix_registry(repo_root: Path) -> list[str]:
    """就地修复可自动修复的问题，返回改动过的相对路径。"""
    changed: list[str] = []
    for path in _registry_json_paths(repo_root):
        if path.is_file() and _fix_json_hashes(repo_root, path):
            changed.append(path.relative_to(repo_root).as_posix())
    for rel_path in LINE_ALLOWLIST_FILES:
        if _fix_line_file(repo_root, rel_path):
            changed.append(rel_path)
    base_path = repo_root.joinpath(*BASE_LEDGER_PATH.split("/"))
    pin_file = repo_root.joinpath(*PIN_FILE_PATH.split("/"))
    if base_path.is_file() and pin_file.is_file():
        before = pin_file.read_text(encoding="utf-8")
        merge_common.update_base_pin(repo_root)
        if pin_file.read_text(encoding="utf-8") != before:
            changed.append(PIN_FILE_PATH)
    return changed


def _print_problems(problems: list[str]) -> None:
    print(f"发现 {len(problems)} 个卫生登记问题：", file=sys.stderr)
    for problem in problems:
        print(f"  - {problem}", file=sys.stderr)
    print("", file=sys.stderr)
    print("一条命令修复（在仓库根目录执行）：", file=sys.stderr)
    print(f"  {FIX_COMMAND}", file=sys.stderr)
    print(
        "或者安装本地合并驱动后执行：git fetch && git rebase origin/master",
        file=sys.stderr,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="洁净室登记自检 / 修复")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true", help="只检查，不修改文件")
    mode.add_argument("--fix", action="store_true", help="重算哈希并排序去重后写回")
    parser.add_argument(
        "--repo", type=Path, default=REPO_ROOT, help="仓库根目录（默认脚本所在仓库）"
    )
    args = parser.parse_args(argv)
    repo_root = args.repo.resolve()

    if args.fix:
        changed = fix_registry(repo_root)
        for rel_path in changed:
            print(f"已修复: {rel_path}")
        problems = check_registry(repo_root)
        if problems:
            _print_problems(problems)
            return 1
        print("修复完成，登记与工作树一致。")
        return 0

    problems = check_registry(repo_root)
    if problems:
        _print_problems(problems)
        return 1
    print("卫生登记自检通过：基础账本钉、逐条哈希与行式白名单均与工作树一致。")
    return 0


if __name__ == "__main__":  # pragma: no cover - 命令行入口
    raise SystemExit(main())
