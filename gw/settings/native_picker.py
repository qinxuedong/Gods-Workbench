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
# 优先尝试 tkinter；在 Windows embeddable 等无 tkinter 环境下，回退标准库 ctypes
# 调用 Windows 原生 COM IFileOpenDialog（FOS_PICKFOLDERS），零新增第三方依赖。
_PICKER_CHILD_SCRIPT = r"""
import ctypes
from ctypes import wintypes
import json
import os
import sys


def _pick_win32_com(title: str, initial_dir: str | None) -> dict[str, str | None]:
    ole32 = ctypes.windll.ole32
    shell32 = ctypes.windll.shell32

    class GUID(ctypes.Structure):
        _fields_ = [
            ("Data1", wintypes.DWORD),
            ("Data2", wintypes.WORD),
            ("Data3", wintypes.WORD),
            ("Data4", ctypes.c_ubyte * 8),
        ]

        @classmethod
        def from_str(cls, text: str) -> "GUID":
            import uuid

            u = uuid.UUID(text)
            tail = (ctypes.c_ubyte * 8).from_buffer_copy(u.bytes[8:])
            return cls(u.time_low, u.time_mid, u.time_hi_version, tail)

    CLSID_FileOpenDialog = GUID.from_str("{DC1C5A9C-E88A-4DDE-A5A1-60F82A20AEF7}")
    IID_IFileOpenDialog = GUID.from_str("{D57C7288-D4AD-4768-BE02-9D969532D960}")
    IID_IShellItem = GUID.from_str("{43826D1E-E718-42EE-BC55-A1E261C37BFE}")

    COINIT_APARTMENTTHREADED = 0x2
    COINIT_DISABLE_OLE1DDE = 0x4
    CLSCTX_INPROC_SERVER = 0x1
    FOS_PICKFOLDERS = 0x00000020
    FOS_FORCEFILESYSTEM = 0x00000040
    FOS_NOCHANGEDIR = 0x00000008
    SIGDN_FILESYSPATH = 0x80058000
    HRESULT_CANCELLED = -2147023673  # 0x800704C7

    def vtable_call(obj_ptr: ctypes.c_void_p, index: int, restype, *argtypes):
        vtbl = ctypes.cast(obj_ptr, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p)))[0]
        fn_type = ctypes.WINFUNCTYPE(restype, ctypes.c_void_p, *argtypes)
        return fn_type(vtbl[index])

    ole32.CoInitializeEx.argtypes = [ctypes.c_void_p, wintypes.DWORD]
    ole32.CoInitializeEx.restype = ctypes.c_long
    ole32.CoCreateInstance.argtypes = [
        ctypes.POINTER(GUID),
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(GUID),
        ctypes.POINTER(ctypes.c_void_p),
    ]
    ole32.CoCreateInstance.restype = ctypes.c_long
    ole32.CoTaskMemFree.argtypes = [ctypes.c_void_p]
    ole32.CoTaskMemFree.restype = None

    co_hr = ole32.CoInitializeEx(None, COINIT_APARTMENTTHREADED | COINIT_DISABLE_OLE1DDE)
    should_uninit = co_hr in (0, 1)
    dialog_ptr = ctypes.c_void_p()
    folder_item = ctypes.c_void_p()
    result_item = ctypes.c_void_p()
    try:
        hr = ole32.CoCreateInstance(
            ctypes.byref(CLSID_FileOpenDialog),
            None,
            CLSCTX_INPROC_SERVER,
            ctypes.byref(IID_IFileOpenDialog),
            ctypes.byref(dialog_ptr),
        )
        if hr != 0 or not dialog_ptr.value:
            return {"status": "unavailable", "reason": "win32-com-unavailable"}

        options = wintypes.DWORD(0)
        get_options = vtable_call(dialog_ptr, 10, ctypes.c_long, ctypes.POINTER(wintypes.DWORD))
        if get_options(dialog_ptr, ctypes.byref(options)) == 0:
            set_options = vtable_call(dialog_ptr, 9, ctypes.c_long, wintypes.DWORD)
            set_options(dialog_ptr, options.value | FOS_PICKFOLDERS | FOS_FORCEFILESYSTEM | FOS_NOCHANGEDIR)

        if title:
            set_title = vtable_call(dialog_ptr, 17, ctypes.c_long, wintypes.LPCWSTR)
            set_title(dialog_ptr, title)

        if initial_dir and os.path.isdir(initial_dir):
            sh_create = shell32.SHCreateItemFromParsingName
            sh_create.argtypes = [wintypes.LPCWSTR, ctypes.c_void_p, ctypes.POINTER(GUID), ctypes.POINTER(ctypes.c_void_p)]
            sh_create.restype = ctypes.c_long
            if sh_create(initial_dir, None, ctypes.byref(IID_IShellItem), ctypes.byref(folder_item)) == 0 and folder_item.value:
                set_folder = vtable_call(dialog_ptr, 12, ctypes.c_long, ctypes.c_void_p)
                set_folder(dialog_ptr, folder_item)

        owner_hwnd = None
        try:
            user32 = ctypes.windll.user32
            user32.GetForegroundWindow.restype = wintypes.HWND
            owner_hwnd = user32.GetForegroundWindow() or None
        except Exception:
            owner_hwnd = None

        show = vtable_call(dialog_ptr, 3, ctypes.c_long, wintypes.HWND)
        show_hr = show(dialog_ptr, owner_hwnd)
        if show_hr == HRESULT_CANCELLED:
            return {"status": "cancelled"}
        if show_hr != 0:
            return {"status": "unavailable", "reason": "win32-dialog-error"}

        get_result = vtable_call(dialog_ptr, 20, ctypes.c_long, ctypes.POINTER(ctypes.c_void_p))
        if get_result(dialog_ptr, ctypes.byref(result_item)) != 0 or not result_item.value:
            return {"status": "unavailable", "reason": "win32-result-error"}

        psz_path = wintypes.LPWSTR()
        get_display_name = vtable_call(result_item, 5, ctypes.c_long, wintypes.DWORD, ctypes.POINTER(wintypes.LPWSTR))
        if get_display_name(result_item, SIGDN_FILESYSPATH, ctypes.byref(psz_path)) != 0 or not psz_path.value:
            return {"status": "unavailable", "reason": "win32-path-error"}
        try:
            picked = str(psz_path.value).strip()
        finally:
            ole32.CoTaskMemFree(psz_path)
        if not picked:
            return {"status": "cancelled"}
        return {"status": "selected", "path": picked}
    except Exception:
        return {"status": "unavailable", "reason": "win32-dialog-error"}
    finally:
        for ptr in (result_item, folder_item, dialog_ptr):
            if ptr.value:
                release = vtable_call(ptr, 2, ctypes.c_ulong)
                release(ptr)
        if should_uninit:
            ole32.CoUninitialize()


def main() -> int:
    title = sys.argv[1]
    initial_dir = sys.argv[2] or None
    try:
        from tkinter import Tk, filedialog
    except Exception:
        if sys.platform == "win32":
            print(json.dumps(_pick_win32_com(title, initial_dir)))
            return 0
        print(json.dumps({"status": "unavailable", "reason": "tkinter-unavailable"}))
        return 0
    try:
        root = Tk()
    except Exception:
        if sys.platform == "win32":
            print(json.dumps(_pick_win32_com(title, initial_dir)))
            return 0
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
        run_kwargs: dict[str, object] = {}
        if sys.platform == "win32":
            run_kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
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
            encoding="utf-8",
            errors="replace",
            timeout=timeout_seconds,
            check=False,
            shell=False,
            **run_kwargs,
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
