// Copyright 2026 Gods-Workbench Authors
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//     http://www.apache.org/licenses/LICENSE-2.0
//
// Unless required by applicable law or agreed to in writing, software
// distributed under the License is distributed on an "AS IS" BASIS,
// WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
// See the License for the specific language governing permissions and
// limitations under the License.

// QIN-602：资产页嵌入态切换收集箱 / 挂载文件夹后，异步 root.innerHTML 覆盖同步插入的
// 总览卡片 + 工具栏。本文件全部为行为证据：在 vm 中执行汇聚点真实代码块，切换看板后
// 断言 DOM（顶栏份数、标题归属、内容归属、错误面板），不以源码文本匹配充当通过依据。

import assert from 'node:assert/strict';
import test from 'node:test';
import {createAssetViewHarness, extractFunction} from './qin-602-asset-view-harness.mjs';

const BOARDS = Object.freeze({
    inbox: {id: 'inbox', name: '收集箱', system: true},
    folderA: {id: 'folderA', name: '挂载文件夹A', system: false},
});

function assertSingleTopbar(harness, stage){
    assert.equal(harness.root.countClass('registry-overview-shell'), 1, `${stage}：总览卡片应恰好 1 份`);
    assert.equal(harness.root.countClass('registry-toolbar'), 1, `${stage}：工具栏应恰好 1 份`);
}

test('AC1: 资产总览 → 收集箱 → 挂载文件夹 → 回资产总览，每个视图顶栏各 1 份', async () => {
    const harness = createAssetViewHarness({tab: 'registry', boards: BOARDS});
    const steps = [
        ['registry', 'registry'],
        ['inbox', 'renderInboxManager'],
        ['folderA', 'renderCustomAssetLibrary'],
        ['registry', 'registry'],
    ];
    for(const [tab, view] of steps){
        harness.setTab(tab);
        harness.api.renderActiveAssetView();
        await harness.flush();
        assertSingleTopbar(harness, `${tab} 渲染完成后`);
        assert.ok(
            harness.root.innerHTML.includes(`data-asset-view="${view}"`),
            `${tab}：DOM 内容应属于当前视图 ${view}`,
        );
        assert.equal(harness.api.getSnapshot().tab, tab, `${tab}：顶栏快照应属于当前视图`);
        if(tab === 'inbox') assert.ok(harness.root.innerHTML.includes('<strong>收集箱</strong>'), '收集箱标题可见');
        if(tab === 'folderA') assert.ok(harness.root.innerHTML.includes('<strong>挂载文件夹A</strong>'), '挂载文件夹标题可见');
        if(tab === 'registry') assert.ok(!harness.root.innerHTML.includes('class="content-heading"'), '总览沿用 hideHeading 契约');
    }
});

test('AC2: 1500ms / 5000ms 慢加载乱序，顶栏不闪没、不重复、旧结果不覆盖、标题不串台', async () => {
    const harness = createAssetViewHarness({tab: 'inbox', boards: BOARDS});
    // 可控 deferred 表达票面两种人为延迟的先后语义：organize 1500ms 先落地、
    // inbox 5000ms 后落地。不真等墙钟，但落地顺序与乱序语义一致。
    const slowInbox = harness.deferDomain('inbox');
    const fastOrganize = harness.deferDomain('organize');

    harness.setTab('inbox');
    harness.api.renderActiveAssetView();
    assertSingleTopbar(harness, '收集箱发起时（模块尚未落地）');
    assert.ok(harness.root.innerHTML.includes('<strong>收集箱</strong>'), '发起时顶栏已挂载且标题冻结');

    harness.setTab('folderA');
    harness.api.renderActiveAssetView();
    assertSingleTopbar(harness, '挂载文件夹发起时');
    assert.ok(harness.root.innerHTML.includes('<strong>挂载文件夹A</strong>'), '新代际顶栏标题立即更新');
    assert.ok(!harness.root.innerHTML.includes('<strong>收集箱</strong>'), '旧标题不得残留');

    fastOrganize.resolve(harness.domainApi('folderA')); // 1500ms
    await harness.flush();
    assert.ok(harness.root.innerHTML.includes('data-asset-view="renderCustomAssetLibrary"'), '1500ms 结果应落地');
    assertSingleTopbar(harness, '1500ms 结果落地后');

    slowInbox.resolve(harness.domainApi('inbox')); // 5000ms：旧代际后落地
    await harness.flush();
    assert.ok(
        harness.root.innerHTML.includes('data-asset-view="renderCustomAssetLibrary"'),
        '5000ms 旧结果不得覆盖当前视图',
    );
    assert.ok(harness.root.innerHTML.includes('<strong>挂载文件夹A</strong>'), '顶栏标题不得串台');
    assert.ok(!harness.root.innerHTML.includes('<strong>收集箱</strong>'), '旧视图标题不得回来');
    assertSingleTopbar(harness, '5000ms 旧结果落地后');
});

