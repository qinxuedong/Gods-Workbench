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
TASK_TRANSFER_ID_RE = re.compile(r"^ttr_[0-9a-f]{24}$")
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
    def new_task_transfer_id() -> str:
        return "ttr_" + secrets.token_hex(12)

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

    def _task_records_dir(self, *, create: bool) -> Path:
        root = self._root()
        if create:
            return _ensure_directory(root, "transfers", "task_records")
        path = root / "transfers" / "task_records"
        if _is_link(root / "transfers") or _is_link(path):
            raise _error(403, "TRANSFER_PATH_UNAVAILABLE", "任务传输回执目录不允许链接")
        return path

    def _task_record_path(self, preflight_id: str, *, create: bool = False) -> Path:
        if not TASK_TRANSFER_ID_RE.fullmatch(preflight_id):
            raise _error(404, "TASK_TRANSFER_NOT_FOUND", "任务传输回执不存在")
        return self._task_records_dir(create=create) / f"{preflight_id}.json"

    def _load_task_transfer_locked(self, preflight_id: str) -> dict[str, Any]:
        path = self._task_record_path(preflight_id)
        try:
            with path.open("r", encoding="utf-8") as handle:
                record = json.load(handle)
        except FileNotFoundError:
            raise _error(404, "TASK_TRANSFER_NOT_FOUND", "任务传输回执不存在") from None
        except (OSError, json.JSONDecodeError):
            raise _error(500, "TASK_TRANSFER_RECORD_UNREADABLE", "任务传输回执损坏") from None
        if not isinstance(record, dict) or record.get("preflight_id") != preflight_id:
            raise _error(500, "TASK_TRANSFER_RECORD_UNREADABLE", "任务传输回执不完整")
        return record

    def _save_task_transfer_locked(self, record: dict[str, Any]) -> None:
        path = self._task_record_path(str(record.get("preflight_id") or ""), create=True)
        temporary = path.with_name(f".{path.name}.{secrets.token_hex(8)}.part")
        try:
            with temporary.open("x", encoding="utf-8", newline="\n") as handle:
                json.dump(record, handle, ensure_ascii=False, separators=(",", ":"))
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        except OSError:
            temporary.unlink(missing_ok=True)
            raise _error(500, "TASK_TRANSFER_RECORD_WRITE_FAILED", "任务传输回执写入失败") from None

    @staticmethod
    def _public_task_transfer(record: dict[str, Any]) -> dict[str, Any]:
        public = copy.deepcopy(record)
        for key in ("owner_id", "result_root", "result_prefix", "mapping_root_id"):
            public.pop(key, None)
        public["input_asset_count"] = len(public.get("input_assets", []))
        return public

    @staticmethod
    def _task_bindings(value: list[dict[str, Any]]) -> list[dict[str, str]]:
        try:
            bindings = [
                {
                    "node_id": str(item["node_id"]),
                    "field": str(item["field"]),
                    "asset_id": str(item["asset_id"]),
                }
                for item in value
            ]
        except (KeyError, TypeError):
            raise _error(400, "INVALID_TASK_BINDINGS", "工作流素材绑定格式无效") from None
        if any(not item["node_id"] or not item["field"] or not item["asset_id"] for item in bindings):
            raise _error(400, "INVALID_TASK_BINDINGS", "工作流素材绑定不完整")
        return sorted(bindings, key=lambda item: (item["node_id"], item["field"], item["asset_id"]))

    @staticmethod
    def _task_input_assets(value: list[dict[str, Any]]) -> list[dict[str, Any]]:
        try:
            assets = []
            for item in value:
                bindings = [
                    {"node_id": str(binding["node_id"]), "field": str(binding["field"])}
                    for binding in item.get("bindings", [])
                ]
                assets.append(
                    {
                        "asset_id": str(item["asset_id"]),
                        "filename": str(item["filename"]),
                        "size_bytes": int(item["size_bytes"]),
                        "content_type": str(item["content_type"]),
                        "transfer_mode": str(item["transfer_mode"]),
                        "executor_name": item.get("executor_name"),
                        "bindings": sorted(bindings, key=lambda binding: (binding["node_id"], binding["field"])),
                    }
                )
        except (KeyError, TypeError, ValueError):
            raise _error(400, "INVALID_TASK_INPUT_ASSETS", "任务素材预检清单无效") from None
        if any(item["transfer_mode"] not in {"mapped", "upload"} for item in assets):
            raise _error(400, "INVALID_TASK_INPUT_ASSETS", "任务素材传输方式无效")
        if len({item["asset_id"] for item in assets}) != len(assets):
            raise _error(400, "INVALID_TASK_INPUT_ASSETS", "任务素材预检清单包含重复资产")
        return sorted(assets, key=lambda item: item["asset_id"])

    def create_task_preflight(
        self,
        *,
        owner_id: str,
        workflow_id: str,
        workflow_version: int,
        target: str,
        bindings: list[dict[str, Any]],
        input_assets: list[dict[str, Any]],
        context: dict[str, Any],
        workspace_revision: int,
        mapping_root_id: str | None,
        result_root: str,
        result_prefix: list[str],
        directory_id: str,
        target_label: str,
        mapping_status: dict[str, Any],
    ) -> dict[str, Any]:
        if not owner_id:
            raise _error(401, "UNAUTHORIZED", "会话失效，请重新登录")
        if target not in {"local_comfy", "rh_cloud"}:
            raise _error(400, "INVALID_TASK_TARGET", "任务执行目标无效")
        if not SAFE_SEGMENT_RE.fullmatch(directory_id) or any(
            not SAFE_SEGMENT_RE.fullmatch(str(segment)) for segment in result_prefix
        ):
            raise _error(400, "INVALID_TRANSFER_DIRECTORY", "目标目录标识无效")
        bindings = self._task_bindings(bindings)
        if len({(item["node_id"], item["field"]) for item in bindings}) != len(bindings):
            raise _error(400, "INVALID_TASK_BINDINGS", "工作流素材字段重复绑定")
        asset_ids = {item["asset_id"] for item in bindings}
        if asset_ids != {str(item.get("asset_id") or "") for item in input_assets}:
            raise _error(400, "INVALID_TASK_BINDINGS", "素材清单与节点绑定不一致")
        record = {
            "preflight_id": self.new_task_transfer_id(),
            "owner_id": owner_id,
            "workflow_id": workflow_id,
            "workflow_version": int(workflow_version),
            "target": target,
            "bindings": bindings,
            "input_assets": self._task_input_assets(input_assets),
            "workspace_id": str(context["workspace_id"]),
            "workspace_mode": str(context["workspace_mode"]),
            "workspace_revision": int(workspace_revision),
            "mapping_id": context.get("mapping_id"),
            "mapping_root_id": mapping_root_id,
            "execution_machine_id": str(context["execution_machine_id"]),
            "target_label": target_label,
            "directory_id": directory_id,
            "result_root": str(Path(result_root).expanduser().resolve(strict=True)),
            "result_prefix": [str(segment) for segment in result_prefix],
            "mapping_status": copy.deepcopy(mapping_status),
            "status": "awaiting_confirmation",
            "version": 1,
            "job_id": None,
            "outputs": [],
            "created_at": _now(),
            "updated_at": _now(),
        }
        with storage.namespace_lock("transfers"):
            self._save_task_transfer_locked(record)
        return self._public_task_transfer(record)

    def _check_task_preflight(
        self,
        record: dict[str, Any],
        *,
        workflow_id: str,
        workflow_version: int,
        target: str,
        bindings: list[dict[str, Any]],
        input_assets: list[dict[str, Any]],
        workspace_id: str,
        workspace_revision: int,
        mapping_id: str | None,
        mapping_root_id: str | None,
        directory_id: str,
        status: str,
    ) -> None:
        expected = (
            str(record.get("workflow_id")),
            int(record.get("workflow_version", 0)),
            str(record.get("target")),
            self._task_bindings(record.get("bindings", [])),
            self._task_input_assets(record.get("input_assets", [])),
            str(record.get("workspace_id")),
            int(record.get("workspace_revision", 0)),
            record.get("mapping_id"),
            record.get("mapping_root_id"),
            str(record.get("directory_id")),
        )
        actual = (
            workflow_id,
            int(workflow_version),
            target,
            self._task_bindings(bindings),
            self._task_input_assets(input_assets),
            workspace_id,
            int(workspace_revision),
            mapping_id,
            mapping_root_id,
            directory_id,
        )
        if expected != actual:
            raise _error(409, "TASK_PREFLIGHT_STALE", "工作流或工作区已变化，请重新预检并确认")
        if record.get("status") != status:
            raise _error(409, "TASK_TRANSFER_STATE_CONFLICT", "任务传输回执未处于可用状态")

    def confirm_task_preflight(
        self,
        preflight_id: str,
        *,
        owner_id: str,
        expected_version: int,
        workflow_id: str,
        workflow_version: int,
        target: str,
        bindings: list[dict[str, Any]],
        input_assets: list[dict[str, Any]],
        workspace_id: str,
        workspace_revision: int,
        mapping_id: str | None,
        mapping_root_id: str | None,
        directory_id: str,
    ) -> dict[str, Any]:
        with storage.namespace_lock("transfers"):
            record = self._load_task_transfer_locked(preflight_id)
            self._check_owner(record, owner_id)
            self._check_version(record, expected_version)
            self._check_task_preflight(
                record,
                workflow_id=workflow_id,
                workflow_version=workflow_version,
                target=target,
                bindings=bindings,
                input_assets=input_assets,
                workspace_id=workspace_id,
                workspace_revision=workspace_revision,
                mapping_id=mapping_id,
                mapping_root_id=mapping_root_id,
                directory_id=directory_id,
                status="awaiting_confirmation",
            )
            record["status"] = "confirmed"
            record["version"] += 1
            record["updated_at"] = _now()
            self._save_task_transfer_locked(record)
            return self._public_task_transfer(record)

    def validate_task_preflight(
        self,
        preflight_id: str,
        *,
        owner_id: str,
        workflow_id: str,
        workflow_version: int,
        target: str,
        bindings: list[dict[str, Any]],
        input_assets: list[dict[str, Any]],
        workspace_id: str,
        workspace_revision: int,
        mapping_id: str | None,
        mapping_root_id: str | None,
        directory_id: str,
    ) -> dict[str, Any]:
        with storage.namespace_lock("transfers"):
            record = self._load_task_transfer_locked(preflight_id)
            self._check_owner(record, owner_id)
            self._check_task_preflight(
                record,
                workflow_id=workflow_id,
                workflow_version=workflow_version,
                target=target,
                bindings=bindings,
                input_assets=input_assets,
                workspace_id=workspace_id,
                workspace_revision=workspace_revision,
                mapping_id=mapping_id,
                mapping_root_id=mapping_root_id,
                directory_id=directory_id,
                status="confirmed",
            )
            return self._public_task_transfer(record)

    def bind_task_job(self, preflight_id: str, *, owner_id: str, job_id: str) -> dict[str, Any]:
        with storage.namespace_lock("transfers"):
            record = self._load_task_transfer_locked(preflight_id)
            self._check_owner(record, owner_id)
            if record.get("status") != "confirmed":
                raise _error(409, "TASK_TRANSFER_STATE_CONFLICT", "任务传输回执尚未确认")
            if record.get("job_id") not in (None, job_id):
                raise _error(409, "TASK_TRANSFER_ALREADY_USED", "任务传输回执已绑定其他任务")
            if record.get("job_id") is None:
                record["job_id"] = job_id
                record["version"] += 1
                record["updated_at"] = _now()
                self._save_task_transfer_locked(record)
            return self._public_task_transfer(record)

    def get_task_preflight(self, preflight_id: str, *, owner_id: str) -> dict[str, Any]:
        with storage.namespace_lock("transfers"):
            record = self._load_task_transfer_locked(preflight_id)
            self._check_owner(record, owner_id)
            return self._public_task_transfer(record)

    def get_task_output(
        self, preflight_id: str, *, owner_id: str, job_id: str, output_key: str
    ) -> dict[str, Any] | None:
        with storage.namespace_lock("transfers"):
            record = self._load_task_transfer_locked(preflight_id)
            self._check_owner(record, owner_id)
            if record.get("job_id") != job_id:
                raise _error(404, "TASK_TRANSFER_NOT_FOUND", "任务传输回执不存在")
            item = next(
                (entry for entry in record.get("outputs", []) if entry.get("output_key") == output_key),
                None,
            )
            return copy.deepcopy(item) if item else None

    @staticmethod
    def _digest_file(path: Path) -> tuple[int, str]:
        digest = hashlib.sha256()
        size = 0
        with path.open("rb") as handle:
            while chunk := handle.read(1024 * 1024):
                size += len(chunk)
                digest.update(chunk)
        return size, digest.hexdigest()

    def _publish_registered_output(
        self,
        source: Path,
        target: Path,
        *,
        expected_size: int,
        expected_sha256: str,
        temp_prefix: str,
    ) -> None:
        if _is_link(target):
            raise _error(403, "TRANSFER_PATH_UNAVAILABLE", "目标产物路径不允许链接")
        if target.exists():
            size, digest = self._digest_file(target)
            if size == expected_size and digest == expected_sha256:
                return
            raise _error(409, "TRANSFER_TARGET_EXISTS", "目标文件已存在且内容不同，未覆盖")
        temporary = target.with_name(f".{temp_prefix}_{secrets.token_hex(8)}.part")
        digest = hashlib.sha256()
        size = 0
        try:
            with source.open("rb") as incoming, temporary.open("xb") as outgoing:
                while chunk := incoming.read(1024 * 1024):
                    size += len(chunk)
                    digest.update(chunk)
                    outgoing.write(chunk)
                outgoing.flush()
                os.fsync(outgoing.fileno())
            if size != expected_size or digest.hexdigest() != expected_sha256:
                raise _error(409, "TRANSFER_SOURCE_CHANGED", "已登记产物在回传前发生变化")
            try:
                os.link(temporary, target)
            except FileExistsError:
                size, existing_hash = self._digest_file(target)
                if size != expected_size or existing_hash != expected_sha256:
                    raise _error(409, "TRANSFER_TARGET_EXISTS", "目标文件已存在且内容不同，未覆盖")
            except OSError:
                raise _error(409, "TRANSFER_ATOMIC_PUBLISH_UNAVAILABLE", "目标目录不支持安全的不覆盖发布") from None
        except CleanroomException:
            raise
        except OSError:
            raise _error(507, "TRANSFER_PUBLISH_FAILED", "产物回传失败，原有文件已保留") from None
        finally:
            temporary.unlink(missing_ok=True)

    def return_registered_output(
        self,
        preflight_id: str,
        *,
        owner_id: str,
        job_id: str,
        output_key: str,
        asset_id: str,
        filename: str,
        source_path: str | Path,
        size_bytes: int,
        executor: str,
    ) -> dict[str, Any]:
        if not re.fullmatch(r"[0-9a-f]{64}", output_key):
            raise _error(400, "INVALID_TASK_OUTPUT_KEY", "产物回传标识无效")
        if not re.fullmatch(r"[0-9A-Fa-f-]{16,64}", asset_id):
            raise _error(400, "INVALID_TASK_OUTPUT_ASSET", "产物资产标识无效")
        source = Path(source_path).expanduser()
        if _is_link(source):
            raise _error(403, "TRANSFER_PATH_UNAVAILABLE", "已登记产物源文件不允许链接")
        try:
            source = source.resolve(strict=True)
            if not source.is_file():
                raise _error(404, "TASK_OUTPUT_ASSET_NOT_FOUND", "已登记产物文件不存在")
            actual_size, digest = self._digest_file(source)
        except CleanroomException:
            raise
        except OSError:
            raise _error(404, "TASK_OUTPUT_ASSET_NOT_FOUND", "已登记产物文件不可读") from None
        if actual_size != int(size_bytes):
            raise _error(409, "TRANSFER_SOURCE_CHANGED", "已登记产物大小发生变化")
        suffix = Path(filename).suffix.lower().lstrip(".")
        safe_extension = "".join(ch for ch in suffix if ch.isascii() and ch.isalnum())[:12]
        target_name = asset_id + ("." + safe_extension if safe_extension else "")

        with storage.namespace_lock("transfers"):
            record = self._load_task_transfer_locked(preflight_id)
            self._check_owner(record, owner_id)
            if record.get("status") != "confirmed" or record.get("job_id") != job_id:
                raise _error(404, "TASK_TRANSFER_NOT_FOUND", "任务传输回执不存在")
            outputs = record.setdefault("outputs", [])
            item = next((entry for entry in outputs if entry.get("output_key") == output_key), None)
            if item is None:
                item = {
                    "output_key": output_key,
                    "asset_id": asset_id,
                    "filename": filename,
                    "size_bytes": actual_size,
                    "sha256": digest,
                    "executor": executor,
                    "status": "pending",
                    "target_name": target_name,
                    "relative_path": None,
                    "error": None,
                    "updated_at": _now(),
                }
                outputs.append(item)
            elif item.get("asset_id") != asset_id:
                raise _error(409, "TASK_OUTPUT_IDENTITY_CONFLICT", "同一执行产物已绑定其他资产")
            elif item.get("sha256") != digest or int(item.get("size_bytes", -1)) != actual_size:
                raise _error(409, "TASK_OUTPUT_IDENTITY_CONFLICT", "已登记产物内容与回执不一致")
            try:
                result_root = Path(str(record["result_root"]))
                directory = _ensure_directory(
                    result_root,
                    *record.get("result_prefix", []),
                    "transfers",
                    "assets",
                    str(record["workspace_id"]),
                    str(record["directory_id"]),
                )
                target = directory / target_name
                self._publish_registered_output(
                    source,
                    target,
                    expected_size=actual_size,
                    expected_sha256=digest,
                    temp_prefix=asset_id,
                )
                item["status"] = "returned"
                item["relative_path"] = "/".join(
                    [*record.get("result_prefix", []), "transfers", "assets", str(record["workspace_id"]), str(record["directory_id"]), target_name]
                )
                item["error"] = None
            except CleanroomException as exc:
                item["status"] = "failed"
                item["error"] = exc.code
            except OSError:
                item["status"] = "failed"
                item["error"] = "TRANSFER_PUBLISH_FAILED"
            item["updated_at"] = _now()
            record["version"] = int(record.get("version", 0)) + 1
            record["updated_at"] = _now()
            self._save_task_transfer_locked(record)
            return copy.deepcopy(item)

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
