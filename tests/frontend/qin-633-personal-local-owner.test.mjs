import assert from 'node:assert/strict';
import test from 'node:test';
import {readFileSync} from 'node:fs';
import {runInNewContext} from 'node:vm';

import {createAssetAuthApi} from '../../web/js/modules/asset-auth/api.js';

const read = path => readFileSync(new URL(`../../${path}`, import.meta.url), 'utf8');
const bootstrapSource = read('web/js/modules/trusted-local/bootstrap.js');
const hardware = read('web/js/core/hardware-telemetry.js');

const TICKET = 'eyJ2IjoxLCJqdGkiOiJ0ZXN0In0.c2lnbmF0dXJl';

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

function makeElement(id = '') {
    const classSet = new Set();
    const attrs = new Map();
    return {
        id,
        innerHTML: '',
        textContent: '',
        style: {},
        classList: {
            add(v) { classSet.add(v); },
            remove(v) { classSet.delete(v); },
            contains(v) { return classSet.has(v); },
        },
        setAttribute(k, v) { attrs.set(k, String(v)); },
        getAttribute(k) { return attrs.get(k) || null; },
        removeAttribute(k) { attrs.delete(k); },
        querySelector() { return null; },
        querySelectorAll() { return []; },
        focus() {},
        remove() {},
    };
}

function deckHarness(options = {}) {
    const elements = new Map();
    const dialog = makeElement('accountModalDialog');
    const modal = makeElement('accountModal');
    elements.set('accountModalDialog', dialog);
    elements.set('accountModal', modal);

    const document = {
        readyState: 'loading',
        addEventListener() {},
        getElementById(id) { return elements.get(id) || null; },
        querySelector() { return null; },
        querySelectorAll() { return []; },
        createElement(tag) { return makeElement(); },
        createRange() { return {selectNodeContents() {}}; },
        body: {
            style: {},
            appendChild() {},
        },
    };

    let openedModals = [];
    let closedModals = [];

    class CustomEvent { constructor(t, i) { this.type = t; this.detail = i?.detail; } }

    const defaultFetch = (url) => {
        if (options.statusResponse) {
            return Promise.resolve({
                status: options.statusResponse.status ?? 200,
                ok: options.statusResponse.ok ?? true,
                json: () => Promise.resolve(options.statusResponse.json ?? options.statusResponse),
            });
        }
        return Promise.resolve({
            status: 200,
            ok: true,
            json: () => Promise.resolve({
                auth_mode: 'local_account',
                authenticated: true,
                principal: {username: 'local-owner', role: 'admin'},
                setup_required: false,
                trusted_local: {
                    enabled: true,
                    established: true,
                    needs_manual_claim: false,
                    mode: 'trusted_local',
                },
            }),
        });
    };

    const fetchImpl = options.fetch || defaultFetch;

    const window = {
        addEventListener() {},
        dispatchEvent() {},
        CustomEvent,
        setTimeout(fn) { if (typeof fn === 'function') fn(); return 1; },
        clearTimeout() {},
        setInterval() { return 1; },
        clearInterval() {},
        performance: { now: () => Date.now() },
        location: { reload() {} },
        requestAnimationFrame: (fn) => { if (typeof fn === 'function') fn(); return 1; },
        cancelAnimationFrame: () => {},
        fetch: fetchImpl,
        AbortSignal: globalThis.AbortSignal || { timeout: () => ({}) },
    };

    const context = {
        window,
        document,
        navigator: {},
        console,
        CustomEvent,
        performance: { now: () => Date.now() },
        setTimeout(fn) { if (typeof fn === 'function') fn(); return 1; },
        clearTimeout() {},
        setInterval() { return 1; },
        clearInterval() {},
        requestAnimationFrame: (fn) => { if (typeof fn === 'function') fn(); return 1; },
        cancelAnimationFrame: () => {},
        fetch: fetchImpl,
        AbortSignal: globalThis.AbortSignal || { timeout: () => ({}) },
    };

    runInNewContext(hardware, context);
    const deck = window.HardwareDeck;

    // 观测弹窗调用
    const origOpen = deck.openModal.bind(deck);
    deck.openModal = function(id) {
        openedModals.push(id);
        const el = document.getElementById(id);
        if (el) el.classList.add('open');
        return origOpen(id);
    };

    const origClose = deck.closeModal.bind(deck);
    deck.closeModal = function(id) {
        closedModals.push(id);
        const el = document.getElementById(id);
        if (el) el.classList.remove('open');
        return origClose(id);
    };

    return {context, window, document, elements, modal, dialog, deck, openedModals, closedModals};
}

