/**
 * Copyright 2026 qinxuedong (Gods-Workbench Authors)
 * SPDX-License-Identifier: FSL-1.1-Apache-2.0
 *
 * Licensed under the Functional Source License, Version 1.1, Apache 2.0
 * Future License (the "License"); you may not use this file except in
 * compliance with the License. You may obtain a copy of the License at
 *     https://fsl.software/
 *
 * Licensed on an "AS IS" basis, WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND,
 * either express or implied. See the License for the specific language
 * governing permissions and limitations under the License.
 */

import assert from 'node:assert/strict';
import test from 'node:test';
import {
    MOUNTED_BOARD, dropTree, installHost, loadProduct, makeProductTree, wait,
} from './qin-602-asset-view-harness.mjs';

const PREVIEW_DELAY_CASES = [1500, 5000];

function makeRegistryAssets(count = 144) {
    return Array.from({length: count}, (_, index) => {
        const id = `qin673-asset-${String(index).padStart(3, '0')}`;
        return {
            id,
            asset_id: id,
            name: `${id}.png`,
            kind: 'image',
            media_type: 'image',
            mime_type: 'image/png',
            category: 'image',
            status: 'active',
            is_remote: true,
            public_url: `/thumb/${id}.png`,
            storage_root_id: 'qin673-root',
            display_path: `${id}.png`,
            width: 1600,
            height: 900,
            size_bytes: 4096,
            metadata: {width: 1600, height: 900},
        };
    });
}

function makeVersionStructure(assets) {
    return {
        id: 'qin673-version-stack',
        structure_type: 'version',
        current_asset_id: assets[0].id,
        version: 1,
        members: assets.slice(0, 2).map((asset, sort_order) => ({
            asset_id: asset.id,
            sort_order,
            asset,
        })),
    };
}

function jsonResponse(payload) {
    return new Response(JSON.stringify(payload), {
        status: 200,
        headers: {'Content-Type': 'application/json'},
    });
}

function registryFetch(assets, structure, requests, gates) {
    return (url, init = {}) => {
        const parsed = new URL(String(url), 'http://gods-workbench.test');
        requests.push({url: parsed.href, method: String(init.method || 'GET').toUpperCase()});

        if (parsed.pathname.endsWith('/api/asset-registry/assets')) {
            if (parsed.searchParams.has('project_id')) {
                return Promise.resolve(jsonResponse({total: 0, items: [], total_known: true}));
            }
            if (gates.holdNextList) {
                gates.holdNextList = false;
                return new Promise((resolve, reject) => {
                    const pending = {resolve: () => resolve(jsonResponse({
                        total: assets.length,
                        items: assets,
                        limit: 1500,
                        offset: 0,
                        has_more: false,
                        next_offset: null,
                        total_known: true,
                    }))};
                    gates.pendingLists.push(pending);
                    init.signal?.addEventListener('abort', () => reject(new DOMException('已取消', 'AbortError')), {once: true});
                });
            }
            return Promise.resolve(jsonResponse({
                total: assets.length,
                items: assets,
                limit: 1500,
                offset: 0,
                has_more: false,
                next_offset: null,
                total_known: true,
            }));
        }
        if (parsed.pathname.endsWith('/api/asset-registry/asset-structures')) {
            return Promise.resolve(jsonResponse({structures: [structure]}));
        }
        if (parsed.pathname.endsWith('/api/asset-registry/status')) {
            return Promise.resolve(jsonResponse({ready: true, revision: 1, overview: {roots: [], features: []}}));
        }
        if (parsed.pathname.endsWith('/api/asset-registry/facets')) {
            return Promise.resolve(jsonResponse({kinds: [{id: 'image', name: 'image', count: assets.length}], categories: [], tags: [], collections: [], roots: []}));
        }
        if (parsed.pathname.endsWith('/api/asset-registry/folders')) return Promise.resolve(jsonResponse({folders: [], indexed_folders: []}));
        if (parsed.pathname.endsWith('/api/asset-registry/projects')) return Promise.resolve(jsonResponse({projects: []}));
        if (parsed.pathname.endsWith('/api/asset-registry/presets')) return Promise.resolve(jsonResponse({presets: []}));
        if (parsed.pathname.includes('/api/media-preview') || parsed.pathname.startsWith('/static/')) {
            return Promise.resolve(new Response('', {status: 204}));
        }
        return Promise.resolve(jsonResponse({items: [], total: 0, total_known: true}));
    };
}

