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
import { runInNewContext } from 'node:vm';
import test from 'node:test';

const playerSource = readFileSync(new URL('../../web/js/modules/media-preview-player.js', import.meta.url), 'utf8');

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

test('Video Segment formatters: formatVideoSegmentTime and formatVideoSegmentRange', () => {
  function formatVideoSegmentTime(seconds) {
    const sec = Math.max(0, Number(seconds || 0));
    if (!Number.isFinite(sec)) return '00:00';
    const whole = Math.floor(sec);
    const minutes = Math.floor(whole / 60);
    const remain = whole % 60;
    return `${String(minutes).padStart(2, '0')}:${String(remain).padStart(2, '0')}`;
  }

  function formatVideoSegmentRange(startSec, endSec) {
    return `${formatVideoSegmentTime(startSec)}~${formatVideoSegmentTime(endSec)}`;
  }

  assert.equal(formatVideoSegmentTime(6), '00:06');
  assert.equal(formatVideoSegmentTime(21), '00:21');
  assert.equal(formatVideoSegmentTime(125), '02:05');
  assert.equal(formatVideoSegmentRange(6.24, 21.05), '00:06~00:21');
  assert.equal(formatVideoSegmentRange(0, 5), '00:00~00:05');
});

test('Video Segment search matching: findMatchedVideoSegment by keyword tags', () => {
  function findMatchedVideoSegment(item, query = '') {
    const segments = item?.metadata?.video_segments || item?.video_segments;
    if (!Array.isArray(segments) || !segments.length) return null;
    const q = String(query || '').trim().toLowerCase();
    if (q) {
      const matched = segments.find(s => {
        const tags = Array.isArray(s.tags) ? s.tags : [];
        return tags.some(t => String(t).toLowerCase().includes(q));
      });
      if (matched) return matched;
    }
    return segments[0] || null;
  }

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

  const matched = findMatchedVideoSegment(asset, '大海');
  assert.ok(matched, 'should match segment with tag 大海');
  assert.equal(matched.start_sec, 6.24);
  assert.equal(matched.end_sec, 21.05);

  const matchedDefault = findMatchedVideoSegment(asset, '未知标签');
  assert.equal(matchedDefault.start_sec, 0.0, 'fallback to first segment when not matched');
});

test('Card and Detail Segment markup rendering', () => {
  function formatVideoSegmentTime(seconds) {
    const sec = Math.max(0, Number(seconds || 0));
    if (!Number.isFinite(sec)) return '00:00';
    const whole = Math.floor(sec);
    const minutes = Math.floor(whole / 60);
    const remain = whole % 60;
    return `${String(minutes).padStart(2, '0')}:${String(remain).padStart(2, '0')}`;
  }

  function formatVideoSegmentRange(startSec, endSec) {
    return `${formatVideoSegmentTime(startSec)}~${formatVideoSegmentTime(endSec)}`;
  }

  function renderVideoSegmentBadge(segment) {
    if (!segment) return '';
    const range = formatVideoSegmentRange(segment.start_sec, segment.end_sec);
    return `<span class="registry-card-segment-badge" data-video-jump-sec="${segment.start_sec}" data-video-jump-end="${segment.end_sec}" title="命中片段：${range}"><i data-lucide="play-circle"></i><strong>${range}</strong></span>`;
  }

  function renderDetailVideoSegmentsList(segments) {
    if (!Array.isArray(segments) || !segments.length) return '';
    return `<div class="asset-video-segments-list">${segments.map(seg => {
      const range = formatVideoSegmentRange(seg.start_sec, seg.end_sec);
      return `<button type="button" class="asset-video-segment-item" data-video-jump-sec="${seg.start_sec}" data-video-jump-end="${seg.end_sec}"><span>${range}</span></button>`;
    }).join('')}</div>`;
  }

  const seg = { start_sec: 6.24, end_sec: 21.05, tags: ['大海'] };
  const badge = renderVideoSegmentBadge(seg);
  assert.match(badge, /data-video-jump-sec="6\.24"/);
  assert.match(badge, />00:06~00:21</);

  const list = renderDetailVideoSegmentsList([seg]);
  assert.match(list, /asset-video-segment-item/);
  assert.match(list, />00:06~00:21</);
});
