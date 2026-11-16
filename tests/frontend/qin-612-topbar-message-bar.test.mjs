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
 * QIN-612 / GitHub #733：全站顶栏操作/提示信息栏。
 *
 * 行为证据（GW-DoD-v1 §4.2 / 票面 AC）：
 * - 结构/箭头/配色 CSS 读真实页面与真实样式表断言；
 * - 状态机（代际、进行中保持、~4s 回空闲、乱序丢弃、1500/5000ms）**真实 import 产品主模块**
 *   workbench-shell.js，取其导出的 createEngine，用真实 setTimeout/clearTimeout/Date.now 驱动，
 *   不用源码切片 / vm 函数替身。
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

const PAGES = ['assets', 'index', 'projects', 'production', 'workshop',
    'workflow', 'collab', 'agents', 'settings', 'storyboard'];
const ARROWLESS = ['assets', 'index', 'projects', 'agents', 'collab', 'settings', 'storyboard'];
const SWITCH_PAGES = ['production', 'workshop', 'workflow'];

/* ----------------------------- AC1/2/3 结构 ----------------------------- */

test('AC1 十页顶栏中条统一形态：脉冲点 + 页名 + 分隔线 + role=status 状态节点', () => {
    for (const page of PAGES) {
        const html = read(`web/pages/${page}.html`);
        assert.match(html, /data-gw-topbar-status/, `${page} 必须有统一状态节点`);
        assert.match(html, /gw-topbar-msg-bar/, `${page} 必须有统一中条容器`);
        assert.match(html, /gw-topbar-msg-slot/, `${page} 必须有可收缩的中条外层`);
        assert.match(html, /data-gw-topbar-status[^>]*role="status"|role="status"[^>]*data-gw-topbar-status/,
            `${page} 状态节点必须带 role=status`);
        assert.match(html, /aria-live="polite"/, `${page} 状态节点必须对辅助技术可见`);
        assert.match(html, /data-gw-idle-text="[^"]+"/, `${page} 必须声明非空空闲文案`);
        assert.match(html, /animate-pulse/, `${page} 必须保留金色脉冲圆点`);
        assert.match(html, /data-gw-topbar-runner/, `${page} 状态节点需有超长滚动载体`);
        // 页名在分隔线左侧：容器内含分隔线，且状态节点位于其后。
        const barMatch = html.match(/gw-topbar-msg-bar[\s\S]{0,1600}?<\/div>\s*<\/div>/);
        assert.ok(barMatch, `${page} 应能定位中条块`);
        const bar = barMatch[0];
        const sep = bar.indexOf('w-px bg-white/15');
        const status = bar.indexOf('data-gw-topbar-status');
        assert.ok(sep >= 0 && status > sep, `${page} 状态节点必须位于分隔线右侧`);
    }
});

