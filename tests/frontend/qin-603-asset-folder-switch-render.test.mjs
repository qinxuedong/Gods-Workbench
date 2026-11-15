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
 * QIN-603：资产页点击文件夹的重绘合批、目录缓存不复用作废、热切换缓存命中。
 *
 * 全部用例跑真实产品模块（见 ./qin-602-asset-view-harness.mjs）：整棵 web/js 复制到
 * 临时目录后用原生 import() 加载，经 window.GWAssetTreeBridge.activate 驱动真实点击链路
 * （selectAssetTreeNode → gwCall('settings','selectAssetRackFolder') → refreshRegistryAssets）。
 * 不使用源码切片 / vm / 函数替身；重绘次数由「管理根里 .asset-content 元素身份变化」观测，
 * 不是数 render() 被调了几次。
 */

import {test} from 'node:test';
import assert from 'node:assert/strict';
import {
    MOUNTED_BOARD, bootProduct, dropTree, installHost, loadProduct, makeProductTree, wait,
} from './qin-602-asset-view-harness.mjs';

const ROOTS = [
    {id: 'root_a', name: '甲库', count: 3},
    {id: 'root_b', name: '乙库', count: 7},
];
/** 各根的权威总数不同，用来判定「当前视图读的是哪个根的列表」。 */
const TOTALS = {root_a: 3, root_b: 7};
/** AC4 失效回读场景下受控网络给出的「写入 / 索引后」新总数。 */
const NEW_TOTAL = 11;

function assetOf(rootId, index) {
    return {
        id: `${rootId}-${index}`,
        asset_id: `${rootId}-${index}`,
        kind: 'image',
        media_type: 'image',
        display_path: `/srv/${rootId}/cat-${rootId}-${index}.png`,
        relative_path: `cat-${rootId}-${index}.png`,
        storage_root_id: rootId,
        root_id: rootId,
        canonical_relpath: `cat-${rootId}-${index}.png`,
        name: `cat-${rootId}-${index}.png`,
    };
}

/**
 * 受控注册表网络：只实现这条链路上产品真正会调的接口，其余一律 reject，
 * 让「漏了某个接口」立刻暴露，而不是静默拿到空数据。
 * `hooks.beforeAssets(url)` 可返回 Promise 实现「慢响应 / 乱序响应」；
 * `hooks.assetsTotal(rootId, url, callIndex)` 可定制该次响应的权威总数
 * （AC4 回读新数据；AC6 用 callIndex 让旧响应携带可区分的总数）。
 */
function registryFetch(hooks = {}) {
    const requests = [];
    let assetsCallCount = 0;
    const impl = (url, init) => {
        const text = String(url);
        requests.push({url: text, method: String(init?.method || 'GET').toUpperCase()});
        if (text.includes('/api/asset-registry/facets')) {
            return Promise.resolve(json({
                kinds: [{id: 'image', count: 6}],
                categories: [{id: 'image', count: 6}],
                tags: [],
                collections: [],
                roots: ROOTS,
            }));
        }
        if (text.includes('/api/asset-registry/status')) {
            return Promise.resolve(json({
                ready: true,
                revision: hooks.statusRevision ? hooks.statusRevision() : 7,
                overview: {roots: ROOTS, features: [], active_job: null, last_job: null},
            }));
        }
        if (text.includes('/api/asset-registry/assets') && !text.includes('project_id=')) {
            const rootMatch = /root_id=([^&]+)/.exec(text);
            const rootId = rootMatch ? decodeURIComponent(rootMatch[1]) : 'root_a';
            // 每次 bootHost 实例内对 /assets 列表的调用序号；total 在请求发出时同步确定
            // 并闭包捕获（AC6 用它让 A1 与 A2 携带可区分的权威总数，不受响应落地顺序影响）。
            assetsCallCount += 1;
            const callIndex = assetsCallCount;
            const total = hooks.assetsTotal
                ? hooks.assetsTotal(rootId, text, callIndex)
                : (TOTALS[rootId] ?? 0);
            const respond = () => json({
                total,
                items: [],
                limit: 120,
                offset: 0,
                has_more: false,
                next_offset: null,
                total_known: true,
            });
            return hooks.beforeAssets ? hooks.beforeAssets(text).then(respond) : respond();
        }
        if (text.includes('/api/asset-registry/assets') && text.includes('project_id=')) {
            // AC2 的项目计数通道（/assets?project_id=…&limit=1）：单独成支，只回 total，
            // 与列表通道分开，避免「误把计数响应当列表」的假绿。
            return Promise.resolve(json({total: 2, items: [], limit: 1, offset: 0, total_known: true}));
        }
        if (text.includes('/api/asset-registry/asset-structures')) return Promise.resolve(json({structures: []}));
        if (text.includes('/api/asset-registry/folders')) {
            return Promise.resolve(json({folders: [], indexed_folders: []}));
        }
        if (text.includes('/api/asset-registry/projects')) return Promise.resolve(json({projects: []}));
        if (text.includes('/api/asset-registry/presets')) return Promise.resolve(json({presets: []}));
        if (text.includes('/api/media-preview') || text.includes('/static/')) return Promise.resolve(new Response('', {status: 204}));
        return Promise.reject(new Error(`QIN-603 测试宿主未提供该接口: ${text}`));
    };
    impl.requests = requests;
    return impl;
}

