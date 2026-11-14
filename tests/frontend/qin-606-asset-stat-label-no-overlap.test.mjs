import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

// QIN-606（GitHub #720）：资产总览主卡「资产总量」与「统一资产索引」几何重叠修复。
// 静态契约：主卡徽标 em 必须携带与「资产总量」small 同款截断三件套，使徽标在列 3
// 轨道被压缩到固有宽（66px）以下时就地截断，而不是 justify-self:end 左溢压字。
// 修复前后浏览器量测：1440 重叠 2.27px → 间距 ≥0；1920 相距 45.5px 不回归。
// 同时护栏 QIN-531 的 13 卡列数、2/3 宽度公式与 QIN-602 的窄视口 span 2 修复不被本票波及。

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const read = relativePath => readFileSync(path.join(root, relativePath), 'utf8');

const vaultCss = read('web/css/pages/asset-vault.css');
const js = readFileSync(path.join(root, 'web/js/modules/asset-manager.js'), 'utf8');

// AC1/AC2 核心契约：em 截断三件套与 justify-self:end 同块共存。
// （171~172 行还有一条 color 简写块同样以 .registry-stat-card em 结尾，故锚定 justify-self: end 锁定布局块。）
const emBlock = vaultCss.match(/\.registry-stat-card em \{[^}]*justify-self: end[^}]*\}/);
assert.ok(emBlock, 'asset-vault.css 必须定义概览卡徽标 em 布局规则');
for (const declaration of ['max-width: 100%', 'overflow: hidden', 'text-overflow: ellipsis']) {
  assert.match(emBlock[0], new RegExp(declaration.replace(/[:()]/g, '\\$&')), `徽标 em 规则应包含 ${declaration}（QIN-606 截断三件套）`);
}
assert.match(emBlock[0], /justify-self: end/, '徽标保持右对齐锚点（justify-self: end 不被移除）');
assert.match(emBlock[0], /white-space: nowrap/, '徽标保持单行文本语义（white-space: nowrap 不被移除）');

// 布局状态护栏 1：QIN-531 的 13 卡列数与 2/3 宽度公式不被本票改动。
const statsBlock = vaultCss.match(/\.registry-overview-stats \{([^}]*)\}/);
assert.ok(statsBlock, '必须定义统计条 grid 容器');
assert.match(statsBlock[1], /grid-template-columns:\s*2fr repeat\(12, \.95fr\)/, '13 卡列数（主卡 2fr + 12 张 .95fr）保持不变');
assert.match(statsBlock[1], /2 \/ 3/, 'QIN-531 的 2/3 宽度公式保持不变');

// 布局状态护栏 2：QIN-602 的 ≤820px 主卡跨两列修复不被本票改动。
assert.match(
  vaultCss,
  /@media \(max-width: 820px\) \{[\s\S]*?\.registry-overview-stats > \.registry-stat-card\.primary \{\s*grid-column: span 2;[\s\S]*?\}/,
  'QIN-602 的 ≤820px 主卡 span 2 修复保持不变'
);

// 渲染模板护栏：主卡徽标文案与三元逻辑不被本票改动（AC 范围外：文案与统计口径）。
assert.match(
  js,
  /<em>\$\{stat\.primary \? '统一资产索引' : `\$\{ratio\}%`\}<\/em>/,
  '主卡徽标渲染模板保持「统一资产索引」三元逻辑不变'
);
