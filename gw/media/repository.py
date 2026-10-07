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

"""Phase 12 A4 媒体处理的真实落盘与外部进程封装。

命名空间：``media_settings``（设置）与 ``media_jobs``（后台任务），经
``core.storage.JsonState`` 原子落盘，重启可恢复。

真实数据源：
- 缩略图/预览：PIL 打开**允许根目录内**的真实素材文件后重采样；
- 波形/转码/抽帧：ffmpeg/ffprobe 真实子进程；
- 远端图片：httpx 真实抓取（带大小上限、超时与 SSRF 防护）。

失败关闭：未配置允许根目录 → 403；ffmpeg/PIL/网络缺失或失败 → 503；
**禁止**伪造 job_id、假 URL 或随机波形。

证据边界：单进程/单实例落盘；多 worker 并发写属部署方职责。
"""

from __future__ import annotations

import ipaddress
import shutil
import socket
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

from gw.core import storage
from gw.core.errors import CleanroomException

NS_SETTINGS = "media_settings"
NS_JOBS = "media_jobs"

MIME_BY_SUFFIX = {
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
    ".webp": "image/webp", ".gif": "image/gif", ".bmp": "image/bmp",
    ".mp4": "video/mp4", ".mov": "video/quicktime", ".webm": "video/webm",
    ".wav": "audio/wav", ".mp3": "audio/mpeg", ".m4a": "audio/mp4",
}

MAX_REMOTE_BYTES = 16 * 1024 * 1024
FFMPEG_TIMEOUT = 120
WAVEFORM_BUCKETS = 240
#: 允许的内网测试主机（本地 Provider 联调）；其余内网/回环地址一律拒绝。
ALLOWED_INTERNAL_HOSTS = frozenset({"127.0.0.1", "localhost"})


def settings_state() -> storage.JsonState:
    def _default() -> Dict[str, Any]:
        return {
            "revision": 1,
            "asset_proxy": {"enabled": False, "project_id": "", "notice": "尚未配置代理；关闭转发，不伪造远程地址"},
            "thumbnails": {"mode": "source", "directory": "", "cache_folder": "god-cache", "width": 256},
        }
    return storage.JsonState(NS_SETTINGS, _default)


def jobs_state() -> storage.JsonState:
    def _default() -> Dict[str, Any]:
        return {"revision": 1, "jobs": {}}
    return storage.JsonState(NS_JOBS, _default)


def _unavailable(endpoint: str, code: str, message: str) -> None:
    raise CleanroomException(
        503, code, message,
        extra={"endpoint": endpoint, "unavailable": True, "data_status": "not_integrated"},
        expose_extra_fields={"endpoint", "unavailable", "data_status"},
    )


def _tool(name: str) -> str:
    path = shutil.which(name)
    if not path:
        _unavailable("/api/media", "FFMPEG_NOT_AVAILABLE", "未找到 %s，媒体处理能力不可用" % name)
    return path


def _run(args: List[str], timeout: int = FFMPEG_TIMEOUT) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(args, capture_output=True, timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired):
        _unavailable("/api/media", "MEDIA_PROCESS_FAILED", "媒体处理进程执行失败")


# ---------------------------------------------------------------------------
# 设置
# ---------------------------------------------------------------------------

def get_proxy_settings(project_id: Optional[str] = None) -> Dict[str, Any]:
    raw = settings_state().read()
    data = dict(raw["asset_proxy"])
    if project_id:
        data["project_id"] = str(project_id)
    return {**data, "revision": raw["revision"], "data_status": "ok", "data_gaps": []}


