// [QIN-637] 安装包添加资源文件夹选夹（AC-A）与侧栏已添加资源根右键菜单（AC-B）回归
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

const manager = readFileSync(new URL('../../web/js/modules/asset-manager.js', import.meta.url), 'utf8');
const organize = readFileSync(new URL('../../web/js/modules/asset-manager/organize.js', import.meta.url), 'utf8');
const settings = readFileSync(new URL('../../web/js/modules/asset-manager/settings.js', import.meta.url), 'utf8');

function extractBlock(source, startMarker, endMarker) {
  const start = source.indexOf(startMarker);
  const end = source.indexOf(endMarker, start);
  assert.ok(start >= 0 && end > start, `未能提取代码块: ${startMarker}`);
  return source.slice(start, end);
}

const createAbortControllerSrc = extractBlock(
  settings,
  'function _createDirectoryAbortController(',
  '\nasync function _loadServerDirectoryBrowserImpl(',
);
const pickServerDirectoryNativeSrc = extractBlock(
  settings,
  'async function pickServerDirectoryNative(',
  '\nasync function pickClientDirectoryNative(',
);
const loadServerDirectoryBrowserSrc = extractBlock(
  settings,
  'async function _loadServerDirectoryBrowserImpl(',
  '\nasync function _validateServerResourceDirectoryImpl(',
);
const validateServerResourceDirectorySrc = extractBlock(
  settings,
  'async function validateServerResourceDirectory(',
  '\nasync function openServerDirectoryBrowser(',
);
const renderServerDirectoryBrowserHostSrc = extractBlock(
  settings,
  'function renderServerDirectoryBrowserHost(',
  '\nasync function loadServerDirectoryBrowser(',
);
const pickerHandlerSrc = extractBlock(
  manager,
  "if(target.closest?.('[data-board-source-pick]')){",
  "if(target.closest?.('[data-board-source-new]')){",
);

const loadAssetAllowedRootPathsSrc = extractBlock(
  manager,
  'async function loadAssetAllowedRootPaths(',
  '\nfunction assetRackRootDiskPath(',
);
const assetRackRootDiskPathSrc = extractBlock(
  manager,
  'function assetRackRootDiskPath(',
  '\nfunction assetRackDiskDirectories(',
);
const assetRackRootsSrc = extractBlock(
  manager,
  'function assetRackRoots(',
  '\nfunction assetRackRegisteredFoldersByRoot(',
);
const serializeNodeSrc = extractBlock(
  manager,
  'function serializeAssetRackFolderNode(',
  '\nfunction gwAssetTreeSnapshot(',
);
const gwAssetTreeSnapshotSrc = extractBlock(
  manager,
  'function gwAssetTreeSnapshot(',
  '\nfunction expandAssetTreeAncestors(',
);

