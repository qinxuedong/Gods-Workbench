import assert from 'node:assert/strict';
import path from 'node:path';
import test from 'node:test';
import {pathToFileURL} from 'node:url';
import {createAssetViewHost, wait} from './qin-602-asset-view-harness.mjs';

const VERSIONS = [
    {id:'version-2', version_number:2, edit:{crop:{x:0,y:0,w:640,h:360}}, created_at:2},
    {id:'version-1', version_number:1, edit:{crop:{x:0,y:0,w:640,h:360}}, created_at:1},
    {id:'version-hidden', version_number:3, edit:{crop:{x:0,y:0,w:640,h:360}}, is_hidden:true, created_at:3},
];

function image(id, extra={}){
    return {id, asset_id:id, name:`${id}.png`, kind:'image', asset_type:'image', media_type:'image', mime_type:'image/png', url:`/assets/${id}.png`, image_version_selection:'original', ...extra};
}

function installImageLoader(onCreate=()=>{}){
    const previousImage=globalThis.Image;
    const instances=[];
    globalThis.Image=class TestImage{
        constructor(){this.onload=null;this.onerror=null;this._src='';instances.push(this);}
        set src(value){this._src=String(value);if(value)onCreate(this,this._src);}
        get src(){return this._src;}
    };
    return {instances,restore(){if(previousImage===undefined)delete globalThis.Image;else globalThis.Image=previousImage;}};
}

function jsonResponse(body={}){
    return {ok:true,status:200,json:async()=>body};
}

function createHost(){
    const hiddenIds=new Set(VERSIONS.filter(version=>version.is_hidden).map(version=>String(version.id)));
    return createAssetViewHost({
        tab:'registry',
        fetchImpl:async (url,options={})=>{
            const target=String(url);
            const method=String(options.method||'GET').toUpperCase();
            const versionPath=target.match(/\/image-versions\/([^/?]+)$/);
            if(versionPath && options.body){
                const body=JSON.parse(options.body);
                if(Object.hasOwn(body,'hidden')){
                    if(body.hidden) hiddenIds.add(versionPath[1]);
                    else hiddenIds.delete(versionPath[1]);
                }
                return jsonResponse({version:{id:versionPath[1],is_hidden:hiddenIds.has(versionPath[1])}});
            }
            if(target.includes('/image-versions?')){
                const versions=VERSIONS.map(version=>({...version,is_hidden:hiddenIds.has(String(version.id))}));
                return jsonResponse({versions});
            }
            return jsonResponse({asset:{}});
        },
    });
}
async function openReference(host, items){
    return openCanvas(host,items,{referenceCanvasMode:true});
}

async function openCanvas(host, items, options={}){
    host.main.setRegistryAssets(items);
    await host.main.gwCall('preview','showDetailPreview','registry',items[0].id,{
        items, mediaCanvasItems:items, ...options,
    });
    await wait(35);
}

async function setSelection(host, ids){
    const state=host.main.detailViewerState;
    state.mediaCanvasSelectedIds=new Set(ids);
    state.mediaCanvasSelectionOrder=[...ids];
    state.mediaCanvasActiveId=ids[0]||'';
    if(ids[0]){
        const index=state.items.findIndex(item=>item.id===ids[0]);
        if(index>=0){state.index=index;state.id=ids[0];}
    }
    await host.main.gwCall('preview','renderDetailViewer');
}

function click(host, element){
    host.document.dispatchEvent({type:'click',target:element,preventDefault(){},stopPropagation(){}});
}
function keydown(host,key,options={}){
    host.document.dispatchEvent({type:'keydown',target:options.target||host.document.body,key,code:options.code||'',ctrlKey:Boolean(options.ctrlKey),metaKey:Boolean(options.metaKey),shiftKey:Boolean(options.shiftKey),altKey:Boolean(options.altKey),isComposing:false,preventDefault(){},stopPropagation(){}});
}
function pointer(host,type,target,pointerId,clientX,clientY){
    host.document.dispatchEvent({type,target,pointerId,button:0,clientX,clientY,ctrlKey:false,metaKey:false,shiftKey:false,preventDefault(){},stopPropagation(){}});
}

