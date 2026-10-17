import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';

const controller = readFileSync('web/js/controllers/storyboard-controller.js', 'utf8');

function response(payload, status = 200) {
  return { ok: status >= 200 && status < 300, status, json: async () => payload };
}

function createHarness(fetchImpl) {
  const elements = {};
  const makeElement = (tag, id = '') => {
    const element = {
      tagName: tag.toUpperCase(),
      id,
      innerHTML: '',
      textContent: '',
      value: '',
      dataset: {},
      style: {},
      parentElement: null,
      children: [],
      contains() { return false; },
      querySelector() { return null; },
      querySelectorAll() { return []; },
      setAttribute(name, value) { this[name] = String(value); },
      getAttribute(name) { return this[name] ?? null; },
      removeAttribute(name) { delete this[name]; },
      classList: { toggle() {}, add() {}, remove() {} }
    };
    if (id) elements[id] = element;
    return element;
  };

  for (const [tag, id] of [
    ['div', 'storyboardGrid'],
    ['div', 'storyboardSceneNav'],
    ['h2', 'storyboardTitle'],
    ['span', 'navCountBadge']
  ]) makeElement(tag, id);

  const document = {
    readyState: 'complete',
    getElementById(id) { return elements[id] || null; },
    querySelector(selector) {
      return selector === '#storyboardSceneNavContainer .font-mono.text-\\[\\#dfc384\\]'
        ? elements.navCountBadge : null;
    },
    querySelectorAll() { return []; },
    addEventListener() {}
  };
  const storage = new Map();
  const calls = [];
  const window = {
    location: {
      search: '?project_id=project-c8-0001&view=storyboard',
      origin: 'http://127.0.0.1:2077',
      href: 'http://127.0.0.1:2077/static/pages/storyboard.html?project_id=project-c8-0001&view=storyboard'
    },
    localStorage: {
      getItem(key) { return storage.get(key) ?? null; },
      setItem(key, value) { storage.set(key, String(value)); }
    },
    document,
    URL,
    URLSearchParams,
    fetch: async (url, options = {}) => {
      calls.push({ url, options });
      return fetchImpl(url, options);
    },
    alert() {},
    lucide: { createIcons() {} }
  };
  window.window = window;
  const context = vm.createContext(window);
  vm.runInContext(controller, context);
  return { storyboard: window.WorkbenchStoryboard, elements, calls };
}

function pipeline(version = 1) {
  return {
    pipeline_id: 'ep-c8-0001',
    project_id: 'project-c8-0001',
    version: 4,
    production_context: {
      episodes: [{ id: 'EP01', code: 'EP01', name: '第一集', scenes: [{ id: 'SC01', code: 'SC01', name: '主场景' }] }]
    },
    shots: [{
      shot_id: 'SC01_SH001', episode_id: 'EP01', scene_id: 'SC01', status: 'completed',
      job_id: 'job-c8-0001', asset_id: 'asset-c8-0001', version_id: `asset-c8-0001-v00${version}`,
      version, source: { provider: 'local_comfy', source_url: '/api/assets/asset-c8-0001/content' }
    }]
  };
}

