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
 * `hooks.beforeAssets(url)` 可返回 Promise 实现「慢响应 / 乱序响应」。
 */
function registryFetch(hooks = {}) {
    const requests = [];
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
                revision: 7,
                overview: {roots: ROOTS, features: [], active_job: null, last_job: null},
            }));
        }
        if (text.includes('/api/asset-registry/assets')) {
            const rootMatch = /root_id=([^&]+)/.exec(text);
            const rootId = rootMatch ? decodeURIComponent(rootMatch[1]) : 'root_a';
            const respond = () => json({
                total: TOTALS[rootId] ?? 0,
                items: [],
                limit: 120,
                offset: 0,
                has_more: false,
                next_offset: null,
                total_known: true,
            });
            return hooks.beforeAssets ? hooks.beforeAssets(text).then(respond) : respond();
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

test('QIN-603 AC4：回到看过的文件夹命中缓存不再请求列表；revision 变化后必须重新取数', async () => {
    const {host, requests} = await bootHost();
    try {
        await switchFolder(host, 'root_a');
        await switchFolder(host, 'root_b');
        requests.length = 0;

        await switchFolder(host, 'root_a');
        const assets = registryRequests(requests).filter(entry => entry.url.includes('/api/asset-registry/assets'));
        assert.equal(assets.length, 0, '热切换回到看过的文件夹不得再请求 /api/asset-registry/assets');
        assert.equal(host.main.registryTotal, TOTALS.root_a, '命中缓存后仍必须呈现该文件夹的权威总数（不是空白）');

        // 写后失效：revision 推进后同一条缓存必须作废（不能拿旧列表当新数据）
        host.main.setRegistryStatus({ready: true, revision: 8, overview: {roots: ROOTS, features: []}});
        requests.length = 0;
        await switchFolder(host, 'root_b');
        await switchFolder(host, 'root_a');
        const after = registryRequests(requests).filter(entry => entry.url.includes('/api/asset-registry/assets'));
        assert.equal(after.length, 1, 'revision 变化后重新进入必须重新取数（旧缓存条目作废）');
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
