import assert from 'node:assert/strict';
import test from 'node:test';
import {readFileSync} from 'node:fs';
import {runInNewContext} from 'node:vm';

const read = path => readFileSync(new URL(`../../${path}`, import.meta.url), 'utf8');
const hardware = read('web/js/core/hardware-telemetry.js');

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
  const document = {
    readyState: 'loading',
    addEventListener() {},
    getElementById(id) { return elements.get(id) || null; },
    createElement() { return element(); },
    createRange() { return {selectNodeContents() {}}; },
    body: {appendChild() {}},
    execCommand() { return false; },
  };
  const window = {
    addEventListener() {},
    confirm() { return false; },
    getSelection() { return {removeAllRanges() {}, addRange() {}}; },
    setTimeout() { return 1; },
  };
  const context = {window, document, navigator: {}, console, setTimeout() { return 1; }};
  runInNewContext(hardware, context);
  return {context, window, document, elements, dialog, deck: window.HardwareDeck};
}

// 已登录态固定夹具：QIN-483 只考察认证中心弹窗「已登录视图」的操作区。
function renderAuthenticated(h, role = 'admin') {
  h.deck.authState.degradation = 'ok';
  h.deck.authState.authenticated = true;
  h.deck.authState.auth_mode = 'local_account';
  h.deck.authState.principal = {username: 'tester', display_name: '测试者', role};
  h.deck.renderAccountModalContent();
  return h.dialog.innerHTML;
}

test('已登录认证中心弹窗删除「打开资产中心与团队分配」入口', () => {
  const html = renderAuthenticated(harness());
  assert.doesNotMatch(html, /打开资产中心与团队分配/);
  assert.doesNotMatch(html, /\/static\/pages\/assets\.html/);
});

test('已登录视图删除底部「关闭」按钮，仅保留右上角关闭入口', () => {
  const html = renderAuthenticated(harness());
  // 右上角圆形关闭按钮仍在（带 closeModal 调用与 aria-label）
  assert.match(html, /btn-hw-round[^>]*closeModal\('accountModal'\)[^>]*aria-label="关闭"/);
  // 底部不再有「关闭」文字按钮
  assert.doesNotMatch(html, /<button[^>]*hw-btn[^>]*>\s*关闭\s*<\/button>/);
});

test('「退出登录」移到账号信息右侧且绑定 handleLogout', () => {
  const html = renderAuthenticated(harness());
  // 退出登录按钮位于账号信息卡片（bay-inset 行）内部，位于会话信息块之前
  assert.match(html, /bay-inset p-3 rounded-xl flex items-center space-x-3[\s\S]*?账户ID: tester[\s\S]*?<button[^>]*hw-btn-danger[^>]*onclick="HardwareDeck\.handleLogout\(\)">\s*退出登录\s*<\/button>[\s\S]*?会话鉴权方式/);
  // handleLogout 功能入口存在（AC5）
  assert.equal(typeof harness().deck.handleLogout, 'function');
});

test('弹窗最下面一行保留但留空，行高与原按钮行一致（尺寸不变）', () => {
  const html = renderAuthenticated(harness());
  // 底部行保留分隔线与原行高（pt-2 + 32px 占位 = 原 pt-2 + hw-btn 32px）
  assert.match(html, /pt-2 border-t border-white\/10 flex items-center justify-between" aria-hidden="true">\s*<span class="h-8 w-full"><\/span>\s*<\/div>/);
  // 弹窗标题与整体结构未变
  assert.match(html, /认证中心 · 账户管理/);
});

test('降级与未登录视图不受本次调整影响', () => {
  const h = harness();
  h.deck.authState.degradation = 'not_integrated';
  h.deck.authState.authenticated = false;
  h.deck.renderAccountModalContent();
  assert.match(h.dialog.innerHTML, /认证服务未接入/);
  // 降级视图的底部关闭按钮与右上角关闭入口保持原样（本票只改已登录视图）
  assert.match(h.dialog.innerHTML, /closeModal\('accountModal'\)">关闭<\/button>/);
});
