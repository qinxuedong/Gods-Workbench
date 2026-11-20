// [QIN-625] 资源文件夹选择、可达性预检与异步登记交互回归
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

const manager = readFileSync(new URL('../../web/js/modules/asset-manager.js', import.meta.url), 'utf8');
const organize = readFileSync(new URL('../../web/js/modules/asset-manager/organize.js', import.meta.url), 'utf8');
const settings = readFileSync(new URL('../../web/js/modules/asset-manager/settings.js', import.meta.url), 'utf8');
const css = readFileSync(new URL('../../web/css/pages/asset-manager.css', import.meta.url), 'utf8');
const acceptedIndexStart = organize.indexOf('function acceptedAssetIndexJobId(');
const acceptedIndexEnd = organize.indexOf('\nasync function attachPathToAssetBoard', acceptedIndexStart);
const confirmStart = organize.indexOf('async function confirmBoardSource(');
const confirmEnd = organize.indexOf('\nasync function refreshCASReadState', confirmStart);
const attachStart = organize.indexOf('async function attachPathToAssetBoard(');
const attachEnd = organize.indexOf('\nlet boardSourceFocusTarget', attachStart);
const waitStart = manager.indexOf('async function waitForAssetIndexJob(');
const waitEnd = manager.indexOf('\nasync function copyTextToClipboard', waitStart);
assert.ok(acceptedIndexStart >= 0 && acceptedIndexEnd > acceptedIndexStart);
assert.ok(confirmStart >= 0 && confirmEnd > confirmStart);
assert.ok(attachStart >= 0 && attachEnd > attachStart);
assert.ok(waitStart >= 0 && waitEnd > waitStart);
const acceptedIndexSource = organize.slice(acceptedIndexStart, acceptedIndexEnd);
const confirmSource = organize.slice(confirmStart, confirmEnd);
const attachSource = organize.slice(attachStart, attachEnd);
const waitSource = manager.slice(waitStart, waitEnd);

const pickerStart = manager.indexOf("if(target.closest?.('[data-board-source-pick]')){");
const pickerEnd = manager.indexOf("if(target.closest?.('[data-board-source-new]')){", pickerStart);
assert.ok(pickerStart >= 0 && pickerEnd > pickerStart, '资源文件夹选择入口必须存在');
const pickerHandler = manager.slice(pickerStart, pickerEnd);

function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((res, rej) => { resolve = res; reject = rej; });
  return { promise, resolve, reject };
}

function pickerHarness({ select = true, validate = async path => path } = {}) {
  const state = { open: true, generation: 0, path: '', error: '', picking: false, validating: false };
  const calls = { validate: 0, confirm: 0, render: 0, status: [], options: null };
  const target = { closest: selector => selector === '[data-board-source-pick]' ? {} : null };
  const gwCall = (domain, name, ...args) => {
    if (domain === 'organize' && name === 'renderBoardSourceModal') {
      calls.render += 1;
      return undefined;
    }
    if (domain === 'settings' && name === 'pickServerDirectoryNative') {
      const options = args[1];
      return select ? options.onSelect('Z:/shared/library') : undefined;
    }
    if (domain === 'settings' && name === 'validateServerResourceDirectory') {
      calls.validate += 1;
      return validate(args[0]);
    }
    if (domain === 'organize' && name === 'confirmBoardSource') {
      calls.confirm += 1;
      calls.options = args[0];
      return Promise.resolve();
    }
    throw new Error(`未预期的异步模块调用：${domain}.${name}`);
  };
  const run = new Function('target', 'boardSourceState', 'gwCall', 'setStatus', `
    return (async () => { ${pickerHandler} })();
  `);
  return {
    state,
    calls,
    run: () => run(target, state, gwCall, message => calls.status.push(message)),
  };
}

