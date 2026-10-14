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
    wfGet: () => body, markUnknownTaskSeen: () => {},
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

test('CF启动中只显示等待，再次点击取消且从不发送工作流', async () => {
  const offline = {ok:true,status:'offline',highlight:false};
  const pending = {ok:true,status:'starting',highlight:false,managed:true,can_stop:true};
  const h = comfyHarness([offline,pending,pending]);
  const notices=[]; h.window.showToast=message=>notices.push(message);
  await h.comfyControl(h.toggle);
  assert.ok(notices.some(message=>message.includes('正在启动')));
  assert.ok(!notices.some(message=>message==='CF 已确认运行'));
  assert.ok(!h.classes.has('active'));
  assert.match(h.toggle.title,/取消启动/);
});

test('CF启动中点击发送停止而不是再次启动', async () => {
  const pending = {ok:true,status:'starting',highlight:false,managed:true,can_stop:true};
  const offline = {ok:true,status:'offline',highlight:false};
  const h = comfyHarness([pending,offline,offline]);
  await h.comfyControl(h.toggle);
  const body=JSON.parse(h.requests.find(request=>request?.method==='POST').body);
  assert.equal(body.action,'stop');
  assert.ok(!h.classes.has('active'));
});

test('CF慢启动后退出在下一次轮询显示原因且同一失败只提示一次', async () => {
  const pending={ok:true,status:'starting',highlight:false,managed:true};
  const failed={ok:true,status:'offline',highlight:false,code:'COMFY_START_FAILED',exit_code:1,
    message:'ComfyUI 启动进程已退出（退出码 1），请检查本地启动环境'};
  const h=comfyHarness([pending,failed,failed,pending,failed]);
  const notices=[];h.window.showToast=message=>notices.push(message);
  await h.pollComfyState();await h.pollComfyState();await h.pollComfyState();
  assert.deepEqual(notices,[failed.message]);
  assert.match(h.toggle.title,/退出码 1/);
  assert.ok(!h.classes.has('active'));
  await h.pollComfyState();await h.pollComfyState();
  assert.equal(notices.length,2);
});