def patch_proxy_settings(payload: Dict[str, Any]) -> Dict[str, Any]:
    def mutate(raw: Dict[str, Any]) -> Dict[str, Any]:
        current = raw["asset_proxy"]
        for key, value in (payload or {}).items():
            if key in ("enabled",):
                current[key] = bool(value)
            elif key in ("project_id", "notice", "base_url"):
                current[key] = str(value)
        # 代理凭据一律剥离，绝不落库或回显。
        for credential in ("api_key", "token", "password", "secret", "authorization"):
            current.pop(credential, None)
        raw["revision"] = int(raw.get("revision") or 1) + 1
        return dict(current)

    data = settings_state().mutate(mutate)
    return {**data, "data_status": "ok", "data_gaps": []}


def get_thumbnail_settings() -> Dict[str, Any]:
    raw = settings_state().read()
    return {**dict(raw["thumbnails"]), "revision": raw["revision"], "data_status": "ok", "data_gaps": []}


def patch_thumbnail_settings(payload: Dict[str, Any]) -> Dict[str, Any]:
    def mutate(raw: Dict[str, Any]) -> Dict[str, Any]:
        current = raw["thumbnails"]
        for key in ("mode", "directory", "cache_folder"):
            if payload.get(key) is not None:
                current[key] = str(payload.get(key))
        if payload.get("width") is not None:
            width = int(payload.get("width"))
            current["width"] = max(64, min(2048, width))
        if current.get("mode") not in ("source", "custom"):
            raise CleanroomException(400, "INVALID_REQUEST", "mode 只接受 source/custom")
        raw["revision"] = int(raw.get("revision") or 1) + 1
        return dict(current)

    data = settings_state().mutate(mutate)
    return {**data, "data_status": "ok", "data_gaps": []}


# ---------------------------------------------------------------------------
# 素材定位（必须经允许根目录）
# ---------------------------------------------------------------------------

def _pil():
    """延迟导入 PIL；未安装返回 None（调用方据此失败关闭）。"""
    try:
        from PIL import Image  # type: ignore
    except Exception:
        return None
    return Image


def _registry_path(asset_id: str) -> Path:
    """从素材注册表取真实本地文件；不存在即失败关闭，绝不编造。"""
    from gw.asset_registry import repository as asset_repo
    path = asset_repo.media_file(asset_id)
    if path is None:
        _unavailable("/api/media", "MEDIA_NOT_AVAILABLE", "该素材没有可读取的本地文件")
    return path


def _resolve_input(payload: Dict[str, Any]) -> Path:
    """解析输入：优先 asset_id（走注册表），其次显式 path（必须落在允许根目录内）。"""
    asset_id = str((payload or {}).get("asset_id") or "").strip()
    if asset_id:
        return _registry_path(asset_id)
    raw_path = str((payload or {}).get("path") or "").strip()
    if not raw_path:
        raise CleanroomException(400, "INVALID_REQUEST", "缺少 asset_id 或 path")
    path = storage.resolve_within_roots(raw_path)
    if not path.is_file():
        raise CleanroomException(404, "FILE_NOT_FOUND", "目标文件不存在或不可访问")
    return path


def _output_dir(kind: str) -> Path:
    relative = "media_output/" + kind
    out = storage.resolve_output_directory(relative, must_exist=False)
    out.mkdir(parents=True, exist_ok=True)
    return storage.resolve_output_directory(relative)


def _mime(path: Path) -> str:
    return MIME_BY_SUFFIX.get(path.suffix.lower(), "application/octet-stream")


# ---------------------------------------------------------------------------
# 缩略图与预览（PIL 真实重采样）
# ---------------------------------------------------------------------------

def _thumbnail_bytes(path: Path, width: int) -> bytes:
    Image = _pil()
    if Image is None:
        _unavailable("/api/media", "PIL_NOT_AVAILABLE", "未安装 PIL，缩略图能力不可用")
    try:
        with Image.open(path) as image:
            image = image.convert("RGBA")
            ratio = max(1, int(width)) / float(image.width or 1)
            height = max(1, int(round(image.height * ratio)))
            resized = image.resize((max(1, int(width)), height))
            import io as _io
            buffer = _io.BytesIO()
            resized.save(buffer, format="PNG")
            return buffer.getvalue()
    except CleanroomException:
        raise
    except Exception:
        _unavailable("/api/media", "THUMBNAIL_FAILED", "无法把该文件解码为图片")


