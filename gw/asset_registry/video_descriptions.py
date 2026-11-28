# Copyright 2026 qinxuedong (Gods-Workbench Authors)
# SPDX-License-Identifier: FSL-1.1-Apache-2.0

"""VLM 镜头画面摘要与标签：受控调用服务端凭据网关。"""

from __future__ import annotations

import base64
import hashlib
import io
import json
import math
from pathlib import Path
from typing import Any, Callable

from gw.core import storage
from gw.core.errors import CleanroomException

SETTINGS_NAMESPACE = "asset_video_descriptions"
DEFAULT_PROMPT = (
    "准确描述画面中的主体、动作、场景和可读文字。只根据画面，不推测画外信息。"
)
MAX_PROMPT_LENGTH = 4000
MAX_SUMMARY_LENGTH = 1000
MAX_TAGS = 20
MAX_TAG_LENGTH = 80
_IMAGE_WIDTH = 320
_IMAGE_HEIGHT = 180


class VideoDescriptionError(Exception):
    """带有稳定安全错误码的单镜头失败。"""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _default_settings() -> dict[str, Any]:
    return {"auto_enabled": False, "provider_id": "", "model": "", "prompt": DEFAULT_PROMPT}


def get_settings() -> dict[str, Any]:
    raw = storage.load(SETTINGS_NAMESPACE, _default_settings())
    if not isinstance(raw, dict):
        raw = _default_settings()
    return {
        "auto_enabled": raw.get("auto_enabled") is True,
        "provider_id": str(raw.get("provider_id") or ""),
        "model": str(raw.get("model") or ""),
        "prompt": str(raw.get("prompt") or DEFAULT_PROMPT),
    }


def validate_settings(payload: dict[str, Any]) -> dict[str, Any]:
    auto_enabled = payload.get("auto_enabled")
    provider_id = payload.get("provider_id", "")
    model = payload.get("model", "")
    prompt = payload.get("prompt", DEFAULT_PROMPT)
    if not isinstance(auto_enabled, bool):
        raise CleanroomException(400, "INVALID_REQUEST", "auto_enabled 必须为布尔值")
    if not isinstance(provider_id, str) or len(provider_id) > 128:
        raise CleanroomException(400, "INVALID_REQUEST", "provider_id 无效")
    if not isinstance(model, str) or len(model) > 256:
        raise CleanroomException(400, "INVALID_REQUEST", "model 无效")
    if bool(provider_id.strip()) != bool(model.strip()):
        raise CleanroomException(400, "INVALID_REQUEST", "provider_id 与 model 必须同时填写")
    if not isinstance(prompt, str) or len(prompt) > MAX_PROMPT_LENGTH:
        raise CleanroomException(400, "INVALID_REQUEST", "VLM 提示词长度无效")
    return {
        "auto_enabled": auto_enabled,
        "provider_id": provider_id.strip(),
        "model": model.strip(),
        "prompt": prompt.strip() or DEFAULT_PROMPT,
    }


def set_settings(payload: dict[str, Any]) -> dict[str, Any]:
    validated = validate_settings(payload)
    state = storage.JsonState(SETTINGS_NAMESPACE, _default_settings)
    state.mutate(lambda raw: raw.update(validated))
    return get_settings()


def validate_provider_selection(provider_id: str, model: str) -> None:
    """只认当前服务端平台白名单中的已启用视觉模型，不读取或返回凭据。"""
    from gw.settings import default_provider_service

    providers = default_provider_service.get_snapshot().providers
    provider = next(
        (
            item
            for item in providers
            if str(item.get("provider_id") or item.get("id") or "") == provider_id
        ),
        None,
    )
    if not provider or provider.get("enabled") is False:
        raise CleanroomException(400, "VLM_PROVIDER_UNAVAILABLE", "请选择已启用的模型平台")
    models = provider.get("chat_models")
    if not isinstance(models, list) or model not in models:
        raise CleanroomException(400, "VLM_MODEL_UNAVAILABLE", "请选择该平台已登记的模型")


def _sample_times(segment: dict[str, Any]) -> list[float]:
    start = segment.get("start_sec")
    end = segment.get("end_sec")
    if (
        isinstance(start, bool)
        or isinstance(end, bool)
        or not isinstance(start, (int, float))
        or not isinstance(end, (int, float))
    ):
        return []
    start_sec, end_sec = float(start), float(end)
    if not math.isfinite(start_sec) or not math.isfinite(end_sec) or start_sec < 0 or end_sec <= start_sec:
        return []
    duration = end_sec - start_sec
    fractions = (0.2, 0.5, 0.8) if duration > 3.0 else (0.5,)
    return [start_sec + duration * fraction for fraction in fractions]


