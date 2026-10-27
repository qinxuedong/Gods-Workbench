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

"""C11-T4 媒体轻量代理派生与真实降级状态。

代理是原始资产/版本的派生读取表示。它只在允许根目录内读取源文件，
把派生文件写入 ``media_output/proxy``，不会覆盖原片，也不会伪造 URL。
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

from gw.asset_registry import repository as asset_registry_repo
from gw.core import storage
from gw.core.errors import CleanroomException
from gw.media import repository as media_repo

VIDEO_SUFFIXES = frozenset(
    {".mp4", ".mov", ".mkv", ".avi", ".webm", ".m4v", ".flv", ".wmv"}
)
PROFILE_LIMITS = {"thumbnail": 512, "small": 640, "preview": 1280}
PROFILE_ALIASES = {"low": "small", "proxy": "preview"}
ARTIFACT_RE = re.compile(r"^proxy-[A-Za-z0-9_-]{16,64}\.(?:png|jpg|mp4)$")
IMAGE_FORMATS = {".png": ("image/png", "png"), ".jpg": ("image/jpeg", "jpeg")}
DEPENDENCY_CODES = frozenset({"PIL_NOT_AVAILABLE", "FFMPEG_NOT_AVAILABLE"})
HEAVY_VIDEO_CODECS = frozenset(
    {"cineform", "dnxhd", "dnxhr", "rawvideo", "r210", "v210"}
)
HIGH_BITRATE_MOV_THRESHOLD = 100_000_000
#: 按需缩略图/代理缓存的默认预算（20 GiB，MVP1-AC9）；只约束派生产物，
#: 源素材目录永远不参与清理。
PROXY_CACHE_BUDGET_BYTES = 20 * 1024 * 1024 * 1024
PROXY_CACHE_BUDGET_ENV = "GW_PROXY_CACHE_BUDGET_BYTES"
PROXY_ARTIFACT_PREFIX = "proxy-"


def cache_budget_bytes() -> int:
    """读取代理缓存预算字节数；环境变量非法时回落到默认 20 GiB。"""
    raw = os.environ.get(PROXY_CACHE_BUDGET_ENV, "").strip()
    if not raw:
        return PROXY_CACHE_BUDGET_BYTES
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return PROXY_CACHE_BUDGET_BYTES
    return value if value > 0 else PROXY_CACHE_BUDGET_BYTES


def _cache_entries(directory: Path) -> list[tuple[float, int, Path]]:
    """列出缓存目录内可淘汰的派生产物（按 mtime、大小、路径）。"""
    entries: list[tuple[float, int, Path]] = []
    try:
        candidates = list(directory.iterdir())
    except OSError:
        return entries
    for item in candidates:
        name = item.name
        if not name.startswith(PROXY_ARTIFACT_PREFIX) or name.endswith(".part"):
            continue
        try:
            if not item.is_file():
                continue
            stat = item.stat()
        except OSError:
            continue
        entries.append((stat.st_mtime, stat.st_size, item))
    return entries


def cache_usage(directory: Path) -> dict[str, int]:
    """统计代理缓存真实占用；读不到的条目跳过，不估算、不伪造大小。"""
    entries = _cache_entries(directory)
    return {
        "bytes": sum(size for _, size, _ in entries),
        "artifacts": len(entries),
        "budget_bytes": cache_budget_bytes(),
    }


def touch_cache_artifact(path: Path) -> None:
    """把本次命中时间写回 mtime，使「最久未用优先淘汰」可判定。

    Windows 上 atime 常被系统关闭，因此用 mtime 近似最近使用时间。
    """
    try:
        os.utime(path, None)
    except OSError:
        pass


def enforce_cache_budget(
    directory: Path, *, keep: Path | None = None
) -> dict[str, int]:
    """按最近使用时间淘汰最旧派生产物，直到不超过预算。

    只处理 ``media_output/proxy`` 下的 ``proxy-*`` 文件，永远不触碰源素材目录，
    也不删除本轮刚产出的 ``keep``。返回淘汰后的真实占用与淘汰数量。
    """
    budget = cache_budget_bytes()
    entries = _cache_entries(directory)
    total = sum(size for _, size, _ in entries)
    removed = 0
    for _, size, path in sorted(entries, key=lambda item: (item[0], item[2].name)):
        if total <= budget:
            break
        if keep is not None and path == keep:
            continue
        try:
            path.unlink()
        except OSError:
            continue
        total -= size
        removed += 1
    return {"bytes": total, "removed": removed, "budget_bytes": budget}


def requires_720p_proxy(video_metadata: dict[str, Any]) -> bool:
    """只为 4K、专业中间编码、RAW 或高码率 MOV 标记 720p 代理需求。"""
    if not isinstance(video_metadata, dict):
        return False
    try:
        width = int(video_metadata.get("width") or 0)
        height = int(video_metadata.get("height") or 0)
        bit_rate = int(video_metadata.get("bit_rate") or 0)
    except (TypeError, ValueError, OverflowError):
        return False
    codec = str(video_metadata.get("codec") or "").strip().lower()
    container = str(video_metadata.get("container") or "").strip().lower()
    return (
        width >= 3840
        or height >= 2160
        or codec.startswith("prores")
        or codec in HEAVY_VIDEO_CODECS
        or (container == "mov" and bit_rate >= HIGH_BITRATE_MOV_THRESHOLD)
    )


def _pil():
    try:
        from PIL import Image  # type: ignore
    except Exception:
        return None
    return Image


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _profile(payload: dict[str, Any]) -> tuple[str, int]:
    raw = str((payload or {}).get("profile") or "preview").strip().lower()
    profile = PROFILE_ALIASES.get(raw, raw)
    if profile not in PROFILE_LIMITS:
        raise CleanroomException(
            400, "UNSUPPORTED_PROFILE", "代理 profile 只接受 thumbnail/small/preview"
        )
    raw_width = (payload or {}).get("width", (payload or {}).get("w"))
    if raw_width in (None, ""):
        width = PROFILE_LIMITS[profile]
    else:
        try:
            width = int(raw_width)
        except (TypeError, ValueError):
            raise CleanroomException(
                400, "INVALID_WIDTH", "代理 width 必须是整数"
            ) from None
        if width < 64:
            raise CleanroomException(400, "INVALID_WIDTH", "代理 width 不能小于 64")
    return profile, min(width, PROFILE_LIMITS[profile])


def _source(payload: dict[str, Any]) -> tuple[Path, str | None, str | None]:
    payload = payload or {}
    asset_id = str(payload.get("asset_id") or "").strip() or None
    version_id = str(payload.get("version_id") or "").strip() or None
    raw_path = str(payload.get("path") or "").strip()
    if version_id and not asset_id:
        raise CleanroomException(400, "INVALID_REQUEST", "version_id 必须绑定 asset_id")
    if asset_id:
        path = asset_registry_repo.media_file(asset_id, version_id)
        if path is None:
            raise CleanroomException(
                503, "MEDIA_NOT_AVAILABLE", "该资产版本没有可读取的媒体文件"
            )
        return path, asset_id, version_id
    if not raw_path:
        raise CleanroomException(400, "INVALID_REQUEST", "缺少 asset_id 或 path")
    path = storage.resolve_request_path(
        raw_path, root_id=str(payload.get("root_id") or "").strip() or None
    )
    if not path.is_file():
        raise CleanroomException(404, "FILE_NOT_FOUND", "目标文件不存在或不可访问")
    return path, None, None


def _output_dir() -> Path:
    directory = storage.resolve_output_directory("media_output/proxy", must_exist=False)
    directory.mkdir(parents=True, exist_ok=True)
    return storage.resolve_output_directory("media_output/proxy")


def _identity(
    path: Path, asset_id: str | None, version_id: str | None, profile: str, width: int
) -> str:
    stat = path.stat()
    logical = "|".join(
        (
            asset_id or "{}:{}".format(*storage.location_identity(path)),
            version_id or "",
            profile,
            str(width),
            str(stat.st_size),
            str(stat.st_mtime_ns),
        )
    )
    return hashlib.sha256(logical.encode("utf-8")).hexdigest()[:32]


def _image_proxy(path: Path, width: int, target_base: Path) -> dict[str, Any]:
    Image = _pil()
    if Image is None:
        raise CleanroomException(
            503,
            "PIL_NOT_AVAILABLE",
            "未安装 PIL，图片代理能力未接入",
            extra={"data_status": "not_integrated"},
            expose_extra_fields={"data_status"},
        )
    try:
        with Image.open(path) as opened:
            source_width, source_height = opened.size
            target_width = min(max(1, int(width)), max(1, int(source_width)))
            target_height = max(
                1, int(round(source_height * target_width / float(source_width or 1)))
            )
            converted = opened.convert("RGBA")
            resized = converted.resize(
                (target_width, target_height), Image.Resampling.LANCZOS
            )
            alpha = resized.getchannel("A")
            has_alpha = alpha.getextrema()[0] < 255
            suffix = ".png" if has_alpha else ".jpg"
            media_type, image_format = IMAGE_FORMATS[suffix]
            target = target_base.with_suffix(suffix)
            if not target.is_file() or target.stat().st_size <= 0:
                partial = target.with_suffix(target.suffix + ".part")
                try:
                    if image_format == "jpeg":
                        resized.convert("RGB").save(
                            partial, format="JPEG", quality=82, optimize=True
                        )
                    else:
                        resized.save(partial, format="PNG", optimize=True)
                    partial.replace(target)
                finally:
                    partial.unlink(missing_ok=True)
            touch_cache_artifact(target)
            return {
                "path": target,
                "media_type": media_type,
                "width": target_width,
                "height": target_height,
            }
    except CleanroomException:
        raise
    except PermissionError:
        raise CleanroomException(
            403, "MEDIA_PERMISSION_DENIED", "没有权限读取源媒体"
        ) from None
    except OSError as exc:
        try:
            from PIL import UnidentifiedImageError
        except ImportError:
            UnidentifiedImageError = ()
        if isinstance(exc, UnidentifiedImageError):
            raise CleanroomException(
                503, "SOURCE_MEDIA_CORRUPT", "源图片损坏或无法解码"
            ) from None
        raise CleanroomException(
            503, "SOURCE_MEDIA_UNREADABLE", "源图片暂时无法读取"
        ) from None
    except Exception:
        raise CleanroomException(
            503, "THUMBNAIL_FAILED", "无法把该文件解码为图片"
        ) from None


def _probe_video_metadata(path: Path) -> dict[str, Any]:
    """读取代理判定需要的轻量视频元数据；探测不可用时回退原代理规格。"""
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        return {}
    try:
        completed = subprocess.run(
            [
                ffprobe,
                "-v",
                "error",
                "-select_streams",
                "v:0",
                "-show_entries",
                "stream=codec_name,width,height:format=bit_rate",
                "-of",
                "json",
                str(path),
            ],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        if completed.returncode != 0:
            return {}
        payload = json.loads(completed.stdout)
        stream = payload["streams"][0]
        width = int(stream["width"])
        height = int(stream["height"])
        codec = str(stream["codec_name"]).strip()
        try:
            bit_rate = int(payload.get("format", {}).get("bit_rate") or 0)
        except (TypeError, ValueError, OverflowError):
            bit_rate = 0
    except (OSError, subprocess.SubprocessError, IndexError, KeyError, TypeError, ValueError):
        return {}
    if width <= 0 or height <= 0 or not codec:
        return {}
    return {
        "width": width,
        "height": height,
        "codec": codec,
        "bit_rate": bit_rate,
        "container": path.suffix.lower().lstrip("."),
    }


def _720p_proxy_dimensions(video_metadata: dict[str, Any]) -> tuple[int, int]:
    """保持源宽高比并把重格式代理限制到 720 行。"""
    width = int(video_metadata["width"])
    height = int(video_metadata["height"])
    scaled_width = max(2, int(round(width * 720 / height / 2)) * 2)
    return scaled_width, 720


def _ffmpeg_proxy(
    path: Path, width: int, target: Path, *, target_height: int | None = None
) -> dict[str, Any]:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise CleanroomException(
            503,
            "FFMPEG_NOT_AVAILABLE",
            "未找到 ffmpeg，视频代理能力未接入",
            extra={"data_status": "not_integrated"},
            expose_extra_fields={"data_status"},
        )
    partial = target.with_suffix(target.suffix + ".part")
    args = [
        ffmpeg,
        "-y",
        "-i",
        str(path),
        "-vf",
        f"scale=-2:{target_height}" if target_height else f"scale={width}:-2",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-b:v",
        "1200k",
        "-maxrate",
        "1400k",
        "-bufsize",
        "2400k",
        "-c:a",
        "aac",
        "-b:a",
        "96k",
        "-movflags",
        "+faststart",
        "-f",
        "mp4",
        str(partial),
    ]
    try:
        completed = subprocess.run(args, capture_output=True, timeout=120, check=False)
    except subprocess.TimeoutExpired:
        partial.unlink(missing_ok=True)
        raise CleanroomException(
            503, "PROXY_GENERATION_TIMEOUT", "代理生成超时"
        ) from None
    except OSError:
        partial.unlink(missing_ok=True)
        raise CleanroomException(
            503, "MEDIA_PROCESS_FAILED", "视频代理处理失败"
        ) from None
    if (
        completed.returncode != 0
        or not partial.is_file()
        or partial.stat().st_size <= 0
    ):
        partial.unlink(missing_ok=True)
        raise CleanroomException(503, "MEDIA_PROCESS_FAILED", "视频代理处理失败")
    partial.replace(target)
    return {
        "path": target,
        "media_type": "video/mp4",
        "width": width,
        "height": target_height,
    }


def build_proxy(payload: dict[str, Any]) -> dict[str, Any]:
    """生成或读取确定性的真实代理，并返回不含绝对路径的元数据。"""
    path, asset_id, version_id = _source(payload)
    profile, width = _profile(payload)
    is_video = path.suffix.lower() in VIDEO_SUFFIXES
    target_height = None
    if is_video:
        video_metadata = _probe_video_metadata(path)
        if requires_720p_proxy(video_metadata):
            width, target_height = _720p_proxy_dimensions(video_metadata)
    identity = _identity(path, asset_id, version_id, profile, width)
    directory = _output_dir()
    target_base = directory / ("proxy-" + identity + (".mp4" if is_video else ".png"))
    if is_video:
        result = _ffmpeg_proxy(path, width, target_base, target_height=target_height)
    else:
        result = _image_proxy(path, width, target_base)
    target = result["path"]
    # 按需产物落盘后立即做一次 LRU 收口：命中即刷新使用时间，超预算则淘汰最旧派生文件。
    touch_cache_artifact(target)
    enforce_cache_budget(directory, keep=target)
    artifact_id = target.name
    return {
        "availability": "ready",
        "asset_id": asset_id,
        "version_id": version_id,
        "artifact_id": artifact_id,
        "media_type": result["media_type"],
        "width": result["width"],
        "height": result["height"],
        "duration_ms": None,
        "profile": profile,
        "source": "proxy",
        "generated_at": datetime.fromtimestamp(target.stat().st_mtime, UTC)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z"),
        "bytes": target.stat().st_size,
        "output": "media_output/proxy/" + artifact_id,
        "_request_width": width,
    }


def _next_action(code: str) -> str:
    if code in DEPENDENCY_CODES:
        return "install_media_dependency"
    if code == "MEDIA_NOT_AVAILABLE":
        return "restore_source_media"
    if code == "MEDIA_PERMISSION_DENIED":
        return "check_source_permissions"
    if code == "SOURCE_MEDIA_CORRUPT":
        return "replace_source_media"
    return "retry_proxy_generation"


def _degraded_payload(
    payload: dict[str, Any], exc: CleanroomException
) -> dict[str, Any]:
    code = exc.code
    availability = "not_integrated" if code in DEPENDENCY_CODES else "unavailable"
    return {
        "availability": availability,
        "asset_id": str((payload or {}).get("asset_id") or "") or None,
        "version_id": str((payload or {}).get("version_id") or "") or None,
        "artifact_id": None,
        "proxy_url": None,
        "media_type": None,
        "width": None,
        "height": None,
        "duration_ms": None,
        "profile": str((payload or {}).get("profile") or "preview"),
        "reason_code": code,
        "next_action": _next_action(code),
        "data_status": "not_integrated"
        if availability == "not_integrated"
        else "degraded",
        "data_gaps": [code],
        "retryable": True,
    }


def descriptor(payload: dict[str, Any]) -> dict[str, Any]:
    """返回可供检索结果消费的 ready 或规范降级状态。"""
    try:
        result = build_proxy(payload)
    except CleanroomException as exc:
        if exc.status_code != 503:
            raise
        return _degraded_payload(payload, exc)
    except TimeoutError:
        return _degraded_payload(
            payload,
            CleanroomException(503, "PROXY_GENERATION_TIMEOUT", "代理生成超时"),
        )
    except Exception:
        return _degraded_payload(
            payload,
            CleanroomException(503, "PROXY_GENERATION_FAILED", "代理生成失败"),
        )
    query: dict[str, str] = {"artifact_id": result["artifact_id"]}
    query["profile"] = result["profile"]
    query["width"] = str(result.get("_request_width") or result["width"] or "")
    if result.get("asset_id"):
        query["asset_id"] = result["asset_id"]
    if result.get("version_id"):
        query["version_id"] = result["version_id"]
    elif not result.get("asset_id"):
        try:
            source_path = _source(payload)[0]
            query["path"] = storage.relative_display(source_path)
            query["root_id"] = storage.location_identity(source_path)[0]
        except Exception:
            pass
    result["proxy_url"] = "/api/media-proxy/content?" + urlencode(query)
    result["data_status"] = "complete"
    result["data_gaps"] = []
    result.pop("output", None)
    result.pop("_request_width", None)
    return result


def artifact_path(payload: dict[str, Any]) -> tuple[Path, dict[str, Any]]:
    """解析代理内容；有源标识时重新验证绑定关系。"""
    artifact_id = str((payload or {}).get("artifact_id") or "").strip()
    if artifact_id and not ARTIFACT_RE.fullmatch(artifact_id):
        raise CleanroomException(400, "INVALID_ARTIFACT_ID", "代理产物标识不合法")
    has_source = bool(
        str((payload or {}).get("asset_id") or "").strip()
        or str((payload or {}).get("path") or "").strip()
    )
    if has_source:
        result = build_proxy(payload)
        if artifact_id and artifact_id != result["artifact_id"]:
            raise CleanroomException(
                404, "PROXY_NOT_FOUND", "代理产物与当前媒体版本不匹配"
            )
        relative = "media_output/proxy/" + result["artifact_id"]
        return storage.resolve_output_path(relative), result
    if not artifact_id:
        raise CleanroomException(
            400, "INVALID_REQUEST", "缺少 artifact_id 或媒体源标识"
        )
    path = storage.resolve_output_path("media_output/proxy/" + artifact_id)
    return path, {"artifact_id": artifact_id, "media_type": media_repo._mime(path)}


def error_payload(exc: CleanroomException) -> dict[str, Any]:
    """构造媒体代理专用错误包，保留统一 detail 外层并补齐降级动作。"""
    return {
        "detail": {
            "code": exc.code,
            "message": exc.message,
            "reason_code": exc.code,
            "next_action": _next_action(exc.code),
            "data_status": "not_integrated"
            if exc.code in DEPENDENCY_CODES
            else "degraded",
            "retryable": exc.status_code in {408, 429, 500, 502, 503, 504},
        }
    }
