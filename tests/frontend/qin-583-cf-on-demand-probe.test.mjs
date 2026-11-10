/**
 * QIN-583（GitHub #677）：CF 停止自动探测，改为按需探测。
 *
 * 验收标准：
 * 1. 进页不探：进入任意带工作台顶栏的页面，加载与刷新时不得请求 GET /api/god_workflow/comfy-control，也不得启动定时轮询。
 * 2. 未触发探测前，顶栏 CF 开关展示「未检测」，不得显示为在线或离线。
 * 3. 用户打开启动开关后才开始探测/启动；启动成功或失败后，若无进行中的生成任务，必须停止探测。
 * 4. 发起需要本机 CF 的生成任务、以及任务仍在跑需要轮询结果时，允许探测；任务结束或失败后停止。
 * 5. 需要探测时快速失败，不阻塞 API 工作线程。
 * 6. 测试登记 node-files.txt 与洁净室分片。
 */
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, existsSync } from 'node:fs';
import { runInNewContext } from 'node:vm';

const root = new URL('../../', import.meta.url);
const read = path => readFileSync(new URL(path, root), 'utf8');
const shellSource = read('web/js/core/workbench-shell.js');
const controllerSource = read('web/js/controllers/workflow-controller.js');
const nodeFiles = read('tests/frontend/node-files.txt');

function createShellHarness(states = []) {
  const start = shellSource.indexOf('  let comfyBusy = false;');
  const end = shellSource.indexOf('  function init()', start);
  const requests = [];
  const timers = new Map();
  const classes = new Set();
  const attributes = {};
  const toggle = {
    classList: {
      toggle(name, on) { on ? classes.add(name) : classes.delete(name); },
      contains(name) { return classes.has(name); },
      add(name) { classes.add(name); },
      remove(name) { classes.delete(name); }
    },
    setAttribute(name, value) { attributes[name] = String(value); },
    getAttribute(name) { return attributes[name] ?? null; },
    dataset: {},
    addEventListener(name, fn) {
      this.handlers ??= {};
      this.handlers[name] = fn;
    },
    click() {
      if (!this.disabled) return this.handlers?.click?.();
    },
    disabled: false,
    title: ''
  };

  const listeners = new Map();
  const context = {
    document: {
      querySelectorAll: selector => (selector.includes('data-gw-comfy-toggle') ? [toggle] : [])
    },
    window: {
      GWAuthGate: { isAuthenticated: () => true, revision: 1 },
      HardwareDeck: { authState: { principal: { role: 'admin' } } },
      confirm: () => true,
      showToast: () => {},
      addEventListener: (type, fn) => {
        if (!listeners.has(type)) listeners.set(type, []);
        listeners.get(type).push(fn);
      },
      dispatchEvent: event => {
        const type = event?.type || event;
        for (const fn of listeners.get(type) || []) fn(event);
      }
    },
    fetch: async (url, options) => {
      requests.push({ url, options });
      const next = states.shift() || { ok: true, status: 'offline', highlight: false };
      const httpOk = typeof next.http_ok === 'boolean'
        ? next.http_ok
        : (next.status_code ? next.status_code >= 200 && next.status_code < 300 : true);
      return {
        ok: httpOk,
        status: next.status_code || 200,
        json: async () => next
      };
    },
    setInterval: (fn, ms) => {
      const id = timers.size + 1;
      timers.set(id, { fn, ms });
      return id;
    },
    clearInterval: id => {
      timers.delete(id);
    },
    setTimeout: (fn, ms) => {
      const id = timers.size + 1000;
      timers.set(id, { fn, ms });
      return id;
    },
    clearTimeout: id => {
      timers.delete(id);
    },
    Set,
    String,
    Error,
    Object
  };

  runInNewContext(shellSource.slice(start, end), context);
  return { ...context, toggle, requests, timers, classes, attributes, listeners };
}