test('服务端目录可达性预检只读，并采用服务端规范化路径', async () => {
  const start = settings.indexOf('async function validateServerResourceDirectory(');
  const end = settings.indexOf('\nasync function openServerDirectoryBrowser', start);
  assert.ok(start >= 0 && end > start);
  const source = settings.slice(start, end);
  let request;
  const validate = new Function('assetManagerHttp', 'apiJsonResponse', `${source}\nreturn validateServerResourceDirectory;`)(
    {
      request: (url, options) => {
        request = { url, options };
        return Promise.resolve({ path: 'Z:/shared/library' });
      },
    },
    async response => response,
  );

  assert.equal(await validate(' Z:/shared/library '), 'Z:/shared/library');
  assert.match(request.url, /^\/api\/storage-settings\/browse\?path=/);
  assert.equal(request.options.method, 'GET');
  assert.equal(request.options.body, undefined);
  await assert.rejects(() => validate('  '), /请选择资源文件夹/);
});

test('取消选择不发预检或登记请求', async () => {
  const harness = pickerHarness({ select: false });
  await harness.run();
  assert.equal(harness.calls.validate, 0);
  assert.equal(harness.calls.confirm, 0);
  assert.equal(harness.state.picking, false);
});

test('1500ms 与 5000ms 慢预检期间显示检查状态，完成前不提交', { timeout: 15000 }, async () => {
  for (const delayMs of [1500, 5000]) {
    const gate = deferred();
    const entered = deferred();
    const harness = pickerHarness({
      validate: path => {
        entered.resolve();
        return gate.promise.then(() => path);
      },
    });
    const pending = harness.run();
    await entered.promise;
    assert.equal(harness.state.validating, true);
    assert.equal(harness.calls.confirm, 0);
    await new Promise(resolve => setTimeout(resolve, delayMs));
    assert.equal(harness.state.validating, true);
    assert.equal(harness.calls.confirm, 0);
    gate.resolve();
    await pending;
    assert.equal(harness.calls.confirm, 1);
    assert.deepEqual(harness.calls.options, { atomicRegistration: true });
  }

  assert.match(organize, /正在检查服务端目录访问权限；关闭可取消，检查不会写入授权或来源/);
  assert.match(organize, /boardSourceState\.saving \? 'disabled' : ''/);
});

test('关闭弹窗后丢弃迟到的可达性响应', async () => {
  const gate = deferred();
  const entered = deferred();
  const harness = pickerHarness({
    validate: path => {
      entered.resolve();
      return gate.promise.then(() => path);
    },
  });
  const pending = harness.run();
  await entered.promise;
  harness.state.open = false;
  harness.state.generation += 1;
  harness.state.validating = false;
  gate.resolve();
  await pending;
  assert.equal(harness.calls.confirm, 0);
  assert.equal(harness.state.error, '');
});

test('后端不可达提示不触发登记；受理文案保持 pending 语义', async () => {
  const harness = pickerHarness({
    validate: async () => { throw Object.assign(new Error('backend unavailable'), { status: 503 }); },
  });
  await harness.run();
  assert.equal(harness.calls.confirm, 0);
  assert.equal(harness.state.error, '当前文件夹后端不可访问，需授权（共享）给后端');
  assert.match(organize, /已受理资源文件夹.*正在建立索引/);
  assert.doesNotMatch(organize, /已添加完成/);
});

