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

/**
 * QIN-493：顶栏导航项目上下文参数治理守卫。
 *
 * 覆盖三层契约：
 * 1. 白名单：只有消费项目上下文的页面携带 project_id（控制台/项目中心/设置不带）；
 * 2. 同步：页面内切换项目时地址栏以 replaceState 跟上，不新增历史记录；
 * 3. 校正：已有参数与实际项目不一致时静默改写，无参数时不主动注入。
 */

import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';

const ORIGIN = 'http://127.0.0.1:2077';

const homeSource = readFileSync('web/js/controllers/home-controller.js', 'utf8');
const projectsSource = readFileSync('web/js/controllers/projects-controller.js', 'utf8');
const productionSource = readFileSync('web/js/controllers/production-controller.js', 'utf8');
const agentsSource = readFileSync('web/js/controllers/agents-controller.js', 'utf8');
const workshopHtml = readFileSync('web/pages/workshop.html', 'utf8');

const CONTEXT_PAGES = ['workshop.html', 'production.html', 'storyboard.html', 'canvas.html', 'assets.html', 'agents.html', 'workflow.html', 'collab.html'];
const NON_CONTEXT_PAGES = ['index.html', 'projects.html', 'settings.html'];

function makeElement(id = '') {
  return {
    id,
    innerHTML: '',
    textContent: '',
    value: '',
    src: '',
    alt: '',
    style: {},
    dataset: {},
    children: [],
    attributes: {},
    disabled: false,
    options: [],
    selectedIndex: 0,
    classList: { add() {}, remove() {}, toggle() {}, contains() { return false; } },
    setAttribute(k, v) { this.attributes[k] = String(v); },
    getAttribute(k) { return this.attributes[k] ?? null; },
    removeAttribute(k) { delete this.attributes[k]; },
    addEventListener() {},
    removeEventListener() {},
    appendChild(child) { this.children.push(child); return child; },
    insertBefore(child) { this.children.push(child); return child; },
    prepend(child) { this.children.push(child); return child; },
    remove() {},
    focus() {},
    click() {},
    closest() { return null; },
    contains() { return false; },
    insertAdjacentHTML() {},
    replaceChildren() {},
    querySelector() { return null; },
    querySelectorAll() { return []; }
  };
}

function createSandbox(options = {}) {
  const { urlSearch = '', pathname = '/static/pages/index.html', storedProjectId = null, navHrefs = [], fetchHandler = null } = options;

  const elements = new Map();
  const getElement = id => {
    if (!elements.has(id)) elements.set(id, makeElement(id));
    return elements.get(id);
  };

  const navLinks = navHrefs.map(href => {
    const el = makeElement('');
    el.setAttribute('href', href);
    return el;
  });

  const storage = new Map();
  if (storedProjectId) storage.set('workspace_project_id', storedProjectId);

  const replaced = [];
  const assigned = [];

  const documentMock = {
    readyState: 'complete',
    body: makeElement('body'),
    head: makeElement('head'),
    documentElement: makeElement('html'),
    activeElement: null,
    getElementById: getElement,
    createElement: () => makeElement(),
    querySelector: () => null,
    querySelectorAll(sel) {
      if (sel === '#navPillsGroup a') return navLinks;
      return [];
    },
    addEventListener() {},
    removeEventListener() {},
    dispatchEvent() { return true; }
  };

  const locationMock = {
    search: urlSearch,
    pathname,
    origin: ORIGIN,
    href: `${ORIGIN}${pathname}${urlSearch}`,
    assign(url) { assigned.push(url); },
    replace() {}
  };

  const defaultFetch = async url => {
    if (String(url).includes('/api/asset-registry/projects')) {
      return { ok: true, status: 200, json: async () => ({ projects: [{ project_id: 'projA', name: '工程 A' }, { project_id: 'projB', name: '工程 B' }] }) };
    }
    return { ok: true, status: 200, json: async () => ({}) };
  };

  const sandbox = {
    console,
    setTimeout,
    clearTimeout,
    setInterval: () => 0,
    clearInterval() {},
    Promise,
    URL,
    URLSearchParams,
    AbortController,
    document: documentMock,
    location: locationMock,
    localStorage: {
      getItem: k => storage.get(k) ?? null,
      setItem: (k, v) => storage.set(k, String(v)),
      removeItem: k => storage.delete(k)
    },
    history: {
      replaceState(_state, _title, url) { replaced.push(String(url)); },
      pushState(_state, _title, url) { replaced.push(String(url)); }
    },
    fetch: fetchHandler || defaultFetch,
    addEventListener() {},
    removeEventListener() {},
    lucide: { createIcons() {} },
    matchMedia: () => ({ matches: false, addEventListener() {}, removeEventListener() {} }),
    getComputedStyle: () => ({ getPropertyValue: () => '' }),
    requestAnimationFrame: cb => { cb(0); return 0; },
    cancelAnimationFrame() {}
  };
  sandbox.window = sandbox;
  sandbox.self = sandbox;
  sandbox.top = sandbox;
  sandbox.parent = sandbox;
  sandbox.globalThis = sandbox;

  return { sandbox, navLinks, replaced, assigned, storage, elements };
}

