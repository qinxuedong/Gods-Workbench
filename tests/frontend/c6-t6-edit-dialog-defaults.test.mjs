/**
 * QIN-163 (C6-T6) 编辑对话框 scene/shot 默认 0 回归测试。
 * 验证：
 * 1. 当项目具有真实数值时（如 scenes: 12, shots: 48），编辑对话框展示真实数值而非无条件重置为 0；
 * 2. 当项目 scene/shot 为 0 时，编辑对话框正确展示 0；
 * 3. 当项目 scene/shot 缺失或为 null 时，编辑对话框默认回填数值 0；
 * 4. 提交保存项目时，scene/shot 缺失或空值默认按 0 提交，有效数值如实提交。
 */
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import { createContext, runInContext } from 'node:vm';

const source = readFileSync(new URL('../../web/js/controllers/projects-controller.js', import.meta.url), 'utf8');

function createHarness(projects = []) {
  const elements = {};
  const getEl = id => {
    if (!elements[id]) {
      elements[id] = { id, value: '', innerHTML: '', textContent: '', style: {} };
    }
    return elements[id];
  };

  const requests = [];
  const toasts = [];

  const context = createContext({
    window: {
      location: { search: '' },
      addEventListener() {},
      GWProjectDateRange: { setRange() {} }
    },
    document: {
      getElementById: getEl,
      querySelector: () => null,
      querySelectorAll: () => [],
      body: { appendChild() {} }
    },
    HardwareDeck: {
      openModal() {},
      closeModal() {}
    },
    localStorage: {
      getItem: () => null,
      setItem: () => {}
    },
    console: {
      log() {},
      warn() {},
      error() {}
    },
    fetch: async (url, options = {}) => {
      requests.push({ url, options });
      return {
        ok: true,
        status: 200,
        json: async () => ({ project: { project_id: 'test', version: 2 } })
      };
    }
  });

  context.window.window = context.window;
  context.window.document = context.document;
  context.window.HardwareDeck = context.HardwareDeck;

  runInContext(source, context);

  const ctrl = context.window.WorkbenchProjects;

  // 注入初始项目数据用于测试
  if (projects.length > 0) {
    // 调用内部 load 或是通过 rebind / mock 设置 state
    // projects-controller 闭包内的 state.projects 可通过 mock fetch 初始化
  }

  return { ctrl, context, getEl, requests, toasts };
}

test('源码规则核查：openEditProject 中 scene/shot 缺失时默认回填 0 且保留真实数值', () => {
  // 必须保证缺失时为 0 而不是 ''
  assert.match(
    source,
    /scenesInput\.value\s*=\s*rawNumber\(p\.scenes\)\s*===\s*null\s*\?\s*0\s*:\s*rawNumber\(p\.scenes\)/,
    'scenesInput 应在缺失时回填 0 且有效值时保留真实值'
  );
  assert.match(
    source,
    /shotsInput\.value\s*=\s*rawNumber\(p\.shots\)\s*===\s*null\s*\?\s*0\s*:\s*rawNumber\(p\.shots\)/,
    'shotsInput 应在缺失时回填 0 且有效值时保留真实值'
  );
});

test('源码规则核查：handleUpdateProject 提交时 scene/shot 缺失/空值默认回填 0', () => {
  assert.match(
    source,
    /scenes:\s*Number\.isFinite\(parseInt\(scenesInput\?\.value,\s*10\)\)\s*\?\s*parseInt\(scenesInput\?\.value,\s*10\)\s*:\s*0/,
    'handleUpdateProject 应在 scenes 为空时默认提交 0'
  );
  assert.match(
    source,
    /shots:\s*Number\.isFinite\(parseInt\(shotsInput\?\.value,\s*10\)\)\s*\?\s*parseInt\(shotsInput\?\.value,\s*10\)\s*:\s*0/,
    'handleUpdateProject 应在 shots 为空时默认提交 0'
  );
});

