/**
 * C13-T2 制片页宽银幕多轨样片播放器契约测试。
 */
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';

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
    currentTime: 0,
    duration: 0,
    readyState: 0,
    style: {},
    dataset: {},
    children: [],
    parentElement: null,
    attributes: {},
    disabled: false,
    playCalls: 0,
    pauseCalls: 0,
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
    setAttribute(name, value) { this.attributes[name] = String(value); },
    getAttribute(name) { return this.attributes[name] ?? null; },
    removeAttribute(name) { delete this.attributes[name]; },
    addEventListener() {},
    removeEventListener() {},
    contains() { return false; },
    querySelector() { return null; },
    querySelectorAll() { return []; },
    insertAdjacentHTML() {},
    play() { this.playCalls += 1; return Promise.resolve(); },
    pause() { this.pauseCalls += 1; }
  };
  element.parentElement = { children: [element], querySelector: () => null, querySelectorAll: () => [] };
  return element;
}

function createProductionHarness(fetchImpl) {
  const elements = {};
  for (const [tag, id] of [
    ['span', 'currentProjectDisplayTitle'], ['img', 'mainMonitorImage'], ['audio', 'animaticAudioTrack'],
    ['div', 'animaticStageFader'],
    ['h3', 'currentShotTitle'], ['textarea', 'shotPromptInput'], ['div', 'sceneCatalogList'],
    ['div', 'catalogEpisodePills'], ['span', 'catalogEpisodeTag'], ['div', 'shotTrackList'],
    ['div', 'contextBusContainer'], ['div', 'productionReviewPanel'], ['span', 'timelinePlaybackStatus'],
    ['button', 'timelinePlayToggle'], ['button', 'timelineStopToggle']
  ]) elements[id] = makeElement(tag, id);
  const monitorFrame = makeElement('div', 'monitorFrame');
  elements.mainMonitorImage.parentElement = monitorFrame;
  monitorFrame.children = [elements.mainMonitorImage];
  const faderGlow = makeElement('div');
  faderGlow.classList.add('hw-fader-glow-bar');
  const faderThumb = makeElement('div');
  faderThumb.classList.add('hw-fader-thumb-3d');
  elements.animaticStageFader.children = [faderGlow, faderThumb];
  elements.animaticStageFader.querySelector = selector => selector === '.hw-fader-glow-bar'
    ? faderGlow : (selector === '.hw-fader-thumb-3d' ? faderThumb : null);
  const bus = elements.contextBusContainer;
  const busContent = makeElement('div');
  bus.children = [busContent];
  const nav = [makeElement('a'), makeElement('a'), makeElement('a')];
  nav.forEach((item, index) => item.setAttribute('href', ['projects.html', 'production.html', 'storyboard.html?view=canvas'][index]));
  const focal = [35, 50, 85].map(value => {
    const item = makeElement('button');
    item.dataset.productionFocal = String(value);
    return item;
  });
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
  const storage = new Map([['workspace_project_id', 'proj-c13']]);
  const timers = [];
  const window = {
    location: { search: '?project_id=proj-c13', origin: 'http://localhost', href: 'http://localhost/production.html?project_id=proj-c13' },
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
  return { production: window.WorkbenchProduction, elements, timers };
}

const pipeline = {
  pipeline_id: 'ep-c13',
  project_id: 'proj-c13',
  version: 1,
  production_context: {
    episodes: [{ id: 'EP01', code: 'EP01', name: '第一集', scenes: [{ id: 'SC01', code: 'SC01', name: '主场景' }] }]
  },
  shots: [
    { shot_id: 'SC01_SH001', episode_id: 'EP01', scene_id: 'SC01', asset_id: 'asset-img-001', image_url: '/media/shot-001.png', camera: 'MS', movement: 'pan', status: 'completed' },
    { shot_id: 'SC01_SH002', episode_id: 'EP01', scene_id: 'SC01', asset_id: 'asset-img-002', image_url: '/media/shot-002.png', camera: 'CU', movement: 'dolly', status: 'completed' }
  ]
};

const manifest = {
  manifest_id: 'timeline-0013',
  pipeline_id: 'ep-c13',
  project_id: 'proj-c13',
  version: 1,
  tracks: [
    { track_id: 'video-01', kind: 'video', clips: [
      { shot_id: 'SC01_SH001', asset_id: 'asset-img-001', at: 0, duration: 1.5, overrides: { src: '/media/shot-001.png', camera: 'MS', movement: 'pan' } },
      { shot_id: 'SC01_SH002', asset_id: 'asset-img-002', at: 1.5, duration: 2, overrides: { src: '/media/shot-002.png', camera: 'CU', movement: 'dolly' } }
    ] },
    { track_id: 'dialogue-01', kind: 'audio', clips: [
      { shot_id: 'SC01_SH001', asset_id: 'asset-audio-001', at: 0, duration: 1.5, audio_url: '/media/dialogue-001.wav' },
      { shot_id: 'SC01_SH002', asset_id: 'asset-audio-002', at: 1.5, duration: 2, audio_url: '/media/dialogue-002.wav' }
    ] }
  ]
};

function createFetch() {
  return async url => {
    if (url === '/api/projects/proj-c13') return response({ project: { project_id: 'proj-c13', name: 'C13 项目' } });
    if (url === '/api/episode-pipelines?project_id=proj-c13') return response({ pipelines: [pipeline] });
    if (url === '/api/episode-pipelines/ep-c13/timeline-manifest') return response({ timeline_manifest: manifest });
    if (url.startsWith('/api/episode-pipelines/ep-c13/shots/')) {
      return response({ shot: pipeline.shots.find(item => url.endsWith(item.shot_id)) || {} });
    }
    throw new Error(`unexpected request ${url}`);
  };
}

test('C13-AC2 静态入口声明宽银幕动态样片与对白轨', () => {
  assert.match(productionHtml, /data-animatic-player/);
  assert.match(productionHtml, /id="animaticAudioTrack"/);
  assert.match(productionHtml, /data-animatic-track="dialogue"/);
  assert.match(productionHtml, /data-animatic-fader="stage"/);
  assert.match(productionSource, /timelineAudioClips/);
  assert.match(productionSource, /audio\.currentTime/);
});

test('C13-AC2 多轨连续播放按 target duration 切图并同步对白音频', async () => {
  const { production, elements, timers } = createProductionHarness(createFetch());
  await production.initRouting();

  let state = production.getState();
  assert.equal(state.timelineClips.length, 2);
  assert.equal(state.timelineAudioClips.length, 2);
  assert.equal(state.timelineClips[0].duration, 1.5);
  assert.equal(elements.mainMonitorImage.src, '/media/shot-001.png');
  assert.match(elements.shotTrackList.innerHTML, /data-timeline-active="true"/);
  assert.equal(elements.animaticStageFader.getAttribute('data-animatic-active-shot'), 'SC01_SH001');

  production.playTimeline();
  state = production.getState();
  assert.equal(state.timelinePlaying, true);
  assert.equal(elements.animaticAudioTrack.src, '/media/dialogue-001.wav');
  assert.equal(elements.animaticAudioTrack.currentTime, 0);
  assert.equal(elements.animaticAudioTrack.playCalls, 1);
  assert.equal(timers.at(-1).delay, 1500);

  timers.shift().callback();
  state = production.getState();
  assert.equal(state.activeTimelineIndex, 1);
  assert.equal(elements.mainMonitorImage.src, '/media/shot-002.png');
  assert.equal(elements.animaticAudioTrack.src, '/media/dialogue-002.wav');
  assert.equal(elements.animaticAudioTrack.currentTime, 0);
  assert.equal(elements.animaticAudioTrack.playCalls, 2);
  assert.equal(elements.animaticStageFader.getAttribute('data-animatic-active-shot'), 'SC01_SH002');
  assert.match(elements.shotTrackList.innerHTML, /data-timeline-index="1"[^>]*data-timeline-active="true"/);

  production.stopTimeline();
  assert.equal(production.getState().timelinePlaying, false);
  assert.ok(elements.animaticAudioTrack.pauseCalls >= 1);
});
