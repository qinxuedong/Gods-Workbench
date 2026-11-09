import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import test from 'node:test';

import {
    composeLutPreview,
    LUT_COMPARE_MODES,
    lutLookup,
    lutSampleImageData,
    parseCubeLut,
} from '../../web/js/modules/asset-manager/lut-preview.js';

const read = relative => readFileSync(new URL(`../../${relative}`, import.meta.url), 'utf8');
const cube = name => read(`web/vendor/luts/${name}`);
const manager = read('web/js/modules/asset-manager.js');
const styles = read('web/css/pages/asset-manager.css');

const pixelAt = (frame, x, y) => {
    const offset = (y * frame.width + x) * 4;
    return [frame.data[offset], frame.data[offset + 1], frame.data[offset + 2]];
};
const differs = (left, right) => left.data.some((value, index) => value !== right.data[index]);

test('QIN-554：内置 .cube 可解析出标题、网格与采样数', () => {
    const warm = parseCubeLut(cube('builtin_lut_warm.cube'));
    assert.equal(warm.title, 'GW Warm');
    assert.equal(warm.dimension, '3d');
    assert.equal(warm.size, 8);
    assert.equal(warm.sampleCount, 512);
    assert.equal(warm.data.length, 512 * 3);
    assert.deepEqual(warm.domainMin, [0, 0, 0]);
    assert.deepEqual(warm.domainMax, [1, 1, 1]);
    // 暖调：中灰查表后 R 更高、B 更低，说明通道顺序与插值方向正确。
    const [red, green, blue] = lutLookup(warm, 0.5, 0.5, 0.5);
    assert.ok(red > green, `${red} 应大于 ${green}`);
    assert.ok(green > blue, `${green} 应大于 ${blue}`);
});

test('QIN-554：非法 LUT 一律带稳定错误码，不静默产出错帧', () => {
    const invalidCases = [
        '',
        'LUT_3D_SIZE 2\n0 0 0\n0 0 1\n',                 // 采样行数不足
        '0 0 0\n0 0 1\n',                                 // 缺少尺寸头
        'LUT_3D_SIZE 2\nLUT_3D_SIZE 2\n',                 // 头信息重复
        'TITLE "x"\nLUT_3D_SIZE 700\n',                   // 尺寸超限
        'LUT_3D_SIZE 2\n0 0 0\n0 0 nan\n',                // 非有限数字
        'LUT_3D_SIZE 2\n0 0 0\n0 0 1\n0 0 0 0\n',         // 通道数不是 3
    ];
    invalidCases.forEach(source => {
        assert.throws(() => parseCubeLut(source), error => {
            assert.ok(['LUT_CUBE_INVALID', 'LUT_CUBE_TOO_LARGE'].includes(error.code), source);
            assert.ok(error.message.length > 0);
            return true;
        }, source);
    });
});

test('QIN-554：统一样片确定性生成，含灰阶与饱和色块', () => {
    const first = lutSampleImageData(96, 54);
    const second = lutSampleImageData(96, 54);
    assert.equal(first.width, 96);
    assert.equal(first.height, 54);
    assert.deepEqual([...first.data], [...second.data]);
    // 上段灰阶：最左为黑、最右为白且三通道相等。
    assert.deepEqual(pixelAt(first, 0, 2), [0, 0, 0]);
    assert.deepEqual(pixelAt(first, 95, 2), [255, 255, 255]);
    // 中段饱和色块：首块为纯红，说明样片不是单色占位。
    assert.deepEqual(pixelAt(first, 2, 30), [255, 0, 0]);
    // 下段色相渐变：跨越半幅应出现明显色相差异。
    assert.notDeepEqual(pixelAt(first, 4, 52), pixelAt(first, 90, 52));
});

test('QIN-554：强度 0 恒等于原图，100% 生效，50% 居中', () => {
    const lut = parseCubeLut(cube('builtin_lut_warm.cube'));
    const sample = lutSampleImageData(96, 54);
    const zero = composeLutPreview(sample, lut, {strength:0});
    const full = composeLutPreview(sample, lut, {strength:1});
    const half = composeLutPreview(sample, lut, {strength:.5});
    assert.deepEqual([...zero.data], [...sample.data]);
    assert.ok(differs(sample, full), '100% 必须与原图不同');
    const point = [48, 10];
    const source = pixelAt(sample, ...point);
    const fullPixel = pixelAt(full, ...point);
    const halfPixel = pixelAt(half, ...point);
    for(let channel = 0; channel < 3; channel += 1){
        const expected = Math.round(source[channel] + (fullPixel[channel] - source[channel]) * .5);
        assert.ok(Math.abs(halfPixel[channel] - expected) <= 1, `通道 ${channel} 的 50% 结果应介于两端`);
    }
});

