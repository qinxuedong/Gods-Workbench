/**
 * C7-T2 工作流媒体最小闭环前端交互与溯源测试。
 * 验证统一资产徽标展示、资产跳转、来源回跳以及任务恢复。
 */
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import { runInNewContext } from 'node:vm';

const root = new URL('../../', import.meta.url);
const controller = readFileSync(new URL('web/js/controllers/workflow-controller.js', root), 'utf8');

function setupPollingHarness(taskResponse) {
  const start = controller.indexOf('function startTaskPolling(taskId, target) {');
  const end = controller.indexOf('/* ==================== 7. 事件绑定', start);
  const body = { innerHTML: '', querySelector: () => null };
  const timers = new Map();
  const state = { pollTimer: null };
  const context = {
    state,
    Set,
    String,
    Array,
    encodeURIComponent,
    URLSearchParams,
    location: { search: '?project_id=prj-c7-001&canvas_id=cnv-c7-002' },
    wfGet: () => body,
    markUnknownTaskSeen: () => {},
    escapeHtml: value => String(value ?? '').replaceAll('<', '&lt;'),
    apiFetch: async () => taskResponse,
    clearInterval: id => timers.delete(id),
    clearTimeout: id => timers.delete(id),
    setInterval: callback => {
      const id = timers.size + 1;
      timers.set(id, callback);
      return id;
    },
    setTimeout: callback => {
      const id = timers.size + 1;
      timers.set(id, callback);
      return id;
    },
  };
  runInNewContext(controller.slice(start, end), context);
  return { ...context, body, timers, flush: () => new Promise(resolve => setImmediate(resolve)) };
}

test('C7-T2 轮询完成时产物渲染包含统一资产徽标与资产详情链接', async () => {
  const harness = setupPollingHarness({
    status: 'completed',
    execution_status: 'completed',
    collection_status: 'completed',
    outputs: [
      {
        registered: true,
        asset_id: 'ast-c7-media-001',
        url: '/api/god_workflow/assets/ast-c7-media-001/content',
        media_type: 'image',
        filename: 'c7_controlled_output.png',
      },
    ],
    source_context: { project_id: 'prj-c7-001', canvas_id: 'cnv-c7-002' },
  });

  harness.startTaskPolling('job-c7-test-1', 'local_comfy');
  await harness.flush();

  assert.match(harness.body.innerHTML, /统一资产:\s*ast-c7-media-001/);
  assert.match(harness.body.innerHTML, /data-asset-id="ast-c7-media-001"/);
  assert.match(harness.body.innerHTML, /href="\/static\/pages\/assets\.html\?asset_id=ast-c7-media-001"/);
  assert.match(harness.body.innerHTML, /查看资产详情/);
});

test('C7-T2 轮询完成时展示来源回跳项目与画布链接', async () => {
  const harness = setupPollingHarness({
    status: 'completed',
    execution_status: 'completed',
    collection_status: 'completed',
    outputs: [
      {
        registered: true,
        asset_id: 'ast-c7-media-002',
        url: '/api/god_workflow/assets/ast-c7-media-002/content',
        media_type: 'image',
      },
    ],
    source_context: { project_id: 'prj-c7-001', canvas_id: 'cnv-c7-002' },
  });

  harness.startTaskPolling('job-c7-test-2', 'rh_cloud');
  await harness.flush();

  assert.match(harness.body.innerHTML, /来源回跳/);
  assert.match(harness.body.innerHTML, /data-source-provenance/);
  assert.match(harness.body.innerHTML, /href="\/static\/pages\/projects\.html\?project_id=prj-c7-001"/);
  assert.match(harness.body.innerHTML, /返回来源项目/);
  assert.match(harness.body.innerHTML, /href="\/static\/pages\/canvas\.html\?canvas_id=cnv-c7-002&(?:amp;)?project_id=prj-c7-001"/);
  assert.match(harness.body.innerHTML, /返回来源画布/);
});

test('C7-T2 restoreWorkflowTask 支持从 URL 直接恢复指定 job_id 任务', async () => {
  const start = controller.indexOf('async function restoreWorkflowTask() {');
  const end = controller.indexOf('function applySavedSettings(s) {', start);

  const modal = { classList: { add() {}, remove() {}, contains: () => false } };
  let polledTaskId = null;
  const state = {
    currentWorkflow: { workflow_id: 'wf-c7-001' },
    workflowTasks: [],
    activeTaskId: null,
    expandedTaskId: null,
  };

  const context = {
    state,
    URLSearchParams,
    location: { search: '?job_id=job-requested-c7' },
    wfGet: id => (id === 'taskModal' ? modal : null),
    setWorkflowTaskTrigger: () => {},
    markUnknownTaskSeen: () => {},
    renderWorkflowTaskList: () => {},
    showToast: () => {},
    apiFetch: async url => {
      if (url === '/api/god_workflow/tasks') return { tasks: [] };
      if (url === '/api/god_workflow/tasks/job-requested-c7') {
        return {
          job_id: 'job-requested-c7',
          target: 'local_comfy',
          status: 'running',
          outputs: [],
        };
      }
      throw new Error(`Unexpected url: ${url}`);
    },
    startTaskPolling: (taskId, target) => {
      polledTaskId = taskId;
    },
  };

  runInNewContext(controller.slice(start, end), context);
  await context.restoreWorkflowTask();

  assert.equal(state.activeTaskId, 'job-requested-c7');
  assert.equal(polledTaskId, 'job-requested-c7');
  assert.equal(state.workflowTasks.length, 1);
  assert.equal(state.workflowTasks[0].job_id, 'job-requested-c7');
});

test('C7-T2 renderProjectContextBadge 渲染项目与画布导航入口', () => {
  const start = controller.indexOf('function renderProjectContextBadge() {');
  const end = controller.indexOf('function getSourceBadgeText(', start);

  const elements = new Map();
  const context = {
    escapeHtml: v => String(v ?? ''),
    encodeURIComponent,
    URLSearchParams,
    location: { search: '?project_id=prj-proj-123&canvas_id=cnv-456' },
    document: {
      createElement: tag => {
        const el = { tagName: tag, id: '', className: '', innerHTML: '', style: {} };
        return el;
      },
    },
    wfGet: id => elements.get(id) || null,
    wfQuery: sel => null,
  };

  // Mock capsuleRunway to receive badge
  const runway = { appendChild: el => elements.set('workflowProjectContextBadge', el) };
  elements.set('capsuleRunway', runway);

  runInNewContext(controller.slice(start, end), context);
  context.renderProjectContextBadge();

  const badge = elements.get('workflowProjectContextBadge');
  assert.ok(badge, '必须在 sub-deck 中挂载项目上下文 badge');
  assert.match(badge.innerHTML, /prj-proj-123/);
  assert.match(badge.innerHTML, /href="\/static\/pages\/projects\.html\?project_id=prj-proj-123"/);
  assert.match(badge.innerHTML, /回到项目/);
  assert.match(badge.innerHTML, /href="\/static\/pages\/canvas\.html\?canvas_id=cnv-456&(?:amp;)?project_id=prj-proj-123"/);
  assert.match(badge.innerHTML, /回到画布/);
});
