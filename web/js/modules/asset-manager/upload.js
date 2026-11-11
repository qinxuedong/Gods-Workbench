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

// QIN-591：资产库延后域模块（upload）。首屏不加载；经 GW_DEFERRED 注册函数面，
// 由主文件网关（gwCall/gwLoad）按需加载或在空闲时段预热。函数体除跨文件赋值点改写外保持原样。

import {
    CLIENT_SETTING_KEY_UPLOAD_SOURCE,
    DEFAULT_LOCAL_LIBRARY_NAME,
    LOCAL_MEDIA_EXTS,
    activeAssetCategory,
    activeAssetCategoryId,
    activeAssetLibrary,
    activeAssetLibraryId,
    activeLocalFolderId,
    activeLocalLibrary,
    activeLocalLibraryId,
    activeLocalLibraryKind,
    activeLocalLibraryName,
    activeLocalUploadClassEntry,
    activeLocalUploadClassFilter,
    activeLocalUploadFolder,
    activeSharedFolderName,
    activeTab,
    activeWorkflowCategory,
    activeWorkflowCategoryId,
    activeWorkflowLibraryId,
    apiJsonResponse,
    assetBoardById,
    assetKind,
    assetLibrary,
    assetThumb,
    boardSourceSelection,
    clientSettingBusy,
    clientUploadSourceDir,
    customAssetLibraryItems,
    escapeAttr,
    escapeHtml,
    findLocalItem,
    findLocalUpload,
    gwSetLocalClipboard,
    gwSetLocalUploadClipboard,
    loadLocalAssets,
    localAssets,
    localAssetsError,
    localAssetsHasMore,
    localAssetsLoaded,
    localAssetsLoading,
    localAssetsTotal,
    localCaptionBusy,
    localCaptionModel,
    localCaptionProvider,
    localClassifyBusy,
    localClassifyPrompt,
    localClipboard,
    localFolderMap,
    localItemKind,
    localItemMap,
    localLibraries,
    localLibraryDragId,
    localObjectUrl,
    localQuery,
    localUploadClassEntries,
    localUploadClipboard,
    localUploadManageMode,
    localUploadQuery,
    localUploadTree,
    openLocalUploadClassGroup,
    refreshIcons,
    refreshRegistryAssets,
    registryFacets,
    registrySelectedIds,
    reindexAssetSource,
    render,
    renderAssetTreePanel,
    requireExpectedVersion,
    root,
    selectedAssetId,
    selectedAssetIds,
    selectedLocalId,
    selectedLocalIds,
    selectedLocalUploadId,
    selectedLocalUploadIds,
    selectedWorkflowId,
    selectedWorkflowIds,
    setActiveLocalFolderId,
    setActiveLocalLibraryId,
    setActiveLocalUploadClassFilter,
    setActiveLocalUploadFolder,
    setActiveSharedFolderName,
    setAssetLibrary,
    setClientSettingBusy,
    setClientUploadSourceDir,
    setLocalAssets,
    setLocalClassifyBusy,
    setLocalItemMap,
    setLocalLibraries,
    setLocalUploadTree,
    setOpenLocalUploadClassGroup,
    setPendingBatchDelete,
    setSelectedAssetId,
    setSelectedLocalId,
    setSelectedLocalIds,
    setSelectedLocalUploadId,
    setSelectedWorkflowId,
    setSharedFolders,
    setStatus,
    sharedFolders,
    storageCleanName,
    storageLocalLibraryNames,
    storageSettingsState,
    tauriInvokeFn,
    uploadInput,
} from '../asset-manager.js';
import assetManagerApi from './api.js';
import {
    formatDate,
    formatFileSize,
} from './formatters.js';
import {
    localAssetBatchStatus,
} from './batch.js';
import {
    assetDownloadName,
    assetKindLabel,
    downloadUrl,
} from './organize.js';
import {
    normalizeLocalCaptionSettings,
    renderClassificationChips,
    renderLocalCaptionTools,
    saveLocalLibrarySettings,
} from './settings.js';
import { GW_DEFERRED } from './state.js';

