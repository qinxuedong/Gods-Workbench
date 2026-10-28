// QIN-423 MVP1-T8b：Chrome/Edge MV3 扩展契约测试。
//
// 用 node:vm 加载**出货文件本体**（manifest / 解析器 / 接口客户端 / 后台），
// 断言权限边界、地址解析、收集 HTTP 契约、延迟日志与零密钥约束。
import assert from 'node:assert/strict';
import {readdirSync, readFileSync, statSync} from 'node:fs';
import {dirname, join, relative} from 'node:path';
import test from 'node:test';
import {fileURLToPath} from 'node:url';
import vm from 'node:vm';

const REPO_ROOT = join(dirname(fileURLToPath(import.meta.url)), '..', '..');
const EXT_DIR = join(REPO_ROOT, 'clients', 'browser-extension');

/** vm 内创建的对象属于另一个 realm，比较前转成本 realm 的普通值。 */
function plain(value) {
  return JSON.parse(JSON.stringify(value));
}

function listFiles(dir) {
  const result = [];
  for (const entry of readdirSync(dir)) {
    const full = join(dir, entry);
    if (statSync(full).isDirectory()) result.push(...listFiles(full));
    else result.push(full);
  }
  return result;
}

function readExtensionFile(name) {
  return readFileSync(join(EXT_DIR, name), 'utf8');
}

/** 立即可执行的定时器：让后台重试逻辑在测试内同步推进，避免真实等待。 */
function createSandbox(extra = {}) {
  const sandbox = {
    console,
    URL,
    JSON,
    Date,
    Promise,
    Array,
    Object,
    Number,
    String,
    Error,
    setTimeout: fn => {
      queueMicrotask(fn);
      return 0;
    },
    clearTimeout: () => {},
    ...extra,
  };
  sandbox.globalThis = sandbox;
  sandbox.self = sandbox;
  vm.createContext(sandbox);
  return sandbox;
}

function runFiles(sandbox, files) {
  for (const file of files) {
    vm.runInContext(readExtensionFile(file), sandbox, {filename: file});
  }
  return sandbox;
}

function loadResolver() {
  const sandbox = createSandbox();
  runFiles(sandbox, ['src/target-resolver.js']);
  return sandbox.GwCollectTarget;
}

function loadClient() {
  const sandbox = createSandbox();
  runFiles(sandbox, ['src/collect-client.js']);
  return sandbox.GwCollectClient;
}

function loadBackground() {
  const sandbox = createSandbox();
  sandbox.importScripts = (...files) => runFiles(sandbox, files);
  runFiles(sandbox, ['background.js']);
  return sandbox.GwCollectBackground;
}

/** 最小 chrome.* 替身：只实现后台与选项页实际用到的 API 形状。 */
function createChromeMock(options = {}) {
  const localMap = new Map(Object.entries(options.local || {}));
  const sessionMap = new Map(Object.entries(options.session || {}));
  const calls = [];
  const menus = [];
  const listeners = {installed: [], menuClicked: [], message: []};
  const tabList = (options.tabs || []).map(tab => ({...tab}));

  function storageArea(map) {
    return {
      get(query) {
        const result = {};
        if (typeof query === 'string') {
          if (map.has(query)) result[query] = map.get(query);
        } else if (Array.isArray(query)) {
          for (const key of query) if (map.has(key)) result[key] = map.get(key);
        } else if (query && typeof query === 'object') {
          for (const [key, fallback] of Object.entries(query)) {
            result[key] = map.has(key) ? map.get(key) : fallback;
          }
        }
        return Promise.resolve(result);
      },
      set(values) {
        for (const [key, value] of Object.entries(values)) map.set(key, value);
        return Promise.resolve();
      },
    };
  }

  function patternMatches(url, patterns) {
    return (Array.isArray(patterns) ? patterns : [patterns]).some(pattern => {
      const prefix = String(pattern).replace(/\*$/, '');
      return String(url || '').startsWith(prefix);
    });
  }

  const api = {
    storage: {
      local: storageArea(localMap),
      ...(options.sessionStorage === false ? {} : {session: storageArea(sessionMap)}),
    },
    tabs: {
      query(queryInfo = {}) {
        calls.push({api: 'tabs.query', queryInfo});
        const matched = tabList.filter(tab => patternMatches(tab.url, queryInfo.url || '*'));
        return Promise.resolve(matched.map(tab => ({...tab})));
      },
      create(createInfo) {
        calls.push({api: 'tabs.create', createInfo});
        const tab = {id: 900 + tabList.length, url: createInfo.url, title: '神工坊'};
        tabList.push(tab);
        return Promise.resolve({...tab});
      },
      sendMessage(tabId, message) {
        calls.push({api: 'tabs.sendMessage', tabId, message});
        const response = options.onMessage ? options.onMessage(tabId, message) : undefined;
        if (response === 'NO_RECEIVER') {
          return Promise.reject(new Error('Could not establish connection. Receiving end does not exist.'));
        }
        return Promise.resolve(response);
      },
    },
    contextMenus: {
      removeAll() {
        menus.length = 0;
        calls.push({api: 'contextMenus.removeAll'});
        return Promise.resolve();
      },
      create(properties) {
        calls.push({api: 'contextMenus.create', properties});
        menus.push({...properties});
      },
      onClicked: {addListener: fn => listeners.menuClicked.push(fn)},
    },
    runtime: {
      onInstalled: {addListener: fn => listeners.installed.push(fn)},
      onMessage: {addListener: fn => listeners.message.push(fn)},
    },
    action: {
      badgeText: '',
      badgeColor: '',
      title: '',
      setBadgeText(details) {
        api.action.badgeText = details.text;
        return Promise.resolve();
      },
      setBadgeBackgroundColor(details) {
        api.action.badgeColor = details.color;
        return Promise.resolve();
      },
      setTitle(details) {
        api.action.title = details.title;
        return Promise.resolve();
      },
    },
  };

  return {api, calls, menus, listeners, localMap, sessionMap, tabList};
}