function currentSwitcher(host){
    return host.document.querySelector('.asset-detail-media-version-switcher');
}

function installReplaceWith(doc){
    const prototype=Object.getPrototypeOf(doc.body);
    const previous=prototype.replaceWith;
    const originalQuery=prototype.querySelector;
    const originalQueryAll=prototype.querySelectorAll;
    const previousCss=globalThis.CSS;
    globalThis.CSS={escape:value=>String(value).replace(/[^a-zA-Z0-9_-]/g,'\\$&')};
    const normalize=selector=>String(selector).replace(/:scope\s*>\s*/g,'').replace(/\s*>\s*/g,'>');
    prototype.querySelector=function(selector){return originalQuery.call(this,normalize(selector));};
    prototype.querySelectorAll=function(selector){return originalQueryAll.call(this,normalize(selector));};
    prototype.replaceWith=function(...nodes){
        const parent=this.parentNode;
        const index=parent?.childNodes.indexOf(this) ?? -1;
        if(index<0)return;
        nodes.forEach(node=>{node.parentNode=parent;});
        parent.childNodes.splice(index,1,...nodes);
        this.parentNode=null;
    };
    return ()=>{
        if(previous)prototype.replaceWith=previous;else delete prototype.replaceWith;
        prototype.querySelector=originalQuery;
        prototype.querySelectorAll=originalQueryAll;
        if(previousCss===undefined)delete globalThis.CSS;else globalThis.CSS=previousCss;
    };
}

test('QIN-668：从真实版本结构卡按 · 打开参考画布入口',async()=>{
    const restoreTimers=trackTimers();
    const images=installImageLoader();
    const host=await createHost();
    const restoreDom=installReplaceWith(host.document);
    try{
        const assets=[image('qin-668-structure-current'),image('qin-668-structure-other')];
        const structureId='qin-668-version-structure';
        host.main.setRegistryAssets(assets);
        host.main.registryAssetStructures.set(structureId,{
            id:structureId,
            structure_type:'version',
            current_asset_id:assets[0].id,
            members:assets.map(asset=>({asset_id:asset.id,asset})),
        });
        const card=host.document.createElement('section');
        card.dataset.registryStructureCard=structureId;
        card.dataset.registryStructureType='version';
        host.root.appendChild(card);
        keydown(host,'·',{target:card});
        await wait(45);
        const state=host.main.detailViewerState;
        assert.equal(state.open,true,'结构卡 · 键盘入口打开画布');
        assert.equal(state.referenceCanvasMode,true,'入口进入参考画布模式');
        assert.equal(state.mediaCanvasItems.length,2,'参考画布接收真实结构的两张图片');
        assert.equal(state.mediaCanvasActiveId,assets[0].id,'结构当前素材成为画布当前素材');
        const body=host.document.querySelector('.asset-detail-viewer-body');
        assert.equal(body?.querySelector('.asset-detail-image-versions'),null,'· 入口无左侧版本栏');
        assert.equal(body?.querySelector('.asset-detail-structure-versions'),null,'· 入口无左侧结构卡');
        assert.equal(host.document.querySelectorAll('.asset-detail-media-version-card').length,1,'· 入口右侧只有一个当前版本卡');
        assert.ok(currentSwitcher(host),'· 入口显示右侧版本切换按钮');
    }finally{
        host.dispose();
        restoreDom();
        images.restore();
        restoreTimers();
    }
});

