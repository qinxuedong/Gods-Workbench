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

function keyEvent({key, code, ctrlKey = false, metaKey = false, altKey = false, shiftKey = false, isComposing = false, keyCode = 0}) {
  return {
    type: 'keydown', key, code, ctrlKey, metaKey, altKey, shiftKey, isComposing, keyCode, defaultPrevented: false,
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

test('IME composing 时 Enter 不提交 Agent 请求，也不阻止上屏', () => {
  const {document, window} = harness();
  document.dispatchEvent(keyEvent({key: ' ', code: 'Space', ctrlKey: true}));
  const input = document.getElementById('gwOmnibarInput');
  input.value = '继续生成';
  window.GWOmnibar.setMode('agent');
  let submitCount = 0;
  window.addEventListener('gw:omnibar-agent-submit', () => { submitCount++; });

  const event = keyEvent({key: 'Enter', code: 'Enter', isComposing: true});
  input.dispatchEvent(event);

  assert.equal(event.defaultPrevented, false);
  assert.equal(event.propagationStopped, undefined);
  assert.equal(submitCount, 0);
});

test('旧版 IME 用 keyCode 229 标记 Enter 时不提交，也不阻止上屏', () => {
  const {document, window} = harness();
  document.dispatchEvent(keyEvent({key: ' ', code: 'Space', ctrlKey: true}));
  const input = document.getElementById('gwOmnibarInput');
  input.value = '继续生成';
  window.GWOmnibar.setMode('agent');
  let submitCount = 0;
  window.addEventListener('gw:omnibar-agent-submit', () => { submitCount++; });

  const event = keyEvent({key: 'Enter', code: 'Enter', keyCode: 229});
  input.dispatchEvent(event);

  assert.equal(event.defaultPrevented, false);
  assert.equal(event.propagationStopped, undefined);
  assert.equal(submitCount, 0);
});

test('非 composing Enter 仍正常提交 Agent 请求', () => {
  const {document, window} = harness();
  document.dispatchEvent(keyEvent({key: ' ', code: 'Space', ctrlKey: true}));
  const input = document.getElementById('gwOmnibarInput');
  input.value = '继续生成';
  window.GWOmnibar.setMode('agent');
  let submittedPrompt = null;
  window.addEventListener('gw:omnibar-agent-submit', event => { submittedPrompt = event.detail.prompt; });

  const event = keyEvent({key: 'Enter', code: 'Enter'});
  input.dispatchEvent(event);

  assert.equal(event.defaultPrevented, true);
  assert.equal(event.propagationStopped, true);
  assert.equal(submittedPrompt, '继续生成');
});

test('无效结果链接会发出可观察的 Omnibar 错误事件', () => {
  const {document, window, omnibar} = harness();
  document.dispatchEvent(keyEvent({key: ' ', code: 'Space', ctrlKey: true}));
  omnibar.setResults([{title: '无效链接', href: 'http://['}]);
  document.dispatchEvent(keyEvent({key: 'ArrowDown', code: 'ArrowDown'}));
  const errors = [];
  window.addEventListener('gw:omnibar-error', event => { errors.push(event.detail); });

  const enter = keyEvent({key: 'Enter', code: 'Enter'});
  assert.doesNotThrow(() => document.getElementById('gwOmnibarInput').dispatchEvent(enter));

  assert.equal(enter.defaultPrevented, true);
  assert.equal(errors.length, 1);
  assert.equal(errors[0].code, 'INVALID_RESULT_URL');
  assert.equal(errors[0].href, 'http://[');
  assert.equal(typeof errors[0].message, 'string');
  assert.notEqual(errors[0].message, '');

  const errorState = document.getElementById('gwOmnibarError');
  assert.ok(errorState, 'Omnibar 应创建错误提示节点');
  assert.equal(errorState.hidden, false, '解析失败后错误态应可见');
  assert.equal(errorState.getAttribute('role'), 'alert', '错误态应由辅助技术及时播报');
  assert.match(errorState.textContent, /无法打开/, '错误态应提供面向用户的反馈');
  assert.equal(omnibar.snapshot().open, true, '解析失败后 Omnibar 应保持打开');
  assert.equal(document.getElementById('gwOmnibar').hidden, false);
});

test('正常同源结果链接会跳转且不显示 Omnibar 错误态', () => {
  const {document, window, omnibar} = harness();
  let assignedUrl = '';
  const errors = [];
  window.location.assign = url => { assignedUrl = url; };
  window.addEventListener('gw:omnibar-error', event => { errors.push(event.detail); });
  document.dispatchEvent(keyEvent({key: ' ', code: 'Space', ctrlKey: true}));
  omnibar.setResults([{title: '项目', href: '/projects?project_id=project-1'}]);
  document.dispatchEvent(keyEvent({key: 'ArrowDown', code: 'ArrowDown'}));

  const enter = keyEvent({key: 'Enter', code: 'Enter'});
  document.getElementById('gwOmnibarInput').dispatchEvent(enter);

  assert.equal(assignedUrl, 'http://workbench.local/projects?project_id=project-1');
  assert.deepEqual(errors, []);
  assert.equal(document.getElementById('gwOmnibarError').hidden, true);
  assert.equal(omnibar.snapshot().open, false, '成功跳转后 Omnibar 仍应关闭');
});

test('同源 URL 导航异常不误报解析错误，也不会形成未处理异常', async () => {
  const {document, window, omnibar} = harness();
  const errors = [];
  window.location.assign = () => { throw new Error('navigation failed'); };
  window.addEventListener('gw:omnibar-error', event => { errors.push(event.detail); });
  document.dispatchEvent(keyEvent({key: ' ', code: 'Space', ctrlKey: true}));
  omnibar.setResults([{title: '项目', href: '/projects?project_id=project-1'}]);
  document.dispatchEvent(keyEvent({key: 'ArrowDown', code: 'ArrowDown'}));

  await assert.rejects(omnibar.activateResult(), /navigation failed/);
  document.getElementById('gwOmnibarInput').dispatchEvent(keyEvent({key: 'Enter', code: 'Enter'}));
  await new Promise(resolve => setImmediate(resolve));

  assert.deepEqual(errors, [], 'URL 解析成功时，导航异常不能发 INVALID_RESULT_URL');
  assert.equal(document.getElementById('gwOmnibarError').hidden, true);
  assert.equal(omnibar.snapshot().open, true, '导航未完成时 Omnibar 保持打开');
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

function selectOmnibarResult(document, omnibar, result) {
  document.dispatchEvent(keyEvent({key: ' ', code: 'Space', ctrlKey: true}));
  omnibar.setResults([result]);
  document.dispatchEvent(keyEvent({key: 'ArrowDown', code: 'ArrowDown'}));
  return document.getElementById('gwOmnibarInput');
}

async function pressOmnibarEnter(input) {
  const event = keyEvent({key: 'Enter', code: 'Enter'});
  input.dispatchEvent(event);
  await new Promise(resolve => setImmediate(resolve));
  return event;
}

for (const {label, href} of [
  {label: '跨源域名', href: 'https://evil.example/phish'},
  {label: '跨源端口', href: 'http://workbench.local:8080/path'},
  {label: '跨源协议', href: 'https://workbench.local/secure'},
  {label: 'javascript 伪协议', href: 'javascript:alert(1)'},
  {label: 'data 伪协议', href: 'data:text/html,<h1>bad</h1>'},
]) {
  test(`${label} URL 被拦截并显示错误，不关闭 Omnibar`, () => {
    const {document, window, omnibar} = harness();
    let assignedUrl = '';
    const errors = [];
    window.location.assign = url => { assignedUrl = url; };
    window.addEventListener('gw:omnibar-error', event => { errors.push(event.detail); });
    selectOmnibarResult(document, omnibar, {title: label, href});
    document.getElementById('gwOmnibarInput').dispatchEvent(keyEvent({key: 'Enter', code: 'Enter'}));

    assert.equal(assignedUrl, '', '危险 URL 不得触发跳转');
    assert.equal(omnibar.snapshot().open, true, '错误时面板保持打开');
    assert.equal(errors.length, 1);
    assert.equal(errors[0].code, 'CROSS_ORIGIN_URL_BLOCKED');
    assert.equal(document.getElementById('gwOmnibarError').textContent, '不支持跳转到外部地址');
  });
}

test('activate 同步抛错时显示脱敏错误、保留输入焦点和面板', async () => {
  const {document, window, omnibar} = harness();
  const errors = [];
  window.addEventListener('gw:omnibar-error', event => { errors.push(event.detail); });
  const input = selectOmnibarResult(document, omnibar, {
    title: '失败命令',
    activate() { throw new Error('C:\\private\\source.js:77 secret stack'); },
  });
  input.value = '/run private';

  assert.doesNotThrow(() => input.dispatchEvent(keyEvent({key: 'Enter', code: 'Enter'})));
  await new Promise(resolve => setImmediate(resolve));

  const errorState = document.getElementById('gwOmnibarError');
  assert.equal(omnibar.snapshot().open, true);
  assert.equal(errorState.hidden, false);
  assert.equal(input.value, '/run private');
  assert.equal(document.activeElement, input);
  assert.equal(errors.length, 1);
  assert.equal(errors[0].code, 'COMMAND_ACTIVATION_FAILED');
  assert.equal(errorState.textContent, '命令执行失败，请重试。');
  assert.doesNotMatch(errorState.textContent, /private|source\.js|stack/i);
  assert.equal(JSON.stringify(errors[0]).includes('C:\\private'), false);
});

test('activate 异步 reject 后可用 Enter 重试，成功后才关闭', async () => {
  const {document, window, omnibar} = harness();
  let attempts = 0;
  const errors = [];
  window.addEventListener('gw:omnibar-error', event => { errors.push(event.detail); });
  const input = selectOmnibarResult(document, omnibar, {
    title: '异步命令',
    activate() {
      attempts++;
      if (attempts === 1) return Promise.reject(new Error('C:\\private\\backend.py:91 secret stack'));
      return Promise.resolve('done');
    },
  });
  input.value = '/retry command';

  await pressOmnibarEnter(input);
  assert.equal(attempts, 1);
  assert.equal(omnibar.snapshot().open, true);
  assert.equal(input.value, '/retry command');
  assert.equal(document.activeElement, input);
  assert.equal(document.getElementById('gwOmnibarError').textContent, '命令执行失败，请重试。');
  assert.equal(errors.length, 1);
  assert.equal(errors[0].code, 'COMMAND_ACTIVATION_FAILED');
  assert.equal(JSON.stringify(errors[0]).includes('backend.py'), false);

  await pressOmnibarEnter(input);
  assert.equal(attempts, 2);
  assert.equal(omnibar.snapshot().open, false, '重试成功后才关闭');
});

test('activate 成功完成后关闭 Omnibar', async () => {
  const {document, omnibar} = harness();
  const input = selectOmnibarResult(document, omnibar, {
    title: '成功命令',
    activate: () => Promise.resolve('done'),
  });

  await pressOmnibarEnter(input);

  assert.equal(omnibar.snapshot().open, false);
});
