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

// [QIN-599] 删除资源库看板与「看板显示」偏好区块、系统入口固定与一次性来源迁移自动化测试
import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {
    bootProduct,
    dropTree,
    installHost,
    loadProduct,
    makeProductTree,
    wait,
} from './qin-602-asset-view-harness.mjs';

const ASSET_BOARD_KEY = 'asset_board_navigation_v1';
const ASSET_BOARD_LOCK_KEY = 'gw_asset_board_migration_lock_v1';

const managerSrc = readFileSync(new URL('../../web/js/modules/asset-manager.js', import.meta.url), 'utf8');
const organizeSrc = readFileSync(new URL('../../web/js/modules/asset-manager/organize.js', import.meta.url), 'utf8');
const settingsSrc = readFileSync(new URL('../../web/js/modules/asset-manager/settings.js', import.meta.url), 'utf8');
const managerCss = readFileSync(new URL('../../web/css/pages/asset-manager.css', import.meta.url), 'utf8');
const nodeFiles = readFileSync(new URL('./node-files.txt', import.meta.url), 'utf8');

function jsonResponse(data, status = 200) {
    return {
        status,
        ok: status >= 200 && status < 300,
        headers: {get: name => (String(name).toLowerCase() === 'content-type' ? 'application/json' : null)},
        json: async () => data,
        text: async () => JSON.stringify(data),
    };
}

function createMigrationFetch({
    role = 'admin',
    existingExtras = [],
    initialRoots = [],
    onPostRoot = null,
    onPostExtra = null,
    onGetStorageAfterPost = null,
} = {}) {
    const requests = [];
    let roots = [...initialRoots];
    let extras = [...existingExtras];
    let settingsRevision = 1;
    let rootsRevision = 1;
    let wroteExtras = 0;

    const fetchImpl = async (url, init = {}) => {
        const method = String(init.method || 'GET').toUpperCase();
        const body = init.body ? JSON.parse(String(init.body)) : null;
        requests.push({url, method, body});

        if (url.includes('/api/asset-auth/status')) {
            return jsonResponse({authenticated: true, auth_mode: 'local_account', principal: {username: 'tester', role}});
        }
        if (url.includes('/api/asset-registry/roots/alias-suggestions')) {
            return jsonResponse({candidates: [], revision: rootsRevision});
        }
        if (url.includes('/api/asset-registry/facets')) {
            return jsonResponse({
                kinds: [{kind: 'image', count: 3}],
                categories: [{category: 'image', count: 3}],
                tags: [],
                collections: [],
                roots: extras.map(e => ({id: e.id, name: e.name, path: e.path})),
            });
        }
        if (url.includes('/api/asset-registry/status')) {
            return jsonResponse({revision: 1, overview: {total: 3, roots: []}, jobs: []});
        }
        if (url.includes('/api/asset-registry/assets')) {
            return jsonResponse({items: [], total: 0, total_known: true});
        }
        if (url.includes('/api/asset-registry/folders')) {
            return jsonResponse({folders: []});
        }
        if (url.includes('/api/storage-settings/roots') && method === 'GET') {
            return jsonResponse({roots, revision: rootsRevision});
        }
        if (url.includes('/api/storage-settings/roots') && method === 'POST') {
            if (onPostRoot) {
                const custom = await onPostRoot(body, {roots, rootsRevision});
                if (custom) return custom;
            }
            if (role !== 'admin') {
                return jsonResponse({detail: {code: 'FORBIDDEN', message: '请联系管理员授权该文件夹'}}, 403);
            }
            rootsRevision += 1;
            const newRoot = {id: `root_${rootsRevision}`, path: body.path, source: 'persistent', active: true, availability: 'available'};
            roots.push(newRoot);
            return jsonResponse({roots, revision: rootsRevision});
        }
        if (url.endsWith('/api/storage-settings') && method === 'GET') {
            if (wroteExtras > 0 && onGetStorageAfterPost) {
                const override = await onGetStorageAfterPost({wroteExtras, extras});
                if (override) return override;
            }
            return jsonResponse({
                dirs: {local: 'D:/Sample/Local', upload: 'D:/Sample/Upload', generated: 'D:/Sample/Gen'},
                extra_local_entries: extras,
                revision: settingsRevision,
            });
        }
        if (url.includes('/api/asset-registry/reindex') && method === 'POST') {
            if (onPostExtra) {
                const custom = await onPostExtra(body, {extras, settingsRevision});
                if (custom) return custom;
            }
            if (role !== 'admin') {
                return jsonResponse({detail: {code: 'PATH_OUTSIDE_ALLOWED_ROOTS', message: '该文件夹尚未授权，请联系管理员'}}, 403);
            }
            wroteExtras += 1;
            settingsRevision += 1;
            const reg = body?.register_root || {};
            const extra = {id: reg.id || `local_extra_${wroteExtras}`, name: reg.name || '素材', path: reg.path};
            extras.push(extra);
            return jsonResponse({
                ok: true,
                job_id: `job_${wroteExtras}`,
                revision: settingsRevision,
            }, 200);
        }
        return jsonResponse({});
    };

    return {fetchImpl, requests, getRoots: () => roots, getExtras: () => extras};
}

