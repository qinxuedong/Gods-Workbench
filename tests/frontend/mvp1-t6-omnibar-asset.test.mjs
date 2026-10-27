/**
 * Copyright 2026 Gods-Workbench Authors
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 *     http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */

// MVP1-T6 / AC7：Omnibar「素材」模式直接消费镜头级检索 API，命中项携带 start_sec，
// 由 gw:omnibar-result-select 交给素材页复用 C18-T5 跳帧。测试加载真实 omnibar.js，不重写被测逻辑。
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {runInNewContext} from 'node:vm';
import test from 'node:test';

const omnibarSource = readFileSync(new URL('../../web/js/core/omnibar.js', import.meta.url), 'utf8');
const SHOT_SEARCH_PATH = '/api/asset-registry/search/shots';

class FakeEventTarget {
  constructor() { this.listeners = new Map(); }
  addEventListener(type, listener) {
    if (!this.listeners.has(type)) this.listeners.set(type, []);
    this.listeners.get(type).push(listener);
  }
  removeEventListener(type, listener) {
    this.listeners.set(type, (this.listeners.get(type) || []).filter(candidate => candidate !== listener));
  }
  dispatchEvent(event) {
    event.target ??= this;
    event.currentTarget = this;
    (this.listeners.get(event.type) || []).forEach(listener => listener(event));
    return !event.defaultPrevented;
  }
}

class FakeElement extends FakeEventTarget {
  constructor(tagName, ownerDocument) {
    super();
    this.tagName = tagName.toUpperCase();
    this.ownerDocument = ownerDocument;
    this.children = [];
    this.attributes = new Map();
    this.dataset = {};
    this.className = '';
    this.classSet = new Set();
    this.classList = {
      add: name => this.classSet.add(name),
      remove: name => this.classSet.delete(name),
      contains: name => this.classSet.has(name),
      toggle: (name, enabled) => {
        const shouldEnable = enabled === undefined ? !this.classSet.has(name) : enabled;
        if (shouldEnable) this.classSet.add(name); else this.classSet.delete(name);
        return shouldEnable;
      },
    };
    this.hidden = false;
    this.value = '';
    this.textContent = '';
    this.isConnected = false;
    this.focusCount = 0;
  }
  append(...nodes) { nodes.forEach(node => this.appendChild(node)); }
  appendChild(node) {
    node.parentElement = this;
    node.isConnected = this.isConnected || this.tagName === 'BODY';
    this.children.push(node);
    return node;
  }
  replaceChildren(...nodes) {
    this.children = [];
    this.append(...nodes);
  }
  setAttribute(name, value) {
    this.attributes.set(name, String(value));
    if (name === 'hidden') this.hidden = true;
    if (name === 'id') this.id = String(value);
    if (name === 'class') this.className = String(value);
    if (name.startsWith('data-')) {
      const key = name.slice(5).replace(/-([a-z])/g, (_, letter) => letter.toUpperCase());
      this.dataset[key] = String(value);
    }
  }
  getAttribute(name) { return this.attributes.get(name) ?? null; }
  removeAttribute(name) { this.attributes.delete(name); }
  focus() { this.focusCount++; this.ownerDocument.activeElement = this; }
  scrollIntoView() {}
  querySelectorAll(selector) {
    const matches = [];
    const visit = parent => parent.children.forEach(child => {
      if (selector === '[role="option"]' && child.getAttribute('role') === 'option') matches.push(child);
      if (selector.startsWith('#') && child.id === selector.slice(1)) matches.push(child);
      visit(child);
    });
    visit(this);
    return matches;
  }
  querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
}

class FakeDocument extends FakeEventTarget {
  constructor() {
    super();
    this.readyState = 'complete';
    this.body = new FakeElement('body', this);
    this.body.isConnected = true;
    this.head = new FakeElement('head', this);
    this.documentElement = new FakeElement('html', this);
    this.documentElement.isConnected = true;
    this.body.parentElement = this.documentElement;
    this.documentElement.children.push(this.body);
    this.activeElement = this.body;
    this.iframes = [];
    this.defaultView = null;
  }
  createElement(tagName) { return new FakeElement(tagName, this); }
  createTextNode(value) { const node = new FakeElement('#text', this); node.textContent = value; return node; }
  getElementById(id) {
    if (this.body.id === id) return this.body;
    return this.body.querySelectorAll(`#${id}`)[0] || null;
  }
  querySelectorAll(selector) { return selector === 'iframe' ? this.iframes : this.body.querySelectorAll(selector); }
}