function confirmHarness({ reindexResult, selectedId = 'source-1', sourcePath = 'Z:/shared/library', attachPathToAssetBoard, waitForAssetIndexJob } = {}) {
  const library = { id: 'source-1', name: '已有来源', path: 'Z:/shared/library' };
  const localLibraries = [library];
  const state = {
    open: true,
    generation: 7,
    existingId: selectedId,
    name: '已有来源',
    path: sourcePath,
    error: '',
    saving: false,
    picking: false,
    validating: false,
  };
  const calls = { reindex: [], close: 0, renderModal: 0, status: [], wait: [], waitPromise: null };
  const fields = {
    boardSourceExisting: { value: selectedId },
    boardSourceName: { value: '已有来源' },
    boardSourcePath: { value: sourcePath },
  };
  const run = new Function(
    'boardSourceState', 'document', 'localLibraries', 'storageCleanName', 'reindexAssetSource',
    'renderBoardSourceModal', 'attachPathToAssetBoard', 'closeBoardSourceModal', 'assetAllowedRootPaths',
    'loadAssetAllowedRootPaths', 'refreshRegistryFacetData', 'notifyShellTreeSnapshot', 'render', 'setStatus', 'waitForAssetIndexJob',
    `${acceptedIndexSource}\n${confirmSource}\nreturn confirmBoardSource;`,
  )(
    state,
    { getElementById: id => fields[id] || null },
    localLibraries,
    (name, fallback) => name || fallback,
    async id => { calls.reindex.push(id); return reindexResult; },
    () => { calls.renderModal += 1; },
    attachPathToAssetBoard || (async () => { throw new Error('意外进入新来源登记'); }),
    () => { calls.close += 1; state.generation += 1; state.open = false; state.saving = false; state.error = ''; },
    new Map(),
    () => Promise.resolve(),
    () => Promise.resolve(),
    () => {},
    () => {},
    message => calls.status.push(message),
    (jobId, sourceName) => {
      calls.wait.push({ jobId, sourceName });
      calls.waitPromise = waitForAssetIndexJob ? waitForAssetIndexJob(jobId, sourceName) : Promise.resolve(null);
      return calls.waitPromise;
    },
  );
  return { library, localLibraries, state, calls, run };
}

function failedScanWaitHarness(scanJob, status) {
  const calls = { polls: [], indexStates: [], renders: 0 };
  const wait = new Function(
    'apiJsonResponse', 'assetManagerApi', 'setWorkspaceTaskContext', 'markIndexState', 'refreshRegistryAssets', 'refreshRegistryFacetData',
    'activeTab', 'assetBoardById', 'render', 'setStatus', 'loadAssetAllowedRootPaths',
    `${waitSource}\nreturn waitForAssetIndexJob;`,
  )(
    async response => response,
    { getWorkspaceJob: jobId => { calls.polls.push(jobId); return Promise.resolve({ job: scanJob }); } },
    () => {},
    value => calls.indexStates.push(value),
    async () => null,
    async () => null,
    'registry',
    () => null,
    () => { calls.renders += 1; },
    status,
    async () => null,
  );
  return { calls, wait };
}

function knownSourceAttach({ localLibraries, reindexAssetSource, status }) {
  return new Function(
    'ensureCreateLibraryPathAllowedRoot', 'apiJsonResponse', 'assetManagerApi', 'applyStorageSettingsData',
    'pathCompareKey', 'localLibraries', 'storageCleanName', 'setStatus', 'reindexAssetSource',
    `${acceptedIndexSource}\n${attachSource}\nreturn attachPathToAssetBoard;`,
  )(
    async () => true,
    async response => response,
    { getStorageSettings: () => Promise.resolve({}) },
    () => {},
    value => String(value || '').replace(/\\/g, '/').toLowerCase(),
    localLibraries,
    (name, fallback) => name || fallback,
    status,
    reindexAssetSource,
  );
}

test('已有来源索引启动失败或缺少 job_id 时保留选择并显示错误', async () => {
  const cases = [
    { result: { ok: false, error: 'request rejected' }, error: 'request rejected' },
    { result: { ok: true, data: {} }, error: '索引任务未返回有效任务编号' },
  ];
  for (const item of cases) {
    const harness = confirmHarness({ reindexResult: item.result });
    const before = structuredClone(harness.localLibraries);
    await harness.run();
    assert.equal(harness.state.open, true);
    assert.equal(harness.state.saving, false);
    assert.equal(harness.state.existingId, 'source-1');
    assert.equal(harness.state.path, 'Z:/shared/library');
    assert.equal(harness.state.error, item.error);
    assert.equal(harness.calls.close, 0);
    assert.deepEqual(harness.calls.wait, []);
    assert.equal(harness.calls.status.includes('已受理资源文件夹“已有来源”，正在建立索引'), false);
    assert.deepEqual(harness.localLibraries, before);
  }
});

