"""C18-AC2 本地 FFmpeg 镜头切片与 WebP 关键帧契约测试。"""

from __future__ import annotations

import json
import shutil
import subprocess

import pytest

from gw.media.keyframe_extractor import extract_keyframes
from gw.media.scene_detector import detect_scenes


def test_detect_scenes_builds_millisecond_segment_metadata(tmp_path, monkeypatch):
    video = tmp_path / "fixture.mp4"
    video.write_bytes(b"test input")
    calls = []

    def fake_run(args, **kwargs):
        calls.append(args)
        if "ffprobe" in args[0]:
            return subprocess.CompletedProcess(
                args,
                0,
                stdout=(
                    '{"streams":[{"codec_name":"h264","width":160,"height":90}],'
                    '"format":{"duration":"2.000000","bit_rate":"1000000"}}'
                ),
                stderr="",
            )
        return subprocess.CompletedProcess(
            args,
            0,
            stdout="",
            stderr=(
                "[Parsed_showinfo_1] n: 10 pts: 1000 pts_time:1.000000 "
                "fmt:yuv420p s:160x90\n"
            ),
        )

    monkeypatch.setattr("gw.media.scene_detector.shutil.which", lambda name: name)
    monkeypatch.setattr("gw.media.scene_detector.subprocess.run", fake_run)

    scenes = detect_scenes(video)

    assert scenes == [
        {
            "start_sec": 0.0,
            "end_sec": 1.0,
            "duration_sec": 1.0,
            "width": 160,
            "height": 90,
            "codec": "h264",
            "bit_rate": 1000000,
            "container": "mp4",
            "source_url": None,
            "tags": [],
            "thumbnail": None,
        },
        {
            "start_sec": 1.0,
            "end_sec": 2.0,
            "duration_sec": 1.0,
            "width": 160,
            "height": 90,
            "codec": "h264",
            "bit_rate": 1000000,
            "container": "mp4",
            "source_url": None,
            "tags": [],
            "thumbnail": None,
        },
    ]
    assert len(calls) == 2
    assert any("gt(scene,0.3)" in argument for argument in calls[1])


def test_real_ffmpeg_detects_scene_and_writes_webp_keyframes_and_sprite(tmp_path):
    ffmpeg = shutil.which("ffmpeg")
    ffprobe = shutil.which("ffprobe")
    if not ffmpeg or not ffprobe:
        pytest.skip("ffmpeg/ffprobe 未安装")

    video = tmp_path / "hard-cut.mp4"
    generated = subprocess.run(
        [
            ffmpeg,
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=c=black:s=160x90:r=10:d=1",
            "-f",
            "lavfi",
            "-i",
            "color=c=white:s=160x90:r=10:d=1",
            "-filter_complex",
            "[0:v][1:v]concat=n=2:v=1:a=0,format=yuv420p[v]",
            "-map",
            "[v]",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-pix_fmt",
            "yuv420p",
            str(video),
        ],
        capture_output=True,
        timeout=30,
        check=False,
    )
    if generated.returncode != 0 or not video.is_file():
        pytest.skip("当前 ffmpeg 不支持 lavfi/libx264 测试夹具")

    scenes = detect_scenes(video)
    assert len(scenes) == 2
    assert scenes[0]["start_sec"] == 0.0
    assert abs(scenes[1]["start_sec"] - 1.0) <= 0.1
    assert scenes[-1]["end_sec"] == 2.0
    assert scenes[0]["width"] == 160
    assert scenes[0]["height"] == 90
    assert scenes[0]["codec"] == "h264"
    assert scenes[0]["duration_sec"] == scenes[0]["end_sec"]
    assert scenes[0]["source_url"] is None

    result = extract_keyframes(video, scenes, tmp_path / "grounding")
    metadata_file = tmp_path / "grounding" / "metadata.json"

    assert result["sprite"] == "scene_sprite.webp"
    assert set(result) == {"metadata", "sprite"}
    assert set(result["metadata"]) == {"video_segments"}
    persisted = json.loads(metadata_file.read_text(encoding="utf-8"))
    assert persisted == {"metadata": result["metadata"]}
    assert len(result["metadata"]["video_segments"]) == 2
    assert (tmp_path / "grounding" / result["sprite"]).read_bytes().startswith(b"RIFF")
    assert (tmp_path / "grounding" / result["sprite"]).read_bytes()[8:12] == b"WEBP"
    assert len(result["metadata"]["video_segments"]) == 2
    for segment in result["metadata"]["video_segments"]:
        assert set(segment) == {"start_sec", "end_sec", "tags", "thumbnail"}
        thumbnail = tmp_path / "grounding" / segment["thumbnail"]
        assert thumbnail.is_file()
        assert thumbnail.read_bytes()[8:12] == b"WEBP"