function patchDomHost(document) {
    const createElement = document.createElement.bind(document);
    document.createElement = tag => {
        const element = createElement(tag);
        if (String(tag).toLowerCase() === 'template') element.content = element;
        return element;
    };

    const nodePrototype = Object.getPrototypeOf(document.body);
    const innerHTML = Object.getOwnPropertyDescriptor(nodePrototype, 'innerHTML');
    Object.defineProperty(nodePrototype, 'innerHTML', {
        configurable: true,
        get() { return innerHTML.get.call(this); },
        set(value) {
            this.childNodes.forEach(node => { node.parentNode = null; });
            innerHTML.set.call(this, value);
        },
    });
    const querySelectorAll = nodePrototype.querySelectorAll;
    nodePrototype.querySelectorAll = function(selector) {
        const scopeChild = String(selector).match(/^:scope\s*>\s*(.+)$/);
        if (scopeChild) return this.children.filter(child => child.matches(scopeChild[1]));
        return querySelectorAll.call(this, selector);
    };
    Object.defineProperty(nodePrototype, 'isConnected', {
        configurable: true,
        get() {
            let node = this;
            while (node) {
                if (node === document.documentElement) return true;
                node = node.parentNode;
            }
            return false;
        },
    });
    Object.defineProperty(nodePrototype, 'src', {
        configurable: true,
        get() { return this.getAttribute('src') || ''; },
        set(value) { this.setAttribute('src', String(value)); },
    });
    Object.defineProperty(nodePrototype, 'currentSrc', {
        configurable: true,
        get() { return this.src; },
    });
    const insertBefore = nodePrototype.insertBefore;
    nodePrototype.insertBefore = function(node, reference) {
        if (node === reference) return node;
        if (node.parentNode) node.parentNode.removeChild(node);
        return insertBefore.call(this, node, reference);
    };
    if (!nodePrototype.append) {
        nodePrototype.append = function(...nodes) { nodes.forEach(node => this.appendChild(node)); };
    }
}

function dispatchPointerGesture(document, target) {
    const sent = [];
    for (const type of ['pointerdown', 'pointerup', 'click']) {
        const event = {
            type,
            target,
            button: 0,
            detail: type === 'click' ? 1 : 0,
            pointerId: 1,
            pointerType: 'mouse',
            isPrimary: true,
            defaultPrevented: false,
            propagationStopped: false,
            preventDefault() { this.defaultPrevented = true; },
            stopPropagation() { this.propagationStopped = true; },
            stopImmediatePropagation() { this.propagationStopped = true; },
        };
        let node = target;
        while (node) {
            event.currentTarget = node;
            node.dispatchEvent(event);
            if (event.propagationStopped) break;
            node = node.parentNode;
        }
        if (!event.propagationStopped) {
            event.currentTarget = document;
            document.dispatchEvent(event);
        }
        sent.push(type);
    }
    assert.deepEqual(sent, ['pointerdown', 'pointerup', 'click']);
}

function seedRegistry(main, assets, structure) {
    main.setActiveTab('registry');
    main.setRegistryGroupMode('none');
    main.setRegistryLoading(false);
    main.setRegistryPageLoading(false);
    main.setRegistryAssets(assets);
    main.registryAssetStructures.clear();
    main.registryAssetStructures.set(structure.id, structure);
    main.registrySelectedIds.clear();
    main.setSelectedRegistryAssetId('');
    main.setRegistryDetail(null);
}

function setViewport(root, width = 900, height = 360) {
    const scroll = root.querySelector('.registry-assets-scroll');
    assert.ok(scroll, '资产滚动容器应由生产 render() 创建');
    scroll.clientWidth = width;
    scroll.clientHeight = height;
    const grid = scroll.querySelector('.registry-grid');
    if (grid) grid.clientWidth = width;
    return scroll;
}

function activePreviewImages(root) {
    return [...root.querySelectorAll('img[data-registry-preview-loaded="true"]')];
}

async function waitFor(predicate, message, timeout = 2000) {
    const deadline = Date.now() + timeout;
    while (Date.now() < deadline) {
        if (predicate()) return;
        await wait(10);
    }
    assert.fail(message);
}

