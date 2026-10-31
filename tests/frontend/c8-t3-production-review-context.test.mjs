/**
 * Copyright 2026 Gods-Workbench Authors
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 */

import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';

const controllerSource = readFileSync('web/js/controllers/production-controller.js', 'utf8');

function response(body, status = 200) {
  return { ok: status >= 200 && status < 300, status, json: async () => body };
}

function createEnvironment(fetchHandler) {
  const elements = {};
  const makeElement = (tag, id = '') => {
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
        _classes: new Set(),
        add(name) { this._classes.add(name); },
        remove(name) { this._classes.delete(name); },
        toggle(name, force) { if (force === undefined) force = !this._classes.has(name); force ? this._classes.add(name) : this._classes.delete(name); },
        contains(name) { return this._classes.has(name); }
      },
      setAttribute(name, value) { this.attributes[name] = String(value); },
      getAttribute(name) { return this.attributes[name] ?? null; },
      removeAttribute(name) { delete this.attributes[name]; },
      contains() { return false; },
      querySelector() { return null; },
      querySelectorAll() { return []; },
      insertAdjacentHTML() {}
    };
    if (id) elements[id] = element;
    return element;
  };

  const title = makeElement('span', 'currentProjectDisplayTitle');
  const monitor = makeElement('img', 'mainMonitorImage');
  const monitorFrame = makeElement('div', 'monitorFrame');
  monitor.parentElement = monitorFrame;
  monitorFrame.children.push(monitor);
  const shotTitle = makeElement('h3', 'currentShotTitle');
  const prompt = makeElement('textarea', 'shotPromptInput');
  const sceneList = makeElement('div', 'sceneCatalogList');
  const episodePills = makeElement('div', 'catalogEpisodePills');
  const episodeTag = makeElement('span', 'catalogEpisodeTag');
  const shotTrack = makeElement('div', 'shotTrackList');
  const bus = makeElement('div', 'contextBusContainer');
  const busContent = makeElement('div');
  bus.children.push(busContent);
  const reviewPanel = makeElement('div', 'productionReviewPanel');
  const reviewComment = makeElement('textarea', 'productionReviewComment');
  reviewComment.value = '请调整光照';
  const addScene = makeElement('button');
  addScene.setAttribute('aria-label', '新建场次');
  const nav = makeElement('div', 'navPillsGroup');
  const navProject = makeElement('a');
  navProject.setAttribute('href', 'projects.html');
  const navProduction = makeElement('a');
  navProduction.setAttribute('href', 'production.html');
  const navStoryboard = makeElement('a');
  navStoryboard.setAttribute('href', 'storyboard.html?view=canvas');
  nav.children.push(navProject, navProduction, navStoryboard);
  const focal = [35, 50, 85].map(value => {
    const button = makeElement('button');
    button.dataset.productionFocal = String(value);
    return button;
  });

  const document = {
    readyState: 'loading',
    getElementById(id) { return elements[id] || null; },
    querySelector(selector) {
      if (selector === '#currentProjectDisplayTitle') return title;
      if (selector === '#mainMonitorImage') return monitor;
      if (selector === '#currentShotTitle') return shotTitle;
      if (selector === '#shotPromptInput') return prompt;
      if (selector === '#sceneCatalogList') return sceneList;
      if (selector === '#catalogEpisodePills') return episodePills;
      if (selector === '#catalogEpisodeTag') return episodeTag;
      if (selector === '#shotTrackList') return shotTrack;
      if (selector === '#contextBusContainer > .flex-1') return busContent;
      if (selector === 'button[aria-label="新建场次"]') return addScene;
      if (selector === '[data-production-review-panel]') return reviewPanel;
      return null;
    },
    querySelectorAll(selector) {
      if (selector === '#navPillsGroup a') return [navProject, navProduction, navStoryboard];
      if (selector === '[data-production-focal]') return focal;
      return [];
    },
    addEventListener() {}
  };

  const storage = new Map();
  const localStorage = {
    getItem(key) { return storage.get(key) ?? null; },
    setItem(key, value) { storage.set(key, String(value)); }
  };
  const window = {
    location: { search: '?project_id=project-c8-0001', origin: 'http://127.0.0.1:2077', href: 'http://127.0.0.1:2077/pages/production.html?project_id=project-c8-0001' },
    localStorage,
    document,
    URL,
    URLSearchParams,
    fetch: fetchHandler,
    alert() {},
    lucide: { createIcons() {} }
  };
  window.window = window;
  return { window, elements, reviewComment, navProject, navProduction, navStoryboard };
}

