/**
 * C14-AC3 分镜台多 Take 接触印样与主力回选契约测试。
 */
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';

const storyboardHtml = readFileSync('web/pages/storyboard.html', 'utf8');
const contactSheetSource = readFileSync('web/js/modules/contact-sheet.js', 'utf8');
const storyboardSource = readFileSync('web/js/controllers/storyboard-controller.js', 'utf8');

function response(body, status = 200) {
  return { ok: status >= 200 && status < 300, status, json: async () => body };
}

function makeElement(tag, id = '') {
  const element = {
    tagName: tag.toUpperCase(),
    id,
    innerHTML: '',
    textContent: '',
    value: '',
    src: '',
    disabled: false,
    dataset: {},
    style: {},
    parentElement: null,
    children: [],
    classList: {
      values: new Set(),
      add(...names) { names.forEach(name => this.values.add(name)); },
      remove(...names) { names.forEach(name => this.values.delete(name)); },
      toggle(name, force) {
        const next = force === undefined ? !this.values.has(name) : Boolean(force);
        if (next) this.values.add(name); else this.values.delete(name);
        return next;
      },
      contains(name) { return this.values.has(name); }
    },
    contains() { return false; },
    closest() { return null; },
    querySelector() { return null; },
    querySelectorAll() { return []; },
    setAttribute(name, value) {
      this[name] = String(value);
      this.attributes ||= {};
      this.attributes[name] = String(value);
    },
    getAttribute(name) { return this.attributes?.[name] ?? null; },
    removeAttribute(name) { if (this.attributes) delete this.attributes[name]; },
    insertAdjacentHTML() {},
    focus() {}
  };
  element.parentElement = { children: [element], querySelector: () => null, querySelectorAll: () => [] };
  return element;
}

function createHarness(fetchImpl) {
  const elements = {};
  for (const [tag, id] of [
    ['div', 'storyboardGrid'], ['div', 'storyboardSceneNav'], ['h2', 'storyboardTitle'],
    ['span', 'navCountBadge'], ['button', 'btnSyncTimeline'], ['span', 'timelineSyncStatus'],
    ['button', 'btnSyncAgentStoryboard'], ['span', 'agentStoryboardSyncStatus']
  ]) elements[id] = makeElement(tag, id);
  const storage = new Map();
  const calls = [];
  const document = {
    readyState: 'complete',
    activeElement: null,
    body: { classList: { add() {}, remove() {} } },
    getElementById(id) { return elements[id] || null; },
    querySelector(selector) {
      if (selector === '#storyboardSceneNavContainer .font-mono.text-\\[\\#dfc384\\]') return elements.navCountBadge;
      return null;
    },
    querySelectorAll() { return []; },
    addEventListener() {}
  };
  const window = {
    location: {
      search: '?project_id=proj-c14&view=storyboard',
      origin: 'http://localhost',
      href: 'http://localhost/storyboard.html?project_id=proj-c14&view=storyboard'
    },
    localStorage: {
      getItem: key => storage.get(key) ?? null,
      setItem: (key, value) => storage.set(key, String(value))
    },
    document,
    URL,
    URLSearchParams,
    fetch: async (url, options = {}) => {
      calls.push({ url, options });
      return fetchImpl(url, options);
    },
    alert() {},
    lucide: { createIcons() {} }
  };
  window.window = window;
  const context = vm.createContext(window);
  vm.runInContext(contactSheetSource, context);
  vm.runInContext(storyboardSource, context);
  return { storyboard: window.WorkbenchStoryboard, contactSheet: window.WorkbenchContactSheet, elements, calls };
}

const take = (number, { primary = false } = {}) => ({
  take_id: `take-${number}`,
  asset_id: `asset-c14-${number}`,
  version_id: `asset-c14-${number}-v00${number}`,
  version: number,
  media_type: 'video',
  media_url: `/media/SC01_SH001-v00${number}.mp4`,
  status: 'completed',
  primary
});

const pipeline = {
  pipeline_id: 'ep-c14',
  project_id: 'proj-c14',
  version: 4,
  production_context: {
    episodes: [{ id: 'EP01', code: 'EP01', name: '第一集', scenes: [{ id: 'SC01', code: 'SC01', name: '主场景' }] }]
  },
  shots: [{
    shot_id: 'SC01_SH001',
    scene_id: 'SC01',
    episode_id: 'EP01',
    asset_id: 'asset-c14-1',
    version_id: 'asset-c14-1-v001',
    version: 1,
    image_url: '/media/SC01_SH001-v001.mp4',
    media_type: 'video',
    duration: 3,
    status: 'completed',
    takes: [take(1, { primary: true }), take(2), take(3)]
  }]
};