test('QIN-668：从已保存参考画布资产卡打开真实画布入口',async()=>{
    const restoreTimers=trackTimers();
    const images=installImageLoader();
    const host=await createHost();
    const restoreDom=installReplaceWith(host.document);
    try{
        const canvasId='qin-668-saved-reference';
        const items=[
            {...image('qin-668-saved-current'),canvas_id:canvasId,canvas_kind:'reference'},
            {...image('qin-668-saved-other'),canvas_id:canvasId,canvas_kind:'reference'},
        ];
        host.main.canvasAssetsData.items=items;
        host.main.canvasAssetsData.canvases=[{id:canvasId,kind:'reference'}];
        const card=host.document.createElement('article');
        card.dataset.canvasAssetCard=items[0].id;
        host.root.appendChild(card);
        host.root.dispatchEvent({type:'dblclick',target:card,detail:2,preventDefault(){},stopPropagation(){}});
        await wait(45);
        const state=host.main.detailViewerState;
        assert.equal(state.open,true,'双击已保存的画布素材卡打开查看器');
        assert.equal(state.source,'canvas-asset');
        assert.equal(state.referenceCanvasMode,true,'保存的 reference canvas 仍识别为参考画布');
        assert.equal(state.mediaCanvasItems.length,2,'保存画布入口加载其实际关联图片');
        const body=host.document.querySelector('.asset-detail-viewer-body');
        assert.equal(body?.querySelector('.asset-detail-image-versions'),null,'已保存画布入口无左侧版本栏');
        assert.equal(host.document.querySelectorAll('.asset-detail-media-version-card').length,1,'已保存画布入口右侧只有一个当前版本卡');
        assert.ok(currentSwitcher(host),'已保存画布入口显示右侧版本切换按钮');
    }finally{
        host.dispose();
        restoreDom();
        images.restore();
        restoreTimers();
    }
});