test('DOM 行为：编辑已有项目时保留真实 scene/shot 数值，缺失时默认 0', async () => {
  let projectList = [
    { id: 'p-real', name: '真实分镜项目', scenes: 12, shots: 48, version: 1 },
    { id: 'p-zero', name: '零分镜项目', scenes: 0, shots: 0, version: 1 },
    { id: 'p-none', name: '未设分镜项目', scenes: null, shots: undefined, version: 1 }
  ];

  const elements = {};
  const getEl = id => {
    if (!elements[id]) {
      elements[id] = { id, value: '', innerHTML: '', textContent: '', style: {}, appendChild() {} };
    }
    return elements[id];
  };

  const requests = [];
  const context = createContext({
    URLSearchParams,
    URL,
    location: { search: '' },
    setTimeout,
    clearTimeout,
    window: {
      location: { search: '' },
      addEventListener() {},
      GWProjectDateRange: { setRange() {} }
    },
    document: {
      getElementById: getEl,
      querySelector: () => null,
      querySelectorAll: () => [],
      createElement: tag => ({
        tagName: tag,
        className: '',
        style: {},
        classList: { add() {}, remove() {} },
        appendChild() {},
        remove() {}
      }),
      body: { appendChild() {} }
    },
    HardwareDeck: {
      openModal() {},
      closeModal() {}
    },
    requestAnimationFrame: fn => fn(),
    localStorage: {
      getItem: () => null,
      setItem: () => {}
    },
    console: {
      log() {},
      warn() {},
      error() {}
    },
    fetch: async (url, options = {}) => {
      requests.push({ url, options });
      if (url.includes('deleted=true')) {
        return { ok: true, status: 200, json: async () => ({ total: 0, projects: [] }) };
      }
      if (url.includes('archived=true')) {
        return { ok: true, status: 200, json: async () => ({ total: 0, projects: [] }) };
      }
      if (url.includes('/api/asset-registry/projects')) {
        return { ok: true, status: 200, json: async () => ({ total: projectList.length, projects: projectList }) };
      }
      return { ok: true, status: 200, json: async () => ({}) };
    }
  });

  context.window.window = context.window;
  context.window.document = context.document;
  context.window.HardwareDeck = context.HardwareDeck;

  runInContext(source, context);
  const ctrl = context.window.WorkbenchProjects;

  // 加载数据
  await ctrl.load();

  // 1. 项目具有真实有效值时，展示真实数值而非 0
  ctrl.openEditProject('p-real');
  assert.equal(elements['editProjectScenesInput'].value, 12, 'p-real scenes 应保持真实值 12');
  assert.equal(elements['editProjectShotsInput'].value, 48, 'p-real shots 应保持真实值 48');

  // 2. 项目数值本身为 0 时，展示 0
  ctrl.openEditProject('p-zero');
  assert.equal(elements['editProjectScenesInput'].value, 0, 'p-zero scenes 应为 0');
  assert.equal(elements['editProjectShotsInput'].value, 0, 'p-zero shots 应为 0');

  // 3. 项目数值缺失/null/undefined 时，默认回填 0
  ctrl.openEditProject('p-none');
  assert.equal(elements['editProjectScenesInput'].value, 0, 'p-none scenes 缺失时应默认 0');
  assert.equal(elements['editProjectShotsInput'].value, 0, 'p-none shots 缺失时应默认 0');

  // 4. 提交测试：输入为空或未输入时，payload 默认提交 0
  elements['editProjectId'].value = 'p-none';
  elements['editProjectVersion'].value = '1';
  elements['editProjectNameInput'].value = '未设分镜项目';
  elements['editProjectScenesInput'].value = '';
  elements['editProjectShotsInput'].value = '';

  const patchRequests = [];
  context.fetch = async (url, options = {}) => {
    patchRequests.push({ url, options });
    return { ok: true, status: 200, json: async () => ({ project: { project_id: 'p-none', version: 2 } }) };
  };

  const fakeEvent = { preventDefault() {} };
  await ctrl.handleUpdateProject(fakeEvent);

  const actualPatch = patchRequests.filter(r => r.options?.method === 'PATCH');
  assert.equal(actualPatch.length, 1);
  const sentPayload = JSON.parse(actualPatch[0].options.body);
  assert.equal(sentPayload.scenes, 0, '提交时空的 scenes 应默认为 0');
  assert.equal(sentPayload.shots, 0, '提交时空的 shots 应默认为 0');
});
