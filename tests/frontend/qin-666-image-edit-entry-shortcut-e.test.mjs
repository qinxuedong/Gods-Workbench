import assert from 'node:assert/strict';
import path from 'node:path';
import test from 'node:test';
import {pathToFileURL} from 'node:url';
import {createAssetViewHost, wait} from './qin-602-asset-view-harness.mjs';

function image(id){
    return {
        id, asset_id:id, name:`${id}.png`, kind:'image', mime_type:'image/png',
        url:`/assets/${id}.png`, media_url:`/assets/${id}.png`,
        image_version_selection:'original',
    };
}
function video(id){
    return {id,asset_id:id,name:`${id}.mp4`,kind:'video',mime_type:'video/mp4',url:`/assets/${id}.mp4`,media_url:`/assets/${id}.mp4`};
}
function keyboard(target, key='e', extra={}){
    return {
        type:'keydown',target,key,code:key.toLowerCase() === 'e' ? 'KeyE' : '',
        repeat:false,ctrlKey:false,metaKey:false,altKey:false,isComposing:false,
        defaultPrevented:false,prevented:false,stopped:false,
        preventDefault(){this.defaultPrevented=true;this.prevented=true;},
        stopPropagation(){this.stopped=true;},
        ...extra,
    };
}
function pointer(type, target, pointerId, clientX=20, clientY=20){
    return {type,target,pointerId,button:0,clientX,clientY,shiftKey:false,ctrlKey:false,metaKey:false,
        preventDefault(){this.defaultPrevented=true;},stopPropagation(){}};
}
function trackTimers(){
    const originalTimeout=globalThis.setTimeout,originalInterval=globalThis.setInterval;
    const timeouts=new Set(),intervals=new Set();
    globalThis.setTimeout=(...args)=>{const id=originalTimeout(...args);timeouts.add(id);return id;};
    globalThis.setInterval=(...args)=>{const id=originalInterval(...args);id?.unref?.();intervals.add(id);return id;};
    return ()=>{for(const id of timeouts) clearTimeout(id);for(const id of intervals) clearInterval(id);globalThis.setTimeout=originalTimeout;globalThis.setInterval=originalInterval;};
}
const mockFetch=async()=>({ok:true,status:200,json:async()=>({versions:[],items:[],total:0})});
function createHost({fetchImpl=mockFetch,...options}={}){return createAssetViewHost({...options,fetchImpl});}
function installImageLoader(createImage){
    const previousImage=globalThis.Image;
    const instances=[];
    globalThis.Image=class TestImage{
        constructor(){this.naturalWidth=320;this.naturalHeight=180;this.width=320;this.height=180;instances.push(this);}
        set src(value){this._src=value;createImage?.(this,value);}
        get src(){return this._src||'';}
    };
    return {instances,restore(){if(previousImage===undefined) delete globalThis.Image; else globalThis.Image=previousImage;}};
}
async function loadCoverflow(host){
    await import(pathToFileURL(path.join(host.tree,'web/js/modules/asset-manager/coverflow.js')));
}
function installCanvasAndImageStubs(doc){
    const previousImage=globalThis.Image;
    const previousCss=globalThis.CSS,previousHTMLElement=globalThis.HTMLElement;
    globalThis.Image=class MockImage{
        constructor(){this.naturalWidth=320;this.naturalHeight=180;this.width=320;this.height=180;}
        set src(value){this._src=value;queueMicrotask(()=>this.onload?.());}
        get src(){return this._src||'';}
    };
    const prototype=Object.getPrototypeOf(doc.body);
    globalThis.HTMLElement=doc.body.constructor;
    globalThis.CSS={escape:value=>String(value).replace(/[^a-zA-Z0-9_-]/g,'\\$&')};
    const originalContext=prototype.getContext,originalQuery=prototype.querySelector,originalQueryAll=prototype.querySelectorAll;
    const originalAppend=prototype.append,originalReplaceWith=prototype.replaceWith;
    const originalConnected=Object.getOwnPropertyDescriptor(prototype,'isConnected');
    const originalCreate=doc.createElement.bind(doc);
    const normalizeSelector=selector=>String(selector).replace(/:scope\s*>\s*/g,'').replace(/\s*>\s*/g,'>');
    prototype.querySelector=function(selector){return originalQuery.call(this,normalizeSelector(selector));};
    prototype.querySelectorAll=function(selector){return originalQueryAll.call(this,normalizeSelector(selector));};
    if(typeof prototype.append!=='function') prototype.append=function(...nodes){nodes.forEach(node=>this.appendChild(typeof node==='string'?doc.createTextNode(node):node));};
    if(typeof prototype.replaceWith!=='function') prototype.replaceWith=function(...nodes){
        const parent=this.parentNode;if(!parent)return;
        const index=parent.childNodes.indexOf(this);if(index<0)return;
        const replacements=nodes.map(node=>typeof node==='string'?doc.createTextNode(node):node);
        replacements.forEach(node=>{node.parentNode=parent;});
        parent.childNodes.splice(index,1,...replacements);this.parentNode=null;
    };
    if(!originalConnected) Object.defineProperty(prototype,'isConnected',{configurable:true,get(){let node=this;while(node?.parentNode)node=node.parentNode;return node===doc.documentElement;}});
    doc.createElement=tag=>{
        const node=originalCreate(tag);
        if(String(tag).toLowerCase()==='template'){
            Object.defineProperty(node,'content',{configurable:true,get(){return this;}});
            Object.defineProperty(node,'firstElementChild',{configurable:true,get(){return this.children[0]||null;}});
        }
        return node;
    };
    prototype.getContext=function(){
        if(this.localName!=='canvas') return originalContext?.apply(this,arguments) || null;
        return {save(){},restore(){},clearRect(){},fillRect(){},strokeRect(){},beginPath(){},moveTo(){},lineTo(){},stroke(){},fill(){},translate(){},scale(){},rotate(){},drawImage(){},setTransform(){},measureText:text=>({width:String(text||'').length*8}),fillText(){},setLineDash(){}};
    };
    return ()=>{
        prototype.getContext=originalContext;
        prototype.querySelector=originalQuery;
        prototype.querySelectorAll=originalQueryAll;
        if(originalAppend) prototype.append=originalAppend; else delete prototype.append;
        if(originalReplaceWith) prototype.replaceWith=originalReplaceWith; else delete prototype.replaceWith;
        if(originalConnected) Object.defineProperty(prototype,'isConnected',originalConnected); else delete prototype.isConnected;
        doc.createElement=originalCreate;
        if(previousImage===undefined) delete globalThis.Image; else globalThis.Image=previousImage;
        if(previousCss===undefined) delete globalThis.CSS; else globalThis.CSS=previousCss;
        if(previousHTMLElement===undefined) delete globalThis.HTMLElement; else globalThis.HTMLElement=previousHTMLElement;
    };
}
async function openReference(host, assets, canvasItems=assets){
    host.main.setRegistryAssets(assets);
    await host.main.gwCall('preview','showDetailPreview','registry',assets[0].id,{
        items:assets,mediaCanvasItems:canvasItems,referenceCanvasMode:true,
    });
    await wait(25);
}
async function selectCanvas(host, ids){
    const state=host.main.detailViewerState;
    state.mediaCanvasSelectedIds=new Set(ids);
    state.mediaCanvasSelectionOrder=[...ids];
    state.mediaCanvasActiveId=ids[0]||'';
    if(ids[0]){
        const index=state.items.findIndex(item=>item.id===ids[0]);
        if(index>=0){state.index=index;state.id=ids[0];}
    }
    await host.main.gwCall('preview','renderDetailViewer');
    await wait(15);
}

