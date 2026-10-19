import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {runInNewContext} from 'node:vm';
import test from 'node:test';

const source = readFileSync(new URL('../../web/js/modules/shortcuts.js', import.meta.url), 'utf8');
const shell = readFileSync(new URL('../../web/js/core/workbench-shell.js', import.meta.url), 'utf8');

class Node {
  constructor({tag = 'div', attrs = {}, parent = null} = {}) {
    this.tagName = tag.toUpperCase();
    this.parentElement = parent;
    this.children = [];
    this.dataset = {};
    this.attributes = {...attrs};
    this.classList = new Set();
    this.hidden = false;
    this.disabled = false;
    this.tabIndex = 0;
    this.isContentEditable = false;
    this.focused = 0;
    this.clicked = 0;
    this.listeners = new Map();
    Object.entries(attrs).forEach(([key, value]) => {
      if (key.startsWith('data-')) this.dataset[key.slice(5).replace(/-([a-z])/g, (_, c) => c.toUpperCase())] = String(value);
      if (key === 'class') String(value).split(/\s+/).filter(Boolean).forEach(item => this.classList.add(item));
    });
  }
  append(...nodes) { nodes.forEach(node => { node.parentElement = this; this.children.push(node); }); return this; }
  contains(node) { return node === this || this.children.some(child => child.contains(node)); }
  closest(selector) {
    let current = this;
    while (current) { if (current.matches(selector)) return current; current = current.parentElement; }
    return null;
  }
  matches(selector) {
    return selector.split(',').some(part => {
      const item = part.trim();
      if (item === '[data-gw-shortcut-item]') return this.dataset.gwShortcutItem !== undefined;
      if (item === '[data-gw-focus-scope]') return this.dataset.gwFocusScope !== undefined;
      if (item === '[data-gw-shortcut-surface]') return this.dataset.gwShortcutSurface !== undefined;
      if (item === '[data-gw-shortcut-preview]') return this.dataset.gwShortcutPreview !== undefined;
      if (item === '[data-gw-shortcut-close]') return this.dataset.gwShortcutClose !== undefined;
      if (item === '[data-floating-surface]') return this.dataset.floatingSurface !== undefined;
      if (item === '[role="dialog"]') return this.attributes.role === 'dialog';
      if (item === '[tabindex]:not([tabindex="-1"])') return this.tabIndex >= 0;
      if (item === 'button:not([disabled])') return this.tagName === 'BUTTON' && !this.disabled;
      if (item === 'a[href]') return this.tagName === 'A' && Boolean(this.attributes.href);
      if (item === 'input:not([disabled])') return this.tagName === 'INPUT' && !this.disabled;
      if (item === 'textarea:not([disabled])') return this.tagName === 'TEXTAREA' && !this.disabled;
      if (item === 'select:not([disabled])') return this.tagName === 'SELECT' && !this.disabled;
      if (item === '[contenteditable="true"]') return this.isContentEditable;
      return item.startsWith('.') ? this.classList.has(item.slice(1)) : item.toUpperCase() === this.tagName;
    });
  }
  querySelectorAll(selector) {
    const found = [];
    const visit = node => { node.children.forEach(child => { if (child.matches(selector)) found.push(child); visit(child); }); };
    visit(this);
    return found;
  }
  querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
  focus() { this.focused += 1; this.ownerDocument.activeElement = this; }
  click() { this.clicked += 1; this.listeners.get('click')?.({target: this}); }
  addEventListener(type, handler) { this.listeners.set(type, handler); }
  getAttribute(name) { return this.attributes[name] ?? null; }
  setAttribute(name, value) { this.attributes[name] = String(value); }
  removeAttribute(name) { delete this.attributes[name]; }
}

