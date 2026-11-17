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

function loadResourceFolderDisplayName() {
  const source = sliceBetween(settings, 'function resourceFolderDisplayName(root)', 'function renderPurgeConfirmModal()');
  return new Function(`${source}; return resourceFolderDisplayName;`)();
}

function loadSettingsRenderer(roots) {
  const source = sliceBetween(settings, 'function renderAllowedRootsSettings()', 'function renderGlobalDirectorySettings()');
  const factory = new Function(
    'allowedRootSettingsState','canManageAllowedRoots','escapeAttr','escapeHtml','pendingPurgeRoot',
    'renderPurgeConfirmModal','canBrowseServerDirectories','ALLOWED_ROOT_FIELD_KEY','resourceFolderDisplayName',
    `${source}; return renderAllowedRootsSettings;`
  );
  return factory(
    {roots, saving:false, error:''}, () => true, value => String(value), value => String(value),
    null, () => '', () => false, 'allowed-root-path', loadResourceFolderDisplayName()
  )();
}

function createClickTarget(attributes) {
  return {
    attributes,
    closest() { return this; },
    getAttribute(name) { return Object.hasOwn(this.attributes, name) ? this.attributes[name] : null; },
    hasAttribute(name) { return Object.hasOwn(this.attributes, name); },
  };
}

function loadSettingsClickHandler({roots, calls=[], onCall=async () => {}}={}) {
  const start = manager.indexOf("document.addEventListener('click', event => {", manager.indexOf('async function saveAllowedRootPurge'));
  const end = manager.indexOf("document.addEventListener('change', event => {", start);
  assert.ok(start >= 0 && end > start, 'settings click listener exists');
  const source = manager.slice(start, end);
  const bodyStart = source.indexOf('=> {') + 4;
  const bodyEnd = source.lastIndexOf('});');
  assert.ok(bodyStart > 0 && bodyEnd > bodyStart, 'click listener body is bounded');
  const handlerSource = `const handler = async event => {${source.slice(bodyStart, bodyEnd)}};`;
  const factory = new Function(
    'allowedRootSettingsState','gwCall','saveAllowedRootsRequest','window','document',
    `let pendingPurgeRoot = null; ${handlerSource}; return event => handler(event);`
  );
  const dispatch = factory(
    {roots:roots || [{id:'root_0123456789abcdef01234567',path:'D:/Media/Film'}],revision:3,saving:false},
    (...args)=>{ calls.push(args); return onCall(...args); }, async (...args)=>{ calls.push(['request',...args]); },
    {confirm:()=>true}, {getElementById:()=>null}
  );
  return {calls, dispatch};
}

function loadDirectorySettingsBoot({loadSettings, facets={roots:[{id:'local_extra_abc_123',aliases:['root_0123456789abcdef01234567'],path:'D:/Media/Film'}]}}={}) {
  const bootStart = manager.indexOf("document.addEventListener('DOMContentLoaded', () => {");
  const bootEnd = manager.indexOf('    // 【QIN-573 AC2】', bootStart);
  assert.ok(bootStart >= 0 && bootEnd > bootStart, 'directory settings boot branch exists');
  const bootBody = manager.slice(manager.indexOf('=> {', bootStart) + 4, bootEnd);
  const loaderSource = sliceBetween(manager, 'async function loadGlobalDirectorySettings()', 'let pendingPurgeRoot = null;');
  const loaderFactory = new Function(
    'storageSettingsState','directorySettingsError','directorySettingsReady','gwCall','loadRegistryPrincipal',
    'canManageProjectWorkspaceSettings','apiJsonResponse','assetManagerApi','canManageAllowedRoots',
    'assetManagerHttp','applyStorageSettingsData','registryStatus','applyThumbnailSettingsSnapshot',
    'isTauriClientMode','loadClientUploadSourceDir','currentStatusText','setStatus','registryFacets',
    `${loaderSource}; return {load:loadGlobalDirectorySettings, getFacets:()=>registryFacets};`
  );
  const state = {facets:{roots:[]}, calls:[], settings:{roots:[]}};
  const loaderRuntime = loaderFactory(
    {open:false}, '', false, (domain, method, ...args) => {
      state.calls.push([domain,method,...args]);
      if(domain === 'settings' && method === 'applyAllowedRootSettingsData') state.settings = args[0];
    }, async () => {}, () => true,
    async request => request?.url?.includes('/api/storage-settings/roots')
      ? {roots:[{id:'root_0123456789abcdef01234567',path:'D:/Media/Film'}],revision:4}
      : request?.url?.includes('/api/asset-registry/facets')
        ? facets
        : request,
    {getStorageSettings:()=>({kind:'storage'}),getThumbnailSettings:()=>({kind:'thumbnail'}),getRegistryStatus:()=>({kind:'status'}),getRegistryFacets:()=>({url:'/api/asset-registry/facets'})},
    () => true,
    {request: async url => ({url,roots:[{id:'root_0123456789abcdef01234567',path:'D:/Media/Film'}],revision:4})},
    () => {}, {overview:{roots:[]}}, () => {}, () => false, async () => {}, '', () => {}, state.facets
  );
  const loader = loaderRuntime.load;
  Object.defineProperty(state, 'loadedFacets', {get:()=>loaderRuntime.getFacets()});
  const bootFactory = new Function('directorySettingsPage','openDeferredDomainGate','loadGlobalDirectorySettings',
    `const callback = () => {${bootBody}}; return callback;`);
  const boot = bootFactory(true, () => state.calls.push(['gate']), () => {
    state.calls.push(['load-settings']);
    state.loaderPromise = loadSettings ? loadSettings(loader) : loader();
    return state.loaderPromise;
  });
  return {state, loader, boot};
}

