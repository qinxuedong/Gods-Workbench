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

"""C15-AC3: 本地 FFmpeg 按时间线导出可验证的 Master MP4。"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from pathlib import Path

import pytest
from gw.api.app import create_app
from fastapi.testclient import TestClient

from gw.api import routes_export
from gw.core.errors import CleanroomException
from gw.media import ffmpeg_master


AUTH = {"Authorization": "Bearer cleanroom-test", "X-User-Role": "editor"}
FIXTURE_ID = "c15-t3-master-export-001"


def _manifest() -> dict:
    return {
        "version": 1,
        "tracks": [
            {
                "track_id": "video-01",
                "kind": "video",
                "clips": [
                    {
                        "shot_id": "SC01_SH001",
                        "asset_id": "asset-video-001",
                        "media_type": "video",
                        "at": 0.0,
                        "duration": 1.0,
                        "overrides": {
                            "crop": {"x": 0.1, "y": 0.2, "width": 0.8, "height": 0.7}
                        },
                    },
                    {
                        "shot_id": "SC01_SH002",
                        "asset_id": "asset-video-002",
                        "media_type": "video",
                        "at": 1.5,
                        "duration": 1.0,
                        "overrides": {},
                    },
                ],
            },
            {
                "track_id": "audio-01",
                "kind": "audio",
                "clips": [
                    {"asset_id": "asset-audio-001", "at": 0.0, "duration": 2.5}
                ],
            },
        ],
    }


def test_build_master_command_keeps_timeline_gaps_crop_and_audio_alignment(tmp_path: Path):
    manifest = _manifest()
    paths = {
        "asset-video-001": tmp_path / "one.mp4",
        "asset-video-002": tmp_path / "two.mp4",
        "asset-audio-001": tmp_path / "dialogue.wav",
    }

    command = ffmpeg_master.build_ffmpeg_command(
        manifest,
        paths,
        tmp_path / "master.part.mp4",
        ffmpeg_path="ffmpeg",
        encoder="libx264",
    )

    assert command[0] == "ffmpeg"
    assert command.count("-i") == 3
    graph = command[command.index("-filter_complex") + 1]
    assert "crop=iw*0.8:ih*0.7:iw*0.1:ih*0.2" in graph
    assert "color=c=black:s=1920x1080:r=24:d=0.5" in graph
    assert "adelay=0|0" in graph
    assert "-map" in command and "[vout]" in command and "[aout]" in command
    assert command[command.index("-c:v") + 1] == "libx264"
    assert command[command.index("-c:a") + 1] == "aac"
    assert command[-1].endswith("master.part.mp4")
    assert manifest["tracks"][0]["clips"][0]["overrides"]["crop"]["width"] == 0.8


def test_build_master_command_rejects_missing_assets_and_overlapping_clips(tmp_path: Path):
    with pytest.raises(CleanroomException) as missing:
        ffmpeg_master.build_ffmpeg_command(
            _manifest(), {}, tmp_path / "out.mp4", ffmpeg_path="ffmpeg", encoder="libx264"
        )
    assert missing.value.code == "MEDIA_NOT_AVAILABLE"

    manifest = _manifest()
    manifest["tracks"][0]["clips"][1]["at"] = 0.5
    with pytest.raises(CleanroomException) as overlap:
        ffmpeg_master.build_ffmpeg_command(
            manifest,
            {
                "asset-video-001": tmp_path / "one.mp4",
                "asset-video-002": tmp_path / "two.mp4",
                "asset-audio-001": tmp_path / "dialogue.wav",
            },
            tmp_path / "out.mp4",
            ffmpeg_path="ffmpeg",
            encoder="libx264",
        )
    assert overlap.value.code == "UNSUPPORTED_TIMELINE_OVERLAP"


def test_master_export_falls_back_to_cpu_after_hardware_encoder_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    source = tmp_path / "source.mp4"
    source.write_bytes(b"input")
    calls: list[list[str]] = []

    monkeypatch.setattr(ffmpeg_master, "_hardware_encoder", lambda _ffmpeg: "h264_nvenc")
    monkeypatch.setattr(
        ffmpeg_master,
        "_probe_output",
        lambda _path, _ffprobe: {"duration_seconds": 1.0, "width": 1920, "height": 1080, "frame_count": 24,
                                 "video_codec": "h264", "audio_codec": None},
    )

    def fake_run(command, *, timeout):
        calls.append(command)
        if "h264_nvenc" in command:
            return subprocess.CompletedProcess(command, 1, stdout=b"", stderr=b"no device")
        Path(command[-1]).write_bytes(b"valid-mp4")
        return subprocess.CompletedProcess(command, 0, stdout=b"", stderr=b"")

    monkeypatch.setattr(ffmpeg_master, "_run", fake_run)
    result = ffmpeg_master.render_master_mp4(
        {"tracks": [{"kind": "video", "clips": [
            {"asset_id": "video-001", "media_type": "video", "at": 0, "duration": 1}
        ]}]},
        {"video-001": source},
        tmp_path / "master.mp4",
        ffmpeg_path="fake-ffmpeg",
        ffprobe_path="fake-ffprobe",
        prefer_hardware=True,
    )

    assert len(calls) == 2
    assert result["encoder"] == "libx264"
    assert result["hardware_accelerated"] is False
    assert result["bytes"] == len(b"valid-mp4")
    assert (tmp_path / "master.mp4").is_file()
    assert not (tmp_path / "master.part.mp4").exists()


def test_master_export_rejects_missing_frames_before_publishing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    source = tmp_path / "source.mp4"
    source.write_bytes(b"input")
    monkeypatch.setattr(ffmpeg_master, "_probe_output", lambda *_args: {
        "duration_seconds": 23 / 24,
        "width": 1920,
        "height": 1080,
        "frame_count": 23,
        "video_codec": "h264",
        "audio_codec": None,
    })

    def fake_run(command, *, timeout):
        Path(command[-1]).write_bytes(b"truncated-mp4")
        return subprocess.CompletedProcess(command, 0, stdout=b"", stderr=b"")

    monkeypatch.setattr(ffmpeg_master, "_run", fake_run)
    output = tmp_path / "master.mp4"
    with pytest.raises(CleanroomException) as mismatch:
        ffmpeg_master.render_master_mp4(
            {"tracks": [{"kind": "video", "clips": [
                {"asset_id": "video-001", "media_type": "video", "at": 0, "duration": 1}
            ]}]},
            {"video-001": source},
            output,
            ffmpeg_path="fake-ffmpeg",
            ffprobe_path="fake-ffprobe",
            prefer_hardware=False,
        )

    assert mismatch.value.code == "MASTER_EXPORT_FRAME_COUNT_MISMATCH"
    assert not output.exists()
    assert not (tmp_path / "master.part.mp4").exists()


def test_c15_ac3_real_ffmpeg_export_has_1080p_video_and_synchronized_audio(tmp_path: Path):
    ffmpeg = shutil.which("ffmpeg")
    ffprobe = shutil.which("ffprobe")
    if not ffmpeg or not ffprobe:
        pytest.skip("ffmpeg/ffprobe 未安装")

    video = tmp_path / "source.mp4"
    audio = tmp_path / "dialogue.wav"
    video_result = subprocess.run(
        [ffmpeg, "-v", "error", "-y", "-f", "lavfi", "-i",
         "color=c=blue:s=64x36:r=24:d=0.4", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(video)],
        capture_output=True,
        timeout=30,
        check=False,
    )
    audio_result = subprocess.run(
        [ffmpeg, "-v", "error", "-y", "-f", "lavfi", "-i",
         "sine=frequency=1000:sample_rate=48000:duration=0.4", "-c:a", "pcm_s16le", str(audio)],
        capture_output=True,
        timeout=30,
        check=False,
    )
    if video_result.returncode or audio_result.returncode:
        pytest.skip("当前 FFmpeg 构建不支持本地 lavfi 测试源")

    manifest = {
        "tracks": [
            {"kind": "video", "clips": [
                {"asset_id": "video-001", "media_type": "video", "at": 0.0, "duration": 0.4}
            ]},
            {"kind": "audio", "clips": [
                {"asset_id": "audio-001", "at": 0.0, "duration": 0.4}
            ]},
        ]
    }
    output = tmp_path / "master.mp4"
    result = ffmpeg_master.render_master_mp4(
        manifest,
        {"video-001": video, "audio-001": audio},
        output,
        ffmpeg_path=ffmpeg,
        prefer_hardware=False,
    )

    probed = subprocess.run(
        [ffprobe, "-v", "error", "-count_frames", "-show_entries",
         "format=duration:stream=codec_type,codec_name,width,height,duration,nb_read_frames",
         "-of", "json", str(output)],
        capture_output=True,
        text=True,
        timeout=30,
        check=True,
    )
    metadata = json.loads(probed.stdout)
    streams = {stream["codec_type"]: stream for stream in metadata["streams"]}
    automatic_output = tmp_path / "master-auto.mp4"
    automatic_result = ffmpeg_master.render_master_mp4(
        manifest,
        {"video-001": video, "audio-001": audio},
        automatic_output,
        ffmpeg_path=ffmpeg,
        ffprobe_path=ffprobe,
        prefer_hardware=True,
    )
    video_duration = float(streams["video"].get("duration") or metadata["format"]["duration"])
    audio_duration = float(streams["audio"].get("duration") or metadata["format"]["duration"])

    assert result["encoder"] == "libx264"
    assert result["hardware_accelerated"] is False
    assert streams["video"]["width"] == 1920
    assert streams["video"]["height"] == 1080
    assert streams["video"]["codec_name"] == "h264"
    assert streams["audio"]["codec_name"] == "aac"
    assert abs(video_duration - audio_duration) <= 0.06
    assert 0.35 <= float(metadata["format"]["duration"]) <= 0.5
    assert result["bytes"] == output.stat().st_size
    assert result["sha256"] == hashlib.sha256(output.read_bytes()).hexdigest()
    assert int(streams["video"]["nb_read_frames"]) == 10
    assert result["frame_count"] == 10
    assert automatic_result["hardware_accelerated"] == (automatic_result["encoder"] != "libx264")
    assert automatic_output.is_file()
    assert FIXTURE_ID == "c15-t3-master-export-001"


def test_export_route_requires_edit_access_and_returns_only_relative_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    source = tmp_path / "registered-source.mp4"
    source.write_bytes(b"source")
    manifest = {"tracks": [{"kind": "video", "clips": [
        {"asset_id": "video-001", "media_type": "video", "at": 0, "duration": 0.4}
    ]}]}
    monkeypatch.setattr(
        routes_export.episode_repository,
        "get_timeline_manifest",
        lambda _pipeline_id: {"timeline_manifest": manifest},
    )
    monkeypatch.setattr(routes_export.asset_repository, "media_file", lambda _asset_id: source)

    def fake_render(_manifest, _paths, output_path, **_kwargs):
        Path(output_path).write_bytes(b"mp4")
        return {"bytes": 3, "duration_seconds": 0.4, "width": 1920, "height": 1080,
                "video_codec": "h264", "audio_codec": None, "encoder": "libx264",
                "hardware_accelerated": False}

    monkeypatch.setattr(routes_export.ffmpeg_master, "render_master_mp4", fake_render)
    app = create_app()
    client = TestClient(app)
    assert "/api/episode-pipelines/{pipeline_id}/export" in app.openapi()["paths"]


    denied = client.post("/api/episode-pipelines/pipeline-001/export")
    assert denied.status_code == 401

    response = client.post(
        "/api/episode-pipelines/pipeline-001/export",
        headers=AUTH,
        json={"use_hardware_acceleration": False},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["pipeline_id"] == "pipeline-001"
    assert body["output"].startswith("media_output/master/")
    assert body["output"].endswith(".mp4")
    assert "C:\\" not in json.dumps(body)
    assert Path(routes_export.storage.data_root(), body["output"]).is_file()
