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

"""洁净室登记 JSON 合并驱动（基础账本与票面分片）。

用法（由 Git 调用，不手工执行）::

    merge_json_registry.py %O %A %B %P

合并口径
--------
- 顶层键取两侧并集，键序以 ours 为主、按 theirs 的补充键顺序追加；
- 对象列表按稳定键并集：``path``（admitted_files / accepted_test_files 等）、
  ``issue``（authorized_issue_file_sets）、``source_path``（excluded_artifacts）；
- 纯字符串列表取并集后排序去重；
- 标量冲突保留 ours（``schema`` 不一致则视为致命冲突）；
- 所有带 ``path`` + ``sha256`` 的对象，其 ``sha256`` 一律按工作树真实内容重算，
  **从不信任任何一侧的旧哈希**；目标文件缺失时保留原哈希并告警，交给校验脚本
  显式失败；
- 若被合并的是基础账本，写出结果后同步重写 ``test_cleanroom_hygiene.py`` 里的
  ``ADMISSION_BASE_METADATA_SHA256``。
"""

from __future__ import annotations

import copy
import sys
from pathlib import Path, PurePosixPath
from typing import Any

import merge_common
from merge_common import MergeDriverError, canonical_sha256

KEY_CANDIDATES = ("path", "issue", "source_path")


def _ordered_keys(ours: dict[str, Any], theirs: dict[str, Any]) -> list[str]:
    keys = list(ours)
    keys.extend(key for key in theirs if key not in ours)
    return keys


def _all_strings(items: list[Any]) -> bool:
    return all(isinstance(item, str) for item in items)


def _all_dicts(items: list[Any]) -> bool:
    return all(isinstance(item, dict) for item in items)


def _common_key(items: list[dict[str, Any]]) -> str | None:
    for candidate in KEY_CANDIDATES:
        if all(candidate in item for item in items):
            return candidate
    return None


def _merge_string_list(ours: list[str], theirs: list[str]) -> list[str]:
    if ours == theirs:
        return copy.deepcopy(ours)
    return sorted(set(ours) | set(theirs))


def _merge_lists(
    ours: list[Any],
    theirs: list[Any],
    label: str,
    fatal: list[str],
    warnings: list[str],
) -> list[Any]:
    if ours == theirs:
        return copy.deepcopy(ours)
    if _all_strings(ours) and _all_strings(theirs):
        return _merge_string_list(ours, theirs)
    if _all_dicts(ours) and _all_dicts(theirs):
        key = _common_key([*ours, *theirs])
        if key is not None:
            merged_items: list[dict[str, Any]] = []
            positions: dict[Any, int] = {}
            for item in ours:
                item_key = item.get(key)
                if item_key in positions:
                    # 同一侧自身重复不应静默丢数据：交给校验脚本报错。
                    warnings.append(f"{label}: ours 内重复键 {key}={item_key!r}")
                    continue
                positions[item_key] = len(merged_items)
                merged_items.append(copy.deepcopy(item))
            for item in theirs:
                item_key = item.get(key)
                if item_key in positions:
                    index = positions[item_key]
                    merged_items[index] = _merge_dicts(
                        merged_items[index],
                        item,
                        f"{label}[{key}={item_key!r}]",
                        fatal,
                        warnings,
                    )
                else:
                    positions[item_key] = len(merged_items)
                    merged_items.append(copy.deepcopy(item))
            return merged_items
        fatal.append(f"{label}: 对象列表缺少稳定键 {KEY_CANDIDATES}")
        return copy.deepcopy(ours)
    fatal.append(f"{label}: 两侧列表元素类型不一致")
    return copy.deepcopy(ours)


def _merge_dicts(
    ours: dict[str, Any],
    theirs: dict[str, Any],
    label: str,
    fatal: list[str],
    warnings: list[str],
) -> dict[str, Any]:
    merged: dict[str, Any] = {}
    for key in _ordered_keys(ours, theirs):
        item_label = f"{label}.{key}" if label else key
        if key not in ours:
            merged[key] = copy.deepcopy(theirs[key])
            continue
        if key not in theirs:
            merged[key] = copy.deepcopy(ours[key])
            continue
        left = ours[key]
        right = theirs[key]
        if key == "schema":
            if left != right:
                fatal.append(
                    f"{item_label}: schema 不一致（ours={left!r}, theirs={right!r}）"
                )
            merged[key] = copy.deepcopy(left)
            continue
        if isinstance(left, list) and isinstance(right, list):
            merged[key] = _merge_lists(
                left,
                right,
                item_label,
                fatal,
                warnings,
            )
        elif isinstance(left, dict) and isinstance(right, dict):
            merged[key] = _merge_dicts(left, right, item_label, fatal, warnings)
        elif left == right:
            merged[key] = copy.deepcopy(left)
        elif isinstance(left, (dict, list)) or isinstance(right, (dict, list)):
            fatal.append(f"{item_label}: 两侧类型不一致")
            merged[key] = copy.deepcopy(left)
        else:
            warnings.append(f"{item_label}: 标量冲突，保留 ours={left!r}")
            merged[key] = copy.deepcopy(left)
    return merged