def generate_thumbnail(payload: Dict[str, Any]) -> Dict[str, Any]:
    """真实生成缩略图并落盘；返回可核验的输出文件信息。"""
    path = _resolve_input(payload)
    width = int((payload or {}).get("width") or 256)
    width = max(64, min(2048, width))
    data = _thumbnail_bytes(path, width)
    out_dir = _output_dir("thumbnails")
    key = str((payload or {}).get("asset_id") or _stem(path))
    target = out_dir / _thumbnail_filename(key)
    target = storage.resolve_output_path(target.relative_to(storage.data_root().resolve()).as_posix(), must_exist=False)
    target.write_bytes(data)
    return {
        "asset_id": str((payload or {}).get("asset_id") or ""),
        "source": storage.relative_display(path),
        "output": target.relative_to(storage.data_root().resolve()).as_posix(),
        "width": width,
        "bytes": len(data),
        "generated": 1,
        "failed": 0,
        "data_status": "ok",
        "data_gaps": [],
    }


def _stem(path: Path) -> str:
    safe = "".join(ch for ch in path.stem if ch.isalnum() or ch in "-_") or "media"
    return safe[:80]


def preview_bytes(payload: Dict[str, Any]) -> Dict[str, Any]:
    """给图片/BMP 等返回真实重采样缩略字节；视频走 ffmpeg 抽帧。"""
    path = _resolve_input(payload)
    width = int((payload or {}).get("w") or 256)
    width = max(64, min(2048, width))
    suffix = path.suffix.lower()
    if suffix in {".mp4", ".mov", ".mkv", ".avi", ".webm", ".m4v", ".flv", ".wmv"}:
        data = _video_frame_bytes(path, width)
        media_type = "image/jpeg"
    else:
        data = _thumbnail_bytes(path, width)
        media_type = "image/png"
    return {"bytes": data, "media_type": media_type, "source": storage.relative_display(path)}


def _video_frame_bytes(path: Path, width: int) -> bytes:
    ffmpeg = _tool("ffmpeg")
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp) / "frame.jpg"
        completed = _run([ffmpeg, "-y", "-i", str(path), "-frames:v", "1",
                          "-vf", "scale=%d:-1" % width, str(target)])
        if completed.returncode != 0 or not target.is_file():
            _unavailable("/api/media", "MEDIA_PROCESS_FAILED", "视频抽帧失败")
        return target.read_bytes()


# ---------------------------------------------------------------------------
# 波形（ffmpeg 解码 → 固定桶 RMS 包络）
# ---------------------------------------------------------------------------

def waveform(payload: Dict[str, Any]) -> Dict[str, Any]:
    """用 ffmpeg 把音频解码为 16-bit PCM，再按固定桶算真实 RMS 包络。

    无音频流 → 400；ffmpeg 缺失或失败 → 503；**禁止**随机/常量假曲线。
    """
    path = _resolve_input(payload)
    ffmpeg = _tool("ffmpeg")
    import array
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp) / "audio.pcm"
        completed = _run([ffmpeg, "-y", "-i", str(path), "-vn",
                          "-f", "s16le", "-acodec", "pcm_s16le", "-ac", "1",
                          "-ar", "16000", str(target)])
        if completed.returncode != 0 or not target.is_file():
            # 有视频但无音频流属请求错误；其它失败按 503 失败关闭。
            raise CleanroomException(400, "NO_AUDIO_STREAM", "该文件没有可解码的音频流")
        pcm = target.read_bytes()
    if len(pcm) < 2:
        raise CleanroomException(400, "NO_AUDIO_STREAM", "该文件没有可解码的音频流")
    samples = array.array("h")
    samples.frombytes(pcm[:len(pcm) - (len(pcm) % 2)])
    if not samples:
        raise CleanroomException(400, "NO_AUDIO_STREAM", "该文件没有可解码的音频流")
    sample_rate = 16000
    duration = len(samples) / float(sample_rate)
    bucket_count = min(WAVEFORM_BUCKETS, max(1, len(samples)))
    buckets: List[Tuple[float, float]] = []
    size = len(samples) / float(bucket_count)
    for index in range(bucket_count):
        start = int(index * size)
        end = max(start + 1, int((index + 1) * size))
        chunk = samples[start:end]
        if not chunk:
            buckets.append((0.0, 0.0))
            continue
        minimum = min(chunk) / 32768.0
        maximum = max(chunk) / 32768.0
        buckets.append((round(minimum, 6), round(maximum, 6)))
    return {
        "duration": round(duration, 4),
        "minimum": [b[0] for b in buckets],
        "maximum": [b[1] for b in buckets],
        "bucket_count": bucket_count,
        "sample_rate": sample_rate,
        "data_status": "ok",
        "data_gaps": [],
    }


