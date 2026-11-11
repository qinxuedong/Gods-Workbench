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
 * QIN-591 返工：延后域网关的「真实模块入口 + 返回值契约」回归护栏。
 *
 * 审核提出的三类回归都不是源文本能否匹配的问题，而是运行时契约：
 *  1. 4 个模块漏导入分类 helper → 真实函数调用 ReferenceError；
 *  2. 6 处全选把 gwCall 返回的 Promise 当数组 → `.forEach is not a function`；
 *  3. 首屏仍在加载延后域 → AC1 未达成。
 * 本用例用真实源码驱动：注册面/调用面闭合、helper 导入闭合、网关排队顺序与返回值、
 * 以及「gwCall 结果不得当同步值使用」的静态护栏。
 */

import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync, existsSync} from 'node:fs';
import path from 'node:path';
import vm from 'node:vm';
import {fileURLToPath} from 'node:url';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.resolve(HERE, '../..');
const MODULE_ROOT = path.join(ROOT, 'web/js/modules');
const MAIN = path.join(MODULE_ROOT, 'asset-manager.js');
const PACKAGE_DIR = path.join(MODULE_ROOT, 'asset-manager');
const EMBED_HTML = path.join(ROOT, 'web/embeds/asset-manager.html');

const mainSource = readFileSync(MAIN, 'utf8');
const packageFiles = [
    path.join(PACKAGE_DIR, 'state.js'),
    ...['batch', 'canvas-bridge', 'coverflow-bridge', 'governance', 'inbox', 'legacy-pages',
        'lut', 'media-canvas', 'organize', 'preview', 'recycle', 'settings', 'tags', 'upload']
        .map(name => path.join(PACKAGE_DIR, `${name}.js`)),
];

/** 从 `open` 指向的 `{` 开始做括号配对，返回配对 `}` 的下标。 */
function matchBrace(source, open) {
    let depth = 0;
    for (let i = open; i < source.length; i += 1) {
        const ch = source[i];
        if (ch === '{') depth += 1;
        else if (ch === '}') {
            depth -= 1;
            if (depth === 0) return i;
        }
    }
    throw new Error('括号未配对');
}

/** 从 `open` 指向的 `(` 开始做括号配对，返回配对 `)` 的下标（找不到返回 -1）。 */
function matchParen(source, open) {
    let depth = 0;
    for (let i = open; i < source.length; i += 1) {
        const ch = source[i];
        if (ch === '(') depth += 1;
        else if (ch === ')') {
            depth -= 1;
            if (depth === 0) return i;
        }
    }
    return -1;
}

/** 取 `anchor` 之后第一个 `{...}` 块的内部文本。 */
function blockAfter(source, anchor) {
    const start = source.indexOf(anchor);
    assert.ok(start >= 0, `找不到锚点: ${anchor}`);
    const open = source.indexOf('{', start);
    const close = matchBrace(source, open);
    return source.slice(open + 1, close);
}

function loaderDomains() {
    const body = blockAfter(mainSource, 'const GW_DOMAIN_LOADERS = {');
    const out = new Map();
    for (const match of body.matchAll(/'([a-z-]+)':\s*\(\)\s*=>\s*import\('\.\/asset-manager\/([a-z-]+)\.js'\)/g)) {
        out.set(match[1], match[2]);
    }
    return out;
}

function registrationFace() {
    const face = new Map();
    for (const file of packageFiles) {
        const text = readFileSync(file, 'utf8');
        for (const match of text.matchAll(/GW_DEFERRED\[\s*'([^']+)'\s*\]\s*=\s*\{/g)) {
            const open = text.indexOf('{', match.index + match[0].length - 1);
            const close = matchBrace(text, open);
            const names = new Set();
            for (const line of text.slice(open + 1, close).split('\n')) {
                const name = line.trim().replace(/,$/, '');
                if (/^[A-Za-z_$][\w$]*$/.test(name)) names.add(name);
            }
            face.set(match[1], names);
        }
    }
    return face;
}

