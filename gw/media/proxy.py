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


def _ffmpeg_proxy(path: Path, width: int, target: Path) -> dict[str, Any]:
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
        f"scale={width}:-2",
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
    return {"path": target, "media_type": "video/mp4", "width": width, "height": None}


def build_proxy(payload: dict[str, Any]) -> dict[str, Any]:
    """生成或读取确定性的真实代理，并返回不含绝对路径的元数据。"""
    path, asset_id, version_id = _source(payload)
    profile, width = _profile(payload)
    identity = _identity(path, asset_id, version_id, profile, width)
    directory = _output_dir()
    is_video = path.suffix.lower() in VIDEO_SUFFIXES
    target_base = directory / ("proxy-" + identity + (".mp4" if is_video else ".png"))
    if is_video:
        result = _ffmpeg_proxy(path, width, target_base)
    else:
        result = _image_proxy(path, width, target_base)
    target = result["path"]
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
