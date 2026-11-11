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

// QIN-591：资产库延后域模块（canvas）。首屏不加载；经 GW_DEFERRED 注册函数面，
// 由主文件网关（gwCall/gwLoad）按需加载或在空闲时段预热。函数体除跨文件赋值点改写外保持原样。

import {
    SMART_CANVAS_ASSET_INBOX_KEY,
    activeCanvasAssetCanvas,
    activeCanvasAssetCanvasId,
    activeCanvasAssetCategory,
    activeCanvasAssetCategoryInfo,
    activeTab,
    apiJsonResponse,
    assetBoardById,
    assetKind,
    assetThumb,
    canvasAssetCategories,
    canvasAssetCountForCanvas,
    canvasAssetManageMode,
    canvasAssetQuery,
    canvasAssetSort,
    canvasAssetViewTitle,
    canvasAssetsData,
    canvasAssetsForCategory,
    canvasKindLabel,
    defaultCanvasAssetCategory,
    detailImageEditorState,
    detailViewerState,
    escapeAttr,
    escapeHtml,
    findAssetItem,
    findCanvasAssetItem,
    findLocalUpload,
    refreshIcons,
    registryActiveSelectedIds,
    registryCanvasLoading,
    render,
    renderAssetTreePanel,
    root,
    selectedAssetIds,
    selectedCanvasAssetId,
    selectedCanvasAssetIds,
    selectedLocalUploadIds,
    setActiveCanvasAssetCanvasId,
    setActiveCanvasAssetCategory,
    setCanvasAssetsData,
    setRegistryBulkPanel,
    setRegistryCanvasLoading,
    setRegistryCanvasTargets,
    setSelectedCanvasAssetId,
    setSelectedCanvasAssetIds,
    setStatus,
    uniqueCanvasAssets,
} from '../asset-manager.js';
import assetManagerApi from './api.js';
import {
    formatDate,
} from './formatters.js';
import {
    canvasInboxAssetFromItem,
} from './inbox.js';
import {
    downloadUrl,
} from './organize.js';
import {
    applyDetailImageEditorZoom,
    detailImageEditorGroup,
    detailImageEditorTextBounds,
    detailImageEditorWorldPixels,
    positionDetailImageEditorEraserCursor,
} from './preview.js';
import {
    signalFlowAccentColor,
} from './settings.js';
import { GW_DEFERRED } from './state.js';

