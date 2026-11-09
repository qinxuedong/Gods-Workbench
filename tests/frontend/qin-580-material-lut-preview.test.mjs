import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import test from 'node:test';

import {
    composeLutPreview,
    lutLookup,
    mediaLutSamplable,
    mediaToLutSample,
    parseCubeLut,
} from '../../web/js/modules/asset-manager/lut-preview.js';

const read = relative => readFileSync(new URL(`../../${relative}`, import.meta.url), 'utf8');
const cube = name => read(`web/vendor/luts/${name}`);
const manager = read('web/js/modules/asset-manager.js');
const styles = read('web/css/pages/asset-manager.css');
const previewModule = read('web/js/modules/asset-manager/lut-preview.js');

/** QIN-580 新增代码段：用于「只读」与接线断言，避免扫到无关历史代码。 */
const mediaLutSection = (() => {
    const start = manager.indexOf('QIN-580：给素材（图片 / 视频）实时套用 LUT 预览');
    assert.ok(start > 0, '找不到 QIN-580 新增代码段');
    const end = manager.indexOf('function renderDetailLutWorkspace(source, item){', start);
    assert.ok(end > start, '找不到 QIN-580 代码段结束位置');
    return manager.slice(start, end);
})();

const pixelAt = (frame, x, y) => {
    const offset = (y * frame.width + x) * 4;
    return [frame.data[offset], frame.data[offset + 1], frame.data[offset + 2]];
};

/** 在假 canvas 环境里跑一次取样，避免污染其它用例。 */
function withMockCanvas(makePixels, run) {
    const hadDocument = Object.prototype.hasOwnProperty.call(globalThis, 'document');
    const original = globalThis.document;
    const context = {
        drawImage() {},
        getImageData(x, y, width, height) {
            return {width, height, data: makePixels(width, height)};
        },
    };
    globalThis.document = {
        createElement(tag) {
            assert.equal(tag, 'canvas', '取样只应创建 canvas');
            return {width: 0, height: 0, getContext: () => context};
        },
    };
    try {
        return run();
    } finally {
        if (hadDocument) globalThis.document = original;
        else delete globalThis.document;
    }
}

test('QIN-580：素材可取样判定按加载状态给出，未就绪不硬套', () => {
    assert.equal(mediaLutSamplable(null), false);
    // 尺寸还没解析出来（图片未加载 / 视频无元数据）时不能套用。
    assert.equal(mediaLutSamplable({tagName:'IMG', naturalWidth:0, naturalHeight:0, complete:true}), false);
    assert.equal(mediaLutSamplable({tagName:'IMG', naturalWidth:800, naturalHeight:600, complete:true}), true);
    assert.equal(mediaLutSamplable({tagName:'IMG', naturalWidth:800, naturalHeight:600, complete:false}), false);
    // 视频要有当前帧（readyState>=2）才能取像素，否则 getImageData 会拿到黑帧。
    assert.equal(mediaLutSamplable({tagName:'VIDEO', videoWidth:1920, videoHeight:1080, readyState:1}), false);
    assert.equal(mediaLutSamplable({tagName:'VIDEO', videoWidth:1920, videoHeight:1080, readyState:2}), true);
});

test('QIN-580：大尺寸素材按最长边降采样，避免逐像素查表拖慢', () => {
    const frame = withMockCanvas(
        (width, height) => new Uint8ClampedArray(width * height * 4).fill(128),
        () => mediaToLutSample({tagName:'IMG', naturalWidth:4096, naturalHeight:2160, complete:true}),
    );
    // 默认上限 1280，高度按比例：2160 * 1280 / 4096 = 675。
    assert.equal(frame.width, 1280);
    assert.equal(frame.height, 675);
    assert.equal(frame.data.length, 1280 * 675 * 4);
});

test('QIN-580：小素材不放大，视频可指定更小的取样宽度', () => {
    const small = withMockCanvas(
        (width, height) => new Uint8ClampedArray(width * height * 4).fill(64),
        () => mediaToLutSample({tagName:'IMG', naturalWidth:320, naturalHeight:180, complete:true}),
    );
    assert.equal(small.width, 320);
    assert.equal(small.height, 180);

    const video = withMockCanvas(
        (width, height) => new Uint8ClampedArray(width * height * 4).fill(96),
        () => mediaToLutSample({tagName:'VIDEO', videoWidth:1920, videoHeight:1080, readyState:2}, {maxWidth:720}),
    );
    // 视频逐帧取样比图片贵，这里钉住 720 的降采样口径。
    assert.equal(video.width, 720);
    assert.equal(video.height, 405);
});