function json(payload) {
    return new Response(JSON.stringify(payload), {status: 200, headers: {'Content-Type': 'application/json'}});
}

/**
 * 重绘计数器：整页渲染由 renderRegistryManager 写 `root.innerHTML` 完成，
 * 因此「.asset-content 元素身份变了」= 发生了一次整树重绘。
 * 只观测身份变化，不清空时的 innerHTML='' 不计入（那只是清容器，不产出内容）。
 */
function countRenders(root) {
    const proto = Object.getPrototypeOf(root);
    const descriptor = Object.getOwnPropertyDescriptor(proto, 'innerHTML');
    assert.ok(descriptor?.get && descriptor?.set, '宿主根节点必须支持 innerHTML 读写');
    const state = {renders: 0};
    Object.defineProperty(root, 'innerHTML', {
        configurable: true,
        get() { return descriptor.get.call(root); },
        set(value) {
            const before = root.querySelector('.asset-content');
            descriptor.set.call(root, value);
            const after = root.querySelector('.asset-content');
            if (after && after !== before) state.renders += 1;
        },
    });
    return state;
}

async function bootHost(hooks) {
    const fetchImpl = registryFetch(hooks);
    const host = installHost({embedded: true, theme: 'dark', boards: MOUNTED_BOARD, fetchImpl});
    // 宿主补齐 <template>.content：卡片渲染会经 template.innerHTML 解一段 markup 再取 img。
    // 只补这一个 DOM 能力，不触碰任何产品代码或断言目标。
    const doc = host.document;
    const createElement = doc.createElement.bind(doc);
    doc.createElement = tag => {
        const element = createElement(tag);
        if (String(tag).toLowerCase() === 'template') element.content = element;
        return element;
    };
    const tree = makeProductTree();
    const main = await loadProduct(tree);
    await bootProduct(doc);
    main.setActiveTab('registry');
    main.render();
    await wait(60);
    const renders = countRenders(host.root);
    return {host: {...host, main, tree, dispose: () => dropTree(tree)}, renders, requests: fetchImpl.requests};
}

async function switchFolder(host, rootId) {
    globalThis.GWAssetTreeBridge.activate(`mine:root:${rootId}`);
    await wait(120);
}

function registryRequests(requests) {
    return requests.filter(entry => entry.url.includes('/api/asset-registry/'));
}

test('QIN-603 AC1/AC2：一次目录切换只重绘 2 次，且不重拉 /folders 与 /assets?project_id=', async () => {
    const {host, renders, requests} = await bootHost();
    try {
        // 先暖一次，把「首次进入某目录」的建树成本排除在计数窗口外
        await switchFolder(host, 'root_a');
        await switchFolder(host, 'root_b');
        requests.length = 0;
        renders.renders = 0;

        await switchFolder(host, 'root_a');

        assert.equal(renders.renders, 2, '一次点击只允许「一次加载态 + 一次终态」两次整树重绘');
        const folders = registryRequests(requests).filter(entry => entry.url.includes('/api/asset-registry/folders'));
        assert.equal(folders.length, 0, '筛选未变时切文件夹不得重拉 /api/asset-registry/folders');
        const projectCounts = registryRequests(requests).filter(entry => entry.url.includes('project_id='));
        assert.equal(projectCounts.length, 0, '筛选未变时切文件夹不得重拉 /api/asset-registry/assets?project_id=');
    } finally {
        host.dispose();
    }
});

test('QIN-603 AC5：同一目录在加载态未落地时被重复激活，只画一帧加载态', async () => {
    let releaseSlow = () => {};
    const slow = new Promise(resolve => { releaseSlow = resolve; });
    let slowed = false;
    const {host, renders} = await bootHost({
        beforeAssets(url) {
            if (!slowed && url.includes('root_id=root_b')) {
                slowed = true;
                return slow;
            }
            return Promise.resolve();
        },
    });
    try {
        // 先暖掉建树成本，再把计数窗口对准「重复激活同一目录」这一段
        await switchFolder(host, 'root_a');
        renders.renders = 0;

        globalThis.GWAssetTreeBridge.activate('mine:root:root_b');
        await wait(40);                       // 第一次点击的加载态已画出来，它的列表仍挂起
        assert.equal(renders.renders, 1, '第一次点击必须画出加载态');

        // 同一目录再点一次：可见内容与当前加载态完全一样，不得再整树重画一帧。
        globalThis.GWAssetTreeBridge.activate('mine:root:root_b');
        await wait(160);
        assert.equal(renders.renders, 2, '重复激活同一目录只允许「一帧加载态 + 一帧终态」');

        // 被取代的那次响应迟到也不能改写视图、更不能再多画一帧。
        releaseSlow();
        await wait(200);
        assert.equal(renders.renders, 2, '迟到的旧响应不得再触发重绘');
        assert.equal(host.main.registryTotal, TOTALS.root_b, '终态必须是最后一次点击的那个目录');
    } finally {
        host.dispose();
    }
});

