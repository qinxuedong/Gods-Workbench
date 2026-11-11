/**
 * QIN-590: 资产库左侧栏增加展开收起，Coverflow 打开时自动收起铺满
 */
import assert from 'node:assert/strict';
import { readAssetManagerAggregate } from './asset-manager-aggregate.mjs';
import {readFileSync} from 'node:fs';
import test from 'node:test';
import {createContext, runInContext} from 'node:vm';

const html = readFileSync(new URL('../../web/pages/assets.html', import.meta.url), 'utf8');
const controller = readFileSync(new URL('../../web/js/controllers/assets-controller.js', import.meta.url), 'utf8');
const manager = readAssetManagerAggregate();

function extractControllerLogic(source = controller) {
  const parts = [];
  const keys = [
    'const SIDEBAR_COLLAPSED_KEY',
    'let sidebarCollapsed',
    'function isSidebarCollapsed',
    'function setSidebarCollapsed',
    'function toggleSidebarCollapse',
    'function initSidebarCollapseState',
    'function handleCoverflowOpen',
    'function handleCoverflowClose',
  ];
  for (const key of keys) {
    const idx = source.indexOf(key);
    assert.ok(idx >= 0, `缺少代码段: ${key}`);
  }
  const start = source.indexOf('// 【QIN-590】资产库左侧栏展开/收起与 Coverflow 自动收起铺满');
  const end = source.indexOf('// 【QIN-566 / QIN-590】内嵌页每次树状态与 Coverflow 变化调用宿主回调');
  assert.ok(start >= 0 && end > start, '缺少 QIN-590 核心状态机实现块');
  return source.slice(start, end);
}

function createMockEnvironment(initialStorage = {}) {
  const storage = {...initialStorage};
  const localStorageMock = {
    getItem(k) { return Object.prototype.hasOwnProperty.call(storage, k) ? storage[k] : null; },
    setItem(k, v) { storage[k] = String(v); },
    removeItem(k) { delete storage[k]; },
  };

  const classListSet = new Set();
  const sidebarElement = {
    classList: {
      toggle(cls, force) {
        if (force === undefined) {
          if (classListSet.has(cls)) classListSet.delete(cls);
          else classListSet.add(cls);
        } else if (force) {
          classListSet.add(cls);
        } else {
          classListSet.delete(cls);
        }
      },
      contains(cls) { return classListSet.has(cls); },
    },
  };

  const toggleAttributes = new Map();
  const toggleBtnElement = {
    title: '',
    setAttribute(k, v) { toggleAttributes.set(k, String(v)); },
    getAttribute(k) { return toggleAttributes.get(k) ?? null; },
    querySelector() { return null; },
  };

  let relayoutCalls = 0;
  const dispatchedEvents = [];
  const frameMock = {
    contentWindow: {
      GWAssetVirtualGridHost: {
        relayout() {
          relayoutCalls++;
        },
      },
      dispatchEvent(evt) {
        dispatchedEvents.push(evt);
      },
    },
  };

  const documentMock = {
    getElementById(id) {
      if (id === 'assetVaultSidebar') return sidebarElement;
      if (id === 'assetSidebarToggle') return toggleBtnElement;
      if (id === 'assetVaultFrame') return frameMock;
      return null;
    },
  };

  const context = createContext({
    localStorage: localStorageMock,
    document: documentMock,
    frame: frameMock,
    CustomEvent: globalThis.CustomEvent,
    lucide: undefined,
    setTimeout: (fn) => typeof fn === 'function' && fn(),
    clearTimeout: () => {},
    Boolean,
    String,
  });

  const code = extractControllerLogic(controller) + `
    this.api = {
      isSidebarCollapsed,
      setSidebarCollapsed,
      toggleSidebarCollapse,
      initSidebarCollapseState,
      handleCoverflowOpen,
      handleCoverflowClose,
    };
  `;

  runInContext(code, context);

  return {
    api: context.api,
    sidebar: sidebarElement,
    toggleBtn: toggleBtnElement,
    storage,
    get relayoutCalls() { return relayoutCalls; },
    dispatchedEvents,
  };
}

