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

function sourceBetween(source, startMarker, endMarker) {
    const start = source.indexOf(startMarker);
    const end = source.indexOf(endMarker, start);
    assert.notEqual(start, -1, `missing source marker: ${startMarker}`);
    assert.notEqual(end, -1, `missing source marker: ${endMarker}`);
    return source.slice(start, end);
}

function registryTypeHarness() {
    const source = readFileSync(new URL('../../web/js/modules/asset-manager.js', import.meta.url), 'utf8');
    const assetKindSource = sourceBetween(source, 'function assetKind(item){', '\nfunction assetClassificationEntriesForLibrary');
    const viewerTypeSource = sourceBetween(source, 'function detailViewerType(source, item={}){', '\nfunction detailViewerTypeLabel');
    const coverflowKindSource = sourceBetween(source, 'function registryCoverflowSourceMediaKind(source, item={}){', '\nfunction registryCoverflowSourceIsImage');
    const extension = item => {
        const value = String(item.name || item.relative_path || item.relativePath || item.file || item.url || item.public_url || '')
            .split('?')[0].split('#')[0];
        return value.toLowerCase().match(/\.([a-z0-9]+)$/)?.[1] || '';
    };
    const context = createContext({
        workflowSourceId: item => workflowSourceId(item, 'http://127.0.0.1:2077'),
        assetFileExtension: extension,
        assetSupportsImagePreview: item => extension(item) === 'png',
        ASSET_VIDEO_EXTENSIONS: new Set(['mp4', 'webm']),
        ASSET_3D_PREVIEW_EXTENSIONS: ['glb'],
        ASSET_FONT_PREVIEW_EXTENSIONS: ['otf'],
        ASSET_AUDIO_PREVIEW_EXTENSIONS: ['mp3'],
        ASSET_IMAGE_PREVIEW_EXTENSIONS: new Set(['png']),
        ASSET_DOCUMENT_PREVIEW_EXTENSIONS: ['pdf'],
    });
    runInContext(`${assetKindSource}\n${viewerTypeSource}\n${coverflowKindSource}`, context);
    return context;
}

test('工作流深链即使名称带图片扩展名也不分类为媒体', () => {
    const h = registryTypeHarness();
    const item = {name:'workflow-preview.png', url:'/static/pages/workflow.html?id=wf-preview'};
    assert.equal(h.assetKind(item), 'workflow');
    assert.equal(h.detailViewerType('registry', item), 'workflow');
    assert.equal(h.registryCoverflowSourceMediaKind('registry', {...item, kind:'image'}), '');
});

function workflowCanvasHarness() {
    const source = readFileSync(new URL('../../web/js/modules/asset-manager.js', import.meta.url), 'utf8');
    const itemsSource = sourceBetween(source, 'function detailMediaCanvasItems(){', '\nfunction detailMediaCanvasPersistId');
    const nodeSource = sourceBetween(source, 'function renderDetailMediaCanvasNode(source, item){', '\nfunction preloadDetailViewerNeighbors');
    const item = {id:'workflow-canvas-item', name:'workflow-preview.png', url:'/static/pages/workflow.html?id=wf-preview'};
    const context = createContext({
        detailViewerState:{source:'registry', mediaCanvasItems:[item], mediaCanvasLayout:{}},
        detailViewerType:(_source, value) => workflowSourceId(value, 'http://127.0.0.1:2077') ? 'workflow' : 'image',
        detailViewerUrl:(_source, value) => value.url,
        detailMediaCanvasPersistId:() => 'workflow-asset',
        detailMediaCanvasNaturalSize:() => ({width:320, height:180}),
        detailMediaCanvasGroupForMember:() => null,
        detailMediaCanvasSelectedIds:() => new Set(),
        assetPreviewUrl:url => url,
        workflowSourceId:value => workflowSourceId(value, 'http://127.0.0.1:2077'),
        workflowThumb:() => '<div class="workflow-file-icon"><i></i></div>',
        escapeAttr:value => String(value ?? ''),
    });
    runInContext(`${itemsSource}\n${nodeSource}\nrendered = renderDetailMediaCanvasNode('registry', detailViewerState.mediaCanvasItems[0])`, context);
    return context;
}