function loadFacetRefresh() {
  const source = sliceBetween(manager, 'let registryFacetRefreshGeneration = 0;', 'function registryStatusJobKey');
  const factory = new Function('apiJsonResponse','assetManagerApi','registryFacets',
    `${source}; return {refreshRegistryFacetData, get:()=>registryFacets};`);
  let requestQueue = [];
  const state = {roots:[]};
  const runtime = factory(() => new Promise(resolve => requestQueue.push(resolve)), {getRegistryFacets:()=>({})}, state);
  return {runtime, requestQueue};
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

function loadResourceFolderActions({removeRoot = async () => ({}), purgeRoot = async () => ({}), confirms = [true], roots = [{id:'local_extra_abc_123', aliases:['root_0123456789abcdef01234567'], path:'C:/Media'}]} = {}) {
  const start = organize.indexOf('let resourceFolderActionSeq = 0;');
  const end = organize.indexOf('async function removeGeneratedSourceBoard', start);
  assert.ok(start >= 0 && end > start, 'resource-folder action block exists');
  const source = organize.slice(start, end);
  const state = {
    registryFacets: {roots:[...roots]},
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

test('runtime: settings boot fetches facets and root-only access can be revoked without a source', async () => {
  async function run({facets, expectedSourceId}) {
    const {state, boot} = loadDirectorySettingsBoot({facets});
    boot();
    await state.loaderPromise;
    assert.equal(state.loadedFacets.roots.length, facets.roots.length);
    assert.deepEqual(state.calls.slice(0, 2), [['gate'], ['load-settings']]);
    assert.deepEqual(state.settings.roots, [{id:'root_0123456789abcdef01234567',path:'D:/Media/Film'}]);
    const {actions, state:actionState} = loadResourceFolderActions({roots:state.loadedFacets.roots, confirms:[true]});
    const actionCalls = [];
    const {dispatch} = loadSettingsClickHandler({
      roots:state.settings.roots,
      calls:actionCalls,
      onCall:(domain, method, root, action) => method === 'executeResourceFolderAction' && actions.executeResourceFolderAction(root, action),
    });
    const remove = {target:createClickTarget({'data-resource-folder-remove':'root_0123456789abcdef01234567'}),preventDefault(){ this.defaultPrevented = true; }};
    await dispatch(remove);
    assert.equal(remove.defaultPrevented, true);
    assert.equal(actionCalls.find(call => call[1] === 'executeResourceFolderAction')?.[3], 'remove');
    assert.deepEqual(actionState.removals, expectedSourceId ? [[expectedSourceId]] : []);
    assert.deepEqual(actionState.allowedRootRemovals, ['root_0123456789abcdef01234567']);
  }
  await run({facets:{roots:[]}});
  await run({facets:{roots:[{id:'local_extra_abc_123',aliases:['root_0123456789abcdef01234567'],path:'D:/Media/Film'}]},expectedSourceId:'local_extra_abc_123'});
});

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

test('runtime: actual settings renderer derives folder title and retains full path and status', () => {
  const roots = [
    {id:'root_1',path:'D:\\\\Media\\\\Film',name:'',active:true,availability:'available',readOnly:false,aliases:[],aliasCandidates:[]},
    {id:'root_2',path:'C:\\\\',name:'',active:true,availability:'offline',readOnly:false,aliases:[],aliasCandidates:[]},
    {id:'root_3',path:'/',name:'',active:true,availability:'removed',readOnly:false,aliases:[],aliasCandidates:[]},
    {id:'root_4',path:'',name:'',active:false,availability:'removed',readOnly:true,aliases:[],aliasCandidates:[]},
  ];
  const markup = loadSettingsRenderer(roots);
  assert.ok(markup.includes('allowed-root-row'), markup.slice(0, 1600));
  assert.ok(markup.includes('<strong>Film</strong>'));
  assert.ok(markup.includes('title="D:\\\\Media\\\\Film">D:\\\\Media\\\\Film</code>'));
  assert.ok(markup.includes('<strong>C: 盘</strong>'));
  assert.ok(markup.includes('<strong>根目录 (/)</strong>'));
  assert.equal(loadResourceFolderDisplayName()({path:'',readOnly:true}), '资源文件夹');
  assert.match(markup, /在线/);
  assert.match(markup, /离线/);
  assert.match(markup, /已移除/);
  assert.doesNotMatch(markup, /rename|data-resource-folder-name-edit/i);
});

test('runtime: actual settings click listener routes purge prompt confirmation to shared action', async () => {
  const calls = [];
  const {dispatch} = loadSettingsClickHandler({calls});
  const prompt = {target:createClickTarget({'data-storage-root-purge-prompt':'root_0123456789abcdef01234567','data-root-path':'D:/Media/Film'}), preventDefault(){ this.defaultPrevented = true; }};
  await dispatch(prompt);
  assert.equal(prompt.defaultPrevented, true);

  const confirm = {target:createClickTarget({'data-purge-confirm':'root_0123456789abcdef01234567'}), preventDefault(){}};
  await dispatch(confirm);
  const actionCall = calls.find(call => call[1] === 'executeResourceFolderAction');
  assert.ok(actionCall, 'confirmed purge routes to the shared action');
  assert.equal(actionCall[2].id, 'root_0123456789abcdef01234567');
  assert.equal(actionCall[3], 'purge');
  assert.deepEqual(actionCall[4], {firstConfirmationSatisfied:true});
});

test('runtime: cancelled purge prompt does not dispatch a destructive action', () => {
  const calls = [];
  const {dispatch} = loadSettingsClickHandler({calls});
  const cancel = {target:createClickTarget({'data-purge-cancel':''}), preventDefault(){ this.defaultPrevented = true; }};
  dispatch(cancel);
  assert.equal(cancel.defaultPrevented, true);
  assert.deepEqual(calls, [['settings','renderGlobalDirectorySettings']]);
});

test('runtime: delayed purge after view switch cannot publish stale success or clear current state', async () => {
  let resolvePurge;
  const {actions, state} = loadResourceFolderActions({
    purgeRoot:() => new Promise(resolve => { resolvePurge = resolve; }),
    confirms:[true, true],
  });
  const pending = actions.executeResourceFolderAction({id:'root_0123456789abcdef01234567',path:'D:/Media/Film'}, 'purge');
  await Promise.resolve();
  assert.equal(typeof resolvePurge, 'function');
  actions.invalidateResourceFolderAction();
  resolvePurge({});
  assert.equal((await pending).status, 'stale');
  assert.deepEqual(state.statuses, []);
  assert.equal(state.cacheCleared, undefined);
});

test('runtime: concurrent facet refreshes only apply the newest response', async () => {
  const {runtime, requestQueue} = loadFacetRefresh();
  const older = runtime.refreshRegistryFacetData();
  const newer = runtime.refreshRegistryFacetData();
  assert.equal(requestQueue.length, 2);
  requestQueue[1]({roots:[{id:'new-root'}]});
  await newer;
  requestQueue[0]({roots:[{id:'old-root'}]});
  await older;
  assert.deepEqual(runtime.get().roots, [{id:'new-root'}]);
});
