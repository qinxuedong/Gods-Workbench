// Copyright 2026 qinxuedong (Gods-Workbench Authors)
// SPDX-License-Identifier: FSL-1.1-Apache-2.0
//
// Licensed under the Functional Source License, Version 1.1, Apache 2.0
// Future License (the "License"); you may not use this file except in
// compliance with the License. You may obtain a copy of the License at
//     https://fsl.software/
//
// Unless required by applicable law or agreed to in writing, software
// distributed under the License is distributed on an "AS IS" BASIS,
// WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
// See the License for the specific language governing permissions and
// limitations under the License.

import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import path from 'node:path';
import test from 'node:test';
import { fileURLToPath } from 'node:url';
import vm from 'node:vm';

const repoRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const taskCenterCss = readFileSync(path.join(repoRoot, 'web/css/pages/task-center.css'), 'utf8');
const taskCenterJs = readFileSync(path.join(repoRoot, 'web/js/modules/task-center.js'), 'utf8');
const taskCenterI18nJs = readFileSync(path.join(repoRoot, 'web/js/modules/i18n/task-center.js'), 'utf8');

function createElement(tagName = 'div', id = '') {
  let html = '';
  let text = '';
  let innerHtmlWrites = 0;
  const classes = new Set();
  const attrs = new Map();
  const listeners = new Map();
  const el = {
    tagName: tagName.toUpperCase(),
    id,
    dataset: {},
    style: {},
    children: [],
    parentNode: null,
    hidden: false,
    disabled: false,
    open: false,
    value: '',
    scrollHeight: 0,
    scrollTop: 0,
    clientHeight: 0,
    classList: {
      add: (...tokens) => tokens.forEach((t) => classes.add(t)),
      remove: (...tokens) => tokens.forEach((t) => classes.delete(t)),
      toggle: (t, force) => {
        if (force === undefined) {
          if (classes.has(t)) classes.delete(t);
          else classes.add(t);
        } else if (force) classes.add(t);
        else classes.delete(t);
      },
      contains: (t) => classes.has(t),
    },
    set className(val) {
      classes.clear();
      String(val || '').split(/\s+/).filter(Boolean).forEach((t) => classes.add(t));
    },
    get className() {
      return [...classes].join(' ');
    },
    set innerHTML(val) {
      html = String(val ?? '');
      innerHtmlWrites += 1;
    },
    get innerHTML() {
      return html;
    },
    getInnerHtmlWrites() {
      return innerHtmlWrites;
    },
    set textContent(val) {
      text = String(val ?? '');
    },
    get textContent() {
      return text;
    },
    setAttribute(k, v) {
      attrs.set(k, String(v));
      if (k === 'hidden') this.hidden = true;
    },
    getAttribute: (k) => attrs.get(k) ?? null,
    addEventListener(type, fn) {
      if (!listeners.has(type)) listeners.set(type, []);
      listeners.get(type).push(fn);
    },
    insertBefore(newNode, refNode) {
      newNode.parentNode = this;
      const idx = this.children.indexOf(refNode);
      if (idx === -1) this.children.push(newNode);
      else this.children.splice(idx, 0, newNode);
      return newNode;
    },
    appendChild(child) {
      child.parentNode = this;
      this.children.push(child);
      return child;
    },
    remove() {
      if (!this.parentNode) return;
      this.parentNode.children = this.parentNode.children.filter((c) => c !== this);
      this.parentNode = null;
    },
    matches: () => false,
    querySelector: () => null,
    querySelectorAll: () => [],
    showModal() { this.open = true; },
    close() { this.open = false; },
    focus() {},
    getClientRects: () => [{ left: 0, top: 0, width: 100, height: 30 }],
  };
  return el;
}

