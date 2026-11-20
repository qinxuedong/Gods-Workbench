import assert from 'node:assert/strict';
import test from 'node:test';
import {readFileSync} from 'node:fs';
import {runInNewContext} from 'node:vm';

import {createAssetAuthApi} from '../../web/js/modules/asset-auth/api.js';

const read = path => readFileSync(new URL(`../../${path}`, import.meta.url), 'utf8');
const bootstrapSource = read('web/js/modules/trusted-local/bootstrap.js');
const hardware = read('web/js/core/hardware-telemetry.js');
const indexHtml = read('web/pages/index.html');

const TICKET = 'eyJ2IjoxfQ.c2lnbmF0dXJl';

function bootstrapHarness() {
    const replaced = [];
    const requests = [];
    const events = [];
    const context = {
        console,
        setTimeout,
        clearTimeout,
        location: {
            hash: '',
            hostname: '127.0.0.1',
            pathname: '/static/pages/index.html',
            search: '',
        },
        history: {
            replaceState(state, title, url) { replaced.push(url); },
        },
        dispatchEvent(event) { events.push(event); },
        CustomEvent: class CustomEvent {
            constructor(type, init = {}) {
                this.type = type;
                this.detail = init.detail;
            }
        },
        fetch(url, init) {
            requests.push({url, init});
            const responder = context.responder;
            if (typeof responder === 'function') return responder(url, init);
            return Promise.resolve({status: 200, ok: true, json: () => Promise.resolve({})});
        },
    };
    runInNewContext(bootstrapSource, context);
    return {context, replaced, requests, events, instance: context.GWTrustedLocal};
}

function startExchange(context, overrides = {}) {
    const instance = context.GWTrustedLocal.create({
        window: context,
        slowThresholdMs: 20,
        timeoutMs: 120,
        retryDelayMs: 40,
        ...overrides,
    });
    return instance;
}

function jsonResponse(status, payload) {
    return {
        status,
        ok: status >= 200 && status < 300,
        json: () => Promise.resolve(payload),
        clone() { return this; },
    };
}

// ---------------------------------------------------------------------------
// 片段读取与抹除
// ---------------------------------------------------------------------------

test('读取片段后立刻抹除，票据只留在内存里', async () => {
    const h = bootstrapHarness();
    h.context.location.hash = `#gw_trusted=${TICKET}`;
    const instance = startExchange(h.context);
    await instance.start();
    assert.deepEqual(h.replaced, ['/static/pages/index.html']);
    assert.equal(h.instance.ticket, '');
});

test('抹除片段时保留其它 hash 路由参数', async () => {
    const h = bootstrapHarness();
    h.context.location.hash = `#gw_trusted=${TICKET}&section=general`;
    const instance = startExchange(h.context);
    await instance.start();
    assert.deepEqual(h.replaced, ['/static/pages/index.html#section=general']);
});

test('没有票据时不抹除历史、不发起请求', async () => {
    const h = bootstrapHarness();
    h.context.location.hash = '#section=general';
    const instance = startExchange(h.context);
    const detail = await instance.start();
    assert.equal(detail.state, 'idle');
    assert.deepEqual(h.replaced, []);
    assert.deepEqual(h.requests, []);
});

