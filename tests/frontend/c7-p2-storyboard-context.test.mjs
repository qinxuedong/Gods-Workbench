import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';

const controller = readFileSync('web/js/controllers/storyboard-controller.js', 'utf8');

function response(payload, status = 200) {
  return { ok: status >= 200 && status < 300, status, json: async () => payload };
}

function createHarness({ search = '', storedProjectId = '', fetchImpl = async () => response({}) } = {}) {
  const elements = {};
  const makeElement = (tag, id) => {
    const el = {
      tagName: tag.toUpperCase(), id: id || '', innerHTML: '', textContent: '',
      dataset: {}, style: {}, contains: () => false, querySelectorAll: () => [],
      querySelector: () => null, setAttribute: () => {}, removeAttribute: () => {},
      classList: { toggle: () => {} }
    };
    if (id) elements[id] = el;
    return el;
  };
  for (const [tag, id] of [['div', 'storyboardGrid'], ['div', 'storyboardSceneNav'], ['h2', 'storyboardTitle'], ['span', 'navCountBadge']]) {
    makeElement(tag, id);
  }
  const doc = {
    readyState: 'complete',
    getElementById: id => elements[id] || null,
    querySelector: selector => selector === '#storyboardSceneNavContainer .font-mono.text-\\[\\#dfc384\\]' ? elements.navCountBadge : null,
    querySelectorAll: () => [],
    addEventListener: () => {}
  };
  const calls = [];
  const mockWindow = {
    location: { search, origin: 'http://localhost', href: `http://localhost/storyboard.html${search}` },
    localStorage: { getItem: () => storedProjectId, setItem: () => {} },
    document: doc,
    URLSearchParams: globalThis.URLSearchParams,
    URL,
    fetch: async (...args) => { calls.push(args[0]); return fetchImpl(...args); },
    lucide: { createIcons: () => {} },
    alert: () => {}
  };
  mockWindow.window = mockWindow;
  const context = vm.createContext({ ...mockWindow, fetch: mockWindow.fetch });
  vm.runInContext(controller, context);
  return { storyboard: mockWindow.WorkbenchStoryboard, elements, calls };
}

test('分镜页优先从 URL 解析 project_id，并消费项目资产为真实分镜', async () => {
  const { storyboard, elements, calls } = createHarness({
    search: '?project_id=proj-real-001',
    fetchImpl: async url => url.startsWith('/api/projects/')
      ? response({ project: { project_id: 'proj-real-001', name: '真实项目' } })
      : response({ assets: [{
        asset_id: 'asset-real-001', name: '真实镜头资产', kind: 'image',
        metadata: { source_context: { scene_id: 'scene-real-001' }, prompt: '真实提示词' }
      }] })
  });

  await storyboard.ready;

  assert.deepEqual(calls, ['/api/projects/proj-real-001', '/api/asset-registry/assets?project_id=proj-real-001&limit=500']);
  assert.equal(storyboard.getState().projectId, 'proj-real-001');
  assert.equal(storyboard.getState().shots[0].asset_id, 'asset-real-001');
  assert.equal(storyboard.getState().shots[0].scene_id, 'scene-real-001');
  assert.match(elements.storyboardTitle.textContent, /真实项目/);
  assert.match(elements.storyboardGrid.innerHTML, /真实镜头资产/);
  assert.match(elements.storyboardGrid.innerHTML, /data-asset-id="asset-real-001"/);
  assert.doesNotMatch(elements.storyboardGrid.innerHTML, /data-gw-degradation="not_integrated"/);
  assert.match(elements.storyboardSceneNav.innerHTML, /scene-real-001/);
});

test('分镜页上下文缺失或读取失败时保持明确未接入空态且不发起伪造请求', async () => {
  const missing = createHarness();
  await missing.storyboard.ready;
  assert.deepEqual(missing.calls, []);
  assert.match(missing.elements.storyboardGrid.innerHTML, /分镜数据未接入/);
  assert.match(missing.elements.storyboardTitle.textContent, /共 0 个分镜/);

  const failed = createHarness({
    search: '?project_id=proj-forbidden',
    fetchImpl: async () => response({ detail: { code: 'PROJECT_NOT_FOUND' } }, 404)
  });
  await failed.storyboard.ready;
  assert.deepEqual(Array.from(failed.storyboard.getState().shots), []);
  assert.match(failed.elements.storyboardGrid.innerHTML, /分镜数据未接入/);
  assert.match(failed.elements.storyboardSceneNav.innerHTML, /场次数据未接入/);
});

test('分镜页在 URL 缺失时回退到已保存的 project_id', async () => {
  const { storyboard, calls } = createHarness({
    storedProjectId: 'proj-from-storage',
    fetchImpl: async url => url.startsWith('/api/projects/')
      ? response({ project: { project_id: 'proj-from-storage', name: '缓存项目' } })
      : response({ assets: [] })
  });

  await storyboard.ready;

  assert.deepEqual(calls, [
    '/api/projects/proj-from-storage',
    '/api/asset-registry/assets?project_id=proj-from-storage&limit=500'
  ]);
  assert.equal(storyboard.getState().projectId, 'proj-from-storage');
});

test('分镜页不把文档资产当作图片预览', async () => {
  const { storyboard, elements } = createHarness({
    search: '?project_id=proj-document-only',
    fetchImpl: async url => url.startsWith('/api/projects/')
      ? response({ project: { project_id: 'proj-document-only', name: '文档项目' } })
      : response({ assets: [{asset_id: 'doc-001', name: '文本产物', kind: 'document'}] })
  });

  await storyboard.ready;

  assert.equal(storyboard.getState().shots[0].imageUrl, '');
  assert.match(elements.storyboardGrid.innerHTML, /媒体预览未接入/);
  assert.doesNotMatch(elements.storyboardGrid.innerHTML, /<img /);
});

test('分镜页不在本地追加未持久化的假镜头', async () => {
  const { storyboard } = createHarness();
  storyboard.setShots([{ id: 'asset-real-001', name: '真实镜头', asset_id: 'asset-real-001' }]);
  const before = storyboard.getState().shots.length;
  storyboard.addNewShot();
  assert.equal(storyboard.getState().shots.length, before);
});