test('MV3 清单：权限最小化、端点仅本机回环、零二进制资源', () => {
  const manifest = JSON.parse(readExtensionFile('manifest.json'));
  assert.equal(manifest.manifest_version, 3);
  assert.deepEqual([...manifest.permissions].sort(), ['contextMenus', 'storage']);
  assert.deepEqual([...manifest.host_permissions].sort(), [
    'http://127.0.0.1:2077/*',
    'http://localhost:2077/*',
  ]);
  assert.equal(manifest.background.service_worker, 'background.js');
  // 经典 worker 才能 importScripts 复用共享逻辑；模块化 worker 会破坏该加载方式。
  assert.equal(manifest.background.type, undefined);
  assert.equal(manifest.options_ui.page, 'options.html');
  assert.equal(manifest.icons, undefined, '不得引入未获准的图片/图标二进制');
  assert.equal(manifest.externally_connectable, undefined);
  assert.ok(!JSON.stringify(manifest).includes('<all_urls>'));

  const matched = manifest.content_scripts.flatMap(item => item.matches);
  assert.ok(matched.includes('http://127.0.0.1:2077/*'));
  assert.ok(matched.includes('http://localhost:2077/*'));

  // 桥接脚本必须先加载共享逻辑，再加载业务脚本（声明顺序即执行顺序）。
  const bridge = manifest.content_scripts.find(item => item.js.includes('content/app-bridge.js'));
  assert.deepEqual(bridge.js, ['src/collect-client.js', 'content/app-bridge.js']);
  const shortcut = manifest.content_scripts.find(item => item.js.includes('content/collect-shortcut.js'));
  assert.deepEqual(shortcut.js, [
    'src/target-resolver.js',
    'src/collect-client.js',
    'content/collect-shortcut.js',
  ]);

  const binaryAssets = listFiles(EXT_DIR).filter(file =>
    /\.(png|jpe?g|gif|webp|ico|svg|woff2?|ttf|otf|crx|zip)$/i.test(file));
  assert.deepEqual(binaryAssets, [], '扩展目录不得包含未经批准的二进制资源');
});

test('端点校验只放行本机 2077', () => {
  const client = loadClient();
  assert.equal(client.normalizeEndpoint('http://127.0.0.1:2077/'), 'http://127.0.0.1:2077');
  assert.equal(client.normalizeEndpoint('http://localhost:2077'), 'http://localhost:2077');
  const rejected = [
    ['https://example.com', 'endpoint_not_loopback'],
    ['http://192.168.1.2:2077', 'endpoint_not_loopback'],
    ['http://127.0.0.1:8080', 'endpoint_not_supported'],
    ['ftp://127.0.0.1:2077', 'endpoint_not_loopback'],
    ['不是地址', 'endpoint_not_loopback'],
  ];
  for (const [value, code] of rejected) {
    assert.throws(() => client.normalizeEndpoint(value), error => error.errorCode === code, value);
  }
});