test('QIN-668：所有媒体画布入口共用右侧当前版本卡、切换按钮与异步选择',async()=>{
    const restoreTimers=trackTimers();
    const images=installImageLoader();
    const host=await createHost();
    const restoreDom=installReplaceWith(host.document);
    const media=await import(pathToFileURL(path.join(host.tree,'web/js/modules/asset-manager/media-canvas.js')).href);
    try{
        const selected=image('qin-668-selected');
        const other=image('qin-668-other');
        await openReference(host,[selected,other]);
        assert.equal(globalThis.location?.origin,'http://127.0.0.1:2077','测试宿主页面环境应保持安装状态');
        await setSelection(host,[selected.id]);
        const state=host.main.detailViewerState;
        const body=host.document.querySelector('.asset-detail-viewer-body');
        const history=host.document.querySelector('.asset-detail-media-version-history');
        const switcher=currentSwitcher(host);

        assert.ok(body,'参考画布应渲染查看器主体');
        assert.equal(body.querySelector('.asset-detail-image-versions'),null,'不再出现左侧展开栏');
        assert.equal(body.querySelector('[data-detail-image-versions-toggle]'),null,'不再出现左侧折叠窄条');
        assert.equal(body.querySelector('.asset-detail-structure-versions'),null,'合并版本结构卡随左栏移除');
        assert.ok(history,'右侧版本区域保留');
        assert.equal(history.querySelector('.registry-detail-heading'),null,'移除版本历史标题');
        assert.equal(history.querySelectorAll('.registry-detail-image-version').length,1,'右侧只渲染当前版本卡，不显示多版本卡片列表');
        assert.equal(history.querySelector('.asset-detail-media-version-count')?.textContent,'4','计数包含原始、显示和隐藏版本');
        assert.equal(history.querySelector('.asset-detail-media-version-card')?.dataset.detailCurrentImageVersion,'original','唯一卡片对应已提交的原始版本');
        assert.equal(history.lastElementChild,switcher,'切换条位于版本历史列表下方');
        assert.deepEqual([...switcher.querySelectorAll('button')].map(button=>button.textContent),['原始','版本3','版本2','版本1']);
        assert.equal(switcher.querySelector('[data-detail-canvas-image-version-select="original"]').getAttribute('aria-pressed'),'true');
        assert.ok(history.querySelector('[data-detail-image-version-visibility="original"]'),'当前版本卡保留隐藏操作');
        assert.ok(history.querySelector('[data-detail-image-version-edit="original"]'),'当前版本卡保留编辑操作');
        assert.ok(history.querySelector('[data-detail-image-version-delete="original"]'),'当前版本卡保留删除操作');
        assert.equal(history.querySelector('[data-detail-image-version-select]'),null,'版本卡本身不另设切换入口');
        assert.equal(switcher.querySelector('[data-detail-image-version-edit],[data-detail-image-version-delete],[data-detail-image-version-visibility]'),null,'切换条不重复版本管理动作');
        assert.equal(state.imageVersionSelectedId,'original');

        const hiddenButton=currentSwitcher(host).querySelector('[data-detail-canvas-image-version-select="version-hidden"]');
        assert.ok(hiddenButton,'隐藏版本仍保留切换按钮');
        assert.equal(hiddenButton.dataset.hidden,'1','隐藏版本按钮标记灰态');
        assert.equal(hiddenButton.hasAttribute('disabled'),false,'灰态按钮仍可点击以恢复版本');
        click(host,hiddenButton);
        await wait(0);
        const hiddenImage=images.instances.findLast(value=>value.src.includes('/image-versions/version-hidden/media'));
        assert.ok(hiddenImage,'可切换到隐藏版本以访问卡片眼睛');
        hiddenImage.onload();
        await wait(15);
        assert.equal(state.imageVersionSelectedId,'version-hidden','隐藏版本媒体加载后可进入管理卡');
        const hiddenCard=host.document.querySelector('.asset-detail-media-version-card');
        assert.equal(hiddenCard?.querySelector('[data-detail-image-version-visibility]')?.dataset.hidden,'1','隐藏版本卡片显示恢复眼睛');
        click(host,hiddenCard.querySelector('[data-detail-image-version-visibility]'));
        await wait(35);
        assert.equal(state.imageVersions.find(value=>value.id==='version-hidden')?.is_hidden,false,'点击眼睛恢复隐藏版本');
        assert.equal(currentSwitcher(host).querySelector('[data-detail-canvas-image-version-select="version-hidden"]')?.dataset.hidden,'0','恢复后按钮取消灰态');
        click(host,currentSwitcher(host).querySelector('[data-detail-canvas-image-version-select="original"]'));
        await wait(0);
        images.instances.findLast(value=>value.src.endsWith('/media'))?.onload?.();
        await wait(15);
        assert.equal(state.imageVersionSelectedId,'original','恢复测试后回到原始提交版本');

        state.imageVersions=[state.imageVersions[0],{...VERSIONS[0],is_hidden:true}];
        await host.main.gwCall('preview','renderDetailViewer');
        assert.ok(currentSwitcher(host),'仅有一个可见版本时仍显示隐藏版本按钮');
        assert.equal(currentSwitcher(host).querySelector('.is-hidden')?.hasAttribute('disabled'),false,'隐藏版本保持可操作');
        state.imageVersions=[state.imageVersions[0],...VERSIONS];
        await setSelection(host,[selected.id,'qin-668-other']);
        assert.equal(currentSwitcher(host),null,'多选时不显示切换条');
        assert.equal(host.document.querySelector('.asset-detail-media-version-history'),null,'多选时不显示单版本卡');
        await setSelection(host,[]);
        assert.equal(currentSwitcher(host),null,'无选中时不显示切换条');
        const imageItems=state.mediaCanvasItems;
        const videoItem={...selected,kind:'video',asset_type:'video',media_type:'video',mime_type:'video/mp4'};
        state.mediaCanvasItems=imageItems.map(item=>item.id===selected.id?videoItem:item);
        state.items=state.items.map(item=>item.id===selected.id?videoItem:item);
        state.mediaCanvasActiveId=selected.id;
        state.mediaCanvasSelectedIds=new Set([selected.id]);
        state.mediaCanvasSelectionOrder=[selected.id];
        media.refreshDetailMediaCanvasSidebarDetail();
        assert.equal(currentSwitcher(host),null,'视频选择时不显示切换条');
        state.mediaCanvasItems=imageItems;
        state.items=state.items.map(item=>item.id===selected.id?selected:item);

        state.mediaCanvasGroups=[{id:'group-1',memberIds:[selected.id]}];
        state.mediaCanvasActiveId=selected.id;
        state.mediaCanvasSelectedIds=new Set(['group-1']);
        state.mediaCanvasSelectionOrder=['group-1'];
        media.refreshDetailMediaCanvasSidebarDetail();
        assert.equal(currentSwitcher(host),null,'组卡选择时不显示切换条');
        state.mediaCanvasGroups=[];
        await setSelection(host,[selected.id]);
        state.imageVersions=[{id:'original',asset_id:selected.id,version_number:0,is_original:true,is_hidden:false},...VERSIONS];
        state.imageVersionsAssetId=selected.id;
        state.imageVersionSelectedId='original';
        await host.main.gwCall('preview','renderDetailViewer');
        const originalUrl=state.mediaCanvasItems[0].url;
        assert.ok(currentSwitcher(host),JSON.stringify({active:state.mediaCanvasActiveId,selected:[...state.mediaCanvasSelectedIds],order:state.mediaCanvasSelectionOrder,versionsAsset:state.imageVersionsAssetId,loading:state.imageVersionsLoading,versions:state.imageVersions.map(version=>[version.id,version.is_hidden])}));
        click(host,currentSwitcher(host).querySelector('[data-detail-canvas-image-version-select="version-2"]'));
        await wait(0);

        assert.equal(state.imageVersionSwitchPendingId,'version-2');
        assert.equal(state.imageVersionSelectedId,'original','pending 阶段不提交版本高亮');
        assert.equal(state.mediaCanvasItems[0].url,originalUrl,'pending 阶段保留画布原图');
        assert.equal(currentSwitcher(host).querySelector('[data-detail-canvas-image-version-select="original"]').getAttribute('aria-pressed'),'true');
        const firstPendingImage=images.instances.find(image=>image.src.includes('/image-versions/version-2/media'));
        assert.ok(firstPendingImage,images.instances.map(image=>image.src).join('\n'));

        firstPendingImage.onload();
        await wait(15);
        assert.equal(state.imageVersionSwitchPendingId,'');
        assert.equal(state.imageVersionSelectedId,'version-2');
        assert.match(state.mediaCanvasItems[0].url,/image-versions\/version-2\/media$/);
        assert.equal(currentSwitcher(host).querySelector('[data-detail-canvas-image-version-select="version-2"]').getAttribute('aria-pressed'),'true');
        const selectedCard=host.document.querySelector('.asset-detail-media-version-card');
        assert.equal(selectedCard?.dataset.detailCurrentImageVersion,'version-2','切换成功后唯一版本卡更新为当前版本');
        assert.ok(selectedCard?.querySelector('[data-detail-image-version-visibility="version-2"]'),'隐藏操作绑定当前版本');
        assert.ok(selectedCard?.querySelector('[data-detail-image-version-edit="version-2"]'),'编辑操作绑定当前版本');
        assert.ok(selectedCard?.querySelector('[data-detail-image-version-delete="version-2"]'),'删除操作绑定当前版本');

        click(host,currentSwitcher(host).querySelector('[data-detail-canvas-image-version-select="version-1"]'));
        await wait(0);
        const failedImage=images.instances.findLast(image=>image.src.includes('/image-versions/version-1/media'));
        failedImage.onerror();
        await wait(5);
        assert.equal(state.imageVersionSelectedId,'version-2','加载失败保留已提交版本');
        assert.match(currentSwitcher(host).querySelector('[role="alert"]').textContent,/版本加载失败/);

        click(host,currentSwitcher(host).querySelector('[data-detail-canvas-image-version-select="version-1"]'));
        await wait(0);
        const staleImage=images.instances.findLast(image=>image.src.includes('/image-versions/version-1/media'));
        const staleOnload=staleImage.onload;
        click(host,currentSwitcher(host).querySelector('[data-detail-canvas-image-version-select="version-2"]'));
        await wait(0);
        staleOnload?.();
        await wait(0);
        assert.equal(state.imageVersionSelectedId,'version-2','较早的迟到响应不能抢先提交');
        assert.equal(state.imageVersionSwitchPendingId,'version-2','快速连点后只显示最新目标 pending');
        const latestImage=images.instances.findLast(image=>image.src.includes('/image-versions/version-2/media'));
        latestImage.onload();
        await wait(15);
        assert.equal(state.imageVersionSelectedId,'version-2','只提交最新成功请求');

        click(host,currentSwitcher(host).querySelector('[data-detail-canvas-image-version-select="version-1"]'));
        await wait(0);
        const closeImage=images.instances.findLast(image=>image.src.includes('/image-versions/version-1/media'));
        const closeOnload=closeImage.onload;
        media.activateDetailMediaCanvasItem(other.id,false);
        closeOnload?.();
        await wait(5);
        assert.match(state.mediaCanvasItems.find(item=>item.id===selected.id).url,/image-versions\/version-2\/media$/,'切换画布节点后旧请求不能改写原节点的已提交版本');
        assert.equal(state.mediaCanvasItems.find(item=>item.id===other.id).url,`/api/asset-registry/assets/${other.id}/media`,'迟到响应不能把旧版本写到新选中的节点');
        await host.main.gwCall('preview','renderDetailViewer');
        click(host,currentSwitcher(host)?.querySelector('[data-detail-canvas-image-version-select="version-1"]'));
        await wait(0);
        const lateOnClose=images.instances[5].onload;
        host.main.closeDetailPreview();
        lateOnClose?.();
        await wait(5);
        assert.equal(host.main.detailViewerState.open,false,'关闭后旧响应不重新提交画布状态');
    } finally {
        host.dispose();
        restoreDom();
        images.restore();
        restoreTimers();
    }
});