def _rehash_entries(payload: Any, repo_root: Path, warnings: list[str], label: str = "") -> None:
    """递归重算所有 ``path`` + ``sha256`` 对象的哈希。"""
    if isinstance(payload, dict):
        path_value = payload.get("path")
        hash_value = payload.get("sha256")
        if isinstance(path_value, str) and isinstance(hash_value, str):
            target = repo_root.joinpath(*PurePosixPath(path_value).parts)
            if target.is_file():
                payload["sha256"] = canonical_sha256(target)
            else:
                warnings.append(
                    f"{label or path_value}: 目标文件缺失，保留原 sha256，等待校验失败"
                )
        for key, value in payload.items():
            _rehash_entries(value, repo_root, warnings, f"{label}.{key}" if label else key)
    elif isinstance(payload, list):
        for index, value in enumerate(payload):
            _rehash_entries(value, repo_root, warnings, f"{label}[{index}]")


def merge_payloads(
    ours: Any,
    theirs: Any,
    repo_root: Path,
) -> tuple[dict[str, Any], list[str], list[str]]:
    """合并两份已解析的 JSON，返回 ``(结果, 致命问题, 告警)``。"""
    if ours is None and theirs is None:
        raise MergeDriverError("两侧 JSON 均为空，无法合并")
    fatal: list[str] = []
    warnings: list[str] = []
    if ours is None:
        merged = copy.deepcopy(theirs)
    elif theirs is None:
        merged = copy.deepcopy(ours)
    elif not isinstance(ours, dict) or not isinstance(theirs, dict):
        raise MergeDriverError("登记文件顶层必须是 JSON 对象")
    else:
        merged = _merge_dicts(ours, theirs, "", fatal, warnings)
    if fatal:
        return merged, fatal, warnings
    _rehash_entries(merged, repo_root, warnings)
    return merged, fatal, warnings


def _summary_warnings(warnings: list[str]) -> None:
    for warning in warnings:
        print(f"[merge-json] 警告: {warning}", file=sys.stderr)


def main(argv: list[str]) -> int:
    if len(argv) < 5:
        print(
            "用法: merge_json_registry.py %O %A %B %P",
            file=sys.stderr,
        )
        return 2
    base_path, ours_path, theirs_path, rel_path = (
        Path(argv[1]),
        Path(argv[2]),
        Path(argv[3]),
        argv[4],
    )
    repo_root = merge_common.working_tree_root()
    try:
        base = merge_common.read_json(base_path)
        ours = merge_common.read_json(ours_path)
        theirs = merge_common.read_json(theirs_path)
        merged, fatal, warnings = merge_payloads(ours, theirs, repo_root)
    except MergeDriverError as exc:
        print(f"[merge-json] 合并失败: {exc}", file=sys.stderr)
        return 1

    _summary_warnings(warnings)
    if fatal:
        for problem in fatal:
            print(f"[merge-json] 冲突: {problem}", file=sys.stderr)
        return 1

    # base 仅用于诊断：本驱动按需求取 ours ∪ theirs，不复活双方都删除的条目。
    if base is not None and not isinstance(base, dict):
        print("[merge-json] 警告: 基线不是 JSON 对象，已忽略", file=sys.stderr)

    merge_common.write_text(ours_path, merge_common.dump_json(merged))
    if merge_common.is_base_ledger(rel_path):
        # Git 传入的 %A 可能是临时文件；直接以刚写出的合并结果计算钉值。
        if not merge_common.update_base_pin(repo_root, ours_path):
            print(
                "[merge-json] 警告: 未能更新 ADMISSION_BASE_METADATA_SHA256，"
                "请运行 scripts/fix-hygiene-registry.py --fix",
                file=sys.stderr,
            )
    return 0


if __name__ == "__main__":  # pragma: no cover - Git/命令行入口
    raise SystemExit(main(sys.argv))
