/**
 * Copyright 2026 Gods-Workbench Authors
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 *     http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */

/**
 * QIN-554：Cube LUT 的前端解析与样片预览。
 *
 * 只做「读 + 算」：把 `.cube` 文本解析成查表数据，用统一样片逐像素套用，
 * 供资产卡片缩略与详情工作区共用。不写回原文件、不接时间线、不触发导出。
 *
 * 校验口径与后端 `gw/asset_registry/lut_cube.py` 保持一致（头信息顺序/重复、
 * 尺寸区间、采样行数、有限数值），因此前端失败态与索引失败态可互相印证。
 */

export const LUT_PREVIEW_MAX_BYTES = 64 * 1024 * 1024;

/** 详情工作区与卡片缩略共用的样片尺寸（宽高比 16:9，卡片按容器裁切）。 */
export const LUT_PREVIEW_SAMPLE_WIDTH = 640;
export const LUT_PREVIEW_SAMPLE_HEIGHT = 360;

const HEADER_DIRECTIVES = new Set([
    'TITLE',
    'LUT_1D_SIZE',
    'LUT_3D_SIZE',
    'DOMAIN_MIN',
    'DOMAIN_MAX',
    'LUT_1D_INPUT_RANGE',
    'LUT_3D_INPUT_RANGE',
]);

const INTEGER_TOKEN = /^[0-9]+$/;

/** 带稳定错误码的 LUT 预览错误；UI 直接展示 message。 */
export class LutPreviewError extends Error {
    constructor(code, message) {
        super(message);
        this.name = 'LutPreviewError';
        this.code = code;
    }
}

function invalid(message) {
    return new LutPreviewError('LUT_CUBE_INVALID', message);
}

function parseFiniteNumber(token, lineNumber) {
    const value = Number(token);
    if (!Number.isFinite(value)) throw invalid(`LUT 第 ${lineNumber} 行包含无效数字`);
    return value;
}

/**
 * 解析 Cube LUT 文本为查表数据。
 * 返回 `{title, dimension, size, domainMin, domainMax, sampleCount, data}`，
 * `data` 为 Float32Array，按 Cube 规范 red 变化最快；3D 为 `size^3*3`，1D 为 `size*3`。
 */
