/** QIN-664 挂载目录素材显隐的真实外壳 DOM 行为测试。 */
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import vm from 'node:vm';

const ROOT = path.resolve(import.meta.dirname, '../..');
const controllerSource = fs.readFileSync(path.join(ROOT, 'web/js/controllers/assets-controller.js'), 'utf8');
const organizeSource = fs.readFileSync(path.join(ROOT, 'web/js/modules/asset-manager/organize.js'), 'utf8');
const assetManagerSource = fs.readFileSync(path.join(ROOT, 'web/js/modules/asset-manager.js'), 'utf8');

function createSandbox() {
  const storage = new Map();
  const listeners = new Map();
  const bridgeCalls = { activate: [], expand: [], visibility: [], empty: [] };
  function matchesSelector(node, selector) {
    if (selector.startsWith('#')) return node.id === selector.slice(1);
    const attr = selector.match(/^\[data-asset-tree-entry="([^"]+)"\]$/);
    if (attr) return node.dataset?.assetTreeEntry === attr[1].replace(/\\(.)/g, '$1');
    if (selector.startsWith('.')) return selector.slice(1).split(/,\s*\./).some(c => node.classList?.contains(c));
    const plainAttr = selector.match(/^\[([^\]]+)\]$/);
    return Boolean(plainAttr && Object.hasOwn(node.attributes || {}, plainAttr[1]));
  }
  function makeNode(tagName = 'div') {
    const el = {
      tagName: String(tagName).toUpperCase(), id: '', className: '', textContent: '', title: '', type: '',
      disabled: false, style: {}, dataset: {}, attributes: {}, children: [], listeners: {}, parentNode: null,
      classList: {
        add(...classes) { const set = new Set(el.className.split(/\s+/).filter(Boolean)); classes.forEach(c => set.add(c)); el.className = [...set].join(' '); },
        remove(...classes) { const set = new Set(el.className.split(/\s+/).filter(Boolean)); classes.forEach(c => set.delete(c)); el.className = [...set].join(' '); },
        toggle(c, force) { const set = new Set(el.className.split(/\s+/).filter(Boolean)); const next = force === undefined ? !set.has(c) : Boolean(force); if (next) set.add(c); else set.delete(c); el.className = [...set].join(' '); return next; },
        contains(c) { return el.className.split(/\s+/).includes(c); },
      },
      setAttribute(k, v) { el.attributes[k] = String(v); }, getAttribute(k) { return el.attributes[k] ?? null; },
      removeAttribute(k) { delete el.attributes[k]; },
      append(...nodes) { nodes.filter(Boolean).forEach(n => { n.parentNode = el; el.children.push(n); }); },
      appendChild(node) { if (node) { node.parentNode = el; el.children.push(node); } return node; },
      replaceChildren(...nodes) { el.children = nodes.filter(Boolean).map(n => { n.parentNode = el; return n; }); },
      insertBefore(node, ref) { if (node) node.parentNode = el; const i = el.children.indexOf(ref); if (i >= 0) el.children.splice(i, 0, node); else el.children.push(node); return node; },
      remove() { if (el.parentNode?.children) el.parentNode.children = el.parentNode.children.filter(c => c !== el); },
      contains(target) { return target === el || el.children.some(c => c?.contains?.(target)); },
      addEventListener(type, fn) { (el.listeners[type] ||= []).push(fn); },
      removeEventListener(type, fn) { el.listeners[type] = (el.listeners[type] || []).filter(f => f !== fn); },
      closest() { return null; }, focus() {},
      querySelector(selector) { return el.querySelectorAll(selector)[0] || null; },
      querySelectorAll(selector) { const out = []; const walk = n => { for (const c of n.children || []) { if (matchesSelector(c, selector)) out.push(c); walk(c); } }; walk(el); return out; },
      set innerHTML(html) { el._innerHTML = String(html || ''); }, get innerHTML() { return el._innerHTML || ''; },
    };
    return el;
  }
  const categoriesEl = makeNode(); categoriesEl.id = 'assetCategories';
  const bodyEl = makeNode('body');
  const frame = {
    dataset: {}, src: '', contentWindow: {
      GWAssetOrganizeBridge: {
        canOrganize: key => key !== 'mine:root:offline_root',
        canToggleAssetsVisibility: key => key.startsWith('mine:root:'),
        canManageResourceFolders: () => false,
        setMountedRootAssetsHidden: async (target, hidden) => { bridgeCalls.visibility.push({ target, hidden }); },
        setHideEmptyFolders: hidden => bridgeCalls.empty.push(hidden),
        toggleExpand: key => bridgeCalls.expand.push(key),
      },
      GWAssetTreeBridge: { activate: key => bridgeCalls.activate.push(key) },
    },
    addEventListener() {}, removeEventListener() {},
  };
  const doc = {
    body: bodyEl, activeElement: null,
    createElement: tag => makeNode(tag), createDocumentFragment: () => makeNode('fragment'),
    getElementById(id) { if (id === 'assetCategories') return categoriesEl; if (id === 'assetVaultFrame') return frame; if (id === 'assetOrganizeMenu') return bodyEl.children.find(c => c.id === id) || null; return null; },
    addEventListener() {}, removeEventListener() {},
  };
  const windowObj = {
    innerWidth: 1280, innerHeight: 800, location: { origin: 'http://localhost:2077', search: '' },
    localStorage: { getItem: k => storage.has(k) ? storage.get(k) : null, setItem: (k, v) => storage.set(k, String(v)), removeItem: k => storage.delete(k) },
    addEventListener(type, fn) { listeners.set(type, [...(listeners.get(type) || []), fn]); },
    dispatchEvent(evt) { (listeners.get(evt?.type) || []).forEach(fn => fn(evt)); return true; },
    lucide: { createIcons() {} },
  };
  windowObj.window = windowObj;
  const context = {
    window: windowObj, location: windowObj.location, document: doc, CSS: { escape: s => String(s) },
    CustomEvent: class { constructor(type, init = {}) { this.type = type; this.detail = init.detail; } },
    URL, URLSearchParams, setTimeout: () => 0, clearTimeout() {}, console,
  };
  vm.createContext(context);
  vm.runInContext(controllerSource, context);
  return { windowObj, categoriesEl, bodyEl, storage, bridgeCalls };
}

