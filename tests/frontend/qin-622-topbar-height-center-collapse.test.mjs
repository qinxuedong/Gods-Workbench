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
 * QIN-622 / GitHub #753：统一顶栏消息栏高度并水平居中，空闲 15 秒后收起、新消息再展开。
 *
 * 行为证据（GW-DoD-v1）：
 * - 真实 import 产品主模块 workbench-shell.js，驱动 createEngine / bind / measureWidths；
 * - 剧集制片基准布局（1440×900 Chromium）实测高度：42px（含边框与内边距）；
 * - 覆盖 AC1～AC11：高度统一、相对顶栏居中与窄屏退让、14000/16000/30000ms 空闲边界、
 *   1500/5000/16000/30000ms 进行中不收起、收起/展开途中新消息代际获胜、4s+跑马灯协调、
 *   三页切项目箭头独立、收起无空条无幽灵热区、prefers-reduced-motion 直接切态。
 */
import test from 'node:test';
import assert from 'node:assert/strict';
import {
    cpSync, mkdtempSync, realpathSync, readFileSync, rmSync, writeFileSync,
} from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {fileURLToPath, pathToFileURL} from 'node:url';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.resolve(HERE, '../..');
const read = rel => readFileSync(path.join(ROOT, rel), 'utf8');

const PRODUCTION_MEASURED_HEIGHT_PX = 42;
const PAGES = ['assets', 'index', 'projects', 'production', 'workshop',
    'workflow', 'collab', 'agents', 'settings', 'storyboard'];
const ARROWLESS = ['assets', 'index', 'projects', 'agents', 'collab', 'settings', 'storyboard'];
const SWITCH_PAGES = ['production', 'workshop', 'workflow'];

let TREE = null;
function loadShellBus() {
    if (!TREE) {
        TREE = realpathSync(mkdtempSync(path.join(os.tmpdir(), 'gw-qin622-shell-')));
        cpSync(path.join(ROOT, 'web', 'js'), path.join(TREE, 'web', 'js'), {recursive: true});
        writeFileSync(path.join(TREE, 'package.json'), '{"type":"module"}\n', 'utf8');
        const noop = () => {};
        const doc = {
            readyState: 'loading',
            addEventListener: noop,
            removeEventListener: noop,
            querySelector: () => null,
            querySelectorAll: () => [],
            body: {appendChild: noop},
            createElement: () => ({setAttribute: noop, appendChild: noop}),
        };
        globalThis.window = globalThis;
        globalThis.document = doc;
        globalThis.location = {href: 'https://example.invalid/static/pages/index.html', pathname: '/static/pages/index.html'};
        globalThis.localStorage = {getItem: () => null, setItem: noop, removeItem: noop};
        globalThis.addEventListener = noop;
        globalThis.innerWidth = 1440;
    }
    return import(pathToFileURL(path.join(TREE, 'web/js/core/workbench-shell.js')).href)
        .then(() => globalThis.GWTopbarStatus);
}

test.after(() => {
    if (TREE) { try { rmSync(TREE, {recursive: true, force: true}); } catch (_) {} }
});

/** 可控高精度虚拟时钟，用于精确验证 14000 / 16000 / 30000ms 及毫秒级动画边界。 */
function createVirtualClock() {
    let nowMs = 1_000_000;
    let nextId = 1;
    const queue = new Map();
    return {
        now: () => nowMs,
        timers: {
            setTimeout(fn, ms) {
                const id = nextId++;
                queue.set(id, {id, at: nowMs + Math.max(0, Number(ms) || 0), fn});
                return id;
            },
            clearTimeout(id) {
                queue.delete(id);
            },
        },
        advanceBy(ms) {
            const target = nowMs + ms;
            while (true) {
                let earliest = null;
                for (const item of queue.values()) {
                    if (item.at <= target && (!earliest || item.at < earliest.at || (item.at === earliest.at && item.id < earliest.id))) {
                        earliest = item;
                    }
                }
                if (!earliest) break;
                queue.delete(earliest.id);
                nowMs = earliest.at;
                earliest.fn();
            }
            nowMs = target;
        },
        pendingCount: () => queue.size,
    };
}

