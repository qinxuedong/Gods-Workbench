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
    const start = manager.indexOf('QIN-580：参考画布素材套用 LUT 预览');
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

test('QIN-580：工具条入口 + 节点角标浮窗都接齐事件，且保持只读', () => {
    // 入口接在画布工具条，工具条里不能再有「注释文字色」控件（位置让给 LUT）。
    assert.match(manager, /\$\{renderDetailMediaLutButton\(\)\}/);
    assert.ok(!manager.includes('data-detail-annotation-color="text"'), '「文字」控件应被 LUT 入口取代');
    assert.ok(manager.includes('data-detail-annotation-color="background"'), '注释背景色应保留');

    // 两级菜单：选素材 → 选 LUT。
    for (const hook of [
        'data-detail-media-lut-menu',
        'data-detail-media-lut-target',
        'data-detail-media-lut-apply',
        'data-detail-media-lut-back',
    ]) {
        assert.ok(mediaLutSection.includes(hook), `缺少菜单挂点 ${hook}`);
    }
    // 节点角标与右侧浮窗：强度、三态对比、竖列 LUT、清除。
    for (const hook of [
        'data-detail-media-node-lut=',
        'data-detail-media-node-lut-panel=',
        'data-detail-media-node-lut-strength',
        'data-detail-media-node-lut-compare',
        'data-detail-media-node-lut-item',
        'data-detail-media-node-lut-clear',
    ]) {
        assert.ok(mediaLutSection.includes(hook), `缺少节点 LUT 挂点 ${hook}`);
    }
    // 角标/浮窗在节点内部，点击分支必须排在节点点击处理（mediaCanvasObject）之前，
    // 否则点角标会被当成「点节点」，浮窗刚开就被关掉。
    const lutBranch = manager.indexOf("closest?.('[data-detail-media-lut-menu]')");
    const nodeClickBranch = manager.indexOf("const mediaCanvasObject = event.target.closest?.('[data-detail-media-object]')");
    assert.ok(lutBranch > 0 && nodeClickBranch > 0, '找不到点击委托分支');
    assert.ok(lutBranch < nodeClickBranch, 'LUT 点击分支必须排在节点点击处理之前');

    // 视频暂停与拖动后要补一帧，保证画面与该帧单独套用一致。
    assert.match(manager, /media\.addEventListener\('seeked', repaint\)/);
    assert.match(manager, /media\.addEventListener\('pause', repaint\)/);
    assert.ok(mediaLutSection.includes('requestVideoFrameCallback'), '视频应逐帧补画');
    // 逐帧回调很密，同一节点必须串行并合并补绘。
    assert.match(mediaLutSection, /detailMediaLutDraw\.pending\.add\(key\)/);

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
        for (const key of ['mediaCanvasLut', 'mediaCanvasLutPanel', 'mediaLutMenu']) {
            assert.ok(new RegExp(`\\b${key}:`).test(literal), `状态字面量缺少 ${key}，该字段会被重置成 undefined`);
        }
    }
    // 强度必须有有限性兜底：0% 是合法取值，不能和「没取到」共用同一个回落值。
    assert.match(mediaLutSection, /function detailMediaLutStrengthOf\(entry\)\{/);
    assert.ok(!/mediaLutStrength\) \|\| 0/.test(mediaLutSection), '不得用 `|| 0` 把缺失强度压成 0%');
    assert.ok(!/Number\(entry\?\.strength\) \|\|/.test(mediaLutSection), '强度回落不能吞掉合法的 0');
});

