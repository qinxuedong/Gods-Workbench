import assert from 'node:assert/strict';
import test from 'node:test';
import { readFileSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const read = relativePath => readFileSync(path.join(root, relativePath), 'utf8');

const assetManagerSrc = read('web/js/modules/asset-manager.js');
const settingsSrc = read('web/js/modules/asset-manager/settings.js');
const inboxSrc = read('web/js/modules/asset-manager/inbox.js');
const uploadSrc = read('web/js/modules/asset-manager/upload.js');

function extractFunction(source, name) {
  const asyncMarker = `async function ${name}(`;
  const marker = `function ${name}(`;
  let start = source.indexOf(asyncMarker);
  if (start === -1) start = source.indexOf(marker);
  assert.notEqual(start, -1, `未找到函数 ${name}`);
  const braceStart = source.indexOf('{', start);
  let depth = 0;
  for (let i = braceStart; i < source.length; i += 1) {
    const ch = source[i];
    if (ch === '{') depth += 1;
    else if (ch === '}') {
      depth -= 1;
      if (depth === 0) return source.slice(start, i + 1);
    }
  }
  throw new Error(`函数 ${name} 大括号未闭合`);
}

test('AC1: assetTreeGlobalRootsList 仅排除内置本地素材（id=local）占位挂载，保留同名自定义根与用户挂载根', () => {
  const fnSrc = extractFunction(assetManagerSrc, 'assetTreeGlobalRootsList');
  const factory = new Function(
    'assetTreeResolveRootPath',
    'pathCompareKey',
    'registryFacets',
    'localLibraries',
    'allowedRootSettingsState',
    `${fnSrc}; return assetTreeGlobalRootsList;`,
  );

  const rootsFn = factory(
    (id) => (id === 'local' ? '/srv/local' : (id === 'root-user' ? '/srv/user' : (id === 'custom-name-only' ? '/srv/custom-local' : ''))),
    (p) => String(p || '').toLowerCase(),
    {
      roots: [
        { id: 'local', name: '本地素材', count: 0, availability: 'available', path: '/srv/local' },
        { id: 'video', name: '视频库素材', count: 3, availability: 'available', path: '/srv/video' },
        { id: 'root-user', name: '拍摄素材库', count: 12, availability: 'available', path: '/srv/user' },
      ],
    },
    [
      { id: 'local', name: '本地素材', path: '/srv/local' },
      { id: 'custom-name-only', name: '本地素材', path: '/srv/custom-local' },
    ],
    {
      roots: [
        { id: 'local', active: true, availability: 'available', path: '/srv/local' },
        { id: 'root-user', active: true, availability: 'available', path: '/srv/user' },
      ],
    },
  );

  const list = rootsFn();
  assert.equal(list.length, 2);
  assert.deepEqual(list.map(r => r.id), ['root-user', 'custom-name-only']);
  assert.equal(list[1].name, '本地素材');
});

test('AC1: 收集箱在客户端模式下支持原生上传选择器与来源目录设置', async () => {
  assert.match(
    assetManagerSrc,
    /\[data-inbox-upload\][\s\S]*?isTauriClientMode\(\)[\s\S]*?gwCall\('upload', 'uploadViaClientPicker'\)/,
  );
  assert.match(
    assetManagerSrc,
    /\[data-inbox-settings-toggle\][\s\S]*?loadClientUploadSourceDir/,
  );

  // 行为验证：uploadViaClientPicker 在 activeTab === 'inbox' 时调用 uploadAssetInboxFiles
  const pickerFnSrc = extractFunction(uploadSrc, 'uploadViaClientPicker');
  const uploadedBatches = [];
  const invokeCalls = [];
  const factory = new Function(
    'tauriInvokeFn',
    'uploadInput',
    'clientUploadSourceDir',
    'activeTab',
    'uploadAssetInboxFiles',
    'uploadLocalAssets',
    'uploadWorkflowFiles',
    'uploadFiles',
    'setStatus',
    'File',
    `${pickerFnSrc}; return uploadViaClientPicker;`,
  );
  class FakeFile {
    constructor(parts, name) {
      this.parts = parts;
      this.name = name;
    }
  }
  const uploadViaClientPicker = factory(
    () => async (cmd, payload) => {
      invokeCalls.push([cmd, payload]);
      if (cmd === 'pick_client_upload_files') return ['C:\\clips\\shot01.png'];
      if (cmd === 'read_client_file') return new Uint8Array([1, 2, 3]);
      return null;
    },
    null,
    'C:\\clips',
    'inbox',
    async (files) => {
      uploadedBatches.push(files);
    },
    () => { throw new Error('不应走 uploadLocalAssets'); },
    async () => { throw new Error('不应走 uploadWorkflowFiles'); },
    async () => { throw new Error('不应走 uploadFiles'); },
    () => {},
    FakeFile,
  );

  await uploadViaClientPicker();
  assert.deepEqual(invokeCalls[0], ['pick_client_upload_files', { defaultDir: 'C:\\clips' }]);
  assert.equal(uploadedBatches.length, 1);
  assert.equal(uploadedBatches[0][0].name, 'shot01.png');

  // 行为验证：renderInboxSettingsPanel 在客户端模式下渲染客户端上传来源目录输入与选夹按钮
  const panelFnSrc = extractFunction(inboxSrc, 'renderInboxSettingsPanel');
  const panelFactory = new Function(
    'inboxSettings',
    'isTauriClientMode',
    'clientUploadSourceDir',
    'escapeAttr',
    `${panelFnSrc}; return renderInboxSettingsPanel;`,
  );
  const tauriHtml = panelFactory({ path: '/srv/inbox' }, () => true, 'C:\\source', (v) => String(v ?? ''))();
  assert.match(tauriHtml, /id="clientUploadSourceDir"/);
  assert.match(tauriHtml, /value="C:\\source"/);
  assert.match(tauriHtml, /data-client-directory-picker="upload_source"/);

  const webHtml = panelFactory({ path: '/srv/inbox' }, () => false, '', (v) => String(v ?? ''))();
  assert.doesNotMatch(webHtml, /id="clientUploadSourceDir"/);
});

test('AC2 & AC3: 目录设置页下线冗余卡片并将缓存目录移动到项目工作区正下方、工作区上方', () => {
  const renderFnSrc = extractFunction(settingsSrc, 'renderGlobalDirectorySettings');
  const host = {
    innerHTML: '',
    contains: () => false,
    querySelector: () => null,
  };
  const factory = new Function(
    'captureWorkspaceMappingForm',
    'document',
    'permissionsSettingsPage',
    'directorySettingsSaving',
    'directorySettingsError',
    'directorySettingsReady',
    'directorySettingsPage',
    'currentStatusText',
    'escapeHtml',
    'renderDataLocationBanner',
    'renderDataLocationCard',
    'renderProjectWorkspaceSettingsBody',
    'renderAssetThumbnailCacheDirectoryBody',
    'renderWorkspaceSettingsBody',
    'renderAllowedRootsSettings',
    'renderServerDirectoryBrowser',
    'refreshIcons',
    'requestAnimationFrame',
    'ensureDataLocationBindings',
    `${renderFnSrc}; return renderGlobalDirectorySettings;`,
  );

  const renderGlobalDirectorySettings = factory(
    () => {},
    {
      getElementById: (id) => (id === 'storageSettingsOverlay' ? host : null),
      activeElement: null,
    },
    false,
    false,
    '',
    true,
    true,
    '目录已同步',
    (v) => String(v ?? ''),
    () => '',
    () => '<section data-card="data-location">数据位置</section>',
    () => '<section data-card="project-workspace">项目工作区</section>',
    () => '<div data-card="thumbnail-cache">缓存目录</div>',
    () => '<section data-card="workspaces">工作区</section>',
    () => '',
    () => '',
    () => {},
    () => {},
    () => {},
  );

  renderGlobalDirectorySettings();
  const html = host.innerHTML;

  // AC2: 不再渲染冗余卡片与输入框
  assert.doesNotMatch(html, /客户端与默认目录/);
  assert.doesNotMatch(html, /上传与生成/);
  assert.doesNotMatch(html, /storageDir_local|storageDir_upload|storageDir_generated|clientUploadSourceDir/);

  // AC3: 左列仅数据位置，右列顺序为 项目工作区 -> 缓存目录 -> 工作区
  const leftStart = html.indexOf('directory-cards-column--left');
  const rightStart = html.indexOf('directory-cards-column--right');
  const leftHtml = html.slice(leftStart, rightStart);
  const rightHtml = html.slice(rightStart);

  assert.match(leftHtml, /data-card="data-location"/);
  assert.doesNotMatch(leftHtml, /data-card="thumbnail-cache"/);

  const idxProjectWs = rightHtml.indexOf('data-card="project-workspace"');
  const idxCache = rightHtml.indexOf('data-card="thumbnail-cache"');
  const idxWorkspaces = rightHtml.indexOf('data-card="workspaces"');
  assert.ok(idxProjectWs >= 0 && idxCache > idxProjectWs && idxWorkspaces > idxCache, '右列顺序应为 项目工作区 -> 缓存目录 -> 工作区');

  // AC2: syncStorageSettingsInputsToState 不再读取已下线的 upload/generated/local
  const syncFnSrc = extractFunction(settingsSrc, 'syncStorageSettingsInputsToState');
  assert.doesNotMatch(syncFnSrc, /\['upload','generated','local','trash'\]/);
  assert.doesNotMatch(assetManagerSrc, /\['upload','generated','local','projects'\]\.some/);
});
