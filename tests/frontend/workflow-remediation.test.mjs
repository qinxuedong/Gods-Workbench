// 运行真实控制器，替换 DOM、网络与时钟，不连接任何服务。
import {readFileSync, writeFileSync} from 'node:fs';
import {createContext, runInContext} from 'node:vm';
import assert from 'node:assert/strict';
import test from 'node:test';
import {webcrypto} from 'node:crypto';

const source = readFileSync(new URL('../../web/js/controllers/workflow-controller.js', import.meta.url), 'utf8');
const results = {};
const flush = () => new Promise(resolve => setImmediate(resolve));

function harness(fetchImpl) {
  const timers = new Map(), handlers = new Map(), requests = [], warnings = [];
  let serial = 0;
  const elements = new Map();
  const element = id => {
    if (!elements.has(id)) elements.set(id, {innerHTML:'',textContent:'',style:{},classList:{add(){},remove(){},toggle(){}},querySelector(){return null;},querySelectorAll(){return[];}});
    return elements.get(id);
  };
  const context = createContext({
    window:{location:{search:''},GWAuthGate:{revision:1,isAuthenticated:()=>true},addEventListener(type,fn){handlers.set(type,fn);},removeEventListener(type){handlers.delete(type);}},
    document:{readyState:'loading',addEventListener(){},createElement:()=>({textContent:'',innerHTML:''})},
    localStorage:{getItem:()=>null,setItem(){}}, CSS:{escape:value=>value},
    AbortController, URLSearchParams, crypto:webcrypto, console:{warn(...args){warnings.push(args.map(String).join(' '));},log(){}},
    setTimeout:fn=>{const id=++serial;timers.set(id,fn);return id;}, clearTimeout:id=>timers.delete(id),
    setInterval:fn=>{const id=++serial;timers.set(id,fn);return id;}, clearInterval:id=>timers.delete(id),
    fetch:async(url, options={})=>{requests.push({url,...options});return fetchImpl(url,options,requests.length);}
  });
  runInContext(source,context);
  context.element = element;
  runInContext('globalThis.auditState=state; workbenchRoot={querySelector:id=>element(id),querySelectorAll:()=>[]};',context);
  return {context,state:context.auditState,timers,handlers,requests,warnings,element,
    run:code=>runInContext(code,context),
    async runLastTimeout(){const [id,fn]=[...timers.entries()].at(-1);timers.delete(id);await fn();await flush();}};
}
const response = (data,status=200)=>({ok:status<400,status,json:async()=>data});
const workflow = id=>({workflow_id:id,version:1,revision:1,name:id,nodes:[{id:'1',node_id:'1',kind:'LoadImage',inputs:{image:id+'.png'},widgets:[{name:'image',value:id+'.png'}]}],connections:[],actuator_params:[]});