// ---------------------------------------------------------------------------
// AC1 & AC2: 直入时不误弹「首次设置·创建管理员」弹窗
// ---------------------------------------------------------------------------

test('AC1/AC2: 已认证且本机所有者已建立时，syncAuth 绝不触发 openAccountModal 弹窗', async () => {
    const {deck, openedModals} = deckHarness({
        statusResponse: {
            auth_mode: 'local_account',
            authenticated: true,
            principal: {username: 'local-owner', role: 'admin'},
            setup_required: false,
            trusted_local: {
                enabled: true,
                established: true,
                needs_manual_claim: false,
                mode: 'trusted_local',
            },
        },
    });

    await deck.syncAuth();

    assert.equal(openedModals.includes('accountModal'), false, '已认证直入时不得弹出 accountModal');
    assert.equal(deck.authState.authenticated, true);
    assert.equal(deck.authState.principal.username, 'local-owner');
});

test('AC1/AC2: 处于 bootstrapping 兑换中时，即使 setup_required 为 true 也绝不提前弹窗', async () => {
    const {deck, window, openedModals} = deckHarness({
        statusResponse: {
            auth_mode: 'local_account',
            authenticated: false,
            principal: null,
            setup_required: true,
            trusted_local: {
                enabled: true,
                established: false,
                mode: 'uninitialized',
            },
        },
    });
    window.GWTrustedLocal = {
        state: 'bootstrapping',
    };

    await deck.syncAuth();

    assert.equal(openedModals.includes('accountModal'), false, '票据兑换中不得抢先弹创建管理员弹窗');
});

test('AC1/AC2: 若 accountModal 因先前状态打开，直入成功后会自动关闭', async () => {
    const {deck, modal, closedModals} = deckHarness({
        statusResponse: {
            auth_mode: 'local_account',
            authenticated: true,
            principal: {username: 'qinxuedong', role: 'admin'},
            setup_required: false,
            trusted_local: {
                enabled: true,
                established: true,
                mode: 'trusted_local',
            },
        },
    });
    modal.classList.add('open');

    await deck.syncAuth();

    assert.equal(closedModals.includes('accountModal'), true, '直入成功后应安全关闭先前残留的 accountModal');
    assert.equal(modal.classList.contains('open'), false);
});

// ---------------------------------------------------------------------------
// AC4: 启动可信失败显式可恢复（不白屏，提供密码登录入口）
// ---------------------------------------------------------------------------

test('AC4: 票据兑换失败时 renderTrustedLocalNotice 给出明确失败说明与密码登录提示', () => {
    const {deck} = deckHarness();
    deck.authState.auth_mode = 'local_account';
    deck.authState.trusted_local = {
        enabled: true,
        established: false,
        needs_manual_claim: false,
        mode: 'uninitialized',
        bootstrap_state: 'failed',
    };

    const html = deck.renderTrustedLocalNotice();
    assert.match(html, /data-gw-trusted-local="failed"/);
    assert.match(html, /本机自动进入失败/);
    assert.match(html, /请用下方账号密码登录/);
});

test('AC4: 票据超时回退时提示密码登录', () => {
    const {deck} = deckHarness();
    deck.authState.auth_mode = 'local_account';
    deck.authState.trusted_local = {
        enabled: true,
        established: false,
        bootstrap_state: 'fallback_password',
    };

    const html = deck.renderTrustedLocalNotice();
    assert.match(html, /data-gw-trusted-local="fallback_password"/);
    assert.match(html, /本机自动进入超时/);
    assert.match(html, /请用下方账号密码登录/);
});

// ---------------------------------------------------------------------------
// 引导模块兑换成功广播
// ---------------------------------------------------------------------------

test('bootstrap.js: 收到 200 响应时正确迁移到 entered 并派发广播', async () => {
    const h = bootstrapHarness();
    h.context.location.hash = `#gw_trusted=${TICKET}`;
    h.context.responder = () => Promise.resolve({
        status: 200,
        ok: true,
        json: () => Promise.resolve({user_id: 'u-1', username: 'local-owner'}),
    });

    const instance = h.context.GWTrustedLocal.create({
        window: h.context,
        slowThresholdMs: 20,
        timeoutMs: 120,
    });

    const result = await instance.start();
    assert.equal(result.state, 'entered');
    assert.equal(instance.state, 'entered');
    assert.equal(instance.ticket, ''); // 终态抹除内存中的票据
    assert.equal(h.replaced.includes('/static/pages/index.html'), true);

    const enteredEvents = h.events.filter(e => e.type === 'gw-trusted-local' && e.detail?.state === 'entered');
    assert.equal(enteredEvents.length, 1);
});
