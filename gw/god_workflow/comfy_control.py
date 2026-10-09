"""本机 CF 生命周期；仅精确身份与 OS 句柄可授权停止，不按名称/端口盲杀。"""
from __future__ import annotations

import os
from pathlib import Path
import secrets
import subprocess
import threading
import time
from urllib.parse import urlsplit

import httpx
import psutil


class ProcessBackend:
    def identity(self, process):
        return {"pid": process.pid, "created": process.create_time(),
                "exe": process.exe(), "cmd": process.cmdline()}

    def listener(self, port):
        # 枚举失败/多实例/非 loopback 监听均失败关闭。
        pids = {c.pid for c in psutil.net_connections(kind="tcp")
                if c.status == psutil.CONN_LISTEN and c.laddr.port == port and c.pid
                and c.laddr.ip in {"127.0.0.1", "::1"}}
        if len(pids) != 1:
            return None
        process = psutil.Process(pids.pop())
        identity = self.identity(process)
        cmd = identity["cmd"]
        # 必须确认实际 Python ComfyUI main.py 与安装目录标志，不靠端口猜测。
        mains = [Path(v) for v in cmd[1:] if Path(v).name.lower() == "main.py"]
        if len(mains) != 1:
            return None
        main = mains[0]
        if not main.is_absolute():
            main = Path(process.cwd()) / main
        if not main.is_file() or not (main.parent / "comfy").is_dir():
            return None
        if "python" not in Path(identity["exe"]).name.lower():
            return None
        return identity

    def spawn(self, root, port):
        base = Path(root)
        if not base.is_absolute() or not base.is_dir():
            raise ValueError("需管理员配置绝对 ComfyUI 目录")
        main = next((p for p in (base / "main.py", base / "ComfyUI" / "main.py")
                     if p.is_file() and (p.parent / "comfy").is_dir()), None)
        python = next((p for p in (base / "python_embeded" / "python.exe",
                                   base / ".venv" / "Scripts" / "python.exe",
                                   base / "venv" / "Scripts" / "python.exe",
                                   base / ".venv" / "bin" / "python",
                                   base / "venv" / "bin" / "python") if p.is_file()), None)
        if main is None or python is None:
            raise ValueError("仅支持已配置 Python 环境及 ComfyUI main.py，拒绝任意启动脚本")
        process = subprocess.Popen([str(python), str(main), "--listen", "127.0.0.1", "--port", str(port)],
                                   cwd=str(main.parent), stdin=subprocess.DEVNULL,
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                   shell=False, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                                   start_new_session=os.name != "nt")
        return process, self.identity(psutil.Process(process.pid))

    def stop(self, expected):
        pid = expected["pid"]
        if os.name == "nt":
            import ctypes
            from ctypes import wintypes
            kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
            kernel.OpenProcess.restype = wintypes.HANDLE
            kernel.CloseHandle.argtypes = [wintypes.HANDLE]
            kernel.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
            kernel.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
            kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
            handle = kernel.OpenProcess(0x0001 | 0x1000 | 0x00100000, False, pid)
            if not handle:
                return False
            try:
                times = [wintypes.FILETIME() for _ in range(4)]
                if not kernel.GetProcessTimes(handle, *(ctypes.byref(v) for v in times)):
                    return False
                created = ((times[0].dwHighDateTime << 32) + times[0].dwLowDateTime) / 10000000 - 11644473600
                if abs(created - expected["created"]) > 0.00001:
                    return False
                if self.identity(psutil.Process(pid)) != expected:
                    return False
                # 句柄在重验之前绑定进程对象，PID复用不会改变终止对象。
                return bool(kernel.TerminateProcess(handle, 0)) and kernel.WaitForSingleObject(handle, 3000) == 0
            finally:
                kernel.CloseHandle(handle)
        if not hasattr(os, "pidfd_open"):
            return False
        import signal
        fd = os.pidfd_open(pid)
        try:
            if self.identity(psutil.Process(pid)) != expected:
                return False
            signal.pidfd_send_signal(fd, signal.SIGTERM)
            import select
            return bool(select.select([fd], [], [], 3)[0])
        finally:
            os.close(fd)


def probe(url):
    try:
        with httpx.Client(timeout=1, trust_env=False, follow_redirects=False) as client:
            response = client.get(url.rstrip("/") + "/system_stats")
            data = response.json()
            return response.status_code == 200 and isinstance(data, dict) and isinstance(data.get("system"), dict)
    except Exception:
        return False


