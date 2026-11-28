# Copyright 2026 qinxuedong (Gods-Workbench Authors)
# SPDX-License-Identifier: FSL-1.1-Apache-2.0
#
# Licensed under the Functional Source License, Version 1.1, Apache 2.0
# Future License (the "License"); you may not use this file except in
# compliance with the License. You may obtain a copy of the License at
#     https://fsl.software/
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Phase 12 A4 观测真实数据源：硬件遥测、采样存储、来源登记、素材体积。

设计取舍：
- 硬件遥测直接读 ``psutil``；依赖缺失时返回 ``None``，由调用方标 not_integrated，
  绝不编造 CPU/内存/磁盘数字；
- 指标采样落盘到命名空间 ``observability_samples``（core.storage.JsonState），
  只有**真实落盘**的采样点才对外返回；不足 2 点一律 degraded，禁止插值补点；
- 素材体积真实遍历 ``GW_ALLOWED_ROOTS``；未配置根目录返回空 + not_integrated，
  对外只暴露相对路径（``relative_display``），不回显绝对路径。

证据边界：采样为单进程/单实例落盘；多 worker 不共享，属部署方职责。
"""

from __future__ import annotations

import logging
import queue
import threading
import time
from typing import Any

from gw.core import storage

NS_SAMPLES = "observability_samples"
#: 采样指标白名单；只有这些指标允许写入采样存储。
KNOWN_METRICS = (
    "cpu_percent",
    "memory_percent",
    "disk_percent",
    "gpu_utilization_percent",
    "gpu_memory_percent",
    "asset_response_bytes",
    "asset_search_duration_ms",
)


def _psutil():
    """延迟导入 psutil；未安装返回 None（调用方据此失败关闭）。"""
    try:
        import psutil  # type: ignore
    except Exception:
        return None
    return psutil


#: nvidia-smi 查询语句；只取真实设备读数，不解析任何估算值。
_NVIDIA_QUERY = "index,name,memory.total,memory.used,utilization.gpu"
_NVIDIA_TIMEOUT_SECONDS = 5.0


def gpu_telemetry() -> dict[str, Any] | None:
    """通过 nvidia-smi 读取真实 GPU 读数；命令缺失/失败返回 None。

    设计：仅采集 nvidia-smi 自身报告的真实数值；不做任何推算或插值。
    无 NVIDIA 设备或驱动时返回 None，由调用方如实标 not_integrated。
    """
    import shutil
    import subprocess

    exe = shutil.which("nvidia-smi")
    if not exe:
        return None
    try:
        proc = subprocess.run(
            [exe, "--query-gpu=" + _NVIDIA_QUERY, "--format=csv,noheader,nounits"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=_NVIDIA_TIMEOUT_SECONDS,
        )
    except Exception:
        return None
    if proc.returncode != 0:
        return None
    devices: list[dict[str, Any]] = []
    total_util_sum = 0.0
    for line in (proc.stdout or "").splitlines():
        parts = [cell.strip() for cell in line.split(",")]
        if len(parts) < 5:
            continue
        try:
            index = int(parts[0])
            name = parts[1]
            total_mib = float(parts[2])
            used_mib = float(parts[3])
            util = float(parts[4])
        except Exception:
            continue
        # 分母为 0 属驱动异常读数，跳过而不猜测。
        util_percent = util
        mem_percent = (used_mib / total_mib * 100.0) if total_mib > 0 else None
        devices.append(
            {
                "index": index,
                "name": name,
                "memory_total_mib": total_mib,
                "memory_used_mib": used_mib,
                "utilization_percent": util_percent,
                "memory_utilization_percent": mem_percent,
            }
        )
        total_util_sum += util_percent
    if not devices:
        return None
    mem_percents = [
        d["memory_utilization_percent"]
        for d in devices
        if d["memory_utilization_percent"] is not None
    ]
    return {
        "gpu_count": len(devices),
        "gpu_utilization_percent": round(total_util_sum / len(devices), 2),
        "gpu_memory_percent": round(sum(mem_percents) / len(mem_percents), 2)
        if mem_percents
        else None,
        "devices": devices,
    }


def hardware_telemetry() -> dict[str, Any] | None:
    """读取真实 CPU/内存/磁盘读数；psutil 不可用返回 None。"""
    psutil = _psutil()
    if psutil is None:
        return None
    try:
        memory = psutil.virtual_memory()
        disk = psutil.disk_usage("/")
        return {
            "cpu_percent": float(psutil.cpu_percent(interval=None)),
            "cpu_count": int(psutil.cpu_count() or 0),
            "memory_percent": float(memory.percent),
            "memory_total_bytes": int(memory.total),
            "memory_used_bytes": int(memory.used),
            "disk_percent": float(disk.percent),
            "disk_total_bytes": int(disk.total),
            "disk_used_bytes": int(disk.used),
        }
    except Exception:
        return None


def _samples_state() -> storage.JsonState:
    return storage.JsonState(NS_SAMPLES, lambda: {"revision": 1, "samples": {}})


#: HTTP 观测缓冲：把真实请求的字节数与耗时按指标累计，达到节流间隔后落盘。
_HTTP_LOCK = threading.Lock()
_HTTP_PENDING: dict[str, float] = {}
_HTTP_LAST_FLUSH = 0.0
#: 落盘节流间隔（秒）；缓冲内的真实读数在下次读取前强制刷盘。
HTTP_FLUSH_INTERVAL_SECONDS = 1.0
HTTP_OBSERVATION_QUEUE_CAPACITY = 256
HTTP_OBSERVATION_WRITER_THREAD = "gw-http-observation-writer"


class _HttpObservationWriter:
    """串行持久化 HTTP 观测批次的单写者线程。"""

    def __init__(self) -> None:
        self._queue: queue.Queue[tuple[str, dict[str, float] | None, threading.Event | None]] = queue.Queue(
            maxsize=HTTP_OBSERVATION_QUEUE_CAPACITY
        )
        self._state_lock = threading.Lock()
        self._stopping = False
        self._stopped = threading.Event()
        self._thread = threading.Thread(
            target=self._run,
            name=HTTP_OBSERVATION_WRITER_THREAD,
            daemon=False,
        )
        self._thread.start()

    @property
    def is_alive(self) -> bool:
        return self._thread.is_alive()

    def submit(self, payload: dict[str, float]) -> bool:
        with self._state_lock:
            if self._stopping:
                return False
            try:
                self._queue.put_nowait(("payload", dict(payload), None))
            except queue.Full:
                return False
        return True

    def flush(self, payload: dict[str, float] | None = None) -> bool:
        completed = threading.Event()
        command = ("flush", dict(payload) if payload else None, completed)
        while True:
            with self._state_lock:
                if self._stopping:
                    stopping = True
                else:
                    stopping = False
                    try:
                        self._queue.put_nowait(command)
                    except queue.Full:
                        pass
                    else:
                        break
            if stopping:
                self._stopped.wait()
                return False
            time.sleep(0.001)
        completed.wait()
        return True

    def stop(self, payload: dict[str, float] | None = None) -> None:
        with self._state_lock:
            enqueue_stop = not self._stopping
            self._stopping = True
        if enqueue_stop:
            self._queue.put(
                ("stop", dict(payload) if payload else None, self._stopped)
            )
        self._stopped.wait()
        self._thread.join()

    def _run(self) -> None:
        try:
            while True:
                command, payload, completed = self._queue.get()
                try:
                    if payload:
                        _persist_http_payload(payload)
                except Exception:
                    logging.getLogger(__name__).exception(
                        "HTTP 观测采样后台落盘异常；请求处理不受影响"
                    )
                finally:
                    if completed is not None:
                        completed.set()
                if command == "stop":
                    return
        finally:
            self._stopped.set()


_HTTP_WRITER_LOCK = threading.Lock()
_HTTP_WRITER: _HttpObservationWriter | None = None
_HTTP_WRITER_STOPPING: _HttpObservationWriter | None = None
_HTTP_WRITER_RESTART_EVENT = threading.Event()
_HTTP_WRITER_RESTART_EVENT.set()
_HTTP_WRITER_LEASES = 0


class _HttpObservationWriterLease:
    """单个 app lifespan 对进程级单写者的幂等所有权句柄。"""

    def __init__(self, writer: _HttpObservationWriter) -> None:
        self.writer = writer
        self._released = threading.Event()
        self._release_lock = threading.Lock()

    @property
    def is_alive(self) -> bool:
        return self.writer.is_alive

    def release_once(self) -> bool:
        with self._release_lock:
            if self._released.is_set():
                return False
            self._released.set()
            return True


def start_http_observation_writer() -> _HttpObservationWriterLease:
    """启动或共享 app lifespan 持有的单写者；模块导入本身不会创建线程。"""
    global _HTTP_WRITER, _HTTP_WRITER_LEASES
    while True:
        with _HTTP_WRITER_LOCK:
            if _HTTP_WRITER_STOPPING is not None:
                restart_event = _HTTP_WRITER_RESTART_EVENT
            else:
                if _HTTP_WRITER is not None and _HTTP_WRITER.is_alive:
                    _HTTP_WRITER_LEASES += 1
                    return _HttpObservationWriterLease(_HTTP_WRITER)
                _HTTP_WRITER = _HttpObservationWriter()
                _HTTP_WRITER_LEASES = 1
                return _HttpObservationWriterLease(_HTTP_WRITER)
        restart_event.wait()


def stop_http_observation_writer(
    lease: _HttpObservationWriterLease | None,
) -> None:
    """排空当前缓冲后停止 worker；重复调用安全。"""
    global _HTTP_WRITER, _HTTP_WRITER_LEASES, _HTTP_WRITER_STOPPING, _HTTP_LAST_FLUSH
    if lease is None or not lease.release_once():
        return
    writer = lease.writer
    with _HTTP_WRITER_LOCK:
        if _HTTP_WRITER is not writer:
            return
        _HTTP_WRITER_LEASES = max(0, _HTTP_WRITER_LEASES - 1)
        if _HTTP_WRITER_LEASES:
            return
        _HTTP_WRITER = None
        _HTTP_WRITER_STOPPING = writer
        _HTTP_WRITER_RESTART_EVENT.clear()
    with _HTTP_LOCK:
        payload = dict(_HTTP_PENDING)
        _HTTP_PENDING.clear()
        _HTTP_LAST_FLUSH = time.time()
    try:
        writer.stop(payload)
    finally:
        with _HTTP_WRITER_LOCK:
            if _HTTP_WRITER_STOPPING is writer:
                _HTTP_WRITER_STOPPING = None
                _HTTP_WRITER_RESTART_EVENT.set()


def _active_http_writer() -> _HttpObservationWriter | None:
    with _HTTP_WRITER_LOCK:
        writer = _HTTP_WRITER
        return writer if writer is not None and writer.is_alive else None


def _restore_http_pending(payload: dict[str, float]) -> None:
    with _HTTP_LOCK:
        for key, value in payload.items():
            _HTTP_PENDING[key] = _HTTP_PENDING.get(key, 0.0) + float(value)


def record_http_observation(
    path: str, *, response_bytes: int, duration_ms: float
) -> None:
    """记录一次真实 HTTP 请求的响应字节数与耗时（仅 /api/ 请求）。

    只累计调用方提供的真实测量值；不编造、不插值。写入按固定间隔节流，
    缓冲中的真实读数由 :func:`flush_http_samples` 在读取前强制落盘。
    """
    global _HTTP_LAST_FLUSH
    try:
        size = float(response_bytes)
        elapsed = float(duration_ms)
    except Exception:
        return
    if size < 0 or elapsed < 0:
        return
    now = time.time()
    with _HTTP_LOCK:
        _HTTP_PENDING["asset_response_bytes"] = (
            _HTTP_PENDING.get("asset_response_bytes", 0.0) + size
        )
        _HTTP_PENDING["_count"] = _HTTP_PENDING.get("_count", 0.0) + 1.0
        _HTTP_PENDING["_duration_total_ms"] = (
            _HTTP_PENDING.get("_duration_total_ms", 0.0) + elapsed
        )
        if now - _HTTP_LAST_FLUSH >= HTTP_FLUSH_INTERVAL_SECONDS:
            _HTTP_LAST_FLUSH = now
            payload = dict(_HTTP_PENDING)
            _HTTP_PENDING.clear()
        else:
            payload = None
    if payload:
        writer = _active_http_writer()
        if writer is None or not writer.submit(payload):
            _restore_http_pending(payload)


def flush_http_samples() -> None:
    """把缓冲中的真实 HTTP 读数强制落盘（读取指标前调用，保证读到自己刚产生的真实点）。"""
    global _HTTP_LAST_FLUSH
    with _HTTP_LOCK:
        payload = dict(_HTTP_PENDING)
        _HTTP_PENDING.clear()
        _HTTP_LAST_FLUSH = time.time()
    writer = _active_http_writer()
    if writer is not None and writer.flush(payload):
        return
    if payload:
        _persist_http_payload(payload)


def _persist_http_payload(payload: dict[str, float]) -> None:
    """把一批真实 HTTP 读数写入采样存储（一次请求对应一个真实采样点）。"""
    count = float(payload.get("_count") or 0.0)
    if count <= 0:
        return
    total_bytes = float(payload.get("asset_response_bytes") or 0.0)
    total_duration = float(payload.get("_duration_total_ms") or 0.0)
    ts_ms = int(time.time() * 1000)
    values = {
        "asset_response_bytes": total_bytes,
        "asset_search_duration_ms": total_duration / count,
    }

    def mutate(raw):
        bucket = raw.setdefault("samples", {})
        key = str(ts_ms)
        suffix = 1
        while key in bucket:
            suffix += 1
            key = f"{ts_ms}-{suffix}"
        bucket[key] = {"ts_ms": ts_ms, "values": dict(values), "sample_key": key}
        keys = sorted(bucket.keys())
        for stale in keys[:-500]:
            bucket.pop(stale, None)
        raw["revision"] = int(raw.get("revision") or 1) + 1
        return values

    try:
        _samples_state().mutate(mutate)
    except Exception:
        logging.getLogger(__name__).exception("HTTP 观测采样落盘失败")


def record_sample() -> dict[str, Any]:
    """采集一次真实硬件读数并落盘；返回写入的采样点（或空 dict）。"""
    reading = hardware_telemetry()
    if reading is None:
        return {}
    ts_ms = int(time.time() * 1000)

    def mutate(raw: dict[str, Any]) -> dict[str, Any]:
        bucket = raw.setdefault("samples", {})
        values = {
            "cpu_percent": reading["cpu_percent"],
            "memory_percent": reading["memory_percent"],
            "disk_percent": reading["disk_percent"],
        }
        # GPU 为可选真实来源：只在 nvidia-smi 真实可用时写入，缺失不得补零。
        gpu = gpu_telemetry()
        if gpu is not None:
            values["gpu_utilization_percent"] = gpu["gpu_utilization_percent"]
            if gpu.get("gpu_memory_percent") is not None:
                values["gpu_memory_percent"] = gpu["gpu_memory_percent"]
        point = {"ts_ms": ts_ms, "values": values}
        # 同一毫秒内的多次采样也不能互相覆盖：追加序号后缀保证唯一键。
        key = str(ts_ms)
        suffix = 1
        while key in bucket:
            suffix += 1
            key = f"{ts_ms}-{suffix}"
        point["sample_key"] = key
        bucket[key] = point
        # 只保留最近 500 个采样点，避免无界增长。
        keys = sorted(bucket.keys())
        for stale in keys[:-500]:
            bucket.pop(stale, None)
        raw["revision"] = int(raw.get("revision") or 1) + 1
        return point

    return _samples_state().mutate(mutate)


def load_series(
    metric: str, start_ms: int | None, end_ms: int | None
) -> list[dict[str, Any]]:
    """读取真实采样点；只返回白名单指标且落在窗口内的点。"""
    if metric not in KNOWN_METRICS:
        return []
    if metric == "asset_response_bytes" or metric == "asset_search_duration_ms":
        # 先把缓冲中的真实 HTTP 读数落盘，避免「刚产生的真实请求读不到」。
        flush_http_samples()
    raw = _samples_state().read()
    points: list[dict[str, Any]] = []
    for _key, item in sorted((raw.get("samples") or {}).items()):
        try:
            ts_ms = int(item.get("ts_ms"))
        except Exception:
            continue
        if start_ms is not None and ts_ms < int(start_ms):
            continue
        if end_ms is not None and ts_ms > int(end_ms):
            continue
        values = item.get("values") or {}
        if metric not in values:
            continue
        points.append({"ts_ms": ts_ms, "value": values[metric]})
    points.sort(key=lambda p: p["ts_ms"])
    return points


def source_registry() -> list[dict[str, Any]]:
    """返回本进程真实接入的数据源清单（与实现路径一一对应）。"""
    return [
        {
            "source_id": "core.audit",
            "name": "认证审计缓冲",
            "kind": "audit",
            "path": "gw.core.audit.list_auth_events",
            "data_status": "ok",
        },
        {
            "source_id": "projects_hub.service",
            "name": "项目内存服务",
            "kind": "service",
            "path": "gw.projects_hub.service.ProjectsService.list_projects",
            "data_status": "ok",
        },
        {
            "source_id": "god_canvas.service",
            "name": "画布任务内存服务",
            "kind": "service",
            "path": "gw.god_canvas.service.GodCanvasService.list_jobs",
            "data_status": "ok",
        },
        {
            "source_id": "observability.samples",
            "name": "指标采样落盘",
            "kind": "storage",
            "path": "gw.observability.telemetry.load_series",
            "data_status": "ok",
        },
        {
            "source_id": "observability.http_samples",
            "name": "HTTP 响应采样",
            "kind": "middleware",
            "path": "gw.observability.telemetry.record_http_observation",
            "data_status": "ok",
        },
        {
            "source_id": "core.storage.asset_volumes",
            "name": "素材体积扫描",
            "kind": "filesystem",
            "path": "gw.observability.telemetry.scan_asset_volumes",
            "data_status": "ok",
        },
    ]


def scan_asset_volumes(limit: int = 200) -> dict[str, Any]:
    """真实遍历允许根目录统计素材体积；未配置根目录返回 not_integrated。"""
    roots = storage.allowed_roots()
    if not roots:
        return {
            "items": [],
            "data_status": "not_integrated",
            "data_gaps": ["未配置 GW_ALLOWED_ROOTS，无法遍历素材体积"],
        }
    items: list[dict[str, Any]] = []
    gaps: list[str] = []
    for root in roots:
        if not root.exists():
            gaps.append("允许根目录不存在或不可访问")
            continue
        try:
            for path in root.rglob("*"):
                if len(items) >= limit:
                    break
                if not path.is_file():
                    continue
                try:
                    size = path.stat().st_size
                except OSError:
                    gaps.append("存在无法读取的素材文件，体积已跳过")
                    continue
                items.append(
                    {
                        "path": storage.relative_display(path),
                        "size_bytes": int(size),
                        "root": storage.relative_display(root),
                    }
                )
        except OSError:
            gaps.append("允许根目录遍历失败，结果可能不完整")
    items.sort(key=lambda item: item["path"])
    return {
        "items": items[:limit],
        "data_status": "ok" if not gaps else "degraded",
        "data_gaps": gaps,
    }