test('AC-A1/A2/A4: 本机回环原生选夹不可用时不降级页面内盘符浏览器，仅远程 403 PICKER_NON_LOOPBACK 降级', async () => {
  const statuses = [];
  let browserOpened = 0;
  let unavailableReason = '';
  let selectedPath = '';
  let cancelledCount = 0;

  const makeRunner = requestImpl => {
    const assetManagerHttp = { request: requestImpl };
    const apiJsonResponse = async response => {
      const res = await response;
      if (!res.ok) {
        const err = new Error(res.detail?.message || '操作失败');
        err.status = res.status;
        err.code = res.detail?.code || null;
        throw err;
      }
      return res.json();
    };
    const directoryFieldInput = () => null;
    const setStatus = msg => statuses.push(msg);
    const openServerDirectoryBrowser = async () => { browserOpened += 1; };
    const fillServerDirectoryFromNative = async () => {};
    return new Function(
      'assetManagerHttp',
      'apiJsonResponse',
      'directoryFieldInput',
      'setStatus',
      'openServerDirectoryBrowser',
      'fillServerDirectoryFromNative',
      `const _createDirectoryAbortController = (${createAbortControllerSrc}); return (${pickServerDirectoryNativeSrc});`,
    )(
      assetManagerHttp,
      apiJsonResponse,
      directoryFieldInput,
      setStatus,
      openServerDirectoryBrowser,
      fillServerDirectoryFromNative,
    );
  };

  // 1) 本机回环 unavailable -> 走 onUnavailable 可见报错，不打开页面内浏览器
  const pickLoopbackUnavailable = makeRunner(async () => ({
    ok: true,
    status: 200,
    json: async () => ({ status: 'unavailable', reason: 'win32-dialog-error' }),
  }));
  await pickLoopbackUnavailable('boardSource', {
    onUnavailable: reason => { unavailableReason = reason; },
    onSelect: p => { selectedPath = p; },
    onCancel: () => { cancelledCount += 1; },
  });
  assert.equal(unavailableReason, 'win32-dialog-error');
  assert.equal(browserOpened, 0);
  assert.equal(selectedPath, '');

  // 2) 本机回环 cancelled -> 无错误、不登记、不降级
  unavailableReason = '';
  const pickCancelled = makeRunner(async () => ({
    ok: true,
    status: 200,
    json: async () => ({ status: 'cancelled' }),
  }));
  await pickCancelled('boardSource', {
    onUnavailable: reason => { unavailableReason = reason; },
    onCancel: () => { cancelledCount += 1; },
  });
  assert.equal(cancelledCount, 1);
  assert.equal(unavailableReason, '');
  assert.equal(browserOpened, 0);

  // 3) 远程会话 403 PICKER_NON_LOOPBACK -> 允许降级到页面内服务器目录浏览器
  const pickRemote = makeRunner(async () => ({
    ok: false,
    status: 403,
    detail: { code: 'PICKER_NON_LOOPBACK', message: '仅限服务器本机会话' },
  }));
  await pickRemote('boardSource', {
    onUnavailable: reason => { unavailableReason = reason; },
  });
  assert.equal(browserOpened, 1);
  assert.equal(unavailableReason, '');
});

test('AC-A1: #boardSourceDirectoryBrowserOverlay 绑定单一事件委托复用 handleClick，消除点击无效果', async () => {
  const listeners = new Map();
  let appended = null;
  let handledEvent = null;
  const fakeOverlay = {
    id: '',
    className: '',
    innerHTML: '',
    addEventListener(type, fn) { listeners.set(type, fn); },
    remove() {},
  };
  const documentMock = {
    getElementById: () => null,
    createElement: () => fakeOverlay,
    body: { appendChild: el => { appended = el; } },
  };
  const renderHost = new Function(
    'document',
    'serverDirectoryBrowserState',
    'renderServerDirectoryBrowser',
    'markAssetFloatingSurface',
    'setServerDirectoryBrowserState',
    'refreshIcons',
    'handleClick',
    'setStatus',
    'renderStorageSettingsModal',
    `return (${renderServerDirectoryBrowserHostSrc});`,
  )(
    documentMock,
    { key: 'boardSource', open: true },
    () => '<div data-server-directory-enter="D:/Assets"></div>',
    () => {},
    () => {},
    () => {},
    async event => { handledEvent = event; },
    () => {},
    () => {},
  );

  renderHost();
  assert.equal(appended, fakeOverlay);
  assert.equal(fakeOverlay.id, 'boardSourceDirectoryBrowserOverlay');
  assert.equal(typeof listeners.get('click'), 'function');
  const clickEvt = { target: { closest: () => ({ dataset: { serverDirectoryEnter: '<dir>' } }) } };
  await listeners.get('click')(clickEvt);
  assert.equal(handledEvent, clickEvt);
  assert.match(manager, /\[data-server-directory-retry\]/);
});

