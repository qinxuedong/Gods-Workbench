import assert from 'node:assert/strict';
import test from 'node:test';
import {readFileSync} from 'node:fs';
import {runInNewContext} from 'node:vm';

const read = path => readFileSync(new URL(`../../${path}`, import.meta.url), 'utf8');
const hardware = read('web/js/core/hardware-telemetry.js');
const settings = read('web/pages/settings.html');

function element() {
  const values = new Set(['hidden']);
  return {
    textContent: '',
    value: '',
    disabled: false,
    focused: false,
    style: {},
    removed: false,
    classList: {
      add(value) { values.add(value); },
      remove(value) { values.delete(value); },
      contains(value) { return values.has(value); },
    },
    setAttribute() {},
    focus() { this.focused = true; },
    select() { this.selected = true; },
    remove() { this.removed = true; },
  };
}

function harness() {
  const elements = new Map();
  const dialog = element();
  elements.set('accountModalDialog', dialog);
  const selection = {cleared: false, added: false};
  let selectedNode = null;
  const document = {
    readyState: 'loading',
    addEventListener() {},
    getElementById(id) { return elements.get(id) || null; },
    createElement() { return element(); },
    createRange() {
      return {selectNodeContents(node) { selectedNode = node; }};
    },
    body: {appendChild(node) { node.parentNode = this; }},
    execCommand() { return false; },
  };
  const window = {
    addEventListener() {},
    confirm() { return false; },
    getSelection() {
      return {
        removeAllRanges() { selection.cleared = true; },
        addRange() { selection.added = true; },
      };
    },
    setTimeout() { return 1; },
  };
  const context = {window, document, navigator: {}, console, setTimeout() { return 1; }};
  runInNewContext(hardware, context);
  return {context, window, document, elements, dialog, deck: window.HardwareDeck, selection, selectedNode: () => selectedNode};
}

function recoveryList(codes) {
  return {querySelectorAll(selector) {
    assert.equal(selector, 'li');
    return codes.map(code => ({textContent: ` ${code} `}));
  }};
}

test('恢复码弹窗提供复制与重新生成，重置流程初始仍需勾选保存', () => {
  const h = harness();
  h.deck.renderLocalRecoveryCodes(['RC-1', 'RC-2']);
  assert.match(h.dialog.innerHTML, /id="localRecoveryCopyAll"[\s\S]*?复制全部/);
  assert.match(h.dialog.innerHTML, /id="localRecoveryRegenerate"[\s\S]*?重新生成/);
  assert.match(h.dialog.innerHTML, /id="localRecoveryCurrentPassword"[\s\S]*type="password"/);
  assert.match(h.dialog.innerHTML, /id="localRecoveryContinue"[^>]*disabled/);
  assert.doesNotMatch(h.dialog.innerHTML, /id="localRecoverySaved"[^>]*checked/);
});

