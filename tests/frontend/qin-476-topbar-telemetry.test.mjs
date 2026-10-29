import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import path from 'node:path';
import vm from 'node:vm';
import { fileURLToPath } from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const source = readFileSync(path.join(root, 'web/js/core/hardware-telemetry.js'), 'utf8');

function makeNode(textContent = '') {
  const attributes = new Map();
  const listeners = new Map();
  return {
    textContent,
    className: '',
    title: '',
    dataset: {},
    style: {},
    attributes,
    listeners,
    parentElement: { querySelectorAll: () => [] },
    addEventListener(type, callback) { listeners.set(type, callback); },
    setAttribute(name, value) { attributes.set(name, String(value)); },
    removeAttribute(name) { attributes.delete(name); },
    hasAttribute(name) { return attributes.has(name); },
    getAttribute(name) { return attributes.get(name) ?? null; }
  };
}

function makeTopbar() {
  return new Map([
    ['masterDeckClock', makeNode('00:00:00')],
    ['masterDeckDate', makeNode('--')],
    ['masterDeckDateMonth', makeNode('---')],
    ['masterDeckDateDay', makeNode('--')],
    ['gwOnlineStatus', makeNode('探测中…')],
    ['svgNeedleCPU', makeNode()],
    ['cpuValText', makeNode('未接入')],
    ['svgNeedleRAM', makeNode()],
    ['ramValText', makeNode('未接入')]
  ]);
}

function createHarness(fetchImpl = async () => { throw new Error('fetch unavailable'); }) {
  let nodes = makeTopbar();
  let nextTimerId = 1;
  const intervals = new Map();
  const clearedIntervals = [];
  const windowListeners = new Map();
  const documentListeners = new Map();
  const animationFrames = [];
  const document = {
    readyState: 'loading',
    activeElement: null,
    addEventListener(type, callback) { documentListeners.set(type, callback); },
    getElementById(id) { return nodes.get(id) ?? null; },
    querySelector() { return null; },
    querySelectorAll() { return []; }
  };
  const window = {
    addEventListener(type, callback) { windowListeners.set(type, callback); },
    dispatchEvent() { return true; },
    FloatingDismissal: { scan() {} }
  };
  const context = {
    window,
    document,
    fetch: fetchImpl,
    AbortSignal: { timeout: timeoutMs => ({ timeoutMs }) },
    setInterval(callback, delay) {
      const id = nextTimerId++;
      intervals.set(id, { callback, delay });
      return id;
    },
    clearInterval(id) {
      clearedIntervals.push(id);
      intervals.delete(id);
    },
    requestAnimationFrame(callback) {
      animationFrames.push(callback);
      return animationFrames.length;
    }
  };
  vm.runInNewContext(source, context, { filename: 'hardware-telemetry.js' });
  return {
    window,
    document,
    intervals,
    clearedIntervals,
    windowListeners,
    documentListeners,
    animationFrames,
    setNodes(value) { nodes = value; }
  };
}

// SPA 路由替换顶栏节点后，应绑定新日期控件并让唯一时钟 timer 更新新节点。
{
  const harness = createHarness();
  const { window, intervals, clearedIntervals, windowListeners } = harness;
  window.HardwareDeck.initClock();
  const firstTimerId = [...intervals.keys()][0];
  assert.equal(intervals.get(firstTimerId).delay, 1000);

  const nextTopbar = makeTopbar();
  harness.setNodes(nextTopbar);
  await windowListeners.get('gw:route-loaded')();

  assert.ok(clearedIntervals.includes(firstTimerId), '路由切换应清除旧时钟 timer');
  assert.equal(intervals.size, 1, '路由切换后只保留一个时钟 timer');
  assert.notEqual(nextTopbar.get('masterDeckClock').textContent, '00:00:00');
  assert.notEqual(nextTopbar.get('masterDeckDateMonth').textContent, '---');
  assert.notEqual(nextTopbar.get('masterDeckDateDay').textContent, '--');
  assert.equal(typeof nextTopbar.get('masterDeckDate').listeners.get('click'), 'function');
}