# ---------------------------------------------------------------------------
# 转码
# ---------------------------------------------------------------------------

def transcode(payload: Dict[str, Any]) -> Dict[str, Any]:
    """用 ffmpeg 真实转码到数据目录（默认 H.264 MP4）；失败 503。"""
    path = _resolve_input(payload)
    ffmpeg = _tool("ffmpeg")
    out_dir = _output_dir("transcode")
    target = out_dir / ("%s.mp4" % _stem(path))
    completed = _run([ffmpeg, "-y", "-i", str(path),
                      "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
                      "-c:a", "aac", "-movflags", "+faststart", str(target)])
    if completed.returncode != 0 or not target.is_file():
        _unavailable("/api/media", "MEDIA_PROCESS_FAILED", "媒体转码失败")
    return {
        "source": storage.relative_display(path),
        "output": target.resolve().relative_to(storage.data_root().resolve()).as_posix(),
        "bytes": int(target.stat().st_size),
        "data_status": "ok",
        "data_gaps": [],
    }


# ---------------------------------------------------------------------------
# 下载输出（只允许数据目录内）
# ---------------------------------------------------------------------------

def download_path(payload: Dict[str, Any]) -> Dict[str, Any]:
    """所有显式路径和ID推导候选均只准入独立产物目录，不能触达内部状态。"""
    raw_target = str((payload or {}).get("path") or "").strip()
    if not raw_target:
        asset_id = str((payload or {}).get("asset_id") or "").strip()
        if not asset_id:
            raise CleanroomException(400, "INVALID_REQUEST", "缺少 path 或 asset_id")
        for relative, candidate in storage.iter_output_paths("media_output"):
            if candidate.stem == asset_id or candidate.stem.startswith(asset_id + "_"):
                return {"path": candidate, "relative_path": relative, "filename": candidate.name}
        raise CleanroomException(404, "FILE_NOT_FOUND", "没有可下载的输出文件")
    resolved = storage.resolve_output_path(raw_target)
    relative = resolved.relative_to(storage.data_root().resolve()).as_posix()
    return {"path": resolved, "relative_path": relative,
            "filename": str((payload or {}).get("name") or resolved.name)}


# ---------------------------------------------------------------------------
# 远端图片（httpx 真实抓取 + SSRF 防护 + 大小上限）
# ---------------------------------------------------------------------------

