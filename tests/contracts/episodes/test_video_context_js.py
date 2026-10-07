"""执行真实前端函数，验证上下文迟到响应、身份隔离和未知提交不可改绑。"""
import shutil
import subprocess
from pathlib import Path
import pytest

ROOT = Path(__file__).resolve().parents[3]


def test_video_context_identity_late_response_and_original_submission(tmp_path):
    node = shutil.which("node")
    if not node:
        pytest.skip("Node不可用，无法执行前端函数")
    text = (ROOT / "web/js/modules/episode-pipeline.js").read_text(encoding="utf-8")
    context_functions = text[text.index("  function videoRequestKey("):text.index("  function videoContextMarkup(")]
    generation = text[text.index("  function makeVideoIdempotencyKey("):text.index("  async function cancelVideoExport(")]
    script = r'''
const assert = require('node:assert/strict');
const state = {projectId:'p1',videoActorId:'actorA',videoIdentityReadGeneration:0};
const VIDEO_CONTEXT_STORAGE_PREFIX='test-video-context';
const videoContext={scopeKey:'',projectId:'',pipelineId:'',actorId:'',canvasId:'',entityId:'',canvases:[],nodes:[],loading:'',error:'',generation:0,controller:null};
const videoRequests=new Set();
let pipeline={project_id:'p1',pipeline_id:'pipe1'};
const currentPipeline=()=>pipeline;
const saved=new Map();
const localStorage={getItem:k=>saved.get(k)??null,setItem:(k,v)=>saved.set(k,v)};
const render=()=>{}; const toast=()=>{}; const scheduleVideoPoll=()=>{};
const window={crypto:require('node:crypto').webcrypto};
let calls=[];
let apiImpl=async url=>url.includes('?')?{canvases:[{canvas_id:'c1',project_id:'p1'},{canvas_id:'c2',project_id:'p1'}]}:{canvas_id:url.split('/').at(-1),nodes:[{entity_id:'e1'}]};
const api=(...args)=>apiImpl(...args);
let data={videoScripts:[]};
const updateInputData=()=>data; const localData=()=>data; const writeWorkspace=()=>{};
const selectedVideoModel=()=>({provider_id:'provider',model:'model'});
const tick=()=>new Promise(resolve=>setImmediate(resolve));
''' + context_functions + generation + r'''
(async()=>{
  activateVideoContext(pipeline); await tick();
  assert.equal(videoContext.canvasId,'');
  assert.equal(videoContextReady(pipeline),false);
  changeVideoContextCanvas('c1'); await tick(); changeVideoContextEntity('e1');
  assert.equal(videoContextReady(pipeline),true);
  assert.ok(saved.has(videoContextPreferenceKey('actorA','p1','pipe1')));
  state.videoActorId='actorB'; activateVideoContext(pipeline); await tick();
  assert.equal(videoContext.canvasId,'');
  state.videoActorId=''; activateVideoContext(pipeline); await tick();
  assert.equal(videoContext.canvasId,'');
  state.videoActorId='actorA'; activateVideoContext(pipeline); await tick();
  assert.equal(videoContext.canvasId,'c1');
  assert.equal(videoContext.entityId,'e1');
  let resolvers={};
  apiImpl=url=>new Promise(resolve=>{resolvers[url.split('/').at(-1)]=resolve;});
  changeVideoContextCanvas('c1'); changeVideoContextCanvas('c2');
  resolvers.c2({canvas_id:'c2',nodes:[{entity_id:'new'}]}); await tick();
  resolvers.c1({canvas_id:'c1',nodes:[{entity_id:'old'}]}); await tick();
  assert.equal(videoContext.canvasId,'c2'); assert.equal(videoContext.nodes[0].entity_id,'new');
  const original={actor_id:'actorA',idempotency_key:'original-key',payload:{prompt:'原始提示',production_context:{project_id:'p1',canvas_id:'c1',entity_id:'e1'}}};
  data.videoScripts=[{id:'v1',videoStatus:'submitting',videoSubmission:structuredClone(original),prompt:'已改提示'}];
  apiImpl=async(url,options)=>{if(url.includes('identity-binding')) return {bound:true,user_id:state.videoActorId}; calls.push({url,options});throw new Error('受控断线');};
  await generateVideo(pipeline,'v1');
  assert.equal(calls.length,1);
  assert.equal(calls[0].options.headers['Idempotency-Key'],'original-key');
  assert.deepEqual(JSON.parse(calls[0].options.body),original.payload);
  assert.deepEqual(data.videoScripts[0].videoSubmission,original);
  state.videoActorId='actorB';
  await assert.rejects(generateVideo(pipeline,'v1'),/身份|主体/);
  assert.equal(calls.length,1,'换账号不得沿旧未知提交产生另一计费请求');
  state.videoActorId='actorA';
  data.videoExportSubmission={actor_id:'actorA',idempotency_key:'original-export',payload:{asset_ids:['asset-old'],project_id:'p1',canvas_id:'c1',entity_id:'e1',preset:'h264_720p_30fps'}};
  const exportOriginal=structuredClone(data.videoExportSubmission);
  await generateVideoExport(pipeline);
  assert.equal(calls.length,2);
  assert.equal(calls[1].url,'/api/video-exports');
  assert.deepEqual(JSON.parse(calls[1].options.body),exportOriginal.payload);
  state.videoActorId='actorB';
  await assert.rejects(generateVideoExport(pipeline),/身份|主体/);
  assert.equal(calls.length,2);
  // 先前未知请求遇当前拒绝时，不能据此抹掉历史幂等键或原始载荷。
  state.videoActorId='actorA';
  for (const status of [400,401,403,404,422]) {
    apiImpl=async(url,options)=>{
      if(url.includes('identity-binding')) return {bound:true,user_id:'actorA'};
      calls.push({url,options}); throw Object.assign(new Error('本次请求拒绝'),{status});
    };
    await generateVideo(pipeline,'v1');
    assert.deepEqual(data.videoScripts[0].videoSubmission,original);
    assert.equal(data.videoScripts[0].videoStatus,'submitting');
    await generateVideoExport(pipeline);
    assert.deepEqual(data.videoExportSubmission,exportOriginal);
    assert.equal(data.videoExportStatus,'submitting');
  }
  // focus/pageshow的新查询抢占本次查询：丢弃的结果必须阻止两种POST。
  for (const submit of [()=>generateVideo(pipeline,'v1'),()=>generateVideoExport(pipeline)]) {
    state.videoActorId='actorA'; calls=[];
    const queries=[];
    apiImpl=(url,options)=>{
      if(url.includes('identity-binding')) return new Promise(resolve=>queries.push(resolve));
      if(options?.method === 'POST') calls.push({url,options});
      return Promise.resolve({canvases:[],nodes:[]});
    };
    const submitting=submit();
    const rejected=assert.rejects(submitting,/身份|主体/);
    const focusing=refreshVideoIdentity();
    assert.equal(queries.length,2);
    queries[0]({bound:true,user_id:'actorB'});
    await rejected;
    assert.equal(calls.length,0);
    assert.deepEqual(data.videoScripts[0].videoSubmission,original);
    assert.deepEqual(data.videoExportSubmission,exportOriginal);
    queries[1]({bound:true,user_id:'actorB'}); await focusing;
  }
  // 当前查询失败不能借用缓存主体继续恢复。
  state.videoActorId='actorA'; calls=[];
  apiImpl=async(url,options)=>{
    if(url.includes('identity-binding')) throw new Error('身份查询断线');
    if(options?.method === 'POST') calls.push({url,options});
    return {canvases:[],nodes:[]};
  };
  await assert.rejects(generateVideo(pipeline,'v1'),/身份|主体/);
  await assert.rejects(generateVideoExport(pipeline),/身份|主体/);
  assert.equal(calls.length,0);
  assert.deepEqual(data.videoScripts[0].videoSubmission,original);
  assert.deepEqual(data.videoExportSubmission,exportOriginal);
  console.log('PASS: identity + stale response + immutable submission + concurrent identity + rejection recovery');
})().catch(e=>{console.error(e);process.exitCode=1});
'''
    runner = tmp_path / "video-context.cjs"
    runner.write_text(script, encoding="utf-8")
    result = subprocess.run([node, str(runner)], capture_output=True, text=True, encoding="utf-8", timeout=15)
    assert result.returncode == 0, result.stderr
    assert "PASS:" in result.stdout
