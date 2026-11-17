// [QIN-598] 真实产品模块加载校验：允许根迁入「权限」面板、非管理员隐藏、3s/20s 慢延迟与离页代际保护
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import test from 'node:test';
import {
  bootProduct,
  dropTree,
  installHost,
  loadProduct,
  makeProductTree,
  wait,
} from './qin-602-asset-view-harness.mjs';

const indexHtml = readFileSync(new URL('../../web/pages/index.html', import.meta.url), 'utf8');
const settingsHtml = readFileSync(new URL('../../web/pages/settings.html', import.meta.url), 'utf8');
const telemetryJs = readFileSync(new URL('../../web/js/core/hardware-telemetry.js', import.meta.url), 'utf8');
const homeControllerJs = readFileSync(new URL('../../web/js/controllers/home-controller.js', import.meta.url), 'utf8');
const directoryCss = readFileSync(new URL('../../web/css/pages/directory-settings.css', import.meta.url), 'utf8');

function jsonResponse(data, status = 200) {
  return {
    status,
    ok: status >= 200 && status < 300,
    headers: { get: name => (String(name).toLowerCase() === 'content-type' ? 'application/json' : null) },
    json: async () => data,
    text: async () => JSON.stringify(data),
  };
}

function patchDocumentGetElementById(document) {
  const orig = document.getElementById.bind(document);
  document.getElementById = id => {
    const key = String(id);
    const inTree = document.querySelector(`[id="${key}"]`);
    return inTree || orig(key);
  };
}

async function bootPermissionsProduct({ mode = 'permissions', role = 'admin', routesHandler, rootsState } = {}) {
  const roots = rootsState || [
    { id: 'root_sys', path: 'D:/Sample/System', source: 'system', read_only: true, active: true, availability: 'available', aliases: [], alias_candidates: [] },
    { id: 'root_0123456789abcdef01234567', path: 'D:/Sample/Film', source: 'persistent', read_only: false, active: true, availability: 'available', aliases: [], alias_candidates: [] },
    { id: 'root_rem', path: 'D:/Sample/Removed', source: 'persistent', read_only: false, active: false, availability: 'removed', availability_reason: 'MOUNT_ROOT_REMOVED', availability_message: '允许根已移除', aliases: [], alias_candidates: [] },
    { id: 'root_inc', path: 'D:/Sample/Incomplete', source: 'persistent', read_only: false, active: false, availability: 'removed', availability_reason: 'PURGE_INCOMPLETE', availability_message: '上次清除索引未完成，请重试清除索引', aliases: [], alias_candidates: [] },
  ];
  const postedMessages = [];
  const fetchImpl = async (url, init, entry) => {
    if (url.includes('/api/asset-auth/status')) {
      return jsonResponse({ authenticated: true, auth_mode: 'local_account', principal: { username: 'tester', role } });
    }
    if (url.includes('/api/asset-registry/facets')) {
      return jsonResponse({ kinds: [], categories: [], tags: [], collections: [], roots: [{ id: 'local_extra_film', aliases: ['root_0123456789abcdef01234567'], path: 'D:/Sample/Film' }] });
    }
    if (url.includes('/api/asset-registry/status')) {
      return jsonResponse({ overview: { roots: [] }, jobs: [] });
    }
    if (url.includes('/api/thumbnail-cache')) {
      return jsonResponse({ mode: 'source', directory: '', cache_folder: 'god-cache' });
    }
    if (url.includes('/api/storage-settings/roots')) {
      if (routesHandler) return routesHandler(url, init, entry);
      return jsonResponse({ roots, revision: 2 });
    }
    if (url.includes('/api/storage-settings')) {
      if (routesHandler) {
        const maybe = await routesHandler(url, init, entry);
        if (maybe) return maybe;
      }
      return jsonResponse({ dirs: { local: 'D:/Sample/Local', upload: 'D:/Sample/Upload', generated: 'D:/Sample/Gen' }, extra_local_entries: [], revision: 1 });
    }
    return jsonResponse({});
  };

  const host = installHost({ embedded: true, theme: 'dark', fetchImpl });
  const nodeProto = Object.getPrototypeOf(host.root);
  if (nodeProto && !nodeProto.contains) {
    nodeProto.contains = function (target) {
      let cur = target;
      while (cur) {
        if (cur === this) return true;
        cur = cur.parentNode;
      }
      return false;
    };
  }
  patchDocumentGetElementById(host.document);
  globalThis.location.search = `?settings=${mode}&embedded=1`;
  globalThis.location.href = `http://127.0.0.1:2077/static/asset-manager.html?settings=${mode}&embedded=1`;
  globalThis.confirm = () => true;
  globalThis.parent = {
    postMessage(msg, origin) {
      postedMessages.push({ msg, origin });
    },
  };
  const tree = makeProductTree();
  const main = await loadProduct(tree);
  await bootProduct(host.document);
  await wait(30);
  return { host, document: host.document, tree, main, postedMessages };
}

