// Copyright 2026 Gods-Workbench Authors
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//     http://www.apache.org/licenses/LICENSE-2.0
//
// Unless required by applicable law or agreed to in writing, software
// distributed under the License is distributed on an "AS IS" BASIS,
// WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
// See the License for the specific language governing permissions and
// limitations under the License.

/**
 * QIN-651（GitHub Issue #808）导航请求 Network-First 契约。
 *
 * - AC1：导航命中壳文档时先取服务端最新 index.html，可缓存则刷新缓存；
 *        网络/服务不可达才回退缓存，缓存也缺失时维持 503 Offline shell unavailable。
 * - AC2：CACHE_NAME 升至 gw-static-shell-v3，activate 淘汰旧代缓存。
 * - AC3：本测试自身登记到 tests/frontend/node-files.txt 与卫生准入分片。
 *
 * 全部断言以真实 web/sw.js 在 vm 内运行后的行为为准，不按源码文本猜实现。
 */

import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import path from 'node:path';
import test from 'node:test';
import { fileURLToPath } from 'node:url';
import vm from 'node:vm';

const repoRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const workerSource = readFileSync(path.join(repoRoot, 'web/sw.js'), 'utf8');
const ORIGIN = 'https://workbench.local';
const SHELL_PATH = '/static/pages/index.html';
const SHELL_URL = `${ORIGIN}${SHELL_PATH}`;
const CURRENT_CACHE = 'gw-static-shell-v3';
const LEGACY_CACHE = 'gw-static-shell-v2';

/** 构造一个只实现 sw.js 所需字段的响应替身（真实 Response 的 type 恒为 default）。 */
function shellResponse(body, overrides = {}) {
  const headers = overrides.headers || { 'content-type': 'text/html; charset=utf-8' };
  return {
    ok: overrides.ok !== undefined ? overrides.ok : true,
    status: overrides.status !== undefined ? overrides.status : 200,
    statusText: overrides.statusText || 'OK',
    type: overrides.type || 'basic',
    redirected: Boolean(overrides.redirected),
    headers: new Headers(headers),
    url: overrides.url || SHELL_URL,
    body,
    clone() {
      return shellResponse(body, { ...overrides, headers });
    },
  };
}

function navigationRequest(url = SHELL_URL) {
  return { method: 'GET', mode: 'navigate', headers: new Headers(), url };
}

function createHarness({ fetchImpl, seeds = {}, cacheKeys = [] } = {}) {
  const listeners = {};
  const stores = new Map();
  const putCalls = [];
  const deleteCalls = [];
  const fetchCalls = [];
  const openedCacheNames = [];
  let claimed = 0;

  const ensureStore = name => {
    if (!stores.has(name)) stores.set(name, new Map());
    return stores.get(name);
  };
  const normalize = key => new URL(typeof key === 'string' ? key : key.url, ORIGIN).href;

  for (const [name, entries] of Object.entries(seeds)) {
    const store = ensureStore(name);
    for (const [url, response] of Object.entries(entries)) {
      store.set(normalize(url), response);
    }
  }
  for (const name of cacheKeys) ensureStore(name);

  const makeCache = name => ({
    async put(key, value) {
      ensureStore(name).set(normalize(key), value);
      putCalls.push({ cache: name, url: normalize(key), body: value && value.body });
    },
    async match(key) {
      return ensureStore(name).get(normalize(key));
    },
  });

  const caches = {
    async open(name) {
      openedCacheNames.push(name);
      ensureStore(name);
      return makeCache(name);
    },
    // 旧实现依赖跨代全局 match；这里如实实现，退化回 Cache-First 时会被断言抓住。
    async match(key) {
      const url = normalize(key);
      for (const store of stores.values()) {
        if (store.has(url)) return store.get(url);
      }
      return undefined;
    },
    async keys() {
      return [...stores.keys()];
    },
    async delete(name) {
      deleteCalls.push(name);
      return stores.delete(name);
    },
  };

  const fetch = async request => {
    fetchCalls.push(request);
    if (!fetchImpl) throw new Error('network unreachable');
    return fetchImpl(request);
  };

  const context = {
    URL,
    Set,
    Headers,
    Request,
    Response,
    console,
    caches,
    fetch,
    self: {
      location: { origin: ORIGIN },
      addEventListener(name, handler) {
        listeners[name] = handler;
      },
      skipWaiting: async () => {},
      clients: {
        async claim() {
          claimed += 1;
        },
      },
    },
  };
  vm.runInNewContext(workerSource, context);

  const navigate = async url => {
    const event = { request: navigationRequest(url) };
    let pending = null;
    event.respondWith = promise => {
      pending = promise;
    };
    listeners.fetch(event);
    assert.ok(pending, '壳文档导航必须被 Service Worker 接管');
    return pending;
  };

  const activate = async () => {
    const event = {};
    let pending = null;
    event.waitUntil = promise => {
      pending = promise;
    };
    listeners.activate(event);
    assert.ok(pending, 'activate 必须调用 waitUntil');
    await pending;
  };

  return {
    listeners,
    stores,
    putCalls,
    deleteCalls,
    fetchCalls,
    openedCacheNames,
    navigate,
    activate,
    claims: () => claimed,
  };
}

