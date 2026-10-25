import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {runInNewContext} from 'node:vm';
import test from 'node:test';

const i18nSource = readFileSync(new URL('../../web/js/core/i18n-core.js', import.meta.url), 'utf8');
const dateRangeSource = readFileSync(new URL('../../web/js/modules/project-date-range.js', import.meta.url), 'utf8');

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
    (this.listeners.get(event.type) || []).forEach(listener => listener(event));
    return true;
  }
}

// === i18n Tests ===

function setupI18n() {
  const store = new Map();
  const localStorage = {
    getItem: (k) => store.get(k) || null,
    setItem: (k, v) => store.set(k, v)
  };
  const window = new FakeEventTarget();
  window.CustomEvent = class {
    constructor(t, d) { this.type = t; this.detail = d?.detail; }
  };
  window.localStorage = localStorage;

  const document = new FakeEventTarget();
  document.documentElement = {
    attrs: {},
    setAttribute(k, v) { this.attrs[k] = v; }
  };


  document.readyState = 'loading';
  document.querySelectorAll = (sel) => root.queries[sel] || [];

  document.querySelectorAll = (sel) => root.queries[sel] || [];


  const root = {
    queries: {
      '[data-i18n]': [],
      '[data-i18n-placeholder]': [],
      '[data-i18n-title]': [],
      '[data-i18n-aria-label]': []
    },
    querySelectorAll(selector) {
      return this.queries[selector] || [];
    },
    documentElement: document.documentElement
  };

  runInNewContext(i18nSource, { window, document, localStorage, JSON, CustomEvent: window.CustomEvent });
  return { window, document, localStorage, root };
}

test('i18n: default language is zh and can register/lookup dictionaries with fallback', () => {
  const { window } = setupI18n();
  const i18n = window.StudioI18n;

  assert.equal(i18n.lang(), 'zh');

  i18n.register({
    'hello': { zh: '你好', en: 'Hello' },
    'only_zh': { zh: '仅中文' },
    'only_en': { en: 'Only EN' }
  });

  // Default is zh
  assert.equal(i18n.t('hello'), '你好');
  assert.equal(i18n.t('only_zh'), '仅中文');
  assert.equal(i18n.t('only_en'), 'Only EN'); // fallback to en if zh not present
  assert.equal(i18n.t('missing'), 'missing'); // fallback to key
});

test('i18n: language switch affects resolution and normalizes entry fallbacks', () => {
  const { window, localStorage, root } = setupI18n();
  const i18n = window.StudioI18n;

  i18n.register({
    'hello': { zh: '你好', en: 'Hello' },
    'only_zh': { zh: '仅中文' },
    'only_en': { en: 'Only EN' }
  });

  let events = [];
  window.addEventListener('studio-lang-change', (e) => events.push(e.detail.lang));

  i18n.set('en');
  assert.equal(i18n.lang(), 'en');

  assert.equal(events[0], 'en');

  // EN resolution
  assert.equal(i18n.t('hello'), 'Hello');
  assert.equal(i18n.t('only_en'), 'Only EN');
  assert.equal(i18n.t('only_zh'), '仅中文'); // fallback to zh or key, code says entry.zh || entry.en || key, so returns '仅中文'
  assert.equal(i18n.t('missing'), 'missing');

  i18n.toggle();
  assert.equal(i18n.lang(), 'zh');
  assert.equal(events[1], 'zh');

  i18n.toggle();
  assert.equal(i18n.lang(), 'en');
});

test('i18n: apply updates DOM elements correctly', () => {
  const { window, root } = setupI18n();
  const i18n = window.StudioI18n;

  i18n.register({ 'btn.save': { zh: '保存', en: 'Save' } });

  const el = { dataset: { i18n: 'btn.save' }, textContent: '' };
  root.queries['[data-i18n]'] = [el];

  i18n.apply(root);
  assert.equal(el.textContent, '保存');
  assert.equal(root.documentElement.attrs['lang'], 'zh-CN');

  i18n.set('en');
  i18n.apply(root);
  assert.equal(el.textContent, 'Save');
  assert.equal(root.documentElement.attrs['lang'], 'en');
});


// === Date Range Tests ===

function setupDateRange() {
  const window = {
    Intl: globalThis.Intl,
    FloatingDismissal: {
      register() {},
      closeSurface() { return false; }
    }
  };
  const document = {
    readyState: 'complete',
    activeElement: null,
    addEventListener() {},
    querySelectorAll() { return []; }
  };

  runInNewContext(dateRangeSource, { window, document, console, Date, String, Number, Boolean });
  return { window, document };
}

