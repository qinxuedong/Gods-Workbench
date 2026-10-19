/**
 * C12-AC4 分镜同步与制片台时间线连播契约测试。
 */
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';

const storyboardHtml = readFileSync('web/pages/storyboard.html', 'utf8');
const storyboardSource = readFileSync('web/js/controllers/storyboard-controller.js', 'utf8');
const productionHtml = readFileSync('web/pages/production.html', 'utf8');
const productionSource = readFileSync('web/js/controllers/production-controller.js', 'utf8');

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
    alt: '',
    style: {},
    dataset: {},
    children: [],
    parentElement: null,
    attributes: {},
    disabled: false,
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
    setAttribute(name, value) { this.attributes[name] = String(value); if (name === 'aria-pressed') this.ariaPressed = String(value); },
    getAttribute(name) { return this.attributes[name] ?? null; },
    removeAttribute(name) { delete this.attributes[name]; },
    contains() { return false; },
    querySelector() { return null; },
    querySelectorAll() { return []; },
    insertAdjacentHTML() {}
  };
  element.parentElement = { children: [element], querySelector: () => null, querySelectorAll: () => [] };
  return element;
}

function createStoryboardHarness(fetchImpl) {
  const elements = {};
  for (const [tag, id] of [
    ['div', 'storyboardGrid'], ['div', 'storyboardSceneNav'], ['h2', 'storyboardTitle'],
    ['span', 'navCountBadge'], ['button', 'btnSyncAgentStoryboard'], ['span', 'agentStoryboardSyncStatus'],
    ['button', 'btnSyncTimeline'], ['span', 'timelineSyncStatus']
  ]) elements[id] = makeElement(tag, id);
  const storage = new Map();
  const calls = [];
  const document = {
    readyState: 'complete',
    activeElement: null,
    getElementById(id) { return elements[id] || null; },
    querySelector(selector) {
      if (selector === '#storyboardSceneNavContainer .font-mono.text-\\[\\#dfc384\\]') return elements.navCountBadge;
      return null;
    },
    querySelectorAll() { return []; },
    addEventListener() {}
  };
  const window = {
    location: { search: '?project_id=proj-c12&view=storyboard', origin: 'http://localhost', href: 'http://localhost/storyboard.html?project_id=proj-c12&view=storyboard' },
    localStorage: { getItem: key => storage.get(key) ?? null, setItem: (key, value) => storage.set(key, String(value)) },
    document,
    URL,
    URLSearchParams,
    fetch: async (url, options = {}) => { calls.push({ url, options }); return fetchImpl(url, options); },
    alert() {},
    lucide: { createIcons() {} }
  };
  window.window = window;
  vm.runInContext(storyboardSource, vm.createContext(window));
  return { storyboard: window.WorkbenchStoryboard, elements, calls };
}

function createProductionHarness(fetchImpl) {
  const elements = {};
  for (const [tag, id] of [
    ['span', 'currentProjectDisplayTitle'], ['img', 'mainMonitorImage'], ['h3', 'currentShotTitle'],
    ['textarea', 'shotPromptInput'], ['div', 'sceneCatalogList'], ['div', 'catalogEpisodePills'],
    ['span', 'catalogEpisodeTag'], ['div', 'shotTrackList'], ['div', 'contextBusContainer'],
    ['div', 'productionReviewPanel'], ['span', 'timelinePlaybackStatus'], ['button', 'timelinePlayToggle'],
    ['button', 'timelineStopToggle']
  ]) elements[id] = makeElement(tag, id);
  const monitorFrame = makeElement('div', 'monitorFrame');
  elements.mainMonitorImage.parentElement = monitorFrame;
  monitorFrame.children = [elements.mainMonitorImage];
  const bus = elements.contextBusContainer;
  const busContent = makeElement('div');
  bus.children = [busContent];
  const nav = [makeElement('a'), makeElement('a'), makeElement('a')];
  nav.forEach((item, index) => item.setAttribute('href', ['projects.html', 'production.html', 'storyboard.html?view=canvas'][index]));
  const focal = [35, 50, 85].map(value => { const item = makeElement('button'); item.dataset.productionFocal = String(value); return item; });
  const addScene = makeElement('button');
  addScene.setAttribute('aria-label', '新建场次');
  const document = {
    readyState: 'complete',
    activeElement: null,
    getElementById(id) { return elements[id] || null; },
    querySelector(selector) {
      if (selector === '#contextBusContainer > .flex-1') return busContent;
      if (selector === 'button[aria-label="新建场次"]') return addScene;
      return null;
    },
    querySelectorAll(selector) {
      if (selector === '#navPillsGroup a') return nav;
      if (selector === '[data-production-focal]') return focal;
      return [];
    },
    addEventListener() {}
  };
  const storage = new Map([['workspace_project_id', 'proj-c12']]);
  const timers = [];
  const window = {
    location: { search: '?project_id=proj-c12', origin: 'http://localhost', href: 'http://localhost/production.html?project_id=proj-c12' },
    history: { pushState() {} },
    localStorage: { getItem: key => storage.get(key) ?? null, setItem: (key, value) => storage.set(key, String(value)) },
    document,
    URL,
    URLSearchParams,
    fetch: async (url, options = {}) => fetchImpl(url, options),
    setTimeout(callback, delay) { timers.push({ callback, delay }); return timers.length; },
    clearTimeout() {},
    alert() {},
    lucide: { createIcons() {} }
  };
  window.window = window;
  vm.runInContext(productionSource, vm.createContext(window));
  return { production: window.WorkbenchProduction, elements, timers, storage };
}