test('目录与素材列表保留 items 和 folders', async()=>{
  const h=harness(async()=>response({items:[{asset_id:'a'}],folders:[{folder_id:'f'}]}));
  const list=await h.run('apiFetch("/api/god_workflow/list")');
  const assets=await h.run('apiFetch("/api/god_workflow/assets/list")');
  assert.equal(list.items.length,1);assert.equal(list.folders.length,1);assert.equal(assets.items.length,1);
});
test('快速编辑合并不同字段，连续保存回写版本',async()=>{
  let version=1;
  const h=harness(async(url,options)=>{
    assert.equal(JSON.parse(options.body).expected_version,version);
    return response({...workflow('A'),version:++version});
  });h.state.currentWorkflow=workflow('A');
  h.run('schedulePersistWorkflowParams([{node_id:"1",field_name:"image",value:"first"}]);schedulePersistWorkflowParams([{node_id:"1",field_name:"seed",value:123}]);');
  await h.run('flushWorkflowParams("A")');
  assert.deepEqual(JSON.parse(h.requests[0].body).widget_updates.map(x=>x.field_name),['image','seed']);
  h.run('schedulePersistWorkflowParams([{node_id:"1",field_name:"image",value:"second"}]);');await h.run('flushWorkflowParams("A")');
  assert.equal(version,3);assert.equal(h.state.currentWorkflow.revision,3);
});
test('切换文档时待保存仍使用原目标版本',async()=>{
  const h=harness(async()=>response({...workflow('A'),version:2}));h.state.currentWorkflow=workflow('A');
  h.run('schedulePersistWorkflowParams([{node_id:"1",field_name:"image",value:"first"}]);');
  h.state.currentWorkflow={...workflow('B'),version:9,revision:9};await h.run('flushWorkflowParams("A")');
  assert.equal(JSON.parse(h.requests[0].body).expected_version,1);assert.equal(h.state.currentWorkflow.revision,9);
});
test('服务端新版本重载后下一次编辑使用新的CAS版本',async()=>{
  const h=harness(async(url,options)=>response({...workflow('A'),version:JSON.parse(options.body).expected_version+1}));
  h.state.currentWorkflow=workflow('A');
  h.run('schedulePersistWorkflowParams([{node_id:"1",field_name:"image",value:"first"}]);');
  await h.run('flushWorkflowParams("A")');
  h.context.latest={...workflow('A'),version:3,revision:3};
  h.run('renderWorkflowCatalog=()=>{};renderHeaderTelemetry=()=>{};renderEnvironmentDiff=()=>{};renderActuatorRack=()=>{};renderNodeCanvas=()=>{};setExecutionTarget=()=>{};setCurrentWorkflow(latest);');
  h.run('schedulePersistWorkflowParams([{node_id:"1",field_name:"image",value:"after-reload"}]);');
  await h.run('flushWorkflowParams("A")');
  assert.equal(JSON.parse(h.requests[1].body).expected_version,3);assert.equal(h.state.currentWorkflow.version,4);
});
test('移动请求只包含目标目录和显式目标版本',async()=>{
  const h=harness(async()=>response(workflow('B')));h.state.currentWorkflow=workflow('A');
  await h.run('apiFetch("/api/god_workflow/item/B/folder",{method:"PUT",body:JSON.stringify({folder_id:"f",expected_version:9})})');
  assert.deepEqual(JSON.parse(h.requests[0].body),{folder_id:'f',expected_version:9});
});
test('重复点击执行只提交一次并携带幂等键',async()=>{
  const h=harness(async()=>response({job_id:'job-1',target:'local_comfy'}));h.state.currentWorkflow=workflow('A');h.state.executionTarget='local_comfy';h.run('startTaskPolling=()=>{};');
  await Promise.all([h.run('handleExecuteWorkflow()'),h.run('handleExecuteWorkflow()')]);
  assert.equal(h.requests.length,1);assert.ok(h.requests[0].headers['Idempotency-Key']);assert.equal(JSON.parse(h.requests[0].body).expected_version,1);
});
test('执行把宿主URL来源传至服务端，不沿用另一项目的文档来源',async()=>{
  const h=harness(async()=>response({job_id:'project-job',target:'local_comfy'}));
  h.state.currentWorkflow={...workflow('A'),metadata:{source_context:{project_id:'old',canvas_id:'old-canvas',project_version:9}}};
  h.context.window.location.search='?project_id=current&canvas_id=current-canvas&project_version=2';
  h.run('startTaskPolling=()=>{};');await h.run('handleExecuteWorkflow()');
  assert.deepEqual(JSON.parse(h.requests[0].body).source_context,{project_id:'current',canvas_id:'current-canvas',project_version:'2'});
});
test('URL无项目参数时不携带文档遗留来源',async()=>{
  const h=harness(async()=>response({job_id:'fresh-job',target:'local_comfy'}));
  h.state.currentWorkflow={...workflow('A'),metadata:{source_context:{project_id:'stale',project_version:3}}};
  h.context.window.location.search='';
  h.run('startTaskPolling=()=>{};');await h.run('handleExecuteWorkflow()');
  assert.deepEqual(JSON.parse(h.requests[0].body).source_context,{});
});
test('参数保存版本冲突后按服务端当前版本自动重试一次且草稿不丢',async()=>{
  let calls=0;
  const h=harness(async(url,options)=>{
    calls+=1;
    if(calls===1)return response({detail:{code:'VERSION_CONFLICT',message:'expected_version 与当前版本不一致',expected_version:1,current_version:5}},409);
    return response({...workflow('A'),version:6});
  });
  h.state.currentWorkflow=workflow('A');h.state.syncRevisions.A=1;
  h.run('schedulePersistWorkflowParams([{node_id:"1",field_name:"image",value:"draft"}]);');
  await h.run('flushWorkflowParams("A")');
  assert.equal(JSON.parse(h.requests[0].body).expected_version,1);
  assert.equal(JSON.parse(h.requests[1].body).expected_version,5);
  assert.deepEqual(JSON.parse(h.requests[1].body).widget_updates.map(x=>x.value),['draft']);
  assert.equal(h.state.currentWorkflow.version,6);
  assert.equal(h.state.syncRevisions.A,6);
});
test('执行遇来源项目失效时换无项目上下文重试一次',async()=>{
  let calls=0;
  const h=harness(async()=>{
    calls+=1;
    if(calls===1)return response({detail:{code:'PROJECT_NOT_FOUND',message:'项目不存在'}},404);
    return response({job_id:'retry-job',target:'local_comfy'});
  });
  h.state.currentWorkflow=workflow('A');
  h.context.window.location.search='?project_id=gone';
  h.run('startTaskPolling=()=>{};');await h.run('handleExecuteWorkflow()');
  assert.equal(h.requests.length,2);
  assert.deepEqual(JSON.parse(h.requests[0].body).source_context,{project_id:'gone'});
  assert.deepEqual(JSON.parse(h.requests[1].body).source_context,{});
  assert.notEqual(h.requests[0].headers['Idempotency-Key'],h.requests[1].headers['Idempotency-Key']);
  assert.equal(h.state.activeTaskId,'retry-job');
});
test('旧任务迟到终态不停止新任务轮询',async()=>{
  let resolveA,resolveB;const a=new Promise(r=>resolveA=r),b=new Promise(r=>resolveB=r);
  const h=harness(async url=>response(await(url.endsWith('/A')?a:b)));
  h.run('startTaskPolling("A","local_comfy");startTaskPolling("B","local_comfy");');
  resolveA({status:'completed',outputs:[]});await flush();assert.equal(h.timers.size,1);
  resolveB({status:'completed',outputs:[]});await flush();assert.equal(h.timers.size,0);
});
test('本地导入默认本地执行',()=>{
  const h=harness(async()=>response({}));h.context.localDoc=workflow('local');
  h.run('setExecutionTarget=target=>globalThis.chosenTarget=target;renderWorkflowCatalog=()=>{};renderHeaderTelemetry=()=>{};renderEnvironmentDiff=()=>{};renderActuatorRack=()=>{};renderNodeCanvas=()=>{};setCurrentWorkflow(localDoc);');
  assert.equal(h.context.chosenTarget,'local_comfy');
});

