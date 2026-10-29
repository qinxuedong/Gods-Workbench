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

"""洁净室登记合并驱动的公共工具。

只依赖 Python 3.11 标准库。所有脚本都假定 Git 以**工作树根目录**为当前目录
调用合并驱动（这是 Git 对合并驱动的既定行为），因此这里统一用 ``Path.cwd()``
定位被合并的文件与待重算哈希的目标文件。

哈希算法必须与仓库唯一来源 ``tests/hygiene/cleanroom_admission.py`` 的
``canonical_sha256`` 完全一致：先把 CRLF / CR 归一为 LF，再取 SHA-256。
本模块优先从驱动脚本自身所在的仓库导入该实现；仅在极端情况下（脚本被单独
拷出仓库且工作树中也没有该模块）才退回一份逐字节相同的本地实现。
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import re
from pathlib import Path
from typing import Any

BASE_LEDGER_PATH = "tests/hygiene/fixtures/cleanroom_admission_metadata.json"
PIN_FILE_PATH = "tests/hygiene/test_cleanroom_hygiene.py"
PIN_CONSTANT = "ADMISSION_BASE_METADATA_SHA256"

_PIN_PATTERN = re.compile(
    r"("
    + PIN_CONSTANT
    + r"\s*=\s*\(?\s*\")([0-9a-f]{64})(\")"
)


class MergeDriverError(RuntimeError):
    """合并驱动无法继续；调用方应返回非零让 Git 保留冲突。"""


def fallback_canonical_sha256(path: Path) -> str:
    """与 ``cleanroom_admission.canonical_sha256`` 逐字节一致的兜底实现。"""
    raw = path.read_bytes().replace(b"\r\n", b"\n").replace(b"\r", b"\n")
    return hashlib.sha256(raw).hexdigest()


def _import_canonical_sha256() -> Any:
    """优先导入仓库唯一来源；返回 ``canonical_sha256`` 可调用对象。"""
    candidates = [
        Path(__file__).resolve().parents[2] / "tests" / "hygiene" / "cleanroom_admission.py",
        Path.cwd() / "tests" / "hygiene" / "cleanroom_admission.py",
    ]
    seen: set[Path] = set()
    for candidate in candidates:
        if candidate in seen or not candidate.is_file():
            continue
        seen.add(candidate)
        spec = importlib.util.spec_from_file_location(
            "_gw_cleanroom_admission_for_merge", candidate
        )
        if spec is None or spec.loader is None:
            continue
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        canonical = getattr(module, "canonical_sha256", None)
        if callable(canonical):
            return canonical
    return fallback_canonical_sha256


canonical_sha256 = _import_canonical_sha256()


def driver_repo_root() -> Path:
    """驱动脚本所在仓库的根目录（scripts/git-merge-drivers 的上两级）。"""
    return Path(__file__).resolve().parents[2]


def working_tree_root() -> Path:
    """Git 合并驱动的当前工作树根目录。"""
    return Path.cwd()


def normalize_rel_path(value: str) -> str:
    """把 Git 传入的 ``%P`` 统一成正斜杠相对路径。"""
    return value.replace("\\", "/")


def is_base_ledger(rel_path: str) -> bool:
    return normalize_rel_path(rel_path) == BASE_LEDGER_PATH


def read_json(path: Path) -> Any | None:
    """读取 JSON；空文件视为「不存在」（add/add 场景下 ``%O`` 可能为空）。"""
    if not path.is_file():
        return None
    raw = path.read_text(encoding="utf-8")
    if not raw.strip():
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise MergeDriverError(f"不是合法 JSON（{path}）: {exc}") from exc


def dump_json(payload: Any) -> str:
    """按仓库既有格式输出：UTF-8 原文、2 空格缩进、保留键序、结尾换行。"""
    return json.dumps(payload, ensure_ascii=False, indent=2) + "\n"


def write_text(path: Path, text: str) -> None:
    """以 UTF-8 写回且不做平台行尾转换，保证哈希稳定。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        handle.write(text)


def update_base_pin(repo_root: Path, ledger_source: Path | None = None) -> bool:
    """用合并后基础账本的真实内容重写 ``ADMISSION_BASE_METADATA_SHA256``。

    ``ledger_source`` 用于合并驱动场景：Git 传入的 ``%A`` 可能是临时文件，
    此时待钉的内容在 ``%A`` 而不在工作树账本路径上。省略时读取工作树账本。

    返回是否成功改写。基础账本或钉文件不存在时视为无需改写；钉常量缺失时
    返回 ``False`` 并由调用方打印告警（后续 ``--check`` 会显式失败）。
    """
    ledger = ledger_source or repo_root.joinpath(*BASE_LEDGER_PATH.split("/"))
    pin_file = repo_root.joinpath(*PIN_FILE_PATH.split("/"))
    if not ledger.is_file() or not pin_file.is_file():
        return False
    digest = canonical_sha256(ledger)
    text = pin_file.read_text(encoding="utf-8")
    updated, replacements = _PIN_PATTERN.subn(
        lambda match: f"{match.group(1)}{digest}{match.group(3)}", text, count=1
    )
    if replacements == 0:
        return False
    if updated != text:
        write_text(pin_file, updated)
    return True