class MockElement {
  constructor(tag = 'div') {
    this.tag = tag;
    this.attrs = new Map();
    this.dataset = {};
    this.textContent = '';
    this.innerHTML = '';
    this.value = '';
    this.hidden = false;
    this.tabIndex = -1;
    this.listeners = new Map();
  }
  setAttribute(k, v) { this.attrs.set(k, String(v)); }
  getAttribute(k) { return this.attrs.has(k) ? this.attrs.get(k) : null; }
  hasAttribute(k) { return this.attrs.has(k); }
  addEventListener(type, listener) {
    if (!this.listeners.has(type)) this.listeners.set(type, []);
    this.listeners.get(type).push(listener);
  }
  dispatchEvent(event) {
    (this.listeners.get(event.type) || []).forEach(l => l(event));
  }
  contains() { return false; }
  querySelector() { return null; }
}

test('date range: parses/formats start and end dates', () => {
  const { window } = setupDateRange();

  const container = new MockElement();
  const trigger = new MockElement();
  const label = new MockElement();
  const start = new MockElement('input');
  const end = new MockElement('input');
  const popover = new MockElement();

  container.querySelector = (sel) => {
    if (sel === '[data-date-range-trigger]') return trigger;
    if (sel === '[data-date-range-label]') return label;
    if (sel === '[data-date-range-start]') return start;
    if (sel === '[data-date-range-end]') return end;
    if (sel === '[data-date-range-popover]') return popover;
    return null;
  };

  const root = {
    querySelectorAll(sel) {
      if (sel === '[data-project-date-range]') return [container];
      return [];
    }
  };

  window.GWProjectDateRange.bind(root);

  // Set empty state
  window.GWProjectDateRange.setRange(container, '', '');
  assert.equal(label.textContent, '选择项目排期');

  // Set partial state
  window.GWProjectDateRange.setRange(container, 1704067200000, ''); // 2024-01-01
  // Timestamp might be interpreted in local timezone by new Date(timestamp) inside the script.
  // We use dates to test dynamically to avoid timezone issues.
  const d1 = new Date(2024, 0, 1).getTime();
  const d2 = new Date(2024, 0, 5).getTime();

  window.GWProjectDateRange.setRange(container, d1, '');
  assert.match(label.textContent, /开始：2024-01-01 · 请选择结束日期/);

  // Set full range
  window.GWProjectDateRange.setRange(container, d1, d2);
  assert.match(label.textContent, /2024-01-01 至 2024-01-05/);

  // Test parsing invalid dates
  window.GWProjectDateRange.setRange(container, 'invalid', 'NaN');
  assert.equal(start.value, '');
  assert.equal(end.value, '');
  assert.equal(label.textContent, '选择项目排期');
});

test('date range: click events pick dates correctly (edge cases)', () => {
  const { window } = setupDateRange();

  const container = new MockElement();
  const trigger = new MockElement();
  const label = new MockElement();
  const start = new MockElement('input');
  const end = new MockElement('input');
  const popover = new MockElement();

  container.querySelector = (sel) => {
    if (sel === '[data-date-range-trigger]') return trigger;
    if (sel === '[data-date-range-label]') return label;
    if (sel === '[data-date-range-start]') return start;
    if (sel === '[data-date-range-end]') return end;
    if (sel === '[data-date-range-popover]') return popover;
    return null;
  };

  window.GWProjectDateRange.bind({ querySelectorAll: () => [container] });

  // Simulate popover click on a day button
  // Pick first date
  let eventTarget = {
    dataset: { dateRangeDay: '2024-01-10' },
    closest: (sel) => (sel === '[data-date-range-day]' ? eventTarget : null)
  };
  popover.dispatchEvent({ type: 'click', target: eventTarget });

  let dStart = new Date(2024, 0, 10).getTime();
  assert.equal(start.value, String(dStart));
  assert.equal(end.value, '');

  // Pick second date earlier than first date
  eventTarget = {
    dataset: { dateRangeDay: '2024-01-05' },
    closest: (sel) => (sel === '[data-date-range-day]' ? eventTarget : null)
  };
  popover.dispatchEvent({ type: 'click', target: eventTarget });

  let dStartNew = new Date(2024, 0, 5).getTime();
  assert.equal(start.value, String(dStartNew));
  assert.equal(end.value, String(dStart)); // First date became end date

  // Pick third date
  eventTarget = {
    dataset: { dateRangeDay: '2024-01-20' },
    closest: (sel) => (sel === '[data-date-range-day]' ? eventTarget : null)
  };
  popover.dispatchEvent({ type: 'click', target: eventTarget });

  let dStartNext = new Date(2024, 0, 20).getTime();
  assert.equal(start.value, String(dStartNext));
  assert.equal(end.value, ''); // Resets end date because we had a full range and clicked again
});
