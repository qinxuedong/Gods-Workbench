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

"""受信本机模式：设备长期密钥、一次性启动票据与本机所有者绑定。

本模块只承担三件事：

1. **受信本机识别**：设备长期密钥 + 一次性启动票据的握手；
2. **首启原子建立**本机所有者身份（与既有本地账户体系同构，不旁路权限模型）；
3. **本机 / 远程凭据隔离校验**。

安全边界（与 QIN-630 裁决一致）：

- 身份判定**只**认设备密钥签名的一次性票据。``GW_TRUSTED_LOCAL`` 只是功能开关；
  环境变量、loopback、Host 与来源 IP 一律**不作为认证依据**。
- 设备长期密钥永远不出现在 URL、响应体、日志与审计中。
- 启动票据短时、一次性、原子兑换；兑换后由前端抹除 URL 片段。
- 设备密钥损坏、撤销、权限不合格或多实例冲突时一律显式报错，
  **绝不**静默创建第二个所有者。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import stat
import subprocess
import tempfile
import time
from functools import lru_cache
from pathlib import Path

from gw.core.errors import CleanroomException

TRUSTED_LOCAL_ENV = "GW_TRUSTED_LOCAL"
TRUSTED_LOCAL_TICKET_PURPOSE = "trusted_local_bootstrap"
SUPPORTED_TICKET_VERSION = 1
TICKET_TTL_SECONDS = 120
DEVICE_KEY_BYTES = 32

DEVICE_KEY_FILENAME = "trusted_local.key"
PENDING_TICKET_FILENAME = "trusted_local.pending"

_OFF_VALUES = frozenset({"0", "off", "false", "no"})

# Windows 受控主体：当前登录账户、SYSTEM、本机管理员组。
_WINDOWS_SYSTEM_SID = "S-1-5-18"
_WINDOWS_ADMINISTRATORS_SID = "S-1-5-32-544"

# icacls /save 写出的 SDDL 对已知主体使用**别名**而非完整 SID；
# 解析 icacls 常见别名；BU 会映射到 Users SID，但仍不在允许集合内。
_WINDOWS_SDDL_ALIASES = {
    "BA": _WINDOWS_ADMINISTRATORS_SID,  # BUILTIN\Administrators
    "BU": "S-1-5-32-545",  # BUILTIN\Users（解析为已知 SID，但仍不属于允许集合）
    "SY": _WINDOWS_SYSTEM_SID,  # NT AUTHORITY\SYSTEM
}


def trusted_local_enabled() -> bool:
    """受信本机模式是否启用。

    这是**功能开关**，不是凭据：即使开启，没有合法签名的一次性票据一样无法进入。
    """
    try:
        from gw.core.config import AUTH_MODE_LOCAL_ACCOUNT, load_runtime_auth_config

        if load_runtime_auth_config().mode != AUTH_MODE_LOCAL_ACCOUNT:
            return False
    except Exception:  # noqa: BLE001 - 配置未就绪时按未启用处理，失败关闭
        return False
    raw = str(os.environ.get(TRUSTED_LOCAL_ENV) or "1").strip().lower()
    return raw not in _OFF_VALUES


def _auth_dir() -> Path:
    """返回当前运行态的账户目录（账户库同级）。"""
    from gw.core.runtime_paths import resolve_runtime_paths

    return resolve_runtime_paths().auth_db.parent


def device_key_path() -> Path:
    """设备长期密钥文件路径（受控目录内）。"""
    return _auth_dir() / DEVICE_KEY_FILENAME


def pending_ticket_path() -> Path:
    """待用启动票据文件路径（受控目录内，供本机进程读取）。"""
    return _auth_dir() / PENDING_TICKET_FILENAME


# ---------------------------------------------------------------------------
# 文件权限：Unix 0600 / Windows 受限 NTFS DACL
# ---------------------------------------------------------------------------


_RESTRICTED_AUTH_DIRS: set[Path] = set()


@lru_cache(maxsize=1)
def _windows_current_sid() -> str:
    """返回当前登录账户的 SID（不依赖系统语言）。"""
    result = subprocess.run(
        ["whoami", "/user", "/fo", "csv", "/nh"],
        capture_output=True,
        text=True,
        check=False,
    )
    for line in (result.stdout or "").splitlines():
        parts = [part.strip().strip('"') for part in line.split(",")]
        if len(parts) >= 2 and parts[1].upper().startswith("S-1-"):
            return parts[1]
    raise CleanroomException(
        500, "TRUSTED_LOCAL_KEY_PERMISSIONS_UNSAFE", "无法确认当前登录账户，设备密钥未启用"
    )


def _restrict_windows_acl(path: Path, *, is_dir: bool = False) -> None:
    """用系统自带 icacls 施加显式受限 DACL（去继承 + 仅授受控主体）。"""
    current_sid = _windows_current_sid()
    perm = "(OI)(CI)(F)" if is_dir else "(F)"
    result = subprocess.run(
        [
            "icacls",
            str(path),
            "/inheritance:r",
            "/grant:r",
            f"*{current_sid}:{perm}",
            "/grant:r",
            f"*{_WINDOWS_SYSTEM_SID}:{perm}",
            "/grant:r",
            f"*{_WINDOWS_ADMINISTRATORS_SID}:{perm}",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise CleanroomException(
            500,
            "TRUSTED_LOCAL_KEY_PERMISSIONS_UNSAFE",
            "无法为设备密钥施加受限访问控制，设备密钥未启用",
        )


def restrict_directory_permissions(path: Path) -> None:
    """收紧账户目录访问权限：Windows 仅授受控主体并去继承，Unix 为 0700。"""
    if os.name == "nt":
        _restrict_windows_acl(path, is_dir=True)
        return
    os.chmod(path, 0o700)


def ensure_auth_dir_restricted(auth_dir: Path) -> Path:
    """确保认证目录存在并已收紧访问控制（进程内按解析路径幂等记忆）。"""
    auth_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    resolved = auth_dir.resolve()
    if resolved in _RESTRICTED_AUTH_DIRS:
        return auth_dir
    restrict_directory_permissions(auth_dir)
    _RESTRICTED_AUTH_DIRS.add(resolved)
    return auth_dir


def _decode_acl_text(raw: bytes) -> str:
    """解码 ``icacls /save`` 的输出。

    该输出固定为 **UTF-16LE 且不带 BOM**，按 UTF-8 读取会得到「字符间夹 NUL」
    的乱码，导致 SID 解析全部落空。
    """
    if raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return raw.decode("utf-16", errors="replace")
    if len(raw) >= 2 and raw[1:2] == b"\x00":
        return raw.decode("utf-16-le", errors="replace")
    return raw.decode("utf-8", errors="replace")


def _dacl_trustees(dacl_line: str) -> set[str]:
    """解析 SDDL 的 DACL 段，得出全部受托主体。

    只接受完整 SID（``S-1-...``）与已知别名；无法识别的受托主体以
    ``alias:`` 前缀返回，调用方按**不受控**处理（失败关闭）。
    """
    trustees: set[str] = set()
    for ace in re.findall(r"\(([^()]*)\)", dacl_line):
        fields = [field for field in ace.split(";") if field != ""]
        if len(fields) < 2:
            continue
        trustee = fields[-1]
        if trustee.upper().startswith("S-1-"):
            trustees.add(trustee.upper())
            continue
        alias = _WINDOWS_SDDL_ALIASES.get(trustee.upper())
        trustees.add(alias if alias else f"alias:{trustee.upper()}")
    return trustees


def _windows_dacl_sids(path: Path) -> tuple[set[str], bool]:
    """用 ``icacls /save`` 读回 DACL 的受托主体集合与「是否已断开继承」标记。"""
    handle = tempfile.NamedTemporaryFile(prefix="gw-acl-", suffix=".txt", delete=False)
    acl_file = Path(handle.name)
    handle.close()
    try:
        result = subprocess.run(
            ["icacls", str(path), "/save", str(acl_file), "/c"],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0 or not acl_file.exists():
            return set(), False
        text = _decode_acl_text(acl_file.read_bytes())
        for line in text.splitlines():
            stripped = line.strip()
            if not stripped.startswith("D:"):
                continue
            # "D:P..." 的 P 标志表示 DACL 已断开继承；缺失即视为权限不合格。
            return _dacl_trustees(stripped), stripped.startswith("D:P")
        return set(), False
    finally:
        acl_file.unlink(missing_ok=True)


def restrict_file_permissions(path: Path) -> None:
    """把受控文件权限收敛到当前账户可读写。"""
    if os.name == "nt":
        _restrict_windows_acl(path)
        return
    os.chmod(path, 0o600)


def verify_file_permissions(path: Path) -> None:
    """回读校验受控文件权限；不合格一律失败关闭。

    Windows 上 ``os.chmod`` 只切换只读位，**不构成保护**，因此必须用
    ``icacls /save`` 读回 DACL 并确认可访问主体集合只含受控主体。
    """
    if os.name == "nt":
        allowed = {
            _windows_current_sid(),
            _WINDOWS_SYSTEM_SID,
            _WINDOWS_ADMINISTRATORS_SID,
        }
        sids, inheritance_broken = _windows_dacl_sids(path)
        if not sids:
            raise CleanroomException(
                500,
                "TRUSTED_LOCAL_KEY_PERMISSIONS_UNSAFE",
                "无法确认受控文件访问控制，设备密钥未启用",
            )
        unexpected = sids - allowed
        if unexpected:
            raise CleanroomException(
                500,
                "TRUSTED_LOCAL_KEY_PERMISSIONS_UNSAFE",
                "受控文件存在非受控访问主体，设备密钥未启用",
            )
        if not inheritance_broken:
            raise CleanroomException(
                500,
                "TRUSTED_LOCAL_KEY_PERMISSIONS_UNSAFE",
                "受控文件仍继承上级目录权限，设备密钥未启用",
            )
        return
    mode = stat.S_IMODE(os.stat(path).st_mode)
    if mode != 0o600:
        raise CleanroomException(
            500,
            "TRUSTED_LOCAL_KEY_PERMISSIONS_UNSAFE",
            "受控文件权限不是 0600，设备密钥未启用",
        )


def _write_restricted_file(path: Path, payload: bytes) -> None:
    """先保护并验证空临时文件，再写入秘密并原子替换目标。"""
    ensure_auth_dir_restricted(path.parent)
    handle = tempfile.NamedTemporaryFile(
        dir=str(path.parent), prefix=f"{path.name}.", suffix=".tmp", delete=False
    )
    temp_path = Path(handle.name)
    try:
        with handle:
            # 临时文件可能继承宽松父目录 ACL；先在空文件上切断继承并验证，
            # 再写入票据，避免秘密在 ACL 收敛前短暂暴露。
            restrict_file_permissions(temp_path)
            verify_file_permissions(temp_path)
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
        try:
            verify_file_permissions(path)
        except BaseException:
            path.unlink(missing_ok=True)
            raise
    finally:
        temp_path.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# 设备长期密钥
# ---------------------------------------------------------------------------


def _read_device_key(path: Path) -> bytes:
    raw = path.read_bytes()
    if len(raw) != DEVICE_KEY_BYTES:
        raise CleanroomException(
            500,
            "TRUSTED_LOCAL_KEY_CORRUPT",
            "设备密钥文件已损坏，请执行显式恢复命令后再试",
        )
    return raw


def read_device_key() -> bytes:
    """读取**既有**设备密钥；缺失或损坏一律显式失败。

    校验路径**绝不允许**顺手创建新密钥：那等于静默轮换信任根，会让既有本机
    绑定与新密钥指纹不一致，违反「损坏或撤销时明确报错、不得静默创建第二个
    owner」的要求（AC8）。
    """
    path = device_key_path()
    if not path.exists():
        raise CleanroomException(
            500,
            "TRUSTED_LOCAL_KEY_MISSING",
            "设备密钥缺失，请执行显式恢复命令后再试",
        )
    verify_file_permissions(path)
    return _read_device_key(path)


def load_or_create_device_key() -> bytes:
    """读取或原子创建设备长期密钥。

    并发首启时只有一个进程能通过 ``O_CREAT|O_EXCL`` 完成创建，其余进程回读
    已创建的同一把密钥，保证所有进程使用同一信任根。
    """
    path = device_key_path()
    ensure_auth_dir_restricted(path.parent)
    try:
        descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        verify_file_permissions(path)
        return _read_device_key(path)
    except OSError as exc:
        raise CleanroomException(
            500, "TRUSTED_LOCAL_KEY_UNAVAILABLE", "无法创建设备密钥文件，已拒绝启用本机身份"
        ) from exc
    try:
        with os.fdopen(descriptor, "wb") as stream:
            # Windows 的 0600 创建参数不提供 ACL 保护。先对空文件施加并回读
            # 受限 DACL；Unix 同样先固定 0600，再写入设备密钥。
            restrict_file_permissions(path)
            verify_file_permissions(path)
            stream.write(secrets.token_bytes(DEVICE_KEY_BYTES))
            stream.flush()
            os.fsync(stream.fileno())
    except BaseException:
        path.unlink(missing_ok=True)
        raise
    try:
        verify_file_permissions(path)
    except BaseException:
        path.unlink(missing_ok=True)
        raise
    return _read_device_key(path)


def device_key_fingerprint(key: bytes | None = None) -> str:
    """设备密钥指纹（短摘要）；**不可**反推密钥本身。"""
    raw = key if key is not None else load_or_create_device_key()
    return hashlib.sha256(raw).hexdigest()[:16]


# ---------------------------------------------------------------------------
# 一次性启动票据
# ---------------------------------------------------------------------------


def _b64encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64decode(text: str) -> bytes:
    padding = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + padding)


def _canonical_payload(payload: dict) -> bytes:
    return json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _reject_rotated_trust_root() -> None:
    """已建立本机绑定但设备密钥缺失时**拒绝签发**，不静默轮换信任根。

    密钥缺失或损坏属于「显式恢复」场景：直接另起一把密钥会让既有绑定与新指纹
    不一致，等于悄悄造出第二个互相矛盾的所有者（AC8）。
    """
    from gw.core import local_accounts

    try:
        with local_accounts.database() as db:
            binding = local_accounts.trusted_local_owner(db)
    except Exception:  # noqa: BLE001 - 存储不可用时不阻断签发路径
        return
    if binding is None:
        return
    if not device_key_path().exists():
        raise CleanroomException(
            500,
            "TRUSTED_LOCAL_KEY_MISSING",
            "设备密钥缺失且本机所有者已建立，请先执行显式恢复或解绑命令",
        )


def issue_bootstrap_ticket(*, now: float | None = None) -> str:
    """签发一张短时一次性启动票据，并登记待用票据文件。

    票据串只含 ``base64url(payload).base64url(HMAC-SHA256)``；设备长期密钥
    本身永不出现在票据里，也永不进入 URL 之外的任何通道。
    """
    if not trusted_local_enabled():
        raise CleanroomException(
            403, "TRUSTED_LOCAL_DISABLED", "受信本机模式未启用，已拒绝签发启动票据"
        )
    _reject_rotated_trust_root()
    key = load_or_create_device_key()
    fingerprint = device_key_fingerprint(key)
    issued_at = time.time() if now is None else now
    payload = {
        "v": SUPPORTED_TICKET_VERSION,
        "jti": secrets.token_hex(16),
        "exp": int(issued_at + TICKET_TTL_SECONDS),
        "aud": fingerprint,
        "purpose": TRUSTED_LOCAL_TICKET_PURPOSE,
    }
    canonical = _canonical_payload(payload)
    signature = hmac.new(key, canonical, hashlib.sha256).digest()
    ticket = f"{_b64encode(canonical)}.{_b64encode(signature)}"

    from gw.core import local_accounts

    try:
        with local_accounts.database() as db:
            db.execute("BEGIN IMMEDIATE")
            local_accounts.record_bootstrap_ticket(
                db,
                str(payload["jti"]),
                fingerprint,
                issued_at,
                float(payload["exp"]),
            )
    except CleanroomException as exc:
        # 桌面端等场景中，后端主进程已启动并持有 data_dir_lock（LOCK_HELD）；
        # 外部 trusted-open 子进程无法抢锁预写库，交由持有锁的后端在兑换时原子落库防重放。
        if exc.code != "DATA_LOCATION_QUIESCING" or "LOCK_HELD" not in exc.message:
            raise
    _write_restricted_file(pending_ticket_path(), ticket.encode("ascii"))
    return ticket


def read_pending_ticket() -> str | None:
    """读取待用启动票据文件；不可用或已过期时返回 None。"""
    path = pending_ticket_path()
    try:
        if not path.exists():
            return None
        verify_file_permissions(path)
        raw = path.read_text(encoding="ascii").strip()
    except (OSError, UnicodeDecodeError, CleanroomException):
        return None
    if not raw:
        return None
    try:
        claims = verify_bootstrap_ticket(raw)
    except CleanroomException:
        return None
    return raw if claims else None


def clear_pending_ticket() -> None:
    """清除待用票据文件（兑换成功或显式撤销后调用）。"""
    pending_ticket_path().unlink(missing_ok=True)


def verify_bootstrap_ticket(ticket: str, *, now: float | None = None) -> dict:
    """校验一次性启动票据并返回其载荷。

    校验口径：协议版本、HMAC 签名（常数时间比对）、有效期、实例指纹、用途。
    任何一项不符都失败关闭；**不**把环境变量、loopback、Host 或来源 IP
    当作身份判据。
    """
    if not trusted_local_enabled():
        raise CleanroomException(
            403, "TRUSTED_LOCAL_DISABLED", "受信本机模式未启用，已拒绝本次请求"
        )
    raw = str(ticket or "").strip()
    if "." not in raw:
        raise CleanroomException(
            401, "TRUSTED_LOCAL_BOOTSTRAP_INVALID", "启动票据无效，已拒绝本次请求"
        )
    body, signature = raw.split(".", 1)
    try:
        canonical = _b64decode(body)
        signature_bytes = _b64decode(signature)
        payload = json.loads(canonical.decode("utf-8"))
    except Exception as exc:  # noqa: BLE001 - 任何解析异常都按无效票据处理
        raise CleanroomException(
            401, "TRUSTED_LOCAL_BOOTSTRAP_INVALID", "启动票据无效，已拒绝本次请求"
        ) from exc
    if not isinstance(payload, dict):
        raise CleanroomException(
            401, "TRUSTED_LOCAL_BOOTSTRAP_INVALID", "启动票据无效，已拒绝本次请求"
        )
    if payload.get("v") != SUPPORTED_TICKET_VERSION:
        raise CleanroomException(
            401,
            "TRUSTED_LOCAL_BOOTSTRAP_INVALID",
            "启动票据版本不受支持，已拒绝本次请求",
        )
    key = read_device_key()
    expected = hmac.new(key, canonical, hashlib.sha256).digest()
    if not hmac.compare_digest(expected, signature_bytes):
        raise CleanroomException(
            401, "TRUSTED_LOCAL_BOOTSTRAP_INVALID", "启动票据签名校验失败，已拒绝本次请求"
        )
    current = time.time() if now is None else now
    expires_at = payload.get("exp")
    if not isinstance(expires_at, int) or expires_at <= int(current):
        raise CleanroomException(
            401, "TRUSTED_LOCAL_BOOTSTRAP_EXPIRED", "启动票据已过期，已拒绝本次请求"
        )
    if str(payload.get("aud") or "") != device_key_fingerprint(key):
        raise CleanroomException(
            401, "TRUSTED_LOCAL_BOOTSTRAP_INVALID", "启动票据与本机不匹配，已拒绝本次请求"
        )
    if str(payload.get("purpose") or "") != TRUSTED_LOCAL_TICKET_PURPOSE:
        raise CleanroomException(
            401, "TRUSTED_LOCAL_BOOTSTRAP_INVALID", "启动票据用途不符，已拒绝本次请求"
        )
    if not isinstance(payload.get("jti"), str) or not payload["jti"]:
        raise CleanroomException(
            401, "TRUSTED_LOCAL_BOOTSTRAP_INVALID", "启动票据无效，已拒绝本次请求"
        )
    return payload


__all__ = [
    "DEVICE_KEY_BYTES",
    "PENDING_TICKET_FILENAME",
    "SUPPORTED_TICKET_VERSION",
    "TICKET_TTL_SECONDS",
    "TRUSTED_LOCAL_ENV",
    "TRUSTED_LOCAL_TICKET_PURPOSE",
    "clear_pending_ticket",
    "device_key_fingerprint",
    "device_key_path",
    "issue_bootstrap_ticket",
    "load_or_create_device_key",
    "pending_ticket_path",
    "read_device_key",
    "read_pending_ticket",
    "restrict_file_permissions",
    "trusted_local_enabled",
    "verify_bootstrap_ticket",
    "verify_file_permissions",
]
