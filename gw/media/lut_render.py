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

"""QIN-581：把已登记 LUT 套到素材上并落盘（图片直出 / 视频转码）。

与 QIN-580 的浏览器预览共用同一套查表语义：Cube 采样按 ``array("f")``
（float32）存放，三线性插值的运算顺序与
``web/js/modules/asset-manager/lut-preview.js`` 逐行一致，强度按
``round((v + (lutv - v) * strength) * 255)`` 混合。因此「保存出来的新资产」
与「预览看到的画面」是同一像素结果，而不是两套近似实现。

失败关闭：Cube 损坏、素材不可解码、像素数超限、ffmpeg 缺失或执行失败，
一律抛 ``LutRenderError``（带稳定错误码），不返回半成品、不静默回退原图。
"""

from __future__ import annotations

import array
import math
from pathlib import Path
import shutil
import subprocess
import tempfile

from gw.asset_registry.lut_cube import LutCubeError, parse_lut_cube_bytes

#: 图片直出的像素数上限（约 8K 单帧）。超过即明确失败，绝不静默降采样。
MAX_IMAGE_PIXELS = 40_000_000
#: 视频转码总超时；长素材按需上调，但必须有界，避免子进程挂死。
VIDEO_TIMEOUT_SEC = 900
#: Cube 头指令；第二遍取采样值时逐行跳过。
_HEADER_DIRECTIVES = frozenset({
    "TITLE",
    "LUT_1D_SIZE",
    "LUT_3D_SIZE",
    "DOMAIN_MIN",
    "DOMAIN_MAX",
    "LUT_1D_INPUT_RANGE",
    "LUT_3D_INPUT_RANGE",
})


class LutRenderError(RuntimeError):
    """带稳定错误码的 LUT 渲染失败。"""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _iter_sample_floats(text: str):
    """逐行产出采样值；头部指令与注释按 Cube 规范跳过。

    合法性（行数、尺寸、非有限数）已由 ``parse_lut_cube_bytes`` 前置校验，
    这里只负责按 ``red`` 变化最快的顺序取值，不再重复报错口径。
    """
    for raw_line in text.splitlines():
        content = raw_line.split("#", 1)[0].strip()
        if not content:
            continue
        parts = content.split()
        if parts[0].upper() in _HEADER_DIRECTIVES:
            continue
        yield float(parts[0])
        yield float(parts[1])
        yield float(parts[2])


def load_cube_bytes(data: bytes) -> dict:
    """解析并校验 Cube LUT，返回与前端 ``parseCubeLut`` 同构的查表数据。"""
    try:
        meta = parse_lut_cube_bytes(data)
    except LutCubeError as exc:
        raise LutRenderError(exc.code, str(exc)) from exc
    try:
        text = data.decode("utf-8-sig")
        values = array.array("f", list(_iter_sample_floats(text)))
    except (UnicodeDecodeError, ValueError) as exc:
        raise LutRenderError("LUT_CUBE_INVALID", "LUT 文件编码或数值无效") from exc

    size_1d = int(meta.get("lut_1d_size") or 0)
    size_3d = int(meta.get("lut_3d_size") or 0)
    expected_values = (size_1d + size_3d**3) * 3
    if len(values) != expected_values:
        raise LutRenderError(
            "LUT_CUBE_INVALID",
            f"LUT 采样数量应为 {expected_values}，实际为 {len(values)}",
        )
    return {
        "title": str(meta.get("title") or ""),
        "dimension": "3d" if size_3d else "1d",
        "size": size_3d or size_1d,
        "domain_min": [float(value) for value in meta.get("domain_min") or [0.0, 0.0, 0.0]],
        "domain_max": [float(value) for value in meta.get("domain_max") or [1.0, 1.0, 1.0]],
        "data": values,
    }


def _clamp01(value: float) -> float:
    if not math.isfinite(value):
        return 0.0
    if value <= 0:
        return 0.0
    if value >= 1:
        return 1.0
    return value


def _normalize_channel(value: float, minimum: float, maximum: float) -> float:
    span = maximum - minimum
    if not math.isfinite(span) or span <= 0:
        return 0.0
    return _clamp01((value - minimum) / span)


