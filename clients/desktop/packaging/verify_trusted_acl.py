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

"""只读验证受信本机账户目录与设备密钥的访问控制。

- Windows：读取所有者与 DACL，逐 ACE 比较 SID；``chmod 0600`` 不构成 Windows 保护。
- 允许主体：当前用户、SYSTEM、Administrators；其余任何普通用户/Everyone/Users/
  Authenticated Users/Guest/匿名主体一律失败关闭。
- 只验证，不创建密钥、不改 ACL、不读取密钥内容。
- 输出不包含真实路径或 SID，只给桌面展示稳定原因。

QIN-630 负责创建密钥并施加 ACL；QIN-626 在安装自检与启动握手前复用本脚本验证。
"""

from __future__ import annotations

import argparse
import json
import os
import stat
import sys
from pathlib import Path

if sys.platform == "win32" and hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

ALLOWED_SYSTEM_SIDS = frozenset({"S-1-5-18", "S-1-5-32-544"})
FILE_READ_WRITE = 0x120089 | 0x120116
ACCESS_ALLOWED_ACE_TYPE = 0x00
ACCESS_DENIED_ACE_TYPE = 0x01
ACCESS_ALLOWED_OBJECT_ACE_TYPE = 0x05
ACCESS_DENIED_OBJECT_ACE_TYPE = 0x06
INHERIT_ONLY_ACE = 0x08


def _auth_dir(explicit: str | None) -> Path:
    if explicit:
        return Path(explicit)
    from gw.core.runtime_paths import resolve_runtime_paths

    return resolve_runtime_paths().auth_db.parent


def _result(ok: bool, reason: str, checked: list[str], platform: str) -> dict[str, object]:
    return {"ok": ok, "reason": reason, "checked": checked, "platform": platform}


if os.name != "nt":
    import stat as _stat

    def verify(auth_dir: Path) -> dict[str, object]:
        checked: list[str] = []
        if not auth_dir.is_dir():
            return _result(False, "账户目录尚不存在", checked, "posix")
        targets = [("auth_dir", auth_dir)]
        for name in ("trusted_local.key", "trusted_local.pending"):
            candidate = auth_dir / name
            if candidate.exists():
                targets.append((name, candidate))
        for label, path in targets:
            mode = _stat.S_IMODE(path.stat().st_mode)
            if label == "auth_dir":
                if mode & 0o077:
                    return _result(False, "账户目录对同组或其他用户开放", checked, "posix")
            elif mode != 0o600:
                return _result(False, f"{label} 权限不是 0600", checked, "posix")
            checked.append(label)
        return _result(True, "", checked, "posix")