test('AC1 & AC2: 进页外壳初始化不请求 /comfy-control，不启动轮询定时器，状态呈现「未检测」', () => {
  const h = createShellHarness([{ ok: true, status: 'online', highlight: true }]);
  // 执行绑定
  h.bindComfyControl();

  // 断言未发出任何 fetch 请求
  assert.equal(h.requests.length, 0, '进页绑定时不应发送网络请求');
  // 断言无轮询定时器运行
  assert.equal(h.timers.size, 0, '进页绑定时不应启动 setInterval 定时器');
  // 断言状态为 untested 且标题明确注明未检测
  assert.equal(h.toggle.dataset.gwComfyStatus, 'untested', '未探测前状态应为 untested');
  assert.match(h.toggle.title, /未检测/, '未探测前 title 应包含“未检测”');
  assert.equal(h.classes.has('active'), false, '未探测前开关不应为 active');
  assert.equal(h.attributes['aria-pressed'], 'false');
});

test('AC2: 冷加载匿名进入显示未检测登录提示，gw-auth-state 后管理员切换为「CF 未检测；点击后台启动」', () => {
  let authenticated = false;
  let userRole = 'anonymous';
  const h = createShellHarness();
  h.window.GWAuthGate.isAuthenticated = () => authenticated;
  h.window.HardwareDeck.authState.principal = { role: userRole };
  h.bindComfyControl();

  // 冷加载未认证状态
  assert.equal(h.toggle.dataset.gwComfyStatus, 'untested');
  assert.match(h.toggle.title, /未检测（请先登录后读取\/控制 CF/);

  // 认证就绪为管理员，触发 gw-auth-state
  authenticated = true;
  userRole = 'admin';
  h.window.HardwareDeck.authState.principal = { role: 'admin' };
  for (const handler of h.listeners.get('gw-auth-state') || []) {
    handler();
  }

  // 认证更新后，title 必须被重渲染并切换为「CF 未检测；点击后台启动」
  assert.equal(h.toggle.dataset.gwComfyStatus, 'untested');
  assert.equal(h.toggle.title, 'CF 未检测；点击后台启动');
});

test('AC3: 用户点击启动开关才开始探测/启动，启动中轮询，启动成功且无任务后自动停止轮询', async () => {
  const offline = { ok: true, status: 'offline', highlight: false };
  const starting = { ok: true, status: 'starting', highlight: false, managed: true };
  const online = { ok: true, status: 'online', highlight: true, managed: true };

  // 点击前读取返回 offline -> 发送 POST start 返回 starting -> finally 轮询 probe 返回 starting
  // -> 第一轮定时器轮询 probe 仍为 starting -> 第二轮定时器轮询 probe 返回 online
  const h = createShellHarness([offline, starting, starting, starting, online]);
  h.bindComfyControl();
  assert.equal(h.requests.length, 0);

  // 用户点击开关触发启动
  await h.toggle.click();

  // 此时应当已经发送了读状态请求与 POST start 请求
  assert.ok(h.requests.length >= 2, '点击后应发起状态读取与控制请求');
  const postReq = h.requests.find(r => r.options?.method === 'POST');
  assert.ok(postReq, '应发送 POST 控制请求');
  assert.match(postReq.options.body, /"action":"start"/);

  // 处于 starting 期间，应有轮询定时器
  assert.ok(h.timers.size > 0, 'starting 状态下应激活轮询定时器');

  // 触发定时器轮询：下一轮 probe 依然 starting
  for (const timer of [...h.timers.values()]) {
    await timer.fn();
  }
  assert.ok(h.timers.size > 0, '仍为 starting 时定时器保持');

  // 再次触发定时器轮询：此时返回 online（启动成功）
  for (const timer of [...h.timers.values()]) {
    await timer.fn();
  }

  // 启动成功后，由于无进行中的生成任务，定时器必须停止
  assert.equal(h.timers.size, 0, '启动成功且无进行中任务时必须停止轮询定时器');
  assert.ok(h.classes.has('active'), '就绪后开关应高亮');
  assert.equal(h.toggle.dataset.gwComfyStatus, 'online');
});