test('参考画布编辑入口只对唯一图片启用，点击编辑所选图而非初始预览图',async()=>{
    const restoreTimers=trackTimers();
    const host=await createHost({tab:'registry'});
    const restore=installCanvasAndImageStubs(host.document);
    try{
        const first=image('qin-666-first'),selected=image('qin-666-selected'),other=image('qin-666-other'),clip=video('qin-666-video');
        await openReference(host,[first,selected,other,clip],[first,selected,clip]);
        const state=host.main.detailViewerState;
        state.mediaCanvasContextMenu={open:true,x:12,y:12};

        await selectCanvas(host,[]);
        let toolbar=host.document.querySelector('.asset-detail-media-canvas-toolbar [data-detail-media-context-action="edit-image"]');
        let menu=host.document.querySelector('[data-detail-media-context-menu] [data-detail-media-context-action="edit-image"]');
        assert.ok(toolbar?.hasAttribute('disabled'),'空选时工具栏入口置灰');
        assert.ok(menu?.hasAttribute('disabled'),'空选时右键入口置灰');

        await selectCanvas(host,[selected.id,first.id]);
        toolbar=host.document.querySelector('.asset-detail-media-canvas-toolbar [data-detail-media-context-action="edit-image"]');
        assert.ok(toolbar?.hasAttribute('disabled'),'多选时工具栏入口置灰');

        await selectCanvas(host,[clip.id]);
        toolbar=host.document.querySelector('.asset-detail-media-canvas-toolbar [data-detail-media-context-action="edit-image"]');
        assert.ok(toolbar?.hasAttribute('disabled'),'视频选择时工具栏入口置灰');

        await selectCanvas(host,[selected.id]);
        toolbar=host.document.querySelector('.asset-detail-media-canvas-toolbar [data-detail-media-context-action="edit-image"]');
        menu=host.document.querySelector('[data-detail-media-context-menu] [data-detail-media-context-action="edit-image"]');
        assert.ok(toolbar && !toolbar.hasAttribute('disabled'));
        assert.ok(menu && !menu.hasAttribute('disabled'));
        assert.equal(toolbar.getAttribute('aria-label'),'编辑图片','窄屏图标按钮仍有明确名称');
        assert.equal(menu.getAttribute('aria-label'),'编辑图片','右键菜单项有明确名称');
        const click={type:'click',target:toolbar};
        host.document.dispatchEvent(click);
        await wait(40);
        assert.equal(host.main.detailImageEditorState?.itemId,selected.id,'编辑器使用画布唯一选中图片');
        assert.equal(host.main.detailViewerState.mediaCanvasActiveId,selected.id,'打开编辑后画布仍保留选中项');
    }finally{restore();host.dispose();restoreTimers();}
});

