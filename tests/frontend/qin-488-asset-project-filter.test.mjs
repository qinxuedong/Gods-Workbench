// QIN-488 (GitHub #489): 资产库默认全局统一金库并在智能筛选增加项目素材展开与过滤
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import test from 'node:test';
import {createContext, runInContext} from 'node:vm';

const js = readFileSync(new URL('../../web/js/modules/asset-manager.js', import.meta.url), 'utf8');
const css = readFileSync(new URL('../../web/css/pages/asset-manager.css', import.meta.url), 'utf8');

const extractFn = (name) => {
    let needle = 'async function ' + name;
    let start = js.indexOf(needle);
    if (start < 0) {
        needle = 'function ' + name;
        start = js.indexOf(needle);
    }
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

test('AC2-Scenario-Paging: 分页场景：后端总数 150，前端首屏只加载 1 页（37 条），最近项目仍权威显示 150', async () => {
    const realProjects = [
        {project_id: 'p7', id: 'p7', name: '项目7', updated_at: '2026-10-30T12:00:00Z'},
        {project_id: 'p6', id: 'p6', name: '项目6', updated_at: '2026-10-29T12:00:00Z'},
        {project_id: 'p5', id: 'p5', name: '项目5', updated_at: '2026-10-28T12:00:00Z'},
        {project_id: 'p4', id: 'p4', name: '项目4', updated_at: '2026-10-27T12:00:00Z'},
        {project_id: 'p3', id: 'p3', name: '项目3', updated_at: '2026-10-26T12:00:00Z'},
    ];
    // 全局列表只加载了第一页（100 条中只有 37 条属于 p7）
    const page1Assets = Array.from({length: 37}, (_, i) => ({id: `ast-${i}`, project_id: 'p7'}));

    const context = createContext({
        projects: realProjects,
        registryAssets: page1Assets,
        registryProjectFilterId: '',
        registryProjectFacetExpanded: true,
        registryProjectAssetCounts: new Map(),
        registryProjectCountsLoading: false,
        escapeHtml: str => String(str ?? ''),
        escapeAttr: str => String(str ?? ''),
        render: () => {},
        apiJsonResponse: async (resPromise) => {
            const res = await resPromise;
            return typeof res.json === 'function' ? await res.json() : res;
        },
        assetManagerApi: {
            getRegistryAssets: async (query) => {
                // 后端真实总数：p7 有 150 条，p6 有 3 条
                if (query.includes('project_id=p7')) {
                    return {total: 150, assets: []};
                }
                if (query.includes('project_id=p6')) {
                    return {total: 3, assets: []};
                }
                return {total: 0, assets: []};
            },
        },
    });

    runInContext(extractFn('registryRecentProjects'), context);
    runInContext(extractFn('registryProjectAssetCount'), context);
    runInContext(extractFn('refreshRegistryProjectCounts'), context);
    runInContext(extractFn('renderRegistryProjectFacetSection'), context);

    await runInContext('refreshRegistryProjectCounts(false)', context);
    const html = runInContext('renderRegistryProjectFacetSection()', context);

    // 验证：显示权威总数 150，绝不能退化为已加载第一页的 37
    assert.match(html, /data-registry-project-filter="p7"[\s\S]*?<em>150<\/em>/, '分页场景下项目7应权威显示 150');
    assert.doesNotMatch(html, /data-registry-project-filter="p7"[\s\S]*?<em>37<\/em>/, '不可仅按已加载部分显示 37');
});

test('AC2-Scenario-Delete-Refresh: 删除后刷新场景：后端素材删减后，刷新项目素材计数准确回落（无只增不减缓存）', async () => {
    const realProjects = [
        {project_id: 'p7', id: 'p7', name: '项目7', updated_at: '2026-10-30T12:00:00Z'},
        {project_id: 'p6', id: 'p6', name: '项目6', updated_at: '2026-10-29T12:00:00Z'},
        {project_id: 'p5', id: 'p5', name: '项目5', updated_at: '2026-10-28T12:00:00Z'},
        {project_id: 'p4', id: 'p4', name: '项目4', updated_at: '2026-10-27T12:00:00Z'},
        {project_id: 'p3', id: 'p3', name: '项目3', updated_at: '2026-10-26T12:00:00Z'},
    ];

    let backendP7Count = 150;
    let backendP6Count = 3;

    const context = createContext({
        projects: realProjects,
        registryAssets: [],
        registryProjectFilterId: '',
        registryProjectFacetExpanded: true,
        registryProjectAssetCounts: new Map(),
        registryProjectCountsLoading: false,
        escapeHtml: str => String(str ?? ''),
        escapeAttr: str => String(str ?? ''),
        render: () => {},
        apiJsonResponse: async (resPromise) => {
            const res = await resPromise;
            return typeof res.json === 'function' ? await res.json() : res;
        },
        assetManagerApi: {
            getRegistryAssets: async (query) => {
                if (query.includes('project_id=p7')) {
                    return {total: backendP7Count, assets: []};
                }
                if (query.includes('project_id=p6')) {
                    return {total: backendP6Count, assets: []};
                }
                return {total: 0, assets: []};
            },
        },
    });

    runInContext(extractFn('registryRecentProjects'), context);
    runInContext(extractFn('registryProjectAssetCount'), context);
    runInContext(extractFn('refreshRegistryProjectCounts'), context);
    runInContext(extractFn('renderRegistryProjectFacetSection'), context);

    // 初始加载
    await runInContext('refreshRegistryProjectCounts(false)', context);
    const htmlBefore = runInContext('renderRegistryProjectFacetSection()', context);
    assert.match(htmlBefore, /data-registry-project-filter="p7"[\s\S]*?<em>150<\/em>/);
    assert.match(htmlBefore, /data-registry-project-filter="p6"[\s\S]*?<em>3<\/em>/);

    // 模拟后端删掉 p7 的 100 条（剩 50）和 p6 的 2 条（剩 1）
    backendP7Count = 50;
    backendP6Count = 1;

    // 触发刷新
    await runInContext('refreshRegistryProjectCounts(false)', context);
    const htmlAfter = runInContext('renderRegistryProjectFacetSection()', context);

    // 验证：计数准确递减回落至 50 和 1，绝无 Math.max 锁定
    assert.match(htmlAfter, /data-registry-project-filter="p7"[\s\S]*?<em>50<\/em>/, '删除后刷新，项目7计数应回落至 50');
    assert.match(htmlAfter, /data-registry-project-filter="p6"[\s\S]*?<em>1<\/em>/, '删除后刷新，项目6计数应回落至 1');
    assert.doesNotMatch(htmlAfter, /data-registry-project-filter="p7"[\s\S]*?<em>150<\/em>/, '删除后不能仍显示 150');
    assert.doesNotMatch(htmlAfter, /data-registry-project-filter="p6"[\s\S]*?<em>3<\/em>/, '删除后不能仍显示 3');
});

test('AC2-Scenario-RealStructure: 真实后端 ProjectItem 结构（不含 asset_count 字段），通过 limit=1 权威接口正确获取各项目实际总数', async () => {
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

    const context = createContext({
        projects: realProjects,
        registryAssets: [],
        registryProjectFilterId: '',
        registryProjectFacetExpanded: true,
        registryProjectAssetCounts: new Map(),
        registryProjectCountsLoading: false,
        escapeHtml: str => String(str ?? ''),
        escapeAttr: str => String(str ?? ''),
        render: () => {},
        apiJsonResponse: async (resPromise) => {
            const res = await resPromise;
            return typeof res.json === 'function' ? await res.json() : res;
        },
        assetManagerApi: {
            getRegistryAssets: async (query) => {
                if (query.includes('project_id=p7')) return {total: 88, assets: []};
                if (query.includes('project_id=p6')) return {total: 12, assets: []};
                if (query.includes('project_id=p5')) return {total: 5, assets: []};
                if (query.includes('project_id=p4')) return {total: 0, assets: []};
                if (query.includes('project_id=p3')) return {total: 1, assets: []};
                return {total: 0, assets: []};
            },
        },
    });

    runInContext(extractFn('registryRecentProjects'), context);
    runInContext(extractFn('registryProjectAssetCount'), context);
    runInContext(extractFn('refreshRegistryProjectCounts'), context);
    runInContext(extractFn('renderRegistryProjectFacetSection'), context);

    await runInContext('refreshRegistryProjectCounts(false)', context);
    const html = runInContext('renderRegistryProjectFacetSection()', context);

    // 断言项目7 显示计数为 88
    assert.match(html, /data-registry-project-filter="p7"[\s\S]*?<em>88<\/em>/, '真实 ProjectItem 结构下项目7应统计为 88 个素材');
    // 断言项目6 显示计数为 12
    assert.match(html, /data-registry-project-filter="p6"[\s\S]*?<em>12<\/em>/, '真实 ProjectItem 结构下项目6应统计为 12 个素材');
    // 断言项目4 显示计数为 0
    assert.match(html, /data-registry-project-filter="p4"[\s\S]*?<em>0<\/em>/, '真实 ProjectItem 结构下无素材的项目应显示 0');
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
