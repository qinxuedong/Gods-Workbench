/** QIN-589：Coverflow 视觉——按钮焦点静默、舞台纯深色、中心加倍与透明卡片。
 *  返工二：新增基于「投影后实际渲染矩形」的 AC5 验收（1920×1080 / 1440×900），
 *  带 trigger 的关闭集成路径、零尺寸重试与文档级监听清理用例。 */
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import test from 'node:test';
import {createContext, runInContext} from 'node:vm';

const COVERFLOW = readFileSync(new URL('../../web/js/modules/asset-manager/coverflow.js', import.meta.url), 'utf8');
const COVERFLOW_CSS = readFileSync(new URL('../../web/css/pages/asset-manager-coverflow.css', import.meta.url), 'utf8');
const VAULT_CSS = readFileSync(new URL('../../web/css/pages/asset-vault.css', import.meta.url), 'utf8');
const MANIFEST = readFileSync(new URL('./node-files.txt', import.meta.url), 'utf8');

/** 与源码解耦的验收口径（返工二）：
 *  - 「可见」= 卡片投影后渲染矩形扣除所有更高 z-index 卡片遮挡后的净外露宽度，
 *    不依赖半透明透出；每侧至少 2 张 ≥ 40px（票面 AC5 的最小可见宽度口径）。
 *  - 中心卡在 1920 验收视口下的像素宽度下限 1100px：把夹紧份额从 0.62 微调到
 *    0.56（scale 仍 >1.2）也会变红，不靠宽松的 scale 门槛。 */
const MIN_VISIBLE_STRIP_PX = 40;
const CENTER_MIN_WIDTH_PX = 1100;

/** 从源码读取模块常量声明值——测试不得自行注入与源码无关的值。 */
function readConstant(source, name) {
    const match = source.match(new RegExp(`const ${name} = ([0-9.]+);`));
    assert.ok(match, `源码缺少常量声明：${name}`);
    return Number(match[1]);
}

function readArrayConstant(source, name) {
    const match = source.match(new RegExp(`const ${name} = \\[([0-9.,\\s]+)\\];`));
    assert.ok(match, `源码缺少数组常量声明：${name}`);
    return match[1].split(',').map(value => Number(value.trim()));
}

function readStringConstant(source, name) {
    const match = source.match(new RegExp(`const ${name} = '([^']+)'`));
    assert.ok(match, `源码缺少常量声明：${name}`);
    return match[1];
}

/** CSS 的 stage perspective 是 projection 真源；测试从 CSS 读取并与源码常量核对，
 *  防止 CSS 改了透视强度而 JS 反解常量没同步（审核返工二的核心漂移风险）。 */
