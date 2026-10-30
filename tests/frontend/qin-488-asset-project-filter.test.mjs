// QIN-488 (GitHub #489): 资产库默认全局统一金库并在智能筛选增加项目素材展开与过滤
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import test from 'node:test';

const js = readFileSync(new URL('../../web/js/modules/asset-manager.js', import.meta.url), 'utf8');
const css = readFileSync(new URL('../../web/css/pages/asset-manager.css', import.meta.url), 'utf8');

test('AC1: 资产总览查询与活动项目解耦，默认查询不带 project_id 呈现全局统一金库', () => {
    assert.match(js, /let registryProjectFilterId = '';/);
    assert.match(js, /if\(registryProjectFilterId\)\s*params\.set\('project_id',\s*registryProjectFilterId\);/);
    assert.doesNotMatch(js, /if\(activeProjectId\)\s*params\.set\('project_id',\s*activeProjectId\);/);
});

test('AC2: 智能筛选中包含「项目素材」折叠与最近5个项目提取能力', () => {
    assert.match(js, /function registryRecentProjects\(\)/);
    assert.match(js, /\.slice\(0,\s*5\)/);
    assert.match(js, /function renderRegistryProjectFacetSection\(\)/);
    assert.match(js, /data-registry-project-toggle/);
    assert.match(js, /<strong>项目素材<\/strong>/);
    assert.match(js, /data-registry-project-filter/);
});

test('AC3: 包含项目筛选与点击事件处理、清除芯片及右侧联动', () => {
    assert.match(js, /target\.closest\?\.\('\[data-registry-project-toggle\]'\)/);
    assert.match(js, /target\.closest\?\.\('\[data-registry-project-filter\]'\)/);
    assert.match(js, /registryProjectFilterId = registryProjectFilterId === pid \? '' : pid;/);
    assert.match(js, /key\s*===\s*'project'/);
    assert.match(js, /chips\.push\(`<button type="button" data-registry-clear="project">项目：/);
});

test('AC4: 样式包含项目素材折叠按钮与项目项硬件激活样式', () => {
    assert.match(css, /\.registry-facet-projects\s*\.registry-project-toggle-btn/);
    assert.match(css, /\.registry-project-facet-item\.active\s*\{[^}]*border-color:\s*var\(--strong\)/);
});
