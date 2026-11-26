# Copyright 2026 qinxuedong (Gods-Workbench Authors)
# SPDX-License-Identifier: FSL-1.1-Apache-2.0
#
# Licensed under the Functional Source License, Version 1.1, Apache 2.0
# Future License (the "License"); you may not use this file except in
# compliance with the License. You may obtain a copy of the License at
#     https://fsl.software/
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""真实素材索引后台任务；单实例、有限队列、持续授权及提交收据。"""

from __future__ import annotations

import hashlib
import importlib
import json
import math
import os
import re
import stat
import struct
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import suppress
from pathlib import Path
from queue import Empty, Queue

from gw.asset_registry import generation_metadata
from gw.core import storage
from gw.core.auth import EDIT_ROLES
from gw.core.config import load_runtime_auth_config
from gw.core.errors import CleanroomException
from gw.core.runtime_paths import (
    DATA_DIR_ENV,
    LOCAL_AUTH_DB_ENV,
    RUNTIME_MODE_ENV,
    VIDEO_DATA_DIR_ENV,
)
from gw.workspaces import context as workspace_context
from gw.workspaces.service import default_workspace_service

MAX_ACTIVE = 16
MAX_JOBS = 1000
MAX_FILES = 250000
MAX_ENTRIES = 500000
# 单文件完整 sha256 上限：超过该大小的文件改用采样哈希（QIN-564）。
# 完整哈希大文件代价过高（试点库 102 个 >512MiB 文件占 48.4% 字节），
# 原 413 fail-fast 会让整单索引失败，改为首尾 4MiB + 长度的采样哈希。
_HASH_LIMIT_BYTES = 512 * 1024 * 1024


def _decode_shot_vector_frame(source: Path, sample_sec: float) -> bytes:
    """在索引任务运行时延迟加载媒体解码器，避免领域包循环依赖。"""
    decoder = importlib.import_module(
        "gw.media.keyframe_extractor"
    ).decode_keyframe_to_memory
    return decoder(source, sample_sec)
_HASH_SAMPLE_BYTES = 4 * 1024 * 1024
# 采样哈希域分离前缀：摘要输入与完整 sha256（纯文件字节流）结构不同，
# 理论碰撞空间互斥；查重组按值分组时两种模式不会交叉误配。
_SAMPLED_HASH_DOMAIN = b"gw-asset-registry-sampled-sha256-v1"
# hash_mode 诚实标记（QIN-564 AC1/AC3）：完整哈希素材登记 "full"，
# 超限采样素材登记 "sampled"，查重侧按标记区分降级口径。
HASH_MODE_FULL = "full"
HASH_MODE_SAMPLED = "sampled"
VIDEO_SUFFIXES = frozenset(
    {".mp4", ".mov", ".mkv", ".avi", ".webm", ".m4v", ".flv", ".wmv"}
)
# 扫描进度落盘节流：除每 25 项外，距上次落盘超过该秒数也落盘。小样本里单个
# 视频探针/哈希可能耗时数秒，只按批次落盘会让运行中的 scanned 恒为 0。
PROGRESS_MIN_INTERVAL_SECONDS = 0.2
PROGRESS_BATCH_SIZE = 25
# 温和节流：大目录扫描时每处理 50 个文件在锁外让出 5ms 时间片，避免单核 CPU
# 持续打满并与预览请求/主服务事件循环竞争资源；小库（<50 项）零开销。
SCAN_THROTTLE_EVERY = 50
SCAN_THROTTLE_SECONDS = 0.005
TERMINAL = {"succeeded", "failed", "cancelled", "interrupted"}
ACTIONS = {
    "pause": {"queued", "running"},
    "resume": {"paused"},
    "cancel": {"queued", "running", "paused"},
    "retry": {"failed", "cancelled", "interrupted"},
}

# QIN-652：快扫入库后，视频探测与文件哈希在后台受控并发补齐。
# PM 裁决（issue #812）：视频镜头检测并发固定为 1，避免多个 ffmpeg 全片软解
# 同时吃满多核；文件哈希并发默认 2，仅哈希通道可由环境变量在 1..4 调整。
# 不扩编 gw/media/*，也不改 ffmpeg 线程参数。
ENRICH_VIDEO_CONCURRENCY = 1
ENRICH_HASH_CONCURRENCY_DEFAULT = 2
ENRICH_HASH_CONCURRENCY_MAX = 4
ENRICH_HASH_CHUNK_BYTES = 8 * 1024 * 1024
ENRICH_BATCH_SIZE = 20
ENRICH_BATCH_INTERVAL_SECONDS = 1.5
# 单条目长时间无结果才视为卡死；暂停时长不计入，取消另有 _checkpoint 即时生效。
ENRICH_STALL_SECONDS = 600


def _enrich_hash_concurrency() -> int:
    """哈希通道并发数；视频通道恒为 1，不受此值影响。"""
    raw = os.environ.get("GW_INDEX_ENRICH_CONCURRENCY", "")
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return ENRICH_HASH_CONCURRENCY_DEFAULT
    return max(1, min(ENRICH_HASH_CONCURRENCY_MAX, value))


# 扫描期落盘的文件版本指纹键。旧记录的 `modified_at` 只有秒级精度，同秒等长
# 改写会与原版本逐字节相同；`mtime_ns` 提供纳秒分辨率，`ino`/`dev` 再区分
# 「原子替换成另一份同秒同长文件」，三者合起来才足以判定「内容未变」。
FILE_VERSION_FIELDS = ("size_bytes", "mtime_ns", "ino", "dev")


def _file_version(info) -> dict:
    """由 stat 结果构造版本指纹（扫描与富化同一口径）。"""
    return {
        "size_bytes": int(getattr(info, "st_size", 0) or 0),
        "mtime_ns": int(getattr(info, "st_mtime_ns", 0) or 0),
        "ino": int(getattr(info, "st_ino", 0) or 0),
        "dev": int(getattr(info, "st_dev", 0) or 0),
    }


def _same_file_version(left: object, right: object) -> bool:
    """两个版本指纹是否逐字段相同；任一侧缺失/畸形一律判「不同」。

    缺失即未知（既有登记建于本改动之前），按不同处理让富化重新计算并补写
    指纹，宁可多算一次也不复用可能过期的旧结果。
    """
    if not isinstance(left, dict) or not isinstance(right, dict):
        return False
    for field in FILE_VERSION_FIELDS:
        if field not in left or field not in right:
            return False
        try:
            if int(left[field]) != int(right[field]):
                return False
        except (TypeError, ValueError):
            return False
    return True


class _EnrichGate:
    """跨任务富化通道闸门：进程内同一通道并发数不超过 ``limit``。

    PM 裁决（issue #812）的并发上限是**全局**口径：视频 1 / 哈希 2。
    每个任务各建线程池时，两个并发索引任务会同时跑 2 个视频探测、4 个哈希，
    因此把上限收到进程级闸门上，池大小只决定「谁在等闸门」。
    """

    def __init__(self, limit: int):
        self._condition = threading.Condition()
        self._active = 0
        self._limit = max(1, int(limit))

    def configure(self, limit: int) -> None:
        """按当前配置对齐上限；调小只拦新进入者，已在跑的不强杀。"""
        with self._condition:
            self._limit = max(1, int(limit))
            self._condition.notify_all()

    def acquire(self, timeout: float) -> bool:
        deadline = time.monotonic() + max(0.0, float(timeout))
        with self._condition:
            while self._active >= self._limit:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                self._condition.wait(remaining)
            self._active += 1
            return True

    def release(self) -> None:
        with self._condition:
            if self._active > 0:
                self._active -= 1
            self._condition.notify()


_ENRICH_GATES: dict[str, _EnrichGate] = {}
_ENRICH_GATES_LOCK = threading.Lock()


def _enrich_gate(kind: str) -> _EnrichGate:
    """取（必要时创建）通道闸门；上限每次按当前配置对齐。"""
    limit = ENRICH_VIDEO_CONCURRENCY if kind == "video" else _enrich_hash_concurrency()
    with _ENRICH_GATES_LOCK:
        gate = _ENRICH_GATES.get(kind)
        if gate is None:
            gate = _ENRICH_GATES[kind] = _EnrichGate(limit)
        else:
            gate.configure(limit)
        return gate


def _error(status, code, message, *, data_status=None, unavailable=False):
    """抛出受控错误；来源不可用时同时返回明确的降级标记。"""
    extra = {}
    exposed = set()
    if data_status is not None:
        extra["data_status"] = data_status
        exposed.add("data_status")
    if unavailable:
        extra["unavailable"] = True
        exposed.add("unavailable")
    raise CleanroomException(
        status, code, message, extra=extra, expose_extra_fields=exposed or None
    )


def _digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()


def _root_configuration_fingerprint(aliases):
    """只对来源身份配置做指纹；挂载可达性属于运行时状态。"""
    volatile = {"availability", "availability_reason", "availability_message", "alias_candidates"}
    stable = {
        source_id: str(
            {key: value for key, value in alias.items() if key not in volatile}
        )
        for source_id, alias in sorted(aliases.items())
    }
    return _digest(stable)


def _legacy_root_configuration_fingerprint(aliases):
    """读取升级前已入队任务使用的指纹格式。"""
    return _digest({key: str(value) for key, value in sorted(aliases.items())})


def _actor(auth):
    return {
        "subject": auth.subject,
        "mode": auth.mode,
        "domain": auth.identity_domain,
        "role": auth.role,
    }


def _owner(actor):
    return _digest([actor["mode"], actor["domain"], actor["subject"]])


def _restore_matching_removed_root(candidate_path: str) -> bool:
    """若目标目录命中已移除允许根（active=False），自动调用 restore_root 恢复。"""
    from gw.settings.models import StorageAllowedRootDeleteRequest
    from gw.settings.service import default_storage_allowed_roots_service

    root_state = default_storage_allowed_roots_service._state.read()
    candidate = Path(candidate_path)
    matched = None
    matched_depth = -1
    for record in root_state.get("roots", []):
        if record.get("active") or not record.get("path"):
            continue
        try:
            candidate.relative_to(Path(record["path"]))
        except ValueError:
            continue
        depth = len(Path(record["path"]).parts)
        if depth > matched_depth:
            matched = record
            matched_depth = depth
    if matched is None:
        return False
    root_snapshot = default_storage_allowed_roots_service.get_snapshot()
    default_storage_allowed_roots_service.restore_root(
        str(matched.get("id") or ""),
        StorageAllowedRootDeleteRequest(expected_version=root_snapshot.revision),
    )
    return True


def _runtime_path_configuration():
    return tuple(
        (name, os.environ.get(name))
        for name in (
            RUNTIME_MODE_ENV,
            DATA_DIR_ENV,
            LOCAL_AUTH_DB_ENV,
            VIDEO_DATA_DIR_ENV,
            "LOCALAPPDATA",
        )
    )


def _video_frame_rate(path: Path) -> float | None:
    """读取真实帧率，供详情面板显示；探测失败返回 None（不阻断入库）。

    复用 ``gw.asset_registry.media_probe.probe`` 既有 ffprobe 口径，不新增
    ``gw/media/*`` 依赖，也不整片解码。
    """
    try:
        media_probe = importlib.import_module("gw.asset_registry.media_probe")
        probed = media_probe.probe(path)
    except Exception:  # noqa: BLE001 - 任何探测失败都不影响该条目入库
        return None
    try:
        value = float((probed or {}).get("fps") or 0)
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) and value > 0 else None


def video_detail_fields(metadata: dict | None) -> dict:
    """把 ``metadata.video_metadata`` 的技术字段投影成详情面板读的扁平字段。

    前端详情面板读取的是资产顶层 ``width``/``height``/``frame_rate``（与
    ``import_assets`` 既有口径一致），而索引把技术元数据写在
    ``metadata.video_metadata``。这里给出唯一的投影口径，扫描与富化共用。
    """
    video = (metadata or {}).get("video_metadata")
    if not isinstance(video, dict):
        return {}
    fields: dict[str, float | int] = {}
    try:
        width = int(video.get("width") or 0)
        height = int(video.get("height") or 0)
    except (TypeError, ValueError):
        width = height = 0
    if width > 0 and height > 0:
        fields["width"] = width
        fields["height"] = height
    try:
        frame_rate = float(video.get("frame_rate") or 0)
    except (TypeError, ValueError):
        frame_rate = 0.0
    if math.isfinite(frame_rate) and frame_rate > 0:
        fields["frame_rate"] = round(frame_rate, 6)
    return fields


def _video_index_data(path: Path) -> tuple[dict, float | None]:
    """为本地视频登记镜头与技术元数据，缩略图由后续按需提取。"""
    detect_scenes = importlib.import_module("gw.media.scene_detector").detect_scenes

    scenes = []
    try:
        scenes = detect_scenes(path)
    except CleanroomException as exc:
        return (
            {
                "video_segments": [],
                "video_metadata": {
                    "scene_detection_status": "unavailable",
                    "keyframe_status": "unavailable",
                    "reason_code": exc.code,
                    "source_url": None,
                },
            },
            None,
        )

    if not scenes:
        return (
            {
                "video_segments": [],
                "video_metadata": {
                    "scene_detection_status": "unavailable",
                    "keyframe_status": "unavailable",
                    "reason_code": "SCENE_DETECTION_FAILED",
                    "source_url": None,
                },
            },
            None,
        )

    first = scenes[0]
    duration = scenes[-1]["end_sec"]
    video_metadata = {
        "width": first["width"],
        "height": first["height"],
        "duration_sec": duration,
        "codec": first["codec"],
        "bit_rate": first.get("bit_rate"),
        "container": first.get("container") or path.suffix.lower().lstrip("."),
        "source_url": first.get("source_url"),
        "scene_detection_status": "complete",
        "keyframe_status": "pending",
    }
    # 详情面板要显示分辨率与帧率：帧率只能由 ffprobe 拿到，探测失败按缺省处理。
    frame_rate = _video_frame_rate(path)
    if frame_rate is not None:
        video_metadata["frame_rate"] = frame_rate
    return {"video_segments": scenes, "video_metadata": video_metadata}, duration


def _roots():
    """解析允许根与来源别名；每个别名保存其根 ID、相对前缀和筛选 ID。"""
    from gw.asset_registry import repository as repo
    from gw.settings.service import default_storage_settings_service

    purging_ids, purging_keys = repo._purging_root_ids_and_keys()
    roots = [
        root
        for root in storage.allowed_roots()
        if storage.root_identity_id(root) not in purging_ids
        and not repo._path_within_any_key(root, purging_keys)
    ]
    canonical = {}
    root_statuses = {}
    root_resolutions = {}
    for root in roots:
        root_id = storage.root_identity_id(root)
        status = storage.probe_mount(str(root))
        resolved_root = storage.resolve_mount_path(str(root)) if status["available"] else None
        if status["available"] and resolved_root is None:
            status = {
                "available": False,
                "reason": "MOUNT_OFFLINE",
                "message": "允许根路径解析检查超时，目录暂时离线",
            }
        path_key = storage.directory_path_key(str(root))
        root_statuses[path_key] = status
        root_resolutions[path_key] = resolved_root
        if root_id not in canonical or (
            not root_statuses[
                storage.directory_path_key(str(canonical[root_id]))
            ]["available"]
            and status["available"]
        ):
            canonical[root_id] = root
    aliases = {}
    for root_id, root in canonical.items():
        status = root_statuses[storage.directory_path_key(str(root))]
        aliases[root_id] = {
            "root_id": root_id,
            "root": root,
            "prefix": "",
            "storage_root_id": root_id,
            "name": root.name or root_id,
            "availability": "available" if status["available"] else "offline",
            "availability_reason": str(status["reason"] or ""),
            "availability_message": str(status["message"] or ""),
        }
    registered_locations = {}
    local_libraries = default_storage_settings_service.get_snapshot().local_libraries
    removed_roots = [
        record
        for record in storage._allowed_roots_state().get("roots", [])
        if not record.get("active") and record.get("path")
    ]

    def removed_root_for_path(path):
        for record in removed_roots:
            try:
                relative = path.relative_to(Path(record["path"]))
            except (OSError, RuntimeError, ValueError):
                continue
            return record, relative
        return None

    def record_unavailable_location(source_id, item, path, status):
        matches = []
        for allowed_root in roots:
            try:
                relative = path.relative_to(allowed_root)
            except ValueError:
                continue
            matches.append((allowed_root, relative))
        if matches:
            root_path, relative = max(matches, key=lambda match: len(match[0].parts))
            root_id = storage.root_identity_id(root_path)
            root = canonical[root_id]
            prefix = "" if str(relative) == "." else relative.as_posix()
            aliases[source_id] = {
                "root_id": root_id,
                "root": root,
                "prefix": prefix,
                "storage_root_id": source_id,
                "name": str(item.get("name") or source_id),
                "availability": "offline",
                "availability_reason": str(status["reason"] or "MOUNT_OFFLINE"),
                "availability_message": str(status["message"] or "目录暂时不可访问"),
            }
            registered_locations[source_id] = {
                "root_id": root_id,
                "prefix": prefix,
                "available": False,
            }
            return
        aliases[source_id] = {
            "root_id": source_id,
            "root": None,
            "prefix": "",
            "storage_root_id": source_id,
            "name": str(item.get("name") or source_id),
            "availability": "offline",
            "availability_reason": str(status["reason"] or "MOUNT_OFFLINE"),
            "availability_message": str(status["message"] or "目录暂时不可访问"),
        }

    for item in local_libraries:
        source_id = str(item.get("id") or "")
        raw_path = str(item.get("path") or "")
        path = Path(raw_path)
        if not source_id:
            continue
        if not raw_path or not path.is_absolute():
            aliases[source_id] = {
                "root_id": source_id,
                "root": None,
                "prefix": "",
                "storage_root_id": source_id,
                "name": str(item.get("name") or source_id),
                "availability": "invalid",
                "availability_reason": "MOUNT_NOT_FOUND",
                "availability_message": "素材来源没有有效的绝对目录路径",
            }
            continue
        removed = removed_root_for_path(path)
        if removed is not None:
            record, relative = removed
            root_id = str(record.get("id") or source_id)
            prefix = "" if str(relative) == "." else relative.as_posix()
            aliases[source_id] = {
                "root_id": root_id,
                "root": None,
                "prefix": prefix,
                "storage_root_id": source_id,
                "name": str(item.get("name") or source_id),
                "availability": "removed",
                "availability_reason": "MOUNT_ROOT_REMOVED",
                "availability_message": "允许根已移除；已有索引与源文件保留",
            }
            registered_locations[source_id] = {
                "root_id": root_id,
                "prefix": prefix,
                "available": False,
            }
            continue
        status = storage.probe_mount(raw_path)
        if not status["available"]:
            record_unavailable_location(source_id, item, path, status)
            continue
        # 路径解析也有界；探测或解析超时均保留离线 facet，不让网络盘卡住索引请求。
        resolved = storage.resolve_mount_path(raw_path)
        if resolved is None:
            record_unavailable_location(
                source_id,
                item,
                path,
                {
                    "reason": "MOUNT_OFFLINE",
                    "message": "素材目录路径解析检查超时，目录暂时离线",
                },
            )
            continue
        matches = []
        for allowed_root in roots:
            root_resolved = root_resolutions.get(
                storage.directory_path_key(str(allowed_root))
            )
            if root_resolved is None:
                continue
            try:
                relative = resolved.relative_to(root_resolved)
            except ValueError:
                continue
            matches.append((allowed_root, relative))
        if not matches:
            suggestions = storage.mount_alias_candidates(raw_path, roots)
            code = "MOUNT_ALIAS_UNCONFIRMED" if suggestions else "MOUNT_OUTSIDE_ALLOWED_ROOTS"
            message = (
                "该路径可能是允许根的映射盘或 UNC 别名，尚未确认"
                if suggestions
                else "该素材来源不在已授权的允许根内"
            )
            aliases[source_id] = {
                "root_id": source_id,
                "root": None,
                "prefix": "",
                "storage_root_id": source_id,
                "name": str(item.get("name") or source_id),
                "availability": "invalid",
                "availability_reason": code,
                "availability_message": message,
                "alias_candidates": suggestions,
            }
            continue
        root_path, relative = max(matches, key=lambda match: len(match[0].parts))
        root_id = storage.root_identity_id(root_path)
        root = canonical[root_id]
        prefix = "" if str(relative) == "." else relative.as_posix()
        alias_record = {
            "root_id": root_id,
            "root": root,
            "prefix": prefix,
            "storage_root_id": source_id,
            "name": str(item.get("name") or source_id),
            "availability": "available" if status["available"] else "offline",
            "availability_reason": str(status["reason"] or ""),
            "availability_message": str(status["message"] or ""),
        }
        if storage.directory_path_key(str(root_path)) != storage.directory_path_key(str(root)):
            alias_record["scan_path"] = resolved
        aliases[source_id] = alias_record
        registered_locations[source_id] = {
            "root_id": root_id,
            "prefix": prefix,
            "available": bool(status["available"]),
        }
    if not roots and not local_libraries:
        _error(
            503,
            "INDEX_SOURCE_NOT_AVAILABLE",
            "未配置允许根目录",
            data_status="not_integrated",
            unavailable=True,
        )
    # 已确认的路径别名仅作为同一登记来源的离线恢复通道；未确认候选不参与索引。
    from gw.asset_registry import repository as repo

    # QIN-604：只读来源别名走同一份注册表快照，避免 /assets 的 facet 链每次全表解析。
    bound_aliases = repo.readonly_registry_snapshot().get("root_aliases", {})
    for item in local_libraries:
        source_id = str(item.get("id") or "")
        if not source_id:
            continue
        registered = registered_locations.get(source_id)
        # 登记目录仍可访问时只使用登记路径；已离线时才考虑确认过的别名。
        if source_id in aliases and (
            registered is None or registered["available"]
        ):
            continue
        bound = bound_aliases.get(source_id)
        bound_root_id = str(bound.get("root_id") or "") if isinstance(bound, dict) else ""
        if not bound_root_id:
            continue
        paths = bound.get("aliases", [])
        if not isinstance(paths, list):
            continue
        for raw_alias_path in sorted(str(value) for value in paths if value):
            try:
                alias_path = Path(raw_alias_path).resolve(strict=True)
            except (OSError, RuntimeError, ValueError):
                continue
            if not storage.probe_mount(str(alias_path))["available"]:
                continue

            if registered is not None:
                # 离线来源的身份与 canonical 前缀必须仍与持久绑定完全一致。
                if bound_root_id != registered["root_id"]:
                    continue
                bound_prefix = bound.get("canonical_relpath")
                if not isinstance(bound_prefix, str):
                    continue
                try:
                    expected_prefix = (
                        storage.canonical_relpath(registered["prefix"])
                        if registered["prefix"]
                        else ""
                    )
                    confirmed_prefix = (
                        storage.canonical_relpath(bound_prefix) if bound_prefix else ""
                    )
                except CleanroomException:
                    continue
                root = canonical.get(bound_root_id)
                if (
                    confirmed_prefix != expected_prefix
                    or root is None
                ):
                    continue
                aliases[source_id] = {
                    "root_id": bound_root_id,
                    "root": root,
                    "prefix": registered["prefix"],
                    "scan_path": alias_path,
                    "storage_root_id": source_id,
                    "name": str(item.get("name") or source_id),
                }
                break

            # 旧绑定路径位于 allowed root 内时保留原有支持，不把外部路径升格为 allowed root。
            matches = []
            for allowed_root in roots:
                try:
                    relative = alias_path.relative_to(allowed_root.resolve())
                except (OSError, RuntimeError, ValueError):
                    continue
                matches.append((allowed_root, relative))
            if not matches:
                continue
            root, relative = max(matches, key=lambda match: len(match[0].parts))
            existing_root = canonical.get(bound_root_id)
            if existing_root is None or not existing_root.is_dir():
                canonical[bound_root_id] = root
            aliases[source_id] = {
                "root_id": bound_root_id,
                "root": root,
                "prefix": "" if str(relative) == "." else relative.as_posix(),
                "storage_root_id": source_id,
                "name": str(item.get("name") or source_id),
            }
            break
    return canonical, aliases


# QIN-582：来源根解析与逐别名挂载探测的进程内有界缓存。
# _roots() 每次都会对允许根、登记目录与绑定别名做有界 probe_mount，单次首屏
# 列表请求原本要跑 3 遍。这里只缓存「别名解析 + 逐别名可用性」，root_facets()
# 的 count 仍每次基于实时注册表重算，避免资产增删后来源计数不刷新。
ROOT_FACETS_CACHE_TTL_SECONDS = 2.0
_ROOT_FACETS_CACHE_LOCK = threading.Lock()
_ROOT_FACETS_CACHE = {"key": None, "value": None, "expires_at": 0.0}


def invalidate_root_facets_cache() -> None:
    """在移出、恢复、注销来源或清除后主动失效来源根缓存。"""
    with _ROOT_FACETS_CACHE_LOCK:
        _ROOT_FACETS_CACHE["key"] = None
        _ROOT_FACETS_CACHE["value"] = None
        _ROOT_FACETS_CACHE["expires_at"] = 0.0


def _path_stat_signature(raw_path: str):
    """目录出现/消失或内容变化时的轻量签名；只 stat，不做有界挂载探测。"""
    try:
        info = Path(raw_path).stat()
    except OSError:
        return None
    return (info.st_mtime_ns, info.st_size)


def _root_facets_cache_key():
    """缓存失效键：配置 revision、运行时数据根与目录状态任一变化即失效。

    目录被外部 rename / mkdir 不会改动任何配置 revision，但会改变来源可用性。
    因此把所有相关路径的 stat 签名放进键；用一次有界调用收口，避免不可达网络盘
    把 facet 请求卡死。超时或探测槽占满时退化为 ``unknown``，由短 TTL 兜底。
    """
    from gw.asset_registry import repository as repo
    from gw.settings.service import default_storage_settings_service

    roots_state = storage._allowed_roots_state()
    settings_state = default_storage_settings_service._state.read()
    registry_snapshot = repo.readonly_registry_snapshot()
    bound_aliases = registry_snapshot.get("root_aliases", {})
    purging_roots = registry_snapshot.get("purging_roots", {})

    paths = [str(record.get("path") or "") for record in roots_state.get("roots", [])]
    paths.extend(
        str(entry.get("path") or "")
        for entry in (settings_state.get("extra_local_entries") or [])
    )
    paths.extend(
        str(value or "") for value in (settings_state.get("dirs") or {}).values()
    )
    if isinstance(bound_aliases, dict):
        for bound in bound_aliases.values():
            if isinstance(bound, dict):
                paths.extend(str(item) for item in (bound.get("aliases") or []))
    paths.extend(
        item
        for item in (os.environ.get("GW_ALLOWED_ROOTS") or "").split(os.pathsep)
        if item
    )
    unique_paths = sorted({item for item in paths if item})
    path_state = storage._run_bounded_mount_call(
        lambda: tuple((item, _path_stat_signature(item)) for item in unique_paths),
        storage.MOUNT_PROBE_TIMEOUT_SECONDS,
    )
    if path_state is None:
        path_state = "unknown"

    return (
        str(storage.data_root()),
        int(roots_state.get("revision", 1)),
        json.dumps(
            [
                (str(item.get("id") or ""), str(item.get("path") or ""), bool(item.get("active")))
                for item in roots_state.get("roots", [])
            ],
            ensure_ascii=False,
        ),
        int(settings_state.get("revision", 1)),
        json.dumps(settings_state, sort_keys=True, ensure_ascii=False, default=str),
        json.dumps(bound_aliases, sort_keys=True, ensure_ascii=False, default=str),
        json.dumps(purging_roots, sort_keys=True, ensure_ascii=False, default=str),
        path_state,
    )


def _cached_root_snapshot():
    """返回 ``(aliases, alias_status)``；命中缓存时不做任何磁盘探测。"""
    with _ROOT_FACETS_CACHE_LOCK:
        # 键计算也收在锁内：并发下只算一次，避免多条请求因探测槽争用得到
        # 不一致的路径状态签名而同时 miss（惊群）。
        key = _root_facets_cache_key()
        now = time.monotonic()
        cached = _ROOT_FACETS_CACHE
        if (
            cached["value"] is not None
            and cached["key"] == key
            and now < cached["expires_at"]
        ):
            return cached["value"]
        # single-flight：miss 时只算一次，并发请求复用同一份结果，避免抢光探测槽。
        _, aliases = _roots()
        alias_status = {}
        for source_id, alias in sorted(aliases.items()):
            if (
                str(alias.get("availability") or "available") == "invalid"
                or alias.get("root") is None
            ):
                continue
            path = (
                Path(alias["scan_path"])
                if alias.get("scan_path") is not None
                else alias["root"].joinpath(*Path(alias.get("prefix") or ".").parts)
            )
            status = storage.probe_mount(str(path))
            alias_status[source_id] = {
                "availability": "available" if status["available"] else "offline",
                "availability_reason": str(status["reason"] or ""),
                "availability_message": str(status["message"] or ""),
            }
        value = (aliases, alias_status)
        _ROOT_FACETS_CACHE.update(
            key=key,
            value=value,
            expires_at=time.monotonic() + ROOT_FACETS_CACHE_TTL_SECONDS,
        )
        return value


def _alias_mount_path(alias, source_id, local_paths, allowed_paths):
    """从来源别名或持久登记中取路径；离线时仍保留其已保存目录。"""
    if isinstance(alias, dict) and alias.get("root") is not None:
        root = Path(alias["root"])
        prefix = str(alias.get("prefix") or "")
        return root.joinpath(*Path(prefix).parts) if prefix else root
    if source_id in local_paths:
        return Path(local_paths[source_id])
    if source_id in allowed_paths:
        return Path(allowed_paths[source_id])
    return None


def _root_group_path_keys(member_ids, aliases):
    """归一化身份组的挂载路径，包含离线登记和已确认路径别名。"""
    from gw.settings.service import default_storage_settings_service

    settings = default_storage_settings_service.get_snapshot()
    local_paths = {
        str(item.get("id") or ""): str(item.get("path") or "")
        for item in settings.local_libraries
        if item.get("id") and item.get("path")
    }
    allowed_paths = {}
    for record in storage._allowed_roots_state().get("roots", []):
        path = str(record.get("path") or "")
        if not path:
            continue
        for identity in {str(record.get("id") or ""), *(str(v) for v in record.get("aliases") or [])}:
            if identity:
                allowed_paths[identity] = path
    keys = set()
    for member_id in member_ids:
        alias = aliases.get(str(member_id)) if isinstance(aliases, dict) else None
        path = _alias_mount_path(alias, str(member_id), local_paths, allowed_paths)
        if path is None:
            continue
        try:
            keys.add(storage.directory_path_key(str(path)))
        except CleanroomException:
            continue
        # Confirmed mount aliases are physical paths in the same QIN-596 identity group.
        if isinstance(alias, dict):
            scan_path = alias.get("scan_path")
            if scan_path:
                try:
                    keys.add(storage.directory_path_key(str(scan_path)))
                except CleanroomException:
                    pass
    return keys


def root_identity_path_keys(root_id: str) -> list[str]:
    """返回指定挂载根及 QIN-596 别名身份组的路径键；未知身份失败关闭。"""
    identity = str(root_id or "").strip()
    if not identity:
        raise CleanroomException(400, "INVALID_REQUEST", "挂载根身份不能为空")
    aliases, _ = _cached_root_snapshot()
    facet = next(
        (
            item for item in root_facets()
            if identity in {str(item.get("id") or ""), *(str(v) for v in item.get("aliases") or [])}
        ),
        None,
    )
    if facet is None:
        raise CleanroomException(404, "ROOT_NOT_FOUND", "素材来源根不存在")
    member_ids = {str(facet.get("id") or ""), *(str(v) for v in facet.get("aliases") or [])}
    keys = sorted(_root_group_path_keys(member_ids, aliases))
    if not keys:
        raise CleanroomException(409, "MOUNT_ROOT_PATH_UNAVAILABLE", "素材来源根没有可用的保存路径")
    return keys


def _normalize_relative_folder_parts(folder_path: str | None) -> tuple[str, ...]:
    """校验并拆解挂载根内的相对文件夹路径，拒绝绝对路径与 .. 越界段。"""
    raw = str(folder_path or "").strip()
    if not raw or raw == "__root__":
        return ()
    normalized = raw.replace("\\", "/").strip("/")
    if not normalized:
        return ()
    if normalized.startswith("/") or (len(normalized) >= 2 and normalized[1] == ":"):
        raise CleanroomException(400, "INVALID_PATH", "文件夹路径必须是根内相对路径")
    parts = [part for part in normalized.split("/") if part and part != "."]
    if any(part == ".." for part in parts):
        raise CleanroomException(400, "INVALID_PATH", "文件夹路径包含非法上级目录段")
    return tuple(parts)


def folder_identity_path_keys(root_id: str, folder_path: str | None = None) -> list[str]:
    """返回指定挂载根或根内子文件夹及 QIN-596 别名身份组的规范路径键。"""
    parts = _normalize_relative_folder_parts(folder_path)
    if not parts:
        return root_identity_path_keys(root_id)
    identity = str(root_id or "").strip()
    if not identity:
        raise CleanroomException(400, "INVALID_REQUEST", "挂载根身份不能为空")
    aliases, _ = _cached_root_snapshot()
    facet = next(
        (
            item for item in root_facets()
            if identity in {str(item.get("id") or ""), *(str(v) for v in item.get("aliases") or [])}
        ),
        None,
    )
    if facet is None:
        raise CleanroomException(404, "ROOT_NOT_FOUND", "素材来源根不存在")
    member_ids = {str(facet.get("id") or ""), *(str(v) for v in facet.get("aliases") or [])}
    keys: set[str] = set()
    for member_id in member_ids:
        alias = aliases.get(member_id) if isinstance(aliases, dict) else None
        physical_root = alias.get("root") if isinstance(alias, dict) else None
        if physical_root is None:
            continue
        prefix = str(alias.get("prefix") or "")
        base = physical_root.joinpath(*Path(prefix).parts) if prefix else physical_root
        target = base.joinpath(*parts)
        try:
            keys.add(storage.directory_path_key(str(target)))
        except CleanroomException:
            continue
    if not keys:
        raise CleanroomException(409, "MOUNT_ROOT_PATH_UNAVAILABLE", "素材来源根没有可用的保存路径")
    return sorted(keys)


def has_strict_hidden_ancestor(candidate_keys, hidden_keys) -> bool:
    """判断候选目录是否存在处于隐藏态的严格祖先目录（不含自身）。"""
    normalized_hidden = [str(k or "").rstrip("\\/") for k in (hidden_keys or []) if str(k or "").strip()]
    for raw_cand in candidate_keys or []:
        cand = str(raw_cand or "").rstrip("\\/")
        if not cand:
            continue
        for parent in normalized_hidden:
            if cand != parent and _path_key_within_any(cand, [parent]):
                return True
    return False


def path_key_is_self_or_inherited_hidden(path_key: str, hidden_keys) -> tuple[bool, bool]:
    """返回单个目录路径键的 (self_hidden, inherited_hidden) 二元组。"""
    cand = str(path_key or "").rstrip("\\/")
    if not cand or not hidden_keys:
        return (False, False)
    normalized_hidden = [str(k or "").rstrip("\\/") for k in hidden_keys if str(k or "").strip()]
    self_hidden = cand in normalized_hidden
    inherited_hidden = any(
        cand != parent and _path_key_within_any(cand, [parent])
        for parent in normalized_hidden
    )
    return (self_hidden, inherited_hidden)


def asset_path_key(asset, aliases=None):
    """由已索引资产的根身份和规范相对路径还原比较键，不依赖在线文件。"""
    if not isinstance(asset, dict):
        return ""
    explicit = str(asset.get("path") or "")
    if explicit:
        try:
            return storage.directory_path_key(explicit)
        except CleanroomException:
            pass
    if aliases is None:
        aliases, _ = _cached_root_snapshot()
    root_id = str(asset.get("root_id") or "")
    alias = aliases.get(root_id) if isinstance(aliases, dict) else None
    root = alias.get("root") if isinstance(alias, dict) else None
    if root is None:
        return ""
    relative = str(asset.get("canonical_relpath") or asset.get("relative_path") or "")
    if not relative:
        return ""
    try:
        physical = Path(root).joinpath(*Path(relative.replace("\\", "/")).parts)
        return storage.directory_path_key(str(physical))
    except (CleanroomException, OSError, ValueError):
        return ""


def _path_key_within_any(candidate_key, parent_keys):
    candidate = str(candidate_key or "")
    for raw_parent in parent_keys:
        parent = str(raw_parent or "").rstrip("\\/")
        if not parent:
            continue
        if candidate == parent:
            return True
        separator = "\\" if "\\" in parent else "/"
        if candidate.startswith(parent + separator):
            return True
    return False


def asset_hidden_by_mounted_root(asset, hidden_root_keys, aliases=None):
    """按规范物理路径判断资产是否落在隐藏挂载根或其后代中。"""
    if not hidden_root_keys:
        return False
    path_key = asset_path_key(asset, aliases)
    return bool(path_key and _path_key_within_any(path_key, hidden_root_keys))


def root_facets():
    """返回可筛选来源及资产数，不暴露服务器绝对路径。

    QIN-596：同一路径的允许根身份（root_xxx）与登记来源身份（local_extra_*）
    按 ``storage.physical_path_key(词法根/前缀)`` 合并为一条 facet：主 ID 取
    登记来源身份优先；aliases 携带组内全部身份；计数按组内物理位置键去重，
    不重复相加。root=None 的离线/移除别名不参与合并，保持单身份 facet。
    """
    from gw.asset_registry import repository as repo

    try:
        aliases, alias_status = _cached_root_snapshot()
    except CleanroomException:
        return []
    from gw.settings.service import default_storage_settings_service

    hidden_root_keys = getattr(default_storage_settings_service.get_snapshot(), "hidden_asset_root_keys", [])
    # QIN-604：root_facets 是 /assets 每次请求的下游读点，改走快照后整段只读一次。
    raw = repo.readonly_registry_snapshot()

    # ── 分组：物理路径可解析的别名按词法物理键合并；其余保持单身份。
    groups: dict[tuple, dict] = {}
    identity_group: dict[str, tuple] = {}
    for source_id, alias in sorted(aliases.items()):
        physical_root = alias.get("root")
        group_key = None
        if physical_root is not None:
            prefix = str(alias.get("prefix") or "")
            physical = (
                physical_root.joinpath(*Path(prefix).parts)
                if prefix
                else physical_root
            )
            try:
                group_key = ("p", storage.physical_path_key(str(physical)))
            except CleanroomException:
                group_key = None
        if group_key is None:
            group_key = ("i", source_id)
        group = groups.get(group_key)
        if group is None:
            group = groups[group_key] = {"ids": []}
        group["ids"].append(source_id)
        identity_group[source_id] = group_key

    # ── 计数：身份→组归属；组内以规范化物理位置键去重（词法根/relpath）。
    # 身份无组时保留身份级计数，供 removed 兜底环使用（口径与旧实现一致）。
    root_map = {
        rid: entry["root"]
        for rid, entry in aliases.items()
        if entry.get("root") is not None and str(entry.get("root_id") or "") == rid
    }
    counts_by_identity = {}
    counts_by_group = {}
    seen_by_group = {}
    for asset in raw["assets"].values():
        if not repo._visible(asset):
            continue
        source_id = str(asset.get("storage_root_id") or asset.get("root_id") or "")
        if not source_id:
            continue
        counts_by_identity[source_id] = counts_by_identity.get(source_id, 0) + 1
        group_key = identity_group.get(source_id)
        if group_key is None:
            continue
        relpath = str(asset.get("canonical_relpath") or "")
        asset_root = root_map.get(str(asset.get("root_id") or ""))
        if asset_root is not None and relpath:
            physical = asset_root.joinpath(*Path(relpath).parts)
            try:
                dedup_key = ("p", storage.physical_path_key(str(physical)))
            except CleanroomException:
                dedup_key = ("f", str(asset.get("asset_id") or ""),)
        else:
            # 无物理位置信息（无 relpath 或根已移除）的记录按记录级键回退：
            # 逐条计数，不与物理键混用（避免同根无 relpath 记录被误合并）。
            dedup_key = ("f", str(asset.get("asset_id") or ""),)
        seen = seen_by_group.setdefault(group_key, set())
        if dedup_key in seen:
            continue
        seen.add(dedup_key)
        counts_by_group[group_key] = counts_by_group.get(group_key, 0) + 1

    # ── facet 组装：主 ID=登记来源身份优先；可用性取组内最受限；名称优先
    # 登记来源的用户命名。
    availability_rank = {"removed": 0, "invalid": 1, "offline": 2, "available": 3}
    facets = {}
    for group_key, group in groups.items():
        ids = group["ids"]
        registered = [
            sid
            for sid in ids
            if str(aliases[sid].get("storage_root_id") or "") == sid
            and str(aliases[sid].get("root_id") or "") != sid
        ]
        primary = registered[0] if registered else ids[0]
        best_rank = None
        best = ("available", "", "")
        for sid in ids:
            alias = aliases[sid]
            status = alias_status.get(sid)
            availability = (
                str(status["availability"])
                if status
                else str(alias.get("availability") or "available")
            )
            reason = str(alias.get("availability_reason") or "")
            message = str(alias.get("availability_message") or "")
            rank = availability_rank.get(availability, 1)
            if best_rank is None or rank < best_rank:
                best_rank = rank
                best = (availability, reason, message)
        name = ""
        for sid in ids:
            alias = aliases[sid]
            if (
                str(alias.get("storage_root_id") or "") == sid
                and str(alias.get("root_id") or "") != sid
            ):
                name = str(alias.get("name") or "")
                break
        if not name:
            name = str(aliases[ids[0]].get("name") or "")
        facets[primary] = {
            "id": primary,
            "name": name,
            "count": counts_by_group.get(group_key, 0),
            "availability": best[0],
            "availability_reason": best[1],
            "availability_message": best[2],
            "aliases": list(ids),
            "assets_hidden": any(
                _path_key_within_any(path_key, hidden_root_keys)
                for path_key in _root_group_path_keys(ids, aliases)
            ),
        }
    removed_root_ids = {
        str(record.get("id") or "")
        for record in storage._allowed_roots_state().get("roots", [])
        if not record.get("active")
    }
    for source_id, count in counts_by_identity.items():
        if source_id in removed_root_ids and source_id not in facets:
            facets[source_id] = {
                "id": source_id,
                "name": source_id,
                "count": count,
                "availability": "removed",
                "availability_reason": "MOUNT_ROOT_REMOVED",
                "availability_message": "允许根已移除；已有索引与源文件保留",
                "assets_hidden": any(
                    _path_key_within_any(path_key, hidden_root_keys)
                    for path_key in _root_group_path_keys({source_id}, aliases)
                ),
            }
    return sorted(facets.values(), key=lambda item: item["id"])


def _admission(actor):
    """只检查服务器本地可撤销授权；不把会话有效冒称远端IdP实时授权。"""
    runtime = load_runtime_auth_config()
    if runtime.mode != actor["mode"]:
        _error(403, "INDEX_AUTH_CHANGED", "认证配置已改变，任务停止")
    if actor["mode"] == "local_account":
        from gw.core.local_accounts import database

        with database() as db:
            row = db.execute(
                "SELECT role FROM local_users WHERE user_id=?", (actor["subject"],)
            ).fetchone()
            live = db.execute(
                "SELECT 1 FROM local_sessions WHERE user_id=? "
                "AND (expires_at IS NULL OR expires_at>?) LIMIT 1",
                (actor["subject"], time.time()),
            ).fetchone()
        if not row or row["role"] not in EDIT_ROLES or not live:
            _error(403, "INDEX_AUTH_REVOKED", "发起账户写权限或会话已失效")
    elif actor["mode"] == "oidc":
        from gw.core import session

        if actor["domain"] != "oidc:" + runtime.oidc.issuer:
            _error(403, "INDEX_AUTH_CHANGED", "身份域已改变")
        if not any(
            principal.get("role") in EDIT_ROLES
            for principal in session.get_sessions_for_subject(actor["subject"])
        ):
            _error(
                403, "INDEX_SESSION_REQUIRED", "后台索引需要持续有效的服务端登录会话"
            )
    elif actor["role"] not in EDIT_ROLES:
        _error(403, "INDEX_AUTH_REVOKED", "发起主体无写权限")


def _merge_generation_metadata(current: object, scanned: object) -> object:
    """按 QIN-601 设计表 §2.6 合并 reindex 扫描到的 `generation`。

    - 现值 `comfyui.source != "gw-workflow"`：按 QIN-600 原规则整体替换。
    - 现值 `comfyui.source == "gw-workflow"`：来源不被文件内嵌元数据翻回
      `embedded`，`has_*` 与扫描结果取或；其余字段「扫描有值则替换」，
      扫描没给出 `prompt` 时保留 GW 写入的提示词（Q4）。
    """
    if not isinstance(scanned, dict):
        return current
    if not isinstance(current, dict):
        return scanned
    current_marker = current.get("comfyui")
    if not isinstance(current_marker, dict) or current_marker.get("source") != "gw-workflow":
        return scanned
    merged = dict(current)
    merged.update({key: value for key, value in scanned.items() if key != "comfyui"})
    scanned_marker = scanned.get("comfyui")
    marker = dict(current_marker)
    if isinstance(scanned_marker, dict):
        marker["has_workflow"] = bool(current_marker.get("has_workflow")) or bool(
            scanned_marker.get("has_workflow")
        )
        marker["has_prompt_graph"] = bool(
            current_marker.get("has_prompt_graph")
        ) or bool(scanned_marker.get("has_prompt_graph"))
    marker["source"] = "gw-workflow"
    merged["comfyui"] = marker
    return merged


# 状态文件瞬时共享冲突的有界重试窗口。Windows 上 Python 的读句柄默认不带
# FILE_SHARE_DELETE，轮询读取与 storage.save 的 os.replace 交错时两侧都会抛
# PermissionError（读 errno=13 / 写 winerror=5），同一个冲突窗口既能造成
# INDEX_STORE_INVALID(503)，也能让阶段落盘失败把任务误标 INDEX_EXECUTION_FAILED。
# 与 gw/media/repository.py 的 _PREVIEW_ALL_WRITE_ATTEMPTS 同口径：5 次尝试、
# 退避 20/40/60/80ms（合计 ≤200ms）。退避期间不持有文件锁，见 _retry_io。
STATE_IO_ATTEMPTS = 5
STATE_IO_BACKOFF_SECONDS = 0.02

# 退避的间接调用点：退避发生在「本次 I/O 已抛错、文件锁已释放、下一次尝试未发起」
# 的边界上，测试可在此同步等待，不必靠时间窗口猜测。
_STATE_IO_SLEEP = time.sleep

# 事务锁按状态文件绝对路径键控，覆盖「读-改-写 + 退避」的整个事务。
# 与 storage.namespace_lock 分层的理由：JsonState.mutate() 原本在整个事务期间持
# 命名空间锁，退避 sleep 会连带阻塞同命名空间的读者（实测 60-90ms）。这里把
# 「事务串行」与「单次 I/O 互斥」拆开：事务锁只在写事务之间串行，文件锁只包
# 单次 I/O，退避期间两把锁都不需要文件锁。锁序固定 事务锁 → 文件锁。
_STATE_TX_LOCKS: dict[str, threading.RLock] = {}
_STATE_TX_GUARD = threading.Lock()


def _transaction_lock(path) -> threading.RLock:
    """按状态文件绝对路径取事务锁；调用时解析，便于测试包装后仍生效。"""
    key = os.path.abspath(str(path))
    with _STATE_TX_GUARD:
        lock = _STATE_TX_LOCKS.get(key)
        if lock is None:
            lock = threading.RLock()
            _STATE_TX_LOCKS[key] = lock
        return lock


class _JobState(storage.JsonState):
    """任务真源损坏时失败关闭，不能回退空表复用稳定ID。"""

    def _retry_io(self, operation):
        """执行一次 I/O，仅对瞬时共享冲突做有界重试。

        每次尝试单独持有文件锁，退避在锁外进行：等待期间同命名空间的读者不会
        被阻塞。重试只包住单次读写，不重跑 mutator：_recover 的 restore 每次
        执行都会自增 version，在事务外层重试会造成版本双增。非共享冲突类错误
        （含非法 JSON、结构非法、永久权限问题）一次即抛，保持失败关闭。
        """
        for attempt in range(STATE_IO_ATTEMPTS):
            try:
                with storage.namespace_lock(self.namespace):
                    return operation()
            except PermissionError:
                if attempt + 1 >= STATE_IO_ATTEMPTS:
                    raise
                _STATE_IO_SLEEP(STATE_IO_BACKOFF_SECONDS * (attempt + 1))
        raise AssertionError("unreachable")  # pragma: no cover

    def read(self):
        path = storage.file_for(self.namespace)

        def load_state():
            if not path.exists():
                return {"sequence": 0, "jobs": {}, "keys": {}}
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
                if (
                    type(raw["sequence"]) is not int
                    or not isinstance(raw["jobs"], dict)
                    or not isinstance(raw["keys"], dict)
                ):
                    raise ValueError()
                return raw
            except PermissionError:
                # 瞬时共享冲突交给 _retry_io 有界重试，不在此降级成存储损坏。
                raise
            except (OSError, ValueError, KeyError, TypeError):
                _error(503, "INDEX_STORE_INVALID", "索引任务持久状态不可读取")

        try:
            return self._retry_io(load_state)
        except PermissionError:
            # 重试预算内仍被占用：明确报存储不可读，不回落空状态复用稳定 ID。
            _error(503, "INDEX_STORE_INVALID", "索引任务持久状态不可读取")

    def _write_locked(self, payload) -> None:
        """调用方已持事务锁时的落盘体；含单次 I/O 及其有界重试预算。"""
        self._retry_io(lambda: storage.save(self.namespace, payload))

    def write(self, payload) -> None:
        """整份快照替换：事务锁包住单次落盘及其重试预算。

        这是整体替换语义，不合并、不基于盘上状态重算；若它与 mutate 并发，
        后提交者按先到先得整体覆盖，不属于 lost update（那是读-改-写事务之间
        的概念）。现有调用点均在 IndexJobs 的任务锁内构造 payload。
        """
        with _transaction_lock(storage.file_for(self.namespace)):
            self._write_locked(payload)

    def mutate(self, mutator):
        """事务锁内完成读-改-写，退避期间不持文件锁。

        不能直接复用 JsonState.mutate()：它把命名空间锁跨在整个事务上，
        重试退避的 sleep 会连带阻塞同命名空间的读者。这里事务锁只串行写事务，
        文件锁逐次获取。每个事务恰好获取一次事务锁（写入体不再重复获取），
        mutator 恰好执行一次，锁内不重读，不会覆盖并发提交。
        """
        with _transaction_lock(storage.file_for(self.namespace)):
            state = self.read()
            result = mutator(state)
            self._write_locked(state)
            return result


class IndexJobs:
    """进度独立持久化；取消/暂停和注册表提交在同一线程锁内线性化。"""

    def __init__(self):
        self.root = storage.data_root().resolve()
        self.store = _JobState(
            "registry_index_jobs", lambda: {"sequence": 0, "jobs": {}, "keys": {}}
        )
        self.lock = threading.RLock()
        self.condition = threading.Condition(self.lock)
        self.executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="gw-index")
        self._root_snapshot_cache = {}
        self._runtime_path_configuration = _runtime_path_configuration()
        self.closed = False
        self._recover()

    def _recover(self):
        from gw.asset_registry import repository as repo

        # QIN-604：索引服务初始化只读收据，走快照避免 /status 首次请求额外全表解析。
        snapshot = repo.readonly_registry_snapshot()
        receipts = snapshot.get("index_job_receipts", {})

        def restore(raw):
            for job in raw["jobs"].values():
                receipt = receipts.get(f"{job['job_id']}:{job['attempt']}")
                if receipt:
                    if job["status"] == "cancelled":
                        # 已取消任务不得因残留收据在重启时被恢复成成功；
                        # 快照条目本身保留，取消语义优先。
                        continue
                    if isinstance(receipt, dict) and receipt.get("enrich_pending"):
                        # QIN-652：进程在富化阶段退出时仍按既有「有收据即成功」
                        # 口径恢复，但如实标注富化未完成，retry 按跳过条件补齐。
                        receipt = dict(receipt)
                        receipt.pop("enrich_pending", None)
                        enrich = dict(receipt.get("enrich") or job.get("enrich") or {})
                        enrich["interrupted"] = True
                        receipt["enrich"] = enrich
                    job.update(
                        status="succeeded",
                        phase="completed",
                        result=receipt,
                        error_code=None,
                    )
                elif job["status"] not in TERMINAL:
                    job.update(
                        status="interrupted",
                        phase="interrupted",
                        error_code="INDEX_PROCESS_INTERRUPTED",
                    )
                else:
                    continue
                job["version"] += 1
                job["updated_at"] = storage.now_iso()

        self.store.mutate(restore)
        for operation_id in list((self.store.read().get("registration_ops") or {}).keys()):
            try:
                self._settle_resource_folder_registration(operation_id)
            except Exception:
                # 补偿失败时保留日志；下一次服务重启继续按收据和归属恢复。
                continue

    def shutdown(self):
        with self.condition:
            self.closed = True
            self.condition.notify_all()
        self.executor.shutdown(wait=True, cancel_futures=True)

    def active_job_count(self):
        """活动索引任务数；供迁移前确认无后台复制/写入。"""
        raw = self.store.read()
        jobs = raw.get("jobs") or {}
        return sum(
            1
            for job in jobs.values()
            if str(job.get("status") or "") in {"queued", "running", "paused"}
        )

    def _root_snapshot_key(self):
        """廉价探测根配置快照是否改变，避免每个 NAS 文件都重新 resolve 全部根。"""
        try:
            state_files = []
            for namespace in ("storage_settings", "asset_registry", "allowed_roots"):
                path = self.root / f"{namespace}.json"
                try:
                    info = path.stat()
                except FileNotFoundError:
                    state_files.append(None)
                else:
                    state_files.append((info.st_mtime_ns, info.st_size))
            return _digest(
                {
                    "allowed_roots": os.environ.get(storage.ALLOWED_ROOTS_ENV, ""),
                    "state_files": state_files,
                }
            )
        except (CleanroomException, OSError, ValueError):
            return None

    def _check(self, job):
        if (
            self.closed
            or _runtime_path_configuration() != self._runtime_path_configuration
        ):
            _error(409, "INDEX_PROCESS_INTERRUPTED", "任务执行环境已停止")
        _admission(job["_actor"])
        from gw.asset_registry import repository as repo

        request_meta = job.get("_request") or {}
        job_ids = {str(rid) for rid in request_meta.get("roots") or [] if rid}
        for scope in request_meta.get("scopes") or []:
            if scope.get("root_id"):
                job_ids.add(str(scope["root_id"]))
            if scope.get("storage_root_id"):
                job_ids.add(str(scope["storage_root_id"]))

        cache_key = self._root_snapshot_key()
        cached = self._root_snapshot_cache.get(cache_key) if cache_key else None
        if cached is None:
            data_root = getattr(self, "root", None) or repo._index_scan_runtime_data_root()
            purging_ids, _ = repo._purging_root_ids_and_keys(data_root=data_root)
            removed_ids = repo._removed_root_ids(data_root=data_root) if data_root is not None else set()
            blocked_ids = purging_ids | removed_ids
            if job_ids & blocked_ids:
                _error(403, "INDEX_ROOTS_CHANGED", "允许根或来源配置已移除或正在清除")
            canonical, aliases = _roots()
            fingerprint = _root_configuration_fingerprint(aliases)
            legacy_fingerprint = _legacy_root_configuration_fingerprint(aliases)
            if cache_key:
                self._root_snapshot_cache[cache_key] = (
                    fingerprint,
                    legacy_fingerprint,
                    canonical,
                    blocked_ids,
                )
                if len(self._root_snapshot_cache) > MAX_ACTIVE:
                    self._root_snapshot_cache.pop(next(iter(self._root_snapshot_cache)))
        else:
            if len(cached) == 4:
                fingerprint, legacy_fingerprint, canonical, blocked_ids = cached
            else:
                fingerprint, legacy_fingerprint, canonical = cached
                blocked_ids = set()
            if job_ids & blocked_ids:
                _error(403, "INDEX_ROOTS_CHANGED", "允许根或来源配置已移除或正在清除")
        if job["_fingerprint"] not in {fingerprint, legacy_fingerprint}:
            _error(403, "INDEX_ROOTS_CHANGED", "允许根或来源配置已改变")
        return canonical

    def cancel_roots(self, root_ids, paths=()):
        """QIN-598：移出、注销来源或清除索引时立即取消相关在途索引任务。"""
        target_ids = {str(item).strip() for item in (root_ids or []) if str(item).strip()}
        target_keys = set()
        for raw_path in paths or ():
            if not raw_path:
                continue
            try:
                target_keys.add(storage.directory_path_key(str(raw_path)))
            except Exception:
                pass
        if not target_ids and not target_keys:
            return []

        def _path_matches_target(raw_path: str) -> bool:
            if not raw_path or not target_keys:
                return False
            try:
                cand_key = storage.directory_path_key(str(raw_path))
            except Exception:
                return False
            for tkey in target_keys:
                if cand_key == tkey:
                    return True
                prefix = tkey.rstrip("\\/")
                sep = "\\" if "\\" in tkey else "/"
                if cand_key.startswith((prefix + sep) if prefix else sep):
                    return True
            return False

        cancelled_ids = []
        with self.condition:
            raw = self.store.read()
            for job_id, job in raw.get("jobs", {}).items():
                if not isinstance(job, dict) or job.get("status") in TERMINAL:
                    continue
                req = job.get("_request") or {}
                req_roots = {str(x) for x in req.get("roots") or [] if x}
                scopes = req.get("scopes") or []
                scope_ids = {
                    str(scope.get(key) or "")
                    for scope in scopes
                    if isinstance(scope, dict)
                    for key in ("root_id", "storage_root_id")
                    if scope.get(key)
                }
                scope_paths = [
                    str(scope.get("scan_path") or "")
                    for scope in scopes
                    if isinstance(scope, dict) and scope.get("scan_path")
                ]
                matched = bool((req_roots | scope_ids) & target_ids) or bool(
                    req.get("legacy_identity_scan_complete")
                ) or any(_path_matches_target(p) for p in scope_paths)
                if not matched:
                    continue
                job["status"] = "cancelled"
                job["phase"] = "cancelled"
                job["error_code"] = "INDEX_ROOT_REMOVED"
                job["version"] = int(job.get("version", 1)) + 1
                job["updated_at"] = storage.now_iso()
                cancelled_ids.append(job_id)
            if cancelled_ids:
                self.store.write(raw)
            self._root_snapshot_cache.clear()
            self.condition.notify_all()
        invalidate_root_facets_cache()
        return cancelled_ids

    def _job(self, job_id, auth):
        job = self.store.read()["jobs"].get(job_id)
        if not job or job["_owner"] != _owner(_actor(auth)):
            _error(404, "WORKSPACE_JOB_NOT_FOUND", "工作区任务不存在")
        return self._committed_truth(job)

    def _receipt_from_registry(self, job):
        """按任务记录的工作区落点读取业务收据；不重新核验，避免失败读被门禁误伤。"""
        from gw.asset_registry import repository as repo

        stored = workspace_context.context_from_job(job)
        token = (
            workspace_context.set_execution_context(stored) if stored is not None else None
        )
        try:
            return (
                repo.state()
                .read()
                .get("index_job_receipts", {})
                .get(f"{job['job_id']}:{job['attempt']}")
            )
        finally:
            if token is not None:
                workspace_context.reset_execution_context(token)

    def _committed_truth(self, job):
        """业务提交收据高于任务快照；写状态失败也绝不开放取消或重试。"""
        receipt = self._receipt_from_registry(job)
        # QIN-652：快扫收据只代表「基础条目已入库」，富化仍在后台运行。若按
        # 既有口径把它投影成 succeeded，下一次 _checkpoint 会抛 _Stopped 打断
        # 富化，pause/cancel 也会被终态拒绝；只有终态收据才沿用既有投影。
        if (
            receipt is not None
            and job["status"] not in ("succeeded", "cancelled")
            and not (isinstance(receipt, dict) and receipt.get("enrich_pending"))
        ):
            job.update(
                status="succeeded",
                phase="completed",
                result=receipt,
                error_code=None,
            )
            job["version"] += 1
            # 状态盘仍故障时按已持久化收据投影成功；动作判断使用同一投影。
            with suppress(OSError):
                self._save(job)
        return job

    def _save(self, job):
        job["updated_at"] = storage.now_iso()
        self.store.mutate(lambda raw: raw["jobs"].__setitem__(job["job_id"], job))

    @staticmethod
    def public(job):
        public = {k: v for k, v in job.items() if not k.startswith("_")}
        status = str(job.get("status") or "unknown")
        error_code = str(job.get("error_code") or "")
        if status == "succeeded":
            projection = {
                "data_status": "ok",
                "data_gaps": [],
                "next_action": "review_result",
                "result_unknown": False,
            }
        elif status in {"queued", "running"}:
            projection = {
                "data_status": "degraded",
                "data_gaps": ["INDEX_PENDING"],
                "next_action": "poll_job",
                "result_unknown": False,
            }
        elif status == "paused":
            projection = {
                "data_status": "degraded",
                "data_gaps": ["INDEX_PAUSED"],
                "next_action": "resume_index",
                "result_unknown": False,
            }
        elif status == "failed":
            projection = {
                "data_status": "degraded",
                "data_gaps": [error_code or "INDEX_EXECUTION_FAILED"],
                "next_action": "retry_index",
                "result_unknown": False,
            }
        elif status == "cancelled":
            projection = {
                "data_status": "degraded",
                "data_gaps": [error_code or "INDEX_CANCELLED"],
                "next_action": "retry_index",
                "result_unknown": False,
            }
        elif status == "interrupted":
            projection = {
                "data_status": "degraded",
                "data_gaps": [error_code or "INDEX_PROCESS_INTERRUPTED"],
                "next_action": "reconcile_index_job",
                "result_unknown": True,
            }
        else:
            projection = {
                "data_status": "degraded",
                "data_gaps": ["INDEX_STATUS_UNKNOWN"],
                "next_action": "reconcile_index_job",
                "result_unknown": True,
            }
        public.update(projection)
        public["has_error"] = bool(job.get("error_code"))
        public["progress"] = {
            "scanned": job["scanned"],
            "error_count": job["error_count"],
            "phase": job.get("phase")
            or {
                "queued": "queued",
                "running": "scanning",
                "succeeded": "completed",
                "failed": "failed",
                "cancelled": "cancelled",
                "interrupted": "interrupted",
            }.get(status, status),
        }
        return public

    def detail(self, job_id, auth):
        with self.lock:
            job = self._job(job_id, auth)
            writable = auth.role in EDIT_ROLES
            actions = {
                a: writable
                and job["status"] in states
                and (a != "retry" or job["attempt"] < 5)
                for a, states in ACTIONS.items()
            }
            view = self.public(job)
            return {
                "job": view,
                "actions": actions,
                "action_reasons": {
                    a: "当前状态或权限不允许此操作"
                    for a, ok in actions.items()
                    if not ok
                },
                "data_status": view["data_status"],
                "data_gaps": view["data_gaps"],
                "next_action": view["next_action"],
                "result_unknown": view["result_unknown"],
            }

    def listing(self, auth):
        with self.lock:
            owner = _owner(_actor(auth))
            jobs = [
                self.public(self._committed_truth(j))
                for j in self.store.read()["jobs"].values()
                if j["_owner"] == owner
            ]
            return sorted(jobs, key=lambda j: j["created_at"], reverse=True)[:100]

    def _settle_resource_folder_registration(self, operation_id: str, *, committed: bool = False):
        """按登记归属收尾；无收据时只撤回仍未被其他任务使用的新增。"""
        from gw.asset_registry import repository as repo
        from gw.settings.service import (
            default_storage_allowed_roots_service,
            default_storage_settings_service,
        )

        with self.condition:
            raw = self.store.read()
            operation = (raw.get("registration_ops") or {}).get(operation_id)
            if not isinstance(operation, dict):
                return
            receipt_id = f"{operation.get('job_id')}:{operation.get('attempt', 1)}"
            receipt = repo.resource_folder_registration_receipt(
                repo.readonly_registry_snapshot(), receipt_id
            )
            committed = committed or bool(
                receipt and receipt.get("operation_id") == operation_id
            )
            source_id = str(operation.get("source_id") or "")
            root_id = str(operation.get("root_id") or "")
            path = str(operation.get("path") or "")
            root_path = str(operation.get("root_path") or path)
            source_used = False
            root_used = False
            if committed:
                if source_id:
                    default_storage_settings_service.finish_resource_folder_registration(
                        source_id, path, operation_id, rollback=False
                    )
                if root_id:
                    default_storage_allowed_roots_service.finish_resource_folder_registration(
                        root_id, root_path, operation_id, rollback=False
                    )
            else:
                registry = repo.state().read()
                assets = registry.get("assets", {})
                jobs = raw.get("jobs", {})
                source_used = any(
                    str(asset.get("storage_root_id") or "") == source_id
                    for asset in assets.values()
                    if isinstance(asset, dict)
                )
                root_used = any(
                    str(asset.get("root_id") or "") == root_id
                    for asset in assets.values()
                    if isinstance(asset, dict)
                )
                for other_id, job in jobs.items():
                    if other_id == operation.get("job_id") or not isinstance(job, dict):
                        continue
                    request = job.get("_request") or {}
                    scopes = request.get("scopes") or []
                    ids = {str(item) for item in request.get("roots") or [] if item}
                    for scope in scopes:
                        if isinstance(scope, dict):
                            ids.update(
                                str(scope.get(key))
                                for key in ("root_id", "storage_root_id")
                                if scope.get(key)
                            )
                    source_used = source_used or source_id in ids
                    root_used = root_used or root_id in ids
                settings = default_storage_settings_service._state.read()
                root_key = storage.directory_path_key(root_path) if root_path else ""
                separator = "\\" if "\\" in root_key else "/"
                prefix = root_key.rstrip("\\/") + separator if root_key else ""
                for entry in settings.get("extra_local_entries") or []:
                    if not isinstance(entry, dict) or str(entry.get("id") or "") == source_id:
                        continue
                    entry_path = str(entry.get("path") or "")
                    try:
                        entry_key = storage.directory_path_key(entry_path)
                    except CleanroomException:
                        continue
                    if root_key and (entry_key == root_key or entry_key.startswith(prefix)):
                        root_used = True
                for other_id, other in (raw.get("registration_ops") or {}).items():
                    if other_id != operation_id and isinstance(other, dict):
                        if other.get("path_key") == operation.get("path_key"):
                            source_used = root_used = True
                root_state = default_storage_allowed_roots_service._state.read()
                if any(
                    str(record.get("id") or "") == root_id
                    and storage.directory_path_key(str(record.get("path") or ""))
                    == storage.directory_path_key(root_path)
                    and bool(record.get("aliases"))
                    for record in root_state.get("roots", [])
                ):
                    root_used = True
                if source_used:
                    # 活跃任务可能只引用来源 ID，尚未产生带 root_id 的资产记录。
                    # 保留本次新建的允许根，避免来源仍在但扫描根被误撤回。
                    root_used = True

                if operation.get("created_source") and source_id:
                    default_storage_settings_service.finish_resource_folder_registration(
                        source_id, path, operation_id, rollback=not source_used
                    )
                if operation.get("created_root") and root_id:
                    default_storage_allowed_roots_service.finish_resource_folder_registration(
                        root_id, root_path, operation_id, rollback=not root_used
                    )

            def finish(raw_state):
                registrations = raw_state.setdefault("registration_ops", {})
                registrations.pop(operation_id, None)
                job = raw_state.get("jobs", {}).get(str(operation.get("job_id") or ""))
                if isinstance(job, dict) and isinstance(job.get("_resource_folder_registration"), dict):
                    rolled_back_any = (
                        (bool(operation.get("created_source")) and not source_used)
                        or (bool(operation.get("created_root")) and not root_used)
                    )
                    job["_resource_folder_registration"]["settled"] = (
                        "committed" if committed else "rolled_back" if rolled_back_any else "preserved"
                    )

            self.store.mutate(finish)

    def submit(self, kind, payload, auth, key, *, registration=None, _registration_context=None):
        # QIN-627 L5：写窗口关闭后不再接受新的后台索引任务。
        from gw.core.write_window import guard_write

        guard_write()
        if not isinstance(key, str) or not 1 <= len(key) <= 160:
            _error(
                400,
                "INDEX_IDEMPOTENCY_KEY_REQUIRED",
                "后台索引需要不超过160字符的幂等键",
            )
        actor = _actor(auth)
        _admission(actor)
        # QIN-628：真实索引任务提交时必须解析当前工作区/租约与执行端映射；
        # 失败即拒绝入队，不留下任务与半注册。
        execution_context = default_workspace_service.build_execution_context()
        if registration is not None:
            from gw.settings.models import StorageAllowedRootRequest
            from gw.settings.service import (
                default_storage_allowed_roots_service,
                default_storage_settings_service,
            )

            source_id = str(registration.get("id") or "").strip()
            source_name = str(registration.get("name") or "").strip()
            raw_path = registration.get("path")
            authorize_root = registration.get("authorize_root") is True
            expected_version = registration.get("expected_version")
            if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", source_id):
                _error(400, "INVALID_REQUEST", "素材来源标识无效")
            if not source_name or len(source_name) > 32:
                _error(400, "INVALID_REQUEST", "素材来源名称必须为 1 到 32 个字符")
            if type(expected_version) is not int or expected_version < 1:
                _error(400, "EXPECTED_VERSION_REQUIRED", "登记素材来源必须携带存储设置版本")
            rejection = storage.describe_resource_folder_rejection(raw_path)
            if rejection:
                _error(403, "RESOURCE_FOLDER_NOT_PERMITTED", rejection)
            normalized_path = storage.normalize_directory_path(raw_path)
            availability = storage.probe_mount(normalized_path)
            if not availability["available"]:
                _error(
                    503,
                    "RESOURCE_FOLDER_BACKEND_UNAVAILABLE",
                    "当前文件夹后端不可访问，需授权（共享）给后端",
                )
            try:
                normalized_path = str(Path(normalized_path).resolve(strict=True))
            except (OSError, RuntimeError, ValueError):
                _error(
                    503,
                    "RESOURCE_FOLDER_BACKEND_UNAVAILABLE",
                    "当前文件夹后端不可访问，需授权（共享）给后端",
                )
            path_key = storage.directory_path_key(normalized_path)
            owner = _owner(actor)
            key_digest = _digest([owner, key])
            op_id = _digest(["resource-folder-registration", owner, key])

            with self.condition:
                current = self.store.read()
                previous_id = current["keys"].get(key_digest)
                if previous_id:
                    previous = current["jobs"].get(previous_id)
                    previous_registration = (previous or {}).get("_resource_folder_registration") or {}
                    if not previous or previous_registration.get("path_key") != path_key:
                        _error(409, "INDEX_IDEMPOTENCY_CONFLICT", "幂等键已用于不同索引请求")
                    return self._accepted(self._committed_truth(previous))
                for active in current["jobs"].values():
                    active_registration = active.get("_resource_folder_registration") or {}
                    if (
                        active.get("_owner") == owner
                        and active.get("status") not in TERMINAL
                        and active_registration.get("path_key") == path_key
                    ):
                        current["keys"][key_digest] = active["job_id"]
                        self.store.write(current)
                        accepted = self._accepted(active)
                        accepted["registered_root"] = {
                            "id": active_registration.get("source_id"),
                            "name": active_registration.get("source_name"),
                        }
                        return accepted
                if (
                    len(current["jobs"]) >= MAX_JOBS
                    or sum(job["status"] not in TERMINAL for job in current["jobs"].values()) >= MAX_ACTIVE
                ):
                    _error(429, "INDEX_QUEUE_FULL", "索引任务容量已满")

                settings_snapshot = default_storage_settings_service.get_snapshot()
                existing_source = next(
                    (
                        item for item in settings_snapshot.local_libraries
                        if item.get("path")
                        and storage.directory_path_key(str(item["path"])) == path_key
                    ),
                    None,
                )
                if existing_source:
                    source_id = str(existing_source["id"])
                    source_name = str(existing_source.get("name") or source_name)
                    normalized_path = str(existing_source.get("path") or normalized_path)
                    path_key = storage.directory_path_key(normalized_path)
                else:
                    conflicting_id = next(
                        (item for item in settings_snapshot.local_libraries if str(item.get("id")) == source_id),
                        None,
                    )
                    if conflicting_id:
                        _error(409, "ASSET_ROOT_ID_CONFLICT", "素材来源标识已用于其他目录")

                created_root = False
                try:
                    root, prefix = storage.resolve_asset_registration_root(normalized_path)
                except CleanroomException as exc:
                    if exc.code not in {"PATH_OUTSIDE_ALLOWED_ROOTS", "LOCAL_FILE_ACCESS_NOT_ADMITTED"} or not authorize_root:
                        raise
                    if str(actor.get("role") or "").lower() != "admin":
                        _error(403, "FORBIDDEN", "仅管理员可授权新的资源文件夹根")
                    if existing_source is None and expected_version != settings_snapshot.revision:
                        _error(409, "VERSION_CONFLICT", "存储设置版本已改变，请重试")
                    if _restore_matching_removed_root(normalized_path):
                        root, prefix = storage.resolve_asset_registration_root(normalized_path)
                        settings_snapshot = default_storage_settings_service.get_snapshot()
                        existing_source = next(
                            (
                                item for item in settings_snapshot.local_libraries
                                if item.get("path")
                                and storage.directory_path_key(str(item["path"])) == path_key
                            ),
                            None,
                        )
                        if existing_source:
                            source_id = str(existing_source["id"])
                            source_name = str(existing_source.get("name") or source_name)
                            normalized_path = str(existing_source.get("path") or normalized_path)
                            path_key = storage.directory_path_key(normalized_path)
                        else:
                            expected_version = settings_snapshot.revision
                            if any(str(item.get("id")) == source_id for item in settings_snapshot.local_libraries):
                                _error(409, "ASSET_ROOT_ID_CONFLICT", "素材来源标识已用于其他目录")
                    else:
                        created_root = True
                        planned_root_id = storage.root_identity_id(Path(normalized_path))
                        planned_job_id = f"job_index_{int(current['sequence']) + 1:08d}"
                        operation = {
                            "operation_id": op_id,
                            "job_id": planned_job_id,
                            "attempt": 1,
                            "owner": owner,
                            "source_id": source_id,
                            "source_name": source_name,
                            "path": normalized_path,
                            "root_path": normalized_path,
                            "path_key": path_key,
                            "root_id": planned_root_id,
                            "created_root": True,
                            "created_source": existing_source is None,
                            "phase": "prepared",
                        }
                        self.store.mutate(
                            lambda state: state.setdefault("registration_ops", {}).__setitem__(op_id, operation)
                        )
                        try:
                            root_snapshot = default_storage_allowed_roots_service.get_snapshot()
                            default_storage_allowed_roots_service.add_root(
                                StorageAllowedRootRequest(path=normalized_path, expected_version=root_snapshot.revision),
                                registration_operation_id=op_id,
                            )
                            root, prefix = storage.resolve_asset_registration_root(normalized_path)
                        except Exception:
                            self._settle_resource_folder_registration(op_id)
                            raise

                if not created_root:
                    planned_root_id = storage.root_identity_id(root)
                    planned_job_id = f"job_index_{int(current['sequence']) + 1:08d}"
                    operation = {
                        "operation_id": op_id,
                        "job_id": planned_job_id,
                        "attempt": 1,
                        "owner": owner,
                        "source_id": source_id,
                        "source_name": source_name,
                        "path": normalized_path,
                        "root_path": str(root),
                        "path_key": path_key,
                        "root_id": planned_root_id,
                        "created_root": False,
                        "created_source": existing_source is None,
                        "phase": "prepared",
                    }
                    self.store.mutate(
                        lambda state: state.setdefault("registration_ops", {}).__setitem__(op_id, operation)
                    )

                registered_path = str(root.joinpath(*prefix.split("/"))) if prefix else str(root)
                if existing_source is None:
                    if expected_version != settings_snapshot.revision:
                        self._settle_resource_folder_registration(op_id)
                        _error(409, "VERSION_CONFLICT", "存储设置版本已改变，请重试")
                    try:
                        updated_settings = default_storage_settings_service.register_resource_folder_entry(
                            {"id": source_id, "kind": source_id, "name": source_name, "path": registered_path},
                            source_name,
                            expected_version,
                            op_id,
                        )
                        registered_path = str(next(
                            (item.get("path") for item in updated_settings.local_libraries if str(item.get("id")) == source_id),
                            registered_path,
                        ))
                    except Exception:
                        self._settle_resource_folder_registration(op_id)
                        raise
                operation["path"] = registered_path
                operation["root_path"] = str(root)
                operation["path_key"] = storage.directory_path_key(registered_path)
                operation["root_id"] = storage.root_identity_id(root)
                operation["source_id"] = source_id
                operation["source_name"] = source_name
                operation["created_source"] = existing_source is None
                operation["phase"] = "registered"
                self.store.mutate(
                    lambda state: state.setdefault("registration_ops", {}).__setitem__(op_id, operation)
                )
                job_payload = {key: value for key, value in payload.items() if key != "register_root"}
                job_payload["roots"] = [source_id]
                context = {
                    "operation_id": op_id,
                    "owner": owner,
                    "source_id": source_id,
                    "source_name": source_name,
                    "path": registered_path,
                    "root_path": str(root),
                    "path_key": storage.directory_path_key(registered_path),
                    "root_id": storage.root_identity_id(root),
                    "created_source": existing_source is None,
                    "created_root": bool(operation.get("created_root")),
                    "authorize_root": authorize_root,
                }
                try:
                    accepted = self.submit(
                        kind, job_payload, auth, key, _registration_context=context
                    )
                except Exception:
                    self._settle_resource_folder_registration(op_id)
                    raise
                if accepted.get("job", {}).get("status") == "failed":
                    self._settle_resource_folder_registration(op_id)
                accepted["registered_root"] = {
                    "id": source_id,
                    "name": source_name,
                }
                return accepted

        canonical, aliases = _roots()
        selected = payload.get("roots")
        if selected is not None and (
            not isinstance(selected, list)
            or not selected
            or len(selected) > 64
            or any(not isinstance(x, str) or x not in aliases for x in selected)
        ):
            _error(400, "INDEX_ROOT_INVALID", "来源必须为已准入的根标识")
        invalid_aliases = [
            aliases[source_id]
            for source_id in (selected or aliases.keys())
            if source_id in aliases and aliases[source_id].get("availability") == "invalid"
        ]
        if invalid_aliases:
            invalid = invalid_aliases[0]
            _error(
                409,
                str(invalid.get("availability_reason") or "MOUNT_INVALID"),
                str(invalid.get("availability_message") or "索引来源路径无效"),
            )
        selected_aliases = (
            [
                alias
                for alias in aliases.values()
                if alias["storage_root_id"] == alias["root_id"]
                and alias.get("availability") not in {"invalid", "removed"}
            ]
            if selected is None
            else [
                aliases[source_id]
                for source_id in selected
                if aliases[source_id].get("availability") != "removed"
            ]
        )
        if not selected_aliases:
            _error(503, "INDEX_SOURCE_NOT_AVAILABLE", "没有可用的已登记索引来源", unavailable=True)
        scopes = [
            {
                "root_id": alias["root_id"],
                "prefix": alias["prefix"],
                "storage_root_id": alias["storage_root_id"],
                **(
                    {"scan_path": str(alias["scan_path"])}
                    if alias.get("scan_path") is not None
                    else {}
                ),
            }
            for alias in selected_aliases
        ]
        # 同一来源可能通过多个别名被重复选中，合并完全相同的扫描范围。
        scopes = list(
            {
                (scope["root_id"], scope["prefix"], scope["storage_root_id"]): scope
                for scope in scopes
            }.values()
        )
        root_ids = sorted({scope["root_id"] for scope in scopes})
        max_files = payload.get("max_files", MAX_FILES)
        if type(max_files) is not int or not 1 <= max_files <= MAX_FILES:
            _error(400, "INDEX_LIMIT_INVALID", "扫描文件数上限无效")
        hash_files = payload.get("hash_files", False)
        if not isinstance(hash_files, bool):
            _error(400, "INDEX_HASH_INVALID", "hash_files必须为布尔值")
        request = {
            "kind": kind,
            "roots": root_ids,
            "scopes": scopes,
            "legacy_identity_scan_complete": selected is None,
            "max_files": max_files,
            "hash_files": hash_files,
        }
        fingerprint = _root_configuration_fingerprint(aliases)
        request_digest = _digest([request, fingerprint])
        key_digest = _digest([_owner(actor), key])
        with self.condition:
            raw = self.store.read()
            previous = raw["keys"].get(key_digest)
            if previous:
                job = self._committed_truth(raw["jobs"][previous])
                if job["_request_digest"] != request_digest:
                    _error(
                        409, "INDEX_IDEMPOTENCY_CONFLICT", "幂等键已用于不同索引请求"
                    )
                return self._accepted(job)
            if (
                len(raw["jobs"]) >= MAX_JOBS
                or sum(j["status"] not in TERMINAL for j in raw["jobs"].values())
                >= MAX_ACTIVE
            ):
                _error(429, "INDEX_QUEUE_FULL", "索引任务容量已满")
            raw["sequence"] += 1
            job_id = f"job_index_{raw['sequence']:08d}"
            job = {
                "job_id": job_id,
                "job_type": (
                    "asset.index"
                    if kind == "reindex"
                    else "asset.shot_vectors"
                    if kind == "shot_vectors"
                    else "asset.index.sync"
                ),
                "name": (
                    "重建素材索引"
                    if kind == "reindex"
                    else "生成镜头视觉向量"
                    if kind == "shot_vectors"
                    else "同步索引差集"
                ),
                "status": "queued",
                "phase": "queued",
                "version": 1,
                "attempt": 1,
                "max_attempts": 5,
                "created_at": storage.now_iso(),
                "updated_at": storage.now_iso(),
                "scanned": 0,
                "error_count": 0,
                "result": None,
                "error_code": None,
                "_actor": actor,
                "_owner": _owner(actor),
                "_fingerprint": fingerprint,
                "_request": request,
                "_request_digest": request_digest,
                "workspace_id": execution_context["workspace_id"],
                "workspace_mode": execution_context["workspace_mode"],
                "data_namespace": execution_context["data_namespace"],
                "execution_machine_id": execution_context["execution_machine_id"],
                "result_root_label": execution_context["result_root_label"],
                "result_root": execution_context["result_root"],
                "logical_source_id": execution_context.get("logical_source_id"),
                "mapping_id": execution_context.get("mapping_id"),
                "client_instance_id": execution_context["client_instance_id"],
            }
            if _registration_context is not None:
                job["_resource_folder_registration"] = dict(_registration_context)
                raw.setdefault("registration_ops", {}).setdefault(
                    str(_registration_context["operation_id"]), {}
                ).update(job_id=job_id, attempt=job["attempt"], phase="accepted")
            raw["jobs"][job_id] = job
            raw["keys"][key_digest] = job_id
            self.store.write(raw)
            self._dispatch(job)
            if _registration_context is not None and job.get("status") == "failed":
                self._settle_resource_folder_registration(
                    str(_registration_context["operation_id"])
                )
            return self._accepted(job)

    def _dispatch(self, job):
        try:
            self.executor.submit(self._run, job["job_id"], job["attempt"])
        except RuntimeError:
            job.update(
                status="failed",
                phase="failed",
                error_code="INDEX_EXECUTOR_UNAVAILABLE",
            )
            job["version"] += 1
            self._save(job)

    def _accepted(self, job):
        view = self.public(job)
        return {
            "job_id": job["job_id"],
            "job": view,
            "poll_hint": {
                "url": "/api/asset-registry/workspace-jobs/" + job["job_id"],
                "interval_ms": 500,
            },
            "data_status": view["data_status"],
            "data_gaps": view["data_gaps"],
            "next_action": view["next_action"],
            "result_unknown": view["result_unknown"],
        }

    def _prepare_resource_folder_registration_retry(self, job, auth):
        """为已回滚的首次登记创建独立 attempt 所有权。"""
        from gw.settings.models import StorageAllowedRootRequest
        from gw.settings.service import (
            default_storage_allowed_roots_service,
            default_storage_settings_service,
        )

        registration = job.get("_resource_folder_registration") or {}
        raw_path = str(registration.get("path") or "")
        rejection = storage.describe_resource_folder_rejection(raw_path)
        if rejection:
            _error(403, "RESOURCE_FOLDER_NOT_PERMITTED", rejection)
        try:
            path = str(Path(storage.normalize_directory_path(raw_path)).resolve(strict=True))
        except (OSError, RuntimeError, ValueError):
            _error(503, "RESOURCE_FOLDER_BACKEND_UNAVAILABLE", "当前文件夹后端不可访问，需授权（共享）给后端")
        availability = storage.probe_mount(path)
        if not availability["available"]:
            _error(503, "RESOURCE_FOLDER_BACKEND_UNAVAILABLE", "当前文件夹后端不可访问，需授权（共享）给后端")

        owner = str(job.get("_owner") or "")
        source_id = str(registration.get("source_id") or "")
        source_name = str(registration.get("source_name") or "")
        path_key = storage.directory_path_key(path)
        settings_snapshot = default_storage_settings_service.get_snapshot()
        existing_source = next(
            (
                item for item in settings_snapshot.local_libraries
                if item.get("path")
                and storage.directory_path_key(str(item["path"])) == path_key
            ),
            None,
        )
        if existing_source:
            source_id = str(existing_source["id"])
            source_name = str(existing_source.get("name") or source_name)
            path = str(existing_source.get("path") or path)
            path_key = storage.directory_path_key(path)
        elif any(
            str(item.get("id") or "") == source_id
            for item in settings_snapshot.local_libraries
        ):
            _error(409, "ASSET_ROOT_ID_CONFLICT", "素材来源标识已用于其他目录")

        try:
            root, prefix = storage.resolve_asset_registration_root(path)
            created_root = False
        except CleanroomException as exc:
            if exc.code not in {"PATH_OUTSIDE_ALLOWED_ROOTS", "LOCAL_FILE_ACCESS_NOT_ADMITTED"} or not registration.get("authorize_root"):
                raise
            actor = _actor(auth)
            if str(actor.get("role") or "").lower() != "admin":
                _error(403, "FORBIDDEN", "仅管理员可授权新的资源文件夹根")
            if _restore_matching_removed_root(path):
                root, prefix = storage.resolve_asset_registration_root(path)
                created_root = False
                settings_snapshot = default_storage_settings_service.get_snapshot()
                existing_source = next(
                    (
                        item for item in settings_snapshot.local_libraries
                        if item.get("path")
                        and storage.directory_path_key(str(item["path"])) == path_key
                    ),
                    None,
                )
                if existing_source:
                    source_id = str(existing_source["id"])
                    source_name = str(existing_source.get("name") or source_name)
                    path = str(existing_source.get("path") or path)
                    path_key = storage.directory_path_key(path)
                elif any(
                    str(item.get("id") or "") == source_id
                    for item in settings_snapshot.local_libraries
                ):
                    _error(409, "ASSET_ROOT_ID_CONFLICT", "素材来源标识已用于其他目录")
            else:
                root = Path(path)
                prefix = ""
                created_root = True

        attempt = int(job.get("attempt") or 0) + 1
        previous_operation_id = str(registration.get("operation_id") or "")
        operation_id = _digest([previous_operation_id, "retry", attempt])
        operation = {
            "operation_id": operation_id,
            "job_id": str(job["job_id"]),
            "attempt": attempt,
            "owner": owner,
            "source_id": source_id,
            "source_name": source_name,
            "path": path,
            "root_path": str(root),
            "path_key": path_key,
            "root_id": storage.root_identity_id(root),
            "created_root": created_root,
            "created_source": existing_source is None,
            "phase": "prepared",
        }
        self.store.mutate(
            lambda state: state.setdefault("registration_ops", {}).__setitem__(operation_id, operation)
        )
        try:
            if created_root:
                root_snapshot = default_storage_allowed_roots_service.get_snapshot()
                default_storage_allowed_roots_service.add_root(
                    StorageAllowedRootRequest(path=path, expected_version=root_snapshot.revision),
                    registration_operation_id=operation_id,
                )
                root, prefix = storage.resolve_asset_registration_root(path)
            registered_path = str(root.joinpath(*prefix.split("/"))) if prefix else str(root)
            if existing_source is None:
                updated = default_storage_settings_service.register_resource_folder_entry(
                    {"id": source_id, "kind": source_id, "name": source_name, "path": registered_path},
                    source_name,
                    settings_snapshot.revision,
                    operation_id,
                )
                registered_path = str(next(
                    (
                        item.get("path") for item in updated.local_libraries
                        if str(item.get("id")) == source_id
                    ),
                    registered_path,
                ))
            operation.update(
                path=registered_path,
                root_path=str(root),
                path_key=storage.directory_path_key(registered_path),
                root_id=storage.root_identity_id(root),
                phase="registered",
            )
            self.store.mutate(
                lambda state: state.setdefault("registration_ops", {}).__setitem__(operation_id, operation)
            )
            return {
                "operation_id": operation_id,
                "owner": owner,
                "source_id": source_id,
                "source_name": source_name,
                "path": registered_path,
                "root_path": str(root),
                "path_key": storage.directory_path_key(registered_path),
                "root_id": storage.root_identity_id(root),
                "created_source": existing_source is None,
                "created_root": created_root,
                "authorize_root": bool(registration.get("authorize_root")),
            }
        except Exception:
            self._settle_resource_folder_registration(operation_id)
            raise

    def operate(self, job_id, action, payload, auth):
        if action not in ACTIONS:
            _error(400, "INVALID_ACTION", "不支持的任务操作")
        with self.condition:
            job = self._job(job_id, auth)
            expected = payload.get("expected_version")
            if type(expected) is not int or expected < 1:
                _error(
                    400, "EXPECTED_VERSION_REQUIRED", "任务操作必须携带expected_version"
                )
            if expected != job["version"]:
                _error(409, "VERSION_CONFLICT", "任务版本已改变，请重新读取")
            retry_registration = None
            if action == "retry":
                registration = job.get("_resource_folder_registration") or {}
                operation_id = str(registration.get("operation_id") or "")
                if operation_id and operation_id in (self.store.read().get("registration_ops") or {}):
                    self._settle_resource_folder_registration(operation_id)
                    job = self._job(job_id, auth)
                    registration = job.get("_resource_folder_registration") or {}
            if job["status"] not in ACTIONS[action] or (
                action == "retry" and job["attempt"] >= 5
            ):
                _error(409, "INDEX_ACTION_CONFLICT", "当前任务状态不允许此操作")
            if action == "retry":
                if (
                    sum(
                        j["status"] not in TERMINAL
                        for j in self.store.read()["jobs"].values()
                    )
                    >= MAX_ACTIVE
                ):
                    _error(429, "INDEX_QUEUE_FULL", "索引任务容量已满")
                if registration.get("settled") == "rolled_back":
                    retry_registration = self._prepare_resource_folder_registration_retry(job, auth)
                    job["_resource_folder_registration"] = retry_registration
                    job["_request"]["roots"] = [retry_registration["root_id"]]
                    for scope in job["_request"].get("scopes") or []:
                        if scope.get("storage_root_id") == registration.get("source_id"):
                            scope["storage_root_id"] = retry_registration["source_id"]
                        if scope.get("root_id") == registration.get("root_id"):
                            scope["root_id"] = retry_registration["root_id"]
                self._check(job)
                job.update(
                    status="queued",
                    phase="queued",
                    attempt=job["attempt"] + 1,
                    scanned=0,
                    error_count=0,
                    result=None,
                    error_code=None,
                )
            else:
                self._check(job)
                job["status"] = {
                    "pause": "paused",
                    "resume": "running",
                    "cancel": "cancelled",
                }[action]
                if action == "cancel":
                    job["phase"] = "cancelled"
            job["version"] += 1
            try:
                self._save(job)
            except Exception:
                if retry_registration and retry_registration.get("operation_id"):
                    with suppress(Exception):
                        self._settle_resource_folder_registration(
                            str(retry_registration["operation_id"])
                        )
                raise
            if action == "cancel":
                registration = job.get("_resource_folder_registration") or {}
                if registration.get("operation_id"):
                    self._settle_resource_folder_registration(
                        str(registration["operation_id"])
                    )
            self.condition.notify_all()
            if action == "retry":
                self._dispatch(job)
            return self.detail(job_id, auth)

    def _checkpoint(self, job_id, attempt):
        with self.condition:
            while True:
                job = self.store.read()["jobs"][job_id]
                if job["attempt"] != attempt or job["status"] in TERMINAL:
                    raise _Stopped()
                self._check(job)
                if job["status"] != "paused":
                    return job
                self.condition.wait(0.2)

    def _reverify_workspace_context(self, job):
        """执行时刻重新核验映射，历史 verified 标记不作为放行依据。"""
        stored = workspace_context.context_from_job(job)
        if not stored:
            return None
        return default_workspace_service.build_execution_context(
            client_instance_id=stored.get("client_instance_id"),
            execution_machine_id=stored.get("execution_machine_id"),
            logical_source_id=stored.get("logical_source_id"),
        )

    def _run_shot_vector_rebuild(self, job_id, attempt, roots):
        """只补跑指定允许根内可见视频的镜头向量，不扫描或重写资产注册表。"""
        from gw.asset_registry import repository as repo
        from gw.core.sqlite_vec_engine import SQLiteVecEngine

        root_ids = {
            str(value)
            for value in (self._checkpoint(job_id, attempt).get("_request") or {}).get("roots") or []
        }
        root_ids.intersection_update(str(root_id) for root_id in roots)
        snapshot = repo.readonly_registry_snapshot()
        assets = []
        for asset_id, asset in (snapshot.get("assets") or {}).items():
            if not isinstance(asset, dict) or str(asset.get("root_id") or "") not in root_ids:
                continue
            visible = repo.visible_asset_from_snapshot(snapshot, str(asset_id))
            if visible is None:
                continue
            metadata = visible.get("metadata")
            segments = metadata.get("video_segments") if isinstance(metadata, dict) else None
            if isinstance(segments, list) and segments:
                assets.append(visible)

        db_path = storage.data_root() / repo.ASSET_SEARCH_INDEX_FILENAME
        processed = shots = frames = failed = 0
        with SQLiteVecEngine(db_path) as engine:
            encoder = None
            if assets:
                from gw.asset_registry.embedder import (
                    DEFAULT_MODEL_ID,
                    LocalMultimodalEmbedder,
                )

                encoder = LocalMultimodalEmbedder(
                    DEFAULT_MODEL_ID, data_root=storage.data_root()
                )
            for asset in assets:
                self._checkpoint(job_id, attempt)
                asset_id = str(asset.get("asset_id") or asset.get("id") or "")
                try:
                    rows, _skipped = repo._asset_shot_rows(asset)
                    engine.replace_asset_shots(asset_id, rows)
                    result = repo.refresh_asset_shot_vectors(
                        engine,
                        asset,
                        embedder=encoder,
                        frame_decoder=_decode_shot_vector_frame,
                    )
                    shots += result["shots"]
                    frames += result["frames"]
                    failed += result["failed"]
                except Exception:
                    failed += 1
                processed += 1
                with self.condition:
                    job = self._checkpoint(job_id, attempt)
                    job.update(
                        phase="embedding",
                        scanned=processed,
                        progress={"phase": "embedding", "scanned": processed, "total": len(assets)},
                        result={"assets": processed, "shots": shots, "frames": frames, "failed": failed},
                    )
                    job["version"] += 1
                    self._save(job)
                    self.condition.notify_all()
        return {"assets": processed, "shots": shots, "frames": frames, "failed": failed}

    # ------------------------------------------------------------------
    # QIN-652：快扫入库后的后台富化（视频探测并发 1 / 文件哈希并发 N）
    # ------------------------------------------------------------------

    def _enrich_video_value(self, task):
        """复用既有探测入口，产物形状与单阶段实现逐字段一致。"""
        metadata, duration = _video_index_data(Path(task["path"]))
        return {"metadata": metadata, "duration": duration}

    def _enrich_hash_value(self, task, job_id, attempt):
        """复用既有哈希口径（含防篡改三件套），仅把分块放宽到 8 MiB。"""
        from gw.asset_registry.dedup_confirm import build_full_sha256_cache

        path = Path(task["path"])
        stat_info = task["stat"]
        if stat_info.st_size > _HASH_LIMIT_BYTES:
            return {
                "sha256": self._sampled_hash(job_id, attempt, path, stat_info),
                "hash_mode": HASH_MODE_SAMPLED,
            }
        digest = self._hash(
            job_id, attempt, path, stat_info, ENRICH_HASH_CHUNK_BYTES
        )
        return {
            "sha256": digest,
            "hash_mode": HASH_MODE_FULL,
            "full_sha256_cache": build_full_sha256_cache(
                digest, stat_info, source="index_full"
            ),
        }

    def _enrich_acquire(self, gate, stop, job_id, attempt):
        """拿通道闸门；等不到就短暂让出，暂停阻塞、取消抛 ``_Stopped``。

        闸门的条件变量不跨 ``_checkpoint`` 持有：暂停期间 worker 会阻塞在
        任务条件上，若同时握着闸门锁，别的任务连释放都做不到。
        """
        while not stop.is_set():
            if gate.acquire(0.2):
                return True
            self._checkpoint(job_id, attempt)
        return False

    def _enrich_worker(self, kind, pending, completed, stop, job_id, attempt, tasks):
        """通道 worker：在条目边界检查暂停/取消，失败只上报不整单失败。"""
        gate = _enrich_gate(kind)
        while not stop.is_set():
            try:
                index = pending.get(timeout=0.2)
            except Empty:
                continue
            try:
                if index is None:
                    return
                try:
                    # paused 时在此阻塞等待，不取新任务；cancelled/新尝试抛 _Stopped。
                    self._checkpoint(job_id, attempt)
                except _Stopped:
                    stop.set()
                    return
                task = tasks[index]
                try:
                    acquired = self._enrich_acquire(gate, stop, job_id, attempt)
                except _Stopped:
                    stop.set()
                    return
                if not acquired:
                    return
                try:
                    value = (
                        self._enrich_video_value(task)
                        if kind == "video"
                        else self._enrich_hash_value(task, job_id, attempt)
                    )
                    completed.put((index, kind, value, None))
                except _Stopped:
                    # 取消/新尝试/停机：不是条目失败，不得计入 enrich.failed。
                    stop.set()
                    return
                except Exception as exc:  # 单条目失败：保留条目，计入 enrich.failed
                    completed.put((index, kind, None, str(exc)))
                finally:
                    gate.release()
            finally:
                pending.task_done()

    def _save_enrich_progress(self, job, summary):
        """job 顶层 enrich 进度（progress 三键保持不动）。"""
        with self.condition:
            current = self.store.read()["jobs"].get(job["job_id"])
            if (
                current is None
                or current["attempt"] != job["attempt"]
                or current["status"] in TERMINAL
            ):
                return
            current["enrich"] = dict(summary)
            self._save(current)

    def _flush_enrich_batch(self, job, files, tasks, ready, results, summary):
        """把已补齐的条目合并进已存在资产；复用 _commit 的字段合并与检索索引。"""
        indexes = sorted(ready)
        ready.clear()
        batch = []
        for index in indexes:
            task = tasks[index]
            item = files[task["index"]]
            video = results[index].get("video")
            hashed = results[index].get("hash")
            if video is not None:
                metadata = dict(item.get("metadata") or {})
                metadata.update(video["metadata"])
                item["metadata"] = metadata
                if video["duration"] is not None:
                    item["duration"] = video["duration"]
                # 详情面板读顶层技术字段；口径与扫描期一致，统一走同一投影。
                item.update(video_detail_fields(metadata))
            if hashed is not None:
                item.update(hashed)
            batch.append(item)
        if batch:
            # skip：只合并资产与镜头行，不动幂等收据；update_only：绝不新增记录。
            # 走 _commit_items 而非 _commit，保持既有「一次任务一次提交」契约。
            self._commit_items(
                job,
                batch,
                None,
                {"receipt_mode": "skip", "update_only": True},
            )
        self._save_enrich_progress(job, summary)

    def _write_receipt_result(self, job, result):
        """富化结束后把收据从「快照态」更新为终态（幂等键不变）。"""
        from gw.asset_registry import repository as repo

        receipt_id = f"{job['job_id']}:{job['attempt']}"

        def update(raw):
            raw.setdefault("index_job_receipts", {})[receipt_id] = result

        execution_context = self._reverify_workspace_context(job)
        token = (
            workspace_context.set_execution_context(execution_context)
            if execution_context
            else None
        )
        try:
            return repo.state().mutate(update)
        finally:
            if token is not None:
                workspace_context.reset_execution_context(token)

    def _finalize_enrich_result(self, job, files, snapshot_result, summary):
        """终态 result：沿用快照计数，哈希统计按补齐后的真实值覆盖。"""
        result = dict(snapshot_result)
        result.pop("enrich_pending", None)
        if job["_request"].get("hash_files"):
            result["hashed"] = sum(
                1 for x in files if x.get("hash_mode") == HASH_MODE_FULL
            )
            result["hash_sampled_oversize"] = sum(
                1 for x in files if x.get("hash_mode") == HASH_MODE_SAMPLED
            )
        else:
            result.pop("hashed", None)
            result.pop("hash_sampled_oversize", None)
        result["enrich"] = dict(summary)
        self._write_receipt_result(job, result)
        return result

    def _enrich_abort_reason(self, job_id, attempt):
        """富化中断归因：只读任务当前状态，供取消/接管/停机分支如实收尾。

        返回 None 表示任务仍在 running（仅 watchdog 停池，可按已批准口径
        写终态并标注 interrupted）；返回 cancelled/superseded/failed/closed
        表示任务已离开运行态，必须保留 enrich_pending、不得写终态收据。
        """
        if self.closed:
            return "closed"
        try:
            with self.condition:
                current = self.store.read()["jobs"].get(job_id)
        except Exception:
            return "closed"
        if current is None or current["attempt"] != attempt:
            return "superseded"
        status = str(current.get("status") or "")
        if status == "cancelled":
            return "cancelled"
        if status in ("failed", "interrupted"):
            return "failed"
        return None

    def _pending_enrich_result(self, snapshot_result, summary):
        """取消/接管/停机：收据保持快照态（含 enrich_pending），不写终态收据。"""
        result = dict(snapshot_result)
        result["enrich"] = dict(summary)
        return result

    def _enrich(self, job, files, tasks, snapshot_result, summary, pre_commit_existing):
        """双通道并发补齐视频元数据与哈希，按批次增量落盘。

        幂等基线必须是**快照提交前**的注册表状态：提交会把 size/modified_at
        更新为本次值，但保留旧 sha256；若用提交后的状态判「未变」，同秒改写
        内容时会误判成可跳过，旧哈希永远不被刷新。跳过判定统一用
        ``scan_version`` 版本指纹（``mtime_ns`` + ``ino``/``dev`` + size），
        秒级 ``modified_at`` 不参与判定。
        """
        job_id, attempt = job["job_id"], job["attempt"]
        hash_workers = _enrich_hash_concurrency()
        summary["hash_concurrency"] = hash_workers
        existing = pre_commit_existing or {}

        video_pending = Queue()
        hash_pending = Queue()
        completed = Queue()
        stop = threading.Event()
        results = {index: {} for index in range(len(tasks))}
        needed = {
            index: int(task["want_video"]) + int(task["want_hash"])
            for index, task in enumerate(tasks)
        }
        ready = set()
        received = 0
        total = int(summary["total"])

        for index, task in enumerate(tasks):
            item = files[task["index"]]
            current = existing.get(
                (str(task["root_id"]), str(task["canonical_relpath"]))
            )
            if not isinstance(current, dict):
                current = {}
            # 「未变化」必须按版本指纹判定：秒级 modified_at + 文件大小无法区分
            # 同秒等长改写，会复用过期的哈希与镜头数据。
            unchanged = _same_file_version(
                current.get("scan_version"), item.get("scan_version")
            )
            if task["want_hash"]:
                if current.get("sha256") and unchanged:
                    for field in ("sha256", "hash_mode", "full_sha256_cache"):
                        if field in current:
                            item[field] = current[field]
                    needed[index] -= 1
                    received += 1
                    summary["skipped"] += 1
                    summary["done"] += 1
                    summary["pending"] -= 1
                else:
                    hash_pending.put(index)
            if task["want_video"]:
                current_metadata = current.get("metadata")
                if not isinstance(current_metadata, dict):
                    current_metadata = {}
                video_meta = current_metadata.get("video_metadata")
                if not isinstance(video_meta, dict):
                    video_meta = {}
                if (
                    video_meta.get("scene_detection_status") == "complete"
                    and unchanged
                ):
                    metadata = dict(item.get("metadata") or {})
                    for field in ("video_segments", "video_metadata"):
                        if field in current_metadata:
                            metadata[field] = current_metadata[field]
                    item["metadata"] = metadata
                    if "duration" in current:
                        item["duration"] = current["duration"]
                    needed[index] -= 1
                    received += 1
                    summary["skipped"] += 1
                    summary["done"] += 1
                    summary["pending"] -= 1
                else:
                    video_pending.put(index)
            # 两个重活都命中跳过时 existing 已是现值，不加入待落盘集合；
            # 有计算结果的条目在结果到达、needed 归零时才进入 ready。
        if received >= total:
            self._save_enrich_progress(job, summary)
            if ready:
                self._flush_enrich_batch(
                    job, files, tasks, ready, results, summary
                )
            return self._finalize_enrich_result(
                job, files, snapshot_result, summary
            )

        video_pool = ThreadPoolExecutor(
            max_workers=ENRICH_VIDEO_CONCURRENCY,
            thread_name_prefix="gw-enrich-video",
        )
        hash_pool = ThreadPoolExecutor(
            max_workers=hash_workers, thread_name_prefix="gw-enrich-hash"
        )
        futures = [
            video_pool.submit(
                self._enrich_worker,
                "video",
                video_pending,
                completed,
                stop,
                job_id,
                attempt,
                tasks,
            )
            for _ in range(ENRICH_VIDEO_CONCURRENCY)
        ] + [
            hash_pool.submit(
                self._enrich_worker,
                "hash",
                hash_pending,
                completed,
                stop,
                job_id,
                attempt,
                tasks,
            )
            for _ in range(hash_workers)
        ]
        last_flush = time.monotonic()
        last_progress = last_flush
        last_progress_save = last_flush
        abort_reason = None
        try:
            while received < total and not stop.is_set():
                try:
                    index, kind, value, error = completed.get(timeout=0.2)
                except Empty:
                    with self.condition:
                        current_job = self.store.read()["jobs"].get(job_id)
                        if (
                            self.closed
                            or current_job is None
                            or current_job["attempt"] != attempt
                            or current_job["status"] in TERMINAL
                        ):
                            stop.set()
                            abort_reason = self._enrich_abort_reason(job_id, attempt)
                            break
                        # paused 时 worker 在 _checkpoint 阻塞等恢复，暂停时长
                        # 不计入「无进展」，避免 600s 看门狗误停并翻转终态。
                        if current_job["status"] == "paused":
                            last_progress = time.monotonic()
                    now = time.monotonic()
                    if now - last_progress > ENRICH_STALL_SECONDS:
                        stop.set()
                        abort_reason = "stalled"
                        break
                    if ready and now - last_flush >= ENRICH_BATCH_INTERVAL_SECONDS:
                        self._flush_enrich_batch(
                            job, files, tasks, ready, results, summary
                        )
                        last_flush = now
                    continue
                received += 1
                summary["pending"] -= 1
                last_progress = time.monotonic()
                if error is not None:
                    summary["failed"] += 1
                else:
                    summary["done"] += 1
                    results[index][kind] = value
                needed[index] -= 1
                if needed[index] == 0:
                    ready.add(index)
                now = time.monotonic()
                # 与扫描期同口径节流：长耗时富化也要能观察到递增进度。
                if now - last_progress_save >= PROGRESS_MIN_INTERVAL_SECONDS:
                    self._save_enrich_progress(job, summary)
                    last_progress_save = now
                if len(ready) >= ENRICH_BATCH_SIZE or (
                    ready and now - last_flush >= ENRICH_BATCH_INTERVAL_SECONDS
                ):
                    self._flush_enrich_batch(
                        job, files, tasks, ready, results, summary
                    )
                    last_flush = now
            if ready:
                self._flush_enrich_batch(job, files, tasks, ready, results, summary)
        finally:
            stop.set()
            for _ in range(ENRICH_VIDEO_CONCURRENCY):
                video_pending.put(None)
            for _ in range(hash_workers):
                hash_pending.put(None)
            video_pool.shutdown(wait=True)
            hash_pool.shutdown(wait=True)
            for future in futures:
                with suppress(Exception):
                    future.exception()
        # 取消/接管/停机导致未收齐时，如实留下 pending，不伪造完成。
        summary["pending"] = max(0, total - received)
        if received < total:
            reason = abort_reason or self._enrich_abort_reason(job_id, attempt)
            if reason is None:
                # 任务仍在 running：仅看门狗停池，按已批准口径写终态并标注中断。
                summary["interrupted"] = True
                summary["interrupt_reason"] = "stalled"
                return self._finalize_enrich_result(
                    job, files, snapshot_result, summary
                )
            summary["interrupted"] = True
            summary["interrupt_reason"] = reason
            # 取消/新尝试/停机：收据保留 enrich_pending，绝不写终态收据，
            # 否则 _committed_truth 会把 cancelled 投影回 succeeded。
            return self._pending_enrich_result(snapshot_result, summary)
        # 收齐也要复核：取消可能恰在最后一个结果之后、收据写入之前到达。
        reason = self._enrich_abort_reason(job_id, attempt)
        if reason is not None:
            summary["interrupted"] = True
            summary["interrupt_reason"] = reason
            return self._pending_enrich_result(snapshot_result, summary)
        return self._finalize_enrich_result(job, files, snapshot_result, summary)

    def _run(self, job_id, attempt):
        try:
            with self.condition:
                job = self.store.read()["jobs"][job_id]
                if job["attempt"] != attempt or job["status"] in TERMINAL:
                    return
                if job["status"] == "queued":
                    job["status"] = "running"
                    job["phase"] = "scanning"
                    job["version"] += 1
                    self._save(job)
            job = self._checkpoint(job_id, attempt)
            self._reverify_workspace_context(job)
            roots = self._check(job)
            # QIN-652：只在 reindex 的 request 副本上打开快扫。_request 与
            # _request_digest 逐字节不变，幂等键与 sync 分支语义保持原样。
            request = dict(job["_request"])
            if request.get("kind") == "shot_vectors":
                result = self._run_shot_vector_rebuild(job_id, attempt, roots)
                with self.condition:
                    job = self._checkpoint(job_id, attempt)
                    job.update(
                        status="succeeded",
                        phase="completed",
                        scanned=result["assets"],
                        result=result,
                        error_code=None,
                    )
                    job["version"] += 1
                    self._save(job)
                    self.condition.notify_all()
                return
            fast_scan = request.get("kind") == "reindex"
            if fast_scan:
                request["_fast_scan"] = True
            files = self._scan(job_id, attempt, roots, request)
            generation_stats = getattr(files, "generation_stats", None)
            enrich_tasks = (
                list(getattr(files, "enrich_tasks", None) or []) if fast_scan else []
            )
            summary = None
            if enrich_tasks:
                total_units = sum(
                    int(task["want_video"]) + int(task["want_hash"])
                    for task in enrich_tasks
                )
                summary = {
                    "total": total_units,
                    "done": 0,
                    "failed": 0,
                    "skipped": 0,
                    "pending": total_units,
                    "video_concurrency": ENRICH_VIDEO_CONCURRENCY,
                    "hash_concurrency": _enrich_hash_concurrency(),
                }
            with self.condition:
                job = self._checkpoint(job_id, attempt)
                job.update(phase="indexing", scanned=len(files))
                self._save(job)
                if job["error_count"]:
                    _error(
                        503,
                        "INDEX_FILE_ERRORS",
                        "扫描存在不可读取文件，未提交不完整索引",
                    )
                # 提交必须与取消/暂停在同一线程锁内线性化：
                # test_commit_wins_cancel_never_reports_cancelled 覆盖该不变量，
                # 不能在提交期间放开任务锁。
                # 提交前先取幂等基线（只保留判定所需字段的快照，避免提交
                #  mutate 原地修改同一 dict 造成基线漂移）。
                pre_commit_existing = {}
                if enrich_tasks:
                    from gw.asset_registry import repository as repo

                    try:
                        for asset in (
                            repo.readonly_registry_snapshot().get("assets", {}).values()
                        ):
                            if not isinstance(asset, dict):
                                continue
                            source_metadata = asset.get("metadata")
                            if not isinstance(source_metadata, dict):
                                source_metadata = {}
                            pre_commit_existing[
                                (
                                    str(asset.get("root_id") or ""),
                                    str(asset.get("canonical_relpath") or ""),
                                )
                            ] = {
                                "size_bytes": asset.get("size_bytes"),
                                "modified_at": asset.get("modified_at"),
                                "scan_version": asset.get("scan_version"),
                                "sha256": asset.get("sha256"),
                                "hash_mode": asset.get("hash_mode"),
                                "full_sha256_cache": asset.get("full_sha256_cache"),
                                "duration": asset.get("duration"),
                                "metadata": {
                                    field: source_metadata[field]
                                    for field in ("video_segments", "video_metadata")
                                    if field in source_metadata
                                },
                            }
                    except Exception:
                        pre_commit_existing = {}
                job["_enrich_commit_options"] = {
                    "enrich_pending": bool(enrich_tasks)
                }
                try:
                    result = self._commit(job, files, generation_stats)
                finally:
                    job.pop("_enrich_commit_options", None)
                if enrich_tasks:
                    job.update(phase="indexed", result=result, enrich=dict(summary))
                else:
                    job.update(
                        status="succeeded",
                        phase="completed",
                        result=result,
                        scanned=len(files),
                        error_code=None,
                    )
                job["version"] += 1
                self._save(job)
                # QIN-652（PM 确认）：登记 settle 提前到快照提交后，用户立即
                # 可进库；后台富化异常不回滚已入库条目。
                registration = job.get("_resource_folder_registration") or {}
                if registration.get("operation_id"):
                    self._settle_resource_folder_registration(
                        str(registration["operation_id"]), committed=True
                    )
                self.condition.notify_all()
            if not enrich_tasks:
                return
            with self.condition:
                job = self._checkpoint(job_id, attempt)
                job.update(phase="enriching")
                self._save(job)
                self.condition.notify_all()
            result = self._enrich(
                job, files, enrich_tasks, result, summary, pre_commit_existing
            )
            with self.condition:
                job = self._checkpoint(job_id, attempt)
                job.update(
                    status="succeeded",
                    phase="completed",
                    result=result,
                    scanned=len(files),
                    error_code=None,
                    enrich=dict(summary),
                )
                job["version"] += 1
                self._save(job)
                self.condition.notify_all()
        except _Stopped:
            return
        except Exception as exc:
            with self.condition:
                if storage.data_root().resolve() != self.root:
                    return
                job = self.store.read()["jobs"].get(job_id)
                if not job or job["attempt"] != attempt or job["status"] in TERMINAL:
                    return
                # 注册表已提交而任务状态落盘失败时，以同事务收据为准。
                receipt = self._receipt_from_registry(job)
                if isinstance(receipt, dict) and receipt.get("enrich_pending"):
                    # 富化阶段异常：收据只是快照态，如实标注未补齐，不伪造完成。
                    receipt = dict(receipt)
                    receipt.pop("enrich_pending", None)
                    enrich = dict(receipt.get("enrich") or job.get("enrich") or {})
                    enrich["interrupted"] = True
                    receipt["enrich"] = enrich
                job.update(
                    status="succeeded"
                    if receipt
                    else ("interrupted" if self.closed else "failed"),
                    phase="completed"
                    if receipt
                    else ("interrupted" if self.closed else "failed"),
                    result=receipt,
                    error_code=None
                    if receipt
                    else (
                        exc.code
                        if isinstance(exc, CleanroomException)
                        else "INDEX_EXECUTION_FAILED"
                    ),
                )
                job["version"] += 1
                # 恢复时只相信注册表收据，禁止重新提交。
                with suppress(Exception):
                    self._save(job)
                registration = job.get("_resource_folder_registration") or {}
                if registration.get("operation_id"):
                    with suppress(Exception):
                        self._settle_resource_folder_registration(
                            str(registration["operation_id"]), committed=bool(receipt)
                        )

    def _children(self, job_id, attempt, folder, root, *, hash_files=False, resolved_root=None):
        # 目录项枚举与元数据读取共享一次检查点，paused 后不继续读取新条目。
        with self.condition:
            self._checkpoint(job_id, attempt)
            resolved_folder = folder.resolve()
            if resolved_root is None:
                resolved_root = root.resolve()
            mapped_drive_path = storage._is_network_path(str(folder))
            inside_resolved_root = (
                resolved_folder == resolved_root
                or resolved_folder.is_relative_to(resolved_root)
            )
            if (
                (resolved_folder != folder.absolute() and not mapped_drive_path)
                or not inside_resolved_root
            ):
                _error(403, "INDEX_PATH_CHANGED", "扫描目录已改变")
            iterator = os.scandir(folder)
        try:
            while True:
                with self.condition:
                    self._checkpoint(job_id, attempt)
                    try:
                        child = next(iterator)
                    except StopIteration:
                        break
                    try:
                        # 哈希流程需要真实 inode/dev 与打开句柄比对；普通扫描使用
                        # DirEntry 的枚举元数据，避免每个 NAS 文件再单独 lstat。
                        info = (
                            Path(child.path).lstat()
                            if hash_files
                            else child.stat(follow_symlinks=False)
                        )
                        is_symlink = child.is_symlink()
                        stat_error = None
                    except OSError as exc:
                        info = None
                        is_symlink = False
                        stat_error = exc
                yield child, info, is_symlink, stat_error
        finally:
            iterator.close()

    def _hash(self, job_id, attempt, path, expected, chunk_bytes=1024 * 1024):
        """完整 sha256；分块大小可由富化通道按位置参数放宽，防篡改三件套不变。"""
        with self.condition:
            self._checkpoint(job_id, attempt)
            # 与 scandir 分支一致：映射盘上 resolve() 返回 UNC 而 absolute() 保留
            # 盘符，形态差本身不代表路径被篡改，故网络路径不按形态判失败。
            # 真实变更仍由下方打开句柄后的 inode/dev/size 比对失败关闭。
            if (
                path.resolve() != path.absolute()
                and not storage._is_network_path(str(path))
            ):
                _error(403, "INDEX_PATH_CHANGED", "摘要源路径已改变")
            handle = path.open("rb")
            actual = os.fstat(handle.fileno())
            if (actual.st_ino, actual.st_dev, actual.st_size) != (
                expected.st_ino,
                expected.st_dev,
                expected.st_size,
            ):
                handle.close()
                _error(409, "INDEX_FILE_CHANGED", "摘要源文件已改变")
        digest = hashlib.sha256()
        try:
            while True:
                with self.condition:
                    self._checkpoint(job_id, attempt)
                    chunk = handle.read(chunk_bytes)
                if not chunk:
                    break
                digest.update(chunk)
            final = os.fstat(handle.fileno())
            if (final.st_size, final.st_mtime_ns) != (
                expected.st_size,
                expected.st_mtime_ns,
            ):
                _error(409, "INDEX_FILE_CHANGED", "摘要计算期间文件已改变")
        finally:
            handle.close()
        return digest.hexdigest()

    def _sampled_hash(self, job_id, attempt, path, expected):
        """超限文件采样哈希：首尾 4MiB + 长度（QIN-561 基线口径，QIN-564）。

        完整 sha256 对 >512MiB 文件代价过高；采样值经域分离前缀 + 定长
        长度编码参与摘要，与完整 sha256 不共摘要空间，查重组不会把两种
        模式交叉误配。同大小文件间「大小不同必然内容不同」，采样分组
        只在真实同内容副本间命中（首尾相同、中间不同的理论假阳性为
        采样哈希的已知限制，与 QIN-561 基线工具同口径）。

        防篡改三件套与 _hash 一致：形态差 + 网络路径豁免（QIN-466 口径）、
        打开句柄后 ino/dev/size 比对、摘要后 size/mtime_ns 终检。
        """
        with self.condition:
            self._checkpoint(job_id, attempt)
            if (
                path.resolve() != path.absolute()
                and not storage._is_network_path(str(path))
            ):
                _error(403, "INDEX_PATH_CHANGED", "摘要源路径已改变")
            handle = path.open("rb")
            actual = os.fstat(handle.fileno())
            if (actual.st_ino, actual.st_dev, actual.st_size) != (
                expected.st_ino,
                expected.st_dev,
                expected.st_size,
            ):
                handle.close()
                _error(409, "INDEX_FILE_CHANGED", "摘要源文件已改变")
        digest = hashlib.sha256()
        digest.update(_SAMPLED_HASH_DOMAIN)
        digest.update(struct.pack("<Q", expected.st_size))
        size = expected.st_size
        try:
            for offset in (0, max(0, size - _HASH_SAMPLE_BYTES)):
                handle.seek(offset)
                with self.condition:
                    self._checkpoint(job_id, attempt)
                digest.update(handle.read(_HASH_SAMPLE_BYTES))
            final = os.fstat(handle.fileno())
            if (final.st_size, final.st_mtime_ns) != (
                expected.st_size,
                expected.st_mtime_ns,
            ):
                _error(409, "INDEX_FILE_CHANGED", "摘要计算期间文件已改变")
        finally:
            handle.close()
        return digest.hexdigest()

    def _scan(self, job_id, attempt, roots, request):
        from gw.asset_registry import repository as repo

        files = []
        entries = 0
        # QIN-652：_run 只在 request 副本上注入 _fast_scan，快扫阶段跳过视频
        # 探测与哈希；直接调用 _scan 的既有测试与 sync 分支默认 False，单阶段
        # 语义原样保留。富化任务清单挂在 _ScanFiles 上，绝不序列化进注册表。
        fast_scan = bool(request.get("_fast_scan"))
        enrich_tasks = []
        scan_paths = {}
        # QIN-600：只在重建索引时读生成元数据；sync 只做差集统计，不碰文件内容。
        want_generation = request.get("kind") == "reindex"
        generation_stats = {
            "scanned_images": 0,
            "with_metadata": 0,
            "warnings": 0,
            "truncated": 0,
            # QIN-601：带 `generation.comfyui` 标记的图片数（可复核）。
            "comfyui_tagged": 0,
        }
        progress_saved_at = time.monotonic()
        configured_exclusion_roots, custom_cache_base = (
            repo._index_scan_exclusion_configuration()
        )
        scopes = request.get("scopes") or [
            {"root_id": root_id, "prefix": "", "storage_root_id": root_id}
            for root_id in request["roots"]
        ]
        for scope in scopes:
            root_id = scope["root_id"]
            root = roots[root_id]
            prefix = scope.get("prefix") or ""
            scan_path = scope.get("scan_path")
            scan_root = (
                Path(scan_path)
                if scan_path is not None
                else root.joinpath(*Path(prefix or ".").parts)
            )
            boundary = scan_root if scan_path is not None else root
            with self.condition:
                self._checkpoint(job_id, attempt)
                try:
                    scan_root_resolved = scan_root.resolve(strict=True)
                    root_resolved = root.resolve(strict=True) if scan_path is None else None
                except (OSError, RuntimeError, ValueError):
                    scan_root_resolved = None
                    root_resolved = None
                mapped_drive_path = storage._is_network_path(str(scan_root))
                if (
                    not scan_root.is_dir()
                    or scan_root_resolved is None
                    or (
                        scan_path is not None
                        and scan_root_resolved != scan_root.absolute()
                    )
                    or (
                        scan_path is None
                        and (
                            root_resolved is None
                            or not (
                                scan_root_resolved == root_resolved
                                or scan_root_resolved.is_relative_to(root_resolved)
                            )
                            or (
                                scan_root_resolved != scan_root.absolute()
                                and not mapped_drive_path
                            )
                        )
                    )
                ):
                    _error(
                        503,
                        "INDEX_ROOT_UNAVAILABLE",
                        "索引来源目录不可用",
                        data_status="not_integrated",
                        unavailable=True,
                    )
            scan_data_root = getattr(self, "root", None) or repo._index_scan_runtime_data_root()
            purging_ids, purging_keys = repo._purging_root_ids_and_keys(data_root=scan_data_root)
            removed_ids = repo._removed_root_ids(data_root=scan_data_root) if scan_data_root is not None else set()
            if (
                root_id in purging_ids
                or root_id in removed_ids
                or str(scope.get("storage_root_id") or "") in (purging_ids | removed_ids)
                or repo._path_within_any_key(scan_root_resolved, purging_keys)
            ):
                continue
            exclusion_roots = repo._index_scan_exclusions_for_scope(
                scan_root_resolved,
                configured_exclusion_roots,
                custom_cache_base,
            )
            if repo._index_scan_scope_is_excluded(
                scan_root_resolved, prefix, exclusion_roots
            ):
                continue
            pending = [scan_root]
            while pending:
                folder = pending.pop()
                for child, info, is_symlink, stat_error in self._children(
                    job_id,
                    attempt,
                    folder,
                    boundary,
                    hash_files=request["hash_files"],
                    resolved_root=(
                        scan_root_resolved if scan_path is not None else root_resolved
                    ),
                ):
                    entries += 1
                    if entries > MAX_ENTRIES:
                        _error(413, "INDEX_SCAN_LIMIT", "扫描目录项数量超过上限")
                    try:
                        if stat_error is not None:
                            raise stat_error
                        if info is None:
                            continue
                        if is_symlink or getattr(info, "st_file_attributes", 0) & 0x400:
                            continue
                        path = Path(child.path)
                        if stat.S_ISDIR(info.st_mode):
                            if repo._index_scan_directory_is_excluded(
                                path, exclusion_roots
                            ) or repo._path_within_any_key(path, purging_keys):
                                continue
                            pending.append(path)
                            continue
                        if repo._index_scan_file_name_is_excluded(path.name):
                            continue
                        if not stat.S_ISREG(info.st_mode):
                            continue
                        if len(files) >= request["max_files"]:
                            _error(413, "INDEX_SCAN_LIMIT", "扫描文件数量超过上限")
                        relative_path = path.relative_to(boundary).as_posix()
                        display_path = (
                            "/".join(
                                part
                                for part in (prefix, relative_path)
                                if part and part != "."
                            )
                            if scan_path is not None
                            else relative_path
                        )
                        item = {
                            "root_id": root_id,
                            "storage_root_id": scope.get("storage_root_id")
                            or root_id,
                            "display_path": display_path,
                            "canonical_relpath": storage.canonical_relpath(display_path),
                            "name": path.name,
                            "size_bytes": info.st_size,
                            # QIN-539：索引期就落盘文件修改时间，读取时不必再 stat。
                            "modified_at": time.strftime(
                                "%Y-%m-%dT%H:%M:%SZ", time.gmtime(info.st_mtime)
                            ),
                            # QIN-652 返工：落盘版本指纹，供下次 reindex 判定
                            # 「内容未变」与富化批次校验版本，避免同秒等长改写
                            # 复用过期哈希/镜头数据。
                            "scan_version": _file_version(info),
                        }
                        if path.suffix.lower() == ".cube":
                            from gw.asset_registry.lut_cube import (
                                LutCubeError,
                                parse_lut_cube_file,
                            )

                            try:
                                item["kind"] = "lut"
                                item["metadata"] = {
                                    "lut": parse_lut_cube_file(path)
                                }
                            except LutCubeError as exc:
                                _error(422, exc.code, str(exc))
                        if path.suffix.lower() in VIDEO_SUFFIXES and not fast_scan:
                            video_metadata, duration = _video_index_data(path)
                            item["metadata"] = video_metadata
                            if duration is not None:
                                item["duration"] = duration
                            # 详情面板读顶层字段；与富化批次同一投影口径。
                            item.update(video_detail_fields(video_metadata))
                        if want_generation and generation_metadata.is_supported_image(
                            path
                        ):
                            # QIN-600：只读元数据段（不解码像素）。失败只计 warning，
                            # 不影响该文件入库；文件不可读的 OSError 仍走下方既有
                            # error_count 路径。无元数据时不写该键（AC4）。
                            generation, warnings = (
                                generation_metadata.extract_generation(path)
                            )
                            generation_stats["scanned_images"] += 1
                            generation_stats["warnings"] += warnings
                            if generation is not None:
                                item.setdefault("metadata", {})[
                                    "generation"
                                ] = generation
                                generation_stats["with_metadata"] += 1
                                if generation.get("comfyui"):
                                    generation_stats["comfyui_tagged"] += 1
                                if generation.get("truncated"):
                                    generation_stats["truncated"] += 1
                        if request["hash_files"] and not fast_scan:
                            # QIN-564：超限文件不再 413 fail-fast 整单失败，
                            # 改走首尾 4MiB + 长度的采样哈希并诚实标记 hash_mode。
                            if info.st_size > _HASH_LIMIT_BYTES:
                                item["sha256"] = self._sampled_hash(
                                    job_id, attempt, path, info
                                )
                                item["hash_mode"] = HASH_MODE_SAMPLED
                            else:
                                item["sha256"] = self._hash(job_id, attempt, path, info)
                                item["hash_mode"] = HASH_MODE_FULL
                                from gw.asset_registry.dedup_confirm import (
                                    build_full_sha256_cache,
                                )

                                item["full_sha256_cache"] = build_full_sha256_cache(
                                    item["sha256"],
                                    info,
                                    source="index_full",
                                )
                        files.append(item)
                        if fast_scan:
                            scan_paths[(root_id, item["canonical_relpath"])] = path
                        want_video = path.suffix.lower() in VIDEO_SUFFIXES
                        want_hash = bool(request["hash_files"])
                        # 只有确实需要后台重活的文件才进富化清单：非哈希、
                        # 非视频的纯快扫保持单阶段一次提交语义。
                        if fast_scan and (want_video or want_hash):
                            enrich_tasks.append(
                                {
                                    "index": len(files) - 1,
                                    "path": str(path),
                                    "stat": info,
                                    "root_id": root_id,
                                    "storage_root_id": item["storage_root_id"],
                                    "canonical_relpath": item["canonical_relpath"],
                                    "display_path": display_path,
                                    "name": item["name"],
                                    "size_bytes": info.st_size,
                                    "modified_at": item["modified_at"],
                                    "want_video": want_video,
                                    "want_hash": want_hash,
                                }
                            )
                        now = time.monotonic()
                        if (
                            len(files) % PROGRESS_BATCH_SIZE == 0
                            or now - progress_saved_at >= PROGRESS_MIN_INTERVAL_SECONDS
                        ):
                            # 进度落盘是尽力而为：状态盘用 tmp + os.replace 原子写入，
                            # Windows 上若恰有并发读取（轮询进度）正在打开目标文件，
                            # 替换会抛 PermissionError。这里跳过本个节流点、下一批继续，
                            # 绝不把状态盘抖动算成素材文件不可读而整单失败（INDEX_FILE_ERRORS）。
                            with suppress(OSError):
                                with self.condition:
                                    job = self._checkpoint(job_id, attempt)
                                    job["scanned"] = len(files)
                                    self._save(job)
                            progress_saved_at = now
                        if (
                            SCAN_THROTTLE_SECONDS > 0
                            and len(files) % SCAN_THROTTLE_EVERY == 0
                        ):
                            time.sleep(SCAN_THROTTLE_SECONDS)
                    except OSError:
                        with self.condition:
                            job = self._checkpoint(job_id, attempt)
                            job["error_count"] += 1
                            self._save(job)
                            if job["error_count"] >= 100:
                                    _error(
                                        503, "INDEX_FILE_ERRORS", "不可读取文件数量超过上限"
                                    )
        scanned = _ScanFiles(files)
        scanned.generation_stats = generation_stats
        scanned.enrich_tasks = enrich_tasks
        scanned.scan_paths = scan_paths if fast_scan else None
        return scanned

    def _commit(self, job, files, generation_stats=None):
        """原子提交扫描结果（既有入口，签名与语义保持三参数不变）。

        QIN-652：后台富化批次走 ``_commit_items`` 而非 ``_commit``，使既有
        「一次索引任务一次提交」契约（含对 ``_commit`` 的 monkeypatch 计数）
        不被批次增量落盘打破。
        """
        options = job.get("_enrich_commit_options") or {}
        return self._commit_items(job, files, generation_stats, options)

    def _commit_items(self, job, files, generation_stats, options):
        """提交实现；``options`` 只由后台编排传入（下划线键，不进 public）。

        * ``enrich_pending``：快照收据标记「富化未完成」，``_committed_truth``
          不会把它投影成 succeeded，富化期间 pause/cancel 仍然可用；
        * ``receipt_mode``：``write`` 首次写收据与登记收据（既有行为）、
          ``skip`` 富化批次只合并资产不动收据、``update`` 终态只更新收据；
        * ``update_only``：富化批次只更新已存在条目，绝不新增资产记录。
        """
        from gw.asset_registry import repository as repo

        self._check(job)
        enrich_pending = bool(options.get("enrich_pending"))
        receipt_mode = options.get("receipt_mode", "write")
        update_only = bool(options.get("update_only"))
        scan_paths = getattr(files, "scan_paths", None)
        receipt_id = f"{job['job_id']}:{job['attempt']}"

        def commit(raw):
            self._check(job)
            # QIN-598：事务锁内二次校验允许根、已移出来源与清除墓碑，丢弃迟到扫描结果。
            commit_data_root = getattr(self, "root", None) or repo._index_scan_runtime_data_root()
            purging_ids, _ = repo._purging_root_ids_and_keys(raw, data_root=commit_data_root)
            removed_ids = repo._removed_root_ids(data_root=commit_data_root) if commit_data_root is not None else set()
            blocked_ids = purging_ids | removed_ids
            scoped_root_ids = {
                str(s.get("root_id") or "")
                for s in (job.get("_request", {}).get("scopes") or [])
                if s.get("root_id")
            }
            scoped_source_ids = {
                str(s.get("storage_root_id") or "")
                for s in (job.get("_request", {}).get("scopes") or [])
                if s.get("storage_root_id")
            }
            live_root_ids = (
                {storage.root_identity_id(r) for r in storage.allowed_roots()}
                if scoped_root_ids
                else set()
            )
            if (scoped_root_ids | scoped_source_ids) & blocked_ids or (
                scoped_root_ids and any(rid not in live_root_ids for rid in scoped_root_ids)
            ):
                _error(403, "INDEX_ROOTS_CHANGED", "允许根或来源配置已移除或正在清除")
            valid_files = [
                item
                for item in files
                if str(item.get("root_id") or "") not in blocked_ids
                and str(item.get("storage_root_id") or "") not in blocked_ids
                and (not scoped_root_ids or str(item.get("root_id") or "") in live_root_ids)
            ]
            receipts = raw.setdefault("index_job_receipts", {})
            if receipt_id in receipts and receipt_mode == "write":
                return receipts[receipt_id]
            by_relpath = {}
            for item in valid_files:
                by_relpath.setdefault(item["canonical_relpath"], []).append(item)
            repair_queue = raw.setdefault("identity_repair_queue", [])
            if not isinstance(repair_queue, list):
                repair_queue = raw["identity_repair_queue"] = []
            known = {}
            for asset in raw["assets"].values():
                display_path = asset.get("display_path")
                asset_root_id = asset.get("root_id")
                canonical_relpath = asset.get("canonical_relpath")
                if display_path and not canonical_relpath:
                    try:
                        canonical_relpath = storage.canonical_relpath(str(display_path))
                    except CleanroomException:
                        canonical_relpath = None
                if not asset_root_id:
                    matches = by_relpath.get(canonical_relpath or "", [])
                    if (
                        job["_request"].get("legacy_identity_scan_complete")
                        and len(matches) == 1
                    ):
                        asset_root_id = matches[0]["root_id"]
                        asset["root_id"] = asset_root_id
                        asset["storage_root_id"] = matches[0]["storage_root_id"]
                    else:
                        asset["identity_status"] = "ambiguous"
                        asset_id = str(asset.get("asset_id") or asset.get("id") or "")
                        if asset_id and asset_id not in repair_queue:
                            repair_queue.append(asset_id)
                        continue
                if canonical_relpath:
                    asset["canonical_relpath"] = canonical_relpath
                    known[(asset_root_id, canonical_relpath)] = asset
            disk = {(x["root_id"], x["canonical_relpath"]) for x in valid_files}
            added = 0
            updated = 0
            if job["_request"]["kind"] == "reindex":
                search_documents = {}
                next_asset_number = repo._asset_sequence_start(raw)
                # 富化批次（update_only）只允许回写富化产物：身份、名称、大小、
                # 修改时间、类型都属扫描字段，旧批次绝不能覆盖新版本。
                scan_fields = (
                    ("sha256", "hash_mode")
                    if update_only
                    else (
                        "storage_root_id",
                        "canonical_relpath",
                        "name",
                        "size_bytes",
                        "sha256",
                        "hash_mode",
                        "modified_at",
                        "kind",
                    )
                )
                cache_breaking_fields = {
                    "storage_root_id",
                    "canonical_relpath",
                    "size_bytes",
                    "sha256",
                    "hash_mode",
                    "modified_at",
                }
                for item in valid_files:
                    identity = (item["root_id"], item["canonical_relpath"])
                    existing = known.get(identity)
                    if existing is not None:
                        item_version = item.get("scan_version")
                        # 版本校验：富化批次只对「扫描时那一版」的资产生效。
                        # 两个 reindex 交错时，旧任务晚到的批次会把新版本的
                        # 信息与富化结果改回旧值，因此版本不符整条丢弃。
                        version_changed = not _same_file_version(
                            existing.get("scan_version"), item_version
                        )
                        if update_only and version_changed:
                            continue
                        if not update_only and version_changed and scan_paths is not None:
                            # 指纹没有先后顺序。提交锁内复核磁盘当前版本，防止
                            # 旧任务在新任务完成后迟到并清空新版本的富化结果。
                            path = scan_paths.get(identity)
                            try:
                                current_stat = path.lstat() if path is not None else None
                            except OSError:
                                current_stat = None
                            current_version = _file_version(current_stat) if current_stat else {}
                            # Windows 的 DirEntry.stat 在无哈希扫描时可能不提供
                            # ino/dev；保留扫描侧的未知值，只比较可得字段。
                            if isinstance(item_version, dict):
                                for field in ("ino", "dev"):
                                    if item_version.get(field) == 0:
                                        current_version[field] = 0
                            if (
                                current_stat is None
                                or not stat.S_ISREG(current_stat.st_mode)
                                or getattr(current_stat, "st_file_attributes", 0) & 0x400
                                or not _same_file_version(current_version, item_version)
                            ):
                                continue
                        changed = False
                        cache_invalidated = False
                        was_lut = existing.get("kind") == "lut"
                        if version_changed and item_version is not None:
                            # 扫到新版本：旧哈希/镜头结果描述的是上一份内容，
                            # 必须先失效再等富化补齐；否则富化失败或取消后，
                            # 下一次 reindex 会把「新文件信息 + 旧结果」判成
                            # 未变化而永久跳过。
                            for stale in (
                                "sha256",
                                "hash_mode",
                                "full_sha256_cache",
                                "duration",
                                "width",
                                "height",
                                "frame_rate",
                            ):
                                existing.pop(stale, None)
                            stale_metadata = existing.get("metadata")
                            if isinstance(stale_metadata, dict):
                                stale_metadata.pop("video_segments", None)
                                stale_metadata.pop("video_metadata", None)
                            existing["scan_version"] = item_version
                            changed = True
                        for field in scan_fields:
                            if field in item and existing.get(field) != item[field]:
                                existing[field] = item.get(field)
                                changed = True
                                if field in cache_breaking_fields:
                                    cache_invalidated = True
                        if cache_invalidated and "full_sha256_cache" in existing:
                            existing.pop("full_sha256_cache", None)
                        if (
                            "full_sha256_cache" in item
                            and item.get("hash_mode") == HASH_MODE_FULL
                        ):
                            prev_cache = existing.get("full_sha256_cache")
                            next_cache = item["full_sha256_cache"]
                            same_version = (
                                isinstance(prev_cache, dict)
                                and isinstance(next_cache, dict)
                                and all(
                                    prev_cache.get(k) == next_cache.get(k)
                                    for k in ("sha256", "size_bytes", "mtime_ns", "ino", "dev", "verified")
                                )
                            )
                            if not same_version:
                                existing["full_sha256_cache"] = next_cache
                                changed = True
                        elif (
                            item.get("hash_mode") == HASH_MODE_SAMPLED
                            and "full_sha256_cache" in existing
                            and cache_invalidated
                        ):
                            existing.pop("full_sha256_cache", None)
                        if was_lut and "kind" not in item:
                            existing["kind"] = "indexed"
                            changed = True
                        if "metadata" in item:
                            current_metadata = existing.get("metadata")
                            if not isinstance(current_metadata, dict):
                                current_metadata = existing["metadata"] = {}
                            scanned_metadata = item["metadata"]
                            scanned_video = scanned_metadata.get("video_metadata", {})
                            current_video = current_metadata.get("video_metadata", {})
                            if (
                                scanned_video.get("scene_detection_status") == "complete"
                                or not current_video
                            ):
                                for field in ("video_segments", "video_metadata"):
                                    if current_metadata.get(field) != scanned_metadata.get(
                                        field
                                    ):
                                        current_metadata[field] = scanned_metadata.get(
                                            field
                                        )
                                        changed = True
                                # 与前端详情面板的读取口径对齐：顶层投影必须与
                                # metadata 同源，避免出现「元数据补齐了、详情仍读取中」。
                                for detail_field, detail_value in video_detail_fields(
                                    scanned_metadata
                                ).items():
                                    if existing.get(detail_field) != detail_value:
                                        existing[detail_field] = detail_value
                                        changed = True
                            if "lut" in scanned_metadata:
                                if current_metadata.get("lut") != scanned_metadata["lut"]:
                                    current_metadata["lut"] = scanned_metadata["lut"]
                                    changed = True
                            elif was_lut and "lut" in current_metadata:
                                current_metadata.pop("lut")
                                changed = True
                            # QIN-600：只替换 generation 这一个键；扫描不到时不删旧值
                            # （GW 产物登记写入的提示词不会被无元数据的重扫清掉）。
                            # QIN-601：现值来源是 gw-workflow 时按 §2.6 合并，不翻回
                            # embedded，has_* 取或。
                            if "generation" in scanned_metadata:
                                merged_generation = _merge_generation_metadata(
                                    current_metadata.get("generation"),
                                    scanned_metadata["generation"],
                                )
                                if (
                                    current_metadata.get("generation")
                                    != merged_generation
                                ):
                                    current_metadata["generation"] = merged_generation
                                    changed = True
                        elif was_lut:
                            current_metadata = existing.get("metadata")
                            if isinstance(current_metadata, dict) and "lut" in current_metadata:
                                current_metadata.pop("lut")
                                changed = True
                        if "duration" in item and existing.get("duration") != item[
                            "duration"
                        ]:
                            existing["duration"] = item["duration"]
                            changed = True
                        if changed:
                            existing["updated_at"] = storage.now_iso()
                            updated += 1
                        asset_id = str(existing.get("asset_id") or existing.get("id") or "")
                        if asset_id:
                            search_documents[asset_id] = existing
                        continue
                    if update_only:
                        # QIN-652：富化批次只补齐已存在条目，不产生重复/新增记录。
                        continue
                    asset_id = f"ast_{next_asset_number:04d}"
                    next_asset_number += 1
                    asset_kind = item.get("kind", "indexed")
                    record = repo._plain_asset(
                        asset_id,
                        item["name"],
                        asset_kind,
                        **{
                            k: v
                            for k, v in item.items()
                            if k not in {"name", "kind"}
                        },
                    )
                    raw["assets"][asset_id] = record
                    known[identity] = record
                    search_documents[asset_id] = record
                    added += 1
                if added or updated:
                    repo._bump(raw)
                repo._upsert_asset_search_documents(
                    search_documents.values(),
                    frame_decoder=_decode_shot_vector_frame,
                )
                result = {
                    "indexed": added,
                    "updated": updated,
                    "scanned": len(valid_files),
                    "revision": raw["revision"],
                }
                # QIN-600：生成元数据提取的可复核统计（终态后不变）。
                if generation_stats is not None:
                    result["generation"] = dict(generation_stats)
                # QIN-652：快照收据带标记，避免富化阶段被投影成终态。
                if enrich_pending:
                    result["enrich_pending"] = True
                # QIN-564 AC1/AC4：哈希模式任务结果如实区分「完整哈希 N /
                # 超限转采样 M」；非哈希任务不附加哈希计数（诚实口径）。
                if job["_request"].get("hash_files"):
                    result["hashed"] = sum(
                        1
                        for x in valid_files
                        if x.get("hash_mode") == HASH_MODE_FULL
                    )
                    result["hash_sampled_oversize"] = sum(
                        1
                        for x in valid_files
                        if x.get("hash_mode") == HASH_MODE_SAMPLED
                    )
            else:
                scoped = {x for x in known if x[0] in job["_request"]["roots"]}
                result = {
                    "on_disk": len(disk),
                    "registered": len(scoped),
                    "missing_from_registry": len(disk - scoped),
                    "missing_from_disk": len(scoped - disk),
                    "revision": raw["revision"],
                }
            if receipt_mode == "skip":
                return result
            receipts[receipt_id] = result
            if receipt_mode == "write":
                repo.write_resource_folder_registration_receipt(raw, job)
            return result

        # 提交前最后一次实时核验；令牌只包裹注册表写入，避免跨任务泄漏。
        execution_context = self._reverify_workspace_context(job)
        context_token = (
            workspace_context.set_execution_context(execution_context)
            if execution_context
            else None
        )
        try:
            return repo.state().mutate(commit)
        finally:
            if context_token is not None:
                workspace_context.reset_execution_context(context_token)


class _ScanFiles(list):
    """扫描结果：保持 list 语义，附带统计与后台富化任务（QIN-600 / QIN-652）。

    既有调用方与契约测试都把 `_scan` 的返回值当列表使用（遍历、`len`），
    因此统计与富化清单挂在列表子类的属性上，而不是把返回值改成元组。
    """

    __slots__ = ("generation_stats", "enrich_tasks", "scan_paths")


class _Stopped(Exception):
    """取消或新尝试已接管，不落失败状态覆盖新任务。"""


_default = None
_default_lock = threading.Lock()


def service():
    global _default
    with _default_lock:
        root = storage.data_root().resolve()
        if _default is None or _default.root != root or _default.closed:
            if _default is not None:
                _default.shutdown()
            _default = IndexJobs()
        return _default


def shutdown():
    """应用关闭只停止自身索引线程，不清理任务、资产和用户文件。"""
    if _default is not None:
        _default.shutdown()
    try:
        from gw.asset_registry.dedup_confirm import shutdown as shutdown_dedup_confirm

        shutdown_dedup_confirm()
    except Exception:
        pass


def _active_job_count():
    if _default is None:
        return 0
    return _default.active_job_count()


def _register_write_window_host():
    from gw.core.write_window import get_write_window

    get_write_window().register_host("asset_index", _active_job_count)


_register_write_window_host()
