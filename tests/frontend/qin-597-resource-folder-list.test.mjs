// [QIN-597] 资源文件夹统一清单与根目录操作共享管道
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

const manager = readFileSync(new URL('../../web/js/modules/asset-manager.js', import.meta.url), 'utf8');
const organize = readFileSync(new URL('../../web/js/modules/asset-manager/organize.js', import.meta.url), 'utf8');
const settings = readFileSync(new URL('../../web/js/modules/asset-manager/settings.js', import.meta.url), 'utf8');

function sliceBetween(source, startMarker, endMarker) {
  const start = source.indexOf(startMarker);
  const end = source.indexOf(endMarker, start + startMarker.length);
  assert.ok(start >= 0 && end > start, `${startMarker} section exists`);
  return source.slice(start, end);
}

test('AC1/AC2: one settings list shows active and removed roots with status and role-gated actions', () => {
  const list = sliceBetween(settings, 'function renderAllowedRootsSettings()', 'function renderGlobalDirectorySettings()');
  assert.match(list, /资源文件夹/);
  assert.match(list, /在线/);
  assert.match(list, /已移除/);
  assert.match(list, /data-storage-root-restore/);
  assert.match(list, /data-storage-root-purge-prompt/);
  assert.match(list, /data-resource-folder-remove/);
  assert.match(list, /canManageAllowedRoots\(\)/);
  assert.doesNotMatch(list, /renderRemovedRootsModal/);
  assert.match(list, /canManageAllowedRoots\(\) && !root\.readOnly/);
  assert.match(list, /!canManageAllowedRoots\(\).*disabled/);
});

test('AC3: sidebar root menu offers move-out and purge without changing child recycle actions', () => {
  const menu = sliceBetween(manager, 'function renderAssetOrganizeMenu', 'function closeAssetOrganizeMenu');
  assert.match(menu, /data-asset-organize-remove-root/);
  assert.match(menu, /data-asset-organize-purge-root/);
  assert.match(menu, /data-asset-organize-delete/);
  assert.match(menu, /target\.isRoot/);
});