test('QIN-176 静态契约消费真实流水线与精确审阅版本', () => {
  assert.match(controller, /\/api\/episode-pipelines/);
  assert.match(controller, /\/api\/asset-reviews\/sessions/);
  assert.match(controller, /version_id/);
  assert.match(controller, /expected_version/);
  assert.match(controller, /superseded/);
  assert.match(controller, /review_new_version/);
  assert.doesNotMatch(controller, /setTimeout\(\(\) => \{/);
});

test('QIN-176 分镜页读取版本审阅状态并以 CAS 退回当前版本', async () => {
  const calls = [];
  let expectedVersion = 1;
  const fetchImpl = async (url, options = {}) => {
    if (url.startsWith('/api/projects/')) return response({ project: { project_id: 'project-c8-0001', name: 'C8 真实项目' } });
    if (url.startsWith('/api/episode-pipelines?')) return response({ pipelines: [pipeline(1)] });
    if (url.startsWith('/api/asset-reviews/sessions?')) return response({
      sessions: [{ session_id: 'rs-c8-0001', review_id: 'rs-c8-0001', asset_id: 'asset-c8-0001',
        shot_id: 'SC01_SH001', version_id: 'asset-c8-0001-v001', version: 1,
        expected_version: expectedVersion, review_status: 'pending_review', next_action: 'review' }],
      revision: expectedVersion
    });
    if (url === '/api/asset-reviews/sessions/rs-c8-0001') return response({
      session: { session_id: 'rs-c8-0001', review_id: 'rs-c8-0001', asset_id: 'asset-c8-0001',
        shot_id: 'SC01_SH001', version_id: 'asset-c8-0001-v001', version: 1,
        expected_version: expectedVersion, review_status: 'pending_review', next_action: 'review' },
      comments: [], approvals: [], delivery: null, revision: expectedVersion
    });
    if (url.endsWith('/comments')) {
      const payload = JSON.parse(options.body);
      assert.equal(options.method, 'POST');
      assert.equal(payload.shot_id, 'SC01_SH001');
      assert.equal(payload.version_id, 'asset-c8-0001-v001');
      assert.equal(payload.version, 1);
      assert.equal(payload.expected_version, 1);
      expectedVersion = 2;
      return response({ comment: { comment_id: 'rc-c8-0001', expected_version: 2 } }, 201);
    }
    if (url.endsWith('/approval')) {
      const payload = JSON.parse(options.body);
      assert.equal(options.method, 'PUT');
      assert.equal(payload.decision, 'changes_requested');
      assert.equal(payload.shot_id, 'SC01_SH001');
      assert.equal(payload.version_id, 'asset-c8-0001-v001');
      assert.equal(payload.version, 1);
      assert.equal(payload.expected_version, 2);
      expectedVersion = 3;
      return response({ approval: { decision: 'changes_requested', review_status: 'changes_requested', next_action: 'regenerate_version' }, session: {
        session_id: 'rs-c8-0001', review_id: 'rs-c8-0001', asset_id: 'asset-c8-0001',
        shot_id: 'SC01_SH001', version_id: 'asset-c8-0001-v001', version: 1,
        expected_version: 3, review_status: 'changes_requested', next_action: 'regenerate_version'
      } });
    }
    throw new Error(`unexpected request ${url}`);
  };

  const { storyboard, elements } = createHarness(async (url, options) => {
    calls.push({ url, options });
    return fetchImpl(url, options);
  });
  await storyboard.ready;

  const state = storyboard.getState();
  assert.equal(state.pipelineId, 'ep-c8-0001');
  assert.equal(state.shots[0].version_id, 'asset-c8-0001-v001');
  assert.equal(state.reviews['SC01_SH001'].versionId, 'asset-c8-0001-v001');
  assert.match(elements.storyboardGrid.innerHTML, /data-shot-version-id="asset-c8-0001-v001"/);
  assert.match(elements.storyboardGrid.innerHTML, /待审阅/);

  const comment = await storyboard.addReviewComment('SC01_SH001', '保留构图');
  assert.equal(comment.comment_id, 'rc-c8-0001');
  assert.equal(storyboard.getState().reviews['SC01_SH001'].expectedVersion, 2);

  const result = await storyboard.submitReviewDecision('SC01_SH001', 'changes_requested', '调整光照');
  assert.equal(result.approval.decision, 'changes_requested');
  assert.equal(storyboard.getState().reviews['SC01_SH001'].nextAction, 'regenerate_version');
  assert.ok(calls.some(call => call.url.endsWith('/approval')));
});

test('QIN-176 新版本出现时旧批准不继续显示为当前批准', async () => {
  let currentVersion = 1;
  const { storyboard } = createHarness(async url => {
    if (url.startsWith('/api/projects/')) return response({ project: { project_id: 'project-c8-0001', name: 'C8 真实项目' } });
    if (url.startsWith('/api/episode-pipelines?')) return response({ pipelines: [pipeline(currentVersion)] });
    if (url.startsWith('/api/asset-reviews/sessions?')) return response({ sessions: currentVersion === 1 ? [{
      session_id: 'rs-c8-old', asset_id: 'asset-c8-0001', shot_id: 'SC01_SH001', version_id: 'asset-c8-0001-v001',
      version: 1, expected_version: 2, review_status: 'approved', next_action: 'deliver'
    }] : [{
      session_id: 'rs-c8-old', asset_id: 'asset-c8-0001', shot_id: 'SC01_SH001', version_id: 'asset-c8-0001-v001',
      version: 1, expected_version: 2, review_status: 'superseded', next_action: 'review_new_version'
    }], revision: 2 });
    if (url === '/api/asset-reviews/sessions/rs-c8-old') return response({ session: {
      session_id: 'rs-c8-old', asset_id: 'asset-c8-0001', shot_id: 'SC01_SH001', version_id: 'asset-c8-0001-v001',
      version: 1, expected_version: 2, review_status: currentVersion === 1 ? 'approved' : 'superseded', next_action: currentVersion === 1 ? 'deliver' : 'review_new_version'
    }, comments: [], approvals: [{ decision: 'approved' }], revision: 2 });
    throw new Error(`unexpected request ${url}`);
  });

  await storyboard.ready;
  assert.equal(storyboard.getState().reviews['SC01_SH001'].status, 'approved');
  currentVersion = 2;
  await storyboard.loadProjectContext('project-c8-0001');
  const review = storyboard.getState().reviews['SC01_SH001'];
  assert.equal(storyboard.getState().shots[0].version_id, 'asset-c8-0001-v002');
  assert.notEqual(review.versionId, 'asset-c8-0001-v001');
  assert.notEqual(review.status, 'approved');
  assert.equal(review.nextAction, 'review_new_version');
});