function buttons(node, out = []) {
  if (!node || typeof node !== 'object') return out;
  if (node.tagName === 'BUTTON' && node.dataset?.assetTreeEntry) out.push(node);
  for (const child of node.children || []) buttons(child, out);
  return out;
}

function rightClick(button) {
  button.listeners.contextmenu[0]({ preventDefault() {}, stopPropagation() {}, clientX: 40, clientY: 60 });
}

function createVisibilityAction({ getSettings, saveSettings, applySettings, setStatus, toast }) {
  const start = organizeSource.indexOf('async function setMountedRootAssetsHidden(rootTarget, hidden){');
  const end = organizeSource.indexOf('function syncRemovedResourceFolderLibraries', start);
  assert.ok(start >= 0 && end > start, '找到并运行真实显隐写入函数');
  const actionSource = organizeSource.slice(start, end);
  const api = {
    getStorageSettings: () => ({ operation: 'read' }),
    setMountedRootVisibility: (...args) => ({ operation: 'write', args }),
  };
  const factory = new Function(
    'apiJsonResponse', 'assetManagerApi', 'applyStorageSettingsData', 'storageSettingsState',
    'setStatus', 'showAssetOrganizeToast',
    `let mountedRootVisibilitySeq = 0; ${actionSource}; return setMountedRootAssetsHidden;`,
  );
  return factory(async request => request.operation === 'read'
    ? getSettings()
    : saveSettings(...request.args), api, applySettings, { revision: 1 }, setStatus, toast);
}