test('QIN-668：marquee 框选清空选择时取消待切换并刷新版本区',async()=>{
    const restoreTimers=trackTimers();
    const images=installImageLoader();
    const host=await createHost();
    const restoreDom=installReplaceWith(host.document);
    try{
        const selected=image('qin-668-marquee-selected');
        const other=image('qin-668-marquee-other');
        await openReference(host,[selected,other]);
        await setSelection(host,[selected.id]);
        click(host,currentSwitcher(host).querySelector('[data-detail-canvas-image-version-select="version-2"]'));
        await wait(0);
        assert.equal(host.main.detailViewerState.imageVersionSwitchPendingId,'version-2');
        const viewport=host.document.querySelector('[data-detail-media-canvas-viewport]');
        pointer(host,'pointerdown',viewport,6681,9000,9000);
        pointer(host,'pointermove',viewport,6681,9010,9010);
        pointer(host,'pointerup',viewport,6681,9010,9010);
        assert.equal(host.main.detailViewerState.imageVersionSwitchPendingId,'','框选结束取消挂起的版本媒体请求');
        assert.equal(host.main.detailViewerState.mediaCanvasSelectedIds.size,0,'空白 marquee 取消单图选择');
        assert.equal(host.document.querySelector('.asset-detail-media-version-history'),null,'侧栏同步移除不再适用的版本区');
    }finally{
        host.dispose();
        restoreDom();
        images.restore();
        restoreTimers();
    }
});

