/** QIN-589：Coverflow 视觉——按钮焦点静默、舞台纯深色、中心加倍与透明卡片。 */
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import test from 'node:test';
import {createContext, runInContext} from 'node:vm';

const COVERFLOW = readFileSync(new URL('../../web/js/modules/asset-manager/coverflow.js', import.meta.url), 'utf8');
const COVERFLOW_CSS = readFileSync(new URL('../../web/css/pages/asset-manager-coverflow.css', import.meta.url), 'utf8');
const VAULT_CSS = readFileSync(new URL('../../web/css/pages/asset-vault.css', import.meta.url), 'utf8');
const MANIFEST = readFileSync(new URL('./node-files.txt', import.meta.url), 'utf8');

/** 从源码读取模块常量声明值——测试不得自行注入与源码无关的值。 */
function readConstant(source, name) {
    const match = source.match(new RegExp(`const ${name} = ([0-9.]+);`));
    assert.ok(match, `源码缺少常量声明：${name}`);
    return Number(match[1]);
}

function readStringConstant(source, name) {
    const match = source.match(new RegExp(`const ${name} = '([^']+)'`));
    assert.ok(match, `源码缺少常量声明：${name}`);
    return match[1];
}

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

/** 最小 DOM 桩：足以执行 coverflow.js 全模块的真实打开 / 关闭路径。 */
class FakeElement {
    constructor(tag) {
        this.tagName = String(tag || 'div').toLowerCase();
        this.className = '';
        this.children = [];
        this.parentNode = null;
        this.dataset = {};
        this.attributes = {};
        this.style = {setProperty(key, value) { this[key] = value; }};
        this.vars = this.style;
        this.clientWidth = 0;
        this.clientHeight = 0;
        this.offsetWidth = 880;
        this.isConnected = false;
        this.nodeType = 1;
        this.__listeners = new Map();
        this.focusCalls = 0;
        this.removed = false;
        this.initClassList();
    }
    append(...nodes) {
        for (const node of nodes) {
            if (node && node.parentNode) node.parentNode.children = node.parentNode.children.filter(c => c !== node);
            node.parentNode = this;
            node.isConnected = true;
            this.children.push(node);
        }
    }
    replaceChildren(...nodes) {
        for (const child of this.children) { child.parentNode = null; child.isConnected = false; }
        this.children = [];
        this.append(...nodes);
    }
    remove() {
        this.removed = true;
        this.isConnected = false;
        if (this.parentNode) this.parentNode.children = this.parentNode.children.filter(c => c !== this);
        this.parentNode = null;
        this.isConnected = false;
    }
    setAttribute(name, value) { this.attributes[name] = String(value); }
    getAttribute(name) { return this.attributes[name]; }
    addEventListener(type, fn) { this.__listeners.set(type, [...(this.__listeners.get(type) ?? []), fn]); }
    removeEventListener(type, fn) { this.__listeners.set(type, (this.__listeners.get(type) ?? []).filter(f => f !== fn)); }
    initClassList() {
        this.classList = {
            add: c => { if (!this.className.includes(c)) this.className = `${this.className} ${c}`.trim(); },
            remove: c => { this.className = this.className.split(/\s+/).filter(x => x && x !== c).join(' '); },
            contains: c => this.className.split(/\s+/).includes(c),
        };
    }
    createElement(tag) { return new FakeElement(tag); }
    querySelector(selector) {
        for (const child of this.children) {
            if (matchesSimple(child, selector)) return child;
            const deep = child.querySelector?.(selector);
            if (deep) return deep;
        }
        return null;
    }
    querySelectorAll(selector) {
        const found = [];
        for (const child of this.children) {
            if (matchesSimple(child, selector)) found.push(child);
            found.push(...(child.querySelectorAll?.(selector) ?? []));
        }
        return found;
    }
    closest(selector) { return matchesSimple(this, selector) ? this : (this.parentNode?.closest?.(selector) ?? null); }
    focus() { this.focusCalls += 1; }
    contains() { return false; }
}

function matchesSimple(node, selector) {
    if (!node || node.nodeType === undefined) return false;
    return selector.split(',').map(part => part.trim()).some(part => {
        return part.split('.').filter(Boolean).every(cls => node.className.split(/\s+/).includes(cls));
    });
}

