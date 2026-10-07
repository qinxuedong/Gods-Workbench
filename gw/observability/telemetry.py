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

import threading
import time
from typing import Any, Dict, List, Optional

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


def gpu_telemetry() -> Optional[Dict[str, Any]]:
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
            timeout=_NVIDIA_TIMEOUT_SECONDS,
        )
    except Exception:
        return None
    if proc.returncode != 0:
        return None
    devices: List[Dict[str, Any]] = []
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
        devices.append({
            "index": index,
            "name": name,
            "memory_total_mib": total_mib,
            "memory_used_mib": used_mib,
            "utilization_percent": util_percent,
            "memory_utilization_percent": mem_percent,
        })
        total_util_sum += util_percent
    if not devices:
        return None
    mem_percents = [d["memory_utilization_percent"] for d in devices
                    if d["memory_utilization_percent"] is not None]
    return {
        "gpu_count": len(devices),
        "gpu_utilization_percent": round(total_util_sum / len(devices), 2),
        "gpu_memory_percent": round(sum(mem_percents) / len(mem_percents), 2) if mem_percents else None,
        "devices": devices,
    }


def hardware_telemetry() -> Optional[Dict[str, Any]]:
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
_HTTP_PENDING: Dict[str, float] = {}
_HTTP_LAST_FLUSH = 0.0
#: 落盘节流间隔（秒）；缓冲内的真实读数在下次读取前强制刷盘。
HTTP_FLUSH_INTERVAL_SECONDS = 1.0


def record_http_observation(path: str, *, response_bytes: int, duration_ms: float) -> None:
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
        _persist_http_payload(payload)


def flush_http_samples() -> None:
    """把缓冲中的真实 HTTP 读数强制落盘（读取指标前调用，保证读到自己刚产生的真实点）。"""
    global _HTTP_LAST_FLUSH
    with _HTTP_LOCK:
        payload = dict(_HTTP_PENDING)
        _HTTP_PENDING.clear()
        _HTTP_LAST_FLUSH = time.time()
    if payload:
        _persist_http_payload(payload)


def _persist_http_payload(payload: Dict[str, float]) -> None:
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
            key = "%s-%d" % (ts_ms, suffix)
        bucket[key] = {"ts_ms": ts_ms, "values": dict(values), "sample_key": key}
        keys = sorted(bucket.keys())
        for stale in keys[:-500]:
            bucket.pop(stale, None)
        raw["revision"] = int(raw.get("revision") or 1) + 1
        return values

    try:
        _samples_state().mutate(mutate)
    except Exception:
        return


def record_sample() -> Dict[str, Any]:
    """采集一次真实硬件读数并落盘；返回写入的采样点（或空 dict）。"""
    reading = hardware_telemetry()
    if reading is None:
        return {}
    ts_ms = int(time.time() * 1000)

    def mutate(raw: Dict[str, Any]) -> Dict[str, Any]:
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
            key = "%s-%d" % (ts_ms, suffix)
        point["sample_key"] = key
        bucket[key] = point
        # 只保留最近 500 个采样点，避免无界增长。
        keys = sorted(bucket.keys())
        for stale in keys[:-500]:
            bucket.pop(stale, None)
        raw["revision"] = int(raw.get("revision") or 1) + 1
        return point

    return _samples_state().mutate(mutate)


def load_series(metric: str, start_ms: Optional[int], end_ms: Optional[int]) -> List[Dict[str, Any]]:
    """读取真实采样点；只返回白名单指标且落在窗口内的点。"""
    if metric not in KNOWN_METRICS:
        return []
    if metric == "asset_response_bytes" or metric == "asset_search_duration_ms":
        # 先把缓冲中的真实 HTTP 读数落盘，避免「刚产生的真实请求读不到」。
        flush_http_samples()
    raw = _samples_state().read()
    points: List[Dict[str, Any]] = []
    for key, item in sorted((raw.get("samples") or {}).items()):
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


def source_registry() -> List[Dict[str, Any]]:
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


def scan_asset_volumes(limit: int = 200) -> Dict[str, Any]:
    """真实遍历允许根目录统计素材体积；未配置根目录返回 not_integrated。"""
    roots = storage.allowed_roots()
    if not roots:
        return {"items": [], "data_status": "not_integrated", "data_gaps": ["未配置 GW_ALLOWED_ROOTS，无法遍历素材体积"]}
    items: List[Dict[str, Any]] = []
    gaps: List[str] = []
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
                items.append({
                    "path": storage.relative_display(path),
                    "size_bytes": int(size),
                    "root": storage.relative_display(root),
                })
        except OSError:
            gaps.append("允许根目录遍历失败，结果可能不完整")
    items.sort(key=lambda item: item["path"])
    return {
        "items": items[:limit],
        "data_status": "ok" if not gaps else "degraded",
        "data_gaps": gaps,
    }
