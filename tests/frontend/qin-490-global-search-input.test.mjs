import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import path from 'node:path';
import test from 'node:test';

// QIN-490（GitHub #491）：Ctrl+Space 浮层更名「全局搜索」，搜索输入框统一黑灰底，
// 焦点高亮作用于整行而不是输入元素自身的方框。
const repoRoot = process.cwd();
const read = (relativePath) => readFileSync(path.join(repoRoot, relativePath), 'utf8');

const omnibarJs = read('web/js/core/omnibar.js');
const omnibarCss = read('web/css/components/omnibar.css');

const blockOf = (selector) => {
  const at = omnibarCss.indexOf(`${selector} {`);
  assert.ok(at >= 0, `omnibar.css 缺少规则 ${selector}`);
  return omnibarCss.slice(at, omnibarCss.indexOf('}', at));
};

test('AC1 面板标题统一为「全局搜索」，不再出现「命令总线」', () => {
  assert.match(omnibarJs, /makeElement\('span', 'gw-omnibar-title', '全局搜索'\)/);
  assert.doesNotMatch(omnibarJs, /统一命令总线/);
  assert.doesNotMatch(omnibarJs, /命令面板/);
});

test('AC1 浮层与面板的 aria 文案同步为「全局搜索」', () => {
  assert.match(omnibarJs, /overlay\.setAttribute\('aria-label', '全局搜索'\)/);
  assert.match(omnibarJs, /panel\.setAttribute\('aria-label', '全局搜索面板'\)/);
});

test('AC2 输入行使用黑灰底，不再用近纯黑透明铺底', () => {
  const row = blockOf('.gw-omnibar-input-row');
  assert.match(row, /background:\s*#14171c;/, '输入行应为黑灰实色底');
  assert.doesNotMatch(row, /rgba\(3,\s*5,\s*8/);
});

test('AC2 输入元素自身透明无边框，并压过各页 input 皮肤', () => {
  assert.match(
    omnibarCss,
    /\.gw-omnibar-panel \.gw-omnibar-input,\s*\n\.gw-omnibar-panel \.gw-omnibar-input:hover,[\s\S]*?background:\s*transparent !important;/,
    '需要面板级高特异性覆盖，避免页面 !important 皮肤把输入框刷成白底'
  );
  assert.match(omnibarCss, /\.gw-omnibar-panel \.gw-omnibar-input:focus-visible \{[\s\S]*?background:\s*transparent !important;/);
  assert.match(omnibarCss, /\.gw-omnibar-panel \.gw-omnibar-input[^{]*\{[\s\S]*?border:\s*0 !important;/);
});

test('AC3 焦点高亮落在整个输入行，输入元素自身无 outline 方框', () => {
  const focusWithIn = blockOf('.gw-omnibar-input-row:focus-within');
  assert.match(focusWithIn, /border-color:/);
  assert.match(focusWithIn, /box-shadow:\s*[^;]*0 0 0 3px/, '整行应有外扩高亮环');
  assert.match(
    omnibarCss,
    /\.gw-omnibar-input:focus-visible \{[\s\S]*?outline:\s*0 !important;/,
    '输入元素自身必须去掉 outline 方框'
  );
  assert.match(omnibarCss, /\.gw-omnibar-panel :focus-visible:not\(\.gw-omnibar-input\) \{/);
});

test('AC4 占位符颜色在面板内统一，不受页面 ::placeholder !important 影响', () => {
  assert.match(
    omnibarCss,
    /\.gw-omnibar-panel \.gw-omnibar-input::placeholder \{[\s\S]*?color:\s*#[0-9a-f]{6} !important;/i
  );
});

test('AC2 搜索框原生装饰（放大镜取消按钮等）不参与渲染', () => {
  assert.match(omnibarCss, /::-webkit-search-cancel-button[\s\S]*?-webkit-appearance:\s*none;/);
});
