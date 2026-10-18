// C10-T3：资产索引后台状态与页面降级必须使用同一映射。
import {readFileSync} from 'node:fs';
import {createContext, runInContext} from 'node:vm';
import assert from 'node:assert/strict';
import test from 'node:test';

const source = readFileSync(new URL('../../web/js/controllers/assets-controller.js', import.meta.url), 'utf8');

function harness(elements = {}) {
  const context = createContext({
    window: {addEventListener() {}, location: {search: ''}},
    document: {
      getElementById: id => elements[id] || null,
      body: {appendChild() {}},
      createElement: () => ({}),
    },
    MutationObserver: class { observe() {} disconnect() {} },
    CSS: {escape: value => value},
    fetch: async () => ({ok: true, status: 200, json: async () => ({assets: [], total: 0})}),
    console,
  });
  context.window.window = context.window;
  runInContext(source, context);
  return {context, assets: context.window.WorkbenchAssets, status: context.window.GWAssetJobStatus};
}

test('C10-T3 后台索引状态映射显式区分完成、失败和未知', () => {
  const {status} = harness();
  assert.ok(status, '必须暴露资产后台任务状态映射');
  assert.deepEqual({...status.view({status: 'queued'})}, {
    status: 'queued', kind: 'pending', dataStatus: 'degraded',
    label: '索引排队中', nextAction: 'poll_job', resultUnknown: false,
    errorCode: '', message: '索引排队中',
  });
  assert.equal(status.view({status: 'running'}).nextAction, 'poll_job');
  assert.equal(status.view({status: 'paused'}).nextAction, 'resume_index');
  assert.equal(status.view({status: 'succeeded'}).dataStatus, 'ok');
  assert.equal(status.view({status: 'failed', error_code: 'INDEX_ROOT_UNAVAILABLE'}).nextAction, 'retry_index');
  assert.equal(status.view({status: 'interrupted'}).resultUnknown, true);
  assert.equal(status.view({status: 'mystery'}).nextAction, 'reconcile_index_job');
});

test('C10-T3 页面状态条带结构化降级和下一步动作', () => {
  const statusNode = {textContent: '', dataset: {}, setAttribute(name, value) { this[name] = value; }, removeAttribute(name) { delete this[name]; }};
  const {assets, status} = harness({assetVaultStatus: statusNode});
  const view = assets.showIndexJobStatus({status: 'failed', error_code: 'INDEX_SOURCE_NOT_AVAILABLE'});
  assert.equal(view.nextAction, 'retry_index');
  assert.equal(statusNode.textContent, '索引失败：INDEX_SOURCE_NOT_AVAILABLE');
  assert.equal(statusNode['data-gw-degradation'], 'degraded');
  assert.equal(statusNode.dataset.nextAction, 'retry_index');
  assert.equal(status.render({status: 'interrupted', error_code: 'INDEX_PROCESS_INTERRUPTED'}).includes('data-gw-degradation="unknown"'), true);
  assert.match(status.render({status: 'interrupted'}), /下一步：人工核对索引任务/);
});
