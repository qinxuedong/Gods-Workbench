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

// QIN-591：资产库延后域模块（coverflow-bridge）。首屏不加载；经 GW_DEFERRED 注册函数面，
// 由主文件网关（gwCall/gwLoad）按需加载或在空闲时段预热。函数体除跨文件赋值点改写外保持原样。

import {
    ASSET_DETAIL_MEDIA_COVERFLOW_VISIBLE_KEY,
    activeTab,
    assetBoardById,
    assetPreviewUrl,
    detailImageEditorState,
    detailMediaCanvasCoverflowVisible,
    detailMediaCanvasItems,
    detailMediaCanvasPersistId,
    detailMediaCanvasSelectedCoverflowAssets,
    detailViewerItem,
    detailViewerState,
    detailViewerUrl,
    gwSetDetailMediaCanvasCoverflowVisible,
    loadMoreRegistryAssets,
    registryAssetStructures,
    registryAssets,
    registryCoverflowAssets,
    registryCoverflowAssetsForItems,
    registryCoverflowCurrentAssetId,
    registryCoverflowSourceIsVisual,
    registryCoverflowSourceMediaKind,
    registryGroupStructureMembers,
    registryHasMoreAssets,
    registryStructureMemberAsset,
    selectedRegistryAssetId,
    setRegistryCoverflowBoardId,
    setRegistryCoverflowCurrentAssetId,
    setRegistryDetail,
    setSelectedRegistryAssetId,
    setStatus,
} from '../asset-manager.js';
import {
    detailMediaCanvasLibraryItems,
} from './media-canvas.js';
import {
    detailViewerItems,
    openDetailImageEditor,
    showDetailPreview,
    switchDetailImageEditorAsset,
} from './preview.js';
import { GW_DEFERRED } from './state.js';