test('QIN-591：14 个延后域都有真实模块文件，且注册面覆盖全部 gwCall/gwLoad 调用', () => {
    const domains = loaderDomains();
    assert.equal(domains.size, 14, `延后域数量应为 14，实际 ${domains.size}`);
    const face = registrationFace();

    for (const [domain, file] of domains) {
        const absolute = path.join(PACKAGE_DIR, `${file}.js`);
        assert.ok(existsSync(absolute), `延后域 ${domain} 的模块文件不存在: ${file}.js`);
        assert.ok(face.has(domain), `延后域 ${domain} 没有任何 GW_DEFERRED 注册块`);
    }

    // 调用面必须全部命中注册面：漏注册会在运行时报「未注册」，而不是构建期报错。
    const missing = [];
    for (const match of mainSource.matchAll(/gwCall\(\s*'([a-z-]+)'\s*,\s*'([A-Za-z_$][\w$]*)'/g)) {
        const [, domain, fn] = match;
        const registered = face.get(domain);
        if (!registered) missing.push(`${domain}/${fn}（域未注册）`);
        else if (!registered.has(fn)) missing.push(`${domain}/${fn}`);
    }
    assert.deepEqual(missing, [], `调用面存在未注册函数: ${missing.join(', ')}`);

    for (const match of mainSource.matchAll(/gwLoad\(\s*'([a-z-]+)'\s*\)/g)) {
        assert.ok(domains.has(match[1]), `gwLoad 使用了未声明的域: ${match[1]}`);
    }
});

test('QIN-591：引用分类 helper 的延后域必须从主文件导入同一实例', () => {
    const helpers = [
        'assetClassificationChips',
        'assetClassificationEntryGroup',
        'assetClassificationSearchText',
        'groupedAssetClassificationEntries',
        'parseAssetClassFilterKey',
    ];
    const offenders = [];
    for (const file of packageFiles) {
        const text = readFileSync(file, 'utf8');
        const importBlocks = [...text.matchAll(/import\s*\{([\s\S]*?)\}\s*from\s*'\.\.\/asset-manager\.js'/g)]
            .map(match => match[1]);
        const imported = new Set();
        for (const block of importBlocks) {
            for (const part of block.split(',')) {
                const name = part.trim().split(/\s+as\s+/)[0].trim();
                if (name) imported.add(name);
            }
        }
        for (const helper of helpers) {
            const used = new RegExp(`(^|[^\\w$.])${helper}\\s*\\(`, 'm').test(text);
            if (used && !imported.has(helper)) {
                offenders.push(`${path.basename(file)} 使用了 ${helper} 但没有从主文件导入`);
            }
        }
    }
    assert.deepEqual(offenders, [], offenders.join('; '));
});

/**
 * 取 `gwCall(...)` 之后**真正**紧跟的内容，允许外面再包一层或多层分组括号：
 * `(gwCall(...)).forEach` 与 `gwCall(...).forEach` 语义完全相同，只看自己的右括号会被
 * 括号形式绕过（第二轮审核的「括号形式去 await」变异正是这样 7/7 全绿的）。
 * 只剥分组括号；`foo(gwCall(...))` 这种调用括号前的标识符说明 `.forEach` 属于 `foo` 的
 * 返回值，必须原样返回、不能算成 gwCall 的误用。
 */
function restAfterGwCall(line, at, close) {
    let open = at;
    let cursor = close;
    for (let guard = 0; guard < 8; guard += 1) {
        const rest = line.slice(cursor + 1);
        if (!/^\s*\)/.test(rest)) return rest;
        let before = open - 1;
        while (before >= 0 && /\s/.test(line[before])) before -= 1;
        if (before < 0 || line[before] !== '(') return rest;
        if (before > 0 && /[\w$)\]]/.test(line[before - 1])) return rest;
        const outerClose = matchParen(line, before);
        if (outerClose < 0) return rest;
        open = before;
        cursor = outerClose;
    }
    return line.slice(cursor + 1);
}

const SYNC_VALUE_MEMBERS =
    /^\s*\.\s*(forEach|map|filter|find|findIndex|some|every|reduce|join|slice|length|at|indexOf)\b/;

/** 找出「把 gwCall 的 Promise 当数组/成员用」的行（含括号包裹形式）。 */
function scanSyncValueMisuse(source) {
    const offenders = [];
    source.split('\n').forEach((line, index) => {
        let from = 0;
        for (;;) {
            const at = line.indexOf('gwCall(', from);
            if (at < 0) break;
            from = at + 'gwCall('.length;
            const close = matchParen(line, at + 'gwCall('.length - 1);
            if (close < 0) continue;
            // `await gwCall(...)` / `(await gwCall(...))` 都是正确用法：结果已是真实值。
            if (/await\s+$/.test(line.slice(0, at))) continue;
            if (SYNC_VALUE_MEMBERS.test(restAfterGwCall(line, at, close))) {
                offenders.push(`asset-manager.js:${index + 1} ${line.trim().slice(0, 120)}`);
            }
        }
    });
    return offenders;
}

