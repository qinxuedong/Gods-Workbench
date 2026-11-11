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

// QIN-591：资产库延后域模块（recycle）。首屏不加载；经 GW_DEFERRED 注册函数面，
// 由主文件网关（gwCall/gwLoad）按需加载或在空闲时段预热。函数体除跨文件赋值点改写外保持原样。

import {
    ASSET_REGISTRY_RECYCLE_CONFIRM_KEY,
    ASSET_REGISTRY_RECYCLE_MODE_KEY,
    ASSET_REGISTRY_RECYCLE_SETUP_KEY,
    apiJsonResponse,
    closeDetailPreview,
    detailViewerState,
    escapeAttr,
    escapeHtml,
    refreshIcons,
    refreshRegistryAssets,
    refreshRegistryFacetData,
    registryAssets,
    registryCanRecycle,
    registryDetail,
    registryKindLabel,
    registryRecycleBinState,
    registrySelectedIds,
    registryStatus,
    render,
    requireExpectedVersion,
    selectedRegistryAssetId,
    setRegistryDetail,
    setRegistryRecycleBinState,
    setSelectedRegistryAssetId,
    setStatus,
    setThumbnailCacheVersion,
    storageSettingsState,
} from '../asset-manager.js';
import assetManagerApi from './api.js';
import {
    formatDate,
    formatFileSize,
} from './formatters.js';
import {
    readStoredBoolean,
} from './storage.js';
import {
    markAssetFloatingSurface,
} from './preview.js';
import {
    applyStorageSettingsData,
} from './settings.js';
import { GW_DEFERRED } from './state.js';

