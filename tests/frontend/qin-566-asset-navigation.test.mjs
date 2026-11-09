// QIN-566: 资产库导航收敛——恢复旧版外壳左栏为唯一导航，顶栏新增项目/剧本/提示词入口
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import test from 'node:test';

const html = readFileSync(new URL('../../web/pages/assets.html', import.meta.url), 'utf8');
const controller = readFileSync(new URL('../../web/js/controllers/assets-controller.js', import.meta.url), 'utf8');
const manager = readFileSync(new URL('../../web/js/modules/asset-manager.js', import.meta.url), 'utf8');
const vaultCss = readFileSync(new URL('../../web/css/pages/asset-vault.css', import.meta.url), 'utf8');
const managerCss = readFileSync(new URL('../../web/css/pages/asset-manager.css', import.meta.url), 'utf8');

test('AC1: 外壳恢复旧版左栏（资产总览标题卡 + 页头加号 + 黑金卡片树）', () => {
  assert.match(html, /id="assetVaultSidebar"/);
  assert.match(html, /id="assetSidebarOverview"/);
  assert.match(html, /id="assetSidebarCreate" class="hw-btn hw-btn-icon" onclick="WorkbenchAssets\.openCreateAssetLibrary\(\)"/);
  assert.match(html, /id="assetCategories"/);
  assert.match(html, /\.asset-shell-overview-card \{/);
  assert.match(html, /\.asset-shell-nav-button \{/);
  assert.match(html, /card-connector-pin/);
  assert.doesNotMatch(html, /id="assetSidebarCreate" class="hw-btn hw-btn-icon shrink-0"/);
});

test('AC1: 旧版外壳样式（总览卡、卡片按钮、分支针脚、窄屏横排）随外壳恢复', () => {
  for (const needle of ['.asset-shell-overview-card {', '.asset-shell-nav-button {', '#assetVaultSidebar .asset-shell-nav-button .card-connector-pin', '@media (max-width: 700px)']) {
    assert.ok(html.includes(needle), `旧版侧栏样式应存在：${needle}`);
  }
});

test('AC2: 左栏「用户资源」只含收集箱/用户资源库/挂载文件夹，系统入口不回归左栏', () => {
  const aside = html.slice(html.indexOf('id="assetVaultSidebar"'), html.indexOf('</aside>'));
  for (const label of ['项目制片', '剧本资产', '托管资产', '工作流', '画布资产', '上传资源', '提示词库']) {
    assert.ok(!aside.includes(label), `系统入口不得回到外壳左栏：${label}`);
  }
  assert.match(controller, /name:'收集箱'/);
  assert.match(controller, /mine:root:/);
  assert.match(controller, /mine:board:/);
  assert.match(controller, /用户资源/);
  assert.doesNotMatch(controller, /SYSTEM_NAVIGATION_GROUPS/);
});

test('AC3: 统计顶栏「其他」后依次排布「项目」「剧本」「提示词」「LUT」导航入口', () => {
  const stats = manager.slice(manager.indexOf('function renderRegistryOverviewStats'), manager.indexOf('function toggleRegistryFeature'));
  // 【QIN-580】LUT 从统计卡改为导航入口，紧跟「提示词」之后（QIN-578 已把三个导航入口整体挪到「其他」之后）。
  const projects = stats.indexOf("{id:'projects', name:'项目', nav:true");
  const scripts = stats.indexOf("{id:'scripts', name:'剧本', nav:true");
  const prompts = stats.indexOf("{id:'prompts', name:'提示词', nav:true");
  const lut = stats.indexOf("{id:'lut', name:'LUT', nav:true");
  const canvas = stats.indexOf("{id:'reference-canvas'");
  const other = stats.indexOf("{id:'other'");
  for (const pos of [projects, scripts, prompts, lut]) assert.ok(pos > 0, '四个导航入口应存在');
  assert.ok(canvas < other && other < projects && projects < scripts && scripts < prompts && prompts < lut, '入口顺序应为 画布 → 其他 → 项目 → 剧本 → 提示词 → LUT');
  assert.match(stats, /data-registry-nav=/);
  assert.match(stats, /is-nav/);
  const navTemplate = stats.slice(stats.indexOf('if(stat.nav)'), stats.indexOf('// 双行结构'));
  assert.ok(!navTemplate.includes('registry-stat-meter') && !navTemplate.includes('<strong>'), '导航入口不得输出计数/占比/进度条');
});

test('AC3: 顶栏导航入口点击激活对应系统看板并收口持久化', () => {
  const block = manager.slice(manager.indexOf("data-registry-nav]')){"), manager.indexOf('const registryStat ='));
  assert.match(block, /activateAssetBoard\(navBoard\)/);
  assert.match(block, /saveAssetTreeState\(\)/, '顶栏路径同样收口持久化（审核残留修复）');
});

test('AC4: 内嵌金库模式不再渲染重复树，内容区吃满余宽', () => {
  const panel = manager.slice(manager.indexOf('function renderAssetTreePanel'), manager.indexOf('function toggleAssetTreeNode'));
  assert.match(panel, /vault-shell-embedded.*return ''/s);
  assert.match(managerCss, /html\.vault-shell-embedded \.asset-manager-root\.asset-tree-layout { grid-template-columns:0 minmax\(0,1fr\); \}/);
  assert.match(managerCss, /> :is\(\.registry-overview-shell,\.registry-toolbar,\.asset-content\) \{ grid-column:2; \}/);
});

test('AC5: 树桥接同源直调（无新增 API），推送+持久化+恢复入口状态', () => {
  assert.match(manager, /window\.GWAssetTreeBridge = Object\.freeze\(\{snapshot:gwAssetTreeSnapshot, activate:gwAssetTreeActivate\}\)/);
  assert.match(manager, /notifyShellTreeSnapshot\(\)/);
  assert.match(manager, /gw\.asset\.tree\.state/);
  assert.match(manager, /restoreAssetTreeState\(\)/);
  assert.match(manager, /function gwAssetTreeActivate[\s\S]{0,400}saveAssetTreeState\(\);\s*\n\}/, '持久化必须收口在外壳激活入口（点击时落盘，bootstrap 渲染不触碰该键）');
  assert.match(manager, /loadAll\(\)\.then\(async \(\) => \{\s*\n\s*const restoredScroll = await restoreRegistryBrowseSession\(\);\s*\n\s*\/\/ 【QIN-566】/);
  assert.match(controller, /window\.GWAssetTreeHost = Object\.freeze\(\{onTreeSnapshot: applyTreeSnapshot\}\)/);
  const bridge = manager.slice(manager.indexOf('function gwAssetTreeEntryKey'), manager.indexOf('function toggleAssetTreeNode'));
  assert.ok(!bridge.includes('fetch('), '桥接不得新增后端请求');
});

test('AC3: 统计网格 13 卡同排（主卡 2fr + 12×.95fr，间距令牌 12）', () => {
  assert.match(vaultCss, /grid-template-columns: 2fr repeat\(12, \.95fr\);/);
  assert.match(vaultCss, /--registry-stat-gap-count: 12;/);
});

test('QIN-569 AC1: 引导失败错误条可恢复且不被同轮清除', () => {
  // loadAll catch 设置失败标记并渲染后必须 rethrow：catch 吞掉拒绝会让 promise 恒
  // fulfill，尾部的 bootstrapOk 成败区分退化为恒真，错误条仍会被同轮抹掉。
  assert.match(manager, /registryBootstrapError = err\.message \|\| '统一资产注册表加载失败';/);
  assert.match(manager, /throw err;\s*\n\s*\}\);/, 'catch 设置标记后必须 rethrow，把失败传给 loadAll 尾部的 bootstrapOk 成败区分');
  assert.match(manager, /const bootstrapOk = await registryBootstrap\.then\(\(\) => true, \(\) => false\);/);
  assert.match(manager, /if\(bootstrapOk && registryRequestId === registryRequestSeq\) registryBootstrapError = '';/);
  // registry 视图渲染可恢复错误条与重试入口；重试清标记后重走完整引导链路。
  assert.match(manager, /registryBootstrapError \? `<div class="registry-bootstrap-error" role="alert" data-registry-bootstrap-error><i data-lucide="alert-triangle"><\/i><span>统一资产注册表加载失败：\$\{escapeHtml\(registryBootstrapError\)\}<\/span><button class="hw-btn btn-size-sm" type="button" data-registry-bootstrap-retry>重试<\/button><\/div>` : ''/);
  assert.match(manager, /data-registry-bootstrap-retry/);
  assert.match(manager, /\[data-registry-bootstrap-retry\]'\)\)\{\s*\n\s*\/\/ 【QIN-569 AC1】首屏失败错误条的重试入口：清错误态并重新走完整引导链路。\s*\n\s*registryBootstrapError = '';/);
});
