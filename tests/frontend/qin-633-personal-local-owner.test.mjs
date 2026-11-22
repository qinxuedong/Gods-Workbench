import assert from 'node:assert/strict';
import test from 'node:test';
import {readFileSync} from 'node:fs';
import {runInNewContext} from 'node:vm';

import {createAssetAuthApi} from '../../web/js/modules/asset-auth/api.js';

const read = path => readFileSync(new URL(`../../${path}`, import.meta.url), 'utf8');
const bootstrapSource = read('web/js/modules/trusted-local/bootstrap.js');
const hardware = read('web/js/core/hardware-telemetry.js');
const floatingSource = read('web/js/core/floating-dismissal.js');

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

function makeElement(id = '', elementsMap = null) {
    const classSet = new Set();
    const attrs = new Map();
    let _innerHTML = '';
    const el = {
        id,
        get innerHTML() { return _innerHTML; },
        set innerHTML(html) {
            _innerHTML = String(html || '');
            if (elementsMap) {
                const matches = _innerHTML.matchAll(/<([a-z0-9-]+)\s+[^>]*id=["']([^"']+)["'][^>]*>/gi);
                for (const match of matches) {
                    const tagString = match[0];
                    const childId = match[2];
                    let child = elementsMap.get(childId);
                    if (!child) {
                        child = makeElement(childId, elementsMap);
                        elementsMap.set(childId, child);
                    }
                    if (/\bdisabled\b/i.test(tagString)) {
                        child.disabled = true;
                    }
                }
            }
        },
        textContent: '',
        style: {},
        classList: {
            add(v) { classSet.add(v); },
            remove(v) { classSet.delete(v); },
            toggle(v, force) {
                if (force === undefined) {
                    classSet.has(v) ? classSet.delete(v) : classSet.add(v);
                } else if (force) {
                    classSet.add(v);
                } else {
                    classSet.delete(v);
                }
            },
            contains(v) { return classSet.has(v); },
        },
        disabled: false,
        checked: false,
        setAttribute(k, v) { attrs.set(k, String(v)); },
        getAttribute(k) { return attrs.get(k) || null; },
        removeAttribute(k) { attrs.delete(k); },
        querySelector() { return null; },
        querySelectorAll() { return []; },
        focus() {},
        remove() {},
    };
    return el;
}

