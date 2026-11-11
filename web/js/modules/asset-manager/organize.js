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

// QIN-591：资产库延后域模块（organize）。首屏不加载；经 GW_DEFERRED 注册函数面，
// 由主文件网关（gwCall/gwLoad）按需加载或在空闲时段预热。函数体除跨文件赋值点改写外保持原样。

import {
    ASSET_DETAIL_FONT_LIST_COLLAPSED_KEY,
    ASSET_LIBRARY_FOLDER_COLLAPSED_KEY,
    ASSET_LIBRARY_FOLDER_HIDDEN_KEY,
    ASSET_LIBRARY_PRESETS,
    ASSET_ORGANIZE_UNDO_LIMIT,
    BOARD_CREATE_PATH_FIELD_KEY,
    VERSION_CONFLICT_MESSAGE,
    activeAssetCategory,
    activeAssetCategoryId,
    activeAssetClassEntry,
    activeAssetClassFilter,
    activeAssetLibrary,
    activeAssetLibraryId,
    activeProjectId,
    activePromptCategories,
    activePromptCategory,
    activePromptLibrary,
    activePromptLibraryId,
    activeTab,
    activeWorkflowCategory,
    activeWorkflowCategoryId,
    activeWorkflowLibrary,
    activeWorkflowLibraryId,
    apiJsonResponse,
    applyRegistryAssetResponse,
    applyRegistryFilterDefinition,
    assetAllowedRootPaths,
    assetBoardById,
    assetCategories,
    assetClassificationSearchText,
    assetClassifyBusy,
    assetClipboard,
    assetFileExtension,
    assetKind,
    assetLibraries,
    assetLibrary,
    assetLibraryForItemId,
    assetLibraryVersionForItem,
    assetManageMode,
    assetMoveTarget,
    assetOrganizeAbsPath,
    assetOrganizeAskConflict,
    assetOrganizeDefaultRootId,
    assetOrganizeDialog,
    assetOrganizeDrag,
    assetOrganizeDropHint,
    assetOrganizeDropTargetFor,
    assetOrganizeDropZoneFor,
    assetOrganizeErrorText,
    assetOrganizeFolderLabel,
    assetOrganizeLeafName,
    assetOrganizeMenu,
    assetOrganizeOpenFolderInSystem,
    assetOrganizeParentPath,
    assetOrganizeRequest,
    assetOrganizeUndoLast,
    assetOrganizeUndoStack,
    assetOrganizeValidateFolderName,
    assetOrganizeWritableRoot,
    assetPreviewAllState,
    assetQuery,
    assetRackRoots,
    assetTabsEl,
    assetThumb,
    assetTreeExpanded,
    assetTreeSelectedKey,
    boardCollapsedFolders,
    boardCreateFocusTarget,
    boardCreateState,
    boardEditState,
    boardFolderCreateState,
    boardFolderSelection,
    boardGroupSelection,
    boardHiddenFolders,
    boardReferenceCanvasSelection,
    boardSourceSelection,
    boardSourceState,
    canBrowseServerDirectories,
    canvasAssetsData,
    closeAssetOrganizeMenu,
    customAssetLibraryBaseItems,
    customAssetLibraryItems,
    customBoardFolderRootIds,
    detailImageEditorState,
    detailMediaLibraryDragId,
    detailViewerFontListCollapsed,
    detailViewerState,
    detailViewerVideo,
    ensureAssetTreeProjectData,
    ensureBoardNavigationState,
    ensureBuiltinLutsRegistered,
    escapeAttr,
    escapeHtml,
    findAssetItem,
    findPromptItem,
    findWorkflowItem,
    gwSetAssetClipboard,
    isSystemPromptLibrary,
    loadAll,
    loadAssetAllowedRootPaths,
    loadLocalAssets,
    loadRegistryPrincipal,
    localAssetsLoaded,
    localLibraries,
    markIndexState,
    normalizeRegistryAsset,
    notifyShellTreeSnapshot,
    parseAssetClassFilterKey,
    pendingBatchDelete,
    pendingDeleteAssetId,
    pendingDeletePromptId,
    pendingTreeDelete,
    projectBoard,
    projects,
    promptCategoriesFor,
    promptCategoryVersion,
    promptCreateMode,
    promptEditMode,
    promptLibraries,
    promptLibrary,
    promptManageMode,
    promptQuery,
    promptTreeEdit,
    promptTreeFocus,
    readScriptWorkspaceAssets,
    refreshIcons,
    refreshRegistryAssets,
    refreshRegistryFacetData,
    registryActiveSelectedIds,
    registryArchiveBusy,
    registryAspect,
    registryAssetExtension,
    registryAssetFolderPath,
    registryAssetPrimaryPath,
    registryAssets,
    registryAssetsQueryCache,
    registryBoardCustomGroups,
    registryBoardGroupKey,
    registryCardSize,
    registryCustomGroups,
    registryDetail,
    registryDiskFolderCache,
    registryDiskFolderLoading,
    registryDiskFolderPermissionDenied,
    registryDiskFolderPermissionNoticed,
    registryExcludeTags,
    registryFacets,
    registryGroupSimilar,
    registryIncludeTags,
    registryLayout,
    registryLoading,
    registryPageHasMore,
    registryPdfBusy,
    registryPresets,
    registryProjectFilterId,
    registryQueryString,
    registryRequestSeq,
    registryRootFolders,
    registryRootFoldersLoading,
    registrySelectedIds,
    registrySimple,
    registryStatus,
    registryTotal,
    registryTotalKnown,
    registryVisibleAssetList,
    registryVisibleAssets,
    reindexAssetSource,
    render,
    renderAssetTreePanel,
    renderBoardNavigation,
    renderRegistryAssetSections,
    renderRegistryFilterChips,
    renderRegistryManager,
    renderRegistryPagination,
    renderRegistrySelectionBar,
    renderRegistryToolbar,
    requireExpectedVersion,
    resetRegistryScroll,
    root,
    saveAssetTreeState,
    saveBoardNavigationState,
    scriptArchiveItems,
    selectedAssetId,
    selectedAssetIds,
    selectedCanvasAssetId,
    selectedCanvasAssetIds,
    selectedLocalId,
    selectedLocalIds,
    selectedLocalUploadId,
    selectedLocalUploadIds,
    selectedProjectEntityId,
    selectedPromptId,
    selectedPromptIds,
    selectedRegistryAssetId,
    selectedWorkflowId,
    selectedWorkflowIds,
    serverDirectoryBrowserState,
    setActiveAssetCategoryId,
    setActiveAssetClassFilter,
    setActiveAssetLibraryId,
    setActiveLocalLibraryId,
    setActiveProjectId,
    setActivePromptCategory,
    setActivePromptLibraryId,
    setActiveTab,
    setActiveWorkflowCategoryId,
    setActiveWorkflowLibraryId,
    setAssetLibrary,
    setAssetMoveTarget,
    setAssetOrganizeDialog,
    setAssetOrganizeDrag,
    setAssetOrganizeDropHint,
    setAssetPreviewAllState,
    setAssetTreeEdit,
    setAssetTreeSelectedKey,
    setBoardCreateFocusTarget,
    setBoardCreateState,
    setBoardEditState,
    setBoardFolderCreateState,
    setBoardSourceState,
    setDetailMediaLibraryDragId,
    setDetailViewerFontListCollapsed,
    setPendingBatchDelete,
    setPendingDeleteAssetId,
    setPendingDeletePromptId,
    setPendingTreeDelete,
    setProjectBoard,
    setPromptCreateMode,
    setPromptEditMode,
    setPromptLibrary,
    setPromptTreeEdit,
    setRegistryArchiveBusy,
    setRegistryAssets,
    setRegistryBulkPanel,
    setRegistryDetail,
    setRegistryDiskFolderPermissionNoticed,
    setRegistryGroupMode,
    setRegistryKind,
    setRegistryLoading,
    setRegistryManageMode,
    setRegistryNavFolder,
    setRegistryPageLoading,
    setRegistryPdfBusy,
    setRegistryPresets,
    setRegistryProjectEntityFilter,
    setRegistryProjectFilterId,
    setRegistryQuery,
    setRegistryRackSnapshot,
    setRegistryRecentDays,
    setRegistryRequestSeq,
    setRegistryRootId,
    setRegistrySelectionAnchorId,
    setRegistryStatCategory,
    setRegistryStatus,
    setRegistryTotal,
    setRegistryTotalKnown,
    setSelectedAssetId,
    setSelectedAssetIds,
    setSelectedCanvasAssetId,
    setSelectedLocalId,
    setSelectedLocalUploadId,
    setSelectedProjectEntityId,
    setSelectedPromptId,
    setSelectedPromptIds,
    setSelectedRegistryAssetId,
    setSelectedWorkflowId,
    setSelectedWorkflowIds,
    setServerDirectoryBrowserState,
    setStatus,
    setWorkflowTreeEdit,
    storageCleanName,
    storageExtraLocalEntries,
    storageLocalLibraryNames,
    storageSettingsState,
    toggleSelectionSet,
    visibleAssetBoards,
    waitForAssetIndexJob,
    workflowCategories,
    workflowDeleteBusy,
    workflowDeleteVersions,
    workflowKindLabel,
    workflowManageMode,
    workflowQuery,
    workflowThumb,
    workflowTreeEdit,
} from '../asset-manager.js';
import assetManagerApi from './api.js';
import {
    formatDate,
} from './formatters.js';
import {
    pathCompareKey,
} from './path-utils.js';
import {
    workflowAssetId,
    workflowCategoryId,
    workflowLibraryId,
    workflowSourceId,
    zipWorkflowFiles,
} from './workflow-assets.js';
import {
    detailReferenceCurrentRootId,
} from './media-canvas.js';
import {
    detailImageEditorBlob,
    detailViewerFontItems,
    fitDetailFontLineWidth,
    formatDetailVideoTime,
    markAssetFloatingSurface,
    mergeDetailExportedRegistryAsset,
    renderAssetDetail,
    renderDetailImageEditor,
    renderDetailViewer,
    renderRegistryDetailPanel,
    renderRegistryDetailResizer,
} from './preview.js';
import {
    applyStorageSettingsData,
    clearAssetRackFolder,
    confirmRootAliasSuggestion,
    ensureAssetRackDirectoryIndex,
    ensureCreateLibraryPathAllowedRoot,
    loadAssetRackDiskFolders,
    loadAssetRackRegisteredFolders,
    renderServerDirectoryBrowser,
    renderStorageSettingsModal,
    resetAssetRackDirectoryIndex,
    saveDetailImageEditorVersion,
    saveRegistryCustomGroups,
    saveRegistryViewSettings,
    storageNewExtraId,
} from './settings.js';
import {
    renderLocalClipboardBar,
    renderUploadCard,
    renderWorkflowUploadCard,
} from './upload.js';
import { GW_DEFERRED } from './state.js';

