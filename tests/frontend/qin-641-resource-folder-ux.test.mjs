import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';

const REPO_ROOT = process.cwd();
const assetManagerSource = fs.readFileSync(path.join(REPO_ROOT, 'web/js/modules/asset-manager.js'), 'utf8');
const organizeSource = fs.readFileSync(path.join(REPO_ROOT, 'web/js/modules/asset-manager/organize.js'), 'utf8');
const controllerSource = fs.readFileSync(path.join(REPO_ROOT, 'web/js/controllers/assets-controller.js'), 'utf8');

function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

function extractFunction(source, signature, nextSignature) {
  const start = source.indexOf(signature);
  assert.ok(start >= 0, `未找到函数签名: ${signature}`);
  const end = source.indexOf(nextSignature, start);
  assert.ok(end > start, `未找到结束标记: ${nextSignature}`);
  return source.slice(start, end);
}

const leafHelperSource = extractFunction(
  assetManagerSource,
  'function boardSourceLeafFolderName(pathValue){',
  "document.addEventListener('input',",
);
const { boardSourceLeafFolderName, isDefaultBoardSourceName } = new Function(
  `${leafHelperSource}\nreturn { boardSourceLeafFolderName, isDefaultBoardSourceName };`,
)();

test('AC2 路径末段提取与默认占位判定兼容 Windows/POSIX 与盘符根路径', () => {
  assert.equal(boardSourceLeafFolderName('<local-root>/Input'), 'Input');
  assert.equal(boardSourceLeafFolderName('<local-root>/Input/'), 'Input');
  assert.equal(boardSourceLeafFolderName('<drive>\\Projects\\Shots\\'), 'Shots');
  assert.equal(boardSourceLeafFolderName('D:\\'), '');
  assert.equal(boardSourceLeafFolderName('D:'), '');
  assert.equal(isDefaultBoardSourceName(''), true);
  assert.equal(isDefaultBoardSourceName('用户素材'), true);
  assert.equal(isDefaultBoardSourceName('上传素材'), true);
  assert.equal(isDefaultBoardSourceName('自定义素材库'), false);
});

test('AC1/AC2 选夹校验后不自动提交且自动匹配末段文件夹名，保留手动修改能力', async () => {
  const pickBlockStart = assetManagerSource.indexOf("if(target.closest?.('[data-board-source-pick]')){");
  const pickBlockEnd = assetManagerSource.indexOf("if(target.closest?.('[data-board-source-new]')){", pickBlockStart);
  assert.ok(pickBlockStart >= 0 && pickBlockEnd > pickBlockStart);
  const pickBlock = assetManagerSource.slice(pickBlockStart, pickBlockEnd);

  // 确认 pick 的 onSelect 内部绝不再自动调用 confirmBoardSource
  assert.doesNotMatch(pickBlock, /confirmBoardSource/);
  assert.match(pickBlock, /boardSourceState\.pickedValidatedPath\s*=\s*finalPath/);

  const state = {
    open: true,
    generation: 0,
    abortController: null,
    error: '',
    picking: false,
    validating: false,
    saving: false,
    existingId: '',
    name: '用户素材',
    nameCustom: false,
    pickedValidatedPath: '',
    path: '',
  };
  let confirmCalls = 0;
  let renderCalls = 0;

  const runPick = async (selectedPath, { validateDelayMs = 0, preClose = false } = {}) => {
    const target = { closest: sel => (sel === '[data-board-source-pick]' ? {} : null) };
    let capturedOpts = null;
    const gwCall = async (domain, fn, ...args) => {
      if (domain === 'settings' && fn === 'pickServerDirectoryNative') {
        capturedOpts = args[1];
        await capturedOpts.onSelect(selectedPath);
        return;
      }
      if (domain === 'settings' && fn === 'validateServerResourceDirectory') {
        if (validateDelayMs > 0) await new Promise(r => setTimeout(r, validateDelayMs));
        if (preClose) {
          state.open = false;
          state.generation += 1;
        }
        return args[0];
      }
      if (domain === 'organize' && fn === 'renderBoardSourceModal') {
        renderCalls += 1;
        return;
      }
      if (domain === 'organize' && fn === 'confirmBoardSource') {
        confirmCalls += 1;
        return;
      }
    };
    const body = pickBlock
      .replace(/^if\(target\.closest\?\.\('\[data-board-source-pick\]'\)\)\{/, '')
      .replace(/\n\s*return;\s*\}\s*$/, '');
    const fn = new Function(
      'target',
      'boardSourceState',
      'gwCall',
      'setStatus',
      'boardSourceLeafFolderName',
      'isDefaultBoardSourceName',
      `return (async () => { ${body} })();`,
    );
    await fn(target, state, gwCall, () => {}, boardSourceLeafFolderName, isDefaultBoardSourceName);
  };

  // 1) 选夹后仅填充路径和末段名称，绝不自动提交
  await runPick('/mnt/nas/Input/');
  assert.equal(confirmCalls, 0);
  assert.equal(state.picking, false);
  assert.equal(state.validating, false);
  assert.equal(state.path, '/mnt/nas/Input/');
  assert.equal(state.pickedValidatedPath, '/mnt/nas/Input/');
  assert.equal(state.name, 'Input');
  assert.ok(renderCalls > 0);

  // 2) 用户手动自定义名称后再次选夹，不覆盖自定义名称
  state.name = '我的专属素材';
  state.nameCustom = true;
  await runPick('/mnt/nas/Output');
  assert.equal(confirmCalls, 0);
  assert.equal(state.path, '/mnt/nas/Output');
  assert.equal(state.name, '我的专属素材');

  // 3) 1500ms 延迟下关闭弹窗，迟到校验响应被 generation 丢弃且不覆盖状态
  const delayedStateBefore = { ...state };
  await runPick('/mnt/nas/LateDir', { validateDelayMs: 20, preClose: true });
  assert.equal(state.name, delayedStateBefore.name);
  assert.equal(confirmCalls, 0);
});

