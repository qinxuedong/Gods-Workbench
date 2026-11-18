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

"""QIN-619：疑似重复素材按 size 筛候选后的完整 SHA 后台确认引擎。

职责边界：
- 候选范围：先按精确 ``size_bytes`` 分桶，仅对同 size ``>= 2`` 的疑似重复候选计算标准
  SHA-256（与 ``hash_mode="full"`` 同口径），唯一大小文件不纳入完整 SHA 普查；
- 缓存与失效：完整 SHA 写入独立字段 ``full_sha256_cache``（含 ``size_bytes/mtime_ns/ino/dev``
  版本签名与 ``verified=True``），绝不把 ``hash_mode="sampled"`` 或未知历史摘要写为确认；
  读中修改或元数据更新立即失效旧缓存，绝不提交脏指纹；
- 限速与不阻塞：全局共享令牌桶限制聚合读带宽（默认 20 MiB/s）与并发（默认 2，范围 1..4），
  全程支持测试注入；资产登记与浏览热路径绝不等待本任务完成；
- 断点与进程锁：区分任务断点与文件级 checkpoint（未完成文件整文件重算，已完成且版本匹配
  跳过）；重启默认收敛为 ``interrupted`` 不自动抢跑；跨进程锁基于 ``pid + start_time_ns``
  区分进程身份，防 PID 复用误判。
"""

from __future__ import annotations

from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, wait
import contextvars
from contextlib import suppress
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import threading
import time
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple
import uuid

from gw.core import storage
from gw.core.asset_visibility import asset_owner_key, asset_visible, current_asset_owner
from gw.core.auth import EDIT_ROLES
from gw.core.errors import CleanroomException
from gw.core.runtime_paths import (
    DATA_DIR_ENV,
    LOCAL_AUTH_DB_ENV,
    RUNTIME_MODE_ENV,
    VIDEO_DATA_DIR_ENV,
)

# PM 拍板默认限速与并发参数（2026-11-18）
DEFAULT_MAX_READ_BYTES_PER_SEC = 20 * 1024 * 1024
DEFAULT_MAX_CONCURRENCY = 2
MAX_ALLOWED_CONCURRENCY = 4
DEFAULT_CHUNK_SIZE_BYTES = 1024 * 1024
DEFAULT_HEARTBEAT_TTL_SECONDS = 30.0

JOB_ID_PREFIX = "job_dedup_"
JOB_TYPE_DEDUP_CONFIRM = "asset.dedup.confirm"
JOB_STORE_NAMESPACE = "dedup_confirm_jobs"
LOCK_FILENAME = "dedup_confirm.lock"
RECOVER_LOCK_FILENAME = "dedup_confirm.lock.recover"

MAX_ACTIVE = 4
MAX_JOBS = 500
PROGRESS_MIN_INTERVAL_SECONDS = 0.2

TERMINAL = {"succeeded", "failed", "cancelled", "interrupted"}
ACTIONS = {
    "pause": {"queued", "running"},
    "resume": {"paused"},
    "cancel": {"queued", "running", "paused"},
    "retry": {"failed", "cancelled", "interrupted"},
}

_SHA256_HEX_RE = re.compile(r"^[0-9a-f]{64}$")
_STATE_IO_ATTEMPTS = 5
_STATE_IO_BACKOFF_SECONDS = 0.02
_STATE_IO_SLEEP = time.sleep
_STATE_TX_LOCKS: dict[str, threading.RLock] = {}
_STATE_TX_GUARD = threading.Lock()


def _transaction_lock(path: Path) -> threading.RLock:
    key = os.path.abspath(str(path))
    with _STATE_TX_GUARD:
        lock = _STATE_TX_LOCKS.get(key)
        if lock is None:
            lock = threading.RLock()
            _STATE_TX_LOCKS[key] = lock
        return lock


def _error(status: int, code: str, message: str, *, extra: Optional[dict[str, Any]] = None) -> None:
    raise CleanroomException(
        status,
        code,
        message,
        extra=extra,
        expose_extra_fields=set(extra.keys()) if extra else None,
    )


def _digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False, default=str).encode("utf-8")
    ).hexdigest()


def _actor(auth: Any) -> dict[str, Any]:
    return {
        "subject": getattr(auth, "subject", ""),
        "mode": getattr(auth, "mode", ""),
        "domain": getattr(auth, "identity_domain", ""),
        "role": getattr(auth, "role", ""),
    }


def _owner(actor: Mapping[str, Any]) -> str:
    return _digest([actor.get("mode"), actor.get("domain"), actor.get("subject")])


def _runtime_path_configuration() -> tuple[tuple[str, Optional[str]], ...]:
    return tuple(
        (name, os.environ.get(name))
        for name in (
            RUNTIME_MODE_ENV,
            DATA_DIR_ENV,
            LOCAL_AUTH_DB_ENV,
            VIDEO_DATA_DIR_ENV,
            "LOCALAPPDATA",
        )
    )


def is_valid_sha256_hex(value: Any) -> bool:
    return isinstance(value, str) and bool(_SHA256_HEX_RE.match(value.strip().lower()))


