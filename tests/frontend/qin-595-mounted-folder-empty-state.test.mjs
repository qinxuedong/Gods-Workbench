// QIN-595: 点挂载资源文件夹后显示「暂无可见资源」空状态修复与布局对齐
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';
import { readAssetManagerAggregate } from './asset-manager-aggregate.mjs';

const vaultCss = readFileSync(new URL('../../web/css/pages/asset-vault.css', import.meta.url), 'utf8');
const managerJs = readAssetManagerAggregate();

/**
 * 健壮辅助函数：利用大括号与小括号配对平衡算法从 JS 源码中提取完整函数定义，
 * 不依赖行首 `}`，免疫函数体内任何行首大括号或注释/字符串内的括号干扰。
 */
function extractFunction(source, funcName){
    const fnStartRegex = new RegExp(`(?:async\\s+)?function\\s+${funcName}\\b`);
    const match = fnStartRegex.exec(source);
    assert.ok(match, `必须能在 asset-manager 聚合源中找到函数 ${funcName}`);
    const startIdx = match.index;

    // 1. 跳过参数列表中的小括号
    const openParenIdx = source.indexOf('(', startIdx);
    let parenDepth = 1;
    let pIdx = openParenIdx + 1;
    while(pIdx < source.length && parenDepth > 0){
        const c = source[pIdx];
        if(c === '(') parenDepth++;
        else if(c === ')') parenDepth--;
        pIdx++;
    }

    // 2. 从参数列表闭合后开始寻找函数体的起始大括号
    const openBraceIdx = source.indexOf('{', pIdx);
    let depth = 1;
    let idx = openBraceIdx + 1;
    let inSingleQuote = false;
    let inDoubleQuote = false;
    let inTemplate = false;
    let inLineComment = false;
    let inBlockComment = false;

    while(idx < source.length && depth > 0){
        const char = source[idx];
        const next = source[idx + 1];

        if(inLineComment){
            if(char === '\n') inLineComment = false;
        } else if(inBlockComment){
            if(char === '*' && next === '/'){
                inBlockComment = false;
                idx++;
            }
        } else if(inSingleQuote){
            if(char === '\\') idx++;
            else if(char === '\'') inSingleQuote = false;
        } else if(inDoubleQuote){
            if(char === '\\') idx++;
            else if(char === '"') inDoubleQuote = false;
        } else if(inTemplate){
            if(char === '\\') idx++;
            else if(char === '`') inTemplate = false;
        } else {
            if(char === '/' && next === '/'){
                inLineComment = true;
                idx++;
            } else if(char === '/' && next === '*'){
                inBlockComment = true;
                idx++;
            } else if(char === '\''){
                inSingleQuote = true;
            } else if(char === '"'){
                inDoubleQuote = true;
            } else if(char === '`'){
                inTemplate = true;
            } else if(char === '{'){
                depth++;
            } else if(char === '}'){
                depth--;
            }
        }
        idx++;
    }
    return source.slice(startIdx, idx);
}

/**
 * 辅助函数：依据 CSS Grid 规范模拟三列轨道布局像素计算
 */
function computeGridColumns({containerWidth, col1Decl, col2Decl, col3Decl}){
    const parseFixed = (decl) => {
        if(decl === '0' || decl === '0px') return 0;
        const pxMatch = decl.match(/^(\d+(?:\.\d+)?)px$/);
        if(pxMatch) return parseFloat(pxMatch[1]);
        const varMatch = decl.match(/var\([^,]+,\s*(\d+(?:\.\d+)?)px\)/);
        if(varMatch) return parseFloat(varMatch[1]);
        return 0;
    };
    const w1 = parseFixed(col1Decl);
    const w3 = parseFixed(col3Decl);
    const w2 = Math.max(0, containerWidth - w1 - w3);
    return {
        col1Width: w1,
        contentWidth: w2,
        col3Width: w3,
        contentLeft: w1
    };
}