test('QIN-603 AC4：回到看过的文件夹命中缓存不再请求列表；revision 变化后必须重新取数并回读新数据', async () => {
    const hooks = {};
    const {host, requests} = await bootHost(hooks);
    try {
        await switchFolder(host, 'root_a');
        await switchFolder(host, 'root_b');
        requests.length = 0;

        await switchFolder(host, 'root_a');
        const assets = registryRequests(requests).filter(entry => entry.url.includes('/api/asset-registry/assets'));
        assert.equal(assets.length, 0, '热切换回到看过的文件夹不得再请求 /api/asset-registry/assets');
        assert.equal(host.main.registryTotal, TOTALS.root_a, '命中缓存回读必须呈现该文件夹的权威总数（不是空白）');

        // 写后失效：revision 推进后同一条缓存必须作废（不能拿旧列表当新数据），
        // 回读必须看到失效后受控网络给出的新总数（不只是「重新请求了」）。
        // 受控 /status 同步返回推进后的权威 revision（真实链路：写入/索引完成 → 服务端
        // revision 推进 → /status 轮询带回新值），否则真实 /status 响应会把推进覆盖回去。
        hooks.statusRevision = () => 8;
        host.main.setRegistryStatus({ready: true, revision: 8, overview: {roots: ROOTS, features: []}});
        hooks.assetsTotal = rootId => rootId === 'root_a' ? NEW_TOTAL : TOTALS[rootId];
        requests.length = 0;
        await switchFolder(host, 'root_b');
        await switchFolder(host, 'root_a');
        const after = registryRequests(requests).filter(entry => entry.url.includes('/api/asset-registry/assets'));
        assert.equal(after.length, 2, 'revision 变化后两个看过的文件夹都必须重新取数（旧缓存条目作废）');
        assert.equal(host.main.registryTotal, NEW_TOTAL, '失效回读必须看到新数据（不是旧总数）');
    } finally {
        host.dispose();
    }
});

test('QIN-603 AC4：真实写后刷新入口使全表缓存失效并回读新数据（不依赖 revision 推进）', async () => {
    const hooks = {};
    const {host, requests} = await bootHost(hooks);
    try {
        await switchFolder(host, 'root_a');
        await switchFolder(host, 'root_b');
        requests.length = 0;
        await switchFolder(host, 'root_a');
        const hot = registryRequests(requests).filter(entry => entry.url.includes('/api/asset-registry/assets'));
        assert.equal(hot.length, 0, '写后刷新前热切换必须命中缓存（0 请求）');

        // 【审核 R6】「写后必刷」的窗口期场景（revision 用例钉不住的那一段）：
        // 服务端数据已变（受控列表返回新总数），但 /status 还没把新 revision 带回来
        // （revision 全程不推进）——此时唯一兜底是 refreshRegistryAssets() 默认的
        // invalidateCache=true 清全表。反证：删掉 asset-manager.js 的
        // invalidateRegistryAssetCache('refresh') 调用后，下面的重取断言必须变红
        // （root_b 命中旧缓存、0 请求），「写后必刷」机制因此被独立钉住。
        hooks.assetsTotal = rootId => rootId === 'root_a' ? NEW_TOTAL : TOTALS[rootId];
        await host.main.refreshRegistryAssets();
        await wait(120);
        requests.length = 0;
        await switchFolder(host, 'root_b');
        await switchFolder(host, 'root_a');
        const after = registryRequests(requests).filter(entry => entry.url.includes('/api/asset-registry/assets'));
        assert.equal(after.length, 1, '写后刷新清空全表：切走必须重新取数（root_b 旧条目已失效）');
        // 回 root_a 命中的是 refresh 刚落盘的写后条目（正是写后必刷的语义闭环：
        // 写后先刷拿到最新数据，回看不再取旧数据），且回读必须看到新总数。
        assert.equal(host.main.registryTotal, NEW_TOTAL, '写后刷新回读必须看到新数据（窗口期内不依赖 revision）');
    } finally {
        host.dispose();
    }
});