test('1500ms 慢图加载保持进行中且同图连按只创建一次加载',async()=>{
    const restoreTimers=trackTimers();
    const host=await createHost({tab:'registry'});
    const restore=installCanvasAndImageStubs(host.document);
    let loader;
    try{
        const target=image('qin-666-slow-image');
        await openReference(host,[target],[target]);
        await selectCanvas(host,[target.id]);
        loader=installImageLoader(image=>setTimeout(()=>image.onload?.(),1500));
        const toolbar=host.document.querySelector('.asset-detail-media-canvas-toolbar [data-detail-media-context-action="edit-image"]');
        const start=Date.now();
        host.document.dispatchEvent({type:'click',target:toolbar});
        await wait(40);
        host.document.dispatchEvent({type:'click',target:toolbar});
        await wait(100);
        assert.equal(loader.instances.length,1,'同图并发请求复用同一 Image 加载');
        assert.equal(host.main.detailImageEditorState,null,'慢图未加载前不显示半成品编辑器');
        assert.match(host.document.body.textContent,/正在打开单图影院编辑器/,'慢加载期间不误报就绪');
        await wait(Math.max(0,1600-(Date.now()-start)));
        assert.equal(host.main.detailImageEditorState?.itemId,target.id,'1500ms 后打开正确图片');
    }finally{loader?.restore();restore();host.dispose();restoreTimers();}
});