test('readonly 根可显隐；灰态子树仍可交互且不影响空目录偏好', async () => {
  const { windowObj, categoriesEl, bodyEl, storage, bridgeCalls } = createSandbox();
  const snapshot = hidden => ({
    selected_key: 'mine:root:offline_root', inbox_badge: 0, boards: [], roots: [{
      id: 'offline_root', name: '离线素材盘', count: 4, isRoot: true, offline: true, expanded: true,
      assets_hidden: hidden, hasChildren: true, children: [{
        key: 'mine:root:offline_root::folder', name: 'folder', count: 4, expanded: true, hasChildren: true,
        assets_hidden: hidden, children: [{ key: 'mine:root:offline_root::folder/child', name: 'child', count: 4, hasChildren: false, assets_hidden: hidden, children: [] }],
      }],
    }],
  });
  windowObj.GWAssetTreeHost.onTreeSnapshot(snapshot(false));
  let root = buttons(categoriesEl).find(b => b.dataset.assetTreeEntry === 'mine:root:offline_root');
  assert.ok(root, '只读/离线根仍显示在树中');
  rightClick(root);
  let menu = bodyEl.children.find(n => n.id === 'assetOrganizeMenu');
  let hideAssets = menu.children.find(n => Object.hasOwn(n.attributes, 'data-asset-organize-toggle-assets'));
  assert.match(hideAssets.innerHTML, /隐藏素材/);
  assert.ok(menu.children.find(n => Object.hasOwn(n.attributes, 'data-asset-organize-open')).disabled, '只读根的文件系统动作仍禁用');
  await hideAssets.listeners.click[0]({ stopPropagation() {} });
  assert.equal(bridgeCalls.visibility.length, 1);
  assert.equal(bridgeCalls.visibility[0].hidden, true);

  windowObj.GWAssetTreeHost.onTreeSnapshot(snapshot(true));
  const treeButtons = buttons(categoriesEl);
  root = treeButtons.find(b => b.dataset.assetTreeEntry === 'mine:root:offline_root');
  const folder = treeButtons.find(b => b.dataset.assetTreeEntry === 'mine:root:offline_root::folder');
  const child = treeButtons.find(b => b.dataset.assetTreeEntry === 'mine:root:offline_root::folder/child');
  for (const row of [root, folder, child]) assert.ok(row.classList.contains('is-assets-hidden'), '隐藏状态应用到整棵子树');
  assert.ok(root.querySelector('[data-asset-tree-toggle]'), '灰态根仍保留展开控件');
  root.listeners.click[0]({ detail: 1 });
  root.querySelector('[data-asset-tree-toggle]').listeners.click[0]({ stopPropagation() {} });
  assert.deepEqual(bridgeCalls.activate, ['mine:root:offline_root']);
  assert.deepEqual(bridgeCalls.expand, ['mine:root:offline_root']);
  rightClick(root);
  menu = bodyEl.children.find(n => n.id === 'assetOrganizeMenu');
  const showAssets = menu.children.find(n => Object.hasOwn(n.attributes, 'data-asset-organize-toggle-assets'));
  assert.match(showAssets.innerHTML, /显示素材/);

  // 在线隐藏子目录仍可右键整理；目录显隐与空目录显示偏好是两个独立操作。
  rightClick(folder);
  menu = bodyEl.children.find(n => n.id === 'assetOrganizeMenu');
  assert.ok(menu, '灰态子目录仍能打开右键菜单');
  assert.equal(menu.children.some(n => Object.hasOwn(n.attributes, 'data-asset-organize-toggle-assets')), false, '显隐开关仅在根菜单出现');

  // 对离线根点击空目录偏好开关，仍只写原 localStorage 键并派发原事件。
  rightClick(root);
  menu = bodyEl.children.find(n => n.id === 'assetOrganizeMenu');
  const hideEmpty = menu.children.find(n => Object.hasOwn(n.attributes, 'data-asset-organize-toggle-empty'));
  assert.match(hideEmpty.innerHTML, /隐藏空文件夹/);
  await hideEmpty.listeners.click[0]({ stopPropagation() {} });
  assert.equal(storage.get('gw.asset.tree.hideEmptyFolders'), '1');
  assert.deepEqual(bridgeCalls.empty, [true]);
  assert.equal(buttons(categoriesEl).some(b => b.dataset.assetTreeEntry === 'mine:root:offline_root'), true, '空目录偏好不会隐藏根节点');
});

test('显隐写入遵循 CAS；失败不预改状态，慢响应与乱序只采纳最新操作', async () => {
  let settings = { revision: 4, hidden_asset_root_keys: [] };
  let conflictOnce = true;
  const writes = [];
  const applied = [];
  const errors = [];
  const action = createVisibilityAction({
    getSettings: async () => ({ ...settings }),
    saveSettings: async (rootId, hidden, expectedVersion) => {
      writes.push({ rootId, hidden, expectedVersion });
      if (conflictOnce) {
        conflictOnce = false;
        settings = { ...settings, revision: settings.revision + 1 };
        throw new Error('409 VERSION_CONFLICT');
      }
      if (expectedVersion !== settings.revision) throw new Error('409 VERSION_CONFLICT');
      settings = {
        revision: settings.revision + 1,
        hidden_asset_root_keys: hidden ? ['/mount/root'] : [],
      };
      return { ...settings };
    },
    applySettings: value => applied.push({ ...value }),
    setStatus: message => errors.push(message),
    toast: message => errors.push(message),
  });
  const target = { id: 'local_extra_mount', rootId: 'root_0123456789abcdef01234567', isRoot: true };
  const saved = await action(target, true);
  assert.equal(saved.status, 'saved', '409 后回读新版本并重试成功');
  assert.deepEqual(writes.map(item => item.expectedVersion), [4, 5]);
  assert.equal(settings.hidden_asset_root_keys[0], '/mount/root');
  assert.equal(applied.at(-1).revision, 6);

  settings = { revision: 8, hidden_asset_root_keys: [] };
  const failing = createVisibilityAction({
    getSettings: async () => ({ ...settings }),
    saveSettings: async () => { throw new Error('403 FORBIDDEN'); },
    applySettings: value => applied.push({ ...value }),
    setStatus: message => errors.push(message),
    toast: message => errors.push(message),
  });
  const failed = await failing(target, true);
  assert.equal(failed.status, 'failed');
  assert.deepEqual(settings.hidden_asset_root_keys, [], '失败保留服务端原状态');
  assert.equal(errors.at(-1), '403 FORBIDDEN');

  let finishOldRead;
  let reads = 0;
  settings = { revision: 10, hidden_asset_root_keys: ['/mount/root'] };
  const racedWrites = [];
  const raced = createVisibilityAction({
    getSettings: () => {
      reads += 1;
      if (reads === 1) return new Promise(resolve => { finishOldRead = resolve; });
      return Promise.resolve({ ...settings });
    },
    saveSettings: async (rootId, hidden, expectedVersion) => {
      racedWrites.push({ rootId, hidden, expectedVersion });
      settings = { revision: settings.revision + 1, hidden_asset_root_keys: hidden ? ['/mount/root'] : [] };
      return { ...settings };
    },
    applySettings: value => applied.push({ ...value }), setStatus() {}, toast() {},
  });
  const obsolete = raced(target, true);
  const latest = await raced(target, false);
  finishOldRead({ revision: 10, hidden_asset_root_keys: ['/mount/root'] });
  assert.equal((await obsolete).status, 'stale');
  assert.equal(latest.status, 'saved');
  assert.equal(racedWrites.length, 1, '旧读取完成后不会发出旧显隐写入');
  assert.equal(racedWrites[0].hidden, false);
});