const tick = (ms = 30) => new Promise(resolve => setTimeout(resolve, ms));

function hrefOf(link) {
  return link.getAttribute('href') || '';
}

function hasParam(href, name, value) {
  const url = new URL(href, ORIGIN);
  return url.searchParams.get(name) === value;
}

test('QIN-493 AC1：控制台顶栏不得携带 project_id，并清除自身地址栏残留参数', async () => {
  const { sandbox, navLinks, replaced } = createSandbox({
    urlSearch: '?project_id=projA',
    pathname: '/static/pages/index.html',
    storedProjectId: 'projA',
    navHrefs: [...CONTEXT_PAGES.map(p => `/${p}`), ...NON_CONTEXT_PAGES.map(p => `/${p}`)]
  });

  vm.runInContext(homeSource, vm.createContext(sandbox));
  await tick();

  // 控制台自身不持有上下文：残留参数必须被移除。
  assert.ok(replaced.length > 0, '控制台应调用 replaceState 清除残留 project_id');
  const cleaned = new URL(replaced[replaced.length - 1], ORIGIN);
  assert.equal(cleaned.searchParams.has('project_id'), false);

  for (const page of CONTEXT_PAGES) {
    const link = navLinks.find(l => hrefOf(l).includes(page));
    assert.ok(link, `缺少导航链接 ${page}`);
    assert.equal(hasParam(hrefOf(link), 'project_id', 'projA'), true, `${page} 应携带 project_id`);
  }
  for (const page of NON_CONTEXT_PAGES) {
    const link = navLinks.find(l => hrefOf(l).includes(page));
    if (!link) continue;
    assert.equal(new URL(hrefOf(link), ORIGIN).searchParams.has('project_id'), false, `${page} 不得携带 project_id`);
  }
});

test('QIN-493 AC1：项目中心顶栏不携带 project_id，切换选中项目只改链接不写地址栏', async () => {
  const { sandbox, navLinks, replaced } = createSandbox({
    urlSearch: '?project_id=projA',
    pathname: '/static/pages/projects.html',
    storedProjectId: 'projA',
    navHrefs: [...CONTEXT_PAGES.map(p => `/${p}`), ...NON_CONTEXT_PAGES.map(p => `/${p}`)],
    fetchHandler: async url => {
      if (String(url).includes('/api/asset-registry/projects')) {
        return { ok: true, status: 200, json: async () => ({ projects: [{ project_id: 'projA', name: '工程 A' }, { project_id: 'projB', name: '工程 B' }] }) };
      }
      return { ok: true, status: 200, json: async () => ({}) };
    }
  });

  vm.runInContext(projectsSource, vm.createContext(sandbox));
  await tick();

  const controller = sandbox.WorkbenchProjects;
  assert.ok(controller, 'WorkbenchProjects 必须挂载至 window');
  controller.selectProject('projB');
  await tick();

  for (const page of CONTEXT_PAGES) {
    const link = navLinks.find(l => hrefOf(l).includes(page));
    assert.ok(link, `缺少导航链接 ${page}`);
    assert.equal(hasParam(hrefOf(link), 'project_id', 'projB'), true, `${page} 应跟随选中项目`);
  }
  for (const page of NON_CONTEXT_PAGES) {
    const link = navLinks.find(l => hrefOf(l).startsWith(`/${page}`) || hrefOf(l).startsWith(`${page}`));
    if (!link) continue;
    assert.equal(new URL(hrefOf(link), ORIGIN).searchParams.has('project_id'), false, `${page} 不得携带 project_id`);
  }

  // 项目中心是选择器：选中动作不得把 project_id 写回地址栏。
  const writtenProject = replaced.map(u => new URL(u, ORIGIN).searchParams.get('project_id')).filter(Boolean);
  assert.deepEqual(writtenProject, [], '项目中心不得向地址栏写入 project_id');
});