function readStagePerspective(css) {
    const start = css.indexOf('.gw-coverflow-stage {');
    const block = css.slice(start, css.indexOf('.gw-coverflow-track {', start));
    assert.ok(start >= 0 && block, '缺少 .gw-coverflow-stage 规则');
    const match = block.match(/perspective:\s*([0-9.]+)px/);
    assert.ok(match, '缺少 stage perspective 声明');
    return Number(match[1]);
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

/** 独立投影模型：与 CSS transform 链一致
 *  (translate3d → rotateY → scale，stage perspective)，输入为 applySlidePosition
 *  真实写出的 CSS 变量，输出相对轨道中心的屏幕矩形，等价于 getBoundingClientRect。 */
function projectSlideRect({x, z, rotateDeg, scale, baseWidth, perspective}) {
    const theta = rotateDeg * Math.PI / 180;
    const half = scale * baseWidth / 2;
    const project = (plane, depth) => plane * perspective / (perspective - depth);
    const right = project(x + half * Math.cos(theta), z - half * Math.sin(theta));
    const left = project(x - half * Math.cos(theta), z + half * Math.sin(theta));
    return {left: Math.min(left, right), right: Math.max(left, right)};
}

/** 与审核独立夹具同口径：矩形区间减去所有更高 z-index 的矩形，得净外露宽度。 */
function exposedWidth(rect, blockers) {
    let intervals = [[rect.left, rect.right]];
    for (const [bl, br] of blockers) {
        const next = [];
        for (const [l, r] of intervals) {
            if (br <= l || bl >= r) { next.push([l, r]); continue; }
            if (bl > l) next.push([l, Math.min(r, bl)]);
            if (br < r) next.push([Math.max(l, br), r]);
        }
        intervals = next;
    }
    return intervals.reduce((sum, [l, r]) => sum + Math.max(0, r - l), 0);
}

/** 最小 DOM 桩：足以执行 coverflow.js 全模块的真实打开 / 关闭路径。 */
class FakeElement {
    constructor(tag, ownerDocument = null) {
        this.tagName = String(tag || 'div').toLowerCase();
        this.ownerDocument = ownerDocument;
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
    }
    setAttribute(name, value) { this.attributes[name] = String(value); }
    getAttribute(name) { return this.attributes[name]; }
    removeAttribute(name) { delete this.attributes[name]; }
    addEventListener(type, fn) { this.__listeners.set(type, [...(this.__listeners.get(type) ?? []), fn]); }
    removeEventListener(type, fn) { this.__listeners.set(type, (this.__listeners.get(type) ?? []).filter(f => f !== fn)); }
    dispatchEvent(event) {
        for (const fn of this.__listeners.get(event.type) ?? []) fn(event);
        return true;
    }
    initClassList() {
        this.classList = {
            add: c => { if (!this.className.includes(c)) this.className = `${this.className} ${c}`.trim(); },
            remove: c => { this.className = this.className.split(/\s+/).filter(x => x && x !== c).join(' '); },
            contains: c => this.className.split(/\s+/).includes(c),
        };
    }
    createElement(tag) { return new FakeElement(tag, this.ownerDocument || this); }
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
    focus() {
        this.focusCalls += 1;
        if (this.ownerDocument) this.ownerDocument.activeElement = this;
    }
    contains() { return false; }
    get childElementCount() { return this.children.length; }
}

function matchesSimple(node, selector) {
    if (!node || node.nodeType === undefined) return false;
    return selector.split(',').map(part => part.trim()).some(part => {
        if (part === '[data-gw-coverflow-action]') return Boolean(node.dataset?.gwCoverflowAction);
        return part.split('.').filter(Boolean).every(cls => node.className.split(/\s+/).includes(cls));
    });
}

/** 构造可执行 coverflow.js 全模块的 vm 环境（最小浏览器桩）。 */
function createCoverflowRuntime({stageW = 1890, stageH = 958, trackW = 1718, trackH = 918, zeroSize = false} = {}) {
    const document = new FakeElement('document');
    document.nodeType = 9;
    document.ownerDocument = document;
    document.activeElement = null;
    const body = document.createElement('body');
    document.body = body;
    body.isConnected = true;
    const events = new Map();
    const listeners = new Map();
    const observers = [];
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
    document.addEventListener = (type, fn) => { listeners.set(type, [...(listeners.get(type) ?? []), fn]); };
    document.removeEventListener = (type, fn) => { listeners.set(type, (listeners.get(type) ?? []).filter(f => f !== fn)); };
    document.dispatchEvent = event => {
        for (const fn of listeners.get(event.type) ?? []) fn(event);
        return true;
    };
    class CustomEvent {
        constructor(type, opts) { this.type = type; this.detail = opts?.detail; }
    }
    class ResizeObserverStub {
        constructor(cb) { this.cb = cb; observers.push(this); }
        observe(element) {
            if (zeroSize) return;
            // observe 的目标即全屏 stage：在此把舞台几何置为验收视口并触发重渲染
            element.clientWidth = stageW;
            element.clientHeight = stageH;
            const track = element.querySelector('.gw-coverflow-track');
            if (track) {
                track.clientWidth = trackW;
                track.clientHeight = trackH;
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
    runInContext(COVERFLOW, context);
    return {context, document, body, window, listeners, observers, CustomEvent: context.CustomEvent};
}

/** 打开全屏 Coverflow（走真实模块 openFullscreen 路径）。 */
function openFullscreenInRuntime(runtime, extra = {}) {
    const payload = {
        mode: 'fullscreen',
        assets: Array.from({length: 7}, (_, i) => ({
            id: `asset-${i + 1}`,
            name: `素材 ${i + 1}`,
            mediaSrc: `/media/asset-${i + 1}.png`,
        })),
        currentAssetId: 'asset-1',
        ...extra,
    };
    runtime.window.dispatchEvent(new runtime.CustomEvent('gw:asset-coverflow-open', {detail: payload}));
}

const tick = () => new Promise(resolve => setTimeout(resolve, 0));

/** 布局沙盒：常量一律取自源码声明，不得在测试内另行注入。 */
function buildLayoutSandbox() {
    const names = [
        'WINDOW_RADIUS', 'FULLSCREEN_CENTER_WIDTH_SHARE', 'FULLSCREEN_ADJACENT_SCALE',
        'FULLSCREEN_PERSPECTIVE_PX', 'FULLSCREEN_ROTATE_DEG_PER_LEVEL', 'FULLSCREEN_DEPTH_PX_PER_LEVEL',
    ];
    const values = names.map(name => readConstant(COVERFLOW, name));
    const weights = readArrayConstant(COVERFLOW, 'FULLSCREEN_STRIP_WEIGHTS');
    return new Function(...names, 'FULLSCREEN_STRIP_WEIGHTS', 'viewItem', 'isElement', `
        ${functionSource(COVERFLOW, 'computeFullscreenLayout')}
        ${functionSource(COVERFLOW, 'applySlidePosition')}
        return {computeFullscreenLayout, applySlidePosition};
    `)(...values, weights, () => ({id: 'asset-1'}), () => true);
}

function createMockSlide() {
    const vars = {};
    // style 必须像真实 DOM 一样可直接赋 zIndex；桩若只支持 setProperty 会倒逼
    // 生产代码把 z-index 写到 slide.zIndex（真实 DOM 上不生效）。
    const style = {zIndex: '', setProperty(key, value) { vars[key] = value; }};
    return {vars, style};
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

/** 对给定验收视口，从真实布局函数 + 真实位移函数出发，算出投影后渲染矩形与净外露宽度。 */
function measureViewport(viewport) {
    const {computeFullscreenLayout, applySlidePosition} = buildLayoutSandbox();
    const layout = computeFullscreenLayout(createMockView(viewport));
    assert.ok(layout, '验收视口下布局不应为 null');
    const perspective = readStagePerspective(COVERFLOW_CSS);
    const slides = [-3, -2, -1, 0, 1, 2, 3].map(offset => {
        const slide = createMockSlide();
        applySlidePosition(slide, offset, layout);
        return {
            offset,
            level: Math.abs(offset),
            zIndex: Number(slide.style.zIndex),
            vars: slide.vars,
            rect: projectSlideRect({
                x: Number.parseFloat(slide.vars['--gw-coverflow-x']),
                z: Number.parseFloat(slide.vars['--gw-coverflow-z']),
                rotateDeg: Number.parseFloat(slide.vars['--gw-coverflow-rotate']),
                scale: Number(slide.vars['--gw-coverflow-scale']),
                baseWidth: viewport.baseWidth,
                perspective,
            }),
        };
    });
    const center = slides.find(slide => slide.offset === 0);
    const exposed = slides.filter(slide => slide.offset !== 0).map(slide => {
        const blockers = [center, ...slides.filter(other => other.offset !== 0 && other.zIndex > slide.zIndex)]
            .map(other => [other.rect.left, other.rect.right]);
        return {...slide, exposed: exposedWidth(slide.rect, blockers)};
    });
    return {layout, slides, center, exposed, perspective};
}

const ACCEPTANCE_VIEWPORTS = {
    '1920x1080（左栏收起）': {stageW: 1890, stageH: 958, trackW: 1718, trackH: 918, baseWidth: 880},
    '1440x900': {stageW: 1412, stageH: 872, trackW: 1240, trackH: 738, baseWidth: 835.2},
};

test('全屏位移序列：中心 2 倍夹紧、两侧每级减半、投影外缘按权重分配', () => {
    const viewport = ACCEPTANCE_VIEWPORTS['1920x1080（左栏收起）'];
    const {layout, slides, perspective} = measureViewport(viewport);
    const centerShare = readConstant(COVERFLOW, 'FULLSCREEN_CENTER_WIDTH_SHARE');
    const adjacentScale = readConstant(COVERFLOW, 'FULLSCREEN_ADJACENT_SCALE');
    const centerWidth = Math.min(880 * 2, 1890 * centerShare, 918 * 16 / 9);
    assert.equal(layout.centerScale, centerWidth / 880);
    assert.deepEqual(layout.sideScales.map(value => Number(value.toFixed(4))), [0.86, 0.43, 0.215]);
    // 与源码解耦的像素下限：份额轻微漂移（0.62→0.56，scale 仍 >1.2）也会变红。
    assert.ok(centerWidth >= CENTER_MIN_WIDTH_PX, `中心卡像素宽度 ${centerWidth} 低于验收下限`);
    assert.ok(centerWidth <= 1890 * 0.62 + 1, '中心卡不得超过舞台 62% 夹紧');
    // CSS perspective 与 JS 反解常量必须同源，防止改 CSS 忘了改布局。
    assert.equal(perspective, readConstant(COVERFLOW, 'FULLSCREEN_PERSPECTIVE_PX'));
    const weights = readArrayConstant(COVERFLOW, 'FULLSCREEN_STRIP_WEIGHTS');
    const total = weights.reduce((sum, weight) => sum + weight, 0);
    const available = 1718 / 2 - centerWidth / 2;
    let edge = centerWidth / 2;
    const expectedEdges = weights.map(weight => (edge += available * weight / total));
    layout.edges.forEach((value, index) => {
        assert.ok(Math.abs(value - expectedEdges[index]) < 1e-6, `第 ${index + 1} 级目标外缘不符`);
    });
    assert.ok(Math.abs(layout.edges[2] - 1718 / 2) < 1e-6, '最外级目标外缘与轨道右缘相切');
    // 反解出的平面位移回代投影后必须落在目标外缘（2D positions 与渲染结果一致）。
    for (let level = 1; level <= 3; level += 1) {
        const right = slides.find(slide => slide.offset === level);
        assert.ok(Math.abs(right.rect.right - layout.edges[level - 1]) < 0.01,
            `第 ${level} 级投影外缘 ${right.rect.right} 与目标 ${layout.edges[level - 1]} 不一致`);
    }
    const centerSlide = createMockSlide();
    const {applySlidePosition} = buildLayoutSandbox();
    applySlidePosition(centerSlide, 0, layout);
    assert.equal(centerSlide.vars['--gw-coverflow-scale'], String(centerWidth / 880));
    assert.equal(centerSlide.vars['--gw-coverflow-x'], '0px');
    const level1 = createMockSlide();
    applySlidePosition(level1, 1, layout);
    assert.equal(level1.vars['--gw-coverflow-scale'], String(adjacentScale));
    assert.ok(Math.abs(Number.parseFloat(level1.vars['--gw-coverflow-x']) - layout.positions[0]) < 1e-6);
    const left1 = createMockSlide();
    applySlidePosition(left1, -1, layout);
    assert.equal(Number.parseFloat(left1.vars['--gw-coverflow-x']), -layout.positions[0]);
    const level3 = createMockSlide();
    applySlidePosition(level3, -3, layout);
    assert.equal(level3.vars['--gw-coverflow-scale'], String(adjacentScale / 4));
    assert.equal(level3.vars['--gw-coverflow-z'], `${-3 * readConstant(COVERFLOW, 'FULLSCREEN_DEPTH_PX_PER_LEVEL')}px`);
    assert.equal(level3.vars['--gw-coverflow-rotate'], `${3 * readConstant(COVERFLOW, 'FULLSCREEN_ROTATE_DEG_PER_LEVEL')}deg`);
    assert.equal(level3.style.zIndex, '37');
    const far = createMockSlide();
    applySlidePosition(far, 5, layout);
    assert.equal(far.vars['--gw-coverflow-scale'], String(adjacentScale / 4));
});

test('空间充裕：最外级仍与轨道右缘相切，投影外缘严格递增', () => {
    const viewport = {stageW: 5000, stageH: 1300, trackW: 4828, trackH: 1188, baseWidth: 880};
    const {layout, slides} = measureViewport(viewport);
    const adjacentScale = readConstant(COVERFLOW, 'FULLSCREEN_ADJACENT_SCALE');
    assert.equal(layout.centerScale, 2);
    assert.deepEqual(layout.sideScales.map(value => Number(value.toFixed(4))),
        [adjacentScale, adjacentScale / 2, adjacentScale / 4]);
    assert.ok(layout.edges.every((edge, index) => index === 0 || edge > layout.edges[index - 1]), '外缘严格递增');
    assert.ok(Math.abs(layout.edges[2] - 4828 / 2) < 1e-6, '最外级与轨道右缘相切');
    for (let level = 1; level <= 3; level += 1) {
        const right = slides.find(slide => slide.offset === level);
        assert.ok(Math.abs(right.rect.right - layout.edges[level - 1]) < 0.01);
    }
});

for (const [label, viewport] of Object.entries(ACCEPTANCE_VIEWPORTS)) {
    test(`实际渲染矩形验收：${label} 每侧至少 2 张明确可见、最外缘不越轨道`, () => {
        const {layout, center, exposed} = measureViewport(viewport);
        const centerWidth = center.rect.right - center.rect.left;
        if (label.startsWith('1920')) {
            assert.ok(centerWidth >= CENTER_MIN_WIDTH_PX, `中心渲染宽度 ${centerWidth} 低于像素下限`);
        }
        for (const side of [1, -1]) {
            const levels = exposed
                .filter(slide => slide.offset * side > 0)
                .sort((a, b) => a.level - b.level);
            assert.equal(levels.length, 3, '每侧应渲染 3 张侧卡');
            assert.ok(levels[0].exposed >= MIN_VISIBLE_STRIP_PX,
                `${label} 第 1 张净外露 ${levels[0].exposed.toFixed(1)}px 低于 ${MIN_VISIBLE_STRIP_PX}px`);
            assert.ok(levels[1].exposed >= MIN_VISIBLE_STRIP_PX,
                `${label} 第 2 张净外露 ${levels[1].exposed.toFixed(1)}px 低于 ${MIN_VISIBLE_STRIP_PX}px`);
            assert.ok(levels[2].exposed > 0, `${label} 第 3 张也应存在投影条带`);
        }
        const rightEdges = exposed.filter(slide => slide.offset > 0).map(slide => slide.rect.right);
        const leftEdges = exposed.filter(slide => slide.offset < 0).map(slide => slide.rect.left);
        assert.ok(Math.max(...rightEdges) <= viewport.trackW / 2 + 0.01, '最外缘越过右侧控件');
        assert.ok(Math.min(...leftEdges) >= -viewport.trackW / 2 - 0.01, '最外缘越过左侧控件');
        // 无横向滚动：投影后所有卡片都落在轨道内。
        for (let level = 1; level <= 3; level += 1) {
            const right = exposed.find(slide => slide.offset === level);
            assert.ok(Math.abs(right.rect.right - layout.edges[level - 1]) < 0.01,
                `第 ${level} 级投影外缘与目标序列不符`);
        }
    });
}

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

test('按钮焦点静默：归还焦点但不再常亮，Tab 焦点框保留；连续登记不累积监听', () => {
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
    const listeners = {};
    const fakeDocument = {
        addEventListener(type, fn) { (listeners[type] ||= []).push(fn); },
        removeEventListener(type, fn) { listeners[type] = (listeners[type] ?? []).filter(f => f !== fn); },
    };
    const quietValue = readStringConstant(COVERFLOW, 'FOCUS_QUIET_ATTR');
    const quietState = {revert: null};
    const sandbox = new Function('document', 'FOCUS_QUIET_ATTR', 'quietFocusState', `
        ${functionSource(COVERFLOW, 'clearFocusQuiet')}
        ${functionSource(COVERFLOW, 'markTriggerFocusQuiet')}
        const isElement = value => Boolean(value);
        return {markTriggerFocusQuiet, clearFocusQuiet};
    `)(fakeDocument, quietValue, quietState);
    const calls = [];
    const trigger = {
        isConnected: true,
        setAttribute(name) { calls.push(['set', name]); },
        removeAttribute(name) { calls.push(['remove', name]); },
    };
    sandbox.markTriggerFocusQuiet(trigger);
    assert.deepEqual(calls, [['set', 'data-gw-coverflow-focus-quiet']]);
    assert.equal(listeners.keydown.length, 1);
    assert.equal(listeners.pointerdown.length, 1);
    listeners.keydown[0]();
    assert.deepEqual(calls, [['set', 'data-gw-coverflow-focus-quiet'], ['remove', 'data-gw-coverflow-focus-quiet']]);
    assert.equal(listeners.keydown.length, 0, 'revert 后文档级监听已移除');
    listeners.pointerdown[0]?.();
    assert.equal(calls.length, 2, '静默标记只移除一次');
    // 连续登记 3 次且无真实键鼠交互：旧监听必须先被清理，不得累积。
    for (let index = 0; index < 3; index += 1) {
        sandbox.markTriggerFocusQuiet(trigger);
        assert.equal(listeners.keydown.length, 1, `第 ${index + 1} 次登记后 keydown 监听应为 1`);
        assert.equal(listeners.pointerdown.length, 1, `第 ${index + 1} 次登记后 pointerdown 监听应为 1`);
    }
    sandbox.clearFocusQuiet();
    assert.equal(listeners.keydown.length, 0);
    assert.equal(listeners.pointerdown.length, 0);
});

test('真实模块：打开后卡片错开、无未捕获错误；中心卡像素宽度达标', async () => {
    const runtime = createCoverflowRuntime();
    await assert.doesNotReject(async () => {
        openFullscreenInRuntime(runtime);
        await tick();
    });
    const overlay = runtime.document.body.children.find(node => node.className.includes('gw-coverflow-overlay'));
    assert.ok(overlay, '全屏 overlay 未挂载');
    const surface = overlay.children[0];
    const stage = surface.querySelector('.gw-coverflow-stage');
    const track = stage.querySelector('.gw-coverflow-track');
    assert.ok(track, '轨道未创建');
    const slides = track.children.filter(node => node.className.includes('gw-coverflow-slide'));
    assert.equal(slides.length, 7, '窗口半径 3 应渲染 7 张卡片');
    const xs = slides.map(slide => Number(String(slide.style['--gw-coverflow-x'] ?? '0px').replace('px', '')) || 0);
    assert.ok(new Set(xs).size >= 5, `卡片应错开（实际 ${xs.length} 张、${new Set(xs).size} 个不同位置）`);
    const scales = slides.map(slide => Number(slide.style['--gw-coverflow-scale'] ?? 1));
    assert.ok(Math.max(...scales) > 1.2, '中心卡应放大');
    // 与源码解耦的像素下限：不靠 scale>1.2 的宽松门槛。
    const current = slides.find(slide => slide.className.includes('is-current'));
    const centerWidth = current.offsetWidth * Number(current.style['--gw-coverflow-scale']);
    assert.ok(centerWidth >= CENTER_MIN_WIDTH_PX, `中心卡渲染宽度 ${centerWidth} 低于 ${CENTER_MIN_WIDTH_PX}px`);
    assert.ok(scales.filter(value => Math.abs(value - 0.86) < 1e-9).length === 2, '相邻两级缩放 0.86');
    // 回归护栏：层叠顺序必须落在 style.zIndex 上。真实 DOM 里 element.zIndex 只是
    // 一个普通 JS 属性、不影响 CSS；返工二曾因测试桩把它写错，浏览器复验才发现。
    const zIndexes = slides.map(slide => slide.style.zIndex);
    assert.equal(zIndexes.filter(value => value === '40').length, 1, '中心卡 z-index 应为 40');
    assert.equal(zIndexes.filter(value => value === '39').length, 2, '相邻两张 z-index 应为 39');
    assert.equal(zIndexes.filter(value => value === '38').length, 2, '次外两张 z-index 应为 38');
    assert.equal(zIndexes.filter(value => value === '37').length, 2, '最外两张 z-index 应为 37');
    await assert.doesNotReject(async () => {
        runtime.window.dispatchEvent(new runtime.CustomEvent('gw:asset-coverflow-close', {detail: {mode: 'fullscreen'}}));
        await tick();
    });
    assert.ok(overlay.removed, '关闭后 overlay 已移除');
    assert.ok(!runtime.document.body.style.overflow, 'body 滚动锁已恢复');
});

test('真实模块：带 trigger 打开 → Esc / 关闭键 → 焦点归还 + 静默标记', async () => {
    const quietAttr = readStringConstant(COVERFLOW, 'FOCUS_QUIET_ATTR');
    for (const path of ['escape', 'close-button']) {
        const runtime = createCoverflowRuntime();
        const trigger = runtime.document.createElement('button');
        trigger.className = 'hw-btn registry-coverflow-open';
        runtime.document.body.append(trigger);
        openFullscreenInRuntime(runtime, {trigger});
        await tick();
        const overlay = runtime.document.body.children.find(node => node.className.includes('gw-coverflow-overlay'));
        assert.ok(overlay, `${path}：overlay 未挂载`);
        const surface = overlay.children[0];
        if (path === 'escape') {
            surface.dispatchEvent({
                type: 'keydown', key: 'Escape', code: 'Escape',
                ctrlKey: false, metaKey: false, altKey: false, defaultPrevented: false,
                preventDefault() {}, stopPropagation() {},
            });
        } else {
            const close = surface.querySelector('.gw-coverflow-close');
            assert.ok(close, `${path}：缺少关闭按钮`);
            // 关闭键走容器级点击委托；桩不冒泡，直接向容器派发并带 close 作为 target。
            surface.dispatchEvent({type: 'click', target: close, stopPropagation() {}, preventDefault() {}});
        }
        await tick();
        await tick();
        assert.ok(overlay.removed, `${path}：overlay 未移除`);
        assert.equal(runtime.document.activeElement, trigger, `${path}：焦点未归还触发按钮`);
        assert.ok(trigger.getAttribute(quietAttr) !== undefined, `${path}：缺少静默标记`);
        // 下一次真实键盘交互移除标记
        runtime.document.dispatchEvent({type: 'keydown', key: 'ArrowDown'});
        assert.equal(trigger.getAttribute(quietAttr), undefined, `${path}：标记未在下一次键盘交互后移除`);
    }
});

test('真实模块：连续程序化开关不累积文档级监听', async () => {
    const runtime = createCoverflowRuntime();
    const trigger = runtime.document.createElement('button');
    trigger.className = 'hw-btn registry-coverflow-open';
    runtime.document.body.append(trigger);
    for (let index = 0; index < 4; index += 1) {
        openFullscreenInRuntime(runtime, {trigger});
        await tick();
        runtime.window.dispatchEvent(new runtime.CustomEvent('gw:asset-coverflow-close', {detail: {mode: 'fullscreen'}}));
        await tick();
        await tick();
        const keydown = runtime.listeners.get('keydown') ?? [];
        const pointerdown = runtime.listeners.get('pointerdown') ?? [];
        assert.equal(keydown.length, 1, `第 ${index + 1} 轮 keydown 监听累积为 ${keydown.length}`);
        assert.equal(pointerdown.length, 1, `第 ${index + 1} 轮 pointerdown 监听累积为 ${pointerdown.length}`);
    }
});

test('零尺寸：不回落嵌入式 20vw，隐藏卡片并在尺寸就绪后重算', async () => {
    const runtime = createCoverflowRuntime({zeroSize: true});
    openFullscreenInRuntime(runtime);
    await tick();
    const overlay = runtime.document.body.children.find(node => node.className.includes('gw-coverflow-overlay'));
    const surface = overlay.children[0];
    const track = surface.querySelector('.gw-coverflow-track');
    let slides = track.children.filter(node => node.className.includes('gw-coverflow-slide'));
    assert.equal(slides.length, 7);
    assert.ok(slides.every(slide => slide.style['--gw-coverflow-x'] === undefined),
        '零尺寸时不得写入嵌入式 20vw 或任何固定步长位移');
    assert.ok(slides.every(slide => slide.style.visibility === 'hidden'), '零尺寸时卡片应隐藏待重算');
    // 尺寸观察回调恢复几何后重算，卡片按 px 落位且不再隐藏。
    const stage = surface.querySelector('.gw-coverflow-stage');
    stage.clientWidth = 1890;
    stage.clientHeight = 958;
    track.clientWidth = 1718;
    track.clientHeight = 918;
    runtime.observers[0].cb();
    await tick();
    slides = track.children.filter(node => node.className.includes('gw-coverflow-slide'));
    const xs = slides.map(slide => String(slide.style['--gw-coverflow-x'] ?? ''));
    assert.ok(xs.every(value => value.endsWith('px')), `重算后应为 px 位移：${xs.join(',')}`);
    assert.ok(new Set(xs).size >= 5, '重算后卡片应错开');
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
