/** QIN-670 左侧目录树任意文件夹独立显隐与祖先继承行为测试。 */
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import vm from 'node:vm';

const ROOT = path.resolve(import.meta.dirname, '../..');
const controllerSource = fs.readFileSync(path.join(ROOT, 'web/js/controllers/assets-controller.js'), 'utf8');
const organizeSource = fs.readFileSync(path.join(ROOT, 'web/js/modules/asset-manager/organize.js'), 'utf8');
const assetManagerSource = fs.readFileSync(path.join(ROOT, 'web/js/modules/asset-manager.js'), 'utf8');

function createControllerSandbox() {
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
        canOrganize: () => true,
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
  const CustomEventImpl = class { constructor(type, init = {}) { this.type = type; this.detail = init.detail; } };
  windowObj.CustomEvent = CustomEventImpl;
  const context = {
    window: windowObj, location: windowObj.location, document: doc, CSS: { escape: s => String(s) },
    CustomEvent: CustomEventImpl,
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

function extractFunction(source, signature, nextAnchor) {
  const start = source.indexOf(signature);
  const end = source.indexOf(nextAnchor, start);
  assert.ok(start >= 0 && end > start, `提取函数失败: ${signature}`);
  return source.slice(start, end);
}

test('子文件夹右键菜单与祖先继承禁用规则：置灰仍可交互且不影响空文件夹偏好', async () => {
  const { windowObj, categoriesEl, bodyEl, storage, bridgeCalls } = createControllerSandbox();
  storage.set('gw.asset.tree.hideEmptyFolders', '1');

  windowObj.GWAssetTreeHost.onTreeSnapshot({
    selected_key: 'mine:root:r1::shots/sc01',
    roots: [{
      id: 'r1', rootId: 'r1', key: 'mine:root:r1', name: '主素材库', isRoot: true,
      self_hidden: false, inherited_hidden: false, assets_hidden: false,
      expanded: true, hasChildren: true, count: 10,
      children: [{
        key: 'mine:root:r1::shots', name: 'shots', path: 'shots', isRoot: false,
        self_hidden: true, inherited_hidden: false, assets_hidden: true,
        expanded: true, hasChildren: true, hasNonEmptyDescendant: true, count: 5,
        children: [{
          key: 'mine:root:r1::shots/sc01', name: 'sc01', path: 'shots/sc01', isRoot: false,
          self_hidden: false, inherited_hidden: true, assets_hidden: true,
          expanded: false, hasChildren: false, hasNonEmptyDescendant: true, count: 2,
          children: [],
        }],
      }, {
        key: 'mine:root:r1::other', name: 'other', path: 'other', isRoot: false,
        self_hidden: false, inherited_hidden: false, assets_hidden: false,
        expanded: false, hasChildren: false, hasNonEmptyDescendant: true, count: 3,
        children: [],
      }],
    }],
  });

  const treeBtns = buttons(categoriesEl);
  const shotsBtn = treeBtns.find(b => b.dataset.assetTreeEntry === 'mine:root:r1::shots');
  const sc01Btn = treeBtns.find(b => b.dataset.assetTreeEntry === 'mine:root:r1::shots/sc01');
  const otherBtn = treeBtns.find(b => b.dataset.assetTreeEntry === 'mine:root:r1::other');

  assert.ok(shotsBtn && sc01Btn && otherBtn, '含隐藏素材的子目录在隐藏空文件夹模式下仍留在树中');
  assert.equal(shotsBtn.classList.contains('is-assets-hidden'), true);
  assert.equal(sc01Btn.classList.contains('is-assets-hidden'), true);
  assert.equal(sc01Btn.dataset.inheritedHidden, 'true');
  assert.equal(otherBtn.classList.contains('is-assets-hidden'), false);

  // 1) 父级 shots 自身隐藏，右键可点「显示素材」
  rightClick(shotsBtn);
  let menu = bodyEl.children.find(n => n.id === 'assetOrganizeMenu');
  const shotsToggle = menu.children.find(n => Object.hasOwn(n.attributes, 'data-asset-organize-toggle-assets'));
  assert.ok(shotsToggle);
  assert.match(shotsToggle.innerHTML, /显示素材/);
  assert.equal(shotsToggle.disabled, false);
  await shotsToggle.listeners.click[0]({ stopPropagation() {} });
  assert.equal(bridgeCalls.visibility.length, 1);
  assert.equal(bridgeCalls.visibility[0].hidden, false);

  // 2) 子级 sc01 因祖先隐藏而禁用「显示素材」并附带说明
  rightClick(sc01Btn);
  menu = bodyEl.children.find(n => n.id === 'assetOrganizeMenu');
  const sc01Toggle = menu.children.find(n => Object.hasOwn(n.attributes, 'data-asset-organize-toggle-assets'));
  assert.ok(sc01Toggle);
  assert.match(sc01Toggle.innerHTML, /显示素材/);
  assert.equal(sc01Toggle.disabled, true);
  assert.match(sc01Toggle.title, /上级文件夹已隐藏/);

  // 3) 未隐藏的普通子目录 other 可正常「隐藏素材」
  rightClick(otherBtn);
  menu = bodyEl.children.find(n => n.id === 'assetOrganizeMenu');
  const otherToggle = menu.children.find(n => Object.hasOwn(n.attributes, 'data-asset-organize-toggle-assets'));
  assert.ok(otherToggle);
  assert.match(otherToggle.innerHTML, /隐藏素材/);
  assert.equal(otherToggle.disabled, false);

  // 4) 灰态节点仍可点击选中
  sc01Btn.listeners.click[0]({ detail: 1 });
  assert.deepEqual(bridgeCalls.activate, ['mine:root:r1::shots/sc01']);
});

test('asset-manager 真实树序列化函数与 organize 显隐写入动作覆盖子文件夹与祖先拦截', async () => {
  const fnHidden = extractFunction(assetManagerSource, 'function folderHiddenStatesForKey', 'function serializeAssetRackFolderNode');
  const fnSerialize = extractFunction(assetManagerSource, 'function serializeAssetRackFolderNode', 'function gwAssetTreeSnapshot');
  const fnTarget = extractFunction(assetManagerSource, 'function assetOrganizeVisibilityTargetFromKey', 'function assetOrganizePhysicalRootId');

  const storageSettingsState = {
    hiddenAssetRootKeys: ['d:/media/proj/shots/sc01'],
  };
  const registryFacets = {
    roots: [{ id: 'r1', name: '项目库', aliases: ['r1'], assets_hidden: false }],
  };
  const assetTreeExpanded = new Set(['mine:root:r1::shots']);
  const treeFactory = new Function(
    'storageSettingsState', 'registryFacets', 'assetTreeExpanded', 'registryIndexedFolders',
    'registryRegisteredFoldersLoaded', 'assetRackFolderTotalCount', 'assetOrganizeAbsPath', 'assetOrganizeIsMineTree',
    `${fnHidden}\n${fnSerialize}\n${fnTarget}\nreturn { folderHiddenStatesForKey, serializeAssetRackFolderNode, assetOrganizeVisibilityTargetFromKey };`,
  );
  const { serializeAssetRackFolderNode, assetOrganizeVisibilityTargetFromKey } = treeFactory(
    storageSettingsState,
    registryFacets,
    assetTreeExpanded,
    [{ root_id: 'r1', path: 'shots/sc01', count: 2, total_count: 2 }],
    true,
    () => 2,
    (rid, rel) => (rel ? `D:/Media/Proj/${rel}` : 'D:/Media/Proj'),
    () => true,
  );

  // 仅 sc01 自身隐藏：shots 为可见，sc01 为 self_hidden=true
  const shotsNode = serializeAssetRackFolderNode('r1', {
    name: 'shots',
    path: 'shots',
    children: [{ name: 'sc01', path: 'shots/sc01', children: [] }],
  }, 1, false);
  assert.equal(shotsNode.self_hidden, false);
  assert.equal(shotsNode.assets_hidden, false);
  assert.equal(shotsNode.children[0].self_hidden, true);
  assert.equal(shotsNode.children[0].inherited_hidden, false);
  assert.equal(shotsNode.children[0].assets_hidden, true);

  // 隐藏父级 shots 后，sc01 同时具备 self_hidden=true 与 inherited_hidden=true
  storageSettingsState.hiddenAssetRootKeys = ['d:/media/proj/shots', 'd:/media/proj/shots/sc01'];
  const shotsHiddenNode = serializeAssetRackFolderNode('r1', {
    name: 'shots',
    path: 'shots',
    children: [{ name: 'sc01', path: 'shots/sc01', children: [] }],
  }, 1, false);
  assert.equal(shotsHiddenNode.self_hidden, true);
  assert.equal(shotsHiddenNode.assets_hidden, true);
  assert.equal(shotsHiddenNode.children[0].self_hidden, true);
  assert.equal(shotsHiddenNode.children[0].inherited_hidden, true);
  assert.equal(shotsHiddenNode.children[0].assets_hidden, true);

  // assetOrganizeVisibilityTargetFromKey 支持子文件夹键
  const subTarget = assetOrganizeVisibilityTargetFromKey('mine:root:r1::shots/sc01');
  assert.equal(subTarget.isRoot, false);
  assert.equal(subTarget.path, 'shots/sc01');
  assert.equal(subTarget.inherited_hidden, true);

  // 验证 organize.js 的 setMountedRootAssetsHidden 传参 folderPath 且对 FOLDER_ANCESTOR_HIDDEN 不重试
  const actionSource = extractFunction(organizeSource, 'async function setMountedRootAssetsHidden(rootTarget, hidden){', 'function syncRemovedResourceFolderLibraries');
  let writeAttempts = 0;
  let capturedArgs = null;
  let toastText = '';
  const api = {
    getStorageSettings: () => ({ operation: 'read' }),
    setMountedRootVisibility: (...args) => ({ operation: 'write', args }),
  };
  const actionFactory = new Function(
    'apiJsonResponse', 'assetManagerApi', 'applyStorageSettingsData', 'storageSettingsState',
    'setStatus', 'showAssetOrganizeToast',
    `let mountedRootVisibilitySeq = 0; ${actionSource}; return setMountedRootAssetsHidden;`,
  );
  const runAction = actionFactory(
    async req => {
      if (req.operation === 'read') return { revision: 3, hidden_asset_root_keys: [] };
      writeAttempts += 1;
      capturedArgs = req.args;
      const err = new Error('上级文件夹已隐藏，请先恢复上级显示');
      err.code = 'FOLDER_ANCESTOR_HIDDEN';
      throw err;
    },
    api,
    () => {},
    { revision: 3 },
    () => {},
    msg => { toastText = msg; },
  );
  const res = await runAction({ id: 'r1', rootId: 'r1', isRoot: false, path: 'shots/sc01' }, false);
  assert.equal(res.status, 'failed');
  assert.equal(writeAttempts, 1, 'FOLDER_ANCESTOR_HIDDEN 不应触发 409 重试');
  assert.equal(capturedArgs[4], 'shots/sc01');
  assert.match(toastText, /上级文件夹已隐藏/);
});