function patchDocId(document) {
    const orig = document.getElementById.bind(document);
    document.getElementById = id => document.querySelector(`[id="${String(id)}"]`) || orig(String(id));
    if (!globalThis.HTMLElement) globalThis.HTMLElement = Object;
    const nodeProto = document.documentElement ? Object.getPrototypeOf(document.documentElement) : null;
    if (nodeProto && !nodeProto.contains) {
        nodeProto.contains = function (target) {
            let cur = target;
            while (cur) {
                if (cur === this) return true;
                cur = cur.parentNode;
            }
            return false;
        };
    }
    const origSetInterval = globalThis.setInterval;
    const intervals = new Set();
    globalThis.setInterval = (...args) => {
        const handle = origSetInterval(...args);
        if (handle && typeof handle.unref === 'function') handle.unref();
        intervals.add(handle);
        return handle;
    };
    return () => {
        for (const handle of intervals) clearInterval(handle);
        intervals.clear();
        globalThis.setInterval = origSetInterval;
    };
}

async function waitFor(predicate, timeoutMs = 2000) {
    const deadline = Date.now() + timeoutMs;
    while (Date.now() < deadline) {
        if (predicate()) return;
        await wait(20);
    }
}

test('AC2/AC3/AC4/AC5/AC7：系统分类入口固定显示、清除 hidden、移除建看板与「看板显示」设置及死样式', async () => {
    const {fetchImpl} = createMigrationFetch();
    const boards = {
        order: ['registry', 'library_old'],
        hidden: ['scripts', 'projects', 'workflows', 'prompts', 'lut', 'canvas-assets'],
        customBoards: [],
    };
    const host = installHost({embedded: false, boards, fetchImpl});
    const cleanupHost = patchDocId(host.document);
    const tree = makeProductTree();
    try {
        const main = await loadProduct(tree);
        await bootProduct(host.document);
        await wait(60);

        const persisted = JSON.parse(host.storage.getItem(ASSET_BOARD_KEY) || '{}');
        assert.deepEqual(persisted.hidden, [], 'QIN-599 AC4：已存储的 hidden 偏好必须被强制清空');
        assert.equal(main.visibleAssetBoards().length, 7, '全部 7 个系统分类入口固定可见');
        assert.equal(main.allAssetBoards().every(b => b.system === true), true, 'allAssetBoards 仅包含系统分类入口');

        const tabsMarkup = host.document.getElementById('assetBoardTabs')?.innerHTML || '';
        assert.match(tabsMarkup, /\+ 添加资源文件夹/, '统一创建按钮文案为 + 添加资源文件夹');
        assert.doesNotMatch(tabsMarkup, /library_old|source_library_|draggable="true"|data-board-drag/, '导航条不含旧版看板、自动来源看板与拖拽属性');
        for (const id of ['registry', 'scripts', 'projects', 'workflows', 'prompts', 'lut', 'canvas-assets']) {
            assert.ok(tabsMarkup.includes(`data-tab="${id}"`), `系统入口 ${id} 必须固定渲染`);
        }

        // 点击「+ 添加资源文件夹」应打开 QIN-596 添加资源文件夹弹窗而非旧创建资源库弹窗
        const addBtn = host.document.querySelector('#assetBoardTabs [data-board-create]');
        assert.ok(addBtn, '#assetBoardTabs [data-board-create] 存在');
        await main.gwCall('organize', 'openBoardSourceModal', '', 'new');
        await wait(20);
        const modalTitle = host.document.getElementById('boardSourceTitle')?.textContent || '';
        assert.equal(modalTitle, '添加资源文件夹');
        assert.equal(host.document.getElementById('createLibraryOverlay')?.innerHTML || '', '', '旧 createLibraryOverlay 不得渲染');

        // 打开资产设置面板：不含「看板显示」区块、不含 visibility/edit/delete 控件
        await main.gwCall('settings', 'openStorageSettings', 'ai');
        await wait(20);
        const settingsModalHtml = host.document.getElementById('storageSettingsOverlay')?.innerHTML || '';
        assert.doesNotMatch(settingsModalHtml, /看板显示|data-board-visibility|data-board-edit|data-board-delete|data-source-board-delete|renderAssetBoardSettingsBody/);
    } finally {
        cleanupHost();
        dropTree(tree);
    }

    assert.doesNotMatch(settingsSrc, /renderAssetBoardSettingsBody|data-board-visibility|data-board-edit|data-board-delete|data-source-board-delete/);
    assert.doesNotMatch(organizeSrc, /function createPresetAssetBoard|function renderCreateLibraryModal|function openCreateLibraryModal|function removeGeneratedSourceBoard/);
    assert.doesNotMatch(managerCss, /\.board-settings-row|\.board-visibility-switch|\.board-library-edit-modal/);
    assert.ok(nodeFiles.includes('tests/frontend/qin-599-remove-asset-boards.test.mjs'), '新测试文件必须登记到 tests/frontend/node-files.txt');
});