test('QIN-580：样式挂点齐备，覆盖画布、徽标、面板与失败态', () => {
    for (const selector of [
        '.asset-detail-media-lut-canvas',
        '.asset-detail-media-lut-entry',
        '.asset-detail-media-lut-menu',
        '.asset-detail-media-lut-menu-item',
        '.asset-detail-media-node-lut',
        '.asset-detail-media-node-lut-panel',
        '.asset-detail-media-node-lut-item',
        '.asset-detail-media-node-lut-clear',
    ]) {
        assert.ok(styles.includes(selector), `缺少样式 ${selector}`);
    }
    // 字号必须走令牌，禁止裸 px（typography governance）。
    const styleBlock = styles.slice(
        styles.indexOf('.asset-detail-media-lut-canvas {'),
        styles.indexOf('.asset-detail-thumbnail-fallback {'),
    );
    assert.ok(styleBlock.length > 0, '找不到 QIN-580 样式块');
    assert.ok(!/font-size:\s*\d+px/.test(styleBlock), '样式块内不得出现裸字号');
    assert.ok(!/font:\s*\d+\s+\d+px/.test(styleBlock), '样式块内不得出现裸字号简写');

    // 取样模块同样只用令牌，避免取样口径与渲染脱节。
    assert.ok(previewModule.includes('LUT_MEDIA_SAMPLE_MAX_WIDTH'));
});

