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

// QIN-591：资产库延后域模块（inbox）。首屏不加载；经 GW_DEFERRED 注册函数面，
// 由主文件网关（gwCall/gwLoad）按需加载或在空闲时段预热。函数体除跨文件赋值点改写外保持原样。

import {
    INBOX_KIND_LABELS,
    INBOX_SOURCE_LABELS,
    activeTab,
    apiJsonResponse,
    assetInboxUnreadCount,
    escapeAttr,
    escapeHtml,
    inboxItems,
    inboxLoaded,
    inboxQuery,
    inboxSettings,
    inboxSettingsOpen,
    inboxSourceFilter,
    refreshIcons,
    render,
    renderAssetTreePanel,
    root,
    setActiveTab,
    setAssetTreeSelectedKey,
    setAssetTreeTab,
    setInboxItems,
    setInboxLoaded,
    setInboxSettings,
    setInboxSettingsOpen,
    setStatus,
    showAssetFailure,
    showAssetNotice,
} from '../asset-manager.js';
import {
    formatFileSize,
} from './formatters.js';
import {
    assetManagerHttp,
} from './http.js';
import { GW_DEFERRED } from './state.js';

let inboxLoading = false;
let inboxError = '';
// 【QIN-585 AC2】收集箱读取的单飞与新鲜度：首屏延后调度与用户点开会并发触发同一份数据，
// 不共享的话一次进页会打出多条 GET，晚返回的旧响应还会把已清零的未读角标改回旧值。
let inboxRequestSeq = 0;
let inboxInFlight = null;
let inboxInFlightToken = null;
let inboxMigrating = false;
function assetInboxKindLabel(kind=''){
    return INBOX_KIND_LABELS[String(kind || '')] || String(kind || '素材');
}
function assetInboxSourceLabel(source=''){
    return INBOX_SOURCE_LABELS[String(source || '')] || '素材';
}
function assetInboxTotalCount(){
    return Math.max(0, Number(inboxSettings?.count || inboxItems.length || 0));
}
function assetInboxFilteredItems(){
    const query = String(inboxQuery || '').trim().toLowerCase();
    return inboxItems.filter(item => {
        if(inboxSourceFilter && item.source !== inboxSourceFilter) return false;
        if(!query) return true;
        return String(item.name || '').toLowerCase().includes(query);
    });
}
function inboxPreviewUrl(item, w){
    // 【QIN-588】收集箱卡片预览地址：只用服务端下发的受控查询串拼装，前端零路径知识。
    const query = String(item?.preview_query || '');
    const kind = String(item?.kind || '');
    if(!query || (kind !== 'image' && kind !== 'video')) return '';
    const width = Math.max(64, Math.min(2048, Math.round(Number(w) || 256)));
    const params = new URLSearchParams(query);
    params.set('w', String(width));
    return `/api/media-preview?${params.toString()}`;
}
function renderInboxCard(item){
    const name = item.name || '未命名素材';
    const metaParts = [assetInboxSourceLabel(item.source), assetInboxKindLabel(item.kind)];
    if(Number(item.size_bytes || 0) > 0) metaParts.push(formatFileSize(item.size_bytes));
    const linkLike = String(item.url || '').startsWith('http') || item.kind === 'url';
    const previewUrl = inboxPreviewUrl(item, 320);
    const thumb = previewUrl
        ? `<img class="inbox-preview-img" src="${escapeAttr(previewUrl)}" alt="${escapeAttr(name)}" loading="lazy" decoding="async" draggable="false" data-preview-fallback="${escapeAttr(name)}">`
        : `<span class="asset-file-icon"><i data-lucide="${linkLike ? 'link' : 'file'}"></i><small>${escapeHtml(assetInboxKindLabel(item.kind))}</small></span>`;
    return `<article class="asset-card inbox-card" data-inbox-card="${escapeAttr(item.id)}" tabindex="0" role="article" aria-label="${escapeAttr(name)}">
        <div class="asset-thumb">${thumb}</div>
        <div class="asset-card-body">
            <div class="asset-card-name" title="${escapeAttr(name)}">${escapeHtml(name)}</div>
            <div class="asset-card-meta">${escapeHtml(metaParts.join(' · '))}</div>
        </div>
    </article>`;
}
function renderInboxSourceChips(){
    const chips = [['', '全部'], ['inbox', '收集箱'], ['collect_inbox', '浏览器收集'], ['asset_library', '托管资产']];
    return `<div class="asset-tools">${chips.map(([value, label]) => `<button class="hw-btn ${inboxSourceFilter === value ? 'hw-btn-gold' : ''}" type="button" data-inbox-source="${escapeAttr(value)}">${escapeHtml(label)}</button>`).join('')}</div>`;
}
function renderInboxSettingsPanel(){
    const path = String(inboxSettings?.path || '');
    return `<section class="inbox-settings" data-inbox-settings-panel aria-label="收集箱设置">
        <label class="asset-search-wrap"><i data-lucide="folder-cog"></i><input class="asset-search" type="text" data-inbox-path value="${escapeAttr(path)}" placeholder="收集箱绝对路径" aria-label="收集箱位置"></label>
        <label class="inbox-settings-migrate"><input type="checkbox" data-inbox-migrate checked><span>把已有文件迁移到新位置（不勾选则旧文件留在原处）</span></label>
        <div class="asset-tools">
            <button class="hw-btn hw-btn-gold" type="button" data-inbox-settings-save><i data-lucide="save"></i><span>保存位置</span></button>
            <button class="hw-btn" type="button" data-inbox-settings-cancel><span>取消</span></button>
        </div>
    </section>`;
}
function renderInboxEmpty(){
    return `<div class="tree-empty" role="status"><p>收集箱还是空的</p><small>浏览器插件收集和手动上传都会默认落到这里；也可以在设置里把已有文件迁移进来。</small></div>`;
}
function renderInboxManager(){
    if(!inboxLoaded && !inboxLoading){ void loadAssetInbox().then(() => { if(activeTab === 'inbox') render(); }); }
    const items = assetInboxFilteredItems();
    const total = assetInboxTotalCount();
    const unread = assetInboxUnreadCount();
    root.innerHTML = `${renderAssetTreePanel()}
    <section class="asset-panel asset-content inbox-content">
        <div class="content-toolbar">
            <div class="content-heading">
                <strong>收集箱</strong>
                <span>${total ? `${total} 个素材` : '默认收集文件夹'}${unread ? ` · ${unread} 个新素材` : ''}${inboxSettings?.is_default ? ' · 默认位置' : ''}</span>
            </div>
            <div class="asset-tools">
                <input id="inboxFileInput" type="file" multiple hidden>
                <button class="hw-btn hw-btn-gold" type="button" data-inbox-upload><i data-lucide="upload"></i><span>上传文件</span></button>
                <button class="hw-btn" type="button" data-inbox-migrate-run ${inboxMigrating ? 'disabled' : ''}><i data-lucide="package-open"></i><span>${inboxMigrating ? '迁移中' : '迁移托管资产'}</span></button>
                <button class="hw-btn" type="button" data-inbox-settings-toggle><i data-lucide="settings-2"></i><span>收集箱设置</span></button>
            </div>
        </div>
        ${renderInboxSourceChips()}
        ${inboxSettingsOpen ? renderInboxSettingsPanel() : ''}
        <div class="content-scroll">
            ${inboxError ? `<div class="tree-empty" role="alert">${escapeHtml(inboxError)}</div>` : ''}
            ${items.length ? `<div class="asset-grid">${items.map(renderInboxCard).join('')}</div>` : (inboxError ? '' : renderInboxEmpty())}
        </div>
    </section>`;
    refreshIcons();
}
// 【QIN-585 AC2】单飞与新鲜度：首屏延后调度、用户点开、上传后回读会并发触发同一份数据。
// - 普通读取（缺省）复用同一份在途请求，一次进页只打一条 GET；
// - 写后必刷（`{refresh:true}`）既不复用也不被复用：写之前发出的在途请求读到的是旧快照，
//   复用它会出现「提示上传/保存成功、列表里却没有新文件、设置还显示旧路径」。
//   这里只递增序号作废旧响应（它的结果与 loading 都不再回写），并另发一条新请求。
function loadAssetInbox(options){
    const refresh = Boolean(options && options.refresh);
    if(!refresh && inboxInFlight) return inboxInFlight;
    if(refresh){
        inboxInFlight = null;
        inboxInFlightToken = null;
    }
    const token = {seq:++inboxRequestSeq};
    inboxLoading = true;
    inboxError = '';
    const request = (async () => {
        try{
            const data = await apiJsonResponse(assetManagerHttp.request('/api/asset-inbox?limit=500', {method:'GET', credentials:'same-origin'}));
            // 过期响应直接丢弃：清零角标之后晚到的旧响应不得把未读数改回去。
            if(token.seq !== inboxRequestSeq) return;
            setInboxItems(Array.isArray(data.items) ? data.items : []);
            setInboxSettings(data.settings || null);
            setInboxLoaded(true);
        }catch(error){
            if(token.seq !== inboxRequestSeq) return;
            inboxError = String(error?.message || error || '收集箱读取失败');
        }finally{
            if(inboxInFlightToken === token){
                inboxInFlight = null;
                inboxInFlightToken = null;
            }
            if(token.seq === inboxRequestSeq) inboxLoading = false;
        }
    })();
    inboxInFlight = request;
    inboxInFlightToken = token;
    return request;
}
async function openAssetInbox(){
    setActiveTab('inbox');
    setAssetTreeTab('mine');
    setAssetTreeSelectedKey('mine:inbox');
    setInboxLoaded(false);
    render();
    await loadAssetInbox();
    if(activeTab === 'inbox') render();
    if(assetInboxUnreadCount() > 0) await markAssetInboxViewed();
}
async function markAssetInboxViewed(){
    try{
        await apiJsonResponse(assetManagerHttp.request('/api/asset-inbox/viewed', {method:'POST', credentials:'same-origin'}));
        const previous = assetInboxUnreadCount();
        if(inboxSettings) setInboxSettings({...inboxSettings, unread_count:0});
        // 【QIN-585 AC2】清零即作废所有更早发出的读取响应，否则它们晚到会把角标改回未读；
        // 被作废的那次请求不会自己复位 loading（它的 finally 已按新序号短路），这里一并复位。
        inboxRequestSeq += 1;
        inboxLoading = false;
        // 【QIN-547】角标清零后立即重绘，否则左栏数字「统计行 ${unread} 个新素材」要等到下一次 render 才消失。
        if(previous > 0) render();
    }catch(_){ /* 角标失败不阻断浏览 */ }
}
// 【QIN-588 AC2】点击收集箱卡片打开页内媒体预览浮层：图片显示大图，视频显示真实首帧。
// 浮窗规范 GW-UI-FW-01：显式关闭按钮、Escape 关闭、点击遮罩关闭、焦点在打开时移入浮层、
// 关闭后返还给触发卡片。
function openInboxMediaPreview(itemId){
    const item = inboxItems.find(entry => entry.id === itemId);
    if(!item) return;
    const previewUrl = inboxPreviewUrl(item, 2048);
    if(!previewUrl) return;
    const name = String(item.name || '');
    const trigger = document.activeElement;
    const overlay = document.createElement('div');
    overlay.className = 'inbox-media-preview';
    overlay.setAttribute('role', 'dialog');
    overlay.setAttribute('aria-modal', 'true');
    overlay.setAttribute('aria-label', `${name || '收集箱素材'} 媒体预览`);
    overlay.innerHTML = `
        <div class="inbox-media-preview-backdrop" data-inbox-preview-close></div>
        <figure class="inbox-media-preview-body">
            <img class="inbox-media-preview-image" src="${escapeAttr(previewUrl)}" alt="${escapeAttr(name || '收集箱素材')}" draggable="false" data-preview-fallback="${escapeAttr(name || '收集箱素材')}">
            <figcaption class="inbox-media-preview-caption">${escapeHtml(name || '收集箱素材')}</figcaption>
            <button type="button" class="hw-btn hw-btn-icon inbox-media-preview-close" data-inbox-preview-close aria-label="关闭预览"><i data-lucide="x"></i></button>
        </figure>
    `;
    document.body.appendChild(overlay);
    document.body.classList.add('asset-lightbox-open');
    refreshIcons();
    const close = () => {
        overlay.remove();
        document.body.classList.remove('asset-lightbox-open');
        document.removeEventListener('keydown', onKeyDown, true);
        if(trigger && typeof trigger.focus === 'function'){
            try{ trigger.focus(); }catch(_){ /* 返还焦点失败不阻断 */ }
        }
    };
    const onKeyDown = event => {
        if(event.key === 'Escape'){ event.preventDefault(); close(); }
    };
    overlay.querySelectorAll('[data-inbox-preview-close]').forEach(node => {
        node.addEventListener('click', close);
    });
    document.addEventListener('keydown', onKeyDown, true);
    const closeBtn = overlay.querySelector('.inbox-media-preview-close');
    if(closeBtn) closeBtn.focus();
}
async function migrateAssetLibraryToInbox(){
    if(inboxMigrating) return;
    inboxMigrating = true;
    render();
    try{
        const result = await apiJsonResponse(assetManagerHttp.request('/api/asset-inbox/migrate', {method:'POST', credentials:'same-origin'}));
        const failed = Array.isArray(result.failed) ? result.failed.length : 0;
        const movedCount = Number(result.moved_files || 0);
        showAssetNotice(`托管资产已并入收集箱：迁入 ${movedCount} 个文件${movedCount ? '（源文件保留）' : ''}，保留 ${Number(result.url_entries || 0)} 个链接条目${failed ? `，${failed} 个失败，可再次点击重试` : ''}`);
        await loadAssetInbox({refresh:true});
    }catch(error){
        showAssetFailure('迁移托管资产失败', error);
    }finally{
        inboxMigrating = false;
    }
    if(activeTab === 'inbox') render();
}
async function saveAssetInboxSettings(){
    const panel = document.querySelector('[data-inbox-settings-panel]');
    const path = String(panel?.querySelector('[data-inbox-path]')?.value || '').trim();
    const migrate = Boolean(panel?.querySelector('[data-inbox-migrate]')?.checked);
    if(!path){ setStatus('请填写收集箱位置'); return; }
    try{
        const result = await apiJsonResponse(assetManagerHttp.request('/api/asset-inbox/settings', {
            method:'PUT',
            credentials:'same-origin',
            headers:{'Content-Type':'application/json'},
            body:JSON.stringify({path, migrate}),
        }));
        setInboxSettingsOpen(false);
        const moved = Array.isArray(result.migrated) ? result.migrated.length : 0;
        const conflicts = Array.isArray(result.conflicts) ? result.conflicts.length : 0;
        const skipped = Array.isArray(result.skipped) ? result.skipped.length : 0;
        // 【QIN-547】后端搬不动文件时不会切换位置（已回滚），此时必须留住设置面板并说明原因，
        // 否则用户会以为位置已改成功、文件却不见了。
        if(result.applied === false){
            setInboxSettingsOpen(true);
            const failed = Array.isArray(result.failed) ? result.failed.length : 0;
            showAssetFailure('收集箱位置未变更', new Error(`${failed} 个文件搬不动，已回滚到原位置`));
            await loadAssetInbox({refresh:true});
            if(activeTab === 'inbox') render();
            return;
        }
        showAssetNotice(`收集箱位置已更新${moved ? `，迁移 ${moved} 个文件` : ''}${skipped ? `，${skipped} 个相同文件已存在` : ''}${conflicts ? `，${conflicts} 个同名文件未迁移` : ''}`);
        await loadAssetInbox({refresh:true});
    }catch(error){
        showAssetFailure('保存收集箱位置失败', error);
    }
    if(activeTab === 'inbox') render();
}
function canvasInboxAssetFromItem(item){
    const out = {url:item?.url || '', name:item?.name || '素材', kind:item?.kind || ''};
    ['natural_w','natural_h','width','height','w','h','layout_w','layout_h'].forEach(key => {
        const n = Number(item?.[key]);
        if(Number.isFinite(n) && n > 0) out[key] = n;
    });
    return out;
}

// 【QIN-591】域函数面注册：主文件 gwCall('inbox', fn) 经由此对象分派。
GW_DEFERRED['inbox'] = {
    loadAssetInbox,
    markAssetInboxViewed,
    migrateAssetLibraryToInbox,
    openAssetInbox,
    openInboxMediaPreview,
    renderInboxManager,
    saveAssetInboxSettings,
};

export {
    canvasInboxAssetFromItem,
};