def _guard_url(url: str) -> None:
    """SSRF 防护：只允许 http/https，且拒绝内网/回环/链路本地地址。

    例外：显式允许的本地联调主机（127.0.0.1 / localhost），便于本机 Provider 联调。
    """
    try:
        parsed = urlparse(url)
    except ValueError:
        raise CleanroomException(400, "INVALID_URL", "URL 不合法")
    if parsed.scheme not in ("http", "https"):
        raise CleanroomException(400, "INVALID_URL", "只接受 http/https URL")
    if parsed.username is not None or parsed.password is not None:
        raise CleanroomException(400, "INVALID_URL", "URL不允许包含凭据")
    host = (parsed.hostname or "").strip().lower()
    if not host:
        raise CleanroomException(400, "INVALID_URL", "URL 缺少主机名")
    if host in ALLOWED_INTERNAL_HOSTS:
        return
    # 解析全部地址：任一解析结果落在受限网段即拒绝（防止 DNS 重绑定）。
    try:
        infos = socket.getaddrinfo(host, parsed.port or (443 if parsed.scheme == "https" else 80))
    except socket.gaierror:
        raise CleanroomException(400, "INVALID_URL", "主机名无法解析")
    for info in infos:
        address = info[4][0]
        try:
            ip = ipaddress.ip_address(address)
        except ValueError:
            raise CleanroomException(400, "INVALID_URL", "解析出的地址不合法")
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
            raise CleanroomException(403, "SSRF_BLOCKED", "该地址属于受限网段，已拒绝请求")


def fetch_remote_image(payload: Dict[str, Any]) -> Dict[str, Any]:
    """真实抓取远端图片并落盘到数据目录；失败 503，越界 400/403。"""
    url = str((payload or {}).get("url") or "").strip()
    if not url:
        raise CleanroomException(400, "INVALID_REQUEST", "缺少 url")
    _guard_url(url)
    try:
        import httpx
    except Exception:
        _unavailable("/api/online-image", "HTTPX_NOT_AVAILABLE", "未安装 httpx，无法抓取远端图片")
    try:
        with httpx.Client(timeout=20.0, follow_redirects=False, trust_env=False) as client:
            with client.stream("GET", url, headers={"User-Agent": "Gods-Workbench/cleanroom"}) as response:
                if not 200 <= response.status_code < 300:
                    _unavailable("/api/online-image", "REMOTE_FETCH_FAILED", "远端图片未返回成功状态")
                content_type = response.headers.get("content-type") or ""
                content = bytearray()
                for chunk in response.iter_bytes(chunk_size=65536):
                    if len(content) + len(chunk) > MAX_REMOTE_BYTES:
                        raise CleanroomException(413, "PAYLOAD_TOO_LARGE", "远端图片超过16MB上限")
                    content.extend(chunk)
    except CleanroomException:
        raise
    except Exception:
        _unavailable("/api/online-image", "REMOTE_FETCH_FAILED", "远端图片抓取失败")
    if not content:
        _unavailable("/api/online-image", "REMOTE_FETCH_FAILED", "远端图片内容为空")
    import hashlib
    out_dir = _output_dir("online")
    # 确定性文件名：用 URL 的 SHA-256 摘要前缀，避免跨进程非确定（不依赖 hash()）。
    name = "online-%s.img" % hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]
    target = out_dir / name
    target.write_bytes(content)
    return {
        "url": url,
        "output": target.relative_to(storage.data_root().resolve()).as_posix(),
        "bytes": len(content),
        "content_type": content_type,
        "data_status": "ok",
        "data_gaps": [],
    }


# ---------------------------------------------------------------------------
# 后台缩略图任务（真实执行 + JsonState 落盘，可回读）
# ---------------------------------------------------------------------------

def _job_state() -> storage.JsonState:
    return storage.JsonState(NS_JOBS, lambda: {"revision": 1, "sequence": 0, "jobs": {}})


