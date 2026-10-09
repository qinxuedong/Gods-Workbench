import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, existsSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

const root = new URL('../../', import.meta.url);
const read = path => readFileSync(new URL(path, root), 'utf8');
const controller = read('web/js/controllers/workflow-controller.js');
const html = read('web/pages/workflow.html');
const css = read('web/css/pages/workflow-workbench.css');

test('工作流入口保留统一顶栏导航与资源路径', () => {
  for (const page of ['index', 'projects', 'workshop', 'production', 'storyboard', 'agents', 'assets', 'collab', 'settings', 'workflow']) {
    const source = read(`web/pages/${page}.html`);
    assert.match(source, /href="collab\.html"[\s\S]*?<\/a>\s*<a href="workflow\.html"/);
    assert.equal((source.match(/href="workflow\.html"/g) || []).length, 1);
  }
  for (const match of html.matchAll(/(?:src|href)="(\/static\/[^"?]+)/g)) assert.ok(existsSync(fileURLToPath(new URL(match[1].replace('/static/', 'web/'), root))));
  assert.doesNotMatch(html, /(?:src|href)="https?:\/\//);
  assert.match(html, /topbar-master-deck/);
  assert.match(html, /class="workflow-workbench"/);
});

test('页面提供成熟四区与完整成熟入口', () => {
  for (const id of ['workflowWorkbench', 'workflowSubDeck', 'capsuleRunway', 'leftBusSidebar', 'centerActuatorRack', 'envDiffDrawer', 'workflowTreeList', 'selectedNodeContainer', 'actuatorCardsContainer', 'canvasViewport', 'canvasNodesLayer', 'taskModal', 'assetLibraryModal', 'clipboardModal', 'contractModal']) assert.match(html, new RegExp(`id="${id}"`));
  for (const token of ['btnCloseAllWorkflows', 'btnExportApiJson', 'btnExportNodeList', 'btnOpenContractModal', 'btnNewFolder', 'btnRandomizeSeeds', 'btnRefreshDiff', 'btnAutoLayout', 'btnViewTaskDrawer', 'nodeMediaUploadInput', 'btnExecuteWorkflow']) assert.match(html, new RegExp(`id="${token}"`));
  assert.match(html, /class="workbench-main"/);
});

