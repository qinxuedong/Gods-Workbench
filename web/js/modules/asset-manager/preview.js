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

// QIN-591：资产库延后域模块（preview）。首屏不加载；经 GW_DEFERRED 注册函数面，
// 由主文件网关（gwCall/gwLoad）按需加载或在空闲时段预热。函数体除跨文件赋值点改写外保持原样。

import {
    ASSET_BROWSER_PREVIEW_EXTENSIONS,
    ASSET_DETAIL_FONT_PANEL_KEY,
    ASSET_DETAIL_INSPECTOR_COLLAPSED_KEY,
    ASSET_DETAIL_MEDIA_LIBRARY_SIZE_KEY,
    ASSET_FONT_PREVIEW_EN,
    ASSET_FONT_PREVIEW_LETTERS_LOWER,
    ASSET_FONT_PREVIEW_LETTERS_UPPER,
    ASSET_FONT_PREVIEW_NUMBERS,
    ASSET_FONT_PREVIEW_PUNCTUATION,
    ASSET_FONT_PREVIEW_PUNCTUATION_ZH,
    ASSET_FONT_PREVIEW_ZH,
    ASSET_FONT_PREVIEW_ZH_LINE,
    ASSET_REGISTRY_DETAIL_COLLAPSED_KEY,
    ASSET_REGISTRY_DETAIL_WIDTH_KEY,
    DETAIL_BACKQUOTE_DOUBLE_TAP_MS,
    VIDEO_STORYBOARD_IMAGE_STYLE,
    VIDEO_STORYBOARD_LARGE_STYLE,
    activeAssetCategory,
    activeAssetLibrary,
    activeAssetLibraryId,
    activeRegistryVisibleAssets,
    activeTab,
    alignTimeToFps,
    apiJsonResponse,
    assetBoardById,
    assetEditMode,
    assetFileExtension,
    assetHoverPreviewMedia,
    assetKind,
    assetPreviewAllState,
    assetPreviewUrl,
    assetThumb,
    assetVideoUrl,
    boardFolderSelection,
    boardGroupSelection,
    boardReferenceCanvasSelection,
    boardSourceSelection,
    canvasAssetsData,
    closeDetailPreview,
    closeRegistrySelectMenus,
    closeRegistryStructureMenu,
    closeRegistryVersionStructureRemoveDialog,
    confirmRegistryVersionStructureRemoval,
    customAssetLibraryBaseItems,
    detailActiveImageItem,
    detailImageEditorBackquoteCloseTimer,
    detailImageEditorState,
    detailImageVersionAssetId,
    detailMediaCanvasColorSnapshot,
    detailMediaCanvasCoverflowVisible,
    detailMediaCanvasItems,
    detailMediaCanvasLibraryCollapsed,
    detailMediaCanvasLibraryThumbSize,
    detailMediaCanvasLibraryWidth,
    detailMediaCanvasMinimapVisible,
    detailMediaCanvasPersistId,
    detailMediaCanvasSelectedIds,
    detailMediaCanvasSelectionObjectId,
    detailMediaCanvasSpacePressed,
    detailMediaLutSplitDragEndedAt,
    detailPreviewBackquoteCloseTimer,
    detailReferenceFromTarget,
    detailVideoStoryboardEnabled,
    detailViewerAudioFrame,
    detailViewerAudioWaveformCache,
    detailViewerCanEditText,
    detailViewerFileInfoCache,
    detailViewerFileParams,
    detailViewerFontExpandedPreference,
    detailViewerFontListCollapsed,
    detailViewerInspectorCollapsed,
    detailViewerItem,
    detailViewerMediaPreloads,
    detailViewerSourceLabel,
    detailViewerState,
    detailViewerTextCache,
    detailViewerType,
    detailViewerUrl,
    detailViewerVideo,
    detailViewerVideoItem,
    ensureMediaPreviewPlayer,
    escapeAttr,
    escapeHtml,
    findAssetItem,
    findCanvasAssetItem,
    formatMediaDuration,
    handleClick,
    loadDetailImageVersions,
    localAssets,
    localItemMap,
    mergeRegistryAssetUpdate,
    normalizeRegistryAsset,
    openRegistryStructureCoverflow,
    openRegistryStructureReferenceCanvas,
    openRegistryStructureRemoveDialog,
    parseRegistryTagNames,
    pendingDeleteAssetId,
    refreshIcons,
    refreshRegistryAssets,
    registryAssetStructures,
    registryAssets,
    registryCategoryCount,
    registryCoverflowAssets,
    registryCoverflowCurrentAssetId,
    registryDetail,
    registryDetailPanelCollapsed,
    registryDetailPanelWidth,
    registryFullscreenViewerState,
    registryGroupStructureMembers,
    registryModifiedLabel,
    registryPdfBusy,
    registrySort,
    registryStructureIsRenderable,
    registryStructureMemberAsset,
    registryStructureMenu,
    registryTotal,
    registryTotalKnown,
    render,
    renderDetailVideoSegments,
    renderDetailVideoStoryboardPanel,
    renderDetailVideoSummary,
    renderRegistryDetail,
    renderRegistryEditableTags,
    renderRegistryRelations,
    root,
    runRegistryStructureAction,
    scheduleAssetTouchbarReport,
    scheduleRegistryMasonryLayout,
    seekDetailVideoTo,
    selectedAssetId,
    selectedRegistryAssetId,
    setAssetHoverPreviewMedia,
    setAssetPreviewAllState,
    setDetailImageEditorBackquoteCloseTimer,
    setDetailImageEditorState,
    setDetailMediaCanvasColorSnapshot,
    setDetailMediaCanvasLibraryThumbSize,
    setDetailPreviewBackquoteCloseTimer,
    setDetailTextEditorBackquoteInput,
    setDetailViewerAudioFrame,
    setDetailViewerFontExpandedPreference,
    setDetailViewerState,
    setRegistryAssets,
    setRegistryDetail,
    setRegistryDetailPanelCollapsed,
    setRegistryFocusIndex,
    setRegistryFullscreenViewerState,
    setRegistryTagEditorAssetId,
    setRegistryTotal,
    setRegistryVersionStructureCurrent,
    setSelectedAssetId,
    setSelectedCanvasAssetId,
    setSelectedLocalId,
    setSelectedLocalUploadId,
    setSelectedPromptId,
    setSelectedRegistryAssetId,
    setSelectedWorkflowId,
    setStatus,
    setThumbnailCacheVersion,
    setThumbnailGeneratorState,
    showAssetFailure,
    storageSettingsState,
    syncRegistrySelectionViews,
    thumbnailGeneratorState,
    videoStoryboardImageCache,
    workflowProvenanceLinks,
    workflowThumb,
} from '../asset-manager.js';
import {
    assetManagerApi,
} from './api.js';
import {
    renderCollectProvenance,
} from './collect-provenance.js';
import {
    formatDate,
    formatFileSize,
} from './formatters.js';
import {
    assetManagerHttp,
} from './http.js';
import {
    renderProvenanceChain,
} from './provenance.js';
import {
    workflowSourceId,
} from './workflow-assets.js';
import {
    currentCanvasAssetItems,
    detailImageEditorCanvasPoint,
    drawDetailImageEditorCanvas,
} from './canvas-bridge.js';
import {
    mountDetailImageEditorCoverflow,
    mountDetailReferenceCanvasCoverflow,
    openRegistryCoverflowAssetInEditor,
    registryCoverflowBoard,
    restoreDetailImageEditorCoverflow,
    setDetailMediaCanvasCoverflowVisible,
} from './coverflow-bridge.js';
import {
    clearDetailMediaNodeLut,
    closeDetailMediaNodeLutPanel,
    handleDetailMediaLutMenuClick,
    hydrateDetailMediaCanvasLut,
    initializeDetailLutPreview,
    navigateDetailLutToId,
    refreshDetailMediaLutEntryDom,
    renderDetailLutWorkspace,
    restoreDetailLutListScroll,
    saveDetailMediaNodeLutVersion,
    setDetailLutCompare,
    setDetailLutStrength,
    setDetailMediaNodeLut,
    setDetailMediaNodeLutCompare,
    setDetailMediaNodeLutStrength,
    toggleDetailMediaNodeLutPanel,
} from './lut.js';
import {
    activateDetailMediaCanvasItem,
    addDetailMediaCanvasLibraryItem,
    addSelectedAssetsToDetailMediaCanvas,
    adjustDetailMediaCanvasZoom,
    applyDetailMediaCanvasAnnotationColor,
    beginDetailMediaCanvasAnnotationEdit,
    closeDetailMediaCanvasContextMenu,
    deleteDetailMediaCanvasSelection,
    detailMediaCanvasInitialLibraryScope,
    detailMediaCanvasLibraryItems,
    ensureDetailReferenceAssetIds,
    finishDetailMediaCanvasAnnotationEdit,
    fitDetailMediaCanvas,
    fitDetailMediaCanvasAnnotationToText,
    handleDetailMediaCanvasContextAction,
    initialDetailMediaCanvasDrawings,
    initialDetailMediaCanvasGroups,
    initialDetailMediaCanvasItems,
    initialDetailMediaCanvasLayout,
    loadDetailMediaCanvasLibraryRoot,
    mergeResolvedReferenceAsset,
    observeDetailMediaCanvasViewport,
    openReferenceCanvasDetail,
    pushDetailMediaCanvasHistory,
    refreshDetailMediaCanvasLibraryGrid,
    refreshDetailMediaCanvasSidebarDetail,
    renderDetailMediaCanvas,
    renderDetailMediaCanvasLibrary,
    saveDetailReferenceCanvas,
    selectDetailMediaCanvasItem,
    selectDetailMediaCanvasLibraryItem,
    setDetailMediaCanvasMinimapVisible,
    setDetailMediaCanvasSidebarView,
    syncDetailMediaCanvasActiveDom,
    toggleDetailMediaCanvasLibrary,
} from './media-canvas.js';
import {
    activateAssetBoard,
    assetKindLabel,
    beginDetailMediaLibraryDrag,
    closeBoardCreatePathBrowser,
    closeBoardEditModal,
    closeBoardSourceModal,
    closeCreateLibraryModal,
    closeCustomBoardFolderCreateModal,
    copyDetailVideoFrameToClipboard,
    currentAssetItems,
    currentPromptItems,
    currentWorkflowItems,
    detailViewerPromptText,
    downloadRegistryPdf,
    formatModelCount,
    handleAssetOrganizeOverlayClick,
    importDetailVideoBlobToLibrary,
    registryDownloadName,
    renderDetailFontLibrary,
    saveDetailImageEditorToLibrary,
    syncBoardCreateDraftFromDom,
    textVersionActionLabel,
    toggleDetailFontLibrary,
} from './organize.js';
import {
    chooseRegistryRecyclerSetup,
    closeRegistryRecycleBin,
    closeRegistryRecycleDialog,
    closeRegistryRecyclerSetupDialog,
    confirmRegistryRecycle,
    loadRegistryRecycleBin,
    restoreRegistryRecycleEntry,
} from './recycle.js';
import {
    closeStorageSettings,
    closeThumbnailGenerator,
    detailWritableStorageRoots,
    refreshThumbnailCacheStats,
    renderAvatarSection,
    renderClassificationChips,
    renderStorageSettingsModal,
    saveDetailImageEditorAs,
    saveDetailImageEditorVersion,
    saveDetailTextEdit,
    saveDetailTextVersionEdit,
    saveDetailVideoBlobToPath,
    saveDetailViewerName,
    signalFlowAccentColor,
    syncProjectDirectoryTemplateDraft,
    thumbnailGeneratorAssetIds,
    updateThumbnailCacheExclusionHint,
} from './settings.js';
import {
    addRegistryAssetTags,
    deleteRegistryAssetTag,
    detailViewerTags,
} from './tags.js';
import {
    localItemsForFolder,
    localUploadItems,
} from './upload.js';
import { GW_DEFERRED } from './state.js';

let registryDetailPanelResizeState = null;
let detailImageEditorOpeningPromise = null;
let detailImageEditorOpeningItemId = '';
let detailImageEditorPointer = null;
let detailCinemaClickState = {id:'', at:0};
let detailMediaCanvasPositionRaf = 0;
let detailViewerFontFace = null;
let detailViewerFontLoadToken = 0;
function markAssetFloatingSurface(element, {surface, kind='modal', content='self', close, trigger, parent}={}){
    if(!element || !surface) return element;
    const attributes = {
        'data-floating-surface': surface,
        'data-floating-kind': kind,
        'data-floating-content': content,
        'data-floating-dismiss': 'outside',
        'data-floating-escape': 'close-only',
    };
    if(typeof close === 'string' && close) attributes['data-floating-close'] = close;
    if(typeof trigger === 'string' && trigger) attributes['data-floating-trigger'] = trigger;
    if(typeof parent === 'string' && parent) attributes['data-floating-parent'] = parent;
    Object.entries(attributes).forEach(([name, value]) => element.setAttribute(name, String(value)));
    window.FloatingDismissal?.mark(element, {surface, kind, content, close, trigger, parent});
    return element;
}
function openEpisodePipelineFromAsset(asset={}){
    const params = new URLSearchParams();
    if(asset.projectId) params.set('project_id', String(asset.projectId));
    if(asset.pipelineId) params.set('pipeline_id', String(asset.pipelineId));
    const query = params.toString();
    try {
        const target = parent.document.getElementById('frame-episode-pipeline');
        if(target){
            const source = target.dataset.src || '/static/episode-pipeline.html?embedded=1';
            const targetUrl = new URL(source, window.location.origin);
            if(asset.projectId) targetUrl.searchParams.set('project_id', String(asset.projectId));
            if(asset.pipelineId) targetUrl.searchParams.set('pipeline_id', String(asset.pipelineId));
            target.src = targetUrl.href;
            parent.switchUI(parent.document.querySelector("[onclick*=\"'episode-pipeline'\"]"), 'episode-pipeline');
            return;
        }
    } catch(_) {}
    const workbenchTarget = `/static/pages/workshop.html?step=script${query ? `&${query}` : ''}`;
    try {
        if(window.top !== window){ window.top.location.href = workbenchTarget; return; }
    } catch(_) {}
    location.href = workbenchTarget;
}
function previewCacheStats(){
    const raw = thumbnailGeneratorState.cacheStats;
    const stats = (raw && typeof raw === 'object') ? raw : {};
    const fallbackTotal = registryCategoryCount('image') + registryCategoryCount('video');
    const categories = {};
    for(const key of ['image','video','storyboard','other']) categories[key] = {files:0, bytes:0};
    for(const item of (Array.isArray(stats.categories) ? stats.categories : [])){
        const key = String(item?.id || '');
        if(!(key in categories)) continue;
        categories[key] = {files:Number(item.files || 0) || 0, bytes:Number(item.bytes || 0) || 0};
    }
    return {
        available: stats.available !== false,
        totalAssets: Number(stats.total_assets ?? fallbackTotal) || 0,
        cachedAssets: Number(stats.cached_assets || 0) || 0,
        bytes: Number(stats.bytes || 0) || 0,
        categories,
    };
}
function previewCacheDeleteSelection(){
    const stats = previewCacheStats();
    const categories = ['image','video','storyboard','other'].filter(key => assetPreviewAllState.deleteCategories[key]);
    return {
        categories,
        files: categories.reduce((sum, key) => sum + Number(stats.categories[key]?.files || 0), 0),
        bytes: categories.reduce((sum, key) => sum + Number(stats.categories[key]?.bytes || 0), 0),
    };
}
function renderPreviewCacheDeletePanel(){
    const state = assetPreviewAllState;
    if(!state.deleteOpen) return '';
    const stats = previewCacheStats();
    const rows = [
        {id:'image', label:'图像预览'},
        {id:'video', label:'视频预览'},
        {id:'storyboard', label:'视频分镜'},
        {id:'other', label:'其他'},
    ].map(row => {
        const info = stats.categories[row.id] || {files:0, bytes:0};
        return `<label class="preview-cache-delete-row"><input type="checkbox" data-preview-delete-toggle="${row.id}" ${state.deleteCategories[row.id] ? 'checked' : ''} ${info.files ? '' : 'disabled'}><span><strong>${row.label}</strong><small>${info.files.toLocaleString('zh-CN')} 个文件 · ${escapeHtml(formatFileSize(info.bytes))}</small></span></label>`;
    }).join('');
    const selection = previewCacheDeleteSelection();
    const allChecked = selection.categories.length === 4;
    return `<div class="preview-cache-delete-panel" data-preview-delete-panel>
        <p class="board-settings-hint">勾选要删除的缓存分类；素材源文件、代理与无损转码产物不受影响。</p>
        ${rows}
        <div class="preview-cache-delete-foot">
            <label class="preview-cache-delete-all"><input type="checkbox" data-preview-delete-toggle="all" ${allChecked ? 'checked' : ''}><span>全选</span></label>
            <div class="board-thumbnail-actions">
                <button class="hw-btn" type="button" data-preview-delete-cancel>取消</button>
                <button class="hw-btn hw-btn-danger" type="button" data-preview-delete-confirm ${selection.files && !state.deleting ? '' : 'disabled'}><i data-lucide="${state.deleting ? 'loader-2' : 'trash-2'}"></i><span>${state.deleting ? '正在删除…' : `删除 ${selection.files.toLocaleString('zh-CN')} 个文件`}</span></button>
            </div>
        </div>
    </div>`;
}
function requestPreviewAllConfirm(mode='fill'){
    if(assetPreviewAllState.submitting) return;
    setAssetPreviewAllState({
        ...assetPreviewAllState,
        confirming:true,
        confirmMode: mode === 'rebuild' ? 'rebuild' : 'fill',
        deleteOpen:false,
        // 清掉上一次任务的结果行：新动作不该和旧的「失败 / 已重建」混在一起。
        job:null,
        error:'',
        notice:'',
    });
    renderStorageSettingsModal();
}
function cancelPreviewAllConfirm(){
    setAssetPreviewAllState({...assetPreviewAllState, confirming:false, confirmMode:'fill'});
    renderStorageSettingsModal();
}
async function startPreviewAllCache(){
    if(assetPreviewAllState.submitting) return;
    const mode = assetPreviewAllState.confirmMode === 'rebuild' ? 'rebuild' : 'fill';
    setAssetPreviewAllState({...assetPreviewAllState, confirming:false, confirmMode:'fill', submitting:true, error:'', notice:''});
    renderStorageSettingsModal();
    try {
        const accepted = await apiJsonResponse(assetManagerHttp.request('/api/asset-thumbnails/generate-background', {
            method:'POST', credentials:'same-origin', headers:{'Content-Type':'application/json'}, body:JSON.stringify({scope:'library', mode})
        }));
        const jobId = accepted.job_id || accepted.task?.id || '';
        const total = Number(accepted.total || accepted.task?.progress?.total || 0);
        setAssetPreviewAllState({
            ...assetPreviewAllState,
            submitting:false,
            job:{job_id:jobId, status:'running', mode, progress:{processed:0, total}, result:null},
            pollingJobId:jobId,
        });
        renderStorageSettingsModal();
        setStatus(`${mode === 'rebuild' ? '已开始重建' : '已开始缓存'}全库预览：共 ${total.toLocaleString('zh-CN')} 项，可继续使用资产中心`);
        if(jobId) window.setTimeout(() => void pollPreviewAllJob(jobId), 600);
    } catch(error){
        setAssetPreviewAllState({...assetPreviewAllState, submitting:false, error:`${error.code ? `${error.code}：` : ''}${error.message || '提交全库缓存失败'}`});
        renderStorageSettingsModal();
    }
}
function openPreviewCacheDeletePanel(){
    setAssetPreviewAllState({...assetPreviewAllState, deleteOpen:true, confirming:false, confirmMode:'fill', job:null, error:'', notice:''});
    renderStorageSettingsModal();
}
function cancelPreviewCacheDelete(){
    setAssetPreviewAllState({...assetPreviewAllState, deleteOpen:false, deleteCategories:{image:true, video:true, storyboard:true, other:true}});
    renderStorageSettingsModal();
}
async function confirmPreviewCacheDelete(){
    const state = assetPreviewAllState;
    if(state.deleting) return;
    const selection = previewCacheDeleteSelection();
    if(!selection.categories.length) return;
    setAssetPreviewAllState({...assetPreviewAllState, deleting:true, error:'', notice:''});
    renderStorageSettingsModal();
    try {
        const data = await apiJsonResponse(assetManagerHttp.request('/api/asset-thumbnails/delete', {
            method:'POST', credentials:'same-origin', headers:{'Content-Type':'application/json'},
            body:JSON.stringify({scope:'all', categories:selection.categories})
        }));
        const removed = Number(data.removed || 0);
        const failed = Array.isArray(data.failed) ? data.failed.length : 0;
        setAssetPreviewAllState({
            ...assetPreviewAllState,
            deleting:false,
            deleteOpen:false,
            deleteCategories:{image:true, video:true, storyboard:true, other:true},
            // 缓存已清空，上一轮任务结果不再代表当前状态，一并清掉避免并存误导。
            job:null,
            notice: failed ? `已删除 ${removed.toLocaleString('zh-CN')} 个缓存文件，${failed} 个删除失败` : `已删除 ${removed.toLocaleString('zh-CN')} 个缓存文件`,
            error:'',
        });
        renderStorageSettingsModal();
        setStatus(`已删除 ${removed} 个预览缓存文件`);
        await refreshThumbnailCacheStats();
    } catch(error){
        setAssetPreviewAllState({...assetPreviewAllState, deleting:false, error:`${error.code ? `${error.code}：` : ''}${error.message || '删除缓存失败'}`});
        renderStorageSettingsModal();
    }
}
async function pollPreviewAllJob(jobId){
    if(!jobId) return;
    try {
        const data = await apiJsonResponse(assetManagerHttp.request(`/api/asset-thumbnails/jobs/${encodeURIComponent(jobId)}`, {method:'GET', credentials:'same-origin'}));
        const task = data.task || {};
        const running = ['queued','running','paused'].includes(task.status);
        const previous = assetPreviewAllState.job;
        // 状态与进度都没变就别重建整个设置面板：整树替换会打断正在点的按钮。
        const unchanged = Boolean(previous
            && previous.status === task.status
            && Number(previous.progress?.processed || 0) === Number(task.progress?.processed || 0)
            && assetPreviewAllState.pollingJobId === (running ? jobId : ''));
        setAssetPreviewAllState({...assetPreviewAllState, job:{...task, result:task.result || null}, pollingJobId:running ? jobId : '', controlling:false});
        if(storageSettingsState.open && !unchanged) renderStorageSettingsModal();
        if(running){ window.setTimeout(() => void pollPreviewAllJob(jobId), 800); return; }
        if(task.status === 'succeeded'){
            const result = task.result || {};
            const cleared = Number(result.cleared || 0);
            const prefix = result.mode === 'rebuild' ? `预览缓存已重建${cleared ? `（清空 ${cleared} 个旧文件）` : ''}` : '全库预览缓存完成';
            const notice = `${prefix}：新增 ${Number(result.generated || 0)} 项，跳过 ${Number(result.skipped || 0)} 项，失败 ${Number(result.failed || 0)} 项，耗时 ${Number(result.elapsed_seconds || 0)} 秒`;
            setAssetPreviewAllState({...assetPreviewAllState, notice, error:''});
            setStatus(notice);
            // 任务结束后缓存覆盖变了，重取统计让按钮在【全部缓存】/【重建缓存】之间切换。
            void refreshThumbnailCacheStats();
        } else if(task.status === 'cancelled'){
            const result = task.result || {};
            const notice = `已取消：完成 ${Number(result.processed || 0)}/${Number(result.total || 0)} 项，已生成 ${Number(result.generated || 0)} 项，失败 ${Number(result.failed || 0)} 项`;
            setAssetPreviewAllState({...assetPreviewAllState, notice, error:''});
            setStatus(notice);
            void refreshThumbnailCacheStats();
        } else {
            const message = task.error || '全库缓存失败';
            setAssetPreviewAllState({...assetPreviewAllState, error:message});
            setStatus(`全库预览缓存失败：${message}`);
        }
        if(storageSettingsState.open) renderStorageSettingsModal();
    } catch(error){
        if([401,403,404].includes(error.status)){
            setAssetPreviewAllState({...assetPreviewAllState, pollingJobId:'', error:error.message || '缓存任务不可读取'});
            if(storageSettingsState.open) renderStorageSettingsModal();
            return;
        }
        window.setTimeout(() => void pollPreviewAllJob(jobId), 1500);
    }
}
async function controlPreviewAllJob(action){
    const jobId = assetPreviewAllState.job?.job_id || assetPreviewAllState.pollingJobId;
    if(!jobId || assetPreviewAllState.controlling) return;
    setAssetPreviewAllState({...assetPreviewAllState, controlling:true, error:''});
    if(storageSettingsState.open) renderStorageSettingsModal();
    try {
        const data = await apiJsonResponse(assetManagerHttp.request(`/api/asset-thumbnails/jobs/${encodeURIComponent(jobId)}/control`, {
            method:'POST',
            credentials:'same-origin',
            headers:{'Content-Type':'application/json'},
            body:JSON.stringify({action}),
        }));
        const task = data.task || {};
        const active = ['queued','running','paused'].includes(task.status);
        setAssetPreviewAllState({
            ...assetPreviewAllState,
            controlling:false,
            job:{...task, result:task.result || null},
            pollingJobId:active ? jobId : '',
            error:'',
        });
        if(action === 'cancel') setStatus('已请求取消，正在收尾当前批次…');
        if(action === 'pause') setStatus('已暂停全库预览缓存');
        if(action === 'resume') setStatus('已继续全库预览缓存');
        if(storageSettingsState.open) renderStorageSettingsModal();
        // 轮询链每 800ms 自我调度一次，且 paused 仍算活动态，这里不重复起链。
        if(!active) void refreshThumbnailCacheStats();
    } catch(error){
        setAssetPreviewAllState({
            ...assetPreviewAllState,
            controlling:false,
            error:error.message || '任务操作失败',
        });
        if(storageSettingsState.open) renderStorageSettingsModal();
    }
}
async function generateBoardVideoStoryboards(){
    const ids = thumbnailGeneratorAssetIds();
    if(!ids.length || thumbnailGeneratorState.generatingStoryboards) return;
    setThumbnailGeneratorState({...thumbnailGeneratorState, generatingStoryboards:true, error:''});
    renderStorageSettingsModal();
    setStatus(`正在生成 ${ids.length} 项视频九宫格…`);
    try {
        const data = await apiJsonResponse(assetManagerApi.generateThumbnailStoryboards({asset_ids:ids}));
        const taskId = data.job_id || data.task?.id || '';
        const job = taskId ? await apiJsonResponse(assetManagerApi.getThumbnailJob(taskId)) : {};
        const result = job.result || data.result || {};
        setThumbnailGeneratorState({...thumbnailGeneratorState, generatingStoryboards:false});
        videoStoryboardImageCache.clear();
        // 分镜缩略图已回写注册表元数据，需要重新拉取素材与能力开关才能看到九宫格。
        await refreshRegistryAssets(false);
        render();
        renderStorageSettingsModal();
        const failed = Number(result.failed || 0);
        setStatus(`九宫格已生成：新增 ${Number(result.generated || 0)} 项，复用 ${Number(result.cached || 0)} 项${failed ? `，失败 ${failed} 项（非视频或缺少本地文件）` : ''}`);
    } catch(err) {
        setThumbnailGeneratorState({...thumbnailGeneratorState, generatingStoryboards:false, error:err.message || '生成九宫格失败'});
        renderStorageSettingsModal();
        setStatus(thumbnailGeneratorState.error);
    }
}
async function deleteBoardVideoStoryboards(){
    const ids = thumbnailGeneratorAssetIds();
    if(!ids.length || thumbnailGeneratorState.deletingStoryboards) return;
    if(!window.confirm(`直接删除 ${ids.length} 项素材的九宫格缩略图？普通缩略图和视频转码缓存会保留。`)) return;
    setThumbnailGeneratorState({...thumbnailGeneratorState, deletingStoryboards:true, error:''});
    renderStorageSettingsModal();
    try {
        const data = await apiJsonResponse(assetManagerApi.deleteThumbnailStoryboards({asset_ids:ids}));
        // 【修复问题1】显式删除九宫格属于真实失效，保留会话级 bump（仅影响旧版 url 参数 URL）
        setThumbnailCacheVersion(Date.now());
        setThumbnailGeneratorState({...thumbnailGeneratorState, deletingStoryboards:false});
        videoStoryboardImageCache.clear();
        // 元数据里的片段缩略图已被清空，重新拉取注册表避免界面显示已删除的路径。
        await refreshRegistryAssets(false);
        render();
        renderStorageSettingsModal();
        setStatus(`已从受管缓存删除 ${Number(data.removed || 0)} 个九宫格缩略图，并清空 ${Number(data.metadata_cleared || 0)} 项素材的片段缩略图字段；可点击“生成九宫格”恢复`);
    } catch(err){
        setThumbnailGeneratorState({...thumbnailGeneratorState, deletingStoryboards:false, error:err.message || '删除九宫格缩略图失败'});
        renderStorageSettingsModal();
        setStatus(thumbnailGeneratorState.error);
    }
}

function registryFullscreenViewerMediaUrl(item={}){
    // 真全屏查看器与 Coverflow 同口径：中央媒体始终使用原片地址。
    return String(item.mediaSrc || item.url || item.public_url || '').trim();
}

function registryFullscreenViewerPosterUrl(item={}){
    const url = registryFullscreenViewerMediaUrl(item);
    return assetPreviewUrl(url, 512, item) || url;
}

function closeRegistryAssetFullscreenViewer({restoreFocus=true}={}){
    const state = registryFullscreenViewerState;
    if(!state) return false;
    setRegistryFullscreenViewerState(null);
    document.removeEventListener('keydown', state.onKeydown, true);
    document.removeEventListener('fullscreenchange', state.onFullscreenChange);
    if(document.fullscreenElement && document.exitFullscreen){
        const exitPromise = document.exitFullscreen();
        if(exitPromise && typeof exitPromise.catch === 'function'){
            exitPromise.catch(err => console.warn('[AssetManager] 退出真全屏失败:', err));
        }
    }
    state.overlay?.remove();
    if(restoreFocus && state.trigger?.focus) state.trigger.focus({preventScroll:true});
    return true;
}

function registryFullscreenViewerShow(state, index){
    const items = state.items;
    if(!items.length) return;
    state.index = (index % items.length + items.length) % items.length;
    const item = items[state.index];
    const stage = state.overlay.querySelector('[data-gw-asset-fsviewer-stage]');
    stage.replaceChildren();
    if(item.mediaKind === 'video'){
        const video = document.createElement('video');
        video.src = registryFullscreenViewerMediaUrl(item);
        const poster = registryFullscreenViewerPosterUrl(item);
        if(poster && poster !== video.src) video.poster = poster;
        video.controls = true;
        video.autoplay = true;
        video.playsInline = true;
        video.setAttribute('aria-label', item.name || '视频素材');
        stage.append(video);
    } else {
        const image = document.createElement('img');
        image.src = registryFullscreenViewerMediaUrl(item);
        image.alt = item.name || '图片素材';
        image.decoding = 'async';
        stage.append(image);
    }
    state.overlay.querySelector('[data-gw-asset-fsviewer-name]').textContent = item.name || '';
    state.overlay.querySelector('[data-gw-asset-fsviewer-count]').textContent = `${state.index + 1} / ${items.length}`;
}