test('QIN-668：Ctrl+A 取消单图待切换并刷新多选侧栏',async()=>{
    const restoreTimers=trackTimers();
    const images=installImageLoader();
    const host=await createHost();
    const restoreDom=installReplaceWith(host.document);
    try{
        const selected=image('qin-668-ctrl-a-selected');
        const other=image('qin-668-ctrl-a-other');
        await openReference(host,[selected,other]);
        await setSelection(host,[selected.id]);
        click(host,currentSwitcher(host).querySelector('[data-detail-canvas-image-version-select="version-2"]'));
        await wait(0);
        keydown(host,'a',{ctrlKey:true});
        await wait(0);
        const state=host.main.detailViewerState;
        assert.equal(state.imageVersionSwitchPendingId,'','Ctrl+A 先取消待切换请求');
        assert.equal(state.mediaCanvasSelectedIds.size,2,'Ctrl+A 选择所有画布对象');
        assert.equal(host.document.querySelector('.asset-detail-media-version-history'),null,'多选状态刷新后隐藏单图版本区');
    }finally{
        host.dispose();
        restoreDom();
        images.restore();
        restoreTimers();
    }
});

test('QIN-668：删除当前节点取消待切换并加载下一节点版本',async()=>{
    const restoreTimers=trackTimers();
    const images=installImageLoader();
    const host=await createHost();
    const restoreDom=installReplaceWith(host.document);
    try{
        const selected=image('qin-668-delete-selected');
        const next=image('qin-668-delete-next');
        await openReference(host,[selected,next]);
        await setSelection(host,[selected.id]);
        click(host,currentSwitcher(host).querySelector('[data-detail-canvas-image-version-select="version-2"]'));
        await wait(0);
        keydown(host,'Delete');
        await wait(60);
        const state=host.main.detailViewerState;
        assert.equal(state.imageVersionSwitchPendingId,'','删除节点取消待切换请求');
        assert.equal(state.mediaCanvasActiveId,next.id,'删除当前节点后选中下一节点');
        assert.equal(state.imageVersionsAssetId,next.id,'下一节点加载自己的版本列表');
        assert.equal(state.imageVersionsLoading,false,'下一节点版本列表已加载完成');
        assert.equal(host.document.querySelector('.asset-detail-media-version-card')?.dataset.detailCurrentImageVersion,'original','右侧卡片切换到下一节点的原始版本');
    }finally{
        host.dispose();
        restoreDom();
        images.restore();
        restoreTimers();
    }
});