test('QIN-175 静态契约：消费 T2 与审阅 API，禁止演示进度回退', () => {
  assert.match(controllerSource, /\/api\/episode-pipelines/);
  assert.match(controllerSource, /\/api\/asset-reviews\/sessions/);
  assert.match(controllerSource, /version_id/);
  assert.match(controllerSource, /expected_version/);
  assert.match(controllerSource, /changes_requested/);
  assert.doesNotMatch(controllerSource, /setVUMeter\(['"]cpu['"]/);
  assert.doesNotMatch(controllerSource, /setTimeout\(\(\) => \{/);
});

test('QIN-175 真实流水线上下文按精确版本回读并可退回', async () => {
  const calls = [];
  let sessionExpectedVersion = 1;
  const detail = {
    session: {
      session_id: 'rs-c8-0001', review_id: 'rs-c8-0001', asset_id: 'asset-c8-0001',
      version_id: 'asset-c8-0001-v001', version: 1, expected_version: 1,
      review_status: 'pending_review', next_action: 'review'
    },
    comments: [], approvals: [], delivery: null, revision: 1
  };
  const fetchHandler = async (url, options = {}) => {
    calls.push({ url, options });
    if (url.startsWith('/api/projects/')) return response({ project: { project_id: 'project-c8-0001', name: 'C8 真实项目' } });
    if (url.startsWith('/api/episode-pipelines?')) return response({ pipelines: [{
      pipeline_id: 'ep-0001', project_id: 'project-c8-0001', title: 'C8 单镜头', version: 4,
      production_context: { episodes: [{ id: 'EP01', code: 'EP01', name: '第一集', scenes: [{ id: 'SC01', code: 'SC01', name: '主场景' }] }] },
      shots: [{ shot_id: 'SC01_SH001', episode_id: 'EP01', scene_id: 'SC01', status: 'completed',
        job_id: 'job-c8-0001', asset_id: 'asset-c8-0001', version_id: 'asset-c8-0001-v001', version: 1,
        source: { provider: 'comfyui', source_url: '/api/assets/asset-c8-0001/content' } }]
    }] });
    if (url === '/api/episode-pipelines/ep-0001/shots/SC01_SH001') return response({ shot: {
      project_id: 'project-c8-0001', shot_id: 'SC01_SH001', episode_id: 'EP01', scene_id: 'SC01', status: 'completed',
      next_action: 'review_source', job_id: 'job-c8-0001', asset_id: 'asset-c8-0001', version_id: 'asset-c8-0001-v001', version: 1,
      source: { provider: 'comfyui', source_url: '/api/assets/asset-c8-0001/content' }
    } });
    if (url.startsWith('/api/asset-reviews/sessions?')) return response({ sessions: [{ ...detail.session, expected_version: sessionExpectedVersion }], revision: sessionExpectedVersion });
    if (url === '/api/asset-reviews/sessions/rs-c8-0001') return response({ ...detail, session: { ...detail.session, expected_version: sessionExpectedVersion } });
    if (url === '/api/asset-reviews/sessions/rs-c8-0001/comments') {
      assert.equal(options.method, 'POST');
      const payload = JSON.parse(options.body);
      assert.equal(payload.body, '保留构图');
      assert.equal(payload.version_id, 'asset-c8-0001-v001');
      assert.equal(payload.version, 1);
      assert.equal(payload.expected_version, 1);
      sessionExpectedVersion = 2;
      return response({ comment: { comment_id: 'rc-c8-0001', review_id: 'rs-c8-0001', version_id: 'asset-c8-0001-v001', expected_version: 2 } }, 201);
    }
    if (url === '/api/asset-reviews/sessions/rs-c8-0001/approval') {
      assert.equal(options.method, 'PUT');
      const payload = JSON.parse(options.body);
      assert.equal(payload.decision, 'changes_requested');
      assert.equal(payload.version_id, 'asset-c8-0001-v001');
      assert.equal(payload.version, 1);
      assert.equal(payload.expected_version, 2);
      sessionExpectedVersion = 3;
      return response({ approval: { decision_id: 'rd-c8-0001', decision: 'changes_requested', review_status: 'changes_requested', next_action: 'regenerate_version' }, session: { ...detail.session, expected_version: 3, review_status: 'changes_requested', next_action: 'regenerate_version' } });
    }
    throw new Error(`unexpected request ${url}`);
  };

  const { window, elements, navProject, navProduction, navStoryboard } = createEnvironment(fetchHandler);
  const context = vm.createContext(window);
  vm.runInContext(controllerSource, context);
  const production = window.WorkbenchProduction;
  assert.ok(production);
  await production.initRouting();

  const state = production.getState();
  assert.equal(state.projectId, 'project-c8-0001');
  assert.equal(state.pipelineId, 'ep-0001');
  assert.equal(state.currentShot.id, 'SC01_SH001');
  assert.equal(state.currentShot.version_id, 'asset-c8-0001-v001');
  assert.equal(state.review.versionId, 'asset-c8-0001-v001');
  assert.equal(state.review.expectedVersion, 1);
  assert.equal(elements.currentShotTitle.textContent.includes('SC01_SH001'), true);
  // QIN-493：项目中心是选择器不携带参数；消费页（制片/分镜）继续携带当前项目。
  assert.doesNotMatch(navProject.getAttribute('href'), /project_id=/);
  assert.match(navProduction.getAttribute('href'), /project_id=project-c8-0001/);
  assert.match(navStoryboard.getAttribute('href'), /project_id=project-c8-0001/);

  const comment = await production.addReviewComment('保留构图');
  assert.equal(comment.comment_id, 'rc-c8-0001');
  assert.equal(production.getState().review.expectedVersion, 2);

  const result = await production.submitReviewDecision('changes_requested');
  assert.equal(result.approval.decision, 'changes_requested');
  assert.equal(production.getState().review.nextAction, 'regenerate_version');
  assert.ok(calls.some(call => call.url.endsWith('/approval')));
});
