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

  const documentMock = {
    getElementById(id) {
      if (id === 'assetVaultSidebar') return sidebarElement;
      if (id === 'assetSidebarToggle') return toggleBtnElement;
      return null;
    },
  };

  const context = createContext({
    localStorage: localStorageMock,
    document: documentMock,
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

  // 样式规则验证：收起态宽为 0，内部树隐藏，金色竖线留在统计条左侧（top: 10px, left: 0），不侵入素材网格
  assert.match(html, /#assetVaultSidebar\.is-collapsed\s*\{[\s\S]*?width:\s*0\s*!important/);
  assert.match(html, /#assetVaultSidebar\.is-collapsed\s*\{[\s\S]*?border-right:\s*none\s*!important/);
  assert.match(html, /#assetVaultSidebar\.is-collapsed\s*\.gw-side-bus-list/);
  assert.match(html, /#assetVaultSidebar\.is-collapsed\s*#assetSidebarToggle\s*\{[\s\S]*?position:\s*absolute/);
  assert.match(html, /#assetVaultSidebar\.is-collapsed\s*#assetSidebarToggle\s*\{[\s\S]*?top:\s*10px/);
  assert.doesNotMatch(html, /#assetVaultSidebar\.is-collapsed\s*#assetSidebarToggle\s*\{[\s\S]*?top:\s*124px/, '折叠热区不得出现在 top: 124px 的素材网格或预览区内');
});

test('AC1: 侧栏展开/收起状态切换及刷新状态保持（localStorage 持久化）', () => {
  const env = createMockEnvironment();
  assert.equal(env.api.isSidebarCollapsed(), false, '初始状态为展开');

  // 手动收起
  env.api.toggleSidebarCollapse();
  assert.equal(env.api.isSidebarCollapsed(), true, '调用后切换为收起');
  assert.equal(env.sidebar.classList.contains('is-collapsed'), true);
  assert.equal(env.toggleBtn.getAttribute('aria-expanded'), 'false');
  assert.equal(env.toggleBtn.title, '展开侧栏');
  assert.equal(env.storage['gw:asset-sidebar-collapsed'], 'true', '收起态持久化至 localStorage');

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

test('QIN-594 AC3: 侧栏折叠/展开与容器宽度变化后通知重排，内嵌页支持 ResizeObserver 与 relayout 钩子', () => {
  // 1. controller 在折叠/展开时通知内嵌页重排
  assert.match(controller, /GWAssetVirtualGridHost(?:\?\.|\.)relayout\(\)/);
  assert.match(controller, /gw:asset-sidebar-collapsed/);

  // 2. manager 中使用 ResizeObserver 监听滚动容器宽度变化，并提供 GWAssetVirtualGridHost
  assert.match(manager, /new ResizeObserver\(\(entries\)\s*=>\s*\{[\s\S]*?updateRegistryVirtualWindow\(true\)/);
  assert.match(manager, /window\.GWAssetVirtualGridHost\s*=\s*Object\.freeze\(\{\s*relayout:\s*gwAssetVirtualGridRelayout\s*\}\)/);
  assert.match(manager, /window\.addEventListener\('gw:asset-sidebar-collapsed'/);
});
