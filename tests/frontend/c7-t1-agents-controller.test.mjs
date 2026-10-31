/**
 * QIN-167 (C7-T1) 智能体控制器 (agents-controller.js) 文本最小闭环回归测试。
 * 验证：
 * 1. 当 URL 或本地存储提供 project_id 时，控制器解析并绑定项目上下文，
 *    在界面生成带有项目标识与“回到项目”导航的链接；
 * 2. 当服务端 GET /api/agent/config 返回配置就绪时，script-03 (剧本架构师)
 *    状态由 not_integrated 正确同步为 configured，并呈现服务端主力模型；
 * 3. 支持 WorkbenchAgents.setProjectId() 动态变更并同步回到项目入口。
 */
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import { createContext, runInContext } from 'node:vm';

const source = readFileSync(new URL('../../web/js/controllers/agents-controller.js', import.meta.url), 'utf8');

function createHarness({ search = '', chatConfig = {}, agentConfig = {} } = {}) {
  const elements = {};
  const getEl = id => {
    if (!elements[id]) {
      elements[id] = {
        id,
        value: '',
        innerHTML: '',
        textContent: '',
        style: {},
        classList: {
          contains(cls) { return Boolean(this[cls]); },
          add(cls) { this[cls] = true; },
          remove(cls) { delete this[cls]; }
        },
        dataset: {},
        setAttribute(k, v) { this[k] = String(v); },
        removeAttribute(k) { delete this[k]; },
        addEventListener() {},
        appendChild(child) {
          if (!this.children) this.children = [];
          this.children.push(child);
        },
        prepend(child) {
          if (!this.children) this.children = [];
          this.children.unshift(child);
        },
        remove() {},
        querySelectorAll() { return []; },
        querySelector() { return null; }
      };
    }
    return elements[id];
  };

  const storage = new Map();
  const localStorage = {
    getItem(k) { return storage.get(k) ?? null; },
    setItem(k, v) { storage.set(k, String(v)); },
    removeItem(k) { storage.delete(k); }
  };

  const location = {
    search,
    origin: 'http://localhost',
    pathname: '/static/pages/agents.html'
  };

  const fetchMock = async (url) => {
    if (url.includes('/api/chat/config')) {
      return {
        ok: true,
        status: 200,
        json: async () => ({
          configured: true,
          configuration_status: 'ready',
          default_provider_id: 'deepseek',
          providers: [
            { provider_id: 'deepseek', configured: true, models: ['deepseek-chat', 'deepseek-reasoner'] }
          ],
          ...chatConfig
        })
      };
    }
    if (url.includes('/api/agent/config')) {
      return {
        ok: true,
        status: 200,
        json: async () => ({
          ready: true,
          configured: true,
          providers: [{ provider_id: 'deepseek', models: ['deepseek-reasoner'] }],
          profiles: [
            { config_snapshot_id: 'snap-1', provider_id: 'deepseek', model: 'deepseek-reasoner', mode: 'approval' }
          ],
          ...agentConfig
        })
      };
    }
    return {
      ok: false,
      status: 404,
      json: async () => ({ detail: 'Not Found' })
    };
  };

  const windowObj = {
    location,
    localStorage,
    URLSearchParams: globalThis.URLSearchParams,
    fetch: fetchMock,
    addEventListener() {},
    removeEventListener() {},
    dispatchEvent() {},
    lucide: { createIcons() {} },
    AuraTraceBus: {
      history() { return []; },
      subscribe() { return () => {}; },
      publish() {}
    }
  };

  const docObj = {
    readyState: 'complete',
    getElementById: getEl,
    createElement: (tag) => ({
      tagName: tag.toUpperCase(),
      id: '',
      className: '',
      innerHTML: '',
      setAttribute(k, v) { this[k] = String(v); },
      removeAttribute(k) { delete this[k]; },
      addEventListener() {},
      remove() {}
    }),
    querySelectorAll: () => [],
    querySelector: () => null,
    activeElement: null
  };

  const context = createContext({
    window: windowObj,
    document: docObj,
    localStorage,
    location,
    fetch: fetchMock,
    URLSearchParams: globalThis.URLSearchParams,
    AbortSignal: { timeout: () => undefined },
    CustomEvent: class { constructor(type, detail) { this.type = type; this.detail = detail; } },
    alert() {},
    confirm() { return true; },
    setTimeout: (fn) => setTimeout(fn, 0),
    clearTimeout: () => {}
  });

  return { context, elements, storage, windowObj };
}