test('AC2: 切走后旧 Promise 回来不得写入当前视图（取消）', async () => {
    const harness = createAssetViewHarness({tab: 'inbox', boards: BOARDS});
    const pending = harness.deferDomain('inbox');
    harness.setTab('inbox');
    harness.api.renderActiveAssetView();
    assertSingleTopbar(harness, '切走前');

    harness.setTab('registry');
    harness.api.renderActiveAssetView();
    pending.resolve(harness.domainApi('inbox'));
    await harness.flush();

    assert.ok(harness.root.innerHTML.includes('data-asset-view="registry"'), '当前仍应是总览内容');
    assert.ok(!harness.root.innerHTML.includes('data-asset-view="renderInboxManager"'), '旧 Promise 不得写入');
    assertSingleTopbar(harness, '取消后');
    assert.equal(harness.api.getSnapshot().tab, 'registry');
});

test('AC2: 连续快速切换不累积多排顶栏', async () => {
    const harness = createAssetViewHarness({tab: 'inbox', boards: BOARDS});
    for(const domain of ['inbox', 'organize', 'lut']) harness.deferDomain(domain);
    for(const tab of ['inbox', 'folderA', 'lut', 'scripts']){
        harness.setTab(tab);
        harness.api.renderActiveAssetView();
        await harness.flush();
        assertSingleTopbar(harness, `快速切到 ${tab} 后`);
    }
});

test('AC4: inbox 模块加载失败时顶栏仍在且有可见错误提示', async () => {
    const harness = createAssetViewHarness({tab: 'inbox', boards: BOARDS});
    const pending = harness.deferDomain('inbox');
    harness.api.renderActiveAssetView();
    assertSingleTopbar(harness, '失败前');
    pending.reject(new Error('模块加载失败'));
    await harness.flush();

    assertSingleTopbar(harness, '失败后');
    assert.match(harness.root.innerHTML, /role="alert"/, '错误面板须可感知');
    assert.match(harness.root.innerHTML, /data-asset-view-error/, '错误面板须有稳定标记');
    assert.match(harness.root.innerHTML, /视图加载失败/, '须显示失败文案');
    assert.match(harness.root.innerHTML, /模块加载失败/, '须显示失败原因');
    assert.ok(!harness.root.innerHTML.includes('data-asset-view="renderInboxManager"'), '不得伪装成功');
    assert.ok(harness.root.innerHTML.length > 100, '不得空白');
});

test('PM 决策 4: board 无效旁路经 render() 汇聚点，嵌入态顶栏不丢失', async () => {
    const harness = createAssetViewHarness({tab: 'folderA', boards: BOARDS});
    harness.api.renderCustomAssetLibrary(null);
    await harness.flush();

    assert.deepEqual(harness.calls.setActiveTab, ['registry'], '旁路应切回总览');
    assert.equal(harness.calls.render.length, 1, '旁路必须经 render() 汇聚点');
    assert.equal(harness.context.activeTab, 'registry', '旁路后当前看板应为总览');
    assertSingleTopbar(harness, '旁路回退后');
    assert.ok(harness.root.innerHTML.includes('data-asset-view="registry"'), '旁路回退后应显示总览内容');
    assert.equal(harness.api.getSnapshot().tab, 'registry', '顶栏快照应按 registry 重新冻结');
    assert.equal(harness.api.getSnapshot().title, '资产总览');

    const plain = createAssetViewHarness({embedded: false, tab: 'folderA', boards: BOARDS});
    plain.api.renderCustomAssetLibrary(null);
    await plain.flush();
    assert.equal(plain.calls.render.length, 1, '非嵌入态同样回到汇聚点');
    assert.ok(plain.root.innerHTML.includes('data-asset-view="registry"'), '非嵌入态回退后仍有总览内容');
});