function makeMockElement({className = '', rect = null, clientWidth = 180, scrollWidth = 80, attrs = {}} = {}) {
    const classes = new Set(String(className).split(/\s+/).filter(Boolean));
    const attrMap = new Map(Object.entries(attrs));
    const styleMap = {};
    return {
        nodeType: 1,
        children: [],
        parentElement: null,
        clientWidth,
        scrollWidth,
        textContent: '',
        dataset: {},
        style: {
            ...styleMap,
            setProperty(k, v) { styleMap[k] = v; },
            removeProperty(k) { delete styleMap[k]; },
            getPropertyValue(k) { return styleMap[k] || ''; },
        },
        classList: {
            add: (...names) => names.forEach(n => classes.add(n)),
            remove: (...names) => names.forEach(n => classes.delete(n)),
            toggle: (n, force) => {
                if (force === undefined) {
                    if (classes.has(n)) classes.delete(n); else classes.add(n);
                    return classes.has(n);
                }
                if (force) classes.add(n); else classes.delete(n);
                return Boolean(force);
            },
            contains: n => classes.has(n),
        },
        setAttribute(k, v) { attrMap.set(k, String(v)); },
        getAttribute(k) { return attrMap.has(k) ? attrMap.get(k) : null; },
        hasAttribute(k) { return attrMap.has(k); },
        removeAttribute(k) { attrMap.delete(k); },
        getBoundingClientRect: () => rect || {left: 0, right: 0, width: 0, top: 0, bottom: 0, height: 42},
        closest(sel) {
            let cur = this;
            while (cur) {
                if (sel.startsWith('.') && cur.classList?.contains(sel.slice(1))) return cur;
                cur = cur.parentElement;
            }
            return null;
        },
        querySelector(sel) {
            if (sel === '[data-gw-topbar-runner]') return this._runner || null;
            return null;
        },
    };
}

function buildTopbarDom({headerWidth = 1440, leftRight = 420, rightLeft = 1040, project = false, barWidth = 312} = {}) {
    const header = makeMockElement({
        className: 'topbar-master-deck',
        rect: {left: 0, right: headerWidth, width: headerWidth, top: 0, bottom: 116, height: 116},
    });
    const upper = makeMockElement({
        className: 'master-deck-upper flex',
        rect: {left: 0, right: headerWidth, width: headerWidth, top: 0, bottom: 52, height: 52},
    });
    upper.parentElement = header;
    const leftCluster = makeMockElement({
        className: 'master-deck-upper-left',
        rect: {left: 16, right: leftRight, width: leftRight - 16, top: 4, bottom: 52, height: 48},
    });
    leftCluster.parentElement = upper;
    const slot = makeMockElement({
        className: 'gw-topbar-msg-slot',
        rect: {left: leftRight + 8, right: leftRight + 320, width: 312, top: 6, bottom: 48, height: 42},
        attrs: project ? {'data-gw-topbar-project-slot': ''} : {},
    });
    slot.parentElement = upper;
    const bar = makeMockElement({
        className: 'gw-topbar-msg-bar',
        rect: {left: leftRight + 8, right: leftRight + barWidth, width: barWidth, top: 6, bottom: 48, height: 42},
    });
    bar.parentElement = slot;
    const runner = makeMockElement({clientWidth: 80, scrollWidth: 80});
    const statusNode = makeMockElement({
        clientWidth: 160,
        scrollWidth: 80,
        attrs: {'role': 'status', 'data-gw-idle-text': '准备就绪'},
    });
    statusNode._runner = runner;
    statusNode.parentElement = bar;
    const rightCluster = makeMockElement({
        className: 'master-deck-upper-right',
        rect: {left: rightLeft, right: headerWidth - 16, width: (headerWidth - 16) - rightLeft, top: 4, bottom: 52, height: 48},
    });
    rightCluster.parentElement = upper;
    upper.children = [leftCluster, slot, rightCluster];
    const doc = {
        querySelector(sel) {
            if (sel === '.gw-topbar-msg-slot') return slot;
            if (sel === '.topbar-master-deck') return header;
            if (sel === '.topbar-master-deck .master-deck-upper') return upper;
            return null;
        },
        querySelectorAll(sel) {
            if (sel === '[data-gw-topbar-status]') return [statusNode];
            return [];
        },
    };
    return {doc, header, upper, slot, bar, statusNode};
}

