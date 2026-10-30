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
import test from 'node:test';
import vm from 'node:vm';

const controllerSource = readFileSync('web/js/controllers/production-controller.js', 'utf8');

function createMockEnvironment(options = {}) {
  const {
    urlSearch = '',
    storedProjectId = null,
    fetchHandler = async () => ({ ok: false, status: 404, json: async () => ({ detail: { code: 'PROJECT_NOT_FOUND', message: 'Project not found' } }) })
  } = options;

  const elements = {};

  const makeElement = (tag, id = '', attrs = {}) => {
    const el = {
      tagName: tag.toUpperCase(),
      id,
      innerHTML: '',
      textContent: '',
      value: '',
      src: '',
      alt: '',
      style: {},
      dataset: { ...attrs },
      children: [],
      parentElement: null,
      attributes: {},
      disabled: false,
      classList: {
        _classes: new Set(),
        add(c) { this._classes.add(c); },
        remove(c) { this._classes.delete(c); },
        toggle(c, force) {
          if (force === undefined) {
            if (this._classes.has(c)) this._classes.delete(c); else this._classes.add(c);
          } else if (force) {
            this._classes.add(c);
          } else {
            this._classes.delete(c);
          }
        },
        contains(c) { return this._classes.has(c); }
      },
      setAttribute(k, v) { this.attributes[k] = String(v); if (k === 'aria-label') this.ariaLabel = String(v); },
      getAttribute(k) { return this.attributes[k] ?? null; },
      removeAttribute(k) { delete this.attributes[k]; },
      contains() { return false; },
      remove() {
        if (this.parentElement) {
          const idx = this.parentElement.children.indexOf(this);
          if (idx !== -1) this.parentElement.children.splice(idx, 1);
          this.parentElement = null;
        }
      },
      querySelector(sel) {
        if (sel === '[data-production-empty-state]') {
          return this.children.find(c => c.getAttribute('data-production-empty-state') !== null) || null;
        }
        return null;
      },
      querySelectorAll() { return []; },
      insertAdjacentHTML(position, html) {
        const child = makeElement('div', '', { 'data-production-empty-state': '' });
        child.innerHTML = html;
        child.parentElement = this;
        this.children.push(child);
      }
    };
    if (id) elements[id] = el;
    return el;
  };

  // 构造制片页 DOM 骨架
  const titleEl = makeElement('span', 'currentProjectDisplayTitle');
  const monitorImg = makeElement('img', 'mainMonitorImage');
  const monitorFrame = makeElement('div', 'monitorFrame');
  monitorImg.parentElement = monitorFrame;
  monitorFrame.children.push(monitorImg);

  const shotTitleEl = makeElement('h3', 'currentShotTitle');
  const promptInput = makeElement('textarea', 'shotPromptInput');
  const catalogList = makeElement('div', 'sceneCatalogList');
  const episodePills = makeElement('div', 'catalogEpisodePills');
  const episodeTag = makeElement('span', 'catalogEpisodeTag');
  const shotTrackList = makeElement('div', 'shotTrackList');

  const busWrapper = makeElement('div', 'contextBusContainer');
  const busFlex1 = makeElement('div');
  busWrapper.children.push(busFlex1);

  const addSceneBtn = makeElement('button', '', { 'aria-label': '新建场次' });
  addSceneBtn.setAttribute('aria-label', '新建场次');

  const navPillsGroup = makeElement('div', 'navPillsGroup');
  const navLink1 = makeElement('a');
  navLink1.setAttribute('href', 'projects.html');
  const navLink2 = makeElement('a');
  navLink2.setAttribute('href', 'production.html');
  const navLink3 = makeElement('a');
  navLink3.setAttribute('href', 'storyboard.html?view=canvas');
  navPillsGroup.children.push(navLink1, navLink2, navLink3);

  const focal35 = makeElement('button', '', { 'data-production-focal': '35' });
  focal35.dataset.productionFocal = '35';
  const focal50 = makeElement('button', '', { 'data-production-focal': '50' });
  focal50.dataset.productionFocal = '50';
  const focal85 = makeElement('button', '', { 'data-production-focal': '85' });
  focal85.dataset.productionFocal = '85';

  const doc = {
    readyState: 'complete',
    getElementById(id) { return elements[id] || null; },
    querySelector(sel) {
      if (sel === '#currentProjectDisplayTitle') return titleEl;
      if (sel === '#mainMonitorImage') return monitorImg;
      if (sel === '#currentShotTitle') return shotTitleEl;
      if (sel === '#shotPromptInput') return promptInput;
      if (sel === '#sceneCatalogList') return catalogList;
      if (sel === '#catalogEpisodePills') return episodePills;
      if (sel === '#catalogEpisodeTag') return episodeTag;
      if (sel === '#shotTrackList') return shotTrackList;
      if (sel === '#contextBusContainer > .flex-1') return busFlex1;
      if (sel === 'button[aria-label="新建场次"]') return addSceneBtn;
      return null;
    },
    querySelectorAll(sel) {
      if (sel === '#navPillsGroup a') return [navLink1, navLink2, navLink3];
      if (sel === '[data-production-focal]') return [focal35, focal50, focal85];
      if (sel === '[data-production-episode]') return [];
      if (sel === '[data-production-scene]') return [];
      return [];
    },
    addEventListener() {}
  };

  const storage = new Map();
  if (storedProjectId) storage.set('workspace_project_id', storedProjectId);

  const localStorageMock = {
    getItem: k => storage.get(k) ?? null,
    setItem: (k, v) => storage.set(k, String(v)),
    removeItem: k => storage.delete(k)
  };

  const mockWindow = {
    location: {
      search: urlSearch,
      origin: 'http://127.0.0.1:2077',
      href: `http://127.0.0.1:2077/pages/production.html${urlSearch}`
    },
    localStorage: localStorageMock,
    document: doc,
    alert() {},
    lucide: { createIcons() {} },
    URLSearchParams: globalThis.URLSearchParams,
    URL: globalThis.URL,
    fetch: fetchHandler
  };
  mockWindow.window = mockWindow;

  return { mockWindow, elements, busFlex1, addSceneBtn, navLinks: [navLink1, navLink2, navLink3], focalButtons: [focal35, focal50, focal85], storage };
}

