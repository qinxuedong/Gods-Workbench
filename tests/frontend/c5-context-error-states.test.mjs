/**
 * C5-T6 前端项目上下文与错误状态回归守卫。
 * 这些测试只读取前端源码，不连接后端或外部服务。
 */
import assert from 'node:assert/strict';
import fs from 'node:fs';
import test from 'node:test';

const read = path => fs.readFileSync(path, 'utf8');
const canvasList = read('web/js/modules/canvas-list.js');
const canvasWorkspace = read('web/js/modules/canvas-workspace.js');
const workflow = read('web/js/controllers/workflow-controller.js');
const assetManager = read('web/js/modules/asset-manager.js');

test('C5 画布列表显式 project_id 失效时不得静默回落到默认项目', () => {
  assert.match(canvasList, /initialCanvasContext\.projectId/);
  assert.match(canvasList, /renderCanvasLoadError/);
  assert.doesNotMatch(canvasList, /if\(!projects\.length\)\s*projects\s*=\s*\[\{\s*id:\s*'default'/);
  assert.doesNotMatch(canvasList, /const def = projects\.find\(p => p\.id === 'default'\)/);
});

test('C5 画布工作台读取携带项目上下文且拒绝演示种子', () => {
  assert.match(canvasWorkspace, /project_id/);
  assert.match(canvasWorkspace, /GWDegradation\.noticeHtml/);
  assert.match(canvasWorkspace, /data-gw-degradation/);
  assert.doesNotMatch(canvasWorkspace, /entity_id:\s*'nd-001'/);
});

test('C5 工作流保存与执行统一使用显式项目来源上下文', () => {
  assert.match(workflow, /function executionSourceContext\(wf\)/);
  assert.match(workflow, /requireProjectContext/);
  assert.match(workflow, /source_context/);
  assert.match(workflow, /data-gw-degradation/);
});

test('C5 资产注册表项目列表把 activeProjectId 传给查询并保留错误状态', () => {
  const queryStart = assetManager.indexOf('function registryQueryString(');
  const queryEnd = assetManager.indexOf('function registryTotalFromResponse', queryStart);
  assert.ok(queryStart >= 0 && queryEnd > queryStart, '缺少 registryQueryString');
  const queryBody = assetManager.slice(queryStart, queryEnd);
  assert.match(queryBody, /activeProjectId/);
  assert.match(assetManager, /data-gw-degradation/);
  assert.match(assetManager, /project_id/);
});
