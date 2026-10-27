// Copyright 2026 Gods-Workbench Authors
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//     http://www.apache.org/licenses/LICENSE-2.0
//
// Unless required by applicable law or agreed to in writing, software
// distributed under the License is distributed on an "AS IS" BASIS,
// WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
// See the License for the specific language governing permissions and
// limitations under the License.

// MVP1-T5 治理建议清单视图静态契约：asset-manager.js 顶层即触 DOM，无法整体
// import，因此按既有 mvp1-t0 模式读取源码做结构断言。

import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

const source = readFileSync(
    new URL('../../web/js/modules/asset-manager.js', import.meta.url),
    'utf8',
);

test('governance board is a fixed system tab with stable id', () => {
    assert.match(
        source,
        /\{id:'governance', name:'治理建议', icon:'[^']+', system:true\}/,
    );
    assert.match(source, /else if\(activeTab === 'governance'\) renderGovernanceSuggestionsManager\(\);/);
});

test('suggestion view only calls the two implemented governance endpoints', () => {
    const apiCalls = [...source.matchAll(/['"]\/api\/asset-registry\/governance\/suggestions[^'"]*['"]/g)]
        .map(match => match[0].replace(/['"]/g, ''));
    assert.ok(apiCalls.includes('/api/asset-registry/governance/suggestions'));
    assert.ok(apiCalls.includes('/api/asset-registry/governance/suggestions/confirm'));
    for (const call of apiCalls) {
        assert.match(call, /^\/api\/asset-registry\/governance\/suggestions(\/confirm)?$/);
    }
});

test('confirm flow sends CAS expected_version and surfaces conflicts', () => {
    assert.match(
        source,
        /JSON\.stringify\(\{suggestion_id:id, expected_version:revision\}\)/,
    );
    // apiJsonResponse 统一处理 401 跳转与 409 VERSION_CONFLICT 提示。
    assert.match(source, /async function apiJsonResponse\(response\)/);
    assert.match(source, /VERSION_CONFLICT_MESSAGE/);
});

test('suggestion cards render per-kind details with a single confirm action', () => {
    assert.match(source, /data-governance-suggestion=/);
    assert.match(source, /data-governance-confirm=/);
    assert.match(source, /data-governance-refresh/);
    for (const kind of ['naming', 'tagging', 'duplicate_files', 'duplicate_shots', 'vector_duplicates', 'similar_names']) {
        assert.ok(
            source.includes(`'${kind}`) || source.includes(`${kind}:`),
            `缺少建议类别 ${kind} 的呈现逻辑`,
        );
    }
});

test('similar-name entries are informational and never auto-confirmable', () => {
    assert.match(source, /GOVERNANCE_INFORMATIONAL_KINDS = new Set\(\['similar_names'\]\)/);
    assert.match(source, /仅提示，不提供写入动作/);
});

test('view declares list semantics and an explicit empty state', () => {
    assert.match(source, /role="list" aria-label="治理建议清单"/);
    assert.match(source, /data-governance-empty/);
    assert.match(source, /暂无治理建议/);
});

test('degraded index state is surfaced instead of silently hiding duplicates', () => {
    assert.match(source, /data-governance-degraded/);
    assert.match(source, /查重候选可能不完整/);
});

test('no source file mutation or deletion wording enters the view', () => {
    // 治理视图只允许“确认写入/登记”语义，不得出现删除或改源文件动作。
    const viewStart = source.indexOf('MVP1-T5 治理建议清单视图');
    assert.ok(viewStart > 0, '缺少治理视图代码段');
    const viewEnd = source.indexOf('function render(){', viewStart);
    const view = source.slice(viewStart, viewEnd);
    assert.doesNotMatch(view, /(delete|remove|unlink|rename)\w*\(/i);
    assert.match(view, /系统永不自动删除/);
});