test('AC1 & 持久化冷读：管理员首次加载按路径去重迁移来源（已存在仅删看板定义），刷新零残留', async () => {
    const net = createMigrationFetch({
        role: 'admin',
        existingExtras: [{id: 'local_extra_existing', name: '已有目录', path: 'D:\\Assets\\Existing'}],
        initialRoots: [{id: 'root_existing', path: 'D:/Assets/Existing', source: 'persistent', active: true}],
    });
    const boards = {
        order: ['registry', 'library_a', 'library_b'],
        hidden: ['prompts'],
        customBoards: [
            {
                id: 'library_a',
                name: '品牌库A',
                preset: 'image',
                sources: [
                    {id: 'src_1', name: '品牌目录', path: 'D:\\Assets\\Brand\\'},
                    {id: 'src_2', name: '已有目录', path: 'D:/Assets/Existing'},
                ],
            },
            {
                id: 'library_b',
                name: '品牌库B',
                preset: 'all',
                sources: [
                    {id: 'src_3', name: '品牌目录副本', path: 'D:/Assets/Brand'},
                ],
            },
        ],
    };
    const host = installHost({embedded: false, boards, fetchImpl: net.fetchImpl});
    const cleanupHost = patchDocId(host.document);
    const tree = makeProductTree();
    try {
        const main = await loadProduct(tree);
        await bootProduct(host.document);
        await waitFor(() => {
            const p = JSON.parse(host.storage.getItem(ASSET_BOARD_KEY) || '{}');
            return p.migration?.status === 'done';
        });

        const rootPosts = net.requests.filter(r => r.url.includes('/api/storage-settings/roots') && r.method === 'POST');
        const extraPosts = net.requests.filter(r => r.url.includes('/api/asset-registry/reindex') && r.method === 'POST');
        assert.equal(rootPosts.length, 1, '重复/等价物理路径只自动授权 1 次允许根，且不重复登记已存在目录');
        assert.equal(extraPosts.length, 1, '已存在来源只删看板定义，新来源登记 1 次');

        const persisted = JSON.parse(host.storage.getItem(ASSET_BOARD_KEY) || '{}');
        assert.deepEqual(persisted.customBoards, [], '迁移完成后必须删除已转换的看板定义');
        assert.equal(persisted.migration?.status, 'done');
        assert.deepEqual(persisted.hidden, []);

        const isWritePost = r => r.method === 'POST' && (r.url.includes('/api/storage-settings/roots') || r.url.includes('/api/asset-registry/reindex'));
        // 模拟冷读（刷新后再次执行迁移）：幂等零写盘
        const beforeCount = net.requests.length;
        const second = await main.gwCall('organize', 'runAssetBoardOneTimeMigration', {trigger: 'boot'});
        assert.equal(second.status, 'noop');
        assert.equal(net.requests.filter(isWritePost).length, rootPosts.length + extraPosts.length, '二次加载零重复写 POST');
        assert.ok(net.requests.length === beforeCount, '冷读空待迁移账本不再请求网络');
    } finally {
        cleanupHost();
        dropTree(tree);
    }
});

