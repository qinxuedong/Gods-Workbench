/**
 * QIN-584（GitHub #678）：资产库首屏按视口分页，先画第一页。
 *
 * 断言面覆盖票面 AC1～AC5：
 * - AC1 首屏请求的 limit 与 dynamic_limit 同源于视口动态上限（不少于 100、不多于 1500），
 *   不再默认 1500 整库下发；
 * - AC2 第一屏在那一页列表返回后立即渲染，不等结构 / 分面 / 状态 / 预设 / 项目列表；
 * - AC3 结构查询只针对已加载页的素材 id，且排在首屏绘制之后；
 * - AC4 翻页仍走同一分页口径，且不与首屏结构补齐抢序号；
 * - AC5 本测试登记 node-files.txt 与洁净室准入分片。
 */
import assert from 'node:assert/strict';
import {existsSync, readFileSync} from 'node:fs';
import test from 'node:test';

const MANAGER = readFileSync(
    new URL('../../web/js/modules/asset-manager.js', import.meta.url),
    'utf8'
);
const NODE_FILES = readFileSync(new URL('./node-files.txt', import.meta.url), 'utf8');
const SHARD_URL = new URL('../hygiene/fixtures/admissions/issue-678.json', import.meta.url);

function functionSource(name) {
    const asyncStart = MANAGER.indexOf(`async function ${name}(`);
    const start = asyncStart >= 0 ? asyncStart : MANAGER.indexOf(`function ${name}(`);
    assert.ok(start >= 0, `缺少函数：${name}`);
    // 参数表可能自带默认值（如 {offset=0, limit=null}），必须先跳过配对括号，
    // 否则会把参数里的 { 当成函数体起点，截出无法解析的半截源码。
    const paramsStart = MANAGER.indexOf('(', start);
    let paramsDepth = 0;
    let cursor = paramsStart;
    for (; cursor < MANAGER.length; cursor += 1) {
        if (MANAGER[cursor] === '(') paramsDepth += 1;
        else if (MANAGER[cursor] === ')') {
            paramsDepth -= 1;
            if (paramsDepth === 0) break;
        }
    }
    const bodyStart = MANAGER.indexOf('{', cursor);
    let depth = 0;
    for (let index = bodyStart; index < MANAGER.length; index += 1) {
        if (MANAGER[index] === '{') depth += 1;
        else if (MANAGER[index] === '}') {
            depth -= 1;
            if (depth === 0) return MANAGER.slice(start, index + 1);
        }
    }
    throw new Error(`函数未闭合：${name}`);
}

function buildQuery(params) {
    const source = functionSource('registryQueryString');
    return new Function(
        'URLSearchParams',
        'REGISTRY_PAGE_SIZE',
        'registryDynamicPageSize',
        `
        const registrySort = 'newest', registryProjectFilterId = '', registryQuery = '', registryKind = '';
        const registryStatCategory = '', registryCollectionId = '', registryRootId = '', registryRecentDays = 0;
        const registryIncludeTags = new Set(), registryExcludeTags = new Set();
        const activeTab = 'registry', registryNavFolder = {}, boardFolderSelection = {}, boardSourceSelection = {};
        const assetBoardById = () => null;
        ${source}
        return registryQueryString;
        `
    )(URLSearchParams, 1500, () => params.dynamicPageSize);
}

test('AC1: 首屏查询的 limit 与 dynamic_limit 同源于视口动态上限', () => {
    const query = buildQuery({dynamicPageSize: 120});
    const only = new URLSearchParams(query());
    assert.equal(only.get('limit'), '120', '首屏不得再默认 limit=1500 整库下发');
    assert.equal(only.get('dynamic_limit'), '120', '两端上限必须同源，避免各说各话');

    const paged = new URLSearchParams(query({offset: 240}));
    assert.equal(paged.get('limit'), '120', '后续页沿用同一页大小');
    assert.equal(paged.get('offset'), '240');
});

test('AC1: 视口动态上限夹在 100～1500 之间', () => {
    const source = functionSource('registryDynamicPageSize');
    const build = (width, height) => new Function(
        'root',
        'window',
        'registryCardSize',
        `${source}; return registryDynamicPageSize;`
    )(
        {querySelector: () => ({clientWidth: width, clientHeight: height})},
        {innerWidth: width, innerHeight: height},
        176
    )();

    assert.equal(build(400, 200), 100, '窄视口取下限 100，保证一屏有卡可看');
    assert.equal(build(8000, 8000), 1500, '超宽视口不越过 1500 的既有上限');
    const mid = build(1280, 800);
    assert.ok(mid >= 100 && mid <= 1500, `常规视口必须落在 100～1500，实际 ${mid}`);
});