test('AC-A 慢请求（9 秒与 20 秒档）：3 秒内保持进行中，超过 8 秒可见失败且可重试/取消，取消丢弃迟到返回', async t => {
  t.mock.timers.enable({ apis: ['setTimeout'] });

  const makeValidate = requestImpl => {
    const apiJsonResponse = async response => (await response).json();
    return new Function(
      'assetManagerHttp',
      'apiJsonResponse',
      `const _createDirectoryAbortController = (${createAbortControllerSrc}); return (${validateServerResourceDirectorySrc});`,
    )({ request: requestImpl }, apiJsonResponse);
  };

  // 9 秒慢响应：8 秒时触发超时（3 秒时仍在途），20 秒响应同样被中止
  for (const slowMs of [9000, 20000]) {
    let settled = false;
    const validateSlow = makeValidate((_url, init) => new Promise((resolve, reject) => {
      init.signal.addEventListener('abort', () => {
        const err = new Error('Aborted');
        err.name = 'AbortError';
        reject(err);
      });
      setTimeout(() => resolve({ ok: true, json: async () => ({ path: '<late>' }) }), slowMs);
    }));

    const promise = validateSlow('<target>').then(
      () => { settled = true; return 'ok'; },
      err => { settled = true; return err; },
    );

    t.mock.timers.tick(3000);
    await Promise.resolve();
    assert.equal(settled, false, `${slowMs}ms 慢请求在 3s 时必须保持进行中`);

    t.mock.timers.tick(5050);
    const outcome = await promise;
    assert.equal(settled, true);
    assert.equal(outcome.code, 'DIRECTORY_BROWSE_TIMEOUT');
    assert.match(outcome.message, /超时/);
  }

  // 验证 loadServerDirectoryBrowser 9s 慢响应在 8s 超时并展示重试错误，且关闭/取消丢弃迟到返回
  let browserState = { open: true, generation: 0, path: null, parent: null, directories: [], loading: false, saving: false, error: '' };
  let renderCount = 0;
  const loadBrowser = new Function(
    'serverDirectoryBrowserState',
    'setServerDirectoryBrowserState',
    'renderServerDirectoryBrowserHost',
    'assetManagerHttp',
    'apiJsonResponse',
    `const _createDirectoryAbortController = (${createAbortControllerSrc}); return (${loadServerDirectoryBrowserSrc});`,
  )(
    browserState,
    next => { browserState = next; },
    () => { renderCount += 1; },
    {
      request: (_url, init) => new Promise((resolve, reject) => {
        init.signal.addEventListener('abort', () => {
          const err = new Error('Aborted');
          err.name = 'AbortError';
          reject(err);
        });
        setTimeout(() => resolve({ ok: true, json: async () => ({ path: '<late>', directories: [] }) }), 9000);
      }),
    },
    async response => (await response).json(),
  );

  const loadPromise = loadBrowser('<root>');
  t.mock.timers.tick(3000);
  await Promise.resolve();
  assert.equal(browserState.loading, true);
  t.mock.timers.tick(5050);
  await loadPromise;
  assert.equal(browserState.loading, false);
  assert.match(browserState.error, /超时/);
  assert.ok(renderCount >= 2);
});

test('AC-A 取消/关闭弹窗时 abort 选夹并按 generation 丢弃迟到响应', async () => {
  let state = { open: true, generation: 0, path: '', error: '', picking: false, validating: false, abortController: null };
  let confirmCalls = 0;
  let capturedOpts = null;
  let aborted = false;

  const runner = new Function(
    'target',
    'boardSourceState',
    'gwCall',
    'setStatus',
    `return async function() { ${pickerHandlerSrc} };`,
  )(
    { closest: sel => (sel === '[data-board-source-pick]' ? {} : null) },
    state,
    (domain, name, ...args) => {
      if (domain === 'settings' && name === 'pickServerDirectoryNative') {
        capturedOpts = args[1];
        capturedOpts.signal?.addEventListener('abort', () => { aborted = true; });
        // 模拟用户关闭弹窗导致 generation 递增和 signal 中止
        state.open = false;
        state.generation += 1;
        state.abortController?.abort();
        return capturedOpts.onSelect('<late-path>');
      }
      if (domain === 'organize' && name === 'confirmBoardSource') {
        confirmCalls += 1;
      }
      return undefined;
    },
    () => {},
  );

  await runner();
  assert.equal(aborted, true);
  assert.equal(confirmCalls, 0);
  assert.equal(state.path, '');
});