test('QIN-180 静态规范：无 demo 假数据，具备真实上下文解析与未接入降级契约', () => {
  assert.doesNotMatch(controllerSource, /DEMO_PREVIEW/);
  assert.doesNotMatch(controllerSource, /\b(?:const|let|var)\s+demoProjectCatalog\b/);
  assert.doesNotMatch(controllerSource, /proj-01/);
  assert.match(controllerSource, /params\.get\('project_id'\) \|\| localStorage\.getItem\('workspace_project_id'\) \|\| ''/);
  assert.match(controllerSource, /function renderNotIntegratedState\(\)/);
  assert.match(controllerSource, /data-gw-degradation="not_integrated"/);
  assert.match(controllerSource, /bindCurrentShot/);
  assert.match(controllerSource, /bindProductionContext/);
});

test('QIN-180 上下文缺失（无 URL 参数且无 localStorage）触发标准未接入降级', async () => {
  const { mockWindow, elements, busFlex1 } = createMockEnvironment({
    urlSearch: '',
    storedProjectId: null
  });

  const context = vm.createContext(mockWindow);
  vm.runInContext(controllerSource, context);

  const production = mockWindow.WorkbenchProduction;
  assert.ok(production, 'WorkbenchProduction 必须挂载至 window');

  // 等待初始化路由解析完成
  await production.initRouting();

  assert.equal(elements.currentProjectDisplayTitle.textContent, '项目数据未接入 · 剧集制片工坊');
  assert.equal(elements.currentShotTitle.textContent, '镜头数据未接入');
  assert.equal(elements.shotPromptInput.value, '');
  assert.equal(elements.mainMonitorImage.getAttribute('data-gw-degradation'), 'not_integrated');
  assert.equal(elements.mainMonitorImage.style.display, 'none');
  assert.match(elements.sceneCatalogList.innerHTML, /data-gw-degradation="not_integrated"/);
  assert.match(elements.shotTrackList.innerHTML, /data-gw-degradation="not_integrated"/);
  assert.match(busFlex1.innerHTML, /data-gw-degradation="not_integrated"/);
});