test('AC1: 侧栏收起由「资产总览」左侧金色竖线触发，不再出现独立大图标收起键且折叠定位在统计条左侧', () => {
  assert.match(html, /id="assetSidebarToggle"/, '左侧栏总览卡必须提供 #assetSidebarToggle 金色竖线触发器');
  assert.match(html, /onclick="WorkbenchAssets\.toggleSidebarCollapse\(\)"/);
  assert.match(html, /aria-expanded="true"/, '初始展开态 aria-expanded 应为 true');
  assert.match(html, /title="收起侧栏"/);
  assert.match(html, /aria-label="收起侧栏"/);
  // 【QIN-594 AC1】去掉侧栏顶部独立的显眼收起按钮（不再渲染独立 panel-left-close 大图标键）
  assert.doesNotMatch(html, /<i data-lucide="panel-left-close"/, '不再出现独立大图标收起键');

  // 样式规则验证：收起态宽为 0，右边框隐藏，内部树隐藏，折叠按钮悬浮可见，舞台铺满
  const collapsedSidebarRuleMatch = html.match(/#assetVaultSidebar\.is-collapsed\s*\{([^}]+)\}/);
  assert.ok(collapsedSidebarRuleMatch, '必须包含 #assetVaultSidebar.is-collapsed 规则块');
  const collapsedSidebarRule = collapsedSidebarRuleMatch[1];
  assert.match(collapsedSidebarRule, /width:\s*0\s*!important/, '折叠态侧栏宽度必须强制归零');
  assert.match(collapsedSidebarRule, /border-right:\s*none\s*!important/, '折叠态侧栏右边框必须隐藏以释放舞台空间');

  // 真实折叠宽度数值断言（防止被改回 256px 或其他展开宽度）
  const widthValMatch = collapsedSidebarRule.match(/width:\s*([^;!]+)/);
  const parsedWidth = widthValMatch ? parseInt(widthValMatch[1].trim(), 10) : NaN;
  assert.equal(parsedWidth, 0, '折叠态真实宽度必须严格等于 0px，不得保持 256px');

  // 内部树列表与导航元素隐藏断言
  const hiddenTreeRuleMatch = html.match(/#assetVaultSidebar\.is-collapsed\s+\.gw-side-bus-list[^\{]*\{([^}]+)\}/);
  assert.ok(hiddenTreeRuleMatch, '必须包含折叠态隐藏树列表 .gw-side-bus-list 的规则');
  assert.match(hiddenTreeRuleMatch[1], /display:\s*none\s*!important/, '折叠态内部树列表必须 display: none 隐藏');

  // 样式规则验证（限定在独立规则块内匹配，防止跨规则假绿）
  const toggleRuleMatch = html.match(/#assetVaultSidebar\.is-collapsed\s+#assetSidebarToggle\s*\{([^}]+)\}/);
  assert.ok(toggleRuleMatch, '必须包含 #assetVaultSidebar.is-collapsed #assetSidebarToggle 规则块');
  const toggleRule = toggleRuleMatch[1];
  assert.match(toggleRule, /position:\s*absolute/);
  assert.match(toggleRule, /top:\s*10px/);
  assert.match(toggleRule, /left:\s*0/);
  assert.match(toggleRule, /width:\s*(?:8px|[1-9]px|10px)/);
  assert.doesNotMatch(toggleRule, /top:\s*(?:70px|124px)/, '单个规则块内不得包含 top: 70px 或 124px');
  assert.doesNotMatch(toggleRule, /left:\s*(?:[1-9]\d*px)/, '单个规则块内 left 不得偏移漂移');

  // 验证含块与定位上下文（position: relative 且 overflow: visible）
  const overviewCardRule = html.match(/\.asset-shell-overview-card\s*\{([^}]+)\}/)?.[1] || '';
  assert.match(overviewCardRule, /position:\s*relative/, '折叠热区的含块必须具备 position: relative 定位上下文');
  const collapsedOverviewCardRule = html.match(/#assetVaultSidebar\.is-collapsed\s+\.asset-shell-overview-card\s*\{([^}]+)\}/)?.[1] || '';
  assert.match(collapsedOverviewCardRule, /overflow:\s*visible\s*!important/, '折叠态下含块不得溢出隐藏');

  // 折叠热区与「资产总量」统计卡及素材网格的矩形坐标几何关系断言
  const toggleTop = parseInt(toggleRule.match(/top:\s*(\d+)px/)?.[1] || '10', 10);
  const toggleLeft = parseInt(toggleRule.match(/left:\s*(\d+)px/)?.[1] || '0', 10);
  const toggleWidth = parseInt(toggleRule.match(/width:\s*(\d+)px/)?.[1] || '8', 10);
  const toggleHeight = parseInt(toggleRule.match(/height:\s*(\d+)px/)?.[1] || '36', 10);
  const toggleBottom = toggleTop + toggleHeight; // 46px
  const toggleRight = toggleLeft + toggleWidth;   // 8px

  const statBarMaxBottom = 60; // 统计条容器高度区间
  const statCardMinLeft = 8;   // 统计条第 1 张卡片「资产总量」内容起始左边距
  const assetGridTop = 100;    // 素材网格与预览区起始顶部（低于统计条与工具栏）

  assert.ok(toggleTop >= 0 && toggleBottom <= statBarMaxBottom, '折叠热区必须完全停留在顶部统计条垂直区间 (10px~46px) 内');
  assert.ok(toggleBottom < assetGridTop, `折叠热区底部 (${toggleBottom}px) 必须远在素材网格 (${assetGridTop}px) 上方，绝不侵入网格`);
  assert.ok(toggleWidth <= 10, '热区宽度必须收在 10px 以内');
  assert.ok(toggleRight <= statCardMinLeft, `热区右缘 (${toggleRight}px) 不得侵入「资产总量」卡片文字内容 (${statCardMinLeft}px)`);
});

test('AC1: 侧栏展开/收起状态切换及刷新状态保持（localStorage 持久化）', () => {
  const env = createMockEnvironment();
  assert.equal(env.api.isSidebarCollapsed(), false, '初始状态为展开');

  // 手动收起
  const callsBefore = env.relayoutCalls;
  env.api.toggleSidebarCollapse();
  assert.equal(env.api.isSidebarCollapsed(), true, '调用后切换为收起');
  assert.equal(env.sidebar.classList.contains('is-collapsed'), true);
  assert.equal(env.toggleBtn.getAttribute('aria-expanded'), 'false');
  assert.equal(env.toggleBtn.title, '展开侧栏');
  assert.equal(env.storage['gw:asset-sidebar-collapsed'], 'true', '收起态持久化至 localStorage');
  assert.ok(env.relayoutCalls > callsBefore, '切换收起状态必须实际调用 GWAssetVirtualGridHost.relayout 通知网格重排（防短路）');
  assert.ok(env.dispatchedEvents.some(e => e.type === 'gw:asset-sidebar-collapsed' && e.detail?.collapsed === true), '必须向内嵌页派发侧栏折叠事件');

  // 模拟页面刷新重新初始化
  const refreshedEnv = createMockEnvironment({ 'gw:asset-sidebar-collapsed': 'true' });
  refreshedEnv.api.initSidebarCollapseState();
  assert.equal(refreshedEnv.api.isSidebarCollapsed(), true, '刷新后从 localStorage 恢复收起状态');
  assert.equal(refreshedEnv.sidebar.classList.contains('is-collapsed'), true);
  assert.equal(refreshedEnv.toggleBtn.getAttribute('aria-expanded'), 'false');
});

test('AC2 & AC3: 打开全屏 Coverflow 自动收起侧栏，关闭后恢复打开前状态', () => {
  const env = createMockEnvironment();
  assert.equal(env.api.isSidebarCollapsed(), false, '初始展开');

  // 打开全屏 Coverflow
  env.api.handleCoverflowOpen({ mode: 'fullscreen' });
  assert.equal(env.api.isSidebarCollapsed(), true, '打开 Coverflow 后自动收起');
  assert.equal(env.sidebar.classList.contains('is-collapsed'), true);
  assert.equal(env.toggleBtn.getAttribute('aria-expanded'), 'false');

  // 自动收起不得污染持久化存储
  assert.equal(env.storage['gw:asset-sidebar-collapsed'], undefined, 'Coverflow 自动收起不污染 localStorage');

  // 关闭全屏 Coverflow
  env.api.handleCoverflowClose({ mode: 'fullscreen' });
  assert.equal(env.api.isSidebarCollapsed(), false, '关闭 Coverflow 后恢复打开前的展开状态');
  assert.equal(env.sidebar.classList.contains('is-collapsed'), false);
  assert.equal(env.toggleBtn.getAttribute('aria-expanded'), 'true');
});

test('AC3: 若打开 Coverflow 前侧栏原本就是收起的，关闭后继续保持收起', () => {
  const env = createMockEnvironment();
  // 用户在打开前主动收起
  env.api.setSidebarCollapsed(true, { manual: true });
  assert.equal(env.api.isSidebarCollapsed(), true);
  assert.equal(env.storage['gw:asset-sidebar-collapsed'], 'true');

  // 打开 Coverflow
  env.api.handleCoverflowOpen({ mode: 'fullscreen' });
  assert.equal(env.api.isSidebarCollapsed(), true);

  // 关闭 Coverflow
  env.api.handleCoverflowClose({ mode: 'fullscreen' });
  assert.equal(env.api.isSidebarCollapsed(), true, '恢复打开前的收起状态');
});

test('AC4: 若在 Coverflow 打开期间用户手动切换过侧栏，关闭时以用户选择为准，不再强行恢复', () => {
  const env = createMockEnvironment();
  assert.equal(env.api.isSidebarCollapsed(), false, '打开前展开');

  // 打开 Coverflow 触发自动收起
  env.api.handleCoverflowOpen({ mode: 'fullscreen' });
  assert.equal(env.api.isSidebarCollapsed(), true, '自动收起');

  // 用户在 Coverflow 打开期间点击了展开按钮
  env.api.toggleSidebarCollapse();
  assert.equal(env.api.isSidebarCollapsed(), false, '用户手动展开');
  assert.equal(env.storage['gw:asset-sidebar-collapsed'], 'false', '用户操作写入 localStorage');

  // 关闭 Coverflow
  env.api.handleCoverflowClose({ mode: 'fullscreen' });
  assert.equal(env.api.isSidebarCollapsed(), false, '以用户选择为准，保持展开状态，不强行恢复');
});

test('AC5: 嵌入式 Coverflow (mode: editor) 不影响父页侧栏收起或恢复', () => {
  const env = createMockEnvironment();
  assert.equal(env.api.isSidebarCollapsed(), false);

  env.api.handleCoverflowOpen({ mode: 'editor' });
  assert.equal(env.api.isSidebarCollapsed(), false, 'editor 模式不触发自动收起');

  env.api.handleCoverflowClose({ mode: 'editor' });
  assert.equal(env.api.isSidebarCollapsed(), false);
});

test('AC6: GWAssetTreeHost 同源桥接与内嵌页 Coverflow 事件钩子已接通', () => {
  assert.match(controller, /window\.GWAssetTreeHost = Object\.freeze\(\{[\s\S]*?onCoverflowOpen:\s*handleCoverflowOpen[\s\S]*?onCoverflowClose:\s*handleCoverflowClose[\s\S]*?\}\)/);
  assert.match(controller, /toggleSidebarCollapse/);
  assert.match(controller, /setSidebarCollapsed/);
  assert.match(controller, /isSidebarCollapsed/);

  // asset-manager.js 中的钩子派发
  assert.match(manager, /window\.addEventListener\('gw:asset-coverflow-open'[\s\S]*?window\.parent\?\.GWAssetTreeHost\?\.onCoverflowOpen\?\.\(event\.detail\)/);
  assert.match(manager, /window\.addEventListener\('gw:asset-coverflow-closed'[\s\S]*?window\.parent\?\.GWAssetTreeHost\?\.onCoverflowClose\?\.\(event\.detail\)/);
});

test('QIN-594 AC3: 侧栏折叠/展开与容器宽度变化后通知重排，虚拟网格列数自适应增加且右侧无空列', () => {
  // 1. controller 在折叠/展开时通知内嵌页重排并派发事件
  assert.match(controller, /GWAssetVirtualGridHost(?:\?\.|\.)relayout\(\)/);
  assert.match(controller, /gw:asset-sidebar-collapsed/);

  // 2. manager 中使用 ResizeObserver 监听滚动容器宽度变化，并提供 GWAssetVirtualGridHost
  assert.match(manager, /new ResizeObserver\(\(entries\)\s*=>\s*\{[\s\S]*?updateRegistryVirtualWindow\(true\)/);
  assert.match(manager, /window\.GWAssetVirtualGridHost\s*=\s*Object\.freeze\(\{\s*relayout:\s*gwAssetVirtualGridRelayout\s*\}\)/);
  assert.match(manager, /window\.addEventListener\('gw:asset-sidebar-collapsed'/);

  // 3. 验证 ResizeObserver 观察的是 .registry-assets-scroll（滚动容器）
  assert.match(manager, /const scrollRoot = grid\?\.closest\('\.registry-assets-scroll'\);/);
  assert.match(manager, /resizeObserver\.observe\(scrollRoot\);/);
  assert.doesNotMatch(manager, /resizeObserver\.observe\(grid\);/, '宽度观察必须监听滚动容器宽度，不能监听网格自身以避免无效或回环');

  // 4. 虚拟网格列数自适应重算与右侧无留白断言（模拟 registryVirtualLayout 中的列数核心算法）
  const cardPreferred = 176;
  const colGap = 10;
  const calculateCols = (w) => Math.max(1, Math.floor((w + colGap) / (cardPreferred + colGap)));

  // 展开态：1440px 视口扣减 256px 侧栏 = 1184px
  const widthExpanded = 1184;
  const colsExpanded = calculateCols(widthExpanded);
  assert.equal(colsExpanded, 6, '展开态 1184px 下应排 6 列');

  // 折叠态：侧栏归零，内容区拓宽至 1440px
  const widthCollapsed = 1440;
  const colsCollapsed = calculateCols(widthCollapsed);
  assert.equal(colsCollapsed, 7, '折叠后 1440px 宽度必须自适应增加到 7 列');
  assert.ok(colsCollapsed > colsExpanded, '折叠后网格必须加列');

  // 右侧留白断言：剩余宽度必须严格小于单列卡片占用宽度，证明网格自适应铺满无空列
  const remainingCollapsed = widthCollapsed - (colsCollapsed * cardPreferred + (colsCollapsed - 1) * colGap);
  assert.ok(remainingCollapsed < cardPreferred, '折叠后右侧空隙必须严格小于单卡宽度（绝无空列留白）');

  // 5. 行为断言：调用 setSidebarCollapsed 时必须实际触发 GWAssetVirtualGridHost.relayout（防 false && 短路）
  const env = createMockEnvironment();
  assert.equal(env.relayoutCalls, 0);
  env.api.setSidebarCollapsed(true);
  assert.ok(env.relayoutCalls > 0, '折叠时必须实际调用 GWAssetVirtualGridHost.relayout 通知网格重排');
  const callsAfterCollapse = env.relayoutCalls;
  env.api.setSidebarCollapsed(false);
  assert.ok(env.relayoutCalls > callsAfterCollapse, '展开时必须再次实际调用 GWAssetVirtualGridHost.relayout 通知网格重排');
});
