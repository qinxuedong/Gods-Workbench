import assert from 'node:assert/strict';
import { readAssetManagerAggregate } from './asset-manager-aggregate.mjs';
import test from 'node:test';

const source = readAssetManagerAggregate();

function functionBody(name){
    const start = source.indexOf(`function ${name}(`);
    assert.ok(start >= 0, `缺少函数：${name}`);
    const next = source.indexOf('\nfunction ', start + 1);
    const asyncNext = source.indexOf('\nasync function ', start + 1);
    const end = Math.min(...[next, asyncNext].filter(index => index > 0));
    return source.slice(start, Number.isFinite(end) ? end : undefined);
}

test('QIN-673 AC2/AC3：结构卡不关闭虚拟化，虚拟窗口按稳定卡片键差分', () => {
    const virtualization = functionBody('registryVirtualizationEnabled');
    assert.doesNotMatch(virtualization, /registryAssetStructures\.size/);
    const update = functionBody('updateRegistryVirtualWindow');
    const executable = update.replace(/\/\/[^\n]*/g, '');
    assert.doesNotMatch(executable, /grid\.innerHTML\s*=/, '不得整窗替换网格子树');
    assert.match(executable, /template\.innerHTML\s*=/, '只解析窗口模板以增量插入新卡');
    assert.match(executable, /currentCards\.get\(id\)/, '当前窗口内的同键卡片必须复用实例');
    assert.match(executable, /card\.remove\(\)/, '只移除离开窗口的卡片');
    assert.match(executable, /grid\.insertBefore\(card, reference\)/, '新卡按窗口顺序插入');
});

function buildPreviewScheduler(){
    const create = new Function(
        'applyRegistryVirtualPreviewAspect',
        'scheduleRegistryVirtualRemeasure',
        `let registryPreviewQueue = [];
        let registryPreviewActive = 0;
        let registryPreviewInteractionCount = 0;
        const registryPreviewInFlight = new Set();
        let registryPreviewQueueRevision = 0;
        const REGISTRY_PREVIEW_CONCURRENCY = 4;
        ${functionBody('cancelRegistryPreviewWorkForScopeChange')}
        ${functionBody('pumpRegistryPreviewQueue')}
        ${functionBody('activateRegistryPreviewImage')}
        ${functionBody('beginRegistryPreviewInteraction')}
        return {
            activate:activateRegistryPreviewImage,
            begin:beginRegistryPreviewInteraction,
            cancel:cancelRegistryPreviewWorkForScopeChange,
            active:() => registryPreviewActive,
            queued:() => registryPreviewQueue.length,
        };`,
    );
    return create(() => {}, () => {});
}

function makeImage(id){
    const listeners = new Map();
    return {
        id,
        isConnected:true,
        complete:false,
        dataset:{registryPreviewSrc:`/thumb/${id}.png`},
        listeners,
        _src:null,
        set src(value){ this._src = value; },
        get src(){ return this._src; },
        addEventListener(name, callback){ listeners.set(name, callback); },
        removeEventListener(name, callback){ if(listeners.get(name) === callback) listeners.delete(name); },
        removeAttribute(name){
            if(name === 'src') this._src = null;
            if(name.startsWith('data-')) delete this.dataset[name.slice(5).replace(/-([a-z])/g, (_match, letter) => letter.toUpperCase())];
        },
        closest(){ return null; },
    };
}

test('QIN-673 AC1/AC4：交互期间暂停派发，切视图取消在途图且不记失败', () => {
    const scheduler = buildPreviewScheduler();
    const image = makeImage('old-scope');
    const release = scheduler.begin();
    assert.equal(scheduler.activate(image, true), true);
    assert.equal(image.src, null, '交互优先级持有期间不派发缩略图');
    assert.equal(scheduler.queued(), 1);

    release();
    assert.equal(image.src, '/thumb/old-scope.png', '交互结束后恢复当前队列');
    assert.equal(scheduler.active(), 1);
    const lateError = image.listeners.get('error');
    scheduler.cancel();

    assert.equal(scheduler.active(), 0, '取消应立即释放并发槽位');
    assert.equal(image.src, null, '取消应中止旧图请求');
    assert.equal(image.dataset.registryPreviewSrc, '/thumb/old-scope.png', '取消项恢复为可调度状态');
    assert.equal(image.dataset.registryPreviewLoaded, 'false');
    assert.equal(image.dataset.registryPreviewQueued, 'false');
    assert.equal(image.dataset.registryPreviewFailed, undefined, '取消不得误记为失败');
    lateError?.();
    assert.equal(image.dataset.registryPreviewFailed, undefined, '迟到错误事件不得污染新代状态');
});

test('QIN-673 AC1：列表与详情 API 持有交互优先级直到结算', () => {
    const refresh = functionBody('refreshRegistryAssets');
    assert.match(refresh, /withRegistryPreviewInteraction/);
    assert.match(functionBody('prioritizedRegistryPreviewCall'), /withRegistryPreviewInteraction/);
    assert.match(functionBody('syncRegistryPreviewScope'), /cancelRegistryPreviewWorkForScopeChange\(\)/);
});
