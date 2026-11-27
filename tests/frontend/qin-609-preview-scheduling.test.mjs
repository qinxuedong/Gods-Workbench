import assert from 'node:assert/strict';
import { readAssetManagerAggregate } from './asset-manager-aggregate.mjs';
import test from 'node:test';

const source = readAssetManagerAggregate();

/** 截取具名函数的源码片段，用于断言分支内部行为。 */
function functionBody(name) {
    const start = source.indexOf(`function ${name}(`);
    assert.ok(start > 0, `未找到函数 ${name}`);
    const next = source.indexOf('\nfunction ', start + 1);
    const fallback = source.indexOf('\nasync function ', start + 1);
    const end = Math.min(...[next, fallback].filter(index => index > 0));
    return source.slice(start, Number.isFinite(end) ? end : undefined);
}

test('QIN-609 AC1：全部缩略图统一走并发队列，immediate 直加载路径已删除', () => {
    const defer = functionBody('deferRegistryPreviewThumb');
    // deferred 标记：占位符 + data-src + loaded=false + aria-busy，不允许保留真实 src 直加载。
    assert.match(defer, /setAttribute\('src', REGISTRY_PREVIEW_PLACEHOLDER\)/);
    assert.match(defer, /setAttribute\('data-registry-preview-src', source\)/);
    assert.match(defer, /setAttribute\('data-registry-preview-loaded', 'false'\)/);
    assert.doesNotMatch(defer, /if\(immediate\)/);
    // 三处调用点（列表 / 结构 / 网格）不再传 immediate 第二参数。
    assert.doesNotMatch(source, /deferRegistryPreviewThumb\([^)]*,\s*Number\(index\)/);
    // QIN-673：同源 HTTP/1.1 6 连接池至少为交互请求保留两条通道。
    assert.match(source, /const REGISTRY_PREVIEW_CONCURRENCY = 4;/);
});

test('QIN-609 AC1/AC3：IO 视口提前量收窄到 96px，settle 协议内请求收敛到视口必需集', () => {
    assert.match(source, /const REGISTRY_PREVIEW_ROOT_MARGIN = '96px 0px';/);
    assert.doesNotMatch(source, /REGISTRY_PREVIEW_ROOT_MARGIN = '200px/);
});

test('QIN-609 mutation 防线：滚轮预载提前量常量与预载语义不被静默移除', () => {
    // 把 REGISTRY_PREVIEW_SCROLL_AHEAD_COUNT 改成 0 的变异必须让本测试变红：
    // 预载 20 张是滚动体验的兜底，也是 AC2 请求级对比里「视口外请求」的来源。
    assert.match(source, /const REGISTRY_PREVIEW_SCROLL_AHEAD_COUNT = 20;/);
    // 预载激活不带 priority（走队尾 push）：与视口内插队（unshift）构成优先级对照面。
    const preloadLine = source.match(/\.slice\(0, REGISTRY_PREVIEW_SCROLL_AHEAD_COUNT\)[\s\S]{0,120}activateRegistryPreviewImage/);
    assert.ok(preloadLine, '未找到 wheel 预载激活调用');
    assert.doesNotMatch(preloadLine[0], /,\s*true\)/, 'wheel 预载不得插队');
});

test('QIN-609 运行时：视口内激活插队首，先于先入队的 wheel 预载项出队赋 src', () => {
    const buildQueue = new Function(
        'document',
        `const registryPreviewQueue = [];
        let registryPreviewActive = 0;
        let registryPreviewInteractionCount = 0;
        const registryPreviewInFlight = new Set();
        const REGISTRY_PREVIEW_CONCURRENCY = 4;
        const applyRegistryVirtualPreviewAspect = () => {};
        const scheduleRegistryVirtualRemeasure = () => {};
        ${functionBody('pumpRegistryPreviewQueue')}
        ${functionBody('activateRegistryPreviewImage')}
        return {activate: activateRegistryPreviewImage};`,
    );
    const {activate} = buildQueue({});

    const makeImg = (id) => ({
        id,
        isConnected: true,
        dataset: {registryPreviewSrc: `/thumb/${id}.png`},
        listeners: {},
        _src: null,
        set src(v){ this._src = v; },
        get src(){ return this._src; },
        addEventListener(name, fn){ this.listeners[name] = fn; },
        removeAttribute(){},
        closest(){ return null; },
    });
    // 并发上限 4：先用 4 个占位项占满槽位（load 挂起不触发），再对比两类激活的出队顺序。
    const slots = Array.from({length: 4}, (_, i) => makeImg(`slot-${i}`));
    slots.forEach(img => assert.ok(activate(img, false), '占位项出队'));
    // wheel 预载项（队尾 push）
    const pre1 = makeImg('pre1');
    const pre2 = makeImg('pre2');
    assert.ok(activate(pre1, false), '预载项队尾入队');
    assert.ok(activate(pre2, false), '预载项队尾入队');
    // 视口内项（插队首）
    const viewport = makeImg('viewport');
    assert.ok(activate(viewport, true), '视口内项插队首');
    assert.equal(viewport._src, null, '并发满时新项排队');
    // 释放一个槽位（占位项 load 完成）：队首（视口内项）先出队
    slots[0].listeners.load();
    assert.equal(viewport._src, '/thumb/viewport.png', '视口内项先于预载项出队赋 src');
    assert.equal(pre1._src, null, 'wheel 预载项仍排队');
    assert.equal(pre2._src, null, 'wheel 预载项仍排队');
});