test('设置页重新生成结果区提供复制全部按钮', () => {
  assert.match(settings, /id="settingsRecoveryCodeList"/);
  assert.match(settings, /id="settingsRecoveryCopyAll"[^>]*>复制全部/);
  assert.match(settings, /copyRecoveryCodes\(document\.getElementById\('settingsRecoveryCodeList'\)/);
});

test('复制全部写入 8 条纯文本恢复码并显示已复制', async () => {
  const h = harness();
  const codes = Array.from({length: 8}, (_, index) => `RC-${index + 1}-TOKEN`);
  let clipboardText = '';
  h.context.navigator.clipboard = {async writeText(text) { clipboardText = text; }};
  const button = element();
  button.textContent = '复制全部';
  const status = element();
  const copied = await h.deck.copyRecoveryCodes(recoveryList(codes), button, status);
  assert.equal(copied, true);
  assert.equal(clipboardText, codes.join('\n'));
  assert.equal(button.textContent, '已复制');
  assert.equal(status.textContent, '已复制');
});

test('Clipboard API 失败时用 execCommand 复制纯文本', async () => {
  const h = harness();
  const codes = ['RC-ONE', 'RC-TWO'];
  h.context.navigator.clipboard = {async writeText() { throw new Error('clipboard denied'); }};
  let copiedText = '';
  h.document.execCommand = command => {
    assert.equal(command, 'copy');
    copiedText = h.document.body.lastChild.value;
    return true;
  };
  h.document.body.appendChild = function(node) {
    node.parentNode = this;
    this.lastChild = node;
  };
  const button = element();
  button.textContent = '复制全部';
  const status = element();
  assert.equal(await h.deck.copyRecoveryCodes(recoveryList(codes), button, status), true);
  assert.equal(copiedText, codes.join('\n'));
  assert.equal(h.document.body.lastChild.selected, true);
  assert.equal(h.document.body.lastChild.removed, true);
  assert.equal(status.textContent, '已复制');
});

test('Clipboard API 不可用时回退到 execCommand', async () => {
  const h = harness();
  const codes = ['RC-ONE', 'RC-TWO'];
  let copiedText = '';
  h.document.execCommand = command => {
    assert.equal(command, 'copy');
    copiedText = h.document.body.lastChild.value;
    return true;
  };
  h.document.body.appendChild = function(node) {
    node.parentNode = this;
    this.lastChild = node;
  };
  const status = element();
  assert.equal(await h.deck.copyRecoveryCodes(recoveryList(codes), element(), status), true);
  assert.equal(copiedText, codes.join('\n'));
  assert.equal(status.textContent, '已复制');
});

test('自动复制失败时选中恢复码并提示手动复制', async () => {
  const h = harness();
  h.context.navigator.clipboard = {async writeText() { throw new Error('clipboard denied'); }};
  const list = recoveryList(['RC-ONE', 'RC-TWO']);
  const status = element();
  assert.equal(await h.deck.copyRecoveryCodes(list, element(), status), false);
  assert.equal(h.selectedNode(), list);
  assert.equal(h.selection.cleared, true);
  assert.equal(h.selection.added, true);
  assert.match(status.textContent, /手动复制/);
});

test('弹窗重新生成先确认，取消时不显示密码表单也不发请求', () => {
  const h = harness();
  const form = element();
  h.elements.set('localRecoveryRegenerationForm', form);
  let requestCount = 0;
  h.window.GodsWorkbenchAssetAuthApi = {
    regenerateLocalRecoveryCodes() { requestCount += 1; },
  };
  h.window.confirm = message => {
    assert.match(message, /旧码将全部作废/);
    return false;
  };
  h.deck.confirmLocalRecoveryCodeRegeneration();
  assert.equal(form.classList.contains('hidden'), true);
  assert.equal(requestCount, 0);
});

test('弹窗重新生成复用现有 API，成功替换恢复码并重置保存确认', async () => {
  const h = harness();
  const oldCodes = ['OLD-CODE-1', 'OLD-CODE-2'];
  const newCodes = ['NEW-CODE-1', 'NEW-CODE-2'];
  h.deck.renderLocalRecoveryCodes(oldCodes);
  const password = element();
  password.value = 'Test2026';
  const submit = element();
  const error = element();
  const form = {
    classList: element().classList,
    querySelector(selector) {
      if (selector === '#localRecoveryCurrentPassword') return password;
      if (selector === 'button[type="submit"]') return submit;
      return null;
    },
  };
  h.elements.set('localRecoveryRegenerationForm', form);
  h.elements.set('localRecoveryRegenerationError', error);
  h.window.confirm = message => {
    assert.match(message, /旧码将全部作废/);
    return true;
  };
  h.deck.confirmLocalRecoveryCodeRegeneration();
  assert.equal(form.classList.contains('hidden'), false);
  assert.equal(password.focused, true);
  const requests = [];
  h.window.GodsWorkbenchAssetAuthApi = {
    async regenerateLocalRecoveryCodes(payload) {
      requests.push(payload);
      return {ok: true, json: async () => ({recovery_codes: newCodes})};
    },
  };
  await h.deck.handleLocalRecoveryCodeRegeneration({preventDefault() {}, target: form});
  assert.equal(requests.length, 1);
  assert.equal(requests[0].current_password, 'Test2026');
  assert.equal(password.value, '');
  assert.equal(submit.disabled, false);
  assert.match(h.dialog.innerHTML, /NEW-CODE-1/);
  assert.doesNotMatch(h.dialog.innerHTML, /OLD-CODE-1/);
  assert.match(h.dialog.innerHTML, /id="localRecoverySaved"/);
  assert.doesNotMatch(h.dialog.innerHTML, /id="localRecoverySaved"[^>]*checked/);
  assert.match(h.dialog.innerHTML, /id="localRecoveryContinue"[^>]*disabled/);
});

test('弹窗重新生成失败显示错误且保留旧恢复码', async () => {
  const h = harness();
  const oldCodes = ['OLD-CODE-1', 'OLD-CODE-2'];
  h.deck.renderLocalRecoveryCodes(oldCodes);
  const before = h.dialog.innerHTML;
  const password = element();
  password.value = 'Wrong2026';
  const submit = element();
  const error = element();
  const form = {
    querySelector(selector) {
      if (selector === '#localRecoveryCurrentPassword') return password;
      if (selector === 'button[type="submit"]') return submit;
      return null;
    },
  };
  h.elements.set('localRecoveryRegenerationForm', form);
  h.elements.set('localRecoveryRegenerationError', error);
  h.window.GodsWorkbenchAssetAuthApi = {
    async regenerateLocalRecoveryCodes() {
      return {ok: false, json: async () => ({detail: {message: '当前密码错误'}})};
    },
  };
  await h.deck.handleLocalRecoveryCodeRegeneration({preventDefault() {}, target: form});
  assert.equal(h.dialog.innerHTML, before);
  assert.equal(error.textContent, '当前密码错误');
  assert.equal(error.classList.contains('hidden'), false);
  assert.equal(password.value, '');
  assert.equal(submit.disabled, false);
});
