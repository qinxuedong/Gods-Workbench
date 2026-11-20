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
 * 受信本机首启引导（QIN-630）。
 *
 * 本脚本在页面 `<head>` **早期**同步执行，只做三件事：
 *
 * 1. 读取 URL **片段**中的本机启动票据（`#gw_trusted=...`）；
 * 2. 读取后**立刻**用 `history.replaceState` 抹除该片段（历史记录、地址栏不留残留）；
 * 3. 把票据放进**内存**变量并发起一次性兑换，把结果广播给页面。
 *
 * 安全边界：
 *
 * - 票据只是**短时一次性能力值**，不是账号口令；设备长期密钥永远不进入浏览器；
 * - 票据只存在于内存，**不写** localStorage / sessionStorage / Cookie；
 * - 只在**回环来源**发起兑换；非本机来源一律丢弃票据，**禁止**向远程端转发；
 * - 兑换未成功前**不得**宣称「已进入本机」，只回落既有密码登录路径；
 * - 错误信息只保留服务端错误码与文案，**绝不**回显票据。
 *
 * 状态机：`booting` → `bootstrapping` → `entered` / `failed` / `needs_claim` /
 * `fallback_password`；非本机来源额外有 `remote_rejected`。
 */
(function (global) {
    'use strict';

    /** 票据在 URL 片段中的键名（与其它模块的 hash 路由参数互不冲突）。 */
    var FRAGMENT_KEY = 'gw_trusted';
    /** 一次性启动票据的兑换端点。 */
    var BOOTSTRAP_PATH = '/api/asset-auth/local/trusted-bootstrap';
    /** 慢档阈值：超过即提示「正在进入本机」。 */
    var SLOW_THRESHOLD_MS = 1500;
    /** 超时阈值：超过即回落密码登录并给出「用密码登录」入口。 */
    var TIMEOUT_MS = 5000;
    /** 自动重试的最小间隔。 */
    var RETRY_DELAY_MS = 3000;
    /** 自动重试次数上限。 */
    var MAX_AUTO_RETRY = 1;

    var STATE = {
        IDLE: 'idle',
        BOOTING: 'booting',
        BOOTSTRAPPING: 'bootstrapping',
        ENTERED: 'entered',
        FAILED: 'failed',
        NEEDS_CLAIM: 'needs_claim',
        FALLBACK_PASSWORD: 'fallback_password',
        REMOTE_REJECTED: 'remote_rejected'
    };

    /**
     * 判定主机名是否回环地址。
     * 只认 `localhost`、`127.0.0.0/8` 与 `::1`；其它一律视为非本机。
     */
    function isLoopbackHost(hostname) {
        var host = String(hostname || '').trim().toLowerCase();
        if (!host) return false;
        if (host === 'localhost') return true;
        if (host.charAt(0) === '[' && host.charAt(host.length - 1) === ']') {
            host = host.slice(1, -1);
        }
        if (host === '::1' || host === '0:0:0:0:0:0:0:1') return true;
        if (host.indexOf('::ffff:') === 0) host = host.slice(7);
        var matched = /^(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})$/.exec(host);
        if (!matched) return false;
        for (var index = 1; index <= 4; index += 1) {
            var octet = Number(matched[index]);
            if (!Number.isInteger(octet) || octet < 0 || octet > 255) return false;
        }
        return Number(matched[1]) === 127;
    }

    /** 从 URL 片段中取出票据；没有或格式不对时返回空串。 */
    function extractTrustedTicket(hash) {
        var text = String(hash || '');
        if (text.charAt(0) === '#') text = text.slice(1);
        if (!text) return '';
        var pairs = text.split('&');
        for (var index = 0; index < pairs.length; index += 1) {
            var pair = pairs[index];
            var split = pair.indexOf('=');
            var rawKey = split >= 0 ? pair.slice(0, split) : pair;
            var rawValue = split >= 0 ? pair.slice(split + 1) : '';
            var key = rawKey;
            try {
                key = decodeURIComponent(rawKey);
            } catch (error) {
                key = rawKey;
            }
            if (key !== FRAGMENT_KEY) continue;
            try {
                return decodeURIComponent(rawValue);
            } catch (error) {
                return rawValue;
            }
        }
        return '';
    }

    /** 从片段中移除票据参数，保留其它片段参数（页面 hash 路由仍可用）。 */
    function stripTrustedFragment(hash) {
        var text = String(hash || '');
        if (text.charAt(0) === '#') text = text.slice(1);
        if (!text) return '';
        var kept = [];
        var pairs = text.split('&');
        for (var index = 0; index < pairs.length; index += 1) {
            var pair = pairs[index];
            var split = pair.indexOf('=');
            var rawKey = split >= 0 ? pair.slice(0, split) : pair;
            var key = rawKey;
            try {
                key = decodeURIComponent(rawKey);
            } catch (error) {
                key = rawKey;
            }
            if (key === FRAGMENT_KEY) continue;
            kept.push(pair);
        }
        return kept.join('&');
    }

    /** 读取标准错误包里的错误码与文案；读不到就返回空值。 */
    function readErrorPayload(response) {
        if (!response) return Promise.resolve({code: '', message: ''});
        var reader = typeof response.clone === 'function'
            ? response.clone()
            : response;
        if (typeof reader.json !== 'function') {
            return Promise.resolve({code: '', message: ''});
        }
        return Promise.resolve()
            .then(function () { return reader.json(); })
            .then(function (data) {
                var detail = data && data.detail;
                if (detail && typeof detail === 'object') {
                    return {
                        code: String(detail.code || ''),
                        message: String(detail.message || '')
                    };
                }
                return {code: '', message: ''};
            })
            .catch(function () { return {code: '', message: ''}; });
    }

    /** 构造一个受信本机引导实例；参数用于测试注入时序与传输。 */
    function createTrustedLocalBootstrap(options) {
        var config = options || {};
        var win = config.window || global;
        var slowThresholdMs = Number(config.slowThresholdMs || SLOW_THRESHOLD_MS);
        var timeoutMs = Number(config.timeoutMs || TIMEOUT_MS);
        var retryDelayMs = Number(config.retryDelayMs || RETRY_DELAY_MS);
        var maxAutoRetry = config.maxAutoRetry === undefined
            ? MAX_AUTO_RETRY
            : Number(config.maxAutoRetry);
        var fetchImpl = config.fetch || (win.fetch ? win.fetch.bind(win) : null);
        var now = config.now || function () { return Date.now(); };
        var setTimer = config.setTimeout || win.setTimeout
            ? (config.setTimeout || win.setTimeout).bind(win)
            : function (fn, ms) { return setTimeout(fn, ms); };
        var clearTimer = config.clearTimeout || win.clearTimeout
            ? (config.clearTimeout || win.clearTimeout).bind(win)
            : function (id) { clearTimeout(id); };

        var instance = {
            /** 内存中的票据；兑换结束或被丢弃后清空。 */
            ticket: '',
            /** 状态机取值。 */
            state: STATE.IDLE,
            /** 失败原因（只含错误码与文案，绝不含票据）。 */
            error: null,
            /** 递增代号：避免过期回调覆盖新状态。 */
            generation: 0,
            /** 是否已越过慢档阈值。 */
            slow: false,
            /** 兑换结束（或超时）后 resolve 的 promise。 */
            ready: null
        };

        function dispatch(detail) {
            if (typeof win.dispatchEvent !== 'function') return;
            if (typeof win.CustomEvent !== 'function') return;
            win.dispatchEvent(new win.CustomEvent('gw-trusted-local', {detail: detail}));
        }

        function setState(nextState, detail) {
            var payload = detail || {};
            instance.generation += 1;
            instance.state = nextState;
            instance.error = payload.error || null;
            dispatch({
                state: nextState,
                error: instance.error,
                generation: instance.generation,
                slow: instance.slow,
                message: payload.message || ''
            });
        }

        function delay(ms) {
            return new Promise(function (resolve) {
                setTimer(resolve, Math.max(0, ms));
            });
        }

        /** 发起兑换：优先走既有 API 外观层，否则同源裸 fetch。 */
        function requestBootstrap(ticket) {
            var api = win.GodsWorkbenchAssetAuthApi;
            if (api && typeof api.bootstrapTrustedLocal === 'function') {
                return api.bootstrapTrustedLocal(ticket);
            }
            if (typeof fetchImpl !== 'function') {
                return Promise.reject(new Error('本机启动票据兑换不可用'));
            }
            return fetchImpl(BOOTSTRAP_PATH, {
                method: 'POST',
                credentials: 'same-origin',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({ticket: ticket})
            });
        }

        function classifyResponse(response) {
            var status = response && typeof response.status === 'number'
                ? response.status
                : 0;
            if (status >= 200 && status < 300) return Promise.resolve({kind: 'ok'});
            return readErrorPayload(response).then(function (payload) {
                if (payload.code === 'TRUSTED_LOCAL_CLAIM_REQUIRED') {
                    return {kind: 'claim', code: payload.code, message: payload.message};
                }
                if (status === 502 || status === 503 || status === 504) {
                    return {kind: 'retryable', code: payload.code, message: payload.message};
                }
                return {kind: 'rejected', code: payload.code, message: payload.message};
            });
        }

        function classifyThrown(error) {
            var status = error && typeof error.status === 'number' ? error.status : 0;
            if (error && error.retryable === true) {
                return {kind: 'retryable', code: String(error.code || ''), message: ''};
            }
            if (status >= 400 && status < 600) {
                // 明确的 HTTP 错误（如路由未接入）不重试：重试也不会改变结果。
                return {
                    kind: 'rejected',
                    code: String((error && error.code) || ''),
                    message: String((error && error.message) || '')
                };
            }
            // 没有 HTTP 状态码：传输层失败（连接中断等），允许一次自动重试。
            return {
                kind: 'retryable',
                code: String((error && error.code) || ''),
                message: String((error && error.message) || '')
            };
        }

        /** 单次兑换尝试；超过本次预算即按超时处理。 */
        function singleAttempt(ticket, budgetMs) {
            return new Promise(function (resolve) {
                var settled = false;
                var timer = setTimer(function () {
                    if (settled) return;
                    settled = true;
                    resolve({kind: 'timeout'});
                }, Math.max(0, budgetMs));
                var pending;
                try {
                    pending = requestBootstrap(ticket);
                } catch (error) {
                    if (settled) return;
                    settled = true;
                    clearTimer(timer);
                    resolve(classifyThrown(error));
                    return;
                }
                Promise.resolve(pending).then(function (response) {
                    if (settled) return;
                    settled = true;
                    clearTimer(timer);
                    resolve(classifyResponse(response));
                }, function (error) {
                    if (settled) return;
                    settled = true;
                    clearTimer(timer);
                    resolve(classifyThrown(error));
                });
            });
        }

        /** 兑换主流程：整体预算 TIMEOUT_MS，自动重试最多一次。 */
        function exchange(ticket) {
            var startedAt = now();
            var deadline = startedAt + timeoutMs;
            var retryAt = startedAt + retryDelayMs;
            var attempts = 0;

            function attempt() {
                attempts += 1;
                var remaining = deadline - now();
                if (remaining <= 0) {
                    return Promise.resolve({state: STATE.FALLBACK_PASSWORD, reason: 'timeout'});
                }
                return singleAttempt(ticket, remaining).then(function (outcome) {
                    if (outcome.kind === 'ok') return {state: STATE.ENTERED};
                    if (outcome.kind === 'claim') {
                        return {
                            state: STATE.NEEDS_CLAIM,
                            code: outcome.code,
                            message: outcome.message
                        };
                    }
                    if (outcome.kind === 'timeout') {
                        return {state: STATE.FALLBACK_PASSWORD, reason: 'timeout'};
                    }
                    if (outcome.kind === 'retryable' && attempts <= maxAutoRetry) {
                        return delay(Math.max(0, retryAt - now())).then(attempt);
                    }
                    return {
                        state: STATE.FAILED,
                        code: outcome.code || '',
                        message: outcome.message || ''
                    };
                });
            }

            var slowTimer = setTimer(function () {
                if (instance.state !== STATE.BOOTSTRAPPING) return;
                instance.slow = true;
                dispatch({
                    state: instance.state,
                    error: instance.error,
                    generation: instance.generation,
                    slow: true,
                    message: '本机自动进入较慢，正在等待本机确认…'
                });
            }, slowThresholdMs);

            return attempt().then(function (outcome) {
                clearTimer(slowTimer);
                // 终态一律清空内存中的票据：用过、过期或被拒都不再需要保留。
                instance.ticket = '';
                setState(outcome.state, {
                    error: outcome.state === STATE.FAILED
                        ? (outcome.code || 'TRUSTED_LOCAL_BOOTSTRAP_INVALID')
                        : null,
                    message: outcome.message || ''
                });
                return {
                    state: outcome.state,
                    code: outcome.code || '',
                    message: outcome.message || '',
                    reason: outcome.reason || '',
                    generation: instance.generation
                };
            });
        }

        /** 读取片段、抹除、决定是否兑换。 */
        function start() {
            var location = win.location || {};
            var hash = location.hash || '';
            var ticket = extractTrustedTicket(hash);
            var cleaned = stripTrustedFragment(hash);
            if (cleaned !== hash.replace(/^#/, '')) {
                var pathname = location.pathname || '';
                var search = location.search || '';
                var target = pathname + search + (cleaned ? '#' + cleaned : '');
                try {
                    if (win.history && typeof win.history.replaceState === 'function') {
                        win.history.replaceState({}, '', target || '/');
                    }
                } catch (error) {
                    // 抹除失败不阻断兑换：票据仍然短时一次性。
                }
            }
            if (!ticket) {
                instance.ready = Promise.resolve({state: STATE.IDLE, generation: instance.generation});
                return instance.ready;
            }
            // 隔离闸门：非本机来源一律丢弃票据，禁止向远程端转发。
            if (!isLoopbackHost(location.hostname)) {
                instance.ticket = '';
                setState(STATE.REMOTE_REJECTED, {
                    message: '非本机来源，已丢弃本机启动票据'
                });
                instance.ready = Promise.resolve({
                    state: STATE.REMOTE_REJECTED,
                    message: '非本机来源，已丢弃本机启动票据',
                    generation: instance.generation
                });
                return instance.ready;
            }
            instance.ticket = ticket;
            setState(STATE.BOOTSTRAPPING, {message: '正在进入本机…'});
            instance.ready = exchange(ticket);
            return instance.ready;
        }

        instance.STATE = STATE;
        instance.start = start;
        instance.isLoopbackHost = isLoopbackHost;
        instance.extractTrustedTicket = extractTrustedTicket;
        instance.stripTrustedFragment = stripTrustedFragment;
        instance.readErrorPayload = readErrorPayload;
        instance.create = createTrustedLocalBootstrap;
        return instance;
    }

    var instance = createTrustedLocalBootstrap({window: global});
    instance.FRAGMENT_KEY = FRAGMENT_KEY;
    instance.BOOTSTRAP_PATH = BOOTSTRAP_PATH;
    instance.SLOW_THRESHOLD_MS = SLOW_THRESHOLD_MS;
    instance.TIMEOUT_MS = TIMEOUT_MS;
    instance.RETRY_DELAY_MS = RETRY_DELAY_MS;
    instance.MAX_AUTO_RETRY = MAX_AUTO_RETRY;
    global.GWTrustedLocal = instance;
    instance.start();
})(globalThis);
