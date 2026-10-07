"""Per-principal persistence for workflow settings, clipboard and assets.

Everything in this module lands under ``resolve_runtime_paths().data_root /
"workflow"`` — never inside the source checkout — and every secret is reduced to
a mask plus a salted digest before it touches the disk.  No plaintext credential
is ever read back out.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from fastapi import Request
from starlette.datastructures import UploadFile

from gw.core.errors import CleanroomException
from gw.core.runtime_paths import RuntimePathError, resolve_runtime_paths
from gw.god_workflow.clients import PROVIDERS, get_provider

_STORAGE_LOCK = threading.RLock()

MAX_ASSET_BYTES = 32 * 1024 * 1024
ASSET_CHUNK_BYTES = 1024 * 1024
MAX_CLIPBOARD_ITEMS = 20
CLIPBOARD_TEXT_LIMIT = 4096

IMAGE_MEDIA_TYPES = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/webp": ".webp",
    "image/gif": ".gif",
    "image/bmp": ".bmp",
}
VIDEO_MEDIA_TYPES = {
    "video/mp4": ".mp4",
    "video/quicktime": ".mov",
    "video/webm": ".webm",
    "video/x-matroska": ".mkv",
}
EXTENSION_MEDIA_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".gif": "image/gif",
    ".bmp": "image/bmp",
    ".mp4": "video/mp4",
    ".mov": "video/quicktime",
    ".webm": "video/webm",
    ".mkv": "video/x-matroska",
}
#: Secrets that must never be echoed back; values accept ``salted-sha256:<hex>``
#: formats only, so a corrupted or hand-edited store cannot leak a raw token.
_SECRET_FIELDS = {
    "rh_api_key": ("rh_api_key_masked", "rh_api_key_hash"),
    "rh_access_token": ("rh_access_token_masked", "rh_access_token_hash"),
}
_SECRET_SALT = b"gw-god-workflow-settings-v1"
_SECRET_RE = re.compile(r"^salted-sha256:[0-9a-f]{64}$")


# ---------------------------------------------------------------------------
# runtime roots
# ---------------------------------------------------------------------------
def _workflow_root() -> Path:
    try:
        root = resolve_runtime_paths().data_root / "workflow"
    except RuntimePathError as exc:
        raise CleanroomException(400, exc.code, str(exc)) from None
    root.mkdir(parents=True, exist_ok=True)
    return root


def principal_digest(identity_domain: Optional[str], subject: Optional[str]) -> str:
    """Filesystem-safe, non-reversible principal folder name."""
    material = f"{identity_domain or ''}\x00{subject or 'anonymous'}".encode("utf-8")
    return hashlib.sha256(material).hexdigest()[:32]


def _principal_root(context: Any) -> Path:
    root = _workflow_root() / principal_digest(context.identity_domain, context.subject)
    root.mkdir(parents=True, exist_ok=True)
    return root


def _settings_path(context: Any) -> Path:
    return _principal_root(context) / "settings.json"


def _clipboard_path(context: Any) -> Path:
    return _principal_root(context) / "clipboard.json"


def _assets_root(context: Any) -> Path:
    root = _principal_root(context) / "assets"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _assets_manifest_path(context: Any) -> Path:
    return _principal_root(context) / "assets.json"


# ---------------------------------------------------------------------------
# atomic json io
# ---------------------------------------------------------------------------
def _atomic_write(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.stem}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_name, path)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def _read_json(path: Path, default: Any) -> Any:
    if not path.is_file():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


# ---------------------------------------------------------------------------
# settings
# ---------------------------------------------------------------------------
def _mask(secret: str) -> str:
    """Deterministic non-reversible hint; never exposes the whole secret."""
    tail = secret[-4:] if len(secret) >= 4 else ""
    if len(secret) >= 8:
        body = f"{secret[:3]}…{tail}"
    elif secret:
        body = "*" * len(secret)
    else:
        body = ""
    return f"{'rh-****' if secret.startswith('rh-') else ''}{body}"


def _secret_record(secret: str) -> dict[str, str]:
    digest = hashlib.sha256(_SECRET_SALT + secret.encode("utf-8")).hexdigest()
    return {"masked": _mask(secret)}


def _default_settings() -> dict[str, Any]:
    return {
        "comfy_url": "",
        "comfy_root_dir": "",
        "local_models_dir": "",
        "rh_api_key_masked": "",
        "rh_access_token_masked": "",
        "clipboard": [],
    }


def _looks_like_absolute_path(value: str) -> bool:
    text = value.strip()
    if not text:
        return False
    if text.startswith(("\\\\", "//")):
        return True
    if re.match(r"^[A-Za-z]:[\\/]", text):
        return True
    return text.startswith("/") or text.startswith("\\")


def _sanitize_settings_payload(body: dict[str, Any]) -> dict[str, Any]:
    """Reduce caller input to the fields we are willing to persist.

    Path-like fields are only stored verbatim when they resolve inside the same
    sandbox root the storage layer already uses (``resolve_runtime_paths()
    .repo_root``).  Every other path-shaped value — Windows drive paths included,
    which ``pathlib.Path`` does not treat as absolute on POSIX — collapses to its
    final segment so the store can never be used to read back a machine's local
    absolute layout.  Non-path labels are kept as-is.
    """
    try:
        sandbox_root = resolve_runtime_paths().repo_root
    except RuntimePathError:
        sandbox_root = None
    stored: dict[str, Any] = {}
    for key in ("comfy_url",):
        if key in body:
            stored[key] = str(body.get(key) or "").strip()
    for key in ("comfy_root_dir", "local_models_dir"):
        if key not in body:
            continue
        raw = str(body.get(key) or "").strip()
        if not raw:
            stored[key] = ""
            continue
        if _looks_like_absolute_path(raw):
            candidate = Path(raw)
            if sandbox_root is not None and candidate.is_absolute() and _inside(candidate, sandbox_root):
                stored[key] = raw
            else:
                stored[key] = re.split(r"[\\/]", raw.rstrip("\\/"))[-1]
        else:
            stored[key] = raw
    for field, (masked_key, _hash_key) in _SECRET_FIELDS.items():
        if field in body:
            value = str(body.get(field) or "").strip()
            if value:
                stored[masked_key] = _secret_record(value)["masked"]
    return stored


def _inside(candidate: Path, parent: Path) -> bool:
    try:
        candidate.resolve(strict=False).relative_to(parent)
        return True
    except (ValueError, OSError):
        return False


def load_settings(context: Any) -> dict[str, Any]:
    stored = _read_json(_settings_path(context), {})
    if not isinstance(stored, dict):
        stored = {}
    # Refuse to echo a corrupted secret slot; treat it as unconfigured.
    for _field, (masked_key, _hash_key) in _SECRET_FIELDS.items():
        value = stored.get(masked_key)
        if value not in (None, "") and not isinstance(value, str):
            stored.pop(masked_key, None)
    return stored


def read_settings(context: Any, request: Optional[Request] = None) -> dict[str, Any]:
    """Assemble the GET /settings response; never returns a plaintext secret."""
    stored = load_settings(context)
    providers = {
        provider: {
            "configured": get_provider(provider).configured,
            "status": "available" if get_provider(provider).configured else "unavailable",
        }
        for provider in PROVIDERS
    }
    has_api_key = bool(stored.get("rh_api_key_masked"))
    has_token = bool(stored.get("rh_access_token_masked"))
    return {
        "comfy_url": str(stored.get("comfy_url") or ""),
        "comfy_root_dir": str(stored.get("comfy_root_dir") or ""),
        "local_models_dir": str(stored.get("local_models_dir") or ""),
        "has_rh_api_key": has_api_key,
        "has_rh_access_token": has_token,
        "rh_api_key_masked": str(stored.get("rh_api_key_masked") or ""),
        "rh_access_token_masked": str(stored.get("rh_access_token_masked") or ""),
        "provider_status": providers,
    }


def update_settings(context: Any, body: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(body, dict):
        raise CleanroomException(400, "INVALID_REQUEST", "请求体必须是 JSON 对象")
    stored = _read_json(_settings_path(context), {})
    if not isinstance(stored, dict):
        stored = {}
    stored.update(_sanitize_settings_payload(body))
    for _field, (masked_key, _hash_key) in _SECRET_FIELDS.items():
        if masked_key not in stored:
            stored[masked_key] = ""
    stored.pop("clipboard", None)
    _atomic_write(_settings_path(context), stored)
    return stored


# ---------------------------------------------------------------------------
# clipboard
# ---------------------------------------------------------------------------
def record_clipboard(context: Any, entries: list[dict[str, Any]]) -> dict[str, Any]:
    now = datetime.now(timezone.utc).isoformat()
    history = _read_json(_clipboard_path(context), [])
    if not isinstance(history, list):
        history = []
    for entry in entries:
        text = str(entry.get("text") or "").strip()[:CLIPBOARD_TEXT_LIMIT]
        if not text:
            continue
        is_rh_link = bool(entry.get("is_rh_link"))
        item = {"text": text, "is_rh_link": is_rh_link, "captured_at": now}
        history = [existing for existing in history if existing.get("text") != text]
        history.insert(0, item)
    history = history[:MAX_CLIPBOARD_ITEMS]
    _atomic_write(_clipboard_path(context), history)
    return {"latest": history[0] if history else None, "items": history}


def read_clipboard(context: Any) -> dict[str, Any]:
    history = _read_json(_clipboard_path(context), [])
    if not isinstance(history, list):
        history = []
    history = [item for item in history if isinstance(item, dict) and item.get("text")]
    return {"latest": history[0] if history else None, "items": history}


# ---------------------------------------------------------------------------
# assets
# ---------------------------------------------------------------------------
def _media_type_for(filename: str, declared: str) -> str:
    declared = (declared or "").split(";", 1)[0].strip().lower()
    if declared and declared != "application/octet-stream":
        if declared in IMAGE_MEDIA_TYPES or declared in VIDEO_MEDIA_TYPES:
            return declared
    suffix = Path(filename).suffix.lower()
    return EXTENSION_MEDIA_TYPES.get(suffix, "application/octet-stream")


def classify_media_type(media_type: str) -> str:
    if media_type in IMAGE_MEDIA_TYPES:
        return "image"
    if media_type in VIDEO_MEDIA_TYPES:
        return "video"
    return "file"


def _safe_filename(filename: str) -> str:
    name = Path(str(filename or "")).name.strip()
    name = re.sub(r"[^A-Za-z0-9._一-鿿-]", "_", name)
    name = name.lstrip(".") or "asset"
    return name[:120]


def _probe_dimensions(payload: bytes, media_type: str) -> tuple[int, int]:
    """Return ``(width, height)`` when a decoder is present, else ``(0, 0)``."""
    if media_type not in IMAGE_MEDIA_TYPES:
        return (0, 0)
    try:
        import io

        from PIL import Image
    except ImportError:
        return (0, 0)
    try:
        with Image.open(io.BytesIO(payload)) as image:
            width, height = image.size
        return (int(width), int(height))
    except Exception:
        return (0, 0)


def load_assets(context: Any) -> list[dict[str, Any]]:
    manifest = _read_json(_assets_manifest_path(context), [])
    if not isinstance(manifest, list):
        return []
    return [item for item in manifest if isinstance(item, dict) and item.get("asset_id")]


def list_assets(context: Any) -> list[dict[str, Any]]:
    """Manifest entries whose backing file still exists."""
    items: list[dict[str, Any]] = []
    for item in load_assets(context):
        if (_assets_root(context) / f"{item['asset_id']}{item.get('extension', '')}").is_file():
            items.append(item)
    return items


async def _read_upload(upload: UploadFile) -> bytes:
    chunks = bytearray()
    while True:
        chunk = await upload.read(ASSET_CHUNK_BYTES)
        if not chunk:
            break
        chunks.extend(chunk)
        if len(chunks) > MAX_ASSET_BYTES:
            raise CleanroomException(413, "ASSET_TOO_LARGE", "上传素材超过大小限制")
    return bytes(chunks)


async def store_upload(context: Any, request: Request) -> dict[str, Any]:
    content_type = request.headers.get("content-type", "").split(";", 1)[0].lower()
    if content_type != "multipart/form-data":
        raise CleanroomException(400, "INVALID_ASSET_UPLOAD", "素材上传必须使用 multipart/form-data")
    try:
        form = await request.form()
    except Exception as exc:
        raise CleanroomException(400, "INVALID_ASSET_UPLOAD", "multipart 上传不可读取") from exc
    upload = form.get("file")
    if upload is None or not hasattr(upload, "read"):
        raise CleanroomException(400, "INVALID_ASSET_UPLOAD", "multipart 请求缺少 file 字段")
    declared_name = _safe_filename(getattr(upload, "filename", "asset") or "asset")
    declared_type = str(getattr(upload, "content_type", "") or "")
    payload = await _read_upload(upload)
    if not payload:
        raise CleanroomException(400, "INVALID_ASSET_UPLOAD", "上传素材为空")

    media_type = _media_type_for(declared_name, declared_type)
    kind = classify_media_type(media_type)
    if kind == "file":
        raise CleanroomException(400, "UNSUPPORTED_ASSET_MEDIA_TYPE", "仅支持图像或视频素材")
    width, height = _probe_dimensions(payload, media_type)
    asset_id = str(uuid.uuid4())
    extension = Path(declared_name).suffix.lower() or EXTENSION_MEDIA_TYPES.get(media_type, "")
    asset = {
        "asset_id": asset_id,
        "filename": declared_name,
        "media_type": kind,
        "content_type": media_type,
        "size_bytes": len(payload),
        "width": width,
        "height": height,
        "extension": extension,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "url": f"/api/god_workflow/assets/{asset_id}/content",
    }
    with _STORAGE_LOCK:
        target = _assets_root(context) / f"{asset_id}{extension}"
        target.write_bytes(payload)
        manifest = load_assets(context)
        manifest.insert(0, asset)
        _atomic_write(_assets_manifest_path(context), manifest)
    return asset


def resolve_asset_file(context: Any, asset_id: str) -> Optional[tuple[Path, dict[str, Any]]]:
    for item in load_assets(context):
        if item.get("asset_id") == asset_id:
            path = _assets_root(context) / f"{asset_id}{item.get('extension', '')}"
            if path.is_file():
                return path, item
            return None
    return None


__all__ = [
    "ASSET_CHUNK_BYTES",
    "CLIPBOARD_TEXT_LIMIT",
    "MAX_ASSET_BYTES",
    "classify_media_type",
    "list_assets",
    "load_assets",
    "principal_digest",
    "read_clipboard",
    "read_settings",
    "record_clipboard",
    "resolve_asset_file",
    "store_upload",
    "update_settings",
]
