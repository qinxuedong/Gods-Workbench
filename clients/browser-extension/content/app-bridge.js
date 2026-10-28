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
 * MVP1-T8b 工作台页面桥接（只注入工作台自身源）。
 *
 * 为什么网络请求放在这里而不是 service worker：工作台会话 Cookie 为
 * `SameSite=strict`，`chrome-extension://` 源属于跨站，携带不上会话，因此所有
 * 鉴权请求必须在**工作台页面源**内发起；扩展只通过消息把地址与令牌串起来，
 * 不读取、不保存 Cookie 原文。
 */
(function (root) {
    'use strict';

    if (typeof chrome === 'undefined' || !chrome.runtime || !chrome.runtime.onMessage) return;
    if (!root.GwCollectClient) return;

    var TOKEN_TTL_MS = 50 * 60 * 1000;
    var COLLECT_DEADLINE_MS = 2500;
    var client = root.GwCollectClient.create({endpoint: location.origin});
    var token = '';
    var pairedAt = 0;

    function ensureToken(force) {
        if (!force && token && Date.now() - pairedAt < TOKEN_TTL_MS) {
            return Promise.resolve(token);
        }
        return client.pair().then(function (fresh) {
            token = fresh;
            pairedAt = Date.now();
            return token;
        });
    }

    function collect(url) {
        return ensureToken(false).then(function (current) {
            return client.collectUrl(url, {token: current, deadlineMs: COLLECT_DEADLINE_MS});
        }).catch(function (error) {
            // 工作台重启会让旧令牌失效：重配对后只重试一次。
            if (!error || error.errorCode !== 'unauthorized') throw error;
            return ensureToken(true).then(function (fresh) {
                return client.collectUrl(url, {token: fresh, deadlineMs: COLLECT_DEADLINE_MS});
            });
        });
    }

    function respond(sendResponse, promise) {
        promise.then(function (result) {
            sendResponse(result);
        }).catch(function (error) {
            sendResponse({
                ok: false,
                errorCode: (error && error.errorCode) || 'collect_failed',
                message: (error && error.message) || root.GwCollectClient.copyFor('collect_failed')
            });
        });
    }

    chrome.runtime.onMessage.addListener(function (message, sender, sendResponse) {
        if (!message || typeof message.type !== 'string') return undefined;
        if (message.type === 'gw:ensure-pair') {
            respond(sendResponse, ensureToken(true).then(function (value) {
                return {ok: true, token: value, endpoint: location.origin};
            }));
            return true;
        }
        if (message.type === 'gw:collect-url') {
            respond(sendResponse, collect(String(message.url || '')));
            return true;
        }
        return undefined;
    });

    // 页面加载即静默配对一次，省掉首次收集的一次往返；失败不打扰用户。
    ensureToken(false).catch(function () {});
    try {
        chrome.runtime.sendMessage({type: 'gw:app-ready', endpoint: location.origin}, function () {
            void chrome.runtime.lastError;
        });
    } catch (error) {
        // 扩展上下文切换（更新/重载）时发送失败属于预期，忽略。
    }
})(typeof globalThis !== 'undefined' ? globalThis : this);
