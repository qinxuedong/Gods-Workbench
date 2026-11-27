// QIN-675：治理建议按需加载、刷新/列表切换取消与延后网关超时取消回归。

import assert from 'node:assert/strict';
import test, { after, before } from 'node:test';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import {
    createAssetViewHost,
    dropTree,
    installHost,
    loadProduct,
    makeProductTree,
    wait,
} from './qin-602-asset-view-harness.mjs';

const REAL_SET_INTERVAL = globalThis.setInterval;
const ACTIVE_INTERVALS = new Set();

before(() => {
    globalThis.setInterval = (...args) => {
        const handle = REAL_SET_INTERVAL(...args);
        handle?.unref?.();
        ACTIVE_INTERVALS.add(handle);
        return handle;
    };
});

after(() => {
    for (const handle of ACTIVE_INTERVALS) clearInterval(handle);
    ACTIVE_INTERVALS.clear();
    globalThis.setInterval = REAL_SET_INTERVAL;
});

function response(data, status = 200) {
    return {
        status,
        ok: status >= 200 && status < 300,
        headers: { get: key => String(key).toLowerCase() === 'content-type' ? 'application/json' : null },
        json: async () => data,
        text: async () => JSON.stringify(data),
    };
}

function suggestionsResponse() {
    return response({
        revision: 1,
        data_status: 'ok',
        data_gaps: [],
        counts: {},
        dedup_summary: { confirmed_groups: 0, suspected_groups: 0 },
        suggestions: {
            naming: [], similar_names: [], duplicate_files: [], duplicate_shots: [],
            vector_duplicates: [], tagging: [],
        },
    });
}

function deferred() {
    let resolve;
    const promise = new Promise(done => { resolve = done; });
    return { promise, resolve };
}

function stateModuleUrl(tree) {
    return pathToFileURL(path.join(tree, 'web/js/modules/asset-manager/state.js')).href;
}

test('首屏无治理建议请求，只有打开治理抽屉才 GET', async () => {
    const host = await createAssetViewHost({
        embedded: true,
        tab: 'registry',
        fetchImpl: url => String(url).includes('/api/asset-registry/governance/suggestions')
            ? Promise.resolve(suggestionsResponse())
            : Promise.resolve(response({ items: [], total: 0, limit: 120, offset: 0, data_status: 'ok' })),
    });
    try {
        await wait(30);
        const governanceGets = () => host.requests.filter(item =>
            item.method === 'GET' && item.url.endsWith('/api/asset-registry/governance/suggestions')
        );
        assert.equal(governanceGets().length, 0, '启动与自动调度不得读取治理建议');

        await import(pathToFileURL(path.join(host.tree, 'web/js/modules/asset-manager/governance.js')).href);
        const { GW_DEFERRED } = await import(stateModuleUrl(host.tree));
        await GW_DEFERRED.governance.openGovernanceDrawer(null);
        assert.equal(governanceGets().length, 1, '显式打开抽屉应按需读取治理建议');
    } finally {
        host.dispose();
    }
});

test('列表请求替换会中止在途治理建议并复位 loading', async () => {
    const request = deferred();
    let signal = null;
    const host = await createAssetViewHost({
        embedded: true,
        tab: 'registry',
        fetchImpl: (url, init = {}) => {
            const target = String(url);
            if (target.endsWith('/api/asset-registry/governance/suggestions')) {
                signal = init.signal;
                return request.promise;
            }
            if (target.includes('/api/asset-registry/status')) return Promise.resolve(response({ roots: [], jobs: [], data_status: 'ok' }));
            return Promise.resolve(response({ items: [], total: 0, limit: 120, offset: 0, data_status: 'ok' }));
        },
    });
    try {
        await import(pathToFileURL(path.join(host.tree, 'web/js/modules/asset-manager/governance.js')).href);
        const { GW_DEFERRED } = await import(stateModuleUrl(host.tree));
        const pending = GW_DEFERRED.governance.loadGovernanceSuggestions(true);
        await wait(10);
        assert.ok(signal && !signal.aborted);

        void host.main.refreshRegistryAssets(false);
        await wait(10);
        assert.equal(signal.aborted, true, '切换列表查询必须调用 AbortController.abort()');
        assert.equal(host.main.governanceSuggestionsLoading, false, '取消后清除 loading 状态');
        request.resolve(suggestionsResponse());
        await pending;
    } finally {
        request.resolve(suggestionsResponse());
        host.dispose();
    }
});