function deckHarness(options = {}) {
    const elements = new Map();
    let modal = null;
    let dialog = null;
    if (options.presetModal !== false) {
        dialog = makeElement('accountModalDialog', elements);
        modal = makeElement('accountModal', elements);
        elements.set('accountModalDialog', dialog);
        elements.set('accountModal', modal);
    }

    const document = {
        readyState: 'loading',
        addEventListener() {},
        getElementById(id) { return elements.get(id) || null; },
        querySelector(selector) {
            if (selector === '.hw-modal-backdrop.open') {
                for (const el of elements.values()) {
                    if (el.classList?.contains('hw-modal-backdrop') && el.classList?.contains('open')) {
                        return el;
                    }
                }
            }
            return null;
        },
        querySelectorAll() { return []; },
        createElement(tag) {
            const el = makeElement('', elements);
            return new Proxy(el, {
                set(target, prop, value) {
                    target[prop] = value;
                    if (prop === 'id' && value) {
                        elements.set(value, target);
                    }
                    return true;
                }
            });
        },
        createRange() { return {selectNodeContents() {}}; },
        body: {
            style: {},
            appendChild(child) {
                if (child?.id) {
                    elements.set(child.id, child);
                }
            },
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

test('bootstrap.js: 收到 200 响应且携带 recovery_codes 时在 result, instance 与事件中完整透传', async () => {
    const h = bootstrapHarness();
    h.context.location.hash = `#gw_trusted=${TICKET}`;
    const mockCodes = ['RC-1111-2222', 'RC-3333-4444', 'RC-5555-6666'];
    h.context.responder = () => Promise.resolve({
        status: 200,
        ok: true,
        json: () => Promise.resolve({
            user_id: 'u-1',
            username: 'local-owner',
            recovery_codes: mockCodes,
        }),
    });

    const instance = h.context.GWTrustedLocal.create({
        window: h.context,
        slowThresholdMs: 20,
        timeoutMs: 120,
    });

    const result = await instance.start();
    assert.equal(result.state, 'entered');
    assert.deepEqual(result.recovery_codes, mockCodes);
    assert.deepEqual(instance.recovery_codes, mockCodes);

    const enteredEvents = h.events.filter(e => e.type === 'gw-trusted-local' && e.detail?.state === 'entered');
    assert.equal(enteredEvents.length, 1);
    assert.deepEqual(enteredEvents[0].detail.recovery_codes, mockCodes);
});

// ---------------------------------------------------------------------------
// AC1 & AC4: 空库受信首启恢复码展示与用户交互确认
// ---------------------------------------------------------------------------

test('AC1: 可信首启直入且携带 recovery_codes 时，安全展示一次性恢复码弹窗供用户保存', async () => {
    const mockCodes = ['RC-AAA1-BBB2', 'RC-CCC3-DDD4', 'RC-EEE5-FFF6', 'RC-GGG7-HHH8'];
    const {deck, dialog, modal, openedModals} = deckHarness({
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
    deck.authState.trusted_local.recovery_codes = mockCodes;

    await deck.syncAuth();

    assert.equal(openedModals.includes('accountModal'), true);
    assert.equal(modal.classList.contains('open'), true);
    assert.match(dialog.innerHTML, /本机管理员已建立 · 请妥善保存恢复码/);
    assert.match(dialog.innerHTML, /RC-AAA1-BBB2/);
    assert.match(dialog.innerHTML, /RC-GGG7-HHH8/);
    assert.match(dialog.innerHTML, /复制全部/);
    assert.match(dialog.innerHTML, /我已妥善保存这些恢复码/);
    assert.match(dialog.innerHTML, /已保存，进入工作台/);
});

test('AC1: 恢复码弹窗未勾选保存确认前禁用继续按钮，勾选后解禁，点击进入工作台安全关闭弹窗', async () => {
    const mockCodes = ['RC-1111-2222', 'RC-3333-4444'];
    const {deck, elements, closedModals} = deckHarness({
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
    deck.authState.trusted_local.recovery_codes = mockCodes;
    await deck.syncAuth();

    const checkbox = elements.get('localRecoverySaved');
    const continueBtn = elements.get('localRecoveryContinue');
    assert.ok(checkbox, '应存在已保存确认复选框');
    assert.ok(continueBtn, '应存在继续按钮');

    // 初始状态禁用
    assert.equal(continueBtn.disabled, true);

    // 模拟用户勾选已保存
    checkbox.checked = true;
    deck.handleRecoveryCodeAcknowledgement();
    assert.equal(continueBtn.disabled, false);

    // 模拟点击进入工作台关闭弹窗
    deck.closeModal('accountModal');
    assert.equal(closedModals.includes('accountModal'), true);
});

test('AC2: 已有账户自动接管或二次启动（recovery_codes 为空）时，绝不弹出恢复码弹窗', async () => {
    const {deck, openedModals} = deckHarness({
        statusResponse: {
            auth_mode: 'local_account',
            authenticated: true,
            principal: {username: 'chief_admin', role: 'admin'},
            setup_required: false,
            trusted_local: {
                enabled: true,
                established: true,
                needs_manual_claim: false,
                mode: 'trusted_local',
                recovery_codes: [],
            },
        },
    });
    deck.authState.trusted_local.recovery_codes = [];

    await deck.syncAuth();
    assert.equal(openedModals.includes('accountModal'), false);
});

test('AC1/P1回归: 初始 DOM 无弹窗节点时，可信首启直入携带 recovery_codes 能自动创建 modal 并正确展示', async () => {
    // 初始 DOM 绝无预置 modal 或 dialog 节点
    const harness = deckHarness({
        presetModal: false,
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

    assert.equal(harness.document.getElementById('accountModal'), null, '初始不得存在 accountModal 节点');
    assert.equal(harness.document.getElementById('accountModalDialog'), null, '初始不得存在 accountModalDialog 节点');

    const sampleCodes = ['RC-1111-2222', 'RC-3333-4444', 'RC-5555-6666', 'RC-7777-8888'];
    harness.deck.authState.trusted_local = {
        enabled: true,
        established: true,
        recovery_codes: sampleCodes,
    };

    assert.equal(harness.deck.recoveryCodesShown, false);

    await harness.deck.syncAuth();

    // 验证：自动创建了 modal 和 dialog 节点
    const modal = harness.document.getElementById('accountModal');
    const dialog = harness.document.getElementById('accountModalDialog');
    assert.ok(modal, 'syncAuth 必须自动创建 accountModal 节点');
    assert.ok(dialog, 'syncAuth 必须自动创建 accountModalDialog 节点');
    assert.ok(harness.openedModals.includes('accountModal'), 'accountModal 必须被显式打开');
    assert.match(dialog.innerHTML, /本机管理员已建立 · 请妥善保存恢复码/);
    assert.match(dialog.innerHTML, /RC-1111-2222/);
    assert.equal(harness.deck.recoveryCodesShown, true, '成功展示后方可置 recoveryCodesShown 为 true');
});

test('AC1/P1回归: 若初次渲染因故未成功展示恢复码，不得预先标记 recoveryCodesShown，后续 syncAuth 必须补显', async () => {
    const harness = deckHarness({
        presetModal: false,
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

    const sampleCodes = ['RC-AAAA-BBBB'];
    harness.deck.authState.trusted_local = {
        enabled: true,
        established: true,
        recovery_codes: sampleCodes,
    };

    // 模拟初次渲染时 ensureAccountModal 失败（如 document.body 尚未就绪）
    const origEnsure = harness.deck.ensureAccountModal;
    harness.deck.ensureAccountModal = () => null;

    await harness.deck.syncAuth();
    assert.equal(harness.deck.recoveryCodesShown, false, '渲染失败时绝对不得标记为已展示');

    // 恢复正常环境后再次 syncAuth
    harness.deck.ensureAccountModal = origEnsure;
    await harness.deck.syncAuth();

    assert.equal(harness.deck.recoveryCodesShown, true, '恢复后二次 syncAuth 必须成功补显并标记已展示');
    const dialog = harness.document.getElementById('accountModalDialog');
    assert.ok(dialog);
    assert.match(dialog.innerHTML, /RC-AAAA-BBBB/);
});

function createFloatingDeckHarness() {
    function matchesSelector(node, selector) {
        if (!node || node.nodeType !== 1) return false;
        selector = selector.trim();
        if (selector.includes(',')) {
            return selector.split(',').some(s => matchesSelector(node, s.trim()));
        }
        let target = selector;
        const idMatch = target.match(/#([a-zA-Z0-9_-]+)/);
        if (idMatch && node.id !== idMatch[1]) return false;
        const tagMatch = target.match(/^[a-zA-Z0-9-]+/);
        if (tagMatch && node.tagName.toLowerCase() !== tagMatch[0].toLowerCase()) return false;
        const notMatches = target.matchAll(/:not\(([^)]+)\)/g);
        for (const m of notMatches) {
            if (matchesSelector(node, m[1])) return false;
        }
        const cleanTarget = target.replace(/:not\([^)]+\)/g, '');
        const classMatches = cleanTarget.matchAll(/\.([a-zA-Z0-9_-]+)/g);
        for (const m of classMatches) {
            if (!node.classList.contains(m[1])) return false;
        }
        const attrMatches = cleanTarget.matchAll(/\[([a-zA-Z0-9_-]+)(?:([*~|^$]?=)(["']?)(.*?)\3)?\]/g);
        for (const m of attrMatches) {
            const attrName = m[1];
            const op = m[2];
            const expected = m[4];
            if (!node.hasAttribute(attrName)) return false;
            if (op) {
                const actual = node.getAttribute(attrName) || '';
                if (op === '=' && actual !== expected) return false;
                if (op === '*=' && !actual.includes(expected)) return false;
            }
        }
        return true;
    }

    class HarnessElement {
        constructor(tagName = 'div', id = '', ownerDocument = null) {
            this.tagName = tagName.toUpperCase();
            this.nodeType = 1;
            this._id = id;
            this.ownerDocument = ownerDocument;
            this.attributes = new Map();
            if (id) {
                this.attributes.set('id', id);
                if (ownerDocument) ownerDocument.elementsMap.set(id, this);
            }
            this.classList = {
                _set: new Set(),
                add(...names) { names.forEach(n => this._set.add(n)); },
                remove(...names) { names.forEach(n => this._set.delete(n)); },
                contains(name) { return this._set.has(name); },
                toggle(name, force) {
                    if (force === undefined) {
                        this._set.has(name) ? this._set.delete(name) : this._set.add(name);
                    } else if (force) {
                        this._set.add(name);
                    } else {
                        this._set.delete(name);
                    }
                }
            };
            this.dataset = new Proxy({}, {
                get: (_t, k) => this.attributes.get('data-' + String(k).replace(/[A-Z]/g, l => '-' + l.toLowerCase())) ?? undefined,
                set: (_t, k, v) => { this.setAttribute('data-' + String(k).replace(/[A-Z]/g, l => '-' + l.toLowerCase()), String(v)); return true; },
                has: (_t, k) => this.hasAttribute('data-' + String(k).replace(/[A-Z]/g, l => '-' + l.toLowerCase())),
            });
            this.children = [];
            this.parentElement = null;
            this.parentNode = null;
            this.style = {};
            this.disabled = false;
            this.checked = false;
            this.type = tagName.toLowerCase() === 'input' ? 'text' : '';
            this.value = '';
            this._listeners = new Map();
            this._innerHTML = '';
            this.textContent = '';
        }
        get id() { return this._id; }
        set id(val) {
            this._id = String(val || '');
            this.attributes.set('id', this._id);
            if (this.ownerDocument && this._id) {
                this.ownerDocument.elementsMap.set(this._id, this);
            }
        }
        get name() { return this.getAttribute('name') || ''; }
        set name(val) { this.setAttribute('name', val); }
        get type() { return this.getAttribute('type') || (this.tagName === 'INPUT' ? 'text' : ''); }
        set type(val) { this.setAttribute('type', val); }
        get className() { return [...this.classList._set].join(' '); }
        set className(val) {
            this.classList._set = new Set(String(val || '').split(/\s+/).filter(Boolean));
        }
        get isConnected() { return true; }
        setAttribute(k, v) { this.attributes.set(String(k).toLowerCase(), String(v)); }
        getAttribute(k) { return this.attributes.get(String(k).toLowerCase()) ?? null; }
        hasAttribute(k) { return this.attributes.has(String(k).toLowerCase()); }
        removeAttribute(k) { this.attributes.delete(String(k).toLowerCase()); }
        matches(sel) { return matchesSelector(this, sel); }
        contains(other) {
            let cur = other;
            while (cur) {
                if (cur === this) return true;
                cur = cur.parentElement;
            }
            return false;
        }
        closest(sel) {
            let cur = this;
            while (cur && cur.nodeType === 1) {
                if (matchesSelector(cur, sel)) return cur;
                cur = cur.parentElement;
            }
            return null;
        }
        querySelectorAll(sel) {
            const res = [];
            const walk = node => {
                for (const child of node.children) {
                    if (matchesSelector(child, sel)) res.push(child);
                    walk(child);
                }
            };
            walk(this);
            return res;
        }
        querySelector(sel) {
            const list = this.querySelectorAll(sel);
            return list[0] ?? null;
        }
        appendChild(child) {
            child.parentElement = this;
            child.parentNode = this;
            this.children.push(child);
            if (this.ownerDocument && child.id) {
                this.ownerDocument.elementsMap.set(child.id, child);
            }
            return child;
        }
        addEventListener(type, fn) {
            if (!this._listeners.has(type)) this._listeners.set(type, []);
            this._listeners.get(type).push(fn);
        }
        removeEventListener(type, fn) {
            const list = this._listeners.get(type) || [];
            this._listeners.set(type, list.filter(l => l !== fn));
        }
        dispatchEvent(e) {
            const list = this._listeners.get(e.type) || [];
            list.forEach(fn => fn.call(this, e));
            return true;
        }
        focus() {}
        blur() {}
        click() {
            const path = [];
            let cur = this;
            while (cur) {
                path.push(cur);
                cur = cur.parentElement;
            }
            if (this.ownerDocument) {
                path.push(this.ownerDocument);
                if (this.ownerDocument.defaultView) path.push(this.ownerDocument.defaultView);
            }
            const evt = {
                type: 'click',
                target: this,
                composedPath: () => path,
                defaultPrevented: false,
                preventDefault() { this.defaultPrevented = true; },
                stopImmediatePropagation() { this._stopped = true; }
            };
            if (this.ownerDocument) {
                this.ownerDocument.dispatchEvent(evt);
            }
            if (!evt._stopped) {
                this.dispatchEvent(evt);
            }
        }
        get innerHTML() { return this._innerHTML; }
        set innerHTML(html) {
            this._innerHTML = String(html || '');
            this.children = [];
            const tagRegex = /<([a-z0-9-]+)([^>]*)>/gi;
            let match;
            while ((match = tagRegex.exec(this._innerHTML)) !== null) {
                const tagName = match[1];
                const attrString = match[2];
                if (tagName.startsWith('/')) continue;
                const el = new HarnessElement(tagName, '', this.ownerDocument);
                const attrRegex = /([a-z0-9_-]+)(?:=["']([^"']*)["'])?/gi;
                let attrMatch;
                while ((attrMatch = attrRegex.exec(attrString)) !== null) {
                    const name = attrMatch[1];
                    const val = attrMatch[2] !== undefined ? attrMatch[2] : '';
                    el.setAttribute(name, val);
                    if (name === 'id') el.id = val;
                    if (name === 'class') el.className = val;
                    if (name === 'disabled') el.disabled = true;
                    if (name === 'type') el.type = val;
                }
                this.appendChild(el);
            }
        }
    }

    class HarnessDocument {
        constructor() {
            this.readyState = 'complete';
            this.elementsMap = new Map();
            this._listeners = new Map();
            this.body = new HarnessElement('body', 'body', this);
            this.documentElement = new HarnessElement('html', 'html', this);
            this.documentElement.appendChild(this.body);
        }
        getElementById(id) {
            return this.elementsMap.get(id) || null;
        }
        querySelector(sel) {
            return this.documentElement.querySelector(sel);
        }
        querySelectorAll(sel) {
            return this.documentElement.querySelectorAll(sel);
        }
        createElement(tag) {
            return new HarnessElement(tag, '', this);
        }
        createRange() { return { selectNodeContents() {} }; }
        addEventListener(type, fn, opts) {
            if (!this._listeners.has(type)) this._listeners.set(type, []);
            this._listeners.get(type).push(fn);
        }
        removeEventListener(type, fn) {
            const list = this._listeners.get(type) || [];
            this._listeners.set(type, list.filter(l => l !== fn));
        }
        dispatchEvent(e) {
            const list = this._listeners.get(e.type) || [];
            list.forEach(fn => fn.call(this, e));
            return true;
        }
    }

    const doc = new HarnessDocument();
    const confirmCalls = [];
    const win = {
        Element: HarnessElement,
        document: doc,
        dispatchEvent(e) { return true; },
        addEventListener(t, f) { doc.addEventListener(t, f); },
        removeEventListener(t, f) { doc.removeEventListener(t, f); },
        confirm(msg) {
            confirmCalls.push(msg);
            return true;
        },
        requestAnimationFrame(fn) { return 1; },
        cancelAnimationFrame() {},
        setTimeout(fn) { if (typeof fn === 'function') fn(); return 1; },
        clearTimeout() {},
        setInterval() { return 1; },
        clearInterval() {},
        CustomEvent: class { constructor(t, i) { this.type = t; this.detail = i?.detail; } },
        location: { reload() {} },
        getComputedStyle(el) { return { display: 'block', visibility: 'visible' }; },
    };
    doc.defaultView = win;

    const context = {
        window: win,
        global: win,
        globalThis: win,
        document: doc,
        Element: HarnessElement,
        console,
        confirm: (...args) => win.confirm(...args),
        CustomEvent: win.CustomEvent,
        setTimeout: win.setTimeout,
        clearTimeout: win.clearTimeout,
        setInterval: win.setInterval,
        clearInterval: win.clearInterval,
        requestAnimationFrame: win.requestAnimationFrame,
        cancelAnimationFrame: win.cancelAnimationFrame,
    };

    runInNewContext(floatingSource, context);
    runInNewContext(hardware, context);

    return { win, doc, deck: win.HardwareDeck, confirmCalls };
}

test('AC1/R4-F1回归: 真实 FloatingDismissal 浮层守卫下，保存确认复选框不被误判为未保存草稿，勾选后点击进入工作台无二次确认并成功关闭', async () => {
    const { win, doc, deck, confirmCalls } = createFloatingDeckHarness();

    const sampleCodes = [
        'RC-1111-2222', 'RC-3333-4444', 'RC-5555-6666', 'RC-7777-8888',
        'RC-9999-0000', 'RC-AAAA-BBBB', 'RC-CCCC-DDDD', 'RC-EEEE-FFFF'
    ];

    // 1. 渲染 8 个恢复码
    const rendered = deck.renderTrustedLocalRecoveryCodes(sampleCodes);
    assert.equal(rendered, true, '恢复码渲染必须成功');

    const modal = doc.getElementById('accountModal');
    const dialog = doc.getElementById('accountModalDialog');
    assert.ok(modal, '弹窗外层 accountModal 必须存在');
    assert.ok(dialog, '弹窗容器 accountModalDialog 必须存在');
    assert.equal(modal.classList.contains('open'), true, '弹窗必须处于打开状态');
    assert.equal(win.FloatingDismissal.hasOpen(), true, 'FloatingDismissal 必须记录弹窗已打开');

    // 2. 初始状态检查：恢复码完整展示，复选框未勾选，继续按钮禁用
    const checkbox = doc.getElementById('localRecoverySaved');
    const continueBtn = doc.getElementById('localRecoveryContinue');
    assert.ok(checkbox, '必须存在保存确认复选框');
    assert.ok(continueBtn, '必须存在继续进入工作台按钮');
    assert.equal(continueBtn.disabled, true, '未勾选保存前继续按钮必须处于禁用状态');

    // 3. 用户勾选保存确认
    checkbox.checked = true;
    deck.handleRecoveryCodeAcknowledgement();
    assert.equal(continueBtn.disabled, false, '勾选保存确认后继续按钮必须解禁');

    // 4. 核心：通过继续按钮真实点击事件（包含 composedPath 与捕获阶段拦截），触发 FloatingDismissal 守卫判定
    continueBtn.click();

    // 5. 核心验证：守卫绝不弹出“有未保存的修改，确认放弃并关闭？”，弹窗顺利关闭
    assert.equal(confirmCalls.length, 0, '确认保存进入工作台操作绝不得触发未保存修改确认框');
    assert.equal(modal.classList.contains('open'), false, '弹窗必须已成功关闭（移除 open 类）');
    assert.equal(win.FloatingDismissal.hasOpen(), false, 'FloatingDismissal 栈中应无打开的浮层');
});

test('AC1/R5-F1回归: 突变防御测试验证——若人为移除草稿排除过滤，按钮真实点击必会触发 confirm 拦截', async () => {
    const { win, doc, deck, confirmCalls } = createFloatingDeckHarness();

    // 突变注入：故意移除排除逻辑，模拟 R4 故障重现
    deck.snapshotDraft = function(content) {
        if (!content || !content.querySelectorAll) return '[]';
        const inputs = Array.from(content.querySelectorAll('input:not([type="hidden"]),textarea,select,[contenteditable="true"]'));
        return JSON.stringify(inputs.map(el => [el.id || el.name, el.type === 'checkbox' || el.type === 'radio' ? el.checked : el.isContentEditable ? el.textContent : el.value]));
    };

    const sampleCodes = [
        'RC-1111-2222', 'RC-3333-4444', 'RC-5555-6666', 'RC-7777-8888',
        'RC-9999-0000', 'RC-AAAA-BBBB', 'RC-CCCC-DDDD', 'RC-EEEE-FFFF'
    ];

    deck.renderTrustedLocalRecoveryCodes(sampleCodes);

    const checkbox = doc.getElementById('localRecoverySaved');
    const continueBtn = doc.getElementById('localRecoveryContinue');

    checkbox.checked = true;
    deck.handleRecoveryCodeAcknowledgement();

    // 触发真实按钮点击
    continueBtn.click();

    // 核心反证：当缺少排除逻辑时，保存确认勾选框必会被误判为未保存草稿并触发 confirm 拦截
    assert.equal(confirmCalls.length, 1, '移除排除过滤时真实按钮点击必须触发 window.confirm 拦截');
    assert.match(confirmCalls[0], /有未保存的修改，确认放弃并关闭？/);
});

test('AC1/R4-F1回归: 验证通用浮层守卫具有真实防御能力，表单未保存修改时确实会触发 confirm 拦截', async () => {
    const { win, doc, deck, confirmCalls } = createFloatingDeckHarness();

    const testModal = doc.createElement('div');
    testModal.id = 'editModal';
    testModal.className = 'hw-modal-backdrop';
    const testDialog = doc.createElement('div');
    testDialog.id = 'editModalDialog';
    testDialog.className = 'hw-modal-dialog';
    testModal.appendChild(testDialog);
    doc.body.appendChild(testModal);

    const textInput = doc.createElement('input');
    textInput.id = 'usernameInput';
    textInput.name = 'username';
    textInput.type = 'text';
    textInput.value = 'initial_name';
    testDialog.appendChild(textInput);

    // 打开普通表单弹窗
    deck.openModal('editModal');
    assert.equal(testModal.classList.contains('open'), true);

    // 用户在表单中进行了修改（产生了未保存草稿）
    textInput.value = 'changed_name';

    // 此时关闭表单，通用浮层守卫必须检测到草稿差异并触发 confirm
    const prevCalls = confirmCalls.length;
    win.FloatingDismissal.closeSurface(testModal);
    assert.equal(confirmCalls.length, prevCalls + 1, '普通表单存在未保存修改时必须触发 window.confirm');
    assert.match(confirmCalls[prevCalls], /有未保存的修改，确认放弃并关闭？/);
});