test('QIN-591：gwCall 结果不得当同步值使用（含括号包裹形式，全选把 Promise 当数组的回归）', () => {
    const offenders = scanSyncValueMisuse(mainSource);
    assert.deepEqual(offenders, [], `gwCall 返回 Promise，不能直接取数组/成员：\n${offenders.join('\n')}`);

    // 护栏自检：括号形式与普通形式必须同样命中，正确写法不得误报。
    // 用 `\` 转义掉源码里的真实调用，避免本断言自己被上面的扫描捡走。
    const cases = [
        {code: "x = gwCall('a', 'b').forEach(item => item);", hit: true},
        {code: "x = (gwCall('a', 'b')).forEach(item => item);", hit: true},
        {code: "x = ((gwCall('a', 'b'))).forEach(item => item);", hit: true},
        {code: "x = (gwCall('a', 'b')).length;", hit: true},
        {code: "x = (await gwCall('a', 'b')).forEach(item => item);", hit: false},
        {code: "await gwCall('a', 'b');", hit: false},
        {code: "x = wrapper(gwCall('a', 'b')).forEach(item => item);", hit: false},
        {code: "if(gwCall('a', 'b').length > 1) go();", hit: true},
    ];
    for (const sample of cases) {
        const found = scanSyncValueMisuse(sample.code).length > 0;
        assert.equal(found, sample.hit, `护栏判定错误: ${sample.code}（期望 ${sample.hit ? '命中' : '不命中'}）`);
    }
});

test('QIN-591：6 处全选执行真实源码块后必须选中全部条目（去掉 await 必抛错）', async () => {
    const guards = [
        {attr: '[data-localup-select-all]', set: 'selectedLocalUploadIds', domain: 'upload', fn: 'localUploadItems'},
        {attr: '[data-canvas-asset-select-all]', set: 'selectedCanvasAssetIds', domain: 'canvas', fn: 'currentCanvasAssetItems'},
        {attr: '[data-local-select-all]', set: 'selectedLocalIds', domain: 'upload', fn: 'localItemsForFolder'},
        {attr: '[data-workflow-select-all]', set: 'selectedWorkflowIds', domain: 'organize', fn: 'currentWorkflowItems'},
        {attr: '[data-asset-select-all]', set: 'selectedAssetIds', domain: 'organize', fn: 'currentAssetItems'},
        {attr: '[data-prompt-select-all]', set: 'selectedPromptIds', domain: 'organize', fn: 'currentPromptItems'},
    ];
    const items = [{id: 'a'}, {id: 'b'}, {id: 'c'}];

    for (const {attr, set, domain, fn} of guards) {
        // 直接取真实源码里该分支的 {...} 块执行：不是文本匹配，去掉 await 会真实抛 TypeError。
        const body = blockAfter(mainSource, `closest?.('${attr}')`);
        const sandbox = {
            gwCall: (calledDomain, calledFn) => {
                assert.equal(calledDomain, domain, `${attr} 应调用 ${domain} 域`);
                assert.equal(calledFn, fn, `${attr} 应调用 ${domain}/${fn}`);
                return Promise.resolve(items);
            },
            [set]: new Set(),
            workflowAssetId: item => item.id,
            pendingBatchDelete: '',
            render() {},
            console,
        };
        vm.createContext(sandbox);
        const run = body => vm.runInContext(`(async () => {${body}})()`, sandbox, {filename: `qin-591-${attr}.js`});

        await run(body);
        assert.equal(sandbox[set].size, items.length,
            `${attr} 点全选后必须选中 ${items.length} 项（gwCall 返回 Promise，漏 await 会静默选中 0）`);

        // 同一段真实源码去掉 await：必须抛错，否则上面的断言可能只是「没跑到」。
        const stripped = body.replace(/\bawait\s+/g, '');
        assert.notEqual(stripped, body, `${attr} 分支里没有 await，全选仍在把 Promise 当数组`);
        sandbox[set] = new Set();
        await assert.rejects(async () => { await run(stripped); }, err => {
            // 错误由 vm realm 构造，跨 realm 的 instanceof 不成立，按 name 判定。
            assert.equal(err && err.name, 'TypeError',
                `${attr} 去掉 await 后应抛 TypeError，实际 ${err && err.name}: ${err && err.message}`);
            return true;
        });
        assert.equal(sandbox[set].size, 0, `${attr} 去掉 await 后不应选中任何条目`);
    }
});