test('AC4 removeResourceFolderViewState 同步清理 localLibraries 与 allowedRoots 并立即推送左栏快照', () => {
  const fnSource = extractFunction(
    organizeSource,
    'function removeResourceFolderViewState(rootTarget, identity){',
    '\nfunction togglePreviewDeleteCategory(',
  );
  const syncLibsSource = extractFunction(
    organizeSource,
    'function syncRemovedResourceFolderLibraries(ids){',
    '\nexport {',
  );

  let currentLibraries = [
    { id: 'root_1', name: 'Input', path: '/data/Input' },
    { id: 'root_2', name: 'Keep', path: '/data/Keep' },
  ];
  const storageSettingsState = {
    localLibraries: currentLibraries.slice(),
  };
  const assetAllowedRootPaths = new Map([
    ['root_1', '/data/Input'],
    ['root_2', '/data/Keep'],
  ]);
  const registryFacets = {
    roots: [
      { id: 'root_1', aliases: ['alias_1'] },
      { id: 'root_2', aliases: [] },
    ],
  };
  const registryStatus = {
    overview: {
      roots: [{ id: 'root_1' }, { id: 'root_2' }],
    },
  };
  let registryAssets = [
    { id: 'a1', storage_root_id: 'root_1' },
    { id: 'a2', storage_root_id: 'root_2' },
  ];
  const assetTreeExpanded = new Set(['mine:root:root_1', 'mine:root:root_1::__root__', 'mine:root:root_2']);
  let selectedKey = 'mine:root:root_1';
  let activeTab = 'source_library_alias_1';
  const callOrder = [];

  const fn = new Function(
    'registryAssetsQueryCache',
    'registryFacets',
    'registryAssets',
    'setRegistryAssets',
    'registryStatus',
    'localLibraries',
    'setLocalLibraries',
    'storageSettingsState',
    'assetAllowedRootPaths',
    'assetTreeExpanded',
    'assetTreeSelectedKey',
    'setAssetTreeSelectedKey',
    'activeTab',
    'setActiveTab',
    'directorySettingsPage',
    'permissionsSettingsPage',
    'refreshRegistryFacetData',
    'refreshRegistryAssets',
    'render',
    'notifyShellTreeSnapshot',
    `${syncLibsSource}\n${fnSource}\nreturn removeResourceFolderViewState;`,
  );

  let resourceFolderInternalViewSwitch = false;
  const removeFn = fn(
    { clear: () => {} },
    registryFacets,
    registryAssets,
    next => { registryAssets = next; },
    registryStatus,
    currentLibraries,
    next => { currentLibraries = next; },
    storageSettingsState,
    assetAllowedRootPaths,
    assetTreeExpanded,
    selectedKey,
    next => { selectedKey = next; },
    activeTab,
    next => { activeTab = next; },
    true,
    false,
    async () => {},
    async () => {},
    () => { callOrder.push('render'); },
    () => { callOrder.push(`snapshot:${currentLibraries.map(l => l.id).join(',')}`); },
  );

  removeFn(
    { key: 'mine:root:root_1', rootId: 'root_1' },
    { allowedRootId: 'root_1', sourceId: 'alias_1' },
  );

  assert.deepEqual(currentLibraries.map(l => l.id), ['root_2']);
  assert.deepEqual(storageSettingsState.localLibraries.map(l => l.id), ['root_2']);
  assert.equal(assetAllowedRootPaths.has('root_1'), false);
  assert.equal(assetAllowedRootPaths.has('root_2'), true);
  assert.deepEqual(registryAssets.map(a => a.id), ['a2']);
  assert.equal(activeTab, 'registry');
  assert.equal(selectedKey, '');
  assert.deepEqual(callOrder, ['render', 'snapshot:root_2']);
});

test('AC3 左栏树容器与节点统一阻止浏览器原生右键菜单，仅可整理节点唤起黑金菜单并带选中态', () => {
  assert.match(controllerSource, /boundCategories\.addEventListener\('contextmenu',\s*categoriesContextMenuHandler\)/);
  assert.match(controllerSource, /categoriesContextMenuHandler\s*=\s*\(e\)\s*=>\s*\{\s*e\.preventDefault\(\);\s*\}/);
  assert.match(controllerSource, /button\.addEventListener\('contextmenu',\s*\(e\)\s*=>\s*\{\s*e\.preventDefault\(\);\s*e\.stopPropagation\(\);\s*if\s*\(organizeBridge\(\)\?\.canOrganize\(row\.key\)\)/);
  assert.match(controllerSource, /classList\.add\('is-context-open'\)/);
  assert.match(controllerSource, /classList\.remove\('is-context-open'\)/);
  assert.match(controllerSource, /Math\.max\(8,\s*Math\.min\(clientX,\s*window\.innerWidth\s*-\s*230\)\)/);
});