// {kind:'create'|'delete'|'conflict', ...}
let assetOrganizeInline = null;
let assetOrganizeToastTimer = 0;
let boardCreateReturnFocus = null;
let promptItemSnapshot = {revision:0, items:[]};
let casRefreshInFlight = false;
function renderScriptAssetsManager(){
    const items = scriptArchiveItems.length ? scriptArchiveItems : readScriptWorkspaceAssets();
    const cards = items.map(item => {
        const title = escapeHtml(item.title || item.name || '未命名剧本');
        const date = escapeHtml(formatDate(item.createdAt));
        const preview = escapeHtml(String(item.content || '').replace(/\s+/g, ' ').slice(0, 180) || '暂无正文预览');
        const fileName = escapeHtml(item.name || '剧本.md');
        const projectId = escapeHtml(item.projectId || '未绑定项目');
        const itemId = escapeAttr(item.id || '');

        return `<article class="script-asset-card">
            <div class="script-card-header">
                <span class="script-format-badge">MD</span>
                <span class="script-date">${date}</span>
            </div>
            <div class="script-card-body">
                <h3 class="script-card-title">${title}</h3>
                <p class="script-card-preview">${preview}</p>
            </div>
            <div class="script-card-meta">
                <span class="script-meta-file"><i data-lucide="file-text"></i>${fileName}</span>
                <span class="script-meta-project"><i data-lucide="folder"></i>${projectId}</span>
            </div>
            <div class="script-card-actions">
                <button type="button" class="hw-btn hw-btn-gold" data-script-open="${itemId}">
                    <i data-lucide="pencil"></i><span>继续创作</span>
                </button>
                <button type="button" class="hw-btn" data-script-copy="${itemId}">
                    <i data-lucide="copy"></i><span>复制正文</span>
                </button>
            </div>
        </article>`;
    }).join('');

    root.innerHTML = `${renderAssetTreePanel()}}<section class="asset-workspace-panel script-assets-panel">
        <header class="asset-workspace-panel-head">
            <div>
                <span class="asset-panel-kicker">WRITING ARCHIVE</span>
                <h2>剧本资产</h2>
                <p>每次完成的剧本都会以 Markdown 版本归档，审核意见和剧集上下文保持可追踪。</p>
            </div>
            <button type="button" class="asset-primary-action" data-script-create>
                <i data-lucide="plus"></i><span>创作剧本</span>
            </button>
        </header>
        ${items.length
            ? `<div class="script-asset-grid">${cards}</div>`
            : `<div class="script-assets-empty">
                <i data-lucide="file-text"></i>
                <h3>还没有剧本资产</h3>
                <p>在剧集四步工作流中完成剧本并保存，Markdown 会自动出现在这里。</p>
                <button type="button" class="asset-primary-action" data-script-create>
                    <i data-lucide="plus"></i><span>创作第一份剧本</span>
                </button>
            </div>`}
    </section>`;
    refreshIcons();
}
function resetBoardSelections(){
    registrySelectedIds.clear();
    setSelectedRegistryAssetId('');
    setRegistryDetail(null);
    setRegistryManageMode(false);
    setRegistrySelectionAnchorId('');
    setRegistryBulkPanel('');
    selectedAssetIds.clear();
    selectedWorkflowIds.clear();
    selectedPromptIds.clear();
    selectedLocalIds.clear();
    selectedLocalUploadIds.clear();
    selectedCanvasAssetIds.clear();
}
async function activateAssetBoard(id){
    resetRegistryScroll();
    const board = assetBoardById(id);
    if(!board || ensureBoardNavigationState().hidden.includes(id)) return;
    const previousBoard = assetBoardById(activeTab);
    setActiveTab(id);
    setRegistryRackSnapshot(null);
    setRegistryRequestSeq(registryRequestSeq + 1);
    setRegistryPageLoading(false);
    resetBoardSelections();
    renderBoardNavigation();
    if(id === 'projects'){
        // 【QIN-572 AC2/AC4】进入项目视图：清空前置分类/标签筛选，初始化为全部项目素材
        setRegistryStatCategory('');
        setRegistryKind('');
        setRegistryQuery('');
        setRegistryRecentDays(0);
        registryIncludeTags.clear();
        registryExcludeTags.clear();
        setRegistryNavFolder({rootId:'', path:''});
        setRegistryRootId('');
        setRegistryProjectEntityFilter(null);
        if(!registryProjectFilterId) setRegistryProjectFilterId('__all__');
        const queryKey = registryQueryString();
        const cached = registryAssetsQueryCache.get(queryKey);
        if(cached) applyRegistryAssetResponse(cached);
        else { setRegistryAssets([]); setRegistryTotal(null); setRegistryTotalKnown(false); }
        setRegistryLoading(!cached);
        render();
        refreshRegistryAssets(false).then(() => { if(activeTab === id) render(); }).catch(err => setStatus(err.message || '项目素材加载失败'));
        return;
    }
    if(id !== 'projects' && registryProjectFilterId === '__all__'){
        setRegistryProjectFilterId('');
    }
    if(!board.system){
        // 【QIN-553】进入 LUT 库（含 lut 预设看板）时幂等登记内置 LUT 包。
        if(board.preset === 'lut' || (board.kinds || []).includes('lut')){
            try { await ensureBuiltinLutsRegistered(); } catch(err){ setStatus(err.message || '内置 LUT 登记失败'); }
        }
        const selectedSource = board.sourceBoard ? board.rootId : (boardSourceSelection[board.id] || '');
        applyRegistryFilterDefinition({category:board.category || '', rootId:selectedSource === '__automatic__' ? '' : selectedSource, groupSimilar:registryGroupSimilar});
        const queryKey = registryQueryString();
        const cached = registryAssetsQueryCache.get(queryKey);
        if(cached) applyRegistryAssetResponse(cached);
        else { setRegistryAssets([]); setRegistryTotal(null); setRegistryTotalKnown(false); }
        setRegistryLoading(!cached);
        render();
        loadCustomBoardFolderDirectories(board).catch(() => null);
        if(!cached){
            refreshRegistryAssets(false).then(() => { if(activeTab === id) render(); }).catch(err => setStatus(err.message || '绱犳潗搴撳姞杞藉け璐?'));
        }
        return;
    }
    if(id === 'lut'){
        // 【QIN-580】LUT 库是独立子页面：按 kind=lut 取数，客户端再按 .cube 收口。
        try { await ensureBuiltinLutsRegistered(); } catch(err){ setStatus(err.message || '内置 LUT 登记失败'); }
        applyRegistryFilterDefinition({kind:'lut'});
        const queryKey = registryQueryString();
        const cached = registryAssetsQueryCache.get(queryKey);
        if(cached) applyRegistryAssetResponse(cached);
        else { setRegistryAssets([]); setRegistryTotal(null); setRegistryTotalKnown(false); }
        setRegistryLoading(!cached);
        render();
        if(!cached){
            refreshRegistryAssets(false).then(() => { if(activeTab === id) render(); }).catch(err => setStatus(err.message || 'LUT 库加载失败'));
        }
        return;
    }
    // 【QIN-586 AC2】点击「资产总览」彻底清除库/目录/分类/标签筛选，回到资产库首页
    if(board.id === 'registry'){
        clearAssetRackFolder();
        setRegistryNavFolder({rootId:'', path:''});
        setRegistryRootId('');
        setRegistryStatCategory('');
        setRegistryProjectFilterId('');
        setRegistryProjectEntityFilter(null);
        setActiveProjectId('');
        setAssetTreeSelectedKey('registry');
        applyRegistryFilterDefinition({groupSimilar:registryGroupSimilar});
        const queryKey = registryQueryString();
        const cached = registryAssetsQueryCache.get(queryKey);
        if(cached) applyRegistryAssetResponse(cached);
        else { setRegistryAssets([]); setRegistryTotal(null); setRegistryTotalKnown(false); }
        setRegistryLoading(!cached);
        render();
        saveAssetTreeState();
        notifyShellTreeSnapshot();
        try {
            await refreshRegistryAssets(false);
            if(activeTab === id) render();
        } catch(err){
            setStatus(err.message || '资产库加载失败');
        }
        notifyShellTreeSnapshot();
        return;
    }
    if(board.id === 'local' && !localAssetsLoaded){
        const loading = loadLocalAssets();
        render();
        await loading;
        if(activeTab === id) render();
        return;
    }
    render();
}
function moveAssetBoardByKeyboard(id, direction){
    if(id === 'registry') return;
    const visible = visibleAssetBoards().map(item => item.id);
    const index = visible.indexOf(id);
    const target = visible[index + direction];
    if(index < 0 || !target || target === 'registry') return;
    const state = ensureBoardNavigationState();
    const sourceIndex = state.order.indexOf(id);
    const targetIndex = state.order.indexOf(target);
    if(sourceIndex < 1 || targetIndex < 1) return;
    [state.order[sourceIndex], state.order[targetIndex]] = [state.order[targetIndex], state.order[sourceIndex]];
    saveBoardNavigationState();
    renderBoardNavigation();
    requestAnimationFrame(() => assetTabsEl?.querySelector(`[data-tab="${CSS.escape(id)}"]`)?.focus());
}
function setAssetBoardHidden(id, hidden){
    if(id === 'registry') return;
    const state = ensureBoardNavigationState();
    const values = new Set(state.hidden);
    if(hidden) values.add(id); else values.delete(id);
    state.hidden = [...values];
    if(activeTab === id && hidden) setActiveTab('registry');
    saveBoardNavigationState();
    renderBoardNavigation();
    render();
}
async function removeAssetSourceRegistrations(sourceIds=[]){
    const ids = new Set((Array.isArray(sourceIds) ? sourceIds : [...sourceIds]).map(String).filter(Boolean));
    if(!ids.size) return null;
    const state = ensureBoardNavigationState();
    const referenced = new Set(state.customBoards.flatMap(item => (item.sources || []).map(source => String(source.id || ''))));
    const removableIds = new Set([...ids].filter(sourceId => !referenced.has(sourceId)));
    if(!removableIds.size) return null;
    const entries = storageExtraLocalEntries().filter(entry => !removableIds.has(String(entry.id || '')));
    const names = storageLocalLibraryNames();
    removableIds.forEach(sourceId => delete names[sourceId]);
    const data = await apiJsonResponse(assetManagerApi.updateStorageSettings({
        dirs:storageSettingsState.dirs || {},
        extra_local_entries:entries,
        local_library_names:names,
        local_library_order:localLibraries.map(lib => lib.id).filter(id => !removableIds.has(String(id))),
        expected_version:Number(storageSettingsState.revision || 1),
    }));
    applyStorageSettingsData(data || {});
    setRegistryAssets(registryAssets.filter(item => !removableIds.has(String(item.storage_root_id || ''))));
    registryFacets.roots = (registryFacets.roots || []).filter(root => !removableIds.has(String(root.id || '')));
    if(registryStatus?.overview?.roots){
        registryStatus.overview.roots = registryStatus.overview.roots.filter(root => !removableIds.has(String(root.id || '')));
    }
    registryAssetsQueryCache.clear();
    return removableIds;
}
async function deleteCustomAssetBoard(id){
    const state = ensureBoardNavigationState();
    const board = state.customBoards.find(item => item.id === id);
    if(!board || !window.confirm(`删除资源库“${board.name}”？仅移除该资源库看板定义，不会注销其素材来源路径；如需停止索引并注销路径，请在来源设置中单独移除来源。`)) return;
    state.customBoards = state.customBoards.filter(item => item.id !== id);
    state.order = state.order.filter(value => value !== id);
    state.hidden = state.hidden.filter(value => value !== id);
    if(activeTab === id) setActiveTab('registry');
    saveBoardNavigationState();
    // 【修复问题5】删除看板与注销素材来源解耦：删除只移除看板定义，已登记路径保留。
    // 保留的路径会以“已登记素材库”自动看板继续显示；注销路径请在来源设置中显式操作。
    renderBoardNavigation();
    renderStorageSettingsModal();
    render();
    setStatus(`已删除资源库“${board.name}”，素材来源路径已保留，可在来源设置中单独注销`);
}
async function removeGeneratedSourceBoard(rootId, name='素材库'){
    const sourceId = String(rootId || '').trim();
    if(!sourceId || !sourceId.startsWith('local')) return;
    if(!window.confirm(`移除素材库”${name}”？将停止该路径的索引并从资产中心隐藏其内容，不会删除源文件。`)) return;
    setStatus(`正在移除素材库“${name}”…`);
    try {
        const removed = await removeAssetSourceRegistrations([sourceId]);
        if(!removed?.size) throw new Error('素材来源未移除，请刷新后重试');
        if(activeTab === `source_library_${sourceId}`) setActiveTab('registry');
        renderBoardNavigation();
        renderStorageSettingsModal();
        render();
        setStatus(`已移除素材库”${name}”，索引任务已停止`);
    } catch(err){
        setStatus(err.message || '素材库路径移除失败');
    }
}
function togglePreviewDeleteCategory(id){
    const keys = ['image','video','storyboard','other'];
    const current = assetPreviewAllState.deleteCategories || {};
    const next = {...current};
    if(id === 'all'){
        const everyChecked = keys.every(key => current[key]);
        for(const key of keys) next[key] = !everyChecked;
    } else if(keys.includes(id)){
        next[id] = !current[id];
    } else {
        return;
    }
    setAssetPreviewAllState({...assetPreviewAllState, deleteCategories:next});
    renderStorageSettingsModal();
}
function renderCreateLibraryModal(){
    let overlay = document.getElementById('boardCreateOverlay');
    if(!boardCreateState.open){ overlay?.remove(); return; }
    const pathPicker = canBrowseServerDirectories()
        ? `<button type="button" class="hw-btn hw-btn-icon directory-picker-btn" data-directory-picker="${BOARD_CREATE_PATH_FIELD_KEY}" title="浏览服务器目录" aria-label="选择资源库路径"><i data-lucide="folder-open"></i></button>`
        : '';
    if(!overlay){
        overlay = document.createElement('div');
        overlay.id = 'boardCreateOverlay';
        overlay.className = 'storage-settings-overlay board-create-overlay';
        document.body.appendChild(overlay);
    }
    overlay.innerHTML = `<div class="board-create-modal" data-floating-content role="dialog" aria-modal="true" aria-labelledby="boardCreateTitle">
        <div class="storage-settings-head">
            <div><strong id="boardCreateTitle">新增资源库</strong><span>创建一个不限资源类型的独立看板。</span></div>
            <button type="button" data-board-create-close aria-label="关闭"><i data-lucide="x"></i></button>
        </div>
        <div class="board-create-form board-create-form-path">
            <label><span>资源库名称</span><input id="boardCreateName" maxlength="32" value="${escapeAttr(boardCreateState.name || '')}" placeholder="例如：品牌素材库"></label>
            <label class="board-create-path-field"><span>资源路径（可选）</span><div class="directory-input-row"><input id="boardCreatePath" list="boardKnownPaths" value="${escapeAttr(boardCreateState.path || '')}" placeholder="输入本机或 NAS 素材目录">${pathPicker}</div></label>
            <datalist id="boardKnownPaths">${localLibraries.map(lib => `<option value="${escapeAttr(lib.path || '')}">${escapeHtml(lib.name || '')}</option>`).join('')}</datalist>
            ${boardCreateState.error ? `<div class="board-create-error" role="alert"><i data-lucide="alert-triangle"></i><span>${escapeHtml(boardCreateState.error)}</span></div>` : ''}
        </div>
        <div class="board-create-actions"><button class="hw-btn" type="button" data-board-create-close ${boardCreateState.saving ? 'disabled' : ''}>取消</button><button class="hw-btn hw-btn-gold" type="button" data-board-create-confirm ${boardCreateState.saving ? 'disabled' : ''}><i data-lucide="${boardCreateState.saving ? 'loader' : 'plus'}"></i><span>${boardCreateState.saving ? '正在创建…' : '创建资源库'}</span></button></div>
    </div>`;
    markAssetFloatingSurface(overlay, {surface:'asset-board-create', content:'.board-create-modal', close:closeCreateLibraryModal});
    refreshIcons();
    const requestedFocus = boardCreateFocusTarget;
    setBoardCreateFocusTarget('');
    requestAnimationFrame(() => {
        const requested = requestedFocus ? document.getElementById(requestedFocus) : null;
        if(requested){ requested.focus(); return; }
        const name = document.getElementById('boardCreateName');
        const modal = document.querySelector('#boardCreateOverlay .board-create-modal');
        const active = document.activeElement;
        if(!name || !modal) return;
        if(active && modal.contains(active) && active !== name && active.matches?.('input, textarea, select, button')) return;
        name.focus();
    });
}
function createLibraryTrigger(){
    try {
        return window.parent?.document?.getElementById('assetSidebarCreate') || document.querySelector('#assetBoardTabs [data-board-create]');
    } catch(_) { return document.querySelector('#assetBoardTabs [data-board-create]'); }
}
async function openCreateLibraryModal(){
    await loadRegistryPrincipal();
    boardCreateReturnFocus = createLibraryTrigger();
    setBoardCreateState({open:true, preset:'other', name:'', nameManuallyEdited:false, path:'', saving:false, error:''});
    renderCreateLibraryModal();
}
function closeCreateLibraryModal(){
    const returnFocus = boardCreateReturnFocus;
    boardCreateReturnFocus = null;
    if(serverDirectoryBrowserState.key === BOARD_CREATE_PATH_FIELD_KEY){
        setServerDirectoryBrowserState({...serverDirectoryBrowserState, open:false, loading:false, saving:false});
        renderBoardCreatePathBrowser();
    }
    boardCreateState.open = false;
    boardCreateState.saving = false;
    boardCreateState.error = '';
    renderCreateLibraryModal();
    if(returnFocus?.isConnected) requestAnimationFrame(() => returnFocus.focus());
}
function renderBoardEditModal(){
    let overlay = document.getElementById('boardEditOverlay');
    if(!boardEditState.open){ overlay?.remove(); return; }
    const board = ensureBoardNavigationState().customBoards.find(item => item.id === boardEditState.boardId);
    if(!board){ boardEditState.open = false; overlay?.remove(); return; }
    if(!overlay){
        overlay = document.createElement('div');
        overlay.id = 'boardEditOverlay';
        overlay.className = 'storage-settings-overlay board-edit-overlay';
        document.body.appendChild(overlay);
    }
    overlay.innerHTML = `<div class="board-source-modal board-library-edit-modal" data-floating-content role="dialog" aria-modal="true" aria-labelledby="boardEditTitle">
        <div class="storage-settings-head"><div><strong id="boardEditTitle">修改资源库</strong><span>名称用于看板显示，路径用于素材索引。</span></div><button type="button" data-board-edit-close aria-label="关闭"><i data-lucide="x"></i></button></div>
        <div class="board-source-form board-library-edit-form">
            <label><span>资源库名称</span><input id="boardEditName" maxlength="32" value="${escapeAttr(boardEditState.name || board.name)}" placeholder="资源库名称"></label>
            <label><span>素材库路径</span><input id="boardEditPath" value="${escapeAttr(boardEditState.path || '')}" placeholder="D:\\Assets 或 NAS 共享目录"></label>
        </div>
        <div class="board-create-actions"><button class="hw-btn" type="button" data-board-edit-close>取消</button><button class="hw-btn hw-btn-gold" type="button" data-board-edit-save ${boardEditState.saving ? 'disabled' : ''}><i data-lucide="${boardEditState.saving ? 'loader-2' : 'save'}"></i><span>${boardEditState.saving ? '正在保存…' : '保存修改'}</span></button></div>
    </div>`;
    markAssetFloatingSurface(overlay, {surface:'asset-board-edit', content:'.board-library-edit-modal', close:closeBoardEditModal});
    refreshIcons();
    if(!boardEditState.saving) requestAnimationFrame(() => document.getElementById('boardEditName')?.focus());
}
function openBoardEditModal(boardId){
    const board = ensureBoardNavigationState().customBoards.find(item => item.id === boardId);
    if(!board) return;
    setBoardEditState({open:true, boardId, name:board.name || '', path:String(board.sources?.[0]?.path || ''), saving:false});
    renderBoardEditModal();
}
function closeBoardEditModal(){
    if(boardEditState.saving) return;
    boardEditState.open = false;
    renderBoardEditModal();
}
async function saveBoardEdit(){
    const board = ensureBoardNavigationState().customBoards.find(item => item.id === boardEditState.boardId);
    if(!board || boardEditState.saving) return;
    const name = String(document.getElementById('boardEditName')?.value || '').trim().slice(0,32);
    const path = String(document.getElementById('boardEditPath')?.value || '').trim();
    if(!name){ setStatus('请输入资源库名称'); return; }
    const source = board.sources?.[0] || null;
    if(source && !path){ setStatus('素材库路径不能为空'); return; }
    setBoardEditState({...boardEditState, name, path, saving:true});
    renderBoardEditModal();
    try {
        if(source && pathCompareKey(path) !== pathCompareKey(source.path)){
            const updated = await updateBoardSourcePath(source.id, `${name}素材`, path);
            if(updated) Object.assign(source, {name:updated.name, path:updated.path});
        } else if(!source && path){
            const created = await attachPathToAssetBoard(board.id, `${name}素材`, path);
            if(!created) throw new Error('素材路径添加失败');
            if(created.job_id){
                waitForAssetIndexJob(created.job_id, created.name).catch(() => {});
            }
        }
        board.name = name;
        saveBoardNavigationState();
        setBoardEditState({...boardEditState, open:false, saving:false});
        renderBoardEditModal();
        renderBoardNavigation();
        renderStorageSettingsModal();
        render();
        setStatus(`已更新资源库“${name}”`);
    } catch(err){
        setBoardEditState({...boardEditState, saving:false});
        renderBoardEditModal();
        setStatus(err.message || '资源库修改失败');
    }
}
function syncBoardCreateDraftFromDom(changedField=''){
    if(!boardCreateState.open) return;
    const name = document.getElementById('boardCreateName');
    const path = document.getElementById('boardCreatePath');
    if(!name && !path) return;
    const nameManuallyEdited = changedField === 'boardCreateName' || boardCreateState.nameManuallyEdited;
    const pathValue = path ? String(path.value || '') : boardCreateState.path;
    const nameValue = name ? String(name.value || '') : boardCreateState.name;
    const nextName = nameManuallyEdited ? nameValue : deriveBoardCreateNameFromPath(pathValue);
    setBoardCreateState({
        ...boardCreateState,
        name: nextName,
        nameManuallyEdited,
        path: pathValue,
    });
    if(!nameManuallyEdited && name && name.value !== nextName) name.value = nextName;
}
function deriveBoardCreateNameFromPath(pathValue){
    const path = String(pathValue || '').trim().replace(/[\\/]+$/, '');
    if(!path || /^[a-z]:$/i.test(path)) return '';
    return String(path.split(/[\\/]/).pop() || '').trim().slice(0, 32);
}
function updateBoardCreatePath(pathValue){
    const path = String(pathValue || '').trim();
    setBoardCreateState({
        ...boardCreateState,
        path,
        name:boardCreateState.nameManuallyEdited ? boardCreateState.name : deriveBoardCreateNameFromPath(path),
    });
}
async function createPresetAssetBoard(){
    if(boardCreateState.saving) return;
    const preset = ASSET_LIBRARY_PRESETS.find(item => item.id === 'other') || ASSET_LIBRARY_PRESETS[ASSET_LIBRARY_PRESETS.length - 1];
    syncBoardCreateDraftFromDom();
    const name = String(document.getElementById('boardCreateName')?.value || boardCreateState.name || '').trim().slice(0, 32);
    const path = String(document.getElementById('boardCreatePath')?.value || boardCreateState.path || '').trim();
    setBoardCreateState({...boardCreateState, name, path, error:''});
    if(!name){
        boardCreateState.error = '请输入资源库名称';
        renderCreateLibraryModal();
        setStatus('请输入资源库名称');
        return;
    }
    if(!boardCreateState.open){
        markIndexState('failed');
        setStatus('资源库窗口已关闭，未建立索引');
        return;
    }
    boardCreateState.saving = true;
    boardCreateState.error = '';
    renderCreateLibraryModal();
    let source = null;
    if(path){
        markIndexState('pending');
        setStatus('正在校验路径并登记素材来源…');
        try {
            const allowed = await ensureCreateLibraryPathAllowedRoot(path);
            if(!allowed){
                boardCreateState.saving = false;
                renderCreateLibraryModal();
                markIndexState('skipped');
                setStatus('已取消恢复，资源库未创建');
                return;
            }
            source = await attachPathToAssetBoard('', `${name}素材`, path);
        } catch(err) {
            boardCreateState.saving = false;
            boardCreateState.error = err.message || '素材库索引失败';
            renderCreateLibraryModal();
            markIndexState('failed');
            setStatus(err.message || '素材库索引失败');
            return;
        }
        if(!source){
            boardCreateState.saving = false;
            boardCreateState.error = '素材来源添加失败，未建立索引';
            renderCreateLibraryModal();
            markIndexState('failed');
            setStatus('素材来源添加失败，未建立索引');
            return;
        }
    }
    boardCreateState.saving = false;
    const state = ensureBoardNavigationState();
    const id = `library_${preset.id}_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 6)}`;
    state.customBoards.push({id, name, preset:preset.id, icon:preset.icon, category:preset.category || '', kinds:[...preset.kinds], extensions:[...preset.extensions], sources:source ? [{...source, hidden:false}] : [], automaticHidden:false, excludedSourceIds:[], system:false});
    state.order.push(id);
    if(source) boardSourceSelection[id] = source.id;
    saveBoardNavigationState();
    closeCreateLibraryModal();
    renderBoardNavigation();
    if(source){
        if(source.path && source.id) assetAllowedRootPaths.set(source.id, source.path);
        void loadAssetAllowedRootPaths().catch(() => null);
        void refreshRegistryFacetData().catch(() => null);
        if(source.job_id){
            markIndexState('pending');
            setStatus(`已创建资源库“${name}”，素材来源正在建立索引…`);
            waitForAssetIndexJob(source.job_id, source.name).catch(() => {});
        } else {
            markIndexState('indexed');
            setStatus(`已创建资源库“${name}”`);
        }
    } else {
        markIndexState('skipped');
        setStatus(`已创建资源库“${name}”，未填写路径，未建立索引`);
    }
    await activateAssetBoard(id);
}
function attachSourceRecordToBoard(boardId, source){
    const board = ensureBoardNavigationState().customBoards.find(item => item.id === boardId);
    if(!board || !source?.id) return;
    board.sources = Array.isArray(board.sources) ? board.sources : [];
    const existing = board.sources.find(item => item.id === source.id);
    if(existing) Object.assign(existing, source, {hidden:false});
    else board.sources.push({...source, hidden:false});
    board.excludedSourceIds = (board.excludedSourceIds || []).filter(id => id !== source.id);
    boardSourceSelection[boardId] = source.id;
    boardFolderSelection[boardId] = '';
    saveBoardNavigationState();
}
async function attachPathToAssetBoard(boardId, sourceName, path){
    const cleanPath = String(path || '').trim();
    if(!cleanPath) throw new Error('未填写素材路径，已跳过索引');
    const settings = await apiJsonResponse(assetManagerApi.getStorageSettings());
    applyStorageSettingsData(settings || {});
    const known = localLibraries.find(lib => pathCompareKey(lib.path) === pathCompareKey(cleanPath));
    let source = known ? {id:known.id, name:storageCleanName(sourceName || known.name, known.name), path:known.path} : null;
    let accepted = null;
    if(!source){
        let suggestionData = null;
        try {
            suggestionData = await apiJsonResponse(assetManagerApi.getRootAliasSuggestions({path:cleanPath}));
        } catch(_) {
            suggestionData = null;
        }
        const candidates = Array.isArray(suggestionData?.candidates) ? suggestionData.candidates : [];
        if(candidates.length){
            const choice = await confirmRootAliasSuggestion(cleanPath, candidates, suggestionData?.revision);
            if(!choice?.confirmation?.bound) return null;
            const saved = await apiJsonResponse(assetManagerApi.getStorageSettings());
            applyStorageSettingsData(saved || {});
            const registered = localLibraries.find(lib => String(lib.id) === String(choice.candidate.storage_root_id));
            source = {
                id:choice.candidate.storage_root_id,
                name:storageCleanName(sourceName || choice.candidate.name, choice.candidate.name || '用户素材'),
                path:registered?.path || cleanPath,
            };
        } else {
            const id = storageNewExtraId();
            const cleanName = storageCleanName(sourceName, '用户素材');
            const nonce = globalThis.crypto?.randomUUID?.() || `${Date.now()}-${Math.random().toString(36).slice(2)}`;
            setStatus(`正在校验并登记素材库“${cleanName}”…`);
            accepted = await apiJsonResponse(assetManagerApi.registerAssetRoot({
                background:true,
                hash_files:true,
                max_files:250000,
                roots:[id],
                register_root:{id, name:cleanName, path:cleanPath, expected_version:Number(storageSettingsState.revision || 1)},
            }, {headers:{'Idempotency-Key':`asset-register:${id}:${nonce}`}}));
            const saved = await apiJsonResponse(assetManagerApi.getStorageSettings());
            applyStorageSettingsData(saved || {});
            const created = localLibraries.find(lib => lib.id === id) || localLibraries.find(lib => pathCompareKey(lib.path) === pathCompareKey(cleanPath));
            source = {id:created?.id || id, name:created?.name || cleanName, path:created?.path || cleanPath};
        }
    }
    if(boardId) attachSourceRecordToBoard(boardId, source);
    if(accepted?.job_id){
        source.job_id = accepted.job_id;
    }
    return source;
}
function renderBoardSourceModal(){
    let overlay = document.getElementById('boardSourceOverlay');
    if(!boardSourceState.open){ overlay?.remove(); return; }
    const board = assetBoardById(boardSourceState.boardId);
    const editingSource = board?.sources?.find(source => source.id === boardSourceState.editor) || null;
    const editing = Boolean(editingSource);
    const showForm = !board || Boolean(boardSourceState.editor);
    if(!overlay){
        overlay = document.createElement('div');
        overlay.id = 'boardSourceOverlay';
        overlay.className = 'storage-settings-overlay board-create-overlay';
        document.body.appendChild(overlay);
    }
    overlay.innerHTML = `<div class="board-source-modal" data-floating-content role="dialog" aria-modal="true" aria-labelledby="boardSourceTitle">
        <div class="storage-settings-head"><div><strong id="boardSourceTitle">${board ? '素材来源设置' : '添加上传来源'}</strong><span>${board ? `设置“${escapeHtml(board.name)}”的素材路径与显示范围。` : '添加一个可上传和分类的素材目录。'}</span></div><button type="button" data-board-source-close aria-label="关闭"><i data-lucide="x"></i></button></div>
        ${board ? `<div class="board-source-settings-list">
            <div class="board-source-settings-row">
                <span class="board-source-settings-icon"><i data-lucide="sparkles"></i></span>
                <span class="board-source-settings-copy"><strong>自动归档</strong><small>按本资源库的素材类型自动收录</small></span>
                <button class="board-visibility-switch ${board.automaticHidden ? '' : 'is-on'}" type="button" role="switch" aria-checked="${!board.automaticHidden}" data-board-source-visibility="__automatic__" data-board-id="${escapeAttr(board.id)}"><span></span><em>${board.automaticHidden ? '隐藏' : '显示'}</em></button>
            </div>
            ${(board.sources || []).map(source => `<div class="board-source-settings-row ${source.hidden ? 'is-hidden' : ''}">
                <span class="board-source-settings-icon"><i data-lucide="folder"></i></span>
                <span class="board-source-settings-copy"><strong>${escapeHtml(source.name)}</strong><small title="${escapeAttr(source.path)}">${escapeHtml(source.path || '未设置路径')}</small></span>
                <button class="board-source-row-action" type="button" data-board-source-edit="${escapeAttr(source.id)}" title="更改素材路径"><i data-lucide="pencil"></i><span>更改</span></button>
                <button class="board-visibility-switch ${source.hidden ? '' : 'is-on'}" type="button" role="switch" aria-checked="${!source.hidden}" data-board-source-visibility="${escapeAttr(source.id)}" data-board-id="${escapeAttr(board.id)}"><span></span><em>${source.hidden ? '隐藏' : '显示'}</em></button>
                <button class="board-source-row-delete" type="button" data-board-source-delete="${escapeAttr(source.id)}" data-board-id="${escapeAttr(board.id)}" title="注销素材来源路径并停止索引（不会删除源文件）"><i data-lucide="trash-2"></i></button>
            </div>`).join('')}
            <button class="board-source-new" type="button" data-board-source-new><i data-lucide="folder-plus"></i><span>添加素材路径</span></button>
        </div>` : ''}
        ${showForm ? `<div class="board-source-form-wrap"><div class="board-source-form-head"><strong>${editing ? '更改素材路径' : '添加素材路径'}</strong><span>${editing ? '修改后会重新建立该来源的资产索引。' : '可选择已有来源，或创建新的本机 / NAS 路径。'}</span></div><div class="board-source-form">
            ${editing ? '' : `<label><span>已有来源</span><select id="boardSourceExisting"><option value="">新建素材路径</option>${localLibraries.map(lib => `<option value="${escapeAttr(lib.id)}" ${boardSourceState.existingId === lib.id ? 'selected' : ''}>${escapeHtml(lib.name)} · ${escapeHtml(lib.path || '')}</option>`).join('')}</select></label>`}
            <label><span>来源名称</span><input id="boardSourceName" maxlength="40" value="${escapeAttr(boardSourceState.name || editingSource?.name || '用户素材')}" placeholder="例如：NAS 品牌素材"></label>
            <label><span>素材路径</span><input id="boardSourcePath" value="${escapeAttr(boardSourceState.path || editingSource?.path || '')}" placeholder="D:\\Assets 或 NAS 共享目录"></label>
            ${editing ? `<label><span>来源只读</span><input id="boardSourceReadOnly" type="checkbox" ${storageSettingsState.localLibraryReadOnly?.[editingSource?.id] ? 'checked' : ''}><small>禁止将该来源素材移入回收站</small></label>` : ''}
        </div></div>` : ''}
        <div class="board-create-actions"><button class="hw-btn" type="button" ${showForm ? 'data-board-source-editor-cancel' : 'data-board-source-close'}>${showForm && board ? '取消编辑' : '关闭'}</button>${showForm ? `<button class="hw-btn hw-btn-gold" type="button" data-board-source-confirm><i data-lucide="${editing ? 'save' : 'folder-plus'}"></i><span>${editing ? '保存更改' : '添加来源'}</span></button>` : ''}</div>
    </div>`;
    markAssetFloatingSurface(overlay, {surface:'asset-board-source', content:'.board-source-modal', close:closeBoardSourceModal});
    refreshIcons();
}
function openBoardSourceModal(boardId='', editor=''){
    setBoardSourceState({open:true, boardId, editor:editor || (boardId ? '' : 'new'), existingId:'', name:boardId ? `${assetBoardById(boardId)?.name || ''}素材` : '上传素材', path:''});
    renderBoardSourceModal();
}
function closeBoardSourceModal(){ boardSourceState.open = false; renderBoardSourceModal(); }
function editBoardSource(sourceId){
    const board = assetBoardById(boardSourceState.boardId);
    const source = board?.sources?.find(item => item.id === sourceId);
    if(!source) return;
    setBoardSourceState({...boardSourceState, editor:source.id, existingId:'', name:source.name, path:source.path});
    renderBoardSourceModal();
}
function cancelBoardSourceEditor(){
    if(!boardSourceState.boardId){ closeBoardSourceModal(); return; }
    setBoardSourceState({...boardSourceState, editor:'', existingId:'', name:'', path:''});
    renderBoardSourceModal();
}
async function updateBoardSourcePath(sourceId, name, path, readOnly=false){
    const cleanPath = String(path || '').trim();
    if(!sourceId || !cleanPath) return null;
    const data = await apiJsonResponse(assetManagerApi.getStorageSettings());
    applyStorageSettingsData(data || {});
    const dirs = {...(storageSettingsState.dirs || {})};
    const entries = storageExtraLocalEntries();
    const names = {...storageLocalLibraryNames(), [sourceId]:storageCleanName(name, '用户素材')};
    if(sourceId === 'local') dirs.local = cleanPath;
    else {
        const entry = entries.find(item => item.id === sourceId);
        if(!entry) throw new Error('素材来源不存在，请重新添加');
        entry.name = storageCleanName(name, entry.name || '用户素材');
        entry.path = cleanPath;
        entry.kind = sourceId;
    }
    const saved = await apiJsonResponse(assetManagerApi.updateStorageSettings({
        dirs,
        extra_local_entries:entries,
        local_library_names:names,
        local_library_read_only:{...storageSettingsState.localLibraryReadOnly, [sourceId]:Boolean(readOnly)},
        local_library_order:localLibraries.map(lib => lib.id).filter(Boolean),
        expected_version:Number(storageSettingsState.revision || 1),
    }));
    applyStorageSettingsData(saved || {});
    const updated = localLibraries.find(item => item.id === sourceId) || {id:sourceId, name:storageCleanName(name, '用户素材'), path:cleanPath};
    ensureBoardNavigationState().customBoards.forEach(board => {
        const source = (board.sources || []).find(item => item.id === sourceId);
        if(source) Object.assign(source, {name:updated.name, path:updated.path});
    });
    saveBoardNavigationState();
    await reindexAssetSource(sourceId);
    return updated;
}
async function setBoardSourceVisible(boardId, sourceId){
    const board = assetBoardById(boardId);
    if(!board) return;
    if(sourceId === '__automatic__') board.automaticHidden = !board.automaticHidden;
    else {
        const source = (board.sources || []).find(item => item.id === sourceId);
        if(!source) return;
        source.hidden = !source.hidden;
        if(source.hidden && boardSourceSelection[boardId] === sourceId) boardSourceSelection[boardId] = '';
    }
    if(sourceId === '__automatic__' && board.automaticHidden && boardSourceSelection[boardId] === '__automatic__') boardSourceSelection[boardId] = '';
    boardFolderSelection[boardId] = '';
    saveBoardNavigationState();
    renderBoardSourceModal();
    if(activeTab === boardId){
        applyRegistryFilterDefinition({category:board.category || '', rootId:'', groupSimilar:registryGroupSimilar});
        await refreshRegistryAssets();
    }
}
async function deleteBoardSource(boardId, sourceId){
    const board = assetBoardById(boardId);
    const source = board?.sources?.find(item => item.id === sourceId);
    if(!board || !source || !window.confirm(`注销素材来源“${source.name}”？将停止该路径的索引并从资产中心移除其内容，源文件不受影响。`)) return;
    board.sources = board.sources.filter(item => item.id !== sourceId);
    board.excludedSourceIds = [...new Set([...(board.excludedSourceIds || []), sourceId])];
    if(boardSourceSelection[boardId] === sourceId) boardSourceSelection[boardId] = '';
    boardFolderSelection[boardId] = '';
    saveBoardNavigationState();
    renderBoardSourceModal();
    if(activeTab === boardId){
        applyRegistryFilterDefinition({category:board.category || '', rootId:'', groupSimilar:registryGroupSimilar});
    }
    try {
        await removeAssetSourceRegistrations([sourceId]);
        if(activeTab === boardId) await refreshRegistryAssets();
        else render();
        setStatus(`已从“${board.name}”删除来源“${source.name}”`);
    } catch(err) {
        board.sources = [...board.sources, source];
        board.excludedSourceIds = (board.excludedSourceIds || []).filter(id => id !== sourceId);
        saveBoardNavigationState();
        setStatus(err.message || '素材库路径移除失败');
        renderBoardSourceModal();
        if(activeTab === boardId) await refreshRegistryAssets();
    }
}
async function confirmBoardSource(){
    const existingId = String(document.getElementById('boardSourceExisting')?.value || '');
    const name = String(document.getElementById('boardSourceName')?.value || '用户素材').trim();
    const path = String(document.getElementById('boardSourcePath')?.value || '').trim();
    let source = null;
    const board = assetBoardById(boardSourceState.boardId);
    const editingSource = board?.sources?.find(item => item.id === boardSourceState.editor) || null;
    if(editingSource){
        if(!path){ setStatus('请输入素材路径'); return; }
        source = await updateBoardSourcePath(editingSource.id, name, path, Boolean(document.getElementById('boardSourceReadOnly')?.checked));
        if(!source) return;
        Object.assign(editingSource, {name:source.name, path:source.path});
        boardFolderSelection[board.id] = '';
        saveBoardNavigationState();
        setBoardSourceState({...boardSourceState, editor:'', name:'', path:''});
        renderBoardSourceModal();
        if(activeTab === board.id) await refreshRegistryAssets();
        setStatus(`已更新素材来源“${source.name}”`);
        return;
    }
    if(existingId){
        const lib = localLibraries.find(item => item.id === existingId);
        if(lib) source = {id:lib.id, name:storageCleanName(name || lib.name, lib.name), path:lib.path};
        if(boardSourceState.boardId && source) attachSourceRecordToBoard(boardSourceState.boardId, source);
        if(source) await reindexAssetSource(source.id);
    } else {
        if(!path){ setStatus('请输入素材路径'); return; }
        source = await attachPathToAssetBoard(boardSourceState.boardId, name, path);
    }
    if(!source) return;
    const boardId = boardSourceState.boardId;
    if(boardId){
        applyRegistryFilterDefinition({category:assetBoardById(boardId)?.category || '', rootId:source.id, groupSimilar:registryGroupSimilar});
        await refreshRegistryAssets();
        setBoardSourceState({...boardSourceState, editor:'', existingId:'', name:'', path:''});
        renderBoardSourceModal();
    } else {
        closeBoardSourceModal();
        setActiveLocalLibraryId(source.id);
        await loadLocalAssets();
        render();
    }
    setStatus(`已添加素材来源“${source.name}”，正在建立索引`);
    if(source?.job_id){
        waitForAssetIndexJob(source.job_id, source.name).catch(() => {});
    }
}
async function refreshCASReadState(){
    if(casRefreshInFlight) return;
    casRefreshInFlight = true;
    try {
        const results = await Promise.allSettled([
            apiJsonResponse(assetManagerApi.getAssetLibrary()),
            apiJsonResponse(assetManagerApi.getPromptLibraries()),
            apiJsonResponse(assetManagerApi.getPromptLibraryItems()),
            apiJsonResponse(assetManagerApi.getRegistryStatus()),
        ]);
        if(results[0].status === 'fulfilled') setAssetLibrary(results[0].value.library || assetLibrary);
        if(results[1].status === 'fulfilled') setPromptLibrary(results[1].value.library || promptLibrary);
        if(results[2].status === 'fulfilled') promptItemSnapshot = results[2].value;
        if(results[3].status === 'fulfilled') setRegistryStatus(results[3].value);
        await Promise.allSettled([refreshRegistryAssets(false), refreshRegistryFacetData()]);
        normalizeAssetState();
        render();
    } finally {
        casRefreshInFlight = false;
    }
}
function closeBoardCreatePathBrowser(){
    setServerDirectoryBrowserState({...serverDirectoryBrowserState, open:false, loading:false, saving:false});
    renderBoardCreatePathBrowser();
    requestAnimationFrame(() => document.querySelector(`[data-directory-picker="${BOARD_CREATE_PATH_FIELD_KEY}"]`)?.focus());
}
function renderBoardCreatePathBrowser(){
    let overlay = document.getElementById('boardCreatePathPickerOverlay');
    if(!serverDirectoryBrowserState.open || serverDirectoryBrowserState.key !== BOARD_CREATE_PATH_FIELD_KEY){ overlay?.remove(); return; }
    if(!overlay){
        overlay = document.createElement('div');
        overlay.id = 'boardCreatePathPickerOverlay';
        overlay.className = 'storage-settings-overlay board-create-path-picker-overlay';
        document.body.appendChild(overlay);
    }
    overlay.innerHTML = renderServerDirectoryBrowser();
    markAssetFloatingSurface(overlay, {surface:'asset-board-path-picker', content:'.server-directory-browser', close:closeBoardCreatePathBrowser});
    refreshIcons();
}
function expectedAssetLibraryVersions(assetIds){
    const versions = {};
    for(const assetId of new Set((assetIds || []).map(value => String(value || '')).filter(Boolean))){
        const library = assetLibraryForItemId(assetId);
        if(!library) throw new Error('素材已变化，请刷新');
        versions[library.id] = requireExpectedVersion(library.version, '素材库');
    }
    return versions;
}
function assetViewTitle(){
    const entry = activeAssetClassEntry();
    if(entry) return entry.tag || '智能分类';
    return activeAssetCategory()?.name || '图片资产';
}
function assetViewSubtitle(items){
    const libName = activeAssetLibrary()?.name || '资产库';
    const entry = activeAssetClassEntry();
    if(entry) return `${libName} / 智能分类：${entry.label} / ${items.length} 个素材`;
    return `${libName} / ${items.length} 个素材`;
}
function workflowCount(){
    return workflowCategories().reduce((sum, cat) => sum + ((cat.items || []).length), 0);
}
async function currentPromptItemSnapshot(){
    promptItemSnapshot = await apiJsonResponse(assetManagerApi.getPromptLibraryItems());
    return promptItemSnapshot;
}
async function currentPromptItemsRevision(){
    const snapshot = await currentPromptItemSnapshot();
    return requireExpectedVersion(snapshot.revision, '提示词条目集合');
}
async function promptItemVersion(itemId){
    const snapshot = await currentPromptItemSnapshot();
    const item = (snapshot.items || []).find(value => String(value.item_id || value.id || '') === String(itemId || ''));
    if(!item) throw new Error('提示词条目已变化，请刷新');
    return requireExpectedVersion(item.version, '提示词条目');
}
function promptCategoryLabel(category='custom'){
    const found = activePromptCategories().find(cat => cat.id === category);
    if(found?.name) return found.name;
    const map = {view:'视角', storyboard:'分镜', character:'角色', product:'产品', lighting:'光影', mine:'我的', custom:'我的'};
    return map[category] || category || '自定义';
}
function promptCountForCategory(category, lib=activePromptLibrary()){
    const items = lib?.items || [];
    if(category === 'all') return items.length;
    return items.filter(item => (item.category || 'custom') === category).length;
}
function assetItemsForClassFilter(key=activeAssetClassFilter){
    const parsed = parseAssetClassFilterKey(key);
    if(!parsed) return [];
    return assetCategories().flatMap(cat => cat.items || []).filter(item => {
        const flat = Array.isArray(item?.classification?.flat) ? item.classification.flat : [];
        return flat.some(entry => String(entry.dimension || '') === parsed.dimension && String(entry.tag || '') === parsed.tag);
    });
}
function assetKindLabel(item){
    const kind = assetKind(item);
    if(kind === 'video') return '视频';
    if(kind === 'audio') return '音频';
    if(kind === 'workflow') return '工作流';
    if(kind === 'text') return '文本';
    return '图片';
}
function currentAssetItems(){
    const query = assetQuery.trim().toLowerCase();
    const source = activeAssetClassFilter
        ? assetItemsForClassFilter(activeAssetClassFilter)
        : query
            ? assetCategories().flatMap(cat => cat.items || [])
            : (activeAssetCategory()?.items || []);
    return source.filter(item => {
        if(!query) return true;
        return [item.name, item.url, assetKindLabel(item), assetClassificationSearchText(item)].join(' ').toLowerCase().includes(query);
    });
}
function currentWorkflowItems(){
    const query = workflowQuery.trim().toLowerCase();
    return (activeWorkflowCategory()?.items || []).filter(item => {
        if(!query) return true;
        return [item.name, item.url, workflowKindLabel(item)].join(' ').toLowerCase().includes(query);
    });
}
function assetMoveTargets(){
    const currentKey = `${activeAssetLibraryId}::${activeAssetCategoryId}`;
    const targets = [];
    assetLibraries().forEach(lib => {
        (lib.categories || []).filter(cat => (cat.type || 'image') === 'image').forEach(cat => {
            const key = `${lib.id}::${cat.id}`;
            if(key !== currentKey) targets.push({key, libraryId:lib.id, categoryId:cat.id, label:`${lib.name || '资产库'} / ${cat.name || '分组'}`});
        });
    });
    return targets;
}
function normalizeAssetMoveTarget(){
    const targets = assetMoveTargets();
    if(!targets.some(item => item.key === assetMoveTarget)) setAssetMoveTarget(targets[0]?.key || '');
    return targets;
}
function currentPromptItems(){
    const lib = activePromptLibrary();
    const query = promptQuery.trim().toLowerCase();
    return (lib?.items || []).filter(item => {
        if(activePromptCategory !== 'all' && (item.category || 'custom') !== activePromptCategory) return false;
        if(!query) return true;
        return [item.name, item.scene, item.positive, item.negative, item.category].join(' ').toLowerCase().includes(query);
    });
}
function selectedAsset(){
    const items = currentAssetItems();
    return items.find(item => item.id === selectedAssetId) || items[0] || null;
}
function selectedWorkflow(){
    const items = currentWorkflowItems();
    return items.find(item => workflowAssetId(item) === selectedWorkflowId) || items[0] || null;
}
function selectedPrompt(){
    const items = currentPromptItems();
    return items.find(item => item.id === selectedPromptId) || items[0] || null;
}
function syncAssetSelectionDom(){
    document.querySelectorAll('[data-asset-card]').forEach(card => {
        card.classList.toggle('active', card.dataset.assetCard === selectedAssetId);
    });
    document.querySelectorAll('[data-asset-check]').forEach(input => {
        input.checked = selectedAssetIds.has(input.dataset.assetCheck);
    });
    const summary = root.querySelector('.asset-content .manage-summary');
    if(summary) summary.textContent = `已选 ${selectedAssetIds.size} 个，其中 ${selectedAssetImageItems().length} 张图片`;
    const detail = root.querySelector('.asset-detail');
    if(detail){ detail.innerHTML = renderAssetDetail(selectedAsset()); refreshIcons(); }
}
function syncWorkflowSelectionDom(){
    document.querySelectorAll('[data-workflow-card]').forEach(card => {
        card.classList.toggle('active', card.dataset.workflowCard === selectedWorkflowId);
    });
    document.querySelectorAll('[data-workflow-check]').forEach(input => {
        input.checked = selectedWorkflowIds.has(input.dataset.workflowCheck);
    });
    const summary = root.querySelector('.asset-content .manage-tools > span');
    if(summary) summary.textContent = `已选择 ${selectedWorkflowIds.size} 个工作流。`;
    const detail = root.querySelector('.asset-detail');
    if(detail){ detail.innerHTML = renderWorkflowDetail(selectedWorkflow()); refreshIcons(); }
}
function applyManagedSelection(kind, id){
    if(kind === 'asset'){
        const selected = toggleSelectionSet(selectedAssetIds, id);
        setSelectedAssetId(selected ? id : (selectedAssetId === id ? '' : selectedAssetId));
    } else if(kind === 'localup'){
        const selected = toggleSelectionSet(selectedLocalUploadIds, id);
        setSelectedLocalUploadId(selected ? id : (selectedLocalUploadId === id ? '' : selectedLocalUploadId));
    } else if(kind === 'local'){
        const selected = toggleSelectionSet(selectedLocalIds, id);
        setSelectedLocalId(selected ? id : (selectedLocalId === id ? '' : selectedLocalId));
    } else if(kind === 'workflow'){
        const selected = toggleSelectionSet(selectedWorkflowIds, id);
        setSelectedWorkflowId(selected ? id : (selectedWorkflowId === id ? '' : selectedWorkflowId));
    } else if(kind === 'prompt'){
        const selected = toggleSelectionSet(selectedPromptIds, id);
        setSelectedPromptId(selected ? id : (selectedPromptId === id ? '' : selectedPromptId));
        setPromptEditMode(false);
        setPromptCreateMode(false);
    } else if(kind === 'canvasAsset'){
        const selected = toggleSelectionSet(selectedCanvasAssetIds, id);
        setSelectedCanvasAssetId(selected ? id : (selectedCanvasAssetId === id ? '' : selectedCanvasAssetId));
    }
    setPendingBatchDelete('');
}
async function deleteRegistryPreset(id){
    await apiJsonResponse(assetManagerApi.deleteRegistryPreset(id));
    setRegistryPresets(registryPresets.filter(item => item.id !== id));
    render();
}
function addSelectedRegistryAssetsToGroup(board=assetBoardById(activeTab)){
    const selectedIds = registryActiveSelectedIds(board);
    if(!selectedIds.length){ setStatus('请先按 Ctrl 或 Shift 选择素材'); return; }
    const boardKey = registryBoardGroupKey(board);
    const groups = registryBoardCustomGroups(board);
    const name = String(window.prompt('输入自定义分组名称；同名分组会继续追加素材', '') || '').trim().slice(0, 40);
    if(!name) return;
    let group = groups.find(item => item.name.toLocaleLowerCase() === name.toLocaleLowerCase());
    if(!group){
        group = {id:`group_${Date.now().toString(36)}_${Math.random().toString(36).slice(2,6)}`, name, assetIds:[]};
        groups.push(group);
    }
    groups.forEach(item => { item.assetIds = (item.assetIds || []).filter(id => !selectedIds.includes(id)); });
    group.assetIds = [...new Set([...(group.assetIds || []), ...selectedIds])];
    registryCustomGroups[boardKey] = groups;
    if(board?.id){
        boardGroupSelection[board.id] = group.id;
        boardFolderSelection[board.id] = '';
    }
    setRegistryGroupMode('custom');
    saveRegistryCustomGroups();
    saveRegistryViewSettings();
    render();
    setStatus(`已将 ${selectedIds.length} 个素材加入“${group.name}”`);
}
function removeSelectedRegistryAssetsFromGroups(board=assetBoardById(activeTab)){
    const selectedIds = new Set(registryActiveSelectedIds(board));
    if(!selectedIds.size){ setStatus('请先选择已分组素材'); return; }
    const boardKey = registryBoardGroupKey(board);
    const groups = registryBoardCustomGroups(board);
    let removed = 0;
    groups.forEach(group => {
        const before = (group.assetIds || []).length;
        group.assetIds = [...new Set((group.assetIds || []).filter(id => !selectedIds.has(id)))];
        removed += before - group.assetIds.length;
    });
    if(!removed){ setStatus('所选素材不在自定义分组中'); return; }
    registryCustomGroups[boardKey] = groups;
    saveRegistryCustomGroups();
    render();
    setStatus(`已将 ${removed} 个分组项移出自定义分组`);
}
function registryDownloadName(response, fallback='资产打包.zip'){
    const custom = response.headers.get('x-archive-filename') || '';
    if(custom){ try { return decodeURIComponent(custom); } catch(_) {} }
    const disposition = response.headers.get('content-disposition') || '';
    const encoded = disposition.match(/filename\*=UTF-8''([^;]+)/i)?.[1];
    if(encoded){ try { return decodeURIComponent(encoded); } catch(_) {} }
    const plain = disposition.match(/filename="?([^";]+)"?/i)?.[1];
    return plain || fallback;
}
async function downloadSelectedRegistryArchive(board=assetBoardById(activeTab)){
    const assetIds = registryActiveSelectedIds(board);
    if(!assetIds.length || registryArchiveBusy) return;
    setRegistryArchiveBusy(true);
    render();
    setStatus(`正在打包 ${assetIds.length} 个素材...`);
    try {
        const response = await assetManagerApi.archiveRegistryAssets({asset_ids:assetIds, name:`${board?.name || '资产中心'}-${new Date().toISOString().slice(0,10)}`});
        if(!response.ok){
            const detail = await response.json().catch(() => ({}));
            throw new Error(detail.detail || `打包失败（${response.status}）`);
        }
        const blob = await response.blob();
        const url = URL.createObjectURL(blob);
        const link = document.createElement('a');
        link.href = url;
        link.download = registryDownloadName(response);
        document.body.appendChild(link);
        link.click();
        link.remove();
        setTimeout(() => URL.revokeObjectURL(url), 1000);
        setStatus(`已导出 ${assetIds.length} 个素材`);
    } finally {
        setRegistryArchiveBusy(false);
        render();
    }
}
async function downloadRegistryPdf(assetIds=[],name='资产导出'){
    const ids = [...new Set((assetIds || []).map(String).filter(Boolean))];
    if(!ids.length || registryPdfBusy) return;
    setRegistryPdfBusy(true);
    render();
    if(detailViewerState.open) renderDetailViewer();
    setStatus(`正在将 ${ids.length} 个文本/图片素材导出为 PDF…`);
    try {
        const response = await assetManagerApi.exportRegistryPdf({asset_ids:ids,name});
        if(!response.ok){
            const detail = await response.json().catch(() => ({}));
            throw new Error(detail.detail || `PDF 导出失败（${response.status}）`);
        }
        const blob = await response.blob();
        const url = URL.createObjectURL(blob);
        const link = document.createElement('a');
        link.href = url;
        link.download = registryDownloadName(response,`${name || '资产导出'}.pdf`);
        document.body.appendChild(link); link.click(); link.remove();
        setTimeout(() => URL.revokeObjectURL(url),1000);
        const exported = Number(response.headers.get('x-pdf-exported') || ids.length);
        const skipped = Number(response.headers.get('x-pdf-skipped') || 0);
        setStatus(`PDF 已导出 ${exported} 项${skipped ? `，跳过 ${skipped} 项不支持或不可用素材` : ''}`);
    } finally {
        setRegistryPdfBusy(false);
        render();
        if(detailViewerState.open) renderDetailViewer();
    }
}
async function loadProjectBoard(projectId=activeProjectId, renderNow=true){
    setActiveProjectId(projectId || '');
    if(!activeProjectId){ setProjectBoard(null); if(renderNow) render(); return; }
    setProjectBoard(await apiJsonResponse(assetManagerApi.getRegistryProject(activeProjectId)));
    const entities = projectBoard?.entities || [];
    if(selectedProjectEntityId && !entities.some(item => item.id === selectedProjectEntityId)) setSelectedProjectEntityId('');
    if(!selectedProjectEntityId && entities.length) setSelectedProjectEntityId(entities[0].id);
    if(renderNow) render();
}
function normalizeAssetState(){
    const libs = assetLibraries();
    if(!activeAssetLibraryId || !libs.some(lib => lib.id === activeAssetLibraryId)) setActiveAssetLibraryId(assetLibrary.active_library_id || libs[0]?.id || '');
    const cats = assetCategories();
    if(!activeAssetCategoryId || !cats.some(cat => cat.id === activeAssetCategoryId)) setActiveAssetCategoryId(cats[0]?.id || '');
    if(activeAssetClassFilter && !activeAssetClassEntry()) setActiveAssetClassFilter('');
    const items = currentAssetItems();
    if(selectedAssetId && !items.some(item => item.id === selectedAssetId)) setSelectedAssetId('');
    if(!selectedAssetId && items.length) setSelectedAssetId(items[0].id);
    setSelectedAssetIds(new Set([...selectedAssetIds].filter(id => findAssetItem(id))));
}
function normalizeWorkflowState(){
    const libs = assetLibraries();
    if(!activeWorkflowLibraryId || !libs.some(lib => workflowLibraryId(lib) === activeWorkflowLibraryId)) setActiveWorkflowLibraryId(assetLibrary.active_library_id || workflowLibraryId(libs[0]));
    const cats = workflowCategories();
    if(
        !activeWorkflowCategoryId
        || !cats.some(cat => workflowCategoryId(cat) === activeWorkflowCategoryId && cat.__libraryId === activeWorkflowLibraryId)
    ){
        setActiveWorkflowCategoryId(workflowCategoryId(cats[0]));
        setActiveWorkflowLibraryId(cats[0]?.__libraryId || activeWorkflowLibraryId);
    }
    const activeCat = cats.find(cat => workflowCategoryId(cat) === activeWorkflowCategoryId && cat.__libraryId === activeWorkflowLibraryId) || null;
    if(activeCat?.__libraryId) setActiveWorkflowLibraryId(activeCat.__libraryId);
    const items = currentWorkflowItems();
    if(selectedWorkflowId && !items.some(item => workflowAssetId(item) === selectedWorkflowId)) setSelectedWorkflowId('');
    if(!selectedWorkflowId && items.length) setSelectedWorkflowId(workflowAssetId(items[0]));
    setSelectedWorkflowIds(new Set([...selectedWorkflowIds].filter(id => findWorkflowItem(id))));
}
function normalizePromptState(){
    const libs = promptLibraries();
    if(!activePromptLibraryId || !libs.some(lib => lib.id === activePromptLibraryId)) setActivePromptLibraryId(promptLibrary.active_library_id || libs[0]?.id || '');
    const cats = activePromptCategories();
    if(activePromptCategory !== 'all' && !cats.some(cat => cat.id === activePromptCategory)) setActivePromptCategory('all');
    const items = currentPromptItems();
    if(selectedPromptId && !items.some(item => item.id === selectedPromptId)) setSelectedPromptId('');
    if(!selectedPromptId && items.length) setSelectedPromptId(items[0].id);
    setSelectedPromptIds(new Set([...selectedPromptIds].filter(id => findPromptItem(id))));
}
function clearSearchSelection(id){
    if(id === 'registrySearch') { setSelectedRegistryAssetId(''); setRegistryDetail(null); }
    else if(id === 'assetSearch') setSelectedAssetId('');
    else if(id === 'workflowSearch') setSelectedWorkflowId('');
    else if(id === 'promptSearch') setSelectedPromptId('');
    else if(id === 'localSearch') setSelectedLocalId('');
    else if(id === 'localUploadSearch') setSelectedLocalUploadId('');
    else if(id === 'canvasAssetSearch') setSelectedCanvasAssetId('');
}
function renderHeadTreeInlineEdit(edit, inputId, saveAttr, cancelAttr){
    if(!edit || edit.placement !== 'head') return '';
    const label = edit.label || '名称';
    return `<div class="head-inline-edit">
        <input id="${escapeAttr(inputId)}" type="text" value="${escapeAttr(edit.value || '')}" placeholder="${escapeAttr(label)}">
        <button type="button" class="primary" ${saveAttr} title="保存"><i data-lucide="check"></i><span>保存</span></button>
        <button type="button" ${cancelAttr} title="取消"><i data-lucide="x"></i><span>取消</span></button>
    </div>`;
}
function selectedAssetImageItems(){
    return [...selectedAssetIds].map(id => findAssetItem(id)).filter(item => item && assetKind(item) === 'image');
}
function saveHiddenLibraryFolders(){
    try { localStorage.setItem(ASSET_LIBRARY_FOLDER_HIDDEN_KEY, JSON.stringify(boardHiddenFolders)); }
    catch(_) {}
}
async function loadCustomBoardFolderDirectories(board, force=false){
    const baseItems = customAssetLibraryBaseItems(board);
    const rootIds = customBoardFolderRootIds(board, baseItems);
    await Promise.all(rootIds.map(async rootId => {
        if(registryRootFoldersLoading.has(rootId) || (!force && registryRootFolders.has(rootId))) return;
        registryRootFoldersLoading.add(rootId);
        try {
            const data = await apiJsonResponse(assetManagerApi.getRegistryFolders(rootId));
            registryRootFolders.set(rootId, Array.isArray(data.folders) ? data.folders : []);
        } catch(_) {
            if(!registryRootFolders.has(rootId)) registryRootFolders.set(rootId, []);
        } finally {
            registryRootFoldersLoading.delete(rootId);
        }
    }));
    // 磁盘真实子目录（含空目录）懒加载；自动归档聚合各素材根的一层目录
    const diskResults = await Promise.all([
        ...rootIds.map(rootId => loadAssetRackDiskFolders(rootId)),
        loadAssetRackDiskFolders('__automatic__'),
    ]);
    // QIN-542 AC4：403 提示条需要一次强制重绘（可能其他结果全为空），noticed 只放行一次
    if(registryDiskFolderPermissionDenied && !registryDiskFolderPermissionNoticed){
        setRegistryDiskFolderPermissionNoticed(true);
        if(activeTab === board?.id) render();
        return;
    }
    if(activeTab === board?.id && diskResults.some(entries => entries.length)) render();
}
function saveCollapsedLibraryFolders(){
    try { localStorage.setItem(ASSET_LIBRARY_FOLDER_COLLAPSED_KEY, JSON.stringify(boardCollapsedFolders)); }
    catch(_) {}
}
function customBoardWritableRoots(board, parent=''){
    const baseItems = customAssetLibraryBaseItems(board);
    const candidates = customBoardFolderRootIds(board, baseItems);
    const statusRoots = new Map((registryStatus?.overview?.roots || []).map(root => [String(root.id || ''), root]));
    const facetRoots = new Map((registryFacets.roots || []).map(root => [String(root.id || ''), root]));
    const boardSources = new Map((board?.sources || []).map(source => [String(source.id || ''), source]));
    const writable = candidates.map(rootId => {
        const statusRoot = statusRoots.get(rootId) || {};
        const facetRoot = facetRoots.get(rootId) || {};
        const source = boardSources.get(rootId) || {};
        return {
            id:rootId,
            name:String(source.name || statusRoot.name || facetRoot.name || rootId),
            path:String(source.path || statusRoot.path || ''),
            readOnly:String(rootId).startsWith('shared_') || Boolean(statusRoot.read_only),
        };
    }).filter(root => !root.readOnly);
    const cleanParent = String(parent || '').replace(/\\/g, '/').replace(/^\/+|\/+$/g, '');
    if(!cleanParent || writable.length < 2) return writable;
    const parentRootIds = new Set();
    writable.forEach(root => {
        if((registryRootFolders.get(root.id) || []).some(folder => String(folder?.path || '') === cleanParent)) parentRootIds.add(root.id);
    });
    baseItems.forEach(item => {
        const folder = registryAssetFolderPath(item);
        if(folder === cleanParent || folder.startsWith(`${cleanParent}/`)) parentRootIds.add(String(item.storage_root_id || ''));
    });
    const matching = writable.filter(root => parentRootIds.has(root.id));
    return matching.length ? matching : writable;
}
function renderCustomBoardFolderCreateModal(){
    let overlay = document.getElementById('boardFolderCreateOverlay');
    if(!boardFolderCreateState.open){ overlay?.remove(); return; }
    const board = assetBoardById(boardFolderCreateState.boardId);
    if(!board){ boardFolderCreateState.open = false; overlay?.remove(); return; }
    const roots = customBoardWritableRoots(board, boardFolderCreateState.parent);
    if(!roots.some(root => root.id === boardFolderCreateState.rootId)) boardFolderCreateState.rootId = roots[0]?.id || '';
    const selectedRoot = roots.find(root => root.id === boardFolderCreateState.rootId) || roots[0] || null;
    const parentLabel = boardFolderCreateState.parent || '根目录';
    const unavailableMessage = roots.length ? '' : '当前资源库没有可写素材来源';
    const error = boardFolderCreateState.error || unavailableMessage;
    if(!overlay){
        overlay = document.createElement('div');
        overlay.id = 'boardFolderCreateOverlay';
        overlay.className = 'storage-settings-overlay board-edit-overlay';
        document.body.appendChild(overlay);
    }
    overlay.innerHTML = `<div class="board-source-modal board-library-edit-modal" data-floating-content role="dialog" aria-modal="true" aria-labelledby="boardFolderCreateTitle">
        <div class="storage-settings-head"><div><strong id="boardFolderCreateTitle">新建文件夹</strong><span>在“${escapeHtml(board.name)}”的当前目录中创建实体文件夹。</span></div><button type="button" data-board-folder-create-close aria-label="关闭" ${boardFolderCreateState.saving ? 'disabled' : ''}><i data-lucide="x"></i></button></div>
        <div class="board-source-form board-library-edit-form">
            <label><span>文件夹名称</span><input id="boardFolderCreateName" maxlength="120" value="${escapeAttr(boardFolderCreateState.name || '')}" placeholder="新文件夹" ${boardFolderCreateState.saving ? 'disabled' : ''}></label>
            <label><span>存储来源</span>${roots.length > 1
                ? `<select id="boardFolderCreateRoot" ${boardFolderCreateState.saving ? 'disabled' : ''}>${roots.map(root => `<option value="${escapeAttr(root.id)}" ${root.id === selectedRoot?.id ? 'selected' : ''}>${escapeHtml(root.name)}${root.path ? ` · ${escapeHtml(root.path)}` : ''}</option>`).join('')}</select>`
                : `<input value="${escapeAttr(selectedRoot ? `${selectedRoot.name}${selectedRoot.path ? ` · ${selectedRoot.path}` : ''}` : '无可写来源')}" readonly>`}
            </label>
            <label><span>当前父目录</span><input value="${escapeAttr(parentLabel)}" readonly></label>
        </div>
        ${error ? `<div class="thumbnail-cache-error">${escapeHtml(error)}</div>` : ''}
        <div class="board-create-actions"><button class="hw-btn" type="button" data-board-folder-create-close ${boardFolderCreateState.saving ? 'disabled' : ''}>取消</button><button class="hw-btn hw-btn-gold" type="button" data-board-folder-create-confirm ${boardFolderCreateState.saving || !selectedRoot ? 'disabled' : ''}><i data-lucide="${boardFolderCreateState.saving ? 'loader-2' : 'folder-plus'}"></i><span>${boardFolderCreateState.saving ? '正在创建…' : '创建文件夹'}</span></button></div>
    </div>`;
    markAssetFloatingSurface(overlay, {surface:'asset-board-folder-create', content:'.board-library-edit-modal', close:closeCustomBoardFolderCreateModal});
    refreshIcons();
    if(!boardFolderCreateState.saving) requestAnimationFrame(() => {
        const input = document.getElementById('boardFolderCreateName');
        input?.focus();
        input?.select();
    });
}
function openCustomBoardFolderCreateModal(boardId){
    const board = assetBoardById(boardId);
    if(!board){ setStatus('当前资源库不存在'); return; }
    const selected = boardFolderSelection[board.id] || '';
    const parent = selected && selected !== '__root__' ? selected : '';
    const roots = customBoardWritableRoots(board, parent);
    const preferredIds = [
        board.sourceBoard ? String(board.rootId || '') : '',
        String(boardSourceSelection[board.id] || ''),
        String(board.sources?.[0]?.id || ''),
    ];
    const rootId = preferredIds.find(id => roots.some(root => root.id === id)) || roots[0]?.id || '';
    setBoardFolderCreateState({
        open:true, boardId:board.id, rootId, parent, name:'新文件夹', saving:false,
        error:roots.length ? '' : '当前资源库没有可写素材来源',
    });
    renderCustomBoardFolderCreateModal();
    if(!roots.length) setStatus(boardFolderCreateState.error);
}
function closeCustomBoardFolderCreateModal(){
    if(boardFolderCreateState.saving) return;
    setBoardFolderCreateState({...boardFolderCreateState, open:false, error:''});
    renderCustomBoardFolderCreateModal();
}
async function submitCustomBoardFolderCreate(){
    if(!boardFolderCreateState.open || boardFolderCreateState.saving) return;
    const board = assetBoardById(boardFolderCreateState.boardId);
    const name = String(document.getElementById('boardFolderCreateName')?.value || '').trim().slice(0, 120);
    const rootId = String(document.getElementById('boardFolderCreateRoot')?.value || boardFolderCreateState.rootId || '');
    const fail = message => {
        setBoardFolderCreateState({...boardFolderCreateState, name, rootId, error:message});
        renderCustomBoardFolderCreateModal();
        setStatus(message);
    };
    if(!board){ fail('当前资源库不存在'); return; }
    if(!name){ fail('请输入文件夹名称'); return; }
    if(!rootId){ fail('当前资源库没有可写素材来源'); return; }
    setBoardFolderCreateState({...boardFolderCreateState, name, rootId, saving:true, error:''});
    renderCustomBoardFolderCreateModal();
    try {
        const data = await apiJsonResponse(assetManagerApi.createRegistryFolder({root_id:rootId,parent:boardFolderCreateState.parent,name}));
        registryRootFolders.set(rootId, Array.isArray(data.folders) ? data.folders : []);
        const createdName = data.folder?.name || name;
        const parent = boardFolderCreateState.parent;
        if(parent){
            boardCollapsedFolders[board.id] = (boardCollapsedFolders[board.id] || []).filter(path => path !== parent);
            saveCollapsedLibraryFolders();
        }
        setBoardFolderCreateState({...boardFolderCreateState, open:false, saving:false, error:''});
        renderCustomBoardFolderCreateModal();
        render();
        setStatus(`已在${parent ? `“${parent}”中` : '根目录'}新建文件夹“${createdName}”`);
    } catch(err){
        const message = err.message || '新建文件夹失败';
        setBoardFolderCreateState({...boardFolderCreateState, saving:false, error:message});
        renderCustomBoardFolderCreateModal();
        setStatus(message);
    }
}
async function revealRegistryAssetFile(itemOrId){
    const item = typeof itemOrId === 'string'
        ? (registryDetail?.id === itemOrId ? registryDetail : registryAssets.find(value => value.id === itemOrId))
        : itemOrId;
    if(!item?.id) throw new Error('未找到对应素材');
    const path = registryAssetPrimaryPath(item);
    if(!path || item.is_remote) throw new Error('当前素材没有可用的本地文件路径');
    await apiJsonResponse(assetManagerApi.revealAssetFile({asset_id:item.id,path}));
    setStatus('已在文件夹中定位素材');
}
function showAssetOrganizeToast(text, {undoable=false}={}){
    // AC4：整理结果用独立 toast 呈现；toast 挂在 body 上，按钮由文档级委托处理
    document.getElementById('assetOrganizeToast')?.remove();
    const host = document.createElement('div');
    host.id = 'assetOrganizeToast';
    host.className = 'asset-organize-toast';
    host.setAttribute('role', 'status');
    host.innerHTML = `<i data-lucide="folder-cog"></i><span>${escapeHtml(text)}</span>${undoable ? '<button type="button" class="asset-organize-toast-undo" data-asset-organize-undo-now><i data-lucide="undo-2"></i><span>撤销</span></button>' : ''}`;
    document.body.appendChild(host);
    refreshIcons();
    clearTimeout(assetOrganizeToastTimer);
    assetOrganizeToastTimer = window.setTimeout(() => host.remove(), 4200);
}
function assetOrganizePushUndo(data, label, rootId=''){
    const undo = data?.undo;
    if(!undo || !undo.undo_id) return;
    assetOrganizeUndoStack.push({undo, label, rootId:String(data?.root_id || rootId || '')});
    while(assetOrganizeUndoStack.length > ASSET_ORGANIZE_UNDO_LIMIT) assetOrganizeUndoStack.shift();
}
function assetOrganizeInvalidateDiskFolders(rootId){
    // 目录树读的是逐层磁盘缓存；整理后必须让受影响根整棵缓存失效，否则看到的还是旧层级
    const prefix = `${rootId}::`;
    [...registryDiskFolderCache.keys()].forEach(key => {
        if(key.startsWith(prefix)) registryDiskFolderCache.delete(key);
    });
    [...registryDiskFolderLoading.keys()].forEach(key => {
        if(key.startsWith(prefix)) registryDiskFolderLoading.delete(key);
    });
}
async function assetOrganizeRefresh(rootId=''){
    const id = String(rootId || '');
    if(id) assetOrganizeInvalidateDiskFolders(id);
    resetAssetRackDirectoryIndex();
    try {
        if(id){
            assetTreeExpanded.add(`mine:root:${id}`);
            await loadAssetRackDiskFolders(id, '');
        }
        await loadAssetRackRegisteredFolders(true);
    } catch(_) {}
    await refreshRegistryAssets().catch(() => null);
    render();
}
/* ----- 弹窗：新建 / 删除确认 / 重名冲突 ----- */
function renderAssetOrganizeDialog(){
    const state = assetOrganizeDialog;
    if(!state) return '';
    if(state.kind === 'create'){
        return `<div class="asset-organize-dialog" role="dialog" aria-modal="true" aria-label="新建子文件夹">
            <header><strong>新建子文件夹</strong><button type="button" class="asset-organize-dialog-close" data-asset-organize-dialog-cancel aria-label="关闭"><i data-lucide="x"></i></button></header>
            <p class="asset-organize-dialog-hint">位置：${escapeHtml(state.locationLabel)}</p>
            <input class="asset-organize-dialog-input" data-asset-organize-dialog-input value="${escapeAttr(state.value || '')}" placeholder="文件夹名称" maxlength="120" aria-label="新文件夹名称">
            <p class="asset-organize-dialog-error" data-asset-organize-dialog-error></p>
            <footer><button class="hw-btn" type="button" data-asset-organize-dialog-cancel>取消</button><button class="hw-btn hw-btn-gold" type="button" data-asset-organize-dialog-confirm>创建</button></footer>
        </div>`;
    }
    if(state.kind === 'delete'){
        return `<div class="asset-organize-dialog" role="dialog" aria-modal="true" aria-label="删除确认">
            <header><strong>删除文件夹</strong><button type="button" class="asset-organize-dialog-close" data-asset-organize-dialog-cancel aria-label="关闭"><i data-lucide="x"></i></button></header>
            <p class="asset-organize-dialog-hint">「${escapeHtml(state.label)}」将移入应用回收站，不会永久删除；删除后可用 Ctrl+Z 撤销。</p>
            <footer><button class="hw-btn" type="button" data-asset-organize-dialog-cancel>取消</button><button class="hw-btn hw-btn-danger" type="button" data-asset-organize-dialog-confirm>删除</button></footer>
        </div>`;
    }
    if(state.kind === 'conflict'){
        return `<div class="asset-organize-dialog" role="dialog" aria-modal="true" aria-label="重名处理">
            <header><strong>目标位置已存在同名项</strong><button type="button" class="asset-organize-dialog-close" data-asset-organize-dialog-cancel aria-label="关闭"><i data-lucide="x"></i></button></header>
            <p class="asset-organize-dialog-hint">「${escapeHtml(state.name)}」在目标文件夹中已存在，请选择处理方式。</p>
            <footer class="asset-organize-dialog-choices">
                <button class="hw-btn" type="button" data-asset-organize-conflict="cancel">取消本次</button>
                <button class="hw-btn" type="button" data-asset-organize-conflict="skip">跳过</button>
                <button class="hw-btn" type="button" data-asset-organize-conflict="rename">改名</button>
                <button class="hw-btn hw-btn-gold" type="button" data-asset-organize-conflict="overwrite">覆盖</button>
            </footer>
        </div>`;
    }
    return '';
}
function syncAssetOrganizeDialog(){
    document.getElementById('assetOrganizeDialog')?.remove();
    if(!assetOrganizeDialog) return;
    const host = document.createElement('div');
    host.id = 'assetOrganizeDialog';
    host.className = 'storage-settings-overlay asset-organize-dialog-overlay';
    host.innerHTML = renderAssetOrganizeDialog();
    document.body.appendChild(host);
    window.FloatingDismissal?.mark?.(host, {kind:'modal', content:'self', close:() => closeAssetOrganizeDialog('cancel')});
    refreshIcons();
    const input = host.querySelector('[data-asset-organize-dialog-input]');
    if(input){
        input.focus();
        input.select?.();
        return;
    }
    requestAnimationFrame(() => host.querySelector('button:not([disabled])')?.focus({preventScroll:true}));
}
function openAssetOrganizeDialog(state){
    setAssetOrganizeDialog(state);
    syncAssetOrganizeDialog();
}
function closeAssetOrganizeDialog(result='cancel'){
    const state = assetOrganizeDialog;
    setAssetOrganizeDialog(null);
    document.getElementById('assetOrganizeDialog')?.remove();
    if(state && typeof state.resolve === 'function'){
        try { state.resolve(result); } catch(_) {}
    }
}
function setAssetOrganizeDialogError(text=''){
    const el = document.querySelector('#assetOrganizeDialog [data-asset-organize-dialog-error]');
    if(el) el.textContent = String(text || '');
}
/* ----- 新建 / 改名 / 删除 / 在系统中打开 ----- */
function openAssetOrganizeCreateDialog(target, presetRootId=''){
    const rootId = String(presetRootId || target?.rootId || assetOrganizeDefaultRootId() || '');
    if(!assetOrganizeWritableRoot(rootId)){
        showAssetOrganizeToast('没有可写入的挂载文件夹');
        return;
    }
    const parentPath = target ? String(target.path || '') : '';
    const locationLabel = parentPath
        ? `${assetRackRoots().find(root => String(root.id || '') === rootId)?.name || '挂载文件夹'} / ${parentPath}`
        : `${assetRackRoots().find(root => String(root.id || '') === rootId)?.name || '挂载文件夹'} / 根目录`;
    openAssetOrganizeDialog({kind:'create', rootId, parentPath, locationLabel, value:'新文件夹'});
}
async function assetOrganizeCreateFolder(state, rawName){
    const name = assetOrganizeValidateFolderName(rawName);
    const relPath = state.parentPath ? `${state.parentPath}/${name}` : name;
    const abs = assetOrganizeAbsPath(state.rootId, relPath);
    if(!abs) throw new Error('挂载路径不可用，无法新建文件夹');
    const data = await assetOrganizeRequest('/api/local-assets/folders', {path:abs, root_id:state.rootId});
    assetOrganizePushUndo(data, `新建 ${name}`, state.rootId);
    setAssetTreeSelectedKey(`mine:root:${state.rootId}::${relPath}`);
    showAssetOrganizeToast(`已新建文件夹「${name}」`, {undoable:true});
    setStatus(`已新建文件夹「${name}」`);
    await assetOrganizeRefresh(state.rootId);
}
function beginAssetOrganizeRename(target){
    // 行本身就是带 data-asset-tree-node 的 button，不能再按后代去查 .asset-tree-row
    const row = root.querySelector(`.asset-tree-panel [data-asset-tree-node="${CSS.escape(target.key)}"]`);
    const nameEl = row?.querySelector('.asset-tree-name');
    if(!row || !nameEl) return;
    const current = assetOrganizeLeafName(target.path);
    assetOrganizeInline = {key:target.key, value:current, busy:false};
    const input = document.createElement('input');
    input.className = 'asset-tree-rename';
    input.value = current;
    input.maxLength = 120;
    input.setAttribute('data-asset-organize-rename-input', target.key);
    input.setAttribute('aria-label', '重命名文件夹');
    nameEl.replaceWith(input);
    input.focus();
    input.select?.();
}
function cancelAssetOrganizeRename(){
    if(!assetOrganizeInline) return;
    assetOrganizeInline = null;
    render();
}
async function commitAssetOrganizeRename(target, rawValue){
    if(!assetOrganizeInline || assetOrganizeInline.busy) return;
    const current = assetOrganizeLeafName(target.path);
    const next = String(rawValue || '').trim();
    if(!next || next === current){ cancelAssetOrganizeRename(); return; }
    assetOrganizeInline = {key:target.key, value:next, busy:true};
    try {
        const name = assetOrganizeValidateFolderName(next);
        const abs = assetOrganizeAbsPath(target.rootId, target.path);
        const data = await assetOrganizeRequest('/api/local-assets/folders', {path:abs, name, root_id:target.rootId}, {method:'PATCH'});
        assetOrganizePushUndo(data, `重命名 ${current} → ${name}`, target.rootId);
        assetOrganizeInline = null;
        const renamed = `${assetOrganizeParentPath(target.path) ? `${assetOrganizeParentPath(target.path)}/` : ''}${name}`;
        setAssetTreeSelectedKey(`mine:root:${target.rootId}::${renamed}`);
        assetTreeExpanded.add(target.key);
        assetTreeExpanded.add(`mine:root:${target.rootId}::${renamed}`);
        showAssetOrganizeToast(`已重命名为「${name}」`, {undoable:true});
        setStatus(`已重命名为「${name}」`);
        await assetOrganizeRefresh(target.rootId);
    } catch(err) {
        assetOrganizeInline = {key:target.key, value:next, busy:false};
        showAssetOrganizeToast(err?.message || '重命名失败');
        setStatus(err?.message || '重命名失败');
        render();
    }
}
async function assetOrganizeDeleteFolder(target){
    openAssetOrganizeDialog({
        kind:'delete',
        label:assetOrganizeFolderLabel(target),
        resolve: choice => {
            if(choice !== 'confirm') return;
            void (async () => {
                try {
                    const abs = assetOrganizeAbsPath(target.rootId, target.path);
                    const data = await assetOrganizeRequest('/api/local-assets/folders/delete', {path:abs, root_id:target.rootId});
                    assetOrganizePushUndo(data, `删除 ${assetOrganizeFolderLabel(target)}`, target.rootId);
                    if(assetTreeSelectedKey === target.key) setAssetTreeSelectedKey('');
                    showAssetOrganizeToast(`已移入回收站：${assetOrganizeFolderLabel(target)}`, {undoable:true});
                    setStatus(`已移入回收站：${assetOrganizeFolderLabel(target)}`);
                    await assetOrganizeRefresh(target.rootId);
                } catch(err) {
                    showAssetOrganizeToast(err?.message || '删除失败');
                    setStatus(err?.message || '删除失败');
                }
            })();
        },
    });
}
/* ----- 拖拽：素材卡片 / 文件夹 ----- */
function clearAssetOrganizeDropHint(){
    if(assetOrganizeDropHint?.row){
        assetOrganizeDropHint.row.classList.remove('is-organize-drop-into', 'is-organize-drop-before', 'is-organize-drop-after');
    }
    setAssetOrganizeDropHint(null);
}
function applyAssetOrganizeDropHint(row, zone, before){
    if(assetOrganizeDropHint?.row === row && assetOrganizeDropHint.zone === zone) return;
    clearAssetOrganizeDropHint();
    if(zone === 'into') row.classList.add('is-organize-drop-into');
    else row.classList.add(before ? 'is-organize-drop-before' : 'is-organize-drop-after');
    setAssetOrganizeDropHint({row, zone});
}
async function dropAssetOrganize(event){
    if(!assetOrganizeDrag) return;
    const hit = assetOrganizeDropTargetFor(event);
    if(!hit) return;
    event.preventDefault();
    const zone = assetOrganizeDropZoneFor(hit.row, event.clientY, hit.target.isRoot);
    const drag = assetOrganizeDrag;
    clearAssetOrganizeDropHint();
    setAssetOrganizeDrag(null);
    document.querySelectorAll('.is-organize-dragging').forEach(el => el.classList.remove('is-organize-dragging'));
    const destPath = zone === 'into' ? hit.target.path : assetOrganizeParentPath(hit.target.path);
    if(drag.kind === 'folder'){
        // 【QIN-546】/api/local-assets/folders/move 的 target 是「最终路径」（不像素材移动
        // 那支会在 target 为目录时自动拼文件名），这里必须把本层名字补上，
        // 否则拖进任何已存在的目录都会 409 TARGET_EXISTS。
        const leaf = assetOrganizeLeafName(drag.path);
        const folderDestPath = destPath ? `${destPath}/${leaf}` : leaf;
        await assetOrganizeMoveFolder(drag, hit.target.rootId, folderDestPath);
        return;
    }
    await assetOrganizeMoveAssets(drag.assetIds, hit.target.rootId, destPath);
}
async function assetOrganizeMoveFolder(drag, destRootId, destPath){
    // destPath 是本层移动后的最终路径（含自身名字），与 folders/move 的 target 语义一致
    if(drag.rootId !== destRootId){
        showAssetOrganizeToast('仅支持在同一挂载文件夹内移动');
        setStatus('仅支持在同一挂载文件夹内移动');
        return;
    }
    if(destPath === drag.path){
        // 拖回原位置 = 无变化
        render();
        return;
    }
    const sourceAbs = assetOrganizeAbsPath(drag.rootId, drag.path);
    const targetAbs = assetOrganizeAbsPath(destRootId, destPath);
    if(!sourceAbs || !targetAbs) return;
    try {
        const data = await assetOrganizeRequest('/api/local-assets/folders/move', {
            source:sourceAbs,
            target:targetAbs,
            source_root_id:drag.rootId,
            target_root_id:destRootId,
        });
        assetOrganizePushUndo(data, `移动 ${assetOrganizeLeafName(drag.path)}`, drag.rootId);
        setAssetTreeSelectedKey(`mine:root:${destRootId}::${destPath}`);
        showAssetOrganizeToast(`已移动文件夹「${assetOrganizeLeafName(drag.path)}」`, {undoable:true});
        setStatus(`已移动文件夹「${assetOrganizeLeafName(drag.path)}」`);
        await assetOrganizeRefresh(destRootId);
    } catch(err) {
        showAssetOrganizeToast(assetOrganizeErrorText(err));
        setStatus(assetOrganizeErrorText(err));
        render();
    }
}
async function assetOrganizeMoveOne(assetId, item, destRootId, destPath){
    const sourceRoot = String(item?.storage_root_id || '');
    const rel = String(item?.canonical_relpath || item?.relative_path || '').replace(/\\/g, '/').replace(/^\/+|\/+$/g, '');
    if(!sourceRoot || !rel) return {status:'failed', reason:'素材缺少真实磁盘路径'};
    if(sourceRoot !== destRootId) return {status:'failed', reason:'仅支持在同一挂载文件夹内移动'};
    if(assetOrganizeParentPath(rel) === destPath) return {status:'unchanged'};
    const sourceAbs = assetOrganizeAbsPath(sourceRoot, rel);
    const targetAbs = assetOrganizeAbsPath(destRootId, destPath);
    if(!sourceAbs || !targetAbs) return {status:'failed', reason:'挂载路径不可用'};
    const move = async extra => assetOrganizeRequest('/api/local-assets/move', {
        source:sourceAbs,
        target:targetAbs,
        source_root_id:sourceRoot,
        target_root_id:destRootId,
        organize:true,
        ...extra,
    });
    try {
        const data = await move({});
        assetOrganizePushUndo(data, `移动 ${item.name || assetId}`, sourceRoot);
        return {status:'moved', rootId:sourceRoot};
    } catch(err) {
        if(err?.code !== 'TARGET_EXISTS') return {status:'failed', reason:assetOrganizeErrorText(err)};
        const choice = await assetOrganizeAskConflict(item?.name || assetId);
        if(choice === 'cancel') return {status:'aborted'};
        if(choice === 'skip') return {status:'skipped'};
        try {
            const data = await move({conflict_resolution:choice});
            assetOrganizePushUndo(data, `移动 ${item.name || assetId}`, sourceRoot);
            return {status:'moved', rootId:sourceRoot};
        } catch(retryErr) {
            return {status:'failed', reason:assetOrganizeErrorText(retryErr)};
        }
    }
}
async function assetOrganizeMoveAssets(assetIds=[], destRootId='', destPath=''){
    const ids = [...new Set((assetIds || []).map(String).filter(Boolean))];
    if(!ids.length) return;
    const pool = new Map(registryAssets.map(item => [String(item.id || ''), item]));
    let moved = 0;
    const failures = [];
    for(const id of ids){
        const item = pool.get(id);
        if(!item){ failures.push({id, reason:'素材不在当前列表'}); continue; }
        const result = await assetOrganizeMoveOne(id, item, destRootId, destPath);
        if(result.status === 'moved') moved += 1;
        else if(result.status === 'aborted') break;
        else if(result.status === 'failed') failures.push({id, reason:result.reason});
    }
    const targetLabel = destPath ? assetOrganizeLeafName(destPath) : '根目录';
    if(moved){
        showAssetOrganizeToast(`已移动 ${moved} 项到「${targetLabel}」${failures.length ? `，${failures.length} 项失败` : ''}`, {undoable:true});
        setStatus(`已移动 ${moved} 项到「${targetLabel}」`);
        await assetOrganizeRefresh(destRootId);
        return;
    }
    if(failures.length){
        showAssetOrganizeToast(failures[0].reason || '移动失败');
        setStatus(failures[0].reason || '移动失败');
    }
    render();
}
/* ----- body 级浮层的点击委托（菜单/弹窗/toast 不在 #assetManagerRoot 内） ----- */
async function handleAssetOrganizeOverlayClick(event){
    const target = event.target;
    if(!target?.closest?.('#assetOrganizeMenu, #assetOrganizeDialog, #assetOrganizeToast')) return false;
    if(target.closest('[data-asset-organize-undo-now]')){ document.getElementById('assetOrganizeToast')?.remove(); await assetOrganizeUndoLast(); return true; }
    if(target.closest('[data-asset-organize-new]')){ const current = assetOrganizeMenu; closeAssetOrganizeMenu(); openAssetOrganizeCreateDialog(current); return true; }
    if(target.closest('[data-asset-organize-rename]')){ const current = assetOrganizeMenu; closeAssetOrganizeMenu(); if(current && !current.isRoot) beginAssetOrganizeRename(current); return true; }
    if(target.closest('[data-asset-organize-open]')){ const current = assetOrganizeMenu; closeAssetOrganizeMenu(); if(current) await assetOrganizeOpenFolderInSystem(current); return true; }
    if(target.closest('[data-asset-organize-delete]')){ const current = assetOrganizeMenu; closeAssetOrganizeMenu(); if(current && !current.isRoot) await assetOrganizeDeleteFolder(current); return true; }
    const conflictChoice = target.closest('[data-asset-organize-conflict]');
    if(conflictChoice){ closeAssetOrganizeDialog(conflictChoice.dataset.assetOrganizeConflict || 'cancel'); return true; }
    if(target.closest('[data-asset-organize-dialog-cancel]')){ closeAssetOrganizeDialog('cancel'); return true; }
    if(target.closest('[data-asset-organize-dialog-confirm]')){
        const state = assetOrganizeDialog;
        if(!state) return true;
        if(state.kind === 'delete'){ closeAssetOrganizeDialog('confirm'); return true; }
        if(state.kind === 'create'){
            const input = document.querySelector('#assetOrganizeDialog [data-asset-organize-dialog-input]');
            try {
                setAssetOrganizeDialogError('');
                closeAssetOrganizeDialog('created');
                await assetOrganizeCreateFolder(state, input?.value || '');
            } catch(err) {
                setStatus(err?.message || '新建文件夹失败');
                showAssetOrganizeToast(err?.message || '新建文件夹失败');
            }
            return true;
        }
        return true;
    }
    if(target.id === 'assetOrganizeDialog'){ closeAssetOrganizeDialog('cancel'); return true; }
    return true;
}
function renderCustomAssetLibrary(board){
    if(!board){ setActiveTab('registry'); renderRegistryManager(); return; }
    const embeddedVault = document.documentElement.classList.contains('vault-shell-embedded');
    const matching = customAssetLibraryItems(board);
    const visible = registryVisibleAssetList(matching);
    if(selectedRegistryAssetId && !matching.some(item => item.id === selectedRegistryAssetId)){
        setSelectedRegistryAssetId('');
        setRegistryDetail(null);
    }
    const preset = ASSET_LIBRARY_PRESETS.find(item => item.id === board.preset) || ASSET_LIBRARY_PRESETS[3];
    const emptyAssetMessage = board.sourceBoard
        ? `“${board.name}”暂无已索引素材。`
        : board.preset === 'other'
            ? '暂无可见资源。素材进入统一索引后会自动显示在这里。'
            : `暂无匹配的${preset.name.replace(/库$/, '')}资产。资产进入统一索引后会自动显示在这里。`;
    const activeSourceId = board.sourceBoard ? String(board.rootId || '') : (boardSourceSelection[board.id] || '');
    const activeSource = (board.sources || []).find(source => source.id === activeSourceId) || null;
    const sourceLabel = activeSource ? activeSource.name : activeSourceId === '__automatic__' ? '自动归档' : '全部素材';
    const activeFolderPath = boardFolderSelection[board.id] || '';
    const folderLabel = activeFolderPath === '__root__' ? '根目录' : activeFolderPath.split('/').filter(Boolean).pop() || '';
    const activeGroup = registryBoardCustomGroups(board).find(group => group.id === (boardGroupSelection[board.id] || '')) || null;
    const activeReferenceCanvas = (canvasAssetsData.canvases || []).find(canvas => canvas.id === (boardReferenceCanvasSelection[board.id] || '')) || null;
    root.innerHTML = `${renderAssetTreePanel()}
    <section class="asset-panel asset-content registry-content registry-layout-${registryLayout} registry-aspect-${registryAspect} ${registrySimple ? 'registry-simple' : ''} ${registrySelectedIds.size ? 'manage-on' : ''}" style="--registry-card-size:${registryCardSize}px">
        ${embeddedVault ? '' : renderRegistryToolbar(board, board.name, `${matching.length} 个匹配资产 · ${activeReferenceCanvas ? `参考画布：${activeReferenceCanvas.title || '未命名'}` : sourceLabel}${folderLabel ? ` · ${folderLabel}` : ''}${activeGroup ? ` · ${activeGroup.name}` : ''}`)}
        ${renderRegistryFilterChips()}
        <div class="registry-fresh-notice" data-registry-fresh-notice></div>
        <div class="content-scroll registry-assets-scroll" data-registry-zoom-area title="Shift+滚轮调整预览大小">${renderRegistryAssetSections(visible, board, emptyAssetMessage)}${renderRegistryPagination()}</div>
        <div class="registry-vault-footer" aria-label="资产库状态"><span>匹配资产 <strong>${Number(matching.length || 0).toLocaleString('zh-CN')}</strong></span><span>${registryLoading ? '正在读取' : registryPageHasMore ? `已加载 ${visible.length} 项` : `已加载全部 ${visible.length} 项`}</span><span>${escapeHtml(sourceLabel)}</span></div>
        ${renderRegistrySelectionBar(board)}
    </section>
    ${renderRegistryDetailResizer()}
    ${renderRegistryDetailPanel()}`;
    // 注意：这里只能触发补齐目录索引（内部有 promise 去重，不会递归重绘）；
    // 不可在此调用 loadCustomBoardFolderDirectories —— 它收尾会无条件 render()，
    // 与渲染入口互相触发会直接把页面打成死循环。
    void ensureAssetRackDirectoryIndex();
}
function projectStatusLabel(status='pending'){
    return {pending:'待处理',in_progress:'进行中',approved:'已通过',blocked:'已阻塞'}[status] || status;
}
function renderProjectManager(){
    const embeddedVault = document.documentElement.classList.contains('vault-shell-embedded');
    const selectedProject = (registryProjectFilterId && registryProjectFilterId !== '__all__')
        ? (projects.find(item => item.id === registryProjectFilterId) || projectBoard?.project || null)
        : null;
    const project = selectedProject || projectBoard?.project || projects.find(item => item.id === activeProjectId) || null;
    const entities = selectedProject ? (projectBoard?.entities || []) : [];
    const visible = registryVisibleAssets();
    root.innerHTML = `${renderAssetTreePanel()}
    <section class="asset-panel asset-content registry-content registry-layout-${registryLayout} registry-aspect-${registryAspect} ${registrySimple ? 'registry-simple' : ''} ${registrySelectedIds.size ? 'manage-on' : ''}" style="--registry-card-size:${registryCardSize}px">
        ${embeddedVault ? '' : `<div class="content-toolbar"><div class="content-heading"><strong>${escapeHtml(selectedProject?.name || '全部项目素材')}</strong><span>${selectedProject ? `${entities.length} 个条目 · ${Number(selectedProject?.asset_count || 0)} 个素材 · ` : ''}当前显示 ${visible.length} 项</span></div><div class="asset-tools"><button class="hw-btn" type="button" data-project-proxy-settings ${selectedProject?'':'disabled'}><i data-lucide="film"></i><span>代理设置</span></button><button class="hw-btn" type="button" data-project-template ${selectedProject?'':'disabled'}><i data-lucide="settings-2"></i><span>模板设置</span></button><button class="hw-btn" type="button" data-project-add-entity ${selectedProject?'':'disabled'}><i data-lucide="plus"></i><span>新增条目</span></button></div></div>`}
        ${renderRegistryFilterChips()}
        <div class="content-scroll registry-assets-scroll" data-registry-zoom-area title="Shift+滚轮调整预览大小">${renderRegistryAssetSections(visible, null, selectedProject ? '该项目暂无素材。' : '全部项目暂无素材。')}${renderRegistryPagination()}</div>
        ${renderRegistrySelectionBar(null)}
    </section>`;
    if(selectedProject?.id){
        if(!assetTreeExpanded.has('system:projects')){
            assetTreeExpanded.add('system:projects');
            assetTreeExpanded.add(`system:projects:${selectedProject.id}`);
        }
        void ensureAssetTreeProjectData(selectedProject.id);
    }
}

