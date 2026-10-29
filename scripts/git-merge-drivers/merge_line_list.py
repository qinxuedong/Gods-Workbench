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

"""行式白名单文件（如 ``tests/frontend/node-files.txt``）合并驱动。

用法（由 Git 调用，不手工执行）::

    merge_line_list.py %O %A %B %P

行为
----
- 文件顶部的注释 / 空行块原样保留（优先取 ours，其次 theirs）；
- 其余有效行取并集后排序、去重；
- 始终以单个换行结尾。文件按 UTF-8 读写，不做平台行尾转换。
"""

from __future__ import annotations

import sys
from pathlib import Path

import merge_common


def _split_header(lines: list[str]) -> tuple[list[str], list[str]]:
    """拆出顶部注释/空行块与数据行。"""
    header: list[str] = []
    index = 0
    while index < len(lines):
        stripped = lines[index].strip()
        if not stripped or stripped.startswith("#"):
            header.append(lines[index].rstrip())
            index += 1
            continue
        break
    return header, lines[index:]


def merge_line_text(ours: str, theirs: str) -> str:
    """返回合并后的文本：顶部头部 + 排序去重的数据行。"""
    ours_lines = ours.splitlines()
    theirs_lines = theirs.splitlines()
    ours_header, _ = _split_header(ours_lines)
    theirs_header, _ = _split_header(theirs_lines)
    header = ours_header or theirs_header

    data: set[str] = set()
    for line in [*ours_lines, *theirs_lines]:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        data.add(stripped)

    body = sorted(data)
    merged = [*header, *body]
    return "\n".join(merged) + "\n"


def main(argv: list[str]) -> int:
    if len(argv) < 4:
        print("用法: merge_line_list.py %O %A %B %P", file=sys.stderr)
        return 2
    ours_path = Path(argv[2])
    theirs_path = Path(argv[3])
    try:
        ours = ours_path.read_text(encoding="utf-8")
        theirs = theirs_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        print(f"[merge-lines] 读取失败: {exc}", file=sys.stderr)
        return 1
    merge_common.write_text(ours_path, merge_line_text(ours, theirs))
    return 0


if __name__ == "__main__":  # pragma: no cover - Git/命令行入口
    raise SystemExit(main(sys.argv))