test('AC6/AC7 真实入口：目录设置右栏移除允许根，权限子页仅管理员渲染允许根且文案遵守 QIN-597', async () => {
  // 1. ?settings=directories&embedded=1：右栏仅保留项目目录模板，不渲染允许根
  const dirRun = await bootPermissionsProduct({ mode: 'directories', role: 'admin' });
  try {
    const overlay = dirRun.document.getElementById('storageSettingsOverlay');
    const rightCol = overlay.querySelector('.directory-cards-column--right');
    assert.ok(rightCol, '目录设置右栏存在');
    assert.match(rightCol.innerHTML, /项目目录模板|project-workspace-settings/);
    assert.equal(overlay.querySelector('[data-allowed-roots-settings]'), null, '目录设置页不得再渲染允许根设置区');
  } finally {
    dropTree(dirRun.tree);
  }

  // 2. ?settings=permissions&embedded=1（admin）：正确渲染在线/已移除/PURGE_INCOMPLETE 与系统只读根边界
  const permAdmin = await bootPermissionsProduct({ mode: 'permissions', role: 'admin' });
  try {
    const overlay = permAdmin.document.getElementById('storageSettingsOverlay');
    assert.match(overlay.innerHTML, /允许根权限管理/);
    assert.ok(overlay.querySelector('[data-allowed-roots-settings]'), '管理员在权限子页可见允许根卡片');
    assert.ok(overlay.querySelector('.allowed-root-row--removed'), '已移除根具备明显区分样式类');
    assert.match(overlay.innerHTML, /清除未完成/);
    assert.equal(overlay.querySelector('[data-storage-root-restore="root_inc"]'), null, 'PURGE_INCOMPLETE 不得显示恢复按钮');
    assert.ok(overlay.querySelector('[data-storage-root-purge-prompt="root_inc"]'), 'PURGE_INCOMPLETE 保留重试清除索引按钮');
    assert.equal(overlay.querySelector('[data-resource-folder-remove="root_sys"]'), null, '系统根无移出按钮');
    assert.equal(overlay.querySelector('[data-storage-root-purge-prompt="root_sys"]'), null, '系统根无清除按钮');
    assert.doesNotMatch(overlay.innerHTML, /已移除登记目录|登记允许根/);
    assert.ok(permAdmin.postedMessages.some(item => item.msg?.type === 'gw-permissions-roots-height' && item.msg.height >= 220), '渲染后向父容器上报自适应高度');
  } finally {
    dropTree(permAdmin.tree);
  }

  // 3. ?settings=permissions&embedded=1（非管理员 editor）：拒绝并不得渲染允许根卡片
  const permEditor = await bootPermissionsProduct({ mode: 'permissions', role: 'editor' });
  try {
    const overlay = permEditor.document.getElementById('storageSettingsOverlay');
    assert.equal(overlay.querySelector('[data-allowed-roots-settings]'), null, '非管理员不得看到允许根卡片');
    assert.match(overlay.innerHTML, /仅管理员可查看和修改允许根权限/);
  } finally {
    dropTree(permEditor.tree);
  }
});