test('QIN-668：保存后卡片按当前版本动态计算缩略图，不使用过期缓存字段',async()=>{
    const restoreTimers=trackTimers();
    const host=await createHost();
    const restoreDom=installReplaceWith(host.document);
    try{
        const selected=image('qin-668-preview-refresh');
        await openCanvas(host,[selected]);
        await setSelection(host,[selected.id]);
        const state=host.main.detailViewerState;
        const savedVersion={id:'version-after-save',version_number:4,edit:{crop:{x:0,y:0,w:640,h:360}},created_at:4};
        state.imageVersions=[state.imageVersions[0],savedVersion];
        state.imageVersionSelectedId=savedVersion.id;
        state.imageVersionPreviewUrl='/stale-thumbnail.png';
        await host.main.gwCall('preview','renderDetailViewer');
        const thumbnail=host.document.querySelector('.asset-detail-media-version-card img');
        assert.equal(thumbnail?.getAttribute('src'),'/api/asset-registry/assets/qin-668-preview-refresh/image-versions/version-after-save/media?w=220');
        assert.notEqual(thumbnail?.getAttribute('src'),state.imageVersionPreviewUrl,'渲染始终根据当前资产与版本计算缩略图');
    }finally{
        host.dispose();
        restoreDom();
        restoreTimers();
    }
});

function trackTimers(){
    const originalTimeout=globalThis.setTimeout,originalInterval=globalThis.setInterval;
    const timeouts=new Set(),intervals=new Set();
    globalThis.setTimeout=(...args)=>{const id=originalTimeout(...args);timeouts.add(id);return id;};
    globalThis.setInterval=(...args)=>{const id=originalInterval(...args);id?.unref?.();intervals.add(id);return id;};
    return ()=>{
        for(const id of timeouts)clearTimeout(id);
        for(const id of intervals)clearInterval(id);
        globalThis.setTimeout=originalTimeout;
        globalThis.setInterval=originalInterval;
    };
}