test('引导脚本不写任何浏览器持久存储', () => {
    assert.doesNotMatch(bootstrapSource, /localStorage\s*[.[]/);
    assert.doesNotMatch(bootstrapSource, /sessionStorage\s*[.[]/);
    assert.doesNotMatch(bootstrapSource, /document\s*\.\s*cookie/);
});

// ---------------------------------------------------------------------------
// 兑换请求与状态机
// ---------------------------------------------------------------------------

test('兑换走同源 POST，只提交票据本身', async () => {
    const h = bootstrapHarness();
    h.context.location.hash = `#gw_trusted=${TICKET}`;
    const instance = startExchange(h.context);
    await instance.start();
    assert.equal(h.requests.length, 1);
    const request = h.requests[0];
    assert.equal(request.url, '/api/asset-auth/local/trusted-bootstrap');
    assert.equal(request.init.method, 'POST');
    assert.equal(request.init.credentials, 'same-origin');
    assert.match(request.init.headers['Content-Type'], /application\/json/i);
    assert.deepEqual(JSON.parse(request.init.body), {ticket: TICKET});
});

test('兑换成功后状态为 entered，票据从内存清空', async () => {
    const h = bootstrapHarness();
    h.context.responder = () => Promise.resolve(jsonResponse(200, {ok: true}));
    h.context.location.hash = `#gw_trusted=${TICKET}`;
    const instance = startExchange(h.context);
    const detail = await instance.start();
    assert.equal(detail.state, 'entered');
    assert.equal(instance.state, 'entered');
    assert.equal(instance.ticket, '');
});

test('已有账户需要人工认领时状态为 needs_claim', async () => {
    const h = bootstrapHarness();
    h.context.responder = () => Promise.resolve(jsonResponse(409, {
        detail: {code: 'TRUSTED_LOCAL_CLAIM_REQUIRED', message: '需人工认领'},
    }));
    h.context.location.hash = `#gw_trusted=${TICKET}`;
    const instance = startExchange(h.context);
    const detail = await instance.start();
    assert.equal(detail.state, 'needs_claim');
    assert.equal(detail.code, 'TRUSTED_LOCAL_CLAIM_REQUIRED');
    // 终态后内存中不再保留票据
    assert.equal(instance.ticket, '');
});

test('票据无效时状态为 failed，且错误信息不回显票据', async () => {
    const h = bootstrapHarness();
    h.context.responder = () => Promise.resolve(jsonResponse(401, {
        detail: {code: 'TRUSTED_LOCAL_BOOTSTRAP_INVALID', message: '启动票据无效，已拒绝本次请求'},
    }));
    h.context.location.hash = `#gw_trusted=${TICKET}`;
    const instance = startExchange(h.context);
    const detail = await instance.start();
    assert.equal(detail.state, 'failed');
    assert.equal(detail.code, 'TRUSTED_LOCAL_BOOTSTRAP_INVALID');
    assert.equal(JSON.stringify(detail).includes(TICKET), false);
    assert.equal(instance.error.includes(TICKET), false);
});

test('超过超时阈值即回落密码登录', async () => {
    const h = bootstrapHarness();
    h.context.responder = () => new Promise(() => {});
    h.context.location.hash = `#gw_trusted=${TICKET}`;
    const instance = startExchange(h.context, {timeoutMs: 60});
    const detail = await instance.start();
    assert.equal(detail.state, 'fallback_password');
    assert.equal(detail.reason, 'timeout');
});

test('传输层失败最多自动重试一次', async () => {
    const h = bootstrapHarness();
    let calls = 0;
    h.context.responder = () => {
        calls += 1;
        return Promise.reject(new TypeError('network down'));
    };
    h.context.location.hash = `#gw_trusted=${TICKET}`;
    const instance = startExchange(h.context, {retryDelayMs: 30, timeoutMs: 400});
    const detail = await instance.start();
    assert.equal(calls, 2);
    assert.equal(detail.state, 'failed');
});

test('明确的 HTTP 错误不重试', async () => {
    const h = bootstrapHarness();
    let calls = 0;
    h.context.responder = () => {
        calls += 1;
        return Promise.resolve(jsonResponse(403, {
            detail: {code: 'TRUSTED_LOCAL_DISABLED', message: '受信本机模式未启用'},
        }));
    };
    h.context.location.hash = `#gw_trusted=${TICKET}`;
    const instance = startExchange(h.context, {retryDelayMs: 30, timeoutMs: 400});
    const detail = await instance.start();
    assert.equal(calls, 1);
    assert.equal(detail.state, 'failed');
    assert.equal(detail.code, 'TRUSTED_LOCAL_DISABLED');
});

// ---------------------------------------------------------------------------
// 隔离：非本机来源一律丢弃票据
// ---------------------------------------------------------------------------

test('非回环来源丢弃票据且不向远程端转发', async () => {
    const h = bootstrapHarness();
    h.context.location.hostname = '192.168.1.20';
    h.context.location.hash = `#gw_trusted=${TICKET}`;
    const instance = startExchange(h.context);
    const detail = await instance.start();
    assert.equal(detail.state, 'remote_rejected');
    assert.deepEqual(h.requests, []);
    assert.deepEqual(h.replaced, ['/static/pages/index.html']);
});

test('伪造 localhost 主机名与非规范 IP 也丢弃票据且不发请求', async () => {
    for (const hostname of ['localhost.evil.invalid', '127.0.0.1.evil', '127.1', '2130706433']) {
        const h = bootstrapHarness();
        h.context.location.hostname = hostname;
        h.context.location.hash = `#gw_trusted=${TICKET}`;
        const instance = startExchange(h.context);
        const detail = await instance.start();
        assert.equal(detail.state, 'remote_rejected', hostname);
        assert.equal(instance.ticket, '', hostname);
        assert.deepEqual(h.requests, [], hostname);
        assert.deepEqual(h.replaced, ['/static/pages/index.html'], hostname);
    }
});

test('回环判定覆盖 localhost、127.0.0.0/8 与 ::1', () => {
    const h = bootstrapHarness();
    const isLoopback = h.instance.isLoopbackHost;
    assert.equal(isLoopback('localhost'), true);
    assert.equal(isLoopback('127.0.0.1'), true);
    assert.equal(isLoopback('127.5.6.7'), true);
    assert.equal(isLoopback('::1'), true);
    assert.equal(isLoopback('[::1]'), true);
    assert.equal(isLoopback('192.168.1.20'), false);
    assert.equal(isLoopback('example.com'), false);
    assert.equal(isLoopback(''), false);
});

test('票据提取只认本票独立键名', () => {
    const h = bootstrapHarness();
    assert.equal(h.instance.extractTrustedTicket('#gw_trusted=abc'), 'abc');
    assert.equal(h.instance.extractTrustedTicket('#section=general'), '');
    assert.equal(h.instance.extractTrustedTicket('#task-123'), '');
    assert.equal(h.instance.extractTrustedTicket(''), '');
});

// ---------------------------------------------------------------------------
// API 外观层契约
// ---------------------------------------------------------------------------

test('asset-auth 外观层的兑换方法与引导脚本指向同一端点', async () => {
    const requests = [];
    const api = createAssetAuthApi({
        request(url, init) {
            requests.push({url, init});
            return Promise.resolve({status: 200});
        },
    });
    await api.bootstrapTrustedLocal(TICKET);
    const request = requests.at(-1);
    assert.equal(request.url, '/api/asset-auth/local/trusted-bootstrap');
    assert.equal(request.init.method, 'POST');
    assert.equal(request.init.credentials, 'same-origin');
    assert.match(request.init.headers['Content-Type'], /application\/json/i);
    assert.deepEqual(JSON.parse(request.init.body), {ticket: TICKET});
});

// ---------------------------------------------------------------------------
// 认证中心弹窗的四种可见状态
// ---------------------------------------------------------------------------

function element() {
    const values = new Set(['hidden']);
    return {
        textContent: '',
        value: '',
        disabled: false,
        focused: false,
        style: {},
        removed: false,
        classList: {
            add(value) { values.add(value); },
            remove(value) { values.delete(value); },
            contains(value) { values.has(value); },
        },
        setAttribute() {},
        focus() { this.focused = true; },
        select() { this.selected = true; },
        remove() { this.removed = true; },
    };
}

function deckHarness() {
    const elements = new Map();
    const dialog = element();
    elements.set('accountModalDialog', dialog);
    const document = {
        readyState: 'loading',
        addEventListener() {},
        getElementById(id) { return elements.get(id) || null; },
        createElement() { return element(); },
        createRange() { return {selectNodeContents() {}}; },
        body: {appendChild() {}},
        execCommand() { return false; },
    };
    const window = {
        addEventListener() {},
        confirm() { return false; },
        getSelection() { return {removeAllRanges() {}, addRange() {}}; },
        setTimeout() { return 1; },
    };
    const context = {window, document, navigator: {}, console, setTimeout() { return 1; }};
    runInNewContext(hardware, context);
    return {context, window, document, elements, dialog, deck: window.HardwareDeck};
}

function trustedState(deck, patch) {
    deck.authState.degradation = 'ok';
    deck.authState.auth_mode = 'local_account';
    deck.authState.trusted_local = {
        enabled: true,
        established: false,
        needs_manual_claim: false,
        mode: 'trusted_local',
        bootstrap_state: 'idle',
        message: '',
        ...patch,
    };
}

test('弹窗显示「本机所有者已建立」', () => {
    const h = deckHarness();
    h.deck.authState.authenticated = true;
    h.deck.authState.principal = {username: 'local-owner', display_name: 'local-owner', role: 'admin'};
    trustedState(h.deck, {established: true, mode: 'trusted_local', bootstrap_state: 'entered'});
    h.deck.renderAccountModalContent();
    assert.match(h.dialog.innerHTML, /本机所有者已建立/);
    assert.match(h.dialog.innerHTML, /data-gw-trusted-local="established"/);
});

test('弹窗显示「需人工认领」，且不自动接管既有账户', () => {
    const h = deckHarness();
    h.deck.authState.authenticated = false;
    h.deck.authState.setup_required = false;
    trustedState(h.deck, {needs_manual_claim: true, mode: 'needs_manual_claim', bootstrap_state: 'needs_claim'});
    h.deck.renderAccountModalContent();
    assert.match(h.dialog.innerHTML, /需人工认领/);
    assert.match(h.dialog.innerHTML, /trusted-claim/);
    assert.match(h.dialog.innerHTML, /data-gw-trusted-local="needs_manual_claim"/);
});

test('弹窗显示「本机自动进入失败」并保留密码登录入口', () => {
    const h = deckHarness();
    h.deck.authState.authenticated = false;
    h.deck.authState.setup_required = false;
    trustedState(h.deck, {bootstrap_state: 'failed'});
    h.deck.renderAccountModalContent();
    assert.match(h.dialog.innerHTML, /本机自动进入失败/);
    assert.match(h.dialog.innerHTML, /id="localAccountForm"/);
    assert.match(h.dialog.innerHTML, /data-gw-trusted-local="failed"/);
});

test('弹窗显示「本机自动进入超时」并保留密码登录入口', () => {
    const h = deckHarness();
    h.deck.authState.authenticated = false;
    h.deck.authState.setup_required = false;
    trustedState(h.deck, {bootstrap_state: 'fallback_password'});
    h.deck.renderAccountModalContent();
    assert.match(h.dialog.innerHTML, /本机自动进入超时/);
    assert.match(h.dialog.innerHTML, /id="localAccountForm"/);
    assert.match(h.dialog.innerHTML, /data-gw-trusted-local="fallback_password"/);
});

test('弹窗不回显票据、密钥或路径', () => {
    const h = deckHarness();
    h.deck.authState.authenticated = false;
    h.deck.authState.setup_required = false;
    trustedState(h.deck, {bootstrap_state: 'failed', message: '启动票据无效'});
    h.deck.renderAccountModalContent();
    assert.doesNotMatch(h.dialog.innerHTML, /trusted_local\.key/);
    assert.doesNotMatch(h.dialog.innerHTML, /eyJ2IjoxfQ/);
});

// ---------------------------------------------------------------------------
// 页面接线
// ---------------------------------------------------------------------------

test('index.html 在 head 早期引入引导脚本，且只引入一处', () => {
    const matches = indexHtml.match(/trusted-local\/bootstrap\.js/g) || [];
    assert.equal(matches.length, 1);
    const headEnd = indexHtml.indexOf('</head>');
    assert.ok(indexHtml.indexOf('trusted-local/bootstrap.js') < headEnd);
});
