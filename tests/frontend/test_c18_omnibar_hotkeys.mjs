import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {runInNewContext} from 'node:vm';
import test from 'node:test';

const omnibarSource = readFileSync(new URL('../../web/js/core/omnibar.js', import.meta.url), 'utf8');
const shellSource = readFileSync(new URL('../../web/js/core/workbench-shell.js', import.meta.url), 'utf8');

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
    event.target ??= this;
    event.currentTarget = this;
    (this.listeners.get(event.type) || []).forEach(listener => listener(event));
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
    this.classSet = new Set();
    this.classList = {
      add: name => this.classSet.add(name),
      remove: name => this.classSet.delete(name),
      contains: name => this.classSet.has(name),
      toggle: (name, enabled) => {
        const shouldEnable = enabled === undefined ? !this.classSet.has(name) : enabled;
        if (shouldEnable) this.classSet.add(name); else this.classSet.delete(name);
        return shouldEnable;
      },
    };
    this.hidden = false;
    this.value = '';
    this.textContent = '';
    this.isConnected = false;
    this.focusCount = 0;
  }
  append(...nodes) { nodes.forEach(node => this.appendChild(node)); }
  appendChild(node) {
    node.parentElement = this;
    node.isConnected = this.isConnected || this.tagName === 'BODY';
    this.children.push(node);
    return node;
  }
  replaceChildren(...nodes) {
    this.children = [];
    this.append(...nodes);
  }
  setAttribute(name, value) {
    this.attributes.set(name, String(value));
    if (name === 'hidden') this.hidden = true;
    if (name === 'id') this.id = String(value);
    if (name === 'class') this.className = String(value);
    if (name.startsWith('data-')) {
      const key = name.slice(5).replace(/-([a-z])/g, (_, letter) => letter.toUpperCase());
      this.dataset[key] = String(value);
    }
  }
  getAttribute(name) { return this.attributes.get(name) ?? null; }
  removeAttribute(name) { this.attributes.delete(name); }
  focus() { this.focusCount++; this.ownerDocument.activeElement = this; }
  scrollIntoView() {}
  querySelectorAll(selector) {
    const matches = [];
    const visit = parent => parent.children.forEach(child => {
      if (selector === '[role="option"]' && child.getAttribute('role') === 'option') matches.push(child);
      if (selector.startsWith('#') && child.id === selector.slice(1)) matches.push(child);
      visit(child);
    });
    visit(this);
    return matches;
  }
  querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
}

class FakeDocument extends FakeEventTarget {
  constructor() {
    super();
    this.readyState = 'complete';
    this.body = new FakeElement('body', this);
    this.body.isConnected = true;
    this.head = new FakeElement('head', this);
    this.documentElement = new FakeElement('html', this);
    this.documentElement.isConnected = true;
    this.body.parentElement = this.documentElement;
    this.documentElement.children.push(this.body);
    this.activeElement = this.body;
    this.iframes = [];
    this.defaultView = null;
  }
  createElement(tagName) { return new FakeElement(tagName, this); }
  createTextNode(value) { const node = new FakeElement('#text', this); node.textContent = value; return node; }
  getElementById(id) {
    if (this.body.id === id) return this.body;
    return this.body.querySelectorAll(`#${id}`)[0] || null;
  }
  querySelectorAll(selector) { return selector === 'iframe' ? this.iframes : this.body.querySelectorAll(selector); }
}

function harness({frames = []} = {}) {
  const document = new FakeDocument();
  const window = new FakeEventTarget();
  const rafQueue = [];
  class CustomEvent {
    constructor(type, options = {}) { this.type = type; this.detail = options.detail; }
  }
  class MutationObserver {
    constructor(callback) { this.callback = callback; }
    observe() {}
  }
  window.CustomEvent = CustomEvent;
  window.MutationObserver = MutationObserver;
  window.location = {href: 'http://workbench.local/pages/index.html', origin: 'http://workbench.local', assign() {}};
  window.requestAnimationFrame = callback => { rafQueue.push(callback); };
  window.setTimeout = callback => { rafQueue.push(callback); };
  window.flushFrames = () => { while (rafQueue.length) rafQueue.shift()(); };
  window.dispatchEvent = event => FakeEventTarget.prototype.dispatchEvent.call(window, event);
  document.defaultView = window;
  for (const frame of frames) {
    document.iframes.push(frame);
    document.body.appendChild(frame);
  }
  const context = {window, document, URL, console};
  runInNewContext(omnibarSource, context);
  return {document, window, omnibar: window.GWOmnibar};
}

function keyEvent({key, code, ctrlKey = false, metaKey = false, altKey = false, shiftKey = false}) {
  return {
    type: 'keydown', key, code, ctrlKey, metaKey, altKey, shiftKey, defaultPrevented: false,
    preventDefault() { this.defaultPrevented = true; },
    stopPropagation() { this.propagationStopped = true; },
    stopImmediatePropagation() { this.immediatePropagationStopped = true; },
  };
}

test('Ctrl+Space 主快捷键唤出命令面板并拦截默认动作', () => {
  const {document, omnibar} = harness();
  const event = keyEvent({key: ' ', code: 'Space', ctrlKey: true});
  document.dispatchEvent(event);
  assert.equal(event.defaultPrevented, true);
  assert.equal(omnibar.snapshot().open, true);
  assert.equal(document.getElementById('gwOmnibar').hidden, false);
  assert.equal(document.activeElement.id, 'gwOmnibarInput');
});

