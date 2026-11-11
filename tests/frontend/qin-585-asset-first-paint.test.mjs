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

// QIN-585（GitHub #679）资产页首屏脚本减负：
// - 外壳页不再加载本地 Tailwind 运行时（836KB，进页做 JIT 编译），改用仓库静态快照；
// - 收集箱与治理建议这两条非首屏链路不再与首屏列表并发发起。
//
// 时序断言按**行为**写：把真实的 loadAssetInbox / loadGovernanceSuggestions /
// scheduleDeferredBootRequests 从源码里抽出来，配上假的 HTTP、空闲调度与 bootstrap promise
// 直接执行，观察「请求何时发出」。源码字符窗口（例如「前 900 字里不许出现某调用」）
// 会漏检：把同步请求插回调用点后，字符窗口照样成立、行为已经退化。

import assert from 'node:assert/strict';
import { readAssetManagerAggregate } from './asset-manager-aggregate.mjs';
import {readFileSync} from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';

const read = path => readFileSync(new URL(`../../${path}`, import.meta.url), 'utf8');

const SHELL_PAGE = read('web/pages/assets.html');
const EMBED_PAGE = read('web/embeds/asset-manager.html');
const UTILITIES = read('web/css/base/tailwind-utilities.css');
const MANAGER = readAssetManagerAggregate();

// 外壳页自己的脚本同样依赖这份快照：去掉运行时后，脚本里动态加的工具类
// （登录弹层禁用态、团队面板布局等）也必须能在快照里找到，否则只是「静态 HTML 好看」。
const SHELL_SCRIPTS = [...SHELL_PAGE.matchAll(/<script src="(\/static\/js\/[^"?]+)[^"]*"><\/script>/g)]
    .map(match => `web${match[1].replace('/static/', '/')}`);

// 从快照反解选择器：CSS 选择器里的 `[`/`]`/`#`/`/` 等字符带反斜杠，反解后再与页面 class 比对，
// 避免在测试里重复实现一套转义规则（`_` 是否转义这类细节最容易导致假红）。
function utilitySelectors(css){
    const tokens = new Set();
    // 取 `.` 后的类名主干：遇到未转义的组合符（`:>` `~` `+` `(` `[`）、`{`、空白、逗号即止。
    // 这样 `.space-x-1>:not([hidden])~:not([hidden])` 归一到 `space-x-1`，
    // 而 `.hover\:scale-105:hover` 里的转义冒号仍属于类名本身。
    for(const match of css.matchAll(/\.((?:[^\\{}\s,;:>~+()[\] ]|\\.)+)/g)){
        tokens.add(match[1].replace(/\\(.)/g, '$1'));
    }
    return tokens;
}

// 本仓库自定义类前缀（部件/布局类），不属于 Tailwind 工具类。
const PROJECT_CLASS = /^(?:gw-|hw-|asset-|orb-|oled-|topbar-|trapezoid-|uv-|recessed-|master-|launch-|nav-|pill-|active$|select-none$)/;

// Tailwind 工具类的形式特征：变体前缀（hover:/active:）或工具族前缀。
const TAILWIND_LIKE = /^(?:(?:hover|active|focus|focus-visible|group|disabled|sm|md|lg|xl|dark):)?(?:flex|block|inline|inline-block|inline-flex|grid|hidden|absolute|relative|fixed|sticky|isolate|sr-only|items-|justify-|space-|self-|px-|py-|p-|pt-|pb-|pl-|pr-|m-|mt-|mb-|ml-|mr-|w-|h-|min-|max-|text-|bg-|border|rounded|shadow|z-|top-|bottom-|left-|right-|inset-|overflow-|font-|animate-|transition|cursor-|shrink-|grow-|basis-|from-|via-|to-|antialiased|pointer-events-|gap-|opacity-|leading-|tracking-|whitespace-|truncate|uppercase|backdrop-|ring-|divide-|col-|row-|order-|aspect-|object-|fill-|stroke-|align-|list-|float-|clear-)/;