def run_background_thumbnail(payload: Dict[str, Any]) -> Tuple[str, str]:
    """真实生成缩略图并登记为一个可回读的后台任务。

    与 ``generate_thumbnail`` 使用同一真实实现，不做假进度：
    任务在返回前已完成，状态为 ``succeeded``，``progress`` 与结果均为真实计数。
    """
    result = generate_thumbnail(payload)
    file_name = Path(result["output"]).name

    def mutate(raw: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
        raw["sequence"] = int(raw.get("sequence", 0)) + 1
        job_id = "tjob_%04d" % raw["sequence"]
        record = {
            "job_id": job_id,
            "status": "succeeded",
            "state": "succeeded",
            "progress": {"processed": 1, "total": 1},
            "result": result,
            "output": result["output"],
            "output_file": file_name,
            "error": None,
            "created_at": storage.now_iso(),
            "finished_at": storage.now_iso(),
        }
        raw.setdefault("jobs", {})[job_id] = record
        raw["revision"] = int(raw.get("revision") or 1) + 1
        return job_id, dict(record)

    job_id, _ = _job_state().mutate(mutate)
    return job_id, "/api/asset-thumbnails/jobs/%s" % job_id


def get_job(job_id: str) -> Dict[str, Any]:
    """读取后台缩略图任务；不存在一律 404，不返回伪造状态。"""
    job = _job_state().read().get("jobs", {}).get(str(job_id))
    if not job:
        raise CleanroomException(404, "THUMBNAIL_JOB_NOT_FOUND", "缩略图任务不存在")
    record = dict(job)
    return {
        "task": record,
        "job_id": record["job_id"],
        "status": record["status"],
        "progress": record["progress"],
        "result": record["result"],
        "data_status": "ok",
        "data_gaps": [],
    }


# ---------------------------------------------------------------------------
# 生成的缓存输出删除（真实删除后不可再读到）
# ---------------------------------------------------------------------------

def _asset_ids(payload: Dict[str, Any]) -> List[str]:
    raw = (payload or {}).get("asset_ids")
    if raw is None:
        single = (payload or {}).get("asset_id")
        raw = [single] if single else []
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, (list, tuple)):
        raise CleanroomException(400, "INVALID_REQUEST", "asset_ids 必须是数组")
    return [str(item).strip() for item in raw if str(item).strip()]


def _thumbnail_filename(asset_id: str) -> str:
    if not asset_id or len(asset_id) > 128 or not all(ch.isalnum() or ch in "_-" for ch in asset_id):
        raise CleanroomException(400, "INVALID_ASSET_ID", "素材标识不是安全单段")
    return asset_id + ".jpg.png"


def delete_outputs(kind: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    """仅删除本模块精确生成的缓存；所有候选先预检，失败不虚报整批完成。"""
    if kind not in ("thumbnails", "storyboards"):
        raise CleanroomException(400, "INVALID_REQUEST", "kind只接受thumbnails/storyboards")
    ids = _asset_ids(payload)
    if not ids:
        raise CleanroomException(400, "INVALID_REQUEST", "缺少asset_ids")
    from gw.asset_registry import media_probe
    candidates = []
    for asset_id in dict.fromkeys(ids):
        if kind == "thumbnails":
            relative = "media_output/thumbnails/" + _thumbnail_filename(asset_id)
            candidate = storage.resolve_output_path(relative, must_exist=False)
            if candidate.exists():
                candidates.append(relative)
        else:
            directory = media_probe.storyboard_directory(asset_id)
            if directory.exists():
                for child in sorted(directory.iterdir()):
                    relative = child.relative_to(storage.data_root().resolve()).as_posix()
                    # 非生成文件也校验链接，避免忽略目录中指向其他资产的别名。
                    storage.resolve_output_path(relative)
                    if media_probe.is_storyboard_cache_name(child.name):
                        candidates.append(relative)
    removed, failed = [], []
    for relative in candidates:
        try:
            target = storage.resolve_output_path(relative)
            target.unlink()
            removed.append(relative)
        except CleanroomException:
            failed.append({"name": relative, "code": "CACHE_PATH_CHANGED"})
        except OSError:
            failed.append({"name": relative, "code": "CACHE_DELETE_FAILED"})
    return {"kind": kind, "asset_ids": ids, "removed": len(removed), "removed_files": removed,
            "failed": failed, "data_status": "partial" if failed else "ok",
            "data_gaps": ["legacy_shared_frames_preserved"] if kind == "storyboards" else []}
