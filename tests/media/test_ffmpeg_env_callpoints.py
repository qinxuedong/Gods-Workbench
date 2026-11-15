# -*- coding: utf-8 -*-
"""QIN-607：12 处调用点（9 文件）GW_FFMPEG 有效/无效/缺失三类用例。

「有效」用例以子进程命令首元素断言「用的是 GW_FFMPEG 目录里的桩，不是 PATH 副本」；
「无效」用例以 shutil.which 哨兵证明失败关闭不回退 PATH；「缺失」用例证明各入口
既有错误码保持不变（AC5）。PM 附加项：扩围 4 文件与票面 5 点同标准全覆盖。
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from gw.core.errors import CleanroomException
from gw.core.ffmpeg_resolver import FFMPEG_DIR_ENV

EXE = ".exe" if os.name == "nt" else ""
SENTINEL = "C:/from-path/ffmpeg"


def _stub_dir(tmp_path, name="ffbin"):
    d = tmp_path / name
    d.mkdir()
    (d / ("ffmpeg" + EXE)).write_bytes(b"stub")
    (d / ("ffprobe" + EXE)).write_bytes(b"stub")
    return d


def _sentinel_which(monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda name: SENTINEL)


def _no_which(monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda name: None)


def _capture_run(monkeypatch, calls, returncode=0):
    """把 subprocess.run 换成捕获器：记录命令首元素，返回可定制结果。"""

    def fake_run(args, **_kwargs):
        calls.append(list(args))
        return SimpleNamespace(returncode=returncode)

    monkeypatch.setattr(subprocess, "run", fake_run)


# ---------------------------------------------------------------------------
# gw/media/proxy.py：硬点 _ffmpeg_proxy + 软点 _probe_video_metadata
# ---------------------------------------------------------------------------


def test_proxy_uses_gw_ffmpeg_env(tmp_path, monkeypatch):
    """AC1：GW_FFMPEG 有效时，代理转码子进程用的是目录内 ffmpeg 桩。"""
    d = _stub_dir(tmp_path)
    monkeypatch.setenv(FFMPEG_DIR_ENV, str(d))
    _sentinel_which(monkeypatch)
    calls = []
    _capture_run(monkeypatch, calls)
    from gw.media import proxy

    source = tmp_path / "src.mp4"
    source.write_bytes(b"x")
    target = tmp_path / "proxy-out.mp4"
    partial = Path(str(target) + ".part")
    partial.write_bytes(b"v")
    proxy._ffmpeg_proxy(source, 1280, target)
    assert calls[0][0] == str(d / ("ffmpeg" + EXE))


def test_proxy_invalid_env_fails_closed(tmp_path, monkeypatch):
    """AC3：GW_FFMPEG 无效时代理明确失败，不回退 PATH。"""
    monkeypatch.setenv(FFMPEG_DIR_ENV, str(tmp_path / "no-such-dir"))
    _sentinel_which(monkeypatch)
    from gw.media import proxy

    source = tmp_path / "src.mp4"
    source.write_bytes(b"x")
    with pytest.raises(CleanroomException) as ei:
        proxy._ffmpeg_proxy(source, 1280, tmp_path / "proxy-out.mp4")
    assert ei.value.code == "FFMPEG_NOT_AVAILABLE"
    assert ei.value.status_code == 503


def test_proxy_missing_fails_closed(tmp_path, monkeypatch):
    """AC5：ffmpeg 缺失时代理既有 503 错误码保持不变。"""
    monkeypatch.delenv(FFMPEG_DIR_ENV, raising=False)
    _no_which(monkeypatch)
    from gw.media import proxy

    source = tmp_path / "src.mp4"
    source.write_bytes(b"x")
    with pytest.raises(CleanroomException) as ei:
        proxy._ffmpeg_proxy(source, 1280, tmp_path / "proxy-out.mp4")
    assert ei.value.code == "FFMPEG_NOT_AVAILABLE"


def test_proxy_soft_probe_keeps_fallback(tmp_path, monkeypatch):
    """软探测点：GW_FFMPEG 无效时保持既有保守回退，不回退 PATH。"""
    monkeypatch.setenv(FFMPEG_DIR_ENV, str(tmp_path / "no-such-dir"))
    _sentinel_which(monkeypatch)
    from gw.media import proxy

    source = tmp_path / "src.mp4"
    source.write_bytes(b"x")
    assert proxy._probe_video_metadata(source) == {}


# ---------------------------------------------------------------------------
# gw/media/lut_render.py
# ---------------------------------------------------------------------------


def test_lut_render_uses_gw_ffmpeg_env(tmp_path, monkeypatch):
    """AC1：LUT 视频转码子进程用的是 GW_FFMPEG 目录内 ffmpeg 桩。"""
    d = _stub_dir(tmp_path)
    monkeypatch.setenv(FFMPEG_DIR_ENV, str(d))
    _sentinel_which(monkeypatch)
    calls = []
    _capture_run(monkeypatch, calls)
    from gw.media.lut_render import render_video_to_mp4

    source = tmp_path / "src.mp4"
    source.write_bytes(b"x")
    cube = tmp_path / "look.cube"
    cube.write_text("TITLE t\nLUT_3D_SIZE 2\n", encoding="utf-8")
    output = tmp_path / "out.mp4"
    output.with_name(output.stem + ".part" + output.suffix).write_bytes(b"v")
    render_video_to_mp4(source, cube, 0.5, output)
    assert calls[0][0] == str(d / ("ffmpeg" + EXE))


def test_lut_render_invalid_env_fails_closed(tmp_path, monkeypatch):
    """AC3：GW_FFMPEG 无效时 LUT 转码明确失败，不回退 PATH。"""
    monkeypatch.setenv(FFMPEG_DIR_ENV, str(tmp_path / "no-such-dir"))
    _sentinel_which(monkeypatch)
    from gw.media.lut_render import LutRenderError, render_video_to_mp4

    with pytest.raises(LutRenderError) as ei:
        render_video_to_mp4(tmp_path / "s.mp4", tmp_path / "l.cube", 0.5, tmp_path / "o.mp4")
    assert ei.value.code == "FFMPEG_NOT_AVAILABLE"


def test_lut_render_missing_fails_closed(tmp_path, monkeypatch):
    """AC5：ffmpeg 缺失时 LUT 转码既有错误码保持不变。"""
    monkeypatch.delenv(FFMPEG_DIR_ENV, raising=False)
    _no_which(monkeypatch)
    from gw.media.lut_render import LutRenderError, render_video_to_mp4

    with pytest.raises(LutRenderError) as ei:
        render_video_to_mp4(tmp_path / "s.mp4", tmp_path / "l.cube", 0.5, tmp_path / "o.mp4")
    assert ei.value.code == "FFMPEG_NOT_AVAILABLE"


# ---------------------------------------------------------------------------
# gw/media/ffmpeg_master.py
# ---------------------------------------------------------------------------


def _minimal_manifest():
    """单片段最小时间线：足以走到 _hardware_encoder 的 -encoders 探测。"""
    return {
        "version": 1,
        "tracks": [
            {
                "track_id": "video-01",
                "kind": "video",
                "clips": [
                    {
                        "asset_id": "a1",
                        "media_type": "video",
                        "at": 0.0,
                        "duration": 1.0,
                    }
                ],
            }
        ],
    }


def test_master_uses_gw_ffmpeg_env(tmp_path, monkeypatch):
    """AC1：母带导出首个子进程（-encoders 探测）用的是 GW_FFMPEG 目录内 ffmpeg 桩。"""
    d = _stub_dir(tmp_path)
    monkeypatch.setenv(FFMPEG_DIR_ENV, str(d))
    _sentinel_which(monkeypatch)
    calls = []

    def fake_run(args, **_kwargs):
        calls.append(list(args))
        return SimpleNamespace(returncode=0, stdout=b"", stderr=b"")

    monkeypatch.setattr(subprocess, "run", fake_run)
    from gw.media.ffmpeg_master import render_master_mp4

    output = tmp_path / "master.mp4"
    with pytest.raises(CleanroomException):
        # 不预写 partial：转码后必然 MASTER_EXPORT_FAILED，探测调用已被记录。
        render_master_mp4(_minimal_manifest(), {"a1": tmp_path / "a1.mp4"}, output)
    assert calls[0][0] == str(d / ("ffmpeg" + EXE))
    assert all(call[0] != SENTINEL for call in calls)


def test_master_invalid_env_fails_closed(tmp_path, monkeypatch):
    """AC3：GW_FFMPEG 无效时母带导出 503 FFMPEG_UNAVAILABLE，不回退 PATH。"""
    monkeypatch.setenv(FFMPEG_DIR_ENV, str(tmp_path / "no-such-dir"))
    _sentinel_which(monkeypatch)
    from gw.media.ffmpeg_master import render_master_mp4

    with pytest.raises(CleanroomException) as ei:
        render_master_mp4(_minimal_manifest(), {"a1": tmp_path / "a1.mp4"}, tmp_path / "m.mp4")
    assert ei.value.code == "FFMPEG_UNAVAILABLE"


def test_master_missing_fails_closed(tmp_path, monkeypatch):
    """AC5：ffmpeg/ffprobe 缺失时母带导出既有错误码保持不变。"""
    monkeypatch.delenv(FFMPEG_DIR_ENV, raising=False)
    _no_which(monkeypatch)
    from gw.media.ffmpeg_master import render_master_mp4

    with pytest.raises(CleanroomException) as ei:
        render_master_mp4(_minimal_manifest(), {"a1": tmp_path / "a1.mp4"}, tmp_path / "m.mp4")
    assert ei.value.code == "FFMPEG_UNAVAILABLE"


# ---------------------------------------------------------------------------
# gw/video_tasks/service.py
# ---------------------------------------------------------------------------


def _video_probe_stdout():
    return json.dumps(
        {
            "streams": [
                {
                    "codec_type": "video",
                    "width": 640,
                    "height": 360,
                    "codec_name": "h264",
                    "duration": "2.0",
                }
            ],
            "format": {},
        }
    ).encode("utf-8")


def test_video_tasks_probe_uses_gw_ffprobe_env(tmp_path, monkeypatch):
    """AC1：视频任务校验子进程用的是 GW_FFMPEG 目录内 ffprobe 桩。"""
    d = _stub_dir(tmp_path)
    monkeypatch.setenv(FFMPEG_DIR_ENV, str(d))
    _sentinel_which(monkeypatch)
    calls = []

    def fake_run(args, **_kwargs):
        calls.append(list(args))
        return SimpleNamespace(returncode=0, stdout=_video_probe_stdout())

    monkeypatch.setattr(subprocess, "run", fake_run)
    from gw.video_tasks.service import VideoTaskService

    media = VideoTaskService._probe(tmp_path / "x.mp4")
    assert calls[0][0] == str(d / ("ffprobe" + EXE))
    assert media["width"] == 640 and media["duration_seconds"] == 2.0


def test_video_tasks_probe_invalid_env_fails_closed(tmp_path, monkeypatch):
    """AC3：GW_FFMPEG 无效时视频任务校验明确失败，不回退 PATH。"""
    monkeypatch.setenv(FFMPEG_DIR_ENV, str(tmp_path / "no-such-dir"))
    _sentinel_which(monkeypatch)
    from gw.video_tasks.service import VideoTaskService

    with pytest.raises(CleanroomException) as ei:
        VideoTaskService._probe(tmp_path / "x.mp4")
    assert ei.value.code == "VIDEO_FFPROBE_UNAVAILABLE"


def test_video_tasks_probe_missing_fails_closed(tmp_path, monkeypatch):
    """AC5：ffprobe 缺失时视频任务校验既有错误码保持不变。"""
    monkeypatch.delenv(FFMPEG_DIR_ENV, raising=False)
    _no_which(monkeypatch)
    from gw.video_tasks.service import VideoTaskService

    with pytest.raises(CleanroomException) as ei:
        VideoTaskService._probe(tmp_path / "x.mp4")
    assert ei.value.code == "VIDEO_FFPROBE_UNAVAILABLE"


def test_video_tasks_export_uses_gw_ffmpeg_env(tmp_path, monkeypatch):
    """AC1：本地渲染子进程用的是 GW_FFMPEG 目录内 ffmpeg 桩。"""
    d = _stub_dir(tmp_path)
    monkeypatch.setenv(FFMPEG_DIR_ENV, str(d))
    _sentinel_which(monkeypatch)
    from gw.video_tasks.service import VideoTaskService
    from gw.video_tasks.store import VideoTaskStore

    service = VideoTaskService(VideoTaskStore(tmp_path / "video"))
    monkeypatch.setattr(service, "_require_project_binding", lambda *_a: None)
    monkeypatch.setattr(service, "_authorized_asset_path", lambda *_a: tmp_path / "a.mp4")
    calls = []
    fake_proc = SimpleNamespace(
        poll=lambda: 0, terminate=lambda: None, wait=lambda *_a, **_k: 0, kill=lambda: None
    )

    def fake_popen(cmd, **_kwargs):
        calls.append(list(cmd))
        return fake_proc

    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    row = {"job_id": "tjob_0001", "project_id": "p", "actor_key": "local:alice"}
    request = {"preset": "h264_720p_30fps", "asset_ids": [], "project_id": "p"}
    service._run_export(row, request)
    assert calls[0][0] == str(d / ("ffmpeg" + EXE))


def test_video_tasks_export_invalid_env_fails_closed(tmp_path, monkeypatch):
    """AC3：GW_FFMPEG 无效时本地渲染在拉起进程前明确失败，不回退 PATH。"""
    monkeypatch.setenv(FFMPEG_DIR_ENV, str(tmp_path / "no-such-dir"))
    _sentinel_which(monkeypatch)
    from gw.video_tasks.service import VideoTaskService
    from gw.video_tasks.store import VideoTaskStore

    service = VideoTaskService(VideoTaskStore(tmp_path / "video2"))
    monkeypatch.setattr(service, "_require_project_binding", lambda *_a: None)
    monkeypatch.setattr(service, "_authorized_asset_path", lambda *_a: tmp_path / "a.mp4")
    calls = []

    def fail_popen(cmd, **_kwargs):
        calls.append(list(cmd))
        raise AssertionError("GW_FFMPEG 无效时不得拉起渲染进程")

    monkeypatch.setattr(subprocess, "Popen", fail_popen)
    row = {"job_id": "tjob_0002", "project_id": "p", "actor_key": "local:alice"}
    request = {"preset": "h264_720p_30fps", "asset_ids": [], "project_id": "p"}
    with pytest.raises(CleanroomException) as ei:
        service._run_export(row, request)
    assert ei.value.code == "VIDEO_FFMPEG_UNAVAILABLE"
    assert calls == []


def test_video_tasks_export_missing_fails_closed(tmp_path, monkeypatch):
    """AC5：ffmpeg/ffprobe 缺失时本地渲染既有错误码保持不变。"""
    monkeypatch.delenv(FFMPEG_DIR_ENV, raising=False)
    _no_which(monkeypatch)
    from gw.video_tasks.service import VideoTaskService
    from gw.video_tasks.store import VideoTaskStore

    service = VideoTaskService(VideoTaskStore(tmp_path / "video3"))
    monkeypatch.setattr(service, "_require_project_binding", lambda *_a: None)
    monkeypatch.setattr(service, "_authorized_asset_path", lambda *_a: tmp_path / "a.mp4")
    row = {"job_id": "tjob_0003", "project_id": "p", "actor_key": "local:alice"}
    request = {"preset": "h264_720p_30fps", "asset_ids": [], "project_id": "p"}
    with pytest.raises(CleanroomException) as ei:
        service._run_export(row, request)
    assert ei.value.code == "VIDEO_FFMPEG_UNAVAILABLE"


# ---------------------------------------------------------------------------
# gw/asset_registry/media_probe.py：硬点 _ffmpeg() + 软点 _probe_jxl_ffprobe
# ---------------------------------------------------------------------------


def test_media_probe_uses_gw_ffmpeg_env(tmp_path, monkeypatch):
    """AC1：媒体探测解析出 GW_FFMPEG 目录内工具。"""
    d = _stub_dir(tmp_path)
    monkeypatch.setenv(FFMPEG_DIR_ENV, str(d))
    _sentinel_which(monkeypatch)
    from gw.asset_registry import media_probe

    assert media_probe._ffmpeg("ffmpeg") == str(d / ("ffmpeg" + EXE))
    assert media_probe._ffmpeg("ffprobe") == str(d / ("ffprobe" + EXE))


def test_media_probe_invalid_env_fails_closed(tmp_path, monkeypatch):
    """AC3：GW_FFMPEG 无效时媒体探测明确失败，不回退 PATH。"""
    monkeypatch.setenv(FFMPEG_DIR_ENV, str(tmp_path / "no-such-dir"))
    _sentinel_which(monkeypatch)
    from gw.asset_registry import media_probe

    with pytest.raises(CleanroomException) as ei:
        media_probe._ffmpeg("ffmpeg")
    assert ei.value.code == "FFMPEG_NOT_AVAILABLE"


def test_media_probe_missing_fails_closed(tmp_path, monkeypatch):
    """AC5：工具缺失时媒体探测既有 503 错误码保持不变。"""
    monkeypatch.delenv(FFMPEG_DIR_ENV, raising=False)
    _no_which(monkeypatch)
    from gw.asset_registry import media_probe

    with pytest.raises(CleanroomException) as ei:
        media_probe._ffmpeg("ffprobe")
    assert ei.value.code == "FFMPEG_NOT_AVAILABLE"


def test_media_probe_soft_jxl_keeps_fallback(tmp_path, monkeypatch):
    """软探测点：GW_FFMPEG 无效时保持轻量头解析回退，不回退 PATH。"""
    monkeypatch.setenv(FFMPEG_DIR_ENV, str(tmp_path / "no-such-dir"))
    _sentinel_which(monkeypatch)
    from gw.asset_registry import media_probe

    source = tmp_path / "a.jxl"
    source.write_bytes(b"x")
    assert media_probe._probe_jxl_ffprobe(source) is None


# ---------------------------------------------------------------------------
# gw/media/keyframe_extractor.py / scene_detector.py / repository.py（纯解析函数）
# ---------------------------------------------------------------------------


def test_keyframe_extractor_uses_gw_ffmpeg_env(tmp_path, monkeypatch):
    """AC1：关键帧抽取解析出 GW_FFMPEG 目录内工具。"""
    d = _stub_dir(tmp_path)
    monkeypatch.setenv(FFMPEG_DIR_ENV, str(d))
    _sentinel_which(monkeypatch)
    from gw.media.keyframe_extractor import _tool

    assert _tool("ffmpeg") == str(d / ("ffmpeg" + EXE))


def test_keyframe_extractor_invalid_env_fails_closed(tmp_path, monkeypatch):
    """AC3：GW_FFMPEG 无效时关键帧抽取明确失败，不回退 PATH。"""
    monkeypatch.setenv(FFMPEG_DIR_ENV, str(tmp_path / "no-such-dir"))
    _sentinel_which(monkeypatch)
    from gw.media.keyframe_extractor import _tool

    with pytest.raises(CleanroomException) as ei:
        _tool("ffmpeg")
    assert ei.value.code == "FFMPEG_NOT_AVAILABLE"


def test_keyframe_extractor_missing_fails_closed(tmp_path, monkeypatch):
    """AC5：工具缺失时关键帧抽取既有 503 错误码保持不变。"""
    monkeypatch.delenv(FFMPEG_DIR_ENV, raising=False)
    _no_which(monkeypatch)
    from gw.media.keyframe_extractor import _tool

    with pytest.raises(CleanroomException) as ei:
        _tool("ffmpeg")
    assert ei.value.code == "FFMPEG_NOT_AVAILABLE"


def test_scene_detector_uses_gw_ffmpeg_env(tmp_path, monkeypatch):
    """AC1：场景切分解析出 GW_FFMPEG 目录内工具。"""
    d = _stub_dir(tmp_path)
    monkeypatch.setenv(FFMPEG_DIR_ENV, str(d))
    _sentinel_which(monkeypatch)
    from gw.media.scene_detector import _tool

    assert _tool("ffmpeg") == str(d / ("ffmpeg" + EXE))


def test_scene_detector_invalid_env_fails_closed(tmp_path, monkeypatch):
    """AC3：GW_FFMPEG 无效时场景切分明确失败，不回退 PATH。"""
    monkeypatch.setenv(FFMPEG_DIR_ENV, str(tmp_path / "no-such-dir"))
    _sentinel_which(monkeypatch)
    from gw.media.scene_detector import _tool

    with pytest.raises(CleanroomException) as ei:
        _tool("ffmpeg")
    assert ei.value.code == "FFMPEG_NOT_AVAILABLE"


def test_scene_detector_missing_fails_closed(tmp_path, monkeypatch):
    """AC5：工具缺失时场景切分既有 503 错误码保持不变。"""
    monkeypatch.delenv(FFMPEG_DIR_ENV, raising=False)
    _no_which(monkeypatch)
    from gw.media.scene_detector import _tool

    with pytest.raises(CleanroomException) as ei:
        _tool("ffmpeg")
    assert ei.value.code == "FFMPEG_NOT_AVAILABLE"


def test_media_repository_uses_gw_ffmpeg_env(tmp_path, monkeypatch):
    """AC1：媒体仓库解析出 GW_FFMPEG 目录内工具。"""
    d = _stub_dir(tmp_path)
    monkeypatch.setenv(FFMPEG_DIR_ENV, str(d))
    _sentinel_which(monkeypatch)
    from gw.media.repository import _tool

    assert _tool("ffmpeg") == str(d / ("ffmpeg" + EXE))


def test_media_repository_invalid_env_fails_closed(tmp_path, monkeypatch):
    """AC3：GW_FFMPEG 无效时媒体仓库明确失败，不回退 PATH。"""
    monkeypatch.setenv(FFMPEG_DIR_ENV, str(tmp_path / "no-such-dir"))
    _sentinel_which(monkeypatch)
    from gw.media.repository import _tool

    with pytest.raises(CleanroomException) as ei:
        _tool("ffmpeg")
    assert ei.value.code == "FFMPEG_NOT_AVAILABLE"


def test_media_repository_missing_fails_closed(tmp_path, monkeypatch):
    """AC5：工具缺失时媒体仓库既有 503 错误码保持不变。"""
    monkeypatch.delenv(FFMPEG_DIR_ENV, raising=False)
    _no_which(monkeypatch)
    from gw.media.repository import _tool

    with pytest.raises(CleanroomException) as ei:
        _tool("ffmpeg")
    assert ei.value.code == "FFMPEG_NOT_AVAILABLE"


# ---------------------------------------------------------------------------
# gw/media/asset_extract.py
# ---------------------------------------------------------------------------


def _asset_probe_stdout():
    return json.dumps(
        {
            "streams": [
                {
                    "codec_type": "video",
                    "codec_name": "h264",
                    "width": 640,
                    "height": 360,
                    "r_frame_rate": "24/1",
                }
            ],
            "format": {"duration": "2.0"},
        }
    )


def test_asset_extract_uses_gw_ffprobe_env(tmp_path, monkeypatch):
    """AC1：镜头提取元数据探测用的是 GW_FFMPEG 目录内 ffprobe 桩。"""
    d = _stub_dir(tmp_path)
    monkeypatch.setenv(FFMPEG_DIR_ENV, str(d))
    _sentinel_which(monkeypatch)
    calls = []

    def fake_run(args, **_kwargs):
        calls.append(list(args))
        return SimpleNamespace(returncode=0, stdout=_asset_probe_stdout())

    monkeypatch.setattr(subprocess, "run", fake_run)
    from gw.media.asset_extract import _probe_metadata

    metadata = _probe_metadata(tmp_path / "x.mp4")
    assert calls[0][0] == str(d / ("ffprobe" + EXE))
    assert metadata["width"] == 640 and metadata["duration"] == 2.0


def test_asset_extract_invalid_env_fails_closed(tmp_path, monkeypatch):
    """AC3：GW_FFMPEG 无效时镜头提取明确失败，不回退 PATH。"""
    monkeypatch.setenv(FFMPEG_DIR_ENV, str(tmp_path / "no-such-dir"))
    _sentinel_which(monkeypatch)
    from gw.media.asset_extract import _probe_metadata

    with pytest.raises(CleanroomException) as ei:
        _probe_metadata(tmp_path / "x.mp4")
    assert ei.value.code == "FFMPEG_NOT_AVAILABLE"


def test_asset_extract_missing_fails_closed(tmp_path, monkeypatch):
    """AC5：ffprobe 缺失时镜头提取既有 503 错误码保持不变。"""
    monkeypatch.delenv(FFMPEG_DIR_ENV, raising=False)
    _no_which(monkeypatch)
    from gw.media.asset_extract import _probe_metadata

    with pytest.raises(CleanroomException) as ei:
        _probe_metadata(tmp_path / "x.mp4")
    assert ei.value.code == "FFMPEG_NOT_AVAILABLE"