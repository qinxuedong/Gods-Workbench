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
 * QIN-602：嵌入态切换看板后顶部总览与工具栏被异步渲染覆盖。
 *
 * 全部用例都跑真实产品模块（见 ./qin-602-asset-view-harness.mjs）：整棵 web/js
 * 复制到临时目录后用原生 import() 加载，再经 window.GWAssetTreeBridge.activate /
 * 真实 render() 驱动。不使用源码切片、vm 或函数替身。
 *
 * 【r3】针对独立复审的 4 项重要意见补上：真实副作用请求护栏（qin-547 内）、
 * 多域延迟 + 连续快速切换（删清容器必红）、精确失败日志前缀 + 过期代际失败不写 DOM、
 * inbox 失败后真实入口可回到总览。另恢复 7 个异步分支冒烟与防串台护栏。
 */

import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import path from 'node:path';
import {
    MAIN_SOURCE, MAIN_REL, bootProduct, captureConsoleErrors, createAssetViewHost,
    dropTree, inboxSideEffectFetch, installHost, loadProduct, makeProductTree,
    topbar, toolbarTitle, wait,
} from './qin-602-asset-view-harness.mjs';

/** 具体域名前缀：不能用 includes('inbox')，依赖链的 gwCall 失败日志会误命中。 */
const FAILURE_PREFIX = '资产视图渲染失败: inbox';
/** 模块加载失败时带上 URL，用来验证失败面板不会把动态 import 地址摊给用户。 */
const INBOX_FAIL = [{
    file: 'web/js/modules/asset-manager/inbox.js',
    prepend: "throw new Error('inbox 模块加载失败 https://asset.invalid/js/inbox.js');\n",
}];
const LOG_LINE = "    console.error(`资产视图渲染失败: ${domain || 'unknown'}`, error);";
const RESET_LINE = "    if(document.documentElement.classList.contains('vault-shell-embedded')) root.innerHTML = '';";
const BACK_SYNC = "        if(value === 'registry') syncActivateRegistryOverview();";
const TAB_GUARD = '    if(!snapshot || snapshot.seq !== seq || seq !== assetViewRenderSeq || snapshot.tab !== activeTab) return;';
/** 树节点入口的副作用派发：删掉它后「清未读角标」请求必须消失。 */
const INBOX_CHAIN = "            return gwCall('inbox', 'openAssetInbox');";
const PENDING_LOADER = '() => new Promise(() => {})';
/** 真实加载边界注入：域模块仍真实 import，只是 loader 永远不落地（模拟慢网络/挂起）。 */
const SLOW_DOMAINS = {inbox: PENDING_LOADER, organize: PENDING_LOADER, lut: PENDING_LOADER};
const QUICK_TABS = ['inbox', 'lut', 'library_demo', 'scripts', 'projects'];

/**
 * 建一个「从引导段开始就捕获 console.error」的真实宿主。
 * 域失败可能发生在 bootProduct、延后域队列或几百毫秒后的迟到代际里，
 * 只包住点击那一下会漏掉真正的失败日志。
 */
async function bootCaptured({sourceEdits = [], fileEdits = [], loaderPatches = {}} = {}) {
    const host = installHost();
    const tree = makeProductTree(loaderPatches, sourceEdits, fileEdits);
    const main = await loadProduct(tree);
    const captured = captureConsoleErrors();
    try {
        await bootProduct(host.document);
    } catch (error) {
        captured.stop();
        dropTree(tree);
        throw error;
    }
    return {host, tree, main, captured};
}

test('QIN-602：临时模块树必须是仓库源码的逐字节副本（加载结论有效性的前置条件）', () => {
    const tree = makeProductTree();
    assert.equal(readFileSync(path.join(tree, MAIN_REL), 'utf8'), MAIN_SOURCE, '临时树与仓库源码不一致');
    dropTree(tree);
});

