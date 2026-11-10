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

import assetManagerApi from './asset-manager/api.js';
import assetManagerHttp from './asset-manager/http.js';
import assetAuthApi from './asset-auth/api.js';
import {createAssetClassificationHelpers} from './asset-manager/classification.js';
import {readStoredBoolean, readStoredNumber} from './asset-manager/storage.js';
import {formatDate, formatFileSize} from './asset-manager/formatters.js';
import {pathCompareKey} from './asset-manager/path-utils.js';
import {workflowAssetId, workflowLibraryId, workflowCategoryId, workflowSourceId, zipWorkflowFiles} from './asset-manager/workflow-assets.js';
import {openAssetCollectOverlay} from './asset-collect-panel.js';
import {createHttpCollectAdapter} from './asset-collect-panel.js';
import {renderProvenanceChain, handleProvenanceNodeClick} from './asset-manager/provenance.js';
import {renderCollectProvenance, resolveCollectDeleteAssetId} from './asset-manager/collect-provenance.js';
import {
    composeLutPreview,
    LUT_COMPARE_MODES,
    LUT_MEDIA_SAMPLE_MAX_WIDTH,
    LutPreviewError,
    lutSampleImageData,
    mediaLutSamplable,
    mediaToLutSample,
    paintLutFrame,
    parseCubeLut,
} from './asset-manager/lut-preview.js';

// 【QIN-591】延后域注册表（state.js 只承载 GW_DEFERRED，不承载业务状态）
import { GW_DEFERRED } from './asset-manager/state.js';


let mediaPreviewPlayerLoading = null;
export async function ensureMediaPreviewPlayer() {
    if (typeof window !== 'undefined' && window.MediaPreviewPlayer) return window.MediaPreviewPlayer;
    if (mediaPreviewPlayerLoading) return mediaPreviewPlayerLoading;
    mediaPreviewPlayerLoading = (async () => {
        try {
            const mod = await import('./media-preview-player.js');
            if (mod && mod.MediaPreviewPlayer && typeof window !== 'undefined') {
                window.MediaPreviewPlayer = mod.MediaPreviewPlayer;
            }
            return mod?.MediaPreviewPlayer || window?.MediaPreviewPlayer || null;
        } catch (err) {
            console.warn('[AssetManager] 动态加载 media-preview-player.js 失败:', err);
            return null;
        }
    })();
    return mediaPreviewPlayerLoading;
}

const root = document.getElementById('assetManagerRoot');
const directorySettingsPage = new URLSearchParams(location.search).get('settings') === 'directories';
let directorySettingsReady = false;
let directorySettingsSaving = false;
let directorySettingsError = '';
// QIN-432：Tauri 客户端模式下的本机配置（客户端上传来源目录）。
const CLIENT_SETTING_KEY_UPLOAD_SOURCE = 'upload_source_dir';
let clientUploadSourceDir = '';
let clientSettingBusy = false;
function tauriInvokeFn(){
    // 目录设置可能运行在 iframe 内；Tauri 注入与宿主同源，top 上同样可用。
    return window.__TAURI__?.core?.invoke || window.top?.__TAURI__?.core?.invoke || null;
}
function isTauriClientMode(){ return Boolean(tauriInvokeFn()); }
const SIGNAL_FLOW_ACCENT_FALLBACK = '#69d6ff';
let currentStatusText = '准备就绪';
let currentIndexState = '';
function currentStatusElement(){
    const local = document.getElementById('assetStatus');
    if(local) return local;
    try {
        if(window.parent !== window) return window.parent.document.getElementById('assetVaultStatus');
    } catch(_) {}
    return null;
}
const uploadInput = document.getElementById('assetUploadInput');
const assetTabsEl = document.getElementById('assetBoardTabs');
const registryFacetCompact = window.matchMedia('(max-width: 900px)');

function syncRegistryFacetLayout(){
    const panel = document.getElementById('registryFacetPopover');
    if(!panel) return;
    if(registryFacetCompact.matches){
        panel.setAttribute('popover', 'auto');
    }else{
        if(panel.matches(':popover-open')) panel.hidePopover();
        panel.removeAttribute('popover');
    }
}
registryFacetCompact.addEventListener('change', syncRegistryFacetLayout);

window.addEventListener('storage', event => {
    if(event.key !== 'prompt_source_settings_v1' || activeTab !== 'prompts') return;
    selectedPromptId = '';
    selectedPromptIds.clear();
    render();
});

const LOCAL_CAPTION_SETTINGS_KEY = 'asset_manager_local_caption_settings_v1';
const ASSET_REGISTRY_VIEW_SETTINGS_KEY = 'asset_registry_view_settings_v2';
const ASSET_REGISTRY_CUSTOM_GROUPS_KEY = 'asset_registry_custom_groups_v1';
const ASSET_REGISTRY_DETAIL_COLLAPSED_KEY = 'asset_registry_detail_collapsed_v1';
const ASSET_REGISTRY_DETAIL_WIDTH_KEY = 'asset_registry_detail_width_v1';
const ASSET_DETAIL_INSPECTOR_COLLAPSED_KEY = 'asset_detail_inspector_collapsed_v1';
const ASSET_DETAIL_FONT_PANEL_KEY = 'asset_detail_font_panel_v1';
const ASSET_DETAIL_FONT_LIST_COLLAPSED_KEY = 'asset_detail_font_list_collapsed_v1';
const ASSET_DETAIL_MEDIA_LIBRARY_COLLAPSED_KEY = 'asset_detail_media_library_collapsed_v1';
const ASSET_DETAIL_MEDIA_LIBRARY_SIZE_KEY = 'asset_detail_media_library_size_v1';
const ASSET_DETAIL_MEDIA_LIBRARY_WIDTH_KEY = 'asset_detail_media_library_width_v1';
const ASSET_DETAIL_MEDIA_MINIMAP_VISIBLE_KEY = 'asset_detail_media_minimap_visible_v1';
const ASSET_LIBRARY_FOLDER_COLLAPSED_KEY = 'asset_library_folder_collapsed_v1';
const ASSET_LIBRARY_FOLDER_HIDDEN_KEY = 'asset_library_folder_hidden_v1';
const ASSET_REGISTRY_RECYCLE_CONFIRM_KEY = 'asset_registry_recycle_skip_confirm_v1';
const ASSET_REGISTRY_RECYCLE_SETUP_KEY = 'gw_recycler_setup_done';
const ASSET_REGISTRY_RECYCLE_MODE_KEY = 'gw_recycler_mode';
const PROMPT_SOURCE_SETTINGS_KEY = 'prompt_source_settings_v1';
function readPromptSourceSettings(){
    try {
        const value = JSON.parse(localStorage.getItem(PROMPT_SOURCE_SETTINGS_KEY) || '{}');
        return value && typeof value === 'object' ? value : {};
    } catch(_) {
        return {};
    }
}
function promptSourceEnabled(sourceId, settings=readPromptSourceSettings()){
    return settings.enabled?.[sourceId] !== false;
}
function filteredPromptRegistryLibrary(lib){
    if(lib?.id !== 'image_prompt_registry') return lib;
    const settings = readPromptSourceSettings();
    const items = (lib.items || []).filter(item => promptSourceEnabled(item?.metadata?.source_id, settings));
    const sourceIds = new Set(items.map(item => item?.metadata?.source_id).filter(Boolean));
    return {...lib, items, categories:(lib.categories || []).filter(category => sourceIds.has(String(category.id || '').replace(/^registry_/, '')))};
}
function readLocalCaptionSettings(){
    try {
        const data = JSON.parse(localStorage.getItem(LOCAL_CAPTION_SETTINGS_KEY) || '{}');
        return data && typeof data === 'object' ? data : {};
    } catch(_) {
        return {};
    }
}
const savedLocalCaptionSettings = readLocalCaptionSettings();
function readRegistryViewSettings(){
    try {
        const data = JSON.parse(localStorage.getItem(ASSET_REGISTRY_VIEW_SETTINGS_KEY) || '{}');
        return data && typeof data === 'object' ? data : {};
    } catch(_) {
        return {};
    }
}
function readRegistryCustomGroups(){
    try {
        const raw = JSON.parse(localStorage.getItem(ASSET_REGISTRY_CUSTOM_GROUPS_KEY) || '{}');
        const result = {};
        Object.entries(raw && typeof raw === 'object' ? raw : {}).forEach(([boardId, groups]) => {
            result[boardId] = (Array.isArray(groups) ? groups : []).map(group => ({
                id:String(group.id || ''),
                name:String(group.name || '自定义分组').slice(0, 40),
                assetIds:[...new Set((Array.isArray(group.assetIds) ? group.assetIds : []).map(String).filter(Boolean))],
            })).filter(group => group.id);
        });
        return result;
    } catch(_) {
        return {};
    }
}
function readCollapsedLibraryFolders(){
    try {
        const raw = JSON.parse(localStorage.getItem(ASSET_LIBRARY_FOLDER_COLLAPSED_KEY) || '{}');
        return Object.fromEntries(Object.entries(raw && typeof raw === 'object' ? raw : {}).map(([boardId, paths]) => [boardId, (Array.isArray(paths) ? paths : []).map(String).filter(Boolean)]));
    } catch(_) {
        return {};
    }
}
function readHiddenLibraryFolders(){
    try {
        const raw = JSON.parse(localStorage.getItem(ASSET_LIBRARY_FOLDER_HIDDEN_KEY) || '{}');
        return Object.fromEntries(Object.entries(raw && typeof raw === 'object' ? raw : {}).map(([boardId, paths]) => [boardId, (Array.isArray(paths) ? paths : []).map(String).filter(Boolean)]));
    } catch(_) {
        return {};
    }
}
// 【QIN-524】统一资产库中栏「紧凑目录机架」：折叠态与目录展开态的本地记忆
// 【QIN-533】智能筛选页下线，中栏只保留目录层级；模式记忆键随之删除。
// 折叠记忆键升到 v2：旧版默认展开时写入的 '0' 不得让中栏在新版仍然默认展开。
const ASSET_RACK_COLLAPSED_KEY = 'asset_registry_rack_collapsed_v2';
const ASSET_RACK_EXPANDED_KEY = 'asset_registry_rack_expanded_v1';
const ASSET_RACK_KNOWN_ROOTS_KEY = 'asset_registry_rack_known_roots_v1';
const ASSET_RACK_ROOT_KEY = '__root__';
function readAssetRackCollapsed(){
    // 【QIN-533】缺省收起：只有用户显式展开过（存 '0'）才保持展开
    try {
        const stored = localStorage.getItem(ASSET_RACK_COLLAPSED_KEY);
        return stored === null ? true : stored === '1';
    } catch(_) { return true; }
}
function readAssetRackExpandedFolders(){
    try {
        const raw = JSON.parse(localStorage.getItem(ASSET_RACK_EXPANDED_KEY) || '[]');
        return (Array.isArray(raw) ? raw : []).map(String).filter(Boolean);
    } catch(_) { return []; }
}
function readDetailFontPanelPreference(){
    try {
        const value = localStorage.getItem(ASSET_DETAIL_FONT_PANEL_KEY) || '';
        return ['','zh','en','glyph'].includes(value) ? value : '';
    } catch(_) {
        return '';
    }
}
const savedRegistryViewSettings = readRegistryViewSettings();

let activeTab = 'registry';
let boardNavigationState = null;
let assetRackCollapsed = readAssetRackCollapsed();
const assetRackExpandedFolders = new Set(readAssetRackExpandedFolders());
// 【QIN-544】左栏单栏树状态：系统/我的两个标签页，各一棵树
let assetTreeTab = 'system'; // 'system' | 'mine'
const assetTreeExpanded = new Set(); // 展开的树节点 key
let assetTreeSelectedKey = ''; // 当前选中树节点 key
const assetTreeProjectData = new Map(); // projectId -> QIN-543 项目详情（entities/groups）
let registryProjectEntityFilter = null; // 项目条目素材过滤 {projectId, entityTypes:Set, entityId}
/* 【QIN-546】我的资源整理交互状态：右键菜单 / 内联改名 / 拖拽落点 / 重名与删除弹窗 / 撤销栈 */
let assetOrganizeMenu = null; // {key, rootId, path, isRoot, trigger}
let assetOrganizeDialog = null;
// {key, value, busy}
let assetOrganizeDrag = null; // {kind:'assets'|'folder', assetIds, rootId, path, key}
let assetOrganizeDropHint = null; // {row, zone, key}
let assetOrganizeVersion = 0; // 整理 CAS 版本缓存；0 表示尚未读取
const assetOrganizeUndoStack = []; // 最近操作的反向描述（会话内）
const ASSET_ORGANIZE_UNDO_LIMIT = 20;
// 目录层级模式下的目录过滤（rootId + 物理相对路径），纯前端过滤，不触发网络请求
let registryNavFolder = {rootId:'', path:''};
let registryRegisteredFolders = [];
let registryIndexedFolders = [];
let registryRegisteredFoldersLoaded = false;
let boardDragId = '';
let boardCreateState = {open:false, preset:'other', name:'', nameManuallyEdited:false, path:'', saving:false, error:''};
let boardCreateFocusTarget = '';
let boardEditState = {open:false, boardId:'', name:'', path:'', saving:false};
let boardSourceState = {open:false, boardId:'', editor:'', existingId:'', name:'用户素材', path:''};
let boardFolderCreateState = {open:false, boardId:'', rootId:'', parent:'', name:'新文件夹', saving:false, error:''};
let boardSourceSelection = {};
let boardFolderSelection = {};
let registryRackSnapshot = null;
let boardGroupSelection = {};
let boardReferenceCanvasSelection = {};
let boardCollapsedFolders = readCollapsedLibraryFolders();
let boardHiddenFolders = readHiddenLibraryFolders();
const registryRootFolders = new Map();
const assetReindexRequests = new Map();
const registryRootFoldersLoading = new Set();
let registryAssets = [];
// 结构数据只缓存服务端返回的稳定 structure_id 投影，不作为资产或版本真源。
let registryAssetStructures = new Map();
let registryAssetStructuresRevision = 1;
let registryAssetStructureRequestSeq = 0;
let registryStructureMenu = null;
let registryStructureMutationId = '';
let registryStructureRemoveState = {open:false, structureId:'', busy:false, error:'', trigger:null};
let registryCoverflowCurrentAssetId = '';
let registryCoverflowBoardId = '';
let registryFacets = {kinds:[], categories:[], tags:[], collections:[], roots:[]};
let registryStatus = {ready:false, backend:'', jobs:[], overview:{}};
const VERSION_CONFLICT_MESSAGE = '内容已变化，请刷新';
let registryAutoPollTimer = 0;
let registryAutoPollBusy = false;
let registryObservedJobKey = '';
// 【跳顶终修】索引完成对账已改为 DOM 安全的静默合并（见 reconcileRegistryIndexChanges），不再需要交互期延迟机制
let registryReconcilePageIds = new Set(); // 最近一次增量对账返回的当前页 asset_id 集合
let registryPendingNewIds = new Set(); // 【修复跳顶】对账发现的新素材待看缓冲，不自动入网格
let registryPendingVanishedIds = new Set(); // 【修复跳顶】对账发现的消失素材缓冲，不自动抽卡
// 【轻量增量同步】壳层 asset-sync（切回页签）语义：切回页签=轻量增量同步；新素材自动插入但不动已加载内容；
// 全量刷新（重拉数据/清空选择/分页与筛选重置）仍只属于用户显式触发的 asset-refresh 与 [data-refresh-assets]。
const registryPendingNewItems = new Map(); // 待前插新素材的完整对象缓冲（id -> normalized item），由对账暂存、applyPendingRegistryAssets 消费
let registryBootstrapCompleted = false; // 首屏引导是否完成；未完成且网格无数据时 asset-sync 兜底执行 loadAll()
// 【QIN-585 AC2】首屏列表（注册表引导）请求的**真实终态**。非首屏链路只认这个终态：
// loadAll 里资产库/本地设置任一先失败都会提前 reject，而列表请求仍在途，
// 此时若按「catch 即首屏结束」放行收集箱与治理，它们照样抢在列表前面占用单进程服务端。
// 按**代际**维护：首屏列表在途时用户刷新会 abort 旧请求并开启新一代，
// 旧代际的终态既不能清掉新一代的「在途」，也不能把等新一代的调度放行。
let registryBootstrapGeneration = 1;         // 已登记终态的首屏列表请求代际；初值 1 表示「首屏尚未开始」
let registryBootstrapSettledGeneration = 0;  // 已落地的最大代际；小于当前代际即仍有列表在途
let registryBootstrapSettled = null;         // 当前代际的终态 promise（尚未登记过为 null）
let registrySyncBusy = false; // asset-sync 处理重入保护
let registryPendingApplyScrollBound = null; // 滚回顶部时自动补插新素材的一次性滚动监听
let registryPresets = [];
let registryTotal = null;
let registryTotalKnown = false;
// 【QIN-569 AC1】注册表引导请求失败时的可恢复错误态标记（首屏失败不再空白等待）。
let registryBootstrapError = '';
let registryStatCategory = '';
let registryQuery = '';
let registryKind = '';
let registryCollectionId = '';
let registryRootId = '';
let registryRecentDays = 0;
let registrySort = 'newest';
let registryIncludeTags = new Set();
let registryExcludeTags = new Set();
let registrySelectedIds = new Set();
let selectedRegistryAssetId = '';
let registryDetail = null;
let touchbarReportRevision = 0;
let touchbarReportTimer = 0;
let registryPrincipal = null;
let registryPrincipalLoading = true;
function touchbarStableId(value){
    const id = String(value == null ? '' : value).trim();
    return /^[A-Za-z0-9][A-Za-z0-9_.:@-]{0,191}$/.test(id) ? id : '';
}
function canAddRegistryAssetToCanvas(){
    if(registryPrincipalLoading) return false;
    const role = String(registryPrincipal?.role || '').trim().toLowerCase();
    return Boolean(registryPrincipal?.local_mode) || ['editor','admin'].includes(role);
}
async function loadRegistryPrincipal(){
    registryPrincipalLoading = true;
    scheduleAssetTouchbarReport();
    try {
        const status = await apiJsonResponse(assetAuthApi.getStatus());
        registryPrincipal = status?.principal && typeof status.principal === 'object'
            ? status.principal
            : null;
    } catch(_) {
        registryPrincipal = null;
    } finally {
        registryPrincipalLoading = false;
        scheduleAssetTouchbarReport();
    }
}
function reportAssetTouchbarContext(){
    if(window.parent === window) return;
    const asset = selectedRegistryAssetId
        ? (registryDetail || registryAssets.find(item => item.id === selectedRegistryAssetId) || null)
        : null;
    const assetId = touchbarStableId(asset?.id);
    const projectId = touchbarStableId(activeProjectId);
    const entityId = touchbarStableId(selectedProjectEntityId);
    try {
        window.parent.postMessage({
            type:'gw-touchbar-capabilities',
            version:1,
            revision:++touchbarReportRevision,
            page:'asset-manager',
            context:{...(projectId ? {project_id:projectId} : {}), ...(entityId ? {entity_id:entityId} : {}), ...(assetId ? {asset_id:assetId} : {})},
            selection:assetId ? {type:'asset', id:assetId, label:String(asset?.name || '').slice(0,100), status:String(asset?.status || '').slice(0,36)} : null,
            actions:assetId
                ? [{action_id:'asset.open-detail'}, ...(canAddRegistryAssetToCanvas() ? [{action_id:'asset.add-to-canvas'}] : [])]
                : [],
        }, location.origin);
    } catch(_) {}
}
function scheduleAssetTouchbarReport(){
    clearTimeout(touchbarReportTimer);
    touchbarReportTimer = setTimeout(reportAssetTouchbarContext, 0);
}
let registryDetailPanelCollapsed = readStoredBoolean(ASSET_REGISTRY_DETAIL_COLLAPSED_KEY, true);
let registryDetailPanelWidth = Math.max(280,Math.min(720,readStoredNumber(ASSET_REGISTRY_DETAIL_WIDTH_KEY,360)));
let registryManageMode = false;
// Similar-asset stacking was removed; keep the persisted field ignored for old sessions.
let registryGroupSimilar = false;
// 预览大小改为连续档：Shift+滚轮调节（px），旧的 小/中/大 离散档按中值迁移。
const REGISTRY_CARD_SIZE = { min: 96, max: 360, fallback: 176, presets: { small: 126, medium: 176, large: 244 } };
let registryCardSize = (() => {
    const stored = Number(savedRegistryViewSettings.cardSize);
    if(Number.isFinite(stored) && stored > 0) return Math.max(REGISTRY_CARD_SIZE.min, Math.min(REGISTRY_CARD_SIZE.max, Math.round(stored)));
    return REGISTRY_CARD_SIZE.presets[savedRegistryViewSettings.size] || REGISTRY_CARD_SIZE.fallback;
})();
// The unified "方格" control starts in original-ratio mode and toggles to square on click.
let registryAspect = savedRegistryViewSettings.aspect === 'square' ? 'square' : 'original';
let registrySimple = savedRegistryViewSettings.simple !== false;
let registryLayout = savedRegistryViewSettings.layout === 'list' ? 'list' : 'grid';
let registryGroupMode = ['none','source','folder','custom'].includes(savedRegistryViewSettings.groupMode) ? savedRegistryViewSettings.groupMode : 'none';
let registryCustomGroups = readRegistryCustomGroups();
let registryExpandedStacks = new Set();
let registryBulkPanel = '';
let registryCanvasTargets = [];
let registryCanvasLoading = false;
let registryTagEditorAssetId = '';
let registryLoading = false;
let registryPageLoading = false;
let registryNextOffset = 0;
let registryNextCursor = '';
let registryPageHasMore = false;
let registryRequestSeq = 0;
let registryRequestAbortController = null;
let registryPrefetchAbortController = null;
// 【浏览预加载】下一页数据预取状态：与 registryRequestSeq 相互独立，
// 预取只写 registryAssetsQueryCache，不推进分页游标、不触发渲染。
let registryPrefetchInFlight = false; // 预取去重守卫（独立于 registryRequestSeq）
let registryPrefetchQueryKey = '';    // 进行中预取的 queryKey，供 loadMore 共享同一请求
let registryPrefetchPromise = null;   // 进行中预取的 Promise，loadMore 命中时直接等待，避免重复请求
let registryPrefetchTimer = 0;        // 空闲调度句柄（requestIdleCallback 回退 setTimeout）
let registryPrefetchTimerIdle = false;// 句柄类型标记，取消时选择对应 API
// 【QIN-584】首屏那一页的结构补齐在飞时的句柄。结构查询共用同一个序号，
// 翻页请求若在它落地前发起会递增序号、把首屏页的结构结果作废，故翻页前需等待它。
let registryStructureBootstrapPromise = null;
let registryFocusIndex = -1;
let registryFocusRestoreId = '';
let registrySelectionAnchorId = '';
let registryArchiveBusy = false;
let registryPdfBusy = false;
let registryRecycleBinState = {open:false, loading:false, restoringId:'', items:[], error:''};
const registryAssetsQueryCache = new Map();
const REGISTRY_PAGE_SIZE = 1500;
// 【QIN-584 AC1】页大小只能跟「可见视口」有关，绝不能跟「已经加载了多少卡片」有关。
// `.registry-assets-scroll` 是随内容撑高的普通列容器（没有高度约束，clientHeight 恒等于
// scrollHeight），读它的 clientHeight 等于把卡片数量当成视口高度：预取与后续页会把
// dynamic_limit 从视口值一路抬到 1500，搜索命中几百条时就是一次下发整段结果。
// 因此可见高度只取「容器在窗口内的可见区间」：窗口高度 ∩ 容器相对窗口的上下边界。
let registryDynamicPageSizeMemo = {signature:'', size:0};
function registryVisibleViewportHeight(area){
    const viewportHeight = Math.max(1, Number(window.innerHeight) || 768);
    if(!area || typeof area.getBoundingClientRect !== 'function') return viewportHeight;
    const rect = area.getBoundingClientRect() || {};
    const top = Math.max(0, Math.min(Number(rect.top) || 0, viewportHeight));
    const bottom = Math.min(viewportHeight, Number(rect.bottom) || 0);
    const visible = bottom - top;
    // 页面滚动后容器顶边跑到窗口上方，可见高度自然退化成整屏高度，不会越过窗口。
    if(visible >= registryCardSize) return visible;
    // 内容还没撑开时容器只有内边距，此时按「容器顶边到窗口底边」估——与撑开后的可见高度同值，
    // 首屏与后续页因此不会在 120/100 之间跳变。
    return Math.max(1, viewportHeight - top);
}
function registryDynamicPageSize(memo = registryDynamicPageSizeMemo){
    const area = root.querySelector('.registry-assets-scroll');
    const viewportHeight = Math.max(1, Number(window.innerHeight) || 768);
    const width = Math.max(1, Math.round(Number(area?.clientWidth) || Number(window.innerWidth) || 1024));
    const signature = `${width}x${viewportHeight}x${registryCardSize}`;
    if(memo.signature === signature && memo.size > 0) return memo.size;
    const height = registryVisibleViewportHeight(area);
    const columns = Math.max(1, Math.floor(width / (registryCardSize + 12)));
    const rows = Math.max(1, Math.ceil(height / registryCardSize));
    const size = Math.min(REGISTRY_PAGE_SIZE, Math.max(100, columns * rows * 4));
    // 首屏那次就定稿：整场会话共用同一个页大小，预取 / 翻页 / 搜索重查询都不再重算。
    // 只有窗口宽度、高度或卡片尺寸变化（签名不同）才重新测量。
    memo.signature = signature;
    memo.size = size;
    return size;
}
// 【QIN-584 AC1】续页判据必须区分两种滚动模型。页面级滚动布局下 `.registry-assets-scroll`
// 没有自身滚动条：scrollTop 恒为 0、clientHeight 恒等于 scrollHeight，用
// `scrollTop + clientHeight >= scrollHeight - lead` 判断「是否接近底部」会恒为真，
// 任意一次 scroll 事件（哪怕容器根本没滚动）都会续一页，最终把整库拉完。
// 容器自身可滚动时按 scrollTop 算；页面级滚动时只能按哨兵相对窗口的位置算。
function registryReachedLoadMoreBoundary(scrollRoot, lead){
    if(!scrollRoot) return false;
    const viewportHeight = Math.max(1, Number(window.innerHeight) || 768);
    const selfScrollable = Number(scrollRoot.scrollHeight) - Number(scrollRoot.clientHeight) > 8;
    if(selfScrollable){
        return scrollRoot.scrollTop + scrollRoot.clientHeight >= scrollRoot.scrollHeight - lead;
    }
    const sentinel = scrollRoot.querySelector?.('.registry-infinite-sentinel');
    if(!sentinel || typeof sentinel.getBoundingClientRect !== 'function') return false;
    return Number(sentinel.getBoundingClientRect().top) - lead <= viewportHeight;
}
// 【QIN-584 AC4】页面级滚动布局下滚动发生在文档上，`.registry-assets-scroll` 不派发 scroll 事件，
// 「继续下滑自动加载」因此整段失效（只有点击 / 键盘能续页）。这里把文档级滚动折算回资产库容器：
// 仅当资产库处于激活视图、且容器确实落在窗口内时才参与，避免在别的页签滚动时误拉素材。
function registryPageScrollTarget(event){
    const target = event?.target;
    const isDocumentScroll = !target || target === document || target === document.documentElement || target === document.body;
    if(!isDocumentScroll) return null;
    if(activeTab !== 'registry' && assetBoardById(activeTab)?.system !== false) return null;
    if(registryLoading) return null;
    const area = root?.querySelector?.('.registry-assets-scroll');
    if(!area || typeof area.getBoundingClientRect !== 'function') return null;
    const rect = area.getBoundingClientRect();
    const viewportHeight = Math.max(1, Number(window.innerHeight) || 768);
    const visible = Math.min(viewportHeight, Number(rect.bottom) || 0) - Math.max(0, Number(rect.top) || 0);
    return visible > 0 ? area : null;
}
// Asset-structure discovery must stay bounded as the user scrolls a large
// library.  Appended pages query only their newly seen IDs, one API window at
// a time, instead of re-requesting every previously loaded card in parallel.
const REGISTRY_STRUCTURE_QUERY_CHUNK_SIZE = 500;
const REGISTRY_CACHE_MAX_SIZE = 20; // 【优化问题1】限制缓存大小，避免内存膨胀
// 【QIN-518】视口提前量：卡片在进入视口前 200px 就发起预览解码，
// 消除快速滚动时的大面积白块与渐现延迟。
const REGISTRY_PREVIEW_ROOT_MARGIN = '200px 0px';
const REGISTRY_PREVIEW_SCROLL_AHEAD_COUNT = 20;
const REGISTRY_PREVIEW_SCROLL_PRELOAD_INTERVAL_MS = 120;
const REGISTRY_PREVIEW_CONCURRENCY = 8;
const registryPreviewImageCache = new Map();
const REGISTRY_VIRTUAL_WINDOW_SIZE = 120;
const REGISTRY_VIRTUAL_OVERSCAN_ROWS = 3;
const REGISTRY_VIRTUAL_GAP = 10;
const REGISTRY_PREVIEW_PLACEHOLDER = 'data:image/svg+xml,%3Csvg xmlns=%22http://www.w3.org/2000/svg%22 width=%221%22 height=%221%22/%3E';
let registryPreviewQueue = [];
let registryPreviewActive = 0;
let registryPreviewQueueRevision = 0;
let registryPreviewObserver = null;
let registryPreviewFallbackCleanup = null;
let registryVirtualScrollCleanup = null;
let registryVirtualScrollFrame = 0;
let registryVirtualRemeasureFrame = 0;
let registryVirtualObserver = null;
let registryVirtualState = {key:'', start:0, end:0, cols:1, totalHeight:0, rowTops:[], rowHeights:[], itemLayouts:[]};
const registryVirtualHeightCache = new Map();
let registryVirtualHeightCacheMode = '';
let savedScrollPositions = {registry: 0, assets: 0, workflows: 0, prompts: 0, local: 0, canvas: 0}; // 【修复问题4】保存各个标签页的滚动位置
let registryPreviewScheduleRevision = 0;
let registryMasonryObserver = null;
let registryMasonryScheduleRevision = 0;
// 九宫格单图缓存：接口返回后端合成的 3×3 单图路径，面板只渲染这一张。
const videoStoryboardImageCache = new Map();
let thumbnailGeneratorState = {open:false, loading:false, generating:false, deleting:false, deletingStoryboards:false, generatingStoryboards:false, submittingBackground:false, savingFormat:false, backgroundTaskId:'', mode:'source', directory:'', savedMode:'source', savedDirectory:'', cacheFolder:'god-cache', format:'jxl', revision:1, transcodeVideos:true, cacheStats:null, error:'', cacheBlocked:null, grantingRoot:false};
// 【QIN-519 / QIN-525】全库预览缓存：需创作者显式确认后才启动；默认惰性读时写缓存不变。
// confirming 为「全部缓存 / 重建缓存」两段式确认，deleteOpen 为分类删除面板。
let assetPreviewAllState = {confirming:false, confirmMode:'fill', submitting:false, controlling:false, job:null, pollingJobId:'', error:'', notice:'', deleting:false, deleteOpen:false, deleteCategories:{image:true, video:true, storyboard:true, other:true}, deleteResult:null};
// 切换缓存目录的确认弹窗：迁移 / 删除 / 取消（取消不改设置，继续用原目录）。
let thumbnailCacheSwitchState = {open:false, busy:false, action:'', error:'', fromMode:'source', fromDirectory:'', toMode:'source', toDirectory:''};
let thumbnailCacheVersion = 0; // 【修复问题1】仅作为无 asset_id 的旧版 URL 的会话级版本，任务完成不再全局变化
let projects = [];
function requestedRegistryProjectId(){
    try {
        return String(new URLSearchParams(window.location.search).get('project_id') || '').trim();
    } catch(e){ return ''; }
}
const initialRegistryProjectId = requestedRegistryProjectId();
let activeProjectId = initialRegistryProjectId;
const explicitRegistryProjectContext = Boolean(initialRegistryProjectId);
let registryProjectFilterId = '';
let registryProjectFacetExpanded = false;
const registryProjectAssetCounts = new Map();
let registryProjectCountsLoading = false;
async function refreshRegistryProjectCounts(renderNow = true){
    if(registryProjectCountsLoading) return;
    const recent = registryRecentProjects();
    if(!recent.length) return;
    registryProjectCountsLoading = true;
    try {
        await Promise.all(recent.map(async proj => {
            try {
                const res = await apiJsonResponse(assetManagerApi.getRegistryAssets(`project_id=${encodeURIComponent(proj.id)}&limit=1`));
                const total = Number(res?.total);
                if(Number.isFinite(total)){
                    proj.asset_count = total;
                    registryProjectAssetCounts.set(proj.id, total);
                }
            } catch(_) {}
        }));
    } finally {
        registryProjectCountsLoading = false;
    }
    if(renderNow) render();
}
// 【QIN-582 AC5】项目计数按项目逐个打 getRegistryAssets('project_id=…&limit=1')，
// 启动期会并发放大后端的来源根探测链。这里改为首屏就绪后在空闲时段刷新一次：
// 不阻塞 loadAll()，也不在首屏关键路径上 await；计数到达后仍会 render() 更新 UI。
let registryFirstScreenReady = false;
let registryProjectCountsDirty = false;
let registryProjectCountsScheduled = false;
function scheduleRegistryProjectCounts(){
    registryProjectCountsDirty = true;
    if(!registryFirstScreenReady || registryProjectCountsScheduled) return;
    registryProjectCountsScheduled = true;
    const run = () => {
        registryProjectCountsScheduled = false;
        if(!registryProjectCountsDirty) return;
        registryProjectCountsDirty = false;
        void refreshRegistryProjectCounts(true).catch(() => {});
    };
    if(typeof window.requestIdleCallback === 'function'){
        window.requestIdleCallback(run, {timeout:2000});
    } else {
        window.setTimeout(run, 0);
    }
}
function markRegistryFirstScreenReady(){
    registryFirstScreenReady = true;
    if(registryProjectCountsDirty) scheduleRegistryProjectCounts();
}
function registryProjectAssetCount(projOrId){
    const projId = typeof projOrId === 'object' && projOrId ? (projOrId.id || projOrId.project_id) : String(projOrId || '');
    const proj = typeof projOrId === 'object' && projOrId ? projOrId : projects.find(item => item.id === projId);
    if(registryProjectAssetCounts.has(projId)){
        return registryProjectAssetCounts.get(projId);
    }
    if(typeof proj?.asset_count === 'number' && Number.isFinite(proj.asset_count)){
        return proj.asset_count;
    }
    return 0;
}
function registryRecentProjects(){
    return [...projects]
        .sort((a, b) => {
            const timeA = new Date(a.updated_at || a.created_at || a.start_at || 0).getTime();
            const timeB = new Date(b.updated_at || b.created_at || b.start_at || 0).getTime();
            return timeB - timeA;
        })
        .slice(0, 5);
}
let projectBoard = null;
let selectedProjectEntityId = '';
let assetLibrary = {libraries:[], categories:[]};
let promptLibrary = {libraries:[]};
let apiProviders = [];
let avatarRegisterProvider = '';
let activeAssetLibraryId = '';
let activeAssetCategoryId = '';
let activeAssetClassFilter = '';
let openAssetClassGroup = 'environment';
let activeWorkflowLibraryId = '';
let activeWorkflowCategoryId = '';
let activePromptLibraryId = '';
let activePromptCategory = 'all';
let assetTreeFocus = 'category';
let promptTreeFocus = 'category';
let selectedAssetId = '';
let selectedWorkflowId = '';
let selectedPromptId = '';
let selectedAssetIds = new Set();
let selectedWorkflowIds = new Set();
const workflowDeleteVersions = new Map();
const workflowDeleteBusy = new Set();
let selectedPromptIds = new Set();
let assetQuery = '';
let workflowQuery = '';
let promptQuery = '';
let assetManageMode = false;
let workflowManageMode = false;
let promptManageMode = false;
let assetMoveTarget = '';
let assetClipboard = null;
let managedSelectionPointerGuard = null;
let assetEditMode = false;
let promptEditMode = false;
let promptCreateMode = false;
let pendingDeleteAssetId = '';
let pendingDeletePromptId = '';
let pendingBatchDelete = '';
let assetTreeEdit = null;
let workflowTreeEdit = null;
let promptTreeEdit = null;
let pendingTreeDelete = '';
let marqueeState = null;
let registryMarqueeSuppressClickUntil = 0;
let sharedFolders = [];
let activeSharedFolderName = '';
let localFolderMap = new Map();
let localItemMap = new Map();
let activeLocalFolderId = '';
let selectedLocalId = '';
let selectedLocalIds = new Set();
let localQuery = '';
let localManageMode = false;
let localClipboard = null;
let localCaptionBusy = false;
let localCaptionProvider = savedLocalCaptionSettings.provider || '';
let localCaptionModel = savedLocalCaptionSettings.model || '';
let localCaptionPrompt = savedLocalCaptionSettings.captionPrompt || '描述图片';
let localClassifyPrompt = savedLocalCaptionSettings.classifyPrompt || '';
let localAutoClassify = savedLocalCaptionSettings.autoClassify === true;
let localClassifyPromptOpen = false;
let localAssets = [];
let localAssetsLoaded = false;
let localAssetsCursor = '';
let localAssetsHasMore = false;
let localAssetsLoading = false;
let localAssetsError = '';
let localAssetsTotal = 0;
let localAssetsRequestId = 0;
const LOCAL_ASSET_PAGE_SIZE = 100;
let localLibraries = [];
let activeLocalLibraryId = 'local';
let localLibraryDragId = '';
let localLibraryDragBlockUntil = 0;
let localUploadTree = null;
let activeLocalUploadFolder = '';
let activeLocalUploadClassFilter = '';
let openLocalUploadClassGroup = '';
let selectedLocalUploadId = '';
let selectedLocalUploadIds = new Set();
let localUploadQuery = '';
let localUploadManageMode = false;
let localUploadClipboard = null;
let assetClassifyBusy = false;
let localClassifyBusy = false;
let lightboxPanState = null;
let detailMediaCanvasPanState = null;
let detailMediaCanvasSpacePressed = false;
let detailMediaCanvasEditState = null;
let detailMediaCanvasAnnotationSnapshot = null;
let detailMediaCanvasColorSnapshot = null;
let detailMediaLibraryDragId = '';
let detailImageEditorState = null;
let assetNoticeTimer = 0;
// 【QIN-547】收集箱：默认收集目录、素材列表、未读角标与设置面板状态
let inboxItems = [];
let inboxSettings = null;
let inboxLoaded = false;
let inboxQuery = '';
let inboxSourceFilter = '';
let inboxSettingsOpen = false;
let inboxBadgeTimer = 0;
let detailViewerState = {
    open:false, source:'', id:'', index:0, items:[], text:'', textLoading:false, textError:'',
    textEditing:false, textTruncated:false, textVersions:[], textVersionsLoading:false,
    selectedTextVersion:null, textVersionEditing:false, textVersionError:'',
    modelInfo:null, modelInfoLoading:false, modelInfoError:'', wheelAt:0, audioZoom:1,
    fontScale:1, fontFit:1, fontLineWidth:0, fontExpanded:'', fontListScrollTop:0,
    lutStrength:1, lutCompare:'applied', lutListScrollTop:0,
    mediaCanvasLut:{}, mediaCanvasLutPanel:'', mediaLutMenu:{open:false, step:'asset', assetId:''},
    mediaCanvasItems:[], mediaCanvasActiveId:'', mediaCanvasSelectedIds:new Set(), mediaCanvasSelectionOrder:[], mediaCanvasLayout:{},
    mediaCanvasAnnotations:[], mediaCanvasDrawings:[], mediaCanvasGroups:[], mediaCanvasContextMenu:null,
    mediaCanvasEditingAnnotationId:'', mediaCanvasHistory:[], mediaCanvasSnap:true, mediaCanvasLibraryVisible:false,
    mediaCanvasNormalizeBasis:'first', mediaCanvasExportScope:'all', mediaCanvasLibrarySelectedId:'',
    mediaCanvasLibraryQuery:'', mediaCanvasLibrarySort:'newest', mediaCanvasLibraryScope:'all', mediaCanvasLibraryStorageRootId:'all', mediaCanvasLibraryScrollTop:0, mediaCanvasSidebarView:'detail',
    mediaCanvasScale:1, mediaCanvasX:0, mediaCanvasY:0, mediaCanvasFitted:false,
    videoRanges:{}, videoExportRootId:'', videoExportBusy:'', videoStoryboardExpandedId:'',
    imageVersions:[],imageVersionsAssetId:'',imageVersionsLoading:false,imageVersionSelectedId:'',imageVersionPanelCollapsed:true,
    referenceCanvasId:'', referenceCanvasTitle:'参考画布', referenceCanvasMode:false, referenceCanvasStructureId:'', referenceCanvasSaving:false, originTab:'registry'
};
let detailViewerClickState = {source:'', id:'', at:0};
let referenceCanvasClickState = {id:'', at:0};
let detailViewerInspectorCollapsed = readStoredBoolean(ASSET_DETAIL_INSPECTOR_COLLAPSED_KEY, true);
let detailViewerFontExpandedPreference = readDetailFontPanelPreference();
let detailViewerFontListCollapsed = readStoredBoolean(ASSET_DETAIL_FONT_LIST_COLLAPSED_KEY, false);
let detailMediaCanvasLibraryCollapsed = readStoredBoolean(ASSET_DETAIL_MEDIA_LIBRARY_COLLAPSED_KEY, false);
let detailMediaCanvasLibraryThumbSize = Math.max(96,Math.min(220,readStoredNumber(ASSET_DETAIL_MEDIA_LIBRARY_SIZE_KEY,156)));
let detailMediaCanvasLibraryWidth = Math.max(280,Math.min(720,readStoredNumber(ASSET_DETAIL_MEDIA_LIBRARY_WIDTH_KEY,420)));
let detailMediaCanvasMinimapVisible = readStoredBoolean(ASSET_DETAIL_MEDIA_MINIMAP_VISIBLE_KEY, true);
const detailMediaCanvasLibraryRootItemsCache = new Map();
const detailViewerTextCache = new Map();
const detailViewerFileInfoCache = new Map();
const detailViewerAudioWaveformCache = new Map();
const detailViewerMediaPreloads = new Map();
let detailViewerAudioFrame = 0;
let assetHoverPreviewMedia = null;
let canvasAssetsData = {categories:[], canvases:[], items:[]};
let activeCanvasAssetCategory = 'smart';
let activeCanvasAssetCanvasId = '';
let selectedCanvasAssetId = '';
let selectedCanvasAssetIds = new Set();
let canvasAssetQuery = '';
let canvasAssetSort = 'canvas_asc';
let canvasAssetManageMode = false;
let searchCompositionActive = false;
let searchRenderTimer = null;
let lastSearchCompositionEndAt = 0;
let storageSettingsState = {open:false, tab:'boards', focusSection:'', dirs:{}, defaults:{}, revision:null, extraLocalDirs:[], extraLocalEntries:[], localLibraryNames:{}, localLibraryReadOnly:{}, localLibraries:[]};
let allowedRootSettingsState = {revision:null, roots:[], loading:false, saving:false, error:''};
let serverDirectoryBrowserState = {open:false, path:null, parent:null, directories:[], loading:false, saving:false, error:'', key:'projects'};
let assetAiSettingsState = {rule:'', defaultRule:'', loading:false, saving:false, message:'', backgroundTaskId:''};
let projectDirectoryTemplateManagerState = {open:false, loading:false, saving:false, error:'', templates:[], revision:null, selectedId:'', editor:null, pendingAction:null, archiveId:'', returnFocus:null};

const ASSET_DOCUMENT_PREVIEW_EXTENSIONS = ['txt','csv','tsv','json','xml','html','htm','md','rtf','doc','docx','odt','pdf'];
const ASSET_3D_PREVIEW_EXTENSIONS = ['stl','3mf','obj','fbx','gltf','glb','usd','usdz','step','stp','iges','igs','dxf','ply','3ds','dae','ifc','wrl','vrml','blend','c4d','max','ma','mb'];
const ASSET_FONT_PREVIEW_EXTENSIONS = ['ttf','otf','ttc','otc','woff','woff2'];
// 【QIN-554】LUT 预览：Cube 查找表在详情里走 canvas 样片套用，不当作图片文件预览。
const ASSET_LUT_PREVIEW_EXTENSIONS = ['cube'];
const ASSET_AUDIO_PREVIEW_EXTENSIONS = ['mp3','wav','flac','ogg','m4a','aac'];
const ASSET_PREVIEW_RENDER_VERSION = '2026.08.01.5';
const ASSET_FONT_PREVIEW_ZH_LINE = '观自在菩萨，行深般若波罗蜜多时照见五蕴皆空，';
const ASSET_FONT_PREVIEW_LETTERS_UPPER = 'ABCDEFGHIJKLMNOP';
const ASSET_FONT_PREVIEW_LETTERS_LOWER = 'abcdefghijklmnop';
const ASSET_FONT_PREVIEW_NUMBERS = '0123456789';
const ASSET_FONT_PREVIEW_PUNCTUATION = `.,:;!?'"()[]{}-_/@#&%+=<>\\`;
const ASSET_FONT_PREVIEW_PUNCTUATION_ZH = '，。！？；：“”‘’（）《》【】';
const ASSET_FONT_PREVIEW_ZH = `观自在菩萨，行深般若波罗蜜多时照见五蕴皆空，度一切苦厄。

舍利子，色不异空，空不异色。色即是空，空即是色。受想行识，亦复如是。

舍利子，是诸法空相，不生不灭。不垢不净，不增不减。是故空中无色，无受想行识。无眼耳鼻舌身意，无色声香味触法。无眼界，乃至无意识界。无无明，亦无无明尽。乃至无老死，亦无老死尽。无苦集灭道，无智亦无得，以无所得故。

菩提萨埵，依般若波罗蜜多故。心无挂碍，无挂碍故，无有恐怖。远离颠倒梦想，究竟涅槃。

三世诸佛，依般若波罗蜜多故。得阿耨多罗三藐三菩提。

故知般若波罗蜜多。是大神咒，是大明咒，是无上咒。是无等等咒，能除一切苦，真实不虚。

故说般若波罗蜜多咒。即说咒曰，揭谛揭谛，波罗揭谛。波罗僧揭谛，菩提萨婆诃。`;
const ASSET_FONT_PREVIEW_EN = `Never give up,

Never lose hope.

Always have faith,

It allows you to cope.

Trying times will pass,

As they always do.

Just have patience,

Your dreams will come true.

So put on a smile,

You'll live through your pain.

Know it will pass,

And strength you will gain.`;
const ASSET_EDITABLE_TEXT_EXTENSIONS = new Set(['txt','csv','tsv','json','xml','md']);
const ASSET_IMAGE_PREVIEW_EXTENSIONS = new Set(['jpg','jpeg','png','gif','webp','bmp','tiff','tif','heic','heif','avif','raw','svg','eps','psd','ai','cdr']);
const ASSET_PREVIEW_EXTENSIONS = new Set(['jpg','jpeg','png','gif','webp','bmp','tiff','tif','heic','heif','avif','raw','svg','eps','psd','ai','cdr',...ASSET_DOCUMENT_PREVIEW_EXTENSIONS,...ASSET_3D_PREVIEW_EXTENSIONS,...ASSET_FONT_PREVIEW_EXTENSIONS,...ASSET_AUDIO_PREVIEW_EXTENSIONS]);
const ASSET_BROWSER_PREVIEW_EXTENSIONS = new Set(['jpg','jpeg','png','gif','webp','bmp','svg','avif']);
const ASSET_VIDEO_EXTENSIONS = new Set(['mp4','webm','mov','m4v','avi','mkv','flv','mts','m2ts','wmv','mpeg','mpg','ts','mxf','vob','ogv','3gp']);
const ASSET_BROWSER_VIDEO_EXTENSIONS = new Set(['mp4','webm','mov','m4v']);
const LOCAL_MEDIA_EXTS = /\.(jpe?g|png|gif|webp|bmp|tiff?|heic|heif|avif|raw|svg|eps|psd|ai|cdr|pdf|txt|csv|tsv|json|xml|html?|md|rtf|docx?|odt|pptx?|odp|xlsx?|ods|stl|3mf|obj|fbx|gltf|glb|usd|usdz|step|stp|iges|igs|dxf|ply|3ds|dae|ifc|wrl|vrml|blend|c4d|max|ma|mb|ttf|otf|ttc|otc|woff2?|mp4|webm|mov|m4v|avi|mkv|flv|mts|m2ts|wmv|mpeg|mpg|ts|mxf|vob|ogv|3gp|mp3|wav|flac|ogg|m4a|aac)(\?|#|$)/i;
const SEARCH_INPUT_IDS = new Set(['registrySearch','assetSearch','workflowSearch','promptSearch','localSearch','localUploadSearch','canvasAssetSearch']);

function refreshIcons(){ if(window.lucide) lucide.createIcons(); }
function markIndexState(state=''){
    currentIndexState = String(state || '');
    const el = currentStatusElement();
    if(!el) return;
    if(currentIndexState) el.setAttribute('data-gw-index-state', currentIndexState);
    else el.removeAttribute('data-gw-index-state');
}
function setStatus(text='准备就绪'){
    currentStatusText = text || '准备就绪';
    const el = currentStatusElement();
    if(el){
        el.textContent = currentStatusText;
        if(currentIndexState) el.setAttribute('data-gw-index-state', currentIndexState);
        else el.removeAttribute('data-gw-index-state');
        // 显式降级标记（用户 2026-09-21 裁决第 3 项）：未接入 / 暂不可用必须可被核验，
        // 不得静默。父页 /static/pages/assets.html 会读取该标记并同步展示。
        const kind = /未纳入当前切片/.test(currentStatusText) ? 'not_integrated'
            : (/暂时不可用/.test(currentStatusText) ? 'service_unavailable'
                : (/项目不存在|无权访问|权限不足|只读/.test(currentStatusText) ? 'forbidden' : ''));
        if(kind) el.setAttribute('data-gw-degradation', kind);
        else el.removeAttribute('data-gw-degradation');
    }
}
function showAssetNotice(message,type='success',duration=8000){
    document.querySelector('[data-asset-notice]')?.remove();
    if(assetNoticeTimer) clearTimeout(assetNoticeTimer);
    const notice=document.createElement('div');
    notice.className=`asset-operation-notice is-${type === 'error' ? 'error' : 'success'}`;
    notice.dataset.assetNotice='';
    notice.setAttribute('role',type === 'error' ? 'alert' : 'status');
    notice.innerHTML=`<i data-lucide="${type === 'error' ? 'circle-alert' : 'circle-check-big'}"></i><strong>${escapeHtml(message)}</strong><button type="button" data-asset-notice-close aria-label="关闭提示"><i data-lucide="x"></i></button>`;
    document.body.appendChild(notice);
    refreshIcons();
    requestAnimationFrame(()=>notice.classList.add('is-visible'));
    assetNoticeTimer=setTimeout(()=>{ notice.classList.remove('is-visible'); setTimeout(()=>notice.remove(),180); },Math.max(1000,Number(duration||8000)));
}
function showAssetFailure(title,error){
    document.querySelector('[data-asset-error-dialog]')?.remove();
    const detail=String(error?.stack || error?.message || error || '未知错误').slice(0,8000);
    const overlay=document.createElement('div');
    overlay.className='asset-operation-error-overlay';
    overlay.dataset.assetErrorDialog='';
    overlay.innerHTML=`<section data-floating-content role="alertdialog" aria-modal="true" aria-label="${escapeAttr(title)}"><header><i data-lucide="triangle-alert"></i><div><strong>${escapeHtml(title)}</strong><span>后台返回的失败原因与前端调用日志</span></div></header><pre>${escapeHtml(detail)}</pre><footer><button class="hw-btn" type="button" data-asset-error-close>关闭日志</button></footer></section>`;
    document.body.appendChild(overlay);
    gwCall('preview', 'markAssetFloatingSurface', overlay, {surface:'asset-operation-error', content:'[role="alertdialog"]', close:() => overlay.remove()});
    refreshIcons();
}
function escapeHtml(value=''){
    return String(value ?? '').replace(/[&<>"']/g, ch => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch]));
}
function escapeAttr(value=''){ return escapeHtml(value); }
const ASSET_BOARD_SETTINGS_KEY = 'asset_board_navigation_v1';
const ASSET_SOURCE_BOARD_PREFIX = 'source_library_';
// 【QIN-547】托管资产与上传资源合并为「收集箱」（我的资源树第一位），不再各占一个看板入口。
const SYSTEM_ASSET_BOARDS = [
    {id:'registry', name:'资产总览', icon:'library-big', fixed:true, system:true},
    {id:'scripts', name:'剧本资产', icon:'file-text', system:true},
    {id:'projects', name:'项目制片', icon:'clapperboard', system:true},
    {id:'workflows', name:'工作流', icon:'workflow', system:true},
    {id:'prompts', name:'提示词', icon:'text-cursor-input', system:true},
    // 【QIN-580】LUT 从统计卡升级为独立子页面：紧跟在「提示词」之后，与导航入口同构。
    {id:'lut', name:'LUT 库', icon:'palette', system:true},
    {id:'canvas-assets', name:'画布资产', icon:'layout-dashboard', system:true},
];
// QIN-549：治理建议由资产工具栏顶栏入口打开，不再占用左侧看板。
const SYSTEM_BOARD_RACK_TABS = ['scripts','projects','workflows','prompts','lut','canvas-assets'];
const SCRIPT_ASSET_STORAGE_KEY = 'gwb_script_assets_v1';
let scriptArchiveItems = [];
function readScriptWorkspaceAssets(){
    try {
        const value = JSON.parse(localStorage.getItem(SCRIPT_ASSET_STORAGE_KEY) || '[]');
        return Array.isArray(value) ? value.filter(item => item && item.kind === 'script') : [];
    } catch(_) { return []; }
}
async function refreshScriptArchive(){
    const projectId = String(localStorage.getItem('workspace_project_id') || '').trim();
    const localItems = readScriptWorkspaceAssets();
    if(!projectId){ scriptArchiveItems = localItems; if(activeTab === 'scripts') render(); return; }
    try {
        const data = await apiJsonResponse(assetManagerApi.getEpisodePipelines(projectId));
        const remoteItems = (Array.isArray(data.pipelines) ? data.pipelines : []).flatMap(pipeline => {
            const stage = Array.isArray(pipeline.stages) ? pipeline.stages.find(item => item.stage === 'script') : null;
            const content = String(stage?.result?.output_text || '').trim();
            return content ? [{id:`pipeline-script:${pipeline.pipeline_id}`, pipelineId:pipeline.pipeline_id, projectId, title:`${pipeline.title || '未命名剧集'} · 剧本`, name:`${pipeline.title || '未命名剧集'}.md`, kind:'script', format:'md', content, createdAt:pipeline.updated_at || pipeline.created_at || Date.now()}] : [];
        });
        const merged = [...remoteItems, ...localItems].filter((item, index, list) => list.findIndex(value => value.pipelineId === item.pipelineId && value.content === item.content) === index);
        scriptArchiveItems = merged.sort((a, b) => Number(b.createdAt || 0) - Number(a.createdAt || 0));
        if(activeTab === 'scripts') render();
    } catch(_) { scriptArchiveItems = localItems; if(activeTab === 'scripts') render(); }
}
const ASSET_LIBRARY_PRESETS = [
    {id:'music', name:'音乐库', description:'音乐、音效与配音文件', icon:'music-2', category:'audio', kinds:['audio'], extensions:['mp3','wav','flac','aac','ogg','m4a']},
    {id:'font', name:'字体库', description:'品牌与项目字体文件', icon:'type', category:'other', kinds:[], extensions:[...ASSET_FONT_PREVIEW_EXTENSIONS]},
    {id:'model', name:'模型库', description:'三维模型与生成模型', icon:'box', category:'model', kinds:[], extensions:[...ASSET_3D_PREVIEW_EXTENSIONS,'blend','c4d','max','ma','mb','ckpt','safetensors','pt']},
    {id:'image', name:'图片库', description:'图片、插画与视觉参考', icon:'images', category:'image', kinds:['image'], extensions:[...ASSET_PREVIEW_EXTENSIONS]},
    {id:'video', name:'视频库', description:'视频、样片与动态参考', icon:'clapperboard', category:'video', kinds:['video'], extensions:[...ASSET_VIDEO_EXTENSIONS]},
    {id:'lut', name:'LUT库', description:'色彩查找表（.cube）', icon:'palette', category:'other', kinds:['lut'], extensions:['cube']},
    {id:'other', name:'其他资源', description:'其他', icon:'package-open', category:'', kinds:[], extensions:[]},
];
function defaultBoardNavigationState(){
    return {order:SYSTEM_ASSET_BOARDS.map(item => item.id), hidden:[], customBoards:[]};
}
function readBoardNavigationState(){
    const fallback = defaultBoardNavigationState();
    try {
        const raw = JSON.parse(localStorage.getItem(ASSET_BOARD_SETTINGS_KEY) || '{}');
        const customBoards = (Array.isArray(raw.customBoards) ? raw.customBoards : []).map(item => {
            const preset = ASSET_LIBRARY_PRESETS.find(value => value.id === item.preset) || ASSET_LIBRARY_PRESETS[3];
            return {
                id:String(item.id || ''),
                name:String(item.name || preset.name).slice(0, 32),
                preset:preset.id,
                icon:preset.icon,
                category:String(item.category || preset.category || ''),
                kinds:[...preset.kinds],
                extensions:[...preset.extensions],
                sources:(Array.isArray(item.sources) ? item.sources : []).map(source => ({id:String(source.id || ''), name:String(source.name || '用户素材'), path:String(source.path || ''), hidden:Boolean(source.hidden)})).filter(source => source.id),
                automaticHidden:Boolean(item.automaticHidden),
                excludedSourceIds:[...new Set((Array.isArray(item.excludedSourceIds) ? item.excludedSourceIds : []).map(String).filter(Boolean))],
                system:false,
            };
        }).filter((item, index, list) => /^library_[a-z0-9_]+$/i.test(item.id) && list.findIndex(value => value.id === item.id) === index);
        const validIds = new Set([...SYSTEM_ASSET_BOARDS.map(item => item.id), ...customBoards.map(item => item.id)]);
        const incomingOrder = Array.isArray(raw.order) ? raw.order.map(String).filter(id => validIds.has(id) && id !== 'registry') : [];
        const remaining = [...validIds].filter(id => id !== 'registry' && !incomingOrder.includes(id));
        const hidden = (Array.isArray(raw.hidden) ? raw.hidden : []).map(String).filter(id => id !== 'registry' && validIds.has(id));
        return {order:['registry', ...incomingOrder, ...remaining], hidden:[...new Set(hidden)], customBoards};
    } catch(_) {
        return fallback;
    }
}
function ensureBoardNavigationState(){
    if(!boardNavigationState) boardNavigationState = readBoardNavigationState();
    return boardNavigationState;
}
function saveBoardNavigationState(){
    try { localStorage.setItem(ASSET_BOARD_SETTINGS_KEY, JSON.stringify(ensureBoardNavigationState())); }
    catch(_) {}
}
function registrySourceBoardPath(root={}){
    const rootId = String(root.id || '');
    const local = localLibraries.find(item => String(item.id || '') === rootId);
    if(local?.path) return local.path;
    const sharedId = rootId.startsWith('shared_') ? rootId.slice(7) : rootId;
    const shared = sharedFolders.find(item => String(item.id || '') === sharedId);
    return String(shared?.path || '');
}
function registrySourceBoardName(value=''){
    const name = String(value || '素材库').trim();
    return name.endsWith('库素材') ? `${name.slice(0, -3)}素材库` : name;
}
function configuredRegistrySourceIds(){
    // Facets retain detached roots for cache recovery on the server. Only
    // roots still present in the current storage settings may become source
    // boards in the navigation.
    if(!storageSettingsState.dirs || !Object.keys(storageSettingsState.dirs).length) return null;
    return new Set([
        ...localLibraries.map(item => String(item?.id || '')).filter(Boolean),
        ...sharedFolders.map(item => `shared_${String(item?.id || '')}`).filter(id => id !== 'shared_'),
    ]);
}
function registrySourceBoards(state=ensureBoardNavigationState()){
    const attachedSourceIds = new Set((state.customBoards || []).flatMap(board => (board.sources || []).map(source => String(source.id || ''))));
    const configuredSourceIds = configuredRegistrySourceIds();
    return (registryFacets.roots || [])
        .filter(root => root?.id && !attachedSourceIds.has(String(root.id))
            && (!configuredSourceIds || configuredSourceIds.has(String(root.id)))
            && ['local','shared'].includes(String(root.kind || '').toLowerCase()))
        .map(root => {
            const rootId = String(root.id);
            const sourceName = String(root.name || '素材库');
            const name = registrySourceBoardName(sourceName);
            return {
                id:`${ASSET_SOURCE_BOARD_PREFIX}${rootId}`,
                name,
                icon:String(root.kind || '') === 'shared' ? 'folder-network' : 'database',
                preset:'other', category:'', kinds:[], extensions:[],
                sources:[{id:rootId, name:sourceName, path:registrySourceBoardPath(root), hidden:false}],
                rootId, automaticHidden:true, excludedSourceIds:[],
                system:false, sourceBoard:true,
            };
        });
}
function allAssetBoards(){
    const state = ensureBoardNavigationState();
    const map = new Map([...SYSTEM_ASSET_BOARDS, ...state.customBoards].map(item => [item.id, item]));
    const ordered = state.order.map(id => map.get(id)).filter(Boolean);
    const orderedIds = new Set(ordered.map(item => item.id));
    return [...ordered, ...registrySourceBoards(state).filter(item => !orderedIds.has(item.id))];
}
function assetBoardById(id){ return allAssetBoards().find(item => item.id === id) || null; }
function visibleAssetBoards(){
    const hidden = new Set(ensureBoardNavigationState().hidden);
    return allAssetBoards().filter(item => item.id === 'registry' || !hidden.has(item.id));
}
function renderBoardNavigation(){
    if(!assetTabsEl) return;
    const boards = visibleAssetBoards();
    const embeddedVault = document.documentElement.classList.contains('vault-shell-embedded');
    assetTabsEl.innerHTML = `<div class="asset-board-list">${boards.map(board => {
        const active = board.id === activeTab;
        const movable = !board.fixed && !board.sourceBoard;
        const title = board.sourceBoard ? '已登记素材库' : board.fixed ? '固定看板' : '拖动调整看板顺序';
        return `<button class="asset-board-tab btn-segmented-item ${active ? 'active' : ''} ${board.fixed ? 'fixed' : ''} ${board.sourceBoard ? 'source-board' : ''}" type="button" role="tab" aria-selected="${active}" tabindex="${active ? '0' : '-1'}" data-tab="${escapeAttr(board.id)}" ${movable ? `draggable="true" data-board-drag="${escapeAttr(board.id)}"` : ''} title="${title}"><i data-lucide="${escapeAttr(board.icon)}"></i><span>${escapeHtml(board.name)}</span>${board.fixed ? '<i class="asset-board-lock" data-lucide="lock-keyhole"></i>' : ''}</button>`;
    }).join('')}<button class="hw-btn asset-board-add" type="button" data-board-create aria-label="新建资源"><i data-lucide="plus"></i><span>新建资源</span></button></div>
    <div class="asset-board-menu-actions">
        ${embeddedVault ? '' : `<div id="assetStatus" class="asset-status" role="status" aria-live="polite"${currentIndexState ? ` data-gw-index-state="${escapeAttr(currentIndexState)}"` : ''}>${escapeHtml(currentStatusText)}</div>`}
        ${embeddedVault ? '' : '<button class="hw-btn" type="button" data-refresh-assets><i data-lucide="refresh-cw"></i><span>刷新</span></button>'}
        <button class="hw-btn" type="button" data-board-settings-open ${embeddedVault ? 'hidden aria-hidden="true" tabindex="-1"' : ''}><i data-lucide="panel-top"></i><span>资产设置</span></button>
    </div>`;
    refreshIcons();
}
function resetRegistryScroll(){
    savedScrollPositions.registry = 0;
    const currentScroll = root.querySelector('.registry-assets-scroll');
    if(currentScroll) currentScroll.scrollTop = 0;
}
function reorderAssetBoard(sourceId, targetId){
    if(!sourceId || sourceId === 'registry' || !targetId || sourceId === targetId) return false;
    const state = ensureBoardNavigationState();
    const order = state.order.filter(id => id !== sourceId);
    const targetIndex = order.indexOf(targetId);
    if(targetIndex < 0) return false;
    order.splice(Math.max(1, targetIndex), 0, sourceId);
    state.order = ['registry', ...order.filter(id => id !== 'registry')];
    saveBoardNavigationState();
    return true;
}
// 缓存根目录不是 storageDir_* 字段，用独立 key 复用同一套选夹与自动保存链路。
const THUMBNAIL_DIRECTORY_FIELD_KEY = 'thumbnail_cache';
// 允许根是「登记」动作而非 storageDir_* 字段，同样用独立 key 复用选夹与自动保存链路。
const ALLOWED_ROOT_FIELD_KEY = 'allowed_root';
const BOARD_CREATE_PATH_FIELD_KEY = 'board_create_path';
// 允许留空的目录字段：清空后照常保存（如回收根目录留空即跟随素材源）。
const OPTIONAL_DIRECTORY_FIELDS = new Set(['trash']);
const THUMBNAIL_CACHE_EXCLUSION_HINT = '检测到缓存位于素材库内，系统已自动加入扫描排除名单，防止重复索引';
function directoryFieldInput(key){
    if(key === THUMBNAIL_DIRECTORY_FIELD_KEY) return document.getElementById('thumbnailDirectory');
    if(key === ALLOWED_ROOT_FIELD_KEY) return document.getElementById('allowedRootPath');
    if(key === BOARD_CREATE_PATH_FIELD_KEY) return document.getElementById('boardCreatePath');
    return document.getElementById('storageDir_' + key);
}
async function reindexAssetSource(sourceId){
    if(!sourceId) return {ok:false, error:'缺少素材来源'};
    if(assetReindexRequests.has(sourceId)) return assetReindexRequests.get(sourceId);
    const nonce = globalThis.crypto?.randomUUID?.() || `${Date.now()}-${Math.random().toString(36).slice(2)}`;
    // 【修复问题7】索引任务启动失败不再吞成 null：结构化返回 {ok,error} 供调用方回显
    const request = apiJsonResponse(assetManagerApi.reindexAssetSource({roots:[sourceId], hash_files:true, max_files:250000, background:true}, {
        headers:{'Idempotency-Key':`asset-reindex:${sourceId}:${nonce}`},
    })).then(data => ({ok:true, data})).catch(err => ({ok:false, error:err?.message || '索引任务启动失败'})).finally(() => assetReindexRequests.delete(sourceId));
    assetReindexRequests.set(sourceId, request);
    return request;
}
async function waitForAssetIndexJob(jobId, sourceName='素材库'){
    if(!jobId) return null;
    const deadline = Date.now() + 120000;
    while(Date.now() < deadline){
        try {
            const data = await apiJsonResponse(assetManagerApi.getWorkspaceJob(jobId));
            const job = data?.job || {};
            if(job.status === 'succeeded'){
                markIndexState('indexed');
                await Promise.allSettled([refreshRegistryAssets(false), refreshRegistryFacetData()]);
                if(activeTab === 'registry' || assetBoardById(activeTab)?.system === false) render();
                setStatus(`素材库“${sourceName}”索引完成，已扫描 ${Number(job.result?.scanned || 0)} 项`);
                return job;
            }
            if(['failed','cancelled','interrupted'].includes(job.status)){
                markIndexState('failed');
                const message = ({
                    LUT_CUBE_INVALID:'LUT 文件格式无效，请检查文件头和采样数据',
                    LUT_CUBE_TOO_LARGE:'LUT 文件超过 64 MiB 上限',
                })[job.error_code] || job.error_code || job.status;
                setStatus(`素材库“${sourceName}”索引失败：${message}`);
                return job;
            }
            const phase = job.progress?.phase || job.status;
            const phaseLabel = phase === 'queued' ? '等待扫描' : phase === 'indexing' ? '正在写入索引' : '正在扫描';
            setStatus(`素材库“${sourceName}”${phaseLabel}… 已扫描 ${Number(job.progress?.scanned || 0)} 项`);
        } catch(_) {}
        await new Promise(resolve => setTimeout(resolve, 500));
    }
    setStatus(`素材库“${sourceName}”索引仍在后台运行，可稍后刷新查看`);
    return null;
}
async function copyTextToClipboard(text){
    const value = String(text || '');
    if(!value) return false;
    try {
        if(navigator.clipboard?.writeText){ await navigator.clipboard.writeText(value); return true; }
    } catch(_) {}
    try {
        const ta = document.createElement('textarea');
        ta.value = value;
        ta.setAttribute('readonly', '');
        ta.style.position = 'fixed';
        ta.style.left = '-9999px';
        ta.style.top = '0';
        document.body.appendChild(ta);
        ta.focus();
        ta.select();
        const ok = document.execCommand('copy');
        ta.remove();
        return ok;
    } catch(_) { return false; }
}
async function apiJsonResponse(response){
    const res = await response;
    const data = await res.json().catch(() => ({}));
    if(res.status === 401) window.dispatchEvent(new CustomEvent('asset-auth-needed'));
    if(!res.ok){
        const detail = data.detail;
        const error = new Error((typeof detail === 'object' ? detail?.message : detail) || data.message || '操作失败');
        error.status = res.status;
        error.code = typeof detail === 'object' ? detail?.code : null;
        if(error.status === 409 && error.code === 'VERSION_CONFLICT'){
            error.message = VERSION_CONFLICT_MESSAGE;
            setStatus(`${VERSION_CONFLICT_MESSAGE}（版本冲突）`);
            void gwCall('organize', 'refreshCASReadState');
        }
        throw error;
    }
    return data;
}
function requireExpectedVersion(value, label){
    const version = Number(value);
    if(!Number.isSafeInteger(version) || version < 1) throw new Error(`${label}版本缺失，请刷新`);
    return version;
}
const STORAGE_KIND_LABELS = {upload:'\u4e0a\u4f20\u7d20\u6750', generated:'\u751f\u6210\u7d20\u6750', local:'\u672c\u5730\u7d20\u6750'};
const DEFAULT_LOCAL_LIBRARY_NAME = '\u672c\u5730\u7d20\u6750';
function storageCleanName(value, fallback=DEFAULT_LOCAL_LIBRARY_NAME){
    const text = String(value || '').trim().replace(new RegExp('\\s+', 'g'), ' ');
    return text || fallback;
}
function storageExtraDefaultName(path='', index=0){
    const name = String(path || '').replace(new RegExp('\\\\', 'g'), '/').split('/').filter(Boolean).pop();
    return storageCleanName(name, `\u76ee\u5f55 ${index + 2}`);
}
function canManageProjectWorkspaceSettings(){
    if(registryPrincipalLoading) return false;
    const role = String(registryPrincipal?.role || '').trim().toLowerCase();
    return Boolean(registryPrincipal?.local_mode) || role === 'admin';
}
function canBrowseServerDirectories(){
    if(registryPrincipalLoading) return false;
    return String(registryPrincipal?.role || '').trim().toLowerCase() === 'admin';
}
function canManageAllowedRoots(){ return canBrowseServerDirectories(); }
function storageExtraLocalEntries(){
    const entries = Array.isArray(storageSettingsState.extraLocalEntries) ? storageSettingsState.extraLocalEntries : [];
    if(entries.length){
        return entries.map((entry, index) => ({
            id:String(entry.id || entry.kind || `local_extra_${index}`),
            kind:String(entry.kind || entry.id || `local_extra_${index}`),
            name:storageCleanName(entry.name, storageExtraDefaultName(entry.path, index)),
            path:String(entry.path || '').trim()
        }));
    }
    return (Array.isArray(storageSettingsState.extraLocalDirs) ? storageSettingsState.extraLocalDirs : [])
        .map((path, index) => ({id:`local_extra_${index}`, kind:`local_extra_${index}`, name:storageExtraDefaultName(path, index), path:String(path || '').trim()}));
}
function storageLocalLibraryNames(){
    const names = {...(storageSettingsState.localLibraryNames || {})};
    names.local = storageCleanName(names.local, DEFAULT_LOCAL_LIBRARY_NAME);
    storageExtraLocalEntries().forEach((entry, index) => { names[entry.id] = storageCleanName(entry.name, storageExtraDefaultName(entry.path, index)); });
    return names;
}
function activeLocalLibrary(){ return localLibraries.find(lib => lib.id === activeLocalLibraryId) || localLibraries[0] || {id:'local', kind:'local', name:DEFAULT_LOCAL_LIBRARY_NAME, path:''}; }
function activeLocalLibraryKind(){ return activeLocalLibrary().kind || activeLocalLibrary().id || 'local'; }
function activeLocalLibraryName(){ return storageCleanName(activeLocalLibrary().name, DEFAULT_LOCAL_LIBRARY_NAME); }
function trapStorageSettingsFocus(event){
    if(event.key !== 'Tab' || !storageSettingsState.open || projectDirectoryTemplateManagerState.open) return false;
    const dialog = document.querySelector('#storageSettingsOverlay [role="dialog"]');
    if(!dialog) return false;
    const focusables = [...dialog.querySelectorAll('button:not(:disabled),input:not(:disabled),select:not(:disabled),textarea:not(:disabled),[href]')]
        .filter(element => !element.hidden && element.getAttribute('aria-hidden') !== 'true');
    if(!focusables.length){ event.preventDefault(); dialog.focus(); return true; }
    const active = document.activeElement;
    const first = focusables[0];
    const last = focusables[focusables.length - 1];
    if(event.shiftKey && (active === first || !dialog.contains(active))){ event.preventDefault(); last.focus(); return true; }
    if(!event.shiftKey && (active === last || !dialog.contains(active))){ event.preventDefault(); first.focus(); return true; }
    return false;
}
function trapProjectDirectoryTemplateManagerFocus(event){
    if(event.key !== 'Tab' || !projectDirectoryTemplateManagerState.open) return false;
    const dialog = document.querySelector('#projectDirectoryTemplateSheetOverlay [role="dialog"]');
    if(!dialog) return false;
    const focusables = [...dialog.querySelectorAll('button:not(:disabled),input:not(:disabled),select:not(:disabled),textarea:not(:disabled),[href]')]
        .filter(element => !element.hidden && element.getAttribute('aria-hidden') !== 'true');
    if(!focusables.length){ event.preventDefault(); dialog.focus(); return true; }
    const active = document.activeElement;
    const first = focusables[0];
    const last = focusables[focusables.length - 1];
    if(event.shiftKey && (active === first || !dialog.contains(active))){ event.preventDefault(); last.focus(); return true; }
    if(!event.shiftKey && (active === last || !dialog.contains(active))){ event.preventDefault(); first.focus(); return true; }
    return false;
}
async function saveStorageSettings(){
    gwCall('settings', 'syncStorageSettingsInputsToState');
    const dirs = {...(storageSettingsState.dirs || {})};
    const payload = {
        dirs,
        upload:dirs.upload || '',
        generated:dirs.generated || '',
        local:dirs.local || '',
        extra_local_entries:storageExtraLocalEntries().filter(entry => entry.path),
        local_library_names:storageLocalLibraryNames(),
        local_library_order:(localLibraries.length ? localLibraries : storageSettingsState.localLibraries || []).map(lib => lib.id).filter(Boolean)
    };
    if(Number.isFinite(Number(storageSettingsState.revision))) payload.expected_version = Number(storageSettingsState.revision);
    try {
        const data = await apiJsonResponse(assetManagerApi.updateStorageSettings(payload));
        gwCall('settings', 'applyStorageSettingsData', data || {});
        gwCall('settings', 'renderStorageSettingsModal');
        setStatus('保存目录已更新');
    } catch(error) {
        if(/revision|conflict|409/i.test(String(error?.message || ''))){
            try {
                gwCall('settings', 'applyStorageSettingsData', await apiJsonResponse(assetManagerApi.getStorageSettings()));
                gwCall('settings', 'renderStorageSettingsModal');
                setStatus('目录设置已被其他管理员更新，请检查最新值后重新保存');
            } catch(_) {}
        }
        throw error;
    }
}
async function loadGlobalDirectorySettings(){
    storageSettingsState.open = true;
    directorySettingsReady = false;
    directorySettingsError = '';
    gwCall('settings', 'renderGlobalDirectorySettings');
    try {
        await loadRegistryPrincipal();
        if(!canManageProjectWorkspaceSettings()) throw new Error('仅管理员可查看和修改目录设置');
        const [storage, thumbnail, status, roots] = await Promise.all([
            apiJsonResponse(assetManagerApi.getStorageSettings()),
            apiJsonResponse(assetManagerApi.getThumbnailSettings()),
            apiJsonResponse(assetManagerApi.getRegistryStatus()),
            canManageAllowedRoots()
                ? apiJsonResponse(assetManagerHttp.request('/api/storage-settings/roots', {method:'GET', credentials:'same-origin'}))
                : Promise.resolve(null),
        ]);
        gwCall('settings', 'applyStorageSettingsData', storage);
        if(roots) gwCall('settings', 'applyAllowedRootSettingsData', roots);
        registryStatus = status;
        gwCall('settings', 'applyThumbnailSettingsSnapshot', thumbnail, {loading:false});
        if(isTauriClientMode()) await gwCall('upload', 'loadClientUploadSourceDir');
        directorySettingsReady = true;
        currentStatusText = '目录已同步';
    } catch(error){ directorySettingsError = error.message || '目录设置读取失败'; }
    gwCall('settings', 'renderGlobalDirectorySettings');
}
let allowedRootsRemovedModalOpen = false;
let pendingPurgeRoot = null;
async function saveAllowedRootsRequest(url, method, payload){
    allowedRootSettingsState = {...allowedRootSettingsState, saving:true, error:''};
    gwCall('settings', 'renderGlobalDirectorySettings');
    try {
        const data = await apiJsonResponse(assetManagerHttp.request(url, {
            method, credentials:'same-origin', headers:{'Content-Type':'application/json'}, body:JSON.stringify(payload)
        }));
        gwCall('settings', 'applyAllowedRootSettingsData', data);
        if(method === 'POST' && url === '/api/storage-settings/roots'){
            const input = document.getElementById('allowedRootPath');
            if(input) input.value = '';
        }
        let successMessage = '允许根设置已更新';
        if(method === 'POST' && url.includes('/aliases/confirm')){
            successMessage = '路径别名已确认';
        } else if(method === 'POST' && url.includes('/restore')){
            successMessage = '允许根已恢复并重新比对';
        } else if(method === 'DELETE' && url.includes('/purge')){
            successMessage = '已彻底清除允许根的所有索引与缓存记录（原始素材保留）';
        } else if(method === 'DELETE'){
            successMessage = '允许根已移除';
        }
        setStatus(successMessage);
        if(typeof loadRegistryBootstrap === 'function'){
            // 【QIN-585 AC2】void 路径同样要接住回读拒绝：登记函数现在返回原始请求，
            // 列表 500 若不 catch 会变成未处理拒绝，且用户只看到「已更新」。
            void refreshRegistryBootstrapTracked().catch(error => {
                reportRegistryWriteRefreshFailure('允许根已保存，但列表刷新失败', error);
            });
        }
    } catch(error){
        allowedRootSettingsState = {
            ...allowedRootSettingsState,
            error:`${error.code ? `${error.code}：` : ''}${error.message || '允许根设置失败'}`
        };
        setStatus(error.message || '允许根设置失败');
    } finally {
        allowedRootSettingsState = {...allowedRootSettingsState, saving:false};
        gwCall('settings', 'renderGlobalDirectorySettings');
    }
}
document.addEventListener('click', event => {
    const target = event.target?.closest?.(
        '[data-storage-root-add],[data-storage-root-delete],[data-storage-root-alias-confirm],' +
        '[data-storage-root-removed-toggle],[data-modal-close="removed-roots"],' +
        '[data-storage-root-restore],[data-storage-root-purge-prompt],[data-purge-cancel],[data-purge-confirm]'
    );
    if(!target){
        if(event.target?.getAttribute?.('data-modal-backdrop') === 'purge-confirm'){
            pendingPurgeRoot = null;
            gwCall('settings', 'renderGlobalDirectorySettings');
            return;
        }
        if(event.target?.getAttribute?.('data-modal-backdrop') === 'removed-roots'){
            allowedRootsRemovedModalOpen = false;
            gwCall('settings', 'renderGlobalDirectorySettings');
            return;
        }
        return;
    }
    event.preventDefault();
    if(target.hasAttribute('data-purge-cancel')){
        pendingPurgeRoot = null;
        gwCall('settings', 'renderGlobalDirectorySettings');
        return;
    }
    if(target.getAttribute('data-modal-close') === 'removed-roots'){
        allowedRootsRemovedModalOpen = false;
        gwCall('settings', 'renderGlobalDirectorySettings');
        return;
    }
    if(target.hasAttribute('data-storage-root-removed-toggle')){
        allowedRootsRemovedModalOpen = !allowedRootsRemovedModalOpen;
        gwCall('settings', 'renderGlobalDirectorySettings');
        return;
    }
    const purgePromptId = target.getAttribute('data-storage-root-purge-prompt');
    if(purgePromptId){
        const purgePath = target.getAttribute('data-root-path') || '';
        pendingPurgeRoot = {id: purgePromptId, path: purgePath};
        gwCall('settings', 'renderGlobalDirectorySettings');
        return;
    }
    if(allowedRootSettingsState.saving) return;
    const expectedVersion = Number(allowedRootSettingsState.revision || 1);
    const purgeConfirmId = target.getAttribute('data-purge-confirm');
    if(purgeConfirmId){
        pendingPurgeRoot = null;
        void saveAllowedRootsRequest(`/api/storage-settings/roots/${encodeURIComponent(purgeConfirmId)}/purge`, 'DELETE', {expected_version: expectedVersion});
        return;
    }
    const restoreRootId = target.getAttribute('data-storage-root-restore');
    if(restoreRootId){
        void saveAllowedRootsRequest(`/api/storage-settings/roots/${encodeURIComponent(restoreRootId)}/restore`, 'POST', {expected_version: expectedVersion});
        return;
    }
    if(target.hasAttribute('data-storage-root-add')){
        const path = String(document.getElementById('allowedRootPath')?.value || '').trim();
        void gwCall('settings', 'registerAllowedRootPath', path);
        return;
    }
    const removeRootId = target.getAttribute('data-storage-root-delete');
    if(removeRootId){
        if(!window.confirm('移除该允许根？已索引记录和源文件会保留，目录将停止扫描并显示为已移除。')) return;
        void saveAllowedRootsRequest(`/api/storage-settings/roots/${encodeURIComponent(removeRootId)}`, 'DELETE', {expected_version:expectedVersion});
        return;
    }
    const rootId = target.getAttribute('data-storage-root-alias-confirm');
    const path = target.getAttribute('data-storage-root-alias-path') || '';
    if(rootId && path){
        void saveAllowedRootsRequest(`/api/storage-settings/roots/${encodeURIComponent(rootId)}/aliases/confirm`, 'POST', {path, expected_version:expectedVersion});
    }
});
document.addEventListener('change', event => {
    // 客户端来源目录只保存在客户端本机，不进入服务器 storage settings。
    const input = event.target?.closest?.('#clientUploadSourceDir');
    if(!input) return;
    void gwCall('upload', 'saveClientUploadSourceDir', String(input.value || '').trim());
});
const PROJECT_DIRECTORY_TEMPLATE_TYPES = [
    {id:'film', label:'影片'},
    {id:'series', label:'剧集'},
    {id:'other', label:'其他'},
];
const PROJECT_DIRECTORY_TEMPLATE_RESERVED_NAMES = new Set([
    'con', 'prn', 'aux', 'nul',
    ...Array.from({length:9}, (_, index) => `com${index + 1}`),
    ...Array.from({length:9}, (_, index) => `lpt${index + 1}`),
]);
const PROJECT_DIRECTORY_TEMPLATE_FILE_SUFFIXES = new Set([
    '.7z', '.bat', '.bash', '.bin', '.bz2', '.cjs', '.cmd', '.com', '.css', '.csv',
    '.dll', '.doc', '.docx', '.exe', '.fish', '.gz', '.htm', '.html', '.jar', '.jpeg',
    '.jpg', '.js', '.json', '.lua', '.md', '.mjs', '.msi', '.pdf', '.php', '.pl', '.png',
    '.ps1', '.psm1', '.py', '.pyw', '.rb', '.sh', '.svg', '.tar', '.tgz', '.toml', '.ts',
    '.tsx', '.txt', '.vbe', '.vbs', '.xml', '.yaml', '.yml', '.zip', '.zsh',
]);
const THUMBNAIL_CACHE_BLOCK_CODES = ['CACHE_DIRECTORY_NOT_ALLOWED', 'CACHE_DIRECTORY_NOT_FOUND'];
function assetLibraries(){
    return Array.isArray(assetLibrary.libraries) && assetLibrary.libraries.length
        ? assetLibrary.libraries
        : [{id:'default', name:'默认资产库', categories:assetLibrary.categories || []}];
}
function activeAssetLibrary(){
    const libs = assetLibraries();
    return libs.find(lib => lib.id === activeAssetLibraryId) || libs[0] || null;
}
function assetLibraryForItemId(assetId){
    const id = String(assetId || '');
    for(const lib of assetLibraries()) for(const cat of lib.categories || []) for(const item of cat.items || []){
        if(String(item.id || item.asset_id || '') === id || workflowAssetId(item) === id) return lib;
    }
    return null;
}
function assetLibraryVersionForItem(assetId){
    const library = assetLibraryForItemId(assetId);
    return requireExpectedVersion(library?.version, '素材库');
}
function activeWorkflowLibrary(){
    const libs = assetLibraries();
    return libs.find(lib => workflowLibraryId(lib) === activeWorkflowLibraryId) || libs[0] || null;
}
function assetCategories(){
    return (activeAssetLibrary()?.categories || []).filter(cat => (cat.type || 'image') === 'image');
}
function workflowCategories(){
    const cats = [];
    assetLibraries().forEach(lib => {
        (lib.categories || []).filter(cat => (cat.type || '') === 'workflow').forEach(cat => {
            cats.push({...cat, __libraryId:workflowLibraryId(lib), __libraryName:lib.name || '资产库'});
        });
    });
    return cats;
}
function activeAssetCategory(){
    const cats = assetCategories();
    return cats.find(cat => cat.id === activeAssetCategoryId) || cats[0] || null;
}
function activeWorkflowCategory(){
    const cats = workflowCategories();
    return cats.find(cat => workflowCategoryId(cat) === activeWorkflowCategoryId && cat.__libraryId === activeWorkflowLibraryId)
        || cats.find(cat => workflowCategoryId(cat) === activeWorkflowCategoryId)
        || cats[0]
        || null;
}
function assetCountForLibrary(lib){
    return (lib?.categories || [])
        .filter(cat => (cat.type || 'image') === 'image')
        .reduce((sum, cat) => sum + ((cat.items || []).length), 0);
}
function promptLibraries(){
    const libs = Array.isArray(promptLibrary.libraries)
        ? promptLibrary.libraries.filter(Boolean).map(filteredPromptRegistryLibrary)
        : [];
    if(!libs.length) return [{id:'system', name:'系统提示词库', system:true, items:[], categories:[]}];
    const system = libs.filter(lib => lib.id === 'system');
    const others = libs.filter(lib => lib.id !== 'system');
    return [...system, ...others];
}
function isSystemPromptLibrary(lib){
    return !lib || lib.id === 'system';
}
function activePromptLibrary(){
    const libs = promptLibraries();
    return libs.find(lib => lib.id === activePromptLibraryId) || libs[0] || null;
}
function promptCategoryVersion(categoryId){
    for(const lib of promptLibraries()) for(const category of lib.categories || []){
        if(String(category.id || category.category_id || '') === String(categoryId || '')){
            return requireExpectedVersion(category.version, '提示词分类');
        }
    }
    throw new Error('提示词分类已变化，请刷新');
}
// 按“某个库自身”解析分类：库有自己的分类就用它；只有系统库在完全为空时才补默认内置分类。
// 新建的普通库分类为空 → 返回 []，不能借用当前激活库/系统库的分类（否则树里会错显系统分类）。
function promptCategoriesFor(lib){
    const fromLib = Array.isArray(lib?.categories) ? lib.categories.filter(c => c?.id) : [];
    if(fromLib.length) return fromLib;
    if(!isSystemPromptLibrary(lib)) return [];
    return [
        {id:'view', name:'视角'},
        {id:'storyboard', name:'分镜'},
        {id:'character', name:'角色'},
        {id:'product', name:'产品'},
        {id:'lighting', name:'光影'},
        {id:'custom', name:'我的'}
    ];
}
function activePromptCategories(){
    return promptCategoriesFor(activePromptLibrary());
}
const PROMPT_BUILTIN_CATEGORY_IDS = new Set(['view','storyboard','character','product','lighting','custom']);
const ASSET_CLASS_GROUPS = [
    {id:'environment', name:'环境', dims:['environment','scene','space','mood']},
    {id:'composition', name:'构图', dims:['composition']},
    {id:'lighting', name:'光影', dims:['lighting','color']},
    {id:'model', name:'模特', dims:['model','people']},
    {id:'subject', name:'主体', dims:['subject','objects','materials']},
    {id:'style', name:'风格', dims:['style','use_case','quality']},
    {id:'tags', name:'标签', dims:['tags']},
];
const ASSET_CLASSIFICATION_LABELS = {
    environment:'环境',
    scene:'场景',
    space:'空间',
    subject:'主体',
    model:'模特',
    people:'人物',
    style:'风格',
    lighting:'光影',
    color:'色彩',
    composition:'构图',
    mood:'氛围',
    use_case:'用途',
    objects:'物体',
    materials:'材质',
    quality:'质量',
    tags:'标签'
};
const {
    assetClassFilterKey,
    parseAssetClassFilterKey,
    assetClassificationChips,
    assetClassificationEntriesForItems,
    assetClassificationEntryGroup,
    assetClassificationSearchText,
    groupedAssetClassificationEntries,
    assetClassificationGroupIdForFilter,
} = createAssetClassificationHelpers({groups:ASSET_CLASS_GROUPS, labels:ASSET_CLASSIFICATION_LABELS});
function assetKind(item){
    if(workflowSourceId(item)) return 'workflow';
    const url = String(item?.url || '').toLowerCase();
    const kind = String(item?.kind || item?.type || '').toLowerCase();
    if(kind.includes('video') || ASSET_VIDEO_EXTENSIONS.has(assetFileExtension(item)) || /\.(mp4|webm|mov|m4v)(\?|#|$)/.test(url)) return 'video';
    if(kind.includes('audio') || /\.(mp3|wav|flac|ogg|m4a)(\?|#|$)/.test(url)) return 'audio';
    if(kind.includes('text') || kind.includes('workflow') || kind.includes('document') || kind.includes('project_file') || /\.(txt|json|csv|srt|vtt|md|zip|psd|aep|blend)(\?|#|$)/.test(url)) return 'text';
    return 'image';
}
function assetClassificationEntriesForLibrary(lib=activeAssetLibrary()){
    const items = [];
    (lib?.categories || []).filter(cat => (cat.type || 'image') === 'image').forEach(cat => items.push(...(cat.items || [])));
    return assetClassificationEntriesForItems(items);
}
function activeAssetClassEntry(){
    return assetClassificationEntriesForLibrary().find(entry => entry.key === activeAssetClassFilter) || null;
}
function workflowKindLabel(item){
    if(workflowSourceId(item)) return '神工坊工作流';
    const format = String(item?.format || '').toLowerCase();
    const url = String(item?.url || '').toLowerCase();
    if(format === 'json' || url.endsWith('.json')) return 'JSON 工作流';
    return 'ZIP 工作流包';
}
function assetFileExtension(item={}){
    const source = String(item.name || item.relative_path || item.relativePath || item.file || item.url || item.public_url || '').split('?')[0].split('#')[0];
    const match = source.toLowerCase().match(/\.([a-z0-9]+)$/);
    return match ? match[1] : '';
}
function assetSupportsImagePreview(item={}){
    return ASSET_PREVIEW_EXTENSIONS.has(assetFileExtension(item));
}
// 缩略图走服务端缩放代理（/api/media-preview），把大原图降到 ~256px 再传给浏览器。素材多时滚动只解码小图，
// 不再因为加载/解码整张原图而卡。仅对本地 /output、/assets 的图片/视频生效，其它地址原样返回。
function assetPreviewUrl(url, w=256, item={}){
    const raw = String(url || '');
    if(!raw || raw.startsWith('data:') || raw.startsWith('blob:')) return raw;
    let path = raw;
    if(/^https?:\/\//i.test(raw)){
        try { path = new URL(raw).pathname; } catch(e) { return raw; }
    }
    const local = path.startsWith('/output/') || path.startsWith('/assets/') || path.startsWith('/api/storage-files/') || path.startsWith('/api/shared-folders/') || path.startsWith('/api/asset-registry/');
    if(!local) return raw;
    const video = ASSET_VIDEO_EXTENSIONS.has(assetFileExtension({...item, url:raw}));
    if(!video && !assetSupportsImagePreview({...item, url:raw})) return raw;
    if(!video && assetFileExtension({...item, url:raw}) === 'svg') return raw;
    const width = Math.max(64, Math.min(2048, Math.round(Number(w) || 256)));
    if(raw.includes('/image-versions/') && /\/media(?:\?|#|$)/.test(raw)){
        const separator=raw.includes('?') ? '&' : '?';
        return `${raw}${separator}w=${width}`;
    }
    const params = new URLSearchParams({w:String(width)});
    params.set('rv', ASSET_PREVIEW_RENDER_VERSION);
    if(item?.id && (item?.storage_root_id || item?.stored_name) && !item?.is_remote){
        params.set('asset_id', String(item.id));
        // 【修复问题1】按资产粒度版本：仅 updated_at/indexed_at 变化的资产 URL 才变化，任务完成不再整片失效
        params.set('v', String(Number(item?.updated_at || item?.indexed_at || 0)));
    } else {
        params.set('url', raw);
        // 旧版 url 参数没有资产元数据，保留会话级版本（不再因任务完成而变化）
        if(thumbnailCacheVersion) params.set('v', String(thumbnailCacheVersion));
    }
    return `/api/media-preview?${params.toString()}`;
}
function assetVideoUrl(url, item={}){
    const raw = String(url || '');
    const extension = assetFileExtension({...item, url:raw});
    if(!raw || ASSET_BROWSER_VIDEO_EXTENSIONS.has(extension)) return raw;
    const params = new URLSearchParams();
    if(item?.id && (item?.storage_root_id || item?.stored_name) && !item?.is_remote){
        params.set('asset_id', String(item.id));
        // 【修复问题1】转码地址同样按资产粒度版本
        params.set('v', String(Number(item?.updated_at || item?.indexed_at || 0)));
    } else {
        params.set('url', raw);
        if(thumbnailCacheVersion) params.set('v', String(thumbnailCacheVersion));
    }
    return `/api/media-transcode?${params.toString()}`;
}
function assetThumb(item){
    if(workflowSourceId(item)) return workflowThumb(item);
    // 【QIN-554】LUT 卡片直接用「统一样片套该 LUT」的真实像素预览，不用文本图标凑合。
    if(lutPreviewIsAsset(item)) return renderRegistryLutThumb(item);
    const kind = assetKind(item);
    // 首帧与视频分层显示，避免浏览器把 video poster 按元素尺寸拉伸。
    if(kind === 'video'){
        const poster = assetPreviewUrl(item.url, 256, item);
        return `<img class="asset-video-poster" src="${escapeAttr(poster)}" alt="${escapeAttr(item.name || '视频')}" data-preview-fallback="${escapeAttr(assetFileExtension(item).toUpperCase() || 'VIDEO')}" loading="lazy" decoding="async"><video class="asset-video-hover" src="${escapeAttr(assetVideoUrl(item.url, item))}" muted preload="none" playsinline data-asset-hover-preview="video"></video>`;
    }
    if(kind === 'audio'){
        const cover = assetSupportsImagePreview(item)
            ? `<img src="${escapeAttr(assetPreviewUrl(item.url, 384, item))}" alt="${escapeAttr(item.name || '音频')}" data-preview-fallback="AUDIO" loading="lazy" decoding="async">`
            : '<div class="asset-file-icon"><i data-lucide="file-audio"></i><span>音频</span></div>';
        return `${cover}<audio src="${escapeAttr(item.url || item.public_url || '')}" preload="none" data-asset-hover-preview="audio"></audio>`;
    }
    if(assetSupportsImagePreview(item)){
        const format = assetFileExtension(item).toUpperCase() || 'IMAGE';
        const width = kind === 'text' ? 384 : 256;
        return `<img src="${escapeAttr(assetPreviewUrl(item.url, width, item))}" alt="${escapeAttr(item.name || 'asset')}" data-preview-fallback="${escapeAttr(format)}" loading="lazy" decoding="async">`;
    }
    if(kind === 'text') return `<div class="asset-file-icon"><i data-lucide="file-text"></i><span>文本</span></div>`;
    return `<div class="asset-file-icon"><i data-lucide="file"></i><span>${escapeHtml(assetFileExtension(item).toUpperCase() || '文件')}</span></div>`;
}
function workflowThumb(item){
    return `<div class="asset-file-icon workflow-file-icon"><i data-lucide="workflow"></i><span>${escapeHtml(workflowKindLabel(item))}</span></div>`;
}
function localItemKind(item){
    // 共享文件夹的 item 已带 kind；兜底按名称判断
    if(item && item.kind) return item.kind;
    const name = String(item?.name || '').toLowerCase();
    if(/\.(mp4|webm|mov|m4v|mkv)(\?|#|$)/i.test(name)) return 'video';
    if(/\.(mp3|wav|flac|ogg|m4a|aac)(\?|#|$)/i.test(name)) return 'audio';
    return 'image';
}
function localObjectUrl(item){
    // 共享文件夹素材直接走后端 URL（局域网也可访问），不再用 createObjectURL
    return item?.url || '';
}
function findLocalItem(id){
    return localItemMap.get(id) || null;
}
async function loadLocalStorageSettings(){
    try {
        const settings = await apiJsonResponse(assetManagerApi.getStorageSettings());
        if(settings) gwCall('settings', 'applyStorageSettingsData', settings);
    } catch(_) {}
}
async function loadLocalAssets({append=false}={}){
    if(append && localAssetsLoading) return localAssets;
    const requestId = append ? localAssetsRequestId : ++localAssetsRequestId;
    const kind = activeLocalLibraryKind();
    if(!append){
        localAssetsCursor = '';
        localAssetsHasMore = false;
        localAssetsTotal = 0;
    }
    localAssetsError = '';
    localAssetsLoading = true;
    try {
        if(!append) await loadLocalStorageSettings();
        const data = await apiJsonResponse(assetManagerApi.getLocalAssets(kind, {
            limit:LOCAL_ASSET_PAGE_SIZE,
            ...(append && localAssetsCursor ? {cursor:localAssetsCursor} : {}),
        }));
        if(requestId !== localAssetsRequestId) return localAssets;
        const pageItems = Array.isArray(data.items) ? data.items : [];
        if(append){
            const seen = new Set(localAssets.map(item => item.id));
            localAssets = [...localAssets, ...pageItems.filter(item => !seen.has(item.id))];
        } else {
            localAssets = pageItems;
        }
        localAssetsTotal = Number(data.total ?? data.count ?? localAssets.length) || 0;
        localAssetsCursor = data.next_cursor || '';
        localAssetsHasMore = Boolean(data.has_more);
        localUploadTree = data.tree || {id:'__root__', path:'', name:'根目录', count:localAssetsTotal, items:[], children:[]};
    } catch(err) {
        if(requestId !== localAssetsRequestId) return localAssets;
        localAssetsError = err?.message || '加载本地素材失败';
        if(!append){
            localAssets = [];
            localUploadTree = {id:'__root__', path:'', name:'根目录', count:0, items:[], children:[]};
        }
    } finally {
        if(requestId === localAssetsRequestId){
            localAssetsLoading = false;
            localAssetsLoaded = true;
        }
    }
    if(requestId !== localAssetsRequestId) return localAssets;
    if(activeLocalUploadFolder && !localUploadFolderExists(activeLocalUploadFolder)) activeLocalUploadFolder = '';
    if(activeLocalUploadClassFilter && !activeLocalUploadClassEntry()) activeLocalUploadClassFilter = '';
    selectedLocalUploadIds = new Set([...selectedLocalUploadIds].filter(id => localAssets.some(item => item.id === id)));
    return localAssets;
}
async function loadMoreLocalAssets(){
    if(!localAssetsHasMore || localAssetsLoading) return localAssets;
    return loadLocalAssets({append:true});
}
function reorderLocalLibraries(sourceId, targetId){
    if(!sourceId || !targetId || sourceId === targetId) return false;
    const list = localLibraries.slice();
    const from = list.findIndex(item => item.id === sourceId);
    const to = list.findIndex(item => item.id === targetId);
    if(from < 0 || to < 0) return false;
    const [item] = list.splice(from, 1);
    list.splice(to, 0, item);
    localLibraries = list;
    storageSettingsState.localLibraries = list.slice();
    return true;
}
function canvasAssetCategories(){
    // 【QIN-563】「全部画布」虚拟分类：聚合三类画布与全部画布资产，供统计卡单击筛选与树顶入口使用。
    const all = {
        id:'all',
        name:'全部画布',
        count:(canvasAssetsData.items || []).length,
        canvas_count:(canvasAssetsData.canvases || []).length
    };
    const cats = Array.isArray(canvasAssetsData.categories) && canvasAssetsData.categories.length
        ? canvasAssetsData.categories.filter(cat => ['smart','classic','reference'].includes(cat.id))
        : [
            {id:'smart', name:'智能画布', count:(canvasAssetsData.items || []).filter(item => item.canvas_kind === 'smart').length, canvas_count:(canvasAssetsData.canvases || []).filter(item => item.kind === 'smart').length},
            {id:'classic', name:'普通画布', count:(canvasAssetsData.items || []).filter(item => (item.canvas_kind || 'classic') === 'classic').length, canvas_count:(canvasAssetsData.canvases || []).filter(item => (item.kind || 'classic') === 'classic').length},
            {id:'reference', name:'参考画布', count:(canvasAssetsData.items || []).filter(item => item.canvas_kind === 'reference').length, canvas_count:(canvasAssetsData.canvases || []).filter(item => item.kind === 'reference').length}
        ];
    return [all, ...cats];
}
function activeCanvasAssetCategoryInfo(){
    return canvasAssetCategories().find(cat => cat.id === activeCanvasAssetCategory) || canvasAssetCategories()[0] || {id:'all', name:'全部画布', count:0, canvas_count:0};
}
function defaultCanvasAssetCategory(){
    // 【QIN-563】默认落地分类跳过「全部画布」，保持普通入口仍落在具体分类。
    const cats = canvasAssetCategories().filter(cat => cat.id !== 'all');
    return cats.find(cat => Number(cat.canvas_count || 0) > 0)?.id || (cats[0] ? cats[0].id : 'smart');
}
function uniqueCanvasAssets(items){
    const seen = new Set();
    const result = [];
    (items || []).forEach(item => {
        // 【QIN-567】素材索引接线后条目自带 asset_id/id 稳定身份；未登记媒体载体的
        // 注册表素材 url 如实为空，不得因缺 url 丢条目，仅按身份去重。
        const key = `${item.canvas_id || ''}::${item.url || item.asset_id || item.id || ''}`;
        if(!item?.url && !item?.asset_id && !item?.id) return;
        if(seen.has(key)) return;
        seen.add(key);
        result.push(item);
    });
    return result;
}
function canvasAssetCountForCanvas(canvasId){
    return uniqueCanvasAssets(canvasAssetsData.items || []).filter(item => item.canvas_id === canvasId).length;
}
function canvasAssetsForCategory(categoryId=activeCanvasAssetCategory){
    // 【QIN-563】「全部画布」聚合三类画布列表；具体分类仍按画布类型过滤。
    if(categoryId === 'all'){
        return (Array.isArray(canvasAssetsData.canvases) ? canvasAssetsData.canvases.slice() : []).sort((a, b) => String(a.title || '').localeCompare(String(b.title || ''), 'zh-Hans-CN', {numeric:true, sensitivity:'base'}));
    }
    let list = Array.isArray(canvasAssetsData.canvases) ? canvasAssetsData.canvases.slice() : [];
    list = list.filter(canvas => (canvas.kind || 'classic') === categoryId);
    return list.sort((a, b) => String(a.title || '').localeCompare(String(b.title || ''), 'zh-Hans-CN', {numeric:true, sensitivity:'base'}));
}
function activeCanvasAssetCanvas(){
    if(!activeCanvasAssetCanvasId) return null;
    // 【QIN-563】索引画布元素以 canvas_id 为稳定字段（id 仅为历史兼容），两者都兼容。
    return (canvasAssetsData.canvases || []).find(canvas => (canvas.canvas_id || canvas.id) === activeCanvasAssetCanvasId) || null;
}
function canvasAssetViewTitle(){
    return activeCanvasAssetCanvas()?.title || activeCanvasAssetCategoryInfo().name || '画布资产';
}
function canvasKindLabel(kind){
    return kind === 'smart' ? '智能画布' : kind === 'reference' ? '参考画布' : '普通画布';
}
// 认证支持的平台键（与后端 AVATAR_SUPPORTED_PLATFORMS 保持一致；新增平台时同步）
const AVATAR_SUPPORTED_PLATFORMS = ['apimart', 'volcengine'];
const AVATAR_PLATFORM_LABELS = {apimart:'APIMart', volcengine:'火山引擎'};
function findAssetItem(id){
    for(const lib of assetLibraries()) for(const cat of lib.categories || []) for(const item of cat.items || []) if(item.id === id) return item;
    return null;
}
function findWorkflowItem(id){
    for(const lib of assetLibraries()) for(const cat of lib.categories || []) {
        if((cat.type || '') !== 'workflow') continue;
        for(const item of cat.items || []) if(workflowAssetId(item) === id) return item;
    }
    return null;
}
function findPromptItem(id){
    for(const lib of promptLibraries()) for(const item of lib.items || []) if(item.id === id) return item;
    return null;
}
function findCanvasAssetItem(id){
    return (canvasAssetsData.items || []).find(item => item.id === id) || null;
}
function toggleSelectionSet(set, id){
    if(!id) return false;
    if(set.has(id)){
        set.delete(id);
        return false;
    }
    set.add(id);
    return true;
}
function managedSelectionTarget(target){
    if(activeTab === 'assets' && assetManageMode){
        const check = target.closest?.('[data-asset-check]');
        const card = target.closest?.('[data-asset-card]');
        const id = check?.dataset.assetCheck || card?.dataset.assetCard || '';
        if(id) return {kind:'asset', id};
    }
    if(activeTab === 'local' && localUploadManageMode){
        const check = target.closest?.('[data-localup-check]');
        const card = target.closest?.('[data-localup-card]');
        const id = check?.dataset.localupCheck || card?.dataset.localupCard || '';
        if(id) return {kind:'localup', id};
    }
    if(activeTab === 'local' && localManageMode){
        const check = target.closest?.('[data-local-check]');
        const card = target.closest?.('[data-local-card]');
        const id = check?.dataset.localCheck || card?.dataset.localCard || '';
        if(id) return {kind:'local', id};
    }
    if(activeTab === 'workflows' && workflowManageMode){
        const check = target.closest?.('[data-workflow-check]');
        const card = target.closest?.('[data-workflow-card]');
        const id = check?.dataset.workflowCheck || card?.dataset.workflowCard || '';
        if(id) return {kind:'workflow', id};
    }
    if(activeTab === 'prompts' && promptManageMode){
        const check = target.closest?.('[data-prompt-check]');
        const row = target.closest?.('[data-prompt-row]');
        const id = check?.dataset.promptCheck || row?.dataset.promptRow || '';
        if(id) return {kind:'prompt', id};
    }
    if(activeTab === 'canvas-assets' && canvasAssetManageMode){
        const check = target.closest?.('[data-canvas-asset-check]');
        const card = target.closest?.('[data-canvas-asset-card]');
        const id = check?.dataset.canvasAssetCheck || card?.dataset.canvasAssetCard || '';
        if(id) return {kind:'canvasAsset', id};
    }
    return null;
}
function guardMatchesManagedSelection(target){
    const current = managedSelectionTarget(target);
    return !!current
        && !!managedSelectionPointerGuard
        && current.kind === managedSelectionPointerGuard.kind
        && current.id === managedSelectionPointerGuard.id
        && Date.now() - managedSelectionPointerGuard.at < 600;
}

function normalizeRegistryAsset(item={}){
    const metadata = item.metadata && typeof item.metadata === 'object' ? item.metadata : {};
    const classification = metadata.classification && typeof metadata.classification === 'object'
        ? metadata.classification
        : (metadata.classification === null ? {} : (metadata.classification || {}));
    const assetId = String(item.id || item.asset_id || '').trim();
    // 上传素材（QIN-550）：无 storage_root_id 但登记了 stored_name 的条目同样可经
    // /media 端点回读真实文件，构造回读地址，不再留空 src。
    const localMediaUrl = !item.is_remote && assetId && (item.storage_root_id || item.stored_name)
        ? `/api/asset-registry/assets/${encodeURIComponent(assetId)}/media`
        : '';
    const size = Number(item.size ?? item.size_bytes ?? 0);
    return {
        ...item,
        url:item.latest_image_version_url || item.public_url || item.url || localMediaUrl,
        size:Number.isFinite(size) ? size : 0,
        classification:metadata.classification || metadata.classification === null
            ? classification
            : (metadata.classification || metadata?.classification || {}),
        created_at:Number(item.created_at || 0),
    };
}
function registryStructureId(value={}){
    return String(value?.id || value?.structure_id || '').trim();
}
function normalizeRegistryAssetStructure(value={}){
    const id = registryStructureId(value);
    // API returns `kind`; keep `structure_type` as a read-only compatibility
    // alias for older persisted responses during rolling upgrades.
    const type = String(value?.structure_type || value?.kind || '').trim().toLowerCase();
    const memberIds = new Set();
    const members = (Array.isArray(value?.members) ? value.members : [])
        .map((member, index) => {
            const assetId = String(member?.asset_id || member?.asset?.id || '').trim();
            if(!assetId || memberIds.has(assetId)) return null;
            memberIds.add(assetId);
            const asset = member?.asset && typeof member.asset === 'object' ? member.asset : {};
            return {
                asset_id:assetId,
                sort_order:Number.isFinite(Number(member?.sort_order)) ? Number(member.sort_order) : index,
                asset:normalizeRegistryAsset({...asset, id:assetId, asset_id:assetId}),
            };
        })
        .filter(Boolean)
        .sort((left,right) => left.sort_order - right.sort_order || left.asset_id.localeCompare(right.asset_id));
    const requestedCurrentId = String(value?.current_asset_id || '').trim();
    const currentAssetId = members.some(member => member.asset_id === requestedCurrentId)
        ? requestedCurrentId
        : (members[0]?.asset_id || '');
    return {
        id,
        structure_type:type,
        current_asset_id:currentAssetId,
        version:Number.isFinite(Number(value?.version)) ? Number(value.version) : 0,
        created_at:value?.created_at || '',
        updated_at:value?.updated_at || '',
        members,
    };
}
function registryStructurePresentationKey(structures){
    return [...structures.values()]
        .map(structure => `${structure.id}:${structure.structure_type}:${structure.version}:${structure.current_asset_id}:${structure.members.map(member => `${member.asset_id}@${member.sort_order}:${member.asset?.status || ''}`).join(',')}`)
        .sort()
        .join('|');
}
function registryStructureMemberAsset(structure, assetId){
    const id = String(assetId || '').trim();
    const member = (structure?.members || []).find(value => value.asset_id === id);
    if(!member) return null;
    const loaded = registryAssets.find(item => String(item.id || '') === id) || {};
    // `member.asset` comes from asset-structures and carries the server-side
    // availability state. Keep it authoritative over a stale or paged-out grid item.
    return normalizeRegistryAsset({...loaded, ...member.asset, id, asset_id:id});
}
// 【QIN-534 修复】素材记录里的原始 kind 字段来自索引来源（本地索引素材常是 unknown / 来源枚举），
// 不能直接拿来判断「是否图片」——否则 PNG/JPG 素材会被判成非图片，导致版本结构与组结构既不能创建也不能渲染。
// 改用与素材列表、筛选、类型标签完全一致的分类解析（扩展名优先），与后端 _asset_category 同口径。
function isRegistryStructureImage(item={}){
    return registryVisualType(item) === 'image';
}
function isRegistryStructureActiveImage(item={}){
    if(!isRegistryStructureImage(item)) return false;
    const status = String(item?.status || '').trim().toLowerCase();
    if(status) return status === 'active';
    return !item?.archived && !item?.hidden;
}
function registryStructureIsRenderable(structure){
    return Boolean(structure?.id)
        && ['version','group'].includes(structure.structure_type)
        && structure.members.length >= 2
        && structure.members.every(member => isRegistryStructureActiveImage(registryStructureMemberAsset(structure, member.asset_id)));
}
function registryVersionStructuresForItems(items=[]){
    const visibleIds = new Set(items.map(item => String(item?.id || '')).filter(Boolean));
    return [...registryAssetStructures.values()]
        .filter(structure => structure.structure_type === 'version')
        .filter(registryStructureIsRenderable)
        .filter(structure => structure.members.some(member => visibleIds.has(member.asset_id)))
        .sort((left,right) => String(right.updated_at || '').localeCompare(String(left.updated_at || '')) || left.id.localeCompare(right.id));
}
function registryGroupStructuresForItems(items=[]){
    const visibleIds = new Set(items.map(item => String(item?.id || '')).filter(Boolean));
    return [...registryAssetStructures.values()]
        .filter(structure => structure.structure_type === 'group')
        .filter(registryStructureIsRenderable)
        .filter(structure => structure.members.some(member => visibleIds.has(member.asset_id)))
        .sort((left,right) => String(right.updated_at || '').localeCompare(String(left.updated_at || '')) || left.id.localeCompare(right.id));
}
function registryVersionStructureProjection(structure){
    const current = registryStructureMemberAsset(structure, structure.current_asset_id) || registryStructureMemberAsset(structure, structure.members[0]?.asset_id);
    if(!current) return null;
    return {
        ...current,
        id:`structure:${structure.id}`,
        asset_id:current.id,
        structure_current_asset_id:current.id,
        __registry_asset_structure:structure,
    };
}
function registryGroupStructureProjection(structure){
    const cover = registryStructureMemberAsset(structure, structure.current_asset_id) || registryStructureMemberAsset(structure, structure.members[0]?.asset_id);
    if(!cover) return null;
    return {
        ...cover,
        id:`structure:${structure.id}`,
        asset_id:cover.id,
        structure_current_asset_id:cover.id,
        structure_group_cover_asset_id:cover.id,
        __registry_asset_structure:structure,
    };
}
function registryProjectedAssetItems(items=[]){
    const structures = [...registryVersionStructuresForItems(items), ...registryGroupStructuresForItems(items)];
    if(!structures.length) return items;
    const structuresByMemberId = new Map();
    structures.forEach(structure => structure.members.forEach(member => {
        if(!structuresByMemberId.has(member.asset_id)) structuresByMemberId.set(member.asset_id, []);
        structuresByMemberId.get(member.asset_id).push(structure);
    }));
    const emitted = new Set();
    const consumed = new Set();
    const result = [];
    items.forEach(item => {
        const assetId = String(item?.id || '');
        if(!assetId || consumed.has(assetId)) return;
        const structure = (structuresByMemberId.get(assetId) || []).find(value => !emitted.has(value.id));
        if(!structure){
            result.push(item);
            return;
        }
        const projection = structure.structure_type === 'group'
            ? registryGroupStructureProjection(structure)
            : registryVersionStructureProjection(structure);
        if(projection) result.push(projection);
        emitted.add(structure.id);
        structure.members.forEach(member => consumed.add(member.asset_id));
    });
    return result;
}
async function refreshRegistryAssetStructures({
    requestId=registryRequestSeq,
    assetIds=registryAssets.filter(isRegistryStructureImage).map(item => item.id),
    replace=true,
}={}){
    const imageAssetIds = new Set(
        registryAssets
            .filter(isRegistryStructureImage)
            .map(item => String(item?.id || '').trim())
            .filter(Boolean),
    );
    const ids = [...new Set((assetIds || [])
        .map(value => String(value || '').trim())
        .filter(value => value && imageAssetIds.has(value)))];
    const sequence = ++registryAssetStructureRequestSeq;
    if(!ids.length){
        const changed = replace && registryAssetStructures.size > 0;
        if(replace){
            registryAssetStructures.clear();
        }
        return changed;
    }
    if(typeof assetManagerApi.listAssetStructures !== 'function') return false;
    const before = registryStructurePresentationKey(registryAssetStructures);
    try {
        const pages = [];
        for(let index=0; index<ids.length; index+=REGISTRY_STRUCTURE_QUERY_CHUNK_SIZE){
            const page = await apiJsonResponse(assetManagerApi.listAssetStructures(
                ids.slice(index, index + REGISTRY_STRUCTURE_QUERY_CHUNK_SIZE)
            ));
            if(sequence !== registryAssetStructureRequestSeq || requestId !== registryRequestSeq) return false;
            pages.push(page);
        }
        const latestRevision = Number(pages[pages.length - 1]?.revision);
        if(Number.isFinite(latestRevision) && latestRevision >= 1) registryAssetStructuresRevision = latestRevision;
        const requestedIds = new Set(ids);
        const next = replace ? new Map() : new Map(registryAssetStructures);
        if(!replace){
            // The list endpoint is authoritative for every requested member.
            // Drop any previous projection it no longer returns before merging
            // the current response, while keeping unrelated loaded pages intact.
            [...next.entries()].forEach(([structureId, structure]) => {
                if((structure?.members || []).some(member => requestedIds.has(member.asset_id))){
                    next.delete(structureId);
                }
            });
        }
        pages.flatMap(page => Array.isArray(page?.items) ? page.items : []).forEach(value => {
            const structure = normalizeRegistryAssetStructure(value);
            if(registryStructureIsRenderable(structure)) next.set(structure.id, structure);
        });
        registryAssetStructures = next;
        return before !== registryStructurePresentationKey(next);
    } catch(err) {
        if(sequence === registryAssetStructureRequestSeq && requestId === registryRequestSeq) setStatus(err.message || '合并结构读取失败');
        return false;
    }
}
function resetRegistryAssetStructures(){
    registryAssetStructureRequestSeq += 1;
    registryAssetStructures.clear();
    closeRegistryStructureMenu();
}
function closeRegistryStructureMenu(){
    registryStructureMenu = null;
    document.querySelector('[data-registry-structure-menu]')?.remove();
}
// 【QIN-534 反馈 4】素材的关联与结构状态判定。
// 关联关系是单向登记（来源 → 目标），后端在素材响应里回填 related_assets；
// 结构状态来自 asset-structures 集合投影，成员覆盖全部选中项时才可解除。
function registryRelatedAssetIds(item={}){
    return (Array.isArray(item?.related_assets) ? item.related_assets : [])
        .map(asset => String(asset?.id || asset?.asset_id || '').trim())
        .filter(Boolean);
}
function registrySelectedAssetMap(assetIds=[]){
    const wanted = new Set(assetIds.map(value => String(value || '').trim()).filter(Boolean));
    const found = new Map();
    registryAssets.forEach(item => {
        const id = String(item?.id || '');
        if(wanted.has(id)) found.set(id, item);
    });
    return found;
}
// 选中集合内部是否已经存在关联：任一方向的已登记关系成立即视为已关联。
function registrySelectionRelationState(assetIds=[]){
    const ids = [...new Set(assetIds.map(value => String(value || '').trim()).filter(Boolean))];
    if(ids.length < 2) return false;
    const selected = new Set(ids);
    const items = registrySelectedAssetMap(ids);
    return ids.some(id => registryRelatedAssetIds(items.get(id) || {}).some(targetId => selected.has(targetId)));
}
// 找到选中的素材共同所属的同类型结构：全选或部分选中该结构成员时均可移出/解除。
function registryOwningStructures(assetIds=[]){
    const ids = [...new Set((assetIds || []).map(value => String(value || '').trim()).filter(Boolean))];
    if(ids.length < 2) return {version:null, group:null};
    const pick = (type) => [...registryAssetStructures.values()]
        .filter(structure => structure.structure_type === type && Array.isArray(structure.members) && structure.members.length >= 2)
        .find(structure => {
            const memberIds = new Set(structure.members.map(member => String(member.asset_id || '').trim()));
            return ids.every(id => memberIds.has(id));
        }) || null;
    return {version:pick('version'), group:pick('group')};
}
// 【QIN-534 反馈 4】解除结构只把选中素材移出结构，其余成员保留；
// 剩余成员不足两个时结构失去意义，整体删除。CAS 使用结构自身版本号。
async function releaseRegistryStructureMembers(structure, assetIds=[]){
    if(!structure || !['version','group'].includes(structure.structure_type)) throw new Error('待解除的结构不存在');
    const removing = new Set(assetIds.map(value => String(value || '').trim()).filter(Boolean));
    const remaining = structure.members.map(member => member.asset_id).filter(id => !removing.has(id));
    const isGroup = structure.structure_type === 'group';
    registryStructureMutationId = structure.id;
    try {
        if(remaining.length >= 2){
            const payload = {asset_ids:remaining, expected_version:structure.version};
            // 当前代表素材被移出时必须改指剩余成员，否则服务端按非法成员拒绝。
            if(!remaining.includes(structure.current_asset_id)) payload.current_asset_id = remaining[0];
            const patchRequest = typeof assetManagerApi?.updateAssetStructure === 'function'
                ? assetManagerApi.updateAssetStructure(structure.id, payload)
                : assetManagerHttp.request(
                    `/api/asset-registry/asset-structures/${encodeURIComponent(structure.id)}`,
                    {method:'PATCH', credentials:'same-origin', headers:{'Content-Type':'application/json'}, body:JSON.stringify(payload)}
                );
            const data = await apiJsonResponse(patchRequest);
            const updated = normalizeRegistryAssetStructure(data?.structure || {});
            if(updated.id) registryAssetStructures.set(updated.id, updated);
            setStatus(`已移出 ${removing.size} 项素材，${isGroup ? '组结构' : '版本结构'}保留 ${remaining.length} 项`);
        } else {
            const deleted = await apiJsonResponse(assetManagerApi.deleteAssetStructure(structure.id, structure.version));
            const latestRevision = Number(deleted?.revision);
            if(Number.isFinite(latestRevision) && latestRevision >= 1) registryAssetStructuresRevision = latestRevision;
            registryAssetStructures.delete(structure.id);
            setStatus(`已解除${isGroup ? '组结构' : '版本结构'}`);
        }
    } catch(err) {
        // CAS 冲突时先回读结构真值，让下一次提交以服务端最新版本进行。
        await reloadRegistryAssetStructure(structure.id).catch(() => null);
        throw err;
    } finally {
        registryStructureMutationId = '';
    }
    closeRegistryStructureMenu();
    render();
}
// 【QIN-534 反馈 4】解除关联：关系是单向登记（来源 → 目标），
// 因此按选中集合内的有序对逐条解除；本就没有关联的对返回 404，直接跳过。
async function releaseRegistryRelations(assetIds=[]){
    const ids = [...new Set(assetIds.map(value => String(value || '').trim()).filter(Boolean))];
    if(ids.length < 2) throw new Error('至少选择两个素材才能解除关联');
    const removedPairs = [];
    for(const from of ids){
        for(const to of ids){
            if(from === to) continue;
            try {
                await apiJsonResponse(assetManagerApi.removeRegistryAssetRelation(from, to));
                removedPairs.push(`${from}->${to}`);
            } catch(err) {
                // 只有「关系不存在」才是可跳过的正常情况；
                // 素材已不存在等其它 404 必须上抛，不能被当成「本来就没关联」吞掉。
                if(err?.code === 'RELATION_NOT_FOUND') continue;
                throw err;
            }
        }
    }
    // 本地同步已解除的对端，菜单状态与详情面板立即反映真实结果。
    if(removedPairs.length){
        const removed = new Set(removedPairs);
        registryAssets.forEach(item => {
            if(!Array.isArray(item.related_assets)) return;
            const id = String(item.id || '');
            item.related_assets = item.related_assets.filter(asset => {
                const targetId = String(asset?.id || asset?.asset_id || '');
                return !removed.has(`${id}->${targetId}`) && !removed.has(`${targetId}->${id}`);
            });
        });
    }
    setStatus(removedPairs.length ? `已解除 ${removedPairs.length} 条素材关联` : '选中素材之间没有可解除的关联');
    if(detailViewerState.open && detailViewerState.source === 'registry') gwCall('preview', 'renderDetailViewer');
    else render();
}
// 【QIN-534】多选右键菜单不再限定 kind==='image'：版本/组结构按「是否全为图片」决定可用性，
// 关联结构对任意多模态素材可用，避免右键菜单被旧限制静默拦掉。
// 【QIN-534 反馈 4】三项动作按素材当前状态切换「合并 / 解除」语义：
// 已存在版本结构、组结构或关联时给出解除入口，避免重复合并产生第二个结构。
function renderRegistryStructureContextMenu(assetIds=[], options={}){
    const count = assetIds.length;
    const imageOnly = options.imageOnly !== false;
    const owning = options.owning || {version:null, group:null};
    const related = Boolean(options.related);
    const versionStructure = owning.version || null;
    const groupStructure = owning.group || null;
    const versionDisabled = imageOnly ? '' : ' disabled';
    const versionLabel = versionStructure ? '解除版本结构' : '合并为版本结构';
    const versionHint = versionStructure ? '移出该版本结构，保留其余成员' : (imageOnly ? '切换最终版本' : '仅支持图片素材');
    const groupLabel = groupStructure ? '解除组结构' : '合并为组结构';
    const groupHint = groupStructure ? '移出该组结构，保留其余成员' : (imageOnly ? '叠放缩略图，双击进入组 Coverflow' : '仅支持图片素材');
    const relationLabel = related ? '解除关联结构' : '合并为关联结构';
    const relationHint = related ? '断开选中素材之间的关联' : '建立素材互相关联';
    // 解除态统一用 unlink 图标，与底部批量栏的解除按钮保持同一视觉语言。
    const versionIcon = versionStructure ? 'unlink' : 'git-branch';
    const groupIcon = groupStructure ? 'unlink' : 'layers-2';
    const relationIcon = related ? 'unlink' : 'link-2';
    return `<div class="registry-structure-context-menu" data-registry-structure-menu role="menu" aria-label="合并已选素材">
        <div class="registry-structure-context-title"><i data-lucide="layers-3"></i><span><b>${count}</b> 项素材</span></div>
        <button type="button" role="menuitem" data-registry-structure-${versionStructure ? 'release' : 'create'}="version"${versionStructure ? '' : versionDisabled}><i data-lucide="${versionIcon}"></i><span>${versionLabel}</span><small>${versionHint}</small></button>
        <button type="button" role="menuitem" data-registry-structure-${related ? 'release' : 'create'}="relation"><i data-lucide="${relationIcon}"></i><span>${relationLabel}</span><small>${relationHint}</small></button>
        <button type="button" role="menuitem" data-registry-structure-${groupStructure ? 'release' : 'create'}="group"${groupStructure ? '' : versionDisabled}><i data-lucide="${groupIcon}"></i><span>${groupLabel}</span><small>${groupHint}</small></button>
    </div>`;
}
// 【QIN-534】触发修复：多选后右键点击「任意已选卡片」或「网格空白区」都要弹出同一套黑金菜单；
// 旧的 kind==='image' 限制与空白区无响应一并解除，preventDefault 前置避免原生右键菜单抢先弹出。
function openRegistryStructureContextMenu(event){
    if(activeTab !== 'registry' && assetBoardById(activeTab)?.system !== false) return;
    const board = assetBoardById(activeTab);
    const selected = registrySelectedAssets(board);
    if(selected.length < 2) return;
    const card = event.target?.closest?.('[data-registry-card]');
    if(card){
        if(!registrySelectedIds.has(String(card.dataset.registryCard || ''))) return;
    } else if(!event.target?.closest?.('.registry-grid, .registry-assets-scroll')){
        return;
    }
    event.preventDefault();
    event.stopPropagation();
    closeRegistryStructureMenu();
    const menuHost = document.createElement('div');
    const selectedIds = selected.map(item => item.id);
    const imageOnly = selected.every(isRegistryStructureImage);
    // 【QIN-534 反馈 4】按素材当前状态决定给出「合并」还是「解除」入口。
    const owning = registryOwningStructures(selectedIds);
    const related = registrySelectionRelationState(selectedIds);
    menuHost.innerHTML = renderRegistryStructureContextMenu(selectedIds, {imageOnly, owning, related});
    const menu = menuHost.firstElementChild;
    if(!menu) return;
    document.body.appendChild(menu);
    const margin = 10;
    const bounds = menu.getBoundingClientRect();
    menu.style.left = `${Math.max(margin, Math.min(event.clientX, window.innerWidth - bounds.width - margin))}px`;
    menu.style.top = `${Math.max(margin, Math.min(event.clientY, window.innerHeight - bounds.height - margin))}px`;
    registryStructureMenu = {assetIds:selected.map(item => item.id), trigger:card};
    window.FloatingDismissal?.mark?.(menu, {kind:'popover', content:'self', trigger:card, close:closeRegistryStructureMenu});
    refreshIcons();
    requestAnimationFrame(() => menu.querySelector('button:not(:disabled)')?.focus({preventScroll:true}));
}
async function createRegistryVersionStructure(assetIds=[]){
    const ids = [...new Set((assetIds || []).map(value => String(value || '').trim()).filter(Boolean))];
    const available = new Map(registryAssets.map(item => [String(item.id || ''), item]));
    if(ids.length < 2 || !ids.every(id => isRegistryStructureImage(available.get(id)))){
        throw new Error('仅支持至少两张已选图片合并为版本结构');
    }
    if(typeof assetManagerApi.createAssetStructure !== 'function') throw new Error('版本结构接口尚未加载');
    registryStructureMutationId = 'create-version';
    try {
        const data = await apiJsonResponse(assetManagerApi.createAssetStructure({
            kind:'version',
            asset_ids:ids,
            current_asset_id:ids[0],
            expected_version:registryAssetStructuresRevision,
        }));
        registryAssetStructuresRevision += 1;
        const structure = normalizeRegistryAssetStructure(data?.structure || {});
        if(!registryStructureIsRenderable(structure)) throw new Error('服务端未返回可用的版本结构');
        registryAssetStructures.set(structure.id, structure);
        registrySelectedIds.clear();
        registrySelectionAnchorId = '';
        registryManageMode = false;
        registryBulkPanel = '';
        selectedRegistryAssetId = structure.current_asset_id;
        registryDetail = registryAssets.find(item => item.id === selectedRegistryAssetId) || null;
        closeRegistryStructureMenu();
        registryStructureMutationId = '';
        render();
        setStatus(`已合并 ${structure.members.length} 张图片为版本结构`);
    } finally {
        registryStructureMutationId = '';
    }
}
async function createRegistryGroupStructure(assetIds=[]){
    const ids = [...new Set((assetIds || []).map(value => String(value || '').trim()).filter(Boolean))];
    const available = new Map(registryAssets.map(item => [String(item.id || ''), item]));
    if(ids.length < 2 || !ids.every(id => isRegistryStructureImage(available.get(id)))){
        throw new Error('仅支持至少两张已选图片合并为组结构');
    }
    if(typeof assetManagerApi.createAssetStructure !== 'function') throw new Error('组结构接口尚未加载');
    registryStructureMutationId = 'create-group';
    try {
        const data = await apiJsonResponse(assetManagerApi.createAssetStructure({
            kind:'group',
            asset_ids:ids,
            current_asset_id:ids[0],
            expected_version:registryAssetStructuresRevision,
        }));
        registryAssetStructuresRevision += 1;
        const structure = normalizeRegistryAssetStructure(data?.structure || {});
        if(!registryStructureIsRenderable(structure) || structure.structure_type !== 'group') throw new Error('服务端未返回可用的组结构');
        registryAssetStructures.set(structure.id, structure);
        registrySelectedIds.clear();
        registrySelectionAnchorId = '';
        registryManageMode = false;
        registryBulkPanel = '';
        selectedRegistryAssetId = structure.current_asset_id;
        registryDetail = registryAssets.find(item => item.id === selectedRegistryAssetId) || registryStructureMemberAsset(structure, selectedRegistryAssetId) || null;
        closeRegistryStructureMenu();
        registryStructureMutationId = '';
        render();
        setStatus(`已合并 ${structure.members.length} 张图片为组结构`);
    } finally {
        registryStructureMutationId = '';
    }
}
// 【QIN-534】结构动作统一入口：右键菜单传菜单捕获的 id，底部批量栏传当前选择集，
// 两个入口共用同一套创建/关联实现，避免两处行为漂移。
async function runRegistryStructureAction(type='', assetIds=[]){
    const ids = [...new Set((assetIds||[]).map(value => String(value||'').trim()).filter(Boolean))];
    if(type === 'version') return createRegistryVersionStructure(ids);
    if(type === 'group') return createRegistryGroupStructure(ids);
    if(type === 'relation') return relateSelectedRegistryAssets(ids);
    // 【QIN-534 反馈 4】解除语义：只把选中素材移出结构，其余成员保留。
    if(type === 'release-version' || type === 'release-group'){
        const owning = registryOwningStructures(ids);
        const structure = type === 'release-version' ? owning.version : owning.group;
        if(!structure) throw new Error('选中素材不属于同一个结构，无法解除');
        return releaseRegistryStructureMembers(structure, ids);
    }
    if(type === 'release-relation') return releaseRegistryRelations(ids);
    return undefined;
}
function registryCoverflowRawItems(board=assetBoardById(activeTab)){
    return board?.system === false ? customAssetLibraryItems(board) : registryAssets;
}
function registryCoverflowAsset(item={}){
    const id = String(item?.id || item?.asset_id || '').trim();
    if(!id) return null;
    const mediaKind = registryCoverflowSourceMediaKind('registry', item);
    if(!mediaKind) return null;
    const fallback = `/api/asset-registry/assets/${encodeURIComponent(id)}/media`;
    // Coverflow's center media is always the canonical source asset. Image
    // edit versions remain available to the editor, but must never replace
    // the original in this browsing surface.
    const explicitOriginal = item.original_url || item.originalUrl || item.media_url || item.mediaUrl || item.mediaSrc || item.media_src;
    const url = String(explicitOriginal || (item.is_remote ? item.public_url || item.url : item.public_url) || fallback).trim();
    if(!url) return null;
    const preview = assetPreviewUrl(url, 512, item) || url;
    return {
        ...item,
        id,
        kind:mediaKind,
        mediaKind,
        url,
        public_url:url,
        mediaSrc:url,
        backdropSrc:preview,
        // The bounded derivative is only for the carousel's backdrop and
        // compact mode. Fullscreen display consumes mediaSrc.
        src:preview,
        previewUrl:preview,
        preview_url:preview,
        thumbnail_url:preview,
    };
}
function registryCoverflowSourceMediaKind(source, item={}){
    if(workflowSourceId(item)) return '';
    const declaredKind = String(item?.kind || item?.type || item?.media_type || item?.mediaType || '').trim().toLowerCase();
    // A server-declared kind is authoritative. Only records without kind may
    // fall back to extension-based detailViewerType inference.
    if(['image','photo','picture'].includes(declaredKind)) return 'image';
    if(['video','movie','film'].includes(declaredKind)) return 'video';
    const inferred = detailViewerType(source || 'registry', item);
    return ['image','video'].includes(inferred) ? inferred : '';
}
function registryCoverflowSourceIsVisual(source, item={}){
    return Boolean(registryCoverflowSourceMediaKind(source, item));
}
function registryCoverflowImageCandidate(item={}){
    return registryCoverflowSourceIsVisual('registry', item);
}
function registryCoverflowAssetsForItems(items=[]){
    const seen = new Set();
    // Filter the source record before normalization so audio/text records with
    // a generic /media URL cannot be promoted into the visual Coverflow.
    return (Array.isArray(items) ? items : []).filter(registryCoverflowImageCandidate)
        .map(registryCoverflowAsset).filter(item => {
        if(!item || seen.has(item.id)) return false;
        seen.add(item.id);
        return true;
    });
}
function registryCoverflowAssets(board=assetBoardById(activeTab), extraItems=[]){
    return registryCoverflowAssetsForItems([
        ...registryCoverflowRawItems(board),
        ...(Array.isArray(extraItems) ? extraItems : []),
    ]);
}
let registryFullscreenViewerState = null;
function detailMediaCanvasSelectedCoverflowAssets(){
    const source = detailViewerState.source || 'registry';
    const selectedIds = new Set(detailMediaCanvasExpandedObjectIds(detailMediaCanvasSelectionOrder()));
    const seen = new Set();
    return detailMediaCanvasItems().filter(item => selectedIds.has(String(item.id || ''))).map(item => {
        const id = String(detailMediaCanvasPersistId(item,source) || item.id || '').trim();
        const mediaKind = registryCoverflowSourceMediaKind(source,item);
        const rawUrl = String(detailViewerUrl(source,item) || item.public_url || item.url || item.mediaSrc || '').trim();
        if(!id || !mediaKind || !rawUrl || seen.has(id)) return null;
        seen.add(id);
        const preview = assetPreviewUrl(rawUrl,512,item) || rawUrl;
        return {...item,id,asset_id:id,kind:mediaKind,mediaKind,url:rawUrl,public_url:rawUrl,mediaSrc:rawUrl,backdropSrc:preview,src:preview,previewUrl:preview,preview_url:preview,thumbnail_url:preview};
    }).filter(Boolean);
}
window.addEventListener('gw:asset-coverflow-change', event => {
    gwCall('media-canvas', 'handleCoverflowChange', event);
});
window.addEventListener('gw:asset-coverflow-add-to-reference-canvas', event => {
    gwCall('media-canvas', 'handleCoverflowAddToReferenceCanvas', event);
});
window.addEventListener('gw:asset-coverflow-open', event => {
    if(String(event.detail?.mode || '') === 'fullscreen'){
        try { window.parent?.GWAssetTreeHost?.onCoverflowOpen?.(event.detail); } catch(_) {}
    }
});
window.addEventListener('gw:asset-coverflow-request-edit', event => {
    gwCall('coverflow-bridge', 'openRegistryCoverflowAssetInEditor', event.detail || {}).catch(err => setStatus(err.message || '无法打开 Coverflow 图片编辑模式'));
});
window.addEventListener('gw:asset-coverflow-status', event => {
    const message = String(event.detail?.message || '').trim();
    if(message) setStatus(message);
});
window.addEventListener('gw:asset-coverflow-closed', event => {
    if(event.detail?.mode === 'editor'){ gwCall('coverflow-bridge', 'setDetailMediaCanvasCoverflowVisible', false); }
    if(String(event.detail?.mode || '') === 'fullscreen'){
        registryCoverflowBoardId = '';
        try { window.parent?.GWAssetTreeHost?.onCoverflowClose?.(event.detail); } catch(_) {}
    }
});
function registryGroupStructureMembers(structure){
    return (structure?.members || [])
        .map(member => ({member, asset:registryStructureMemberAsset(structure, member.asset_id)}))
        .filter(value => value.asset && isRegistryStructureActiveImage(value.asset));
}
function registryStructureCoverflowAssets(structure){
    return registryGroupStructureMembers(structure)
        .map(value => registryCoverflowAsset(value.asset))
        .filter(Boolean);
}
function openRegistryStructureCoverflow(structureId, trigger=null){
    const structure = registryAssetStructures.get(String(structureId || '').trim());
    if(!structure || !['version','group'].includes(structure.structure_type)) return;
    const scopeAssets = registryStructureCoverflowAssets(structure);
    if(!scopeAssets.length){
        setStatus('当前结构没有可展示的图片');
        return;
    }
    const currentAssetId = scopeAssets.some(item => item.id === structure.current_asset_id)
        ? structure.current_asset_id
        : scopeAssets[0].id;
    gwCall('coverflow-bridge', 'openRegistryCoverflow', trigger, {
        scopeAssets,
        currentAssetId,
        structureId:structure.id,
        scope:structure.structure_type,
    });
}
function openRegistryStructureReferenceCanvas(structureId, trigger=null){
    const structure = registryAssetStructures.get(String(structureId || '').trim());
    if(!structure || structure.structure_type !== 'version') return;
    const members = registryGroupStructureMembers(structure);
    if(members.length < 2){
        setStatus('当前版本结构没有可用于参考画布的图片');
        return;
    }
    const assets = members.map(value => value.asset);
    const currentAssetId = assets.some(asset => asset.id === structure.current_asset_id)
        ? structure.current_asset_id
        : assets[0].id;
    window.dispatchEvent(new CustomEvent('gw:asset-structure-reference-canvas-open', {detail:{
        source:'registry',
        structureId:structure.id,
        assetIds:assets.map(asset => asset.id),
        assets,
        currentAssetId,
        trigger,
    }}));
}
async function reloadRegistryAssetStructure(structureId){
    if(typeof assetManagerApi.getAssetStructure !== 'function') return null;
    const data = await apiJsonResponse(assetManagerApi.getAssetStructure(structureId));
    const structure = normalizeRegistryAssetStructure(data?.structure || {});
    if(registryStructureIsRenderable(structure)) registryAssetStructures.set(structure.id, structure);
    else registryAssetStructures.delete(structureId);
    return structure;
}
async function setRegistryVersionStructureCurrent(structureId, assetId){
    const structure = registryAssetStructures.get(String(structureId || ''));
    const nextAssetId = String(assetId || '').trim();
    if(!structure || structure.structure_type !== 'version') return;
    if(!structure.members.some(member => member.asset_id === nextAssetId)) throw new Error('该图片不属于当前版本结构');
    if(typeof assetManagerApi.setAssetStructureCurrent !== 'function') throw new Error('版本结构接口尚未加载');
    registryStructureMutationId = structure.id;
    render();
    try {
        const data = await apiJsonResponse(assetManagerApi.setAssetStructureCurrent(structure.id, {
            current_asset_id:nextAssetId,
            expected_version:structure.version,
        }));
        const updated = normalizeRegistryAssetStructure(data?.structure || {});
        if(!registryStructureIsRenderable(updated)) throw new Error('服务端未返回可用的版本结构');
        registryAssetStructures.set(updated.id, updated);
        selectedRegistryAssetId = updated.current_asset_id;
        registryDetail = registryAssets.find(item => item.id === updated.current_asset_id)
            || registryStructureMemberAsset(updated, updated.current_asset_id)
            || null;
        registryStructureMutationId = '';
        render();
        setStatus(`当前最终版本已切换为 ${registryDetail?.name || '所选图片'}`);
        return updated;
    } catch(err) {
        // CAS 冲突或权限变化时，重新读取服务端真值，避免以旧 version 覆盖当前结果。
        const reloaded = await reloadRegistryAssetStructure(structure.id).catch(() => null);
        registryStructureMutationId = '';
        render();
        // The version rail inside an already-open scoped reference canvas is
        // not part of the registry render tree. Rebuild it only after a
        // successful server read so the rail reflects the authoritative CAS
        // result without closing the canvas or losing its placed nodes.
        const refreshReferenceCanvas = reloaded !== null
            && detailViewerState.open
            && detailViewerState.referenceCanvasMode
            && detailViewerState.referenceCanvasStructureId === structure.id;
        if(refreshReferenceCanvas) gwCall('preview', 'renderDetailViewer');
        throw err;
    } finally {
        registryStructureMutationId = '';
    }
}
function closeRegistryVersionStructureRemoveDialog(force=false){
    if(registryStructureRemoveState.busy && !force) return;
    const trigger = registryStructureRemoveState.trigger;
    registryStructureRemoveState = {open:false, structureId:'', busy:false, error:'', trigger:null};
    document.getElementById('registryStructureRemoveOverlay')?.remove();
    if(!force && trigger?.isConnected && typeof trigger.focus === 'function'){
        requestAnimationFrame(() => trigger.focus({preventScroll:true}));
    }
}
function renderRegistryVersionStructureRemoveDialog(){
    let overlay = document.getElementById('registryStructureRemoveOverlay');
    if(!registryStructureRemoveState.open){ overlay?.remove(); return; }
    const structure = registryAssetStructures.get(registryStructureRemoveState.structureId);
    if(!structure || !['version','group'].includes(structure.structure_type)){
        closeRegistryVersionStructureRemoveDialog(true);
        return;
    }
    const current = registryStructureMemberAsset(structure, structure.current_asset_id)
        || registryGroupStructureMembers(structure)[0]?.asset;
    if(!overlay){
        overlay = document.createElement('div');
        overlay.id = 'registryStructureRemoveOverlay';
        overlay.className = 'storage-settings-overlay registry-structure-remove-overlay';
        document.body.appendChild(overlay);
    }
    const busy = registryStructureRemoveState.busy;
    const isGroup = structure.structure_type === 'group';
    const title = isGroup ? '解组？' : '解除版本结构？';
    const actionLabel = isGroup ? '解组' : '解除结构';
    const body = isGroup
        ? '解组后将恢复组内图片的独立展示，不会删除文件、素材来源或图片版本。'
        : '解除后将恢复全部原始图片的独立展示，不会删除文件、素材来源或图片版本。';
    overlay.innerHTML = `<section class="registry-structure-remove-dialog" data-registry-structure-remove-type="${escapeAttr(structure.structure_type)}" data-floating-content role="dialog" aria-modal="true" aria-labelledby="registryStructureRemoveTitle">
        <header><span><i data-lucide="${isGroup ? 'ungroup' : 'unlink'}"></i></span><div><strong id="registryStructureRemoveTitle">${title}</strong><small>${escapeHtml(current?.name || (isGroup ? '当前图片组' : '当前最终版本'))}，共 ${structure.members.length} 张图片</small></div><button type="button" data-registry-structure-remove-dialog-close aria-label="关闭" ${busy ? 'disabled' : ''}><i data-lucide="x"></i></button></header>
        <div class="registry-structure-remove-body"><p>${body}</p>${registryStructureRemoveState.error ? `<div class="registry-structure-remove-error" role="alert">${escapeHtml(registryStructureRemoveState.error)}</div>` : ''}</div>
        <footer><button class="hw-btn" type="button" data-registry-structure-remove-dialog-close ${busy ? 'disabled' : ''}>取消</button><button class="hw-btn hw-btn-danger registry-structure-remove-confirm" type="button" data-registry-structure-remove-confirm ${busy ? 'disabled' : ''}><i data-lucide="${busy ? 'loader-2' : (isGroup ? 'ungroup' : 'unlink')}"></i><span>${busy ? '正在解除…' : actionLabel}</span></button></footer>
    </section>`;
    gwCall('preview', 'markAssetFloatingSurface', overlay, {
        surface:'asset-registry-version-structure-remove',
        content:'.registry-structure-remove-dialog',
        close:closeRegistryVersionStructureRemoveDialog,
        trigger:registryStructureRemoveState.trigger,
    });
    refreshIcons();
}
function openRegistryVersionStructureRemoveDialog(structureId, trigger=null){
    const structure = registryAssetStructures.get(String(structureId || ''));
    if(!structure || !['version','group'].includes(structure.structure_type)) return;
    registryStructureRemoveState = {open:true, structureId:structure.id, busy:false, error:'', trigger};
    renderRegistryVersionStructureRemoveDialog();
}
function openRegistryStructureRemoveDialog(structureId, trigger=null){
    openRegistryVersionStructureRemoveDialog(structureId, trigger);
}
function focusRegistryStructureMemberAfterRemoval(assetId){
    const id = String(assetId || '');
    requestAnimationFrame(() => {
        const card = [...root.querySelectorAll('[data-registry-card]')]
            .find(node => node.dataset.registryCard === id);
        card?.focus?.({preventScroll:true});
    });
}
async function confirmRegistryVersionStructureRemoval(){
    const structure = registryAssetStructures.get(registryStructureRemoveState.structureId);
    if(!structure || !['version','group'].includes(structure.structure_type) || registryStructureRemoveState.busy) return;
    if(typeof assetManagerApi.deleteAssetStructure !== 'function') throw new Error('结构解除接口尚未加载');
    registryStructureRemoveState = {...registryStructureRemoveState, busy:true, error:''};
    registryStructureMutationId = structure.id;
    renderRegistryVersionStructureRemoveDialog();
    render();
    try {
        const deleted = await apiJsonResponse(assetManagerApi.deleteAssetStructure(structure.id, structure.version));
        const latestRevision = Number(deleted?.revision);
        if(Number.isFinite(latestRevision) && latestRevision >= 1) registryAssetStructuresRevision = latestRevision;
        const restoredAssetId = structure.current_asset_id;
        registryAssetStructures.delete(structure.id);
        registryStructureMutationId = '';
        closeRegistryVersionStructureRemoveDialog(true);
        render();
        focusRegistryStructureMemberAfterRemoval(restoredAssetId);
        setStatus(`${structure.structure_type === 'group' ? '已解组' : '已解除版本结构'}，恢复 ${structure.members.length} 张图片`);
    } catch(err) {
        // CAS 冲突时先回读结构真值，让确认层以服务端最新版本重新提交。
        await reloadRegistryAssetStructure(structure.id).catch(() => null);
        registryStructureMutationId = '';
        registryStructureRemoveState = {...registryStructureRemoveState, busy:false, error:err.message || (structure.structure_type === 'group' ? '解组失败' : '解除版本结构失败')};
        render();
        renderRegistryVersionStructureRemoveDialog();
        throw err;
    } finally {
        registryStructureMutationId = '';
    }
}
function registryBoardGroupKey(board=assetBoardById(activeTab)){
    return board?.id || (activeTab === 'registry' ? 'registry' : String(activeTab || 'registry'));
}
function registryBoardCustomGroups(board=assetBoardById(activeTab)){
    return registryCustomGroups[registryBoardGroupKey(board)] || [];
}
function registryAssetFolderPath(item={}){
    // 【QIN-527】素材库索引产物只带 canonical_relpath（relative_path 仅媒体产物有），
    // 目录层级必须回退到 canonical_relpath，否则真实物理子目录会全部压到「根目录」。
    const relative = String(item.relative_path || item.canonical_relpath || '').replace(/\\/g, '/').replace(/^\/+|\/+$/g, '');
    const index = relative.lastIndexOf('/');
    return index > 0 ? relative.slice(0, index) : '';
}
function registryAssetFolderLabel(item={}){
    return registryAssetFolderPath(item) || '根目录';
}
function registryAssetMatchesFolder(item, folderPath=''){
    const selected = String(folderPath || '').replace(/\\/g, '/').replace(/^\/+|\/+$/g, '');
    if(!selected) return true;
    const folder = registryAssetFolderPath(item);
    if(selected === '__root__') return !folder;
    return folder === selected || folder.startsWith(`${selected}/`);
}
function registrySourceLabel(item={}, board=null){
    const sourceId = String(item.storage_root_id || '');
    const boardSource = (board?.sources || []).find(source => source.id === sourceId);
    const facet = (registryFacets.roots || []).find(source => String(source.id || '') === sourceId);
    return boardSource?.name || item.storage_root_name || facet?.name || '自动归档';
}
function registryRootIsOffline(item={}){
    if(String(item.root_availability || '') === 'offline') return true;
    const sourceId = String(item.storage_root_id || item.root_id || '');
    return (registryFacets.roots || []).some(source =>
        String(source.id || '') === sourceId && String(source.availability || '') === 'offline'
    );
}
function registryGroupedSections(items=[], board=null){
    const values = Array.isArray(items) ? items : [];
    if(registryGroupMode === 'none') return [{id:'all', name:'', items:values}];
    if(registryGroupMode === 'custom'){
        const groups = registryBoardCustomGroups(board);
        const assigned = new Set();
        const sections = groups.map(group => {
            const ids = new Set(group.assetIds || []);
            const members = values.filter(item => ids.has(item.id) || ids.has(item.structure_current_asset_id));
            members.forEach(item => assigned.add(item.id));
            return {id:group.id, name:group.name, icon:'bookmark', items:members};
        }).filter(section => section.items.length);
        const rest = values.filter(item => !assigned.has(item.id));
        if(rest.length) sections.push({id:'ungrouped', name:'未分组', icon:'inbox', items:rest});
        return sections.length ? sections : [{id:'ungrouped', name:'未分组', icon:'inbox', items:values}];
    }
    const groups = new Map();
    values.forEach(item => {
        const id = registryGroupMode === 'source' ? String(item.storage_root_id || '__automatic__') : registryAssetFolderLabel(item);
        const name = registryGroupMode === 'source' ? registrySourceLabel(item, board) : id;
        if(!groups.has(id)) groups.set(id, {id, name, icon:registryGroupMode === 'source' ? 'database' : 'folder', items:[]});
        groups.get(id).items.push(item);
    });
    return [...groups.values()].sort((a,b) => String(a.name).localeCompare(String(b.name), 'zh-Hans-CN', {numeric:true, sensitivity:'base'}));
}
function registryDisplayedItems(board=assetBoardById(activeTab)){
    const base = board?.system === false ? registryVisibleAssetList(customAssetLibraryItems(board)) : registryVisibleAssets();
    return registryGroupedSections(base, board).flatMap(section => section.items);
}
function registryActiveSelectedIds(board=assetBoardById(activeTab)){
    const available = new Set((board?.system === false ? customAssetLibraryItems(board) : registryAssets).map(item => item.id));
    return [...registrySelectedIds].filter(id => available.has(id));
}
function registrySourcePath(item={}){
    const rootName = String(item.storage_root_name || '统一资产').trim();
    const relativePath = String(item.relative_path || '').replace(/\\/g, '/').replace(/^\/+/, '');
    return relativePath ? `${rootName}/${relativePath}` : rootName;
}
function registrySourceNames(item={}){
    const names = Array.isArray(item.source_names) ? item.source_names.map(String).filter(Boolean) : [];
    return names.length ? [...new Set(names)] : [item.storage_root_name || '统一资产'];
}
// 【QIN-539】修改时间以文件修改时间为准：后端下发 ISO 8601 字符串，本地 File 给毫秒数，
// 统一收敛成 formatDate 需要的毫秒时间戳；两者都没有时返回 0，由调用方决定回落文案。
function registryModifiedTime(item={}){
    const raw = item.modified_at || item.lastModified || 0;
    if(typeof raw === 'number') return Number.isFinite(raw) ? raw : 0;
    const parsed = Date.parse(String(raw || ''));
    return Number.isFinite(parsed) ? parsed : 0;
}
// 确无修改时间时统一回落「—」，不再把「未知」当作时间展示。
function registryModifiedLabel(item={}){
    const value = registryModifiedTime(item);
    return value ? formatDate(value) : '—';
}
// 【QIN-541】标准卡来源小图标：默认隐藏；仅当资产为多库、URL 采集，或来源不在当前左栏所选库
// 范围内时显示（title 恒显示来源名，URL 资产显示采集 URL）。判定口径：
// - 已登记素材库看板（sourceBoard）：素材 storage_root_id ≠ 看板绑定根 → 视为外来；
// - 自定义库看板：素材根不在看板绑定的未隐藏来源内，且机架内来源选择会进一步收窄当前库；
//   看板明确收下自动归档（automaticHidden=false）时，自动归档素材视为当前范围；
// - 系统聚合看板（资产总览等）：以机架目录层级/来源过滤的当前根为「当前库」；未选库时
//   无「当前库」可比，维持默认隐藏。
function registryCardSourceBadge(item={}, board=null){
    const names = registrySourceNames(item);
    const multi = names.length > 1;
    const collectSources = Array.isArray(item.metadata?.collect_sources) ? item.metadata.collect_sources : [];
    const urlSource = String(collectSources[0]?.url || '').trim();
    const root = String(item.storage_root_id || '');
    const offline = registryRootIsOffline(item);
    let foreign = false;
    let icon = 'database';
    let title = names.join(' · ') + (offline ? ' · 离线' : '');
    if(board?.sourceBoard){
        foreign = Boolean(root) && root !== String(board.rootId);
    } else if(board?.system === false){
        const visibleSources = (board.sources || []).filter(source => !source.hidden).map(source => String(source.id));
        const selected = String(boardSourceSelection[board.id] || '');
        if(root){
            foreign = selected ? root !== selected : Boolean(visibleSources.length && !visibleSources.includes(root));
        } else {
            foreign = Boolean(board.automaticHidden);
        }
    } else {
        const scopeRoot = String(registryNavFolder.rootId || registryRootId || '');
        foreign = Boolean(scopeRoot) && Boolean(root) && root !== scopeRoot;
    }
    if(!multi && !urlSource && !foreign) return '';
    if(urlSource){
        icon = 'link';
        title = urlSource;
    }
    return `<span class="registry-card-source" title="${escapeAttr(title)}"><i data-lucide="${icon}"></i></span>`;
}
function registryKindIcon(kind=''){
    return ({image:'image',video:'video',audio:'audio-lines',document:'file-text',workflow:'workflow',font:'type',model:'box',lut:'palette'})[String(kind || '').toLowerCase()] || 'file';
}
function registryCanRecycle(item={}){
    return typeof item.can_recycle === 'boolean'
        ? item.can_recycle
        : Boolean(item.storage_root_path && !item.storage_root_read_only && !item.is_remote);
}
// 【QIN-553】内置 LUT 包：首次进入 LUT 库幂等登记；隐藏/恢复显示经服务端守卫。
// ensure 只做登记并同步隐藏态集合，列表刷新由调用方统一执行，避免双刷新。
let registryBuiltinLutsEnsured = false;
let registryBuiltinHiddenIds = new Set();
async function ensureBuiltinLutsRegistered(options={}){
    if(registryBuiltinLutsEnsured && !options.reveal) return null;
    const data = await apiJsonResponse(assetManagerApi.ensureBuiltinLuts(Boolean(options.reveal)));
    registryBuiltinLutsEnsured = true;
    registryBuiltinHiddenIds = new Set(Array.isArray(data?.hidden) ? data.hidden : []);
    return data;
}
async function toggleRegistryBuiltinHidden(assetId, hidden){
    if(!assetId) return;
    try {
        await apiJsonResponse(assetManagerApi.updateRegistryAsset(assetId, {
            hidden,
            expected_version: requireExpectedVersion(registryStatus.revision, '资产注册表'),
        }));
        if(hidden) registryBuiltinHiddenIds.add(assetId);
        else registryBuiltinHiddenIds.delete(assetId);
        await Promise.all([refreshRegistryAssets(false), refreshRegistryFacetData()]);
        render();
        setStatus(hidden ? '内置 LUT 已隐藏（源文件保留）' : '内置 LUT 已恢复显示');
    } catch(err){
        setStatus(err.message || '内置 LUT 显示状态更新失败');
    }
}
// 【QIN-554】LUT 预览：浏览器内解析 .cube 并套用统一样片，只读、不落盘。
// 预置 LUT 仅十余 KB（8³ 采样），因此按需 fetch 原文 + 前端三线性插值，
// 换取强度滑杆与库内切换的零延迟反馈，也不新增任何后端渲染端点。
const LUT_THUMB_SAMPLE_WIDTH = 320;
const LUT_THUMB_SAMPLE_HEIGHT = 200;
const LUT_THUMB_CONCURRENCY = 2;
const lutPreviewSourceCache = new Map();
const lutPreviewSampleCache = new Map();
const registryLutThumbQueue = [];
function lutPreviewIsAsset(item={}){
    const kind = String(item?.kind || item?.media_type || item?.type || '').toLowerCase();
    return kind === 'lut' || assetFileExtension(item).toLowerCase() === 'cube';
}
function lutPreviewSourceUrl(item={}, source='registry'){
    const explicit = String(item?.url || item?.public_url || '').trim();
    if(explicit) return explicit;
    const assetId = String(item?.id || item?.asset_id || '').trim();
    if(source === 'registry' && assetId) return `/api/asset-registry/assets/${encodeURIComponent(assetId)}/media`;
    return '';
}
function renderRegistryLutThumb(item={}){
    const url = lutPreviewSourceUrl(item, 'registry');
    return `<span class="registry-lut-thumb" data-lut-thumb><canvas class="registry-lut-thumb-canvas" data-lut-thumb-url="${escapeAttr(url)}" width="${LUT_THUMB_SAMPLE_WIDTH}" height="${LUT_THUMB_SAMPLE_HEIGHT}" role="img" aria-label="${escapeAttr(item.name || 'LUT')} 样片预览"></canvas><span class="registry-lut-thumb-badge"><i data-lucide="palette"></i>LUT</span></span>`;
}
function renderRegistryRecycleButton(item={}, className=''){
    if(item.is_remote){
        return `<button class="${escapeAttr(className)} registry-card-recycle" type="button" data-registry-delete-remote="${escapeAttr(item.id || '')}" title="删除 URL 资产" aria-label="删除 ${escapeAttr(item.name || 'URL 资产')}"><i data-lucide="trash-2"></i></button>`;
    }
    // 【QIN-553】内置资产不可移入回收站（服务端同步 403 守卫），卡片提供隐藏开关。
    if(item.metadata?.builtin === true || item.builtin === true){
        const hidden = item.hidden === true;
        const label = hidden ? '显示内置 LUT' : '隐藏内置 LUT';
        return `<button class="${escapeAttr(className)} registry-card-recycle registry-card-builtin-hide" type="button" data-registry-builtin-hidden="${escapeAttr(item.id || '')}" data-hidden-state="${hidden ? '1' : '0'}" title="${label}" aria-label="${label} ${escapeAttr(item.name || '内置素材')}"><i data-lucide="${hidden ? 'eye' : 'eye-off'}"></i></button>`;
    }
    const disabled = !registryCanRecycle(item);
    const title = disabled ? '当前素材来源为只读，不能移入回收站' : '移入 god-recycler';
    return `<button class="${escapeAttr(className)} registry-card-recycle" type="button" data-registry-recycle="${escapeAttr(item.id || '')}" title="${escapeAttr(title)}" aria-label="删除 ${escapeAttr(item.name || '素材')}" ${disabled ? 'disabled' : ''}><i data-lucide="trash-2"></i></button>`;
}
function deferRegistryPreviewThumb(markup, immediate=false){
    const template = document.createElement('template');
    template.innerHTML = String(markup || '');
    template.content.querySelectorAll('img[src]').forEach(image => {
        const source = image.getAttribute('src') || '';
        if(!source || source === REGISTRY_PREVIEW_PLACEHOLDER) return;
        if(immediate){
            image.setAttribute('data-registry-preview-loaded', 'true');
            image.removeAttribute('data-registry-preview-src');
            image.removeAttribute('aria-busy');
            return;
        }
        image.setAttribute('src', REGISTRY_PREVIEW_PLACEHOLDER);
        image.setAttribute('data-registry-preview-src', source);
        image.setAttribute('data-registry-preview-loaded', 'false');
        image.setAttribute('aria-busy', 'true');
    });
    return template.innerHTML;
}
function renderRegistryListRow(item, index=0){
    if(item?.__registry_asset_structure){
        return item.__registry_asset_structure.structure_type === 'group'
            ? renderRegistryGroupStructureListRow(item)
            : renderRegistryVersionStructureListRow(item);
    }
    const selected = registrySelectedIds.has(item.id);
    const name = item.name || 'asset';
    const tags = Array.isArray(item.tags) ? item.tags.slice(0, 4) : [];
    return `<article class="registry-list-row asset-type-${registryVisualType(item)} ${item.id === selectedRegistryAssetId ? 'active' : ''} ${selected ? 'selected' : ''}" data-registry-card="${escapeAttr(item.id)}" draggable="true" data-registry-drag="${escapeAttr(item.id)}" tabindex="0" role="row" aria-label="查看资产 ${escapeAttr(name)}">
        <span class="registry-list-check" role="cell"><input type="checkbox" aria-label="选择 ${escapeAttr(name)}" data-registry-check="${escapeAttr(item.id)}" ${selected ? 'checked' : ''}></span>
        <span class="registry-list-name" role="cell"><span class="registry-list-thumb">${deferRegistryPreviewThumb(assetThumb(item), Number(index) < 30)}</span><strong data-registry-card-name title="${escapeAttr(name)}">${escapeHtml(name)}</strong></span>
        <span class="registry-list-type" role="cell"><i data-lucide="${escapeAttr(registryAssetTypeIcon(item))}"></i>${escapeHtml(registryAssetTypeLabel(item))}</span>
        <span role="cell">${escapeHtml(formatFileSize(item.size || 0))}</span>
        <span role="cell">${escapeHtml(registryModifiedLabel(item))}</span>
        <span class="registry-list-tags" role="cell">${tags.map(tag => `<em>${escapeHtml(tag.name || tag.tag || String(tag))}</em>`).join('') || '<small>—</small>'}${(item.tags || []).length > tags.length ? `<small>+${(item.tags || []).length - tags.length}</small>` : ''}</span>
        <span class="registry-list-path" role="cell" title="${escapeAttr(`${registrySourceNames(item).join(' · ')} / ${registrySourcePath(item)}`)}"><span>${escapeHtml(registrySourceNames(item).join(' · '))}</span>${renderRegistryRecycleButton(item, 'registry-list-recycle')}</span>
    </article>`;
}
function renderRegistryList(items=[]){
    return `<div class="registry-list" role="table" aria-label="资产列表">
        <div class="registry-list-head" role="row"><span></span><span>缩略图 / 名称</span><span>类型</span><span>大小</span><span>修改时间</span><span>标签</span><span>来源路径</span></div>
        <div class="registry-list-body" role="rowgroup">${items.map(renderRegistryListRow).join('')}</div>
    </div>`;
}
function registryVirtualizationEnabled(items=[], board=null){
    return registryLayout === 'grid'
        && registryGroupMode === 'none'
        && !registryAssetStructures.size
        && items.length > REGISTRY_VIRTUAL_WINDOW_SIZE
        && (activeTab === 'registry' || board?.system === false);
}
function registryVirtualStateKey(items=[]){
    return [
        items.length,
        items[0]?.id || '',
        items[items.length - 1]?.id || '',
        registryCardSize,
        registrySimple ? 1 : 0,
        registryAspect,
    ].join(':');
}
function registryVirtualCardHeight(item={}, cardWidth=176){
    const type = registryVisualType(item);
    const width = Number(item.width || item.metadata?.width || 0);
    const height = Number(item.height || item.metadata?.height || 0);
    const ratio = width > 0 && height > 0 ? width / height : type === 'video' ? 16 / 9 : 1;
    const innerWidth = Math.max(1, cardWidth - 2);
    const hasImagePreview = type === 'image' || type === 'video' || (type === 'audio' && assetSupportsImagePreview(item));
    const mediaHeight = hasImagePreview
        ? registryAspect === 'square'
            ? innerWidth
            : width > 0 && height > 0
                ? Math.max(96, Math.min(520, innerWidth / Math.max(.2, ratio)))
                : type === 'video'
                    ? innerWidth / (16 / 9)
                    : 96
        : 150;
      // 【QIN-541】仅标准模式正文收敛到 44px；方形模式保留原正文及 92px 估算，简洁模式为 0。
      const bodyHeight = registrySimple ? 0 : registryAspect === 'square' ? 92 : 44;
    return Math.ceil(mediaHeight + bodyHeight + 14);
}
function registryVirtualHeightCacheKey(item, index=0, cacheMode=''){
    item = item || {};
    const width = Number(item?.width || item?.metadata?.width || 0);
    const height = Number(item?.height || item?.metadata?.height || 0);
    return `${cacheMode}:${String(item?.id || index)}:${String(item?.kind || item?.type || '')}:${width}x${height}:${String(item?.name || '')}`;
}
function registryGridColumnCount(template=''){
    const value = String(template || '').trim();
    const repeat = value.match(/repeat\(\s*([^,]+)\s*,/i);
    if(repeat){
        const count = Number(repeat[1]);
        return Number.isInteger(count) && count > 0 ? count : 0;
    }
    let depth = 0;
    let columns = 0;
    let hasTrack = false;
    for(const char of value){
        if(char === '(') depth += 1;
        else if(char === ')') depth = Math.max(0, depth - 1);
        if(/\s/.test(char) && depth === 0){
            if(hasTrack){ columns += 1; hasTrack = false; }
            continue;
        }
        hasTrack = true;
    }
    if(hasTrack) columns += 1;
    return columns;
}
function registryVirtualMeasureCardHeights(items=[], options){
    const {contentWidth=0, cardWidth=0, cols=1, columnGap=REGISTRY_VIRTUAL_GAP, rowGap=REGISTRY_VIRTUAL_GAP, rowHeight=8, cacheMode=''} = options || {};
    if(!items.length || typeof document === 'undefined' || !document.body || typeof renderRegistryCard !== 'function') return;
    const missing = items.filter((item,index) => !registryVirtualHeightCache.has(registryVirtualHeightCacheKey(item,index,cacheMode)));
    if(!missing.length) return;
    const missingById = new Map(missing.map((item,index) => [String(item?.id || index), {item,index}]));
    const host = document.createElement('div');
    host.style.cssText = `position:fixed;left:-100000px;top:0;width:${contentWidth}px;visibility:hidden;pointer-events:none;contain:layout style`;
    const content = document.createElement('section');
    content.className = `asset-panel asset-content registry-content registry-layout-grid registry-aspect-${registryAspect} ${registrySimple ? 'registry-simple' : ''}`;
    content.style.cssText = `width:${contentWidth}px;--registry-card-size:${cardWidth}px`;
    const grid = document.createElement('div');
    grid.className = 'asset-grid registry-grid';
    grid.style.gridTemplateColumns = `repeat(${cols}, minmax(0px, 1fr))`;
    grid.style.gridAutoRows = `${rowHeight}px`;
    grid.style.columnGap = `${columnGap}px`;
    grid.style.rowGap = `${rowGap}px`;
    grid.innerHTML = missing.map(renderRegistryCard).join('');
    content.appendChild(grid);
    host.appendChild(content);
    document.body.appendChild(host);
    grid.querySelectorAll(':scope > .registry-card[data-registry-card]').forEach(card => {
        const id = String(card.dataset.registryCard || '');
        const entry = missingById.get(id);
        const height = Number(card.getBoundingClientRect().height || 0);
        if(entry && height > 0) registryVirtualHeightCache.set(registryVirtualHeightCacheKey(entry.item,entry.index,cacheMode), height);
    });
    host.remove();
}
function registryVirtualLayout(items=[], width=0, measureMissing=true){
    const grid = root.querySelector('.registry-grid');
    const contentWidth = Math.max(320, Number(width) || grid?.clientWidth || root.querySelector('.registry-content')?.clientWidth || window.innerWidth || 1024);
    const gridStyle = grid ? getComputedStyle(grid) : {};
    const columnGap = Math.max(0, Number.parseFloat(gridStyle.columnGap || gridStyle.gap) || REGISTRY_VIRTUAL_GAP);
    const rowGap = Math.max(0, Number.parseFloat(gridStyle.getPropertyValue?.('--registry-masonry-gap')) || Number.parseFloat(gridStyle.rowGap || gridStyle.gap) || REGISTRY_VIRTUAL_GAP);
    const rowHeight = Math.max(1, Number.parseFloat(gridStyle.gridAutoRows) || 8);
    const preferred = Math.max(REGISTRY_CARD_SIZE.min, Math.min(REGISTRY_CARD_SIZE.max, Number(registryCardSize) || REGISTRY_CARD_SIZE.fallback));
    const cols = Math.max(1, Math.floor((contentWidth + columnGap) / (preferred + columnGap)));
    const availableCardWidth = (contentWidth - columnGap * Math.max(0, cols - 1)) / cols;
    const cardWidth = Math.max(96, Math.min(preferred, availableCardWidth));
    const pitch = rowHeight + rowGap;
    const cacheMode = `${cardWidth.toFixed(2)}:${registryAspect}:${registrySimple ? 1 : 0}`;
    if(registryVirtualHeightCacheMode !== cacheMode){
        registryVirtualHeightCache.clear();
        registryVirtualHeightCacheMode = cacheMode;
    }
    const measuredDomCards = grid?.querySelectorAll?.(':scope > .registry-card[data-registry-card]') || [];
    const itemById = new Map(items.map((item,index) => [String(item?.id || index), {item,index}]));
    measuredDomCards.forEach(card => {
        const id = String(card.dataset.registryCard || '');
        const entry = itemById.get(id);
        if(!entry) return;
        const thumb = card.querySelector?.('.asset-thumb');
        const expectedAspect = Number(entry.item?.width || entry.item?.metadata?.width || 0) > 0 && Number(entry.item?.height || entry.item?.metadata?.height || 0) > 0
            ? (Number(entry.item?.width || entry.item?.metadata?.width || 0) / Number(entry.item?.height || entry.item?.metadata?.height || 1)).toFixed(6)
            : '';
        const actualAspect = thumb?.style?.getPropertyValue?.('--asset-media-aspect') || '';
        const measuredWidth = Number(card.getBoundingClientRect().width || 0);
        const height = Number(card.getBoundingClientRect().height || 0);
        if(thumb && expectedAspect && actualAspect !== expectedAspect) return;
        if(measuredWidth > 0 && Math.abs(measuredWidth - cardWidth) > 1) return;
        if(id && height > 0) registryVirtualHeightCache.set(registryVirtualHeightCacheKey(entry.item,entry.index,cacheMode), height);
    });
    if(measureMissing) registryVirtualMeasureCardHeights(items, {contentWidth, cardWidth, cols, columnGap, rowGap, rowHeight, cacheMode});
    // 每次放入当前最短列，避免行优先占位留下大块空隙。
    const columnBottoms = Array(cols).fill(0);
    const itemLayouts = [];
    items.forEach((item,index) => {
        const cachedHeight = registryVirtualHeightCache.get(registryVirtualHeightCacheKey(item,index,cacheMode));
        const height = Math.max(1, Number(cachedHeight || registryVirtualCardHeight(item, cardWidth)) || 1);
        const column = columnBottoms.indexOf(Math.min(...columnBottoms));
        const top = columnBottoms[column];
        const span = Math.max(1, Math.ceil((height + rowGap) / pitch));
        itemLayouts.push({left:column * (cardWidth + columnGap), top, width:cardWidth,
            height, column, row:Math.floor(top / pitch), span});
        columnBottoms[column] = top + height + rowGap;
    });
    if(!items.length){
        registryVirtualHeightCache.clear();
    }
    const rowTops = itemLayouts.map(position => position.top);
    const rowHeights = itemLayouts.map(position => position.height);
    const itemMaxBottoms = [];
    let maxBottom = 0;
    itemLayouts.forEach(position => {
        maxBottom = Math.max(maxBottom, position.top + position.height);
        itemMaxBottoms.push(maxBottom);
    });
    const totalHeight = Math.max(0, ...columnBottoms) - (items.length ? rowGap : 0);
    return {cols, cardWidth, width:contentWidth, rowHeight, rowGap, rowHeights, rowTops, itemMaxBottoms, totalHeight, itemLayouts};
}
function registryVirtualRowAt(layout, target, fallback=0){
    const rows = layout.rowTops || [];
    let low = 0;
    let high = rows.length - 1;
    while(low <= high){
        const middle = Math.floor((low + high) / 2);
        if(rows[middle] <= target) low = middle + 1;
        else high = middle - 1;
    }
    return Math.max(0, Math.min(rows.length - 1, high >= 0 ? high : fallback));
}
function registryVirtualRange(items, layout, scrollTop=0, viewportHeight=650){
    if(!items.length) return {start:0, end:0};
    const overscan = REGISTRY_VIRTUAL_OVERSCAN_ROWS;
    const startTarget = Math.max(0, scrollTop) - overscan * 260;
    const endTarget = Math.max(0, scrollTop) + Math.max(320, viewportHeight) + overscan * 260;
    const itemMaxBottoms = layout.itemMaxBottoms || [];
    let start = 0;
    if(itemMaxBottoms.length === items.length){
        let low = 0;
        let high = items.length;
        while(low < high){
            const middle = Math.floor((low + high) / 2);
            if(itemMaxBottoms[middle] < startTarget) low = middle + 1;
            else high = middle;
        }
        start = Math.min(items.length - 1, low);
    } else {
        const firstRow = Math.max(0, registryVirtualRowAt(layout, startTarget));
        start = Math.min(items.length - 1, firstRow * layout.cols);
    }
    const lastItem = Math.min(items.length - 1, registryVirtualRowAt(layout, endTarget));
    let end = Math.min(items.length, Math.max(start + 1, lastItem + 1));
    const focusId = String(registryFocusRestoreId || '');
    if(focusId){
        const focusIndex = items.findIndex(item => String(item?.id || '') === focusId);
        if(focusIndex >= 0){
            start = Math.min(start, focusIndex);
            end = Math.max(end, focusIndex + 1);
        }
    }
    return {start, end};
}
function registryVirtualCardMarkup(item, index, layout){
    const markup = renderRegistryCard(item, index).trim();
    const position = layout.itemLayouts[index] || {left:0, top:0, width:layout.cardWidth};
    const end = markup.indexOf('>');
    if(end < 0) return markup;
    const style = `--registry-virtual-left:${position.left.toFixed(2)}px;--registry-virtual-top:${position.top.toFixed(2)}px;--registry-virtual-width:${position.width.toFixed(2)}px`;
    return `${markup.slice(0, end)} style="${style}"${markup.slice(end)}`;
}
function registryVirtualGridMarkup(items=[], layout, range={start:0,end:0}){
    const before = layout.itemLayouts[range.start]?.top || 0;
    const after = Math.max(0, layout.totalHeight - before);
    const cards = items.slice(range.start, range.end).map((item, offset) => registryVirtualCardMarkup(item, range.start + offset, layout)).join('');
    return `<div class="registry-grid registry-grid-virtualized" data-registry-virtual-grid style="--registry-virtual-height:${layout.totalHeight.toFixed(2)}px">
        <div class="registry-virtual-spacer" data-registry-virtual-spacer="top" style="height:${before.toFixed(2)}px" aria-hidden="true"></div>
        ${cards}
        <div class="registry-virtual-spacer" data-registry-virtual-spacer="bottom" style="height:${after.toFixed(2)}px" aria-hidden="true"></div>
    </div>`;
}
function restoreRegistryVirtualScroll(scrollRoot, previousScrollTop=0, anchorId='', anchorOffset=0){
    if(!scrollRoot) return;
    scrollRoot.scrollTop = Math.max(0, Number(previousScrollTop) || 0);
    if(!anchorId) return;
    const anchor = [...(scrollRoot.querySelectorAll?.('[data-registry-card]') || [])]
        .find(card => String(card?.dataset?.registryCard || '') === String(anchorId));
    if(!anchor) return;
    const scrollTop = scrollRoot.getBoundingClientRect?.().top || 0;
    const anchorTop = (anchor.getBoundingClientRect?.().top || 0) - scrollTop;
    scrollRoot.scrollTop = Math.max(0, scrollRoot.scrollTop + anchorTop - (Number(anchorOffset) || 0));
}
function renderRegistryVirtualGrid(items=[], board=null){
    const scrollRoot = root.querySelector('.registry-assets-scroll');
    const gridWidth = scrollRoot?.querySelector?.('.registry-grid')?.clientWidth || scrollRoot?.clientWidth || 0;
    const layout = registryVirtualLayout(items, gridWidth);
    const scrollTop = scrollRoot ? scrollRoot.scrollTop : (savedScrollPositions.registry || 0);
    const range = registryVirtualRange(items, layout, scrollTop, scrollRoot?.clientHeight || 650);
    registryVirtualState = {
        ...layout,
        width:layout.width,
        key:registryVirtualStateKey(items),
        start:range.start,
        end:range.end,
    };
    return registryVirtualGridMarkup(items, layout, range);
}
function registryVirtualItemsForActiveBoard(){
    const board = activeTab === 'registry' ? null : assetBoardById(activeTab);
    const base = board?.system === false ? registryVisibleAssetList(customAssetLibraryItems(board)) : registryVisibleAssets();
    return {board, items:registryProjectedAssetItems(base)};
}
function updateRegistryVirtualWindow(force=false, skipMeasurements=false){
    const grid = root.querySelector('[data-registry-virtual-grid]');
    const scrollRoot = grid?.closest('.registry-assets-scroll');
    if(!grid || !scrollRoot) return false;
    const {board, items} = registryVirtualItemsForActiveBoard();
    if(!registryVirtualizationEnabled(items, board)) return false;
    const width = Math.max(320, grid.clientWidth || scrollRoot.clientWidth || root.querySelector('.registry-content')?.clientWidth || window.innerWidth || 1024);
    const key = registryVirtualStateKey(items);
    const canReuseLayout = !force
        && registryVirtualState.key === key
        && registryVirtualState.itemLayouts?.length === items.length
        && Math.abs(Number(registryVirtualState.width || 0) - width) < 1;
    const oldState = registryVirtualState;
    const anchorIndex = force && oldState.key === key
        ? oldState.itemLayouts.findIndex(position => position.top + position.height > scrollRoot.scrollTop)
        : -1;
    const anchorOffset = anchorIndex >= 0 ? oldState.itemLayouts[anchorIndex].top - scrollRoot.scrollTop : 0;
    const layout = canReuseLayout ? oldState : registryVirtualLayout(items, width, !skipMeasurements);
    const adjustedScrollTop = anchorIndex >= 0 && layout.itemLayouts[anchorIndex]
        ? Math.max(0, layout.itemLayouts[anchorIndex].top - anchorOffset) : scrollRoot.scrollTop;
    const range = registryVirtualRange(items, layout, adjustedScrollTop, scrollRoot.clientHeight || 650);
    const sameWindow = !force
        && registryVirtualState.key === key
        && registryVirtualState.start === range.start
        && registryVirtualState.end === range.end
        && registryVirtualState.cols === layout.cols
        && Math.abs(Number(registryVirtualState.width || 0) - width) < 1
        && Math.abs(Number(registryVirtualState.totalHeight || 0) - layout.totalHeight) < 1;
    if(sameWindow) return false;
    const registryPreviewCache = captureRegistryPreviewImages();
    grid.style.setProperty('--registry-virtual-height', `${layout.totalHeight.toFixed(2)}px`);
    grid.innerHTML = registryVirtualGridMarkup(items, layout, range).replace(/^<div class="registry-grid registry-grid-virtualized"[^>]*>/, '').replace(/<\/div>\s*$/, '');
    restoreRegistryPreviewImages(registryPreviewCache);
    grid.querySelectorAll('img[data-registry-preview-loaded="true"]').forEach(applyRegistryVirtualPreviewAspect);
    registryVirtualState = {
        ...layout,
        width,
        key,
        start:range.start,
        end:range.end,
    };
    refreshIcons();
    scrollRoot.scrollTop = adjustedScrollTop;
    observeRegistryVirtualCards(grid);
    scheduleRegistryPreviewImages();
    return true;
}
function scheduleRegistryVirtualWindow(){
    registryVirtualScrollCleanup?.();
    registryVirtualScrollCleanup = null;
    const grid = root.querySelector('[data-registry-virtual-grid]');
    const scrollRoot = grid?.closest('.registry-assets-scroll');
    if(!grid || !scrollRoot) return;
    let frame = 0;
    const onScroll = () => {
        if(frame) return;
        frame = requestAnimationFrame(() => {
            frame = 0;
            updateRegistryVirtualWindow();
        });
    };
    scrollRoot.addEventListener('scroll', onScroll, {passive:true});
    registryVirtualScrollCleanup = () => {
        scrollRoot.removeEventListener('scroll', onScroll);
        if(frame) cancelAnimationFrame(frame);
    };
    observeRegistryVirtualCards(grid);
    requestAnimationFrame(() => scheduleRegistryVirtualRemeasure());
}
function renderRegistryAssetSections(items=[], board=null, emptyText='暂无匹配资产'){
    const projectedItems = registryProjectedAssetItems(items);
    if(registryLoading && !projectedItems.length) return '<div class="empty-state"><i data-lucide="loader"></i>正在加载…</div>';
    if(!projectedItems.length) return `<div class="empty-state">${escapeHtml(emptyText)}</div>`;
    if(registryVirtualizationEnabled(projectedItems, board)) return renderRegistryVirtualGrid(projectedItems, board);
    const sections = registryGroupedSections(projectedItems, board);
    if(registryLayout !== 'list'){
        // Keep one masonry flow even when the user groups by source/folder. A
        // grid per section resets CSS columns and makes later sections look
        // like a regular row layout.
        return `<div class="asset-grid registry-grid">${sections.map(section => `${section.name ? `<header class="registry-grid-group-header"><span><i data-lucide="${escapeAttr(section.icon || 'folder')}"></i><strong>${escapeHtml(section.name)}</strong></span><em>${section.items.length}</em></header>` : ''}${section.items.map(renderRegistryCard).join('')}`).join('')}</div>`;
    }
    return sections.map(section => `<section class="registry-asset-group ${section.name ? '' : 'ungrouped'}">
        ${section.name ? `<header><span><i data-lucide="${escapeAttr(section.icon || 'folder')}"></i><strong>${escapeHtml(section.name)}</strong></span><em>${section.items.length}</em></header>` : ''}
        ${renderRegistryList(section.items)}
    </section>`).join('');
}
function registryHasMoreAssets(){
    return registryPageHasMore;
}
function renderRegistryPagination(){
    if(!registryAssets.length || !registryHasMoreAssets()) return '';
    // 旧版“加载全部结果后可按相似素材堆叠”入口已移除；保留无障碍
    // sentinel 标记供旧浏览器回退逻辑识别，用户无需点击分页控件。
    return `<div class="registry-infinite-sentinel" aria-live="polite" aria-busy="${registryPageLoading}">${registryPageLoading ? '<i data-lucide="loader-2"></i><span>正在加载更多素材…</span>' : '<span>继续下滑自动加载</span>'}<button type="button" data-registry-load-more hidden tabindex="-1" aria-hidden="true"></button></div>`;
}
function syncRegistryVaultFooter(){
    if(activeTab !== 'registry') return;
    const footer = root.querySelector('.registry-vault-footer');
    const spans = Array.from(footer?.querySelectorAll('span') || []);
    if(spans.length < 3) return;
    const total = spans[0].querySelector('strong');
    if(total) total.textContent = registryTotalKnown ? Number(registryTotal || 0).toLocaleString('zh-CN') : '统计中';
    spans[1].textContent = registryLoading ? '正在读取' : `已加载${registryPageHasMore ? '' : '全部'} ${registryAssets.length} 项`;
    spans[2].textContent = registryPageHasMore ? '下滑加载更多' : `当前显示 ${registryVisibleAssets().length} 项`;
}
function selectedRegistryAsset(){
    return registryAssets.find(item => item.id === selectedRegistryAssetId) || registryDetail || null;
}
function registryFilterDefinition(){
    return {
        query:registryQuery,
        kind:registryKind,
        category:registryStatCategory,
        collectionId:registryCollectionId,
        rootId:registryRootId,
        recentDays:registryRecentDays,
        sort:registrySort,
        projectId:registryProjectFilterId,
        includeTags:[...registryIncludeTags],
        excludeTags:[...registryExcludeTags],
        groupSimilar:false,
        groupMode:registryGroupMode,
    };
}
function applyRegistryFilterDefinition(definition={}){
    registryQuery = String(definition.query || '');
    registryKind = String(definition.kind || '');
    registryStatCategory = String(definition.category || '');
    registryCollectionId = String(definition.collectionId || '');
    registryRootId = String(definition.rootId || '');
    registryRecentDays = Number(definition.recentDays || 0);
    registrySort = String(definition.sort || 'newest');
    registryProjectFilterId = String(definition.project_id || definition.projectId || '');
    registryIncludeTags = new Set(Array.isArray(definition.includeTags) ? definition.includeTags : []);
    registryExcludeTags = new Set(Array.isArray(definition.excludeTags) ? definition.excludeTags : []);
    registryGroupSimilar = false;
    if(['none','source','folder','custom'].includes(definition.groupMode)) registryGroupMode = definition.groupMode;
    selectedRegistryAssetId = '';
    registryDetail = null;
    registrySelectedIds.clear();
    registryPageLoading = false;
    registryNextOffset = 0;
    registryNextCursor = '';
    registryPageHasMore = false;
    registryVirtualState = {key:'', start:0, end:0, cols:1, totalHeight:0, rowTops:[], rowHeights:[], itemLayouts:[]};
    resetRegistryAssetStructures();
}
function registryQueryString({offset=0, limit=null, cursor=''}={}){
    // 【QIN-584 AC1】一律按视口动态上限取一页（不少于 100、不多于 1500），
    // 不再默认 limit=1500 让千级库一次下发全库。limit 与 dynamic_limit 同源，
    // 前后端口径不会各说各话；后续页沿用同一页大小，保证分页不重不漏。
    // 显式 limit 也不能把服务端上限顶大：dynamic_limit 永远不超过视口页大小。
    const viewportLimit = registryDynamicPageSize();
    const pageLimit = Math.min(REGISTRY_PAGE_SIZE, Math.max(1, Number(limit) || viewportLimit));
    const params = new URLSearchParams({limit:String(pageLimit), offset:String(Math.max(0, Number(offset) || 0)), sort:registrySort || 'newest', view:'card'});
    // 默认呈现全局统一资产金库；仅当用户通过「项目素材」智能筛选选择项目时携带（解耦默认的 activeProjectId）
    // 【QIN-572】项目视图下默认传 __all__ 或选中的具体 project_id
    if(activeTab === 'projects' && !registryProjectFilterId) registryProjectFilterId = '__all__';
    if(registryProjectFilterId) params.set('project_id', registryProjectFilterId);
    params.set('dynamic_limit', String(Math.min(pageLimit, viewportLimit)));
    if(cursor) params.set('cursor', cursor);
    if(registryQuery.trim()) params.set('search', registryQuery.trim());
    if(registryKind) params.set('kind', registryKind);
    if(registryStatCategory) params.set('category', registryStatCategory);
    if(registryCollectionId) params.set('collection_id', registryCollectionId);
    const board = assetBoardById(activeTab);
    const folderRoot = board?.system === false ? (board.sourceBoard ? board.rootId : boardSourceSelection[board.id]) : registryNavFolder.rootId;
    const folderPath = board?.system === false ? boardFolderSelection[board.id] : registryNavFolder.path;
    const selectedRoot = folderRoot && folderRoot !== '__automatic__' ? folderRoot : registryRootId;
    if(selectedRoot) params.set('root_id', selectedRoot);
    if(board?.system === false && !board.sourceBoard){
        const sources = Array.isArray(board.sources) ? board.sources : [];
        const visible = sources.filter(source => !source.hidden).map(source => String(source.id));
        const known = [...new Set([...sources.map(source => String(source.id)), ...(board.excludedSourceIds || []).map(String)])];
        params.set('board_sources', JSON.stringify({visible, known, automatic:!board.automaticHidden, automatic_only:folderRoot === '__automatic__'}));
    }
    if(folderPath) params.set('folder_path', folderPath);
    if(registryRecentDays) params.set('recent_days', String(registryRecentDays));
    if(registryIncludeTags.size) params.set('include_tags', [...registryIncludeTags].join(','));
    if(registryExcludeTags.size) params.set('exclude_tags', [...registryExcludeTags].join(','));
    return params.toString();
}
function registryTotalFromResponse(data={}){
    const value = Number(data?.total);
    const explicitlyKnown = data?.total_known === true;
    const explicitlyUnknown = data?.total_known === false;
    registryTotalKnown = !explicitlyUnknown && (explicitlyKnown || Number.isFinite(value));
    registryTotal = registryTotalKnown ? Math.max(0, value) : null;
    return registryTotal;
}
function registryTotalSummary(visibleCount=registryAssets.length){
    return registryTotalKnown
        ? `共 ${Number(registryTotal || 0)} 项`
        : `已加载 ${Math.max(0, Number(visibleCount || 0))} 项`;
}
function registryAssetPageCacheEntry(data={}){
    const totalValue = Number(data.total);
    const totalKnown = data.total_known === true || (data.total_known === undefined && Number.isFinite(totalValue));
    const nextOffset = data.next_offset;
    return {
        items:Array.isArray(data.items) ? data.items : [],
        total:totalKnown ? Math.max(0, totalValue) : null,
        total_known:totalKnown,
        limit:Number(data.limit || REGISTRY_PAGE_SIZE),
        offset:Math.max(0, Number(data.offset) || 0),
        has_more:data.has_more === true,
        next_offset:nextOffset === null || nextOffset === undefined ? null : Number(nextOffset),
        next_cursor:typeof data.next_cursor === 'string' ? data.next_cursor : '',
    };
}
// 【优化问题1】LRU 缓存管理，当缓存超过限制时清理最旧的条目
function setRegistryAssetCache(key, value){
    if(registryAssetsQueryCache.size >= REGISTRY_CACHE_MAX_SIZE){
        const firstKey = registryAssetsQueryCache.keys().next().value;
        if(firstKey !== undefined) registryAssetsQueryCache.delete(firstKey);
    }
    registryAssetsQueryCache.set(key, value);
}
function applyRegistryAssetResponse(data={}, {append=false}={}){
    const incoming = (data.items || []).map(normalizeRegistryAsset);
    if(append){
        const existingIds = new Set(registryAssets.map(item => item.id));
        registryAssets = [...registryAssets, ...incoming.filter(item => !existingIds.has(item.id))];
    } else registryAssets = incoming;
    registryTotalFromResponse(data);
    const responseOffset = Math.max(0, Number(data.offset) || 0);
    const responseNextOffset = data.next_offset === null || data.next_offset === undefined
        ? Number.NaN
        : Number(data.next_offset);
    registryNextOffset = Number.isFinite(responseNextOffset) ? responseNextOffset : responseOffset + incoming.length;
    registryPageHasMore = typeof data.has_more === 'boolean'
        ? data.has_more
        : registryTotalKnown
            ? registryNextOffset < registryTotal
            : incoming.length >= (Number(data.limit) || REGISTRY_PAGE_SIZE);
    registryNextCursor = registryPageHasMore && typeof data.next_cursor === 'string' ? data.next_cursor : '';
    if(!append) registrySelectedIds = new Set([...registrySelectedIds].filter(id => registryAssets.some(item => item.id === id)));
    if(!append && selectedRegistryAssetId && !registryAssets.some(item => item.id === selectedRegistryAssetId)){
        selectedRegistryAssetId = '';
        registryDetail = null;
    }
}
function abortRegistryAssetRequests(){
    registryRequestAbortController?.abort?.();
    registryRequestAbortController = null;
    registryPrefetchAbortController?.abort?.();
    registryPrefetchAbortController = null;
}
function beginRegistryAssetRequest(){
    registryRequestAbortController?.abort?.();
    registryRequestAbortController = typeof AbortController === 'function' ? new AbortController() : null;
    return registryRequestAbortController?.signal;
}
function isRegistryAbortError(error){
    return error?.name === 'AbortError' || String(error?.message || '').toLowerCase().includes('aborted');
}
// 【QIN-585 AC2】所有「替换当前列表」的读取都汇聚到这个共享入口：搜索、筛选、排序、
// 翻页、看板切换都经 refreshRegistryAssets，内部 ++registryRequestSeq 并 abort 上一代列表。
// 因此它必须登记为新一代 —— 否则首屏 A 被它 abort 后的旧终态会把 pending 清成 false，
// 延后链路抢在 B 前面发出（第 4 轮亲复现：A pending 时搜索，B 仍在途就发了 inbox/governance）。
// 与 refreshRegistryBootstrapTracked 同口径：返回**原始 request**，业务调用方的
// await / .then / .catch 仍能拿到列表 500 等真实失败，终态登记不吞错误。
function refreshRegistryAssets(renderNow=true){
    return trackRegistryBootstrapSettled(refreshRegistryAssetsRequest(renderNow));
}
async function refreshRegistryAssetsRequest(renderNow=true){
    const requestId = ++registryRequestSeq;
    abortRegistryAssetRequests();
    const signal = beginRegistryAssetRequest();
    registryLoading = true;
    registryPageLoading = false;
    registryNextOffset = 0;
    registryNextCursor = '';
    registryPageHasMore = false;
    registryAssetsQueryCache.clear();
    resetRegistryAssetStructures();
    // 筛选/排序变化后目录索引需要按新结果重算，否则会沿用旧查询的补拉结论
    gwCall('settings', 'resetAssetRackDirectoryIndex');
    // 【审核 N1 加固】筛选/查询变化后旧对账跟踪页失效，避免跨筛选误删增量对账条目
    registryReconcilePageIds = new Set();
    registryPendingNewIds = new Set();
    registryPendingVanishedIds = new Set();
    registryPendingNewItems.clear();
    if(renderNow) render();
    try {
        const queryKey = registryQueryString();
        const data = await apiJsonResponse(assetManagerApi.getRegistryAssets(queryKey, signal ? {signal} : {}));
        if(requestId !== registryRequestSeq) return;
        setRegistryAssetCache(queryKey, registryAssetPageCacheEntry(data));
        applyRegistryAssetResponse(data);
        await refreshRegistryAssetStructures({requestId});
        if(requestId !== registryRequestSeq) return;
        // Establish the first visible-page baseline so the next incremental
        // poll can report assets that disappeared after initial load.
        registryReconcilePageIds = new Set(registryAssets.map(item => item.id));
        apiJsonResponse(assetManagerApi.getRegistryStatus()).then(statusData => {
            if(requestId !== registryRequestSeq || !statusData || typeof statusData !== 'object') return;
            registryStatus = statusData;
            if(activeTab === 'registry') render();
        }).catch(() => {});
        scheduleRegistryProjectCounts();
    } catch(err) {
        if(isRegistryAbortError(err)) return;
        throw err;
    } finally {
        if(requestId === registryRequestSeq) registryRequestAbortController = null;
        if(requestId === registryRequestSeq) registryLoading = false;
        if(renderNow && requestId === registryRequestSeq) render();
        if(requestId === registryRequestSeq && !registryLoading) scheduleRegistryPrefetch();
    }
}
function registryAppendDomContext(){
    // 【修复滚动跳顶】快路径仅限网格布局且未启用分组：追加在网格末尾不会移动视口，
    // 无需整树渲染、无需滚动恢复；列表/分组布局存在组序语义，退回既有 render() 路径。
    if(registryLayout === 'list') return null;
    if(registryGroupMode !== 'none') return null;
    if(registryAssetStructures.size) return null;
    const board = assetBoardById(activeTab);
    if(activeTab !== 'registry' && board?.system !== false) return null;
    const scrollRoot = root.querySelector('.registry-assets-scroll');
    if(!scrollRoot) return null;
    const grid = scrollRoot.querySelector('.registry-grid');
    if(!grid) return null;
    if(!grid.classList.contains('registry-grid-virtualized')) return {scrollRoot, grid};
    return {scrollRoot, grid, virtual:true};
}
function appendRegistryAssetPageCards(items=[], context){
    // 【修复滚动跳顶】DOM 安全末尾追加：只为当前显示列表会包含的素材建卡（自定义看板有类型/来源过滤），
    // 复用既有卡片模板 appendChild 到网格末尾；严禁整体 render()、严禁替换既有卡片节点；
    // 后处理仅 refreshIcons + masonry span 调度 + 懒加载预览观察；不动滚动位置、不抢焦点。
    if(!items.length || !context) return 0;
    if(context.virtual){
        updateRegistryVirtualWindow(true);
        return 0;
    }
    const displayIds = new Set(activeRegistryVisibleAssets().map(item => item.id));
    const domIds = new Set([...context.scrollRoot.querySelectorAll('[data-registry-card]')].map(node => node.dataset.registryCard || ''));
    let inserted = 0;
    items.forEach(item => {
        if(!displayIds.has(item.id) || domIds.has(item.id)) return;
        const template = document.createElement('template');
        template.innerHTML = renderRegistryCard(item).trim();
        const node = template.content.firstElementChild;
        if(!node) return;
        context.grid.appendChild(node);
        domIds.add(item.id);
        inserted += 1;
    });
    if(!inserted) return 0;
    refreshIcons();
    const bootSkeleton = document.getElementById('assetManagerBootSkeleton');
    if(bootSkeleton) bootSkeleton.hidden = true;
    scheduleRegistryMasonryLayout();
    scheduleRegistryVirtualWindow();
    scheduleRegistryPreviewImages();
    return inserted;
}
function promoteRegistryGridToVirtual(){
    // 【QIN-518】跨虚拟化阈值时原地升级：以当前首个可见卡片为滚动锚点，
    // 只把网格容器内部结构换成虚拟窗口，筛选栏/侧栏/滚动容器节点全部保留，
    // 严禁调用全局 render() 整树重建，因此不会出现翻页闪跳。
    const scrollRoot = root.querySelector('.registry-assets-scroll');
    const grid = scrollRoot?.querySelector?.(':scope > .registry-grid');
    if(!scrollRoot || !grid || grid.classList.contains('registry-grid-virtualized')) return false;
    const {board, items} = registryVirtualItemsForActiveBoard();
    if(!registryVirtualizationEnabled(items, board)) return false;
    const previousScrollTop = scrollRoot.scrollTop;
    const scrollRect = scrollRoot.getBoundingClientRect();
    let anchorId = '';
    let anchorOffset = 0;
    for(const card of grid.querySelectorAll(':scope > .registry-card[data-registry-card]')){
        const cardRect = card.getBoundingClientRect();
        if(cardRect.bottom <= scrollRect.top + 1) continue;
        anchorId = String(card.dataset.registryCard || '');
        anchorOffset = cardRect.top - scrollRect.top;
        break;
    }
    const registryPreviewCache = captureRegistryPreviewImages();
    const width = Math.max(320, grid.clientWidth || scrollRoot.clientWidth || 0);
    const layout = registryVirtualLayout(items, width);
    const range = registryVirtualRange(items, layout, previousScrollTop, scrollRoot.clientHeight || 650);
    const template = document.createElement('template');
    template.innerHTML = registryVirtualGridMarkup(items, layout, range).trim();
    const nextGrid = template.content.firstElementChild;
    if(!nextGrid) return false;
    grid.replaceWith(nextGrid);
    registryVirtualState = {
        ...layout,
        width,
        key:registryVirtualStateKey(items),
        start:range.start,
        end:range.end,
    };
    restoreRegistryPreviewImages(registryPreviewCache);
    refreshIcons();
    // 先恢复 scrollTop 再按锚点卡片微调，保证升级前后视口停留位置一致。
    scrollRoot.scrollTop = previousScrollTop;
    restoreRegistryVirtualScroll(scrollRoot, previousScrollTop, anchorId, anchorOffset);
    scheduleRegistryVirtualWindow();
    scheduleRegistryPreviewImages();
    return true;
}
async function loadMoreRegistryAssets(){
    if(registryLoading || registryPageLoading || !registryHasMoreAssets()) return;
    const requestId = registryRequestSeq;
    const offset = registryNextOffset;
    const cursor = registryNextCursor;
    if(!Number.isFinite(offset) || offset < 0) return;
    const queryKey = registryQueryString({offset,cursor});
    registryPageLoading = true;
    cancelRegistryPrefetchTimer();
    // 【修复浏览刷新】加载前一律不整树 render()：网格快路径原地同步哨兵忙态；
    // 列表/分组布局回退路径同样只原地同步哨兵（分组顺序语义仍需收尾一次 render()，
    // 但加载前的整树重建已消除——原先加载前后各一次 render()，翻倍且引发跳顶）。
    const appendContext = registryAppendDomContext();
    let appendTarget = appendContext;
    contextSentinelBusy(appendContext ? appendContext.scrollRoot : root.querySelector('.registry-assets-scroll'), true);
    let appendedIds = [];
    let structurePresentationChanged = false;
    let promoteToVirtual = false;
    try {
        const cached = registryAssetsQueryCache.get(queryKey);
        let data = cached;
        if(!data && registryPrefetchInFlight && registryPrefetchQueryKey === queryKey && registryPrefetchPromise){
            // 【浏览预加载】命中进行中的预取：共享同一请求等待落地，不重复发起网络请求；
            // 预取完成后数据已在缓存中，直接读出消费。
            data = await registryPrefetchPromise.then(() => registryAssetsQueryCache.get(queryKey)).catch(() => null);
        }
        if(!data){
            const signal = beginRegistryAssetRequest();
            data = signal
                ? await apiJsonResponse(assetManagerApi.getRegistryAssets(queryKey, {signal}))
                : await apiJsonResponse(assetManagerApi.getRegistryAssets(queryKey));
        }
        if(requestId !== registryRequestSeq) return;
        setRegistryAssetCache(queryKey, registryAssetPageCacheEntry(data));
        const existingIds = new Set(registryAssets.map(item => item.id));
        applyRegistryAssetResponse(data, {append:true});
        // Keep structure discovery proportional to this page.  Re-querying the
        // accumulated registry IDs here turns every page append into an
        // increasingly expensive full-library fan-out.
        appendedIds = (Array.isArray(data.items) ? data.items : [])
            .map(item => String(item?.id || ''))
            .filter(id => id && !existingIds.has(id));
        // 【QIN-584】首屏页的结构补齐若还在飞，先等它落地再发本页查询：
        // 结构查询共用同一个请求序号，抢跑会递增序号、把首屏页的结果作废在 await 检查点。
        if(registryStructureBootstrapPromise){
            await registryStructureBootstrapPromise.catch(() => {});
            if(requestId !== registryRequestSeq) return;
        }
        const structuresChanged = await refreshRegistryAssetStructures({
            requestId,
            assetIds: appendedIds,
            replace:false,
        });
        if(requestId !== registryRequestSeq) return;
        structurePresentationChanged = structuresChanged;
        // The first page is intentionally rendered as a normal grid so the
        // existing append fast path remains cheap. 【QIN-518】Once the loaded
        // window crosses the virtualization threshold, upgrade the grid in
        // place instead of a whole-tree render(), so the scroll anchor and the
        // surrounding chrome survive the transition.
        if(appendTarget){
            const board = activeTab === 'registry' ? null : assetBoardById(activeTab);
            const shouldVirtualize = registryVirtualizationEnabled(registryDisplayedItems(board), board);
            if(shouldVirtualize !== appendTarget.virtual){
                promoteToVirtual = shouldVirtualize && !appendTarget.virtual && !structurePresentationChanged;
                appendTarget = null;
            }
        }
        if(appendTarget && !structurePresentationChanged){
            const appendedItems = appendedIds
                .map(id => registryAssets.find(item => item.id === id))
                .filter(Boolean);
            appendRegistryAssetPageCards(appendedItems, appendContext);
        }
    } catch(err) {
        if(requestId === registryRequestSeq && err.status === 409 && err.code === 'VERSION_CONFLICT'){
            // 集合变化后丢弃旧游标、预取与缓存，重新读取第一页，不拼接不同快照。
            await refreshRegistryAssets();
            setStatus('资产列表已变化，已重新读取第一页');
        } else if(requestId === registryRequestSeq && !isRegistryAbortError(err)) setStatus(err.message || '加载更多资产失败');
    } finally {
        if(requestId !== registryRequestSeq) return;
        registryRequestAbortController = null;
        registryPageLoading = false;
        if(promoteToVirtual){
            // 【QIN-518】跨阈值：原地升级为虚拟窗口，保留滚动锚点，不走全局 render()。
            const promoted = promoteRegistryGridToVirtual();
            if(appendContext) contextSentinelBusy(appendContext.scrollRoot, false);
            if(promoted){
                if(!registryHasMoreAssets()) appendContext?.scrollRoot.querySelector('.registry-infinite-sentinel')?.remove();
                syncRegistryVaultFooter();
            } else {
                // 原地升级前置条件失效（布局/分组在本次加载中变化）时的兜底路径。
                render();
            }
        } else if(appendTarget && !structurePresentationChanged){
            // Fast path has already updated the existing sentinel and cards.
            if(appendContext){
                // 快路径收尾：仅同步哨兵忙态，不重建任何卡片节点；已无更多资产时摘除哨兵，避免残留误导文案
                contextSentinelBusy(appendContext.scrollRoot, false);
                if(!registryHasMoreAssets()) appendContext.scrollRoot.querySelector('.registry-infinite-sentinel')?.remove();
            }
            syncRegistryVaultFooter();
        } else {
            render();
            requestAnimationFrame(() => {
                if(requestId !== registryRequestSeq) return;
                document.querySelector('[data-registry-load-more]')?.focus({preventScroll:true});
            });
        }
        // 【浏览预加载】本页应用完成后的空闲时机预取下一页数据（只进缓存，不改游标、不重建 DOM）
        scheduleRegistryPrefetch();
    }
}
// 【浏览预加载】契约：预取只写 registryAssetsQueryCache（复用 setRegistryAssetCache 的 LRU 入口），
// 1) 不推进 registryNextOffset/registryNextCursor（分页游标只在 applyRegistryAssetResponse 真正应用时推进）；
// 2) 不占用 registryRequestSeq、不干扰任何真实请求：预取拥有独立去重守卫 registryPrefetchInFlight，
//    且写入前用 registryRequestSeq 快照做新鲜度检查，筛选/排序变更后的迟到响应直接丢弃；
// 3) 不做任何 DOM 操作：命中的 loadMoreRegistryAssets 沿用既有 DOM 安全追加路径。
function prefetchRegistryNextPage(){
    if(registryPrefetchInFlight || registryLoading || registryPageLoading || !registryHasMoreAssets()) return;
    if(document.hidden) return;
    if(activeTab !== 'registry' && assetBoardById(activeTab)?.system !== false) return;
    const offset = registryNextOffset;
    const cursor = registryNextCursor;
    if(!Number.isFinite(offset) || offset < 0) return;
    const queryKey = registryQueryString({offset,cursor});
    if(registryAssetsQueryCache.has(queryKey)) return; // 已预取过，无需重复
    registryPrefetchInFlight = true;
    registryPrefetchQueryKey = queryKey;
    const requestEpoch = registryRequestSeq;
    registryPrefetchAbortController = typeof AbortController === 'function' ? new AbortController() : null;
    const signal = registryPrefetchAbortController?.signal;
    registryPrefetchPromise = apiJsonResponse(assetManagerApi.getRegistryAssets(queryKey, signal ? {signal} : {}))
        .then(data => {
            if(requestEpoch === registryRequestSeq) setRegistryAssetCache(queryKey, registryAssetPageCacheEntry(data));
        })
        .catch(() => {})
        .finally(() => {
            registryPrefetchInFlight = false;
            registryPrefetchPromise = null;
            registryPrefetchQueryKey = '';
            registryPrefetchAbortController = null;
        });
}
function scheduleRegistryPrefetch(){
    // 空闲时机调度：requestIdleCallback 优先，回退 setTimeout；重复调度幂等。
    if(registryPrefetchTimer) return;
    const run = () => {
        registryPrefetchTimer = 0;
        registryPrefetchTimerIdle = false;
        prefetchRegistryNextPage();
    };
    if(typeof requestIdleCallback === 'function'){
        registryPrefetchTimerIdle = true;
        registryPrefetchTimer = requestIdleCallback(run, {timeout: 2000});
    } else {
        registryPrefetchTimerIdle = false;
        registryPrefetchTimer = setTimeout(run, 400);
    }
}
function cancelRegistryPrefetchTimer(){
    // 取消未触发的空闲预取；进行中的请求由新鲜度检查与 loadMore 共享等待兜底，无需强行中断。
    if(!registryPrefetchTimer) return;
    if(registryPrefetchTimerIdle && typeof cancelIdleCallback === 'function') cancelIdleCallback(registryPrefetchTimer);
    else clearTimeout(registryPrefetchTimer);
    registryPrefetchTimer = 0;
    registryPrefetchTimerIdle = false;
}
function contextSentinelBusy(scrollRoot, busy){
    // 【修复浏览刷新】哨兵忙态原地同步：不整树 render() 也同步 aria-busy 与加载文案，
    // 供网格快路径与分组/列表回退路径共用，消除加载前的整树重建。
    const sentinel = scrollRoot?.querySelector?.('.registry-infinite-sentinel');
    if(!sentinel) return;
    sentinel.setAttribute('aria-busy', String(Boolean(busy)));
    sentinel.innerHTML = busy
        ? '<i data-lucide="loader-2"></i><span>正在加载更多素材…</span>'
        : '<span>继续下滑自动加载</span>';
    refreshIcons();
}
function beginRegistryBootstrapRequest(){
    const requestId = ++registryRequestSeq;
    abortRegistryAssetRequests();
    beginRegistryAssetRequest();
    registryAssetsQueryCache.clear();
    resetRegistryAssetStructures();
    // 【浏览预加载】筛选/排序/看板变更 = 新查询语义：取消未触发的空闲预取；
    // 已写入缓存的上面的 clear() 覆盖；进行中的预取响应由新鲜度检查丢弃，不会污染新查询。
    cancelRegistryPrefetchTimer();
    registryLoading = true;
    registryPageLoading = false;
    registryNextOffset = 0;
    registryNextCursor = '';
    registryPageHasMore = false;
    registryVirtualState = {key:'', start:0, end:0, cols:1, totalHeight:0, rowTops:[], rowHeights:[], itemLayouts:[]};
    return requestId;
}
async function loadRegistryBootstrap(requestId=beginRegistryBootstrapRequest()){
    const queryKey = registryQueryString();
    const signal = registryRequestAbortController?.signal;
    const assetRequest = apiJsonResponse(assetManagerApi.getRegistryAssets(queryKey, signal ? {signal} : {}));
    let secondaryRequest = null;
    try {
        const assetData = await assetRequest;
        if(requestId !== registryRequestSeq) return false;
        setRegistryAssetCache(queryKey, registryAssetPageCacheEntry(assetData));
        const prevAssetFingerprint = registryAssets.map(a => `${a.id}:${a.updated_at || a.modified_at || 0}`).join(',');
        applyRegistryAssetResponse(assetData);
        // 【QIN-584 AC2】首屏那一页列表到手即画：不再等待结构、分面、状态、预设与项目列表。
        // registryLoading 一并落下，翻页与回显才有「已加载」的准确口径。
        registryRequestAbortController = null;
        registryLoading = false;
        const newAssetFingerprint = registryAssets.map(a => `${a.id}:${a.updated_at || a.modified_at || 0}`).join(',');
        const hasMountedCards = Boolean(root.querySelector('.registry-card'));
        const assetsChanged = prevAssetFingerprint !== newAssetFingerprint || !hasMountedCards;
        if(assetsChanged && (activeTab === 'registry' || assetBoardById(activeTab)?.system === false)){
            render();
        } else {
            syncRegistryVaultFooter();
        }
        saveRegistrySnapshot();
        // 【浏览预加载】首屏应用完成后的空闲时机预取下一页数据（只进缓存，不改游标）
        scheduleRegistryPrefetch();
        // 【QIN-584 AC3】结构只查已加载页的素材 id，且放在首屏绘制之后补；
        // 到达后若卡组构成变了再同步一次（render 自带滚动位置保存与还原）。
        const structureRequest = refreshRegistryAssetStructures({requestId});
        registryStructureBootstrapPromise = structureRequest;
        const structuresChanged = await structureRequest;
        if(registryStructureBootstrapPromise === structureRequest) registryStructureBootstrapPromise = null;
        if(requestId !== registryRequestSeq) return false;
        if(structuresChanged && (activeTab === 'registry' || assetBoardById(activeTab)?.system === false)) render();
        // 【优化】次要数据并行请求，无需 500ms 强制延时
        secondaryRequest = Promise.all([
            apiJsonResponse(assetManagerApi.getRegistryFacets()),
            apiJsonResponse(assetManagerApi.getRegistryStatus()).catch(() => ({ready:false, overview:{}})),
            apiJsonResponse(assetManagerApi.getRegistryPresets()).catch(() => ({presets:[]})),
            apiJsonResponse(assetManagerApi.getRegistryProjects()).catch(() => ({projects:[]})),
        ]);
        const [facets, statusData, presetData, projectData] = await secondaryRequest;
        if(requestId !== registryRequestSeq) return false;
        registryFacets = {
            kinds:Array.isArray(facets.kinds) ? facets.kinds : [],
            categories:Array.isArray(facets.categories) ? facets.categories : [],
            tags:Array.isArray(facets.tags) ? facets.tags : [],
            collections:Array.isArray(facets.collections) ? facets.collections : [],
            roots:Array.isArray(facets.roots) ? facets.roots : [],
        };
        registryStatus = statusData && typeof statusData === 'object' ? statusData : {ready:false, overview:{}};
        registryPresets = Array.isArray(presetData.presets) ? presetData.presets : [];
        // 契约对齐：项目中心 API 的稳定实体 ID 字段为 project_id（见 docs/contracts/PROJECTS-HUB-INTERFACE-CATALOG.yaml，
        // 黄金夹具 docs/fixtures/projects-hub-list-active.json 不含 id）；归一化为内部 id，
        // 否则 activeProjectId 为 undefined、项目看板不加载、项目按钮 data-project 为空。
        projects = (Array.isArray(projectData.projects) ? projectData.projects : [])
            .map(item => ({...item, id:item.id || item.project_id}));
        for(const proj of projects){
            if(typeof proj.asset_count === 'number'){
                registryProjectAssetCounts.set(proj.id, proj.asset_count);
            }
        }
        scheduleRegistryProjectCounts();
        if(explicitRegistryProjectContext && !projects.some(item => item.id === activeProjectId)){
            const error = new Error('项目不存在或无权访问');
            error.status = 404;
            error.code = 'PROJECT_NOT_FOUND';
            error.detail = {code:error.code, message:error.message};
            throw error;
        }
        if(!activeProjectId && projects.length) activeProjectId = projects[0].id;
        if(activeProjectId) await gwCall('organize', 'loadProjectBoard', activeProjectId, false);
        if(requestId !== registryRequestSeq) return false;
        if(activeTab === 'registry' || assetBoardById(activeTab)?.system === false){
            if(!root.querySelector('.registry-card')){
                render();
            } else {
                renderBoardNavigation();
                renderAssetTreeBreadcrumb();
                syncRegistryVaultFooter();
                // 【QIN-580】嵌入态同样要在 facets 到达后刷新概览：首屏渲染时 facets 还没回来，
                // 分类计数与「资产总量」扣除 LUT 的口径都要等这一轮才算得对（否则永远停在未扣 LUT 的旧值）。
                // 只做概览的局部 outerHTML，不整树 render()，不影响 QIN-573 的缩略图直出与滚动位置。
                // 注意容器类名是 .registry-overview-shell（renderRegistryOverview 的输出根），不是 .registry-overview。
                const overview = root.querySelector('.registry-overview-shell');
                if(overview){
                    const overviewMarkup = renderRegistryOverview();
                    if(overview.outerHTML !== overviewMarkup) overview.outerHTML = overviewMarkup;
                }
            }
        }
        registryBootstrapCompleted = true; // 引导完成，后续 asset-sync 走轻量增量而非重复引导
        saveRegistrySnapshot();
        return true;
    } catch(err) {
        if(secondaryRequest) void secondaryRequest.catch(() => {});
        if(requestId === registryRequestSeq) registryLoading = false;
        if(requestId === registryRequestSeq) registryRequestAbortController = null;
        if(requestId === registryRequestSeq && isRegistryAbortError(err)) return false;
        throw err;
    }
}
function registryManagerViewMounted(){
    // 当前挂载视图是否与 activeTab 匹配的注册表家族视图（注册表页/自定义看板）。
    // 跨页签调用（如全局搜索切到注册表页）时返回 false，交由调用方整树渲染。
    if(activeTab === 'registry') return Boolean(root.querySelector('.registry-nav'));
    const board = assetBoardById(activeTab);
    if(board?.system === false) return Boolean(root.querySelector('.library-board-nav'));
    return false;
}
function syncRegistrySelectionViews(keepDetailScroll=false){
    // 【修复浏览刷新】选中态+详情面板的就地轻量同步（syncRegistryCardsInPlace 模式）：
    // 视图未挂载或网格无卡片时返回 false，由调用方决定是否整树 render()。
    if(!registryManagerViewMounted()) return false;
    if(!root.querySelector('[data-registry-card]')) return false;
    syncRegistryCardsInPlace();
    gwCall('preview', 'refreshRegistryDetailPanelDom', keepDetailScroll);
    return true;
}
async function openRegistryAssetFromGlobalSearch(assetId, startTime = null){
    const id = touchbarStableId(assetId);
    if(!id) return;
    activeTab = 'registry';
    registryQuery = '';
    registryDetailPanelCollapsed = false;
    await refreshRegistryAssets(false);
    await gwCall('preview', 'openRegistryAsset', id);
    if(startTime != null && Number.isFinite(Number(startTime))){
        gwCall('preview', 'showDetailPreview', 'registry', id, { startTime: Number(startTime) });
    }
}
function registryNavFolderActive(){
    // 【QIN-527】看板态以看板自身的来源/目录选择为准，机架与右网格共用同一口径
    const board = assetRackBoard();
    if(board) return Boolean(boardSourceSelection[board.id] || boardFolderSelection[board.id]);
    return Boolean(registryNavFolder.rootId || registryNavFolder.path);
}
function registryNavFolderMatches(item={}){
    // 看板态由 customAssetLibraryItems 完成来源/目录收敛，此处不再二次叠加
    if(assetRackBoard()) return true;
    if(!registryNavFolderActive()) return true;
    if(registryNavFolder.rootId && String(item.storage_root_id || '') !== registryNavFolder.rootId) return false;
    return registryAssetMatchesFolder(item, registryNavFolder.path);
}
function registryNavFolderLabel(){
    const board = assetRackBoard();
    if(board){
        const rootName = assetRackBoardRoots(board).find(root => root.id === String(boardSourceSelection[board.id] || ''))?.name || '';
        const path = String(boardFolderSelection[board.id] || '');
        const folderName = path === '__root__' ? '根目录' : (path.split('/').filter(Boolean).pop() || '');
        return [rootName, folderName].filter(Boolean).join(' / ');
    }
    if(!registryNavFolderActive()) return '';
    const rootName = (registryFacets.roots || []).find(root => String(root.id || '') === registryNavFolder.rootId)?.name || '';
    const folderName = registryNavFolder.path.split('/').filter(Boolean).pop() || '';
    return [rootName, folderName].filter(Boolean).join(' / ') || rootName;
}
function registryProjectEntityMatches(asset){
    // AC2：树点分组/条目后，右侧素材按 QIN-543 entities[].assets 收敛（前端过滤）
    if(!registryProjectEntityFilter) return true;
    const filter = registryProjectEntityFilter;
    const detail = assetTreeProjectDetail(filter.projectId);
    if(!detail) return true;
    const ids = filter.entityId
        ? assetTreeEntityAssetIds(detail, entity => String(entity.id || '') === String(filter.entityId))
        : assetTreeEntityAssetIds(detail, entity => filter.entityTypes?.has(String(entity.entity_type || '')) || false);
    return ids.has(String(asset.id || ''));
}
function assetTreeBreadcrumbTrail(){
    // AC7：右侧顶部面包屑层级链，每级可点击（data-asset-tree-crumb=对应树节点 key）
    const trail = [{key:'registry', label:'资产库'}];
    const board = assetBoardById(activeTab);
    if(registryProjectEntityFilter?.entityId){
        const detail = assetTreeProjectDetail(registryProjectEntityFilter.projectId);
        const entity = (detail?.entities || []).find(item => String(item.id) === String(registryProjectEntityFilter.entityId));
        const project = projects.find(item => String(item.id) === String(registryProjectEntityFilter.projectId));
        if(project) trail.push({key:`system:projects:${project.id}`, label:project.name});
        if(entity) trail.push({key:`system:projects:${registryProjectEntityFilter.projectId}:group:${entity.entity_type}:entity:${entity.id}`, label:entity.name || entity.code || '条目'});
        return trail;
    }
    if((registryProjectEntityFilter?.entityTypes?.size || 0) || registryProjectFilterId){
        const project = projects.find(item => String(item.id) === String(registryProjectFilterId));
        if(project) trail.push({key:`system:crumbs:project`, label:project.name});
        return trail;
    }
    if(registryNavFolderActive()){
        const root = (registryFacets.roots || []).find(item => String(item.id) === String(registryNavFolder.rootId));
        if(root) trail.push({key:`mine:root:${root.id}`, label:root.name});
        const segments = String(registryNavFolder.path || '').split('/').filter(Boolean);
        let path = '';
        segments.forEach(segment => {
            path = path ? `${path}/${segment}` : segment;
            trail.push({key:`mine:root:${registryNavFolder.rootId}::${path}`, label:segment});
        });
        return trail;
    }
    if(board && activeTab !== 'registry') trail.push({key:`system:${board.id}`, label:board.name});
    return trail;
}
function renderAssetTreeBreadcrumb(){
    // AC7：更新右侧顶部面包屑（embeds/asset-manager.html 预留的 .asset-breadcrumbs 壳）
    const holder = document.querySelector('.asset-breadcrumbs .asset-heading-row');
    if(!holder) return;
    const trail = assetTreeBreadcrumbTrail();
    holder.innerHTML = trail.map((item, index) => {
        const last = index === trail.length - 1;
        return `<button class="asset-tree-crumb ${last ? 'is-current' : ''}" type="button" data-asset-tree-crumb="${escapeAttr(item.key)}" ${last ? 'aria-current="location"' : ''}>${escapeHtml(item.label)}</button>`;
    }).join('');
}
function handleAssetTreeCrumbClick(key){
    // 每级可点击：回退到对应层级（清空更深层的过滤态）
    const value = String(key || '');
    if(!value || value === 'registry'){
        // 根级「资产库」：清空项目/条目/目录过滤，回总览
        registryProjectFilterId = '';
        registryProjectEntityFilter = null;
        registryNavFolder = {rootId:'', path:''};
        registryRootId = '';
        activeTab = 'registry';
        assetTreeSelectedKey = '';
        render();
        void refreshRegistryAssets(false).then(render, render);
        return;
    }
    if(value.startsWith('system:crumbs:project')){
        // 项目级：保留项目过滤、清条目过滤
        registryProjectEntityFilter = null;
        void refreshRegistryAssetsFromRack().catch(() => null);
        render();
        return;
    }
    if(value.startsWith('mine:root:')){
        const body = value.slice('mine:root:'.length);
        const separator = body.indexOf('::');
        const rootId = separator > 0 ? body.slice(0, separator) : body;
        const path = separator > 0 ? body.slice(separator + 2) : '';
        gwCall('settings', 'selectAssetRackFolder', rootId, path);
        return;
    }
    selectAssetTreeNode(value);
}
function focusAssetTreeSelectedRow(){
    // render 重建 DOM 后焦点随旧节点丢失；下一帧把焦点挪回新树的选中行，保证连续键盘导航
    requestAnimationFrame(() => {
        const key = String(assetTreeSelectedKey || '');
        if(!key) return;
        const fresh = root.querySelector(`.asset-tree-panel [data-asset-tree-node="${CSS.escape(key)}"]`);
        fresh?.focus();
    });
}
function assetTreeDomNodes(){
    // 键盘导航按 DOM 顺序遍历（与视觉顺序一致），不重算树逻辑
    return [...root.querySelectorAll('.asset-tree-panel .asset-tree-row')];
}
function moveAssetTreeSelection(delta){
    const nodes = assetTreeDomNodes();
    if(!nodes.length) return;
    const currentIndex = nodes.findIndex(node => node.dataset.assetTreeNode === assetTreeSelectedKey);
    const nextIndex = currentIndex < 0 ? (delta > 0 ? 0 : nodes.length - 1) : Math.min(nodes.length - 1, Math.max(0, currentIndex + delta));
    const next = nodes[nextIndex];
    if(next) selectAssetTreeNode(next.dataset.assetTreeNode || '');
    focusAssetTreeSelectedRow();
}
function handleAssetTreeKeydown(event){
    // AC7：键盘上下键切换、右键展开、左键折叠、回车展开或进入；树内按键不向下穿透
    const panel = event.target?.closest?.('.asset-tree-panel');
    if(!panel) return;
    const row = event.target.closest('.asset-tree-row');
    const key = row?.dataset.assetTreeNode || assetTreeSelectedKey;
    if(event.key === 'ArrowDown'){ event.preventDefault(); event.stopPropagation(); moveAssetTreeSelection(1); }
    else if(event.key === 'ArrowUp'){ event.preventDefault(); event.stopPropagation(); moveAssetTreeSelection(-1); }
    else if(event.key === 'ArrowRight'){ event.preventDefault(); event.stopPropagation(); if(key) toggleAssetTreeNode(key); }
    else if(event.key === 'ArrowLeft'){ event.preventDefault(); event.stopPropagation(); if(key){ assetTreeExpanded.delete(key); render(); } }
    else if(event.key === 'Enter'){ event.preventDefault(); event.stopPropagation(); if(key){ selectAssetTreeNode(key); focusAssetTreeSelectedRow(); } }
}
function registryVisibleAssets(){
    // 【QIN-524】目录层级选中的物理目录即时收敛右网格（仅对已加载分页生效，页脚区分总库与当前显示）
    // 【QIN-544】树点分组/条目后按 QIN-543 条目素材集合收敛
    return registryVisibleAssetList(registryAssets).filter(registryNavFolderMatches).filter(registryProjectEntityMatches);
}
function registryVisibleAssetList(items=[]){
    // Kept as a compatibility path for persisted filter definitions; stacking
    // is disabled at state level and therefore this branch is never active.
    if(!registryGroupSimilar || registryHasMoreAssets()) return items;
    const result = [];
    const groups = new Map();
    items.forEach(item => {
        const key = item.similar_key || item.id;
        if(!groups.has(key)) groups.set(key, []);
        groups.get(key).push(item);
    });
    groups.forEach((items, key) => {
        result.push(items[0]);
        if(items.length > 1 && registryExpandedStacks.has(key)) result.push(...items.slice(1));
    });
    return result;
}
function activeRegistryVisibleAssets(){
    return registryDisplayedItems(assetBoardById(activeTab));
}
function selectedRegistryCustomGroupIds(board=assetBoardById(activeTab)){
    const selectedIds = new Set(registryActiveSelectedIds(board));
    if(!selectedIds.size) return [];
    return registryBoardCustomGroups(board)
        .filter(group => (group.assetIds || []).some(id => selectedIds.has(id)))
        .map(group => group.id);
}
function mergeRegistryAssetUpdate(updated={}){
    if(!updated?.id) return;
    const normalized = normalizeRegistryAsset(updated);
    registryAssets = registryAssets.map(item => item.id === normalized.id ? {...item,...normalized} : item);
    if(selectedRegistryAssetId === normalized.id) registryDetail = normalized;
    if(detailViewerState.open && detailViewerState.source === 'registry'){
        const index = detailViewerState.items.findIndex(item => item.id === normalized.id);
        if(index >= 0) detailViewerState.items[index] = {...detailViewerState.items[index], ...normalized};
    }
}
async function refreshRegistryFacetData(){
    const facets = await apiJsonResponse(assetManagerApi.getRegistryFacets()).catch(() => null);
    if(!facets) return;
    registryFacets = {
        kinds:Array.isArray(facets.kinds) ? facets.kinds : registryFacets.kinds,
        categories:Array.isArray(facets.categories) ? facets.categories : registryFacets.categories,
        tags:Array.isArray(facets.tags) ? facets.tags : registryFacets.tags,
        collections:Array.isArray(facets.collections) ? facets.collections : registryFacets.collections,
        roots:Array.isArray(facets.roots) ? facets.roots : registryFacets.roots,
    };
}
function registryStatusJobKey(status=registryStatus){
    const job = status?.overview?.active_job || status?.overview?.last_job || {};
    return job.id ? `${job.id}:${job.status || ''}:${job.finished_at || ''}` : '';
}
function registryStatusRootTotal(status=registryStatus){
    return (status?.overview?.roots || []).reduce((total,root) => total+Number(root.asset_count || 0),0);
}
async function pollRegistryAutomaticIndex(){
    if(registryAutoPollBusy || document.hidden) return;
    registryAutoPollBusy = true;
    try {
        const previousStatus = registryStatus;
        const status = await apiJsonResponse(assetManagerApi.getRegistryStatus()).catch(() => null);
        if(!status) return;
        const job = status?.overview?.active_job || status?.overview?.last_job || {};
        const nextKey = registryStatusJobKey(status);
        const changed = nextKey && nextKey !== registryObservedJobKey;
        registryStatus = status;
        if(changed) registryObservedJobKey = nextKey;
        const completedChange = changed && ['completed','failed'].includes(String(job.status || ''));
        const assetChanged = Number(job.indexed_count || 0) > 0 || registryStatusRootTotal(status) !== registryStatusRootTotal(previousStatus);
        // 【修复问题2→跳顶终修】任务完成即静默对账：已显示素材原地合并、新素材进待看提示，
        // 网格顺序与滚动位置永不被自动重排（对账本身对预览/浏览无干扰，无需交互期延迟）。
        if(completedChange && assetChanged){
            await applyRegistryIndexReconcile(job);
        }
    } finally {
        registryAutoPollBusy = false;
    }
}
async function reconcileRegistryIndexChanges(){
    // 轻量对账（契约：后台刷新保留现有网格）：
    // - 已显示素材的原地更新（标签/元数据/时间戳）静默合并，不打乱顺序、不重渲染；
    // - 新增素材进入待看缓冲，以"新素材提示"呈现，由用户点击后显式刷新；
    // - 已显示素材从当前页消失同样只记录，不在浏览中抽卡。
    const queryKey = registryQueryString();
    const data = await apiJsonResponse(assetManagerApi.getRegistryAssets(queryKey)).catch(() => null);
    if(!data) return false;
    setRegistryAssetCache(queryKey, registryAssetPageCacheEntry(data));
    const incoming = (Array.isArray(data.items) ? data.items : []).map(normalizeRegistryAsset);
    const incomingById = new Map(incoming.map(item => [item.id, item]));
    const displayedIds = new Set(registryAssets.map(item => item.id));
    let metadataChanged = false;
    registryAssets = registryAssets.map(item => {
        const fresh = incomingById.get(item.id);
        if(!fresh) return item;
        const before = registryDisplayedItemKey(item);
        const after = registryDisplayedItemKey(fresh);
        if(before !== after) metadataChanged = true;
        return {...item, ...fresh};
    });
    const newIds = [];
    incoming.forEach(item => { if(!displayedIds.has(item.id)) newIds.push(item.id); });
    const vanishedIds = [...registryReconcilePageIds].filter(id => !incomingById.has(id) && displayedIds.has(id));
    if(newIds.length || vanishedIds.length){
        registryPendingNewIds = new Set([...(registryPendingNewIds || new Set()), ...newIds]);
        registryPendingVanishedIds = new Set([...(registryPendingVanishedIds || new Set()), ...vanishedIds]);
        // 暂存新素材完整对象（已 normalize）供 applyPendingRegistryAssets 前插复用；
        // 本函数保持"对账核心不前插网格"契约：这里只写数据缓冲，不做任何网格 DOM 操作。
        newIds.forEach(id => { const item = incomingById.get(id); if(item) registryPendingNewItems.set(id, item); });
        renderRegistryFreshNotice();
    }
    registryTotalFromResponse(data);
    registryReconcilePageIds = new Set(incoming.map(item => item.id));
    if(selectedRegistryAssetId && incomingById.has(selectedRegistryAssetId)){
        registryDetail = registryDetail ? {...registryDetail, ...incomingById.get(selectedRegistryAssetId)} : {...incomingById.get(selectedRegistryAssetId)};
    }
    if(detailViewerState.open && detailViewerState.source === 'registry'){
        detailViewerState.items = detailViewerState.items.map(item => incomingById.has(item.id) ? {...item, ...incomingById.get(item.id)} : item);
    }
    return metadataChanged;
}
function registryDisplayedItemKey(item={}){
    // 仅以卡片可见字段判定是否需要原地同步，避免内部索引字段触发无意义操作。
    return JSON.stringify({
        id:item.id || '',
        name:item.name || '',
        url:item.url || item.public_url || '',
        created_at:Number(item.created_at || 0),
        updated_at:Number(item.updated_at || 0),
        indexed_at:Number(item.indexed_at || 0),
        size:Number(item.size || 0),
        kind:item.kind || item.type || '',
        source_names:registrySourceNames(item),
        tags:(Array.isArray(item.tags) ? item.tags : []).map(tag => ({
            id:typeof tag === 'object' ? tag.id || '' : '',
            name:typeof tag === 'object' ? tag.name || tag.tag || '' : String(tag || ''),
        })),
    });
}
function registryPendingFreshCount(){
    return (registryPendingNewIds ? registryPendingNewIds.size : 0) + (registryPendingVanishedIds ? registryPendingVanishedIds.size : 0);
}
function renderRegistryFreshNotice(){
    // 顶部待看提示：由用户决定何时刷新，绝不自动重排网格
    const host = document.querySelector('[data-registry-fresh-notice]');
    if(!host){
        if(registryPendingFreshCount()) setStatus(registryPendingFreshNoticeText());
        return;
    }
    // 【QIN-518】宿主常驻：胶囊以绝对定位浮在网格右上角，出现/更新/消失都不撑开文档流；
    // 不再 remove() 宿主，避免提示条消失后整树渲染前永久失效。
    if(!registryPendingFreshCount()){ host.innerHTML = ''; return; }
    host.innerHTML = `<button class="hw-btn registry-fresh-capsule" type="button" data-registry-fresh-apply><i data-lucide="refresh-cw"></i><span>${escapeHtml(registryPendingFreshNoticeText())}</span></button>`;
    refreshIcons();
}
function registryPendingFreshNoticeText(){
    const fresh = registryPendingNewIds ? registryPendingNewIds.size : 0;
    const gone = registryPendingVanishedIds ? registryPendingVanishedIds.size : 0;
    if(fresh && gone) return `有 ${fresh} 个新素材、${gone} 个素材不可见，点击刷新网格`;
    if(fresh) return `有 ${fresh} 个新素材，点击刷新网格`;
    return `${gone} 个素材已不可见，点击刷新网格`;
}
async function applyRegistryIndexReconcile(job){
    // 增量更新：静默合并已显示素材的元数据并刷新 facet 统计；网格顺序与滚动位置保持不变
    const metadataChanged = await reconcileRegistryIndexChanges();
    await refreshRegistryFacetData();
    if(metadataChanged && (activeTab === 'registry' || assetBoardById(activeTab)?.system === false)){
        // 原地更新（如新增标签）不打乱顺序：仅同步当前 DOM 的轻量字段，不整树重建
        syncRegistryCardsInPlace();
    }
    if(job?.status === 'completed') setStatus(`已自动索引 ${Number(job.indexed_count || 0)} 个新增或变更文件`);
}
function syncRegistryCardsInPlace(){
    // 只同步现有卡片的可见 DOM；卡片身份和网格节点保持不变，避免滚动位置漂移。
    let previewChanged = false;
    let iconsChanged = false;
    document.querySelectorAll('[data-registry-card]').forEach(card => {
        const item = registryAssets.find(value => value.id === card.dataset.registryCard);
        if(!item) return;
        [...card.classList].forEach(className => {
            if(className.startsWith('asset-type-')) card.classList.remove(className);
        });
        card.classList.add(`asset-type-${registryVisualType(item)}`);
        card.classList.toggle('active', item.id === selectedRegistryAssetId);
        card.classList.toggle('selected', registrySelectedIds.has(item.id));
        card.setAttribute('aria-label', `查看资产 ${item.name || 'asset'}`);
        const nameNode = card.querySelector('[data-registry-card-name]');
        if(nameNode){
            const name = item.name || 'asset';
            if(nameNode.textContent !== name) nameNode.textContent = name;
            nameNode.setAttribute('title', name);
        }
        const checkbox = card.querySelector('[data-registry-check]');
        if(checkbox){
            checkbox.checked = registrySelectedIds.has(item.id);
            checkbox.setAttribute('aria-label', `选择 ${item.name || 'asset'}`);
        }
        const formatNode = card.querySelector('.registry-card-format');
        if(formatNode){
            const format = registryAssetExtension(item).toUpperCase() || 'FILE';
            formatNode.textContent = format;
            formatNode.setAttribute('aria-label', `文件格式 ${format}`);
        }
        const mediaMeta = card.querySelector('.asset-card-meta');
        if(mediaMeta){
            const values = mediaMeta.querySelectorAll('span');
            if(values[0]) values[0].textContent = registryAssetTypeLabel(item);
            if(values[1]) values[1].textContent = formatDate(item.created_at);
        }
        const sourceNode = card.querySelector('.registry-card-source');
        if(sourceNode){
            const names = registrySourceNames(item);
            const label = names.join(' · ');
            const labelNode = sourceNode.querySelector(':scope > span');
            const countNode = sourceNode.querySelector(':scope > em');
            sourceNode.title = label;
            if(labelNode?.textContent !== label || Boolean(countNode) !== (names.length > 1)){
                sourceNode.innerHTML = `<i data-lucide="${names.length > 1 ? 'database-zap' : 'database'}"></i><span>${escapeHtml(label)}</span>${names.length > 1 ? `<em>${names.length} 库</em>` : ''}`;
                iconsChanged = true;
            }
        }
        const listTypeNode = card.querySelector('.registry-list-type');
        if(listTypeNode){
            const label = registryAssetTypeLabel(item);
            if(listTypeNode.textContent.trim() !== label){
                listTypeNode.innerHTML = `<i data-lucide="${escapeAttr(registryAssetTypeIcon(item))}"></i>${escapeHtml(label)}`;
                iconsChanged = true;
            }
        }
        const listCells = [...card.children];
        if(card.classList.contains('registry-list-row')){
            if(listCells[3]) listCells[3].textContent = formatFileSize(item.size || 0);
            if(listCells[4]) listCells[4].textContent = registryModifiedLabel(item);
            const tagsNode = card.querySelector('.registry-list-tags');
            if(tagsNode){
                const tags = Array.isArray(item.tags) ? item.tags : [];
                const visibleTags = tags.slice(0, 4);
                tagsNode.innerHTML = `${visibleTags.map(tag => `<em>${escapeHtml(tag.name || tag.tag || String(tag))}</em>`).join('') || '<small>—</small>'}${tags.length > visibleTags.length ? `<small>+${tags.length - visibleTags.length}</small>` : ''}`;
            }
            const pathNode = card.querySelector('.registry-list-path');
            if(pathNode){
                const names = registrySourceNames(item);
                pathNode.title = `${names.join(' · ')} / ${registrySourcePath(item)}`;
                const pathLabel = pathNode.querySelector(':scope > span');
                if(pathLabel) pathLabel.textContent = names.join(' · ');
            }
        }
        const thumb = card.querySelector('.asset-thumb, .registry-list-thumb');
        if(thumb){
            const nextTemplate = document.createElement('template');
            nextTemplate.innerHTML = `<div>${deferRegistryPreviewThumb(assetThumb(item))}</div>`;
            const nextMedia = nextTemplate.content.firstElementChild;
            const currentSources = [...thumb.children]
                .filter(node => !node.classList.contains('registry-card-selection-overlay'))
                .map(node => node.dataset?.registryPreviewSrc || node.getAttribute('src') || node.textContent || '');
            const nextSources = nextMedia ? [...nextMedia.children].map(node => node.dataset?.registryPreviewSrc || node.getAttribute('src') || node.textContent || '') : [];
            const nextAspect = registryMediaAspectStyle(item);
            if(nextAspect) thumb.setAttribute('style', nextAspect); else thumb.removeAttribute('style');
            if(currentSources.join('\u001f') !== nextSources.join('\u001f')){
                const overlay = thumb.querySelector('.registry-card-selection-overlay');
                [...thumb.children].forEach(node => { if(node !== overlay) node.remove(); });
                if(nextMedia){
                    [...nextMedia.children].forEach(node => {
                        if(node.classList.contains('registry-card-selection-overlay')) return;
                        thumb.insertBefore(document.importNode(node, true), overlay || null);
                    });
                }
                previewChanged = true;
            }
        }
    });
    if(previewChanged) scheduleRegistryPreviewImages();
    if(iconsChanged) refreshIcons();
}
function registrySyncDomContext(){
    // 仅当注册表标签页（或自定义看板）处于前台且网格中已有卡片时，才允许 DOM 前插；
    // 其他标签页/空结果占位返回 null，由调用方退化为“只合并数据模型”。
    const board = assetBoardById(activeTab);
    if(activeTab !== 'registry' && board?.system !== false) return null;
    const scrollRoot = root.querySelector('.registry-assets-scroll');
    if(!scrollRoot) return null;
    const firstCard = scrollRoot.querySelector('[data-registry-card]');
    if(!firstCard) return null;
    return {scrollRoot, firstCard};
}
function mergePendingRegistryAssetsIntoModel(){
    // 数据层前插：新素材(去重后)拼接原 registryAssets 头部，保证 asset_id 唯一；
    // registryTotal 已由对账更新，这里不重复累加。
    const newItems = [];
    registryPendingNewIds.forEach(id => {
        const item = registryPendingNewItems.get(id);
        if(item) newItems.push(item);
    });
    const existingIds = new Set(registryAssets.map(item => item.id));
    const deduped = newItems.filter(item => !existingIds.has(item.id));
    if(deduped.length){
        registryAssets = [...deduped, ...registryAssets];
        // 头部插入使既有素材整体后移，同步校正键盘焦点索引，避免焦点漂移到别的卡片
        if(registryFocusIndex >= 0) registryFocusIndex += deduped.length;
    }
    return deduped;
}
function insertPendingRegistryCards(newItems=[], context){
    // DOM 层前插：只为“当前显示列表会包含”的新素材建卡（自定义看板有类型/来源过滤），
    // 复用既有卡片模板 insertBefore 到当前第一张卡片之前；严禁整体 render()、严禁替换既有卡片节点。
    if(!newItems.length || !context) return;
    const displayIds = new Set(activeRegistryVisibleAssets().map(item => item.id));
    const domIds = new Set([...context.scrollRoot.querySelectorAll('[data-registry-card]')].map(node => node.dataset.registryCard || ''));
    const container = context.firstCard.parentNode;
    if(!container) return;
    let anchor = context.firstCard;
    let insertedAny = false;
    newItems.forEach(item => {
        if(!displayIds.has(item.id) || domIds.has(item.id)) return;
        const markup = (registryLayout === 'list' ? renderRegistryListRow(item) : renderRegistryCard(item)).trim();
        const template = document.createElement('template');
        template.innerHTML = markup;
        const node = template.content.firstElementChild;
        if(!node) return;
        container.insertBefore(node, anchor);
        anchor = node;
        domIds.add(item.id);
        insertedAny = true;
    });
    if(!insertedAny) return;
    // 与现有渲染一致的后处理：图标、masonry span、懒加载预览观察；不动滚动位置、不抢焦点
    refreshIcons();
    const bootSkeleton = document.getElementById('assetManagerBootSkeleton');
    if(bootSkeleton) bootSkeleton.hidden = true;
    scheduleRegistryMasonryLayout();
    scheduleRegistryPreviewImages();
}
function registrySyncRefreshReconcileBaseline(appliedIds=[]){
    // 前插完成后把已应用的新素材并入对账基线（新素材按当前排序位于对账第一页内），
    // 避免下一轮对账把它们误判为“消失”；既有基线保持不动，不纳入未加载的后续页 id。
    if(!appliedIds.length) return;
    registryReconcilePageIds = new Set([...registryReconcilePageIds, ...appliedIds]);
}
function registrySyncApplyScrollCleanup(){
    if(registryPendingApplyScrollBound){
        document.removeEventListener('scroll', registryPendingApplyScrollBound, true);
        registryPendingApplyScrollBound = null;
    }
}
function registrySyncBindDeferredApply(){
    // 用户正在浏览下方内容（滚动容器离开顶部）：暂不插入以免顶走当前视口内容，
    // 保留“新素材”提示，注册一次性滚动监听——用户滚回顶部时自动补插，无需点击。
    if(registryPendingApplyScrollBound) return;
    registryPendingApplyScrollBound = event => {
        const target = event.target?.closest?.('.registry-assets-scroll');
        if(!target || target.scrollTop > 4) return;
        registrySyncApplyScrollCleanup();
        applyPendingRegistryAssets();
    };
    document.addEventListener('scroll', registryPendingApplyScrollBound, true);
}
function applyPendingRegistryAssets(){
    // 消费待看缓冲：新素材自动新增进已加载列表，且绝不刷新/替换/重排已加载内容。
    if(!registryPendingNewIds.size){
        registrySyncApplyScrollCleanup();
        return;
    }
    const context = registrySyncDomContext();
    if(!context){
        // 不在注册表标签页/看板视图：只更新数据模型，清空新素材缓冲避免重复提示，
        // 待用户切回该标签页的下一次 render() 自然包含；消失素材提示按现状保留。
        mergePendingRegistryAssetsIntoModel();
        const appliedIds = [...registryPendingNewIds];
        registryPendingNewItems.clear();
        registryPendingNewIds = new Set();
        registrySyncRefreshReconcileBaseline(appliedIds);
        renderRegistryFreshNotice();
        return;
    }
    if(context.scrollRoot.scrollTop > 4){
        registrySyncBindDeferredApply();
        return;
    }
    // 视口在顶部：立即合并数据模型并前插卡片
    registrySyncApplyScrollCleanup();
    const appliedIds = [...registryPendingNewIds];
    const deduped = mergePendingRegistryAssetsIntoModel();
    registryPendingNewItems.clear();
    registryPendingNewIds = new Set();
    insertPendingRegistryCards(deduped, context);
    registrySyncRefreshReconcileBaseline(appliedIds);
    // 提示同步：新素材已应用即从提示移除；仍有消失素材则保留其提示（消失语义=现状，仅缓冲不抽卡）
    renderRegistryFreshNotice();
}
async function syncRegistryOnReveal(){
    // 壳层切回资产页签（asset-sync）=轻量增量同步：对账差异 + 新素材自动前插，
    // 不重拉全量数据、不清空选择/分页/筛选；全量刷新仍归 asset-refresh / [data-refresh-assets]。
    if(registrySyncBusy) return;
    if(registryLoading) return; // 引导进行中，勿叠加请求
    if(!registryAssets.length && !registryBootstrapCompleted){
        await loadAll(); // 兜底首次进入
        return;
    }
    registrySyncBusy = true;
    try {
        const pendingBefore = registryPendingFreshCount();
        const metadataChanged = await reconcileRegistryIndexChanges();
        // 发现新素材/消失素材（待看计数变化）或已显示素材元数据变化时，同步刷新 facet 统计
        if(registryPendingFreshCount() !== pendingBefore || metadataChanged){
            await refreshRegistryFacetData();
        }
        await applyPendingRegistryAssets();
    } finally {
        registrySyncBusy = false;
    }
}
function startRegistryAutomaticIndexPolling(){
    clearInterval(registryAutoPollTimer);
    registryObservedJobKey = registryStatusJobKey();
    registryAutoPollTimer = window.setInterval(() => pollRegistryAutomaticIndex().catch(() => {}),5000);
    clearInterval(inboxBadgeTimer);
    // 【QIN-547】收集箱角标：不在收集箱视图时低频刷新未读数，新素材到达即更新角标。
    inboxBadgeTimer = window.setInterval(async () => {
        if(document.hidden) return;
        // 【QIN-588 AC5】停留收集箱时同样低频回读：外部新落到收集箱的文件在下一个
        // 30s 窗口内自动出现；签名无变化不重绘，重绘前记录滚动位置、重绘后恢复，
        // 不重置滚动与选择。非收集箱视图保持 QIN-547 角标逻辑不变。
        if(activeTab === 'inbox'){
            const before = assetInboxItemsSignature();
            await gwCall('inbox', 'loadAssetInbox');
            if(activeTab !== 'inbox') return;
            if(assetInboxItemsSignature() !== before){
                const scroller = root.querySelector('.inbox-content .content-scroll');
                const scrollTop = scroller ? scroller.scrollTop : 0;
                render();
                const refreshed = root.querySelector('.inbox-content .content-scroll');
                if(refreshed && scrollTop > 0) refreshed.scrollTop = scrollTop;
            }
            return;
        }
        const before = assetInboxUnreadCount();
        await gwCall('inbox', 'loadAssetInbox');
        if(assetInboxUnreadCount() !== before) render();
    },30000);
}
function parseRegistryTagNames(value=''){
    return [...new Set(String(value || '').split(/[,，;；\n]+/).map(tag => tag.trim()).filter(Boolean))].slice(0, 30);
}
// 【QIN-534】允许显式传入素材 id 集合：底部「建立关联」沿用当前选择，
// 右键菜单的「合并为关联结构」直接使用菜单捕获的 id，避免菜单与选择集不同步。
async function relateSelectedRegistryAssets(assetIds=[]){
    const explicit = [...new Set((assetIds || []).map(value => String(value || '').trim()).filter(Boolean))];
    const ids = explicit.length ? explicit : registryActiveSelectedIds(assetBoardById(activeTab));
    if(ids.length < 2){ setStatus('至少选择两个素材才能建立关联'); return; }
    const data = await apiJsonResponse(assetManagerApi.relateRegistryAssets({asset_ids:ids,relation_type:'related'}));
    (data.assets || []).forEach(mergeRegistryAssetUpdate);
    // 【QIN-534 反馈 4】建立关联后立刻把服务端返回的关系对端写回本地状态，
    // 否则菜单判不出「已关联」，会一直停在「合并为关联结构」。
    const anchor = ids[0];
    const relations = Array.isArray(data?.relations) ? data.relations : [];
    const relatedTargets = relations
        .filter(relation => String(relation?.from_asset_id || '') === anchor)
        .map(relation => String(relation?.to_asset_id || '').trim())
        .filter(Boolean);
    if(relatedTargets.length){
        const anchorItem = registryAssets.find(item => String(item.id || '') === anchor);
        if(anchorItem){
            const existing = new Set(registryRelatedAssetIds(anchorItem));
            const summaries = relatedTargets
                .filter(targetId => !existing.has(targetId))
                .map(targetId => {
                    const target = registryAssets.find(item => String(item.id || '') === targetId);
                    return target
                        ? {id:target.id, asset_id:target.id, name:target.name, kind:target.kind}
                        : {id:targetId, asset_id:targetId};
                });
            if(summaries.length) anchorItem.related_assets = [...(anchorItem.related_assets || []), ...summaries];
        }
        // 双向同步：让被关联的目标素材也记录与 anchor 的关联，避免右键反向选择时出现判定盲区
        relatedTargets.forEach(targetId => {
            const targetItem = registryAssets.find(item => String(item.id || '') === targetId);
            if(targetItem){
                const existing = new Set(registryRelatedAssetIds(targetItem));
                if(!existing.has(anchor)){
                    const summary = anchorItem
                        ? {id:anchorItem.id, asset_id:anchorItem.id, name:anchorItem.name, kind:anchorItem.kind}
                        : {id:anchor, asset_id:anchor};
                    targetItem.related_assets = [...(targetItem.related_assets || []), summary];
                }
            }
        });
    }
    if(detailViewerState.open && detailViewerState.source === 'registry') gwCall('preview', 'renderDetailViewer');
    else render();
    setStatus(`已关联 ${ids.length} 个素材`);
}
function registryRemoteKind(url){
    const clean = String(url || '').toLowerCase().split('?')[0].split('#')[0];
    if(/\.(mp4|webm|mov|m4v|avi|mkv)$/.test(clean)) return 'video';
    if(/\.(mp3|wav|m4a|aac|ogg|flac)$/.test(clean)) return 'audio';
    if(/\.(json|zip)$/.test(clean)) return 'workflow';
    if(/\.(md|markdown|txt|pdf)$/.test(clean)) return 'document';
    return 'image';
}
async function createRegistryRemoteAsset(){
    const value = window.prompt('添加公网 URL 素材\n可填写“名称 + 换行 + URL”，或只填写 URL', 'https://');
    if(value === null) return;
    const lines = String(value || '').split(/\r?\n/).map(line => line.trim()).filter(Boolean);
    const urlIndex = lines.findIndex(line => /^https?:\/\//i.test(line));
    if(urlIndex < 0){ setStatus('请输入有效的 http(s) URL'); return; }
    const url = lines[urlIndex];
    const name = lines.find((line, index) => index !== urlIndex) || '';
    const data = await apiJsonResponse(assetManagerApi.createRegistryRemoteAsset({url,name,kind:registryRemoteKind(url)}));
    // 【QIN-585 AC2】写入已成功，列表回读是另一件事：回读失败不能报「已加入」，
    // 也不能被终态观察 promise 吞掉。refreshRegistryBootstrapTracked 返回原始请求，
    // 这里显式接住并提示「已写入但刷新失败」，避免旧列表 + 成功文案误导用户。
    try {
        await refreshRegistryBootstrapTracked();
    } catch(error){
        reportRegistryWriteRefreshFailure('公网 URL 已写入，但列表刷新失败', error);
        return;
    }
    selectedRegistryAssetId = data.asset?.id || selectedRegistryAssetId;
    registryDetail = data.asset ? normalizeRegistryAsset(data.asset) : null;
    render();
    setStatus('公网 URL 已加入统一资产中心');
}
async function deleteRegistryRemoteAsset(id){
    if(!id || !confirm('确认删除这个公网 URL 资产？')) return;
    await apiJsonResponse(assetManagerApi.deleteRegistryRemoteAsset(id));
    selectedRegistryAssetId = '';
    registryDetail = null;
    // 【QIN-585 AC2】同上：删除已生效，回读失败必须明确提示，不能报「已删除」。
    try {
        await refreshRegistryBootstrapTracked();
    } catch(error){
        reportRegistryWriteRefreshFailure('公网 URL 已删除，但列表刷新失败', error);
        return;
    }
    render();
    setStatus('公网 URL 资产已删除');
}
// 【QIN-585 AC2】「写入成功 / 列表回读失败」的统一提示：写入已经生效，不能冒充整体成功，
// 也不能让它退化成一条未被处理的拒绝。force 刷新是用户显式动作，不受首屏闸门影响。
function reportRegistryWriteRefreshFailure(message, error){
    const reason = error?.message || error || '列表读取失败';
    const text = `${message}：${reason}`;
    setStatus(text);
    showAssetFailure(message, error);
    return text;
}
// 【QIN-585 AC2】登记一次「替换当前列表」请求的终态（成功/失败都算终态）。
// 每次登记即开启**新一代**：新请求会 abort 旧一代，旧一代落地时不得把新一代的「在途」清掉。
// 这里刻意**分离两件事**：
//   ① 终态观察 `registryBootstrapSettled` 永不 reject，只给 waitRegistryBootstrapSettled()
//      与延后链路等待，避免 loadAll 提前 reject、允许根 void 等路径留下未处理的拒绝；
//   ② 返回值是**原始 request**，业务调用方 await 它时仍能拿到列表 500 等真实失败。
// 早期版本把 ① 当返回值，导致「写入成功但列表回读 500」被吞成成功。
function trackRegistryBootstrapSettled(request){
    registryBootstrapGeneration += 1;
    const generation = registryBootstrapGeneration;
    const settleGeneration = () => {
        // 只推进落地代际，不回退：旧代际晚于新代际落地时不能把「新一代仍在途」抹掉。
        if(generation > registryBootstrapSettledGeneration) registryBootstrapSettledGeneration = generation;
        return true;
    };
    registryBootstrapSettled = request.then(settleGeneration, settleGeneration);
    return request;
}
// 【QIN-585 AC2】无参引导（添加/删除公网 URL、允许根保存）统一走这里登记代际，
// 并把原始请求返回给业务方：写入已成功而列表回读失败时必须能明确提示，不能误报成功。
function refreshRegistryBootstrapTracked(){
    return trackRegistryBootstrapSettled(loadRegistryBootstrap());
}
function isRegistryBootstrapPending(){
    return registryBootstrapSettledGeneration < registryBootstrapGeneration;
}
async function waitRegistryBootstrapSettled(){
    // 等的是**当前代际**的列表落地。等的过程里用户刷新开了新一代，就继续等新一代：
    // 旧代际落地只代表它自己被 abort 了，此时放行等于让非首屏链路抢在新列表前面。
    for(;;){
        if(!isRegistryBootstrapPending()) return true;
        const settled = registryBootstrapSettled;
        // 当前代际还没登记过终态（首屏列表尚未开始）：没有在途请求，不阻塞。
        if(!settled) return true;
        await settled;
    }
}
async function loadAll(){
    // 【QIN-573 AC2】快照优先秒开：若存在会话快照，在网络请求到达前先行挂载渲染
    tryApplyRegistrySnapshot();
    const registryRequestId = beginRegistryBootstrapRequest();
    setStatus('加载中...');
    const registryBootstrap = loadRegistryBootstrap(registryRequestId).catch(err => {
        if(registryRequestId !== registryRequestSeq) return;
        registryLoading = false;
        registryFacets = {kinds:[], categories:[], tags:[], collections:[], roots:[]};
        // 【QIN-569 AC1】引导请求失败时落可恢复错误态标记，首屏渲染可恢复错误条而非空白列表。
        registryBootstrapError = err.message || '统一资产注册表加载失败';
        if(!registryAssets.length) setStatus(registryBootstrapError);
        render();
        // 【QIN-569 AC1】rethrow 把失败传给 loadAll 尾部的成败区分（bootstrapOk），
        // 否则 catch 吞掉拒绝后 promise 恒 fulfill，失败标记会被同轮无条件清除。
        throw err;
    });
    // 【QIN-585 AC2】登记首屏列表请求的真实终态；延后链路与治理建议都以此为准，
    // 不再拿 loadAll 的 catch（可能只是资产库/本地设置先失败）当「首屏已结束」。
    trackRegistryBootstrapSettled(registryBootstrap);
    const [assetData] = await Promise.all([
        apiJsonResponse(assetManagerApi.getAssetLibrary()),
        loadLocalStorageSettings(),
        // 本地目录可能包含大量文件，只有进入本地素材页时再扫描。
        // 共享目录同样按需读取，避免首屏被无关文件系统请求阻塞。
    ]);
    if(registryRequestId !== registryRequestSeq) return;
    assetLibrary = assetData.library || {libraries:[], categories:[]};
    promptLibrary = {libraries:[]};
    apiProviders = [];
    canvasAssetsData = {categories:[], canvases:[], items:[]};
    // 刷新时默认回到「默认资产库」
    const libs = assetLibraries();
    activeAssetLibraryId = (libs.find(lib => lib.id === 'default') || libs[0])?.id || '';
    activeWorkflowLibraryId = workflowLibraryId(libs.find(lib => workflowLibraryId(lib) === 'default') || libs[0]);
    activeAssetCategoryId = '';
    activeWorkflowCategoryId = '';
    selectedAssetId = '';
    selectedWorkflowId = '';
    selectedAssetIds.clear();
    selectedWorkflowIds.clear();
    selectedPromptIds.clear();
    selectedCanvasAssetIds.clear();
    // The registry bootstrap owns the first card render. If it has already
    // supplied cards, rebuilding here would discard their node-local preview
    // state and can start a third request for the same viewport image. Other
    // tabs still render after their own data is ready.
    const registryOwnsActiveRender = activeTab === 'registry' || assetBoardById(activeTab)?.system === false;
    if(registryLoading || !registryOwnsActiveRender) render();
    Promise.all([
        apiJsonResponse(assetManagerApi.getPromptLibraries()).catch(() => ({library:{libraries:[]}})),
        apiJsonResponse(assetManagerApi.getProviders()).catch(() => ({providers:[]})),
        apiJsonResponse(assetManagerApi.getCanvasAssets()).catch(() => ({categories:[], canvases:[], items:[]})),
    ]).then(([promptData,providerData,canvasAssetData]) => {
        if(registryRequestId !== registryRequestSeq) return;
        promptLibrary = promptData.library || {libraries:[]};
        apiProviders = Array.isArray(providerData.providers) ? providerData.providers : [];
        canvasAssetsData = {
            categories:Array.isArray(canvasAssetData.categories) ? canvasAssetData.categories : [],
            canvases:Array.isArray(canvasAssetData.canvases) ? canvasAssetData.canvases : [],
            items:Array.isArray(canvasAssetData.items) ? canvasAssetData.items : []
        };
        if(['prompts','workflows','canvas-assets'].includes(activeTab)) render();
    }).catch(() => {});
    setStatus('准备就绪');
    refreshScriptArchive();
    // 【QIN-569 AC1】仅在本轮引导请求成功时清除错误态标记；catch 设置的失败标记
    // 必须保持到用户重试成功或新一轮引导接管（loadRegistryBootstrap 的 catch 已吞掉
    // 拒绝，await 恒常继续，这里必须显式区分成败，否则错误条会被同轮无条件抹掉）。
    const bootstrapOk = await registryBootstrap.then(() => true, () => false);
    if(bootstrapOk && registryRequestId === registryRequestSeq) registryBootstrapError = '';
}
function disconnectRegistryPreviewScheduling(){
    registryPreviewScheduleRevision += 1;
    clearRegistryPreviewObservers();
    registryPreviewQueueRevision += 1;
    registryPreviewQueue = [];
    registryVirtualScrollCleanup?.();
    registryVirtualScrollCleanup = null;
    registryVirtualObserver?.disconnect?.();
    registryVirtualObserver = null;
    if(registryVirtualScrollFrame) cancelAnimationFrame(registryVirtualScrollFrame);
    registryVirtualScrollFrame = 0;
    registryMasonryScheduleRevision += 1;
    registryMasonryObserver?.disconnect?.();
    registryMasonryObserver = null;
}
function clearRegistryPreviewObservers(){
    registryPreviewObserver?.disconnect?.();
    registryPreviewObserver = null;
    registryPreviewFallbackCleanup?.();
    registryPreviewFallbackCleanup = null;
    gwCall('lut', 'clearRegistryLutThumbObserver');
}
function updateRegistryMasonryCards(cards=[], grid){
    if(!grid || !cards.length) return;
    const candidates = cards.filter(card => card
        && !card.classList.contains('registry-virtual-spacer')
        && (!card.style.getPropertyValue('--registry-masonry-span') || card.dataset.registryMasonryDirty === 'true'));
    if(!candidates.length) return;
    const gridStyle = getComputedStyle(grid);
    const rowHeight = Math.max(1, parseFloat(gridStyle.gridAutoRows) || 1);
    const rowGap = Math.max(0, Number.parseFloat(gridStyle.rowGap) || 0);
    const requiredGap = Math.max(0, Number.parseFloat(gridStyle.getPropertyValue('--registry-masonry-gap')) || 10);
    // 【修复 masonry 测量塌陷】测量态（grid-row:auto + 固定行轨道）会让整块网格瞬时塌陷，
    // getBoundingClientRect 强制回流时浏览器可能把 scrollTop 钳回顶部——浏览中“跳回顶部”的元凶之一。
    // 测量前记录滚动容器位置，跨度回填完成后立即恢复。
    const measureScrollRoot = grid.closest('.registry-assets-scroll');
    const savedMeasureScrollTop = measureScrollRoot ? measureScrollRoot.scrollTop : 0;
    // Measure intrinsic card height outside the assigned span. Metadata-backed
    // image ratios keep this stable even while the preview URL is deferred.
    candidates.forEach(card => { card.dataset.registryMasonryMeasuring = 'true'; });
    const measurements = candidates.map(card => ({
        card,
        height: Math.max(1, card.getBoundingClientRect().height),
    }));
    measurements.forEach(({card}) => {
        delete card.dataset.registryMasonryMeasuring;
        delete card.dataset.registryMasonryDirty;
    });
    measurements.forEach(({card, height}) => {
        const span = Math.max(1, Math.ceil((height + requiredGap) / (rowHeight + rowGap)));
        if(card.style.getPropertyValue('--registry-masonry-span') !== String(span)){
            card.style.setProperty('--registry-masonry-span', String(span));
        }
    });
    // 跨度回填完成后，若测量态塌陷已把滚动位置钳走，立即恢复到测量前位置。
    if(measureScrollRoot && savedMeasureScrollTop > 0 && measureScrollRoot.scrollTop !== savedMeasureScrollTop){
        measureScrollRoot.scrollTop = savedMeasureScrollTop;
    }
}
function remeasureRegistryMasonryGrid(grid){
    if(!grid || grid.classList?.contains('registry-grid-virtualized') || grid.classList?.contains('registry-masonry-fallback')) return;
    const cards = [...grid.querySelectorAll(':scope > .registry-card')];
    cards.forEach(card => { card.dataset.registryMasonryDirty = 'true'; });
    updateRegistryMasonryCards(cards, grid);
}
function scheduleRegistryMasonryLayout(){
    const revision = ++registryMasonryScheduleRevision;
    requestAnimationFrame(() => {
        if(revision !== registryMasonryScheduleRevision) return;
        const grids = [...root.querySelectorAll('.registry-content.registry-layout-grid .registry-grid')];
        if(!grids.length) return;
        registryMasonryObserver?.disconnect?.();
        registryMasonryObserver = null;
        if(grids.some(grid => grid.classList.contains('registry-grid-virtualized'))) return;
        if(typeof ResizeObserver !== 'function'){
            // Older engines cannot measure intrinsic card height. Fall back to
            // content-sized rows so cards never overlap while remaining usable.
            grids.forEach(grid => grid.classList.add('registry-masonry-fallback'));
            return;
        }
        const observer = new ResizeObserver(entries => {
            if(registryMasonryObserver !== observer) return;
            const cardsByGrid = new Map();
            entries.forEach(entry => {
                const grid = entry.target.closest('.registry-grid');
                if(!grid) return;
                entry.target.dataset.registryMasonryDirty = 'true';
                if(!cardsByGrid.has(grid)) cardsByGrid.set(grid, []);
                cardsByGrid.get(grid).push(entry.target);
            });
            cardsByGrid.forEach((cards, grid) => updateRegistryMasonryCards(cards, grid));
        });
        registryMasonryObserver = observer;
        grids.forEach(grid => {
            const cards = [...grid.querySelectorAll(':scope > .registry-card')];
            cards.forEach(card => {
                observer.observe(card);
            });
            updateRegistryMasonryCards(cards, grid);
        });
    });
}
function applyRegistryVirtualPreviewAspect(image){
    if(registryAspect !== 'original' || !image?.naturalWidth || !image?.naturalHeight) return;
    const thumb = image.closest?.('.asset-thumb');
    if(!thumb || thumb.style.getPropertyValue('--asset-media-aspect')) return;
    thumb.style.setProperty('--asset-media-aspect', String(image.naturalWidth / image.naturalHeight));
}
function observeRegistryVirtualCards(grid){
    registryVirtualObserver?.disconnect?.();
    registryVirtualObserver = null;
    if(!grid || typeof ResizeObserver !== 'function') return;
    registryVirtualObserver = new ResizeObserver(() => scheduleRegistryVirtualRemeasure());
    grid.querySelectorAll(':scope > .registry-card[data-registry-card]').forEach(card => registryVirtualObserver.observe(card));
}
function scheduleRegistryVirtualRemeasure(){
    if(registryVirtualRemeasureFrame) return;
    registryVirtualRemeasureFrame = requestAnimationFrame(() => {
        registryVirtualRemeasureFrame = 0;
        const grid = root.querySelector('[data-registry-virtual-grid]');
        if(!grid) return;
        const {items} = registryVirtualItemsForActiveBoard();
        const positions = new Map(items.map((item, index) => [String(item.id || ''), registryVirtualState.itemLayouts?.[index]]));
        const changed = [...grid.querySelectorAll(':scope > .registry-card[data-registry-card]')].some(card => {
            const position = positions.get(String(card.dataset.registryCard || ''));
            return position && Math.abs(card.getBoundingClientRect().height - position.height) > 1;
        });
        if(changed) updateRegistryVirtualWindow(true);
    });
}
function pumpRegistryPreviewQueue(){
    while(registryPreviewActive < REGISTRY_PREVIEW_CONCURRENCY && registryPreviewQueue.length){
        const image = registryPreviewQueue.shift();
        if(!image?.isConnected) continue;
        const source = String(image.dataset.registryPreviewSrc || '');
        image.removeAttribute('data-registry-preview-src');
        image.removeAttribute('aria-busy');
        image.dataset.registryPreviewQueued = 'false';
        image.dataset.registryPreviewLoaded = 'true';
        if(!source) continue;
        registryPreviewActive += 1;
        let settled = false;
        const finish = () => {
            if(settled) return;
            settled = true;
            registryPreviewActive = Math.max(0, registryPreviewActive - 1);
            if(image.closest?.('[data-registry-virtual-grid]')) applyRegistryVirtualPreviewAspect(image);
            scheduleRegistryVirtualRemeasure();
            pumpRegistryPreviewQueue();
        };
        image.addEventListener('load', finish, {once:true});
        image.addEventListener('error', finish, {once:true});
        image.src = source;
        if(image.complete) queueMicrotask(finish);
    }
}
function activateRegistryPreviewImage(image){
    const source = String(image?.dataset?.registryPreviewSrc || '');
    if(!source || image.dataset.registryPreviewLoaded === 'true' || image.dataset.registryPreviewQueued === 'true') return false;
    image.dataset.registryPreviewQueued = 'true';
    registryPreviewQueue.push(image);
    pumpRegistryPreviewQueue();
    return true;
}
function scheduleRegistryPreviewFallback(scrollRoot){
    let frame = 0;
    let preloadAhead = false;
    let lastScrollPreloadAt = Number.NEGATIVE_INFINITY;
    const activateVisible = (preloadOutsideViewport=false) => {
        const rootRect = scrollRoot.getBoundingClientRect();
        const previews = [...root.querySelectorAll('img[data-registry-preview-src]')]
            .filter(image => image.dataset.registryPreviewQueued !== 'true')
            .map(image => ({
                image,
                rect:image.getBoundingClientRect(),
            }))
            .filter(({rect}) => rect.right >= rootRect.left && rect.left <= rootRect.right);
        previews.forEach(({image,rect}) => {
            const visible = rect.bottom >= rootRect.top && rect.top <= rootRect.bottom;
            if(visible) activateRegistryPreviewImage(image);
        });
        if(!preloadOutsideViewport) return;
        previews.filter(({rect}) => rect.top >= rootRect.bottom)
            .sort((a,b) => a.rect.top - b.rect.top || a.rect.left - b.rect.left)
            .slice(0, REGISTRY_PREVIEW_SCROLL_AHEAD_COUNT)
            .forEach(({image}) => activateRegistryPreviewImage(image));
    };
    const scheduleVisible = (preloadOutsideViewport=false) => {
        if(preloadOutsideViewport) preloadAhead = true;
        if(frame) return;
        frame = requestAnimationFrame(() => {
            frame = 0;
            const shouldPreloadAhead = preloadAhead;
            preloadAhead = false;
            activateVisible(shouldPreloadAhead);
        });
    };
    const onScroll = () => scheduleVisible();
    const onWheel = event => {
        if(Number(event.deltaY || 0) <= 0){
            preloadAhead = false;
            lastScrollPreloadAt = Number.NEGATIVE_INFINITY;
            scheduleVisible();
            return;
        }
        const currentTime = performance.now();
        if(currentTime - lastScrollPreloadAt < REGISTRY_PREVIEW_SCROLL_PRELOAD_INTERVAL_MS) return;
        lastScrollPreloadAt = currentTime;
        scheduleVisible(true);
    };
    scrollRoot.addEventListener('scroll', onScroll, {passive:true});
    scrollRoot.addEventListener('wheel', onWheel, {passive:true});
    registryPreviewFallbackCleanup = () => {
        scrollRoot.removeEventListener('scroll', onScroll);
        scrollRoot.removeEventListener('wheel', onWheel);
        if(frame) cancelAnimationFrame(frame);
        preloadAhead = false;
    };
    activateVisible();
}
function scheduleRegistryPreviewImages(){
    clearRegistryPreviewObservers();
    // 【QIN-554】LUT 卡片缩略与图片缩略共用同一渲染节奏：可见区优先、限并发绘制。
    gwCall('lut', 'scheduleRegistryLutThumbs');
    const revision = registryPreviewScheduleRevision;
    requestAnimationFrame(() => {
        if(revision !== registryPreviewScheduleRevision) return;
        const scrollRoot = root.querySelector('.registry-assets-scroll');
        const images = [...root.querySelectorAll('img[data-registry-preview-src]')];
        if(!scrollRoot || !images.length) return;
        // Chromium can occasionally miss an observer notification while the
        // registry scroll container is being repainted. Keep the same
        // viewport-bounded scroll pass active even when the observer exists.
        scheduleRegistryPreviewFallback(scrollRoot);
        if(typeof IntersectionObserver !== 'function'){
            return;
        }
        const deferredImages = images.filter(image => image.dataset.registryPreviewSrc);
        if(!deferredImages.length) return;
        const observer = new IntersectionObserver(entries => {
            if(registryPreviewObserver !== observer) return;
            entries.forEach(entry => {
                if(!entry.isIntersecting) return;
                const image = entry.target;
                activateRegistryPreviewImage(image);
                observer.unobserve(image);
            });
        }, {root:scrollRoot, rootMargin:REGISTRY_PREVIEW_ROOT_MARGIN});
        registryPreviewObserver = observer;
        deferredImages.forEach(image => observer.observe(image));
    });
}
function registryPreviewCacheKey(image){
    const card = image.closest?.('[data-registry-card]');
    if(!card) return '';
    const imageIndex = [...card.querySelectorAll('img')].indexOf(image);
    return imageIndex < 0 ? '' : `${card.dataset.registryCard || ''}:${imageIndex}`;
}
function captureRegistryPreviewImages(){
    const cache = registryPreviewImageCache;
    root?.querySelectorAll('img[data-registry-preview-loaded="true"]').forEach(image => {
        const key = registryPreviewCacheKey(image);
        if(!key) return;
        cache.set(key, {image, source:image.currentSrc || image.src});
    });
    return cache;
}
function restoreRegistryPreviewImages(cache){
    if(!cache?.size) return;
    root?.querySelectorAll('img[data-registry-preview-src]').forEach(image => {
        const key = registryPreviewCacheKey(image);
        const entry = cache.get(key);
        const source = String(image.dataset.registryPreviewSrc || '');
        if(!key || !source) return;
        let expectedSource = '';
        try { expectedSource = new URL(source, document.baseURI).href; } catch(_) { return; }
        if(!entry || entry.image.isConnected) return;
        if(entry.source !== expectedSource){
            cache.delete(key);
            return;
        }
        image.replaceWith(entry.image);
    });
}
// ===== MVP1-T5 / QIN-549 治理建议：顶栏入口与分组抽屉 =====
let governanceSuggestionsData = null;
let governanceSuggestionsLoading = false;
let governanceSuggestionsError = null;
let governanceDrawerOpen = false;
const GOVERNANCE_SUGGESTION_KIND_LABELS = {
    naming:'命名建议',
    similar_names:'相似名（仅提示）',
    duplicate_files:'文件查重',
    duplicate_shots:'镜头查重',
    vector_duplicates:'向量近重',
    tagging:'受控打标',
};
const GOVERNANCE_SUGGESTION_GROUPS = [
    {id:'naming', label:'命名建议', icon:'text-search', description:'发现不符合统一命名要求的素材名称。', action:'打开素材后按命名规则改名，或确认系统给出的建议名称。'},
    {id:'similar_names', label:'相似名（仅提示）', icon:'scan-search', description:'发现规范化后名称相近的素材。', action:'打开素材人工核对后改名；此类仅作提示，不会自动合并。'},
    {id:'duplicate_files', label:'文件查重', icon:'files', description:'发现文件内容哈希相同的素材。', action:'打开素材核对内容，确认后可建立重复素材关联。'},
    {id:'duplicate_shots', label:'镜头查重', icon:'film', description:'发现不同素材中内容相同的镜头片段。', action:'打开相关视频核对时间片段，确认后可建立镜头关联。'},
    {id:'vector_duplicates', label:'向量近重', icon:'scan-search', description:'发现向量相似度达到阈值的素材对。', action:'打开素材人工复核，确认后可建立近重关联。'},
    {id:'tagging', label:'受控打标', icon:'tags', description:'发现可映射到受控词表的自由标签。', action:'打开素材手动打标，或确认写入建议的受控标签。'},
];
const GOVERNANCE_INFORMATIONAL_KINDS = new Set(['similar_names']);
function governanceSuggestionEntries(){
    const suggestions = governanceSuggestionsData?.suggestions || {};
    return GOVERNANCE_SUGGESTION_GROUPS.flatMap(group => (
        (Array.isArray(suggestions[group.id]) ? suggestions[group.id] : []).map(entry => ({...entry, kind:group.id}))
    ));
}
function governanceSuggestionTotal(){
    return governanceSuggestionEntries().length;
}
function governanceTriggerLabel(){
    if(governanceSuggestionsError?.status === 403) return '治理建议：需要治理权限，打开查看';
    if(governanceSuggestionsError) return '治理建议读取失败，打开查看错误并重试';
    if(governanceSuggestionsLoading) return '治理建议正在加载';
    if(!governanceSuggestionsData) return '打开治理建议';
    return '治理建议，待处理 ' + governanceSuggestionTotal() + ' 条';
}
function render(){
    const registryPreviewCache = captureRegistryPreviewImages();
    disconnectRegistryPreviewScheduling();
    const previewScheduleRevision = registryPreviewScheduleRevision;
    const focusedRegistryCard = document.activeElement?.closest?.('[data-registry-card]');
    const registryFocusId = registryFocusRestoreId || focusedRegistryCard?.dataset?.registryCard || '';
    const scheduleRegistryPreviews = () => {
        if(previewScheduleRevision === registryPreviewScheduleRevision) scheduleRegistryPreviewImages();
    };
    // 【修复问题4】在重新渲染前，保存当前标签页的滚动位置
    const contentScroll = document.querySelector('.content-scroll');
    if(contentScroll){
        if(activeTab === 'registry' || assetBoardById(activeTab)?.system === false) savedScrollPositions.registry = contentScroll.scrollTop;
        else if(contentScroll.scrollTop > 0 && activeTab === 'assets') savedScrollPositions.assets = contentScroll.scrollTop;
        else if(contentScroll.scrollTop > 0 && activeTab === 'workflows') savedScrollPositions.workflows = contentScroll.scrollTop;
        else if(contentScroll.scrollTop > 0 && activeTab === 'prompts') savedScrollPositions.prompts = contentScroll.scrollTop;
        else if(contentScroll.scrollTop > 0 && activeTab === 'local') savedScrollPositions.local = contentScroll.scrollTop;
        else if(contentScroll.scrollTop > 0 && activeTab === 'canvas-assets') savedScrollPositions.canvas = contentScroll.scrollTop;
    }
    // 【修复问题4-增强】按卡片身份锚定滚动：优先聚焦卡片，其次视口内第一张卡片；找不到时仍走序号回退
    let registryScrollAnchorId = '';
    let registryScrollAnchorOffset = 0;
    if((activeTab === 'registry' || assetBoardById(activeTab)?.system === false) && contentScroll){
        const anchorCard = document.activeElement?.closest?.('[data-registry-card]')
            || [...contentScroll.querySelectorAll('[data-registry-card]')].find(card => {
                const rect = card.getBoundingClientRect();
                const scrollRect = contentScroll.getBoundingClientRect();
                return rect.bottom > scrollRect.top && rect.top < scrollRect.bottom;
            })
            || contentScroll.querySelector('[data-registry-card]');
        if(anchorCard){
            registryScrollAnchorId = anchorCard.dataset.registryCard || '';
            registryScrollAnchorOffset = anchorCard.getBoundingClientRect().top - contentScroll.getBoundingClientRect().top;
        }
    }
    const scrollState = [...document.querySelectorAll('.nav-scroll,.content-scroll,.detail-scroll')]
        .map((el, index) => ({index, top:el.scrollTop, left:el.scrollLeft}));
    renderBoardNavigation();
    renderAssetTreeBreadcrumb();
    const customLibraryActive = assetBoardById(activeTab)?.system === false;
    const systemRackActive = systemBoardRackActive();
    const pageClass = String(activeTab || 'assets').replace(/[^a-z0-9_-]/gi, '-');
    root.className = `asset-manager-root asset-tree-layout asset-manager-root-page-${pageClass}${activeTab === 'local' ? ' asset-manager-root-local' : ''}${activeTab === 'registry' ? ' asset-manager-root-registry' : ''}${customLibraryActive ? ' asset-manager-root-library' : ''}${customLibraryActive && registryDetailPanelCollapsed ? ' registry-detail-collapsed' : ''}${activeTab === 'lut' && registryDetailPanelCollapsed ? ' registry-detail-collapsed' : ''}${systemRackActive ? ' asset-manager-root-rack' : ''}`;
    gwCall('preview', 'syncRegistryDetailGridWidth');
    if(activeTab === 'registry') renderRegistryManager();
    else if(activeTab === 'scripts') gwLoad('organize').then(m => m.renderScriptAssetsManager());
    else if(activeTab === 'projects') gwLoad('organize').then(m => m.renderProjectManager());
    else if(activeTab === 'prompts') gwLoad('organize').then(m => m.renderPromptManager());
    else if(activeTab === 'lut') gwLoad('lut').then(m => m.renderLutManager());
    else if(activeTab === 'workflows') gwLoad('organize').then(m => m.renderWorkflowManager());
    else if(activeTab === 'inbox') gwLoad('inbox').then(m => m.renderInboxManager());
    else if(activeTab === 'local') gwLoad('upload').then(m => m.renderLocalManager());
    else if(activeTab === 'canvas-assets') gwLoad('canvas').then(m => m.renderCanvasAssetsManager());
    else if(assetBoardById(activeTab)?.system === false) gwLoad('organize').then(m => m.renderCustomAssetLibrary(assetBoardById(activeTab)));
    else gwLoad('organize').then(m => m.renderAssetManager());
    if(document.documentElement.classList.contains('vault-shell-embedded')){
        const board = assetBoardById(activeTab);
        let toolbarTitle = board?.name || '资源库';
        let toolbarSubtitle = '统一资产工具';
        if(activeTab === 'registry'){
            toolbarTitle = registryCategoryLabel(registryStatCategory);
            toolbarSubtitle = registryTotalSummary(registryVisibleAssets().length);
        } else if(activeTab === 'projects'){
            toolbarTitle = '项目素材';
            const selectedProj = (registryProjectFilterId && registryProjectFilterId !== '__all__')
                ? projects.find(item => item.id === registryProjectFilterId)
                : null;
            toolbarSubtitle = `${selectedProj?.name || '全部项目'} · ${registryTotalSummary(registryVisibleAssets().length)}`;
        } else if(board?.system === false){
            toolbarSubtitle = registryTotalSummary(registryVisibleAssets().length);
        }
        root.insertAdjacentHTML('afterbegin', `${renderRegistryOverview()}${renderRegistryToolbar(null, toolbarTitle, toolbarSubtitle, {allowUrl:true,hideHeading:activeTab === 'registry' || activeTab === 'projects'})}`);
    }
    if(activeTab === 'registry' || customLibraryActive) renderRegistryFreshNotice();
    restoreRegistryPreviewImages(registryPreviewCache);
    if(activeTab === 'registry' || customLibraryActive){
        restoreRegistryVirtualScroll(
            root.querySelector('.registry-assets-scroll'),
            contentScroll?.scrollTop ?? savedScrollPositions.registry,
            registryScrollAnchorId,
            registryScrollAnchorOffset,
        );
    }
    refreshIcons();
    const bootSkeleton = document.getElementById('assetManagerBootSkeleton');
    if(bootSkeleton) bootSkeleton.hidden = true;
    scheduleRegistryMasonryLayout();
    scheduleRegistryVirtualWindow();
    if(scrollState.length){
        requestAnimationFrame(() => {
            document.querySelectorAll('.nav-scroll,.content-scroll,.detail-scroll').forEach((el, index) => {
                const saved = scrollState.find(item => item.index === index);
                if(!saved) return;
                el.scrollTop = saved.top;
                el.scrollLeft = saved.left;
            });
            scheduleRegistryPreviews();
        });
    } else scheduleRegistryPreviews();
    // 【修复问题4】在渲染完成后，恢复当前标签页的滚动位置
    requestAnimationFrame(() => {
        const newContentScroll = document.querySelector('.content-scroll');
        if(newContentScroll){
            let targetScroll = 0;
            if(activeTab === 'registry') targetScroll = savedScrollPositions.registry;
            else if(activeTab === 'assets') targetScroll = savedScrollPositions.assets;
            else if(activeTab === 'workflows') targetScroll = savedScrollPositions.workflows;
            else if(activeTab === 'prompts') targetScroll = savedScrollPositions.prompts;
            else if(activeTab === 'local') targetScroll = savedScrollPositions.local;
            else if(activeTab === 'canvas-assets') targetScroll = savedScrollPositions.canvas;
            if(targetScroll > 0) newContentScroll.scrollTop = targetScroll;
            // 【修复问题4-增强】优先把锚点卡片恢复到原视口位置；卡片不存在时保持上方位置回退
            if(registryScrollAnchorId){
                const anchorCard = newContentScroll.querySelector(`[data-registry-card="${CSS.escape(registryScrollAnchorId)}"]`);
                if(anchorCard){
                    const anchorTop = anchorCard.getBoundingClientRect().top - newContentScroll.getBoundingClientRect().top;
                    newContentScroll.scrollTop = Math.max(0, newContentScroll.scrollTop + anchorTop - registryScrollAnchorOffset);
                }
            }
        }
    });
    if(registryFocusId){
        requestAnimationFrame(() => {
            const card = document.querySelector(`[data-registry-card="${CSS.escape(registryFocusId)}"]`);
            if(!card) return;
            card.scrollIntoView({block:'nearest'});
            card.focus({preventScroll:true});
            if(registryFocusRestoreId === registryFocusId) registryFocusRestoreId = '';
        });
    }
    scheduleAssetTouchbarReport();
    gwCall('preview', 'hydrateVideoStoryboardImages', document);
    // 【QIN-566】渲染收尾同步外壳侧栏（推送快照 + 持久化入口，仅内嵌金库模式生效）。
    notifyShellTreeSnapshot();
}
function updateSearchQueryFromInput(id, value){
    if(id === 'registrySearch') registryQuery = value || '';
    else if(id === 'assetSearch') assetQuery = value || '';
    else if(id === 'workflowSearch') workflowQuery = value || '';
    else if(id === 'promptSearch') promptQuery = value || '';
    else if(id === 'localSearch') localQuery = value || '';
    else if(id === 'localUploadSearch') localUploadQuery = value || '';
    else if(id === 'canvasAssetSearch') canvasAssetQuery = value || '';
}
function scheduleSearchRender(id, pos=0, delay=140){
    clearTimeout(searchRenderTimer);
    searchRenderTimer = setTimeout(() => {
        gwCall('organize', 'clearSearchSelection', id);
        const restoreFocus = () => requestAnimationFrame(() => {
            const input = document.getElementById(id);
            input?.focus();
            try { input?.setSelectionRange?.(pos, pos); } catch(_) {}
        });
        if(id === 'registrySearch'){
            refreshRegistryAssets(true).then(restoreFocus).catch(err => setStatus(err.message || '筛选失败'));
            return;
        }
        render();
        restoreFocus();
    }, Math.max(0, delay));
}
function renderCanvasAssetTreeBranch(cat){
    const canvases = canvasAssetsForCategory(cat.id);
    const activeParent = cat.id === activeCanvasAssetCategory && !activeCanvasAssetCanvasId;
    const containsActive = cat.id === activeCanvasAssetCategory && !!activeCanvasAssetCanvasId;
    return `<div class="tree-branch ${cat.id === activeCanvasAssetCategory ? 'expanded' : ''}">
        <button class="tree-row tree-parent ${activeParent ? 'active' : ''} ${containsActive ? 'contains-active' : ''}" type="button" data-canvas-asset-cat="${escapeAttr(cat.id)}">
            <span class="tree-row-icon"><i data-lucide="${cat.id === 'smart' ? 'sparkles' : cat.id === 'classic' ? 'layout-grid' : cat.id === 'reference' ? 'map' : 'layout-dashboard'}"></i></span>
            <span class="tree-row-name">${escapeHtml(cat.name || '画布')}</span>
            <span class="tree-row-count">${Number(cat.count || 0)}</span>
        </button>
        <div class="tree-children">
            ${canvases.length ? canvases.map(canvas => {
                const active = canvas.id === activeCanvasAssetCanvasId && cat.id === activeCanvasAssetCategory;
                const count = canvasAssetCountForCanvas(canvas.id);
                return `<button class="tree-row tree-child ${active ? 'active' : ''}" type="button" data-canvas-asset-canvas="${escapeAttr(canvas.id)}" data-canvas-asset-canvas-cat="${escapeAttr(cat.id)}">
                    <span class="tree-elbow"></span>
                    <span class="tree-row-icon"><i data-lucide="${canvas.kind === 'smart' ? 'sparkles' : canvas.kind === 'reference' ? 'map' : 'file-image'}"></i></span>
                    <span class="tree-row-name" title="${escapeAttr(canvas.title || '未命名画布')}">${escapeHtml(canvas.title || '未命名画布')}</span>
                    <span class="tree-row-count">${count}</span>
                </button>`;
            }).join('') : '<div class="tree-empty">暂无画布</div>'}
        </div>
    </div>`;
}
function focusTreeEditInput(id){
    requestAnimationFrame(() => {
        const input = document.getElementById(id);
        input?.focus();
        input?.select?.();
    });
}
function findLocalUpload(id){
    return (localAssets || []).find(it => it.id === id) || null;
}
function localUploadFolderExists(path=''){
    const target = String(path || '');
    let found = !target;
    function walk(node){
        if(!node || found) return;
        if(String(node.path || '') === target){ found = true; return; }
        (node.children || []).forEach(walk);
    }
    walk(localUploadTree);
    return found;
}
function localUploadClassEntries(){
    return assetClassificationEntriesForItems((localAssets || []).filter(item => assetKind(item) === 'image'));
}
function activeLocalUploadClassEntry(){
    return localUploadClassEntries().find(entry => entry.key === activeLocalUploadClassFilter) || null;
}
function renderLocalUploadFolderBranch(folder, depth=0){
    if(!folder) return '';
    const path = String(folder.path || '');
    const children = Array.isArray(folder.children) ? folder.children : [];
    const active = !activeLocalUploadClassFilter && path === activeLocalUploadFolder;
    const contains = !active && children.some(child => localUploadFolderContainsActive(child));
    const isPath = active || contains;
    const hasChildren = children.length > 0;
    return `<div class="asset-tree-node ${active ? 'is-selected' : ''} ${isPath ? 'is-path' : 'is-path-dim'}" style="--tree-depth:${Math.min(depth, 8)}" role="treeitem" aria-selected="${active}" aria-expanded="${hasChildren}" aria-level="${depth + 1}">
        <button class="asset-tree-row" type="button" data-localup-folder="${escapeAttr(path)}" aria-label="${escapeAttr(folder.name || '文件夹')}">
            <span class="asset-tree-twisty">${hasChildren ? '<i data-lucide="chevron-down"></i>' : ''}</span>
            <span class="asset-tree-dot"></span>
            <i class="asset-tree-icon" data-lucide="${active ? 'folder-open' : (depth ? 'folder' : 'upload-cloud')}"></i>
            <span class="asset-tree-name" title="${escapeAttr(folder.name || '文件夹')}">${escapeHtml(folder.name || '文件夹')}</span>
            <span class="asset-tree-count">${Number(folder.count || 0)}</span>
        </button>
        ${hasChildren ? `<div role="group">${children.map(child => renderLocalUploadFolderBranch(child, depth + 1)).join('')}</div>` : ''}
    </div>`;
}
function localUploadFolderContainsActive(folder){
    if(!folder) return false;
    if(activeLocalUploadClassFilter) return false;
    if(String(folder.path || '') === activeLocalUploadFolder) return true;
    return (folder.children || []).some(child => localUploadFolderContainsActive(child));
}
function renderLocalUploadFolderPanel(){
    const tree = localUploadTree || {id:'__root__', path:'', name:'根目录', count:0, children:[]};
    const canRename = Boolean(activeLocalUploadFolder) && !activeLocalUploadClassFilter;
    return `<aside class="asset-panel asset-nav asset-tree-panel" aria-label="上传文件夹">
        <div class="asset-tree-tabs" role="group" aria-label="上传目录操作">
            <div class="asset-tree-tab active" role="heading" aria-level="2">上传资源</div>
            <button class="hw-btn hw-btn-icon" type="button" data-localup-back-to-tree title="返回资源入口" aria-label="返回资源入口"><i data-lucide="arrow-left"></i></button>
            <button class="asset-tree-tab" type="button" data-localup-folder-new title="新建文件夹" aria-label="新建文件夹"><i data-lucide="folder-plus"></i><span>新建</span></button>
        </div>
        <div class="asset-tree-scroll" role="tree" aria-label="上传文件夹树" tabindex="0">${renderLocalUploadFolderBranch(tree)}</div>
        <div class="asset-tree-footer">
            <button class="hw-btn" type="button" data-localup-folder-rename ${canRename ? '' : 'disabled'} title="重命名当前文件夹"><i data-lucide="pencil"></i><span>重命名文件夹</span></button>
        </div>
    </aside>`;
}
/* ---------------------------------------------------------------------------
 * 【QIN-547】收集箱：托管资产与上传资源合并后的默认收集文件夹。
 * 真实磁盘目录（默认 data_root/inbox，位置可在设置里改）；列表同时展示收集箱文件、
 * 遗留浏览器收集文件与托管资产迁移后的 URL 条目；未读角标按「已查看时间点」计算。
 * ------------------------------------------------------------------------- */
const INBOX_KIND_LABELS = {
    image:'图片', video:'视频', audio:'音频', document:'文档', archive:'压缩包',
    workflow:'工作流', url:'链接', record:'仅记录', '未分类':'未分类',
};
const INBOX_SOURCE_LABELS = {
    inbox:'收集箱', collect_inbox:'浏览器收集', asset_library:'托管资产',
};
function assetInboxUnreadCount(){
    return Math.max(0, Number(inboxSettings?.unread_count || 0));
}
// 【QIN-588 AC5】列表内容签名：id/修改时间/大小任一变化即视为内容变化；
// 停留刷新只在签名变化时重绘，避免无谓重置滚动。
function assetInboxItemsSignature(){
    return inboxItems.map(item => `${item.id}|${item.modified_at}|${item.size_bytes}`).join(',');
}
async function uploadAssetInboxFiles(files){
    const list = Array.from(files || []).filter(Boolean);
    if(!list.length) return;
    const form = new FormData();
    list.forEach(file => form.append('files', file, file.name));
    try{
        const result = await apiJsonResponse(assetManagerHttp.request('/api/asset-inbox/upload', {method:'POST', credentials:'same-origin', body:form}));
        showAssetNotice(`已上传 ${Number(result.count || list.length)} 个文件到收集箱`);
        // 【QIN-585 AC2】写后必刷：不复用写之前发出的在途读取（那是旧快照），否则新文件不出现。
        await gwCall('inbox', 'loadAssetInbox', {refresh:true});
        // 用户此刻正在收集箱里看到新文件，角标不应再算作未读。
        if(activeTab === 'inbox' && assetInboxUnreadCount() > 0) await gwCall('inbox', 'markAssetInboxViewed');
    }catch(error){
        showAssetFailure('上传到收集箱失败', error);
    }
    if(activeTab === 'inbox') render();
}
function registryKindLabel(kind=''){
    return {image:'图片',video:'视频',audio:'音频',workflow:'工作流',document:'文档',project_file:'工程文件',lut:'LUT'}[kind] || kind || '其他';
}
const REGISTRY_FILE_TYPE_LABELS = {
    jpg:'JPEG 图片', jpeg:'JPEG 图片', png:'PNG 图片', gif:'GIF 图片', webp:'WebP 图片', bmp:'BMP 图片',
    tiff:'TIFF 图片', tif:'TIFF 图片', heic:'HEIC 图片', heif:'HEIF 图片', avif:'AVIF 图片', raw:'RAW 图像',
    svg:'SVG 矢量图', eps:'EPS 矢量图', psd:'Photoshop 工程', ai:'Illustrator 工程', cdr:'CorelDRAW 工程',
    hdr:'HDR 图像', exr:'EXR 图像', cube:'Cube LUT 色彩查找表（.cube）',
    txt:'TXT 文本', csv:'CSV 表格', tsv:'TSV 表格', json:'JSON 文件', xml:'XML 文件', html:'HTML 文档', htm:'HTML 文档',
    md:'Markdown 文档', markdown:'Markdown 文档', rtf:'RTF 文档', doc:'Word 文档', docx:'Word 文档', odt:'OpenDocument 文档',
    ppt:'PowerPoint 演示文稿', pptx:'PowerPoint 演示文稿', odp:'OpenDocument 演示文稿',
    xls:'Excel 工作簿', xlsx:'Excel 工作簿', ods:'OpenDocument 工作簿',
    pdf:'PDF 文档', srt:'SRT 字幕', vtt:'WebVTT 字幕', zip:'ZIP 压缩包',
    ttf:'TrueType 字体', otf:'OpenType 字体', ttc:'TrueType 字体集', otc:'OpenType 字体集', woff:'Web 字体', woff2:'Web 字体',
    stl:'STL 模型', '3mf':'3MF 模型', obj:'OBJ 模型', fbx:'FBX 模型', gltf:'glTF 模型', glb:'GLB 模型', usd:'USD 模型',
    usdz:'USDZ 模型', step:'STEP 模型', stp:'STEP 模型', iges:'IGES 模型', igs:'IGES 模型', dxf:'DXF 模型', ply:'PLY 模型',
    '3ds':'3DS 模型', dae:'Collada 模型', ifc:'IFC 模型', wrl:'VRML 模型', vrml:'VRML 模型',
    blend:'Blender 工程', c4d:'Cinema 4D 工程', max:'3ds Max 工程', ma:'Maya ASCII 工程', mb:'Maya 工程', aep:'After Effects 工程',
    ckpt:'AI 模型权重', safetensors:'AI 模型权重', pt:'PyTorch 模型',
};
function registryAssetTypeLabel(item={}){
    const extension = registryAssetExtension(item);
    if(REGISTRY_FILE_TYPE_LABELS[extension]) return REGISTRY_FILE_TYPE_LABELS[extension];
    if(ASSET_VIDEO_EXTENSIONS.has(extension)) return `${extension.toUpperCase()} 视频`;
    if(ASSET_AUDIO_PREVIEW_EXTENSIONS.includes(extension)) return `${extension.toUpperCase()} 音频`;
    return registryKindLabel(item.kind);
}
function registryAssetTypeIcon(item={}){
    return registryKindIcon(registryAssetCategory(item) === 'lut' ? 'lut' : detailViewerType('registry', item));
}
function registryFacetKindCount(...kinds){
    const wanted = new Set(kinds);
    return (registryFacets.kinds || []).reduce((total, item) => wanted.has(String(item.name || item.id || '')) ? total + Number(item.count || 0) : total, 0);
}
function registryCategoryCount(category){
    const item = (registryFacets.categories || []).find(value => String(value.name || '') === category);
    return Number(item?.count || 0);
}
function registryCategoryLabel(category=''){
    return {image:'图片',video:'视频',audio:'音频',document:'文档',model:'模型',font:'字体',lut:'LUT',other:'其他'}[category] || '全部资产';
}
function renderRegistryOverviewStats(){
    // 【QIN-580】「资产总量」口径排除 LUT 素材：LUT 是调色预设而非素材，只在统计条里保留导航入口，不参与总量与占比分母。
    const lutTotal = Math.max(registryCategoryCount('lut'), registryFacetKindCount('lut'));
    const facetTotal = (registryFacets.categories || []).reduce((total, item) => total + Number(item.count || 0), 0)
        || (registryFacets.kinds || []).reduce((total, item) => total + Number(item.count || 0), 0)
        || Number(registryTotal || 0);
    const globalTotal = Math.max(0, facetTotal - lutTotal);
    // 【QIN-531】画布卡片改为全量统计：智能画布 + 参考画布 + 普通画布一并计入，与其它分类卡片同构。
    const canvasTotal = Array.isArray(canvasAssetsData.canvases) ? canvasAssetsData.canvases.length : 0;
    const stats = [
        {id:'', name:'资产总量', value:globalTotal, icon:'boxes', primary:true},
        {id:'image', name:'图片', value:registryCategoryCount('image'), icon:'image'},
        {id:'video', name:'视频', value:registryCategoryCount('video'), icon:'film'},
        {id:'audio', name:'音频', value:registryCategoryCount('audio'), icon:'audio-lines'},
        {id:'document', name:'文档', value:registryCategoryCount('document'), icon:'files'},
        {id:'model', name:'模型', value:registryCategoryCount('model'), icon:'box'},
        {id:'font', name:'字体', value:registryCategoryCount('font'), icon:'type'},
        // 【QIN-531】卡片更名「画布」；data-registry-stat 仍沿用 reference-canvas，既有跳转不动。
        {id:'reference-canvas', name:'画布', value:canvasTotal, icon:'map'},
        {id:'other', name:'其他', value:registryCategoryCount('other'), icon:'package-open'},
        // 【QIN-566 / QIN-578】顶栏导航入口：排在「其他」之后。
        {id:'projects', name:'项目', nav:true, icon:'clapperboard'},
        {id:'scripts', name:'剧本', nav:true, icon:'file-text'},
        {id:'prompts', name:'提示词', nav:true, icon:'text-cursor-input'},
        // 【QIN-580】LUT 库保留导航入口，排在「提示词」之后；但它不计入「资产总量」，也不出统计数值卡。
        {id:'lut', name:'LUT', nav:true, icon:'palette'},
    ];
    return `<section class="registry-overview-stats" aria-label="资产统计">${stats.map(stat => {
        const ratio = globalTotal ? Math.round(Number(stat.value || 0) / globalTotal * 100) : 0;
        // 【QIN-569 AC3/AC4】统计卡选中态跟随真实目的地：导航入口看 activeTab；画布卡在画布资产视图时点亮；
        // 其余统计卡必须仍处于资产总览（registry）才允许点亮，避免切换到项目/剧本/提示词后 LUT 等高亮残留。
        const active = stat.nav
            ? activeTab === stat.id
            : (stat.id === 'reference-canvas'
                ? activeTab === 'canvas-assets'
                : activeTab === 'registry' && registryStatCategory === stat.id);
        if(stat.nav){
            // 【QIN-566】导航入口卡：只承载目的地与选中态，不输出计数/占比/进度条（不伪造统计数据）。
            return `<button type="button" class="registry-stat-card is-nav ${active ? 'active' : ''}" data-registry-nav="${escapeAttr(stat.id)}" aria-label="${escapeAttr(stat.name)}" title="${escapeAttr(stat.name)}"><span class="registry-stat-icon"><i data-lucide="${stat.icon}"></i></span><span class="registry-stat-copy"><small>${escapeHtml(stat.name)}</small></span></button>`;
        }
        // 双行结构：第 1 行名称 + 占比，第 2 行「图标 + 数量」与高光渐变进度条。
        return `<button type="button" class="registry-stat-card ${stat.primary ? 'primary' : ''} ${active ? 'active' : ''} ${Number(stat.value || 0) === 0 ? 'is-empty' : ''}" data-registry-stat="${escapeAttr(stat.id)}" aria-pressed="${active}"><span class="registry-stat-icon"><i data-lucide="${stat.icon}"></i></span><span class="registry-stat-copy"><small>${stat.name}</small><strong>${Number(stat.value || 0).toLocaleString('zh-CN')}</strong></span><em>${stat.primary ? '统一资产索引' : `${ratio}%`}</em><span class="registry-stat-meter" aria-hidden="true"><span style="--vault-ratio:${Math.min(100, Math.max(0, ratio))}%"></span></span></button>`;
    }).join('')}</section>`;
}
function renderRegistryOverview(){
    // 资产中心仅保留统计入口，索引状态、托管目录和问题详情分别归入资产设置与中心页。
    return `<section class="registry-overview-shell">${renderRegistryOverviewStats()}</section>`;
}
function registryAssetExtension(item={}){
    const existing = assetFileExtension(item);
    if(existing) return existing;
    const source = String(item.display_path || item.canonical_relpath || item.relative_path || item.metadata?.original_filename || item.metadata?.file_name || item.metadata?.filename || '').split('?')[0].split('#')[0];
    const match = source.toLowerCase().match(/\.([a-z0-9]+)$/);
    if(match) return match[1];
    return String(item.metadata?.extension || item.metadata?.file_extension || '').trim().toLowerCase().replace(/^\./, '');
}
const REGISTRY_ASSET_CATEGORY_EXTENSIONS = Object.freeze({
    image:new Set(['jpg','jpeg','png','gif','webp','bmp','tif','tiff','heic','heif','avif','raw','svg','eps','psd','ai','cdr','hdr','exr']),
    video:new Set(['mp4','mov','mkv','avi','webm','m4v','flv','wmv']),
    audio:new Set(['mp3','wav','flac','aac','ogg','m4a','aiff','aif','wma','opus']),
    document:new Set(['txt','csv','tsv','json','xml','html','htm','md','markdown','rtf','doc','docx','odt','ppt','pptx','odp','xls','xlsx','ods','pdf','srt','vtt','zip']),
    model:new Set(['stl','3mf','obj','fbx','gltf','glb','usd','usdz','step','stp','iges','igs','dxf','ply','3ds','dae','ifc','wrl','vrml','blend','c4d','max','ma','mb','ckpt','safetensors','pt','pth','onnx','gguf']),
    font:new Set(['ttf','otf','ttc','otc','woff','woff2']),
    lut:new Set(['cube']),
});
function registryAssetCategory(item={}){
    const extension = registryAssetExtension(item);
    for(const [category, extensions] of Object.entries(REGISTRY_ASSET_CATEGORY_EXTENSIONS)){
        if(extensions.has(extension)) return category;
    }
    const type = detailViewerType('registry', item);
    if(type === 'text') return 'document';
    if(['image','video','audio','document','model','font','lut'].includes(type)) return type;
    const kind = String(item.kind || item.media_type || item.type || '').toLowerCase();
    if(['image','video','audio','document','model','font','lut'].includes(kind)) return kind;
    return kind === 'text' ? 'document' : 'other';
}
function customAssetLibraryIncludesAsset(board,item={}){
    if(!board || board.system !== false || !item?.id) return false;
    const kinds = new Set(Array.isArray(board.kinds) ? board.kinds : []);
    const extensions = new Set(Array.isArray(board.extensions) ? board.extensions : []);
    const categoryMatch = board.preset === 'other' || kinds.has(String(item.kind || '')) || extensions.has(registryAssetExtension(item));
    if(!categoryMatch) return false;
    const itemSourceId = String(item.storage_root_id || '');
    if(board.sourceBoard) return itemSourceId === String(board.rootId || '');
    const sources = Array.isArray(board.sources) ? board.sources : [];
    const sourceIds = new Set([...sources.map(source => source.id), ...(board.excludedSourceIds || [])].map(String));
    const visibleSourceIds = new Set(sources.filter(source => !source.hidden).map(source => String(source.id)));
    if(sourceIds.has(itemSourceId)) return visibleSourceIds.has(itemSourceId);
    // 【修复资源库归属】未绑定任何可见路径/来源的资源库不再自动收纳全部同类未认领素材：
    // 只显示明确绑定来源的素材，未绑定路径时依赖空状态提示。
    if(!board.sourceBoard && !sources.some(source => !source.hidden)) return false;
    return !board.automaticHidden;
}
function customAssetLibraryBaseItems(board, options={}){
    const kinds = new Set(Array.isArray(board?.kinds) ? board.kinds : []);
    const extensions = new Set(Array.isArray(board?.extensions) ? board.extensions : []);
    // 【QIN-527】机架需要按来源分别计数，忽略当前来源选择才能避免其余来源计数被压成 0
    const ignoreSourceSelection = Boolean(options.ignoreSourceSelection);
    const sourceId = ignoreSourceSelection ? '' : (board?.sourceBoard ? String(board.rootId || '') : (boardSourceSelection[board?.id] || ''));
    const sources = Array.isArray(board?.sources) ? board.sources : [];
    const sourceIds = new Set([...sources.map(source => source.id), ...(board?.excludedSourceIds || [])]);
    const visibleSourceIds = new Set(sources.filter(source => !source.hidden).map(source => source.id));
    return (options.items || registryAssets).filter(item => {
        const categoryMatch = board?.preset === 'other' || kinds.has(String(item.kind || '')) || extensions.has(registryAssetExtension(item));
        if(!categoryMatch) return false;
        const itemSourceId = String(item.storage_root_id || '');
        if(sourceId === '__automatic__') return !board?.automaticHidden && !sourceIds.has(itemSourceId) && Boolean(board?.sourceBoard || sources.some(source => !source.hidden));
        if(sourceId) return visibleSourceIds.has(sourceId) && itemSourceId === sourceId;
        return customAssetLibraryIncludesAsset(board,item);
    });
}
function customBoardFolderIsHidden(boardId, folderPath=''){
    const folder = String(folderPath || '').replace(/\\/g, '/').replace(/^\/+|\/+$/g, '');
    if(!folder) return false;
    return (boardHiddenFolders[boardId] || []).some(hidden => folder === hidden || folder.startsWith(`${hidden}/`));
}
function customBoardFolderRootIds(board, items=[]){
    if(!board) return [];
    const selected = board.sourceBoard ? String(board.rootId || '') : String(boardSourceSelection[board.id] || '');
    if(selected && selected !== '__automatic__') return [selected];
    const ids = new Set();
    if(!board.sourceBoard) (board.sources || []).filter(source => !source.hidden).forEach(source => ids.add(String(source.id || '')));
    (items || []).forEach(item => ids.add(String(item.storage_root_id || '')));
    return [...ids].filter(Boolean);
}
function customBoardCachedFolderEntries(board, items=[]){
    return customBoardFolderRootIds(board, items).flatMap(rootId => registryRootFolders.get(rootId) || []);
}
function customAssetLibraryItems(board){
    const folderPath = boardFolderSelection[board?.id] || '';
    const groupId = boardGroupSelection[board?.id] || '';
    const referenceCanvasId = boardReferenceCanvasSelection[board?.id] || '';
    const group = groupId ? registryBoardCustomGroups(board).find(item => item.id === groupId) : null;
    const groupIds = group ? new Set(group.assetIds || []) : null;
    const referenceIds = referenceCanvasId ? new Set((canvasAssetsData.items || []).filter(item => item.canvas_id === referenceCanvasId).map(item => String(item.asset_id || '')).filter(Boolean)) : null;
    return customAssetLibraryBaseItems(board).filter(item => !customBoardFolderIsHidden(board?.id, registryAssetFolderPath(item)) && registryAssetMatchesFolder(item, folderPath) && (!groupIds || groupIds.has(item.id)) && (!referenceIds || referenceIds.has(item.id)));
}
function customBoardFolderTree(items=[], directories=[], summary=null, category=''){
    const roots = [];
    const map = new Map();
    const ensureFolder = folder => {
        if(!folder) return;
        let parentPath = '';
        folder.split('/').filter(Boolean).forEach((name, index, segments) => {
            const path = segments.slice(0, index + 1).join('/');
            if(!map.has(path)){
                const node = {path, name, count:0, selfCount:0, children:[]};
                map.set(path, node);
                if(parentPath) map.get(parentPath)?.children.push(node);
                else roots.push(node);
            }
            parentPath = path;
        });
    };
    directories.forEach(entry => ensureFolder(typeof entry === 'string' ? entry : entry?.path));
    items.forEach(item => {
        const folder = registryAssetFolderPath(item);
        ensureFolder(folder);
        folder.split('/').filter(Boolean).forEach((_name, index, segments) => {
            const path = segments.slice(0, index + 1).join('/');
            if(map.has(path)){
                map.get(path).count += 1;
                // QIN-542 AC3：素材落在该目录本体时计为本层文件（非递归口径）
                if(index === segments.length - 1) map.get(path).selfCount += 1;
            }
        });
    });
    // QIN-542 AC2 修复：reset 计数前先按全类型口径记录「本目录是否有已索引素材」，
    // 供空目录标记判定（QA 复验缺陷：有文件无子目录的目录曾被误标「空」）
    map.forEach(node => { node.hasIndexedAssets = node.count > 0; });
    if(Array.isArray(summary)){
        map.forEach(node => { node.count = 0; node.selfCount = 0; });
        summary.forEach(entry => {
            const folder = String(entry.path || '');
            ensureFolder(folder);
            // 后端 entry.count 是全类型本层文件数，与看板分类无关；有文件即非空目录
            if(Number(entry.count || 0) > 0){
                const selfNode = map.get(folder);
                if(selfNode) selfNode.hasIndexedAssets = true;
            }
            const selfC = category ? Number(entry.categories?.[category] || 0) : Number(entry.count || 0);
            folder.split('/').filter(Boolean).forEach((_name, index, segments) => {
                const path = segments.slice(0, index + 1).join('/');
                const node = map.get(path);
                if(!node) return;
                node.count += selfC;
                if(index === segments.length - 1) node.selfCount += selfC;
            });
        });
        // QIN-542 AC3：后端 total_count（含子目录递归总数）与累加口径等价，有值时覆盖写入
        if(!category){
            summary.forEach(entry => {
                const node = map.get(String(entry.path || ''));
                if(node && entry.total_count != null) node.count = Number(entry.total_count || 0);
            });
        }
    }
    const sortNodes = nodes => nodes.sort((a,b) => a.name.localeCompare(b.name, 'zh-Hans-CN', {numeric:true, sensitivity:'base'})).forEach(node => sortNodes(node.children));
    sortNodes(roots);
    return roots;
}
function customBoardFolderParentPaths(nodes=[]){
    return nodes.flatMap(node => node.children.length ? [node.path, ...customBoardFolderParentPaths(node.children)] : []);
}
function renderRegistryFilterChips(){
    const tags = new Map((registryFacets.tags || []).map(tag => [tag.id, tag]));
    const chips = [];
    if(registryProjectFilterId && activeTab !== 'projects' && registryProjectFilterId !== '__all__'){
        const proj = projects.find(item => item.id === registryProjectFilterId);
        chips.push(`<button type="button" data-registry-clear="project">项目：${escapeHtml(proj?.name || registryProjectFilterId)} <i data-lucide="x"></i></button>`);
    }
    if(registryStatCategory) chips.push(`<button type="button" data-registry-clear="category">统计：${escapeHtml(registryCategoryLabel(registryStatCategory))} <i data-lucide="x"></i></button>`);
    // 【QIN-553】LUT 视图存在已隐藏的内置条目时，提供一键恢复显示入口。
    // 【QIN-580】LUT 改成独立子页面后，入口条件同时认 category 与 board。
    if(registryBuiltinHiddenIds.size && (registryStatCategory === 'lut' || activeTab === 'lut')){
        chips.push(`<button type="button" data-registry-reveal-builtin>恢复显示内置 LUT（${registryBuiltinHiddenIds.size}）<i data-lucide="eye"></i></button>`);
    }
    if(registryRecentDays) chips.push(`<button type="button" data-registry-clear="recent">最近 ${registryRecentDays} 天 <i data-lucide="x"></i></button>`);
    if(registryKind) chips.push(`<button type="button" data-registry-clear="kind">类型：${escapeHtml(registryKindLabel(registryKind))} <i data-lucide="x"></i></button>`);
    if(registryCollectionId){
        const value = (registryFacets.collections || []).find(item => item.id === registryCollectionId);
        chips.push(`<button type="button" data-registry-clear="collection">集合：${escapeHtml(value?.name || '')} <i data-lucide="x"></i></button>`);
    }
    if(registryRootId){
        const value = (registryFacets.roots || []).find(item => item.id === registryRootId);
        chips.push(`<button type="button" data-registry-clear="root">来源：${escapeHtml(value?.name || '')} <i data-lucide="x"></i></button>`);
    }
    // 【QIN-524】目录层级选中项可一键清除，避免目录过滤被误认为空库
    if(registryNavFolderActive()) chips.push(`<button type="button" data-asset-rack-clear-folder>目录：${escapeHtml(registryNavFolderLabel())} <i data-lucide="x"></i></button>`);
    registryIncludeTags.forEach(id => chips.push(`<button type="button" class="include" data-registry-remove-tag="${escapeAttr(id)}">包含：${escapeHtml(tags.get(id)?.name || id)} <i data-lucide="x"></i></button>`));
    registryExcludeTags.forEach(id => chips.push(`<button type="button" class="exclude" data-registry-remove-tag="${escapeAttr(id)}">排除：${escapeHtml(tags.get(id)?.name || id)} <i data-lucide="x"></i></button>`));
    return chips.length ? `<div class="registry-filter-chips">${chips.join('')}<button type="button" data-registry-clear="all">清空筛选</button></div>` : '';
}
function renderRegistryFacetRow(item, type, activeValue=''){
    const active = String(item.id || item.name) === String(activeValue || '');
    return `<button class="registry-facet-basic ${active ? 'active' : ''}" type="button" data-registry-${type}="${escapeAttr(item.id || item.name || '')}">
        <span>${escapeHtml(type === 'kind' ? registryKindLabel(item.name) : (item.name || '未命名'))}${type === 'root' && item.availability === 'offline' ? ' · 离线' : ''}</span><em>${Number(item.count || 0)}</em>
    </button>`;
}
function renderRegistryProjectFacetSection(){
    const recent = registryRecentProjects();
    const isExpanded = registryProjectFacetExpanded;
    return `<section class="registry-facet-section registry-facet-projects">
        <div class="registry-facet-title">
            <button type="button" class="registry-project-toggle-btn hw-btn ${isExpanded ? 'is-active' : ''}" data-registry-project-toggle aria-expanded="${isExpanded}" title="${isExpanded ? '收起项目素材' : '展开最近项目素材'}">
                <span class="registry-project-toggle-label">
                    <i data-lucide="clapperboard"></i>
                    <strong>项目素材</strong>
                </span>
                <i data-lucide="${isExpanded ? 'chevron-down' : 'chevron-right'}"></i>
            </button>
        </div>
        ${isExpanded ? `
            <div class="registry-project-list" data-registry-project-list>
                ${recent.map(proj => {
                    const active = proj.id === registryProjectFilterId;
                    const count = registryProjectAssetCount(proj);
                    return `<button type="button" class="registry-facet-basic registry-project-facet-item ${active ? 'active' : ''}" data-registry-project-filter="${escapeAttr(proj.id)}" aria-pressed="${active}" title="${escapeAttr(proj.name)}">
                        <span class="registry-project-item-name">
                            <i data-lucide="folder-kanban" class="registry-project-item-icon"></i>
                            <span class="registry-project-item-text">${escapeHtml(proj.name)}</span>
                        </span>
                        <em>${Number(count || 0)}</em>
                    </button>`;
                }).join('') || '<div class="tree-empty registry-project-empty">暂无项目</div>'}
                ${registryProjectFilterId ? `<button type="button" class="hw-btn hw-btn-sm registry-project-clear-btn" data-registry-clear="project" title="清除项目筛选"><i data-lucide="x"></i><span>显示全部资产</span></button>` : ''}
            </div>
        ` : ''}
    </section>`;
}
function registryVisualType(item={}){
    const category = registryAssetCategory(item);
    return category === 'document' ? 'text' : category;
}
function registryMediaAspectStyle(item={}){
    if(!['image','video'].includes(registryVisualType(item))) return '';
    const width = Number(item.width || item.metadata?.width || 0);
    const height = Number(item.height || item.metadata?.height || 0);
    if(!Number.isFinite(width) || !Number.isFinite(height) || width <= 0 || height <= 0) return '';
    return `--asset-media-aspect:${(width / height).toFixed(6)}`;
}
function registryVersionStructureMemberControl(structure, member){
    const asset = registryStructureMemberAsset(structure, member.asset_id);
    if(!asset) return '';
    const current = member.asset_id === structure.current_asset_id;
    const busy = registryStructureMutationId === structure.id;
    return `<button type="button" class="registry-structure-member-control ${current ? 'is-current' : ''}" data-registry-structure-current="${escapeAttr(member.asset_id)}" data-registry-structure-id="${escapeAttr(structure.id)}" title="${current ? '当前最终版本' : `设为最终版本：${escapeAttr(asset.name || '图片')}`}" aria-label="${current ? `当前最终版本：${escapeAttr(asset.name || '图片')}` : `设为最终版本：${escapeAttr(asset.name || '图片')}`}" aria-pressed="${current}" ${current || busy ? 'disabled' : ''}>${deferRegistryPreviewThumb(assetThumb(asset))}<span>${escapeHtml(current ? '当前' : `V${Math.max(1, member.sort_order + 1)}`)}</span></button>`;
}
function renderRegistryVersionStructureRemoveButton(structure, current, className=''){
    const busy = registryStructureMutationId === structure.id || (registryStructureRemoveState.busy && registryStructureRemoveState.structureId === structure.id);
    const name = current?.name || '当前版本结构';
    return `<button type="button" class="registry-structure-remove ${escapeAttr(className)}" data-registry-structure-remove="${escapeAttr(structure.id)}" title="解除版本结构" aria-label="解除版本结构：${escapeAttr(name)}" ${busy ? 'disabled' : ''}><i data-lucide="unlink"></i></button>`;
}
function renderRegistryGroupStructureRemoveButton(structure, current, className=''){
    const busy = registryStructureMutationId === structure.id || (registryStructureRemoveState.busy && registryStructureRemoveState.structureId === structure.id);
    const name = current?.name || '当前图片组';
    return `<button type="button" class="registry-structure-remove registry-structure-group-remove ${escapeAttr(className)}" data-registry-structure-remove="${escapeAttr(structure.id)}" title="解组" aria-label="解组：${escapeAttr(name)}" ${busy ? 'disabled' : ''}><i data-lucide="ungroup"></i></button>`;
}
function renderRegistryStructureCoverflowButton(structure, className=''){
    const label = structure?.structure_type === 'group' ? '打开当前组 Coverflow' : '打开当前版本 Coverflow';
    return `<button type="button" class="registry-structure-coverflow ${escapeAttr(className)}" data-registry-structure-coverflow="${escapeAttr(structure?.id || '')}" title="${label}" aria-label="${label}"><i data-lucide="images"></i></button>`;
}
function renderRegistryGroupStructureStack(structure){
    const members = registryGroupStructureMembers(structure);
    const layers = members.slice(0, 5).map(({asset}, index) => {
        const offset = index * 7;
        const rotation = (index - Math.min(2, members.length - 1) / 2) * 3;
        return `<span class="registry-structure-group-layer" style="--group-layer-index:${index};--group-layer-offset:${offset}px;--group-layer-rotation:${rotation.toFixed(2)}deg">${deferRegistryPreviewThumb(assetThumb(asset))}</span>`;
    }).join('');
    return `<button type="button" class="registry-structure-group-main" data-registry-structure-group-select="${escapeAttr(structure.id)}" aria-label="选择图片组，共 ${members.length} 张图片"><span class="registry-structure-group-stack" aria-hidden="true">${layers}</span></button>`;
}
function renderRegistryVersionStructureCard(item, index=0){
    const structure = item.__registry_asset_structure;
    const current = registryStructureMemberAsset(structure, structure.current_asset_id);
    if(!current) return '';
    const selected = current.id === selectedRegistryAssetId || structure.members.some(member => registrySelectedIds.has(member.asset_id));
    const mediaAspectStyle = registryMediaAspectStyle(current);
    const memberStrip = `<div class="registry-structure-member-strip" role="group" aria-label="切换版本">${structure.members.map(member => registryVersionStructureMemberControl(structure, member)).join('')}</div>`;
    return `<section class="asset-card registry-card registry-structure-card registry-structure-version asset-type-image ${selected ? 'active' : ''}" data-registry-structure-card="${escapeAttr(structure.id)}" data-registry-structure-type="version" data-registry-card="structure:${escapeAttr(structure.id)}" tabindex="0" role="group" aria-label="版本结构：${escapeAttr(current.name || '未命名图片')}">
        <div class="registry-structure-main" data-registry-structure-open="${escapeAttr(structure.id)}" role="button" tabindex="0" aria-label="查看当前版本：${escapeAttr(current.name || '未命名图片')}">
            <div class="asset-thumb"${mediaAspectStyle ? ` style="${mediaAspectStyle}"` : ''}>
                ${deferRegistryPreviewThumb(assetThumb(current), Number(index) < 24)}
                ${memberStrip}
            </div>
        </div>
        ${renderRegistryVersionStructureRemoveButton(structure, current)}
        ${renderRegistryStructureCoverflowButton(structure, 'registry-structure-version-coverflow')}
        <div class="asset-card-body">
            <div class="registry-card-caption">
                <div class="asset-card-name" data-registry-card-name title="${escapeAttr(current.name || '')}">${escapeHtml(current.name || '未命名图片')}</div>
                <span class="registry-card-measure">${escapeHtml(formatFileSize(current.size || 0))}</span>
            </div>
            <div class="asset-card-meta"><span>${structure.members.length} 个版本</span><span>${escapeHtml(formatDate(current.created_at))}</span></div>
            <div class="registry-card-source" title="${escapeAttr(registrySourceNames(current).join(' · '))}"><i data-lucide="${registrySourceNames(current).length > 1 ? 'database-zap' : 'database'}"></i><span>${escapeHtml(registrySourceNames(current).join(' · '))}${registryRootIsOffline(current) ? ' · 离线' : ''}</span>${registrySourceNames(current).length > 1 ? `<em>${registrySourceNames(current).length} 库</em>` : ''}</div>
        </div>
    </section>`;
}
function renderRegistryGroupStructureCard(item, index=0){
    const structure = item.__registry_asset_structure;
    const members = registryGroupStructureMembers(structure);
    const cover = registryStructureMemberAsset(structure, structure.current_asset_id) || members[0]?.asset;
    if(!cover || members.length < 2) return '';
    const selected = members.some(value => registrySelectedIds.has(value.asset.id));
    const name = cover.name || '图片组';
    return `<section class="asset-card registry-card registry-structure-card registry-structure-group asset-type-image ${selected ? 'selected' : ''}" data-registry-structure-card="${escapeAttr(structure.id)}" data-registry-structure-type="group" data-registry-card="structure:${escapeAttr(structure.id)}" tabindex="0" role="group" aria-label="组结构：${escapeAttr(name)}，共 ${members.length} 张图片">
        ${renderRegistryGroupStructureStack(structure)}
        ${renderRegistryGroupStructureRemoveButton(structure, cover, 'registry-structure-group-remove-card')}
        ${renderRegistryStructureCoverflowButton(structure, 'registry-structure-group-coverflow')}
        <div class="asset-card-body">
            <div class="registry-card-caption">
                <div class="asset-card-name" data-registry-card-name title="${escapeAttr(name)}">${escapeHtml(name)}</div>
                <span class="registry-card-measure">${members.length} 张图片</span>
            </div>
            <div class="asset-card-meta"><span>图片组</span><span>${escapeHtml(formatDate(cover.created_at))}</span></div>
            <div class="registry-card-source" title="${escapeAttr(registrySourceNames(cover).join(' · '))}"><i data-lucide="${registrySourceNames(cover).length > 1 ? 'database-zap' : 'database'}"></i><span>${escapeHtml(registrySourceNames(cover).join(' · '))}${registryRootIsOffline(cover) ? ' · 离线' : ''}</span>${registrySourceNames(cover).length > 1 ? `<em>${registrySourceNames(cover).length} 库</em>` : ''}</div>
        </div>
    </section>`;
}
function renderRegistryVersionStructureListRow(item){
    const structure = item.__registry_asset_structure;
    const current = registryStructureMemberAsset(structure, structure.current_asset_id);
    if(!current) return '';
    return `<article class="registry-list-row registry-structure-list-row asset-type-image" data-registry-structure-card="${escapeAttr(structure.id)}" data-registry-structure-type="version" tabindex="0" role="row" aria-label="版本结构：${escapeAttr(current.name || '未命名图片')}">
        <span role="cell"><i data-lucide="git-branch"></i></span>
        <span class="registry-list-name" role="cell"><button type="button" class="registry-structure-list-open" data-registry-structure-open="${escapeAttr(structure.id)}"><span class="registry-list-thumb">${deferRegistryPreviewThumb(assetThumb(current))}</span><strong title="${escapeAttr(current.name || '')}">${escapeHtml(current.name || '未命名图片')}</strong></button></span>
        <span class="registry-list-type" role="cell"><i data-lucide="git-branch"></i>版本结构</span>
        <span role="cell">${structure.members.length} 个版本</span>
        <span role="cell">${escapeHtml(formatDate(structure.updated_at || current.updated_at || current.created_at))}</span>
        <span class="registry-structure-list-members" role="cell">${structure.members.map(member => registryVersionStructureMemberControl(structure, member)).join('')}</span>
        <span class="registry-list-path registry-structure-list-path" role="cell"><span>${escapeHtml(registrySourceNames(current).join(' · '))}</span>${renderRegistryStructureCoverflowButton(structure, 'registry-structure-list-coverflow')}${renderRegistryVersionStructureRemoveButton(structure, current, 'registry-structure-list-remove')}</span>
    </article>`;
}
function renderRegistryGroupStructureListRow(item){
    const structure = item.__registry_asset_structure;
    const members = registryGroupStructureMembers(structure);
    const cover = registryStructureMemberAsset(structure, structure.current_asset_id) || members[0]?.asset;
    if(!cover || members.length < 2) return '';
    const name = cover.name || '图片组';
    return `<article class="registry-list-row registry-structure-list-row registry-structure-group-list-row asset-type-image" data-registry-structure-card="${escapeAttr(structure.id)}" data-registry-structure-type="group" data-registry-card="structure:${escapeAttr(structure.id)}" tabindex="0" role="row" aria-label="组结构：${escapeAttr(name)}，共 ${members.length} 张图片">
        <span role="cell"><i data-lucide="layers-2"></i></span>
        <span class="registry-list-name" role="cell"><button type="button" class="registry-structure-list-open registry-structure-group-list-toggle" data-registry-structure-group-select="${escapeAttr(structure.id)}"><span class="registry-list-thumb registry-structure-group-list-thumb">${deferRegistryPreviewThumb(assetThumb(cover))}</span><strong title="${escapeAttr(name)}">${escapeHtml(name)}</strong></button></span>
        <span class="registry-list-type" role="cell"><i data-lucide="layers-2"></i>组结构</span>
        <span role="cell">${members.length} 张图片</span>
        <span role="cell">${escapeHtml(formatDate(structure.updated_at || cover.updated_at || cover.created_at))}</span>
        <span class="registry-structure-list-members registry-structure-group-list-members" role="cell"><small>双击打开 Coverflow</small></span>
        <span class="registry-list-path registry-structure-list-path" role="cell"><span>${escapeHtml(registrySourceNames(cover).join(' · '))}</span>${renderRegistryStructureCoverflowButton(structure, 'registry-structure-list-coverflow')}${renderRegistryGroupStructureRemoveButton(structure, cover, 'registry-structure-list-remove')}</span>
    </article>`;
}
function renderRegistryCard(item, index=0){
    if(item?.__registry_asset_structure){
        return item.__registry_asset_structure.structure_type === 'group'
            ? renderRegistryGroupStructureCard(item, index)
            : renderRegistryVersionStructureCard(item, index);
    }
    const selected = registrySelectedIds.has(item.id);
    const name = item.name || 'asset';
    const isLut = String(item.kind || item.media_type || item.type || '').toLowerCase() === 'lut'
        || registryAssetExtension(item).toLowerCase() === 'cube';
    const format = isLut ? 'LUT' : (registryAssetExtension(item).toUpperCase() || 'FILE');
    const referenceCanvasId = boardReferenceCanvasSelection[activeTab] || '';
    const mediaAspectStyle = registryMediaAspectStyle(item);
    const matchedSegment = item.kind === 'video' ? findMatchedVideoSegment(item, registryQuery) : null;
    if (item.kind === 'video' && typeof window !== 'undefined' && !window.MediaPreviewPlayer) {
        ensureMediaPreviewPlayer();
    }
    const fps = Number(item.frame_rate || item.metadata?.frame_rate || 0);
    const segmentBadge = matchedSegment ? `<span class="registry-card-segment-badge" data-video-jump-sec="${matchedSegment.start_sec}" data-video-jump-end="${matchedSegment.end_sec}" data-video-asset-id="${escapeAttr(item.id)}" data-video-frame-rate="${fps || ''}" title="命中片段：${escapeAttr(formatVideoSegmentRange(matchedSegment.start_sec, matchedSegment.end_sec))} ${escapeAttr(Array.isArray(matchedSegment.tags)?matchedSegment.tags.join(' · '):'')}"><i data-lucide="play-circle"></i><strong>${escapeHtml(formatVideoSegmentRange(matchedSegment.start_sec, matchedSegment.end_sec))}</strong></span>` : '';
    // 【QIN-541】第二行为灰色单行「大小 · 尺寸」：无值字段直接省略，两字段皆无时整行省略。
    const measureParts = [];
    if(Number(item.size || 0) > 0) measureParts.push(formatFileSize(item.size));
    const sublineWidth = Number(item.width || item.metadata?.width || 0);
    const sublineHeight = Number(item.height || item.metadata?.height || 0);
    if(sublineWidth > 0 && sublineHeight > 0) measureParts.push(`${sublineWidth}×${sublineHeight}`);
    const subline = measureParts.length ? `<span class="registry-card-subline">${escapeHtml(measureParts.join(' · '))}</span>` : '';
    const cardBody = registryAspect === 'square'
        ? `<div class="asset-card-body">
            <div class="registry-card-caption"><div class="asset-card-name" data-registry-card-name title="${escapeAttr(name)}">${escapeHtml(name)}</div><span class="registry-card-measure">${escapeHtml(item.kind === 'video' ? formatMediaDuration(item.duration || item.metadata?.duration) : formatFileSize(item.size || 0))}</span></div>
            <div class="asset-card-meta"><span>${escapeHtml(registryAssetTypeLabel(item))}</span><span>${escapeHtml(formatDate(item.created_at))}</span></div>
            <div class="registry-card-source" title="${escapeAttr(registrySourceNames(item).join(' · '))}"><i data-lucide="${registrySourceNames(item).length > 1 ? 'database-zap' : 'database'}"></i><span>${escapeHtml(registrySourceNames(item).join(' · '))}${registryRootIsOffline(item) ? ' · 离线' : ''}</span>${registrySourceNames(item).length > 1 ? `<em>${registrySourceNames(item).length} 库</em>` : ''}</div>
        </div>`
        : `<div class="asset-card-body">
            <div class="asset-card-name" data-registry-card-name title="${escapeAttr(name)}">${escapeHtml(name)}</div>
            <div class="registry-card-caption">${subline}${registryCardSourceBadge(item, assetBoardById(activeTab))}</div>
        </div>`;
    return `<article class="asset-card registry-card asset-type-${registryVisualType(item)} ${item.id === selectedRegistryAssetId ? 'active' : ''} ${selected ? 'selected' : ''}" data-registry-card="${escapeAttr(item.id)}" draggable="true" data-registry-drag="${escapeAttr(item.id)}" ${referenceCanvasId ? `data-reference-canvas-open="${escapeAttr(referenceCanvasId)}"` : ''} tabindex="0" role="button" aria-label="查看资产 ${escapeAttr(name)}">
        <input class="asset-card-check" type="checkbox" aria-label="选择 ${escapeAttr(name)}" data-registry-check="${escapeAttr(item.id)}" ${selected ? 'checked' : ''}>
        <span class="registry-card-format" aria-label="文件格式 ${escapeAttr(format)}">${escapeHtml(format)}</span>
        ${renderRegistryRecycleButton(item)}
        <div class="asset-thumb"${mediaAspectStyle ? ` style="${mediaAspectStyle}"` : ''}>${deferRegistryPreviewThumb(assetThumb(item), Number(index) < 24)}${segmentBadge}<span class="registry-card-selection-overlay" aria-hidden="true"></span></div>
        ${cardBody}
    </article>`;
}
const REGISTRY_TAG_RECOMMENDATIONS = {
    image:['参考图','构图','角色','场景','材质','可商用'],
    video:['成片','素材','镜头','转场','待审核'],
    audio:['音乐','音效','环境音','配音','节奏点'],
    document:['脚本','方案','字幕','合同','说明'],
    text:['脚本','方案','字幕','说明'],
    model:['角色模型','场景模型','道具','材质','待优化'],
    font:['标题字','正文字','品牌字','可商用'],
    other:['参考','归档','待整理'],
};
function registrySelectedAssets(board=assetBoardById(activeTab)){
    const ids = new Set(registryActiveSelectedIds(board));
    // Selection must resolve against the currently visible library. Custom
    // libraries are filtered views over the same registry records, but using
    // the global list here drops their selected items from structure actions.
    const source = board?.system === false ? customAssetLibraryItems(board) : registryAssets;
    return source.filter(item => ids.has(item.id));
}
function registrySuggestedTags(items=[]){
    const suggestions = [];
    items.forEach(item => {
        const visual = detailViewerType('registry', item);
        (REGISTRY_TAG_RECOMMENDATIONS[visual] || REGISTRY_TAG_RECOMMENDATIONS[item.kind] || REGISTRY_TAG_RECOMMENDATIONS.other).forEach(tag => suggestions.push(tag));
    });
    return [...new Set(suggestions)].slice(0, 12);
}
function renderRegistryBulkPanel(board=null){
    const selected = registrySelectedAssets(board);
    if(!selected.length || !registryBulkPanel) return '';
    if(registryBulkPanel === 'canvas'){
        return `<div class="registry-bulk-panel registry-canvas-panel">
            <div class="registry-bulk-panel-title"><span><i data-lucide="layout-dashboard"></i><strong>加入画布</strong></span><button type="button" data-registry-bulk-close aria-label="关闭"><i data-lucide="x"></i></button></div>
            <div class="registry-canvas-target-row"><label><span>已有画布</span><select id="registryCanvasTarget" ${registryCanvasLoading ? 'disabled' : ''}><option value="">${registryCanvasLoading ? '正在读取…' : '选择画布'}</option>${registryCanvasTargets.map(canvas => `<option value="${escapeAttr(canvas.id)}">${escapeHtml(canvas.title || '未命名画布')} · ${escapeHtml(canvasKindLabel(canvas.kind))}</option>`).join('')}</select></label><button class="hw-btn hw-btn-gold" type="button" data-registry-add-to-canvas ${registryCanvasLoading || !registryCanvasTargets.length ? 'disabled' : ''}><i data-lucide="plus"></i><span>加入已有画布</span></button></div>
            <div class="registry-canvas-target-row"><label><span>新画布名称</span><input id="registryNewCanvasTitle" type="text" value="资产画布" maxlength="80"></label><label class="compact"><span>类型</span><select id="registryNewCanvasKind"><option value="classic">普通画布</option><option value="smart">智能画布</option><option value="reference">参考画布</option></select></label><button class="hw-btn" type="button" data-registry-create-canvas><i data-lucide="panel-top-open"></i><span>新建并加入</span></button></div>
        </div>`;
    }
    if(registryBulkPanel === 'tags'){
        const tags = registrySuggestedTags(selected);
        return `<div class="registry-bulk-panel registry-tags-panel">
            <div class="registry-bulk-panel-title"><span><i data-lucide="tags"></i><strong>添加标签</strong></span><button type="button" data-registry-bulk-close aria-label="关闭"><i data-lucide="x"></i></button></div>
            <div class="registry-tag-suggestions"><span>智能推荐</span>${tags.map(tag => `<button type="button" data-registry-tag-suggestion="${escapeAttr(tag)}">${escapeHtml(tag)}</button>`).join('')}</div>
            <div class="registry-tag-entry"><input id="registryBulkTagInput" type="text" placeholder="输入标签，多个标签用逗号分隔" maxlength="500"><button class="hw-btn hw-btn-gold" type="button" data-registry-tags-save><i data-lucide="plus"></i><span>添加到 ${selected.length} 项</span></button></div>
        </div>`;
    }
    return '';
}
// 【QIN-534】底部批量抽屉：数量汇总改暗金硬件徽标，操作按钮组接入标准黑金硬件微胶囊（hw-btn），
// 关闭按钮保留暗金微型图标；底色与描边全部交给 CSS 的黑金毛玻璃容器。
function renderRegistrySelectionBar(board=null){
    const selected = registrySelectedAssets(board);
    if(!selected.length) return '';
    const totalSize = selected.reduce((total,item) => total + Number(item.size || 0), 0);
    // 【QIN-534】版本/组结构只对「至少两张且全为图片」的选择开放；不可用时禁用并说明原因。
    const imageOnly = selected.length >= 2 && selected.every(isRegistryStructureImage);
    const mergeDisabled = imageOnly ? '' : ' disabled';
    const mergeTitle = imageOnly ? '' : ' title="仅支持至少两张已选图片"';
    // 【QIN-534 反馈 4】底部批量栏与右键菜单同口径：按素材当前状态切换「合并 / 解除」。
    const selectedIds = selected.map(item => item.id);
    const owning = registryOwningStructures(selectedIds);
    const related = registrySelectionRelationState(selectedIds);
    const versionLabel = owning.version ? '解除版本' : '合并为版本';
    const groupLabel = owning.group ? '解除组' : '合并为组';
    const relationLabel = related ? '解除关联' : '建立关联';
    const versionIcon = owning.version ? 'unlink' : 'git-branch';
    const groupIcon = owning.group ? 'unlink' : 'layers-2';
    const relationIcon = related ? 'unlink' : 'link-2';
    return `<div class="registry-selection-dock">
        ${renderRegistryBulkPanel(board)}
        <div class="registry-selection-bar">
            <div class="registry-selection-summary"><span class="registry-selection-badge"><i data-lucide="check-check"></i>${selected.length}</span><strong>已选素材</strong><small>${escapeHtml(formatFileSize(totalSize))}</small></div>
            <div class="registry-selection-actions">
                <button class="hw-btn${registryBulkPanel === 'canvas' ? ' is-active' : ''}" type="button" data-registry-bulk-panel="canvas"><i data-lucide="layout-dashboard"></i><span>加入画布</span></button>
                <button class="hw-btn${registryBulkPanel === 'tags' ? ' is-active' : ''}" type="button" data-registry-bulk-panel="tags"><i data-lucide="tags"></i><span>添加标签</span></button>
                <button class="hw-btn" type="button" data-registry-${owning.version ? 'bulk-release' : 'structure-merge'}="version"${owning.version ? '' : mergeDisabled}${owning.version ? '' : mergeTitle}><i data-lucide="${versionIcon}"></i><span>${versionLabel}</span></button>
                <button class="hw-btn" type="button" data-registry-${owning.group ? 'bulk-release' : 'structure-merge'}="group"${owning.group ? '' : mergeDisabled}${owning.group ? '' : mergeTitle}><i data-lucide="${groupIcon}"></i><span>${groupLabel}</span></button>
                <button class="hw-btn" type="button" data-registry-${related ? 'bulk-release' : 'relate'}="relation"${selected.length < 2 ? 'disabled' : ''}><i data-lucide="${relationIcon}"></i><span>${relationLabel}</span></button>
                <button class="hw-btn" type="button" data-registry-archive ${registryArchiveBusy ? 'disabled' : ''}><i data-lucide="package"></i><span>打包导出</span></button>
                <button class="hw-btn" type="button" data-registry-export-pdf ${registryPdfBusy ? 'disabled' : ''}><i data-lucide="file-down"></i><span>导出 PDF</span></button>
            </div>
            <button class="registry-selection-clear" type="button" data-registry-clear-selection title="清空选择"><i data-lucide="x"></i></button>
        </div>
    </div>`;
}
// Selection changes should not rebuild the registry grid: doing so discards
// already loaded thumbnails and makes a simple click look like a refresh.
function syncRegistrySelectionDom(){
    const board = assetBoardById(activeTab)?.system === false ? assetBoardById(activeTab) : null;
    root.querySelectorAll('[data-registry-card]').forEach(card => {
        const id = card.dataset.registryCard || '';
        card.classList.toggle('active', id === selectedRegistryAssetId);
        card.classList.toggle('selected', registrySelectedIds.has(id));
        card.setAttribute('aria-pressed', id === selectedRegistryAssetId ? 'true' : 'false');
        const checkbox = card.querySelector('[data-registry-check]');
        if(checkbox) checkbox.checked = registrySelectedIds.has(id);
    });
    root.querySelectorAll('[data-registry-structure-card]').forEach(card => {
        const structure = registryAssetStructures.get(card.dataset.registryStructureCard || '');
        const active = Boolean(structure?.current_asset_id && structure.current_asset_id === selectedRegistryAssetId);
        const selectedGroupMember = structure?.structure_type === 'group'
            && registryGroupStructureMembers(structure).some(value => registrySelectedIds.has(value.asset.id));
        card.classList.toggle('active', active);
        card.classList.toggle('selected', selectedGroupMember);
        card.setAttribute('aria-current', active ? 'true' : 'false');
    });
    const content = root.querySelector('.registry-content');
    if(content) content.classList.toggle('manage-on', registrySelectedIds.size > 0);
    const selectionDock = root.querySelector('.registry-selection-dock');
    if(registrySelectedIds.size){
        const html = renderRegistrySelectionBar(board);
        if(selectionDock) selectionDock.outerHTML = html;
        else content?.insertAdjacentHTML('beforeend', html);
    } else selectionDock?.remove();
    const detailPanel = root.querySelector('.registry-detail-panel');
    if(detailPanel && !registryDetailPanelCollapsed){
        detailPanel.innerHTML = renderRegistryDetail();
        // 【QIN-554】右栏换选素材会新建 LUT 缩略 canvas，需单独调度绘制，避免留下空框。
        if(detailPanel.querySelector('canvas[data-lut-thumb-url]')) gwCall('lut', 'scheduleRegistryLutThumbs');
    }
    refreshIcons();
}
function clearRegistrySelectionDom(){
    if(!selectedRegistryAssetId && !registryDetail && !registrySelectedIds.size && !registryManageMode && !registryBulkPanel) return false;
    selectedRegistryAssetId = '';
    registryDetail = null;
    registrySelectedIds.clear();
    registrySelectionAnchorId = '';
    registryManageMode = false;
    registryBulkPanel = '';
    syncRegistrySelectionDom();
    return true;
}
// 【QIN-531 修复】原生 <select> 的系统下拉无法控制圆角、悬浮蓝底与焦点方框，
// 排序/分组改为自绘 listbox：胶囊整体是触发器，菜单圆角、金色悬浮态、无三角箭头。
const REGISTRY_SELECT_OPTIONS = {
    sort:[{value:'newest', label:'最近新增'}, {value:'oldest', label:'最早新增'}, {value:'name', label:'名称'}, {value:'size_desc', label:'大小降序'}],
    group:[{value:'none', label:'不分组'}, {value:'source', label:'素材来源'}, {value:'folder', label:'文件夹'}, {value:'custom', label:'自定义分组'}],
};
function registrySelectOptions(kind){
    if(kind === 'project'){
        return [
            {value:'__all__', label:'全部项目'},
            ...projects.map(p => ({value:String(p.id || ''), label:p.name || p.id}))
        ];
    }
    return REGISTRY_SELECT_OPTIONS[kind] || REGISTRY_SELECT_OPTIONS.sort;
}
function registrySelectCurrentValue(kind){
    if(kind === 'project') return registryProjectFilterId || '__all__';
    return kind === 'sort' ? (registrySort || 'newest') : (registryGroupMode || 'none');
}
function registrySelectCurrentLabel(kind){
    const value = registrySelectCurrentValue(kind);
    return (registrySelectOptions(kind).find(option => option.value === value) || registrySelectOptions(kind)[0]).label;
}
function renderRegistrySelectCapsule({kind, icon, label}){
    const current = registrySelectCurrentValue(kind);
    if(kind === 'project'){
        return `<label class="hw-btn registry-toolbar-select" data-registry-select-capsule="${kind}"><i data-lucide="${icon}"></i><span>${label}</span><button class="registry-select-trigger" id="registryProjectSelect" type="button" data-registry-select-trigger="${kind}" aria-haspopup="listbox" aria-expanded="false" aria-label="${escapeAttr(label)}：${escapeAttr(registrySelectCurrentLabel(kind))}">${escapeHtml(registrySelectCurrentLabel(kind))}</button><div class="registry-select-menu" role="listbox" aria-label="${escapeAttr(label)}" hidden>${registrySelectOptions(kind).map(option => `<button class="registry-select-option${option.value === current ? ' is-selected' : ''}" type="button" role="option" data-registry-select-kind="${kind}" data-registry-select-option="${escapeAttr(option.value)}" aria-selected="${option.value === current}">${escapeHtml(option.label)}</button>`).join('')}</div></label>`;
    }
    return `<label class="hw-btn registry-toolbar-select" data-registry-select-capsule="${kind}"><i data-lucide="${icon}"></i><span>${label}</span><button class="registry-select-trigger" id="${kind === 'sort' ? 'registrySort' : 'registryGroupMode'}" type="button" data-registry-select-trigger="${kind}" aria-haspopup="listbox" aria-expanded="false" aria-label="${escapeAttr(label)}：${escapeAttr(registrySelectCurrentLabel(kind))}">${escapeHtml(registrySelectCurrentLabel(kind))}</button><div class="registry-select-menu" role="listbox" aria-label="${escapeAttr(label)}" hidden>${registrySelectOptions(kind).map(option => `<button class="registry-select-option${option.value === current ? ' is-selected' : ''}" type="button" role="option" data-registry-select-kind="${kind}" data-registry-select-option="${escapeAttr(option.value)}" aria-selected="${option.value === current}">${escapeHtml(option.label)}</button>`).join('')}</div></label>`;
}
function closeRegistrySelectMenus(){
    root.querySelectorAll('.registry-toolbar-select.is-open').forEach(node => {
        node.classList.remove('is-open');
        node.querySelector('[data-registry-select-trigger]')?.setAttribute('aria-expanded', 'false');
        const menu = node.querySelector('.registry-select-menu');
        if(menu) menu.hidden = true;
    });
}
function toggleRegistrySelectMenu(kind){
    const capsule = root.querySelector(`[data-registry-select-capsule="${CSS.escape(kind)}"]`);
    if(!capsule) return;
    const willOpen = !capsule.classList.contains('is-open');
    closeRegistrySelectMenus();
    if(!willOpen) return;
    capsule.classList.add('is-open');
    capsule.querySelector('[data-registry-select-trigger]')?.setAttribute('aria-expanded', 'true');
    const menu = capsule.querySelector('.registry-select-menu');
    if(menu) menu.hidden = false;
}
function applyRegistrySelectValue(kind, value){
    closeRegistrySelectMenus();
    if(kind === 'project'){
        const next = String(value || '__all__');
        if(next === (registryProjectFilterId || '__all__')) return;
        registryProjectFilterId = next;
        if(next !== '__all__') activeProjectId = next;
        registryProjectEntityFilter = null;
        render();
        refreshRegistryAssets(false).then(() => { if(activeTab === 'projects') render(); }).catch(err => setStatus(err.message || '项目素材加载失败'));
        return;
    }
    if(kind === 'sort'){
        const next = String(value || 'newest');
        if(next === (registrySort || 'newest')) return;
        registrySort = next;
        refreshRegistryAssets().catch(err => setStatus(err.message || '排序失败'));
        return;
    }
    if(!['none', 'source', 'folder', 'custom'].includes(value) || value === registryGroupMode) return;
    registryGroupMode = value;
    gwCall('settings', 'saveRegistryViewSettings');
    render();
}
function renderRegistryToolbar(board, title, subtitle, options={}){
    const selectedIds = registryActiveSelectedIds(board);
    const selectedGroupIds = selectedRegistryCustomGroupIds(board);
    const searchLabel = board?.name || '全部资产';
    const coverflowAvailable = registryCoverflowAssets(board).length > 0;
    const coverflowLabel = coverflowAvailable ? '打开当前素材库 Coverflow' : '当前素材库没有图片或视频';
    const coverflowControl = `<button class="hw-btn registry-coverflow-open" type="button" data-registry-coverflow-open ${coverflowAvailable ? '' : 'disabled'} aria-label="${coverflowLabel}" title="${coverflowLabel}"><i data-lucide="images"></i><span>Coverflow</span></button>`;
    const embeddedVault = document.documentElement.classList.contains('vault-shell-embedded');
    let shellUnassignedSource = null;
    if(embeddedVault){
        try { shellUnassignedSource = window.parent.document.getElementById('btnUnassignedInbox'); }
        catch (_) { shellUnassignedSource = null; }
    }
    const shellUnassignedCount = shellUnassignedSource?.querySelector('[data-unassigned-count]')?.textContent || '';
    const shellUnassignedVisibility = shellUnassignedSource?.style.display === 'none' ? ' style="display:none"' : '';
    const groupActions = embeddedVault ? `<div class="registry-toolbar-group-actions">
        <button class="hw-btn" type="button" data-registry-group-selected ${selectedIds.length ? '' : 'disabled'}><i data-lucide="folder-plus"></i><span>加入分组</span></button>
        <button class="hw-btn" type="button" data-registry-group-remove ${selectedGroupIds.length ? '' : 'disabled'}><i data-lucide="folder-minus"></i><span>移出分组</span></button>
        <div class="registry-toolbar-shell-actions" aria-label="资产库操作">
            <button class="hw-btn" type="button" data-vault-shell-unassigned${shellUnassignedVisibility} onclick="parent.GWUnassignedInbox.open()" title="尚未归属任何项目的资产（仅本人可见）"><i data-lucide="inbox"></i><span>未归属</span><span data-unassigned-count>${escapeHtml(shellUnassignedCount)}</span></button>
            <button class="hw-btn" type="button" data-vault-shell-settings onclick="parent.WorkbenchAssets.openAssetSettings()" title="打开资产库设置"><i data-lucide="settings-2"></i><span>资产设置</span></button>
        </div>
    </div>` : `<button class="hw-btn" type="button" data-registry-group-selected ${selectedIds.length ? '' : 'disabled'}><i data-lucide="folder-plus"></i><span>加入分组</span></button>
            <button class="hw-btn" type="button" data-registry-group-remove ${selectedGroupIds.length ? '' : 'disabled'}><i data-lucide="folder-minus"></i><span>移出分组</span></button>`;
    const governanceTotal = governanceSuggestionTotal();
    const governanceBadge = governanceSuggestionsError ? '!' : governanceSuggestionsLoading ? '…' : governanceSuggestionsData ? String(governanceTotal) : '—';
    const governanceTrigger = '<button class="hw-btn governance-topbar-entry' + (governanceSuggestionsError ? ' has-error' : '') + '" type="button" data-governance-open aria-haspopup="dialog" aria-expanded="' + String(governanceDrawerOpen) + '" aria-label="' + escapeAttr(governanceTriggerLabel()) + '" title="' + escapeAttr(governanceTriggerLabel()) + '"><i data-lucide="list-checks"></i><span>治理建议</span><span class="governance-topbar-count" data-governance-total>' + escapeHtml(governanceBadge) + '</span></button>';
    if(embeddedVault && activeTab === 'scripts') options.hideHeading = true;
    const hideHeading = Boolean(options.hideHeading || (embeddedVault && ['prompts', 'canvas-assets', 'workflows'].includes(activeTab)));
    return `<div class="content-toolbar registry-toolbar">
        ${options.hideHeading ? '' : (hideHeading ? '' : `<div class="content-heading"><strong>${escapeHtml(title)}</strong><span>${escapeHtml(subtitle)}</span></div>`)}
        <div class="registry-toolbar-controls">
            ${!board && document.documentElement.classList.contains('vault-shell-embedded') ? '<button class="hw-btn hw-btn-icon vault-filters-toggle" type="button" popovertarget="registryFacetPopover" title="目录层级" aria-label="目录层级"><i data-lucide="folder-tree"></i></button>' : ''}
            ${governanceTrigger}${coverflowControl}
            <button class="hw-btn registry-refresh" type="button" data-refresh-assets title="刷新资产" aria-label="刷新资产"><i data-lucide="refresh-cw"></i><span>刷新</span></button>
            <label class="asset-search-wrap"><i data-lucide="search"></i><input id="registrySearch" class="asset-search" type="search" aria-label="搜索 ${escapeAttr(searchLabel)}" value="${escapeAttr(registryQuery)}" placeholder="搜索文件名 / 标签 / ID"></label>
            <div class="registry-layout-switch btn-segmented-deck" role="group" aria-label="显示方式"><button class="btn-segmented-item btn-size-sm hw-btn-icon ${registryLayout === 'grid' ? 'is-active' : ''}" type="button" data-registry-layout="grid" title="方格视图" aria-label="方格视图" aria-pressed="${registryLayout === 'grid'}"><i data-lucide="grid-2x2"></i></button><button class="btn-segmented-item btn-size-sm hw-btn-icon ${registryLayout === 'list' ? 'is-active' : ''}" type="button" data-registry-layout="list" title="列表视图" aria-label="列表视图" aria-pressed="${registryLayout === 'list'}"><i data-lucide="list"></i></button></div>
            ${activeTab === 'projects' ? renderRegistrySelectCapsule({kind:'project', icon:'folder-kanban', label:'项目'}) : ''}
            ${renderRegistrySelectCapsule({kind:'sort', icon:'arrow-down-wide-narrow', label:'排序'})}
            ${renderRegistrySelectCapsule({kind:'group', icon:'layers', label:'分组'})}
            <button class="hw-btn" type="button" data-registry-aspect-toggle aria-pressed="${registryAspect === 'square'}" title="${registryAspect === 'square' ? '当前方格显示，点击恢复原始比例' : '当前原始比例，点击切换方格显示'}"><i data-lucide="square"></i><span>方格</span></button>
            <button class="hw-btn registry-toggle ${registrySimple ? 'is-active' : ''}" type="button" data-registry-simple-toggle aria-pressed="${registrySimple}"><i data-lucide="image"></i><span>简洁</span></button>
            ${options.allowUrl ? '<button class="hw-btn" type="button" data-registry-add-url><i data-lucide="link"></i><span>添加 URL</span></button>' : ''}
            ${groupActions}
        </div>
    </div>`;
}
function renderRegistryEditableTags(item={}, context='side'){
    const tags = Array.isArray(item.tags) ? item.tags : [];
    const editing = registryTagEditorAssetId === item.id;
    const suggestions = registrySuggestedTags([item]).slice(0, 6);
    return `<div class="registry-detail-block registry-editable-tags"><div class="registry-detail-heading"><strong>标签</strong><button type="button" data-registry-tag-editor="${escapeAttr(item.id || '')}" title="添加标签"><i data-lucide="plus"></i></button></div>
        <div class="registry-tag-chip-list">${tags.map(tag => `<span class="registry-tag-chip"><em>${escapeHtml(tag.name || tag.tag || String(tag))}</em>${tag.id ? `<button type="button" data-registry-tag-delete="${escapeAttr(tag.id)}" data-asset-id="${escapeAttr(item.id || '')}" title="删除标签 ${escapeAttr(tag.name || '')}" aria-label="删除标签 ${escapeAttr(tag.name || '')}"><i data-lucide="x"></i></button>` : ''}</span>`).join('') || '<span class="registry-tag-empty">暂无标签</span>'}</div>
        ${editing ? `<div class="registry-detail-tag-presets">${suggestions.map(tag => `<button type="button" data-registry-tag-preset="${escapeAttr(tag)}" data-asset-id="${escapeAttr(item.id || '')}">${escapeHtml(tag)}</button>`).join('')}</div><div class="registry-inline-tag-editor"><input id="registryTagInput-${escapeAttr(context)}" type="text" placeholder="输入标签，多个用逗号分隔" maxlength="500" data-registry-tag-input="${escapeAttr(item.id || '')}"><button class="hw-btn hw-btn-gold" type="button" data-registry-tag-save="${escapeAttr(item.id || '')}" data-tag-context="${escapeAttr(context)}"><i data-lucide="check"></i><span>添加</span></button><button class="hw-btn" type="button" data-registry-tag-editor=""><span>取消</span></button></div>` : ''}
    </div>`;
}
function renderRegistryRelations(item={}){
    const related = Array.isArray(item.related_assets) ? item.related_assets : [];
    return `<div class="registry-detail-block registry-related-block"><div class="registry-detail-heading"><strong>关联素材</strong><span>${related.length}</span></div>
        <div class="registry-related-list">${related.map(asset => `<article><button class="registry-related-main" type="button" data-registry-related-open="${escapeAttr(asset.id || '')}"><span class="registry-related-thumb">${assetThumb({...asset,url:asset.url || asset.public_url || ''})}</span><span><strong>${escapeHtml(asset.name || '未命名素材')}</strong><small>${escapeHtml(registryAssetTypeLabel(asset))}</small></span></button><button class="registry-related-remove" type="button" data-registry-relation-delete="${escapeAttr(asset.id || '')}" data-asset-id="${escapeAttr(item.id || '')}" title="解除关联"><i data-lucide="unlink"></i></button></article>`).join('') || '<div class="registry-related-empty">尚未建立关联</div>'}</div>
    </div>`;
}
function normalizedImageEditVersions(values=[]){
    const edits=(Array.isArray(values) ? values : []).filter(version=>String(version.metadata?.version_type || '')==='image_edit' || Boolean(version.edit && !version.is_original && version.id!=='original'));
    const chronological=[...edits].sort((left,right)=>Number(left.created_at||0)-Number(right.created_at||0) || Number(left.version_number||0)-Number(right.version_number||0));
    return chronological.map((version,index)=>({
        ...version,
        version_number:Number(version.edit_version_number || version.metadata?.edit_version_number || (version.storage_version_number != null ? version.version_number : 0) || index+1),
        is_hidden:Boolean(version.is_hidden),
    })).sort((left,right)=>Number(right.version_number||0)-Number(left.version_number||0));
}
function registryImageVersionHistory(item={}){
    const versions=Array.isArray(item.versions) ? item.versions : [];
    const edits=normalizedImageEditVersions(versions);
    const originals=versions.filter(version=>String(version.metadata?.version_type || '')!=='image_edit');
    const original=originals.sort((left,right)=>Number(left.version_number||0)-Number(right.version_number||0))[0];
    const rows=[...edits];
    if(original) rows.push({...original,id:'original',version_number:0,is_original:true,is_hidden:false});
    return rows.sort((left,right)=>Number(right.version_number||0)-Number(left.version_number||0));
}
function renderRegistryImageVersionHistory(item={}){
    const versions=registryImageVersionHistory(item);
    if(!versions.length) return '';
    return `<div class="registry-detail-block registry-image-versions"><strong>版本历史</strong>${versions.slice(0,24).map(version=>`<div class="registry-detail-image-version ${version.is_hidden?'is-hidden':''}"><button type="button" data-registry-image-version-open="${escapeAttr(version.id)}" data-asset-id="${escapeAttr(item.id || '')}" title="打开此版本"><span><b>${version.is_original?'原始素材':`V${Number(version.version_number||0)}`}${version.is_hidden?' · 已隐藏':''}</b><small>${escapeHtml(formatDate(version.created_at))}</small></span><i data-lucide="chevron-right"></i></button><button type="button" data-registry-image-version-visibility="${escapeAttr(version.id)}" data-version-number="${Number(version.version_number||0)}" data-hidden="${version.is_hidden?'1':'0'}" data-asset-id="${escapeAttr(item.id || '')}" title="${version.is_original?'原始素材不能隐藏':version.is_hidden?'显示此版本':'隐藏此版本'}" aria-label="${version.is_hidden?'显示':'隐藏'}版本 V${Number(version.version_number||0)}" ${version.is_original?'disabled':''}><i data-lucide="${version.is_hidden?'eye-off':'eye'}"></i></button><button type="button" data-registry-image-version-edit="${escapeAttr(version.id)}" data-asset-id="${escapeAttr(item.id || '')}" title="编辑此版本" aria-label="编辑版本 V${Number(version.version_number||0)}"><i data-lucide="pencil"></i></button><button type="button" data-registry-image-version-delete="${escapeAttr(version.id)}" data-version-number="${Number(version.version_number||0)}" data-asset-id="${escapeAttr(item.id || '')}" title="${version.is_original?'原始素材不能删除':'删除此版本'}" aria-label="删除版本 V${Number(version.version_number||0)}" ${version.is_original?'disabled':''}><i data-lucide="trash-2"></i></button></div>`).join('')}</div>`;
}
async function setRegistryImageVersionHidden(assetId,versionId,versionNumber=0,hidden=false){
    if(!assetId || !versionId || versionId==='original') return;
    await apiJsonResponse(assetManagerApi.updateRegistryImageVersion(assetId, versionId, {hidden:Boolean(hidden)}));
    await gwCall('preview', 'refreshRegistryAssetAfterImageVersion', assetId);
    render();
    setStatus(`图片版本 V${Number(versionNumber||0)} 已${hidden?'隐藏':'显示'}`);
}
async function deleteRegistryImageVersion(assetId,versionId,versionNumber=0){
    if(!assetId || !versionId || versionId==='original') return;
    if(!window.confirm(`删除图片版本 V${Number(versionNumber||0)}？引用该版本的参考画布将回退到原始素材。`)) return;
    await apiJsonResponse(assetManagerApi.deleteRegistryImageVersion(assetId, versionId));
    await gwCall('preview', 'refreshRegistryAssetAfterImageVersion', assetId);
    render();
    setStatus(`已删除图片版本 V${Number(versionNumber||0)}`);
}
function registryAssetPrimaryPath(item={}){
    const direct = String(item.absolute_path || '').trim();
    if(direct) return direct;
    const locations = Array.isArray(item.locations) ? item.locations : [];
    const primary = locations.find(location => location?.is_primary && location?.is_available !== false && location?.absolute_path)
        || locations.find(location => location?.is_available !== false && location?.absolute_path);
    return String(primary?.absolute_path || '').trim();
}
function registryAssetLinkedBoards(item={}){
    return visibleAssetBoards().filter(board => board.system === false && customAssetLibraryIncludesAsset(board,item));
}
function registryLinkedBoardLabel(board={}){
    const name = String(board.name || '素材库').trim();
    return name.endsWith('素材') ? name : `${name}素材`;
}
function registryDetailPanelToggleButton(collapsed=registryDetailPanelCollapsed){
    return `<button class="hw-btn hw-btn-icon registry-detail-toggle" type="button" data-registry-detail-toggle aria-expanded="${!collapsed}" title="${collapsed ? '展开资产详情' : '折叠资产详情'}" aria-label="${collapsed ? '展开资产详情' : '折叠资产详情'}"><i data-lucide="${collapsed ? 'panel-right-open' : 'panel-right-close'}"></i></button>`;
}
function workflowProvenanceLinks(item){
    const metadata = item?.metadata || {}, source = metadata.source_context || {};
    if(!metadata.workflow_id) return '';
    const url = new URL('/static/pages/workflow.html', location.origin);
    url.searchParams.set('id', metadata.workflow_id);
    if(metadata.job_id) url.searchParams.set('job_id', metadata.job_id);
    if(source.project_id) url.searchParams.set('project_id', source.project_id);
    if(source.canvas_id) url.searchParams.set('canvas_id', source.canvas_id);
    return `<div class="registry-detail-block"><strong>生成来源</strong><div class="registry-linked-board-list"><a class="registry-linked-board-button" href="${escapeAttr(url.pathname + url.search)}">${metadata.job_id ? '查看来源工作流与任务' : '编辑来源工作流'}</a>${source.project_id ? `<a class="registry-linked-board-button" href="/static/pages/projects.html?project_id=${encodeURIComponent(source.project_id)}">返回来源项目</a>` : ''}</div></div>`;
}
function renderRegistryDetail(){
    const item = registryDetail?.id === selectedRegistryAssetId ? registryDetail : selectedRegistryAsset();
    if(!item) return `<div class="panel-head"><div class="panel-title"><strong>资产详情</strong><span>选择素材查看</span></div><div class="panel-actions">${registryDetailPanelToggleButton()}</div></div><div class="detail-empty"><i data-lucide="library-big"></i><span>暂无资产</span></div>`;
    const tags = Array.isArray(item.tags) ? item.tags : [];
    const versions = Array.isArray(item.versions) ? item.versions : [];
    const storagePath = registryAssetPrimaryPath(item);
    const linkedBoards = registryAssetLinkedBoards(item);
    const classification = item.metadata?.classification || item.classification || {};
    const width = Number(item.width || item.metadata?.width || 0);
    const height = Number(item.height || item.metadata?.height || 0);
    const frameRate = Number(item.frame_rate || item.metadata?.frame_rate || 0);
    const mediaType = detailViewerType('registry', item);
    const originalMediaUrl = mediaType === 'image' && item.id ? `/api/asset-registry/assets/${encodeURIComponent(item.id)}/media` : (item.url || '');
    const detailPreviewItem = mediaType === 'image' ? {...item,url:originalMediaUrl} : item;
    return `<div class="panel-head"><div class="panel-title"><strong>资产详情</strong><span>${escapeHtml(registryAssetTypeLabel(item))} · ${escapeHtml(item.storage_root_name || '统一资产')}</span></div>
        <div class="panel-actions">${item.is_remote ? `<button class="hw-btn hw-btn-icon" type="button" data-registry-delete-remote="${escapeAttr(item.id)}" title="删除 URL 资产"><i data-lucide="trash-2"></i></button>` : ''}${registryDetailPanelToggleButton()}</div></div>
        <div class="registry-review-entry"><button type="button" data-review-open><i data-lucide="messages-square"></i>审阅批注</button><button type="button" data-review-share-selected><i data-lucide="share-2"></i>创建分享</button></div>
        <div class="detail-scroll">
            <div class="detail-media"><button class="detail-media-frame detail-media-zoomable" type="button" aria-label="放大预览 ${escapeAttr(item.name || 'asset')}" data-registry-preview="${escapeAttr(item.id)}">${mediaType === 'video' ? `<span class="asset-thumb detail-media-video-thumb">${assetThumb(detailPreviewItem)}</span>` : assetThumb(detailPreviewItem)}</button></div>
            <div class="detail-body">
                <div class="detail-name">${escapeHtml(item.name || 'asset')}</div>
                ${mediaType === 'video' ? `${renderDetailVideoStoryboardPanel(item,'registry')}${renderDetailVideoSegments(item,'registry')}${renderDetailVideoSummary(item,'registry')}` : `<div class="detail-meta-grid">
                    <div class="detail-meta"><span>稳定 ID</span><strong title="${escapeAttr(item.id)}">${escapeHtml(String(item.id).slice(0,8))}</strong></div>
                    <div class="detail-meta"><span>大小</span><strong>${escapeHtml(formatFileSize(item.size))}</strong></div>
                    <div class="detail-meta"><span>关联库</span><strong>${linkedBoards.length}</strong></div>
                    <div class="detail-meta"><span>版本</span><strong>${mediaType === 'image' ? registryImageVersionHistory(item).length : Number(item.version_count || versions.length || 0)}</strong></div>
                    ${['image','video'].includes(mediaType) && width && height ? `<div class="detail-meta"><span>分辨率</span><strong>${width} × ${height}</strong></div>` : ''}
                    ${mediaType === 'video' && frameRate ? `<div class="detail-meta"><span>帧率</span><strong>${Number.isInteger(frameRate) ? frameRate : frameRate.toFixed(2)} fps</strong></div>` : ''}
                </div>`}
                ${classification.summary ? `<div class="detail-caption"><strong>智能描述</strong><p>${escapeHtml(classification.summary)}</p></div>` : ''}
                ${workflowProvenanceLinks(item)}
                ${renderRegistryEditableTags(item, 'side')}
                ${renderRegistryRelations(item)}
                <div class="registry-detail-block"><strong>存储位置</strong><button class="registry-storage-location" type="button" data-registry-file-reveal="${escapeAttr(item.id)}" ${storagePath && !item.is_remote ? '' : 'disabled'} title="${storagePath && !item.is_remote ? '在文件夹中显示此文件' : '位置不可用'}"><code>${escapeHtml(storagePath || '位置不可用')}</code><i data-lucide="folder-search"></i></button></div>
                <div class="registry-detail-block"><strong>关联素材库</strong><div class="registry-linked-board-list">${linkedBoards.map(board => `<button class="registry-linked-board-button" type="button" data-registry-linked-board="${escapeAttr(board.id)}" data-asset-id="${escapeAttr(item.id)}"><i data-lucide="library"></i><span>${escapeHtml(registryLinkedBoardLabel(board))}</span><i data-lucide="arrow-up-right"></i></button>`).join('') || '<span class="registry-linked-board-empty">暂无关联素材库</span>'}</div></div>
                ${mediaType === 'image' ? renderRegistryImageVersionHistory(item) : (versions.length ? `<div class="registry-detail-block"><strong>版本历史</strong>${versions.slice(0,8).map(version => `<div class="registry-detail-row"><span>v${version.version_number}</span><small>${escapeHtml(formatDate(version.created_at))}</small></div>`).join('')}</div>` : '')}
            </div>
        </div>`;
}
/* ---------------------------------------------------------------------------
 * QIN-524：统一资产库中栏「双模紧凑机架」
 * 顶部双段拨片在「目录层级」与「智能筛选」之间切换，状态落 localStorage；
 * 目录层级只呈现已登记素材库根目录与素材 relative_path 反映的真实物理子目录，
 * 点击节点按目录分页收敛右网格；智能筛选收敛为紧凑硬件胶囊。
 *
 * QIN-527：同一套机架覆盖自定义资源库看板（Input / 剧本 / 制片等）。
 * 看板态下「来源」= 看板绑定的素材来源，「目录」= 该来源素材 relative_path 反映的真实物理子目录；
 * 选中的来源与目录写回看板既有状态（boardSourceSelection / boardFolderSelection），
 * 因此素材增删改查、分组、参考画布等既有链路一律不变。
 * ------------------------------------------------------------------------- */
function assetRackBoard(){
    const board = assetBoardById(activeTab);
    return board && board.system === false ? board : null;
}
function assetRackBoardPool(board=assetRackBoard()){
    return board ? customAssetLibraryBaseItems(board, {ignoreSourceSelection:true, items:registryRackSnapshot || registryAssets}) : [];
}
function assetRackBoardRoots(board){
    const pool = assetRackBoardPool(board);
    const counts = new Map();
    pool.forEach(item => {
        const id = String(item.storage_root_id || '');
        if(!id) return;
        counts.set(id, (counts.get(id) || 0) + 1);
    });
    const sources = (board.sources || []).filter(source => !source.hidden);
    const roots = sources.map(source => ({
        id:String(source.id || ''),
        name:String(source.name || '未命名来源'),
        count:counts.get(String(source.id || '')) || 0,
        availability:'',
    })).filter(root => root.id);
    if(!roots.length && board.sourceBoard && board.rootId){
        const id = String(board.rootId);
        roots.push({id, name:String(board.name || '未命名来源'), count:counts.get(id) || 0, availability:''});
    }
    if(!board.sourceBoard && !board.automaticHidden){
        const known = new Set([...sources.map(source => String(source.id || '')), ...(board.excludedSourceIds || []).map(String)]);
        const automaticCount = pool.filter(item => !known.has(String(item.storage_root_id || ''))).length;
        if(automaticCount || boardSourceSelection[board.id] === '__automatic__'){
            roots.push({id:'__automatic__', name:'自动归档', count:automaticCount, availability:''});
        }
    }
    return roots;
}
function assetRackRoots(){
    const board = assetRackBoard();
    if(board) return assetRackBoardRoots(board);
    const facets = (registryFacets.roots || []).filter(root => String(root.availability || '') !== 'removed');
    if(facets.length){
        return facets.map(root => ({
            id:String(root.id || ''),
            name:String(root.name || '未命名来源'),
            count:Number(root.count || 0),
            availability:String(root.availability || ''),
        }));
    }
    // 来源面尚未返回时，从已加载素材推导，避免目录层级空窗
    const derived = new Map();
    registryAssets.forEach(item => {
        const id = String(item.storage_root_id || '');
        if(!id) return;
        if(!derived.has(id)) derived.set(id, {id, name:String(item.storage_root_name || '未命名来源'), count:0, availability:''});
        derived.get(id).count += 1;
    });
    return [...derived.values()];
}
function assetRackRegisteredFoldersByRoot(){
    // 后端 /api/asset-registry/folders?root_id= 只按 parent_id === root_id 列出根层登记，
    // 因此这里只采信可直接归属到某个根目录的登记项，避免把嵌套记录挂到错误来源。
    const knownRoots = new Set(assetRackRoots().map(root => root.id));
    const result = new Map();
    (registryRegisteredFolders || []).forEach(folder => {
        const parent = String(folder?.parent_id || '');
        const name = String(folder?.name || '').trim();
        if(!knownRoots.has(parent) || !name) return;
        if(!result.has(parent)) result.set(parent, []);
        result.get(parent).push(name);
    });
    return result;
}
function assetRackAutomaticDirectories(items=[]){
    // 自动归档聚合了多个根目录，登记目录需要逐根合并（同路径去重）后才能参与建树
    const seenRoots = new Set();
    const directories = [];
    items.forEach(item => {
        const id = String(item.storage_root_id || '');
        if(!id || seenRoots.has(id)) return;
        seenRoots.add(id);
        (registryRootFolders.get(id) || []).forEach(folder => {
            const path = String(folder?.path || '').replace(/\\/g, '/').replace(/^\/+|\/+$/g, '');
            if(path) directories.push(path);
        });
    });
    return directories;
}
function assetRackAutomaticItems(board=assetRackBoard()){
    // 「自动归档」= 池内但未在看板来源中显式登记的素材，与 assetRackBoardRoots 的计数口径保持一致
    if(!board) return [];
    const knownSources = new Set([
        ...(board.sources || []).map(source => String(source.id || '')),
        ...(board.excludedSourceIds || []).map(String),
    ]);
    return assetRackBoardPool(board).filter(item => !knownSources.has(String(item.storage_root_id || '')));
}
/* ---------------------------------------------------------------------------
 * QIN-527 补充：目录树合并磁盘真实子目录（含尚未索引到素材的空目录）。
 * 素材 canonical_relpath 只能推出「有素材的目录」，磁盘上像 3d/、pixaroma/、
 * 111111111/ 这类空目录必须走 /api/storage-settings/browse 逐层懒加载；
 * QIN-542：拼树消费全部已加载层级缓存（不再只读根层），非 admin 403 时
 * 在目录层级页显式提示（不再静默），空目录在加载确认后标明「空」徽标。
 * ------------------------------------------------------------------------- */
const registryDiskFolderCache = new Map();
const registryDiskFolderLoading = new Map();
let registryDiskFolderPermissionDenied = false;
let registryDiskFolderPermissionNoticed = false;
let assetAllowedRootPathsPromise = null;
const assetAllowedRootPaths = new Map();
async function loadAssetAllowedRootPaths(){
    // 历史索引根（root_xxx）不在本机库清单里，磁盘路径只能从允许根快照取；
    // 快照仅 admin 可读，editor 403 时缓存保持为空，磁盘目录浏览自然降级为仅素材推导。
    if(assetAllowedRootPathsPromise) return assetAllowedRootPathsPromise;
    assetAllowedRootPathsPromise = (async () => {
        try {
            const snapshot = await apiJsonResponse(assetManagerHttp.request('/api/storage-settings/roots', {method:'GET', credentials:'same-origin'}));
            gwCall('settings', 'applyAllowedRootSettingsData', snapshot);
            (Array.isArray(snapshot?.roots) ? snapshot.roots : []).forEach(root => {
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
        } catch(_) {}
        return assetAllowedRootPaths;
    })();
    return assetAllowedRootPathsPromise;
}
function assetRackRootDiskPath(rootId){
    const id = String(rootId || '');
    if(!id || id === '__automatic__') return '';
    const board = assetRackBoard();
    const source = (board?.sources || []).find(item => String(item.id || '') === id);
    const root = assetRackRoots().find(item => String(item.id || '') === id);
    const path = source?.path || (root ? registrySourceBoardPath(root) : '') || assetAllowedRootPaths.get(id) || '';
    return String(path || '').trim();
}
function assetRackDiskDirectories(rootId, relPath=''){
    // QIN-542 AC1：拼树必须消费全部已加载层级的磁盘目录缓存，不能再只读根层；
    // 逐层懒加载靠展开节点的 toggleAssetRackFolder 触发，这里只负责合并已有缓存。
    const id = String(rootId || '');
    if(!id) return [];
    const merged = [];
    const walk = prefix => {
        (registryDiskFolderCache.get(`${id}::${prefix}`) || []).forEach(entry => {
            const rel = String(entry?.rel || '');
            if(!rel || merged.includes(rel)) return;
            merged.push(rel);
            walk(rel);
        });
    };
    walk(relPath);
    return merged;
}
function assetRackDiskDirectoriesForItems(items=[]){
    // 聚合（自动归档）视角：合并池内各素材根已缓存的磁盘目录
    const merged = [];
    new Set(items.map(item => String(item.storage_root_id || '')).filter(Boolean)).forEach(id => {
        assetRackDiskDirectories(id).forEach(rel => { if(!merged.includes(rel)) merged.push(rel); });
    });
    return merged;
}
function assetRackFolderTree(rootId){
    const root = String(rootId || '');
    const board = assetRackBoard();
    if(board){
        if(root === '__automatic__'){
            // 【QIN-527 修复】自动归档同样要下钻真实物理子目录：生产库 900+ 条素材都落在
            // 未显式登记的根上，早先返回空树会让用户只看到一行「自动归档」而误判没有层级。
            const items = assetRackAutomaticItems(board);
            const known = new Set([...(board.sources || []).map(source => String(source.id || '')), ...(board.excludedSourceIds || []).map(String)]);
            const directories = [
                ...registryIndexedFolders.filter(folder => !known.has(String(folder.root_id || ''))).map(folder => folder.path),
                ...assetRackAutomaticDirectories(items),
                ...assetRackDiskDirectoriesForItems(items),
            ];
            const summary = registryIndexedFolders.filter(folder => !known.has(String(folder.root_id || '')));
            return customBoardFolderTree(items, directories, summary, board.preset === 'other' ? '' : board.preset);
        }
        const items = assetRackBoardPool(board).filter(item => String(item.storage_root_id || '') === root);
        const directories = [
            ...registryIndexedFolders.filter(folder => String(folder.root_id || '') === root).map(folder => folder.path),
            ...(registryRootFolders.get(root) || []).map(folder => String(folder?.path || '')).filter(Boolean),
            ...assetRackDiskDirectories(root),
        ];
        const summary = registryIndexedFolders.filter(folder => String(folder.root_id || '') === root);
        return customBoardFolderTree(items, directories, summary, board.preset === 'other' ? '' : board.preset);
    }
    const items = registryAssets.filter(item => String(item.storage_root_id || '') === root);
    const directories = [
        ...registryIndexedFolders.filter(folder => String(folder.root_id || '') === root).map(folder => folder.path),
        ...(assetRackRegisteredFoldersByRoot().get(root) || []),
        ...assetRackDiskDirectories(root),
    ];
    const summary = registryIndexedFolders.filter(folder => String(folder.root_id || '') === root);
    return customBoardFolderTree(items, directories, summary);
}
/* ---------------------------------------------------------------------------
 * 【QIN-544】左栏单栏树：系统资源 / 我的资源两个标签页，各一棵树。
 * 管线样式（竖线+节点圆点，根到选中支路点亮金色）、右侧顶部面包屑、键盘导航。
 * ------------------------------------------------------------------------- */
const ASSET_TREE_GROUP_LABELS = {
    episode:'剧集', scene:'场景', shot:'镜头', character:'角色', task:'任务',
};
function assetTreeKeyPath(key=''){
    // 根到当前节点的祖先 key 链：按 `:` 分段生成；目录 key（mine:root:<id>::<path>）
    // 中路径可能含 `:`，但祖先链只在 root 层（mine:root:<id>）需要命中，安全。
    const parts = String(key || '').split(':');
    const chain = [];
    for(let index = 1; index < parts.length; index += 1){
        chain.push(parts.slice(0, index).join(':'));
    }
    return chain;
}
function assetTreeSelectedPathKeys(){
    const selected = String(assetTreeSelectedKey || '');
    if(!selected) return new Set();
    return new Set(assetTreeKeyPath(selected).concat([selected]));
}
function assetTreeSystemBoards(){
    // AC2：系统资源树顶层节点（剧本/画布/提示词）；工作流入口删除（独立页面），系统资源只读
    return ['scripts', 'canvas-assets', 'prompts'].map(id => assetBoardById(id)).filter(Boolean);
}
function assetTreeUserBoards(){
    // QIN-560：外壳侧栏退役后用户资源入口直达；
    // QIN-547：托管资产与上传资源已并入收集箱，不再单独占用树节点。
    return visibleAssetBoards().filter(board => board.id !== 'assets' && board.id !== 'local' && !board.system);
}
// QIN-546：draggable=true 的节点（「我的资源」下的可写文件夹）才输出拖拽手柄
// QIN-547：badge 为收集箱新素材角标，仅收集箱节点携带。
function renderAssetTreeNode({key, icon, name, count, depth=0, children='', expanded=false, hasChildren=false, groupLabel=false, draggable=false, badge=0}){
    // AC6 管线样式：竖线与节点圆点由 CSS 依 --tree-depth 定位；is-path=根到选中支路，
    // is-selected=选中淡金底，is-empty=数量为 0 的目录变暗，is-group-label=分组标题小号灰字。
    const pathKeys = assetTreeSelectedPathKeys();
    const selected = assetTreeSelectedKey === key;
    const countValue = Number(count);
    const hasCount = count !== null && count !== undefined && String(count).trim() !== '' && Number.isFinite(countValue);
    const showCount = hasCount && countValue > 0;
    const zeroDim = hasCount && countValue === 0;
    return `<div class="asset-tree-node ${selected ? 'is-selected' : ''} ${pathKeys.has(key) ? 'is-path' : 'is-path-dim'} ${zeroDim ? 'is-empty' : ''}" style="--tree-depth:${Math.min(depth, 8)}" role="treeitem" aria-selected="${selected}" aria-expanded="${hasChildren ? String(Boolean(expanded)) : ''}" data-asset-tree-node-key="${escapeAttr(key)}">
        <button class="asset-tree-row" type="button" data-asset-tree-node="${escapeAttr(key)}" ${draggable ? `draggable="true" data-asset-tree-drag="${escapeAttr(key)}"` : ''} aria-label="${escapeAttr(name)}">
            <span class="asset-tree-twisty" ${hasChildren ? `data-asset-tree-toggle="${escapeAttr(key)}" aria-expanded="${Boolean(expanded)}" title="${expanded ? '折叠' : '展开'}"` : ''}>${hasChildren ? `<i data-lucide="${expanded ? 'chevron-down' : 'chevron-right'}"></i>` : ''}</span>
            <span class="asset-tree-dot"></span>
            <i class="asset-tree-icon" data-lucide="${escapeAttr(icon || 'folder')}"></i>
            <span class="asset-tree-name" title="${escapeAttr(name)}">${escapeHtml(name)}</span>
            ${groupLabel ? '' : `<span class="asset-tree-count">${showCount ? countValue.toLocaleString('zh-CN') : ''}</span>`}
            ${Number(badge) > 0 ? `<span class="asset-tree-badge" aria-label="${Number(badge)} 个新素材" title="${Number(badge)} 个新素材">${Number(badge) > 99 ? '99+' : Number(badge)}</span>` : ''}
        </button>
        ${children}
    </div>`;
}
function ensureAssetTreeProjectData(projectId){
    // AC2：展开项目时懒加载 QIN-543 详情（entities/groups）；Promise 缓存避免重复请求
    const id = String(projectId || '');
    if(!id) return Promise.resolve(null);
    const existing = assetTreeProjectData.get(id);
    if(existing) return Promise.resolve(existing).then(() => assetTreeProjectData.get(id));
    const promise = apiJsonResponse(assetManagerApi.getRegistryProject(id))
        .then(detail => {
            assetTreeProjectData.set(id, detail || {entities:[], groups:[]});
            return detail;
        })
        .catch(() => { assetTreeProjectData.set(id, {entities:[], groups:[]}); return null; });
    assetTreeProjectData.set(id, promise);
    return promise;
}
function assetTreeProjectDetail(projectId){
    // 已落地的详情数据（Promise 未决时为 null，渲染自然降级为空分组）
    const value = assetTreeProjectData.get(String(projectId || ''));
    return value && typeof value === 'object' && !value.then ? value : null;
}
function assetTreeEntityAssetIds(detail, predicate){
    // 从 QIN-543 entities[].assets 摘要收集素材 ID 集合（前端过滤数据源）
    const ids = new Set();
    (detail?.entities || []).forEach(entity => {
        if(predicate && !predicate(entity)) return;
        (entity.assets || []).forEach(asset => { if(asset?.id) ids.add(String(asset.id)); });
    });
    return ids;
}
function assetTreeProjectGroupNode(pid, type, detail){
    // 单类条目分组节点：剧集/场景/镜头/角色/任务，点分组筛选到该类条目的素材
    const pKey = `system:projects:${pid}`;
    const gKey = `${pKey}:group:${type.id}`;
    const gExpanded = assetTreeExpanded.has(gKey);
    const group = (detail.groups || []).find(item => item.entity_type === type.id);
    const entities = (detail.entities || []).filter(item => item.entity_type === type.id);
    const entityNodes = gExpanded ? entities.map(entity => renderAssetTreeNode({
        key:`${gKey}:entity:${entity.id}`, icon:'tag', name:entity.name || entity.code || entity.id,
        count:entity.asset_count, depth:3, hasChildren:false,
    })) : '';
    return renderAssetTreeNode({
        key:gKey, icon:'layers', name:ASSET_TREE_GROUP_LABELS[type.id] || type.name || type.id,
        count:group?.asset_count, depth:2,
        children:`<div class="asset-tree-children" role="group">${entityNodes}</div>`,
        expanded:gExpanded, hasChildren:true,
    });
}
function renderAssetTreeProjectsBranch(){
    // AC2：项目制片 › 项目 › 条目分组 › 条目；点项目显全部素材、点分组/条目筛选素材
    const expanded = assetTreeExpanded.has('system:projects');
    const projectNodes = expanded ? projects.map(project => {
        const pid = String(project.id || '');
        const pKey = `system:projects:${pid}`;
        const detail = assetTreeProjectDetail(pid);
        const gExpanded = assetTreeExpanded.has(pKey);
        const types = (project?.template?.entity_types || AssetRegistryDefaultEntityTypes);
        const groupNodes = (detail && gExpanded)
            ? types.map(type => assetTreeProjectGroupNode(pid, type, detail)).join('')
            : '';
        return renderAssetTreeNode({
            key:pKey, icon:'clapperboard', name:project.name || pid, count:project.asset_count, depth:1,
            children:`<div class="asset-tree-children" role="group">${groupNodes}</div>`,
            expanded:gExpanded, hasChildren:true,
        });
    }).join('') : '';
    return renderAssetTreeNode({
        key:'system:projects', icon:'clapperboard', name:'项目制片',
        count:projects.reduce((sum, item) => sum + Number(item.asset_count || 0), 0), depth:0,
        children:`<div class="asset-tree-children" role="group">${projectNodes}</div>`,
        expanded, hasChildren:true,
    });
}
function assetTreeFolderNodeFor(rootId, node, depth=0){
    // 【QIN-542】完整磁盘子目录树：展开时逐层懒加载磁盘子目录（含空目录）
    const key = `mine:root:${rootId}::${node.path}`;
    const expanded = assetTreeExpanded.has(key);
    const children = expanded
        ? `<div class="asset-tree-children" role="group">${node.children.map(child => assetTreeFolderNodeFor(rootId, child, depth + 1)).join('')}</div>`
        : '';
    return renderAssetTreeNode({
        key, icon:'folder', name:node.name, count:node.count, depth,
        children, expanded, hasChildren:true, draggable:true,
    });
}
function renderAssetTreeMineTree(){
    // AC3：收集箱第一位（QIN-547 真实目录 + 未读角标）；挂载文件夹完整磁盘子目录树；底部「+ 挂载文件夹」
    const inboxNode = renderAssetTreeNode({
        // 收集箱是固定入口，只显示未读角标，避免总数与角标挤在同一行。
        key:'mine:inbox', icon:'inbox', name:'收集箱', count:'', depth:0,
        badge:assetInboxUnreadCount(), hasChildren:false,
    });
    const boardNodes = assetTreeUserBoards().map(board => renderAssetTreeNode({
        key:`mine:board:${board.id}`, icon:board.icon, name:board.name, count:'', depth:1,
        hasChildren:false,
    })).join('');
    const boardSection = boardNodes
        ? `<div class="asset-tree-mount-section"><div class="asset-tree-group-label">资源入口</div>${boardNodes}</div>`
        : '';
    const roots = assetTreeRootsList();
    const rootNodes = roots.map(root => {
        const key = `mine:root:${root.id}`;
        const expanded = assetTreeExpanded.has(key);
        const tree = expanded ? assetRackFolderTree(root.id) : [];
        const children = expanded
            ? `<div class="asset-tree-children" role="group">${tree.map(node => assetTreeFolderNodeFor(root.id, node, 1)).join('')}</div>`
            : '';
        return renderAssetTreeNode({
            key, icon:'hard-drive', name:root.name, count:root.count, depth:1,
            children, expanded, hasChildren:true, draggable:assetOrganizeWritableRoot(root.id),
        });
    }).join('');
    // 【QIN-546 AC1】分组标题悬停出现「+」：直接在该分组下新建子文件夹
    const mountLabel = `<div class="asset-tree-group-label"><span>挂载文件夹</span><button class="asset-tree-group-add" type="button" data-asset-organize-group-new aria-label="新建文件夹" title="新建文件夹"><i data-lucide="plus"></i></button></div>`;
    const emptyGuide = roots.length ? '' : `<div class="asset-tree-empty-guide"><p>尚未挂载文件夹</p><small>挂载后按真实磁盘子目录浏览</small></div>`;
    return `<div class="asset-tree-scroll" role="tree" aria-label="我的资源目录树" tabindex="0" data-asset-tree-view="mine">${inboxNode}${boardSection}<div class="asset-tree-mount-section">${mountLabel}${rootNodes}${emptyGuide}</div></div>`;
}
function assetTreeRootsList(){
    // 根目录清单沿用 facets 口径（含离线来源），排除系统预置的历史占位根（视频库素材/图片库素材）
    return assetRackRoots().filter(root => {
        const id = String(root?.id || '').toLowerCase();
        const name = String(root?.name || '').trim();
        return id !== 'video' && id !== 'image' && name !== '视频库素材' && name !== '图片库素材';
    });
}
function renderAssetTreeSystemTree(){
    // AC2：系统资源树（只读）：项目制片、剧本资产、画布资产（【QIN-563】挂画布分类子分支）、提示词库
    const projectBranch = renderAssetTreeProjectsBranch();
    const boardNodes = assetTreeSystemBoards().map(board => {
        if(board.id === 'canvas-assets') return renderAssetTreeCanvasBoardNode(board);
        return renderAssetTreeNode({
            key:`system:${board.id}`, icon:board.icon, name:board.id === 'prompts' ? '提示词库' : board.name, count:'', depth:0,
            hasChildren:false,
        });
    }).join('');
    return `<div class="asset-tree-scroll" role="tree" aria-label="系统资源树" tabindex="0" data-asset-tree-view="system">${projectBranch}${boardNodes}</div>`;
}
function renderAssetTreeCanvasBoardNode(board){
    // 【QIN-563】「画布资产」节点挂画布分类子分支：顶置「全部画布」聚合入口，下接智能/普通/参考分类，
    // 分类展开列出各画布子行。数据来自画布资产索引（loadAll 预取的本地状态），无新增 API。
    const expanded = assetTreeExpanded.has('system:canvas-assets');
    return renderAssetTreeNode({
        key:'system:canvas-assets', icon:board?.icon || 'layout-dashboard', name:board?.name || '画布资产', count:'', depth:0,
        children: expanded ? renderAssetTreeCanvasBranch() : '',
        expanded, hasChildren:true,
    });
}
function renderAssetTreeCanvasBranch(){
    // 【QIN-563】画布分类子分支：选中态走 assetTreeSelectedKey（统计卡单击路径会同步设置），
    // 分类节点计数显示画布数（本地从画布索引推导，不依赖 API canvas_count 字段），画布子行计数显示该画布资产数。
    const catNode = cat => {
        const catCanvases = canvasAssetsForCategory(cat.id);
        return renderAssetTreeNode({
            key:`system:canvas-assets:cat:${cat.id}`,
            icon: cat.id === 'all' ? 'layers' : cat.id === 'smart' ? 'sparkles' : cat.id === 'classic' ? 'layout-grid' : 'map',
            name:cat.name, count:catCanvases.length, depth:1,
            children: assetTreeExpanded.has(`system:canvas-assets:cat:${cat.id}`)
                ? `<div class="asset-tree-children" role="group">${catCanvases.map(canvas => renderAssetTreeNode({
                    key:`system:canvas-assets:cat:${cat.id}:canvas:${canvas.canvas_id || canvas.id}`,
                    icon: canvas.kind === 'smart' ? 'sparkles' : canvas.kind === 'reference' ? 'map' : 'file-image',
                    name:canvas.title || '未命名画布',
                    count:canvasAssetCountForCanvas(canvas.canvas_id || canvas.id),
                    depth:2,
                    hasChildren:false,
                })).join('')}</div>`
                : '',
            expanded: assetTreeExpanded.has(`system:canvas-assets:cat:${cat.id}`),
            hasChildren:true,
        });
    };
    return `<div class="asset-tree-children" role="group">${canvasAssetCategories().map(catNode).join('')}</div>`;
}
function renderAssetTreePanel(extraClass=''){
    // 【QIN-566】外壳左栏恢复后，内嵌金库模式不再渲染重复树与「系统资源/我的资源」切换栏（AC4 唯一导航）。
    if(document.documentElement.classList.contains('vault-shell-embedded')) return '';
    if(activeTab === 'local') return renderLocalUploadFolderPanel();
    // AC1：左栏顶部「系统资源 / 我的资源」标签页，各一棵树；AC3 底部「+ 挂载文件夹」
    const mine = assetTreeTab === 'mine';
    const treeBody = mine ? renderAssetTreeMineTree() : renderAssetTreeSystemTree();
    return `<aside class="asset-panel asset-nav asset-tree-panel ${extraClass}" aria-label="资产导航树">
        <div class="asset-tree-tabs" role="tablist" aria-label="资产导航标签页">
            <button class="asset-tree-tab ${mine ? '' : 'active'}" type="button" role="tab" aria-selected="${mine ? 'false' : 'true'}" data-asset-tree-tab="system">系统资源</button>
            <button class="asset-tree-tab ${mine ? 'active' : ''}" type="button" role="tab" aria-selected="${mine ? 'true' : 'false'}" data-asset-tree-tab="mine">我的资源</button>
        </div>
        ${treeBody}
        <div class="asset-tree-footer">
            ${mine ? `<button class="hw-btn hw-btn-gold" type="button" data-asset-tree-mount aria-label="挂载文件夹"><i data-lucide="folder-plus"></i><span>挂载文件夹</span></button>` : ''}
        </div>
    </aside>`;
}
function activateAssetTreeTab(tab){
    // AC1：系统资源/我的资源标签页切换（各一棵树）
    const next = tab === 'mine' ? 'mine' : 'system';
    if(assetTreeTab === next) return;
    assetTreeTab = next;
    render();
}
/* 【QIN-566 / QIN-586】内嵌金库模式对外壳侧栏的树桥接：快照读取 + 入口激活，同源直调，无新增 API。
   - snapshot()：外壳渲染「收集箱 + 挂载资源」所需数据与当前选中入口 key；
   - 统一资源库树：同一物理来源只显示一次，显示后端聚合的递归素材计数（total_count）；
   - activate(key)：外壳点击入口时复用树上同一条点击分发逻辑（资产总览走看板激活并清空筛选）。 */
function assetTreeResolveRootPath(rootId, rootName=''){
    const id = String(rootId || '').trim();
    const name = String(rootName || '').trim();
    if(!id && !name) return '';

    // 1. localLibraries（按 id / kind 查）
    const fromLib = (localLibraries || []).find(l => String(l.id || '') === id || String(l.kind || '') === id);
    if(fromLib?.path) return fromLib.path;

    // 2. storageSettingsState.localLibraries（按 id / kind 查）
    const fromStateLib = (storageSettingsState?.localLibraries || []).find(l => String(l.id || '') === id || String(l.kind || '') === id);
    if(fromStateLib?.path) return fromStateLib.path;

    // 3. storageSettingsState.extraLocalEntries（按 id / kind 查）
    const fromExtra = (storageSettingsState?.extraLocalEntries || []).find(e => String(e.id || '') === id || String(e.kind || '') === id);
    if(fromExtra?.path) return fromExtra.path;

    // 4. allowedRootSettingsState.roots（按 id、aliases、aliasCandidates 查）
    const allowedRoots = Array.isArray(allowedRootSettingsState?.roots) ? allowedRootSettingsState.roots : [];
    for(const r of allowedRoots){
        if(!r?.path) continue;
        if(id && (String(r.id || '') === id || (Array.isArray(r.aliases) && r.aliases.map(String).includes(id)))) return r.path;
        if(id && Array.isArray(r.aliasCandidates) && r.aliasCandidates.some(c => String(c.storage_root_id || c.id || '') === id)) return r.path;
    }

    // 5. assetAllowedRootPaths Map 缓存（含 id 与 aliasId）
    if(id && assetAllowedRootPaths.has(id)) return assetAllowedRootPaths.get(id);

    // 6. allAssetBoards sources（按 source.id 查）
    for(const b of allAssetBoards()){
        const fromSource = (b.sources || []).find(s => String(s.id || '') === id);
        if(fromSource?.path) return fromSource.path;
    }

    // 7. 内置目录
    if(id === 'local' && storageSettingsState?.dirs?.local) return storageSettingsState.dirs.local;
    if(id === 'uploads' && storageSettingsState?.dirs?.uploads) return storageSettingsState.dirs.uploads;

    // 8. 降级兜底：按名称反查（当 facets 根仅有 name 和 id，而 id 在配置重载后变化或未直接匹配时）
    if(name){
        const fromExtraByName = (storageSettingsState?.extraLocalEntries || []).find(e => String(e.name || '') === name);
        if(fromExtraByName?.path) return fromExtraByName.path;
        const fromLibByName = (localLibraries || []).find(l => String(l.name || '') === name);
        if(fromLibByName?.path) return fromLibByName.path;
        for(const b of allAssetBoards()){
            if(b.name === name || b.name === name.replace(/素材$/, '')){
                const p = (b.sources || []).find(s => s.path)?.path;
                if(p) return p;
            }
        }
    }

    return '';
}
function assetTreeGlobalRootsList(){
    const rawRoots = [];
    const seenPaths = new Map();
    const seenIds = new Set();
    const aliasToPrimaryId = new Map();
    const findRootPath = (rootId) => assetTreeResolveRootPath(rootId);

    const addRoot = (id, name, count = null, availability = '', path = '') => {
        const cleanId = String(id || '').trim();
        const cleanName = String(name || '').trim();
        const lowerId = cleanId.toLowerCase();
        if(!cleanId || lowerId === 'video' || lowerId === 'image' || cleanName === '视频库素材' || cleanName === '图片库素材') return;

        const resolvedPath = path || findRootPath(cleanId) || assetTreeResolveRootPath(cleanId, cleanName);
        const pathKey = resolvedPath ? pathCompareKey(resolvedPath) : '';

        let existingIndex = -1;
        if(pathKey && seenPaths.has(pathKey)){
            existingIndex = seenPaths.get(pathKey);
        } else if(aliasToPrimaryId.has(cleanId)){
            const primaryId = aliasToPrimaryId.get(cleanId);
            existingIndex = rawRoots.findIndex(r => r.id === primaryId);
        }

        if(pathKey && seenPaths.has(pathKey)){
            const existing = rawRoots[seenPaths.get(pathKey)];
            if(!existing.aliases) existing.aliases = [existing.id];
            if(!existing.aliases.includes(cleanId)) existing.aliases.push(cleanId);
            aliasToPrimaryId.set(cleanId, existing.id);

            if(!existing.path && resolvedPath){
                existing.path = resolvedPath;
            }

            if((existing.count == null || existing.count === 0) && count != null && count > 0){
                existing.count = Number(count);
            }

            if(count != null && count > 0 && (!existing.count || existing.count === 0)){
                const oldId = existing.id;
                existing.id = cleanId;
                if(!existing.aliases.includes(oldId)) existing.aliases.push(oldId);
                aliasToPrimaryId.set(oldId, cleanId);
                aliasToPrimaryId.set(cleanId, cleanId);
            }

            if(availability) existing.availability = availability;

            if(cleanName && (!existing.name || existing.name.endsWith('素材') || existing.name.startsWith('local_extra_') || existing.name.startsWith('root_'))){
                existing.name = cleanName;
            }
            return;
        }

        if(existingIndex >= 0){
            const existing = rawRoots[existingIndex];
            if(!existing.aliases) existing.aliases = [existing.id];
            if(!existing.aliases.includes(cleanId)) existing.aliases.push(cleanId);
            aliasToPrimaryId.set(cleanId, existing.id);
            if(!existing.path && resolvedPath){
                existing.path = resolvedPath;
                if(pathKey && !seenPaths.has(pathKey)) seenPaths.set(pathKey, existingIndex);
            }
            if((existing.count == null || existing.count === 0) && count != null && count > 0){
                existing.count = Number(count);
            }
            if(availability) existing.availability = availability;
            if(cleanName && (!existing.name || existing.name.endsWith('素材') || existing.name.startsWith('local_extra_') || existing.name.startsWith('root_'))){
                existing.name = cleanName;
            }
            return;
        }

        if(seenIds.has(cleanId)) return;
        seenIds.add(cleanId);

        const newIndex = rawRoots.length;
        if(pathKey) seenPaths.set(pathKey, newIndex);
        aliasToPrimaryId.set(cleanId, cleanId);

        const allowedRoots = Array.isArray(allowedRootSettingsState?.roots) ? allowedRootSettingsState.roots : [];
        const matchedAllowed = allowedRoots.find(r => String(r.id || '') === cleanId || (Array.isArray(r.aliases) && r.aliases.map(String).includes(cleanId)));
        const aliases = [cleanId];
        if(matchedAllowed){
            if(matchedAllowed.id && !aliases.includes(matchedAllowed.id)) aliases.push(matchedAllowed.id);
            if(Array.isArray(matchedAllowed.aliases)){
                matchedAllowed.aliases.forEach(a => { if(a && !aliases.includes(String(a))) aliases.push(String(a)); });
            }
        }
        aliases.forEach(a => aliasToPrimaryId.set(a, cleanId));

        rawRoots.push({
            id: cleanId,
            name: cleanName || cleanId,
            count,
            availability,
            path: resolvedPath,
            aliases,
        });
    };

    (registryFacets.roots || []).forEach(r => {
        if(String(r.availability || '') !== 'removed'){
            addRoot(r.id, r.name, r.count != null ? Number(r.count) : null, String(r.availability || ''), r.path || '');
        }
    });

    (localLibraries || []).forEach(lib => {
        addRoot(lib.id, lib.name, null, '', lib.path || '');
    });

    (allowedRootSettingsState.roots || []).forEach(root => {
        if(root.active !== false){
            addRoot(root.id, '', null, root.availability || '', root.path || '');
        }
    });

    allAssetBoards().forEach(board => {
        (board.sources || []).forEach(s => {
            addRoot(s.id, s.name, null, '', s.path || '');
        });
    });

    return rawRoots;
}
function assetRackFolderTotalCount(rootId, relPath){
    if(!registryRegisteredFoldersLoaded) return null;
    const cleanPath = String(relPath || '').replace(/\\/g, '/').replace(/^\/+|\/+$/g, '');
    const found = registryIndexedFolders.find(f => String(f.root_id || '') === String(rootId || '') && String(f.path || '').replace(/\\/g, '/').replace(/^\/+|\/+$/g, '') === cleanPath);
    if(found && found.total_count != null) return Number(found.total_count);
    if(found && found.count != null) return Number(found.count);
    return 0;
}
function assetRackGlobalFolderTree(rootId){
    const root = String(rootId || '');
    const directories = [
        ...registryIndexedFolders.filter(folder => String(folder.root_id || '') === root).map(folder => folder.path),
        ...(assetRackRegisteredFoldersByRoot().get(root) || []),
        ...assetRackDiskDirectories(root),
    ];
    const summary = registryIndexedFolders.filter(folder => String(folder.root_id || '') === root);
    return customBoardFolderTree([], directories, summary);
}
function gwAssetTreeEntryKey(){
    // 外壳侧栏选中口径：收集箱、用户资源库、按根目录/子目录筛选的资产总览，或未带筛选的资产总览。
    if(activeTab === 'inbox') return 'mine:inbox';
    if(activeTab === 'registry'){
        let rootId = String(registryNavFolder?.rootId || '');
        const path = String(registryNavFolder?.path || '');
        if(rootId){
            const globalRoots = assetTreeGlobalRootsList();
            const matchedRoot = globalRoots.find(r => r.id === rootId || (Array.isArray(r.aliases) && r.aliases.includes(rootId)));
            if(matchedRoot?.id) rootId = matchedRoot.id;
            return path && path !== '__root__' ? `mine:root:${rootId}::${path}` : `mine:root:${rootId}`;
        }
        return 'registry';
    }
    const board = assetBoardById(activeTab);
    if(board && !board.system){
        const rawRootId = String(board.rootId || (board.sources && board.sources.length === 1 ? board.sources[0].id : '') || '');
        const boardPath = board.sources && board.sources.length === 1 ? String(board.sources[0].path || assetTreeResolveRootPath(board.sources[0].id, board.name) || '') : '';
        const boardPathKey = boardPath ? pathCompareKey(boardPath) : '';
        const globalRoots = assetTreeGlobalRootsList();
        const matchedRoot = globalRoots.find(r => (
            (rawRootId && (r.id === rawRootId || (Array.isArray(r.aliases) && r.aliases.includes(rawRootId)))) ||
            (boardPathKey && r.path && pathCompareKey(r.path) === boardPathKey)
        ));
        const mappedRootId = matchedRoot ? matchedRoot.id : rawRootId;
        const currentFolder = String(boardFolderSelection[board.id] || '');
        if(mappedRootId){
            return currentFolder && currentFolder !== '__root__'
                ? `mine:root:${mappedRootId}::${currentFolder}`
                : `mine:root:${mappedRootId}`;
        }
        return `mine:board:${board.id}`;
    }
    return '';
}
function serializeAssetRackFolderNode(rootId, node, depth=1){
    const key = `mine:root:${rootId}::${node.path}`;
    const expanded = assetTreeExpanded.has(key);
    const totalCount = assetRackFolderTotalCount(rootId, node.path);
    const loading = totalCount === null;
    return {
        key,
        name: node.name,
        path: node.path,
        count: loading ? '' : totalCount,
        loading,
        depth,
        hasChildren: Array.isArray(node.children) && node.children.length > 0,
        expanded,
        draggable: true,
        children: expanded && Array.isArray(node.children)
            ? node.children.map(child => serializeAssetRackFolderNode(rootId, child, depth + 1))
            : [],
    };
}
function gwAssetTreeSnapshot(){
    const userBoards = assetTreeUserBoards();
    const globalRoots = assetTreeGlobalRootsList();

    // 建立看板与物理根的映射：若看板绑定了单一物理根或物理路径相同，则合并为同一资源库节点，避免重复
    const mappedBoardIds = new Set();
    const rootBoardMap = new Map();
    userBoards.forEach(board => {
        const boardRootId = String(board.rootId || (board.sources && board.sources.length === 1 ? board.sources[0].id : '') || '');
        const boardPath = board.sources && board.sources.length === 1 ? String(board.sources[0].path || assetTreeResolveRootPath(board.sources[0].id, board.name) || '') : '';
        const boardPathKey = boardPath ? pathCompareKey(boardPath) : '';

        let matchedRoot = null;
        if(boardRootId){
            matchedRoot = globalRoots.find(r => r.id === boardRootId || (Array.isArray(r.aliases) && r.aliases.includes(boardRootId)));
        }
        if(!matchedRoot && boardPathKey){
            matchedRoot = globalRoots.find(r => r.path && pathCompareKey(r.path) === boardPathKey);
        }

        if(matchedRoot){
            mappedBoardIds.add(board.id);
            if(!rootBoardMap.has(matchedRoot.id)){
                rootBoardMap.set(matchedRoot.id, board);
            } else {
                const current = rootBoardMap.get(matchedRoot.id);
                if(board.name && (!current.name || current.name.endsWith('素材'))){
                    rootBoardMap.set(matchedRoot.id, board);
                }
            }
        }
    });

    const independentBoards = userBoards.filter(b => !mappedBoardIds.has(b.id));

    const roots = globalRoots.map(root => {
        const key = `mine:root:${root.id}`;
        const expanded = assetTreeExpanded.has(key);
        const tree = expanded ? assetRackGlobalFolderTree(root.id) : [];
        const matchingBoard = rootBoardMap.get(root.id);
        const displayName = (matchingBoard && matchingBoard.name) ? matchingBoard.name : root.name;
        const icon = (matchingBoard && matchingBoard.icon) ? matchingBoard.icon : 'hard-drive';

        const facetRoot = (registryFacets.roots || []).find(r => (
            String(r.id || '') === root.id || (Array.isArray(root.aliases) && root.aliases.includes(String(r.id || '')))
        ));
        const hasFacet = Boolean(facetRoot && facetRoot.count != null);
        const count = hasFacet ? Number(facetRoot.count) : (root.count != null ? Number(root.count) : '');
        const loading = !hasFacet && root.count == null;
        const offline = String(root.availability || '').toLowerCase() === 'offline' || String(facetRoot?.availability || '').toLowerCase() === 'offline';

        return {
            id: String(root.id || ''),
            name: displayName,
            icon,
            count,
            loading,
            offline,
            key,
            writable: assetOrganizeWritableRoot(root.id),
            expanded,
            hasChildren: true,
            draggable: assetOrganizeWritableRoot(root.id),
            children: expanded ? tree.map(node => serializeAssetRackFolderNode(root.id, node, 1)) : [],
        };
    });

    return {
        inbox_badge: assetInboxUnreadCount(),
        boards: independentBoards.map(board => ({id:board.id, name:board.name, icon:board.icon})),
        roots,
        selected_key: gwAssetTreeEntryKey(), 
    };
}
function expandAssetTreeAncestors(key){
    const value = String(key || '');
    if(!value.startsWith('mine:root:')) return;
    const body = value.slice('mine:root:'.length);
    const separator = body.indexOf('::');
    const rootId = separator > 0 ? body.slice(0, separator) : body;
    const path = separator > 0 ? body.slice(separator + 2) : '';
    assetTreeExpanded.add(`mine:root:${rootId}`);
    if(path){
        const segments = path.split('/').filter(Boolean);
        for(let i = 1; i <= segments.length; i++){
            const sub = segments.slice(0, i).join('/');
            if(i < segments.length) assetTreeExpanded.add(`mine:root:${rootId}::${sub}`);
            void gwCall('settings', 'loadAssetRackDiskFolders', rootId, sub);
        }
    }
}
function gwAssetTreeActivate(key){
    const value = String(key || '');
    if(!value || value === 'registry'){
        void gwCall('organize', 'activateAssetBoard', 'registry');
        saveAssetTreeState();
        return;
    }
    selectAssetTreeNode(value);
    saveAssetTreeState();
}
function notifyShellTreeSnapshot(){
    // 每次渲染后同步外壳（推送快照；持久化改由用户点击分发时落盘，见 saveAssetTreeState）。
    if(!document.documentElement.classList.contains('vault-shell-embedded')) return;
    try { window.parent?.GWAssetTreeHost?.onTreeSnapshot?.(gwAssetTreeSnapshot()); } catch(_) {}
}
const ASSET_TREE_STATE_KEY = 'gw.asset.tree.state';
function saveAssetTreeState(){
    const entry = gwAssetTreeEntryKey();
    try {
        if(!entry || entry === 'registry') sessionStorage.removeItem(ASSET_TREE_STATE_KEY);
        else sessionStorage.setItem(ASSET_TREE_STATE_KEY, JSON.stringify({entry, expanded:Array.from(assetTreeExpanded)}));
    } catch(_) {}
}
function restoreAssetTreeState(){
    let entry = '';
    let savedExpanded = [];
    try {
        const raw = sessionStorage.getItem(ASSET_TREE_STATE_KEY);
        if(raw){
            const parsed = JSON.parse(raw);
            entry = String(parsed.entry || '');
            if(Array.isArray(parsed.expanded)) savedExpanded = parsed.expanded;
        }
    } catch(_) {}
    if(!entry || entry === 'registry') return '';
    savedExpanded.forEach(k => assetTreeExpanded.add(k));
    expandAssetTreeAncestors(entry);
    gwAssetTreeActivate(entry);
    return entry;
}
function toggleAssetTreeNode(key){
    // 展开/折叠树节点；展开项目节点时懒加载 QIN-543 详情，展开目录时懒加载磁盘子目录
    const value = String(key || '');
    if(!value) return;
    if(assetTreeExpanded.has(value)){
        assetTreeExpanded.delete(value);
        render();
        return;
    }
    assetTreeExpanded.add(value);
    if(value.startsWith('system:projects:') && !value.includes(':group:')){
        const pid = value.slice('system:projects:'.length);
        void ensureAssetTreeProjectData(pid).then(() => { if(assetTreeExpanded.has(value)) render(); });
    }
    if(value.startsWith('mine:root:')){
        const body = value.slice('mine:root:'.length);
        const separator = body.indexOf('::');
        const rootId = separator > 0 ? body.slice(0, separator) : body;
        const path = separator > 0 ? body.slice(separator + 2) : '';
        if(path){
            // 【QIN-542】加载完成即重绘（呈现深层子目录或空目录标记）
            void gwCall('settings', 'loadAssetRackDiskFolders', rootId, path).then(() => { if(assetTreeExpanded.has(value)) render(); });
        }
    }
    render();
}
function selectAssetTreeNode(key){
    // AC2/AC3：树节点点击分发——系统看板、用户资源看板、项目/分组/条目筛选、目录过滤
    const value = String(key || '');
    if(!value) return;
    assetTreeSelectedKey = value;
    if(value.startsWith('mine:board:')){
        const boardId = value.slice('mine:board:'.length);
        if(assetBoardById(boardId)){ void gwCall('organize', 'activateAssetBoard', boardId); return; }
        render();
        return;
    }
    if(value === 'mine:inbox'){
        // AC3：收集箱是真实磁盘文件夹视图（QIN-547）
        void gwCall('inbox', 'openAssetInbox');
        return;
    }
    if(value.startsWith('mine:root:')){
        expandAssetTreeAncestors(value);
        const body = value.slice('mine:root:'.length);
        const separator = body.indexOf('::');
        const rootId = separator > 0 ? body.slice(0, separator) : body;
        const path = separator > 0 ? body.slice(separator + 2) : '';
        const matchingBoard = assetTreeUserBoards().find(b => {
            const bId = String(b.rootId || (b.sources && b.sources.length === 1 ? b.sources[0].id : '') || '');
            if(bId === rootId) return true;
            const targetRoot = assetTreeGlobalRootsList().find(r => r.id === rootId);
            if(targetRoot?.aliases?.includes(bId)) return true;
            if(targetRoot?.path && b.sources && b.sources.length === 1){
                const bPath = b.sources[0].path || assetTreeResolveRootPath(b.sources[0].id, b.name);
                if(bPath && pathCompareKey(bPath) === pathCompareKey(targetRoot.path)) return true;
            }
            return false;
        });
        if(matchingBoard && !path){
            void gwCall('organize', 'activateAssetBoard', matchingBoard.id);
            return;
        }
        gwCall('settings', 'selectAssetRackFolder', rootId, path === '__root__' ? '' : path);
        return;
    }
    if(value === 'system:projects'){
        // 组节点点击=仅展开/折叠，不做素材筛选
        toggleAssetTreeNode(value);
        return;
    }
    if(value.startsWith('system:projects:')){
        const body = value.slice('system:projects:'.length);
        const pid = body.split(':')[0];
        const rest = body.slice(pid.length);
        if(!rest){
            // 点项目：右侧显示该项目全部素材
            registryProjectEntityFilter = null;
            registryProjectFilterId = pid;
            activeProjectId = pid;
            void gwCall('organize', 'loadProjectBoard', pid, false).then(() => render());
            void refreshRegistryAssetsFromRack().catch(() => null);
            return;
        }
        const rest2 = rest.startsWith(':') ? rest.slice(1) : rest;
        if(rest2.startsWith('group:')){
            const gBody = rest2.slice('group:'.length);
            const typeId = gBody.split(':')[0];
            const entityPart = gBody.slice(typeId.length);
            if(entityPart.startsWith(':entity:')){
                // 点条目：筛选到该条目素材
                const entityId = entityPart.slice(':entity:'.length);
                registryProjectFilterId = pid;
                activeProjectId = pid;
                registryProjectEntityFilter = {projectId:pid, entityTypes:null, entityId};
                void ensureAssetTreeProjectData(pid).then(() => { void refreshRegistryAssetsFromRack().catch(() => null); render(); });
                return;
            }
            // 点分组：筛选到该类条目的素材
            registryProjectFilterId = pid;
            activeProjectId = pid;
            registryProjectEntityFilter = {projectId:pid, entityTypes:new Set([typeId]), entityId:''};
            void ensureAssetTreeProjectData(pid).then(() => { void refreshRegistryAssetsFromRack().catch(() => null); render(); });
            return;
        }
    }
    if(value.startsWith('system:canvas-assets:cat:')){
        // 【QIN-563】画布分类子节点（含「全部画布」）：进入画布资产视图并筛选该分类；
        // 分类下画布子行聚焦单个画布。选中态由 assetTreeSelectedKey 统一承载。
        const body = value.slice('system:canvas-assets:cat:'.length);
        const canvasSep = body.indexOf(':canvas:');
        if(canvasSep >= 0){
            const catId = body.slice(0, canvasSep);
            const canvasId = body.slice(canvasSep + ':canvas:'.length);
            activeCanvasAssetCategory = catId;
            activeCanvasAssetCanvasId = canvasId;
            selectedCanvasAssetId = '';
            selectedCanvasAssetIds.clear();
            if(activeTab !== 'canvas-assets') gwCall('organize', 'activateAssetBoard', 'canvas-assets');
            else render();
            return;
        }
        activeCanvasAssetCategory = body;
        activeCanvasAssetCanvasId = '';
        selectedCanvasAssetId = '';
        selectedCanvasAssetIds.clear();
        if(activeTab !== 'canvas-assets') gwCall('organize', 'activateAssetBoard', 'canvas-assets');
        else render();
        return;
    }
    if(value.startsWith('system:')){ gwCall('organize', 'activateAssetBoard', value.slice('system:'.length)); return; }
    render();
}
function handleAssetTreeNodeClick(event){
    const node = event?.target?.closest?.('[data-asset-tree-node]');
    if(!node) return false;
    selectAssetTreeNode(node.dataset.assetTreeNode || '');
    return true;
}
function handleLocalUploadBackClick(event){
    const button = event?.target?.closest?.('[data-localup-back-to-tree]');
    if(!button) return false;
    handleAssetTreeCrumbClick('registry');
    return true;
}

/* ---------------------------------------------------------------------------
 * QIN-546 我的资源整理交互：右键菜单、内联改名、拖拽移动、重名处理与 Ctrl+Z 撤销。
 * 约束：
 * - 只有「我的资源」标签页内的挂载根与真实磁盘子目录可整理；系统资源、收集箱、
 *   聚合根（__automatic__）与无物理路径的根一律不响应任何整理操作。
 * - 所有写操作都走 QIN-545 的 organize 接口并强制携带 expected_version，
 *   版本冲突时回读一次再重试，绝不做静默覆盖。
 * - 新建的 body 级浮层（菜单/弹窗/toast）必须登记到文档级点击委托清单。
 * ------------------------------------------------------------------------- */
function assetOrganizeIsMineTree(){
    return assetTreeTab === 'mine' || (typeof document !== 'undefined' && Boolean(document?.documentElement?.classList?.contains('vault-shell-embedded')));
}
function assetOrganizeWritableRoot(rootId){
    // 聚合根没有单一物理目录；离线根没有可写路径；其余挂载根按真实磁盘路径判定
    const id = String(rootId || '');
    if(!id || id === '__automatic__') return false;
    return Boolean(assetRackRootDiskPath(id));
}
function assetOrganizeTargetFromKey(key){
    if(!assetOrganizeIsMineTree()) return null;
    const value = String(key || '');
    if(!value.startsWith('mine:root:')) return null;
    const body = value.slice('mine:root:'.length);
    const separator = body.indexOf('::');
    const rootId = separator > 0 ? body.slice(0, separator) : body;
    const lowerRoot = String(rootId || '').toLowerCase();
    if(lowerRoot === 'video' || lowerRoot === 'image') return null;
    const rawPath = separator > 0 ? body.slice(separator + 2) : '';
    if(!assetOrganizeWritableRoot(rootId)) return null;
    const path = rawPath === '__root__' ? '' : rawPath;
    return {key:value, rootId, path, isRoot:!path};
}
function assetOrganizeAbsPath(rootId, relPath=''){
    const base = String(assetRackRootDiskPath(rootId) || '').replace(/\\/g, '/').replace(/\/+$/g, '');
    if(!base) return '';
    const rel = String(relPath || '').replace(/\\/g, '/').replace(/^\/+|\/+$/g, '');
    return rel ? `${base}/${rel}` : base;
}
function assetOrganizeParentPath(relPath=''){
    const rel = String(relPath || '').replace(/\\/g, '/').replace(/^\/+|\/+$/g, '');
    const index = rel.lastIndexOf('/');
    return index > 0 ? rel.slice(0, index) : '';
}
function assetOrganizeLeafName(relPath=''){
    const rel = String(relPath || '').replace(/\\/g, '/').replace(/^\/+|\/+$/g, '');
    const index = rel.lastIndexOf('/');
    return index >= 0 ? rel.slice(index + 1) : rel;
}
function assetOrganizeFolderLabel(target){
    if(!target) return '';
    if(target.isRoot){
        return assetRackRoots().find(root => String(root.id || '') === target.rootId)?.name || '挂载文件夹';
    }
    return assetOrganizeLeafName(target.path) || '文件夹';
}
function assetOrganizeRevealReason(reason){
    return ({
        'spawn-failed':'系统未允许启动文件管理器',
        'launch-failed':'启动文件管理器失败',
        'target-missing':'目标文件夹已不存在',
    })[String(reason || '')] || '当前环境不支持唤起系统文件管理器';
}
function assetOrganizeRequestUrl(url, payload, {method='POST'}={}){
    return assetManagerHttp.request(url, {
        method,
        credentials:'same-origin',
        headers:{'Content-Type':'application/json'},
        body:JSON.stringify(payload),
    });
}
async function assetOrganizeEnsureVersion(force=false){
    if(!force && assetOrganizeVersion > 0) return assetOrganizeVersion;
    const data = await apiJsonResponse(assetManagerHttp.request('/api/local-assets/organize/version', {method:'GET', credentials:'same-origin'}));
    const version = Number(data?.version);
    if(!Number.isFinite(version) || version <= 0) throw new Error('整理版本状态不可用，已拒绝写入');
    assetOrganizeVersion = version;
    return version;
}
async function assetOrganizeRequest(url, payload, {method='POST', retry=true}={}){
    // CAS：每次写操作都带 expected_version；冲突时回读一次最新版本再重试
    const version = await assetOrganizeEnsureVersion();
    try {
        const data = await apiJsonResponse(assetOrganizeRequestUrl(url, {...payload, expected_version:version}, {method}));
        const next = Number(data?.version);
        if(Number.isFinite(next) && next > 0) assetOrganizeVersion = next;
        return data;
    } catch(err) {
        if(retry && err?.code === 'VERSION_CONFLICT'){
            await assetOrganizeEnsureVersion(true);
            return assetOrganizeRequest(url, payload, {method, retry:false});
        }
        throw err;
    }
}
async function assetOrganizeUndoLast(){
    const entry = assetOrganizeUndoStack.pop();
    if(!entry){
        gwCall('organize', 'showAssetOrganizeToast', '没有可撤销的整理操作');
        return;
    }
    try {
        await assetOrganizeRequest('/api/local-assets/undo', {undo:entry.undo});
        gwCall('organize', 'showAssetOrganizeToast', `已撤销：${entry.label}`, {undoable:assetOrganizeUndoStack.length > 0});
        setStatus(`已撤销：${entry.label}`);
        await gwCall('organize', 'assetOrganizeRefresh', entry.rootId);
    } catch(err) {
        gwCall('organize', 'showAssetOrganizeToast', err?.message || '撤销失败');
        setStatus(err?.message || '撤销失败');
    }
}
/* ----- 右键菜单 ----- */
function renderAssetOrganizeMenu(target){
    const label = assetOrganizeFolderLabel(target);
    return `<div class="asset-organize-menu" id="assetOrganizeMenu" role="menu" aria-label="文件夹整理">
        <div class="asset-organize-menu-title"><i data-lucide="folder"></i><span title="${escapeAttr(label)}">${escapeHtml(label)}</span></div>
        <button type="button" role="menuitem" data-asset-organize-new><i data-lucide="folder-plus"></i><span>新建子文件夹</span></button>
        <button type="button" role="menuitem" data-asset-organize-rename ${target.isRoot ? 'disabled' : ''}><i data-lucide="pencil"></i><span>重命名</span></button>
        <button type="button" role="menuitem" data-asset-organize-open><i data-lucide="folder-open"></i><span>在系统中打开</span></button>
        <button type="button" role="menuitem" class="is-danger" data-asset-organize-delete ${target.isRoot ? 'disabled' : ''}><i data-lucide="trash-2"></i><span>删除（进回收站）</span></button>
    </div>`;
}
function closeAssetOrganizeMenu(){
    if(!assetOrganizeMenu) return;
    assetOrganizeMenu = null;
    document.getElementById('assetOrganizeMenu')?.remove();
}
function openAssetOrganizeMenu(event){
    const row = event.target?.closest?.('[data-asset-tree-node]');
    if(!row || !row.closest('.asset-tree-panel')) return;
    const target = assetOrganizeTargetFromKey(row.dataset.assetTreeNode || '');
    // 系统资源 / 收集箱 / 聚合根：不弹整理菜单，也不阻止原生菜单
    if(!target) return;
    event.preventDefault();
    event.stopPropagation();
    closeAssetOrganizeMenu();
    gwCall('organize', 'closeAssetOrganizeDialog', 'cancel');
    const host = document.createElement('div');
    host.innerHTML = renderAssetOrganizeMenu(target);
    const menu = host.firstElementChild;
    if(!menu) return;
    document.body.appendChild(menu);
    const margin = 10;
    const bounds = menu.getBoundingClientRect();
    menu.style.left = `${Math.max(margin, Math.min(event.clientX, window.innerWidth - bounds.width - margin))}px`;
    menu.style.top = `${Math.max(margin, Math.min(event.clientY, window.innerHeight - bounds.height - margin))}px`;
    // 焦点返还目标就是行按钮本身（它同时带 data-asset-tree-node 与 asset-tree-row 类）
    const trigger = row;
    assetOrganizeMenu = {...target, trigger};
    window.FloatingDismissal?.mark?.(menu, {kind:'popover', content:'self', trigger, close:closeAssetOrganizeMenu});
    refreshIcons();
    requestAnimationFrame(() => menu.querySelector('button:not(:disabled)')?.focus({preventScroll:true}));
}
function assetOrganizeAskConflict(name){
    return new Promise(resolve => {
        gwCall('organize', 'openAssetOrganizeDialog', {kind:'conflict', name, resolve});
    });
}
function assetOrganizeDefaultRootId(){
    const selected = assetOrganizeTargetFromKey(assetTreeSelectedKey);
    if(selected) return selected.rootId;
    return assetRackRoots().map(root => String(root.id || '')).find(id => assetOrganizeWritableRoot(id)) || '';
}
function assetOrganizeValidateFolderName(name){
    const value = String(name || '').trim();
    if(!value) throw new Error('文件夹名称不能为空');
    if(/[\\/:*?"<>|]/.test(value) || value === '.' || value === '..') throw new Error('文件夹名称不能包含 \\ / : * ? " < > |');
    if([...value].length > 120) throw new Error('文件夹名称过长');
    return value;
}
async function assetOrganizeOpenFolderInSystem(target){
    const abs = assetOrganizeAbsPath(target.rootId, target.path);
    if(!abs){ gwCall('organize', 'showAssetOrganizeToast', '挂载路径不可用，无法打开'); return; }
    try {
        const data = await apiJsonResponse(assetOrganizeRequestUrl('/api/local-assets/folders/open', {path:abs, root_id:target.rootId}));
        if(data?.status === 'opened'){
            gwCall('organize', 'showAssetOrganizeToast', '已在系统文件管理器中打开');
            setStatus('已在系统文件管理器中打开');
            return;
        }
        const reason = assetOrganizeRevealReason(data?.reason);
        gwCall('organize', 'showAssetOrganizeToast', `无法打开：${reason}`);
        setStatus(`无法打开：${reason}`);
    } catch(err) {
        gwCall('organize', 'showAssetOrganizeToast', err?.message || '无法打开文件夹');
        setStatus(err?.message || '无法打开文件夹');
    }
}
function assetOrganizeDropZoneFor(row, clientY, isRoot){
    if(isRoot) return 'into';
    const bounds = row.getBoundingClientRect();
    const ratio = (clientY - bounds.top) / Math.max(1, bounds.height);
    // 上下 26% 视为「插到这一层」，中间视为「放进去」——两种落点视觉与语义都要区分
    return ratio < 0.26 || ratio > 0.74 ? 'insert' : 'into';
}
function beginAssetOrganizeDrag(event){
    const folderRow = event.target.closest?.('[data-asset-tree-drag]');
    if(folderRow){
        if(!assetOrganizeIsMineTree()){ event.preventDefault(); return; }
        const target = assetOrganizeTargetFromKey(folderRow.dataset.assetTreeDrag || '');
        if(!target || target.isRoot){ event.preventDefault(); return; }
        assetOrganizeDrag = {kind:'folder', key:target.key, rootId:target.rootId, path:target.path, assetIds:[]};
        event.dataTransfer?.setData('application/x-gw-organize', target.path);
        event.dataTransfer.effectAllowed = 'move';
        folderRow.classList.add('is-organize-dragging');
        return;
    }
    const card = event.target.closest?.('[data-registry-drag]');
    if(!card) return;
    // 系统资源视图里的卡片同样带 draggable，这里显式取消拖拽，避免出现拖不动的幽灵影子
    if(!assetOrganizeIsMineTree()){ event.preventDefault(); return; }
    const assetId = String(card.dataset.registryDrag || '');
    if(!assetId){ event.preventDefault(); return; }
    const ids = registrySelectedIds.has(assetId) ? [...registrySelectedIds] : [assetId];
    assetOrganizeDrag = {kind:'assets', key:'', rootId:'', path:'', assetIds:ids};
    event.dataTransfer?.setData('application/x-gw-organize', ids.join('\n'));
    event.dataTransfer?.setData('text/plain', ids.join('\n'));
    event.dataTransfer.effectAllowed = 'move';
    card.classList.add('is-organize-dragging');
    try { if(window.parent !== window) window.parent.GWAssetDragState = assetOrganizeDrag; } catch(_) {}
}
function assetOrganizeDropTargetFor(event){
    const row = event.target.closest?.('.asset-tree-row');
    if(!row || !row.closest('.asset-tree-panel')) return null;
    if(!assetOrganizeDrag) return null;
    const target = assetOrganizeTargetFromKey(row.dataset.assetTreeNode || '');
    if(!target) return null;
    if(assetOrganizeDrag.kind === 'folder'){
        // 不允许把文件夹拖进自己或自己的子目录
        if(target.path === assetOrganizeDrag.path) return null;
        if(target.path.startsWith(`${assetOrganizeDrag.path}/`)) return null;
    }
    return {row, target};
}
function allowAssetOrganizeDrop(event){
    const hit = assetOrganizeDropTargetFor(event);
    if(!hit) return;
    event.preventDefault();
    if(event.dataTransfer) event.dataTransfer.dropEffect = 'move';
    const zone = assetOrganizeDropZoneFor(hit.row, event.clientY, hit.target.isRoot);
    const bounds = hit.row.getBoundingClientRect();
    gwCall('organize', 'applyAssetOrganizeDropHint', hit.row, zone, (event.clientY - bounds.top) < bounds.height / 2);
}
function endAssetOrganizeDrag(){
    assetOrganizeDrag = null;
    try { if(window.parent !== window) window.parent.GWAssetDragState = null; } catch(_) {}
    gwCall('organize', 'clearAssetOrganizeDropHint');
    document.querySelectorAll('.is-organize-dragging').forEach(el => el.classList.remove('is-organize-dragging'));
}
function assetOrganizeErrorText(err){
    if(err?.code === 'FILE_LOCKED') return '文件被占用，无法完成移动；请关闭占用该文件的程序后重试';
    if(err?.code === 'TARGET_EXISTS') return '目标位置已存在同名项';
    if(err?.code === 'CROSS_ROOT_FORBIDDEN') return '禁止跨挂载文件夹操作';
    if(err?.code === 'INVALID_MOVE_DESTINATION') return '不能移动到自身或其子目录';
    return err?.message || '整理操作失败';
}
/* ----- 键盘：Ctrl+Z 撤销最近操作 ----- */
function handleAssetOrganizeKeydown(event){
    const renameInput = event.target?.closest?.('[data-asset-organize-rename-input]');
    if(renameInput){
        // 内联改名：回车提交、Esc 放弃，其余按键留在输入框内
        if(event.key === 'Enter'){
            event.preventDefault();
            event.stopPropagation();
            const target = assetOrganizeTargetFromKey(renameInput.dataset.assetOrganizeRenameInput || '');
            if(target) void gwCall('organize', 'commitAssetOrganizeRename', target, renameInput.value);
            return;
        }
        if(event.key === 'Escape'){
            event.preventDefault();
            event.stopPropagation();
            gwCall('organize', 'cancelAssetOrganizeRename');
            return;
        }
        return;
    }
    const active = document.activeElement;
    const editing = active && (active.matches?.('input, textarea, select') || active.isContentEditable);
    if(event.key === 'Escape'){
        if(assetOrganizeMenu){ closeAssetOrganizeMenu(); return; }
        if(assetOrganizeDialog){ gwCall('organize', 'closeAssetOrganizeDialog', 'cancel'); return; }
        return;
    }
    if(!(event.ctrlKey || event.metaKey) || event.shiftKey || event.altKey) return;
    if(String(event.key || '').toLowerCase() !== 'z') return;
    if(editing || detailViewerState?.open) return;
    if(!assetOrganizeUndoStack.length) return;
    event.preventDefault();
    void assetOrganizeUndoLast();
}
/* ---------------------------------------------------------------------------
 * QIN-527 补充：系统看板（剧本/制片/托管/工作流/提示词/画布/上传）全域接入机架外壳。
 * 「目录层级」页保留各看板的领域树（画布分类、项目层级、资产层级等，功能零破坏）。
 * QIN-533：智能筛选页下线，机架外壳只保留目录层级，不再做跨看板的假筛选入口。
 * ------------------------------------------------------------------------- */
function systemBoardRackActive(){
    return SYSTEM_BOARD_RACK_TABS.includes(String(activeTab || ''));
}
async function refreshRegistryAssetsFromRack(){
    if(systemBoardRackActive()){
        activeTab = 'registry';
        renderBoardNavigation();
    }
    await refreshRegistryAssets();
}
function renderRegistryManager(){
    const visible = registryVisibleAssets();
    const embeddedVault = document.documentElement.classList.contains('vault-shell-embedded');
    root.innerHTML = `${embeddedVault ? '' : renderRegistryOverview()}${renderAssetTreePanel()}
    <section class="asset-panel asset-content registry-content registry-layout-${registryLayout} registry-aspect-${registryAspect} ${registrySimple ? 'registry-simple' : ''} ${registrySelectedIds.size ? 'manage-on' : ''}" style="--registry-card-size:${registryCardSize}px">
        ${embeddedVault ? '' : renderRegistryToolbar(null, registryCategoryLabel(registryStatCategory), `${registryProjectFilterId ? `【项目: ${projects.find(item => item.id === registryProjectFilterId)?.name || registryProjectFilterId}】` : ''}${registryTotalSummary(visible.length)}${registryTotalKnown && registryTotal > visible.length ? `，当前显示 ${visible.length} 项` : ''}`, {allowUrl:true,hideHeading:true})}
        ${renderRegistryFilterChips()}
        ${registryBootstrapError ? `<div class="registry-bootstrap-error" role="alert" data-registry-bootstrap-error><i data-lucide="alert-triangle"></i><span>统一资产注册表加载失败：${escapeHtml(registryBootstrapError)}</span><button class="hw-btn btn-size-sm" type="button" data-registry-bootstrap-retry>重试</button></div>` : ''}
        <div class="registry-fresh-notice" data-registry-fresh-notice></div>
        <div class="content-scroll registry-assets-scroll" data-registry-zoom-area title="Shift+滚轮调整预览大小">${renderRegistryAssetSections(visible, null, '没有符合条件的资产。可以清空排除条件。')}${renderRegistryPagination()}</div>
        <div class="registry-vault-footer" aria-label="资产库状态"><span>资产索引 <strong>${registryTotalKnown ? Number(registryTotal).toLocaleString('zh-CN') : '统计中'}</strong></span><span>${registryLoading ? '正在读取' : `已加载${registryPageHasMore ? '' : '全部'} ${registryAssets.length} 项`}</span><span>${registryPageHasMore ? '下滑加载更多' : `当前显示 ${visible.length} 项`}</span></div>
        ${renderRegistrySelectionBar(null)}
    </section>`;
    syncRegistryFacetLayout();
    // 【QIN-544】树面板常驻，目录索引与根目录清单始终加载（不再依赖中栏折叠态）
    gwCall('settings', 'loadAssetRackRegisteredFolders').catch(() => null);
    void gwCall('settings', 'ensureAssetRackDirectoryIndex');
}
const AssetRegistryDefaultEntityTypes = [
    {id:'episode',name:'剧集'},{id:'scene',name:'场景'},{id:'shot',name:'镜头'},{id:'character',name:'角色'},{id:'task',name:'任务'}
];
function renderAssetTreeBranch(lib){
    const isActiveLib = lib.id === activeAssetLibraryId;
    const cats = (lib.categories || []).filter(cat => (cat.type || 'image') === 'image');
    const smartClasses = isActiveLib ? assetClassificationEntriesForLibrary(lib) : [];
    const smartGroups = groupedAssetClassificationEntries(smartClasses);
    const activeClassEntry = isActiveLib ? activeAssetClassEntry() : null;
    const activeClassGroup = activeClassEntry ? assetClassificationEntryGroup(activeClassEntry)?.id : '';
    const showLibActions = isActiveLib && assetTreeFocus === 'library';
    return `<div class="tree-branch ${isActiveLib ? 'expanded' : ''}">
        <button class="tree-row tree-parent ${isActiveLib ? 'contains-active' : ''} ${showLibActions ? 'active' : ''}" type="button" data-asset-lib="${escapeAttr(lib.id)}">
            <span class="tree-row-icon"><i data-lucide="${isActiveLib ? 'folder-open' : 'folder'}"></i></span>
            <span class="tree-row-name">${escapeHtml(lib.name || '资产库')}</span>
            <span class="tree-row-count">${assetCountForLibrary(lib)}</span>
        </button>
        ${showLibActions ? renderAssetTreeActionBar('library') : ''}
        <div class="tree-children">
            ${cats.length ? cats.map(cat => `<button class="tree-row tree-child ${isActiveLib && cat.id === activeAssetCategoryId && assetTreeFocus === 'category' ? 'active' : ''}" type="button" data-asset-cat="${escapeAttr(cat.id)}" data-asset-cat-lib="${escapeAttr(lib.id)}">
                <span class="tree-elbow"></span>
                <span class="tree-row-icon"><i data-lucide="image"></i></span>
                <span class="tree-row-name">${escapeHtml(cat.name || '分组')}</span>
                <span class="tree-row-count">${(cat.items || []).length}</span>
            </button>${isActiveLib && cat.id === activeAssetCategoryId && assetTreeFocus === 'category' ? renderAssetTreeActionBar('category') : ''}`).join('') : '<div class="tree-empty">暂无分组</div>'}
            ${isActiveLib ? `<div class="tree-smart-class">
                <button class="tree-row tree-parent ${activeAssetClassFilter ? 'contains-active' : ''}" type="button" data-asset-class-root="${escapeAttr(lib.id)}">
                    <span class="tree-row-icon"><i data-lucide="tags"></i></span>
                    <span class="tree-row-name">智能分类</span>
                    <span class="tree-row-count">${smartGroups.length}</span>
                </button>
                ${smartGroups.length ? smartGroups.map(group => `<div class="smart-class-group">
                    <button class="smart-class-group-btn ${openAssetClassGroup === group.id ? 'open' : ''} ${activeClassGroup === group.id ? 'active' : ''}" type="button" data-asset-class-group="${escapeAttr(group.id)}">
                        <span>${escapeHtml(group.name)}</span><small>${group.entries.length}</small><i data-lucide="${openAssetClassGroup === group.id ? 'chevron-up' : 'chevron-down'}"></i>
                    </button>
                    ${openAssetClassGroup === group.id ? `<div class="smart-class-pills">
                        ${group.entries.map(entry => `<button class="smart-class-pill ${activeAssetClassFilter === entry.key ? 'active' : ''}" type="button" data-asset-class-filter="${escapeAttr(entry.key)}" data-asset-class-lib="${escapeAttr(lib.id)}" title="${escapeAttr(entry.label)}">
                            <span>${escapeHtml(entry.tag)}</span><small>${entry.count}</small>
                        </button>`).join('')}
                    </div>` : ''}
                </div>`).join('') : '<div class="tree-empty">暂无分类，先选择图片点“智能分类”</div>'}
            </div>` : ''}
        </div>
    </div>`;
}
function renderAssetTreeActionBar(kind){
    const editHtml = renderAssetTreeInlineEdit(kind);
    if(editHtml) return editHtml;
    const deleteKey = kind === 'library' ? `asset-lib:${activeAssetLibraryId}` : `asset-cat:${activeAssetLibraryId}:${activeAssetCategoryId}`;
    if(kind === 'library'){
        return `<div class="tree-action-bar library-actions">
            <button type="button" data-asset-cat-new><i data-lucide="folder-plus"></i><span>新分组</span></button>
            <button type="button" data-asset-lib-rename><i data-lucide="pencil"></i><span>重命名</span></button>
            <button type="button" class="danger ${pendingTreeDelete === deleteKey ? 'detail-confirm' : ''}" data-asset-lib-delete><i data-lucide="trash-2"></i><span>${pendingTreeDelete === deleteKey ? '确认删除' : '删除库'}</span></button>
        </div>`;
    }
    return `<div class="tree-action-bar child-actions">
        <button type="button" data-asset-cat-new><i data-lucide="folder-plus"></i><span>新分组</span></button>
        <button type="button" data-asset-cat-rename><i data-lucide="pencil"></i><span>重命名</span></button>
        <button type="button" class="danger ${pendingTreeDelete === deleteKey ? 'detail-confirm' : ''}" data-asset-cat-delete><i data-lucide="trash-2"></i><span>${pendingTreeDelete === deleteKey ? '确认删除' : '删除'}</span></button>
    </div>`;
}
function renderAssetTreeInlineEdit(kind){
    if(!assetTreeEdit) return '';
    if(assetTreeEdit.placement === 'head') return '';
    const expectedKinds = kind === 'library'
        ? ['library-new', 'library-rename', 'category-new']
        : ['category-new', 'category-rename'];
    if(!expectedKinds.includes(assetTreeEdit.kind)) return '';
    const label = assetTreeEdit.label || '名称';
    return `<div class="tree-inline-edit ${kind === 'category' ? 'child-actions' : 'library-actions'}">
        <input id="assetTreeEditInput" type="text" value="${escapeAttr(assetTreeEdit.value || '')}" placeholder="${escapeAttr(label)}">
        <button type="button" class="primary" data-asset-tree-edit-save><i data-lucide="check"></i><span>保存</span></button>
        <button type="button" data-asset-tree-edit-cancel><i data-lucide="x"></i><span>取消</span></button>
    </div>`;
}
const SMART_CANVAS_ASSET_INBOX_KEY = 'smart_canvas_asset_inbox';
async function createRegistryProject(){
    const name = window.prompt('项目名称', '新项目');
    if(!String(name || '').trim()) return;
    const description = window.prompt('项目说明（可选）', '') || '';
    const data = await apiJsonResponse(assetManagerApi.createRegistryProject({name:String(name).trim(), description}));
    if(data.project){
        // 契约对齐：创建接口只回传 project_id / version（见 create_project.response_200_201），
        // 归一化后项目列表去重与选中才能生效。
        const createdPid = data.project.id || data.project.project_id;
        projects = [{...data.project, id:createdPid, name:data.project.name || String(name).trim()},
                    ...projects.filter(item => item.id !== createdPid)];
        activeProjectId = createdPid;
        selectedProjectEntityId = '';
        await gwCall('organize', 'loadProjectBoard', activeProjectId);
    }
}
async function addRegistryProjectEntity(){
    if(!activeProjectId || !projectBoard?.project) return;
    const types = projectBoard.project.template?.entity_types || AssetRegistryDefaultEntityTypes;
    const typeHint = types.map(item => `${item.id}=${item.name}`).join('，');
    const entityType = String(window.prompt(`条目类型：${typeHint}`, types[0]?.id || 'task') || '').trim();
    if(!types.some(item => item.id === entityType)){ setStatus('请选择模板中已有的条目类型'); return; }
    const name = String(window.prompt('条目名称', '新条目') || '').trim();
    if(!name) return;
    const code = String(window.prompt('编号（可选）', '') || '').trim();
    const data = await apiJsonResponse(assetManagerApi.createRegistryProjectEntity(activeProjectId, {entity_type:entityType,name,code}));
    selectedProjectEntityId = data.entity?.id || '';
    await gwCall('organize', 'loadProjectBoard', activeProjectId);
}
async function editRegistryProjectTemplate(){
    const project = projectBoard?.project;
    if(!project) return;
    const raw = window.prompt('编辑项目模板 JSON（entity_types / gates）', JSON.stringify(project.template || {}, null, 2));
    if(raw === null) return;
    let template;
    try { template = JSON.parse(raw); }
    catch(_){ setStatus('模板 JSON 格式错误'); return; }
    const data = await apiJsonResponse(assetManagerApi.updateRegistryProject(project.id, {template}));
    if(data.project) projects = projects.map(item => item.id === data.project.id ? data.project : item);
    await gwCall('organize', 'loadProjectBoard', project.id);
}
async function cycleRegistryProjectGate(id, status){
    const order = ['pending','in_progress','approved','blocked'];
    const next = order[(order.indexOf(status) + 1) % order.length];
    await apiJsonResponse(assetManagerApi.updateRegistryProjectGate(id, {status:next}));
    await gwCall('organize', 'loadProjectBoard', activeProjectId);
}
async function cycleRegistryProjectEntity(id, status){
    const order = ['pending','in_progress','approved','blocked'];
    const next = order[(order.indexOf(status) + 1) % order.length];
    await apiJsonResponse(assetManagerApi.updateRegistryProjectEntity(id, {status:next}));
    await gwCall('organize', 'loadProjectBoard', activeProjectId);
}
async function handleClick(event){
    const target = event.target;
    // 【QIN-531 修复】排序/分组自绘下拉：点击胶囊（含图标与标签）经 label 转发到触发器，
    // 菜单项单独处理，避免原生 select 的系统蓝底与焦点方框。
    const governanceOpenTarget = target.closest?.('[data-governance-open]');
    if(governanceOpenTarget){
        gwCall('governance', 'openGovernanceDrawer', governanceOpenTarget);
        return;
    }
    const registrySelectTrigger = target.closest?.('[data-registry-select-trigger]');
    if(registrySelectTrigger){
        event.preventDefault();
        event.stopPropagation();
        toggleRegistrySelectMenu(registrySelectTrigger.dataset.registrySelectTrigger || '');
        return;
    }
    const registrySelectOption = target.closest?.('[data-registry-select-option]');
    if(registrySelectOption){
        event.preventDefault();
        event.stopPropagation();
        applyRegistrySelectValue(registrySelectOption.dataset.registrySelectKind || '', registrySelectOption.dataset.registrySelectOption || '');
        return;
    }
    const governanceConfirmTarget = target.closest?.('[data-governance-confirm]');
    if(governanceConfirmTarget){
        await gwCall('governance', 'confirmGovernanceSuggestion', governanceConfirmTarget.dataset.governanceConfirm || '');
        return;
    }
    const directoryBrowserClose = target.closest?.('[data-server-directory-close]');
    if(directoryBrowserClose){
        serverDirectoryBrowserState = {...serverDirectoryBrowserState, open:false, loading:false};
        gwCall('settings', 'renderServerDirectoryBrowserHost');
        if(serverDirectoryBrowserState.key === BOARD_CREATE_PATH_FIELD_KEY){
            requestAnimationFrame(() => document.querySelector(`[data-directory-picker="${BOARD_CREATE_PATH_FIELD_KEY}"]`)?.focus());
        }
        return;
    }
    const directoryBrowserParent = target.closest?.('[data-server-directory-parent]');
    if(directoryBrowserParent && serverDirectoryBrowserState.parent){
        await gwCall('settings', 'loadServerDirectoryBrowser', serverDirectoryBrowserState.parent);
        return;
    }
    const directoryBrowserEnter = target.closest?.('[data-server-directory-enter]');
    if(directoryBrowserEnter){
        await gwCall('settings', 'loadServerDirectoryBrowser', directoryBrowserEnter.dataset.serverDirectoryEnter || null);
        return;
    }
    const directoryBrowserMount = target.closest?.('[data-server-directory-mount],[data-server-directory-mount-current]');
    if(directoryBrowserMount){
        const pathValue = directoryBrowserMount.dataset.serverDirectoryMount || directoryBrowserMount.dataset.serverDirectoryMountCurrent;
        await gwCall('settings', 'saveDirectoryPath', serverDirectoryBrowserState.key || 'projects', pathValue, true);
        return;
    }
    const clientDirectoryPicker = target.closest?.('[data-client-directory-picker]');
    if(clientDirectoryPicker){
        if(clientSettingBusy) return;
        await gwCall('settings', 'pickClientDirectoryNative');
        return;
    }
    const directoryPicker = target.closest?.('[data-directory-picker]');
    if(directoryPicker){
        const key = directoryPicker.dataset.directoryPicker;
        const input = directoryFieldInput(key);
        if(!input || input.readOnly || !canBrowseServerDirectories()) return;
        await gwCall('settings', 'pickServerDirectoryNative', key);
        return;
    }
    if(target.closest('[data-directory-reload]')){
        if(directorySettingsReady && !window.confirm('重新读取将放弃当前未保存的目录修改，是否继续？')) return;
        await loadGlobalDirectorySettings(); return;
    }
    const structureRemove = target.closest?.('[data-registry-structure-remove]');
    if(structureRemove){
        event.preventDefault();
        event.stopPropagation();
        openRegistryStructureRemoveDialog(structureRemove.dataset.registryStructureRemove || '', structureRemove);
        return;
    }
    const structureCoverflow = target.closest?.('[data-registry-structure-coverflow]');
    if(structureCoverflow){
        event.preventDefault();
        event.stopPropagation();
        openRegistryStructureCoverflow(structureCoverflow.dataset.registryStructureCoverflow || '', structureCoverflow);
        return;
    }
    const groupSelect = target.closest?.('[data-registry-structure-group-select]');
    if(groupSelect){
        event.preventDefault();
        event.stopPropagation();
        if(Number(event.detail || 0) <= 1){
            const structure = registryAssetStructures.get(groupSelect.dataset.registryStructureGroupSelect || '');
            const cover = structure ? registryStructureMemberAsset(structure, structure.current_asset_id) : null;
            if(cover){
                selectedRegistryAssetId = cover.id;
                registryDetail = cover;
                registrySelectedIds.clear();
                registrySelectionAnchorId = cover.id;
                registryManageMode = false;
                syncRegistrySelectionDom();
            }
        }
        return;
    }
    const groupCard = target.closest?.('[data-registry-structure-card][data-registry-structure-type="group"]');
    if(groupCard){
        event.preventDefault();
        event.stopPropagation();
        if(Number(event.detail || 0) <= 1){
            const structure = registryAssetStructures.get(groupCard.dataset.registryStructureCard || '');
            const cover = structure ? registryStructureMemberAsset(structure, structure.current_asset_id) : null;
            if(cover){
                selectedRegistryAssetId = cover.id;
                registryDetail = cover;
                registrySelectedIds.clear();
                registrySelectionAnchorId = cover.id;
                registryManageMode = false;
                syncRegistrySelectionDom();
            }
        }
        return;
    }
    const versionCurrent = target.closest?.('[data-registry-structure-current]');
    if(versionCurrent){
        await setRegistryVersionStructureCurrent(versionCurrent.dataset.registryStructureId || '', versionCurrent.dataset.registryStructureCurrent || '');
        return;
    }
    const structureOpen = target.closest?.('[data-registry-structure-open]');
    if(structureOpen){
        const structure = registryAssetStructures.get(structureOpen.dataset.registryStructureOpen || '');
        if(structure?.structure_type === 'version'){
            // A normal click selects the current final version without
            // rebuilding the card. The browser can then deliver its native
            // dblclick to the stable card, which opens the reference canvas.
            event.preventDefault();
            event.stopPropagation();
            if(Number(event.detail || 0) <= 1 && structure.current_asset_id){
                const current = registryStructureMemberAsset(structure, structure.current_asset_id);
                if(current){
                    selectedRegistryAssetId = current.id;
                    registryDetail = current;
                    registrySelectedIds.clear();
                    registrySelectionAnchorId = current.id;
                    registryManageMode = false;
                    syncRegistrySelectionDom();
                }
            }
            return;
        }
        if(structure?.current_asset_id) await gwCall('preview', 'openRegistryAsset', structure.current_asset_id);
        return;
    }
    if(target.closest?.('[data-registry-detail-toggle]')){ gwCall('preview', 'toggleRegistryDetailPanel'); return; }
    const registryFileReveal = target.closest?.('[data-registry-file-reveal]');
    if(registryFileReveal){
        await gwCall('organize', 'revealRegistryAssetFile', registryFileReveal.dataset.registryFileReveal || selectedRegistryAssetId);
        return;
    }
    const registryLinkedBoard = target.closest?.('[data-registry-linked-board]');
    if(registryLinkedBoard){
        await gwCall('preview', 'openRegistryAssetInBoard', registryLinkedBoard.dataset.registryLinkedBoard || '', registryLinkedBoard.dataset.assetId || selectedRegistryAssetId);
        return;
    }
    if(target.closest?.('[data-registry-recycle-bin-open]')){ await gwCall('recycle', 'openRegistryRecycleBin'); return; }
    if(target.closest?.('[data-board-ai-auto]')){
        await gwCall('settings', 'setAssetAiAutoClassify', !localAutoClassify);
        return;
    }
    if(target.closest?.('[data-board-ai-save]')){ await gwCall('settings', 'saveAssetAiSettings'); return; }
    if(target.closest?.('[data-board-ai-reset]')){
        gwCall('settings', 'syncStorageSettingsInputsToState');
        assetAiSettingsState = {...assetAiSettingsState, rule:assetAiSettingsState.defaultRule || '', message:'已恢复，保存后生效'};
        gwCall('settings', 'renderStorageSettingsModal');
        return;
    }
    const settingsTab = target.closest?.('[data-storage-settings-tab]');
    if(settingsTab){
        gwCall('settings', 'syncStorageSettingsInputsToState');
        thumbnailGeneratorState.directory = String(document.getElementById('thumbnailDirectory')?.value || thumbnailGeneratorState.directory || '').trim();
        thumbnailGeneratorState.transcodeVideos = Boolean(document.getElementById('thumbnailTranscodeVideos')?.checked ?? thumbnailGeneratorState.transcodeVideos);
        storageSettingsState.tab = settingsTab.dataset.storageSettingsTab || 'boards';
        gwCall('settings', 'renderStorageSettingsModal');
        return;
    }
    if(target.closest?.('[data-board-proxy-save]')){ await gwCall('settings', 'saveAssetProxySettings'); return; }
    if(target.closest?.('[data-jxl-transcode-scan]')){ await gwCall('settings', 'loadJxlTranscodeScan'); return; }
    if(target.closest?.('[data-jxl-transcode-start]')){ await gwCall('settings', 'startJxlLosslessTranscode'); return; }
    if(target.closest?.('[data-jxl-transcode-cancel]')){ await gwCall('settings', 'cancelJxlLosslessTranscode'); return; }
    const boardThumbnailMode = target.closest?.('[data-board-thumbnail-mode]');
    if(boardThumbnailMode){
        gwCall('settings', 'syncStorageSettingsInputsToState');
        const nextDirectory = String(document.getElementById('thumbnailDirectory')?.value || thumbnailGeneratorState.directory || '').trim();
        thumbnailGeneratorState.transcodeVideos = Boolean(document.getElementById('thumbnailTranscodeVideos')?.checked ?? thumbnailGeneratorState.transcodeVideos);
        gwCall('settings', 'commitThumbnailSettingsChange', boardThumbnailMode.dataset.boardThumbnailMode === 'custom' ? 'custom' : 'source', nextDirectory);
        gwCall('settings', 'renderStorageSettingsModal');
        return;
    }
    // 【QIN-519 / QIN-525】预览缓存两按钮：全部缓存（增量）/ 重建缓存（先清空）+ 分类删除。
    const previewCacheStart = target.closest?.('[data-preview-all-start]');
    if(previewCacheStart){
        gwCall('preview', 'requestPreviewAllConfirm', previewCacheStart.dataset.previewAllMode === 'rebuild' ? 'rebuild' : 'fill');
        return;
    }
    if(target.closest?.('[data-preview-all-confirm]')){ await gwCall('preview', 'startPreviewAllCache'); return; }
    if(target.closest?.('[data-preview-all-cancel]')){ gwCall('preview', 'cancelPreviewAllConfirm'); return; }
    // 运行中：暂停 / 继续 / 取消（取消只投递信号，收尾由后端在批次边界完成）。
    const previewPause = target.closest?.('[data-preview-all-pause]');
    if(previewPause){
        await gwCall('preview', 'controlPreviewAllJob', assetPreviewAllState.job?.status === 'paused' ? 'resume' : 'pause');
        return;
    }
    if(target.closest?.('[data-preview-all-stop]')){ await gwCall('preview', 'controlPreviewAllJob', 'cancel'); return; }
    if(target.closest?.('[data-preview-cache-delete]')){ gwCall('preview', 'openPreviewCacheDeletePanel'); return; }
    const previewDeleteToggle = target.closest?.('[data-preview-delete-toggle]');
    if(previewDeleteToggle){ gwCall('organize', 'togglePreviewDeleteCategory', previewDeleteToggle.dataset.previewDeleteToggle); return; }
    if(target.closest?.('[data-preview-delete-cancel]')){ gwCall('preview', 'cancelPreviewCacheDelete'); return; }
    if(target.closest?.('[data-preview-delete-confirm]')){ await gwCall('preview', 'confirmPreviewCacheDelete'); return; }
    if(target.closest?.('[data-board-storyboard-generate]')){ await gwCall('preview', 'generateBoardVideoStoryboards'); return; }
    if(target.closest?.('[data-board-storyboard-delete]')){ await gwCall('preview', 'deleteBoardVideoStoryboards'); return; }
    if(target.closest?.('[data-thumbnail-generator-open]')){ await gwCall('settings', 'openThumbnailGenerator'); return; }
    if(target.closest?.('[data-thumbnail-generator-close]')){ gwCall('settings', 'closeThumbnailGenerator'); return; }
    const thumbnailMode = target.closest?.('[data-thumbnail-mode]');
    if(thumbnailMode){
        const nextDirectory = String(document.getElementById('thumbnailDirectory')?.value || thumbnailGeneratorState.directory || '').trim();
        thumbnailGeneratorState.transcodeVideos = Boolean(document.getElementById('thumbnailTranscodeVideos')?.checked);
        gwCall('settings', 'commitThumbnailSettingsChange', thumbnailMode.dataset.thumbnailMode === 'custom' ? 'custom' : 'source', nextDirectory);
        gwCall('settings', 'renderThumbnailGeneratorModal');
        return;
    }
    if(target.closest?.('[data-thumbnail-generator-run]')){ await gwCall('settings', 'generateAssetThumbnails'); return; }
    if(target.closest?.('[data-thumbnail-generator-background]')){ await gwCall('settings', 'generateAssetThumbnailsInBackground'); return; }
    const cacheSwitchAction = target.closest?.('[data-thumbnail-cache-switch]');
    if(cacheSwitchAction){ await gwCall('settings', 'confirmThumbnailCacheSwitch', cacheSwitchAction.dataset.thumbnailCacheSwitch || ''); return; }
    if(target.closest?.('[data-thumbnail-cache-grant]')){ await gwCall('settings', 'grantThumbnailCacheRootAndRetry'); return; }
    if(target.closest?.('[data-thumbnail-cache-cancel]')){ gwCall('settings', 'dismissThumbnailCacheBlock'); return; }
    if(target.closest?.('[data-local-ai-start]')){
        localUploadManageMode = true;
        render();
        setStatus('请选择图片，然后使用 AI 分类或提示词反推');
        return;
    }
    if(target.closest?.('[data-board-source-close]')){ gwCall('organize', 'closeBoardSourceModal'); return; }
    if(target.closest?.('[data-board-source-editor-cancel]')){ gwCall('organize', 'cancelBoardSourceEditor'); return; }
    if(target.closest?.('[data-board-source-new]')){
        boardSourceState = {...boardSourceState, editor:'new', existingId:'', name:`${assetBoardById(boardSourceState.boardId)?.name || ''}素材`, path:''};
        gwCall('organize', 'renderBoardSourceModal');
        return;
    }
    const boardSourceEdit = target.closest?.('[data-board-source-edit]');
    if(boardSourceEdit){ gwCall('organize', 'editBoardSource', boardSourceEdit.dataset.boardSourceEdit || ''); return; }
    const boardSourceVisibility = target.closest?.('[data-board-source-visibility]');
    if(boardSourceVisibility){ await gwCall('organize', 'setBoardSourceVisible', boardSourceVisibility.dataset.boardId || activeTab, boardSourceVisibility.dataset.boardSourceVisibility || ''); return; }
    const boardSourceDelete = target.closest?.('[data-board-source-delete]');
    if(boardSourceDelete){ await gwCall('organize', 'deleteBoardSource', boardSourceDelete.dataset.boardId || activeTab, boardSourceDelete.dataset.boardSourceDelete || ''); return; }
    const boardSourceSettings = target.closest?.('[data-board-source-settings]');
    if(boardSourceSettings){ gwCall('organize', 'openBoardSourceModal', boardSourceSettings.dataset.boardSourceSettings || activeTab); return; }
    if(target.closest?.('[data-board-source-confirm]')){ await gwCall('organize', 'confirmBoardSource'); return; }
    const boardSourceAdd = target.closest?.('[data-board-source-add]');
    if(boardSourceAdd){ gwCall('organize', 'openBoardSourceModal', boardSourceAdd.dataset.boardSourceAdd || ''); return; }
    const boardSource = target.closest?.('[data-board-source]');
    if(boardSource){
        const boardId = boardSource.dataset.boardId || activeTab;
        const sourceId = boardSource.dataset.boardSource || '';
        const nextSourceId = boardSourceSelection[boardId] === sourceId ? '' : sourceId;
        boardSourceSelection[boardId] = nextSourceId;
        boardFolderSelection[boardId] = '';
        boardGroupSelection[boardId] = '';
        boardReferenceCanvasSelection[boardId] = '';
        const board = assetBoardById(boardId);
        applyRegistryFilterDefinition({category:board?.category || '', rootId:nextSourceId === '__automatic__' ? '' : nextSourceId, groupSimilar:registryGroupSimilar});
        await refreshRegistryAssets();
        gwCall('organize', 'loadCustomBoardFolderDirectories', board).catch(() => null);
        return;
    }
    if(target.closest?.('[data-board-folder-create-close]')){ gwCall('organize', 'closeCustomBoardFolderCreateModal'); return; }
    if(target.closest?.('[data-board-folder-create-confirm]')){ await gwCall('organize', 'submitCustomBoardFolderCreate'); return; }
    const boardFolderCreate = target.closest?.('[data-board-folder-create]');
    if(boardFolderCreate){ gwCall('organize', 'openCustomBoardFolderCreateModal', boardFolderCreate.dataset.boardFolderCreate || activeTab); return; }
    const boardFolder = target.closest?.('[data-board-folder]');
    if(boardFolder){
        const boardId = boardFolder.dataset.boardId || activeTab;
        const folderPath = boardFolder.dataset.boardFolder || '';
        boardFolderSelection[boardId] = boardFolderSelection[boardId] === folderPath ? '' : folderPath;
        boardGroupSelection[boardId] = '';
        boardReferenceCanvasSelection[boardId] = '';
        registrySelectedIds.clear();
        registrySelectionAnchorId = '';
        selectedRegistryAssetId = '';
        registryDetail = null;
        render();
        return;
    }
    const boardFolderToggle = target.closest?.('[data-board-folder-toggle]');
    if(boardFolderToggle){
        const boardId = boardFolderToggle.dataset.boardId || activeTab;
        const path = boardFolderToggle.dataset.boardFolderToggle || '';
        const collapsed = new Set(boardCollapsedFolders[boardId] || []);
        if(collapsed.has(path)) collapsed.delete(path); else collapsed.add(path);
        boardCollapsedFolders[boardId] = [...collapsed];
        gwCall('organize', 'saveCollapsedLibraryFolders');
        render();
        return;
    }
    const boardFolderVisibility = target.closest?.('[data-board-folder-visibility]');
    if(boardFolderVisibility){
        const boardId = boardFolderVisibility.dataset.boardId || activeTab;
        const path = boardFolderVisibility.dataset.boardFolderVisibility || '';
        const hidden = new Set(boardHiddenFolders[boardId] || []);
        if(hidden.has(path)) hidden.delete(path); else hidden.add(path);
        boardHiddenFolders[boardId] = [...hidden];
        gwCall('organize', 'saveHiddenLibraryFolders');
        registrySelectedIds.clear();
        registrySelectionAnchorId = '';
        selectedRegistryAssetId = '';
        registryDetail = null;
        render();
        setStatus(hidden.has(path) ? '已隐藏该文件夹内素材，目录仍保留' : '已显示该文件夹内素材');
        return;
    }
    const boardFolderExpandAll = target.closest?.('[data-board-folder-expand-all]');
    if(boardFolderExpandAll){
        boardCollapsedFolders[boardFolderExpandAll.dataset.boardFolderExpandAll || activeTab] = [];
        gwCall('organize', 'saveCollapsedLibraryFolders');
        render();
        return;
    }
    const boardFolderCollapseAll = target.closest?.('[data-board-folder-collapse-all]');
    if(boardFolderCollapseAll){
        const boardId = boardFolderCollapseAll.dataset.boardFolderCollapseAll || activeTab;
        const board = assetBoardById(boardId);
        boardCollapsedFolders[boardId] = customBoardFolderParentPaths(customBoardFolderTree(customAssetLibraryBaseItems(board), customBoardCachedFolderEntries(board, customAssetLibraryBaseItems(board))));
        gwCall('organize', 'saveCollapsedLibraryFolders');
        render();
        return;
    }
    const boardGroup = target.closest?.('[data-board-group]');
    if(boardGroup){
        const boardId = boardGroup.dataset.boardId || activeTab;
        const groupId = boardGroup.dataset.boardGroup || '';
        boardGroupSelection[boardId] = boardGroupSelection[boardId] === groupId ? '' : groupId;
        boardFolderSelection[boardId] = '';
        boardReferenceCanvasSelection[boardId] = '';
        registrySelectedIds.clear();
        registrySelectionAnchorId = '';
        selectedRegistryAssetId = '';
        registryDetail = null;
        render();
        return;
    }
    const boardReferenceCanvas = target.closest?.('[data-board-reference-canvas]');
    if(boardReferenceCanvas){
        const boardId = boardReferenceCanvas.dataset.boardId || activeTab;
        const canvasId = boardReferenceCanvas.dataset.boardReferenceCanvas || '';
        boardReferenceCanvasSelection[boardId] = boardReferenceCanvasSelection[boardId] === canvasId ? '' : canvasId;
        boardFolderSelection[boardId] = '';
        boardGroupSelection[boardId] = '';
        if(!assetBoardById(boardId)?.sourceBoard) boardSourceSelection[boardId] = '';
        registrySelectedIds.clear();
        registrySelectionAnchorId = '';
        selectedRegistryAssetId = '';
        registryDetail = null;
        render();
        return;
    }
    if(target.closest?.('[data-board-create-close]')){ gwCall('organize', 'closeCreateLibraryModal'); return; }
    if(target.closest?.('[data-board-create-confirm]')){ await gwCall('organize', 'createPresetAssetBoard'); return; }
    if(target.closest?.('[data-registry-bootstrap-retry]')){
        // 【QIN-569 AC1】首屏失败错误条的重试入口：清错误态并重新走完整引导链路。
        registryBootstrapError = '';
        render();
        void loadAll();
        return;
    }
    if(target.closest?.('[data-registry-nav]')){
        // 【QIN-566】顶栏导航入口（项目/剧本/提示词）：激活对应系统看板，目的地与既有看板切换一致。
        const navBoard = target.closest('[data-registry-nav]').dataset.registryNav || '';
        if(assetBoardById(navBoard)) void gwCall('organize', 'activateAssetBoard', navBoard);
        // 【审核残留修复】顶栏路径同样收口持久化，避免外壳侧栏已取消选中而旧 mine:* 键残留复活。
        saveAssetTreeState();
        return;
    }
    const registryStat = target.closest?.('[data-registry-stat]');
    if(registryStat){
        const category = registryStat.dataset.registryStat || '';
        if(category === 'reference-canvas'){
            // 【QIN-563】单击「画布」统计卡：直接筛选展示全部画布资产（智能+普通+参考），不再要求双击。
            // 树选中态同步到「画布资产」节点下的「全部画布」分类子节点。
            registryStatCategory = '';
            assetTreeSelectedKey = 'system:canvas-assets:cat:all';
            activeTab = 'canvas-assets';
            activeCanvasAssetCategory = 'all';
            activeCanvasAssetCanvasId = '';
            selectedCanvasAssetId = '';
            selectedCanvasAssetIds.clear();
            saveAssetTreeState();
            render();
            return;
        }
        applyRegistryFilterDefinition({category, groupSimilar:registryGroupSimilar});
        // 【QIN-569 AC3】统计卡是资产总览的组件：从其他视图（项目/剧本/提示词等）点击统计卡
        // 即切换回资产总览并应用该分类筛选，保证「切换回资产分类仍可正常筛选」。
        if(activeTab !== 'registry') activeTab = 'registry';
        // 【审核残留修复】统计卡筛选同样收口持久化（侧栏选中清空后旧 mine:* 键不应残留）。
        saveAssetTreeState();
        // 【QIN-553】首次打开 LUT 库统计视图时，幂等登记内置 LUT 包。
        if(category === 'lut'){
            try { await ensureBuiltinLutsRegistered(); } catch(err){ setStatus(err.message || '内置 LUT 登记失败'); }
        }
        await refreshRegistryAssets();
        return;
    }
    const registryFeature = target.closest?.('[data-registry-feature-toggle]');
    if(registryFeature){ await gwCall('legacy', 'toggleRegistryFeature', registryFeature.dataset.registryFeatureToggle || ''); return; }
    if(target.closest?.('[data-registry-add-url]')){ await createRegistryRemoteAsset(); return; }
    const registryVersionOpen=target.closest?.('[data-registry-image-version-open]');
    if(registryVersionOpen){
        event.stopPropagation();
        try { await openRegistryImageVersion(registryVersionOpen.dataset.assetId || '',registryVersionOpen.dataset.registryImageVersionOpen || '',false); }
        catch(err){ setStatus(err.message||'图片版本打开失败'); showAssetFailure('图片版本打开失败',err); }
        return;
    }
    const registryVersionVisibility=target.closest?.('[data-registry-image-version-visibility]');
    if(registryVersionVisibility){
        event.stopPropagation();
        try { await setRegistryImageVersionHidden(registryVersionVisibility.dataset.assetId || '',registryVersionVisibility.dataset.registryImageVersionVisibility || '',Number(registryVersionVisibility.dataset.versionNumber||0),registryVersionVisibility.dataset.hidden!=='1'); }
        catch(err){ setStatus(err.message||'图片版本显示状态更新失败'); showAssetFailure('图片版本显示状态更新失败',err); }
        return;
    }
    const registryVersionEdit=target.closest?.('[data-registry-image-version-edit]');
    if(registryVersionEdit){
        event.stopPropagation();
        try { await openRegistryImageVersion(registryVersionEdit.dataset.assetId || '',registryVersionEdit.dataset.registryImageVersionEdit || '',true); }
        catch(err){ setStatus(err.message||'图片版本编辑失败'); showAssetFailure('图片版本编辑失败',err); }
        return;
    }
    const registryVersionDelete=target.closest?.('[data-registry-image-version-delete]');
    if(registryVersionDelete){
        event.stopPropagation();
        try { await deleteRegistryImageVersion(registryVersionDelete.dataset.assetId || '',registryVersionDelete.dataset.registryImageVersionDelete || '',Number(registryVersionDelete.dataset.versionNumber||0)); }
        catch(err){ setStatus(err.message||'图片版本删除失败'); showAssetFailure('图片版本删除失败',err); }
        return;
    }
    if(target.closest?.('[data-registry-coverflow-open]')){
        gwCall('coverflow-bridge', 'openRegistryCoverflow', target.closest('[data-registry-coverflow-open]'), {currentAssetId:selectedRegistryAssetId});
        return;
    }
    const registryLayoutBtn = target.closest?.('[data-registry-layout]');
    if(registryLayoutBtn){ registryLayout = registryLayoutBtn.dataset.registryLayout === 'list' ? 'list' : 'grid'; gwCall('settings', 'saveRegistryViewSettings'); render(); return; }
    const registryBulkPanelBtn = target.closest?.('[data-registry-bulk-panel]');
    if(registryBulkPanelBtn){
        const panel = registryBulkPanelBtn.dataset.registryBulkPanel || '';
        registryBulkPanel = registryBulkPanel === panel ? '' : panel;
        render();
        if(registryBulkPanel === 'canvas' && !registryCanvasTargets.length) await gwCall('canvas', 'loadRegistryCanvasTargets');
        return;
    }
    if(target.closest?.('[data-registry-bulk-close]')){ registryBulkPanel=''; render(); return; }
    const registryTagSuggestion = target.closest?.('[data-registry-tag-suggestion]');
    if(registryTagSuggestion){
        const input = document.getElementById('registryBulkTagInput');
        const tag = registryTagSuggestion.dataset.registryTagSuggestion || '';
        if(input && tag){ const names=parseRegistryTagNames(input.value); if(!names.includes(tag)) names.push(tag); input.value=names.join('，'); input.focus(); }
        return;
    }
    if(target.closest?.('[data-registry-tags-save]')){
        const input = document.getElementById('registryBulkTagInput');
        await gwCall('tags', 'addRegistryAssetTags', registryActiveSelectedIds(assetBoardById(activeTab)), input?.value || '');
        return;
    }
    if(target.closest?.('[data-registry-add-to-canvas]')){ await gwCall('canvas', 'attachSelectedRegistryAssetsToCanvas', false); return; }
    if(target.closest?.('[data-registry-create-canvas]')){ await gwCall('canvas', 'attachSelectedRegistryAssetsToCanvas', true); return; }
    if(target.closest?.('[data-registry-relate]')){ await relateSelectedRegistryAssets(); return; }
    const registryTagEditor = target.closest?.('[data-registry-tag-editor]');
    if(registryTagEditor){ registryTagEditorAssetId=registryTagEditor.dataset.registryTagEditor || ''; render(); requestAnimationFrame(() => document.querySelector(`[data-registry-tag-input="${registryTagEditorAssetId}"]`)?.focus()); return; }
    const registryTagPreset = target.closest?.('[data-registry-tag-preset]');
    if(registryTagPreset){
        const assetId = registryTagPreset.dataset.assetId || '';
        const input = [...document.querySelectorAll('[data-registry-tag-input]')].find(node => node.dataset.registryTagInput === assetId);
        const tag = registryTagPreset.dataset.registryTagPreset || '';
        if(input && tag){ const names=parseRegistryTagNames(input.value); if(!names.includes(tag)) names.push(tag); input.value=names.join('，'); input.focus(); }
        return;
    }
    const registryTagSave = target.closest?.('[data-registry-tag-save]');
    if(registryTagSave){
        const assetId = registryTagSave.dataset.registryTagSave || '';
        const input = [...document.querySelectorAll('[data-registry-tag-input]')].find(node => node.dataset.registryTagInput === assetId);
        await gwCall('tags', 'addRegistryAssetTags', [assetId], input?.value || '');
        return;
    }
    const registryTagDelete = target.closest?.('[data-registry-tag-delete]');
    if(registryTagDelete){ await gwCall('tags', 'deleteRegistryAssetTag', registryTagDelete.dataset.assetId || '', registryTagDelete.dataset.registryTagDelete || ''); return; }
    const registryRelatedOpen = target.closest?.('[data-registry-related-open]');
    if(registryRelatedOpen){ await gwCall('preview', 'openRegistryRelatedAsset', registryRelatedOpen.dataset.registryRelatedOpen || ''); return; }
    const registryRelationDelete = target.closest?.('[data-registry-relation-delete]');
    if(registryRelationDelete){ await gwCall('preview', 'removeRegistryAssetRelation', registryRelationDelete.dataset.assetId || '', registryRelationDelete.dataset.registryRelationDelete || ''); return; }
    const registryDeleteRemote = target.closest?.('[data-registry-delete-remote]');
    if(registryDeleteRemote){ await deleteRegistryRemoteAsset(registryDeleteRemote.dataset.registryDeleteRemote || ''); return; }
    const registryFreshApply = target.closest?.('[data-registry-fresh-apply]');
    if(registryFreshApply){ event.stopPropagation(); await refreshRegistryAssets(); return; }
    const registryBuiltinHidden = target.closest?.('[data-registry-builtin-hidden]');
    if(registryBuiltinHidden){
        event.stopPropagation();
        await toggleRegistryBuiltinHidden(registryBuiltinHidden.dataset.registryBuiltinHidden || '',
            registryBuiltinHidden.dataset.hiddenState !== '1');
        return;
    }
    if(target.closest?.('[data-registry-reveal-builtin]')){
        event.stopPropagation();
        try {
            await ensureBuiltinLutsRegistered({reveal:true});
            await Promise.all([refreshRegistryAssets(false), refreshRegistryFacetData()]);
            render();
            setStatus('已恢复显示内置 LUT');
        } catch(err){ setStatus(err.message || '恢复显示内置 LUT 失败'); }
        return;
    }
    const registryRecycle = target.closest?.('[data-registry-recycle]');
    if(registryRecycle){ event.stopPropagation(); await gwCall('recycle', 'openRegistryRecycleDialog', registryRecycle.dataset.registryRecycle || ''); return; }
    const registryRecent = target.closest?.('[data-registry-recent]');
    if(registryRecent){ const value=Number(registryRecent.dataset.registryRecent||0); registryRecentDays=registryRecentDays===value?0:value; await refreshRegistryAssetsFromRack(); return; }
    const registryProjectToggle = target.closest?.('[data-registry-project-toggle]');
    if(registryProjectToggle){
        registryProjectFacetExpanded = !registryProjectFacetExpanded;
        if(registryProjectFacetExpanded){
            void refreshRegistryProjectCounts(true);
        }
        render();
        return;
    }
    const registryProjectFilter = target.closest?.('[data-registry-project-filter]');
    if(registryProjectFilter){
        const pid = registryProjectFilter.dataset.registryProjectFilter || '';
        registryProjectFilterId = registryProjectFilterId === pid ? '' : pid;
        if(registryProjectFilterId) activeProjectId = registryProjectFilterId;
        await refreshRegistryAssetsFromRack();
        return;
    }
    // 【QIN-524】中栏目录机架：折叠成图标条、目录树展开与目录过滤
    // 【QIN-533】智能筛选页下线，不再有模式切换委托
    // 【QIN-544】左栏单栏树：标签页、展开折叠、选中、挂载、面包屑点击
    if(handleLocalUploadBackClick(event)) return;
    const treeTabBtn = target.closest?.('[data-asset-tree-tab]');
    if(treeTabBtn){ activateAssetTreeTab(treeTabBtn.dataset.assetTreeTab || ''); return; }
    const treeToggle = target.closest?.('[data-asset-tree-toggle]');
    if(treeToggle){ event.stopPropagation(); toggleAssetTreeNode(treeToggle.dataset.assetTreeToggle || ''); return; }
    const treeMount = target.closest?.('[data-asset-tree-mount]');
    if(treeMount){ gwCall('organize', 'openBoardSourceModal', '', 'new'); return; }
    const treeCrumb = target.closest?.('[data-asset-tree-crumb]');
    if(treeCrumb){ handleAssetTreeCrumbClick(treeCrumb.dataset.assetTreeCrumb || ''); return; }
    // 【QIN-546】分组标题悬停「+」：在该分组下新建子文件夹
    if(target.closest?.('[data-asset-organize-group-new]')){
        event.stopPropagation();
        gwCall('organize', 'openAssetOrganizeCreateDialog', null, assetOrganizeDefaultRootId());
        return;
    }
    // 【QIN-546】双击改名：单击选中会异步重绘整棵树，原生 dblclick 的第二击常落在
    // 重建后的新节点上而丢失，因此用 click 的 detail 计数兜底判定双击。
    const treeNode = target.closest?.('[data-asset-tree-node]');
    if(treeNode && Number(event.detail) >= 2 && target.closest?.('.asset-tree-panel .asset-tree-name')){
        const dblTarget = assetOrganizeTargetFromKey(treeNode.dataset.assetTreeNode || '');
        if(dblTarget && !dblTarget.isRoot){
            event.preventDefault();
            gwCall('organize', 'beginAssetOrganizeRename', dblTarget);
            return;
        }
    }
    if(handleAssetTreeNodeClick(event)) return;
    // 【QIN-547】收集箱：上传、迁移托管资产、改位置与来源筛选
    if(target.closest?.('[data-inbox-upload]')){
        const inboxInput = document.getElementById('inboxFileInput');
        if(inboxInput){
            inboxInput.value = '';
            inboxInput.onchange = async () => { await uploadAssetInboxFiles(inboxInput.files); };
            inboxInput.click();
        }
        return;
    }
    if(target.closest?.('[data-inbox-migrate-run]')){ await gwCall('inbox', 'migrateAssetLibraryToInbox'); return; }
    if(target.closest?.('[data-inbox-settings-toggle]')){ inboxSettingsOpen = !inboxSettingsOpen; render(); return; }
    if(target.closest?.('[data-inbox-settings-cancel]')){ inboxSettingsOpen = false; render(); return; }
    if(target.closest?.('[data-inbox-settings-save]')){ await gwCall('inbox', 'saveAssetInboxSettings'); return; }
    const inboxSourceChip = target.closest?.('[data-inbox-source]');
    if(inboxSourceChip){ inboxSourceFilter = inboxSourceChip.dataset.inboxSource || ''; render(); return; }
    // 【QIN-588 AC2】点击收集箱卡片打开媒体预览浮层。
    const inboxCard = target.closest?.('[data-inbox-card]');
    if(inboxCard){ gwCall('inbox', 'openInboxMediaPreview', inboxCard.dataset.inboxCard || ''); return; }
    if(target.closest?.('[data-asset-rack-collapse]')){ gwCall('settings', 'setAssetRackCollapsed', true); return; }
    if(target.closest?.('[data-asset-rack-expand]')){ gwCall('settings', 'setAssetRackCollapsed', false); return; }
    const assetRackToggle = target.closest?.('[data-asset-rack-toggle]');
    if(assetRackToggle){ gwCall('settings', 'toggleAssetRackFolder', assetRackToggle.dataset.assetRackToggle || ''); return; }
    const assetRackFolder = target.closest?.('[data-asset-rack-folder-root]');
    if(assetRackFolder){ gwCall('settings', 'selectAssetRackFolder', assetRackFolder.dataset.assetRackFolderRoot || '', assetRackFolder.dataset.assetRackFolderPath || ''); return; }
    if(target.closest?.('[data-asset-rack-expand-all]')){ gwCall('settings', 'toggleAssetRackFoldersAll'); return; }
    const assetRackFolderCreate = target.closest?.('[data-asset-rack-folder-create]');
    if(assetRackFolderCreate){ await gwCall('settings', 'createAssetRackFolder', assetRackFolderCreate.dataset.assetRackFolderCreate || ''); return; }
    if(target.closest?.('[data-asset-rack-clear-folder]')){ gwCall('settings', 'clearAssetRackFolder'); return; }
    // 【QIN-527】空状态引导：直接唤起登记素材来源/素材库根目录的既有弹窗
    if(target.closest?.('[data-asset-rack-register-root]')){
        const board = assetRackBoard();
        gwCall('organize', 'openBoardSourceModal', board ? board.id : '', 'new');
        return;
    }
    const registryKindBtn = target.closest?.('[data-registry-kind]');
    if(registryKindBtn){ const value=registryKindBtn.dataset.registryKind||''; registryKind=registryKind===value?'':value; await refreshRegistryAssetsFromRack(); return; }
    const registryRootBtn = target.closest?.('[data-registry-root]');
    if(registryRootBtn){ const value=registryRootBtn.dataset.registryRoot||''; registryRootId=registryRootId===value?'':value; await refreshRegistryAssetsFromRack(); return; }
    const registryCollectionBtn = target.closest?.('[data-registry-collection]');
    if(registryCollectionBtn){ const value=registryCollectionBtn.dataset.registryCollection||''; registryCollectionId=registryCollectionId===value?'':value; await refreshRegistryAssetsFromRack(); return; }
    const registryTagBtn = target.closest?.('[data-registry-tag]');
    if(registryTagBtn){ const id=registryTagBtn.dataset.registryTag||''; if(registryIncludeTags.has(id)) registryIncludeTags.delete(id); else {registryIncludeTags.add(id);registryExcludeTags.delete(id);} await refreshRegistryAssetsFromRack(); return; }
    const registryTagExclude = target.closest?.('[data-registry-tag-exclude]');
    if(registryTagExclude){ const id=registryTagExclude.dataset.registryTagExclude||''; if(registryExcludeTags.has(id)) registryExcludeTags.delete(id); else {registryExcludeTags.add(id);registryIncludeTags.delete(id);} await refreshRegistryAssetsFromRack(); return; }
    const registryRemoveTag = target.closest?.('[data-registry-remove-tag]');
    if(registryRemoveTag){ const id=registryRemoveTag.dataset.registryRemoveTag||''; registryIncludeTags.delete(id); registryExcludeTags.delete(id); await refreshRegistryAssetsFromRack(); return; }
    const registryClear = target.closest?.('[data-registry-clear]');
    if(registryClear){ const key=registryClear.dataset.registryClear||''; if(key==='all'){ applyRegistryFilterDefinition({groupSimilar:registryGroupSimilar}); registryProjectFilterId=''; const board = assetRackBoard(); if(board){ boardSourceSelection[board.id]=''; boardFolderSelection[board.id]=''; boardGroupSelection[board.id]=''; boardReferenceCanvasSelection[board.id]=''; } } else if(key==='project'){ registryProjectFilterId=''; } else if(key==='recent') registryRecentDays=0; else if(key==='kind') registryKind=''; else if(key==='category') registryStatCategory=''; else if(key==='collection') registryCollectionId=''; else if(key==='root') registryRootId=''; await refreshRegistryAssetsFromRack(); return; }
    if(target.closest?.('[data-registry-preset-save]')){ await gwCall('settings', 'saveRegistryPreset'); return; }
    const registryPreset = target.closest?.('[data-registry-preset]');
    if(registryPreset){ const item=registryPresets.find(value=>value.id===(registryPreset.dataset.registryPreset||'')); if(item){applyRegistryFilterDefinition(item.definition||{});await refreshRegistryAssetsFromRack();} return; }
    const registryPresetDelete = target.closest?.('[data-registry-preset-delete]');
    if(registryPresetDelete){ await gwCall('organize', 'deleteRegistryPreset', registryPresetDelete.dataset.registryPresetDelete||''); return; }
    const registryAspectToggle = target.closest?.('[data-registry-aspect-toggle]');
    if(registryAspectToggle){
        registryAspect = registryAspect === 'square' ? 'original' : 'square';
        gwCall('settings', 'saveRegistryViewSettings');
        render();
        return;
    }
    if(target.closest?.('[data-registry-simple-toggle]')){ registrySimple=!registrySimple; gwCall('settings', 'saveRegistryViewSettings'); render(); return; }
    if(target.closest?.('[data-registry-group-selected]')){ gwCall('organize', 'addSelectedRegistryAssetsToGroup', assetBoardById(activeTab)); return; }
    if(target.closest?.('[data-registry-group-remove]')){ gwCall('organize', 'removeSelectedRegistryAssetsFromGroups', assetBoardById(activeTab)); return; }
    if(target.closest?.('[data-registry-structure-merge]')){
        // 【QIN-534】底部批量栏的「合并为版本 / 合并为组」：用当前选择集，与右键菜单共用同一实现。
        const mergeBtn = target.closest('[data-registry-structure-merge]');
        await runRegistryStructureAction(mergeBtn.dataset.registryStructureMerge || '', registryActiveSelectedIds(assetBoardById(activeTab)));
        return;
    }
    if(target.closest?.('[data-registry-bulk-release]')){
        // 【QIN-534 反馈 4】底部批量栏的「解除版本 / 解除组 / 解除关联」：
        // 用独立属性，避免与右键菜单的 data-registry-structure-release 互相命中导致重复触发。
        const releaseBtn = target.closest('[data-registry-bulk-release]');
        await runRegistryStructureAction(`release-${releaseBtn.dataset.registryBulkRelease || ''}`, registryActiveSelectedIds(assetBoardById(activeTab)));
        return;
    }
    if(target.closest?.('[data-registry-archive]')){ await gwCall('organize', 'downloadSelectedRegistryArchive', assetBoardById(activeTab)); return; }
    if(target.closest?.('[data-registry-export-pdf]')){
        const board = assetBoardById(activeTab);
        await gwCall('organize', 'downloadRegistryPdf', registryActiveSelectedIds(board),`${board?.name || '资产中心'}-${new Date().toISOString().slice(0,10)}`);
        return;
    }
    if(target.closest?.('[data-registry-clear-selection]')){ registrySelectedIds.clear(); registrySelectionAnchorId=''; registryManageMode=false; registryBulkPanel=''; render(); return; }
    if(target.closest?.('[data-registry-copy-ids]')){ await copyTextToClipboard([...registrySelectedIds].join('\n'));setStatus('资产 ID 已复制');return; }
    const registryCheck = target.closest?.('[data-registry-check]');
    if(registryCheck){ event.stopPropagation(); const id=registryCheck.dataset.registryCheck||''; if(registrySelectedIds.has(id))registrySelectedIds.delete(id);else registrySelectedIds.add(id);registrySelectionAnchorId=id;registryManageMode=registrySelectedIds.size>0;syncRegistrySelectionDom();return; }
    if(target.closest?.('[data-registry-load-more]')){ await loadMoreRegistryAssets(); return; }
    const registryAssetsBlank = target.closest?.('.registry-assets-scroll');
    if(registryAssetsBlank && !target.closest?.('[data-registry-card]')){
        if(Date.now() < registryMarqueeSuppressClickUntil) return;
        clearRegistrySelectionDom();
        return;
    }
    const videoJump = target.closest?.('[data-video-jump-sec]');
    if(videoJump){
        event.stopPropagation();
        const rawSec = parseFloat(videoJump.dataset.videoJumpSec);
        const fps = parseFloat(videoJump.dataset.videoFrameRate || '') || 0;
        const assetId = videoJump.dataset.videoAssetId || selectedRegistryAssetId;
        const currentItem = detailViewerVideoItem();
        const effectiveFps = fps || Number(currentItem?.frame_rate || currentItem?.metadata?.frame_rate || 0);
        const sec = alignTimeToFps(rawSec, effectiveFps);
        if(Number.isFinite(sec)){
            if(detailViewerState.open){
                seekDetailVideoTo(sec, { play: true, fps: effectiveFps });
            } else if(assetId){
                gwCall('preview', 'showDetailPreview', 'registry', assetId, { startTime: sec, fps: effectiveFps });
            }
        }
        return;
    }
    const registryCard = target.closest?.('[data-registry-card]');
    if(registryCard){
        const id = registryCard.dataset.registryCard || '';
        const visible = activeRegistryVisibleAssets();
        if(event.shiftKey){
            const anchorId = registrySelectionAnchorId || selectedRegistryAssetId || id;
            const anchorIndex = Math.max(0, visible.findIndex(item => item.id === anchorId));
            const targetIndex = Math.max(0, visible.findIndex(item => item.id === id));
            if(!(event.ctrlKey || event.metaKey)) registrySelectedIds.clear();
            visible.slice(Math.min(anchorIndex,targetIndex), Math.max(anchorIndex,targetIndex) + 1).forEach(item => registrySelectedIds.add(item.id));
            registrySelectionAnchorId = anchorId;
            selectedRegistryAssetId = id;
            registryDetail = registryAssets.find(item => item.id === id) || null;
            registryManageMode = true;
            syncRegistrySelectionDom();
        } else if(event.ctrlKey || event.metaKey){
            if(!registrySelectedIds.size && selectedRegistryAssetId && selectedRegistryAssetId !== id) registrySelectedIds.add(selectedRegistryAssetId);
            if(registrySelectedIds.has(id)) registrySelectedIds.delete(id); else registrySelectedIds.add(id);
            registrySelectionAnchorId = id;
            selectedRegistryAssetId = id;
            registryDetail = registryAssets.find(item => item.id === id) || null;
            registryManageMode = registrySelectedIds.size > 0;
            syncRegistrySelectionDom();
        } else {
            registrySelectedIds.clear();
            registrySelectionAnchorId = id;
            registryManageMode = false;
            selectedRegistryAssetId = id;
            registryDetail = registryAssets.find(item => item.id === id) || null;
            syncRegistrySelectionDom();
        }
        return;
    }

    const registryPreview = target.closest?.('[data-registry-preview]');
    if(registryPreview){ gwCall('preview', 'showDetailPreview', 'registry',registryPreview.dataset.registryPreview||'');return; }
    if(target.closest?.('[data-project-create]')){ await createRegistryProject();return; }
    const projectBtn = target.closest?.('[data-project]');
    if(projectBtn){ selectedProjectEntityId='';await gwCall('organize', 'loadProjectBoard', projectBtn.dataset.project||'');return; }
    if(target.closest?.('[data-project-add-entity]')){ await addRegistryProjectEntity();return; }
    if(target.closest?.('[data-project-proxy-settings]')){ gwCall('settings', 'openProjectProxySettings');return; }
    if(target.closest?.('[data-project-template]')){ await editRegistryProjectTemplate();return; }
    const projectEntity = target.closest?.('[data-project-entity]');
    if(projectEntity){ selectedProjectEntityId=projectEntity.dataset.projectEntity||'';render();return; }
    const projectEntityStatus = target.closest?.('[data-project-entity-status]');
    if(projectEntityStatus){ await cycleRegistryProjectEntity(projectEntityStatus.dataset.projectEntityStatus||'',projectEntityStatus.dataset.status||'pending');return; }
    const projectGate = target.closest?.('[data-project-gate]');
    if(projectGate){ await cycleRegistryProjectGate(projectGate.dataset.projectGate||'',projectGate.dataset.status||'pending');return; }
    if(target.closest?.('[data-project-link-selected]')){ await gwCall('organize', 'linkSelectedRegistryAssets');return; }
    const localLibraryRename = target.closest?.('[data-local-library-rename]');
    if(localLibraryRename){ await gwCall('upload', 'renameActiveLocalLibrary'); return; }
    const localLibraryTab = target.closest?.('[data-local-library-tab]');
    if(localLibraryTab){
        if(Date.now() < localLibraryDragBlockUntil) return;
        await gwCall('upload', 'switchLocalLibrary', localLibraryTab.dataset.localLibraryTab || 'local');
        return;
    }
    if(target.closest?.('[data-project-directory-template-manager-open]')){ await gwCall('settings', 'openProjectDirectoryTemplateManagerSheet'); return; }
    if(target.closest?.('[data-project-directory-template-manager-close]')){ gwCall('settings', 'closeProjectDirectoryTemplateManagerSheet'); return; }
    const projectDirectoryTemplateSelect = target.closest?.('[data-project-directory-template-select]');
    if(projectDirectoryTemplateSelect){
        gwCall('settings', 'requestProjectDirectoryTemplatePendingAction', {kind:'select', templateId:projectDirectoryTemplateSelect.dataset.projectDirectoryTemplateSelect || ''});
        return;
    }
    if(target.closest?.('[data-project-directory-template-new]')){ gwCall('settings', 'requestProjectDirectoryTemplatePendingAction', {kind:'new'}); return; }
    const projectDirectoryTemplateCopy = target.closest?.('[data-project-directory-template-copy]');
    if(projectDirectoryTemplateCopy){
        gwCall('settings', 'requestProjectDirectoryTemplatePendingAction', {kind:'copy', templateId:projectDirectoryTemplateCopy.dataset.projectDirectoryTemplateCopy || ''});
        return;
    }
    if(target.closest?.('[data-project-directory-template-discard-cancel]')){
        projectDirectoryTemplateManagerState = {...projectDirectoryTemplateManagerState, pendingAction:null};
        gwCall('settings', 'renderProjectDirectoryTemplateManagerSheet');
        return;
    }
    if(target.closest?.('[data-project-directory-template-discard-confirm]')){
        const action = projectDirectoryTemplateManagerState.pendingAction;
        if(projectDirectoryTemplateManagerState.editor) projectDirectoryTemplateManagerState.editor.dirty = false;
        projectDirectoryTemplateManagerState = {...projectDirectoryTemplateManagerState, pendingAction:null};
        gwCall('settings', 'runProjectDirectoryTemplatePendingAction', action);
        return;
    }
    if(target.closest?.('[data-project-directory-template-save]')){ await gwCall('settings', 'saveProjectDirectoryTemplate'); return; }
    const projectDirectoryTemplateDefault = target.closest?.('[data-project-directory-template-default]');
    if(projectDirectoryTemplateDefault){ await gwCall('settings', 'setProjectDirectoryTemplateDefault', projectDirectoryTemplateDefault.dataset.projectDirectoryTemplateDefault || ''); return; }
    const projectDirectoryTemplateArchive = target.closest?.('[data-project-directory-template-archive]');
    if(projectDirectoryTemplateArchive){
        projectDirectoryTemplateManagerState = {...projectDirectoryTemplateManagerState, archiveId:projectDirectoryTemplateArchive.dataset.projectDirectoryTemplateArchive || ''};
        gwCall('settings', 'renderProjectDirectoryTemplateManagerSheet');
        return;
    }
    if(target.closest?.('[data-project-directory-template-archive-cancel]')){
        projectDirectoryTemplateManagerState = {...projectDirectoryTemplateManagerState, archiveId:''};
        gwCall('settings', 'renderProjectDirectoryTemplateManagerSheet');
        return;
    }
    if(target.closest?.('[data-project-directory-template-archive-confirm]')){ await gwCall('settings', 'archiveProjectDirectoryTemplate', projectDirectoryTemplateManagerState.archiveId); return; }
    if(target.closest?.('[data-storage-close]')){ gwCall('settings', 'closeStorageSettings'); return; }
    const boardVisibility = target.closest?.('[data-board-visibility]');
    if(boardVisibility){
        const id = boardVisibility.dataset.boardVisibility || '';
        gwCall('organize', 'setAssetBoardHidden', id, boardVisibility.getAttribute('aria-checked') === 'true');
        gwCall('settings', 'renderStorageSettingsModal');
        return;
    }
    const boardEdit = target.closest?.('[data-board-edit]');
    if(boardEdit){ gwCall('organize', 'openBoardEditModal', boardEdit.dataset.boardEdit || ''); return; }
    if(target.closest?.('[data-board-edit-close]')){ gwCall('organize', 'closeBoardEditModal'); return; }
    if(target.closest?.('[data-board-edit-save]')){ await gwCall('organize', 'saveBoardEdit'); return; }
    const boardDelete = target.closest?.('[data-board-delete]');
    if(boardDelete){ gwCall('organize', 'deleteCustomAssetBoard', boardDelete.dataset.boardDelete || ''); return; }
    const sourceBoardDelete = target.closest?.('[data-source-board-delete]');
    if(sourceBoardDelete){
        await gwCall('organize', 'removeGeneratedSourceBoard', sourceBoardDelete.dataset.sourceBoardDelete || '', sourceBoardDelete.dataset.sourceBoardName || '素材库');
        return;
    }
    if(target.closest?.('[data-storage-save]')){
        if(directorySettingsPage && (!directorySettingsReady || directorySettingsSaving)) return;
        if(directorySettingsPage){
            gwCall('settings', 'syncStorageSettingsInputsToState');
            if(['upload','generated','local','projects'].some(key => !storageSettingsState.dirs[key])){ setStatus('素材与项目目录不能为空'); return; }
            thumbnailGeneratorState.directory = document.getElementById('thumbnailDirectory')?.value.trim() || '';
            directorySettingsSaving = true;
            gwCall('settings', 'renderGlobalDirectorySettings');
        }
        try { await saveStorageSettings(); }
        catch(err){ setStatus(err.message || '保存存储设置失败'); }
        finally { if(directorySettingsPage){ directorySettingsSaving = false; gwCall('settings', 'renderGlobalDirectorySettings'); } }
        return;
    }
    if(guardMatchesManagedSelection(target)){
        event.preventDefault();
        event.stopPropagation();
        return;
    }
    if(activeTab === 'assets' && assetManageMode){
        const assetCheck = target.closest?.('[data-asset-check]');
        const assetCard = target.closest?.('[data-asset-card]');
        if(assetCheck || assetCard){
            event.preventDefault();
            event.stopPropagation();
            const id = assetCheck?.dataset.assetCheck || assetCard?.dataset.assetCard || '';
            const selected = toggleSelectionSet(selectedAssetIds, id);
            selectedAssetId = selected ? id : (selectedAssetId === id ? '' : selectedAssetId);
            pendingBatchDelete = '';
            gwCall('organize', 'syncAssetSelectionDom');
            return;
        }
    }
    if(activeTab === 'local' && localUploadManageMode){
        const localUpCheck = target.closest?.('[data-localup-check]');
        const localUpCard = target.closest?.('[data-localup-card]');
        if(localUpCheck || localUpCard){
            event.preventDefault();
            event.stopPropagation();
            const id = localUpCheck?.dataset.localupCheck || localUpCard?.dataset.localupCard || '';
            const selected = toggleSelectionSet(selectedLocalUploadIds, id);
            selectedLocalUploadId = selected ? id : (selectedLocalUploadId === id ? '' : selectedLocalUploadId);
            pendingBatchDelete = '';
            gwCall('upload', 'refreshLocalUploadSelectionOnly');
            return;
        }
    }
    if(activeTab === 'local' && localManageMode){
        const localCheck = target.closest?.('[data-local-check]');
        const localCard = target.closest?.('[data-local-card]');
        if(localCheck || localCard){
            event.preventDefault();
            event.stopPropagation();
            const id = localCheck?.dataset.localCheck || localCard?.dataset.localCard || '';
            const selected = toggleSelectionSet(selectedLocalIds, id);
            selectedLocalId = selected ? id : (selectedLocalId === id ? '' : selectedLocalId);
            pendingBatchDelete = '';
            gwCall('upload', 'syncLocalSelectionDom');
            return;
        }
    }
    if(activeTab === 'workflows' && workflowManageMode){
        const workflowCheck = target.closest?.('[data-workflow-check]');
        const workflowCard = target.closest?.('[data-workflow-card]');
        if(workflowCheck || workflowCard){
            event.preventDefault();
            event.stopPropagation();
            const id = workflowCheck?.dataset.workflowCheck || workflowCard?.dataset.workflowCard || '';
            const selected = toggleSelectionSet(selectedWorkflowIds, id);
            selectedWorkflowId = selected ? id : (selectedWorkflowId === id ? '' : selectedWorkflowId);
            pendingBatchDelete = '';
            gwCall('organize', 'syncWorkflowSelectionDom');
            return;
        }
    }
    if(activeTab === 'prompts' && promptManageMode){
        const promptCheck = target.closest?.('[data-prompt-check]');
        const promptRow = target.closest?.('[data-prompt-row]');
        if(promptCheck || promptRow){
            event.preventDefault();
            event.stopPropagation();
            const id = promptCheck?.dataset.promptCheck || promptRow?.dataset.promptRow || '';
            const selected = toggleSelectionSet(selectedPromptIds, id);
            selectedPromptId = selected ? id : (selectedPromptId === id ? '' : selectedPromptId);
            promptEditMode = false;
            promptCreateMode = false;
            pendingBatchDelete = '';
            render();
            return;
        }
    }
    if(activeTab === 'canvas-assets' && canvasAssetManageMode){
        const canvasCheck = target.closest?.('[data-canvas-asset-check]');
        const canvasCard = target.closest?.('[data-canvas-asset-card]');
        if(canvasCheck || canvasCard){
            event.preventDefault();
            event.stopPropagation();
            const id = canvasCheck?.dataset.canvasAssetCheck || canvasCard?.dataset.canvasAssetCard || '';
            const selected = toggleSelectionSet(selectedCanvasAssetIds, id);
            selectedCanvasAssetId = selected ? id : (selectedCanvasAssetId === id ? '' : selectedCanvasAssetId);
            gwCall('canvas', 'refreshCanvasAssetSelectionOnly');
            return;
        }
    }
    const tabBtn = target.closest?.('[data-tab]');
    if(tabBtn){
        activeTab = tabBtn.dataset.tab || 'assets';
        selectedAssetIds.clear(); selectedWorkflowIds.clear(); selectedPromptIds.clear(); selectedLocalIds.clear(); selectedLocalUploadIds.clear(); selectedCanvasAssetIds.clear();
        if(activeTab === 'local' && !localAssetsLoaded){
            const loading = loadLocalAssets();
            render();
            await loading;
            if(activeTab === 'local') render();
        } else {
            render();
        }
        return;
    }
    if(handleProvenanceNodeClick(event)) return;
    const collectDeleteAssetId = resolveCollectDeleteAssetId(event);
    if(collectDeleteAssetId){ await gwCall('recycle', 'openRegistryRecycleDialog', collectDeleteAssetId); return; }
    const assetPreview = target.closest?.('[data-asset-preview]');
    if(assetPreview){ gwCall('preview', 'showDetailPreview', 'asset', assetPreview.dataset.assetPreview || ''); return; }
    const canvasAssetPreview = target.closest?.('[data-canvas-asset-preview]');
    if(canvasAssetPreview){ gwCall('preview', 'showDetailPreview', 'canvas-asset', canvasAssetPreview.dataset.canvasAssetPreview || ''); return; }
    const localPreview = target.closest?.('[data-local-preview]');
    if(localPreview){ gwCall('preview', 'showDetailPreview', 'local', localPreview.dataset.localPreview || ''); return; }
    const localUpPreview = target.closest?.('[data-localup-preview]');
    if(localUpPreview){ gwCall('preview', 'showDetailPreview', 'localup', localUpPreview.dataset.localupPreview || ''); return; }
    if(target.closest?.('[data-localup-upload]')){ uploadInput?.click(); return; }
    if(target.closest?.('[data-localup-load-more]')){
        const loading = loadMoreLocalAssets();
        render();
        await loading;
        if(activeTab === 'local') render();
        return;
    }
    if(target.closest?.('[data-localup-retry]')){
        const loading = localAssetsHasMore
            ? loadMoreLocalAssets()
            : loadLocalAssets();
        render();
        await loading;
        if(activeTab === 'local') render();
        return;
    }
    if(target.closest?.('[data-localup-folder-new]')){ await gwCall('upload', 'createLocalUploadFolder'); return; }
    if(target.closest?.('[data-localup-folder-rename]')){ await gwCall('upload', 'renameLocalUploadFolder'); return; }
    const localUpFolder = target.closest?.('[data-localup-folder]');
    if(localUpFolder){
        activeLocalUploadFolder = localUpFolder.dataset.localupFolder || '';
        activeLocalUploadClassFilter = '';
        selectedLocalUploadId = '';
        selectedLocalUploadIds.clear();
        pendingBatchDelete = '';
        render();
        return;
    }
    if(target.closest?.('[data-localup-class-root]')){
        if(!localUploadClassEntries().length) setStatus('暂无智能分类，请先选择图片并点击“智能分类”');
        return;
    }
    const localUpClassGroup = target.closest?.('[data-localup-class-group]');
    if(localUpClassGroup){
        const groupId = localUpClassGroup.dataset.localupClassGroup || '';
        openLocalUploadClassGroup = openLocalUploadClassGroup === groupId ? '' : groupId;
        render();
        return;
    }
    const localUpClass = target.closest?.('[data-localup-class-filter]');
    if(localUpClass){
        const nextFilter = localUpClass.dataset.localupClassFilter || '';
        activeLocalUploadClassFilter = activeLocalUploadClassFilter === nextFilter ? '' : nextFilter;
        // 不再联动展开左侧「智能分类」分组；也不再清空预览选中——切换分类时保持当前预览的那张图，
        // 方便频繁切分类找相似图片。
        selectedLocalUploadIds.clear();
        pendingBatchDelete = '';
        render();
        return;
    }
    if(target.closest?.('[data-localup-manage]')){
        localUploadManageMode = !localUploadManageMode;
        if(!localUploadManageMode) selectedLocalUploadIds.clear();
        render();
        return;
    }
    if(target.closest?.('[data-localup-select-all]')){ gwCall('upload', 'localUploadItems').forEach(item => selectedLocalUploadIds.add(item.id)); render(); return; }
    if(target.closest?.('[data-localup-clear]')){ selectedLocalUploadIds.clear(); render(); return; }
    if(target.closest?.('[data-localup-download-selected]')){ await gwCall('upload', 'downloadSelectedLocalUploads'); return; }
    if(target.closest?.('[data-localup-canvas-selected]')){ gwCall('canvas', 'copySelectedLocalUploadsToCanvas'); return; }
    if(target.closest?.('[data-localup-cut-selected]')){ gwCall('upload', 'setLocalUploadClipboard'); return; }
    if(target.closest?.('[data-localup-paste-clipboard]')){ await gwCall('upload', 'pasteLocalUploadClipboard'); return; }
    if(target.closest?.('[data-localup-clear-clipboard]')){ localUploadClipboard = null; render(); return; }
    const localUpDownload = target.closest?.('[data-localup-download]');
    if(localUpDownload){ gwCall('upload', 'downloadLocalUpload', localUpDownload.dataset.localupDownload || ''); return; }
    if(target.closest?.('[data-localup-delete-selected]')){ await gwCall('upload', 'deleteLocalAssets', [...selectedLocalUploadIds]); return; }
    if(target.closest?.('[data-local-classify-toggle]')){ localClassifyPromptOpen = !localClassifyPromptOpen; render(); return; }
    if(target.closest?.('[data-local-classify-run]')){ await gwCall('settings', 'runLocalUploadClassifySelected'); return; }
    if(target.closest?.('[data-local-caption-run]')){ await gwCall('settings', 'runLocalUploadCaptionSelected'); return; }
    const localUpCaptionOne = target.closest?.('[data-localup-caption-one]');
    if(localUpCaptionOne){ await gwCall('settings', 'runLocalUploadCaptionOne', localUpCaptionOne.dataset.localupCaptionOne || ''); return; }
    const localUpCaptionCopy = target.closest?.('[data-localup-caption-copy]');
    if(localUpCaptionCopy){ await gwCall('settings', 'copyLocalUploadCaption', localUpCaptionCopy.dataset.localupCaptionCopy || ''); return; }
    const localUpCaptionSave = target.closest?.('[data-localup-caption-save]');
    if(localUpCaptionSave){ await gwCall('settings', 'saveLocalUploadCaption', localUpCaptionSave.dataset.localupCaptionSave || ''); return; }
    const localUpRename = target.closest?.('[data-localup-rename]');
    if(localUpRename){ event.stopPropagation(); gwCall('upload', 'beginLocalUploadInlineRename', localUpRename.dataset.localupRename || ''); return; }
    const localUpDeleteOne = target.closest?.('[data-localup-delete-one]');
    if(localUpDeleteOne){ await gwCall('upload', 'deleteLocalAssets', [localUpDeleteOne.dataset.localupDeleteOne || '']); return; }
    const localUpCheck = target.closest?.('[data-localup-check]');
    if(localUpCheck){
        const id = localUpCheck.dataset.localupCheck || '';
        const selected = toggleSelectionSet(selectedLocalUploadIds, id);
        selectedLocalUploadId = selected ? id : (selectedLocalUploadId === id ? '' : selectedLocalUploadId);
        pendingBatchDelete = '';
        render();
        return;
    }
    const localUpOpen = target.closest?.('[data-localup-open]');
    if(localUpOpen){ const it = findLocalUpload(localUpOpen.dataset.localupOpen || ''); if(it?.url) window.open(it.url, '_blank'); return; }
    const localUpCopy = target.closest?.('[data-localup-copy]');
    if(localUpCopy){ const it = findLocalUpload(localUpCopy.dataset.localupCopy || ''); const ok = await copyTextToClipboard(it?.url || ''); setStatus(ok ? '已复制链接' : '复制失败'); return; }
    const localUpCard = target.closest?.('[data-localup-card]');
    if(localUpCard){
        const id = localUpCard.dataset.localupCard || '';
        if(localUploadManageMode){
            const selected = toggleSelectionSet(selectedLocalUploadIds, id);
            selectedLocalUploadId = selected ? id : (selectedLocalUploadId === id ? '' : selectedLocalUploadId);
            pendingBatchDelete = '';
            render();
        } else {
            selectedLocalUploadId = id;
            pendingBatchDelete = '';
            gwCall('upload', 'refreshLocalUploadSelectionOnly');
        }
        return;
    }
    if(target.closest?.('[data-local-pick-folder]')){ await gwCall('upload', 'registerSharedFolder'); return; }
    const canvasAssetCat = target.closest?.('[data-canvas-asset-cat]');
    if(canvasAssetCat){
        activeCanvasAssetCategory = canvasAssetCat.dataset.canvasAssetCat || defaultCanvasAssetCategory();
        activeCanvasAssetCanvasId = '';
        selectedCanvasAssetId = '';
        selectedCanvasAssetIds.clear();
        render();
        return;
    }
    const canvasAssetCanvas = target.closest?.('[data-canvas-asset-canvas]');
    if(canvasAssetCanvas){
        activeCanvasAssetCategory = canvasAssetCanvas.dataset.canvasAssetCanvasCat || activeCanvasAssetCategory || 'all';
        activeCanvasAssetCanvasId = canvasAssetCanvas.dataset.canvasAssetCanvas || '';
        selectedCanvasAssetId = '';
        selectedCanvasAssetIds.clear();
        render();
        return;
    }
    if(target.closest?.('[data-canvas-asset-manage]')){
        canvasAssetManageMode = !canvasAssetManageMode;
        if(!canvasAssetManageMode) selectedCanvasAssetIds.clear();
        render();
        return;
    }
    if(target.closest?.('[data-canvas-asset-refresh]')){ await gwCall('canvas', 'refreshCanvasAssets'); return; }
    if(target.closest?.('[data-canvas-asset-select-all]')){ gwCall('canvas', 'currentCanvasAssetItems').forEach(item => selectedCanvasAssetIds.add(item.id)); render(); return; }
    if(target.closest?.('[data-canvas-asset-clear-selection]')){ selectedCanvasAssetIds.clear(); render(); return; }
    if(target.closest?.('[data-canvas-asset-download-selected]')){ await gwCall('canvas', 'downloadCanvasAssetItems', [...selectedCanvasAssetIds]); return; }
    const canvasAssetDownload = target.closest?.('[data-canvas-asset-download]');
    if(canvasAssetDownload){ await gwCall('canvas', 'downloadCanvasAssetItems', [canvasAssetDownload.dataset.canvasAssetDownload || '']); return; }
    const canvasAssetOpen = target.closest?.('[data-canvas-asset-open]');
    if(canvasAssetOpen){ const it = findCanvasAssetItem(canvasAssetOpen.dataset.canvasAssetOpen || ''); if(it?.url) window.open(it.url, '_blank', 'noopener'); return; }
    const canvasAssetCopy = target.closest?.('[data-canvas-asset-copy]');
    if(canvasAssetCopy){ const it = findCanvasAssetItem(canvasAssetCopy.dataset.canvasAssetCopy || ''); const ok = await copyTextToClipboard(it?.url || ''); setStatus(ok ? '已复制画布资产链接' : '复制失败'); return; }
    const canvasAssetCheck = target.closest?.('[data-canvas-asset-check]');
    if(canvasAssetCheck){
        event.preventDefault();
        event.stopPropagation();
        if(canvasAssetManageMode){
            const id = canvasAssetCheck.dataset.canvasAssetCheck || '';
            const selected = toggleSelectionSet(selectedCanvasAssetIds, id);
            selectedCanvasAssetId = selected ? id : (selectedCanvasAssetId === id ? '' : selectedCanvasAssetId);
            gwCall('canvas', 'refreshCanvasAssetSelectionOnly');
        }
        return;
    }
    const canvasAssetCard = target.closest?.('[data-canvas-asset-card]');
    if(canvasAssetCard){
        const id = canvasAssetCard.dataset.canvasAssetCard || '';
        if(canvasAssetManageMode){
            const selected = toggleSelectionSet(selectedCanvasAssetIds, id);
            selectedCanvasAssetId = selected ? id : (selectedCanvasAssetId === id ? '' : selectedCanvasAssetId);
        } else {
            selectedCanvasAssetId = id;
        }
        gwCall('canvas', 'refreshCanvasAssetSelectionOnly');
        return;
    }
    const sharedRemove = target.closest?.('[data-shared-remove]');
    if(sharedRemove){ event.stopPropagation(); await gwCall('upload', 'unregisterSharedFolder', sharedRemove.dataset.sharedRemove || ''); return; }
    const sharedOpen = target.closest?.('[data-shared-open]');
    if(sharedOpen){ await gwCall('upload', 'openSharedFolder', sharedOpen.dataset.sharedOpen || ''); return; }
    if(target.closest?.('[data-local-manage]')){
        localManageMode = !localManageMode;
        pendingBatchDelete = '';
        if(!localManageMode) selectedLocalIds.clear();
        render();
        return;
    }
    if(target.closest?.('[data-local-select-all]')){ gwCall('upload', 'localItemsForFolder').forEach(item => selectedLocalIds.add(item.id)); pendingBatchDelete = ''; render(); return; }
    if(target.closest?.('[data-local-clear-selection]')){ selectedLocalIds.clear(); pendingBatchDelete = ''; render(); return; }
    if(target.closest?.('[data-local-copy-selected]')){ gwCall('upload', 'setLocalClipboard', 'copy'); return; }
    if(target.closest?.('[data-local-import-clipboard]')){ await gwCall('upload', 'pasteLocalClipboardToAssets'); return; }
    if(target.closest?.('[data-local-clear-clipboard]')){ localClipboard = null; render(); return; }
    const localImportOne = target.closest?.('[data-local-import-one]');
    if(localImportOne){ gwCall('upload', 'setLocalClipboard', 'copy', [localImportOne.dataset.localImportOne || '']); await gwCall('upload', 'pasteLocalClipboardToAssets'); return; }
    const localOpen = target.closest?.('[data-local-open]');
    if(localOpen){ gwCall('upload', 'openLocalItem', localOpen.dataset.localOpen || ''); return; }
    const localFolder = target.closest?.('[data-local-folder]');
    if(localFolder){ activeLocalFolderId = localFolder.dataset.localFolder || ''; selectedLocalId = ''; selectedLocalIds.clear(); pendingBatchDelete = ''; render(); return; }
    const localCheck = target.closest?.('[data-local-check]');
    if(localCheck){
        event.preventDefault();
        event.stopPropagation();
        if(localManageMode){
            const id = localCheck.dataset.localCheck || '';
            const selected = toggleSelectionSet(selectedLocalIds, id);
            selectedLocalId = selected ? id : (selectedLocalId === id ? '' : selectedLocalId);
            pendingBatchDelete = '';
            gwCall('upload', 'syncLocalSelectionDom');
        }
        return;
    }
    const localCard = target.closest?.('[data-local-card]');
    if(localCard){
        const id = localCard.dataset.localCard || '';
        if(localManageMode){
            const selected = toggleSelectionSet(selectedLocalIds, id);
            selectedLocalId = selected ? id : (selectedLocalId === id ? '' : selectedLocalId);
        } else {
            selectedLocalId = id;
        }
        pendingBatchDelete = '';
        gwCall('upload', 'syncLocalSelectionDom');
        return;
    }
    if(target.closest?.('[data-workflow-manage]')){
        workflowManageMode = !workflowManageMode;
        if(!workflowManageMode) selectedWorkflowIds.clear();
        pendingDeleteAssetId = '';
        render();
        return;
    }
    if(target.closest?.('[data-workflow-select-all]')){ gwCall('organize', 'currentWorkflowItems').forEach(item => selectedWorkflowIds.add(workflowAssetId(item))); render(); return; }
    if(target.closest?.('[data-workflow-clear-selection]')){ selectedWorkflowIds.clear(); render(); return; }
    if(target.closest?.('[data-workflow-export-selected]')){ await gwCall('organize', 'exportWorkflowItems', [...selectedWorkflowIds]); return; }
    if(target.closest?.('[data-workflow-delete-selected]')){ await gwCall('organize', 'deleteSelectedWorkflows'); return; }
    if(target.closest?.('[data-workflow-upload]')){
        if(uploadInput) uploadInput.accept = '.json,.zip,application/json,application/zip,application/x-zip-compressed';
        uploadInput?.click();
        return;
    }
    const workflowDownload = target.closest?.('[data-workflow-download]');
    if(workflowDownload){ await gwCall('organize', 'exportWorkflowItems', [workflowDownload.dataset.workflowDownload || '']); return; }
    const workflowRename = target.closest?.('[data-workflow-rename]');
    if(workflowRename){ await gwCall('organize', 'renameWorkflowItem', workflowRename.dataset.workflowRename || ''); return; }
    const workflowDelete = target.closest?.('[data-workflow-delete]');
    if(workflowDelete){ await gwCall('organize', 'deleteWorkflowItem', workflowDelete.dataset.workflowDelete || ''); return; }
    if(target.closest?.('[data-workflow-cat-new]')){
        workflowTreeEdit = {kind:'category-new', placement:'head', value:'新工作流分组', label:'工作流分组名称'};
        pendingTreeDelete = '';
        render();
        focusTreeEditInput('workflowTreeEditInput');
        return;
    }
    if(target.closest?.('[data-workflow-cat-rename]')){
        const cat = activeWorkflowCategory();
        if(!cat) return;
        workflowTreeEdit = {kind:'category-rename', value:cat.name || '', label:'工作流分组名称', categoryId:cat.id};
        pendingTreeDelete = '';
        render();
        focusTreeEditInput('workflowTreeEditInput');
        return;
    }
    if(target.closest?.('[data-workflow-cat-delete]')){ await gwCall('organize', 'deleteWorkflowCategory'); return; }
    const workflowLib = target.closest?.('[data-workflow-lib]');
    if(workflowLib){ activeWorkflowLibraryId = workflowLib.dataset.workflowLib || ''; activeWorkflowCategoryId = ''; selectedWorkflowId = ''; selectedWorkflowIds.clear(); render(); return; }
    const workflowCat = target.closest?.('[data-workflow-cat]');
    if(workflowCat){ activeWorkflowLibraryId = workflowCat.dataset.workflowCatLib || activeWorkflowLibraryId; activeWorkflowCategoryId = workflowCat.dataset.workflowCat || ''; selectedWorkflowId = ''; selectedWorkflowIds.clear(); render(); return; }
    const workflowCard = target.closest?.('[data-workflow-card]');
    if(workflowCard){
        const id = workflowCard.dataset.workflowCard || '';
        if(workflowManageMode){
            const selected = toggleSelectionSet(selectedWorkflowIds, id);
            selectedWorkflowId = selected ? id : (selectedWorkflowId === id ? '' : selectedWorkflowId);
        } else {
            selectedWorkflowId = id;
        }
        pendingDeleteAssetId = '';
        gwCall('organize', 'syncWorkflowSelectionDom');
        return;
    }
    if(target.closest?.('[data-asset-tree-edit-save]')){ await saveAssetTreeEdit(); return; }
    if(target.closest?.('[data-asset-tree-edit-cancel]')){ assetTreeEdit = null; render(); return; }
    if(target.closest?.('[data-workflow-tree-edit-save]')){ await saveWorkflowTreeEdit(); return; }
    if(target.closest?.('[data-workflow-tree-edit-cancel]')){ workflowTreeEdit = null; render(); return; }
    if(target.closest?.('[data-prompt-tree-edit-save]')){ await savePromptTreeEdit(); return; }
    if(target.closest?.('[data-prompt-tree-edit-cancel]')){ promptTreeEdit = null; render(); return; }
    const assetEditSave = target.closest?.('[data-asset-edit-save]');
    if(assetEditSave){ await gwCall('settings', 'saveAssetEdit', assetEditSave.dataset.assetEditSave || ''); return; }
    if(target.closest?.('[data-asset-edit-cancel]')){ assetEditMode = false; render(); return; }
    const assetEditStart = target.closest?.('[data-asset-edit-start]');
    if(assetEditStart){ selectedAssetId = assetEditStart.dataset.assetEditStart || selectedAssetId; assetEditMode = true; pendingDeleteAssetId = ''; render(); return; }
    if(target.closest?.('[data-asset-manage]')){
        assetManageMode = !assetManageMode;
        pendingBatchDelete = '';
        if(!assetManageMode) selectedAssetIds.clear();
        render();
        return;
    }
    if(target.closest?.('[data-asset-select-all]')){ gwCall('organize', 'currentAssetItems').forEach(item => selectedAssetIds.add(item.id)); pendingBatchDelete = ''; render(); return; }
    if(target.closest?.('[data-asset-clear-selection]')){ selectedAssetIds.clear(); pendingBatchDelete = ''; render(); return; }
    if(target.closest?.('[data-asset-cut-selected]')){ gwCall('organize', 'setAssetClipboard', 'cut'); return; }
    if(target.closest?.('[data-asset-copy-selected]')){ gwCall('organize', 'setAssetClipboard', 'copy'); return; }
    if(target.closest?.('[data-asset-paste-clipboard]')){ await gwCall('organize', 'pasteAssetClipboard'); return; }
    if(target.closest?.('[data-asset-clear-clipboard]')){ assetClipboard = null; render(); return; }
    const assetRename = target.closest?.('[data-asset-rename]');
    if(assetRename){ await gwCall('organize', 'renameAssetItem', assetRename.dataset.assetRename || ''); return; }
    const assetDelete = target.closest?.('[data-asset-delete]');
    if(assetDelete){ await gwCall('organize', 'deleteAssetItem', assetDelete.dataset.assetDelete || ''); return; }
    const assetDownload = target.closest?.('[data-asset-download]');
    if(assetDownload){ gwCall('organize', 'downloadAssetItem', assetDownload.dataset.assetDownload || ''); return; }
    const assetOpen = target.closest?.('[data-asset-open]');
    if(assetOpen){ gwCall('preview', 'openAssetItem', assetOpen.dataset.assetOpen || ''); return; }
    const avatarCopy = target.closest?.('[data-avatar-copy]');
    if(avatarCopy){
        const uri = avatarCopy.dataset.avatarCopy || '';
        const ok = await copyTextToClipboard(uri);
        setStatus(ok ? '已复制 asset:// 地址' : `复制失败，请手动复制：${uri}`);
        return;
    }
    const avatarRegister = target.closest?.('[data-avatar-register]');
    if(avatarRegister){ await gwCall('settings', 'registerAssetAvatar', avatarRegister.dataset.avatarRegister || '', avatarRegister.dataset.avatarProv || ''); return; }
    const avatarCheck = target.closest?.('[data-avatar-check]');
    if(avatarCheck){ await gwCall('settings', 'checkAssetAvatarStatus', avatarCheck.dataset.avatarCheck || '', false, avatarCheck.dataset.avatarProv || ''); return; }
    if(target.closest?.('[data-asset-classify-selected]')){ await gwCall('settings', 'runAssetClassifySelected'); return; }
    if(target.closest?.('[data-asset-download-selected]')){ await gwCall('organize', 'downloadSelectedAssets'); return; }
    if(target.closest?.('[data-asset-copy-to-canvas]')){ gwCall('canvas', 'copySelectedAssetsToCanvas'); return; }
    if(target.closest?.('[data-asset-delete-selected]')){ await gwCall('organize', 'deleteSelectedAssets'); return; }
    if(target.closest?.('[data-asset-upload]')){
        if(isTauriClientMode()){
            void gwCall('upload', 'uploadViaClientPicker');
            return;
        }
        if(uploadInput) uploadInput.accept = 'image/*,video/*,audio/*';
        uploadInput?.click();
        return;
    }
    if(target.closest?.('[data-asset-lib-new]')){ assetTreeFocus = 'library'; assetTreeEdit = {kind:'library-new', placement:'head', value:'新资产库', label:'资产库名称'}; render(); focusTreeEditInput('assetTreeEditInput'); return; }
    if(target.closest?.('[data-asset-lib-rename]')){
        const row = target.closest('[data-asset-lib]');
        if(row) activeAssetLibraryId = row.dataset.assetLib || activeAssetLibraryId;
        assetTreeFocus = 'library';
        assetTreeEdit = {kind:'library-rename', value:activeAssetLibrary()?.name || '', label:'资产库名称'};
        pendingTreeDelete = '';
        render(); return;
    }
    if(target.closest?.('[data-asset-lib-delete]')){
        const row = target.closest('[data-asset-lib]');
        if(row) activeAssetLibraryId = row.dataset.assetLib || activeAssetLibraryId;
        await gwCall('organize', 'deleteAssetLibrary'); return;
    }
    if(target.closest?.('[data-asset-cat-new]')){
        const row = target.closest('[data-asset-lib]');
        const catRow = target.closest('[data-asset-cat]');
        if(row) activeAssetLibraryId = row.dataset.assetLib || activeAssetLibraryId;
        if(catRow) activeAssetLibraryId = catRow.dataset.assetCatLib || activeAssetLibraryId;
        assetTreeEdit = {kind:'category-new', value:'新分组', label:'分组名称'};
        pendingTreeDelete = '';
        render(); return;
    }
    if(target.closest?.('[data-asset-cat-rename]')){
        const row = target.closest('[data-asset-cat]');
        if(row){ activeAssetLibraryId = row.dataset.assetCatLib || activeAssetLibraryId; activeAssetCategoryId = row.dataset.assetCat || activeAssetCategoryId; }
        assetTreeFocus = 'category';
        assetTreeEdit = {kind:'category-rename', value:activeAssetCategory()?.name || '', label:'分组名称'};
        pendingTreeDelete = '';
        render(); return;
    }
    if(target.closest?.('[data-asset-cat-delete]')){
        const row = target.closest('[data-asset-cat]');
        if(row){ activeAssetLibraryId = row.dataset.assetCatLib || activeAssetLibraryId; activeAssetCategoryId = row.dataset.assetCat || activeAssetCategoryId; }
        await gwCall('organize', 'deleteAssetCategory'); return;
    }
    const assetLib = target.closest?.('[data-asset-lib]');
    if(assetLib){ activeAssetLibraryId = assetLib.dataset.assetLib || ''; assetTreeFocus = 'library'; activeAssetClassFilter = ''; activeAssetCategoryId = assetCategories()[0]?.id || ''; selectedAssetId = ''; selectedAssetIds.clear(); render(); return; }
    const assetClassRoot = target.closest?.('[data-asset-class-root]');
    if(assetClassRoot){
        activeAssetLibraryId = assetClassRoot.dataset.assetClassRoot || activeAssetLibraryId;
        assetTreeFocus = 'class';
        if(!assetClassificationEntriesForLibrary(activeAssetLibrary()).length) setStatus('暂无智能分类，请先选择图片并点击“智能分类”');
        return;
    }
    const assetClassGroup = target.closest?.('[data-asset-class-group]');
    if(assetClassGroup){
        openAssetClassGroup = openAssetClassGroup === (assetClassGroup.dataset.assetClassGroup || '') ? '' : (assetClassGroup.dataset.assetClassGroup || '');
        render();
        return;
    }
    const assetClass = target.closest?.('[data-asset-class-filter]');
    if(assetClass){
        activeAssetLibraryId = assetClass.dataset.assetClassLib || activeAssetLibraryId;
        const nextFilter = assetClass.dataset.assetClassFilter || '';
        activeAssetClassFilter = activeAssetClassFilter === nextFilter ? '' : nextFilter;
        if(activeAssetClassFilter) {
            openAssetClassGroup = assetClassificationGroupIdForFilter(activeAssetClassFilter, assetClassificationEntriesForLibrary(activeAssetLibrary())) || openAssetClassGroup;
        }
        assetTreeFocus = 'class';
        selectedAssetId = '';
        selectedAssetIds.clear();
        pendingBatchDelete = '';
        render();
        return;
    }
    const assetCat = target.closest?.('[data-asset-cat]');
    if(assetCat){ activeAssetLibraryId = assetCat.dataset.assetCatLib || activeAssetLibraryId; activeAssetCategoryId = assetCat.dataset.assetCat || ''; activeAssetClassFilter = ''; assetTreeFocus = 'category'; selectedAssetId = ''; selectedAssetIds.clear(); render(); return; }
    const assetCheck = target.closest?.('[data-asset-check]');
    if(assetCheck){
        event.preventDefault();
        event.stopPropagation();
        if(assetManageMode){
            const id = assetCheck.dataset.assetCheck || '';
            const selected = toggleSelectionSet(selectedAssetIds, id);
            selectedAssetId = selected ? id : (selectedAssetId === id ? '' : selectedAssetId);
            pendingBatchDelete = '';
            gwCall('organize', 'syncAssetSelectionDom');
        }
        return;
    }
    const assetCard = target.closest?.('[data-asset-card]');
    if(assetCard){
        const id = assetCard.dataset.assetCard || '';
        if(assetManageMode){
            const selected = toggleSelectionSet(selectedAssetIds, id);
            selectedAssetId = selected ? id : (selectedAssetId === id ? '' : selectedAssetId);
        } else {
            selectedAssetId = id;
        }
        assetEditMode = false;
        pendingDeleteAssetId = '';
        pendingBatchDelete = '';
        gwCall('organize', 'syncAssetSelectionDom');
        return;
    }

    const promptEditSave = target.closest?.('[data-prompt-edit-save]');
    if(promptEditSave){ await gwCall('organize', 'savePromptEdit', promptEditSave.dataset.promptEditSave || ''); return; }
    if(target.closest?.('[data-prompt-create-save]')){ await gwCall('organize', 'savePromptCreate'); return; }
    if(target.closest?.('[data-prompt-edit-cancel]')){ promptEditMode = false; promptCreateMode = false; render(); return; }
    const promptEditStart = target.closest?.('[data-prompt-edit-start]');
    if(promptEditStart){ selectedPromptId = promptEditStart.dataset.promptEditStart || selectedPromptId; promptEditMode = true; promptCreateMode = false; pendingDeletePromptId = ''; render(); return; }
    if(target.closest?.('[data-prompt-manage]')){
        promptManageMode = !promptManageMode;
        pendingBatchDelete = '';
        if(!promptManageMode) selectedPromptIds.clear();
        render();
        return;
    }
    if(target.closest?.('[data-prompt-select-all]')){ gwCall('organize', 'currentPromptItems').forEach(item => selectedPromptIds.add(item.id)); pendingBatchDelete = ''; render(); return; }
    if(target.closest?.('[data-prompt-clear-selection]')){ selectedPromptIds.clear(); pendingBatchDelete = ''; render(); return; }
    const promptEdit = target.closest?.('[data-prompt-edit]');
    if(promptEdit){ await gwCall('organize', 'editPromptItem', promptEdit.dataset.promptEdit || ''); return; }
    const promptDelete = target.closest?.('[data-prompt-delete]');
    if(promptDelete){ await gwCall('organize', 'deletePromptItem', promptDelete.dataset.promptDelete || ''); return; }
    if(target.closest?.('[data-prompt-delete-selected]')){ await gwCall('organize', 'deleteSelectedPrompts'); return; }
    const promptNewBtn = target.closest?.('[data-prompt-new]');
    if(promptNewBtn){
        const libId = promptNewBtn.dataset.libId || target.closest('[data-prompt-lib]')?.dataset.promptLib;
        const catRow = target.closest('[data-prompt-cat]');
        if(libId){ activePromptLibraryId = libId; activePromptCategory = 'all'; }
        if(catRow){ activePromptLibraryId = catRow.dataset.promptCatLib || activePromptLibraryId; activePromptCategory = catRow.dataset.promptCat || activePromptCategory; }
        promptCreateMode = true; promptEditMode = false; pendingDeletePromptId = ''; render(); return;
    }
    if(target.closest?.('[data-prompt-lib-new]')){ promptTreeFocus = 'library'; promptTreeEdit = {kind:'library-new', placement:'head', value:'新提示词库', label:'提示词库名称'}; render(); focusTreeEditInput('promptTreeEditInput'); return; }
    if(target.closest?.('[data-prompt-cat-new]')){
        const libRow = target.closest('[data-prompt-lib]');
        if(libRow) activePromptLibraryId = libRow.dataset.promptLib || activePromptLibraryId;
        promptTreeFocus = 'library';
        promptTreeEdit = {kind:'category-new', value:'新分组', label:'分组名称'};
        pendingTreeDelete = '';
        render(); return;
    }
    if(target.closest?.('[data-prompt-cat-rename]')){
        promptTreeFocus = 'category';
        const cat = activePromptCategories().find(c => c.id === activePromptCategory);
        promptTreeEdit = {kind:'category-rename', value:cat?.name || '', label:'分组名称'};
        pendingTreeDelete = '';
        render(); return;
    }
    if(target.closest?.('[data-prompt-cat-delete]')){ await gwCall('organize', 'deletePromptCategory'); return; }
    const promptLibRenameBtn = target.closest?.('[data-prompt-lib-rename]');
    if(promptLibRenameBtn){
        const libRow = target.closest('[data-prompt-lib]');
        if(promptLibRenameBtn.dataset.libId) activePromptLibraryId = promptLibRenameBtn.dataset.libId;
        if(libRow) activePromptLibraryId = libRow.dataset.promptLib || activePromptLibraryId;
        promptTreeFocus = 'library';
        promptTreeEdit = {kind:'library-rename', value:activePromptLibrary()?.name || '', label:'提示词库名称'};
        render(); return;
    }
    const promptLibDeleteBtn = target.closest?.('[data-prompt-lib-delete]');
    if(promptLibDeleteBtn){
        if(promptLibDeleteBtn.dataset.libId) activePromptLibraryId = promptLibDeleteBtn.dataset.libId;
        await gwCall('organize', 'deletePromptLibrary'); return;
    }
    const promptLib = target.closest?.('[data-prompt-lib]');
    if(promptLib){ activePromptLibraryId = promptLib.dataset.promptLib || ''; activePromptCategory = 'all'; promptTreeFocus = 'library'; selectedPromptId = ''; promptCreateMode = false; promptEditMode = false; selectedPromptIds.clear(); render(); return; }
    const promptCat = target.closest?.('[data-prompt-cat]');
    if(promptCat){ activePromptLibraryId = promptCat.dataset.promptCatLib || activePromptLibraryId; activePromptCategory = promptCat.dataset.promptCat || 'all'; promptTreeFocus = 'category'; selectedPromptId = ''; promptCreateMode = false; promptEditMode = false; selectedPromptIds.clear(); render(); return; }
    const promptRow = target.closest?.('[data-prompt-row]');
    if(promptRow){
        const id = promptRow.dataset.promptRow || '';
        if(promptManageMode){
            const selected = toggleSelectionSet(selectedPromptIds, id);
            selectedPromptId = selected ? id : (selectedPromptId === id ? '' : selectedPromptId);
        } else {
            selectedPromptId = id;
        }
        promptEditMode = false;
        promptCreateMode = false;
        pendingDeletePromptId = '';
        pendingBatchDelete = '';
        render();
        return;
    }
}
function detailViewerItem(source, id){
    if(source === 'registry') return registryDetail?.id === id ? registryDetail : registryAssets.find(item => item.id === id);
    if(source === 'asset') return findAssetItem(id);
    if(source === 'workflow') return findWorkflowItem(id);
    if(source === 'prompt') return findPromptItem(id);
    if(source === 'localup') return findLocalUpload(id);
    if(source === 'local') return findLocalItem(id);
    if(source === 'canvas-asset') return findCanvasAssetItem(id);
    return null;
}
function detailViewerUrl(source, item={}){
    if(source === 'local') return localObjectUrl(item);
    const explicit = item.url || item.public_url || '';
    if(explicit) return explicit;
    // MVP1-T6：仅导入登记、尚未回填 url 的素材仍可通过素材端点读取已登记媒体，
    // 否则镜头命中后详情拿不到媒体地址，跳帧与预览都会退化成空播放器。
    if(source !== 'registry') return '';
    const assetId = detailMediaCanvasPersistId(item, source);
    return assetId ? `/api/asset-registry/assets/${encodeURIComponent(assetId)}/media` : '';
}
function detailViewerType(source, item={}){
    if(workflowSourceId(item)) return 'workflow';
    const extension = assetFileExtension(item);
    const kind = source === 'local' ? String(item.kind || localItemKind(item)).toLowerCase() : assetKind(item);
    if(source === 'prompt') return 'text';
    if(source === 'workflow') return extension === 'json' ? 'text' : 'other';
    if(ASSET_LUT_PREVIEW_EXTENSIONS.includes(extension) || kind === 'lut') return 'lut';
    if(ASSET_3D_PREVIEW_EXTENSIONS.includes(extension)) return 'model';
    if(ASSET_FONT_PREVIEW_EXTENSIONS.includes(extension)) return 'font';
    if(ASSET_AUDIO_PREVIEW_EXTENSIONS.includes(extension) || kind === 'audio') return 'audio';
    if(ASSET_VIDEO_EXTENSIONS.has(extension) || kind === 'video') return 'video';
    if(ASSET_IMAGE_PREVIEW_EXTENSIONS.has(extension)) return 'image';
    if(ASSET_DOCUMENT_PREVIEW_EXTENSIONS.includes(extension) || kind === 'text') return 'text';
    if(assetSupportsImagePreview(item) || kind === 'image') return 'image';
    return 'other';
}
function detailViewerSourceLabel(source, item={}){
    if(source === 'registry') return item.storage_root_name || '统一资产';
    if(source === 'asset') return `${activeAssetLibrary()?.name || '托管资产'} / ${activeAssetCategory()?.name || '分组'}`;
    if(source === 'workflow') return `${activeWorkflowLibrary()?.name || '工作流'} / ${activeWorkflowCategory()?.name || '分组'}`;
    if(source === 'prompt') return activePromptLibrary()?.name || '提示词库';
    if(source === 'localup') return activeLocalLibraryName();
    if(source === 'local') return activeSharedFolderName || '素材来源';
    if(source === 'canvas-asset') return item.canvas_title || canvasAssetViewTitle();
    return '资产中心';
}
function detailViewerFileParams(source, item={}){
    const params = new URLSearchParams();
    const assetId = detailMediaCanvasPersistId(item, source);
    if(assetId && !item.is_remote) params.set('asset_id', assetId);
    else {
        const url = detailViewerUrl(source, item);
        if(url) params.set('url', url);
    }
    return params;
}
function detailViewerCanEditText(source, item={}){
    return source !== 'prompt' && !item.is_remote && ASSET_EDITABLE_TEXT_EXTENSIONS.has(assetFileExtension(item)) && detailViewerFileParams(source, item).toString().length > 0;
}
function formatMediaDuration(value){
    const seconds = Math.max(0, Number(value || 0));
    if(!Number.isFinite(seconds) || !seconds) return '时长读取中';
    const whole = Math.floor(seconds);
    const hours = Math.floor(whole / 3600);
    const minutes = Math.floor((whole % 3600) / 60);
    const remain = whole % 60;
    return hours ? `${hours}:${String(minutes).padStart(2,'0')}:${String(remain).padStart(2,'0')}` : `${minutes}:${String(remain).padStart(2,'0')}`;
}
function formatVideoSegmentTime(seconds){
    const sec = Math.max(0, Number(seconds || 0));
    if(!Number.isFinite(sec)) return '00:00';
    const whole = Math.floor(sec);
    const minutes = Math.floor(whole / 60);
    const remain = whole % 60;
    return `${String(minutes).padStart(2,'0')}:${String(remain).padStart(2,'0')}`;
}
function formatVideoSegmentRange(startSec, endSec){
    return `${formatVideoSegmentTime(startSec)}~${formatVideoSegmentTime(endSec)}`;
}
function alignTimeToFps(seconds, fps = 0){
    const sec = Math.max(0, Number(seconds || 0));
    if(!Number.isFinite(sec)) return 0;
    let rate = Number(fps || 0);
    if(rate <= 0) return sec;
    if(Math.abs(rate - 29.97) < 0.01) rate = 30000 / 1001;
    else if(Math.abs(rate - 23.98) < 0.01 || Math.abs(rate - 23.976) < 0.01) rate = 24000 / 1001;
    else if(Math.abs(rate - 59.94) < 0.01) rate = 60000 / 1001;
    const frameIndex = Math.round(sec * rate);
    return frameIndex / rate;
}
function findMatchedVideoSegment(item, query=''){
    const segments = item?.metadata?.video_segments || item?.video_segments;
    if(!Array.isArray(segments) || !segments.length) return null;
    const q = String(query || '').trim().toLowerCase();
    if(!q) return null;
    const matched = segments.find(s => {
        const tags = Array.isArray(s.tags) ? s.tags : [];
        return tags.some(t => String(t).toLowerCase().includes(q))
            || (s.summary && String(s.summary).toLowerCase().includes(q));
    });
    return matched || null;
}
// 【QIN-554】LUT 详情工作区：左侧「同库 LUT」列表对标字体库侧栏，右侧样片舞台。
// 强度与对比模式只改 canvas 像素（不重渲染整个查看器），因此滑杆拖动无闪烁。
const DETAIL_LUT_SAMPLE_WIDTH = 640;
const DETAIL_LUT_SAMPLE_HEIGHT = 360;
/* ------------------------------------------------------------------ */
/* QIN-580：参考画布素材套用 LUT 预览                                    */
/* 只读：取像素 → 查表 → 画到覆盖 canvas，不写盘、不新增资产。            */
/* 入口是画布工具条的「套用 LUT」，节点右上角角标打开强度/对比浮窗。        */
/* ------------------------------------------------------------------ */

/** 视频逐帧取像素比图片贵，降采样更狠；图片可以放宽到 1280。 */
const DETAIL_MEDIA_LUT_VIDEO_WIDTH = 720;

const detailMediaLutListCache = {items:null, inflight:null};
/** lutId → {lut, title}：每个节点各用各的 LUT，不能只缓存一个。 */
const detailMediaLutCache = new Map();
/** 正在合成 / 待补绘的节点，避免同一节点并发合成。 */
const detailMediaLutDraw = {busy:new Set(), pending:new Set()};
/** 图片元素 → 已取样帧：拖强度、切对比模式复用像素，切 LUT 不必重取。 */
const detailMediaLutSamples = new WeakMap();
let detailMediaLutSplitDragEndedAt = 0;
function detailMediaCanvasItems(){
    return (detailViewerState.mediaCanvasItems || []).filter(value => value?.id && ['image','video'].includes(detailViewerType(detailViewerState.source, value)));
}
function detailMediaCanvasPersistId(item={}, source=detailViewerState.source){
    const direct = String(item.asset_id || item.assetId || (source === 'registry' ? item.id : '') || '').trim();
    if(direct) return direct;
    const url = String(item.assetUrl || item.public_url || item.url || '');
    try {
        const queryAssetId = new URL(url,window.location.origin).searchParams.get('asset_id');
        if(queryAssetId) return String(queryAssetId).trim();
    } catch(_) {}
    const match = url.match(/\/api\/asset-registry\/assets\/([^/?#]+)(?:\/|\?|#|$)/i);
    if(!match?.[1]) return '';
    try { return decodeURIComponent(match[1]); } catch(_) { return match[1]; }
}
function detailVideoStoryboardEnabled(){
    return Boolean((registryStatus?.overview?.features || []).find(feature => feature.id === 'video_storyboard')?.enabled);
}
function detailVideoStoryboardAssetId(item={},source=detailViewerState.source){
    return detailMediaCanvasPersistId(item,source);
}
function detailVideoStoryboardUrl(item={},source=detailViewerState.source){
    const assetId=detailVideoStoryboardAssetId(item,source);
    if(!assetId) return '';
    // 【修复问题1】九宫格预览同样使用按资产的 updated_at/indexed_at 版本，不再依赖全局 cache-bust
    const version=Number(item.updated_at || item.indexed_at || 0);
    return `/api/asset-registry/assets/${encodeURIComponent(assetId)}/video/storyboard?v=${encodeURIComponent(version)}`;
}
function renderDetailVideoStoryboardPanel(item={},source=detailViewerState.source){
    if(detailViewerType(source,item)!=='video' || !detailVideoStoryboardEnabled()) return '';
    const assetId=detailVideoStoryboardAssetId(item,source);
    const url=detailVideoStoryboardUrl(item,source);
    if(!assetId || !url) return '';
    const version=String(Number(item.updated_at || item.indexed_at || 0) || '');
    return `<section class="asset-video-storyboard-panel" aria-label="视频九宫格缩略图">
        <button type="button" data-detail-video-storyboard-open="${escapeAttr(assetId)}" data-storyboard-url="${escapeAttr(url)}" data-storyboard-version="${escapeAttr(version)}" title="放大查看九宫格">
            <div class="asset-video-storyboard-slot" data-storyboard-image="${escapeAttr(assetId)}" data-storyboard-version="${escapeAttr(version)}"><span class="asset-video-storyboard-placeholder"><i data-lucide="grid-3x3"></i></span></div>
        </button>
    </section>`;
}
// 九宫格是后端合成的 3×3 单图（按素材原画幅自然拼接），前端只渲染一张。
// 图幅用内联 contain 约束：无论缓存里是旧口径的竖版还是新横版，都完整显示不裁切。
const VIDEO_STORYBOARD_IMAGE_STYLE = 'position:absolute;inset:0;width:100%;height:100%;object-fit:contain;display:block;';
// 放大层：外框按图片实际尺寸自适应，四周留 10px 内边距；加载完成前先用视口兜底。
const VIDEO_STORYBOARD_LARGE_STYLE = 'max-width:100%;max-height:calc(100vh - 132px);width:auto;height:auto;object-fit:contain;display:block;';
function renderDetailVideoSegments(item={}, source=detailViewerState.source){
    if(detailViewerType(source, item) !== 'video') return '';
    const segments = item.metadata?.video_segments || item.video_segments;
    if(!Array.isArray(segments) || !segments.length) return '';
    const fps = Number(item.frame_rate || item.metadata?.frame_rate || 0);
    return `<section class="asset-video-segments-panel" aria-label="视频内容场景切片">
        <div class="asset-video-segments-head">
            <span class="segments-title"><i data-lucide="clapperboard"></i><strong>场景切片 (${segments.length})</strong></span>
            <small>${fps ? `${Number.isInteger(fps) ? fps : fps.toFixed(2)} fps` : '点击直接跳帧播放'}</small>
        </div>
        <div class="asset-video-segments-grid">
            ${segments.map((seg) => {
                const range = formatVideoSegmentRange(seg.start_sec, seg.end_sec);
                const tags = Array.isArray(seg.tags) ? seg.tags.join(' · ') : '';
                return `<button type="button" class="asset-video-segment-chip" data-video-jump-sec="${seg.start_sec}" data-video-jump-end="${seg.end_sec}" data-video-asset-id="${escapeAttr(item.id)}" data-video-frame-rate="${fps || ''}" title="点击跳至 ${range} 起播">
                    <i data-lucide="play-circle"></i>
                    <strong>${range}</strong>
                    ${tags ? `<span class="chip-tags">${escapeHtml(tags)}</span>` : ''}
                </button>`;
            }).join('')}
        </div>
    </section>`;
}
function renderDetailVideoSummary(item={},source=detailViewerState.source){
    if(detailViewerType(source,item)!=='video') return '';
    const assetId=detailVideoStoryboardAssetId(item,source);
    const width=Number(item.width || item.metadata?.width || 0);
    const height=Number(item.height || item.metadata?.height || 0);
    const frameRate=Number(item.frame_rate || item.metadata?.frame_rate || 0);
    const versions=Array.isArray(item.versions) ? item.versions : [];
    const linkedBoardCount=registryAssetLinkedBoards(item).length;
    // 【QIN-539 口径 + 用户指示 2026-11-05】视频详情摘要时间行与详情一致：优先文件修改时间，
    // 回退记录更新时间；确无数据时显示「—」，不再出现「未知」。
    const updated=registryModifiedTime(item) || item.updated_at || item.canvas_updated_at || 0;
    const values=[
        ['稳定 ID',assetId ? assetId.slice(0,8) : '未关联',assetId],
        ['格式',assetFileExtension(item).toUpperCase() || 'VIDEO'],
        ['大小',formatFileSize(item.size || 0)],
        ['关联库',String(linkedBoardCount)],
        ['版本',String(Number(item.version_count || versions.length || 0))],
        ['分辨率',width && height ? `${width} × ${height}` : '读取中'],
        ['帧率',frameRate ? `${Number.isInteger(frameRate) ? frameRate : frameRate.toFixed(2)} fps` : '读取中'],
        ['素材来源',detailViewerSourceLabel(source,item)],
        ['修改时间',updated ? formatDate(updated) : '—'],
    ];
    return `<dl class="asset-video-summary">${values.map(([label,value,title=''])=>`<div><dt>${escapeHtml(label)}</dt><dd title="${escapeAttr(title || value)}">${escapeHtml(value)}</dd></div>`).join('')}</dl>`;
}
function detailMediaCanvasSelectedIds(){
    if(!(detailViewerState.mediaCanvasSelectedIds instanceof Set)){
        detailViewerState.mediaCanvasSelectedIds = new Set(detailViewerState.mediaCanvasSelectedIds || []);
    }
    return detailViewerState.mediaCanvasSelectedIds;
}
const DETAIL_MEDIA_CANVAS_GROUP_COLORS = ['#173a5e','#4b2142','#214f3a','#4a3420','#3b285f','#29464c'];
function detailMediaCanvasGroups(){
    if(!Array.isArray(detailViewerState.mediaCanvasGroups)) detailViewerState.mediaCanvasGroups = [];
    return detailViewerState.mediaCanvasGroups;
}
function detailMediaCanvasGroup(id){
    return detailMediaCanvasGroups().find(group => group.id === id) || null;
}
function detailMediaCanvasGroupForMember(id){
    return detailMediaCanvasGroups().find(group => (group.memberIds || []).includes(id)) || null;
}
function detailMediaCanvasSelectionObjectId(id){
    return detailMediaCanvasGroup(id)?.id || detailMediaCanvasGroupForMember(id)?.id || id;
}
function detailMediaCanvasSelectionOrder(){
    const validIds = new Set(detailMediaCanvasObjectIds());
    const selected = detailMediaCanvasSelectedIds();
    const normalizedSelected = new Set();
    selected.forEach(id => {
        const objectId = detailMediaCanvasSelectionObjectId(id);
        if(validIds.has(objectId)) normalizedSelected.add(objectId);
    });
    const order = [];
    (detailViewerState.mediaCanvasSelectionOrder || []).forEach(id => {
        const objectId = detailMediaCanvasSelectionObjectId(id);
        if(normalizedSelected.has(objectId) && !order.includes(objectId)) order.push(objectId);
    });
    normalizedSelected.forEach(id => { if(!order.includes(id)) order.push(id); });
    detailViewerState.mediaCanvasSelectedIds = normalizedSelected;
    detailViewerState.mediaCanvasSelectionOrder = order;
    return order;
}
function detailMediaCanvasAnnotations(){
    return detailViewerState.mediaCanvasAnnotations || [];
}
function detailMediaCanvasDrawings(){
    if(!Array.isArray(detailViewerState.mediaCanvasDrawings)) detailViewerState.mediaCanvasDrawings = [];
    return detailViewerState.mediaCanvasDrawings;
}
function detailMediaCanvasRawObjectIds(){
    return [
        ...detailMediaCanvasItems().map(item => item.id),
        ...detailMediaCanvasAnnotations().map(note => note.id),
        ...detailMediaCanvasDrawings().map(drawing => drawing.id),
    ];
}
function detailMediaCanvasObjectIds(){
    const groupedIds = new Set(detailMediaCanvasGroups().flatMap(group => group.memberIds || []));
    const directlySelectableIds = [
        ...detailMediaCanvasItems().map(item => item.id),
        ...detailMediaCanvasAnnotations().map(note => note.id),
    ];
    return [...detailMediaCanvasGroups().map(group => group.id), ...directlySelectableIds.filter(id => !groupedIds.has(id))];
}
function detailActiveImageItem(){
    const activeId=detailViewerState.mediaCanvasActiveId;
    return detailMediaCanvasItems().find(item=>item.id===activeId) || detailViewerState.items?.[detailViewerState.index] || null;
}
function detailImageVersionAssetId(item=detailActiveImageItem()){
    return item && detailViewerType(detailViewerState.source,item)==='image' ? detailMediaCanvasPersistId(item) : '';
}
function detailImageVersionSelection(item,versions=[]){
    const explicit=String(item?.image_version_selection || item?.asset_version_id || item?.reference_edit?.asset_version_id || '');
    if(explicit && versions.some(version=>String(version.id)===explicit)) return explicit;
    return String(versions.find(version=>version.is_original || version.id==='original')?.id || 'original');
}
async function loadDetailImageVersions(item=detailActiveImageItem(),force=false){
    const assetId=detailImageVersionAssetId(item);
    if(!assetId){
        detailViewerState.imageVersions=[]; detailViewerState.imageVersionsAssetId=''; detailViewerState.imageVersionsLoading=false; detailViewerState.imageVersionSelectedId='';
        if(detailViewerState.open) gwCall('preview', 'refreshDetailImageVersionsPanelDom');
        return [];
    }
    if(!force && detailViewerState.imageVersionsAssetId===assetId && detailViewerState.imageVersions.length) return detailViewerState.imageVersions;
    detailViewerState.imageVersionsAssetId=assetId; detailViewerState.imageVersionsLoading=true;
    if(detailViewerState.open) gwCall('preview', 'refreshDetailImageVersionsPanelDom');
    try {
        const data=await apiJsonResponse(assetManagerApi.getRegistryImageVersions(assetId, 500));
        if(detailImageVersionAssetId()!==assetId) return [];
        const received=Array.isArray(data.versions) ? data.versions : [];
        const originalValue=received.find(version=>version.is_original || version.id==='original');
        const original=originalValue ? {...originalValue,id:'original',version_number:0,is_original:true,is_hidden:false} : {
            id:'original',asset_id:assetId,version_number:0,is_original:true,is_hidden:false,edit:null,created_at:Number(item?.created_at||0),
        };
        const versions=[original,...normalizedImageEditVersions(received)];
        detailViewerState.imageVersions=versions;
        const selectedId=detailImageVersionSelection(detailActiveImageItem(),versions);
        detailViewerState.imageVersionSelectedId=selectedId;
        gwCall('preview', 'selectDetailImageVersion', selectedId,{render:false,announce:false});
        return versions;
    } finally {
        if(detailViewerState.imageVersionsAssetId===assetId){ detailViewerState.imageVersionsLoading=false; if(detailViewerState.open) gwCall('preview', 'refreshDetailImageVersionsPanelDom'); }
    }
}
async function openRegistryImageVersion(assetId,versionId,edit=false){
    gwCall('preview', 'showDetailPreview', 'registry',assetId);
    const versions=await loadDetailImageVersions(detailActiveImageItem(),true);
    const target=versions.find(version=>String(version.id)===String(versionId));
    if(!target) throw new Error('图片版本不存在');
    gwCall('preview', 'selectDetailImageVersion', target.id);
    if(edit) await gwCall('preview', 'openDetailImageEditor', detailActiveImageItem()?.id || assetId);
}
function returnAssetManagerHome(){
    if(detailViewerState.open && !closeDetailPreview()) return;
    gwCall('organize', 'activateAssetBoard', 'registry').catch(err => setStatus(err.message || '返回资产首页失败'));
}
function openRegistryStructureReferenceCanvasDetail(detail={}){
    const structureId = String(detail.structureId || '').trim();
    const structure = registryAssetStructures.get(structureId);
    if(!structure) throw new Error('版本结构不存在或已失效');
    const membersById = new Map(registryGroupStructureMembers(structure).map(value => [String(value.asset.id || ''), value.asset]));
    const requestedIds = Array.isArray(detail.assetIds) && detail.assetIds.length
        ? detail.assetIds.map(value => String(value || '').trim()).filter(Boolean)
        : (structure.members || []).map(member => String(member.asset_id || '').trim()).filter(Boolean);
    const items = requestedIds.map(assetId => membersById.get(assetId)).filter(Boolean);
    if(items.length < 2) throw new Error('参考画布至少需要两张可访问图片');
    const currentAssetId = String(detail.currentAssetId || structure.current_asset_id || items[0].id || '').trim();
    const current = items.find(item => String(item.id || '') === currentAssetId) || items[0];
    selectedRegistryAssetId = current.id;
    registryDetail = current;
    gwCall('preview', 'showDetailPreview', 'registry', current.id, {
        items,
        mediaCanvasItems:items,
        referenceCanvasMode:true,
        referenceCanvasStructureId:structureId,
        referenceCanvasTitle:structure.structure_type === 'version' ? '版本参考画布' : '图片组参考画布',
        returnFocus:detail.trigger || document.activeElement,
    });
    requestAnimationFrame(() => document.querySelector('[data-detail-media-canvas-viewport]')?.focus({preventScroll:true}));
}
window.addEventListener('gw:asset-structure-reference-canvas-open', event => {
    try {
        openRegistryStructureReferenceCanvasDetail(event.detail || {});
    } catch(err) {
        setStatus(err.message || '无法打开结构参考画布');
    }
});
function closeDetailPreview(force=false){
    if(detailViewerState.textVersionEditing && !force){
        gwCall('preview', 'cancelDetailTextVersionEdit');
        setStatus('已取消版本编辑，再次关闭可退出详情');
        return false;
    }
    if(detailViewerState.textEditing && !force){
        gwCall('preview', 'cancelDetailTextEdit');
        setStatus('已取消正文编辑，再次关闭可退出详情');
        return false;
    }
    const returnFocus = detailViewerState.returnFocus;
    const fullscreen = document.fullscreenElement;
    if(fullscreen?.closest?.('.asset-detail-viewer')){
        const exitPromise = document.exitFullscreen?.();
        if(exitPromise && typeof exitPromise.catch === 'function') exitPromise.catch(err => console.warn('[AssetManager] 退出全屏失败:', err));
    }
    document.querySelector('.asset-detail-viewer')?.querySelectorAll('audio,video').forEach(media => {
        try {
            if (typeof media.pause === 'function') media.pause();
            media.src = '';
            media.removeAttribute('src');
            if (typeof media.load === 'function') media.load();
        } catch (err) {
            console.warn('[AssetManager] 释放媒体元素资源失败:', err);
        }
    });
    const viewer = document.querySelector('.asset-detail-viewer');
    if (viewer && window.MediaPreviewPlayer?.destroy) {
        try {
            window.MediaPreviewPlayer.destroy(viewer);
        } catch (err) {
            console.warn('[AssetManager] 销毁播放器组件失败:', err);
        }
    }
    viewer?.remove();
    // 【QIN-580】LUT 下拉菜单挂在 body 上，详情关了必须一起摘掉，否则会留一块孤儿浮层。
    document.querySelector('[data-detail-media-lut-layer]')?.remove();
    detailViewerMediaPreloads.clear();
    document.body.classList.remove('asset-lightbox-open');
    lightboxPanState = null;
    detailMediaCanvasPanState = null;
    detailMediaCanvasSpacePressed = false;
    detailMediaCanvasEditState = null;
    detailMediaCanvasAnnotationSnapshot = null;
    detailMediaCanvasColorSnapshot = null;
    cancelAnimationFrame(detailViewerAudioFrame);
    gwCall('preview', 'clearDetailViewerFontFace');
    detailViewerState = {
        open:false, source:'', id:'', index:0, items:[], text:'', textLoading:false, textError:'', textEditing:false,
        textTruncated:false, textVersions:[], textVersionsLoading:false, selectedTextVersion:null,
        textVersionEditing:false, textVersionError:'', modelInfo:null, modelInfoLoading:false, modelInfoError:'',
        wheelAt:0, audioZoom:1, fontScale:1, fontFit:1, fontLineWidth:0, fontExpanded:detailViewerFontExpandedPreference, fontListScrollTop:0,
        mediaCanvasItems:[], mediaCanvasActiveId:'', mediaCanvasSelectedIds:new Set(), mediaCanvasSelectionOrder:[], mediaCanvasLayout:{},
        mediaCanvasAnnotations:[], mediaCanvasDrawings:[], mediaCanvasGroups:[], mediaCanvasContextMenu:null,
        mediaCanvasEditingAnnotationId:'', mediaCanvasHistory:[], mediaCanvasSnap:true, mediaCanvasLibraryVisible:false,
        mediaCanvasNormalizeBasis:'first', mediaCanvasExportScope:'all', mediaCanvasLibrarySelectedId:'',
        mediaCanvasLibraryQuery:'', mediaCanvasLibrarySort:'newest', mediaCanvasLibraryScope:'all', mediaCanvasLibraryStorageRootId:'all', mediaCanvasLibraryScrollTop:0, mediaCanvasSidebarView:'detail',
        mediaCanvasScale:1, mediaCanvasX:0, mediaCanvasY:0, mediaCanvasFitted:false,
        videoRanges:{}, videoExportRootId:'', videoExportBusy:'', videoStoryboardExpandedId:'',
        imageVersions:[],imageVersionsAssetId:'',imageVersionsLoading:false,imageVersionSelectedId:'',imageVersionPanelCollapsed:true,
        // 与打开处保持同一份字段表；少写一项，下次打开就拿到 undefined。
        lutStrength:1, lutCompare:'applied', lutListScrollTop:0,
        mediaCanvasLut:{}, mediaCanvasLutPanel:'', mediaLutMenu:{open:false, step:'asset', assetId:''},
        referenceCanvasId:'', referenceCanvasTitle:'参考画布', referenceCanvasMode:false, referenceCanvasStructureId:'', referenceCanvasSaving:false, originTab:'registry'
    };
    returnFocus?.focus?.({preventScroll:true});
    return true;
}
function detailViewerVideo(videoId=''){
    const id = videoId || detailViewerState.mediaCanvasActiveId || '';
    const nodeVideo = id ? document.querySelector(`.asset-detail-viewer [data-detail-media-canvas-node="${CSS.escape(id)}"] video.asset-detail-player`) : null;
    return nodeVideo
        || document.querySelector('.asset-detail-viewer .asset-detail-media-node.is-active video.asset-detail-player')
        || document.querySelector('.asset-detail-viewer .asset-lightbox-video.asset-detail-player');
}
function seekDetailVideoTo(seconds, { play = false, videoId = '', fps = 0 } = {}){
    const rawSec = Math.max(0, Number(seconds || 0));
    if(!Number.isFinite(rawSec)) return false;
    const video = detailViewerVideo(videoId);
    if(!video) return false;
    if(video.error || video.networkState === 3) return false;
    const item = detailViewerVideoItem(videoId);
    const effectiveFps = fps || Number(video.getAttribute('data-frame-rate') || item?.frame_rate || item?.metadata?.frame_rate || 0);
    const sec = alignTimeToFps(rawSec, effectiveFps);

    const performSeek = () => {
        try {
            video.currentTime = sec;
            if(play && video.paused && typeof video.play === 'function'){
                const playPromise = video.play();
                if(playPromise && typeof playPromise.catch === 'function'){
                    playPromise.catch(err => {
                        console.warn('[AssetManager] 自动起播被浏览器策略拦截:', err);
                        setStatus(`已跳帧至 ${formatVideoSegmentTime(sec)}（自动起播受浏览器策略限制，请手动点击播放）`);
                    });
                }
            }
            gwCall('preview', 'syncDetailVideoControls', video);
            // 跳帧目标已在本次定位中消费，后续重渲染不得再把播放位置拉回旧时间点。
            detailViewerState.startTime = null;
            setStatus(`已跳帧至 ${formatVideoSegmentTime(sec)}`);
            return true;
        } catch (err) {
            console.error('[AssetManager] seekDetailVideoTo 失败:', err);
            return false;
        }
    };

    if(video.readyState >= 1){
        return performSeek();
    } else {
        // 不要只等一帧就直接设置 currentTime，等待 loadedmetadata / canplay 事件准备就绪
        const onReady = () => {
            video.removeEventListener('loadedmetadata', onReady);
            video.removeEventListener('canplay', onReady);
            performSeek();
        };
        video.addEventListener('loadedmetadata', onReady, { once: true });
        video.addEventListener('canplay', onReady, { once: true });
        return true;
    }
}
function detailViewerVideoItem(videoId=''){
    const id = videoId || detailViewerState.mediaCanvasActiveId || '';
    return detailMediaCanvasItems().find(item => item.id === id) || detailViewerState.items[detailViewerState.index] || {};
}
const ASSET_DETAIL_MEDIA_COVERFLOW_VISIBLE_KEY = 'gw:asset-detail-media-coverflow-visible';
let detailMediaCanvasCoverflowVisible = false;
try {
    detailMediaCanvasCoverflowVisible = localStorage.getItem(ASSET_DETAIL_MEDIA_COVERFLOW_VISIBLE_KEY) === 'true';
} catch(_) {}
function detailMediaCanvasExpandedObjectIds(ids=[]){
    const expanded = [];
    ids.forEach(id => {
        const group = detailMediaCanvasGroup(id);
        (group ? group.memberIds || [] : [id]).forEach(memberId => {
            if(detailMediaCanvasRawObjectIds().includes(memberId) && !expanded.includes(memberId)) expanded.push(memberId);
        });
    });
    return expanded;
}
function beginLightboxPan(event){
    const image = event.target.closest?.('.asset-lightbox-image');
    const overlay = event.target.closest?.('.asset-lightbox');
    if(!image || !overlay) return;
    const scale = Number(overlay.dataset.scale || 1);
    if(scale <= 1.01) return;
    event.preventDefault();
    lightboxPanState = {
        overlay,
        pointerId:event.pointerId,
        startX:event.clientX,
        startY:event.clientY,
        originX:Number(overlay.dataset.x || 0),
        originY:Number(overlay.dataset.y || 0)
    };
    image.setPointerCapture?.(event.pointerId);
    overlay.classList.add('dragging');
}
function updateLightboxPan(event){
    if(!lightboxPanState || event.pointerId !== lightboxPanState.pointerId) return;
    const {overlay, startX, startY, originX, originY} = lightboxPanState;
    overlay.dataset.x = String(originX + event.clientX - startX);
    overlay.dataset.y = String(originY + event.clientY - startY);
    gwCall('preview', 'applyLightboxTransform', overlay);
}
function endLightboxPan(event){
    if(!lightboxPanState || event.pointerId !== lightboxPanState.pointerId) return;
    lightboxPanState.overlay?.classList.remove('dragging');
    lightboxPanState = null;
}
function detailReferenceFromTarget(target){
    const direct = target?.closest?.('[data-detail-source][data-detail-id]');
    if(direct) return {source:direct.dataset.detailSource || '', id:direct.dataset.detailId || ''};
    const registry = target?.closest?.('[data-registry-card]');
    if(registry) return {source:'registry', id:registry.dataset.registryCard || ''};
    const asset = target?.closest?.('[data-asset-card]');
    if(asset) return {source:'asset', id:asset.dataset.assetCard || ''};
    const workflow = target?.closest?.('[data-workflow-card]');
    if(workflow) return {source:'workflow', id:workflow.dataset.workflowCard || ''};
    const prompt = target?.closest?.('[data-prompt-row]');
    if(prompt) return {source:'prompt', id:prompt.dataset.promptRow || ''};
    const localUpload = target?.closest?.('[data-localup-card]');
    if(localUpload) return {source:'localup', id:localUpload.dataset.localupCard || ''};
    const local = target?.closest?.('[data-local-card]');
    if(local) return {source:'local', id:local.dataset.localCard || ''};
    const canvas = target?.closest?.('[data-canvas-asset-card]');
    if(canvas) return {source:'canvas-asset', id:canvas.dataset.canvasAssetCard || ''};
    return null;
}
function currentDetailViewerReference(){
    const focused = detailReferenceFromTarget(document.activeElement);
    if(focused?.id) return focused;
    if(activeTab === 'registry' || assetBoardById(activeTab)?.system === false) return selectedRegistryAssetId ? {source:'registry', id:selectedRegistryAssetId} : null;
    if(activeTab === 'assets') return selectedAssetId ? {source:'asset', id:selectedAssetId} : null;
    if(activeTab === 'workflows') return selectedWorkflowId ? {source:'workflow', id:selectedWorkflowId} : null;
    if(activeTab === 'prompts') return selectedPromptId ? {source:'prompt', id:selectedPromptId} : null;
    if(activeTab === 'canvas-assets') return selectedCanvasAssetId ? {source:'canvas-asset', id:selectedCanvasAssetId} : null;
    if(activeTab === 'local'){
        if(selectedLocalUploadId && document.querySelector(`[data-localup-card="${CSS.escape(selectedLocalUploadId)}"]`)) return {source:'localup', id:selectedLocalUploadId};
        if(selectedLocalId && document.querySelector(`[data-local-card="${CSS.escape(selectedLocalId)}"]`)) return {source:'local', id:selectedLocalId};
    }
    return null;
}
function rectsIntersect(a, b){
    return a.left < b.right && a.right > b.left && a.top < b.bottom && a.bottom > b.top;
}
function marqueeTargetSelector(){
    if(activeTab === 'registry' || assetBoardById(activeTab)?.system === false) return '[data-registry-card]';
    if(activeTab === 'assets' && assetManageMode) return '[data-asset-card]';
    if(activeTab === 'prompts' && promptManageMode) return '[data-prompt-row]';
    if(activeTab === 'local' && localUploadManageMode) return '[data-localup-card]';
    if(activeTab === 'canvas-assets' && canvasAssetManageMode) return '[data-canvas-asset-card]';
    return '';
}
function beginMarqueeSelection(event){
    const selector = marqueeTargetSelector();
    if(!selector) return;
    if(event.button !== 0) return;
    if(event.target.closest?.('[data-registry-detail], .registry-detail-panel, .asset-detail-viewer, .asset-detail-stage, .asset-detail-stage-content, .asset-detail-player, .detail-media, [data-floating-content]')) return;
    if(event.target.closest?.('button,input,textarea,select,.side-upload-card,.upload-grid-card,.asset-search-wrap')) return;
    const area = event.target.closest?.('.content-scroll');
    if(!area) return;
    const registryMode = activeTab === 'registry' || assetBoardById(activeTab)?.system === false;
    if(registryMode && event.target.closest?.('[data-registry-card]')) return;
    event.preventDefault();
    const box = document.createElement('div');
    box.className = 'selection-marquee';
    area.appendChild(box);
    marqueeState = {
        startX:event.clientX,
        startY:event.clientY,
        area,
        box,
        selector,
        mode:registryMode ? 'registry' : '',
        moved:false,
        baseRegistry:new Set(event.ctrlKey || event.metaKey || event.shiftKey ? registrySelectedIds : []),
        baseAsset:new Set(selectedAssetIds),
        basePrompt:new Set(selectedPromptIds),
        baseLocal:new Set(selectedLocalUploadIds),
        baseCanvasAsset:new Set(selectedCanvasAssetIds)
    };
    updateMarqueeSelection(event);
}
function updateMarqueeSelection(event){
    if(!marqueeState) return;
    const distance = Math.hypot(event.clientX-marqueeState.startX, event.clientY-marqueeState.startY);
    if(!marqueeState.moved && distance < 4) return;
    marqueeState.moved = true;
    const left = Math.min(marqueeState.startX, event.clientX);
    const top = Math.min(marqueeState.startY, event.clientY);
    const right = Math.max(marqueeState.startX, event.clientX);
    const bottom = Math.max(marqueeState.startY, event.clientY);
    const areaRect = marqueeState.area.getBoundingClientRect();
    const boxLeft = left - areaRect.left + marqueeState.area.scrollLeft;
    const boxTop = top - areaRect.top + marqueeState.area.scrollTop;
    Object.assign(marqueeState.box.style, {
        left:`${boxLeft}px`,
        top:`${boxTop}px`,
        width:`${Math.max(1, right - left)}px`,
        height:`${Math.max(1, bottom - top)}px`
    });
    const rect = {left, top, right, bottom};
    if(marqueeState.mode === 'registry'){
        registrySelectedIds = new Set(marqueeState.baseRegistry);
        let lastId = '';
        document.querySelectorAll(marqueeState.selector).forEach(el => {
            const id = el.dataset.registryCard || '';
            if(rectsIntersect(rect, el.getBoundingClientRect()) && id){ registrySelectedIds.add(id); lastId = id; }
            el.classList.toggle('selected', registrySelectedIds.has(id));
        });
        selectedRegistryAssetId = '';
        registryDetail = null;
        registryManageMode = registrySelectedIds.size > 0;
        registrySelectionAnchorId = lastId || registrySelectionAnchorId;
    } else if(activeTab === 'assets'){
        selectedAssetIds = new Set(marqueeState.baseAsset);
        document.querySelectorAll(marqueeState.selector).forEach(el => {
            if(rectsIntersect(rect, el.getBoundingClientRect())) selectedAssetIds.add(el.dataset.assetCard);
        });
    } else if(activeTab === 'prompts') {
        selectedPromptIds = new Set(marqueeState.basePrompt);
        document.querySelectorAll(marqueeState.selector).forEach(el => {
            if(rectsIntersect(rect, el.getBoundingClientRect())) selectedPromptIds.add(el.dataset.promptRow);
        });
    } else if(activeTab === 'local') {
        selectedLocalUploadIds = new Set(marqueeState.baseLocal);
        document.querySelectorAll(marqueeState.selector).forEach(el => {
            if(rectsIntersect(rect, el.getBoundingClientRect())) selectedLocalUploadIds.add(el.dataset.localupCard);
        });
    } else if(activeTab === 'canvas-assets') {
        selectedCanvasAssetIds = new Set(marqueeState.baseCanvasAsset);
        document.querySelectorAll(marqueeState.selector).forEach(el => {
            if(rectsIntersect(rect, el.getBoundingClientRect())) selectedCanvasAssetIds.add(el.dataset.canvasAssetCard);
        });
    }
    document.querySelectorAll('[data-asset-check]').forEach(input => { input.checked = selectedAssetIds.has(input.dataset.assetCheck); });
    document.querySelectorAll('[data-prompt-check]').forEach(input => { input.checked = selectedPromptIds.has(input.dataset.promptCheck); });
    document.querySelectorAll('[data-localup-check]').forEach(input => { input.checked = selectedLocalUploadIds.has(input.dataset.localupCheck); });
    document.querySelectorAll('[data-canvas-asset-check]').forEach(input => { input.checked = selectedCanvasAssetIds.has(input.dataset.canvasAssetCheck); });
    document.querySelectorAll('[data-registry-check]').forEach(input => { input.checked = registrySelectedIds.has(input.dataset.registryCheck); });
}
function endMarqueeSelection(){
    if(!marqueeState) return;
    const state = marqueeState;
    state.box.remove();
    marqueeState = null;
    pendingBatchDelete = '';
    if(state.mode === 'registry' && !state.moved){
        clearRegistrySelectionDom();
        return;
    }
    if(state.moved){
        if(state.mode === 'registry') registryMarqueeSuppressClickUntil = Date.now() + 250;
        render();
    }
}
async function saveAssetTreeEdit(){
    if(!assetTreeEdit) return;
    const name = document.getElementById('assetTreeEditInput')?.value || '';
    if(!String(name || '').trim()){
        setStatus('名称不能为空');
        return;
    }
    let data = null;
    if(assetTreeEdit.kind === 'library-new'){
        data = await apiJsonResponse(assetManagerApi.createAssetLibrary({name, expected_version:requireExpectedVersion(assetLibrary.version, '素材库目录')}));
        assetLibrary = data.library || assetLibrary;
        activeAssetLibraryId = data.asset_library?.id || activeAssetLibraryId;
        activeAssetClassFilter = '';
        assetTreeFocus = 'library';
    } else if(assetTreeEdit.kind === 'library-rename'){
        const lib = activeAssetLibrary();
        if(!lib) return;
        data = await apiJsonResponse(assetManagerApi.renameAssetLibrary(lib.id, {name, expected_version:requireExpectedVersion(lib.version, '素材库')}));
        assetLibrary = data.library || assetLibrary;
        assetTreeFocus = 'library';
    } else if(assetTreeEdit.kind === 'category-new'){
        data = await apiJsonResponse(assetManagerApi.createAssetLibraryCategory({library_id:activeAssetLibraryId, name, type:'image', expected_version:requireExpectedVersion(activeAssetLibrary()?.version, '素材库')}));
        assetLibrary = data.library || assetLibrary;
        activeAssetCategoryId = data.category?.id || activeAssetCategoryId;
        activeAssetClassFilter = '';
        assetTreeFocus = 'category';
    } else if(assetTreeEdit.kind === 'category-rename'){
        const cat = activeAssetCategory();
        if(!cat) return;
        data = await apiJsonResponse(assetManagerApi.renameAssetLibraryCategory(cat.id, {name, library_id:activeAssetLibraryId, expected_version:requireExpectedVersion(cat.version, '素材分类')}));
        assetLibrary = data.library || assetLibrary;
        assetTreeFocus = 'category';
    }
    assetTreeEdit = null;
    pendingTreeDelete = '';
    render();
    setStatus('已保存');
}
async function saveWorkflowTreeEdit(){
    if(!workflowTreeEdit) return;
    const name = document.getElementById('workflowTreeEditInput')?.value || '';
    if(!String(name || '').trim()){
        setStatus('名称不能为空');
        return;
    }
    if(workflowTreeEdit.kind === 'category-new'){
        const lib = activeWorkflowLibrary();
        if(!lib){
            setStatus('请先选择资产库');
            return;
        }
        activeWorkflowLibraryId = workflowLibraryId(lib) || activeWorkflowLibraryId;
        const data = await apiJsonResponse(assetManagerApi.createAssetLibraryCategory({library_id:activeWorkflowLibraryId, name, type:'workflow', expected_version:lib.version}));
        assetLibrary = data.asset_library || assetLibrary;
        activeWorkflowCategoryId = workflowCategoryId(data.category) || activeWorkflowCategoryId;
    } else if(workflowTreeEdit.kind === 'category-rename'){
        const cat = activeWorkflowCategory();
        const categoryId = workflowTreeEdit.categoryId || cat?.id || activeWorkflowCategoryId;
        if(!categoryId) return;
        const data = await apiJsonResponse(assetManagerApi.renameAssetLibraryCategory(categoryId, {name, library_id:activeWorkflowLibraryId, expected_version:requireExpectedVersion(cat?.version, '素材分类')}));
        assetLibrary = data.library || assetLibrary;
        activeWorkflowCategoryId = categoryId;
    }
    workflowTreeEdit = null;
    pendingTreeDelete = '';
    render();
    setStatus('已保存');
}
async function savePromptTreeEdit(){
    if(!promptTreeEdit) return;
    const name = document.getElementById('promptTreeEditInput')?.value || '';
    if(!String(name || '').trim()){
        setStatus('名称不能为空');
        return;
    }
    let data = null;
    if(promptTreeEdit.kind === 'library-new'){
        data = await apiJsonResponse(assetManagerApi.createPromptLibrary({name, expected_version:requireExpectedVersion(promptLibrary.version, '提示词库目录')}));
        promptLibrary = data.library || promptLibrary;
        activePromptLibraryId = data.prompt_library?.id || activePromptLibraryId;
        activePromptCategory = 'all';
        promptTreeFocus = 'library';
    } else if(promptTreeEdit.kind === 'library-rename'){
        const lib = activePromptLibrary();
        if(!lib) return;
        data = await apiJsonResponse(assetManagerApi.renamePromptLibrary(lib.id, {name, expected_version:requireExpectedVersion(lib.version, '提示词库')}));
        promptLibrary = data.library || promptLibrary;
        promptTreeFocus = 'library';
    } else if(promptTreeEdit.kind === 'category-new'){
        const lib = activePromptLibrary();
        data = await apiJsonResponse(assetManagerApi.createPromptLibraryCategory({library_id:lib?.id || 'system', name, expected_version:requireExpectedVersion(lib?.version, '提示词库')}));
        promptLibrary = data.library || promptLibrary;
        activePromptCategory = data.category?.id || activePromptCategory;
        promptTreeFocus = 'category';
    } else if(promptTreeEdit.kind === 'category-rename'){
        data = await apiJsonResponse(assetManagerApi.renamePromptLibraryCategory(activePromptCategory, {name, expected_version:promptCategoryVersion(activePromptCategory)}));
        promptLibrary = data.library || promptLibrary;
        promptTreeFocus = 'category';
    }
    promptTreeEdit = null;
    pendingTreeDelete = '';
    render();
    setStatus('已保存');
}
document.addEventListener('error', event => {
    const original = event.target?.closest?.('img[data-detail-media-preview-fallback]');
    if(original){
        const fallbackUrl = original.dataset.detailMediaPreviewFallback || '';
        original.removeAttribute('data-detail-media-preview-fallback');
        if(fallbackUrl && original.src !== fallbackUrl) original.src = fallbackUrl;
        return;
    }
    const image = event.target?.closest?.('img[data-preview-fallback]');
    if(!image) return;
    const fallback = document.createElement('div');
    fallback.className = 'asset-file-icon preview-unavailable';
    fallback.innerHTML = `<i data-lucide="file-image"></i><span>${escapeHtml(image.dataset.previewFallback || '文件')}</span><small>暂无预览</small>`;
    image.replaceWith(fallback);
    refreshIcons();
}, true);
root.addEventListener('pointerover', event => {
    const thumb = event.target?.closest?.('.asset-grid .asset-thumb');
    if(!thumb || thumb.contains(event.relatedTarget)) return;
    gwCall('preview', 'startAssetHoverPreview', event.target);
});
root.addEventListener('pointerout', event => {
    const thumb = event.target?.closest?.('.asset-grid .asset-thumb');
    if(!thumb || thumb.contains(event.relatedTarget)) return;
    if(assetHoverPreviewMedia && thumb.contains(assetHoverPreviewMedia)) gwCall('preview', 'stopAssetHoverPreview');
});
root.addEventListener('pointerdown', event => {
    // 单击只负责选择。详情统一由 dblclick 或“·”快捷键打开。
    if(event.button !== 0) return;
});
root.addEventListener('pointerdown', event => {
    if(event.button !== 0) return;
    const target = managedSelectionTarget(event.target);
    if(!target) return;
    event.preventDefault();
    event.stopPropagation();
    gwCall('organize', 'applyManagedSelection', target.kind, target.id);
    managedSelectionPointerGuard = {...target, at:Date.now()};
    if(target.kind === 'asset') gwCall('organize', 'syncAssetSelectionDom');
    else if(target.kind === 'localup') gwCall('upload', 'refreshLocalUploadSelectionOnly');
    else if(target.kind === 'local') gwCall('upload', 'syncLocalSelectionDom');
    else if(target.kind === 'workflow') gwCall('organize', 'syncWorkflowSelectionDom');
    else if(target.kind === 'canvasAsset') gwCall('canvas', 'refreshCanvasAssetSelectionOnly');
    else render();
}, true);
root.addEventListener('click', event => {
    handleClick(event).catch(err => setStatus(err.message || '操作失败'));
});
document.querySelector('.asset-breadcrumbs .asset-heading-row')?.addEventListener('click', event => {
    // 面包屑位于 assetManagerRoot 外，根节点的点击委托无法接收这里的点击。
    const crumb = event.target.closest?.('[data-asset-tree-crumb]');
    if(crumb) handleAssetTreeCrumbClick(crumb.dataset.assetTreeCrumb || '');
});
const DETAIL_BACKQUOTE_DOUBLE_TAP_MS = 500;
let lastDetailBackquoteTap = null;
let detailPreviewBackquoteCloseTimer = 0;
let detailImageEditorBackquoteCloseTimer = 0;
let detailTextEditorBackquoteInput = null;
function isBackquoteKey(event){
    return event.code === 'Backquote' || ['·','~','`'].includes(event.key);
}
function isBackquoteShortcut(event){
    return isBackquoteKey(event)
        && !event.ctrlKey && !event.metaKey && !event.altKey && !event.isComposing;
}
function detailBackquoteTappedTwice(event, context){
    if(!isBackquoteShortcut(event) || event.repeat) return false;
    const now = Date.now();
    if(lastDetailBackquoteTap?.context === context
        && now - lastDetailBackquoteTap.at <= DETAIL_BACKQUOTE_DOUBLE_TAP_MS){
        lastDetailBackquoteTap = null;
        return true;
    }
    lastDetailBackquoteTap = {at:now,context};
    return false;
}
function detailBackquoteContext(source, id){
    return `${String(source || '')}:${String(id || '')}`;
}
function detailEditableType(source, item){
    const type = detailViewerType(source,item);
    if(type === 'image') return true;
    return type === 'text' && detailViewerCanEditText(source,item);
}
document.addEventListener('keydown', event => {
    if(!isBackquoteShortcut(event)){
        lastDetailBackquoteTap = null;
        detailTextEditorBackquoteInput = null;
    }
    if(event.key === 'Escape'){
        // 【QIN-580】「套用 LUT」下拉菜单是最外层浮层，ESC 先收它，不直接关详情。
        if(detailViewerState.mediaLutMenu?.open){
            event.preventDefault();
            gwCall('lut', 'closeDetailMediaLutMenu');
            return;
        }
        // 缓存目录切换弹窗属于最上层确认框，先于目录设置页自身状态处理。
        if(thumbnailCacheSwitchState.open){
            event.preventDefault();
            gwCall('settings', 'cancelThumbnailCacheSwitch');
            return;
        }        if(pendingPurgeRoot){
            event.preventDefault();
            pendingPurgeRoot = null;
            gwCall('settings', 'renderGlobalDirectorySettings');
            return;
        }
        if(allowedRootsRemovedModalOpen){
            event.preventDefault();
            allowedRootsRemovedModalOpen = false;
            gwCall('settings', 'renderGlobalDirectorySettings');
            return;
        }
        if(serverDirectoryBrowserState.open){
            event.preventDefault();
            serverDirectoryBrowserState = {...serverDirectoryBrowserState, open:false, loading:false};
            gwCall('settings', 'renderServerDirectoryBrowserHost');
            if(serverDirectoryBrowserState.key === BOARD_CREATE_PATH_FIELD_KEY){
                requestAnimationFrame(() => document.querySelector(`[data-directory-picker="${BOARD_CREATE_PATH_FIELD_KEY}"]`)?.focus());
            }
            return;
        }
    }
    if(event.key === 'Enter'){
        // 路径类字段 Enter 即保存：登记目录 / 允许根 / 上传与生成 / 项目工作区 / 回收站 / 缓存根目录。
        const storageInput = event.target?.closest?.('[id^="storageDir_"]')
            || (event.target?.id === 'thumbnailDirectory' ? event.target : null)
            || (event.target?.id === 'allowedRootPath' ? event.target : null);
        if(storageInput && !storageInput.readOnly){
            event.preventDefault();
            const key = storageInput.id === 'thumbnailDirectory'
                ? THUMBNAIL_DIRECTORY_FIELD_KEY
                : storageInput.id === 'allowedRootPath'
                    ? ALLOWED_ROOT_FIELD_KEY
                    : storageInput.id.replace('storageDir_', '');
            gwCall('settings', 'saveDirectoryPath', key, storageInput.value).catch(error => setStatus(error.message || '保存目录失败'));
            return;
        }
    }
    if(trapProjectDirectoryTemplateManagerFocus(event) || trapStorageSettingsFocus(event)) return;
    if(event.key === 'Escape' && projectDirectoryTemplateManagerState.open){
        event.preventDefault();
        gwCall('settings', 'closeProjectDirectoryTemplateManagerSheet');
        return;
    }
    if(event.key === 'Escape' && document.querySelector('[data-video-storyboard-overlay]')){
        event.preventDefault(); gwCall('preview', 'closeDetailVideoStoryboard'); return;
    }
    if(detailImageEditorState){
        const state=detailImageEditorState;
        const inline=event.target?.matches?.('[data-image-editor-inline-text]');
        const formControl=['INPUT','TEXTAREA','SELECT'].includes(String(event.target?.tagName||'').toUpperCase());
        if(inline){
            if(event.key==='Escape'){ event.preventDefault(); state.inlineTextId=''; gwCall('preview', 'renderDetailImageEditor'); }
            return;
        }
        if(formControl) return;
        if(event.key === 'Escape'){
            event.preventDefault();
            gwCall('preview', 'requestCloseDetailImageEditor', '退出编辑器')
                .catch(err => setStatus(err.message || '单图编辑关闭失败'));
            return;
        }
        if(isBackquoteShortcut(event)){
            event.preventDefault();
            event.stopPropagation();
            if(event.repeat) return;
            const context = `image-editor:${state.itemId || ''}`;
            if(detailBackquoteTappedTwice(event,context)){
                clearTimeout(detailImageEditorBackquoteCloseTimer);
                detailImageEditorBackquoteCloseTimer = 0;
                gwCall('preview', 'requestCloseDetailImageEditor', '关闭编辑器')
                    .catch(err => setStatus(err.message || '单图编辑关闭失败'));
            } else {
                gwCall('preview', 'deferDetailImageEditorCloseForBackquote', state);
            }
            return;
        }
        const key=String(event.key||'').toLowerCase();
        if(event.shiftKey && key==='n'){ event.preventDefault(); gwCall('preview', 'newDetailImageEditorBrushLayer', true); gwCall('preview', 'renderDetailImageEditor'); }
        else if((event.shiftKey || ((event.ctrlKey||event.metaKey)&&event.shiftKey)) && key==='g'){ event.preventDefault(); gwCall('preview', 'ungroupDetailImageEditorLayers'); }
        else if((event.ctrlKey||event.metaKey) && key==='g'){ event.preventDefault(); gwCall('preview', 'groupDetailImageEditorLayers'); }
        else if(event.key==='Enter' && state.tool==='crop' && state.cropDraft){ event.preventDefault(); gwCall('preview', 'applyDetailImageEditorCrop', state.cropDraft); }
        else if(event.key==='Delete' || event.key==='Backspace'){ event.preventDefault(); gwCall('preview', 'deleteDetailImageEditorLayers'); }
        else if(!event.ctrlKey && !event.metaKey && !event.altKey && key==='q'){ event.preventDefault(); gwCall('preview', 'setDetailImageEditorTool', 'select'); }
        else if(!event.ctrlKey && !event.metaKey && !event.altKey && key==='b'){ event.preventDefault(); gwCall('preview', 'setDetailImageEditorTool', 'brush'); }
        else if(!event.ctrlKey && !event.metaKey && !event.altKey && key==='e'){ event.preventDefault(); gwCall('preview', 'setDetailImageEditorTool', 'eraser'); }
        else if(!event.ctrlKey && !event.metaKey && !event.altKey && key==='c'){ event.preventDefault(); gwCall('preview', 'setDetailImageEditorTool', 'crop'); }
        else if(!event.ctrlKey && !event.metaKey && !event.altKey && key==='t'){ event.preventDefault(); gwCall('preview', 'addDetailImageEditorText'); }
        else if(['+','=','add'].includes(key) && ['brush','eraser'].includes(state.tool)){ event.preventDefault(); state.brushWidth=Math.min(300,state.brushWidth+1); gwCall('preview', 'renderDetailImageEditor'); }
        else if(['-','subtract'].includes(key) && ['brush','eraser'].includes(state.tool)){ event.preventDefault(); state.brushWidth=Math.max(1,state.brushWidth-1); gwCall('preview', 'renderDetailImageEditor'); }
        return;
    }
    const libraryResizer = event.target?.closest?.('[data-detail-media-library-resizer]');
    if(libraryResizer && (event.key === 'ArrowLeft' || event.key === 'ArrowRight')){
        event.preventDefault();
        gwCall('media-canvas', 'setDetailMediaCanvasLibraryWidth', detailMediaCanvasLibraryWidth+(event.key === 'ArrowLeft' ? 16 : -16),true);
        return;
    }
    const mediaLibraryItem = event.target?.closest?.('[data-detail-media-library-item]');
    if(mediaLibraryItem && (event.key === 'Enter' || event.key === ' ')){
        event.preventDefault();
        gwCall('media-canvas', 'selectDetailMediaCanvasLibraryItem', mediaLibraryItem.dataset.detailMediaLibraryItem || '');
        return;
    }
    const keyboardEditing = ['INPUT','TEXTAREA','SELECT'].includes(String(event.target?.tagName || '').toUpperCase()) || event.target?.isContentEditable;
    const interactiveTarget = event.target?.closest?.('button,a,input,textarea,select');
    const detailToggleShortcut = isBackquoteShortcut(event);
    const focusedRegistryVersionStructure = event.target?.closest?.('[data-registry-structure-card][data-registry-structure-type="version"]')
        || document.activeElement?.closest?.('[data-registry-structure-card][data-registry-structure-type="version"]');
    if(detailToggleShortcut && focusedRegistryVersionStructure && !keyboardEditing && !detailViewerState.open){
        event.preventDefault();
        openRegistryStructureReferenceCanvas(
            focusedRegistryVersionStructure.dataset.registryStructureCard || '',
            focusedRegistryVersionStructure,
        );
        return;
    }
    if(detailToggleShortcut && !keyboardEditing){
        event.preventDefault();
        event.stopPropagation();
        if(event.repeat) return;
        if(detailViewerState.open){
            const item = detailViewerState.items[detailViewerState.index]
                || detailViewerItem(detailViewerState.source,detailViewerState.id);
            if(item && detailEditableType(detailViewerState.source,item)){
                const context = detailBackquoteContext(detailViewerState.source,item.id);
                if(detailBackquoteTappedTwice(event,context)){
                    clearTimeout(detailPreviewBackquoteCloseTimer);
                    detailPreviewBackquoteCloseTimer = 0;
                    gwCall('preview', 'enterCurrentDetailEditableContent');
                } else {
                    gwCall('preview', 'deferDetailPreviewCloseForBackquote', detailViewerState);
                }
            } else {
                closeDetailPreview();
            }
        } else {
            const reference = currentDetailViewerReference();
            if(reference?.id){
                const item = detailViewerItem(reference.source,reference.id);
                const editable = item && detailEditableType(reference.source,item);
                const isDouble = editable && detailBackquoteTappedTwice(
                    event,
                    detailBackquoteContext(reference.source,reference.id),
                );
                gwCall('preview', 'showDetailPreview', reference.source,reference.id);
                if(isDouble) gwCall('preview', 'enterCurrentDetailEditableContent');
            }
        }
        return;
    }
    if(detailViewerState.open){
        if(event.key === 'Escape' && document.fullscreenElement) return;
        if(event.target.closest?.('[data-detail-version-editor]')){
            if(event.key === 'Escape'){
                event.preventDefault();
                gwCall('preview', 'cancelDetailTextVersionEdit');
            } else if((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 's'){
                event.preventDefault();
                gwCall('settings', 'saveDetailTextVersionEdit').catch(err => setStatus(err.message || '版本保存失败'));
            }
            return;
        }
        if(event.target.closest?.('[data-detail-text-editor]')){
            if(event.key === 'Escape'){
                event.preventDefault();
                gwCall('preview', 'cancelDetailTextEdit');
            } else if((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 's'){
                event.preventDefault();
                gwCall('settings', 'saveDetailTextEdit').catch(err => setStatus(err.message || '正文保存失败'));
            } else if(isBackquoteShortcut(event)){
                if(event.repeat){
                    event.preventDefault();
                    lastDetailBackquoteTap = null;
                    detailTextEditorBackquoteInput = null;
                    return;
                }
                const editor = event.target;
                const context = `text-editor:${detailBackquoteContext(detailViewerState.source,detailViewerState.id)}`;
                if(detailBackquoteTappedTwice(event,context)){
                    event.preventDefault();
                    gwCall('preview', 'exitDetailTextEditFromBackquote', editor,detailTextEditorBackquoteInput);
                } else {
                    const start = Number(editor.selectionStart || 0);
                    const end = Number(editor.selectionEnd || start);
                    detailTextEditorBackquoteInput = {
                        context,
                        editor,
                        before:String(editor.value || ''),
                        start,
                        end,
                        key:event.key,
                    };
                }
            }
            return;
        }
        if(event.target.closest?.('[data-detail-name-input]')){
            if(event.key === 'Enter'){
                event.preventDefault();
                gwCall('settings', 'saveDetailViewerName').catch(err => setStatus(err.message || '名称保存失败'));
            }
            return;
        }
        if(keyboardEditing) return;
        const activeVideoItem = detailViewerVideoItem();
        const activeVideo = detailViewerType(detailViewerState.source,activeVideoItem) === 'video' ? detailViewerVideo() : null;
        if(activeVideo){
            const key = String(event.key || '').toLowerCase();
            if((event.ctrlKey || event.metaKey) && event.shiftKey && !event.altKey && key === 'c'){
                event.preventDefault(); gwCall('preview', 'exportDetailVideoFrame', '',false).catch(err => { setStatus(err.message || '单帧导出失败'); showAssetFailure('单帧导出失败',err); }); return;
            }
            if((event.ctrlKey || event.metaKey) && !event.shiftKey && !event.altKey && key === 'c'){
                event.preventDefault(); gwCall('organize', 'copyDetailVideoFrameToClipboard').catch(err => setStatus(err.message || '视频帧复制失败')); return;
            }
            if(!event.ctrlKey && !event.metaKey && !event.altKey && ['i','o','x','c','z'].includes(key)){
                event.preventDefault();
                const action = key === 'i' ? 'set-in' : key === 'o' ? 'set-out' : key === 'x' ? 'rate-down' : key === 'c' ? 'rate-up' : 'rate-reset';
                const button = document.querySelector(`.asset-detail-viewer [data-detail-video-action="${action}"]`);
                if(button) gwCall('preview', 'handleDetailVideoControl', button).catch(err => setStatus(err.message || '视频快捷操作失败'));
                return;
            }
        }
        if((event.ctrlKey || event.metaKey) && !event.shiftKey && !event.altKey && event.key.toLowerCase() === 'c' && detailMediaCanvasItems().length){
            event.preventDefault();
            gwCall('media-canvas', 'copyDetailMediaCanvasSelectionToClipboard').catch(err => setStatus(err.message || '图片复制失败'));
            return;
        }
        if(detailMediaCanvasItems().length || detailMediaCanvasAnnotations().length || detailMediaCanvasDrawings().length){
            if((event.ctrlKey || event.metaKey) && event.shiftKey && event.key.toLowerCase() === 'g'){
                event.preventDefault(); gwCall('media-canvas', 'ungroupDetailMediaCanvasSelection'); return;
            }
            if((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'g'){
                event.preventDefault(); gwCall('media-canvas', 'groupDetailMediaCanvasSelection'); return;
            }
            if((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'a'){
                event.preventDefault();
                gwCall('media-canvas', 'setDetailMediaCanvasSelection', detailMediaCanvasObjectIds());
                gwCall('media-canvas', 'syncDetailMediaCanvasSelectionDom');
                setStatus(`已全选画布内 ${detailMediaCanvasObjectIds().length} 个对象`);
                return;
            }
            if((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'z'){
                event.preventDefault(); gwCall('media-canvas', 'undoDetailMediaCanvas'); return;
            }
            if(event.key === 'Delete'){
                event.preventDefault(); gwCall('media-canvas', 'deleteDetailMediaCanvasSelection'); return;
            }
        }
        if(event.key === 'Escape' && detailViewerState.mediaCanvasContextMenu){
            event.preventDefault();
            gwCall('media-canvas', 'closeDetailMediaCanvasContextMenu');
            return;
        }
        if(event.key === 'Escape'){
            event.preventDefault();
            closeDetailPreview();
            return;
        }
        const detailType = detailViewerType(detailViewerState.source, detailViewerState.items[detailViewerState.index] || {});
        const focusedMediaNode = event.target.closest?.('[data-detail-media-canvas-node]');
        const focusedMediaCanvas = event.target.closest?.('[data-detail-media-canvas-viewport]');
        const key = String(event.key || '').toLowerCase();
        if(detailType === 'video' && !interactiveTarget
            && !event.ctrlKey && !event.metaKey && !event.altKey && key === 'f'
            && (document.fullscreenElement || detailMediaCanvasSelectedCoverflowAssets().length <= 1)){
            event.preventDefault();
            gwCall('preview', 'toggleDetailVideoFullscreen');
            return;
        }
        if((focusedMediaNode || focusedMediaCanvas) && !interactiveTarget
            && !event.ctrlKey && !event.metaKey && !event.altKey && key === 'f'){
            event.preventDefault();
            gwCall('coverflow-bridge', 'openDetailMediaCanvasSelectionCoverflow', event.target);
            return;
        }
        if(focusedMediaNode && !interactiveTarget && ['Enter',' '].includes(event.key)){
            event.preventDefault();
            gwCall('media-canvas', 'activateDetailMediaCanvasItem', focusedMediaNode.dataset.detailMediaCanvasNode || '');
            return;
        }
        if(detailType === 'video' && !interactiveTarget
            && !event.ctrlKey && !event.metaKey && !event.altKey && ['a','d'].includes(key)){
            event.preventDefault();
            gwCall('preview', 'stepDetailVideoFrame', key === 'd' ? 1 : -1);
            return;
        }
        if(detailType === 'video' && !interactiveTarget
            && !event.ctrlKey && !event.metaKey && !event.altKey && key === 'f'){
            event.preventDefault();
            gwCall('preview', 'toggleDetailVideoFullscreen');
            return;
        }
        if(detailType === 'video' && !interactiveTarget && event.key === 'Enter'){
            event.preventDefault();
            gwCall('preview', 'enterDetailVideoFullscreen');
            return;
        }
        if(['ArrowLeft','ArrowRight'].includes(event.key)){
            event.preventDefault();
            if(detailMediaCanvasItems().length > 1) gwCall('media-canvas', 'cycleDetailMediaCanvasItem', event.key === 'ArrowRight' ? 1 : -1);
            else gwCall('preview', 'navigateDetailPreview', event.key === 'ArrowRight' ? 1 : -1);
            return;
        }
        // 空格键处理：画布模式下作为抓手快捷键；常规视频/音频详情模式下作为播放/暂停
        if((event.key === ' ' || event.code === 'Space') && !event.target.closest?.('button,a,input,textarea,select')){
            event.preventDefault(); // 始终阻止默认滚动行为
            const canvasViewport = document.querySelector('[data-detail-media-canvas-viewport]');
            if(canvasViewport){
                if(!detailMediaCanvasSpacePressed){
                    detailMediaCanvasSpacePressed = true;
                    canvasViewport.classList.add('is-space-panning');
                }
                return;
            }
            const item = detailViewerState.items[detailViewerState.index] || {};
            const type = detailViewerType(detailViewerState.source, item);
            // 只有普通视频或音频查看器才执行播放/暂停操作
            if(type === 'video' || type === 'audio'){
                gwCall('preview', 'toggleDetailPreviewPlayback');
            }
            return;
        }
    }
    if(event.key === 'Escape'){
        closeDetailPreview();
        if(boardCreateState.open) gwCall('organize', 'closeCreateLibraryModal');
        if(boardSourceState.open) gwCall('organize', 'closeBoardSourceModal');
        if(boardEditState.open) gwCall('organize', 'closeBoardEditModal');
        if(boardFolderCreateState.open) gwCall('organize', 'closeCustomBoardFolderCreateModal');
        if(thumbnailGeneratorState.open) gwCall('settings', 'closeThumbnailGenerator');
        if(storageSettingsState.open) gwCall('settings', 'closeStorageSettings');
        if(registryRecycleBinState.open) gwCall('recycle', 'closeRegistryRecycleBin');
    }
    const editing = keyboardEditing;
    if((activeTab === 'registry' || assetBoardById(activeTab)?.system === false) && !editing){
        const visible = activeRegistryVisibleAssets();
        if((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'a'){
            event.preventDefault();
            visible.forEach(item => registrySelectedIds.add(item.id));
            registryManageMode = true;
            render();
            return;
        }
        if(['ArrowLeft','ArrowRight','ArrowUp','ArrowDown'].includes(event.key) && visible.length){
            event.preventDefault();
            const current = Math.max(0, visible.findIndex(item => item.id === selectedRegistryAssetId));
            const grid = document.querySelector('.registry-grid');
            const virtualColumns = grid?.classList.contains('registry-grid-virtualized')
                ? Number(registryVirtualState.cols || 0)
                : 0;
            const columns = Math.max(1, virtualColumns || String(grid ? getComputedStyle(grid).gridTemplateColumns : '').split(' ').filter(Boolean).length || 1);
            const delta = event.key === 'ArrowLeft' ? -1 : event.key === 'ArrowRight' ? 1 : event.key === 'ArrowUp' ? -columns : columns;
            registryFocusIndex = Math.max(0, Math.min(visible.length - 1, current + delta));
            registryFocusRestoreId = visible[registryFocusIndex].id;
            selectedRegistryAssetId = registryFocusRestoreId;
            registryDetail = visible[registryFocusIndex];
            render();
            requestAnimationFrame(() => {
                const card = document.querySelector(`[data-registry-card="${CSS.escape(registryFocusRestoreId)}"]`);
                card?.scrollIntoView({block:'nearest'});
                card?.focus({preventScroll:true});
            });
            return;
        }
        const registryFullscreenShortcut = event.key === 'Enter'
            || (String(event.key || '').toLowerCase() === 'f'
                && !event.ctrlKey && !event.metaKey && !event.altKey);
        if(String(event.key || '').toLowerCase() === 'f' && registryFullscreenViewerState){
            // 真全屏查看器打开时，F 仅负责关闭（捕获阶段监听已处理，此处兜底防穿透）。
            event.preventDefault();
            gwCall('preview', 'closeRegistryAssetFullscreenViewer');
            return;
        }
        if(registryFullscreenShortcut && !interactiveTarget){
            const current = registryAssets.find(item => item.id === selectedRegistryAssetId)
                || (registryDetail?.id === selectedRegistryAssetId ? registryDetail : null)
                || visible.find(item => item.id === selectedRegistryAssetId || item.asset_id === selectedRegistryAssetId)
                || null;
            if(current && registryCoverflowSourceIsVisual('registry', current)){
                event.preventDefault();
                if(event.key === 'Enter'){
                    gwCall('coverflow-bridge', 'openRegistryCoverflow', document.activeElement, {currentAssetId:String(current.asset_id || current.id || '')});
                } else {
                    // F 键语义（QIN-570）：浏览器真全屏查看当前素材，不再打开 Coverflow。
                    gwCall('preview', 'openRegistryAssetFullscreenViewer', document.activeElement, {currentAssetId:String(current.asset_id || current.id || '')});
                }
                return;
            }
        }
        if(event.key === 'Escape' && registrySelectedIds.size){
            registrySelectedIds.clear();
            registryManageMode = false;
            render();
            return;
        }
    }
    if(event.target?.id === 'assetTreeEditInput'){
        if(event.key === 'Enter'){ event.preventDefault(); saveAssetTreeEdit().catch(err => setStatus(err.message || '保存失败')); }
        if(event.key === 'Escape'){ event.preventDefault(); assetTreeEdit = null; render(); }
    }
    if(event.target?.id === 'workflowTreeEditInput'){
        if(event.key === 'Enter'){ event.preventDefault(); saveWorkflowTreeEdit().catch(err => setStatus(err.message || '保存失败')); }
        if(event.key === 'Escape'){ event.preventDefault(); workflowTreeEdit = null; render(); }
    }
    if(event.target?.id === 'promptTreeEditInput'){
        if(event.key === 'Enter'){ event.preventDefault(); savePromptTreeEdit().catch(err => setStatus(err.message || '保存失败')); }
        if(event.key === 'Escape'){ event.preventDefault(); promptTreeEdit = null; render(); }
    }
});
document.addEventListener('keyup', event => {
    if(event.code === 'Space' || event.key === ' '){
        if(detailMediaCanvasSpacePressed){
            detailMediaCanvasSpacePressed = false;
            document.querySelector('[data-detail-media-canvas-viewport]')?.classList.remove('is-space-panning');
        }
    }
});
window.addEventListener('blur', () => {
    if(detailMediaCanvasSpacePressed){
        detailMediaCanvasSpacePressed = false;
        document.querySelector('[data-detail-media-canvas-viewport]')?.classList.remove('is-space-panning');
    }
});
let registryCardSizeSaveTimer = 0;
let registryCardSizeLayoutFrame = 0;
let registryCardSizeVirtualMeasureRevision = 0;
function handleRegistryCardResizeWheel(event){
    if(!event.shiftKey || event.ctrlKey || event.metaKey || event.altKey) return;
    const target = event.target;
    if(!target?.closest?.('[data-registry-zoom-area]')) return;
    if(!target.closest('.registry-content.registry-layout-grid')) return;
    event.preventDefault();
    registryCardSize = Math.max(REGISTRY_CARD_SIZE.min, Math.min(REGISTRY_CARD_SIZE.max, registryCardSize + (event.deltaY > 0 ? -12 : 12)));
    root.querySelectorAll('.registry-content').forEach(node => node.style.setProperty('--registry-card-size', registryCardSize + 'px'));
    registryCardSizeVirtualMeasureRevision += 1;
    if(!registryCardSizeLayoutFrame){
        registryCardSizeLayoutFrame = requestAnimationFrame(() => {
            registryCardSizeLayoutFrame = 0;
            if(root.querySelector('.registry-grid-virtualized')){
                updateRegistryVirtualWindow(true, true);
                const virtualMeasureRevision = registryCardSizeVirtualMeasureRevision;
                requestAnimationFrame(() => {
                    if(virtualMeasureRevision !== registryCardSizeVirtualMeasureRevision) return;
                    if(root.querySelector('.registry-grid-virtualized')) updateRegistryVirtualWindow(true, true);
                });
            } else {
                root.querySelectorAll('.registry-content.registry-layout-grid .registry-grid:not(.registry-grid-virtualized)').forEach(remeasureRegistryMasonryGrid);
                scheduleRegistryMasonryLayout();
            }
        });
    }
    clearTimeout(registryCardSizeSaveTimer);
    registryCardSizeSaveTimer = setTimeout(() => {
        gwCall('settings', 'saveRegistryViewSettings');
        const boundary = registryCardSize === REGISTRY_CARD_SIZE.min ? '（已到最小）' : registryCardSize === REGISTRY_CARD_SIZE.max ? '（已到最大）' : '';
        setStatus('预览大小 ' + registryCardSize + 'px' + boundary);
    }, 240);
}
document.addEventListener('wheel', handleRegistryCardResizeWheel, {passive:false});
document.addEventListener('scroll', event => {
    if(event.target?.matches?.('[data-detail-media-library-grid]')) detailViewerState.mediaCanvasLibraryScrollTop = event.target.scrollTop;
    const directScroll = event.target?.closest?.('.registry-assets-scroll');
    const registryScroll = directScroll || registryPageScrollTarget(event);
    if(registryScroll){
        // 页面级滚动布局下容器的 scrollTop 恒为 0，保存它没有意义；且文档滚动事件密集，
        // 每次都写 sessionStorage / 快照会拖慢滚动，故只对容器自身的滚动做位置沉淀。
        if(directScroll) saveRegistryBrowseSession();
        // 【浏览预加载】触发提前量从固定 520px 提升到约 1.3 倍可见视口高度（1.2~1.5 倍区间取中），
        // 下一页数据预取先于滚动到位，配合 prefetchRegistryNextPage 实现无感续读；
        // 键盘可达的 [data-registry-load-more] 按钮保留不动。
        // 【QIN-584 AC1】提前量按可见视口算，不能按被卡片撑高的 clientHeight 算。
        const lead = Math.max(520, Math.round(registryVisibleViewportHeight(registryScroll) * 1.3));
        if(registryReachedLoadMoreBoundary(registryScroll, lead)){
            loadMoreRegistryAssets().catch(err => setStatus(err.message || '加载更多资产失败'));
        }
    }
}, true);
document.addEventListener('contextmenu', openRegistryStructureContextMenu);
/* 【QIN-546】我的资源整理：右键菜单、双击改名、内联输入收尾、Ctrl+Z 撤销与拖拽移动 */
document.addEventListener('contextmenu', openAssetOrganizeMenu);
document.addEventListener('keydown', handleAssetOrganizeKeydown);
document.addEventListener('dblclick', event => {
    const nameEl = event.target.closest?.('.asset-tree-panel .asset-tree-row .asset-tree-name');
    if(!nameEl) return;
    const row = nameEl.closest('[data-asset-tree-node]');
    const target = assetOrganizeTargetFromKey(row?.dataset.assetTreeNode || '');
    if(!target || target.isRoot) return;
    event.preventDefault();
    event.stopPropagation();
    gwCall('organize', 'beginAssetOrganizeRename', target);
});
document.addEventListener('focusout', event => {
    const input = event.target?.closest?.('[data-asset-organize-rename-input]');
    // 输入框随重绘被摘除时也会触发 focusout，已断连的节点不得再提交一次
    if(!input || !input.isConnected) return;
    const target = assetOrganizeTargetFromKey(input.dataset.assetOrganizeRenameInput || '');
    if(target) void gwCall('organize', 'commitAssetOrganizeRename', target, input.value);
});
document.addEventListener('dragleave', event => {
    const viewport = event.target.closest?.('[data-detail-media-canvas-viewport]');
    if(viewport && !viewport.contains(event.relatedTarget)) viewport.classList.remove('is-asset-drag-over');
});
document.addEventListener('dragend', () => {
    detailMediaLibraryDragId = '';
    document.querySelector('[data-detail-media-canvas-viewport]')?.classList.remove('is-asset-drag-over');
});
document.addEventListener('auxclick', event => {
    if(event.button === 1 && event.target.closest?.('[data-detail-media-canvas-viewport],.asset-image-editor-stage')) event.preventDefault();
});
document.addEventListener('pointerdown', beginLightboxPan);
document.addEventListener('pointermove', updateLightboxPan);
document.addEventListener('pointerup', endLightboxPan);
document.addEventListener('pointercancel', endLightboxPan);
root.addEventListener('pointerdown', beginMarqueeSelection);
document.addEventListener('pointermove', event => updateMarqueeSelection(event));
document.addEventListener('pointerup', endMarqueeSelection);
document.addEventListener('pointercancel', endMarqueeSelection);
root.addEventListener('compositionstart', event => {
    if(SEARCH_INPUT_IDS.has(event.target?.id || '')){
        searchCompositionActive = true;
        clearTimeout(searchRenderTimer);
    }
});
root.addEventListener('compositionend', event => {
    const id = event.target?.id || '';
    if(!SEARCH_INPUT_IDS.has(id)) return;
    searchCompositionActive = false;
    lastSearchCompositionEndAt = Date.now();
    updateSearchQueryFromInput(id, event.target.value || '');
    scheduleSearchRender(id, event.target.selectionStart || String(event.target.value || '').length, 0);
});
root.addEventListener('input', event => {
    if(event.target?.id === 'boardCreateName') boardCreateState.name = event.target.value || '';
    if(event.target?.id === 'boardCreatePath') boardCreateState.path = event.target.value || '';
    const searchId = event.target?.id || '';
    if(SEARCH_INPUT_IDS.has(searchId)){
        updateSearchQueryFromInput(searchId, event.target.value || '');
        if(event.isComposing || searchCompositionActive) return;
        if(Date.now() - lastSearchCompositionEndAt < 40) return;
        scheduleSearchRender(searchId, event.target.selectionStart || 0);
        return;
    }
    if(event.target?.id === 'localCaptionPrompt'){
        localCaptionPrompt = event.target.value || '';
        gwCall('settings', 'writeLocalCaptionSettings');
    }
    if(event.target?.id === 'localClassifyPrompt'){
        localClassifyPrompt = event.target.value || '';
        gwCall('settings', 'writeLocalCaptionSettings');
    }
});
root.addEventListener('change', event => {
    const inlineLocalUploadName = event.target.closest?.('[data-localup-inline-name]');
    if(inlineLocalUploadName){
        gwCall('upload', 'saveLocalUploadInlineName', inlineLocalUploadName.dataset.localupInlineName || '', inlineLocalUploadName.value || '').catch(err => setStatus(err.message || '保存失败'));
        return;
    }
    const inlineAssetName = event.target.closest?.('[data-asset-inline-name]');
    if(inlineAssetName){
        gwCall('settings', 'saveAssetInlineName', inlineAssetName.dataset.assetInlineName || '', inlineAssetName.value || '').catch(err => setStatus(err.message || '保存失败'));
        return;
    }
    const inlineWorkflowName = event.target.closest?.('[data-workflow-inline-name]');
    if(inlineWorkflowName){
        const itemId = inlineWorkflowName.dataset.workflowInlineName || '';
        apiJsonResponse(assetManagerApi.updateAssetLibraryItem(itemId, {name:inlineWorkflowName.value || '', expected_version:assetLibraryVersionForItem(itemId)})).then(data => {
            assetLibrary = data.library || assetLibrary;
            render();
            setStatus('已保存工作流名称');
        }).catch(err => setStatus(err.message || '保存失败'));
        return;
    }
    if(event.target?.id === 'canvasAssetSort'){
        canvasAssetSort = event.target.value || 'canvas_asc';
        selectedCanvasAssetId = '';
        render();
    }
    if(event.target?.id === 'assetMoveTarget'){
        assetMoveTarget = event.target.value || '';
        pendingBatchDelete = '';
        render();
    }
    if(event.target?.id === 'localCaptionProvider'){
        localCaptionProvider = event.target.value || '';
        localCaptionModel = '';
        gwCall('settings', 'normalizeLocalCaptionSettings');
        gwCall('settings', 'writeLocalCaptionSettings');
        render();
    }
    if(event.target?.id === 'localCaptionModel'){
        localCaptionModel = event.target.value || '';
        gwCall('settings', 'writeLocalCaptionSettings');
        render();
    }
    const avatarProvider = event.target.closest?.('[data-avatar-provider]');
    if(avatarProvider){
        avatarRegisterProvider = avatarProvider.value || '';
        render();
    }
});
document.addEventListener('change', event => {
    if(event.target?.matches?.('[data-thumbnail-format]')){
        const nextFormat = event.target.value === 'webp' ? 'webp' : 'jxl';
        const previousFormat = thumbnailGeneratorState.format === 'webp' ? 'webp' : 'jxl';
        if(nextFormat === previousFormat || thumbnailGeneratorState.savingFormat) return;
        thumbnailGeneratorState = {...thumbnailGeneratorState, format:nextFormat, savingFormat:true, error:''};
        if(storageSettingsState.open) gwCall('settings', 'renderStorageSettingsModal');
        if(thumbnailGeneratorState.open) gwCall('settings', 'renderThumbnailGeneratorModal');
        gwCall('settings', 'persistThumbnailStorageSettings', {format:nextFormat}).catch(error => {
            thumbnailGeneratorState = {...thumbnailGeneratorState, format:previousFormat, error:error.message || '保存缩略图格式失败'};
        }).finally(() => {
            thumbnailGeneratorState = {...thumbnailGeneratorState, savingFormat:false};
            if(storageSettingsState.open) gwCall('settings', 'renderStorageSettingsModal');
            if(thumbnailGeneratorState.open) gwCall('settings', 'renderThumbnailGeneratorModal');
        });
        return;
    }
    if(event.target?.id === 'thumbnailDirectoryPreset'){
        thumbnailGeneratorState.directory = event.target.value || '';
        const input = document.getElementById('thumbnailDirectory');
        if(input) input.value = thumbnailGeneratorState.directory;
        gwCall('settings', 'applyThumbnailCacheDirectoryField', thumbnailGeneratorState.directory);
        gwCall('settings', 'updateThumbnailCacheExclusionHint');
        return;
    }
    if(event.target?.id === 'boardSourceExisting'){
        const lib = localLibraries.find(item => item.id === event.target.value);
        const nameInput = document.getElementById('boardSourceName');
        const pathInput = document.getElementById('boardSourcePath');
        if(lib){ if(nameInput) nameInput.value = lib.name || '用户素材'; if(pathInput) pathInput.value = lib.path || ''; }
        else if(pathInput) pathInput.value = '';
        return;
    }
    if(!event.target?.closest?.('#storageSettingsOverlay')) return;
    if(event.target?.id === 'thumbnailDirectory'){
        thumbnailGeneratorState.directory = String(event.target.value || '').trim();
        gwCall('settings', 'updateThumbnailCacheExclusionHint');
        if(!directorySettingsPage && thumbnailGeneratorState.mode === 'custom' && thumbnailGeneratorState.directory){
            gwCall('settings', 'persistThumbnailStorageSettings').catch(err => {
                thumbnailGeneratorState.error = err.message || '缓存目录创建失败';
                gwCall('settings', 'renderStorageSettingsModal');
            });
        }
        return;
    }
    if(event.target?.id === 'thumbnailTranscodeVideos'){
        thumbnailGeneratorState.transcodeVideos = Boolean(event.target.checked);
        return;
    }
    if(event.target?.id === 'prefCaptionProvider'){
        gwCall('settings', 'syncStorageSettingsInputsToState');
        localCaptionProvider = event.target.value || '';
        localCaptionModel = '';
        gwCall('settings', 'normalizeLocalCaptionSettings');
        gwCall('settings', 'renderStorageSettingsModal');
        return;
    }
    if(event.target?.id === 'prefCaptionModel'){
        localCaptionModel = event.target.value || '';
        return;
    }
});
root.addEventListener('dragstart', event => {
    const tab = event.target.closest?.('[data-local-library-drag]');
    if(!tab) return;
    localLibraryDragId = tab.dataset.localLibraryDrag || '';
    event.dataTransfer?.setData('text/plain', localLibraryDragId);
    event.dataTransfer.effectAllowed = 'move';
    tab.classList.add('dragging');
});
root.addEventListener('dragover', event => {
    const libraryTab = event.target.closest?.('[data-local-library-tab]');
    if(localLibraryDragId && libraryTab){
        event.preventDefault();
        event.dataTransfer.dropEffect = 'move';
        return;
    }
    const drop = event.target.closest?.('#assetDrop, #localUploadDrop, #workflowDrop');
    if(!drop) return;
    event.preventDefault();
    drop.classList.add('drag-over');
});
root.addEventListener('dragleave', event => {
    event.target.closest?.('#assetDrop, #localUploadDrop, #workflowDrop')?.classList.remove('drag-over');
});
root.addEventListener('drop', event => {
    const libraryTab = event.target.closest?.('[data-local-library-tab]');
    if(localLibraryDragId && libraryTab){
        event.preventDefault();
        const changed = reorderLocalLibraries(localLibraryDragId, libraryTab.dataset.localLibraryTab || '');
        localLibraryDragBlockUntil = Date.now() + 500;
        localLibraryDragId = '';
        if(changed) gwCall('settings', 'saveLocalLibrarySettings').then(() => render()).catch(err => setStatus(err.message || '保存素材库排序失败'));
        else render();
        return;
    }
    const drop = event.target.closest?.('#assetDrop, #localUploadDrop, #workflowDrop');
    if(!drop) return;
    event.preventDefault();
    drop.classList.remove('drag-over');
    if(drop.id === 'localUploadDrop') gwCall('upload', 'uploadLocalAssets', event.dataTransfer.files);
    else if(drop.id === 'workflowDrop') gwCall('upload', 'uploadWorkflowFiles', event.dataTransfer.files).catch(err => setStatus(err.message || '上传失败'));
    else gwCall('upload', 'uploadFiles', event.dataTransfer.files).catch(err => setStatus(err.message || '上传失败'));
});
root.addEventListener('dragend', () => {
    if(!localLibraryDragId) return;
    localLibraryDragId = '';
    render();
});
/* 【QIN-546】我的资源整理拖拽：与素材库排序/上传拖拽用独立状态与 data 属性隔离，互不拦截 */
root.addEventListener('dragstart', beginAssetOrganizeDrag);
root.addEventListener('dragover', allowAssetOrganizeDrop);
root.addEventListener('dragleave', event => {
    if(!assetOrganizeDropHint?.row) return;
    if(event.target?.closest?.('.asset-tree-row') === assetOrganizeDropHint.row) gwCall('organize', 'clearAssetOrganizeDropHint');
});
root.addEventListener('drop', event => {
    if(!assetOrganizeDrag) return;
    event.preventDefault();
    void gwCall('organize', 'dropAssetOrganize', event);
});
root.addEventListener('dragend', endAssetOrganizeDrag);
uploadInput?.addEventListener('change', event => {
    const files = event.target.files;
    if(files?.length){
        if(activeTab === 'local') gwCall('upload', 'uploadLocalAssets', files);
        else if(activeTab === 'workflows') gwCall('upload', 'uploadWorkflowFiles', files).catch(err => setStatus(err.message || '上传失败'));
        else gwCall('upload', 'uploadFiles', files).catch(err => setStatus(err.message || '上传失败'));
    }
    event.target.value = '';
});
assetTabsEl?.addEventListener('click', event => {
    if(event.target.closest?.('[data-board-create]')){ gwCall('organize', 'openCreateLibraryModal'); return; }
    if(event.target.closest?.('[data-registry-recycle-bin-open]')){ gwCall('recycle', 'openRegistryRecycleBin').catch(err => setStatus(err.message || '打开回收站失败')); return; }
    if(event.target.closest?.('[data-thumbnail-generator-open]')){ gwCall('settings', 'openThumbnailGenerator').catch(err => setStatus(err.message || '打开缩略图工具失败')); return; }
    if(event.target.closest?.('[data-refresh-assets]')){ resetRegistryScroll(); loadAll().catch(err => setStatus(err.message || '刷新资产失败')); return; }
    if(event.target.closest?.('[data-storage-directories-open]')){ gwCall('settings', 'openStorageSettings', 'directories').catch(err => setStatus(err.message || '打开保存目录失败')); return; }
    if(event.target.closest?.('[data-board-settings-open]')){ gwCall('settings', 'openStorageSettings', 'boards').catch(err => setStatus(err.message || '打开资产设置失败')); return; }
    const tab = event.target.closest?.('[data-tab]');
    if(tab) gwCall('organize', 'activateAssetBoard', tab.dataset.tab || 'registry').catch(err => setStatus(err.message || '打开看板失败'));
});
root.addEventListener('click', async event => {
    if(event.target.closest?.('[data-refresh-assets]')){ resetRegistryScroll(); loadAll().catch(err => setStatus(err.message || '刷新资产失败')); return; }
    if(event.target.closest?.('[data-asset-collect-open]')){
        const trigger = event.target.closest('[data-asset-collect-open]');
        try {
            const pair = await fetch('/api/asset-collect/pair', {method:'POST'});
            if(!pair.ok) throw new Error('无法连接本机收集服务');
            let {pair_token} = await pair.json();
            let pairExpires = Date.now() + 50 * 60 * 1000;
            const adapter = createHttpCollectAdapter(async (...args) => {
                if(Date.now() >= pairExpires){
                    const renewed = await fetch('/api/asset-collect/pair', {method:'POST'});
                    if(!renewed.ok) throw new Error('配对已失效，请重新登录');
                    pair_token = (await renewed.json()).pair_token;
                    pairExpires = Date.now() + 50 * 60 * 1000;
                }
                const [url, request = {}] = args;
                if(request.body instanceof FormData) request.body.set('pair_token', pair_token);
                else if(typeof request.body === 'string'){
                    const payload = JSON.parse(request.body);
                    payload.pair_token = pair_token;
                    request.body = JSON.stringify(payload);
                }
                request.headers = {...request.headers, 'X-Collect-Token':pair_token};
                args = [url, request];
                const response = await fetch(...args);
                if(response.ok){
                    if(String(url).includes('/asset-collect/jobs/')){
                        const body = await response.json();
                        if(body.status === 'failed' && body.message) body.error_code = body.message;
                        return {ok:true, json:async () => body};
                    }
                    return response;
                }
                const body = await response.json().catch(() => ({}));
                const detail = body.detail || {};
                return {ok:false, status:response.status, json:async () => ({error_code:detail.code, message:detail.message})};
            });
            openAssetCollectOverlay(trigger, {adapter, pairToken:pair_token});
        } catch(error) {
            window.alert(error?.message || '无法连接本机收集服务');
        }
        return;
    }
    const create = event.target.closest?.('[data-script-create]');
    if(create){ gwCall('preview', 'openEpisodePipelineFromAsset', {projectId:localStorage.getItem('workspace_project_id') || ''}); return; }
    const open = event.target.closest?.('[data-script-open]');
    const copy = event.target.closest?.('[data-script-copy]');
    if(!open && !copy) return;
    const item = (scriptArchiveItems.length ? scriptArchiveItems : readScriptWorkspaceAssets()).find(value => String(value.id || '') === String((open || copy).dataset.scriptOpen || (open || copy).dataset.scriptCopy || ''));
    if(!item) return;
    if(open){ gwCall('preview', 'openEpisodePipelineFromAsset', item); return; }
    navigator.clipboard?.writeText(String(item.content || '')).then(() => setStatus('剧本正文已复制')).catch(() => setStatus('复制失败，请检查剪贴板权限'));
});
assetTabsEl?.addEventListener('keydown', event => {
    const current = event.target.closest?.('[data-tab]');
    if(!current || !['ArrowLeft','ArrowRight','Home','End'].includes(event.key)) return;
    event.preventDefault();
    const id = current.dataset.tab || '';
    if(event.altKey && ['ArrowLeft','ArrowRight'].includes(event.key)){
        gwCall('organize', 'moveAssetBoardByKeyboard', id, event.key === 'ArrowRight' ? 1 : -1);
        return;
    }
    const tabs = [...assetTabsEl.querySelectorAll('[data-tab]')];
    const index = tabs.indexOf(current);
    const nextIndex = event.key === 'Home' ? 0 : event.key === 'End' ? tabs.length - 1 : (index + (event.key === 'ArrowRight' ? 1 : -1) + tabs.length) % tabs.length;
    tabs[nextIndex]?.focus();
    tabs[nextIndex]?.click();
});
assetTabsEl?.addEventListener('dragstart', event => {
    const tab = event.target.closest?.('[data-board-drag]');
    if(!tab) return;
    boardDragId = tab.dataset.boardDrag || '';
    event.dataTransfer?.setData('text/plain', boardDragId);
    if(event.dataTransfer) event.dataTransfer.effectAllowed = 'move';
    tab.classList.add('dragging');
});
assetTabsEl?.addEventListener('dragover', event => {
    const target = event.target.closest?.('[data-tab]');
    if(!boardDragId || !target || target.dataset.tab === boardDragId) return;
    event.preventDefault();
    if(event.dataTransfer) event.dataTransfer.dropEffect = 'move';
    assetTabsEl.querySelectorAll('.drag-target').forEach(item => item.classList.remove('drag-target'));
    target.classList.add('drag-target');
});
assetTabsEl?.addEventListener('drop', event => {
    const target = event.target.closest?.('[data-tab]');
    if(!boardDragId || !target) return;
    event.preventDefault();
    const movedId = boardDragId;
    const changed = reorderAssetBoard(movedId, target.dataset.tab || '');
    boardDragId = '';
    if(changed){ renderBoardNavigation(); requestAnimationFrame(() => assetTabsEl.querySelector(`[data-tab="${CSS.escape(movedId)}"]`)?.focus()); setStatus('看板顺序已保存'); }
});
assetTabsEl?.addEventListener('dragend', () => {
    boardDragId = '';
    assetTabsEl.querySelectorAll('.dragging,.drag-target').forEach(item => item.classList.remove('dragging','drag-target'));
});
window.addEventListener('message', event => {
    if(event.origin && event.origin !== location.origin) return;
    if(event.data?.type === 'gw-touchbar-action'){
        if(event.origin !== location.origin || event.source !== window.parent || event.data.version !== 1) return;
        if(event.data.action_id === 'asset.open-detail' && selectedRegistryAssetId){
            gwCall('preview', 'openRegistryAsset', selectedRegistryAssetId).catch(err => setStatus(err.message || '打开素材失败'));
            return;
        }
        if(event.data.action_id === 'asset.add-to-canvas' && selectedRegistryAssetId){
            if(!canAddRegistryAssetToCanvas()){
                setStatus('当前账户没有将素材加入画布的权限');
                return;
            }
            registrySelectedIds.add(selectedRegistryAssetId);
            registryManageMode = true;
            registryBulkPanel = 'canvas';
            render();
        }
        return;
    }
    if(event.data?.type === 'gw-touchbar-request'){
        if(event.origin !== location.origin || event.source !== window.parent || event.data.version !== 1) return;
        // The shell may have missed the initial capability while this iframe
        // was inactive. Report the current, sanitized context as a handshake.
        scheduleAssetTouchbarReport();
        return;
    }
    if(event.data?.type === 'studio-theme') window.StudioTheme?.apply?.(event.data.theme);
    if(event.data?.type === 'asset-return-home') returnAssetManagerHome();
    if(event.data?.type === 'asset-global-search'){
        const assetId = touchbarStableId(event.data.asset_id);
        if(event.origin !== location.origin || event.source !== window.parent || !assetId) return;
        // MVP1-T6：壳层 Omnibar 的镜头命中会带上 start_sec，直接复用 C18-T5 跳帧。
        const rawStart = event.data.start_sec;
        const startSec = rawStart == null || rawStart === '' ? null : Number(rawStart);
        openRegistryAssetFromGlobalSearch(assetId, Number.isFinite(startSec) ? Math.max(0, startSec) : null)
            .catch(err => setStatus(err.message || '打开素材失败'));
    }
    if(event.data?.type === 'asset-refresh'){
        resetRegistryScroll();
        loadAll().catch(err => setStatus(err.message || '刷新资产失败'));
    }
    if(event.data?.type === 'asset-sync'){
        // 切回页签=轻量增量同步；新素材自动插入但不动已加载内容（全量刷新语义保留在 asset-refresh）
        syncRegistryOnReveal().catch(err => setStatus(err.message || '增量同步资产失败'));
    }
});
window.addEventListener('gw:omnibar-result-select', (event) => {
    const detail = event?.detail || {};
    const result = detail.result || {};
    const assetId = result.asset_id || result.id;
    let targetTime = null;
    if (result.start_sec != null) targetTime = Number(result.start_sec);
    else if (result.time != null) targetTime = Number(result.time);
    else if (typeof result.href === 'string' && result.href.includes('#t=')) {
        const match = result.href.match(/#t=([\d.]+)/);
        if (match) targetTime = Number(match[1]);
    }
    if (assetId) {
        openRegistryAssetFromGlobalSearch(assetId, targetTime).catch(err => {
            console.error(err);
            setStatus(err.message || '打开素材失败');
        });
    }
});
// asset-review.js remains a classic script during the incremental M3 migration.
// Keep the page globals it consumes available after asset-manager.js becomes a module.
Object.defineProperties(window, {
    readStoredBoolean:{configurable:true, value:readStoredBoolean},
    readStoredNumber:{configurable:true, value:readStoredNumber},
    selectedRegistryAsset:{configurable:true, value:selectedRegistryAsset},
    registrySelectedIds:{configurable:true, get:() => registrySelectedIds},
    formatVideoSegmentTime:{configurable:true, value:formatVideoSegmentTime},
    formatVideoSegmentRange:{configurable:true, value:formatVideoSegmentRange},
    alignTimeToFps:{configurable:true, value:alignTimeToFps},
    findMatchedVideoSegment:{configurable:true, value:findMatchedVideoSegment},
    ensureMediaPreviewPlayer:{configurable:true, value:ensureMediaPreviewPlayer},
    seekDetailVideoTo:{configurable:true, value:seekDetailVideoTo},
    openRegistryAssetFromGlobalSearch:{configurable:true, value:openRegistryAssetFromGlobalSearch},
});
const REGISTRY_SESSION_KEY = 'gw.registry.browse.session';
const REGISTRY_SNAPSHOT_KEY = 'gw_asset_registry_snapshot_v1';
const REGISTRY_ROUTE_PENDING_KEY = 'gw.registry.browse.route.pending';
let registryBrowseInteracted = false;
let registrySessionReady = false;
document.addEventListener('pointerdown', () => { registryBrowseInteracted = true; }, {capture:true, once:true});
document.addEventListener('keydown', () => { registryBrowseInteracted = true; }, {capture:true, once:true});
// 【QIN-544】树面板键盘导航（捕获阶段委托，render 重建 DOM 不受影响）
document.addEventListener('keydown', handleAssetTreeKeydown, {capture:true});

// 【QIN-573 AC2】保存当前首屏核心快照供下次打开资产库时秒开
function saveRegistrySnapshot(){
    try {
        if(!Array.isArray(registryAssets) || !registryAssets.length) return false;
        const snapshot = {
            version: 1,
            savedAt: Date.now(),
            tab: activeTab,
            total: registryTotal,
            totalKnown: registryTotalKnown,
            facets: {
                kinds: Array.isArray(registryFacets?.kinds) ? registryFacets.kinds : [],
                categories: Array.isArray(registryFacets?.categories) ? registryFacets.categories : [],
                tags: Array.isArray(registryFacets?.tags) ? registryFacets.tags : [],
                collections: Array.isArray(registryFacets?.collections) ? registryFacets.collections : [],
                roots: Array.isArray(registryFacets?.roots) ? registryFacets.roots : [],
            },
            status: registryStatus && typeof registryStatus === 'object' ? registryStatus : {},
            items: registryAssets.slice(0, 60),
        };
        sessionStorage.setItem(REGISTRY_SNAPSHOT_KEY, JSON.stringify(snapshot));
        return true;
    } catch(_) { return false; }
}

// 【QIN-573 AC2】优先从会话快照恢复首屏渲染，隐藏骨架屏
function tryApplyRegistrySnapshot(){
    try {
        const raw = sessionStorage.getItem(REGISTRY_SNAPSHOT_KEY);
        if(!raw) return false;
        const snapshot = JSON.parse(raw);
        if(!snapshot || !Array.isArray(snapshot.items) || !snapshot.items.length) return false;
        if(registryAssets.length > 0 && registryBootstrapCompleted) return false;
        registryAssets = snapshot.items;
        if(typeof snapshot.total === 'number') registryTotal = snapshot.total;
        registryTotalKnown = Boolean(snapshot.totalKnown);
        if(snapshot.facets && typeof snapshot.facets === 'object') registryFacets = snapshot.facets;
        if(snapshot.status && typeof snapshot.status === 'object') registryStatus = snapshot.status;
        const skeleton = document.getElementById('assetManagerBootSkeleton');
        if(skeleton) skeleton.hidden = true;
        if(activeTab === 'registry' || assetBoardById(activeTab)?.system === false) render();
        return true;
    } catch(_) { return false; }
}

function saveRegistryBrowseSession(){
    if(!registrySessionReady || !assetBoardById(activeTab)) return false;
    saveRegistrySnapshot();
    try {
        sessionStorage.setItem(REGISTRY_SESSION_KEY, JSON.stringify({
            tab:activeTab, folder:registryNavFolder, sources:boardSourceSelection,
            folders:boardFolderSelection, scroll:root.querySelector('.registry-assets-scroll')?.scrollTop || 0,
        }));
        return true;
    } catch(_) { return false; }
}
function saveRegistryBrowseSessionForRoute(){
    if(!saveRegistryBrowseSession()) return;
    try { sessionStorage.setItem(REGISTRY_ROUTE_PENDING_KEY, '1'); } catch(_) {}
}
window.GWAssetBrowseSession = Object.freeze({save:saveRegistryBrowseSession, saveForRoute:saveRegistryBrowseSessionForRoute});
// 【QIN-573 AC2】快照控制对象暴露给测试与同源宿主
window.GWAssetRegistrySnapshot = Object.freeze({save:saveRegistrySnapshot, apply:tryApplyRegistrySnapshot});
// 【QIN-566】外壳侧栏树桥接：同源直调（外壳读快照/驱动入口，内嵌页渲染后主动推送）。
window.GWAssetTreeBridge = Object.freeze({snapshot:gwAssetTreeSnapshot, activate:gwAssetTreeActivate});
// 【QIN-579】外壳侧栏整理能力桥接：右键菜单/双击改名/拖拽移动/重名/撤销
async function gwAssetTreeOpenInSystem(key){
    await loadAssetAllowedRootPaths?.();
    const target = assetOrganizeTargetFromKey(key);
    if(!target) return {status:'failed', message:'目标不可整理'};
    return assetOrganizeOpenFolderInSystem(target);
}
async function gwAssetTreeCreateFolder(parentKey, folderName){
    await loadAssetAllowedRootPaths?.();
    let target = parentKey ? assetOrganizeTargetFromKey(parentKey) : null;
    let rootId = target?.rootId || '';
    if(!target){
        rootId = assetOrganizeDefaultRootId();
        if(!rootId) throw new Error('未找到可写挂载目录');
    }
    const name = assetOrganizeValidateFolderName(folderName);
    const parentPath = target ? target.path : '';
    const newPath = parentPath ? `${parentPath}/${name}` : name;
    const abs = assetOrganizeAbsPath(rootId, newPath);
    if(!abs) throw new Error('挂载路径不可用，无法新建文件夹');
    const data = await assetOrganizeRequest('/api/local-assets/folders', {
        path: abs,
        root_id: rootId,
    });
    gwCall('organize', 'assetOrganizePushUndo', data, `新建文件夹「${name}」`, rootId);
    gwCall('organize', 'showAssetOrganizeToast', `已新建文件夹「${name}」`, {undoable:true});
    setStatus(`已新建文件夹「${name}」`);
    assetTreeSelectedKey = `mine:root:${rootId}::${newPath}`;
    await gwCall('organize', 'assetOrganizeRefresh', rootId);
    return data;
}
async function gwAssetTreeCommitRename(key, newName){
    await loadAssetAllowedRootPaths?.();
    const target = assetOrganizeTargetFromKey(key);
    if(!target || target.isRoot) return false;
    const current = assetOrganizeLeafName(target.path);
    const next = String(newName || '').trim();
    if(!next || next === current) return true;
    try {
        const name = assetOrganizeValidateFolderName(next);
        const abs = assetOrganizeAbsPath(target.rootId, target.path);
        const data = await assetOrganizeRequest('/api/local-assets/folders', {
            path: abs,
            name: name,
            root_id: target.rootId,
        }, {method:'PATCH'});
        gwCall('organize', 'assetOrganizePushUndo', data, `重命名为「${name}」`, target.rootId);
        gwCall('organize', 'showAssetOrganizeToast', `已重命名文件夹为「${name}」`, {undoable:true});
        setStatus(`已重命名文件夹为「${name}」`);
        const parent = assetOrganizeParentPath(target.path);
        const nextPath = parent ? `${parent}/${name}` : name;
        assetTreeSelectedKey = `mine:root:${target.rootId}::${nextPath}`;
        await gwCall('organize', 'assetOrganizeRefresh', target.rootId);
        return true;
    } catch(err) {
        gwCall('organize', 'showAssetOrganizeToast', assetOrganizeErrorText(err));
        setStatus(assetOrganizeErrorText(err));
        return false;
    }
}
async function gwAssetTreeDeleteFolder(key){
    await loadAssetAllowedRootPaths?.();
    const target = assetOrganizeTargetFromKey(key);
    if(!target || target.isRoot) return false;
    const label = assetOrganizeFolderLabel(target);
    const absPath = assetOrganizeAbsPath(target.rootId, target.path);
    try {
        const data = await assetOrganizeRequest('/api/local-assets/folders/delete', {
            path: absPath,
            root_id: target.rootId,
        });
        gwCall('organize', 'assetOrganizePushUndo', data, `删除 ${label}`, target.rootId);
        gwCall('organize', 'showAssetOrganizeToast', `已移入回收站：${label}`, {undoable:true});
        setStatus(`已删除文件夹「${label}」`);
        assetTreeSelectedKey = `mine:root:${target.rootId}`;
        await gwCall('organize', 'assetOrganizeRefresh', target.rootId);
        return true;
    } catch(err) {
        gwCall('organize', 'showAssetOrganizeToast', assetOrganizeErrorText(err));
        setStatus(assetOrganizeErrorText(err));
        return false;
    }
}
async function gwAssetTreeMoveFolder(dragKey, targetKey, zone='into'){
    await loadAssetAllowedRootPaths?.();
    const drag = assetOrganizeTargetFromKey(dragKey);
    const target = assetOrganizeTargetFromKey(targetKey);
    if(!drag || !target || drag.isRoot) return false;
    if(drag.rootId !== target.rootId){
        gwCall('organize', 'showAssetOrganizeToast', '仅支持在同一挂载文件夹内移动');
        setStatus('仅支持在同一挂载文件夹内移动');
        return false;
    }
    const leaf = assetOrganizeLeafName(drag.path);
    let destPath = '';
    if(zone === 'into'){
        destPath = target.path ? `${target.path}/${leaf}` : leaf;
    } else {
        const parent = assetOrganizeParentPath(target.path);
        destPath = parent ? `${parent}/${leaf}` : leaf;
    }
    await gwCall('organize', 'assetOrganizeMoveFolder', drag, target.rootId, destPath);
    return true;
}
async function gwAssetTreeMoveAssets(assetIds, targetKey){
    await loadAssetAllowedRootPaths?.();
    const target = assetOrganizeTargetFromKey(targetKey);
    if(!target) return false;
    await gwCall('organize', 'assetOrganizeMoveAssets', assetIds, target.rootId, target.path);
    return true;
}
window.GWAssetOrganizeBridge = Object.freeze({
    toggleExpand: toggleAssetTreeNode,
    canOrganize: key => Boolean(assetOrganizeTargetFromKey(key)),
    getTarget: key => assetOrganizeTargetFromKey(key),
    openInSystem: gwAssetTreeOpenInSystem,
    createFolder: gwAssetTreeCreateFolder,
    commitRename: gwAssetTreeCommitRename,
    deleteFolder: gwAssetTreeDeleteFolder,
    moveFolder: gwAssetTreeMoveFolder,
    moveAssets: gwAssetTreeMoveAssets,
    undoLast: assetOrganizeUndoLast,
    getSelectedAssetIds: () => Array.from(registrySelectedIds),
    defaultRootId: () => assetOrganizeDefaultRootId(),
    askConflict: assetOrganizeAskConflict,
    hasUndo: () => assetOrganizeUndoStack.length > 0,
    folderLabel: key => assetOrganizeFolderLabel(assetOrganizeTargetFromKey(key)),
});
async function restoreRegistryBrowseSession(){
    try {
        const navigation = performance.getEntriesByType?.('navigation')?.[0];
        const saved = JSON.parse(sessionStorage.getItem(REGISTRY_SESSION_KEY) || 'null');
        const restoreForShellRoute = sessionStorage.getItem(REGISTRY_ROUTE_PENDING_KEY) === '1';
        sessionStorage.removeItem(REGISTRY_SESSION_KEY);
        sessionStorage.removeItem(REGISTRY_ROUTE_PENDING_KEY);
        const parentNavigation = window.parent !== window ? window.parent.performance.getEntriesByType?.('navigation')?.[0] : navigation;
        if(!saved || navigation?.type === 'reload' || (parentNavigation?.type === 'reload' && !restoreForShellRoute) || registryBrowseInteracted) return;
        registryNavFolder = saved.folder || {rootId:'', path:''};
        boardSourceSelection = saved.sources || {};
        boardFolderSelection = saved.folders || {};
        if(saved.tab === 'registry' && (registryNavFolder.rootId || registryNavFolder.path)) await refreshRegistryAssets(false);
        if(saved.tab && saved.tab !== 'registry' && assetBoardById(saved.tab)){
            await gwCall('organize', 'activateAssetBoard', saved.tab);
            if(registryLoading) await refreshRegistryAssets(false);
        }
        render();
        const desired = Math.max(0, Number(saved.scroll) || 0);
        let scroll = root.querySelector('.registry-assets-scroll');
        while(scroll && desired > scroll.scrollHeight - scroll.clientHeight && registryHasMoreAssets()){
            const previous = registryNextOffset;
            await loadMoreRegistryAssets();
            if(registryNextOffset === previous) break;
            scroll = root.querySelector('.registry-assets-scroll');
        }
        savedScrollPositions.registry = desired;
        return desired;
    } catch(_) {}
}
async function applyRegistryBrowseSessionScroll(desired){
    const deadline = Date.now() + 1500;
    let area = null;
    while(Date.now() < deadline){
        area = root.querySelector('.registry-assets-scroll');
        if(area && (desired <= 0 || desired <= area.scrollHeight - area.clientHeight)) break;
        await new Promise(resolve => setTimeout(resolve, 50));
    }
    area = root.querySelector('.registry-assets-scroll');
    if(area){ area.scrollTop = desired; scheduleRegistryVirtualWindow(); }
}
window.addEventListener('pagehide', saveRegistryBrowseSession);
document.addEventListener('click', () => {
    if(registrySessionReady) setTimeout(saveRegistryBrowseSession, 250);
});
if(!directorySettingsPage){
    renderBoardNavigation();
    if(document.readyState !== 'loading') tryApplyRegistrySnapshot();
}
// 【QIN-585 AC2】首屏延后块：收集箱与治理建议都不参与第一屏卡片渲染，
// 却在 DOMContentLoaded 里与首屏列表并发请求。治理建议在千级素材库上单次要跑数秒，
// 单进程服务端会把首屏列表请求排在它后面 —— 首屏因此被一个「用户还没打开的抽屉」拖住。
// 改为首屏就绪后再发，用空闲时段调度；两条链路都自带失败兜底，延后不改变可用性。
function scheduleDeferredBootRequests(){
    const run = async () => {
        // 【QIN-585 AC2】idle 已经排队之后用户仍可能刷新开了新一代列表：此刻直接发请求
        // 会抢在它前面；治理建议还会被 pending 闸门挡下，之后再无人补取。执行时按当前代际
        // 再确认一次（wait 内部会循环等新一代落地），两条链路最终都仍会补齐。
        await waitRegistryBootstrapSettled();
        // 用户可能已经在首屏期间点开收集箱：已加载过就不再补一次，避免同一份数据读两遍。
        if(!inboxLoaded){
            void gwLoad('inbox').then(m => m.loadAssetInbox()).then(() => {
                if(assetInboxUnreadCount() > 0 && activeTab !== 'inbox') render();
            }).catch(() => null);
        }
        gwCall('governance', 'loadGovernanceSuggestions');
    };
    const dispatch = () => {
        const launch = () => { void run(); };
        if(typeof window.requestIdleCallback === 'function'){
            window.requestIdleCallback(launch, {timeout:2000});
            return;
        }
        setTimeout(launch, 200);
    };
    // 调度时机绑定「首屏列表请求终态」而不是调用点：成功分支调用时列表也已落地，
    // 失败分支却可能只是资产库/本地设置先失败、列表仍在途 —— 必须等它落地再放行。
    void waitRegistryBootstrapSettled().then(dispatch);
    // 【QIN-591】首屏就绪后的空闲时段预热：把全部延后域模块拉起并完成 GW_DEFERRED 注册，
    // 用户首次使用对应功能时 gwCall 命中已注册函数面，无需等待动态 import。
    if (typeof requestIdleCallback === 'function') {
        requestIdleCallback(() => {
            for (const d of Object.keys(GW_DOMAIN_LOADERS)) { GW_DOMAIN_LOADERS[d]().catch(() => {}); }
        }, { timeout: 2000 });
    }
}
document.addEventListener('DOMContentLoaded', () => {
    if(directorySettingsPage){ loadGlobalDirectorySettings(); return; }
    // 【QIN-573 AC2】优先从会话快照恢复首屏渲染，隐藏骨架屏
    tryApplyRegistrySnapshot();
    loadRegistryPrincipal();
    // 【QIN-585 AC2】治理建议的启动期意图仍在此声明；loadGovernanceSuggestions 会在
    // 首屏列表落地前把自己排队（见该函数内的闸门），不再与列表争服务端资源。
    gwCall('governance', 'loadGovernanceSuggestions');
    loadAll().then(async () => {
        const restoredScroll = await restoreRegistryBrowseSession();
        // 【QIN-566】恢复上次外壳侧栏入口（仅内嵌金库模式；刷新/往返不丢目标入口，AC5）。
        await restoreAssetTreeState();
        registrySessionReady = true;
        if(Number.isFinite(restoredScroll)) await applyRegistryBrowseSessionScroll(restoredScroll);
        saveRegistryBrowseSession();
        // 【QIN-582 AC5】首屏就绪：项目计数延后到空闲时段，5s 索引轮询仍只在此之后启动。
        markRegistryFirstScreenReady();
        startRegistryAutomaticIndexPolling();
    }).catch(err => {
        markRegistryFirstScreenReady();
        setStatus(err.message || '加载失败');
    });
    // 【QIN-585 AC2】无论首屏成败都排入延后链路（用户仍可能直接打开收集箱或治理抽屉），
    // 调度内部只等首屏列表请求的真实终态，因此成功/失败两个分支共用这一处调用。
    scheduleDeferredBootRequests();
});

// 【QIN-591】跨文件状态写通道：以下 setter 由延后域模块调用（域文件内不直接赋值，主文件其余函数体保持逐字不变）
function setDirectorySettingsSaving(v){ directorySettingsSaving = v; }
function setClientUploadSourceDir(v){ clientUploadSourceDir = v; }
function setClientSettingBusy(v){ clientSettingBusy = v; }
function setActiveTab(v){ activeTab = v; }
function gwSetAssetRackCollapsed(v){ assetRackCollapsed = v; }
function setAssetTreeTab(v){ assetTreeTab = v; }
function setAssetTreeSelectedKey(v){ assetTreeSelectedKey = v; }
function setRegistryProjectEntityFilter(v){ registryProjectEntityFilter = v; }
function setAssetOrganizeDialog(v){ assetOrganizeDialog = v; }
function setAssetOrganizeDrag(v){ assetOrganizeDrag = v; }
function setAssetOrganizeDropHint(v){ assetOrganizeDropHint = v; }
function setRegistryNavFolder(v){ registryNavFolder = v; }
function setRegistryRegisteredFolders(v){ registryRegisteredFolders = v; }
function setRegistryIndexedFolders(v){ registryIndexedFolders = v; }
function setRegistryRegisteredFoldersLoaded(v){ registryRegisteredFoldersLoaded = v; }
function setBoardCreateState(v){ boardCreateState = v; }
function setBoardCreateFocusTarget(v){ boardCreateFocusTarget = v; }
function setBoardEditState(v){ boardEditState = v; }
function setBoardSourceState(v){ boardSourceState = v; }
function setBoardFolderCreateState(v){ boardFolderCreateState = v; }
function setRegistryRackSnapshot(v){ registryRackSnapshot = v; }
function setRegistryAssets(v){ registryAssets = v; }
function setRegistryCoverflowCurrentAssetId(v){ registryCoverflowCurrentAssetId = v; }
function setRegistryCoverflowBoardId(v){ registryCoverflowBoardId = v; }
function setRegistryStatus(v){ registryStatus = v; }
function setRegistryPresets(v){ registryPresets = v; }
function setRegistryTotal(v){ registryTotal = v; }
function setRegistryTotalKnown(v){ registryTotalKnown = v; }
function setRegistryStatCategory(v){ registryStatCategory = v; }
function setRegistryQuery(v){ registryQuery = v; }
function setRegistryKind(v){ registryKind = v; }
function setRegistryRootId(v){ registryRootId = v; }
function setRegistryRecentDays(v){ registryRecentDays = v; }
function setSelectedRegistryAssetId(v){ selectedRegistryAssetId = v; }
function setRegistryDetail(v){ registryDetail = v; }
function setRegistryDetailPanelCollapsed(v){ registryDetailPanelCollapsed = v; }
function setRegistryManageMode(v){ registryManageMode = v; }
function setRegistryGroupMode(v){ registryGroupMode = v; }
function setRegistryBulkPanel(v){ registryBulkPanel = v; }
function setRegistryCanvasTargets(v){ registryCanvasTargets = v; }
function setRegistryCanvasLoading(v){ registryCanvasLoading = v; }
function setRegistryTagEditorAssetId(v){ registryTagEditorAssetId = v; }
function setRegistryLoading(v){ registryLoading = v; }
function setRegistryPageLoading(v){ registryPageLoading = v; }
function setRegistryRequestSeq(v){ registryRequestSeq = v; }
function setRegistryFocusIndex(v){ registryFocusIndex = v; }
function setRegistrySelectionAnchorId(v){ registrySelectionAnchorId = v; }
function setRegistryArchiveBusy(v){ registryArchiveBusy = v; }
function setRegistryPdfBusy(v){ registryPdfBusy = v; }
function setRegistryRecycleBinState(v){ registryRecycleBinState = v; }
function setThumbnailGeneratorState(v){ thumbnailGeneratorState = v; }
function setAssetPreviewAllState(v){ assetPreviewAllState = v; }
function setThumbnailCacheSwitchState(v){ thumbnailCacheSwitchState = v; }
function setThumbnailCacheVersion(v){ thumbnailCacheVersion = v; }
function setActiveProjectId(v){ activeProjectId = v; }
function setRegistryProjectFilterId(v){ registryProjectFilterId = v; }
function setProjectBoard(v){ projectBoard = v; }
function setSelectedProjectEntityId(v){ selectedProjectEntityId = v; }
function setAssetLibrary(v){ assetLibrary = v; }
function setPromptLibrary(v){ promptLibrary = v; }
function setApiProviders(v){ apiProviders = v; }
function setActiveAssetLibraryId(v){ activeAssetLibraryId = v; }
function setActiveAssetCategoryId(v){ activeAssetCategoryId = v; }
function setActiveAssetClassFilter(v){ activeAssetClassFilter = v; }
function setActiveWorkflowLibraryId(v){ activeWorkflowLibraryId = v; }
function setActiveWorkflowCategoryId(v){ activeWorkflowCategoryId = v; }
function setActivePromptLibraryId(v){ activePromptLibraryId = v; }
function setActivePromptCategory(v){ activePromptCategory = v; }
function setSelectedAssetId(v){ selectedAssetId = v; }
function setSelectedWorkflowId(v){ selectedWorkflowId = v; }
function setSelectedPromptId(v){ selectedPromptId = v; }
function setSelectedAssetIds(v){ selectedAssetIds = v; }
function setSelectedWorkflowIds(v){ selectedWorkflowIds = v; }
function setSelectedPromptIds(v){ selectedPromptIds = v; }
function setAssetMoveTarget(v){ assetMoveTarget = v; }
function gwSetAssetClipboard(v){ assetClipboard = v; }
function setAssetEditMode(v){ assetEditMode = v; }
function setPromptEditMode(v){ promptEditMode = v; }
function setPromptCreateMode(v){ promptCreateMode = v; }
function setPendingDeleteAssetId(v){ pendingDeleteAssetId = v; }
function setPendingDeletePromptId(v){ pendingDeletePromptId = v; }
function setPendingBatchDelete(v){ pendingBatchDelete = v; }
function setAssetTreeEdit(v){ assetTreeEdit = v; }
function setWorkflowTreeEdit(v){ workflowTreeEdit = v; }
function setPromptTreeEdit(v){ promptTreeEdit = v; }
function setPendingTreeDelete(v){ pendingTreeDelete = v; }
function setSharedFolders(v){ sharedFolders = v; }
function setActiveSharedFolderName(v){ activeSharedFolderName = v; }
function setLocalItemMap(v){ localItemMap = v; }
function setActiveLocalFolderId(v){ activeLocalFolderId = v; }
function setSelectedLocalId(v){ selectedLocalId = v; }
function setSelectedLocalIds(v){ selectedLocalIds = v; }
function gwSetLocalClipboard(v){ localClipboard = v; }
function setLocalCaptionBusy(v){ localCaptionBusy = v; }
function setLocalCaptionProvider(v){ localCaptionProvider = v; }
function setLocalCaptionModel(v){ localCaptionModel = v; }
function setLocalCaptionPrompt(v){ localCaptionPrompt = v; }
function setLocalClassifyPrompt(v){ localClassifyPrompt = v; }
function setLocalAutoClassify(v){ localAutoClassify = v; }
function setLocalAssets(v){ localAssets = v; }
function setLocalAssetsLoaded(v){ localAssetsLoaded = v; }
function setLocalAssetsTotal(v){ localAssetsTotal = v; }
function setLocalLibraries(v){ localLibraries = v; }
function setActiveLocalLibraryId(v){ activeLocalLibraryId = v; }
function setLocalUploadTree(v){ localUploadTree = v; }
function setActiveLocalUploadFolder(v){ activeLocalUploadFolder = v; }
function setActiveLocalUploadClassFilter(v){ activeLocalUploadClassFilter = v; }
function setOpenLocalUploadClassGroup(v){ openLocalUploadClassGroup = v; }
function setSelectedLocalUploadId(v){ selectedLocalUploadId = v; }
function gwSetLocalUploadClipboard(v){ localUploadClipboard = v; }
function setAssetClassifyBusy(v){ assetClassifyBusy = v; }
function setLocalClassifyBusy(v){ localClassifyBusy = v; }
function setDetailMediaCanvasPanState(v){ detailMediaCanvasPanState = v; }
function setDetailMediaCanvasEditState(v){ detailMediaCanvasEditState = v; }
function setDetailMediaCanvasAnnotationSnapshot(v){ detailMediaCanvasAnnotationSnapshot = v; }
function setDetailMediaCanvasColorSnapshot(v){ detailMediaCanvasColorSnapshot = v; }
function setDetailMediaLibraryDragId(v){ detailMediaLibraryDragId = v; }
function setDetailImageEditorState(v){ detailImageEditorState = v; }
function setInboxItems(v){ inboxItems = v; }
function setInboxSettings(v){ inboxSettings = v; }
function setInboxLoaded(v){ inboxLoaded = v; }
function setInboxSettingsOpen(v){ inboxSettingsOpen = v; }
function setDetailViewerState(v){ detailViewerState = v; }
function setDetailViewerFontExpandedPreference(v){ detailViewerFontExpandedPreference = v; }
function setDetailViewerFontListCollapsed(v){ detailViewerFontListCollapsed = v; }
function setDetailMediaCanvasLibraryCollapsed(v){ detailMediaCanvasLibraryCollapsed = v; }
function setDetailMediaCanvasLibraryThumbSize(v){ detailMediaCanvasLibraryThumbSize = v; }
function gwSetDetailMediaCanvasLibraryWidth(v){ detailMediaCanvasLibraryWidth = v; }
function gwSetDetailMediaCanvasMinimapVisible(v){ detailMediaCanvasMinimapVisible = v; }
function setDetailViewerAudioFrame(v){ detailViewerAudioFrame = v; }
function setAssetHoverPreviewMedia(v){ assetHoverPreviewMedia = v; }
function setCanvasAssetsData(v){ canvasAssetsData = v; }
function setActiveCanvasAssetCategory(v){ activeCanvasAssetCategory = v; }
function setActiveCanvasAssetCanvasId(v){ activeCanvasAssetCanvasId = v; }
function setSelectedCanvasAssetId(v){ selectedCanvasAssetId = v; }
function setSelectedCanvasAssetIds(v){ selectedCanvasAssetIds = v; }
function setAllowedRootSettingsState(v){ allowedRootSettingsState = v; }
function setServerDirectoryBrowserState(v){ serverDirectoryBrowserState = v; }
function setAssetAiSettingsState(v){ assetAiSettingsState = v; }
function setProjectDirectoryTemplateManagerState(v){ projectDirectoryTemplateManagerState = v; }
function setRegistryFullscreenViewerState(v){ registryFullscreenViewerState = v; }
function setGovernanceSuggestionsData(v){ governanceSuggestionsData = v; }
function setGovernanceSuggestionsLoading(v){ governanceSuggestionsLoading = v; }
function setGovernanceSuggestionsError(v){ governanceSuggestionsError = v; }
function setGovernanceDrawerOpen(v){ governanceDrawerOpen = v; }
function setRegistryDiskFolderPermissionDenied(v){ registryDiskFolderPermissionDenied = v; }
function setRegistryDiskFolderPermissionNoticed(v){ registryDiskFolderPermissionNoticed = v; }
function setDetailMediaLutSplitDragEndedAt(v){ detailMediaLutSplitDragEndedAt = v; }
function gwSetDetailMediaCanvasCoverflowVisible(v){ detailMediaCanvasCoverflowVisible = v; }
function setDetailPreviewBackquoteCloseTimer(v){ detailPreviewBackquoteCloseTimer = v; }
function setDetailImageEditorBackquoteCloseTimer(v){ detailImageEditorBackquoteCloseTimer = v; }
function setDetailTextEditorBackquoteInput(v){ detailTextEditorBackquoteInput = v; }

// 【QIN-591】延后域网关：首屏只加载列表与卡片所需代码；以下域模块在首屏绘制后的空闲时段预热，
// 或在首次使用时按需动态加载。域模块加载完成即在 GW_DEFERRED 注册函数面。
const GW_DOMAIN_LOADERS = {
    'settings': () => import('./asset-manager/settings.js'),
    'organize': () => import('./asset-manager/organize.js'),
    'upload': () => import('./asset-manager/upload.js'),
    'media-canvas': () => import('./asset-manager/media-canvas.js'),
    'tags': () => import('./asset-manager/tags.js'),
    'canvas': () => import('./asset-manager/canvas-bridge.js'),
    'coverflow-bridge': () => import('./asset-manager/coverflow-bridge.js'),
    'legacy': () => import('./asset-manager/legacy-pages.js'),
    'preview': () => import('./asset-manager/preview.js'),
    'lut': () => import('./asset-manager/lut.js'),
    'batch': () => import('./asset-manager/batch.js'),
    'recycle': () => import('./asset-manager/recycle.js'),
    'inbox': () => import('./asset-manager/inbox.js'),
    'governance': () => import('./asset-manager/governance.js'),
};
function gwCall(domain, fn, ...args) {
    const api = GW_DEFERRED[domain];
    if (api && typeof api[fn] === 'function') return api[fn](...args);
    (GW_DOMAIN_LOADERS[domain] || Promise.reject(new Error(`gwCall: 未知域 ${domain}`)))().then(m => m[fn](...args)).catch(err => console.error(`gwCall 失败: ${domain}/${fn}`, err));
}
function gwLoad(domain) {
    const api = GW_DEFERRED[domain];
    if (api) return Promise.resolve(api);
    return (GW_DOMAIN_LOADERS[domain] || Promise.reject(new Error(`gwLoad: 未知域 ${domain}`)))();
}

// 【QIN-591】延后域模块所需主文件符号与跨文件状态写通道
export {
    ALLOWED_ROOT_FIELD_KEY,
    ASSET_BROWSER_PREVIEW_EXTENSIONS,
    ASSET_CLASS_GROUPS,
    ASSET_DETAIL_FONT_LIST_COLLAPSED_KEY,
    ASSET_DETAIL_FONT_PANEL_KEY,
    ASSET_DETAIL_INSPECTOR_COLLAPSED_KEY,
    ASSET_DETAIL_MEDIA_COVERFLOW_VISIBLE_KEY,
    ASSET_DETAIL_MEDIA_LIBRARY_COLLAPSED_KEY,
    ASSET_DETAIL_MEDIA_LIBRARY_SIZE_KEY,
    ASSET_DETAIL_MEDIA_LIBRARY_WIDTH_KEY,
    ASSET_DETAIL_MEDIA_MINIMAP_VISIBLE_KEY,
    ASSET_FONT_PREVIEW_EN,
    ASSET_FONT_PREVIEW_LETTERS_LOWER,
    ASSET_FONT_PREVIEW_LETTERS_UPPER,
    ASSET_FONT_PREVIEW_NUMBERS,
    ASSET_FONT_PREVIEW_PUNCTUATION,
    ASSET_FONT_PREVIEW_PUNCTUATION_ZH,
    ASSET_FONT_PREVIEW_ZH,
    ASSET_FONT_PREVIEW_ZH_LINE,
    ASSET_LIBRARY_FOLDER_COLLAPSED_KEY,
    ASSET_LIBRARY_FOLDER_HIDDEN_KEY,
    ASSET_LIBRARY_PRESETS,
    ASSET_ORGANIZE_UNDO_LIMIT,
    ASSET_RACK_COLLAPSED_KEY,
    ASSET_RACK_EXPANDED_KEY,
    ASSET_RACK_KNOWN_ROOTS_KEY,
    ASSET_RACK_ROOT_KEY,
    ASSET_REGISTRY_CUSTOM_GROUPS_KEY,
    ASSET_REGISTRY_DETAIL_COLLAPSED_KEY,
    ASSET_REGISTRY_DETAIL_WIDTH_KEY,
    ASSET_REGISTRY_RECYCLE_CONFIRM_KEY,
    ASSET_REGISTRY_RECYCLE_MODE_KEY,
    ASSET_REGISTRY_RECYCLE_SETUP_KEY,
    ASSET_REGISTRY_VIEW_SETTINGS_KEY,
    AVATAR_PLATFORM_LABELS,
    AVATAR_SUPPORTED_PLATFORMS,
    BOARD_CREATE_PATH_FIELD_KEY,
    CLIENT_SETTING_KEY_UPLOAD_SOURCE,
    DEFAULT_LOCAL_LIBRARY_NAME,
    DETAIL_BACKQUOTE_DOUBLE_TAP_MS,
    DETAIL_LUT_SAMPLE_HEIGHT,
    DETAIL_LUT_SAMPLE_WIDTH,
    DETAIL_MEDIA_CANVAS_GROUP_COLORS,
    DETAIL_MEDIA_LUT_VIDEO_WIDTH,
    GOVERNANCE_INFORMATIONAL_KINDS,
    GOVERNANCE_SUGGESTION_GROUPS,
    GOVERNANCE_SUGGESTION_KIND_LABELS,
    INBOX_KIND_LABELS,
    INBOX_SOURCE_LABELS,
    LOCAL_CAPTION_SETTINGS_KEY,
    LOCAL_MEDIA_EXTS,
    LUT_THUMB_CONCURRENCY,
    LUT_THUMB_SAMPLE_HEIGHT,
    LUT_THUMB_SAMPLE_WIDTH,
    OPTIONAL_DIRECTORY_FIELDS,
    PROJECT_DIRECTORY_TEMPLATE_FILE_SUFFIXES,
    PROJECT_DIRECTORY_TEMPLATE_RESERVED_NAMES,
    PROJECT_DIRECTORY_TEMPLATE_TYPES,
    SIGNAL_FLOW_ACCENT_FALLBACK,
    SMART_CANVAS_ASSET_INBOX_KEY,
    STORAGE_KIND_LABELS,
    THUMBNAIL_CACHE_BLOCK_CODES,
    THUMBNAIL_CACHE_EXCLUSION_HINT,
    THUMBNAIL_DIRECTORY_FIELD_KEY,
    VERSION_CONFLICT_MESSAGE,
    VIDEO_STORYBOARD_IMAGE_STYLE,
    VIDEO_STORYBOARD_LARGE_STYLE,
    activeAssetCategory,
    activeAssetCategoryId,
    activeAssetClassEntry,
    activeAssetClassFilter,
    activeAssetLibrary,
    activeAssetLibraryId,
    activeCanvasAssetCanvas,
    activeCanvasAssetCanvasId,
    activeCanvasAssetCategory,
    activeCanvasAssetCategoryInfo,
    activeLocalFolderId,
    activeLocalLibrary,
    activeLocalLibraryId,
    activeLocalLibraryKind,
    activeLocalLibraryName,
    activeLocalUploadClassEntry,
    activeLocalUploadClassFilter,
    activeLocalUploadFolder,
    activeProjectId,
    activePromptCategories,
    activePromptCategory,
    activePromptLibrary,
    activePromptLibraryId,
    activeRegistryVisibleAssets,
    activeSharedFolderName,
    activeTab,
    activeWorkflowCategory,
    activeWorkflowCategoryId,
    activeWorkflowLibrary,
    activeWorkflowLibraryId,
    alignTimeToFps,
    allAssetBoards,
    allowedRootSettingsState,
    allowedRootsRemovedModalOpen,
    apiJsonResponse,
    apiProviders,
    applyRegistryAssetResponse,
    applyRegistryFilterDefinition,
    assetAiSettingsState,
    assetAllowedRootPaths,
    assetBoardById,
    assetCategories,
    assetClassifyBusy,
    assetClipboard,
    assetEditMode,
    assetFileExtension,
    assetHoverPreviewMedia,
    assetInboxUnreadCount,
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
    assetPreviewUrl,
    assetQuery,
    assetRackAutomaticItems,
    assetRackBoard,
    assetRackBoardPool,
    assetRackCollapsed,
    assetRackExpandedFolders,
    assetRackFolderTree,
    assetRackRootDiskPath,
    assetRackRoots,
    assetTabsEl,
    assetThumb,
    assetTreeExpanded,
    assetTreeSelectedKey,
    assetVideoUrl,
    avatarRegisterProvider,
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
    canManageAllowedRoots,
    canManageProjectWorkspaceSettings,
    canvasAssetCategories,
    canvasAssetCountForCanvas,
    canvasAssetManageMode,
    canvasAssetQuery,
    canvasAssetSort,
    canvasAssetViewTitle,
    canvasAssetsData,
    canvasAssetsForCategory,
    canvasKindLabel,
    clientSettingBusy,
    clientUploadSourceDir,
    closeAssetOrganizeMenu,
    closeDetailPreview,
    closeRegistrySelectMenus,
    closeRegistryStructureMenu,
    closeRegistryVersionStructureRemoveDialog,
    confirmRegistryVersionStructureRemoval,
    copyTextToClipboard,
    currentStatusText,
    customAssetLibraryBaseItems,
    customAssetLibraryItems,
    customBoardFolderIsHidden,
    customBoardFolderParentPaths,
    customBoardFolderRootIds,
    customBoardFolderTree,
    defaultCanvasAssetCategory,
    detailActiveImageItem,
    detailImageEditorBackquoteCloseTimer,
    detailImageEditorState,
    detailImageVersionAssetId,
    detailMediaCanvasAnnotationSnapshot,
    detailMediaCanvasAnnotations,
    detailMediaCanvasColorSnapshot,
    detailMediaCanvasCoverflowVisible,
    detailMediaCanvasDrawings,
    detailMediaCanvasEditState,
    detailMediaCanvasExpandedObjectIds,
    detailMediaCanvasGroup,
    detailMediaCanvasGroupForMember,
    detailMediaCanvasGroups,
    detailMediaCanvasItems,
    detailMediaCanvasLibraryCollapsed,
    detailMediaCanvasLibraryRootItemsCache,
    detailMediaCanvasLibraryThumbSize,
    detailMediaCanvasLibraryWidth,
    detailMediaCanvasMinimapVisible,
    detailMediaCanvasObjectIds,
    detailMediaCanvasPanState,
    detailMediaCanvasPersistId,
    detailMediaCanvasRawObjectIds,
    detailMediaCanvasSelectedCoverflowAssets,
    detailMediaCanvasSelectedIds,
    detailMediaCanvasSelectionObjectId,
    detailMediaCanvasSelectionOrder,
    detailMediaCanvasSpacePressed,
    detailMediaLibraryDragId,
    detailMediaLutCache,
    detailMediaLutDraw,
    detailMediaLutListCache,
    detailMediaLutSamples,
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
    directoryFieldInput,
    directorySettingsError,
    directorySettingsPage,
    directorySettingsReady,
    directorySettingsSaving,
    ensureAssetTreeProjectData,
    ensureBoardNavigationState,
    ensureBuiltinLutsRegistered,
    escapeAttr,
    escapeHtml,
    findAssetItem,
    findCanvasAssetItem,
    findLocalItem,
    findLocalUpload,
    findPromptItem,
    findWorkflowItem,
    formatMediaDuration,
    governanceDrawerOpen,
    governanceSuggestionTotal,
    governanceSuggestionsData,
    governanceSuggestionsError,
    governanceSuggestionsLoading,
    governanceTriggerLabel,
    gwSetAssetClipboard,
    gwSetAssetRackCollapsed,
    gwSetDetailMediaCanvasCoverflowVisible,
    gwSetDetailMediaCanvasLibraryWidth,
    gwSetDetailMediaCanvasMinimapVisible,
    gwSetLocalClipboard,
    gwSetLocalUploadClipboard,
    handleClick,
    inboxItems,
    inboxLoaded,
    inboxQuery,
    inboxSettings,
    inboxSettingsOpen,
    inboxSourceFilter,
    isRegistryBootstrapPending,
    isSystemPromptLibrary,
    isTauriClientMode,
    loadAll,
    loadAssetAllowedRootPaths,
    loadDetailImageVersions,
    loadLocalAssets,
    loadMoreRegistryAssets,
    loadRegistryPrincipal,
    localAssets,
    localAssetsError,
    localAssetsHasMore,
    localAssetsLoaded,
    localAssetsLoading,
    localAssetsTotal,
    localAutoClassify,
    localCaptionBusy,
    localCaptionModel,
    localCaptionPrompt,
    localCaptionProvider,
    localClassifyBusy,
    localClassifyPrompt,
    localClassifyPromptOpen,
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
    lutPreviewIsAsset,
    lutPreviewSampleCache,
    lutPreviewSourceCache,
    lutPreviewSourceUrl,
    markIndexState,
    mergeRegistryAssetUpdate,
    normalizeRegistryAsset,
    notifyShellTreeSnapshot,
    openLocalUploadClassGroup,
    openRegistryAssetFromGlobalSearch,
    openRegistryStructureCoverflow,
    openRegistryStructureReferenceCanvas,
    openRegistryStructureRemoveDialog,
    parseRegistryTagNames,
    pendingBatchDelete,
    pendingDeleteAssetId,
    pendingDeletePromptId,
    pendingPurgeRoot,
    pendingTreeDelete,
    projectBoard,
    projectDirectoryTemplateManagerState,
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
    registryAssetCategory,
    registryAssetExtension,
    registryAssetFolderPath,
    registryAssetMatchesFolder,
    registryAssetPrimaryPath,
    registryAssetStructures,
    registryAssets,
    registryAssetsQueryCache,
    registryBoardCustomGroups,
    registryBoardGroupKey,
    registryCanRecycle,
    registryCanvasLoading,
    registryCardSize,
    registryCategoryCount,
    registryCollectionId,
    registryCoverflowAssets,
    registryCoverflowAssetsForItems,
    registryCoverflowCurrentAssetId,
    registryCoverflowSourceIsVisual,
    registryCoverflowSourceMediaKind,
    registryCustomGroups,
    registryDetail,
    registryDetailPanelCollapsed,
    registryDetailPanelWidth,
    registryDiskFolderCache,
    registryDiskFolderLoading,
    registryDiskFolderPermissionDenied,
    registryDiskFolderPermissionNoticed,
    registryExcludeTags,
    registryFacetKindCount,
    registryFacets,
    registryFilterDefinition,
    registryFullscreenViewerState,
    registryGroupMode,
    registryGroupSimilar,
    registryGroupStructureMembers,
    registryHasMoreAssets,
    registryIncludeTags,
    registryIndexedFolders,
    registryKind,
    registryKindLabel,
    registryLayout,
    registryLoading,
    registryLutThumbQueue,
    registryModifiedLabel,
    registryNavFolder,
    registryNavFolderActive,
    registryPageHasMore,
    registryPdfBusy,
    registryPresets,
    registryPrincipal,
    registryPrincipalLoading,
    registryProjectFilterId,
    registryQueryString,
    registryRackSnapshot,
    registryRecentDays,
    registryRecycleBinState,
    registryRegisteredFoldersLoaded,
    registryRequestSeq,
    registryRootFolders,
    registryRootFoldersLoading,
    registryRootId,
    registrySelectedIds,
    registrySimple,
    registrySort,
    registryStatus,
    registryStructureIsRenderable,
    registryStructureMemberAsset,
    registryStructureMenu,
    registryTotal,
    registryTotalKnown,
    registryVisibleAssetList,
    registryVisibleAssets,
    registryVisualType,
    reindexAssetSource,
    render,
    renderAssetTreePanel,
    renderBoardNavigation,
    renderDetailVideoSegments,
    renderDetailVideoStoryboardPanel,
    renderDetailVideoSummary,
    renderRegistryAssetSections,
    renderRegistryDetail,
    renderRegistryEditableTags,
    renderRegistryFacetRow,
    renderRegistryFilterChips,
    renderRegistryManager,
    renderRegistryPagination,
    renderRegistryProjectFacetSection,
    renderRegistryRelations,
    renderRegistrySelectionBar,
    renderRegistryToolbar,
    requireExpectedVersion,
    resetRegistryScroll,
    root,
    runRegistryStructureAction,
    saveAllowedRootsRequest,
    saveAssetTreeState,
    saveBoardNavigationState,
    saveStorageSettings,
    savedScrollPositions,
    scheduleAssetTouchbarReport,
    scheduleRegistryMasonryLayout,
    scriptArchiveItems,
    seekDetailVideoTo,
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
    setActiveCanvasAssetCanvasId,
    setActiveCanvasAssetCategory,
    setActiveLocalFolderId,
    setActiveLocalLibraryId,
    setActiveLocalUploadClassFilter,
    setActiveLocalUploadFolder,
    setActiveProjectId,
    setActivePromptCategory,
    setActivePromptLibraryId,
    setActiveSharedFolderName,
    setActiveTab,
    setActiveWorkflowCategoryId,
    setActiveWorkflowLibraryId,
    setAllowedRootSettingsState,
    setApiProviders,
    setAssetAiSettingsState,
    setAssetClassifyBusy,
    setAssetEditMode,
    setAssetHoverPreviewMedia,
    setAssetLibrary,
    setAssetMoveTarget,
    setAssetOrganizeDialog,
    setAssetOrganizeDrag,
    setAssetOrganizeDropHint,
    setAssetPreviewAllState,
    setAssetTreeEdit,
    setAssetTreeSelectedKey,
    setAssetTreeTab,
    setBoardCreateFocusTarget,
    setBoardCreateState,
    setBoardEditState,
    setBoardFolderCreateState,
    setBoardSourceState,
    setCanvasAssetsData,
    setClientSettingBusy,
    setClientUploadSourceDir,
    setDetailImageEditorBackquoteCloseTimer,
    setDetailImageEditorState,
    setDetailMediaCanvasAnnotationSnapshot,
    setDetailMediaCanvasColorSnapshot,
    setDetailMediaCanvasEditState,
    setDetailMediaCanvasLibraryCollapsed,
    setDetailMediaCanvasLibraryThumbSize,
    setDetailMediaCanvasPanState,
    setDetailMediaLibraryDragId,
    setDetailMediaLutSplitDragEndedAt,
    setDetailPreviewBackquoteCloseTimer,
    setDetailTextEditorBackquoteInput,
    setDetailViewerAudioFrame,
    setDetailViewerFontExpandedPreference,
    setDetailViewerFontListCollapsed,
    setDetailViewerState,
    setDirectorySettingsSaving,
    setGovernanceDrawerOpen,
    setGovernanceSuggestionsData,
    setGovernanceSuggestionsError,
    setGovernanceSuggestionsLoading,
    setInboxItems,
    setInboxLoaded,
    setInboxSettings,
    setInboxSettingsOpen,
    setLocalAssets,
    setLocalAssetsLoaded,
    setLocalAssetsTotal,
    setLocalAutoClassify,
    setLocalCaptionBusy,
    setLocalCaptionModel,
    setLocalCaptionPrompt,
    setLocalCaptionProvider,
    setLocalClassifyBusy,
    setLocalClassifyPrompt,
    setLocalItemMap,
    setLocalLibraries,
    setLocalUploadTree,
    setOpenLocalUploadClassGroup,
    setPendingBatchDelete,
    setPendingDeleteAssetId,
    setPendingDeletePromptId,
    setPendingTreeDelete,
    setProjectBoard,
    setProjectDirectoryTemplateManagerState,
    setPromptCreateMode,
    setPromptEditMode,
    setPromptLibrary,
    setPromptTreeEdit,
    setRegistryArchiveBusy,
    setRegistryAssets,
    setRegistryBulkPanel,
    setRegistryCanvasLoading,
    setRegistryCanvasTargets,
    setRegistryCoverflowBoardId,
    setRegistryCoverflowCurrentAssetId,
    setRegistryDetail,
    setRegistryDetailPanelCollapsed,
    setRegistryDiskFolderPermissionDenied,
    setRegistryDiskFolderPermissionNoticed,
    setRegistryFocusIndex,
    setRegistryFullscreenViewerState,
    setRegistryGroupMode,
    setRegistryIndexedFolders,
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
    setRegistryRecycleBinState,
    setRegistryRegisteredFolders,
    setRegistryRegisteredFoldersLoaded,
    setRegistryRequestSeq,
    setRegistryRootId,
    setRegistrySelectionAnchorId,
    setRegistryStatCategory,
    setRegistryStatus,
    setRegistryTagEditorAssetId,
    setRegistryTotal,
    setRegistryTotalKnown,
    setRegistryVersionStructureCurrent,
    setSelectedAssetId,
    setSelectedAssetIds,
    setSelectedCanvasAssetId,
    setSelectedCanvasAssetIds,
    setSelectedLocalId,
    setSelectedLocalIds,
    setSelectedLocalUploadId,
    setSelectedProjectEntityId,
    setSelectedPromptId,
    setSelectedPromptIds,
    setSelectedRegistryAssetId,
    setSelectedWorkflowId,
    setSelectedWorkflowIds,
    setServerDirectoryBrowserState,
    setSharedFolders,
    setStatus,
    setThumbnailCacheSwitchState,
    setThumbnailCacheVersion,
    setThumbnailGeneratorState,
    setWorkflowTreeEdit,
    sharedFolders,
    showAssetFailure,
    showAssetNotice,
    storageCleanName,
    storageExtraDefaultName,
    storageExtraLocalEntries,
    storageLocalLibraryNames,
    storageSettingsState,
    syncRegistrySelectionDom,
    syncRegistrySelectionViews,
    tauriInvokeFn,
    thumbnailCacheSwitchState,
    thumbnailGeneratorState,
    toggleSelectionSet,
    uniqueCanvasAssets,
    uploadInput,
    videoStoryboardImageCache,
    visibleAssetBoards,
    waitForAssetIndexJob,
    workflowCategories,
    workflowDeleteBusy,
    workflowDeleteVersions,
    workflowKindLabel,
    workflowManageMode,
    workflowProvenanceLinks,
    workflowQuery,
    workflowThumb,
    workflowTreeEdit,
};