test('AC1 & 安全底线：非管理员不静默授权允许根，保留未迁移看板并展示联系管理员提示', async () => {
    const net = createMigrationFetch({role: 'member'});
    const boards = {
        order: ['registry', 'library_member'],
        hidden: [],
        customBoards: [
            {
                id: 'library_member',
                name: '团队未授权目录',
                preset: 'all',
                sources: [{id: 'src_mem', name: '未授权素材', path: 'D:\\Team\\Secret'}],
            },
        ],
    };
    const host = installHost({embedded: false, boards, fetchImpl: net.fetchImpl});
    const cleanupHost = patchDocId(host.document);
    const tree = makeProductTree();
    try {
        await loadProduct(tree);
        await bootProduct(host.document);
        await waitFor(() => {
            const p = JSON.parse(host.storage.getItem(ASSET_BOARD_KEY) || '{}');
            return p.migration?.status === 'partial';
        });

        const rootPosts = net.requests.filter(r => r.url.includes('/api/storage-settings/roots') && r.method === 'POST');
        assert.equal(rootPosts.length, 0, '非管理员绝对不得静默调用 POST /api/storage-settings/roots');

        const persisted = JSON.parse(host.storage.getItem(ASSET_BOARD_KEY) || '{}');
        assert.equal(persisted.customBoards.length, 1, '未授权来源对应的看板定义不得删除');
        assert.equal(persisted.customBoards[0].id, 'library_member');
        assert.equal(persisted.migration?.status, 'partial');

        const noticeText = host.root.innerHTML || '';
        assert.match(noticeText, /data-board-migration-notice/);
        assert.match(noticeText, /请联系管理员授权/);
        assert.match(noticeText, /Secret/);
    } finally {
        cleanupHost();
        dropTree(tree);
    }
});

test('异步慢延迟（3s）与选态回落：完成前不得提前删看板或报完成，选中被删看板时回落到资产总览', async () => {
    const net = createMigrationFetch({
        role: 'admin',
        onPostRoot: async () => {
            await wait(3050);
            return null;
        },
    });
    const boards = {
        order: ['registry', 'library_slow'],
        hidden: [],
        customBoards: [
            {
                id: 'library_slow',
                name: '慢速迁移库',
                preset: 'all',
                sources: [{id: 'src_slow', name: '慢速目录', path: 'D:\\Assets\\Slow3s'}],
            },
        ],
    };
    const host = installHost({embedded: false, boards, fetchImpl: net.fetchImpl});
    const cleanupHost = patchDocId(host.document);
    const tree = makeProductTree();
    try {
        const main = await loadProduct(tree);
        main.setActiveTab('library_slow');
        await bootProduct(host.document);
        await waitFor(() => main.boardMigrationState.running === true, 1500);
        main.render();

        // 在 3s 慢请求完成前断言中间态：running=true，且 customBoards 仍保留在 localStorage
        const midPersisted = JSON.parse(host.storage.getItem(ASSET_BOARD_KEY) || '{}');
        assert.equal(midPersisted.customBoards.length, 1, '3s 慢迁移完成前不得提前删除看板定义');
        assert.equal(main.boardMigrationState.running, true, '3s 慢迁移期间 running 必须保持 true');
        assert.equal(main.boardMigrationState.completed, false, '完成前不得标记 completed');
        assert.match(host.root.innerHTML || host.document.getElementById('assetStatus')?.textContent || '', /正在将旧版资源库来源迁移为资源文件夹|请勿关闭页面/);

        // 等待 3s 慢请求落地完成
        await waitFor(() => !main.boardMigrationState.running && main.boardMigrationState.completed, 4500);
        const endPersisted = JSON.parse(host.storage.getItem(ASSET_BOARD_KEY) || '{}');
        assert.deepEqual(endPersisted.customBoards, [], '慢迁移完成后删除看板定义');
        assert.equal(main.boardMigrationState.running, false);
        assert.equal(main.boardMigrationState.completed, true);
        assert.equal(main.activeTab, 'registry', '被迁移删除的旧选中看板回落到资产总览 registry');
    } finally {
        cleanupHost();
        dropTree(tree);
    }
});