test('QIN-602 AC1/AC4：真实入口切收集箱 → 挂载文件夹 → 总览，顶栏始终各 1 份', async () => {
    const host = installHost();
    const tree = makeProductTree();
    const main = await loadProduct(tree);
    await bootProduct(host.document);

    main.render();
    await wait(60);
    assert.deepEqual(topbar(host.root), {overview: 1, toolbar: 1, marked: 2}, '总览：顶栏各 1 份');

    window.GWAssetTreeBridge.activate('mine:inbox');
    await wait(80);
    assert.deepEqual(topbar(host.root), {overview: 1, toolbar: 1, marked: 2}, '收集箱：顶栏各 1 份');
    assert.ok(host.root.innerHTML.includes('inbox-content'), '收集箱内容已渲染到顶栏之后');
    assert.equal(toolbarTitle(host.root), '资源库', '收集箱没有独立看板，顶栏标题取兜底「资源库」');
    assert.equal(main.activeTab, 'inbox', '状态已切到收集箱');

    main.setAssetTreeSelectedKey('mine:board:library_demo');
    window.GWAssetTreeBridge.activate('mine:board:library_demo');
    await wait(120);
    assert.deepEqual(topbar(host.root), {overview: 1, toolbar: 1, marked: 2}, '挂载文件夹：顶栏各 1 份');
    assert.equal(toolbarTitle(host.root), '挂载文件夹A', '挂载文件夹顶栏标题取真实看板名');
    assert.equal(main.activeTab, 'library_demo', '状态已切到挂载文件夹');

    window.GWAssetTreeBridge.activate('registry');
    await wait(120);
    assert.deepEqual(topbar(host.root), {overview: 1, toolbar: 1, marked: 2}, '回到总览：顶栏各 1 份');
    assert.equal(main.activeTab, 'registry', '状态已回到总览');

    dropTree(tree);
});

/**
 * 【QIN-602 r4】树节点点击的真实副作用护栏。
 * 第 3 轮把这条等价性护栏放在 qin-547 里改写了旧断言，超出 PM 预授权范围；本轮把 qin-547
 * 恢复为 master 原文，护栏迁到本票自有测试文件（不涉及任何旧断言改写）。
 */
test('QIN-602 AC4 等价护栏：真实入口点收集箱必须拉列表并清未读角标', async () => {
    const host = await createAssetViewHost({tab: 'registry', fetchImpl: inboxSideEffectFetch});
    try {
        globalThis.GWAssetTreeBridge.activate('mine:inbox');
        await wait(150);
        assert.equal(host.main.activeTab, 'inbox', '真实入口必须切到收集箱');
        assert.ok(
            host.requests.some(r => r.method === 'GET' && r.url.includes('/api/asset-inbox?limit=500')),
            '树节点点击必须真的拉收集箱列表（不是只改了个状态字段）',
        );
        assert.ok(
            host.requests.some(r => r.method === 'POST' && r.url.includes('/api/asset-inbox/viewed')),
            '列表返回未读时必须真的清角标；副作用派发被删掉这条必须变红',
        );
        assert.deepEqual(topbar(host.root), {overview: 1, toolbar: 1, marked: 2}, '副作用落地后顶栏仍各 1 份');
    } finally {
        host.dispose();
    }
});

test('QIN-602 AC4 护栏：删掉树节点副作用派发后清角标请求必须消失', async () => {
    const host = await createAssetViewHost({
        tab: 'registry',
        fetchImpl: inboxSideEffectFetch,
        sourceEdits: [{from: INBOX_CHAIN, to: '            return undefined;'}],
    });
    try {
        globalThis.GWAssetTreeBridge.activate('mine:inbox');
        await wait(150);
        assert.equal(host.main.activeTab, 'inbox', '删掉副作用不影响主文件侧同步落状态（AC4 仍成立）');
        assert.ok(!host.requests.some(r => r.url.includes('/api/asset-inbox/viewed')), '删掉派发后不得再清角标');
    } finally {
        host.dispose();
    }
});

