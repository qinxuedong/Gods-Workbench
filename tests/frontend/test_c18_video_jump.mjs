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

import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createContext, runInContext, runInNewContext } from 'node:vm';
import test from 'node:test';

// 真实加载源码，绝不在测试内重写待测函数
const playerSource = readFileSync(new URL('../../web/js/modules/media-preview-player.js', import.meta.url), 'utf8');
const assetManagerSource = readFileSync(new URL('../../web/js/modules/asset-manager.js', import.meta.url), 'utf8');

/** 按花括号配对从真实源码提取完整函数，保证测试运行的是生产代码 */
function extractFunction(source, name) {
  const marker = `function ${name}(`;
  const start = source.indexOf(marker);
  assert.ok(start >= 0, `源码中缺少函数 ${name}`);
  let depth = 0;
  for (let i = start; i < source.length; i++) {
    if (source[i] === '{') depth++;
    else if (source[i] === '}') {
      depth--;
      if (depth === 0) return source.slice(start, i + 1);
    }
  }
  assert.fail(`函数 ${name} 花括号不闭合`);
}

// 从 asset-manager.js 提取真实函数
const assetManagerHarnessContext = createContext({});
for (const fnName of ['formatVideoSegmentTime', 'formatVideoSegmentRange', 'alignTimeToFps', 'findMatchedVideoSegment']) {
  runInContext(extractFunction(assetManagerSource, fnName), assetManagerHarnessContext);
}
const realAssetManagerFns = {
  formatVideoSegmentTime: assetManagerHarnessContext.formatVideoSegmentTime,
  formatVideoSegmentRange: assetManagerHarnessContext.formatVideoSegmentRange,
  alignTimeToFps: assetManagerHarnessContext.alignTimeToFps,
  findMatchedVideoSegment: assetManagerHarnessContext.findMatchedVideoSegment,
};

class FakeEventTarget {
  constructor() {
    this.listeners = new Map();
  }
  addEventListener(type, listener, options = {}) {
    if (!this.listeners.has(type)) this.listeners.set(type, []);
    this.listeners.get(type).push({ listener, once: Boolean(options.once) });
  }
  removeEventListener(type, listener) {
    if (!this.listeners.has(type)) return;
    this.listeners.set(type, this.listeners.get(type).filter(item => item.listener !== listener));
  }
  dispatchEvent(event) {
    event.target ??= this;
    event.currentTarget = this;
    const items = [...(this.listeners.get(event.type) || [])];
    items.forEach(({ listener, once }) => {
      listener(event);
      if (once) this.removeEventListener(event.type, listener);
    });
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
    this.src = '';
    this.currentTime = 0;
    this.paused = true;
    this.playCount = 0;
    this.pauseCount = 0;
    this.loadCount = 0;
    this.readyState = 4; // 默认已加载完成 (HAVE_ENOUGH_DATA)
  }
  get firstChild() {
    return this.children[0] || null;
  }
  appendChild(node) {
    node.parentElement = this;
    this.children.push(node);
    return node;
  }
  removeChild(node) {
    const idx = this.children.indexOf(node);
    if (idx >= 0) {
      this.children.splice(idx, 1);
      node.parentElement = null;
    }
    return node;
  }
  append(...nodes) {
    nodes.forEach(n => this.appendChild(n));
  }
  setAttribute(name, value) {
    this.attributes.set(name, String(value));
    if (name.startsWith('data-')) {
      const key = name.slice(5).replace(/-([a-z])/g, (_, l) => l.toUpperCase());
      this.dataset[key] = String(value);
    }
  }
  getAttribute(name) {
    return this.attributes.get(name) ?? null;
  }
  removeAttribute(name) {
    this.attributes.delete(name);
    if (name.startsWith('data-')) {
      const key = name.slice(5).replace(/-([a-z])/g, (_, l) => l.toUpperCase());
      delete this.dataset[key];
    }
  }
  querySelector(selector) {
    if (selector === 'video, audio' || selector === 'video') {
      return this.children.find(c => c.tagName === 'VIDEO' || c.tagName === 'AUDIO') || null;
    }
    return null;
  }
  querySelectorAll(selector) {
    return this.children.filter(c => selector.includes(c.tagName.toLowerCase()));
  }
  play() {
    this.paused = false;
    this.playCount++;
    return Promise.resolve();
  }
  pause() {
    this.paused = true;
    this.pauseCount++;
  }
  load() {
    this.loadCount++;
  }
}