test('AC4: settings and sidebar invoke the registered shared action for both operations', () => {
  const overlay = sliceBetween(organize, 'async function handleAssetOrganizeOverlayClick', 'function renderCustomAssetLibrary');
  const settingsClicks = sliceBetween(manager, "document.addEventListener('click', event => {", "document.addEventListener('change', event => {");
  assert.match(overlay, /executeResourceFolderAction\(current, action\)/);
  assert.match(settingsClicks, /gwCall\('organize', 'executeResourceFolderAction', root, 'remove'\)/);
  assert.match(settingsClicks, /gwCall\('organize', 'executeResourceFolderAction', root, 'purge'/);
  assert.match(organize, /executeResourceFolderAction,\s*invalidateResourceFolderAction/);
});

test('AC5/AC6: shared action maps QIN-596 aliases, retries partial removal and clears selected view state', () => {
  const identity = sliceBetween(organize, 'function resolveResourceFolderIdentity', 'function confirmResourceFolderPurge');
  const action = sliceBetween(organize, 'async function executeResourceFolderAction', 'function removeResourceFolderViewState');
  const cleanup = sliceBetween(organize, 'function removeResourceFolderViewState', 'function ');
  assert.match(identity, /root_\[a-f0-9\]\{24\}/);
  assert.match(identity, /local_extra_/);
  assert.match(identity, /registryFacets\.roots/);
  assert.match(action, /removeAssetSourceRegistrations\(\[identity\.sourceId\], \{deferViewCleanup:true\}\)/);
  assert.match(action, /resourceFolderUnregisteredSources/);
  assert.match(action, /saveAllowedRootRemoval\(identity\.allowedRootId, \{throwOnError:true\}\)/);
  assert.match(cleanup, /registryAssetsQueryCache\.clear\(\)/);
  assert.match(cleanup, /registryFacets\.roots/);
  assert.match(cleanup, /assetTreeExpanded/);
  assert.match(cleanup, /setActiveTab\('registry'\)/);
  assert.match(cleanup, /setAssetTreeSelectedKey\(''\)/);
});

test('AC7: purge requires two confirmations, is irreversible, and preserves source files', () => {
  const confirm = sliceBetween(organize, 'function confirmResourceFolderPurge', 'async function executeResourceFolderAction');
  const request = sliceBetween(manager, 'async function saveAllowedRootPurge', "document.addEventListener('click', event => {");
  assert.match(confirm, /不可恢复/);
  assert.match(confirm, /不会删除源文件/);
  assert.match(confirm, /最后确认/);
  assert.match(request, /\/purge/);
  assert.match(request, /expected_version/);
  assert.match(settings, /绝不会删除您的磁盘原始素材文件/);
});

function loadResourceFolderActions({removeRoot = async () => ({}), purgeRoot = async () => ({}), confirms = [true]} = {}) {
  const start = organize.indexOf('let resourceFolderActionSeq = 0;');
  const end = organize.indexOf('async function removeGeneratedSourceBoard', start);
  assert.ok(start >= 0 && end > start, 'resource-folder action block exists');
  const source = organize.slice(start, end);
  const state = {
    registryFacets: {roots:[{id:'local_extra_abc_123', aliases:['root_0123456789abcdef01234567'], path:'C:/Media'}]},
    registryAssetsQueryCache: {clear(){ this.cleared = true; }},
    registryAssets: [], registryStatus: {overview:{roots:[]}}, assetTreeExpanded: new Set(),
    assetTreeSelectedKey:'', activeTab:'registry', statuses:[], toasts:[], removals:[], purges:[],
    confirmQueue:[...confirms]
  };
  const factory = new Function(
    'registryFacets','registryAssetsQueryCache','registryAssets','registryStatus','assetTreeExpanded',
    'assetTreeSelectedKey','activeTab','window','canManageAllowedRoots','setStatus','showAssetOrganizeToast',
    'removeAssetSourceRegistrations','saveAllowedRootRemoval','saveAllowedRootPurge','setRegistryAssets',
    'setAssetTreeSelectedKey','setActiveTab','refreshRegistryFacetData','refreshRegistryAssets','render',
    `${source}; return {executeResourceFolderAction, invalidateResourceFolderAction, state:()=>({registryFacets, assetTreeSelectedKey, activeTab})};`
  );
  const actions = factory(
    state.registryFacets, state.registryAssetsQueryCache, state.registryAssets, state.registryStatus,
    state.assetTreeExpanded, state.assetTreeSelectedKey, state.activeTab,
    {confirm: message => { state.confirmMessages = [...(state.confirmMessages || []), message]; return state.confirmQueue.shift() ?? true; }},
    () => true,
    message => state.statuses.push(message),
    message => state.toasts.push(message),
    async ids => { state.removals.push(ids); return new Set(ids); },
    async id => { state.allowedRootRemovals = [...(state.allowedRootRemovals || []), id]; return removeRoot(id); },
    async id => { state.purges.push(id); return purgeRoot(id); },
    assets => { state.registryAssets = assets; },
    key => { state.assetTreeSelectedKey = key; },
    tab => { state.activeTab = tab; },
    async () => {}, async () => {}, () => {}
  );
  return {actions, state};
}

test('runtime: move out, refresh, then purge removed root by allowed-root ID alone', async () => {
  const {actions, state} = loadResourceFolderActions({confirms:[true, true]});
  const active = {id:'root_0123456789abcdef01234567', path:'C:/Media', name:'Media'};
  assert.equal((await actions.executeResourceFolderAction(active, 'remove')).status, 'removed');
  assert.deepEqual(state.removals, [['local_extra_abc_123']]);
  assert.deepEqual(state.allowedRootRemovals, ['root_0123456789abcdef01234567']);

  state.registryFacets.roots = [];
  const refreshedRemoved = {id:'root_0123456789abcdef01234567', path:'C:/Media', active:false};
  assert.equal((await actions.executeResourceFolderAction(refreshedRemoved, 'purge', {firstConfirmationSatisfied:true})).status, 'purged');
  assert.deepEqual(state.purges, ['root_0123456789abcdef01234567']);
  assert.equal(state.confirmMessages.length, 2, 'move and final purge confirmations were shown');
});

test('runtime: partial move failure retains source-unregister progress for retry', async () => {
  let revokeAttempts = 0;
  const {actions, state} = loadResourceFolderActions({removeRoot: async () => {
    revokeAttempts += 1;
    if(revokeAttempts === 1) throw new Error('revision conflict');
    return {};
  }, confirms:[true, true]});
  const root = {id:'root_0123456789abcdef01234567', path:'C:/Media'};
  assert.equal((await actions.executeResourceFolderAction(root, 'remove')).status, 'failed');
  assert.equal((await actions.executeResourceFolderAction(root, 'remove')).status, 'removed');
  assert.equal(state.removals.length, 1, 'retry must not unregister the source twice');
  assert.equal(revokeAttempts, 2);
});
