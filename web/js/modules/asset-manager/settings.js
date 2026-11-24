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

// QIN-591：资产库延后域模块（settings）。首屏不加载；经 GW_DEFERRED 注册函数面，
// 由主文件网关（gwCall/gwLoad）按需加载或在空闲时段预热。函数体除跨文件赋值点改写外保持原样。

import {
    ALLOWED_ROOT_FIELD_KEY,
    ASSET_RACK_COLLAPSED_KEY,
    ASSET_RACK_EXPANDED_KEY,
    ASSET_RACK_ROOT_KEY,
    ASSET_REGISTRY_CUSTOM_GROUPS_KEY,
    ASSET_REGISTRY_VIEW_SETTINGS_KEY,
    AVATAR_PLATFORM_LABELS,
    AVATAR_SUPPORTED_PLATFORMS,
    DEFAULT_LOCAL_LIBRARY_NAME,
    LOCAL_CAPTION_SETTINGS_KEY,
    OPTIONAL_DIRECTORY_FIELDS,
    PROJECT_DIRECTORY_TEMPLATE_FILE_SUFFIXES,
    PROJECT_DIRECTORY_TEMPLATE_RESERVED_NAMES,
    PROJECT_DIRECTORY_TEMPLATE_TYPES,
    SIGNAL_FLOW_ACCENT_FALLBACK,
    STORAGE_KIND_LABELS,
    THUMBNAIL_CACHE_BLOCK_CODES,
    THUMBNAIL_CACHE_EXCLUSION_HINT,
    THUMBNAIL_DIRECTORY_FIELD_KEY,
    activeAssetClassFilter,
    activeAssetLibraryId,
    activeLocalLibraryId,
    activeLocalLibraryKind,
    activeLocalUploadClassFilter,
    activeProjectId,
    activeRegistryVisibleAssets,
    activeTab,
    allowedRootSettingsState,
    apiJsonResponse,
    apiProviders,
    assetAiSettingsState,
    assetAllowedRootPaths,
    assetBoardById,
    assetClassificationChips,
    assetClassifyBusy,
    assetFileExtension,
    assetKind,
    assetLibrary,
    assetLibraryVersionForItem,
    assetPreviewAllState,
    assetRackAutomaticItems,
    assetRackBoard,
    assetRackCollapsed,
    assetRackExpandedFolders,
    assetRackFolderTree,
    assetRackRootDiskPath,
    assetRackRoots,
    avatarRegisterProvider,
    boardFolderSelection,
    boardSourceSelection,
    canBrowseServerDirectories,
    canManageAllowedRoots,
    canManageProjectWorkspaceSettings,
    clientUploadSourceDir,
    copyTextToClipboard,
    currentStatusText,
    customBoardFolderParentPaths,
    detailImageEditorState,
    detailViewerCanEditText,
    detailViewerFileInfoCache,
    detailViewerState,
    detailViewerTextCache,
    directoryFieldInput,
    directorySettingsError,
    directorySettingsPage,
    directorySettingsReady,
    directorySettingsSaving,
    embeddedSettingsPage,
    escapeAttr,
    escapeHtml,
    findAssetItem,
    findLocalUpload,
    findWorkflowItem,
    gwSetAssetRackCollapsed, handleClick,
    isTauriClientMode,
    loadAssetAllowedRootPaths,
    loadLocalAssets,
    loadRegistryPrincipal,
    localAssets,
    localAutoClassify,
    localCaptionBusy,
    localCaptionModel,
    localCaptionPrompt,
    localCaptionProvider,
    localClassifyBusy,
    localClassifyPrompt,
    localClassifyPromptOpen,
    localLibraries,
    localUploadTree,
    normalizeRegistryAsset,
    pendingPurgeRoot,
    permissionsSettingsPage,
    projectDirectoryTemplateManagerState,
    refreshIcons,
    refreshRegistryAssets,
    registryActiveSelectedIds,
    registryAspect,
    registryAssets,
    registryCardSize,
    registryCustomGroups,
    registryDetail,
    registryDiskFolderCache,
    registryDiskFolderLoading,
    registryDiskFolderPermissionDenied,
    registryDiskFolderPermissionNoticed,
    registryFilterDefinition,
    registryGroupMode,
    registryIndexedFolders,
    registryLayout,
    registryNavFolder,
    registryNavFolderActive,
    registryPresets,
    registryPrincipal,
    registryPrincipalLoading,
    registryRackSnapshot,
    registryRegisteredFoldersLoaded,
    registrySimple,
    registryStatus,
    render,
    root,
    saveAllowedRootsRequest,
    saveStorageSettings,
    savedScrollPositions,
    selectedLocalUploadId,
    selectedLocalUploadIds,
    serverDirectoryBrowserState,
    setActiveLocalLibraryId,
    setAllowedRootSettingsState,
    setApiProviders,
    setAssetAiSettingsState,
    setAssetClassifyBusy,
    setAssetEditMode,
    setAssetLibrary,
    setDirectorySettingsSaving,
    setLocalAssets,
    setLocalAutoClassify,
    setLocalCaptionBusy,
    setLocalCaptionModel,
    setLocalCaptionPrompt,
    setLocalCaptionProvider,
    setLocalClassifyBusy,
    setLocalClassifyPrompt,
    setLocalLibraries,
    setLocalUploadTree,
    setProjectDirectoryTemplateManagerState,
    setRegistryAssets,
    setRegistryDetail,
    setRegistryDiskFolderPermissionDenied,
    setRegistryDiskFolderPermissionNoticed,
    setRegistryIndexedFolders,
    setRegistryNavFolder,
    setRegistryPresets,
    setRegistryRackSnapshot,
    setRegistryRegisteredFolders,
    setRegistryRegisteredFoldersLoaded,
    setRegistryRootId,
    setRegistryStatus,
    setSelectedAssetId,
    setSelectedLocalUploadId,
    setServerDirectoryBrowserState,
    setStatus,
    setThumbnailCacheSwitchState,
    setThumbnailCacheVersion,
    setThumbnailGeneratorState,
    storageCleanName,
    storageExtraDefaultName,
    storageExtraLocalEntries,
    storageLocalLibraryNames,
    storageSettingsState,
    tauriInvokeFn,
    thumbnailCacheSwitchState,
    thumbnailGeneratorState,
} from '../asset-manager.js';
import assetManagerApi from './api.js';
import {
    formatFileSize,
} from './formatters.js';
import assetManagerHttp from './http.js';
import {
    pathCompareKey,
} from './path-utils.js';
import {
    updateDetailImageEditorCanvasVersion,
} from './canvas-bridge.js';
import {
    renderRegistryFeatureControl,
} from './legacy-pages.js';
import {
    selectedAssetImageItems,
} from './organize.js';
import {
    detailImageEditorBlob,
    detailImageEditorSerialized,
    detailTextVersionParams,
    detailViewerFileKey,
    detailViewerItems,
    detailViewerSyncSelection,
    detailViewerTextKey,
    loadDetailTextVersions,
    loadDetailViewerFileInfo,
    markAssetFloatingSurface,
    previewCacheStats,
    renderDetailViewer,
    renderPreviewCacheDeletePanel,
} from './preview.js';
import {
    saveClientUploadSourceDir,
    selectedLocalUploadImageItems,
} from './upload.js';
import { GW_DEFERRED } from './state.js';

function signalFlowAccentColor(){
    const color = getComputedStyle(document.documentElement).getPropertyValue('--sf-cyan').trim();
    return /^#[0-9a-f]{6}$/i.test(color) ? color : SIGNAL_FLOW_ACCENT_FALLBACK;
}
function writeLocalCaptionSettings(){
    try {
        localStorage.setItem(LOCAL_CAPTION_SETTINGS_KEY, JSON.stringify({
            provider:localCaptionProvider || '',
            model:localCaptionModel || '',
            captionPrompt:localCaptionPrompt || '描述图片',
            classifyPrompt:localClassifyPrompt || '',
            autoClassify:Boolean(localAutoClassify)
        }));
    } catch(_) {}
}
function saveAssetRackCollapsed(collapsed){
    try { localStorage.setItem(ASSET_RACK_COLLAPSED_KEY, collapsed ? '1' : '0'); } catch(_) {}
}
function saveAssetRackExpandedFolders(){
    try { localStorage.setItem(ASSET_RACK_EXPANDED_KEY, JSON.stringify([...assetRackExpandedFolders])); } catch(_) {}
}
let registryRegisteredFoldersLoading = false;
let assetJxlTranscodeState = {loading:false, starting:false, cancelling:false, scan:null, job:null, pollingJobId:'', error:''};
let avatarBusyId = '';
let storageSettingsReturnFocus = null;
let workspaceSettingsState = {
    snapshot: null,
    loading: false,
    saving: false,
    target: 'ws_nas_01',
    machine: 'machine_local_01',
    path: '',
    error: '',
    generation: 0,
};
function setWorkspaceSettingsState(patch={}){ workspaceSettingsState = {...workspaceSettingsState, ...patch}; }
function captureWorkspaceMappingForm(options={}){
    // 任何重绘之前先捕获完整表单；重绘后仍按捕获值回填，防止读到默认机器。
    const machineInput = document.getElementById('workspaceMapMachine');
    const pathInput = document.getElementById('workspaceMapPath');
    const patch = {};
    if(machineInput) patch.machine = String(machineInput.value || '').trim() || workspaceSettingsState.machine;
    if(pathInput) patch.path = options.clearPath ? '' : String(pathInput.value || '').trim();
    if(Object.keys(patch).length) setWorkspaceSettingsState(patch);
    return {machine:workspaceSettingsState.machine, path:workspaceSettingsState.path};
}
function workspaceClientId(){
    try {
        let id = sessionStorage.getItem('gw_workspace_client_instance');
        if(!id){
            id = 'client_' + Math.random().toString(36).slice(2, 10);
            sessionStorage.setItem('gw_workspace_client_instance', id);
        }
        return id;
    } catch(_) { return 'client_local'; }
}
function workspaceErrorMessage(error){
    const code = String(error?.code || error?.detail?.code || '');
    const messages = {
        VERSION_CONFLICT:'工作区配置已更新，请重新读取后再提交',
        WORKSPACE_WRITE_CONFLICT:'另一个客户端正在写入工作区，请稍后重试',
        WORKSPACE_INCOMPATIBLE:'版本或授权不兼容，不能连接该 NAS 工作区',
        WORKSPACE_MAPPING_REQUIRED:'请先登记并核验一条共享路径映射',
        WORKSPACE_PATH_NOT_PERMITTED:'该目录未获服务端授权，需共享/授权给实际执行端',
        WORKSPACE_PATH_UNREACHABLE:'实际执行端无法访问该目录，需共享/授权或检查连接',
        WORKSPACE_WRITE_DENIED:'实际执行端无写权限，需共享/授权给执行端',
    };
    return messages[code] || String(error?.message || error?.detail?.message || '工作区操作失败，请重试');
}
function notifyWorkspaceChanged(){
    document.dispatchEvent(new CustomEvent('gw-workspace-changed', {detail:workspaceSettingsState.snapshot}));
}
function applyWorkspaceSnapshot(snapshot){
    setWorkspaceSettingsState({
        snapshot:snapshot || null,
        loading:false,
        saving:false,
        error:'',
        target:String(snapshot?.active_workspace_id || '') === 'ws_nas_01' ? 'ws_nas_01' : 'ws_local_01',
    });
    notifyWorkspaceChanged();
}
function selectWorkspaceTarget(workspaceId){
    setWorkspaceSettingsState({target:String(workspaceId || 'ws_local_01'), error:''});
    renderGlobalDirectorySettings();
}
async function workspaceRequest(path, method='GET', payload=null){
    const options = {method, credentials:'same-origin'};
    if(payload){
        options.headers = {'Content-Type':'application/json'};
        options.body = JSON.stringify(payload);
    }
    return apiJsonResponse(assetManagerHttp.request(path, options));
}
async function loadWorkspaceSnapshot(options={}){
    if(workspaceSettingsState.loading && !options.force) return;
    const generation = workspaceSettingsState.generation;
    setWorkspaceSettingsState({loading:true, error:''});
    if(!options.silentRender) renderGlobalDirectorySettings();
    try {
        const snapshot = await workspaceRequest('/api/workspaces');
        if(generation !== workspaceSettingsState.generation) return;
        applyWorkspaceSnapshot(snapshot);
        if(!options.silentRender) renderGlobalDirectorySettings();
    } catch(error) {
        if(generation !== workspaceSettingsState.generation) return;
        setWorkspaceSettingsState({loading:false, error:workspaceErrorMessage(error)});
        if(!options.silentRender) renderGlobalDirectorySettings();
    }
}
async function switchWorkspace(workspaceId){
    const snapshot = workspaceSettingsState.snapshot;
    if(workspaceSettingsState.saving || !snapshot) return;
    const target = (snapshot.workspaces || []).find(item => item.workspace_id === workspaceId);
    if(!target) return;
    if(target.workspace_id !== snapshot.active_workspace_id){
        const confirmed = window.confirm(`确认切换到「${target.display_name}」？切换后当前客户端只向该工作区写入。`);
        if(!confirmed){ setStatus('已取消切换工作区'); return; }
    }
    const generation = workspaceSettingsState.generation;
    setWorkspaceSettingsState({saving:true, error:''});
    renderGlobalDirectorySettings();
    setStatus('正在切换工作区…');
    try {
        const updated = await workspaceRequest('/api/workspaces/switch', 'POST', {
            workspace_id:target.workspace_id,
            expected_version:Number(snapshot.revision || 1),
            client_instance_id:workspaceClientId(),
        });
        if(generation !== workspaceSettingsState.generation) return;
        applyWorkspaceSnapshot(updated);
        await loadAssetAllowedRootPaths();
        setStatus(`已切换到「${target.display_name}」`);
        renderGlobalDirectorySettings();
    } catch(error) {
        if(generation !== workspaceSettingsState.generation) return;
        setWorkspaceSettingsState({saving:false, error:workspaceErrorMessage(error)});
        setStatus(workspaceErrorMessage(error));
        renderGlobalDirectorySettings();
    }
}
async function verifyWorkspaceMapping(){
    const form = captureWorkspaceMappingForm();
    const path = form.path;
    if(!path){ setStatus('请输入共享路径'); return; }
    const generation = workspaceSettingsState.generation;
    setWorkspaceSettingsState({saving:true, error:'', machine:form.machine, path});
    renderGlobalDirectorySettings();
    setStatus('正在核验服务端执行端权限…');
    try {
        const result = await workspaceRequest('/api/workspaces/verify', 'POST', {
            access_path:path,
            execution_machine_id:form.machine,
            capability:'read_write',
        });
        if(generation !== workspaceSettingsState.generation) return;
        setWorkspaceSettingsState({saving:false, error:''});
        const verifiedBy = String(result?.verified_by || '服务端执行上下文');
        const prefix = result?.status === 'verified' ? `服务端执行端可读写该路径（核验宿主：${verifiedBy}）` : '执行端核验未通过';
        setStatus(prefix);
        renderGlobalDirectorySettings();
    } catch(error) {
        if(generation !== workspaceSettingsState.generation) return;
        const message = workspaceErrorMessage(error);
        setWorkspaceSettingsState({saving:false, error:message});
        setStatus(message);
        renderGlobalDirectorySettings();
    }
}
async function addWorkspaceMapping(){
    const snapshot = workspaceSettingsState.snapshot;
    if(workspaceSettingsState.saving || !snapshot) return;
    const form = captureWorkspaceMappingForm();
    const path = form.path;
    if(!path){ setStatus('请输入共享路径'); return; }
    const generation = workspaceSettingsState.generation;
    const target = workspaceSettingsState.target;
    setWorkspaceSettingsState({saving:true, error:'', machine:form.machine, path});
    renderGlobalDirectorySettings();
    setStatus('正在登记共享映射…');
    try {
        const updated = await workspaceRequest('/api/workspaces/mappings', 'POST', {
            access_path:path,
            execution_machine_id:form.machine,
            capability:'read_write',
            workspace_id:target,
            expected_version:Number(snapshot.revision || 1),
        });
        if(generation !== workspaceSettingsState.generation) return;
        applyWorkspaceSnapshot(updated);
        const pathInput = document.getElementById('workspaceMapPath');
        if(pathInput) pathInput.value = '';
        setWorkspaceSettingsState({path:''});
        await loadAssetAllowedRootPaths();
        setStatus('共享映射已登记，未复制素材且未新增重复来源');
        renderGlobalDirectorySettings();
    } catch(error) {
        if(generation !== workspaceSettingsState.generation) return;
        const message = workspaceErrorMessage(error);
        setWorkspaceSettingsState({saving:false, error:message, machine:form.machine, path});
        setStatus(message);
        renderGlobalDirectorySettings();
    }
}
function renderWorkspaceSettingsBody(){
    const snapshot = workspaceSettingsState.snapshot;
    const busy = Boolean(workspaceSettingsState.loading || workspaceSettingsState.saving);
    const active = snapshot?.active_workspace_id || 'ws_local_01';
    const workspaces = snapshot?.workspaces || [];
    const activeLabel = workspaces.find(item => item.workspace_id === active)?.display_name || '独立本机工作区';
    const compatibility = snapshot?.compatibility || {status:'pending', detail:'兼容检查尚未读取'};
    const modeButtons = workspaces.map(item => `<button type="button" class="hw-btn ${workspaceSettingsState.target === item.workspace_id ? 'hw-btn-gold' : ''}" data-workspace-mode="${escapeAttr(item.workspace_id)}" aria-pressed="${workspaceSettingsState.target === item.workspace_id}" ${busy ? 'disabled' : ''}>${escapeHtml(item.display_name)}</button>`).join('');
    const mappings = (snapshot?.mappings || []).map(item => `<article class="allowed-root-row workspace-mapping-row" data-workspace-mapping="${escapeAttr(item.mapping_id)}"><div class="allowed-root-info"><strong>${escapeHtml(item.logical_source_id)}</strong><code title="共享路径">${escapeHtml(item.access_path)}</code><small>执行机器：${escapeHtml(item.execution_machine_id)} · 核验：${escapeHtml(item.verification?.status || 'pending')} · 核验宿主：${escapeHtml(item.verification?.verified_by || '服务端执行上下文')}</small></div></article>`).join('');
    return `<section class="asset-pref-section workspace-settings" data-workspace-settings aria-busy="${busy}">
        <div class="asset-pref-title"><i data-lucide="hard-drive"></i><span>工作区</span><small>${busy ? '正在处理…' : escapeHtml(activeLabel)}</small></div>
        <p class="board-settings-hint">默认使用独立本机工作区；安装本地后台不会自动加入 NAS。切换需确认，同一客户端只向当前工作区写入。</p>
        <div class="workspace-mode-switch" role="group" aria-label="工作区模式">${modeButtons}<button type="button" class="hw-btn hw-btn-gold" data-workspace-switch="${escapeAttr(workspaceSettingsState.target)}" ${busy || workspaceSettingsState.target === active ? 'disabled' : ''}>${busy ? '处理中…' : '切换工作区'}</button></div>
        <p class="board-settings-hint workspace-compat">兼容检查：${escapeHtml(compatibility.status === 'compatible' ? '已通过' : (compatibility.detail || '待接入'))}</p>
        <div class="allowed-root-add"><label class="storage-dir-row"><span>执行机器</span><div class="directory-input-row"><input id="workspaceMapMachine" value="${escapeAttr(workspaceSettingsState.machine)}" autocomplete="off" ${busy ? 'disabled' : ''}></div></label><label class="storage-dir-row"><span>共享路径（服务端可解析）</span><div class="directory-input-row"><input id="workspaceMapPath" value="${escapeAttr(workspaceSettingsState.path)}" autocomplete="off" placeholder="输入执行端可访问的资源文件夹" ${busy ? 'disabled' : ''}></div></label><div class="workspace-mapping-actions"><button type="button" class="hw-btn" data-workspace-map-verify ${busy ? 'disabled' : ''}>核验权限</button><button type="button" class="hw-btn hw-btn-gold" data-workspace-map-add ${busy ? 'disabled' : ''}>登记映射</button></div></div>
        ${workspaceSettingsState.error ? `<p class="allowed-root-error" role="alert">${escapeHtml(workspaceSettingsState.error)}</p>` : ''}
        <div class="allowed-root-list">${mappings || '<p class="directory-status">暂无共享映射</p>'}</div>
    </section>`;
}
let assetProxySettingsState = {projectId:'', enabled:false, autoQueue:false, maxWidth:960, maxHeight:540, loading:false, saving:false, message:''};
// QIN-599 AC3：「看板显示」设置区块整块移除；偏好弹窗保留 AI 分类、目录设置入口、缩略图与视频。
function renderAssetAiSettingsBody(){
    const providers = localCaptionProviders();
    if(!assetAiSettingsState.loading) normalizeLocalCaptionSettings();
    const provider = providers.find(item => item.id === localCaptionProvider) || providers[0];
    const models = (provider?.chat_models || []).filter(Boolean);
    const stateLabel = assetAiSettingsState.loading ? '正在读取…' : assetAiSettingsState.saving ? '正在保存…' : assetAiSettingsState.message || (providers.length ? '已连接可用模型' : '请先配置 API 平台');
    return `<section class="asset-pref-section board-ai-settings" data-board-ai-settings>
        <div class="asset-pref-title"><i data-lucide="sparkles"></i><span>AI 分类设置</span><small>${escapeHtml(stateLabel)}</small></div>
        <div class="board-ai-settings-grid">
            <label class="storage-dir-row board-ai-compact"><span>平台</span><select id="prefCaptionProvider" ${providers.length ? '' : 'disabled'}>${providers.length ? providers.map(provider => `<option value="${escapeAttr(provider.id)}" ${provider.id === localCaptionProvider ? 'selected' : ''}>${escapeHtml(provider.name || provider.id)}</option>`).join('') : '<option value="">暂无视觉平台</option>'}</select></label>
            <label class="storage-dir-row board-ai-compact"><span>模型</span><select id="prefCaptionModel" ${models.length ? '' : 'disabled'}>${models.length ? models.map(model => `<option value="${escapeAttr(model)}" ${model === localCaptionModel ? 'selected' : ''}>${escapeHtml(model)}</option>`).join('') : '<option value="">暂无可用模型</option>'}</select></label>
            <label class="storage-dir-row board-ai-compact"><span>反推提示词</span><input id="prefCaptionPrompt" value="${escapeAttr(localCaptionPrompt || '描述图片')}" placeholder="描述图片"></label>
            <button class="board-ai-auto-control" type="button" role="switch" aria-checked="${localAutoClassify}" data-board-ai-auto ${assetAiSettingsState.saving ? 'disabled' : ''}><span class="registry-feature-switch ${localAutoClassify ? 'is-on' : ''}"><i></i></span><span><strong>一键 AI 智能分类</strong><small>打开立即执行，关闭立即停止</small></span></button>
            <label class="storage-dir-row board-ai-extra"><span>智能分类追加要求</span><input id="prefClassifyPrompt" value="${escapeAttr(localClassifyPrompt || '')}" placeholder="例如：镜头焦段、商业用途、产品材质"></label>
            <details class="board-ai-rule"><summary><span>智能分类规则</span><small>展开编辑 JSON 输出规则</small></summary><textarea id="prefClassificationRule" class="asset-pref-rule-textarea" rows="5" placeholder="智能分类规则">${escapeHtml(assetAiSettingsState.rule || assetAiSettingsState.defaultRule || '')}</textarea></details>
        </div>
        <div class="storage-settings-actions inline board-ai-actions">
            <button class="hw-btn" type="button" data-board-ai-reset ${assetAiSettingsState.loading || assetAiSettingsState.saving ? 'disabled' : ''}><i data-lucide="rotate-ccw"></i><span>恢复内置规则</span></button>
            <button class="hw-btn hw-btn-gold" type="button" data-board-ai-save ${assetAiSettingsState.loading || assetAiSettingsState.saving ? 'disabled' : ''}><i data-lucide="save"></i><span>保存 AI 分类设置</span></button>
        </div>
    </section>`;
}
function thumbnailCacheDirectoryIsInsideLibrary(directory=thumbnailGeneratorState.directory){
    const libraries = storageSettingsState.localLibraries?.length
        ? storageSettingsState.localLibraries
        : localLibraries;
    return (Array.isArray(libraries) ? libraries : []).some(library =>
        Boolean(library?.path) && assetPathWithinAllowedRoot(directory, library.path)
    );
}
function updateThumbnailCacheExclusionHint(){
    const hint = document.querySelector('#storageSettingsOverlay [data-thumbnail-cache-exclusion-hint]');
    if(!hint) return;
    const visible = thumbnailGeneratorState.mode === 'custom'
        && thumbnailCacheDirectoryIsInsideLibrary();
    hint.textContent = visible ? THUMBNAIL_CACHE_EXCLUSION_HINT : '';
    hint.hidden = !visible;
}
function thumbnailCacheTargetText(){
    const folder = thumbnailGeneratorState.cacheFolder || 'god-cache';
    return thumbnailGeneratorState.mode === 'custom'
        ? `真实写入所选目录下的 ${folder} 子目录；切换目录时会询问原缓存迁移还是删除，所选目录自动授权为资源文件夹`
        : `写入应用受管数据目录内的 ${folder}，不写入素材源目录`;
}
function renderAssetThumbnailCacheDirectoryBody(){
    const known = thumbnailKnownDirectories();
    const mode = thumbnailGeneratorState.mode === 'custom' ? 'custom' : 'source';
    const exclusionHint = mode === 'custom' && thumbnailCacheDirectoryIsInsideLibrary()
        ? THUMBNAIL_CACHE_EXCLUSION_HINT
        : '';
    const cacheRootPicker = canBrowseServerDirectories()
        ? '<button type="button" class="hw-btn hw-btn-icon directory-picker-btn" data-directory-picker="' + THUMBNAIL_DIRECTORY_FIELD_KEY + '" title="浏览服务器目录" aria-label="浏览服务器缓存根目录"><i data-lucide="folder-open"></i></button>'
        : '';
    return `<div class="board-directory-cache">
        <div class="asset-pref-title"><i data-lucide="folder-clock"></i><span>缓存目录</span></div>
        <p class="board-settings-hint">缩略图缓存${escapeHtml(thumbnailCacheTargetText())}；同一源文件未变更时直接复用已有缓存。</p>
        <div class="thumbnail-cache-options board-thumbnail-options">
            <button type="button" class="thumbnail-cache-option ${mode === 'source' ? 'is-active' : ''}" data-board-thumbnail-mode="source"><i data-lucide="folders"></i><span><strong>跟随素材源</strong><small>${escapeHtml(thumbnailGeneratorState.cacheFolder)} · 受管数据目录</small></span></button>
            <button type="button" class="thumbnail-cache-option ${mode === 'custom' ? 'is-active' : ''}" data-board-thumbnail-mode="custom"><i data-lucide="folder-cog"></i><span><strong>指定缓存目录</strong><small>${escapeHtml(thumbnailGeneratorState.cacheFolder)} · 写入所选目录</small></span></button>
        </div>
        ${mode === 'custom' ? `<div class="thumbnail-cache-directory">
            <label class="storage-dir-row"><span>选择资源文件夹</span><select id="thumbnailDirectoryPreset"><option value="">手工输入路径</option>${known.map(item => `<option value="${escapeAttr(item.path)}" ${pathCompareKey(item.path) === pathCompareKey(thumbnailGeneratorState.directory) ? 'selected' : ''}>${escapeHtml(item.name)} · ${escapeHtml(item.path)}</option>`).join('')}</select></label>
            <label class="storage-dir-row"><span>缓存根目录</span><div class="directory-input-row"><input id="thumbnailDirectory" value="${escapeAttr(thumbnailGeneratorState.directory)}" placeholder="例如 D:\\GodsCache" autocomplete="off">${cacheRootPicker}</div></label>
        </div>` : ''}
        <p class="board-settings-hint" data-thumbnail-cache-exclusion-hint role="status" aria-live="polite" ${exclusionHint ? '' : 'hidden'}>${escapeHtml(exclusionHint)}</p>
    </div>`;
}
// 原 renderAssetDirectorySettingsBody()（「上传与生成目录」）已下线清理（QIN-649）。
function renderThumbnailFormatOptions(){
    const format = thumbnailGeneratorState.format === 'webp' ? 'webp' : 'jxl';
    const disabled = thumbnailGeneratorState.loading || thumbnailGeneratorState.savingFormat;
    return `<fieldset class="thumbnail-format-options" aria-label="缩略图格式">
        <legend>缩略图格式</legend>
        <label class="thumbnail-format-option ${format === 'jxl' ? 'is-active' : ''}">
            <input
                type="radio"
                name="thumbnailFormat"
                value="jxl"
                data-thumbnail-format ${format === 'jxl' ? 'checked' : ''} ${disabled ? 'disabled' : ''}>
            <span>
                <strong>JXL</strong>
                <small>默认推荐 · 体积更小</small>
            </span>
        </label>
        <label class="thumbnail-format-option ${format === 'webp' ? 'is-active' : ''}">
            <input type="radio" name="thumbnailFormat" value="webp" data-thumbnail-format ${format === 'webp' ? 'checked' : ''} ${disabled ? 'disabled' : ''}>
            <span>
                <strong>WebP</strong>
                <small>兼容过渡 · 适用于旧版 WebView</small>
            </span>
        </label>
    </fieldset>`;
}
function renderAssetPreviewSettingsBody(){
    const features = Array.isArray(registryStatus?.overview?.features) ? registryStatus.overview.features : [];
    const thumbnailFeature = features.find(feature => feature.id === 'thumbnail') || {id:'thumbnail',name:'自动缩略图',enabled:true};
    const state = assetPreviewAllState;
    const stats = previewCacheStats();
    const job = state.job || null;
    // 暂停中仍是「运行中」：轮询要继续，删除缓存等互斥操作也要保持禁用。
    const running = Boolean(job && ['queued','running','paused'].includes(job.status));
    const paused = job?.status === 'paused';
    const controlling = Boolean(state.controlling);
    const result = job?.result || null;
    const progress = job?.progress || {};
    const total = Number(progress.total || 0);
    const processed = Math.min(total, Number(progress.processed || 0));
    const percent = total ? Math.round(processed / total * 100) : 0;
    const busy = running || state.submitting || state.deleting;
    // 按钮态看真实缓存覆盖，而不是上一次任务状态：新增素材后应回到【全部缓存】。
    const complete = !running && stats.totalAssets > 0 && stats.cachedAssets >= stats.totalAssets;
    const primaryLabel = complete ? '重建缓存' : '全部缓存';
    const progressLabel = paused
        ? (total ? `已暂停 ${processed}/${total}` : '已暂停')
        : (total ? `正在缓存 ${processed}/${total}…` : '正在缓存…');
    const terminalLabel = job?.status === 'succeeded'
        ? (result?.mode === 'rebuild' ? '已重建' : '已完成')
        : job?.status === 'cancelled' ? '已取消'
        : job?.status === 'failed' ? '失败'
        : job?.status === 'paused' ? '已暂停'
        : '排队中';
    const confirmText = state.confirmMode === 'rebuild'
        ? `将先清空全部预览缓存，再重新生成 ${stats.totalAssets.toLocaleString('zh-CN')} 项素材的预览，会占用一定 CPU 与磁盘；确认重建？`
        : `将遍历全库 ${stats.totalAssets.toLocaleString('zh-CN')} 项素材生成预览缓存，已缓存项自动跳过；确认开始？`;
    return `<section class="asset-pref-section board-preview-settings" data-preview-cache-settings aria-busy="${busy}">
        <div class="asset-pref-title board-preview-title"><span><i data-lucide="images"></i>预览缓存</span><div class="board-preview-feature-switches">${renderRegistryFeatureControl(thumbnailFeature)}</div></div>
        <p class="board-settings-hint">为全库素材后台生成预览图，适合离线前预热。已缓存的素材会自动跳过，不重复解码。</p>
        <div class="thumbnail-cache-summary board-thumbnail-summary"><span><i data-lucide="database"></i>全库 ${stats.totalAssets.toLocaleString('zh-CN')} 项 · 已缓存 ${stats.cachedAssets.toLocaleString('zh-CN')} 项 · 占用 ${escapeHtml(formatFileSize(stats.bytes))}</span><small>默认按 256px 宽度预热，与网格预览一致</small></div>
        ${renderThumbnailFormatOptions()}
        ${!stats.available ? `<div class="thumbnail-cache-error board-thumbnail-error" role="alert">缓存目录当前不可用，请先在下方重新选择缓存目录。</div>` : ''}
        ${job ? `<div class="thumbnail-cache-summary board-thumbnail-summary" role="status" aria-live="polite"><span>${escapeHtml(running ? progressLabel : terminalLabel)}${result ? ` · 新增 ${Number(result.generated || 0)} 项 · 跳过 ${Number(result.skipped || 0)} 项 · 失败 ${Number(result.failed || 0)} 项 · 耗时 ${Number(result.elapsed_seconds || 0)} 秒` : ''}</span>${total && running ? `<progress class="board-preview-all-progress" max="${Math.max(1, total)}" value="${processed}" aria-label="预览缓存进度" title="${percent}%"></progress>` : ''}</div>` : ''}
        ${state.confirming ? `<p class="board-settings-hint">${escapeHtml(confirmText)}</p>` : ''}
        ${state.error ? `<div class="thumbnail-cache-error board-thumbnail-error" role="alert">${escapeHtml(state.error)}</div>` : ''}
        ${state.notice ? `<div class="thumbnail-cache-summary board-thumbnail-summary" role="status"><span><i data-lucide="check-circle"></i>${escapeHtml(state.notice)}</span></div>` : ''}
        ${state.deleteOpen ? renderPreviewCacheDeletePanel() : `<div class="board-thumbnail-actions">
            <button class="hw-btn hw-btn-danger" type="button" data-preview-cache-delete ${busy ? 'disabled' : ''}><i data-lucide="trash-2"></i><span>${state.deleting ? '正在删除…' : '删除缓存'}</span></button>
            ${running
                ? `<button class="hw-btn" type="button" data-preview-all-pause ${controlling ? 'disabled' : ''}><i data-lucide="${paused ? 'play' : 'pause'}"></i><span>${paused ? '继续' : '暂停'}</span></button>
                   <button class="hw-btn hw-btn-danger" type="button" data-preview-all-stop ${controlling ? 'disabled' : ''}><i data-lucide="ban"></i><span>取消</span></button>`
                : state.confirming
                ? `<button class="hw-btn" type="button" data-preview-all-cancel ${state.submitting ? 'disabled' : ''}>取消</button>
                   <button class="hw-btn hw-btn-gold" type="button" data-preview-all-confirm ${state.submitting ? 'disabled' : ''}><i data-lucide="${state.submitting ? 'loader-2' : 'check'}"></i><span>${state.submitting ? '正在提交…' : '确认'}</span></button>`
                : `<button class="hw-btn hw-btn-gold" type="button" data-preview-all-start data-preview-all-mode="${complete ? 'rebuild' : 'fill'}" ${busy || !stats.totalAssets ? 'disabled' : ''}><i data-lucide="${complete ? 'refresh-cw' : 'download-cloud'}"></i><span>${primaryLabel}</span></button>`}
        </div>`}
        <div class="board-settings-divider" aria-hidden="true"></div>
        ${renderJxlLosslessTranscodeSettingsBody()}
    </section>`;
}
function renderJxlLosslessTranscodeSettingsBody(){
    const state = assetJxlTranscodeState;
    const scan = state.scan || {};
    const tools = scan.tool_status || {};
    const encoder = tools.cjxl || {};
    const decoder = tools.djxl || {};
    const job = state.job || null;
    const running = Boolean(job && ['queued','running'].includes(job.status));
    const ready = Boolean(encoder.available && decoder.available);
    const toolLabel = tools.status === 'ready' ? '已就绪' : tools.status === 'error' ? '探测失败' : '未安装';
    const progress = job?.progress || {};
    const progressTotal = Math.max(1, Number(progress.total || 0));
    const progressCompleted = Math.min(progressTotal, Number(progress.completed || 0));
    const progressText = `${progressCompleted} / ${Number(progress.total || 0)}`;
    const statusText = job ? ({queued:'排队中',running:'处理中',succeeded:'已完成',failed:'失败',cancelled:'已取消',interrupted:'服务重启后中断'}[job.status] || job.status) : '';
    const guidance = tools.guidance || '请安装官方 libjxl 工具，并确保 cjxl 与 djxl 位于 PATH 或应用 bin/ 目录。';
    return `<section class="asset-jxl-transcode" data-jxl-transcode-settings aria-busy="${state.loading || state.starting || state.cancelling}">
        <div class="asset-pref-title"><i data-lucide="file-output"></i><span>旧 JPEG 无损转码</span><small>${escapeHtml(state.loading ? '正在扫描…' : toolLabel)}</small></div>
        <p class="board-settings-hint">使用官方 cjxl 生成 JXL，并以 djxl 还原校验 SHA256。原 JPEG 始终保留。</p>
        <div class="thumbnail-cache-summary board-thumbnail-summary">
            <span>工具：cjxl ${escapeHtml(encoder.version || encoder.code || '未探测')} · djxl ${escapeHtml(decoder.version || decoder.code || '未探测')}</span>
            <small>登记 JPEG ${Number(scan.jpeg_assets || 0)} 项 · 待转码 ${Number(scan.pending_count || 0)} 项 · 预计节省 ${escapeHtml(formatFileSize(scan.estimated_savings_min_bytes || 0))}–${escapeHtml(formatFileSize(scan.estimated_savings_max_bytes || 0))}</small>
        </div>
        ${!ready ? `<p class="board-settings-hint">${escapeHtml(guidance)} ${tools.download_url ? `<a href="${escapeAttr(tools.download_url)}" target="_blank" rel="noopener noreferrer">官方发布页</a>` : ''}</p>` : ''}
        ${scan.unavailable_count ? `<p class="board-settings-hint">${Number(scan.unavailable_count)} 项源文件当前不可读取，已跳过。</p>` : ''}
        ${state.error ? `<div class="thumbnail-cache-error board-thumbnail-error" role="alert">${escapeHtml(state.error)}</div>` : ''}
        ${job ? `<div class="thumbnail-cache-summary board-thumbnail-summary" role="status" aria-live="polite"><span>${escapeHtml(statusText)} · ${escapeHtml(progressText)} · 已节省 ${escapeHtml(formatFileSize(progress.saved_bytes || 0))}</span><progress class="board-jxl-transcode-progress" max="${progressTotal}" value="${progressCompleted}" aria-label="JPEG XL 无损转码进度"></progress><small>${progress.failed ? `失败 ${Number(progress.failed)} 项` : ''}${job.error_code ? ` · ${escapeHtml(job.error_code)}` : ''}${job.cancel_requested ? ' · 当前文件结束后停止' : ''}</small></div>` : ''}
        <div class="board-thumbnail-actions">
            <button class="hw-btn" type="button" data-jxl-transcode-scan ${state.loading || state.starting || state.cancelling ? 'disabled' : ''}><i data-lucide="${state.loading ? 'loader-2' : 'refresh-cw'}"></i><span>重新扫描</span></button>
            ${running
                ? `<button class="hw-btn hw-btn-danger" type="button" data-jxl-transcode-cancel ${state.cancelling || job.cancel_requested ? 'disabled' : ''}><i data-lucide="ban"></i><span>${state.cancelling ? '正在取消…' : '完成当前项后取消'}</span></button>`
                : `<button class="hw-btn hw-btn-gold" type="button" data-jxl-transcode-start ${!ready || state.loading || state.starting || !Number(scan.pending_count || 0) ? 'disabled' : ''}><i data-lucide="${state.starting ? 'loader-2' : 'file-output'}"></i><span>${state.starting ? '正在提交…' : `开始无损转码 ${Number(scan.pending_count || 0)} 项`}</span></button>`}
        </div>
    </section>`;
}
async function loadJxlTranscodeScan(){
    assetJxlTranscodeState = {...assetJxlTranscodeState, loading:true, error:''};
    if(storageSettingsState.open) renderStorageSettingsModal();
    try {
        const scan = await apiJsonResponse(assetManagerHttp.request('/api/assets/transcode-jpeg-lossless/scan', {method:'GET', credentials:'same-origin'}));
        const activeJob = scan.active_job && ['queued','running'].includes(scan.active_job.status) ? scan.active_job : null;
        const shouldStartPoll = Boolean(activeJob && assetJxlTranscodeState.pollingJobId !== activeJob.job_id);
        assetJxlTranscodeState = {
            ...assetJxlTranscodeState,
            scan,
            job:activeJob || assetJxlTranscodeState.job,
            pollingJobId:activeJob?.job_id || assetJxlTranscodeState.pollingJobId,
            loading:false,
        };
        if(shouldStartPoll && activeJob){
            void pollJxlTranscodeJob(activeJob.job_id, Number(scan.active_job.poll_hint?.interval_ms || 500));
        }
    } catch(error){
        assetJxlTranscodeState = {...assetJxlTranscodeState, loading:false, error:error.message || 'JPEG 扫描失败'};
    }
    if(storageSettingsState.open) renderStorageSettingsModal();
}
async function pollJxlTranscodeJob(jobId, intervalMs=500){
    if(!jobId) return;
    try {
        const job = await apiJsonResponse(assetManagerHttp.request(`/api/assets/transcode-jpeg-lossless/jobs/${encodeURIComponent(jobId)}`, {method:'GET', credentials:'same-origin'}));
        if(assetJxlTranscodeState.job?.job_id !== jobId) return;
        assetJxlTranscodeState = {...assetJxlTranscodeState, job, pollingJobId:['queued','running'].includes(job.status) ? jobId : ''};
        if(storageSettingsState.open) renderStorageSettingsModal();
        if(['queued','running'].includes(job.status)){
            window.setTimeout(() => void pollJxlTranscodeJob(jobId, intervalMs), intervalMs);
        } else {
            await loadJxlTranscodeScan();
        }
    } catch(error){
        if(assetJxlTranscodeState.job?.job_id !== jobId) return;
        if([401,403,404].includes(error.status)){
            assetJxlTranscodeState = {...assetJxlTranscodeState, pollingJobId:'', error:error.message || '转码任务不可读取'};
            if(storageSettingsState.open) renderStorageSettingsModal();
            return;
        }
        assetJxlTranscodeState = {...assetJxlTranscodeState, error:error.message || '读取转码进度失败'};
        if(storageSettingsState.open) renderStorageSettingsModal();
        window.setTimeout(() => void pollJxlTranscodeJob(jobId, Math.max(intervalMs, 1500)), Math.max(intervalMs, 1500));
    }
}
async function startJxlLosslessTranscode(){
    if(assetJxlTranscodeState.starting || Number(assetJxlTranscodeState.scan?.pending_count || 0) < 1) return;
    assetJxlTranscodeState = {...assetJxlTranscodeState, starting:true, error:''};
    renderStorageSettingsModal();
    try {
        const accepted = await apiJsonResponse(assetManagerHttp.request('/api/assets/transcode-jpeg-lossless', {
            method:'POST', credentials:'same-origin', headers:{'Content-Type':'application/json'}, body:JSON.stringify({})
        }));
        assetJxlTranscodeState = {...assetJxlTranscodeState, starting:false, job:accepted.job || null, pollingJobId:accepted.job_id || ''};
        renderStorageSettingsModal();
        if(accepted.job_id) void pollJxlTranscodeJob(accepted.job_id, Number(accepted.poll_hint?.interval_ms || 500));
    } catch(error){
        assetJxlTranscodeState = {...assetJxlTranscodeState, starting:false, error:`${error.code ? `${error.code}：` : ''}${error.message || '提交转码失败'}`};
        renderStorageSettingsModal();
    }
}
async function cancelJxlLosslessTranscode(){
    const jobId = assetJxlTranscodeState.job?.job_id;
    if(!jobId || assetJxlTranscodeState.cancelling) return;
    assetJxlTranscodeState = {...assetJxlTranscodeState, cancelling:true, error:''};
    renderStorageSettingsModal();
    try {
        const job = await apiJsonResponse(assetManagerHttp.request(`/api/assets/transcode-jpeg-lossless/jobs/${encodeURIComponent(jobId)}/cancel`, {
            method:'POST', credentials:'same-origin', headers:{'Content-Type':'application/json'}, body:JSON.stringify({})
        }));
        assetJxlTranscodeState = {...assetJxlTranscodeState, cancelling:false, job};
        renderStorageSettingsModal();
        if(!['queued','running'].includes(job.status)) await loadJxlTranscodeScan();
    } catch(error){
        assetJxlTranscodeState = {...assetJxlTranscodeState, cancelling:false, error:error.message || '取消转码失败'};
        renderStorageSettingsModal();
    }
}
async function refreshThumbnailCacheStats(){
    try {
        const data = await apiJsonResponse(assetManagerApi.getThumbnailSettings());
        applyThumbnailSettingsSnapshot(data || {});
        if(storageSettingsState.open) renderStorageSettingsModal();
        return data;
    } catch(error){
        return null;
    }
}
function renderAssetVideoSettingsBody(){
    const features = Array.isArray(registryStatus?.overview?.features) ? registryStatus.overview.features : [];
    const storyboardFeature = features.find(feature => feature.id === 'video_storyboard') || {id:'video_storyboard',name:'视频九宫格缩略图',enabled:false};
    const ids = thumbnailGeneratorAssetIds();
    const busy = thumbnailGeneratorState.loading || thumbnailGeneratorState.generating || thumbnailGeneratorState.deleting || thumbnailGeneratorState.deletingStoryboards || thumbnailGeneratorState.generatingStoryboards || thumbnailGeneratorState.submittingBackground;
    return `<section class="asset-pref-section board-video-settings" data-board-video-settings>
        <div class="asset-pref-title board-preview-title"><span><i data-lucide="film"></i>视频</span><div class="board-preview-feature-switches">${renderRegistryFeatureControl(storyboardFeature)}</div></div>
        <p class="board-settings-hint">视频九宫格按总时长九等分抽取 9 帧（首帧、尾帧与 7 个中间帧），由服务端合成为一张 3×3 单图，宽边不超过 2160px。</p>
        <label class="thumbnail-video-transcode board-thumbnail-transcode"><input id="thumbnailTranscodeVideos" type="checkbox" ${thumbnailGeneratorState.transcodeVideos ? 'checked' : ''}><span><strong>视频同时转码</strong><small>生成 H.264 MP4，与缩略图共用当前缓存目录。</small></span></label>
        <div class="board-settings-divider" aria-hidden="true"></div>
        ${renderAssetProxySettingsBody()}
        <div class="board-thumbnail-actions"><button class="hw-btn" type="button" data-board-storyboard-generate ${busy || !ids.length ? 'disabled' : ''}><i data-lucide="${thumbnailGeneratorState.generatingStoryboards ? 'loader-2' : 'grid-3x3'}"></i><span>${thumbnailGeneratorState.generatingStoryboards ? '正在生成…' : `生成九宫格 ${ids.length}`}</span></button><button class="hw-btn hw-btn-danger" type="button" data-board-storyboard-delete ${busy || !ids.length ? 'disabled' : ''}><i data-lucide="grid-3x3"></i><span>${thumbnailGeneratorState.deletingStoryboards ? '正在删除…' : `删除九宫格 ${ids.length}`}</span></button></div>
    </section>`;
}
function assetPathWithinAllowedRoot(path, rootPath){
    const candidate = pathCompareKey(path);
    const root = pathCompareKey(rootPath);
    return Boolean(candidate && root && (candidate === root || candidate.startsWith(`${root}/`)));
}
function confirmRestoreRemovedAllowedRoot(root){
    return new Promise(resolve => {
        const overlay = document.createElement('div');
        overlay.className = 'storage-settings-overlay root-restore-confirm-overlay';
        overlay.innerHTML = `<section class="board-source-modal" data-floating-content role="dialog" aria-modal="true" aria-labelledby="rootRestoreConfirmTitle">
            <header class="storage-settings-head"><div><strong id="rootRestoreConfirmTitle">旧根已移除，是否恢复？</strong><span>恢复后会继续创建资源库并重新扫描该目录。</span></div><button class="hw-btn" type="button" data-root-restore-cancel aria-label="关闭" title="关闭"><i data-lucide="x"></i></button></header>
            <p class="board-create-summary">${escapeHtml(root.path || '')}</p>
            <div class="board-create-actions"><button class="hw-btn" type="button" data-root-restore-cancel>取消</button><button class="hw-btn hw-btn-gold" type="button" data-root-restore-confirm>恢复并继续</button></div>
        </section>`;
        let finished = false;
        const close = confirmed => {
            if(finished) return;
            finished = true;
            overlay.remove();
            resolve(Boolean(confirmed));
        };
        document.body.appendChild(overlay);
        markAssetFloatingSurface(overlay, {surface:'asset-root-restore-confirm', content:'[role="dialog"]', close:() => close(false)});
        overlay.addEventListener('click', event => {
            if(event.target.closest('[data-root-restore-cancel]')){ close(false); return; }
            if(event.target.closest('[data-root-restore-confirm]')) close(true);
        });
        refreshIcons();
    });
}
// QIN-596 AC5：系统目录拒绝清单（前端体验层；服务端为最终门禁，与
// gw/core/storage.py 的 describe_resource_folder_rejection 双端常量逐条一致）。
const RESOURCE_FOLDER_BLOCKED_WIN_SEGMENTS = ['windows', 'program files', 'program files (x86)', 'programdata'];
const RESOURCE_FOLDER_BLOCKED_POSIX_SEGMENTS = ['system', 'library', 'applications', 'usr', 'bin', 'sbin', 'etc', 'var', 'private'];
const RESOURCE_FOLDER_USERS_SEGMENT = 'users';
const RESOURCE_FOLDER_DRIVE_ROOT_RE = /^[a-zA-Z]:[\\/]{0,1}$/;
const RESOURCE_FOLDER_UNC_MACHINE_ROOT_RE = /^\\\\[^\\/]+\\{0,1}$/;
const RESOURCE_FOLDER_POSIX_ROOT_RE = /^[\\/]+$/;