test('QIN-603 AC6：快速连点不同文件夹只采用最后一次点击的结果', async () => {
    let releaseSlow = () => {};
    const slow = new Promise(resolve => { releaseSlow = resolve; });
    // 让「第一次点 root_a」的列表响应挂起：A 的旧响应晚于 B 落地，用来看它会不会覆盖新视图
    let slowed = false;
    const {host} = await bootHost({
        beforeAssets(url) {
            if (!slowed && url.includes('root_id=root_a')) {
                slowed = true;
                return slow;
            }
            return Promise.resolve();
        },
    });
    try {
        globalThis.GWAssetTreeBridge.activate('mine:root:root_a');
        await wait(20);
        globalThis.GWAssetTreeBridge.activate('mine:root:root_b');
        await wait(160);
        assert.equal(host.main.registryNavFolder.rootId, 'root_b', '最后点击的文件夹必须已生效');
        assert.equal(host.main.registryTotal, TOTALS.root_b, '最后点击的文件夹列表必须已落地');

        releaseSlow();
        await wait(200);
        assert.equal(host.main.registryNavFolder.rootId, 'root_b', '视图目录必须停在最后一次点击');
        assert.equal(host.main.registryTotal, TOTALS.root_b, '迟到的旧响应不得把旧文件夹的列表写进当前视图');
    } finally {
        host.dispose();
    }
});

test('QIN-603 AC6：快速连点 A→B→A，最后一次点击的结果胜出且两个旧响应都不覆盖', async () => {
    let releaseFirst = () => {};
    const firstSlow = new Promise(resolve => { releaseFirst = resolve; });
    let slowedCount = 0;
    // 【审核 R6】A1 与 A2 必须携带可区分的权威总数：A1（第 1 次 root_a 请求，挂起）
    // 返回 STALE_A_TOTAL，A2 返回 TOTALS.root_a。这样能区分「迟到响应被拦截」与
    // 「覆盖但值恰好相同」——若 A1 覆盖视图，registryTotal 会变成 STALE_A_TOTAL 而露馅。
    const STALE_A_TOTAL = 21;
    let rootACalls = 0;
    const {host} = await bootHost({
        beforeAssets(url) {
            if (slowedCount === 0 && url.includes('root_id=root_a')) {
                slowedCount += 1;
                return firstSlow;
            }
            return Promise.resolve();
        },
        assetsTotal(rootId, url) {
            // A1 = 第 1 个携带 root_id=root_a 的列表请求（请求发出时同步判定）。
            if (rootId === 'root_a' && url.includes('root_id=root_a')) {
                rootACalls += 1;
                if (rootACalls === 1) return STALE_A_TOTAL;
            }
            return TOTALS[rootId] ?? 0;
        },
    });
    try {
        globalThis.GWAssetTreeBridge.activate('mine:root:root_a');   // A1 挂起（STALE_A_TOTAL）
        await wait(20);
        globalThis.GWAssetTreeBridge.activate('mine:root:root_b');   // B 落地
        await wait(160);
        assert.equal(host.main.registryNavFolder.rootId, 'root_b', 'B 落地后视图必须停在 root_b');
        assert.equal(host.main.registryTotal, TOTALS.root_b, 'B 的列表必须已落地');
        globalThis.GWAssetTreeBridge.activate('mine:root:root_a');   // A2：A1 未落地无缓存 → 新请求
        await wait(160);
        assert.equal(host.main.registryNavFolder.rootId, 'root_a', '最后一次点击必须已生效');
        assert.equal(host.main.registryTotal, TOTALS.root_a, '最后一次点击的列表必须已落地');

        releaseFirst();  // A1 旧响应迟到：晚于 B 与 A2 落地，携带 STALE_A_TOTAL
        await wait(200);
        assert.equal(host.main.registryNavFolder.rootId, 'root_a', 'A1 迟到响应不得改写视图目录');
        assert.equal(host.main.registryTotal, TOTALS.root_a, 'A1 迟到的 STALE 总数不得写进当前视图');
    } finally {
        host.dispose();
    }
});

test('QIN-603 AC3 等价护栏：目录切换不改变 /api/asset-registry/folders 的调用面', async () => {
    // 与 AC2 同源但独立观测点：连切三次不同目录，folders 请求次数必须恒为 0，
    // 防止「只在第一次跳过、后面又逐次重拉」这类半吊子实现蒙过去。
    const {host, requests} = await bootHost();
    try {
        await switchFolder(host, 'root_a');
        await switchFolder(host, 'root_b');
        requests.length = 0;
        await switchFolder(host, 'root_a');
        await switchFolder(host, 'root_b');
        await switchFolder(host, 'root_a');
        const folders = registryRequests(requests).filter(entry => entry.url.includes('/api/asset-registry/folders'));
        assert.equal(folders.length, 0, '连续切换目录全程都不得重拉 /folders');
    } finally {
        host.dispose();
    }
});
