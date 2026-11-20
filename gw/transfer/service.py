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

"""QIN-629 受管文件传输回执、代际与并发状态。"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import secrets
import shutil
import stat
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from gw.core import storage
from gw.core.errors import CleanroomException

TRANSFER_ID_RE = re.compile(r"^trf_[0-9a-f]{24}$")
FILE_ID_RE = re.compile(r"^file_[0-9a-f]{24}$")
SAFE_SEGMENT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
MAX_FILES = 50
MAX_FILE_BYTES = 8 * 1024 * 1024 * 1024
MAX_TOTAL_BYTES = 32 * 1024 * 1024 * 1024
PROGRESS_PERSIST_BYTES = 512 * 1024


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _error(status_code: int, code: str, message: str, **extra: Any) -> CleanroomException:
    return CleanroomException(
        status_code=status_code,
        code=code,
        message=message,
        extra=extra,
        expose_extra_fields=set(extra),
    )


def _is_link(path: Path) -> bool:
    try:
        info = path.lstat()
    except FileNotFoundError:
        return False
    except OSError:
        raise _error(403, "TRANSFER_PATH_UNAVAILABLE", "传输目录无法安全核验") from None
    return stat.S_ISLNK(info.st_mode) or bool(
        getattr(info, "st_file_attributes", 0) & 0x400
    )


def _ensure_directory(root: Path, *segments: str) -> Path:
    """在已核验根目录下逐层创建目录，拒绝符号链接与 Windows 重解析点。"""
    try:
        boundary = Path(root).expanduser().resolve(strict=True)
        if _is_link(Path(root).expanduser()):
            raise _error(403, "TRANSFER_PATH_UNAVAILABLE", "传输根目录不允许链接")
        if not boundary.is_dir():
            raise _error(403, "TRANSFER_PATH_UNAVAILABLE", "传输根目录不可用")
        current = boundary
        for segment in segments:
            if not SAFE_SEGMENT_RE.fullmatch(segment):
                raise _error(400, "INVALID_TRANSFER_PATH", "传输目录标识不合法")
            current = current / segment
            try:
                info = current.lstat()
            except FileNotFoundError:
                try:
                    current.mkdir()
                except FileExistsError:
                    pass
                info = current.lstat()
            if _is_link(current) or not stat.S_ISDIR(info.st_mode):
                raise _error(403, "TRANSFER_PATH_UNAVAILABLE", "传输目录包含不安全路径组件")
            if not current.resolve(strict=True).is_relative_to(boundary):
                raise _error(403, "TRANSFER_PATH_UNAVAILABLE", "传输目录越出受管根目录")
        return current
    except CleanroomException:
        raise
    except (OSError, RuntimeError, ValueError):
        raise _error(403, "TRANSFER_PATH_UNAVAILABLE", "传输目录不可访问") from None


def _remove_generation(path: Path) -> bool:
    """仅清理由本服务生成的单个传输代目录。"""
    try:
        if _is_link(path):
            path.unlink(missing_ok=True)
        elif path.exists():
            shutil.rmtree(path)
        return True
    except OSError:
        return False


class TransferService:
    """单进程原子回执服务；状态变更全部经版本与代际校验。"""

    def __init__(self) -> None:
        self._active_uploads: set[tuple[str, int, str]] = set()
        self._active_commits: set[tuple[str, int]] = set()
        self._activity_lock = threading.RLock()

    @staticmethod
    def new_transfer_id() -> str:
        return "trf_" + secrets.token_hex(12)

    @staticmethod
    def new_file_id() -> str:
        return "file_" + secrets.token_hex(12)

    @staticmethod
    def _root() -> Path:
        return storage.data_root()

    def _records_dir(self, *, create: bool) -> Path:
        root = self._root()
        if create:
            return _ensure_directory(root, "transfers", "records")
        path = root / "transfers" / "records"
        if _is_link(root / "transfers") or _is_link(path):
            raise _error(403, "TRANSFER_PATH_UNAVAILABLE", "传输回执目录不允许链接")
        return path

    def _record_path(self, transfer_id: str, *, create: bool = False) -> Path:
        if not TRANSFER_ID_RE.fullmatch(transfer_id):
            raise _error(404, "TRANSFER_NOT_FOUND", "传输记录不存在")
        return self._records_dir(create=create) / f"{transfer_id}.json"

    def _stage_dir(self, transfer_id: str, generation: int, *, create: bool) -> Path:
        if not TRANSFER_ID_RE.fullmatch(transfer_id) or generation < 1:
            raise _error(400, "INVALID_TRANSFER_PATH", "传输代际不合法")
        root = self._root()
        if create:
            transfer_dir = _ensure_directory(root, "transfers", "staging", transfer_id)
            return _ensure_directory(transfer_dir, str(generation))
        path = root / "transfers" / "staging" / transfer_id / str(generation)
        if any(_is_link(part) for part in (root / "transfers", root / "transfers" / "staging", path.parent, path)):
            raise _error(403, "TRANSFER_PATH_UNAVAILABLE", "暂存目录不允许链接")
        return path

    def _load_locked(self, transfer_id: str) -> dict[str, Any]:
        path = self._record_path(transfer_id)
        try:
            with path.open("r", encoding="utf-8") as handle:
                record = json.load(handle)
        except FileNotFoundError:
            raise _error(404, "TRANSFER_NOT_FOUND", "传输记录不存在") from None
        except (OSError, json.JSONDecodeError):
            raise _error(500, "TRANSFER_RECORD_UNREADABLE", "传输回执损坏，已拒绝继续写入") from None
        if not isinstance(record, dict) or record.get("transfer_id") != transfer_id:
            raise _error(500, "TRANSFER_RECORD_UNREADABLE", "传输回执不完整，已拒绝继续写入")
        return record

    def _save_locked(self, record: dict[str, Any]) -> None:
        path = self._record_path(str(record.get("transfer_id", "")), create=True)
        temporary = path.with_name(f".{path.name}.{secrets.token_hex(8)}.part")
        try:
            with temporary.open("x", encoding="utf-8", newline="\n") as handle:
                json.dump(record, handle, ensure_ascii=False, separators=(",", ":"))
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        except OSError:
            temporary.unlink(missing_ok=True)
            raise _error(500, "TRANSFER_RECORD_WRITE_FAILED", "传输回执写入失败") from None

    @staticmethod
    def _check_owner(record: dict[str, Any], owner_id: str) -> None:
        if not owner_id or record.get("owner_id") != owner_id:
            raise _error(404, "TRANSFER_NOT_FOUND", "传输记录不存在")

    @staticmethod
    def _check_version(record: dict[str, Any], expected_version: int) -> None:
        current = int(record.get("version", 0))
        if current != expected_version:
            raise _error(
                409,
                "VERSION_CONFLICT",
                "传输状态已变化，请重新读取后再操作",
                expected_version=expected_version,
                current_version=current,
            )

    @staticmethod
    def _public(record: dict[str, Any]) -> dict[str, Any]:
        public = copy.deepcopy(record)
        for key in (
            "owner_id",
            "target_root",
            "result_root",
            "mapping_root_id",
            "idempotency_key_hash",
            "idempotency_request_hash",
        ):
            public.pop(key, None)
        files = public.get("files", [])
        public["total_bytes"] = sum(int(item.get("size_bytes", 0)) for item in files)
        public["received_bytes"] = sum(int(item.get("received_bytes", 0)) for item in files)
        total = public["total_bytes"]
        public["progress_percent"] = (
            round(min(100, public["received_bytes"] * 100 / total), 1) if total else 100
        )
        public["cleanup_policy"] = "取消或失败只清理未发布暂存；已发布素材与原文件保留"
        return public

    def create_preflight(
        self,
        *,
        owner_id: str,
        context: dict[str, Any],
        workspace_revision: int,
        mapping_root_id: str | None,
        target_root: str,
        directory_id: str,
        file_specs: list[dict[str, Any]],
        target_label: str,
        estimated_bytes: int,
        staging_available_bytes: int,
        target_available_bytes: int,
    ) -> dict[str, Any]:
        if not owner_id:
            raise _error(401, "UNAUTHORIZED", "会话失效，请重新登录")
        transfer_id = self.new_transfer_id()
        files = []
        for item in file_specs:
            files.append(
                {
                    "file_id": self.new_file_id(),
                    "name": item["name"],
                    "size_bytes": int(item["size_bytes"]),
                    "sha256": item["sha256"],
                    "has_name_conflict": bool(item.get("has_name_conflict")),
                    "conflict_asset_id": item.get("conflict_asset_id"),
                    "action": None,
                    "target_name": None,
                    "status": "pending",
                    "received_bytes": 0,
                    "asset_id": None,
                    "published_path": None,
                    "error": None,
                }
            )
        record = {
            "transfer_id": transfer_id,
            "owner_id": owner_id,
            "workspace_id": str(context["workspace_id"]),
            "workspace_mode": str(context["workspace_mode"]),
            "workspace_revision": int(workspace_revision),
            "mapping_id": context.get("mapping_id"),
            "mapping_root_id": mapping_root_id,
            "execution_machine_id": str(context["execution_machine_id"]),
            "target_label": target_label,
            "target_root": str(Path(target_root).expanduser().resolve(strict=True)),
            "result_root": str(Path(context["result_root"]).expanduser().resolve(strict=True)),
            "directory_id": directory_id,
            "source_label": "当前设备（本地选择）",
            "generation": 1,
            "version": 1,
            "status": "awaiting_confirmation",
            "estimated_bytes": estimated_bytes,
            "staging_available_bytes": staging_available_bytes,
            "target_available_bytes": target_available_bytes,
            "files": files,
            "error": None,
            "created_at": _now(),
            "updated_at": _now(),
            "idempotency_key_hash": None,
            "idempotency_request_hash": None,
        }
        with storage.namespace_lock("transfers"):
            self._save_locked(record)
        return self._public(record)

    def confirm(
        self,
        transfer_id: str,
        *,
        owner_id: str,
        expected_version: int,
        key_hash: str,
        request_hash: str,
        choices: dict[str, dict[str, str | None]],
    ) -> dict[str, Any]:
        with storage.namespace_lock("transfers"):
            records_dir = self._records_dir(create=False)
            if records_dir.exists():
                for path in records_dir.glob("trf_*.json"):
                    try:
                        existing = json.loads(path.read_text(encoding="utf-8"))
                    except (OSError, json.JSONDecodeError):
                        continue
                    if (
                        isinstance(existing, dict)
                        and existing.get("owner_id") == owner_id
                        and existing.get("idempotency_key_hash") == key_hash
                    ):
                        if existing.get("idempotency_request_hash") != request_hash:
                            raise _error(409, "IDEMPOTENCY_CONFLICT", "幂等键已用于不同的传输清单")
                        return self._public(existing)
            record = self._load_locked(transfer_id)
            self._check_owner(record, owner_id)
            self._check_version(record, expected_version)
            if record.get("status") != "awaiting_confirmation":
                raise _error(409, "TRANSFER_STATE_CONFLICT", "该传输已确认或已结束")
            expected_ids = {item["file_id"] for item in record["files"]}
            if set(choices) != expected_ids:
                raise _error(400, "INVALID_TRANSFER_CHOICES", "必须逐项确认传输清单")
            for item in record["files"]:
                choice = choices[item["file_id"]]
                action = choice.get("action")
                new_name = choice.get("new_name")
                if action not in {"upload", "skip", "rename"}:
                    raise _error(400, "INVALID_TRANSFER_CHOICES", "传输处理方式无效")
                if item["has_name_conflict"] and action == "upload":
                    raise _error(409, "TRANSFER_NAME_CONFLICT", "同名素材必须选择跳过或重命名")
                if action == "rename":
                    if not isinstance(new_name, str) or not new_name.strip():
                        raise _error(400, "INVALID_TRANSFER_CHOICES", "重命名时必须提供新名称")
                    item["target_name"] = new_name.strip()
                else:
                    item["target_name"] = item["name"]
                item["action"] = action
                if action == "skip":
                    item["status"] = "skipped"
            record["idempotency_key_hash"] = key_hash
            record["idempotency_request_hash"] = request_hash
            record["status"] = "uploading" if any(item["status"] == "pending" for item in record["files"]) else "ready"
            record["version"] += 1
            record["updated_at"] = _now()
            self._save_locked(record)
            return self._public(record)

    def begin_upload(
        self,
        transfer_id: str,
        file_id: str,
        *,
        owner_id: str,
        generation: int,
        expected_version: int,
    ) -> tuple[dict[str, Any], Path, bool]:
        with storage.namespace_lock("transfers"):
            record = self._load_locked(transfer_id)
            self._check_owner(record, owner_id)
            self._check_version(record, expected_version)
            if record.get("generation") != generation or record.get("status") != "uploading":
                raise _error(409, "TRANSFER_GENERATION_STALE", "傳輸代際已失效")
            item = next((entry for entry in record["files"] if entry["file_id"] == file_id), None)
            if item is None:
                raise _error(404, "TRANSFER_FILE_NOT_FOUND", "傳輸文件不存在")
            if item.get("status") == "verified":
                return self._public(record), self._stage_dir(transfer_id, generation, create=False) / f"{file_id}.part", True
            if item.get("status") != "pending":
                raise _error(409, "TRANSFER_FILE_STATE_CONFLICT", "该文件当前不能上传")
            active_for_transfer = any(
                active_id == transfer_id and active_generation == generation
                for active_id, active_generation, _ in self._active_uploads
            )
            if active_for_transfer:
                raise _error(409, "TRANSFER_UPLOAD_IN_PROGRESS", "请等待当前文件上传完成")
            stage_dir = self._stage_dir(transfer_id, generation, create=True)
            stage_path = stage_dir / f"{file_id}.part"
            if stage_path.exists() or _is_link(stage_path):
                stage_path.unlink(missing_ok=True)
            item["status"] = "receiving"
            item["received_bytes"] = 0
            item["error"] = None
            record["updated_at"] = _now()
            self._save_locked(record)
            with self._activity_lock:
                self._active_uploads.add((transfer_id, generation, file_id))
            return self._public(record), stage_path, False

    def upload_is_active(self, transfer_id: str, generation: int, file_id: str) -> bool:
        with storage.namespace_lock("transfers"):
            try:
                record = self._load_locked(transfer_id)
            except CleanroomException:
                return False
            item = next((entry for entry in record["files"] if entry["file_id"] == file_id), None)
            return bool(
                record.get("generation") == generation
                and record.get("status") == "uploading"
                and item is not None
                and item.get("status") == "receiving"
            )

    def record_progress(self, transfer_id: str, generation: int, file_id: str, received: int) -> None:
        with storage.namespace_lock("transfers"):
            record = self._load_locked(transfer_id)
            if record.get("generation") != generation or record.get("status") != "uploading":
                return
            item = next((entry for entry in record["files"] if entry["file_id"] == file_id), None)
            if item is None or item.get("status") != "receiving":
                return
            item["received_bytes"] = received
            record["updated_at"] = _now()
            self._save_locked(record)

    def finish_upload(
        self,
        transfer_id: str,
        generation: int,
        file_id: str,
        *,
        actual_size: int,
        actual_sha256: str,
    ) -> tuple[dict[str, Any], bool]:
        with storage.namespace_lock("transfers"):
            record = self._load_locked(transfer_id)
            with self._activity_lock:
                self._active_uploads.discard((transfer_id, generation, file_id))
            if record.get("generation") != generation or record.get("status") != "uploading":
                return self._public(record), False
            item = next((entry for entry in record["files"] if entry["file_id"] == file_id), None)
            if item is None or item.get("status") != "receiving":
                return self._public(record), False
            valid = (
                actual_size == int(item["size_bytes"])
                and actual_sha256.lower() == str(item["sha256"]).lower()
            )
            item["received_bytes"] = actual_size if valid else 0
            if not valid:
                item["status"] = "failed"
                item["error"] = "上传内容与预检大小或 SHA-256 不一致"
                record["status"] = "failed"
                record["error"] = item["error"]
            else:
                item["status"] = "verified"
                item["error"] = None
                if all(entry["status"] in {"verified", "skipped"} for entry in record["files"]):
                    record["status"] = "ready"
            record["version"] += 1
            record["updated_at"] = _now()
            self._save_locked(record)
            return self._public(record), valid

    def abort_upload(
        self,
        transfer_id: str,
        generation: int,
        file_id: str,
        *,
        message: str,
    ) -> dict[str, Any] | None:
        with storage.namespace_lock("transfers"):
            with self._activity_lock:
                self._active_uploads.discard((transfer_id, generation, file_id))
            try:
                record = self._load_locked(transfer_id)
            except CleanroomException:
                return None
            item = next((entry for entry in record["files"] if entry["file_id"] == file_id), None)
            if record.get("generation") == generation and record.get("status") == "uploading" and item and item.get("status") == "receiving":
                item["status"] = "failed"
                item["received_bytes"] = 0
                item["error"] = message
                record["status"] = "failed"
                record["error"] = message
                record["version"] += 1
                record["updated_at"] = _now()
                self._save_locked(record)
            stage_dir = self._stage_dir(transfer_id, generation, create=False)
            if not any(key[0] == transfer_id and key[1] == generation for key in self._active_uploads):
                _remove_generation(stage_dir)
            return self._public(record)

    def start_commit(
        self,
        transfer_id: str,
        *,
        owner_id: str,
        generation: int,
        expected_version: int,
    ) -> dict[str, Any]:
        with storage.namespace_lock("transfers"):
            record = self._load_locked(transfer_id)
            self._check_owner(record, owner_id)
            self._check_version(record, expected_version)
            if record.get("generation") != generation:
                raise _error(409, "TRANSFER_GENERATION_STALE", "传输代际已失效")
            if record.get("status") != "ready":
                raise _error(409, "TRANSFER_NOT_READY", "全部文件校验通过后才能提交")
            record["status"] = "committing"
            record["version"] += 1
            record["updated_at"] = _now()
            self._save_locked(record)
            with self._activity_lock:
                self._active_commits.add((transfer_id, generation))
            return copy.deepcopy(record)

    def assign_asset_id(
        self, transfer_id: str, generation: int, file_id: str, asset_id: str, published_path: str
    ) -> None:
        with storage.namespace_lock("transfers"):
            record = self._load_locked(transfer_id)
            if record.get("generation") != generation or record.get("status") != "committing":
                raise _error(409, "TRANSFER_GENERATION_STALE", "传输提交代际已失效")
            item = next((entry for entry in record["files"] if entry["file_id"] == file_id), None)
            if item is None:
                raise _error(404, "TRANSFER_FILE_NOT_FOUND", "传输文件不存在")
            item["asset_id"] = asset_id
            item["published_path"] = published_path
            self._save_locked(record)

    def mark_file_committed(self, transfer_id: str, generation: int, file_id: str) -> None:
        with storage.namespace_lock("transfers"):
            record = self._load_locked(transfer_id)
            if record.get("generation") != generation or record.get("status") != "committing":
                raise _error(409, "TRANSFER_GENERATION_STALE", "传输提交代际已失效")
            item = next((entry for entry in record["files"] if entry["file_id"] == file_id), None)
            if item is None:
                raise _error(404, "TRANSFER_FILE_NOT_FOUND", "传输文件不存在")
            item["status"] = "committed"
            item["received_bytes"] = int(item["size_bytes"])
            self._save_locked(record)

    def finish_commit(self, transfer_id: str, generation: int) -> dict[str, Any]:
        with storage.namespace_lock("transfers"):
            record = self._load_locked(transfer_id)
            if record.get("generation") != generation or record.get("status") != "committing":
                raise _error(409, "TRANSFER_GENERATION_STALE", "传输提交代际已失效")
            if any(item.get("status") not in {"committed", "skipped"} for item in record["files"]):
                raise _error(409, "TRANSFER_COMMIT_INCOMPLETE", "仍有文件未完成提交")
            record["status"] = "completed"
            record["version"] += 1
            record["updated_at"] = _now()
            record["error"] = None
            self._save_locked(record)
            with self._activity_lock:
                self._active_commits.discard((transfer_id, generation))
        self._remove_generation(transfer_id, generation)
        with storage.namespace_lock("transfers"):
            return self._public(self._load_locked(transfer_id))

    def fail_commit(self, transfer_id: str, generation: int, message: str) -> dict[str, Any]:
        with storage.namespace_lock("transfers"):
            with self._activity_lock:
                self._active_commits.discard((transfer_id, generation))
            record = self._load_locked(transfer_id)
            if record.get("generation") == generation and record.get("status") == "committing":
                record["status"] = "failed"
                record["error"] = message
                record["version"] += 1
                record["updated_at"] = _now()
                self._save_locked(record)
            return self._public(record)

    def cancel(
        self,
        transfer_id: str,
        *,
        owner_id: str,
        generation: int,
        expected_version: int,
    ) -> dict[str, Any]:
        with storage.namespace_lock("transfers"):
            record = self._load_locked(transfer_id)
            self._check_owner(record, owner_id)
            self._check_version(record, expected_version)
            if record.get("generation") != generation:
                raise _error(409, "TRANSFER_GENERATION_STALE", "传输代际已失效")
            if record.get("status") in {"committing", "completed"}:
                raise _error(409, "TRANSFER_CANCEL_TOO_LATE", "传输已进入提交阶段，不能取消")
            if record.get("status") == "cancelled":
                return self._public(record)
            old_generation = generation
            record["status"] = "cancelled"
            record["generation"] = old_generation + 1
            record["version"] += 1
            record["updated_at"] = _now()
            record["error"] = None
            self._save_locked(record)
            with self._activity_lock:
                active = any(key[0] == transfer_id and key[1] == old_generation for key in self._active_uploads)
            result = self._public(record)
            result["cleanup_pending"] = active
        if not active:
            self._remove_generation(transfer_id, old_generation)
        return result

    def retry(
        self,
        transfer_id: str,
        *,
        owner_id: str,
        expected_version: int,
    ) -> dict[str, Any]:
        with storage.namespace_lock("transfers"):
            record = self._load_locked(transfer_id)
            self._check_owner(record, owner_id)
            self._check_version(record, expected_version)
            if record.get("status") not in {"failed", "cancelled"}:
                raise _error(409, "TRANSFER_NOT_RETRYABLE", "只有失败或已取消的传输可以重试")
            old_generation = int(record["generation"])
            record["generation"] = old_generation + 1
            for item in record["files"]:
                if item.get("status") in {"committed", "skipped"}:
                    continue
                item["status"] = "pending"
                item["received_bytes"] = 0
                item["error"] = None
            record["status"] = "uploading" if any(item["status"] == "pending" for item in record["files"]) else "ready"
            record["error"] = None
            record["version"] += 1
            record["updated_at"] = _now()
            self._save_locked(record)
        self._remove_generation(transfer_id, old_generation)
        with storage.namespace_lock("transfers"):
            return self._public(self._load_locked(transfer_id))

    def get(self, transfer_id: str, *, owner_id: str) -> dict[str, Any]:
        stale_generation: int | None = None
        with storage.namespace_lock("transfers"):
            record = self._load_locked(transfer_id)
            self._check_owner(record, owner_id)
            generation = int(record.get("generation", 1))
            with self._activity_lock:
                active_upload = any(key[0] == transfer_id and key[1] == generation for key in self._active_uploads)
                active_commit = (transfer_id, generation) in self._active_commits
            if record.get("status") == "uploading" and not active_upload:
                if any(item.get("status") == "receiving" for item in record["files"]):
                    stale_generation = generation
                    for item in record["files"]:
                        if item.get("status") == "receiving":
                            item["status"] = "failed"
                            item["received_bytes"] = 0
                            item["error"] = "进程中断，未发布的暂存文件已清理"
                    record["status"] = "failed"
                    record["error"] = "进程中断，传输未完成"
                    record["version"] += 1
                    record["updated_at"] = _now()
                    self._save_locked(record)
            elif record.get("status") == "committing" and not active_commit:
                record["status"] = "failed"
                record["error"] = "进程中断，提交结果待重试核对"
                record["version"] += 1
                record["updated_at"] = _now()
                self._save_locked(record)
            result = self._public(record)
        if stale_generation is not None:
            self._remove_generation(transfer_id, stale_generation)
        return result

    def _remove_generation(self, transfer_id: str, generation: int) -> bool:
        try:
            stage_dir = self._stage_dir(transfer_id, generation, create=False)
        except CleanroomException:
            return False
        return _remove_generation(stage_dir)


default_transfer_service = TransferService()