/** 构造可执行 coverflow.js 全模块的 vm 环境（最小浏览器桩）。 */
function createCoverflowRuntime({stageW = 1892, stageH = 1052, trackW = 1720, trackH = 920, baseWidth = 880} = {}) {
    const document = new FakeElement('document');
    document.nodeType = 9;
    const body = document.createElement('body');
    document.body = body;
    body.isConnected = true;
    const events = new Map();
    const window = {
        addEventListener(type, fn) { events.set(type, [...(events.get(type) ?? []), fn]); },
        removeEventListener(type, fn) { events.set(type, (events.get(type) ?? []).filter(f => f !== fn)); },
        dispatchEvent(event) {
            for (const fn of events.get(event.type) ?? []) fn(event);
            return true;
        },
        location: {href: 'http://127.0.0.1:2077/'},
        FloatingDismissal: null,
    };
    const listeners = new Map();
    document.addEventListener = (type, fn) => { listeners.set(type, [...(listeners.get(type) ?? []), fn]); };
    document.removeEventListener = (type, fn) => { listeners.set(type, (listeners.get(type) ?? []).filter(f => f !== fn)); };
    class CustomEvent {
        constructor(type, opts) { this.type = type; this.detail = opts?.detail; }
    }
    class ResizeObserverStub {
        constructor(cb) { this.cb = cb; }
        observe(element) {
            // observe 的目标即全屏 stage：在此把舞台几何置为验收视口并触发重渲染
            element.clientWidth = 1892;
            element.clientHeight = 1052;
            const track = element.querySelector('.gw-coverflow-track');
            if (track) {
                track.clientWidth = 1720;
                track.clientHeight = 920;
            }
            if (this.cb) this.cb();
        }
        disconnect() {}
    }
    const context = createContext({
        window,
        document,
        Element: FakeElement,
        CustomEvent,
        ResizeObserver: ResizeObserverStub,
        requestAnimationFrame: fn => setTimeout(fn, 0),
        performance,
        URL,
        console,
        setTimeout,
        clearTimeout,
    });
    const eventsRef = {events, listeners};
    runInContext(COVERFLOW, context);
    return {context, document, body, window, eventsRef, CustomEvent: context.CustomEvent};
}

/** 打开全屏 Coverflow（走真实模块 openFullscreen 路径）并返回视图句柄。 */
function openFullscreenInRuntime(runtime) {
    const payload = {
        mode: 'fullscreen',
        assets: Array.from({length: 7}, (_, i) => ({
            id: `asset-${i + 1}`,
            name: `素材 ${i + 1}`,
            mediaSrc: `/media/asset-${i + 1}.png`,
        })),
        currentAssetId: 'asset-1',
    };
    const event = new runtime.CustomEvent('gw:asset-coverflow-open', {detail: payload});
    runtime.window.dispatchEvent(event);
}

/** 布局沙盒：常量一律取自源码声明，不得在测试内另行注入。 */
function buildLayoutSandbox() {
    const centerShare = readConstant(COVERFLOW, 'FULLSCREEN_CENTER_WIDTH_SHARE');
    const adjacentScale = readConstant(COVERFLOW, 'FULLSCREEN_ADJACENT_SCALE');
    const windowRadius = readConstant(COVERFLOW, 'WINDOW_RADIUS');
    return new Function('viewItem', 'isElement', 'WINDOW_RADIUS', 'FULLSCREEN_CENTER_WIDTH_SHARE', 'FULLSCREEN_ADJACENT_SCALE', `
        ${functionSource(COVERFLOW, 'computeFullscreenLayout')}
        ${functionSource(COVERFLOW, 'applySlidePosition')}
        return {computeFullscreenLayout, applySlidePosition};
    `)(() => ({id: 'asset-1'}), () => true, windowRadius, centerShare, adjacentScale);
}

