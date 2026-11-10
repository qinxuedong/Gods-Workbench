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

/**
 * 真正执行视口测量函数（不是源码断言）：用一个假的可变容器复现
 * `.registry-assets-scroll` 的真实行为——clientHeight / scrollHeight 随卡片一起长，
 * 但它在窗口里的可见区间不变。QA 复现的缺陷正是「读 clientHeight ⇒ 页大小随卡片数量膨胀」。
 */
function dynamicPageSizeHarness({width = 1280, viewportHeight = 800, top = 200, containerHeight = 24, cardSize = 176} = {}) {
    const state = {width, viewportHeight, top, containerHeight};
    const area = {
        get clientWidth() { return state.width; },
        get clientHeight() { return state.containerHeight; },
        get scrollHeight() { return state.containerHeight; },
        querySelector: () => null,
        getBoundingClientRect() {
            return {top: state.top, bottom: state.top + state.containerHeight, height: state.containerHeight};
        },
    };
    const built = new Function(
        'root',
        'window',
        'registryCardSize',
        'REGISTRY_PAGE_SIZE',
        `
        ${functionSource('registryVisibleViewportHeight')}
        ${functionSource('registryDynamicPageSize')}
        return {registryDynamicPageSize, registryVisibleViewportHeight};
        `
    )(
        {querySelector: () => area},
        {innerWidth: state.width, innerHeight: state.viewportHeight},
        cardSize,
        1500
    );
    const memo = {signature: '', size: 0};
    return {
        state,
        memo,
        // 逐页加载把容器撑高：clientHeight 与 scrollHeight 同步增长，卡片数量与视口高度无关。
        grow(height) { state.containerHeight = height; },
        size: () => built.registryDynamicPageSize(memo),
        visible: () => built.registryVisibleViewportHeight(area),
    };
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

test('AC1: 显式 limit 也顶不大 dynamic_limit', () => {
    const query = buildQuery({dynamicPageSize: 120});
    const forced = new URLSearchParams(query({limit: 1500}));
    assert.equal(forced.get('limit'), '1500', '显式 limit 仍按调用方声明下发');
    assert.equal(forced.get('dynamic_limit'), '120', 'dynamic_limit 永远不超过视口页大小');
});

test('AC1: 视口动态上限夹在 100～1500 之间', () => {
    const narrow = dynamicPageSizeHarness({width: 400, viewportHeight: 200, top: 120, containerHeight: 80});
    assert.equal(narrow.size(), 100, '窄视口取下限 100，保证一屏有卡可看');

    const huge = dynamicPageSizeHarness({width: 8000, viewportHeight: 8000, top: 0, containerHeight: 8000});
    assert.equal(huge.size(), 1500, '超宽视口不越过 1500 的既有上限');

    const mid = dynamicPageSizeHarness().size();
    assert.ok(mid >= 100 && mid <= 1500, `常规视口必须落在 100～1500，实际 ${mid}`);
});

test('AC1: 容器被已加载卡片撑高后，页大小不再膨胀到整库上限', () => {
    const coldStart = dynamicPageSizeHarness({width: 1144, viewportHeight: 784, top: 180, containerHeight: 24});
    const first = coldStart.size();
    assert.ok(first >= 100 && first < 1500, `首屏必须按视口收窄，实际 ${first}`);

    // QA 复现：预取与后续页把容器撑到 504 → 1500 → 整库高度（clientHeight === scrollHeight），
    // 此后用户搜索 / 排序 / 翻页发出的新查询都必须仍是同一个视口页大小。
    for (const grown of [504, 1500, 24000]) {
        const afterGrowth = dynamicPageSizeHarness({width: 1144, viewportHeight: 784, top: 180, containerHeight: grown});
        assert.equal(
            afterGrowth.size(),
            first,
            `容器已撑到 ${grown}px 时的新查询必须与首屏同一页大小（QA 实测此处曾抬到 1500）`
        );
    }
    // 同一会话内继续加载也不漂移
    coldStart.grow(24000);
    assert.equal(coldStart.size(), first, '同一会话内页大小不得随已加载卡片数量漂移');
    assert.ok(coldStart.size() < 1500, '预取 / 翻页 / 搜索重查询都不得回到整库上限');
});

test('AC1: 可见高度取自窗口视口，且页大小测量不再读容器 clientHeight', () => {
    const harness = dynamicPageSizeHarness({width: 1280, viewportHeight: 800, top: 200, containerHeight: 24});
    // 内容还没撑开：按「容器顶边到窗口底边」估，不读只有 24px 的容器自身高度
    assert.equal(harness.visible(), 600);
    harness.grow(24000);
    // 撑高后可见高度被窗口截断：800 - 200，而不是 24000
    assert.equal(harness.visible(), 600, '撑开前后必须得到同一个可见高度，页大小才不会跳变');

    const source = functionSource('registryVisibleViewportHeight') + functionSource('registryDynamicPageSize');
    assert.doesNotMatch(
        source,
        /clientHeight/,
        '页大小测量不得再读滚动容器的 clientHeight —— 它随已加载卡片增长'
    );
});

test('AC1: 页面级滚动布局下续页判据不再恒为真', () => {
    const boundary = (state) => new Function(
        'window',
        `${functionSource('registryReachedLoadMoreBoundary')}; return registryReachedLoadMoreBoundary;`
    )({innerHeight: 800})({
        scrollHeight: state.scrollHeight,
        clientHeight: state.clientHeight,
        scrollTop: state.scrollTop,
        querySelector: () => ({getBoundingClientRect: () => ({top: state.sentinelTop})}),
    }, state.lead);

    // 容器被卡片撑高：scrollTop 0 且 scrollHeight === clientHeight，旧判据恒真
    assert.equal(
        boundary({scrollHeight: 3604, clientHeight: 3604, scrollTop: 0, sentinelTop: 3600, lead: 1000}),
        false,
        '容器没有自身滚动条时，scrollTop 判据会把任意一次 scroll 事件都当成到底，续页直到整库'
    );
    assert.equal(
        boundary({scrollHeight: 3604, clientHeight: 3604, scrollTop: 0, sentinelTop: 1600, lead: 1000}),
        true,
        '哨兵进入提前量范围后必须继续续页'
    );

    // 自身可滚动的容器（虚拟列表 / 抽屉）仍按 scrollTop 口径
    assert.equal(
        boundary({scrollHeight: 4000, clientHeight: 600, scrollTop: 0, sentinelTop: 9999, lead: 1000}),
        false,
        '容器自身可滚动且离底部很远时不得续页'
    );
    assert.equal(
        boundary({scrollHeight: 4000, clientHeight: 600, scrollTop: 3400, sentinelTop: 9999, lead: 1000}),
        true,
        '容器自身滚到接近底部时必须续页'
    );
});

test('AC1: 页大小按窗口尺寸重算，不随已加载卡片数量漂移', () => {
    const session = dynamicPageSizeHarness({width: 1280, viewportHeight: 800, top: 200, containerHeight: 24});
    const before = session.size();
    session.grow(24000);
    assert.equal(session.size(), before);

    // 换一个窗口尺寸（同一 memo 会被签名变化作废）→ 重新按新视口计算
    const resized = dynamicPageSizeHarness({width: 2560, viewportHeight: 1440, top: 160, containerHeight: 24000});
    const after = resized.size();
    assert.ok(after >= before, `更宽的视口应给出不小于原视口的页大小：${before} → ${after}`);
    assert.ok(after < 1500, '重算结果仍受 1500 上限约束');
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

test('AC4: 页面级滚动也能驱动续页，且不会在别的页签误拉素材', () => {
    const fakeDocument = {documentElement: {id: 'html'}, body: {id: 'body'}};
    const build = (options) => {
        const area = {getBoundingClientRect: () => options.rect};
        return new Function(
            'root', 'window', 'document', 'activeTab', 'assetBoardById', 'registryLoading',
            `${functionSource('registryPageScrollTarget')}; return registryPageScrollTarget;`
        )(
            {querySelector: () => area},
            {innerHeight: options.viewport || 800},
            fakeDocument,
            options.tab || 'registry',
            () => (options.boardSystem === false ? {system: false} : {system: true}),
            Boolean(options.loading)
        );
    };

    const visibleRect = {top: 300, bottom: 900};
    assert.ok(
        build({rect: visibleRect})({target: fakeDocument}),
        '资产库激活且容器在窗口内时，文档级滚动必须能折算回容器，否则「继续下滑自动加载」整段失效'
    );
    assert.equal(
        build({rect: visibleRect, tab: 'projects'})({target: fakeDocument}),
        null,
        '非资产库页签的文档滚动不得触发拉取'
    );
    assert.equal(
        build({rect: visibleRect, loading: true})({target: fakeDocument}),
        null,
        '引导请求在飞时不再叠加续页'
    );
    assert.equal(
        build({rect: {top: -1200, bottom: -400}})({target: fakeDocument}),
        null,
        '容器完全滚出窗口后不再续页'
    );
    assert.equal(
        build({rect: visibleRect})({target: {closest: () => null}}),
        null,
        '只有文档级滚动才走折算，容器内滚动由原分支处理'
    );
    assert.match(
        MANAGER,
        /const registryScroll = directScroll \|\| registryPageScrollTarget\(event\);/,
        '滚动监听必须接上文档级折算'
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