class FakeDocument {
  constructor() {
    this.readyState = 'complete';
  }
  createElement(tagName) {
    return new FakeElement(tagName, this);
  }
  addEventListener() {}
  removeEventListener() {}
}

function createPlayerContext() {
  const document = new FakeDocument();
  const fetchMock = async () => ({
    ok: true,
    status: 200,
    json: async () => ({
      availability: 'ready',
      media_type: 'video/mp4',
      proxy_url: '/api/video-proxy/asset-123.mp4',
      artifact_id: 'art-456',
    }),
  });
  const context = {
    document,
    window: { document, fetch: fetchMock },
    globalThis: { document, fetch: fetchMock },
    fetch: fetchMock,
    console,
    URL,
    URLSearchParams,
  };
  context.window.window = context.window;
  context.window.globalThis = context.window;
  runInNewContext(playerSource, context);
  return { context, document, player: context.window.MediaPreviewPlayer };
}

test('MediaPreviewPlayer: mount with startTime appends HTML5 fragment #t=xx', async () => {
  const { document, player } = createPlayerContext();
  const container = document.createElement('div');
  container.dataset.mediaPreview = 'true';
  container.dataset.assetId = 'asset-123';
  container.dataset.mediaKind = 'video';
  container.dataset.startTime = '6.24';

  await player.mount(container);

  const video = container.querySelector('video');
  assert.ok(video, 'video element should be created');
  assert.match(video.src, /#t=6\.24$/, 'video src should end with #t=6.24 fragment');

  video.dispatchEvent({ type: 'loadedmetadata' });
  assert.equal(video.currentTime, 6.24, 'video currentTime should be set to 6.24 on loadedmetadata');
});

test('MediaPreviewPlayer: seekTo smoothly updates currentTime and triggers play if requested', async () => {
  const { document, player } = createPlayerContext();
  const container = document.createElement('div');
  container.dataset.mediaPreview = 'true';
  container.dataset.assetId = 'asset-123';
  container.dataset.mediaKind = 'video';

  await player.mount(container);
  const video = container.querySelector('video');
  assert.ok(video);

  const seekResult = player.seekTo(container, 14.5, { play: true });
  assert.equal(seekResult, true);
  assert.equal(video.currentTime, 14.5);
  assert.equal(container.dataset.startTime, '14.5');
  assert.equal(video.playCount, 1, 'play should be triggered when play: true');
});

test('MediaPreviewPlayer: seekTo waits for loadedmetadata instead of blind frame delay when readyState is 0', async () => {
  const { document, player } = createPlayerContext();
  const container = document.createElement('div');
  container.dataset.mediaPreview = 'true';
  container.dataset.assetId = 'asset-123';
  container.dataset.mediaKind = 'video';

  await player.mount(container);
  const video = container.querySelector('video');
  assert.ok(video);

  // 模拟元数据尚未加载 (readyState = 0)
  video.readyState = 0;
  video.currentTime = 0;

  const seekResult = player.seekTo(container, 18.25, { play: true });
  assert.equal(seekResult, true, 'seekTo 应成功挂载就绪监听器');
  assert.equal(video.currentTime, 0, '元数据加载前不应盲目设置 currentTime');

  // 当浏览器 loadedmetadata 事件到达时，自动完成定位与起播
  video.readyState = 1;
  video.dispatchEvent({ type: 'loadedmetadata' });
  assert.equal(video.currentTime, 18.25, 'loadedmetadata 到达后准确同步 currentTime');
  assert.equal(video.playCount, 1, '起播请求被顺延执行');
});

test('MediaPreviewPlayer: seekTo handles play() rejection gracefully (browser autoplay restrictions)', async () => {
  const { document, player } = createPlayerContext();
  const container = document.createElement('div');
  container.dataset.mediaPreview = 'true';
  container.dataset.assetId = 'asset-123';
  container.dataset.mediaKind = 'video';

  await player.mount(container);
  const video = container.querySelector('video');
  assert.ok(video);

  let caughtRejection = false;
  video.play = () => {
    caughtRejection = true;
    return Promise.reject(new Error('NotAllowedError: play() failed because user gesture required'));
  };

  // 即使 play() 被浏览器策略拦截拒绝，seekTo 也不会抛出未捕获异常
  assert.doesNotThrow(() => {
    const result = player.seekTo(container, 10.0, { play: true });
    assert.equal(result, true, 'seekTo 设置时间依然返回 true');
  });
  assert.equal(caughtRejection, true, 'play rejection 被安全拦截');
  assert.equal(video.currentTime, 10.0);
});

test('MediaPreviewPlayer: destroy cleans up media resources and pauses video (prevents memory leak)', async () => {
  const { document, player } = createPlayerContext();
  const container = document.createElement('div');
  container.dataset.mediaPreview = 'true';
  container.dataset.assetId = 'asset-123';
  container.dataset.mediaKind = 'video';
  container.dataset.startTime = '5.0';

  await player.mount(container);
  const video = container.querySelector('video');
  assert.ok(video);

  player.destroy(container);

  assert.equal(video.pauseCount, 1, 'video should be paused on destroy');
  assert.equal(video.loadCount, 1, 'video load should be called to release decoder memory');
  assert.equal(video.src, '', 'video src should be cleared');
  assert.equal(container.children.length, 0, 'container should be emptied');
  assert.equal(container.dataset.previewState, undefined);
  assert.equal(container.dataset.startTime, undefined);
});

test('MediaPreviewPlayer: stress test 100 mount/destroy cycles without reference retention', async () => {
  const { document, player } = createPlayerContext();
  const container = document.createElement('div');
  container.dataset.mediaPreview = 'true';
  container.dataset.assetId = 'asset-123';
  container.dataset.mediaKind = 'video';

  for (let i = 0; i < 100; i++) {
    container.dataset.startTime = String(i * 0.5);
    await player.mount(container);
    assert.equal(container.children.length, 1);
    player.destroy(container);
    assert.equal(container.children.length, 0);
  }
  assert.equal(container.children.length, 0, 'all media children properly recycled in stress test');
});

test('MediaPreviewPlayer: seekTo returns false when video is damaged (error or networkState=3)', async () => {
  const { document, player } = createPlayerContext();
  const container = document.createElement('div');
  container.dataset.mediaPreview = 'true';
  container.dataset.assetId = 'asset-err';
  container.dataset.mediaKind = 'video';

  await player.mount(container);
  const video = container.querySelector('video');
  assert.ok(video);

  // Normal seek succeeds
  assert.equal(player.seekTo(container, 10), true);

  // Damaged state 1: error object present
  video.error = { code: 4, message: 'MEDIA_ELEMENT_ERROR: Format error' };
  assert.equal(player.seekTo(container, 12), false, 'seekTo should fail when video.error exists');

  // Damaged state 2: networkState === 3 (NETWORK_NO_SOURCE)
  delete video.error;
  video.networkState = 3;
  assert.equal(player.seekTo(container, 12), false, 'seekTo should fail when networkState is NETWORK_NO_SOURCE');
});

test('Real source formatters: formatVideoSegmentTime and formatVideoSegmentRange from asset-manager.js and MediaPreviewPlayer', () => {
  const { formatVideoSegmentTime, formatVideoSegmentRange } = realAssetManagerFns;
  const { player } = createPlayerContext();

  // 1. 验证从 asset-manager.js 提取的真实源码
  assert.equal(formatVideoSegmentTime(6), '00:06');
  assert.equal(formatVideoSegmentTime(21), '00:21');
  assert.equal(formatVideoSegmentTime(125), '02:05');
  assert.equal(formatVideoSegmentRange(6.24, 21.05), '00:06~00:21');
  assert.equal(formatVideoSegmentRange(0, 5), '00:00~00:05');

  // 2. 验证 MediaPreviewPlayer 暴露的真实函数行为严格一致
  assert.equal(player.formatVideoSegmentTime(6), '00:06');
  assert.equal(player.formatVideoSegmentRange(6.24, 21.05), '00:06~00:21');
});

test('Real source search matching: findMatchedVideoSegment from asset-manager.js and MediaPreviewPlayer', () => {
  const { findMatchedVideoSegment } = realAssetManagerFns;
  const { player } = createPlayerContext();

  const asset = {
    id: 'asset-ocean-001',
    name: '海边日出.mp4',
    metadata: {
      video_segments: [
        { start_sec: 0.0, end_sec: 6.24, tags: ['日出', '天空'], thumbnail: 'scene_001.webp' },
        { start_sec: 6.24, end_sec: 21.05, tags: ['大海', '海浪'], thumbnail: 'scene_002.webp' },
        { start_sec: 21.05, end_sec: 35.0, tags: ['沙滩', '足迹'], thumbnail: 'scene_003.webp' },
      ],
    },
  };

  // 1. 搜索“大海”精准命中区间 [6.24, 21.05]
  const matched = findMatchedVideoSegment(asset, '大海');
  assert.ok(matched, 'should match segment with tag 大海');
  assert.equal(matched.start_sec, 6.24);
  assert.equal(matched.end_sec, 21.05);

  const playerMatched = player.findMatchedVideoSegment(asset, '大海');
  assert.equal(playerMatched.start_sec, 6.24);
  assert.equal(playerMatched.end_sec, 21.05);

  // 2. 空搜索返回 null
  const matchedEmpty = findMatchedVideoSegment(asset, '');
  assert.equal(matchedEmpty, null, 'should return null when query is empty');

  // 3. 绝不回退显示第一段
  const matchedDefault = findMatchedVideoSegment(asset, '未知标签');
  assert.equal(matchedDefault, null, 'should return null when not matched (no fallback to first segment)');
  assert.equal(player.findMatchedVideoSegment(asset, '未知标签'), null);
});

test('Real source frame rate alignment: alignTimeToFps for 24, 25, 29.97fps from asset-manager.js and MediaPreviewPlayer', () => {
  const { alignTimeToFps } = realAssetManagerFns;
  const { player } = createPlayerContext();

  // 24fps: 1 frame = 1/24 = 0.041666...s
  // 6.24s at 24fps: 6.24 * 24 = 149.76 -> frame 150 -> 150/24 = 6.25s
  const aligned24 = alignTimeToFps(6.24, 24);
  assert.equal(aligned24, 6.25);
  assert.equal(player.alignTimeToFps(6.24, 24), 6.25);

  // 25fps: 1 frame = 0.04s
  // 6.24s at 25fps: 6.24 * 25 = 156 frames exact -> 156/25 = 6.24s
  const aligned25 = alignTimeToFps(6.24, 25);
  assert.equal(aligned25, 6.24);
  assert.equal(player.alignTimeToFps(6.24, 25), 6.24);

  // 29.97fps: 30000/1001 approx 29.97003
  // 6.24s at 29.97fps: 6.24 * (30000/1001) = 187.01298 -> frame 187 -> 187 * 1001 / 30000 = 6.2395666...
  const aligned2997 = alignTimeToFps(6.24, 29.97);
  assert.ok(Math.abs(aligned2997 - 6.2395666) < 0.001);
  assert.ok(Math.abs(player.alignTimeToFps(6.24, 29.97) - 6.2395666) < 0.001);

  // 0 或非法 fps 安全回退返回原始秒数
  assert.equal(alignTimeToFps(6.24, 0), 6.24);
  assert.equal(player.alignTimeToFps(6.24, 0), 6.24);
});