test('AC1: 导航先取服务端最新壳，并刷新当前代缓存', async () => {
  const harness = createHarness({
    seeds: {
      [CURRENT_CACHE]: { [SHELL_PATH]: shellResponse('cached-shell-before-update') },
      [LEGACY_CACHE]: { [SHELL_PATH]: shellResponse('legacy-shell-from-v2') },
    },
    fetchImpl: async () => shellResponse('fresh-shell-from-server'),
  });

  const response = await harness.navigate();

  assert.equal(response.body, 'fresh-shell-from-server', '必须返回服务端最新 index.html');
  assert.equal(harness.fetchCalls.length, 1, '导航必须先发出网络请求');
  assert.equal(harness.fetchCalls[0].mode, 'navigate');
  const shellPuts = harness.putCalls.filter(call => call.url === SHELL_URL);
  assert.equal(shellPuts.length, 1, '最新壳必须写回缓存一次');
  assert.equal(shellPuts[0].cache, CURRENT_CACHE, '只能写入当前代缓存');
  assert.equal(shellPuts[0].body, 'fresh-shell-from-server');
  assert.equal(harness.stores.get(CURRENT_CACHE).get(SHELL_URL).body, 'fresh-shell-from-server');
  assert.equal(
    harness.stores.get(LEGACY_CACHE).get(SHELL_URL).body,
    'legacy-shell-from-v2',
    '旧代缓存不应被跨代复用或改写'
  );
});

test('AC1: 网络不可达时回退当前代缓存壳', async () => {
  const harness = createHarness({
    seeds: { [CURRENT_CACHE]: { [SHELL_PATH]: shellResponse('cached-shell-offline') } },
    fetchImpl: async () => {
      throw new Error('offline');
    },
  });

  const response = await harness.navigate();

  assert.equal(response.body, 'cached-shell-offline');
  assert.equal(harness.putCalls.length, 0, '离线失败路径不得写缓存');
});

test('AC1: 网络与缓存同时缺失时维持 503 Offline shell unavailable', async () => {
  const harness = createHarness({
    fetchImpl: async () => {
      throw new Error('offline');
    },
  });

  const response = await harness.navigate();

  assert.equal(response.status, 503);
  assert.equal(response.statusText, 'Offline');
  assert.equal(await response.text(), 'Offline shell unavailable');
});

test('AC1: 服务端 5xx 时回退缓存壳，且不把错误响应当壳缓存', async () => {
  const harness = createHarness({
    seeds: { [CURRENT_CACHE]: { [SHELL_PATH]: shellResponse('cached-shell-during-outage') } },
    fetchImpl: async () => shellResponse('service-unavailable-body', { ok: false, status: 503, statusText: 'Service Unavailable' }),
  });

  const response = await harness.navigate();

  assert.equal(response.body, 'cached-shell-during-outage', '服务不可用应回退最近一次成功的壳');
  assert.equal(harness.putCalls.length, 0, '5xx 响应不得写入缓存');
});

