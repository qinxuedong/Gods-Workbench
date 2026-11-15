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
    // 并发上限保持 8（票面建议 6–8）。
    assert.match(source, /const REGISTRY_PREVIEW_CONCURRENCY = 8;/);
});

test('QIN-609 AC1/AC3：IO 视口提前量收窄到 96px，settle 协议内请求收敛到视口必需集', () => {
    assert.match(source, /const REGISTRY_PREVIEW_ROOT_MARGIN = '96px 0px';/);
    assert.doesNotMatch(source, /REGISTRY_PREVIEW_ROOT_MARGIN = '200px/);
});

test('QIN-609 AC2：视口内激活插队首，视口外 wheel 预载保持队尾', () => {
    const activate = functionBody('activateRegistryPreviewImage');
    assert.match(activate, /function activateRegistryPreviewImage\(image, priority=false\)/);
    assert.match(activate, /if\(priority\) registryPreviewQueue\.unshift\(image\);/);
    assert.match(activate, /else registryPreviewQueue\.push\(image\);/);
    // 两个视口内激活点（fallback 可见激活 + IO 回调）必须插队。
    const fallback = functionBody('scheduleRegistryPreviewFallback');
    assert.match(fallback, /if\(visible\) activateRegistryPreviewImage\(image, true\);/);
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
    assert.match(pump, /addEventListener\('load', \(\) => finish\(true\)/);
    assert.match(pump, /addEventListener\('error', \(\) => finish\(false\)/);
    assert.match(pump, /if\(success === false\) image\.dataset\.registryPreviewFailed = 'true';/);
    // 槽位释放先于副作用守卫：失败/断连都要继续消化队列。
    assert.match(pump, /registryPreviewActive = Math\.max\(0, registryPreviewActive - 1\);/);
    assert.match(pump, /pumpRegistryPreviewQueue\(\);/);
});

test('QIN-609 DoD 代际：断连节点跳过 aspect/remeasure 副作用，槽位照常释放', () => {
    const pump = functionBody('pumpRegistryPreviewQueue');
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