test('Ctrl+F 兜底快捷键拦截浏览器查找并由 Escape 关闭及返焦', () => {
  const {document, omnibar, window} = harness();
  const trigger = document.createElement('button');
  trigger.isConnected = true;
  document.body.appendChild(trigger);
  trigger.focus();

  const findEvent = keyEvent({key: 'f', code: 'KeyF', ctrlKey: true});
  document.dispatchEvent(findEvent);
  assert.equal(findEvent.defaultPrevented, true, 'Ctrl+F 的默认浏览器查找动作应被拦截');
  assert.equal(omnibar.snapshot().open, true);

  const escapeEvent = keyEvent({key: 'Escape', code: 'Escape'});
  document.dispatchEvent(escapeEvent);
  window.flushFrames();
  assert.equal(escapeEvent.defaultPrevented, true);
  assert.equal(omnibar.snapshot().open, false);
  assert.equal(document.getElementById('gwOmnibar').hidden, true);
  assert.equal(document.activeElement, trigger);
});

test('Ctrl+Shift+F 不劫持未授权的组合键', () => {
  const {document, omnibar} = harness();
  const event = keyEvent({key: 'F', code: 'KeyF', ctrlKey: true, shiftKey: true});
  document.dispatchEvent(event);
  assert.equal(event.defaultPrevented, false);
  assert.equal(omnibar.snapshot().open, false);
});

test('输入斜杠切换 Agent 态，模式按钮可返回检索态', () => {
  const {document, omnibar} = harness();
  document.dispatchEvent(keyEvent({key: ' ', code: 'Space', ctrlKey: true}));
  const input = document.getElementById('gwOmnibarInput');
  input.value = '/梳理项目进度';
  input.dispatchEvent({type: 'input'});
  assert.equal(omnibar.snapshot().mode, 'agent');
  const searchMode = document.getElementById('gwOmnibarModeSearch');
  searchMode.dispatchEvent({type: 'click'});
  assert.equal(omnibar.snapshot().mode, 'search');
});

test('方向键循环选择结果并更新 listbox aria-selected 状态', () => {
  const {document, omnibar} = harness();
  document.dispatchEvent(keyEvent({key: ' ', code: 'Space', ctrlKey: true}));
  omnibar.setResults([{title: '资产 A'}, {title: '项目 B'}]);

  document.dispatchEvent(keyEvent({key: 'ArrowDown', code: 'ArrowDown'}));
  assert.equal(omnibar.snapshot().selectedIndex, 0);
  assert.equal(document.getElementById('gwOmnibarResults').querySelectorAll('[role="option"]')[0].getAttribute('aria-selected'), 'true');
  document.dispatchEvent(keyEvent({key: 'ArrowDown', code: 'ArrowDown'}));
  assert.equal(omnibar.snapshot().selectedIndex, 1);
  document.dispatchEvent(keyEvent({key: 'ArrowUp', code: 'ArrowUp'}));
  assert.equal(omnibar.snapshot().selectedIndex, 0);
});

test('Tab 与 Shift+Tab 在 Omnibar 控件间循环，不会移出模态面板', () => {
  const {document, window} = harness();
  document.dispatchEvent(keyEvent({key: ' ', code: 'Space', ctrlKey: true}));
  const input = document.getElementById('gwOmnibarInput');
  const panel = document.getElementById('gwOmnibarPanel');
  const closeButton = panel.children[0].children[1];

  const forward = keyEvent({key: 'Tab', code: 'Tab'});
  forward.target = input;
  document.dispatchEvent(forward);
  assert.equal(forward.defaultPrevented, true);
  assert.equal(document.activeElement, closeButton);

  const backward = keyEvent({key: 'Tab', code: 'Tab', shiftKey: true});
  backward.target = closeButton;
  document.dispatchEvent(backward);
  window.flushFrames();
  assert.equal(backward.defaultPrevented, true);
  assert.equal(document.activeElement, input);
});

test('同源嵌入页中的 Ctrl+F 也会打开顶层面板', () => {
  const parentDocument = new FakeDocument();
  const frameDocument = new FakeDocument();
  const frame = parentDocument.createElement('iframe');
  frame.contentDocument = frameDocument;
  frameDocument.defaultView = {frameElement: frame};
  const frameTrigger = frameDocument.createElement('button');
  frameTrigger.isConnected = true;
  frameDocument.body.appendChild(frameTrigger);
  frameTrigger.focus();
  parentDocument.iframes.push(frame);
  parentDocument.body.appendChild(frame);

  const window = new FakeEventTarget();
  window.CustomEvent = class { constructor(type, options = {}) { this.type = type; this.detail = options.detail; } };
  window.MutationObserver = class { observe() {} };
  window.location = {href: 'http://workbench.local/', origin: 'http://workbench.local', assign() {}};
  const rafQueue = [];
  window.requestAnimationFrame = callback => rafQueue.push(callback);
  window.setTimeout = callback => rafQueue.push(callback);
  window.dispatchEvent = event => FakeEventTarget.prototype.dispatchEvent.call(window, event);
  parentDocument.defaultView = window;
  runInNewContext(omnibarSource, {window, document: parentDocument, URL, console});

  const event = keyEvent({key: 'f', code: 'KeyF', ctrlKey: true});
  frameDocument.dispatchEvent(event);
  assert.equal(event.defaultPrevented, true);
  assert.equal(window.GWOmnibar.snapshot().open, true);
  assert.equal(parentDocument.getElementById('gwOmnibar').hidden, false);
});

test('工作台壳在全站入口挂载 Omnibar 脚本与黑金样式', () => {
  assert.match(shellSource, /function loadWorkbenchOmnibar\(\)/);
  assert.match(shellSource, /\/static\/js\/core\/omnibar\.js/);
  assert.match(shellSource, /\/static\/css\/components\/omnibar\.css/);
  assert.match(shellSource, /loadWorkbenchOmnibar\(\);/);
});