test('QIN-493 AC2/AC3：剧集制片顶栏按白名单携带参数，地址栏与实际项目保持一致', async () => {
  const { sandbox, navLinks, replaced } = createSandbox({
    urlSearch: '?project_id=projA',
    pathname: '/static/pages/production.html',
    navHrefs: [`/projects.html`, `/workshop.html`, `/production.html`, `/index.html`],
    fetchHandler: async url => {
      const target = String(url);
      if (target.includes('/api/projects/')) {
        return { ok: true, status: 200, json: async () => ({ project: { project_id: 'projA', name: '工程 A' } }) };
      }
      if (target.includes('/api/episode-pipelines')) {
        return { ok: true, status: 200, json: async () => ({ pipelines: [] }) };
      }
      return { ok: true, status: 200, json: async () => ({}) };
    }
  });

  vm.runInContext(productionSource, vm.createContext(sandbox));
  const controller = sandbox.WorkbenchProduction;
  assert.ok(controller, 'WorkbenchProduction 必须挂载至 window');
  await controller.initRouting();
  await tick();

  const projectsLink = navLinks.find(l => hrefOf(l).includes('projects.html'));
  assert.equal(new URL(hrefOf(projectsLink), ORIGIN).searchParams.has('project_id'), false, '项目中心链接不得携带 project_id');

  const workshopLink = navLinks.find(l => hrefOf(l).includes('workshop.html'));
  assert.equal(hasParam(hrefOf(workshopLink), 'project_id', 'projA'), true, '工坊链接必须携带当前项目');

  const indexLink = navLinks.find(l => hrefOf(l).includes('index.html'));
  assert.equal(new URL(hrefOf(indexLink), ORIGIN).searchParams.has('project_id'), false, '控制台链接不得携带 project_id');

  // 地址栏已是 projA，与实读一致，不应产生无意义改写。
  assert.deepEqual(replaced, [], '参数与实读一致时不得重复改写地址栏');
});

test('QIN-493 AC4：地址栏参数过期时校正为实际项目，无参数时不注入', async () => {
  const stale = createSandbox({
    urlSearch: '?project_id=projSTALE',
    pathname: '/static/pages/production.html',
    navHrefs: [`/workshop.html`],
    fetchHandler: async url => {
      const target = String(url);
      if (target.includes('/api/projects/')) {
        return { ok: true, status: 200, json: async () => ({ project: { project_id: 'projA', name: '工程 A' } }) };
      }
      if (target.includes('/api/episode-pipelines')) {
        return { ok: true, status: 200, json: async () => ({ pipelines: [] }) };
      }
      return { ok: true, status: 200, json: async () => ({}) };
    }
  });

  vm.runInContext(productionSource, vm.createContext(stale.sandbox));
  await stale.sandbox.WorkbenchProduction.initRouting();
  await tick();

  const corrected = stale.replaced.map(u => new URL(u, ORIGIN)).filter(u => u.searchParams.has('project_id'));
  assert.ok(corrected.length > 0, '过期参数必须被校正');
  assert.equal(corrected[corrected.length - 1].searchParams.get('project_id'), 'projA');

  // 无参数进入：只读取 localStorage，不向地址栏注入参数。
  const clean = createSandbox({
    urlSearch: '',
    pathname: '/static/pages/production.html',
    storedProjectId: 'projA',
    navHrefs: [`/workshop.html`],
    fetchHandler: async url => {
      const target = String(url);
      if (target.includes('/api/projects/')) {
        return { ok: true, status: 200, json: async () => ({ project: { project_id: 'projA', name: '工程 A' } }) };
      }
      if (target.includes('/api/episode-pipelines')) {
        return { ok: true, status: 200, json: async () => ({ pipelines: [] }) };
      }
      return { ok: true, status: 200, json: async () => ({}) };
    }
  });

  vm.runInContext(productionSource, vm.createContext(clean.sandbox));
  await clean.sandbox.WorkbenchProduction.initRouting();
  await tick();

  assert.deepEqual(clean.replaced, [], '地址栏本无参数时不得主动注入');
});

test('QIN-493 AC3：智能体页切换项目后地址栏同步，且不新增历史记录', async () => {
  const { sandbox, replaced } = createSandbox({
    urlSearch: '?project_id=projA',
    pathname: '/static/pages/agents.html',
    storedProjectId: 'projA'
  });

  vm.runInContext(agentsSource, vm.createContext(sandbox));
  const controller = sandbox.WorkbenchAgents;
  assert.ok(controller, 'WorkbenchAgents 必须挂载至 window');

  controller.setProjectId('projB', '工程 B');
  const synced = replaced.map(u => new URL(u, ORIGIN)).filter(u => u.searchParams.has('project_id'));
  assert.ok(synced.length > 0, '切换项目后必须同步地址栏');
  assert.equal(synced[synced.length - 1].searchParams.get('project_id'), 'projB');
  assert.equal(synced[synced.length - 1].hash, '', '同步地址栏不得携带多余片段');
});

test('QIN-493 AC1/AC2：工坊内联导航按白名单改写，不再无条件追加参数', () => {
  assert.match(workshopHtml, /const PROJECT_CONTEXT_PAGES = new Set\(/, '工坊需声明上下文页面白名单');
  assert.match(workshopHtml, /url\.searchParams\.delete\('project_id'\)/, '非上下文目标必须移除 project_id');
  assert.match(workshopHtml, /function syncAddressBarProject\(/, '工坊需具备地址栏校正');
  assert.doesNotMatch(
    workshopHtml,
    /document\.querySelectorAll\('#navPillsGroup a, #gotoProductionLink'\)\.forEach/,
    '旧的无条件追加实现必须移除'
  );
});
