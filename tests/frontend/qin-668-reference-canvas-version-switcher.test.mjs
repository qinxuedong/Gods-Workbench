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
    return createAssetViewHost({
        tab:'registry',
        fetchImpl:async url=>String(url).includes('/image-versions?')
            ? jsonResponse({versions:VERSIONS})
            : jsonResponse({asset:{}}),
    });
}

async function openReference(host, items){
    host.main.setRegistryAssets(items);
    await host.main.gwCall('preview','showDetailPreview','registry',items[0].id,{
        items, mediaCanvasItems:items, referenceCanvasMode:true,
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

test('QIN-668：参考画布版本切换条与异步选择行为',async()=>{
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
        assert.ok(history,'右侧版本历史管理列表保留');
        assert.equal(history.lastElementChild,switcher,'切换条位于版本历史列表下方');
        assert.deepEqual([...switcher.querySelectorAll('button')].map(button=>button.textContent),['原始','版本2','版本1']);
        assert.equal(switcher.querySelector('[data-detail-canvas-image-version-select="original"]').getAttribute('aria-pressed'),'true');
        assert.ok(history.querySelector('[data-detail-image-version-visibility="version-hidden"]'),'历史列表仍提供隐藏管理');
        assert.ok(history.querySelector('[data-detail-image-version-edit="version-2"]'),'历史列表仍提供编辑管理');
        assert.ok(history.querySelector('[data-detail-image-version-delete="version-2"]'),'历史列表仍提供删除管理');
        assert.equal(switcher.querySelector('[data-detail-image-version-edit],[data-detail-image-version-delete],[data-detail-image-version-visibility]'),null,'切换条不重复版本管理动作');
        assert.equal(state.imageVersionSelectedId,'original');

        state.imageVersions=[state.imageVersions[0],{...VERSIONS[0],is_hidden:true}];
        await host.main.gwCall('preview','renderDetailViewer');
        assert.equal(currentSwitcher(host),null,'只有一个可见版本时不显示切换条');

        state.imageVersions=[state.imageVersions[0],...VERSIONS];
        await setSelection(host,[selected.id,'qin-668-other']);
        assert.equal(currentSwitcher(host),null,'多选时不显示切换条');
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