test('AC2 七页未绑定空箭头已删除，无空热区', () => {
    for (const page of ARROWLESS) {
        const html = read(`web/pages/${page}.html`);
        assert.doesNotMatch(html, /aria-label="上一项"/, `${page} 不得残留上一项空箭头`);
        assert.doesNotMatch(html, /aria-label="下一项"/, `${page} 不得残留下一项空箭头`);
        // 中条块内不得残留箭头按钮（页面其他区域的 chevron 图标不属本栏，不误判）。
        const barMatch = html.match(/<div class="flex items-center space-x-2 gw-topbar-msg-slot[\s\S]{0,1600}?<\/div>\s*<\/div>/);
        assert.ok(barMatch, `${page} 应能定位中条块`);
        const bar = barMatch[0];
        assert.doesNotMatch(bar, /data-lucide="chevron-left"/, `${page} 中条不得残留左箭头图标`);
        assert.doesNotMatch(bar, /data-lucide="chevron-right"/, `${page} 中条不得残留右箭头图标`);
    }
});

test('AC3 三页切项目箭头保留且绑定不变，提示文本在两箭头中间', () => {
    for (const page of SWITCH_PAGES) {
        const html = read(`web/pages/${page}.html`);
        const left = html.match(/<button[^>]*onclick="[^"]*prevProject\(\)"[^>]*>/);
        const right = html.match(/<button[^>]*onclick="[^"]*nextProject\(\)"[^>]*>/);
        assert.ok(left, `${page} 必须保留上一项目箭头与 prevProject 绑定`);
        assert.ok(right, `${page} 必须保留下一项目箭头与 nextProject 绑定`);
        const status = html.indexOf('data-gw-topbar-status');
        assert.ok(status > html.indexOf(left[0]) && status < html.indexOf(right[0]),
            `${page} 状态节点必须位于两条箭头之间`);
        // workflow 的失效绑定按 PM 裁决维持现状：只断言绑定文本未被本票改写。
        if (page === 'workflow') {
            assert.match(left[0], /WorkbenchWorkshop\.prevProject\(\)/, 'workflow 维持现状绑定');
            assert.match(right[0], /WorkbenchWorkshop\.nextProject\(\)/, 'workflow 维持现状绑定');
        }
    }
});

/* ----------------------------- AC4/5 宽度与滚动 ----------------------------- */

test('AC4 中条最短宽度常量=实测 257.578125px 向上取整，CSS 以该下限约束且不改窄', () => {
    const shell = read('web/js/core/workbench-shell.js');
    assert.match(shell, /GW_TOPBAR_MIN_WIDTH\s*=\s*258/, '最短宽度常量须为实测向上取整的 258px');
    assert.match(shell, /257\.578125/, '实现须记录实测基准宽度来源');
    const css = read('web/css/base/hardware-design-system.css');
    assert.match(css, /--gw-topbar-min-width\s*,\s*258px/, 'CSS 必须使用 258px 下限');
    assert.match(css, /\.gw-topbar-msg-slot[\s\S]{0,200}?min-width:\s*0/, '中条外层须可收缩以免溢出');
    assert.match(css, /@media \(max-width: 1320px\)[\s\S]{0,900}?\.gw-topbar-msg-bar\.is-narrow[\s\S]{0,240}?min-width:\s*0\s*!important/,
        '窄屏空隙不足最短宽时须退让到实测空隙内，不得溢出 header');
    assert.match(shell, /bar\.classList\.toggle\('is-narrow',\s*!wide\)/,
        '运行时须按实测空隙切换窄屏退让态');
    const assets = read('web/pages/assets.html');
    assert.doesNotMatch(assets, /\.asset-shell-deck-status\s*\{[^}]*150px/, '资产页 150px 省略号上限必须移除');
});

test('AC5 超长文字从右向左滚动、无省略号，滚动时长按位移计算', async () => {
    const css = read('web/css/base/hardware-design-system.css');
    assert.match(css, /@keyframes gw-topbar-marquee/, '必须有从右向左滚动动画');
    assert.match(css, /translateX\(calc\(-1 \* var\(--gw-topbar-scroll-distance/, '滚动方向为右向左位移');
    assert.match(css, /data-gw-topbar-scrolling="1"[\s\S]{0,240}?animation:/, '超长时启用滚动动画');
    assert.match(css, /--gw-topbar-scroll-distance/, '位移由实测内容宽度差驱动');
    const shell = read('web/js/core/workbench-shell.js');
    assert.match(shell, /contentWidth > viewportWidth \+ 1/, '须按内容宽与可视宽判定溢出');
    assert.doesNotMatch(shell, /text-overflow:\s*ellipsis/, '中条不得用省略号代替滚动');

    // 真实 import + 注入溢出：render 回调应报告 scrolling，并给出结束时刻供「等滚完」使用。
    const bus = await loadShellBus();
    const events = [];
    const engine = bus.createEngine({render: s => events.push(s), now: () => Date.now()});
    engine.announce('这是一条非常非常长的操作结果提示文案，足以超过顶栏中条可用空隙并触发滚动', {scrolling: true, scrollEndAt: Date.now() + 2000});
    const last = events[events.length - 1];
    assert.equal(last.scrolling, true, '超长时状态须标记滚动中');
    assert.ok(last.scrollEndAt > Date.now(), '滚动须带结束时刻，供 TTL 到点后等待');
});

/* ------------------------ AC9 分流：撤销 toast 保留 ------------------------ */

test('AC9 带撤销的底部 toast 保留在底部，不与中条重复同一句', () => {
    const controller = read('web/js/controllers/assets-controller.js');
    assert.match(controller, /function showShellOrganizeToast\(text, undoable/, '带撤销 toast 函数须保留');
    assert.match(controller, /data-asset-organize-undo-now/, '撤销操作按钮须保留在底部 toast');
    const manager = read('web/js/modules/asset-manager.js');
    assert.match(manager, /function announceTopbarStatus/, '资产页状态投影须接入统一入口');
    assert.match(manager, /bus\.announce\(value, \{tone:/, 'setStatus 文案须带 tone 走统一入口');
    // 带撤销的 toast 仍写自己的节点，不调用中条入口。
    assert.doesNotMatch(controller, /showShellOrganizeToast[\s\S]{0,400}?GWTopbarStatus\.announce/, '撤销 toast 不得同时写入中条同一句');
});

/* ------------------- 真实 import 产品模块 + 真实计时器 ------------------- */

let TREE = null;
function loadShellBus() {
    if (!TREE) {
        TREE = realpathSync(mkdtempSync(path.join(os.tmpdir(), 'gw-qin612-shell-')));
        cpSync(path.join(ROOT, 'web', 'js'), path.join(TREE, 'web', 'js'), {recursive: true});
        writeFileSync(path.join(TREE, 'package.json'), '{"type":"module"}\n', 'utf8');
        // 最小宿主：readyState=loading 阻止 IIFE 立即 init，只完成函数定义与 window 导出。
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
    // 用动态 import 加载真实产品文件；ESM 缓存实例，导出对象被 freeze，跨用例无状态耦合（状态在每个 engine 内）。
    return import(pathToFileURL(path.join(TREE, 'web/js/core/workbench-shell.js')).href)
        .then(() => globalThis.GWTopbarStatus);
}

test.after(() => {
    if (TREE) { try { rmSync(TREE, {recursive: true, force: true}); } catch (_) {} }
});

/** 真实计时器宿主：用全局 setTimeout/clearTimeout/Date.now，不注入假时钟。 */
function realHost(onRender) {
    return {
        render: onRender || (() => {}),
        now: () => Date.now(),
        setTimeout: (fn, ms) => setTimeout(fn, ms),
        clearTimeout: id => clearTimeout(id),
    };
}
const wait = ms => new Promise(resolve => setTimeout(resolve, ms));

test('AC10/1 统一入口由产品主模块导出，含 announce/settle/clear/configure 与 role=status 节点', async () => {
    const bus = await loadShellBus();
    assert.ok(bus, 'workbench-shell.js 必须导出 window.GWTopbarStatus');
    for (const name of ['announce', 'settle', 'clear', 'snapshot', 'createEngine', 'bind', 'measureWidths']) {
        assert.equal(typeof bus[name], 'function', `统一入口须暴露 ${name}`);
    }
    // configure 属于 engine 实例方法（逐页声明空闲文案），非 bus 全局方法。
    const engine = bus.createEngine(realHost());
    assert.equal(typeof engine.configure, 'function', 'engine 须暴露 configure');
    assert.equal(bus.CONSTANTS.MIN_WIDTH, 258);
    assert.equal(bus.CONSTANTS.TTL, 4000);
});

test('AC1 空闲文案可由各页 configure，默认不空白', async () => {
    const bus = await loadShellBus();
    const engine = bus.createEngine(realHost());
    assert.equal(engine.snapshot().idleText, '准备就绪');
    assert.equal(engine.snapshot().text, '准备就绪');
    engine.configure({idleText: 'AI-FLUX 引擎总线 · 读取中…'});
    assert.equal(engine.snapshot().text, 'AI-FLUX 引擎总线 · 读取中…', '空闲态文案须随 configure 更新且不为空');
});

test('AC6 进行中提示保持显示，1500ms 后未结束不回「准备就绪」', {timeout: 15000}, async () => {
    const bus = await loadShellBus();
    const engine = bus.createEngine(realHost());
    engine.announce('正在加载资源…', {phase: 'progress'});
    assert.equal(engine.snapshot().phase, 'progress');
    assert.equal(engine.snapshot().text, '正在加载资源…');
    await wait(1500);
    assert.equal(engine.snapshot().text, '正在加载资源…', '进行中提示 1500ms 后仍须保持，不得提前回空闲');
    assert.equal(engine.snapshot().phase, 'progress');
});

test('AC6 进行中提示 5000ms 内不自动回空闲（慢延迟）', {timeout: 15000}, async () => {
    const bus = await loadShellBus();
    const engine = bus.createEngine(realHost());
    engine.announce('正在建立索引…', {phase: 'progress'});
    await wait(5000);
    assert.equal(engine.snapshot().text, '正在建立索引…', '进行中提示 5000ms 后仍须保持到操作结束');
    assert.notEqual(engine.snapshot().text, '准备就绪');
});

test('AC7 新消息立即替换旧消息，不排队', async () => {
    const bus = await loadShellBus();
    const engine = bus.createEngine(realHost());
    engine.announce('旧消息');
    engine.announce('新消息');
    assert.equal(engine.snapshot().text, '新消息');
    assert.equal(engine.snapshot().generation, 2, '每次 announce 代际 +1');
});

test('AC7 乱序：被替换的进行中操作的结束文案不得写回', async () => {
    const bus = await loadShellBus();
    const engine = bus.createEngine(realHost());
    const staleToken = engine.announce('正在处理旧操作…', {phase: 'progress'});
    const freshToken = engine.announce('正在处理新操作…', {phase: 'progress'});
    const accepted = engine.settle(staleToken, '旧操作完成', {phase: 'done'});
    assert.equal(accepted, false, '旧代际 settle 必须被拒绝');
    assert.equal(engine.snapshot().text, '正在处理新操作…', '旧操作结束文案不得覆盖当前消息');
    assert.notEqual(engine.settle(freshToken, '新操作完成', {phase: 'done'}), false, '当前代际 settle 必须被接受');
    assert.equal(engine.snapshot().text, '新操作完成');
});

test('AC8 danger 提示用红字态；完成/普通提示用金色态', async () => {
    const bus = await loadShellBus();
    const engine = bus.createEngine(realHost());
    engine.announce('已将 3 项放入回收站', {tone: 'danger'});
    assert.equal(engine.snapshot().tone, 'danger', '破坏性操作须为 danger 配色态');
    engine.announce('索引完成', {tone: 'info'});
    assert.equal(engine.snapshot().tone, 'info', '普通完成须为 info 配色态');
    const manager = read('web/js/modules/asset-manager.js');
    assert.match(manager, /function topbarToneForStatus/, '须有按文案语义判定 tone 的函数');
    const css = read('web/css/base/hardware-design-system.css');
    assert.match(css, /\[data-gw-topbar-status\]\.is-danger\s*\{\s*color:\s*var\(--gw-error\)/, 'danger 须为红字 token，不得新增硬编码 hex');
    assert.match(css, /\[data-gw-topbar-status\]\s*\{[\s\S]{0,200}?color:\s*var\(--gold-light\)/, '普通态须为金色字 token');
});

test('AC6 完成提示约 4s 后回到空闲（真实计时器）', {timeout: 15000}, async () => {
    const bus = await loadShellBus();
    const engine = bus.createEngine(realHost());
    engine.configure({idleText: '准备就绪'});
    engine.announce('已将 xx 放入回收站');
    assert.equal(engine.snapshot().text, '已将 xx 放入回收站');
    await wait(4300);
    assert.equal(engine.snapshot().text, '准备就绪', '完成提示约 4s 后须回到空闲文案');
    assert.equal(engine.snapshot().phase, 'idle');
});

test('AC8/不跨页：切页 clear 立即回空闲并作废旧代际', async () => {
    const bus = await loadShellBus();
    const engine = bus.createEngine(realHost());
    const token = engine.announce('正在加载…', {phase: 'progress'});
    engine.clear();
    assert.equal(engine.snapshot().phase, 'idle');
    assert.equal(engine.snapshot().text, '准备就绪');
    assert.equal(engine.settle(token, '迟到的结束文案'), false, 'clear 后旧操作结束文案不得写回');
    const shell = read('web/js/core/workbench-shell.js');
    assert.match(shell, /topbarEngine\.clear\(\);\s*\n\s*bindTopbarStatus\(document\)/, '路由切换须 clear 后重新绑定');
});