async function makeHost() {
    const assets = makeRegistryAssets();
    const structure = makeVersionStructure(assets);
    const requests = [];
    const gates = {holdNextList: false, pendingLists: []};
    const host = installHost({
        embedded: false,
        theme: 'dark',
        boards: MOUNTED_BOARD,
        fetchImpl: registryFetch(assets, structure, requests, gates),
    });
    const previousCss = Object.getOwnPropertyDescriptor(globalThis, 'CSS');
    if (!globalThis.CSS) {
        globalThis.CSS = {escape: value => String(value).replace(/[^a-zA-Z0-9_-]/g, '\\$&')};
    }
    patchDomHost(host.document);
    const tree = makeProductTree();
    const main = await loadProduct(tree);
    main.openDeferredDomainGate();
    await main.gwLoadNow('organize');
    await main.gwLoadNow('settings');
    await wait(120);
    seedRegistry(main, assets, structure);
    main.render();
    setViewport(host.root);
    await wait(30);
    return {
        ...host,
        main,
        assets,
        structure,
        requests,
        gates,
        tree,
        dispose() {
            dropTree(tree);
            if (previousCss) Object.defineProperty(globalThis, 'CSS', previousCss);
            else delete globalThis.CSS;
        },
    };
}

for (const delayMs of PREVIEW_DELAY_CASES) {
    test(`QIN-673 AC1/AC2/AC3：${delayMs}ms 缩略图加载期间，栅格虚拟卡片可交互且重测保留节点`, async () => {
        const host = await makeHost();
        try {
            const {main, root, document, assets, structure} = host;
            const grid = root.querySelector('[data-registry-virtual-grid]');
            assert.ok(grid, '超过 120 项且存在结构卡时仍由生产渲染启用虚拟网格');
            const cards = [...grid.querySelectorAll(':scope > .registry-card[data-registry-card]')];
            assert.ok(cards.some(card => card.dataset.registryCard === `structure:${structure.id}`), '版本结构投影须进入同一个虚拟窗口');
            assert.ok(cards.length < assets.length, `DOM 只挂载当前窗口卡片，实际 ${cards.length}/${assets.length}`);

            await waitFor(() => activePreviewImages(root).length > 0,
                `生产预览队列应开始加载视口缩略图（deferred=${root.querySelectorAll('img[data-registry-preview-src]').length}, queued=${root.querySelectorAll('img[data-registry-preview-queued="true"]').length}, cards=${root.querySelectorAll('.registry-card').length}, requests=${host.requests.length}, loading=${main.registryLoading}）`);
            const pending = activePreviewImages(root);
            assert.ok(pending.length <= 4, `在途预览不得超过 4，实际 ${pending.length}`);
            const image = pending[0];
            const card = image.closest('[data-registry-card]');
            assert.ok(card, '实际缩略图应位于生产卡片 DOM 中');
            const cardId = card.dataset.registryCard;
            let loaded = false;
            const loadAfterDelay = (async () => {
                await wait(delayMs);
                image.complete = true;
                image.naturalWidth = 1600;
                image.naturalHeight = 900;
                image.dispatchEvent({type: 'load', target: image});
                loaded = true;
            })();

            dispatchPointerGesture(document, card);
            await wait(20);
            assert.equal(main.selectedRegistryAssetId, cardId, '完整指针手势必须在缩略图完成前更新选中状态');
            assert.equal(loaded, false, '断言交互时 1500ms / 5000ms 图像延迟仍在进行');
            assert.notEqual(image.complete, true, '目标图片在交互时仍未完成');

            await loadAfterDelay;
            await wait(20);
            assert.equal(image.dataset.registryPreviewFailed, undefined, '成功载入不得记为失败');
            globalThis.GWAssetVirtualGridHost.relayout();
            const retainedCard = [...root.querySelectorAll('.registry-card[data-registry-card]')]
                .find(node => node.dataset.registryCard === cardId);
            assert.equal(retainedCard, card, '实际重测后窗口内稳定键对应同一 article 节点');
            assert.equal(retainedCard.querySelector('img'), image, '实际重测后已加载缩略图节点身份不变');
        } finally {
            host.dispose();
        }
    });
}

