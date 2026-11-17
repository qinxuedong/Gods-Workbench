// [QIN-598] 允许根迁入「权限」面板 + 非管理员隐藏 + 多主题与响应式布局校验
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

const manager = readFileSync(new URL('../../web/js/modules/asset-manager.js', import.meta.url), 'utf8');
const settings = readFileSync(new URL('../../web/js/modules/asset-manager/settings.js', import.meta.url), 'utf8');
const organize = readFileSync(new URL('../../web/js/modules/asset-manager/organize.js', import.meta.url), 'utf8');
const indexHtml = readFileSync(new URL('../../web/pages/index.html', import.meta.url), 'utf8');
const settingsHtml = readFileSync(new URL('../../web/pages/settings.html', import.meta.url), 'utf8');
const homeController = readFileSync(new URL('../../web/js/controllers/home-controller.js', import.meta.url), 'utf8');
const telemetry = readFileSync(new URL('../../web/js/core/hardware-telemetry.js', import.meta.url), 'utf8');
const directoryCss = readFileSync(new URL('../../web/css/pages/directory-settings.css', import.meta.url), 'utf8');

function sliceBetween(source, startMarker, endMarker) {
  const start = source.indexOf(startMarker);
  const end = source.indexOf(endMarker, start + startMarker.length);
  assert.ok(start >= 0 && end > start, `${startMarker} section exists`);
  return source.slice(start, end);
}

test('AC6/AC7: directory settings excludes allowed roots card and keeps QIN-597 terminology', () => {
  const renderFn = sliceBetween(settings, 'function renderGlobalDirectorySettings()', '// 资源文件夹授权登记：');
  assert.match(renderFn, /class="directory-cards-column directory-cards-column--right"[\s\S]*renderProjectWorkspaceSettingsBody\(\)/);
  assert.doesNotMatch(renderFn, /class="directory-cards-column directory-cards-column--right"[\s\S]*renderAllowedRootsSettings\(\)/);
  assert.match(renderFn, /if\(permissionsSettingsPage\)[\s\S]*允许根权限管理[\s\S]*renderAllowedRootsSettings\(\)/);
  assert.match(renderFn, /gw-permissions-roots-height/);
  assert.doesNotMatch(settings + indexHtml + settingsHtml, /已移除登记目录|登记允许根/);
});

test('AC6: permissions section mounts allowed roots only for admin and hides for non-admin/degraded', () => {
  for (const html of [indexHtml, settingsHtml]) {
    const panel = html.slice(html.indexOf('data-panel="permissions"'));
    assert.match(panel, /id="permissionsAllowedRootsMount"/);
    assert.match(panel, /data-permissions-allowed-roots/);
    assert.doesNotMatch(html, /data-section="advanced"/);
  }
  const syncFn = sliceBetween(telemetry, 'syncPermissionsAllowedRootsMount: function()', 'bindAccountEntry: function()');
  assert.match(syncFn, /this\.authState\?\.authenticated[\s\S]*principal\?\.role \|\| ''\) === 'admin'/);
  assert.match(syncFn, /mount\.innerHTML = ''/);
  assert.match(syncFn, /\/static\/asset-manager\.html\?settings=permissions&embedded=1/);
  assert.match(syncFn, /gw-permissions-roots-height/);
  assert.match(syncFn, /gw-permissions-section-leave/);
  assert.match(homeController, /sec === 'permissions'[\s\S]*syncSeatPanel\?\.\(\)/);
});