function harness({fetchImpl, frames = []} = {}) {
  const document = new FakeDocument();
  const window = new FakeEventTarget();
  const rafQueue = [];
  const timers = new Map();
  let nextTimerId = 1;
  class CustomEvent {
    constructor(type, options = {}) { this.type = type; this.detail = options.detail; }
  }
  class MutationObserver {
    constructor(callback) { this.callback = callback; }
    observe() {}
  }
  window.CustomEvent = CustomEvent;
  window.MutationObserver = MutationObserver;
  window.location = {href: 'http://workbench.local/pages/index.html', origin: 'http://workbench.local', assign() {}};
  window.requestAnimationFrame = callback => { rafQueue.push(callback); };
  window.setTimeout = (callback, delay = 0) => {
    if (delay === 0 || typeof delay !== 'number') {
      rafQueue.push(callback);
      return rafQueue.length;
    }
    const id = nextTimerId++;
    const nodeTimer = setTimeout(() => {
      timers.delete(id);
      callback();
    }, delay);
    timers.set(id, nodeTimer);
    return id;
  };
  window.clearTimeout = id => {
    if (timers.has(id)) {
      clearTimeout(timers.get(id));
      timers.delete(id);
    }
  };
  window.flushFrames = () => { while (rafQueue.length) rafQueue.shift()(); };
  window.dispatchEvent = event => FakeEventTarget.prototype.dispatchEvent.call(window, event);
  if (fetchImpl) window.fetch = fetchImpl;
  document.defaultView = window;
  for (const frame of frames) {
    document.iframes.push(frame);
    document.body.appendChild(frame);
  }
  const context = {window, document, URL, URLSearchParams, console};
  runInNewContext(omnibarSource, context);
  return {document, window, omnibar: window.GWOmnibar};
}

function keyEvent({key, code, ctrlKey = false, metaKey = false, altKey = false, shiftKey = false, isComposing = false, keyCode = 0}) {
  return {
    type: 'keydown', key, code, ctrlKey, metaKey, altKey, shiftKey, isComposing, keyCode, defaultPrevented: false,
    preventDefault() { this.defaultPrevented = true; },
    stopPropagation() { this.propagationStopped = true; },
    stopImmediatePropagation() { this.immediatePropagationStopped = true; },
  };
}

/** 等待防抖窗口结束（素材检索默认 160ms 防抖）。 */
async function settle(ms = 260) {
  await new Promise(resolve => setTimeout(resolve, ms));
}

function openAssetMode({fetchImpl, frames = []} = {}) {
  const env = harness({fetchImpl, frames});
  env.document.dispatchEvent(keyEvent({key: ' ', code: 'Space', ctrlKey: true}));
  env.document.getElementById('gwOmnibarModeAsset').dispatchEvent({type: 'click'});
  return {...env, input: env.document.getElementById('gwOmnibarInput')};
}

/** 素材页 iframe 桩：只暴露桥接所需的最小接口（src + contentWindow.postMessage）。 */
function frameStub(src, onPostMessage) {
  return {
    children: [],
    getAttribute: name => (name === 'src' ? src : null),
    addEventListener() {},
    removeEventListener() {},
    contentWindow: {postMessage: onPostMessage},
  };
}

function jsonResponse(body, {ok = true, status = 200} = {}) {
  return {
    ok,
    status,
    async json() { return body; },
  };
}

function shotPayload() {
  return {
    items: [
      {
        asset_id: 'asset-shot-1',
        name: '海边日落',
        granularity: 'shot',
        start_sec: 12,
        end_sec: 18,
        tags: ['日落', '海边'],
        summary: '镜头内出现海面与夕阳',
      },
      {
        asset_id: 'asset-file-1',
        name: '整段素材',
        granularity: 'asset',
        tags: [],
        summary: '',
      },
    ],
  };
}

test('素材模式是第三种模式，切换后更新占位符与 aria 状态', () => {
  const {document, omnibar, input} = openAssetMode();
  assert.equal(omnibar.snapshot().mode, 'asset');
  const button = document.getElementById('gwOmnibarModeAsset');
  assert.equal(button.getAttribute('aria-pressed'), 'true');
  assert.equal(document.getElementById('gwOmnibarModeSearch').getAttribute('aria-pressed'), 'false');
  assert.match(input.placeholder, /素材镜头/);
  assert.equal(input.getAttribute('aria-label'), '搜索素材镜头');
  assert.equal(document.getElementById('gwOmnibarPanel').dataset.mode, 'asset');
});

test('输入关键词按防抖请求镜头级检索端点，并带上 limit', async () => {
  const calls = [];
  const env = openAssetMode({
    fetchImpl: async url => {
      calls.push(String(url));
      return jsonResponse(shotPayload());
    },
  });
  env.input.value = '海边 日落';
  env.input.dispatchEvent({type: 'input'});
  await settle();

  assert.equal(calls.length, 1);
  const requested = new URL(calls[0], 'http://workbench.local');
  assert.equal(requested.pathname, SHOT_SEARCH_PATH);
  assert.equal(requested.searchParams.get('q'), '海边 日落');
  assert.equal(requested.searchParams.get('limit'), '12');
});