test('工作流深链不会进入画布媒体列表或生成媒体元素', () => {
    const h = workflowCanvasHarness();
    assert.equal(h.detailMediaCanvasItems().length, 0);
    assert.match(h.rendered, /workflow-file-icon/);
    assert.doesNotMatch(h.rendered, /<(?:img|video)\b/i);
    assert.doesNotMatch(h.rendered, /workflow\.html/i);
});

function marqueeHarness(zone) {
    const source = readFileSync(new URL('../../web/js/modules/asset-manager.js', import.meta.url), 'utf8');
    const beginSource = sourceBetween(source, 'function beginMarqueeSelection(event){', '\nfunction updateMarqueeSelection');
    let created = 0, prevented = 0;
    const area = {appendChild(){}};
    const detail = {};
    const target = {
        closest(selector) {
            if(zone === 'detail' && selector.includes('[data-registry-detail]')) return detail;
            if(zone === 'player' && selector.includes('.asset-detail-player')) return detail;
            if(selector === '.content-scroll') return area;
            return null;
        },
    };
    const context = createContext({
        marqueeTargetSelector: () => '[data-registry-card]',
        activeTab:'registry',
        assetBoardById:() => null,
        registrySelectedIds:new Set(),
        selectedAssetIds:new Set(),
        selectedPromptIds:new Set(),
        selectedLocalUploadIds:new Set(),
        selectedCanvasAssetIds:new Set(),
        document:{createElement:() => { created += 1; return {style:{}}; }},
        updateMarqueeSelection:() => {},
    });
    context.event = {
        button:0,
        clientX:10,
        clientY:10,
        target,
        preventDefault:() => { prevented += 1; },
    };
    runInContext(`${beginSource}\nbeginMarqueeSelection(event)`, context);
    return {context, created:() => created, prevented:() => prevented};
}

test('详情面板与预览播放器中的指针按下不启动框选', () => {
    for(const zone of ['detail', 'player']) {
        const h = marqueeHarness(zone);
        assert.equal(h.context.marqueeState, undefined, zone);
        assert.equal(h.created(), 0, zone);
        assert.equal(h.prevented(), 0, zone);
    }
});

function endMarqueeHarness(hasSelection) {
    const source = readFileSync(new URL('../../web/js/modules/asset-manager.js', import.meta.url), 'utf8');
    const clearSource = sourceBetween(source, 'function clearRegistrySelectionDom(){', '\nfunction renderRegistryToolbar');
    const endSource = sourceBetween(source, 'function endMarqueeSelection(){', '\nasync function createAssetLibrary');
    let renders = 0, syncs = 0;
    const context = createContext({
        marqueeState:{mode:'registry', moved:false, box:{remove(){}}},
        pendingBatchDelete:'batch',
        selectedRegistryAssetId:hasSelection ? 'asset-one' : '',
        registryDetail:hasSelection ? {id:'asset-one'} : null,
        registrySelectedIds:new Set(hasSelection ? ['asset-one'] : []),
        registrySelectionAnchorId:hasSelection ? 'asset-one' : '',
        registryManageMode:hasSelection,
        registryBulkPanel:hasSelection ? 'tags' : '',
        syncRegistrySelectionDom:() => { syncs += 1; },
        render:() => { renders += 1; },
    });
    runInContext(`${clearSource}\n${endSource}\nendMarqueeSelection()`, context);
    return {context, renders:() => renders, syncs:() => syncs};
}

test('无位移空白点击只在存在选择时局部清选中状态', () => {
    const empty = endMarqueeHarness(false);
    assert.equal(empty.renders(), 0);
    assert.equal(empty.syncs(), 0);

    const selected = endMarqueeHarness(true);
    assert.equal(selected.renders(), 0);
    assert.equal(selected.syncs(), 1);
    assert.equal(selected.context.selectedRegistryAssetId, '');
    assert.equal(selected.context.registryDetail, null);
    assert.equal(selected.context.registrySelectedIds.size, 0);
});