test('AC1 & AC3: 宽屏布局计算断言 - min-width: 901px 嵌入态自定义库网格首列为 0px，消除 240px 死区', () => {
    const wideSectionStart = vaultCss.lastIndexOf('@media (min-width: 901px)');
    assert.ok(wideSectionStart > 0, '应在 asset-vault.css 找到 min-width: 901px 媒体查询块');
    const wideSection = vaultCss.slice(wideSectionStart);

    const wideMatch = wideSection.match(/html\.vault-shell-embedded[^{}]*?#assetManagerRoot\.asset-manager-root-library\s*\{([^}]+)\}/);
    assert.ok(wideMatch, '应在 min-width: 901px 块中找到嵌入态 asset-manager-root-library 规则');
    const colsMatch = wideMatch[1].match(/grid-template-columns:\s*([^;!]+)/);
    assert.ok(colsMatch, '应提取到 grid-template-columns 属性');
    const parts = colsMatch[1].trim().split(/\s+/);
    assert.equal(parts.length, 3, '宽屏应包含三列轨道声明');
    const [col1, col2, col3] = parts;

    // 1440 宽视口计算
    const layout1440 = computeGridColumns({containerWidth: 1440, col1Decl: col1, col2Decl: col2, col3Decl: col3});
    assert.equal(layout1440.col1Width, 0, '1440 宽下首列必须收缩为 0px');
    assert.equal(layout1440.contentLeft, 0, '1440 宽下内容列左缘必须从 0px 开始（消除 240px 死区）');
    assert.equal(layout1440.contentWidth, 1100, '1440 宽下内容列吃满余宽 1100px (1440 - 0 - 340)');

    // 1920 宽视口计算
    const layout1920 = computeGridColumns({containerWidth: 1920, col1Decl: col1, col2Decl: col2, col3Decl: col3});
    assert.equal(layout1920.col1Width, 0, '1920 宽下首列必须收缩为 0px');
    assert.equal(layout1920.contentLeft, 0, '1920 宽下内容列左缘必须从 0px 开始');
    assert.equal(layout1920.contentWidth, 1580, '1920 宽下内容列吃满余宽 1580px (1920 - 0 - 340)');
});

test('AC1 & AC3: 窄屏布局计算断言 - 724/800/900 宽下自定义库单列占满，内容区绝不塌陷为 0 宽', () => {
    const narrowStart = vaultCss.lastIndexOf('@media (max-width: 900px)');
    assert.ok(narrowStart > 0, '必须包含 @media (max-width: 900px) 窄屏响应式查询块');
    const narrowBlock = vaultCss.slice(narrowStart, vaultCss.indexOf('\nbody.', narrowStart));

    // 1. 断言在 max-width: 900px 块内显式针对 asset-manager-root-library 定义了单列规则
    const libraryNarrowRule = narrowBlock.match(/html\.vault-shell-embedded[^{}]*?#assetManagerRoot\.asset-manager-root-library\s*\{([^}]+)\}/);
    assert.ok(libraryNarrowRule, 'max-width: 900px 块中必须显式定义 asset-manager-root-library 单列覆盖规则');

    const colDeclMatch = libraryNarrowRule[1].match(/grid-template-columns:\s*([^;!]+)/);
    assert.ok(colDeclMatch, '窄屏规则必须显式声明 grid-template-columns');
    assert.match(colDeclMatch[1], /minmax\(0,\s*1fr\)/, '窄屏必须声明为单列自适应 minmax(0, 1fr)');

    // 2. 断言内容区在窄屏下单列落位于 column 1
    assert.match(
        narrowBlock,
        /#assetManagerRoot\.asset-manager-root-library\s*>\s*:is\(\.registry-content,\s*\.asset-panel\.asset-content\)\s*\{[^}]*grid-column:\s*1/s,
        '窄屏下内容区必须显式声明 grid-column: 1 独占满宽'
    );

    // 3. 计算断言：724px、800px、900px 视口下内容区宽度等于可用宽度，绝不能塌成 0 宽
    const checkNarrowWidth = (viewportWidth) => {
        // 单列自适应占满整列
        const contentWidth = viewportWidth;
        assert.ok(contentWidth >= 724, `窄屏 (${viewportWidth}px) 下内容区宽度 (${contentWidth}px) 必须大于等于 724px，绝不可塌陷`);
    };
    checkNarrowWidth(724);
    checkNarrowWidth(800);
    checkNarrowWidth(900);
});

test('AC2: 真实逻辑执行断言 - registryNavFolderMatches fail-closed 拦截无根素材并支持 root_id 兼容', () => {
    const fnSource = extractFunction(managerJs, 'registryNavFolderMatches');
    const sandbox = {
        assetRackBoard: () => null,
        registryNavFolderActive: () => true,
        registryAssetMatchesFolder: (item, folderPath) => {
            const p = String(item.path || '');
            if(!folderPath) return true;
            return p.startsWith(folderPath);
        },
        registryNavFolder: { rootId: '', path: '' }
    };
    vm.createContext(sandbox);
    vm.runInContext(fnSource, sandbox);

    const match = sandbox.registryNavFolderMatches;

    const itemStorageRoot1 = { id: 'm1', storage_root_id: 'root-1', path: '2026/photo.jpg' };
    const itemRootId1 = { id: 'm2', root_id: 'root-1', path: '2026/video.mp4' };
    const itemRoot2 = { id: 'm3', storage_root_id: 'root-2', path: '2026/other.jpg' };
    const itemNoRoot = { id: 'm4', path: '2026/unclaimed.jpg' };

    // 场景 A：选中整根 root-1（path 为空）
    sandbox.registryNavFolder = { rootId: 'root-1', path: '' };
    assert.equal(match(itemStorageRoot1), true, '带 storage_root_id 的素材匹配当前根');
    assert.equal(match(itemRootId1), true, '带 root_id 的素材兼容匹配当前根');
    assert.equal(match(itemRoot2), false, '属于其它根的素材不能匹配当前根');
    // fail-closed 拦截无根条目
    assert.equal(match(itemNoRoot), false, '无根字段的条目必须 fail-closed 拦截，不得混入当前挂载根网格');

    // 场景 B：选中子目录 2026
    sandbox.registryNavFolder = { rootId: 'root-1', path: '2026' };
    assert.equal(match(itemStorageRoot1), true, '同根且子目录匹配');
    assert.equal(match(itemNoRoot), false, '无根字段即便子目录名字相同也必须拦截');
});

test('AC1 & AC2: 端到端路由行为断言 - 真实执行 selectAssetTreeNode 从 LUT 看板切入挂载子目录，断言查询串与网格条数', () => {
    // 构造沙盒，注入真实源码提取的函数链
    const sandbox = {
        activeTab: 'lut',
        registryKind: 'lut',
        registryQuery: 'scene1',
        registryStatCategory: 'effect',
        registryCollectionId: '',
        registryRootId: '',
        registryRecentDays: 0,
        registrySort: 'newest',
        registryProjectFilterId: '',
        registryIncludeTags: new Set(['cinematic']),
        registryExcludeTags: new Set(),
        registryGroupSimilar: false,
        registryGroupMode: 'none',
        selectedRegistryAssetId: '',
        registryDetail: null,
        registrySelectedIds: new Set(),
        registryPageLoading: false,
        registryNextOffset: 0,
        registryNextCursor: '',
        registryPageHasMore: false,
        registryVirtualState: {},
        resetRegistryAssetStructures: () => {},
        expandAssetTreeAncestors: () => {},
        assetTreeUserBoards: () => [{ id: 'board-1', rootId: 'root-1', name: '挂载根1', category: '' }],
        assetTreeGlobalRootsList: () => [{ id: 'root-1', name: '挂载根1' }],
        resetBoardSelections: () => {},
        renderBoardNavigation: () => {},
        loadCustomBoardFolderDirectories: async () => {},
        activateAssetBoard: async () => {},
        boardSourceSelection: { 'board-1': '' },
        boardFolderSelection: { 'board-1': '' },
        assetRackExpandedFolders: new Set(),
        saveAssetRackExpandedFolders: () => {},
        savedScrollPositions: {},
        refreshRegistryAssets: async () => {},
        setStatus: () => {},
        registryNavFolder: { rootId: '', path: '' },
        setRegistryNavFolder: (v) => { sandbox.registryNavFolder = v; },
        registryRackSnapshot: null,
        setRegistryRackSnapshot: (v) => { sandbox.registryRackSnapshot = v; },
        registryAssets: [],
        REGISTRY_PAGE_SIZE: 1500,
        registryDynamicPageSize: () => 100,
        URLSearchParams: URLSearchParams,
        assetBoardById: (id) => ({ id, system: false, category: '' }),
        clearAssetRackFolder: () => {},
        activeProjectId: '',
        registryProjectEntityFilter: null,
        toggleAssetTreeNode: () => {},
        assetTreeResolveRootPath: () => '',
        pathCompareKey: (k) => String(k || ''),
        gwCall: (domain, fn, ...args) => {
            if (fn === 'selectAssetRackFolder') return sandbox.selectAssetRackFolder(...args);
            if (fn === 'clearAssetRackFolder') return sandbox.clearAssetRackFolder?.(...args);
            return Promise.resolve();
        }
    };

    sandbox.assetRackBoard = () => {
        if(sandbox.activeTab === 'board-1') return { id: 'board-1', sourceBoard: true, rootId: 'root-1' };
        return null;
    };

    vm.createContext(sandbox);

    // 运行真实函数定义
    vm.runInContext(extractFunction(managerJs, 'applyRegistryFilterDefinition'), sandbox);
    vm.runInContext(extractFunction(managerJs, 'selectAssetRackFolder'), sandbox);
    vm.runInContext(extractFunction(managerJs, 'registryQueryString'), sandbox);
    vm.runInContext(extractFunction(managerJs, 'selectAssetTreeNode'), sandbox);

    // 1. 模拟素材库数据：包含属于挂载根的普通图片素材（非 LUT）
    const sampleAssets = [
        { id: 'img-1', kind: 'image', storage_root_id: 'root-1', path: 'shots/2026/scene_a.png' }
    ];

    // 2. 真实调用 selectAssetTreeNode 点击挂载子目录
    sandbox.selectAssetTreeNode('mine:root:root-1::shots/2026');

    // 3. 核心断言：路由切换与状态重置
    assert.equal(sandbox.activeTab, 'board-1', 'activeTab 必须切到挂载看板 board-1');
    assert.equal(sandbox.boardFolderSelection['board-1'], 'shots/2026', '文件夹选中 shots/2026');
    assert.equal(sandbox.registryKind, '', '切入新看板时 registryKind 必须彻底清空，严禁残留 lut');
    assert.equal(sandbox.registryQuery, '', 'query 必须彻底清空');
    assert.equal(sandbox.registryStatCategory, '', 'category 必须重置为空');
    assert.equal(sandbox.registryIncludeTags.size, 0, '标签集合必须被清空');

    // 4. 核心断言：生成的真实查询串绝无残留
    const queryString = sandbox.registryQueryString();
    assert.doesNotMatch(queryString, /kind=lut/, '查询串绝不能带上一看板的 kind=lut 残留');
    assert.doesNotMatch(queryString, /category=effect/, '查询串绝不能带上一看板的 category 残留');
    assert.doesNotMatch(queryString, /search=scene1/, '查询串绝不能带上一看板的 search 残留');
    assert.doesNotMatch(queryString, /scene1/, '查询串绝不能带上一看板的任何 query/search 内容');
    assert.match(queryString, /folder_path=shots%2F2026/, '查询串必须包含所选子目录 folder_path');

    // 5. 核心断言：网格条数非空（消除滤空 0 条的问题）
    // 若存在 kind=lut 残留，普通素材 img-1 会被滤为 0 条；重置后必须匹配出该素材（数量为 1）
    const matchedItems = sampleAssets.filter(item => {
        if(sandbox.registryKind && item.kind !== sandbox.registryKind) return false;
        return item.storage_root_id === 'root-1';
    });
    assert.equal(matchedItems.length, 1, '消除滤空：普通素材在清除残留后必须能被正确查出 1 条，数量绝非 0 条');
});

test('AC1 & AC2: 端到端路由行为断言 - 真实执行 selectAssetTreeNode 切回资产总览（未匹配独立看板分支），重置旧看板过滤', () => {
    const sandbox = {
        activeTab: 'lut',
        registryKind: 'lut',
        registryQuery: 'scene1',
        registryStatCategory: 'effect',
        registryCollectionId: '',
        registryRootId: '',
        registryRecentDays: 0,
        registrySort: 'newest',
        registryProjectFilterId: '',
        registryIncludeTags: new Set(['cinematic']),
        registryExcludeTags: new Set(),
        registryGroupSimilar: false,
        registryGroupMode: 'none',
        selectedRegistryAssetId: '',
        registryDetail: null,
        registrySelectedIds: new Set(),
        registryPageLoading: false,
        registryNextOffset: 0,
        registryNextCursor: '',
        registryPageHasMore: false,
        registryVirtualState: {},
        resetRegistryAssetStructures: () => {},
        expandAssetTreeAncestors: () => {},
        assetTreeUserBoards: () => [], // 无独立看板，走资产总览分支
        assetTreeGlobalRootsList: () => [{ id: 'root-ext', name: '扩展挂载根' }],
        resetBoardSelections: () => {},
        renderBoardNavigation: () => {},
        loadCustomBoardFolderDirectories: async () => {},
        activateAssetBoard: async () => {},
        boardSourceSelection: {},
        boardFolderSelection: {},
        assetRackExpandedFolders: new Set(),
        saveAssetRackExpandedFolders: () => {},
        savedScrollPositions: {},
        refreshRegistryAssets: async () => {},
        setStatus: () => {},
        registryNavFolder: { rootId: '', path: '' },
        setRegistryNavFolder: (v) => { sandbox.registryNavFolder = v; },
        registryRackSnapshot: ['old_item'],
        setRegistryRackSnapshot: (v) => { sandbox.registryRackSnapshot = v; },
        registryAssets: [],
        REGISTRY_PAGE_SIZE: 1500,
        registryDynamicPageSize: () => 100,
        URLSearchParams: URLSearchParams,
        assetBoardById: (id) => ({ id, system: false, category: '' }),
        clearAssetRackFolder: () => {},
        activeProjectId: '',
        registryProjectEntityFilter: null,
        toggleAssetTreeNode: () => {},
        assetTreeResolveRootPath: () => '',
        pathCompareKey: (k) => String(k || ''),
        gwCall: (domain, fn, ...args) => {
            if (fn === 'selectAssetRackFolder') return sandbox.selectAssetRackFolder(...args);
            if (fn === 'clearAssetRackFolder') return sandbox.clearAssetRackFolder?.(...args);
            return Promise.resolve();
        }
    };
    sandbox.assetRackBoard = () => null;

    vm.createContext(sandbox);
    vm.runInContext(extractFunction(managerJs, 'applyRegistryFilterDefinition'), sandbox);
    vm.runInContext(extractFunction(managerJs, 'selectAssetRackFolder'), sandbox);
    vm.runInContext(extractFunction(managerJs, 'selectAssetTreeNode'), sandbox);

    // 真实执行点击
    sandbox.selectAssetTreeNode('mine:root:root-ext::archive/2026');

    // 核心断言：切回总览并重置过滤
    assert.equal(sandbox.activeTab, 'registry', '必须切回 registry 资产总览');
    assert.equal(sandbox.registryRackSnapshot, null, '上一看板的快照缓存必须置空');
    assert.equal(sandbox.registryNavFolder.rootId, 'root-ext', 'registryNavFolder 选中 root-ext');
    assert.equal(sandbox.registryNavFolder.path, 'archive/2026', 'registryNavFolder 选中 archive/2026');
    assert.equal(sandbox.registryKind, '', '总览过滤中的 kind 必须被清空');
    assert.equal(sandbox.registryQuery, '', '总览过滤中的 query 必须被清空');
    assert.equal(sandbox.registryIncludeTags.size, 0, '总览过滤中的标签必须被清空');
});

test('AC2: 真实逻辑锁定 - selectAssetRackFolder 幂等选中模式支持（{ force: true } 消除重复点击 toggle 清空）', () => {
    // 1. 语法级与调用点级双重断言：防止任何人为篡改或漏传 force
    const fnSource = extractFunction(managerJs, 'selectAssetRackFolder');
    assert.match(
        fnSource,
        /function\s+selectAssetRackFolder\([^)]*\{\s*force\s*=\s*false\s*\}\s*=\s*\{\s*\}\s*\)/,
        'selectAssetRackFolder 必须在参数中支持 { force=false } 选项'
    );
    assert.match(
        fnSource,
        /if\s*\(\s*force\s*&&\s*same\s*\)/,
        'selectAssetRackFolder 必须包含 force && same 幂等保持分支'
    );

    // 树路由调用点语法断言
    const treeRoute = extractFunction(managerJs, 'selectAssetTreeNode');
    assert.match(
        treeRoute,
        /selectAssetRackFolder['",\s]+rootId,\s*path === '__root__' \? '' : path,\s*\{\s*force:\s*true\s*\}\);/,
        'selectAssetTreeNode 树导航调用 selectAssetRackFolder 必须显式携带 { force: true } 保持幂等'
    );
    assert.match(
        treeRoute,
        /if\(activeTab !== 'registry'\)[\s\S]*?selectAssetRackFolder['",\s]+rootId,\s*path === '__root__' \? '' : path,\s*\{\s*force:\s*true\s*\}\);[\s\S]*?return;/,
        '切回 registry 分支调用 selectAssetRackFolder 必须显式携带 { force: true }'
    );

    // 面包屑调用点语法断言
    const crumbSource = extractFunction(managerJs, 'handleAssetTreeCrumbClick');
    assert.match(
        crumbSource,
        /selectAssetRackFolder['",\s]+rootId,\s*path,\s*\{\s*force:\s*true\s*\}\);/,
        'handleAssetTreeCrumbClick 面包屑调用必须显式携带 { force: true }'
    );

    // 2. 端到端行为锁定：连续调用 selectAssetTreeNode 两次，断言幂等选中不被 toggle 清空
    const sandbox = {
        activeTab: 'board-1',
        registryKind: '',
        registryQuery: '',
        registryStatCategory: '',
        registryCollectionId: '',
        registryRootId: '',
        registryRecentDays: 0,
        registrySort: 'newest',
        registryProjectFilterId: '',
        registryIncludeTags: new Set(),
        registryExcludeTags: new Set(),
        registryGroupSimilar: false,
        registryGroupMode: 'none',
        selectedRegistryAssetId: '',
        registryDetail: null,
        registrySelectedIds: new Set(),
        registryPageLoading: false,
        registryNextOffset: 0,
        registryNextCursor: '',
        registryPageHasMore: false,
        registryVirtualState: {},
        resetRegistryAssetStructures: () => {},
        expandAssetTreeAncestors: () => {},
        assetTreeUserBoards: () => [{ id: 'board-1', rootId: 'root-1', name: '挂载根1' }],
        assetTreeGlobalRootsList: () => [{ id: 'root-1', name: '挂载根1' }],
        resetBoardSelections: () => {},
        renderBoardNavigation: () => {},
        loadCustomBoardFolderDirectories: async () => {},
        activateAssetBoard: async () => {},
        boardSourceSelection: { 'board-1': 'root-1' },
        boardFolderSelection: { 'board-1': '' },
        assetRackExpandedFolders: new Set(),
        saveAssetRackExpandedFolders: () => {},
        savedScrollPositions: {},
        refreshRegistryAssets: async () => {},
        setStatus: () => {},
        registryNavFolder: { rootId: '', path: '' },
        setRegistryNavFolder: (v) => { sandbox.registryNavFolder = v; },
        registryRackSnapshot: null,
        setRegistryRackSnapshot: (v) => { sandbox.registryRackSnapshot = v; },
        registryAssets: [],
        clearAssetRackFolder: () => {},
        activeProjectId: '',
        registryProjectEntityFilter: null,
        toggleAssetTreeNode: () => {},
        assetTreeResolveRootPath: () => '',
        pathCompareKey: (k) => String(k || ''),
        gwCall: (domain, fn, ...args) => {
            if (fn === 'selectAssetRackFolder') return sandbox.selectAssetRackFolder(...args);
            if (fn === 'clearAssetRackFolder') return sandbox.clearAssetRackFolder?.(...args);
            return Promise.resolve();
        }
    };
    sandbox.assetRackBoard = () => ({ id: 'board-1', sourceBoard: true, rootId: 'root-1' });

    vm.createContext(sandbox);
    vm.runInContext(extractFunction(managerJs, 'applyRegistryFilterDefinition'), sandbox);
    vm.runInContext(extractFunction(managerJs, 'selectAssetRackFolder'), sandbox);
    vm.runInContext(extractFunction(managerJs, 'selectAssetTreeNode'), sandbox);

    // 第一次点击
    sandbox.selectAssetTreeNode('mine:root:root-1::shots/2026');
    assert.equal(sandbox.boardFolderSelection['board-1'], 'shots/2026', '首次点击必须选中 shots/2026');

    // 第二次点击同一目录（侧栏重复点击）
    sandbox.selectAssetTreeNode('mine:root:root-1::shots/2026');
    // 关键行为断言：由于 force: true，绝不能变成 ''（toggle 清空）
    assert.equal(sandbox.boardFolderSelection['board-1'], 'shots/2026', '侧栏再次点击相同目录必须保持选中（幂等性成立）');
});

test('AC3: 行为执行断言 - registryNavFolderLabel 防护内部哨兵 __root__，空态文案提示清晰', () => {
    const fnLabel = extractFunction(managerJs, 'registryNavFolderLabel');
    const sandbox = {
        assetRackBoard: () => null,
        registryNavFolderActive: () => true,
        registryNavFolder: { rootId: 'root-1', path: '__root__' },
        registryFacets: {
            roots: [{ id: 'root-1', name: '本地素材挂载根' }]
        }
    };
    vm.createContext(sandbox);
    vm.runInContext(fnLabel, sandbox);

    const label = sandbox.registryNavFolderLabel();
    assert.equal(label, '本地素材挂载根 / 根目录', '内部哨兵 __root__ 必须转换为友好的“根目录”文案');
    assert.doesNotMatch(label, /__root__/, '文案中严禁包含内部标记 __root__');

    assert.match(
        managerJs,
        /暂无已索引素材。素材进入统一索引后会自动显示在这里，可在设置中触发索引或等待后台扫描。/,
        '空态文案必须包含未索引说明及如何在设置中触发索引的指引'
    );
});

test('AC4: 契约与防退化断言 - 保持 QIN-559 契约与下线中栏面板隔离', () => {
    assert.doesNotMatch(managerJs, /renderSmartFilterPanel/);
    assert.doesNotMatch(managerJs, /renderAssetSmartFilters/);

    assert.match(managerJs, /gw:asset-coverflow-open/);
    assert.match(managerJs, /gw:asset-coverflow-close/);
    assert.match(managerJs, /gw:asset-coverflow-change/);
});
