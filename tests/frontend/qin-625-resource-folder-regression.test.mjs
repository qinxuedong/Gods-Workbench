// [QIN-625] 资源文件夹选择、可达性预检与异步登记交互回归
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

const manager = readFileSync(new URL('../../web/js/modules/asset-manager.js', import.meta.url), 'utf8');
const organize = readFileSync(new URL('../../web/js/modules/asset-manager/organize.js', import.meta.url), 'utf8');
const settings = readFileSync(new URL('../../web/js/modules/asset-manager/settings.js', import.meta.url), 'utf8');
const css = readFileSync(new URL('../../web/css/pages/asset-manager.css', import.meta.url), 'utf8');

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

test('选择按钮与状态说明可见，焦点态仍保留', () => {
  assert.match(organize, /data-board-source-pick aria-label="选择文件夹"/);
  assert.match(organize, /role="status" aria-live="polite"/);
  assert.match(css, /\.board-source-path-row \{ display:grid; grid-template-columns:minmax\(0,1fr\) 38px;/);
  assert.match(css, /:focus-visible/);
  assert.match(organize, /waitForAssetIndexJob\(source\.job_id, source\.name\)/);
});