test('QIN-554：分屏对比左半原图、右半套用', () => {
    assert.deepEqual(LUT_COMPARE_MODES, ['applied', 'split', 'original']);
    const lut = parseCubeLut(cube('builtin_lut_cool.cube'));
    const sample = lutSampleImageData(96, 54);
    const full = composeLutPreview(sample, lut, {strength:1});
    const split = composeLutPreview(sample, lut, {mode:'split', strength:1, splitX:.5});
    const splitColumn = Math.round(96 * .5);
    for(let y = 0; y < sample.height; y += 1){
        for(let x = 0; x < splitColumn; x += 1){
            assert.deepEqual(pixelAt(split, x, y), pixelAt(sample, x, y));
        }
        for(let x = splitColumn; x < sample.width; x += 1){
            assert.deepEqual(pixelAt(split, x, y), pixelAt(full, x, y));
        }
    }
    const original = composeLutPreview(sample, lut, {mode:'original'});
    assert.deepEqual([...original.data], [...sample.data]);
});

test('QIN-554：恒等 LUT 不改变样片，验证查表与插值无偏移', () => {
    const neutral = parseCubeLut(cube('builtin_lut_neutral.cube'));
    const sample = lutSampleImageData(120, 68);
    const applied = composeLutPreview(sample, neutral, {strength:1});
    assert.deepEqual([...applied.data], [...sample.data]);
});

test('QIN-554：卡片与详情接线齐备（缩略 canvas / 侧栏切换 / 强度 / 对比）', () => {
    // LUT 资源判定与详情类型分支
    assert.match(manager, /const ASSET_LUT_PREVIEW_EXTENSIONS = \['cube'\];/);
    assert.match(manager, /if\(ASSET_LUT_PREVIEW_EXTENSIONS\.includes\(extension\) \|\| kind === 'lut'\) return 'lut';/);
    assert.match(manager, /if\(lutPreviewIsAsset\(item\)\) return renderRegistryLutThumb\(item\);/);
    // 卡片缩略：真实像素 canvas + 明确失败态
    assert.match(manager, /<canvas class="registry-lut-thumb-canvas" data-lut-thumb-url=/);
    assert.match(manager, /failRegistryLutThumb\(canvas, err\?\.code === 'LUT_CUBE_INVALID' \? 'LUT 解析失败'/);
    assert.match(manager, /scheduleRegistryLutThumbs\(\);/);
    // 详情工作区：同库侧栏、强度滑杆、三态对比
    assert.match(manager, /if\(type === 'lut'\) return renderDetailLutWorkspace\(source, item\);/);
    assert.match(manager, /data-detail-lut-item="\$\{escapeAttr\(lut\.id \|\| ''\)\}"/);
    assert.match(manager, /data-detail-lut-strength aria-label="LUT 强度"/);
    ['applied', 'split', 'original'].forEach(mode => {
        assert.match(manager, new RegExp(`data-detail-lut-compare="${mode}"`));
    });
    assert.match(manager, /const lutItem = event\.target\.closest\?\.\('\[data-detail-lut-item\]'\);/);
    assert.match(manager, /const lutCompare = event\.target\.closest\?\.\('\[data-detail-lut-compare\]'\);/);
    assert.match(manager, /const detailLutStrength = event\.target\?\.closest\?\.\('\[data-detail-lut-strength\]'\);/);
    assert.match(manager, /if\(type === 'lut'\) initializeDetailLutPreview\(overlay, item\);/);
    // 预览组件与样式挂点
    assert.match(manager, /from '\.\/asset-manager\/lut-preview\.js'/);
    assert.match(styles, /\.asset-detail-lut-workspace \{/);
    assert.match(styles, /\.registry-lut-thumb-canvas \{/);
    assert.match(styles, /\.registry-lut-thumb\.is-error \{/);
});