function createHarness(apiHandler, { embedded = true } = {}) {
  const ids = [
    'observationRange', 'observationSource', 'observationLevel', 'observationStatus',
    'observationStableId', 'observationScope', 'observabilityNotice', 'observationViews',
    'observabilityContent', 'taskRows', 'logRows', 'taskCount', 'logCount', 'refreshState',
    'loadMoreTasks', 'loadMoreEvents', 'loadMoreAssetVolumes', 'taskCursorState', 'cursorState',
    'taskDetail', 'taskDetailSubtitle', 'taskDetailBody', 'taskToast',
    'observation-panel-tasks', 'observation-panel-logs', 'observation-tab-tasks', 'observation-tab-logs',
  ];
  const elements = new Map(ids.map((id) => [id, createElement(id.includes('Detail') ? 'dialog' : 'div', id)]));
  const views = elements.get('observationViews');
  const toolbar = createElement('section', 'toolbar');
  toolbar.className = 'observability-toolbar';
  const main = createElement('main', 'main');
  main.appendChild(views);
  main.appendChild(toolbar);

  const tabTasks = elements.get('observation-tab-tasks');
  tabTasks.dataset.view = 'tasks';
  const tabLogs = elements.get('observation-tab-logs');
  tabLogs.dataset.view = 'logs';
  const badgeTasks = createElement('span');
  badgeTasks.dataset.tabCount = 'tasks';
  const badgeLogs = createElement('span');
  badgeLogs.dataset.tabCount = 'logs';
  const panelTasks = elements.get('observation-panel-tasks');
  panelTasks.dataset.viewPanel = 'tasks';
  const panelLogs = elements.get('observation-panel-logs');
  panelLogs.dataset.viewPanel = 'logs';
  const closeDetailBtn = createElement('button');
  const detailDialog = elements.get('taskDetail');
  detailDialog.querySelector = (sel) => (sel === '[data-close-task-detail]' ? closeDetailBtn : null);

  const selectorMap = new Map([
    ['.observability-toolbar', toolbar],
    ['[data-tab-count="tasks"]', badgeTasks],
    ['[data-tab-count="logs"]', badgeLogs],
  ]);

  const intervals = [];
  const docListeners = new Map();
  const winListeners = new Map();
  const requests = [];

  const translations = new Map();
  const StudioI18n = {
    lang: () => 'zh',
    register(obj) {
      Object.entries(obj).forEach(([k, v]) => translations.set(k, v.zh || v.en || k));
    },
    t: (k) => translations.get(k) || k,
  };

  const documentObj = {
    hidden: false,
    documentElement: createElement('html'),
    querySelector(sel) {
      if (sel.startsWith('#')) return elements.get(sel.slice(1)) || null;
      return selectorMap.get(sel) || null;
    },
    querySelectorAll(sel) {
      if (sel === '[data-view-panel]') return [panelTasks, panelLogs];
      if (sel === '[data-view]') return [tabTasks, tabLogs];
      return [];
    },
    addEventListener(type, fn) {
      if (!docListeners.has(type)) docListeners.set(type, []);
      docListeners.get(type).push(fn);
    },
  };

  const windowObj = {
    self: null,
    top: embedded ? {} : null,
    parent: { postMessage() {} },
    StudioI18n,
    setInterval(fn, ms) {
      intervals.push({ fn, ms });
      return intervals.length;
    },
    clearInterval() {},
    setTimeout(fn) {
      return setTimeout(fn, 0);
    },
    clearTimeout() {},
    addEventListener(type, fn) {
      if (!winListeners.has(type)) winListeners.set(type, []);
      winListeners.get(type).push(fn);
    },
  };
  windowObj.self = windowObj;
  if (!embedded) windowObj.top = windowObj;

  const context = vm.createContext({
    console,
    Math,
    Number,
    String,
    Array,
    Object,
    JSON,
    Boolean,
    Set,
    Map,
    Promise,
    URL,
    URLSearchParams,
    Date,
    AbortController,
    HTMLElement: Object,
    CSS: { escape: (s) => String(s) },
    history: { replaceState() {} },
    location: {
      href: `http://127.0.0.1:2077/static/embeds/task-center.html${embedded ? '?embedded=1' : ''}`,
      origin: 'http://127.0.0.1:2077',
      pathname: '/static/embeds/task-center.html',
      search: embedded ? '?embedded=1' : '',
      hash: '',
    },
    localStorage: { getItem: () => null },
    Workspace: {
      api: async (url, opts) => {
        requests.push(url);
        return apiHandler(url, opts);
      },
      escapeHtml: (v) => String(v ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;'),
      dateTime: (v) => (v ? String(v) : '—'),
      timeValue: (v) => (v ? String(v) : '—'),
      navigate() {},
    },
    StudioI18n,
    window: windowObj,
    document: documentObj,
    setTimeout: windowObj.setTimeout,
    clearTimeout: windowObj.clearTimeout,
    setInterval: windowObj.setInterval,
    clearInterval: windowObj.clearInterval,
  });

  vm.runInContext(taskCenterI18nJs, context);
  vm.runInContext(taskCenterJs, context);

  return {
    elements,
    intervals,
    requests,
    docListeners,
    triggerClick(target) {
      const handlers = docListeners.get('click') || [];
      return Promise.all(handlers.map((h) => h({ target })));
    },
  };
}

test('AC1: 6 列表格列宽分配合理，操作列与时间列不截断且对齐黑金样式', () => {
  assert.match(taskCenterCss, /body\.gw-page--task-center\s+\.task-table\s*\{[^}]*min-width:\s*560px;/s);
  assert.match(taskCenterCss, /\.task-table\s+th:nth-child\(5\)\s*\{\s*width:\s*112px;/);
  assert.match(taskCenterCss, /\.task-table\s+th:nth-child\(6\)\s*\{\s*width:\s*96px;/);
  assert.match(taskCenterCss, /\.task-table\s+tbody\s+td:nth-child\(5\)\s*\{[^}]*white-space:\s*nowrap;/s);
  assert.match(taskCenterCss, /\.task-table\s+tbody\s+td:nth-child\(6\)\s*\{[^}]*white-space:\s*nowrap;/s);
  assert.match(taskCenterCss, /\.task-detail-summary-card/);
  assert.match(taskCenterCss, /\.task-detail-tech/);
});

test('AC2: 任务详情置顶中文错误解释与结果摘要，技术细节默认折叠置于次要位置', async () => {
  const harness = createHarness(async (url) => {
    if (url.startsWith('/api/observability/events')) {
      return { data_status: 'ok', events: [], has_more: false };
    }
    if (url.startsWith('/api/observability/tasks')) {
      return {
        data_status: 'ok',
        items: [
          {
            job_id: 'job_idx_fail_001',
            job_type: 'asset.index',
            status: 'failed',
            source: 'asset_registry',
            error_safe: 'INDEX_ROOTS_CHANGED',
            updated_at: '2026-11-23T22:00:00Z',
          },
          {
            job_id: 'job_idx_ok_002',
            job_type: 'asset.index',
            status: 'succeeded',
            source: 'asset_registry',
            updated_at: '2026-11-23T22:01:00Z',
          },
        ],
        has_more: false,
      };
    }
    if (url.includes('job_idx_fail_001')) {
      return {
        job: {
          job_id: 'job_idx_fail_001',
          job_type: 'asset.index',
          status: 'failed',
          has_error: true,
          error_code: 'INDEX_ROOTS_CHANGED',
          version: 1,
          events: [],
        },
        actions: {},
      };
    }
    if (url.includes('job_idx_ok_002')) {
      return {
        job: {
          job_id: 'job_idx_ok_002',
          job_type: 'asset.index',
          status: 'succeeded',
          has_error: false,
          result: { scanned: 120, indexed: 15, error_count: 0 },
          version: 1,
          events: [],
        },
        actions: {},
      };
    }
    return { data_status: 'ok' };
  });

  await new Promise((r) => setTimeout(r, 20));

  const openFailBtn = { matches: (sel) => sel === '[data-open-task]', dataset: { openTask: 'job_idx_fail_001' } };
  await harness.triggerClick({
    closest: (sel) => (sel === 'button,[data-nav]' ? openFailBtn : null),
  });
  await new Promise((r) => setTimeout(r, 20));

  const failHtml = harness.elements.get('taskDetailBody').innerHTML;
  assert.match(failHtml, /task-detail-summary-card is-failed/);
  assert.match(failHtml, /资源目录配置已变更/);
  assert.match(failHtml, /下一步建议/);
  assert.match(failHtml, /<details class="task-detail-tech">/);
  assert.ok(failHtml.indexOf('task-detail-summary-card') < failHtml.indexOf('<details class="task-detail-tech">'));

  const openOkBtn = { matches: (sel) => sel === '[data-open-task]', dataset: { openTask: 'job_idx_ok_002' } };
  await harness.triggerClick({
    closest: (sel) => (sel === 'button,[data-nav]' ? openOkBtn : null),
  });
  await new Promise((r) => setTimeout(r, 20));

  const okHtml = harness.elements.get('taskDetailBody').innerHTML;
  assert.match(okHtml, /task-detail-summary-card is-succeeded/);
  assert.match(okHtml, /共扫描 120 项，新登记 15 项/);
  assert.match(okHtml, /<details class="task-detail-tech">/);
});

test('AC3: 5秒静默刷新在快照无实质变化时不重绘列表 DOM，有变化时正常更新', async () => {
  let phase = 1;
  const harness = createHarness(async (url) => {
    if (url.startsWith('/api/observability/events')) {
      return { data_status: 'ok', events: [], has_more: false };
    }
    if (url.startsWith('/api/observability/tasks')) {
      return {
        data_status: 'ok',
        items: [
          {
            job_id: 'job_static_001',
            job_type: 'asset.index',
            status: phase === 1 ? 'running' : 'succeeded',
            source: 'asset_registry',
            updated_at: phase === 1 ? '2026-11-23T22:00:00Z' : '2026-11-23T22:05:00Z',
          },
        ],
        has_more: false,
      };
    }
    return { data_status: 'ok' };
  });

  await new Promise((r) => setTimeout(r, 20));
  const taskRowsEl = harness.elements.get('taskRows');
  const writesAfterInit = taskRowsEl.getInnerHtmlWrites();
  assert.equal(harness.intervals.length, 1);
  assert.equal(harness.intervals[0].ms, 5000);

  // 快照无变化时触发 5s 定时器：不应重绘 taskRows
  harness.intervals[0].fn();
  await new Promise((r) => setTimeout(r, 20));
  assert.equal(taskRowsEl.getInnerHtmlWrites(), writesAfterInit);

  // 数据发生实质变化时触发 5s 定时器：应重绘 taskRows
  phase = 2;
  harness.intervals[0].fn();
  await new Promise((r) => setTimeout(r, 20));
  assert.ok(taskRowsEl.getInnerHtmlWrites() > writesAfterInit);
  assert.match(taskRowsEl.innerHTML, /已完成/);
});

test('AC4: 任务快照仅时间窗说明不触发降级红框误报；真实故障仍告警', async () => {
  let simulateRealFailure = false;
  const harness = createHarness(async (url) => {
    if (url.startsWith('/api/observability/events')) {
      return { data_status: 'ok', events: [], has_more: false };
    }
    if (url.startsWith('/api/observability/tasks')) {
      if (simulateRealFailure) {
        return {
          data_status: 'degraded',
          items: [],
          degraded_sources: ['asset_registry'],
          data_gaps: ['SOURCE_UNAVAILABLE:asset_registry'],
        };
      }
      return {
        data_status: 'degraded',
        items: [{ job_id: 'job_ok_001', job_type: 'asset.index', status: 'succeeded', source: 'asset_registry' }],
        degraded_sources: [],
        truncated_sources: [],
        data_gaps: ['god-canvas 任务不记录时间戳，range/start_ms/end_ms 未生效；本次返回的是当前状态快照（显式披露，非静默忽略时间条件）'],
      };
    }
    return { data_status: 'ok' };
  });

  await new Promise((r) => setTimeout(r, 20));
  const taskReq = harness.requests.find((u) => u.startsWith('/api/observability/tasks'));
  assert.ok(taskReq, '必须发出 /api/observability/tasks 请求');

  const noticeEl = harness.elements.get('observabilityNotice');
  assert.equal(noticeEl.classList.contains('visible'), false);
  assert.doesNotMatch(noticeEl.innerHTML, /任务数据暂不可用/);

  // 真实数据源故障时仍须显示错误横幅
  simulateRealFailure = true;
  harness.intervals[0].fn();
  await new Promise((r) => setTimeout(r, 20));
  assert.equal(noticeEl.classList.contains('visible'), true);
  assert.match(noticeEl.innerHTML, /任务数据暂不可用/);
});