// 解析错误路径执行真实 apiFetch/handleParseLink，不替换其错误映射。
function parseHarness(status, data) {
  const btn={disabled:false,innerHTML:'解析'}, banner={style:{},innerHTML:'',classList:{add(){},remove(){}}};
  const notices=[], requests=[];
  const h={window:{GWAuthGate:{isAuthenticated:()=>true}},state:{activeFolderId:'folder-real'},
    wfGet:id=>id==='btnParseLink'?btn:banner, recordClipboardHistory(){},
    fetch:async(url,options)=>{requests.push({url,options});return {ok:status===200,status,json:async()=>data};},
    targetApiUrl:u=>u,showToast:m=>notices.push(m),fetchClipboardSnapshot:async()=>({latestText:''}),
    isLikelyRhLink:()=>false,readClipboardWorkflowImage:async()=>null,openClipboardModal:async()=>{},clearInterval(){},
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
test('剪贴板PNG原字节直接交给统一上传解析，不发送链接解析',async()=>{
  const h=parseHarness(200,{});let uploaded;
  // 函数闭包在VM内，设置通过同一对象引用进入其全局。
  const image={name:'clipboard-workflow.png'};
  const context={state:{},fetchClipboardSnapshot:async()=>({latestText:'https://www.runninghub.cn/workflow/old',browserText:''}),readClipboardWorkflowImage:async()=>image,
    handleUploadFile:async file=>{uploaded=file;},recordClipboardHistory(){},isLikelyRhLink:()=>true,showToast(){}};
  runInNewContext(controller.slice(controller.indexOf('async function handleParseLink('),controller.indexOf('async function handleUploadFile(')),context);
  await context.handleParseLink();assert.equal(uploaded,image);assert.equal(h.requests.length,0);
});
test('剪贴板图片读取保持Blob原字节且不接受JPEG视觉推测',async()=>{
  const context={navigator:{clipboard:{read:async()=>[{types:['image/png'],getType:async()=>new Blob(['metadata-original'])}]}},File};
  runInNewContext(controller.slice(controller.indexOf('async function readClipboardWorkflowImage('),controller.indexOf('async function handleParseLink(')),context);
  const image=await context.readClipboardWorkflowImage();assert.equal(image.name,'clipboard-workflow.png');assert.equal(await image.text(),'metadata-original');
  context.navigator.clipboard.read=async()=>[{types:['image/jpeg']}];assert.equal(await context.readClipboardWorkflowImage(),null);
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

function importHarness({existing = false, blocked = false, fail = false, refreshDeferred = false} = {}) {
  const events = [], notices = [], timers = [];
  let finishRefresh;
  const pendingRefresh = new Promise(resolve => {finishRefresh=resolve;});
  const loaded=[];
  const native = {closed:false, location:{href:existing?'http://127.0.0.1:8199/':'about:blank'},
    focus(){}, close(){this.closed=true;}};
  const btn = {disabled:false, innerHTML:'导入'};
  const h = {URL, crypto:{randomUUID:()=> 'single-nonce'}, Date,
    state:{currentWorkflow:{workflow_id:'real-id',name:'原生图'},authPrincipalKey:'owner',comfyWindowRef:existing?native:null},
    window:{GWAuthGate:{isAuthenticated:()=>true},location:{origin:'http://127.0.0.1:2077'},
      open(url){events.push(['open',url]);return blocked?null:native;}},
    wfGet:()=>btn, showToast:(...args)=>notices.push(args),
    flushWorkflowParams:async()=>{events.push(['flush']);if(fail) throw Error('保存失败');},
    apiFetch:async url=>{events.push(['api',url]);if(url.includes('/item/')) return refreshDeferred?pendingRefresh:{...h.state.currentWorkflow};return {open_url:'http://127.0.0.1:8199/',expected_version:1,workflow_graph:{version:0.4}};},
    normalizeMatureWorkflow:w=>w,setCurrentWorkflow(w){loaded.push(w);h.state.currentWorkflow=w;},
    clearTimeout(){},setTimeout(fn){timers.push(fn);return timers.length;}};
  runInNewContext(controller.slice(controller.indexOf('async function handleOpenInComfyUi('),controller.indexOf('async function checkComfySavedSyncOnce(')),h);
  return {...h,events,notices,timers,native,btn,loaded,finishRefresh};
}

for (const changed of ['principal', 'selection']) {
  test(`导入刷新期间切换${changed}不会回填旧私有图`,async()=>{
    const h=importHarness({refreshDeferred:true});const pending=h.handleOpenInComfyUi();
    while(!h.events.some(event=>String(event[1]).includes('/item/'))) await new Promise(resolve=>setImmediate(resolve));
    if(changed==='principal') {h.state.authPrincipalKey='other';h.state.comfyBridgeSession=null;h.state.currentWorkflow=null;}
    else h.state.currentWorkflow={workflow_id:'other-selected'};
    h.finishRefresh({workflow_id:'real-id',name:'old-private'});await pending;
    assert.equal(h.loaded.length,0);assert.equal(h.state.currentWorkflow?.workflow_id,changed==='selection'?'other-selected':undefined);
  });
}

test('宿主异步编译期间编辑会拒绝图与执行缓存错配',async()=>{
  const adapter=read('tools/comfy_workbench_bridge/js/workbench.js');
  let graph={nodes:[{id:1,widgets_values:['old']}],links:[]};let finish;
  const context={structuredClone,JSON,Object,app:{graph:{serialize:()=>structuredClone(graph),_nodes:[]},graphToPrompt:()=>new Promise(resolve=>{finish=resolve;})}};
  runInNewContext(adapter.slice(adapter.indexOf('const parseGraph ='),adapter.indexOf('const listener =')).replace('const parseGraph =','globalThis.parseGraph ='),context);
  const pending=context.parseGraph();graph.nodes[0].widgets_values[0]='new';
  finish({output:{'1':{class_type:'Node',inputs:{value:'old'}}}});
  await assert.rejects(pending,/编译期间工作流已改变/);
});

test('导入在首个异步保存前预留窗口，先会话后导航，等待真实确认',async()=>{
  const h=importHarness();const pending=h.handleOpenInComfyUi();
  assert.deepEqual(h.events.slice(0,2),[['open',''],['flush']]);
  await pending;
  assert.equal(h.state.comfyBridgeSession.window,h.native);
  assert.match(h.native.location.href,/gw_nonce=single-nonce/);
  assert.equal(h.state.comfyBridgeSession.connected,undefined);
  h.timers[0]();assert.match(h.notices.at(-1)[0],/未收到.*载入回执/);
  assert.equal(h.btn.disabled,false);
});

test('导入失败仅关闭预留空页，保留已有宿主图',async()=>{
  const fresh=importHarness({fail:true});await fresh.handleOpenInComfyUi();assert.equal(fresh.native.closed,true);
  const existing=importHarness({existing:true,fail:true});await existing.handleOpenInComfyUi();
  assert.equal(existing.native.closed,false);assert.equal(existing.native.location.href,'http://127.0.0.1:8199/');
  assert.equal(existing.events.filter(e=>e[0]==='open').length,0);
});

test('窗口拦截显示操作入口，登出后不能用旧入口导入',async()=>{
  const h=importHarness({blocked:true});await h.handleOpenInComfyUi();
  const toast=h.notices.at(-1);assert.match(toast[0],/浏览器阻止/);assert.equal(typeof toast[3],'function');
  h.window.GWAuthGate.isAuthenticated=()=>false;toast[3]();
  assert.equal(h.events.filter(e=>e[0]==='open').length,1);
  h.timers.forEach(fn=>fn());assert.equal(h.notices.at(-1),toast);
});

test('已确认或已换主体的导入不显示迟到超时',async()=>{
  const h=importHarness();await h.handleOpenInComfyUi();const count=h.notices.length;
  h.state.comfyBridgeSession.connected=true;h.timers[0]();assert.equal(h.notices.length,count);
  h.state.comfyBridgeSession.connected=false;h.state.authPrincipalKey='another';h.timers[0]();assert.equal(h.notices.length,count);
});

test('未登录导入按钮拦截反馈，认证后留给业务事件处理',()=>{
  let stopped=0,login=0;const notices=[];const button={};
  const h={document:{getElementById:()=>({contains:b=>b===button})},
    window:{GWAuthGate:{isAuthenticated:()=>false},HardwareDeck:{openAccountModal:()=>login++}},
    showToast:m=>notices.push(m)};
  runInNewContext(controller.slice(controller.indexOf('function handleUnauthenticatedComfyImport('),controller.indexOf("document.addEventListener('click', handleUnauthenticatedComfyImport")),h);
  const event={target:{closest:()=>button},stopImmediatePropagation:()=>stopped++};
  h.handleUnauthenticatedComfyImport(event);assert.equal(stopped,1);assert.equal(login,1);assert.match(notices[0],/登录/);
  h.window.GWAuthGate.isAuthenticated=()=>true;h.handleUnauthenticatedComfyImport(event);assert.equal(stopped,1);
});

for (const scenario of ['offline', 'internal-ready', 'last-resort']) {
  test(`定义补齐 ${scenario} 只在内部仍不足时请求页面，不自动启动`,async()=>{
    const requests=[];let opened=0,started=0,hostFallback=0;
    const h={state:{authPrincipalKey:'principal',currentWorkflow:{workflow_id:'workflow',version:1}},
      window:{GWAuthGate:{isAuthenticated:()=>true},open:()=>{opened++;return {};},WorkbenchComfy:{start:()=>started++}},
      showToast(){},flushWorkflowParams:async()=>{},normalizeMatureWorkflow:w=>w,
      setCurrentWorkflow:w=>{h.state.currentWorkflow=w;},handleOpenInComfyUi:async()=>hostFallback++,
      apiFetch:async(url,options)=>{requests.push({url,options});return url.endsWith('comfy-control')
        ? {highlight:scenario!=='offline'} : {workflow:{workflow_id:'workflow',version:2},needs_definition_resolution:scenario==='last-resort'};}};
    runInNewContext(controller.slice(controller.indexOf('async function offerDefinitionResolution('),controller.indexOf('async function handleOpenInComfyUi(')),h);
    await h.offerDefinitionResolution({workflow_id:'workflow'});
    assert.equal(opened,0);assert.equal(started,0);assert.equal(hostFallback,scenario==='last-resort'?1:0);
    assert.equal(requests.length,scenario==='offline'?1:2);
    if(scenario==='offline') assert.ok(h.state.pendingDefinitionWorkflow);
    else assert.equal(h.state.pendingDefinitionWorkflow,null);
  });
}

// C4-AC15：结果未知任务同一错误最多自动弹出一次，之后只显示徽标。
function restoreHarness(tasks, { search = '', storage = new Map(), workflowId = 'wf-1', respond } = {}) {
  const start = controller.indexOf('const UNKNOWN_TASK_SEEN_PREFIX');
  const end = controller.indexOf('function applySavedSettings', start);
  const modal = { opened: 0, classList: { add() { modal.opened++; } } };
  const polled = [];
  const urls = [];
  const localStorage = { getItem: k => storage.get(k) ?? null, setItem: (k, v) => storage.set(k, String(v)) };
  const rows = tasks.map(task => ({ workflow_id: 'wf-1', ...task }));
  const context = {
    state: { currentWorkflow: workflowId ? { workflow_id: workflowId } : null }, Array, URLSearchParams, Object, Boolean, String, Set,
    window: { location: { search }, localStorage },
    apiFetch: async url => { urls.push(url); return respond ? respond(url, rows) : { tasks: rows }; },
    wfGet: id => id === 'taskModal' ? modal : null,
    setWorkflowTaskTrigger() {}, showToast() {}, renderWorkflowTaskList() {},
    startTaskPolling: id => polled.push(id),
  };
  runInNewContext(controller.slice(start, end), context);
  return { ...context, modal, polled, storage, urls };
}
test('结果未知任务首次恢复弹出一次，再次进入视图不再自动弹出', async () => {
  const tasks = [{ job_id: 'job-u', status: 'outcome_unknown', error_code: 'RH_OUTCOME_UNKNOWN', target: 'rh_cloud' }];
  const storage = new Map();
  const first = restoreHarness(tasks, { storage });
  await first.restoreWorkflowTask();
  assert.equal(first.modal.opened, 1);
  for (let i = 0; i < 3; i++) {
    const again = restoreHarness(tasks, { storage });
    await again.restoreWorkflowTask();
    assert.equal(again.modal.opened, 0);
    assert.deepEqual(again.polled, []);
  }
  assert.ok([...storage.keys()].some(k => k.includes('job-u') && k.includes('RH_OUTCOME_UNKNOWN')));
});
test('同一任务出现不同错误码时重新提示一次；其他任务互不影响', async () => {
  const storage = new Map();
  await restoreHarness([{ job_id: 'job-u', status: 'outcome_unknown', error_code: 'A' }], { storage }).restoreWorkflowTask();
  const changed = restoreHarness([{ job_id: 'job-u', status: 'outcome_unknown', error_code: 'B' }], { storage });
  await changed.restoreWorkflowTask();
  assert.equal(changed.modal.opened, 1);
  const other = restoreHarness([{ job_id: 'job-v', status: 'outcome_unknown', error_code: 'A' }], { storage });
  await other.restoreWorkflowTask();
  assert.equal(other.modal.opened, 1);
});
test('运行中任务照常恢复弹出；显式 job_id 链接即使已知悉也打开', async () => {
  const storage = new Map();
  for (let i = 0; i < 2; i++) {
    const running = restoreHarness([{ job_id: 'job-r', status: 'running' }], { storage });
    await running.restoreWorkflowTask();
    assert.equal(running.modal.opened, 1);
  }
  const tasks = [{ job_id: 'job-u', status: 'outcome_unknown' }];
  await restoreHarness(tasks, { storage }).restoreWorkflowTask();
  const linked = restoreHarness(tasks, { storage, search: '?job_id=job-u' });
  await linked.restoreWorkflowTask();
  assert.equal(linked.modal.opened, 1);
});
test('localStorage 不可用时恢复不抛错', async () => {
  const h = restoreHarness([{ job_id: 'job-u', status: 'outcome_unknown' }]);
  h.window.localStorage = { getItem() { throw new Error('x'); }, setItem() { throw new Error('x'); } };
  await h.restoreWorkflowTask();
  assert.equal(h.modal.opened, 1);
});
test('任务面板对结果未知任务显示徽标并记为已知悉', async () => {
  const seen = [];
  const harness = pollingHarness({ status: 'outcome_unknown', outputs: [], error_code: 'E1' });
  harness.markUnknownTaskSeen = task => seen.push(task);
  const start = controller.indexOf('function startTaskPolling(taskId, target) {');
  const end = controller.indexOf('/* ==================== 7. 事件绑定', start);
  runInNewContext(controller.slice(start, end), harness);
  harness.startTaskPolling('job-u', 'rh_cloud');
  await harness.flush();
  assert.match(harness.body.innerHTML, /data-task-badge="outcome_unknown"/);
  assert.equal(seen[0]?.job_id, 'job-u');
  assert.equal(seen[0]?.error_code, 'E1');
});
test('无错误码时已知悉键退回 job_id + 状态', async () => {
  const storage = new Map();
  await restoreHarness([{ job_id: 'job-n', status: 'outcome_unknown' }], { storage }).restoreWorkflowTask();
  assert.deepEqual([...storage.keys()], ['gw.workflow.unknownTaskSeen:job-n:outcome_unknown']);
});
function panelHarness(tasks, currentWorkflowId = 'wf-1', respond) {
  const helpersStart = controller.indexOf('function workflowTaskId(task)');
  const helpersEnd = controller.indexOf('async function restoreWorkflowTask()', helpersStart);
  const start = controller.indexOf('const WORKFLOW_TASK_STATUS_LABEL');
  const end = controller.indexOf('function startTaskPolling(taskId, target) {', start);
  const body = { innerHTML: '', textContent: '' };
  const list = { innerHTML: '' };
  const modal = { classList: { contains: () => false, add() {} } };
  const polled = [];
  const urls = [];
  const context = {
    state: { pollTimer: null, currentWorkflow: { workflow_id: currentWorkflowId } }, clearInterval() {}, String, Number, Date, Array, URLSearchParams, Object, Boolean, Set,
    escapeHtml: value => String(value ?? '').replaceAll('<', '&lt;'),
    apiFetch: async url => { urls.push(url); return respond ? respond(url, tasks) : { tasks }; }, showToast() {}, setWorkflowTaskTrigger() {},
    wfGet: id => ({ taskModal: modal, taskModalBody: body, workflowTaskList: list })[id],
    startTaskPolling: (id, target) => polled.push([id, target]),
  };
  runInNewContext(controller.slice(helpersStart, helpersEnd) + controller.slice(start, end), context);
  const rows = () => [...list.innerHTML.matchAll(/data-task-row="([^"]+)"/g)].map(m => m[1]);
  return { ...context, body, list, polled, rows, urls };
}
const TASKS = [
  { job_id: 'aaaaaaaa-old-0000', workflow_id: 'wf-1', status: 'completed', target: 'local_comfy', created_at: '2026-10-14T01:00:00+00:00' },
  { job_id: 'bbbbbbbb-other-wf', workflow_id: 'wf-2', status: 'running', target: 'rh_cloud', created_at: '2026-10-14T03:00:00+00:00' },
  { job_id: 'cccccccc-new-1111', workflow_id: 'wf-1', status: 'outcome_unknown', target: 'rh_cloud', created_at: '2026-10-14T02:00:00+00:00' },
];
test('任务面板列出当前工作流多条任务（新→旧），每行含短ID、开始时间、执行端与状态标记', async () => {
  const h = panelHarness(TASKS);
  await h.openWorkflowTasks();
  assert.deepEqual(h.rows(), ['cccccccc-new-1111', 'aaaaaaaa-old-0000']);
  assert.match(h.list.innerHTML, /data-task-short-id>cccccccc</);
  assert.doesNotMatch(h.list.innerHTML, /data-task-short-id>cccccccc-/);
  assert.match(h.list.innerHTML, /data-task-executor>RH</);
  assert.match(h.list.innerHTML, /data-task-executor>ComfyUI</);
  assert.match(h.list.innerHTML, /data-task-time[^>]*>2026-10-14 \d{2}:00:00</);
  assert.match(h.list.innerHTML, /data-task-badge="outcome_unknown"/);
  assert.match(h.list.innerHTML, /data-task-badge="completed"/);
});
test('最新任务为结果未知时打开面板不自动展开详情；点击该行才展开', async () => {
  const h = panelHarness(TASKS);
  await h.openWorkflowTasks();
  assert.deepEqual(h.polled, []);
  assert.match(h.body.textContent, /点击/);
  h.expandWorkflowTask('cccccccc-new-1111');
  assert.deepEqual(h.polled, [['cccccccc-new-1111', 'rh_cloud']]);
  assert.match(h.list.innerHTML, /data-task-row="cccccccc-new-1111"[^>]*aria-expanded="true"/);
});
test('点击其他行展开对应任务的执行监控', async () => {
  const h = panelHarness(TASKS);
  await h.openWorkflowTasks();
  h.expandWorkflowTask('aaaaaaaa-old-0000');
  assert.deepEqual(h.polled, [['aaaaaaaa-old-0000', 'local_comfy']]);
});
test('最新任务为其他状态时打开面板照常展开', async () => {
  const h = panelHarness([{ job_id: 'job-r', workflow_id: 'wf-1', status: 'running', target: 'local_comfy', created_at: '2026-10-14T05:00:00Z' }]);
  await h.openWorkflowTasks();
  assert.deepEqual(h.polled, [['job-r', 'local_comfy']]);
});
test('当前工作流无任务时显示空态', async () => {
  const h = panelHarness(TASKS, 'wf-none');
  await h.openWorkflowTasks();
  assert.deepEqual(h.rows(), []);
  assert.match(h.body.textContent, /暂无任务/);
});
test('列表点击委托到 data-task-row', () => {
  assert.match(controller, /wfGet\('workflowTaskList'\)\?\.addEventListener\('click'[\s\S]{0,160}data-task-row[\s\S]{0,80}expandWorkflowTask/);
  assert.match(html, /id="workflowTaskList"/);
  assert.doesNotMatch(html, /id="workflowTaskSelect"/);
});

test('切换工作流界面时其他工作流的运行中任务不自动弹出', async () => {
  const h = restoreHarness([{ job_id: 'job-other', workflow_id: 'wf-2', status: 'running' }]);
  await h.restoreWorkflowTask();
  assert.equal(h.modal.opened, 0);
  assert.deepEqual(h.polled, []);
  const none = restoreHarness([{ job_id: 'job-r', status: 'running' }], { workflowId: null });
  await none.restoreWorkflowTask();
  assert.equal(none.modal.opened, 0);
});
test('恢复与面板请求携带 workflow_id 与 limit', async () => {
  const h = restoreHarness([{ job_id: 'job-r', status: 'running' }]);
  await h.restoreWorkflowTask();
  assert.equal(h.urls[0], '/api/god_workflow/tasks?workflow_id=wf-1&limit=20');
  assert.equal(h.modal.opened, 1);
});
// 新后端：回显 workflow_id 且含 next_cursor 时使用服务端结果与游标。
function pagedResponder(pages) {
  return url => {
    const cursor = new URL(url, 'http://x').searchParams.get('cursor') || '';
    return { workflow_id: 'wf-1', ...pages[cursor] };
  };
}
test('后端支持分页时显示「加载更多」并按 cursor 追加，末页隐藏', async () => {
  const pages = {
    '': { tasks: [{ job_id: 'job-3', workflow_id: 'wf-1', status: 'completed', created_at: '2026-10-14T03:00:00Z' }], next_cursor: 'c1', has_more: true },
    c1: { tasks: [{ job_id: 'job-2', workflow_id: 'wf-1', status: 'failed', created_at: '2026-10-14T02:00:00Z' }], next_cursor: null, has_more: false },
  };
  const h = panelHarness([], 'wf-1', pagedResponder(pages));
  await h.openWorkflowTasks();
  assert.match(h.list.innerHTML, /data-task-load-more/);
  await h.loadMoreWorkflowTasks();
  assert.equal(h.urls.at(-1), '/api/god_workflow/tasks?workflow_id=wf-1&limit=20&cursor=c1');
  assert.deepEqual(h.rows(), ['job-3', 'job-2']);
  assert.doesNotMatch(h.list.innerHTML, /data-task-load-more/);
});
test('后端未回显 workflow_id 时退回前端过滤且不显示「加载更多」', async () => {
  const h = panelHarness([], 'wf-1', () => ({ tasks: [
    { job_id: 'job-a', workflow_id: 'wf-1', status: 'completed', created_at: '2026-10-14T01:00:00Z' },
    { job_id: 'job-b', workflow_id: 'wf-2', status: 'completed', created_at: '2026-10-14T02:00:00Z' }], next_cursor: 'ignored' }));
  await h.openWorkflowTasks();
  assert.deepEqual(h.rows(), ['job-a']);
  assert.doesNotMatch(h.list.innerHTML, /data-task-load-more/);
});
test('服务端已过滤但无 next_cursor 键时不显示「加载更多」', async () => {
  const h = panelHarness([], 'wf-1', () => ({ workflow_id: 'wf-1', tasks: [{ job_id: 'job-a', workflow_id: 'wf-1', status: 'completed' }] }));
  await h.openWorkflowTasks();
  assert.deepEqual(h.rows(), ['job-a']);
  assert.doesNotMatch(h.list.innerHTML, /data-task-load-more/);
});
