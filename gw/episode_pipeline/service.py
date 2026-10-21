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

"""剧集时间线服务层。"""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from typing import Any, Dict

from gw.core.errors import CleanroomException, VersionConflictException
from gw.episode_pipeline import repository


def replace_timeline_clip_with_video(
    manifest: Mapping[str, Any],
    *,
    shot_id: str,
    video_asset_id: str,
    video_src: str,
) -> dict[str, Any]:
    """原位升级一个时间线镜头，并保留剪辑师配置的所有 overrides。"""
    if not isinstance(manifest, Mapping):
        raise CleanroomException(
            400, "INVALID_TIMELINE_MANIFEST", "timeline_manifest 必须是对象"
        )
    shot = str(shot_id or "").strip()
    asset = str(video_asset_id or "").strip()
    source = str(video_src or "").strip()
    if not shot or not asset or not source:
        raise CleanroomException(
            400,
            "INVALID_TIMELINE_RELINK",
            "shot_id、video_asset_id 与 video_src 均不能为空",
        )

    updated = deepcopy(dict(manifest))
    tracks = updated.get("tracks")
    if not isinstance(tracks, list):
        raise CleanroomException(
            400, "INVALID_TIMELINE_MANIFEST", "timeline_manifest.tracks 必须是数组"
        )

    matches = []
    for track in tracks:
        if not isinstance(track, dict) or not isinstance(track.get("clips"), list):
            continue
        matches.extend(
            clip
            for clip in track["clips"]
            if isinstance(clip, dict) and clip.get("shot_id") == shot
        )
    if not matches:
        raise CleanroomException(
            404, "TIMELINE_CLIP_NOT_FOUND", "时间线中不存在指定 shot_id"
        )
    if len(matches) != 1:
        raise CleanroomException(
            409, "TIMELINE_CLIP_AMBIGUOUS", "时间线中指定 shot_id 不唯一"
        )

    clip = matches[0]
    overrides = clip.get("overrides")
    if overrides is None:
        overrides = {}
    if not isinstance(overrides, dict):
        raise CleanroomException(
            400, "INVALID_TIMELINE_MANIFEST", "片段 overrides 必须是对象"
        )

    clip["asset_id"] = asset
    clip["media_type"] = "video"
    clip["overrides"] = {**overrides, "src": source}
    return updated


def relink_timeline_video(
    pipeline_id: str,
    *,
    shot_id: str,
    video_asset_id: str,
    video_src: str,
    expected_version: int,
) -> dict[str, Any]:
    """在 timeline_manifest 上应用原位视频替换并通过仓库 CAS 持久化。"""
    if (
        isinstance(expected_version, bool)
        or not isinstance(expected_version, int)
        or expected_version < 1
    ):
        raise CleanroomException(
            400, "INVALID_VERSION", "expected_version 必须为正整数"
        )

    current = repository.get_timeline_manifest(pipeline_id)["timeline_manifest"]
    current_version = int(current.get("version") or 1)
    if expected_version != current_version:
        raise VersionConflictException(
            expected_version,
            current_version,
            "timeline_manifest 版本冲突，请重新读取后重试",
        )

    updated = replace_timeline_clip_with_video(
        current,
        shot_id=shot_id,
        video_asset_id=video_asset_id,
        video_src=video_src,
    )
    return repository.update_timeline_manifest(
        pipeline_id,
        {"timeline_manifest": updated, "expected_version": expected_version},
    )