test('QIN-580：取样失败一律带稳定错误码，不静默产出错帧', () => {
    withMockCanvas(
        () => new Uint8ClampedArray(4),
        () => {
            assert.throws(
                () => mediaToLutSample({tagName:'IMG', naturalWidth:0, naturalHeight:0}),
                error => error.code === 'LUT_SOURCE_UNAVAILABLE' && /尺寸不可用/.test(error.message),
            );

            // 跨源污染会让 getImageData 抛 SecurityError，必须转成可读的失败态。
            const hadDocument = globalThis.document;
            globalThis.document = {
                createElement: () => ({
                    width: 0,
                    height: 0,
                    getContext: () => ({
                        drawImage() {},
                        getImageData() { throw new Error('The canvas has been tainted'); },
                    }),
                }),
            };
            try {
                assert.throws(
                    () => mediaToLutSample({tagName:'IMG', naturalWidth:800, naturalHeight:600, complete:true}),
                    error => error.code === 'LUT_SOURCE_UNAVAILABLE' && /跨源/.test(error.message),
                );
            } finally {
                globalThis.document = hadDocument;
            }
        },
    );
});

test('QIN-580：素材真实像素上 0% 等于原样、100% 等于完整查表', () => {
    const lut = parseCubeLut(cube('builtin_lut_warm.cube'));
    // 4x2 的假素材帧：左侧纯灰、右侧饱和色，覆盖插值边界。
    const data = new Uint8ClampedArray(4 * 2 * 4);
    const seeds = [[0, 0, 0], [128, 128, 128], [200, 60, 40], [20, 90, 210]];
    for (let index = 0; index < 8; index += 1) {
        const [r, g, b] = seeds[index % seeds.length];
        const offset = index * 4;
        data[offset] = r;
        data[offset + 1] = g;
        data[offset + 2] = b;
        data[offset + 3] = 255;
    }
    const sample = {width:4, height:2, data};

    const original = composeLutPreview(sample, lut, {mode:'applied', strength:0});
    assert.deepEqual([...original.data], [...data], '0% 必须逐像素等于原图');

    const applied = composeLutPreview(sample, lut, {mode:'applied', strength:1});
    for (let y = 0; y < 2; y += 1) {
        for (let x = 0; x < 4; x += 1) {
            const offset = (y * 4 + x) * 4;
            const expected = lutLookup(lut, data[offset] / 255, data[offset + 1] / 255, data[offset + 2] / 255);
            assert.deepEqual(
                pixelAt(applied, x, y),
                expected.map(value => Math.round(value * 255)),
                `(${x},${y}) 应等于完整查表结果`,
            );
        }
    }
    assert.notDeepEqual([...applied.data], [...data], '100% 必须与原图有差异');
});

test('QIN-580：同一帧重复合成结果一致，视频暂停重绘不会漂移', () => {
    const lut = parseCubeLut(cube('builtin_lut_cool.cube'));
    const sample = {
        width: 3,
        height: 2,
        data: Uint8ClampedArray.from([10, 20, 30, 255, 90, 120, 150, 255, 200, 210, 220, 255,
            5, 6, 7, 255, 240, 200, 160, 255, 33, 66, 99, 255]),
    };
    const first = composeLutPreview(sample, lut, {mode:'applied', strength:0.62});
    const second = composeLutPreview(sample, lut, {mode:'applied', strength:0.62});
    assert.deepEqual([...first.data], [...second.data], '同一帧同一参数必须完全一致');
});

