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
 * MVP1-T8b 收集目标解析。
 *
 * 纯逻辑，不触碰 DOM、不发网络请求：content script 负责把元素描述成普通对象，
 * 本模块只按 AC11 契约把描述解析成可提交的 http(s) 地址或稳定错误码。
 * 挂载到 `globalThis.GwCollectTarget`，Node 测试直接读取该全局对象。
 */
(function (root) {
    'use strict';

    /** 浏览器内部地址：页外无法抓取，必须明确失败而不是静默丢弃。 */
    var INTERNAL_SCHEMES = {
        'blob:': 'blob_url',
        'data:': 'data_url',
        'mediasource:': 'media_stream',
        'filesystem:': 'blob_url'
    };

    function schemeOf(value) {
        var index = value.indexOf(':');
        return index > 0 ? value.slice(0, index + 1).toLowerCase() : '';
    }

    /**
     * 判定单个地址：只接受无凭据的 http(s)，其余给出契约错误码。
     * 返回 {url} 或 {errorCode}，两者互斥。
     */
    function classifyUrl(rawUrl, baseUrl) {
        var raw = typeof rawUrl === 'string' ? rawUrl.trim() : '';
        if (!raw) return {errorCode: 'no_target'};
        var scheme = schemeOf(raw);
        if (INTERNAL_SCHEMES[scheme]) return {errorCode: INTERNAL_SCHEMES[scheme]};
        if (scheme && scheme !== 'http:' && scheme !== 'https:') {
            return {errorCode: 'unsupported_url'};
        }
        var absolute;
        try {
            absolute = baseUrl ? new URL(raw, baseUrl) : new URL(raw);
        } catch (error) {
            return {errorCode: 'unsupported_url'};
        }
        if (absolute.protocol !== 'http:' && absolute.protocol !== 'https:') {
            return {errorCode: 'unsupported_url'};
        }
        if (absolute.username || absolute.password) {
            return {errorCode: 'unsupported_url'};
        }
        return {url: absolute.href};
    }

    /** srcset 取最大候选：优先最大宽度描述符，其次最大倍率，无描述符按 1x 计。 */
    function pickLargestFromSrcset(srcset, baseUrl) {
        var text = typeof srcset === 'string' ? srcset : '';
        if (!text.trim()) return '';
        var widths = [];
        var densities = [];
        text.split(',').forEach(function (part) {
            var pieces = part.trim().split(/\s+/);
            var url = pieces[0];
            if (!url) return;
            var descriptor = (pieces[1] || '').toLowerCase();
            var widthMatch = /^([0-9.]+)w$/.exec(descriptor);
            var densityMatch = /^([0-9.]+)x$/.exec(descriptor);
            if (widthMatch) {
                widths.push({url: url, weight: parseFloat(widthMatch[1]) || 0});
            } else if (densityMatch) {
                densities.push({url: url, weight: parseFloat(densityMatch[1]) || 0});
            } else {
                densities.push({url: url, weight: 1});
            }
        });
        var pool = widths.length ? widths : densities;
        if (!pool.length) return '';
        var best = pool[0];
        pool.forEach(function (item) {
            if (item.weight > best.weight) best = item;
        });
        var classified = classifyUrl(best.url, baseUrl);
        return classified.url || '';
    }

    /** 从 computedStyle 的 background-image 里取第一个 url()。 */
    function extractBackgroundImage(styleValue, baseUrl) {
        var text = typeof styleValue === 'string' ? styleValue : '';
        var match = /url\(\s*(?:['"])?(.+?)(?:['"])?\s*\)/i.exec(text);
        if (!match) return '';
        var classified = classifyUrl(match[1], baseUrl);
        return classified.url || '';
    }

    /** 按元素类别列出候选（顺序即优先级），只做字符串收集。 */
    function candidatesOf(source) {
        var tag = String(source.tagName || '').toUpperCase();
        var baseUrl = source.pageUrl || undefined;
        var list = [];
        var push = function (kind, raw) {
            if (typeof raw === 'string' && raw.trim()) list.push({kind: kind, raw: raw});
        };
        if (tag === 'IMG' || tag === '') {
            push('image', source.currentSrc);
            push('image', source.src);
            push('image', pickLargestFromSrcset(source.srcset, baseUrl));
            push('image', extractBackgroundImage(source.backgroundImage, baseUrl));
        }
        if (tag === 'VIDEO' || tag === '') {
            push('video', source.src);
            push('image', source.poster);
        }
        if (tag === 'A' || tag === 'SOURCE' || tag === '') {
            push('link', source.href);
        }
        if (tag !== '' && tag !== 'IMG' && tag !== 'VIDEO' && tag !== 'A' && tag !== 'SOURCE') {
            push('image', extractBackgroundImage(source.backgroundImage, baseUrl));
        }
        return {list: list, baseUrl: baseUrl};
    }

    /**
     * 解析收集目标：元素自身地址优先；元素完全没有候选地址时才用页面地址兜底。
     *
     * 关键契约：用户右键点中的是元素时，元素地址不可用（如 `blob:`）必须**明确失败**，
     * 不能静默退化成「收集当前页面」，否则 AC11 的「blob/MSE 明确提示不支持」会失效。
     */
    function resolveCollectTarget(input) {
        var source = input && typeof input === 'object' ? input : {};
        var candidates = candidatesOf(source);
        var firstError = '';
        for (var index = 0; index < candidates.list.length; index += 1) {
            var item = candidates.list[index];
            var classified = classifyUrl(item.raw, candidates.baseUrl);
            if (classified.url) return {url: classified.url, kind: item.kind};
            if (!firstError && classified.errorCode && classified.errorCode !== 'no_target') {
                firstError = classified.errorCode;
            }
        }
        if (candidates.list.length) {
            return {errorCode: firstError || 'unsupported_url'};
        }
        var page = classifyUrl(source.pageUrl, undefined);
        if (page.url) return {url: page.url, kind: 'page'};
        return {errorCode: firstError || page.errorCode || 'no_target'};
    }

    root.GwCollectTarget = {
        INTERNAL_SCHEMES: INTERNAL_SCHEMES,
        classifyUrl: classifyUrl,
        pickLargestFromSrcset: pickLargestFromSrcset,
        extractBackgroundImage: extractBackgroundImage,
        resolveCollectTarget: resolveCollectTarget
    };
})(typeof globalThis !== 'undefined' ? globalThis : this);