test('QIN-591：入口 URL 不带查询串（否则与域内 import 形成两个 ESM 实例）', () => {
    const html = readFileSync(EMBED_HTML, 'utf8');
    const entry = html.match(/<script[^>]+type="module"[^>]+src="([^"]*\/modules\/asset-manager\.js[^"]*)"/);
    assert.ok(entry, '入口没有引用 asset-manager.js');
    assert.equal(entry[1].includes('?'), false, `入口不得带查询串: ${entry[1]}`);
    for (const file of packageFiles) {
        const text = readFileSync(file, 'utf8');
        assert.equal(/from\s*'\.\.\/asset-manager\.js\?/.test(text), false,
            `${path.basename(file)} 从带查询串的 URL 导入主文件`);
    }
});

/** 主文件里首卡闸门的实现片段（声明 → gwLoad 结束），供网关与启动两处夹具共用。 */
function sliceGateway() {
    const anchor = 'let deferredDomainsOpen = false;';
    const start = mainSource.indexOf(anchor);
    assert.ok(start >= 0, '找不到首卡闸门声明');
    const loadFnStart = mainSource.indexOf('\nfunction gwLoad(domain) {', start);
    assert.ok(loadFnStart > start, '找不到 gwLoad 实现');
    const open = mainSource.indexOf('{', loadFnStart);
    const close = matchBrace(mainSource, open);
    return mainSource.slice(start, close + 1);
}

/** 首屏列表终态代际的真实实现：启动夹具不能用桩，否则「等真实终态」这件事就没被测到。 */
function sliceBootstrapTerminalState() {
    const start = mainSource.indexOf('function trackRegistryBootstrapSettled(request){');
    assert.ok(start >= 0, '找不到 trackRegistryBootstrapSettled');
    const end = mainSource.indexOf('\nasync function loadAll(){', start);
    assert.ok(end > start, '找不到 loadAll 的分界');
    return mainSource.slice(start, end);
}

/** 终态代际的三个状态变量，初值从源码取，避免与本夹具漂移。 */
function sliceBootstrapState() {
    return ['registryBootstrapGeneration', 'registryBootstrapSettledGeneration', 'registryBootstrapSettled']
        .map(name => {
            const found = mainSource.match(new RegExp(`^let ${name} = ([^;]+);`, 'm'));
            assert.ok(found, `找不到 ${name} 的声明`);
            return `let ${name} = ${found[1]};`;
        })
        .join('\n');
}

/** 真实的 DOMContentLoaded 引导入口（含闸门放行与 15s 兜底）。 */
function sliceBootEntry() {
    const start = mainSource.indexOf("document.addEventListener('DOMContentLoaded'");
    assert.ok(start >= 0, '找不到 DOMContentLoaded 引导入口');
    const end = mainSource.indexOf('\n});', start);
    assert.ok(end > start, '引导入口缺少行首闭合括号');
    return mainSource.slice(start, end + 4);
}

function loadGatewaySandbox() {
    const sandbox = {
        GW_DEFERRED: {},
        GW_DOMAIN_LOADERS: {},
        console: {error() {}},
    };
    vm.createContext(sandbox);
    vm.runInContext(
        `${sliceGateway()}\n;globalThis.__gateway = {gwCall, gwLoad, openDeferredDomainGate, deferredDomainsGateOpen};`,
        sandbox,
        {filename: 'qin-591-gateway.js'},
    );
    return {sandbox, gateway: sandbox.__gateway};
}

/**
 * 真实启动夹具：真实 DOMContentLoaded 入口 + 真实网关 + 真实终态代际，只有「首屏列表请求」
 * 与外壳恢复函数是桩（它们与闸门无关）。这样「启动时立刻开闸」「列表在途时超时开闸」都会
 * 被观察到，而不是靠源文本能否匹配。
 */