test('QIN-580：图片与视频详情都接了舞台、工具条与事件，且保持只读', () => {
    // 两条媒体分支都要包舞台，否则只有一种素材能用。
    assert.match(manager, /return renderDetailMediaLutStage\(`<video[^`]*`, item, 'video'\);/);
    assert.match(manager, /return renderDetailMediaLutStage\(`<img[^`]*`, item, 'image'\);/);
    assert.match(manager, /if\(type === 'image' \|\| type === 'video'\) initializeDetailMediaLut\(overlay, item, type\);/);

    // 面板：打开、选 LUT、强度、三态对比、清除。
    for (const hook of [
        'data-detail-media-lut-open',
        'data-detail-media-lut-item',
        'data-detail-media-lut-strength',
        'data-detail-media-lut-compare',
        'data-detail-media-lut-clear',
    ]) {
        assert.ok(manager.includes(hook), `缺少交互挂点 ${hook}`);
    }
    assert.match(manager, /closest\?\.\('\[data-detail-media-lut-open\]'\)/);
    assert.match(manager, /closest\?\.\('\[data-detail-media-lut-item\]'\)/);
    assert.match(manager, /closest\?\.\('\[data-detail-media-lut-strength\]'\)/);

    // 视频暂停与拖动后要补一帧，保证画面与该帧单独套用一致。
    assert.match(manager, /video\.addEventListener\('seeked', scheduleDetailMediaLutDraw\)/);
    assert.match(manager, /video\.addEventListener\('pause', scheduleDetailMediaLutDraw\)/);
    // 不能每个回调都并发跑合成，必须合并补绘。
    assert.match(manager, /detailMediaLutRuntime\.pending = true/);

    // 只读预览：新增代码段不得出现写请求。
    for (const forbidden of ['POST', 'PUT', 'PATCH', 'DELETE', 'method:']) {
        assert.ok(!mediaLutSection.includes(`'${forbidden}'`), `只读预览不应出现写请求标记 ${forbidden}`);
    }
    // 唯一的外呼是按 kind 拉 LUT 清单，属于只读查询。
    assert.match(mediaLutSection, /fetch\('\/api\/asset-registry\/assets\?kind=lut[^']*', \{credentials:'same-origin'\}\)/);
    assert.equal((mediaLutSection.match(/fetch\(/g) || []).length, 1, '只应有 1 次 fetch，且为只读清单查询');
});

test('QIN-580：打开与关闭详情都整体重置状态，字面量不得丢掉 LUT 字段', () => {
    // openDetailPreview / closeDetailPreview 都用 `detailViewerState = {...}` 整体重建，
    // 没列进字面量的字段会被抹成 undefined；强度取不到值就画成 0%（看起来「选了 LUT 没反应」）。
    const resets = [...manager.matchAll(/^ {4}detailViewerState = \{/gm)].map(match => match.index);
    assert.ok(resets.length >= 2, '至少应有打开与关闭两处整体重置');
    for (const start of resets) {
        const end = manager.indexOf('\n    };', start);
        assert.ok(end > start, '找不到 detailViewerState 字面量结尾');
        const literal = manager.slice(start, end);
        for (const key of ['mediaLutId', 'mediaLutStrength', 'mediaLutCompare', 'mediaLutOpen', 'mediaLutError']) {
            assert.ok(new RegExp(`\\b${key}:`).test(literal), `状态字面量缺少 ${key}，该字段会被重置成 undefined`);
        }
    }
    // 强度必须有有限性兜底：0% 是合法取值，不能和「没取到」共用同一个回落值。
    assert.match(mediaLutSection, /const strength = detailMediaLutStrengthValue\(\);/);
    assert.match(mediaLutSection, /\n\s+strength,\n/);
    assert.ok(!/mediaLutStrength\) \|\| 0/.test(mediaLutSection), '不得用 `|| 0` 把缺失强度压成 0%');
});

test('QIN-580：样式挂点齐备，覆盖画布、徽标、面板与失败态', () => {
    for (const selector of [
        '.asset-detail-media-lut-stage',
        '.asset-detail-media-lut-canvas',
        '.asset-detail-media-lut-badge',
        '.asset-detail-media-lut-panel',
        '.asset-detail-media-lut-item',
        '.asset-detail-media-lut-error',
    ]) {
        assert.ok(styles.includes(selector), `缺少样式 ${selector}`);
    }
    // 字号必须走令牌，禁止裸 px（typography governance）。
    const styleBlock = styles.slice(
        styles.indexOf('.asset-detail-media-lut-stage {'),
        styles.indexOf('.asset-detail-thumbnail-fallback {'),
    );
    assert.ok(styleBlock.length > 0, '找不到 QIN-580 样式块');
    assert.ok(!/font-size:\s*\d+px/.test(styleBlock), '样式块内不得出现裸字号');
    assert.ok(!/font:\s*\d+\s+\d+px/.test(styleBlock), '样式块内不得出现裸字号简写');

    // 取样模块同样只用令牌，避免取样口径与渲染脱节。
    assert.ok(previewModule.includes('LUT_MEDIA_SAMPLE_MAX_WIDTH'));
});