test('重复添加已登记路径的索引启动失败不伪报受理', async () => {
  const calls = { reindex: [], status: [] };
  const harness = confirmHarness({
    selectedId: '',
    reindexResult: { ok: false, error: 'request rejected' },
    attachPathToAssetBoard: null,
  });
  const attach = knownSourceAttach({
    localLibraries: harness.localLibraries,
    reindexAssetSource: async id => { calls.reindex.push(id); return { ok: false, error: 'request rejected' }; },
    status: message => calls.status.push(message),
  });
  const direct = new Function(
    'boardSourceState', 'document', 'localLibraries', 'storageCleanName', 'reindexAssetSource',
    'renderBoardSourceModal', 'attachPathToAssetBoard', 'closeBoardSourceModal', 'assetAllowedRootPaths',
    'loadAssetAllowedRootPaths', 'refreshRegistryFacetData', 'notifyShellTreeSnapshot', 'render', 'setStatus', 'waitForAssetIndexJob',
    `${acceptedIndexSource}\n${confirmSource}\nreturn confirmBoardSource;`,
  )(
    harness.state,
    { getElementById: id => ({ boardSourceExisting: { value: '' }, boardSourceName: { value: '已有来源' }, boardSourcePath: { value: 'Z:/shared/library' } })[id] || null },
    harness.localLibraries,
    (name, fallback) => name || fallback,
    async id => { calls.reindex.push(id); return { ok: false, error: 'request rejected' }; },
    () => {},
    attach,
    () => { harness.calls.close += 1; harness.state.open = false; },
    new Map(),
    () => Promise.resolve(),
    () => Promise.resolve(),
    () => {},
    () => {},
    message => { calls.status.push(message); harness.calls.status.push(message); },
    () => { harness.calls.wait.push(true); return Promise.resolve(null); },
  );
  await direct({ atomicRegistration: true });
  assert.deepEqual(calls.reindex, ['source-1']);
  assert.equal(harness.state.open, true);
  assert.equal(harness.state.error, 'request rejected');
  assert.equal(harness.calls.close, 0);
  assert.equal(harness.calls.wait.length, 0);
  assert.equal(harness.calls.status.includes('已受理资源文件夹“已有来源”，正在建立索引'), false);
});

test('已有来源的 202 任务后续失败会轮询并显示终态错误', async () => {
  const status = [];
  const waitHarness = failedScanWaitHarness({ status: 'failed', error_code: 'SCAN_FAILED' }, message => status.push(message));
  const harness = confirmHarness({
    reindexResult: { ok: true, data: { job_id: 'job-accepted-1' } },
    waitForAssetIndexJob: waitHarness.wait,
  });
  const before = structuredClone(harness.localLibraries);
  await harness.run();
  assert.equal(harness.state.open, false);
  assert.equal(harness.calls.close, 1);
  assert.deepEqual(harness.calls.wait, [{ jobId: 'job-accepted-1', sourceName: '已有来源' }]);
  await harness.calls.waitPromise;
  assert.deepEqual(waitHarness.calls.polls, ['job-accepted-1']);
  assert.deepEqual(waitHarness.calls.indexStates, ['failed']);
  assert.equal(status.at(-1), '素材库“已有来源”索引失败：SCAN_FAILED');
  assert.equal(harness.calls.status.includes('已受理资源文件夹“已有来源”，正在建立索引'), true);
  assert.deepEqual(harness.localLibraries, before);
});

test('选择按钮与状态说明可见，焦点态仍保留', () => {
  assert.match(organize, /data-board-source-pick aria-label="选择文件夹"/);
  assert.match(organize, /role="status" aria-live="polite"/);
  assert.match(css, /\.board-source-path-row \{ display:grid; grid-template-columns:minmax\(0,1fr\) 38px;/);
  assert.match(css, /:focus-visible/);
  assert.match(organize, /waitForAssetIndexJob\(source\.job_id, source\.name\)/);
});