test('5000ms 慢版本查询保持进行中，版本返回后再加载图片',async()=>{
    const restoreTimers=trackTimers();
    let delayVersionResponse=false;
    let versionCalls=0;
    const host=await createHost({tab:'registry',fetchImpl:(input,init)=>{
        if(delayVersionResponse && String(input).includes('/image-versions?')){
            versionCalls+=1;
            return new Promise(resolve=>setTimeout(()=>resolve({ok:true,status:200,json:async()=>({versions:[]})}),5000));
        }
        return mockFetch(input,init);
    }});
    const restore=installCanvasAndImageStubs(host.document);
    let loader;
    try{
        const target={...image('qin-666-slow-version'),image_version_selection:''};
        await openReference(host,[target],[target]);
        await selectCanvas(host,[target.id]);
        await wait(100);
        host.main.detailViewerState.mediaCanvasItems[0].image_version_selection='';
        delayVersionResponse=true;
        loader=installImageLoader(image=>queueMicrotask(()=>image.onload?.()));
        const toolbar=host.document.querySelector('.asset-detail-media-canvas-toolbar [data-detail-media-context-action="edit-image"]');
        const start=Date.now();
        host.document.dispatchEvent({type:'click',target:toolbar});
        await wait(1500);
        assert.equal(versionCalls,1);
        assert.equal(loader.instances.length,0,`版本查询未结束前不创建图像: ${JSON.stringify(loader.instances.map(value=>value.src))}`);
        assert.equal(host.main.detailImageEditorState,null);
        assert.match(host.document.body.textContent,/正在打开单图影院编辑器/,'5000ms 慢响应期间保持进行中');
        await wait(Math.max(0,5100-(Date.now()-start)));
        assert.equal(host.main.detailImageEditorState?.itemId,target.id,'慢版本查询完成后进入编辑');
        assert.equal(loader.instances.length,1);
    }finally{loader?.restore();restore();host.dispose();restoreTimers();}
});

test('图片加载失败不创建半成品编辑器，取消与乱序响应不污染新选中项',async()=>{
    const restoreTimers=trackTimers();
    const host=await createHost({tab:'registry'});
    const restore=installCanvasAndImageStubs(host.document);
    let loader;
    try{
        const failed=image('qin-666-failed-image'),first=image('qin-666-stale-first'),second=image('qin-666-stale-second');
        await openReference(host,[failed,first,second],[failed,first,second]);
        await selectCanvas(host,[failed.id]);
        await wait(100);
        loader=installImageLoader(()=>{});
        let toolbar=host.document.querySelector('.asset-detail-media-canvas-toolbar [data-detail-media-context-action="edit-image"]');
        host.document.dispatchEvent({type:'click',target:toolbar});
        await wait(10);
        loader.instances.at(-1).onerror?.(new Error('load failed'));
        await wait(20);
        assert.equal(host.main.detailImageEditorState,null,'加载失败不创建编辑器状态');
        assert.equal(host.document.body.classList.contains('asset-image-editor-open'),false,'加载失败不留下编辑器遮罩');
        assert.match(host.document.body.textContent,/图片加载失败/,'失败原因保持可见');

        await selectCanvas(host,[first.id]);
        await wait(30);
        toolbar=host.document.querySelector('.asset-detail-media-canvas-toolbar [data-detail-media-context-action="edit-image"]');
        host.document.dispatchEvent({type:'click',target:toolbar});
        await wait(10);
        const cancelledLoad=loader.instances.at(-1);
        await selectCanvas(host,[]);
        cancelledLoad.onload?.();
        await wait(20);
        assert.equal(host.main.detailImageEditorState,null,'切换为无选中后丢弃迟到加载');
        assert.match(host.document.body.textContent,/单图编辑入口已取消/,'取消状态可见');

        await selectCanvas(host,[first.id]);
        await wait(30);
        toolbar=host.document.querySelector('.asset-detail-media-canvas-toolbar [data-detail-media-context-action="edit-image"]');
        host.document.dispatchEvent({type:'click',target:toolbar});
        await wait(10);
        const olderLoad=loader.instances.at(-1);
        await selectCanvas(host,[second.id]);
        await wait(30);
        toolbar=host.document.querySelector('.asset-detail-media-canvas-toolbar [data-detail-media-context-action="edit-image"]');
        host.document.dispatchEvent({type:'click',target:toolbar});
        await wait(10);
        const newerLoad=loader.instances.at(-1);
        assert.notEqual(olderLoad,newerLoad);
        newerLoad.onload?.();
        await wait(20);
        assert.equal(host.main.detailImageEditorState?.itemId,second.id,'先完成的新请求使用当前选中项');
        olderLoad.onload?.();
        await wait(20);
        assert.equal(host.main.detailImageEditorState?.itemId,second.id,'旧请求后到不能覆盖新编辑器');
        assert.match(host.document.body.textContent,/已进入单图编辑模式/,'迟到旧结果不覆盖成功状态');
    }finally{loader?.restore();restore();host.dispose();restoreTimers();}
});

