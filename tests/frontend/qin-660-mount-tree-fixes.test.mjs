import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';

const root = process.cwd();
const manager = fs.readFileSync(path.join(root, 'web/js/modules/asset-manager.js'), 'utf8');
const settings = fs.readFileSync(path.join(root, 'web/js/modules/asset-manager/settings.js'), 'utf8');
const organize = fs.readFileSync(path.join(root, 'web/js/modules/asset-manager/organize.js'), 'utf8');
const controller = fs.readFileSync(path.join(root, 'web/js/controllers/assets-controller.js'), 'utf8');

function extractFunction(source, signature) {
  const start = source.indexOf(signature);
  assert.ok(start >= 0, `missing function: ${signature}`);
  const braceStart = source.indexOf('{', start);
  let depth = 0;
  for (let i = braceStart; i < source.length; i += 1) {
    if (source[i] === '{') depth += 1;
    else if (source[i] === '}') {
      depth -= 1;
      if (depth === 0) return source.slice(start, i + 1);
    }
  }
  throw new Error(`unclosed function: ${signature}`);
}

test('QIN-660 AC1: 选中左侧目录默认展示全部类型并支持叠加类型胶囊双向筛选', () => {
  const start = manager.indexOf('function registryQueryString(');
  const end = manager.indexOf('function registryTotalFromResponse(', start);
  assert.ok(start >= 0 && end > start, 'registryQueryString exists');
  const fnSource = manager.slice(start, end);
  const run = new Function(
    'deps',
    `let { activeTab, registryKind, registryStatCategory, registryNavFolder, registryRootId, registryQuery, registryCollectionId, registryRecentDays, registryIncludeTags, registryExcludeTags, registrySort, registryProjectFilterId, boardSourceSelection, boardFolderSelection } = deps;
     const REGISTRY_PAGE_SIZE = 60;
     const registryDynamicPageSize = () => 24;
     const assetBoardById = () => null;
     ${fnSource}
     return registryQueryString();`,
  );

  const baseDeps = {
    activeTab: 'registry',
    registryKind: '',
    registryStatCategory: '',
    registryNavFolder: { rootId: '', path: '' },
    registryRootId: '',
    registryQuery: '',
    registryCollectionId: '',
    registryRecentDays: 0,
    registryIncludeTags: new Set(),
    registryExcludeTags: new Set(),
    registrySort: 'newest',
    registryProjectFilterId: '',
    boardSourceSelection: {},
    boardFolderSelection: {},
  };

  // 未选文件夹的资产总览维持原有默认 category=image,video
  const overviewParams = new URLSearchParams(run(baseDeps));
  assert.equal(overviewParams.get('category'), 'image,video');

  // 选中文件夹后移除默认 category=image,video，展示全部类型
  const folderParams = new URLSearchParams(
    run({ ...baseDeps, registryNavFolder: { rootId: 'root_a', path: 'output/audio' } }),
  );
  assert.equal(folderParams.get('category'), null);
  assert.equal(folderParams.get('root_id'), 'root_a');
  assert.equal(folderParams.get('folder_path'), 'output/audio');

  // 叠加顶部类型胶囊实现「目录 + 类型」双向筛选
  const combinedParams = new URLSearchParams(
    run({ ...baseDeps, registryNavFolder: { rootId: 'root_a', path: 'output/audio' }, registryKind: 'audio' }),
  );
  assert.equal(combinedParams.get('kind'), 'audio');
  assert.equal(combinedParams.get('category'), null);
  assert.equal(combinedParams.get('root_id'), 'root_a');
  assert.equal(combinedParams.get('folder_path'), 'output/audio');
});

test('QIN-660 AC2: 系统目录精确段过滤不误伤含子串的普通目录', () => {
  const fnSource = extractFunction(manager, 'function isSystemExcludedAssetFolderPath(');
  const fn = new Function(
    'thumbnailGeneratorState',
    `${fnSource}; return isSystemExcludedAssetFolderPath;`,
  )({ cacheFolder: 'my-cache-dir' });

  assert.equal(fn('god-recycler'), true);
  assert.equal(fn('sub/god-cache'), true);
  assert.equal(fn('.gods-workbench/tmp'), true);
  assert.equal(fn('my-cache-dir'), true);
  assert.equal(fn('my-god-cache-backup'), false);
  assert.equal(fn('god-recycler-demo'), false);
  assert.equal(fn('audio/sfx'), false);

  assert.match(settings, /excludedDirs\.has\(seg\.toLowerCase\(\)\)/);
});

