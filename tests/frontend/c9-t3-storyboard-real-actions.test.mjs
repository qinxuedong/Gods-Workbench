import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';

const controller = readFileSync('web/js/controllers/storyboard-controller.js', 'utf8');

function response(payload, status = 200) {
  return { ok: status >= 200 && status < 300, status, json: async () => payload };
}

function createHarness({ search = '', storedProjectId = '', fetchImpl = async () => response({}), alerts = [] } = {}) {
  const elements = {};
  const makeElement = (tag, id) => {
    const element = {
      tagName: tag.toUpperCase(), id: id || '', innerHTML: '', textContent: '', value: '',
      dataset: {}, style: {}, parentElement: null,
      contains: () => false, querySelector: () => null, querySelectorAll: () => [],
      setAttribute(name, value) { this.dataset[name] = String(value); },
      getAttribute(name) { return this.dataset[name] ?? null; },
      removeAttribute(name) { delete this.dataset[name]; },
      classList: { toggle() {}, add() {}, remove() {} }
    };
    if (id) elements[id] = element;
    return element;
  };

  for (const [tag, id] of [
    ['div', 'storyboardGrid'], ['div', 'storyboardSceneNav'], ['h2', 'storyboardTitle'],
    ['span', 'navCountBadge']
  ]) makeElement(tag, id);

  const document = {
    readyState: 'complete',
    activeElement: null,
    getElementById(id) { return elements[id] || null; },
    querySelector(selector) {
      return selector === '#storyboardSceneNavContainer .font-mono.text-\\[\\#dfc384\\]'
        ? elements.navCountBadge : null;
    },
    querySelectorAll() { return []; },
    addEventListener() {}
  };
  const storage = new Map(storedProjectId ? [['workspace_project_id', storedProjectId]] : []);
  const window = {
    location: { search, origin: 'http://127.0.0.1:2077' },
    localStorage: {
      getItem(key) { return storage.get(key) ?? null; },
      setItem(key, value) { storage.set(key, String(value)); }
    },
    document,
    URL,
    URLSearchParams,
    fetch: async (...args) => fetchImpl(...args),
    alert(message) { alerts.push(String(message)); },
    lucide: { createIcons() {} }
  };
  window.window = window;
  const context = vm.createContext(window);
  vm.runInContext(controller, context);
  return { storyboard: window.WorkbenchStoryboard, elements, alerts, storage };
}

test('C9-T3 静态约束：不伪造就绪状态，批量渲染和 PDF 明确受限', () => {
  assert.doesNotMatch(controller, /已就绪/);
  assert.match(controller, /BATCH_RENDER_OUT_OF_SCOPE/);
  assert.match(controller, /PDF_EXPORT_NOT_INTEGRATED/);
  assert.match(controller, /closure_status/);
  assert.match(controller, /blocker/);
  assert.match(controller, /next_action/);
  assert.doesNotMatch(controller, /Date\.now\(\)/);
});

test('C9-T3 读取真实场次和镜头状态，不从 shot_id 伪造 asset_id', async () => {
  const { storyboard, elements } = createHarness({
    search: '?project_id=proj-c9&view=storyboard',
    fetchImpl: async url => {
      if (url === '/api/projects/proj-c9') return response({ project: { project_id: 'proj-c9', name: 'C9 实际项目' } });
      if (url === '/api/episode-pipelines?project_id=proj-c9') return response({ pipelines: [{
        pipeline_id: 'pipe-c9', project_id: 'proj-c9', version: 4,
        production_context: { episodes: [{ id: 'EP01', code: 'EP01', name: '第一集', scenes: [
          { id: 'SC01', code: 'SC01', name: '真实场次', shots: [] }
        ] }] },
        shots: [{ shot_id: 'SC01_SH001', episode_id: 'EP01', scene_id: 'SC01', status: 'queued',
          next_action: 'poll_job', version: 2, data_gaps: ['PROVIDER_PENDING'] }]
      }] });
      if (url.startsWith('/api/asset-reviews/sessions?')) return response({ sessions: [], revision: 1 });
      throw new Error(`unexpected request ${url}`);
    }
  });

  await storyboard.ready;
  const state = storyboard.getState();
  assert.equal(state.pipelineId, 'pipe-c9');
  assert.equal(state.pipelineVersion, 4);
  assert.equal(state.shots.length, 1);
  assert.equal(state.shots[0].shot_id, 'SC01_SH001');
  assert.equal(state.shots[0].asset_id, '');
  assert.equal(state.shots[0].status, 'queued');
  assert.equal(state.shots[0].next_action, 'poll_job');
  assert.equal(state.scenes[0].id, 'SC01');
  assert.match(elements.storyboardSceneNav.innerHTML, /真实场次/);
  assert.match(elements.storyboardGrid.innerHTML, /排队中/);
  assert.match(elements.storyboardGrid.innerHTML, /读取任务状态/);
  assert.doesNotMatch(elements.storyboardGrid.innerHTML, /已就绪/);
});

test('C9-T3 重绘、追加、批量和 PDF 不写入本地伪造状态', () => {
  const alerts = [];
  const { storyboard, elements } = createHarness({ alerts });
  storyboard.setShots([{ shot_id: 'SC01_SH001', name: '真实镜头', status: 'completed', version: 1 }]);
  const before = storyboard.getState().shots;

  assert.equal(storyboard.regenerateShot(0), null);
  assert.equal(storyboard.addNewShot(), null);
  assert.equal(storyboard.batchGenerate(), null);
  assert.equal(storyboard.exportPdf(), null);

  assert.deepEqual(storyboard.getState().shots, before);
  assert.match(alerts.join('\n'), /RENDER_NOT_INTEGRATED|SHOT_APPEND_NOT_INTEGRATED/);
  assert.match(alerts.join('\n'), /BATCH_RENDER_OUT_OF_SCOPE/);
  assert.match(alerts.join('\n'), /PDF_EXPORT_NOT_INTEGRATED/);
  assert.match(elements.storyboardGrid.innerHTML, /data-gw-degradation="blocked"/);
});
