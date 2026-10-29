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

"""在当前仓库的**本地** Git 配置中安装洁净室登记合并驱动。

安装内容::

    git config merge.hygiene-json.name  ...
    git config merge.hygiene-json.driver "<python> <abs>/merge_json_registry.py %O %A %B %P"
    git config merge.hygiene-lines.name ...
    git config merge.hygiene-lines.driver "<python> <abs>/merge_line_list.py %O %A %B %P"

这些配置只写入 ``.git/config``，不随仓库提交；换机器或移动仓库后需重新运行。
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

DRIVERS = (
    (
        "hygiene-json",
        "Gods-Workbench cleanroom admission JSON union merge driver",
        "merge_json_registry.py",
    ),
    (
        "hygiene-lines",
        "Gods-Workbench line allowlist union merge driver",
        "merge_line_list.py",
    ),
)


def _git(*args: str) -> str:
    result = subprocess.run(
        ["git", *args], check=True, capture_output=True, text=True
    )
    return result.stdout.strip()


def _quote(value: str) -> str:
    return '"' + value.replace('"', '\\"') + '"'


def main() -> int:
    try:
        repo_root = Path(_git("rev-parse", "--show-toplevel"))
    except (OSError, subprocess.CalledProcessError) as exc:
        print(f"必须在 Git 仓库内运行：{exc}", file=sys.stderr)
        return 1

    scripts = repo_root / "scripts" / "git-merge-drivers"
    for name, description, script in DRIVERS:
        driver_path = (scripts / script).resolve()
        if not driver_path.is_file():
            print(f"缺少驱动脚本：{driver_path}", file=sys.stderr)
            return 1
        command = f"{_quote(sys.executable)} {_quote(str(driver_path))} %O %A %B %P"
        _git("config", f"merge.{name}.name", description)
        _git("config", f"merge.{name}.driver", command)
        print(f"已安装 merge.{name}.driver")
        print(f"  {command}")
    print("完成。该配置仅对当前仓库的本地 Git 生效。")
    return 0


if __name__ == "__main__":  # pragma: no cover - 命令行入口
    raise SystemExit(main())
