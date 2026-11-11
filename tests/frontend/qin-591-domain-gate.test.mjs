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

test('QIN-591：gwCall 结果不得当同步值使用（全选把 Promise 当数组的回归）', () => {
    const offenders = [];
    mainSource.split('\n').forEach((line, index) => {
        let from = 0;
        for (;;) {
            const at = line.indexOf('gwCall(', from);
            if (at < 0) break;
            from = at + 7;
            // 只有「gwCall(...) 自己的右括号」后面紧跟数组/成员访问才算误用；
            // 形如 `if(fn().length) gwCall(...)` 的写法不算（QIN-591 返工实际踩过的误报）。
            const close = matchParen(line, at + 'gwCall('.length - 1);
            if (close < 0) continue;
            const awaited = /await\s+$/.test(line.slice(0, at));
            if (awaited) continue;
            const rest = line.slice(close + 1);
            if (/^\s*\.\s*(forEach|map|filter|find|findIndex|some|every|reduce|join|slice|length)\b/.test(rest)) {
                offenders.push(`asset-manager.js:${index + 1} ${line.trim().slice(0, 120)}`);
            }
        }
    });
    assert.deepEqual(offenders, [], `gwCall 返回 Promise，不能直接取数组/成员：\n${offenders.join('\n')}`);
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

function loadGatewaySandbox() {
    const anchor = 'let deferredDomainsOpen = false;';
    const start = mainSource.indexOf(anchor);
    assert.ok(start >= 0, '找不到首卡闸门声明');
    const loadFnStart = mainSource.indexOf('\nfunction gwLoad(domain) {', start);
    assert.ok(loadFnStart > start, '找不到 gwLoad 实现');
    const open = mainSource.indexOf('{', loadFnStart);
    const close = matchBrace(mainSource, open);
    const gatewaySource = mainSource.slice(start, close + 1);

    const sandbox = {
        GW_DEFERRED: {},
        GW_DOMAIN_LOADERS: {},
        console: {error() {}},
    };
    vm.createContext(sandbox);
    vm.runInContext(
        `${gatewaySource}\n;globalThis.__gateway = {gwCall, gwLoad, openDeferredDomainGate, deferredDomainsGateOpen};`,
        sandbox,
        {filename: 'qin-591-gateway.js'},
    );
    return {sandbox, gateway: sandbox.__gateway};
}

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