test('异步慢延迟（3s）：purge 请求结束前保持 saving=true 且禁用动作按钮，完成后才更新状态', { timeout: 15000 }, async () => {
  let revision = 2;
  const run = await bootPermissionsProduct({
    mode: 'permissions',
    role: 'admin',
    routesHandler: async (url, init) => {
      if (init?.method === 'DELETE' && url.includes('/purge')) {
        await wait(3000);
        revision += 1;
        return jsonResponse({ roots: [], revision });
      }
      return jsonResponse({
        roots: [{ id: 'root_slow3', path: 'D:/Sample/Slow3', source: 'persistent', readOnly: false, active: true, availability: 'available', aliases: [], aliasCandidates: [] }],
        revision,
      });
    },
  });
  try {
    const started = Date.now();
    const promise = run.main.saveAllowedRootsRequest('/api/storage-settings/roots/root_slow3/purge', 'DELETE', { expected_version: 2 });
    await wait(1200);
    assert.equal(run.main.allowedRootSettingsState.saving, true, '3s 慢请求进行中 saving 必须为 true');
    const overlay = run.document.getElementById('storageSettingsOverlay');
    assert.ok(overlay.querySelector('[data-storage-root-add][disabled]'), '3s 进行中按钮保持禁用，不得提前标完成');
    assert.notEqual(run.document.getElementById('assetStatus').textContent, '已清除资源文件夹的索引与缓存记录（原始素材保留）');

    await promise;
    const elapsed = Date.now() - started;
    assert.ok(elapsed >= 2900, `必须真实等待满 3s 才结束（实测 ${elapsed}ms）`);
    assert.equal(run.main.allowedRootSettingsState.saving, false);
    assert.equal(run.document.getElementById('assetStatus').textContent, '已清除资源文件夹的索引与缓存记录（原始素材保留）');
  } finally {
    dropTree(run.tree);
  }
});

test('异步慢延迟（20s）与离页取消：进行中切出权限页后迟到响应不写回状态或覆盖文案', { timeout: 35000 }, async () => {
  let resolveSlowFetch;
  const run = await bootPermissionsProduct({
    mode: 'permissions',
    role: 'admin',
    routesHandler: async (url, init) => {
      if (init?.method === 'DELETE' && url.includes('/purge')) {
        return new Promise(resolve => {
          resolveSlowFetch = () => resolve(jsonResponse({ roots: [], revision: 99 }));
          setTimeout(resolveSlowFetch, 20000);
        });
      }
      return jsonResponse({
        roots: [{ id: 'root_slow20', path: 'D:/Sample/Slow20', source: 'persistent', readOnly: false, active: true, availability: 'available', aliases: [], aliasCandidates: [] }],
        revision: 2,
      });
    },
  });
  try {
    const started = Date.now();
    const pending = run.main.saveAllowedRootsRequest('/api/storage-settings/roots/root_slow20/purge', 'DELETE', { expected_version: 2 });
    await wait(150);
    assert.equal(run.main.allowedRootSettingsState.saving, true, '20s 慢请求进行中保持 saving=true');

    // 真实触发宿主页发来的离页消息，使代际令牌失效
    globalThis.window.dispatchEvent({
      type: 'message',
      origin: globalThis.location.origin,
      data: { type: 'gw-permissions-section-leave' },
    });

    const settled = await pending;
    const waited = Date.now() - started;
    assert.ok(waited >= 19500, `必须等满 20s 迟到响应到达（实测 ${waited}ms）`);
    assert.equal(settled, null, '离页后迟到的 20s 响应必须被丢弃');
    assert.equal(run.main.allowedRootSettingsState.revision, 2, '迟到响应不得覆盖 revision');
    assert.equal(run.main.allowedRootSettingsState.roots.length, 1, '迟到响应不得写回旧状态');
    assert.notEqual(run.document.getElementById('assetStatus').textContent, '已清除资源文件夹的索引与缓存记录（原始素材保留）');
  } finally {
    dropTree(run.tree);
  }
});