def normalize_sha256_hex(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    text = value.strip().lower()
    return text if _SHA256_HEX_RE.match(text) else ""


def file_version_signature(stat_result: os.stat_result) -> dict[str, int]:
    """提取用于缓存失效校验的文件版本签名。"""
    return {
        "size_bytes": int(stat_result.st_size),
        "mtime_ns": int(stat_result.st_mtime_ns),
        "ino": int(getattr(stat_result, "st_ino", 0) or 0),
        "dev": int(getattr(stat_result, "st_dev", 0) or 0),
    }


def build_full_sha256_cache(
    sha256_hex: str,
    stat_result: os.stat_result,
    *,
    source: str,
    generation: int = 0,
    computed_at: Optional[str] = None,
) -> dict[str, Any]:
    """构造可落盘的完整 SHA-256 确认缓存对象；绝不接受非标准 hex。"""
    digest = normalize_sha256_hex(sha256_hex)
    if not digest:
        raise ValueError("sha256_hex 必须是 64 位十六进制字符串")
    sig = file_version_signature(stat_result)
    return {
        "sha256": digest,
        **sig,
        "verified": True,
        "source": str(source or "dedup_confirm"),
        "generation": int(generation),
        "computed_at": computed_at or storage.now_iso(),
    }


def invalidate_full_sha256_cache(asset: dict[str, Any]) -> bool:
    """清除资产记录上已过期的完整 SHA 确认缓存，返回是否发生了清除。"""
    if isinstance(asset, dict) and "full_sha256_cache" in asset:
        asset.pop("full_sha256_cache", None)
        return True
    return False


def cache_matches_asset_and_stat(
    cache: Any,
    asset: Mapping[str, Any],
    stat_result: Optional[os.stat_result] = None,
) -> bool:
    """校验 full_sha256_cache 是否合法、已验真且与资产登记及磁盘文件版本匹配。"""
    if not isinstance(cache, Mapping) or cache.get("verified") is not True:
        return False
    digest = normalize_sha256_hex(cache.get("sha256"))
    if not digest:
        return False
    cache_size = cache.get("size_bytes")
    if isinstance(cache_size, bool) or not isinstance(cache_size, int) or cache_size < 0:
        return False
    asset_size = asset.get("size_bytes")
    if (
        not isinstance(asset_size, bool)
        and isinstance(asset_size, int)
        and asset_size >= 0
        and asset_size != cache_size
    ):
        return False
    if stat_result is None:
        return True
    if int(stat_result.st_size) != cache_size:
        return False
    cache_mtime = cache.get("mtime_ns")
    if (
        isinstance(cache_mtime, bool)
        or not isinstance(cache_mtime, int)
        or int(stat_result.st_mtime_ns) != cache_mtime
    ):
        return False
    cache_ino = int(cache.get("ino") or 0)
    cache_dev = int(cache.get("dev") or 0)
    actual_ino = int(getattr(stat_result, "st_ino", 0) or 0)
    actual_dev = int(getattr(stat_result, "st_dev", 0) or 0)
    if (cache_ino or actual_ino) and (actual_ino != cache_ino or actual_dev != cache_dev):
        return False
    return True


def asset_has_local_binding(asset: Mapping[str, Any]) -> bool:
    """判断资产记录是否绑定了本地物理路径或托管文件。"""
    if any(
        str(asset.get(key) or "").strip()
        for key in ("root_id", "storage_root_id", "canonical_relpath", "stored_name")
    ):
        return True
    display = str(asset.get("display_path") or "").strip().lower()
    if display and not display.startswith(("http://", "https://")):
        return True
    return False


def trusted_full_sha256(
    asset: Mapping[str, Any],
    *,
    path: Optional[Path] = None,
    stat_result: Optional[os.stat_result] = None,
) -> Optional[str]:
    """仅返回可信、已验真且文件版本匹配的完整 SHA-256；否则返回 None。

    绝不把 ``hash_mode="sampled"`` 或未知/缺失 ``hash_mode`` 升级为可信完整 SHA；
    若资产绑定了本地路径但磁盘文件已缺失（无法获取 ``stat_result``），同样视为失效并返回 None。
    """
    current_stat = stat_result
    if current_stat is None and path is not None:
        try:
            current_stat = path.lstat()
        except OSError:
            return None

    if current_stat is None and asset_has_local_binding(asset):
        return None

    cache = asset.get("full_sha256_cache")
    if cache_matches_asset_and_stat(cache, asset, current_stat):
        return normalize_sha256_hex(cache.get("sha256"))

    # 兼容：由 collector 只读入库写入的 collect_sha256（存储在不可变收集目录）
    metadata = asset.get("metadata")
    if isinstance(metadata, Mapping):
        collect_digest = normalize_sha256_hex(metadata.get("collect_sha256"))
        is_collect_inbox = (
            str(asset.get("display_path") or "").startswith("registry_uploads/collect_inbox/")
            or str(asset.get("stored_name") or "").startswith("registry_uploads/collect_inbox/")
        )
        if collect_digest and is_collect_inbox and current_stat is not None:
            asset_size = asset.get("size_bytes")
            if (
                not isinstance(asset_size, bool)
                and isinstance(asset_size, int)
                and int(current_stat.st_size) == asset_size
            ):
                return collect_digest

    return None


def resolve_asset_media_path(
    raw: Mapping[str, Any],
    asset_id: str,
    record: Optional[Mapping[str, Any]] = None,
) -> Optional[Path]:
    """解析资产对应的本地可读文件，兼顾允许根目录、受管上传及收集入库目录。"""
    from gw.asset_registry import repository as repo

    path: Optional[Path] = None
    try:
        path = repo._media_file_in_state(dict(raw), asset_id)
    except CleanroomException:
        path = None
    if path is not None and path.is_file():
        return path

    rec = record if record is not None else (raw.get("assets") or {}).get(asset_id)
    if not isinstance(rec, Mapping):
        return None
    display = str(rec.get("display_path") or "").strip()
    if display.startswith("registry_uploads/collect_inbox/"):
        try:
            inbox_root = (storage.data_root() / "registry_uploads" / "collect_inbox").resolve()
            candidate = (storage.data_root() / display).resolve()
        except (OSError, RuntimeError, ValueError):
            return None
        if candidate.is_relative_to(inbox_root) and candidate.is_file():
            return candidate
    return None




def _is_valid_asset_size(asset: Mapping[str, Any]) -> Optional[int]:
    size = asset.get("size_bytes")
    if isinstance(size, bool) or not isinstance(size, int) or size < 0:
        return None


    return size


# ---------------------------------------------------------------------------
# 聚合带宽令牌桶限速器（可注入时钟/睡眠，跨并发 worker 共享）
# ---------------------------------------------------------------------------


class AggregateRateLimiter:
    """所有后台哈希 worker 共享的令牌桶限速器，限制聚合读带宽。"""

    def __init__(
        self,
        max_bytes_per_sec: int,
        *,
        burst_bytes: Optional[int] = None,
        clock: Callable[[], float] = time.monotonic,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        self.rate = max(0, int(max_bytes_per_sec))
        self.clock = clock
        self.sleeper = sleeper
        self._lock = threading.Lock()
        # 初始与突发令牌控制在较小窗口，避免小样本测试时因初始满桶跳过限速观察。
        default_burst = min(self.rate, 64 * 1024) if self.rate > 0 else 0
        self.capacity = max(1, int(burst_bytes if burst_bytes is not None else default_burst)) if self.rate > 0 else 0
        self._tokens = float(self.capacity)
        self._last_ts = self.clock()

    def acquire(
        self,
        num_bytes: int,
        *,
        check_stopped: Optional[Callable[[], None]] = None,
    ) -> None:
        if self.rate <= 0 or num_bytes <= 0:
            return
        remaining = float(num_bytes)
        while remaining > 0:
            if check_stopped is not None:
                check_stopped()
            with self._lock:
                now = self.clock()
                elapsed = max(0.0, now - self._last_ts)
                if elapsed > 0:
                    self._tokens = min(float(self.capacity), self._tokens + elapsed * self.rate)
                    self._last_ts = now
                if self._tokens >= remaining:
                    self._tokens -= remaining
                    return
                if self._tokens > 0:
                    remaining -= self._tokens
                    self._tokens = 0.0
                wait_sec = min(0.05, remaining / float(self.rate))
            self.sleeper(max(0.001, wait_sec))


# ---------------------------------------------------------------------------
# 跨进程单实例锁与进程身份探测（防 PID 复用）
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ProcessIdentity:
    alive: bool
    start_time_ns: Optional[int]


class DedupLockError(CleanroomException):
    """确认任务跨进程锁冲突或持有者存活无响应。"""


def default_inspect_process(pid: int) -> ProcessIdentity:
    """读取目标进程存活状态与启动时间，避免 PID 复用误判陈旧锁。"""
    if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0:
        return ProcessIdentity(alive=False, start_time_ns=None)
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes

        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        STILL_ACTIVE = 259
        ERROR_ACCESS_DENIED = 5
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            err = ctypes.GetLastError()
            if err == ERROR_ACCESS_DENIED:
                return ProcessIdentity(alive=True, start_time_ns=None)
            return ProcessIdentity(alive=False, start_time_ns=None)
        try:
            exit_code = wintypes.DWORD()
            if kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
                if exit_code.value != STILL_ACTIVE:
                    return ProcessIdentity(alive=False, start_time_ns=None)
            creation = wintypes.FILETIME()
            exit_t = wintypes.FILETIME()
            kernel_t = wintypes.FILETIME()
            user_t = wintypes.FILETIME()
            if not kernel32.GetProcessTimes(
                handle,
                ctypes.byref(creation),
                ctypes.byref(exit_t),
                ctypes.byref(kernel_t),
                ctypes.byref(user_t),
            ):
                return ProcessIdentity(alive=True, start_time_ns=None)
            ticks = (creation.dwHighDateTime << 32) | creation.dwLowDateTime
            return ProcessIdentity(alive=True, start_time_ns=int(ticks) * 100)
        finally:
            kernel32.CloseHandle(handle)
    # POSIX
    proc_stat = Path(f"/proc/{pid}/stat")
    if proc_stat.exists():
        try:
            text = proc_stat.read_text(encoding="utf-8", errors="ignore")
            rparen = text.rfind(")")
            fields = text[rparen + 2 :].split()
            state = fields[0] if fields else ""
            if state == "Z":
                return ProcessIdentity(alive=False, start_time_ns=None)
            starttime = int(fields[19]) if len(fields) > 19 else None
            return ProcessIdentity(alive=True, start_time_ns=starttime)
        except (OSError, ValueError, IndexError):
            pass
    try:
        os.kill(pid, 0)
        return ProcessIdentity(alive=True, start_time_ns=None)
    except ProcessLookupError:
        return ProcessIdentity(alive=False, start_time_ns=None)
    except PermissionError:
        return ProcessIdentity(alive=True, start_time_ns=None)
    except OSError:
        return ProcessIdentity(alive=False, start_time_ns=None)


class DedupWorkerLock:
    """`<data_root>/dedup_confirm.lock` 跨进程互斥锁。

    - 冷启动：无锁且无健康实例时原子建锁并置 ``starting``；
    - 热启动：存在存活且心跳健康的同身份实例时拒绝重复拉起；
    - 陈旧锁（持有者已死 / PID 已被复用 / 锁文件损坏）：通过 ``.recover`` 独占夺标恢复，
      并发恢复严格只有 1 个赢家；
    - 陈旧锁（持有者存活但无响应）：绝不删锁、绝不接管，明确报错；
    - 退出清理：启动中与已运行两种状态下退出，核对 ``instance_id`` 归属后清理锁文件。
    """

    def __init__(
        self,
        root: Path,
        *,
        process_inspector: Callable[[int], ProcessIdentity] = default_inspect_process,
        clock_ns: Callable[[], int] = time.time_ns,
        heartbeat_ttl_seconds: float = DEFAULT_HEARTBEAT_TTL_SECONDS,
        ping_probe: Optional[Callable[[Mapping[str, Any]], bool]] = None,
    ) -> None:
        self.root = Path(root)
        self.lock_path = self.root / LOCK_FILENAME
        self.recover_path = self.root / RECOVER_LOCK_FILENAME
        self.process_inspector = process_inspector
        self.clock_ns = clock_ns
        self.heartbeat_ttl_ns = int(max(0.05, float(heartbeat_ttl_seconds)) * 1_000_000_000)
        self.ping_probe = ping_probe
        self.instance_id = f"dcl_{uuid.uuid4().hex[:16]}"
        self.pid = os.getpid()
        probe = self.process_inspector(self.pid)
        self.start_time_ns = probe.start_time_ns
        self._held = False
        self._payload: dict[str, Any] = {}
        self._mutex = threading.Lock()

    def _build_payload(self, status: str, job_id: str, attempt: int) -> dict[str, Any]:
        now_ns = self.clock_ns()
        return {
            "lock_version": 1,
            "instance_id": self.instance_id,
            "pid": self.pid,
            "process_start_time_ns": self.start_time_ns,
            "process_identity": f"{self.pid}:{self.start_time_ns}",
            "status": status,
            "heartbeat_at_ns": now_ns,
            "job_id": job_id,
            "attempt": int(attempt),
        }

    def _read_existing_lock(self) -> Tuple[Optional[dict[str, Any]], bool]:
        """读取现有锁文件，返回 ``(payload, is_corrupted)``。"""
        try:
            raw_text = self.lock_path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return None, False
        except OSError:
            return None, True
        try:
            data = json.loads(raw_text)
        except (ValueError, TypeError):
            return None, True
        if not isinstance(data, dict):
            return None, True
        required = {"lock_version", "instance_id", "pid", "heartbeat_at_ns", "status"}
        if not required.issubset(data.keys()):
            return None, True
        if isinstance(data["pid"], bool) or not isinstance(data["pid"], int) or data["pid"] <= 0:
            return None, True
        if not isinstance(data["instance_id"], str) or not data["instance_id"]:
            return None, True
        return data, False

    def _atomic_write_lock(self, payload: dict[str, Any]) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        tmp = self.root / f".{LOCK_FILENAME}.{self.instance_id}.tmp"
        try:
            fd = os.open(tmp, os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600)
            try:
                os.write(fd, json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8"))
                os.fsync(fd)
            finally:
                os.close(fd)
            os.replace(tmp, self.lock_path)
        finally:
            with suppress(OSError):
                tmp.unlink(missing_ok=True)

    def _is_holder_alive_same_process(self, existing: Mapping[str, Any]) -> bool:
        holder_pid = int(existing["pid"])
        probe = self.process_inspector(holder_pid)
        if not probe.alive:
            return False
        recorded_start = existing.get("process_start_time_ns")
        # 仅当双侧都能拿到启动时间时做精确身份匹配，避免 PID 复用误判。
        if recorded_start is not None and probe.start_time_ns is not None:
            return int(recorded_start) == int(probe.start_time_ns)
        return True

    def _is_holder_healthy(self, existing: Mapping[str, Any]) -> bool:
        now_ns = self.clock_ns()
        heartbeat = existing.get("heartbeat_at_ns")
        if isinstance(heartbeat, bool) or not isinstance(heartbeat, int):
            return False
        if now_ns - heartbeat > self.heartbeat_ttl_ns:
            return False
        if self.ping_probe is not None:
            return bool(self.ping_probe(existing))
        return True

    def acquire(self, job_id: str, attempt: int) -> dict[str, Any]:
        with self._mutex:
            if self._held:
                return dict(self._payload)
            self.root.mkdir(parents=True, exist_ok=True)
            payload = self._build_payload("starting", job_id, attempt)
            try:
                fd = os.open(self.lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            except FileExistsError:
                pass
            else:
                try:
                    os.write(
                        fd,
                        json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8"),
                    )
                    os.fsync(fd)
                except Exception:
                    os.close(fd)
                    with suppress(OSError):
                        self.lock_path.unlink(missing_ok=True)
                    raise
                os.close(fd)
                self._held = True
                self._payload = payload
                return dict(payload)

            # 锁文件已存在：区分健康热启动 / 存活无响应 / 陈旧损坏恢复
            existing, corrupted = self._read_existing_lock()
            if not corrupted and existing is not None:
                if self._is_holder_alive_same_process(existing):
                    if self._is_holder_healthy(existing):
                        raise DedupLockError(
                            409,
                            "DEDUP_WORKER_ALREADY_RUNNING",
                            "后台查重确认任务已在运行，不重复拉起实例",
                            extra={"holder_instance_id": existing["instance_id"]},
                        )
                    raise DedupLockError(
                        409,
                        "DEDUP_WORKER_UNRESPONSIVE",
                        "查重确认实例进程仍存活但无响应，拒绝强行删锁或接管",
                        extra={"holder_instance_id": existing["instance_id"]},
                    )

            # 允许恢复（锁损坏 或 持有者已死/PID 复用）：通过独占恢复夺标保证唯一赢家
            try:
                rfd = os.open(self.recover_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            except FileExistsError:
                raise DedupLockError(
                    409,
                    "DEDUP_LOCK_RECOVERY_CONTENTION",
                    "另一个恢复流程正在接管陈旧锁",
                ) from None
            try:
                os.write(rfd, self.instance_id.encode("utf-8"))
                os.close(rfd)
                # 夺标后二次检查锁，避免在竞争前已被另一个成功赢家接管
                rechecked, re_corrupted = self._read_existing_lock()
                if not re_corrupted and rechecked is not None and self._is_holder_alive_same_process(rechecked):
                    if self._is_holder_healthy(rechecked):
                        raise DedupLockError(
                            409,
                            "DEDUP_WORKER_ALREADY_RUNNING",
                            "后台查重确认任务已由并发实例接管",
                        )
                    raise DedupLockError(
                        409,
                        "DEDUP_WORKER_UNRESPONSIVE",
                        "查重确认实例进程仍存活但无响应，拒绝强行删锁或接管",
                    )
                self._atomic_write_lock(payload)
                self._held = True
                self._payload = payload
                return dict(payload)
            finally:
                with suppress(OSError):
                    self.recover_path.unlink(missing_ok=True)

    def mark_running(self) -> None:
        with self._mutex:
            if not self._held:
                return
            self._payload["status"] = "running"
            self._payload["heartbeat_at_ns"] = self.clock_ns()
            self._atomic_write_lock(self._payload)

    def heartbeat(self) -> None:
        with self._mutex:
            if not self._held:
                return
            self._payload["heartbeat_at_ns"] = self.clock_ns()
            self._atomic_write_lock(self._payload)

    def release(self) -> None:
        with self._mutex:
            if not self._held:
                return
            try:
                existing, corrupted = self._read_existing_lock()
                if not corrupted and existing is not None and existing.get("instance_id") == self.instance_id:
                    with suppress(OSError):
                        self.lock_path.unlink(missing_ok=True)
            finally:
                self._held = False
                self._payload = {}


# ---------------------------------------------------------------------------
# 持久化任务存储与确认服务
# ---------------------------------------------------------------------------


class _DedupJobState(storage.JsonState):
    """确认任务持久化存储；损坏时失败关闭，防止复用旧 ID 或丢失代际。"""

    def _retry_io(self, operation: Callable[[], Any]) -> Any:
        for attempt in range(_STATE_IO_ATTEMPTS):
            try:
                with storage.namespace_lock(self.namespace):
                    return operation()
            except PermissionError:
                if attempt + 1 >= _STATE_IO_ATTEMPTS:
                    raise
                _STATE_IO_SLEEP(_STATE_IO_BACKOFF_SECONDS * (attempt + 1))
        raise AssertionError("unreachable")  # pragma: no cover

    def read(self) -> dict[str, Any]:
        path = storage.file_for(self.namespace)

        def load_state() -> dict[str, Any]:
            if not path.exists():
                return {"sequence": 0, "generation": 0, "jobs": {}, "keys": {}}
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
                if (
                    type(raw["sequence"]) is not int
                    or type(raw.get("generation", 0)) is not int
                    or not isinstance(raw["jobs"], dict)
                    or not isinstance(raw["keys"], dict)
                ):
                    raise ValueError()
                raw.setdefault("generation", 0)
                return raw
            except PermissionError:
                raise
            except (OSError, ValueError, KeyError, TypeError):
                _error(503, "DEDUP_STORE_INVALID", "查重确认任务持久状态不可读取")

        try:
            return self._retry_io(load_state)
        except PermissionError:
            _error(503, "DEDUP_STORE_INVALID", "查重确认任务持久状态不可读取")

    def _write_locked(self, payload: dict[str, Any]) -> None:
        self._retry_io(lambda: storage.save(self.namespace, payload))

    def write(self, payload: dict[str, Any]) -> None:
        with _transaction_lock(storage.file_for(self.namespace)):
            self._write_locked(payload)

    def mutate(self, mutator: Callable[[dict[str, Any]], Any]) -> Any:
        with _transaction_lock(storage.file_for(self.namespace)):
            state = self.read()
            result = mutator(state)
            self._write_locked(state)
            return result


class _PausedOrCancelled(Exception):
    """工作线程遇到 pause / cancel / 过期代际时优雅中止当前读取。"""


class DedupConfirmJobs:
    """按 size 分桶筛选疑似重复候选，并在后台限速计算完整 SHA-256。"""

    def __init__(self) -> None:
        self.root = storage.data_root().resolve()
        self.store = _DedupJobState(
            JOB_STORE_NAMESPACE,
            lambda: {"sequence": 0, "generation": 0, "jobs": {}, "keys": {}},
        )
        self.lock = threading.RLock()
        self.condition = threading.Condition(self.lock)
        self._runtime_path_configuration = _runtime_path_configuration()
        self.closed = False
        self._active_worker_threads: set[threading.Thread] = set()
        self._running_jobs: dict[str, threading.Thread] = {}
        self._active_worker_slot: Optional[str] = None
        # 可测试注入点
        self._test_rate_config: dict[str, Any] = {}
        self._test_read_chunk_hook: Optional[Callable[[Path, int], None]] = None
        self._test_process_inspector = default_inspect_process
        self._test_clock: Callable[[], float] = time.monotonic
        self._test_sleeper: Callable[[float], None] = time.sleep
        self._active_readers = 0
        self._peak_active_readers = 0
        self._recover()

    def _recover(self) -> None:
        """重启恢复规则：绝不自动启动后台线程池或抢占磁盘资源。

        queued / running 未终态收敛为 interrupted；paused 保持 paused。
        """
        def restore(raw: dict[str, Any]) -> None:
            for job in raw["jobs"].values():
                if job.get("status") in {"queued", "running"}:
                    job["status"] = "interrupted"
                    job["phase"] = "interrupted"
                    job["error_code"] = "DEDUP_PROCESS_INTERRUPTED"
                    job["version"] = int(job.get("version", 1)) + 1
                    job["updated_at"] = storage.now_iso()

        self.store.mutate(restore)

    def shutdown(self) -> None:
        with self.condition:
            self.closed = True
            self.condition.notify_all()
            threads = list(self._active_worker_threads)
        for thread in threads:
            if thread.is_alive() and thread is not threading.current_thread():
                thread.join(timeout=5.0)

    def configure_test_hooks(
        self,
        *,
        max_read_bytes_per_sec: Optional[int] = None,
        max_concurrency: Optional[int] = None,
        chunk_size_bytes: Optional[int] = None,
        read_chunk_hook: Optional[Callable[[Path, int], None]] = None,
        process_inspector: Optional[Callable[[int], ProcessIdentity]] = None,
        clock: Optional[Callable[[], float]] = None,
        sleeper: Optional[Callable[[float], None]] = None,
    ) -> None:
        with self.condition:
            if max_read_bytes_per_sec is not None:
                self._test_rate_config["max_read_bytes_per_sec"] = int(max_read_bytes_per_sec)
            if max_concurrency is not None:
                self._test_rate_config["max_concurrency"] = int(max_concurrency)
            if chunk_size_bytes is not None:
                self._test_rate_config["chunk_size_bytes"] = int(chunk_size_bytes)
            if read_chunk_hook is not None:
                self._test_read_chunk_hook = read_chunk_hook
            if process_inspector is not None:
                self._test_process_inspector = process_inspector
            if clock is not None:
                self._test_clock = clock
            if sleeper is not None:
                self._test_sleeper = sleeper

    def _admission(self, actor: Mapping[str, Any]) -> None:
        from gw.asset_registry.index_jobs import _admission as index_admission

        index_admission(dict(actor))

    def _check(self, job: Mapping[str, Any]) -> None:
        if self.closed or _runtime_path_configuration() != self._runtime_path_configuration:
            _error(409, "DEDUP_PROCESS_INTERRUPTED", "确认任务执行环境已停止")
        self._admission(job["_actor"])

    def _job(self, job_id: str, auth: Any) -> dict[str, Any]:
        job = self.store.read()["jobs"].get(job_id)
        if not job or job.get("_owner") != _owner(_actor(auth)):
            _error(404, "WORKSPACE_JOB_NOT_FOUND", "工作区任务不存在")
        return job

    def _save(self, job: dict[str, Any]) -> None:
        job["updated_at"] = storage.now_iso()
        self.store.mutate(lambda raw: raw["jobs"].__setitem__(job["job_id"], job))

    @staticmethod
    def public(job: Mapping[str, Any]) -> dict[str, Any]:
        public = {k: v for k, v in job.items() if not k.startswith("_")}
        status = str(job.get("status") or "unknown")
        error_code = str(job.get("error_code") or "")
        if status == "succeeded":
            projection = {
                "data_status": "ok",
                "data_gaps": [],
                "next_action": "review_result",
                "result_unknown": False,
            }
        elif status in {"queued", "running"}:
            projection = {
                "data_status": "degraded",
                "data_gaps": ["DEDUP_PENDING"],
                "next_action": "poll_job",
                "result_unknown": False,
            }
        elif status == "paused":
            projection = {
                "data_status": "degraded",
                "data_gaps": ["DEDUP_PAUSED"],
                "next_action": "resume_dedup",
                "result_unknown": False,
            }
        elif status == "failed":
            projection = {
                "data_status": "degraded",
                "data_gaps": [error_code or "DEDUP_EXECUTION_FAILED"],
                "next_action": "retry_dedup",
                "result_unknown": False,
            }
        elif status == "cancelled":
            projection = {
                "data_status": "degraded",
                "data_gaps": [error_code or "DEDUP_CANCELLED"],
                "next_action": "retry_dedup",
                "result_unknown": False,
            }
        elif status == "interrupted":
            projection = {
                "data_status": "degraded",
                "data_gaps": [error_code or "DEDUP_PROCESS_INTERRUPTED"],
                "next_action": "reconcile_dedup_job",
                "result_unknown": True,
            }
        else:
            projection = {
                "data_status": "degraded",
                "data_gaps": ["DEDUP_STATUS_UNKNOWN"],
                "next_action": "reconcile_dedup_job",
                "result_unknown": True,
            }
        public.update(projection)
        public["has_error"] = bool(job.get("error_code"))
        total = int(job.get("total_candidates") or 0)
        processed = int(job.get("processed_candidates") or 0)
        percent = int(round((processed * 100.0) / total)) if total > 0 else (100 if status == "succeeded" else 0)
        public["progress"] = {
            "scanned": processed,
            "processed": processed,
            "total": total,
            "percent": min(100, percent),
            "error_count": int(job.get("error_count") or 0),
            "phase": job.get("phase") or status,
        }
        return public

    def detail(self, job_id: str, auth: Any) -> dict[str, Any]:
        with self.lock:
            job = self._job(job_id, auth)
            writable = getattr(auth, "role", "") in EDIT_ROLES
            actions = {
                action: writable
                and job["status"] in states
                and (action != "retry" or int(job.get("attempt", 1)) < int(job.get("max_attempts", 5)))
                for action, states in ACTIONS.items()
            }
            view = self.public(job)
            return {
                "job": view,
                "actions": actions,
                "action_reasons": {
                    action: "当前状态或权限不允许此操作"
                    for action, ok in actions.items()
                    if not ok
                },
                "data_status": view["data_status"],
                "data_gaps": view["data_gaps"],
                "next_action": view["next_action"],
                "result_unknown": view["result_unknown"],
            }

    def listing(self, auth: Any) -> list[dict[str, Any]]:
        with self.lock:
            owner = _owner(_actor(auth))
            jobs = [
                self.public(job)
                for job in self.store.read()["jobs"].values()
                if job.get("_owner") == owner
            ]
            return sorted(jobs, key=lambda item: item["created_at"], reverse=True)[:100]

    def _build_candidates(
        self,
        *,
        asset_ids: Optional[Sequence[str]] = None,
    ) -> tuple[list[dict[str, Any]], int]:
        """按精确 size_bytes 分桶，只筛选同 size >= 2 的疑似重复候选。"""
        from gw.asset_registry import repository as repo

        raw = repo.readonly_registry_snapshot()
        removed_ids = repo._removed_root_ids()
        filter_ids = {str(aid).strip() for aid in (asset_ids or ()) if str(aid).strip()}

        by_size: dict[int, list[tuple[str, dict[str, Any], Optional[Path]]]] = defaultdict(list)
        for aid, record in sorted(raw.get("assets", {}).items()):
            if not isinstance(record, dict) or record.get("archived") or not asset_visible(record):
                continue
            if repo._hidden_builtin(record) or repo._is_removed_asset(record, removed_ids):
                continue
            if filter_ids and aid not in filter_ids:
                continue
            size = _is_valid_asset_size(record)
            if size is None:
                continue
            path = resolve_asset_media_path(raw, str(aid), record)
            if path is None or not path.is_file():
                continue
            by_size[size].append((str(aid), record, path))

        candidates: list[dict[str, Any]] = []
        unique_skipped = 0
        for size in sorted(by_size):
            bucket = by_size[size]
            if len(bucket) < 2:
                unique_skipped += len(bucket)
                continue
            for aid, record, path in bucket:
                try:
                    st = path.lstat()
                except OSError:
                    continue
                if int(st.st_size) != size:
                    # 注册表大小已过期时以当前磁盘大小重新验证或仍待计算
                    pass
                cached = trusted_full_sha256(record, path=path, stat_result=st)
                candidates.append(
                    {
                        "asset_id": aid,
                        "expected_size_bytes": int(st.st_size),
                        "expected_mtime_ns": int(st.st_mtime_ns),
                        "expected_ino": int(getattr(st, "st_ino", 0) or 0),
                        "expected_dev": int(getattr(st, "st_dev", 0) or 0),
                        "file_state": "reused" if cached else "pending",
                        "sha256": cached or None,
                    }
                )
        return candidates, unique_skipped

    def _resolve_rate_params(self, payload: Mapping[str, Any]) -> dict[str, int]:
        max_rate = payload.get(
            "max_read_bytes_per_sec",
            self._test_rate_config.get("max_read_bytes_per_sec", DEFAULT_MAX_READ_BYTES_PER_SEC),
        )
        max_conc = payload.get(
            "max_concurrency",
            self._test_rate_config.get("max_concurrency", DEFAULT_MAX_CONCURRENCY),
        )
        chunk_size = payload.get(
            "chunk_size_bytes",
            self._test_rate_config.get("chunk_size_bytes", DEFAULT_CHUNK_SIZE_BYTES),
        )
        if isinstance(max_rate, bool) or not isinstance(max_rate, int) or max_rate < 0:
            _error(400, "INVALID_REQUEST", "max_read_bytes_per_sec 必须是非负整数")
        if isinstance(max_conc, bool) or not isinstance(max_conc, int) or not 1 <= max_conc <= MAX_ALLOWED_CONCURRENCY:
            _error(400, "INVALID_REQUEST", f"max_concurrency 必须在 1 到 {MAX_ALLOWED_CONCURRENCY} 之间")
        if isinstance(chunk_size, bool) or not isinstance(chunk_size, int) or chunk_size < 1024:
            _error(400, "INVALID_REQUEST", "chunk_size_bytes 必须是 >= 1024 的整数")
        return {
            "max_read_bytes_per_sec": int(max_rate),
            "max_concurrency": int(max_conc),
            "chunk_size_bytes": int(chunk_size),
        }

    def submit(
        self,
        payload: Mapping[str, Any],
        auth: Any,
        idempotency_key: str,
    ) -> dict[str, Any]:
        if not isinstance(idempotency_key, str) or not 1 <= len(idempotency_key) <= 160:
            _error(400, "DEDUP_IDEMPOTENCY_KEY_REQUIRED", "查重确认任务需要不超过 160 字符的幂等键")
        actor = _actor(auth)
        self._admission(actor)
        rate_params = self._resolve_rate_params(payload)
        raw_asset_ids = payload.get("asset_ids")
        asset_ids: Optional[list[str]] = None
        if raw_asset_ids is not None:
            if not isinstance(raw_asset_ids, list) or any(not isinstance(x, str) or not x.strip() for x in raw_asset_ids):
                _error(400, "INVALID_REQUEST", "asset_ids 必须是非空字符串列表")
            asset_ids = sorted({x.strip() for x in raw_asset_ids})

        request = {
            "asset_ids": asset_ids,
            **rate_params,
        }
        request_digest = _digest(request)
        key_digest = _digest([_owner(actor), idempotency_key])

        with self.condition:
            pre_raw = self.store.read()
            previous = pre_raw["keys"].get(key_digest)
            if previous and previous in pre_raw["jobs"]:
                prev_job = pre_raw["jobs"][previous]
                if prev_job.get("_request_digest") != request_digest:
                    _error(409, "DEDUP_IDEMPOTENCY_CONFLICT", "幂等键已用于不同的确认请求")
                return self._accepted(prev_job)
            active_count = sum(1 for item in pre_raw["jobs"].values() if item.get("status") not in TERMINAL)
            if len(pre_raw["jobs"]) >= MAX_JOBS or active_count >= MAX_ACTIVE:
                _error(429, "DEDUP_QUEUE_FULL", "查重确认任务队列已满")

            candidates, unique_skipped = self._build_candidates(asset_ids=asset_ids)
            reused_count = sum(1 for item in candidates if item["file_state"] == "reused")
            owner_scope = current_asset_owner.get()
            if owner_scope is None and hasattr(auth, "subject"):
                owner_scope = asset_owner_key(auth)

            def create_job(raw: dict[str, Any]) -> tuple[dict[str, Any], bool]:
                existing_id = raw["keys"].get(key_digest)
                if existing_id and existing_id in raw["jobs"]:
                    prev_job = raw["jobs"][existing_id]
                    if prev_job.get("_request_digest") != request_digest:
                        _error(409, "DEDUP_IDEMPOTENCY_CONFLICT", "幂等键已用于不同的确认请求")
                    return dict(prev_job), False
                cur_active = sum(1 for item in raw["jobs"].values() if item.get("status") not in TERMINAL)
                if len(raw["jobs"]) >= MAX_JOBS or cur_active >= MAX_ACTIVE:
                    _error(429, "DEDUP_QUEUE_FULL", "查重确认任务队列已满")
                raw["sequence"] += 1
                raw["generation"] += 1
                new_job_id = f"{JOB_ID_PREFIX}{raw['sequence']:08d}"
                new_gen = int(raw["generation"])
                new_job = {
                    "job_id": new_job_id,
                    "job_type": JOB_TYPE_DEDUP_CONFIRM,
                    "name": "查重完整 SHA 确认",
                    "status": "queued",
                    "phase": "queued",
                    "version": 1,
                    "attempt": 1,
                    "max_attempts": 5,
                    "generation": new_gen,
                    "created_at": storage.now_iso(),
                    "updated_at": storage.now_iso(),
                    "total_candidates": len(candidates),
                    "processed_candidates": reused_count,
                    "unique_size_skipped": unique_skipped,
                    "error_count": 0,
                    "failed_assets": [],
                    "result": None,
                    "error_code": None,
                    "_actor": actor,
                    "_owner": _owner(actor),
                    "_asset_owner_key": owner_scope,
                    "_request": request,
                    "_request_digest": request_digest,
                    "_candidates": candidates,
                }
                raw["jobs"][new_job_id] = new_job
                raw["keys"][key_digest] = new_job_id
                return dict(new_job), True

            job, created = self.store.mutate(create_job)
            if created:
                self._dispatch(job)
            return self._accepted(job)

    def _accepted(self, job: Mapping[str, Any]) -> dict[str, Any]:
        view = self.public(job)
        return {
            "job_id": job["job_id"],
            "job": view,
            "poll_hint": {
                "url": f"/api/asset-registry/workspace-jobs/{job['job_id']}",
                "interval_ms": 500,
            },
            "data_status": view["data_status"],
            "data_gaps": view["data_gaps"],
            "next_action": view["next_action"],
            "result_unknown": view["result_unknown"],
        }

    def _dispatch(self, job: dict[str, Any]) -> None:
        job_id = str(job["job_id"])
        thread = threading.Thread(
            target=self._run,
            args=(job_id, int(job["attempt"]), int(job["generation"])),
            name=f"gw-dedup-{job_id}",
            daemon=True,
        )
        with self.condition:
            existing = self._running_jobs.get(job_id)
            if existing is not None and existing.is_alive():
                return
            self._active_worker_threads.add(thread)
            self._running_jobs[job_id] = thread
        thread.start()

    def operate(self, job_id: str, action: str, payload: Mapping[str, Any], auth: Any) -> dict[str, Any]:
        if action not in ACTIONS:
            _error(400, "INVALID_ACTION", "不支持的任务操作")
        expected = payload.get("expected_version")
        if type(expected) is not int or expected < 1:
            _error(400, "EXPECTED_VERSION_REQUIRED", "任务操作必须携带 expected_version")

        with self.condition:
            pre_job = self._job(job_id, auth)
            if expected != pre_job["version"]:
                _error(409, "VERSION_CONFLICT", "任务版本已改变，请重新读取")
            self._check(pre_job)
            if pre_job["status"] not in ACTIONS[action] or (
                action == "retry" and int(pre_job.get("attempt", 1)) >= int(pre_job.get("max_attempts", 5))
            ):
                _error(409, "DEDUP_ACTION_CONFLICT", "当前任务状态不允许此操作")

            if action == "retry":
                req = pre_job.get("_request") or {}
                refreshed, unique_skipped = self._build_candidates(
                    asset_ids=req.get("asset_ids")
                )

                def mutate_retry(raw: dict[str, Any]) -> dict[str, Any]:
                    cur = raw["jobs"].get(job_id)
                    if not cur:
                        _error(404, "DEDUP_JOB_NOT_FOUND", "查重确认任务不存在")
                    if expected != cur["version"]:
                        _error(409, "VERSION_CONFLICT", "任务版本已改变，请重新读取")
                    if cur["status"] not in ACTIONS["retry"] or int(cur.get("attempt", 1)) >= int(cur.get("max_attempts", 5)):
                        _error(409, "DEDUP_ACTION_CONFLICT", "当前任务状态不允许此操作")
                    if sum(1 for item in raw["jobs"].values() if item.get("status") not in TERMINAL) >= MAX_ACTIVE:
                        _error(429, "DEDUP_QUEUE_FULL", "查重确认任务队列已满")
                    raw["generation"] += 1
                    new_gen = int(raw["generation"])
                    prior_map = {
                        str(item.get("asset_id")): item
                        for item in (cur.get("_candidates") or [])
                        if item.get("asset_id")
                    }
                    for cand in refreshed:
                        prev = prior_map.get(cand["asset_id"])
                        if (
                            prev
                            and prev.get("file_state") in {"verified", "reused"}
                            and prev.get("expected_size_bytes") == cand.get("expected_size_bytes")
                            and prev.get("expected_mtime_ns") == cand.get("expected_mtime_ns")
                            and prev.get("expected_ino") == cand.get("expected_ino")
                            and prev.get("expected_dev") == cand.get("expected_dev")
                            and cand.get("sha256")
                        ):
                            cand["file_state"] = prev["file_state"]
                    cur["_candidates"] = refreshed
                    cur["total_candidates"] = len(refreshed)
                    cur["processed_candidates"] = sum(
                        1 for item in refreshed if item.get("file_state") in {"verified", "reused"}
                    )
                    cur["unique_size_skipped"] = unique_skipped
                    cur["status"] = "queued"
                    cur["phase"] = "queued"
                    cur["attempt"] = int(cur["attempt"]) + 1
                    cur["generation"] = new_gen
                    cur["error_code"] = None
                    cur["error_count"] = 0
                    cur["failed_assets"] = []
                    cur["result"] = None
                    cur["version"] += 1
                    cur["updated_at"] = storage.now_iso()
                    return dict(cur)

                updated_job = self.store.mutate(mutate_retry)
                self.condition.notify_all()
                self._dispatch(updated_job)
            else:
                # 仅以原子 mutate 修改状态控制字段，禁止整对象覆盖写坏工作线程刚提交的 _candidates / processed_candidates
                def mutate_state(raw: dict[str, Any]) -> dict[str, Any]:
                    cur = raw["jobs"].get(job_id)
                    if not cur:
                        _error(404, "DEDUP_JOB_NOT_FOUND", "查重确认任务不存在")
                    if expected != cur["version"]:
                        _error(409, "VERSION_CONFLICT", "任务版本已改变，请重新读取")
                    if cur["status"] not in ACTIONS[action]:
                        _error(409, "DEDUP_ACTION_CONFLICT", "当前任务状态不允许此操作")
                    if action == "pause":
                        cur["status"] = "paused"
                        cur["phase"] = "paused"
                    elif action == "resume":
                        cur["status"] = "running"
                        cur["phase"] = "hashing"
                    elif action == "cancel":
                        # 递增 generation 防止在途旧计算结果写回（AC12）
                        raw["generation"] += 1
                        cur["generation"] = int(raw["generation"])
                        cur["status"] = "cancelled"
                        cur["phase"] = "cancelled"
                    cur["version"] += 1
                    cur["updated_at"] = storage.now_iso()
                    return dict(cur)

                updated_job = self.store.mutate(mutate_state)
                self.condition.notify_all()
                if action == "resume":
                    live = self._running_jobs.get(job_id)
                    if live is None or not live.is_alive():
                        self._dispatch(updated_job)
            return self.detail(job_id, auth)

    def _checkpoint(self, job_id: str, attempt: int, generation: int) -> dict[str, Any]:
        with self.condition:
            if self.closed or _runtime_path_configuration() != self._runtime_path_configuration:
                raise _PausedOrCancelled()
            job = self.store.read()["jobs"].get(job_id)
            if (
                not job
                or int(job.get("attempt", 0)) != attempt
                or int(job.get("generation", 0)) != generation
                or job.get("status") in TERMINAL
                or job.get("status") == "paused"
            ):
                raise _PausedOrCancelled()
            return job

    def _check_not_paused_or_cancelled_for_read(self, job_id: str, attempt: int, generation: int) -> None:
        with self.condition:
            if self.closed or _runtime_path_configuration() != self._runtime_path_configuration:
                raise _PausedOrCancelled()
            job = self.store.read()["jobs"].get(job_id)
            if (
                not job
                or int(job.get("attempt", 0)) != attempt
                or int(job.get("generation", 0)) != generation
                or job.get("status") in TERMINAL
                or job.get("status") == "paused"
            ):
                raise _PausedOrCancelled()

    def _compute_file_full_sha256(
        self,
        job_id: str,
        attempt: int,
        generation: int,
        asset_id: str,
        path: Path,
        limiter: AggregateRateLimiter,
        chunk_size: int,
    ) -> tuple[str, os.stat_result]:
        """在限速与防篡改校验下计算单个文件完整 SHA-256；读中修改抛出 DEDUP_FILE_CHANGED。"""
        self._check_not_paused_or_cancelled_for_read(job_id, attempt, generation)
        if path.resolve() != path.absolute() and not storage._is_network_path(str(path)):
            _error(403, "DEDUP_PATH_CHANGED", "摘要源路径已改变")
        expected = path.lstat()
        handle = path.open("rb")
        with self.condition:
            self._active_readers += 1
            if self._active_readers > self._peak_active_readers:
                self._peak_active_readers = self._active_readers
        try:
            actual = os.fstat(handle.fileno())
            if (
                actual.st_size != expected.st_size
                or int(getattr(actual, "st_ino", 0) or 0) != int(getattr(expected, "st_ino", 0) or 0)
                or int(getattr(actual, "st_dev", 0) or 0) != int(getattr(expected, "st_dev", 0) or 0)
            ):
                _error(409, "DEDUP_FILE_CHANGED", "源文件打开时已改变")
            digest = hashlib.sha256()
            offset = 0
            while True:
                self._check_not_paused_or_cancelled_for_read(job_id, attempt, generation)
                remaining_hint = max(1, int(expected.st_size) - offset)
                limiter.acquire(
                    min(chunk_size, remaining_hint),
                    check_stopped=lambda: self._check_not_paused_or_cancelled_for_read(
                        job_id, attempt, generation
                    ),
                )
                if self._test_read_chunk_hook is not None:
                    self._test_read_chunk_hook(path, offset)
                self._check_not_paused_or_cancelled_for_read(job_id, attempt, generation)
                chunk = handle.read(chunk_size)
                if not chunk:
                    break
                digest.update(chunk)
                offset += len(chunk)
            final = os.fstat(handle.fileno())
            final_lstat = path.lstat()
            if (
                final.st_size != expected.st_size
                or int(final.st_mtime_ns) != int(expected.st_mtime_ns)
                or int(final_lstat.st_size) != int(expected.st_size)
                or int(final_lstat.st_mtime_ns) != int(expected.st_mtime_ns)
                or int(getattr(final_lstat, "st_ino", 0) or 0) != int(getattr(expected, "st_ino", 0) or 0)
            ):
                _error(409, "DEDUP_FILE_CHANGED", "摘要计算期间文件已改变")
            return digest.hexdigest(), final
        finally:
            handle.close()
            with self.condition:
                self._active_readers = max(0, self._active_readers - 1)

    def _invalidate_asset_cache(self, asset_id: str) -> None:
        from gw.asset_registry import repository as repo

        def mutate(raw: dict[str, Any]) -> None:
            record = raw.get("assets", {}).get(asset_id)
            if isinstance(record, dict) and invalidate_full_sha256_cache(record):
                record["updated_at"] = storage.now_iso()
                repo._bump(raw)

        with suppress(Exception):
            repo.state().mutate(mutate)

    def _commit_verified_file(
        self,
        job_id: str,
        attempt: int,
        generation: int,
        asset_id: str,
        digest: str,
        stat_result: os.stat_result,
    ) -> bool:
        """仅当当前代际与状态仍有效时提交单文件完整 SHA 缓存；原子更新注册表。"""
        from gw.asset_registry import repository as repo

        with self.condition:
            job = self.store.read()["jobs"].get(job_id)
            if (
                not job
                or int(job.get("attempt", 0)) != attempt
                or int(job.get("generation", 0)) != generation
                or job.get("status") != "running"
            ):
                return False

        cache_entry = build_full_sha256_cache(
            digest,
            stat_result,
            source="dedup_confirm",
            generation=generation,
        )

        # 第一步：原子提交注册表单文件缓存
        def mutate_registry(raw: dict[str, Any]) -> bool:
            record = raw.get("assets", {}).get(asset_id)
            if not isinstance(record, dict) or record.get("archived") or not asset_visible(record):
                return False
            # 再次核对生成代际，防止刚取消或被新一轮取代
            current_job = self.store.read()["jobs"].get(job_id)
            if (
                not current_job
                or int(current_job.get("attempt", 0)) != attempt
                or int(current_job.get("generation", 0)) != generation
                or current_job.get("status") != "running"
            ):
                return False
            # 绝不把 sampled 字段覆盖掉（保护 shot_source_key 与 QIN-575）
            record["full_sha256_cache"] = cache_entry
            if str(record.get("hash_mode") or "") != "sampled" and not record.get("sha256"):
                # 仅对原本无哈希的普通文件同步可复用全量摘要标记，不碰 sampled！
                if int(stat_result.st_size) <= 512 * 1024 * 1024:
                    record["sha256"] = digest
                    record["hash_mode"] = "full"
            record["size_bytes"] = int(stat_result.st_size)
            record["updated_at"] = storage.now_iso()
            repo._bump(raw)
            return True

        committed = repo.state().mutate(mutate_registry)
        if not committed:
            return False

        # 第二步：推进任务文件级 checkpoint
        def mutate_job(raw: dict[str, Any]) -> None:
            current = raw["jobs"].get(job_id)
            if (
                not current
                or int(current.get("attempt", 0)) != attempt
                or int(current.get("generation", 0)) != generation
                or current.get("status") in TERMINAL
            ):
                return
            for item in current.get("_candidates", []):
                if item.get("asset_id") == asset_id:
                    item["file_state"] = "verified"
                    item["sha256"] = digest
                    item["expected_size_bytes"] = int(stat_result.st_size)
                    item["expected_mtime_ns"] = int(stat_result.st_mtime_ns)
                    item["expected_ino"] = int(getattr(stat_result, "st_ino", 0) or 0)
                    item["expected_dev"] = int(getattr(stat_result, "st_dev", 0) or 0)
            current["processed_candidates"] = sum(
                1 for item in current.get("_candidates", []) if item.get("file_state") in {"verified", "reused"}
            )
            current["updated_at"] = storage.now_iso()

        self.store.mutate(mutate_job)
        return True

    def _run(self, job_id: str, attempt: int, generation: int) -> None:
        current_thread = threading.current_thread()
        worker_lock = DedupWorkerLock(
            self.root,
            process_inspector=self._test_process_inspector,
        )
        owner_token = None
        acquired_slot = False
        acquired_lock = False
        try:
            with self.condition:
                while self._active_worker_slot not in (None, job_id):
                    if self.closed or _runtime_path_configuration() != self._runtime_path_configuration:
                        return
                    queued_job = self.store.read()["jobs"].get(job_id)
                    if (
                        not queued_job
                        or int(queued_job.get("attempt", 0)) != attempt
                        or int(queued_job.get("generation", 0)) != generation
                        or queued_job.get("status") in TERMINAL
                        or queued_job.get("status") == "paused"
                    ):
                        return
                    self.condition.wait(0.05)
                if self.closed or _runtime_path_configuration() != self._runtime_path_configuration:
                    return
                pre_job = self.store.read()["jobs"].get(job_id)
                if (
                    not pre_job
                    or int(pre_job.get("attempt", 0)) != attempt
                    or int(pre_job.get("generation", 0)) != generation
                    or pre_job.get("status") in TERMINAL
                    or pre_job.get("status") == "paused"
                ):
                    return
                self._active_worker_slot = job_id
                acquired_slot = True

            worker_lock.acquire(job_id, attempt)
            acquired_lock = True

            def mark_started(raw: dict[str, Any]) -> Optional[dict[str, Any]]:
                cur = raw["jobs"].get(job_id)
                if (
                    not cur
                    or int(cur.get("attempt", 0)) != attempt
                    or int(cur.get("generation", 0)) != generation
                    or cur.get("status") in TERMINAL
                    or cur.get("status") == "paused"
                ):
                    return None
                if cur.get("status") == "queued":
                    cur["status"] = "running"
                    cur["phase"] = "hashing"
                    cur["version"] += 1
                    cur["updated_at"] = storage.now_iso()
                return dict(cur)

            job = self.store.mutate(mark_started)
            if not job:
                return
            if job.get("_asset_owner_key") is not None:
                owner_token = current_asset_owner.set(job.get("_asset_owner_key"))
            worker_lock.mark_running()

            job = self._checkpoint(job_id, attempt, generation)
            self._check(job)
            req = job["_request"]
            limiter = AggregateRateLimiter(
                int(req["max_read_bytes_per_sec"]),
                burst_bytes=min(int(req["max_read_bytes_per_sec"]), int(req["chunk_size_bytes"]))
                if int(req["max_read_bytes_per_sec"]) > 0
                else 0,
                clock=self._test_clock,
                sleeper=self._test_sleeper,
            )
            chunk_size = int(req["chunk_size_bytes"])
            max_conc = int(req["max_concurrency"])

            # 续算/重跑前刷新候选并校验已完成项的文件版本是否仍匹配（AC5/AC8）
            from gw.asset_registry import repository as repo

            while True:
                job = self._checkpoint(job_id, attempt, generation)
                self._check(job)
                worker_lock.heartbeat()

                raw_assets = repo.readonly_registry_snapshot()
                pending_batch: list[tuple[str, Path]] = []
                invalidated_ids: set[str] = set()
                missing_pending_ids: set[str] = set()
                for item in job.get("_candidates", []):
                    aid = str(item.get("asset_id") or "")
                    if not aid:
                        continue
                    record = raw_assets.get("assets", {}).get(aid)
                    if not isinstance(record, dict) or record.get("archived") or not asset_visible(record):
                        continue
                    path = resolve_asset_media_path(raw_assets, aid, record)
                    state_now = item.get("file_state")
                    if path is None or not path.is_file():
                        if state_now in {"verified", "reused"}:
                            invalidated_ids.add(aid)
                            self._invalidate_asset_cache(aid)
                            missing_pending_ids.add(aid)
                        elif state_now == "pending":
                            self._invalidate_asset_cache(aid)
                            missing_pending_ids.add(aid)
                        continue
                    if state_now in {"verified", "reused"}:
                        # 断点不混用不同文件版本：若盘上文件已变化，重算并同步回写磁盘任务状态
                        try:
                            st = path.lstat()
                        except OSError:
                            st = None
                        trusted = (
                            trusted_full_sha256(record, path=path, stat_result=st)
                            if st is not None
                            else None
                        )
                        version_matches = (
                            st is not None
                            and trusted is not None
                            and int(st.st_size) == int(item.get("expected_size_bytes") or -1)
                            and int(st.st_mtime_ns) == int(item.get("expected_mtime_ns") or -1)
                            and int(getattr(st, "st_ino", 0) or 0) == int(item.get("expected_ino") or 0)
                            and int(getattr(st, "st_dev", 0) or 0) == int(item.get("expected_dev") or 0)
                        )
                        if version_matches:
                            continue
                        invalidated_ids.add(aid)
                        pending_batch.append((aid, path))
                    elif state_now == "pending":
                        pending_batch.append((aid, path))

                if invalidated_ids or missing_pending_ids:
                    def persist_invalidations(raw: dict[str, Any]) -> None:
                        cur = raw["jobs"].get(job_id)
                        if (
                            not cur
                            or int(cur.get("attempt", 0)) != attempt
                            or int(cur.get("generation", 0)) != generation
                            or cur.get("status") in TERMINAL
                        ):
                            return
                        for c_item in cur.get("_candidates", []):
                            cid = str(c_item.get("asset_id") or "")
                            if cid in missing_pending_ids:
                                if c_item.get("file_state") != "failed":
                                    c_item["file_state"] = "failed"
                                    c_item["sha256"] = None
                                    c_item["error_code"] = "DEDUP_FILE_READ_FAILED"
                                    cur["error_count"] = int(cur.get("error_count") or 0) + 1
                                    failed_list = cur.setdefault("failed_assets", [])
                                    if cid not in failed_list:
                                        failed_list.append(cid)
                            elif cid in invalidated_ids and c_item.get("file_state") in {"verified", "reused"}:
                                c_item["file_state"] = "pending"
                                c_item["sha256"] = None
                        cur["processed_candidates"] = sum(
                            1
                            for c_item in cur.get("_candidates", [])
                            if c_item.get("file_state") in {"verified", "reused"}
                        )
                        cur["updated_at"] = storage.now_iso()

                    self.store.mutate(persist_invalidations)

                if not pending_batch:
                    break

                def process_one(pair: tuple[str, Path]) -> None:
                    aid, file_path = pair
                    self._checkpoint(job_id, attempt, generation)
                    try:
                        digest, st = self._compute_file_full_sha256(
                            job_id,
                            attempt,
                            generation,
                            aid,
                            file_path,
                            limiter,
                            chunk_size,
                        )
                        self._commit_verified_file(job_id, attempt, generation, aid, digest, st)
                    except _PausedOrCancelled:
                        return
                    except (CleanroomException, OSError) as exc:
                        self._invalidate_asset_cache(aid)
                        err_code = exc.code if isinstance(exc, CleanroomException) else "DEDUP_FILE_READ_FAILED"

                        def mark_fail(raw: dict[str, Any]) -> None:
                            cur = raw["jobs"].get(job_id)
                            if (
                                not cur
                                or int(cur.get("attempt", 0)) != attempt
                                or int(cur.get("generation", 0)) != generation
                                or cur.get("status") in TERMINAL
                            ):
                                return
                            for c_item in cur.get("_candidates", []):
                                if c_item.get("asset_id") == aid:
                                    c_item["file_state"] = "failed"
                                    c_item["error_code"] = err_code
                            cur["error_count"] = int(cur.get("error_count") or 0) + 1
                            failed_list = cur.setdefault("failed_assets", [])
                            if aid not in failed_list:
                                failed_list.append(aid)
                            cur["updated_at"] = storage.now_iso()

                        self.store.mutate(mark_fail)

                # 按小批次调度并在每批之间走 _checkpoint，保证 pause 时迅速停止新增文件读取；
                # 批后回到循环顶部重新盘检所有候选版本，绝不因内存未落盘或尾部文件变化提前退出。
                with ThreadPoolExecutor(max_workers=max_conc, thread_name_prefix="gw-dedup-hash") as pool:
                    futures = [
                        pool.submit(contextvars.copy_context().run, process_one, pair)
                        for pair in pending_batch[: max_conc * 2]
                    ]
                    wait(futures)

                self._checkpoint(job_id, attempt, generation)

            # 完成汇总（以 store.mutate 原子校验代际/非终态/非 paused）
            from gw.asset_registry import governance

            suggestions = governance.build_suggestions()
            dup_entries = suggestions["suggestions"].get("duplicate_files", [])
            confirmed_groups = sum(1 for entry in dup_entries if entry.get("dedup_status") == "confirmed")
            suspected_groups = sum(1 for entry in dup_entries if entry.get("dedup_status") != "confirmed")

            def finalize_job(raw: dict[str, Any]) -> None:
                cur = raw["jobs"].get(job_id)
                if (
                    not cur
                    or int(cur.get("attempt", 0)) != attempt
                    or int(cur.get("generation", 0)) != generation
                    or cur.get("status") in TERMINAL
                    or cur.get("status") == "paused"
                ):
                    return
                candidates = cur.get("_candidates", [])
                verified_count = sum(1 for item in candidates if item.get("file_state") == "verified")
                reused_count = sum(1 for item in candidates if item.get("file_state") == "reused")
                failed_count = sum(1 for item in candidates if item.get("file_state") == "failed")
                result_summary = {
                    "candidates_total": len(candidates),
                    "verified_full_sha": verified_count,
                    "reused_cached_sha": reused_count,
                    "failed_count": failed_count,
                    "unique_size_skipped": int(cur.get("unique_size_skipped") or 0),
                    "confirmed_groups": confirmed_groups,
                    "suspected_groups": suspected_groups,
                }
                if failed_count > 0:
                    cur["status"] = "failed"
                    cur["phase"] = "failed"
                    cur["error_code"] = "DEDUP_FILE_ERRORS"
                    cur["result"] = result_summary
                else:
                    cur["status"] = "succeeded"
                    cur["phase"] = "completed"
                    cur["error_code"] = None
                    cur["result"] = result_summary
                cur["processed_candidates"] = verified_count + reused_count
                cur["version"] += 1
                cur["updated_at"] = storage.now_iso()

            self.store.mutate(finalize_job)
        except _PausedOrCancelled:
            return
        except Exception as exc:
            if storage.data_root().resolve() != self.root:
                return
            err_code = exc.code if isinstance(exc, CleanroomException) else "DEDUP_EXECUTION_FAILED"
            target_status = "interrupted" if self.closed else "failed"

            def fail_job(raw: dict[str, Any]) -> None:
                cur = raw["jobs"].get(job_id)
                if (
                    not cur
                    or int(cur.get("attempt", 0)) != attempt
                    or int(cur.get("generation", 0)) != generation
                    or cur.get("status") in TERMINAL
                    or cur.get("status") == "paused"
                ):
                    return
                cur["status"] = target_status
                cur["phase"] = target_status
                cur["error_code"] = err_code
                cur["version"] += 1
                cur["updated_at"] = storage.now_iso()

            with suppress(Exception):
                self.store.mutate(fail_job)
        finally:
            if owner_token is not None:
                current_asset_owner.reset(owner_token)
            if acquired_lock:
                worker_lock.release()
            with self.condition:
                if acquired_slot and self._active_worker_slot == job_id:
                    self._active_worker_slot = None
                if self._running_jobs.get(job_id) is current_thread:
                    self._running_jobs.pop(job_id, None)
                self._active_worker_threads.discard(current_thread)
                self.condition.notify_all()


_default: Optional[DedupConfirmJobs] = None
_default_lock = threading.Lock()


def service() -> DedupConfirmJobs:
    global _default
    with _default_lock:
        root = storage.data_root().resolve()
        if _default is None or _default.root != root or _default.closed:
            if _default is not None:
                _default.shutdown()
            _default = DedupConfirmJobs()
        return _default


def shutdown() -> None:
    if _default is not None:
        _default.shutdown()
