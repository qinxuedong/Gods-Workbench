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