test('QIN-668：参考画布版本媒体慢加载 1500ms / 5000ms 时保留旧画面',async()=>{
    const restoreTimers=trackTimers();
    const images=installImageLoader();
    const host=await createHost();
    const restoreDom=installReplaceWith(host.document);
    try{
        const selected=image('qin-668-slow');
        await openReference(host,[selected]);
        await setSelection(host,[selected.id]);
        const state=host.main.detailViewerState;
        state.imageVersions=[{id:'original',asset_id:selected.id,version_number:0,is_original:true,is_hidden:false},...VERSIONS];
        state.imageVersionsAssetId=selected.id;
        state.imageVersionSelectedId='original';
        await host.main.gwCall('preview','renderDetailViewer');
        const originalUrl=state.mediaCanvasItems[0].url;
        for(const [versionId,delay,expectedSelected] of [['version-2',1500,'original'],['version-1',5000,'version-2']]){
            click(host,currentSwitcher(host).querySelector(`[data-detail-canvas-image-version-select="${versionId}"]`));
            await wait(delay);
            assert.equal(state.imageVersionSwitchPendingId,versionId,`${delay}ms 内保持 pending`);
            assert.equal(state.imageVersionSelectedId,expectedSelected,`${delay}ms 内保留已提交版本`);
            assert.equal(state.mediaCanvasItems[0].url,versionId==='version-2'?originalUrl:`/api/asset-registry/assets/${selected.id}/image-versions/version-2/media`,`${delay}ms 内保留画布旧图`);
            const request=images.instances.findLast(image=>image.src.includes(`/image-versions/${versionId}/media`));
            assert.ok(request,`${delay}ms 请求保持可跟踪`);
            request.onload();
            await wait(20);
            assert.equal(state.imageVersionSwitchPendingId,'');
            assert.equal(state.imageVersionSelectedId,versionId,`${delay}ms 后才提交目标版本`);
        }
    }finally{
        host.dispose();
        restoreDom();
        images.restore();
        restoreTimers();
    }
});

test('QIN-668：普通媒体画布入口也使用右侧单卡和切换按钮',async()=>{
    const restoreTimers=trackTimers();
    const images=installImageLoader();
    const host=await createHost();
    const restoreDom=installReplaceWith(host.document);
    try{
        const selected=image('qin-668-regular-entry');
        await openCanvas(host,[selected]);
        const state=host.main.detailViewerState;
        await setSelection(host,[selected.id]);
        state.referenceCanvasMode=false;
        await host.main.gwCall('preview','renderDetailViewer');
        const body=host.document.querySelector('.asset-detail-viewer-body');
        assert.equal(state.referenceCanvasMode,false,'此案例覆盖非参考画布入口');
        assert.equal(body?.querySelector('.asset-detail-image-versions'),null,'普通媒体画布入口也不显示左侧版本栏');
        assert.equal(body?.querySelector('[data-detail-image-versions-toggle]'),null,'普通入口不保留折叠窄条');
        assert.equal(body?.querySelector('.asset-detail-structure-versions'),null,'普通入口不显示左侧结构卡');
        assert.equal(host.document.querySelectorAll('.asset-detail-media-version-history .registry-detail-image-version').length,1,'普通入口右侧只显示一张当前版本卡');
        assert.ok(currentSwitcher(host),'普通入口也显示版本切换按钮');
        click(host,currentSwitcher(host).querySelector('[data-detail-canvas-image-version-select="version-2"]'));
        await wait(0);
        assert.equal(state.imageVersionSelectedId,'original','普通入口 pending 不提前提交当前版本');
        const request=images.instances.findLast(value=>value.src.includes('/image-versions/version-2/media'));
        assert.ok(request,'普通入口复用异步媒体加载');
        request.onload();
        await wait(15);
        assert.equal(state.imageVersionSelectedId,'version-2','普通入口加载成功后提交当前版本');
        assert.equal(host.document.querySelector('.asset-detail-media-version-card')?.dataset.detailCurrentImageVersion,'version-2','普通入口右侧卡片同步到新版本');
    }finally{
        host.dispose();
        restoreDom();
        images.restore();
        restoreTimers();
    }
});