function canvasAssetViewSubtitle(items){
    const canvas = activeCanvasAssetCanvas();
    if(canvas) return `${canvasKindLabel(canvas.kind)} / ${items.length} 个资产 / ${escapeHtml(canvasAssetSortLabel())}`;
    const activeCat = activeCanvasAssetCategoryInfo();
    return `${Number(activeCat.canvas_count || 0)} 个画布 / ${items.length} 个资产 / ${escapeHtml(canvasAssetSortLabel())}`;
}
function canvasAssetKindLabel(item){
    const kind = assetKind(item);
    if(kind === 'video') return '视频';
    if(kind === 'audio') return '音频';
    if(String(item?.kind || '').toLowerCase() === 'text') return '文本';
    return '图片';
}
function canvasAssetSortLabel(){
    const map = {canvas_asc:'画布名称', updated_desc:'最近更新', updated_asc:'最早更新', name_asc:'名称 A-Z', kind:'类型'};
    return map[canvasAssetSort] || map.canvas_asc;
}
function currentCanvasAssetItems(){
    const q = String(canvasAssetQuery || '').trim().toLowerCase();
    let list = uniqueCanvasAssets(canvasAssetsData.items || []).filter(item => {
        // 【QIN-563】「全部画布」不按画布类型过滤，聚合智能/普通/参考画布的全部资产。
        if(activeCanvasAssetCategory !== 'all' && (item.canvas_kind || 'classic') !== activeCanvasAssetCategory) return false;
        if(activeCanvasAssetCanvasId && item.canvas_id !== activeCanvasAssetCanvasId) return false;
        if(!q) return true;
        return [
            item.name,
            item.url,
            item.canvas_title,
            item.node_title,
            item.node_type,
            canvasAssetKindLabel(item),
            canvasKindLabel(item.canvas_kind)
        ].join(' ').toLowerCase().includes(q);
    });
    list = list.slice();
    const byTime = item => Number(item.canvas_updated_at || item.created_at || 0);
    const byCanvasName = (a, b) => String(a.canvas_title || '').localeCompare(String(b.canvas_title || ''), 'zh-Hans-CN', {numeric:true, sensitivity:'base'})
        || String(a.canvas_id || '').localeCompare(String(b.canvas_id || ''), 'zh-Hans-CN')
        || byTime(b) - byTime(a);
    if(canvasAssetSort === 'canvas_asc') list.sort(byCanvasName);
    else if(canvasAssetSort === 'updated_asc') list.sort((a, b) => byTime(a) - byTime(b));
    else if(canvasAssetSort === 'name_asc') list.sort((a, b) => String(a.name || '').localeCompare(String(b.name || ''), 'zh-Hans-CN', {numeric:true, sensitivity:'base'}));
    else if(canvasAssetSort === 'kind') list.sort((a, b) => canvasAssetKindLabel(a).localeCompare(canvasAssetKindLabel(b), 'zh-Hans-CN') || byTime(b) - byTime(a));
    else list.sort((a, b) => byTime(b) - byTime(a));
    return list;
}
function groupCanvasAssetItems(items){
    if(canvasAssetSort !== 'canvas_asc') return [{key:'__all__', title:'', subtitle:'', items}];
    const groups = [];
    const map = new Map();
    (items || []).forEach(item => {
        const key = item.canvas_id || item.canvas_title || '__unknown__';
        if(!map.has(key)){
            const group = {
                key,
                title:item.canvas_title || '未命名画布',
                subtitle:`${canvasKindLabel(item.canvas_kind)} / ${formatDate(item.canvas_updated_at || item.created_at)}`,
                items:[]
            };
            map.set(key, group);
            groups.push(group);
        }
        map.get(key).items.push(item);
    });
    return groups;
}
function selectedCanvasAsset(){
    const items = currentCanvasAssetItems();
    return items.find(item => item.id === selectedCanvasAssetId) || items[0] || null;
}
async function loadRegistryCanvasTargets(){
    if(registryCanvasLoading) return;
    setRegistryCanvasLoading(true);
    render();
    try {
        const data = await apiJsonResponse(assetManagerApi.getCanvases());
        setRegistryCanvasTargets(Array.isArray(data.canvases) ? data.canvases : []);
    } finally {
        setRegistryCanvasLoading(false);
        render();
    }
}
async function attachSelectedRegistryAssetsToCanvas(createNew=false){
    const assetIds = registryActiveSelectedIds(assetBoardById(activeTab));
    if(!assetIds.length) return;
    const canvasId = createNew ? '' : String(document.getElementById('registryCanvasTarget')?.value || '');
    if(!createNew && !canvasId){ setStatus('请选择已有画布'); return; }
    const newTitle = String(document.getElementById('registryNewCanvasTitle')?.value || '资产画布').trim();
    const newKind = String(document.getElementById('registryNewCanvasKind')?.value || 'classic');
    const data = await apiJsonResponse(assetManagerApi.attachRegistryAssetsToCanvas({asset_ids:assetIds,canvas_id:canvasId,new_title:newTitle,new_kind:newKind}));
    setRegistryBulkPanel('');
    setRegistryCanvasTargets([]);
    render();
    setStatus(`${data.added || assetIds.length} 个素材已加入${data.canvas?.title ? `“${data.canvas.title}”` : '画布'}`);
}
function normalizeCanvasAssetState(){
    const cats = canvasAssetCategories();
    if(!cats.some(cat => cat.id === activeCanvasAssetCategory)) setActiveCanvasAssetCategory(defaultCanvasAssetCategory());
    const activeCanvas = activeCanvasAssetCanvas();
    if(
        activeCanvasAssetCanvasId
        && (
            !activeCanvas
            || (activeCanvasAssetCategory !== 'all' && (activeCanvas.kind || 'classic') !== activeCanvasAssetCategory)
        )
    ) setActiveCanvasAssetCanvasId('');
    const items = currentCanvasAssetItems();
    if(selectedCanvasAssetId && !items.some(item => item.id === selectedCanvasAssetId)) setSelectedCanvasAssetId('');
    if(!selectedCanvasAssetId && items.length) setSelectedCanvasAssetId(items[0].id);
    setSelectedCanvasAssetIds(new Set([...selectedCanvasAssetIds].filter(id => findCanvasAssetItem(id))));
}
async function refreshCanvasAssets(){
    try {
        setStatus('正在刷新画布资产...');
        const data = await apiJsonResponse(assetManagerApi.getCanvasAssets());
        setCanvasAssetsData({
            categories:Array.isArray(data.categories) ? data.categories : [],
            canvases:Array.isArray(data.canvases) ? data.canvases : [],
            items:Array.isArray(data.items) ? data.items : []
        });
        normalizeCanvasAssetState();
        render();
        setStatus('画布资产已刷新');
    } catch(err) {
        setStatus(err.message || '刷新画布资产失败');
    }
}
function renderCanvasAssetsManager(){
    normalizeCanvasAssetState();
    const items = currentCanvasAssetItems();
    const groups = groupCanvasAssetItems(items);
    const total = uniqueCanvasAssets(canvasAssetsData.items || []).length;
    // 【QIN-569 AC4】画布对象卡片：展示当前分类下全部画布（含 0 素材画布），
    // 数据来自现有 canvases + items 计数，点击复用画布行分发（聚焦该画布素材）。
    const categoryCanvases = canvasAssetsForCategory(activeCanvasAssetCategory);
    const detail = selectedCanvasAsset();
    root.innerHTML = `
        ${renderAssetTreePanel()}}
        <section class="asset-panel asset-content ${canvasAssetManageMode ? 'manage-on' : ''}">
            <div class="content-toolbar">
                <div class="content-heading">
                    <strong>${escapeHtml(canvasAssetViewTitle())}</strong>
                    <span>${canvasAssetViewSubtitle(items)}</span>
                </div>
                <div class="asset-tools">
                    <button class="hw-btn" type="button" data-canvas-asset-refresh title="重新读取画布中的图片、视频、音频资源"><i data-lucide="refresh-cw"></i><span>刷新资源</span></button>
                    <label class="asset-search-wrap"><i data-lucide="search"></i><input id="canvasAssetSearch" class="asset-search" type="search" value="${escapeAttr(canvasAssetQuery)}" placeholder="搜索画布资产"></label>
                    <select id="canvasAssetSort" class="manage-select canvas-sort-select" title="排序方法">
                        <option value="canvas_asc" ${canvasAssetSort === 'canvas_asc' ? 'selected' : ''}>画布名称</option>
                        <option value="updated_desc" ${canvasAssetSort === 'updated_desc' ? 'selected' : ''}>最近更新</option>
                        <option value="updated_asc" ${canvasAssetSort === 'updated_asc' ? 'selected' : ''}>最早更新</option>
                        <option value="name_asc" ${canvasAssetSort === 'name_asc' ? 'selected' : ''}>资产名称</option>
                        <option value="kind" ${canvasAssetSort === 'kind' ? 'selected' : ''}>类型</option>
                    </select>
                    <button class="${canvasAssetManageMode ? 'hw-btn hw-btn-gold' : 'hw-btn'}" type="button" data-canvas-asset-manage ${total ? '' : 'disabled'}><i data-lucide="list-checks"></i><span>${canvasAssetManageMode ? '完成管理' : '批量管理'}</span></button>
                </div>
            </div>
            <div class="manage-tools">
                <span>已选择 ${selectedCanvasAssetIds.size} 个画布资产，支持拖拽框选或逐个勾选。</span>
                <div class="asset-tools">
                    <button class="hw-btn" type="button" data-canvas-asset-select-all ${items.length ? '' : 'disabled'}><i data-lucide="check-square"></i><span>全选</span></button>
                    <button class="hw-btn" type="button" data-canvas-asset-clear-selection ${selectedCanvasAssetIds.size ? '' : 'disabled'}><i data-lucide="square"></i><span>清空</span></button>
                    <button class="hw-btn hw-btn-gold" type="button" data-canvas-asset-download-selected ${selectedCanvasAssetIds.size ? '' : 'disabled'}><i data-lucide="download"></i><span>下载所选</span></button>
                </div>
            </div>
            <div class="content-scroll">
                ${renderCanvasObjectBoard(categoryCanvases)}
                <div class="asset-grid">
                    ${groups.map(group => renderCanvasAssetGroup(group)).join('')}
                    ${categoryCanvases.length ? '' : '<div class="empty-state">当前分类没有可下载的画布资产。可以点击“刷新资源”，或在画布中生成/导入图片、视频、音频后再查看。</div>'}
                </div>
            </div>
        </section>
        <aside class="asset-panel asset-detail">
            ${renderCanvasAssetDetail(detail)}
        </aside>
    `;
}
function renderCanvasObjectBoard(canvases){
    // 【QIN-569 AC4】画布对象卡片区：当前分类下全部画布对象（含 0 素材画布），
    // 标题/类型/素材数来自 canvases + canvasAssetCountForCanvas(items 聚合)。
    // 点击复用画布行分发属性（data-canvas-asset-canvas），聚焦该画布查看素材。
    if(!Array.isArray(canvases) || !canvases.length) return '';
    return `<div class="canvas-object-board" role="list" aria-label="画布对象">${canvases.map(canvas => {
        const canvasId = String(canvas.canvas_id || canvas.id || '');
        const count = canvasAssetCountForCanvas(canvasId);
        const active = activeCanvasAssetCanvasId === canvasId;
        return `<article class="canvas-object-card ${active ? 'active' : ''}" role="listitem" tabindex="0" data-canvas-asset-canvas="${escapeAttr(canvasId)}" data-canvas-asset-canvas-cat="${escapeAttr(activeCanvasAssetCategory || 'all')}" aria-pressed="${active}" aria-label="查看画布 ${escapeAttr(canvas.title || '未命名画布')}">
            <span class="canvas-object-icon"><i data-lucide="${canvas.kind === 'smart' ? 'sparkles' : canvas.kind === 'reference' ? 'map' : 'layout-dashboard'}"></i></span>
            <span class="canvas-object-copy">
                <strong title="${escapeAttr(canvas.title || '未命名画布')}">${escapeHtml(canvas.title || '未命名画布')}</strong>
                <small>${escapeHtml(canvasKindLabel(canvas.kind))} · ${count} 个素材</small>
            </span>
        </article>`;
    }).join('')}</div>`;
}
function renderCanvasAssetGroup(group){
    if(!group?.title) return (group?.items || []).map(item => renderCanvasAssetCard(item)).join('');
    return `<section class="canvas-asset-group">
        <div class="canvas-asset-group-head">
            <div>
                <strong title="${escapeAttr(group.title)}">${escapeHtml(group.title)}</strong>
                <span>${escapeHtml(group.subtitle || '')}</span>
            </div>
            <small>${(group.items || []).length} 个资产</small>
        </div>
        <div class="canvas-asset-group-grid">
            ${(group.items || []).map(item => renderCanvasAssetCard(item)).join('')}
        </div>
    </section>`;
}
function renderCanvasAssetCard(item){
    return `<article class="asset-card canvas-asset-card ${item.id === selectedCanvasAssetId ? 'active' : ''}" data-canvas-asset-card="${escapeAttr(item.id)}" tabindex="0" role="button" aria-label="查看 ${escapeAttr(item.name || '画布资产')} 详情">
        <input class="asset-card-check" type="checkbox" data-canvas-asset-check="${escapeAttr(item.id)}" ${selectedCanvasAssetIds.has(item.id) ? 'checked' : ''}>
        <div class="asset-thumb canvas-asset-thumb">${assetThumb(item)}${renderCanvasAssetKindBadge(item)}</div>
        <div class="asset-card-body">
            <div class="asset-card-name" title="${escapeAttr(item.name || '')}">${escapeHtml(item.name || 'canvas asset')}</div>
            <div class="asset-card-meta">${escapeHtml(canvasAssetKindLabel(item))} · ${escapeHtml(item.canvas_title || '未命名画布')}</div>
        </div>
    </article>`;
}
function renderCanvasAssetKindBadge(item){
    const kind = assetKind(item);
    const icon = kind === 'video' ? 'play' : kind === 'audio' ? 'file-audio' : kind === 'text' ? 'file-text' : 'image';
    return `<span class="asset-kind-badge ${escapeAttr(kind)}" title="${escapeAttr(canvasAssetKindLabel(item))}"><i data-lucide="${icon}"></i></span>`;
}
function renderCanvasAssetDetail(item){
    if(!item) return `<div class="panel-head"><div class="panel-title"><strong>画布资产详情</strong><span>选择一个画布资产查看详情</span></div></div><div class="detail-scroll"><div class="detail-empty"><i data-lucide="layout-dashboard"></i><span>暂无画布资产</span></div></div>`;
    const kind = assetKind(item);
    const canPreview = ['image','video'].includes(kind);
    return `
        <div class="panel-head">
            <div class="panel-title"><strong>画布资产详情</strong><span>${escapeHtml(canvasAssetKindLabel(item))}</span></div>
            <div class="panel-actions">
                ${canPreview ? `<button class="hw-btn hw-btn-icon" type="button" data-canvas-asset-preview="${escapeAttr(item.id)}" title="${kind === 'video' ? '预览视频' : '放大预览'}"><i data-lucide="${kind === 'video' ? 'play' : 'maximize-2'}"></i></button>` : ''}
                <button class="hw-btn hw-btn-icon" type="button" data-canvas-asset-open="${escapeAttr(item.id)}" title="打开链接"><i data-lucide="external-link"></i></button>
                <button class="hw-btn hw-btn-icon" type="button" data-canvas-asset-copy="${escapeAttr(item.id)}" title="复制链接"><i data-lucide="copy"></i></button>
                <button class="hw-btn hw-btn-gold" type="button" data-canvas-asset-download="${escapeAttr(item.id)}"><i data-lucide="download"></i><span>下载</span></button>
            </div>
        </div>
        <div class="detail-scroll">
            <div class="detail-media">
                ${canPreview
                    ? `<button class="detail-media-frame detail-media-zoomable" type="button" data-canvas-asset-preview="${escapeAttr(item.id)}" title="${kind === 'video' ? '点击预览视频' : '点击放大预览'}">${assetThumb(item)}</button>`
                    : `<div class="detail-media-frame">${assetThumb(item)}</div>`}
            </div>
            <div class="detail-body">
                <div class="detail-name">${escapeHtml(item.name || '画布资产')}</div>
                <div class="detail-meta-grid">
                    <div class="detail-meta"><span>类型</span><strong>${escapeHtml(canvasAssetKindLabel(item))}</strong></div>
                    <div class="detail-meta"><span>画布分类</span><strong>${escapeHtml(canvasKindLabel(item.canvas_kind))}</strong></div>
                    <div class="detail-meta"><span>来源画布</span><strong title="${escapeAttr(item.canvas_title || '')}">${escapeHtml(item.canvas_title || '未命名画布')}</strong></div>
                    <div class="detail-meta"><span>更新时间</span><strong>${escapeHtml(formatDate(item.canvas_updated_at || item.created_at))}</strong></div>
                    <div class="detail-meta"><span>来源节点</span><strong title="${escapeAttr(item.node_title || item.node_type || '')}">${escapeHtml(item.node_title || item.node_type || '节点')}</strong></div>
                    <div class="detail-meta"><span>节点类型</span><strong>${escapeHtml(item.node_type || '-')}</strong></div>
                </div>
                <div class="detail-url">${escapeHtml(item.url || '')}</div>
            </div>
        </div>
    `;
}
function refreshCanvasAssetSelectionOnly(){
    document.querySelectorAll('[data-canvas-asset-card]').forEach(card => {
        card.classList.toggle('active', card.dataset.canvasAssetCard === selectedCanvasAssetId);
    });
    document.querySelectorAll('[data-canvas-asset-check]').forEach(input => {
        input.checked = selectedCanvasAssetIds.has(input.dataset.canvasAssetCheck);
    });
    const detail = root.querySelector('.asset-detail');
    if(detail){
        detail.innerHTML = renderCanvasAssetDetail(selectedCanvasAsset());
        refreshIcons();
    }
}
async function downloadCanvasAssetItems(ids){
    const items = (ids || []).map(id => findCanvasAssetItem(id)).filter(item => item?.url);
    if(!items.length){
        setStatus('没有可下载的画布资产');
        return;
    }
    if(items.length === 1){
        const item = items[0];
        downloadUrl(item.url, item.name || 'canvas-asset');
        setStatus('已下载画布资产');
        return;
    }
    setStatus(items.length === 1 ? '正在下载画布资产...' : `正在打包 ${items.length} 个画布资产...`);
    const res = await assetManagerApi.downloadCanvasAssets({filename:'canvas-assets.zip', items:items.map(item => ({url:item.url, name:item.name || 'canvas-asset'}))});
    if(!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || '下载画布资产失败');
    const blob = await res.blob();
    const link = document.createElement('a');
    link.href = URL.createObjectURL(blob);
    link.download = items.length === 1 ? (items[0].name || 'canvas-asset') : 'canvas-assets.zip';
    document.body.appendChild(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(link.href), 1200);
    setStatus(`已下载 ${items.length} 个画布资产`);
}
function copySelectedAssetsToCanvas(){
    const items = [...selectedAssetIds]
        .map(id => findAssetItem(id))
        .filter(it => it?.url)
        .map(canvasInboxAssetFromItem);
    if(!items.length){ setStatus('没有可复制的素材'); return; }
    try {
        // 写入跨页剪贴板，画布页按 Ctrl+V 读取并批量粘贴
        localStorage.setItem(SMART_CANVAS_ASSET_INBOX_KEY, JSON.stringify({items, ts: Date.now()}));
    } catch(err){
        setStatus('复制失败：' + (err?.message || err));
        return;
    }
    setStatus(`已复制 ${items.length} 个素材，去智能画布按 Ctrl+V 粘贴`);
}
function copySelectedLocalUploadsToCanvas(){
    const items = [...selectedLocalUploadIds].map(id => findLocalUpload(id)).filter(it => it?.url).map(canvasInboxAssetFromItem);
    if(!items.length){ setStatus('没有可复制的素材'); return; }
    try {
        localStorage.setItem(SMART_CANVAS_ASSET_INBOX_KEY, JSON.stringify({items, ts: Date.now()}));
    } catch(err){
        setStatus('复制失败：' + (err?.message || err));
        return;
    }
    setStatus(`已复制 ${items.length} 个素材，去智能画布按 Ctrl+V 粘贴`);
}
function detailImageEditorCanvasPoint(event,canvas){
    const state = detailImageEditorState;
    const rect = canvas.getBoundingClientRect();
    return {
        x:Math.max(0,Math.min(state.crop.w,(event.clientX-rect.left)/Math.max(1,rect.width)*state.crop.w)),
        y:Math.max(0,Math.min(state.crop.h,(event.clientY-rect.top)/Math.max(1,rect.height)*state.crop.h)),
    };
}
function drawDetailImageEditorCanvas(includeGuides=true){
    const state = detailImageEditorState;
    const canvas = document.querySelector('[data-image-editor-canvas]');
    if(!state || !canvas) return;
    const outputScale = Math.min(1,4096/Math.max(state.crop.w,state.crop.h));
    canvas.width = Math.max(1,Math.round(state.crop.w*outputScale));
    canvas.height = Math.max(1,Math.round(state.crop.h*outputScale));
    const context = canvas.getContext('2d');
    context.setTransform(outputScale,0,0,outputScale,0,0);
    context.clearRect(0,0,state.crop.w,state.crop.h);
    context.save();
    context.translate(state.flipX ? state.crop.w : 0,state.flipY ? state.crop.h : 0);
    context.scale(state.flipX ? -1 : 1,state.flipY ? -1 : 1);
    context.drawImage(state.sourceImage,state.crop.x,state.crop.y,state.crop.w,state.crop.h,0,0,state.crop.w,state.crop.h);
    context.restore();
    state.layers.forEach(layer => {
        const group=detailImageEditorGroup(layer);
        if(layer.hidden || group?.hidden) return;
        if(layer.type === 'brush'){
            const layerCanvas=document.createElement('canvas');
            layerCanvas.width=canvas.width; layerCanvas.height=canvas.height;
            const layerContext=layerCanvas.getContext('2d');
            layerContext.setTransform(outputScale,0,0,outputScale,0,0);
            (layer.paths || []).forEach(path=>{
                if(!path.points?.length) return;
                layerContext.save();
                layerContext.globalCompositeOperation=path.mode === 'erase' ? 'destination-out' : 'source-over';
                layerContext.strokeStyle=path.color || signalFlowAccentColor();
                layerContext.fillStyle=path.color || signalFlowAccentColor();
                layerContext.lineWidth=Math.max(1,Number(path.width || 8));
                layerContext.lineCap='round'; layerContext.lineJoin='round';
                if(path.points.length===1){ const point=path.points[0]; layerContext.beginPath(); layerContext.arc(point.x,point.y,layerContext.lineWidth/2,0,Math.PI*2); layerContext.fill(); }
                else { layerContext.beginPath(); path.points.forEach((point,index)=>index?layerContext.lineTo(point.x,point.y):layerContext.moveTo(point.x,point.y)); layerContext.stroke(); }
                layerContext.restore();
            });
            context.save(); context.setTransform(1,0,0,1,0,0); context.drawImage(layerCanvas,0,0); context.restore();
        } else {
            context.save();
            context.font = `600 ${layer.fontSize}px "Microsoft YaHei",sans-serif`;
            context.textBaseline = 'top'; context.lineJoin = 'round';
            context.strokeStyle = 'rgba(0,0,0,.52)'; context.lineWidth = Math.max(2,layer.fontSize/14);
            context.fillStyle = layer.color;
            String(layer.text || '').split('\n').forEach((line,index)=>{
                const y=layer.y+index*layer.fontSize*1.22;
                context.strokeText(line,layer.x,y); context.fillText(line,layer.x,y);
            });
            context.restore();
        }
    });
    if(includeGuides && state.tool === 'select'){
        state.layers.filter(layer=>layer.type === 'text' && state.selectedLayerIds?.has(layer.id) && !layer.hidden).forEach(layer=>{
            const box=detailImageEditorTextBounds(layer,context);
            const guide=detailImageEditorWorldPixels(canvas,2),dash=detailImageEditorWorldPixels(canvas,7),handle=detailImageEditorWorldPixels(canvas,18);
            context.save(); context.strokeStyle='#ff6a2a'; context.lineWidth=guide; context.setLineDash([dash,dash*.7]);
            context.strokeRect(box.x,box.y,box.w,box.h); context.setLineDash([]);
            context.fillStyle='#f4f5f1'; context.strokeStyle='#ff6a2a'; context.lineWidth=guide;
            context.fillRect(box.x+box.w-handle/2,box.y+box.h-handle/2,handle,handle); context.strokeRect(box.x+box.w-handle/2,box.y+box.h-handle/2,handle,handle); context.restore();
        });
    }
    if(includeGuides && state.cropDraft){
        const draft = state.cropDraft;
        context.save();
        context.fillStyle='rgba(5,7,6,.62)';
        context.beginPath(); context.rect(0,0,state.crop.w,state.crop.h); context.rect(draft.x,draft.y,draft.w,draft.h); context.fill('evenodd');
        const guide=detailImageEditorWorldPixels(canvas,2),handle=detailImageEditorWorldPixels(canvas,20);
        context.strokeStyle = '#ff6a2a'; context.lineWidth = guide;
        context.strokeRect(draft.x,draft.y,draft.w,draft.h);
        [[draft.x,draft.y],[draft.x+draft.w/2,draft.y],[draft.x+draft.w,draft.y],[draft.x,draft.y+draft.h/2],[draft.x+draft.w,draft.y+draft.h/2],[draft.x,draft.y+draft.h],[draft.x+draft.w/2,draft.y+draft.h],[draft.x+draft.w,draft.y+draft.h]].forEach(([x,y])=>{ context.fillStyle='#f4f5f1'; context.fillRect(x-handle/2,y-handle/2,handle,handle); context.strokeRect(x-handle/2,y-handle/2,handle,handle); });
        context.restore();
    }
    applyDetailImageEditorZoom();
    positionDetailImageEditorEraserCursor();
}
function updateDetailImageEditorCanvasVersion(edit,version={}){
    const state=detailImageEditorState;
    if(!state) return;
    const versionUrl=version.id && state.sourceAssetId ? `/api/asset-registry/assets/${encodeURIComponent(state.sourceAssetId)}/image-versions/${encodeURIComponent(version.id)}/media` : '';
    const update=item=>item.id === state.itemId ? {...item,reference_edit:edit,url:versionUrl || item.url,natural_w:state.crop.w,natural_h:state.crop.h,image_version_selection:version.id || '',asset_version_id:version.id || '',asset_version_number:Number(version.version_number || 0)} : item;
    detailViewerState.mediaCanvasItems=detailViewerState.mediaCanvasItems.map(update);
    detailViewerState.items=detailViewerState.items.map(update);
}

// 【QIN-591】域函数面注册：主文件 gwCall('canvas', fn) 经由此对象分派。
GW_DEFERRED['canvas'] = {
    attachSelectedRegistryAssetsToCanvas,
    copySelectedAssetsToCanvas,
    copySelectedLocalUploadsToCanvas,
    currentCanvasAssetItems,
    downloadCanvasAssetItems,
    loadRegistryCanvasTargets,
    refreshCanvasAssetSelectionOnly,
    refreshCanvasAssets,
    renderCanvasAssetsManager,
};

export {
    currentCanvasAssetItems,
    detailImageEditorCanvasPoint,
    drawDetailImageEditorCanvas,
    normalizeCanvasAssetState,
    updateDetailImageEditorCanvasVersion,
};