def _to_byte(value: float) -> int:
    """等价 JS ``Math.round`` 后写入 Uint8ClampedArray：半数向上并 clamp 到 0..255。"""
    byte = math.floor(value + 0.5)
    if byte <= 0:
        return 0
    if byte >= 255:
        return 255
    return byte


def apply_lut_to_pixels(pixels: bytes, table: dict, strength: float) -> bytes:
    """按强度把 LUT 套到 RGBA 像素上；alpha 原样保留。

    运算顺序刻意与前端 ``lutLookupInto`` / ``composeLutPreview`` 一致：
    先按通道归一化，再在 3D 立方体内做三线性插值（或 1D 逐通道线性插值），
    最后 ``round((v + (lut - v) * strength) * 255)``。
    """
    if len(pixels) % 4:
        raise LutRenderError("LUT_RENDER_FAILED", "像素缓冲区不是 RGBA 排列")
    size = int(table["size"])
    data = table["data"]
    domain_min = table["domain_min"]
    domain_max = table["domain_max"]
    scale = size - 1
    ratio = _clamp01(float(strength))
    out = bytearray(len(pixels))

    if table["dimension"] == "1d":
        for offset in range(0, len(pixels), 4):
            red = pixels[offset] / 255.0
            green = pixels[offset + 1] / 255.0
            blue = pixels[offset + 2] / 255.0
            inputs = (
                _normalize_channel(red, domain_min[0], domain_max[0]),
                _normalize_channel(green, domain_min[1], domain_max[1]),
                _normalize_channel(blue, domain_min[2], domain_max[2]),
            )
            originals = (red, green, blue)
            for channel in range(3):
                position = inputs[channel] * scale
                index0 = min(size - 1, math.floor(position))
                index1 = min(size - 1, index0 + 1)
                weight = position - index0
                graded = (
                    data[index0 * 3 + channel] * (1 - weight)
                    + data[index1 * 3 + channel] * weight
                )
                # 强度混合的基准是「原始通道值」，不是归一化值（与前端同口径）。
                source = originals[channel]
                out[offset + channel] = _to_byte((source + (graded - source) * ratio) * 255)
            out[offset + 3] = pixels[offset + 3]
        return bytes(out)

    stride_g = size * 3
    stride_b = size * size * 3
    for offset in range(0, len(pixels), 4):
        red = pixels[offset] / 255.0
        green = pixels[offset + 1] / 255.0
        blue = pixels[offset + 2] / 255.0
        position_r = _normalize_channel(red, domain_min[0], domain_max[0]) * scale
        position_g = _normalize_channel(green, domain_min[1], domain_max[1]) * scale
        position_b = _normalize_channel(blue, domain_min[2], domain_max[2]) * scale
        r0 = min(size - 1, math.floor(position_r))
        g0 = min(size - 1, math.floor(position_g))
        b0 = min(size - 1, math.floor(position_b))
        r1 = min(size - 1, r0 + 1)
        g1 = min(size - 1, g0 + 1)
        b1 = min(size - 1, b0 + 1)
        weight_r = position_r - r0
        weight_g = position_g - g0
        weight_b = position_b - b0
        base_00 = b0 * stride_b + g0 * stride_g
        base_10 = b0 * stride_b + g1 * stride_g
        base_01 = b1 * stride_b + g0 * stride_g
        base_11 = b1 * stride_b + g1 * stride_g
        for channel in range(3):
            c00 = data[base_00 + r0 * 3 + channel] * (1 - weight_r) + \
                data[base_00 + r1 * 3 + channel] * weight_r
            c10 = data[base_10 + r0 * 3 + channel] * (1 - weight_r) + \
                data[base_10 + r1 * 3 + channel] * weight_r
            c01 = data[base_01 + r0 * 3 + channel] * (1 - weight_r) + \
                data[base_01 + r1 * 3 + channel] * weight_r
            c11 = data[base_11 + r0 * 3 + channel] * (1 - weight_r) + \
                data[base_11 + r1 * 3 + channel] * weight_r
            low = c00 * (1 - weight_g) + c10 * weight_g
            high = c01 * (1 - weight_g) + c11 * weight_g
            graded = low * (1 - weight_b) + high * weight_b
            source = red if channel == 0 else green if channel == 1 else blue
            out[offset + channel] = _to_byte((source + (graded - source) * ratio) * 255)
        out[offset + 3] = pixels[offset + 3]
    return bytes(out)