test('runtime: renderAllowedRootsSettings enforces admin gating, removed styling, and PURGE_INCOMPLETE recovery', () => {
  const source = sliceBetween(settings, 'function renderAllowedRootsSettings()', 'function renderGlobalDirectorySettings()');
  const buildRenderer = canManage => new Function(
    'allowedRootSettingsState','canManageAllowedRoots','escapeAttr','escapeHtml','pendingPurgeRoot',
    'renderPurgeConfirmModal','canBrowseServerDirectories','ALLOWED_ROOT_FIELD_KEY','resourceFolderDisplayName',
    `${source}; return renderAllowedRootsSettings;`
  )(
    {
      roots: [
        {id:'root_sys', path:'D:/System', active:true, readOnly:true, availability:'available', aliases:[], aliasCandidates:[]},
        {id:'root_rem', path:'D:/Removed', active:false, readOnly:false, availability:'removed', availabilityReason:'MOUNT_ROOT_REMOVED', aliases:[], aliasCandidates:[]},
        {id:'root_inc', path:'D:/Incomplete', active:false, readOnly:false, availability:'removed', availabilityReason:'PURGE_INCOMPLETE', availabilityMessage:'上次清除索引未完成，请重试清除索引', aliases:[], aliasCandidates:[]},
      ],
      saving: false,
      error: '',
    },
    () => canManage,
    value => String(value),
    value => String(value),
    null, () => '', () => false, 'allowed-root-path',
    root => root.path
  );

  assert.equal(buildRenderer(false)(), '', 'non-admin sees empty permissions allowed-roots section');
  const adminHtml = buildRenderer(true)();
  assert.match(adminHtml, /allowed-root-row--removed/);
  assert.match(adminHtml, /清除未完成/);
  assert.doesNotMatch(adminHtml, /data-storage-root-restore="root_inc"/);
  assert.match(adminHtml, /data-storage-root-purge-prompt="root_inc"/);
  assert.doesNotMatch(adminHtml, /data-resource-folder-remove="root_sys"/);
  assert.doesNotMatch(adminHtml, /data-storage-root-purge-prompt="root_sys"/);
});

test('runtime: saveAllowedRootsRequest drops stale response on leave and reports refresh failures', async () => {
  const source = sliceBetween(manager, 'let allowedRootMutationSeq = 0;', 'async function saveAllowedRootRemoval');
  const calls = [];
  const state = {
    allowedRootSettingsState: {roots:[], revision:1, saving:false, error:''},
    statuses: [],
    refreshErrors: [],
  };
  let resolveReq;
  const runtime = new Function(
    'allowedRootSettingsState','gwCall','apiJsonResponse','assetManagerHttp','assetManagerApi',
    'document','setStatus','embeddedSettingsPage','reportRegistryWriteRefreshFailure','loadRegistryBootstrap',
    `${source}; return {saveAllowedRootsRequest, invalidateAllowedRootMutation, getState:()=>allowedRootSettingsState};`
  )(
    state.allowedRootSettingsState,
    (...args) => calls.push(['gwCall', ...args]),
    p => p,
    {request: () => new Promise(r => { resolveReq = r; })},
    {getStorageSettings: () => Promise.reject(new Error('storage offline'))},
    {getElementById: () => null},
    msg => state.statuses.push(msg),
    true,
    (msg, err) => state.refreshErrors.push([msg, err?.message]),
    undefined
  );

  const pending = runtime.saveAllowedRootsRequest('/api/storage-settings/roots/root_1', 'DELETE', {expected_version:1});
  runtime.invalidateAllowedRootMutation();
  resolveReq({roots:[], revision:2});
  assert.equal(await pending, null);
  assert.equal(state.statuses.length, 0, 'stale completion does not update status');

  // Re-run without invalidation when getStorageSettings fails: must report refresh failure visibly
  let resolveOk;
  const runtimeErr = new Function(
    'allowedRootSettingsState','gwCall','apiJsonResponse','assetManagerHttp','assetManagerApi',
    'document','setStatus','embeddedSettingsPage','reportRegistryWriteRefreshFailure','loadRegistryBootstrap',
    `${source}; return {saveAllowedRootsRequest};`
  )(
    {roots:[], revision:1, saving:false, error:''},
    () => {}, p => p,
    {request: () => new Promise(r => { resolveOk = r; })},
    {getStorageSettings: () => Promise.reject(new Error('storage offline'))},
    {getElementById: () => null},
    msg => state.statuses.push(msg),
    true,
    (msg, err) => state.refreshErrors.push([msg, err?.message]),
    undefined
  );
  const okPromise = runtimeErr.saveAllowedRootsRequest('/api/storage-settings/roots/root_1', 'DELETE', {expected_version:1});
  resolveOk({roots:[], revision:2});
  await okPromise;
  await Promise.resolve();
  await Promise.resolve();
  assert.equal(state.refreshErrors.length, 1);
  assert.equal(state.refreshErrors[0][1], 'storage offline');
});

test('AC6: directory-settings.css covers permissions frame, removed state, light theme, and responsive layout', () => {
  assert.match(directoryCss, /\.permissions-settings-page\s+\.directory-page/);
  assert.match(directoryCss, /\.permissions-settings-frame/);
  assert.match(directoryCss, /\.allowed-root-row--removed/);
  assert.match(directoryCss, /html\[data-theme="light"\]\s*\.directory-page/);
  assert.match(directoryCss, /@media\(max-width:\s*700px\)/);
  assert.match(organize, /invalidateAllowedRootMutation\(\)/);
});