test('目标解析：srcset 最大项、背景图、视频海报与浏览器内部地址', () => {
  const resolver = loadResolver();
  const page = 'https://site.test/gallery/p1';

  assert.deepEqual(plain(resolver.resolveCollectTarget({tagName: 'IMG', src: '/a.png', pageUrl: page})), {
    url: 'https://site.test/a.png',
    kind: 'image',
  });
  assert.deepEqual(plain(resolver.resolveCollectTarget({
    tagName: 'IMG',
    srcset: '/small.png 320w, /large.png 1280w, /mid.png 640w',
    pageUrl: page,
  })), {url: 'https://site.test/large.png', kind: 'image'});
  assert.deepEqual(plain(resolver.resolveCollectTarget({
    tagName: 'IMG',
    srcset: '/one.png 1x, /two.png 2x',
    pageUrl: page,
  })), {url: 'https://site.test/two.png', kind: 'image'});
  assert.deepEqual(plain(resolver.resolveCollectTarget({
    tagName: 'DIV',
    backgroundImage: 'url("/bg/cover.jpg")',
    pageUrl: page,
  })), {url: 'https://site.test/bg/cover.jpg', kind: 'image'});
  assert.deepEqual(plain(resolver.resolveCollectTarget({
    tagName: 'VIDEO',
    src: 'https://cdn.test/clip.mp4',
    pageUrl: page,
  })), {url: 'https://cdn.test/clip.mp4', kind: 'video'});
  assert.deepEqual(plain(resolver.resolveCollectTarget({
    tagName: 'A',
    href: '/downloads/raw.mov',
    pageUrl: page,
  })), {url: 'https://site.test/downloads/raw.mov', kind: 'link'});

  // 元素地址不可用时必须明确失败，不得静默退化为「收集当前页面」。
  assert.deepEqual(
    plain(resolver.resolveCollectTarget({tagName: 'IMG', currentSrc: 'blob:https://site.test/1', pageUrl: page})),
    {errorCode: 'blob_url'},
  );
  assert.deepEqual(
    plain(resolver.resolveCollectTarget({tagName: 'IMG', src: 'data:image/png;base64,AAAA', pageUrl: page})),
    {errorCode: 'data_url'},
  );
  assert.deepEqual(
    plain(resolver.resolveCollectTarget({
      tagName: 'VIDEO',
      src: 'mediasource:https://site.test/stream',
      pageUrl: page,
    })),
    {errorCode: 'media_stream'},
  );

  // 空白处右键：没有元素候选时才回落到页面地址。
  assert.deepEqual(plain(resolver.resolveCollectTarget({tagName: '', pageUrl: page})), {
    url: page,
    kind: 'page',
  });
  assert.deepEqual(plain(resolver.resolveCollectTarget({tagName: ''})), {errorCode: 'no_target'});
});

test('收集客户端按真实契约配对、提交并轮询到终态', async () => {
  const client = loadClient();
  const calls = [];
  let clock = 0;
  const responses = [
    {ok: true, json: async () => ({pair_token: 'token-1'})},
    {ok: true, json: async () => ({job_id: 'job-1', status: 'queued'})},
    {ok: true, json: async () => ({job_id: 'job-1', status: 'running', progress: 40})},
    {ok: true, json: async () => ({job_id: 'job-1', status: 'done', progress: 100})},
  ];
  const instance = client.create({
    endpoint: 'http://127.0.0.1:2077',
    fetchImpl: async (url, init) => {
      calls.push({url, init});
      clock += 120;
      return responses.shift();
    },
    now: () => clock,
    wait: async () => {},
  });

  assert.equal(await instance.pair(), 'token-1');
  assert.equal(calls[0].url, 'http://127.0.0.1:2077/api/asset-collect/pair');
  assert.equal(calls[0].init.method, 'POST');
  assert.equal(calls[0].init.credentials, 'same-origin');

  const result = await instance.collectUrl('https://cdn.test/a.png', {token: 'token-1'});
  assert.deepEqual(plain(result), {
    ok: true,
    jobId: 'job-1',
    status: 'done',
    progress: 100,
    errorCode: '',
    message: '',
    elapsedMs: 360,
    timedOut: false,
  });
  assert.deepEqual(JSON.parse(calls[1].init.body), {
    url: 'https://cdn.test/a.png',
    pair_token: 'token-1',
  });
  assert.equal(calls[2].url, 'http://127.0.0.1:2077/api/asset-collect/jobs/job-1');
  assert.equal(calls[2].init.headers['X-Collect-Token'], 'token-1');
});

