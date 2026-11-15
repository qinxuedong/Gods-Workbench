# -*- coding: utf-8 -*-
"""FFmpeg/FFprobe 共享路径解析（QIN-607）。

统一全仓 12 处 ffmpeg/ffprobe 定位逻辑（QIN-607 设计表 §1 全表）：

1. ``GW_FFMPEG`` 环境变量（指向**同时包含 ffmpeg 与 ffprobe 的目录**）优先；
2. 未设置时回退 ``shutil.which``（PATH）；
3. 显式配置无效 → 失败关闭（``not_found``），**绝不静默回退 PATH**
   （避免「配了但没用上」的隐蔽漂移）。

落位于 ``gw/core``（而非设计表草案的 ``gw/media``）：``gw/media`` 与
``gw/asset_registry`` 已存在 media→asset_registry 方向的依赖，若 asset_registry
再反向依赖 ``gw/media`` 即构成新增域级循环（structure guardrails 硬门禁）；
``gw/core`` 是最底层域，所有领域包均已依赖 core，零循环风险。模块本身零
gw 内部依赖，纯函数只读环境变量 + ``shutil.which``，后台索引任务
（``gw/asset_registry/index_jobs`` 经 importlib 导入 keyframe_extractor /
scene_detector）与请求路径均可直接调用。

两处软探测点（``gw/media/proxy.py`` ``_probe_video_metadata``、
``gw/asset_registry/media_probe.py`` ``_probe_jxl_ffprobe``）以
``path is None`` 判断走既有保守回退分支；``GW_FFMPEG`` 无效时同样
不回退 PATH（与硬失败点一致），最终执行点失败关闭。
"""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from typing import Literal

#: GW_FFMPEG 指向包含 ffmpeg 与 ffprobe 的目录（非可执行文件路径）。
FFMPEG_DIR_ENV = "GW_FFMPEG"

#: 解析来源三态：环境变量目录 / PATH 回退 / 未找到。
ResolutionSource = Literal["gw_ffmpeg_env", "path", "not_found"]


@dataclass(frozen=True)
class FfmpegResolution:
    """单个工具的解析结果；``path is None`` 即未找到（含 GW_FFMPEG 无效）。"""

    path: str | None
    source: ResolutionSource


def _env_dir_executable(directory: str, name: str) -> str | None:
    """在 GW_FFMPEG 目录内定位可执行文件；Windows 额外接受 ``.exe`` 后缀。"""
    suffix = ".exe" if os.name == "nt" else ""
    candidate = os.path.join(directory, name + suffix)
    if os.path.isfile(candidate):
        return candidate
    return None


def resolve_ffmpeg_tool(name: str) -> FfmpegResolution:
    """解析单个 ffmpeg/ffprobe 工具路径；缺失或 GW_FFMPEG 无效返回 not_found。

    GW_FFMPEG 指向的目录必须同时含 ffmpeg 与 ffprobe，缺任一即整个
    GW_FFMPEG 视为无效，不回退 PATH（设计表 §2 确认口径）。
    """
    configured = os.environ.get(FFMPEG_DIR_ENV, "").strip()
    if configured:
        ffmpeg = _env_dir_executable(configured, "ffmpeg")
        ffprobe = _env_dir_executable(configured, "ffprobe")
        if ffmpeg and ffprobe:
            target = _env_dir_executable(configured, name)
            if target:
                return FfmpegResolution(path=target, source="gw_ffmpeg_env")
        # 显式配置无效：失败关闭，不回退 PATH。
        return FfmpegResolution(path=None, source="not_found")
    which = shutil.which(name)
    if which:
        return FfmpegResolution(path=which, source="path")
    return FfmpegResolution(path=None, source="not_found")


def probe_tool_version(path: str, timeout_seconds: float = 5.0) -> str | None:
    """运行 ``<tool> -version`` 取首行版本字符串；失败返回 None（不回显路径）。"""
    try:
        completed = subprocess.run(
            [path, "-version"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_seconds,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if completed.returncode != 0:
        return None
    lines = (completed.stdout or "").splitlines()
    return lines[0].strip() if lines else None
