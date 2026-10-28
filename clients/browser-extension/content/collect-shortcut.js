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
 * MVP1-T8b Ctrl+右键快捷收集（默认开启、可在选项页关闭）。
 *
 * 行为契约：
 * - 只有按住 Ctrl 右键才拦截；未按 Ctrl 时完全不干预，交给原生右键菜单（菜单项由后台注册）。
 * - 解析不到任何目标地址时不拦截原生菜单，避免破坏页面正常操作。
 * - 本脚本**不发任何网络请求**，只把地址交给后台；`blob:`/`data:`/`mediasource:` 明确提示不支持。
 */
(function (root) {
    'use strict';

    if (typeof chrome === 'undefined' || !chrome.runtime || !chrome.runtime.onMessage) return;
    if (!root.GwCollectTarget || !root.GwCollectClient) return;

    var SETTINGS_KEY = 'settings';
    var TOAST_ATTRIBUTE = 'data-gw-collect-toast';
    var TOAST_MS = 2600;
    var FEEDBACK_TIMEOUT_MS = 5000;
    var ctrlEnabled = true;
    var toastTimer = null;

    function readSettings() {
        try {
            chrome.storage.local.get({settings: {ctrlRightClick: true}}, function (items) {
                var settings = (items && items.settings) || {};
                ctrlEnabled = settings.ctrlRightClick !== false;
            });
        } catch (error) {
            ctrlEnabled = true;
        }
    }

    function showToast(text) {
        var host = document.body || document.documentElement;
        if (!host) return;
        var node = document.querySelector('[' + TOAST_ATTRIBUTE + ']');
        if (!node) {
            node = document.createElement('div');
            node.setAttribute(TOAST_ATTRIBUTE, '');
            node.setAttribute('role', 'status');
            node.setAttribute('aria-live', 'polite');
            node.style.cssText = [
                'position:fixed', 'right:16px', 'bottom:16px', 'z-index:2147483647',
                'max-width:320px', 'padding:10px 14px', 'border-radius:8px',
                'background:rgba(12,12,14,0.94)', 'color:#f5e6c8',
                'border:1px solid rgba(214,178,92,0.6)', 'box-shadow:0 8px 24px rgba(0,0,0,0.35)',
                'font:14px/1.5 system-ui,sans-serif', 'letter-spacing:0'
            ].join(';');
            host.appendChild(node);
        }
        node.textContent = String(text || '');
        if (toastTimer) clearTimeout(toastTimer);
        toastTimer = setTimeout(function () {
            toastTimer = null;
            node.remove();
        }, TOAST_MS);
    }

    /** 把一个元素描述成解析器可用的普通对象，不做任何网络或样式写入。 */
    function describe(element) {
        if (!element || !element.tagName) return null;
        var tag = String(element.tagName).toUpperCase();
        var source = {tagName: tag, pageUrl: location.href};
        if (tag === 'IMG') {
            source.currentSrc = element.currentSrc || '';
            source.src = element.getAttribute('src') || '';
            source.srcset = element.getAttribute('srcset') || '';
        } else if (tag === 'VIDEO') {
            source.src = element.currentSrc || element.getAttribute('src') || '';
            source.poster = element.getAttribute('poster') || '';
        } else if (tag === 'A') {
            source.href = element.getAttribute('href') || '';
        } else {
            var style = window.getComputedStyle ? window.getComputedStyle(element) : null;
            source.backgroundImage = style ? style.backgroundImage : '';
        }
        return source;
    }

    /** 只有这几种「浏览器内部地址」才是需要明确提示的目标；其余不成立的候选继续向上找。 */
    var INTERNAL_ERROR_CODES = ['blob_url', 'data_url', 'media_stream'];

    /**
     * 从事件路径上找第一个可用目标。
     * 快捷方式不做页面兜底：空白处 Ctrl+右键必须放行原生菜单，避免阻断页面操作。
     */
    function resolveEventTarget(event) {
        var path = typeof event.composedPath === 'function' ? event.composedPath() : [event.target];
        for (var index = 0; index < path.length; index += 1) {
            var node = path[index];
            if (!node || node.nodeType !== 1) continue;
            var source = describe(node);
            if (!source) continue;
            var target = root.GwCollectTarget.resolveCollectTarget(source, {pageFallback: false});
            if (target.url) return target;
            if (target.errorCode && INTERNAL_ERROR_CODES.indexOf(target.errorCode) !== -1) {
                return target;
            }
        }
        return {errorCode: 'no_target'};
    }

    function submit(target) {
        var settled = false;
        var timer = setTimeout(function () {
            if (settled) return;
            settled = true;
            showToast(root.GwCollectClient.copyFor('collect_timeout'));
        }, FEEDBACK_TIMEOUT_MS);
        try {
            chrome.runtime.sendMessage({
                type: 'gw:collect',
                url: target.url,
                kind: target.kind,
                title: document.title,
                pageUrl: location.href
            }, function (response) {
                if (settled) return;
                settled = true;
                clearTimeout(timer);
                if (chrome.runtime.lastError || !response) {
                    showToast('无法连接扩展后台，请确认扩展已启用');
                    return;
                }
                if (response.ok) {
                    var seconds = (Number(response.elapsedMs || 0) / 1000).toFixed(1);
                    showToast('已收入神工坊 · ' + seconds + 's');
                } else {
                    showToast(response.message || root.GwCollectClient.copyFor(response.errorCode));
                }
            });
        } catch (error) {
            settled = true;
            clearTimeout(timer);
            showToast('无法连接扩展后台，请确认扩展已启用');
        }
    }

    function onContextMenu(event) {
        if (!ctrlEnabled || !event.ctrlKey) return;
        var target = resolveEventTarget(event);
        if (!target.url && target.errorCode === 'no_target') return;
        // 已识别目标（含明确不支持的类型）：拦下原生菜单并给出确定反馈。
        event.preventDefault();
        event.stopPropagation();
        if (!target.url) {
            showToast(root.GwCollectClient.copyFor(target.errorCode));
            return;
        }
        showToast('正在收入神工坊…');
        submit(target);
    }

    readSettings();
    chrome.storage.onChanged.addListener(function (changes, area) {
        if (area !== 'local' || !changes.settings) return;
        var next = changes.settings.newValue || {};
        ctrlEnabled = next.ctrlRightClick !== false;
    });
    document.addEventListener('contextmenu', onContextMenu, true);

    root.GwCollectShortcut = {resolveEventTarget: resolveEventTarget, isCtrlEnabled: function () { return ctrlEnabled; }};
})(typeof globalThis !== 'undefined' ? globalThis : this);