function registryCoverflowBoard(board=assetBoardById(activeTab)){
    return board?.system === false ? board : null;
}
function registryCoverflowSourceIsImage(source, item={}){
    return registryCoverflowSourceMediaKind(source, item) === 'image';
}
async function loadMoreRegistryCoverflowAssets(board=registryCoverflowBoard(), seedItems=[]){
    const before = new Set(registryCoverflowAssets(board, seedItems).map(item => item.id));
    if(registryHasMoreAssets()) await loadMoreRegistryAssets();
    const current = registryCoverflowAssets(board, seedItems);
    const additions = current.filter(item => !before.has(item.id));
    return {
        assets:additions,
        // registryTotal includes non-visual records; Coverflow exposes its
        // loaded visual-media count after one user-driven page load.
        total:current.length,
        hasMore:registryHasMoreAssets(),
    };
}
function openRegistryCoverflow(trigger=null, options={}){
    const board = registryCoverflowBoard();
    const scopedItems = Array.isArray(options?.scopeAssets) ? options.scopeAssets : null;
    const assets = scopedItems ? registryCoverflowAssetsForItems(scopedItems) : registryCoverflowAssets(board);
    if(!assets.length){ setStatus('当前素材库没有可展示的图片或视频'); return; }
    // A freshly focused/selected asset must win over the prior carousel
    // position so Enter and the toolbar always center the current item.
    const selectedId = [options?.currentAssetId, selectedRegistryAssetId, registryCoverflowCurrentAssetId]
        .map(value => String(value || '').trim())
        .find(id => assets.some(item => item.id === id)) || assets[0].id;
    setRegistryCoverflowCurrentAssetId(selectedId);
    setRegistryCoverflowBoardId(board?.id || 'registry');
    const hasMore = !scopedItems && registryHasMoreAssets();
    window.dispatchEvent(new CustomEvent('gw:asset-coverflow-open', {detail:{
        mode:'fullscreen',
        source:'registry',
        scope:String(options?.scope || 'library'),
        structureId:String(options?.structureId || ''),
        assets,
        currentAssetId:selectedId,
        // While mixed registry pages remain, only the loaded image count is
        // truthful; the load callback returns the incremented count.
        total:assets.length,
        hasMore,
        loadMore:hasMore ? () => loadMoreRegistryCoverflowAssets(board, assets) : null,
        trigger,
        parent:options?.parent || null,
    }}));
}
function detailImageEditorCoverflowAssets(state=detailImageEditorState){
    const source = detailViewerState.source || 'registry';
    if(detailViewerState.referenceCanvasMode) return detailReferenceCanvasCoverflowAssets();
    const current = source === 'registry'
        ? (detailViewerItem(source, state?.itemId || '') || {})
        : (detailViewerItem(source, state?.itemId || '') || {});
    if(source === 'registry'){
        const board = assetBoardById(detailViewerState.originTab);
        return registryCoverflowAssets(board, current?.id ? [current] : []);
    }
    const values = [...detailViewerItems(source), current].filter(Boolean);
    const seen = new Set();
    // Keep the source media type authoritative before normalizing visual
    // records for the carousel module.
    return values.filter(item => registryCoverflowSourceIsVisual(source, item)).map(item => {
        const id = String(item?.id || '').trim();
        if(!id) return null;
        const persistId = detailMediaCanvasPersistId(item, source);
        const rawUrl = String(detailViewerUrl(source, item) || item.public_url || item.url || (persistId ? `/api/asset-registry/assets/${encodeURIComponent(persistId)}/media` : '')).trim();
        if(!rawUrl) return null;
        const preview = assetPreviewUrl(rawUrl, 512, item) || rawUrl;
        const mediaKind = registryCoverflowSourceMediaKind(source, item);
        return {...item, id, kind:mediaKind, mediaKind, url:rawUrl, public_url:rawUrl, mediaSrc:rawUrl, backdropSrc:preview, src:preview, previewUrl:preview, preview_url:preview, thumbnail_url:preview};
    }).filter(item => {
        if(!item || seen.has(item.id)) return false;
        seen.add(item.id);
        return true;
    });
}
function mountDetailImageEditorCoverflow(state, shell){
    if(!state || !shell) return;
    const assets = detailImageEditorCoverflowAssets(state);
    const source = detailViewerState.source || 'registry';
    const board = source === 'registry' ? assetBoardById(detailViewerState.originTab) : null;
    const currentAssetId = String(state.coverflowAssetId || state.itemId || assets[0]?.id || '').trim();
    const hasMore = source === 'registry' && registryHasMoreAssets();
    window.dispatchEvent(new CustomEvent('gw:asset-coverflow-open', {detail:{
        mode:'editor',
        source,
        scope:detailViewerState.referenceCanvasMode ? 'reference-canvas' : 'library',
        structureId:detailViewerState.referenceCanvasStructureId || '',
        host:shell,
        assets,
        currentAssetId,
        total:assets.length,
        hasMore,
        loadMore:hasMore ? () => loadMoreRegistryCoverflowAssets(board, assets) : null,
    }}));
}
function detailReferenceCanvasCoverflowAssets(){
    const source = detailViewerState.source || 'registry';
    const seen = new Set();
    const canvasNodeIds = new Set(
        detailMediaCanvasItems().map(item => String(detailMediaCanvasPersistId(item, source) || item.id || '').trim()).filter(Boolean)
    );
    // 已经加入到画布里的图像，底部 Coverflow 里不再显示，仅保留候选库中尚未入画的素材
    const candidatePool = [
        ...(detailViewerState.items || []),
        ...detailMediaCanvasLibraryItems(),
    ];
    return candidatePool.filter(item => {
        if(!registryCoverflowSourceIsVisual(source, item)) return false;
        const assetId = String(detailMediaCanvasPersistId(item, source) || item?.id || '').trim();
        return assetId && !canvasNodeIds.has(assetId);
    }).map(item => {
        const assetId = detailMediaCanvasPersistId(item, source);
        const id = String(assetId || item?.id || '').trim();
        if(!id || canvasNodeIds.has(id) || seen.has(id)) return null;
        const currentUrl = String(detailViewerUrl(source, item) || '').trim();
        const explicitVersion = /\/image-versions\/[^/?#]+\/media(?:\?|#|$)/i.test(currentUrl);
        const rawUrl = assetId && !explicitVersion
            ? `/api/asset-registry/assets/${encodeURIComponent(assetId)}/media`
            : String(item.assetUrl || item.asset_url || item.original_url || currentUrl || '').trim();
        if(!rawUrl) return null;
        seen.add(id);
        const preview = assetPreviewUrl(rawUrl, 512, item) || rawUrl;
        const mediaKind = registryCoverflowSourceMediaKind(source, item);
        return {...item, id, kind:mediaKind, mediaKind, url:rawUrl, public_url:rawUrl, mediaSrc:rawUrl, backdropSrc:preview, src:preview, previewUrl:preview, preview_url:preview, thumbnail_url:preview};
    }).filter(Boolean);
}
function mountDetailReferenceCanvasCoverflow(shell){
    if(!shell) return;
    const assets = detailReferenceCanvasCoverflowAssets();
    if(!assets.length) return;
    const source = detailViewerState.source || 'registry';
    const activeItem = detailMediaCanvasItems().find(value => String(value.id || '') === String(detailViewerState.mediaCanvasActiveId || ''))
        || detailViewerState.items?.[detailViewerState.index]
        || detailViewerItem(source, detailViewerState.id);
    const activeId = String(detailMediaCanvasPersistId(activeItem, source) || detailViewerState.mediaCanvasActiveId || detailViewerState.id || '').trim();
    const currentAssetId = assets.some(value => value.id === activeId) ? activeId : assets[0].id;
    window.dispatchEvent(new CustomEvent('gw:asset-coverflow-open', {detail:{
        mode:'editor', source, scope:'reference-canvas', structureId:detailViewerState.referenceCanvasStructureId || '', host:shell, assets, currentAssetId, total:assets.length,
    }}));
}
function openDetailMediaCanvasSelectionCoverflow(trigger=null){
    const assets = detailMediaCanvasSelectedCoverflowAssets();
    if(!assets.length){ setStatus('请先选择可预览的图片或视频'); return false; }
    const source = detailViewerState.source || 'registry';
    const activeItem = detailMediaCanvasItems().find(item => String(item.id || '') === String(detailViewerState.mediaCanvasActiveId || ''));
    const activeId = String(detailMediaCanvasPersistId(activeItem,source) || activeItem?.id || '');
    const currentAssetId = assets.some(item => item.id === activeId) ? activeId : assets[0].id;
    const referenceCanvas = Boolean(detailViewerState.referenceCanvasMode);
    window.dispatchEvent(new CustomEvent('gw:asset-coverflow-open', {detail:{
        mode:'fullscreen',
        source,
        scope:referenceCanvas ? 'reference-canvas' : 'library',
        structureId:referenceCanvas ? detailViewerState.referenceCanvasStructureId || '' : '',
        assets,
        currentAssetId,
        total:assets.length,
        trigger:trigger || document.activeElement,
        parent:document.querySelector('.asset-detail-viewer'),
    }}));
    return true;
}
async function openRegistryCoverflowAssetInEditor(detail={}){
    const source = String(detail.source || 'registry');
    const id = String(detail.assetId || detail.currentAssetId || '').trim();
    if(!id) return;
    const returnToCoverflow = detail.mode === 'fullscreen' ? {
        mode:'fullscreen', source, scope:String(detail.scope || 'library'),
        structureId:String(detail.structureId || ''), assets:Array.isArray(detail.assets) ? detail.assets : [],
        currentAssetId:id, total:Number(detail.total || detail.assets?.length || 0),
        hasMore:Boolean(detail.hasMore), loadMore:typeof detail.loadMore === 'function' ? detail.loadMore : null,
        trigger:detail.trigger || null, parent:detail.parent || null,
    } : null;
    const requestedAsset = detail.asset
        || detailReferenceCanvasCoverflowAssets().find(item => item.id === id)
        || detailMediaCanvasLibraryItems().find(item => String(detailMediaCanvasPersistId(item, source) || item.id || '') === id)
        || detailViewerItem(source, id)
        || null;
    if(requestedAsset && registryCoverflowSourceMediaKind(source, requestedAsset) !== 'image'){
        setStatus('视频素材可在 Coverflow 中预览，不能进入图像编辑器');
        return;
    }
    if(detail.mode === 'editor' && detailImageEditorState && source === detailViewerState.source && !detail.skipEditorSwitch){
        await switchDetailImageEditorAsset(detail);
        return;
    }
    const referenceCanvasItem = detailViewerState.open
        && detailViewerState.referenceCanvasMode
        && source === detailViewerState.source
        ? detailMediaCanvasItems().find(value => String(detailMediaCanvasPersistId(value, source) || value.id || '') === id)
        : null;
    const referenceCanvasLibraryItem = !referenceCanvasItem
        && detailViewerState.open
        && detailViewerState.referenceCanvasMode
        && source === detailViewerState.source
        ? detailMediaCanvasLibraryItems().find(value => String(detailMediaCanvasPersistId(value, source) || value.id || '') === id)
        : null;
    const hasDetailMediaCanvas = detailViewerState.open
        && source === detailViewerState.source
        && (detailViewerState.referenceCanvasMode || Boolean(document.querySelector('[data-detail-media-canvas-viewport]')));
    const detailMediaCanvasItem = hasDetailMediaCanvas
        ? detailMediaCanvasItems().find(value => String(detailMediaCanvasPersistId(value, source) || value.id || '') === id)
        : null;
    if(referenceCanvasItem || referenceCanvasLibraryItem || detailMediaCanvasItem){
        const item = referenceCanvasItem || referenceCanvasLibraryItem || detailMediaCanvasItem;
        if(detail.mode === 'fullscreen') window.dispatchEvent(new CustomEvent('gw:asset-coverflow-close', {detail:{mode:'fullscreen'}}));
        await openDetailImageEditor(item.id, {item,returnToCoverflow});
        return;
    }
    if(detail.mode === 'fullscreen') window.dispatchEvent(new CustomEvent('gw:asset-coverflow-close', {detail:{mode:'fullscreen'}}));
    const item = detailMediaCanvasItem || detailViewerItem(source, id) || detail.asset || requestedAsset || null;
    if(!item) throw new Error('未找到 Coverflow 当前图片');
    if(source === 'registry'){
        setRegistryCoverflowCurrentAssetId(id);
        setSelectedRegistryAssetId(id);
        setRegistryDetail(registryAssets.find(value => value.id === id) || item);
    }
    if(!detailMediaCanvasItem) showDetailPreview(source, id);
    await openDetailImageEditor(id, {item,returnToCoverflow});
}
function registryGroupMemberSelectionIds(structure){
    return registryGroupStructureMembers(structure).map(value => value.asset.id);
}
function registryGroupMemberAsset(assetId, structureId=''){
    const id = String(assetId || '').trim();
    if(!id) return null;
    const structures = structureId ? [registryAssetStructures.get(String(structureId))] : [...registryAssetStructures.values()];
    for(const structure of structures){
        const asset = structure ? registryStructureMemberAsset(structure, id) : null;
        if(asset) return asset;
    }
    // Prefer the structure's member snapshot above.  A paged registry item
    // can lag behind a recycle/status update and must not make an unavailable
    // group member appear editable again.
    return registryAssets.find(item => String(item.id || '') === id) || null;
}

function setDetailMediaCanvasCoverflowVisible(visible){
    gwSetDetailMediaCanvasCoverflowVisible(Boolean(visible));
    detailViewerState.mediaCanvasCoverflowVisible = detailMediaCanvasCoverflowVisible;
    try { localStorage.setItem(ASSET_DETAIL_MEDIA_COVERFLOW_VISIBLE_KEY, String(detailMediaCanvasCoverflowVisible)); } catch(_) {}
    const shell = document.querySelector('.asset-detail-viewer .asset-detail-media-canvas-shell');
    if(shell){
        if(detailMediaCanvasCoverflowVisible){
            shell.classList.add('gw-coverflow-embedded-host');
            mountDetailReferenceCanvasCoverflow(shell);
        } else {
            shell.classList.remove('gw-coverflow-embedded-host');
            shell.querySelector('.gw-coverflow--embedded')?.remove();
            window.dispatchEvent(new CustomEvent('gw:asset-coverflow-close', {detail:{mode:'editor'}}));
        }
    }
    document.querySelectorAll('.asset-detail-viewer [data-detail-media-coverflow-toggle]').forEach(button => {
        button.classList.toggle('is-active', detailMediaCanvasCoverflowVisible);
        button.setAttribute('aria-pressed', String(detailMediaCanvasCoverflowVisible));
        button.title = detailMediaCanvasCoverflowVisible ? '隐藏 Coverflow 预览' : '显示 Coverflow 预览';
        button.setAttribute('aria-label', button.title);
    });
}
function restoreDetailImageEditorCoverflow(state){
    const detail = state?.returnToCoverflow;
    if(!detail) return;
    requestAnimationFrame(() => {
        if(!detailImageEditorState){
            window.dispatchEvent(new CustomEvent('gw:asset-coverflow-open', {detail}));
        }
    });
}

// 【QIN-591】域函数面注册：主文件 gwCall('coverflow-bridge', fn) 经由此对象分派。
GW_DEFERRED['coverflow-bridge'] = {
    openDetailMediaCanvasSelectionCoverflow,
    openRegistryCoverflow,
    openRegistryCoverflowAssetInEditor,
    setDetailMediaCanvasCoverflowVisible,
};

export {
    detailReferenceCanvasCoverflowAssets,
    mountDetailImageEditorCoverflow,
    mountDetailReferenceCanvasCoverflow,
    openRegistryCoverflowAssetInEditor,
    registryCoverflowBoard,
    restoreDetailImageEditorCoverflow,
    setDetailMediaCanvasCoverflowVisible,
};