test('AC1 基准高度实测常量为 42px，CSS 锁定展开态高度且深浅主题一致', async () => {
    const bus = await loadShellBus();
    assert.equal(bus.CONSTANTS.BASE_HEIGHT_PX, PRODUCTION_MEASURED_HEIGHT_PX,
        '产品模块必须记录剧集制片基准布局实测高度 42px');
    const css = read('web/css/base/hardware-design-system.css');
    assert.match(css, /--gw-topbar-msg-height:\s*42px/, 'CSS 必须声明实测 42px 基准高度变量');
    assert.match(css, /\.gw-topbar-msg-bar\s*\{[\s\S]{0,400}?height:\s*var\(--gw-topbar-msg-height,\s*42px\)/,
        '十页消息栏展开态高度必须统一绑定至 42px 基准');
    for (const page of PAGES) {
        const html = read(`web/pages/${page}.html`);
        assert.match(html, /gw-topbar-msg-bar/, `${page} 必须使用统一消息栏类`);
    }
});

test('AC2 相对整个顶栏水平居中：不对称左右仪表下宽屏居中，724 窄屏自动退让不重叠', async () => {
    const bus = await loadShellBus();
    // 模拟首页不对称仪表（左簇到 420，右簇从 960 开始）在 1440px 下居中
    const wideDom = buildTopbarDom({headerWidth: 1440, leftRight: 420, rightLeft: 960});
    globalThis.document = wideDom.doc;
    globalThis.innerWidth = 1440;
    bus.bind(wideDom.doc);
    assert.equal(wideDom.slot.classList.contains('is-centered'), true,
        '1440px 对称空隙足够时应启用 is-centered 相对顶栏水平居中');
    const wideMeasure = bus.measureWidths(wideDom.bar, wideDom.doc);
    assert.equal(wideMeasure.centered, true);
    assert.ok(wideMeasure.symAvailable >= 258, '对称可用宽应不小于最短宽度 258px');

    // 模拟 900px 同排右簇仍在 header 可视区内（leftRight=525, rightLeft=628, rightRight=876 <= 900）：
    // 真实可用空隙仅 ~95px < 258，必须判定 wide=false 并启用 is-narrow，绝不得把 gapRight 放大为 safeRight 导致压住左右仪表。
    const midDom = buildTopbarDom({headerWidth: 900, leftRight: 525, rightLeft: 628});
    globalThis.document = midDom.doc;
    globalThis.innerWidth = 900;
    bus.bind(midDom.doc);
    const midMeasure = bus.measureWidths(midDom.bar, midDom.doc);
    assert.equal(midMeasure.centered, false, '900px 对称空隙不足时不得强行居中');
    assert.equal(midMeasure.wide, false, '900px 右簇同排可见且真实空隙 < 258px 时必须判定为非宽屏');
    assert.ok(midMeasure.available <= 105 && midMeasure.available >= 80,
        `900px 可用宽度应基于真实 right.left 计得（实得 ${midMeasure.available}）`);
    assert.equal(midDom.bar.classList.contains('is-narrow'), true,
        '900px 可用宽度 < 258px 时必须为 bar 添加 is-narrow，防止 min-width: 258px 遮挡左右仪表');

    // 宽屏短文案未溢出时不误触发 is-compact，确保当前项目名与页名可见
    const shortStatusDom = buildTopbarDom({headerWidth: 1440, leftRight: 525, rightLeft: 1056});
    shortStatusDom.statusNode.clientWidth = 48;
    shortStatusDom.statusNode._runner.scrollWidth = 48;
    globalThis.document = shortStatusDom.doc;
    globalThis.innerWidth = 1440;
    bus.bind(shortStatusDom.doc);
    assert.equal(shortStatusDom.bar.classList.contains('is-compact'), false,
        '宽屏短文案未溢出时不得误触发 is-compact 隐藏页名/当前项目名');

    // 右簇左缘仍在顶栏内、右缘已溢出：必须用右簇左缘，不得退回 safeRight 把 258px 栏盖上去。
    const partialDom = buildTopbarDom({headerWidth: 900, leftRight: 525.59, rightLeft: 533.59});
    partialDom.upper.children[2].getBoundingClientRect = () => ({left: 533.59, right: 1100, width: 566.41, top: 4, bottom: 52, height: 48});
    globalThis.document = partialDom.doc;
    globalThis.innerWidth = 900;
    bus.bind(partialDom.doc);
    const partialMeasure = bus.measureWidths(partialDom.bar, partialDom.doc);
    assert.equal(partialMeasure.rightFits, true, '右簇左缘在顶栏内时不得把它当成已经离开');
    assert.equal(partialMeasure.wide, false, '真实空隙只有几个像素时不得判定为宽屏');
    assert.ok(partialMeasure.available < 40, `可用宽度应停在右簇左缘之前（实得 ${partialMeasure.available}）`);
    assert.equal(partialDom.bar.classList.contains('is-narrow'), true,
        '900px 工作流/资产页这种右簇溢出时必须启用 is-narrow');
    assert.notEqual(partialDom.bar.style.minWidth, '258px');

    // 右簇整体移出顶栏右缘时，才回退 safeRight，避免消息栏跟着跑出 header。
    const narrowDom = buildTopbarDom({headerWidth: 724, leftRight: 513, rightLeft: 800});
    narrowDom.upper.children[2].getBoundingClientRect = () => ({left: 800, right: 1048, width: 248, top: 4, bottom: 52, height: 48});
    globalThis.document = narrowDom.doc;
    globalThis.innerWidth = 724;
    bus.bind(narrowDom.doc);
    assert.equal(narrowDom.slot.classList.contains('is-centered'), false,
        '724px 窄屏不得强行居中导致与左右仪表重叠');
    assert.equal(narrowDom.bar.classList.contains('is-narrow'), true,
        '724px 窄屏应启用 is-narrow 退让态');
    assert.ok(bus.measureWidths(narrowDom.bar, narrowDom.doc).available >= 180,
        '724px 右簇已整体移出可视右缘时应回退 safeRight，保证消息栏有足够可视宽');
});

function boxesOverlap(a, b) {
    return a.left < b.right - 0.5 && b.left < a.right - 0.5;
}

function assertProjectBarGeometry(geo, {center, clusterLeft, clusterRight, page}) {
    assert.ok(geo.barWidth >= 258, `${page} 宽屏消息栏不得低于 258px，实得 ${geo.barWidth}`);
    assert.ok(Math.abs(geo.barLeft + geo.barWidth / 2 - center) < 0.6,
        `${page} 消息栏中心应落在顶栏中心 ${center}`);
    assert.ok(geo.barLeft >= clusterLeft + 8 - 0.6 && geo.barRight <= clusterRight - 8 + 0.6,
        `${page} 消息栏不得进入左右簇`);
    assert.ok(geo.titleWidth >= 48, `${page} 项目标题可辨宽度不足，实得 ${geo.titleWidth}`);
    const {bar, title, prev, next} = geo.boxes;
    for (const [name, box] of [['title', title], ['prev', prev], ['next', next]]) {
        assert.equal(boxesOverlap(bar, box), false, `${page} 消息栏与${name}重叠`);
        assert.ok(box.right - box.left > 0.5, `${page} ${name} 宽度应大于 0`);
        assert.ok(box.left >= geo.slotLeft - 0.6 && box.right <= geo.slotRight + 0.6,
            `${page} ${name} 不得越出左右簇之间的空隙`);
    }
    assert.equal(boxesOverlap(title, prev), false, `${page} 标题与左箭头重叠`);
    assert.equal(boxesOverlap(title, next), false, `${page} 标题与右箭头重叠`);
    assert.equal(geo.columns.includes('50%'), false, `${page} 不得再把消息栏列锁成 50%`);
}

test('AC10/AC12 三页宽屏消息栏保持 258px，标题与箭头不重叠', async () => {
    const css = read('web/css/base/hardware-design-system.css');
    assert.equal(css.includes('minmax(0, 50%)'), false, '居中网格不得再把中间列限制为 50%');
    assert.equal(css.includes('min(var(--gw-topbar-min-width, 258px), 100%)'), false,
        '项目栏最小宽度不得再被 100% 轨道截断');
    for (const page of SWITCH_PAGES) {
        const html = read(`web/pages/${page}.html`);
        assert.match(html, /data-gw-topbar-project-slot/, `${page} 必须走项目槽布局`);
        const barAt = html.indexOf('gw-topbar-msg-bar');
        const titleAt = html.indexOf('data-gw-topbar-project');
        assert.ok(titleAt >= 0 && titleAt < barAt, `${page} 项目标题必须位于可收起消息栏之外`);
    }

    const bus = await loadShellBus();
    // R4 复测几何：1440px 三页左簇右缘 525.23、右簇左缘 1055.64、顶栏中心 720，对称净空约 373px。
    const narrowSide = buildTopbarDom({
        headerWidth: 1440, leftRight: 525.23, rightLeft: 1055.64, project: true, barWidth: 120,
    });
    globalThis.document = narrowSide.doc;
    globalThis.innerWidth = 1440;
    const tight = bus.measureWidths(narrowSide.bar, narrowSide.doc);
    assert.ok(tight.symAvailable >= 258 && tight.symAvailable < 400, `对称净空应落在复测区间，实得 ${tight.symAvailable}`);
    const tightGeo = bus.projectGeometry(tight, 120);
    for (const page of SWITCH_PAGES) {
        assertProjectBarGeometry(tightGeo, {center: 720, clusterLeft: 525.23, clusterRight: 1055.64, page});
    }
    assert.equal(tightGeo.titleOnRight, true, '左侧剩余不足以放下标题时，标题应改到消息栏右侧');
    bus.bind(narrowSide.doc);
    assert.equal(narrowSide.slot.classList.contains('is-centered'), true);
    assert.equal(narrowSide.slot.classList.contains('is-title-trailing'), true);
    assert.equal(narrowSide.bar.style.minWidth, `${tightGeo.barWidth}px`);
    assert.equal(narrowSide.slot.style.gridTemplateColumns, tightGeo.columns);
    assert.ok(Number.parseFloat(narrowSide.bar.style.minWidth) >= 258);

    // 1920px 两侧都宽：标题留在左侧，消息栏仍然不低于 258px 且相对顶栏居中。
    const wideSide = buildTopbarDom({
        headerWidth: 1920, leftRight: 537.7, rightLeft: 1381.64, project: true, barWidth: 180,
    });
    globalThis.document = wideSide.doc;
    globalThis.innerWidth = 1920;
    const wide = bus.measureWidths(wideSide.bar, wideSide.doc);
    const wideGeo = bus.projectGeometry(wide, 180);
    for (const page of SWITCH_PAGES) {
        assertProjectBarGeometry(wideGeo, {center: 960, clusterLeft: 537.7, clusterRight: 1381.64, page});
    }
    assert.equal(wideGeo.titleOnRight, false, '两侧空间足够时标题保持在消息栏左侧');
    bus.bind(wideSide.doc);
    assert.equal(wideSide.slot.classList.contains('is-title-trailing'), false);
    assert.ok(Number.parseFloat(wideSide.bar.style.minWidth) >= 258);
});

test('AC3/AC5 空闲 15 秒收起边界（14000ms 展开 / 16000ms, 30000ms 收起）且进行中不收起', async () => {
    const bus = await loadShellBus();
    const clock = createVirtualClock();
    const events = [];
    const engine = bus.createEngine({
        render: s => events.push(s),
        now: clock.now,
        timers: clock.timers,
        prefersReducedMotion: false,
    });

    // 14000ms：仍在 15s 内，保持展开且文字可见
    clock.advanceBy(14000);
    let snap = engine.snapshot();
    assert.equal(snap.collapseState, 'expanded', '14000ms 空闲栏仍应保持展开');
    assert.equal(snap.textVisible, true, '14000ms 空闲文字仍应可见');

    // 15000ms：先隐去文字
    clock.advanceBy(1000);
    snap = engine.snapshot();
    assert.equal(snap.collapseState, 'hiding-text', '15000ms 到期须先隐去文字');
    assert.equal(snap.textVisible, false);

    // +150ms：开始底框向中心缩放收起
    clock.advanceBy(150);
    snap = engine.snapshot();
    assert.equal(snap.collapseState, 'collapsing', '文字隐去后底框应进入对称缩放收起');

    // 16000ms 与 30000ms：已完成收起
    clock.advanceBy(850);
    snap = engine.snapshot();
    assert.equal(snap.collapseState, 'collapsed', '16000ms 时应已完成收起');
    assert.equal(snap.textVisible, false);

    clock.advanceBy(14000);
    snap = engine.snapshot();
    assert.equal(snap.collapseState, 'collapsed', '30000ms 时仍应保持收起');

    // AC5：进行中操作在 1500 / 5000 / 16000 / 30000ms 均不得收起或回空闲
    engine.announce('正在生成镜头资产…', {phase: 'progress'});
    clock.advanceBy(250);
    for (const step of [1500, 3500, 11000, 14000]) {
        clock.advanceBy(step);
        snap = engine.snapshot();
        assert.equal(snap.phase, 'progress');
        assert.equal(snap.collapseState, 'expanded', '进行中操作无论多久均不得自动收起');
        assert.equal(snap.textVisible, true);
        assert.equal(snap.text, '正在生成镜头资产…');
    }
});

test('AC4 收起后或收起/展开途中到达新消息：取消旧计时与动画，反向展开后再显字，最新代际获胜', async () => {
    const bus = await loadShellBus();
    const clock = createVirtualClock();
    const engine = bus.createEngine({
        render: () => {},
        now: clock.now,
        timers: clock.timers,
        prefersReducedMotion: false,
    });

    // 推进到收起中（hiding-text / collapsing）
    clock.advanceBy(15150);
    assert.equal(engine.snapshot().collapseState, 'collapsing');

    // 收起途中到达新消息：立即取消旧收起动画，进入反向展开（文字先隐藏）
    const gen1 = engine.announce('正在校验素材…', {phase: 'progress'});
    let snap = engine.snapshot();
    assert.equal(snap.collapseState, 'expanding', '新消息到达须反向展开底框');
    assert.equal(snap.textVisible, false, '展开途中文字须先保持隐藏，展开后再显字');

    // 展开途中（100ms 后）又到达更新消息：旧展开动画作废，只采用最新代际
    clock.advanceBy(100);
    const gen2 = engine.announce('已切换为最新任务', {phase: 'progress'});
    assert.ok(gen2 > gen1);
    assert.equal(engine.snapshot().text, '已切换为最新任务');
    assert.equal(engine.snapshot().collapseState, 'expanding');

    // 旧展开剩余 150ms 到点时不应提前显字，须等新代际完整展开 250ms 后才显字
    clock.advanceBy(150);
    assert.equal(engine.snapshot().collapseState, 'expanding');
    clock.advanceBy(100);
    snap = engine.snapshot();
    assert.equal(snap.collapseState, 'expanded');
    assert.equal(snap.textVisible, true);
    assert.equal(snap.text, '已切换为最新任务');
});

test('AC6 完成提示与跑马灯协调：等 4s 且等跑马灯滚完才回空闲，之后才计 15s', async () => {
    const bus = await loadShellBus();
    const clock = createVirtualClock();
    const engine = bus.createEngine({
        render: () => {},
        now: clock.now,
        timers: clock.timers,
        prefersReducedMotion: false,
    });

    engine.announce('这是一条超长提示文案用于验证跑马灯周期结束后才回空闲并启动 15 秒计时', {
        phase: 'done',
        scrolling: true,
        scrollEndAt: clock.now() + 6500,
    });

    // 4200ms 时：TTL 已过但跑马灯尚未滚完（6500ms），不得提前回空闲或启动 15s 收起
    clock.advanceBy(4200);
    assert.equal(engine.snapshot().phase, 'done');
    assert.equal(engine.snapshot().collapseState, 'expanded');

    // 6500ms 时：跑马灯完整周期结束，回到空闲并从此时开始计 15s
    clock.advanceBy(2300);
    assert.equal(engine.snapshot().phase, 'idle');
    assert.equal(engine.snapshot().collapseState, 'expanded');

    // 再过 14000ms（累计 20500ms）：仍展开
    clock.advanceBy(14000);
    assert.equal(engine.snapshot().collapseState, 'expanded');
    assert.equal(engine.snapshot().textVisible, true);

    // 再过 2000ms（累计 22500ms，即回空闲后 16000ms）：已完成收起
    clock.advanceBy(2000);
    assert.equal(engine.snapshot().collapseState, 'collapsed');
});

test('AC7 三页切项目箭头位于消息栏胶囊外侧，收起时独立可见可点；其余七页无空箭头', () => {
    for (const page of SWITCH_PAGES) {
        const html = read(`web/pages/${page}.html`);
        const slotIdx = html.indexOf('gw-topbar-msg-slot');
        const barIdx = html.indexOf('gw-topbar-msg-bar');
        const prevIdx = html.indexOf('prevProject()');
        const nextIdx = html.indexOf('nextProject()');
        assert.ok(slotIdx >= 0 && barIdx > slotIdx, `${page} 必须存在 slot 与 bar`);
        assert.ok(prevIdx > slotIdx && prevIdx < barIdx,
            `${page} 左切项目箭头必须独立位于 .gw-topbar-msg-bar 外侧之前`);
        assert.ok(nextIdx > barIdx,
            `${page} 右切项目箭头必须独立位于 .gw-topbar-msg-bar 外侧之后`);
    }
    for (const page of ARROWLESS) {
        const html = read(`web/pages/${page}.html`);
        assert.doesNotMatch(html, /prevProject\(\)|nextProject\(\)/, `${page} 不得加回空箭头`);
    }
});

test('AC8/AC9/AC11 收起无幽灵热区、prefers-reduced-motion 直接切态、clear 重计 15s', async () => {
    const bus = await loadShellBus();
    const clock = createVirtualClock();
    const dom = buildTopbarDom({headerWidth: 1440, leftRight: 420, rightLeft: 960});
    globalThis.document = dom.doc;
    const engine = bus.createEngine({
        render: state => {
            dom.bar.dataset.gwTopbarCollapse = state.collapseState;
            dom.bar.dataset.gwTopbarTextVisible = state.textVisible ? '1' : '0';
            if (state.collapseState === 'collapsed') dom.bar.setAttribute('aria-hidden', 'true');
            else dom.bar.removeAttribute('aria-hidden');
        },
        now: clock.now,
        timers: clock.timers,
        prefersReducedMotion: true,
    });

    // AC9：reduced-motion 下 15000ms 直接一步切至 collapsed，无中间动画态
    clock.advanceBy(15000);
    assert.equal(engine.snapshot().collapseState, 'collapsed');
    assert.equal(dom.bar.dataset.gwTopbarCollapse, 'collapsed');
    assert.equal(dom.bar.getAttribute('aria-hidden'), 'true');

    // AC9：reduced-motion 下新消息直接一步切至 expanded + textVisible=true
    engine.announce('直接切态消息', {phase: 'progress'});
    assert.equal(engine.snapshot().collapseState, 'expanded');
    assert.equal(engine.snapshot().textVisible, true);
    assert.equal(dom.bar.getAttribute('aria-hidden'), null);

    // AC11：clear 后回空闲并重新计 15s
    engine.clear();
    assert.equal(engine.snapshot().phase, 'idle');
    assert.equal(engine.snapshot().collapseState, 'expanded');
    clock.advanceBy(15000);
    assert.equal(engine.snapshot().collapseState, 'collapsed');

    // AC8 CSS 规则核验：collapsed 无空条、无幽灵热区；AC9 未恢复已删除的减少动态效果开关
    const css = read('web/css/base/hardware-design-system.css');
    assert.match(css, /\.gw-topbar-msg-bar\[data-gw-topbar-collapse="collapsed"\][\s\S]{0,600}?pointer-events:\s*none/);
    assert.match(css, /\.gw-topbar-msg-bar\[data-gw-topbar-collapse="collapsed"\][\s\S]{0,600}?visibility:\s*hidden/);
    assert.match(css, /\.gw-topbar-msg-bar\[data-gw-topbar-collapse="expanded"\][\s\S]{0,240}?transform:\s*scaleX\(1\)/,
        '展开终态必须显式 scaleX(1)，不能把 transform 过渡到 none');
    assert.doesNotMatch(read('web/pages/settings.html'), /减少动态效果/, '不得恢复已删除的减少动态效果开关');
});