test('写后刷新失败：移除允许根成功但 storage-settings 回读 500 时显式报错', async () => {
  let failStorageRefresh = false;
  const run = await bootPermissionsProduct({
    mode: 'permissions',
    role: 'admin',
    routesHandler: async (url, init) => {
      if (url.includes('/api/storage-settings/roots') && init?.method === 'DELETE') {
        failStorageRefresh = true;
        return jsonResponse({ roots: [], revision: 3 });
      }
      if (url.includes('/api/storage-settings') && !url.includes('/roots') && failStorageRefresh) {
        return jsonResponse({ detail: { code: 'STORAGE_UNAVAILABLE', message: '存储配置读取失败' } }, 500);
      }
      return null;
    },
  });
  try {
    await run.main.saveAllowedRootsRequest('/api/storage-settings/roots/root_0123456789abcdef01234567', 'DELETE', { expected_version: 2 });
    await wait(50);
    const statusText = run.document.getElementById('assetStatus').textContent;
    assert.match(statusText, /存储配置刷新失败/);
  } finally {
    dropTree(run.tree);
  }
});

test('真实 HardwareDeck（hardware-telemetry.js）按身份挂载/隐藏权限区 iframe 并转发高度与离页消息', async () => {
  // 校验两个页面均存在正确的 permissionsAllowedRootsMount 容器且不新增高级设置分区
  for (const html of [indexHtml, settingsHtml]) {
    const panel = html.slice(html.indexOf('data-panel="permissions"'));
    assert.match(panel, /id="permissionsAllowedRootsMount"/);
    assert.match(panel, /data-permissions-allowed-roots/);
    assert.doesNotMatch(html, /data-section="advanced"/);
  }

  let authPayload = { ok: true, status: 200, data: { authenticated: false, auth_mode: 'local_account', principal: null } };
  const postedToFrame = [];
  const docListeners = new Map();
  const winListeners = new Map();

  class MiniClassList {
    constructor(el) { this.el = el; this.set = new Set(['hidden', 'space-y-2']); }
    toggle(cls, force) {
      const next = force === undefined ? !this.set.has(cls) : Boolean(force);
      if (next) this.set.add(cls); else this.set.delete(cls);
      return next;
    }
    add(...items) { items.forEach(i => this.set.add(i)); }
    remove(...items) { items.forEach(i => this.set.delete(i)); }
    contains(item) { return this.set.has(item); }
  }

  const createdFrames = [];
  const mountNode = {
    id: 'permissionsAllowedRootsMount',
    attributes: new Map([['hidden', '']]),
    style: {},
    classList: null,
    _html: '',
    get innerHTML() { return this._html; },
    set innerHTML(val) { this._html = String(val); if (!val) createdFrames.length = 0; },
    setAttribute(k, v) { this.attributes.set(k, String(v)); },
    removeAttribute(k) { this.attributes.delete(k); },
    hasAttribute(k) { return this.attributes.has(k); },
    appendChild(child) { createdFrames.push(child); },
    querySelector(sel) { return sel.includes('iframe') ? (createdFrames[0] || null) : null; },
  };
  mountNode.classList = new MiniClassList(mountNode);

  const badge = { textContent: '', setAttribute() {}, removeAttribute() {}, title: '' };
  const byId = {
    permissionsAllowedRootsMount: mountNode,
    settingsSessionBadge: badge,
    settingsSeatRole: { ...badge },
    settingsAuthChannel: { ...badge },
    settingsAuthLatency: { ...badge },
  };

  const sandbox = {
    console,
    Date,
    Number,
    String,
    Boolean,
    Array,
    Object,
    Set,
    Map,
    URLSearchParams,
    Intl,
    Math,
    Promise,
    AbortSignal,
    performance,
    setTimeout,
    clearTimeout,
    setInterval: () => 0,
    clearInterval: () => {},
    requestAnimationFrame: () => 0,
    cancelAnimationFrame: () => {},
    Event: class { constructor(type, init) { this.type = type; Object.assign(this, init); } },
    CustomEvent: class { constructor(type, init) { this.type = type; Object.assign(this, init); } },
    location: { origin: 'http://127.0.0.1:2077', pathname: '/static/pages/settings.html', search: '?section=permissions', href: 'http://127.0.0.1:2077/static/pages/settings.html?section=permissions' },
    localStorage: { getItem: () => null, setItem() {}, removeItem() {} },
    sessionStorage: { getItem: () => null, setItem() {}, removeItem() {} },
    fetch: async url => {
      if (String(url).includes('/api/asset-auth/status')) {
        return {
          ok: authPayload.ok,
          status: authPayload.status,
          json: async () => authPayload.data,
        };
      }
      return { ok: true, status: 200, json: async () => ({}) };
    },
    addEventListener(type, fn) {
      if (!winListeners.has(type)) winListeners.set(type, []);
      winListeners.get(type).push(fn);
    },
    dispatchEvent() {},
    document: {
      readyState: 'complete',
      getElementById: id => byId[id] || null,
      querySelector: () => null,
      querySelectorAll: () => [],
      addEventListener(type, fn) {
        if (!docListeners.has(type)) docListeners.set(type, []);
        docListeners.get(type).push(fn);
      },
      createElement: tag => ({
        tagName: String(tag).toUpperCase(),
        style: {},
        attrs: new Map(),
        setAttribute(k, v) { this.attrs.set(k, String(v)); },
        getAttribute(k) { return this.attrs.get(k) || null; },
        contentWindow: { postMessage: (msg, origin) => postedToFrame.push({ msg, origin }) },
      }),
    },
    FloatingDismissal: { scan() {} },
  };
  sandbox.window = sandbox;

  // 直接运行完整 hardware-telemetry.js（无切片、无 new Function）
  vm.runInNewContext(telemetryJs, sandbox, { filename: 'web/js/core/hardware-telemetry.js' });
  await wait(20);
  assert.equal(createdFrames.length, 0, '未登录状态不得挂载允许根 iframe');
  assert.equal(mountNode.classList.contains('hidden'), true);

  // 切换为非管理员（editor）：仍隐藏并无 iframe
  authPayload = { ok: true, status: 200, data: { authenticated: true, auth_mode: 'local_account', principal: { username: 'ed', role: 'editor' } } };
  await sandbox.window.HardwareDeck.syncAuth();
  assert.equal(createdFrames.length, 0, 'editor 非管理员不得挂载允许根 iframe');
  assert.equal(mountNode.classList.contains('hidden'), true);

  // 切换为管理员（admin）：取消隐藏并挂载真实子页 URL
  authPayload = { ok: true, status: 200, data: { authenticated: true, auth_mode: 'local_account', principal: { username: 'adm', role: 'admin' } } };
  await sandbox.window.HardwareDeck.syncAuth();
  assert.equal(mountNode.classList.contains('hidden'), false);
  assert.equal(mountNode.hasAttribute('hidden'), false);
  assert.equal(createdFrames.length, 1);
  assert.equal(createdFrames[0].src, '/static/asset-manager.html?settings=permissions&embedded=1');

  // 模拟同源子页上报高度消息
  for (const fn of winListeners.get('message') || []) {
    fn({ origin: 'http://127.0.0.1:2077', data: { type: 'gw-permissions-roots-height', height: 412 } });
  }
  assert.equal(createdFrames[0].style.height, '412px');

  // 模拟切换至其他设置分区（非 permissions），应向 iframe 发送离页消息
  for (const fn of docListeners.get('click') || []) {
    fn({ target: { closest: sel => (sel === '[data-section]' ? { dataset: { section: 'general' } } : null) } });
  }
  assert.equal(postedToFrame.length, 1);
  assert.equal(postedToFrame[0]?.msg?.type, 'gw-permissions-section-leave');
  assert.equal(postedToFrame[0]?.origin, 'http://127.0.0.1:2077');

  // 验证 home-controller.js 在激活 permissions 时调用 syncSeatPanel
  assert.match(homeControllerJs, /sec === 'permissions'[\s\S]*syncSeatPanel\?\.\(\)/);
  assert.match(directoryCss, /\.permissions-settings-frame/);
  assert.match(directoryCss, /html\[data-theme="light"\]\s*\.directory-page/);
});