test('QIN-580：工具条下拉菜单挂到 body，不被画布外壳的 overflow 裁掉', () => {
    // 画布外壳与工具条都是 overflow:hidden，菜单渲染在工具条里会被整块裁掉，
    // 表现是「点了按钮什么都没弹出」。菜单必须固定定位挂 body，按按钮 rect 摆放。
    const buttonTemplate = manager.slice(
        manager.indexOf('function renderDetailMediaLutButton(){'),
        manager.indexOf('function renderDetailMediaLutMenu(){'),
    );
    assert.ok(buttonTemplate.length > 0, '找不到 LUT 入口渲染函数');
    assert.ok(!buttonTemplate.includes('renderDetailMediaLutMenu()'), '入口模板不得内嵌菜单，否则会被工具条裁切');

    assert.match(manager, /function detailMediaLutLayer\(\)\{/);
    assert.match(manager, /layer\.dataset\.detailMediaLutLayer = ''/);
    assert.match(manager, /document\.body\.appendChild\(layer\)/);
    assert.match(manager, /function syncDetailMediaLutMenuDom\(\)\{/);
    assert.match(manager, /function positionDetailMediaLutMenu\(\)\{/);
    // 外点收起要放过菜单自身，否则点菜单项会先被当成「点别处」而清空选中素材。
    assert.match(manager, /!event\.target\.closest\?\.\('\[data-detail-media-lut-layer\]'\)/);
    // 详情关闭时摘掉孤儿浮层。
    assert.match(manager, /document\.querySelector\('\[data-detail-media-lut-layer\]'\)\?\.remove\(\)/);

    // 菜单块本身必须是固定定位，且层级高于 .asset-lightbox（z-index:140）。
    const menuRule = styles.slice(
        styles.indexOf('.asset-detail-media-lut-menu {'),
        styles.indexOf('\n', styles.indexOf('.asset-detail-media-lut-menu {')),
    );
    assert.match(menuRule, /position:fixed/);
    const zIndex = Number((menuRule.match(/z-index:(\d+)/) || [])[1] || 0);
    const lightboxZ = Number((styles.match(/\.asset-lightbox \{[^}]*z-index:(\d+)/) || [])[1] || 0);
    assert.ok(lightboxZ > 0, '找不到 .asset-lightbox 层级');
    assert.ok(zIndex > lightboxZ, `菜单层级 ${zIndex} 必须高于详情遮罩 ${lightboxZ}`);
});

test('QIN-580：LUT 不计入资产总量，但保留顶栏导航卡（排在提示词之后）', () => {
    const statsBlock = manager.slice(
        manager.indexOf('function renderRegistryOverviewStats(){'),
        manager.indexOf('async function toggleRegistryFeature('),
    );
    assert.ok(statsBlock.length > 0, '找不到统计条渲染函数');
    // 「资产总量」口径扣掉 LUT 素材：LUT 是调色预设而非素材，既不占总量也不占占比分母。
    assert.match(statsBlock, /const lutTotal = Math\.max\(registryCategoryCount\('lut'\), registryFacetKindCount\('lut'\)\);/);
    assert.match(statsBlock, /const globalTotal = Math\.max\(0, facetTotal - lutTotal\);/);
    // LUT 只剩导航卡：不带 value（不输出计数/占比），且紧跟在「提示词」之后。
    const lutCard = "{id:'lut', name:'LUT', nav:true, icon:'palette'}";
    assert.ok(statsBlock.includes(lutCard), 'LUT 应作为导航卡出现在统计条');
    assert.doesNotMatch(statsBlock, /\{id:'lut',[^}]*\bvalue:/, 'LUT 不得作为统计数值项');
    assert.ok(
        statsBlock.indexOf("{id:'prompts', name:'提示词', nav:true") < statsBlock.indexOf(lutCard),
        'LUT 卡应排在「提示词」之后',
    );
    // nav 分支只由图标 + 名称构成，不带占比与进度条。
    const navBranch = statsBlock.slice(statsBlock.indexOf('if(stat.nav){'), statsBlock.indexOf('// 双行结构'));
    assert.ok(!/registry-stat-meter/.test(navBranch), '导航入口卡不得输出进度条');
    assert.ok(!/<em>/.test(navBranch), '导航入口卡不得输出占比');

    // 子页面：系统看板 + 机架页 + 渲染分派三处都要有 lut。
    const boardsBlock = manager.slice(
        manager.indexOf('const SYSTEM_ASSET_BOARDS = ['),
        manager.indexOf('const SCRIPT_ASSET_STORAGE_KEY'),
    );
    assert.match(boardsBlock, /\{id:'lut', name:'LUT 库', icon:'palette', system:true\}/);
    assert.match(boardsBlock, /const SYSTEM_BOARD_RACK_TABS = \[[^\]]*'lut'/);
    assert.match(manager, /else if\(activeTab === 'lut'\) renderLutManager\(\);/);
    assert.match(manager, /function renderLutManager\(\)\{/);
    assert.match(manager, /function lutLibraryAssets\(\)\{/);
    assert.match(manager, /registryAssetCategory\(item\) === 'lut'/);
    // 进入子页面按 kind=lut 取数，不占统计分类。
    assert.match(manager, /if\(id === 'lut'\)\{[\s\S]{0,400}applyRegistryFilterDefinition\(\{kind:'lut'\}\)/);
});

test('QIN-580：点空白处自动收起 LUT 浮窗，浮窗内点击不受影响', () => {
    const clickBlock = manager.slice(
        manager.indexOf("if(handleDetailMediaLutMenuClick(event)) return;"),
        manager.indexOf("const mediaCanvasNode = event.target.closest?.('[data-detail-media-canvas-node]');"),
    );
    assert.ok(clickBlock.length > 0, '找不到 LUT 点击分支');
    // 浮窗内部（含关闭/选项/对比/清除）先各自 return，落到最后的才是「点空白处」。
    const closeAt = clickBlock.indexOf("if(String(detailViewerState.mediaCanvasLutPanel || '')){ closeDetailMediaNodeLutPanel(); }");
    assert.ok(closeAt > 0, '点空白处必须收起浮窗');
    assert.ok(
        closeAt > clickBlock.indexOf('[data-detail-media-node-lut-panel]'),
        '空白处收起必须排在浮窗内点击之后，否则点浮窗会先被关掉',
    );
});

test('QIN-580：LUT 菜单与浮窗按项目设计语言改成圆角', () => {
    const rule = selector => {
        const start = styles.indexOf(`${selector} {`);
        assert.ok(start > 0, `缺少样式 ${selector}`);
        return styles.slice(start, styles.indexOf('}', start) + 1);
    };
    // 本页不引 signal-flow.css（--gw-radius-* 取不到值），沿用本页浮层口径：
    // 菜单/浮窗 8px（同 .registry-structure-context-menu），条目与控件 6px，小图标件 4px。
    assert.match(rule('.asset-detail-media-lut-menu'), /border-radius:8px/);
    assert.match(rule('.asset-detail-media-lut-menu-item'), /border-radius:6px/);
    assert.match(rule('.asset-detail-media-node-lut-panel'), /border-radius:8px/);
    assert.match(rule('.asset-detail-media-node-lut-item'), /border-radius:6px/);
    assert.match(rule('.asset-detail-media-node-lut'), /border-radius:4px/);
    assert.match(rule('.asset-detail-media-node-lut-clear'), /border-radius:6px/);
});

test('QIN-580：浮窗里换 LUT 就地更新，不整块重建（否则列表滚动位置与浮窗高度都会跳）', () => {
    const start = manager.indexOf('async function setDetailMediaNodeLut(itemId, lutId){');
    assert.ok(start > 0, '找不到换 LUT 的处理函数');
    const block = manager.slice(start, manager.indexOf('function setDetailMediaNodeLutStrength', start));
    assert.ok(!block.includes('refreshDetailMediaNodeLutDom'), '换 LUT 不得整块重建浮窗');
    assert.ok(!block.includes('positionDetailMediaNodeLutPanel'), '换 LUT 不得重新定位浮窗');
    assert.match(block, /data-detail-media-node-lut-item/);
    assert.match(block, /setDetailMediaNodeLutTitle\(key, title\)/);
    assert.match(block, /scheduleDetailMediaLutPaint\(key, \{notify:true\}\)/);
    assert.match(manager, /function setDetailMediaNodeLutTitle\(itemId, title\)\{/);
});

test('QIN-580：浮窗去掉「套用后」按钮，默认即套用后，分屏/原图再点一次回退', () => {
    const panel = manager.slice(
        manager.indexOf('function renderDetailMediaNodeLutPanel(item, entry, title, type){'),
        manager.indexOf('function refreshDetailMediaNodeLutDom(itemId){'),
    );
    assert.ok(panel.length > 0, '找不到浮窗渲染函数');
    assert.ok(!panel.includes('>套用后<'), '浮窗不再需要「套用后」按钮');
    assert.match(panel, /data-detail-media-node-lut-compare="split"/);
    assert.match(panel, /data-detail-media-node-lut-compare="original"/);

    const setter = manager.slice(
        manager.indexOf('function setDetailMediaNodeLutCompare(itemId, mode){'),
        manager.indexOf('function clearDetailMediaNodeLut(itemId){'),
    );
    assert.match(setter, /const next = detailMediaLutCompareOf\(previous\) === mode \? 'applied' : mode;/, '再点一次当前的对比模式要回到套用后');
    assert.match(setter, /syncDetailMediaLutSplit\(key\)/);
});

test('QIN-580：分屏模式在素材中间给出可拖动的对比分割线', () => {
    // 分界位置进 entry；左右切分靠 CSS 裁剪，拖动只改 clip-path，不重算像素。
    assert.match(manager, /function detailMediaLutSplitOf\(entry\)\{/);
    assert.match(manager, /function applyDetailMediaLutSplitClip\(canvas, box, ratio\)\{/);
    // 分割线只在分屏且已画出帧时出现。
    const sync = manager.slice(
        manager.indexOf('function syncDetailMediaLutSplit(itemId){'),
        manager.indexOf('/* 拖动分割线'),
    );
    assert.ok(sync.length > 0, '找不到分割线定位函数');
    assert.match(sync, /detailMediaLutCompareOf\(entry\) !== 'split' \|\| canvas\.hidden/);
    assert.match(sync, /detailMediaLutContentBox\(canvas\)/);
    assert.match(sync, /applyDetailMediaLutSplitClip\(canvas, box, ratio\)/);
    assert.match(sync, /canvas\.style\.clipPath/, '分屏裁剪必须落在 canvas 上');

    // 拖动必须实时：move 只同步分割线与裁剪，重排队整幅重绘会让每次移动都重算查表。
    const move = manager.slice(
        manager.indexOf('function moveDetailMediaLutSplit(event){'),
        manager.indexOf('function endDetailMediaLutSplitDrag(){'),
    );
    assert.ok(move.length > 0, '找不到拖动函数');
    assert.ok(!move.includes('scheduleDetailMediaLutPaint('), '拖动分割线不得触发整幅重绘');
    assert.match(move, /syncDetailMediaLutSplit\(drag\.itemId\)/);

    // 分屏时 canvas 里是整幅「套用后」结果、原图媒体保留可见，右半边才是真原图。
    const paint = manager.slice(
        manager.indexOf('async function paintDetailMediaLutNode('),
        manager.indexOf('function scheduleDetailMediaLutPaint('),
    );
    assert.match(paint, /mode:compareMode === 'original' \? 'original' : 'applied'/, '分屏不得把原图拼进合成结果');
    assert.match(paint, /media\.style\.visibility = compareMode === 'split' \? '' : 'hidden'/);

    // 拖动不能触发画布平移：pointerdown 必须排在平移之前注册，且平移本身要放过分割线。
    const splitAt = manager.indexOf("document.addEventListener('pointerdown', beginDetailMediaLutSplitDrag, true);");
    const panAt = manager.indexOf("document.addEventListener('pointerdown', beginDetailMediaCanvasPan, true);");
    assert.ok(splitAt > 0 && panAt > 0 && splitAt < panAt, '分割线的 pointerdown 必须先于画布平移注册');
    assert.match(manager, /\[data-detail-media-lut-split\]'\)\) return;/);
    assert.match(manager, /function moveDetailMediaLutSplit\(event\)\{/);
    assert.match(manager, /function endDetailMediaLutSplitDrag\(\)\{/);
    assert.match(styles, /\.asset-detail-media-lut-split \{ position:absolute;/);
    assert.match(styles, /cursor:ew-resize/);
});

test('QIN-580：已选中素材时点「套用 LUT」直达预设列表，不再重复选素材', () => {
    const helper = manager.slice(
        manager.indexOf('function detailMediaLutSelectedTarget(){'),
        manager.indexOf('async function toggleDetailMediaLutMenu(){'),
    );
    assert.ok(helper.length > 0, '缺少「已选素材」判定');
    assert.match(helper, /mediaCanvasSelectedIds/, '必须读画布真实选中态');
    assert.match(helper, /selected\.length === 1/, '只在单选时直达，多选仍让用户指定目标');

    const toggle = manager.slice(
        manager.indexOf('async function toggleDetailMediaLutMenu(){'),
        manager.indexOf('async function pickDetailMediaLutTarget('),
    );
    assert.match(toggle, /const selected = detailMediaLutSelectedTarget\(\)/);
    assert.match(toggle, /selected\s*\n?\s*\? \{open:true, step:'lut', assetId:String\(selected\.id \|\| ''\)\}/s);
    assert.match(toggle, /: \{open:true, step:'asset', assetId:''\}/);
});

test('QIN-580：LUT 抽屉不给内置项贴「内置」，滚轮归列表而不是缩放画布', () => {
    const panel = manager.slice(
        manager.indexOf('function renderDetailMediaNodeLutPanel('),
        manager.indexOf('function refreshDetailMediaNodeLutDom('),
    );
    assert.ok(panel.length > 0, '找不到抽屉渲染');
    assert.ok(!panel.includes("'内置'"), '内置 LUT 不该再标「内置」');
    assert.match(panel, /detailMediaLutIsBuiltin\(lut\) \? '' : '<small>用户导入<\/small>'/, '用户导入仍需与内置区分');

    const menu = manager.slice(
        manager.indexOf('function renderDetailMediaLutMenu(){'),
        manager.indexOf('function refreshDetailMediaLutEntryDom('),
    );
    assert.ok(!menu.includes("'内置'"), '下拉菜单同样不标「内置」');

    // 滚轮：LUT 抽屉分支必须排在画布缩放之前，且直接放行给原生滚动。
    const wheel = manager.slice(
        manager.indexOf('function navigateDetailPreviewWheel(event){'),
        manager.indexOf('function beginLightboxPan('),
    );
    const lutBranch = wheel.indexOf("closest?.('.asset-detail-media-node-lut-panel')");
    const zoomBranch = wheel.indexOf("closest?.('[data-detail-media-canvas-viewport]')");
    assert.ok(lutBranch > 0, '缺少 LUT 抽屉的滚轮分支');
    assert.ok(zoomBranch > 0, '找不到画布缩放分支');
    assert.ok(lutBranch < zoomBranch, 'LUT 抽屉必须先于画布缩放命中');
    const slice = wheel.slice(lutBranch, zoomBranch);
    assert.ok(!slice.includes('preventDefault'), '必须放行给原生滚动，别把列表滚动也拦掉');
    assert.ok(!slice.includes('adjustDetailMediaCanvasZoom'), '抽屉内滚轮不得缩放画布');
});
