import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';

const controllerSource = readFileSync('web/js/controllers/production-controller.js', 'utf8');

function response(body, status = 200) {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => body
  };
}

function createEnvironment({ projectId = 'proj-a', fetchHandler, alerts = [] } = {}) {
  const elements = {};
  const makeElement = (tag, id = '') => {
    const element = {
      tagName: tag.toUpperCase(),
      id,
      innerHTML: '',
      textContent: '',
      value: '',
      src: '',
      alt: '',
      style: {},
      dataset: {},
      children: [],
      parentElement: null,
      attributes: {},
      disabled: false,
      classList: {
        add() {},
        remove() {},
        toggle() {},
        contains() { return false; }
      },
      setAttribute(name, value) { this.attributes[name] = String(value); },
      getAttribute(name) { return this.attributes[name] ?? null; },
      removeAttribute(name) { delete this.attributes[name]; },
      contains() { return false; },
      querySelector() { return null; },
      querySelectorAll() { return []; },
      insertAdjacentHTML() {}
    };
    if (id) elements[id] = element;
    return element;
  };

  const monitor = makeElement('img', 'mainMonitorImage');
  const monitorFrame = makeElement('div', 'monitorFrame');
  monitor.parentElement = monitorFrame;
  monitorFrame.children.push(monitor);
  makeElement('span', 'currentProjectDisplayTitle');
  makeElement('h3', 'currentShotTitle');
  makeElement('textarea', 'shotPromptInput');
  makeElement('div', 'sceneCatalogList');
  makeElement('div', 'catalogEpisodePills');
  makeElement('span', 'catalogEpisodeTag');
  makeElement('div', 'shotTrackList');
  makeElement('div', 'contextBusContainer');
  makeElement('div', 'productionReviewPanel');
  const addSceneButton = makeElement('button');
  addSceneButton.setAttribute('aria-label', '新建场次');

  const nav = [makeElement('a'), makeElement('a'), makeElement('a')];
  nav[0].setAttribute('href', 'projects.html');
  nav[1].setAttribute('href', 'production.html');
  nav[2].setAttribute('href', 'storyboard.html?view=canvas');

  const busContent = makeElement('div');
  elements.contextBusContainer.children.push(busContent);

  const document = {
    readyState: 'loading',
    activeElement: null,
    getElementById(id) { return elements[id] || null; },
    querySelector(selector) {
      if (selector === '#contextBusContainer > .flex-1') return busContent;
      if (selector === 'button[aria-label="新建场次"]') return addSceneButton;
      if (selector === '#navPillsGroup a') return null;
      return null;
    },
    querySelectorAll(selector) {
      if (selector === '#navPillsGroup a') return nav;
      return [];
    },
    addEventListener() {}
  };

  const storage = new Map([['workspace_project_id', projectId]]);
  const localStorage = {
    getItem(key) { return storage.get(key) ?? null; },
    setItem(key, value) { storage.set(key, String(value)); },
    removeItem(key) { storage.delete(key); }
  };
  const window = {
    location: {
      search: projectId ? '?project_id=' + projectId : '',
      origin: 'http://127.0.0.1:2077',
      href: 'http://127.0.0.1:2077/pages/production.html' + (projectId ? '?project_id=' + projectId : '')
    },
    history: { pushState() {} },
    localStorage,
    document,
    URL,
    URLSearchParams,
    fetch: fetchHandler,
    alert(message) { alerts.push(String(message)); },
    lucide: { createIcons() {} }
  };
  window.window = window;
  return { window, elements, storage, nav, alerts };
}

function pipelineFor(projectId) {
  return {
    pipeline_id: 'ep-' + projectId,
    project_id: projectId,
    version: 1,
    production_context: { episodes: [] },
    shots: []
  };
}

test('C9-T2 静态约束：剩余入口不得创建本地伪造场次或镜头', () => {
  assert.doesNotMatch(controllerSource, /sc-\$\{Date\.now\(\)\}/);
  assert.doesNotMatch(controllerSource, /sh-\$\{Date\.now\(\)\}-1/);
  assert.match(controllerSource, /PROJECT_NAVIGATION_NOT_AVAILABLE/);
  assert.match(controllerSource, /SCENE_CREATE_NOT_INTEGRATED/);
});

test('C9-T2 项目前后切换消费真实项目列表并绑定 project_id', async () => {
  const calls = [];
  const env = createEnvironment({
    projectId: 'proj-a',
    fetchHandler: async url => {
      calls.push(url);
      if (url === '/api/asset-registry/projects?archived=false') {
        return response({ projects: [
          { project_id: 'proj-a', name: '项目 A' },
          { project_id: 'proj-b', name: '项目 B' }
        ] });
      }
      if (url === '/api/projects/proj-a' || url === '/api/projects/proj-b') {
        const id = url.endsWith('proj-a') ? 'proj-a' : 'proj-b';
        return response({ project: { project_id: id, name: '项目 ' + id.slice(-1).toUpperCase() } });
      }
      if (url === '/api/episode-pipelines?project_id=proj-a' || url === '/api/episode-pipelines?project_id=proj-b') {
        return response({ pipelines: [] });
      }
      throw new Error('unexpected request ' + url);
    }
  });

  const context = vm.createContext(env.window);
  vm.runInContext(controllerSource, context);
  const production = env.window.WorkbenchProduction;

  await production.initRouting();
  const switched = await production.nextProject();

  assert.equal(switched, 'proj-b');
  assert.equal(production.getState().projectId, 'proj-b');
  assert.ok(calls.includes('/api/asset-registry/projects?archived=false'));
  assert.equal(env.storage.get('workspace_project_id'), 'proj-b');
});

test('C9-T2 无项目上下文时切换与新建均明确阻塞且不写入伪造状态', async () => {
  const alerts = [];
  const env = createEnvironment({ projectId: '', alerts, fetchHandler: async () => response({ projects: [] }) });
  const context = vm.createContext(env.window);
  vm.runInContext(controllerSource, context);
  const production = env.window.WorkbenchProduction;

  await production.initRouting();
  const switched = await production.nextProject();
  const added = await production.addScene();

  assert.equal(switched, null);
  assert.equal(added, null);
  assert.equal(production.getState().projectId, '');
  assert.equal(production.getState().currentEpisode, null);
  assert.match(alerts.join('\n'), /PROJECT_CONTEXT_REQUIRED/);
  assert.match(alerts.join('\n'), /SCENE_CREATE_NOT_INTEGRATED/);
});

test('C9-T2 有真实上下文时新建场次不产生内存成功数据', async () => {
  const alerts = [];
  const env = createEnvironment({
    projectId: 'proj-a',
    alerts,
    fetchHandler: async () => response({ projects: [] })
  });
  const context = vm.createContext(env.window);
  vm.runInContext(controllerSource, context);
  const production = env.window.WorkbenchProduction;

  production.bindProductionContext({
    project: { project_id: 'proj-a', name: '项目 A' },
    pipeline: {
      pipeline_id: 'ep-proj-a',
      project_id: 'proj-a',
      version: 1,
      production_context: {
        episodes: [{
          id: 'EP01',
          code: 'EP01',
          name: '第一集',
          scenes: [{ id: 'SC01', code: 'SC01', name: '第一场', shots: [] }]
        }]
      },
      shots: []
    }
  });

  const before = production.getState().currentEpisode.scenes.length;
  const result = await production.addScene();

  assert.equal(result, null);
  assert.equal(production.getState().currentEpisode.scenes.length, before);
  assert.match(alerts.join('\n'), /SCENE_CREATE_NOT_INTEGRATED/);
});

