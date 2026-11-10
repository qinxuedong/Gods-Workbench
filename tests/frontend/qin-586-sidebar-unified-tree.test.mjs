// [QIN-586] 资产库侧栏统一为资源库树并显示递归素材计数自动化测试
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

const html = readFileSync(new URL('../../web/pages/assets.html', import.meta.url), 'utf8');
const controller = readFileSync(new URL('../../web/js/controllers/assets-controller.js', import.meta.url), 'utf8');
const manager = readFileSync(new URL('../../web/js/modules/asset-manager.js', import.meta.url), 'utf8');

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

  // 4. 同一物理来源看板与根去重：如果看板关联了物理根，不重复显示
  assert.match(controller, /const independentBoards = rawBoards\.filter/);
  assert.match(manager, /userBoards\.forEach\(board =>/);
  assert.match(manager, /rootBoardMap\.set\(rootId, board\)/);
});

test('AC2: 资产总览清除库/文件夹筛选回到首页，深层目录选择状态持久化支持', () => {
  // 激活 registry 时清除目录与来源筛选
  assert.match(manager, /if\(!value \|\| value === 'registry'\)\{\s*\n\s*clearAssetRackFolder\(\);\s*\n\s*registryNavFolder = \{rootId:'', path:''\};/);

  // gwAssetTreeEntryKey 支持精准返回子目录深层路径 key
  assert.match(manager, /return path && path !== '__root__' \? `mine:root:\$\{rootId\}::\$\{path\}` : `mine:root:\$\{rootId\}`;/);

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

test('AC4: 同名不同根不会混淆，无路径或多来源旧看板做显示兼容', () => {
  // 根节点 key 始终以稳定 rootId 绑定
  assert.match(manager, /const key = `mine:root:\$\{root\.id\}`;/);

  // 独立未映射看板以 independentBoards 保留并在树中渲染
  assert.match(manager, /const independentBoards = userBoards\.filter\(b => !mappedBoardIds\.has\(b\.id\)\);/);
  assert.match(controller, /independentBoards\.forEach\(board =>/);
  assert.match(controller, /key: `mine:board:\$\{board\.id\}`/);
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

test('AC6: 资源库行与子文件夹行视觉约为 0.8 倍，子树行距加大，正文字号不变', () => {
  // 顶层资源库行高度与内边距缩至约 0.8 倍（原 42px -> 34px）
  assert.match(html, /\.asset-shell-nav-button \{[^}]*min-height:\s*34px;/);
  assert.match(html, /\.asset-shell-nav-button \{[^}]*padding:\s*5px 10px;/);

  // 子文件夹行高度与内边距缩至约 0.8 倍（原 35px -> 28px）
  assert.match(html, /\.asset-shell-nav-button-sub \{[^}]*min-height:\s*28px;/);
  assert.match(html, /\.asset-shell-nav-button-sub \{[^}]*padding:\s*4px 10px;/);

  // 行距加大：子树 gap 由 4px 提到 8px
  assert.match(html, /\.asset-shell-nav-subtree \{[^}]*gap:\s*8px;/);
  assert.match(html, /\.asset-shell-mount-section \{ display: flex; flex-direction: column; gap: 8px; \}/);

  // 正文字号保持不缩，不用 transform
  assert.match(html, /\.asset-shell-nav-button \{[^}]*font-size:\s*var\(--gw-type-body\);/);
  assert.match(html, /\.asset-shell-nav-button \.asset-shell-nav-label \{[^}]*transform:\s*none !important;/);
});
