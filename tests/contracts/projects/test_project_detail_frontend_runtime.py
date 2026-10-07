"""项目详情前端方法契约：真实执行请求包装器，防止 GET 落入仅写路由。"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]


def test_asset_manager_project_detail_uses_frozen_get_route():
    node = shutil.which("node")
    assert node, "项目详情前端守卫需要 Node.js，不能静默跳过"
    source_path = ROOT / "web/js/modules/asset-manager/api.js"
    script = r"""
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync(process.argv[1], 'utf8')
  .replace(/^import assetManagerHttp[^\n]*\n/m, '')
  .replace('export function createAssetManagerApi', 'function createAssetManagerApi')
  .replace('export default assetManagerApi;', '');
const calls = [];
const context = {assetManagerHttp: {request() {}}, Headers, encodeURIComponent};
vm.createContext(context);
vm.runInContext(source, context);
const api = context.createAssetManagerApi({request(url, init) {calls.push({url, init}); return Promise.resolve({});}});
api.getRegistryProject('prj-0001');
api.updateRegistryProject('prj-0001', {expected_version: 1, name: '更新'});
process.stdout.write(JSON.stringify(calls));
"""
    result = subprocess.run([node, "-e", script, str(source_path)], capture_output=True, text=True,
                            encoding="utf-8", timeout=15, check=True)
    calls = json.loads(result.stdout)
    assert calls[0]["url"] == "/api/projects/prj-0001"
    assert calls[0]["init"].get("method", "GET") == "GET"
    assert calls[0]["init"]["credentials"] == "same-origin"
    assert calls[1]["url"] == "/api/asset-registry/projects/prj-0001"
    assert calls[1]["init"]["method"] == "PATCH"


def test_project_detail_read_contract_is_available_to_authenticated_client(client):
    response = client.get("/api/projects/definitely-not-a-project", headers={"Authorization": "Bearer test-project-detail"})
    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "PROJECT_NOT_FOUND"


def test_template_default_action_sends_selected_template_version():
    node = shutil.which("node")
    assert node
    script = r"""
const fs=require('node:fs'),vm=require('node:vm');
const source=fs.readFileSync(process.argv[1],'utf8');
const a=source.indexOf('async function setProjectDirectoryTemplateDefault(');
const b=source.indexOf('\nasync function ',a+1);
const requests=[];
const context={projectDirectoryTemplateManagerState:{templates:[{id:'dtpl_1',version:4,default_revision:99}],saving:false,revision:100},
 renderProjectDirectoryTemplateManagerSheet(){},apiJsonResponse:async x=>x,
 assetManagerApi:{setDefaultProjectDirectoryTemplate:(id,body)=>{requests.push({id,body});return {}; }},
 loadProjectDirectoryTemplatesForManager:async()=>{},chooseProjectDirectoryTemplate(){},setStatus(){}};
vm.createContext(context);vm.runInContext(source.slice(a,b),context);
context.setProjectDirectoryTemplateDefault('dtpl_1').then(()=>console.log(JSON.stringify(requests)));
"""
    result = subprocess.run([node, "-e", script, str(ROOT / "web/js/modules/asset-manager.js")],
                            capture_output=True, text=True, encoding="utf-8", timeout=30)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == [{"id": "dtpl_1", "body": {"expected_version": 4}}]


def test_registry_stale_cursor_reloads_first_page_and_preserves_error_details():
    node = shutil.which("node")
    assert node
    script = r"""
const fs=require('node:fs'),vm=require('node:vm');
const source=fs.readFileSync(process.argv[1],'utf8');
const a=source.indexOf('async function apiJsonResponse('), b=source.indexOf('\nconst STORAGE_KIND_LABELS',a);
const c=source.indexOf('async function loadMoreRegistryAssets('),d=source.indexOf('\n// 【浏览预加载】契约',c);
let reloads=0, applied=0;
const context={registryLoading:false,registryPageLoading:false,registryHasMoreAssets:()=>true,
 registryRequestSeq:1,registryNextOffset:2,registryNextCursor:'old',registryQueryString:()=>'',
 cancelRegistryPrefetchTimer(){},registryAppendDomContext:()=>null,contextSentinelBusy(){},root:{querySelector:()=>null},
 registryAssetsQueryCache:new Map(),registryPrefetchInFlight:false,beginRegistryAssetRequest:()=>null,
 assetManagerApi:{getRegistryAssets:()=>({ok:false,status:409,json:async()=>({detail:{code:'VERSION_CONFLICT',message:'列表变化'}})})},
 refreshRegistryAssets:async()=>{reloads++;context.registryRequestSeq++;context.registryNextCursor='';context.registryNextOffset=0;context.registryPageLoading=false;},
 setStatus(){},applyRegistryAssetResponse(){applied++;},setRegistryAssetCache(){},isRegistryAbortError:()=>false};
vm.createContext(context);vm.runInContext(source.slice(a,b)+'\n'+source.slice(c,d),context);
(async()=>{
 try{await context.apiJsonResponse(context.assetManagerApi.getRegistryAssets())}catch(e){if(e.code!=='VERSION_CONFLICT'||e.status!==409||e.message!=='列表变化')throw e;}
 await context.loadMoreRegistryAssets();
 console.log(JSON.stringify({reloads,applied,cursor:context.registryNextCursor,offset:context.registryNextOffset}));
})().catch(e=>{console.error(e);process.exitCode=1;});
"""
    result = subprocess.run([node, "-e", script, str(ROOT / "web/js/modules/asset-manager.js")],
                            capture_output=True, text=True, encoding="utf-8", timeout=30, check=True)
    assert json.loads(result.stdout) == {"reloads": 1, "applied": 0, "cursor": "", "offset": 0}