function loadBootSandbox() {
    const frames = [];
    const timers = [];
    const order = [];
    const bootCalls = [];

    const sandbox = {
        GW_DEFERRED: {},
        GW_DOMAIN_LOADERS: {},
        console: {error() {}, warn() {}, log() {}},
        document: {
            readyState: 'loading',
            addEventListener(type, handler) {
                if (type === 'DOMContentLoaded') sandbox.__boot = handler;
            },
        },
        requestAnimationFrame(callback) { frames.push(callback); return frames.length; },
        setTimeout(callback, delay) { timers.push({callback, delay}); return timers.length; },
        clearTimeout() {},
        directorySettingsPage: false,
        registrySessionReady: false,
        // 以下与闸门无关，只是引导入口会调到的外壳恢复/诊断函数。
        loadGlobalDirectorySettings() {},
        tryApplyRegistrySnapshot() { bootCalls.push('snapshot'); },
        loadRegistryPrincipal() { bootCalls.push('principal'); },
        restoreRegistryBrowseSession: async () => null,
        restoreAssetTreeState: async () => {},
        applyRegistryBrowseSessionScroll: async () => {},
        saveRegistryBrowseSession() {},
        markRegistryFirstScreenReady() { bootCalls.push('firstScreenReady'); },
        startRegistryAutomaticIndexPolling() {},
        setStatus() {},
        scheduleDeferredBootRequests() { bootCalls.push('deferredBoot'); },
    };
    vm.createContext(sandbox);

    const script = [
        sliceBootstrapState(),
        sliceBootstrapTerminalState(),
        sliceGateway(),
        // 真实 loadAll 的第一件事就是登记「这一代列表请求」的终态，夹具只保留这一必需行为。
        'const __listPromise = new Promise(resolve => { globalThis.__resolveList = resolve; });',
        'function loadAll(){ return trackRegistryBootstrapSettled(__listPromise); }',
        sliceBootEntry(),
        ';globalThis.__api = {gwCall, gwLoad, isRegistryBootstrapPending, deferredDomainsGateOpen};',
    ].join('\n');
    vm.runInContext(script, sandbox, {filename: 'qin-591-boot.js'});
    assert.equal(typeof sandbox.__boot, 'function', '引导入口没有注册 DOMContentLoaded 处理函数');

    return {
        sandbox,
        order,
        bootCalls,
        run: () => sandbox.__boot(),
        settleList: () => sandbox.__resolveList(),
        /** 让微任务链跑完（真实入口里有多层 then / async）。 */
        async drain() {
            for (let i = 0; i < 8; i += 1) await new Promise(resolve => setImmediate(resolve));
        },
        /** 按轮次执行已排队的 requestAnimationFrame（首卡放行用双 rAF）。 */
        flushFrames() {
            for (let round = 0; round < 10 && frames.length; round += 1) {
                for (const callback of frames.splice(0)) callback();
            }
        },
        /** 推进假定时器：只执行到期时间 <= ms 的回调。 */
        advance(ms) {
            for (const timer of timers.splice(0)) {
                if (timer.delay <= ms) timer.callback();
                else timers.push(timer);
            }
        },
    };
}

test('QIN-591：真实启动入口在列表仍在途时不得开闸（15s 兜底也不例外），落地后按序放行', async () => {
    const harness = loadBootSandbox();
    const {sandbox, order, drain} = harness;

    sandbox.GW_DOMAIN_LOADERS.governance = () => {
        order.push('load:governance');
        sandbox.GW_DEFERRED.governance = {loadGovernanceSuggestions() { order.push('call:governance'); }};
        return Promise.resolve();
    };
    sandbox.GW_DOMAIN_LOADERS.organize = () => {
        order.push('load:organize');
        sandbox.GW_DEFERRED.organize = {
            activateAssetBoard(id) { order.push(`call:activate:${id}`); return `board:${id}`; },
        };
        return Promise.resolve();
    };

    harness.run();
    await drain();

    // ① 列表在途：闸门必须关，一个延后域都不许加载。
    assert.equal(sandbox.__api.deferredDomainsGateOpen(), false, '列表在途时闸门必须关闭');
    assert.deepEqual(order, [], '列表在途时不得加载任何延后域');

    // ② 首屏期用户调用进入队列：仍然不得触发 import。
    const queued = sandbox.__api.gwCall('organize', 'activateAssetBoard', 'canvas-assets');
    await drain();
    assert.deepEqual(order, [], '排队中的调用不得提前加载域');

    // ③ 15s 兜底不得在列表仍在途时放行（16s 慢列表下 15s 开闸 = AC1 回归）。
    harness.advance(15000);
    await drain();
    assert.equal(sandbox.__api.isRegistryBootstrapPending(), true, '夹具应保持列表在途');
    assert.equal(sandbox.__api.deferredDomainsGateOpen(), false, '列表仍在途时 15s 兜底不得开闸');
    assert.deepEqual(order, [], '列表仍在途时 15s 兜底不得加载延后域');

    // ④ 列表真实落地 → 双 rAF → 开闸 → 严格 FIFO 放行并返回真实值。
    harness.settleList();
    await drain();
    harness.flushFrames();
    await drain();
    assert.equal(sandbox.__api.deferredDomainsGateOpen(), true, '列表落地后必须开闸');
    assert.equal(await queued, 'board:canvas-assets', '排队调用必须拿到域函数的真实返回值');
    assert.deepEqual(order, ['load:governance', 'call:governance', 'load:organize', 'call:activate:canvas-assets'],
        '放行顺序必须是入队顺序');
});

