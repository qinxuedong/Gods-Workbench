"""C14-T2 视频模型执行边界与异步任务状态适配。

本模块只处理统一任务契约、GNS-2026 命名和供应商适配边界。供应商对象由调用方注入，
因此不会读取凭据、猜测服务地址或把 fixture 当作真实执行结果。
"""
from __future__ import annotations

import inspect
import re
import uuid
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import Any, Protocol

from gw.core.errors import CleanroomException
from gw.media.duration_fitting import build_i2v_payload

_GNS_ID = re.compile(r"^[A-Z]{2}\d{2}$")
_GNS_SHOT = re.compile(r"^SH\d{3}$")
_GNS_VERSION = re.compile(r"^v\d{3}$")
_VARIANTS = frozenset({"raw", "master", "proxy", "preview", "thumb", "sprite"})
_VIDEO_STATES = frozenset({"accepted", "running", "completed", "failed", "cancelled"})


class VideoProvider(Protocol):
    async def submit(self, payload: dict[str, Any]) -> dict[str, Any]: ...
    async def poll(self, remote_task_id: str) -> dict[str, Any]: ...
    async def cancel(self, remote_task_id: str) -> dict[str, Any]: ...


def _text(value: Any, field: str, pattern: re.Pattern[str]) -> str:
    if not isinstance(value, str) or not pattern.fullmatch(value):
        raise ValueError(f"{field} must use the canonical ASCII format")
    return value


def build_video_filename(
    episode: str,
    scene: str,
    shot: str,
    version: int | str,
    *,
    variant: str = "raw",
) -> str:
    """构造 GNS-2026 语义视频名，例如 ``EP01_SC01_SH001_v001_raw.mp4``。"""
    episode = _text(episode, "episode", _GNS_ID)
    scene = _text(scene, "scene", _GNS_ID)
    shot = _text(shot, "shot", _GNS_SHOT)
    if isinstance(version, bool):
        raise ValueError("version must be a positive integer")
    if isinstance(version, int) and 1 <= version <= 999:
        version_text = f"v{version:03d}"
    elif isinstance(version, str) and _GNS_VERSION.fullmatch(version):
        version_text = version
    else:
        raise ValueError("version must be between 1 and 999 or v001 format")
    if variant not in _VARIANTS:
        raise ValueError("variant is not in the GNS-2026 whitelist")
    return f"{episode}_{scene}_{shot}_{version_text}_{variant}.mp4"


def build_storage_path(job_id: str, *, root: str = "artifacts") -> str:
    """返回 ASCII 物理存储路径；语义名称仍保存在资产登记中。"""
    if not isinstance(job_id, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}", job_id):
        raise ValueError("job_id must be a filesystem-safe ASCII identifier")
    if not isinstance(root, str) or not root or any(part in {"", ".", ".."} for part in PurePosixPath(root).parts):
        raise ValueError("root must be a safe relative path")
    return f"{PurePosixPath(root) / (job_id + '.mp4')}"


def _state(value: Any) -> str:
    value = str(value or "running").lower()
    return {"queued": "accepted", "running": "running", "succeeded": "completed", "completed": "completed",
            "failed": "failed", "error": "failed", "canceled": "cancelled", "cancelled": "cancelled",
            "interrupted": "cancelled"}.get(value, "running")


async def _call(method: Any, *args: Any) -> Any:
    result = method(*args)
    return await result if inspect.isawaitable(result) else result


@dataclass
class VideoJobExecutor:
    provider: VideoProvider
    storage_root: str = "artifacts"
    _jobs: dict[str, dict[str, Any]] = field(default_factory=dict)

    async def submit(
        self,
        *,
        shot_id: str,
        episode: str,
        scene: str,
        version: int | str,
        image_asset_id: str,
        target_duration: float,
        prompt: str,
        camera_ctrl: Any = None,
        lora: Any = None,
        fixture_id: Any = None,
        variant: str = "raw",
    ) -> dict[str, Any]:
        payload = build_i2v_payload(
            shot_id=shot_id, image_asset_id=image_asset_id, target_duration=target_duration,
            prompt=prompt, camera_ctrl=camera_ctrl, lora=lora, fixture_id=fixture_id,
        )
        filename = build_video_filename(episode, scene, shot_id, version, variant=variant)
        job_id = str(uuid.uuid4())
        remote = await _call(self.provider.submit, payload)
        remote_id = remote.get("prompt_id") or remote.get("job_id") if isinstance(remote, dict) else None
        if not isinstance(remote_id, str) or not remote_id.strip():
            raise CleanroomException(502, "WORKFLOW_PROVIDER_INVALID_RESPONSE", "视频服务未返回任务 ID")
        job = {
            "job_id": job_id, "remote_task_id": remote_id.strip(), "status": "accepted",
            "shot_id": shot_id, "episode": episode, "scene": scene,
            "version": f"v{int(version):03d}" if isinstance(version, int) else version,
            "filename": filename, "path": build_storage_path(job_id, root=self.storage_root),
            "payload": payload,
        }
        self._jobs[job_id] = job
        return dict(job)

    async def poll(self, job_id: str) -> dict[str, Any]:
        job = self._get(job_id)
        if job["status"] in {"completed", "failed", "cancelled"}:
            return dict(job)
        response = await _call(self.provider.poll, job["remote_task_id"])
        response = response if isinstance(response, dict) else {}
        status = _state(response.get("status"))
        job["status"] = status
        if status == "completed":
            asset_id = str(uuid.uuid4())
            job["outputs"] = response.get("outputs") if isinstance(response.get("outputs"), list) else []
            job["asset"] = {
                "asset_id": asset_id, "job_id": job["job_id"], "filename": job["filename"],
                "path": job["path"], "content_type": "video/mp4", "source_shot_id": job["shot_id"],
                "source_version": job["version"], "origin": "video_model_executor",
            }
        elif status == "failed":
            job["error"] = response.get("error") or "video provider failed"
        return dict(job)

    async def cancel(self, job_id: str) -> dict[str, Any]:
        job = self._get(job_id)
        if job["status"] in {"completed", "failed", "cancelled"}:
            return dict(job)
        response = await _call(self.provider.cancel, job["remote_task_id"])
        response = response if isinstance(response, dict) else {}
        remote_cancelled = bool(response.get("remote_cancelled"))
        job["status"] = "cancelled"
        job["remote_cancelled"] = remote_cancelled
        job["remote_may_continue_or_bill"] = not remote_cancelled
        return dict(job)

    def _get(self, job_id: str) -> dict[str, Any]:
        job = self._jobs.get(job_id)
        if job is None:
            raise CleanroomException(404, "TASK_NOT_FOUND", "视频任务不存在")
        return job


__all__ = ["VideoJobExecutor", "VideoProvider", "build_storage_path", "build_video_filename"]