function describeResourceFolderPathRejection(path){
    // 词法判定作用于原始输入（先于后端 normalize 语义）：命中返回拒绝原因，
    // 未命中返回空串。双端同清单，测试逐条对齐。
    const value = String(path == null ? '' : path).trim();
    if(!value) return '路径为空';
    const unified = value.replace(/\//g, '\\');
    if(RESOURCE_FOLDER_DRIVE_ROOT_RE.test(value) || RESOURCE_FOLDER_DRIVE_ROOT_RE.test(unified)){
        return '盘符根目录不可添加为资源文件夹';
    }
    if(RESOURCE_FOLDER_UNC_MACHINE_ROOT_RE.test(unified)){
        return 'UNC 机器根不可添加为资源文件夹，请指定具体共享目录';
    }
    if(RESOURCE_FOLDER_POSIX_ROOT_RE.test(value)){
        return '文件系统根目录不可添加为资源文件夹';
    }
    let segments = null;
    if(/^\\\\/.test(unified)){
        segments = unified.slice(2).split('\\').filter(Boolean);
    } else if(/^[a-zA-Z]:/.test(unified)){
        segments = unified.slice(2).split('\\').filter(Boolean);
    } else if(/^\//.test(value)){
        segments = value.split('/').filter(Boolean);
    }
    if(segments === null) return '';
    // QIN-596（审核 FIX_REQUIRED）：清单匹配前消解 `.`/`..` 点段；越界点段
    // 最终位置歧义，按拒绝处理（与 gw/core/storage.py 同规则）。
    const resolved = [];
    for(const seg of segments){
      if(seg === '.') continue;
      if(seg === '..'){
        if(!resolved.length) return `路径含越界点段，最终位置歧义，不可添加为资源文件夹：${value}`;
        resolved.pop();
        continue;
      }
      resolved.push(seg);
    }
    const lowered = resolved.map(seg => seg.toLowerCase());
    if(lowered.length && RESOURCE_FOLDER_BLOCKED_WIN_SEGMENTS.includes(lowered[0])){
        return `系统目录不可添加为资源文件夹：${value}`;
    }
    if(lowered.length && RESOURCE_FOLDER_BLOCKED_POSIX_SEGMENTS.includes(lowered[0])){
        return `系统目录不可添加为资源文件夹：${value}`;
    }
    if(lowered.length && lowered[0] === RESOURCE_FOLDER_USERS_SEGMENT && lowered.length <= 2){
        return `用户配置目录根不可添加为资源文件夹：${value}`;
    }
    return '';
}

async function ensureCreateLibraryPathAllowedRoot(path){
    // QIN-596 AC5：清单命中即本地拒绝（不发任何请求），对管理员与非管理员
    // 统一生效；服务端三入口为最终门禁。
    const rejection = describeResourceFolderPathRejection(path);
    if(rejection){ throw new Error(rejection); }
    await loadRegistryPrincipal();
    if(!canManageAllowedRoots()) return true;
    let restorationApprovedRootId = '';
    for(let attempt = 0; attempt < 2; attempt++){
        const snapshot = await apiJsonResponse(assetManagerHttp.request('/api/storage-settings/roots', {method:'GET', credentials:'same-origin'}));
        applyAllowedRootSettingsData(snapshot);
        if((snapshot.roots || []).some(root => root.active !== false && assetPathWithinAllowedRoot(path, root.path))) return true;
        const removedRoots = (snapshot.roots || [])
            .filter(root => root.active === false && assetPathWithinAllowedRoot(path, root.path))
            .sort((left, right) => pathCompareKey(right.path).length - pathCompareKey(left.path).length);
        const removedRoot = restorationApprovedRootId
            ? removedRoots.find(root => String(root.id) === restorationApprovedRootId)
            : removedRoots[0];
        if(restorationApprovedRootId && !removedRoot) throw new Error('资源文件夹授权配置已变化，请重新创建资源库');
        if(removedRoot){
            if(!restorationApprovedRootId){
                if(!await confirmRestoreRemovedAllowedRoot(removedRoot)) return false;
                restorationApprovedRootId = String(removedRoot.id);
            }
            try {
                const restored = await apiJsonResponse(assetManagerHttp.request(`/api/storage-settings/roots/${encodeURIComponent(removedRoot.id)}/restore`, {
                    method:'POST', credentials:'same-origin',
                    headers:{'Content-Type':'application/json'},
                    body:JSON.stringify({expected_version:Number(snapshot.revision || 1)}),
                }));
                applyAllowedRootSettingsData(restored);
                return true;
            } catch(error){
                if(attempt === 0 && ['ALLOWED_ROOT_ALREADY_ACTIVE','VERSION_CONFLICT'].includes(error.code)) continue;
                throw error;
            }
        }
        try {
            const updated = await apiJsonResponse(assetManagerHttp.request('/api/storage-settings/roots', {
                method:'POST', credentials:'same-origin',
                headers:{'Content-Type':'application/json'},
                body:JSON.stringify({path, expected_version:Number(snapshot.revision || 1)}),
            }));
            applyAllowedRootSettingsData(updated);
            return true;
        } catch(error){
            if(attempt === 0 && ['ALLOWED_ROOT_ALREADY_REGISTERED','VERSION_CONFLICT'].includes(error.code)) continue;
            throw error;
        }
    }
    throw new Error('资源文件夹授权配置已变化，请重新创建资源库');
}
// 用户显式指定缓存目录这一步本身就是授权许可：与「创建资源库」同口径自动授权资源文件夹，
// 不让用户先撞一次 403 再点按钮。登记失败不抛错，交给生成链路的提示块说明原因。
async function ensureThumbnailCacheRootAllowed(directory, options={}){
    const path = String(directory || '').trim();
    if(!path) return {ok:false, registered:false, reason:'empty'};
    if(!canManageAllowedRoots()) return {ok:false, registered:false, reason:'forbidden'};
    const announce = options.announce !== false;
    for(let attempt = 0; attempt < 2; attempt++){
        let snapshot;
        try {
            snapshot = await apiJsonResponse(assetManagerHttp.request('/api/storage-settings/roots', {method:'GET', credentials:'same-origin'}));
        } catch(error){
            return {ok:false, registered:false, error, reason:'unavailable'};
        }
        applyAllowedRootSettingsData(snapshot);
        if((snapshot.roots || []).some(root => root.active !== false && assetPathWithinAllowedRoot(path, root.path))){
            return {ok:true, registered:false};
        }
        try {
            const updated = await apiJsonResponse(assetManagerHttp.request('/api/storage-settings/roots', {
                method:'POST', credentials:'same-origin',
                headers:{'Content-Type':'application/json'},
                body:JSON.stringify({path, expected_version:Number(snapshot.revision || 1)}),
            }));
            applyAllowedRootSettingsData(updated);
            if(announce) setStatus(`已授权资源文件夹 ${path}，缓存将写入该目录`);
            return {ok:true, registered:true};
        } catch(error){
            // 已被别的窗口登记：视为成功，不重复写。
            if(error?.code === 'ALLOWED_ROOT_ALREADY_REGISTERED') return {ok:true, registered:false};
            if(attempt === 0 && error?.code === 'VERSION_CONFLICT') continue;
            return {ok:false, registered:false, error, reason:'rejected'};
        }
    }
    return {ok:false, registered:false, reason:'unstable'};
}
function confirmRootAliasSuggestion(path, candidates, expectedVersion){
    return new Promise(resolve => {
        const overlay = document.createElement('div');
        overlay.className = 'storage-settings-overlay root-alias-confirm-overlay';
        const options = candidates.map(candidate => `<button class="hw-btn hw-btn-gold" type="button" data-root-alias-confirm="${escapeAttr(candidate.storage_root_id)}">确认绑定到「${escapeHtml(candidate.name || candidate.storage_root_id)}」</button>`).join('');
        overlay.innerHTML = `<section class="board-source-modal" data-floating-content role="dialog" aria-modal="true" aria-labelledby="rootAliasConfirmTitle">
            <header class="storage-settings-head"><div><strong id="rootAliasConfirmTitle">发现已登记素材路径</strong><span>请确认该路径是否为以下来源的另一种访问方式。</span></div><button class="hw-btn" type="button" data-root-alias-cancel data-root-alias-close aria-label="关闭" title="关闭"><i data-lucide="x"></i></button></header>
            <p class="board-create-summary">${escapeHtml(path)}</p>
            <div class="board-create-actions">${options}<button class="hw-btn" type="button" data-root-alias-cancel>取消</button></div>
        </section>`;
        document.body.appendChild(overlay);
        markAssetFloatingSurface(overlay, {surface:'asset-root-alias-confirm', content:'[role="dialog"]', close:() => { overlay.remove(); resolve(null); }});
        let finished = false;
        const close = value => {
            if(finished) return;
            finished = true;
            overlay.remove();
            resolve(value);
        };
        overlay.addEventListener('click', async event => {
            if(event.target.closest('[data-root-alias-cancel]')){ close(null); return; }
            const button = event.target.closest('[data-root-alias-confirm]');
            if(!button) return;
            const candidate = candidates.find(item => String(item.storage_root_id) === button.dataset.rootAliasConfirm);
            if(!candidate) return;
            button.disabled = true;
            try {
                const confirmation = await apiJsonResponse(assetManagerApi.confirmRootAlias({
                    path,
                    storage_root_id:candidate.storage_root_id,
                    expected_version:Number(expectedVersion),
                }));
                close({candidate, confirmation});
            } catch(err) {
                button.disabled = false;
                setStatus(err.message || '素材路径别名确认失败');
            }
        });
        refreshIcons();
    });
}
async function openAssetAiSettings(){
    await openStorageSettings('ai');
}
function storageNewExtraId(){ return `local_extra_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 8)}`; }
function normalizeLocalLibrariesFromSettings(data={}){
    const raw = Array.isArray(data.local_libraries) ? data.local_libraries : [];
    if(raw.length){
        return raw.map((lib, index) => ({
            id:String(lib.id || lib.kind || (index ? storageNewExtraId() : 'local')),
            kind:String(lib.kind || lib.id || (index ? storageNewExtraId() : 'local')),
            name:storageCleanName(lib.name, index ? storageExtraDefaultName(lib.path, index - 1) : DEFAULT_LOCAL_LIBRARY_NAME),
            path:String(lib.path || ''),
            builtin:Boolean(lib.builtin),
            legacyKind:String(lib.legacy_kind || '')
        }));
    }
    const names = data.local_library_names && typeof data.local_library_names === 'object' ? data.local_library_names : {};
    const extras = Array.isArray(data.extra_local_entries) && data.extra_local_entries.length
        ? data.extra_local_entries
        : (Array.isArray(data.extra_local_dirs) ? data.extra_local_dirs.map((path, index) => ({id:`local_extra_${index}`, kind:`local_extra_${index}`, path, name:storageExtraDefaultName(path, index)})) : []);
    return [
        {id:'local', kind:'local', name:storageCleanName(names.local, DEFAULT_LOCAL_LIBRARY_NAME), path:data.dirs?.local || '', builtin:true, legacyKind:''},
        ...extras.map((entry, index) => ({
            id:String(entry.id || entry.kind || `local_extra_${index}`),
            kind:String(entry.kind || entry.id || `local_extra_${index}`),
            name:storageCleanName(entry.name || names[entry.id] || names[entry.kind], storageExtraDefaultName(entry.path, index)),
            path:String(entry.path || entry || ''),
            builtin:false,
            legacyKind:`local_extra_${index}`
        }))
    ];
}
function applyStorageSettingsData(data={}){
    storageSettingsState.dirs = data.dirs || storageSettingsState.dirs || {};
    if(Number.isFinite(Number(data.revision))) storageSettingsState.revision = Number(data.revision);
    storageSettingsState.defaults = data.defaults || storageSettingsState.defaults || {};
    storageSettingsState.extraLocalDirs = Array.isArray(data.extra_local_dirs) ? data.extra_local_dirs : storageSettingsState.extraLocalDirs || [];
    storageSettingsState.extraLocalEntries = Array.isArray(data.extra_local_entries)
        ? data.extra_local_entries.map((entry, index) => ({
            id:String(entry.id || entry.kind || `local_extra_${index}`),
            kind:String(entry.kind || entry.id || `local_extra_${index}`),
            name:storageCleanName(entry.name, storageExtraDefaultName(entry.path, index)),
            path:String(entry.path || '')
        }))
        : storageSettingsState.extraLocalDirs.map((path, index) => ({id:`local_extra_${index}`, kind:`local_extra_${index}`, name:storageExtraDefaultName(path, index), path}));
    storageSettingsState.localLibraryNames = data.local_library_names || storageSettingsState.localLibraryNames || {};
    storageSettingsState.localLibraryReadOnly = data.local_library_read_only || {};
    storageSettingsState.localLibraries = normalizeLocalLibrariesFromSettings(data);
    setLocalLibraries(storageSettingsState.localLibraries.slice());
    if(!localLibraries.some(lib => lib.id === activeLocalLibraryId)) setActiveLocalLibraryId(localLibraries[0]?.id || 'local');
}
function renderServerDirectoryBrowser(){
    const state = serverDirectoryBrowserState;
    if(!state.open) return '';
    const selectLabel = state.onSelect ? '选择此文件夹' : '选择并挂载';
    const rows = state.directories.map(directory =>
        '<div class="server-directory-row">' +
            '<button type="button" class="server-directory-enter" data-server-directory-enter="' + escapeAttr(directory.path) + '"' + (state.loading || state.saving ? ' disabled' : '') + '>' +
                '<i data-lucide="folder"></i><span>' + escapeHtml(directory.name) + '</span><small>进入</small>' +
            '</button>' +
            '<button type="button" class="hw-btn hw-btn-gold server-directory-mount" data-server-directory-mount="' + escapeAttr(directory.path) + '"' + (state.loading || state.saving ? ' disabled' : '') + '><i data-lucide="check"></i>' + selectLabel + '</button>' +
        '</div>'
    ).join('');
    const path = state.path || '服务器根目录';
    const parentButton = '<button type="button" class="hw-btn" data-server-directory-parent' + (!state.parent || state.loading || state.saving ? ' disabled' : '') + '><i data-lucide="corner-left-up"></i>上级目录</button>';
    const currentButton = state.path
        ? '<button type="button" class="hw-btn hw-btn-gold" data-server-directory-mount-current="' + escapeAttr(state.path) + '"' + (state.loading || state.saving ? ' disabled' : '') + '><i data-lucide="check"></i>' + (state.onSelect ? '选择当前文件夹' : '选择当前目录并挂载') + '</button>'
        : '';
    const message = state.loading
        ? '<p class="server-directory-browser-message" role="status">正在读取目录…</p>'
        : (!rows && !state.error
            ? '<p class="server-directory-browser-message">' + (state.path ? '此目录下没有可浏览的子目录。' : '服务器没有可浏览的根目录。') + '</p>'
            : '');
    return '<section class="server-directory-browser" data-server-directory-browser aria-busy="' + (state.loading || state.saving ? 'true' : 'false') + '">' +
        '<header class="server-directory-browser-head"><div><strong>浏览服务器目录</strong><code title="' + escapeAttr(path) + '">' + escapeHtml(path) + '</code></div>' +
            '<button type="button" class="hw-btn hw-btn-icon" data-server-directory-close aria-label="关闭服务器目录浏览器" title="关闭"><i data-lucide="x"></i></button></header>' +
        '<div class="server-directory-browser-toolbar">' + parentButton + currentButton + '</div>' +
        (state.error ? '<p class="server-directory-browser-error" role="alert">' + escapeHtml(state.error) + ' <button type="button" class="hw-btn" data-server-directory-retry="' + escapeAttr(state.path || '') + '">重试</button></p>' : '') +
        '<div class="server-directory-list" role="tree" aria-label="服务器子目录">' + message + rows + '</div>' +
        (state.truncated ? '<p class="server-directory-browser-note">目录较多，仅显示前 ' + (state.limit || 100) + ' 项。</p>' : '') +
        '<p class="server-directory-browser-note">仅显示文件夹。选择并挂载会立即保存所选目录字段。</p>' +
    '</section>';
}
function renderServerDirectoryBrowserHost(){
    if(serverDirectoryBrowserState.key === 'boardSource'){
        let overlay = document.getElementById('boardSourceDirectoryBrowserOverlay');
        if(!serverDirectoryBrowserState.open){ overlay?.remove(); return; }
        if(!overlay){
            overlay = document.createElement('div'); overlay.id = 'boardSourceDirectoryBrowserOverlay'; overlay.className = 'storage-settings-overlay';
            overlay.addEventListener('click', event => { handleClick(event).catch(err => setStatus(err?.message || '操作失败')); }); document.body.appendChild(overlay);
        }
        overlay.innerHTML = `<div class="storage-settings-modal board-settings-modal" data-floating-content role="dialog" aria-modal="true" aria-label="选择服务器资源文件夹">${renderServerDirectoryBrowser()}</div>`;
        markAssetFloatingSurface(overlay, {surface:'board-source-directory-picker', content:'.board-settings-modal', close:() => setServerDirectoryBrowserState({...serverDirectoryBrowserState, open:false, loading:false})});
        refreshIcons();
        return;
    }
    renderStorageSettingsModal();
}
async function loadServerDirectoryBrowser(pathValue=null, options={}){
    return _loadServerDirectoryBrowserImpl(pathValue, options);
}
async function validateServerResourceDirectory(pathValue, options={}){
    const target = String(pathValue || '').trim();
    if(!target) throw new Error('请选择资源文件夹');
    const controller = typeof AbortController === 'function' ? new AbortController() : null;
    const timeoutMs = Number(options.timeoutMs) > 0 ? Number(options.timeoutMs) : 8000;
    let timedOut = false;
    const timer = controller ? setTimeout(() => { timedOut = true; controller.abort(); }, timeoutMs) : null; const onExternalAbort = () => controller?.abort();
    if(options.signal){
        if(options.signal.aborted){ if(timer) clearTimeout(timer); const e = new Error('已取消目录校验'); e.name = 'AbortError'; throw e; }
        options.signal.addEventListener?.('abort', onExternalAbort, {once:true});
    }
    try {
        const reqInit = {method:'GET', credentials:'same-origin'};
        if(controller) reqInit.signal = controller.signal;
        const response = await apiJsonResponse(assetManagerHttp.request('/api/storage-settings/browse?path=' + encodeURIComponent(target), reqInit));
        return String(response?.path || target);
    } catch(error){
        if(timedOut){ const timeoutErr = new Error('服务端目录校验超时（超过 8 秒），请重试'); timeoutErr.code = 'DIRECTORY_BROWSE_TIMEOUT'; throw timeoutErr; }
        throw error;
    } finally { if(timer) clearTimeout(timer); options.signal?.removeEventListener?.('abort', onExternalAbort); }
}
async function openServerDirectoryBrowser(key, options={}){
    if(!key || !canBrowseServerDirectories()) return;
    serverDirectoryBrowserState.abortController?.abort?.();
    const generation = Number(serverDirectoryBrowserState.generation || 0) + 1;
    setServerDirectoryBrowserState({open:true, path:null, parent:null, directories:[], loading:false, saving:false, error:'', key, truncated:false, limit:100, generation, abortController:null, onSelect:options.onSelect || null});
    renderServerDirectoryBrowserHost();
    await loadServerDirectoryBrowser(null, options);
}
async function fillServerDirectoryFromNative(key, pathValue){
    // 选完文件夹即持久化：写入字段后立即保存，成功与失败都有状态反馈，不静默。
    const input = directoryFieldInput(key);
    if(input) input.value = pathValue;
    await saveDirectoryPath(key, pathValue);
}
async function pickServerDirectoryNative(key, options={}){
    // 服务器本机会话优先弹系统原生对话框；远程/不可用时降级页面内浏览器。
    const input = directoryFieldInput(key);
    const currentPath = input ? String(input.value || '').trim() : '';
    const ctl = _createDirectoryAbortController(options);
    try {
        const reqInit = {method:'POST', credentials:'same-origin', headers:{'Content-Type':'application/json'}, body:JSON.stringify({title:'选择服务器目录', initial_dir:currentPath || null})};
        if(ctl.controller) reqInit.signal = ctl.controller.signal;
        const result = await apiJsonResponse(assetManagerHttp.request('/api/settings/directories/pick-native', reqInit));
        if(options.signal?.aborted) return;
        if(result.status === 'selected' && result.path){
            if(options.onSelect) await options.onSelect(result.path);
            else await fillServerDirectoryFromNative(key, result.path);
            return;
        }
        if(result.status === 'cancelled'){
            options.onCancel?.();
            return;
        }
        if(typeof options.onUnavailable === 'function'){
            await options.onUnavailable(result.reason || '未知原因', null);
            return;
        }
        setStatus('服务端目录选择器不可用（' + (result.reason || '未知原因') + '），已切换页面内浏览');
    } catch(error){
        if(error?.name === 'AbortError' && !ctl.timedOut()) return;
        const errObj = ctl.timedOut() ? Object.assign(new Error('选夹请求超时，请重试'), {code:'PICKER_TIMEOUT'}) : error;
        if(!(Number(errObj?.status) === 403 && errObj?.code === 'PICKER_NON_LOOPBACK') && typeof options.onUnavailable === 'function'){
            await options.onUnavailable(errObj?.message || '请求失败', errObj);
            return;
        }
        setStatus('服务端目录选择器不可用（' + (errObj.message || '请求失败') + '），已切换页面内浏览');
    } finally {
        ctl.cleanup();
    }
    await openServerDirectoryBrowser(key, options);
}
async function pickClientDirectoryNative(){
    // 客户端字段：在客户端本机弹原生对话框；路径只存客户端本地配置。
    const invoke = tauriInvokeFn();
    if(!invoke) return;
    try {
        const picked = await invoke('pick_client_folder', {title:'选择客户端上传来源目录', initialDir:clientUploadSourceDir || null});
        const pathValue = String(picked || '').trim();
        const input = document.getElementById('clientUploadSourceDir');
        if(input) input.value = pathValue;
        await saveClientUploadSourceDir(pathValue);
    } catch(error){
        setStatus(error?.message || '客户端目录选择失败');
    }
}
// 目录字段保存的统一外壳：负责 saving 态、重渲染与失败反馈，失败一律有状态提示。
async function withDirectorySaving(closeBrowser, task){
    if(closeBrowser){
        serverDirectoryBrowserState.saving = true;
        serverDirectoryBrowserState.error = '';
    }
    if(embeddedSettingsPage){
        setDirectorySettingsSaving(true);
        renderGlobalDirectorySettings();
    } else if(closeBrowser) {
        renderStorageSettingsModal();
    }
    try {
        await task();
        if(closeBrowser){
            setServerDirectoryBrowserState({...serverDirectoryBrowserState, open:false, saving:false, error:''});
        }
    } catch(error){
        if(closeBrowser){
            setServerDirectoryBrowserState({...serverDirectoryBrowserState, saving:false, error:error.message || '挂载目录失败'});
        }
        setStatus(error.message || '保存目录失败');
    } finally {
        if(embeddedSettingsPage){
            setDirectorySettingsSaving(false);
            renderGlobalDirectorySettings();
        } else if(closeBrowser) {
            renderStorageSettingsModal();
        }
    }
}
// 缓存根目录走独立的缩略图设置接口，与 storage 目录共用保存外壳与反馈。
async function saveThumbnailCacheDirectory(pathValue, closeBrowser=false){
    const trimmed = String(pathValue ?? '').trim();
    if(!trimmed){ setStatus('缓存根目录不能为空'); return; }
    const input = document.getElementById('thumbnailDirectory');
    if(input) input.value = trimmed;
    // 换到别的目录：先问原缓存是迁移、删除还是取消，别让选择器一挂载就把旧缓存留在原地。
    if(commitThumbnailSettingsChange('custom', trimmed)){
        if(closeBrowser) setServerDirectoryBrowserState({...serverDirectoryBrowserState, open:false, saving:false, error:''});
        return;
    }
    await withDirectorySaving(closeBrowser, async () => {
        const granted = await ensureThumbnailCacheRootAllowed(trimmed, {announce:false});
        await persistThumbnailStorageSettings();
        setStatus((closeBrowser ? '已保存缓存目录：' : '缓存目录已保存：') + trimmed + thumbnailCacheGrantSuffix(granted));
    });
}
// 自动授权失败时把原因留在状态行，生成时仍会弹出可授权的提示块。
function thumbnailCacheGrantSuffix(granted){
    if(!granted || granted.ok) return granted?.registered ? '（已授权资源文件夹）' : '';
    if(granted.reason === 'forbidden') return '（当前身份不能授权，请联系管理员授权该文件夹）';
    return '（未能授权资源文件夹：' + String(granted.error?.message || '目录不可用') + '）';
}
// 设置快照统一入口：同时记住"已保存"的目录，供切换判断使用。
function applyThumbnailSettingsSnapshot(data={}, extra={}){
    const mode = data.mode === 'custom' ? 'custom' : 'source';
    const directory = String(data.directory || '');
    // 格式 / 版本号由 QIN-513 引入，切换缓存目录的响应也带这两项；缺省时保留现值，
    // 不能把 revision 归 1，否则后续 CAS 保存会误判版本冲突。
    const format = data.format === 'webp' ? 'webp' : (data.format === 'jxl' ? 'jxl' : (thumbnailGeneratorState.format || 'jxl'));
    const revision = Number.isFinite(Number(data.revision)) ? Number(data.revision) : Number(thumbnailGeneratorState.revision || 1);
    setThumbnailGeneratorState({
        ...thumbnailGeneratorState,
        mode,
        directory,
        cacheFolder:data.cache_folder || thumbnailGeneratorState.cacheFolder || 'god-cache',
        format,
        revision,
        // GET 设置附带缓存统计（QIN-525）；PATCH 响应没有该字段时保留最近一次快照。
        cacheStats:(data.cache && typeof data.cache === 'object') ? data.cache : thumbnailGeneratorState.cacheStats,
        savedMode:mode,
        savedDirectory:directory,
        error:'',
        ...extra,
    });
    return thumbnailGeneratorState;
}
function thumbnailCacheSwitchResultText(data={}){
    const action = String(data.cache_action || 'keep');
    if(action === 'migrate') return `缓存目录已切换，迁移 ${Number(data.cache_moved || 0)} 个文件，跳过 ${Number(data.cache_skipped || 0)} 个同名文件`;
    if(action === 'delete') return `缓存目录已切换，已删除原缓存 ${Number(data.cache_deleted || 0)} 个文件`;
    return '缓存目录已切换';
}
function renderThumbnailCacheSwitchDialog(){
    const state = thumbnailCacheSwitchState;
    let overlay = document.getElementById('thumbnailCacheSwitchOverlay');
    if(!state.open){ overlay?.remove(); return; }
    if(!overlay){
        overlay = document.createElement('div');
        overlay.id = 'thumbnailCacheSwitchOverlay';
        overlay.className = 'storage-settings-overlay';
        document.body.appendChild(overlay);
    }
    const busy = state.busy;
    const targetLabel = state.toMode === 'custom' ? state.toDirectory : '受管数据目录（跟随素材源）';
    overlay.innerHTML = `<div class="storage-settings-modal storage-settings-modal-compact thumbnail-cache-switch-modal" role="dialog" aria-modal="true" aria-label="切换缓存目录">
        <div class="storage-settings-head"><div><strong>是否迁移原缓存</strong><span>缓存目录要换位置，原目录里的缓存文件需要一个去处。</span></div><button type="button" data-thumbnail-cache-switch="cancel" aria-label="关闭" ${busy ? 'disabled' : ''}><i data-lucide="x"></i></button></div>
        <div class="asset-pref-body">
            <div class="asset-pref-section">
                <p class="thumbnail-cache-switch-paths"><span>原目录</span><code>${escapeHtml(state.fromMode === 'custom' ? (state.fromDirectory || '所选目录') : '受管数据目录（跟随素材源）')}</code><span>新目录</span><code>${escapeHtml(targetLabel)}</code></p>
                <ul class="thumbnail-cache-switch-notes">
                    <li><strong>迁移</strong>：把原缓存搬到新目录，同名文件保留新目录已有版本。</li>
                    <li><strong>删除</strong>：清空原目录下的缓存文件，新目录从零开始。</li>
                    <li><strong>取消</strong>：不改动设置，继续使用原目录。</li>
                </ul>
                ${state.error ? `<div class="thumbnail-cache-error">${escapeHtml(state.error)}</div>` : ''}
            </div>
        </div>
        <div class="thumbnail-cache-actions">
            <button class="hw-btn" type="button" data-thumbnail-cache-switch="cancel" ${busy ? 'disabled' : ''}>取消</button>
            <button class="hw-btn" type="button" data-thumbnail-cache-switch="delete" ${busy ? 'disabled' : ''}><i data-lucide="trash-2"></i><span>${busy && state.action === 'delete' ? '正在删除…' : '删除'}</span></button>
            <button class="hw-btn hw-btn-gold" type="button" data-thumbnail-cache-switch="migrate" ${busy ? 'disabled' : ''}><i data-lucide="folder-input"></i><span>${busy && state.action === 'migrate' ? '正在迁移…' : '迁移'}</span></button>
        </div>
    </div>`;
    markAssetFloatingSurface(overlay, {surface:'thumbnail-cache-switch', content:'.thumbnail-cache-switch-modal', close:() => confirmThumbnailCacheSwitch('cancel')});
    refreshIcons();
}
function openThumbnailCacheSwitchDialog(toMode, toDirectory){
    setThumbnailCacheSwitchState({
        open:true, busy:false, action:'', error:'',
        fromMode:String(thumbnailGeneratorState.savedMode || 'source'),
        fromDirectory:String(thumbnailGeneratorState.savedDirectory || ''),
        toMode:toMode === 'custom' ? 'custom' : 'source',
        toDirectory:String(toDirectory || ''),
    });
    renderThumbnailCacheSwitchDialog();
}
function cancelThumbnailCacheSwitch(){
    if(thumbnailCacheSwitchState.busy) return;
    setThumbnailCacheSwitchState({open:false, busy:false, action:'', error:'', fromMode:'source', fromDirectory:'', toMode:'source', toDirectory:''});
    renderThumbnailCacheSwitchDialog();
    // 取消即继续用原目录：输入框回退到已保存值，界面不显示未生效的路径。
    setThumbnailGeneratorState({...thumbnailGeneratorState, mode:thumbnailGeneratorState.savedMode || thumbnailGeneratorState.mode, directory:thumbnailGeneratorState.savedDirectory || ''});
    const input = document.getElementById('thumbnailDirectory');
    if(input) input.value = thumbnailGeneratorState.savedDirectory || '';
    renderThumbnailCacheHost();
    setStatus('已取消切换，继续使用原缓存目录');
}
async function confirmThumbnailCacheSwitch(action){
    const state = thumbnailCacheSwitchState;
    if(!state.open || state.busy) return;
    if(action === 'cancel'){ cancelThumbnailCacheSwitch(); return; }
    if(!['migrate', 'delete'].includes(action)) return;
    setThumbnailCacheSwitchState({...state, busy:true, action, error:''});
    renderThumbnailCacheSwitchDialog();
    // 新目录先自动授权为资源文件夹；授权被拒时后端也会失败关闭，这里提前给出原因。
    if(state.toMode === 'custom'){
        const granted = await ensureThumbnailCacheRootAllowed(state.toDirectory, {announce:false});
        if(granted.reason === 'rejected'){
            setThumbnailCacheSwitchState({...thumbnailCacheSwitchState, busy:false, action:'', error:'未能授权新资源文件夹：' + String(granted.error?.message || '目录不可用')});
            renderThumbnailCacheSwitchDialog();
            return;
        }
    }
    try {
        const data = await apiJsonResponse(assetManagerApi.switchThumbnailCache({mode:state.toMode, directory:state.toDirectory, cache_action:action}));
        applyThumbnailSettingsSnapshot(data);
        setThumbnailCacheSwitchState({open:false, busy:false, action:'', error:'', fromMode:'source', fromDirectory:'', toMode:'source', toDirectory:''});
        renderThumbnailCacheSwitchDialog();
        renderThumbnailCacheHost();
        render();
        setStatus(thumbnailCacheSwitchResultText(data));
    } catch(error) {
        setThumbnailCacheSwitchState({...thumbnailCacheSwitchState, busy:false, action:'', error:error.message || '切换缓存目录失败'});
        renderThumbnailCacheSwitchDialog();
        setStatus(thumbnailCacheSwitchState.error);
    }
}
// 缓存模式 / 目录的统一提交入口：真的换了根就问旧缓存去处，否则只保存设置。
function commitThumbnailSettingsChange(nextMode, nextDirectory){
    const mode = nextMode === 'custom' ? 'custom' : 'source';
    const directory = mode === 'custom' ? String(nextDirectory || '').trim() : '';
    setThumbnailGeneratorState({...thumbnailGeneratorState, mode, directory, error:''});
    if(mode === 'custom' && !directory) return false;
    const changed = mode !== (thumbnailGeneratorState.savedMode || 'source')
        || (mode === 'custom' && pathCompareKey(directory) !== pathCompareKey(thumbnailGeneratorState.savedDirectory || ''));
    if(changed){
        openThumbnailCacheSwitchDialog(mode, directory);
        return true;
    }
    if(!directorySettingsPage && storageSettingsState.open){
        ensureThumbnailCacheRootAllowed(directory, {announce:false})
            .then(granted => persistThumbnailStorageSettings().then(() => {
                setStatus(`缓存目录已设置：${directory || '受管数据目录'}${thumbnailCacheGrantSuffix(granted)}`);
            }))
            .catch(err => {
                thumbnailGeneratorState.error = err.message || '缓存目录创建失败';
                renderStorageSettingsModal();
            });
    }
    return false;
}
// 设置页 / 生成弹窗改缓存目录：先确认旧缓存去处，确认后再落设置。
function applyThumbnailCacheDirectoryField(directory){
    commitThumbnailSettingsChange('custom', directory);
}
async function saveDirectoryPath(key, pathValue, closeBrowser=false){
    const trimmed = String(pathValue || '').trim();
    // 资源文件夹授权是独立动作，空值交给授权逻辑给出更贴切的提示，不走通用目录空值拦截。
    if(key === ALLOWED_ROOT_FIELD_KEY){ await registerAllowedRootPath(trimmed); return; }
    if(!trimmed && !OPTIONAL_DIRECTORY_FIELDS.has(key)){ setStatus('目录路径不能为空'); return; }
    if(key === THUMBNAIL_DIRECTORY_FIELD_KEY){ await saveThumbnailCacheDirectory(trimmed, closeBrowser); return; }
    const input = document.getElementById('storageDir_' + key);
    if(input) input.value = trimmed;
    syncStorageSettingsInputsToState();
    await withDirectorySaving(closeBrowser, async () => {
        await saveStorageSettings();
        if(closeBrowser) setStatus('目录已保存：' + trimmed);
    });
}
function renderProjectWorkspaceSettingsBody(){
    if(registryPrincipalLoading){
        return `<section class="asset-pref-section project-workspace-settings" data-project-workspace-settings aria-busy="true">
            <div class="asset-pref-title"><i data-lucide="folder-cog"></i><span>项目工作区</span><small>正在确认权限…</small></div>
        </section>`;
    }
    if(!canManageProjectWorkspaceSettings()){
        return `<section class="asset-pref-section project-workspace-settings" data-project-workspace-settings>
            <div class="asset-pref-title"><i data-lucide="folder-lock"></i><span>项目工作区</span><small>仅管理员可管理</small></div>
            <p class="board-settings-hint">项目工作区根目录和目录模板由管理员统一维护。已有项目继续使用创建时绑定的工作区。</p>
        </section>`;
    }
    const dirs = storageSettingsState.dirs || {};
    const defaultRoot = String(storageSettingsState.defaults?.projects || '');
    return `<section class="asset-pref-section project-workspace-settings" data-project-workspace-settings>
        <div class="asset-pref-title"><i data-lucide="folder-cog"></i><span>项目工作区</span><small>只影响之后新建的项目</small></div>
        <p class="board-settings-hint">更改根目录不会迁移、删除或重命名已有项目的工作区。</p>
        <div class="storage-dir-grid">
            <label class="storage-dir-row"><span>项目工作区根目录</span><div class="directory-input-row"><input id="storageDir_projects" value="${escapeAttr(dirs.projects || '')}" title="${escapeAttr(dirs.projects || '')}" placeholder="${escapeAttr(defaultRoot)}" autocomplete="off">${canBrowseServerDirectories() ? '<button type="button" class="hw-btn hw-btn-icon directory-picker-btn" data-directory-picker="projects" title="浏览服务器目录" aria-label="浏览服务器项目工作区目录"><i data-lucide="folder-open"></i></button>' : ''}</div></label>
        </div>
        ${directorySettingsPage ? '' : renderServerDirectoryBrowser()}
        <div class="storage-settings-actions inline">
            <button class="hw-btn" type="button" data-project-directory-template-manager-open><i data-lucide="folders"></i><span>管理目录模板</span></button>
            <button class="hw-btn hw-btn-gold" type="button" data-storage-save><i data-lucide="save"></i><span>保存目录</span></button>
        </div>
    </section>`;
}
function storageExtraLocalDirs(){ return storageExtraLocalEntries().map(entry => entry.path); }
function storageKindEntries(){
    const base = [
        {key:'generated', label:STORAGE_KIND_LABELS.generated},
        {key:'upload', label:STORAGE_KIND_LABELS.upload}
    ];
    const libraries = (storageSettingsState.localLibraries?.length ? storageSettingsState.localLibraries : localLibraries)
        .map(lib => ({key:lib.kind || lib.id, label:storageCleanName(lib.name, DEFAULT_LOCAL_LIBRARY_NAME)}));
    return [...base, ...libraries];
}
function storageKindExists(kind){ return storageKindEntries().some(item => item.key === kind); }
function storageKindLabel(kind){ return storageKindEntries().find(item => item.key === kind)?.label || STORAGE_KIND_LABELS[kind] || kind; }
async function openStorageSettings(tab='ai'){
    if(tab === 'directories' && !directorySettingsPage){
        window.top.location.href = '/static/pages/index.html?view=settings&section=directories';
        return;
    }
    if(!storageSettingsState.open) storageSettingsReturnFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    storageSettingsState.tab = 'all';
    storageSettingsState.focusSection = ['ai','directories','proxy','video'].includes(tab) ? tab : '';
    storageSettingsState.open = true;
    setAssetAiSettingsState({...assetAiSettingsState, loading:true, message:''});
    assetProxySettingsState = {...assetProxySettingsState, projectId:String(activeProjectId || new URLSearchParams(location.search).get('project_id') || '').trim(), loading:true, message:''};
    setThumbnailGeneratorState({...thumbnailGeneratorState, loading:true, error:''});
    renderStorageSettingsModal();
    try {
        const principalRequest = loadRegistryPrincipal().catch(() => null);
        const templateRequest = principalRequest.then(() => (
            canManageProjectWorkspaceSettings()
                ? apiJsonResponse(assetManagerApi.getProjectDirectoryTemplates()).catch(error => ({__error:error}))
                : null
        ));
        const [storage, thumbnail, status, providerData, ruleData, proxy] = await Promise.all([
            apiJsonResponse(assetManagerApi.getStorageSettings()),
            apiJsonResponse(assetManagerApi.getThumbnailSettings()),
            apiJsonResponse(assetManagerApi.getRegistryStatus()).catch(() => null),
            apiJsonResponse(assetManagerApi.getProviders()).catch(() => ({providers:[]})),
            apiJsonResponse(assetManagerApi.getAssetClassificationPrompt()).catch(() => ({})),
            apiJsonResponse(assetManagerApi.getAssetProxySettings(assetProxySettingsState.projectId)).catch(() => null),
        ]);
        const templateData = await templateRequest;
        applyStorageSettingsData(storage || {});
        if(templateData?.__error){
            setProjectDirectoryTemplateManagerState({...projectDirectoryTemplateManagerState, error:templateData.__error.message || '目录模板读取失败'});
        } else if(templateData){
            applyProjectDirectoryTemplates(templateData);
        }
        if(status) setRegistryStatus(status);
        setApiProviders(Array.isArray(providerData.providers) ? providerData.providers : []);
        normalizeLocalCaptionSettings();
        setAssetAiSettingsState({
            ...assetAiSettingsState,
            rule:ruleData.prompt || ruleData.default_prompt || '',
            defaultRule:ruleData.default_prompt || '',
            loading:false,
            message:localCaptionProviders().length ? '已连接可用模型' : '请先配置 API 平台',
        });
        applyThumbnailSettingsSnapshot(thumbnail, {loading:false});
        if(proxy) applyAssetProxySettings(proxy);
        else assetProxySettingsState = {...assetProxySettingsState, loading:false, message:'代理设置读取失败'};
        renderStorageSettingsModal();
    } catch(err){
        setAssetAiSettingsState({...assetAiSettingsState, loading:false, message:err.message || 'AI 分类设置读取失败'});
        setThumbnailGeneratorState({...thumbnailGeneratorState, loading:false, error:err.message || '加载资产设置失败'});
        assetProxySettingsState = {...assetProxySettingsState, loading:false, message:err.message || '代理设置读取失败'};
        setStatus(thumbnailGeneratorState.error);
        renderStorageSettingsModal();
    }
    void loadJxlTranscodeScan();
}
function applyAssetProxySettings(policy={}){
    assetProxySettingsState = {
        ...assetProxySettingsState,
        enabled:Boolean(policy.enabled),
        autoQueue:Boolean(policy.auto_queue),
        maxWidth:Number(policy.max_width || 960),
        maxHeight:Number(policy.max_height || 540),
        loading:false,
        saving:false,
        message:'已加载',
    };
}
function renderAssetProxySettingsBody(){
    const state = assetProxySettingsState;
    const dimensions = `${state.maxWidth}x${state.maxHeight}`;
    const scope = state.projectId ? `项目覆盖 · ${state.projectId}` : '全局默认';
    return `<div class="board-proxy-settings" data-board-proxy-settings>
        <div class="asset-pref-title"><i data-lucide="film"></i><span>代理设置</span><small>${escapeHtml(state.loading ? '正在读取…' : state.saving ? '正在保存…' : state.message || scope)}</small></div>
        <p class="board-settings-hint">为超过预设尺寸的视频生成 H.264 / MP4 工作副本，媒体预览优先使用可用代理。</p>
        <div class="board-proxy-grid">
            <label class="board-proxy-switch"><input id="boardProxyEnabled" type="checkbox" ${state.enabled ? 'checked' : ''} ${state.loading || state.saving ? 'disabled' : ''}><span><strong>自动生成代理</strong><small>入库后只为超过预设尺寸的视频创建持久任务</small></span></label>
            <label class="storage-dir-row board-proxy-field"><span>最大尺寸</span><select id="boardProxyDimensions" ${state.loading || state.saving ? 'disabled' : ''}><option value="640x360" ${dimensions === '640x360' ? 'selected' : ''}>640 x 360</option><option value="960x540" ${dimensions === '960x540' ? 'selected' : ''}>960 x 540</option><option value="1280x720" ${dimensions === '1280x720' ? 'selected' : ''}>1280 x 720</option></select></label>
            <label class="board-proxy-switch"><input id="boardProxyAutoQueue" type="checkbox" ${state.autoQueue ? 'checked' : ''} ${state.loading || state.saving ? 'disabled' : ''}><span><strong>自动入队</strong><small>关闭后仅保留策略，需从素材动作手动提交</small></span></label>
            <div class="board-proxy-profile"><span>编码配置</span><strong>H.264 / MP4</strong><small>${escapeHtml(scope)}</small></div>
        </div>
        <div class="storage-settings-actions inline"><button class="hw-btn hw-btn-gold" type="button" data-board-proxy-save ${state.loading || state.saving ? 'disabled' : ''}><i data-lucide="save"></i><span>${state.saving ? '正在保存…' : '保存代理设置'}</span></button></div>
    </div>`;
}
async function saveAssetProxySettings(){
    const [maxWidth,maxHeight] = String(document.getElementById('boardProxyDimensions')?.value || '960x540').split('x').map(Number);
    const payload = {project_id:assetProxySettingsState.projectId, enabled:Boolean(document.getElementById('boardProxyEnabled')?.checked), auto_queue:Boolean(document.getElementById('boardProxyAutoQueue')?.checked), max_width:maxWidth, max_height:maxHeight};
    assetProxySettingsState = {...assetProxySettingsState, saving:true, message:'正在保存…'};
    renderStorageSettingsModal();
    try {
        applyAssetProxySettings(await apiJsonResponse(assetManagerApi.updateAssetProxySettings(payload)));
        setStatus('视频代理设置已保存');
    } catch(error){
        assetProxySettingsState = {...assetProxySettingsState, saving:false, message:error.message || '保存失败'};
        setStatus(error.message || '视频代理设置保存失败');
    }
    renderStorageSettingsModal();
}
function closeStorageSettings(){
    if(directorySettingsPage) return;
    storageSettingsState.open = false;
    document.getElementById('storageSettingsOverlay')?.remove();
    const returnFocus = storageSettingsReturnFocus;
    storageSettingsReturnFocus = null;
    if(returnFocus?.isConnected) requestAnimationFrame(() => returnFocus.focus());
}
function syncStorageSettingsInputsToState(){
    storageSettingsState.dirs = storageSettingsState.dirs || {};
    const trashEl = document.getElementById('storageDir_trash');
    if(trashEl) storageSettingsState.dirs.trash = trashEl.value.trim();
    if(canManageProjectWorkspaceSettings()){
        const projectsRoot = document.getElementById('storageDir_projects');
        if(projectsRoot) storageSettingsState.dirs.projects = projectsRoot.value.trim();
    }
    const provider = document.getElementById('prefCaptionProvider');
    const model = document.getElementById('prefCaptionModel');
    const captionPrompt = document.getElementById('prefCaptionPrompt');
    const classifyPrompt = document.getElementById('prefClassifyPrompt');
    const classificationRule = document.getElementById('prefClassificationRule');
    if(provider) setLocalCaptionProvider(provider.value || '');
    if(model) setLocalCaptionModel(model.value || '');
    if(captionPrompt) setLocalCaptionPrompt(captionPrompt.value.trim() || '描述图片');
    if(classifyPrompt) setLocalClassifyPrompt(classifyPrompt.value.trim());
    if(classificationRule) assetAiSettingsState.rule = classificationRule.value;
}
async function saveAssetAiSettings(){
    syncStorageSettingsInputsToState();
    if(!localCaptionProvider || !localCaptionModel){
        assetAiSettingsState.message = '请先配置可用的平台和模型';
        renderStorageSettingsModal();
        return;
    }
    setAssetAiSettingsState({...assetAiSettingsState, saving:true, message:'正在保存…'});
    renderStorageSettingsModal();
    try {
        writeLocalCaptionSettings();
        const data = await apiJsonResponse(assetManagerApi.updateAssetClassificationPrompt({prompt:assetAiSettingsState.rule || ''}));
        setAssetAiSettingsState({...assetAiSettingsState, rule:data.prompt || assetAiSettingsState.rule, saving:false, message:'已保存'});
        setStatus('AI 分类设置已保存');
    } catch(err){
        setAssetAiSettingsState({...assetAiSettingsState, saving:false, message:err.message || '保存失败'});
        throw err;
    } finally {
        renderStorageSettingsModal();
    }
}
async function setAssetAiAutoClassify(enabled){
    if(assetAiSettingsState.saving) return;
    syncStorageSettingsInputsToState();
    if(enabled && (!localCaptionProvider || !localCaptionModel)){
        setLocalAutoClassify(false);
        writeLocalCaptionSettings();
        setAssetAiSettingsState({...assetAiSettingsState, message:'请先配置可用的平台和模型'});
        renderStorageSettingsModal();
        setStatus('请先配置 AI 分类平台和模型');
        return;
    }
    const previous = localAutoClassify;
    setLocalAutoClassify(Boolean(enabled));
    writeLocalCaptionSettings();
    setAssetAiSettingsState({...assetAiSettingsState, saving:true, message:enabled ? '正在启动后台分类…' : '正在停止后台分类…'});
    renderStorageSettingsModal();
    try {
        if(enabled){
            const queued = await apiJsonResponse(assetManagerApi.startAssetClassificationBackground({
                provider:localCaptionProvider,
                model:localCaptionModel,
                prompt:localClassifyPrompt || '',
            }));
            const task = queued.task || null;
            setAssetAiSettingsState({...assetAiSettingsState, saving:false, backgroundTaskId:task?.id || '', message:'后台分类已启动'});
            setStatus('AI 智能分类已启动，可在任务中心查看进度');
            if(task?.id) pollAssetClassificationJob(task.id);
        } else {
            await apiJsonResponse(assetManagerApi.stopAssetClassificationBackground());
            setAssetAiSettingsState({...assetAiSettingsState, saving:false, backgroundTaskId:'', message:'后台分类已停止'});
            setStatus('AI 智能分类已停止');
        }
    } catch(err){
        setLocalAutoClassify(previous);
        writeLocalCaptionSettings();
        setAssetAiSettingsState({...assetAiSettingsState, saving:false, message:err.message || (enabled ? '启动失败' : '停止失败')});
        throw err;
    } finally {
        renderStorageSettingsModal();
    }
}
async function pollAssetClassificationJob(taskId){
    if(!taskId) return;
    try {
        const data = await apiJsonResponse(assetManagerApi.getAssetClassificationJob(taskId));
        const task = data.task || {};
        const progress = task.progress || {};
        if(['queued','running'].includes(task.status)){
            setStatus(`AI 分类后台执行中：${Number(progress.processed || 0)} / ${Number(progress.total || 0)}`);
            window.setTimeout(() => pollAssetClassificationJob(taskId), 1800);
            return;
        }
        if(task.status === 'succeeded'){
            await refreshRegistryAssets(false).catch(() => null);
            setStatus(`AI 分类完成：新增 ${Number(progress.classified || 0)} 张，失败 ${Number(progress.failed || 0)} 张`);
        } else if(task.status === 'cancelled') setStatus('AI 智能分类已停止');
        else setStatus(task.error || 'AI 分类后台任务失败');
    } catch(_) {}
}
async function loadStorageFiles(kind, options={}){
    const append = Boolean(options.append);
    let nextKind = kind || 'generated';
    if(!storageKindExists(nextKind)) nextKind = 'generated';
    if(storageSettingsState.loading || storageSettingsState.loadingMore) return;
    const currentGrid = document.querySelector('[data-storage-file-grid]');
    const keepScrollTop = append && currentGrid ? currentGrid.scrollTop : null;
    storageSettingsState.kind = nextKind;
    if(append){
        if(!storageSettingsState.hasMore) return;
        storageSettingsState.loadingMore = true;
    } else {
        storageSettingsState.items = [];
        storageSettingsState.offset = 0;
        storageSettingsState.total = 0;
        storageSettingsState.hasMore = false;
        storageSettingsState.selected = new Set();
        storageSettingsState.loading = true;
    }
    if(!append) renderStorageSettingsModal();
    try {
        const offset = append ? storageSettingsState.offset : 0;
        const limit = storageSettingsState.pageSize || 80;
        const data = await apiJsonResponse(assetManagerApi.getStorageFiles(storageSettingsState.kind, offset, limit));
        const items = data.items || [];
        storageSettingsState.items = append ? [...storageSettingsState.items, ...items] : items;
        storageSettingsState.offset = offset + items.length;
        storageSettingsState.total = Number(data.total || storageSettingsState.items.length || 0);
        storageSettingsState.hasMore = Boolean(data.has_more);
        storageSettingsState.selected = new Set([...storageSettingsState.selected].filter(id => storageSettingsState.items.some(item => item.id === id)));
    } finally {
        storageSettingsState.loading = false;
        storageSettingsState.loadingMore = false;
        storageSettingsState.restoreScrollTop = keepScrollTop;
        renderStorageSettingsModal();
    }
}
function handleStorageFileGridScroll(event){
    const grid = event.currentTarget;
    if(!grid || storageSettingsState.loading || storageSettingsState.loadingMore || !storageSettingsState.hasMore) return;
    if(grid.scrollTop + grid.clientHeight >= grid.scrollHeight - 260){
        loadStorageFiles(storageSettingsState.kind, {append:true}).catch(err => setStatus(err.message || '加载更多图片失败'));
    }
}
function applyAllowedRootSettingsData(data={}){
    assetAllowedRootPaths.clear();
    setAllowedRootSettingsState({
        ...allowedRootSettingsState,
        revision:Number.isFinite(Number(data.revision)) ? Number(data.revision) : allowedRootSettingsState.revision,
        roots:Array.isArray(data.roots) ? data.roots.map(root => ({
            id:String(root.id || ''), name:String(root.name || ''), path:String(root.path || ''), source:String(root.source || 'persistent'),
            readOnly:Boolean(root.read_only), active:root.active !== false,
            aliases:Array.isArray(root.aliases) ? root.aliases.map(String) : [],
            aliasCandidates:Array.isArray(root.alias_candidates) ? root.alias_candidates : [],
            availability:String(root.availability || 'offline'),
            availabilityReason:String(root.availability_reason || ''),
            availabilityMessage:String(root.availability_message || '')
        })) : [],
        error:''
    });
    (Array.isArray(data.roots) ? data.roots : []).forEach(root => {
        const id = String(root?.id || '');
        const path = String(root?.path || '').trim();
        if(id && path) assetAllowedRootPaths.set(id, path);
        if(Array.isArray(root?.aliases)){
            root.aliases.forEach(alias => {
                const aliasId = String(alias || '').trim();
                if(aliasId && path) assetAllowedRootPaths.set(aliasId, path);
            });
        }
    });
}

function resourceFolderDisplayName(root){
    const explicitName = String(root?.name || '').trim();
    if(explicitName) return explicitName;
    const path = String(root?.path || '').trim();
    if(path){
        const normalized = path.replace(/[\\/]+$/, '');
        if(normalized){
            if(/^[a-zA-Z]:$/.test(normalized)){
                return `${normalized.toUpperCase()} 盘`;
            }
            const segments = normalized.split(/[\\/]/).filter(Boolean);
            if(segments.length > 0){
                const leaf = segments[segments.length - 1].trim();
                if(leaf) return leaf;
            }
        } else if(path === '/' || path === '\\'){
            return '根目录 (/)';
        }
    }
    return '资源文件夹';
}

function renderPurgeConfirmModal(){
    if(!pendingPurgeRoot) return '';
    return `<div class="removed-roots-modal-backdrop" data-floating-surface="purge-confirm-modal" data-modal-backdrop="purge-confirm">
        <div class="removed-roots-modal" style="max-width:480px" data-floating-content role="alertdialog" aria-modal="true" aria-labelledby="purgeTitle">
            <header class="removed-roots-modal-header">
                <h2 id="purgeTitle" style="color:var(--gw-error)"><i data-lucide="alert-triangle"></i><span>确认清除资源文件夹索引？</span></h2>
                <button type="button" class="hw-btn hw-btn-icon hw-modal-close" data-floating-close data-purge-cancel aria-label="取消"><i data-lucide="x"></i></button>
            </header>
            <div class="removed-roots-modal-body">
                <p style="margin:0;line-height:1.6">确定清除资源文件夹 <code style="overflow-wrap:anywhere">${escapeHtml(pendingPurgeRoot.path)}</code> 吗？</p>
                <p class="board-settings-hint" style="color:var(--gw-warning, #f59e0b);margin:4px 0 0">此操作将清空本系统生成的关于该素材库的所有元数据记录、搜索索引及缩略图缓存，但<b>绝不会删除您的磁盘原始素材文件</b>。</p>
                <div style="display:flex;justify-content:flex-end;gap:10px;margin-top:12px">
                    <button type="button" class="hw-btn" data-floating-close data-purge-cancel>取消</button>
                    <button type="button" class="hw-btn hw-btn-danger" data-purge-confirm="${escapeAttr(pendingPurgeRoot.id)}">清除索引</button>
                </div>
            </div>
        </div>
    </div>`;
}

function renderAllowedRootsSettings(){
    if(!canManageAllowedRoots()) return '';
    const statusLabel = root => root.availability === 'available' ? '在线'
        : root.availability === 'probing' ? '探活中' : root.availability === 'removed' ? '已移除' : root.availability === 'invalid' ? '无效' : '离线';
    const seenPaths = new Set();
    const uniqueRoots = [];
    for(const root of (allowedRootSettingsState.roots || [])){
        const key = root.path.toLowerCase().replace(/\\/g, '/').replace(/\/+$/, '');
        if(seenPaths.has(key)) continue;
        seenPaths.add(key);
        uniqueRoots.push(root);
    }
    const entries = uniqueRoots.map(root => {
        const removable = canManageAllowedRoots() && !root.readOnly && Boolean(root.path);
        const canOperateActiveRoot = removable && root.availability === 'available';
        const purgeIncomplete = root.availabilityReason === 'PURGE_INCOMPLETE';
        const status = root.active ? statusLabel(root) : (purgeIncomplete ? '清除未完成' : '已移除');
        const rowClass = root.active ? 'allowed-root-row' : 'allowed-root-row allowed-root-row--removed';
        // Legacy contract markers remain source-level only; visible copy is the resource-folder terminology.
        // 系统根（只读）
        // root.active ? root.aliasCandidates.map
        const actions = root.active
            ? `${root.aliasCandidates.map(candidate => `<button type="button" class="hw-btn" data-storage-root-alias-confirm="${escapeAttr(root.id)}" data-storage-root-alias-path="${escapeAttr(candidate.path || '')}" ${!canManageAllowedRoots() || allowedRootSettingsState.saving ? 'disabled' : ''}>确认别名：${escapeHtml(candidate.path || '')}</button>`).join('')}${canOperateActiveRoot ? `<button type="button" class="hw-btn" data-resource-folder-remove="${escapeAttr(root.id)}" ${allowedRootSettingsState.saving ? 'disabled' : ''}>移出资源库</button><button type="button" class="hw-btn hw-btn-danger" data-storage-root-purge-prompt="${escapeAttr(root.id)}" data-root-path="${escapeAttr(root.path)}" ${allowedRootSettingsState.saving ? 'disabled' : ''}>清除索引</button>` : ''}`
            : `${removable ? `${purgeIncomplete ? '' : `<button type="button" class="hw-btn hw-btn-gold" data-storage-root-restore="${escapeAttr(root.id)}" ${allowedRootSettingsState.saving ? 'disabled' : ''}>恢复</button>`}<button type="button" class="hw-btn hw-btn-danger" data-storage-root-purge-prompt="${escapeAttr(root.id)}" data-root-path="${escapeAttr(root.path)}" ${allowedRootSettingsState.saving ? 'disabled' : ''}>清除索引</button>` : ''}`;
        return `<article class="${rowClass}" data-allowed-root="${escapeAttr(root.id)}"><div class="allowed-root-info"><strong>${escapeHtml(resourceFolderDisplayName(root))}</strong><code title="${escapeAttr(root.path)}">${escapeHtml(root.path)}</code><small class="allowed-root-status allowed-root-status--${escapeAttr(root.availability)}">${escapeHtml(status)}${root.availabilityReason ? ` · ${escapeHtml(root.availabilityReason)}` : ''}</small>${root.availabilityMessage ? `<small>${escapeHtml(root.availabilityMessage)}</small>` : ''}${root.aliases.map(alias => `<small>已确认路径别名：<code>${escapeHtml(alias)}</code></small>`).join('')}</div><div class="allowed-root-actions">${actions}</div></article>`;
    }).join('');
    const allowedRootPicker = canBrowseServerDirectories()
        ? '<button type="button" class="hw-btn hw-btn-icon directory-picker-btn" data-directory-picker="' + ALLOWED_ROOT_FIELD_KEY + '" title="浏览服务器目录" aria-label="浏览服务器资源文件夹"><i data-lucide="folder-open"></i></button>'
        : '';
    const addForm = canManageAllowedRoots()
        ? `<div class="allowed-root-add"><label class="storage-dir-row"><span>服务器绝对路径</span><div class="directory-input-row"><input id="allowedRootPath" value="" autocomplete="off" placeholder="输入资源文件夹路径">${allowedRootPicker}</div></label><button type="button" class="hw-btn hw-btn-gold" data-storage-root-add ${allowedRootSettingsState.saving ? 'disabled' : ''}>添加资源文件夹</button></div>`
        : '<p class="board-settings-hint">只读账户可以查看资源文件夹及其状态。</p>';
    return `<section class="asset-pref-section allowed-roots-settings" data-allowed-roots-settings><div class="asset-pref-title allowed-roots-title-row"><div class="allowed-roots-title-left"><i data-lucide="folder-check"></i><span>资源文件夹</span><small>${canManageAllowedRoots() ? '管理员管理' : '只读'}</small></div></div><p class="board-settings-hint">移出资源库只撤销扫描授权并保留索引；清除索引不可恢复，但不会删除源文件。</p>${allowedRootSettingsState.error ? `<p class="allowed-root-error" role="alert">${escapeHtml(allowedRootSettingsState.error)}</p>` : ''}${addForm}<div class="allowed-root-list">${entries || '<p class="directory-status">暂无资源文件夹</p>'}</div>${pendingPurgeRoot ? renderPurgeConfirmModal() : ''}</section>`;
}
function renderGlobalDirectorySettings(){
    captureWorkspaceMappingForm();
    let host = document.getElementById('storageSettingsOverlay');
    if(!host){ host = document.createElement('main'); host.id = 'storageSettingsOverlay'; host.className = 'directory-page'; document.body.appendChild(host); }
    const focusId = host.contains(document.activeElement) ? String(document.activeElement?.id || '') : '';
    if(permissionsSettingsPage){
        host.innerHTML = `<header class="directory-page-header gw-settings-panel-head"><h1><i data-lucide="shield-check"></i>允许根权限管理</h1><button type="button" class="hw-btn" data-directory-reload title="重新读取允许根配置" ${directorySettingsSaving ? 'disabled' : ''}><i data-lucide="refresh-cw"></i>重新读取</button></header>
        ${directorySettingsError ? `<div class="directory-error" role="alert">${escapeHtml(directorySettingsError)}</div>` : ''}
        ${!directorySettingsReady ? `<div class="directory-status" role="status">${directorySettingsError ? '<span>请检查权限或连接后重新读取。</span> <button type="button" class="hw-btn" data-directory-reload data-directory-retry><i data-lucide="refresh-cw"></i>重试读取</button>' : '正在读取允许根权限…'}</div>` : `<fieldset class="permissions-roots-fieldset" ${directorySettingsSaving ? 'disabled' : ''}>${renderAllowedRootsSettings()}</fieldset>${renderWorkspaceSettingsBody()}${renderServerDirectoryBrowser()}<footer class="directory-footer"><div id="assetStatus" class="directory-status" role="status" aria-live="polite">${escapeHtml(currentStatusText)}</div></footer>`}`;
    } else {
        // 目录设置页保留两列布局：左列仅保留数据位置；右列依次为项目工作区、缓存目录、工作区（QIN-649）。
        const headerHtml = `<header class="directory-page-header gw-settings-panel-head"><h1><i data-lucide="folder-cog"></i>目录设置</h1><button type="button" class="hw-btn" data-directory-reload title="重新读取目录设置" ${directorySettingsSaving ? 'disabled' : ''}><i data-lucide="refresh-cw"></i>重新读取</button></header>`;
        const loadingHtml = directorySettingsError
            ? '<span>请检查权限或连接后重新读取。</span> <button type="button" class="hw-btn" data-directory-reload data-directory-retry><i data-lucide="refresh-cw"></i>重试读取</button>'
            : '正在读取目录设置…';
        const leftColumnHtml = `
        <div class="directory-cards-column directory-cards-column--left">
        ${renderDataLocationCard()}
        </div>`;
        const rightColumnHtml = `
        <div class="directory-cards-column directory-cards-column--right">
        ${renderProjectWorkspaceSettingsBody()}
        <section class="asset-pref-section bay-inset p-4 rounded-xl gw-settings-card">
            ${renderAssetThumbnailCacheDirectoryBody()}
        </section>
        ${renderWorkspaceSettingsBody()}
        </div>`;
        const cardsHtml = `<fieldset class="directory-cards-grid" ${directorySettingsSaving ? 'disabled' : ''}>
        ${leftColumnHtml}${rightColumnHtml}
        </fieldset>${directorySettingsPage ? renderServerDirectoryBrowser() : ''}`;
        const footerHtml = `<footer class="directory-footer"><div id="assetStatus" class="directory-status" role="status" aria-live="polite">${escapeHtml(currentStatusText)}</div></footer>`;
        host.innerHTML = `${headerHtml}
        ${renderDataLocationBanner()}
        ${directorySettingsError ? `<div class="directory-error" role="alert">${escapeHtml(directorySettingsError)}</div>` : ''}
        ${!directorySettingsReady ? `<div class="directory-status" role="status">${loadingHtml}</div>` : `${cardsHtml}${footerHtml}`}`;
        host.querySelector('.project-workspace-settings [data-storage-save]')?.remove();
    }
    refreshIcons();
    if(permissionsSettingsPage && typeof window !== 'undefined' && window.parent && window.parent !== window){
        requestAnimationFrame(() => {
            const height = Math.max(220, Math.ceil(Math.max(host.scrollHeight || 0, document.documentElement?.scrollHeight || 0)));
            window.parent.postMessage({type:'gw-permissions-roots-height', height}, window.location.origin);
        });
    }
    // 自动保存会整体重建页面，渲染后把焦点还给原字段，避免打断连续录入。
    requestAnimationFrame(() => {
        if(!focusId) return;
        const next = host.querySelector(`[id="${focusId.replace(/"/g,'')}"]`);
        if(next && document.activeElement !== next) next.focus({preventScroll:true});
    });
    ensureDataLocationBindings(host);
}
// 资源文件夹授权登记：Enter、选文件夹与「添加资源文件夹」按钮共用同一条持久化路径。
async function registerAllowedRootPath(pathValue){
    const path = String(pathValue || '').trim();
    if(!path){
        setAllowedRootSettingsState({...allowedRootSettingsState, error:'请输入服务器绝对路径'});
        renderGlobalDirectorySettings();
        setStatus('请输入服务器绝对路径');
        return;
    }
    if(allowedRootSettingsState.saving) return;
    const payload = {path, expected_version: Number(allowedRootSettingsState.revision || 1)};
    await saveAllowedRootsRequest('/api/storage-settings/roots', 'POST', payload);
}
function renderStorageSettingsModal(){
    if(!storageSettingsState.open) return;
    if(directorySettingsPage){ renderGlobalDirectorySettings(); return; }
    let overlay = document.getElementById('storageSettingsOverlay');
    if(!overlay){
        overlay = document.createElement('div');
        overlay.id = 'storageSettingsOverlay';
        overlay.className = 'storage-settings-overlay';
        document.body.appendChild(overlay);
    }
    const previousFocusId = overlay.contains(document.activeElement) ? String(document.activeElement?.id || '') : '';
    const unifiedBody = `${renderAssetAiSettingsBody()}<div class="board-settings-divider" aria-hidden="true"></div><a class="hw-btn" href="/static/pages/index.html?view=settings&section=directories" target="_top"><i data-lucide="folder-cog"></i>目录设置<i data-lucide="arrow-up-right"></i></a><div class="board-settings-divider" aria-hidden="true"></div>${renderAssetPreviewSettingsBody()}<div class="board-settings-divider" aria-hidden="true"></div>${renderAssetVideoSettingsBody()}`;
    overlay.innerHTML = `
        <div class="storage-settings-modal board-settings-modal" data-floating-content role="dialog" aria-modal="true" aria-labelledby="storageSettingsTitle" tabindex="-1">
            <div class="storage-settings-head">
                <div>
                    <strong id="storageSettingsTitle">资产设置</strong>
                    <span>统一管理 AI 分类、缩略图与视频。</span>
                </div>
                <button id="storageSettingsClose" type="button" data-storage-close aria-label="关闭"><i data-lucide="x"></i></button>
            </div>
            <div class="asset-pref-body board-settings-body">${unifiedBody}</div>
        </div>
    `;
    markAssetFloatingSurface(overlay, {surface:'asset-storage-settings', content:'.board-settings-modal', close:closeStorageSettings});
    refreshIcons();
    requestAnimationFrame(() => {
        if(!storageSettingsState.open) return;
        const focusTarget = previousFocusId ? document.getElementById(previousFocusId) : document.getElementById('storageSettingsClose');
        focusTarget?.focus();
    });
    if(storageSettingsState.focusSection) requestAnimationFrame(() => {
        overlay.querySelector(`[data-board-${storageSettingsState.focusSection}-settings]`)?.scrollIntoView({block:'start'});
        storageSettingsState.focusSection = '';
    });
}
function projectDirectoryTemplateSafePath(value){
    const raw = String(value || '').trim().replaceAll('\\', '/');
    if(!raw || raw.startsWith('/') || /^[A-Za-z]:\//.test(raw)) return '';
    const parts = raw.split('/');
    if(!parts.length || parts.length > 32) return '';
    for(const part of parts){
        if(!part || part !== part.trim() || part === '.' || part === '..' || part.length > 120 || part.endsWith(' ') || part.endsWith('.') || /[<>:"|?*\u0000]/.test(part)) return '';
        const stem = part.split('.', 1)[0].toLocaleLowerCase();
        const suffixIndex = part.lastIndexOf('.');
        const suffix = suffixIndex > 0 ? part.slice(suffixIndex).toLocaleLowerCase() : '';
        if(PROJECT_DIRECTORY_TEMPLATE_RESERVED_NAMES.has(stem) || PROJECT_DIRECTORY_TEMPLATE_FILE_SUFFIXES.has(suffix)) return '';
    }
    const normalized = parts.join('/');
    if(normalized.length > 500) return '';
    return normalized;
}
function projectDirectoryTemplatePaths(tree){
    const values = Array.isArray(tree) ? tree : (Array.isArray(tree?.folders) ? tree.folders : []);
    return [...new Set(values.map(projectDirectoryTemplateSafePath).filter(Boolean))];
}
function normalizeProjectDirectoryTemplate(value={}){
    const id = String(value.id || '').trim();
    if(!id) return null;
    const directoryTree = Array.isArray(value.directory_tree) ? value.directory_tree : (Array.isArray(value.folders) ? value.folders : []);
    const paths = projectDirectoryTemplatePaths(directoryTree);
    const projectType = PROJECT_DIRECTORY_TEMPLATE_TYPES.some(item => item.id === value.project_type) ? value.project_type : 'other';
    const defaultRevision = Number(value.default_revision);
    const mapping = value.slot_mapping && typeof value.slot_mapping === 'object' && !Array.isArray(value.slot_mapping)
        ? value.slot_mapping
        : {};
    return {
        id,
        name:String(value.name || '未命名目录模板').trim() || '未命名目录模板',
        project_type:projectType,
        version:Number(value.version || 0),
        default_revision:Number.isInteger(defaultRevision) && defaultRevision > 0 ? defaultRevision : null,
        is_default:Boolean(value.is_default),
        usage_count:Math.max(0, Number(value.usage_count || 0)),
        is_used:Boolean(value.is_used) || Number(value.usage_count || 0) > 0,
        is_archived:Boolean(value.is_archived),
        folder_count:Math.max(paths.length, Number(value.folder_count || 0)),
        directory_tree:paths,
        slot_mapping:mapping,
    };
}
function applyProjectDirectoryTemplates(payload={}){
    const templates = (Array.isArray(payload.templates) ? payload.templates : [])
        .map(normalizeProjectDirectoryTemplate)
        .filter(Boolean);
    projectDirectoryTemplateManagerState.templates = templates;
    if(Number.isFinite(Number(payload.revision))) projectDirectoryTemplateManagerState.revision = Number(payload.revision);
    if(projectDirectoryTemplateManagerState.selectedId && !templates.some(item => item.id === projectDirectoryTemplateManagerState.selectedId)){
        projectDirectoryTemplateManagerState.selectedId = '';
        projectDirectoryTemplateManagerState.editor = null;
    }
    if(!projectDirectoryTemplateManagerState.selectedId && templates.length){
        const preferred = templates.find(item => item.is_default && item.project_type === 'other') || templates.find(item => item.is_default) || templates[0];
        projectDirectoryTemplateManagerState.selectedId = preferred.id;
        projectDirectoryTemplateManagerState.editor = projectDirectoryTemplateDraft(preferred);
    }
}
function projectDirectoryTemplateDraftKey(draft={}){
    return JSON.stringify({
        name:String(draft.name || '').trim(),
        project_type:String(draft.project_type || 'other'),
        directory_tree_text:String(draft.directory_tree_text || '').replace(/\r\n/g, '\n').trim(),
        slot_mapping_text:String(draft.slot_mapping_text || '').replace(/\r\n/g, '\n').trim(),
    });
}
function projectDirectoryTemplateDraft(template=null, options={}){
    const paths = projectDirectoryTemplatePaths(template?.directory_tree || []);
    const copied = Boolean(options.copy);
    const created = Boolean(options.create);
    const sourceName = String(template?.name || '').trim();
    const draft = {
        id:copied || created ? '' : String(template?.id || ''),
        version:copied || created ? 0 : Number(template?.version || 0),
        name:created ? '新建目录模板' : (copied ? `${sourceName || '目录模板'} 副本` : (sourceName || '未命名目录模板')),
        project_type:PROJECT_DIRECTORY_TEMPLATE_TYPES.some(item => item.id === template?.project_type) ? template.project_type : 'other',
        is_default:Boolean(template?.is_default),
        is_used:copied || created ? false : Boolean(template?.is_used),
        usage_count:copied || created ? 0 : Math.max(0, Number(template?.usage_count || 0)),
        is_archived:copied || created ? false : Boolean(template?.is_archived),
        directory_tree_text:paths.join('\n'),
        slot_mapping_text:JSON.stringify(template?.slot_mapping && typeof template.slot_mapping === 'object' ? template.slot_mapping : {}, null, 2),
        dirty:copied || created,
        isNew:copied || created,
    };
    draft.baseline = projectDirectoryTemplateDraftKey(draft);
    return draft;
}
function projectDirectoryTemplateFallbackDraft(){
    const templates = projectDirectoryTemplateManagerState.templates || [];
    const generic = templates.find(item => item.project_type === 'other' && item.is_default)
        || templates.find(item => item.project_type === 'other')
        || templates.find(item => item.is_default)
        || templates[0]
        || {project_type:'other', directory_tree:['01_项目资料','02_资产','03_制作','04_交付','99_临时'], slot_mapping:{fallback:'99_临时'}, name:'通用制作'};
    return projectDirectoryTemplateDraft(generic, {copy:true});
}
function syncProjectDirectoryTemplateDraft(){
    const draft = projectDirectoryTemplateManagerState.editor;
    const overlay = document.getElementById('projectDirectoryTemplateSheetOverlay');
    if(!draft || !overlay) return draft;
    const name = overlay.querySelector('#projectDirectoryTemplateName');
    const projectType = overlay.querySelector('#projectDirectoryTemplateType');
    const directoryTree = overlay.querySelector('#projectDirectoryTemplateTree');
    const slotMapping = overlay.querySelector('#projectDirectoryTemplateMapping');
    if(name) draft.name = name.value;
    if(projectType) draft.project_type = projectType.value;
    if(directoryTree) draft.directory_tree_text = directoryTree.value;
    if(slotMapping) draft.slot_mapping_text = slotMapping.value;
    draft.dirty = draft.isNew || projectDirectoryTemplateDraftKey(draft) !== draft.baseline;
    return draft;
}
function parseProjectDirectoryTemplatePaths(value){
    const rawPaths = String(value || '').split(/\r?\n/).map(line => line.trim()).filter(Boolean);
    const paths = rawPaths.map(projectDirectoryTemplateSafePath);
    if(!rawPaths.length) throw new Error('至少保留一个相对目录');
    if(paths.some((path, index) => !path || path !== rawPaths[index].replaceAll('\\', '/'))){
        throw new Error('目录只能使用受控相对路径，不能包含盘符、绝对路径或路径穿越');
    }
    if(new Set(paths.map(path => path.toLocaleLowerCase())).size !== paths.length) throw new Error('目录树中存在重复目录');
    return paths;
}
function parseProjectDirectoryTemplateSlotMapping(value, paths){
    const raw = String(value || '').trim();
    if(!raw) return {};
    let mapping;
    try { mapping = JSON.parse(raw); }
    catch(_) { throw new Error('素材槽位映射必须是 JSON 对象'); }
    if(!mapping || typeof mapping !== 'object' || Array.isArray(mapping)) throw new Error('素材槽位映射必须是 JSON 对象');
    const available = new Set(paths);
    const normalizedSlots = new Set();
    Object.entries(mapping).forEach(([slot, path]) => {
        const normalizedSlot = String(slot || '').trim().toLocaleLowerCase();
        if(!/^[A-Za-z0-9_.:-]{1,80}$/.test(normalizedSlot) || normalizedSlots.has(normalizedSlot) || typeof path !== 'string' || !available.has(projectDirectoryTemplateSafePath(path))){
            throw new Error('素材槽位映射只能指向当前模板中的相对目录');
        }
        normalizedSlots.add(normalizedSlot);
    });
    return mapping;
}
function projectDirectoryTemplateTreeMarkup(paths=[]){
    const root = {children:Object.create(null)};
    paths.forEach(path => {
        let node = root;
        path.split('/').forEach(part => {
            node.children[part] ||= {children:Object.create(null)};
            node = node.children[part];
        });
    });
    const renderChildren = node => {
        const entries = Object.entries(node.children);
        return entries.length ? `<ul role="group">${entries.map(([name, child]) => `<li role="treeitem"><span>${escapeHtml(name)}</span>${renderChildren(child)}</li>`).join('')}</ul>` : '';
    };
    return paths.length ? `<div class="project-directory-template-manager-tree" role="tree" aria-readonly="true">${renderChildren(root)}</div>` : '<p class="project-directory-template-manager-empty">尚未声明目录。</p>';
}
async function loadProjectDirectoryTemplatesForManager(){
    if(!canManageProjectWorkspaceSettings()) return;
    setProjectDirectoryTemplateManagerState({...projectDirectoryTemplateManagerState, loading:true, error:''});
    renderProjectDirectoryTemplateManagerSheet();
    try {
        applyProjectDirectoryTemplates(await apiJsonResponse(assetManagerApi.getProjectDirectoryTemplates()));
    } catch(error) {
        setProjectDirectoryTemplateManagerState({...projectDirectoryTemplateManagerState, error:error.message || '目录模板读取失败'});
    } finally {
        setProjectDirectoryTemplateManagerState({...projectDirectoryTemplateManagerState, loading:false});
        renderProjectDirectoryTemplateManagerSheet();
    }
}
function chooseProjectDirectoryTemplate(templateId){
    const template = projectDirectoryTemplateManagerState.templates.find(item => item.id === String(templateId || ''));
    if(!template) return;
    setProjectDirectoryTemplateManagerState({...projectDirectoryTemplateManagerState, selectedId:template.id, editor:projectDirectoryTemplateDraft(template), pendingAction:null, archiveId:''});
    renderProjectDirectoryTemplateManagerSheet();
}
function runProjectDirectoryTemplatePendingAction(action){
    if(!action) return;
    if(action.kind === 'close') return closeProjectDirectoryTemplateManagerSheet(true);
    if(action.kind === 'select') return chooseProjectDirectoryTemplate(action.templateId);
    if(action.kind === 'new'){
        const draft = projectDirectoryTemplateFallbackDraft();
        setProjectDirectoryTemplateManagerState({...projectDirectoryTemplateManagerState, selectedId:'', editor:draft, pendingAction:null, archiveId:''});
        return renderProjectDirectoryTemplateManagerSheet();
    }
    if(action.kind === 'copy'){
        const source = projectDirectoryTemplateManagerState.templates.find(item => item.id === action.templateId);
        if(!source) return;
        setProjectDirectoryTemplateManagerState({...projectDirectoryTemplateManagerState, selectedId:'', editor:projectDirectoryTemplateDraft(source, {copy:true}), pendingAction:null, archiveId:''});
        return renderProjectDirectoryTemplateManagerSheet();
    }
}
function requestProjectDirectoryTemplatePendingAction(action){
    syncProjectDirectoryTemplateDraft();
    if(projectDirectoryTemplateManagerState.editor?.dirty){
        setProjectDirectoryTemplateManagerState({...projectDirectoryTemplateManagerState, pendingAction:action});
        renderProjectDirectoryTemplateManagerSheet();
        return;
    }
    runProjectDirectoryTemplatePendingAction(action);
}
function closeProjectDirectoryTemplateManagerSheet(force=false){
    if(!force){
        syncProjectDirectoryTemplateDraft();
        if(projectDirectoryTemplateManagerState.editor?.dirty){
            setProjectDirectoryTemplateManagerState({...projectDirectoryTemplateManagerState, pendingAction:{kind:'close'}});
            renderProjectDirectoryTemplateManagerSheet();
            return;
        }
    }
    document.getElementById('projectDirectoryTemplateSheetOverlay')?.remove();
    const returnFocus = projectDirectoryTemplateManagerState.returnFocus;
    setProjectDirectoryTemplateManagerState({...projectDirectoryTemplateManagerState, open:false, pendingAction:null, archiveId:'', returnFocus:null});
    if(returnFocus?.isConnected) requestAnimationFrame(() => returnFocus.focus());
}
function renderProjectDirectoryTemplateEditor(){
    const draft = projectDirectoryTemplateManagerState.editor;
    if(!draft) return '<div class="project-directory-template-manager-empty"><i data-lucide="folder-plus"></i><strong>选择一个目录模板</strong><span>可复制系统模板后再调整目录树。</span></div>';
    const isExisting = Boolean(draft.id);
    const isUsed = Boolean(draft.is_used);
    const isArchived = Boolean(draft.is_archived);
    const mutationDisabled = isUsed || isArchived || projectDirectoryTemplateManagerState.saving;
    const archiveConfirm = projectDirectoryTemplateManagerState.archiveId === draft.id;
    return `<div class="project-directory-template-editor" data-project-directory-template-editor>
        <div class="project-directory-template-editor-head"><div><h3>${isExisting ? '编辑目录模板' : '新建目录模板'}</h3><p>${isArchived ? '此模板已归档，不会用于新项目。可复制为新模板后再调整。' : (isUsed ? `已有 ${Number(draft.usage_count || 0)} 个项目使用此模板，不能再修改或设为默认。可复制后调整，或归档以停止后续使用。` : (isExisting ? '模板修改只影响之后新建的项目，不会修改历史工作区。' : '新模板默认复制通用制作模板的目录与槽位映射。'))}</p></div>${draft.dirty ? '<span class="project-directory-template-dirty">未保存</span>' : ''}</div>
        <div class="project-directory-template-editor-grid">
            <label><span>模板名称</span><input id="projectDirectoryTemplateName" maxlength="120" value="${escapeAttr(draft.name)}" autocomplete="off" ${mutationDisabled ? 'disabled' : ''}></label>
            <label><span>适用类型</span><select id="projectDirectoryTemplateType" ${mutationDisabled ? 'disabled' : ''}>${PROJECT_DIRECTORY_TEMPLATE_TYPES.map(item => `<option value="${item.id}" ${item.id === draft.project_type ? 'selected' : ''}>${item.label}</option>`).join('')}</select></label>
            <label class="project-directory-template-editor-wide"><span>目录树</span><textarea id="projectDirectoryTemplateTree" rows="11" spellcheck="false" aria-describedby="projectDirectoryTemplateTreeHint" ${mutationDisabled ? 'disabled' : ''}>${escapeHtml(draft.directory_tree_text)}</textarea><small id="projectDirectoryTemplateTreeHint">每行一个相对目录，例如：<code>03_资产/01_角色</code>。不能填写绝对路径或 <code>..</code>。</small></label>
            <label class="project-directory-template-editor-wide"><span>素材槽位映射（可选 JSON）</span><textarea id="projectDirectoryTemplateMapping" rows="7" spellcheck="false" ${mutationDisabled ? 'disabled' : ''}>${escapeHtml(draft.slot_mapping_text)}</textarea><small>每个映射值必须指向上方已声明目录。未映射素材由服务端落入受控 fallback 目录。</small></label>
        </div>
        <div class="project-directory-template-editor-preview"><header><strong>目录预览</strong><span>${projectDirectoryTemplatePaths(draft.directory_tree_text.split(/\r?\n/)).length} 个有效目录</span></header>${projectDirectoryTemplateTreeMarkup(projectDirectoryTemplatePaths(draft.directory_tree_text.split(/\r?\n/)))}</div>
        ${projectDirectoryTemplateManagerState.error ? `<div class="project-directory-template-manager-error" role="alert">${escapeHtml(projectDirectoryTemplateManagerState.error)}</div>` : ''}
        ${archiveConfirm ? `<div class="project-directory-template-archive-confirm" role="alert"><span>归档后，新项目不再可选择该模板；已有项目不会变化。</span><div><button class="hw-btn" type="button" data-project-directory-template-archive-cancel>取消</button><button class="hw-btn hw-btn-danger" type="button" data-project-directory-template-archive-confirm>确认归档</button></div></div>` : ''}
        <footer><div>${isExisting ? `<button class="hw-btn" type="button" data-project-directory-template-copy="${escapeAttr(draft.id)}"><i data-lucide="copy"></i><span>复制模板</span></button>${draft.is_default || isUsed || isArchived ? '' : `<button class="hw-btn" type="button" data-project-directory-template-default="${escapeAttr(draft.id)}" ${draft.dirty || projectDirectoryTemplateManagerState.saving ? 'disabled' : ''}><i data-lucide="star"></i><span>设为默认</span></button>`}${isArchived ? '' : `<button class="hw-btn hw-btn-danger" type="button" data-project-directory-template-archive="${escapeAttr(draft.id)}" ${draft.dirty || projectDirectoryTemplateManagerState.saving ? 'disabled' : ''}><i data-lucide="archive"></i><span>归档</span></button>`}` : ''}</div><button class="hw-btn hw-btn-gold" type="button" data-project-directory-template-save ${mutationDisabled ? 'disabled' : ''}><i data-lucide="${projectDirectoryTemplateManagerState.saving ? 'loader-2' : 'save'}"></i><span>${projectDirectoryTemplateManagerState.saving ? '正在保存…' : '保存模板'}</span></button></footer>
    </div>`;
}
function renderProjectDirectoryTemplateManagerSheet(){
    if(!projectDirectoryTemplateManagerState.open) return;
    let overlay = document.getElementById('projectDirectoryTemplateSheetOverlay');
    if(!overlay){
        overlay = document.createElement('div');
        overlay.id = 'projectDirectoryTemplateSheetOverlay';
        overlay.className = 'storage-settings-overlay project-directory-template-sheet-overlay';
        document.body.appendChild(overlay);
    }
    const activeId = String(overlay.contains(document.activeElement) ? document.activeElement?.id || '' : '');
    const templates = projectDirectoryTemplateManagerState.templates || [];
    const rows = projectDirectoryTemplateManagerState.loading
        ? '<div class="project-directory-template-manager-loading"><i data-lucide="loader-2"></i><span>正在读取目录模板…</span></div>'
        : (templates.length ? templates.map(template => `<button type="button" class="project-directory-template-list-row ${template.id === projectDirectoryTemplateManagerState.selectedId ? 'is-selected' : ''}" data-project-directory-template-select="${escapeAttr(template.id)}" aria-current="${template.id === projectDirectoryTemplateManagerState.selectedId ? 'true' : 'false'}"><span><strong>${escapeHtml(template.name)}</strong><small>${PROJECT_DIRECTORY_TEMPLATE_TYPES.find(item => item.id === template.project_type)?.label || '其他'} · ${Number(template.folder_count || 0)} 个目录${template.is_archived ? ' · 已归档' : ''}</small></span>${template.is_default ? '<em>默认</em>' : ''}</button>`).join('') : '<div class="project-directory-template-manager-empty"><strong>暂无可用模板</strong><span>可从通用制作模板创建一份。</span></div>');
    const pending = projectDirectoryTemplateManagerState.pendingAction;
    overlay.innerHTML = `<section class="project-directory-template-sheet" data-floating-content role="dialog" aria-modal="true" aria-labelledby="projectDirectoryTemplateSheetTitle">
        <header class="project-directory-template-sheet-head"><div><strong id="projectDirectoryTemplateSheetTitle">项目目录模板</strong><span>模板仅用于之后新建的项目，不展示或推断项目实际工作路径。</span></div><button type="button" data-project-directory-template-manager-close aria-label="关闭" ${projectDirectoryTemplateManagerState.saving ? 'disabled' : ''}><i data-lucide="x"></i></button></header>
        ${pending ? `<div class="project-directory-template-discard-confirm" role="alert"><span>当前模板有未保存修改。</span><div><button class="hw-btn" type="button" data-project-directory-template-discard-cancel>继续编辑</button><button class="hw-btn hw-btn-danger" type="button" data-project-directory-template-discard-confirm>放弃修改</button></div></div>` : ''}
        <div class="project-directory-template-sheet-body"><aside><div class="project-directory-template-list-head"><strong>模板库</strong><button class="hw-btn hw-btn-icon" type="button" data-project-directory-template-new aria-label="新建目录模板" title="新建目录模板"><i data-lucide="plus"></i></button></div><div class="project-directory-template-list">${rows}</div></aside><main>${renderProjectDirectoryTemplateEditor()}</main></div>
    </section>`;
    markAssetFloatingSurface(overlay, {surface:'asset-project-directory-template-manager', content:'.project-directory-template-sheet', close:closeProjectDirectoryTemplateManagerSheet, trigger:projectDirectoryTemplateManagerState.returnFocus, parent:'#storageSettingsOverlay'});
    refreshIcons();
    requestAnimationFrame(() => {
        if(!projectDirectoryTemplateManagerState.open) return;
        const focus = activeId ? overlay.querySelector(`#${CSS.escape(activeId)}`) : overlay.querySelector('[data-project-directory-template-manager-close]');
        focus?.focus();
    });
}
async function openProjectDirectoryTemplateManagerSheet(){
    if(!canManageProjectWorkspaceSettings()){
        setStatus('仅管理员可以管理项目工作区目录模板');
        return;
    }
    if(!projectDirectoryTemplateManagerState.open){
        setProjectDirectoryTemplateManagerState({...projectDirectoryTemplateManagerState, open:true, returnFocus:document.activeElement instanceof HTMLElement ? document.activeElement : null, error:'', pendingAction:null, archiveId:''});
    }
    await loadProjectDirectoryTemplatesForManager();
}
async function saveProjectDirectoryTemplate(){
    const draft = syncProjectDirectoryTemplateDraft();
    if(!draft || projectDirectoryTemplateManagerState.saving) return;
    const name = String(draft.name || '').trim();
    if(!name) throw new Error('请输入模板名称');
    const directoryTree = parseProjectDirectoryTemplatePaths(draft.directory_tree_text);
    const slotMapping = parseProjectDirectoryTemplateSlotMapping(draft.slot_mapping_text, directoryTree);
    const payload = {name, project_type:draft.project_type, directory_tree:directoryTree, slot_mapping:slotMapping};
    if(draft.id) payload.expected_version = Number(draft.version || 0);
    setProjectDirectoryTemplateManagerState({...projectDirectoryTemplateManagerState, saving:true, error:''});
    renderProjectDirectoryTemplateManagerSheet();
    try {
        const response = draft.id
            ? await apiJsonResponse(assetManagerApi.updateProjectDirectoryTemplate(draft.id, payload))
            : await apiJsonResponse(assetManagerApi.createProjectDirectoryTemplate(payload));
        const updated = normalizeProjectDirectoryTemplate(response.template || response);
        await loadProjectDirectoryTemplatesForManager();
        if(updated) chooseProjectDirectoryTemplate(updated.id);
        setStatus('项目目录模板已保存');
    } catch(error) {
        setProjectDirectoryTemplateManagerState({...projectDirectoryTemplateManagerState, error:error.message || '保存目录模板失败'});
        throw error;
    } finally {
        setProjectDirectoryTemplateManagerState({...projectDirectoryTemplateManagerState, saving:false});
        renderProjectDirectoryTemplateManagerSheet();
    }
}
async function setProjectDirectoryTemplateDefault(templateId){
    const template = projectDirectoryTemplateManagerState.templates.find(item => item.id === String(templateId || ''));
    if(!template || projectDirectoryTemplateManagerState.saving) return;
    setProjectDirectoryTemplateManagerState({...projectDirectoryTemplateManagerState, saving:true, error:''});
    renderProjectDirectoryTemplateManagerSheet();
    try {
        const expectedVersion = Number(template.version);
        if(!Number.isInteger(expectedVersion) || expectedVersion < 1) throw new Error('目录模板版本无效，请重新读取');
        const payload = {expected_version:expectedVersion};
        await apiJsonResponse(assetManagerApi.setDefaultProjectDirectoryTemplate(template.id, payload));
        await loadProjectDirectoryTemplatesForManager();
        chooseProjectDirectoryTemplate(template.id);
        setStatus('已更新默认目录模板');
    } catch(error) {
        setProjectDirectoryTemplateManagerState({...projectDirectoryTemplateManagerState, error:error.message || '更新默认模板失败'});
        throw error;
    } finally {
        setProjectDirectoryTemplateManagerState({...projectDirectoryTemplateManagerState, saving:false});
        renderProjectDirectoryTemplateManagerSheet();
    }
}
async function archiveProjectDirectoryTemplate(templateId){
    const template = projectDirectoryTemplateManagerState.templates.find(item => item.id === String(templateId || ''));
    if(!template || projectDirectoryTemplateManagerState.saving) return;
    setProjectDirectoryTemplateManagerState({...projectDirectoryTemplateManagerState, saving:true, error:''});
    renderProjectDirectoryTemplateManagerSheet();
    try {
        const expectedVersion = Number(template.version);
        const payload = Number.isFinite(expectedVersion) ? {expected_version:expectedVersion} : {};
        await apiJsonResponse(assetManagerApi.archiveProjectDirectoryTemplate(template.id, payload));
        setProjectDirectoryTemplateManagerState({...projectDirectoryTemplateManagerState, selectedId:'', editor:null, archiveId:''});
        await loadProjectDirectoryTemplatesForManager();
        setStatus('目录模板已归档；历史项目不会变化');
    } catch(error) {
        setProjectDirectoryTemplateManagerState({...projectDirectoryTemplateManagerState, error:error.message || '归档目录模板失败'});
        throw error;
    } finally {
        setProjectDirectoryTemplateManagerState({...projectDirectoryTemplateManagerState, saving:false});
        renderProjectDirectoryTemplateManagerSheet();
    }
}

function thumbnailGeneratorAssetIds(){
    const selected = registryActiveSelectedIds(assetBoardById(activeTab));
    const visible = activeRegistryVisibleAssets().map(item => item.id).filter(Boolean);
    return [...new Set(selected.length ? selected : visible)];
}
async function persistThumbnailStorageSettings(patch={}){
    const mode = thumbnailGeneratorState.mode === 'custom' ? 'custom' : 'source';
    const directory = String(document.getElementById('thumbnailDirectory')?.value || thumbnailGeneratorState.directory || '').trim();
    if(mode === 'custom' && !directory && patch.format === undefined) return null;
    const expectedVersion = Number(thumbnailGeneratorState.revision || 1);
    const data = await apiJsonResponse(assetManagerApi.updateThumbnailSettings({mode,directory,...patch,expected_version:expectedVersion}));
    applyThumbnailSettingsSnapshot(data, {revision:Number(data.revision || expectedVersion + 1)});
    if(data.cache_directory) setStatus(`缓存目录已就绪：${data.cache_directory}`);
    return data;
}
function thumbnailGeneratorRequest(){
    const ids = thumbnailGeneratorAssetIds();
    const mode = thumbnailGeneratorState.mode === 'custom' ? 'custom' : 'source';
    const directory = String(document.getElementById('thumbnailDirectory')?.value || thumbnailGeneratorState.directory || '').trim();
    const transcodeVideos = Boolean(document.getElementById('thumbnailTranscodeVideos')?.checked);
    if(mode === 'custom' && !directory){
        thumbnailGeneratorState.error = '请选择或输入缓存根目录';
        if(storageSettingsState.open) renderStorageSettingsModal();
        else renderThumbnailGeneratorModal();
        return null;
    }
    // 后端按已保存的设置落盘；界面上的目录还没确认切换时必须先问旧缓存去处。
    if(mode !== (thumbnailGeneratorState.savedMode || 'source')
        || (mode === 'custom' && pathCompareKey(directory) !== pathCompareKey(thumbnailGeneratorState.savedDirectory || ''))){
        openThumbnailCacheSwitchDialog(mode, directory);
        setStatus('缓存目录尚未切换，请先选择原缓存的处理方式');
        return null;
    }
    return {
        ids, mode, directory, transcodeVideos,
        payload:{asset_ids:ids, mode, directory, width:256, transcode_videos:transcodeVideos, replace_existing:true},
    };
}
function thumbnailGeneratorResultText(result={}, prefix='缩略图完成'){
    const videoText = `；MP4 新转码 ${Number(result.transcoded || 0)}，已有 ${Number(result.transcode_cached || 0)}，失败 ${Number(result.transcode_failed || 0)}`;
    return `${prefix}：新生成 ${Number(result.generated || 0)}，已有 ${Number(result.cached || 0)}，失败 ${Number(result.failed || 0)}，跳过 ${Number(result.skipped || 0)}${videoText}`;
}
function isThumbnailCacheBlockError(error){
    return THUMBNAIL_CACHE_BLOCK_CODES.includes(String(error?.code || ''));
}
// 缓存目录被拒时的提示块：说清「写哪个目录失败、为什么、可以怎么做」。
function thumbnailCacheBlock(error, payload, ids=[], transcodeVideos=false){
    if(!isThumbnailCacheBlockError(error)) return null;
    return {
        code:String(error.code || ''),
        message:String(error.message || '缓存目录无法写入'),
        directory:String(thumbnailGeneratorState.directory || ''),
        canGrant:canManageAllowedRoots(),
        payload:payload ? {...payload} : null,
        ids:Array.isArray(ids) ? [...ids] : [],
        transcodeVideos:Boolean(transcodeVideos),
    };
}
function renderThumbnailCacheBlock(){
    const block = thumbnailGeneratorState.cacheBlocked;
    if(!block) return '';
    const busy = thumbnailGeneratorState.grantingRoot;
    const directory = block.directory || '所选目录';
    const actions = block.canGrant
        ? `<div class="thumbnail-cache-block-actions"><button type="button" class="hw-btn hw-btn-gold" data-thumbnail-cache-grant ${busy ? 'disabled' : ''}>${busy ? '正在授权…' : '授权资源文件夹并重试'}</button><button type="button" class="hw-btn" data-thumbnail-cache-cancel ${busy ? 'disabled' : ''}>取消</button></div>`
        : `<small class="thumbnail-cache-block-hint">请联系管理员授权该资源文件夹后重试。</small>`;
    return `<div class="thumbnail-cache-block" data-thumbnail-cache-block><div class="thumbnail-cache-block-text"><strong>无法写入 ${escapeHtml(directory)}</strong><span>${escapeHtml(block.message)}</span></div>${actions}</div>`;
}
function renderThumbnailCacheHost(){
    if(thumbnailGeneratorState.open) renderThumbnailGeneratorModal();
    else renderStorageSettingsModal();
}
async function registerThumbnailCacheAllowedRoot(path){
    const snapshot = await apiJsonResponse(assetManagerHttp.request('/api/storage-settings/roots', {method:'GET', credentials:'same-origin'}));
    applyAllowedRootSettingsData(snapshot);
    const updated = await apiJsonResponse(assetManagerHttp.request('/api/storage-settings/roots', {
        method:'POST', credentials:'same-origin', headers:{'Content-Type':'application/json'},
        body:JSON.stringify({path, expected_version:Number(snapshot.revision || 1)}),
    }));
    applyAllowedRootSettingsData(updated);
    return updated;
}
// 管理员确认后：授权资源文件夹 → 用原请求参数自动重试一次（AC4）。
async function grantThumbnailCacheRootAndRetry(){
    const block = thumbnailGeneratorState.cacheBlocked;
    if(!block || thumbnailGeneratorState.grantingRoot) return;
    if(!block.directory || !block.payload){
        setThumbnailGeneratorState({...thumbnailGeneratorState, cacheBlocked:{...block, message:'缺少缓存目录或原始请求参数，请重新选择目录后生成'}});
        renderThumbnailCacheHost();
        return;
    }
    setThumbnailGeneratorState({...thumbnailGeneratorState, grantingRoot:true, error:''});
    renderThumbnailCacheHost();
    try {
        await registerThumbnailCacheAllowedRoot(block.directory);
    } catch(error) {
        // AC7：版本冲突只提示刷新，不静默重发覆盖他人改动。
        const conflict = error?.code === 'VERSION_CONFLICT';
        setThumbnailGeneratorState({
            ...thumbnailGeneratorState,
            grantingRoot:false,
            cacheBlocked:{...block, conflict, message:conflict ? '资源文件夹授权配置已被其他窗口修改，请刷新后重试（未自动重发）' : (error.message || '授权资源文件夹失败')},
        });
        renderThumbnailCacheHost();
        setStatus(thumbnailGeneratorState.cacheBlocked.message);
        return;
    }
    setThumbnailGeneratorState({...thumbnailGeneratorState, grantingRoot:false, cacheBlocked:null, generating:true});
    renderThumbnailCacheHost();
    setStatus(`已授权资源文件夹 ${block.directory}，正在按原参数重试生成…`);
    try {
        const data = await apiJsonResponse(assetManagerApi.generateThumbnails(block.payload));
        setThumbnailGeneratorState({...thumbnailGeneratorState, generating:false, open:false});
        document.getElementById('thumbnailGeneratorOverlay')?.remove();
        render();
        setStatus(thumbnailGeneratorResultText(block.transcodeVideos ? data : {...data, transcoded:0, transcode_cached:0, transcode_failed:0}));
    } catch(error) {
        const again = thumbnailCacheBlock(error, block.payload, block.ids, block.transcodeVideos);
        setThumbnailGeneratorState({...thumbnailGeneratorState, generating:false, error:error.message || '生成缩略图失败', cacheBlocked:again});
        renderThumbnailCacheHost();
        setStatus(error.message || '生成缩略图失败');
    }
}
// AC5：用户取消只收起提示，不发登记请求、不重试、不改写设置项。
function dismissThumbnailCacheBlock(){
    if(thumbnailGeneratorState.grantingRoot) return;
    setThumbnailGeneratorState({...thumbnailGeneratorState, cacheBlocked:null});
    renderThumbnailCacheHost();
}
function watchThumbnailBackgroundJob(taskId, failures=0){
    if(!taskId) return;
    window.setTimeout(async () => {
        try {
            const data = await apiJsonResponse(assetManagerApi.getThumbnailJob(taskId));
            const task = data.task || {};
            if(task.status === 'succeeded'){
                // 【修复问题1】后台缩略图完成不再全局失效媒体 URL，避免所有缩略图重新下载
                render();
                setStatus(thumbnailGeneratorResultText(task.result || {}, '后台缩略图完成'));
                return;
            }
            if(task.status === 'failed'){
                setStatus(`后台缩略图失败：${task.error || '任务执行失败'}`);
                return;
            }
            const progress = task.progress || {};
            setStatus(`后台生成中：${Number(progress.processed || 0)} / ${Number(progress.total || 0)} 项，可继续使用资产中心`);
            watchThumbnailBackgroundJob(taskId, 0);
        } catch(err){
            if(failures < 4){ watchThumbnailBackgroundJob(taskId, failures + 1); return; }
            setStatus('后台任务仍在执行，可前往任务中心查看');
        }
    }, failures ? 2400 : 1200);
}
function thumbnailKnownDirectories(){
    const rows = [];
    const labels = {upload:'上传素材', generated:'生成素材', local:'本地素材'};
    Object.entries(storageSettingsState.dirs || {}).forEach(([kind,path]) => {
        if(path) rows.push({name:labels[kind] || kind, path});
    });
    localLibraries.forEach(lib => { if(lib.path) rows.push({name:lib.name || '素材来源', path:lib.path}); });
    const seen = new Set();
    return rows.filter(item => {
        const key = pathCompareKey(item.path);
        if(!key || seen.has(key)) return false;
        seen.add(key);
        return true;
    });
}
async function openThumbnailGenerator(){
    setThumbnailGeneratorState({...thumbnailGeneratorState, open:true, loading:true, error:''});
    renderThumbnailGeneratorModal();
    try {
        // 提示块要判断管理员身份（是否展示「添加到允许根并重试」），先确保身份已就绪。
        if(!registryPrincipal && !registryPrincipalLoading) await loadRegistryPrincipal();
        const [settings, storage] = await Promise.all([
            apiJsonResponse(assetManagerApi.getThumbnailSettings()),
            apiJsonResponse(assetManagerApi.getStorageSettings()).catch(() => null),
        ]);
        if(storage) applyStorageSettingsData(storage);
        applyThumbnailSettingsSnapshot(settings, {loading:false});
    } catch(err){
        const missingApi = /not found/i.test(String(err.message || ''));
        setThumbnailGeneratorState({
            ...thumbnailGeneratorState,
            loading:false,
            error:missingApi ? '缩略图服务尚未加载，请重启工作台后刷新页面' : (err.message || '加载缩略图设置失败'),
        });
    }
    renderThumbnailGeneratorModal();
}
function closeThumbnailGenerator(){
    if(thumbnailGeneratorState.generating) return;
    thumbnailGeneratorState.open = false;
    document.getElementById('thumbnailGeneratorOverlay')?.remove();
}
function renderThumbnailGeneratorModal(){
    if(!thumbnailGeneratorState.open){ document.getElementById('thumbnailGeneratorOverlay')?.remove(); return; }
    let overlay = document.getElementById('thumbnailGeneratorOverlay');
    if(!overlay){
        overlay = document.createElement('div');
        overlay.id = 'thumbnailGeneratorOverlay';
        overlay.className = 'storage-settings-overlay';
        document.body.appendChild(overlay);
    }
    const ids = thumbnailGeneratorAssetIds();
    const selectedCount = registryActiveSelectedIds(assetBoardById(activeTab)).length;
    const known = thumbnailKnownDirectories();
    const mode = thumbnailGeneratorState.mode === 'custom' ? 'custom' : 'source';
    const busy = thumbnailGeneratorState.loading || thumbnailGeneratorState.generating || thumbnailGeneratorState.submittingBackground;
    overlay.innerHTML = `<div class="storage-settings-modal storage-settings-modal-compact thumbnail-generator-modal" data-floating-content role="dialog" aria-modal="true" aria-label="生成缩略图">
        <div class="storage-settings-head"><div><strong>生成缩略图</strong><span>预先生成缓存，提升大量素材的浏览速度。</span></div><button type="button" data-thumbnail-generator-close aria-label="关闭" ${busy ? 'disabled' : ''}><i data-lucide="x"></i></button></div>
        <div class="asset-pref-body">
            <div class="asset-pref-section">
                <div class="thumbnail-cache-notice"><i data-lucide="folder-plus"></i><div><strong>将写入 ${escapeHtml(thumbnailGeneratorState.cacheFolder)}</strong><span>${escapeHtml(thumbnailCacheTargetText())}；缓存文件不会进入素材索引。</span></div></div>
            </div>
            ${renderThumbnailFormatOptions()}
            <div class="asset-pref-section">
                <div class="asset-pref-title"><i data-lucide="hard-drive"></i><span>缓存存储位置</span></div>
                <div class="thumbnail-cache-options">
                    <button type="button" class="thumbnail-cache-option ${mode === 'source' ? 'is-active' : ''}" data-thumbnail-mode="source"><i data-lucide="folders"></i><span><strong>跟随素材源</strong><small>${escapeHtml(thumbnailGeneratorState.cacheFolder)} · 受管数据目录</small></span></button>
                    <button type="button" class="thumbnail-cache-option ${mode === 'custom' ? 'is-active' : ''}" data-thumbnail-mode="custom"><i data-lucide="folder-cog"></i><span><strong>指定缓存目录</strong><small>${escapeHtml(thumbnailGeneratorState.cacheFolder)} · 写入所选目录</small></span></button>
                </div>
                ${mode === 'custom' ? `<div class="thumbnail-cache-directory">
                    <label class="storage-dir-row"><span>选择资源文件夹</span><select id="thumbnailDirectoryPreset"><option value="">手工输入路径</option>${known.map(item => `<option value="${escapeAttr(item.path)}" ${pathCompareKey(item.path) === pathCompareKey(thumbnailGeneratorState.directory) ? 'selected' : ''}>${escapeHtml(item.name)} · ${escapeHtml(item.path)}</option>`).join('')}</select></label>
                    <label class="storage-dir-row"><span>缓存根目录</span><input id="thumbnailDirectory" value="${escapeAttr(thumbnailGeneratorState.directory)}" placeholder="例如 D:\\GodsCache"></label>
                </div>` : ''}
            </div>
            <div class="asset-pref-section thumbnail-cache-summary"><span><i data-lucide="images"></i>${selectedCount ? `已选择 ${selectedCount} 项` : `当前视图 ${ids.length} 项`}</span><small>图片、文档、3D、字体和视频将按支持能力生成预览。</small></div>
            <label class="thumbnail-video-transcode"><input id="thumbnailTranscodeVideos" type="checkbox" ${thumbnailGeneratorState.transcodeVideos ? 'checked' : ''}><span><strong>视频同时转码</strong><small>生成 H.264 MP4，与缩略图共用当前缓存目录。</small></span></label>
            ${thumbnailGeneratorState.error ? `<div class="thumbnail-cache-error">${escapeHtml(thumbnailGeneratorState.error)}</div>` : ''}
            ${renderThumbnailCacheBlock()}
        </div>
        <div class="thumbnail-cache-actions"><button class="hw-btn" type="button" data-thumbnail-generator-close ${busy ? 'disabled' : ''}>取消</button><button class="hw-btn thumbnail-background-action" type="button" data-thumbnail-generator-background title="关闭弹窗并在后台生成" ${busy || !ids.length ? 'disabled' : ''}><i data-lucide="${thumbnailGeneratorState.submittingBackground ? 'loader-2' : 'circle-play'}"></i><span>${thumbnailGeneratorState.submittingBackground ? '正在提交…' : '后台生成'}</span></button><button class="hw-btn hw-btn-gold" type="button" data-thumbnail-generator-run ${busy || !ids.length ? 'disabled' : ''}><i data-lucide="${thumbnailGeneratorState.generating ? 'loader-2' : 'images'}"></i><span>${thumbnailGeneratorState.generating ? '正在生成…' : `生成缩略图 ${ids.length}`}</span></button></div>
    </div>`;
    markAssetFloatingSurface(overlay, {surface:'asset-thumbnail-generator', content:'.thumbnail-generator-modal', close:closeThumbnailGenerator});
    refreshIcons();
}
async function generateAssetThumbnails(){
    const request = thumbnailGeneratorRequest();
    if(!request) return;
    const {ids, directory, transcodeVideos, payload} = request;
    setThumbnailGeneratorState({...thumbnailGeneratorState, directory, transcodeVideos, generating:true, error:''});
    renderThumbnailGeneratorModal();
    setStatus(`正在生成 ${ids.length} 项缩略图…`);
    try {
        const data = await apiJsonResponse(assetManagerApi.generateThumbnails(payload));
        // 【修复问题1】生成完成不再做全局 cache-bust：媒体 URL 版本已按资产 updated_at 粒度携带
        setThumbnailGeneratorState({...thumbnailGeneratorState, generating:false, open:false});
        document.getElementById('thumbnailGeneratorOverlay')?.remove();
        render();
        setStatus(thumbnailGeneratorResultText(transcodeVideos ? data : {...data, transcoded:0, transcode_cached:0, transcode_failed:0}));
    } catch(err){
        const blocked = thumbnailCacheBlock(err, payload, ids, transcodeVideos);
        setThumbnailGeneratorState({...thumbnailGeneratorState, generating:false, error:err.message || '生成缩略图失败', cacheBlocked:blocked});
        renderThumbnailGeneratorModal();
        setStatus(blocked ? `缓存目录无法写入：${blocked.directory || '所选目录'}` : thumbnailGeneratorState.error);
    }
}
async function generateAssetThumbnailsInBackground(){
    const request = thumbnailGeneratorRequest();
    if(!request) return;
    const {ids, directory, transcodeVideos, payload} = request;
    setThumbnailGeneratorState({...thumbnailGeneratorState, directory, transcodeVideos, submittingBackground:true, error:''});
    renderThumbnailGeneratorModal();
    try {
        const data = await apiJsonResponse(assetManagerApi.generateThumbnailsBackground(payload));
        const task = data.task || {};
        setThumbnailGeneratorState({...thumbnailGeneratorState, submittingBackground:false, open:false, backgroundTaskId:task.id || ''});
        document.getElementById('thumbnailGeneratorOverlay')?.remove();
        setStatus(`已转入后台生成 ${ids.length} 项，可继续使用资产中心`);
        watchThumbnailBackgroundJob(task.id || '');
    } catch(err){
        const blocked = thumbnailCacheBlock(err, payload, ids, transcodeVideos);
        setThumbnailGeneratorState({...thumbnailGeneratorState, submittingBackground:false, error:err.message || '后台任务提交失败', cacheBlocked:blocked});
        renderThumbnailGeneratorModal();
        setStatus(blocked ? `缓存目录无法写入：${blocked.directory || '所选目录'}` : thumbnailGeneratorState.error);
    }
}
// 【QIN-525】设置面板已收口为「全部缓存 / 删除缓存」两个全库按钮，按选中批量的入口
// 暂无 UI 挂载点；两个函数保留给后续右键 / 工具栏入口复用，文案不再出现旧按钮名。
async function generateBoardAssetThumbnails(background=false){
    const request = thumbnailGeneratorRequest();
    if(!request) return;
    const {ids, directory, transcodeVideos, payload} = request;
    setThumbnailGeneratorState({
        ...thumbnailGeneratorState,
        directory,
        transcodeVideos,
        generating:!background,
        submittingBackground:background,
        error:'',
    });
    renderStorageSettingsModal();
    try {
        const response = background
            ? assetManagerApi.generateThumbnailsBackground(payload)
            : assetManagerApi.generateThumbnails(payload);
        const data = await apiJsonResponse(response);
        setThumbnailGeneratorState({...thumbnailGeneratorState, generating:false, submittingBackground:false});
        if(background){
            const task = data.task || {};
            thumbnailGeneratorState.backgroundTaskId = task.id || '';
            setStatus(`已转入后台生成 ${ids.length} 项，可继续使用资产中心`);
            watchThumbnailBackgroundJob(task.id || '');
        } else {
            // 【修复问题1】生成完成同样不再全局 bump 缩略图版本
            render();
            setStatus(thumbnailGeneratorResultText(data, '缩略图已生成'));
        }
        renderStorageSettingsModal();
    } catch(err){
        const blocked = thumbnailCacheBlock(err, payload, ids, transcodeVideos);
        setThumbnailGeneratorState({...thumbnailGeneratorState, generating:false, submittingBackground:false, error:err.message || '缩略图生成失败', cacheBlocked:blocked});
        renderStorageSettingsModal();
        setStatus(blocked ? `缓存目录无法写入：${blocked.directory || '所选目录'}` : thumbnailGeneratorState.error);
    }
}
async function deleteBoardAssetThumbnails(){
    const ids = thumbnailGeneratorAssetIds();
    if(!ids.length || thumbnailGeneratorState.deleting) return;
    if(!window.confirm(`直接删除 ${ids.length} 项素材的预览缓存？删除后不会自动重建。`)) return;
    setThumbnailGeneratorState({...thumbnailGeneratorState, deleting:true, error:''});
    renderStorageSettingsModal();
    try {
        const data = await apiJsonResponse(assetManagerApi.deleteThumbnails({asset_ids:ids}));
        // 【修复问题1】显式删除缓存属于真实失效，保留会话级 bump（仅影响旧版 url 参数 URL）
        setThumbnailCacheVersion(Date.now());
        setThumbnailGeneratorState({...thumbnailGeneratorState, deleting:false});
        render();
        renderStorageSettingsModal();
        setStatus(`已删除 ${Number(data.removed || 0)} 个缓存，${ids.length} 项素材已停止自动重建`);
    } catch(err){
        setThumbnailGeneratorState({...thumbnailGeneratorState, deleting:false, error:err.message || '删除缓存失败'});
        renderStorageSettingsModal();
        setStatus(thumbnailGeneratorState.error);
    }
}
async function deleteSelectedStorageFiles(){
    const selected = storageSettingsState.items.filter(item => storageSettingsState.selected.has(item.id));
    if(!selected.length) return;
    if(!confirm(`确认删除 ${selected.length} 张图片？此操作会删除磁盘文件。`)) return;
    const data = await apiJsonResponse(assetManagerApi.deleteStorageFiles({
        kind:storageSettingsState.kind,
        items:selected.map(item => item.rel),
    }));
    storageSettingsState.selected.clear();
    await loadStorageFiles(storageSettingsState.kind);
    setStatus(`已删除 ${data.removed || 0} 个文件`);
}
function renderClassificationChips(item, limit=10, options={}){
    const chips = assetClassificationChips(item, limit);
    const kind = options.kind || '';
    const libAttr = options.libraryId ? ` data-asset-class-lib="${escapeAttr(options.libraryId)}"` : '';
    const activeKey = kind === 'localup' ? activeLocalUploadClassFilter : kind === 'asset' ? activeAssetClassFilter : '';
    return chips.length
        ? `<div class="classification-chips">${chips.map(chip => {
            const filterAttr = kind === 'localup'
                ? ` data-localup-class-filter="${escapeAttr(chip.key)}"`
                : kind === 'asset'
                    ? ` data-asset-class-filter="${escapeAttr(chip.key)}"${libAttr}`
                    : '';
            return `<button class="${activeKey === chip.key ? 'active' : ''}" type="button"${filterAttr} title="${escapeAttr(chip.label)}">${escapeHtml(chip.tag)}</button>`;
        }).join('')}</div>`
        : '';
}
function localCaptionProviders(){
    return (apiProviders || []).filter(p => p && p.enabled !== false && Array.isArray(p.chat_models) && p.chat_models.length);
}
function normalizeLocalCaptionSettings(){
    const providers = localCaptionProviders();
    const beforeProvider = localCaptionProvider;
    const beforeModel = localCaptionModel;
    if(!providers.length){
        setLocalCaptionProvider('');
        setLocalCaptionModel('');
        if(beforeProvider || beforeModel) writeLocalCaptionSettings();
        return;
    }
    let provider = providers.find(p => p.id === localCaptionProvider) || providers[0];
    setLocalCaptionProvider(provider.id || '');
    const models = (provider.chat_models || []).filter(Boolean);
    if(!models.includes(localCaptionModel)) setLocalCaptionModel(models[0] || '');
    if(beforeProvider !== localCaptionProvider || beforeModel !== localCaptionModel) writeLocalCaptionSettings();
}
function localCaptionModels(){
    normalizeLocalCaptionSettings();
    const provider = localCaptionProviders().find(p => p.id === localCaptionProvider);
    return (provider?.chat_models || []).filter(Boolean);
}
function renderLocalCaptionTools(imageCount){
    normalizeLocalCaptionSettings();
    const providers = localCaptionProviders();
    const models = localCaptionModels();
    const captionDisabled = !imageCount || !providers.length || !localCaptionModel || localCaptionBusy || localClassifyBusy;
    const classifyDisabled = !imageCount || !providers.length || !localCaptionModel || localCaptionBusy || localClassifyBusy;
    return `
        <div class="local-caption-tools">
            <div class="local-caption-main-row">
            <select id="localCaptionProvider" class="manage-select" title="平台" ${providers.length ? '' : 'disabled'}>
                ${providers.length ? providers.map(p => `<option value="${escapeAttr(p.id)}" ${p.id === localCaptionProvider ? 'selected' : ''}>${escapeHtml(p.name || p.id)}</option>`).join('') : '<option value="">暂无聊天平台</option>'}
            </select>
            <select id="localCaptionModel" class="manage-select" title="模型" ${models.length ? '' : 'disabled'}>
                ${models.length ? models.map(m => `<option value="${escapeAttr(m)}" ${m === localCaptionModel ? 'selected' : ''}>${escapeHtml(m)}</option>`).join('') : '<option value="">暂无模型</option>'}
            </select>
            <input id="localCaptionPrompt" class="local-caption-prompt-input" type="text" value="${escapeAttr(localCaptionPrompt || '描述图片')}" placeholder="描述图片">
            <button class="hw-btn" type="button" data-local-classify-run ${classifyDisabled ? 'disabled' : ''}><i data-lucide="${localClassifyBusy ? 'loader-2' : 'tags'}"></i><span>${localClassifyBusy ? '分类中' : '智能分类'}</span></button>
            <button class="hw-btn hw-btn-gold" type="button" data-local-caption-run ${captionDisabled ? 'disabled' : ''}><i data-lucide="${localCaptionBusy ? 'loader-2' : 'wand-sparkles'}"></i><span>${localCaptionBusy ? '反推中' : '提示词反推'}</span></button>
            <button class="hw-btn hw-btn-icon" type="button" data-local-classify-toggle title="智能分类要求"><i data-lucide="${localClassifyPromptOpen ? 'chevron-up' : 'sliders-horizontal'}"></i></button>
            </div>
            ${localClassifyPromptOpen ? `<textarea id="localClassifyPrompt" class="local-classify-prompt-input" placeholder="默认已内置室内/室外、空间、主体、光影、模特、风格、色彩等分类。这里可以追加你的分类要求，例如：增加家装风格、镜头焦段、商业用途、人物年龄段、产品材质。">${escapeHtml(localClassifyPrompt || '')}</textarea>` : ''}
        </div>
    `;
}
async function saveLocalLibrarySettings(){
    const data = await apiJsonResponse(assetManagerApi.updateStorageSettings({
        dirs:storageSettingsState.dirs || {},
        extra_local_entries:storageExtraLocalEntries().filter(entry => entry.path),
        local_library_names:storageLocalLibraryNames(),
        local_library_order:localLibraries.map(lib => lib.id).filter(Boolean),
        expected_version:Number(storageSettingsState.revision || 1),
    }));
    applyStorageSettingsData(data || {});
    return data;
}
function providerAvatarPlatform(p){
    const proto = String(p?.protocol || '').toLowerCase();
    const base = String(p?.base_url || '').toLowerCase();
    if(proto === 'apimart' || base.includes('apimart.ai')) return 'apimart';
    if(proto === 'volcengine') return 'volcengine';
    return '';
}
function providerAvatarSupported(p){
    return AVATAR_SUPPORTED_PLATFORMS.includes(providerAvatarPlatform(p));
}
function avatarPlatformLabel(platform){
    return AVATAR_PLATFORM_LABELS[String(platform || '')] || String(platform || '平台');
}
// 列出 API 设置里所有启用的 provider 作为认证候选（以 API 设置为中心，由用户自己选平台）；
// 不支持的平台也列出，在下拉里标注「待接入」，避免用户以为漏了。
function avatarCandidateProviders(){
    return (apiProviders || []).filter(p => p && p.enabled !== false);
}
function activeAvatarProvider(){
    const list = avatarCandidateProviders();
    if(!list.length) return null;
    return list.find(p => p.id === avatarRegisterProvider)
        || list.find(p => providerAvatarSupported(p))
        || list[0];
}
function avatarProviderOptionLabel(p){
    const name = p.name || p.id;
    const platform = providerAvatarPlatform(p);
    if(!platform) return `${name}（暂不支持，待接入）`;
    if(!providerAvatarSupported(p)) return `${name}（${avatarPlatformLabel(platform)}·待接入）`;
    return `${name}（${avatarPlatformLabel(platform)}）`;
}
// 找出某平台当前可用的 provider_id（优先注册时记录的，其次同平台任一启用 provider）
function avatarProviderIdForPlatform(platform, preferredId=''){
    const list = avatarCandidateProviders();
    if(preferredId && list.some(p => p.id === preferredId)) return preferredId;
    const match = list.find(p => providerAvatarPlatform(p) === platform);
    return match ? match.id : '';
}
function saveRegistryViewSettings(){
    try {
        localStorage.setItem(ASSET_REGISTRY_VIEW_SETTINGS_KEY, JSON.stringify({
            cardSize:registryCardSize,
            size:registryCardSize <= 140 ? 'small' : registryCardSize >= 220 ? 'large' : 'medium',
            aspect:registryAspect,
            simple:registrySimple,
            layout:registryLayout,
            groupMode:registryGroupMode,
        }));
    } catch(_) {}
}
function saveRegistryCustomGroups(){
    Object.keys(registryCustomGroups).forEach(boardKey => {
        registryCustomGroups[boardKey] = (registryCustomGroups[boardKey] || []).map(group => ({
            ...group,
            assetIds:[...new Set((group.assetIds || []).map(String).filter(Boolean))],
        }));
    });
    try { localStorage.setItem(ASSET_REGISTRY_CUSTOM_GROUPS_KEY, JSON.stringify(registryCustomGroups)); }
    catch(_) {}
}
async function saveRegistryPreset(){
    const name = window.prompt('筛选预设名称', '新筛选预设');
    if(!String(name || '').trim()) return;
    const data = await apiJsonResponse(assetManagerApi.createRegistryPreset({name:String(name).trim(), definition:registryFilterDefinition()}));
    if(data.preset) setRegistryPresets([data.preset, ...registryPresets.filter(item => item.id !== data.preset.id)]);
    render();
    setStatus('筛选预设已保存');
}
async function loadAssetRackDiskFolders(rootId, relPath=''){
    await loadAssetAllowedRootPaths();
    const id = String(rootId || '');
    if(!id) return [];
    if(id === '__automatic__'){
        // 自动归档没有单一物理根：对池内出现过的每个素材根分别浏览（404/无权即缓存空，自然降级）
        const rootIds = [...new Set(assetRackAutomaticItems().map(item => String(item.storage_root_id || '')).filter(Boolean))];
        const groups = await Promise.all(rootIds.map(child => loadAssetRackDiskFolders(child, relPath)));
        return groups.flat();
    }
    const rootPath = assetRackRootDiskPath(id);
    if(!rootPath) return [];
    const key = `${id}::${relPath}`;
    if(registryDiskFolderLoading.has(key)) return registryDiskFolderLoading.get(key);
    if(registryDiskFolderCache.has(key)) return registryDiskFolderCache.get(key) || [];
    const promise = (async () => {
        try {
            const base = rootPath.replace(/\\/g, '/').replace(/\/+$/g, '');
            const target = relPath ? `${base}/${relPath}` : base;
            const response = await fetch(`/api/storage-settings/browse?path=${encodeURIComponent(target)}`, {credentials:'same-origin'});
            const data = await response.json().catch(() => ({}));
            if(!response.ok){
                // QIN-542 AC4：非 admin 浏览磁盘目录被拒（403）时显式登记，不再静默
                if(response.status === 403){
                    setRegistryDiskFolderPermissionDenied(true);
                }
                registryDiskFolderCache.set(key, []);
                return [];
            }
            const entries = (Array.isArray(data?.directories) ? data.directories : []).map(item => {
                const abs = String(item?.path || '').replace(/\\/g, '/').replace(/\/+$/g, '');
                const rel = abs.startsWith(`${base}/`) ? abs.slice(base.length + 1) : String(item?.name || '');
                return {name: String(item?.name || rel), rel: rel.replace(/^\/+|\/+$/g, '')};
            }).filter(item => item.rel);
            registryDiskFolderCache.set(key, entries);
            return entries;
        } catch(_) {
            registryDiskFolderCache.set(key, []);
            return [];
        }
    })();
    registryDiskFolderLoading.set(key, promise);
    void promise.finally(() => registryDiskFolderLoading.delete(key));
    return promise;
}
function assetRackFolderKeys(){
    const keys = [];
    assetRackRoots().forEach(root => {
        keys.push(`${root.id}::${ASSET_RACK_ROOT_KEY}`);
        customBoardFolderParentPaths(assetRackFolderTree(root.id)).forEach(path => keys.push(`${root.id}::${path}`));
    });
    return keys;
}
async function loadAssetRackRegisteredFolders(force=false){
    if(registryRegisteredFoldersLoading) return;
    if(registryRegisteredFoldersLoaded && !force) return;
    registryRegisteredFoldersLoading = true;
    try {
        const data = await apiJsonResponse(assetManagerApi.getRegistryFolders(''));
        setRegistryRegisteredFolders(Array.isArray(data?.folders) ? data.folders : []);
        setRegistryIndexedFolders(Array.isArray(data?.indexed_folders) ? data.indexed_folders : []);
        setRegistryRegisteredFoldersLoaded(true);
    } catch(_) {
        setRegistryRegisteredFoldersLoaded(true);
    } finally {
        registryRegisteredFoldersLoading = false;
    }
    // 总览态同样合并磁盘真实子目录（含空目录）；QIN-542 AC4：403 时提示条需一次强制重绘
    const rootIds = assetRackRoots().map(root => String(root.id || '')).filter(Boolean);
    const diskResults = await Promise.all(rootIds.map(rootId => loadAssetRackDiskFolders(rootId)));
    if(registryDiskFolderPermissionDenied && !registryDiskFolderPermissionNoticed){
        setRegistryDiskFolderPermissionNoticed(true);
        if(activeTab === 'registry' || assetRackBoard()) render();
        return;
    }
    if((activeTab === 'registry' || assetRackBoard()) && (registryIndexedFolders.length || diskResults.some(entries => entries.length))) render();
}
let assetRackDirectoryIndexPromise = null;
function resetAssetRackDirectoryIndex(){
    assetRackDirectoryIndexPromise = null;
    setRegistryRegisteredFoldersLoaded(false);
}
async function ensureAssetRackDirectoryIndex(){
    // 文件夹摘要由后端聚合，避免为了目录树自动拉取整库素材。
    if(assetRackDirectoryIndexPromise) return assetRackDirectoryIndexPromise;
    const request = loadAssetRackRegisteredFolders();
    assetRackDirectoryIndexPromise = request;
    await request;
    return request;
}
function setAssetRackCollapsed(collapsed){
    gwSetAssetRackCollapsed(Boolean(collapsed));
    saveAssetRackCollapsed(assetRackCollapsed);
    render();
}
function toggleAssetRackFolder(key){
    const value = String(key || '');
    if(!value) return;
    if(assetRackExpandedFolders.has(value)){
        assetRackExpandedFolders.delete(value);
    } else {
        assetRackExpandedFolders.add(value);
        // 展开时懒加载该层级的磁盘子目录（含空目录）；有新增才重绘
        const separator = value.indexOf('::');
        const rootId = separator > 0 ? value.slice(0, separator) : '';
        const path = separator > 0 ? value.slice(separator + 2) : '';
        if(path){
            // QIN-542 AC1/AC2：加载完成即重绘（无论有无子目录），呈现深层子目录或「空」标记
            void loadAssetRackDiskFolders(rootId, path).then(() => {
                if(assetRackExpandedFolders.has(value)) render();
            });
        }
    }
    saveAssetRackExpandedFolders();
    render();
}
function toggleAssetRackFoldersAll(){
    const keys = assetRackFolderKeys();
    if(!keys.length) return;
    const allExpanded = keys.every(key => assetRackExpandedFolders.has(key));
    keys.forEach(key => { if(allExpanded) assetRackExpandedFolders.delete(key); else assetRackExpandedFolders.add(key); });
    saveAssetRackExpandedFolders();
    render();
}
function selectAssetRackFolder(rootId, path, { force=false }={}){
    const root = String(rootId || '');
    const folder = path === '__root__' ? '__root__' : String(path || '').replace(/\\/g, '/').replace(/^\/+|\/+$/g, '');
    const board = assetRackBoard();
    if(board){
        if(!registryRackSnapshot) setRegistryRackSnapshot(registryAssets.slice());
        const currentRoot = String(boardSourceSelection[board.id] || '');
        const currentFolder = String(boardFolderSelection[board.id] || '');
        const same = currentRoot === root && currentFolder === folder;
        if(force && same){
            // 幂等模式：保持选中，不 toggle 清空
        } else {
            boardSourceSelection[board.id] = same ? '' : root;
            boardFolderSelection[board.id] = same ? '' : folder;
        }
        if(!board.sourceBoard) setRegistryRootId('');
        if(!same && folder && folder !== '__root__'){
            const segments = folder.split('/').filter(Boolean);
            segments.slice(0, -1).forEach((_segment, index) => assetRackExpandedFolders.add(`${root}::${segments.slice(0, index + 1).join('/')}`));
            saveAssetRackExpandedFolders();
        }
        savedScrollPositions.registry = 0;
        refreshRegistryAssets(true, {reuseCache:true}).catch(err => setStatus(err.message || '加载目录失败'));
        return;
    }
    const same = registryNavFolder.rootId === root && registryNavFolder.path === folder;
    if(force && same){
        // 幂等模式：保持选中，不 toggle 清空
    } else {
        setRegistryNavFolder(same ? {rootId:'', path:''} : {rootId:root, path:folder});
    }
    if(!same && folder){
        // 选中子目录时逐级展开父链，避免节点被折叠隐藏
        const segments = folder.split('/').filter(Boolean);
        segments.slice(0, -1).forEach((_segment, index) => assetRackExpandedFolders.add(`${root}::${segments.slice(0, index + 1).join('/')}`));
        saveAssetRackExpandedFolders();
    }
    savedScrollPositions.registry = 0;
    refreshRegistryAssets(true, {reuseCache:true}).catch(err => setStatus(err.message || '加载目录失败'));
}
function clearAssetRackFolder(){
    const board = assetRackBoard();
    if(board){
        if(!boardSourceSelection[board.id] && !boardFolderSelection[board.id]) return;
        boardSourceSelection[board.id] = '';
        boardFolderSelection[board.id] = '';
        if(!board.sourceBoard) setRegistryRootId('');
        setRegistryRackSnapshot(null);
        savedScrollPositions.registry = 0;
        refreshRegistryAssets(true, {reuseCache:true}).catch(err => setStatus(err.message || '加载目录失败'));
        return;
    }
    if(!registryNavFolderActive()) return;
    setRegistryNavFolder({rootId:'', path:''});
    savedScrollPositions.registry = 0;
    refreshRegistryAssets(true, {reuseCache:true}).catch(err => setStatus(err.message || '加载目录失败'));
}
async function createAssetRackFolder(rootId){
    const root = String(rootId || '').trim();
    if(!root){ setStatus('请先选择素材库根目录'); return; }
    const name = String(window.prompt('新建文件夹名称', '新文件夹') || '').trim().slice(0, 120);
    if(!name) return;
    try {
        // 登记按 parent_id === root_id 归属根目录，统一在根层登记，保证刷新后仍可归属展示
        await apiJsonResponse(assetManagerApi.createRegistryFolder({root_id:root, parent:root, name}));
        await loadAssetRackRegisteredFolders(true);
        assetRackExpandedFolders.add(`${root}::${ASSET_RACK_ROOT_KEY}`);
        assetRackExpandedFolders.add(`${root}::${name}`);
        saveAssetRackExpandedFolders();
        render();
        setStatus(`已登记文件夹“${name}”`);
    } catch(err){
        setStatus(err.message || '新建文件夹失败');
    }
}
function renderAvatarRegistrationCard(item, platform, reg, busy){
    const status = String(reg.status || '');
    const tag = `<span class="avatar-platform-tag">${escapeHtml(avatarPlatformLabel(platform))}</span>`;
    const providerId = avatarProviderIdForPlatform(platform, reg.provider_id || '');
    const provAttr = `data-avatar-prov="${escapeAttr(providerId)}"`;
    if(status === 'Active' && reg.asset_uri){
        return `<div class="avatar-card registered">
            <div class="avatar-head"><i data-lucide="badge-check"></i><span>已认证可用</span>${tag}</div>
            <div class="avatar-uri" title="只能在 ${escapeAttr(avatarPlatformLabel(platform))} 平台的视频生成中通过 @ 调用">${escapeHtml(reg.asset_uri)}</div>
            <div class="asset-tools">
                <button class="hw-btn" type="button" data-avatar-copy="${escapeAttr(reg.asset_uri)}"><i data-lucide="copy"></i><span>复制 asset:// 地址</span></button>
                <button class="hw-btn" type="button" data-avatar-register="${escapeAttr(item.id)}" ${provAttr} ${busy ? 'disabled' : ''}><i data-lucide="refresh-cw"></i><span>${busy ? '处理中…' : '重新注册'}</span></button>
            </div>
        </div>`;
    }
    if(status === 'Processing'){
        return `<div class="avatar-card processing">
            <div class="avatar-head"><i data-lucide="loader"></i><span>审核中</span>${tag}</div>
            <div class="avatar-hint">已提交到 ${escapeHtml(avatarPlatformLabel(platform))} 审核（任务 ${escapeHtml(reg.task_id || '')}），通过后会自动生成 asset:// 地址。审核通常需要几十秒到几分钟。</div>
            <div class="asset-tools">
                <button class="hw-btn hw-btn-gold" type="button" data-avatar-check="${escapeAttr(item.id)}" ${provAttr} ${busy ? 'disabled' : ''}><i data-lucide="refresh-cw"></i><span>${busy ? '查询中…' : '刷新审核状态'}</span></button>
            </div>
        </div>`;
    }
    return `<div class="avatar-card failed">
        <div class="avatar-head"><i data-lucide="x-circle"></i><span>审核未通过</span>${tag}</div>
        <div class="avatar-hint warn">${escapeHtml(reg.detail || '审核未通过，请更换素材后重试。')}</div>
        <div class="asset-tools">
            <button class="hw-btn" type="button" data-avatar-register="${escapeAttr(item.id)}" ${provAttr} ${busy ? 'disabled' : ''}><i data-lucide="refresh-cw"></i><span>${busy ? '处理中…' : '重新提交'}</span></button>
        </div>
    </div>`;
}
function renderAvatarSection(item){
    const busy = avatarBusyId === item.id;
    const regs = (item.registrations && typeof item.registrations === 'object') ? item.registrations : {};
    const cards = Object.keys(regs)
        .filter(platform => regs[platform] && regs[platform].task_id)
        .map(platform => renderAvatarRegistrationCard(item, platform, regs[platform], busy))
        .join('');
    const providers = avatarCandidateProviders();
    if(!providers.length){
        return `<div class="avatar-section">
            ${cards}
            <div class="avatar-head"><i data-lucide="user-round-cog"></i><span>注册为真人/数字人</span></div>
            <div class="avatar-hint">未检测到可用平台。请先在「API 平台管理」中添加并启用 API 平台（如 APIMart）并填写 Key。</div>
        </div>`;
    }
    const selected = activeAvatarProvider();
    const selPlatform = providerAvatarPlatform(selected);
    const supported = providerAvatarSupported(selected);
    const noKey = selected && selected.has_key === false;
    const alreadyRegistered = supported && regs[selPlatform] && regs[selPlatform].task_id;
    const select = `<select class="avatar-provider-select" data-avatar-provider>${providers.map(p => `<option value="${escapeAttr(p.id)}" ${p.id === selected?.id ? 'selected' : ''}>${escapeHtml(avatarProviderOptionLabel(p))}</option>`).join('')}</select>`;
    let registerUI;
    if(!supported){
        registerUI = `<div class="avatar-hint">认证是跨平台功能，但「${escapeHtml(selPlatform ? avatarPlatformLabel(selPlatform) : (selected?.name || selected?.id || '该平台'))}」的资产认证 API 尚未接入（待接入）。请选择已支持的平台，或继续使用官方控制台认证。</div>${select}`;
    } else {
        registerUI = `
            <div class="avatar-hint">提交到 ${escapeHtml(avatarPlatformLabel(selPlatform))} 私域素材审核，通过后生成 asset:// 地址，可在该平台的视频生成中通过 @ 直接调用（一个素材可注册到多个平台，平台间互相隔离）。</div>
            ${select}
            ${noKey ? '<div class="avatar-hint warn">该平台尚未配置 API Key。</div>' : ''}
            ${alreadyRegistered ? '<div class="avatar-hint">该平台已注册，再次提交会覆盖该平台的认证。</div>' : ''}
            <button class="hw-btn hw-btn-gold" type="button" data-avatar-register="${escapeAttr(item.id)}" data-avatar-prov="${escapeAttr(selected?.id || '')}" ${busy || noKey ? 'disabled' : ''}><i data-lucide="user-round-plus"></i><span>${busy ? '注册中，请稍候…' : (alreadyRegistered ? '重新注册到该平台' : '注册并等待审核')}</span></button>`;
    }
    return `<div class="avatar-section">
        ${cards}
        <div class="avatar-head"><i data-lucide="user-round-cog"></i><span>注册到平台</span></div>
        ${registerUI}
    </div>`;
}
async function runLocalUploadCaptionSelected(){
    const images = selectedLocalUploadImageItems();
    if(!images.length || localCaptionBusy) return;
    normalizeLocalCaptionSettings();
    if(!localCaptionProvider || !localCaptionModel){
        setStatus('请先在”API 设置”配置平台，再到”资产设置 → AI 分类设置”选择模型');
        return;
    }
    setLocalCaptionBusy(true);
    render();
    setStatus(`正在反推 ${images.length} 张本地图片的提示词...`);
    try {
        const data = await apiJsonResponse(assetManagerApi.captionLocalAssets({
                kind:activeLocalLibraryKind(),
                names:images.map(item => item.file || item.id),
                provider:localCaptionProvider,
                model:localCaptionModel,
                prompt:(localCaptionPrompt || '描述图片').trim() || '描述图片'
        }));
        await loadLocalAssets();
        selectedLocalUploadIds.clear();
        if(images[0]?.id) setSelectedLocalUploadId(images[0].id);
        render();
        const failed = (data.items || []).filter(item => !item.ok);
        setStatus(failed.length ? `已完成 ${data.count || 0} 张，${failed.length} 张失败：${failed[0].error || '反推失败'}` : `已反推并保存 ${data.count || images.length} 张图片提示词`);
    } catch(err) {
        setStatus(err.message || '提示词反推失败');
    } finally {
        setLocalCaptionBusy(false);
        render();
    }
}
async function runLocalUploadCaptionOne(id){
    const item = findLocalUpload(id);
    if(!item || assetKind(item) !== 'image' || localCaptionBusy || localClassifyBusy) return;
    normalizeLocalCaptionSettings();
    if(!localCaptionProvider || !localCaptionModel){
        setStatus('请先在”API 设置”配置平台，再到”资产设置 → AI 分类设置”选择模型');
        return;
    }
    setLocalCaptionBusy(true);
    setSelectedLocalUploadId(item.id);
    render();
    setStatus(`正在反推「${item.name || '本地图片'}」的提示词...`);
    try {
        const data = await apiJsonResponse(assetManagerApi.captionLocalAssets({
                kind:activeLocalLibraryKind(),
                names:[item.file || item.id],
                provider:localCaptionProvider,
                model:localCaptionModel,
                prompt:(localCaptionPrompt || '描述图片').trim() || '描述图片'
        }));
        const result = (data.items || [])[0] || null;
        if(result && !result.ok) throw new Error(result.error || '反推失败');
        await loadLocalAssets();
        setSelectedLocalUploadId(item.id);
        render();
        setStatus('已反推并保存当前图片提示词');
    } catch(err) {
        setStatus(err.message || '提示词反推失败');
    } finally {
        setLocalCaptionBusy(false);
        render();
    }
}
async function copyLocalUploadCaption(id){
    const item = findLocalUpload(id);
    if(!item || assetKind(item) !== 'image') return;
    const textarea = document.getElementById('localUploadCaptionEdit');
    const text = textarea ? textarea.value : (item.caption || '');
    if(!String(text || '').trim()){
        setStatus('当前图片暂无可复制的提示词');
        return;
    }
    const ok = await copyTextToClipboard(text);
    setStatus(ok ? '已复制提示词' : '复制失败，请手动复制');
}
async function runLocalUploadClassifySelected(){
    const images = selectedLocalUploadImageItems();
    if(!images.length || localClassifyBusy) return;
    normalizeLocalCaptionSettings();
    if(!localCaptionProvider || !localCaptionModel){
        setStatus('请先在”API 设置”配置平台，再到”资产设置 → AI 分类设置”选择模型');
        return;
    }
    setLocalClassifyBusy(true);
    render();
    setStatus(`正在智能分类 ${images.length} 张本地图片...`);
    try {
        const data = await apiJsonResponse(assetManagerApi.classifyLocalAssets({
                kind:activeLocalLibraryKind(),
                names:images.map(item => item.file || item.id),
                provider:localCaptionProvider,
                model:localCaptionModel,
                prompt:(localClassifyPrompt || '').trim()
        }));
        await loadLocalAssets();
        if(images[0]?.id) setSelectedLocalUploadId(images[0].id);
        render();
        const failed = (data.items || []).filter(item => !item.ok);
        setStatus(failed.length ? `处理完成 ${data.count || 0} 张，${failed.length} 张失败：${failed[0].error || '分类失败'}` : `处理完成 ${data.count || images.length} 张图片`);
    } catch(err) {
        setStatus(err.message || '智能分类失败');
    } finally {
        setLocalClassifyBusy(false);
        render();
    }
}
async function runAssetClassifySelected(){
    const images = selectedAssetImageItems();
    if(!images.length || assetClassifyBusy) return;
    normalizeLocalCaptionSettings();
    if(!localCaptionProvider || !localCaptionModel){
        setStatus('请先在”API 设置”配置平台，再到”资产设置 → AI 分类设置”选择模型');
        return;
    }
    setAssetClassifyBusy(true);
    render();
    setStatus(`正在智能分类 ${images.length} 张资产图片...`);
    try {
        const data = await apiJsonResponse(assetManagerApi.classifyAssetLibraryItems({
                library_id:activeAssetLibraryId,
                ids:images.map(item => item.id),
                provider:localCaptionProvider,
                model:localCaptionModel,
                prompt:(localClassifyPrompt || '').trim()
        }));
        setAssetLibrary(data.library || assetLibrary);
        if(images[0]?.id) setSelectedAssetId(images[0].id);
        render();
        const failed = (data.items || []).filter(item => !item.ok);
        setStatus(failed.length ? `处理完成 ${data.count || 0} 张，${failed.length} 张失败：${failed[0].error || '分类失败'}` : `处理完成 ${data.count || images.length} 张资产图片`);
    } catch(err) {
        setStatus(err.message || '智能分类失败');
    } finally {
        setAssetClassifyBusy(false);
        render();
    }
}
async function saveLocalUploadCaption(id){
    const item = findLocalUpload(id);
    if(!item || assetKind(item) !== 'image') return;
    const textarea = document.getElementById('localUploadCaptionEdit');
    const caption = textarea ? textarea.value : (item.caption || '');
    try {
        const data = await apiJsonResponse(assetManagerApi.saveLocalAssetCaption({kind:activeLocalLibraryKind(), name:item.file || item.id, caption}));
        item.caption = data.caption || '';
        item.caption_file = data.caption_file || item.caption_file || '';
        render();
        setStatus('已保存反推提示词');
    } catch(err) {
        setStatus(err.message || '保存提示词失败');
    }
}
function openProjectProxySettings(){
    if(!activeProjectId) return;
    openStorageSettings('proxy').catch(err => setStatus(err.message || '打开视频代理设置失败'));
}
function detailWritableStorageRoots(){
    return (Array.isArray(registryStatus?.overview?.roots) ? registryStatus.overview.roots : [])
        .filter(root => root?.id && root?.path && !root?.read_only);
}
async function saveDetailViewerName(){
    const input = document.querySelector('.asset-detail-viewer [data-detail-name-input]');
    const item = detailViewerState.items[detailViewerState.index];
    if(!input || !item) return;
    let name = String(input.value || '').trim();
    if(!name){ setStatus('文件名称不能为空'); input.focus(); return; }
    const extension = assetFileExtension(item);
    const requestedExtension = (name.match(/\.([^./\\]+)$/) || [])[1] || '';
    if(extension && !requestedExtension) name = `${name}.${extension}`;
    else if(extension && requestedExtension.toLowerCase() !== extension.toLowerCase()){
        setStatus(`请保留 .${extension} 扩展名`);
        input.focus();
        return;
    }
    if(name === item.name) return;
    const source = detailViewerState.source;
    const previousFileKey = detailViewerFileKey(source, item);
    let updated = null;
    if(source === 'registry'){
        const data = await apiJsonResponse(assetManagerApi.updateRegistryAsset(item.id, {name}));
        updated = normalizeRegistryAsset(data.asset || {});
        setRegistryAssets(registryAssets.map(value => value.id === item.id ? {...value,...updated} : value));
        setRegistryDetail(updated);
    } else if(source === 'asset' || source === 'workflow'){
        const data = await apiJsonResponse(assetManagerApi.updateAssetLibraryItem(item.id, {name, expected_version:assetLibraryVersionForItem(item.id)}));
        setAssetLibrary(data.library || assetLibrary);
        updated = source === 'asset' ? findAssetItem(item.id) : findWorkflowItem(item.id);
    } else if(source === 'localup'){
        const data = await apiJsonResponse(assetManagerApi.updateLocalAsset({kind:activeLocalLibraryKind(), path:item.file || item.id, name}));
        setLocalAssets(Array.isArray(data.items) ? data.items : localAssets);
        setLocalUploadTree(data.tree || localUploadTree);
        updated = data.item || null;
        setSelectedLocalUploadId(updated?.id || updated?.file || selectedLocalUploadId);
    }
    if(!updated) return;
    detailViewerFileInfoCache.delete(previousFileKey);
    const items = detailViewerItems(source).filter(Boolean);
    const updatedId = updated.id || item.id;
    let index = items.findIndex(value => value.id === updatedId);
    if(index < 0){ items.splice(detailViewerState.index, 0, updated); index = detailViewerState.index; }
    items[index] = {...items[index], ...updated};
    delete items[index].absolute_path;
    detailViewerState.items = items;
    detailViewerState.index = index;
    detailViewerState.id = updatedId;
    detailViewerSyncSelection(source, items[index]);
    renderDetailViewer();
    loadDetailViewerFileInfo();
    setStatus('文件名称已保存');
}
async function saveDetailTextVersionEdit(){
    const version = detailViewerState.selectedTextVersion;
    const editor = document.querySelector('[data-detail-version-editor]');
    if(!version || !editor) return;
    const {payload} = detailTextVersionParams();
    const data = await apiJsonResponse(assetManagerApi.updateAssetContentVersion(version.id, {...payload, text:editor.value}));
    detailViewerState.selectedTextVersion = data.version || {...version, content:editor.value};
    detailViewerState.textVersionEditing = false;
    await loadDetailTextVersions(true);
    setStatus(`版本 v${Number(version.version_number || 0)} 已保存`);
}
async function saveDetailTextEdit(){
    const item = detailViewerState.items[detailViewerState.index];
    const editor = document.querySelector('[data-detail-text-editor]');
    if(!item || !editor || !detailViewerCanEditText(detailViewerState.source, item)) return;
    const {payload} = detailTextVersionParams(item);
    const text = editor.value;
    const data = await apiJsonResponse(assetManagerApi.updateAssetContent({...payload, text}));
    const key = detailViewerTextKey(detailViewerState.source, item);
    detailViewerTextCache.set(key, {text, truncated:false});
    detailViewerState.text = text;
    detailViewerState.textEditing = false;
    detailViewerState.textTruncated = false;
    item.size = Number(data.size || item.size || 0);
    item.updated_at = Number(data.updated_at || item.updated_at || Date.now());
    renderDetailViewer();
    loadDetailViewerFileInfo();
    await loadDetailTextVersions(true);
    setStatus('正文已保存到原文件');
}
async function saveDetailVideoBlobToPath(blob,filename,destination){
    if(destination?.handle){
        const writable = await destination.handle.createWritable();
        await writable.write(blob);
        await writable.close();
        return true;
    }
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url; link.download = filename;
    document.body.appendChild(link); link.click(); link.remove();
    setTimeout(() => URL.revokeObjectURL(url),1000);
    return true;
}
async function saveDetailImageEditorVersion(){
    const state=detailImageEditorState;
    if(!state || !state.dirty) return null;
    if(!state.sourceAssetId) throw new Error('当前图片没有统一资产 ID，无法保存非破坏性版本');
    const edit=detailImageEditorSerialized();
    const data=await apiJsonResponse(assetManagerApi.createRegistryImageVersion(state.sourceAssetId,{edit,canvas_id:detailViewerState.referenceCanvasId || ''}));
    const version=data.version || {};
    if(!version.id || !version.edit) throw new Error('后台没有返回有效的图片版本记录');
    state.assetVersionId=String(version.id);
    state.assetVersionNumber=Number(version.version_number || 0);
    const savedEdit={...version.edit,asset_version_id:state.assetVersionId,asset_version_number:state.assetVersionNumber};
    updateDetailImageEditorCanvasVersion(savedEdit,version);
    if(detailViewerState.imageVersionsAssetId===state.sourceAssetId){
        detailViewerState.imageVersions=[version,...detailViewerState.imageVersions.filter(item=>item.id!==version.id)].sort((left,right)=>{
            if(left.is_original || left.id==='original') return -1;
            if(right.is_original || right.id==='original') return 1;
            return Number(right.version_number||0)-Number(left.version_number||0);
        });
        detailViewerState.imageVersionSelectedId=String(version.id);
    }
    const versionUrl=`/api/asset-registry/assets/${encodeURIComponent(state.sourceAssetId)}/image-versions/${encodeURIComponent(state.assetVersionId)}/media`;
    const applyLatest=item=>item.id===state.sourceAssetId ? {...item,url:versionUrl,latest_image_version_id:state.assetVersionId,latest_image_version_number:state.assetVersionNumber,latest_image_version_url:versionUrl} : item;
    setRegistryAssets(registryAssets.map(applyLatest));
    if(registryDetail?.id===state.sourceAssetId) setRegistryDetail(applyLatest(registryDetail));
    state.dirty=false;
    state.pendingCanvasSave=Boolean(detailViewerState.referenceCanvasId);
    setStatus(`已保存为当前素材最新版本 V${state.assetVersionNumber}，原文件未修改`);
    return version;
}
async function saveDetailImageEditorAs(){
    const state = detailImageEditorState;
    if(!state) return;
    const blob = await detailImageEditorBlob();
    const filename = `${String(state.name || '图片').replace(/\.[^.]+$/,'')}_编辑.png`;
    if(typeof window.showSaveFilePicker === 'function'){
        try {
            const handle = await window.showSaveFilePicker({suggestedName:filename,types:[{description:'PNG 图片',accept:{'image/png':['.png']}}]});
            const writable = await handle.createWritable();
            await writable.write(blob);
            await writable.close();
        } catch(err){ if(err?.name === 'AbortError') return; throw err; }
    } else {
        const url = URL.createObjectURL(blob);
        const link = Object.assign(document.createElement('a'),{href:url,download:filename});
        link.click();
        setTimeout(() => URL.revokeObjectURL(url),1000);
    }
    setStatus(`编辑图片已另存为：${filename}`);
}
async function saveAssetEdit(id){
    const item = findAssetItem(id);
    const name = document.getElementById('assetEditName')?.value || '';
    if(!item || !String(name || '').trim()) {
        setStatus('素材名称不能为空');
        return;
    }
    const data = await apiJsonResponse(assetManagerApi.updateAssetLibraryItem(id, {name, expected_version:assetLibraryVersionForItem(id)}));
    setAssetLibrary(data.library || assetLibrary);
    setSelectedAssetId(id);
    setAssetEditMode(false);
    render();
    setStatus('素材已保存');
}
async function saveAssetInlineName(id, name){
    const item = findAssetItem(id);
    if(!item || !String(name || '').trim()) return;
    if(String(item.name || '') === String(name || '')) return;
    const data = await apiJsonResponse(assetManagerApi.updateAssetLibraryItem(id, {name, expected_version:assetLibraryVersionForItem(id)}));
    setAssetLibrary(data.library || assetLibrary);
    setSelectedAssetId(id);
    render();
    setStatus('素材名称已保存');
}
async function registerAssetAvatar(id, providerId=''){
    const item = findAssetItem(id);
    if(!item) return;
    const provider = (providerId && (apiProviders || []).find(p => p.id === providerId)) || activeAvatarProvider();
    if(!provider){ setStatus('请先在 API 平台管理中添加并启用 API 平台'); return; }
    if(!providerAvatarSupported(provider)){ setStatus(`「${avatarPlatformLabel(providerAvatarPlatform(provider))}」的资产认证 API 尚未接入`); return; }
    if(avatarBusyId) return;
    avatarBusyId = id;
    setSelectedAssetId(id);
    render();
    setStatus(`正在上传素材并提交 ${avatarPlatformLabel(providerAvatarPlatform(provider))} 审核…`);
    try {
        const data = await apiJsonResponse(assetManagerApi.registerAssetAvatar(id, {
            library_id:activeAssetLibraryId,
            provider_id:provider.id,
        }));
        setAssetLibrary(data.library || assetLibrary);
        setStatus(`已提交审核，正在等待 ${avatarPlatformLabel(providerAvatarPlatform(provider))} 通过…`);
        scheduleAvatarPoll(id, provider.id);
    } catch(err) {
        setStatus(err.message || '数字人提交失败');
    } finally {
        avatarBusyId = '';
        render();
    }
}
function avatarRegistrationOf(item, platform){
    const regs = (item && item.registrations && typeof item.registrations === 'object') ? item.registrations : {};
    return regs[platform] || null;
}
async function checkAssetAvatarStatus(id, silent=false, providerId=''){
    const item = findAssetItem(id);
    if(!item) return;
    const provider = (providerId && (apiProviders || []).find(p => p.id === providerId)) || activeAvatarProvider();
    if(!provider) return;
    const platform = providerAvatarPlatform(provider);
    const reg = avatarRegistrationOf(item, platform);
    if(!reg || !reg.task_id) return;
    if(!silent){ avatarBusyId = id; render(); setStatus('正在查询审核状态…'); }
    try {
        const data = await apiJsonResponse(assetManagerApi.checkAssetAvatarStatus(id, {
            library_id:activeAssetLibraryId,
            provider_id:provider.id,
        }));
        setAssetLibrary(data.library || assetLibrary);
        const newReg = (data.item?.registrations && data.item.registrations[platform]) || {};
        const status = newReg.status || '';
        if(status === 'Active') setStatus('审核通过，已生成 asset:// 地址，可在视频生成中通过 @ 调用');
        else if(status === 'Failed') setStatus(newReg.detail || '审核未通过');
        else { setStatus('仍在审核中，稍后会自动刷新…'); scheduleAvatarPoll(id, provider.id); }
    } catch(err) {
        if(!silent) setStatus(err.message || '查询审核状态失败');
    } finally {
        avatarBusyId = '';
        render();
    }
}
function scheduleAvatarPoll(id, providerId){
    setTimeout(() => {
        const item = findAssetItem(id);
        const provider = (apiProviders || []).find(p => p.id === providerId);
        if(!item || !provider) return;
        const reg = avatarRegistrationOf(item, providerAvatarPlatform(provider));
        if(reg && reg.task_id && reg.status === 'Processing'){
            checkAssetAvatarStatus(id, true, providerId);
        }
    }, 6000);
}

// 【QIN-591】域函数面注册：主文件 gwCall('settings', fn) 经由此对象分派。
GW_DEFERRED['settings'] = {
    addWorkspaceMapping,
    applyAllowedRootSettingsData,
    applyStorageSettingsData,
    applyWorkspaceSnapshot,
    applyThumbnailCacheDirectoryField,
    applyThumbnailSettingsSnapshot,
    archiveProjectDirectoryTemplate,
    cancelJxlLosslessTranscode,
    cancelThumbnailCacheSwitch,
    checkAssetAvatarStatus,
    clearAssetRackFolder,
    closeProjectDirectoryTemplateManagerSheet,
    closeStorageSettings,
    closeThumbnailGenerator,
    commitThumbnailSettingsChange,
    confirmThumbnailCacheSwitch,
    copyLocalUploadCaption,
    createAssetRackFolder,
    dismissThumbnailCacheBlock,
    ensureAssetRackDirectoryIndex,
    generateAssetThumbnails,
    generateAssetThumbnailsInBackground,
    grantThumbnailCacheRootAndRetry,
    loadAssetRackDiskFolders,
    loadAssetRackRegisteredFolders,
    loadJxlTranscodeScan,
    loadServerDirectoryBrowser,
    loadWorkspaceSnapshot,
    normalizeLocalCaptionSettings,
    openProjectDirectoryTemplateManagerSheet,
    openProjectProxySettings,
    openStorageSettings,
    openThumbnailGenerator,
    persistThumbnailStorageSettings,
    pickClientDirectoryNative,
    pickServerDirectoryNative,
    validateServerResourceDirectory,
    registerAllowedRootPath,
    registerAssetAvatar,
    renderGlobalDirectorySettings,
    renderProjectDirectoryTemplateManagerSheet,
    renderServerDirectoryBrowserHost,
    renderWorkspaceSettingsBody,
    selectWorkspaceTarget,
    renderStorageSettingsModal,
    renderThumbnailGeneratorModal,
    requestProjectDirectoryTemplatePendingAction,
    resetAssetRackDirectoryIndex,
    runAssetClassifySelected,
    runLocalUploadCaptionOne,
    runLocalUploadCaptionSelected,
    runLocalUploadClassifySelected,
    runProjectDirectoryTemplatePendingAction,
    saveAssetAiSettings,
    saveAssetEdit,
    saveAssetInlineName,
    saveAssetProxySettings,
    saveDetailTextEdit,
    saveDetailTextVersionEdit,
    saveDetailViewerName,
    saveDirectoryPath,
    saveLocalLibrarySettings,
    saveLocalUploadCaption,
    saveProjectDirectoryTemplate,
    saveRegistryPreset,
    saveRegistryViewSettings,
    selectAssetRackFolder,
    setAssetAiAutoClassify,
    setAssetRackCollapsed,
    setProjectDirectoryTemplateDefault,
    startJxlLosslessTranscode,
    switchWorkspace,
    syncStorageSettingsInputsToState,
    verifyWorkspaceMapping,
    toggleAssetRackFolder,
    toggleAssetRackFoldersAll,
    updateThumbnailCacheExclusionHint,
    writeLocalCaptionSettings,
};

export {
    addWorkspaceMapping,
    applyAllowedRootSettingsData,
    applyStorageSettingsData,
    applyWorkspaceSnapshot,
    assetPathWithinAllowedRoot,
    clearAssetRackFolder,
    closeStorageSettings,
    closeThumbnailGenerator,
    confirmRootAliasSuggestion,
    describeResourceFolderPathRejection,
    detailWritableStorageRoots,
    ensureAssetRackDirectoryIndex,
    ensureCreateLibraryPathAllowedRoot,
    loadAssetRackDiskFolders,
    loadAssetRackRegisteredFolders,
    loadWorkspaceSnapshot,
    normalizeLocalCaptionSettings,
    refreshThumbnailCacheStats,
    renderAvatarSection,
    renderClassificationChips,
    renderLocalCaptionTools,
    renderServerDirectoryBrowser,
    renderStorageSettingsModal,
    renderWorkspaceSettingsBody,
    resetAssetRackDirectoryIndex,
    selectWorkspaceTarget,
    saveAssetRackExpandedFolders,
    saveDetailImageEditorAs,
    saveDetailImageEditorVersion,
    saveDetailTextEdit,
    saveDetailTextVersionEdit,
    saveDetailVideoBlobToPath,
    saveDetailViewerName,
    saveLocalLibrarySettings,
    saveRegistryCustomGroups,
    saveRegistryViewSettings,
    signalFlowAccentColor,
    storageNewExtraId,
    switchWorkspace,
    syncProjectDirectoryTemplateDraft,
    thumbnailGeneratorAssetIds,
    updateThumbnailCacheExclusionHint,
    verifyWorkspaceMapping,
    validateServerResourceDirectory,
};
// ---------------------------------------------------------------------------
// QIN-627：数据位置卡片（当前根、四类分离、预检、迁移、只读横幅）
// ---------------------------------------------------------------------------
const DATA_LOCATION_LOAD_TIMEOUT_MS = 8000;
const DATA_LOCATION_TIMEOUT_MS = DATA_LOCATION_LOAD_TIMEOUT_MS;
const dataLocationState = {
    loading: false,
    ready: false,
    error: "",
    snapshot: null,
    check: null,
    target: "",
    busy: false,
    poll: null,
    seq: 0,
    controller: null,
    loadGeneration: 0,
    checkGeneration: 0,
    loadController: null,
    checkController: null,
    timeoutMs: DATA_LOCATION_TIMEOUT_MS,
};

function dataLocationFormatBytes(value){
    const bytes = Number(value);
    if(!Number.isFinite(bytes) || bytes < 0) return "—";
    const units = ["B", "KB", "MB", "GB", "TB"];
    let size = bytes;
    let index = 0;
    while(size >= 1024 && index < units.length - 1){ size /= 1024; index += 1; }
    return `${size >= 100 || index === 0 ? Math.round(size) : size.toFixed(1)} ${units[index]}`;
}

function dataLocationReadOnly(){
    const snapshot = dataLocationState.snapshot;
    return Boolean(snapshot && String(snapshot.write_window?.state || "open") !== "open");
}

function dataLocationActiveMigration(){
    const migration = dataLocationState.snapshot?.migration;
    return Boolean(migration && !["completed", "failed", "cancelled", "rolled_back"].includes(String(migration.status || "")));
}

function renderDataLocationBanner(){
    const visible = dataLocationReadOnly();
    return `<div class="data-location-readonly-banner" data-location-readonly ${visible ? "" : "hidden"} role="alert">
        <i data-lucide="lock"></i>
        <div><strong>数据位置已切换，重启后生效；当前为只读</strong>
        <span>所有写入、上传、任务启动与设置保存均已暂停；请重启工作台完成切换。旧位置数据保留，不会被自动删除。</span></div>
    </div>`;
}

function renderDataLocationCategoryRows(snapshot){
    const rows = Array.isArray(snapshot?.categories) ? snapshot.categories : [];
    return rows.map(item => `<li class="data-location-category" data-data-location-category="${escapeAttr(String(item.key || ""))}">
        <div><strong>${escapeHtml(String(item.label || item.key || ""))}</strong>
        <span class="data-location-path">${escapeHtml(String(item.path || item.note || "由素材来源目录持有"))}</span></div>
        <em class="${item.default_migrated ? "is-migrated" : "is-kept"}">${item.default_migrated ? "随数据迁移" : (item.key === "models" ? "默认不迁移" : "永不复制/移动")}</em>
    </li>`).join("");
}

function renderDataLocationCard(){
    const snapshot = dataLocationState.snapshot;
    const pointer = snapshot?.pointer || {};
    const migration = snapshot?.migration;
    const busy = dataLocationState.busy;
    const readOnly = dataLocationReadOnly();
    const activeJobs = snapshot?.active_jobs || {};
    const activeJobText = Object.keys(activeJobs).length
        ? Object.entries(activeJobs).map(([name, count]) => `${escapeHtml(name)} × ${Number(count)}`).join("、")
        : "无";
    const checked = dataLocationState.check;
    const targetMatchesCheck = Boolean(checked && dataLocationState.target.trim() && String(checked.target) === dataLocationState.target.trim());
    const canStart = Boolean(snapshot && !readOnly && !migration && !busy && targetMatchesCheck);
    const migrationView = migration ? `<div class="data-location-progress" data-data-location-progress>
        <div class="data-location-progress-head"><strong>迁移 ${escapeHtml(String(migration.status || ""))}</strong>
        <span>${escapeHtml(String(migration.phase || ""))} · ${dataLocationFormatBytes(migration.copied_bytes)} / ${dataLocationFormatBytes(migration.total_bytes)}</span></div>
        <div class="data-location-progress-track"><i style="width:${Math.max(0, Math.min(100, Number(migration.percent || 0)))}%"></i></div>
        ${migration.error_code ? `<p class="data-location-error" role="alert">${escapeHtml(String(migration.error_code))}：${escapeHtml(String(migration.message || ""))}</p>` : ""}
        <div class="data-location-actions">
            ${dataLocationActiveMigration() && ["planned", "checking_space", "quiescing", "copying", "verifying"].includes(String(migration.status)) ? `<button class="hw-btn" type="button" data-data-location-cancel ${busy ? "disabled" : ""}><i data-lucide="x"></i>取消迁移</button>` : ""}
            ${readOnly ? `<button class="hw-btn hw-btn-danger" type="button" data-data-location-rollback ${busy ? "disabled" : ""}><i data-lucide="undo-2"></i>回退到旧位置</button>` : ""}
        </div></div>` : "";
    const errorBox = dataLocationState.error ? `<p class="data-location-error" role="alert">${escapeHtml(dataLocationState.error)}</p>` : "";
    if(!dataLocationState.ready){
        return `<section class="asset-pref-section bay-inset p-4 rounded-xl gw-settings-card data-location-card" data-board-data-location>
            <div class="asset-pref-title"><i data-lucide="database"></i><span>数据位置</span><button class="hw-btn hw-btn-icon" type="button" data-data-location-refresh title="重新读取数据位置" aria-label="重新读取数据位置"><i data-lucide="refresh-cw"></i></button></div>
            ${errorBox ? `${errorBox}<div class="data-location-actions"><button class="hw-btn" type="button" data-data-location-refresh data-data-location-retry><i data-lucide="refresh-cw"></i>重试读取</button></div>` : `<p class="directory-status" role="status">${dataLocationState.loading ? "正在读取数据位置…" : "等待读取数据位置"}</p>`}
        </section>`;
    }
    return `<section class="asset-pref-section bay-inset p-4 rounded-xl gw-settings-card data-location-card" data-board-data-location>
        <div class="asset-pref-title"><i data-lucide="database"></i><span>数据位置</span><button class="hw-btn hw-btn-icon" type="button" data-data-location-refresh title="重新读取数据位置" aria-label="重新读取数据位置" ${busy ? "disabled" : ""}><i data-lucide="refresh-cw"></i></button></div>
        ${errorBox}
        <div class="data-location-summary">
            <div><span>生效软件目录</span><strong style="min-width:0;overflow-wrap:anywhere;word-break:break-word;" title="${escapeAttr(String(snapshot.install_dir || ""))}">${escapeHtml(String(snapshot.install_dir || "—"))}</strong></div>
            <div><span>引导目录</span><strong style="min-width:0;overflow-wrap:anywhere;word-break:break-word;" title="${escapeAttr(String(snapshot.bootstrap_root || ""))}">${escapeHtml(String(snapshot.bootstrap_root || ""))}</strong></div>
            <div><span>当前数据位置</span><strong style="min-width:0;overflow-wrap:anywhere;word-break:break-word;" title="${escapeAttr(String(snapshot.data_location || ""))}">${escapeHtml(String(snapshot.data_location || ""))}</strong></div>
            <div><span>指针版本</span><strong>${Number(pointer.revision || 1)}${pointer.present ? "" : "（默认位置）"}</strong></div>
            <div><span>可用空间</span><strong>${dataLocationFormatBytes(snapshot.disk?.free_bytes)} / ${dataLocationFormatBytes(snapshot.disk?.total_bytes)}</strong></div>
        </div>
        <ul class="data-location-categories">${renderDataLocationCategoryRows(snapshot)}</ul>
        <p class="data-location-note">模型缓存默认不迁移：旧 <code>data/models</code> 文件保留在原位置并继续占用磁盘，迁移后可能需要重新配置或下载；素材原件由资源文件夹持有，永不复制或移动。</p>
        <div class="data-location-target">
            <label><span>新数据位置（绝对路径）</span><div class="directory-input-row"><input type="text" data-data-location-target value="${escapeAttr(dataLocationState.target)}" placeholder="${escapeAttr(String(snapshot.data_location || "选择新的数据目录"))}" autocomplete="off" ${readOnly || busy ? "disabled" : ""}><button class="hw-btn" type="button" data-data-location-check ${readOnly || busy ? "disabled" : ""}><i data-lucide="shield-check"></i>预检</button></div></label>
            <div class="data-location-categories-picker" role="group" aria-label="迁移类别">
                <label><input type="checkbox" data-data-location-category-option="user_data" checked disabled> 用户数据与索引（必选）</label>
                <label><input type="checkbox" data-data-location-category-option="cache" checked ${readOnly || busy ? "disabled" : ""}> 缓存输出</label>
                <label><input type="checkbox" data-data-location-category-option="models" ${readOnly || busy ? "disabled" : ""}> 模型缓存（默认不迁移）</label>
            </div>
        </div>
        ${checked ? `<div class="data-location-check ${targetMatchesCheck ? "is-ok" : "is-stale"}" data-data-location-check-result>
            <strong>预检${targetMatchesCheck ? "通过" : "结果已过期"}</strong>
            <span>目标 ${escapeHtml(String(checked.target || ""))} · 需 ${dataLocationFormatBytes(checked.required_bytes)} · 可用 ${dataLocationFormatBytes(checked.free_bytes)} · ${Number(checked.files || 0)} 个文件</span>
            ${checked.excluded?.length ? `<span>排除：${checked.excluded.map(item => `${escapeHtml(String(item.reason || ""))} × ${Number(item.files || 0)}`).join("、")}</span>` : ""}
        </div>` : ""}
        <div class="data-location-actions">
            <button class="hw-btn hw-btn-gold" type="button" data-data-location-start ${canStart ? "" : "disabled"}><i data-lucide="hard-drive-upload"></i>${busy ? "处理中…" : "开始迁移"}</button>
            <span class="data-location-jobs">活动后台任务：${activeJobText}</span>
        </div>
        ${migrationView}
        ${readOnly ? `<p class="data-location-note">重启后新进程读取新位置并恢复写入；旧位置完整保留。回退会把指针切回旧位置，但仍需重启后生效。</p>` : ""}
    </section>`;
}

function refreshDataLocationDom(){
    const host = document.getElementById('storageSettingsOverlay') || document.getElementById('assetManagerRoot');
    if(!host) return;
    const card = host.querySelector('[data-board-data-location]');
    if(card) card.outerHTML = renderDataLocationCard();
    const banner = host.querySelector('[data-location-readonly]');
    if(banner) banner.outerHTML = renderDataLocationBanner();
    refreshIcons();
}

async function loadDataLocationData({force = false} = {}){
    if(dataLocationState.loading && !force && !dataLocationState.loadController && !dataLocationState.controller) return;
    const seq = ++dataLocationState.seq;
    dataLocationState.loadGeneration = seq;
    if(dataLocationState.controller){
        try { dataLocationState.controller.abort(); } catch(_) {}
    }
    const controller = typeof AbortController === 'function' ? new AbortController() : null;
    dataLocationState.controller = controller;
    dataLocationState.loadController = controller;
    dataLocationState.loading = true;
    dataLocationState.error = '';
    refreshDataLocationDom();
    let timer = 0;
    let timedOut = false;
    const timeoutMs = Number(dataLocationState.timeoutMs) > 0 ? Number(dataLocationState.timeoutMs) : DATA_LOCATION_LOAD_TIMEOUT_MS;
    try{
        const timeoutPromise = new Promise((_, reject) => {
            timer = setTimeout(() => {
                timedOut = true;
                try { controller?.abort?.(); } catch(_) {}
                reject(new Error('读取数据位置超时（超过 8 秒），请重试。'));
            }, timeoutMs);
        });
        const requestInit = {credentials:'same-origin', cache:'no-store', ...(controller ? {signal: controller.signal} : {})};
        const snapshot = await Promise.race([
            apiJsonResponse(fetch('/api/data-location', requestInit)),
            timeoutPromise,
        ]);
        if(seq !== dataLocationState.seq) return;
        dataLocationState.snapshot = snapshot;
        dataLocationState.error = '';
        dataLocationState.ready = true;
        if(dataLocationReadOnly()) dataLocationState.check = null;
    }catch(error){
        if(seq !== dataLocationState.seq) return;
        dataLocationState.error = timedOut
            ? '读取数据位置超时（超过 8 秒），请重试。'
            : (error?.name === 'AbortError'
                ? '读取数据位置已取消或超时，请重试。'
                : (error?.message || '数据位置读取失败'));
    }finally{
        clearTimeout(timer);
        if(dataLocationState.controller === controller){
            dataLocationState.controller = null;
            dataLocationState.loadController = null;
        }
        if(seq === dataLocationState.seq){
            dataLocationState.loading = false;
            refreshDataLocationDom();
            if(dataLocationActiveMigration()){
                clearTimeout(dataLocationState.poll);
                dataLocationState.poll = setTimeout(() => { loadDataLocationData(); }, 900);
            }
        }
    }
}

function selectedDataLocationCategories(){
    const options = document.querySelectorAll('[data-data-location-category-option]');
    const values = [];
    options.forEach(option => { if(option.checked) values.push(option.dataset.dataLocationCategoryOption); });
    if(!values.includes('user_data')) values.unshift('user_data');
    return values;
}

async function checkDataLocationTarget(){
    const target = String(dataLocationState.target || '').trim();
    if(!target){ dataLocationState.error = '请输入新的数据位置绝对路径'; refreshDataLocationDom(); return; }
    if(dataLocationState.checkController){
        try{ dataLocationState.checkController.abort(); }catch(_err){}
    }
    const generation = ++dataLocationState.checkGeneration;
    const controller = typeof AbortController !== 'undefined' ? new AbortController() : null;
    dataLocationState.checkController = controller;
    dataLocationState.busy = true;
    dataLocationState.error = '';
    refreshDataLocationDom();
    let timedOut = false;
    const timeoutMs = Number(dataLocationState.timeoutMs) > 0 ? Number(dataLocationState.timeoutMs) : DATA_LOCATION_TIMEOUT_MS;
    const timer = controller ? setTimeout(() => {
        timedOut = true;
        try{ controller.abort(); }catch(_err){}
    }, timeoutMs) : null;
    try{
        const fetchOptions = {
            method:'POST', credentials:'same-origin',
            headers:{'Content-Type':'application/json'},
            body:JSON.stringify({target, categories:selectedDataLocationCategories()}),
        };
        if(controller) fetchOptions.signal = controller.signal;
        const checked = await apiJsonResponse(fetch('/api/data-location/check', fetchOptions));
        if(generation !== dataLocationState.checkGeneration) return;
        dataLocationState.check = checked;
    }catch(error){
        if(generation !== dataLocationState.checkGeneration) return;
        dataLocationState.check = null;
        dataLocationState.error = timedOut ? '目标位置预检超时，请重试' : (error.message || '目标位置预检失败');
    }finally{
        if(timer) clearTimeout(timer);
        if(generation === dataLocationState.checkGeneration){
            dataLocationState.checkController = null;
            dataLocationState.busy = false;
            refreshDataLocationDom();
        }
    }
}

async function startDataLocationMigration(){
    const snapshot = dataLocationState.snapshot;
    const target = String(dataLocationState.target || '').trim();
    if(!snapshot || !target || !dataLocationState.check) return;
    const modelsSelected = selectedDataLocationCategories().includes('models');
    const message = modelsSelected
        ? '将迁移用户数据、缓存与模型缓存到新位置；完成后需重启生效，旧位置数据保留。素材原件不会被复制或移动。确认开始？'
        : '模型缓存默认不迁移：旧 data/models 文件会保留在原位置并继续占用磁盘，迁移后可能需要重新配置或下载。素材原件永不复制或移动。确认开始？';
    if(!window.confirm(message)) return;
    dataLocationState.busy = true;
    dataLocationState.error = '';
    refreshDataLocationDom();
    try{
        await apiJsonResponse(fetch('/api/data-location/migrations', {
            method:'POST', credentials:'same-origin',
            headers:{'Content-Type':'application/json'},
            body:JSON.stringify({
                target,
                expected_version:Number(snapshot.pointer?.revision || 1),
                categories:selectedDataLocationCategories(),
            }),
        }));
        dataLocationState.check = null;
    }catch(error){
        dataLocationState.error = error.message || '迁移启动失败';
    }finally{
        dataLocationState.busy = false;
        await loadDataLocationData();
        refreshDataLocationDom();
    }
}

async function cancelDataLocationMigration(){
    const migration = dataLocationState.snapshot?.migration;
    if(!migration || dataLocationState.busy) return;
    dataLocationState.busy = true;
    try{
        await apiJsonResponse(fetch(`/api/data-location/migrations/${encodeURIComponent(migration.migration_id)}/cancel`, {
            method:'POST', credentials:'same-origin',
        }));
    }catch(error){
        dataLocationState.error = error.message || '取消失败';
    }finally{
        dataLocationState.busy = false;
        await loadDataLocationData();
    }
}

async function rollbackDataLocation(){
    if(dataLocationState.busy) return;
    if(!window.confirm('回退会把数据位置指针切回上一位置，仍需重启后生效；旧（新）位置数据保留。确认回退？')) return;
    dataLocationState.busy = true;
    try{
        await apiJsonResponse(fetch('/api/data-location/rollback', {
            method:'POST', credentials:'same-origin',
        }));
    }catch(error){
        dataLocationState.error = error.message || '回退失败';
    }finally{
        dataLocationState.busy = false;
        await loadDataLocationData();
    }
}

function handleDataLocationClick(event){
    if(event.target.closest('[data-data-location-refresh]')){ loadDataLocationData({force: true}); return; }
    if(event.target.closest('[data-data-location-check]')){ checkDataLocationTarget(); return; }
    if(event.target.closest('[data-data-location-start]')){ startDataLocationMigration(); return; }
    if(event.target.closest('[data-data-location-cancel]')){ cancelDataLocationMigration(); return; }
    if(event.target.closest('[data-data-location-rollback]')){ rollbackDataLocation(); return; }
}

function ensureDataLocationBindings(host){
    if(!host.dataset.dataLocationBound){
        host.dataset.dataLocationBound = '1';
        host.addEventListener('click', handleDataLocationClick);
        host.addEventListener('input', event => {
            const input = event.target.closest('[data-data-location-target]');
            if(!input) return;
            dataLocationState.target = input.value;
            dataLocationState.check = null;
        });
        host.addEventListener('change', event => {
            if(event.target.closest('[data-data-location-category-option]')) dataLocationState.check = null;
        });
    }
    if(!dataLocationState.ready && !dataLocationState.loading) loadDataLocationData();
}

function _createDirectoryAbortController(options={}){
    const controller = typeof AbortController === 'function' ? new AbortController() : null;
    const timeoutMs = Number(options.timeoutMs) > 0 ? Number(options.timeoutMs) : 0;
    let isTimedOut = false;
    const timer = (controller && timeoutMs > 0)
        ? setTimeout(() => { isTimedOut = true; controller.abort(); }, timeoutMs)
        : null;
    const onExternalAbort = () => controller?.abort();
    if(options.signal){
        if(options.signal.aborted){
            if(timer) clearTimeout(timer);
            controller?.abort();
        } else {
            options.signal.addEventListener?.('abort', onExternalAbort, {once:true});
        }
    }
    return {
        controller,
        timedOut: () => isTimedOut,
        cleanup: () => {
            if(timer) clearTimeout(timer);
            options.signal?.removeEventListener?.('abort', onExternalAbort);
        },
    };
}

async function _loadServerDirectoryBrowserImpl(pathValue=null, options={}){
    const state = serverDirectoryBrowserState;
    state.abortController?.abort?.();
    const generation = Number(state.generation || 0) + 1;
    const timeoutMs = Number(options.timeoutMs) > 0 ? Number(options.timeoutMs) : 8000;
    const ctl = _createDirectoryAbortController({...options, timeoutMs});
    state.generation = generation;
    state.abortController = ctl.controller;
    state.path = pathValue;
    state.parent = null;
    state.directories = [];
    state.truncated = false;
    state.loading = true;
    state.error = '';
    renderServerDirectoryBrowserHost();
    try {
        const query = pathValue ? '?path=' + encodeURIComponent(pathValue) : '';
        const reqInit = {method:'GET', credentials:'same-origin'};
        if(ctl.controller) reqInit.signal = ctl.controller.signal;
        const response = await apiJsonResponse(assetManagerHttp.request('/api/storage-settings/browse' + query, reqInit));
        if(!serverDirectoryBrowserState.open || Number(serverDirectoryBrowserState.generation || 0) !== generation) return;
        setServerDirectoryBrowserState({
            ...serverDirectoryBrowserState,
            path:response.path ?? null,
            parent:response.parent ?? null,
            directories:Array.isArray(response.directories) ? response.directories : [],
            truncated:Boolean(response.truncated),
            limit:Number(response.limit) || 100,
            loading:false,
            abortController:null,
            error:''
        });
    } catch(error){
        if(!serverDirectoryBrowserState.open || Number(serverDirectoryBrowserState.generation || 0) !== generation) return;
        if(error?.name === 'AbortError' && !ctl.timedOut()) return;
        const message = ctl.timedOut()
            ? '读取服务器目录超时（超过 8 秒），请重试或关闭'
            : (error?.message || '服务器目录读取失败');
        setServerDirectoryBrowserState({...serverDirectoryBrowserState, loading:false, abortController:null, error:message});
    } finally {
        ctl.cleanup();
    }
    renderServerDirectoryBrowserHost();
}

async function _validateServerResourceDirectoryImpl(pathValue, options={}){
    const target = String(pathValue || '').trim();
    if(!target) throw new Error('请选择资源文件夹');
    const timeoutMs = Number(options.timeoutMs) > 0 ? Number(options.timeoutMs) : 8000;
    if(options.signal?.aborted){
        const abortErr = new Error('已取消目录校验');
        abortErr.name = 'AbortError';
        throw abortErr;
    }
    const ctl = _createDirectoryAbortController({...options, timeoutMs});
    try {
        const reqInit = {method:'GET', credentials:'same-origin'};
        if(ctl.controller) reqInit.signal = ctl.controller.signal;
        const response = await apiJsonResponse(assetManagerHttp.request(
            '/api/storage-settings/browse?path=' + encodeURIComponent(target),
            reqInit,
        ));
        return String(response?.path || target);
    } catch(error){
        if(ctl.timedOut()){
            const timeoutErr = new Error('服务端目录校验超时（超过 8 秒），请重试');
            timeoutErr.code = 'DIRECTORY_BROWSE_TIMEOUT';
            throw timeoutErr;
        }
        throw error;
    } finally {
        ctl.cleanup();
    }
}