test('原生双击以坐标命中指针捕获节点，真实移动不进入编辑',async()=>{
    const restoreTimers=trackTimers();
    const host=await createHost({tab:'registry'});
    const restore=installCanvasAndImageStubs(host.document);
    try{
        const asset=image('qin-666-dblclick');
        await openReference(host,[asset],[asset]);
        const node=host.document.querySelector(`[data-detail-media-canvas-node="${asset.id}"]`);
        const viewport=host.document.querySelector('[data-detail-media-canvas-viewport]');
        assert.ok(node&&viewport,'参考画布节点已渲染');
        host.document.elementFromPoint=()=>node;
        for(const id of [1,2]){
            host.document.dispatchEvent(pointer('pointerdown',node,id));
            host.document.dispatchEvent(pointer('pointerup',viewport,id));
        }
        node.classList.add('is-moving');
        const doubleClick={type:'dblclick',target:viewport,clientX:20,clientY:20,preventDefault(){this.defaultPrevented=true;},stopPropagation(){this.stopped=true;}};
        host.document.dispatchEvent(doubleClick);
        await wait(35);
        assert.equal(host.main.detailImageEditorState?.itemId,asset.id,'elementFromPoint 命中图片后进入编辑');
        assert.equal(node.classList.contains('is-moving'),false,'进入前清除残留移动态');
    }finally{restore();host.dispose();restoreTimers();}
});

test('网格 E/e 进入图片编辑、输入法事件不吞键、编辑器嵌入 Coverflow 的 E 仍切换橡皮擦',async()=>{
    const restoreTimers=trackTimers();
    const host=await createHost({tab:'registry'});
    const restore=installCanvasAndImageStubs(host.document);
    try{
        await loadCoverflow(host);
        const target=image('qin-666-grid-image'),other=image('qin-666-grid-other');
        host.main.setRegistryAssets([target,other]);
        host.main.setSelectedRegistryAssetId(target.id);
        const card=host.document.createElement('div');
        card.dataset.registryCard=target.id;
        host.root.appendChild(card);
        host.document.activeElement=card;

        const composing=keyboard(card,'e',{isComposing:true});
        host.document.dispatchEvent(composing);
        assert.equal(composing.prevented,false,'输入法组字期间不消费快捷键');
        assert.equal(host.main.detailImageEditorState,null);

        const event=keyboard(card,'E');
        host.document.dispatchEvent(event);
        await wait(45);
        assert.equal(event.prevented,true);
        assert.equal(host.main.detailImageEditorState?.itemId,target.id,'网格 E 打开对应图片编辑器');
        const editor=host.main.detailImageEditorState;
        const embedded=host.document.querySelector('.gw-coverflow--embedded');
        assert.ok(embedded,'图片编辑器挂载嵌入式 Coverflow');
        editor.tool='brush';
        const embeddedKey=keyboard(embedded,'e');
        embedded.dispatchEvent(embeddedKey);
        if(!embeddedKey.stopped) host.document.dispatchEvent(embeddedKey);
        await wait(10);
        assert.equal(embeddedKey.stopped,false,'编辑器内 Coverflow 不拦截 E 冒泡');
        assert.equal(host.main.detailImageEditorState,editor,'E 不重开或替换当前编辑器');
        assert.equal(editor.tool,'eraser','编辑器内 E 继续切换橡皮擦');
    }finally{restore();host.dispose();restoreTimers();}
});