// 未认证时不发送受保护探针请求，并明确呈现状态，不得残留“探测中…”。
{
  let fetchCount = 0;
  const harness = createHarness(async () => {
    fetchCount += 1;
    throw new Error('不应请求');
  });
  Object.assign(harness.window.GWAuthGate, { loaded: true, authenticated: false });

  await harness.windowListeners.get('gw:route-loaded')();

  assert.equal(fetchCount, 0);
  assert.equal(harness.document.getElementById('gwOnlineStatus').textContent, '未认证');
  assert.equal(harness.document.getElementById('cpuValText').textContent, '未认证');
  assert.equal(harness.document.getElementById('ramValText').textContent, '未认证');
}

// 探针拒绝/超时后，状态和 CPU/RAM 都进入准确的暂不可用提示。
{
  const requests = [];
  const harness = createHarness(async (url, options) => {
    requests.push({ url, options });
    throw new Error('probe timeout');
  });
  Object.assign(harness.window.GWAuthGate, { loaded: true, authenticated: true });

  await harness.window.HardwareDeck.syncOnlineStatus();
  await harness.window.HardwareDeck.syncTelemetry();

  assert.equal(requests.length, 2);
  assert.ok(requests.every(request => request.url === '/api/observability/health'));
  assert.ok(requests.every(request => request.options.signal.timeoutMs === 15000));
  assert.equal(harness.document.getElementById('gwOnlineStatus').textContent, '暂不可用');
  assert.equal(harness.document.getElementById('cpuValText').textContent, '暂不可用');
  assert.equal(harness.document.getElementById('ramValText').textContent, '暂不可用');
}

// HTTP 降级按真实响应区分 404、503 与 403。
for (const [status, label] of [[404, '未接入'], [503, '暂不可用'], [403, '权限不足']]) {
  const harness = createHarness(async () => ({
    ok: false,
    status,
    json: async () => ({ detail: { code: status === 403 ? 'FORBIDDEN' : 'UNAVAILABLE' } })
  }));
  Object.assign(harness.window.GWAuthGate, { loaded: true, authenticated: true });

  await harness.window.HardwareDeck.syncOnlineStatus();
  await harness.window.HardwareDeck.syncTelemetry();

  assert.equal(harness.document.getElementById('gwOnlineStatus').textContent, label, `health HTTP ${status}`);
  assert.equal(harness.document.getElementById('cpuValText').textContent, label, `CPU HTTP ${status}`);
  assert.equal(harness.document.getElementById('ramValText').textContent, label, `RAM HTTP ${status}`);
}

// 已认证且探针返回真实读数时，路由后的新顶栏显示接入状态并继续驱动 CPU/RAM 仪表。
{
  const health = {
    status: 'ok',
    data_status: 'ok',
    checks: [
      { name: 'hardware_telemetry', status: 'ok', metrics: { cpu_percent: 42, memory_percent: 37, disk_percent: 55 } },
      { name: 'gpu_telemetry', status: 'ok', metrics: { gpu_utilization_percent: 45, gpu_memory_percent: 32, devices: [] } }
    ]
  };
  const harness = createHarness(async () => ({ ok: true, status: 200, json: async () => health }));
  Object.assign(harness.window.GWAuthGate, { loaded: true, authenticated: true });
  const deck = harness.window.HardwareDeck;
  deck.initVUMeters();
  const nextTopbar = makeTopbar();
  harness.setNodes(nextTopbar);

  await harness.windowListeners.get('gw:route-loaded')();
  harness.animationFrames.shift()();

  assert.equal(nextTopbar.get('gwOnlineStatus').textContent, '已接入');
  assert.equal(nextTopbar.get('cpuValText').textContent, '5%');
  assert.equal(nextTopbar.get('ramValText').textContent, '4%');
}

// VU 动画每帧读取当前 DOM，页面替换后仍写入新仪表，而非已脱离文档的旧节点。
{
  const harness = createHarness();
  const deck = harness.window.HardwareDeck;
  deck.initVUMeters();
  const animate = harness.animationFrames.shift();
  const nextTopbar = makeTopbar();
  harness.setNodes(nextTopbar);
  deck.vuState.integrated = true;
  deck.vuState.cpu.target = 50;
  deck.vuState.ram.target = 25;

  animate();

  assert.notEqual(nextTopbar.get('svgNeedleCPU').getAttribute('transform'), null);
  assert.notEqual(nextTopbar.get('svgNeedleRAM').getAttribute('transform'), null);
  assert.equal(nextTopbar.get('cpuValText').textContent, '6%');
  assert.equal(nextTopbar.get('ramValText').textContent, '3%');
}

console.log('QIN-476 topbar telemetry lifecycle contracts passed');