test('PM 决策 1: 其余 7 个异步分支统一收尾，顶栏不回退', async () => {
    const cases = [
        ['scripts', 'renderScriptAssetsManager'],
        ['projects', 'renderProjectManager'],
        ['prompts', 'renderPromptManager'],
        ['lut', 'renderLutManager'],
        ['workflows', 'renderWorkflowManager'],
        ['local', 'renderLocalManager'],
        ['canvas-assets', 'renderCanvasAssetsManager'],
    ];
    for(const [tab, renderer] of cases){
        const harness = createAssetViewHarness({tab, boards: BOARDS});
        harness.api.renderActiveAssetView();
        await harness.flush();
        assert.equal(
            harness.calls.viewRenders.filter(call => call.name === renderer).length, 1,
            `${tab} 应执行 ${renderer}`,
        );
        assertSingleTopbar(harness, `${tab} 渲染完成后`);
        assert.equal(harness.api.getSnapshot().tab, tab, `${tab} 顶栏快照应属于当前视图`);
    }
});

test('同代际重复收尾不插入第二排顶栏', async () => {
    const harness = createAssetViewHarness({tab: 'inbox', boards: BOARDS});
    harness.api.renderActiveAssetView();
    await harness.flush();
    const seq = harness.api.getSeq();
    harness.api.restoreEmbeddedAssetTopbar(seq);
    harness.api.restoreEmbeddedAssetTopbar(seq);
    assertSingleTopbar(harness, '重复收尾后');
});

test('护栏自检：去掉代际检查后旧结果会覆盖新视图（证明用例确有约束力）', async () => {
    const mutated = extractFunction('runAssetViewRenderer')
        .replace('if(seq !== assetViewRenderSeq) return;', '/* QIN-602 mutation: generation check removed */');
    assert.ok(!mutated.includes('if(seq !== assetViewRenderSeq) return;'), '变异体必须真的删掉代际检查');
    const harness = createAssetViewHarness({
        tab: 'inbox', boards: BOARDS, patches: {runAssetViewRenderer: mutated},
    });
    const oldInbox = harness.deferDomain('inbox');
    const newOrganize = harness.deferDomain('organize');
    harness.setTab('inbox');
    harness.api.renderActiveAssetView();
    harness.setTab('folderA');
    harness.api.renderActiveAssetView();
    newOrganize.resolve(harness.domainApi('folderA'));
    await harness.flush();
    oldInbox.resolve(harness.domainApi('inbox'));
    await harness.flush();
    assert.ok(
        harness.root.innerHTML.includes('data-asset-view="renderInboxManager"'),
        '无代际检查时旧结果确实覆盖新视图；正常用例的「不得覆盖」断言因此不是空断言',
    );
});

test('护栏自检：不重置容器时快速切换会累积多排顶栏', async () => {
    const resetLine = "if(document.documentElement.classList.contains('vault-shell-embedded')) root.innerHTML = '';";
    const mutated = extractFunction('renderActiveAssetView').replace(resetLine, '');
    assert.ok(!mutated.includes(resetLine), '变异体必须真的删掉容器重置');
    const harness = createAssetViewHarness({
        tab: 'inbox', boards: BOARDS, patches: {renderActiveAssetView: mutated},
    });
    for(const domain of ['inbox', 'organize', 'lut']) harness.deferDomain(domain);
    for(const tab of ['inbox', 'folderA', 'lut']){
        harness.setTab(tab);
        harness.api.renderActiveAssetView();
        await harness.flush();
    }
    assert.ok(
        harness.root.countClass('registry-toolbar') > 1,
        '不重置容器时确实出现多排顶栏；正常用例的「恰好 1 份」断言因此不是空断言',
    );
});