def local_endpoint(url):
    parsed = urlsplit(url)
    if (parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}
            or parsed.username or parsed.password or parsed.path not in {"", "/"} or parsed.query or parsed.fragment):
        raise ValueError("CF 控制仅允许本机 loopback HTTP 根地址")
    return parsed.port or 80


class ComfyControl:
    def __init__(self, backend=None, probe_fn=probe, wait_seconds=15, sleep=time.sleep):
        self.backend = backend or ProcessBackend()
        self.probe = probe_fn
        self.wait_seconds = wait_seconds
        self.sleep = sleep
        self.lock = threading.Lock()
        self.owned = None
        self.child = None
        self.owner = None
        self.confirmations = {}

    def _inspect(self, url, principal):
        port = local_endpoint(url)
        online = self.probe(url)
        identity = None
        try:
            identity = self.backend.listener(port) if online else None
        except (psutil.Error, OSError, ValueError):
            pass
        managed = identity is not None and identity == self.owned and principal == self.owner
        result = {"ok": True, "status": "online" if online else "offline", "highlight": online,
                  "managed": managed, "can_stop": identity is not None}
        if online and identity and not managed:
            now = time.monotonic()
            self.confirmations = {k: v for k, v in self.confirmations.items() if v[0] > now}
            # 有界保存；令牌不暴露本地路径/命令行，且绑定当前主体与地址。
            if len(self.confirmations) >= 128:
                self.confirmations.pop(next(iter(self.confirmations)))
            token = secrets.token_urlsafe(32)
            self.confirmations[token] = (now + 60, principal, url, identity)
            result["confirmation_token"] = token
        return result, identity

    def operate(self, action, url, root, principal, token=None):
        if not self.lock.acquire(blocking=False):
            return {"ok": False, "status": "unknown", "highlight": False, "code": "COMFY_CONTROL_BUSY"}
        try:
            result, identity = self._inspect(url, principal)
            if action == "status":
                return result
            if action == "start":
                if result["highlight"]:
                    return result
                # 上次启动仍活着但未就绪，不重复产生后台进程。
                if self.child is not None and self.child.poll() is None:
                    return dict(result, ok=False, code="COMFY_START_PENDING")
                self.child, self.owned = self.backend.spawn(root, local_endpoint(url))
                self.owner = principal
                deadline = time.monotonic() + self.wait_seconds
                while True:
                    result, identity = self._inspect(url, principal)
                    if result["highlight"] and identity == self.owned:
                        return dict(result, launched=True)
                    if self.child.poll() is not None or time.monotonic() >= deadline:
                        # 超时只终止刚保存的精确子进程；身份变化/权限拒绝则保留待恢复。
                        cleaned = self.child.poll() is not None or self.backend.stop(self.owned)
                        if cleaned:
                            self.child = None
                            self.owned = None
                            self.owner = None
                        result, _ = self._inspect(url, principal)
                        return dict(result, ok=False, code="COMFY_START_TIMEOUT", launched=True, cleanup_confirmed=cleaned)
                    self.sleep(0.25)
            if action != "stop":
                return dict(result, ok=False, code="INVALID_ACTION")
            if not identity:
                return dict(result, ok=False, code="COMFY_PROCESS_IDENTITY_UNKNOWN")
            if identity != self.owned or principal != self.owner:
                confirmation = self.confirmations.pop(str(token or ""), None)
                if not confirmation or confirmation[1:3] != (principal, url) or confirmation[0] < time.monotonic():
                    return dict(result, ok=False, code="COMFY_EXTERNAL_CONFIRM_REQUIRED")
                if confirmation[3] != identity:
                    return dict(result, ok=False, code="COMFY_PROCESS_IDENTITY_CHANGED")
            # 监听身份再次检查，OS句柄内再重验，拒绝PID复用及确认竞态。
            if self.backend.listener(local_endpoint(url)) != identity or not self.backend.stop(identity):
                return dict(result, ok=False, code="COMFY_PROCESS_IDENTITY_CHANGED")
            self.owned = None
            self.owner = None
            self.child = None
            deadline = time.monotonic() + 3
            while self.probe(url) and time.monotonic() < deadline:
                self.sleep(0.25)
            result, _ = self._inspect(url, principal)
            return dict(result, ok=not result["highlight"], code=None if not result["highlight"] else "COMFY_STOP_NOT_OFFLINE")
        except (ValueError, OSError, psutil.Error):
            return {"ok": False, "status": "unknown", "highlight": False, "code": "COMFY_CONTROL_UNVERIFIABLE"}
        finally:
            self.lock.release()


service = ComfyControl()
