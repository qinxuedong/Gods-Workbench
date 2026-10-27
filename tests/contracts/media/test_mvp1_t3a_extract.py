"""MVP1-T3a 镜头级纯提取契约测试：切点、内存关键帧、元数据与失败关闭。"""

from __future__ import annotations

import ast
import io
import json
import shutil
import subprocess
from pathlib import Path

import pytest
from PIL import Image

from gw.core.errors import CleanroomException
from gw.media.asset_extract import extract


def _require_ffmpeg_tools() -> None:
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        pytest.skip("ffmpeg/ffprobe 未安装")


def _generate_hard_cut_video(tmp_path: Path, *, with_audio: bool) -> Path:
    """用 lavfi 生成 2 秒 160x90 黑白硬切合成片段（10fps），可选 440Hz 音轨。"""
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        pytest.skip("ffmpeg/ffprobe 未安装")
    video = tmp_path / "hard-cut.mp4"
    args = [
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
    ]
    if with_audio:
        args += ["-f", "lavfi", "-i", "sine=frequency=440:duration=2"]
    args += [
        "-filter_complex",
        "[0:v][1:v]concat=n=2:v=1:a=0,format=yuv420p[v]",
        "-map",
        "[v]",
    ]
    if with_audio:
        args += ["-map", "2:a", "-c:a", "aac"]
    args += [
        "-c:v",
        "libx264",
        "-preset",
        "ultrafast",
        "-pix_fmt",
        "yuv420p",
        str(video),
    ]
    generated = subprocess.run(
        args,
        capture_output=True,
        timeout=60,
        check=False,
        shell=False,
    )
    if generated.returncode != 0 or not video.is_file():
        pytest.skip("当前 ffmpeg 不支持 lavfi/libx264 测试夹具")
    return video


def test_extract_returns_monotonic_cut_points_and_memory_keyframes(tmp_path):
    """AC1：切点单调递增且首个为 0，每镜头 1 张内存关键帧。"""
    _require_ffmpeg_tools()
    video = _generate_hard_cut_video(tmp_path, with_audio=False)

    result = extract(video)

    assert len(result.cut_points_sec) == len(result.shots)
    assert result.cut_points_sec[0] == 0.0
    assert all(
        later > previous
        for previous, later in zip(
            result.cut_points_sec, result.cut_points_sec[1:], strict=False
        )
    )
    assert abs(result.cut_points_sec[-1] - 1.0) <= 0.1
    assert len(result.shots) == 2
    for shot in result.shots:
        assert isinstance(shot.keyframe, bytes) and shot.keyframe
        assert shot.start_sec < shot.end_sec
        with Image.open(io.BytesIO(shot.keyframe)) as frame_image:
            frame_image.load()
            assert frame_image.size == (160, 90)
            assert frame_image.format == "PNG"


def test_extract_metadata_matches_ffprobe_original_values(tmp_path):
    """AC2：技术元数据与 ffprobe 原值一致（合成片段校验）。"""
    _require_ffmpeg_tools()
    video = _generate_hard_cut_video(tmp_path, with_audio=False)

    ffprobe = shutil.which("ffprobe")
    probe = subprocess.run(
        [
            ffprobe,
            "-v",
            "error",
            "-show_entries",
            "stream=codec_name,codec_type,width,height,r_frame_rate",
            "-show_entries",
            "format=duration",
            "-of",
            "json",
            str(video),
        ],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
        shell=False,
    )
    assert probe.returncode == 0
    original = json.loads(probe.stdout)
    stream = next(
        item for item in original["streams"] if item.get("codec_type") == "video"
    )

    result = extract(video)
    metadata = result.metadata

    assert {"codec", "fps", "width", "height", "duration", "has_audio"} <= set(
        metadata
    )
    assert metadata["codec"] == stream["codec_name"]
    assert metadata["width"] == stream["width"]
    assert metadata["height"] == stream["height"]
    numerator, _, denominator = stream["r_frame_rate"].partition("/")
    expected_fps = float(numerator) / float(denominator or 1)
    assert metadata["fps"] == pytest.approx(expected_fps)
    assert metadata["duration"] == pytest.approx(
        float(original["format"]["duration"]), abs=1e-6
    )
    assert metadata["has_audio"] is False


def test_extract_detects_audio_track_presence(tmp_path):
    """音轨有无取 ffprobe 实况：带 sine 音轨的合成片段必须报 has_audio=True。"""
    _require_ffmpeg_tools()
    video = _generate_hard_cut_video(tmp_path, with_audio=True)

    result = extract(video)

    assert result.metadata["has_audio"] is True


def test_extract_writes_no_files_during_run(tmp_path):
    """AC3：运行期间临时目录与数据根下无新增文件。"""
    _require_ffmpeg_tools()
    video = _generate_hard_cut_video(tmp_path, with_audio=False)
    snapshot_before = {path.relative_to(tmp_path) for path in tmp_path.rglob("*")}

    extract(video)

    snapshot_after = {path.relative_to(tmp_path) for path in tmp_path.rglob("*")}
    assert snapshot_after == snapshot_before


def test_extract_fails_closed_when_ffmpeg_missing(tmp_path, monkeypatch):
    """AC4：ffmpeg/ffprobe 缺失时抛出明确的领域异常，不崩溃。"""
    video = tmp_path / "any.mp4"
    video.write_bytes(b"placeholder")

    monkeypatch.setattr(shutil, "which", lambda name: None)
    with pytest.raises(CleanroomException) as excinfo:
        extract(video)

    assert excinfo.value.code == "FFMPEG_NOT_AVAILABLE"
    assert excinfo.value.status_code == 503


def test_extract_fails_closed_on_corrupt_file(tmp_path):
    """AC4：文件损坏时抛出明确的领域异常，不崩溃。"""
    _require_ffmpeg_tools()
    video = tmp_path / "corrupt.mp4"
    video.write_bytes(b"\x00" * 64)

    with pytest.raises(CleanroomException) as excinfo:
        extract(video)

    assert excinfo.value.code == "MEDIA_PROBE_FAILED"
    assert excinfo.value.status_code == 503


def test_asset_extract_module_imports_no_db_or_registry_write_interfaces():
    """AC5：模块不 import 任何 DB / gw.asset_registry 写入接口。

    按仓库根确定性路径读源码做静态 AST 检查，不依赖运行期 sys.modules
    或工作目录，避免被同进程其他测试的环境改动干扰。
    """
    module_source = Path(__file__).resolve().parents[3] / "gw" / "media" / "asset_extract.py"
    imported: set[str] = set()
    for node in ast.walk(ast.parse(module_source.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)

    banned_roots = ("gw.asset_registry", "gw.core.sqlite_vec_engine", "sqlite3")
    violations = [
        name
        for name in imported
        if any(name == root or name.startswith(f"{root}.") for root in banned_roots)
    ]
    assert violations == []