const manifest = {
  manifest_id: 'timeline-c14-0001',
  pipeline_id: 'ep-c14',
  project_id: 'proj-c14',
  version: 1,
  tracks: [{ track_id: 'video-01', kind: 'video', clips: [{
    shot_id: 'SC01_SH001',
    asset_id: 'asset-c14-1',
    version_id: 'asset-c14-1-v001',
    at: 0,
    duration: 3,
    overrides: { src: '/media/SC01_SH001-v001.mp4#t=0,3', camera: 'MS' }
  }] }]
};

test('C14-AC3 页面接入 contact sheet 与主力 Take 入口', () => {
  assert.match(storyboardHtml, /modules\/contact-sheet\.js/);
  assert.match(storyboardHtml, /data-contact-sheet/);
  assert.match(storyboardHtml, /多 Take|主力 Take/);
  assert.match(storyboardSource, /promoteTake/);
  assert.match(contactSheetSource, /currentTime/);
  assert.match(contactSheetSource, /data-take-preview/);
});

test('C14-AC3 同镜头展示 v001~v003 并支持接触印样预览', async () => {
  const { storyboard, contactSheet, elements } = createHarness(async url => {
    if (url === '/api/projects/proj-c14') return response({ project: { project_id: 'proj-c14', name: 'C14 项目' } });
    if (url === '/api/episode-pipelines?project_id=proj-c14') return response({ pipelines: [pipeline] });
    if (url.startsWith('/api/asset-reviews/sessions?')) return response({ sessions: [], revision: 1 });
    throw new Error(`unexpected request ${url}`);
  });

  await storyboard.ready;
  assert.ok(contactSheet);
  assert.match(elements.storyboardGrid.innerHTML, /data-contact-sheet/);
  assert.match(elements.storyboardGrid.innerHTML, /data-take-id="take-1"/);
  assert.match(elements.storyboardGrid.innerHTML, /data-take-id="take-2"/);
  assert.match(elements.storyboardGrid.innerHTML, /data-take-id="take-3"/);
  assert.match(elements.storyboardGrid.innerHTML, /v001/);
  assert.match(elements.storyboardGrid.innerHTML, /v002/);
  assert.match(elements.storyboardGrid.innerHTML, /v003/);
  assert.match(elements.storyboardGrid.innerHTML, /data-take-primary="true"/);
  assert.match(contactSheet.render(pipeline.shots[0].takes, 'SC01_SH001'), /data-take-hover/);
  assert.match(contactSheet.render(pipeline.shots[0].takes, 'SC01_SH001'), /<video /);
});

test('C14-AC3 点击设为主力 Take 以 CAS PUT 返回 200 并切换 timeline_manifest', async () => {
  let putBody = null;
  const { storyboard, elements, calls } = createHarness(async (url, options = {}) => {
    if (url === '/api/projects/proj-c14') return response({ project: { project_id: 'proj-c14', name: 'C14 项目' } });
    if (url === '/api/episode-pipelines?project_id=proj-c14') return response({ pipelines: [pipeline] });
    if (url.startsWith('/api/asset-reviews/sessions?')) return response({ sessions: [], revision: 1 });
    if (url === '/api/episode-pipelines/ep-c14/timeline-manifest' && (!options.method || options.method === 'GET')) {
      return response({ timeline_manifest: manifest });
    }
    if (url === '/api/episode-pipelines/ep-c14/timeline-manifest' && options.method === 'PUT') {
      putBody = JSON.parse(options.body);
      return response({ timeline_manifest: {
        ...manifest,
        version: 2,
        tracks: putBody.tracks,
        metadata: putBody.metadata
      } }, 200);
    }
    throw new Error(`unexpected request ${url}`);
  });

  await storyboard.ready;
  const result = await storyboard.promoteTake('SC01_SH001', 'take-2');
  assert.equal(result.timeline_manifest.version, 2);
  assert.equal(putBody.expected_version, 1);
  const clip = putBody.tracks[0].clips[0];
  assert.equal(clip.asset_id, 'asset-c14-2');
  assert.equal(clip.version_id, 'asset-c14-2-v002');
  assert.equal(clip.take_id, 'take-2');
  assert.match(clip.overrides.src, /SC01_SH001-v002\.mp4#t=0,3$/);
  assert.equal(storyboard.getState().shots[0].asset_id, 'asset-c14-2');
  assert.equal(storyboard.getState().shots[0].version_id, 'asset-c14-2-v002');
  assert.match(elements.storyboardGrid.innerHTML, /data-take-id="take-2"[^>]*data-take-primary="true"/);
  assert.match(elements.timelineSyncStatus.textContent, /已回选主力 Take/);
  assert.ok(calls.some(call => call.options.method === 'PUT' && call.url.endsWith('/timeline-manifest')));
});
