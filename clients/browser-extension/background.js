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
 * MVP1-T8b 后台 service worker：右键菜单（主入口）、配对令牌缓存、应用页发现与延迟日志。
 *
 * 分工：本文件只做编排与状态，**不直接调用收集接口**——鉴权请求必须由工作台页面源的
 * `content/app-bridge.js` 完成（会话 Cookie 为 SameSite=strict，扩展源带不上）。
 */

importScripts('src/target-resolver.js', 'src/collect-client.js');

(function (root) {
    'use strict';

    var DEFAULT_ENDPOINT = 'http://127.0.0.1:2077';
    var SETTINGS_KEY = 'settings';
    var SESSION_KEY = 'pair';
    var HISTORY_KEY = 'gw.collect.history';
    var MAX_HISTORY = 20;
    var TOKEN_TTL_MS = 50 * 60 * 1000;

    if (!root.GwCollectTarget || !root.GwCollectClient) {
        throw new Error('神工坊扩展缺少共享逻辑脚本');
    }

    var MENU_DEFINITIONS = [
        {id: 'gw-collect-image', title: '收入神工坊：收藏这张图片', contexts: ['image']},
        {id: 'gw-collect-video', title: '收入神工坊：收藏该视频地址', contexts: ['video']},
        {id: 'gw-collect-link', title: '收入神工坊：收藏链接目标', contexts: ['link']},
        {id: 'gw-collect-page', title: '收入神工坊：收藏本页地址', contexts: ['page']}
    ];

    function sleep(ms) {
        return new Promise(function (resolve) { setTimeout(resolve, ms); });
    }

    function taggedError(code, message) {
        var error = new Error(message || root.GwCollectClient.copyFor(code));
        error.errorCode = code;
        return error;
    }

    function readSettings(api) {
        var defaults = {};
        defaults[SETTINGS_KEY] = {endpoint: DEFAULT_ENDPOINT, ctrlRightClick: true};
        return api.storage.local.get(defaults).then(function (items) {
            var settings = (items && items[SETTINGS_KEY]) || {};
            var endpoint;
            try {
                endpoint = root.GwCollectClient.normalizeEndpoint(settings.endpoint || DEFAULT_ENDPOINT);
            } catch (error) {
                endpoint = DEFAULT_ENDPOINT;
            }
            return {endpoint: endpoint, ctrlRightClick: settings.ctrlRightClick !== false};
        });
    }

    function writeSettings(api, settings) {
        var next = {};
        next[SETTINGS_KEY] = {
            endpoint: root.GwCollectClient.normalizeEndpoint((settings && settings.endpoint) || DEFAULT_ENDPOINT),
            ctrlRightClick: !settings || settings.ctrlRightClick !== false
        };
        return api.storage.local.set(next).then(function () { return next[SETTINGS_KEY]; });
    }

    function readToken(api, endpoint) {
        var store = api.storage.session;
        if (!store) return Promise.resolve('');
        return store.get(SESSION_KEY).then(function (items) {
            var pair = items && items[SESSION_KEY];
            if (!pair || pair.endpoint !== endpoint || !pair.token) return '';
            if (Number(pair.expiresAt || 0) <= Date.now()) return '';
            return String(pair.token);
        }).catch(function () {
            return '';
        });
    }

    function writeToken(api, endpoint, token) {
        var store = api.storage.session;
        if (!store) return Promise.resolve();
        var value = {};
        value[SESSION_KEY] = {endpoint: endpoint, token: token, expiresAt: Date.now() + TOKEN_TTL_MS};
        return store.set(value).catch(function () {});
    }

    function findAppTab(api, endpoint) {
        return api.tabs.query({url: endpoint + '/*'}).then(function (tabs) {
            return tabs && tabs.length ? tabs[tabs.length - 1] : null;
        });
    }

    function ensureAppTab(api, endpoint) {
        return findAppTab(api, endpoint).then(function (tab) {
            if (tab) return tab;
            return api.tabs.create({url: endpoint + '/', active: false});
        });
    }

    /** 向工作台页发送消息；页面刚拉起或 service worker 冷启动时按间隔重试。 */
    function sendToTab(api, tabId, message, attempts, gapMs) {
        var left = Number.isFinite(attempts) ? attempts : 24;
        var gap = Number.isFinite(gapMs) ? gapMs : 200;
        function attempt(index) {
            return api.tabs.sendMessage(tabId, message).then(function (response) {
                if (response) return response;
                if (index + 1 >= left) throw taggedError('app_unreachable');
                return sleep(gap).then(function () { return attempt(index + 1); });
            }, function () {
                if (index + 1 >= left) throw taggedError('app_unreachable');
                return sleep(gap).then(function () { return attempt(index + 1); });
            });
        }
        return attempt(0);
    }

    function ensureToken(api, endpoint, tabId) {
        return readToken(api, endpoint).then(function (cached) {
            if (cached) return cached;
            return sendToTab(api, tabId, {type: 'gw:ensure-pair'}, 5, 200).then(function (response) {
                if (!response || !response.ok || !response.token) {
                    throw taggedError((response && response.errorCode) || 'pair_failed');
                }
                return writeToken(api, endpoint, String(response.token)).then(function () {
                    return String(response.token);
                });
            });
        });
    }

    function remember(api, record) {
        return api.storage.local.get({[HISTORY_KEY]: []}).then(function (items) {
            var history = Array.isArray(items && items[HISTORY_KEY]) ? items[HISTORY_KEY].slice() : [];
            history.unshift(record);
            var next = {};
            next[HISTORY_KEY] = history.slice(0, MAX_HISTORY);
            return api.storage.local.set(next);
        }).catch(function () {});
    }

    function showBadge(api, record) {
        if (!api.action || !api.action.setBadgeText) return Promise.resolve();
        var seconds = (Number(record.elapsedMs || 0) / 1000).toFixed(1);
        var title = record.ok ? '已收入神工坊（' + seconds + 's）' : ('收集失败：' + (record.message || ''));
        return Promise.all([
            api.action.setBadgeBackgroundColor({color: record.ok ? '#2f7d4f' : '#a33a2a'}),
            api.action.setBadgeText({text: record.ok ? '✓' : '!'}),
            api.action.setTitle ? api.action.setTitle({title: title}) : Promise.resolve()
        ]).catch(function () {});
    }

    function publish(api, result) {
        var elapsedMs = Number.isFinite(result.elapsedMs) ? result.elapsedMs : Date.now() - result.startedAt;
        var record = {
            ok: !!result.ok,
            status: result.ok ? 'done' : 'failed',
            url: String(result.url || ''),
            jobId: String(result.jobId || ''),
            errorCode: String(result.errorCode || ''),
            message: result.message || (result.ok ? '' : root.GwCollectClient.copyFor(result.errorCode)),
            elapsedMs: elapsedMs,
            at: new Date().toISOString()
        };
        return remember(api, record).then(function () {
            return showBadge(api, record);
        }).then(function () {
            return record;
        });
    }

    /** 收集主流程：校验地址 → 找到/拉起工作台 → 取令牌 → 交给页面源提交并轮询。 */
    function dispatchCollect(api, request) {
        var startedAt = Date.now();
        var pageUrl = String((request && request.pageUrl) || '');
        var classified = root.GwCollectTarget.classifyUrl((request && request.url) || '', pageUrl || undefined);
        if (!classified.url) {
            return publish(api, {
                ok: false,
                errorCode: classified.errorCode || 'no_target',
                url: String((request && request.url) || ''),
                startedAt: startedAt
            });
        }
        return readSettings(api).then(function (settings) {
            return ensureAppTab(api, settings.endpoint).then(function (tab) {
                // 先确保令牌就绪（页面源缓存），再让页面源执行提交与轮询。
                return ensureToken(api, settings.endpoint, tab.id).then(function () {
                    return sendToTab(api, tab.id, {
                        type: 'gw:collect-url',
                        url: classified.url,
                        title: String((request && request.title) || ''),
                        referer: pageUrl
                    }, 3, 200);
                });
            }).then(function (response) {
                if (response && response.ok) {
                    return publish(api, {
                        ok: true,
                        url: classified.url,
                        jobId: response.jobId,
                        elapsedMs: response.elapsedMs,
                        startedAt: startedAt
                    });
                }
                return publish(api, {
                    ok: false,
                    errorCode: (response && response.errorCode) || 'collect_failed',
                    message: (response && response.message) || '',
                    url: classified.url,
                    startedAt: startedAt
                });
            });
        }).catch(function (error) {
            return publish(api, {
                ok: false,
                errorCode: (error && error.errorCode) || 'collect_failed',
                message: (error && error.message) || '',
                url: classified.url,
                startedAt: startedAt
            });
        });
    }

    /** 右键菜单点击 → 统一的收集请求（菜单为主入口，不拦截页面）。 */
    function menuRequest(info, tab) {
        var detail = info || {};
        var source = tab || {};
        var pageUrl = detail.pageUrl || source.url || '';
        var base = {pageUrl: pageUrl, title: source.title || ''};
        if (detail.menuItemId === 'gw-collect-image') {
            base.url = detail.srcUrl || '';
            base.kind = 'image';
        } else if (detail.menuItemId === 'gw-collect-video') {
            base.url = detail.srcUrl || '';
            base.kind = 'video';
        } else if (detail.menuItemId === 'gw-collect-link') {
            base.url = detail.linkUrl || '';
            base.kind = 'link';
        } else {
            base.url = pageUrl;
            base.kind = 'page';
        }
        return base;
    }

    function pairNow(api) {
        return readSettings(api).then(function (settings) {
            return ensureAppTab(api, settings.endpoint).then(function (tab) {
                return sendToTab(api, tab.id, {type: 'gw:ensure-pair'}, 10, 250).then(function (response) {
                    if (!response || !response.ok || !response.token) {
                        throw taggedError((response && response.errorCode) || 'pair_failed');
                    }
                    return writeToken(api, settings.endpoint, String(response.token)).then(function () {
                        return {ok: true, endpoint: settings.endpoint};
                    });
                });
            });
        });
    }

    function readState(api) {
        return Promise.all([
            readSettings(api),
            api.storage.local.get({[HISTORY_KEY]: []})
        ]).then(function (values) {
            var settings = values[0];
            var history = Array.isArray(values[1] && values[1][HISTORY_KEY]) ? values[1][HISTORY_KEY] : [];
            return readToken(api, settings.endpoint).then(function (token) {
                return {
                    ok: true,
                    endpoint: settings.endpoint,
                    ctrlRightClick: settings.ctrlRightClick,
                    paired: !!token,
                    lastCollect: history.length ? history[0] : null,
                    history: history
                };
            });
        });
    }

    function register(api) {
        if (!api || !api.contextMenus || !api.runtime) {
            throw new Error('注册后台需要 chrome 扩展 API');
        }
        function buildMenus() {
            return Promise.resolve(api.contextMenus.removeAll()).then(function () {
                MENU_DEFINITIONS.forEach(function (definition) {
                    api.contextMenus.create(definition);
                });
            });
        }
        api.runtime.onInstalled.addListener(function () { void buildMenus(); });
        api.contextMenus.onClicked.addListener(function (info, tab) {
            void dispatchCollect(api, menuRequest(info, tab));
        });
        api.runtime.onMessage.addListener(function (message, sender, sendResponse) {
            if (!message || typeof message.type !== 'string') return undefined;
            if (message.type === 'gw:collect') {
                dispatchCollect(api, message).then(sendResponse).catch(function (error) {
                    sendResponse({
                        ok: false,
                        errorCode: (error && error.errorCode) || 'collect_failed',
                        message: (error && error.message) || ''
                    });
                });
                return true;
            }
            if (message.type === 'gw:pair-now') {
                pairNow(api).then(sendResponse).catch(function (error) {
                    sendResponse({ok: false, errorCode: (error && error.errorCode) || 'pair_failed'});
                });
                return true;
            }
            if (message.type === 'gw:state') {
                readState(api).then(sendResponse).catch(function () {
                    sendResponse({ok: false});
                });
                return true;
            }
            if (message.type === 'gw:save-settings') {
                writeSettings(api, message.settings).then(function (settings) {
                    sendResponse({ok: true, settings: settings});
                }).catch(function (error) {
                    sendResponse({ok: false, errorCode: (error && error.errorCode) || 'endpoint_not_loopback'});
                });
                return true;
            }
            if (message.type === 'gw:app-ready') {
                sendResponse({ok: true});
                return undefined;
            }
            return undefined;
        });
        return {buildMenus: buildMenus};
    }

    root.GwCollectBackground = {
        DEFAULT_ENDPOINT: DEFAULT_ENDPOINT,
        MENU_DEFINITIONS: MENU_DEFINITIONS,
        MAX_HISTORY: MAX_HISTORY,
        HISTORY_KEY: HISTORY_KEY,
        register: register,
        dispatchCollect: dispatchCollect,
        menuRequest: menuRequest,
        readSettings: readSettings,
        writeSettings: writeSettings,
        readState: readState,
        pairNow: pairNow
    };

    if (typeof chrome !== 'undefined' && chrome && chrome.contextMenus && chrome.runtime) {
        try {
            register(chrome);
        } catch (error) {
            console.error('神工坊扩展后台注册失败', error);
        }
    }
})(typeof globalThis !== 'undefined' ? globalThis : this);