async function loadClientUploadSourceDir(){
    const invoke = tauriInvokeFn();
    if(!invoke) return;
    try {
        const value = await invoke('get_client_setting', {key: CLIENT_SETTING_KEY_UPLOAD_SOURCE});
        setClientUploadSourceDir(String(value || ''));
    } catch(error){ setClientUploadSourceDir(''); }
}
async function saveClientUploadSourceDir(pathValue){
    const invoke = tauriInvokeFn();
    if(!invoke || clientSettingBusy) return;
    setClientSettingBusy(true);
    try {
        await invoke('set_client_setting', {key: CLIENT_SETTING_KEY_UPLOAD_SOURCE, value: String(pathValue || '')});
        setClientUploadSourceDir(String(pathValue || ''));
        setStatus(clientUploadSourceDir ? '客户端来源目录已保存到本机' : '客户端来源目录已清空');
    } catch(error){
        setStatus(error?.message || '客户端设置保存失败');
    } finally {
        setClientSettingBusy(false);
    }
}
let activeSharedFolderId = '';
let localFolders = [];
function activeLocalLibraryPath(){ return activeLocalLibrary().path || ''; }
function isLocalMediaFile(file){
    if(!file) return false;
    const type = String(file.type || '').toLowerCase();
    if(type.startsWith('image/') || type.startsWith('video/') || type.startsWith('audio/')) return true;
    return LOCAL_MEDIA_EXTS.test(file.name || '');
}
function localAssetThumb(item){
    return assetThumb({url:localObjectUrl(item), name:item?.name || 'local', kind:item?.kind || localItemKind(item)});
}
function activeLocalFolder(){
    return localFolderMap.get(activeLocalFolderId) || localFolders[0] || null;
}
function localItemsForFolder(folderId=activeLocalFolderId){
    const query = localQuery.trim().toLowerCase();
    const folder = localFolderMap.get(folderId) || activeLocalFolder();
    const items = folder?.items || [];
    return items.filter(item => {
        if(!query) return true;
        return [item.name, item.relativePath, assetKindLabel(item)].join(' ').toLowerCase().includes(query);
    });
}
function normalizeLocalState(){
    if(!activeLocalFolderId || !localFolderMap.has(activeLocalFolderId)) setActiveLocalFolderId(localFolders[0]?.id || '');
    const items = localItemsForFolder();
    if(selectedLocalId && !localItemMap.has(selectedLocalId)) setSelectedLocalId('');
    if(!selectedLocalId && items.length) setSelectedLocalId(items[0].id);
    setSelectedLocalIds(new Set([...selectedLocalIds].filter(id => localItemMap.has(id))));
}
function localFolderTotal(folder){
    if(!folder) return 0;
    return (folder.items || []).length + (folder.children || []).reduce((sum, child) => sum + localFolderTotal(child), 0);
}
function localFolderId(path=''){
    return path || '__root__';
}
function localChildPath(parentPath='', name=''){
    return parentPath ? `${parentPath}/${name}` : name;
}
// ---------------- 共享文件夹（服务端登记 + 只读浏览/引用，局域网可用） ----------------
async function loadSharedFolders(){
    try {
        const data = await apiJsonResponse(assetManagerApi.getSharedFolders());
        setSharedFolders(Array.isArray(data.folders) ? data.folders : []);
    } catch(err) {
        setSharedFolders([]);
    }
    return sharedFolders;
}
function renderLocalLibraryTabs(){
    const libs = localLibraries.length ? localLibraries : [{id:'local', kind:'local', name:DEFAULT_LOCAL_LIBRARY_NAME, path:''}];
    return `<div class="local-library-strip">
        <div class="local-library-tabs" data-local-library-tabs>
            ${libs.map(lib => `<button class="local-library-tab ${lib.id === activeLocalLibraryId ? 'active' : ''} ${lib.id === localLibraryDragId ? 'dragging' : ''}" type="button" draggable="true" data-local-library-tab="${escapeAttr(lib.id)}" data-local-library-drag="${escapeAttr(lib.id)}" title="${escapeAttr(lib.path || '')}">
                <i data-lucide="folder"></i><span>${escapeHtml(storageCleanName(lib.name, DEFAULT_LOCAL_LIBRARY_NAME))}</span>
            </button>`).join('')}
        </div>
        <button class="hw-btn hw-btn-icon" type="button" data-local-library-rename title="修改素材库名称"><i data-lucide="pencil"></i></button>
    </div>`;
}
async function switchLocalLibrary(id){
    if(!id || id === activeLocalLibraryId) return;
    setActiveLocalLibraryId(id);
    setActiveLocalUploadFolder('');
    setActiveLocalUploadClassFilter('');
    setOpenLocalUploadClassGroup('');
    setSelectedLocalUploadId('');
    selectedLocalUploadIds.clear();
    gwSetLocalUploadClipboard(null);
    await loadLocalAssets();
    render();
}
async function renameActiveLocalLibrary(){
    const lib = activeLocalLibrary();
    if(!lib?.id) return;
    const name = window.prompt('素材库名称', lib.name || DEFAULT_LOCAL_LIBRARY_NAME);
    if(!String(name || '').trim()) return;
    setLocalLibraries(localLibraries.map(item => item.id === lib.id ? {...item, name:storageCleanName(name, DEFAULT_LOCAL_LIBRARY_NAME)} : item));
    storageSettingsState.localLibraryNames = storageLocalLibraryNames();
    storageSettingsState.localLibraryNames[lib.id] = storageCleanName(name, DEFAULT_LOCAL_LIBRARY_NAME);
    await saveLocalLibrarySettings();
    render();
}
async function registerSharedFolder(){
    const tip = '请输入要登记的共享文件夹路径（必须位于项目目录内，例如 assets\\library 或 output）：';
    const path = window.prompt(tip, '');
    if(!String(path || '').trim()) return;
    try {
        setStatus('正在登记共享文件夹...');
        const data = await apiJsonResponse(assetManagerApi.createSharedFolder({path}));
        await loadSharedFolders();
        const folder = data.folder;
        if(folder?.id) await openSharedFolder(folder.id);
        else render();
        setStatus(`已登记「${folder?.name || '共享文件夹'}」`);
    } catch(err) {
        setStatus(err.message || '登记共享文件夹失败');
    }
}
async function unregisterSharedFolder(folderId){
    if(!folderId) return;
    try {
        await apiJsonResponse(assetManagerApi.deleteSharedFolder(folderId));
        if(activeSharedFolderId === folderId){
            activeSharedFolderId = '';
            setActiveSharedFolderName('');
            localFolders = [];
            localFolderMap = new Map();
            setLocalItemMap(new Map());
            setActiveLocalFolderId('');
            setSelectedLocalId('');
            selectedLocalIds.clear();
            gwSetLocalClipboard(null);
        }
        await loadSharedFolders();
        render();
        setStatus('已移除共享文件夹登记（不会删除磁盘文件）');
    } catch(err) {
        setStatus(err.message || '移除共享文件夹失败');
    }
}
function indexSharedTree(node){
    if(!node) return;
    localFolderMap.set(node.id, node);
    (node.items || []).forEach(item => localItemMap.set(item.id, item));
    (node.children || []).forEach(child => indexSharedTree(child));
}
async function openSharedFolder(folderId){
    if(!folderId) return;
    try {
        setStatus('正在读取共享文件夹...');
        const data = await apiJsonResponse(assetManagerApi.getSharedFolderTree(folderId));
        const tree = data.tree;
        localFolders = tree ? [tree] : [];
        localFolderMap = new Map();
        setLocalItemMap(new Map());
        if(tree) indexSharedTree(tree);
        activeSharedFolderId = folderId;
        setActiveSharedFolderName(data.folder?.name || tree?.name || '共享文件夹');
        setActiveLocalFolderId(tree?.id || '');
        setSelectedLocalId('');
        selectedLocalIds.clear();
        gwSetLocalClipboard(null);
        normalizeLocalState();
        render();
        setStatus(`已读取「${activeSharedFolderName}」`);
    } catch(err) {
        setStatus(err.message || '读取共享文件夹失败');
    }
}
function syncLocalSelectionDom(){
    document.querySelectorAll('[data-local-card]').forEach(card => {
        card.classList.toggle('active', card.dataset.localCard === selectedLocalId);
    });
    document.querySelectorAll('[data-local-check]').forEach(input => {
        input.checked = selectedLocalIds.has(input.dataset.localCheck);
    });
    const detail = root.querySelector('.asset-detail');
    if(detail){ detail.innerHTML = renderLocalDetail(findLocalItem(selectedLocalId)); refreshIcons(); }
}
function renderLocalLibrarySources(){
    const libs = localLibraries.length ? localLibraries : [{id:'local', kind:'local', name:DEFAULT_LOCAL_LIBRARY_NAME, path:''}];
    return `<section class="library-source-section local-source-section">
        <div class="registry-facet-title"><strong>上传来源</strong><button type="button" data-board-source-add="" title="添加素材路径"><i data-lucide="folder-plus"></i></button></div>
        <div class="library-source-list">${libs.map(lib => `<button class="library-source-row ${lib.id === activeLocalLibraryId ? 'active' : ''}" type="button" data-local-library-tab="${escapeAttr(lib.id)}" title="${escapeAttr(lib.path || '')}"><span><i data-lucide="${lib.id === 'local' ? 'cloud-upload' : 'folder'}"></i></span><b>${escapeHtml(storageCleanName(lib.name, DEFAULT_LOCAL_LIBRARY_NAME))}</b><small>${lib.id === activeLocalLibraryId ? localAssetsTotal : customBoardSourceCount(lib.id)}</small></button>`).join('')}</div>
    </section>`;
}
function localUploadItems(){
    const q = String(localUploadQuery || '').trim().toLowerCase();
    let list = Array.isArray(localAssets) ? localAssets.slice() : [];
    if(activeLocalUploadClassFilter) list = localUploadItemsForClassFilter(activeLocalUploadClassFilter);
    else if(activeLocalUploadFolder && !q) list = list.filter(it => String(it.folder || '') === activeLocalUploadFolder);
    if(q) list = list.filter(it => {
        return [it.name, it.file, assetKindLabel(it), assetClassificationSearchText(it)].join(' ').toLowerCase().includes(q);
    });
    return list;
}
function localUploadFolderByPath(path=''){
    const target = String(path || '');
    let match = null;
    function walk(node){
        if(!node || match) return;
        if(String(node.path || '') === target){ match = node; return; }
        (node.children || []).forEach(walk);
    }
    walk(localUploadTree);
    return match;
}
function localUploadFolderTitle(){
    const entry = activeLocalUploadClassEntry();
    if(entry) return entry.tag || '智能分类';
    if(!activeLocalUploadFolder) return activeLocalLibraryName();
    return localUploadFolderByPath(activeLocalUploadFolder)?.name || activeLocalUploadFolder.split('/').pop() || activeLocalLibraryName();
}
function localUploadViewSubtitle(items){
    const entry = activeLocalUploadClassEntry();
    if(entry) return `智能分类：${entry.label} / ${items.length} 个素材`;
    return `${items.length} / ${localAssetsTotal || (localAssets || []).length} 个素材`;
}
function localUploadItemsForClassFilter(key=activeLocalUploadClassFilter){
    const parsed = parseAssetClassFilterKey(key);
    if(!parsed) return [];
    return (localAssets || []).filter(item => {
        const flat = Array.isArray(item?.classification?.flat) ? item.classification.flat : [];
        return flat.some(entry => String(entry.dimension || '') === parsed.dimension && String(entry.tag || '') === parsed.tag);
    });
}
function renderLocalUploadSmartClassTree(){
    const entries = localUploadClassEntries();
    const groups = groupedAssetClassificationEntries(entries);
    const activeEntry = activeLocalUploadClassEntry();
    const activeGroup = activeEntry ? assetClassificationEntryGroup(activeEntry)?.id : '';
    return `<div class="tree-smart-class">
        <button class="tree-row tree-parent ${activeLocalUploadClassFilter ? 'contains-active' : ''}" type="button" data-localup-class-root>
            <span class="tree-row-icon"><i data-lucide="tags"></i></span>
            <span class="tree-row-name">智能分类</span>
            <span class="tree-row-count">${groups.length}</span>
        </button>
        ${groups.length ? groups.map(group => `<div class="smart-class-group">
            <button class="smart-class-group-btn ${openLocalUploadClassGroup === group.id ? 'open' : ''} ${activeGroup === group.id ? 'active' : ''}" type="button" data-localup-class-group="${escapeAttr(group.id)}">
                <span>${escapeHtml(group.name)}</span><small>${group.entries.length}</small><i data-lucide="${openLocalUploadClassGroup === group.id ? 'chevron-up' : 'chevron-down'}"></i>
            </button>
            ${openLocalUploadClassGroup === group.id ? `<div class="smart-class-pills">
                ${group.entries.map(entry => `<button class="smart-class-pill ${activeLocalUploadClassFilter === entry.key ? 'active' : ''}" type="button" data-localup-class-filter="${escapeAttr(entry.key)}" title="${escapeAttr(entry.label)}">
                    <span>${escapeHtml(entry.tag)}</span><small>${entry.count}</small>
                </button>`).join('')}
            </div>` : ''}
        </div>`).join('') : '<div class="tree-empty">暂无分类，先选择图片点“智能分类”</div>'}
    </div>`;
}
function selectedLocalUploadImageItems(){
    return [...selectedLocalUploadIds].map(id => findLocalUpload(id)).filter(item => item && assetKind(item) === 'image');
}
function renderLocalAssetsPagination(){
    if(localAssetsError){
        return `<div class="tree-empty" role="status"><span>${escapeHtml(localAssetsError)}</span><button class="hw-btn" type="button" data-localup-retry>重试</button></div>`;
    }
    if(localAssetsLoading){
        return `<div class="tree-empty" role="status">正在加载素材…</div>`;
    }
    if(!localAssets.length && localAssetsLoaded){
        return '<div class="tree-empty" role="status">暂无本地素材</div>';
    }
    if(localAssetsHasMore){
        return `<div class="tree-empty" role="status">已加载 ${localAssets.length} / ${localAssetsTotal} 个素材 <button class="hw-btn" type="button" data-localup-load-more>加载更多</button></div>`;
    }
    return localAssets.length
        ? `<div class="tree-empty" role="status">已加载全部 ${localAssetsTotal} 个素材</div>`
        : '';
}
function renderLocalManager(){
    normalizeLocalCaptionSettings();
    const items = localUploadItems();
    // 只有当选中的素材"真的不存在了"(被删除)才清空；只是被分类/筛选挡住时仍保留预览，
    // 这样切换分类时中间预览不会跳回第一张，方便对照着找相似图片。
    if(selectedLocalUploadId && !findLocalUpload(selectedLocalUploadId)) setSelectedLocalUploadId('');
    if(!selectedLocalUploadId && items.length) setSelectedLocalUploadId(items[0].id);
    const detail = findLocalUpload(selectedLocalUploadId);
    const total = localAssetsTotal || (localAssets || []).length;
    const imageCount = selectedLocalUploadImageItems().length;
    const folderTitle = localUploadFolderTitle();
    root.innerHTML = `
        ${renderAssetTreePanel()}
        <section class="asset-panel asset-content ${localUploadManageMode ? 'manage-on' : ''}">
            <div class="content-toolbar">
                <div class="content-heading">
                    <strong>${escapeHtml(folderTitle)}</strong>
                    <span>${localUploadViewSubtitle(items)}</span>
                </div>
                <div class="asset-tools">
                    <label class="asset-search-wrap"><i data-lucide="search"></i><input id="localUploadSearch" class="asset-search" type="search" value="${escapeAttr(localUploadQuery)}" placeholder="搜索本地上传"></label>
                    <button class="hw-btn hw-btn-gold" type="button" data-localup-upload><i data-lucide="upload"></i><span>上传文件</span></button>
                    <button class="hw-btn" type="button" data-asset-collect-open><i data-lucide="cloud-download"></i><span>网络收集</span></button>
                    <button class="${localUploadManageMode ? 'hw-btn hw-btn-gold' : 'hw-btn'}" type="button" data-localup-manage ${total ? '' : 'disabled'}><i data-lucide="list-checks"></i><span>${localUploadManageMode ? '完成管理' : '批量管理'}</span></button>
                </div>
            </div>
            <div class="manage-tools local-manage-tools">
                <div class="manage-group manage-select-group">
                    <span class="manage-group-title">管理</span>
                    <span class="manage-summary">已选 ${selectedLocalUploadIds.size} 个，其中 ${imageCount} 张图片</span>
                    <div class="asset-tools local-manage-actions">
                        <button class="hw-btn" type="button" data-localup-select-all ${items.length ? '' : 'disabled'}><i data-lucide="check-square"></i><span>全选已加载</span></button>
                        <button class="hw-btn" type="button" data-localup-clear ${selectedLocalUploadIds.size ? '' : 'disabled'}><i data-lucide="square"></i><span>清空</span></button>
                        <button class="hw-btn" type="button" data-localup-download-selected ${selectedLocalUploadIds.size ? '' : 'disabled'}><i data-lucide="download"></i><span>下载</span></button>
                        <button class="hw-btn" type="button" data-localup-canvas-selected ${selectedLocalUploadIds.size ? '' : 'disabled'}><i data-lucide="clipboard-paste"></i><span>复制到画布</span></button>
                        <button class="hw-btn" type="button" data-localup-cut-selected ${selectedLocalUploadIds.size ? '' : 'disabled'}><i data-lucide="scissors"></i><span>剪切/移动</span></button>
                        <button class="hw-btn hw-btn-danger" type="button" data-localup-delete-selected ${selectedLocalUploadIds.size ? '' : 'disabled'}><i data-lucide="trash-2"></i><span>删除</span></button>
                    </div>
                </div>
                <div class="manage-group manage-ai-group">
                    <span class="manage-group-title">AI 处理</span>
                    ${renderLocalCaptionTools(imageCount)}
                </div>
            </div>
            ${renderLocalUploadClipboardBar()}
            <div class="content-scroll">
                <div class="asset-grid">
                    ${renderLocalUploadAddCard()}
                    ${items.map(item => renderLocalUploadCard(item)).join('')}
                </div>
                ${renderLocalAssetsPagination()}
            </div>
        </section>
        <aside class="asset-panel asset-detail">
            ${renderLocalUploadDetail(detail)}
        </aside>
    `;
}
function renderLocalUploadAddCard(){
    return `<button id="localUploadDrop" class="upload-grid-card" type="button" data-localup-upload>
        <span class="upload-thumb"><i data-lucide="upload-cloud"></i></span>
        <span class="upload-body">
            <strong>上传本地素材</strong>
            <small>拖入文件或点击上传</small>
        </span>
    </button>`;
}
function renderLocalUploadCard(item){
    const hasCaption = assetKind(item) === 'image' && String(item.caption || '').trim();
    return `<article class="asset-card ${item.id === selectedLocalUploadId ? 'active' : ''}" data-localup-card="${escapeAttr(item.id)}" tabindex="0" role="button" aria-label="查看 ${escapeAttr(item.name || '上传资源')} 详情">
        <input class="asset-card-check" type="checkbox" data-localup-check="${escapeAttr(item.id)}" ${selectedLocalUploadIds.has(item.id) ? 'checked' : ''}>
        <div class="asset-thumb">${assetThumb(item)}</div>
        <div class="asset-card-body">
            <div class="asset-card-name" data-localup-rename="${escapeAttr(item.id)}" title="${escapeAttr(item.name || '')}">${escapeHtml(item.name || '本地素材')}</div>
            <div class="asset-card-meta">${escapeHtml(assetKindLabel(item))} · ${escapeHtml(formatFileSize(item.size))}${hasCaption ? ' · 有提示词' : ''}</div>
        </div>
    </article>`;
}
function renderLocalUploadDetail(item){
    if(!item) return `<div class="panel-head"><div class="panel-title"><strong>素材预览</strong><span>选择一个素材查看详情</span></div></div><div class="detail-scroll"><div class="detail-empty"><i data-lucide="image"></i><span>暂无可预览素材</span></div></div>`;
    const isImage = assetKind(item) === 'image';
    return `
        <div class="panel-head">
            <div class="panel-title"><strong>素材预览</strong><span>${escapeHtml(assetKindLabel(item))}</span></div>
            <div class="panel-actions">
                <button class="hw-btn hw-btn-icon" type="button" data-localup-rename="${escapeAttr(item.id)}" title="重命名"><i data-lucide="pencil"></i></button>
                <button class="hw-btn hw-btn-icon" type="button" data-localup-download="${escapeAttr(item.id)}" title="下载"><i data-lucide="download"></i></button>
                <button class="hw-btn hw-btn-icon" type="button" data-localup-open="${escapeAttr(item.id)}" title="新窗口打开"><i data-lucide="external-link"></i></button>
                <button class="hw-btn hw-btn-icon" type="button" data-localup-copy="${escapeAttr(item.id)}" title="复制链接"><i data-lucide="link"></i></button>
                <button class="hw-btn hw-btn-icon hw-btn-danger" type="button" data-localup-delete-one="${escapeAttr(item.id)}" title="删除"><i data-lucide="trash-2"></i></button>
            </div>
        </div>
        <div class="detail-scroll">
            <div class="detail-media"><button class="detail-media-frame detail-media-zoomable" type="button" data-localup-preview="${escapeAttr(item.id)}" title="点击放大预览">${assetThumb(item)}</button></div>
            <div class="detail-body">
                <input class="detail-name-input" data-localup-inline-name="${escapeAttr(item.id)}" type="text" value="${escapeAttr(item.name || '本地素材')}" title="直接修改名称">
                <div class="detail-meta-grid">
                    <div class="detail-meta"><span>类型</span><strong>${escapeHtml(assetKindLabel(item))}</strong></div>
                    <div class="detail-meta"><span>大小</span><strong>${escapeHtml(formatFileSize(item.size))}</strong></div>
                    <div class="detail-meta"><span>上传时间</span><strong>${escapeHtml(formatDate((item.created_at||0)*1000))}</strong></div>
                    <div class="detail-meta"><span>来源</span><strong>本地上传</strong></div>
                </div>
                <div class="detail-url">${escapeHtml(item.url || '')}</div>
                ${isImage ? `<div class="detail-caption-card">
                    <div class="detail-caption-head"><strong>智能分类</strong></div>
                    <div class="detail-classification-body">${renderClassificationChips(item, 28, {kind:'localup'}) || '<span class="classification-empty">暂无智能分类，可以上传时自动生成或后续批量分类。</span>'}</div>
                </div>` : ''}
                ${isImage ? `
                    <div class="detail-caption-card">
                        <div class="detail-caption-head">
                            <strong>反推提示词</strong>
                            <div class="detail-caption-actions">
                                <button class="hw-btn" type="button" data-localup-caption-one="${escapeAttr(item.id)}" ${localCaptionBusy || localClassifyBusy ? 'disabled' : ''}><i data-lucide="${localCaptionBusy ? 'loader-2' : 'wand-sparkles'}"></i><span>${localCaptionBusy ? '反推中' : '反推'}</span></button>
                                <button class="hw-btn" type="button" data-localup-caption-copy="${escapeAttr(item.id)}"><i data-lucide="copy"></i><span>复制</span></button>
                                <button class="hw-btn hw-btn-gold" type="button" data-localup-caption-save="${escapeAttr(item.id)}"><i data-lucide="save"></i><span>保存</span></button>
                            </div>
                        </div>
                        <textarea id="localUploadCaptionEdit" class="detail-caption-textarea" placeholder="暂无反推提示词，可以批量反推后自动写入，也可以在这里手动编辑。">${escapeHtml(item.caption || '')}</textarea>
                    </div>
                ` : ''}
            </div>
        </div>
    `;
}
function refreshLocalUploadSelectionOnly(){
    document.querySelectorAll('[data-localup-card]').forEach(card => {
        card.classList.toggle('active', card.dataset.localupCard === selectedLocalUploadId);
    });
    document.querySelectorAll('[data-localup-check]').forEach(input => {
        input.checked = selectedLocalUploadIds.has(input.dataset.localupCheck);
    });
    const detail = root.querySelector('.asset-detail');
    if(detail){
        detail.innerHTML = renderLocalUploadDetail(findLocalUpload(selectedLocalUploadId));
        refreshIcons();
    }
}
function renderLocalFolderBranch(folder, depth=0){
    const active = folder.id === activeLocalFolderId;
    const contains = !active && (folder.children || []).some(child => child.id === activeLocalFolderId || folderContainsLocalActive(child));
    return `<div class="tree-branch">
        <button class="tree-row ${depth ? 'tree-child' : 'tree-parent'} ${active ? 'active' : ''} ${contains ? 'contains-active' : ''}" type="button" data-local-folder="${escapeAttr(folder.id)}">
            ${depth ? '<span class="tree-elbow"></span>' : ''}
            <span class="tree-row-icon"><i data-lucide="${active ? 'folder-open' : 'folder'}"></i></span>
            <span class="tree-row-name">${escapeHtml(folder.name || '文件夹')}</span>
            <span class="tree-row-count">${localFolderTotal(folder)}</span>
        </button>
        ${(folder.children || []).length ? `<div class="tree-children">${folder.children.map(child => renderLocalFolderBranch(child, depth + 1)).join('')}</div>` : ''}
    </div>`;
}
function folderContainsLocalActive(folder){
    if(!folder) return false;
    if(folder.id === activeLocalFolderId) return true;
    return (folder.children || []).some(child => folderContainsLocalActive(child));
}
function renderLocalCard(item){
    const hasCaption = localItemKind(item) === 'image' && String(item.caption || '').trim();
    return `<article class="asset-card ${item.id === selectedLocalId ? 'active' : ''}" data-local-card="${escapeAttr(item.id)}" tabindex="0" role="button" aria-label="查看 ${escapeAttr(item.name || '素材')} 详情">
        <input class="asset-card-check" type="checkbox" data-local-check="${escapeAttr(item.id)}" ${selectedLocalIds.has(item.id) ? 'checked' : ''}>
        <div class="asset-thumb">${localAssetThumb(item)}</div>
        <div class="asset-card-body">
            <div class="asset-card-name" title="${escapeAttr(item.relativePath || item.name || '')}">${escapeHtml(item.name || 'local')}</div>
            <div class="asset-card-meta">${escapeHtml(assetKindLabel(item))} · ${escapeHtml(formatFileSize(item.size))}${hasCaption ? ' · 有提示词' : ''}</div>
        </div>
    </article>`;
}
function renderLocalClipboardBar(){
    if(!localClipboard?.items?.length) return '';
    const modeLabel = localClipboard.mode === 'cut' ? '剪切' : '复制';
    const target = activeAssetCategory();
    return `<div class="asset-clipboard-bar">
        <div class="asset-clipboard-info"><i data-lucide="clipboard"></i><span>${escapeHtml(modeLabel)}了 ${localClipboard.items.length} 个本地素材，目标：${escapeHtml(activeAssetLibrary()?.name || '图片资产')} / ${escapeHtml(target?.name || '未选择分组')}</span></div>
        <div class="asset-tools">
            <button class="hw-btn hw-btn-gold" type="button" data-local-import-clipboard ${target ? '' : 'disabled'}><i data-lucide="clipboard-paste"></i><span>导入到图片资产</span></button>
            <button class="hw-btn hw-btn-icon" type="button" data-local-clear-clipboard title="清空本地剪贴板"><i data-lucide="x"></i></button>
        </div>
    </div>`;
}
function renderLocalDetail(item){
    if(!item) return `<div class="panel-head"><div class="panel-title"><strong>本地预览</strong><span>选择一个本地素材查看详情</span></div></div><div class="detail-scroll"><div class="detail-empty"><i data-lucide="folder-open"></i><span>暂无可预览素材</span></div></div>`;
    return `
        <div class="panel-head">
            <div class="panel-title"><strong>本地预览</strong><span>${escapeHtml(assetKindLabel(item))}</span></div>
            <div class="panel-actions">
                <button class="hw-btn hw-btn-icon" type="button" data-local-open="${escapeAttr(item.id)}" title="打开预览"><i data-lucide="external-link"></i></button>
                <button class="hw-btn hw-btn-gold" type="button" data-local-import-one="${escapeAttr(item.id)}"><i data-lucide="download"></i><span>导入</span></button>
            </div>
        </div>
        <div class="detail-scroll">
            <div class="detail-media"><button class="detail-media-frame detail-media-zoomable" type="button" data-local-preview="${escapeAttr(item.id)}" title="点击放大预览">${localAssetThumb(item)}</button></div>
            <div class="detail-body">
                <div class="detail-name">${escapeHtml(item.name || '本地素材')}</div>
                <div class="detail-meta-grid">
                    <div class="detail-meta"><span>类型</span><strong>${escapeHtml(assetKindLabel(item))}</strong></div>
                    <div class="detail-meta"><span>大小</span><strong>${escapeHtml(formatFileSize(item.size))}</strong></div>
                    <div class="detail-meta"><span>修改时间</span><strong>${escapeHtml(formatDate(item.lastModified))}</strong></div>
                    <div class="detail-meta"><span>来源</span><strong>${escapeHtml(activeSharedFolderName || '共享文件夹')}</strong></div>
                </div>
                <div class="detail-url">${escapeHtml(item.relativePath || item.name || '')}</div>
            </div>
        </div>
    `;
}
function customBoardSourceCount(sourceId){
    return Number((registryFacets.roots || []).find(item => String(item.id || '') === String(sourceId || ''))?.count || 0);
}
function renderWorkflowUploadCard(cat){
    return `<button id="workflowDrop" class="upload-grid-card" type="button" data-workflow-upload ${!cat ? 'disabled' : ''}>
        <span class="upload-thumb"><i data-lucide="upload-cloud"></i></span>
        <span class="upload-body">
            <strong>上传工作流</strong>
            <small>支持 JSON / ZIP</small>
        </span>
    </button>`;
}
function renderUploadCard(cat){
    return `<button id="assetDrop" class="upload-grid-card" type="button" data-asset-upload ${!cat ? 'disabled' : ''}>
        <span class="upload-thumb"><i data-lucide="upload-cloud"></i></span>
        <span class="upload-body">
            <strong>上传到当前分组</strong>
            <small>拖入文件或点击上传</small>
        </span>
    </button>`;
}
async function uploadFiles(files){
    const cat = activeAssetCategory();
    if(!cat) throw new Error('请先创建图片分组');
    const form = new FormData();
    [...files].forEach(file => form.append('files', file));
    const uploaded = await apiJsonResponse(assetManagerApi.uploadAiFiles(form));
    const items = (uploaded.files || []).filter(file => file?.url).map(file => ({
        library_id:activeAssetLibraryId,
        category_id:activeAssetCategoryId,
        url:file.url,
        name:file.name || 'asset'
    }));
    if(!items.length) throw new Error('没有可保存的素材');
    const data = await apiJsonResponse(assetManagerApi.createAssetLibraryItemsBatch({library_id:activeAssetLibraryId, category_id:activeAssetCategoryId, items, expected_version:requireExpectedVersion(activeAssetLibrary()?.version, '素材库')}));
    setAssetLibrary(data.library || assetLibrary);
    selectedAssetIds.clear();
    setSelectedAssetId(data.items?.[0]?.id || selectedAssetId);
    render();
    setStatus(`已上传 ${items.length} 个素材`);
    return {count:items.length, items:data.items || []};
}
async function uploadWorkflowFiles(files){
    const cat = activeWorkflowCategory();
    if(!cat) throw new Error('请先创建工作流分组');
    const list = [...files].filter(file => /\.(json|zip)$/i.test(file.name || '') || ['application/json','application/zip','application/x-zip-compressed'].includes(String(file.type || '').toLowerCase()));
    if(!list.length) throw new Error('没有可上传的工作流文件');
    const form = new FormData();
    form.append('library_id', activeWorkflowLibraryId || '');
    form.append('category_id', activeWorkflowCategoryId || '');
    list.forEach(file => form.append('files', file));
    const data = await apiJsonResponse(assetManagerApi.uploadAssetLibraryWorkflows(form));
    setAssetLibrary(data.library || assetLibrary);
    selectedWorkflowIds.clear();
    setSelectedWorkflowId(data.items?.[0]?.id || selectedWorkflowId);
    render();
    setStatus(`已上传 ${data.items?.length || 0} 个工作流`);
}
function downloadLocalUpload(id){
    const item = findLocalUpload(id);
    if(!item || !item.url){ setStatus('没有可下载的素材'); return; }
    const name = assetDownloadName(item);
    downloadUrl(`/api/download-output?url=${encodeURIComponent(item.url)}&name=${encodeURIComponent(name)}`, name);
    setStatus('已开始下载');
}
async function downloadSelectedLocalUploads(){
    const items = [...selectedLocalUploadIds].map(id => findLocalUpload(id)).filter(it => it?.url);
    if(!items.length){ setStatus('没有可下载的素材'); return; }
    if(items.length === 1){ downloadLocalUpload(items[0].id); return; }
    setStatus(`正在打包 ${items.length} 个素材...`);
    try {
        const res = await assetManagerApi.downloadCanvasAssets({filename:'local-assets.zip', items:items.map(it => ({url:it.url, name:assetDownloadName(it)}))});
        if(!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || '下载失败');
        const blob = await res.blob();
        const link = document.createElement('a');
        link.href = URL.createObjectURL(blob);
        link.download = 'local-assets.zip';
        document.body.appendChild(link);
        link.click();
        link.remove();
        setTimeout(() => URL.revokeObjectURL(link.href), 1200);
        setStatus(`已下载 ${items.length} 个素材`);
    } catch(err){
        setStatus(err.message || '下载失败');
    }
}
function renderLocalUploadClipboardBar(){
    if(!localUploadClipboard?.ids?.length) return '';
    const sameFolder = String(localUploadClipboard.sourceFolder || '') === String(activeLocalUploadFolder || '');
    const hereLabel = activeLocalUploadFolder ? localUploadFolderTitle() : '根目录';
    return `<div class="asset-clipboard-bar">
        <div class="asset-clipboard-info"><i data-lucide="clipboard"></i><span>已剪切 ${localUploadClipboard.ids.length} 个素材</span></div>
        <div class="asset-tools">
            <button class="hw-btn hw-btn-gold" type="button" data-localup-paste-clipboard ${sameFolder ? 'disabled' : ''}><i data-lucide="clipboard-paste"></i><span>${sameFolder ? '切到其他文件夹后移动' : `移动到「${escapeHtml(hereLabel)}」`}</span></button>
            <button class="hw-btn hw-btn-icon" type="button" data-localup-clear-clipboard title="取消剪切"><i data-lucide="x"></i></button>
        </div>
    </div>`;
}
function setLocalUploadClipboard(){
    const ids = [...selectedLocalUploadIds];
    if(!ids.length){ setStatus('没有选中的素材'); return; }
    gwSetLocalUploadClipboard({ids, sourceFolder: activeLocalUploadFolder || ''});
    selectedLocalUploadIds.clear();
    render();
    setStatus(`已剪切 ${ids.length} 个素材，切换到目标文件夹后点"移动到此文件夹"`);
}
async function pasteLocalUploadClipboard(){
    if(!localUploadClipboard?.ids?.length) return;
    const names = localUploadClipboard.ids.map(id => findLocalUpload(id)?.file).filter(Boolean);
    if(!names.length){ gwSetLocalUploadClipboard(null); render(); return; }
    setStatus('正在移动...');
    try {
        const data = await apiJsonResponse(assetManagerApi.moveLocalAssets({kind:activeLocalLibraryKind(), names, folder:activeLocalUploadFolder || ''}));
        await loadLocalAssets();
        gwSetLocalUploadClipboard(null);
        selectedLocalUploadIds.clear();
        render();
        setStatus(localAssetBatchStatus('移动',Number(data?.moved || 0),data));
    } catch(err){
        setStatus(err.message || '移动失败');
    }
}
async function uploadViaClientPicker(){
    // 客户端模式：以已保存的客户端来源目录为默认来源，弹客户端本机对话框选文件。
    const invoke = tauriInvokeFn();
    if(!invoke){ uploadInput?.click(); return; }
    let paths = [];
    try {
        paths = await invoke('pick_client_upload_files', {defaultDir: clientUploadSourceDir || null}) || [];
    } catch(error){
        setStatus(error?.message || '客户端文件选择失败');
        return;
    }
    if(!Array.isArray(paths) || !paths.length) return;
    try {
        const files = [];
        for(const path of paths){
            const bytes = await invoke('read_client_file', {path: String(path)});
            const name = String(path).split(/[\\/]/).pop() || 'asset';
            files.push(new File([bytes], name));
        }
        if(!files.length) return;
        if(activeTab === 'local') uploadLocalAssets(files);
        else if(activeTab === 'workflows') uploadWorkflowFiles(files).catch(err => setStatus(err.message || '上传失败'));
        else uploadFiles(files).catch(err => setStatus(err.message || '上传失败'));
    } catch(error){
        setStatus(error?.message || '客户端文件读取失败');
    }
}
async function uploadLocalAssets(files){
    const list = [...files].filter(f => isLocalMediaFile(f));
    if(!list.length){ setStatus('没有可上传的受支持文件'); return; }
    const form = new FormData();
    form.append('path', activeLocalLibraryPath());
    form.append('folder', activeLocalUploadFolder || '');
    form.append('kind', activeLocalLibraryKind());
    list.forEach(file => form.append('files', file));
    setStatus('正在上传...');
    try {
        const data = await apiJsonResponse(assetManagerApi.uploadLocalAssets(form));
        const uploaded = Array.isArray(data.files) ? data.files : [];
        await loadLocalAssets();
        setActiveLocalUploadClassFilter('');
        setSelectedLocalUploadId(uploaded[0]?.id || selectedLocalUploadId);
        render();
        setStatus(`已上传 ${uploaded.length} 个素材`);
    } catch(err) {
        setStatus(err.message || '上传失败');
    }
}
async function deleteLocalAssets(ids){
    const names = (ids || []).map(id => findLocalUpload(id)?.file).filter(Boolean);
    if(!names.length) return;
    setStatus('正在删除...');
    try {
        const data = await apiJsonResponse(assetManagerApi.deleteLocalAssets({kind:activeLocalLibraryKind(), names}));
        await loadLocalAssets();
        if(activeLocalUploadClassFilter && !activeLocalUploadClassEntry()) setActiveLocalUploadClassFilter('');
        selectedLocalUploadIds.clear();
        if(selectedLocalUploadId && !findLocalUpload(selectedLocalUploadId)) setSelectedLocalUploadId('');
        render();
        setStatus(localAssetBatchStatus('删除',Array.isArray(data?.deleted) ? data.deleted.length : 0,data));
    } catch(err) {
        setStatus(err.message || '删除失败');
    }
}
async function saveLocalUploadInlineName(id, name){
    const item = findLocalUpload(id);
    if(!item) return;
    if(!String(name || '').trim()) return;
    if(String(item.name || '') === String(name || '')) return;
    try {
        const data = await apiJsonResponse(assetManagerApi.updateLocalAsset({kind:activeLocalLibraryKind(), path:item.file || item.id, name}));
        setLocalAssets(Array.isArray(data.items) ? data.items : localAssets);
        setLocalUploadTree(data.tree || localUploadTree);
        setSelectedLocalUploadId(data.item?.id || data.item?.file || selectedLocalUploadId);
        if(selectedLocalUploadIds.has(id)){
            selectedLocalUploadIds.delete(id);
            if(data.item?.id) selectedLocalUploadIds.add(data.item.id);
        }
        render();
        setStatus('已重命名素材，反推提示词和分类索引已同步');
    } catch(err) {
        setStatus(err.message || '重命名失败');
    }
}
function beginLocalUploadInlineRename(id){
    const item = findLocalUpload(id);
    const card = [...root.querySelectorAll('[data-localup-card]')].find(el => el.dataset.localupCard === id);
    const nameEl = card?.querySelector('.asset-card-name');
    if(!item || !card || !nameEl || card.querySelector('.asset-card-name-input')) return;
    const previousName = item.name || '本地素材';
    const input = document.createElement('input');
    input.className = 'asset-card-name-input';
    input.type = 'text';
    input.value = previousName;
    input.setAttribute('aria-label', '素材名称');
    nameEl.replaceWith(input);
    input.focus();
    input.select();
    let done = false;
    const restore = () => {
        if(input.isConnected) input.replaceWith(nameEl);
    };
    const finish = async save => {
        if(done) return;
        done = true;
        const name = input.value.trim();
        if(!save || !name || name === previousName){
            restore();
            return;
        }
        input.disabled = true;
        await saveLocalUploadInlineName(id, name);
    };
    input.addEventListener('keydown', event => {
        event.stopPropagation();
        if(event.key === 'Enter'){
            event.preventDefault();
            finish(true);
        } else if(event.key === 'Escape'){
            event.preventDefault();
            finish(false);
        }
    });
    input.addEventListener('pointerdown', event => event.stopPropagation());
    input.addEventListener('mousedown', event => event.stopPropagation());
    input.addEventListener('click', event => event.stopPropagation());
    input.addEventListener('blur', () => finish(true));
}
async function createLocalUploadFolder(){
    const name = window.prompt('新建文件夹名称', '新文件夹');
    if(!String(name || '').trim()) return;
    try {
        const data = await apiJsonResponse(assetManagerApi.createLocalAssetFolder({kind:activeLocalLibraryKind(), parent:activeLocalUploadFolder || '', name}));
        setLocalAssets(Array.isArray(data.items) ? data.items : localAssets);
        setLocalUploadTree(data.tree || localUploadTree);
        setActiveLocalUploadFolder(data.folder?.path || activeLocalUploadFolder);
        setActiveLocalUploadClassFilter('');
        setSelectedLocalUploadId('');
        selectedLocalUploadIds.clear();
        render();
        setStatus('已新建本地素材文件夹');
    } catch(err) {
        setStatus(err.message || '新建文件夹失败');
    }
}
async function renameLocalUploadFolder(){
    if(!activeLocalUploadFolder){
        setStatus('根目录不能重命名');
        return;
    }
    const current = localUploadFolderTitle();
    const name = window.prompt('重命名文件夹', current);
    if(!String(name || '').trim() || name === current) return;
    try {
        const currentFolderPath = String(activeLocalUploadFolder).replace(/\\/g, '/');
        const data = await apiJsonResponse(assetManagerApi.updateLocalAssetFolder({kind:activeLocalLibraryKind(), root_path:activeLocalLibraryPath(), name:currentFolderPath, new_name:name}));
        setLocalAssets(Array.isArray(data.items) ? data.items : localAssets);
        setLocalUploadTree(data.tree || localUploadTree);
        setActiveLocalUploadFolder(data.folder?.path || activeLocalUploadFolder);
        setActiveLocalUploadClassFilter('');
        setSelectedLocalUploadId('');
        selectedLocalUploadIds.clear();
        render();
        setStatus('已重命名本地素材文件夹');
    } catch(err) {
        setStatus(err.message || '重命名文件夹失败');
    }
}
async function runCustomBoardClassifySelected(){
    const board = assetBoardById(activeTab);
    const sourceId = board?.sourceBoard ? String(board.rootId || '') : (boardSourceSelection[board?.id] || '');
    const images = customAssetLibraryItems(board).filter(item => registrySelectedIds.has(item.id) && item.kind === 'image');
    if(!board || !sourceId || !images.length || localClassifyBusy) return;
    normalizeLocalCaptionSettings();
    if(!localCaptionProvider || !localCaptionModel){
        setStatus('请先在资产设置的”AI 分类设置”中配置模型');
        return;
    }
    setLocalClassifyBusy(true);
    render();
    setStatus(`正在智能分类 ${images.length} 张图片...`);
    try {
        const data = await apiJsonResponse(assetManagerApi.classifyLocalAssets({
                kind:sourceId,
                names:images.map(item => item.relative_path || item.name || item.id),
                provider:localCaptionProvider,
                model:localCaptionModel,
                prompt:(localClassifyPrompt || '').trim()
        }));
        await reindexAssetSource(sourceId);
        await refreshRegistryAssets();
        const failed = (data.items || []).filter(item => !item.ok);
        setStatus(failed.length ? `处理完成 ${data.count || 0} 张，${failed.length} 张失败：${failed[0].error || '分类失败'}` : `已完成 ${data.count || images.length} 张图片的 AI 分类与标签`);
    } catch(err){
        setStatus(err.message || '智能分类失败');
    } finally {
        setLocalClassifyBusy(false);
        render();
    }
}
function setLocalClipboard(mode, ids=null){
    // 共享文件夹只读，仅支持「复制」（引用导入），不支持剪切删除源文件
    const sourceIds = Array.isArray(ids) ? ids.filter(Boolean) : [...selectedLocalIds];
    if(!sourceIds.length) return;
    const items = sourceIds.map(id => findLocalItem(id)).filter(Boolean);
    if(!items.length) return;
    gwSetLocalClipboard({
        mode:'copy',
        ids:items.map(item => item.id),
        items,
        sourceRootName:activeSharedFolderName || '共享文件夹'
    });
    selectedLocalIds.clear();
    setPendingBatchDelete('');
    render();
    setStatus(`复制了 ${items.length} 个共享素材，导入后会拷贝到图片资产分组（共享文件夹原文件保留）`);
}
async function pasteLocalClipboardToAssets(){
    if(!localClipboard?.items?.length) return;
    if(!activeAssetCategory()){
        setStatus('请先在图片资产中创建或选择分组');
        return;
    }
    const clip = localClipboard;
    // 按所属共享文件夹分组，调用后端按路径导入（复制到素材库，无需走浏览器文件对象）
    const groups = new Map();
    clip.items.forEach(item => {
        if(!item || !item.folderId || !item.relativePath) return;
        if(!groups.has(item.folderId)) groups.set(item.folderId, []);
        groups.get(item.folderId).push(item.relativePath);
    });
    if(!groups.size) return;
    setStatus('正在导入共享素材...');
    let imported = 0;
    try {
        for(const [folderId, paths] of groups){
            const data = await apiJsonResponse(assetManagerApi.importSharedFolderItems({library_id:activeAssetLibraryId, category_id:activeAssetCategoryId, folder_id:folderId, paths}));
            setAssetLibrary(data.library || assetLibrary);
            imported += (data.items?.length || 0);
        }
    } catch(err) {
        setStatus(err.message || '导入共享素材失败');
        return;
    }
    gwSetLocalClipboard(null);
    selectedLocalIds.clear();
    setSelectedLocalId('');
    render();
    setStatus(`已导入 ${imported} 个素材到图片资产`);
}
function openLocalItem(id){
    const item = findLocalItem(id);
    if(!item) return;
    const url = localObjectUrl(item);
    if(url) window.open(url, '_blank', 'noopener');
}

