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

"""服务端系统原生目录选择器（QIN-432）。

目录设置页在服务器本机会话中优先弹出操作系统原生"选择文件夹"对话框，
避免页面内目录浏览器的样式与交互问题。实现约束：

- 在独立子进程中弹出对话框：tkinter 崩溃、缺显示环境都不会波及服务进程；
- 子进程置顶并受超时保护，绝不阻塞事件循环或静默挂起；
- 无图形环境、依赖缺失、超时与用户取消分开返回，由调用方决定降级；
- 参数经 argv 传入子进程且不经过 shell，不存在注入面。
"""

from __future__ import annotations

import json
import subprocess
import sys

# 子进程脚本：只读 argv，输出单行 JSON 结果。
# 退出码 0 = 正常输出；2 = tkinter 不可用/无显示环境。
_PICKER_CHILD_SCRIPT = r"""
import json
import sys


def main() -> int:
    title = sys.argv[1]
    initial_dir = sys.argv[2] or None
    try:
        from tkinter import Tk, filedialog
    except Exception:
        print(json.dumps({"status": "unavailable", "reason": "tkinter-unavailable"}))
        return 0
    try:
        root = Tk()
    except Exception:
        print(json.dumps({"status": "unavailable", "reason": "no-display"}))
        return 0
    try:
        root.withdraw()
        root.attributes("-topmost", True)
        root.focus_force()
        try:
            if initial_dir:
                root.geometry(f"1x1+{root.winfo_screenwidth() // 3}+{root.winfo_screenheight() // 3}")
        except Exception:
            pass
        try:
            raw = filedialog.askdirectory(initialdir=initial_dir, title=title, parent=root)
        except Exception:
            print(json.dumps({"status": "unavailable", "reason": "dialog-error"}))
            return 0
        finally:
            root.destroy()
    except Exception:
        print(json.dumps({"status": "unavailable", "reason": "dialog-error"}))
        return 0
    if raw:
        print(json.dumps({"status": "selected", "path": str(raw)}))
    else:
        print(json.dumps({"status": "cancelled"}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
"""


def pick_directory(
    title: str,
    initial_dir: str = "",
    timeout_seconds: int = 60,
) -> dict[str, str | None]:
    """弹出系统原生选择文件夹对话框，返回结构化结果。

    返回字段固定为 ``status``/``path``/``reason``：
    - ``selected``：用户选定目录，``path`` 为所选绝对路径；
    - ``cancelled``：用户关闭对话框，不改字段、不报错；
    - ``unavailable``：超时、无图形环境或依赖缺失，``reason`` 说明原因。
    """
    try:
        completed = subprocess.run(
            [
                sys.executable,
                "-c",
                _PICKER_CHILD_SCRIPT,
                title,
                initial_dir or "",
            ],
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
            shell=False,
        )
    except subprocess.TimeoutExpired:
        return {"status": "unavailable", "path": None, "reason": "timeout"}
    except OSError:
        return {"status": "unavailable", "path": None, "reason": "spawn-failed"}
    if completed.returncode != 0:
        return {"status": "unavailable", "path": None, "reason": "picker-exited"}
    try:
        payload = json.loads(completed.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return {"status": "unavailable", "path": None, "reason": "invalid-output"}
    status = str(payload.get("status") or "")
    if status == "selected":
        path = str(payload.get("path") or "").strip()
        if not path:
            return {"status": "unavailable", "path": None, "reason": "invalid-output"}
        return {"status": "selected", "path": path, "reason": None}
    if status == "cancelled":
        return {"status": "cancelled", "path": None, "reason": None}
    return {
        "status": "unavailable",
        "path": None,
        "reason": str(payload.get("reason") or "picker-failed"),
    }
