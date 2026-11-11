// [QIN-586] 资产库侧栏统一为资源库树并显示递归素材计数自动化测试
import assert from 'node:assert/strict';
import { readAssetManagerAggregate } from './asset-manager-aggregate.mjs';
import { readFileSync } from 'node:fs';
import test from 'node:test';

const html = readFileSync(new URL('../../web/pages/assets.html', import.meta.url), 'utf8');
const controller = readFileSync(new URL('../../web/js/controllers/assets-controller.js', import.meta.url), 'utf8');
const manager = readAssetManagerAggregate();

test('AC1: 结构统一为一棵资源库树，去掉「资源入口」与「挂载文件夹」分段', () => {
  // 1. 顶部总览卡与新建资源库按钮
  assert.match(html, /id="assetSidebarOverview"[^>]*title="资产总览"/);
  assert.match(html, /id="assetSidebarCreate"[^>]*title="新建资源库"/);

  // 2. 收集箱与挂载资源单一树分组
  assert.match(controller, /name:\s*'收集箱'/);
  assert.match(controller, /mine:inbox/);
  assert.match(controller, /<span>挂载资源<\/span>/);

  // 3. 不再有独立的「资源入口」分段
  assert.doesNotMatch(controller, /boardSection\.className = 'asset-shell-board-section mt-2'/);
  assert.doesNotMatch(controller, /boardLabel\.textContent = '资源入口'/);

  // 4. 同一物理来源看板与根去重：如果看板关联了物理根或路径相同，不重复显示
  assert.match(controller, /const independentBoards = rawBoards\.filter/);
  assert.match(manager, /userBoards\.forEach\(board =>/);
  assert.match(manager, /rootBoardMap\.set\(matchedRoot\.id, board\)/);
});

test('AC1 行为逻辑: 同一物理来源（相同物理路径）去重合并，不再并排输出多行', () => {
  // 验证 assetTreeGlobalRootsList 具备路径物理去重与有效素材计数继承逻辑
  assert.match(manager, /function assetTreeResolveRootPath\(rootId, rootName=''\)/);
  assert.match(manager, /const pathKey = resolvedPath \? pathCompareKey\(resolvedPath\) : '';/);
  assert.match(manager, /if\(pathKey && seenPaths\.has\(pathKey\)\)\{/);
  assert.match(manager, /existing\.count = Number\(count\);/);

  // 验证 gwAssetTreeSnapshot 中通过 boardPathKey 匹配物理来源合并看板与物理根
  assert.match(manager, /const boardPathKey = boardPath \? pathCompareKey\(boardPath\) : '';/);
  assert.match(manager, /matchedRoot = globalRoots\.find\(r => r\.path && pathCompareKey\(r\.path\) === boardPathKey\);/);
});

test('AC1 实体验证: 模拟 QA 场景（无 path 的 facets 根与 hash 允许根通过别名/路径完全合并为单一资源库行）', () => {
  // 模拟从 manager 中提取的核心合并逻辑（无 DOM 依赖执行验证）
  const pathCompareKey = (p) => String(p || '').replace(/\\/g, '/').replace(/\/+$/g, '').toLowerCase();

  const localLibraries = [];
  const storageSettingsState = {
    extraLocalEntries: [
      { id: 'local_extra_mwam46l2_grg8mw', name: '多层夹具·丙素材', path: '/tmp/qin586-fixtures/lib-gamma' }
    ],
    localLibraries: []
  };
  const allowedRootSettingsState = {
    roots: [
      { id: 'root_99bff3918db7af37b8e15ee1', path: '/tmp/qin586-fixtures/lib-gamma', active: true, aliases: ['local_extra_mwam46l2_grg8mw'] }
    ]
  };
  const assetAllowedRootPaths = new Map([
    ['root_99bff3918db7af37b8e15ee1', '/tmp/qin586-fixtures/lib-gamma'],
    ['local_extra_mwam46l2_grg8mw', '/tmp/qin586-fixtures/lib-gamma'],
  ]);
  const userBoards = [
    {
      id: 'library_preset_123',
      name: 'lib-gamma',
      sources: [{ id: 'root_99bff3918db7af37b8e15ee1', name: 'lib-gamma素材', path: '/tmp/qin586-fixtures/lib-gamma' }]
    }
  ];
  const registryFacets = {
    roots: [
      { id: 'local_extra_mwam46l2_grg8mw', name: '多层夹具·丙素材', count: 1, availability: 'available' }
    ]
  };

  const resolvePath = (id, name = '') => {
    const fromExtra = storageSettingsState.extraLocalEntries.find(e => e.id === id);
    if (fromExtra?.path) return fromExtra.path;
    const fromAllowed = allowedRootSettingsState.roots.find(r => r.id === id || (Array.isArray(r.aliases) && r.aliases.includes(id)));
    if (fromAllowed?.path) return fromAllowed.path;
    if (assetAllowedRootPaths.has(id)) return assetAllowedRootPaths.get(id);
    return '';
  };

  // 模拟执行 assetTreeGlobalRootsList
  const rawRoots = [];
  const seenPaths = new Map();
  const addRoot = (id, name, count = null, availability = '', path = '') => {
    const cleanId = String(id || '').trim();
    const cleanName = String(name || '').trim();
    const resolvedPath = path || resolvePath(cleanId, cleanName);
    const pathKey = resolvedPath ? pathCompareKey(resolvedPath) : '';
    if (pathKey && seenPaths.has(pathKey)) {
      const existing = rawRoots[seenPaths.get(pathKey)];
      if ((existing.count == null || existing.count === 0) && count != null && count > 0) {
        existing.count = Number(count);
      }
      if (cleanName && (!existing.name || existing.name.endsWith('素材') || existing.name.startsWith('local_extra_'))) {
        existing.name = cleanName;
      }
      return;
    }
    seenPaths.set(pathKey, rawRoots.length);
    rawRoots.push({ id: cleanId, name: cleanName, count, path: resolvedPath });
  };

  registryFacets.roots.forEach(r => addRoot(r.id, r.name, r.count, r.availability, ''));
  userBoards.forEach(b => (b.sources || []).forEach(s => addRoot(s.id, s.name, null, '', s.path || '')));

  // 1. 验证 rawRoots 只有 1 个根，没有并排双行
  assert.equal(rawRoots.length, 1, '同一物理路径的 facets 根与 hash 根必须合并为单一根');
  assert.equal(rawRoots[0].count, 1, '合并后的节点必须保留非 0 素材计数 1');
  assert.equal(rawRoots[0].path, '/tmp/qin586-fixtures/lib-gamma');

  // 模拟执行看板映射
  const mappedBoardIds = new Set();
  const rootBoardMap = new Map();
  userBoards.forEach(b => {
    const bPath = b.sources[0]?.path || '';
    const bPathKey = pathCompareKey(bPath);
    const matchedRoot = rawRoots.find(r => r.path && pathCompareKey(r.path) === bPathKey);
    if (matchedRoot) {
      mappedBoardIds.add(b.id);
      rootBoardMap.set(matchedRoot.id, b);
    }
  });

  assert.equal(mappedBoardIds.has('library_preset_123'), true, '同路径看板必须标记为已映射物理根');
  const matchedBoard = rootBoardMap.get(rawRoots[0].id);
  const displayName = matchedBoard ? matchedBoard.name : rawRoots[0].name;
  assert.equal(displayName, 'lib-gamma', '最终展示名称必须使用用户新建的资源库名称');
});

test('AC2: 资产总览清除库/文件夹筛选回到首页，深层目录选择状态持久化支持', () => {
  // 激活 registry 时清除目录与来源筛选
  assert.match(manager, /if\(board\.id === 'registry'\)\{\s*\n\s*clearAssetRackFolder\(\);\s*\n\s*setRegistryNavFolder\(\{rootId:'', path:''\}\);/);
  assert.match(manager, /setRegistryRootId\(''\);/);
  assert.match(manager, /setAssetTreeSelectedKey\('registry'\);/);

  // gwAssetTreeEntryKey 支持精准返回子目录深层路径 key，且在无筛选的 registry 下稳定返回 'registry'
  assert.match(manager, /return path && path !== '__root__' \? `mine:root:\$\{rootId\}::\$\{path\}` : `mine:root:\$\{rootId\}`;/);
  assert.match(manager, /return 'registry';/);

  // 新建资源库能力保持
  assert.match(controller, /openCreateAssetLibrary/);
  assert.match(manager, /function openCreateLibraryModal/);
});

test('AC3: 递归素材计数（含后代素材）取自 total_count，空目录为 0，未返回为加载态，离线分离', () => {
  // 1. 后端聚合 total_count 提取
  assert.match(manager, /function assetRackFolderTotalCount\(rootId, relPath\)/);
  assert.match(manager, /if\(found && found\.total_count != null\) return Number\(found\.total_count\);/);
  assert.match(manager, /return 0;\s*\n\}/);

  // 2. 加载态标记
  assert.match(manager, /const totalCount = assetRackFolderTotalCount/);
  assert.match(manager, /const loading = totalCount === null;/);

  // 3. 根节点使用全库分面计数，分面未返回时为加载态，不采用首屏列表截断长度
  assert.match(manager, /const hasFacet = Boolean\(facetRoot && facetRoot\.count != null\);/);
  assert.match(manager, /const loading = !hasFacet && root\.count == null;/);

  // 4. 外壳支持加载态显示与离线独立标记
  assert.match(controller, /row\.loading/);
  assert.match(controller, /asset-shell-nav-count is-loading/);
  assert.match(controller, /row\.offline/);
  assert.match(controller, /asset-shell-nav-offline/);
  assert.match(controller, /button\.insertBefore\(offline, countEl\)/);
});

test('AC4: 深层目录选择时父链逐级展开，刷新持久化与恢复可用', () => {
  // 1. expandAssetTreeAncestors 实现父链展开
  assert.match(manager, /function expandAssetTreeAncestors\(key\)\{/);
  assert.match(manager, /assetTreeExpanded\.add\(`mine:root:\$\{rootId\}`\);/);
  assert.match(manager, /assetTreeExpanded\.add\(`mine:root:\$\{rootId\}::\$\{sub\}`\);/);

  // 2. 状态保存与恢复持久化包含 expanded 状态
  assert.match(manager, /sessionStorage\.setItem\(ASSET_TREE_STATE_KEY, JSON\.stringify\(\{entry, expanded:Array\.from\(assetTreeExpanded\)\}\)\);/);
  assert.match(manager, /savedExpanded\.forEach\(k => assetTreeExpanded\.add\(k\)\);/);
  assert.match(manager, /expandAssetTreeAncestors\(entry\);/);

  // 3. 根节点 key 始终以稳定 rootId 绑定
  assert.match(manager, /const key = `mine:root:\$\{root\.id\}`;/);

  // 4. 独立未映射看板以 independentBoards 保留并在树中渲染
  assert.match(manager, /const independentBoards = userBoards\.filter\(b => !mappedBoardIds\.has\(b\.id\)\);/);
});

test('AC5: 新建、改名、拖拽移动与整理保护机制完整保留（#602/#667）', () => {
  // 物理根与子文件夹维持 mine:root: 前缀与 path 结构，使 canOrganize 完整生效
  assert.match(manager, /key = `mine:root:\$\{rootId\}::\$\{node\.path\}`/);
  assert.match(manager, /writable: assetOrganizeWritableRoot\(root\.id\)/);
  assert.match(manager, /draggable: assetOrganizeWritableRoot\(root\.id\)/);
  assert.match(controller, /organizeBridge\(\)\?\.canOrganize\(row\.key\)/);
  assert.match(controller, /beginShellRename\(row\.key\)/);
  assert.match(controller, /openShellOrganizeMenu\(row, e\.clientX, e\.clientY\)/);
  assert.match(controller, /bindShellDragEvents\(button, row\)/);
});

test('AC6: 资源库行与子文件夹行视觉约为 0.8 倍，特异性与 !important 覆盖全局设计系统，子树行距加大', () => {
  // 顶层资源库行高度与内边距缩至约 0.8 倍（原 42px -> 34px），覆盖 hardware-design-system 的 padding: 8px 12px !important
  assert.match(html, /\.asset-shell-nav-button \{[^}]*min-height:\s*34px !important;/);
  assert.match(html, /\.asset-shell-nav-button \{[^}]*padding:\s*4px 10px !important;/);

  // 子文件夹行高度与内边距缩至约 0.8 倍（原 40px/35px -> 28px）
  assert.match(html, /\.asset-shell-nav-button-sub \{[^}]*min-height:\s*28px !important;/);
  assert.match(html, /\.asset-shell-nav-button-sub \{[^}]*padding:\s*3px 10px !important;/);

  // 行距加大：子树 gap 由 4px 提到 8px
  assert.match(html, /\.asset-shell-nav-subtree \{[^}]*gap:\s*8px;/);
  assert.match(html, /\.asset-shell-mount-section \{ display: flex; flex-direction: column; gap: 8px; \}/);

  // 正文字号保持不缩，不用 transform
  assert.match(html, /\.asset-shell-nav-button \{[^}]*font-size:\s*var\(--gw-type-body\);/);
  assert.match(html, /\.asset-shell-nav-button \.asset-shell-nav-label \{[^}]*transform:\s*none !important;/);
});

test('QIN-594 AC4 & AC5: 二级文件夹行图标与文字缩小一档、统计数字增大50%、选中态对标「资产总量」', () => {
  // 1. 二级文件夹行图标与文字缩小一档（16px->14px, 14px->12px）
  assert.match(html, /#assetVaultSidebar\s*\.asset-shell-nav-button-sub\s*svg\s*\{[^}]*width:\s*14px\s*!important/);
  assert.match(html, /#assetVaultSidebar\s*\.asset-shell-nav-button-sub\s*svg\s*\{[^}]*height:\s*14px\s*!important/);
  assert.match(html, /#assetVaultSidebar\s*\.asset-shell-nav-button-sub\s*\{[^}]*font-size:\s*12px\s*!important/);
  assert.match(html, /#assetVaultSidebar\s*\.asset-shell-nav-button-sub\s*\.asset-shell-nav-label\s*\{[^}]*font-size:\s*12px\s*!important/);

  // 2. 右侧统计数字增大 50%（当前 11px -> 约 16.5px）
  assert.match(html, /#assetVaultSidebar\s*\.asset-shell-nav-button-sub\s*\.asset-shell-nav-count\s*\{[^}]*font-size:\s*16\.5px\s*!important/);

  // 3. 选中态：文字金色（#dfc384），边框与底色参照「资产总量」按钮选中态
  assert.match(html, /\.asset-shell-nav-button\.is-active\s*\{[^}]*color:\s*#dfc384\s*!important/);
  assert.match(html, /\.asset-shell-nav-button\.is-active\s*\{[^}]*border-color:\s*#dfc384\s*!important/);
  assert.match(html, /\.asset-shell-nav-button\.is-active\s*\{[^}]*background:\s*color-mix\(in srgb,\s*#dfc384 12%,\s*#151820\)\s*!important/);

  // 4. 同一时刻只有一个选中项：activateTreeEntry 同步更新 updateSidebarSelection 与 syncOverviewSelection
  assert.match(controller, /function activateTreeEntry\(key\)\s*\{[\s\S]*?updateSidebarSelection\(selectedId\);[\s\S]*?syncOverviewSelection\(selectedId\);/);
});
