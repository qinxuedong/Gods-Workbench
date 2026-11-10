/** QIN-589：Coverflow 视觉——按钮焦点静默、舞台纯深色、中心加倍与透明卡片。 */
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import test from 'node:test';

const COVERFLOW = readFileSync(new URL('../../web/js/modules/asset-manager/coverflow.js', import.meta.url), 'utf8');
const COVERFLOW_CSS = readFileSync(new URL('../../web/css/pages/asset-manager-coverflow.css', import.meta.url), 'utf8');
const VAULT_CSS = readFileSync(new URL('../../web/css/pages/asset-vault.css', import.meta.url), 'utf8');
const MANIFEST = readFileSync(new URL('./node-files.txt', import.meta.url), 'utf8');

function functionSource(source, name) {
    let start = source.indexOf(`function ${name}(`);
    if (start < 0) start = source.indexOf(`async function ${name}(`);
    assert.ok(start >= 0, `缺少函数：${name}`);
    const open = source.indexOf('{', source.indexOf(')', start));
    let depth = 0;
    for (let index = open; index < source.length; index += 1) {
        if (source[index] === '{') depth += 1;
        else if (source[index] === '}') {
            depth -= 1;
            if (depth === 0) return source.slice(start, index + 1);
        }
    }
    assert.fail(`函数未闭合：${name}`);
}

function buildLayoutSandbox() {
    return new Function('viewItem', `
        const WINDOW_RADIUS = 3;
        const FULLSCREEN_CENTER_WIDTH_SHARE = 0.62;
        const FULLSCREEN_ADJACENT_SCALE = 0.86;
        const isElement = value => Boolean(value);
        ${functionSource(COVERFLOW, 'computeFullscreenLayout')}
        ${functionSource(COVERFLOW, 'applySlidePosition')}
        return {computeFullscreenLayout, applySlidePosition};
    `)(() => ({id: 'asset-1'}));
}

function createMockView({stageW, stageH, trackW, trackH, baseWidth}) {
    const currentSlide = {offsetWidth: baseWidth};
    return {
        mode: 'fullscreen',
        items: [{id: 'asset-1', mediaKind: 'image'}],
        currentIndex: 0,
        elements: {
            stage: {clientWidth: stageW, clientHeight: stageH},
            track: {clientWidth: trackW, clientHeight: trackH, querySelector: () => currentSlide},
        },
    };
}

function createMockSlide() {
    const vars = {};
    return {
        vars,
        style: {
            setProperty(key, value) { vars[key] = value; },
        },
    };
}

test('全屏位移序列：中心 2 倍夹紧、两侧每级减半、位置按实际宽度累加', () => {
    const {computeFullscreenLayout, applySlidePosition} = buildLayoutSandbox();
    // 验收视口（左侧栏收起后的舞台宽度）：1920 窗口 → 舞台 1892×1052、轨道 1720×920、基准卡 880px。
    const view = createMockView({stageW: 1892, stageH: 1052, trackW: 1720, trackH: 920, baseWidth: 880});
    const layout = computeFullscreenLayout(view);
    const centerWidth = Math.min(880 * 2, 1892 * 0.62, 920 * 16 / 9);
    assert.equal(layout.centerScale, centerWidth / 880);
    assert.deepEqual(layout.sideScales.map(v => Number(v.toFixed(4))), [0.86, 0.43, 0.215]);
    // 中心边缘到轨道边缘放不下整段侧卡（需要 1324.4px、仅 273.48px），等比压缩：外缘逐级累加 fit·宽
    const fit = (860 - centerWidth / 2) / (880 * 0.86 * 1.75);
    const outer = [1, 2, 3].map(level => {
        const widths = [756.8, 378.4, 189.2];
        return centerWidth / 2 + fit * widths.slice(0, level).reduce((s, w) => s + w, 0);
    });
    assert.deepEqual(layout.positions.map((x, i) => Math.abs(x + [378.4, 189.2, 94.6][i] - outer[i])).every(d => d < 1e-6), true);
    // 每侧稳定露出 2 张以上：前两级外缘均越过中心边缘且未出轨道
    assert.ok(outer[0] > centerWidth / 2 && outer[0] < 860);
    assert.ok(outer[1] > outer[0] && outer[1] < 860);
    // 最外级与轨道右缘相切，不越过左右控件
    assert.ok(Math.abs(outer[2] - 860) < 1e-6);

    const slide = createMockSlide();
    applySlidePosition(slide, 0, layout);
    assert.equal(slide.vars['--gw-coverflow-scale'], String(centerWidth / 880));
    assert.equal(slide.vars['--gw-coverflow-x'], '0px');
    applySlidePosition(slide, 1, layout);
    assert.equal(slide.vars['--gw-coverflow-scale'], '0.86');
    assert.ok(Math.abs(Number(slide.vars['--gw-coverflow-x'].replace('px', '')) - layout.positions[0]) < 1e-6);
    applySlidePosition(slide, -1, layout);
    assert.equal(Number(slide.vars['--gw-coverflow-x'].replace('px', '')), -layout.positions[0]);
    applySlidePosition(slide, 2, layout);
    assert.equal(slide.vars['--gw-coverflow-scale'], '0.43');
    applySlidePosition(slide, -3, layout);
    assert.equal(slide.vars['--gw-coverflow-scale'], '0.215');
    assert.equal(slide.vars['--gw-coverflow-z'], '-282px');
    assert.equal(slide.vars['--gw-coverflow-rotate'], '45deg');
    assert.equal(slide.style.zIndex, '37');
    applySlidePosition(slide, 5, layout);
    assert.equal(slide.vars['--gw-coverflow-scale'], '0.215');
});

