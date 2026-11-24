/**
 * QIN-659 (#821) 左侧挂载文件夹树黑金视觉重构与根菜单「显示 / 隐藏空文件夹」前端测试
 */
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import vm from 'node:vm';

const ROOT = path.resolve(import.meta.dirname, '../..');
const readText = rel => fs.readFileSync(path.join(ROOT, rel), 'utf8');

const workspaceCss = readText('web/css/base/workspace-pages.css');
const managerCss = readText('web/css/pages/asset-manager.css');
const controllerSource = readText('web/js/controllers/assets-controller.js');

function createControllerSandbox(initialHideEmpty = null) {
  const storage = new Map();
  if (initialHideEmpty !== null) {
    storage.set('gw.asset.tree.hideEmptyFolders', initialHideEmpty);
  }
  const listeners = new Map();

  function makeNode(tagName = 'div') {
    const el = {
      tagName: String(tagName).toUpperCase(),
      id: '',
      className: '',
      textContent: '',
      title: '',
      type: '',
      disabled: false,
      style: {},
      dataset: {},
      attributes: {},
      children: [],
      listeners: {},
      classList: {
        add(...cls) {
          const set = new Set(el.className.split(/\s+/).filter(Boolean));
          cls.forEach(c => set.add(c));
          el.className = Array.from(set).join(' ');
        },
        remove(...cls) {
          const set = new Set(el.className.split(/\s+/).filter(Boolean));
          cls.forEach(c => set.delete(c));
          el.className = Array.from(set).join(' ');
        },
        toggle(c, force) {
          const set = new Set(el.className.split(/\s+/).filter(Boolean));
          const has = set.has(c);
          const next = force === undefined ? !has : Boolean(force);
          if (next) set.add(c);
          else set.delete(c);
          el.className = Array.from(set).join(' ');
          return next;
        },
        contains(c) {
          return el.className.split(/\s+/).filter(Boolean).includes(c);
        },
      },
      setAttribute(k, v) { el.attributes[k] = String(v); },
      getAttribute(k) { return el.attributes[k] ?? null; },
      removeAttribute(k) { delete el.attributes[k]; },
      append(...nodes) {
        nodes.filter(Boolean).forEach(n => { n.parentNode = el; el.children.push(n); });
      },
      appendChild(node) {
        if (node) { node.parentNode = el; el.children.push(node); }
        return node;
      },
      replaceChildren(...nodes) {
        el.children = nodes.filter(Boolean).map(n => { n.parentNode = el; return n; });
      },
      insertBefore(node, ref) {
        if (node) node.parentNode = el;
        const idx = el.children.indexOf(ref);
        if (idx >= 0) el.children.splice(idx, 0, node);
        else el.children.push(node);
        return node;
      },
      remove() {
        if (el.parentNode && Array.isArray(el.parentNode.children)) {
          el.parentNode.children = el.parentNode.children.filter(c => c !== el);
        }
      },
      contains(target) {
        if (target === el) return true;
        return el.children.some(child => typeof child?.contains === 'function' && child.contains(target));
      },
      addEventListener(type, fn) {
        el.listeners[type] = el.listeners[type] || [];
        el.listeners[type].push(fn);
      },
      removeEventListener(type, fn) {
        if (!el.listeners[type]) return;
        el.listeners[type] = el.listeners[type].filter(f => f !== fn);
      },
      closest() { return null; },
      focus() {},
      querySelector(selector) {
        return el.querySelectorAll(selector)[0] || null;
      },
      querySelectorAll(selector) {
        const out = [];
        const walk = (node) => {
          for (const child of node.children || []) {
            if (!child || typeof child !== 'object') continue;
            if (matchesSelector(child, selector)) out.push(child);
            walk(child);
          }
        };
        walk(el);
        return out;
      },
      set innerHTML(html) {
        el._innerHTML = String(html || '');
      },
      get innerHTML() {
        return el._innerHTML || '';
      },
    };
    return el;
  }

  function matchesSelector(node, selector) {
    if (selector.startsWith('#')) {
      return node.id === selector.slice(1);
    }
    const attrMatch = selector.match(/^\[data-asset-tree-entry="([^"]+)"\]$/);
    if (attrMatch) {
      return node.dataset?.assetTreeEntry === attrMatch[1].replace(/\\(.)/g, '$1');
    }
    if (selector.startsWith('.')) {
      const classes = selector.slice(1).split(/,\s*\./);
      return classes.some(c => node.classList?.contains(c));
    }
    if (selector.includes('[')) {
      const m = selector.match(/^\[([^\]]+)\]$/);
      if (m) return Object.hasOwn(node.attributes || {}, m[1]);
    }
    return false;
  }

  const categoriesEl = makeNode('div');
  categoriesEl.id = 'assetCategories';
  const bodyEl = makeNode('body');
  const frameEl = {
    dataset: {},
    src: '',
    contentWindow: {
      GWAssetOrganizeBridge: {
        canOrganize: () => true,
        canManageResourceFolders: () => true,
      },
    },
    addEventListener() {},
    removeEventListener() {},
  };
  const docEl = {
    body: bodyEl,
    activeElement: null,
    createElement: tag => makeNode(tag),
    createDocumentFragment: () => makeNode('fragment'),
    getElementById(id) {
      if (id === 'assetCategories') return categoriesEl;
      if (id === 'assetVaultFrame') return frameEl;
      if (id === 'assetOrganizeMenu') return bodyEl.children.find(c => c.id === 'assetOrganizeMenu') || null;
      return null;
    },
    addEventListener() {},
    removeEventListener() {},
  };

  const windowObj = {
    innerWidth: 1280,
    innerHeight: 800,
    location: { origin: 'http://localhost:2077', search: '' },
    localStorage: {
      getItem: k => (storage.has(k) ? storage.get(k) : null),
      setItem: (k, v) => { storage.set(k, String(v)); },
      removeItem: k => { storage.delete(k); },
    },
    addEventListener(type, fn) {
      listeners.set(type, [...(listeners.get(type) || []), fn]);
    },
    dispatchEvent(evt) {
      const fns = listeners.get(evt?.type) || [];
      fns.forEach(fn => fn(evt));
      return true;
    },
    lucide: { createIcons() {} },
  };
  windowObj.window = windowObj;

  const context = {
    window: windowObj,
    location: windowObj.location,
    document: docEl,
    CSS: { escape: s => String(s) },
    CustomEvent: class CustomEvent {
      constructor(type, init = {}) {
        this.type = type;
        this.detail = init.detail;
      }
    },
    URL,
    URLSearchParams,
    setTimeout: () => 0,
    clearTimeout: () => {},
    console,
  };
  vm.createContext(context);
  vm.runInContext(controllerSource, context);

  return { windowObj, docEl, categoriesEl, bodyEl, storage };
}

