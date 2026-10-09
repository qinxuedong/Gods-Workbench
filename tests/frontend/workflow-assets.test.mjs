import assert from 'node:assert/strict';
import test from 'node:test';
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