export function parseCubeLut(source) {
    const text = String(source ?? '');
    if (text.length > LUT_PREVIEW_MAX_BYTES) {
        throw new LutPreviewError('LUT_CUBE_TOO_LARGE', 'LUT 文件超过 64 MiB 上限');
    }
    if (!text.trim()) throw invalid('LUT 文件为空');

    let title = '';
    let size1d = 0;
    let size3d = 0;
    let domainMin = [0, 0, 0];
    let domainMax = [1, 1, 1];
    const directives = new Set();
    const samples = [];
    let dataStarted = false;
    let lineNumber = 0;

    const lines = text.split(/\r?\n/);
    for (const rawLine of lines) {
        lineNumber += 1;
        const content = rawLine.split('#')[0].trim();
        if (!content) continue;
        const parts = content.split(/\s+/);
        const directive = parts[0].toUpperCase();
        if (HEADER_DIRECTIVES.has(directive)) {
            if (dataStarted) throw invalid(`LUT 第 ${lineNumber} 行头信息出现在采样数据之后`);
            if (directives.has(directive)) throw invalid(`LUT 头信息 ${directive} 重复`);
            directives.add(directive);
            if (directive === 'TITLE') {
                let value = content.slice(parts[0].length).trim();
                if (!value) throw invalid(`LUT 第 ${lineNumber} 行缺少 TITLE 内容`);
                if (value.length >= 2 && value.startsWith('"') && value.endsWith('"')) {
                    value = value.slice(1, -1);
                }
                title = value.slice(0, 300);
                continue;
            }
            if (directive === 'LUT_1D_SIZE' || directive === 'LUT_3D_SIZE') {
                if (parts.length !== 2 || !INTEGER_TOKEN.test(parts[1])) {
                    throw invalid(`LUT 第 ${lineNumber} 行的 ${directive} 无效`);
                }
                const size = Number(parts[1]);
                if (directive === 'LUT_1D_SIZE') {
                    if (size < 2 || size > 65536) throw invalid('LUT_1D_SIZE 必须在 2 到 65536 之间');
                    size1d = size;
                } else {
                    if (size < 2 || size > 256) throw invalid('LUT_3D_SIZE 必须在 2 到 256 之间');
                    size3d = size;
                }
                continue;
            }
            if (directive === 'DOMAIN_MIN' || directive === 'DOMAIN_MAX') {
                if (parts.length !== 4) throw invalid(`LUT 第 ${lineNumber} 行的 ${directive} 必须有三个数值`);
                const values = parts.slice(1).map(token => parseFiniteNumber(token, lineNumber));
                if (directive === 'DOMAIN_MIN') domainMin = values;
                else domainMax = values;
                continue;
            }
            if (parts.length !== 3) throw invalid(`LUT 第 ${lineNumber} 行的 ${directive} 必须有两个数值`);
            const range = parts.slice(1).map(token => parseFiniteNumber(token, lineNumber));
            if (range[0] >= range[1]) throw invalid(`LUT 第 ${lineNumber} 行的输入范围无效`);
            continue;
        }

        if (parts.length !== 3) throw invalid(`LUT 第 ${lineNumber} 行必须是三个通道数值`);
        samples.push(
            parseFiniteNumber(parts[0], lineNumber),
            parseFiniteNumber(parts[1], lineNumber),
            parseFiniteNumber(parts[2], lineNumber),
        );
        dataStarted = true;
    }

    for (let channel = 0; channel < 3; channel += 1) {
        if (domainMin[channel] >= domainMax[channel]) {
            throw invalid('LUT DOMAIN_MIN 必须逐通道小于 DOMAIN_MAX');
        }
    }

    if (!size1d && !size3d) throw invalid('LUT 文件缺少 LUT_1D_SIZE 或 LUT_3D_SIZE');
    const expectedRows = size1d + size3d ** 3;
    const actualRows = samples.length / 3;
    if (actualRows !== expectedRows) {
        throw invalid(`LUT 采样行数应为 ${expectedRows}，实际为 ${actualRows}`);
    }

    return {
        title,
        dimension: size3d ? '3d' : '1d',
        size: size3d || size1d,
        size1d,
        size3d,
        domainMin,
        domainMax,
        sampleCount: actualRows,
        data: Float32Array.from(samples),
    };
}

function clamp01(value) {
    if (!Number.isFinite(value)) return 0;
    if (value <= 0) return 0;
    if (value >= 1) return 1;
    return value;
}

function normalizeChannel(value, minimum, maximum) {
    const span = maximum - minimum;
    if (!Number.isFinite(span) || span <= 0) return 0;
    return clamp01((value - minimum) / span);
}

/** 查表：返回 `[r,g,b]`（0..1）。3D 用三线性插值，1D 用逐通道线性插值。 */
export function lutLookup(lut, red, green, blue) {
    const out = [0, 0, 0];
    lutLookupInto(lut, red, green, blue, out);
    return out;
}