test('异步慢延迟（20s）与取消 + 乱序部分失败 + 写后刷新恢复：迟到响应不写错代际，中断/失败下次续跑', async () => {
    // 子阶段 A：20s 慢请求期间触发 pagehide 取消（立即解除阻塞并清理锁，迟到结果不得误删或覆盖）
    let slow20sTimer = null;
    let resolveSlow20s = null;
    const netA = createMigrationFetch({
        role: 'admin',
        onPostRoot: () => new Promise(resolve => {
            slow20sTimer = setTimeout(() => resolve(null), 20050);
            if (typeof slow20sTimer?.unref === 'function') slow20sTimer.unref();
            resolveSlow20s = resolve;
        }),
    });
    const hostA = installHost({
        embedded: false,
        boards: {
            order: ['registry', 'library_20s'],
            hidden: [],
            customBoards: [{id: 'library_20s', name: '20s慢库', preset: 'all', sources: [{id: 's20', name: 'S20', path: 'D:\\Assets\\Slow20s'}]}],
        },
        fetchImpl: netA.fetchImpl,
    });
    const cleanupA = patchDocId(hostA.document);
    const treeA = makeProductTree();
    try {
        const mainA = await loadProduct(treeA);
        mainA.openDeferredDomainGate();
        const migrationPromise = mainA.gwCall('organize', 'runAssetBoardOneTimeMigration', {trigger: 'boot'});
        await wait(120);
        assert.equal(mainA.boardMigrationState.running, true);
        assert.equal(JSON.parse(hostA.storage.getItem(ASSET_BOARD_KEY)).customBoards.length, 1);

        // 模拟切页 / 刷新触发取消
        globalThis.dispatchEvent?.(new Event('pagehide'));
        const resA = await migrationPromise;
        assert.equal(resA.status, 'cancelled', 'pagehide 后必须以 cancelled 返回且保留原看板定义');
        assert.equal(JSON.parse(hostA.storage.getItem(ASSET_BOARD_KEY)).customBoards.length, 1, '取消时绝不删除未完成看板');
        assert.equal(hostA.storage.getItem(ASSET_BOARD_LOCK_KEY) ?? null, null, '取消必须释放迁移互斥锁');
        if (slow20sTimer) clearTimeout(slow20sTimer);
        if (resolveSlow20s) resolveSlow20s(null);
        await wait(20);
    } finally {
        cleanupA();
        dropTree(treeA);
    }

    // 子阶段 B：乱序返回 + 部分失败（FailFirst 先 500、SlowOk 延迟 80ms 后成功）
    let failAttempt = 0;
    const netB = createMigrationFetch({
        role: 'admin',
        onPostRoot: async body => {
            if (body.path.includes('SlowOk')) {
                await wait(80);
                return null;
            }
            if (body.path.includes('FailFirst')) {
                failAttempt += 1;
                if (failAttempt === 1) {
                    return jsonResponse({detail: {code: 'ROOT_FAIL', message: '首次授权写入失败'}}, 500);
                }
            }
            return null;
        },
    });
    const hostB = installHost({
        embedded: false,
        boards: {
            order: ['registry', 'library_mix'],
            hidden: [],
            customBoards: [{
                id: 'library_mix',
                name: '混合来源库',
                preset: 'all',
                sources: [
                    {id: 's_ok', name: '先发起慢成功', path: 'D:\\Assets\\SlowOk'},
                    {id: 's_fail', name: '乱序先失败', path: 'D:\\Assets\\FailFirst'},
                ],
            }],
        },
        fetchImpl: netB.fetchImpl,
    });
    const cleanupB = patchDocId(hostB.document);
    const treeB = makeProductTree();
    try {
        const mainB = await loadProduct(treeB);
        await bootProduct(hostB.document);
        await waitFor(() => {
            const p = JSON.parse(hostB.storage.getItem(ASSET_BOARD_KEY) || '{}');
            return p.migration?.status === 'partial';
        }, 2000);

        // FailFirst 失败、SlowOk 乱序后成功 -> 仅保留 FailFirst 在 remainingBoards，SlowOk 已移出，写后刷新失败有提示不崩溃
        const mid = JSON.parse(hostB.storage.getItem(ASSET_BOARD_KEY) || '{}');
        assert.equal(mid.customBoards.length, 1);
        assert.deepEqual(mid.customBoards[0].sources.map(s => s.path), ['D:\\Assets\\FailFirst'], '乱序成功来源应精确从待迁移看板剔除，失败来源保留续跑');
        assert.equal(mid.migration?.status, 'partial');

        // 第二次重试/加载续跑 FailFirst，这次成功
        await mainB.gwCall('organize', 'runAssetBoardOneTimeMigration', {trigger: 'retry'});
        await wait(60);
        const finalPersisted = JSON.parse(hostB.storage.getItem(ASSET_BOARD_KEY) || '{}');
        assert.deepEqual(finalPersisted.customBoards, [], '中断/部分失败后再次加载续跑成功清空看板定义');
        assert.equal(finalPersisted.migration?.status, 'done');
    } finally {
        cleanupB();
        dropTree(treeB);
    }
});