test('全屏位移序列：中心 2 倍夹紧、两侧每级减半、位置按实际宽度累加', () => {
    const {computeFullscreenLayout, applySlidePosition} = buildLayoutSandbox();
    // 验收视口（左侧栏收起后的舞台宽度）：1920 窗口 → 舞台 1892×1052、轨道 1720×920、基准卡 880px。
    const view = createMockView({stageW: 1892, stageH: 1052, trackW: 1720, trackH: 920, baseWidth: 880});
    const layout = computeFullscreenLayout(view);
    const centerShare = readConstant(COVERFLOW, 'FULLSCREEN_CENTER_WIDTH_SHARE');
    const adjacentScale = readConstant(COVERFLOW, 'FULLSCREEN_ADJACENT_SCALE');
    const centerWidth = Math.min(880 * 2, 1892 * centerShare, 920 * 16 / 9);
    assert.equal(layout.centerScale, centerWidth / 880);
    assert.deepEqual(layout.sideScales.map(v => Number(v.toFixed(4))), [0.86, 0.43, 0.215]);
    const fit = (860 - centerWidth / 2) / (880 * adjacentScale * 1.75);
    const widths = [880 * adjacentScale, 880 * adjacentScale / 2, 880 * adjacentScale / 4];
    const outer = [1, 2, 3].map(level => centerWidth / 2 + fit * widths.slice(0, level).reduce((s, w) => s + w, 0));
    const halves = widths.map(w => w / 2);
    assert.ok(layout.positions.every((x, i) => Math.abs((x + halves[i]) - outer[i]) < 1e-6), '外缘按压缩宽度累加');
    assert.ok(outer[0] > centerWidth / 2 && outer[0] < 860, '相邻卡外缘越过中心边缘且未出轨道');
    assert.ok(outer[1] > outer[0] && outer[1] < 860, '第二级外缘仍在轨道内');
    assert.ok(Math.abs(outer[2] - 860) < 1e-6, '最外级与轨道右缘相切，不越过左右控件');

    const slide = createMockSlide();
    applySlidePosition(slide, 0, layout);
    assert.equal(slide.vars['--gw-coverflow-scale'], String(centerWidth / 880));
    assert.equal(slide.vars['--gw-coverflow-x'], '0px');
    const slide2 = createMockSlide();
    applySlidePosition(slide, 1, layout);
    assert.equal(slide.vars['--gw-coverflow-scale'], String(adjacentScale));
    assert.ok(Math.abs(Number(slide.vars['--gw-coverflow-x'].replace('px', '')) - layout.positions[0]) < 1e-6);
    applySlidePosition(slide2, -1, layout);
    assert.equal(Number(slide2.vars['--gw-coverflow-x'].replace('px', '')), -layout.positions[0]);
    applySlidePosition(slide2, 2, layout);
    assert.equal(slide2.vars['--gw-coverflow-scale'], String(adjacentScale / 2));
    applySlidePosition(slide2, -3, layout);
    assert.equal(slide2.vars['--gw-coverflow-scale'], String(adjacentScale / 4));
    assert.equal(slide2.vars['--gw-coverflow-z'], '-282px');
    assert.equal(slide2.vars['--gw-coverflow-rotate'], '45deg');
    assert.equal(slide2.style.zIndex, '37');
    applySlidePosition(slide2, 5, layout);
    assert.equal(slide2.vars['--gw-coverflow-scale'], String(adjacentScale / 4));
});

