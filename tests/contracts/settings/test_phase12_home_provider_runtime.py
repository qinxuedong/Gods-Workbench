"""首页真实服务商选择与显式计费动作的可执行前端回归。"""
import json
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]


def test_home_uses_configured_provider_and_prevents_duplicate_paid_requests(tmp_path):
    node = shutil.which('node')
    assert node, '首页运行时回归需要 Node.js'
    script = r"""
const fs = require('node:fs'), vm = require('node:vm'), assert = require('node:assert/strict');
const elements = new Map();
function element() {
  return {value:'', textContent:'', innerHTML:'', children:[], classList:{add(){},remove(){},toggle(){}},
    setAttribute(){},removeAttribute(){}, addEventListener(k,fn){this[k]=fn;},
    replaceChildren(){this.children=[];},appendChild(e){this.children.push(e);},insertAdjacentHTML(){}};
}
const get = id => { if (!elements.has(id)) elements.set(id,element()); return elements.get(id); };
const calls = []; let configuration = {configured:true, providers:[
  {provider_id:'provider-a',configured:true,models:['model-a'],default_model:'model-a'},
  {provider_id:'provider-b',configured:true,models:['model-b'],default_model:'model-b'}]};
let resolveRequest, consent=true, confirmations=0;
const sandbox = {console, AbortController, setTimeout, clearTimeout, localStorage:{getItem(){return null;}},
  document:{readyState:'loading',addEventListener(){}, getElementById:get, createElement:element, querySelector(){return null;}},
  alert(){}, confirm(){confirmations++;return consent;},
  fetch: async (url, init={}) => { calls.push({url,init});
    if (url==='/api/chat/config') return {ok:true,json:async()=>configuration};
    return await new Promise(resolve=>{resolveRequest=resolve;});
  }
};
sandbox.window=sandbox;
let source=fs.readFileSync(process.argv[1],'utf8').replace('    rebind: () =>', '    syncAuraBusStatus,\n    rebind: () =>');
vm.createContext(sandbox); vm.runInContext(source,sandbox);
(async()=>{
  const home=sandbox.WorkbenchHome; await home.syncAuraBusStatus();
  assert.equal(calls.filter(c=>c.init.method==='POST').length,0);
  get('workbenchChatInput').value='测试';
  await home.handleChatSubmit({preventDefault(){}});
  assert.equal(calls.length,1, '多个服务商无默认时禁止静默选取');
  const choices=get('workbenchAuraModelOptions').children;
  assert.equal(choices.length,2); choices[1].click();
  consent=false; await home.handleChatSubmit({preventDefault(){}}); assert.equal(calls.length,1);
  consent=true; const pending=home.handleChatSubmit({preventDefault(){}});
  get('workbenchChatInput').value='重复发送'; await home.handleChatSubmit({preventDefault(){}});
  assert.equal(calls.length,2);
  const body=JSON.parse(calls[1].init.body);
  assert.equal(body.provider_id,'provider-b'); assert.equal(body.model,'model-b');
  assert.equal(confirmations,2); assert.ok(calls[1].init.signal);
  resolveRequest({ok:true,json:async()=>({response:'完成'})}); await pending;
  home.setAuraModel('unknown','model-a');
  const retry=home.handleChatSubmit({preventDefault(){}});
  assert.equal(JSON.parse(calls[2].init.body).provider_id,'provider-b');
  resolveRequest({ok:true,json:async()=>({response:'完成'})}); await retry;
  configuration=null; await home.syncAuraBusStatus();
  get('workbenchChatInput').value='已失效'; await home.handleChatSubmit({preventDefault(){}});
  assert.equal(calls.filter(c=>c.init.method==='POST').length,2);
  console.log(JSON.stringify({ok:true}));
})().catch(e=>{console.error(e);process.exitCode=1;});
"""
    result = subprocess.run([node, '-e', script, str(ROOT / 'web/js/controllers/home-controller.js')],
                            capture_output=True, text=True, encoding='utf-8', timeout=60)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)['ok']
