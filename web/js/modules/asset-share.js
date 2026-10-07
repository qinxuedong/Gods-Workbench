/**
 * Copyright 2026 Gods-Workbench Authors
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 *     http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */

import assetShareApi, {createAssetShareMediaSession} from './asset-share/api.js';

(function(){
    'use strict';
    const app=document.getElementById('shareApp');
    const pathSegment=decodeURIComponent(location.pathname.split('/').filter(Boolean).pop()||'');
    // 直接打开 /static/asset-share.html（末段为 *.html 或空）时根本不存在分享令牌，明确提示而非静默 404。
    const isDirectOpen=!pathSegment||/\.html?$/i.test(pathSegment);
    const token=isDirectOpen?'':pathSegment;
    const state={meta:null,data:null,index:0,tool:'pin',draft:null,start:null,points:[],timecodeMs:null,keyboardPoint:{x:0.5,y:0.5}};
    const mediaSession=createAssetShareMediaSession(assetShareApi,{maxBytes:64*1024*1024});
    let lifecycleEpoch=0;
    let lifecycleActive=true;
    let lifecycleController=new AbortController();
    function captureLifecycle(){
        return {epoch:lifecycleEpoch,controller:lifecycleController,signal:lifecycleController.signal};
    }
    function isLifecycleCurrent(context){
        return Boolean(lifecycleActive&&context&&context.epoch===lifecycleEpoch&&context.controller===lifecycleController&&!context.signal.aborted);
    }
    // 换票、离页及页面失效统一切断旧请求；服务端已处理的写入不会因此回滚。
    function rotateLifecycle({active=lifecycleActive,clearMetadata=false}={}){
        lifecycleEpoch+=1;
        lifecycleController.abort();
        lifecycleController=new AbortController();
        lifecycleActive=active;
        mediaSession.invalidate();
        state.data=null;
        if(clearMetadata)state.meta=null;
        state.draft=null;state.start=null;state.points=[];state.timecodeMs=null;
        return captureLifecycle();
    }
    const esc=value=>String(value??'').replace(/[&<>"']/g,ch=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch]));
    const attr=esc;
    const icon=(name)=>'<i data-lucide="'+name+'"></i>';
    const current=()=>state.data?.assets?.[state.index]||null;
    // 统一从后端标准错误包 {"detail":{"code":...,"message":...}} 提取可读信息。
    function apiErrorMessage(data){
        const detail=data?.detail;
        if(typeof detail==='string'&&detail)return detail;
        if(detail&&typeof detail==='object'&&detail.message)return String(detail.message);
        return data?.message||'访问失败';
    }

    async function readJson(responsePromise){
        const response=await responsePromise;
        const data=await response.json().catch(()=>({}));
        if(!response.ok){
            const err=new Error(apiErrorMessage(data));
            err.status=response.status;err.code=data?.detail?.code||'';
            throw err;
        }
        return data;
    }
    function error(message){
        rotateLifecycle();
        app.setAttribute('aria-busy','false');
        app.setAttribute('role','alert');
        app.setAttribute('aria-live','assertive');
        app.className='share-error';
        app.innerHTML=icon('circle-x')+'<strong>无法打开分享</strong><span>'+esc(message)+'</span>';
        lucide?.createIcons();
    }
    function timecode(ms){
        if(ms===null||ms===undefined)return '';
        const total=Math.max(0,Number(ms));const h=Math.floor(total/3600000);const m=Math.floor(total%3600000/60000);const s=Math.floor(total%60000/1000);const f=Math.floor(total%1000/40);
        return [h,m,s].map(v=>String(v).padStart(2,'0')).join(':')+':'+String(f).padStart(2,'0');
    }
    function finishShareLoading(){
        app.setAttribute('aria-busy','false');
        app.removeAttribute('role');
        app.removeAttribute('aria-live');
    }
    function renderLifecycleLoading(message){
        app.className='share-loading';
        app.setAttribute('aria-busy','true');
        app.setAttribute('role','status');
        app.setAttribute('aria-live','polite');
        app.textContent=message;
    }
    async function load(context=captureLifecycle()){
        if(!isLifecycleCurrent(context))return;
        if(!token){error('缺少分享令牌，请使用完整的分享链接打开本页面。');return;}
        try{
            const meta=await readJson(assetShareApi.getPublicShare(token,{signal:context.signal}));
            if(!isLifecycleCurrent(context))return;
            state.meta=meta;
            if(!meta.available){error(meta.reason||'链接不可用');return;}
            if(meta.requires_password)renderAccess();
            else await unlock('');
        }catch(err){
            if(!isLifecycleCurrent(context)||err?.name==='AbortError')return;
            error(err.message||'分享信息读取失败');
        }
    }
    function renderAccess(message=''){
        finishShareLoading();
        app.className='share-access';
        const passwordInput=state.meta?.requires_password?'<input name="password" type="password" placeholder="分享密码" autofocus required>':'';
        app.innerHTML='<form id="shareAccessForm" class="share-access-card"><h1>'+esc(state.meta?.title||'素材审阅')+'</h1><p>'+(state.meta?.requires_password?'这是受保护的素材审阅链接，请输入分享密码。':'请重新验证当前分享访问。')+'</p>'+passwordInput+'<small>本分享仅保留一个有效票据；再次访问会替换旧票据，其他已打开页面需要重新验证。</small><button class="share-primary" type="submit">'+icon('lock-keyhole')+(state.meta?.requires_password?'进入审阅':'重新验证')+'</button><div class="share-access-error" role="alert" aria-live="assertive">'+esc(message)+'</div></form>';
        lucide?.createIcons();
    }
    async function unlock(password){
        if(!lifecycleActive)return;
        const context=rotateLifecycle({active:true});
        try{
            const data=await readJson(assetShareApi.accessPublicShare(token,{password},{signal:context.signal}));
            if(!isLifecycleCurrent(context))return;
            state.data=data;
            state.index=0;
            render();
        }catch(err){
            if(!isLifecycleCurrent(context)||err?.name==='AbortError')return;
            if(err?.status===410)error(err.message||'分享已过期或访问次数已用尽');
            else renderAccess(err.message||'访问验证失败');
        }
    }

    function handleTicketError(err){
        if(err?.status!==401&&err?.status!==410)return false;
        rotateLifecycle({active:true});
        if(err.status===410)error(err.message||'分享已过期，请联系分享者');
        else renderAccess('访问票据已失效，请重新验证；再次访问会替换当前唯一票据。');
        return true;
    }

    async function loadCurrentMedia(asset){
        if(!lifecycleActive)return;
        const context=captureLifecycle();
        const mediaElement=document.getElementById('shareMedia');
        const ticket=state.data?.ticket;
        if(!mediaElement||!ticket)return;
        const expectedAssetId=asset.id||asset.asset_id;
        mediaElement.setAttribute('aria-busy','true');
        try{
            const objectUrl=await mediaSession.loadPreview(token,expectedAssetId,ticket);
            if(!isLifecycleCurrent(context)||!objectUrl||mediaElement!==document.getElementById('shareMedia')||current()?.id!==expectedAssetId)return;
            mediaElement.src=objectUrl;
            mediaElement.removeAttribute('aria-busy');
            const status=document.getElementById('shareMediaStatus');if(status)status.textContent='';
        }catch(err){
            if(!isLifecycleCurrent(context)||err?.name==='AbortError')return;
            if(handleTicketError(err))return;
            const status=document.getElementById('shareMediaStatus');
            if(status)status.textContent=err.message||'媒体读取失败';
            mediaElement.removeAttribute('aria-busy');
        }
    }

    function safeFilename(value){
        const cleaned=String(value||'素材').replace(/[\\/\r\n\0]/g,'_').slice(0,180);
        return cleaned||'素材';
    }

    async function downloadCurrent(){
        if(!lifecycleActive)return;
        const context=captureLifecycle();
        const asset=current();
        const ticket=state.data?.ticket;
        if(!asset||!ticket)return;
        try{
            const downloaded=await mediaSession.createDownload(token,asset.id||asset.asset_id,ticket);
            if(!downloaded)return;
            if(!isLifecycleCurrent(context)||state.data?.ticket!==ticket||current()?.id!==(asset.id||asset.asset_id)){
                downloaded.dispose();
                return;
            }
            const link=document.createElement('a');
            link.href=downloaded.url;
            link.download=safeFilename(asset.name);
            link.hidden=true;
            document.body.appendChild(link);
            link.click();
            link.remove();
            window.setTimeout(downloaded.dispose,1000);
        }catch(err){
            if(!isLifecycleCurrent(context)||err?.name==='AbortError')return;
            if(!handleTicketError(err))alert(err.message||'下载失败');
        }
    }
    function annotationSvg(annotation,draft=false){
        const g=annotation?.geometry||{};const color=annotation?.style?.color||(draft?'#ffb020':'#ff4d67');const width=Number(annotation?.style?.width||4);
        const x=Number(g.x||0)*1000,y=Number(g.y||0)*1000,x2=Number(g.x2??g.x??0)*1000,y2=Number(g.y2??g.y??0)*1000,w=Number(g.width||0)*1000,h=Number(g.height||0)*1000;
        if(annotation.kind==='pin')return '<g><circle cx="'+x+'" cy="'+y+'" r="18" fill="'+color+'"/><circle cx="'+x+'" cy="'+y+'" r="7" fill="white"/></g>';
        if(annotation.kind==='rect')return '<rect x="'+x+'" y="'+y+'" width="'+w+'" height="'+h+'" fill="none" stroke="'+color+'" stroke-width="'+width+'" vector-effect="non-scaling-stroke"/>';
        if(annotation.kind==='freehand')return '<polyline points="'+attr((g.points||[]).map(p=>p.x*1000+','+p.y*1000).join(' '))+'" fill="none" stroke="'+color+'" stroke-width="'+width+'" stroke-linecap="round" stroke-linejoin="round" vector-effect="non-scaling-stroke"/>';
        return '';
    }
    function annotations(){
        const values=(current()?.review?.comments||[]).flatMap(comment=>comment.annotations||[]);
        if(state.draft)values.push({...state.draft,_draft:true});
        return '<svg id="shareAnnotationSvg" class="share-annotation-svg" viewBox="0 0 1000 1000" preserveAspectRatio="none" aria-hidden="true" focusable="false">'+values.map(value=>annotationSvg(value,value._draft)).join('')+'</svg>';
    }
    function media(asset){
        if(asset.kind==='video')return '<video id="shareMedia" controls playsinline preload="metadata" aria-busy="true" aria-label="'+attr(asset.name)+'"></video>';
        if(asset.kind==='audio')return '<audio id="shareMedia" controls preload="metadata" aria-busy="true" aria-label="'+attr(asset.name)+'"></audio>';
        return '<img id="shareMedia" aria-busy="true" alt="'+attr(asset.name)+'">';
    }
    function thumb(asset){
        if(asset.kind==='image')return icon('image');
        return icon(asset.kind==='video'?'film':asset.kind==='audio'?'audio-lines':'file');
    }
    function reviewTime(value){
        // 写入响应为ISO时间，重新访问读回为epoch；两种真实形状都不能显示Invalid Date。
        const date=new Date(typeof value==='number'?value:String(value||''));
        return Number.isFinite(date.getTime())?date.toLocaleString():'';
    }
    function approval(value){
        const label=value.status==='approved'?'通过':value.status==='changes_requested'?'需修改':'未知结论';
        return '<article class="share-comment share-approval-record"><header><strong>'+esc(value.author_name||'访客')+' · '+esc(label)+'</strong><time>'+esc(reviewTime(value.created_at))+'</time></header><p>'+esc(value.note||'无补充说明').replace(/\n/g,'<br>')+'</p></article>';
    }
    function comment(value){
        const seek=value.timecode_ms!==null&&value.timecode_ms!==undefined?'<button type="button" data-seek="'+attr(value.timecode_ms)+'" aria-label="跳转到 '+timecode(value.timecode_ms)+'">'+icon('timer')+timecode(value.timecode_ms)+'</button>':'';
        return '<article class="share-comment"><header><strong>'+esc(value.author_name||'访客')+'</strong><time>'+esc(reviewTime(value.created_at))+'</time></header><p>'+esc(value.body||(value.annotations?.length?'图形批注':'')).replace(/\n/g,'<br>')+'</p><footer>'+seek+(value.annotations?.length?'<span>'+value.annotations.length+' 个标记</span>':'')+'</footer></article>';
    }
    function render(){
        const asset=current();if(!asset){error('分享中没有可用资产');return;}
        finishShareLoading();
        const permissions=state.data.share.permissions||{};const comments=asset.review?.comments||[];const watermark=state.data.share.watermark_text||'仅供审阅';
        const drawSurfaceAttributes=permissions.comment?' tabindex="0" role="region" aria-label="批注画布" aria-describedby="shareAnnotationHelp"':'';
        const annotationHelp=permissions.comment?'<p id="shareAnnotationHelp" class="share-sr-only">批注画布。使用方向键移动标注位置，Enter 或空格放置标记，框选或画笔模式下再次按 Enter 完成，Escape 取消当前标注。</p>':'';
        const annotationStatus=permissions.comment?'<p id="shareAnnotationStatus" class="share-sr-only" role="status" aria-live="polite"></p>':'';
        app.className='share-shell';
        app.innerHTML='<header class="share-head"><div class="share-brand"><div><strong>'+esc(state.data.share.title)+'</strong><span>'+state.data.assets.length+' 个资产 · '+(state.data.share.expires_at?'有效至 '+new Date(state.data.share.expires_at).toLocaleDateString():'长期有效')+' · 当前为单一有效票据，再次访问会替换旧票据</span></div></div><div class="share-head-actions">'+
            (permissions.download?'<button type="button" data-download aria-label="下载当前资产">'+icon('download')+'<span>下载</span></button>':'')+'<button type="button" data-copy-link>'+icon('link')+'<span>复制链接</span></button></div></header>'+
            '<main class="share-main"><aside class="share-assets">'+state.data.assets.map((item,index)=>'<button type="button" class="share-asset-row '+(index===state.index?'active':'')+'" data-asset-index="'+index+'" aria-pressed="'+(index===state.index)+'"><span class="share-asset-thumb">'+thumb(item)+'</span><span><strong>'+esc(item.name)+'</strong><span>'+esc(item.kind)+'</span></span></button>').join('')+'</aside>'+
            '<section class="share-stage-wrap">'+annotationHelp+'<div class="share-stage" data-draw-surface'+drawSurfaceAttributes+'>'+media(asset)+(asset.kind!=='audio'?annotations():'')+'<div class="share-watermark">'+esc(watermark)+'</div></div><p id="shareMediaStatus" class="share-media-status" role="status" aria-live="polite"></p>'+annotationStatus+
            (permissions.comment?'<div class="share-tools"><button type="button" class="'+(state.tool==='pin'?'active':'')+'" data-tool="pin" aria-pressed="'+(state.tool==='pin')+'">'+icon('map-pin')+'标记</button><button type="button" class="'+(state.tool==='rect'?'active':'')+'" data-tool="rect" aria-pressed="'+(state.tool==='rect')+'">'+icon('square')+'框选</button><button type="button" class="'+(state.tool==='freehand'?'active':'')+'" data-tool="freehand" aria-pressed="'+(state.tool==='freehand')+'">'+icon('pencil')+'画笔</button><button type="button" data-clear>'+icon('eraser')+'清除</button>'+(asset.kind==='video'?'<button type="button" data-time>'+icon('timer')+'取时间点</button>':'')+(state.timecodeMs!==null?'<span class="share-time">'+timecode(state.timecodeMs)+'</span>':'')+'</div>':'')+'</section>'+
            '<aside class="share-review"><div class="share-review-head"><strong>审阅意见</strong></div>'+
            (permissions.comment?'<div class="share-approval"><span>版本结论</span><div><button type="button" data-approval="changes_requested">需修改</button><button type="button" data-approval="approved">通过</button></div></div>':'')+
            '<div class="share-comments">'+(asset.review?.approvals||[]).map(approval).join('')+(comments.map(comment).join('')||'<div class="share-empty">暂无评论</div>')+'</div>'+
            (permissions.comment?'<form id="shareCommentForm" class="share-comment-form"><textarea name="body" placeholder="输入意见，或在画面上标记…"></textarea><div><input name="guest_name" value="'+attr(localStorage.getItem('asset_review_guest')||'')+'" placeholder="您的称呼" required><button class="share-primary" type="submit">'+icon('send')+'发布</button></div></form>':'')+'</aside></main>';
        lucide?.createIcons();
        loadCurrentMedia(asset);
    }
    function refreshSvg(){
        const svg=document.getElementById('shareAnnotationSvg');if(!svg)return;
        const values=(current()?.review?.comments||[]).flatMap(comment=>comment.annotations||[]);if(state.draft)values.push({...state.draft,_draft:true});
        svg.innerHTML=values.map(value=>annotationSvg(value,value._draft)).join('');
    }
    function announceAnnotation(message){
        const status=document.getElementById('shareAnnotationStatus');
        if(status)status.textContent=message;
    }
    function clampAnnotationPoint(point){
        return {x:Math.max(0,Math.min(1,Number(point?.x)||0)),y:Math.max(0,Math.min(1,Number(point?.y)||0))};
    }
    function updateKeyboardDraft(){
        if(!state.start)return;
        const point=clampAnnotationPoint(state.keyboardPoint);
        if(state.tool==='rect')state.draft={kind:'rect',geometry:{x:Math.min(state.start.x,point.x),y:Math.min(state.start.y,point.y),width:Math.abs(point.x-state.start.x),height:Math.abs(point.y-state.start.y)},style:{color:'#ffb020',width:4}};
        if(state.tool==='freehand'){
            const last=state.points[state.points.length-1];
            if(!last||last.x!==point.x||last.y!==point.y)state.points.push(point);
            state.draft={kind:'freehand',geometry:{points:state.points.slice(-5000)},style:{color:'#ffb020',width:4}};
        }
        refreshSvg();
    }
    function handleStageKeyboard(event,surface){
        if(!state.data?.share?.permissions?.comment||event.target.id==='shareMedia')return;
        const step=event.shiftKey?0.1:0.02;
        const movement={ArrowLeft:[-step,0],ArrowRight:[step,0],ArrowUp:[0,-step],ArrowDown:[0,step]}[event.key];
        if(movement){
            event.preventDefault();
            state.keyboardPoint=clampAnnotationPoint({x:state.keyboardPoint.x+movement[0],y:state.keyboardPoint.y+movement[1]});
            updateKeyboardDraft();
            surface.setAttribute('aria-label','批注画布，当前位置 '+Math.round(state.keyboardPoint.x*100)+'%，'+Math.round(state.keyboardPoint.y*100)+'%');
            return;
        }
        if(event.key==='Escape'&&state.start){
            event.preventDefault();state.start=null;state.points=[];state.draft=null;refreshSvg();announceAnnotation('已取消当前批注');return;
        }
        if(event.key!=='Enter'&&event.key!==' ')return;
        event.preventDefault();
        const point=clampAnnotationPoint(state.keyboardPoint);
        if(state.tool==='pin'){
            state.draft={kind:'pin',geometry:point,style:{color:'#ffb020',width:4}};refreshSvg();announceAnnotation('已放置标记，填写意见后发布');return;
        }
        if(!state.start){
            state.start=point;state.points=[point];state.draft=null;announceAnnotation('已设置批注起点，使用方向键调整后再次按 Enter 完成');return;
        }
        updateKeyboardDraft();state.start=null;state.points=[];announceAnnotation('已完成批注，填写意见后发布');
    }
    function position(event,surface){const rect=surface.getBoundingClientRect();return{x:Math.max(0,Math.min(1,(event.clientX-rect.left)/rect.width)),y:Math.max(0,Math.min(1,(event.clientY-rect.top)/rect.height))};}
    function down(event,surface){
        if(!state.data?.share?.permissions?.comment||['VIDEO','AUDIO','BUTTON'].includes(event.target.tagName))return;
        const point=position(event,surface);state.keyboardPoint=point;state.start=point;state.points=[point];
        if(state.tool==='pin'){state.draft={kind:'pin',geometry:point,style:{color:'#ffb020',width:4}};state.start=null;refreshSvg();announceAnnotation('已放置标记，填写意见后发布');}
    }
    function move(event,surface){
        if(!state.start)return;const point=position(event,surface);
        if(state.tool==='rect')state.draft={kind:'rect',geometry:{x:Math.min(state.start.x,point.x),y:Math.min(state.start.y,point.y),width:Math.abs(point.x-state.start.x),height:Math.abs(point.y-state.start.y)},style:{color:'#ffb020',width:4}};
        if(state.tool==='freehand'){state.points.push(point);state.draft={kind:'freehand',geometry:{points:state.points.slice(-5000)},style:{color:'#ffb020',width:4}};}
        refreshSvg();
    }
    function up(event,surface){if(!state.start)return;move(event,surface);state.start=null;state.points=[];}
    async function submitComment(form){
        if(!lifecycleActive)return;
        const asset=current();if(!asset)return;
        const values=new FormData(form);const guest=String(values.get('guest_name')||'访客');localStorage.setItem('asset_review_guest',guest);
        if(!state.data?.ticket){renderAccess('访问票据已失效，请重新验证。');return;}
        const ticket=state.data.ticket;const context=captureLifecycle();
        try{
            const result=await readJson(assetShareApi.createPublicShareComment(token,{ticket,asset_id:asset.id,guest_name:guest,body:String(values.get('body')||''),timecode_ms:state.timecodeMs,annotations:state.draft?[state.draft]:[]},{signal:context.signal}));
            if(!isLifecycleCurrent(context)||state.data?.ticket!==ticket)return;
            asset.review.comments.push(result.comment);state.draft=null;state.timecodeMs=null;render();
        }catch(err){
            if(!isLifecycleCurrent(context)||err?.name==='AbortError')return;
            if(!handleTicketError(err))alert(err.message||'评论提交失败');
        }
    }
    async function approve(status){
        if(!lifecycleActive)return;
        const asset=current();if(!asset)return;
        const guest=localStorage.getItem('asset_review_guest')||prompt('您的称呼')||'访客';const note=prompt(status==='approved'?'审批说明（可选）':'请说明修改意见')??'';
        if(!lifecycleActive)return;
        if(!state.data?.ticket){renderAccess('访问票据已失效，请重新验证。');return;}
        const ticket=state.data.ticket;const context=captureLifecycle();
        try{
            const result=await readJson(assetShareApi.updatePublicShareApproval(token,{ticket,asset_id:asset.id,guest_name:guest,status,note},{signal:context.signal}));
            if(!isLifecycleCurrent(context)||state.data?.ticket!==ticket)return;
            asset.review.approvals.unshift(result.approval);render();
        }catch(err){
            if(!isLifecycleCurrent(context)||err?.name==='AbortError')return;
            if(!handleTicketError(err))alert(err.message||'审批提交失败');
        }
    }
    document.addEventListener('submit',event=>{if(event.target.id==='shareAccessForm'){event.preventDefault();unlock(new FormData(event.target).get('password')||'');}if(event.target.id==='shareCommentForm'){event.preventDefault();submitComment(event.target);}});
    document.addEventListener('click',event=>{
        const asset=event.target.closest?.('[data-asset-index]');if(asset){mediaSession.invalidate();state.index=Number(asset.dataset.assetIndex);state.draft=null;state.start=null;state.points=[];state.timecodeMs=null;state.keyboardPoint={x:0.5,y:0.5};render();document.querySelectorAll('[data-asset-index]')[state.index]?.focus();return;}
        const tool=event.target.closest?.('[data-tool]');if(tool){state.tool=tool.dataset.tool;document.querySelectorAll('[data-tool]').forEach(node=>{const active=node===tool;node.classList.toggle('active',active);node.setAttribute('aria-pressed',String(active));});announceAnnotation('已选择'+tool.textContent.trim()+'工具');return;}
        if(event.target.closest?.('[data-clear]')){state.draft=null;state.start=null;state.points=[];refreshSvg();announceAnnotation('已清除当前批注');return;}
        if(event.target.closest?.('[data-time]')){state.timecodeMs=Math.round(Number(document.getElementById('shareMedia')?.currentTime||0)*1000);render();return;}
        const seek=event.target.closest?.('[data-seek]');if(seek){const media=document.getElementById('shareMedia');media.currentTime=Number(seek.dataset.seek)/1000;media.play?.();return;}
        const approval=event.target.closest?.('[data-approval]');if(approval)approve(approval.dataset.approval);
        if(event.target.closest?.('[data-download]'))downloadCurrent();
        if(event.target.closest?.('[data-copy-link]'))navigator.clipboard.writeText(location.href);
    });
    document.addEventListener('pointerdown',event=>{const surface=event.target.closest?.('[data-draw-surface]');if(surface)down(event,surface);});
    document.addEventListener('pointermove',event=>{const surface=document.querySelector('[data-draw-surface]');if(surface)move(event,surface);});
    document.addEventListener('pointerup',event=>{const surface=document.querySelector('[data-draw-surface]');if(surface)up(event,surface);});
    document.addEventListener('keydown',event=>{const surface=event.target.closest?.('[data-draw-surface]');if(surface)handleStageKeyboard(event,surface);});
    window.addEventListener('pagehide',()=>{
        rotateLifecycle({active:false,clearMetadata:true});
        renderLifecycleLoading('页面已离开；返回后需要重新验证分享访问。');
    });
    window.addEventListener('pageshow',event=>{
        if(!event.persisted&&lifecycleActive)return;
        const context=rotateLifecycle({active:true,clearMetadata:true});
        renderLifecycleLoading('页面已恢复，正在重新验证分享访问…');
        load(context);
    });
    load();
})();