test('QIN-602 AC4：模块加载失败时仍切视图、保留顶栏、显示可操作提示并留下精确域名日志', async () => {
    const {host, tree, main, captured} = await bootCaptured({fileEdits: INBOX_FAIL});
    try {
        main.render();
        await wait(60);
        assert.equal(topbar(host.root).overview, 1, '失败前总览顶栏 1 份');

        window.GWAssetTreeBridge.activate('mine:inbox');
        await wait(150);

        assert.equal(main.activeTab, 'inbox', '模块加载失败也必须切到收集箱（否则错误面板永远走不到）');
        const panel = host.root.querySelector('[data-asset-view-error]');
        assert.ok(panel, '必须显示可见的失败面板');
        assert.equal(panel.getAttribute('role'), 'alert', '失败面板须 role="alert"');
        assert.ok(host.root.innerHTML.includes('inbox 模块加载失败'), '失败面板须写明真实原因');
        assert.equal((host.root.innerHTML.match(/inbox 模块加载失败/g) || []).length, 1, '原因只出现一次，不重复刷屏');
        assert.ok(!host.root.innerHTML.includes('https://asset.invalid'), '不得把动态 import 的模块 URL 摊给用户');
        assert.ok(host.root.innerHTML.includes('可点击左侧「资产总览」返回，或刷新页面后重试'), '须给出可操作的下一步');
        assert.deepEqual(topbar(host.root), {overview: 1, toolbar: 1, marked: 2}, '失败时顶栏仍各 1 份');
        assert.ok(captured.withPrefix(FAILURE_PREFIX).length >= 1, '失败必须留下带具体域名的日志');
    } finally {
        captured.stop();
        dropTree(tree);
    }
});

test('QIN-602 AC4 护栏：删掉失败日志后精确前缀断言必须变红（防止空断言）', async () => {
    const {host, tree, main, captured} = await bootCaptured({
        sourceEdits: [{from: LOG_LINE, to: '    // 变异：删掉失败日志'}],
        fileEdits: INBOX_FAIL,
    });
    try {
        main.render();
        window.GWAssetTreeBridge.activate('mine:inbox');
        await wait(150);
        assert.ok(host.root.querySelector('[data-asset-view-error]'), '面板照常出现，差异只来自日志');
        assert.ok(captured.logs.length > 0, '捕获器本身仍在工作，不是因为没捕到任何日志才通过');
        assert.equal(captured.withPrefix(FAILURE_PREFIX).length, 0, '删掉 console.error 后精确前缀日志应消失');
    } finally {
        captured.stop();
        dropTree(tree);
    }
});

test('QIN-602 AC4：过期代际的失败仍留日志，但不得写 DOM / 覆盖最后选中的视图', async () => {
    const {host, tree, main, captured} = await bootCaptured({fileEdits: [{
        file: 'web/js/modules/asset-manager/inbox.js',
        prepend: "await new Promise(resolve => setTimeout(resolve, 300));\nthrow new Error('inbox 模块加载失败');\n",
    }]});
    try {
        main.render();
        window.GWAssetTreeBridge.activate('mine:inbox');
        await wait(60);
        main.setActiveTab('registry');
        main.render();
        await wait(600);

        assert.ok(captured.withPrefix(FAILURE_PREFIX).length >= 1, '过期失败仍须留痕，便于排查');
        assert.equal(main.activeTab, 'registry', '当前视图仍是用户最后选中的总览');
        assert.ok(!host.root.querySelector('[data-asset-view-error]'), '过期失败不得画错误面板');
        assert.ok(!host.root.innerHTML.includes('当前视图暂时无法加载'), '过期失败不得写失败文案');
        assert.ok(host.root.innerHTML.includes('registry-content'), '总览内容不受影响');
        assert.deepEqual(topbar(host.root), {overview: 1, toolbar: 1, marked: 2}, '顶栏仍各 1 份');
    } finally {
        captured.stop();
        dropTree(tree);
    }
});

