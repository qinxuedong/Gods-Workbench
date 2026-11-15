# -*- coding: utf-8 -*-
"""QIN-607：FFmpeg/FFprobe 统一路径解析单元三态。"""

from __future__ import annotations

import os
import shutil

from gw.core.ffmpeg_resolver import FFMPEG_DIR_ENV, resolve_ffmpeg_tool

EXE = ".exe" if os.name == "nt" else ""


def _stub_dir(tmp_path, *, with_ffmpeg=True, with_ffprobe=True, name="ffbin"):
    """构造 GW_FFMPEG 桩目录，可控制 ffmpeg/ffprobe 是否齐全。"""
    d = tmp_path / name
    d.mkdir()
    if with_ffmpeg:
        (d / ("ffmpeg" + EXE)).write_bytes(b"stub")
    if with_ffprobe:
        (d / ("ffprobe" + EXE)).write_bytes(b"stub")
    return d


def _which_sentinel(monkeypatch, path):
    """把 shutil.which 换成哨兵：若测试目标回退 PATH，结果会等于哨兵。"""
    monkeypatch.setattr(shutil, "which", lambda name: path)


def test_env_dir_wins_over_path(tmp_path, monkeypatch):
    """GW_FFMPEG 有效时返回目录内可执行，即使 PATH 里也有同名工具。"""
    d = _stub_dir(tmp_path)
    monkeypatch.setenv(FFMPEG_DIR_ENV, str(d))
    _which_sentinel(monkeypatch, "C:/from-path/tool")
    r = resolve_ffmpeg_tool("ffmpeg")
    assert r.path == str(d / ("ffmpeg" + EXE))
    assert r.source == "gw_ffmpeg_env"


def test_invalid_env_dir_does_not_fall_back(tmp_path, monkeypatch):
    """GW_FFMPEG 指向不存在目录：失败关闭，不回退 PATH。"""
    monkeypatch.setenv(FFMPEG_DIR_ENV, str(tmp_path / "no-such-dir"))
    _which_sentinel(monkeypatch, "C:/from-path/tool")
    r = resolve_ffmpeg_tool("ffmpeg")
    assert r.path is None and r.source == "not_found"


def test_env_dir_missing_tool_does_not_fall_back(tmp_path, monkeypatch):
    """GW_FFMPEG 目录缺 ffprobe（或 ffmpeg）：整个 GW_FFMPEG 无效，不回退 PATH。"""
    d = _stub_dir(tmp_path, with_ffprobe=False)
    monkeypatch.setenv(FFMPEG_DIR_ENV, str(d))
    _which_sentinel(monkeypatch, "C:/from-path/tool")
    r = resolve_ffmpeg_tool("ffmpeg")
    assert r.path is None and r.source == "not_found"
    # 反向：缺 ffmpeg 只留 ffprobe 同样失败关闭。
    d2 = _stub_dir(tmp_path, with_ffmpeg=False, name="ffbin2")
    monkeypatch.setenv(FFMPEG_DIR_ENV, str(d2))
    r = resolve_ffmpeg_tool("ffprobe")
    assert r.path is None and r.source == "not_found"


def test_unset_env_falls_back_to_path(tmp_path, monkeypatch):
    """未设置（或空白）GW_FFMPEG：回退 shutil.which（PATH），与 master 现状一致。"""
    monkeypatch.delenv(FFMPEG_DIR_ENV, raising=False)
    _which_sentinel(monkeypatch, "C:/path-tool/ffmpeg")
    assert resolve_ffmpeg_tool("ffmpeg").source == "path"
    monkeypatch.setenv(FFMPEG_DIR_ENV, "   ")
    r = resolve_ffmpeg_tool("ffmpeg")
    assert r.path == "C:/path-tool/ffmpeg" and r.source == "path"


def test_all_missing_not_found(tmp_path, monkeypatch):
    """未设置 GW_FFMPEG 且 PATH 也找不到：not_found。"""
    monkeypatch.delenv(FFMPEG_DIR_ENV, raising=False)
    _which_sentinel(monkeypatch, None)
    r = resolve_ffmpeg_tool("ffmpeg")
    assert r.path is None and r.source == "not_found"