test('镜头命中项标题带时间范围，供用户辨认命中位置', async () => {
  const env = openAssetMode({fetchImpl: async () => jsonResponse(shotPayload())});
  env.input.value = '日落';
  env.input.dispatchEvent({type: 'input'});
  await settle();

  const options = env.document.getElementById('gwOmnibarResults').querySelectorAll('[role="option"]');
  assert.equal(options.length, 2);
  assert.equal(options[0].children[0].textContent, '海边日落 · 00:12~00:18');
  assert.match(options[0].children[1].textContent, /00:12~00:18/);
  assert.equal(options[1].children[0].textContent, '整段素材', '整段素材不应伪造时间范围');
});

test('选中镜头结果派发带 start_sec 与 granularity 的选择事件', async () => {
  const env = openAssetMode({fetchImpl: async () => jsonResponse(shotPayload())});
  const selections = [];
  env.window.addEventListener('gw:omnibar-result-select', event => selections.push(event.detail));

  env.input.value = '日落';
  env.input.dispatchEvent({type: 'input'});
  await settle();
  env.document.dispatchEvent(keyEvent({key: 'ArrowDown', code: 'ArrowDown'}));
  env.input.dispatchEvent(keyEvent({key: 'Enter', code: 'Enter'}));

  assert.equal(selections.length, 1);
  assert.equal(selections[0].result.asset_id, 'asset-shot-1');
  assert.equal(selections[0].result.granularity, 'shot');
  assert.equal(selections[0].result.start_sec, 12);
});

test('文件级命中不注入 start_sec，退回文件级打开', async () => {
  const env = openAssetMode({fetchImpl: async () => jsonResponse(shotPayload())});
  const selections = [];
  env.window.addEventListener('gw:omnibar-result-select', event => selections.push(event.detail));

  env.input.value = '素材';
  env.input.dispatchEvent({type: 'input'});
  await settle();
  env.document.dispatchEvent(keyEvent({key: 'ArrowDown', code: 'ArrowDown'}));
  env.document.dispatchEvent(keyEvent({key: 'ArrowDown', code: 'ArrowDown'}));
  env.input.dispatchEvent(keyEvent({key: 'Enter', code: 'Enter'}));

  assert.equal(selections.length, 1);
  assert.equal(selections[0].result.asset_id, 'asset-file-1');
  assert.equal(selections[0].result.granularity, 'asset');
  assert.equal(selections[0].result.start_sec, null);
});

test('未按方向键时 Enter 直接打开首个镜头命中', async () => {
  const env = openAssetMode({fetchImpl: async () => jsonResponse(shotPayload())});
  const selections = [];
  env.window.addEventListener('gw:omnibar-result-select', event => selections.push(event.detail));

  env.input.value = '日落';
  env.input.dispatchEvent({type: 'input'});
  await settle();
  env.input.dispatchEvent(keyEvent({key: 'Enter', code: 'Enter'}));

  assert.equal(selections.length, 1);
  assert.equal(selections[0].result.asset_id, 'asset-shot-1');
});

test('检索失败派发 ASSET_SEARCH_FAILED 并在面板内展示提示', async () => {
  const env = openAssetMode({
    fetchImpl: async () => jsonResponse({detail: {code: 'INTERNAL_ERROR'}}, {ok: false, status: 500}),
  });
  const errors = [];
  env.window.addEventListener('gw:omnibar-error', event => errors.push(event.detail.code));

  env.input.value = '日落';
  env.input.dispatchEvent({type: 'input'});
  await settle();

  assert.deepEqual(errors, ['ASSET_SEARCH_FAILED']);
  const alert = env.document.getElementById('gwOmnibarError');
  assert.equal(alert.hidden, false);
  assert.match(alert.textContent, /素材检索失败/);
});

test('索引未就绪时透出 SEARCH_INDEX_NOT_READY 引导重建索引', async () => {
  const env = openAssetMode({
    fetchImpl: async () => jsonResponse(
      {detail: {code: 'SEARCH_INDEX_NOT_READY'}},
      {ok: false, status: 503},
    ),
  });
  env.input.value = '日落';
  env.input.dispatchEvent({type: 'input'});
  await settle();

  const alert = env.document.getElementById('gwOmnibarError');
  assert.equal(alert.hidden, false);
  assert.match(alert.textContent, /重建索引/);
});