test('QIN-660 AC3: 纯中间祖先目录递归汇总子孙素材总数', () => {
  const fnSource = extractFunction(manager, 'function assetRackFolderTotalCount(');
  const run = new Function(
    'registryRegisteredFoldersLoaded',
    'registryIndexedFolders',
    `${fnSource}; return assetRackFolderTotalCount;`,
  )(true, [
    { root_id: 'root_a', path: 'ep01/shots/sh001', count: 1, total_count: 1 },
    { root_id: 'root_a', path: 'ep01/shots/sh002', count: 2, total_count: 2 },
  ]);

  assert.equal(run('root_a', 'ep01'), 3);
  assert.equal(run('root_a', 'ep01/shots'), 3);
  assert.equal(run('root_a', 'ep01/shots/sh001'), 1);
  assert.equal(run('root_a', 'empty_dir'), 0);
});

test('QIN-660 AC4: openShellCreateDialog 兼容 qin-579 切片且支持确认、取消与飞行中取消竞态守卫', async () => {
  // 确认 qin-579 切片锚点保持完整
  const menuIdx = controller.indexOf('  function openShellOrganizeMenu(target, clientX, clientY) {');
  const dialogIdx = controller.indexOf('  function openShellCreateDialog(parentKey) {', menuIdx);
  assert.ok(menuIdx >= 0 && dialogIdx > menuIdx, 'qin-579 slice anchors preserved');

  const fnSource = extractFunction(controller, '  function openShellCreateDialog(parentKey) {');
  const toasts = [];
  const makeEl = (tag = 'div') => {
    const el = {
      tagName: tag.toUpperCase(),
      id: '',
      className: '',
      innerHTML: '',
      textContent: '',
      value: '',
      disabled: false,
      isConnected: true,
      children: [],
      listeners: {},
      addEventListener(name, cb) {
        this.listeners[name] = cb;
      },
      focus() {},
      remove() {
        this.isConnected = false;
      },
      closest(sel) {
        if (sel === '[data-asset-organize-dialog-cancel]' && this._isCancel) return this;
        if (sel === '[data-asset-organize-dialog-confirm]' && this._isConfirm) return this;
        return null;
      },
      querySelector(sel) {
        if (sel === 'input') return this._input;
        if (sel === '[data-asset-organize-dialog-error]') return this._err;
        if (sel === '[data-asset-organize-dialog-confirm]') return this._btn;
        return null;
      },
    };
    el._input = { value: '', focus() {}, listeners: {}, addEventListener(n, c) { this.listeners[n] = c; } };
    el._err = { textContent: '' };
    el._btn = { disabled: false, textContent: '创建', _isConfirm: true, closest(s) { return s === '[data-asset-organize-dialog-confirm]' ? this : null; } };
    return el;
  };

  let pendingResolve;
  const bridge = {
    createFolder: () => new Promise((resolve) => { pendingResolve = resolve; }),
  };
  const body = { children: [], appendChild(el) { this.children.push(el); } };
  const document = {
    body,
    querySelectorAll: () => [],
    createElement: (tag) => makeEl(tag),
  };

  const factory = new Function(
    'document',
    'window',
    'organizeBridge',
    'showShellOrganizeToast',
    `let activeShellDialogGen = 0; ${fnSource}; return openShellCreateDialog;`,
  );
  const openDialog = factory(document, { lucide: { createIcons() {} } }, () => bridge, (msg) => toasts.push(msg));

  // 打开弹窗并在请求飞行中点关闭按钮取消
  openDialog('mine:root:r1');
  const overlay = body.children.at(-1);
  overlay._input.value = 'new_sub';
  await overlay.listeners.click({ target: overlay._btn, preventDefault() {}, stopPropagation() {} });
  assert.equal(overlay._btn.disabled, true);
  assert.equal(overlay.isConnected, true);

  // 点击关闭按钮取消
  const cancelBtn = { _isCancel: true, closest(s) { return s === '[data-asset-organize-dialog-cancel]' ? this : null; } };
  overlay.listeners.click({ target: cancelBtn, preventDefault() {}, stopPropagation() {} });
  assert.equal(overlay.isConnected, false);

  // 晚到请求返回不应再触发成功 toast
  pendingResolve({});
  await Promise.resolve();
  assert.equal(toasts.length, 0);

  // organize.js 确认失败时不提前关闭弹窗
  assert.match(organize, /await assetOrganizeCreateFolder\(state, input\?\.value \|\| ''\);\s*if\(assetOrganizeDialog === state\) closeAssetOrganizeDialog\('created'\);/);
});

test('QIN-660 AC5: assetOrganizePhysicalRootId 解析物理 rootId 并用于文件夹操作请求', () => {
  assert.match(manager, /function assetOrganizePhysicalRootId\(/);
  assert.match(manager, /root_id:\s*assetOrganizePhysicalRootId\(target\)/);
});
