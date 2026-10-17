/** QIN-197 五项 UI 与交互缺陷的源代码契约回归。 */
import assert from 'node:assert/strict';
import fs from 'node:fs';
import test from 'node:test';

const read = path => fs.readFileSync(path, 'utf8');

test('SPA 工作区选择器不会把首页控制条当成工作区', () => {
  const shell = read('web/js/core/workbench-shell.js');
  const index = read('web/pages/index.html');

  assert.match(shell, /#homeWorkspace, #workspace, #agentsWorkspace, main, \.app-shell-workspace/);
  assert.match(index, /id="homeWorkspace"[^>]*\bapp-shell-workspace\b/);
});

test('顶栏标准右侧清理所有导航之外的旧兄弟节点', () => {
  const shell = read('web/js/core/workbench-shell.js');

  assert.match(shell, /deck-nav-cluster/);
  assert.match(shell, /child !== nav && child !== right/);
});

test('智能体上下文横条挂在主区域内部', () => {
  const source = read('web/js/controllers/agents-controller.js');

  assert.match(source, /main\.insertBefore\(bar, main\.firstChild\)/);
});

test('画布创建结果保留 scope 并切换到可见分类，失败有显式告警', () => {
  const source = read('web/js/modules/canvas-list.js');

  assert.match(source, /scope:\s*canvasScope/);
  assert.match(source, /currentCanvasFilter\s*=\s*['"]project['"]/);
  assert.match(source, /alert\(/);
});