test('QIN-602 AC4：inbox 模块加载失败后，真实入口点总览仍可回到总览', async () => {
    const {host, tree, main, captured} = await bootCaptured({fileEdits: INBOX_FAIL});
    try {
        main.render();
        window.GWAssetTreeBridge.activate('mine:inbox');
        await wait(150);
        assert.equal(main.activeTab, 'inbox', '前置：已停在收集箱');
        assert.ok(host.root.querySelector('[data-asset-view-error]'), '前置：失败面板可见');

        // organize → media-canvas → canvas-bridge → inbox 的静态依赖链让 organize 一起加载失败，
        // 只靠 gwCall('organize', 'activateAssetBoard', 'registry') 会把用户锁死在错误面板。
        window.GWAssetTreeBridge.activate('registry');
        await wait(200);
        assert.equal(main.activeTab, 'registry', '总览入口必须同步落状态并渲染，不依赖 organize');
        assert.equal(main.assetTreeSelectedKey, 'registry', '树选中态同步回到总览');
        assert.ok(!host.root.querySelector('[data-asset-view-error]'), '失败面板必须被真实重绘冲掉');
        assert.ok(host.root.innerHTML.includes('registry-content'), '总览内容真实渲染');
        assert.deepEqual(topbar(host.root), {overview: 1, toolbar: 1, marked: 2}, '回总览后顶栏仍各 1 份');
    } finally {
        captured.stop();
        dropTree(tree);
    }
});

test('QIN-602 AC4 护栏：删掉总览入口的同步落状态后必须卡在失败面板', async () => {
    const {host, tree, main, captured} = await bootCaptured({
        sourceEdits: [{from: BACK_SYNC, to: '        // 变异：删掉总览同步落状态'}],
        fileEdits: INBOX_FAIL,
    });
    try {
        main.render();
        window.GWAssetTreeBridge.activate('mine:inbox');
        await wait(150);
        window.GWAssetTreeBridge.activate('registry');
        await wait(200);
        assert.notEqual(main.activeTab, 'registry', '没有同步落状态时回不到总览');
        assert.ok(host.root.querySelector('[data-asset-view-error]'), '失败面板仍留在屏幕上');
    } finally {
        captured.stop();
        dropTree(tree);
    }
});

test('QIN-602 AC2：收集箱 1500ms 迟到，已切回总览时不得覆盖内容与顶栏', async () => {
    const host = installHost();
    const tree = makeProductTree({}, [], [{
        file: 'web/js/modules/asset-manager/inbox.js',
        prepend: 'await new Promise(resolve => setTimeout(resolve, 1500));\n',
    }]);
    const main = await loadProduct(tree);
    await bootProduct(host.document);

    main.render();
    await wait(60);
    window.GWAssetTreeBridge.activate('mine:inbox');
    await wait(50);
    main.setActiveTab('registry');
    main.render();
    await wait(1700);

    assert.equal(main.activeTab, 'registry', '用户已回到总览');
    assert.deepEqual(topbar(host.root), {overview: 1, toolbar: 1, marked: 2}, '迟到结果不得叠加顶栏');
    assert.ok(!host.root.innerHTML.includes('inbox-content'), '迟到结果不得写进总览视图');

    dropTree(tree);
});

test('QIN-602 AC2：收集箱 5000ms 迟到（取消语义），顶栏与内容仍属最后选中视图', async () => {
    const host = installHost();
    const tree = makeProductTree({}, [], [{
        file: 'web/js/modules/asset-manager/inbox.js',
        prepend: 'await new Promise(resolve => setTimeout(resolve, 5000));\n',
    }]);
    const main = await loadProduct(tree);
    await bootProduct(host.document);

    main.render();
    await wait(60);
    window.GWAssetTreeBridge.activate('mine:inbox');
    await wait(50);
    main.setActiveTab('registry');
    main.render();
    await wait(5200);

    assert.equal(main.activeTab, 'registry', '用户已回到总览');
    assert.deepEqual(topbar(host.root), {overview: 1, toolbar: 1, marked: 2}, '5000ms 迟到结果不得叠加顶栏');
    assert.ok(!host.root.innerHTML.includes('inbox-content'), '5000ms 迟到结果不得写进总览视图');

    dropTree(tree);
});

test('QIN-602 AC2：多域加载挂起时连续快速切换，不累积多排顶栏也不串标题', async () => {
    const {host, tree, main, captured} = await bootCaptured({loaderPatches: SLOW_DOMAINS});
    try {
        main.render();
        for (const tab of QUICK_TABS) {
            main.setActiveTab(tab);
            main.render();
            assert.deepEqual(topbar(host.root), {overview: 1, toolbar: 1, marked: 2}, `快速切到 ${tab} 后顶栏仍各 1 份`);
            if (tab === 'library_demo') {
                assert.equal(toolbarTitle(host.root), '挂载文件夹A', '顶栏标题必须属于最后选中的视图');
            }
        }
        assert.ok(!host.root.querySelector('[data-asset-view-error]'), '挂起中的域不得误判成加载失败');
        // 让 bootProduct 的 rAF / 延后域队列回调在本宿主仍装着时落地，避免卸载后触发异步噪声。
        await wait(20);
    } finally {
        captured.stop();
        dropTree(tree);
    }
});

