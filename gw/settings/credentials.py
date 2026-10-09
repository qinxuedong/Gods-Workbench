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

"""本地凭据保护：Windows当前用户DPAPI，POSIX受限密钥文件加Fernet。

密文与平台元数据在同一原子快照提交。只在执行时解密；不在环境文件保存原文。
Windows密文绑定当前系统用户，复制到其他用户/机器不保证可恢复。
"""

import base64
import ctypes
import json
import os
import stat

from cryptography.fernet import Fernet

from gw.core.errors import CleanroomException
from gw.core.runtime_paths import resolve_runtime_paths


def _unavailable():
    return CleanroomException(503, "PROVIDER_CREDENTIAL_UNAVAILABLE", "本地凭据无法安全保存或读取，请重新配置密钥")


def _dpapi(value: bytes, decrypt: bool) -> bytes:
    """指针与长度显式声明；禁止弹窗与机器范围保护。"""
    from ctypes import wintypes

    class Blob(ctypes.Structure):
        _fields_ = [("length", wintypes.DWORD), ("data", ctypes.POINTER(ctypes.c_ubyte))]

    buffer = (ctypes.c_ubyte * len(value)).from_buffer_copy(value)
    incoming, outgoing = Blob(len(value), buffer), Blob()
    library = ctypes.WinDLL("crypt32", use_last_error=True)
    function = library.CryptUnprotectData if decrypt else library.CryptProtectData
    function.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.POINTER(Blob),
                         ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
    function.restype = wintypes.BOOL
    free = ctypes.WinDLL("kernel32", use_last_error=True).LocalFree
    free.argtypes = [ctypes.c_void_p]
    free.restype = ctypes.c_void_p
    if not function(ctypes.byref(incoming), None, None, None, None, 1, ctypes.byref(outgoing)):
        raise _unavailable()
    try:
        return ctypes.string_at(outgoing.data, outgoing.length)
    finally:
        free(outgoing.data)


def _fernet(*, create: bool) -> Fernet:
    """密钥只在当前校验过的auth根内；丢失时不能生成新钥掩盖旧密文。"""
    path = resolve_runtime_paths().auth_db.parent / "provider-vault.key"
    if create:
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        except FileExistsError:
            pass
        else:
            with os.fdopen(fd, "wb") as handle:
                handle.write(Fernet.generate_key())
                handle.flush()
                os.fsync(handle.fileno())
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, "rb") as handle:
        info = os.fstat(handle.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise _unavailable()
        key = handle.read(45)
    return Fernet(key)


def protect(provider_id: str, field: str, secret: str) -> str:
    """密文中的身份绑定可阻止平台之间调换密文。"""
    try:
        payload = json.dumps([provider_id, field, secret], ensure_ascii=False).encode("utf-8")
        if os.name == "nt":
            return "dpapi:" + base64.b64encode(_dpapi(payload, False)).decode("ascii")
        return "fernet:" + _fernet(create=True).encrypt(payload).decode("ascii")
    except Exception:
        raise _unavailable() from None


def reveal(provider_id: str, field: str, encrypted: str) -> str:
    try:
        if encrypted.startswith("dpapi:") and os.name == "nt":
            payload = _dpapi(base64.b64decode(encrypted[6:], validate=True), True)
        elif encrypted.startswith("fernet:") and os.name != "nt":
            payload = _fernet(create=False).decrypt(encrypted[7:].encode("ascii"))
        else:
            raise _unavailable()
        saved_id, saved_field, secret = json.loads(payload.decode("utf-8"))
        if saved_id != provider_id or saved_field != field or not isinstance(secret, str):
            raise _unavailable()
        return secret
    except Exception:
        raise _unavailable() from None