test('离开素材模式后迟到的检索响应不得覆盖当前结果', async () => {
  let release = null;
  const env = openAssetMode({
    fetchImpl: () => new Promise(resolve => { release = () => resolve(jsonResponse(shotPayload())); }),
  });
  env.input.value = '日落';
  env.input.dispatchEvent({type: 'input'});
  await settle();

  env.omnibar.setResults([{title: '项目 Alpha'}]);
  env.document.getElementById('gwOmnibarModeSearch').dispatchEvent({type: 'click'});
  release();
  await settle(60);

  const options = env.document.getElementById('gwOmnibarResults').querySelectorAll('[role="option"]');
  assert.equal(options.length, 1);
  assert.equal(options[0].children[0].textContent, '项目 Alpha');
});

test('清空关键词会清掉上一次镜头结果，避免误点旧命中', async () => {
  const env = openAssetMode({fetchImpl: async () => jsonResponse(shotPayload())});
  env.input.value = '日落';
  env.input.dispatchEvent({type: 'input'});
  await settle();
  assert.equal(env.document.getElementById('gwOmnibarResults').querySelectorAll('[role="option"]').length, 2);

  env.input.value = '   ';
  env.input.dispatchEvent({type: 'input'});
  await settle();

  assert.equal(env.document.getElementById('gwOmnibarResults').querySelectorAll('[role="option"]').length, 0);
});

test('关闭面板会作废在途检索，重新打开不留旧结果', async () => {
  let release = null;
  const env = openAssetMode({
    fetchImpl: () => new Promise(resolve => { release = () => resolve(jsonResponse(shotPayload())); }),
  });
  env.input.value = '日落';
  env.input.dispatchEvent({type: 'input'});
  await settle();

  env.document.dispatchEvent(keyEvent({key: 'Escape', code: 'Escape'}));
  env.window.flushFrames();
  release();
  await settle(60);

  assert.equal(env.omnibar.snapshot().open, false);
  assert.equal(env.document.getElementById('gwOmnibarResults').querySelectorAll('[role="option"]').length, 0);
});

test('选中镜头结果时把 asset_id 与 start_sec 经既有桥接递给素材页 iframe', async () => {
  const posted = [];
  const assetFrame = frameStub('/static/embeds/asset-manager.html?embedded=1', (payload, origin) => posted.push({payload, origin}));
  const otherFrame = frameStub('/static/embeds/canvas.html?embedded=1', () => assert.fail('非素材页 frame 不应收到素材跳转消息'));
  const env = openAssetMode({
    fetchImpl: async () => jsonResponse(shotPayload()),
    frames: [assetFrame, otherFrame],
  });

  env.input.value = '日落';
  env.input.dispatchEvent({type: 'input'});
  await settle();
  env.input.dispatchEvent(keyEvent({key: 'Enter', code: 'Enter'}));

  assert.equal(posted.length, 1);
  assert.equal(posted[0].payload.type, 'asset-global-search');
  assert.equal(posted[0].payload.asset_id, 'asset-shot-1');
  assert.equal(posted[0].payload.start_sec, 12);
  assert.equal(posted[0].origin, 'http://workbench.local');
});

test('文件级命中只传 asset_id，不伪造 start_sec', async () => {
  const posted = [];
  const assetFrame = frameStub('/static/embeds/asset-manager.html', payload => posted.push(payload));
  const env = openAssetMode({
    fetchImpl: async () => jsonResponse(shotPayload()),
    frames: [assetFrame],
  });

  env.input.value = '素材';
  env.input.dispatchEvent({type: 'input'});
  await settle();
  env.document.dispatchEvent(keyEvent({key: 'ArrowDown', code: 'ArrowDown'}));
  env.document.dispatchEvent(keyEvent({key: 'ArrowDown', code: 'ArrowDown'}));
  env.input.dispatchEvent(keyEvent({key: 'Enter', code: 'Enter'}));

  assert.equal(posted.length, 1);
  assert.equal(posted[0].asset_id, 'asset-file-1');
  assert.equal('start_sec' in posted[0], false);
});

test('跨源 frame 的 postMessage 失败不会中断同页选中事件', async () => {
  const selections = [];
  const env = openAssetMode({
    fetchImpl: async () => jsonResponse(shotPayload()),
    frames: [frameStub('https://evil.example/asset-manager.html', () => assert.fail('跨源 frame 不应被投递'))],
  });
  env.window.addEventListener('gw:omnibar-result-select', event => selections.push(event.detail.result.asset_id));

  env.input.value = '日落';
  env.input.dispatchEvent({type: 'input'});
  await settle();
  env.input.dispatchEvent(keyEvent({key: 'Enter', code: 'Enter'}));

  assert.deepEqual(selections, ['asset-shot-1']);
});