def _contact_sheet(
    source: Path,
    segment: dict[str, Any],
    frame_decoder: Callable[[Path, float], bytes],
) -> bytes:
    try:
        from PIL import Image, ImageOps
    except Exception:
        raise VideoDescriptionError("KEYFRAME_EXTRACTION_FAILED") from None

    frames = []
    seen: set[str] = set()
    for sample_sec in _sample_times(segment):
        try:
            encoded = frame_decoder(source, sample_sec)
            with Image.open(io.BytesIO(encoded)) as decoded:
                frame = decoded.convert("RGB")
                frame.load()
            pixel_hash = hashlib.sha256(
                f"{frame.width}x{frame.height}:".encode("ascii") + frame.tobytes()
            ).hexdigest()
            if pixel_hash not in seen:
                seen.add(pixel_hash)
                frames.append(frame.copy())
        except Exception:
            continue
    if not frames:
        raise VideoDescriptionError("KEYFRAME_EXTRACTION_FAILED")

    sheet = Image.new("RGB", (_IMAGE_WIDTH * len(frames), _IMAGE_HEIGHT), "black")
    for index, frame in enumerate(frames):
        tile = ImageOps.contain(frame, (_IMAGE_WIDTH, _IMAGE_HEIGHT))
        x = index * _IMAGE_WIDTH + (_IMAGE_WIDTH - tile.width) // 2
        y = (_IMAGE_HEIGHT - tile.height) // 2
        sheet.paste(tile, (x, y))
    output = io.BytesIO()
    sheet.save(output, format="JPEG", quality=82, optimize=True)
    return output.getvalue()


def _parse_reply(reply: Any) -> dict[str, Any]:
    if not isinstance(reply, str):
        raise VideoDescriptionError("VLM_OUTPUT_INVALID")
    try:
        value = json.loads(reply)
    except (json.JSONDecodeError, TypeError):
        raise VideoDescriptionError("VLM_OUTPUT_INVALID") from None
    if not isinstance(value, dict) or set(value) != {"summary", "tags"}:
        raise VideoDescriptionError("VLM_OUTPUT_INVALID")
    summary = value.get("summary")
    tags = value.get("tags")
    if not isinstance(summary, str) or not summary.strip() or len(summary) > MAX_SUMMARY_LENGTH:
        raise VideoDescriptionError("VLM_OUTPUT_INVALID")
    if not isinstance(tags, list) or len(tags) > MAX_TAGS:
        raise VideoDescriptionError("VLM_OUTPUT_INVALID")
    normalized_tags = []
    seen = set()
    for tag in tags:
        if not isinstance(tag, str) or len(tag.strip()) > MAX_TAG_LENGTH:
            raise VideoDescriptionError("VLM_OUTPUT_INVALID")
        clean = tag.strip()
        key = clean.casefold()
        if clean and key not in seen:
            seen.add(key)
            normalized_tags.append(clean)
    return {"summary": summary.strip(), "tags": normalized_tags}


def _same_file_version(left: Any, right: Any) -> bool:
    keys = ("size_bytes", "mtime_ns", "ino", "dev")
    if not isinstance(left, dict) or not isinstance(right, dict):
        return False
    try:
        return all(int(left[key]) == int(right[key]) for key in keys)
    except (KeyError, TypeError, ValueError):
        return False


def _file_version(path: Path) -> dict[str, int]:
    info = path.stat()
    return {
        "size_bytes": int(info.st_size),
        "mtime_ns": int(info.st_mtime_ns),
        "ino": int(info.st_ino),
        "dev": int(info.st_dev),
    }