test('普通版本变化保留未保存草稿，迟到查询不切回旧文档',async()=>{
  const h=harness(async url=>response(url.endsWith('sync-status')?{revisions:{A:2}}:{...workflow('A'),version:2}));
  h.state.currentWorkflow=workflow('A');h.state.syncRevisions.A=1;
  h.run('schedulePersistWorkflowParams([{node_id:"1",field_name:"image",value:"draft"}]);');
  await h.run('checkComfySavedSyncOnce()');
  assert.equal(h.requests.length,1);assert.equal(h.state.currentWorkflow.version,1);
  assert.equal(h.state.syncRevisions.A,1);assert.equal(h.state.parameterSaves.get('A').pending.size,1);
  let resolve;
  const late=harness(async url=>url.endsWith('sync-status')?response({revisions:{A:2}}):new Promise(r=>resolve=r));
  late.state.currentWorkflow=workflow('A');late.state.syncRevisions.A=1;
  late.run('refreshWorkflowList=async()=>{};');
  const checking=late.run('checkComfySavedSyncOnce()');await flush();
  late.state.currentWorkflow=workflow('B');resolve(response({...workflow('A'),version:2}));await checking;
  assert.equal(late.state.currentWorkflow.workflow_id,'B');
});

test('文档版本刷新不伪称原生宿主保存，来源徽标按真实类型显示',async()=>{
  const h=harness(async url=>response(url.endsWith('sync-status')?{revisions:{A:2}}:{...workflow('A'),version:2}));
  h.state.currentWorkflow=workflow('A');h.state.syncRevisions.A=1;
  h.run('refreshWorkflowList=async()=>{};setCurrentWorkflow=wf=>{state.currentWorkflow=wf};showToast=text=>globalThis.toast=text;');
  await h.run('checkComfySavedSyncOnce()');
  assert.equal(h.state.currentWorkflow.version,2);assert.match(h.context.toast,/版本已更新/);assert.doesNotMatch(h.context.toast,/ComfyUI/);
  for (const [source,badge] of [['canvas','LOCAL'],['comfyui','LOCAL'],['runninghub','RH'],['liblib','LIB'],['unknown','--']]) {
    assert.equal(h.run(`getSourceBadgeText(${JSON.stringify(source)})`),badge);
  }
});

test('apiFetch 挂载 detail 到 error.detail',async()=>{
  const h=harness(async()=>response({detail:{code:'TEST_ERROR',message:'测试',extra_field:'值'}},400));
  try{await h.run('apiFetch("/test")');assert.fail('应抛出错误');}catch(err){
    assert.equal(err.code,'TEST_ERROR');assert.equal(err.detail.extra_field,'值');
  }
});