test('URL 带有 project_id 时绑定项目上下文并渲染回到项目链接', async () => {
  const { context, elements } = createHarness({ search: '?project_id=proj-test-123' });
  runInContext(source, context);

  // 等待微任务与异步 fetch
  await new Promise(r => setTimeout(r, 20));

  const WorkbenchAgents = context.window.WorkbenchAgents;
  assert.equal(WorkbenchAgents.getProjectId(), 'proj-test-123');

  const bar = elements['agentProjectContextBar'];
  assert.ok(bar, '应创建 agentProjectContextBar 容器');
  assert.match(bar.innerHTML, /回到项目/, '应包含“回到项目”文本');
  // QIN-493：项目中心是选择器，不携带 project_id；目标项目由 localStorage 传递。
  assert.match(bar.innerHTML, /href="\/static\/pages\/projects\.html"/, '应包含指向项目中心的导航超链接');
  assert.doesNotMatch(bar.innerHTML, /projects\.html\?project_id=/, '项目中心链接不得携带 project_id');
});

test('服务端 Agent 配置就绪时剧本架构师 script-03 状态为 configured', async () => {
  const { context } = createHarness({
    agentConfig: {
      ready: true,
      profiles: [
        { config_snapshot_id: 'snap-deepseek', provider_id: 'deepseek', model: 'deepseek-reasoner', mode: 'approval' }
      ]
    }
  });
  runInContext(source, context);

  await new Promise(r => setTimeout(r, 20));

  const WorkbenchAgents = context.window.WorkbenchAgents;
  const scriptAgent = WorkbenchAgents.getAgent('script-03');
  assert.ok(scriptAgent, '应存在 script-03 智能体');
  assert.equal(scriptAgent.status, 'configured', '剧本架构师状态应就绪同步为 configured');
  assert.match(scriptAgent.model, /deepseek-reasoner/, '剧本架构师模型应反映服务端 profile');
});

test('动态调用 setProjectId 更新上下文与回到项目链接', async () => {
  const { context, elements } = createHarness();
  runInContext(source, context);

  await new Promise(r => setTimeout(r, 20));

  const WorkbenchAgents = context.window.WorkbenchAgents;
  assert.equal(WorkbenchAgents.getProjectId(), null);

  WorkbenchAgents.setProjectId('proj-dynamic-888');
  assert.equal(WorkbenchAgents.getProjectId(), 'proj-dynamic-888');

  const bar = elements['agentProjectContextBar'];
  assert.ok(bar, '动态设置后应生成 bar');
  assert.match(bar.innerHTML, /proj-dynamic-888/, '链接应更新为新项目');
});

test('QIN-319: agents-controller 与 assets-controller 包含单属性 var() 兜底值', () => {
  const agentsSource = readFileSync(new URL('../../web/js/controllers/agents-controller.js', import.meta.url), 'utf8');
  const assetsSource = readFileSync(new URL('../../web/js/controllers/assets-controller.js', import.meta.url), 'utf8');

  // agents-controller: bar cssText 与 safeId span 的 style 必须包含 fallback 兜底值
  assert.match(agentsSource, /var\(--gw-type-body-sm,\s*12px\)/, 'agents-controller 应包含 --gw-type-body-sm 兜底值 12px');
  assert.match(agentsSource, /var\(--gw-leading-label-md,\s*16px\)/, 'agents-controller 应包含 --gw-leading-label-md 兜底值 16px');
  assert.match(agentsSource, /var\(--gw-type-label-md,\s*11px\)/, 'agents-controller 应包含 --gw-type-label-md 兜底值 11px');

  // assets-controller: status cssText 必须包含 fallback 兜底值
  assert.match(assetsSource, /var\(--gw-type-body-sm,\s*12px\)/, 'assets-controller 应包含 --gw-type-body-sm 兜底值 12px');
});