test('QIN-609 运行时：断连项由 pump 清扫释放槽位，切目录后队列继续消化', () => {
    const buildQueue = new Function(
        'document',
        `const registryPreviewQueue = [];
        let registryPreviewActive = 0;
        let registryPreviewInteractionCount = 0;
        const registryPreviewInFlight = new Set();
        const REGISTRY_PREVIEW_CONCURRENCY = 4;
        const applyRegistryVirtualPreviewAspect = () => {};
        const scheduleRegistryVirtualRemeasure = () => {};
        ${functionBody('pumpRegistryPreviewQueue')}
        ${functionBody('activateRegistryPreviewImage')}
        return {activate: activateRegistryPreviewImage};`,
    );
    const {activate} = buildQueue({});
    const makeImg = (id) => ({
        id,
        isConnected: true,
        dataset: {registryPreviewSrc: `/thumb/${id}.png`},
        listeners: {},
        _src: null,
        set src(v){ this._src = v; },
        get src(){ return this._src; },
        addEventListener(name, fn){ this.listeners[name] = fn; },
        removeAttribute(){},
        closest(){ return null; },
    });
    // 填满 4 个槽位
    const zombies = Array.from({length: 4}, (_, i) => makeImg(`zombie-${i}`));
    zombies.forEach(img => { assert.ok(activate(img, true)); });
    zombies.forEach(img => { img.isConnected = false; });  // 模拟切目录 DOM 重建
    // 新目录的新卡激活：pump 清扫 4 个断连项后立刻消化新项
    const fresh = makeImg('fresh');
    assert.equal(activate(fresh, true), true, '新卡激活');
    assert.equal(fresh._src, '/thumb/fresh.png', '断连槽位被清扫释放，新卡立即赋 src');
    const failed = makeImg('failed');
    assert.equal(activate(failed, true), true);
    failed.listeners.error();
    assert.equal(failed.dataset.registryPreviewFailed, 'true', '失败不误标成功');
    assert.notEqual(failed.dataset.registryPreviewLoaded, 'false', '失败项不回队重试');
});

test('QIN-609 AC2：视口内激活插队首，视口外 wheel 预载保持队尾', () => {
    const activate = functionBody('activateRegistryPreviewImage');
    assert.match(activate, /function activateRegistryPreviewImage\(image, priority=false\)/);
    assert.match(activate, /if\(priority\) registryPreviewQueue\.unshift\(image\);/);
    assert.match(activate, /else registryPreviewQueue\.push\(image\);/);
    // 两个视口内激活点（fallback 可见激活 + IO 回调）必须插队。
    const fallback = functionBody('scheduleRegistryPreviewFallback');
    assert.match(fallback, /const viewportTop = Math\.max\(rootRect\.top, 0\);/);
    assert.match(fallback, /const viewportBottom = Math\.min\(rootRect\.bottom, window\.innerHeight \|\| 0\);/);
    assert.match(fallback, /if\(visible\) activateRegistryPreviewImage\(image, inVisualViewport\);/);
    const schedule = functionBody('scheduleRegistryPreviewImages');
    assert.match(schedule, /activateRegistryPreviewImage\(image, true\);/);
    // wheel 预载（视口下方提前量）不插队：保持默认 push。
    const preloadLine = source.match(/\.slice\(0, REGISTRY_PREVIEW_SCROLL_AHEAD_COUNT\)[\s\S]{0,80}activateRegistryPreviewImage/);
    assert.ok(preloadLine, '未找到 wheel 预载激活调用');
    assert.doesNotMatch(preloadLine[0], /true\);/);
});

test('QIN-609 DoD 异步失败：失败不误标成功、不阻塞后续队列', () => {
    const pump = functionBody('pumpRegistryPreviewQueue');
    // load/error 分别释放槽位并区分成败标记；失败置 failed（不重试，避免风暴）。
    assert.match(pump, /entry\.onLoad = \(\) => entry\.finish\(true\)/);
    assert.match(pump, /entry\.onError = \(\) => entry\.finish\(false\)/);
    assert.match(pump, /if\(success === false\) image\.dataset\.registryPreviewFailed = 'true';/);
    // 槽位释放先于副作用守卫：失败/断连都要继续消化队列。
    assert.match(pump, /registryPreviewActive = Math\.max\(0, registryPreviewActive - 1\);/);
    assert.match(pump, /pumpRegistryPreviewQueue\(\);/);
});

test('QIN-609 DoD 代际：断连节点跳过 aspect/remeasure 副作用，槽位照常释放', () => {
    const pump = functionBody('pumpRegistryPreviewQueue');
    // 在途登记：切目录 DOM 重建取消请求后 load/error 不触发，pump 每轮清扫断连项防槽位泄漏卡死。
    assert.match(pump, /registryPreviewInFlight/);
    assert.match(pump, /if\(!entry\.image\.isConnected\) entry\.finish\(undefined\);/);
    assert.match(pump, /if\(image\.isConnected\)\{/);
    // 切目录清队列的既有代际机制保留（revision + 队列清空）。
    const disconnect = functionBody('disconnectRegistryPreviewScheduling');
    assert.match(disconnect, /registryPreviewQueueRevision \+= 1;/);
    assert.match(disconnect, /registryPreviewQueue = \[\];/);
});

test('QIN-609：失败节点不进虚拟化缓存，避免复用坏图', () => {
    const capture = functionBody('captureRegistryPreviewImages');
    assert.match(capture, /if\(image\.dataset\.registryPreviewFailed === 'true'\) return;/);
});