test('AC2: 首屏那一页返回后立即渲染，不等结构与次要数据', () => {
    const bootstrap = functionSource('loadRegistryBootstrap');
    const applied = bootstrap.indexOf('applyRegistryAssetResponse(assetData);');
    assert.ok(applied > 0, '引导必须先消费列表页');
    const firstRender = bootstrap.indexOf('render();', applied);
    assert.ok(firstRender > applied, '列表页到手必须立刻渲染第一屏');

    const structures = bootstrap.indexOf('refreshRegistryAssetStructures(');
    assert.ok(structures > firstRender, '结构补齐必须排在首屏渲染之后');
    for (const secondary of [
        'getRegistryFacets(',
        'getRegistryStatus(',
        'getRegistryPresets(',
        'getRegistryProjects(',
    ]) {
        const at = bootstrap.indexOf(secondary);
        assert.ok(at > firstRender, `${secondary} 必须排在首屏渲染之后`);
    }
    assert.match(
        bootstrap,
        /applyRegistryAssetResponse\(assetData\);[\s\S]*?render\(\);[\s\S]*?saveRegistrySnapshot\(\);/,
        '首屏快照要在第一屏画完后立刻沉淀'
    );
    assert.doesNotMatch(
        bootstrap,
        /applyRegistryAssetResponse\(assetData\);\s*await refreshRegistryAssetStructures/,
        '不得在首屏渲染前等待结构查询'
    );
});

test('AC3: 结构查询只针对已加载页的素材 id，并在首屏之后补', () => {
    const structures = functionSource('refreshRegistryAssetStructures');
    assert.match(
        structures,
        /assetIds\s*=\s*registryAssets\.filter\(isRegistryStructureImage\)\.map\(item => item\.id\)/,
        '结构查询默认范围必须限定在已加载页的素材 id'
    );
    assert.doesNotMatch(
        structures,
        /listAssetStructures\(\s*\)/,
        '结构查询不得退化为无参全库扫描'
    );
    const bootstrap = functionSource('loadRegistryBootstrap');
    assert.match(
        bootstrap,
        /const structureRequest = refreshRegistryAssetStructures\(\{requestId\}\);/,
        '首屏结构补齐只查已加载页，不叠加历史 id'
    );
    assert.match(
        bootstrap,
        /const structuresChanged = await structureRequest;[\s\S]*?if\(structuresChanged[\s\S]*?render\(\);/,
        '结构到达后若卡组构成变化，必须再同步一次表现'
    );
});

test('AC4: 翻页沿用同一分页口径，且不与首屏结构补齐抢请求序号', () => {
    const loadMore = functionSource('loadMoreRegistryAssets');
    assert.match(
        loadMore,
        /const queryKey = registryQueryString\(\{offset,cursor\}\);/,
        '翻页必须复用同一查询口径（同一 limit / dynamic_limit）'
    );
    assert.match(
        loadMore,
        /if\(registryStructureBootstrapPromise\)\{[\s\S]*?await registryStructureBootstrapPromise\.catch\(\(\) => \{\}\);[\s\S]*?if\(requestId !== registryRequestSeq\) return;/,
        '首屏结构补齐在飞时，翻页必须先等它落地，避免递增结构序号把首屏结果作废'
    );
    assert.match(
        MANAGER,
        /let registryStructureBootstrapPromise = null;/,
        '缺少首屏结构补齐句柄'
    );
});

test('AC5: 本票测试与洁净室分片已登记', () => {
    assert.match(
        NODE_FILES,
        /tests\/frontend\/qin-584-asset-first-page-viewport\.test\.mjs/,
        '新增前端测试必须登记 node-files.txt'
    );
    assert.ok(existsSync(SHARD_URL), '新增测试必须完成洁净室准入分片登记');
    const shard = JSON.parse(readFileSync(SHARD_URL, 'utf8'));
    assert.equal(shard.schema, 'gw.cleanroom-admission-shard/v1');
    assert.equal(shard.issue, 678);
    const accepted = (shard.accepted_test_files || []).map(entry => entry.path);
    assert.ok(
        accepted.includes('tests/frontend/qin-584-asset-first-page-viewport.test.mjs'),
        '新增前端测试必须在准入分片登记'
    );
});