test('收集客户端：服务端失败码与超预算行为', async () => {
  const client = loadClient();

  const failing = client.create({
    endpoint: 'http://127.0.0.1:2077',
    fetchImpl: async () => ({
      ok: false,
      status: 400,
      json: async () => ({detail: {code: 'drm_protected', message: '该内容受 DRM 版权保护，无法收集'}}),
    }),
    now: () => 0,
    wait: async () => {},
  });
  await assert.rejects(
    () => failing.collectUrl('https://cdn.test/movie.mp4', {token: 'token-2'}),
    error => error.errorCode === 'drm_protected' && /DRM/.test(error.message),
  );

  // 默认预算 2500ms：超时不误报失败，交给工作台继续。
  let clock = 0;
  const slow = client.create({
    endpoint: 'http://127.0.0.1:2077',
    fetchImpl: async url => {
      if (url.endsWith('/url')) return {ok: true, json: async () => ({job_id: 'job-9', status: 'queued'})};
      clock += 200;
      return {ok: true, json: async () => ({job_id: 'job-9', status: 'running', progress: 10})};
    },
    now: () => clock,
    wait: async () => {},
  });
  const result = await slow.collectUrl('https://cdn.test/big.mp4', {token: 'token-3'});
  assert.equal(result.ok, false);
  assert.equal(result.timedOut, true);
  assert.equal(result.errorCode, 'collect_timeout');
  assert.equal(result.status, 'running');
  assert.ok(result.elapsedMs >= 2500 && result.elapsedMs <= 3000, `预算越界: ${result.elapsedMs}`);
});

test('后台：注册右键菜单并在收集成功后写入延迟日志与徽标', async () => {
  const background = loadBackground();
  const mock = createChromeMock({
    tabs: [{id: 7, url: 'http://127.0.0.1:2077/', title: '神工坊'}],
    onMessage: (tabId, message) => {
      if (message.type === 'gw:ensure-pair') return {ok: true, token: 'token-9', endpoint: 'http://127.0.0.1:2077'};
      if (message.type === 'gw:collect-url') return {ok: true, jobId: 'job-7', elapsedMs: 640};
      return undefined;
    },
  });

  const controller = background.register(mock.api);
  await controller.buildMenus();
  assert.deepEqual(mock.menus.map(menu => menu.id), [
    'gw-collect-image',
    'gw-collect-video',
    'gw-collect-link',
    'gw-collect-page',
  ]);
  assert.ok(mock.menus.every(menu => menu.title.includes('收入神工坊')));
  assert.equal(mock.listeners.installed.length, 1);
  assert.equal(mock.listeners.menuClicked.length, 1);
  assert.equal(mock.listeners.message.length, 1);

  const record = plain(await background.dispatchCollect(mock.api, {
    url: 'https://cdn.test/pic.png',
    pageUrl: 'https://site.test/p',
    title: '页面',
  }));
  assert.equal(record.ok, true);
  assert.equal(record.jobId, 'job-7');
  assert.equal(record.elapsedMs, 640);
  assert.equal(mock.api.action.badgeText, '✓');
  assert.equal(mock.sessionMap.get('pair').token, 'token-9');

  const history = mock.localMap.get(background.HISTORY_KEY);
  assert.equal(history.length, 1);
  assert.equal(history[0].status, 'done');
  assert.equal(history[0].elapsedMs, 640);
  assert.equal(history[0].url, 'https://cdn.test/pic.png');

  const collectCall = mock.calls.find(call => call.api === 'tabs.sendMessage' && call.message.type === 'gw:collect-url');
  assert.equal(collectCall.message.url, 'https://cdn.test/pic.png');
  assert.equal(collectCall.message.referer, 'https://site.test/p');
});