test('QIN-602 AC2 护栏：删掉新代际清容器那一行后，快速切换必须累积多排顶栏', async () => {
    const {host, tree, main, captured} = await bootCaptured({
        loaderPatches: SLOW_DOMAINS,
        sourceEdits: [{from: RESET_LINE, to: '    // 变异：删掉清容器'}],
    });
    try {
        main.render();
        const counts = [];
        for (const tab of QUICK_TABS) {
            main.setActiveTab(tab);
            main.render();
            counts.push(topbar(host.root).overview);
        }
        assert.deepEqual(counts, [2, 3, 4, 5, 6], '不清容器时旧顶栏会逐次累积，说明上一条用例确有约束力');
        await wait(20);
    } finally {
        captured.stop();
        dropTree(tree);
    }
});

test('QIN-602 PM 决策 1：其余 7 个异步分支统一收尾，顶栏不回退', async () => {
    const host = await createAssetViewHost({tab: 'registry'});
    try {
        const cases = [
            ['scripts', 'renderScriptAssetsManager'],
            ['projects', 'renderProjectManager'],
            ['prompts', 'renderPromptManager'],
            ['lut', 'renderLutManager'],
            ['workflows', 'renderWorkflowManager'],
            ['local', 'renderLocalManager'],
            ['canvas-assets', 'renderCanvasAssetsManager'],
        ];
        for (const [tab] of cases) {
            await host.setTab(tab);
            assert.deepEqual(topbar(host.root), {overview: 1, toolbar: 1, marked: 2}, `${tab} 渲染完成后顶栏各 1 份`);
            assert.ok(!host.root.querySelector('[data-asset-view-error]'), `${tab} 不得误报加载失败`);
            assert.ok(host.root.innerHTML.length > 10000, `${tab} 内容必须真实渲染`);
        }
    } finally {
        host.dispose();
    }
});

test('QIN-602：organize 的 board 无效旁路必须经汇聚点（顶栏不丢）', async () => {
    const host = installHost();
    const tree = makeProductTree();
    const main = await loadProduct(tree);
    await bootProduct(host.document);

    main.render();
    await wait(60);
    main.setActiveTab('library_demo');
    main.render();
    await wait(120);
    assert.deepEqual(topbar(host.root), {overview: 1, toolbar: 1, marked: 2}, '挂载文件夹顶栏各 1 份');

    // 真实旁路：看板已被删除，activeTab 还停在旧 id，render() 必须经无效看板旁路回总览
    main.setActiveTab('library_removed');
    main.render();
    await wait(120);
    assert.equal(main.activeTab, 'registry', '无效看板旁路必须回到资产总览');
    assert.deepEqual(topbar(host.root), {overview: 1, toolbar: 1, marked: 2}, '旁路后顶栏仍各 1 份');

    dropTree(tree);
});

test('QIN-602：activeTab 已变但尚未开新代际时，不得补回上一个 tab 的顶栏', async () => {
    const {host, tree, main, captured} = await bootCaptured({fileEdits: [{
        file: 'web/js/modules/asset-manager/inbox.js',
        prepend: 'await new Promise(resolve => setTimeout(resolve, 200));\n',
    }]});
    try {
        main.render();
        window.GWAssetTreeBridge.activate('mine:inbox');
        await wait(40);
        // 只改状态、不 render()：模拟 activateAssetBoard 里 setActiveTab(id) → render() 之间的窗口。
        main.setActiveTab('registry');
        await wait(500);
        assert.equal(main.activeTab, 'registry', '状态已经切到总览');
        assert.equal(topbar(host.root).overview, 0, '快照属于收集箱，不得补进当前视图');
        assert.notEqual(toolbarTitle(host.root), '资源库', '不得把上一个 tab 的标题串到当前视图');
    } finally {
        captured.stop();
        dropTree(tree);
    }
});

