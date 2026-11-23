/**
 * QIN-645：内嵌/分享页整体迁出并下线 signal-flow.css 的防回归门禁。
 *
 * 覆盖四类不可回退的契约：
 *   1. 旧样式文件已物理下线，且全库 HTML 不再以 <link> 引用它；
 *   2. 五个页面统一接入硬件设计系统四件套；
 *   3. 公共层只作用于 body.gw-signal，不污染主工作台 body.gw-shell；
 *   4. 公共层完整承接明暗两套语义变量与控件基础层，避免 light 下塌底色。
 */
import assert from 'node:assert/strict';
import { existsSync, readFileSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import test from 'node:test';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const read = relativePath => readFileSync(path.join(root, relativePath), 'utf8');
const exists = relativePath => existsSync(path.join(root, relativePath));

const PAGES = {
  'web/embeds/task-center.html': 'gw-page--task-center',
  'web/embeds/episode-pipeline.html': 'gw-page--episode-pipeline',
  'web/embeds/governance.html': 'gw-page--governance',
  'web/embeds/canvas-list.html': 'gw-page--canvas',
  'web/asset-share.html': 'gw-page--asset-share',
};

const COMMON = [
  'css/base/hardware-tokens.css',
  'css/base/hardware-design-system.css',
  'css/base/hardware-buttons.css',
  'css/base/workspace-pages.css',
];

test('QIN-645：signal-flow.css 已物理下线', () => {
  assert.equal(exists('web/css/base/signal-flow.css'), false, '旧样式文件必须已删除');
});

test('QIN-645：全库 HTML 不再以 link 引用 signal-flow.css', () => {
  for (const page of Object.keys(PAGES)) {
    const html = read(page);
    assert.doesNotMatch(html, /<link[^>]+signal-flow\.css/, `${page} 仍以 link 加载旧样式`);
  }
});

test('QIN-645：五个页面统一接入硬件设计系统四件套', () => {
  for (const [page, bodyClass] of Object.entries(PAGES)) {
    const html = read(page);
    assert.match(html, new RegExp(`<body[^>]*class="[^"]*${bodyClass}`), `${page} 的 body 归属页类丢失`);
    for (const css of COMMON) {
      assert.match(
        html,
        new RegExp(`<link[^>]+${css.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}`),
        `${page} 缺少 ${css}`,
      );
    }
    assert.match(html, /class="gw-signal/, `${page} 需保留 gw-signal 供公共层命中`);
  }
});

test('QIN-645：canvas-list 不再引用已删除的 canvasLegacySignal', () => {
  const html = read('web/embeds/canvas-list.html');
  assert.doesNotMatch(html, /canvasLegacySignal/, '悬空引用会在 overview 布局下抛空指针');
  // 同段的 legacy theme 链接属本票范围外，须保留并带存在性判空。
  assert.match(html, /id="canvasLegacyTheme"/, 'theme.css 链接应保持不变');
  assert.match(
    html,
    /var legacyTheme = document\.getElementById\('canvasLegacyTheme'\);\s*if \(legacyTheme\) legacyTheme\.disabled = true;/,
    'legacy theme 关闭逻辑需判空',
  );
});

test('QIN-645：公共层作用域限定 body.gw-signal 且排除 gw-shell', () => {
  const css = read('web/css/base/hardware-design-system.css');
  const marker = 'QIN-645 内嵌/分享页公共层';
  assert.ok(css.includes(marker), '公共层缺失');
  const block = css.slice(css.indexOf(marker));
  // 公共层内的每条选择器都必须落在 gw-signal 作用域，不得直接写全局 :root 变量块。
  const selectors = [...block.matchAll(/(^|\})\s*([^{}@]+)\{/g)]
    .map(match => match[2].trim())
    .filter(selector => selector && !selector.startsWith('/*'));
  assert.ok(selectors.length > 20, `公共层规则数量异常：${selectors.length}`);
  for (const selector of selectors) {
    const scoped = /gw-signal/.test(selector) || /^html:has\(> body\.gw-signal/.test(selector);
    assert.ok(scoped, `公共层出现非 gw-signal 作用域选择器：${selector.slice(0, 80)}`);
  }
  // 排除护栏：所有 body 级规则都带 :not(:where(.gw-shell))。
  const bodyRules = selectors.filter(selector => selector.startsWith('body.gw-signal'));
  assert.ok(bodyRules.length > 0, '缺少 body.gw-signal 规则');
  for (const selector of bodyRules) {
    assert.match(
      selector,
      /:not\(:where\(\.gw-shell\)\)/,
      `body 规则缺少 gw-shell 排除护栏：${selector.slice(0, 80)}`,
    );
  }
});

test('QIN-645：公共层承接暗色与明色两套语义变量', () => {
  const css = read('web/css/base/hardware-design-system.css');
  const block = css.slice(css.indexOf('QIN-645 内嵌/分享页公共层'));
  // 暗色实值块：非首页路由在暗色下的真实取值。
  assert.match(
    block,
    /body\.gw-signal:not\(\.gw-page--home\):not\(\[data-theme="light"\]\):not\(\.theme-light\)[^{]*\{[^}]*--sf-content:\s*#111b23\s*!important/s,
    '缺少暗色语义变量实值块',
  );
  assert.match(block, /--sf-faint:\s*#71808a\s*!important/, '缺少暗色 faint 取值');
  // 明色块：整套变量切浅色，且带 color-scheme。
  assert.match(block, /html body\.gw-signal:not\(:where\(\.gw-shell\)\)\[data-theme="light"\]/, '缺少明色变量块');
  assert.match(block, /--sf-bg:\s*#f1f0ec\s*!important/, '缺少明色背景取值');
  assert.match(block, /color-scheme:\s*light\s*!important/, '明色缺少 color-scheme');
});

test('QIN-645：公共层承接控件基础层', () => {
  const css = read('web/css/base/hardware-design-system.css');
  const block = css.slice(css.indexOf('QIN-645 内嵌/分享页公共层'));
  assert.match(block, /--sf-radius-control:\s*6px/, '缺少控件圆角令牌');
  assert.match(
    block,
    /:is\(select, textarea, input:not\(\[type="color"\]\)[^{]*\{[^}]*min-height:\s*40px/s,
    '缺少表单控件最小高度',
  );
  assert.match(block, /min-height:\s*36px/, '缺少非硬件按钮控件最小高度');
  assert.match(block, /:focus-visible/, '缺少焦点可见样式');
  assert.match(block, /--sf-font-mono/, '缺少等宽字体令牌');
});