test('后台：浏览器内部地址与工作台不可达都不静默通过', async () => {
  const background = loadBackground();

  const blobMock = createChromeMock({tabs: [{id: 7, url: 'http://127.0.0.1:2077/', title: '神工坊'}]});
  const blobRecord = plain(await background.dispatchCollect(blobMock.api, {
    url: 'blob:https://site.test/abc',
    pageUrl: 'https://site.test/p',
  }));
  assert.equal(blobRecord.ok, false);
  assert.equal(blobRecord.errorCode, 'blob_url');
  assert.match(blobRecord.message, /blob/);
  assert.equal(blobMock.api.action.badgeText, '!');
  assert.equal(blobMock.calls.filter(call => call.api === 'tabs.sendMessage').length, 0);

  const offlineMock = createChromeMock({tabs: [], onMessage: () => 'NO_RECEIVER'});
  const offlineRecord = plain(await background.dispatchCollect(offlineMock.api, {
    url: 'https://cdn.test/a.png',
    pageUrl: 'https://site.test/p',
  }));
  assert.equal(offlineRecord.ok, false);
  assert.equal(offlineRecord.errorCode, 'app_unreachable');
  assert.ok(offlineMock.calls.some(call => call.api === 'tabs.create'), '应尝试拉起工作台标签页');
  assert.equal(offlineMock.localMap.get(background.HISTORY_KEY)[0].errorCode, 'app_unreachable');
});

test('后台：右键菜单目标映射与历史记录上限', async () => {
  const background = loadBackground();
  const tab = {id: 3, url: 'https://site.test/p', title: '示例页'};

  assert.deepEqual(plain(background.menuRequest({menuItemId: 'gw-collect-image', srcUrl: 'https://cdn.test/i.png'}, tab)), {
    pageUrl: 'https://site.test/p',
    title: '示例页',
    url: 'https://cdn.test/i.png',
    kind: 'image',
  });
  assert.equal(background.menuRequest({menuItemId: 'gw-collect-link', linkUrl: 'https://cdn.test/a.zip'}, tab).kind, 'link');
  assert.equal(background.menuRequest({menuItemId: 'gw-collect-page'}, tab).url, 'https://site.test/p');
  assert.equal(background.MAX_HISTORY, 20);

  const seeded = [];
  for (let index = 0; index < 25; index += 1) seeded.push({url: `https://cdn.test/${index}.png`, ok: true});
  const mock = createChromeMock({
    tabs: [{id: 3, url: 'http://127.0.0.1:2077/', title: '神工坊'}],
    local: {[background.HISTORY_KEY]: seeded},
    onMessage: (tabId, message) => (
      message.type === 'gw:ensure-pair'
        ? {ok: true, token: 'token-5'}
        : {ok: true, jobId: 'job-5', elapsedMs: 320}
    ),
  });
  await background.dispatchCollect(mock.api, {url: 'https://cdn.test/new.png', pageUrl: 'https://site.test/p'});
  const history = mock.localMap.get(background.HISTORY_KEY);
  assert.equal(history.length, 20);
  assert.equal(history[0].url, 'https://cdn.test/new.png');
});

test('扩展源码零模型密钥、出向地址仅本机回环', () => {
  const allowedHosts = new Set(['127.0.0.1', 'localhost', 'www.apache.org']);
  const files = listFiles(EXT_DIR).filter(file => /\.(js|json|html|md)$/i.test(file));
  assert.ok(files.length >= 8, `扩展文件数异常: ${files.length}`);

  for (const file of files) {
    const name = relative(EXT_DIR, file).replace(/\\/g, '/');
    const text = readFileSync(file, 'utf8');
    for (const pattern of [/sk-[A-Za-z0-9]{8,}/, /\bapi[_-]?key\b/i, /\bBearer\s/i, /\beval\s*\(/, /new\s+Function\s*\(/]) {
      assert.ok(!pattern.test(text), `${name} 命中禁止模式 ${pattern}`);
    }
    for (const match of text.matchAll(/https?:\/\/[^\s"'`)<>,]+/g)) {
      let target;
      try {
        target = new URL(match[0]);
      } catch {
        // 通配匹配模式（如 http://*/*）不是可解析的出向地址，按模式放行。
        assert.ok(match[0].includes('*'), `${name} 出现无法解析的地址 ${match[0]}`);
        continue;
      }
      if (target.hostname === '*') continue; // content script 匹配模式，不是出向地址
      assert.ok(allowedHosts.has(target.hostname), `${name} 出现非本机地址 ${match[0]}`);
    }
  }

  // 快捷收集脚本不得自行发网络请求：收集必须经工作台页面源完成。
  assert.ok(!/\bfetch\s*\(/.test(readExtensionFile('content/collect-shortcut.js')));
  assert.ok(readExtensionFile('content/app-bridge.js').includes('GwCollectClient'));
});
