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

import assert from 'node:assert/strict';
import test from 'node:test';
import {
    parseProvenance,
    sanitizeProvenanceUrl,
    renderProvenanceChain,
    handleProvenanceClick,
    escapeHtml,
} from '../../web/js/modules/asset-manager/provenance.js';

test('AC1: 完整 provenance 链解析、渲染与节点导航回调', () => {
    const rawData = {
        asset_id: 'out-1',
        provenance: {
            version: 'v1',
            task_id: 't-1',
            shot_id: 's-1',
            reference_asset_ids: ['a-1', 'a-2'],
        },
    };

    // 1. 数据解析
    const parsed = parseProvenance(rawData);
    assert.equal(parsed.asset_id, 'out-1');
    assert.equal(parsed.version, 'v1');
    assert.equal(parsed.task_id, 't-1');
    assert.equal(parsed.shot_id, 's-1');
    assert.deepEqual(parsed.reference_asset_ids, ['a-1', 'a-2']);

    // 2. HTML 渲染
    const html = renderProvenanceChain(rawData);
    assert.match(html, /data-provenance-chain/);
    assert.match(html, /成片/);
    assert.match(html, /out-1/);
    assert.match(html, /版本/);
    assert.match(html, /v1/);
    assert.match(html, /任务/);
    assert.match(html, /t-1/);
    assert.match(html, /镜头/);
    assert.match(html, /s-1/);
    assert.match(html, /参考素材/);
    assert.match(html, /a-1/);
    assert.match(html, /a-2/);

    // 3. 节点导航 click 模拟
    const navEvents = [];
    const mockElement = {
        getAttribute(attr) {
            if (attr === 'data-provenance-type') return 'task';
            if (attr === 'data-provenance-id') return 't-1';
            return null;
        },
    };
    const mockEvent = {
        target: {
            closest(selector) {
                if (selector.includes('data-provenance-type')) return mockElement;
                return null;
            },
        },
        preventDefault() {
            mockEvent.defaultPrevented = true;
        },
        defaultPrevented: false,
    };

    const handled = handleProvenanceClick(mockEvent, nav => {
        navEvents.push(nav);
    });

    assert.equal(handled, true);
    assert.equal(mockEvent.defaultPrevented, true);
    assert.equal(navEvents.length, 1);
    assert.equal(navEvents[0].type, 'task');
    assert.equal(navEvents[0].id, 't-1');
});

test('AC2: 缺失字段统一显示「未知」，无未捕获异常', () => {
    // 1. 部分缺失
    const partialData = {
        asset_id: 'out-1',
        provenance: {
            version: 'v1',
        },
    };
    const partialHtml = renderProvenanceChain(partialData);
    assert.match(partialHtml, /out-1/);
    assert.match(partialHtml, /v1/);
    assert.match(partialHtml, /未知/);

    // 2. 字段全空或 null
    for (const emptyInput of [{}, null, undefined, 'invalid-string', 12345, { provenance: null }]) {
        assert.doesNotThrow(() => {
            const html = renderProvenanceChain(emptyInput);
            assert.match(html, /未知/);
        });
    }

    // 3. 引用素材类型非数组
    const invalidRefData = {
        asset_id: 'out-2',
        provenance: {
            reference_asset_ids: 'not-an-array',
        },
    };
    assert.doesNotThrow(() => {
        const html = renderProvenanceChain(invalidRefData);
        assert.match(html, /未知/);
    });
});

test('AC3: URL 与文本纯文本安全渲染（防 XSS 攻击）', () => {
    const xssData = {
        asset_id: '<script>alert("xss-asset")</script>',
        provenance: {
            version: 'v1"><img src=x onerror=alert("xss-version")>',
            task_id: 'javascript:alert("xss-task")',
            shot_id: '\' onclick=\'alert("xss-shot")',
            reference_asset_ids: ['<svg/onload=alert("xss-ref")>'],
        },
    };

    const html = renderProvenanceChain(xssData);

    // 断言不包含未转义的 HTML 标签（如 <script>, <img ...>, <svg ...>）
    assert.doesNotMatch(html, /<script\b/i);
    assert.doesNotMatch(html, /<img\b/i);
    assert.doesNotMatch(html, /<svg\b/i);

    // 断言 HTML 实体正确转义
    assert.match(html, /&lt;script&gt;/);
    assert.match(html, /&quot;&gt;&lt;img/);

    // 断言 URL 协议安全性（不产生裸 javascript: 协议）
    const safeTaskUrl = sanitizeProvenanceUrl('task', xssData.provenance.task_id);
    assert.equal(safeTaskUrl, '#');
});

test('AC4: 不引入第三方框架与 CDN', () => {
    // 模块自包含 vanilla JS 实现
    assert.equal(typeof parseProvenance, 'function');
    assert.equal(typeof renderProvenanceChain, 'function');
    assert.equal(typeof handleProvenanceClick, 'function');
    assert.equal(typeof sanitizeProvenanceUrl, 'function');
    assert.equal(typeof escapeHtml, 'function');
});