test('AC-B: 允许根冷启动失败不永久缓存、强制刷新同步树快照、roots 含 isRoot/rootId/aliases/path 且子目录 isRoot 为 false', async () => {
  // 1) 首屏 scheduleDeferredBootRequests 调用 loadAssetAllowedRootPaths
  assert.match(manager, /scheduleDeferredBootRequests[\s\S]*?loadAssetAllowedRootPaths\(\)/);
  // 2) organize.js 移出/清除索引与添加来源后均 force 刷新允许根并同步树快照
  assert.match(organize, /saveAllowedRootRemoval[\s\S]*?loadAssetAllowedRootPaths\(\{force:true\}\)/);
  assert.match(organize, /saveAllowedRootPurge[\s\S]*?loadAssetAllowedRootPaths\(\{force:true\}\)/);
  assert.match(organize, /attachPathToAssetBoard[\s\S]*?loadAssetAllowedRootPaths\(\{force:true\}\)/);

  // 3) 运行时验证 loadAssetAllowedRootPaths 首次 403 失败后不锁死空缓存，再次调用可成功并触发 notifyShellTreeSnapshot
  let attempt = 0;
  let shellNotifyCount = 0;
  let appliedSnapshot = null;
  const allowedMap = new Map();
  const apiJsonResponse = async response => {
    const res = await response;
    if (!res.ok) {
      const err = new Error('forbidden');
      err.status = res.status;
      throw err;
    }
    return res.json();
  };
  const assetManagerHttp = {
    request: async () => {
      attempt += 1;
      if (attempt === 1) return { ok: false, status: 403 };
      return {
        ok: true,
        status: 200,
        json: async () => ({
          roots: [{ id: 'root_0123456789abcdef01234567', path: '<root-disk-path>', aliases: ['local_extra_0'] }],
        }),
      };
    },
  };

  const env = {
    assetAllowedRootPaths: allowedMap,
    assetAllowedRootPathsPromise: null,
  };
  const fn = new Function(
    'env',
    'apiJsonResponse',
    'assetManagerHttp',
    'gwCall',
    'notifyShellTreeSnapshot',
    `with (env) { return (${loadAssetAllowedRootPathsSrc}); }`,
  )(
    env,
    apiJsonResponse,
    assetManagerHttp,
    (_d, _m, snap) => { appliedSnapshot = snap; },
    () => { shellNotifyCount += 1; },
  );

  await fn();
  assert.equal(allowedMap.size, 0);
  assert.equal(env.assetAllowedRootPathsPromise, null, '首次失败后不得永久缓存空 Promise');
  assert.equal(shellNotifyCount, 0);

  await fn();
  assert.equal(allowedMap.get('root_0123456789abcdef01234567'), '<root-disk-path>');
  assert.equal(allowedMap.get('local_extra_0'), '<root-disk-path>');
  assert.equal(shellNotifyCount, 1);
  assert.equal(appliedSnapshot.roots.length, 1);

  // 4) 验证 gwAssetTreeSnapshot 与 serializeAssetRackFolderNode 的 isRoot 权限边界
  const serializeNode = new Function(
    'assetTreeExpanded',
    'assetRackFolderTotalCount',
    `return (${serializeNodeSrc});`,
  )(new Set(), () => 3);
  const childNode = serializeNode('local_extra_0', { name: 'sub', path: 'sub', children: [] }, 1);
  assert.equal(childNode.isRoot, false, '子目录节点必须显式为 isRoot: false，不得扩权');

  const snapshotFn = new Function(
    'assetTreeGlobalRootsList',
    'assetTreeExpanded',
    'assetRackGlobalFolderTree',
    'registryFacets',
    'assetRackRootDiskPath',
    'assetOrganizeWritableRoot',
    'serializeAssetRackFolderNode',
    'assetInboxUnreadCount',
    'gwAssetTreeEntryKey',
    `return (${gwAssetTreeSnapshotSrc});`,
  )(
    () => [{ id: 'local_extra_0', name: '资源目录A', aliases: ['root_0123456789abcdef01234567'], count: 5 }],
    new Set(['mine:root:local_extra_0']),
    () => [{ name: 'sub', path: 'sub', children: [] }],
    { roots: [] },
    () => '<root-disk-path>',
    () => true,
    serializeNode,
    () => 0,
    () => 'mine:root:local_extra_0',
  );

  const snapshot = snapshotFn();
  assert.equal(snapshot.roots.length, 1);
  const rootItem = snapshot.roots[0];
  assert.equal(rootItem.isRoot, true);
  assert.equal(rootItem.rootId, 'local_extra_0');
  assert.deepEqual(rootItem.aliases, ['root_0123456789abcdef01234567']);
  assert.equal(rootItem.path, '<root-disk-path>');
  assert.equal(rootItem.children[0].isRoot, false);

  // 5) assetRackRoots 保留 path 且 assetRackRootDiskPath 复用 assetTreeResolveRootPath
  assert.match(assetRackRootsSrc, /path:String\(root\.path \|\| ''\)/);
  assert.match(assetRackRootDiskPathSrc, /assetTreeResolveRootPath\(id/);
});