/** 无分配的查表入口；`out` 为长度 >= 3 的数组或 Float32Array。 */
export function lutLookupInto(lut, red, green, blue, out) {
    if (!lut || !lut.data) throw invalid('LUT 数据不可用');
    const nr = normalizeChannel(red, lut.domainMin[0], lut.domainMax[0]);
    const ng = normalizeChannel(green, lut.domainMin[1], lut.domainMax[1]);
    const nb = normalizeChannel(blue, lut.domainMin[2], lut.domainMax[2]);
    const data = lut.data;

    if (lut.dimension === '1d') {
        // 1D LUT 只有一条通道轴：每个输入通道各取同通道的插值结果。
        const size = lut.size;
        const scale = size - 1;
        for (let channel = 0; channel < 3; channel += 1) {
            const input = channel === 0 ? nr : channel === 1 ? ng : nb;
            const position = input * scale;
            const index0 = Math.min(size - 1, Math.floor(position));
            const index1 = Math.min(size - 1, index0 + 1);
            const weight = position - index0;
            out[channel] = data[index0 * 3 + channel] * (1 - weight) + data[index1 * 3 + channel] * weight;
        }
        return out;
    }

    const size = lut.size;
    const scale = size - 1;
    const pr = nr * scale;
    const pg = ng * scale;
    const pb = nb * scale;
    const r0 = Math.min(size - 1, Math.floor(pr));
    const g0 = Math.min(size - 1, Math.floor(pg));
    const b0 = Math.min(size - 1, Math.floor(pb));
    const r1 = Math.min(size - 1, r0 + 1);
    const g1 = Math.min(size - 1, g0 + 1);
    const b1 = Math.min(size - 1, b0 + 1);
    const tr = pr - r0;
    const tg = pg - g0;
    const tb = pb - b0;
    const strideR = 3;
    const strideG = size * 3;
    const strideB = size * size * 3;

    for (let channel = 0; channel < 3; channel += 1) {
        const at = (r, g, b) => data[b * strideB + g * strideG + r * strideR + channel];
        const c00 = at(r0, g0, b0) * (1 - tr) + at(r1, g0, b0) * tr;
        const c10 = at(r0, g1, b0) * (1 - tr) + at(r1, g1, b0) * tr;
        const c01 = at(r0, g0, b1) * (1 - tr) + at(r1, g0, b1) * tr;
        const c11 = at(r0, g1, b1) * (1 - tr) + at(r1, g1, b1) * tr;
        const c0 = c00 * (1 - tg) + c10 * tg;
        const c1 = c01 * (1 - tg) + c11 * tg;
        out[channel] = c0 * (1 - tb) + c1 * tb;
    }
    return out;
}

function hslToRgb(hue, saturation, lightness, out) {
    const h = ((hue % 360) + 360) % 360;
    const c = (1 - Math.abs(2 * lightness - 1)) * saturation;
    const x = c * (1 - Math.abs(((h / 60) % 2) - 1));
    const m = lightness - c / 2;
    let r = 0;
    let g = 0;
    let b = 0;
    if (h < 60) { r = c; g = x; }
    else if (h < 120) { r = x; g = c; }
    else if (h < 180) { g = c; b = x; }
    else if (h < 240) { g = x; b = c; }
    else if (h < 300) { r = x; b = c; }
    else { r = c; b = x; }
    out[0] = Math.round((r + m) * 255);
    out[1] = Math.round((g + m) * 255);
    out[2] = Math.round((b + m) * 255);
}

const SAMPLE_GRAY_STEPS = 8;
const SAMPLE_SATURATED = [
    [255, 0, 0],
    [255, 128, 0],
    [255, 255, 0],
    [0, 200, 60],
    [0, 220, 220],
    [0, 60, 220],
    [200, 0, 220],
    [232, 178, 146],
];

/**
 * 生成确定性样片（不依赖任何二进制素材）：
 * 上段 8 级灰阶、中段 8 块饱和色与肤色、下段色相 × 明度的二维渐变。
 * 同一 LUT 在任何卡片与详情里都套同一张样片，便于横向比较。
 */