test('空间充裕：中心取 2 倍基准宽，两侧边缘相切无重叠', () => {
    const {computeFullscreenLayout} = buildLayoutSandbox();
    const view = createMockView({stageW: 5000, stageH: 1300, trackW: 4828, trackH: 1188, baseWidth: 880});
    const layout = computeFullscreenLayout(view);
    assert.equal(layout.centerScale, 2);
    assert.deepEqual(layout.sideScales.map(v => Number(v.toFixed(4))), [0.86, 0.43, 0.215]);
    assert.ok(Math.abs(layout.positions[0] - (880 + 378.4)) < 1e-6);
    assert.ok(Math.abs(layout.positions[1] - (880 + 756.8 + 189.2)) < 1e-6);
    assert.ok(Math.abs(layout.positions[2] - (880 + 756.8 + 378.4 + 94.6)) < 1e-6);
});

test('舞台纯深色与透明卡片（CSS/JS）', () => {
    assert.doesNotMatch(COVERFLOW_CSS, /mix-blend-mode:\s*screen/);
    assert.doesNotMatch(COVERFLOW_CSS, /gw-coverflow-backdrop|gw-coverflow-veil/);
    assert.doesNotMatch(COVERFLOW_CSS, /backdrop-filter/);
    assert.match(COVERFLOW_CSS, /\.gw-coverflow \{[^}]*background:\s*var\(--sf-content\)/);
    assert.match(COVERFLOW_CSS, /\.gw-coverflow--fullscreen \.gw-coverflow-slide img,\s*\.gw-coverflow--fullscreen \.gw-coverflow-slide video \{[^}]*background:\s*transparent/);
    assert.match(COVERFLOW_CSS, /\.gw-coverflow--embedded \.gw-coverflow-slide img,\s*\.gw-coverflow--embedded \.gw-coverflow-slide video \{[^}]*background:\s*var\(--sf-bg\)/);
    assert.match(COVERFLOW_CSS, /aspect-ratio: 16 \/ 9/);
    assert.match(COVERFLOW_CSS, /object-fit: contain/);
    assert.doesNotMatch(COVERFLOW, /gw-coverflow-backdrop|gw-coverflow-veil/);
});

test('按钮焦点静默：归还焦点但不再常亮，Tab 焦点框保留', () => {
    const closeSource = functionSource(COVERFLOW, 'closeFullscreen');
    assert.match(closeSource, /markTriggerFocusQuiet\(trigger\)/);
    assert.match(closeSource, /document\.activeElement === trigger/);
    assert.match(VAULT_CSS, /registry-coverflow-open\[data-gw-coverflow-focus-quiet\][^}]*outline:\s*none/);
    assert.match(VAULT_CSS, /registry-coverflow-open\[data-gw-coverflow-focus-quiet\][^}]*border-color:\s*var\(--vault-line\)/);
    // Tab 聚焦的可见焦点框（金色描边规则）未被移除
    assert.match(VAULT_CSS, /:is\(button,input,select\):focus-visible \{ outline: 2px solid var\(--vault-accent\)/);

    const events = {};
    const fakeDocument = {
        addEventListener(type, fn) { events[type] = fn; },
        removeEventListener() {},
    };
    const sandbox = new Function('document', `
        const FOCUS_QUIET_ATTR = 'data-gw-coverflow-focus-quiet';
        const isElement = value => Boolean(value);
        ${functionSource(COVERFLOW, 'markTriggerFocusQuiet')}
        return {markTriggerFocusQuiet};
    `)(fakeDocument);
    const calls = [];
    const trigger = {
        isConnected: true,
        setAttribute(name) { calls.push(['set', name]); },
        removeAttribute(name) { calls.push(['remove', name]); },
    };
    sandbox.markTriggerFocusQuiet(trigger);
    assert.deepEqual(calls, [['set', 'data-gw-coverflow-focus-quiet']]);
    assert.ok(events.keydown && events.pointerdown);
    events.keydown();
    assert.deepEqual(calls, [['set', 'data-gw-coverflow-focus-quiet'], ['remove', 'data-gw-coverflow-focus-quiet']]);
    events.keydown();
    events.pointerdown();
    assert.equal(calls.length, 2, '静默标记只移除一次');
});

test('嵌入式与键盘行为保留', () => {
    const {applySlidePosition} = buildLayoutSandbox();
    const slide = createMockSlide();
    applySlidePosition(slide, 1);
    assert.equal(slide.vars['--gw-coverflow-x'], '20vw');
    assert.equal(slide.vars['--gw-coverflow-scale'], '0.86');
    assert.match(COVERFLOW_CSS, /width: min\(42vw, 336px, calc\(max\(0px, var\(--gw-coverflow-embedded-height\) - 109px\) \* 16 \/ 9\)\)/);
    for (const name of ['open', 'close', 'change', 'request-edit', 'add-to-reference-canvas', 'status', 'closed']) {
        assert.match(COVERFLOW, new RegExp(`gw:asset-coverflow-${name}`));
    }
    assert.match(COVERFLOW, /event\.key === 'ArrowLeft'/);
    assert.match(COVERFLOW, /event\.key === 'Escape'/);
});

test('新测试已登记 node-files.txt', () => {
    assert.match(MANIFEST, /tests\/frontend\/qin-589-coverflow-visual\.test\.mjs/);
});
