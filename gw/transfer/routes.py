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

"""QIN-629 显式文件上传、校验与工作区资产发布 API。"""

from __future__ import annotations

import hashlib
import os
import re
import secrets
import shutil
from pathlib import Path, PureWindowsPath
from typing import Any

from fastapi import APIRouter, Depends, Header, Query, Request, status
from pydantic import BaseModel, ConfigDict, Field

from gw.asset_registry import repository as repo
from gw.core import storage
from gw.core.asset_visibility import (
    asset_owner_key,
    asset_request_scope,
    asset_visible,
    current_asset_owner,
    current_auth_context,
)
from gw.core.auth import require_edit_access
from gw.core.errors import CleanroomException
from gw.transfer.service import (
    MAX_FILE_BYTES,
    MAX_FILES,
    MAX_TOTAL_BYTES,
    SAFE_SEGMENT_RE,
    TransferService,
    _ensure_directory,
    _error,
    default_transfer_service,
)
from gw.workspaces import context as workspace_context
from gw.workspaces.model import CAPABILITY_READ_WRITE, MODE_LOCAL
from gw.workspaces.service import default_workspace_service

router = APIRouter(
    prefix="/api/transfer",
    tags=["explicit-transfer"],
    dependencies=[Depends(asset_request_scope)],
)

TRANSFER_DIRECTORY_ID = "transfer_default_01"
SAFE_NAME_RE = re.compile(r"^[^\\/:\x00-\x1f\x7f]+$")


class TransferPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TransferFileSpec(TransferPayload):
    name: str = Field(min_length=1, max_length=255)
    size_bytes: int = Field(gt=0, le=MAX_FILE_BYTES)
    sha256: str = Field(pattern=r"^[0-9a-fA-F]{64}$")


class PreflightRequest(TransferPayload):
    target_workspace_id: str = Field(min_length=1, max_length=128)
    directory_id: str = Field(default=TRANSFER_DIRECTORY_ID, min_length=1, max_length=64)
    files: list[TransferFileSpec] = Field(min_length=1, max_length=MAX_FILES)


class TransferChoice(TransferPayload):
    file_id: str = Field(pattern=r"^file_[0-9a-f]{24}$")
    action: str = Field(pattern=r"^(upload|skip|rename)$")
    new_name: str | None = Field(default=None, max_length=255)


class ConfirmRequest(TransferPayload):
    preflight_id: str = Field(pattern=r"^trf_[0-9a-f]{24}$")
    expected_version: int = Field(ge=1)
    choices: list[TransferChoice] = Field(min_length=1, max_length=MAX_FILES)


class TransferActionRequest(TransferPayload):
    generation: int = Field(ge=1)
    expected_version: int = Field(ge=1)


class RetryRequest(TransferPayload):
    expected_version: int = Field(ge=1)


def _authorize(authorization: str | None, x_user_role: str) -> str:
    context = require_edit_access(authorization, x_user_role)
    owner_id = current_asset_owner.get()
    if not owner_id:
        owner_id = asset_owner_key(context)
    return owner_id


def get_transfer_service() -> TransferService:
    return default_transfer_service


def _clean_display_name(value: str) -> str:
    name = str(value or "").strip()
    if (
        not name
        or len(name) > 255
        or name in {".", ".."}
        or not SAFE_NAME_RE.fullmatch(name)
        or PureWindowsPath(name).is_reserved()
        or name.endswith((" ", "."))
    ):
        raise _error(400, "INVALID_TRANSFER_NAME", "文件名不合法")
    return name


def _current_context(target_workspace_id: str | None = None) -> tuple[dict[str, Any], dict[str, Any]]:
    snapshot = default_workspace_service.get_snapshot()
    active_workspace_id = str(snapshot.get("active_workspace_id") or "")
    if target_workspace_id and target_workspace_id != active_workspace_id:
        raise _error(409, "WORKSPACE_NOT_ACTIVE", "请先切换到目标工作区后重新预检")
    try:
        context = default_workspace_service.build_execution_context(
            capability=CAPABILITY_READ_WRITE
        )
    except CleanroomException:
        raise
    if context.get("workspace_id") != active_workspace_id:
        raise _error(409, "WORKSPACE_CONTEXT_CHANGED", "工作区上下文已变化，请重新预检")
    return snapshot, context


