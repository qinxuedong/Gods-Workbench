import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, existsSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

const root = new URL('../../', import.meta.url);
const read = path => readFileSync(new URL(path, root), 'utf8');
const controller = read('web/js/controllers/workflow-controller.js');
const html = read('web/pages/workflow-workbench.html');
const css = read('web/css/pages/workflow-workbench.css');

test('工作流入口保留统一顶栏导航与资源路径', () => {
  for (const page of ['index', 'projects', 'workshop', 'production', 'storyboard', 'agents', 'assets', 'collab', 'settings', 'workflow-workbench']) {
    const source = read(`web/pages/${page}.html`);
    assert.match(source, /href="collab\.html"[\s\S]*?<\/a>\s*<a href="workflow-workbench\.html"/);
    assert.equal((source.match(/href="workflow-workbench\.html"/g) || []).length, 1);
  }
  for (const match of html.matchAll(/(?:src|href)="(\/static\/[^"?]+)/g)) assert.ok(existsSync(fileURLToPath(new URL(match[1].replace('/static/', 'web/'), root))));
  assert.doesNotMatch(html, /(?:src|href)="https?:\/\//);
  assert.match(html, /topbar-master-deck/);
  assert.match(html, /class="workflow-workbench"/);
});

test('页面提供成熟四区与完整成熟入口', () => {
  for (const id of ['workflowWorkbench', 'leftBusSidebar', 'centerActuatorRack', 'envDiffDrawer', 'workflowTreeList', 'selectedNodeContainer', 'actuatorCardsContainer', 'canvasViewport', 'canvasNodesLayer', 'taskModal', 'assetLibraryModal', 'clipboardModal', 'contractModal']) assert.match(html, new RegExp(`id="${id}"`));
  for (const token of ['btnNewFolder', 'btnRandomizeSeeds', 'btnRefreshDiff', 'btnAutoLayout', 'btnViewTaskDrawer', 'nodeMediaUploadInput', 'btnExecuteWorkflow']) assert.match(html, new RegExp(`id="${token}"`));
  assert.match(html, /class="workbench-main"/);
});

test('工作台 CSS 无页面级全屏规则且选择器已作用域化', () => {
  assert.doesNotMatch(css, /(^|[\n}])\s*(html|body|:root|\.workbench-shell)\s*\{/);
  assert.match(css, /\.workflow-workbench\s*\{/);
  assert.match(css, /\.workflow-workbench \.left-bus-sidebar/);
});

test('成熟控制器保留生命周期与工作流能力', () => {
  for (const token of ['function initWorkbench', 'function bindEvents', 'function disposeWorkbench', 'window.WorkbenchWorkflow', 'rebind:', 'dispose:']) assert.match(controller, new RegExp(token.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')));
  for (const token of ['capsuleRunway', 'folders', 'parse-link', 'parse-upload', 'assets/upload', 'canvas-contract', 'auto-layout', 'compare-local', 'nodeMediaUploadInput', 'btnRandomizeSeeds', 'btnViewTaskDrawer']) assert.match(controller, new RegExp(token));
  assert.doesNotMatch(controller, /2078/);
  assert.match(controller, /\/api\/god_workflow\/documents/);
});

test('控制器所有页面查询均从工作台根开始', () => {
  assert.doesNotMatch(controller, /document\.querySelector(All)?\(/);
  assert.match(controller, /function wfGet/);
  assert.match(controller, /function wfQueryAll/);
});