test('AC1: 服务端 5xx 且无缓存时如实透传服务端响应', async () => {
  const harness = createHarness({
    fetchImpl: async () => shellResponse('service-unavailable-body', { ok: false, status: 503, statusText: 'Service Unavailable' }),
  });

  const response = await harness.navigate();

  assert.equal(response.status, 503);
  assert.equal(response.body, 'service-unavailable-body');
});

test('AC1: 不可缓存的壳响应（private/no-store/set-cookie）照常返回但不落盘', async () => {
  for (const headers of [
    { 'cache-control': 'private' },
    { 'cache-control': 'no-store' },
    { 'set-cookie': 'gw_session=secret' },
  ]) {
    const harness = createHarness({
      fetchImpl: async () => shellResponse('private-shell-body', { headers }),
    });
    const response = await harness.navigate();
    assert.equal(response.body, 'private-shell-body');
    assert.equal(harness.putCalls.length, 0, `不可缓存响应不得写入: ${JSON.stringify(headers)}`);
  }
});

test('AC2: 缓存代升级到 v3，activate 只淘汰旧代壳缓存', async () => {
  const harness = createHarness({
    cacheKeys: ['gw-static-shell-v1', LEGACY_CACHE, CURRENT_CACHE, 'gw-media-preview'],
    fetchImpl: async () => shellResponse('fresh-shell-from-server'),
  });

  await harness.navigate();
  assert.deepEqual(
    [...new Set(harness.openedCacheNames)],
    [CURRENT_CACHE],
    `导航只能操作当前代缓存，实际: ${JSON.stringify(harness.openedCacheNames)}`
  );
  assert.ok(
    harness.putCalls.every(call => call.cache === CURRENT_CACHE),
    '任何写缓存都必须落在 v3'
  );

  await harness.activate();
  assert.deepEqual(
    [...harness.deleteCalls].sort(),
    ['gw-static-shell-v1', LEGACY_CACHE].sort(),
    'activate 必须淘汰全部旧代壳缓存'
  );
  assert.ok(!harness.deleteCalls.includes(CURRENT_CACHE), '当前代缓存不得被自己删掉');
  assert.ok(!harness.deleteCalls.includes('gw-media-preview'), '非壳缓存不得被误删');
  assert.equal(harness.claims(), 1, 'activate 完成后必须 claim 客户端');
});

test('AC1: 非壳文档导航（带参数或其他页面）不被接管', async () => {
  for (const url of [`${ORIGIN}${SHELL_PATH}?project_id=private`, `${ORIGIN}/static/pages/project.html`]) {
    const harness = createHarness({ fetchImpl: async () => shellResponse('should-not-be-used') });
    const event = { request: navigationRequest(url) };
    let responded = false;
    event.respondWith = () => {
      responded = true;
    };
    harness.listeners.fetch(event);
    assert.equal(responded, false, `不得接管 ${url}`);
    assert.equal(harness.fetchCalls.length, 0, `不得为 ${url} 预取`);
  }
});

test('AC3: 本测试已登记 node-files.txt 与票面卫生准入分片', () => {
  const self = 'tests/frontend/qin-651-sw-navigation-network-first.test.mjs';
  const listed = readFileSync(path.join(repoRoot, 'tests/frontend/node-files.txt'), 'utf8')
    .replace(/\r/g, '')
    .split('\n')
    .map(line => line.trim());
  assert.ok(listed.includes(self), 'node-files.txt 必须登记本测试');

  const shard = JSON.parse(
    readFileSync(path.join(repoRoot, 'tests/hygiene/fixtures/admissions/issue-808.json'), 'utf8')
  );
  assert.equal(shard.issue, 808);
  assert.ok(shard.files.includes(self), '分片 files 必须登记本测试');
  assert.ok(shard.files.includes('web/sw.js'), '分片 files 必须登记 web/sw.js');
  assert.ok(
    shard.accepted_test_files.some(entry => entry.path === self && entry.admission === 'accepted'),
    '分片 accepted_test_files 必须接收本测试'
  );
});