test('工作台 CSS 无页面级全屏规则且选择器已作用域化', () => {
  assert.doesNotMatch(css, /(^|[\n}])\s*(html|body|:root|\.workbench-shell)\s*\{/);
  assert.match(css, /\.workflow-workbench\s*\{/);
  assert.match(css, /\.workflow-workbench \.left-bus-sidebar/);
});

test('成熟控制器保留生命周期与工作流能力', () => {
  for (const token of ['function initWorkbench', 'function bindEvents', 'function disposeWorkbench', 'window.WorkbenchWorkflow', 'rebind:', 'dispose:']) assert.match(controller, new RegExp(token.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')));
  for (const token of ['capsuleRunway', 'folders', 'parse-link', 'parse-upload', 'assets/upload', 'canvas-contract', 'auto-layout', 'compare-local', 'nodeMediaUploadInput', 'btnRandomizeSeeds', 'btnViewTaskDrawer']) assert.match(controller, new RegExp(token));
  assert.doesNotMatch(controller, /2078/);
  assert.match(controller, /\/api\/god_workflow\/documents/);
});

test('控制器所有页面查询均从工作台根开始', () => {
  assert.doesNotMatch(controller, /document\.querySelector(All)?\(/);
  assert.match(controller, /function wfGet/);
  assert.match(controller, /function wfQueryAll/);
});

// 使用真实控制器的轮询函数，仅把网络、DOM 和时钟替换为离线夹具。
const { runInNewContext } = await import('node:vm');
function pollingHarness(response, { deferred = false } = {}) {
  const start = controller.indexOf('function startTaskPolling(taskId, target) {');
  const end = controller.indexOf('/* ==================== 7. 事件绑定', start);
  const body = { innerHTML: '' };
  const timers = new Map();
  const state = { pollTimer: null };
  let requests = 0;
  let resolveResponse;
  const pending = new Promise(resolve => { resolveResponse = resolve; });
  const context = {
    state, Set, String, Array, encodeURIComponent,
    wfGet: () => body,
    escapeHtml: value => String(value ?? '').replaceAll('<', '&lt;'),
    apiFetch: async () => { requests++; return deferred ? pending : response; },
    clearInterval: id => timers.delete(id), clearTimeout: id => timers.delete(id),
    setInterval: callback => { const id = timers.size + 1; timers.set(id, callback); return id; },
    setTimeout: callback => { const id = timers.size + 1; timers.set(id, callback); return id; },
  };
  runInNewContext(controller.slice(start, end), context);
  return { ...context, body, timers, requests: () => requests, finish: value => resolveResponse(value),
    flush: () => new Promise(resolve => setImmediate(resolve)) };
}
for (const status of ['completed', 'failed', 'cancelled', 'outcome_unknown']) {
  test(`轮询真实终态 ${status} 停止且显示状态`, async () => {
    const harness = pollingHarness({ status, outputs: [], messages: ['diagnostic'] });
    harness.startTaskPolling('job-1', 'rh_cloud');
    await harness.flush();
    assert.equal(harness.timers.size, 0);
    assert.match(harness.body.innerHTML, /job-1/);
    assert.match(harness.body.innerHTML, /diagnostic/);
  });
}
test('旧域终态仅适配为宿主状态，不继续轮询', async () => {
  for (const status of ['succeeded', 'canceled', 'interrupted']) {
    const harness = pollingHarness({ status, outputs: [] });
    harness.startTaskPolling('old-job', 'local_comfy');
    await harness.flush();
    assert.equal(harness.timers.size, 0, status);
  }
});
test('回收不完整不会显示完整交付成功或使用远端原始链接', async () => {
  const harness = pollingHarness({ status: 'completed', execution_status: 'completed', collection_status: 'partial_failed',
    outputs: [{ registered: false, fileUrl: 'https://external.test/secret.png', code: 'DOWNLOAD_FAILED' }] });
  harness.startTaskPolling('job-1', 'rh_cloud');
  await harness.flush();
  assert.match(harness.body.innerHTML, /回收/);
  assert.doesNotMatch(harness.body.innerHTML, /ready-ok|external\.test/);
});
test('轮询未完成时不重叠请求', async () => {
  const harness = pollingHarness({}, { deferred: true });
  harness.startTaskPolling('job-1', 'rh_cloud');
  for (const callback of harness.timers.values()) callback();
  assert.equal(harness.requests(), 1);
  harness.finish({ status: 'completed', outputs: [] });
  await harness.flush();
});
test('没有虚假自动启动宣称且dispose清理轮询', () => {
  assert.doesNotMatch(controller, /若未启动将自动后台静默拉起/);
  const dispose = controller.slice(controller.indexOf('function disposeWorkbench()'));
  assert.match(dispose, /clear(?:Interval|Timeout)\(state\.pollTimer\)/);
});

test('刷新恢复使用服务端主体任务列表而非本机共享任务ID', () => {
  assert.match(controller, /async function restoreWorkflowTask\(/);
  assert.match(controller, /\/api\/god_workflow\/tasks/);
  assert.match(controller.slice(controller.indexOf('async function initWorkbench')), /await restoreWorkflowTask\(\)/);
});

test('诊断响应不替换工作流且空态不宣称齐备', () => {
  assert.doesNotMatch(controller, /if \(\/\\\/compare-local[^\n]+normalizeMatureWorkflow/);
  const block = controller.slice(controller.indexOf('const refreshDiffHandler'), controller.indexOf('function nextWorkflowSeed'));
  assert.doesNotMatch(block, /setCurrentWorkflow/);
  assert.match(block, /env_diff/);
  const render = controller.slice(controller.indexOf('function renderEnvironmentDiff'), controller.indexOf('function renderActuatorRack'));
  assert.match(render, /status.*compared|compared.*status/);
});
test('全站顶栏 CF 控制默认关闭并经过真实后端确认', () => {
  const shell = read('web/js/core/workbench-shell.js');
  assert.match(shell, /data-gw-comfy-toggle/);
  assert.match(shell, /comfy-control/);
  assert.match(shell, /status === 'online' && data\.highlight === true/);
  assert.match(shell, /confirmation_token/);
  assert.match(shell, /setInterval\(pollComfyState, 5000\)/);
});

function comfyHarness(states, confirm = true) {
  const source = read('web/js/core/workbench-shell.js');
  const start = source.indexOf('  let comfyBusy = false;');
  const end = source.indexOf('  function init()', start);
  const requests = [];
  const classes = new Set();
  const toggle = {classList: {toggle(name, on) { on ? classes.add(name) : classes.delete(name); }},
    setAttribute(name, value) { this[name] = value; }, dataset: {}, addEventListener(name, fn) { this.handlers ??= {}; this.handlers[name] = fn; }, click() { if (!this.disabled) return this.handlers?.click?.(); }, disabled: false};
  const context = {document: {querySelectorAll: () => [toggle]}, window: {GWAuthGate: {isAuthenticated: () => true}, HardwareDeck: {authState:{principal:{role:'admin'}}}, confirm: () => confirm, showToast() {}},
    fetch: async (url, options) => { requests.push(options); return {ok: true, json: async () => states.shift()}; },
    setInterval() { return 1; }};
  context.window.addEventListener = () => {};
  runInNewContext(source.slice(start, end), context);
  return {...context, toggle, requests, classes};
}
test('CF 外部确认取消不发停止，确认后发送服务端令牌', async () => {
  const online = {ok: true, status:'online', highlight:true, managed:false, can_stop:true, confirmation_token:'token'};
  const refused = comfyHarness([online, online], false);
  await refused.comfyControl(refused.toggle);
  assert.equal(refused.requests.filter(r => r?.method === 'POST').length, 0);
  assert.ok(refused.classes.has('active'));
  const approved = comfyHarness([online, {ok:true,status:'offline',highlight:false}, {ok:true,status:'offline',highlight:false}]);
  await approved.comfyControl(approved.toggle);
  const body = JSON.parse(approved.requests.find(r => r?.method === 'POST').body);
  assert.equal(body.action, 'stop');
  assert.equal(body.confirmation_token, 'token');
  assert.ok(!approved.classes.has('active'));
});
test('CF 定期探测更新真实高亮，失败关闭但不冒充停止成功', async () => {
  const h = comfyHarness([{ok:true,status:'online',highlight:true}, {ok:true,status:'offline',highlight:false}]);
  await h.pollComfyState(); assert.ok(h.classes.has('active'));
  await h.pollComfyState(); assert.ok(!h.classes.has('active'));
  assert.equal(h.toggle.disabled, false);
});

test('共享与静态顶栏不再含 POST 控件，CF 固定原黄色', () => {
  const shell = read('web/js/core/workbench-shell.js');
  assert.doesNotMatch(shell, />POST<|POST 开关/);
  assert.match(shell, /text-\[#dfc384\].*?>CF<\/span>/);
  for (const page of ['index','projects','production','storyboard']) assert.doesNotMatch(read(`web/pages/${page}.html`), />POST<|>PRE</);
});
test('CF 认证未就绪不发送任何业务请求', async () => {
  const h = comfyHarness([]); h.window.GWAuthGate.isAuthenticated = () => false;
  await h.pollComfyState(); await h.comfyControl(h.toggle);
  assert.equal(h.requests.length, 0);
});
test('CF 拒绝关闭保留在线状态并有可见错误反馈', async () => {
  const online = {ok:true,status:'online',highlight:true,managed:true,can_stop:true};
  const h = comfyHarness([online,{...online,ok:false,code:'COMFY_PROCESS_IDENTITY_CHANGED'},online]);
  const notices = []; h.window.showToast = message => notices.push(message);
  await h.comfyControl(h.toggle);
  assert.ok(h.classes.has('active')); assert.ok(notices.some(message => message.includes('COMFY_PROCESS_IDENTITY_CHANGED')));
});
test('普通角色CF可点击解释权限且不发送生命周期POST',async()=>{
  const h=comfyHarness([{ok:true,status:'online',highlight:true}]);h.window.HardwareDeck.authState.principal.role='viewer';
  const notices=[];h.window.showToast=m=>notices.push(m);h.bindComfyControl();await new Promise(r=>setImmediate(r));
  assert.equal(h.toggle.disabled,false);await h.toggle.click();assert.ok(notices.some(m=>m.includes('管理员')));
  assert.equal(h.requests.filter(r=>r?.method==='POST').length,0);
});
test('工作台不含局部CF启动入口和擅加认证横条',()=>{
  assert.doesNotMatch(html,/id="btnOpenComfyUi"/);assert.doesNotMatch(controller,/data-wf-auth-notice|wfAuthNotice/);
});

// 解析错误路径执行真实 apiFetch/handleParseLink，不替换其错误映射。
function parseHarness(status, data) {
  const btn={disabled:false,innerHTML:'解析'}, banner={style:{},innerHTML:'',classList:{add(){},remove(){}}};
  const notices=[], requests=[];
  const h={window:{GWAuthGate:{isAuthenticated:()=>true}},state:{activeFolderId:'folder-real'},
    wfGet:id=>id==='btnParseLink'?btn:banner, recordClipboardHistory(){},
    fetch:async(url,options)=>{requests.push({url,options});return {ok:status===200,status,json:async()=>data};},
    targetApiUrl:u=>u,showToast:m=>notices.push(m),fetchClipboardSnapshot:async()=>({latestText:''}),
    isLikelyRhLink:()=>false,openClipboardModal:async()=>{},clearInterval(){},
    refreshWorkflowList:async()=>{},setCurrentWorkflow:w=>{h.loaded=w;},normalizeMatureWorkflow:w=>w};
  runInNewContext(controller.slice(controller.indexOf('async function apiFetch('),controller.indexOf('async function initWorkbench(')),h);
  runInNewContext(controller.slice(controller.indexOf('async function handleParseLink('),controller.indexOf('async function handleUploadFile(')),h);
  return {...h,btn,notices,requests};
}
test('parse-link 502真实错误显示并释放按钮，不改已加载工作流',async()=>{
  const h=parseHarness(502,{detail:{code:'RH_DETAIL_FETCH_FAILED',message:'RunningHub 网络不可达，请检查网络或上传原始 JSON'}});
  await h.handleParseLink(' https://www.runninghub.cn/workflow/2104915887789797378 ');
  assert.equal(h.requests[0].url,'/api/god_workflow/parse-link');
  assert.deepEqual(JSON.parse(h.requests[0].options.body),{url_or_text:'https://www.runninghub.cn/workflow/2104915887789797378',folder_id:'folder-real'});
  assert.match(h.notices[0],/网络不可达/);assert.equal(h.btn.disabled,false);assert.equal(h.loaded,undefined);
});
test('空剪贴板不发送parse-link，不伪造解析',async()=>{
  const h=parseHarness(502,{});await h.handleParseLink();assert.equal(h.requests.length,0);assert.match(h.notices[0],/剪贴板/);
});
test('解析toast显示规则不能隐藏错误反馈',()=>{
  const rule=css.match(/\.workflow-workbench \.toast-banner\.show\s*\{([^}]+)\}/)[1];
  assert.doesNotMatch(rule,/display:\s*none/);assert.match(rule,/display:\s*flex/);
  assert.match(css,/\.toast-banner[\s\S]*?position:\s*fixed/);
});
test('admin CF真实DOM事件绑定一次，替换顶栏后可再绑定',async()=>{
  const offline={ok:true,status:'offline',highlight:false};
  const h=comfyHarness([offline,offline,{ok:true,status:'online',highlight:true},offline]);
  h.bindComfyControl();await new Promise(r=>setImmediate(r));await h.toggle.click();
  assert.equal(h.requests.filter(r=>r?.method==='POST').length,1);
  assert.equal(JSON.parse(h.requests.find(r=>r?.method==='POST').body).action,'start');
  const old=h.toggle;const next={...old,dataset:{},handlers:{},disabled:false};
  h.document.querySelectorAll=()=>[next];h.bindComfyControl();assert.equal(typeof next.handlers.click,'function');
});
test('CF匿名DOM点击显示登录反馈且不发业务请求',async()=>{
  const h=comfyHarness([]);h.window.GWAuthGate.isAuthenticated=()=>false;let login=0;
  h.window.HardwareDeck.openAccountModal=()=>login++;const notices=[];h.window.showToast=m=>notices.push(m);
  h.bindComfyControl();await h.toggle.click();assert.equal(login,1);assert.equal(h.requests.length,0);assert.match(notices[0],/登录/);
});
test('关闭overlay不拦截顶栏命中，CF无pointer-events:none',()=>{
  const base=read('web/css/base/hardware-design-system.css');
  assert.match(base,/\.hw-modal-backdrop\s*\{[^}]*pointer-events:\s*none/s);
  assert.match(base,/\.hw-modal-backdrop\.open\s*\{[^}]*pointer-events:\s*auto/s);
  assert.doesNotMatch(base,/\[data-gw-comfy-toggle\]\s*\{[^}]*pointer-events:\s*none/s);
});