function renderAssetManager(){
    normalizeAssetState();
    const libs = assetLibraries();
    const cats = assetCategories();
    const lib = activeAssetLibrary();
    const cat = activeAssetCategory();
    const items = currentAssetItems();
    const detail = selectedAsset();
    const imageCount = selectedAssetImageItems().length;
    root.innerHTML = `
        ${renderAssetTreePanel()}}
        <section class="asset-panel asset-content ${assetManageMode ? 'manage-on' : ''}">
            <div class="content-toolbar">
                <div class="content-heading">
                    <strong>${escapeHtml(assetViewTitle())}</strong>
                    <span>${assetViewSubtitle(items)}</span>
                </div>
                <div class="asset-tools">
                    <label class="asset-search-wrap"><i data-lucide="search"></i><input id="assetSearch" class="asset-search" type="search" value="${escapeAttr(assetQuery)}" placeholder="搜索素材"></label>
                    <button class="${assetManageMode ? 'hw-btn hw-btn-gold' : 'hw-btn'}" type="button" data-asset-manage><i data-lucide="list-checks"></i><span>${assetManageMode ? '完成管理' : '批量管理'}</span></button>
                </div>
            </div>
            ${renderAssetClipboardBar()}
            ${renderLocalClipboardBar()}
            <div class="manage-tools">
                <div class="manage-group manage-select-group">
                    <span class="manage-group-title">管理</span>
                    <span class="manage-summary">已选 ${selectedAssetIds.size} 个，其中 ${imageCount} 张图片</span>
                    <div class="asset-tools">
                        <button class="hw-btn" type="button" data-asset-select-all ${items.length ? '' : 'disabled'}><i data-lucide="check-square"></i><span>全选</span></button>
                        <button class="hw-btn" type="button" data-asset-clear-selection ${selectedAssetIds.size ? '' : 'disabled'}><i data-lucide="square"></i><span>清空</span></button>
                        <button class="hw-btn" type="button" data-asset-cut-selected ${selectedAssetIds.size ? '' : 'disabled'}><i data-lucide="scissors"></i><span>剪切</span></button>
                        <button class="hw-btn" type="button" data-asset-copy-selected ${selectedAssetIds.size ? '' : 'disabled'}><i data-lucide="copy"></i><span>复制</span></button>
                        <button class="hw-btn" type="button" data-asset-download-selected ${selectedAssetIds.size ? '' : 'disabled'}><i data-lucide="download"></i><span>下载所选</span></button>
                        <button class="hw-btn" type="button" data-asset-copy-to-canvas ${selectedAssetIds.size ? '' : 'disabled'}><i data-lucide="clipboard-paste"></i><span>复制到画布</span></button>
                        <button class="hw-btn hw-btn-danger" type="button" data-asset-delete-selected ${selectedAssetIds.size ? '' : 'disabled'}><i data-lucide="trash-2"></i><span>删除所选</span></button>
                    </div>
                </div>
                <div class="manage-group manage-ai-group">
                    <span class="manage-group-title">AI 处理</span>
                    <button class="hw-btn" type="button" data-asset-classify-selected ${imageCount && !assetClassifyBusy ? '' : 'disabled'}><i data-lucide="${assetClassifyBusy ? 'loader-2' : 'tags'}"></i><span>${assetClassifyBusy ? '分类中' : '智能分类'}</span></button>
                </div>
            </div>
            <div class="content-scroll">
                <div class="asset-grid">
                    ${activeAssetClassFilter ? '' : renderUploadCard(cat)}
                    ${items.map(item => renderAssetCard(item)).join('')}
                    ${items.length ? '' : '<div class="empty-state">当前分组还没有素材，可以上传，或从智能画布输出保存到素材库。</div>'}
                </div>
            </div>
        </section>
        <aside class="asset-panel asset-detail">
            ${renderAssetDetail(detail)}
        </aside>
    `;
}
function renderWorkflowManager(){
    normalizeWorkflowState();
    const cats = workflowCategories();
    const cat = activeWorkflowCategory();
    const items = currentWorkflowItems();
    const detail = selectedWorkflow();
    root.innerHTML = `
        ${renderAssetTreePanel()}}
        <section class="asset-panel asset-content ${workflowManageMode ? 'manage-on' : ''}">
            <div class="content-toolbar">
                <div class="content-heading">
                    <strong>${escapeHtml(cat?.name || '工作流管理')}</strong>
                    <span>工作流库 / ${items.length} 个工作流</span>
                </div>
                <div class="asset-tools">
                    <label class="asset-search-wrap"><i data-lucide="search"></i><input id="workflowSearch" class="asset-search" type="search" value="${escapeAttr(workflowQuery)}" placeholder="搜索工作流"></label>
                    <button class="${workflowManageMode ? 'hw-btn hw-btn-gold' : 'hw-btn'}" type="button" data-workflow-manage><i data-lucide="list-checks"></i><span>${workflowManageMode ? '完成管理' : '批量管理'}</span></button>
                </div>
            </div>
            <div class="manage-tools">
                <span>已选择 ${selectedWorkflowIds.size} 个工作流。</span>
                <div class="asset-tools">
                    <button class="hw-btn" type="button" data-workflow-select-all ${items.length ? '' : 'disabled'}><i data-lucide="check-square"></i><span>全选</span></button>
                    <button class="hw-btn" type="button" data-workflow-clear-selection ${selectedWorkflowIds.size ? '' : 'disabled'}><i data-lucide="square"></i><span>清空</span></button>
                    <button class="hw-btn" type="button" data-workflow-export-selected ${selectedWorkflowIds.size ? '' : 'disabled'}><i data-lucide="download"></i><span>导出所选</span></button>
                    <button class="hw-btn hw-btn-danger" type="button" data-workflow-delete-selected ${selectedWorkflowIds.size ? '' : 'disabled'}><i data-lucide="trash-2"></i><span>删除所选</span></button>
                </div>
            </div>
            <div class="content-scroll">
                <div class="asset-grid">
                    ${renderWorkflowUploadCard(cat)}
                    ${items.map(item => renderWorkflowCard(item)).join('')}
                    ${items.length ? '' : '<div class="empty-state">当前分组还没有工作流，可以上传 JSON / ZIP，或从传统画布导出到资产库。</div>'}
                </div>
            </div>
        </section>
        <aside class="asset-panel asset-detail">
            ${renderWorkflowDetail(detail)}
        </aside>
    `;
}
function renderWorkflowTreeActionBar(){
    const editHtml = renderWorkflowTreeInlineEdit();
    if(editHtml) return editHtml;
    const deleteKey = `workflow-cat:${activeWorkflowLibraryId}:${activeWorkflowCategoryId}`;
    return `<div class="tree-action-bar child-actions">
        <button type="button" data-workflow-cat-rename><i data-lucide="pencil"></i><span>重命名</span></button>
        <button type="button" class="danger ${pendingTreeDelete === deleteKey ? 'detail-confirm' : ''}" data-workflow-cat-delete><i data-lucide="trash-2"></i><span>${pendingTreeDelete === deleteKey ? '确认删除' : '删除'}</span></button>
    </div>`;
}
function renderWorkflowTreeInlineEdit(){
    if(!workflowTreeEdit || workflowTreeEdit.placement === 'head') return '';
    if(workflowTreeEdit.kind !== 'category-rename') return '';
    const label = workflowTreeEdit.label || '名称';
    return `<div class="tree-inline-edit child-actions">
        <input id="workflowTreeEditInput" type="text" value="${escapeAttr(workflowTreeEdit.value || '')}" placeholder="${escapeAttr(label)}">
        <button type="button" class="primary" data-workflow-tree-edit-save><i data-lucide="check"></i><span>保存</span></button>
        <button type="button" data-workflow-tree-edit-cancel><i data-lucide="x"></i><span>取消</span></button>
    </div>`;
}
function renderWorkflowCard(item){
    return `<article class="asset-card workflow-card ${workflowAssetId(item) === selectedWorkflowId ? 'active' : ''}" data-workflow-card="${escapeAttr(workflowAssetId(item))}" tabindex="0" role="button" aria-label="查看 ${escapeAttr(item.name || '工作流')} 详情">
        <input class="asset-card-check" type="checkbox" data-workflow-check="${escapeAttr(workflowAssetId(item))}" ${selectedWorkflowIds.has(workflowAssetId(item)) ? 'checked' : ''}>
        <div class="asset-thumb">${workflowThumb(item)}</div>
        <div class="asset-card-body">
            <div class="asset-card-name" title="${escapeAttr(item.name || '')}">${escapeHtml(item.name || 'workflow')}</div>
            <div class="asset-card-meta">${escapeHtml(workflowKindLabel(item))} · ${escapeHtml(formatDate(item.created_at))}</div>
        </div>
    </article>`;
}
function renderWorkflowDetail(item){
    if(!item) return `<div class="panel-head"><div class="panel-title"><strong>工作流详情</strong><span>选择一个工作流查看详情</span></div></div><div class="detail-scroll"><div class="detail-empty"><i data-lucide="workflow"></i><span>暂无工作流</span></div></div>`;
    const asset_id = workflowAssetId(item), workflow_id = workflowSourceId(item);
    return `
        <div class="panel-head">
            <div class="panel-title"><strong>工作流详情</strong><span>${escapeHtml(workflowKindLabel(item))}</span></div>
            <div class="panel-actions">
                ${workflow_id ? `<a class="hw-btn hw-btn-icon" href="/static/pages/workflow.html?id=${encodeURIComponent(workflow_id)}" title="编辑工作流" aria-label="编辑工作流"><i data-lucide="external-link"></i></a>` : ''}
                <button class="hw-btn hw-btn-icon" type="button" data-workflow-download="${escapeAttr(asset_id)}" title="导出工作流"><i data-lucide="download"></i></button>
                <button class="hw-btn hw-btn-icon" type="button" data-workflow-rename="${escapeAttr(asset_id)}" title="重命名"><i data-lucide="pencil"></i></button>
                <button class="hw-btn hw-btn-icon hw-btn-danger ${pendingDeleteAssetId === asset_id ? 'detail-confirm' : ''}" type="button" data-workflow-delete="${escapeAttr(asset_id)}" ${workflowDeleteBusy.has(asset_id) ? 'disabled aria-busy="true"' : ''} title="${pendingDeleteAssetId === asset_id ? '再次点击确认删除' : '删除'}"><i data-lucide="trash-2"></i></button>
            </div>
        </div>
        <div class="detail-scroll">
            <div class="detail-media"><div class="detail-media-frame">${workflowThumb(item)}</div></div>
            <div class="detail-body">
                <input class="detail-name-input" data-workflow-inline-name="${escapeAttr(asset_id)}" type="text" value="${escapeAttr(item.name || 'workflow')}" ${workflow_id ? 'readonly' : ''} title="${workflow_id ? '使用重命名按钮按文档版本保存' : '直接修改名称'}">
                <div class="detail-meta-grid">
                    <div class="detail-meta"><span>类型</span><strong>${escapeHtml(workflowKindLabel(item))}</strong></div>
                    <div class="detail-meta"><span>创建时间</span><strong>${escapeHtml(formatDate(item.created_at))}</strong></div>
                    <div class="detail-meta"><span>位置</span><strong>工作流库</strong></div>
                    <div class="detail-meta"><span>分组</span><strong>${escapeHtml(activeWorkflowCategory()?.name || '工作流')}</strong></div>
                </div>
                <div class="detail-url">${escapeHtml(item.url || '')}</div>
            </div>
        </div>
    `;
}
function renderAssetClipboardBar(){
    if(!assetClipboard?.ids?.length) return '';
    const modeLabel = assetClipboard.mode === 'cut' ? '剪切' : '复制';
    const sameTarget = assetClipboard.sourceLibraryId === activeAssetLibraryId && assetClipboard.sourceCategoryId === activeAssetCategoryId;
    const pasteText = sameTarget && assetClipboard.mode === 'cut' ? '选择其他分组后粘贴' : '粘贴到当前分组';
    return `<div class="asset-clipboard-bar">
        <div class="asset-clipboard-info"><i data-lucide="clipboard"></i><span>${escapeHtml(modeLabel)}了 ${assetClipboard.ids.length} 个素材</span></div>
        <div class="asset-tools">
            <button class="hw-btn hw-btn-gold" type="button" data-asset-paste-clipboard ${sameTarget && assetClipboard.mode === 'cut' ? 'disabled' : ''}><i data-lucide="clipboard-paste"></i><span>${escapeHtml(pasteText)}</span></button>
            <button class="hw-btn hw-btn-icon" type="button" data-asset-clear-clipboard title="清空剪贴板"><i data-lucide="x"></i></button>
        </div>
    </div>`;
}
function renderAssetCard(item){
    return `<article class="asset-card ${item.id === selectedAssetId ? 'active' : ''}" data-asset-card="${escapeAttr(item.id)}" tabindex="0" role="button" aria-label="查看 ${escapeAttr(item.name || '素材')} 详情">
        <input class="asset-card-check" type="checkbox" data-asset-check="${escapeAttr(item.id)}" ${selectedAssetIds.has(item.id) ? 'checked' : ''}>
        <div class="asset-thumb">${assetThumb(item)}</div>
        <div class="asset-card-body">
            <div class="asset-card-name" title="${escapeAttr(item.name || '')}">${escapeHtml(item.name || 'asset')}</div>
            <div class="asset-card-meta">${escapeHtml(assetKindLabel(item))} · ${escapeHtml(formatDate(item.created_at))}</div>
        </div>
    </article>`;
}
function renderPromptManager(){
    normalizePromptState();
    const libs = promptLibraries();
    const lib = activePromptLibrary();
    const readonly = Boolean(lib?.readonly);
    const cats = activePromptCategories();
    const items = currentPromptItems();
    const detail = promptCreateMode ? null : selectedPrompt();
    const promptEmptyText = (lib?.items || []).length
        ? '当前条件下没有提示词。可以切换分类或清空搜索条件。'
        : `${lib?.name || '当前提示词库'} 暂无提示词${readonly ? '。' : '，点击「新增」添加。'}`;
    root.innerHTML = `
        ${renderAssetTreePanel()}}
        <section class="asset-panel asset-content ${promptManageMode ? 'manage-on' : ''}">
            <div class="content-toolbar">
                <div class="content-heading">
                    <strong>${escapeHtml(lib?.name || '提示词库')}</strong>
                    <span>共 ${items.length} 条提示词</span>
                </div>
                <div class="asset-tools">
                    <label class="asset-search-wrap"><i data-lucide="search"></i><input id="promptSearch" class="asset-search" type="search" value="${escapeAttr(promptQuery)}" placeholder="搜索名称、说明或正文"></label>
                    <button class="hw-btn hw-btn-gold" type="button" data-prompt-new ${readonly ? 'disabled' : ''}><i data-lucide="file-plus-2"></i><span>新增</span></button>
                    <button class="${promptManageMode ? 'hw-btn hw-btn-gold' : 'hw-btn'}" type="button" data-prompt-manage><i data-lucide="list-checks"></i><span>${promptManageMode ? '完成管理' : '批量管理'}</span></button>
                </div>
            </div>
            <div class="manage-tools">
                <span>已选择 ${selectedPromptIds.size} 条提示词，支持拖拽框选或逐个勾选。</span>
                <div class="asset-tools">
                    <button class="hw-btn" type="button" data-prompt-select-all ${items.length && !readonly ? '' : 'disabled'}><i data-lucide="check-square"></i><span>全选</span></button>
                    <button class="hw-btn" type="button" data-prompt-clear-selection ${selectedPromptIds.size ? '' : 'disabled'}><i data-lucide="square"></i><span>清空</span></button>
                    <button class="hw-btn hw-btn-danger ${pendingBatchDelete === 'prompt' ? 'detail-confirm' : ''}" type="button" data-prompt-delete-selected ${readonly || !selectedPromptIds.size ? 'disabled' : ''}><i data-lucide="trash-2"></i><span>${pendingBatchDelete === 'prompt' ? '确认删除' : '删除所选'}</span></button>
                </div>
            </div>
            <div class="content-scroll">
                ${items.length ? `<div class="prompt-card-grid">${items.map(item => renderPromptRow(item, readonly)).join('')}</div>` : `<div class="empty-state">${escapeHtml(promptEmptyText)}</div>`}
            </div>
        </section>
        <aside class="asset-panel asset-detail">
            ${renderPromptDetail(detail, readonly)}
        </aside>
    `;
}
function renderPromptTreeBranch(lib){
    const isActiveLib = lib.id === activePromptLibraryId;
    // 每个库渲染自己的分类，不再回退到激活库/系统库的分类（修复新库错显系统分类）。
    const cats = promptCategoriesFor(lib);
    const libId = escapeAttr(lib.id);
    const readonly = Boolean(lib.readonly);
    const showLibActions = isActiveLib && promptTreeFocus === 'library';
    return `<div class="tree-branch ${isActiveLib ? 'expanded' : ''}">
        <button class="tree-row tree-parent ${isActiveLib ? 'contains-active' : ''} ${showLibActions ? 'active' : ''}" type="button" data-prompt-lib="${libId}">
            <span class="tree-row-icon"><i data-lucide="${lib.id === 'system' ? 'sparkles' : 'book-open'}"></i></span>
            <span class="tree-row-name">${escapeHtml(lib.name || '提示词库')}</span>
            <span class="tree-row-count">${(lib.items || []).length}</span>
        </button>
        ${showLibActions ? renderPromptTreeActionBar('library') : ''}
        <div class="tree-children">
            <button class="tree-row tree-child ${isActiveLib && activePromptCategory === 'all' && promptTreeFocus === 'category' ? 'active' : ''}" type="button" data-prompt-cat="all" data-prompt-cat-lib="${libId}">
                <span class="tree-elbow"></span>
                <span class="tree-row-icon"><i data-lucide="layout-list"></i></span>
                <span class="tree-row-name">全部提示词</span>
                <span class="tree-row-count">${promptCountForCategory('all', lib)}</span>
            </button>
            ${cats.map(cat => {
                const active = isActiveLib && cat.id === activePromptCategory && promptTreeFocus === 'category';
                return `<button class="tree-row tree-child ${active ? 'active' : ''}" type="button" data-prompt-cat="${escapeAttr(cat.id)}" data-prompt-cat-lib="${libId}">
                <span class="tree-elbow"></span>
                <span class="tree-row-icon"><i data-lucide="tag"></i></span>
                <span class="tree-row-name">${escapeHtml(cat.name || promptCategoryLabel(cat.id))}</span>
                <span class="tree-row-count">${promptCountForCategory(cat.id, lib)}</span>
            </button>${active ? renderPromptTreeActionBar('category') : ''}`;
            }).join('')}
        </div>
    </div>`;
}
function renderPromptTreeActionBar(kind){
    const editHtml = renderPromptTreeInlineEdit(kind);
    if(editHtml) return editHtml;
    if(kind === 'library'){
        const lib = activePromptLibrary();
        if(lib?.readonly){
            return '<div class="tree-action-bar child-actions muted-actions"><span><i data-lucide="lock"></i>只读提示词库</span></div>';
        }
        const isSystem = isSystemPromptLibrary(lib);
        const deleteKey = `prompt-lib:${lib?.id || ''}`;
        return `<div class="tree-action-bar library-actions">
            <button type="button" data-prompt-cat-new><i data-lucide="folder-plus"></i><span>新分组</span></button>
            <button type="button" data-prompt-lib-rename><i data-lucide="pencil"></i><span>重命名</span></button>
            ${isSystem ? '' : `<button type="button" class="danger ${pendingTreeDelete === deleteKey ? 'detail-confirm' : ''}" data-prompt-lib-delete><i data-lucide="trash-2"></i><span>${pendingTreeDelete === deleteKey ? '确认删除' : '删除库'}</span></button>`}
        </div>`;
    }
    if(activePromptCategory === 'all'){
        return `<div class="tree-action-bar child-actions muted-actions"><span><i data-lucide="lock"></i>请选择具体分组后编辑</span></div>`;
    }
    if(activePromptLibrary()?.readonly){
        return '<div class="tree-action-bar child-actions muted-actions"><span><i data-lucide="lock"></i>只读分组</span></div>';
    }
    const deleteKey = `prompt-cat:${activePromptCategory}`;
    return `<div class="tree-action-bar child-actions">
        <button type="button" data-prompt-cat-rename><i data-lucide="pencil"></i><span>重命名</span></button>
        <button type="button" class="danger ${pendingTreeDelete === deleteKey ? 'detail-confirm' : ''}" data-prompt-cat-delete><i data-lucide="trash-2"></i><span>${pendingTreeDelete === deleteKey ? '确认删除' : '删除'}</span></button>
    </div>`;
}
function renderPromptTreeInlineEdit(kind){
    if(!promptTreeEdit) return '';
    if(promptTreeEdit.placement === 'head') return '';
    const expectedKinds = kind === 'library' ? ['library-new', 'library-rename', 'category-new'] : ['category-new', 'category-rename'];
    if(!expectedKinds.includes(promptTreeEdit.kind)) return '';
    const label = promptTreeEdit.label || '名称';
    return `<div class="tree-inline-edit ${kind === 'category' ? 'child-actions' : 'library-actions'}">
        <input id="promptTreeEditInput" type="text" value="${escapeAttr(promptTreeEdit.value || '')}" placeholder="${escapeAttr(label)}">
        <button type="button" class="primary" data-prompt-tree-edit-save><i data-lucide="check"></i><span>保存</span></button>
        <button type="button" data-prompt-tree-edit-cancel><i data-lucide="x"></i><span>取消</span></button>
    </div>`;
}
function renderPromptRow(item, readonly){
    const metadata = item.metadata && typeof item.metadata === 'object' ? item.metadata : {};
    const cover = String(metadata.cover_url || metadata.reference_image_urls?.[0] || '').trim();
    return `<article class="prompt-card ${item.id === selectedPromptId ? 'active' : ''}" data-prompt-row="${escapeAttr(item.id)}" tabindex="0" role="button" aria-label="查看 ${escapeAttr(item.name || '提示词')} 详情">
        <div class="prompt-card-media">
            ${cover ? `<img src="${escapeAttr(cover)}" alt="" loading="lazy" data-preview-fallback="提示词封面">` : '<div class="prompt-card-placeholder"><i data-lucide="text-cursor-input"></i><span>提示词</span></div>'}
            <input class="prompt-card-check" type="checkbox" data-prompt-check="${escapeAttr(item.id)}" aria-label="选择 ${escapeAttr(item.name || '提示词')}" ${selectedPromptIds.has(item.id) ? 'checked' : ''} ${readonly ? 'disabled' : ''}>
        </div>
        <div class="prompt-card-body">
            <div class="prompt-card-title"><strong>${escapeHtml(item.name || '提示词')}</strong><span class="prompt-tag">${escapeHtml(promptCategoryLabel(item.category || 'custom'))}</span></div>
            <div class="prompt-card-scene">${escapeHtml(item.scene || '未填写用途说明')}</div>
            <div class="prompt-card-text">${escapeHtml(item.positive || '')}</div>
        </div>
    </article>`;
}
function renderPromptDetail(item, readonly){
    if(promptCreateMode && !readonly){
        return `
            <div class="panel-head">
                <div class="panel-title"><strong>新增提示词</strong><span>保存到当前提示词库</span></div>
                <div class="panel-actions">
                    <button class="hw-btn hw-btn-gold" type="button" data-prompt-create-save><i data-lucide="check"></i><span>保存</span></button>
                    <button class="hw-btn hw-btn-icon" type="button" data-prompt-edit-cancel title="取消"><i data-lucide="x"></i></button>
                </div>
            </div>
            <div class="detail-scroll">
                <div class="inline-edit-form">
                    <label class="inline-edit-field"><span>名称</span><input id="promptEditName" type="text" value="" placeholder="提示词名称"></label>
                    <label class="inline-edit-field"><span>用途说明</span><textarea id="promptEditScene" placeholder="用途说明"></textarea></label>
                    <label class="inline-edit-field"><span>正向提示词</span><textarea id="promptEditPositive" placeholder="正向提示词"></textarea></label>
                    <label class="inline-edit-field"><span>负向提示词</span><textarea id="promptEditNegative" placeholder="负向提示词"></textarea></label>
                </div>
            </div>
        `;
    }
    if(!item) return `<div class="panel-head"><div class="panel-title"><strong>提示词预览</strong><span>选择一条提示词查看全文</span></div></div><div class="detail-scroll"><div class="detail-empty"><i data-lucide="text-cursor-input"></i><span>暂无可预览提示词</span></div></div>`;
    if(promptEditMode && item.id === selectedPromptId && !readonly){
        return `
            <div class="panel-head">
                <div class="panel-title"><strong>编辑提示词</strong><span>在当前库内保存</span></div>
                <div class="panel-actions">
                    <button class="hw-btn hw-btn-gold" type="button" data-prompt-edit-save="${escapeAttr(item.id)}"><i data-lucide="check"></i><span>保存</span></button>
                    <button class="hw-btn hw-btn-icon" type="button" data-prompt-edit-cancel title="取消"><i data-lucide="x"></i></button>
                </div>
            </div>
            <div class="detail-scroll">
                <div class="inline-edit-form">
                    <label class="inline-edit-field"><span>名称</span><input id="promptEditName" type="text" value="${escapeAttr(item.name || '')}" placeholder="提示词名称"></label>
                    <label class="inline-edit-field"><span>用途说明</span><textarea id="promptEditScene" placeholder="用途说明">${escapeHtml(item.scene || '')}</textarea></label>
                    <label class="inline-edit-field"><span>正向提示词</span><textarea id="promptEditPositive" placeholder="正向提示词">${escapeHtml(item.positive || '')}</textarea></label>
                    <label class="inline-edit-field"><span>负向提示词</span><textarea id="promptEditNegative" placeholder="负向提示词">${escapeHtml(item.negative || '')}</textarea></label>
                </div>
            </div>
        `;
    }
    const params = item.params && typeof item.params === 'object' ? Object.entries(item.params) : [];
    return `
        <div class="panel-head">
            <div class="panel-title"><strong>提示词预览</strong><span>${escapeHtml(promptCategoryLabel(item.category || 'custom'))}</span></div>
            <div class="panel-actions">
                <button class="hw-btn hw-btn-icon" type="button" data-prompt-edit-start="${escapeAttr(item.id)}" ${readonly ? 'disabled' : ''} title="编辑"><i data-lucide="pencil"></i></button>
                <button class="hw-btn hw-btn-icon hw-btn-danger ${pendingDeletePromptId === item.id ? 'detail-confirm' : ''}" type="button" data-prompt-delete="${escapeAttr(item.id)}" ${readonly ? 'disabled' : ''} title="${pendingDeletePromptId === item.id ? '再次点击确认删除' : '删除'}"><i data-lucide="trash-2"></i></button>
            </div>
        </div>
        <div class="detail-scroll">
            <div class="prompt-detail-head">
                <div class="prompt-detail-title">${escapeHtml(item.name || '提示词')}</div>
                <div class="prompt-detail-scene">${escapeHtml(item.scene || '未填写用途说明')}</div>
            </div>
            <section class="prompt-block">
                <div class="prompt-block-head"><span>正向提示词</span><span>${String(item.positive || '').length} 字符</span></div>
                <textarea class="prompt-block-body" readonly spellcheck="false">${escapeHtml(item.positive || '未填写')}</textarea>
            </section>
            <section class="prompt-block">
                <div class="prompt-block-head"><span>负向提示词</span><span>${String(item.negative || '').length} 字符</span></div>
                <textarea class="prompt-block-body negative" readonly spellcheck="false">${escapeHtml(item.negative || '未填写')}</textarea>
            </section>
            ${params.length ? `<div class="params-list">${params.map(([key, value]) => `<div class="param-row"><strong>${escapeHtml(key)}</strong><span>${escapeHtml(value)}</span></div>`).join('')}</div>` : ''}
        </div>
    `;
}
function downloadUrl(url, filename='download'){
    if(!url) return;
    const link = document.createElement('a');
    link.href = url;
    link.download = filename || '';
    link.target = '_blank';
    document.body.appendChild(link);
    link.click();
    link.remove();
}
async function exportWorkflowItems(ids){
    const items = (ids || []).map(id => findWorkflowItem(id)).filter(item => item?.url);
    if(!items.length) return;
    if(items.length > 100) throw new Error('每次最多导出100个工作流');
    const files = [];
    for(const item of items){
        const workflow_id = workflowSourceId(item);
        let content, extension;
        if(workflow_id){
            const graph = await apiJsonResponse(assetManagerApi.exportWorkflowDocument(workflow_id));
            content = JSON.stringify(graph, null, 2); extension = '.json';
        } else {
            const url = new URL(item.url, location.origin);
            if(url.origin !== location.origin) throw new Error('外部工作流文件请从原来源下载');
            const response = await assetManagerApi.getWorkflowFile(url.pathname + url.search);
            if(!response.ok) await apiJsonResponse(response);
            content = new Uint8Array(await response.arrayBuffer());
            if(content.length > 30 * 1024 * 1024) throw new Error('工作流文件超过导出限制');
            if(content[0] === 0x50 && content[1] === 0x4b && content[2] === 3 && content[3] === 4){
                extension = '.zip';
            } else {
                try { JSON.parse(new TextDecoder().decode(content)); extension = '.json'; }
                catch(_) { throw new Error('工作流内容不是受支持的JSON或ZIP文件'); }
            }
        }
        const name = String(item.name || 'workflow').replace(/[\\/:*?"<>|\x00-\x1f]/g, '_').slice(0, 100).replace(/\.(json|zip)$/i, '');
        files.push({name:items.length === 1 ? name + extension : `${name}-${workflowAssetId(item)}${extension}`, content});
    }
    const blob = files.length === 1 ? new Blob([files[0].content], {type:files[0].name.endsWith('.json') ? 'application/json' : 'application/zip'}) : zipWorkflowFiles(files);
    if(blob.size > 30 * 1024 * 1024) throw new Error('工作流导出包超过限制');
    const link = document.createElement('a');
    link.href = URL.createObjectURL(blob);
    link.download = files.length === 1 ? files[0].name : 'workflows.zip';
    document.body.appendChild(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(link.href), 1200);
    setStatus(`已导出 ${items.length} 个工作流`);
}
function assetDownloadName(item){
    let name = String(item?.name || 'asset');
    const urlPath = String(item?.url || '').split('?')[0];
    const dot = urlPath.lastIndexOf('.');
    const ext = dot > urlPath.lastIndexOf('/') ? urlPath.slice(dot) : '';
    if(ext && !name.toLowerCase().endsWith(ext.toLowerCase())) name += ext;
    return name;
}
function downloadAssetItem(id){
    const item = findAssetItem(id);
    if(!item || !item.url){ setStatus('没有可下载的素材'); return; }
    const name = assetDownloadName(item);
    // 走 /api/download-output 强制以附件形式下载（带正确文件名/后缀），而不是在浏览器里打开预览。
    downloadUrl(`/api/download-output?url=${encodeURIComponent(item.url)}&name=${encodeURIComponent(name)}`, name);
    setStatus('已开始下载');
}
async function downloadSelectedAssets(){
    const items = [...selectedAssetIds].map(id => findAssetItem(id)).filter(it => it?.url);
    if(!items.length){ setStatus('没有可下载的素材'); return; }
    if(items.length === 1){ downloadAssetItem(items[0].id); return; }
    setStatus(`正在打包 ${items.length} 个素材...`);
    try {
        const res = await assetManagerApi.downloadCanvasAssets({filename:'assets.zip', items:items.map(it => ({url:it.url, name:assetDownloadName(it)}))});
        if(!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || '下载失败');
        const blob = await res.blob();
        const link = document.createElement('a');
        link.href = URL.createObjectURL(blob);
        link.download = 'assets.zip';
        document.body.appendChild(link);
        link.click();
        link.remove();
        setTimeout(() => URL.revokeObjectURL(link.href), 1200);
        setStatus(`已下载 ${items.length} 个素材`);
    } catch(err){
        setStatus(err.message || '下载失败');
    }
}
async function renameWorkflowItem(id){
    const item = findWorkflowItem(id);
    if(!item) return;
    const workflow_id = workflowSourceId(item);
    // 用户输入前捕获来源版本；输入期间发生并发编辑时拒绝覆盖。
    const source = workflow_id ? await apiJsonResponse(assetManagerApi.getWorkflowDocument(workflow_id)) : null;
    const name = window.prompt('工作流名称', item?.name || '');
    if(!item || !String(name || '').trim()) return;
    if(workflow_id){
        await apiJsonResponse(assetManagerApi.renameWorkflowDocument(workflow_id, {name:String(name).trim(), expected_version:source.version}));
        await loadAll(); setStatus('已重命名工作流'); return;
    }
    const data = await apiJsonResponse(assetManagerApi.updateAssetLibraryItem(id, {name, expected_version:assetLibraryVersionForItem(id)}));
    setAssetLibrary(data.library || assetLibrary);
    render();
    setStatus('已重命名工作流');
}
async function deleteWorkflowItem(id){
    if(workflowDeleteBusy.has(id)) return;
    const item = findWorkflowItem(id);
    if(!item) return;
    const workflow_id = workflowSourceId(item);
    if(pendingDeleteAssetId !== id){
        // 取版本期间锁定当前动作，避免快速点击重复进入首次确认。
        workflowDeleteBusy.add(id);
        render();
        try {
            if(workflow_id){
                const source = await apiJsonResponse(assetManagerApi.getWorkflowDocument(workflow_id));
                workflowDeleteVersions.set(id, source.version);
            }
            setPendingDeleteAssetId(id);
            setStatus('再次点击确认删除工作流');
        } finally {
            workflowDeleteBusy.delete(id);
            render();
        }
        return;
    }
    if(workflow_id){
        workflowDeleteBusy.add(id);
        render();
        let deleteError = null;
        try {
            await apiJsonResponse(assetManagerApi.deleteWorkflowDocument(workflow_id, workflowDeleteVersions.get(id)));
        } catch(error) {
            deleteError = error;
        } finally { workflowDeleteBusy.delete(id); workflowDeleteVersions.delete(id); setPendingDeleteAssetId(''); render(); }
        if(deleteError){
            if(deleteError.status === 409 && deleteError.code === 'VERSION_CONFLICT'){
                setStatus(`${VERSION_CONFLICT_MESSAGE}（版本冲突）`);
                return;
            }
            throw deleteError;
        }
        selectedWorkflowIds.delete(id); setSelectedWorkflowId('');
        await loadAll(); setStatus('已删除工作流'); return;
    }
    const data = await apiJsonResponse(assetManagerApi.deleteAssetLibraryItem(id, assetLibraryVersionForItem(id)));
    setAssetLibrary(data.library || assetLibrary);
    selectedWorkflowIds.delete(id);
    setSelectedWorkflowId('');
    setPendingDeleteAssetId('');
    render();
    setStatus('已删除工作流');
}
async function deleteSelectedWorkflows(){
    const ids = [...selectedWorkflowIds];
    if(!ids.length) return;
    const sources = [];
    for(const id of ids){
        const workflow_id = workflowSourceId(findWorkflowItem(id));
        if(!workflow_id) break;
        const source = await apiJsonResponse(assetManagerApi.getWorkflowDocument(workflow_id));
        sources.push({workflow_id, version:source.version});
    }
    if(sources.length === ids.length){
        if(!window.confirm(`确认删除这${ids.length}个工作流文档？`)) return;
        let removed = 0, failure = null;
        try {
            for(const source of sources){
                await apiJsonResponse(assetManagerApi.deleteWorkflowDocument(source.workflow_id, source.version)); removed++;
            }
        } catch(error){ failure = error; }
        selectedWorkflowIds.clear(); setSelectedWorkflowId(''); await loadAll();
        setStatus(failure ? `已删除${removed}个，其余未删除：${failure.message}` : `已删除${removed}个工作流`);
        return;
    }
    const data = await apiJsonResponse(assetManagerApi.deleteAssetLibraryItems({library_id:activeWorkflowLibraryId, asset_ids:ids, expected_version:requireExpectedVersion(activeWorkflowLibrary()?.version, '素材库'), expected_versions:expectedAssetLibraryVersions(ids)}));
    setAssetLibrary(data.library || assetLibrary);
    selectedWorkflowIds.clear();
    setSelectedWorkflowId('');
    render();
    setStatus(`已删除 ${data.removed || 0} 个工作流`);
}
async function linkSelectedRegistryAssets(){
    if(!activeProjectId || !selectedProjectEntityId || !registrySelectedIds.size) return;
    const data = await apiJsonResponse(assetManagerApi.linkRegistryProjectAssets(activeProjectId, {entity_id:selectedProjectEntityId,asset_ids:[...registrySelectedIds],role:'reference'}));
    await loadProjectBoard(activeProjectId);
    setStatus(`已关联 ${data.linked || 0} 个素材`);
}
function detailViewerPromptText(item={}){
    return [
        item.scene ? `用途说明\n${item.scene}` : '',
        item.positive ? `正向提示词\n${item.positive}` : '',
        item.negative ? `负向提示词\n${item.negative}` : '',
    ].filter(Boolean).join('\n\n');
}
function formatModelCount(value){
    const number = Number(value || 0);
    return Number.isFinite(number) && number > 0 ? new Intl.NumberFormat('zh-CN').format(number) : '未解析';
}
function renderDetailFontLibrary(item){
    const fonts = detailViewerFontItems();
    return `<aside class="asset-detail-font-library ${detailViewerFontListCollapsed ? 'is-collapsed' : ''}" data-detail-font-library>
        <button class="asset-detail-font-library-toggle" type="button" data-detail-font-library-toggle aria-expanded="${!detailViewerFontListCollapsed}" title="${detailViewerFontListCollapsed ? '展开字体列表' : '收起字体列表'}"><i data-lucide="${detailViewerFontListCollapsed ? 'panel-left-open' : 'panel-left-close'}"></i></button>
        <div class="asset-detail-font-library-content">
            <header><div><strong>当前资产库字体</strong><span>按名称排列，点击切换</span></div><em>${fonts.length}</em></header>
            <div class="asset-detail-font-library-list">${fonts.map(font => `<button class="${font.id === item.id ? 'is-active' : ''}" type="button" data-detail-font-item="${escapeAttr(font.id || '')}" ${font.id === item.id ? 'aria-current="true"' : ''}><i data-lucide="type"></i><span title="${escapeAttr(font.name || '未命名字体')}">${escapeHtml(font.name || '未命名字体')}</span><small>${escapeHtml(assetFileExtension(font).toUpperCase() || 'FONT')}</small></button>`).join('') || '<div class="detail-empty"><span>当前资产库没有其他字体</span></div>'}</div>
        </div>
    </aside>`;
}
function textVersionActionLabel(changeType=''){
    return ({baseline:'原始快照',save:'保存正文',restore:'恢复版本',edit:'编辑版本'}[changeType] || '版本记录');
}
function customAssetLibraryAllItems(board){
    if(!board) return [];
    const kinds = new Set(Array.isArray(board.kinds) ? board.kinds : []);
    const extensions = new Set(Array.isArray(board.extensions) ? board.extensions : []);
    const sources = Array.isArray(board.sources) ? board.sources : [];
    const sourceIds = new Set([...sources.map(source => source.id), ...(board.excludedSourceIds || [])]);
    const visibleSourceIds = new Set(sources.filter(source => !source.hidden).map(source => source.id));
    return registryAssets.filter(item => {
        const categoryMatch = board.preset === 'other' || kinds.has(String(item.kind || '')) || extensions.has(registryAssetExtension(item));
        if(!categoryMatch) return false;
        const sourceId = String(item.storage_root_id || '');
        if(board.sourceBoard) return sourceId === String(board.rootId || '');
        if(sourceIds.has(sourceId)) return visibleSourceIds.has(sourceId);
        return !board.automaticHidden;
    });
}
function toggleDetailFontLibrary(){
    const overlay = document.querySelector('.asset-detail-viewer');
    const workspace = overlay?.querySelector?.('[data-detail-font-workspace]');
    const library = overlay?.querySelector?.('[data-detail-font-library]');
    const button = library?.querySelector?.('[data-detail-font-library-toggle]');
    if(!workspace || !library || !button) return;
    setDetailViewerFontListCollapsed(!detailViewerFontListCollapsed);
    try { localStorage.setItem(ASSET_DETAIL_FONT_LIST_COLLAPSED_KEY, String(detailViewerFontListCollapsed)); } catch(_) {}
    workspace.classList.toggle('asset-detail-font-library-collapsed', detailViewerFontListCollapsed);
    library.classList.toggle('is-collapsed', detailViewerFontListCollapsed);
    button.setAttribute('aria-expanded', String(!detailViewerFontListCollapsed));
    button.title = detailViewerFontListCollapsed ? '展开字体列表' : '收起字体列表';
    button.innerHTML = `<i data-lucide="${detailViewerFontListCollapsed ? 'panel-left-open' : 'panel-left-close'}"></i>`;
    refreshIcons();
    requestAnimationFrame(() => fitDetailFontLineWidth(overlay));
}
async function importDetailVideoBlobToLibrary(blob,filename,rootId){
    if(!rootId) throw new Error('请选择可写素材库');
    const form = new FormData();
    form.append('root_id',rootId);
    form.append('folder','');
    form.append('file',blob,filename);
    const data = await apiJsonResponse(assetManagerApi.importRegistryAsset(form));
    return mergeDetailExportedRegistryAsset(data.asset || {});
}
async function copyDetailVideoFrameToClipboard(videoId=''){
    const video = detailViewerVideo(videoId);
    if(!video || !(video.videoWidth > 0) || !(video.videoHeight > 0)) throw new Error('当前视频帧尚未载入');
    if(!navigator.clipboard?.write || typeof ClipboardItem === 'undefined') throw new Error('当前浏览器不支持复制视频帧到剪贴板');
    const canvas = document.createElement('canvas');
    canvas.width = video.videoWidth; canvas.height = video.videoHeight;
    canvas.getContext('2d')?.drawImage(video,0,0,canvas.width,canvas.height);
    const blobPromise = new Promise((resolve,reject) => canvas.toBlob(value => value ? resolve(value) : reject(new Error('当前视频帧生成失败')),'image/png'));
    await navigator.clipboard.write([new ClipboardItem({'image/png':blobPromise})]);
    setStatus(`已复制当前帧 · ${formatDetailVideoTime(video.currentTime || 0)}`);
}
function beginDetailMediaLibraryDrag(event){
    const item = event.target.closest?.('[data-detail-media-library-item]');
    if(!item || !event.dataTransfer) return;
    setDetailMediaLibraryDragId(item.dataset.detailMediaLibraryItem || '');
    event.dataTransfer.effectAllowed = 'copy';
    event.dataTransfer.setData('application/x-gods-reference-asset',detailMediaLibraryDragId);
    event.dataTransfer.setData('text/plain',detailMediaLibraryDragId);
}
async function saveDetailImageEditorToLibrary(){
    const state = detailImageEditorState;
    if(!state || state.saving) return;
    state.saving = true;
    renderDetailImageEditor();
    try {
        if(state.dirty) await saveDetailImageEditorVersion();
        const rootId = detailReferenceCurrentRootId();
        if(!rootId) throw new Error('当前素材库没有可写的存储来源');
        const blob = await detailImageEditorBlob();
        const safeStem = String(state.name || '图片').replace(/\.[^.]+$/,'').replace(/[<>:"/\\|?*\x00-\x1f]+/g,'_').slice(0,120) || '图片';
        const form = new FormData();
        form.append('file',new File([blob],`${safeStem}_版本V${state.assetVersionNumber || 1}_${Date.now()}.png`,{type:'image/png'}));
        form.append('root_id',rootId);
        const board = assetBoardById(detailViewerState.originTab);
        const folder = board ? boardFolderSelection[board.id] || '' : '';
        if(folder && folder !== '__root__') form.append('folder',folder);
        const data = await apiJsonResponse(assetManagerApi.importRegistryAsset(form));
        const asset = normalizeRegistryAsset(data.asset || {});
        if(!asset.id) throw new Error('编辑图片导入当前库失败');
        const known=registryAssets.some(item=>item.id===asset.id);
        setRegistryAssets([asset,...registryAssets.filter(item=>item.id!==asset.id)]);
        if(!known && registryTotalKnown) setRegistryTotal(registryTotal + 1);
        setStatus(`编辑图片已另存到当前库并完成导入：${asset.name || state.name}`);
    } finally {
        state.saving = false;
        if(detailImageEditorState === state) renderDetailImageEditor();
    }
}
async function createAssetLibrary(){
    const name = window.prompt('资产库名称', '新资产库');
    if(!String(name || '').trim()) return;
    const data = await apiJsonResponse(assetManagerApi.createAssetLibrary({name, expected_version:requireExpectedVersion(assetLibrary.version, '素材库目录')}));
    setAssetLibrary(data.library || assetLibrary);
    setActiveAssetLibraryId(data.asset_library?.id || activeAssetLibraryId);
    setActiveAssetCategoryId('');
    setSelectedAssetId('');
    render();
}
async function deleteWorkflowCategory(){
    const cat = activeWorkflowCategory();
    if(!cat) return;
    const libraryId = cat.__libraryId || activeWorkflowLibraryId;
    const key = `workflow-cat:${libraryId}:${cat.id}`;
    if(pendingTreeDelete !== key){
        setPendingTreeDelete(key);
        setWorkflowTreeEdit(null);
        render();
        setStatus('再次点击确认删除工作流分组');
        return;
    }
    const data = await apiJsonResponse(assetManagerApi.deleteAssetLibraryCategory(cat.id, {libraryId, expectedVersion:requireExpectedVersion(cat.version, '素材分类')}));
    setAssetLibrary(data.library || assetLibrary);
    setActiveWorkflowCategoryId('');
    setSelectedWorkflowId('');
    selectedWorkflowIds.clear();
    setPendingTreeDelete('');
    render();
    setStatus('工作流分组已删除');
}
async function renameAssetLibrary(){
    const lib = activeAssetLibrary();
    const name = window.prompt('资产库名称', lib?.name || '');
    if(!lib || !String(name || '').trim()) return;
    const data = await apiJsonResponse(assetManagerApi.renameAssetLibrary(lib.id, {name, expected_version:requireExpectedVersion(lib.version, '素材库')}));
    setAssetLibrary(data.library || assetLibrary);
    render();
}
async function deleteAssetLibrary(){
    const lib = activeAssetLibrary();
    if(!lib) return;
    const key = `asset-lib:${lib.id}`;
    if(pendingTreeDelete !== key){
        setPendingTreeDelete(key);
        setAssetTreeEdit(null);
        render();
        setStatus('再次点击确认删除资产库');
        return;
    }
    const data = await apiJsonResponse(assetManagerApi.deleteAssetLibrary(lib.id, requireExpectedVersion(lib.version, '素材库')));
    setAssetLibrary(data.library || assetLibrary);
    setActiveAssetLibraryId(assetLibrary.active_library_id || assetLibraries()[0]?.id || '');
    setActiveAssetCategoryId('');
    setActiveAssetClassFilter('');
    setSelectedAssetId('');
    selectedAssetIds.clear();
    setPendingTreeDelete('');
    render();
}
async function createAssetCategory(){
    const name = window.prompt('分组名称', '新分组');
    if(!String(name || '').trim()) return;
    const data = await apiJsonResponse(assetManagerApi.createAssetLibraryCategory({library_id:activeAssetLibraryId, name, type:'image', expected_version:requireExpectedVersion(activeAssetLibrary()?.version, '素材库')}));
    setAssetLibrary(data.library || assetLibrary);
    setActiveAssetCategoryId(data.category?.id || activeAssetCategoryId);
    setActiveAssetClassFilter('');
    setSelectedAssetId('');
    render();
}
async function renameAssetCategory(){
    const cat = activeAssetCategory();
    const name = window.prompt('分组名称', cat?.name || '');
    if(!cat || !String(name || '').trim()) return;
    const data = await apiJsonResponse(assetManagerApi.renameAssetLibraryCategory(cat.id, {name, library_id:activeAssetLibraryId, expected_version:requireExpectedVersion(cat.version, '素材分类')}));
    setAssetLibrary(data.library || assetLibrary);
    render();
}
async function deleteAssetCategory(){
    const cat = activeAssetCategory();
    if(!cat) return;
    const key = `asset-cat:${activeAssetLibraryId}:${cat.id}`;
    if(pendingTreeDelete !== key){
        setPendingTreeDelete(key);
        setAssetTreeEdit(null);
        render();
        setStatus('再次点击确认删除分组');
        return;
    }
    const data = await apiJsonResponse(assetManagerApi.deleteAssetLibraryCategory(cat.id, {libraryId:activeAssetLibraryId, expectedVersion:requireExpectedVersion(cat.version, '素材分类')}));
    setAssetLibrary(data.library || assetLibrary);
    setActiveAssetCategoryId('');
    setActiveAssetClassFilter('');
    setSelectedAssetId('');
    selectedAssetIds.clear();
    setPendingTreeDelete('');
    render();
}
async function renameAssetItem(id){
    const item = findAssetItem(id);
    if(!item) return;
    setSelectedAssetId(id);
    render();
    requestAnimationFrame(() => {
        const input = [...root.querySelectorAll('[data-asset-inline-name]')].find(el => el.dataset.assetInlineName === id);
        input?.focus();
        input?.select?.();
    });
}
async function deleteAssetItem(id){
    const item = findAssetItem(id);
    if(!item) return;
    const data = await apiJsonResponse(assetManagerApi.deleteAssetLibraryItem(id, assetLibraryVersionForItem(id)));
    setAssetLibrary(data.library || assetLibrary);
    selectedAssetIds.delete(id);
    if(selectedAssetId === id) setSelectedAssetId('');
    setPendingDeleteAssetId('');
    render();
    setStatus('素材已删除');
}
async function deleteSelectedAssets(){
    if(!selectedAssetIds.size) return;
    const ids = [...selectedAssetIds];
    const data = await apiJsonResponse(assetManagerApi.deleteAssetLibraryItems({library_id:activeAssetLibraryId, asset_ids:ids, expected_version:requireExpectedVersion(activeAssetLibrary()?.version, '素材库'), expected_versions:expectedAssetLibraryVersions(ids)}));
    setAssetLibrary(data.library || assetLibrary);
    if(ids.includes(selectedAssetId)) setSelectedAssetId('');
    selectedAssetIds.clear();
    setPendingBatchDelete('');
    render();
    setStatus(`已删除 ${data.removed || ids.length} 个素材`);
}
function setAssetClipboard(mode){
    if(!selectedAssetIds.size) return;
    gwSetAssetClipboard({
        mode,
        ids:[...selectedAssetIds],
        sourceLibraryId:activeAssetLibraryId,
        sourceCategoryId:activeAssetCategoryId,
        items:[...selectedAssetIds].map(id => findAssetItem(id)).filter(Boolean)
    });
    selectedAssetIds.clear();
    setPendingBatchDelete('');
    render();
    const label = mode === 'cut' ? '剪切' : '复制';
    setStatus(`${label}了 ${assetClipboard.ids.length} 个素材，切换分组后粘贴`);
}
async function pasteAssetClipboard(){
    if(!assetClipboard?.ids?.length) return;
    if(assetClipboard.mode === 'cut'){
        if(assetClipboard.sourceLibraryId === activeAssetLibraryId && assetClipboard.sourceCategoryId === activeAssetCategoryId) return;
        const sourceVersion = requireExpectedVersion(assetLibraries().find(lib => lib.id === assetClipboard.sourceLibraryId)?.version, '素材库');
        const data = await apiJsonResponse(assetManagerApi.moveAssetLibraryItems({library_id:assetClipboard.sourceLibraryId, target_library_id:activeAssetLibraryId, category_id:activeAssetCategoryId, asset_ids:assetClipboard.ids, expected_version:sourceVersion, expected_versions:expectedAssetLibraryVersions(assetClipboard.ids)}));
        setAssetLibrary(data.library || assetLibrary);
        setStatus(`已移动 ${data.moved || 0} 个素材`);
    } else {
        const items = (assetClipboard.items || []).map(item => ({url:item.url, name:item.name || 'asset'})).filter(item => item.url);
        const data = await apiJsonResponse(assetManagerApi.createAssetLibraryItemsBatch({
            library_id:activeAssetLibraryId,
            category_id:activeAssetCategoryId,
            expected_version:requireExpectedVersion(activeAssetLibrary()?.version, '素材库'),
            items,
        }));
        setAssetLibrary(data.library || assetLibrary);
        setStatus(`已复制 ${data.items?.length || 0} 个素材`);
    }
    gwSetAssetClipboard(null);
    selectedAssetIds.clear();
    setSelectedAssetId('');
    render();
}
async function moveSelectedAssets(){
    if(!selectedAssetIds.size || !assetMoveTarget) return;
    const [targetLibraryId, targetCategoryId] = assetMoveTarget.split('::');
    if(!targetLibraryId || !targetCategoryId) return;
    const ids = [...selectedAssetIds];
    const data = await apiJsonResponse(assetManagerApi.moveAssetLibraryItems({library_id:activeAssetLibraryId, target_library_id:targetLibraryId, category_id:targetCategoryId, asset_ids:ids, expected_version:requireExpectedVersion(activeAssetLibrary()?.version, '素材库'), expected_versions:expectedAssetLibraryVersions(ids)}));
    setAssetLibrary(data.library || assetLibrary);
    selectedAssetIds.clear();
    if(ids.includes(selectedAssetId)) setSelectedAssetId('');
    render();
    setStatus(`已移动 ${data.moved || 0} 个素材`);
}
async function createPromptLibrary(){
    const name = window.prompt('提示词库名称', '新提示词库');
    if(!String(name || '').trim()) return;
    const data = await apiJsonResponse(assetManagerApi.createPromptLibrary({name, expected_version:requireExpectedVersion(promptLibrary.version, '提示词库目录')}));
    setPromptLibrary(data.library || promptLibrary);
    setActivePromptLibraryId(data.prompt_library?.id || activePromptLibraryId);
    setActivePromptCategory('all');
    setSelectedPromptId('');
    render();
}
async function deletePromptCategory(){
    if(activePromptCategory === 'all'){
        setStatus('请选择具体分组后删除');
        return;
    }
    const key = `prompt-cat:${activePromptCategory}`;
    if(pendingTreeDelete !== key){
        setPendingTreeDelete(key);
        setPromptTreeEdit(null);
        render();
        setStatus('再次点击确认删除分组');
        return;
    }
    const data = await apiJsonResponse(assetManagerApi.deletePromptLibraryCategory(activePromptCategory, promptCategoryVersion(activePromptCategory)));
    setPromptLibrary(data.library || promptLibrary);
    setActivePromptCategory('all');
    setPendingTreeDelete('');
    render();
    setStatus('分组已删除');
}
async function renamePromptLibrary(){
    const lib = activePromptLibrary();
    const name = window.prompt('提示词库名称', lib?.name || '');
    if(!lib || !String(name || '').trim()) return;
    const data = await apiJsonResponse(assetManagerApi.renamePromptLibrary(lib.id, {name, expected_version:requireExpectedVersion(lib.version, '提示词库')}));
    setPromptLibrary(data.library || promptLibrary);
    render();
}
async function deletePromptLibrary(){
    const lib = activePromptLibrary();
    if(!lib) return;
    if(isSystemPromptLibrary(lib)){ setStatus('系统提示词库不能删除'); return; }
    const key = `prompt-lib:${lib.id}`;
    if(pendingTreeDelete !== key){
        setPendingTreeDelete(key);
        setPromptTreeEdit(null);
        render();
        setStatus('再次点击确认删除提示词库');
        return;
    }
    const data = await apiJsonResponse(assetManagerApi.deletePromptLibrary(lib.id, requireExpectedVersion(lib.version, '提示词库')));
    setPromptLibrary(data.library || promptLibrary);
    setActivePromptLibraryId(promptLibrary.active_library_id || promptLibraries()[0]?.id || 'system');
    setActivePromptCategory('all');
    setSelectedPromptId('');
    selectedPromptIds.clear();
    setPendingTreeDelete('');
    render();
    setStatus('提示词库已删除');
}
async function createPromptItem(){
    const lib = activePromptLibrary();
    if(!lib) return;
    const name = window.prompt('提示词名称', '新提示词');
    if(!String(name || '').trim()) return;
    const scene = window.prompt('用途说明', '') || '';
    const positive = window.prompt('正向提示词内容', '');
    if(!String(positive || '').trim()) return;
    const negative = window.prompt('负向提示词内容', '') || '';
    const category = activePromptCategory === 'all' ? 'custom' : activePromptCategory;
    const data = await apiJsonResponse(assetManagerApi.createPromptLibraryItem({library_id:lib.id, name, positive, negative, category, scene, expected_version:await currentPromptItemsRevision()}));
    setPromptLibrary(data.library || promptLibrary);
    setSelectedPromptId(data.item?.id || selectedPromptId);
    render();
}
async function savePromptCreate(){
    const lib = activePromptLibrary();
    const name = document.getElementById('promptEditName')?.value || '';
    const scene = document.getElementById('promptEditScene')?.value || '';
    const positive = document.getElementById('promptEditPositive')?.value || '';
    const negative = document.getElementById('promptEditNegative')?.value || '';
    if(!lib) return;
    if(!String(name || '').trim() || !String(positive || '').trim()){
        setStatus('名称和正向提示词不能为空');
        return;
    }
    const category = activePromptCategory === 'all' ? 'custom' : activePromptCategory;
    const data = await apiJsonResponse(assetManagerApi.createPromptLibraryItem({library_id:lib.id, name, positive, negative, category, scene, expected_version:await currentPromptItemsRevision()}));
    setPromptLibrary(data.library || promptLibrary);
    setSelectedPromptId(data.item?.id || selectedPromptId);
    setPromptCreateMode(false);
    render();
    setStatus('提示词已新增');
}
async function editPromptItem(id){
    const item = findPromptItem(id);
    const lib = activePromptLibrary();
    if(!item || !lib) return;
    const name = window.prompt('提示词名称', item.name || '');
    if(!String(name || '').trim()) return;
    const scene = window.prompt('用途说明', item.scene || '') || '';
    const positive = window.prompt('正向提示词内容', item.positive || '');
    if(!String(positive || '').trim()) return;
    const negative = window.prompt('负向提示词内容', item.negative || '') || '';
    const data = await apiJsonResponse(assetManagerApi.updatePromptLibraryItem(id, {library_id:lib.id, name, positive, negative, category:item.category || 'custom', scene, expected_version:await promptItemVersion(id)}));
    setPromptLibrary(data.library || promptLibrary);
    setSelectedPromptId(id);
    render();
}
async function savePromptEdit(id){
    const item = findPromptItem(id);
    const lib = activePromptLibrary();
    const name = document.getElementById('promptEditName')?.value || '';
    const scene = document.getElementById('promptEditScene')?.value || '';
    const positive = document.getElementById('promptEditPositive')?.value || '';
    const negative = document.getElementById('promptEditNegative')?.value || '';
    if(!item || !lib) return;
    if(!String(name || '').trim() || !String(positive || '').trim()){
        setStatus('名称和正向提示词不能为空');
        return;
    }
    const data = await apiJsonResponse(assetManagerApi.updatePromptLibraryItem(id, {library_id:lib.id, name, positive, negative, category:item.category || 'custom', scene, expected_version:await promptItemVersion(id)}));
    setPromptLibrary(data.library || promptLibrary);
    setSelectedPromptId(id);
    setPromptEditMode(false);
    render();
    setStatus('提示词已保存');
}
async function deletePromptItem(id){
    const item = findPromptItem(id);
    if(!item) return;
    const data = await apiJsonResponse(assetManagerApi.deletePromptLibraryItem(id, await currentPromptItemsRevision()));
    setPromptLibrary(data.library || promptLibrary);
    selectedPromptIds.delete(id);
    if(selectedPromptId === id) setSelectedPromptId('');
    setPendingDeletePromptId('');
    render();
    setStatus('提示词已删除');
}
async function deleteSelectedPrompts(){
    if(!selectedPromptIds.size) return;
    if(pendingBatchDelete !== 'prompt'){
        setPendingBatchDelete('prompt');
        render();
        setStatus('再次点击确认删除所选提示词');
        return;
    }
    const ids = [...selectedPromptIds];
    const data = await apiJsonResponse(assetManagerApi.deletePromptLibraryItems({ids, expected_version:await currentPromptItemsRevision()}));
    setPromptLibrary(data.library || promptLibrary);
    if(ids.includes(selectedPromptId)) setSelectedPromptId('');
    selectedPromptIds.clear();
    setPendingBatchDelete('');
    render();
}

// 【QIN-591】域函数面注册：主文件 gwCall('organize', fn) 经由此对象分派。
GW_DEFERRED['organize'] = {
    activateAssetBoard,
    addSelectedRegistryAssetsToGroup,
    applyAssetOrganizeDropHint,
    applyManagedSelection,
    assetOrganizeMoveAssets,
    assetOrganizeMoveFolder,
    assetOrganizePushUndo,
    assetOrganizeRefresh,
    beginAssetOrganizeRename,
    cancelAssetOrganizeRename,
    cancelBoardSourceEditor,
    clearAssetOrganizeDropHint,
    clearSearchSelection,
    closeAssetOrganizeDialog,
    closeBoardEditModal,
    closeBoardSourceModal,
    closeCreateLibraryModal,
    closeCustomBoardFolderCreateModal,
    commitAssetOrganizeRename,
    confirmBoardSource,
    copyDetailVideoFrameToClipboard,
    createPresetAssetBoard,
    currentAssetItems,
    currentPromptItems,
    currentWorkflowItems,
    deleteAssetCategory,
    deleteAssetItem,
    deleteAssetLibrary,
    deleteBoardSource,
    deleteCustomAssetBoard,
    deletePromptCategory,
    deletePromptItem,
    deletePromptLibrary,
    deleteRegistryPreset,
    deleteSelectedAssets,
    deleteSelectedPrompts,
    deleteSelectedWorkflows,
    deleteWorkflowCategory,
    deleteWorkflowItem,
    downloadAssetItem,
    downloadRegistryPdf,
    downloadSelectedAssets,
    downloadSelectedRegistryArchive,
    dropAssetOrganize,
    editBoardSource,
    editPromptItem,
    exportWorkflowItems,
    linkSelectedRegistryAssets,
    loadCustomBoardFolderDirectories,
    loadProjectBoard,
    moveAssetBoardByKeyboard,
    openAssetOrganizeCreateDialog,
    openAssetOrganizeDialog,
    openBoardEditModal,
    openBoardSourceModal,
    openCreateLibraryModal,
    openCustomBoardFolderCreateModal,
    pasteAssetClipboard,
    refreshCASReadState,
    removeGeneratedSourceBoard,
    removeSelectedRegistryAssetsFromGroups,
    renameAssetItem,
    renameWorkflowItem,
    renderAssetManager,
    renderBoardSourceModal,
    renderCustomAssetLibrary,
    renderProjectManager,
    renderPromptManager,
    renderScriptAssetsManager,
    renderWorkflowManager,
    revealRegistryAssetFile,
    saveBoardEdit,
    saveCollapsedLibraryFolders,
    saveHiddenLibraryFolders,
    savePromptCreate,
    savePromptEdit,
    setAssetBoardHidden,
    setAssetClipboard,
    setBoardSourceVisible,
    showAssetOrganizeToast,
    submitCustomBoardFolderCreate,
    syncAssetSelectionDom,
    syncWorkflowSelectionDom,
    togglePreviewDeleteCategory,
};

export {
    activateAssetBoard,
    assetDownloadName,
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
    customAssetLibraryAllItems,
    detailViewerPromptText,
    downloadRegistryPdf,
    downloadUrl,
    formatModelCount,
    handleAssetOrganizeOverlayClick,
    importDetailVideoBlobToLibrary,
    registryDownloadName,
    renderBoardCreatePathBrowser,
    renderCreateLibraryModal,
    renderDetailFontLibrary,
    saveDetailImageEditorToLibrary,
    selectedAssetImageItems,
    syncBoardCreateDraftFromDom,
    textVersionActionLabel,
    toggleDetailFontLibrary,
    updateBoardCreatePath,
};
