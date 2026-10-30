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

"""QIN-485: 媒体处理与探针在 Windows/多平台下 subprocess UTF-8 编码安全契约测试。"""

from __future__ import annotations

import sys
from pathlib import Path
import pytest

from gw.media.scene_detector import _run as scene_run
from gw.media.keyframe_extractor import _run as keyframe_run
from gw.media.proxy import _probe_video_metadata
from gw.observability.telemetry import gpu_telemetry


def test_scene_detector_run_decodes_utf8_with_chinese_and_surrogates():
    """验证 scene_detector._run 显式使用 utf-8 且 errors='replace'，遇到 UTF-8 中文不抛 GBK UnicodeDecodeError。"""
    script = (
        "import sys\n"
        "sys.stdout.buffer.write('测试中文路径与标签：\\n'.encode('utf-8'))\n"
        "sys.stderr.buffer.write('错误警告：含有 \\x80\\x81 特殊字节\\n'.encode('utf-8'))\n"
    )
    res = scene_run([sys.executable, "-c", script])
    assert "测试中文路径与标签" in res.stdout
    assert "错误警告" in res.stderr


def test_keyframe_extractor_run_decodes_utf8_with_chinese():
    """验证 keyframe_extractor._run 显式使用 utf-8 且 errors='replace'。"""
    script = (
        "import sys\n"
        "sys.stdout.buffer.write('关键帧提取技术元数据：h264 4K\\n'.encode('utf-8'))\n"
        "sys.stderr.buffer.write('ffmpeg 输出：帧率 60fps\\n'.encode('utf-8'))\n"
    )
    res = keyframe_run([sys.executable, "-c", script])
    assert "关键帧提取技术元数据" in res.stdout
    assert "ffmpeg 输出" in res.stderr


def test_probe_video_metadata_ffprobe_encoding_args(tmp_path, monkeypatch):
    """验证 proxy._probe_video_metadata 调用 ffprobe 时显式携带 utf-8 与 errors='replace'。"""
    chinese_video = tmp_path / "测试素材视频.mp4"
    chinese_video.write_bytes(b"dummy")
    recorded_kwargs = {}

    import subprocess

    def fake_run(args, **kwargs):
        recorded_kwargs.update(kwargs)
        return subprocess.CompletedProcess(
            args,
            0,
            stdout='{"streams":[{"codec_name":"h264","width":1920,"height":1080}],"format":{"bit_rate":"5000000"}}',
            stderr="",
        )

    monkeypatch.setattr("gw.media.proxy.shutil.which", lambda name: "ffprobe")
    monkeypatch.setattr("gw.media.proxy.subprocess.run", fake_run)

    meta = _probe_video_metadata(chinese_video)
    assert meta.get("width") == 1920
    assert meta.get("height") == 1080
    assert recorded_kwargs.get("encoding") == "utf-8"
    assert recorded_kwargs.get("errors") == "replace"
    assert recorded_kwargs.get("text") is True


def test_gpu_telemetry_nvidia_smi_encoding_args(monkeypatch):
    """验证 gpu_telemetry 调用 nvidia-smi 时显式携带 utf-8 与 errors='replace'。"""
    recorded_kwargs = {}
    import subprocess

    def fake_run(args, **kwargs):
        recorded_kwargs.update(kwargs)
        return subprocess.CompletedProcess(
            args,
            0,
            stdout="0, NVIDIA GeForce RTX 5090 中文版, 32768, 4096, 25\n",
            stderr="",
        )

    monkeypatch.setattr("shutil.which", lambda name: "nvidia-smi")
    monkeypatch.setattr("subprocess.run", fake_run)

    info = gpu_telemetry()
    assert info is not None
    assert info["gpu_count"] == 1
    assert "中文版" in info["devices"][0]["name"]
    assert recorded_kwargs.get("encoding") == "utf-8"
    assert recorded_kwargs.get("errors") == "replace"
    assert recorded_kwargs.get("text") is True