def render_image_to_png(
    source: Path, table: dict, strength: float, output: Path
) -> dict:
    """把图片素材套用 LUT 后编码为 PNG（无损，像素与预览逐点可比）。"""
    try:
        from PIL import Image  # type: ignore
    except ImportError as exc:  # pragma: no cover - 部署缺依赖时失败关闭
        raise LutRenderError("PIL_NOT_AVAILABLE", "未安装图片处理依赖，无法直出") from exc

    try:
        with Image.open(source) as image:
            image.load()
            # 统一按 RGBA 处理：浏览器预览的 ImageData 恒为 RGBA，同口径才能逐像素对齐；
            # 只按「长度能被 4 整除」判断通道数会被 4x4 的 RGB 图骗过（48 % 4 == 0）。
            converted = image.convert("RGBA")
    except (OSError, ValueError) as exc:
        raise LutRenderError("LUT_SOURCE_UNSUPPORTED", "素材不可解码，无法套用 LUT") from exc

    width, height = converted.size
    if width <= 0 or height <= 0:
        raise LutRenderError("LUT_SOURCE_UNSUPPORTED", "素材尺寸不可用")
    if width * height > MAX_IMAGE_PIXELS:
        raise LutRenderError(
            "LUT_SOURCE_TOO_LARGE",
            f"素材 {width}x{height} 超过 {MAX_IMAGE_PIXELS} 像素上限",
        )
    rendered = apply_lut_to_pixels(converted.tobytes(), table, strength)
    result = Image.frombytes(converted.mode, (width, height), rendered)
    output.parent.mkdir(parents=True, exist_ok=True)
    try:
        result.save(output, format="PNG")
    except OSError as exc:
        raise LutRenderError("LUT_RENDER_FAILED", "图片编码失败") from exc
    return {
        "width": width,
        "height": height,
        "size_bytes": output.stat().st_size,
    }


def render_video_to_mp4(
    source: Path, cube_path: Path, strength: float, output: Path
) -> dict:
    """用 ffmpeg ``lut3d`` 转码视频，强度以原图/调色图混合近似。"""
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise LutRenderError("FFMPEG_NOT_AVAILABLE", "未找到 ffmpeg，视频转码不可用")
    ratio = _clamp01(float(strength))
    output.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="gw_lut_render_") as workdir:
        # Windows 盘符冒号在 filter 里需要转义；统一复制成相对名再喂 lut3d，避开转义坑。
        look = Path(workdir) / "look.cube"
        look.write_bytes(Path(cube_path).read_bytes())
        if ratio >= 0.9999:
            graph = "[0:v]format=yuv420p,lut3d=look.cube[out]"
        else:
            # blend 的第一个输入是 top 层，`all_opacity` 控制的是 **top** 的权重。
            # 因此 top 必须是「调色后」，才能让 ratio=0 得到原片、ratio=1 得到满调色；
            # 反过来写（top 放原片）会把强度整个拧反：0% 变满调色、75% 只剩 25% 调色。
            graph = (
                "[0:v]format=yuv420p,split=2[base][graded];"
                "[graded]lut3d=look.cube,format=yuv420p[graded];"
                f"[graded][base]blend=all_mode=normal:all_opacity={ratio:.4f}[out]"
            )
        partial = output.with_name(output.stem + ".part" + output.suffix)
        command = [
            ffmpeg,
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(source),
            "-filter_complex",
            graph,
            "-map",
            "[out]",
            "-map",
            "0:a?",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "20",
            "-pix_fmt",
            "yuv420p",
            "-movflags",
            "+faststart",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            str(partial),
        ]
        try:
            completed = subprocess.run(
                command,
                cwd=workdir,
                capture_output=True,
                timeout=VIDEO_TIMEOUT_SEC,
                check=False,
            )
            if completed.returncode != 0 or not partial.is_file() or partial.stat().st_size <= 0:
                raise LutRenderError("LUT_RENDER_FAILED", "视频转码失败")
            partial.replace(output)
        except subprocess.TimeoutExpired as exc:
            raise LutRenderError("LUT_RENDER_FAILED", "视频转码超时") from exc
        except OSError as exc:
            raise LutRenderError("LUT_RENDER_FAILED", "媒体处理进程执行失败") from exc
        finally:
            partial.unlink(missing_ok=True)

    return {"size_bytes": output.stat().st_size}