function harness() {
  const documentListeners = new Map();
  const document = {
    readyState: 'complete',
    activeElement: null,
    body: null,
    addEventListener(type, handler) { documentListeners.set(type, handler); },
    removeEventListener(type) { documentListeners.delete(type); },
    querySelectorAll(selector) { return this.body?.querySelectorAll(selector) || []; },
    querySelector(selector) { return this.body?.querySelector(selector) || null; },
  };
  const body = new Node({tag: 'body'});
  body.ownerDocument = document;
  document.body = body;
  const window = {
    document,
    addEventListener(type, handler) { documentListeners.set(`window:${type}`, handler); },
    removeEventListener(type) { documentListeners.delete(`window:${type}`); },
    CustomEvent: class CustomEvent { constructor(type, init = {}) { this.type = type; this.detail = init.detail; } },
    requestAnimationFrame(callback) { callback(); },
  };
  runInNewContext(source, {window, document, Element: Node, CustomEvent: window.CustomEvent});
  return {document, body, window, keydown(event) {
    const current = {...event, target: event.target || document.activeElement || body, defaultPrevented: false,
      preventDefault() { this.defaultPrevented = true; }, stopPropagation() { this.stopped = true; },
      stopImmediatePropagation() { this.immediateStopped = true; this.stopped = true; }};
    documentListeners.get('keydown')?.(current);
    return current;
  }};
}

function item(parent, id) {
  const node = new Node({attrs: {'data-gw-shortcut-item': id}});
  node.ownerDocument = parent.ownerDocument;
  parent.append(node);
  return node;
}

test('Space 触发当前项预览并阻止页面滚动穿透', () => {
  const h = harness();
  const list = new Node({attrs: {'data-gw-shortcut-scope': 'assets'}});
  list.ownerDocument = h.document;
  h.body.append(list);
  const card = item(list, 'asset-1');
  const preview = new Node({tag: 'button', attrs: {'data-gw-shortcut-preview': 'asset-1'}});
  preview.ownerDocument = h.document;
  card.append(preview);
  card.focus();
  const event = h.keydown({key: ' ', code: 'Space', target: card});
  assert.equal(preview.clicked, 1);
  assert.equal(event.defaultPrevented, true);
  assert.equal(event.immediateStopped, true);
});

test('J/K 与方向键在当前快捷键列表内循环聚焦', () => {
  const h = harness();
  const list = new Node({attrs: {'data-gw-shortcut-scope': 'assets'}});
  list.ownerDocument = h.document;
  h.body.append(list);
  const first = item(list, 'first');
  const second = item(list, 'second');
  const third = item(list, 'third');
  second.focus();
  let event = h.keydown({key: 'j', target: second});
  assert.equal(third.focused, 1);
  assert.equal(event.defaultPrevented, true);
  event = h.keydown({key: 'k', target: third});
  assert.equal(second.focused, 2);
  event = h.keydown({key: 'ArrowUp', target: second});
  assert.equal(first.focused, 1);
  event = h.keydown({key: 'ArrowDown', target: first});
  assert.equal(second.focused, 3);
});

test('Escape 只关闭顶层快捷键浮层并把焦点返还触发控件', () => {
  const h = harness();
  const trigger = new Node({tag: 'button'});
  trigger.ownerDocument = h.document;
  const surface = new Node({attrs: {'data-gw-shortcut-surface': 'preview', 'data-open': 'true'}});
  surface.ownerDocument = h.document;
  const close = new Node({tag: 'button', attrs: {'data-gw-shortcut-close': 'preview'}});
  close.ownerDocument = h.document;
  close.addEventListener('click', () => { surface.dataset.open = 'false'; surface.hidden = true; trigger.focus(); });
  surface.append(close);
  h.body.append(trigger, surface);
  trigger.focus();
  h.document.activeElement = close;
  const event = h.keydown({key: 'Escape', target: close});
  assert.equal(surface.hidden, true);
  assert.equal(trigger.focused, 2);
  assert.equal(event.defaultPrevented, true);
});

test('Tab 在声明的焦点范围内首尾闭环', () => {
  const h = harness();
  const scope = new Node({attrs: {'data-gw-focus-scope': 'dialog'}});
  scope.ownerDocument = h.document;
  const first = new Node({tag: 'button'});
  const last = new Node({tag: 'button'});
  first.ownerDocument = h.document; last.ownerDocument = h.document;
  scope.append(first, last); h.body.append(scope);
  last.focus();
  let event = h.keydown({key: 'Tab', target: last});
  assert.equal(first.focused, 1);
  assert.equal(event.defaultPrevented, true);
  first.focus();
  event = h.keydown({key: 'Tab', shiftKey: true, target: first});
  assert.equal(last.focused, 2);
  assert.equal(event.defaultPrevented, true);
});

test('壳层加载快捷键模块并在路由变更后触发重绑', () => {
  assert.match(shell, /modules\/shortcuts\.js/);
  assert.match(shell, /gw:route-loaded/);
  assert.match(shell, /WorkbenchShortcuts/);
});
