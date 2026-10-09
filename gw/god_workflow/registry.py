"""Per-principal persistence for workflow settings, clipboard and assets.

数据落在运行时 workflow 主体目录。凭据复用本地保护器加密，接口只返回掩码。
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
from gw.settings import credentials

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
DOCUMENT_MEDIA_TYPES = {"image/vnd.adobe.photoshop": ".psd"}
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
    ".psd": "image/vnd.adobe.photoshop",
}
#: 当前主体的本地密文优先；未保存时兼容环境变量。
_CREDENTIAL_ENV = {
    "rh_api_key": ("GW_RUNNINGHUB_API_KEY", "rh_api_key_masked"),
    "rh_access_token": ("GW_RUNNINGHUB_ACCESS_TOKEN", "rh_access_token_masked"),
}
#: 兼容旧的字段名映射；保持 _SECRET_FIELDS 形态以免破坏既有导入。
_SECRET_FIELDS = {
    field: (masked_key, f"{field}_hash") for field, (_env, masked_key) in _CREDENTIAL_ENV.items()
}


def _credential_from_settings(field: str, context: Any, stored: dict[str, Any]) -> str:
    encrypted_key = f"{field}_encrypted"
    if encrypted_key in stored:
        # 密文损坏时失败关闭，不能悄悄改用另一套环境凭据。
        owner = "workflow:" + principal_digest(context.identity_domain, context.subject)
        return credentials.reveal(owner, field, stored[encrypted_key])
    env_name = _CREDENTIAL_ENV.get(field, ("", ""))[0]
    return os.getenv(env_name, "").strip() if env_name else ""


def credential_value(field: str, context: Any) -> str:
    """仅供已鉴权的供应商调用读取，不进入响应与任务快照。"""
    if field not in _CREDENTIAL_ENV:
        return ""
    with _STORAGE_LOCK:
        return _credential_from_settings(field, context, load_settings(context))


def credential_env_name(field: str) -> str:
    """返回该凭据对应的环境变量名，供设置页提示操作者如何配置。"""
    return _CREDENTIAL_ENV.get(field, ("", ""))[0]


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


def _tasks_root(context: Any) -> Path:
    root = _principal_root(context) / "tasks"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _task_path(context: Any, task_id: str) -> Path:
    try:
        parsed = uuid.UUID(str(task_id))
    except (ValueError, AttributeError, TypeError):
        raise CleanroomException(404, "TASK_NOT_FOUND", "任务不存在") from None
    return _tasks_root(context) / f"{parsed}.json"


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


def _default_settings() -> dict[str, Any]:
    return {
        "comfy_url": "",
        "comfy_root_dir": "",
        "local_models_dir": "",
        "rh_api_key_masked": "",
        "rh_access_token_masked": "",
        "clipboard": [],
    }


def _sanitize_settings_payload(body: dict[str, Any], context: Any) -> dict[str, Any]:
    """Reduce caller input to the fields we are willing to persist.

    ``comfy_root_dir`` and ``local_models_dir`` are user-supplied local
    configuration values: they are persisted and echoed back verbatim, exactly
    as the operator typed them.

    凭据只保存主体绑定的密文，空输入保留已有值。
    """
    stored: dict[str, Any] = {}
    for key in ("comfy_url", "comfy_root_dir", "local_models_dir"):
        if key in body:
            stored[key] = str(body.get(key) or "").strip()
    for field, (_env_name, masked_key) in _CREDENTIAL_ENV.items():
        if field in body:
            raw = body[field]
            if not isinstance(raw, str):
                raise CleanroomException(400, "INVALID_REQUEST", "凭据必须为字符串")
            value = raw.strip()
            if value:
                if body.get(f"clear_{field}"):
                    raise CleanroomException(400, "INVALID_REQUEST", "不能同时填写和清除同一凭据")
                owner = "workflow:" + principal_digest(context.identity_domain, context.subject)
                stored[f"{field}_encrypted"] = credentials.protect(owner, field, value)
                stored[masked_key] = _mask(value)
        if f"clear_{field}" in body and not isinstance(body[f"clear_{field}"], bool):
            raise CleanroomException(400, "INVALID_REQUEST", "清除凭据标记必须为布尔值")
    return stored


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
    """Assemble the GET /settings response; never returns a plaintext secret.

    本地密文优先、环境变量回退。损坏密文不会伪造配置状态。
    """
    stored = load_settings(context)
    providers = {
        provider: {
            "configured": get_provider(provider).configured,
            "status": "available" if get_provider(provider).configured else "unavailable",
        }
        for provider in PROVIDERS
    }
    api_key = _credential_from_settings("rh_api_key", context, stored)
    access_token = _credential_from_settings("rh_access_token", context, stored)
    return {
        "comfy_url": str(stored.get("comfy_url") or ""),
        "comfy_root_dir": str(stored.get("comfy_root_dir") or ""),
        "local_models_dir": str(stored.get("local_models_dir") or ""),
        "has_rh_api_key": bool(api_key),
        "has_rh_access_token": bool(access_token),
        "rh_api_key_masked": _mask(api_key),
        "rh_access_token_masked": _mask(access_token),
        "rh_api_key_env": credential_env_name("rh_api_key"),
        "rh_access_token_env": credential_env_name("rh_access_token"),
        "rh_api_key_source": "local" if "rh_api_key_encrypted" in stored else "environment" if api_key else "none",
        "rh_access_token_source": "local" if "rh_access_token_encrypted" in stored else "environment" if access_token else "none",
        "credential_source": "local" if any(f"{field}_encrypted" in stored for field in _CREDENTIAL_ENV) else "environment",
        "provider_status": providers,
    }


def update_settings(context: Any, body: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(body, dict):
        raise CleanroomException(400, "INVALID_REQUEST", "请求体必须是 JSON 对象")
    with _STORAGE_LOCK:
        stored = load_settings(context)
        stored.update(_sanitize_settings_payload(body, context))
        for field, (masked_key, hash_key) in _SECRET_FIELDS.items():
            if body.get(f"clear_{field}") is True:
                stored.pop(f"{field}_encrypted", None)
                stored[masked_key] = ""
            # 旧掩码不能还原密钥；移除旧散列及可能遗留的明文字段。
            stored.pop(field, None)
            stored.pop(hash_key, None)
            stored.setdefault(masked_key, "")
            _credential_from_settings(field, context, stored)
        stored.pop("clipboard", None)
        _atomic_write(_settings_path(context), stored)
        return read_settings(context)


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
        if declared in IMAGE_MEDIA_TYPES or declared in VIDEO_MEDIA_TYPES or declared in DOCUMENT_MEDIA_TYPES:
            return declared
    suffix = Path(filename).suffix.lower()
    return EXTENSION_MEDIA_TYPES.get(suffix, "application/octet-stream")


def classify_media_type(media_type: str) -> str:
    if media_type in IMAGE_MEDIA_TYPES:
        return "image"
    if media_type in VIDEO_MEDIA_TYPES:
        return "video"
    if media_type in DOCUMENT_MEDIA_TYPES:
        return "document"
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
        # 损坏清单不能跳过已核验凭单的补偿恢复。
        manifest = []
    items = [item for item in manifest if isinstance(item, dict) and item.get("asset_id")]
    # 先写恢复凭单再写文件；清单失败或进程中断后以哈希核验原文件补登，不删除产物。
    with _STORAGE_LOCK:
        recovery_root = _principal_root(context) / "output_recovery"
        changed = False
        for receipt in recovery_root.glob("*.json"):
            asset = _read_json(receipt, None)
            if not isinstance(asset, dict) or asset.get("asset_id") != receipt.stem:
                continue
            if any(item.get("asset_id") == receipt.stem for item in items):
                continue
            extension = asset.get("extension", "")
            if extension not in EXTENSION_MEDIA_TYPES:
                continue
            target = _assets_root(context) / f"{receipt.stem}{extension}"
            if not target.is_file() or target.stat().st_size != asset.get("size_bytes"):
                continue
            if hashlib.sha256(target.read_bytes()).hexdigest() != asset.get("content_sha256"):
                continue
            items.insert(0, asset)
            changed = True
        if changed:
            _atomic_write(_assets_manifest_path(context), items)
    return items


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

    return store_bytes(context, payload, declared_name, declared_type)


def resolve_asset_file(context: Any, asset_id: str) -> Optional[tuple[Path, dict[str, Any]]]:
    for item in load_assets(context):
        if item.get("asset_id") == asset_id:
            path = _assets_root(context) / f"{asset_id}{item.get('extension', '')}"
            if path.is_file():
                return path, item
            return None
    return None


def store_bytes(context: Any, payload: bytes, filename: str, media_type: str,
                *, task_id: Optional[str] = None) -> dict[str, Any]:
    """把执行输出字节登记为素材（与上传共用同一清单与命名规则）。

    接受图像、视频及 PSD 原件；PSD 保留字节并提供下载，不伪造图层预览。
    """
    if not payload:
        raise CleanroomException(400, "INVALID_ASSET_UPLOAD", "输出文件为空")
    if len(payload) > MAX_ASSET_BYTES:
        raise CleanroomException(413, "ASSET_TOO_LARGE", "输出素材超过大小限制")
    safe_name = _safe_filename(filename)
    resolved_type = _media_type_for(safe_name, media_type)
    kind = classify_media_type(resolved_type)
    if kind == "file":
        raise CleanroomException(400, "UNSUPPORTED_ASSET_MEDIA_TYPE", "仅支持图像、视频或 PSD 原件")
    width, height = _probe_dimensions(payload, resolved_type)
    if kind == "document":
        # 只核验 PSD 标准文件头；图层数据原样保存，浏览器不尝试解释。
        if len(payload) < 26 or payload[:6] != b'8BPS\x00\x01' or payload[6:12] != bytes(6):
            raise CleanroomException(400, "INVALID_PSD_HEADER", "PSD 文件头无效")
        height, width = int.from_bytes(payload[14:18], 'big'), int.from_bytes(payload[18:22], 'big')
        if not 0 < width <= 30000 or not 0 < height <= 30000:
            raise CleanroomException(400, "INVALID_PSD_HEADER", "PSD 图像尺寸无效")
    asset_id = str(uuid.uuid4())
    # 存储后缀与恢复白名单一致，原始文件名仍保留为显示/去重字段。
    extension = Path(safe_name).suffix.lower()
    if extension not in EXTENSION_MEDIA_TYPES:
        extension = IMAGE_MEDIA_TYPES.get(resolved_type) or VIDEO_MEDIA_TYPES.get(resolved_type) or DOCUMENT_MEDIA_TYPES.get(resolved_type, "")
    asset = {
        "asset_id": asset_id,
        "filename": safe_name,
        "media_type": kind,
        "content_type": resolved_type,
        "size_bytes": len(payload),
        "width": width,
        "height": height,
        "extension": extension,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "url": f"/api/god_workflow/assets/{asset_id}/content",
        "origin": "task" if task_id else "upload",
        "task_id": task_id,
        "preview_available": kind != "document",
    }
    if task_id:
        try:
            task = load_task(context, task_id)
        except CleanroomException as error:
            if error.status_code != 404:
                raise
        else:
            asset.update({"job_id": task["job_id"], "workflow_id": task.get("workflow_id"),
                          "workflow_version": task.get("workflow_version"),
                          "source_context": task.get("source_context", {})})
            asset.update(task.get("source_context", {}))
    with _STORAGE_LOCK:
        digest = hashlib.sha256(payload).hexdigest()
        manifest = load_assets(context)
        if task_id:
            for existing in manifest:
                if existing.get("task_id") == task_id and existing.get("filename") == safe_name and existing.get("content_sha256") == digest:
                    return existing
        asset["content_sha256"] = digest
        target = _assets_root(context) / f"{asset_id}{extension}"
        # 凭单先落盘；文件原子替换后才宣称清单登记成功。凭单保留作为补偿记录。
        _atomic_write(_principal_root(context) / "output_recovery" / f"{asset_id}.json", asset)
        fd, temp_name = tempfile.mkstemp(prefix=".output.", suffix=".tmp", dir=target.parent)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temp_name, target)
        finally:
            if os.path.exists(temp_name):
                os.unlink(temp_name)
        manifest.insert(0, asset)
        _atomic_write(_assets_manifest_path(context), manifest)
    return asset


# ---------------------------------------------------------------------------
# tasks
# ---------------------------------------------------------------------------
def create_task(context: Any, *, workflow_id: Optional[str], prompt_id: Optional[str], target: str,
                node_count: int = 0, idempotency_key: Optional[str] = None,
                request_fingerprint: Optional[str] = None, outcome_unknown: bool = False) -> dict[str, Any]:
    """持久化任务身份与状态；本地 task_id 兼容保留，job_id 是公共稳定键。"""
    now = datetime.now(timezone.utc).isoformat()
    job_id = str(uuid.uuid4())
    public_status = "outcome_unknown" if outcome_unknown else "accepted"
    task = {
        "job_id": job_id,
        "task_id": job_id,
        "workflow_id": workflow_id,
        "remote_task_id": str(prompt_id) if prompt_id else None,
        "prompt_id": str(prompt_id) if prompt_id else None,
        "target": str(target),
        "status": public_status,
        "source_status": "unknown" if outcome_unknown else "queued",
        "execution_status": "unknown" if outcome_unknown else "accepted",
        "collection_status": "pending",
        "node_count": int(node_count),
        "outputs": [],
        "messages": [],
        "remote_cancelled": None,
        "remote_id_missing": not bool(prompt_id),
        "idempotency_key": str(idempotency_key or "").strip() or None,
        "request_fingerprint": request_fingerprint,
        "progress": {"known": False, "value": None},
        "created_at": now,
        "updated_at": now,
    }
    with _STORAGE_LOCK:
        try:
            _atomic_write(_task_path(context, task["task_id"]), task)
        except OSError:
            raise CleanroomException(503, "TASK_LEDGER_WRITE_FAILED", "任务账本写入失败，未发送请求") from None
    return task


def reserve_task(context: Any, **kwargs: Any) -> tuple[dict[str, Any], bool]:
    """单进程原子幂等预留；发送后中断保持未知，不自动重复副作用。"""
    with _STORAGE_LOCK:
        existing = find_idempotent_task(context, kwargs.get("idempotency_key") or "", kwargs["request_fingerprint"])
        if existing is not None:
            return load_task(context, existing["task_id"]), True
        return create_task(context, prompt_id=None, outcome_unknown=True, **kwargs), False


def find_idempotent_task(context: Any, idempotency_key: str, request_fingerprint: str) -> dict[str, Any] | None:
    """按主体范围查找幂等账本；同键不同指纹由调用方拒绝。"""
    key = str(idempotency_key or '').strip()
    if not key:
        return None
    for path in _tasks_root(context).glob("*.json"):
        task = _read_json(path, None)
        if not isinstance(task, dict):
            raise CleanroomException(503, "TASK_LEDGER_UNREADABLE", "任务账本无法读取，拒绝重复提交")
        if task.get("idempotency_key") != key:
            continue
        if task.get("request_fingerprint") != request_fingerprint:
            raise CleanroomException(409, "IDEMPOTENCY_CONFLICT", "幂等键已用于不同请求")
        return task
    return None


def list_tasks(context: Any) -> list[dict[str, Any]]:
    with _STORAGE_LOCK:
        tasks = [load_task(context, path.stem) for path in _tasks_root(context).glob("*.json")]
        return sorted(tasks, key=lambda task: task.get("created_at", ""), reverse=True)[:100]


def load_task(context: Any, task_id: str) -> dict[str, Any]:
    path = _task_path(context, task_id)
    task = _read_json(path, None)
    if not isinstance(task, dict) or not task.get("task_id"):
        raise CleanroomException(404, "TASK_NOT_FOUND", "任务不存在")
    task.setdefault("job_id", task["task_id"])
    task.setdefault("remote_task_id", task.get("prompt_id"))
    task.setdefault("source_status", task.get("status"))
    task["status"] = {"queued": "accepted", "succeeded": "completed", "canceled": "cancelled", "interrupted": "cancelled"}.get(task.get("status"), task.get("status"))
    return task


def update_task(context: Any, task_id: str, **changes: Any) -> dict[str, Any]:
    with _STORAGE_LOCK:
        task = load_task(context, task_id)
        task.update(changes)
        task["updated_at"] = datetime.now(timezone.utc).isoformat()
        try:
            _atomic_write(_task_path(context, task_id), task)
        except OSError:
            raise CleanroomException(503, "TASK_LEDGER_WRITE_FAILED", "任务账本写入失败，请查询恢复，勿重新生成") from None
    return task


__all__ = [
    "ASSET_CHUNK_BYTES",
    "CLIPBOARD_TEXT_LIMIT",
    "MAX_ASSET_BYTES",
    "classify_media_type",
    "create_task",
    "find_idempotent_task",
    "list_assets",
    "load_assets",
    "load_task",
    "principal_digest",
    "read_clipboard",
    "read_settings",
    "record_clipboard",
    "resolve_asset_file",
    "store_bytes",
    "store_upload",
    "update_settings",
    "update_task",
]