const pipeline = {
  pipeline_id: 'ep-c12',
  project_id: 'proj-c12',
  version: 3,
  production_context: { episodes: [{ id: 'EP01', code: 'EP01', name: '第一集', scenes: [{ id: 'SC01', code: 'SC01', name: '主场景' }] }] },
  shots: [
    { shot_id: 'SC02_SH001', scene_id: 'SC02', episode_id: 'EP01', asset_id: 'asset-002', image_url: '/media/asset-002.png', camera: 'CU', movement: 'dolly', duration: 2, status: 'completed' },
    { shot_id: 'SC01_SH002', scene_id: 'SC01', episode_id: 'EP01', asset_id: 'asset-001b', image_url: '/media/asset-001b.png', camera: 'MS', movement: 'pan', duration: '3s', status: 'completed' }
  ]
};

 test('C12-AC4 静态入口：分镜同步按钮与制片台连播控件存在', () => {
  assert.match(storyboardHtml, /id="btnSyncTimeline"/);
  assert.match(storyboardHtml, /同步至时间线/);
  assert.match(storyboardHtml, /id="timelineSyncStatus"/);
  assert.match(productionHtml, /id="timelinePlayToggle"/);
  assert.match(productionHtml, /id="timelineStopToggle"/);
  assert.match(productionHtml, /id="timelinePlaybackStatus"/);
  assert.match(storyboardSource, /function syncTimeline\(/);
  assert.match(productionSource, /function playTimeline\(/);
  assert.match(productionSource, /timeline-manifest/);
});

test('C12-AC4 分镜一键同步按 SCxx_SHxxx 顺序提交 timeline_manifest', async () => {
  let posted = null;
  const { storyboard, elements, calls } = createStoryboardHarness(async (url, options = {}) => {
    if (url === '/api/projects/proj-c12') return response({ project: { project_id: 'proj-c12', name: 'C12 项目' } });
    if (url === '/api/episode-pipelines?project_id=proj-c12') return response({ pipelines: [pipeline] });
    if (url.startsWith('/api/asset-reviews/sessions?')) return response({ sessions: [], revision: 1 });
    if (url === '/api/episode-pipelines/ep-c12/timeline-manifest') {
      assert.equal(options.method, 'POST');
      posted = JSON.parse(options.body);
      return response({ timeline_manifest: { manifest_id: 'timeline-0001', pipeline_id: 'ep-c12', version: 1, ...posted } }, 201);
    }
    throw new Error(`unexpected request ${url}`);
  });

  await storyboard.ready;
  const result = await storyboard.syncTimeline();
  assert.equal(result.timeline_manifest.manifest_id, 'timeline-0001');
  assert.equal(posted.tracks.length, 1);
  assert.deepEqual(posted.tracks[0].clips.map(clip => clip.shot_id), ['SC01_SH002', 'SC02_SH001']);
  assert.deepEqual(posted.tracks[0].clips.map(clip => clip.at), [0, 3]);
  assert.equal(posted.tracks[0].clips[0].duration, 3);
  assert.equal(posted.tracks[0].clips[0].asset_id, 'asset-001b');
  assert.match(posted.tracks[0].clips[0].overrides.src, /#t=0,3$/);
  assert.equal(elements.timelineSyncStatus.textContent, '已同步 2 镜至时间线');
  assert.ok(calls.some(call => call.url.endsWith('/timeline-manifest')));
});

test('C12-AC4 制片台读取清单并按时序高亮、切换静态镜头', async () => {
  const manifest = {
    manifest_id: 'timeline-0001', pipeline_id: 'ep-c12', project_id: 'proj-c12', version: 1,
    tracks: [{ track_id: 'video-01', kind: 'video', clips: [
      { shot_id: 'SC01_SH002', asset_id: 'asset-001b', at: 0, duration: 3, overrides: { src: '/media/asset-001b.png#t=0,3', camera: 'MS', movement: 'pan' } },
      { shot_id: 'SC02_SH001', asset_id: 'asset-002', at: 3, duration: 2, overrides: { src: '/media/asset-002.png#t=0,2', camera: 'CU', movement: 'dolly' } }
    ] }]
  };
  const { production, elements, timers } = createProductionHarness(async (url) => {
    if (url === '/api/projects/proj-c12') return response({ project: { project_id: 'proj-c12', name: 'C12 项目' } });
    if (url === '/api/episode-pipelines?project_id=proj-c12') return response({ pipelines: [pipeline] });
    if (url === '/api/episode-pipelines/ep-c12/timeline-manifest') return response({ timeline_manifest: manifest });
    if (url.startsWith('/api/episode-pipelines/ep-c12/shots/')) return response({ shot: pipeline.shots.find(item => url.endsWith(item.shot_id)) || {} });
    throw new Error(`unexpected request ${url}`);
  });

  await production.initRouting();
  let state = production.getState();
  assert.equal(state.timelineManifest.manifest_id, 'timeline-0001');
  assert.equal(state.timelineClips.length, 2);
  assert.equal(state.activeTimelineIndex, 0);
  assert.match(elements.mainMonitorImage.src, /asset-001b\.png#t=0,3$/);
  assert.match(elements.shotTrackList.innerHTML, /data-timeline-active="true"/);
  assert.match(elements.shotTrackList.innerHTML, /MS/);
  assert.match(elements.shotTrackList.innerHTML, /pan/);

  production.playTimeline();
  assert.equal(production.getState().timelinePlaying, true);
  assert.equal(timers.length, 1);
  assert.equal(timers[0].delay, 3000);
  timers.shift().callback();
  state = production.getState();
  assert.equal(state.activeTimelineIndex, 1);
  assert.match(elements.mainMonitorImage.src, /asset-002\.png#t=0,2$/);
  assert.match(elements.shotTrackList.innerHTML, /data-timeline-index="1"[^>]*data-timeline-active="true"/);
  production.stopTimeline();
  assert.equal(production.getState().timelinePlaying, false);
});
