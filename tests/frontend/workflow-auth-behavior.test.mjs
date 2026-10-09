import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {runInNewContext} from 'node:vm';
const read = p => readFileSync(new URL('../../'+p, import.meta.url),'utf8');
const hardware = read('web/js/core/hardware-telemetry.js');
const controller = read('web/js/controllers/workflow-controller.js');
const flush = () => new Promise(r=>setImmediate(r));
const response = (status=200, data={authenticated:true,principal:{role:'admin'},auth_mode:'local_account'}) => ({status,ok:status===200,json:async()=>data});
function harness() {
  const listeners = new Map(), requests=[];
  const window = {addEventListener(n,f){const a=listeners.get(n)||[];a.push(f);listeners.set(n,a);},dispatchEvent(e){for(const f of listeners.get(e.type)||[]) f(e);}};
  const document={readyState:'loading',addEventListener(){},getElementById(){return null;},querySelectorAll(){return [];}};
  const h={window,document,CustomEvent:class {constructor(type,{detail}){this.type=type;this.detail=detail;}},performance:{now:()=>0},AbortController,AbortSignal,
    fetch:async(url)=>{requests.push(url);return response();},setInterval:()=>1,clearInterval(){},clearTimeout(){},console};
  runInNewContext(hardware,h);
  const deck=window.HardwareDeck;
  for(const name of ['updateAuthDOM','syncTelemetry','syncOnlineStatus','syncStageProgress','openAccountModal']) deck[name]=()=>{};
  h.state={workflows:[{workflow_id:'fixture'}]};h.workbenchRoot=null;h.lifecycleAbortController=null;
  const root={querySelector:()=>null};document.getElementById=id=>id==='workflowWorkbench'?root:null;
  h.applyColumnCollapseState=()=>{};h.bound=0;h.bindEvents=()=>h.bound++;
  h.loads=0;h.loadSettingsState=async()=>h.loads++;h.refreshMediaAssetsCache=async()=>{};h.refreshWorkflowList=async()=>{};
  h.selectWorkflow=async()=>{};h.restoreWorkflowTask=async()=>{};h.startComfySyncWatcher=()=>{};h.disposeWorkbench=()=>{};
  h.handleParseLink=async()=>assert.fail('不应默认解析供应商');
  h.wfQueryAll=()=>[];h.wfGet=()=>null;h.clearEmptyWorkbenchView=()=>{};h.renderHeaderTelemetry=()=>{};
  runInNewContext(controller.slice(controller.indexOf('function resetWorkflowIdentity()'),controller.indexOf('window.WorkbenchWorkflow =')),h);
  runInNewContext(controller.slice(controller.indexOf('async function initWorkbench()'),controller.indexOf('async function restoreWorkflowTask()')),h);
  return Object.assign(h,{deck,gate:window.GWAuthGate,requests});
}
for(const order of ['workflow-before-auth','auth-before-workflow']) test(`已登录200初始化 ${order}`,async()=>{
  const h=harness();
  const auth=order==='auth-before-workflow'?h.deck.syncAuth():null;
  h.initWorkbench();
  await (auth||h.deck.syncAuth());await flush();
  assert.equal(h.gate.isAuthenticated(),true);assert.equal(h.loads,1);assert.equal(h.bound,1);
});
test('认证网络失败后 wait 可重试恢复',async()=>{
  const h=harness();h.fetch=async()=>{throw Error('offline');};await h.deck.syncAuth();
  h.fetch=async()=>response();await h.gate.wait();assert.equal(h.gate.isAuthenticated(),true);
});
test('异步旧401不得击穿新登录，也不能用业务401直接改身份',async()=>{
  const h=harness();await h.deck.syncAuth();const revision=h.gate.revision;
  await h.deck.syncAuth();h.gate.invalidate(401,revision);await flush();assert.equal(h.gate.isAuthenticated(),true);
  h.gate.invalidate(401,h.gate.revision);await flush();assert.equal(h.gate.isAuthenticated(),true);
});
test('匿名停止业务请求但显式 wait 可恢复',async()=>{
  const h=harness();h.fetch=async()=>response(200,{authenticated:false,principal:null});await h.deck.syncAuth();
  await h.initWorkbench();assert.equal(h.loads,0);
  h.fetch=async()=>response();await h.gate.wait();await h.initWorkbench();assert.equal(h.loads,1);
});
test('共享认证重入请求合并，不孤立已有wait',async()=>{
  const h=harness();let finish,count=0;h.fetch=()=>{count++;return new Promise(r=>finish=r);};
  const pending=h.gate.wait();h.deck.syncAuth();h.deck.syncAuth();finish(response());await pending;assert.equal(count,1);
});
test('登录事件恢复不重复绑定，普通角色工作流仍可初始化',async()=>{
  const h=harness();
  runInNewContext(controller.slice(controller.indexOf("window.addEventListener('gw-auth-state'"),controller.indexOf('if (document.readyState === "loading")',controller.indexOf("window.addEventListener('gw-auth-state'"))),Object.assign(h,{rebindWorkbench:()=>h.initWorkbench()}));
  h.fetch=async()=>response(200,{authenticated:false,principal:null});await h.deck.syncAuth();assert.equal(h.loads,0);
  h.fetch=async()=>response(200,{authenticated:true,principal:{role:'viewer'}});await h.deck.syncAuth();await flush();assert.equal(h.loads,1);assert.equal(h.bound,1);
});
test('身份切换清空旧账户工作流和素材缓存',()=>{
  const h=harness();h.state.currentWorkflow={workflow_id:'old-private'};
  h.state.mediaAssets=[{asset_id:'old-asset'}];h.state.clipboardHistory=[{text:'old-link'}];
  h.state.parameterSaves=new Map([['old-private',{updates:new Map()}]]);h.state.activeTaskId='old-job';
  h.resetWorkflowIdentity();
  assert.equal(h.state.currentWorkflow,null);assert.equal(h.state.activeTaskId,null);
  assert.equal(h.state.workflows.length,0);assert.equal(h.state.mediaAssets.length,0);
  assert.equal(h.state.clipboardHistory.length,0);assert.equal(h.state.parameterSaves.size,0);
});
test('业务持续401暂停遥测但不篡改已登录身份，主动认证恢复',async()=>{
  const h=harness();await h.deck.syncAuth();
  // 重新载入真实遥测方法，而非替换业务结果。
  const start=hardware.indexOf('    syncTelemetry: async function() {');const end=hardware.indexOf('\n    },',start);
  runInNewContext('syncTelemetry = '+hardware.slice(start,end).replace('    syncTelemetry:','')+'\n}',Object.assign(h,{authGate:h.gate}));
  const calls=[];h.fetch=async url=>{calls.push(url);return url.includes('auth/status')?response():response(401,{});};
  await h.syncTelemetry();await flush();await h.syncTelemetry();await h.syncTelemetry();
  assert.equal(calls.filter(u=>u.includes('health')).length,1);assert.equal(h.gate.isAuthenticated(),true);
  await h.deck.syncAuth();await h.syncTelemetry();assert.equal(calls.filter(u=>u.includes('health')).length,2);
});
test('任务队列登出后迟到200不会恢复上一账户内容',async()=>{
  const h=harness();await h.deck.syncAuth();let finish;
  h.fetch=()=>new Promise(r=>finish=r);h.state={};h.render=()=>{};h.ENDPOINT='/api/jobs';
  const queue=read('web/js/core/task-queue.js'),start=queue.indexOf('  let reloadSequence = 0;'),end=queue.indexOf("  window.addEventListener('gw-auth-state'",start);
  runInNewContext(queue.slice(start,end),h);const pending=h.reload();
  h.gate.authenticated=false;h.gate.revision++;await h.reload();finish(response(200,{items:[{job_id:'old-private'}],total:1}));await pending;
  assert.equal(h.state.items.length,0);assert.equal(h.state.degradation.kind,'unauthorized');
});
test('认证挂起超时释放共享请求，下一次wait成功',async()=>{
  const h=harness();h.AbortSignal={timeout:()=>AbortSignal.timeout(5)};
  h.fetch=async(url,{signal})=>new Promise((resolve,reject)=>signal.addEventListener('abort',()=>reject(signal.reason)));
  const keep=setTimeout(()=>{},30);await h.deck.syncAuth();clearTimeout(keep);assert.equal(h.gate.inFlight,null);
  h.fetch=async()=>response();await h.gate.wait();assert.equal(h.gate.isAuthenticated(),true);
});
test('控制器先加载且公共认证脚本缺席时，后续认证事件恢复',async()=>{
 const h=harness(),gate=h.window.GWAuthGate,deck=h.window.HardwareDeck;
 delete h.window.GWAuthGate;delete h.window.HardwareDeck;await h.initWorkbench();assert.equal(h.loads,0);
 h.window.GWAuthGate=gate;h.window.HardwareDeck=deck;
 runInNewContext(controller.slice(controller.indexOf("window.addEventListener('gw-auth-state'"),controller.indexOf('if (document.readyState === "loading")',controller.indexOf("window.addEventListener('gw-auth-state'"))),Object.assign(h,{rebindWorkbench:()=>h.initWorkbench()}));
 await deck.syncAuth();await flush();assert.equal(h.loads,1);
});
test('dispose与新根rebind不被旧初始化锁住',async()=>{
 const h=harness();await h.deck.syncAuth();let finish;
 h.loadSettingsState=()=>new Promise(r=>finish=r);const old=h.initWorkbench();
 runInNewContext(controller.slice(controller.indexOf('function disposeWorkbench()'),controller.indexOf('function rebindWorkbench()')),Object.assign(h,{lifecycleListeners:[]}));
 h.disposeWorkbench();h.loadSettingsState=async()=>{};await h.initWorkbench();finish();await old;
 assert.equal(h.bound,2);assert.ok(h.lifecycleAbortController);assert.equal(h.state.authLoading,false);
});

test('空工作流初始化不擅自POST默认RH源',async()=>{
  const h=harness();h.state.workflows=[];await h.deck.syncAuth();await h.initWorkbench();assert.equal(h.loads,1);
});