test('1500ms 与 5000ms 写入延迟期间不提前切换隐藏状态', async () => {
  for (const delayMs of [1500, 5000]) {
    let settings = { revision: 1, hidden_asset_root_keys: [] };
    const action = createVisibilityAction({
      getSettings: async () => ({ ...settings }),
      saveSettings: async (_rootId, hidden) => {
        await new Promise(resolve => setTimeout(resolve, delayMs));
        settings = { revision: settings.revision + 1, hidden_asset_root_keys: hidden ? ['/mount/root'] : [] };
        return { ...settings };
      },
      applySettings: value => { settings = { ...value }; }, setStatus() {}, toast() {},
    });
    const target = { id: 'local_extra_mount', rootId: 'root_0123456789abcdef01234567', isRoot: true };
    let complete = false;
    const pending = action(target, true).finally(() => { complete = true; });
    await new Promise(resolve => setTimeout(resolve, delayMs - 100));
    assert.equal(complete, false, `${delayMs}ms 响应到达前写入仍在等待`);
    assert.deepEqual(settings.hidden_asset_root_keys, [], `${delayMs}ms 等待期间没有提前切换状态`);
    assert.equal((await pending).status, 'saved');
    assert.deepEqual(settings.hidden_asset_root_keys, ['/mount/root']);
  }
});

test('显隐写后在分面刷新完成后重绘顶部可见素材统计', async () => {
  const start = assetManagerSource.indexOf('async function gwSetMountedRootAssetsHidden(rootTarget, hidden){');
  const end = assetManagerSource.indexOf('window.GWAssetOrganizeBridge', start);
  assert.ok(start >= 0 && end > start, '找到真实显隐协调函数');
  const actionSource = assetManagerSource.slice(start, end);
  const renderedCounts = [];
  const calls = [];
  let visibleImageCount = 2;
  const factory = new Function(
    'assetOrganizeVisibilityTargetFromKey', 'gwCall', 'invalidateRegistryAssetCache',
    'abortRegistryAssetRequests', 'refreshRegistryAssets', 'refreshRegistryFacetData',
    'notifyShellTreeSnapshot', 'setStatus', 'showAssetOrganizeToast', 'render',
    `let registryRequestSeq = 0;
     let registryPendingNewItems = new Map();
     let registryPendingNewIds = new Set();
     let registryPendingVanishedIds = new Set();
     let registryReconcilePageIds = new Set();
     ${actionSource}
     return gwSetMountedRootAssetsHidden;`,
  );
  const action = factory(
    key => ({ key }),
    async () => ({ status: 'saved' }),
    reason => calls.push(`invalidate:${reason}`),
    () => calls.push('abort'),
    async () => { calls.push('assets'); },
    async () => { calls.push('facets'); visibleImageCount = 0; },
    () => calls.push('tree'),
    message => calls.push(`status:${message}`),
    message => calls.push(`toast:${message}`),
    () => { renderedCounts.push(visibleImageCount); calls.push('render'); },
  );
  const result = await action({ key: 'mine:root:root_example' }, true);
  assert.equal(result.status, 'saved');
  assert.deepEqual(renderedCounts, [0], '顶部统计从刷新后的可见分面重绘');
  assert.ok(calls.indexOf('render') > calls.indexOf('facets'), '分面刷新完成后才重绘统计');
});
