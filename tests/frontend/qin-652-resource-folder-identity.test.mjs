// [QIN-652] 资源文件夹身份解析：外壳树节点只带登记来源 id（local_extra_*）时，
// 必须能从 facet 组的别名里反查出允许根 id（root_*），否则「清除索引」会误报
// 「无法识别资源文件夹身份，请刷新清单后重试」（QA 复测阻断项）。
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

const organize = readFileSync(
  new URL('../../web/js/modules/asset-manager/organize.js', import.meta.url),
  'utf8',
);

const SOURCE = 'local_extra_mww2cw8q_f4f1z0';
const ROOT = 'root_5e1a1915b261ddd3f4797665';

function loadResourceFolderActions({ roots = [{ id: SOURCE, aliases: [SOURCE, ROOT] }] } = {}) {
  const start = organize.indexOf('let resourceFolderActionSeq = 0;');
  const end = organize.indexOf('function togglePreviewDeleteCategory', start);
  assert.ok(start >= 0 && end > start, 'resource-folder action block exists');
  const source = organize.slice(start, end);
  const state = {
    registryFacets: { roots: [...roots] },
    registryAssetsQueryCache: { clear() { this.cleared = true; } },
    registryAssets: [],
    registryStatus: { overview: { roots: [] } },
    assetTreeExpanded: new Set(),
    assetTreeSelectedKey: '',
    activeTab: 'registry',
    statuses: [],
    toasts: [],
    purges: [],
    confirmQueue: [true, true],
  };
  const factory = new Function(
    'registryFacets', 'registryAssetsQueryCache', 'registryAssets', 'registryStatus', 'assetTreeExpanded',
    'assetTreeSelectedKey', 'activeTab', 'window', 'canManageAllowedRoots', 'setStatus', 'showAssetOrganizeToast',
    'saveAllowedRootRemoval', 'saveAllowedRootPurge', 'setRegistryAssets',
    'setAssetTreeSelectedKey', 'setActiveTab', 'refreshRegistryFacetData', 'refreshRegistryAssets', 'render',
    'invalidateAllowedRootMutation', 'directorySettingsPage', 'permissionsSettingsPage', 'reportRegistryWriteRefreshFailure',
    `${source}; return {executeResourceFolderAction};`,
  );
  const actions = factory(
    state.registryFacets, state.registryAssetsQueryCache, state.registryAssets, state.registryStatus,
    state.assetTreeExpanded, state.assetTreeSelectedKey, state.activeTab,
    { confirm: message => { state.confirmMessages = [...(state.confirmMessages || []), message]; return state.confirmQueue.shift() ?? true; } },
    () => true,
    message => state.statuses.push(message),
    message => state.toasts.push(message),
    async id => { state.allowedRootRemovals = [...(state.allowedRootRemovals || []), id]; return {}; },
    async id => { state.purges.push(id); return {}; },
    assets => { state.registryAssets = assets; },
    key => { state.assetTreeSelectedKey = key; },
    tab => { state.activeTab = tab; },
    async () => {}, async () => {}, () => {},
    () => {},
    false, false, () => {},
  );
  return { actions, state };
}

test('QIN-652: 外壳树只带登记来源 id 时按 facet 组反查允许根并清除索引', async () => {
  const { actions, state } = loadResourceFolderActions();
  const treeNode = { id: SOURCE, rootId: SOURCE, aliases: [SOURCE], key: `mine:root:${SOURCE}`, isRoot: true };
  const result = await actions.executeResourceFolderAction(treeNode, 'purge', { firstConfirmationSatisfied: true });
  assert.equal(result.status, 'purged');
  assert.deepEqual(state.purges, [ROOT]);
  assert.equal(state.confirmMessages.length, 1, '只弹最后一道确认');
});

test('QIN-652: 直接给允许根 id 的既有路径不受影响', async () => {
  const { actions, state } = loadResourceFolderActions({ roots: [] });
  const root = { id: ROOT, path: 'D:/tmp/media-ui', aliases: [] };
  const result = await actions.executeResourceFolderAction(root, 'purge', { firstConfirmationSatisfied: true });
  assert.equal(result.status, 'purged');
  assert.deepEqual(state.purges, [ROOT]);
});

test('QIN-652: 组内确实没有允许根身份时仍如实拒绝且不发请求', async () => {
  const { actions, state } = loadResourceFolderActions({ roots: [{ id: SOURCE, aliases: [SOURCE] }] });
  const treeNode = { id: SOURCE, rootId: SOURCE, aliases: [SOURCE], isRoot: true };
  const result = await actions.executeResourceFolderAction(treeNode, 'purge', { firstConfirmationSatisfied: true });
  assert.equal(result.status, 'invalid-target');
  assert.match(result.error.message, /无法识别资源文件夹身份/);
  assert.deepEqual(state.purges, []);
});