test('QIN-180 项目不存在或权限不足（404 / 403）严格 fail closed，不伪造展示', async () => {
  let requestedUrl = '';
  const { mockWindow, elements } = createMockEnvironment({
    urlSearch: '?project_id=proj_non_existent',
    fetchHandler: async url => {
      requestedUrl = url;
      return {
        ok: false,
        status: 404,
        json: async () => ({ detail: { code: 'PROJECT_NOT_FOUND', message: 'Project not found' } })
      };
    }
  });

  const context = vm.createContext(mockWindow);
  vm.runInContext(controllerSource, context);
  const production = mockWindow.WorkbenchProduction;

  await production.initRouting();

  assert.match(requestedUrl, /\/api\/projects\/proj_non_existent/);
  assert.equal(elements.currentProjectDisplayTitle.textContent, '项目数据未接入 · 剧集制片工坊');
  assert.equal(elements.mainMonitorImage.getAttribute('data-gw-degradation'), 'not_integrated');
  assert.match(elements.sceneCatalogList.innerHTML, /data-gw-degradation="not_integrated"/);
});

test('QIN-180 真实项目校验成功（200 OK）正确绑定标题并传递导航参数', async () => {
  let requestedUrl = '';
  const { mockWindow, elements, navLinks, storage } = createMockEnvironment({
    urlSearch: '?project_id=proj_1234567890ab',
    fetchHandler: async url => {
      requestedUrl = url;
      return {
        ok: true,
        status: 200,
        json: async () => ({
          project: {
            project_id: 'proj_1234567890ab',
            name: '神谕之地',
            project_type: 'series',
            stage: 'production',
            progress: 15.0,
            version: 1
          }
        })
      };
    }
  });

  const context = vm.createContext(mockWindow);
  vm.runInContext(controllerSource, context);
  const production = mockWindow.WorkbenchProduction;

  await production.initRouting();

  assert.match(requestedUrl, /\/api\/projects\/proj_1234567890ab/);
  assert.equal(elements.currentProjectDisplayTitle.textContent, '《神谕之地》· 剧集制片工坊');
  assert.equal(storage.get('workspace_project_id'), 'proj_1234567890ab');

  // 导航栏按白名单携带项目上下文（QIN-493）：项目中心是选择器不携带，消费页携带。
  assert.doesNotMatch(navLinks[0].getAttribute('href'), /project_id=/);
  assert.match(navLinks[1].getAttribute('href'), /project_id=proj_1234567890ab/);
  assert.match(navLinks[2].getAttribute('href'), /project_id=proj_1234567890ab/);

  // 无镜头数据时镜头依然保持未接入空态降级，不捏造镜头
  assert.equal(elements.currentShotTitle.textContent, '镜头数据未接入');
  assert.equal(elements.mainMonitorImage.getAttribute('data-gw-degradation'), 'not_integrated');
});

test('QIN-180 真实镜头元数据绑定（C7-AC3 镜头调参推子台与视窗联动）', async () => {
  const { mockWindow, elements, focalButtons } = createMockEnvironment({
    urlSearch: '?project_id=proj_1234567890ab',
    fetchHandler: async () => ({
      ok: true,
      status: 200,
      json: async () => ({
        project: {
          project_id: 'proj_1234567890ab',
          name: '霓虹深空',
          project_type: 'film',
          stage: 'production',
          progress: 20.0,
          version: 1
        }
      })
    })
  });

  const context = vm.createContext(mockWindow);
  vm.runInContext(controllerSource, context);
  const production = mockWindow.WorkbenchProduction;

  await production.initRouting();

  const realShotMetadata = {
    id: 'shot_001',
    name: '镜头 01 · 霓虹追逐',
    imageUrl: '/api/assets/ast_unified_media_20261017_01/content',
    focal: 50,
    prompt: 'masterpiece, cinematic lighting, neon alley',
    asset_id: 'ast_unified_media_20261017_01'
  };

  // 触发真实镜头绑定
  production.bindCurrentShot(realShotMetadata);

  assert.equal(elements.currentShotTitle.textContent, '镜头 01 · 霓虹追逐');
  assert.equal(elements.mainMonitorImage.src, '/api/assets/ast_unified_media_20261017_01/content');
  assert.equal(elements.mainMonitorImage.style.display, 'block');
  assert.equal(elements.mainMonitorImage.getAttribute('data-gw-degradation'), null);
  assert.equal(elements.mainMonitorImage.getAttribute('data-asset-id'), 'ast_unified_media_20261017_01');
  assert.equal(elements.shotPromptInput.value, 'masterpiece, cinematic lighting, neon alley');
  assert.equal(focalButtons[1].getAttribute('aria-pressed'), 'true');
  assert.equal(focalButtons[0].getAttribute('aria-pressed'), 'false');
});