function classTokens(text){
    const found = new Set();
    const add = raw => {
        for(const token of String(raw).split(/[\s,]+/)){
            const clean = token.replace(/^[\s'"]+/, '').replace(/[\s'";,)}]+$/, '');
            if(!clean || clean.includes('${') || clean.includes('}')) continue;
            if(PROJECT_CLASS.test(clean) || !TAILWIND_LIKE.test(clean)) continue;
            found.add(clean);
        }
    };
    for(const match of text.matchAll(/class="([^"]{0,400})"/g)) add(match[1]);
    for(const match of text.matchAll(/classList\.(?:add|remove|toggle)\(([^)]{0,200})\)/g)) add(match[1]);
    return found;
}

// 规则条数按「反解后的类名」统计：快照里的选择器带转义反斜杠（`.bg-white\/15`），
// 直接拿拼好的选择器去匹配会把转义反斜杠当正则转义吃掉，永远数不到。
function selectorCounts(css){
    const counts = new Map();
    // 先去注释：注释里出现的 `tools/build_static_tailwind_utilities.py` 会被当成一条 `.py` 选择器。
    const stripped = css.replace(/\/\*[\s\S]*?\*\//g, '');
    for(const rule of stripped.matchAll(/([^{}]+)\{/g)){
        for(const part of rule[1].split(',')){
            const match = /\.((?:[^\\{}\s,;:>~+()[\] ]|\\.)+)/.exec(part.trim());
            if(!match) continue;
            const token = match[1].replace(/\\(.)/g, '$1');
            counts.set(token, (counts.get(token) || 0) + 1);
        }
    }
    return counts;
}

// 快照里同一条规则出现两次说明有人重复补齐；用于定位具体是哪一段重复。
function repeatedSelectors(counts){
    return [...counts].filter(([, count]) => count > 1).map(([token]) => token);
}

// ---------------------------------------------------------------------------
// 行为级夹具：把真实的顶层函数与它们读写的模块级状态一起抽出来执行。
// 状态声明也从源码里取，避免测试里复制一份「默认值」而与实现悄悄分叉。
//
// 关键：`loadAll` 也是**真实源码**，不是桩。「登记首屏列表终态」这行接线就在 loadAll 里，
// 把它打桩再手动 track，等于把被测接线搬进测试自己写，删掉源码那行仍然全绿。
// 因此这里只打桩 loadAll 的外部依赖（列表请求、资产库接口、渲染等），loadAll 本体照跑。
// ---------------------------------------------------------------------------

const STATE_DECLARATIONS = [
    'activeTab',
    'inboxItems',
    'inboxSettings',
    'inboxLoaded',
    'inboxLoading',
    'inboxError',
    'inboxSettingsOpen',
    'inboxMigrating',
    'inboxRequestSeq',
    'inboxInFlight',
    'inboxInFlightToken',
    'registryBootstrapGeneration',
    'registryBootstrapSettledGeneration',
    'registryBootstrapSettled',
    'registryLoading',
    'registryRequestSeq',
    'registryFacets',
    'registryBootstrapError',
    'registryAssets',
    'assetLibrary',
    'promptLibrary',
    'apiProviders',
    'canvasAssetsData',
    'activeAssetLibraryId',
    'activeWorkflowLibraryId',
    'activeAssetCategoryId',
    'activeWorkflowCategoryId',
    'selectedAssetId',
    'selectedWorkflowId',
    'selectedAssetIds',
    'selectedWorkflowIds',
    'selectedPromptIds',
    'selectedCanvasAssetIds',
    'governanceSuggestionsData',
    'governanceSuggestionsLoading',
    'governanceSuggestionsError',
    'governanceDrawerOpen',
    'registrySessionReady',
    'selectedRegistryAssetId',
    'registryDetail',
    'searchRenderTimer',
    'allowedRootSettingsState',
];

const BOOT_FUNCTIONS = [
    'trackRegistryBootstrapSettled',
    'refreshRegistryBootstrapTracked',
    'isRegistryBootstrapPending',
    'waitRegistryBootstrapSettled',
    'loadAssetInbox',
    'markAssetInboxViewed',
    'assetInboxUnreadCount',
    'loadGovernanceSuggestions',
    'scheduleDeferredBootRequests',
    'loadAll',
    'uploadAssetInboxFiles',
    'saveAssetInboxSettings',
    'createRegistryRemoteAsset',
    'deleteRegistryRemoteAsset',
    // 第 4 轮：真实「搜索输入」入口与「替换当前列表」的共享入口都进 vm 照跑，
    // 只把 refreshRegistryAssetsRequest（网络实现本身）与宿主依赖打桩。
    'refreshRegistryAssets',
    'scheduleSearchRender',
    'reportRegistryWriteRefreshFailure',
    'saveAllowedRootsRequest',
];

function sliceDeclaration(source, name){
    const match = new RegExp(`^(?:let|const|var) ${name}\\b[^\\n]*$`, 'm').exec(source);
    assert.ok(match, `未找到模块级状态声明：${name}`);
    return match[0];
}

function sliceFunction(source, name){
    const match = new RegExp(`^(?:async )?function ${name}\\(`, 'm').exec(source);
    assert.ok(match, `未找到顶层函数：${name}`);
    const start = match.index;
    const end = source.indexOf('\n}', start);
    assert.ok(end > start, `顶层函数 ${name} 缺少行首闭合括号`);
    return source.slice(start, end + 2);
}

// 整段引导入口一起取出来执行：这样「把某个请求直接塞回启动段」这类改动会被行为捕获，
// 而不是只改了一段字符、断言窗口恰好没覆盖到。
function sliceBootEntry(source){
    const start = source.indexOf("document.addEventListener('DOMContentLoaded'");
    assert.ok(start > 0, '未找到 DOMContentLoaded 引导入口');
    const end = source.indexOf('\n});', start);
    assert.ok(end > start, '引导入口缺少行首闭合括号');
    return source.slice(start, end + 4);
}

function createHarness(){
    const state = {
        requests: [],        // 只记非首屏链路（收集箱/治理）的请求 URL
        pending: [],
        idle: [],
        timers: [],
        renders: 0,
        notices: [],
        failures: [],
        statuses: [],
        searchCalls: [],     // 真实搜索/筛选入口调用 refreshRegistryAssets 的实参
        bootstraps: [],      // 每次替换当前列表（loadAll / 搜索 / URL 写入）的请求句柄
        bootHandler: null,
        libraryMode: 'ok',   // 'ok' | 'fail' | 'hang'
        settingsPath: '',
        promptValue: 'https://example.test/new.png',
        unhandled: [],
    };

    const record = (kind, url) => {
        const pending = {url, kind};
        pending.promise = new Promise((resolve, reject) => {
            pending.resolve = resolve;
            pending.reject = reject;
        });
        state.pending.push(pending);
        state.requests.push(url);
        return pending.promise;
    };
    const newBootstrapRequest = () => {
        const handle = {resolve: null, reject: null};
        handle.promise = new Promise((resolve, reject) => {
            handle.resolve = resolve;
            handle.reject = reject;
        });
        state.bootstraps.push(handle);
        return handle.promise;
    };
    const libraryRequest = () => {
        if(state.libraryMode === 'fail') return Promise.reject(new Error('资产库读取失败'));
        if(state.libraryMode === 'hang') return new Promise(() => {});
        return Promise.resolve({library: {libraries: [], categories: []}});
    };

    // FormData 只用来构造上传体，测试不解析内容。
    class FakeFormData { append(){} }

    const context = vm.createContext({
        console, Promise, Math, Number, String, Array, Object, Set, Map, Error, JSON, Boolean,
        FormData: FakeFormData,
        document: {
            hidden: false,
            querySelector(selector){
                if(selector !== '[data-inbox-settings-panel]') return null;
                return {
                    querySelector(inner){
                        if(inner === '[data-inbox-path]') return {value: state.settingsPath};
                        if(inner === '[data-inbox-migrate]') return {checked: false};
                        return null;
                    },
                };
            },
            getElementById(){ return null; },
            addEventListener(type, handler){
                if(type === 'DOMContentLoaded') state.bootHandler = handler;
            },
        },
        window: {
            requestIdleCallback(fn){ state.idle.push(fn); return state.idle.length; },
            prompt(){ return state.promptValue; },
        },
        localStorage: {getItem(){ return null; }, setItem(){}, removeItem(){}},
        setTimeout(fn){ state.timers.push(fn); return state.timers.length; },
        clearTimeout(){},
        setInterval(){ return 0; },
        clearInterval(){},
        confirm(){ return true; },
        // 假 HTTP：请求挂起，由用例决定何时、以什么内容返回。
        assetManagerHttp: {
            request(url){ return record('http', url); },
        },
        fetch(url){ return record('fetch', url); },
        apiJsonResponse: response => response,
        assetManagerApi: {
            getAssetLibrary: () => libraryRequest(),
            getPromptLibraries: () => Promise.resolve({library: {libraries: []}}),
            getProviders: () => Promise.resolve({providers: []}),
            getCanvasAssets: () => Promise.resolve({categories: [], canvases: [], items: []}),
            createRegistryRemoteAsset: () => Promise.resolve({asset: {id: 'new-url'}}),
            deleteRegistryRemoteAsset: () => Promise.resolve({ok: true}),
        },
        registryRemoteKind: () => 'image',
        normalizeRegistryAsset: asset => asset,
        loadLocalStorageSettings: () => Promise.resolve(),
        // 首屏列表请求本体：真实 loadAll 会把它登记成「当前代际的终态」，何时落地由用例控制。
        loadRegistryBootstrap: () => newBootstrapRequest(),
        // 共享入口 refreshRegistryAssets 的网络实现（40 行重逻辑）不进 vm；
        // 用可控 promise 代表「一次替换当前列表的请求」，接线与代际判定仍是真实源码。
        refreshRegistryAssetsRequest(renderNow){
            state.searchCalls.push(renderNow);
            return newBootstrapRequest();
        },
        clearSearchSelection(){},
        requestAnimationFrame(){},
        clearSearchRenderTimer(){},
        renderGlobalDirectorySettings(){},
        applyAllowedRootSettingsData(){},
        // loadAll 的其余宿主依赖（渲染、状态栏、树/看板索引等），与本票断言无关。
        tryApplyRegistrySnapshot(){},
        setStatus(message){ state.statuses.push(String(message)); },
        render(){ state.renders += 1; },
        refreshScriptArchive(){},
        assetLibraries: () => [],
        workflowLibraryId: () => '',
        assetBoardById: () => null,
        showAssetNotice(message){ state.notices.push(message); },
        showAssetFailure(title, error){ state.failures.push([title, String(error?.message || error || '')]); },
        updateGovernanceTrigger(){},
        renderGovernanceDrawer(){},
        // 引导段的宿主依赖
        directorySettingsPage: false,
        loadRegistryPrincipal(){},
        loadGlobalDirectorySettings(){},
        restoreRegistryBrowseSession: async () => 0,
        restoreAssetTreeState: async () => {},
        applyRegistryBrowseSessionScroll: async () => {},
        saveRegistryBrowseSession(){},
        markRegistryFirstScreenReady(){},
        startRegistryAutomaticIndexPolling(){},
    });

    const body = [
        ...STATE_DECLARATIONS.map(name => sliceDeclaration(MANAGER, name)),
        ...BOOT_FUNCTIONS.map(name => sliceFunction(MANAGER, name)),
        // beginRegistryBootstrapRequest 要写模块级 registryRequestSeq，只能放在 vm 内部。
        // 真实实现还会 abort 旧请求；这里保留「新请求递增序号」这一关键语义，
        // 被 abort 的旧 loadAll 才会像生产那样在 catch 里按序号提前返回。
        'function beginRegistryBootstrapRequest(){ return ++registryRequestSeq; }',
        // QIN-591 注入：跨文件写状态的 setter 桩。拆分后域内函数（loadAssetInbox /
        // loadGovernanceSuggestions 等）对主文件状态的写入经主文件导出的 setter（登记在案的
        // setter 改写），这里把 setter 落回 vm 内已切片的同名模块级状态，赋值语义与拆分前一致。
        'function setInboxItems(v){ inboxItems = v; }',
        'function setInboxLoaded(v){ inboxLoaded = v; }',
        'function setInboxSettings(v){ inboxSettings = v; }',
        'function setInboxSettingsOpen(v){ inboxSettingsOpen = v; }',
        'function setGovernanceSuggestionsData(v){ governanceSuggestionsData = v; }',
        'function setGovernanceSuggestionsLoading(v){ governanceSuggestionsLoading = v; }',
        'function setGovernanceSuggestionsError(v){ governanceSuggestionsError = v; }',
        // QIN-591 注入：gwCall/gwLoad 网关桩。真实网关优先命中 GW_DEFERRED 注册的域函数面；
        // 桩把调用派发回 vm 内已切片的同名函数（inbox/governance 域本体），未切片的宿主依赖
        // 沿用夹具同名桩。行为断言（请求何时发出）仍由真实源码函数驱动。
        `function gwCall(domain, name, ...args){
            if(name === 'loadAssetInbox') return loadAssetInbox(...args);
            if(name === 'markAssetInboxViewed') return markAssetInboxViewed(...args);
            if(name === 'loadGovernanceSuggestions') return loadGovernanceSuggestions(...args);
            if(name === 'clearSearchSelection') return clearSearchSelection(...args);
            if(name === 'renderGlobalDirectorySettings') return renderGlobalDirectorySettings(...args);
            if(name === 'applyAllowedRootSettingsData') return applyAllowedRootSettingsData(...args);
            throw new Error('gwCall 未注入域函数面: ' + domain + '/' + name);
        }`,
        `function gwLoad(domain){
            if(domain === 'inbox') return Promise.resolve({ loadAssetInbox, markAssetInboxViewed, saveAssetInboxSettings });
            return Promise.reject(new Error('gwLoad 未注入域: ' + domain));
        }`,
        // QIN-591 返工：首卡闸门。真实网关在闸门关闭时把延后域调用排队，放行后才加载；
        // 本夹具已把 gwCall/gwLoad 换成桩，所以闸门只需可调用、可查询，避免引导段 ReferenceError。
        // 闸门的排队/顺序/返回值契约由 qin-591-domain-gate.test.mjs 用真实源码切片驱动验证。
        'let __deferredDomainsOpen = false;',
        'function openDeferredDomainGate(){ __deferredDomainsOpen = true; }',
        'function deferredDomainsGateOpen(){ return __deferredDomainsOpen; }',
        sliceBootEntry(MANAGER),
        `return {
            loadAll,
            loadAssetInbox,
            markAssetInboxViewed,
            loadGovernanceSuggestions,
            scheduleDeferredBootRequests,
            uploadAssetInboxFiles,
            saveAssetInboxSettings,
            createRegistryRemoteAsset,
            deleteRegistryRemoteAsset,
            refreshRegistryBootstrapTracked,
            refreshRegistryAssets,
            scheduleSearchRender,
            saveAllowedRootsRequest,
            reportRegistryWriteRefreshFailure,
            trackRegistryBootstrapSettled,
            isRegistryBootstrapPending,
            waitRegistryBootstrapSettled,
            assetInboxUnreadCount,
            get inboxItems(){ return inboxItems; },
            get inboxSettings(){ return inboxSettings; },
            get inboxLoading(){ return inboxLoading; },
            get inboxInFlight(){ return inboxInFlight; },
            get bootstrapPending(){ return isRegistryBootstrapPending(); },
            set governanceDrawerOpen(value){ governanceDrawerOpen = value; },
        };`,
    ].join('\n');
    const api = vm.runInNewContext(`(() => {\n${body}\n})()`, context, {filename: 'asset-manager.boot-harness.js'});

    const fireBoot = () => {
        assert.ok(state.bootHandler, '引导入口没有注册 DOMContentLoaded 监听');
        state.bootHandler();
        return state.bootstraps[state.bootstraps.length - 1];
    };
    // 直接跑真实 loadAll（模拟用户刷新：首屏在途时又发起一次）。
    const startLoadAll = () => {
        const promise = api.loadAll();
        promise.catch(() => {});   // 只关心列表终态的用例不该留下未处理拒绝
        return state.bootstraps[state.bootstraps.length - 1];
    };
    const settle = async (bootstrap, ok = true) => {
        if(bootstrap){
            if(ok) bootstrap.resolve({items: [], facets: {}});
            else bootstrap.reject(new Error('bootstrap failed'));
        }
        await flush();
    };
    return {api, state, fireBoot, startLoadAll, settle};
}

const flush = async () => {
    for(let i = 0; i < 8; i += 1) await new Promise(resolve => setImmediate(resolve));
};

test('AC1: 资产库外壳页不再加载 Tailwind 运行时，改用仓库静态快照', () => {
    assert.doesNotMatch(
        SHELL_PAGE,
        /tailwindcss-cdn\.js/,
        '外壳页不得再加载 Tailwind 运行时（进页 JIT 编译会把首屏拖住）'
    );
    assert.doesNotMatch(
        SHELL_PAGE,
        /cdn\.tailwindcss\.com/,
        '外壳页不得引用外部 Tailwind CDN'
    );
    assert.match(
        SHELL_PAGE,
        /<link rel="stylesheet" href="\/static\/css\/base\/tailwind-utilities\.css\?[^"]+">/,
        '外壳页必须显式引用静态工具类快照（带缓存键）'
    );
    // 内嵌素材管理页本来只用自有 CSS，不得为了「顺手」又把运行时装回来。
    assert.doesNotMatch(EMBED_PAGE, /tailwindcss-cdn\.js/);
});

test('AC1: 静态快照覆盖外壳页与外壳脚本实际使用的全部工具类', () => {
    assert.ok(SHELL_SCRIPTS.length >= 3, `外壳页脚本解析异常：${SHELL_SCRIPTS.length}`);

    const pageTokens = classTokens(SHELL_PAGE);
    assert.ok(pageTokens.size >= 100, `外壳页 Tailwind 工具类数量异常：${pageTokens.size}`);

    const scriptTokens = new Set();
    for(const path of SHELL_SCRIPTS){
        for(const token of classTokens(read(path))) scriptTokens.add(token);
    }
    assert.ok(scriptTokens.size > 0, '外壳脚本里应能解析出工具类（禁用态/团队面板等）');

    const present = utilitySelectors(UTILITIES);
    const missingPage = [...pageTokens].filter(token => !present.has(token));
    const missingScripts = [...scriptTokens].filter(token => !present.has(token));
    assert.deepEqual(missingPage, [], `快照缺少外壳页使用的工具类：${missingPage.join(', ')}`);
    assert.deepEqual(
        missingScripts,
        [],
        `快照缺少外壳脚本动态使用的工具类（去掉运行时后这些类会静默失效）：${missingScripts.join(', ')}`
    );
});

test('AC1: 补齐段不得重复声明快照里已有的规则', () => {
    // 运行时删除后手工补类时最容易犯的错：往快照尾部再追加一条已存在的规则。
    // 重复声明不报错，只是让同一个类在快照里出现两遍；这里按「补齐段 vs 既有段」比对。
    const index = UTILITIES.indexOf('/* QIN-585');
    assert.ok(index > 0, '缺少 QIN-585 补齐段标记');
    const base = selectorCounts(UTILITIES.slice(0, index));
    const appended = selectorCounts(UTILITIES.slice(index));
    assert.ok(appended.size > 0, '补齐段没有解析出任何规则');

    const duplicated = [...appended.keys()].filter(token => base.has(token));
    assert.deepEqual(duplicated, [], `补齐段重复声明了快照里已有的规则：${duplicated.join(', ')}`);

    const repeated = repeatedSelectors(appended);
    assert.deepEqual(repeated, [], `补齐段内部重复声明：${repeated.join(', ')}`);
});

test('AC1: 静态快照自带 preflight，不依赖运行时补齐基础样式', () => {
    // 去掉运行时后若丢了 preflight，卡片与表单控件会出现浏览器默认外观。
    assert.match(UTILITIES, /box-sizing:border-box/);
    assert.match(UTILITIES, /margin:0/);
    assert.match(UTILITIES, /--tw-border-spacing-x:0/);
});

test('AC2: 执行真实启动段时，首屏列表落地前没有任何非首屏请求', async () => {
    // 这条用例真的把源码里的 DOMContentLoaded 整段跑一遍，并且跑的是**真实的 loadAll**：
    // 往启动段里直接塞一条 fetch/request、或删掉 loadAll 里登记终态那行接线，都会立刻变红。
    const {state, fireBoot, settle} = createHarness();
    const bootstrap = fireBoot();
    await flush();

    assert.equal(state.bootstraps.length, 1, '启动段应发起一次首屏列表请求');
    assert.deepEqual(state.requests, [], '首屏列表在途时，启动段不得发出收集箱或治理建议请求');
    assert.equal(state.idle.length, 0, '首屏列表在途时不得把延后块排进空闲时段');

    await settle(bootstrap);
    assert.equal(state.idle.length, 1, '首屏列表落地后才排延后块');

    state.idle.shift()();
    await flush();
    // QIN-591：延后块经网关补发（gwLoad 动态导入异步化），两条链路的到达顺序不再固定；
    // 行为契约不变：两条都补齐、且各自只发一次。
    assert.deepEqual(
        [...state.requests].sort(),
        ['/api/asset-inbox?limit=500', '/api/asset-registry/governance/suggestions'],
        '延后块落地后收集箱与治理建议都应补发'
    );
});

test('AC2: 首屏列表落地前，启动期治理建议请求不发出去', async () => {
    const {api, state, startLoadAll, settle} = createHarness();
    const bootstrap = startLoadAll();
    assert.equal(api.bootstrapPending, true);

    await api.loadGovernanceSuggestions();
    assert.deepEqual(state.requests, [], '首屏列表未落地时不得发起治理建议请求');

    // 用户在首屏期间点开治理抽屉，也走同一道闸门。
    api.governanceDrawerOpen = true;
    await api.loadGovernanceSuggestions();
    assert.deepEqual(state.requests, [], '抽屉打开也不得抢在首屏列表之前');

    // 闸门放行后请求会挂在假 HTTP 上（用例不关心响应），因此只等微任务、不 await 请求本身。
    await settle(bootstrap);
    void api.loadGovernanceSuggestions();
    await flush();
    assert.deepEqual(state.requests, ['/api/asset-registry/governance/suggestions']);
});

test('AC2: 真实 loadAll 的失败分支不得放行延后块（列表仍在途）', async () => {
    // 资产库接口先失败 → loadAll 的 Promise.all 提前 reject，但列表请求还在途。
    // 这条走的是真实 loadAll 的 catch，不是手动 track 出来的状态。
    const {api, state, fireBoot, settle} = createHarness();
    state.libraryMode = 'fail';
    const bootstrap = fireBoot();
    await flush();

    assert.equal(api.bootstrapPending, true, '资产库失败不等于首屏列表已结束');
    assert.equal(state.idle.length, 0, '列表还在途时不得把延后块排进空闲时段');
    assert.deepEqual(state.requests, []);

    await settle(bootstrap, false);   // 列表请求也失败，同样算终态
    assert.equal(api.bootstrapPending, false);
    assert.equal(state.idle.length, 1, '列表终态后才允许调度延后块');

    state.idle.shift()();
    await flush();
    // QIN-591：延后块经网关补发（gwLoad 动态导入异步化），两条链路的到达顺序不再固定；
    // 行为契约不变：两条都补齐、且各自只发一次。
    assert.deepEqual(
        [...state.requests].sort(),
        ['/api/asset-inbox?limit=500', '/api/asset-registry/governance/suggestions'],
        '延后块落地后收集箱与治理建议都应补发'
    );
});

test('AC2: 首屏在途时刷新，正等 A 的延后调度不得被旧代际放行', async () => {
    // 审核复现：首屏 A 在途时用户刷新启动 B，B abort A。旧 A 的终态既不能清掉 B 的「在途」，
    // 也不能把已经在等 A 的延后调度放行 —— 否则 B 还在跑，收集箱与治理又抢到前面。
    const {api, state, startLoadAll, settle} = createHarness();
    const first = startLoadAll();
    await flush();
    api.scheduleDeferredBootRequests();     // 延后链路此刻已排入，正在等 A
    await flush();
    assert.equal(state.idle.length, 0);

    const second = startLoadAll();          // 用户刷新：新代际，旧 A 被 abort
    await flush();
    assert.equal(api.bootstrapPending, true);

    await settle(first, false);             // 旧代际（被 abort）落地
    assert.equal(api.bootstrapPending, true, '旧代际落地不得清掉新一代的在途标记');
    assert.equal(state.idle.length, 0, '旧代际落地不得放行等新一代的延后调度');
    assert.deepEqual(state.requests, [], '新一代未落地前，非首屏链路一条 POST/GET 都不许发');

    await settle(second);
    assert.equal(api.bootstrapPending, false);
    assert.equal(state.idle.length, 1, '新一代落地后才放行延后调度');

    state.idle.shift()();
    await flush();
    // QIN-591：延后块经网关补发（gwLoad 动态导入异步化），到达顺序不再固定，契约仍是两条都补齐。
    assert.deepEqual([...state.requests].sort(), [
        '/api/asset-inbox?limit=500',
        '/api/asset-registry/governance/suggestions',
    ]);
});

test('AC2: 收集箱并发读取只发一次 GET', async () => {
    const {api, state, startLoadAll, settle} = createHarness();
    await settle(startLoadAll());

    const first = api.loadAssetInbox();
    const second = api.loadAssetInbox();
    assert.equal(state.requests.length, 1, '单飞：并发的收集箱读取必须复用同一次请求');

    state.pending[0].resolve({items: [{id: 'a'}], settings: {unread_count: 7}});
    await Promise.all([first, second]);
    assert.equal(api.inboxItems.length, 1);
    assert.equal(api.inboxSettings.unread_count, 7);
    assert.equal(api.inboxLoading, false, '请求落地后必须复位 loading');
    assert.equal(api.inboxInFlight, null, '请求落地后必须释放单飞句柄');
});

test('AC2: 收集箱已加载时，延后块不再重复读一遍', async () => {
    const {api, state, startLoadAll, settle} = createHarness();
    await settle(startLoadAll());
    const first = api.loadAssetInbox();
    state.pending[0].resolve({items: [], settings: {unread_count: 0}});
    await first;

    api.scheduleDeferredBootRequests();
    await flush();
    state.idle.shift()();
    await flush();
    // QIN-591：延后块经网关补发（gwLoad 动态导入异步化），到达顺序不再固定；
    // 行为契约不变：收集箱不重读（仅直读那一次）+ 延后块只补治理建议。
    assert.deepEqual(
        [...state.requests].sort(),
        ['/api/asset-inbox?limit=500', '/api/asset-registry/governance/suggestions'],
        '收集箱已经加载过，延后块只该补治理建议'
    );
});

test('AC2: 角标清零后，晚到的旧收集箱响应不得把未读数改回去', async () => {
    const {api, state, startLoadAll, settle} = createHarness();
    await settle(startLoadAll());
    // 未读数需要非 0 才走清零分支：先落一份带未读的快照。
    const seeded = api.loadAssetInbox();
    state.pending[0].resolve({items: [{id: 'a'}], settings: {unread_count: 7}});
    await seeded;
    assert.equal(api.inboxSettings.unread_count, 7);

    const reading = api.loadAssetInbox({refresh: true});
    const viewed = api.markAssetInboxViewed();
    assert.equal(state.requests.length, 3, '强制读取与清零各自一条请求');
    state.pending[2].resolve({ok: true});     // POST /viewed 先返回
    await viewed;
    assert.equal(api.inboxSettings.unread_count, 0);

    state.pending[1].resolve({items: [{id: 'b'}], settings: {unread_count: 7}});
    await reading;
    assert.equal(api.inboxSettings.unread_count, 0, '过期响应必须被丢弃，不得把角标改回未读');
    assert.equal(api.inboxItems.length, 1, '过期响应同样不得写入列表');
    assert.equal(api.inboxItems[0].id, 'a');
});

test('AC2: 上传成功后必须重新读收集箱，不能复用写前的在途快照', async () => {
    // 审核复现：POST /asset-inbox/upload 成功后总 GET 仍是写之前那一次，
    // 旧快照返回 → 列表里没有新文件，但界面提示「已上传」。
    const {api, state, startLoadAll, settle} = createHarness();
    await settle(startLoadAll());

    const staleRead = api.loadAssetInbox();          // 写前发出的普通读取，还在途
    assert.equal(state.requests.length, 1);

    const uploading = api.uploadAssetInboxFiles([{name: 'fresh.png'}]);
    assert.equal(state.requests.length, 2, '上传请求应发出');

    state.pending[1].resolve({count: 1});            // POST 成功
    await flush();
    assert.equal(
        state.requests.length,
        3,
        '写后必刷：POST 成功后必须另发一条 GET，而不是复用写前那条在途请求'
    );

    // 写前那条晚到：它是旧快照，必须被丢弃。
    state.pending[0].resolve({items: [{id: 'stale'}], settings: {unread_count: 3}});
    await staleRead;
    assert.equal(api.inboxItems.length, 0, '写前快照不得写回列表');
    assert.equal(api.inboxSettings, null, '写前快照不得写回设置');

    state.pending[2].resolve({items: [{id: 'fresh'}], settings: {unread_count: 1}});
    await uploading;
    assert.equal(api.inboxItems.length, 1);
    assert.equal(api.inboxItems[0].id, 'fresh', '写后读取的结果才是当前真相');
});

test('AC2: 保存收集箱位置成功后同样重读，不能显示旧路径', async () => {
    // 同一条单飞逻辑的第二个写入口：PUT /asset-inbox/settings 成功后复用写前快照，
    // 会让界面「提示已更新、设置里还是旧路径」。
    const {api, state, startLoadAll, settle} = createHarness();
    await settle(startLoadAll());
    state.settingsPath = '/new/inbox';

    const staleRead = api.loadAssetInbox();
    const saving = api.saveAssetInboxSettings();
    assert.equal(state.requests.length, 2, '写前读取 + PUT 设置');

    state.pending[1].resolve({applied: true, migrated: [], conflicts: [], skipped: []});
    await flush();
    assert.equal(state.requests.length, 3, 'PUT 成功后必须另发一条 GET');

    state.pending[0].resolve({items: [], settings: {path: '/old/inbox'}});
    await staleRead;
    assert.equal(api.inboxSettings, null, '写前快照不得写回设置');

    state.pending[2].resolve({items: [], settings: {path: '/new/inbox'}});
    await saving;
    assert.equal(api.inboxSettings.path, '/new/inbox', '写后读取才是生效的位置');
    assert.deepEqual(state.failures, []);
});


test('AC2: 首屏 A 在途时真实「添加公网 URL」入口启动 B，A 落地不得放行延后块', async () => {
    // 审核官第 3 轮复现：首屏列表 A 仍 pending 时，真实 loadAll 已经能渲染启用的
    // 「添加 URL」按钮；添加成功后的无参 loadRegistryBootstrap() 会 abort A 并启动 B。
    // B 若不登记代际，A 的旧终态就会放行延后链路，inbox/governance 抢在 B 前面。
    const {api, state, startLoadAll, settle} = createHarness();
    const first = startLoadAll();
    await flush();
    api.scheduleDeferredBootRequests();
    await flush();
    assert.equal(state.idle.length, 0);

    const adding = api.createRegistryRemoteAsset();   // 真实入口，B 在途
    adding.catch(() => {});
    await flush();
    assert.equal(state.bootstraps.length, 2, '添加 URL 必须发起新的列表请求 B');
    const second = state.bootstraps[1];
    assert.equal(api.bootstrapPending, true, 'B 已登记为当前代际且仍在途');

    await settle(first, false);      // A 被 abort 后落地
    assert.equal(api.bootstrapPending, true, 'A 的旧终态不得清掉 B 的在途标记');
    assert.equal(state.idle.length, 0, 'A 落地不得放行延后调度');
    assert.deepEqual(state.requests, [], 'B 未落地前非首屏链路一条都不许发');

    await settle(second);
    await adding;
    assert.equal(api.bootstrapPending, false);
    assert.equal(state.idle.length, 1, 'B 落地后才放行延后调度');

    state.idle.shift()();
    await flush();
    // QIN-591：延后块经网关补发（gwLoad 动态导入异步化），到达顺序不再固定，契约仍是两条都补齐。
    assert.deepEqual([...state.requests].sort(), [
        '/api/asset-inbox?limit=500',
        '/api/asset-registry/governance/suggestions',
    ]);
});

test('AC2: 首屏 A 在途时真实「删除公网 URL」入口同样登记 B 代际', async () => {
    const {api, state, startLoadAll, settle} = createHarness();
    const first = startLoadAll();
    await flush();
    api.scheduleDeferredBootRequests();
    await flush();

    const deleting = api.deleteRegistryRemoteAsset('asset-1');   // 真实入口
    deleting.catch(() => {});
    await flush();
    assert.equal(state.bootstraps.length, 2, '删除 URL 必须发起新的列表请求');
    const second = state.bootstraps[1];

    await settle(first, false);
    assert.equal(api.bootstrapPending, true, '旧代际落地不得放行');
    assert.equal(state.idle.length, 0);
    assert.deepEqual(state.requests, []);

    await settle(second);
    await deleting;
    assert.equal(state.idle.length, 1);
    state.idle.shift()();
    await flush();
    // QIN-591：延后块经网关补发（gwLoad 动态导入异步化），到达顺序不再固定，契约仍是两条都补齐。
    assert.deepEqual([...state.requests].sort(), [
        '/api/asset-inbox?limit=500',
        '/api/asset-registry/governance/suggestions',
    ]);
});

test('AC2: 所有替换当前列表的入口都统一走代际登记协议', () => {
    // 上面两条行为用例驱动真实入口；这条清单断言防的是「将来新增入口又漏登记」。
    const helper = sliceFunction(MANAGER, 'refreshRegistryBootstrapTracked');
    assert.match(
        helper,
        /trackRegistryBootstrapSettled\(loadRegistryBootstrap\(\)\)/,
        '统一入口必须把无参引导请求登记到代际协议'
    );
    // 只统计真实代码：整行 // 注释里说明实现时写到的函数名不算调用点。
    const codeOnly = MANAGER.replace(/^\s*\/\/.*$/gm, '');
    const directCalls = [...codeOnly.matchAll(/(?<!function )loadRegistryBootstrap\(\s*\)/g)];
    assert.equal(directCalls.length, 1, '无参直调只允许出现在 refreshRegistryBootstrapTracked 内');
    for(const name of ['createRegistryRemoteAsset', 'deleteRegistryRemoteAsset']){
        assert.match(
            sliceFunction(MANAGER, name),
            /await refreshRegistryBootstrapTracked\(\);/,
            `${name} 必须走统一登记入口`
        );
    }
    // 共享入口：搜索/筛选/翻页/排序/看板切换都经 refreshRegistryAssets，必须登记代际；
    // 且它返回的是原始请求（业务错误可传播），不是吞掉拒绝的终态观察 promise。
    const shared = sliceFunction(MANAGER, 'refreshRegistryAssets');
    assert.match(
        shared,
        /trackRegistryBootstrapSettled\(refreshRegistryAssetsRequest\(renderNow\)\)/,
        '所有替换当前列表的调用都必须经共享入口登记代际'
    );
    // 网络实现只允许被共享入口调用一次：37 个替换列表的调用点都必须经过登记，
    // 将来有人直接调 refreshRegistryAssetsRequest 绕过代际协议时这条立即变红。
    const implCalls = [...codeOnly.matchAll(/(?<!function )refreshRegistryAssetsRequest\(/g)];
    assert.equal(implCalls.length, 1, '替换列表的网络实现不得被共享入口之外直接调用');
    const tracked = sliceFunction(MANAGER, 'trackRegistryBootstrapSettled');
    assert.match(tracked, /registryBootstrapSettled = request\.then\(settleGeneration, settleGeneration\);/);
    assert.match(tracked, /\breturn request;/, '终态登记必须原样返回业务请求，不得吞掉回读拒绝');
    // 允许根是 void 路径：既要登记代际，也必须接住回读拒绝。
    const rootsSave = sliceFunction(MANAGER, 'saveAllowedRootsRequest');
    assert.match(rootsSave, /void refreshRegistryBootstrapTracked\(\)\.catch\(/, '允许根 void 路径必须处理回读拒绝');
    // loadAll 自己的接线仍是该路径的登记点，删掉它必须仍被行为用例抓住。
    assert.match(MANAGER, /trackRegistryBootstrapSettled\(registryBootstrap\);/, 'loadAll 接线必须保留');
});

test('AC2: idle 已排队后刷新，收集箱不得提前发出且治理补取不得丢失', async () => {
    // 审核官第 3 轮建议：dispatch 只按「排队时」的终态判断，idle 回调真正执行时
    // 如果用户又刷新开了新一代，收集箱会提前发出，治理建议还会被 pending 闸门挡下后无人补取。
    const {api, state, startLoadAll, settle} = createHarness();
    const first = startLoadAll();
    await flush();
    api.scheduleDeferredBootRequests();
    await settle(first);                 // A 落地：idle 已排队但尚未执行
    assert.equal(state.idle.length, 1);
    assert.deepEqual(state.requests, []);

    const second = startLoadAll();       // 刷新开新一代
    await flush();
    assert.equal(api.bootstrapPending, true);

    state.idle.shift()();                // 浏览器此刻才触发空闲回调
    await flush();
    assert.deepEqual(state.requests, [], 'B 仍在途，idle 触发也不许提前发请求');

    await settle(second);
    await flush();
    // QIN-591：延后块经网关补发（gwLoad 动态导入异步化），到达顺序不再固定；
    // 行为契约不变：B 落地后两条链路都要补齐，治理不能被闸门挡下后丢失。
    assert.deepEqual(
        [...state.requests].sort(),
        ['/api/asset-inbox?limit=500', '/api/asset-registry/governance/suggestions'],
        'B 落地后两条链路都要补齐，治理不能被闸门挡下后丢失'
    );
});


test('AC2: 首屏 A 在途时真实「搜索输入」启动 B，A 旧终态不得放行延后块', async () => {
    // 审核官第 4 轮复现：首屏 A pending 时搜索框已可用；输入触发 scheduleSearchRender →
    // refreshRegistryAssets(true) 会 ++registryRequestSeq 并 abort A，替换当前列表。
    // 若共享入口不登记代际，A 落地即把 pending 清 false，B 还在途就发了 inbox/governance。
    const {api, state, startLoadAll, settle} = createHarness();
    const first = startLoadAll();
    await flush();
    api.scheduleDeferredBootRequests();
    await flush();
    assert.equal(state.idle.length, 0);

    // 真实搜索输入：scheduleSearchRender 用 setTimeout 延迟，再走共享入口。
    api.scheduleSearchRender('registrySearch');
    assert.equal(state.timers.length, 1, '搜索输入应排入一次延迟渲染');
    state.timers.shift()();
    await flush();
    assert.deepEqual(state.searchCalls, [true], '搜索必须经共享入口 refreshRegistryAssets');
    assert.equal(state.bootstraps.length, 2, '搜索发起新的列表请求 B');
    const second = state.bootstraps[1];
    assert.equal(api.bootstrapPending, true, 'B 已登记为当前代际且仍在途');

    await settle(first, false);      // A 被 abort 后落地
    assert.equal(api.bootstrapPending, true, 'A 的旧终态不得清掉 B 的在途标记');
    assert.equal(state.idle.length, 0, 'A 落地不得放行延后调度');
    assert.deepEqual(state.requests, [], 'B 未落地前非首屏链路一条都不许发');

    await settle(second);
    await flush();
    assert.equal(api.bootstrapPending, false);
    assert.equal(state.idle.length, 1, 'B 落地后才放行延后调度');
    state.idle.shift()();
    await flush();
    // QIN-591：延后块经网关补发（gwLoad 动态导入异步化），到达顺序不再固定，契约仍是两条都补齐。
    assert.deepEqual([...state.requests].sort(), [
        '/api/asset-inbox?limit=500',
        '/api/asset-registry/governance/suggestions',
    ]);
});

test('AC2: 首屏 A 在途时筛选/排序直接替换列表，共享入口同样登记 B 代际', async () => {
    // 筛选、排序、翻页、看板切换都直接调 refreshRegistryAssets(...)，与搜索共用同一入口。
    const {api, state, startLoadAll, settle} = createHarness();
    const first = startLoadAll();
    await flush();
    api.scheduleDeferredBootRequests();
    await flush();

    const filtering = api.refreshRegistryAssets(false);   // 真实共享入口（非桩）
    filtering.catch(() => {});
    await flush();
    assert.deepEqual(state.searchCalls, [false], '筛选经同一共享入口');
    assert.equal(state.bootstraps.length, 2);
    const second = state.bootstraps[1];
    assert.equal(api.bootstrapPending, true, '筛选 B 登记为当前代际且仍在途');

    await settle(first, false);
    assert.equal(api.bootstrapPending, true, '旧代际落地不得放行');
    assert.equal(state.idle.length, 0);
    assert.deepEqual(state.requests, []);

    await settle(second);
    await filtering;
    assert.equal(state.idle.length, 1);
    state.idle.shift()();
    await flush();
    // QIN-591：延后块经网关补发（gwLoad 动态导入异步化），到达顺序不再固定，契约仍是两条都补齐。
    assert.deepEqual([...state.requests].sort(), [
        '/api/asset-inbox?limit=500',
        '/api/asset-registry/governance/suggestions',
    ]);
});

test('AC2: 公网 URL 写入成功但列表回读 500 时，明确提示「已写入但刷新失败」', async () => {
    // 审核官第 4 轮：helper 旧版返回终态观察 promise，把回读 500 吞成「已加入」，
    // 列表还是旧值且没有任何错误提示。终态登记与业务错误传播必须分离。
    const {api, state} = createHarness();
    const adding = api.createRegistryRemoteAsset();
    await flush();
    assert.equal(state.bootstraps.length, 1, '写入成功后应发起一次列表回读');

    state.bootstraps[0].reject(new Error('registry list 500'));
    await adding;                       // 入口内部接住，不向外抛
    await flush();

    assert.ok(
        !state.statuses.some(text => text.includes('已加入')),
        `回读失败时不得报「已加入」：${JSON.stringify(state.statuses)}`
    );
    assert.ok(
        state.statuses.some(text => /已写入.*刷新失败/.test(text)),
        `必须明确提示「已写入但刷新失败」：${JSON.stringify(state.statuses)}`
    );
    assert.ok(
        state.failures.some(([title, detail]) => /已写入.*刷新失败/.test(title) && detail.includes('500')),
        `错误详情必须带上真实的 500：${JSON.stringify(state.failures)}`
    );
    // 终态不受业务拒绝影响：延后链路仍能在该代际落地后放行。
    assert.equal(api.bootstrapPending, false, '回读失败也算当前代际终态');
});

test('AC2: 删除公网 URL 成功但列表回读 500 时同样明确提示', async () => {
    const {api, state} = createHarness();
    const deleting = api.deleteRegistryRemoteAsset('asset-1');
    await flush();
    assert.equal(state.bootstraps.length, 1);

    state.bootstraps[0].reject(new Error('registry list 500'));
    await deleting;
    await flush();

    assert.ok(
        !state.statuses.includes('公网 URL 资产已删除'),
        `回读失败时不得报成功文案：${JSON.stringify(state.statuses)}`
    );
    assert.ok(state.statuses.some(text => /已删除.*刷新失败/.test(text)));
    assert.ok(state.failures.some(([, detail]) => detail.includes('500')));
});

test('AC2: 允许根设置保存成功但列表 500 时，void 路径接住拒绝并明确提示', async () => {
    const {api, state} = createHarness();
    const saving = api.saveAllowedRootsRequest('/api/storage-settings/roots', 'POST', {path:'/tmp/x'});
    await flush();
    state.pending[0].resolve({roots: [], revision: 2});   // 写入 POST 成功
    await flush();
    assert.equal(state.bootstraps.length, 1, '设置成功后应发起一次列表回读');

    state.bootstraps[0].reject(new Error('registry list 500'));
    await saving;
    await flush();

    assert.ok(
        state.statuses.some(text => /已保存.*刷新失败/.test(text)),
        `void 路径必须接住拒绝并提示：${JSON.stringify(state.statuses)}`
    );
    assert.ok(state.failures.some(([, detail]) => detail.includes('500')));
    assert.equal(api.bootstrapPending, false, '回读失败同样算终态');
});

test('AC2: idle 已排队后搜索，收集箱不得提前发出且治理补取不得丢失', async () => {
    // 审核官建议项：idle 已排队时若发生「替换列表」的搜索，dispatch 早已按旧代际放行，
    // 早期实现会提前发 inbox 并让治理被 pending 闸门挡下后无人补取。
    const {api, state, startLoadAll, settle} = createHarness();
    const first = startLoadAll();
    await flush();
    api.scheduleDeferredBootRequests();
    await settle(first);                 // A 落地：idle 已排队但尚未执行
    assert.equal(state.idle.length, 1);
    assert.deepEqual(state.requests, []);

    api.scheduleSearchRender('registrySearch');
    state.timers.shift()();               // 搜索 B 启动，登记新一代
    await flush();
    assert.equal(api.bootstrapPending, true);
    const second = state.bootstraps[1];

    state.idle.shift()();                // 浏览器此刻才触发空闲回调
    await flush();
    assert.deepEqual(state.requests, [], 'B 仍在途，idle 触发也不许提前发请求');

    await settle(second);
    await flush();
    // QIN-591：延后块经网关补发（gwLoad 动态导入异步化），到达顺序不再固定；
    // 行为契约不变：B 落地后两条链路都要补齐，治理不能被闸门挡下后丢失。
    assert.deepEqual(
        [...state.requests].sort(),
        ['/api/asset-inbox?limit=500', '/api/asset-registry/governance/suggestions'],
        'B 落地后两条链路都要补齐，治理不能被闸门挡下后丢失'
    );
});