test('409 COMFY_ADAPTER_CONFLICT upgradable=true 时 toast 含 fix_hint、版本和升级按钮',async()=>{
  const h=harness(async()=>response({detail:{code:'COMFY_ADAPTER_CONFLICT',message:'页面适配文件已有不同内容',
    installed_version:'legacy-9b0ff8a',required_version:'2',
    fix_hint:'检测到旧版神工坊页面适配（legacy-9b0ff8a），可自动升级',upgradable:true}},409));
  h.state.currentWorkflow=workflow('A');h.state.authPrincipalKey='key';
  h.context.window.location.origin='http://testserver';
  h.context.window.open=()=>({closed:false,location:{href:'about:blank'}});
  h.run('flushWorkflowParams=async()=>{};');
  const toasts=[];
  h.context.toasts=toasts;
  h.run('showToast=(msg,isError,actionLabel,onAction)=>{toasts.push({msg,isError,actionLabel,onAction});};');
  await h.run('handleOpenInComfyUi()');
  // 第一个 toast 是"正在导入..."，第二个是冲突提示
  const conflict=toasts.find(t=>t.msg.includes('legacy-9b0ff8a'));
  assert.ok(conflict,'应有冲突提示');
  assert.match(conflict.msg,/检测到旧版神工坊页面适配/);
  assert.match(conflict.msg,/legacy-9b0ff8a/);
  assert.match(conflict.msg,/需要版本：2/);
  assert.equal(conflict.actionLabel,'升级适配');
  assert.equal(typeof conflict.onAction,'function');
});

test('409 COMFY_ADAPTER_CONFLICT upgradable=false 时无升级按钮',async()=>{
  const h=harness(async()=>response({detail:{code:'COMFY_ADAPTER_CONFLICT',message:'页面适配文件已有不同内容',
    installed_version:'unknown',required_version:'2',
    fix_hint:'不是神工坊发布的适配脚本',upgradable:false}},409));
  h.state.currentWorkflow=workflow('A');h.state.authPrincipalKey='key';
  h.context.window.location.origin='http://testserver';
  h.context.window.open=()=>({closed:false,location:{href:'about:blank'}});
  h.run('flushWorkflowParams=async()=>{};');
  const toasts=[];
  h.context.toasts=toasts;
  h.run('showToast=(msg,isError,actionLabel)=>{toasts.push({msg,isError,actionLabel});};');
  await h.run('handleOpenInComfyUi()');
  const conflict=toasts.find(t=>t.msg.includes('unknown'));
  assert.ok(conflict,'应有冲突提示');
  assert.match(conflict.msg,/不是神工坊发布的适配脚本/);
  assert.match(conflict.msg,/已安装版本：unknown/);
  assert.equal(conflict.actionLabel,undefined,'不应有升级按钮');
});

test('点击升级按钮后发出的请求 body 为 {"adapter_upgrade":true}',async()=>{
  let calls=0;
  const h=harness(async(url,options)=>{
    calls+=1;
    if(calls===1)return response({detail:{code:'COMFY_ADAPTER_CONFLICT',installed_version:'legacy-9b0ff8a',
      required_version:'2',fix_hint:'可自动升级',upgradable:true}},409);
    return response({open_url:'http://127.0.0.1:8188',browser_adapter:{mode:'managed',adapter_version:2,upgraded:true},
      expected_version:1,workflow_graph:{},workflow_format:'native'});
  });
  h.state.currentWorkflow=workflow('A');h.state.authPrincipalKey='key';
  h.context.window.location.origin='http://testserver';
  h.context.window.open=()=>({closed:false,location:{href:'about:blank'}});
  h.run('flushWorkflowParams=async()=>{};');
  let upgradeCallback=null;
  h.context.toasts=[];
  h.run('showToast=(msg,isError,actionLabel,onAction)=>{if(actionLabel==="升级适配")toasts.push(onAction);};');
  await h.run('handleOpenInComfyUi()');
  assert.equal(calls,1);assert.equal(h.requests[0].body,undefined,"首次导入不带升级开关");
  upgradeCallback=h.context.toasts[0];
  assert.ok(upgradeCallback,'应有升级回调');
  await upgradeCallback();await flush();
  assert.equal(calls,2);
  const req=h.requests[1];
  assert.equal(req.headers['Content-Type'],'application/json');
  assert.deepEqual(JSON.parse(req.body),{adapter_upgrade:true});
});

test('升级重试本身再次 409 COMFY_ADAPTER_CONFLICT 不给升级按钮',async()=>{
  const h=harness(async()=>response({detail:{code:'COMFY_ADAPTER_CONFLICT',installed_version:'legacy-9b0ff8a',
    required_version:'2',fix_hint:'可自动升级',upgradable:true}},409));
  h.state.currentWorkflow=workflow('A');h.state.authPrincipalKey='key';
  h.context.window.location.origin='http://testserver';
  h.context.window.open=()=>({closed:false,location:{href:'about:blank'}});
  h.run('flushWorkflowParams=async()=>{};');
  const toasts=[];
  h.context.toasts=toasts;
  h.run('showToast=(msg,isError,actionLabel)=>{toasts.push({msg,isError,actionLabel});};');
  await h.run('handleOpenInComfyUi({adapterUpgrade:true})');
  const conflict=toasts.find(t=>t.msg.includes('legacy-9b0ff8a'));
  assert.ok(conflict,'应有冲突提示');
  assert.equal(conflict.actionLabel,undefined,'升级重试失败不应再给升级按钮');
});
