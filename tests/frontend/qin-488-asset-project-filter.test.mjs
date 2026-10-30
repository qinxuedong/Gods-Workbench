// QIN-488 (GitHub #489): 资产库默认全局统一金库并在智能筛选增加项目素材展开与过滤
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import test from 'node:test';
import {createContext, runInContext} from 'node:vm';

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

test('AC2-RealProjectItem: 真实后端 ProjectItem 结构（不含 asset_count）正确按关联素材统计', () => {
    const extractFn = (name) => {
        const needle = 'function ' + name;
        const start = js.indexOf(needle);
        assert.ok(start >= 0, `缺少函数 ${name}`);
        let depth = 0;
        let inBlock = false;
        let end = start;
        for (let i = start; i < js.length; i++) {
            if (js[i] === '{') {
                depth++;
                inBlock = true;
            } else if (js[i] === '}') {
                depth--;
                if (inBlock && depth === 0) {
                    end = i + 1;
                    break;
                }
            }
        }
        return js.slice(start, end);
    };

    // 真实后端 ProjectItem 结构（gw/projects_hub/models.py），严格不含 asset_count 字段
    const realProjects = [
        {project_id: 'p7', id: 'p7', name: '项目7', project_type: 'feature', stage: 'production', progress: 50, scenes: 0, shots: 0, version: 1, updated_at: '2026-10-30T12:00:00Z'},
        {project_id: 'p6', id: 'p6', name: '项目6', project_type: 'feature', stage: 'production', progress: 40, scenes: 0, shots: 0, version: 1, updated_at: '2026-10-29T12:00:00Z'},
        {project_id: 'p5', id: 'p5', name: '项目5', project_type: 'feature', stage: 'production', progress: 30, scenes: 0, shots: 0, version: 1, updated_at: '2026-10-28T12:00:00Z'},
        {project_id: 'p4', id: 'p4', name: '项目4', project_type: 'feature', stage: 'production', progress: 20, scenes: 0, shots: 0, version: 1, updated_at: '2026-10-27T12:00:00Z'},
        {project_id: 'p3', id: 'p3', name: '项目3', project_type: 'feature', stage: 'production', progress: 10, scenes: 0, shots: 0, version: 1, updated_at: '2026-10-26T12:00:00Z'},
        {project_id: 'p2', id: 'p2', name: '项目2', project_type: 'feature', stage: 'production', progress: 5, scenes: 0, shots: 0, version: 1, updated_at: '2026-10-25T12:00:00Z'},
        {project_id: 'p1', id: 'p1', name: '项目1', project_type: 'feature', stage: 'production', progress: 0, scenes: 0, shots: 0, version: 1, updated_at: '2026-10-24T12:00:00Z'},
    ];

    // 真实素材结构：项目7 关联 3 个素材，项目6 关联 1 个素材
    const realAssets = [
        {id: 'ast-1', asset_id: 'ast-1', project_id: 'p7'},
        {id: 'ast-2', asset_id: 'ast-2', project_id: 'p7'},
        {id: 'ast-3', asset_id: 'ast-3', project_ids: ['p7']},
        {id: 'ast-4', asset_id: 'ast-4', project_id: 'p6'},
    ];

    const context = createContext({
        projects: realProjects,
        registryAssets: realAssets,
        registryProjectFilterId: '',
        registryProjectFacetExpanded: true,
        projectBoard: null,
        registryProjectAssetCounts: new Map(),
        escapeHtml: str => String(str ?? ''),
        escapeAttr: str => String(str ?? ''),
    });

    runInContext(extractFn('updateRegistryProjectAssetCounts'), context);
    runInContext(extractFn('registryProjectAssetCount'), context);
    runInContext(extractFn('registryRecentProjects'), context);
    runInContext(extractFn('renderRegistryProjectFacetSection'), context);

    const html = runInContext('renderRegistryProjectFacetSection()', context);

    // 断言项目7 显示计数为 3
    assert.match(html, /data-registry-project-filter="p7"[\s\S]*?<em>3<\/em>/, '真实 ProjectItem 结构下项目7应统计为 3 个素材');
    // 断言项目6 显示计数为 1
    assert.match(html, /data-registry-project-filter="p6"[\s\S]*?<em>1<\/em>/, '真实 ProjectItem 结构下项目6应统计为 1 个素材');
    // 断言项目5 显示计数为 0
    assert.match(html, /data-registry-project-filter="p5"[\s\S]*?<em>0<\/em>/, '真实 ProjectItem 结构下无素材的项目应显示 0');
    // 断言按更新时间倒序只取前 5 个项目（p7 到 p3）
    assert.match(html, /data-registry-project-filter="p7"/);
    assert.match(html, /data-registry-project-filter="p3"/);
    assert.doesNotMatch(html, /data-registry-project-filter="p2"/);
    assert.doesNotMatch(html, /data-registry-project-filter="p1"/);
    // 断言渲染产物中收拢到样式表，无 style="..." 内联样式
    assert.doesNotMatch(html, /style="/, 'renderRegistryProjectFacetSection 渲染产物不得包含内联样式');
});

test('AC3: 包含项目筛选与点击事件处理、清除芯片及右侧联动', () => {
    assert.match(js, /target\.closest\?\.\('\[data-registry-project-toggle\]'\)/);
    assert.match(js, /target\.closest\?\.\('\[data-registry-project-filter\]'\)/);
    assert.match(js, /registryProjectFilterId = registryProjectFilterId === pid \? '' : pid;/);
    assert.match(js, /key\s*===\s*'project'/);
    assert.match(js, /chips\.push\(`<button type="button" data-registry-clear="project">项目：/);
});

test('AC4: 样式包含项目素材折叠按钮与项目项硬件激活样式，且无内联样式依赖', () => {
    assert.match(css, /\.registry-facet-projects\s*\.registry-project-toggle-btn/);
    assert.match(css, /\.registry-project-facet-item\.active\s*\{[^}]*border-color:\s*var\(--strong\)/);
    assert.match(css, /\.registry-project-toggle-label/);
    assert.match(css, /\.registry-project-item-name/);
    assert.match(css, /\.registry-project-item-icon/);
    assert.match(css, /\.registry-project-item-text/);
});