def process_asset(
    asset: dict[str, Any],
    *,
    provider_id: str,
    model: str,
    prompt: str,
    frame_decoder: Callable[[Path, float], bytes],
    checkpoint: Callable[[], None],
    is_auto: bool = False,
) -> dict[str, Any]:
    """为可见资产生成分镜描述；任一时刻身份变化时放弃整资产写入。"""
    from gw.asset_registry import repository as repo
    from gw.settings import chat as chat_runtime

    asset_id = str(asset.get("asset_id") or asset.get("id") or "")
    metadata = asset.get("metadata") if isinstance(asset.get("metadata"), dict) else {}
    segments = metadata.get("video_segments")
    if not asset_id or not isinstance(segments, list) or not segments:
        return {"generated": 0, "failed": 1, "errors": [{"asset_id": asset_id, "error_code": "VIDEO_SEGMENTS_UNAVAILABLE"}]}
    source = repo._asset_local_video_path(asset)
    if source is None:
        return {"generated": 0, "failed": 1, "errors": [{"asset_id": asset_id, "error_code": "VIDEO_SOURCE_UNAVAILABLE"}]}
    expected_version = asset.get("version")
    expected_scan_version = asset.get("scan_version")
    updates = {}
    errors = []
    failed = 0

    def record_error(shot_index: int, code: str) -> None:
        nonlocal failed
        failed += 1
        if len(errors) < 100:
            errors.append({"asset_id": asset_id, "shot_index": shot_index, "error_code": code})

    def verify_current() -> bool:
        checkpoint()
        if is_auto and not get_settings()["auto_enabled"]:
            raise VideoDescriptionError("VLM_AUTO_DISABLED")
        snapshot = repo.readonly_registry_snapshot()
        current = repo.visible_asset_from_snapshot(snapshot, asset_id)
        if not isinstance(current, dict) or current.get("version") != expected_version:
            return False
        if not _same_file_version(current.get("scan_version"), expected_scan_version):
            return False
        try:
            return _same_file_version(_file_version(source), expected_scan_version)
        except OSError:
            return False

    for shot_index, segment in enumerate(segments):
        if not isinstance(segment, dict):
            record_error(shot_index, "VIDEO_SEGMENT_INVALID")
            continue
        try:
            if not verify_current():
                raise VideoDescriptionError("ASSET_CHANGED_DURING_VLM")
            sheet = _contact_sheet(source, segment, frame_decoder)
            if not verify_current():
                raise VideoDescriptionError("ASSET_CHANGED_DURING_VLM")
            image_url = "data:image/jpeg;base64," + base64.b64encode(sheet).decode("ascii")
            response = chat_runtime.complete_messages(
                {"provider_id": provider_id, "model": model},
                [
                    {
                        "role": "system",
                        "content": (
                            "根据用户提示和图像生成镜头摘要与标签。只输出一个合法 JSON 对象，"
                            "键必须严格为 summary 和 tags；summary 为简洁的可见画面描述，"
                            "tags 为字符串数组。不要输出 Markdown 或其它文字。"
                        ),
                    },
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": prompt},
                            {"type": "image_url", "image_url": {"url": image_url}},
                        ],
                    }
                ],
                timeout_seconds=60,
                max_output_tokens=700,
                endpoint="/api/asset-registry/video-descriptions/jobs",
            )
            updates[shot_index] = _parse_reply(response.get("reply"))
        except VideoDescriptionError as exc:
            record_error(shot_index, exc.code)
            if exc.code in {"ASSET_CHANGED_DURING_VLM", "VLM_AUTO_DISABLED"}:
                return {"generated": 0, "failed": max(1, len(segments)), "errors": errors}
        except Exception:
            record_error(shot_index, "VLM_REQUEST_FAILED")

    try:
        current = verify_current()
    except VideoDescriptionError as exc:
        failed += 1
        if len(errors) < 100:
            errors.append({"asset_id": asset_id, "error_code": exc.code})
        return {"generated": 0, "failed": max(1, len(segments)), "errors": errors}
    if not current:
        return {
            "generated": 0,
            "failed": max(1, len(segments)),
            "errors": [{"asset_id": asset_id, "error_code": "ASSET_CHANGED_DURING_VLM"}],
        }
    if updates:
        written = repo.update_video_segment_descriptions(
            asset_id,
            expected_version=expected_version,
            expected_scan_version=expected_scan_version,
            updates=updates,
        )
        if not written:
            return {
                "generated": 0,
                "failed": max(1, len(segments)),
                "errors": [{"asset_id": asset_id, "error_code": "ASSET_CHANGED_DURING_VLM"}],
            }
    return {"generated": len(updates), "failed": failed, "errors": errors}


__all__ = [
    "DEFAULT_PROMPT",
    "VideoDescriptionError",
    "get_settings",
    "process_asset",
    "set_settings",
    "validate_provider_selection",
    "validate_settings",
]