test('QIN-602 护栏：去掉 snapshot.tab 防串台条件后，旧 tab 顶栏必须被补回来', async () => {
    const {host, tree, main, captured} = await bootCaptured({
        sourceEdits: [{from: TAB_GUARD, to: '    if(!snapshot || snapshot.seq !== seq || seq !== assetViewRenderSeq) return;'}],
        fileEdits: [{
            file: 'web/js/modules/asset-manager/inbox.js',
            prepend: 'await new Promise(resolve => setTimeout(resolve, 200));\n',
        }],
    });
    try {
        main.render();
        window.GWAssetTreeBridge.activate('mine:inbox');
        await wait(40);
        main.setActiveTab('registry');
        await wait(500);
        assert.equal(topbar(host.root).overview, 1, '没有防串台条件就会补回旧顶栏');
        assert.equal(toolbarTitle(host.root), '资源库', '补回的是收集箱（上一个 tab）的标题');
    } finally {
        captured.stop();
        dropTree(tree);
    }
});

test('QIN-602 反证：删掉 render() 里对汇聚点的调用，AC1 必须变红', async () => {
    const host = installHost();
    const tree = makeProductTree({}, [{
        from: '    gwCall(\'preview\', \'syncRegistryDetailGridWidth\');\n    renderActiveAssetView();',
        to: '    gwCall(\'preview\', \'syncRegistryDetailGridWidth\');',
    }]);
    const main = await loadProduct(tree);
    await bootProduct(host.document);

    main.render();
    await wait(60);
    window.GWAssetTreeBridge.activate('mine:inbox');
    await wait(80);

    assert.notDeepEqual(topbar(host.root), {overview: 1, toolbar: 1, marked: 2}, '删掉汇聚点调用后仍全绿说明断言无效');
    dropTree(tree);
});

test('QIN-602 反证：去掉代际作废，迟到结果必须把总览覆盖成收集箱', async () => {
    const host = installHost();
    const tree = makeProductTree({}, [{
        from: '        if(seq !== assetViewRenderSeq) return;\n        let outcome;',
        to: '        let outcome;',
    }], [{
        file: 'web/js/modules/asset-manager/inbox.js',
        prepend: 'await new Promise(resolve => setTimeout(resolve, 300));\n',
    }]);
    const main = await loadProduct(tree);
    await bootProduct(host.document);

    main.render();
    await wait(60);
    window.GWAssetTreeBridge.activate('mine:inbox');
    await wait(50);
    // 直接切状态 + render()：避免总览入口自身的 organize 副作用在本用例里再重绘一帧，
    // 让反证只观察「旧代际 Promise 是否写进当前视图」这一件事。
    main.setActiveTab('registry');
    main.render();
    await wait(600);

    assert.ok(host.root.innerHTML.includes('inbox-content'), '去掉代际检查后迟到结果应当写进当前视图');
    dropTree(tree);
});

test('QIN-602 反证：去掉内容落地后的补顶栏，收集箱顶栏必须变 0 份', async () => {
    const host = installHost();
    // 两条补顶栏调用（同步一次 + 渲染器返回值落地后一次）一起去掉
    const tree = makeProductTree({}, [{
        from: '        restoreEmbeddedAssetTopbar(seq);\n        // 接住返回值',
        to: '        // 接住返回值',
    }, {
        from: '        Promise.resolve(outcome).then(() => restoreEmbeddedAssetTopbar(seq)).catch(fail);',
        to: '        Promise.resolve(outcome).catch(fail);',
    }]);
    const main = await loadProduct(tree);
    await bootProduct(host.document);

    main.render();
    await wait(60);
    window.GWAssetTreeBridge.activate('mine:inbox');
    await wait(120);

    assert.equal(topbar(host.root).overview, 0, '去掉补顶栏后收集箱顶栏应消失，否则断言无效');
    assert.ok(host.root.innerHTML.includes('inbox-content'), '内容本身仍应渲染，说明差异只来自补顶栏');
    dropTree(tree);
});
