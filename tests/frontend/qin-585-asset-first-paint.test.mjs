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
import {readFileSync} from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';

const read = path => readFileSync(new URL(`../../${path}`, import.meta.url), 'utf8');

const SHELL_PAGE = read('web/pages/assets.html');
const EMBED_PAGE = read('web/embeds/asset-manager.html');
const UTILITIES = read('web/css/base/tailwind-utilities.css');
const MANAGER = read('web/js/modules/asset-manager.js');

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

const UTILITY_COUNTS = selectorCounts(UTILITIES);
const ruleCount = token => UTILITY_COUNTS.get(token) || 0;

// ---------------------------------------------------------------------------
// 行为级夹具：把真实的顶层函数与它们读写的模块级状态一起抽出来执行。
// 状态声明也从源码里取，避免测试里复制一份「默认值」而与实现悄悄分叉。
// ---------------------------------------------------------------------------

const STATE_DECLARATIONS = [
    'inboxItems',
    'inboxSettings',
    'inboxLoaded',
    'inboxLoading',
    'inboxError',
    'inboxRequestSeq',
    'inboxInFlight',
    'inboxInFlightToken',
    'registryBootstrapPending',
    'registryBootstrapSettled',
    'governanceSuggestionsData',
    'governanceSuggestionsLoading',
    'governanceSuggestionsError',
    'governanceDrawerOpen',
];