let registryRecycleState = {open:false, assetId:'', busy:false, dontAsk:false, error:''};
let registryRecyclerSetupAssetId = '';
function registryRecyclerPath(item={}){
    const configured = String(storageSettingsState.dirs?.trash || '').trim().replace(/[\\/]+$/, '');
    if(configured) return `${configured}${configured.includes('\\') ? '\\' : '/'}god-recycler`;
    if(localStorage.getItem(ASSET_REGISTRY_RECYCLE_MODE_KEY) === 'source'){
        const sourceRoot = String(item.storage_root_path || '').replace(/[\\/]+$/, '');
        if(sourceRoot) return `${sourceRoot}${sourceRoot.includes('\\') ? '\\' : '/'}god-recycler`;
        return '跟随当前素材来源盘符 / god-recycler';
    }
    return 'registry_uploads/god-recycler（数据根目录下的系统默认目录）';
}
function closeRegistryRecyclerSetupDialog(){
    registryRecyclerSetupAssetId = '';
    document.getElementById('registryRecyclerSetupOverlay')?.remove();
}
function renderRegistryRecyclerSetupDialog(){
    const assetId = registryRecyclerSetupAssetId;
    const item = registryAssets.find(value => value.id === assetId) || (registryDetail?.id === assetId ? registryDetail : null);
    if(!assetId || !item) { closeRegistryRecyclerSetupDialog(); return; }
    let overlay = document.getElementById('registryRecyclerSetupOverlay');
    if(!overlay){
        overlay = document.createElement('div');
        overlay.id = 'registryRecyclerSetupOverlay';
        overlay.className = 'storage-settings-overlay registry-recycler-setup-overlay';
        document.body.appendChild(overlay);
    }
    overlay.innerHTML = `<section class="registry-recycle-dialog registry-recycler-setup-dialog" data-floating-content role="dialog" aria-modal="true" aria-labelledby="registryRecyclerSetupTitle" tabindex="-1">
        <header><span><i data-lucide="folder-cog"></i></span><div><strong id="registryRecyclerSetupTitle">首次设置回收站</strong><small>${escapeHtml(item.name || '当前素材')}</small></div><button type="button" data-registry-recycler-setup-close aria-label="关闭"><i data-lucide="x"></i></button></header>
        <div class="registry-recycle-body"><p>选择软删除文件的回收位置。默认跟随素材盘。确认后会记住此设置；取消不会删除素材。</p>
            <div class="registry-recycler-setup-options">
                <button type="button" class="hw-btn hw-btn-gold" data-registry-recycler-setup-default><strong>默认跟随素材盘（推荐）</strong><small>回收文件放在素材来源盘的 god-recycler 目录</small></button>
                <button type="button" class="hw-btn" data-registry-recycler-setup-custom><strong>自定义目录</strong><small>选择服务器上的绝对目录</small></button>
            </div>
            <div class="registry-recycle-error" data-registry-recycler-setup-error hidden></div>
        </div>
        <footer><button class="hw-btn" type="button" data-registry-recycler-setup-close>取消</button></footer>
    </section>`;
    markAssetFloatingSurface(overlay, {surface:'asset-recycler-setup', content:'.registry-recycler-setup-dialog', close:closeRegistryRecyclerSetupDialog});
    refreshIcons();
    requestAnimationFrame(() => overlay.querySelector('[data-registry-recycler-setup-default]')?.focus());
}
async function chooseRegistryRecyclerSetup(mode){
    const assetId = registryRecyclerSetupAssetId;
    const errorNode = document.querySelector('[data-registry-recycler-setup-error]');
    try {
        if(mode === 'custom'){
            const result = await window.GWWorkbenchShell?.changeRecyclerDirectory?.({quiet:true});
            if(!result?.saved) return;
            const settings = await apiJsonResponse(assetManagerApi.getStorageSettings());
            applyStorageSettingsData(settings || {});
            localStorage.setItem(ASSET_REGISTRY_RECYCLE_MODE_KEY, 'custom');
        } else {
            localStorage.setItem(ASSET_REGISTRY_RECYCLE_MODE_KEY, 'source');
        }
        localStorage.setItem(ASSET_REGISTRY_RECYCLE_SETUP_KEY, 'true');
        closeRegistryRecyclerSetupDialog();
        await openRegistryRecycleDialog(assetId);
    } catch(error){
        if(errorNode){ errorNode.textContent = error.message || '回收目录设置失败'; errorNode.hidden = false; }
    }
}
function closeRegistryRecycleDialog(force=false){
    if(registryRecycleState.busy && !force) return;
    registryRecycleState = {open:false, assetId:'', busy:false, dontAsk:false, error:''};
    document.getElementById('registryRecycleOverlay')?.remove();
}
function renderRegistryRecycleDialog(){
    let overlay = document.getElementById('registryRecycleOverlay');
    if(!registryRecycleState.open){ overlay?.remove(); return; }
    const item = registryAssets.find(value => value.id === registryRecycleState.assetId) || registryDetail;
    if(!item){ closeRegistryRecycleDialog(true); return; }
    if(!overlay){
        overlay = document.createElement('div');
        overlay.id = 'registryRecycleOverlay';
        overlay.className = 'storage-settings-overlay registry-recycle-overlay';
        document.body.appendChild(overlay);
    }
    overlay.innerHTML = `<section class="registry-recycle-dialog" data-floating-content role="dialog" aria-modal="true" aria-labelledby="registryRecycleTitle">
        <header><span><i data-lucide="trash-2"></i></span><div><strong id="registryRecycleTitle">确认删除吗？</strong><small>${escapeHtml(item.name || '未命名素材')}</small></div><button type="button" data-registry-recycle-close aria-label="关闭" ${registryRecycleState.busy ? 'disabled' : ''}><i data-lucide="x"></i></button></header>
        <div class="registry-recycle-body"><p>当前操作并非永久删除，永久删除请前往当前素材库文件夹：</p><code>${escapeHtml(registryRecyclerPath(item))}</code><div class="registry-recycle-note"><i data-lucide="info"></i><span>原文件将移动到 <b>god-recycler</b>，缩略图等缓存会同步清理。</span></div>${registryRecycleState.error ? `<div class="registry-recycle-error">${escapeHtml(registryRecycleState.error)}</div>` : ''}</div>
        <label class="registry-recycle-skip"><input id="registryRecycleDontAsk" type="checkbox" ${registryRecycleState.dontAsk ? 'checked' : ''} ${registryRecycleState.busy ? 'disabled' : ''}><span class="registry-feature-switch ${registryRecycleState.dontAsk ? 'is-on' : ''}"><i></i></span><span><strong>不再提醒</strong><small>以后点击删除按钮将直接移入 god-recycler</small></span></label>
        <footer><button class="hw-btn" type="button" data-registry-recycle-close ${registryRecycleState.busy ? 'disabled' : ''}>取消</button><button class="hw-btn hw-btn-danger registry-recycle-confirm" type="button" data-registry-recycle-confirm ${registryRecycleState.busy ? 'disabled' : ''}><i data-lucide="${registryRecycleState.busy ? 'loader-2' : 'trash-2'}"></i><span>${registryRecycleState.busy ? '正在移动…' : '移入回收站'}</span></button></footer>
    </section>`;
    markAssetFloatingSurface(overlay, {surface:'asset-registry-recycle', content:'.registry-recycle-dialog', close:closeRegistryRecycleDialog});
    refreshIcons();
}
async function openRegistryRecycleDialog(assetId){
    const item = registryAssets.find(value => value.id === assetId) || (registryDetail?.id === assetId ? registryDetail : null);
    if(!item || item.is_remote || !registryCanRecycle(item)) return;
    try {
        applyStorageSettingsData(await apiJsonResponse(assetManagerApi.getStorageSettings()));
    } catch(error){
        setStatus(`无法读取回收站目录设置：${error.message || '请求失败'}`);
        return;
    }
    if(!readStoredBoolean(ASSET_REGISTRY_RECYCLE_SETUP_KEY, false)){
        registryRecyclerSetupAssetId = assetId;
        renderRegistryRecyclerSetupDialog();
        return;
    }
    if(readStoredBoolean(ASSET_REGISTRY_RECYCLE_CONFIRM_KEY, false)){
        await recycleRegistryAsset(assetId, true);
        return;
    }
    registryRecycleState = {open:true, assetId, busy:false, dontAsk:false, error:''};
    renderRegistryRecycleDialog();
}
async function recycleRegistryAsset(assetId, silent=false){
    if(!assetId) return;
    registryRecycleState = {...registryRecycleState, open:!silent, assetId, busy:true, error:''};
    if(!silent) renderRegistryRecycleDialog();
    try {
        const recyclerMode = localStorage.getItem(ASSET_REGISTRY_RECYCLE_MODE_KEY) === 'source' ? 'source' : 'managed';
        const data = await apiJsonResponse(assetManagerApi.deleteRegistryAsset(
            assetId,
            requireExpectedVersion(registryStatus.revision, '资产注册表'),
            {headers:{'X-GW-Recycle-Mode':recyclerMode}}
        ));
        if(detailViewerState.open && detailViewerState.source === 'registry' && detailViewerState.id === assetId) closeDetailPreview(true);
        registrySelectedIds.delete(assetId);
        if(selectedRegistryAssetId === assetId){ setSelectedRegistryAssetId(''); setRegistryDetail(null); }
        closeRegistryRecycleDialog(true);
        await Promise.all([refreshRegistryAssets(false), refreshRegistryFacetData()]);
        render();
        setStatus(`已移入 god-recycler${Number(data.recycled?.removed_previews || 0) ? `，清理 ${Number(data.recycled.removed_previews)} 个缓存` : ''}`);
    } catch(err){
        registryRecycleState = {...registryRecycleState, open:!silent, busy:false, error:err.message || '移入回收站失败'};
        if(silent) setStatus(registryRecycleState.error); else renderRegistryRecycleDialog();
        throw err;
    }
}
async function confirmRegistryRecycle(){
    if(!registryRecycleState.assetId || registryRecycleState.busy) return;
    const dontAsk = Boolean(document.getElementById('registryRecycleDontAsk')?.checked);
    registryRecycleState.dontAsk = dontAsk;
    if(dontAsk){ try { localStorage.setItem(ASSET_REGISTRY_RECYCLE_CONFIRM_KEY, 'true'); } catch(_) {} }
    await recycleRegistryAsset(registryRecycleState.assetId, false);
}
function closeRegistryRecycleBin(){
    if(registryRecycleBinState.restoringId) return;
    setRegistryRecycleBinState({...registryRecycleBinState, open:false, error:''});
    document.getElementById('registryRecycleBinOverlay')?.remove();
}
function renderRegistryRecycleBin(){
    let overlay = document.getElementById('registryRecycleBinOverlay');
    if(!registryRecycleBinState.open){ overlay?.remove(); return; }
    if(!overlay){
        overlay = document.createElement('div');
        overlay.id = 'registryRecycleBinOverlay';
        overlay.className = 'storage-settings-overlay registry-recycle-bin-overlay';
        document.body.appendChild(overlay);
    }
    const items = Array.isArray(registryRecycleBinState.items) ? registryRecycleBinState.items : [];
    const recyclerDirectories = [...new Set(items.map(item => String(item.recycler_directory || '').trim()).filter(Boolean))];
    const body = registryRecycleBinState.loading
        ? `<div class="registry-recycle-bin-empty"><i data-lucide="loader-2"></i><strong>正在读取回收站…</strong></div>`
        : registryRecycleBinState.error
            ? `<div class="registry-recycle-bin-empty error"><i data-lucide="circle-alert"></i><strong>${escapeHtml(registryRecycleBinState.error)}</strong><button class="hw-btn" type="button" data-registry-recycle-bin-reload>重新加载</button></div>`
            : items.length
                ? `<div class="registry-recycle-bin-list">${items.map(item => {
                    const restoring = registryRecycleBinState.restoringId === item.id;
                    const unavailable = !item.available;
                    return `<article class="registry-recycle-bin-item ${unavailable ? 'is-missing' : ''}">
                        <span class="registry-recycle-bin-kind"><i data-lucide="${item.kind === 'image' ? 'image' : item.kind === 'video' ? 'film' : item.kind === 'audio' ? 'audio-lines' : 'file'}"></i></span>
                        <div class="registry-recycle-bin-copy"><strong title="${escapeAttr(item.name || '')}">${escapeHtml(item.name || '未命名素材')}</strong><span>${escapeHtml(item.storage_root_name || '素材来源')} · ${escapeHtml(registryKindLabel(item.kind || 'document'))} · ${escapeHtml(formatFileSize(item.file_size || 0))} · ${escapeHtml(formatDate(item.deleted_at))}</span><code title="${escapeAttr(item.original_relative_path || '')}">${escapeHtml(item.original_relative_path || '')}</code>${unavailable ? '<em>回收站文件已被手动移除</em>' : ''}</div>
                        <button class="hw-btn hw-btn-gold" type="button" data-registry-recycle-restore="${escapeAttr(item.id || '')}" ${restoring || unavailable ? 'disabled' : ''}><i data-lucide="${restoring ? 'loader-2' : 'rotate-ccw'}"></i><span>${restoring ? '恢复中…' : '恢复'}</span></button>
                    </article>`;
                }).join('')}</div>`
                : `<div class="registry-recycle-bin-empty"><i data-lucide="trash-2"></i><strong>回收站暂无素材</strong><span>从资产中心删除的本地素材会显示在这里。</span></div>`;
    overlay.innerHTML = `<section class="registry-recycle-bin-modal" data-floating-content role="dialog" aria-modal="true" aria-labelledby="registryRecycleBinTitle">
        <header class="storage-settings-head"><div><strong id="registryRecycleBinTitle">回收站</strong><span>找回从资产中心移入 god-recycler 的素材。</span></div><button type="button" data-registry-recycle-bin-close aria-label="关闭" ${registryRecycleBinState.restoringId ? 'disabled' : ''}><i data-lucide="x"></i></button></header>
        <div class="registry-recycle-bin-notice"><i data-lucide="shield-check"></i><div><strong>这里只提供恢复，不提供清空</strong><span>永久删除原素材，请手动进入对应素材库的 <b>god-recycler</b> 文件夹处理。</span></div></div>
        ${recyclerDirectories.length ? `<div class="registry-recycle-bin-paths"><strong>回收目录</strong>${recyclerDirectories.map(path => `<code>${escapeHtml(path)}</code>`).join('')}</div>` : ''}
        <div class="registry-recycle-bin-body">${body}</div>
        <footer><span>共 ${items.length} 项</span><button class="hw-btn" type="button" data-registry-recycle-bin-close ${registryRecycleBinState.restoringId ? 'disabled' : ''}>关闭</button></footer>
    </section>`;
    markAssetFloatingSurface(overlay, {surface:'asset-registry-recycle-bin', content:'.registry-recycle-bin-modal', close:closeRegistryRecycleBin});
    refreshIcons();
}
async function loadRegistryRecycleBin(){
    setRegistryRecycleBinState({...registryRecycleBinState, loading:true, error:''});
    renderRegistryRecycleBin();
    try {
        const data = await apiJsonResponse(assetManagerApi.getRegistryRecycleBin());
        setRegistryRecycleBinState({...registryRecycleBinState, loading:false, items:Array.isArray(data.items) ? data.items : []});
    } catch(err){
        setRegistryRecycleBinState({...registryRecycleBinState, loading:false, error:err.message || '回收站读取失败'});
    }
    renderRegistryRecycleBin();
}
async function openRegistryRecycleBin(){
    setRegistryRecycleBinState({...registryRecycleBinState, open:true, error:''});
    await loadRegistryRecycleBin();
}
async function restoreRegistryRecycleEntry(entryId){
    if(!entryId || registryRecycleBinState.restoringId) return;
    setRegistryRecycleBinState({...registryRecycleBinState, restoringId:entryId, error:''});
    renderRegistryRecycleBin();
    try {
        const data = await apiJsonResponse(assetManagerApi.restoreRegistryRecycleEntry(entryId, requireExpectedVersion(registryStatus.revision, '资产注册表')));
        setRegistryRecycleBinState({...registryRecycleBinState, restoringId:'', items:registryRecycleBinState.items.filter(item => item.id !== entryId)});
        await Promise.all([refreshRegistryAssets(false), refreshRegistryFacetData()]);
        // 【修复问题1】回收站恢复属于用户显式失效，保留会话级 bump（仅影响旧版 url 参数 URL）
        setThumbnailCacheVersion(Date.now());
        render();
        renderRegistryRecycleBin();
        setStatus(`已恢复 ${data.restored?.name || '素材'}`);
    } catch(err){
        setRegistryRecycleBinState({...registryRecycleBinState, restoringId:'', error:err.message || '素材恢复失败'});
        renderRegistryRecycleBin();
    }
}

// 【QIN-591】域函数面注册：主文件 gwCall('recycle', fn) 经由此对象分派。
GW_DEFERRED['recycle'] = {
    closeRegistryRecycleBin,
    openRegistryRecycleBin,
    openRegistryRecycleDialog,
};

export {
    chooseRegistryRecyclerSetup,
    closeRegistryRecycleBin,
    closeRegistryRecycleDialog,
    closeRegistryRecyclerSetupDialog,
    confirmRegistryRecycle,
    loadRegistryRecycleBin,
    restoreRegistryRecycleEntry,
};