def _assert_current_context(record: dict[str, Any]) -> dict[str, Any]:
    snapshot, context = _current_context(str(record.get("workspace_id") or ""))
    if (
        int(snapshot.get("revision", 0)) != int(record.get("workspace_revision", 0))
        or context.get("mapping_id") != record.get("mapping_id")
    ):
        raise _error(409, "WORKSPACE_CONTEXT_CHANGED", "工作区或共享映射已变化，请重新预检")
    return context


def _mapping_root_id(snapshot: dict[str, Any], context: dict[str, Any]) -> str | None:
    if context.get("workspace_mode") == MODE_LOCAL:
        return None
    mapping_id = str(context.get("mapping_id") or "")
    mapping = next(
        (item for item in snapshot.get("mappings", []) if item.get("mapping_id") == mapping_id),
        None,
    )
    if not mapping or not mapping.get("root_identity_id"):
        raise _error(409, "WORKSPACE_MAPPING_REQUIRED", "目标工作区缺少已核验映射")
    return str(mapping["root_identity_id"])


def _visible_name_conflict(name: str) -> dict[str, Any] | None:
    folded = name.casefold()
    snapshot = repo.readonly_registry_snapshot()
    for asset_id, item in snapshot.get("assets", {}).items():
        if asset_visible(item) and str(item.get("name") or "").casefold() == folded:
            return {"asset_id": str(asset_id), "name": str(item.get("name") or name)}
    return None


def _available_space(path: Path) -> int:
    try:
        root = path.resolve(strict=True)
        if not root.is_dir() or not os.access(root, os.W_OK | os.X_OK):
            raise _error(403, "TRANSFER_TARGET_NOT_WRITABLE", "目标工作区不可写，已拒绝传输")
        return int(shutil.disk_usage(root).free)
    except CleanroomException:
        raise
    except (OSError, RuntimeError, ValueError):
        raise _error(403, "TRANSFER_TARGET_UNAVAILABLE", "目标工作区不可访问，已拒绝传输") from None


def _validate_choices(
    files: list[dict[str, Any]],
    choices: dict[str, dict[str, str | None]],
) -> None:
    selected_names: set[str] = set()
    for item in files:
        choice = choices[item["file_id"]]
        action = str(choice.get("action") or "")
        name = item["name"] if action != "rename" else _clean_display_name(str(choice.get("new_name") or ""))
        folded = name.casefold()
        if action != "skip" and folded in selected_names:
            raise _error(409, "TRANSFER_BATCH_NAME_CONFLICT", "本批次内有重复目标名称，请重命名或跳过")
        if action != "skip":
            selected_names.add(folded)
            conflict = _visible_name_conflict(name)
            if conflict:
                raise _error(409, "TRANSFER_NAME_CONFLICT", "目标已有同名素材，请选择跳过或其他名称")


def _sha256_file(path: Path) -> tuple[int, str]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            size += len(chunk)
            digest.update(chunk)
    return size, digest.hexdigest()