function openRegistryAssetFullscreenViewer(trigger=null, options={}){
    if(registryFullscreenViewerState) closeRegistryAssetFullscreenViewer({restoreFocus:false});
    const board = registryCoverflowBoard();
    const assets = registryCoverflowAssets(board);
    if(!assets.length){
        setStatus('当前素材库没有可展示的图片或视频');
        return;
    }
    const wanted = [options?.currentAssetId, selectedRegistryAssetId, registryCoverflowCurrentAssetId]
        .map(value => String(value || '').trim())
        .find(id => assets.some(item => item.id === id)) || assets[0].id;
    const overlay = document.createElement('section');
    overlay.className = 'gw-asset-fsviewer';
    overlay.dataset.gwAssetFsviewer = '';
    overlay.setAttribute('role', 'dialog');
    overlay.setAttribute('aria-modal', 'true');
    overlay.setAttribute('aria-label', '素材真全屏查看');
    overlay.setAttribute('data-floating-content', '');
    overlay.innerHTML = [
        '<button type="button" class="gw-asset-fsviewer-nav" data-gw-asset-fsviewer-prev aria-label="上一张">',
        '  <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M15 18l-6-6 6-6"/></svg>',
        '</button>',
        '<div class="gw-asset-fsviewer-stage" data-gw-asset-fsviewer-stage></div>',
        '<div class="gw-asset-fsviewer-status">',
        '  <span class="gw-asset-fsviewer-name" data-gw-asset-fsviewer-name></span>',
        '  <span class="gw-asset-fsviewer-count" data-gw-asset-fsviewer-count></span>',
        '</div>',
        '<button type="button" class="gw-asset-fsviewer-nav gw-asset-fsviewer-nav--next" data-gw-asset-fsviewer-next aria-label="下一张">',
        '  <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M9 6l6 6-6 6"/></svg>',
        '</button>',
    ].join('');
    document.body.append(overlay);
    const state = {
        overlay,
        items: assets,
        index: Math.max(0, assets.findIndex(item => item.id === wanted)),
        trigger: trigger instanceof HTMLElement ? trigger : null,
        onKeydown: null,
        onFullscreenChange: null,
    };
    setRegistryFullscreenViewerState(state);
    state.onFullscreenChange = () => {
        if(registryFullscreenViewerState === state && !document.fullscreenElement){
            closeRegistryAssetFullscreenViewer();
        }
    };
    document.addEventListener('fullscreenchange', state.onFullscreenChange);
    state.onKeydown = event => {
        const key = String(event.key || '');
        if(['ArrowLeft','ArrowRight'].includes(key)){
            event.preventDefault();
            event.stopPropagation();
            registryFullscreenViewerShow(state, state.index + (key === 'ArrowRight' ? 1 : -1));
        } else if(key.toLowerCase() === 'f' && !event.ctrlKey && !event.metaKey && !event.altKey){
            event.preventDefault();
            event.stopPropagation();
            closeRegistryAssetFullscreenViewer();
        } else if(key === 'Escape'){
            event.preventDefault();
            event.stopPropagation();
            closeRegistryAssetFullscreenViewer();
        } else if((key === ' ' || event.code === 'Space') && !event.target.closest?.('video,button,a,input,textarea,select')){
            event.preventDefault();
            event.stopPropagation();
        } else if(key === 'ArrowUp' || key === 'ArrowDown'){
            event.preventDefault();
            event.stopPropagation();
        }
    };
    document.addEventListener('keydown', state.onKeydown, true);
    overlay.addEventListener('click', event => {
        if(event.target.closest('[data-gw-asset-fsviewer-prev]')){
            registryFullscreenViewerShow(state, state.index - 1);
        } else if(event.target.closest('[data-gw-asset-fsviewer-next]')){
            registryFullscreenViewerShow(state, state.index + 1);
        }
    });
    markAssetFloatingSurface(overlay, {surface:'asset-fsviewer', kind:'modal', content:'self', close:() => closeRegistryAssetFullscreenViewer()});
    registryFullscreenViewerShow(state, state.index);
    overlay.tabIndex = -1;
    overlay.focus({preventScroll:true});
    const enterPromise = overlay.requestFullscreen?.();
    if(enterPromise && typeof enterPromise.catch === 'function'){
        enterPromise.catch(err => console.warn('[AssetManager] 进入真全屏失败（回落页面内覆盖层）:', err));
    }
}
async function switchDetailImageEditorAsset(detail={}){
    const state = detailImageEditorState;
    const id = String(detail.assetId || detail.currentAssetId || '').trim();
    if(!state || !id || id === String(state.itemId || '')) return;
    if(state.saving){
        setStatus('当前图片正在保存，请稍后再切换');
        return;
    }
    state.saving = true;
    renderDetailImageEditor();
    try {
        await confirmDetailImageEditorTransition('切换到另一张素材');
        await openRegistryCoverflowAssetInEditor({
        mode:'editor',
        source:detail.source || detailViewerState.source,
        assetId:id,
        asset:detail.asset,
            skipEditorSwitch:true,
        });
        setStatus(`已切换到 Coverflow 图片：${detailImageEditorState?.name || id}`);
    } finally {
        if(detailImageEditorState === state){
            state.saving = false;
            renderDetailImageEditor();
        }
    }
}
function refreshRegistryDetailPanelDom(keepScroll=false){
    // 详情面板就地替换：不整树 render()；keepScroll 用于“同一素材详情数据到达”场景保留面板滚动。
    const panel = root.querySelector('.registry-detail-panel');
    if(!panel) return false;
    const detailScroll = panel.querySelector('.detail-scroll');
    const savedScrollTop = keepScroll && detailScroll ? detailScroll.scrollTop : 0;
    const template = document.createElement('template');
    template.innerHTML = renderRegistryDetailPanel().trim();
    const next = template.content.firstElementChild;
    if(!next) return false;
    panel.replaceWith(next);
    if(savedScrollTop > 0) next.querySelector('.detail-scroll')?.scrollTo?.({top:savedScrollTop});
    refreshIcons();
    return true;
}
async function openRegistryAsset(id){
    setSelectedRegistryAssetId(id);
    setRegistryFocusIndex(activeRegistryVisibleAssets().findIndex(item => item.id === id));
    setRegistryDetail(registryAssets.find(item => item.id === id) || null);
    // 【修复浏览刷新】打开详情不再无条件整树 render()：网格已挂载时仅原地同步卡片选择态与详情面板，
    // 详情数据到达后同样原地更新——消除原先同步+数据到达两次连续整树重建导致的
    // “强制刷新1-2下”与视口跳顶。仅当视图未挂载（如跨页签进入）才回退整树渲染。
    if(!syncRegistrySelectionViews()) render();
    scheduleAssetTouchbarReport();
    try {
        const data = await apiJsonResponse(assetManagerApi.getRegistryAsset(id));
        if(selectedRegistryAssetId === id){
            setRegistryDetail(normalizeRegistryAsset(data.asset || {}));
            if(!syncRegistrySelectionViews(true)) render();
            scheduleAssetTouchbarReport();
        }
    } catch(err){ setStatus(err.message || '读取资产详情失败'); }
}
async function removeRegistryAssetRelation(assetId, relatedAssetId){
    if(!assetId || !relatedAssetId) return;
    const data = await apiJsonResponse(assetManagerApi.removeRegistryAssetRelation(assetId, relatedAssetId));
    if(data.asset) mergeRegistryAssetUpdate(data.asset);
    if(detailViewerState.open && detailViewerState.source === 'registry') renderDetailViewer();
    else render();
    setStatus('素材关联已解除');
}
async function openRegistryRelatedAsset(id){
    if(!id) return;
    const data = await apiJsonResponse(assetManagerApi.getRegistryAsset(id));
    const item = normalizeRegistryAsset(data.asset || {});
    if(!item.id) return;
    mergeRegistryAssetUpdate(item);
    setSelectedRegistryAssetId(item.id);
    setRegistryDetail(item);
    if(detailViewerState.open && detailViewerState.source === 'registry'){
        let index = detailViewerState.items.findIndex(value => value.id === item.id);
        if(index < 0){ detailViewerState.items.push(item); index = detailViewerState.items.length - 1; }
        detailViewerState.index = index;
        detailViewerState.id = item.id;
        renderDetailViewer();
        loadDetailViewerText();
        loadDetailTextVersions();
        loadDetailViewerFileInfo();
    } else {
        render();
    }
}
async function openRegistryAssetInBoard(boardId,assetId){
    const board = assetBoardById(boardId);
    if(!board || board.system !== false || !assetId) return;
    boardFolderSelection[boardId] = '';
    boardGroupSelection[boardId] = '';
    boardReferenceCanvasSelection[boardId] = '';
    if(!board.sourceBoard) boardSourceSelection[boardId] = '';
    await activateAssetBoard(boardId);
    await refreshRegistryAssets(false);
    await openRegistryAsset(assetId);
    requestAnimationFrame(() => requestAnimationFrame(() => {
        const card = document.querySelector(`[data-registry-card="${CSS.escape(assetId)}"]`);
        card?.scrollIntoView({block:'center',inline:'center',behavior:'smooth'});
        card?.focus({preventScroll:true});
    }));
}
function renderRegistryDetailPanel(){
    if(registryDetailPanelCollapsed){
        return `<aside class="asset-panel asset-detail registry-detail-panel is-collapsed" data-registry-detail><button class="registry-detail-expand" type="button" data-registry-detail-toggle aria-expanded="false" title="展开资产详情" aria-label="展开资产详情"><i data-lucide="panel-right-open"></i><span>资产详情</span></button></aside>`;
    }
    return `<aside class="asset-panel asset-detail registry-detail-panel" data-registry-detail>${renderRegistryDetail()}</aside>`;
}
function renderRegistryDetailResizer(){
    if(registryDetailPanelCollapsed) return '';
    return `<span class="registry-detail-resizer" data-registry-detail-resizer role="separator" aria-label="拖动调整资产详情宽度" aria-orientation="vertical" aria-valuemin="280" aria-valuemax="720" aria-valuenow="${registryDetailPanelWidth}"></span>`;
}
function toggleRegistryDetailPanel(){
    setRegistryDetailPanelCollapsed(!registryDetailPanelCollapsed);
    try { localStorage.setItem(ASSET_REGISTRY_DETAIL_COLLAPSED_KEY,String(registryDetailPanelCollapsed)); } catch(_) {}
    // 【修复滚动跳顶】折叠/展开只切换根类与详情面板局部节点：网格卡片节点保持不变，
    // 布局变化由 CSS 类（registry-detail-collapsed 列宽切换）与 masonry 重排承接，
    // 不整树 render()，避免锚点恢复失败导致的视口跳顶与缩略图回退。
    root.classList.toggle('registry-detail-collapsed', assetBoardById(activeTab)?.system === false && registryDetailPanelCollapsed);
    syncRegistryDetailGridWidth();
    const panel = root.querySelector('.registry-detail-panel');
    if(!panel){ render(); return; }
    const template = document.createElement('template');
    template.innerHTML = renderRegistryDetailPanel().trim();
    const next = template.content.firstElementChild;
    if(next){
        panel.replaceWith(next);
        refreshIcons();
        scheduleRegistryMasonryLayout();
    } else render();
}
function syncRegistryDetailGridWidth(){
    root?.style.setProperty('--registry-detail-width',`${registryDetailPanelCollapsed ? 44 : registryDetailPanelWidth}px`);
}
function setRegistryDetailPanelWidth(width,persist=false){
    registryDetailPanelWidth = Math.max(280,Math.min(720,Math.round(Number(width || 360))));
    syncRegistryDetailGridWidth();
    document.querySelector('[data-registry-detail-resizer]')?.setAttribute('aria-valuenow',String(registryDetailPanelWidth));
    if(persist){
        try { localStorage.setItem(ASSET_REGISTRY_DETAIL_WIDTH_KEY,String(registryDetailPanelWidth)); } catch(_) {}
    }
}
function beginRegistryDetailPanelResize(event){
    const handle = event.target.closest?.('[data-registry-detail-resizer]');
    if(!handle || event.button !== 0) return;
    event.preventDefault();
    registryDetailPanelResizeState = {pointerId:event.pointerId,startX:event.clientX,startWidth:registryDetailPanelWidth,handle};
    handle.setPointerCapture?.(event.pointerId);
    handle.classList.add('is-resizing');
    root.classList.add('registry-detail-resizing');
    document.body.style.cursor = 'ew-resize';
    document.body.style.userSelect = 'none';
}
function updateRegistryDetailPanelResize(event){
    const state = registryDetailPanelResizeState;
    if(!state || state.pointerId !== event.pointerId) return;
    setRegistryDetailPanelWidth(state.startWidth+state.startX-event.clientX);
}
function endRegistryDetailPanelResize(event){
    const state = registryDetailPanelResizeState;
    if(!state || state.pointerId !== event.pointerId) return;
    state.handle?.classList.remove('is-resizing');
    registryDetailPanelResizeState = null;
    root.classList.remove('registry-detail-resizing');
    document.body.style.removeProperty('cursor');
    document.body.style.removeProperty('user-select');
    setRegistryDetailPanelWidth(registryDetailPanelWidth,true);
}
function renderAssetDetail(item){
    if(!item) return `<div class="panel-head"><div class="panel-title"><strong>素材预览</strong><span>选择一个素材查看详情</span></div></div><div class="detail-scroll"><div class="detail-empty"><i data-lucide="image"></i><span>暂无可预览素材</span></div></div>`;
    const isImage = assetKind(item) === 'image';
    if(assetEditMode && item.id === selectedAssetId){
        return `
            <div class="panel-head">
                <div class="panel-title"><strong>编辑素材</strong><span>当前分组内直接保存</span></div>
                <div class="panel-actions">
                    <button class="hw-btn hw-btn-gold" type="button" data-asset-edit-save="${escapeAttr(item.id)}"><i data-lucide="check"></i><span>保存</span></button>
                    <button class="hw-btn hw-btn-icon" type="button" data-asset-edit-cancel title="取消"><i data-lucide="x"></i></button>
                </div>
            </div>
            <div class="detail-scroll">
                <div class="detail-media"><button class="detail-media-frame detail-media-zoomable" type="button" data-asset-preview="${escapeAttr(item.id)}" title="点击放大预览">${assetThumb(item)}</button></div>
                <div class="inline-edit-form">
                    <label class="inline-edit-field"><span>素材名称</span><input id="assetEditName" type="text" value="${escapeAttr(item.name || '')}" placeholder="素材名称"></label>
                    <div class="detail-meta-grid">
                        <div class="detail-meta"><span>类型</span><strong>${escapeHtml(assetKindLabel(item))}</strong></div>
                        <div class="detail-meta"><span>创建时间</span><strong>${escapeHtml(formatDate(item.created_at))}</strong></div>
                    </div>
                    <div class="detail-url">${escapeHtml(item.url || '')}</div>
                </div>
            </div>
        `;
    }
    return `
        <div class="panel-head">
            <div class="panel-title"><strong>素材预览</strong><span>${escapeHtml(assetKindLabel(item))}</span></div>
            <div class="panel-actions">
                <button class="hw-btn hw-btn-icon" type="button" data-asset-download="${escapeAttr(item.id)}" title="下载素材"><i data-lucide="download"></i></button>
                <button class="hw-btn hw-btn-icon" type="button" data-asset-edit-start="${escapeAttr(item.id)}" title="编辑"><i data-lucide="pencil"></i></button>
                <button class="hw-btn hw-btn-icon hw-btn-danger ${pendingDeleteAssetId === item.id ? 'detail-confirm' : ''}" type="button" data-asset-delete="${escapeAttr(item.id)}" title="${pendingDeleteAssetId === item.id ? '再次点击确认删除' : '删除'}"><i data-lucide="trash-2"></i></button>
            </div>
        </div>
        <div class="detail-scroll">
            <div class="detail-media"><button class="detail-media-frame detail-media-zoomable" type="button" data-asset-preview="${escapeAttr(item.id)}" title="点击放大预览">${assetThumb(item)}</button></div>
            <div class="detail-body">
                <input class="detail-name-input" data-asset-inline-name="${escapeAttr(item.id)}" type="text" value="${escapeAttr(item.name || 'asset')}" title="直接修改名称">
                <div class="detail-meta-grid">
                    <div class="detail-meta"><span>类型</span><strong>${escapeHtml(assetKindLabel(item))}</strong></div>
                    <div class="detail-meta"><span>创建时间</span><strong>${escapeHtml(formatDate(item.created_at))}</strong></div>
                    <div class="detail-meta"><span>资产库</span><strong>${escapeHtml(activeAssetLibrary()?.name || '资产库')}</strong></div>
                    <div class="detail-meta"><span>分组</span><strong>${escapeHtml(activeAssetCategory()?.name || '分组')}</strong></div>
                </div>
                <div class="detail-url">${escapeHtml(item.url || '')}</div>
                ${renderCollectProvenance(item)}
                ${renderProvenanceChain(item)}
                ${isImage ? `<div class="detail-caption-card">
                    <div class="detail-caption-head"><strong>智能分类</strong></div>
                    <div class="detail-classification-body">${renderClassificationChips(item, 32, {kind:'asset', libraryId:activeAssetLibraryId}) || '<span class="classification-empty">暂无智能分类，可以选中图片后点击“智能分类”。</span>'}</div>
                </div>` : ''}
                ${renderAvatarSection(item)}
            </div>
        </div>
    `;
}
function openAssetItem(id){
    const item = findAssetItem(id);
    if(item?.url) window.open(item.url, '_blank', 'noopener');
}
function detailViewerItems(source){
    if(source === 'registry') return activeRegistryVisibleAssets();
    if(source === 'asset') return currentAssetItems();
    if(source === 'workflow') return currentWorkflowItems();
    if(source === 'prompt') return currentPromptItems();
    if(source === 'localup') return localUploadItems();
    if(source === 'local') return localItemsForFolder();
    if(source === 'canvas-asset') return currentCanvasAssetItems();
    return [];
}
function detailViewerTypeLabel(type){
    return {image:'图片',video:'视频',audio:'音频',text:'文本',workflow:'工作流',model:'3D 模型',font:'字体',lut:'LUT',other:'文件'}[type] || '文件';
}
function detailViewerNameHtml(value=''){
    return escapeHtml(value || '未命名素材').replace(/([_.-])/g, '$1<wbr>');
}
function detailViewerSyncSelection(source, item){
    if(!item) return;
    if(source === 'registry'){ setSelectedRegistryAssetId(item.id); setRegistryDetail(item); }
    else if(source === 'asset') setSelectedAssetId(item.id);
    else if(source === 'workflow') setSelectedWorkflowId(item.id);
    else if(source === 'prompt') setSelectedPromptId(item.id);
    else if(source === 'localup') setSelectedLocalUploadId(item.id);
    else if(source === 'local') setSelectedLocalId(item.id);
    else if(source === 'canvas-asset') setSelectedCanvasAssetId(item.id);
}
function detailViewerTextKey(source, item={}){
    return `${source}:${item.id || detailViewerUrl(source, item) || item.name || ''}`;
}
function detailViewerFileItem(){
    const activeId = String(detailViewerState.mediaCanvasActiveId || '');
    const current = detailMediaCanvasItems().find(value => String(value.id || '') === activeId)
        || detailViewerState.items[detailViewerState.index]
        || {};
    if(!current || detailViewerState.source === 'registry') return current;
    const assetId = detailMediaCanvasPersistId(current, detailViewerState.source);
    if(!assetId) return current;
    const registryItem = registryDetail?.id === assetId
        ? registryDetail
        : registryAssets.find(value => String(value.id || '') === String(assetId));
    if(!registryItem) return current;
    return {...registryItem, ...current, id:registryItem.id, asset_id:registryItem.id, assetId:registryItem.id};
}
function detailViewerFileKey(source, item={}){
    return `${source}:${item.id || detailViewerUrl(source, item) || item.name || ''}`;
}
function detailViewerCanRename(source){
    return ['registry','asset','workflow','localup'].includes(source);
}
function formatDetailVideoTime(value){
    const seconds = Math.max(0, Number(value || 0));
    if(!Number.isFinite(seconds)) return '0:00';
    const whole = Math.floor(seconds);
    const hours = Math.floor(whole / 3600);
    const minutes = Math.floor((whole % 3600) / 60);
    const remain = whole % 60;
    return hours ? `${hours}:${String(minutes).padStart(2,'0')}:${String(remain).padStart(2,'0')}` : `${minutes}:${String(remain).padStart(2,'0')}`;
}
function detailViewerMetaRows(source, item, type){
    const extension = assetFileExtension(item).toUpperCase() || detailViewerTypeLabel(type);
    const fallbackPath = item.relative_path || item.relativePath || item.file || detailViewerUrl(source, item);
    const path = item.absolute_path || (item.is_remote ? detailViewerUrl(source, item) : (fallbackPath ? '正在读取完整路径…' : '位置不可用'));
    const rows = [
        ['文件格式', extension],
        ['文件大小', formatFileSize(item.size || 0)],
        ['素材来源', detailViewerSourceLabel(source, item)],
        ['修改时间', registryModifiedLabel(item)],
    ];
    const width = Number(item.width || item.metadata?.width || 0);
    const height = Number(item.height || item.metadata?.height || 0);
    const frameRate = Number(item.frame_rate || item.metadata?.frame_rate || 0);
    if(['image','video'].includes(type) && width && height) rows.splice(2, 0, ['分辨率', `${width} × ${height}`]);
    if(type === 'video' && frameRate) rows.splice(width && height ? 3 : 2, 0, ['帧率', `${Number.isInteger(frameRate) ? frameRate : frameRate.toFixed(2)} fps`]);
    if(type === 'model' && detailViewerState.modelInfo){
        const info = detailViewerState.modelInfo;
        const dimensions = Array.isArray(info.geometry?.dimensions) ? info.geometry.dimensions : [];
        rows.splice(2, 0, ['预览方式', info.preview_source_label || '静态预览']);
        if(info.companion?.name) rows.splice(3, 0, ['伴随文件', info.companion.name]);
        if(dimensions.length === 3 && dimensions.some(Boolean)) rows.splice(info.companion?.name ? 4 : 3, 0, ['模型尺寸', dimensions.map(value => Number(value).toLocaleString('zh-CN')).join(' × ')]);
    }
    if(type === 'lut'){
        // 【QIN-554】索引期已解析的头信息直接展示，预览帧自身的解析结果只用来兜底。
        const lut = item.metadata?.lut || {};
        const grid = Number(lut.lut_3d_size) > 0
            ? `${Number(lut.lut_3d_size)}³`
            : (Number(lut.lut_1d_size) > 0 ? `${Number(lut.lut_1d_size)} 点` : '');
        if(lut.title) rows.splice(2, 0, ['LUT 标题', String(lut.title)]);
        if(grid) rows.splice(3, 0, ['LUT 网格', grid]);
        if(Number(lut.sample_count) > 0) rows.splice(4, 0, ['采样点', String(Number(lut.sample_count))]);
    }
    if(item.canvas_title) rows.push(['所属画布', item.canvas_title]);
    if(path) rows.push(['存储位置', path, 'path']);
    return rows;
}
function renderDetailFontExpandButton(panel, label){
    const expanded = detailViewerState.fontExpanded === panel;
    return `<button class="asset-detail-font-expand" type="button" data-detail-font-expand="${escapeAttr(panel)}" aria-pressed="${expanded}" title="${expanded ? `收回${label}` : `放大${label}`}"><i data-lucide="${expanded ? 'minimize-2' : 'maximize-2'}"></i></button>`;
}
function renderDetailModelPipeline(info){
    const proprietary = Boolean(info?.application?.name);
    const steps = [
        ['embedded','内嵌缩略图','优先读取工程文件'],
        ['rendered','通用格式渲染','GLB / FBX / OBJ 等'],
        ['companion','伴随模型','匹配同名 FBX / GLB'],
        [proprietary ? 'logo' : 'default', proprietary ? '官方 Logo' : '默认封面', proprietary ? '无可用预览时降级' : '解析失败时显示'],
    ];
    const active = info?.preview_source || '';
    const activeIndex = steps.findIndex(([id]) => id === active);
    return `<div class="asset-detail-model-pipeline" aria-label="静态预览生成顺序">${steps.map(([id,label,detail], index) => {
        const state = !active ? 'is-pending' : id === active ? 'is-used' : index < activeIndex ? 'is-checked' : 'is-unused';
        return `<div class="${state}"><span>${index + 1}</span><p><strong>${escapeHtml(label)}</strong><small>${escapeHtml(detail)}</small></p>${id === active ? '<em>当前</em>' : ''}</div>`;
    }).join('')}</div>`;
}
function renderDetailModelMedia(source, item){
    const info = detailViewerState.modelInfo;
    const geometry = info?.geometry || {};
    const url = detailViewerUrl(source, item);
    const previewUrl = assetPreviewUrl(url, 2048, item);
    const status = detailViewerState.modelInfoLoading ? '正在分析预览来源' : detailViewerState.modelInfoError ? '预览信息不可用' : (info?.preview_source_label || '静态预览');
    const detail = detailViewerState.modelInfoLoading ? '正在检查内嵌预览、通用渲染与伴随模型' : (info?.preview_source_detail || '静态图片，不加载实时 3D 查看器');
    return `<div class="asset-detail-model-preview">
        <header><div><span>静态模型预览</span><strong>${escapeHtml(status)}</strong></div><p title="${escapeAttr(detail)}">${escapeHtml(detail)}</p></header>
        <div class="asset-detail-model-canvas">
            <img class="asset-lightbox-image" src="${escapeAttr(previewUrl)}" alt="${escapeAttr(item.name || '3D 模型静态预览')}" data-preview-fallback="${escapeAttr(assetFileExtension(item).toUpperCase() || '3D')}" draggable="false">
            <dl class="asset-detail-model-stats">
                <div><dt>面数</dt><dd>${escapeHtml(formatModelCount(geometry.faces))}</dd></div>
                <div><dt>顶点</dt><dd>${escapeHtml(formatModelCount(geometry.vertices))}</dd></div>
                <div><dt>材质</dt><dd>${escapeHtml(formatModelCount(geometry.materials))}</dd></div>
            </dl>
        </div>
        ${renderDetailModelPipeline(info)}
    </div>`;
}
function detailViewerFontItems(){
    let candidates = detailViewerState.items || [];
    if(detailViewerState.source === 'registry'){
        const board = assetBoardById(detailViewerState.originTab);
        candidates = board?.system === false ? customAssetLibraryBaseItems(board) : registryAssets;
    } else if(detailViewerState.source === 'asset'){
        candidates = (activeAssetLibrary()?.categories || []).flatMap(category => category.items || []);
    } else if(detailViewerState.source === 'localup'){
        candidates = localAssets || [];
    } else if(detailViewerState.source === 'local'){
        candidates = [...localItemMap.values()];
    } else if(detailViewerState.source === 'canvas-asset'){
        candidates = canvasAssetsData.items || [];
    }
    const current = detailViewerState.items[detailViewerState.index] || detailViewerItem(detailViewerState.source, detailViewerState.id);
    const unique = new Map();
    [...candidates, current].filter(Boolean).forEach(value => {
        if(detailViewerType(detailViewerState.source, value) === 'font') unique.set(String(value.id || detailViewerUrl(detailViewerState.source, value)), value);
    });
    return [...unique.values()].sort((left,right) => String(left.name || '').localeCompare(String(right.name || ''), 'zh-Hans-CN', {numeric:true, sensitivity:'base'}));
}
function restoreDetailFontListScroll(){
    const list = document.querySelector('.asset-detail-viewer .asset-detail-font-library-list');
    if(list) list.scrollTop = Math.max(0, Number(detailViewerState.fontListScrollTop || 0));
}
function detailViewerVideoState(videoId=''){
    const id = String(videoId || detailViewerState.mediaCanvasActiveId || '');
    if(!detailViewerState.videoRanges || typeof detailViewerState.videoRanges !== 'object') detailViewerState.videoRanges = {};
    if(!detailViewerState.videoRanges[id]) detailViewerState.videoRanges[id] = {inPoint:null,outPoint:null,rate:1};
    const state = detailViewerState.videoRanges[id];
    state.rate = Math.max(.1,Math.min(4,Math.round(Number(state.rate || 1)*10)/10));
    return state;
}
function detailVideoPoint(value){
    if(value === null || value === undefined || value === '') return null;
    const number = Number(value);
    return Number.isFinite(number) ? Math.max(0,number) : null;
}
function videoStoryboardImageKey(assetId, version){
    return `${assetId || ''}@${version || ''}`;
}
function videoStoryboardImageUrl(outputName){
    return `/api/download-output?path=${encodeURIComponent(String(outputName || ''))}`;
}
function paintVideoStoryboardImage(node, data, style = VIDEO_STORYBOARD_IMAGE_STYLE){
    if(!node) return;
    const image = String(data?.image || '');
    if(!image){
        node.innerHTML = `<span class="asset-video-storyboard-placeholder"><i data-lucide="grid-3x3"></i><small>暂无九宫格</small></span>`;
        refreshIcons();
        return;
    }
    node.innerHTML = `<img src="${escapeAttr(videoStoryboardImageUrl(image))}" alt="视频九宫格" loading="lazy" style="${style}">`;
    refreshIcons();
}
function hydrateVideoStoryboardImages(scope){
    const nodes = scope?.querySelectorAll ? [...scope.querySelectorAll('[data-storyboard-image]')] : [];
    nodes.forEach(async node => {
        const assetId = node.dataset.storyboardImage || '';
        if(!assetId) return;
        const version = node.dataset.storyboardVersion || '';
        const key = videoStoryboardImageKey(assetId, version);
        const cached = videoStoryboardImageCache.get(key);
        if(cached){ paintVideoStoryboardImage(node, cached); return; }
        if(node.dataset.storyboardLoading === '1') return;
        node.dataset.storyboardLoading = '1';
        try{
            const data = await apiJsonResponse(assetManagerApi.getRegistryVideoStoryboard(assetId));
            videoStoryboardImageCache.set(key, data || {});
            paintVideoStoryboardImage(node, data);
        }catch(err){
            node.innerHTML = `<span class="asset-video-storyboard-placeholder is-error" title="${escapeAttr(err?.message || '九宫格读取失败')}"><i data-lucide="image-off"></i></span>`;
            refreshIcons();
        }finally{
            delete node.dataset.storyboardLoading;
        }
    });
}
function detailVideoRemainingRows(rows=[]){
    return rows.filter(([label])=>['文件位置','所属画布'].includes(label));
}
function renderMediaNodeParametricSvg(item, edit, url, previewFallback=''){
    const sourceWidth = Math.max(1, Number(edit.source_width || item.natural_w || item.naturalWidth || item.metadata?.width || 1200));
    const sourceHeight = Math.max(1, Number(edit.source_height || item.natural_h || item.naturalHeight || item.metadata?.height || 800));
    const rawCrop = edit.crop && typeof edit.crop === 'object' ? edit.crop : {};
    const cropX = Math.max(0, Math.min(sourceWidth - 1, Number(rawCrop.x || 0)));
    const cropY = Math.max(0, Math.min(sourceHeight - 1, Number(rawCrop.y || 0)));
    const cropW = Math.max(1, Math.min(sourceWidth - cropX, Number(rawCrop.w || (sourceWidth - cropX))));
    const cropH = Math.max(1, Math.min(sourceHeight - cropY, Number(rawCrop.h || (sourceHeight - cropY))));
    const flipX = Boolean(edit.flip_x || edit.flipX);
    const flipY = Boolean(edit.flip_y || edit.flipY);
    const transform = (flipX || flipY)
        ? `transform="translate(${flipX ? cropW : 0}, ${flipY ? cropH : 0}) scale(${flipX ? -1 : 1}, ${flipY ? -1 : 1})"`
        : '';
    const groups = new Map((edit.groups || []).map(g => [g.id, g]));
    const layerSvgs = (edit.layers || []).map(layer => {
        const group = groups.get(layer.group_id);
        if(layer.hidden || group?.hidden) return '';
        if(layer.type === 'brush'){
            const paths = (layer.paths || []).map(path => {
                if(!path.points?.length) return '';
                const strokeColor = path.color || signalFlowAccentColor();
                const strokeWidth = Math.max(1, Number(path.width || 8));
                if(path.points.length === 1){
                    const pt = path.points[0];
                    return `<circle cx="${Number(pt.x || 0).toFixed(1)}" cy="${Number(pt.y || 0).toFixed(1)}" r="${(strokeWidth / 2).toFixed(1)}" fill="${escapeAttr(strokeColor)}" />`;
                }
                const d = path.points.map((pt, idx) => `${idx ? 'L' : 'M'}${Number(pt.x || 0).toFixed(1)},${Number(pt.y || 0).toFixed(1)}`).join(' ');
                return `<path d="${escapeAttr(d)}" fill="none" stroke="${escapeAttr(strokeColor)}" stroke-width="${strokeWidth}" stroke-linecap="round" stroke-linejoin="round" />`;
            }).join('');
            return `<g class="asset-detail-parametric-brush">${paths}</g>`;
        }
        if(layer.type === 'text'){
            const fontSize = Math.max(8, Number(layer.fontSize || layer.font_size || 32));
            const color = layer.color || '#ffffff';
            const lines = String(layer.text || '').split('\n');
            const texts = lines.map((line, idx) => {
                const y = Number(layer.y || 0) + (idx + 1) * fontSize * 1.05;
                return `<text x="${Number(layer.x || 0).toFixed(1)}" y="${y.toFixed(1)}" fill="${escapeAttr(color)}" font-family="Microsoft YaHei, sans-serif" font-weight="600" style="font-size:${fontSize}px;stroke:rgba(0,0,0,0.55);stroke-width:${Math.max(1, fontSize / 14)}" paint-order="stroke fill">${escapeHtml(line)}</text>`;
            }).join('');
            return `<g class="asset-detail-parametric-text">${texts}</g>`;
        }
        return '';
    }).join('');
    const safeId = escapeAttr(String(item.id || 'node').replace(/[^a-zA-Z0-9_-]/g, '_'));
    const clipId = `crop-clip-${safeId}`;
    return `<svg class="asset-detail-media-node-content asset-detail-parametric-svg" viewBox="0 0 ${cropW} ${cropH}" preserveAspectRatio="xMidYMid meet" data-detail-media-natural="${escapeAttr(item.id || '')}" data-preview-fallback="${escapeAttr(item.name || '图片')}" style="width:100%;height:100%;display:block;overflow:hidden;pointer-events:none;">
        <defs>
            <clipPath id="${clipId}">
                <rect x="0" y="0" width="${cropW}" height="${cropH}" />
            </clipPath>
        </defs>
        <g clip-path="url(#${clipId})">
            <g ${transform}>
                <image href="${escapeAttr(url)}" x="${-cropX}" y="${-cropY}" width="${sourceWidth}" height="${sourceHeight}" preserveAspectRatio="none" />
            </g>
            ${layerSvgs}
        </g>
    </svg>`;
}
function preloadDetailViewerNeighbors(){
    const source = detailViewerState.source;
    [detailViewerState.index-1,detailViewerState.index+1].forEach(index => {
        const item = detailViewerState.items[index];
        if(!item || detailViewerType(source,item) !== 'image') return;
        const assetId = detailMediaCanvasPersistId(item,source);
        const currentUrl = detailViewerUrl(source,item);
        const url = assetId && !/\/image-versions\/[^/?#]+\/media(?:\?|#|$)/i.test(currentUrl)
            ? `/api/asset-registry/assets/${encodeURIComponent(assetId)}/media`
            : String(item.assetUrl || item.asset_url || item.original_url || currentUrl || '');
        const previewUrl = assetPreviewUrl(url, 1024, item) || url;
        if(!previewUrl || detailViewerMediaPreloads.has(previewUrl)) return;
        const image = new Image();
        detailViewerMediaPreloads.set(previewUrl,image);
        image.src = previewUrl;
    });
    while(detailViewerMediaPreloads.size > 6) detailViewerMediaPreloads.delete(detailViewerMediaPreloads.keys().next().value);
}
function renderDetailMediaVideoControls(item={}){
    if(detailViewerType(detailViewerState.source,item) !== 'video') return '';
    const videoId = String(item.id || '');
    const state = detailViewerVideoState(videoId);
    const duration = Math.max(0, Number(item.duration || item.metadata?.duration || 0));
    const inPoint = detailVideoPoint(state.inPoint);
    const outPoint = detailVideoPoint(state.outPoint);
    const rangeReady = inPoint !== null && outPoint !== null && outPoint > inPoint;
    const roots = detailWritableStorageRoots();
    if(!roots.some(root => root.id === detailViewerState.videoExportRootId)){
        const sourceRoot = String(item.storage_root_id || item.assetStorageRootId || item.asset_storage_root_id || '');
        detailViewerState.videoExportRootId = roots.find(root => String(root.id) === sourceRoot)?.id || roots[0]?.id || '';
    }
    const rootOptions = roots.map(root => `<option value="${escapeAttr(root.id)}" ${root.id === detailViewerState.videoExportRootId ? 'selected' : ''}>${escapeHtml(root.name || root.id)}</option>`).join('');
    return `<div class="asset-detail-video-dock player-chassis" data-detail-video-dock data-detail-video-controls data-detail-video-for="${escapeAttr(videoId)}" role="group" aria-label="视频播放与截取控制">
        <div class="chassis-screw chassis-screw-tl" aria-hidden="true"></div>
        <div class="chassis-screw chassis-screw-tr" aria-hidden="true"></div>
        <div class="chassis-screw chassis-screw-bl" aria-hidden="true"></div>
        <div class="chassis-screw chassis-screw-br" aria-hidden="true"></div>
        <div class="asset-detail-video-telemetry" aria-hidden="true">
            <span class="telemetry-deck">GODS' WORKBENCH · AV-DECK</span>
            <span class="telemetry-specs">SMPTE · ${duration ? formatDetailVideoTime(duration) : '00:00'} · 4K DCI</span>
        </div>
        <div class="asset-detail-video-control-panel transport-bar-trough">
            <div class="asset-detail-video-controls asset-detail-video-primary-controls">
                <button class="asset-detail-video-play hw-tactile-btn" type="button" data-detail-video-action="toggle" title="播放（Space）" aria-label="播放（Space）"><div class="hw-tactile-btn-inner hw-play-tactile-inner"><i class="asset-detail-video-icon-play" data-lucide="play"></i><i class="asset-detail-video-icon-pause" data-lucide="pause"></i></div></button>
                <button class="hw-tactile-btn" type="button" data-detail-video-action="previous-frame" title="上一帧（A）" aria-label="上一帧（A）"><div class="hw-tactile-btn-inner"><i data-lucide="step-back"></i></div></button>
                <button class="hw-tactile-btn" type="button" data-detail-video-action="next-frame" title="下一帧（D）" aria-label="下一帧（D）"><div class="hw-tactile-btn-inner"><i data-lucide="step-forward"></i></div></button>
                <output class="asset-detail-video-time timecode-recessed" aria-live="off"><span data-detail-video-current>0:00</span><i>/</i><span data-detail-video-duration>${duration ? formatDetailVideoTime(duration) : '--:--'}</span></output>
                <div class="asset-detail-video-progress-wrap scrubber-track">
                    <div class="scrubber-track-bar">
                        <div class="scrubber-loaded" style="width:100%;"></div>
                        <div class="scrubber-fill" data-detail-video-fill style="width:0%;"></div>
                    </div>
                    <input class="asset-detail-video-progress" type="range" min="0" max="${duration || 0}" step="any" value="0" ${duration ? '' : 'disabled'} data-detail-video-progress aria-label="播放进度" aria-valuetext="0:00 / ${duration ? formatDetailVideoTime(duration) : '时长读取中'}">
                    <i data-detail-video-in-marker ${inPoint === null ? 'hidden' : ''}></i>
                    <i data-detail-video-out-marker ${outPoint === null ? 'hidden' : ''}></i>
                </div>
                <button class="asset-detail-video-mute hw-tactile-btn" type="button" data-detail-video-action="mute" title="静音" aria-label="静音"><div class="hw-tactile-btn-inner"><i class="asset-detail-video-icon-volume" data-lucide="volume-2"></i><i class="asset-detail-video-icon-muted" data-lucide="volume-x"></i></div></button>
                <label class="asset-detail-video-volume" title="音量"><span>音量</span><input type="range" min="0" max="1" step="0.05" value="1" data-detail-video-volume aria-label="音量"></label>
                <div class="asset-detail-video-vu-meter" aria-hidden="true" title="立体声动态电平">
                    <span class="vu-label">VU</span>
                    <div class="vu-channels">
                        <div class="vu-ch"><span class="vu-ch-name">L</span><div class="vu-bar-track"><div class="vu-bar-fill vu-bar-left"></div></div></div>
                        <div class="vu-ch"><span class="vu-ch-name">R</span><div class="vu-bar-track"><div class="vu-bar-fill vu-bar-right"></div></div></div>
                    </div>
                </div>
            </div>
            <div class="asset-detail-video-controls asset-detail-video-edit-controls">
                <button class="is-labeled hw-capsule-btn" type="button" data-detail-video-action="set-in" title="设置入点（I）"><i data-lucide="log-in"></i><span>入点 I</span></button>
                <button class="is-labeled hw-capsule-btn" type="button" data-detail-video-action="set-out" title="设置出点（O）"><i data-lucide="log-out"></i><span>出点 O</span></button>
                <button class="is-labeled hw-capsule-btn" type="button" data-detail-video-action="clear-range" title="清除入点和出点"><i data-lucide="eraser"></i><span>清除</span></button>
                <output class="asset-detail-video-range"><span>入 <b data-detail-video-in>${inPoint === null ? '--:--' : formatDetailVideoTime(inPoint)}</b></span><span>出 <b data-detail-video-out>${outPoint === null ? '--:--' : formatDetailVideoTime(outPoint)}</b></span></output>
                <div class="asset-detail-video-rate" role="group" aria-label="播放速度"><button type="button" data-detail-video-action="rate-down" title="减速 0.1 倍（X）"><i data-lucide="minus"></i></button><output data-detail-video-rate>${state.rate.toFixed(1)}×</output><button type="button" data-detail-video-action="rate-up" title="加速 0.1 倍（C）"><i data-lucide="plus"></i></button><button type="button" data-detail-video-action="rate-reset" title="恢复原始速度（Z）">1×</button></div>
                <label class="asset-detail-video-root"><span>导出到库</span><select data-detail-video-root aria-label="选择导出素材库" ${roots.length ? '' : 'disabled'}>${rootOptions || '<option value="">没有可写素材库</option>'}</select></label>
                <button class="is-labeled hw-capsule-btn" type="button" data-detail-video-action="clip-file" data-detail-video-export ${rangeReady ? '' : 'disabled'} title="导出入点到出点的视频到指定位置"><i data-lucide="scissors"></i><span>截取到文件</span></button>
                <button class="is-labeled hw-capsule-btn" type="button" data-detail-video-action="clip-library" data-detail-video-export ${rangeReady && roots.length ? '' : 'disabled'} title="导出入点到出点的视频到所选素材库"><i data-lucide="library-big"></i><span>截取到库</span></button>
                <button class="is-labeled hw-capsule-btn" type="button" data-detail-video-action="frame-file" data-detail-video-export title="导出当前帧到指定位置（Ctrl+Shift+C）"><i data-lucide="image-down"></i><span>单帧到文件</span></button>
                <button class="is-labeled hw-capsule-btn" type="button" data-detail-video-action="frame-library" data-detail-video-export ${roots.length ? '' : 'disabled'} title="导出当前帧到所选素材库"><i data-lucide="images"></i><span>单帧到库</span></button>
                <button class="is-labeled hw-capsule-btn" type="button" data-detail-video-action="frame-copy" title="复制当前帧（Ctrl+C）"><i data-lucide="copy"></i><span>复制帧</span></button>
            </div>
        </div>
    </div>`;
}
function renderDetailViewerMedia(source, item, type){
    const url = detailViewerUrl(source, item);
    const extension = assetFileExtension(item);
    if(type === 'workflow' || workflowSourceId(item)) return workflowThumb(item);
    if(type === 'video'){
        let videoSrc = assetVideoUrl(url, item);
        const startTime = detailViewerState.startTime;
        const fps = Number(item.frame_rate || item.metadata?.frame_rate || 0);
        if(startTime != null && Number.isFinite(Number(startTime)) && !videoSrc.includes('#t=')){
            const alignedTime = alignTimeToFps(Number(startTime), fps);
            videoSrc = `${videoSrc}#t=${alignedTime}`;
        }
        return `<video class="asset-lightbox-video asset-detail-player" src="${escapeAttr(videoSrc)}" poster="${escapeAttr(assetPreviewUrl(url, 1280, item))}" controls playsinline preload="metadata" data-frame-rate="${fps || ''}"></video>`;
    }
    if(type === 'audio'){
        const extensionLabel = assetFileExtension(item).toUpperCase() || 'AUDIO';
        return `<div class="asset-detail-audio">
            <div class="asset-detail-audio-filebar"><div><i data-lucide="audio-lines"></i><strong>${escapeHtml(item.name || '未命名音频')}</strong></div><span>${escapeHtml(extensionLabel)} · ${escapeHtml(formatFileSize(item.size || 0))} · <em data-detail-audio-duration>${escapeHtml(formatMediaDuration(item.duration || item.metadata?.duration))}</em></span></div>
            <div class="asset-detail-waveform-shell" data-detail-audio-waveform tabindex="0" aria-label="音频波形，上下滚轮缩放，点击定位播放位置">
                <div class="asset-detail-waveform-track"><canvas class="asset-detail-waveform" aria-hidden="true"></canvas><span class="asset-detail-waveform-playhead" aria-hidden="true"></span></div>
                <span class="asset-detail-waveform-status"><i data-lucide="loader-2"></i>正在解析详细波形…</span>
            </div>
            <div class="asset-detail-audio-player"><audio class="asset-detail-player" src="${escapeAttr(url)}" controls preload="metadata"></audio><span><i data-lucide="scan-line"></i>滚轮左右缩放 · 当前播放帧为缩放中心 · <b data-detail-audio-zoom>${Number(detailViewerState.audioZoom || 1).toFixed(1)}×</b></span></div>
        </div>`;
    }
    if(type === 'text'){
        const selectedVersion = detailViewerState.selectedTextVersion;
        const viewingHistory = Boolean(selectedVersion);
        const historyEditing = viewingHistory && detailViewerState.textVersionEditing;
        const content = viewingHistory
            ? (selectedVersion.content || '该版本正文为空')
            : detailViewerState.textLoading
            ? '正在读取正文…'
            : detailViewerState.textError
                ? detailViewerState.textError
                : (detailViewerState.text || '文件正文为空');
        const editable = detailViewerCanEditText(source, item) && !detailViewerState.textTruncated;
        const editing = editable && (detailViewerState.textEditing || historyEditing);
        const toolbarLabel = viewingHistory
            ? `历史版本 v${Number(selectedVersion.version_number || 0)}${historyEditing ? ' · 正在编辑快照' : ' · 只影响此版本快照'}`
            : (detailViewerState.textEditing ? '正在编辑本地文件' : (editable ? '当前正文' : '只读预览'));
        const toolbarActions = viewingHistory
            ? (historyEditing
                ? '<button type="button" data-detail-version-edit-cancel><i data-lucide="x"></i><span>取消</span></button><button class="primary" type="button" data-detail-version-edit-save><i data-lucide="save"></i><span>保存版本</span></button>'
                : '<button type="button" data-detail-version-current><i data-lucide="undo-2"></i><span>返回当前版本</span></button><button type="button" data-detail-version-edit><i data-lucide="pencil"></i><span>编辑此版本</span></button>')
            : (detailViewerState.textEditing
                ? '<button type="button" data-detail-text-cancel><i data-lucide="x"></i><span>取消</span></button><button class="primary" type="button" data-detail-text-save><i data-lucide="save"></i><span>保存正文</span></button>'
                : `<button type="button" data-detail-text-edit ${editable && !detailViewerState.textLoading && !detailViewerState.textError ? '' : 'disabled'}><i data-lucide="pencil"></i><span>编辑正文</span></button>`);
        const copyShell = `<div class="asset-detail-copy-shell ${editing ? 'is-editing' : ''}">
            <div class="asset-detail-copy-toolbar"><span>${escapeHtml(toolbarLabel)}</span><div>${toolbarActions}</div></div>
            ${editing
                ? `<textarea class="asset-detail-copy asset-detail-copy-editor" ${historyEditing ? 'data-detail-version-editor' : 'data-detail-text-editor'} spellcheck="false" aria-label="${historyEditing ? '历史版本编辑器' : '正文编辑器'}">${escapeHtml(historyEditing ? selectedVersion.content : detailViewerState.text)}</textarea>`
                : `<pre class="asset-detail-copy" tabindex="0">${escapeHtml(content)}</pre>`}
            ${!viewingHistory && detailViewerState.textLoading ? '<i data-lucide="loader-2"></i>' : ''}
        </div>`;
        const officeDocument = ['doc','docx','odt','ppt','pptx','odp','xls','xlsx','ods'].includes(extension);
        if(!officeDocument) return copyShell;
        return `<div class="asset-detail-office-workspace"><aside><div><img src="${escapeAttr(assetPreviewUrl(url,1280,item))}" alt="${escapeAttr(item.name || 'Office 文档预览')}"></div><span><i data-lucide="file-check-2"></i>优先使用 LibreOffice 保留首页版式；不可用时显示结构化封面。</span></aside>${copyShell}</div>`;
    }
    if(type === 'font'){
        const renderParagraphs = text => text.split(/\n\s*\n/).map(paragraph => `<p>${escapeHtml(paragraph)}</p>`).join('');
        const expanded = detailViewerState.fontExpanded || '';
        return `<div class="asset-detail-font-workspace ${detailViewerFontListCollapsed ? 'asset-detail-font-library-collapsed' : ''}" data-detail-font-workspace>
            ${renderDetailFontLibrary(item)}
            <div class="asset-detail-font-preview" data-detail-font-preview data-font-expanded="${escapeAttr(expanded)}">
            <span class="asset-detail-font-measure" data-detail-font-measure aria-hidden="true">${escapeHtml(ASSET_FONT_PREVIEW_ZH_LINE)}</span>
            <header class="asset-detail-font-preview-head">
                <div><i data-lucide="type"></i><strong>双语字形预览</strong></div>
                <span class="asset-detail-font-status" data-detail-font-status aria-live="polite"><span data-detail-font-load>正在载入当前字体</span><b data-detail-font-scale>${Math.round(Number(detailViewerState.fontScale || 1) * 100)}%</b><em>滚轮缩放</em></span>
            </header>
            <div class="asset-detail-font-split" data-font-expanded="${escapeAttr(expanded)}">
                <section class="asset-detail-font-pane asset-detail-font-pane-zh" data-detail-font-panel="zh" lang="zh-CN">
                    <header><div><strong>中文预览</strong><span>简体中文</span></div>${renderDetailFontExpandButton('zh', '中文预览')}</header>
                    <div class="asset-detail-font-copy asset-detail-font-copy-zh" tabindex="0">${renderParagraphs(ASSET_FONT_PREVIEW_ZH)}</div>
                </section>
                <div class="asset-detail-font-secondary">
                    <section class="asset-detail-font-pane asset-detail-font-pane-en" data-detail-font-panel="en" lang="en">
                        <header><div><strong>English Preview</strong><span>Latin</span></div>${renderDetailFontExpandButton('en', '英文预览')}</header>
                        <div class="asset-detail-font-copy asset-detail-font-copy-en" tabindex="0">${renderParagraphs(ASSET_FONT_PREVIEW_EN)}</div>
                    </section>
                    ${renderDetailFontSpecimen()}
                </div>
            </div>
            </div>
        </div>`;
    }
    if(type === 'lut') return renderDetailLutWorkspace(source, item);
    if(type === 'model') return renderDetailModelMedia(source, item);
    if(type === 'image'){
        const rawPreview = ASSET_BROWSER_PREVIEW_EXTENSIONS.has(extension) ? url : assetPreviewUrl(url, 2048, item);
        return `<img class="asset-lightbox-image" src="${escapeAttr(rawPreview)}" alt="${escapeAttr(item.name || '素材预览')}" draggable="false">`;
    }
    const thumbnail = source === 'workflow' ? workflowThumb(item) : assetThumb(item);
    return `<div class="asset-detail-thumbnail-fallback" aria-label="${escapeAttr(item.name || '素材缩略图')}"><div class="asset-thumb">${thumbnail}</div></div>`;
}
function renderDetailViewerMetaRows(rows, item){
    return rows.map(([label,value,kind]) => {
        if(kind !== 'path') return `<div><dt>${escapeHtml(label)}</dt><dd title="${escapeAttr(value)}">${escapeHtml(value)}</dd></div>`;
        if(item.is_remote){
            return `<div class="asset-detail-path-row"><dt>${escapeHtml(label)}</dt><dd><a class="asset-detail-path" href="${escapeAttr(detailViewerUrl(detailViewerState.source, item))}" target="_blank" rel="noopener"><span>${escapeHtml(value)}</span><i data-lucide="external-link"></i></a></dd></div>`;
        }
        const canReveal = detailViewerFileParams(detailViewerState.source,item).toString().length > 0;
        return `<div class="asset-detail-path-row"><dt>${escapeHtml(label)}</dt><dd><button class="asset-detail-path" type="button" data-detail-file-reveal ${canReveal ? '' : 'disabled'} title="${canReveal ? '在文件夹中显示此文件' : '位置不可用'}"><span data-detail-path-value>${escapeHtml(value)}</span><i data-lucide="folder-search"></i></button></dd></div>`;
    }).join('');
}
function renderDetailAudioPlaylist(item){
    const audios = detailViewerState.items
        .filter(value => detailViewerType(detailViewerState.source, value) === 'audio')
        .sort((left,right) => String(left.name || '').localeCompare(String(right.name || ''), 'zh-CN', {numeric:true, sensitivity:'base'}));
    return `<section class="asset-detail-audio-library">
        <header><div><strong>当前素材库音频</strong><span>按名称排列</span></div><em>${audios.length}</em></header>
        <div class="asset-detail-audio-list">${audios.map(value => `<button class="${value.id === item.id ? 'is-active' : ''}" type="button" data-detail-audio-item="${escapeAttr(value.id || '')}" ${value.id === item.id ? 'aria-current="true"' : ''}><i data-lucide="${value.id === item.id ? 'volume-2' : 'file-audio'}"></i><span title="${escapeAttr(value.name || '未命名音频')}">${escapeHtml(value.name || '未命名音频')}</span></button>`).join('') || '<div class="detail-empty"><span>当前素材库没有其他音频</span></div>'}</div>
    </section>`;
}
function renderDetailTextVersions(){
    const versions = detailViewerState.textVersions || [];
    const selectedId = detailViewerState.selectedTextVersion?.id || '';
    let content = '';
    if(detailViewerState.textVersionsLoading) content = '<div class="asset-detail-version-empty"><i data-lucide="loader-2"></i><span>正在读取版本…</span></div>';
    else if(detailViewerState.textVersionError) content = `<div class="asset-detail-version-empty is-error"><i data-lucide="circle-alert"></i><span>${escapeHtml(detailViewerState.textVersionError)}</span></div>`;
    else if(!versions.length) content = '<div class="asset-detail-version-empty"><i data-lucide="history"></i><span>保存正文后会自动建立版本记录</span></div>';
    else content = versions.map(version => {
        const actor = version.updated_by_name || version.created_by_name || '系统';
        const changedAt = version.updated_at || version.created_at;
        const selected = version.id === selectedId;
        return `<article class="asset-detail-version-row ${selected ? 'is-active' : ''}" data-detail-version-row="${escapeAttr(version.id)}">
            <button class="asset-detail-version-main" type="button" data-detail-version-view="${escapeAttr(version.id)}" aria-pressed="${selected ? 'true' : 'false'}">
                <span class="asset-detail-version-number">v${Number(version.version_number || 0)}</span>
                <span class="asset-detail-version-copy"><strong>${escapeHtml(textVersionActionLabel(version.change_type))}</strong><small>${escapeHtml(actor)} · ${escapeHtml(formatDate(changedAt))}</small></span>
            </button>
            <div class="asset-detail-version-actions">
                <button type="button" data-detail-version-view="${escapeAttr(version.id)}" title="查看版本"><i data-lucide="eye"></i></button>
                <button type="button" data-detail-version-edit-id="${escapeAttr(version.id)}" title="编辑版本"><i data-lucide="pencil"></i></button>
                <button type="button" data-detail-version-restore="${escapeAttr(version.id)}" title="恢复为当前正文"><i data-lucide="rotate-ccw"></i></button>
                <button class="danger" type="button" data-detail-version-delete="${escapeAttr(version.id)}" title="删除版本"><i data-lucide="trash-2"></i></button>
            </div>
        </article>`;
    }).join('');
    return `<section class="asset-detail-versions">
        <header><div><strong>版本历史</strong><span>查看、编辑、删除或恢复正文</span></div><button type="button" data-detail-version-refresh title="刷新版本"><i data-lucide="refresh-cw"></i></button></header>
        <div class="asset-detail-version-list">${content}</div>
    </section>`;
}
function renderDetailFontSpecimen(){
    const scale = Math.round(Number(detailViewerState.fontScale || 1) * 100);
    return `<section class="asset-detail-font-specimen" data-detail-font-panel="glyph" aria-label="基础字形预览">
        <header><div><strong>基础字形</strong><span>16 字母 · 数字 · 标点</span></div><span class="asset-detail-font-specimen-tools"><em data-detail-font-scale>${scale}%</em>${renderDetailFontExpandButton('glyph', '基础字形')}</span></header>
        <div class="asset-detail-font-specimen-samples">
            <div><span>字母</span><p>${escapeHtml(ASSET_FONT_PREVIEW_LETTERS_UPPER)}</p><small>${escapeHtml(ASSET_FONT_PREVIEW_LETTERS_LOWER)}</small></div>
            <div><span>数字</span><p>${escapeHtml(ASSET_FONT_PREVIEW_NUMBERS)}</p></div>
            <div><span>标点</span><p>${escapeHtml(ASSET_FONT_PREVIEW_PUNCTUATION)}</p><small>${escapeHtml(ASSET_FONT_PREVIEW_PUNCTUATION_ZH)}</small></div>
        </div>
    </section>`;
}
function renderDetailModelInspector(item, tags, rows, url){
    const info = detailViewerState.modelInfo;
    const geometry = info?.geometry || {};
    const previewSource = info?.preview_source || '';
    const fallback = ['logo','default'].includes(previewSource);
    const statusLabel = detailViewerState.modelInfoLoading ? '正在分析' : previewSource === 'logo' ? 'Logo 降级' : previewSource === 'default' ? '默认封面' : '预览可用';
    const statusClass = fallback ? 'is-fallback' : detailViewerState.modelInfoLoading ? 'is-loading' : 'is-ready';
    const registryMeta = detailViewerState.source === 'registry'
        ? `<section class="asset-detail-registry-meta">${workflowProvenanceLinks(item)}${renderRegistryEditableTags(item, 'viewer')}${renderRegistryRelations(item)}</section>`
        : (tags.length ? `<section class="asset-detail-tags"><strong>智能分类与标签</strong><div>${tags.map(tag => `<span>${escapeHtml(tag)}</span>`).join('')}</div></section>` : '');
    return `<aside class="asset-detail-inspector asset-detail-model-inspector ${detailViewerInspectorCollapsed ? 'is-collapsed' : ''}" data-detail-inspector>
        ${renderDetailInspectorHeader()}
        <div class="asset-detail-inspector-content">
            <div class="asset-detail-title asset-detail-model-title">
                <div><span>3D 文件</span><em class="${statusClass}">${escapeHtml(statusLabel)}</em></div>
                <h2>${detailViewerNameHtml(item.name)}</h2>
                <p>${escapeHtml(detailViewerSourceLabel(detailViewerState.source, item))}</p>
            </div>
            <dl class="asset-detail-model-summary">
                <div><dt>格式</dt><dd>${escapeHtml(info?.format || assetFileExtension(item).toUpperCase() || '3D')}</dd></div>
                <div><dt>面数</dt><dd>${escapeHtml(formatModelCount(geometry.faces))}</dd></div>
                <div><dt>材质</dt><dd>${escapeHtml(formatModelCount(geometry.materials))}</dd></div>
                <div><dt>大小</dt><dd>${escapeHtml(formatFileSize(item.size || 0))}</dd></div>
            </dl>
            ${info?.preview_source_detail ? `<section class="asset-detail-model-source"><strong>${escapeHtml(info.preview_source_label || '静态预览')}</strong><p>${escapeHtml(info.preview_source_detail)}</p></section>` : ''}
            <dl class="asset-detail-facts">${renderDetailViewerMetaRows(rows, item)}</dl>
            ${registryMeta}
            <div class="asset-detail-actions">
                ${url ? `<a class="hw-btn hw-btn-gold" href="${escapeAttr(url)}" ${item.is_remote ? 'target="_blank" rel="noopener"' : 'download'}><i data-lucide="download"></i><span>${item.is_remote ? '打开原文件' : '下载原文件'}</span></a>` : ''}
                <button class="hw-btn" type="button" data-detail-viewer-close><i data-lucide="x"></i><span>关闭详情</span></button>
            </div>
            <div class="asset-detail-shortcuts"><span><kbd>←</kbd><kbd>→</kbd> 切换</span><span><kbd>滚轮</kbd> 缩放图片</span><span><kbd>~</kbd><kbd>Esc</kbd> 关闭</span></div>
        </div>
    </aside>`;
}
function renderDetailViewerInspectorContent(item, type, tags, rows, url, extraContent='', source=detailViewerState.source){
    const editableName = type === 'audio' && detailViewerCanRename(source);
    const inspectorRows = type === 'video' ? detailVideoRemainingRows(rows) : rows;
    return `
        <div class="asset-detail-title"><span>${escapeHtml(detailViewerTypeLabel(type))}</span>${editableName
            ? `<label class="asset-detail-name-editor"><span>文件名称</span><div><input type="text" value="${escapeAttr(item.name || '')}" data-detail-name-input aria-label="文件名称"><button type="button" data-detail-name-save title="保存文件名称"><i data-lucide="check"></i></button></div></label>`
            : `<h2>${detailViewerNameHtml(item.name)}</h2>`}<p>${escapeHtml(detailViewerSourceLabel(source, item))}</p></div>
        ${type === 'video' ? `${renderDetailVideoStoryboardPanel(item,source)}${renderDetailVideoSegments(item,source)}${renderDetailVideoSummary(item,source)}` : ''}
        ${inspectorRows.length ? `<dl class="asset-detail-facts">${renderDetailViewerMetaRows(inspectorRows, item)}</dl>` : ''}
        ${source === 'registry' ? `<section class="asset-detail-registry-meta">${workflowProvenanceLinks(item)}${renderRegistryEditableTags(item, 'viewer')}${renderRegistryRelations(item)}</section>` : (tags.length ? `<section class="asset-detail-tags"><strong>智能分类与标签</strong><div>${tags.map(tag => `<span>${escapeHtml(tag)}</span>`).join('')}</div></section>` : '')}
        ${type === 'text' && detailViewerCanEditText(source, item) ? renderDetailTextVersions() : ''}
        ${extraContent}
        <div class="asset-detail-actions">
            ${url ? `<a class="hw-btn hw-btn-gold" href="${escapeAttr(url)}" ${item.is_remote ? 'target="_blank" rel="noopener"' : 'download'}><i data-lucide="download"></i><span>${item.is_remote ? '打开原文件' : '下载原文件'}</span></a>` : ''}
            ${source === 'registry' && ['image','text'].includes(type) ? `<button class="hw-btn" type="button" data-detail-export-pdf="${escapeAttr(item.id || '')}" ${registryPdfBusy ? 'disabled' : ''}><i data-lucide="file-down"></i><span>导出 PDF</span></button>` : ''}
            <button class="hw-btn" type="button" data-detail-viewer-close><i data-lucide="x"></i><span>关闭详情</span></button>
        </div>
        <div class="asset-detail-shortcuts"><span><kbd>←</kbd><kbd>→</kbd> 切换</span>${type === 'video' ? '<span><kbd>A</kbd><kbd>D</kbd> 上一帧 / 下一帧</span><span><kbd>F</kbd> 切换全屏</span><span><kbd>Enter</kbd> 全屏播放</span>' : ''}${['audio','video'].includes(type) ? '<span><kbd>Space</kbd> 播放 / 暂停</span>' : ''}<span><kbd>·</kbd><kbd>Esc</kbd> 关闭 / 返回</span></div>`;
}
function renderDetailInspectorHeader(){
    return `<header class="asset-detail-inspector-head">
        <button class="asset-detail-inspector-toggle" type="button" data-detail-inspector-toggle aria-expanded="${!detailViewerInspectorCollapsed}" title="${detailViewerInspectorCollapsed ? '展开资产详情' : '收起资产详情'}"><i data-lucide="${detailViewerInspectorCollapsed ? 'panel-right-open' : 'panel-right-close'}"></i></button>
        <strong>资产详情</strong>
        <i data-lucide="info" aria-hidden="true"></i>
    </header>`;
}
function renderDetailViewerInspector(item, type, tags, rows, url){
    if(type === 'model') return renderDetailModelInspector(item, tags, rows, url);
    const content = renderDetailViewerInspectorContent(item, type, tags, rows, url);
    return `<aside class="asset-detail-inspector ${detailViewerInspectorCollapsed ? 'is-collapsed' : ''}" data-detail-inspector>
        ${renderDetailInspectorHeader()}
        <div class="asset-detail-inspector-content">${content}</div>
    </aside>`;
}
function captureDetailViewerMediaNodes(overlay){
    const snapshot = new Map();
    overlay?.querySelectorAll?.('.asset-detail-media-node[data-detail-media-canvas-node] .asset-detail-media-node-content').forEach(media => {
        const node = media.closest('[data-detail-media-canvas-node]');
        const id = String(node?.dataset?.detailMediaCanvasNode || '');
        if(!id) return;
        snapshot.set(id, {
            media,
            tag: String(media.tagName || '').toLowerCase(),
            src: String(media.getAttribute('src') || ''),
            currentTime: Number(media.currentTime || 0),
            paused: media.paused !== false,
            muted: Boolean(media.muted),
            volume: Number(media.volume ?? 1),
            playbackRate: Number(media.playbackRate || 1),
        });
    });
    return snapshot;
}
function restoreDetailViewerMediaNodes(overlay,snapshot){
    if(!overlay || !snapshot?.size) return;
    const pending = [];
    overlay.querySelectorAll?.('.asset-detail-media-node[data-detail-media-canvas-node]').forEach(node => {
        const previous = snapshot.get(String(node.dataset.detailMediaCanvasNode || ''));
        const current = node.querySelector('.asset-detail-media-node-content');
        if(!previous || !current || previous.tag !== String(current.tagName || '').toLowerCase() || previous.tag === 'svg' || previous.src !== String(current.getAttribute('src') || '')) return;
        current.replaceWith(previous.media);
        pending.push(previous);
    });
    const restore = previous => {
        const media = previous.media;
        if(!media?.isConnected) return;
        if(media.tagName === 'VIDEO'){
            try { media.muted = previous.muted; media.volume = previous.volume; media.playbackRate = previous.playbackRate; } catch(_) {}
            if(Number.isFinite(previous.currentTime) && previous.currentTime > 0){
                try { media.currentTime = previous.currentTime; } catch(_) {}
            }
            if(!previous.paused) media.play?.().catch?.(() => {});
            syncDetailVideoControls(media);
        }
    };
    pending.forEach(previous => {
        restore(previous);
        previous.media.addEventListener?.('loadedmetadata', () => restore(previous), {once:true});
    });
}
function detailImageVersionUrl(assetId,version){
    return version?.is_original || version?.id==='original'
        ? `/api/asset-registry/assets/${encodeURIComponent(assetId)}/media`
        : `/api/asset-registry/assets/${encodeURIComponent(assetId)}/image-versions/${encodeURIComponent(version?.id || '')}/media`;
}
function selectDetailImageVersion(versionId,{render=true,announce=true}={}){
    const item=detailActiveImageItem();
    const assetId=detailImageVersionAssetId(item);
    const version=(detailViewerState.imageVersions || []).find(value=>String(value.id)===String(versionId));
    if(!item || !assetId || !version) return null;
    const edit=version.edit && typeof version.edit==='object' ? {...version.edit,asset_version_id:version.id,asset_version_number:Number(version.version_number||0)} : null;
    const url=detailImageVersionUrl(assetId,version);
    const update=value=>value.id===item.id ? {
        ...value,url,reference_edit:edit,image_version_selection:String(version.id),asset_version_id:version.is_original?'':String(version.id),
        asset_version_number:Number(version.version_number||0),natural_w:Number(edit?.crop?.w || value.natural_w || value.width || 0),natural_h:Number(edit?.crop?.h || value.natural_h || value.height || 0),
    } : value;
    detailViewerState.mediaCanvasItems=detailViewerState.mediaCanvasItems.map(update);
    detailViewerState.items=detailViewerState.items.map(update);
    detailViewerState.imageVersionSelectedId=String(version.id);
    if(render) renderDetailViewer();
    if(announce) setStatus(`已切换到 ${version.is_original ? '原始素材' : `图片版本 V${Number(version.version_number||0)}`}`);
    return version;
}
async function editDetailImageVersion(versionId){
    const item=detailActiveImageItem();
    if(!item || !selectDetailImageVersion(versionId)) return;
    await openDetailImageEditor(item.id);
}
async function refreshRegistryAssetAfterImageVersion(assetId){
    const data=await apiJsonResponse(assetManagerApi.getRegistryAsset(assetId));
    const updated=normalizeRegistryAsset(data.asset || {});
    if(!updated.id) return;
    setRegistryAssets(registryAssets.map(item=>item.id===updated.id ? {...item,...updated} : item));
    if(selectedRegistryAssetId===updated.id) setRegistryDetail(updated);
}
async function deleteDetailImageVersion(versionId){
    const assetId=detailViewerState.imageVersionsAssetId || detailImageVersionAssetId();
    const version=(detailViewerState.imageVersions || []).find(value=>String(value.id)===String(versionId));
    if(!assetId || !version || version.is_original) return;
    if(!window.confirm(`删除图片版本 V${Number(version.version_number||0)}？引用该版本的参考画布将回退到原始素材。`)) return;
    await apiJsonResponse(assetManagerApi.deleteRegistryImageVersion(assetId, version.id));
    await loadDetailImageVersions(detailActiveImageItem(),true);
    await refreshRegistryAssetAfterImageVersion(assetId);
    if(detailViewerState.open) renderDetailViewer(); else render();
    setStatus(`已删除图片版本 V${Number(version.version_number||0)}`);
}
async function setDetailImageVersionHidden(versionId,hidden){
    const assetId=detailViewerState.imageVersionsAssetId || detailImageVersionAssetId();
    const version=(detailViewerState.imageVersions || []).find(value=>String(value.id)===String(versionId));
    if(!assetId || !version || version.is_original) return;
    await apiJsonResponse(assetManagerApi.updateRegistryImageVersion(assetId, version.id, {hidden:Boolean(hidden)}));
    if(hidden && String(detailViewerState.imageVersionSelectedId)===String(version.id)) selectDetailImageVersion('original',{render:false,announce:false});
    await loadDetailImageVersions(detailActiveImageItem(),true);
    await refreshRegistryAssetAfterImageVersion(assetId);
    renderDetailViewer();
    setStatus(`图片版本 V${Number(version.version_number||0)} 已${hidden?'隐藏':'显示'}`);
}
function detailRegistryVersionStructure(item=detailActiveImageItem()){
    const assetId = detailImageVersionAssetId(item);
    if(!assetId || detailViewerState.source !== 'registry') return null;
    return [...registryAssetStructures.values()].find(structure => (
        structure?.structure_type === 'version'
        && registryStructureIsRenderable(structure)
        && structure.members.some(member => member.asset_id === assetId)
    )) || null;
}
function renderDetailStructureVersionHistory(item=detailActiveImageItem()){
    const structure = detailRegistryVersionStructure(item);
    if(!structure) return '';
    const members = registryGroupStructureMembers(structure);
    if(members.length < 2) return '';
    return `<section class="asset-detail-structure-versions" aria-label="合并版本">
        <header><span><i data-lucide="git-branch"></i></span><div><strong>合并版本</strong><small>${members.length} 个候选版本 · 点击切换最终版本</small></div><button type="button" data-detail-structure-coverflow="${escapeAttr(structure.id)}" title="打开当前版本 Coverflow" aria-label="打开当前版本 Coverflow"><i data-lucide="images"></i></button></header>
        <div>${members.map(({member,asset}) => {
            const current = member.asset_id === structure.current_asset_id;
            const raw = String(asset.latest_image_version_url || asset.public_url || asset.url || `/api/asset-registry/assets/${encodeURIComponent(asset.id)}/media`);
            const thumbnail = assetPreviewUrl(raw, 220, asset) || raw;
            const versionNumber = Math.max(1, Number(member.sort_order || 0) + 1);
            return `<article class="asset-detail-structure-version ${current ? 'is-current' : ''}"><button type="button" data-detail-structure-version-select="${escapeAttr(asset.id)}" data-detail-structure-id="${escapeAttr(structure.id)}" aria-current="${current}" ${current ? 'disabled' : ''}><img src="${escapeAttr(thumbnail)}" alt="合并版本 V${versionNumber} 预览" loading="lazy"><span><strong>${current ? '当前最终版本' : `候选 V${versionNumber}`}</strong><small title="${escapeAttr(asset.name || '')}">${escapeHtml(asset.name || '未命名图片')}</small></span></button></article>`;
        }).join('')}</div>
    </section>`;
}
async function selectDetailStructureVersion(structureId, assetId){
    const structure = registryAssetStructures.get(String(structureId || '').trim());
    const id = String(assetId || '').trim();
    if(!structure || structure.structure_type !== 'version' || !structure.members.some(member => member.asset_id === id)) return;
    if(structure.current_asset_id !== id) await setRegistryVersionStructureCurrent(structure.id, id);
    const updated = registryAssetStructures.get(structure.id) || structure;
    const asset = registryStructureMemberAsset(updated, id);
    if(!asset) throw new Error('版本图片不可用');
    const inReferenceCanvas = detailViewerState.open
        && detailViewerState.referenceCanvasMode
        && detailViewerState.source === 'registry'
        && detailViewerState.referenceCanvasStructureId === updated.id;
    if(inReferenceCanvas){
        const canvasItem = detailMediaCanvasItems().find(item => (
            detailMediaCanvasPersistId(item, detailViewerState.source) === id
        ));
        if(canvasItem) activateDetailMediaCanvasItem(canvasItem.id);
        else renderDetailViewer();
        setStatus(`当前最终版本已切换为 ${asset.name || '所选图片'}`);
        return;
    }
    showDetailPreview('registry', asset.id);
}
function renderDetailImageVersionsPanel(){
    const item=detailActiveImageItem(),assetId=detailImageVersionAssetId(item);
    if(!assetId) return '';
    const collapsed=Boolean(detailViewerState.imageVersionPanelCollapsed);
    const allVersions=detailViewerState.imageVersionsAssetId===assetId ? detailViewerState.imageVersions : [];
    const versions=allVersions.filter(version=>!version.is_hidden || String(version.id)===String(detailViewerState.imageVersionSelectedId));
    const structureVersions=renderDetailStructureVersionHistory(item);
    const structure=detailRegistryVersionStructure(item);
    const structureCount=structure ? registryGroupStructureMembers(structure).length : 0;
    const visibleCount=versions.length+structureCount;
    if(collapsed) return `<aside class="asset-detail-image-versions is-collapsed"><button type="button" data-detail-image-versions-toggle aria-label="展开图片版本" title="展开图片版本"><i data-lucide="history"></i><span>版本</span><b>${visibleCount || ''}</b></button></aside>`;
    const content=detailViewerState.imageVersionsLoading
        ? '<div class="asset-detail-image-version-loading"><span></span><span></span><span></span></div>'
        : versions.map(version=>{
            const selected=String(detailViewerState.imageVersionSelectedId)===String(version.id);
            const url=detailImageVersionUrl(assetId,version);
            const thumbnail=version.is_original
                ? `/api/media-preview?w=220&asset_id=${encodeURIComponent(assetId)}&v=${encodeURIComponent(Number(item.updated_at || item.indexed_at || 0))}`
                : `${url}${url.includes('?')?'&':'?'}w=220`;
            const thumbMedia = (version.edit && typeof version.edit === 'object')
                ? renderMediaNodeParametricSvg({ ...item, id: `ver-thumb-${version.id}` }, version.edit, url, thumbnail)
                : `<img src="${escapeAttr(thumbnail)}" alt="${version.is_original?'原始素材':`版本 V${Number(version.version_number||0)}`}预览" loading="lazy">`;
            return `<article class="${selected?'is-selected':''} ${version.is_hidden?'is-hidden':''}"><button class="asset-detail-image-version-main" type="button" data-detail-image-version-select="${escapeAttr(version.id)}" aria-current="${selected}">${thumbMedia}<span><strong>${version.is_original?'原始素材':`版本 V${Number(version.version_number||0)}`}${version.is_hidden?' · 已隐藏':''}</strong><small>${escapeHtml(formatDate(version.created_at))}</small></span></button><div><button class="hw-btn" type="button" data-detail-image-version-edit="${escapeAttr(version.id)}" title="编辑此版本" aria-label="编辑版本 V${Number(version.version_number||0)}"><i data-lucide="pencil"></i></button>${version.is_original?'':`<button class="hw-btn hw-btn-danger" type="button" data-detail-image-version-delete="${escapeAttr(version.id)}" title="删除此版本" aria-label="删除版本 V${Number(version.version_number||0)}"><i data-lucide="trash-2"></i></button>`}</div></article>`;
        }).join('') || '<p>当前仅有原始素材</p>';
    return `<aside class="asset-detail-image-versions"><header><button type="button" data-detail-image-versions-toggle aria-label="折叠图片版本" title="折叠图片版本"><i data-lucide="panel-left-close"></i></button><div><strong>图片版本</strong><span>${structureCount ? `${structureCount} 个合并版本 · ` : ''}${versions.length} 个当前素材版本</span></div><button type="button" data-detail-image-versions-refresh title="刷新版本" aria-label="刷新图片版本"><i data-lucide="refresh-cw"></i></button></header><div class="asset-detail-image-version-list">${structureVersions}${content}</div></aside>`;
}
function refreshDetailImageVersionsPanelDom(){
    const body = document.querySelector('.asset-detail-viewer .asset-detail-viewer-body');
    if(!body || !detailMediaCanvasItems().length) return;
    const showPanel = Boolean(detailImageVersionAssetId());
    const current = body.querySelector(':scope > .asset-detail-image-versions');
    body.classList.toggle('asset-detail-has-image-versions',showPanel);
    body.classList.toggle('asset-detail-image-versions-collapsed',showPanel && detailViewerState.imageVersionPanelCollapsed);
    if(!showPanel) current?.remove();
    else {
        const template = document.createElement('template');
        template.innerHTML = renderDetailImageVersionsPanel().trim();
        const panel = template.content.firstElementChild;
        if(current) current.replaceWith(panel);
        else body.insertBefore(panel,body.querySelector(':scope > .asset-detail-stage'));
    }
    refreshDetailMediaCanvasSidebarDetail();
    refreshIcons();
}
function renderDetailViewer(){
    if(!detailViewerState.open){ document.querySelector('.asset-detail-viewer')?.remove(); return; }
    const item = detailViewerState.items[detailViewerState.index] || detailViewerItem(detailViewerState.source, detailViewerState.id);
    if(!item){ closeDetailPreview(); return; }
    const type = detailViewerType(detailViewerState.source, item);
    const tags = detailViewerTags(item);
    const rows = detailViewerMetaRows(detailViewerState.source, item, type);
    const mediaCanvasMode = ['image','video'].includes(type) && (detailMediaCanvasItems().length > 0 || detailViewerState.mediaCanvasLibraryVisible || Boolean(detailViewerState.referenceCanvasId));
    const canZoom = !mediaCanvasMode && ['image','model'].includes(type);
    const mediaCanvasIndex = mediaCanvasMode ? Math.max(0, detailMediaCanvasItems().findIndex(value => value.id === item.id)) : -1;
    const detailPositionLabel = mediaCanvasMode ? (detailMediaCanvasItems().length ? `${mediaCanvasIndex + 1} / ${detailMediaCanvasItems().length}` : '空画布') : `${detailViewerState.index + 1} / ${detailViewerState.items.length}`;
    const url = detailViewerUrl(detailViewerState.source, item);
    const officeEditable = detailViewerState.source === 'registry' && ['doc','docx','odt','ppt','pptx','odp','xls','xlsx','ods'].includes(assetFileExtension(item));
    let overlay = document.querySelector('.asset-detail-viewer');
    if(!overlay){
        overlay = document.createElement('div');
        overlay.className = 'asset-lightbox asset-detail-viewer';
        document.documentElement.appendChild(overlay);
    }
    const previousLibraryScrollTop = overlay.querySelector?.('[data-detail-media-library-grid]')?.scrollTop;
    if(Number.isFinite(previousLibraryScrollTop)) detailViewerState.mediaCanvasLibraryScrollTop = previousLibraryScrollTop;
    const previousMediaNodes = captureDetailViewerMediaNodes(overlay);
    // 【修复浏览回顶】同一素材的查看器重渲染（详情/标签数据到达、busy 状态切换等）保留查看器内滚动位置；
    // 素材身份变化（用户显式翻页/打开新素材）才允许回顶。媒体库网格与字体列表滚动沿用既有契约，
    // 这里补齐 inspector 内容、正文/舞台与音频列表三个滚动容器。
    const sameDetailItemRender = overlay?.dataset?.detailItemId === String(item.id || '');
    const previousDetailScrollTops = sameDetailItemRender
        ? [...overlay.querySelectorAll('[data-detail-inspector] .asset-detail-inspector-content,.asset-detail-stage-content,.asset-detail-audio-list')].map(node => node.scrollTop)
        : [];
    overlay.className = `asset-lightbox asset-detail-viewer asset-detail-type-${type}`;
    overlay.dataset.scale = '1';
    overlay.dataset.x = '0';
    overlay.dataset.y = '0';
    const showMediaLibrary = mediaCanvasMode;
    const showImageVersions = mediaCanvasMode && Boolean(detailImageVersionAssetId());
    const imageVersionsPanel = showImageVersions ? renderDetailImageVersionsPanel() : '';
    const inspector = showMediaLibrary ? renderDetailMediaCanvasLibrary() : renderDetailViewerInspector(item, type, tags, rows, url);
    const originLabel = assetBoardById(detailViewerState.originTab)?.name || detailViewerTypeLabel(type);
    overlay.innerHTML = `<div class="asset-detail-viewer-page ${mediaCanvasMode ? 'is-media-canvas-mode' : ''}" data-floating-content role="dialog" aria-modal="true" aria-label="${escapeAttr(item.name || '素材详情')}">
        ${mediaCanvasMode ? '' : `<header class="asset-detail-viewer-head">
            <nav class="asset-detail-breadcrumb" aria-label="详情路径"><button type="button" data-detail-breadcrumb="home">资产中心</button><i data-lucide="chevron-right"></i><button type="button" data-detail-breadcrumb="origin">${escapeHtml(originLabel)}</button><i data-lucide="chevron-right"></i><strong>${escapeHtml(item.name || '未命名素材')}</strong></nav>
            <div class="asset-detail-head-actions">
                <span data-detail-position-label>${detailPositionLabel}</span>
                ${officeEditable ? `<button type="button" data-detail-office-pdf="${escapeAttr(item.id)}" title="通过 LibreOffice 导出 PDF"><i data-lucide="file-down"></i></button>` : ''}
                ${officeEditable ? `<button type="button" data-detail-office-open="${escapeAttr(item.id)}" title="使用本机 Office 或 LibreOffice 打开编辑"><i data-lucide="file-pen-line"></i></button>` : ''}
                ${url ? `<a href="${escapeAttr(url)}" target="_blank" rel="noopener" title="打开原文件"><i data-lucide="external-link"></i></a>` : ''}
                <button type="button" data-detail-viewer-close aria-label="关闭详情"><i data-lucide="x"></i></button>
            </div>
        </header>`}
        <div class="asset-detail-viewer-body ${type === 'audio' ? 'asset-detail-audio-layout' : ''} ${mediaCanvasMode ? 'asset-detail-media-canvas-layout' : ''} ${showMediaLibrary ? 'asset-detail-media-library-layout' : ''} ${showMediaLibrary && detailMediaCanvasLibraryCollapsed ? 'asset-detail-media-library-collapsed' : ''} ${showImageVersions ? 'asset-detail-has-image-versions' : ''} ${showImageVersions && detailViewerState.imageVersionPanelCollapsed ? 'asset-detail-image-versions-collapsed' : ''} ${detailViewerInspectorCollapsed && !showMediaLibrary ? 'asset-detail-inspector-collapsed' : ''}" ${showMediaLibrary ? `style="--media-library-width:${detailMediaCanvasLibraryWidth}px"` : ''}>
            ${imageVersionsPanel}
            <section class="asset-detail-stage ${type === 'audio' ? 'asset-detail-audio-stage' : ''} ${mediaCanvasMode ? 'asset-detail-media-canvas-stage' : ''}">
                ${mediaCanvasMode ? renderDetailMediaCanvas(detailViewerState.source, item) : `
                    <button class="asset-detail-page-nav previous" type="button" data-detail-navigate="-1" ${detailViewerState.index <= 0 ? 'disabled' : ''} aria-label="上一个素材"><i data-lucide="chevron-left"></i></button>
                    <div class="asset-detail-stage-content">${renderDetailViewerMedia(detailViewerState.source, item, type)}</div>
                    <button class="asset-detail-page-nav next" type="button" data-detail-navigate="1" ${detailViewerState.index >= detailViewerState.items.length - 1 ? 'disabled' : ''} aria-label="下一个素材"><i data-lucide="chevron-right"></i></button>
                    ${canZoom ? `<div class="asset-detail-zoom-controls"><button type="button" data-detail-zoom="-1" aria-label="缩小"><i data-lucide="minus"></i></button><button type="button" data-detail-zoom="0">适应</button><button type="button" data-detail-zoom="1" aria-label="放大"><i data-lucide="plus"></i></button></div>` : ''}
                `}
            </section>
            ${type === 'audio' ? renderDetailAudioPlaylist(item) : ''}
            ${inspector}
        </div>
    </div>`;
    markAssetFloatingSurface(overlay, {surface:'asset-detail-viewer', content:'.asset-detail-viewer-page', close:closeDetailPreview});
    restoreDetailViewerMediaNodes(overlay,previousMediaNodes);
    overlay.dataset.detailItemId = String(item.id || '');
    if(sameDetailItemRender && previousDetailScrollTops.length){
        // 同一素材重渲染：按捕获顺序恢复 inspector/正文/音频列表滚动，浏览中不自动回顶。
        const detailScrollables = overlay.querySelectorAll('[data-detail-inspector] .asset-detail-inspector-content,.asset-detail-stage-content,.asset-detail-audio-list');
        detailScrollables.forEach((node, index) => {
            const savedTop = previousDetailScrollTops[index];
            if(savedTop > 0) node.scrollTop = savedTop;
        });
    }
    refreshIcons();
    if(type === 'audio') initializeDetailAudioWaveform(overlay, item, url);
    if(type === 'video') hydrateVideoStoryboardImages(overlay);
    if(type === 'font') initializeDetailFontPreview(overlay, url);
    else clearDetailViewerFontFace();
    if(type === 'lut') initializeDetailLutPreview(overlay, item);
    // 【QIN-580】画布节点上的 LUT 预览：节点渲染完再补挂覆盖 canvas（节点重建后 canvas 会丢）。
    if(mediaCanvasMode) hydrateDetailMediaCanvasLut();
    if(mediaCanvasMode && detailMediaCanvasCoverflowVisible){
        const refShell = overlay.querySelector('.asset-detail-media-canvas-shell');
        refShell?.classList.add('gw-coverflow-embedded-host');
        mountDetailReferenceCanvasCoverflow(refShell);
    }
    applyLightboxTransform(overlay);
    if(mediaCanvasMode) requestAnimationFrame(() => {
        const grid = overlay.querySelector?.('[data-detail-media-library-grid]');
        if(grid) grid.scrollTop = Math.max(0,Number(detailViewerState.mediaCanvasLibraryScrollTop || 0));
        observeDetailMediaCanvasViewport();
        positionDetailMediaVideoDock();
    });
    if(type === 'font') requestAnimationFrame(restoreDetailFontListScroll);
    if(type === 'lut') requestAnimationFrame(restoreDetailLutListScroll);
    if(mediaCanvasMode && !detailViewerState.mediaCanvasFitted) requestAnimationFrame(fitDetailMediaCanvas);
}
function clearDetailViewerFontFace(){
    detailViewerFontLoadToken += 1;
    if(detailViewerFontFace && document.fonts?.delete) document.fonts.delete(detailViewerFontFace);
    detailViewerFontFace = null;
    document.querySelector('.asset-detail-viewer')?.style.removeProperty('--asset-font-preview-family');
}
function syncDetailFontLineWidth(overlay){
    const copy = overlay?.querySelector?.('.asset-detail-font-copy-zh');
    if(!copy) return;
    copy.style.setProperty('--asset-font-zh-line-width', '100%');
}
function applyDetailFontScale(overlay, syncWidth=true){
    if(!overlay) return;
    const scale = Math.max(.5, Math.min(2.5, Number(detailViewerState.fontScale || 1)));
    overlay.style.setProperty('--asset-font-preview-zh-size', `${(23 * scale).toFixed(2)}px`);
    overlay.style.setProperty('--asset-font-preview-en-size', `${(29 * scale).toFixed(2)}px`);
    overlay.style.setProperty('--asset-font-preview-glyph-size', `${(22 * scale).toFixed(2)}px`);
    overlay.style.setProperty('--asset-font-preview-glyph-small-size', `${(17 * scale).toFixed(2)}px`);
    overlay.querySelectorAll('[data-detail-font-scale]').forEach(label => { label.textContent = `${Math.round(scale * 100)}%`; });
    if(syncWidth) syncDetailFontLineWidth(overlay);
}
function fitDetailFontLineWidth(overlay){
    if(!overlay?.querySelector?.('.asset-detail-font-copy-zh')) return;
    detailViewerState.fontLineWidth = 0;
    detailViewerState.fontFit = 1;
    applyDetailFontScale(overlay);
}
function adjustDetailFontScale(event){
    if(!event.target.closest?.('[data-detail-font-preview],.asset-detail-font-specimen')) return false;
    event.preventDefault();
    const current = Math.max(.5, Math.min(2.5, Number(detailViewerState.fontScale || 1)));
    const next = Math.max(.5, Math.min(2.5, Math.round((current + (event.deltaY < 0 ? .1 : -.1)) * 10) / 10));
    if(next === current) return true;
    detailViewerState.fontScale = next;
    applyDetailFontScale(document.querySelector('.asset-detail-viewer'));
    return true;
}
function toggleDetailFontPanel(panel){
    if(!['zh','en','glyph'].includes(panel)) return;
    const overlay = document.querySelector('.asset-detail-viewer');
    const root = overlay?.querySelector?.('[data-detail-font-preview]');
    const split = root?.querySelector?.('.asset-detail-font-split');
    if(!root || !split) return;
    const next = detailViewerState.fontExpanded === panel ? '' : panel;
    detailViewerState.fontExpanded = next;
    setDetailViewerFontExpandedPreference(next);
    try { localStorage.setItem(ASSET_DETAIL_FONT_PANEL_KEY, next); } catch(_) {}
    root.dataset.fontExpanded = next;
    split.dataset.fontExpanded = next;
    root.querySelectorAll('[data-detail-font-expand]').forEach(button => {
        const active = button.dataset.detailFontExpand === next;
        const label = button.dataset.detailFontExpand === 'zh' ? '中文预览' : button.dataset.detailFontExpand === 'en' ? '英文预览' : '基础字形';
        button.setAttribute('aria-pressed', String(active));
        button.title = active ? `收回${label}` : `放大${label}`;
        button.innerHTML = `<i data-lucide="${active ? 'minimize-2' : 'maximize-2'}"></i>`;
    });
    refreshIcons();
    requestAnimationFrame(() => fitDetailFontLineWidth(overlay));
}
function toggleDetailInspector(){
    const overlay = document.querySelector('.asset-detail-viewer');
    const body = overlay?.querySelector?.('.asset-detail-viewer-body');
    const inspector = overlay?.querySelector?.('[data-detail-inspector]');
    const button = inspector?.querySelector?.('[data-detail-inspector-toggle]');
    if(!body || !inspector || !button) return;
    detailViewerInspectorCollapsed = !detailViewerInspectorCollapsed;
    try { localStorage.setItem(ASSET_DETAIL_INSPECTOR_COLLAPSED_KEY, String(detailViewerInspectorCollapsed)); } catch(_) {}
    body.classList.toggle('asset-detail-inspector-collapsed', detailViewerInspectorCollapsed);
    inspector.classList.toggle('is-collapsed', detailViewerInspectorCollapsed);
    button.setAttribute('aria-expanded', String(!detailViewerInspectorCollapsed));
    button.title = detailViewerInspectorCollapsed ? '展开资产详情' : '收起资产详情';
    button.innerHTML = `<i data-lucide="${detailViewerInspectorCollapsed ? 'panel-right-open' : 'panel-right-close'}"></i>`;
    refreshIcons();
    if(detailViewerType(detailViewerState.source, detailViewerState.items[detailViewerState.index] || {}) === 'font'){
        requestAnimationFrame(() => fitDetailFontLineWidth(overlay));
    }
}
async function initializeDetailFontPreview(overlay, url){
    clearDetailViewerFontFace();
    const root = overlay?.querySelector?.('[data-detail-font-preview]');
    const status = root?.querySelector?.('[data-detail-font-load]');
    const token = detailViewerFontLoadToken;
    if(!root || !status) return;
    applyDetailFontScale(overlay);
    if(!url || typeof FontFace === 'undefined' || !document.fonts){
        root.classList.add('is-error');
        status.textContent = '当前浏览器无法载入字体，已显示系统回退字形';
        return;
    }
    const family = `AssetDetailPreview${token}`;
    try {
        const face = await new FontFace(family, `url(${JSON.stringify(url)})`).load();
        if(token !== detailViewerFontLoadToken || !root.isConnected) return;
        document.fonts.add(face);
        detailViewerFontFace = face;
        overlay.style.setProperty('--asset-font-preview-family', `"${family}"`);
        await document.fonts.ready;
        await new Promise(resolve => requestAnimationFrame(resolve));
        if(token !== detailViewerFontLoadToken || !root.isConnected) return;
        fitDetailFontLineWidth(overlay);
        root.classList.add('is-ready');
        status.textContent = '当前字体已载入';
    } catch(err){
        if(token !== detailViewerFontLoadToken || !root.isConnected) return;
        root.classList.add('is-error');
        status.textContent = '字体载入失败，已显示系统回退字形';
    }
}
function detailAudioWaveformPeaks(buffer, maxPoints=24000){
    const length = Math.max(1, buffer.length || 1);
    const pointCount = Math.max(256, Math.min(maxPoints, length));
    const stride = Math.max(1, Math.ceil(length / pointCount));
    const minimum = new Float32Array(pointCount);
    const maximum = new Float32Array(pointCount);
    const channels = Array.from({length:Math.max(1, buffer.numberOfChannels || 1)}, (_, index) => buffer.getChannelData(index));
    for(let point=0; point<pointCount; point++){
        const start = point * stride;
        const end = Math.min(length, start + stride);
        let low = 0;
        let high = 0;
        for(let sample=start; sample<end; sample++){
            for(const channel of channels){
                const value = channel[sample] || 0;
                if(value < low) low = value;
                if(value > high) high = value;
            }
        }
        minimum[point] = low;
        maximum[point] = high;
    }
    return {minimum, maximum, duration:Number(buffer.duration || 0)};
}
function drawDetailAudioWaveform(overlay, waveform){
    const shell = overlay?.querySelector?.('[data-detail-audio-waveform]');
    const track = shell?.querySelector?.('.asset-detail-waveform-track');
    const canvas = track?.querySelector?.('canvas');
    if(!shell || !track || !canvas || !waveform) return;
    const zoom = Math.max(1, Math.min(8, Number(detailViewerState.audioZoom || 1)));
    const cssWidth = Math.max(shell.clientWidth, Math.min(24000, Math.round(shell.clientWidth * zoom)));
    const cssHeight = Math.max(150, shell.clientHeight);
    const pixelRatio = Math.min(1.5, window.devicePixelRatio || 1);
    track.style.width = `${cssWidth}px`;
    canvas.style.width = `${cssWidth}px`;
    canvas.style.height = `${cssHeight}px`;
    canvas.width = Math.max(1, Math.round(cssWidth * pixelRatio));
    canvas.height = Math.max(1, Math.round(cssHeight * pixelRatio));
    const context = canvas.getContext('2d');
    if(!context) return;
    const signalFlow = Boolean(document.body?.classList.contains('gw-signal'));
    const darkTheme = Boolean(document.querySelector('.theme-dark'));
    context.setTransform(pixelRatio, 0, 0, pixelRatio, 0, 0);
    context.clearRect(0, 0, cssWidth, cssHeight);
    const center = cssHeight / 2;
    const amplitude = Math.max(24, cssHeight * .42);
    context.strokeStyle = signalFlow ? 'rgba(173,205,220,.2)' : (darkTheme ? 'rgba(223,230,218,.2)' : 'rgba(31,41,35,.2)');
    context.lineWidth = 1;
    context.beginPath();
    context.moveTo(0, Math.round(center) + .5);
    context.lineTo(cssWidth, Math.round(center) + .5);
    context.stroke();
    const {minimum, maximum} = waveform;
    const points = Math.max(1, minimum.length);
    context.strokeStyle = signalFlow ? '#69d6ff' : (darkTheme ? '#dfe6da' : '#222b25');
    context.lineWidth = 1;
    context.beginPath();
    for(let x=0; x<cssWidth; x++){
        const start = Math.min(points - 1, Math.floor((x / cssWidth) * points));
        const end = Math.max(start + 1, Math.min(points, Math.ceil(((x + 1) / cssWidth) * points)));
        let low = 0;
        let high = 0;
        for(let index=start; index<end; index++){
            if(minimum[index] < low) low = minimum[index];
            if(maximum[index] > high) high = maximum[index];
        }
        context.moveTo(x + .5, center + low * amplitude);
        context.lineTo(x + .5, center + high * amplitude);
    }
    context.stroke();
    shell.classList.add('is-loaded');
}
function syncDetailAudioPlayhead(overlay, player){
    const playhead = overlay?.querySelector?.('.asset-detail-waveform-playhead');
    const duration = Number(player?.duration || 0);
    if(!playhead || !duration || !Number.isFinite(duration)) return;
    playhead.style.left = `${Math.max(0, Math.min(100, (Number(player.currentTime || 0) / duration) * 100))}%`;
}
function runDetailAudioPlayhead(overlay, player){
    cancelAnimationFrame(detailViewerAudioFrame);
    const update = () => {
        if(!detailViewerState.open || !player?.isConnected) return;
        syncDetailAudioPlayhead(overlay, player);
        if(!player.paused) setDetailViewerAudioFrame(requestAnimationFrame(update));
    };
    update();
}
async function initializeDetailAudioWaveform(overlay, item, url){
    const shell = overlay?.querySelector?.('[data-detail-audio-waveform]');
    const player = overlay?.querySelector?.('audio.asset-detail-player');
    if(!shell || !player || !url) return;
    const key = detailViewerFileKey(detailViewerState.source, item);
    const updateDuration = value => {
        const label = overlay.querySelector('[data-detail-audio-duration]');
        if(label && Number(value || 0)) label.textContent = formatMediaDuration(value);
    };
    player.addEventListener('loadedmetadata', () => {
        updateDuration(player.duration);
        syncDetailAudioPlayhead(overlay, player);
    });
    player.addEventListener('timeupdate', () => syncDetailAudioPlayhead(overlay, player));
    player.addEventListener('play', () => runDetailAudioPlayhead(overlay, player));
    player.addEventListener('pause', () => runDetailAudioPlayhead(overlay, player));
    shell.addEventListener('click', event => {
        if(!Number(player.duration) || !Number.isFinite(player.duration)) return;
        const track = shell.querySelector('.asset-detail-waveform-track');
        const bounds = track?.getBoundingClientRect();
        if(!bounds?.width) return;
        const ratio = Math.max(0, Math.min(1, (event.clientX - bounds.left) / bounds.width));
        player.currentTime = ratio * player.duration;
        syncDetailAudioPlayhead(overlay, player);
    });
    try {
        let waveform = detailViewerAudioWaveformCache.get(key);
        if(!waveform){
            try {
                const response = await assetManagerApi.getMedia(url);
                if(!response.ok) throw new Error(`音频读取失败 (${response.status})`);
                const AudioContextClass = window.AudioContext || window.webkitAudioContext;
                if(!AudioContextClass) throw new Error('当前浏览器不支持音频波形解析');
                const context = new AudioContextClass();
                try {
                    const buffer = await context.decodeAudioData(await response.arrayBuffer());
                    waveform = detailAudioWaveformPeaks(buffer);
                } finally {
                    context.close?.();
                }
            } catch(_browserDecodeError){
                const params = detailViewerFileParams(detailViewerState.source, item);
                params.set('points', '24000');
                const data = await apiJsonResponse(assetManagerApi.getAudioWaveformData(params.toString()));
                waveform = {
                    minimum:Float32Array.from(data.minimum || []),
                    maximum:Float32Array.from(data.maximum || []),
                    duration:Number(data.duration || 0),
                };
            }
            detailViewerAudioWaveformCache.set(key, waveform);
        }
        if(!detailViewerState.open || detailViewerFileKey(detailViewerState.source, detailViewerState.items[detailViewerState.index] || {}) !== key) return;
        updateDuration(waveform.duration);
        drawDetailAudioWaveform(overlay, waveform);
    } catch(err){
        const status = shell.querySelector('.asset-detail-waveform-status');
        shell.classList.add('is-failed');
        if(status){ status.textContent = err.message || '波形解析失败'; }
    }
}
function adjustDetailAudioZoom(event){
    const overlay = document.querySelector('.asset-detail-viewer.asset-detail-type-audio');
    const shell = event.target.closest?.('[data-detail-audio-waveform]');
    const track = shell?.querySelector?.('.asset-detail-waveform-track');
    const player = overlay?.querySelector?.('audio.asset-detail-player');
    if(!overlay || !shell || !track || !player || Math.abs(event.deltaY) < 4) return false;
    event.preventDefault();
    const current = Math.max(1, Number(detailViewerState.audioZoom || 1));
    const next = Math.max(1, Math.min(8, current * (event.deltaY < 0 ? 1.18 : .84)));
    if(Math.abs(next - current) < .01) return true;
    const duration = Number(player.duration || 0);
    const ratio = duration && Number.isFinite(duration) ? Number(player.currentTime || 0) / duration : .5;
    const oldWidth = Math.max(1, track.scrollWidth);
    const playheadViewportX = Math.max(0, Math.min(shell.clientWidth, ratio * oldWidth - shell.scrollLeft));
    detailViewerState.audioZoom = next;
    const key = detailViewerFileKey(detailViewerState.source, detailViewerState.items[detailViewerState.index] || {});
    drawDetailAudioWaveform(overlay, detailViewerAudioWaveformCache.get(key));
    const newWidth = Math.max(1, track.scrollWidth);
    shell.scrollLeft = Math.max(0, ratio * newWidth - playheadViewportX);
    const label = overlay.querySelector('[data-detail-audio-zoom]');
    if(label) label.textContent = `${next.toFixed(1)}×`;
    syncDetailAudioPlayhead(overlay, player);
    return true;
}
function updateDetailViewerPath(item, value='', error=''){
    const button = document.querySelector('.asset-detail-viewer [data-detail-file-reveal]');
    const label = button?.querySelector?.('[data-detail-path-value]');
    if(!button || !label) return;
    label.textContent = value || error || '位置不可用';
    button.disabled = !value;
    button.title = value ? '在文件夹中显示此文件' : (error || '位置不可用');
    if(value) item.absolute_path = value;
}
async function loadDetailViewerFileInfo(){
    if(!detailViewerState.open) return;
    let item = detailViewerFileItem();
    if(!item || item.is_remote) return;
    let params = detailViewerFileParams(detailViewerState.source, item);
    if(!params.toString() && detailViewerState.source !== 'registry'){
        const candidate = detailMediaCanvasItems().find(value => String(value.id || '') === String(detailViewerState.mediaCanvasActiveId || '')) || item;
        if(candidate && ['image','video'].includes(detailViewerType(detailViewerState.source, candidate))){
            try {
                await ensureDetailReferenceAssetIds([candidate], {silent:true});
                item = detailViewerFileItem();
                params = detailViewerFileParams(detailViewerState.source, item);
            } catch(_) {}
        }
    }
    const key = detailViewerFileKey(detailViewerState.source, item);
    const cached = detailViewerFileInfoCache.get(key);
    if(cached){
        updateDetailViewerPath(item, cached.path || '', cached.error || '');
        if(detailViewerType(detailViewerState.source, item) === 'model'){
            detailViewerState.modelInfo = cached.model_preview || null;
            detailViewerState.modelInfoLoading = false;
            detailViewerState.modelInfoError = cached.error || '';
            renderDetailViewer();
        }
        return;
    }
    if(!params.toString()){ updateDetailViewerPath(item, '', '位置不可用'); return; }
    try {
        const data = await apiJsonResponse(assetManagerApi.getAssetFileInfo(params.toString()));
        detailViewerFileInfoCache.set(key, data);
        if(detailViewerState.open && detailViewerFileKey(detailViewerState.source, detailViewerFileItem()) === key){
            updateDetailViewerPath(item, data.path || '', '位置不可用');
            if(detailViewerType(detailViewerState.source, item) === 'model'){
                detailViewerState.modelInfo = data.model_preview || null;
                detailViewerState.modelInfoLoading = false;
                detailViewerState.modelInfoError = '';
                renderDetailViewer();
            }
        }
    } catch(err){
        const value = {error:err.message || '完整路径读取失败'};
        detailViewerFileInfoCache.set(key, value);
        if(detailViewerState.open && detailViewerFileKey(detailViewerState.source, detailViewerFileItem()) === key){
            updateDetailViewerPath(item, '', value.error);
            if(detailViewerType(detailViewerState.source, item) === 'model'){
                detailViewerState.modelInfo = null;
                detailViewerState.modelInfoLoading = false;
                detailViewerState.modelInfoError = value.error;
                renderDetailViewer();
            }
        }
    }
}
async function revealDetailViewerFile(){
    let item = detailViewerFileItem();
    if(!item || item.is_remote) return;
    let params = detailViewerFileParams(detailViewerState.source, item);
    if(!params.toString() && detailViewerState.source !== 'registry'){
        const candidate = detailMediaCanvasItems().find(value => String(value.id || '') === String(detailViewerState.mediaCanvasActiveId || '')) || item;
        if(candidate){
            setStatus('正在解析素材真实路径…');
            await ensureDetailReferenceAssetIds([candidate], {silent:false});
            item = detailViewerFileItem();
            params = detailViewerFileParams(detailViewerState.source, item);
        }
    }
    if(!params.toString() && !item.absolute_path) throw new Error('当前素材没有可用的本地文件路径');
    if(!item.absolute_path){
        await loadDetailViewerFileInfo();
        item = detailViewerFileItem();
    }
    await apiJsonResponse(assetManagerApi.revealAssetFile({asset_id:params.get('asset_id') || '', url:params.get('url') || '', path:item.absolute_path || ''}));
    setStatus('已在文件夹中定位素材');
}
async function loadDetailViewerText(){
    if(!detailViewerState.open) return;
    const item = detailViewerState.items[detailViewerState.index];
    if(!item || detailViewerType(detailViewerState.source, item) !== 'text') return;
    const key = detailViewerTextKey(detailViewerState.source, item);
    if(detailViewerState.source === 'prompt'){
        detailViewerState.text = detailViewerPromptText(item);
        detailViewerState.textLoading = false;
        renderDetailViewer();
        return;
    }
    if(detailViewerTextCache.has(key)){
        const cached = detailViewerTextCache.get(key);
        detailViewerState.text = typeof cached === 'string' ? cached : String(cached?.text || '');
        detailViewerState.textTruncated = typeof cached === 'object' && Boolean(cached?.truncated);
        detailViewerState.textLoading = false;
        renderDetailViewer();
        return;
    }
    const params = detailViewerFileParams(detailViewerState.source, item);
    params.set('limit', String(4 * 1024 * 1024));
    if(!params.has('asset_id') && !params.has('url')){
        detailViewerState.textError = '该内容没有可读取的本地文件。';
        renderDetailViewer();
        return;
    }
    detailViewerState.textLoading = true;
    detailViewerState.textError = '';
    renderDetailViewer();
    try {
        const data = await apiJsonResponse(assetManagerApi.getAssetContent(params.toString()));
        const content = `${data.text || ''}${data.truncated ? '\n\n[正文超过 4 MB，当前仅供阅读，不能直接编辑]' : ''}`;
        detailViewerTextCache.set(key, {text:content, truncated:Boolean(data.truncated)});
        if(detailViewerState.open && detailViewerTextKey(detailViewerState.source, detailViewerState.items[detailViewerState.index] || {}) === key){
            detailViewerState.text = content;
            detailViewerState.textTruncated = Boolean(data.truncated);
            detailViewerState.textLoading = false;
            renderDetailViewer();
        }
    } catch(err){
        if(detailViewerState.open && detailViewerTextKey(detailViewerState.source, detailViewerState.items[detailViewerState.index] || {}) === key){
            detailViewerState.textLoading = false;
            detailViewerState.textError = err.message || '正文读取失败';
            renderDetailViewer();
        }
    }
}
function detailTextVersionParams(item=detailViewerState.items[detailViewerState.index]){
    const fileParams = detailViewerFileParams(detailViewerState.source, item || {});
    return {
        query:new URLSearchParams({
            asset_id:fileParams.get('asset_id') || '',
            url:fileParams.get('url') || '',
        }),
        payload:{asset_id:fileParams.get('asset_id') || '', url:fileParams.get('url') || ''},
    };
}
async function loadDetailTextVersions(force=false){
    if(!detailViewerState.open) return;
    const item = detailViewerState.items[detailViewerState.index];
    if(!item || detailViewerType(detailViewerState.source, item) !== 'text' || !detailViewerCanEditText(detailViewerState.source, item)) return;
    if(detailViewerState.textVersionsLoading || (!force && detailViewerState.textVersions.length)) return;
    const key = detailViewerTextKey(detailViewerState.source, item);
    const {query} = detailTextVersionParams(item);
    detailViewerState.textVersionsLoading = true;
    detailViewerState.textVersionError = '';
    renderDetailViewer();
    try {
        const data = await apiJsonResponse(assetManagerApi.getAssetContentVersions(query.toString()));
        if(detailViewerState.open && detailViewerTextKey(detailViewerState.source, detailViewerState.items[detailViewerState.index] || {}) === key){
            detailViewerState.textVersions = Array.isArray(data.versions) ? data.versions : [];
            detailViewerState.textVersionsLoading = false;
            renderDetailViewer();
        }
    } catch(err){
        if(detailViewerState.open && detailViewerTextKey(detailViewerState.source, detailViewerState.items[detailViewerState.index] || {}) === key){
            detailViewerState.textVersionsLoading = false;
            detailViewerState.textVersionError = err.message || '版本读取失败';
            renderDetailViewer();
        }
    }
}
async function selectDetailTextVersion(versionId, edit=false){
    if(!versionId || detailViewerState.textEditing) return;
    const item = detailViewerState.items[detailViewerState.index];
    const {query} = detailTextVersionParams(item);
    detailViewerState.textVersionError = '';
    try {
        const data = await apiJsonResponse(assetManagerApi.getAssetContentVersion(versionId, query.toString()));
        detailViewerState.selectedTextVersion = data.version || null;
        detailViewerState.textVersionEditing = Boolean(edit && data.version);
        renderDetailViewer();
        if(edit) requestAnimationFrame(() => document.querySelector('[data-detail-version-editor]')?.focus());
    } catch(err){
        detailViewerState.textVersionError = err.message || '版本读取失败';
        renderDetailViewer();
    }
}
function returnToCurrentDetailText(){
    detailViewerState.selectedTextVersion = null;
    detailViewerState.textVersionEditing = false;
    renderDetailViewer();
    requestAnimationFrame(() => document.querySelector('[data-detail-text-edit]')?.focus());
}
function startDetailTextVersionEdit(){
    if(!detailViewerState.selectedTextVersion) return;
    detailViewerState.textVersionEditing = true;
    renderDetailViewer();
    requestAnimationFrame(() => document.querySelector('[data-detail-version-editor]')?.focus());
}
function cancelDetailTextVersionEdit(){
    if(!detailViewerState.textVersionEditing) return;
    detailViewerState.textVersionEditing = false;
    renderDetailViewer();
}
async function deleteDetailTextVersion(versionId){
    const version = detailViewerState.textVersions.find(item => item.id === versionId);
    if(!versionId || !window.confirm(`确定删除版本 v${Number(version?.version_number || 0)}？删除后无法恢复。`)) return;
    const {query} = detailTextVersionParams();
    await apiJsonResponse(assetManagerApi.deleteAssetContentVersion(versionId, query.toString()));
    if(detailViewerState.selectedTextVersion?.id === versionId) returnToCurrentDetailText();
    await loadDetailTextVersions(true);
    setStatus('文本版本已删除');
}
async function restoreDetailTextVersion(versionId){
    const version = detailViewerState.textVersions.find(item => item.id === versionId);
    if(!versionId || !window.confirm(`确定将版本 v${Number(version?.version_number || 0)} 恢复为当前正文？系统会保留一条新的恢复记录。`)) return;
    const {payload} = detailTextVersionParams();
    let source = detailViewerState.selectedTextVersion?.id === versionId ? detailViewerState.selectedTextVersion : null;
    if(!source){
        const {query} = detailTextVersionParams();
        const data = await apiJsonResponse(assetManagerApi.getAssetContentVersion(versionId, query.toString()));
        source = data.version;
    }
    const data = await apiJsonResponse(assetManagerApi.restoreAssetContentVersion(versionId, payload));
    const item = detailViewerState.items[detailViewerState.index];
    const text = String(source?.content || '');
    detailViewerState.text = text;
    detailViewerState.selectedTextVersion = null;
    detailViewerState.textVersionEditing = false;
    detailViewerState.textEditing = false;
    item.size = Number(data.size || item.size || 0);
    item.updated_at = Number(data.updated_at || item.updated_at || Date.now());
    detailViewerTextCache.set(detailViewerTextKey(detailViewerState.source, item), {text, truncated:false});
    await loadDetailTextVersions(true);
    loadDetailViewerFileInfo();
    setStatus(`已恢复版本 v${Number(version?.version_number || 0)}`);
}
function startDetailTextEdit(){
    const item = detailViewerState.items[detailViewerState.index];
    if(!item || !detailViewerCanEditText(detailViewerState.source, item) || detailViewerState.textLoading || detailViewerState.textError || detailViewerState.textTruncated) return;
    detailViewerState.textEditing = true;
    renderDetailViewer();
    requestAnimationFrame(() => {
        const editor = document.querySelector('[data-detail-text-editor]');
        editor?.focus();
        editor?.setSelectionRange?.(0, 0);
    });
}
function cancelDetailTextEdit(){
    if(!detailViewerState.textEditing) return;
    detailViewerState.textEditing = false;
    renderDetailViewer();
    requestAnimationFrame(() => document.querySelector('[data-detail-text-edit]')?.focus());
}
async function returnFromDetailViewer(destination='origin'){
    const originTab = assetBoardById(detailViewerState.originTab) ? detailViewerState.originTab : activeTab;
    if(!closeDetailPreview()) return;
    await activateAssetBoard(destination === 'home' ? 'registry' : originTab);
}
async function hydrateDetailRegistryAsset(id){
    if(!id) return;
    try {
        const canvasItem = detailMediaCanvasItems().find(item => item.id === id) || detailViewerState.items.find(item => item.id === id) || null;
        if(canvasItem && !detailMediaCanvasPersistId(canvasItem)) await ensureDetailReferenceAssetIds([canvasItem],{silent:true});
        const assetId = canvasItem ? detailMediaCanvasPersistId(canvasItem) : (detailViewerState.source === 'registry' ? id : '');
        if(!assetId) return;
        const data = await apiJsonResponse(assetManagerApi.getRegistryAsset(assetId));
        const updated = normalizeRegistryAsset(data.asset || {});
        if(!updated.id) return;
        const registryIndex = registryAssets.findIndex(item => item.id === updated.id);
        if(registryIndex >= 0) registryAssets[registryIndex] = {...registryAssets[registryIndex],...updated};
        else registryAssets.unshift(updated);
        if(selectedRegistryAssetId === updated.id) setRegistryDetail(updated);
        if(detailViewerState.open){
            const mergeItem = item => {
                if(item.id !== id && detailMediaCanvasPersistId(item) !== updated.id) return item;
                if(detailViewerState.source === 'registry') return {...item,...updated};
                mergeResolvedReferenceAsset(item,updated);
                return item;
            };
            detailViewerState.items = detailViewerState.items.map(mergeItem);
            detailViewerState.mediaCanvasItems = detailViewerState.mediaCanvasItems.map(mergeItem);
            if(!detailMediaCanvasItems().length && detailViewerState.mediaCanvasLayout?.[id]?.auto) delete detailViewerState.mediaCanvasLayout[id];
            if(detailViewerState.mediaCanvasActiveId === id && detailViewerType(detailViewerState.source,canvasItem || {}) === 'video' && detailVideoStoryboardEnabled()) detailViewerState.mediaCanvasFitted = false;
            if(detailMediaCanvasItems().length){
                syncDetailMediaCanvasActiveDom();
                refreshDetailImageVersionsPanelDom();
            } else renderDetailViewer();
        }
    } catch(err){ setStatus(err.message || '资产详情读取失败'); }
}
async function openDetailOfficeAsset(id){
    if(!id) return;
    const data = await apiJsonResponse(assetManagerApi.openRegistryAssetLocal(id));
    setStatus(`已使用本机文档应用打开：${data.name || 'Office 文件'}`);
}
async function exportDetailOfficePdf(id, button){
    if(!id || button?.disabled) return;
    if(button) button.disabled = true;
    setStatus('正在通过 LibreOffice 转换 PDF…');
    try {
        const response = await assetManagerApi.downloadAssetContentPdf(id);
        if(response.status === 401) window.dispatchEvent(new CustomEvent('asset-auth-needed'));
        if(!response.ok){
            const detail = await response.json().catch(() => ({}));
            throw new Error(detail.detail || `PDF 转换失败（${response.status}）`);
        }
        const blob = await response.blob();
        const url = URL.createObjectURL(blob);
        const link = document.createElement('a');
        link.href = url;
        link.download = registryDownloadName(response, 'document.pdf');
        document.body.appendChild(link);
        link.click();
        link.remove();
        setTimeout(() => URL.revokeObjectURL(url), 1000);
        setStatus(`PDF 已导出：${link.download}`);
    } finally {
        if(button) button.disabled = false;
    }
}
function showDetailPreview(source, id, options={}){
    const suppliedItems = Array.isArray(options.items) ? options.items.filter(Boolean) : [];
    const item = suppliedItems.find(value => String(value.id || '') === String(id || '')) || detailViewerItem(source, id);
    if(!item) return;
    const items = (suppliedItems.length ? suppliedItems : detailViewerItems(source).filter(Boolean)).slice();
    let index = items.findIndex(value => value.id === item.id);
    if(index < 0){ items.unshift(item); index = 0; }
    let mediaCanvasItems = (Array.isArray(options.mediaCanvasItems) ? options.mediaCanvasItems : initialDetailMediaCanvasItems(source, item)).filter(Boolean);
    mediaCanvasItems = mediaCanvasItems.map(value => {
        const assetId=detailMediaCanvasPersistId(value,source);
        const explicit=String(value.image_version_selection || value.asset_version_id || value.reference_edit?.asset_version_id || '');
        if(assetId && detailViewerType(source,value)==='image' && !explicit){
            return {...value,url:`/api/asset-registry/assets/${encodeURIComponent(assetId)}/media`,reference_edit:null,image_version_selection:'original',asset_version_id:'',asset_version_number:0};
        }
        return value;
    });
    const storedReferenceCanvas = source === 'canvas-asset' && item.canvas_kind === 'reference';
    const referenceCanvasMode = Boolean(options.referenceCanvasMode || storedReferenceCanvas);
    const mediaCanvasAnnotations = storedReferenceCanvas && Array.isArray(item.reference_annotations) ? item.reference_annotations.map(value => ({
        ...value,
        width:Number(value.width || value.w || 280), height:Number(value.height || value.h || 120),
        backgroundColor:value.backgroundColor || value.background_color || '#202622',
        textColor:value.textColor || value.text_color || '#ffffff',
        fontSize:Math.max(8,Number(value.fontSize || value.font_size || 15)),
        padding:Math.max(2,Number(value.padding || 10)),
    })) : [];
    const mediaCanvasDrawings = initialDetailMediaCanvasDrawings(source,item,mediaCanvasItems);
    const mediaCanvasGroups = initialDetailMediaCanvasGroups(source,item,mediaCanvasItems,mediaCanvasAnnotations,mediaCanvasDrawings);
    const initialMediaCanvasObjectId = mediaCanvasGroups.find(group => group.memberIds.includes(item.id))?.id || item.id;
    setDetailViewerState({
        open:true, source, id:item.id, index, items, text:'', textLoading:false, textError:'', textEditing:false,
        textTruncated:false, textVersions:[], textVersionsLoading:false, selectedTextVersion:null,
        textVersionEditing:false, textVersionError:'', modelInfo:null, modelInfoLoading:detailViewerType(source, item) === 'model', modelInfoError:'',
        wheelAt:0, audioZoom:1, fontScale:1, fontFit:1, fontLineWidth:0, fontExpanded:detailViewerFontExpandedPreference, fontListScrollTop:0,
        mediaCanvasItems, mediaCanvasActiveId:mediaCanvasItems.length ? item.id : '', mediaCanvasSelectedIds:new Set(mediaCanvasItems.length ? [initialMediaCanvasObjectId] : []), mediaCanvasSelectionOrder:mediaCanvasItems.length ? [initialMediaCanvasObjectId] : [],
        mediaCanvasLayout:initialDetailMediaCanvasLayout(source, mediaCanvasItems),
        mediaCanvasAnnotations,
        mediaCanvasDrawings,
        mediaCanvasGroups, mediaCanvasContextMenu:null,
        mediaCanvasEditingAnnotationId:'', mediaCanvasHistory:[], mediaCanvasSnap:true,
        mediaCanvasNormalizeBasis:'first', mediaCanvasExportScope:'all', mediaCanvasLibrarySelectedId:item.id,
        mediaCanvasLibraryQuery:'', mediaCanvasLibrarySort:registrySort || 'newest', mediaCanvasLibraryScope:detailMediaCanvasInitialLibraryScope(activeTab), mediaCanvasLibraryStorageRootId:'all', mediaCanvasLibraryScrollTop:0, mediaCanvasSidebarView:'detail',
        mediaCanvasLibraryVisible:mediaCanvasItems.length > 1 || referenceCanvasMode,
        mediaCanvasScale:1, mediaCanvasX:0, mediaCanvasY:0, mediaCanvasFitted:false,
        videoRanges:{}, videoExportRootId:'', videoExportBusy:'', videoStoryboardExpandedId:'',
        // MVP1-T6：跳帧目标必须在首帧渲染前就写入状态，否则媒体画布节点重建后
        // 会丢掉只挂过一次的 loadedmetadata 监听，跳帧静默失效。
        startTime:options.startTime != null && Number.isFinite(Number(options.startTime))
            ? alignTimeToFps(Number(options.startTime), options.fps || Number(item?.frame_rate || item?.metadata?.frame_rate || 0))
            : null,
        imageVersions:[],imageVersionsAssetId:'',imageVersionsLoading:false,imageVersionSelectedId:'',imageVersionPanelCollapsed:true,
        // QIN-580：画布节点的 LUT 配置与菜单状态每次打开详情都清零；
        // 但字段必须显式写在字面量里——整体重新赋值会把没列出的字段抹成 undefined，
        // 强度取不到值就会退化成 0（等于没套），这里漏一个字段就是「预览看起来没生效」。
        lutStrength:1, lutCompare:'applied', lutListScrollTop:0,
        mediaCanvasLut:{}, mediaCanvasLutPanel:'', mediaLutMenu:{open:false, step:'asset', assetId:''},
        referenceCanvasId:storedReferenceCanvas ? String(item.canvas_id || '') : '', referenceCanvasTitle:String(options.referenceCanvasTitle || (storedReferenceCanvas ? item.canvas_title || '参考画布' : '参考画布')), referenceCanvasMode, referenceCanvasStructureId:referenceCanvasMode ? String(options.referenceCanvasStructureId || '') : '', referenceCanvasSaving:false,
        originTab:options.originTab || activeTab, returnFocus:options.returnFocus || document.activeElement
    });
    stopAssetHoverPreview();
    detailViewerSyncSelection(source, item);
    renderDetailViewer();
    preloadDetailViewerNeighbors();
    loadDetailViewerText();
    loadDetailTextVersions();
    loadDetailImageVersions(item);
    loadDetailViewerFileInfo();
    if(['registry','canvas-asset'].includes(source) || detailMediaCanvasPersistId(item,source)) hydrateDetailRegistryAsset(item.id);
    document.body.classList.add('asset-lightbox-open');
    if((detailViewerType(source, item) === 'video' || options.startTime != null) && typeof window !== 'undefined' && !window.MediaPreviewPlayer){
        ensureMediaPreviewPlayer();
    }
    if(options.startTime != null && Number.isFinite(Number(options.startTime))){
        const effectiveFps = options.fps || Number(item?.frame_rate || item?.metadata?.frame_rate || 0);
        const targetTime = alignTimeToFps(Number(options.startTime), effectiveFps);
        detailViewerState.startTime = targetTime;
        // seekDetailVideoTo 内部已具备 readyState 状态自适应与 loadedmetadata 事件监听，无需盲等一帧
        seekDetailVideoTo(targetTime, { play: true, fps: effectiveFps });
    }
}
function navigateDetailPreview(delta){
    if(!detailViewerState.open || !delta) return;
    if(detailViewerState.textEditing || detailViewerState.textVersionEditing){ setStatus('请先保存或取消文本编辑'); return; }
    const currentType = detailViewerType(detailViewerState.source, detailViewerState.items[detailViewerState.index] || {});
    const next = Math.max(0, Math.min(detailViewerState.items.length - 1, detailViewerState.index + Number(delta)));
    if(next === detailViewerState.index) return;
    const nextType = detailViewerType(detailViewerState.source, detailViewerState.items[next] || {});
    const keepFontState = currentType === 'font' && nextType === 'font';
    const replaceSingleMediaCanvas = detailMediaCanvasItems().length === 1;
    const currentCanvasId = detailViewerState.mediaCanvasActiveId || '';
    const currentCanvasSelected = currentCanvasId && detailMediaCanvasSelectedIds().has(detailMediaCanvasSelectionObjectId(currentCanvasId));
    document.querySelector('.asset-detail-viewer')?.querySelectorAll('audio,video').forEach(media => media.pause?.());
    detailViewerState.index = next;
    detailViewerState.id = detailViewerState.items[next]?.id || '';
    if(replaceSingleMediaCanvas){
        const nextItem = detailViewerState.items[next];
        detailViewerState.mediaCanvasItems = ['image','video'].includes(nextType) && nextItem ? [nextItem] : [];
        detailViewerState.mediaCanvasActiveId = detailViewerState.mediaCanvasItems[0]?.id || '';
        detailViewerState.mediaCanvasSelectedIds = new Set(currentCanvasSelected && detailViewerState.mediaCanvasActiveId ? [detailViewerState.mediaCanvasActiveId] : []);
        detailViewerState.mediaCanvasSelectionOrder = currentCanvasSelected && detailViewerState.mediaCanvasActiveId ? [detailViewerState.mediaCanvasActiveId] : [];
        detailViewerState.mediaCanvasLayout = {};
        detailViewerState.mediaCanvasAnnotations = [];
        detailViewerState.mediaCanvasDrawings = [];
        detailViewerState.mediaCanvasGroups = [];
        detailViewerState.mediaCanvasContextMenu = null;
        detailViewerState.mediaCanvasEditingAnnotationId = '';
        detailViewerState.mediaCanvasHistory = [];
        detailViewerState.mediaCanvasLibraryVisible = false;
        detailViewerState.mediaCanvasLibrarySelectedId = detailViewerState.mediaCanvasActiveId;
        detailViewerState.mediaCanvasFitted = false;
    }
    detailViewerState.text = '';
    detailViewerState.textLoading = false;
    detailViewerState.textError = '';
    detailViewerState.textEditing = false;
    detailViewerState.textTruncated = false;
    detailViewerState.textVersions = [];
    detailViewerState.textVersionsLoading = false;
    detailViewerState.selectedTextVersion = null;
    detailViewerState.textVersionEditing = false;
    detailViewerState.textVersionError = '';
    detailViewerState.imageVersions=[];
    detailViewerState.imageVersionsAssetId='';
    detailViewerState.imageVersionsLoading=false;
    detailViewerState.imageVersionSelectedId='';
    detailViewerState.modelInfo = null;
    detailViewerState.modelInfoLoading = detailViewerType(detailViewerState.source, detailViewerState.items[next] || {}) === 'model';
    detailViewerState.modelInfoError = '';
    detailViewerState.audioZoom = 1;
    if(!keepFontState){
        detailViewerState.fontScale = 1;
        detailViewerState.fontFit = 1;
        detailViewerState.fontLineWidth = 0;
        detailViewerState.fontExpanded = detailViewerFontExpandedPreference;
        detailViewerState.fontListScrollTop = 0;
    }
    cancelAnimationFrame(detailViewerAudioFrame);
    detailViewerSyncSelection(detailViewerState.source, detailViewerState.items[next]);
    renderDetailViewer();
    preloadDetailViewerNeighbors();
    loadDetailViewerText();
    loadDetailTextVersions();
    loadDetailImageVersions(detailViewerState.items[next]);
    loadDetailViewerFileInfo();
    if(detailViewerState.source === 'registry') hydrateDetailRegistryAsset(detailViewerState.id);
}
function navigateDetailPreviewToId(id){
    if(!detailViewerState.open || !id) return;
    const next = detailViewerState.items.findIndex(item => item.id === id);
    if(next < 0 || next === detailViewerState.index) return;
    navigateDetailPreview(next - detailViewerState.index);
}
function navigateDetailFontPreviewToId(id){
    if(!detailViewerState.open || !id) return;
    detailViewerState.fontListScrollTop = document.querySelector('.asset-detail-viewer .asset-detail-font-library-list')?.scrollTop || 0;
    const currentId = detailViewerState.items[detailViewerState.index]?.id || detailViewerState.id;
    const fonts = detailViewerFontItems();
    const currentIndex = fonts.findIndex(item => item.id === currentId);
    const nextIndex = fonts.findIndex(item => item.id === id);
    if(currentIndex < 0 || nextIndex < 0 || currentIndex === nextIndex) return;
    detailViewerState.items = fonts;
    detailViewerState.index = currentIndex;
    detailViewerState.id = currentId;
    navigateDetailPreview(nextIndex - currentIndex);
}
function toggleDetailPreviewPlayback(videoId=''){
    const media = detailViewerVideo(videoId)
        || (!detailMediaCanvasItems().length ? document.querySelector('.asset-detail-viewer .asset-detail-player') : null);
    if(!media) return false;
    if(media.paused){
        document.querySelectorAll('.asset-detail-viewer audio.asset-detail-player,.asset-detail-viewer video.asset-detail-player').forEach(other => { if(other !== media) other.pause?.(); });
        media.play?.().catch(err => {
            // 中断型失败（用户立刻暂停/切换媒体导致的 AbortError）属预期行为，不打扰用户
            if(err?.name === 'AbortError') return;
            setStatus(err.message || '播放失败');
        });
    }
    else media.pause?.();
    return true;
}
function stopAssetHoverPreview(reset=true){
    const media = assetHoverPreviewMedia;
    if(!media) return;
    media.pause?.();
    if(reset){
        try { media.currentTime = 0; } catch(_) {}
    }
    media.closest?.('.asset-thumb')?.classList.remove('is-hover-playing');
    setAssetHoverPreviewMedia(null);
}
function startAssetHoverPreview(target){
    const thumb = target?.closest?.('.asset-grid .asset-thumb');
    const media = thumb?.querySelector?.('[data-asset-hover-preview]');
    if(!thumb || !media || media === assetHoverPreviewMedia) return;
    stopAssetHoverPreview();
    setAssetHoverPreviewMedia(media);
    if(media.tagName === 'VIDEO') media.muted = true;
    if(media.tagName === 'AUDIO') media.volume = .75;
    const playback = media.play?.();
    if(playback?.then){
        playback.then(() => thumb.classList.add('is-hover-playing')).catch(() => {
            if(assetHoverPreviewMedia === media) setAssetHoverPreviewMedia(null);
        });
    } else {
        thumb.classList.add('is-hover-playing');
    }
}
function syncDetailVideoControls(video){
    const videoId = video?.closest?.('[data-detail-media-canvas-node]')?.dataset?.detailMediaCanvasNode || '';
    const controls = videoId ? document.querySelector(`.asset-detail-viewer [data-detail-video-controls][data-detail-video-for="${CSS.escape(videoId)}"]`) : null;
    if(!video || !controls) return;
    const node = video.closest('[data-detail-media-canvas-node]');
    const current = Math.max(0, Number(video.currentTime || 0));
    const item = detailViewerVideoItem(controls.dataset.detailVideoFor || '');
    const state = detailViewerVideoState(controls.dataset.detailVideoFor || '');
    const metadataDuration = Number(video.duration || 0);
    const duration = Number.isFinite(metadataDuration) && metadataDuration > 0 ? metadataDuration : Math.max(0, Number(item.duration || item.metadata?.duration || 0));
    const progress = controls.querySelector('[data-detail-video-progress]');
    const volume = controls.querySelector('[data-detail-video-volume]');
    const playButton = controls.querySelector('[data-detail-video-action="toggle"]');
    const muteButton = controls.querySelector('[data-detail-video-action="mute"]');
    const muted = Boolean(video.muted || Number(video.volume || 0) === 0);
    if(Math.abs(Number(video.playbackRate || 1)-Number(state.rate || 1)) > .001) video.playbackRate = Number(state.rate || 1);
    controls.classList.toggle('is-playing', !video.paused && !video.ended);
    node?.classList.toggle('is-video-playing', !video.paused && !video.ended);
    controls.classList.toggle('is-muted', muted);
    if(playButton){
        playButton.title = video.paused || video.ended ? '播放（Space）' : '暂停（Space）';
        playButton.setAttribute('aria-label', playButton.title);
    }
    if(muteButton){
        muteButton.title = muted ? '取消静音' : '静音';
        muteButton.setAttribute('aria-label', muteButton.title);
        muteButton.setAttribute('aria-pressed', String(muted));
    }
    const currentLabel = controls.querySelector('[data-detail-video-current]');
    const durationLabel = controls.querySelector('[data-detail-video-duration]');
    if(currentLabel) currentLabel.textContent = formatDetailVideoTime(current);
    if(durationLabel) durationLabel.textContent = duration ? formatDetailVideoTime(duration) : '--:--';
    if(progress){
        progress.disabled = !(duration > 0);
        progress.max = String(duration || 0);
        progress.value = String(Math.min(duration || current, current));
        progress.setAttribute('aria-valuetext', `${formatDetailVideoTime(current)} / ${duration ? formatDetailVideoTime(duration) : '时长读取中'}`);
    }
    const fill = controls.querySelector('[data-detail-video-fill]');
    if(fill){
        const percent = duration > 0 ? Math.max(0, Math.min(100, (current / duration) * 100)) : 0;
        fill.style.width = `${percent}%`;
    }
    if(volume) volume.value = String(Math.max(0, Math.min(1, Number(video.volume ?? 1))));
    const inPoint = detailVideoPoint(state.inPoint);
    const outPoint = detailVideoPoint(state.outPoint);
    const rangeReady = inPoint !== null && outPoint !== null && outPoint > inPoint && (!duration || outPoint <= duration+.05);
    const inLabel = controls.querySelector('[data-detail-video-in]');
    const outLabel = controls.querySelector('[data-detail-video-out]');
    const rateLabel = controls.querySelector('[data-detail-video-rate]');
    if(inLabel) inLabel.textContent = inPoint === null ? '--:--' : formatDetailVideoTime(inPoint);
    if(outLabel) outLabel.textContent = outPoint === null ? '--:--' : formatDetailVideoTime(outPoint);
    if(rateLabel) rateLabel.textContent = `${Number(state.rate || 1).toFixed(1)}×`;
    [['in',inPoint],['out',outPoint]].forEach(([name,value]) => {
        const marker = controls.querySelector(`[data-detail-video-${name}-marker]`);
        if(!marker) return;
        marker.hidden = value === null || !(duration > 0);
        marker.style.left = `${duration > 0 && value !== null ? Math.max(0,Math.min(100,value/duration*100)) : 0}%`;
    });
    const rootReady = detailWritableStorageRoots().some(root => root.id === detailViewerState.videoExportRootId);
    controls.querySelectorAll('[data-detail-video-action="clip-file"]').forEach(button => { button.disabled = !rangeReady || Boolean(detailViewerState.videoExportBusy); });
    controls.querySelectorAll('[data-detail-video-action="clip-library"]').forEach(button => { button.disabled = !rangeReady || !rootReady || Boolean(detailViewerState.videoExportBusy); });
    controls.querySelectorAll('[data-detail-video-action="frame-file"]').forEach(button => { button.disabled = Boolean(detailViewerState.videoExportBusy); });
    controls.querySelectorAll('[data-detail-video-action="frame-library"]').forEach(button => { button.disabled = !rootReady || Boolean(detailViewerState.videoExportBusy); });
    controls.classList.toggle('is-busy',Boolean(detailViewerState.videoExportBusy));
    controls.setAttribute('aria-busy',String(Boolean(detailViewerState.videoExportBusy)));
}
function handleDetailVideoMediaEvent(event){
    const video = event.target?.matches?.('.asset-detail-viewer video.asset-detail-player') ? event.target : null;
    if(video) syncDetailVideoControls(video);
}
function detailVideoSafeStem(item={}){
    return String(item.name || '视频').replace(/\.[^.]+$/,'').replace(/[<>:"/\\|?*\x00-\x1f]/g,'_').trim().slice(0,100) || '视频';
}
async function fetchDetailVideoBlob(url,options={}){
    const response = await assetManagerApi.getMedia(url,{...options,credentials:'same-origin'});
    if(response.status === 401) window.dispatchEvent(new CustomEvent('asset-auth-needed'));
    if(!response.ok){
        const data = await response.json().catch(() => ({}));
        throw new Error(data.detail || data.message || `导出失败（${response.status}）`);
    }
    return {blob:await response.blob(),filename:registryDownloadName(response,'')};
}
async function requestDetailVideoFileDestination(filename,mime,extension){
    if(typeof window.showSaveFilePicker === 'function'){
        try {
            const handle = await window.showSaveFilePicker({suggestedName:filename,types:[{description:extension === '.mp4' ? 'MP4 视频' : 'PNG 图片',accept:{[mime]:[extension]}}]});
            return {handle};
        } catch(err){
            if(err?.name === 'AbortError') return null;
            throw err;
        }
    }
    return {download:true};
}
function mergeDetailExportedRegistryAsset(raw={}){
    const asset = normalizeRegistryAsset(raw);
    if(!asset.id) return null;
    const index = registryAssets.findIndex(item => item.id === asset.id);
    if(index >= 0) registryAssets[index] = {...registryAssets[index],...asset};
    else { registryAssets.unshift(asset); if(registryTotalKnown) setRegistryTotal(registryTotal + 1); }
    refreshDetailMediaCanvasLibraryGrid();
    return asset;
}
function setDetailVideoExportBusy(value='',videoId=''){
    detailViewerState.videoExportBusy = String(value || '');
    const video = detailViewerVideo(videoId);
    if(video) syncDetailVideoControls(video);
}
async function ensureDetailVideoAssetId(item={}){
    if(detailMediaCanvasPersistId(item)) return detailMediaCanvasPersistId(item);
    await ensureDetailReferenceAssetIds([item],{silent:true});
    const assetId = detailMediaCanvasPersistId(item);
    if(!assetId) throw new Error('当前视频尚未关联统一资产索引，无法导出');
    return assetId;
}
async function exportDetailVideoClip(videoId='',toLibrary=false){
    const video = detailViewerVideo(videoId);
    const item = detailViewerVideoItem(videoId);
    const state = detailViewerVideoState(videoId);
    const inPoint = detailVideoPoint(state.inPoint), outPoint = detailVideoPoint(state.outPoint);
    if(!video || inPoint === null || outPoint === null || outPoint <= inPoint) throw new Error('请先使用 I / O 设置有效的入点和出点');
    const suggestedName = `${detailVideoSafeStem(item)}_${inPoint.toFixed(2)}-${outPoint.toFixed(2)}.mp4`;
    const destination = toLibrary ? null : await requestDetailVideoFileDestination(suggestedName,'video/mp4','.mp4');
    if(!toLibrary && !destination){ setStatus('已取消截取视频导出'); return; }
    const assetId = await ensureDetailVideoAssetId(item);
    setDetailVideoExportBusy(toLibrary ? 'clip-library' : 'clip-file',videoId);
    setStatus(`正在截取 ${formatDetailVideoTime(inPoint)} 到 ${formatDetailVideoTime(outPoint)}…`);
    try {
        const result = await fetchDetailVideoBlob(`/api/asset-registry/assets/${encodeURIComponent(assetId)}/video/clip`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({in_seconds:inPoint,out_seconds:outPoint})});
        const filename = result.filename || suggestedName;
        if(toLibrary){
            const asset = await importDetailVideoBlobToLibrary(result.blob,filename,detailViewerState.videoExportRootId);
            setStatus(`截取视频已导入：${asset?.name || filename}`);
        } else if(await saveDetailVideoBlobToPath(result.blob,filename,destination)) setStatus(`截取视频已导出：${filename}`);
        else setStatus('已取消截取视频导出');
    } finally { setDetailVideoExportBusy('',videoId); }
}
async function exportDetailVideoFrame(videoId='',toLibrary=false){
    const video = detailViewerVideo(videoId);
    const item = detailViewerVideoItem(videoId);
    if(!video) throw new Error('当前视频不可用');
    const at = Math.max(0,Number(video.currentTime || 0));
    const suggestedName = `${detailVideoSafeStem(item)}_frame_${at.toFixed(3)}.png`;
    const destination = toLibrary ? null : await requestDetailVideoFileDestination(suggestedName,'image/png','.png');
    if(!toLibrary && !destination){ setStatus('已取消单帧导出'); return; }
    const assetId = await ensureDetailVideoAssetId(item);
    setDetailVideoExportBusy(toLibrary ? 'frame-library' : 'frame-file',videoId);
    setStatus(`正在截取 ${formatDetailVideoTime(at)} 的单帧…`);
    try {
        const result = await fetchDetailVideoBlob(`/api/asset-registry/assets/${encodeURIComponent(assetId)}/video/frame?at=${encodeURIComponent(at.toFixed(6))}`);
        const filename = result.filename || suggestedName;
        if(toLibrary){
            const asset = await importDetailVideoBlobToLibrary(result.blob,filename,detailViewerState.videoExportRootId);
            setStatus(`视频单帧已导入：${asset?.name || filename}`);
        } else if(await saveDetailVideoBlobToPath(result.blob,filename,destination)) setStatus(`视频单帧已导出：${filename}`);
        else setStatus('已取消单帧导出');
    } finally { setDetailVideoExportBusy('',videoId); }
}
function setDetailVideoRate(delta=0,videoId=''){
    const state = detailViewerVideoState(videoId);
    state.rate = Number(delta) === 0 ? 1 : Math.max(.1,Math.min(4,Math.round((Number(state.rate || 1)+Number(delta))*10)/10));
    const video = detailViewerVideo(videoId);
    if(video){ video.playbackRate = state.rate; syncDetailVideoControls(video); }
    setStatus(`播放速度 ${state.rate.toFixed(1)}×`);
}
async function handleDetailVideoControl(button){
    const controls = button?.closest?.('[data-detail-video-controls]');
    const videoId = controls?.dataset.detailVideoFor || button?.dataset?.detailVideoFor || '';
    const video = detailViewerVideo(videoId);
    if(!video) return;
    const action = button.dataset.detailVideoAction || '';
    if(action === 'toggle') toggleDetailPreviewPlayback(videoId);
    else if(action === 'previous-frame') stepDetailVideoFrame(-1,videoId);
    else if(action === 'next-frame') stepDetailVideoFrame(1,videoId);
    else if(action === 'mute'){
        video.muted = !video.muted;
        syncDetailVideoControls(video);
    } else if(action === 'set-in'){
        const state = detailViewerVideoState(videoId);
        state.inPoint = Math.max(0,Number(video.currentTime || 0));
        if(detailVideoPoint(state.outPoint) !== null && Number(state.outPoint) <= Number(state.inPoint)) state.outPoint = null;
        syncDetailVideoControls(video); setStatus(`入点 ${formatDetailVideoTime(state.inPoint)}`);
    } else if(action === 'set-out'){
        const state = detailViewerVideoState(videoId);
        state.outPoint = Math.max(0,Number(video.currentTime || 0));
        if(detailVideoPoint(state.inPoint) !== null && Number(state.inPoint) >= Number(state.outPoint)) state.inPoint = null;
        syncDetailVideoControls(video); setStatus(`出点 ${formatDetailVideoTime(state.outPoint)}`);
    } else if(action === 'clear-range'){
        const state = detailViewerVideoState(videoId); state.inPoint = null; state.outPoint = null;
        syncDetailVideoControls(video); setStatus('已清除视频入点和出点');
    } else if(action === 'rate-down') setDetailVideoRate(-.1,videoId);
    else if(action === 'rate-up') setDetailVideoRate(.1,videoId);
    else if(action === 'rate-reset') setDetailVideoRate(0,videoId);
    else if(action === 'clip-file') await exportDetailVideoClip(videoId,false);
    else if(action === 'clip-library') await exportDetailVideoClip(videoId,true);
    else if(action === 'frame-file') await exportDetailVideoFrame(videoId,false);
    else if(action === 'frame-library') await exportDetailVideoFrame(videoId,true);
    else if(action === 'frame-copy') await copyDetailVideoFrameToClipboard(videoId);
}
let detailVideoStoryboardFitHandler = null;
// 放大层外框贴合图片：按视口可用高度/宽度等比缩放，外框 = 图片 + 四周 10px 内边距 + 边框。
function fitDetailVideoStoryboardOverlay(overlay){
    const section = overlay?.querySelector('[role="dialog"]');
    const slot = overlay?.querySelector('[data-video-storyboard-large]');
    const image = slot?.querySelector('img');
    if(!section || !slot || !image) return;
    const naturalWidth = Number(image.naturalWidth) || 0;
    const naturalHeight = Number(image.naturalHeight) || 0;
    if(!naturalWidth || !naturalHeight) return;
    const overlayStyle = getComputedStyle(overlay);
    const sectionStyle = getComputedStyle(section);
    const slotStyle = getComputedStyle(slot);
    const px = value => Number.parseFloat(value) || 0;
    const borderX = px(sectionStyle.borderLeftWidth) + px(sectionStyle.borderRightWidth);
    const borderY = px(sectionStyle.borderTopWidth) + px(sectionStyle.borderBottomWidth);
    const slotPadX = px(slotStyle.paddingLeft) + px(slotStyle.paddingRight);
    const slotPadY = px(slotStyle.paddingTop) + px(slotStyle.paddingBottom);
    const frameX = px(overlayStyle.paddingLeft) + px(overlayStyle.paddingRight) + borderX + slotPadX;
    const frameY = px(overlayStyle.paddingTop) + px(overlayStyle.paddingBottom) + borderY + slotPadY + (section.querySelector('header')?.getBoundingClientRect().height || 0);
    const availableWidth = Math.max(160, window.innerWidth - frameX);
    const availableHeight = Math.max(120, window.innerHeight - frameY);
    const scale = Math.min(1, availableWidth / naturalWidth, availableHeight / naturalHeight);
    const width = Math.max(1, Math.round(naturalWidth * scale));
    const height = Math.max(1, Math.round(naturalHeight * scale));
    image.style.width = `${width}px`;
    image.style.height = `${height}px`;
    image.style.maxWidth = 'none';
    image.style.maxHeight = 'none';
    section.style.boxSizing = 'border-box';
    section.style.width = `${width + slotPadX + borderX}px`;
}
function unbindDetailVideoStoryboardFit(){
    if(!detailVideoStoryboardFitHandler) return;
    window.removeEventListener('resize', detailVideoStoryboardFitHandler);
    detailVideoStoryboardFitHandler = null;
}
function bindDetailVideoStoryboardFit(overlay, target){
    unbindDetailVideoStoryboardFit();
    const fit = () => fitDetailVideoStoryboardOverlay(overlay);
    detailVideoStoryboardFitHandler = fit;
    window.addEventListener('resize', fit);
    const image = target?.querySelector('img');
    if(!image) return;
    if(image.complete) fit();
    else image.addEventListener('load', fit, {once:true});
}
function closeDetailVideoStoryboard(){
    unbindDetailVideoStoryboardFit();
    document.querySelector('[data-video-storyboard-overlay]')?.remove();
    detailViewerState.videoStoryboardExpandedId = '';
}
async function openDetailVideoStoryboard(button){
    const assetId = String(button?.dataset?.detailVideoStoryboardOpen || '');
    if(!assetId) return;
    closeDetailVideoStoryboard();
    const overlay = document.createElement('div');
    overlay.className = 'asset-video-storyboard-overlay';
    overlay.dataset.videoStoryboardOverlay = '';
    overlay.innerHTML = `<section data-floating-content role="dialog" aria-modal="true" aria-label="视频九宫格预览"><header><div><i data-lucide="grid-3x3"></i><strong>视频九宫格预览</strong></div><button type="button" data-video-storyboard-close aria-label="关闭九宫格预览"><i data-lucide="x"></i></button></header><div class="asset-video-storyboard-slot is-large" data-video-storyboard-large><span class="asset-video-storyboard-placeholder">正在读取九宫格…</span></div></section>`;
    document.body.appendChild(overlay);
    markAssetFloatingSurface(overlay, {surface:'asset-video-storyboard', content:'[role="dialog"]', close:closeDetailVideoStoryboard});
    detailViewerState.videoStoryboardExpandedId = assetId;
    refreshIcons();
    requestAnimationFrame(() => overlay.querySelector('[data-video-storyboard-close]')?.focus());
    const target = overlay.querySelector('[data-video-storyboard-large]');
    const version = String(button?.dataset?.storyboardVersion || '');
    const key = videoStoryboardImageKey(assetId, version);
    const cached = videoStoryboardImageCache.get(key);
    if(cached){ paintVideoStoryboardImage(target, cached, VIDEO_STORYBOARD_LARGE_STYLE); bindDetailVideoStoryboardFit(overlay, target); return; }
    try{
        const data = await apiJsonResponse(assetManagerApi.getRegistryVideoStoryboard(assetId));
        videoStoryboardImageCache.set(key, data || {});
        paintVideoStoryboardImage(target, data, VIDEO_STORYBOARD_LARGE_STYLE);
        bindDetailVideoStoryboardFit(overlay, target);
    }catch(err){
        if(target) target.innerHTML = `<span class="asset-video-storyboard-placeholder is-error">九宫格读取失败：${escapeHtml(err?.message || '未知错误')}</span>`;
    }
}
function stepDetailVideoFrame(direction,videoId=''){
    const video = detailViewerVideo(videoId);
    const item = detailViewerVideoItem(videoId);
    if(!video || detailViewerType(detailViewerState.source, item) !== 'video') return false;
    if(video.readyState < 1){
        video.addEventListener('loadedmetadata', () => stepDetailVideoFrame(direction), {once:true});
        video.load?.();
        return true;
    }
    video.pause?.();
    const frameRate = Math.max(1, Number(item.frame_rate || item.metadata?.frame_rate || 30));
    const duration = Number.isFinite(video.duration) ? video.duration : Number.MAX_SAFE_INTEGER;
    video.currentTime = Math.max(0, Math.min(duration, Number(video.currentTime || 0) + Number(direction || 0) / frameRate));
    syncDetailVideoControls(video);
    setStatus(`${direction > 0 ? '下一帧' : '上一帧'} · ${video.currentTime.toFixed(3)} 秒`);
    return true;
}
async function enterDetailVideoFullscreen(){
    const video = detailViewerVideo();
    if(!video) return false;
    try {
        if(document.fullscreenElement !== video) await video.requestFullscreen?.();
        await video.play?.();
        return true;
    } catch(err){
        setStatus(err.message || '无法进入全屏播放');
        return false;
    }
}
async function toggleDetailVideoFullscreen(){
    const video = detailViewerVideo();
    if(!video) return false;
    try {
        if(document.fullscreenElement === video){
            await document.exitFullscreen?.();
            return true;
        }
        await video.requestFullscreen?.();
        await video.play?.();
        return true;
    } catch(err){
        setStatus(err.message || '无法切换视频全屏');
        return false;
    }
}
function positionDetailMediaVideoDock(){
    const viewport=document.querySelector('.asset-detail-viewer [data-detail-media-canvas-viewport]');
    const nestedDocks = viewport?.querySelectorAll?.('[data-detail-media-canvas-node] > [data-detail-video-dock]');
    if(nestedDocks?.length){
        nestedDocks.forEach(dock => { dock.classList.remove('is-positioning'); dock.style.removeProperty('transform'); dock.style.removeProperty('left'); dock.style.removeProperty('top'); dock.style.removeProperty('width'); dock.style.removeProperty('max-width'); });
        return;
    }
    const dock=viewport?.querySelector?.('[data-detail-video-dock]');
    if(!viewport || !dock) return;
    const id=String(dock.dataset.detailVideoFor || '');
    const node=id ? [...viewport.querySelectorAll('[data-detail-media-canvas-node]')].find(value => String(value.dataset.detailMediaCanvasNode || '') === id) : null;
    if(!node){
        dock.style.left='14px';
        dock.style.right='14px';
        dock.style.width='auto';
        dock.style.maxWidth='';
        dock.style.transform='';
        dock.style.top='14px';
        dock.classList.remove('is-positioning');
        return;
    }
    const edge=14;
    const box=detailViewerState.mediaCanvasLayout?.[id] || {};
    const naturalWidth=Math.max(1,Number(box.width || node.offsetWidth || 1));
    const naturalHeight=Math.max(1,Number(box.height || node.offsetHeight || 1));
    const canvasScale=Math.max(.05,Math.min(4,Number(detailViewerState.mediaCanvasScale || 1)));
    const maxWidth=Math.max(1,viewport.clientWidth-edge*2);
    const uiScale=Math.min(canvasScale,maxWidth/naturalWidth);
    const visualWidth=naturalWidth*uiScale;
    const leftWorld=Number(detailViewerState.mediaCanvasX || 0) + Number(box.x || 0) * canvasScale;
    const topWorld=Number(detailViewerState.mediaCanvasY || 0) + Number(box.y || 0) * canvasScale;
    const left=Math.max(edge,Math.min(viewport.clientWidth-edge-visualWidth,leftWorld));
    dock.style.width=`${Math.round(naturalWidth)}px`;
    dock.style.maxWidth='none';
    dock.style.transformOrigin='top left';
    dock.style.transform=`scale(${uiScale})`;
    dock.style.right='auto';
    const gap=12;
    const idealTop=topWorld + naturalHeight * canvasScale + gap;
    const visualHeight=dock.offsetHeight*uiScale;
    const maxTop=Math.max(edge,viewport.clientHeight-visualHeight-edge);
    const top=Math.max(edge,Math.min(idealTop,maxTop));
    // CSS left/top position the transformed element; only its contents are scaled.
    dock.style.left=`${Math.round(left)}px`;
    dock.style.top=`${Math.round(top)}px`;
    dock.dataset.detailVideoUiScale=String(uiScale);
    dock.classList.remove('is-positioning');
}
function scheduleDetailMediaVideoDockPosition(){
    if(detailMediaCanvasPositionRaf) return;
    detailMediaCanvasPositionRaf = requestAnimationFrame(() => {
        detailMediaCanvasPositionRaf = 0;
        positionDetailMediaVideoDock();
    });
}
function detailImageEditorLayer(raw={}){
    const type = raw.type === 'text' ? 'text' : 'brush';
    const id=String(raw.id || `${type}_${Date.now()}_${Math.random().toString(36).slice(2,7)}`);
    const common={
        id,type,name:String(raw.name || (type === 'text' ? '文字图层' : '画笔图层')),
        hidden:Boolean(raw.hidden),locked:Boolean(raw.locked),groupId:String(raw.groupId || raw.group_id || ''),
    };
    if(type === 'brush'){
        const legacyPoints=Array.isArray(raw.points) ? raw.points : [];
        const rawPaths=Array.isArray(raw.paths) ? raw.paths : (legacyPoints.length ? [{mode:'draw',color:raw.color,width:raw.width,points:legacyPoints}] : []);
        return {...common,paths:rawPaths.map(path => ({
            mode:path?.mode === 'erase' ? 'erase' : 'draw',color:path?.color || raw.color || signalFlowAccentColor(),
            width:Math.max(1,Number(path?.width || raw.width || 8)),
            points:(path?.points || []).map(point => ({x:Number(point.x || 0),y:Number(point.y || 0)})),
        }))};
    }
    return {...common,text:String(raw.text || '输入文字'),x:Number(raw.x || 0),y:Number(raw.y || 0),
        color:raw.color || '#ffffff',fontSize:Math.max(8,Number(raw.fontSize || raw.font_size || 48))};
}
function detailImageEditorSerialized(){
    const state = detailImageEditorState;
    if(!state) return null;
    return {
        version:2,
        asset_version_id:state.assetVersionId || '',
        asset_version_number:Number(state.assetVersionNumber || 0),
        source_asset_id:state.sourceAssetId || '',
        source_width:state.sourceWidth,
        source_height:state.sourceHeight,
        crop:{...state.crop},
        flip_x:Boolean(state.flipX),
        flip_y:Boolean(state.flipY),
        groups:(state.groups || []).map(group => ({id:group.id,name:group.name,hidden:Boolean(group.hidden),locked:Boolean(group.locked)})),
        layers:state.layers.map(layer => layer.type === 'brush' ? {
            id:layer.id,type:'brush',name:layer.name,hidden:Boolean(layer.hidden),locked:Boolean(layer.locked),group_id:layer.groupId || '',
            paths:(layer.paths || []).map(path => ({mode:path.mode === 'erase' ? 'erase' : 'draw',color:path.color,width:path.width,
                points:path.points.slice(0,100000).map(point => ({x:point.x,y:point.y}))})),
        } : {
            id:layer.id,type:'text',name:layer.name,hidden:Boolean(layer.hidden),locked:Boolean(layer.locked),group_id:layer.groupId || '',
            text:layer.text,x:layer.x,y:layer.y,color:layer.color,font_size:layer.fontSize,
        }),
    };
}
function loadDetailImageEditorImage(url){
    return new Promise((resolve,reject) => {
        const image = new Image();
        image.decoding = 'async';
        image.onload = () => resolve(image);
        image.onerror = () => reject(new Error('图片加载失败，无法进入编辑模式'));
        image.src = url;
    });
}
async function openDetailImageEditor(itemId, options={}){
    const suppliedItem = options?.item || null;
    const returnToCoverflow = options?.returnToCoverflow || null;
    const requestedItemId = String(suppliedItem?.id || itemId || '').trim();
    if(detailImageEditorOpeningPromise && detailImageEditorOpeningItemId === requestedItemId) return detailImageEditorOpeningPromise;
    const opening = openDetailImageEditorNow(requestedItemId, suppliedItem, returnToCoverflow);
    detailImageEditorOpeningPromise = opening;
    detailImageEditorOpeningItemId = requestedItemId;
    try {
        return await opening;
    } finally {
        if(detailImageEditorOpeningPromise === opening){
            detailImageEditorOpeningPromise = null;
            detailImageEditorOpeningItemId = '';
        }
    }
}
async function openDetailImageEditorNow(itemId, suppliedItem=null, returnToCoverflow=null){
    const item = suppliedItem
        || detailMediaCanvasItems().find(value => value.id === itemId)
        || detailViewerItem(detailViewerState.source, itemId);
    if(!item || detailViewerType(detailViewerState.source,item) !== 'image') return;
    const currentAssetId = detailMediaCanvasPersistId(item);
    let stored = item.reference_edit && typeof item.reference_edit === 'object' ? item.reference_edit : {};
    const explicitVersion=String(item.image_version_selection || '');
    if(currentAssetId && !explicitVersion && !Object.keys(stored).length){
        const versionData=await apiJsonResponse(assetManagerApi.getRegistryImageVersions(currentAssetId,1)).catch(()=>null);
        const latest=versionData?.versions?.[0];
        if(latest?.edit) stored=latest.edit;
    }
    const sourceAssetId = String(stored.source_asset_id || currentAssetId || '');
    const sourceUrl = sourceAssetId ? `/api/asset-registry/assets/${encodeURIComponent(sourceAssetId)}/media` : String(stored.source_url || detailViewerUrl(detailViewerState.source,item) || '');
    setStatus('正在打开单图影院编辑器…');
    const sourceImage = await loadDetailImageEditorImage(sourceUrl);
    const sourceWidth = Math.max(1,Number(stored.source_width || sourceImage.naturalWidth || 1));
    const sourceHeight = Math.max(1,Number(stored.source_height || sourceImage.naturalHeight || 1));
    const rawCrop = stored.crop && typeof stored.crop === 'object' ? stored.crop : {};
    const crop = {
        x:Math.max(0,Math.min(sourceWidth-1,Number(rawCrop.x || 0))),
        y:Math.max(0,Math.min(sourceHeight-1,Number(rawCrop.y || 0))),
        w:Math.max(1,Math.min(sourceWidth,Number(rawCrop.w || sourceWidth))),
        h:Math.max(1,Math.min(sourceHeight,Number(rawCrop.h || sourceHeight))),
    };
    crop.w = Math.min(crop.w,sourceWidth-crop.x);
    crop.h = Math.min(crop.h,sourceHeight-crop.y);
    setDetailImageEditorState({
        itemId:item.id, name:item.name || '图片', sourceImage, sourceAssetId, sourceUrl,
        sourceWidth, sourceHeight, crop, flipX:Boolean(stored.flip_x), flipY:Boolean(stored.flip_y),
        layers:Array.isArray(stored.layers) ? stored.layers.map(detailImageEditorLayer) : [],
        groups:Array.isArray(stored.groups) ? stored.groups.map(group => ({id:String(group.id || ''),name:String(group.name || '图层组'),hidden:Boolean(group.hidden),locked:Boolean(group.locked)})).filter(group => group.id) : [],
        selectedLayerIds:new Set(),selectionAnchorId:'',activeBrushLayerId:'',tool:'select',brushColor:signalFlowAccentColor(),brushWidth:8,
        cropDraft:null,cropRatio:'free',zoom:1,panX:0,panY:0,eraserCursor:null,inlineTextId:'',lastTextClickId:'',lastTextClickAt:0,dirty:false,saving:false,pendingCanvasSave:false,
        assetVersionId:String(stored.asset_version_id || ''),assetVersionNumber:Number(stored.asset_version_number || 0),
        coverflowAssetId:item.id,
        returnToCoverflow,
    });
    renderDetailImageEditor();
    document.body.classList.add('asset-image-editor-open');
    setStatus(`已进入单图编辑模式：${item.name || '图片'}`);
}
function detailImageEditorSelectedLayers(){
    const state=detailImageEditorState;
    return state ? state.layers.filter(layer=>state.selectedLayerIds?.has(layer.id)) : [];
}
function detailImageEditorGroup(layer){ return detailImageEditorState?.groups?.find(group=>group.id === layer?.groupId) || null; }
function detailImageEditorLayerUnavailable(layer){
    const group=detailImageEditorGroup(layer);
    return Boolean(layer?.hidden || layer?.locked || group?.hidden || group?.locked);
}
function detailImageEditorTextBounds(layer,context){
    const lines=String(layer?.text || '').split('\n');
    context.save();
    context.font=`600 ${layer.fontSize}px "Microsoft YaHei",sans-serif`;
    const width=Math.max(layer.fontSize*.8,...lines.map(line=>context.measureText(line || ' ').width));
    context.restore();
    return {x:layer.x,y:layer.y,w:width,h:Math.max(layer.fontSize*1.22,lines.length*layer.fontSize*1.22)};
}
function detailImageEditorHitText(point,context){
    const state=detailImageEditorState;
    return [...state.layers].reverse().find(layer=>{
        if(layer.type !== 'text' || detailImageEditorLayerUnavailable(layer)) return false;
        const box=detailImageEditorTextBounds(layer,context);
        return point.x>=box.x && point.x<=box.x+box.w && point.y>=box.y && point.y<=box.y+box.h;
    }) || null;
}
function detailImageEditorWorldPixels(canvas,pixels=1){
    const state=detailImageEditorState;
    const rect=canvas?.getBoundingClientRect?.();
    return state && rect?.width ? Math.max(.01,Number(pixels || 1)*state.crop.w/rect.width) : Number(pixels || 1);
}
function applyDetailImageEditorZoom(){
    const state=detailImageEditorState;
    const canvas=document.querySelector('[data-image-editor-canvas]');
    if(!state || !canvas) return;
    canvas.style.transform=`translate(${Number(state.panX || 0)}px,${Number(state.panY || 0)}px) scale(${state.zoom})`;
    canvas.style.transformOrigin='center center';
    const output=document.querySelector('[data-image-editor-zoom-value]');
    if(output) output.textContent=`${Math.round(state.zoom*100)}%`;
}
function positionDetailImageEditorEraserCursor(event=null){
    const state=detailImageEditorState;
    const cursor=document.querySelector('[data-image-editor-eraser-cursor]');
    const canvas=document.querySelector('[data-image-editor-canvas]');
    const wrap=canvas?.closest('.asset-image-editor-canvas-wrap');
    if(!state || !cursor || !canvas || !wrap) return;
    if(event) state.eraserCursor={clientX:event.clientX,clientY:event.clientY};
    const point=state.eraserCursor;
    const canvasRect=canvas.getBoundingClientRect(),wrapRect=wrap.getBoundingClientRect();
    const inside=point && point.clientX>=canvasRect.left && point.clientX<=canvasRect.right && point.clientY>=canvasRect.top && point.clientY<=canvasRect.bottom;
    if(state.tool !== 'eraser' || !inside){ cursor.hidden=true; return; }
    const diameter=Math.max(12,Number(state.brushWidth || 1)*canvasRect.width/Math.max(1,state.crop.w));
    cursor.hidden=false;
    cursor.style.width=`${diameter}px`; cursor.style.height=`${diameter}px`;
    cursor.style.left=`${point.clientX-wrapRect.left}px`; cursor.style.top=`${point.clientY-wrapRect.top}px`;
}
function renderDetailImageEditorLayerRow(layer,index){
    const state=detailImageEditorState;
    const selected=state.selectedLayerIds?.has(layer.id);
    const group=detailImageEditorGroup(layer);
    const pathCount=(layer.paths || []).length;
    return `<article class="${selected ? 'is-selected' : ''} ${layer.hidden || group?.hidden ? 'is-hidden' : ''} ${layer.locked || group?.locked ? 'is-locked' : ''}" data-image-editor-layer="${escapeAttr(layer.id)}">
        <button type="button" data-image-editor-layer-select="${escapeAttr(layer.id)}"><i data-lucide="${layer.type === 'text' ? 'type' : 'paintbrush'}"></i><span><strong>${escapeHtml(layer.name || (layer.type === 'text' ? layer.text : `画笔图层 ${index+1}`))}</strong><small>${layer.type === 'text' ? '文字图层' : `${pathCount} 条笔迹`}${group ? ` · ${escapeHtml(group.name)}` : ''}</small></span></button>
        <button type="button" data-image-editor-layer-visible="${escapeAttr(layer.id)}" title="${layer.hidden ? '显示图层' : '隐藏图层'}" aria-label="${layer.hidden ? '显示图层' : '隐藏图层'}"><i data-lucide="${layer.hidden ? 'eye-off' : 'eye'}"></i></button>
        <button type="button" data-image-editor-layer-lock="${escapeAttr(layer.id)}" title="${layer.locked ? '解锁图层' : '锁定图层'}" aria-label="${layer.locked ? '解锁图层' : '锁定图层'}"><i data-lucide="${layer.locked ? 'lock-keyhole' : 'lock-keyhole-open'}"></i></button>
        <button type="button" data-image-editor-layer-delete="${escapeAttr(layer.id)}" title="删除图层" aria-label="删除图层"><i data-lucide="trash-2"></i></button>
    </article>`;
}
function renderDetailImageEditorLayerList(){
    const state=detailImageEditorState;
    const groupedIds=new Set();
    const groups=(state.groups || []).map(group=>{
        const members=state.layers.filter(layer=>layer.groupId === group.id);
        members.forEach(layer=>groupedIds.add(layer.id));
        if(!members.length) return '';
        return `<section class="asset-image-editor-layer-group ${group.hidden ? 'is-hidden' : ''} ${group.locked ? 'is-locked' : ''}" data-image-editor-group="${escapeAttr(group.id)}"><header><button type="button" data-image-editor-group-select="${escapeAttr(group.id)}"><i data-lucide="layers-3"></i><span>${escapeHtml(group.name)}</span><small>${members.length}</small></button><button type="button" data-image-editor-group-visible="${escapeAttr(group.id)}" title="${group.hidden ? '显示组' : '隐藏组'}"><i data-lucide="${group.hidden ? 'eye-off' : 'eye'}"></i></button><button type="button" data-image-editor-group-lock="${escapeAttr(group.id)}" title="${group.locked ? '解锁组' : '锁定组'}"><i data-lucide="${group.locked ? 'lock-keyhole' : 'lock-keyhole-open'}"></i></button></header><div>${members.map((layer,index)=>renderDetailImageEditorLayerRow(layer,index)).join('')}</div></section>`;
    }).join('');
    const ungrouped=state.layers.filter(layer=>!groupedIds.has(layer.id)).map((layer,index)=>renderDetailImageEditorLayerRow(layer,index)).join('');
    return groups+ungrouped || '<p>按 Shift+N 新建画笔图层，或使用画笔、文字工具创建图层。</p>';
}
function positionDetailImageEditorInlineText(){
    const state=detailImageEditorState;
    const input=document.querySelector('[data-image-editor-inline-text]');
    const canvas=document.querySelector('[data-image-editor-canvas]');
    const wrap=canvas?.closest('.asset-image-editor-canvas-wrap');
    const layer=state?.layers.find(value=>value.id === state.inlineTextId && value.type === 'text');
    if(!input || !canvas || !wrap || !layer) return;
    const canvasRect=canvas.getBoundingClientRect(),wrapRect=wrap.getBoundingClientRect();
    const scaleX=canvasRect.width/Math.max(1,state.crop.w),scaleY=canvasRect.height/Math.max(1,state.crop.h);
    input.style.left=`${canvasRect.left-wrapRect.left+layer.x*scaleX}px`;
    input.style.top=`${canvasRect.top-wrapRect.top+layer.y*scaleY}px`;
    input.style.width=`${Math.max(120,Math.min(canvasRect.width-layer.x*scaleX,Math.max(layer.fontSize*3,String(layer.text||'').length*layer.fontSize*.75)*scaleX))}px`;
    input.style.minHeight=`${Math.max(34,layer.fontSize*1.35*scaleY)}px`;
    input.style.fontSize=`${Math.max(12,layer.fontSize*scaleY)}px`;
    input.style.color=layer.color;
    requestAnimationFrame(()=>{ input.focus(); if(state.inlineTextSelectAll){ input.select(); state.inlineTextSelectAll=false; } });
}
// 编辑器每次重绘时迁移按钮；保留原 data 属性、原生 disabled 和委托事件。
function applyDetailImageEditorButtons(overlay){
    const state=detailImageEditorState;
    overlay.querySelectorAll('button').forEach(button=>{
        const label=button.getAttribute('aria-label') || button.title || button.textContent.trim();
        if(label) button.setAttribute('aria-label',label);
        const danger=button.matches('[data-image-editor-discard],[data-image-editor-layer-delete],[data-image-editor-layer-delete-selected]');
        const iconOnly=!button.textContent.trim();
        button.classList.remove('primary','is-active');
        if(iconOnly){
            button.classList.add('hw-round-btn');
            if(danger) button.classList.add('hw-btn-danger');
            const inner=document.createElement('span');
            inner.className='hw-round-inner';
            inner.append(...button.childNodes);
            button.appendChild(inner);
        }else{
            button.classList.add('hw-btn');
            if(danger) button.classList.add('hw-btn-danger');
            if(button.matches('[data-image-editor-close],[data-image-editor-apply-crop]')) button.classList.add('hw-btn-gold');
        }
        if(button.hasAttribute('data-image-editor-tool')) button.setAttribute('aria-pressed',String(state.tool===button.dataset.imageEditorTool));
        if(button.hasAttribute('data-image-editor-layer-select')) button.setAttribute('aria-pressed',String(Boolean(state.selectedLayerIds?.has(button.dataset.imageEditorLayerSelect))));
        if(button.hasAttribute('data-image-editor-group-select')){
            const members=state.layers.filter(layer=>layer.groupId===button.dataset.imageEditorGroupSelect);
            button.setAttribute('aria-pressed',String(members.length>0 && members.every(layer=>state.selectedLayerIds?.has(layer.id))));
        }
    });
}
function renderDetailImageEditor(){
    const state = detailImageEditorState;
    if(!state) return;
    let overlay = document.querySelector('[data-image-editor-overlay]');
    if(!overlay){
        overlay = document.createElement('div');
        overlay.className = 'asset-image-editor';
        overlay.dataset.imageEditorOverlay = '';
        document.documentElement.appendChild(overlay);
    }
    const selectedLayers=detailImageEditorSelectedLayers();
    const selectedText=selectedLayers.find(layer=>layer.type === 'text');
    const activeColor=selectedText?.color || state.brushColor;
    overlay.innerHTML = `<section class="asset-image-editor-shell" data-floating-content role="dialog" aria-modal="true" aria-label="单图影院编辑器">
        <header><div class="asset-image-editor-title"><i data-lucide="clapperboard"></i><span><strong>${escapeHtml(state.name)}</strong><small>非破坏性编辑 · 点击完成保存为当前素材的新版本</small></span></div><div class="asset-image-editor-save-actions"><button type="button" data-image-editor-discard><i data-lucide="undo-2"></i>放弃修改</button><button type="button" data-image-editor-save-as><i data-lucide="download"></i>另存为</button><button type="button" data-image-editor-save-library ${state.saving ? 'disabled' : ''}><i data-lucide="library"></i>保存到当前库</button><button class="primary" type="button" data-image-editor-close ${state.saving ? 'disabled' : ''}><i data-lucide="check"></i>完成</button></div></header>
        <div class="asset-image-editor-toolbar">
            <div class="asset-image-editor-toolset">
                <button class="${state.tool === 'select' ? 'is-active' : ''}" type="button" data-image-editor-tool="select" title="选择 Q"><i data-lucide="mouse-pointer-2"></i><span>选择</span><kbd>Q</kbd></button>
                <button class="${state.tool === 'brush' ? 'is-active' : ''}" type="button" data-image-editor-tool="brush" title="画笔 B"><i data-lucide="paintbrush"></i><span>画笔</span><kbd>B</kbd></button>
                <div class="asset-image-editor-brush-controls"><label title="画笔或选中文字颜色"><input type="color" data-image-editor-brush-color value="${escapeAttr(activeColor)}"><span>颜色</span></label><label title="画笔粗细，画笔模式可用 + / - 调整"><input type="range" min="1" max="300" value="${state.brushWidth}" data-image-editor-brush-width><output>${state.brushWidth}</output></label></div>
                <button class="${state.tool === 'eraser' ? 'is-active' : ''}" type="button" data-image-editor-tool="eraser" title="橡皮擦 E"><i data-lucide="eraser"></i><span>橡皮擦</span><kbd>E</kbd></button>
                <span class="asset-image-editor-tool-divider"></span>
                <button class="${state.tool === 'crop' ? 'is-active' : ''}" type="button" data-image-editor-tool="crop" title="裁切 C"><i data-lucide="crop"></i><span>裁切</span><kbd>C</kbd></button>
                <label class="asset-image-editor-crop-select"><select data-image-editor-crop-ratio aria-label="裁切比例"><option value="free" ${state.cropRatio==='free'?'selected':''}>自由</option><option value="1:1" ${state.cropRatio==='1:1'?'selected':''}>1:1</option><option value="4:3" ${state.cropRatio==='4:3'?'selected':''}>4:3</option><option value="3:2" ${state.cropRatio==='3:2'?'selected':''}>3:2</option><option value="16:9" ${state.cropRatio==='16:9'?'selected':''}>16:9</option><option value="9:16" ${state.cropRatio==='9:16'?'selected':''}>9:16</option></select></label>
                ${state.tool === 'crop' ? '<button class="primary" type="button" data-image-editor-apply-crop><i data-lucide="check"></i><span>完成裁切</span><kbd>Enter</kbd></button>' : ''}
                <button type="button" data-image-editor-add-text title="文字 T"><i data-lucide="type"></i><span>文字</span><kbd>T</kbd></button>
                <button type="button" data-image-editor-flip="x"><i data-lucide="flip-horizontal-2"></i><span>左右镜像</span></button>
                <button type="button" data-image-editor-flip="y"><i data-lucide="flip-vertical-2"></i><span>上下镜像</span></button>
            </div>
            <div class="asset-image-editor-options"><span data-image-editor-zoom-value>${Math.round(state.zoom*100)}%</span><small>滚轮缩放</small></div>
        </div>
        <div class="asset-image-editor-body">
            <main class="asset-image-editor-stage is-${state.tool}"><div class="asset-image-editor-canvas-wrap"><canvas data-image-editor-canvas aria-label="图片编辑画布"></canvas><span class="asset-image-editor-eraser-cursor" data-image-editor-eraser-cursor hidden aria-hidden="true"></span>${state.inlineTextId ? `<textarea data-image-editor-inline-text maxlength="2000">${escapeHtml(state.layers.find(layer=>layer.id===state.inlineTextId)?.text || '')}</textarea>` : ''}</div><small>${Math.round(state.crop.w)} × ${Math.round(state.crop.h)} px · 版本 ${state.assetVersionNumber || '未保存'}${state.dirty || state.pendingCanvasSave ? ' · 有待保存修改' : ''}</small></main>
            <aside class="asset-image-editor-layers"><header><strong>图层</strong><span>${state.layers.length}</span></header>
                <div class="asset-image-editor-layer-actions"><button type="button" data-image-editor-layer-new title="新建图层 Shift+N"><i data-lucide="plus"></i>新建图层</button><button type="button" data-image-editor-layer-group title="图层打组 Ctrl+G" ${selectedLayers.length<2?'disabled':''}><i data-lucide="group"></i>打组</button><button type="button" data-image-editor-layer-ungroup title="解组 Shift+G"><i data-lucide="ungroup"></i>解组</button><button type="button" data-image-editor-layer-delete-selected title="删除所选图层" ${selectedLayers.length?'':'disabled'}><i data-lucide="trash-2"></i></button></div>
                <div class="asset-image-editor-layer-list">${renderDetailImageEditorLayerList()}</div>
            </aside>
        </div>
        <footer><span><i data-lucide="keyboard"></i>Q 选择 · B 画笔 · E 橡皮擦 · C 裁切 · T 文字 · +/- 调整画笔/橡皮大小 · 中键拖动画布 · 滚轮缩放</span><strong>${state.saving ? '正在保存版本…' : '完成或关闭会保存新版本，原素材文件不会改变'}</strong></footer>
    </section>`;
    applyDetailImageEditorButtons(overlay);
    markAssetFloatingSurface(overlay, {surface:'asset-image-editor', content:'.asset-image-editor-shell', close:requestCloseDetailImageEditor});
    drawDetailImageEditorCanvas();
    refreshIcons();
    positionDetailImageEditorInlineText();
    mountDetailImageEditorCoverflow(state, overlay.querySelector('.asset-image-editor-shell'));
}
function newDetailImageEditorBrushLayer(select=true){
    const state=detailImageEditorState;
    if(!state) return null;
    const count=state.layers.filter(layer=>layer.type === 'brush').length+1;
    const layer=detailImageEditorLayer({type:'brush',name:`画笔图层 ${count}`,paths:[]});
    state.layers.push(layer);
    if(select){ state.selectedLayerIds=new Set([layer.id]); state.selectionAnchorId=layer.id; }
    state.activeBrushLayerId=layer.id; state.dirty=true;
    return layer;
}
function ensureDetailImageEditorBrushLayer(create=true){
    const state=detailImageEditorState;
    if(!state) return null;
    let layer=state.layers.find(value=>value.id === state.activeBrushLayerId && value.type === 'brush' && !detailImageEditorLayerUnavailable(value));
    layer=layer || detailImageEditorSelectedLayers().find(value=>value.type === 'brush' && !detailImageEditorLayerUnavailable(value));
    layer=layer || [...state.layers].reverse().find(value=>value.type === 'brush' && !detailImageEditorLayerUnavailable(value));
    if(!layer && create) layer=newDetailImageEditorBrushLayer(true);
    if(layer){ state.activeBrushLayerId=layer.id; state.selectedLayerIds=new Set([layer.id]); state.selectionAnchorId=layer.id; }
    return layer;
}
function setDetailImageEditorTool(tool){
    const state=detailImageEditorState;
    if(!state) return;
    const next=['select','brush','eraser','crop'].includes(tool) ? tool : 'select';
    if(state.tool === 'brush' && next !== 'brush') state.activeBrushLayerId='';
    state.tool=next; state.inlineTextId='';
    if(next === 'brush'){
        const selectedBrush=detailImageEditorSelectedLayers().find(layer=>layer.type === 'brush' && !detailImageEditorLayerUnavailable(layer));
        if(selectedBrush) state.activeBrushLayerId=selectedBrush.id;
        else newDetailImageEditorBrushLayer(true);
    } else if(next === 'eraser') ensureDetailImageEditorBrushLayer(true);
    else if(next === 'crop') detailImageEditorCenteredCrop(state.cropRatio || 'free');
    renderDetailImageEditor();
}
function selectDetailImageEditorLayer(layerId,event={}){
    const state=detailImageEditorState;
    if(!state || !state.layers.some(layer=>layer.id===layerId)) return;
    const ids=state.layers.map(layer=>layer.id);
    if(event.shiftKey && state.selectionAnchorId){
        const start=ids.indexOf(state.selectionAnchorId),end=ids.indexOf(layerId);
        if(start>=0 && end>=0) state.selectedLayerIds=new Set(ids.slice(Math.min(start,end),Math.max(start,end)+1));
    } else if(event.ctrlKey || event.metaKey){
        const next=new Set(state.selectedLayerIds || []);
        if(next.has(layerId)) next.delete(layerId); else next.add(layerId);
        state.selectedLayerIds=next; state.selectionAnchorId=layerId;
    } else { state.selectedLayerIds=new Set([layerId]); state.selectionAnchorId=layerId; }
    const layer=state.layers.find(value=>value.id===layerId);
    if(layer?.type === 'brush') state.activeBrushLayerId=layer.id;
    renderDetailImageEditor();
}
function syncDetailImageEditorLayerSelectionDom(){
    const state=detailImageEditorState;
    if(!state) return;
    document.querySelectorAll('[data-image-editor-layer]').forEach(row=>row.classList.toggle('is-selected',state.selectedLayerIds.has(row.dataset.imageEditorLayer || '')));
}
function deleteDetailImageEditorLayers(ids=detailImageEditorState?.selectedLayerIds){
    const state=detailImageEditorState;
    const selected=new Set(ids || []);
    if(!state || !selected.size) return;
    const deletable=new Set(state.layers.filter(layer=>selected.has(layer.id) && !layer.locked && !detailImageEditorGroup(layer)?.locked).map(layer=>layer.id));
    state.layers=state.layers.filter(layer=>!deletable.has(layer.id));
    state.selectedLayerIds=new Set(); state.selectionAnchorId='';
    if(deletable.has(state.activeBrushLayerId)) state.activeBrushLayerId='';
    state.groups=(state.groups || []).filter(group=>state.layers.some(layer=>layer.groupId===group.id));
    if(deletable.size) state.dirty=true;
    renderDetailImageEditor();
}
function groupDetailImageEditorLayers(){
    const state=detailImageEditorState;
    const layers=detailImageEditorSelectedLayers();
    if(!state || layers.length<2) return;
    const id=`layer_group_${Date.now()}_${Math.random().toString(36).slice(2,6)}`;
    state.groups.push({id,name:`图层组 ${state.groups.length+1}`,hidden:false,locked:false});
    layers.forEach(layer=>layer.groupId=id);
    state.dirty=true; renderDetailImageEditor();
}
function ungroupDetailImageEditorLayers(){
    const state=detailImageEditorState;
    if(!state) return;
    const groupIds=new Set(detailImageEditorSelectedLayers().map(layer=>layer.groupId).filter(Boolean));
    if(!groupIds.size) return;
    state.layers.forEach(layer=>{ if(groupIds.has(layer.groupId)) layer.groupId=''; });
    state.groups=state.groups.filter(group=>!groupIds.has(group.id));
    state.dirty=true; renderDetailImageEditor();
}
function beginDetailImageEditorInlineText(layerId,selectAll=false){
    const state=detailImageEditorState;
    const layer=state?.layers.find(value=>value.id===layerId && value.type==='text' && !detailImageEditorLayerUnavailable(value));
    if(!layer) return;
    state.tool='select'; state.inlineTextId=layer.id; state.inlineTextSelectAll=selectAll;
    state.selectedLayerIds=new Set([layer.id]); state.selectionAnchorId=layer.id;
    renderDetailImageEditor();
}
function detailImageEditorCenteredCrop(ratio){
    const state = detailImageEditorState;
    if(!state) return;
    state.tool='crop'; state.cropRatio=ratio || 'free';
    if(state.cropRatio === 'free'){
        state.cropDraft={x:0,y:0,w:state.crop.w,h:state.crop.h};
        return;
    }
    const [rw,rh] = ratio.split(':').map(Number);
    const target = rw/rh;
    let width = state.crop.w, height = width/target;
    if(height > state.crop.h){ height=state.crop.h; width=height*target; }
    state.cropDraft={x:(state.crop.w-width)/2,y:(state.crop.h-height)/2,w:width,h:height};
}
function applyDetailImageEditorCrop(rect){
    const state = detailImageEditorState;
    if(!state || rect.w < 2 || rect.h < 2) return;
    const old = {...state.crop};
    const sourceX = old.x + (state.flipX ? old.w-rect.x-rect.w : rect.x);
    const sourceY = old.y + (state.flipY ? old.h-rect.y-rect.h : rect.y);
    state.crop = {x:sourceX,y:sourceY,w:rect.w,h:rect.h};
    state.layers = state.layers.map(layer => layer.type === 'brush' ? {
        ...layer,paths:(layer.paths || []).map(path=>({...path,points:path.points.map(point => ({x:point.x-rect.x,y:point.y-rect.y}))})),
    } : {...layer,x:layer.x-rect.x,y:layer.y-rect.y}).filter(layer => layer.type === 'text'
        ? layer.x < rect.w && layer.y < rect.h && layer.x+layer.fontSize > 0 && layer.y+layer.fontSize > 0
        : layer.paths.some(path=>path.points.some(point => point.x >= 0 && point.y >= 0 && point.x <= rect.w && point.y <= rect.h)));
    state.cropDraft = null;
    state.dirty = true;
    state.tool = 'select';
    renderDetailImageEditor();
}
function addDetailImageEditorText(){
    const state = detailImageEditorState;
    if(!state) return;
    const fontSize=Math.max(24,Math.min(96,state.crop.w/12));
    const layer = detailImageEditorLayer({type:'text',name:`文字图层 ${state.layers.filter(value=>value.type==='text').length+1}`,text:'输入文字',x:state.crop.w*.5-fontSize*2,y:state.crop.h*.5-fontSize*.6,fontSize,color:state.brushColor});
    state.layers.push(layer);
    state.selectedLayerIds = new Set([layer.id]); state.selectionAnchorId=layer.id;
    state.tool = 'select';
    state.dirty = true;
    beginDetailImageEditorInlineText(layer.id,true);
}
async function detailImageEditorBlob(){
    drawDetailImageEditorCanvas(false);
    const canvas = document.querySelector('[data-image-editor-canvas]');
    if(!canvas) throw new Error('编辑画布不可用');
    const blob = await new Promise((resolve,reject) => canvas.toBlob(value => value ? resolve(value) : reject(new Error('编辑图片生成失败')),'image/png'));
    drawDetailImageEditorCanvas(true);
    return blob;
}
function discardDetailImageEditor(){
    const state = detailImageEditorState;
    const restoreReferenceCanvas = detailViewerState.open && detailViewerState.referenceCanvasMode;
    document.querySelector('[data-image-editor-overlay]')?.remove();
    setDetailImageEditorState(null);
    detailImageEditorPointer = null;
    document.body.classList.remove('asset-image-editor-open');
    if(restoreReferenceCanvas) renderDetailViewer();
    restoreDetailImageEditorCoverflow(state);
    setStatus('已放弃本次单图编辑修改');
}
async function closeDetailImageEditor({save=true}={}){
    const state = detailImageEditorState;
    if(!state || state.saving) return;
    state.saving=true; renderDetailImageEditor();
    try {
        if(save && state.dirty) await saveDetailImageEditorVersion();
        document.querySelector('[data-image-editor-overlay]')?.remove();
        setDetailImageEditorState(null);
        detailImageEditorPointer = null;
        document.body.classList.remove('asset-image-editor-open');
        renderDetailViewer();
        restoreDetailImageEditorCoverflow(state);
        if(save){
            setStatus(`已保存 ${state.name} 的最新版本 V${state.assetVersionNumber || 1}，原素材未修改${state.pendingCanvasSave ? '；如需固定到参考画布，请点击更新参考画布' : ''}`);
        } else {
            setStatus(`已退出 ${state.name} 的单图编辑，未保存本次修改`);
        }
    } finally {
        state.saving=false;
        if(detailImageEditorState===state) renderDetailImageEditor();
    }
}
async function requestCloseDetailImageEditor(reason='关闭编辑器'){
    const state = detailImageEditorState;
    if(!state || state.saving) return;
    if(!state.dirty){
        await closeDetailImageEditor({save:false});
        return;
    }
    const save = window.confirm(`当前图片有未保存修改。点击“确定”保存后${reason}，点击“取消”直接放弃修改并退出。`);
    if(save) await closeDetailImageEditor({save:true});
    else discardDetailImageEditor();
}
async function confirmDetailImageEditorTransition(reason='切换素材'){
    const state = detailImageEditorState;
    if(!state || !state.dirty) return true;
    const save = window.confirm(`当前图片有未保存修改。点击“确定”保存后${reason}，点击“取消”放弃修改并继续。`);
    if(save) await saveDetailImageEditorVersion();
    else discardDetailImageEditor();
    return true;
}
async function handleDetailImageEditorClick(event){
    const state = detailImageEditorState;
    if(!state || !event.target.closest?.('[data-image-editor-overlay]')) return false;
    const target = event.target;
    event.stopPropagation();
    const tool = target.closest?.('[data-image-editor-tool]');
    if(tool){ setDetailImageEditorTool(tool.dataset.imageEditorTool || 'select'); return true; }
    const flip = target.closest?.('[data-image-editor-flip]');
    if(flip){ if(flip.dataset.imageEditorFlip === 'x') state.flipX=!state.flipX; else state.flipY=!state.flipY; state.dirty=true; renderDetailImageEditor(); return true; }
    if(target.closest?.('[data-image-editor-add-text]')){ addDetailImageEditorText(); return true; }
    if(target.closest?.('[data-image-editor-apply-crop]')){ if(state.cropDraft) applyDetailImageEditorCrop(state.cropDraft); return true; }
    const select = target.closest?.('[data-image-editor-layer-select]');
    if(select){ selectDetailImageEditorLayer(select.dataset.imageEditorLayerSelect || '',event); return true; }
    const groupSelect=target.closest?.('[data-image-editor-group-select]');
    if(groupSelect){ const id=groupSelect.dataset.imageEditorGroupSelect||''; state.selectedLayerIds=new Set(state.layers.filter(layer=>layer.groupId===id).map(layer=>layer.id)); state.selectionAnchorId=[...state.selectedLayerIds][0]||''; renderDetailImageEditor(); return true; }
    const visible=target.closest?.('[data-image-editor-layer-visible]');
    if(visible){ const layer=state.layers.find(value=>value.id===visible.dataset.imageEditorLayerVisible); if(layer){layer.hidden=!layer.hidden;state.dirty=true;renderDetailImageEditor();} return true; }
    const lock=target.closest?.('[data-image-editor-layer-lock]');
    if(lock){ const layer=state.layers.find(value=>value.id===lock.dataset.imageEditorLayerLock); if(layer){layer.locked=!layer.locked;state.dirty=true;renderDetailImageEditor();} return true; }
    const groupVisible=target.closest?.('[data-image-editor-group-visible]');
    if(groupVisible){ const group=state.groups.find(value=>value.id===groupVisible.dataset.imageEditorGroupVisible); if(group){group.hidden=!group.hidden;state.dirty=true;renderDetailImageEditor();} return true; }
    const groupLock=target.closest?.('[data-image-editor-group-lock]');
    if(groupLock){ const group=state.groups.find(value=>value.id===groupLock.dataset.imageEditorGroupLock); if(group){group.locked=!group.locked;state.dirty=true;renderDetailImageEditor();} return true; }
    const remove = target.closest?.('[data-image-editor-layer-delete]');
    if(remove){ deleteDetailImageEditorLayers(new Set([remove.dataset.imageEditorLayerDelete])); return true; }
    if(target.closest?.('[data-image-editor-layer-delete-selected]')){ deleteDetailImageEditorLayers(); return true; }
    if(target.closest?.('[data-image-editor-layer-new]')){ newDetailImageEditorBrushLayer(true); renderDetailImageEditor(); return true; }
    if(target.closest?.('[data-image-editor-layer-group]')){ groupDetailImageEditorLayers(); return true; }
    if(target.closest?.('[data-image-editor-layer-ungroup]')){ ungroupDetailImageEditorLayers(); return true; }
    if(target.closest?.('[data-image-editor-discard]')){ if(!state.dirty || window.confirm('放弃本次单图编辑修改？')) discardDetailImageEditor(); return true; }
    if(target.closest?.('[data-image-editor-save-as]')){ await saveDetailImageEditorAs(); return true; }
    if(target.closest?.('[data-image-editor-save-library]')){ await saveDetailImageEditorToLibrary(); return true; }
    if(target.closest?.('[data-image-editor-close]')){ await closeDetailImageEditor({save:true}); return true; }
    return true;
}
function updateDetailImageEditorProperty(event){
    const state = detailImageEditorState;
    if(!state || !event.target.closest?.('[data-image-editor-overlay]')) return;
    const target = event.target;
    if(target.matches('[data-image-editor-brush-color]')){
        const textLayers=detailImageEditorSelectedLayers().filter(layer=>layer.type==='text' && !detailImageEditorLayerUnavailable(layer));
        if(textLayers.length){ textLayers.forEach(layer=>layer.color=target.value || '#ffffff'); state.dirty=true; drawDetailImageEditorCanvas(); }
        else state.brushColor=target.value || signalFlowAccentColor();
    }
    else if(target.matches('[data-image-editor-brush-width]')){
        state.brushWidth=Math.max(1,Math.min(300,Number(target.value || 8)));
        const output=target.parentElement?.querySelector('output'); if(output) output.textContent=String(state.brushWidth);
        positionDetailImageEditorEraserCursor();
    } else if(target.matches('[data-image-editor-crop-ratio]')){
        detailImageEditorCenteredCrop(target.value || 'free');
        renderDetailImageEditor();
    } else if(target.matches('[data-image-editor-inline-text]')){
        const layer=state.layers.find(value=>value.id===state.inlineTextId && value.type==='text');
        if(layer){ layer.text=String(target.value || '').slice(0,2000); state.dirty=true; drawDetailImageEditorCanvas(); }
    }
}
function detailImageEditorCropHandle(point,rect,tolerance){
    const handles=[['nw',rect.x,rect.y],['n',rect.x+rect.w/2,rect.y],['ne',rect.x+rect.w,rect.y],['w',rect.x,rect.y+rect.h/2],['e',rect.x+rect.w,rect.y+rect.h/2],['sw',rect.x,rect.y+rect.h],['s',rect.x+rect.w/2,rect.y+rect.h],['se',rect.x+rect.w,rect.y+rect.h]];
    const hit=handles.find(([,x,y])=>Math.abs(point.x-x)<=tolerance && Math.abs(point.y-y)<=tolerance);
    if(hit) return hit[0];
    if(point.x>=rect.x && point.x<=rect.x+rect.w && point.y>=rect.y && point.y<=rect.y+rect.h) return 'move';
    return '';
}
function resizeDetailImageEditorCrop(pointer,point){
    const state=detailImageEditorState,start=pointer.startRect,action=pointer.action;
    if(action==='move'){
        const dx=point.x-pointer.start.x,dy=point.y-pointer.start.y;
        return {...start,x:Math.max(0,Math.min(state.crop.w-start.w,start.x+dx)),y:Math.max(0,Math.min(state.crop.h-start.h,start.y+dy))};
    }
    let left=start.x,top=start.y,right=start.x+start.w,bottom=start.y+start.h;
    if(action.includes('w')) left=Math.max(0,Math.min(right-4,point.x));
    if(action.includes('e')) right=Math.min(state.crop.w,Math.max(left+4,point.x));
    if(action.includes('n')) top=Math.max(0,Math.min(bottom-4,point.y));
    if(action.includes('s')) bottom=Math.min(state.crop.h,Math.max(top+4,point.y));
    let rect={x:left,y:top,w:right-left,h:bottom-top};
    if(state.cropRatio !== 'free'){
        const [rw,rh]=state.cropRatio.split(':').map(Number),ratio=rw/rh;
        const anchorX=action.includes('w') ? right : left,anchorY=action.includes('n') ? bottom : top;
        if(rect.w/rect.h>ratio) rect.w=rect.h*ratio; else rect.h=rect.w/ratio;
        rect.x=action.includes('w') ? anchorX-rect.w : anchorX;
        rect.y=action.includes('n') ? anchorY-rect.h : anchorY;
        if(rect.x<0){ rect.w+=rect.x; rect.x=0; rect.h=rect.w/ratio; }
        if(rect.y<0){ rect.h+=rect.y; rect.y=0; rect.w=rect.h*ratio; }
        if(rect.x+rect.w>state.crop.w){ rect.w=state.crop.w-rect.x; rect.h=rect.w/ratio; }
        if(rect.y+rect.h>state.crop.h){ rect.h=state.crop.h-rect.y; rect.w=rect.h*ratio; }
    }
    return rect;
}
function beginDetailImageEditorPointer(event){
    const state = detailImageEditorState;
    const stage = event.target.closest?.('.asset-image-editor-stage');
    const canvas = event.target.closest?.('[data-image-editor-canvas]');
    if(!state || !stage) return;
    if(event.button === 1 || (event.button === 0 && detailMediaCanvasSpacePressed)){
        event.preventDefault(); event.stopPropagation();
        detailImageEditorPointer={mode:'pan',pointerId:event.pointerId,target:stage,startClient:{x:event.clientX,y:event.clientY},startPan:{x:Number(state.panX||0),y:Number(state.panY||0)}};
        stage.setPointerCapture?.(event.pointerId); stage.classList.add('is-panning');
        return;
    }
    if(!canvas || event.button !== 0) return;
    event.preventDefault(); event.stopPropagation();
    const point = detailImageEditorCanvasPoint(event,canvas);
    if(state.tool === 'brush' || state.tool === 'eraser'){
        const layer=ensureDetailImageEditorBrushLayer(true);
        if(!layer || detailImageEditorLayerUnavailable(layer)) return;
        const path={mode:state.tool === 'eraser' ? 'erase' : 'draw',color:state.brushColor,width:state.brushWidth,points:[point]};
        layer.paths.push(path); state.dirty=true;
        detailImageEditorPointer={mode:'paint',pointerId:event.pointerId,canvas,layer,path,start:point,current:point};
    } else if(state.tool === 'crop'){
        if(!state.cropDraft) detailImageEditorCenteredCrop(state.cropRatio || 'free');
        const tolerance=detailImageEditorWorldPixels(canvas,22);
        const action=detailImageEditorCropHandle(point,state.cropDraft,tolerance);
        if(!action) return;
        detailImageEditorPointer={mode:'crop',pointerId:event.pointerId,canvas,action,start:point,startRect:{...state.cropDraft},current:point};
    } else if(state.tool === 'select'){
        const context=canvas.getContext('2d');
        const layer=detailImageEditorHitText(point,context);
        if(!layer){ state.selectedLayerIds=new Set(); state.selectionAnchorId=''; syncDetailImageEditorLayerSelectionDom(); drawDetailImageEditorCanvas(); return; }
        if(!state.selectedLayerIds.has(layer.id)) state.selectedLayerIds=new Set([layer.id]);
        state.selectionAnchorId=layer.id;
        const bounds=detailImageEditorTextBounds(layer,context);
        const tolerance=detailImageEditorWorldPixels(canvas,22);
        const resize=Math.abs(point.x-(bounds.x+bounds.w))<=tolerance && Math.abs(point.y-(bounds.y+bounds.h))<=tolerance;
        const snapshots=detailImageEditorSelectedLayers().filter(value=>value.type==='text' && !detailImageEditorLayerUnavailable(value)).map(value=>({layer:value,x:value.x,y:value.y,fontSize:value.fontSize}));
        detailImageEditorPointer={mode:resize?'text-resize':'text-move',pointerId:event.pointerId,canvas,layer,start:point,bounds,snapshots,current:point};
    }
    if(!detailImageEditorPointer) return;
    canvas.setPointerCapture?.(event.pointerId);
    drawDetailImageEditorCanvas();
}
function moveDetailImageEditorPointer(event){
    const state = detailImageEditorState;
    if(state) positionDetailImageEditorEraserCursor(event);
    const pointer = detailImageEditorPointer;
    if(!state || !pointer || event.pointerId !== pointer.pointerId) return;
    event.preventDefault();
    if(pointer.mode === 'pan'){
        state.panX=pointer.startPan.x+event.clientX-pointer.startClient.x;
        state.panY=pointer.startPan.y+event.clientY-pointer.startClient.y;
        applyDetailImageEditorZoom();
        positionDetailImageEditorInlineText();
        positionDetailImageEditorEraserCursor(event);
        return;
    }
    const point = detailImageEditorCanvasPoint(event,pointer.canvas);
    pointer.current=point;
    if(pointer.mode === 'paint'){
        const previous = pointer.path.points[pointer.path.points.length-1] || point;
        if(Math.hypot(point.x-previous.x,point.y-previous.y) >= Math.max(1,state.brushWidth*.2)) pointer.path.points.push(point);
    } else if(pointer.mode === 'crop') state.cropDraft=resizeDetailImageEditorCrop(pointer,point);
    else if(pointer.mode === 'text-move'){
        const dx=point.x-pointer.start.x,dy=point.y-pointer.start.y;
        pointer.snapshots.forEach(snapshot=>{snapshot.layer.x=snapshot.x+dx;snapshot.layer.y=snapshot.y+dy;}); state.dirty=true;
    } else if(pointer.mode === 'text-resize'){
        const factor=Math.max(.1,(point.x-pointer.bounds.x)/Math.max(1,pointer.bounds.w));
        pointer.snapshots.forEach(snapshot=>{snapshot.layer.fontSize=Math.max(8,Math.min(500,snapshot.fontSize*factor));}); state.dirty=true;
    }
    drawDetailImageEditorCanvas();
}
function endDetailImageEditorPointer(event){
    const state = detailImageEditorState;
    const pointer = detailImageEditorPointer;
    if(!state || !pointer || event.pointerId !== pointer.pointerId) return;
    (pointer.target || pointer.canvas)?.releasePointerCapture?.(event.pointerId);
    pointer.target?.classList?.remove('is-panning');
    detailImageEditorPointer=null;
    if(pointer.mode === 'pan'){ positionDetailImageEditorEraserCursor(event); return; }
    if(pointer.mode === 'paint' && !pointer.path.points.length) pointer.layer.paths=pointer.layer.paths.filter(path=>path!==pointer.path);
    if(pointer.mode === 'paint' || pointer.mode === 'crop') renderDetailImageEditor();
    else {
        syncDetailImageEditorLayerSelectionDom(); drawDetailImageEditorCanvas();
        if(pointer.mode === 'text-move'){
            const moved=Math.hypot(pointer.current.x-pointer.start.x,pointer.current.y-pointer.start.y)>detailImageEditorWorldPixels(pointer.canvas,4);
            const now=Date.now();
            if(!moved && state.lastTextClickId===pointer.layer.id && now-Number(state.lastTextClickAt||0)<420){
                state.lastTextClickId=''; state.lastTextClickAt=0;
                beginDetailImageEditorInlineText(pointer.layer.id,false);
            } else if(!moved){ state.lastTextClickId=pointer.layer.id; state.lastTextClickAt=now; }
            else { state.lastTextClickId=''; state.lastTextClickAt=0; }
        }
    }
}
function applyLightboxTransform(overlay){
    const image = overlay?.querySelector?.('.asset-lightbox-image');
    if(!image) return;
    const scale = Number(overlay.dataset.scale || 1);
    const x = Number(overlay.dataset.x || 0);
    const y = Number(overlay.dataset.y || 0);
    image.style.transform = `translate(${x}px, ${y}px) scale(${scale})`;
    overlay.classList.toggle('zoomed', scale > 1.01);
}
function adjustDetailPreviewZoom(direction){
    const overlay = document.querySelector('.asset-detail-viewer');
    if(!overlay?.querySelector('.asset-lightbox-image')) return;
    const current = Number(overlay.dataset.scale || 1);
    const next = Number(direction) === 0 ? 1 : Math.max(0.5, Math.min(6, current * (Number(direction) > 0 ? 1.25 : .8)));
    overlay.dataset.scale = String(next);
    if(next <= 1.01){ overlay.dataset.x = '0'; overlay.dataset.y = '0'; }
    applyLightboxTransform(overlay);
}
function detailViewerScrollableCanMove(element, direction){
    if(!element) return false;
    if(direction > 0) return element.scrollTop + element.clientHeight < element.scrollHeight - 2;
    return element.scrollTop > 2;
}
function navigateDetailPreviewWheel(event){
    if(detailImageEditorState && event.target.closest?.('.asset-image-editor-stage')){
        event.preventDefault();
        const factor=event.deltaY<0 ? 1.12 : .89;
        detailImageEditorState.zoom=Math.max(.2,Math.min(8,detailImageEditorState.zoom*factor));
        applyDetailImageEditorZoom();
        positionDetailImageEditorInlineText();
        return;
    }
    if(!detailViewerState.open || !event.target.closest?.('.asset-detail-viewer')) return;
    if(event.target.closest?.('.asset-detail-media-sidebar,[data-detail-media-sidebar-detail],.asset-detail-inspector')) return;
    if(event.target.closest?.('[data-detail-video-controls]')) return;
    // 【QIN-580】LUT 抽屉里的滚轮归 LUT 列表：放行给原生滚动，不能拿去缩放画布。
    if(event.target.closest?.('.asset-detail-media-node-lut-panel')) return;
    if(event.target.closest?.('[data-detail-media-canvas-viewport]')){
        event.preventDefault();
        adjustDetailMediaCanvasZoom(event.deltaY < 0 ? 1 : -1, event.clientX, event.clientY);
        return;
    }
    const type = detailViewerType(detailViewerState.source, detailViewerState.items[detailViewerState.index] || {});
    if(type === 'audio' && adjustDetailAudioZoom(event)) return;
    if(type === 'font' && adjustDetailFontScale(event)) return;
    if(type === 'text' || type === 'audio' || type === 'font' || type === 'lut') return;
    const direction = event.deltaY > 0 ? 1 : -1;
    const scrollable = event.target.closest?.('.asset-detail-copy,.asset-detail-inspector,.asset-detail-audio-list,.asset-detail-media-library-grid');
    if(scrollable?.matches?.('.asset-detail-media-library-grid')) return;
    if(detailViewerScrollableCanMove(scrollable, direction)) return;
    if(Math.abs(event.deltaY) < 18) return;
    event.preventDefault();
    const now = Date.now();
    if(now - Number(detailViewerState.wheelAt || 0) < 420) return;
    detailViewerState.wheelAt = now;
    navigateDetailPreview(direction);
}
function detailTargetIsInteractive(target){
    if(target?.closest?.('[data-registry-preview],[data-asset-preview],[data-canvas-asset-preview],[data-local-preview],[data-localup-preview]')) return false;
    return Boolean(target?.closest?.('button,input,textarea,select,a,[data-asset-check],[data-localup-check],[data-local-check],[data-workflow-check],[data-prompt-check],[data-canvas-asset-check]'));
}
root.addEventListener('dblclick', event => {
    // 【QIN-563】统计卡「画布」单击即筛选全部画布资产，双击分支随 QIN-531 双击交互一并移除。
    const groupCard = event.target.closest?.('[data-registry-structure-card][data-registry-structure-type="group"]');
    if(groupCard){
        // Action buttons own their double-click semantics. Do not let a
        // second click on Coverflow or ungroup bubble into the card handler.
        const groupCardAction = event.target.closest?.('[data-registry-structure-coverflow],[data-registry-structure-remove]');
        if(groupCardAction) return;
        event.preventDefault();
        event.stopPropagation();
        openRegistryStructureCoverflow(groupCard.dataset.registryStructureCard || '', groupCard);
        return;
    }
    const versionCard = event.target.closest?.('[data-registry-structure-card][data-registry-structure-type="version"]');
    if(versionCard){
        event.preventDefault();
        event.stopPropagation();
        openRegistryStructureReferenceCanvas(versionCard.dataset.registryStructureCard || '', versionCard);
        return;
    }
    const referenceCanvas = event.target.closest?.('[data-board-reference-canvas],[data-reference-canvas-open]');
    if(referenceCanvas){
        event.preventDefault();
        openReferenceCanvasDetail(referenceCanvas.dataset.boardReferenceCanvas || referenceCanvas.dataset.referenceCanvasOpen || '');
        return;
    }
    const canvasAssetCard = event.target.closest?.('[data-canvas-asset-card]');
    if(canvasAssetCard){
        const item = findCanvasAssetItem(canvasAssetCard.dataset.canvasAssetCard || '');
        if(item?.canvas_kind === 'reference' && item.canvas_id){
            event.preventDefault();
            openReferenceCanvasDetail(item.canvas_id);
            return;
        }
    }
    if(detailTargetIsInteractive(event.target)) return;
    const reference = detailReferenceFromTarget(event.target);
    if(!reference?.id) return;
    event.preventDefault();
    showDetailPreview(reference.source, reference.id);
});
document.addEventListener('click', event => {
    // 【QIN-588】收集箱媒体预览浮层打开时，页面点击委托整体短路（GW-UI-FW-01：
    // 禁止穿透误触发底层元素）；浮层自身的关闭由其内部监听处理。
    if(document.querySelector('.inbox-media-preview')) return;
    if(!event.target.closest?.('.registry-toolbar-select')) closeRegistrySelectMenus();
    // 【QIN-580】「套用 LUT」是下拉菜单，点别处就收起。菜单挂在 body 上的
    // `[data-detail-media-lut-layer]` 里，点菜单自身不算「点别处」。
    if(detailViewerState.mediaLutMenu?.open
        && !event.target.closest?.('.asset-detail-media-lut-entry')
        && !event.target.closest?.('[data-detail-media-lut-layer]')){
        detailViewerState.mediaLutMenu = {open:false, step:'asset', assetId:''};
        refreshDetailMediaLutEntryDom();
    }
    const structureCreate = event.target.closest?.('[data-registry-structure-create]');
    if(structureCreate){
        const type = structureCreate.dataset.registryStructureCreate || '';
        const ids = registryStructureMenu?.assetIds || [];
        closeRegistryStructureMenu();
        runRegistryStructureAction(type, ids).catch(err => setStatus(err.message || '结构创建失败'));
        return;
    }
    const structureRelease = event.target.closest?.('[data-registry-structure-release]');
    if(structureRelease){
        // 【QIN-534 反馈 4】右键菜单的解除入口：菜单挂在 body 上，根级点击处理不会命中，这里独占处理。
        const type = structureRelease.dataset.registryStructureRelease || '';
        const ids = registryStructureMenu?.assetIds || [];
        closeRegistryStructureMenu();
        runRegistryStructureAction(`release-${type}`, ids).catch(err => setStatus(err.message || '结构解除失败'));
        return;
    }
    if(event.target.closest?.('[data-registry-structure-remove-dialog-close]')){
        closeRegistryVersionStructureRemoveDialog();
        return;
    }
    if(event.target.closest?.('[data-registry-structure-remove-confirm]')){
        confirmRegistryVersionStructureRemoval().catch(err => setStatus(err.message || '解除版本结构失败'));
        return;
    }
    const structureRemove = event.target.closest?.('[data-registry-structure-remove]');
    if(structureRemove){
        openRegistryStructureRemoveDialog(structureRemove.dataset.registryStructureRemove || '', structureRemove);
        return;
    }
    if(event.target.closest?.('[data-asset-notice-close]')){ event.target.closest('[data-asset-notice]')?.remove(); return; }
    if(event.target.closest?.('[data-asset-error-close]') || (event.target.matches?.('[data-asset-error-dialog]'))){ document.querySelector('[data-asset-error-dialog]')?.remove(); return; }
    if(event.target.closest?.('[data-video-storyboard-close]') || event.target.matches?.('[data-video-storyboard-overlay]')){ closeDetailVideoStoryboard(); return; }
    const storyboardOpen = event.target.closest?.('[data-detail-video-storyboard-open]');
    if(storyboardOpen){ openDetailVideoStoryboard(storyboardOpen); return; }
    if(event.target?.classList?.contains('storage-settings-overlay')){
        const overlayId = event.target.id || '';
        if(overlayId === 'storageSettingsOverlay') closeStorageSettings();
        else if(overlayId === 'boardCreateOverlay') closeCreateLibraryModal();
        else if(overlayId === 'boardCreatePathPickerOverlay') closeBoardCreatePathBrowser();
        else if(overlayId === 'boardSourceOverlay') closeBoardSourceModal();
        else if(overlayId === 'boardEditOverlay') closeBoardEditModal();
        else if(overlayId === 'boardFolderCreateOverlay') closeCustomBoardFolderCreateModal();
        else if(overlayId === 'thumbnailGeneratorOverlay') closeThumbnailGenerator();
        else if(overlayId === 'registryRecycleOverlay') closeRegistryRecycleDialog();
        else if(overlayId === 'registryRecycleBinOverlay') closeRegistryRecycleBin();
        else if(overlayId === 'registryStructureRemoveOverlay') closeRegistryVersionStructureRemoveDialog();
        return;
    }
    if(event.target.closest?.('[data-image-editor-overlay]')){
        handleDetailImageEditorClick(event).catch(err => { setStatus(err.message || '单图编辑操作失败'); if(detailImageEditorState){ detailImageEditorState.saving=false; renderDetailImageEditor(); } });
        return;
    }
    const detailStructureCoverflow=event.target.closest?.('[data-detail-structure-coverflow]');
    if(detailStructureCoverflow){ openRegistryStructureCoverflow(detailStructureCoverflow.dataset.detailStructureCoverflow || '',detailStructureCoverflow); return; }
    const detailStructureVersion=event.target.closest?.('[data-detail-structure-version-select]');
    if(detailStructureVersion){ selectDetailStructureVersion(detailStructureVersion.dataset.detailStructureId || '',detailStructureVersion.dataset.detailStructureVersionSelect || '').catch(err=>setStatus(err.message||'切换合并版本失败')); return; }
    if(event.target.closest?.('[data-detail-image-versions-toggle]')){ detailViewerState.imageVersionPanelCollapsed=!detailViewerState.imageVersionPanelCollapsed; renderDetailViewer(); return; }
    if(event.target.closest?.('[data-detail-image-versions-refresh]')){ loadDetailImageVersions(detailActiveImageItem(),true).catch(err=>setStatus(err.message||'图片版本刷新失败')); return; }
    const imageVersionSelect=event.target.closest?.('[data-detail-image-version-select]');
    if(imageVersionSelect){ selectDetailImageVersion(imageVersionSelect.dataset.detailImageVersionSelect || ''); return; }
    const imageVersionEdit=event.target.closest?.('[data-detail-image-version-edit]');
    if(imageVersionEdit){ editDetailImageVersion(imageVersionEdit.dataset.detailImageVersionEdit || '').catch(err=>setStatus(err.message||'图片版本编辑失败')); return; }
    const imageVersionVisibility=event.target.closest?.('[data-detail-image-version-visibility]');
    if(imageVersionVisibility){ setDetailImageVersionHidden(imageVersionVisibility.dataset.detailImageVersionVisibility || '',imageVersionVisibility.dataset.hidden!=='1').catch(err=>{ setStatus(err.message||'图片版本显示状态更新失败'); showAssetFailure('图片版本显示状态更新失败',err); }); return; }
    const imageVersionDelete=event.target.closest?.('[data-detail-image-version-delete]');
    if(imageVersionDelete){ deleteDetailImageVersion(imageVersionDelete.dataset.detailImageVersionDelete || '').catch(err=>{ setStatus(err.message||'图片版本删除失败'); showAssetFailure('图片版本删除失败',err); }); return; }
    if(detailViewerState.mediaCanvasContextMenu && !event.target.closest?.('[data-detail-media-context-menu]')) closeDetailMediaCanvasContextMenu();
    if(event.target.closest?.('[data-registry-recycle-bin-close]')){ closeRegistryRecycleBin(); return; }
    if(event.target.closest?.('[data-registry-recycle-bin-reload]')){ loadRegistryRecycleBin(); return; }
    if(event.target.closest?.('[data-registry-recycler-setup-close]')){ closeRegistryRecyclerSetupDialog(); return; }
    if(event.target.closest?.('[data-registry-recycler-setup-default]')){ chooseRegistryRecyclerSetup('default').catch(err => setStatus(err.message || '回收目录设置失败')); return; }
    if(event.target.closest?.('[data-registry-recycler-setup-custom]')){ chooseRegistryRecyclerSetup('custom').catch(err => setStatus(err.message || '回收目录设置失败')); return; }
    const recycleRestore = event.target.closest?.('[data-registry-recycle-restore]');
    if(recycleRestore){ restoreRegistryRecycleEntry(recycleRestore.dataset.registryRecycleRestore || ''); return; }
    if(event.target.closest?.('[data-registry-recycle-close]')){ closeRegistryRecycleDialog(); return; }
    if(event.target.closest?.('[data-registry-recycle-confirm]')){
        confirmRegistryRecycle().catch(err => setStatus(err.message || '移入回收站失败'));
        return;
    }
    const viewer = event.target.closest?.('.asset-detail-viewer');
    if(viewer){
        const registryTagEditor = event.target.closest?.('[data-registry-tag-editor]');
        if(registryTagEditor){
            setRegistryTagEditorAssetId(registryTagEditor.dataset.registryTagEditor || '');
            renderDetailViewer();
            requestAnimationFrame(() => document.querySelector('.asset-detail-viewer [data-registry-tag-input]')?.focus());
            return;
        }
        const registryTagPreset = event.target.closest?.('[data-registry-tag-preset]');
        if(registryTagPreset){
            const assetId = registryTagPreset.dataset.assetId || '';
            const input = [...viewer.querySelectorAll('[data-registry-tag-input]')].find(node => node.dataset.registryTagInput === assetId);
            const tag = registryTagPreset.dataset.registryTagPreset || '';
            if(input && tag){ const names=parseRegistryTagNames(input.value); if(!names.includes(tag)) names.push(tag); input.value=names.join('，'); input.focus(); }
            return;
        }
        const registryTagSave = event.target.closest?.('[data-registry-tag-save]');
        if(registryTagSave){
            const assetId = registryTagSave.dataset.registryTagSave || '';
            const input = [...viewer.querySelectorAll('[data-registry-tag-input]')].find(node => node.dataset.registryTagInput === assetId);
            addRegistryAssetTags([assetId], input?.value || '').catch(err => setStatus(err.message || '标签添加失败'));
            return;
        }
        const registryTagDelete = event.target.closest?.('[data-registry-tag-delete]');
        if(registryTagDelete){ deleteRegistryAssetTag(registryTagDelete.dataset.assetId || '', registryTagDelete.dataset.registryTagDelete || '').catch(err => setStatus(err.message || '标签删除失败')); return; }
        const registryRelatedOpen = event.target.closest?.('[data-registry-related-open]');
        if(registryRelatedOpen){ openRegistryRelatedAsset(registryRelatedOpen.dataset.registryRelatedOpen || '').catch(err => setStatus(err.message || '关联素材读取失败')); return; }
        const registryRelationDelete = event.target.closest?.('[data-registry-relation-delete]');
        if(registryRelationDelete){ removeRegistryAssetRelation(registryRelationDelete.dataset.assetId || '', registryRelationDelete.dataset.registryRelationDelete || '').catch(err => setStatus(err.message || '解除关联失败')); return; }
    }
    const mediaCanvasContextAction = event.target.closest?.('[data-detail-media-context-action]');
    if(mediaCanvasContextAction){ handleDetailMediaCanvasContextAction(mediaCanvasContextAction.dataset.detailMediaContextAction || ''); return; }
    const detailVideoAction = event.target.closest?.('[data-detail-video-action]');
    if(detailVideoAction){ handleDetailVideoControl(detailVideoAction).catch(err => { setStatus(err.message || '视频操作失败'); showAssetFailure('视频操作失败',err); }); return; }
    if(event.target.closest?.('[data-detail-media-coverflow-toggle]')){ setDetailMediaCanvasCoverflowVisible(!detailMediaCanvasCoverflowVisible); return; }
    if(event.target.closest?.('[data-detail-reference-share]')){
        const currentItem = detailViewerState.items[detailViewerState.index] || detailViewerItem(detailViewerState.source, detailViewerState.id);
        const shareUrl = window.location.href;
        const shareText = currentItem?.name ? `${currentItem.name} - ${shareUrl}` : shareUrl;
        navigator.clipboard?.writeText(shareText).then(() => {
            setStatus('已复制素材链接到剪贴板');
        }).catch(() => {
            setStatus('已生成素材链接');
        });
        return;
    }
    if(event.target.closest?.('[data-detail-media-minimap-toggle]')){ setDetailMediaCanvasMinimapVisible(!detailMediaCanvasMinimapVisible); return; }
    const mediaCanvasRemove = event.target.closest?.('[data-detail-media-canvas-remove]');
    if(mediaCanvasRemove){ deleteDetailMediaCanvasSelection(); return; }
    if(event.target.closest?.('[data-detail-media-library-toggle]')){ toggleDetailMediaCanvasLibrary(); return; }
    const mediaSidebarView=event.target.closest?.('[data-detail-media-sidebar-view]');
    if(mediaSidebarView){ setDetailMediaCanvasSidebarView(mediaSidebarView.dataset.detailMediaSidebarView || 'library'); return; }
    const mediaLibraryItem = event.target.closest?.('[data-detail-media-library-item]');
    if(mediaLibraryItem){ selectDetailMediaCanvasLibraryItem(mediaLibraryItem.dataset.detailMediaLibraryItem || ''); return; }
    // 【QIN-580】画布 LUT：节点角标浮窗。
    // 这几条必须排在 [data-detail-media-object] 之前——角标和浮窗都在节点内部，
    // 否则点角标会被当成「点节点」而切回活动项，浮窗刚开就被关掉。
    // 工具条下拉菜单挂在 body 上的浮层里，root 委托收不到，由浮层自身的监听处理。
    if(handleDetailMediaLutMenuClick(event)) return;
    const mediaNodeLut = event.target.closest?.('[data-detail-media-node-lut]');
    if(mediaNodeLut){ toggleDetailMediaNodeLutPanel(mediaNodeLut.dataset.detailMediaNodeLut || '').catch(() => {}); return; }
    const mediaNodeLutPanel = event.target.closest?.('[data-detail-media-node-lut-panel]');
    if(mediaNodeLutPanel){
        const targetId = mediaNodeLutPanel.dataset.detailMediaNodeLutPanel || '';
        if(event.target.closest?.('[data-detail-media-node-lut-close]')){ closeDetailMediaNodeLutPanel(); return; }
        const nodeLutItem = event.target.closest?.('[data-detail-media-node-lut-item]');
        if(nodeLutItem){ setDetailMediaNodeLut(targetId, nodeLutItem.dataset.detailMediaNodeLutItem || '').catch(() => {}); return; }
        const nodeLutCompare = event.target.closest?.('[data-detail-media-node-lut-compare]');
        if(nodeLutCompare){ setDetailMediaNodeLutCompare(targetId, nodeLutCompare.dataset.detailMediaNodeLutCompare || ''); return; }
        if(event.target.closest?.('[data-detail-media-node-lut-save]')){ saveDetailMediaNodeLutVersion(targetId).catch(() => {}); return; }
        if(event.target.closest?.('[data-detail-media-node-lut-clear]')){ clearDetailMediaNodeLut(targetId); return; }
    }
    // 分割线只管拖，别让它多选一次节点；刚拖完补上的那次 click 也一并跳过。
    if(event.target.closest?.('[data-detail-media-lut-split]') || Date.now() - detailMediaLutSplitDragEndedAt < 400) return;
    // 【QIN-580】点空白处（面板和角标之外的任何地方）自动收起调节浮窗。
    if(String(detailViewerState.mediaCanvasLutPanel || '')){ closeDetailMediaNodeLutPanel(); }
    const mediaCanvasNode = event.target.closest?.('[data-detail-media-canvas-node]');
    if(mediaCanvasNode?.closest?.('.asset-detail-media-minimap')){
        const id = mediaCanvasNode.dataset.detailMediaCanvasNode || '';
        selectDetailMediaCanvasItem(id,event);
        if(!event.ctrlKey && !event.metaKey && !event.shiftKey) activateDetailMediaCanvasItem(id,false);
        return;
    }
    const mediaCanvasObject = event.target.closest?.('[data-detail-media-object]');
    if(mediaCanvasObject){
        if(event.detail === 0){
            const id = mediaCanvasObject.dataset.detailMediaObject || '';
            selectDetailMediaCanvasItem(id,event);
            if(mediaCanvasNode && !event.ctrlKey && !event.metaKey && !event.shiftKey) activateDetailMediaCanvasItem(mediaCanvasNode.dataset.detailMediaCanvasNode || '',false);
        } else if(mediaCanvasNode && detailViewerState.mediaCanvasActiveId){
            activateDetailMediaCanvasItem(detailViewerState.mediaCanvasActiveId,false);
        }
        return;
    }
    if(event.target.closest?.('[data-detail-reference-add-selection]')){ addSelectedAssetsToDetailMediaCanvas(); return; }
    const mediaCanvasZoom = event.target.closest?.('[data-detail-media-canvas-zoom]');
    if(mediaCanvasZoom){ adjustDetailMediaCanvasZoom(Number(mediaCanvasZoom.dataset.detailMediaCanvasZoom || 0)); return; }
    if(event.target.closest?.('[data-detail-reference-save]')){ saveDetailReferenceCanvas().catch(err => setStatus(err.message || '参考画布保存失败')); return; }
    if(event.target.closest?.('[data-detail-media-canvas-viewport]') && !event.target.closest?.('[data-detail-media-object],[data-detail-media-context-menu]') && finishDetailMediaCanvasAnnotationEdit()){
        renderDetailViewer();
        return;
    }
    if(event.target.closest?.('[data-detail-inspector-toggle]')){ toggleDetailInspector(); return; }
    const officePdf = event.target.closest?.('[data-detail-office-pdf]');
    if(officePdf){ exportDetailOfficePdf(officePdf.dataset.detailOfficePdf || '', officePdf).catch(err => setStatus(err.message || 'PDF 转换失败')); return; }
    const officeOpen = event.target.closest?.('[data-detail-office-open]');
    if(officeOpen){ openDetailOfficeAsset(officeOpen.dataset.detailOfficeOpen || '').catch(err => setStatus(err.message || '无法打开 Office 文件')); return; }
    const detailExportPdf = event.target.closest?.('[data-detail-export-pdf]');
    if(detailExportPdf){
        const item = detailViewerState.items[detailViewerState.index] || {};
        downloadRegistryPdf([detailExportPdf.dataset.detailExportPdf || ''],String(item.name || '资产导出').replace(/\.[^.]+$/,'')).catch(err => setStatus(err.message || 'PDF 导出失败'));
        return;
    }
    if(event.target.closest?.('[data-detail-font-library-toggle]')){ toggleDetailFontLibrary(); return; }
    const fontExpand = event.target.closest?.('[data-detail-font-expand]');
    if(fontExpand){ toggleDetailFontPanel(fontExpand.dataset.detailFontExpand || ''); return; }
    const fontItem = event.target.closest?.('[data-detail-font-item]');
    if(fontItem){ navigateDetailFontPreviewToId(fontItem.dataset.detailFontItem || ''); return; }
    const lutItem = event.target.closest?.('[data-detail-lut-item]');
    if(lutItem){ navigateDetailLutToId(lutItem.dataset.detailLutItem || ''); return; }
    const lutCompare = event.target.closest?.('[data-detail-lut-compare]');
    if(lutCompare){ setDetailLutCompare(lutCompare.dataset.detailLutCompare || ''); return; }
    if(event.target.closest?.('[data-detail-viewer-close]')){ closeDetailPreview(); return; }
    if(event.target.closest?.('[data-detail-text-edit]')){ startDetailTextEdit(); return; }
    if(event.target.closest?.('[data-detail-text-cancel]')){ cancelDetailTextEdit(); return; }
    if(event.target.closest?.('[data-detail-text-save]')){
        saveDetailTextEdit().catch(err => setStatus(err.message || '正文保存失败'));
        return;
    }
    if(event.target.closest?.('[data-detail-version-current]')){ returnToCurrentDetailText(); return; }
    if(event.target.closest?.('[data-detail-version-edit]')){ startDetailTextVersionEdit(); return; }
    if(event.target.closest?.('[data-detail-version-edit-cancel]')){ cancelDetailTextVersionEdit(); return; }
    if(event.target.closest?.('[data-detail-version-edit-save]')){
        saveDetailTextVersionEdit().catch(err => setStatus(err.message || '版本保存失败'));
        return;
    }
    if(event.target.closest?.('[data-detail-version-refresh]')){
        loadDetailTextVersions(true).catch(err => setStatus(err.message || '版本刷新失败'));
        return;
    }
    const versionEdit = event.target.closest?.('[data-detail-version-edit-id]');
    if(versionEdit){ selectDetailTextVersion(versionEdit.dataset.detailVersionEditId || '', true).catch(err => setStatus(err.message || '版本读取失败')); return; }
    const versionView = event.target.closest?.('[data-detail-version-view]');
    if(versionView){ selectDetailTextVersion(versionView.dataset.detailVersionView || '').catch(err => setStatus(err.message || '版本读取失败')); return; }
    const versionRestore = event.target.closest?.('[data-detail-version-restore]');
    if(versionRestore){ restoreDetailTextVersion(versionRestore.dataset.detailVersionRestore || '').catch(err => setStatus(err.message || '版本恢复失败')); return; }
    const versionDelete = event.target.closest?.('[data-detail-version-delete]');
    if(versionDelete){ deleteDetailTextVersion(versionDelete.dataset.detailVersionDelete || '').catch(err => setStatus(err.message || '版本删除失败')); return; }
    const detailBreadcrumb = event.target.closest?.('[data-detail-breadcrumb]');
    if(detailBreadcrumb){
        returnFromDetailViewer(detailBreadcrumb.dataset.detailBreadcrumb || 'origin').catch(err => setStatus(err.message || '返回资产页面失败'));
        return;
    }
    const audioItem = event.target.closest?.('[data-detail-audio-item]');
    if(audioItem){ navigateDetailPreviewToId(audioItem.dataset.detailAudioItem || ''); return; }
    if(event.target.closest?.('[data-detail-file-reveal]')){
        revealDetailViewerFile().catch(err => setStatus(err.message || '无法打开目标文件夹'));
        return;
    }
    if(event.target.closest?.('[data-detail-name-save]')){
        saveDetailViewerName().catch(err => setStatus(err.message || '名称保存失败'));
        return;
    }
    const detailNavigate = event.target.closest?.('[data-detail-navigate]');
    if(detailNavigate){ navigateDetailPreview(Number(detailNavigate.dataset.detailNavigate || 0)); return; }
    const detailZoom = event.target.closest?.('[data-detail-zoom]');
    if(detailZoom){ adjustDetailPreviewZoom(Number(detailZoom.dataset.detailZoom || 0)); return; }
    // 挂在 body 上的浮层不在 #assetManagerRoot 内，root 的点击委托收不到事件；
    // 新增 body 级浮层时务必把容器 id 加到这份清单，否则按钮点击全部无效。
    // 【QIN-546】整理菜单/弹窗/toast 走独立委托：与通用 handleClick 语义不同，不能混在一起。
    if(event.target.closest?.('#assetOrganizeMenu,#assetOrganizeDialog,#assetOrganizeToast')){
        handleAssetOrganizeOverlayClick(event).catch(err => setStatus(err.message || '操作失败'));
        return;
    }
    if(event.target.closest?.('#storageSettingsOverlay,#boardCreateOverlay,#boardSourceOverlay,#boardEditOverlay,#boardFolderCreateOverlay,#thumbnailGeneratorOverlay,#thumbnailCacheSwitchOverlay')){
        handleClick(event).catch(err => setStatus(err.message || '操作失败'));
        return;
    }
});
document.addEventListener('dblclick', event => {
    const editorCanvas=event.target.closest?.('[data-image-editor-canvas]');
    if(detailImageEditorState && editorCanvas){
        event.preventDefault(); event.stopPropagation();
        const point=detailImageEditorCanvasPoint(event,editorCanvas);
        const layer=detailImageEditorHitText(point,editorCanvas.getContext('2d'));
        if(layer) beginDetailImageEditorInlineText(layer.id,false);
        return;
    }
    const cinemaImage = event.target.closest?.('[data-detail-media-canvas-node]');
    if(cinemaImage && !cinemaImage.closest?.('.asset-detail-media-minimap')){
        const item = detailMediaCanvasItems().find(value => value.id === (cinemaImage.dataset.detailMediaCanvasNode || ''));
        if(item && detailViewerType(detailViewerState.source,item) === 'image'){
            event.preventDefault(); event.stopPropagation();
            openDetailImageEditor(item.id).catch(err => setStatus(err.message || '无法打开单图编辑模式'));
            return;
        }
    }
    const libraryItem = event.target.closest?.('[data-detail-media-library-item]');
    if(libraryItem){
        event.preventDefault(); event.stopPropagation();
        const id = libraryItem.dataset.detailMediaLibraryItem || '';
        const candidate = detailMediaCanvasLibraryItems().find(item => item.id === id);
        const persistId = String(candidate?.asset_id || candidate?.assetId || candidate?.id || '');
        const existing = detailMediaCanvasItems().find(item => detailMediaCanvasPersistId(item) === persistId);
        if(existing) activateDetailMediaCanvasItem(existing.id);
        else {
            const viewport = document.querySelector('[data-detail-media-canvas-viewport]');
            const rect = viewport?.getBoundingClientRect();
            if(rect) addDetailMediaCanvasLibraryItem(id,rect.left+rect.width/2,rect.top+rect.height/2);
        }
        return;
    }
    const annotation = event.target.closest?.('[data-detail-media-annotation-node]');
    if(!annotation || event.target.closest?.('[data-detail-media-resize]')) return;
    event.preventDefault(); event.stopPropagation();
    beginDetailMediaCanvasAnnotationEdit(annotation.dataset.detailMediaAnnotationNode || '');
});
function handleDetailCinemaPointer(event){
    if(event.button !== 0 || event.shiftKey || event.ctrlKey || event.metaKey || event.altKey || detailImageEditorState) return;
    const node = event.target.closest?.('[data-detail-media-canvas-node]');
    if(!node || node.closest?.('.asset-detail-media-minimap') || event.target.closest?.('[data-detail-media-resize],button,a,input,textarea,select,.asset-detail-video-controls')) return;
    const id = node.dataset.detailMediaCanvasNode || '';
    const item = detailMediaCanvasItems().find(value => value.id === id);
    if(!item || detailViewerType(detailViewerState.source,item) !== 'image') return;
    const now = Date.now();
    if(detailCinemaClickState.id === id && now-detailCinemaClickState.at < 420){
        event.preventDefault();
        event.stopImmediatePropagation();
        detailCinemaClickState = {id:'',at:0};
        openDetailImageEditor(id).catch(err => setStatus(err.message || '无法打开单图编辑模式'));
        return;
    }
    detailCinemaClickState = {id,at:now};
}
function enterCurrentDetailEditableContent(){
    if(!detailViewerState.open) return false;
    const item = detailViewerState.items[detailViewerState.index]
        || detailViewerItem(detailViewerState.source,detailViewerState.id);
    if(!item) return false;
    const type = detailViewerType(detailViewerState.source,item);
    if(type === 'image'){
        openDetailImageEditor(item.id,{item}).catch(err => setStatus(err.message || '无法打开单图编辑器'));
        return true;
    }
    if(type === 'text' && detailViewerCanEditText(detailViewerState.source,item)){
        if(detailViewerState.textLoading || detailViewerState.textError || detailViewerState.textTruncated){
            setStatus('正文尚未准备好，暂时无法编辑');
            return false;
        }
        startDetailTextEdit();
        return true;
    }
    return false;
}
function deferDetailPreviewCloseForBackquote(state){
    clearTimeout(detailPreviewBackquoteCloseTimer);
    setDetailPreviewBackquoteCloseTimer(setTimeout(() => {
        detailPreviewBackquoteCloseTimer = 0;
        if(detailViewerState === state && state.open) closeDetailPreview();
    },DETAIL_BACKQUOTE_DOUBLE_TAP_MS));
}
function deferDetailImageEditorCloseForBackquote(state){
    clearTimeout(detailImageEditorBackquoteCloseTimer);
    setDetailImageEditorBackquoteCloseTimer(setTimeout(() => {
        detailImageEditorBackquoteCloseTimer = 0;
        if(detailImageEditorState === state){
            requestCloseDetailImageEditor('关闭编辑器')
                .catch(err => setStatus(err.message || '单图编辑关闭失败'));
        }
    },DETAIL_BACKQUOTE_DOUBLE_TAP_MS));
}
function restoreSingleBackquoteInput(editor, pending){
    if(!editor || !pending || pending.editor !== editor) return;
    const inserted = ['·','~','`'].includes(pending.key) ? pending.key : '';
    const expected = pending.before.slice(0,pending.start) + inserted + pending.before.slice(pending.end);
    if(editor.value !== expected) return;
    editor.value = pending.before;
    editor.setSelectionRange?.(pending.start,pending.end);
}
function exitDetailTextEditFromBackquote(editor, pending){
    restoreSingleBackquoteInput(editor,pending);
    setDetailTextEditorBackquoteInput(null);
    if(String(editor?.value || '') === String(detailViewerState.text || '')){
        cancelDetailTextEdit();
        return;
    }
    saveDetailTextEdit().catch(err => setStatus(err.message || '正文保存失败'));
}
document.addEventListener('wheel', navigateDetailPreviewWheel, {passive:false});
document.addEventListener('input', event => {
    if(event.target?.id === 'thumbnailDirectory'){
        thumbnailGeneratorState.directory = String(event.target.value || '').trim();
        updateThumbnailCacheExclusionHint();
        return;
    }
    if(event.target?.id === 'boardCreateName' || event.target?.id === 'boardCreatePath'){
        syncBoardCreateDraftFromDom(event.target.id);
        return;
    }
    if(event.target?.closest?.('[data-project-directory-template-editor]')){
        syncProjectDirectoryTemplateDraft();
        return;
    }
    const detailVideoProgress = event.target?.closest?.('[data-detail-video-progress]');
    if(detailVideoProgress){
        const video = detailViewerVideo(detailVideoProgress.closest('[data-detail-video-controls]')?.dataset.detailVideoFor || '');
        if(video){ video.currentTime = Math.max(0,Math.min(Number(video.duration || detailVideoProgress.max || 0),Number(detailVideoProgress.value || 0))); syncDetailVideoControls(video); }
        return;
    }
    const detailVideoVolume = event.target?.closest?.('[data-detail-video-volume]');
    if(detailVideoVolume){
        const video = detailViewerVideo(detailVideoVolume.closest('[data-detail-video-controls]')?.dataset.detailVideoFor || '');
        if(video){ video.volume = Math.max(0,Math.min(1,Number(detailVideoVolume.value || 0))); if(video.volume > 0) video.muted = false; syncDetailVideoControls(video); }
        return;
    }
    const detailLutStrength = event.target?.closest?.('[data-detail-lut-strength]');
    if(detailLutStrength){
        setDetailLutStrength(detailLutStrength.value);
        return;
    }
    // 【QIN-580】节点 LUT 浮窗里的强度滑杆。
    const mediaNodeLutStrength = event.target?.closest?.('[data-detail-media-node-lut-strength]');
    if(mediaNodeLutStrength){
        const targetId = mediaNodeLutStrength.closest('[data-detail-media-node-lut-panel]')?.dataset.detailMediaNodeLutPanel || '';
        if(targetId) setDetailMediaNodeLutStrength(targetId, mediaNodeLutStrength.value);
        return;
    }
    if(event.target?.matches?.('[data-detail-reference-title]')) detailViewerState.referenceCanvasTitle = String(event.target.value || '').slice(0, 80);
    const annotation = event.target?.closest?.('[data-detail-media-annotation]');
    if(annotation){
        const note = (detailViewerState.mediaCanvasAnnotations || []).find(value => value.id === annotation.dataset.detailMediaAnnotation);
        if(note){
            note.text = String(annotation.value || '').slice(0,2000);
            fitDetailMediaCanvasAnnotationToText(note,annotation.closest('[data-detail-media-annotation-node]'));
        }
    }
    const librarySize = event.target?.closest?.('[data-detail-media-library-size]');
    if(librarySize){
        setDetailMediaCanvasLibraryThumbSize(Math.max(96,Math.min(220,Number(librarySize.value || 156))));
        document.querySelector('.asset-detail-media-library')?.style.setProperty('--media-library-thumb-size',`${detailMediaCanvasLibraryThumbSize}px`);
        const output = document.querySelector('[data-detail-media-library-size-output]');
        if(output) output.textContent = `${detailMediaCanvasLibraryThumbSize}px`;
        try { localStorage.setItem(ASSET_DETAIL_MEDIA_LIBRARY_SIZE_KEY,String(detailMediaCanvasLibraryThumbSize)); } catch(_) {}
    }
    const librarySearch = event.target?.closest?.('[data-detail-media-library-search]');
    if(librarySearch){
        detailViewerState.mediaCanvasLibraryQuery = String(librarySearch.value || '').slice(0,160);
        refreshDetailMediaCanvasLibraryGrid();
    }
    const annotationColor = event.target?.closest?.('[data-detail-annotation-color]');
    if(annotationColor) applyDetailMediaCanvasAnnotationColor(annotationColor.dataset.detailAnnotationColor || '',annotationColor.value || '#ffffff');
});
document.addEventListener('change', event => {
    if(event.target?.closest?.('[data-project-directory-template-editor]')){
        syncProjectDirectoryTemplateDraft();
        return;
    }
    if(event.target?.matches?.('[data-detail-video-root]')){
        detailViewerState.videoExportRootId = String(event.target.value || '');
        const video = detailViewerVideo(event.target.closest('[data-detail-video-controls]')?.dataset.detailVideoFor || '');
        if(video) syncDetailVideoControls(video);
        setStatus(`视频导出素材库：${event.target.selectedOptions?.[0]?.textContent || '未选择'}`);
        return;
    }
    if(event.target?.matches?.('[data-detail-media-library-scope]')){
        detailViewerState.mediaCanvasLibraryScope = event.target.value || 'all';
        refreshDetailMediaCanvasLibraryGrid();
        return;
    }
    if(event.target?.matches?.('[data-detail-media-library-library]')){
        detailViewerState.mediaCanvasLibraryStorageRootId = event.target.value || 'all';
        detailViewerState.mediaCanvasLibraryScope = 'all';
        const scope = document.querySelector('[data-detail-media-library-scope]');
        if(scope) scope.value = 'all';
        refreshDetailMediaCanvasLibraryGrid();
        loadDetailMediaCanvasLibraryRoot(detailViewerState.mediaCanvasLibraryStorageRootId);
        return;
    }
    if(event.target?.matches?.('[data-detail-media-library-sort]')){
        detailViewerState.mediaCanvasLibrarySort = ['newest','oldest','name','size_desc'].includes(event.target.value) ? event.target.value : 'newest';
        refreshDetailMediaCanvasLibraryGrid();
        return;
    }
    if(event.target?.matches?.('[data-detail-media-export-scope]')){
        detailViewerState.mediaCanvasExportScope = event.target.value === 'selection' ? 'selection' : 'all';
        document.querySelectorAll('[data-detail-media-export-scope]').forEach(select => { select.value = detailViewerState.mediaCanvasExportScope; });
        const helper = document.querySelector('.asset-detail-media-export-tools > small');
        if(helper) helper.textContent = detailViewerState.mediaCanvasExportScope === 'selection' ? (detailMediaCanvasSelectedIds().size ? `输出已选 ${detailMediaCanvasSelectedIds().size} 项` : '请先选择对象') : '输出全部画布';
        return;
    }
    if(event.target?.matches?.('[data-detail-media-normalize-basis]')){
        detailViewerState.mediaCanvasNormalizeBasis = ['first','average','last'].includes(event.target.value) ? event.target.value : 'first';
        document.querySelectorAll('[data-detail-media-normalize-basis]').forEach(select => { select.value = detailViewerState.mediaCanvasNormalizeBasis; });
        setStatus(`规格化基准已切换为${detailViewerState.mediaCanvasNormalizeBasis === 'average' ? '平均值' : detailViewerState.mediaCanvasNormalizeBasis === 'last' ? '最后一个' : '第一个'}`);
        return;
    }
    if(!event.target?.matches?.('[data-detail-annotation-color]')) return;
    if(detailMediaCanvasColorSnapshot) pushDetailMediaCanvasHistory(detailMediaCanvasColorSnapshot);
    setDetailMediaCanvasColorSnapshot(null);
});
document.addEventListener('dragstart', beginDetailMediaLibraryDrag);
['loadedmetadata','durationchange','timeupdate','play','pause','ended','volumechange','ratechange'].forEach(type => document.addEventListener(type,handleDetailVideoMediaEvent,true));
document.addEventListener('input', updateDetailImageEditorProperty);
document.addEventListener('change', updateDetailImageEditorProperty);
document.addEventListener('pointerdown', beginDetailImageEditorPointer, true);
document.addEventListener('pointermove', moveDetailImageEditorPointer);
document.addEventListener('pointerup', endDetailImageEditorPointer);
document.addEventListener('pointercancel', endDetailImageEditorPointer);
document.addEventListener('pointerdown', handleDetailCinemaPointer, true);
document.addEventListener('pointerdown', beginRegistryDetailPanelResize);
document.addEventListener('pointermove', updateRegistryDetailPanelResize);
document.addEventListener('pointerup', endRegistryDetailPanelResize);
document.addEventListener('pointercancel', endRegistryDetailPanelResize);

// 【QIN-591】域函数面注册：主文件 gwCall('preview', fn) 经由此对象分派。
GW_DEFERRED['preview'] = {
    addDetailImageEditorText,
    applyDetailImageEditorCrop,
    applyLightboxTransform,
    cancelDetailTextEdit,
    cancelDetailTextVersionEdit,
    cancelPreviewAllConfirm,
    cancelPreviewCacheDelete,
    clearDetailViewerFontFace,
    closeDetailVideoStoryboard,
    closeRegistryAssetFullscreenViewer,
    confirmPreviewCacheDelete,
    controlPreviewAllJob,
    deferDetailImageEditorCloseForBackquote,
    deferDetailPreviewCloseForBackquote,
    deleteBoardVideoStoryboards,
    deleteDetailImageEditorLayers,
    enterCurrentDetailEditableContent,
    enterDetailVideoFullscreen,
    exitDetailTextEditFromBackquote,
    exportDetailVideoFrame,
    generateBoardVideoStoryboards,
    groupDetailImageEditorLayers,
    handleDetailVideoControl,
    hydrateVideoStoryboardImages,
    markAssetFloatingSurface,
    navigateDetailPreview,
    newDetailImageEditorBrushLayer,
    openAssetItem,
    openDetailImageEditor,
    openEpisodePipelineFromAsset,
    openPreviewCacheDeletePanel,
    openRegistryAsset,
    openRegistryAssetFullscreenViewer,
    openRegistryAssetInBoard,
    openRegistryRelatedAsset,
    refreshDetailImageVersionsPanelDom,
    refreshRegistryAssetAfterImageVersion,
    refreshRegistryDetailPanelDom,
    removeRegistryAssetRelation,
    renderDetailImageEditor,
    renderDetailViewer,
    requestCloseDetailImageEditor,
    requestPreviewAllConfirm,
    selectDetailImageVersion,
    setDetailImageEditorTool,
    showDetailPreview,
    startAssetHoverPreview,
    startPreviewAllCache,
    stepDetailVideoFrame,
    stopAssetHoverPreview,
    syncDetailVideoControls,
    syncRegistryDetailGridWidth,
    toggleDetailPreviewPlayback,
    toggleDetailVideoFullscreen,
    toggleRegistryDetailPanel,
    ungroupDetailImageEditorLayers,
};

export {
    applyDetailImageEditorZoom,
    detailImageEditorBlob,
    detailImageEditorGroup,
    detailImageEditorSerialized,
    detailImageEditorTextBounds,
    detailImageEditorWorldPixels,
    detailTextVersionParams,
    detailViewerFileKey,
    detailViewerFontItems,
    detailViewerItems,
    detailViewerMetaRows,
    detailViewerSyncSelection,
    detailViewerTextKey,
    detailViewerTypeLabel,
    fitDetailFontLineWidth,
    formatDetailVideoTime,
    hydrateDetailRegistryAsset,
    loadDetailTextVersions,
    loadDetailViewerFileInfo,
    markAssetFloatingSurface,
    mergeDetailExportedRegistryAsset,
    navigateDetailPreview,
    openDetailImageEditor,
    positionDetailImageEditorEraserCursor,
    positionDetailMediaVideoDock,
    previewCacheStats,
    renderAssetDetail,
    renderDetailImageEditor,
    renderDetailMediaVideoControls,
    renderDetailViewer,
    renderDetailViewerInspectorContent,
    renderMediaNodeParametricSvg,
    renderPreviewCacheDeletePanel,
    renderRegistryDetailPanel,
    renderRegistryDetailResizer,
    scheduleDetailMediaVideoDockPosition,
    showDetailPreview,
    switchDetailImageEditorAsset,
};