export function lutSampleImageData(width = LUT_PREVIEW_SAMPLE_WIDTH, height = LUT_PREVIEW_SAMPLE_HEIGHT) {
    const w = Math.max(2, Math.round(Number(width) || LUT_PREVIEW_SAMPLE_WIDTH));
    const h = Math.max(2, Math.round(Number(height) || LUT_PREVIEW_SAMPLE_HEIGHT));
    const data = new Uint8ClampedArray(w * h * 4);
    const grayBottom = Math.round(h * 0.34);
    const saturatedBottom = Math.round(h * 0.62);
    const rgb = [0, 0, 0];

    for (let y = 0; y < h; y += 1) {
        for (let x = 0; x < w; x += 1) {
            const offset = (y * w + x) * 4;
            if (y < grayBottom) {
                const step = Math.min(SAMPLE_GRAY_STEPS - 1, Math.floor((x / w) * SAMPLE_GRAY_STEPS));
                const level = Math.round((step / (SAMPLE_GRAY_STEPS - 1)) * 255);
                data[offset] = level;
                data[offset + 1] = level;
                data[offset + 2] = level;
            } else if (y < saturatedBottom) {
                const step = Math.min(SAMPLE_SATURATED.length - 1, Math.floor((x / w) * SAMPLE_SATURATED.length));
                const patch = SAMPLE_SATURATED[step];
                data[offset] = patch[0];
                data[offset + 1] = patch[1];
                data[offset + 2] = patch[2];
            } else {
                const hue = (x / w) * 360;
                const ratio = (y - saturatedBottom) / Math.max(1, h - 1 - saturatedBottom);
                const lightness = 0.86 - ratio * 0.72;
                hslToRgb(hue, 0.78, lightness, rgb);
                data[offset] = rgb[0];
                data[offset + 1] = rgb[1];
                data[offset + 2] = rgb[2];
            }
            data[offset + 3] = 255;
        }
    }
    return {width: w, height: h, data};
}

export const LUT_COMPARE_MODES = ['applied', 'split', 'original'];

/**
 * 按强度与对比模式合成预览帧。
 * - `applied`：全帧按强度插值套用；
 * - `original`：原样返回（0% 的等价断言入口）；
 * - `split`：左半原图、右半套用，分界列由 `splitX`（0..1）决定。
 */
export function composeLutPreview(sample, lut, options = {}) {
    if (!sample || !sample.data) throw invalid('预览样片不可用');
    const width = Math.max(1, Math.round(Number(sample.width) || 0));
    const height = Math.max(1, Math.round(Number(sample.height) || 0));
    const mode = LUT_COMPARE_MODES.includes(options.mode) ? options.mode : 'applied';
    const strength = clamp01(options.strength === undefined ? 1 : Number(options.strength));
    const splitX = clamp01(options.splitX === undefined ? 0.5 : Number(options.splitX));
    const source = sample.data;

    if (mode === 'original' || !lut) {
        return {width, height, data: Uint8ClampedArray.from(source)};
    }

    const out = new Uint8ClampedArray(source.length);
    const splitColumn = Math.round(width * splitX);
    const lookup = [0, 0, 0];
    for (let y = 0; y < height; y += 1) {
        for (let x = 0; x < width; x += 1) {
            const offset = (y * width + x) * 4;
            const keepOriginal = mode === 'split' && x < splitColumn;
            if (keepOriginal) {
                out[offset] = source[offset];
                out[offset + 1] = source[offset + 1];
                out[offset + 2] = source[offset + 2];
                out[offset + 3] = source[offset + 3];
                continue;
            }
            const red = source[offset] / 255;
            const green = source[offset + 1] / 255;
            const blue = source[offset + 2] / 255;
            lutLookupInto(lut, red, green, blue, lookup);
            out[offset] = Math.round((red + (lookup[0] - red) * strength) * 255);
            out[offset + 1] = Math.round((green + (lookup[1] - green) * strength) * 255);
            out[offset + 2] = Math.round((blue + (lookup[2] - blue) * strength) * 255);
            out[offset + 3] = source[offset + 3];
        }
    }
    return {width, height, data: out};
}

/** 把合成帧画到 canvas；环境缺少 2D 上下文或 ImageData 时返回 false。 */
export function paintLutFrame(canvas, frame) {
    if (!canvas || !frame) return false;
    const context = typeof canvas.getContext === 'function' ? canvas.getContext('2d') : null;
    if (!context) return false;
    if (typeof ImageData !== 'function') return false;
    canvas.width = frame.width;
    canvas.height = frame.height;
    context.putImageData(new ImageData(new Uint8ClampedArray(frame.data), frame.width, frame.height), 0, 0);
    return true;
}
