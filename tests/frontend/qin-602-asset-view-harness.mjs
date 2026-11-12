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

// QIN-602：资产库视图渲染汇聚点的「真实代码块 + 假宿主」执行器。
// 不复制实现、不整体打桩：被测的 renderActiveAssetView / runAssetViewRenderer /
// restoreEmbeddedAssetTopbar / renderRegistryToolbar / renderCustomAssetLibrary
// 全部从仓库聚合源原文抽取，在 vm 里真跑；只有与渲染时序无关的协作者
// （域内容渲染、统计卡片、图标刷新、全局状态）用最小桩提供。

import assert from 'node:assert/strict';
import vm from 'node:vm';
import {readAssetManagerAggregate} from './asset-manager-aggregate.mjs';

const AGGREGATE_SOURCE = readAssetManagerAggregate();

export function extractFunction(name, text = AGGREGATE_SOURCE){
    const marker = `function ${name}(`;
    const start = text.indexOf(marker);
    assert.ok(start >= 0, `函数应存在：${name}`);
    // 从参数列表收尾之后才找函数体：options={} 这类默认值里的 {} 不能算函数体配平。
    const paramsEnd = text.indexOf(')', start);
    assert.ok(paramsEnd > start, `函数签名应闭合：${name}`);
    let depth = 0;
    for(let index = text.indexOf('{', paramsEnd); index < text.length; index += 1){
        if(text[index] === '{') depth += 1;
        else if(text[index] === '}'){
            depth -= 1;
            if(depth === 0) return text.slice(start, index + 1);
        }
    }
    assert.fail(`函数未闭合：${name}`);
}