function createMockSlide() {
    const vars = {};
    return {
        vars,
        style: {
            setProperty(key, value) { vars[key] = value; },
        },
    };
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

test('空间充裕：中心取 2 倍基准宽，两侧边缘相切无重叠', () => {
    const {computeFullscreenLayout} = buildLayoutSandbox();
    const view = createMockView({stageW: 5000, stageH: 1300, trackW: 4828, trackH: 1188, baseWidth: 880});
    const layout = computeFullscreenLayout(view);
    const adjacentScale = readConstant(COVERFLOW, 'FULLSCREEN_ADJACENT_SCALE');
    assert.equal(layout.centerScale, 2);
    assert.deepEqual(layout.sideScales.map(v => Number(v.toFixed(4))), [adjacentScale, adjacentScale / 2, adjacentScale / 4]);
    const w1 = 880 * adjacentScale;
    assert.ok(Math.abs(layout.positions[0] - (880 + w1 / 2)) < 1e-6);
    assert.ok(Math.abs(layout.positions[1] - (880 + w1 + w1 / 4)) < 1e-6);
    assert.ok(Math.abs(layout.positions[2] - (880 + w1 + w1 / 2 + w1 / 8)) < 1e-6);
});

test('舞台纯深色与透明卡片：全屏固定暗房深色，不随主题翻转', () => {
    assert.doesNotMatch(COVERFLOW_CSS, /mix-blend-mode:\s*screen/);
    assert.doesNotMatch(COVERFLOW_CSS, /gw-coverflow-backdrop|gw-coverflow-veil/);
    // 嵌入式保持原样：基础规则仍为半透明面板 + blur
    assert.match(COVERFLOW_CSS, /\.gw-coverflow \{[^}]*background:\s*color-mix\(in srgb, var\(--sf-content\) 58%, transparent\);\s*backdrop-filter: blur\(8px\);/);
    // 全屏与 overlay 固定暗房深色（字面值，不随主题翻转）
    assert.match(COVERFLOW_CSS, /\.gw-coverflow--fullscreen \{[^}]*background:\s*#111d27/);
    assert.match(COVERFLOW_CSS, /\.gw-coverflow--fullscreen \{[^}]*backdrop-filter:\s*none/);
    assert.match(COVERFLOW_CSS, /\.gw-coverflow-overlay \{[^}]*background:\s*#0a1016/);
    assert.match(COVERFLOW_CSS, /\.gw-coverflow--fullscreen \.gw-coverflow-header,\s*\.gw-coverflow--fullscreen \.gw-coverflow-caption \{[^}]*background:\s*#182532/);
    // 全屏固定深色块内不引用主题面变量
    const fixedBlock = COVERFLOW_CSS.slice(COVERFLOW_CSS.indexOf('QIN-589：全屏舞台固定暗房深色'), COVERFLOW_CSS.indexOf('.gw-coverflow-stage {'));
    assert.ok(fixedBlock, '缺少全屏固定深色块');
    assert.doesNotMatch(fixedBlock, /var\(--sf-content\)|var\(--sf-raised\)|var\(--sf-bg\)|var\(--sf-text\)|var\(--sf-muted\)/);
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
    assert.match(VAULT_CSS, /:is\(button,input,select\):focus-visible \{ outline: 2px solid var\(--vault-accent\)/);
    // 静默态不覆写文字色——与相邻普通按钮常态完全一致
    const quietStart = VAULT_CSS.indexOf('registry-coverflow-open[data-gw-coverflow-focus-quiet]');
    const quietRule = VAULT_CSS.slice(quietStart, VAULT_CSS.indexOf('}', quietStart) + 1);
    assert.ok(quietRule, '缺少静默态规则');
    assert.doesNotMatch(quietRule, /^\s+color:/m);
    const events = {};
    const fakeDocument = {
        addEventListener(type, fn) { events[type] = fn; },
        removeEventListener() {},
    };
    const quietValue = readStringConstant(COVERFLOW, 'FOCUS_QUIET_ATTR');
    const sandbox = new Function('document', 'FOCUS_QUIET_ATTR', `
        ${functionSource(COVERFLOW, 'markTriggerFocusQuiet')}
        ${functionSource(COVERFLOW, 'markTriggerFocusQuiet')}
        const isElement = value => Boolean(value);
        return {markTriggerFocusQuiet};
    `)(fakeDocument, quietValue);
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

test('真实模块：打开后卡片错开、无未捕获错误；关闭后清理无错误', async () => {
    const runtime = createCoverflowRuntime();
    const baseWidth = 880;
    await assert.doesNotReject(async () => {
        openFullscreenInRuntime(runtime);
        await new Promise(resolve => setTimeout(resolve, 0));
    });
    // 全屏视图已挂到 body，且 7 张卡片已按新布局错开
    const overlay = runtime.document.body.children.find(node => node.className.includes('gw-coverflow-overlay'));
    assert.ok(overlay, '全屏 overlay 未挂载');
    const surface = overlay.children[0];
    const stage = surface.querySelector('.gw-coverflow-stage');
    const track = stage.querySelector('.gw-coverflow-track');
    assert.ok(track, '轨道未创建');
    const slides = track.children.filter(node => node.className.includes('gw-coverflow-slide'));
    assert.equal(slides.length, 7, '窗口半径 3 应渲染 7 张卡片');
    stage.clientWidth = 1892;
    stage.clientHeight = 1052;
    track.clientWidth = 1720;
    track.clientHeight = 920;
    slides[0].offsetWidth = 880;
    const xs = slides.map(s => Number(String(s.style['--gw-coverflow-x'] ?? '0px').replace('px', '')) || 0);
    assert.ok(new Set(xs).size >= 5, `卡片应错开（实际 ${xs.length} 张、${new Set(xs).size} 个不同位置）`);
    const scales = slides.map(s => Number(s.style['--gw-coverflow-scale'] ?? 1));
    assert.ok(Math.max(...scales) > 1.2, '中心卡应放大（2 倍目标夹紧后 >1.2）');
    assert.ok(scales.filter(v => Math.abs(v - 0.86) < 1e-9).length === 2, '相邻两级缩放 0.86');
    // 关闭：走真实模块 closeFullscreen 路径
    await assert.doesNotReject(async () => {
        runtime.window.dispatchEvent(new runtime.CustomEvent('gw:asset-coverflow-close', {detail: {mode: 'fullscreen'}}));
        await new Promise(resolve => setTimeout(resolve, 0));
    });
    assert.ok(overlay.removed, '关闭后 overlay 已移除');
    assert.ok(!runtime.document.body.style.overflow, 'body 滚动锁已恢复');
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