test('并发迁移互斥锁：两个标签页同时打开资产页，只有一个标签页执行登记，绝不重复 POST', async () => {
    const net = createMigrationFetch({role: 'admin'});
    const host = installHost({
        embedded: false,
        boards: {
            order: ['registry', 'library_dup'],
            hidden: [],
            customBoards: [{id: 'library_dup', name: '并发测试库', preset: 'all', sources: [{id: 's1', name: '并发目录', path: 'D:\\Assets\\Concurrent'}]}],
        },
        fetchImpl: net.fetchImpl,
    });
    const cleanupHost = patchDocId(host.document);
    const tree = makeProductTree();
    try {
        const main = await loadProduct(tree);
        main.openDeferredDomainGate();
        // 预先由另一个标签页持有租约锁，并在 90ms 后完成迁移并清空 customBoards、释放锁
        host.storage.setItem(ASSET_BOARD_LOCK_KEY, JSON.stringify({owner: 'other_tab', acquiredAt: Date.now(), expiresAt: Date.now() + 10000}));
        const pendingTab = main.gwCall('organize', 'runAssetBoardOneTimeMigration', {tabId: 'current_tab', lockWaitMs: 3000});
        await wait(90);
        host.storage.setItem(ASSET_BOARD_KEY, JSON.stringify({order: ['registry'], hidden: [], customBoards: [], migration: {status: 'done', blockedBoards: [], updatedAt: new Date().toISOString()}}));
        host.storage.removeItem(ASSET_BOARD_LOCK_KEY);

        const result = await pendingTab;
        assert.equal(result.status, 'delegated', '后拿到锁的标签页检测到已完成迁移后应直接委托退出');
        assert.equal(net.requests.filter(r => r.method === 'POST').length, 0, '并发标签页绝不重复提交登记请求');
    } finally {
        cleanupHost();
        dropTree(tree);
    }
});