test('版本结构卡 E 编辑 current_asset_id；真全屏 E 先退栈再打开预览与编辑器',async()=>{
    const restoreTimers=trackTimers();
    const host=await createHost({tab:'registry'});
    const restore=installCanvasAndImageStubs(host.document);
    try{
        await loadCoverflow(host);
        const current=image('qin-666-current'),older=image('qin-666-older');
        host.main.setRegistryAssets([current,older]);
        host.main.setSelectedRegistryAssetId('structure:qin-666-version');
        host.main.registryAssetStructures.set('qin-666-version',{
            id:'qin-666-version',structure_type:'version',current_asset_id:current.id,
            members:[{asset_id:current.id,asset:current},{asset_id:older.id,asset:older}],
        });
        const versionCard=host.document.createElement('div');
        versionCard.dataset.registryStructureCard='qin-666-version';
        versionCard.dataset.registryStructureType='version';
        versionCard.dataset.registryCard='structure:qin-666-version';
        host.root.appendChild(versionCard);
        host.main.registrySelectedIds.add(older.id);
        host.main.registrySelectedIds.add(current.id);
        const versionKey=keyboard(versionCard,'e');
        host.document.dispatchEvent(versionKey);
        await wait(45);
        assert.equal(host.main.detailImageEditorState?.itemId,current.id,'版本卡忽略多选成员并使用 current_asset_id');

        await host.main.gwCall('preview','requestCloseDetailImageEditor','返回预览');
        await wait(10);
        await host.main.gwCall('preview','openRegistryAssetFullscreenViewer',null,{currentAssetId:current.id});
        await wait(15);
        let exitCalls=0;
        host.document.fullscreenElement={};
        host.document.exitFullscreen=function(){exitCalls+=1;this.fullscreenElement=null;return Promise.resolve();};
        const fs=host.main.registryFullscreenViewerState;
        assert.ok(fs,'真全屏查看器已打开');
        const fullscreenKey=keyboard(fs.overlay,'e');
        host.document.dispatchEvent(fullscreenKey);
        await wait(55);
        assert.equal(fullscreenKey.prevented,true);
        assert.equal(exitCalls,1,'进入编辑前请求退出浏览器全屏');
        assert.equal(host.main.registryFullscreenViewerState,null,'全屏遮罩已关闭');
        assert.equal(host.main.detailViewerState.open,true,'底层素材预览栈已准备');
        assert.equal(host.main.detailImageEditorState?.itemId,current.id,'全屏图片进入编辑器');
    }finally{restore();host.dispose();restoreTimers();}
});

test('全屏 Coverflow 按 E 编辑当前图片，退出编辑后回到 Coverflow',async()=>{
    const restoreTimers=trackTimers();
    const host=await createHost({tab:'registry'});
    const restore=installCanvasAndImageStubs(host.document);
    try{
        await loadCoverflow(host);
        const first=image('qin-666-coverflow-first'),current=image('qin-666-coverflow-current');
        host.main.setRegistryAssets([first,current]);
        host.main.setSelectedRegistryAssetId(current.id);
        await host.main.gwCall('coverflow-bridge','openRegistryCoverflow',null,{currentAssetId:current.id});
        await wait(20);
        const surface=host.document.querySelector('.gw-coverflow--fullscreen');
        assert.ok(surface,'全屏 Coverflow 已打开');
        const focused=surface.querySelector('[data-gw-coverflow-action="close"]')||surface;
        const event=keyboard(focused,'e');
        surface.dispatchEvent(event);
        await wait(65);
        assert.equal(event.prevented,true);
        assert.equal(host.main.detailImageEditorState?.itemId,current.id,'E 编辑当前 Coverflow 图片');
        assert.equal(host.main.detailImageEditorState?.returnToCoverflow?.mode,'fullscreen','编辑状态保留返回 Coverflow 信息');

        await host.main.gwCall('preview','requestCloseDetailImageEditor','返回 Coverflow');
        await wait(35);
        assert.ok(host.document.querySelector('.gw-coverflow--fullscreen'),'退出编辑后重新显示 Coverflow');
    }finally{restore();host.dispose();restoreTimers();}
});

test('参考画布 Coverflow 当前图片进入编辑时保留画布上下文',async()=>{
    const restoreTimers=trackTimers();
    const host=await createHost({tab:'registry'});
    const restore=installCanvasAndImageStubs(host.document);
    try{
        const canvasImage=image('qin-666-canvas-image'),slide=image('qin-666-canvas-slide');
        await openReference(host,[canvasImage,slide],[canvasImage]);
        await host.main.gwCall('coverflow-bridge','openRegistryCoverflowAssetInEditor',{
            mode:'editor',source:'registry',assetId:slide.id,
        });
        await wait(60);
        assert.equal(host.main.detailImageEditorState?.itemId,slide.id,'参考画布 Coverflow 候选图进入编辑');
        assert.equal(host.main.detailViewerState.referenceCanvasMode,true,'编辑时保留底层参考画布');
    }finally{restore();host.dispose();restoreTimers();}
});