def _publish_no_replace(
    source: Path,
    target: Path,
    *,
    expected_size: int,
    expected_sha256: str,
    temp_prefix: str,
) -> bool:
    """复制到目标卷的临时文件，再以硬链接原子发布，拒绝覆盖已有文件。"""
    if target.exists():
        size, digest = _sha256_file(target)
        if size == expected_size and digest == expected_sha256:
            return False
        raise _error(409, "TRANSFER_TARGET_EXISTS", "目标文件已存在且内容不同，未覆盖")
    temp_path = target.with_name(f".{temp_prefix}_{secrets.token_hex(8)}.part")
    digest = hashlib.sha256()
    size = 0
    try:
        with source.open("rb") as incoming, temp_path.open("xb") as outgoing:
            while True:
                chunk = incoming.read(1024 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                digest.update(chunk)
                outgoing.write(chunk)
            outgoing.flush()
            os.fsync(outgoing.fileno())
        if size != expected_size or digest.hexdigest() != expected_sha256:
            raise _error(422, "TRANSFER_STAGING_CORRUPT", "暂存文件与校验回执不一致")
        try:
            os.link(temp_path, target)
        except FileExistsError:
            size, existing_hash = _sha256_file(target)
            if size != expected_size or existing_hash != expected_sha256:
                raise _error(409, "TRANSFER_TARGET_EXISTS", "目标文件已存在且内容不同，未覆盖")
        except OSError:
            raise _error(409, "TRANSFER_ATOMIC_PUBLISH_UNAVAILABLE", "目标文件系统不支持安全的不覆盖发布") from None
        return True
    except CleanroomException:
        raise
    except OSError:
        raise _error(507, "TRANSFER_PUBLISH_FAILED", "目标文件发布失败，原有文件已保留") from None
    finally:
        temp_path.unlink(missing_ok=True)


def _asset_path(record: dict[str, Any], asset_id: str, name: str) -> Path:
    root = Path(str(record["target_root"]))
    directory = _ensure_directory(
        root,
        "transfers",
        "assets",
        str(record["workspace_id"]),
        str(record["directory_id"]),
    )
    suffix = Path(name).suffix.lower().lstrip(".")
    safe_ext = "".join(ch for ch in suffix if ch.isascii() and ch.isalnum())[:12]
    filename = asset_id + ("." + safe_ext if safe_ext else "")
    if not SAFE_SEGMENT_RE.fullmatch(asset_id) or not SAFE_NAME_RE.fullmatch(filename):
        raise _error(400, "INVALID_TRANSFER_PATH", "资产落盘标识不合法")
    return directory / filename


def _find_transfer_asset(raw: dict[str, Any], transfer_id: str, file_id: str) -> dict[str, Any] | None:
    for asset in raw.get("assets", {}).values():
        metadata = asset.get("metadata") or {}
        if metadata.get("transfer_id") == transfer_id and metadata.get("transfer_file_id") == file_id:
            return asset
    return None


def _commit_asset(
    *,
    transfer: dict[str, Any],
    file_item: dict[str, Any],
    owner_id: str,
    service: TransferService,
) -> str:
    transfer_id = str(transfer["transfer_id"])
    generation = int(transfer["generation"])
    file_id = str(file_item["file_id"])
    target_name = str(file_item["target_name"])
    stage = storage.data_root() / "transfers" / "staging" / transfer_id / str(generation) / f"{file_id}.part"
    holder: dict[str, str] = {}
    created_paths: list[Path] = []

    def mutate(raw: dict[str, Any]) -> None:
        existing_transfer_asset = _find_transfer_asset(raw, transfer_id, file_id)
        if existing_transfer_asset:
            if (existing_transfer_asset.get("metadata") or {}).get("workflow_owner") != owner_id:
                raise _error(403, "TRANSFER_ASSET_FORBIDDEN", "传输资产归属不匹配")
            asset_id = str(existing_transfer_asset.get("asset_id") or existing_transfer_asset.get("id") or "")
            asset = existing_transfer_asset
        else:
            for other in raw.get("assets", {}).values():
                if asset_visible(other) and str(other.get("name") or "").casefold() == target_name.casefold():
                    raise _error(409, "TRANSFER_NAME_CONFLICT", "目标已有同名素材，请重新预检并选择新名称")
            asset_id = repo._seq(raw, "ast_", raw.get("assets", {}).keys())
            asset = {}

        final_path = _asset_path(transfer, asset_id, target_name)
        created_final = _publish_no_replace(
            stage,
            final_path,
            expected_size=int(file_item["size_bytes"]),
            expected_sha256=str(file_item["sha256"]).lower(),
            temp_prefix=file_id,
        )
        if created_final:
            created_paths.append(final_path)

        _ensure_directory(storage.data_root(), "registry_uploads")
        stored_path = repo.stored_upload_path(asset_id, target_name)
        expected_alias_root = (storage.data_root() / "registry_uploads").resolve(strict=True)
        if stored_path.parent.resolve(strict=True) != expected_alias_root:
            raise _error(403, "TRANSFER_PATH_UNAVAILABLE", "素材注册路径越出受管目录")
        if stored_path.exists():
            size, digest = _sha256_file(stored_path)
            if size != int(file_item["size_bytes"]) or digest != str(file_item["sha256"]).lower():
                raise _error(409, "TRANSFER_REGISTRY_FILE_CONFLICT", "素材注册文件已存在且内容不同，未覆盖")
        else:
            try:
                os.link(final_path, stored_path)
            except OSError:
                raise _error(409, "TRANSFER_ATOMIC_PUBLISH_UNAVAILABLE", "资产目录不支持安全的不覆盖登记") from None
            created_paths.append(stored_path)

        metadata = {
            "workflow_owner": owner_id,
            "transfer_id": transfer_id,
            "transfer_file_id": file_id,
            "transfer_generation": generation,
            "workspace_id": str(transfer["workspace_id"]),
            "directory_id": str(transfer["directory_id"]),
        }
        if not existing_transfer_asset:
            asset = repo._plain_asset(
                asset_id,
                target_name,
                _kind_from_name(target_name),
                size_bytes=int(file_item["size_bytes"]),
                tags=[],
                metadata=metadata,
            )
            raw.setdefault("assets", {})[asset_id] = asset
            raw.setdefault("sequences", {})[asset_id] = True
        else:
            asset.setdefault("metadata", {}).update(metadata)

        asset["size_bytes"] = int(file_item["size_bytes"])
        asset["sha256"] = str(file_item["sha256"]).lower()
        asset["hash_mode"] = "full"
        asset["display_path"] = None
        asset["stored_name"] = stored_path.name
        asset.setdefault("metadata", {}).update(metadata)
        repo._upsert_asset_search_documents([asset])
        repo._bump(raw)
        holder["asset_id"] = asset_id
        holder["path"] = str(final_path)

    try:
        repo.state().mutate(mutate)
    except Exception:
        for path in reversed(created_paths):
            path.unlink(missing_ok=True)
        raise
    service.assign_asset_id(transfer_id, generation, file_id, holder["asset_id"], holder["path"])
    return holder["asset_id"]


@router.get("/context", status_code=status.HTTP_200_OK)
def get_transfer_context(
    authorization: str | None = Header(None),
    x_user_role: str = Header("editor", alias="X-User-Role"),
):
    _authorize(authorization, x_user_role)
    snapshot, context = _current_context()
    workspace = next(
        item for item in snapshot["workspaces"] if item["workspace_id"] == snapshot["active_workspace_id"]
    )
    return {
        "workspace_id": str(workspace["workspace_id"]),
        "workspace_mode": str(workspace["mode"]),
        "workspace_label": str(workspace["display_name"]),
        "execution_machine_id": str(context["execution_machine_id"]),
        "target_label": str(context.get("result_root_label") or workspace["display_name"]),
        "directory_id": TRANSFER_DIRECTORY_ID,
        "directory_label": "受管素材区",
        "source_label": "当前设备（本地选择）",
    }


@router.post("/preflight", status_code=status.HTTP_200_OK)
def preflight_transfer(
    payload: PreflightRequest,
    authorization: str | None = Header(None),
    x_user_role: str = Header("editor", alias="X-User-Role"),
    service: TransferService = Depends(get_transfer_service),
):
    owner_id = _authorize(authorization, x_user_role)
    if payload.directory_id != TRANSFER_DIRECTORY_ID:
        raise _error(400, "INVALID_TRANSFER_DIRECTORY", "目标目录标识无效")
    if len(payload.files) > MAX_FILES:
        raise _error(413, "TRANSFER_TOO_MANY_FILES", "单次最多传输 50 个文件")
    normalized: list[dict[str, Any]] = []
    seen_names: set[str] = set()
    total_bytes = 0
    with workspace_context.bind_execution_context(None):
        snapshot, context = _current_context(payload.target_workspace_id)
    with workspace_context.bind_execution_context(context):
        for item in payload.files:
            name = _clean_display_name(item.name)
            folded = name.casefold()
            conflict = _visible_name_conflict(name)
            has_conflict = bool(conflict) or folded in seen_names
            normalized.append(
                {
                    "name": name,
                    "size_bytes": item.size_bytes,
                    "sha256": item.sha256.lower(),
                    "has_name_conflict": has_conflict,
                    "conflict_asset_id": conflict["asset_id"] if conflict else None,
                }
            )
            seen_names.add(folded)
            total_bytes += item.size_bytes
    if total_bytes > MAX_TOTAL_BYTES:
        raise _error(413, "TRANSFER_TOO_LARGE", "单次传输超过 32 GiB 上限")
    data_root = storage.data_root().resolve(strict=True)
    available = _available_space(data_root)
    required_space = total_bytes * 2
    if available < required_space:
        raise _error(
            409,
            "TRANSFER_SPACE_INSUFFICIENT",
            "暂存与受管落点的可用空间不足",
            required_bytes=required_space,
            available_bytes=available,
        )
    mapping_root_id = _mapping_root_id(snapshot, context)
    return service.create_preflight(
        owner_id=owner_id,
        context=context,
        workspace_revision=int(snapshot["revision"]),
        mapping_root_id=mapping_root_id,
        target_root=str(data_root),
        directory_id=payload.directory_id,
        file_specs=normalized,
        target_label=str(context.get("result_root_label") or "当前工作区"),
        estimated_bytes=total_bytes,
        staging_available_bytes=available,
        target_available_bytes=available,
    )


@router.post("/jobs", status_code=status.HTTP_201_CREATED)
def confirm_transfer(
    payload: ConfirmRequest,
    authorization: str | None = Header(None),
    x_user_role: str = Header("editor", alias="X-User-Role"),
    idempotency_key: str | None = Header(None, alias="Idempotency-Key"),
    service: TransferService = Depends(get_transfer_service),
):
    owner_id = _authorize(authorization, x_user_role)
    raw_key = str(idempotency_key or "").strip()
    if not raw_key or len(raw_key) > 200:
        raise _error(400, "IDEMPOTENCY_KEY_REQUIRED", "确认传输必须提供 Idempotency-Key")
    preflight = service.get(payload.preflight_id, owner_id=owner_id)
    context = _assert_current_context(preflight)
    choices = {item.file_id: {"action": item.action, "new_name": item.new_name} for item in payload.choices}
    if len(choices) != len(payload.choices) or set(choices) != {item["file_id"] for item in preflight["files"]}:
        raise _error(400, "INVALID_TRANSFER_CHOICES", "必须逐项确认传输清单")
    with workspace_context.bind_execution_context(context):
        _validate_choices(preflight["files"], choices)
    total_bytes = sum(
        int(item["size_bytes"])
        for item in preflight["files"]
        if choices[item["file_id"]]["action"] != "skip"
    )
    available = _available_space(storage.data_root())
    if available < total_bytes * 2:
        raise _error(409, "TRANSFER_SPACE_INSUFFICIENT", "确认前空间复核失败，请释放空间后重新预检")
    canonical = [
        {"file_id": file_id, "action": value["action"], "new_name": value.get("new_name")}
        for file_id, value in sorted(choices.items())
    ]
    request_hash = hashlib.sha256(
        (payload.preflight_id + "\0" + str(preflight["workspace_id"]) + "\0" + repr(canonical)).encode("utf-8")
    ).hexdigest()
    key_hash = hashlib.sha256(
        (owner_id + "\0" + str(preflight["workspace_id"]) + "\0" + raw_key).encode("utf-8")
    ).hexdigest()
    return service.confirm(
        payload.preflight_id,
        owner_id=owner_id,
        expected_version=payload.expected_version,
        key_hash=key_hash,
        request_hash=request_hash,
        choices=choices,
    )


@router.put("/jobs/{transfer_id}/files/{file_id}", status_code=status.HTTP_200_OK)
async def upload_transfer_file(
    transfer_id: str,
    file_id: str,
    request: Request,
    generation: int = Query(ge=1),
    expected_version: int = Query(ge=1),
    authorization: str | None = Header(None),
    x_user_role: str = Header("editor", alias="X-User-Role"),
    x_content_sha256: str | None = Header(None, alias="X-Content-SHA256"),
    service: TransferService = Depends(get_transfer_service),
):
    owner_id = _authorize(authorization, x_user_role)
    public = service.get(transfer_id, owner_id=owner_id)
    context = _assert_current_context(public)
    expected_file = next((item for item in public["files"] if item["file_id"] == file_id), None)
    if not expected_file:
        raise _error(404, "TRANSFER_FILE_NOT_FOUND", "传输文件不存在")
    if str(x_content_sha256 or "").lower() != str(expected_file["sha256"]).lower():
        raise _error(400, "TRANSFER_HASH_HEADER_MISMATCH", "上传哈希与预检回执不一致")
    content_length = request.headers.get("content-length")
    if content_length and content_length.isdigit() and int(content_length) != int(expected_file["size_bytes"]):
        raise _error(400, "TRANSFER_SIZE_HEADER_MISMATCH", "上传长度与预检回执不一致")
    with workspace_context.bind_execution_context(context):
        _, stage_path, already_verified = service.begin_upload(
            transfer_id,
            file_id,
            owner_id=owner_id,
            generation=generation,
            expected_version=expected_version,
        )
    if already_verified:
        return service.get(transfer_id, owner_id=owner_id)
    digest = hashlib.sha256()
    received = 0
    next_persist = 512 * 1024
    try:
        with stage_path.open("xb") as handle:
            async for chunk in request.stream():
                if not chunk:
                    continue
                if not service.upload_is_active(transfer_id, generation, file_id):
                    raise _error(409, "TRANSFER_GENERATION_STALE", "传输已取消或代际已变化")
                received += len(chunk)
                if received > int(expected_file["size_bytes"]):
                    raise _error(413, "TRANSFER_SIZE_EXCEEDED", "上传内容超过预检大小")
                handle.write(chunk)
                digest.update(chunk)
                if received >= next_persist:
                    service.record_progress(transfer_id, generation, file_id, received)
                    next_persist = received + 512 * 1024
            handle.flush()
            os.fsync(handle.fileno())
        if not service.upload_is_active(transfer_id, generation, file_id):
            raise _error(409, "TRANSFER_GENERATION_STALE", "传输已取消或代际已变化")
        result, valid = service.finish_upload(
            transfer_id,
            generation,
            file_id,
            actual_size=received,
            actual_sha256=digest.hexdigest(),
        )
        if not valid:
            stage_path.unlink(missing_ok=True)
            raise _error(422, "TRANSFER_CHECKSUM_MISMATCH", "上传内容与预检大小或 SHA-256 不一致")
        return result
    except Exception as exc:
        service.abort_upload(
            transfer_id,
            generation,
            file_id,
            message=str(getattr(exc, "message", "") or "上传中断，未发布该文件"),
        )
        raise


@router.post("/jobs/{transfer_id}/commit", status_code=status.HTTP_200_OK)
def commit_transfer(
    transfer_id: str,
    payload: TransferActionRequest,
    authorization: str | None = Header(None),
    x_user_role: str = Header("editor", alias="X-User-Role"),
    service: TransferService = Depends(get_transfer_service),
):
    owner_id = _authorize(authorization, x_user_role)
    public = service.get(transfer_id, owner_id=owner_id)
    context = _assert_current_context(public)
    _available_space(storage.data_root())
    with workspace_context.bind_execution_context(context):
        snapshot = default_workspace_service.get_snapshot()
        _mapping_root_id(snapshot, context)
        transfer = service.start_commit(
            transfer_id,
            owner_id=owner_id,
            generation=payload.generation,
            expected_version=payload.expected_version,
        )
        try:
            for item in transfer["files"]:
                if item.get("status") == "skipped" or item.get("status") == "committed":
                    continue
                _commit_asset(
                    transfer=transfer,
                    file_item=item,
                    owner_id=owner_id,
                    service=service,
                )
                service.mark_file_committed(transfer_id, payload.generation, str(item["file_id"]))
            return service.finish_commit(transfer_id, payload.generation)
        except Exception as exc:
            service.fail_commit(
                transfer_id,
                payload.generation,
                str(getattr(exc, "message", "") or "提交失败，已保留已有素材"),
            )
            raise


@router.get("/jobs/{transfer_id}", status_code=status.HTTP_200_OK)
def get_transfer(
    transfer_id: str,
    authorization: str | None = Header(None),
    x_user_role: str = Header("editor", alias="X-User-Role"),
    service: TransferService = Depends(get_transfer_service),
):
    owner_id = _authorize(authorization, x_user_role)
    return service.get(transfer_id, owner_id=owner_id)


@router.post("/jobs/{transfer_id}/cancel", status_code=status.HTTP_200_OK)
def cancel_transfer(
    transfer_id: str,
    payload: TransferActionRequest,
    authorization: str | None = Header(None),
    x_user_role: str = Header("editor", alias="X-User-Role"),
    service: TransferService = Depends(get_transfer_service),
):
    owner_id = _authorize(authorization, x_user_role)
    return service.cancel(
        transfer_id,
        owner_id=owner_id,
        generation=payload.generation,
        expected_version=payload.expected_version,
    )


@router.post("/jobs/{transfer_id}/retry", status_code=status.HTTP_200_OK)
def retry_transfer(
    transfer_id: str,
    payload: RetryRequest,
    authorization: str | None = Header(None),
    x_user_role: str = Header("editor", alias="X-User-Role"),
    service: TransferService = Depends(get_transfer_service),
):
    owner_id = _authorize(authorization, x_user_role)
    record = service.get(transfer_id, owner_id=owner_id)
    context = _assert_current_context(record)
    _available_space(storage.data_root())
    return service.retry(transfer_id, owner_id=owner_id, expected_version=payload.expected_version)


def _kind_from_name(name: str) -> str:
    suffix = Path(name).suffix.lower()
    if suffix == ".cube":
        return "lut"
    if suffix in {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".svg", ".tif", ".tiff"}:
        return "image"
    if suffix in {".mp4", ".mov", ".mkv", ".avi", ".webm", ".m4v", ".flv", ".wmv"}:
        return "video"
    if suffix in {".mp3", ".wav", ".flac", ".aac", ".ogg", ".m4a"}:
        return "audio"
    if suffix in {".txt", ".md", ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".csv"}:
        return "document"
    return "unknown"
