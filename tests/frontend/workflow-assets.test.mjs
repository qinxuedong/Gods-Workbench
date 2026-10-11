import assert from 'node:assert/strict';
import test from 'node:test';
import {readFileSync} from 'node:fs';
import {createContext, runInContext} from 'node:vm';
import {workflowAssetId, workflowLibraryId, workflowCategoryId, workflowSourceId, zipWorkflowFiles} from '../../web/js/modules/asset-manager/workflow-assets.js';

test('素材页读取canonical标识，兼容旧字段但不覆盖长ID', () => {
    assert.equal(workflowAssetId({asset_id:'asset-current',id:'old'}),'asset-current');
    assert.equal(workflowLibraryId({library_id:'library-current',id:'old'}),'library-current');
    assert.equal(workflowCategoryId({category_id:'category-current',id:'old'}),'category-current');
    assert.equal(workflowAssetId({id:'legacy'}),'legacy');
});
test('仅同源工作台深链可以作为可编辑来源', () => {
    const origin='http://127.0.0.1:2077';
    assert.equal(workflowSourceId({url:'/static/pages/workflow.html?id=wf-1'},origin),'wf-1');
    for(const url of ['https://example.invalid/static/pages/workflow.html?id=wf-1','javascript:alert(1)',
        '/static/pages/workflow.html?id=..%2Fsecret','/static/other.html?id=wf-1']) {
        assert.equal(workflowSourceId({url},origin),'');
    }
});
test('批量导出限制在取得完整包前拒绝空集合、过多文档和超量字节', () => {
    assert.throws(()=>zipWorkflowFiles([]),/1～100/);
    assert.throws(()=>zipWorkflowFiles(Array(101).fill({name:'file.json',content:'{}'})),/1～100/);
    assert.throws(()=>zipWorkflowFiles([{name:'file.json',content:new Uint8Array(30*1024*1024+1)}]),/超过限制/);
});

function deletionHarness(getDocument, deleteDocument) {
    const source = readFileSync(new URL('../../web/js/modules/asset-manager.js', import.meta.url), 'utf8');
    const start = source.indexOf('async function deleteWorkflowItem(');
    const body = source.slice(start, source.indexOf('\nasync function deleteSelectedWorkflows(', start));
    const frames = [];
    const context = createContext({
        findWorkflowItem: () => ({asset_id:'asset-one'}), workflowSourceId: () => 'workflow-one',
        pendingDeleteAssetId:'', workflowDeleteVersions:new Map(), workflowDeleteBusy:new Set(),
        selectedWorkflowIds:new Set(), selectedWorkflowId:'asset-one',
        assetManagerApi:{getWorkflowDocument:getDocument, deleteWorkflowDocument:deleteDocument},
        apiJsonResponse:async value=>value, loadAll:async()=>{}, setStatus:()=>{},
        render:()=>frames.push(context.workflowDeleteBusy.has('asset-one')),
    });
    runInContext(body, context);
    return {context, frames, remove:()=>runInContext('deleteWorkflowItem("asset-one")', context)};
}

test('取版本期间重复点击不重复请求，确认后仅删除已捕获版本', async () => {
    let release, reads=0;
    const pending=new Promise(resolve=>release=resolve), deleted=[];
    const h=deletionHarness(()=>{reads++;return pending;}, async(id,version)=>deleted.push([id,version]));
    const first=h.remove();
    const duplicate=h.remove();
    release({version:3});await Promise.all([first,duplicate]);
    assert.equal(h.frames[0],true);
    assert.equal(reads,1);assert.deepEqual(deleted,[]);
    assert.equal(h.context.pendingDeleteAssetId,'asset-one');
    assert.equal(h.context.workflowDeleteBusy.size,0);
    await h.remove();
    assert.deepEqual(deleted,[['workflow-one',3]]);
});

test('删除请求期间重复确认只发一次，冲突后解除锁且不沿用旧确认', async () => {
    let reject, deletes=0;
    const pending=new Promise((resolve, fail)=>reject=fail);
    const h=deletionHarness(async()=>({version:2}),()=>{deletes++;return pending;});
    await h.remove();
    const confirmation=h.remove();
    const duplicate=h.remove();
    const settled=Promise.allSettled([confirmation,duplicate]);
    reject(new Error('版本冲突'));const results=await settled;
    assert.equal(deletes,1);
    assert.equal(results[0].status,'rejected');assert.match(results[0].reason.message,/版本冲突/);
    assert.equal(h.context.workflowDeleteBusy.size,0);
    assert.equal(h.context.pendingDeleteAssetId,'');
    assert.equal(h.context.workflowDeleteVersions.size,0);
});