test('AC3: 启动失败后立即停止探测轮询', async () => {
  const offline = { ok: true, status: 'offline', highlight: false };
  const starting = { ok: true, status: 'starting', highlight: false, managed: true };
  const failed = {
    ok: true,
    status: 'offline',
    highlight: false,
    code: 'COMFY_START_FAILED',
    exit_code: 1,
    message: 'ComfyUI 启动进程已退出（退出码 1）'
  };

  // 1: readComfyState -> offline
  // 2: POST /comfy-control -> starting
  // 3: finally pollComfyState -> starting
  // 4: timer.fn() pollComfyState -> failed
  const h = createShellHarness([offline, starting, starting, failed]);
  h.bindComfyControl();

  await h.toggle.click();
  assert.ok(h.timers.size > 0, 'starting 中定时器应激活');

  // 执行轮询得到 failed
  for (const timer of [...h.timers.values()]) {
    await timer.fn();
  }

  // 失败后定时器被清理
  assert.equal(h.timers.size, 0, '启动失败后必须停止轮询定时器');
  assert.match(h.toggle.title, /启动失败|退出码 1/);
});

test('AC4: 发起本机生成任务时激活探测，任务结束后停止探测', async () => {
  const online = { ok: true, status: 'online', highlight: true };
  const h = createShellHarness([online, online]);
  h.bindComfyControl();
  assert.equal(h.timers.size, 0);

  // 发起本机 ComfyUI 生成任务
  await h.window.WorkbenchComfy.beginTask('task-test-001');
  assert.ok(h.timers.size > 0, '任务进行中应激活轮询定时器');
  assert.ok(h.requests.length > 0, '任务激活时应执行探测');

  // 任务完成，注销任务
  h.window.WorkbenchComfy.endTask('task-test-001');
  assert.equal(h.timers.size, 0, '任务结束后必须注销定时器并停止轮询');
});

test('AC4: 工作流控制器 startTaskPolling 联动 Comfy 生命周期', () => {
  // 静态断言控制器对 WorkbenchComfy 的调用契约
  assert.match(controllerSource, /window\.WorkbenchComfy\?\.beginTask\?\.?\(taskId\)/);
  assert.match(controllerSource, /window\.WorkbenchComfy\?\.endTask\?\.?\(taskId\)/);
  assert.match(controllerSource, /isComfyTarget = Boolean\(target && target !== "rh_cloud"\)/);
  // 断言非 online 状态（含初始 untested）均能触发 start()
  assert.match(controllerSource, /window\.WorkbenchComfy\?\.getStatus\(\)\s*!==\s*['"]online['"]/);
});

test('AC6: 未配置服务地址时 title 正确展示为未配置提示，不残留「未运行」', async () => {
  const unconfigured = { ok: false, status: 'unconfigured', code: 'COMFY_HOST_NOT_CONFIGURED', message: '未配置服务地址' };
  const h = createShellHarness([unconfigured, unconfigured, unconfigured]);
  h.bindComfyControl();

  // 初始为 untested
  assert.equal(h.toggle.dataset.gwComfyStatus, 'untested');

  // 点击触发按需探测，返回未配置
  await h.toggle.click();

  assert.equal(h.toggle.dataset.gwComfyStatus, 'unconfigured');
  assert.equal(h.toggle.title, 'CF 未配置服务地址，点击在设置中配置');
  assert.doesNotMatch(h.toggle.title, /未运行/);
});

test('AC6: node-files.txt 与洁净室准入分片已登记', () => {
  assert.ok(
    nodeFiles.includes('tests/frontend/qin-583-cf-on-demand-probe.test.mjs'),
    'node-files.txt 必须包含本测试文件'
  );
  assert.ok(
    existsSync(new URL('../hygiene/fixtures/admissions/issue-677.json', import.meta.url)),
    '必须存在 issue-677.json 洁净室准入分片'
  );
});