function escapeRegex(value){
    return String(value).replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

function escapeMarkup(value){
    const map = {'&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;'};
    return String(value ?? '').replace(/[&<>"']/g, char => map[char]);
}

function classPattern(cls, flags = ''){
    // 精确 class 词：registry-toolbar 不得把 registry-toolbar-controls 等派生类名算进去。
    return new RegExp(`class="(?:[^"]*\\s)?${escapeRegex(cls)}(?=\\s|")`, flags);
}

// 结构化 DOM 宿主：落盘真实 HTML 字符串，支持整块覆盖、前插与 class 查询 / 计数。
export class FakeRoot {
    constructor(){ this._html = ''; }
    get innerHTML(){ return this._html; }
    set innerHTML(value){ this._html = String(value ?? ''); }
    insertAdjacentHTML(position, markup){
        if(position === 'afterbegin') this._html = String(markup) + this._html;
        else if(position === 'beforeend') this._html += String(markup);
        else throw new Error(`不支持的插入位置：${position}`);
    }
    querySelector(selector){
        if(!selector.startsWith('.')) return null;
        const cls = selector.slice(1);
        return classPattern(cls).test(this._html) ? {className: cls} : null;
    }
    countClass(cls){
        const matches = this._html.match(classPattern(cls, 'g'));
        return matches ? matches.length : 0;
    }
}

const FUNCTION_NAMES = [
    'embeddedAssetTopbarMarkup',
    'restoreEmbeddedAssetTopbar',
    'showEmbeddedAssetViewFailure',
    'runAssetViewRenderer',
    'renderActiveAssetView',
    'renderRegistryOverview',
    'renderRegistryToolbar',
    'renderCustomAssetLibrary',
];

// renderCustomAssetLibrary 函数体含大量模板字符串，简单的花括号配平会在中途闭合。
// 本票只测它的 !board 回退分支：取分支原文在 vm 中执行，并在末尾放哨兵证明
// board 有效时不会误入最小宿主；同时把「必须经 render() 汇聚点」写成删掉修复即抛错的护栏。
function extractBoardGuardBranch(text = AGGREGATE_SOURCE){
    const marker = 'function renderCustomAssetLibrary(board){';
    const start = text.indexOf(marker);
    assert.ok(start >= 0, 'renderCustomAssetLibrary 应存在');
    const branchStart = text.indexOf('if(!board){', start);
    const branchEnd = text.indexOf('\n    const embeddedVault', branchStart);
    assert.ok(branchStart >= 0 && branchEnd > branchStart, '!board 回退分支应存在');
    const branch = text.slice(branchStart, branchEnd).trim();
    assert.match(branch, /setActiveTab\('registry'\)/, '回退分支应切回总览');
    assert.match(branch, /render\(\)/, '回退分支必须经 render() 汇聚点（删掉修复即报错）');
    return `function renderCustomAssetLibrary(board){\n    ${branch}\n    throw new Error('board 有效时不应进入 QIN-602 最小宿主');\n}`;
}

function makeDeferred(){
    let resolve;
    let reject;
    const promise = new Promise((res, rej) => { resolve = res; reject = rej; });
    return {promise, resolve, reject};
}

const DOMAIN_RENDER_METHODS = [
    'renderInboxManager', 'renderScriptAssetsManager', 'renderProjectManager',
    'renderPromptManager', 'renderLutManager', 'renderWorkflowManager',
    'renderLocalManager', 'renderCanvasAssetsManager', 'renderAssetManager',
    'renderCustomAssetLibrary',
];

// 不用 Proxy：Promise.resolve(api) 会把 Proxy 的 .then 当 thenable 消费掉，
// 导致域渲染探针永远不落地。显式列出域渲染方法，api 保持普通对象。
export function makeDomainApi(root, calls, label){
    const api = {};
    for(const name of DOMAIN_RENDER_METHODS){
        api[name] = (...args) => {
            calls.viewRenders.push({name, args});
            root.innerHTML = `<section class="asset-view" data-asset-view="${name}"><span>${escapeMarkup(label)}</span></section>`;
        };
    }
    return api;
}

export function createAssetViewHarness({embedded = true, tab = 'inbox', boards = {}, patches = {}} = {}){
    const root = new FakeRoot();
    const calls = {gwLoad: [], setActiveTab: [], render: [], refreshIcons: 0, viewRenders: []};
    const deferredByDomain = new Map();
    let isEmbedded = embedded;

    const context = vm.createContext({
        console,
        root,
        document: {
            documentElement: {classList: {contains: cls => cls === 'vault-shell-embedded' && isEmbedded}},
            activeElement: null,
        },
        window: {parent: {document: {getElementById: () => null}}},
        activeTab: tab,
        registryStatCategory: 'all',
        registryProjectFilterId: '__all__',
        projects: [],
        registryQuery: '',
        registryLayout: 'grid',
        registryAspect: 'wide',
        registrySimple: false,
        governanceSuggestionsError: false,
        governanceSuggestionsLoading: false,
        governanceSuggestionsData: null,
        governanceDrawerOpen: false,
        escapeHtml: escapeMarkup,
        escapeAttr: escapeMarkup,
        governanceTriggerLabel: () => '打开治理建议',
        governanceSuggestionTotal: () => 0,
        registryActiveSelectedIds: () => [],
        selectedRegistryCustomGroupIds: () => [],
        registryCoverflowAssets: () => [],
        renderRegistrySelectCapsule: () => '',
        renderRegistryOverviewStats: () => '<div class="registry-overview-stats"></div>',
        registryCategoryLabel: () => '资产总览',
        registryTotalSummary: count => `共 ${count} 项`,
        registryVisibleAssets: () => [],
        assetBoardById: id => boards[id] ?? null,
        refreshIcons: () => { calls.refreshIcons += 1; },
        renderRegistryManager: () => {
            calls.viewRenders.push({name: 'renderRegistryManager', args: []});
            root.innerHTML = '<section class="registry-view" data-asset-view="registry"></section>';
        },
        setActiveTab: value => {
            context.activeTab = value;
            calls.setActiveTab.push(value);
        },
        gwLoad: domain => {
            calls.gwLoad.push(domain);
            if(deferredByDomain.has(domain)) return deferredByDomain.get(domain).promise;
            return Promise.resolve(makeDomainApi(root, calls, domain));
        },
        // render() 的视图相关部分就是 renderActiveAssetView()；预览调度 / 滚动恢复 /
        // 外壳快照同步与本票顶栏时序无关，宿主不复刻（由 qin-489 等既有用例覆盖）。
        render: () => {
            calls.render.push(context.activeTab);
            return context.__qin602.renderActiveAssetView();
        },
    });

    const blocks = FUNCTION_NAMES.map(name => {
        if(patches[name]) return patches[name];
        return name === 'renderCustomAssetLibrary' ? extractBoardGuardBranch() : extractFunction(name);
    }).join('\n');
    vm.runInContext(
        `let assetViewRenderSeq = 0;\nlet assetViewTopbarSnapshot = null;\n${blocks}\n` +
        'globalThis.__qin602 = {renderActiveAssetView, restoreEmbeddedAssetTopbar, ' +
        'embeddedAssetTopbarMarkup, renderRegistryToolbar, renderRegistryOverview, ' +
        'renderCustomAssetLibrary, getSeq: () => assetViewRenderSeq, ' +
        'getSnapshot: () => assetViewTopbarSnapshot};',
        context,
        {filename: 'qin-602-asset-view-harness.js'},
    );

    return {
        root,
        calls,
        context,
        api: context.__qin602,
        setTab: value => { context.activeTab = value; },
        setEmbedded: value => { isEmbedded = value; },
        deferDomain: domain => {
            const deferred = makeDeferred();
            deferredByDomain.set(domain, deferred);
            return deferred;
        },
        domainApi: label => makeDomainApi(root, calls, label),
        flush: async () => { await new Promise(resolve => setImmediate(resolve)); },
    };
}