test('连续刷新和关闭抽屉会中止对应在途请求', async () => {
    const pending = [];
    const host = await createAssetViewHost({
        embedded: true, tab: 'registry',
        fetchImpl: (url, init = {}) => {
            if (String(url).endsWith('/api/asset-registry/governance/suggestions')) {
                const request = deferred();
                pending.push({ ...request, signal: init.signal });
                return request.promise;
            }
            return Promise.resolve(response({ items: [], total: 0, limit: 120, offset: 0 }));
        },
    });
    try {
        await import(pathToFileURL(path.join(host.tree, 'web/js/modules/asset-manager/governance.js')).href);
        const { GW_DEFERRED } = await import(stateModuleUrl(host.tree));
        const first = GW_DEFERRED.governance.openGovernanceDrawer(null);
        await wait(10);
        const second = GW_DEFERRED.governance.loadGovernanceSuggestions(true);
        await wait(10);
        assert.equal(pending[0].signal.aborted, true);
        assert.equal(pending[1].signal.aborted, false);
        const drawerHost = host.document.querySelector('[data-governance-drawer-host]');
        drawerHost.dispatchEvent({ type: 'click', target: host.document.querySelector('[data-governance-drawer-close]') });
        assert.equal(pending[1].signal.aborted, true);
        assert.equal(host.main.governanceSuggestionsLoading, false);
        pending.forEach(item => item.resolve(suggestionsResponse()));
        await Promise.all([first, second]);
    } finally {
        pending.forEach(item => item.resolve(suggestionsResponse()));
        host.dispose();
    }
});

test('实际加载下一页会中止在途治理建议', async () => {
    const request = deferred();
    let signal;
    const host = await createAssetViewHost({
        embedded: true, tab: 'registry',
        fetchImpl: (url, init = {}) => {
            const target = String(url);
            if (target.endsWith('/api/asset-registry/governance/suggestions')) {
                signal = init.signal;
                return request.promise;
            }
            if (target.includes('/api/asset-registry/assets')) {
                return Promise.resolve(response({ items: [], total: 2, limit: 1, offset: 0, next_offset: 1, has_more: true }));
            }
            return Promise.resolve(response({ items: [], total: 0, limit: 120, offset: 0 }));
        },
    });
    try {
        await host.main.refreshRegistryAssets(false);
        await wait(10);
        assert.equal(host.main.registryHasMoreAssets(), true);
        await import(pathToFileURL(path.join(host.tree, 'web/js/modules/asset-manager/governance.js')).href);
        const { GW_DEFERRED } = await import(stateModuleUrl(host.tree));
        const pending = GW_DEFERRED.governance.loadGovernanceSuggestions(true);
        await wait(10);
        await host.main.loadMoreRegistryAssets();
        assert.equal(signal.aborted, true);
        assert.equal(host.main.governanceSuggestionsLoading, false);
        request.resolve(suggestionsResponse());
        await pending;
    } finally {
        request.resolve(suggestionsResponse());
        host.dispose();
    }
});

test('延后网关超时会真正中止治理建议请求', async () => {
    let signal = null;
    const request = deferred();
    installHost({
        embedded: true,
        fetchImpl: (url, init = {}) => {
            if (String(url).endsWith('/api/asset-registry/governance/suggestions')) {
                signal = init.signal;
                return request.promise;
            }
            return Promise.resolve(response({}));
        },
    });
    const tree = makeProductTree({}, [{
        from: 'const DEFERRED_DOMAIN_JOB_TIMEOUT_MS = 15000;',
        to: 'const DEFERRED_DOMAIN_JOB_TIMEOUT_MS = 500;',
    }]);
    try {
        const main = await loadProduct(tree);
        const pending = main.gwCall('governance', 'loadGovernanceSuggestions');
        main.openDeferredDomainGate();
        await assert.rejects(pending, /延后域任务超时/);
        assert.ok(signal?.aborted, '网关超时后必须中止底层请求');
    } finally {
        request.resolve(suggestionsResponse());
        dropTree(tree);
    }
});

test('目录替换与下一页加载都连到治理取消入口', async () => {
    const source = await import('node:fs/promises').then(fs => fs.readFile(
        path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../../web/js/modules/asset-manager.js'),
        'utf8',
    ));
    assert.match(source, /function beginRegistryAssetRequest\(\)[\s\S]*?cancelGovernanceSuggestions\?\.\(\)/);
    assert.match(source, /async function loadMoreRegistryAssets\(\)[\s\S]*?cancelGovernanceSuggestions\?\.\(\)/);
});
