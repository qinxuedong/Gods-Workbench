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
 */

import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import path from 'node:path';
import {
    MAIN_SOURCE, MAIN_REL, bootProduct, dropTree, installHost, loadProduct,
    makeProductTree, topbar, toolbarTitle, wait,
} from './qin-602-asset-view-harness.mjs';


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

test('QIN-602 AC4：收集箱模块加载失败时，真实入口仍切视图、保留顶栏并显示可见错误', async () => {
    const host = installHost();
    // 收集箱模块文件本身加载失败（等价于该域模块下载/求值失败）。用真实 import 失败
    // 而不是给加载器塞替身：拉不到模块时产品必须自己把状态切过去并画错误面板。
    const tree = makeProductTree({}, [], [{
        file: 'web/js/modules/asset-manager/inbox.js',
        prepend: "throw new Error('inbox 模块加载失败');\n",
    }]);
    const main = await loadProduct(tree);
    await bootProduct(host.document);

    main.render();
    await wait(60);
    assert.equal(topbar(host.root).overview, 1, '失败前总览顶栏 1 份');

    const logged = [];
    const originalError = console.error;
    console.error = (...args) => { logged.push(args.map(String).join(' ')); };
    try {
        window.GWAssetTreeBridge.activate('mine:inbox');
        await wait(120);
    } finally {
        console.error = originalError;
    }

    assert.equal(main.activeTab, 'inbox', '模块加载失败也必须切到收集箱（否则错误面板永远走不到）');
    assert.ok(host.root.querySelector('[data-asset-view-error]'), '必须显示可见的失败面板');
    assert.ok(host.root.querySelector('[data-asset-view-error]')?.getAttribute('role') === 'alert', '失败面板须 role="alert"');
    assert.ok(host.root.innerHTML.includes('inbox 模块加载失败'), '失败面板须写明真实原因');
    assert.deepEqual(topbar(host.root), {overview: 1, toolbar: 1, marked: 2}, '失败时顶栏仍各 1 份');
    assert.ok(logged.some(line => line.includes('inbox')), '失败必须留下日志，不能静默吞掉');

    dropTree(tree);
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
    window.GWAssetTreeBridge.activate('registry');
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