test('QIN-673 AC1/AC4：列表视图超过 120 项仍可选中；筛选与导航手势及时派发 API，代际取消不误报', async () => {
    const host = await makeHost();
    try {
        const {main, root, document, assets, requests, gates} = host;
        const listButton = root.querySelector('[data-registry-layout="list"]');
        assert.ok(listButton, '生产工具栏必须提供列表视图切换');
        dispatchPointerGesture(document, listButton);
        await wait(20);
        assert.equal(main.registryLayout, 'list', '完整指针手势切换到列表视图');
        assert.ok(root.querySelectorAll('.registry-list-row').length > 120, '列表视图实际渲染全部已加载项及结构行');

        main.setRegistryKind('image');
        main.render();
        await waitFor(() => activePreviewImages(root).length > 0, '列表中的生产缩略图应进入同一并发队列');
        assert.ok(activePreviewImages(root).length <= 4, '列表视图在途缩略图仍受 4 并发上限约束');
        await main.refreshRegistryAssets();
        await wait(40);
        assert.equal(main.registryKind, 'image', '筛选 API 刷新后保留当前类型筛选');
        assert.ok(root.querySelector('[data-registry-clear="kind"]'), '当前筛选应有真实清除控件');

        await waitFor(() => activePreviewImages(root).length > 0, '列表刷新后缩略图仍应在队列中等待');
        const navigationImage = activePreviewImages(root)[0];
        let lateErrorDelayElapsed = false;
        const lateNavigationError = (async () => {
            await wait(5000);
            lateErrorDelayElapsed = true;
            navigationImage.dispatchEvent({type: 'error', target: navigationImage});
        })();
        if (!root.querySelector('[data-asset-tree-tab="mine"]')) {
            const treeMount = document.createElement('div');
            treeMount.innerHTML = main.renderAssetTreePanel();
            root.appendChild(treeMount);
        }
        const mineTab = root.querySelector('[data-asset-tree-tab="mine"]');
        assert.ok(mineTab, '生产资产树应提供“我的资源”导航标签');
        dispatchPointerGesture(document, mineTab);
        await wait(20);
        const renderedTree = document.createElement('div');
        renderedTree.innerHTML = main.renderAssetTreePanel();
        const selectedMineTab = renderedTree.querySelector('[data-asset-tree-tab="mine"]');
        assert.equal(selectedMineTab?.getAttribute('aria-selected'), 'true', '导航标签的当次指针手势更新真实选中态');
        assert.equal(lateErrorDelayElapsed, false, '导航手势发生在 5000ms 缩略图延迟尚未结束时');
        assert.equal(main.registryLayout, 'list', '导航重绘保持列表视图');
        assert.ok(root.querySelectorAll('.registry-list-row').length > 120, '导航重绘后列表数据仍在 DOM');

        const image = await (async () => {
            await waitFor(() => activePreviewImages(root).length > 0, '导航重绘后的新代预览应恢复调度');
            return activePreviewImages(root)[0];
        })();
        const row = image.closest('[data-registry-card]');
        assert.ok(row, '列表缩略图应保留稳定 registry card 键');
        const selectedId = row.dataset.registryCard;

        dispatchPointerGesture(document, row);
        await wait(20);
        assert.equal(main.selectedRegistryAssetId, selectedId, '列表卡片可在 5000ms 缩略图等待期间响应完整点击');
        assert.notEqual(image.complete, true, '当前卡片缩略图仍未完成');
        assert.equal(lateErrorDelayElapsed, false, '点击时 5000ms 延迟请求仍在途');
        gates.holdNextList = true;
        const clearFilter = root.querySelector('[data-registry-clear="kind"]');
        dispatchPointerGesture(document, clearFilter);
        await waitFor(() => gates.pendingLists.length === 1, '筛选手势应立即发出列表 API 请求，不等待缩略图');
        assert.equal(main.registryKind, '', '筛选状态由清除手势当场更新');
        assert.equal(main.registryLoading, true, 'API 请求未结算时生产列表处于加载状态');
        assert.ok(requests.some(request => new URL(request.url).pathname.endsWith('/api/asset-registry/assets')),
            '受控网络已观察到生产列表请求');

        image.dispatchEvent({type: 'error', target: image});
        assert.equal(image.dataset.registryPreviewFailed, undefined, '筛选代际取消后的迟到 error 不得误报为缩略图失败');
        await lateNavigationError;
        assert.equal(navigationImage.dataset.registryPreviewFailed, undefined, '导航代际取消后的 5000ms 迟到 error 不得误报为失败');
        gates.pendingLists.shift().resolve();
        await waitFor(() => !main.registryLoading, '筛选 API 结算后生产视图应恢复');
        await wait(30);
        assert.ok(activePreviewImages(root).length <= 4, '交互 API 结算后恢复调度仍不突破 4 并发');
        assert.equal(main.registryAssets.length, assets.length, 'API 结算后的列表回读数据完整');
    } finally {
        host.dispose();
    }
});