test('QIN-591：域函数同步抛错必须以 reject 返回，不得同步抛出（网关返回值契约）', async () => {
    const {sandbox, gateway} = loadGatewaySandbox();
    sandbox.GW_DEFERRED.alpha = {boom() { throw new Error('sync-boom'); }};
    gateway.openDeferredDomainGate();

    let syncThrown = null;
    let request;
    try {
        request = gateway.gwCall('alpha', 'boom');
    } catch (err) {
        syncThrown = err;
    }
    assert.equal(syncThrown, null, `gwCall 不得同步抛出: ${syncThrown && syncThrown.message}`);
    assert.equal(typeof request.then, 'function', 'gwCall 必须返回 Promise');
    await assert.rejects(() => request, /sync-boom/);

    // loader 自己同步抛错同样要变成 reject，而不是把异常抛给调用方。
    sandbox.GW_DOMAIN_LOADERS.beta = () => { throw new Error('loader-boom'); };
    await assert.rejects(() => gateway.gwCall('beta', 'anything'), /loader-boom/);
});

test('QIN-591：闸门关闭时 gwCall 不加载延后域，放行后严格按入队顺序返回真实返回值', async () => {
    const {sandbox, gateway} = loadGatewaySandbox();
    const order = [];
    sandbox.GW_DOMAIN_LOADERS.alpha = () => {
        order.push('load:alpha');
        sandbox.GW_DEFERRED.alpha = {
            ping(value) {
                order.push(`ping:${value}`);
                return `pong:${value}`;
            },
        };
        return Promise.resolve();
    };

    const first = gateway.gwCall('alpha', 'ping', 'a');
    const second = gateway.gwCall('alpha', 'ping', 'b');
    assert.equal(typeof first.then, 'function', 'gwCall 必须返回 Promise');
    assert.equal(gateway.deferredDomainsGateOpen(), false);
    assert.deepEqual(order, [], '闸门关闭时不得加载任何延后域');

    gateway.openDeferredDomainGate();
    assert.equal(await first, 'pong:a');
    assert.equal(await second, 'pong:b');
    assert.deepEqual(order, ['load:alpha', 'ping:a', 'ping:b'],
        '同一域的排队调用必须按入队顺序执行（先 clear 后 schedule 不能被倒序）');
});

test('QIN-591：gwLoad 闸门关闭时排队，放行后返回注册面（而不是 module namespace）', async () => {
    const {sandbox, gateway} = loadGatewaySandbox();
    sandbox.GW_DOMAIN_LOADERS.beta = () => {
        sandbox.GW_DEFERRED.beta = {render() { return 'beta-rendered'; }};
        return Promise.resolve();
    };
    const pending = gateway.gwLoad('beta');
    assert.equal(sandbox.GW_DEFERRED.beta, undefined);
    gateway.openDeferredDomainGate();
    const face = await pending;
    assert.equal(typeof face.render, 'function');
    assert.equal(face.render(), 'beta-rendered');
});

test('QIN-591：未知域与未注册函数必须 reject，不能静默返回 undefined', async () => {
    const {sandbox, gateway} = loadGatewaySandbox();
    sandbox.GW_DOMAIN_LOADERS.gamma = () => {
        sandbox.GW_DEFERRED.gamma = {known() { return 1; }};
        return Promise.resolve();
    };
    gateway.openDeferredDomainGate();
    await assert.rejects(() => gateway.gwCall('delta', 'anything'), /未知域|未注册/);
    await assert.rejects(() => gateway.gwCall('gamma', 'missing'), /未注册/);
    assert.equal(await gateway.gwCall('gamma', 'known'), 1);
});