else:
    import ctypes
    import ctypes.wintypes as wintypes

    _advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    _SE_FILE_OBJECT = 1
    _OWNER_SECURITY_INFORMATION = 0x00000001
    _DACL_SECURITY_INFORMATION = 0x00000004
    _TOKEN_QUERY = 0x0008
    _TOKEN_USER_CLASS = 1
    _ACL_SIZE_INFORMATION_CLASS = 2

    _advapi32.GetNamedSecurityInfoW.argtypes = [
        wintypes.LPWSTR,
        ctypes.c_int,
        wintypes.DWORD,
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_void_p),
    ]
    _advapi32.GetNamedSecurityInfoW.restype = wintypes.DWORD
    _advapi32.ConvertSidToStringSidW.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(wintypes.LPWSTR),
    ]
    _advapi32.ConvertSidToStringSidW.restype = wintypes.BOOL
    _advapi32.GetAclInformation.argtypes = [
        ctypes.c_void_p,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.c_int,
    ]
    _advapi32.GetAclInformation.restype = wintypes.BOOL
    _advapi32.GetAce.argtypes = [
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(ctypes.c_void_p),
    ]
    _advapi32.GetAce.restype = wintypes.BOOL
    _advapi32.GetTokenInformation.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
    ]
    _advapi32.GetTokenInformation.restype = wintypes.BOOL
    _kernel32.OpenProcessToken.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.HANDLE),
    ]
    _kernel32.OpenProcessToken.restype = wintypes.BOOL
    _kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    _kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    _kernel32.LocalFree.argtypes = [ctypes.c_void_p]

    class _AclSizeInformation(ctypes.Structure):
        _fields_ = [
            ("AceCount", wintypes.DWORD),
            ("AclBytesInUse", wintypes.DWORD),
            ("AclBytesFree", wintypes.DWORD),
        ]

    class _SidAndAttributes(ctypes.Structure):
        _fields_ = [("Sid", ctypes.c_void_p), ("Attributes", wintypes.DWORD)]

    class _TokenUser(ctypes.Structure):
        _fields_ = [("User", _SidAndAttributes)]

    def _sid_text(sid: ctypes.c_void_p) -> str:
        text = wintypes.LPWSTR()
        if not _advapi32.ConvertSidToStringSidW(sid, ctypes.byref(text)):
            return ""
        try:
            return str(text.value or "")
        finally:
            _kernel32.LocalFree(ctypes.cast(text, ctypes.c_void_p))

    def _current_sid() -> str:
        token = wintypes.HANDLE()
        if not _kernel32.OpenProcessToken(
            _kernel32.GetCurrentProcess(), _TOKEN_QUERY, ctypes.byref(token)
        ):
            raise OSError("无法打开当前进程令牌")
        try:
            size = wintypes.DWORD(0)
            _advapi32.GetTokenInformation(
                token, _TOKEN_USER_CLASS, None, 0, ctypes.byref(size)
            )
            if size.value <= 0:
                raise OSError("无法读取令牌用户信息")
            buffer = ctypes.create_string_buffer(size.value)
            if not _advapi32.GetTokenInformation(
                token,
                _TOKEN_USER_CLASS,
                ctypes.cast(buffer, ctypes.c_void_p),
                size,
                ctypes.byref(size),
            ):
                raise OSError("无法读取令牌用户信息")
            user = ctypes.cast(buffer, ctypes.POINTER(_TokenUser)).contents
            sid = _sid_text(ctypes.c_void_p(user.User.Sid))
            if not sid.startswith("S-1-"):
                raise OSError("无法确认当前用户 SID")
            return sid
        finally:
            _kernel32.CloseHandle(token)

    def _security_descriptor(path: Path) -> tuple[str, list[tuple[str, int, int]]]:
        owner = ctypes.c_void_p()
        dacl = ctypes.c_void_p()
        descriptor = ctypes.c_void_p()
        status = _advapi32.GetNamedSecurityInfoW(
            str(path),
            _SE_FILE_OBJECT,
            _OWNER_SECURITY_INFORMATION | _DACL_SECURITY_INFORMATION,
            ctypes.byref(owner),
            None,
            ctypes.byref(dacl),
            None,
            ctypes.byref(descriptor),
        )
        if status != 0:
            raise OSError(f"无法读取访问控制（{status}）")
        try:
            if not owner or not dacl:
                raise OSError("对象缺少所有者或 DACL")
            owner_sid = _sid_text(owner)
            size_info = _AclSizeInformation()
            if not _advapi32.GetAclInformation(
                dacl,
                ctypes.byref(size_info),
                ctypes.sizeof(size_info),
                _ACL_SIZE_INFORMATION_CLASS,
            ):
                raise OSError("无法读取 DACL 信息")
            entries: list[tuple[str, int, int]] = []
            for index in range(size_info.AceCount):
                ace = ctypes.c_void_p()
                if not _advapi32.GetAce(dacl, index, ctypes.byref(ace)):
                    continue
                header = ctypes.string_at(ace, 4)
                ace_type = header[0]
                ace_flags = header[1]
                ace_size = int.from_bytes(header[2:4], "little")
                if ace_size < 8 or ace_type not in {
                    ACCESS_ALLOWED_ACE_TYPE,
                    ACCESS_DENIED_ACE_TYPE,
                    ACCESS_ALLOWED_OBJECT_ACE_TYPE,
                    ACCESS_DENIED_OBJECT_ACE_TYPE,
                }:
                    continue
                mask = int.from_bytes(ctypes.string_at(ace, 8)[4:8], "little")
                if ace_type in {ACCESS_ALLOWED_OBJECT_ACE_TYPE, ACCESS_DENIED_OBJECT_ACE_TYPE}:
                    object_flags = int.from_bytes(
                        ctypes.string_at(ace.value + 8, 4), "little"
                    )
                    offset = 12
                    if object_flags & 1:
                        offset += 16
                    if object_flags & 2:
                        offset += 16
                else:
                    offset = 8
                sid = _sid_text(ctypes.c_void_p(ace.value + offset))
                if not sid.startswith("S-1-"):
                    continue
                inherits_to_self = not bool(ace_flags & INHERIT_ONLY_ACE)
                is_allow = ace_type in {
                    ACCESS_ALLOWED_ACE_TYPE,
                    ACCESS_ALLOWED_OBJECT_ACE_TYPE,
                }
                if is_allow and inherits_to_self:
                    entries.append((sid, mask, 1))
                elif is_allow:
                    entries.append((sid, mask, 2))
                elif inherits_to_self:
                    entries.append((sid, mask, 0))
            return owner_sid, entries
        finally:
            if descriptor:
                _kernel32.LocalFree(descriptor)

    def verify(auth_dir: Path) -> dict[str, object]:
        checked: list[str] = []
        if not auth_dir.is_dir():
            return _result(False, "账户目录尚不存在", checked, "windows")
        current = _current_sid()
        targets = [("auth_dir", auth_dir)]
        for name in ("trusted_local.key", "trusted_local.pending"):
            candidate = auth_dir / name
            if candidate.exists():
                targets.append((name, candidate))
        allowed = set(ALLOWED_SYSTEM_SIDS) | {current}
        for label, path in targets:
            try:
                owner, entries = _security_descriptor(path)
            except OSError:
                return _result(False, f"{label} 访问控制无法核验", checked, "windows")
            if owner != current:
                return _result(False, f"{label} 所有者不是当前用户", checked, "windows")
            has_current_access = False
            for sid, mask, is_allow in entries:
                if not is_allow:
                    if sid == current:
                        return _result(
                            False, f"{label} 存在针对当前用户的拒绝项", checked, "windows"
                        )
                    continue
                if sid not in allowed:
                    return _result(
                        False, f"{label} 存在非受控访问主体", checked, "windows"
                    )
                if sid == current and (mask & FILE_READ_WRITE) == FILE_READ_WRITE:
                    has_current_access = True
            if not has_current_access:
                return _result(
                    False, f"{label} 未授予当前用户读写权限", checked, "windows"
                )
            checked.append(label)
        return _result(True, "", checked, "windows")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="验证受信本机账户目录访问控制")
    parser.add_argument("--auth-dir", help="可选：显式账户目录，仅用于受控自检")
    parser.add_argument("--json", action="store_true", help="输出机器可读 JSON")
    args = parser.parse_args(argv)
    try:
        result = verify(_auth_dir(args.auth_dir))
    except Exception:  # noqa: BLE001 - 自检失败必须转为稳定原因，不泄露路径
        result = _result(False, "访问控制核验异常", [], "windows" if os.name == "nt" else "posix")
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