function collectButtons(node, out = []) {
  if (!node || typeof node !== 'object') return out;
  if (node.tagName === 'BUTTON' && node.dataset?.assetTreeEntry) {
    out.push(node);
  }
  for (const child of node.children || []) {
    collectButtons(child, out);
  }
  return out;
}

test('AC1: 挂载目录树样式去除常态亮框并建立三层黑金视觉权重', () => {
  assert.match(
    workspaceCss,
    /#assetVaultSidebar\s+#assetCategories\s+\.asset-shell-mount-section\s+\.asset-shell-nav-button[\s\S]*?border:\s*1px solid transparent !important;[\s\S]*?background:\s*transparent !important;/,
    '挂载目录树常态行应无亮框透明底',
  );
  assert.match(
    workspaceCss,
    /#assetVaultSidebar\s+#assetCategories\s+\.asset-shell-mount-section\s+\.asset-shell-nav-button:hover[\s\S]*?background:\s*rgba\(255,\s*255,\s*255,\s*0\.04\) !important;/,
    '悬停态应使用轻柔半透明底',
  );
  assert.match(
    workspaceCss,
    /#assetVaultSidebar\s+#assetCategories\s+\.asset-shell-mount-section\s+\.asset-shell-nav-button:is\(\.is-active,\s*\.active\)[\s\S]*?border:\s*1px solid rgba\(223,\s*195,\s*132,\s*0\.45\) !important;[\s\S]*?background:\s*rgba\(223,\s*195,\s*132,\s*0\.12\) !important;/,
    '选中态应显示暗金底与金色细边框',
  );
  assert.match(workspaceCss, /\.asset-shell-nav-button\s+svg[\s\S]*?width:\s*14px !important;[\s\S]*?color:\s*#94a3b8 !important;/);
  assert.match(workspaceCss, /\.asset-shell-nav-label[\s\S]*?font-size:\s*var\(--gw-type-label-lg\) !important;[\s\S]*?font-weight:\s*400 !important;[\s\S]*?color:\s*#e2e8f0 !important;[\s\S]*?text-overflow:\s*ellipsis;/);
  assert.match(workspaceCss, /\.asset-shell-nav-count[\s\S]*?font-size:\s*var\(--gw-type-label-md\) !important;[\s\S]*?font-family:\s*var\(--gw-font-mono\) !important;[\s\S]*?color:\s*#64748b !important;/);

  // 独立页 asset-manager.css 同步对齐三层视觉
  assert.match(managerCss, /\.asset-tree-icon\s*\{[^}]*width:14px;[^}]*color:#94a3b8;/);
  assert.match(managerCss, /\.asset-tree-name\s*\{[^}]*font-size:var\(--gw-type-label-lg\);[^}]*font-weight:400;[^}]*color:#e2e8f0;/);
  assert.match(managerCss, /\.asset-tree-count\s*\{[^}]*color:#64748b;[^}]*font-size:var\(--gw-type-label-md\);/);
  assert.match(managerCss, /\.asset-tree-node\.is-selected\s*>\s*\.asset-tree-row\s*\{[^}]*background:rgba\(223,195,132,\.12\);[^}]*border-color:rgba\(223,195,132,\.45\);/);
});

test('AC2: 根文件夹右键菜单提供「显示/隐藏空文件夹」并持久化过滤空子目录', async () => {
  const { windowObj, categoriesEl, bodyEl, storage } = createControllerSandbox(null);

  const snapshot = {
    selected_key: 'mine:root:root_a',
    inbox_badge: 0,
    roots: [
      {
        id: 'root_a',
        name: '素材盘A',
        count: 5,
        isRoot: true,
        expanded: true,
        hasChildren: true,
        children: [
          {
            key: 'mine:root:root_a::empty_dir',
            name: 'empty_dir',
            count: 0,
            isRoot: false,
            hasChildren: false,
            children: [],
          },
          {
            key: 'mine:root:root_a::loading_dir',
            name: 'loading_dir',
            count: '',
            loading: true,
            isRoot: false,
            hasChildren: false,
            children: [],
          },
          {
            key: 'mine:root:root_a::parent_only_via_child',
            name: 'parent_only_via_child',
            count: 0,
            isRoot: false,
            expanded: true,
            hasChildren: true,
            children: [
              {
                key: 'mine:root:root_a::parent_only_via_child/sub_empty',
                name: 'sub_empty',
                count: 0,
                isRoot: false,
                hasChildren: false,
                children: [],
              },
              {
                key: 'mine:root:root_a::parent_only_via_child/sub_non_empty',
                name: 'sub_non_empty',
                count: 3,
                isRoot: false,
                hasChildren: false,
                children: [],
              },
            ],
          },
        ],
      },
    ],
    boards: [],
  };

  windowObj.GWAssetTreeHost.onTreeSnapshot(snapshot);
  const initialKeys = collectButtons(categoriesEl).map(b => b.dataset.assetTreeEntry);
  assert.ok(initialKeys.includes('mine:root:root_a::empty_dir'), '默认显示空文件夹');
  assert.ok(initialKeys.includes('mine:root:root_a::parent_only_via_child/sub_empty'), '默认显示空子文件夹');

  // 对根文件夹派发 contextmenu，应出现「隐藏空文件夹」
  const rootBtn = collectButtons(categoriesEl).find(b => b.dataset.assetTreeEntry === 'mine:root:root_a');
  assert.ok(rootBtn, '根文件夹按钮存在');
  rootBtn.listeners.contextmenu[0]({
    preventDefault() {},
    stopPropagation() {},
    clientX: 40,
    clientY: 60,
  });

  const menu = bodyEl.children.find(c => c.id === 'assetOrganizeMenu');
  assert.ok(menu, '右键菜单已创建');
  const toggleBtn = menu.children.find(c => Object.hasOwn(c.attributes || {}, 'data-asset-organize-toggle-empty'));
  assert.ok(toggleBtn, '根菜单包含显示/隐藏空文件夹切换项');
  assert.match(toggleBtn.innerHTML, /隐藏空文件夹/);

  // 点击切换为隐藏空文件夹
  await toggleBtn.listeners.click[0]({ stopPropagation() {} });
  assert.equal(storage.get('gw.asset.tree.hideEmptyFolders'), '1', '偏好状态已持久化为 1');

  const hiddenKeys = collectButtons(categoriesEl).map(b => b.dataset.assetTreeEntry);
  assert.equal(hiddenKeys.includes('mine:root:root_a::empty_dir'), false, '计数为0且无非空子目录的空文件夹应隐藏');
  assert.equal(hiddenKeys.includes('mine:root:root_a::parent_only_via_child/sub_empty'), false, '空子目录应隐藏');
  assert.ok(hiddenKeys.includes('mine:root:root_a::loading_dir'), 'loading 中的目录不应隐藏');
  assert.ok(hiddenKeys.includes('mine:root:root_a::parent_only_via_child'), '含非空子目录的父文件夹应保留');
  assert.ok(hiddenKeys.includes('mine:root:root_a::parent_only_via_child/sub_non_empty'), '非空子目录应保留');

  // 子文件夹右键菜单不应出现「显示/隐藏空文件夹」
  const childBtn = collectButtons(categoriesEl).find(b => b.dataset.assetTreeEntry === 'mine:root:root_a::parent_only_via_child/sub_non_empty');
  childBtn.listeners.contextmenu[0]({
    preventDefault() {},
    stopPropagation() {},
    clientX: 40,
    clientY: 60,
  });
  const childMenu = bodyEl.children.find(c => c.id === 'assetOrganizeMenu');
  assert.equal(
    childMenu.children.some(c => Object.hasOwn(c.attributes || {}, 'data-asset-organize-toggle-empty')),
    false,
    '子文件夹右键菜单不应出现根菜单专属的空文件夹开关',
  );

  // 再次右键根文件夹，文案应为「显示空文件夹」，点击后恢复显示
  const rootBtn2 = collectButtons(categoriesEl).find(b => b.dataset.assetTreeEntry === 'mine:root:root_a');
  rootBtn2.listeners.contextmenu[0]({
    preventDefault() {},
    stopPropagation() {},
    clientX: 40,
    clientY: 60,
  });
  const menu2 = bodyEl.children.find(c => c.id === 'assetOrganizeMenu');
  const toggleBtn2 = menu2.children.find(c => Object.hasOwn(c.attributes || {}, 'data-asset-organize-toggle-empty'));
  assert.match(toggleBtn2.innerHTML, /显示空文件夹/);
  await toggleBtn2.listeners.click[0]({ stopPropagation() {} });
  assert.equal(storage.get('gw.asset.tree.hideEmptyFolders'), '0', '偏好状态已持久化为 0');

  const restoredKeys = collectButtons(categoriesEl).map(b => b.dataset.assetTreeEntry);
  assert.ok(restoredKeys.includes('mine:root:root_a::empty_dir'), '切回显示后空文件夹恢复展示');

  // 新建沙盒从 localStorage 恢复持久化状态
  const sandbox2 = createControllerSandbox('1');
  sandbox2.windowObj.GWAssetTreeHost.onTreeSnapshot(snapshot);
  const persistedKeys = collectButtons(sandbox2.categoriesEl).map(b => b.dataset.assetTreeEntry);
  assert.equal(persistedKeys.includes('mine:root:root_a::empty_dir'), false, '新会话应从 localStorage 恢复隐藏空文件夹偏好');
});
