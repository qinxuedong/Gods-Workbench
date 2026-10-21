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

"""从场景区间真实抽取 WebP 缩略图并生成联系雪碧图。"""

from __future__ import annotations

import io
import json
import math
import shutil
import subprocess
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from PIL import Image

from gw.core.errors import CleanroomException

_PROCESS_TIMEOUT_SECONDS = 120
_THUMBNAIL_WIDTH = 320
_THUMBNAIL_HEIGHT = 180


def _fail(status: int, code: str, message: str) -> None:
    raise CleanroomException(status, code, message)


def _tool(name: str) -> str:
    path = shutil.which(name)
    if not path:
        _fail(503, "FFMPEG_NOT_AVAILABLE", "本地媒体处理依赖不可用")
    return path


def _run(args: list[str]) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=_PROCESS_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        _fail(503, "MEDIA_PROCESS_FAILED", "本地媒体处理进程失败")


def _validate_scenes(scenes: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    if isinstance(scenes, (str, bytes)) or not isinstance(scenes, Iterable):
        _fail(400, "INVALID_SCENE_METADATA", "场景区间必须是非空对象序列")
    normalized: list[dict[str, Any]] = []
    previous_end_ms = 0
    for scene in scenes:
        if not isinstance(scene, Mapping):
            _fail(400, "INVALID_SCENE_METADATA", "场景区间结构无效")
        try:
            start = float(scene["start_sec"])
            end = float(scene["end_sec"])
        except (KeyError, TypeError, ValueError, OverflowError):
            _fail(400, "INVALID_SCENE_METADATA", "场景区间缺少有效起止时间")
        if (
            not math.isfinite(start)
            or not math.isfinite(end)
            or start < 0
            or end <= start
        ):
            _fail(400, "INVALID_SCENE_METADATA", "场景区间起止时间无效")
        start_ms = int(round(start * 1000))
        end_ms = int(round(end * 1000))
        if start_ms != previous_end_ms or end_ms <= start_ms:
            _fail(400, "INVALID_SCENE_METADATA", "场景区间必须连续且至少持续 1 毫秒")
        tags = scene.get("tags", [])
        if not isinstance(tags, list) or any(not isinstance(tag, str) for tag in tags):
            _fail(400, "INVALID_SCENE_METADATA", "场景 tags 必须是字符串列表")
        normalized.append(
            {
                "start_sec": round(start_ms / 1000, 3),
                "end_sec": round(end_ms / 1000, 3),
                "tags": list(tags),
            }
        )
        previous_end_ms = end_ms
    if not normalized:
        _fail(400, "INVALID_SCENE_METADATA", "场景区间必须是非空对象序列")
    return normalized


def _save_sprite(thumbnails: list[Path], target: Path, columns: int) -> None:
    rows = (len(thumbnails) + columns - 1) // columns
    sprite = Image.new(
        "RGB",
        (columns * _THUMBNAIL_WIDTH, rows * _THUMBNAIL_HEIGHT),
        (16, 16, 16),
    )
    try:
        for index, path in enumerate(thumbnails):
            with Image.open(path) as source:
                tile = source.convert("RGB")
            tile.thumbnail(
                (_THUMBNAIL_WIDTH, _THUMBNAIL_HEIGHT), Image.Resampling.LANCZOS
            )
            x = (index % columns) * _THUMBNAIL_WIDTH
            y = (index // columns) * _THUMBNAIL_HEIGHT
            sprite.paste(tile, (x, y))
        buffer = io.BytesIO()
        sprite.save(buffer, format="WEBP", quality=75, method=6)
        with target.open("xb") as output:
            output.write(buffer.getvalue())
    finally:
        sprite.close()


def extract_keyframes(
    video_path: str | Path,
    scenes: Iterable[Mapping[str, Any]],
    output_dir: str | Path,
    *,
    columns: int = 4,
) -> dict[str, Any]:
    """在每个镜头起点抽取 WebP 缩略图，并合成为 WebP 雪碧图。

    元数据仅含输出目录内的文件名，不泄露本机绝对路径；已有同名输出会失败，
    不覆盖用户文件。``tags`` 逐字保留，语义标签需由其他获准能力生成。
    """
    try:
        path = Path(video_path)
        destination = Path(output_dir)
    except (TypeError, ValueError):
        _fail(400, "INVALID_OUTPUT_PATH", "视频或输出目录路径无效")
    if not path.is_file():
        _fail(400, "VIDEO_NOT_AVAILABLE", "视频文件不存在")
    if (
        isinstance(columns, bool)
        or not isinstance(columns, int)
        or columns < 1
        or columns > 12
    ):
        _fail(400, "INVALID_SPRITE_COLUMNS", "雪碧图列数必须在 1 到 12 之间")

    metadata = _validate_scenes(scenes)
    ffmpeg = _tool("ffmpeg")
    try:
        destination.mkdir(parents=True, exist_ok=True)
    except OSError:
        _fail(503, "OUTPUT_DIRECTORY_UNAVAILABLE", "无法创建媒体输出目录")

    thumbnail_paths = [
        destination / f"scene_{index:03d}.webp" for index in range(1, len(metadata) + 1)
    ]
    sprite_name = "scene_sprite.webp"
    sprite_path = destination / sprite_name
    metadata_path = destination / "metadata.json"
    if (
        metadata_path.exists()
        or sprite_path.exists()
        or any(item.exists() for item in thumbnail_paths)
    ):
        _fail(409, "MEDIA_OUTPUT_EXISTS", "媒体输出文件已存在，拒绝覆盖")

    created: list[Path] = []
    try:
        for scene, target in zip(metadata, thumbnail_paths):
            result = _run(
                [
                    ffmpeg,
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-n",
                    "-i",
                    str(path),
                    "-ss",
                    f"{scene['start_sec']:.3f}",
                    "-frames:v",
                    "1",
                    "-vf",
                    (
                        f"scale={_THUMBNAIL_WIDTH}:{_THUMBNAIL_HEIGHT}:"
                        "force_original_aspect_ratio=decrease,"
                        f"pad={_THUMBNAIL_WIDTH}:{_THUMBNAIL_HEIGHT}:(ow-iw)/2:(oh-ih)/2"
                    ),
                    "-c:v",
                    "libwebp",
                    "-quality",
                    "75",
                    str(target),
                ]
            )
            if (
                result.returncode != 0
                or not target.is_file()
                or target.stat().st_size <= 0
            ):
                _fail(503, "KEYFRAME_EXTRACTION_FAILED", "FFmpeg 无法抽取场景关键帧")
            created.append(target)

        _save_sprite(thumbnail_paths, sprite_path, columns)
        created.append(sprite_path)

        segments = [
            {
                **scene,
                "thumbnail": target.name,
            }
            for scene, target in zip(metadata, thumbnail_paths)
        ]
        registry_metadata = {"video_segments": segments}
        with metadata_path.open("x", encoding="utf-8", newline="\n") as output:
            created.append(metadata_path)
            json.dump(
                {"metadata": registry_metadata},
                output,
                ensure_ascii=False,
                indent=2,
            )
            output.write("\n")
    except CleanroomException:
        for item in created:
            item.unlink(missing_ok=True)
        raise
    except Exception:
        for item in created:
            item.unlink(missing_ok=True)
        _fail(503, "MEDIA_OUTPUT_GENERATION_FAILED", "无法生成媒体输出或结构化元数据")

    return {"metadata": registry_metadata, "sprite": sprite_name}


__all__ = ["extract_keyframes"]