// 【QIN-591】域函数面注册：主文件 gwCall('upload', fn) 经由此对象分派。
GW_DEFERRED['upload'] = {
    beginLocalUploadInlineRename,
    createLocalUploadFolder,
    deleteLocalAssets,
    downloadLocalUpload,
    downloadSelectedLocalUploads,
    loadClientUploadSourceDir,
    localItemsForFolder,
    localUploadItems,
    openLocalItem,
    openSharedFolder,
    pasteLocalClipboardToAssets,
    pasteLocalUploadClipboard,
    refreshLocalUploadSelectionOnly,
    registerSharedFolder,
    renameActiveLocalLibrary,
    renameLocalUploadFolder,
    renderLocalManager,
    saveClientUploadSourceDir,
    saveLocalUploadInlineName,
    setLocalClipboard,
    setLocalUploadClipboard,
    switchLocalLibrary,
    syncLocalSelectionDom,
    unregisterSharedFolder,
    uploadFiles,
    uploadLocalAssets,
    uploadViaClientPicker,
    uploadWorkflowFiles,
};

export {
    activeLocalLibraryPath,
    localItemsForFolder,
    localUploadItems,
    renderLocalClipboardBar,
    renderUploadCard,
    renderWorkflowUploadCard,
    saveClientUploadSourceDir,
    selectedLocalUploadImageItems,
};
