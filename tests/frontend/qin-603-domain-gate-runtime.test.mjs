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
 * QIN-603 返工：延后域网关的**真实模块入口**回归护栏。
 *
 * 前一轮把网关源码切片后在 vm 里重建，等于测了一份复制品（GW-DoD-v1 §4.2 禁止）。
 * 这一版直接 import 产品主模块，调它自己导出的 gwCall / gwLoad / openDeferredDomainGate，
 * 用真实 Promise.race 与真实计时器验证：
 *   1. 单个延后域任务卡死时，15s 后只 reject 该任务（不是整条队列）；
 *   2. 后续任务照常按入队顺序继续（严格 FIFO 不被悬挂任务破坏）；
 *   3. 成功路径清掉自己的超时计时器（不残留 handle）；
 *   4. 目录导航「最后一次点击赢」：排队中的旧导航被后来者取代，放行后只执行最新一次。
 *
 * 只对「外部依赖」做边界替换：域加载器（动态 import 那一跳）由夹具注入，域函数面仍注册进
 * GW_DEFERRED、经产品网关真实派发。被测的网关本体（排队、FIFO、有界等待、去抖）一行没换。
 */

import {test} from 'node:test';
import assert from 'node:assert/strict';
import {
    dropTree, installHost, loadProduct, makeProductTree,
} from './qin-602-asset-view-harness.mjs';

/** 域加载器永远不落地：模拟 5k+ 资产下 /governance/suggestions 60s 不返回的真实故障。 */
const HANGING_GOVERNANCE_LOADER = '() => new Promise(() => {})';

/**
 * 记录型 settings 域：替换的只是「动态 import 这一跳」，域函数面仍走 GW_DEFERRED，
 * 因此 gwCall/gwLoad 的排队、去抖、返回值语义全部是产品代码。
 */
const RECORDING_SETTINGS_LOADER = `() => {
    (globalThis.__qin603Gate = globalThis.__qin603Gate || {calls: []}).calls.push('load');
    GW_DEFERRED['settings'] = {
        selectAssetRackFolder: (...args) => {
            globalThis.__qin603Gate.calls.push(args);
            return args.length ? args[0] : null;
        },
    };
    return Promise.resolve();
}`;

const activeTimeoutCount = () => process.getActiveResourcesInfo()
    .filter(name => name === 'Timeout').length;

/** 每个用例一份独立模块树：网关状态是模块级的，必须互不干扰。 */
async function loadGatewayUnderTest(loaderPatches) {
    installHost({embedded: true});
    const tree = makeProductTree(loaderPatches);
    const main = await loadProduct(tree);
    return {tree, main};
}

// 用例自身带 60s 上限：去掉有界等待时必须「变红」而不是把 CI 挂死（真实超时 15s，留足余量）。
test('QIN-603 缺陷B：单个任务卡死 15s 后只 reject 该任务，后续任务按 FIFO 继续', {timeout: 60000}, async () => {
    const {tree, main} = await loadGatewayUnderTest({governance: HANGING_GOVERNANCE_LOADER});
    try {
        assert.equal(main.deferredDomainsGateOpen(), false, '未触发引导时闸门必须关闭，队列才会生效');

        const order = [];
        const stuck = main.gwCall('governance', 'loadGovernanceSuggestions');
        const stuckSettled = stuck.then(() => 'resolved', err => err.message);
        const afterStuck = main.gwLoad('organize')
            .then(api => { order.push('organize'); return api; });
        const afterThat = main.gwLoad('settings')
            .then(api => { order.push('settings'); return api; });

        const startedAt = Date.now();
        main.openDeferredDomainGate();

        const message = await stuckSettled;
        const waited = Date.now() - startedAt;

        assert.match(String(message), /延后域任务超时（15000ms）/, `必须是「超时并跳过」：${message}`);
        assert.match(String(message), /governance\/loadGovernanceSuggestions/, '错误里要点名被跳过的任务');
        assert.ok(waited >= 14000, `必须真的等满约 15s 才放弃（实测 ${waited}ms）`);
        assert.ok(waited < 60000, `不得无限等下去（实测 ${waited}ms）`);

        // 关键语义：卡死任务只牺牲自己，后面的任务继续跑，且顺序不被打乱
        const organizeApi = await afterStuck;
        const settingsApi = await afterThat;
        assert.equal(typeof organizeApi?.activateAssetBoard, 'function', 'organize 域应已真实注册');
        assert.equal(typeof settingsApi?.selectAssetRackFolder, 'function', 'settings 域应已真实注册');
        assert.deepEqual(order, ['organize', 'settings'], '后续任务必须按入队顺序执行（FIFO）');
    } finally {
        dropTree(tree);
    }
});

test('QIN-603 缺陷B：任务成功时清掉自己的超时计时器，不留悬挂 handle', async () => {
    const {tree, main} = await loadGatewayUnderTest({settings: RECORDING_SETTINGS_LOADER});
    try {
        const before = activeTimeoutCount();
        const pending = [
            main.gwLoad('settings'),
            main.gwCall('settings', 'selectAssetRackFolder', 'root_a'),
        ];
        main.openDeferredDomainGate();
        const [api, picked] = await Promise.all(pending);

        assert.equal(typeof api?.selectAssetRackFolder, 'function', 'gwLoad 必须返回域函数面');
        assert.equal(picked, 'root_a', 'gwCall 必须返回域函数的真实返回值');
        const after = activeTimeoutCount();
        assert.ok(after <= before, `成功任务后不得残留超时计时器：before=${before} after=${after}`);
    } finally {
        dropTree(tree);
    }
});

test('QIN-603 AC6：闸门关闭期连点目录，放行后只执行最后一次（旧的按已完成结账）', async () => {
    const {tree, main} = await loadGatewayUnderTest({settings: RECORDING_SETTINGS_LOADER});
    try {
        globalThis.__qin603Gate = {calls: []};
        const first = main.gwCall('settings', 'selectAssetRackFolder', 'root_a');
        const second = main.gwCall('settings', 'selectAssetRackFolder', 'root_b');
        main.openDeferredDomainGate();

        const [firstResult, secondResult] = await Promise.all([first, second]);
        assert.equal(secondResult, 'root_b', '最后一次点击必须生效');
        assert.equal(firstResult, undefined, '被取代的旧导航按「已完成但无操作」结账');

        const executed = globalThis.__qin603Gate.calls.filter(entry => Array.isArray(entry));
        assert.deepEqual(executed, [['root_b']], '排队中的旧导航不得在放行后补执行');
    } finally {
        dropTree(tree);
    }
});
