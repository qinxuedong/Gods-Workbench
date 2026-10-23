# -*- coding: utf-8 -*-
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

"""本地 FFmpeg 母带导出 API。"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, Header, Request

from gw.asset_registry import repository as asset_repository
from gw.core import storage
from gw.core.auth import require_edit_access
from gw.core.errors import CleanroomException
from gw.episode_pipeline import repository as episode_repository
from gw.media import ffmpeg_master

router = APIRouter()



def _timeline_manifest(payload: Mapping[str, Any]) -> Mapping[str, Any]:
    manifest = payload.get("timeline_manifest")
    if not isinstance(manifest, Mapping):
        raise CleanroomException(400, "INVALID_TIMELINE_MANIFEST", "timeline_manifest 不存在或结构不合法")
    return manifest



def _registered_media_paths(manifest: Mapping[str, Any]) -> dict[str, Path]:
    tracks = manifest.get("tracks")
    if not isinstance(tracks, list):
        raise CleanroomException(400, "INVALID_TIMELINE_MANIFEST", "timeline_manifest.tracks 必须是数组")
    paths: dict[str, Path] = {}
    for track in tracks:
        if not isinstance(track, Mapping):
            raise CleanroomException(400, "INVALID_TIMELINE_MANIFEST", "时间线轨道结构不合法")
        clips = track.get("clips", [])
        if not isinstance(clips, list):
            raise CleanroomException(400, "INVALID_TIMELINE_MANIFEST", "时间线 clips 必须是数组")
        for clip in clips:
            if not isinstance(clip, Mapping):
                raise CleanroomException(400, "INVALID_TIMELINE_MANIFEST", "时间线片段结构不合法")
            asset_id = str(clip.get("asset_id") or "").strip()
            if not asset_id:
                continue
            if asset_id not in paths:
                media_path = asset_repository.media_file(asset_id)
                if media_path is None or not Path(media_path).is_file():
                    raise CleanroomException(404, "MEDIA_NOT_AVAILABLE", "时间线素材未登记或不可用")
                paths[asset_id] = Path(media_path)
    return paths



@router.post("/api/episode-pipelines/{pipeline_id}/export")
async def export_master_mp4(
    pipeline_id: str,
    request: Request,
    authorization: str | None = Header(None),
    x_user_role: str = Header("editor", alias="X-User-Role"),
):
    """从已保存时间线压制本地 1080p H.264 Master MP4。"""
    require_edit_access(authorization, x_user_role)
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    if not isinstance(payload, Mapping):
        payload = {}
    use_hardware = payload.get("use_hardware_acceleration", True)
    if not isinstance(use_hardware, bool):
        raise CleanroomException(400, "INVALID_EXPORT_OPTIONS", "use_hardware_acceleration 必须为布尔值")

    stored = episode_repository.get_timeline_manifest(pipeline_id)
    manifest = _timeline_manifest(stored)
    asset_paths = _registered_media_paths(manifest)

    relative_directory = "media_output/master"
    output_directory = storage.resolve_output_directory(relative_directory, must_exist=False)
    output_directory.mkdir(parents=True, exist_ok=True)
    output_directory = storage.resolve_output_directory(relative_directory, must_exist=True)
    filename = f"master_{uuid4().hex}.mp4"
    output_path = output_directory / filename
    result = ffmpeg_master.render_master_mp4(
        manifest,
        asset_paths,
        output_path,
        prefer_hardware=use_hardware,
    )
    relative_output = f"{relative_directory}/{filename}"
    return {
        "pipeline_id": pipeline_id,
        "output": relative_output,
        "sha256": result.get("sha256", ""),
        "bytes": result["bytes"],
        "duration_seconds": result["duration_seconds"],
        "width": result["width"],
        "height": result["height"],
        "frame_count": result.get("frame_count", 0),
        "video_codec": result["video_codec"],
        "audio_codec": result["audio_codec"],
        "encoder": result["encoder"],
        "hardware_accelerated": result["hardware_accelerated"],
    }