const BOOT_FUNCTIONS = [
    'trackRegistryBootstrapSettled',
    'waitRegistryBootstrapSettled',
    'loadAssetInbox',
    'markAssetInboxViewed',
    'assetInboxUnreadCount',
    'loadGovernanceSuggestions',
    'scheduleDeferredBootRequests',
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

function createHarness(){
    const state = {requests: [], pending: [], idle: [], timers: [], renders: 0};
    const context = vm.createContext({
        console,
        Promise,
        Math,
        Number,
        String,
        Array,
        Object,
        Set,
        Map,
        activeTab: 'registry',
        // 假 HTTP：请求挂起，由用例决定何时、以什么内容返回。
        assetManagerHttp: {
            request(url){
                const pending = {url, kind: 'http'};
                pending.promise = new Promise((resolve, reject) => {
                    pending.resolve = resolve;
                    pending.reject = reject;
                });
                state.pending.push(pending);
                state.requests.push(url);
                return pending.promise;
            },
        },
        fetch(url){
            const pending = {url, kind: 'fetch'};
            pending.promise = new Promise((resolve, reject) => {
                pending.resolve = resolve;
                pending.reject = reject;
            });
            state.pending.push(pending);
            state.requests.push(url);
            return pending.promise;
        },
        apiJsonResponse: response => response,
        render(){ state.renders += 1; },
        setStatus(){},
        showAssetNotice(){},
        showAssetFailure(){},
        updateGovernanceTrigger(){},
        renderGovernanceDrawer(){},
        setTimeout(fn){ state.timers.push(fn); return state.timers.length; },
        window: {
            requestIdleCallback(fn){ state.idle.push(fn); return state.idle.length; },
        },
    });

    const body = [
        ...STATE_DECLARATIONS.map(name => sliceDeclaration(MANAGER, name)),
        ...BOOT_FUNCTIONS.map(name => sliceFunction(MANAGER, name)),
        `return {
            trackRegistryBootstrapSettled,
            waitRegistryBootstrapSettled,
            loadAssetInbox,
            markAssetInboxViewed,
            loadGovernanceSuggestions,
            scheduleDeferredBootRequests,
            assetInboxUnreadCount,
            get inboxItems(){ return inboxItems; },
            set inboxItems(value){ inboxItems = value; },
            get inboxSettings(){ return inboxSettings; },
            set inboxSettings(value){ inboxSettings = value; },
            get inboxLoading(){ return inboxLoading; },
            get inboxInFlight(){ return inboxInFlight; },
            get registryBootstrapPending(){ return registryBootstrapPending; },
            set governanceDrawerOpen(value){ governanceDrawerOpen = value; },
        };`,
    ].join('\n');
    const api = vm.runInNewContext(`(() => {\n${body}\n})()`, context, {filename: 'asset-manager.boot-harness.js'});

    // 让首屏列表请求进入「在途」状态，并暴露手动落地（成功/失败）开关。
    const beginBootstrap = () => {
        const handle = {resolve: null, reject: null};
        const request = new Promise((resolve, reject) => {
            handle.resolve = resolve;
            handle.reject = reject;
        });
        api.trackRegistryBootstrapSettled(request);
        return handle;
    };
    const settle = async (bootstrap, ok = true) => {
        if(bootstrap){
            if(ok) bootstrap.resolve();
            else bootstrap.reject(new Error('bootstrap failed'));
        }
        await flush();
    };
    return {api, state, beginBootstrap, settle};
}

const flush = async () => {
    for(let i = 0; i < 6; i += 1) await new Promise(resolve => setImmediate(resolve));
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

test('AC1: 补齐段不为同一工具类重复声明规则', () => {
    // 运行时删除后手工补类时最容易犯的错：往快照尾部再追加一条已存在的规则。
    // 重复声明不报错，只是让快照里同一个类出现两遍，后续 diff 与体积都不再好核对。
    const appended = ['bg-white/15', 'border-orange-500/30', 'cursor-not-allowed', 'opacity-50', 'text-orange-300'];
    for(const token of appended){
        assert.equal(ruleCount(token), 1, `${token} 的规则必须唯一（当前 ${ruleCount(token)} 条）`);
    }
});

test('AC1: 静态快照自带 preflight，不依赖运行时补齐基础样式', () => {
    // 去掉运行时后若丢了 preflight，卡片与表单控件会出现浏览器默认外观。
    assert.match(UTILITIES, /box-sizing:border-box/);
    assert.match(UTILITIES, /margin:0/);
    assert.match(UTILITIES, /--tw-border-spacing-x:0/);
});

test('AC2: 首屏列表落地前，启动期治理建议请求不发出去', async () => {
    const {api, state, beginBootstrap, settle} = createHarness();
    const bootstrap = beginBootstrap();
    assert.equal(api.registryBootstrapPending, true);

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

test('AC2: 延后调度等的是首屏列表终态，而不是 loadAll 的失败分支', async () => {
    const {api, state, beginBootstrap, settle} = createHarness();
    const bootstrap = beginBootstrap();

    // 资产库/本地设置先失败会走 loadAll 的 catch，此时调度被调用但列表仍在途。
    api.scheduleDeferredBootRequests();
    await flush();
    assert.equal(state.idle.length, 0, '列表还在途时不得把延后块排进空闲时段');
    assert.deepEqual(state.requests, []);

    await settle(bootstrap, false);   // 列表请求也失败，同样算终态
    assert.equal(state.idle.length, 1, '列表终态后才允许调度延后块');

    state.idle.shift()();
    await flush();
    assert.deepEqual(
        state.requests,
        ['/api/asset-inbox?limit=500', '/api/asset-registry/governance/suggestions'],
        '延后块落地后收集箱与治理建议都应补发'
    );
});

test('AC2: 收集箱并发读取只发一次 GET', async () => {
    const {api, state, beginBootstrap, settle} = createHarness();
    await settle(beginBootstrap());

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

test('AC2: 角标清零后，晚到的旧收集箱响应不得把未读数改回去', async () => {
    const {api, state, beginBootstrap, settle} = createHarness();
    api.inboxSettings = {unread_count: 7};
    await settle(beginBootstrap());

    const reading = api.loadAssetInbox();
    const viewed = api.markAssetInboxViewed();
    assert.equal(state.requests.length, 2, '读取与清零各自一条请求');
    state.pending[1].resolve({ok: true});     // POST /viewed 先返回
    await viewed;
    assert.equal(api.inboxSettings.unread_count, 0);

    state.pending[0].resolve({items: [{id: 'a'}], settings: {unread_count: 7}});
    await reading;
    assert.equal(api.inboxSettings.unread_count, 0, '过期响应必须被丢弃，不得把角标改回未读');
    assert.equal(api.inboxItems.length, 0, '过期响应同样不得写入列表');
});
