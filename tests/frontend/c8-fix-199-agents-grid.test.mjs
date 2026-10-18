/** QIN-199 C8 智能体页面 Grid 布局回归。 */
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

const read = path => readFileSync(path, 'utf8');

test('智能体上下文横条占满主区 12 列', () => {
  const controller = read('web/js/controllers/agents-controller.js');

  assert.match(
    controller,
    /bar\.className\s*=\s*['"][^'"]*\bcol-span-12\b[^'"]*['"]/
  );
  assert.match(controller, /main\.insertBefore\(bar, main\.firstChild\)/);
});

test('智能体主区保留 8 列思维链与 4 列控制台并排结构', () => {
  const page = read('web/pages/agents.html');
  const main = page.match(
    /<main id="agentsMain" class="([^"]*grid-cols-12[^"]*)">([\s\S]*?)<\/main>/
  );

  assert.ok(main, 'agentsMain 应保持 12 列 Grid 主区');
  assert.match(main[2], /<div class="col-span-8\b/);
  assert.match(main[2], /<div class="col-span-4\b/);
});

test('上下文横条与主区同属 agentsWorkspace，直接刷新与导航进入使用同一布局容器', () => {
  const page = read('web/pages/agents.html');
  const workspace = page.match(
    /<div id="agentsWorkspace"[^>]*>[\s\S]*?<main id="agentsMain"/
  );

  assert.ok(workspace, 'agentsMain 应挂在 agentsWorkspace 内');
  assert.match(read('web/js/controllers/agents-controller.js'), /getElementById\('agentsMain'\)/);
});
