import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const read = relativePath => readFileSync(path.join(root, relativePath), 'utf8');

const workspacePagesCss = read('web/css/base/workspace-pages.css');
const hardwareDesignSystemCss = read('web/css/base/hardware-design-system.css');

// AC3: workspace-pages.css 不得包含 light 颜色模式或浅色 body 背景覆盖
assert.doesNotMatch(
  workspacePagesCss,
  /color-scheme:\s*light/i,
  'workspace-pages.css 不得声明 color-scheme: light 覆盖深色黑金模式'
);

assert.doesNotMatch(
  workspacePagesCss,
  /\bbody\s*\{[^}]*background(?:-color)?\s*:/is,
  'workspace-pages.css 不得包含全局 body 背景声明，防止覆盖黑金硬件底色'
);

assert.doesNotMatch(
  workspacePagesCss,
  /\bhtml\s*,\s*body\s*\{/is,
  'workspace-pages.css 不得重置全局 html, body'
);

// 验证必须保留的侧栏总线导入与布局
assert.match(
  workspacePagesCss,
  /@import\s+url\(["']\.\/side-bus-unified\.css["']\);/,
  'workspace-pages.css 必须保留侧栏统一样式导入'
);

// 验证 hardware-design-system.css 中的深色底色契约完好
assert.match(
  hardwareDesignSystemCss,
  /background-color:\s*var\(--chassis-bg\);/,
  'hardware-design-system.css 必须提供 --chassis-bg 背景色'
);

// 验证 7 个引入 workspace-pages.css 的页面均正确加载了 hardware-design-system.css
const targetPages = [
  'web/pages/index.html',
  'web/pages/agents.html',
  'web/pages/assets.html',
  'web/pages/collab.html',
  'web/pages/production.html',
  'web/pages/settings.html',
  'web/pages/workflow.html'
];

for (const pageRel of targetPages) {
  const content = read(pageRel);
  assert.match(
    content,
    /hardware-design-system\.css/,
    `${pageRel} 必须引入 hardware-design-system.css 以提供黑金底色`
  );
  assert.match(
    content,
    /workspace-pages\.css/,
    `${pageRel} 保持引入 workspace-pages.css`
  );
}

console.log('QIN-492 workspace pages theme contracts passed');
