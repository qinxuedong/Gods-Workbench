Y™Áäx-ÆÈ‹j◊ù¢Îi∫⁄+äßj[hëÈ‹¢ÈÌ„^6ﬂû¥Û~∂o+^≤â¢∂◊ù/**
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
            console.warn('[AssetManager] Âä®ÊÄÅÂä†ËΩΩ media-preview-player.js Â§±Ë¥•:', err);
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
// QIN-432ÔºöTauri ÂÆ¢Êà∑Á´ØÊ®°Âºè‰∏ãÁöÑÊú¨Êú∫ÈÖçÁΩÆÔºàÂÆ¢Êà∑Á´Ø‰∏ä‰º†Êù•Ê∫êÁõÆÂΩïÔºâ„ÄÇ
const CLIENT_SETTING_KEY_UPLOAD_SOURCE = 'upload_source_dir';
let clientUploadSourceDir = '';
let clientSettingBusy = false;
function tauriInvokeFn(){
    // ÁõÆÂΩïËÆæÁΩÆÂèØËÉΩËøêË°åÂú® iframe ÂÜÖÔºõTauri Ê≥®ÂÖ•‰∏éÂÆø‰∏ªÂêåÊ∫êÔºåtop ‰∏äÂêåÊ†∑ÂèØÁî®„ÄÇ
    return window.__TAURI__?.core?.invoke || window.top?.__TAURI__?.core?.invoke || null;
}
function isTauriClientMode(){ return Boolean(tauriInvokeFn()); }
async function loadClientUploadSourceDir(){
    const invoke = tauriInvokeFn();
    if(!invoke) return;
    try {
        const value = await invoke('get_client_setting', {key: CLIENT_SETTING_KEY_UPLOAD_SOURCE});
        clientUploadSourceDir = String(value || '');
    } catch(error){ clientUploadSourceDir = ''; }
}
async function saveClientUploadSourceDir(pathValue){
    const invoke = tauriInvokeFn();
    if(!invoke || clientSettingBusy) return;
    clientSettingBusy = true;
    try {
        await invoke('set_client_setting', {key: CLIENT_SETTING_KEY_UPLOAD_SOURCE, value: String(pathValue || '')});
        clientUploadSourceDir = String(pathValue || '');
        setStatus(clientUploadSourceDir ? 'ÂÆ¢Êà∑Á´ØÊù•Ê∫êÁõÆÂΩïÂ∑≤‰øùÂ≠òÂà∞Êú¨Êú∫' : 'ÂÆ¢Êà∑Á´ØÊù•Ê∫êÁõÆÂΩïÂ∑≤Ê∏ÖÁ©∫');
    } catch(error){
        setStatus(error?.message || 'ÂÆ¢Êà∑Á´ØËÆæÁΩÆ‰øùÂ≠òÂ§±Ë¥•');
    } finally {
        clientSettingBusy = false;
    }
}
const SIGNAL_FLOW_ACCENT_FALLBACK = '#69d6ff';

function signalFlowAccentColor(){
    const color = getComputedStyle(document.documentElement).getPropertyValue('--sf-cyan').trim();
    return /^#[0-9a-f]{6}$/i.test(color) ? color : SIGNAL_FLOW_ACCENT_FALLBACK;
}

let currentStatusText = 'ÂáÜÂ§áÂ∞±Áª™';
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
function writeLocalCaptionSettings(){
    try {
        localStorage.setItem(LOCAL_CAPTION_SETTINGS_KEY, JSON.stringify({
            provider:localCaptionProvider || '',
            model:localCaptionModel || '',
            captionPrompt:localCaptionPrompt || 'ÊèèËø∞ÂõæÁâá',
            classifyPrompt:localClassifyPrompt || '',
            autoClassify:Boolean(localAutoClassify)
        }));
    } catch(_) {}
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
                name:String(group.name || 'Ëá™ÂÆö‰πâÂàÜÁªÑ').slice(0, 40),
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
let boardDragId = '';
let boardCreateState = {open:false, preset:'other', name:'', nameManuallyEdited:false, path:'', saving:false, error:''};
let boardCreateReturnFocus = null;
let boardCreateFocusTarget = '';
let boardEditState = {open:false, boardId:'', name:'', path:'', saving:false};
let boardSourceState = {open:false, boardId:'', editor:'', existingId:'', name:'Áî®Êà∑Á¥†Êùê', path:''};
let boardFolderCreateState = {open:false, boardId:'', rootId:'', parent:'', name:'Êñ∞Êñá‰ª∂Â§π', saving:false, error:''};
let boardSourceSelection = {};
let boardFolderSelection = {};
let boardGroupSelection = {};
let boardReferenceCanvasSelection = {};
let boardCollapsedFolders = readCollapsedLibraryFolders();
let boardHiddenFolders = readHiddenLibraryFolders();
const registryRootFolders = new Map();
const assetReindexRequests = new Map();
const registryRootFoldersLoading = new Set();
let registryAssets = [];
// ÁªìÊûÑÊï∞ÊçÆÂè™ÁºìÂ≠òÊúçÂä°Á´ØËøîÂõûÁöÑÁ®≥ÂÆö structure_id ÊäïÂΩ±Ôºå‰∏ç‰Ωú‰∏∫ËµÑ‰∫ßÊàñÁâàÊú¨ÁúüÊ∫ê„ÄÇ
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
let promptItemSnapshot = {revision:0, items:[]};
let casRefreshInFlight = false;
const VERSION_CONFLICT_MESSAGE = 'ÂÜÖÂÆπÂ∑≤ÂèòÂåñÔºåËØ∑Âà∑Êñ∞';
let registryAutoPollTimer = 0;
let registryAutoPollBusy = false;
let registryObservedJobKey = '';
// „ÄêË∑≥È°∂Áªà‰øÆ„ÄëÁ¥¢ÂºïÂÆåÊàêÂØπË¥¶Â∑≤Êîπ‰∏∫ DOM ÂÆâÂÖ®ÁöÑÈùôÈªòÂêàÂπ∂ÔºàËßÅ reconcileRegistryIndexChangesÔºâÔºå‰∏çÂÜçÈúÄË¶Å‰∫§‰∫íÊúüÂª∂ËøüÊú∫Âà∂
let registryReconcilePageIds = new Set(); // ÊúÄËøë‰∏ÄÊ¨°Â¢ûÈáèÂØπË¥¶ËøîÂõûÁöÑÂΩìÂâçÈ°µ asset_id ÈõÜÂêà
let registryPendingNewIds = new Set(); // „Äê‰øÆÂ§çË∑≥È°∂„ÄëÂØπË¥¶ÂèëÁé∞ÁöÑÊñ∞Á¥†ÊùêÂæÖÁúãÁºìÂÜ≤Ôºå‰∏çËá™Âä®ÂÖ•ÁΩëÊ†º
let registryPendingVanishedIds = new Set(); // „Äê‰øÆÂ§çË∑≥È°∂„ÄëÂØπË¥¶ÂèëÁé∞ÁöÑÊ∂àÂ§±Á¥†ÊùêÁºìÂÜ≤Ôºå‰∏çËá™Âä®ÊäΩÂç°
// „ÄêËΩªÈáèÂ¢ûÈáèÂêåÊ≠•„ÄëÂ£≥Â±Ç asset-syncÔºàÂàáÂõûÈ°µÁ≠æÔºâËØ≠‰πâÔºöÂàáÂõûÈ°µÁ≠æ=ËΩªÈáèÂ¢ûÈáèÂêåÊ≠•ÔºõÊñ∞Á¥†ÊùêËá™Âä®ÊèíÂÖ•‰ΩÜ‰∏çÂä®Â∑≤Âä†ËΩΩÂÜÖÂÆπÔºõ
// ÂÖ®ÈáèÂà∑Êñ∞ÔºàÈáçÊãâÊï∞ÊçÆ/Ê∏ÖÁ©∫ÈÄâÊã©/ÂàÜÈ°µ‰∏éÁ≠õÈÄâÈáçÁΩÆÔºâ‰ªçÂè™Â±û‰∫éÁî®Êà∑ÊòæÂºèËß¶ÂèëÁöÑ asset-refresh ‰∏é [data-refresh-assets]„ÄÇ
const registryPendingNewItems = new Map(); // ÂæÖÂâçÊèíÊñ∞Á¥†ÊùêÁöÑÂÆåÊï¥ÂØπË±°ÁºìÂÜ≤Ôºàid -> normalized itemÔºâÔºåÁî±ÂØπË¥¶ÊöÇÂ≠ò„ÄÅapplyPendingRegistryAssets Ê∂àË¥π
let registryBootstrapCompleted = false; // È¶ñÂ±èÂºïÂØºÊòØÂê¶ÂÆåÊàêÔºõÊú™ÂÆåÊàê‰∏îÁΩëÊ†ºÊó†Êï∞ÊçÆÊó∂ asset-sync ÂÖúÂ∫ïÊâßË°å loadAll()
let registrySyncBusy = false; // asset-sync Â§ÑÁêÜÈáçÂÖ•‰øùÊä§
let registryPendingApplyScrollBound = null; // ÊªöÂõûÈ°∂ÈÉ®Êó∂Ëá™Âä®Ë°•ÊèíÊñ∞Á¥†ÊùêÁöÑ‰∏ÄÊ¨°ÊÄßÊªöÂä®ÁõëÂê¨
let registryPresets = [];
let registryTotal = null;
let registryTotalKnown = false;
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
let registryDetailPanelResizeState = null;
let registryManageMode = false;
// Similar-asset stacking was removed; keep the persisted field ignored for old sessions.
let registryGroupSimilar = false;
// È¢ÑËßàÂ§ßÂ∞èÊîπ‰∏∫ËøûÁª≠Ê°£ÔºöShift+ÊªöËΩÆË∞ÉËäÇÔºàpxÔºâÔºåÊóßÁöÑ Â∞è/‰∏≠/Â§ß Á¶ªÊï£Ê°£Êåâ‰∏≠ÂÄºËøÅÁßª„ÄÇ
const REGISTRY_CARD_SIZE = { min: 120, max: 360, fallback: 176, presets: { small: 126, medium: 176, large: 244 } };
let registryCardSize = (() => {
    const stored = Number(savedRegistryViewSettings.cardSize);
    if(Number.isFinite(stored) && stored > 0) return Math.max(REGISTRY_CARD_SIZE.min, Math.min(REGISTRY_CARD_SIZE.max, Math.round(stored)));
    return REGISTRY_CARD_SIZE.presets[savedRegistryViewSettings.size] || REGISTRY_CARD_SIZE.fallback;
})();
// The unified "ÊñπÊ†º" control starts in original-ratio mode and toggles to square on click.
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
// „ÄêÊµèËßàÈ¢ÑÂä†ËΩΩ„Äë‰∏ã‰∏ÄÈ°µÊï∞ÊçÆÈ¢ÑÂèñÁä∂ÊÄÅÔºö‰∏é registryRequestSeq Áõ∏‰∫íÁã¨Á´ãÔºå
// È¢ÑÂèñÂè™ÂÜô registryAssetsQueryCacheÔºå‰∏çÊé®ËøõÂàÜÈ°µÊ∏∏Ê†á„ÄÅ‰∏çËß¶ÂèëÊ∏≤Êüì„ÄÇ
let registryPrefetchInFlight = false; // È¢ÑÂèñÂéªÈáçÂÆàÂç´ÔºàÁã¨Á´ã‰∫é registryRequestSeqÔºâ
let registryPrefetchQueryKey = '';    // ËøõË°å‰∏≠È¢ÑÂèñÁöÑ queryKeyÔºå‰æõ loadMore ÂÖ±‰∫´Âêå‰∏ÄËØ∑Ê±Ç
let registryPrefetchPromise = null;   // ËøõË°å‰∏≠È¢ÑÂèñÁöÑ PromiseÔºåloadMore ÂëΩ‰∏≠Êó∂Áõ¥Êé•Á≠âÂæÖÔºåÈÅøÂÖçÈáçÂ§çËØ∑Ê±Ç
let registryPrefetchTimer = 0;        // Á©∫Èó≤Ë∞ÉÂ∫¶Âè•ÊüÑÔºàrequestIdleCallback ÂõûÈÄÄ setTimeoutÔºâ
let registryPrefetchTimerIdle = false;// Âè•ÊüÑÁ±ªÂûãÊ†áËÆ∞ÔºåÂèñÊ∂àÊó∂ÈÄâÊã©ÂØπÂ∫î API
let registryFocusIndex = -1;
let registryFocusRestoreId = '';
let registrySelectionAnchorId = '';
let registryArchiveBusy = false;
let registryPdfBusy = false;
let registryFeatureBusyId = '';
let registryRecycleState = {open:false, assetId:'', busy:false, dontAsk:false, error:''};
let registryRecycleBinState = {open:false, loading:false, restoringId:'', items:[], error:''};
const registryAssetsQueryCache = new Map();
const REGISTRY_PAGE_SIZE = 100;
// Asset-structure discovery must stay bounded as the user scrolls a large
// library.  Appended pages query only their newly seen IDs, one API window at
// a time, instead of re-requesting every previously loaded card in parallel.
const REGISTRY_STRUCTURE_QUERY_CHUNK_SIZE = 500;
const REGISTRY_CACHE_MAX_SIZE = 20; // „Äê‰ºòÂåñÈóÆÈ¢ò1„ÄëÈôêÂà∂ÁºìÂ≠òÂ§ßÂ∞èÔºåÈÅøÂÖçÂÜÖÂ≠òËÜ®ËÉÄ
const REGISTRY_PREVIEW_ROOT_MARGIN = '320px 0px';
const REGISTRY_PREVIEW_FALLBACK_LIMIT = 24;
const REGISTRY_PREVIEW_CONCURRENCY = 4;
const REGISTRY_VIRTUAL_WINDOW_SIZE = 120;
const REGISTRY_VIRTUAL_OVERSCAN_ROWS = 3;
const REGISTRY_VIRTUAL_GAP = 10;
const REGISTRY_PREVIEW_PLACEHOLDER = 'data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///ywAAAAAAQABAAACAUwAOw==';
let registryPreviewQueue = [];
let registryPreviewActive = 0;
let registryPreviewQueueRevision = 0;
let registryPreviewObserver = null;
let registryPreviewFallbackCleanup = null;
let registryVirtualScrollCleanup = null;
let registryVirtualScrollFrame = 0;
let registryVirtualState = {key:'', start:0, end:0, cols:1, totalHeight:0, rowTops:[], rowHeights:[], itemLayouts:[]};
let savedScrollPositions = {registry: 0, assets: 0, workflows: 0, prompts: 0, local: 0, canvas: 0}; // „Äê‰øÆÂ§çÈóÆÈ¢ò4„Äë‰øùÂ≠òÂêÑ‰∏™Ê†áÁ≠æÈ°µÁöÑÊªöÂä®‰ΩçÁΩÆ
let registryPreviewScheduleRevision = 0;
let registryMasonryObserver = null;
let registryMasonryScheduleRevision = 0;
let thumbnailGeneratorState = {open:false, loading:false, generating:false, deleting:false, deletingStoryboards:false, submittingBackground:false, backgroundTaskId:'', mode:'source', directory:'', cacheFolder:'god-cache', transcodeVideos:true, error:''};
let thumbnailCacheVersion = 0; // „Äê‰øÆÂ§çÈóÆÈ¢ò1„Äë‰ªÖ‰Ωú‰∏∫Êó† asset_id ÁöÑÊóßÁâà URL ÁöÑ‰ºöËØùÁ∫ßÁâàÊú¨Ôºå‰ªªÂä°ÂÆåÊàê‰∏çÂÜçÂÖ®Â±ÄÂèòÂåñ
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
let avatarBusyId = '';
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
let activeSharedFolderId = '';
let activeSharedFolderName = '';
let localFolders = [];
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
let localCaptionPrompt = savedLocalCaptionSettings.captionPrompt || 'ÊèèËø∞ÂõæÁâá';
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
let detailMediaCanvasEditState = null;
let detailMediaCanvasAnnotationSnapshot = null;
let detailMediaCanvasColorSnapshot = null;
let detailMediaLibraryDragId = '';
let detailImageEditorState = null;
let detailImageEditorOpeningPromise = null;
let detailImageEditorOpeningItemId = '';
let detailImageEditorPointer = null;
let assetNoticeTimer = 0;
let detailViewerState = {
    open:false, source:'', id:'', index:0, items:[], text:'', textLoading:false, textError:'',
    textEditing:false, textTruncated:false, textVersions:[], textVersionsLoading:false,
    selectedTextVersion:null, textVersionEditing:false, textVersionError:'',
    modelInfo:null, modelInfoLoading:false, modelInfoError:'', wheelAt:0, audioZoom:1,
    fontScale:1, fontFit:1, fontLineWidth:0, fontExpanded:'', fontListScrollTop:0,
    mediaCanvasItems:[], mediaCanvasActiveId:'', mediaCanvasSelectedIds:new Set(), mediaCanvasSelectionOrder:[], mediaCanvasLayout:{},
    mediaCanvasAnnotations:[], mediaCanvasDrawings:[], mediaCanvasGroups:[], mediaCanvasContextMenu:null,
    mediaCanvasEditingAnnotationId:'', mediaCanvasHistory:[], mediaCanvasSnap:true, mediaCanvasLibraryVisible:false,
    mediaCanvasNormalizeBasis:'first', mediaCanvasExportScope:'all', mediaCanvasLibrarySelectedId:'',
    mediaCanvasLibraryQuery:'', mediaCanvasLibrarySort:'newest', mediaCanvasLibraryScope:'all', mediaCanvasLibraryStorageRootId:'all', mediaCanvasLibraryScrollTop:0, mediaCanvasSidebarView:'detail',
    mediaCanvasScale:1, mediaCanvasX:0, mediaCanvasY:0, mediaCanvasFitted:false,
    videoRanges:{}, videoExportRootId:'', videoExportBusy:'', videoStoryboardExpandedId:'',
    imageVersions:[],imageVersionsAssetId:'',imageVersionsLoading:false,imageVersionSelectedId:'',imageVersionPanelCollapsed:true,
    referenceCanvasId:'', referenceCanvasTitle:'ÂèÇËÄÉÁîªÂ∏É', referenceCanvasMode:false, referenceCanvasStructureId:'', referenceCanvasSaving:false, originTab:'registry'
};
let detailViewerClickState = {source:'', id:'', at:0};
let referenceCanvasClickState = {id:'', at:0};
let detailCinemaClickState = {id:'', at:0};
let detailViewerInspectorCollapsed = readStoredBoolean(ASSET_DETAIL_INSPECTOR_COLLAPSED_KEY, true);
let detailViewerFontExpandedPreference = readDetailFontPanelPreference();
let detailViewerFontListCollapsed = readStoredBoolean(ASSET_DETAIL_FONT_LIST_COLLAPSED_KEY, false);
let detailMediaCanvasLibraryCollapsed = readStoredBoolean(ASSET_DETAIL_MEDIA_LIBRARY_COLLAPSED_KEY, false);
let detailMediaCanvasLibraryThumbSize = Math.max(96,Math.min(220,readStoredNumber(ASSET_DETAIL_MEDIA_LIBRARY_SIZE_KEY,156)));
let detailMediaCanvasLibraryWidth = Math.max(280,Math.min(720,readStoredNumber(ASSET_DETAIL_MEDIA_LIBRARY_WIDTH_KEY,420)));
let detailMediaCanvasMinimapVisible = readStoredBoolean(ASSET_DETAIL_MEDIA_MINIMAP_VISIBLE_KEY, true);
let detailMediaCanvasLibraryResizeState = null;
const detailMediaCanvasLibraryRootItemsCache = new Map();
let detailMediaCanvasLibraryRootLoading = '';
let detailMediaCanvasResizeObserver = null;
let detailMediaCanvasObservedViewport = null;
let detailMediaCanvasPositionRaf = 0;
const detailViewerTextCache = new Map();
const detailViewerFileInfoCache = new Map();
const detailViewerAudioWaveformCache = new Map();
const detailViewerMediaPreloads = new Map();
let detailViewerAudioFrame = 0;
let detailViewerFontFace = null;
let detailViewerFontLoadToken = 0;
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
let storageSettingsState = {open:false, tab:'boards', focusSection:'', dirs:{}, defaults:{}, revision:null, extraLocalDirs:[], extraLocalEntries:[], localLibraryNames:{}, localLibraries:[]};
let allowedRootSettingsState = {revision:null, roots:[], loading:false, saving:false, error:''};
let serverDirectoryBrowserState = {open:false, path:null, parent:null, directories:[], loading:false, saving:false, error:'', key:'projects'};
let storageSettingsReturnFocus = null;
let assetAiSettingsState = {rule:'', defaultRule:'', loading:false, saving:false, message:'', backgroundTaskId:''};
let assetProxySettingsState = {projectId:'', enabled:false, autoQueue:false, maxWidth:960, maxHeight:540, loading:false, saving:false, message:''};
let projectDirectoryTemplateManagerState = {open:false, loading:false, saving:false, error:'', templates:[], revision:null, selectedId:'', editor:null, pendingAction:null, archiveId:'', returnFocus:null};

const ASSET_DOCUMENT_PREVIEW_EXTENSIONS = ['txt','csv','tsv','json','xml','html','htm','md','rtf','doc','docx','odt','pdf'];
const ASSET_3D_PREVIEW_EXTENSIONS = ['stl','3mf','obj','fbx','gltf','glb','usd','usdz','step','stp','iges','igs','dxf','ply','3ds','dae','ifc','wrl','vrml','blend','c4d','max','ma','mb'];
const ASSET_FONT_PREVIEW_EXTENSIONS = ['ttf','otf','ttc','otc','woff','woff2'];
const ASSET_AUDIO_PREVIEW_EXTENSIONS = ['mp3','wav','flac','ogg','m4a','aac'];
const ASSET_PREVIEW_RENDER_VERSION = '2026.08.01.5';
const ASSET_FONT_PREVIEW_ZH_LINE = 'ËßÇËá™Âú®Ëè©Ëê®ÔºåË°åÊ∑±Ëà¨Ëã•Ê≥¢ÁΩóËúúÂ§öÊó∂ÁÖßËßÅ‰∫îËï¥ÁöÜÁ©∫Ôºå';
const ASSET_FONT_PREVIEW_LETTERS_UPPER = 'ABCDEFGHIJKLMNOP';
const ASSET_FONT_PREVIEW_LETTERS_LOWER = 'abcdefghijklmnop';
const ASSET_FONT_PREVIEW_NUMBERS = '0123456789';
const ASSET_FONT_PREVIEW_PUNCTUATION = `.,:;!?'"()[]{}-_/@#&%+=<>\\`;
const ASSET_FONT_PREVIEW_PUNCTUATION_ZH = 'Ôºå„ÄÇÔºÅÔºüÔºõÔºö‚Äú‚Äù‚Äò‚ÄôÔºàÔºâ„Ää„Äã„Äê„Äë';
const ASSET_FONT_PREVIEW_ZH = `ËßÇËá™Âú®Ëè©Ëê®ÔºåË°åÊ∑±Ëà¨Ëã•Ê≥¢ÁΩóËúúÂ§öÊó∂ÁÖßËßÅ‰∫îËï¥ÁöÜÁ©∫ÔºåÂ∫¶‰∏ÄÂàáËã¶ÂéÑ„ÄÇ

ËàçÂà©Â≠êÔºåËâ≤‰∏çÂºÇÁ©∫ÔºåÁ©∫‰∏çÂºÇËâ≤„ÄÇËâ≤Âç≥ÊòØÁ©∫ÔºåÁ©∫Âç≥ÊòØËâ≤„ÄÇÂèóÊÉ≥Ë°åËØÜÔºå‰∫¶Â§çÂ¶ÇÊòØ„ÄÇ

ËàçÂà©Â≠êÔºåÊòØËØ∏Ê≥ïÁ©∫Áõ∏Ôºå‰∏çÁîü‰∏çÁÅ≠„ÄÇ‰∏çÂû¢‰∏çÂáÄÔºå‰∏çÂ¢û‰∏çÂáè„ÄÇÊòØÊïÖÁ©∫‰∏≠Êó†Ëâ≤ÔºåÊó†ÂèóÊÉ≥Ë°åËØÜ„ÄÇÊó†ÁúºËÄ≥ÈºªËàåË∫´ÊÑèÔºåÊó†Ëâ≤Â£∞È¶ôÂë≥Ëß¶Ê≥ï„ÄÇÊó†ÁúºÁïåÔºå‰πÉËá≥Êó†ÊÑèËØÜÁïå„ÄÇÊó†Êó†ÊòéÔºå‰∫¶Êó†Êó†ÊòéÂ∞Ω„ÄÇ‰πÉËá≥Êó†ËÄÅÊ≠ªÔºå‰∫¶Êó†ËÄÅÊ≠ªÂ∞Ω„ÄÇÊó†Ëã¶ÈõÜÁÅ≠ÈÅìÔºåÊó†Êô∫‰∫¶Êó†ÂæóÔºå‰ª•Êó†ÊâÄÂæóÊïÖ„ÄÇ

Ëè©ÊèêËê®ÂüµÔºå‰æùËà¨Ëã•Ê≥¢ÁΩóËúúÂ§öÊïÖ„ÄÇÂøÉÊó†ÊåÇÁ¢çÔºåÊó†ÊåÇÁ¢çÊïÖÔºåÊó†ÊúâÊÅêÊÄñ„ÄÇËøúÁ¶ªÈ¢†ÂÄíÊ¢¶ÊÉ≥ÔºåÁ©∂Á´üÊ∂ÖÊßÉ„ÄÇ

‰∏â‰∏ñËØ∏‰ΩõÔºå‰æùËà¨Ëã•Ê≥¢ÁΩóËúúÂ§öÊïÖ„ÄÇÂæóÈòøËÄ®Â§öÁΩó‰∏âËóê‰∏âËè©Êèê„ÄÇ

ÊïÖÁü•Ëà¨Ëã•Ê≥¢ÁΩóËúúÂ§ö„ÄÇÊòØÂ§ßÁ•ûÂííÔºåÊòØÂ§ßÊòéÂííÔºåÊòØÊó†‰∏äÂíí„ÄÇÊòØÊó†Á≠âÁ≠âÂííÔºåËÉΩÈô§‰∏ÄÂàáËã¶ÔºåÁúüÂÆû‰∏çËôö„ÄÇ

ÊïÖËØ¥Ëà¨Ëã•Ê≥¢ÁΩóËúúÂ§öÂíí„ÄÇÂç≥ËØ¥ÂííÊõ∞ÔºåÊè≠Ë∞õÊè≠Ë∞õÔºåÊ≥¢ÁΩóÊè≠Ë∞õ„ÄÇÊ≥¢ÁΩóÂÉßÊè≠Ë∞õÔºåËè©ÊèêËê®Â©ÜËØÉ„ÄÇ`;
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
function setStatus(text='ÂáÜÂ§áÂ∞±Áª™'){
    currentStatusText = text || 'ÂáÜÂ§áÂ∞±Áª™';
    const el = currentStatusElement();
    if(el){
        el.textContent = currentStatusText;
        if(currentIndexState) el.setAttribute('data-gw-index-state', currentIndexState);
        else el.removeAttribute('data-gw-index-state');
        // ÊòæÂºèÈôçÁ∫ßÊ†áËÆ∞ÔºàÁî®Êà∑ 2026-09-21 Ë£ÅÂÜ≥Á¨¨ 3 È°πÔºâÔºöÊú™Êé•ÂÖ• / ÊöÇ‰∏çÂèØÁî®ÂøÖÈ°ªÂèØË¢´Ê†∏È™åÔºå
        // ‰∏çÂæóÈùôÈªò„ÄÇÁà∂È°µ /static/pages/assets.html ‰ºöËØªÂèñËØ•Ê†áËÆ∞Âπ∂ÂêåÊ≠•Â±ïÁ§∫„ÄÇ
        const kind = /Êú™Á∫≥ÂÖ•ÂΩìÂâçÂàáÁâá/.test(currentStatusText) ? 'not_integrated'
            : (/ÊöÇÊó∂‰∏çÂèØÁî®/.test(currentStatusText) ? 'service_unavailable'
                : (/È°πÁõÆ‰∏çÂ≠òÂú®|Êó†ÊùÉËÆøÈóÆ|ÊùÉÈôê‰∏çË∂≥|Âè™ËØª/.test(currentStatusText) ? 'forbidden' : ''));
        if(kind) el.setAttribute('data-gw-degradation', kind);
        else el.removeAttribute('data-gw-degradation');
    }
}
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
function localAssetBatchStatus(action,succeeded,data){
    const failed = Array.isArray(data?.failed) ? data.failed.length : 0;
    const skipped = Array.isArray(data?.skipped) ? data.skipped.length : 0;
    const warnings = Array.isArray(data?.warnings) ? data.warnings.length : 0;
    const parts = [succeeded ? `Â∑≤${action} ${succeeded} ‰∏™Á¥†Êùê` : `${action}Êú™ÂÆåÊàê`];
    if(failed) parts.push(`${failed} ‰∏™Â§±Ë¥•`);
    if(skipped) parts.push(`${skipped} ‰∏™Ë∑≥Ëøá`);
    if(warnings) parts.push(`${warnings} ‰∏™ÈôÑÂ±ûÊñá‰ª∂Ë≠¶Âëä`);
    return parts.join('Ôºå');
}
function showAssetNotice(message,type='success',duration=8000){
    document.querySelector('[data-asset-notice]')?.remove();
    if(assetNoticeTimer) clearTimeout(assetNoticeTimer);
    const notice=document.createElement('div');
    notice.className=`asset-operation-notice is-${type === 'error' ? 'error' : 'success'}`;
    notice.dataset.assetNotice='';
    notice.setAttribute('role',type === 'error' ? 'alert' : 'status');
    notice.innerHTML=`<i data-lucide="${type === 'error' ? 'circle-alert' : 'circle-check-big'}"></i><strong>${escapeHtml(message)}</strong><button type="button" data-asset-notice-close aria-label="ÂÖ≥Èó≠ÊèêÁ§∫"><i data-lucide="x"></i></button>`;
    document.body.appendChild(notice);
    refreshIcons();
    requestAnimationFrame(()=>notice.classList.add('is-visible'));
    assetNoticeTimer=setTimeout(()=>{ notice.classList.remove('is-visible'); setTimeout(()=>notice.remove(),180); },Math.max(1000,Number(duration||8000)));
}
function showAssetFailure(title,error){
    document.querySelector('[data-asset-error-dialog]')?.remove();
    const detail=String(error?.stack || error?.message || error || 'Êú™Áü•ÈîôËØØ').slice(0,8000);
    const overlay=document.createElement('div');
    overlay.className='asset-operation-error-overlay';
    overlay.dataset.assetErrorDialog='';
    overlay.innerHTML=`<section data-floating-content role="alertdialog" aria-modal="true" aria-label="${escapeAttr(title)}"><header><i data-lucide="triangle-alert"></i><div><strong>${escapeHtml(title)}</strong><span>ÂêéÂè∞ËøîÂõûÁöÑÂ§±Ë¥•ÂéüÂõ†‰∏éÂâçÁ´ØË∞ÉÁî®Êó•Âøó</span></div></header><pre>${escapeHtml(detail)}</pre><footer><button class="hw-btn" type="button" data-asset-error-close>ÂÖ≥Èó≠Êó•Âøó</button></footer></section>`;
    document.body.appendChild(overlay);
    markAssetFloatingSurface(overlay, {surface:'asset-operation-error', content:'[role="alertdialog"]', close:() => overlay.remove()});
    refreshIcons();
}
function escapeHtml(value=''){
    return String(value ?? '').replace(/[&<>"']/g, ch => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch]));
}
function escapeAttr(value=''){ return escapeHtml(value); }
const ASSET_BOARD_SETTINGS_KEY = 'asset_board_navigation_v1';
const ASSET_SOURCE_BOARD_PREFIX = 'source_library_';
const SYSTEM_ASSET_BOARDS = [
    {id:'registry', name:'ËµÑ‰∫ßÊÄªËßà', icon:'library-big', fixed:true, system:true},
    {id:'scripts', name:'ÂâßÊú¨ËµÑ‰∫ß', icon:'file-text', system:true},
    {id:'projects', name:'È°πÁõÆÂà∂Áâá', icon:'clapperboard', system:true},
    {id:'assets', name:'ÊâòÁÆ°ËµÑ‰∫ß', icon:'image', system:true},
    {id:'workflows', name:'Â∑•‰ΩúÊµÅ', icon:'workflow', system:true},
    {id:'prompts', name:'ÊèêÁ§∫ËØç', icon:'text-cursor-input', system:true},
    {id:'canvas-assets', name:'ÁîªÂ∏ÉËµÑ‰∫ß', icon:'layout-dashboard', system:true},
    {id:'local', name:'‰∏ä‰º†ËµÑÊ∫ê', icon:'cloud-upload', system:true},
    {id:'governance', name:'Ê≤ªÁêÜÂª∫ËÆÆ', icon:'list-checks', system:true},
];
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
            return content ? [{id:`pipeline-script:${pipeline.pipeline_id}`, pipelineId:pipeline.pipeline_id, projectId, title:`${pipeline.title || 'Êú™ÂëΩÂêçÂâßÈõÜ'} ¬∑ ÂâßÊú¨`, name:`${pipeline.title || 'Êú™ÂëΩÂêçÂâßÈõÜ'}.md`, kind:'script', format:'md', content, createdAt:pipeline.updated_at || pipeline.created_at || Date.now()}] : [];
        });
        const merged = [...remoteItems, ...localItems].filter((item, index, list) => list.findIndex(value => value.pipelineId === item.pipelineId && value.content === item.content) === index);
        scriptArchiveItems = merged.sort((a, b) => Number(b.createdAt || 0) - Number(a.createdAt || 0));
        if(activeTab === 'scripts') render();
    } catch(_) { scriptArchiveItems = localItems; if(activeTab === 'scripts') render(); }
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
function renderScriptAssetsManager(){
    const items = scriptArchiveItems.length ? scriptArchiveItems : readScriptWorkspaceAssets();
    const cards = items.map(item => {
        const title = escapeHtml(item.title || item.name || 'Êú™ÂëΩÂêçÂâßÊú¨');
        const date = escapeHtml(formatDate(item.createdAt));
        const preview = escapeHtml(String(item.content || '').replace(/\s+/g, ' ').slice(0, 180) || 'ÊöÇÊó†Ê≠£ÊñáÈ¢ÑËßà');
        const fileName = escapeHtml(item.name || 'ÂâßÊú¨.md');
        const projectId = escapeHtml(item.projectId || 'Êú™ÁªëÂÆöÈ°πÁõÆ');
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
            <div clas€}µÁkhëÈÏ∂ªßq´^t€€ú›Ÿ^Xõÿ\ôY][ô»H…“SîU	À	’VTëPIÀ	‘—SP’	◊Kö[ò€Y\ ›ö[ô ]ô[ùù\ôŸ]ÀùY”ò[YH	… Kù’\\êÿ\ŸJ
JH]ô[ùù\ôŸ]Àö\–€€ù[ùY]XõN¬à€€ú›[ù\òX›]ôU\ôŸ]H]ô[ùù\ôŸ]Àò€‹Ÿ\›Àä	ÿù]€ãK[ú]^\ôXKŸ[X›	 N¬à€€ú›]Z[ŸŸ€T⁄‹ù›]HY]ô[ùò›õŸ^H	âàY]ô[ùõY]RŸ^H	âàY]ô[ùò[Ÿ^H	âà
]ô[ùò€ŸHOOH	–òX⁄‹][›I»]ô[ùöŸ^HOOH	ﬂâ»]ô[ùöŸ^HOOH	ÿ	»]ô[ùöŸ^HOOH	≠… N¬à€€ú›õÿ›\ŸYôY⁄\›ûUô\ú⁄[€î›ùX›\ôHH]ô[ùù\ôŸ]Àò€‹Ÿ\›Àä	÷Ÿ]K\ôY⁄\›ûK\›ùX›\ôKXÿ\ôVŸ]K\ôY⁄\›ûK\›ùX›\ôK]\OHùô\ú⁄[€àóI Bàÿ›[Y[ùòX›]ôQ[[Y[ùÀò€‹Ÿ\›Àä	÷Ÿ]K\ôY⁄\›ûK\›ùX›\ôKXÿ\ôVŸ]K\ôY⁄\›ûK\›ùX›\ôK]\OHùô\ú⁄[€àóI N¬àYä]Z[ŸŸ€T⁄‹ù›]	âàõÿ›\ŸYôY⁄\›ûUô\ú⁄[€î›ùX›\ôH	âàZŸ^Xõÿ\ôY][ô»	âàY]Z[öY]Ÿ\î›]Kõ‹[ä^¬à]ô[ùúô]ô[ùYò][

N¬à‹[îôY⁄\›ûT›ùX›\ôTôYô\ô[òŸPÿ[ùò\ àõÿ›\ŸYôY⁄\›ûUô\ú⁄[€î›ùX›\ôKô]\Ÿ]úôY⁄\›ûT›ùX›\ôPÿ\ô	…Ààõÿ›\ŸYôY⁄\›ûUô\ú⁄[€î›ùX›\ôKà
N¬àô]\õé¬àBàYä]Z[ŸŸ€T⁄‹ù›]	âàZŸ^Xõÿ\ôY][ô ^¬à]ô[ùúô]ô[ùYò][

N¬àYä]Z[öY]Ÿ\î›]Kõ‹[äH€‹ŸQ]Z[ô]öY] 
N¬à[ŸH¬à€€ú›ôYô\ô[òŸHH›\úô[ù]Z[öY]Ÿ\îôYô\ô[òŸJ
N¬àYäôYô\ô[òŸOÀöY
H⁄›—]Z[ô]öY] ôYô\ô[òŸKú€›\òŸKôYô\ô[òŸKöY
N¬àBàô]\õé¬àBàYä]Z[öY]Ÿ\î›]Kõ‹[ä^¬àYä]ô[ùöŸ^HOOH	—\ÿÿ\I»	âàÿ›[Y[ùôù[ÿ‹ôY[ë[[Y[ù
Hô]\õé¬àYä]ô[ùù\ôŸ]ò€‹Ÿ\›Àä	÷Ÿ]KY]Z[]ô\ú⁄[€ãYY]‹óI J^¬àYä]ô[ùöŸ^HOOH	—\ÿÿ\I ^¬à]ô[ùúô]ô[ùYò][

N¬àÿ[òŸ[]Z[^ô\ú⁄[€ëY]

N¬àH[ŸHYä
]ô[ùò›õŸ^H]ô[ùõY]RŸ^JH	âà]ô[ùöŸ^Kù”›Ÿ\êÿ\ŸJ
HOOH	‹… ^¬à]ô[ùúô]ô[ùYò][

N¬àÿ]ôQ]Z[^ô\ú⁄[€ëY]

Kòÿ]⁄
\úàOàŸ]›]\ \úãõY\‹ÿYŸH	˘‚b9ß+9/Áykf9i,z-)I JN¬àBàô]\õé¬àBàYä]ô[ùù\ôŸ]ò€‹Ÿ\›Àä	÷Ÿ]KY]Z[]^YY]‹óI J^¬àYä]ô[ùöŸ^HOOH	—\ÿÿ\I ^¬à]ô[ùúô]ô[ùYò][

N¬àÿ[òŸ[]Z[^Y]

N¬àH[ŸHYä
]ô[ùò›õŸ^H]ô[ùõY]RŸ^JH	âà]ô[ùöŸ^Kù”›Ÿ\êÿ\ŸJ
HOOH	‹… ^¬à]ô[ùúô]ô[ùYò][

N¬àÿ]ôQ]Z[^Y]

Kòÿ]⁄
\úàOàŸ]›]\ \úãõY\‹ÿYŸH	˘´h˘•°˘/Áykf9i,z-)I JN¬àBàô]\õé¬àBàYä]ô[ùù\ôŸ]ò€‹Ÿ\›Àä	÷Ÿ]KY]Z[[ò[YKZ[ú]I J^¬àYä]ô[ùöŸ^HOOH	—[ù\â ^¬à]ô[ùúô]ô[ùYò][

N¬àÿ]ôQ]Z[öY]Ÿ\ìò[YJ
Kòÿ]⁄
\úàOàŸ]›]\ \úãõY\‹ÿYŸH	˘d#yÈÏ9/Áykf9i,z-)I JN¬àBàô]\õé¬àBàYäŸ^Xõÿ\ôY][ô Hô]\õé¬à€€ú›X›]ôUöY[“][HH]Z[öY]Ÿ\ïöY[“][J
N¬à€€ú›X›]ôUöY[»H]Z[öY]Ÿ\ï\J]Z[öY]Ÿ\î›]Kú€›\òŸKX›]ôUöY[“][JHOOH	›öY[…»»]Z[öY]Ÿ\ïöY[ 
Hàù[¬àYäX›]ôUöY[ ^¬à€€ú›Ÿ^HH›ö[ô ]ô[ùöŸ^H	… Kù”›Ÿ\êÿ\ŸJ
N¬àYä
]ô[ùò›õŸ^H]ô[ùõY]RŸ^JH	âà]ô[ùú⁄YùŸ^H	âàY]ô[ùò[Ÿ^H	âàŸ^HOOH	ÿ… ^¬à]ô[ùúô]ô[ùYò][

N»^‹ù]Z[öY[—úò[YJ	…Àò[ŸJKòÿ]⁄
\úàOà»Ÿ]›]\ \úãõY\‹ÿYŸH	˘ceyn)˘kÔ9aÓπi,z-)I N»⁄›–\‹Ÿ]òZ[\ôJ	˘ceyn)˘kÔ9aÓπi,z-)IÀ\úäN»JN»ô]\õé¬àBàYä
]ô[ùò›õŸ^H]ô[ùõY]RŸ^JH	âàY]ô[ùú⁄YùŸ^H	âàY]ô[ùò[Ÿ^H	âàŸ^HOOH	ÿ… ^¬à]ô[ùúô]ô[ùYò][

N»€‹Q]Z[öY[—úò[YU–€\õÿ\ô

Kòÿ]⁄
\úàOàŸ]›]\ \úãõY\‹ÿYŸH	˙)·∫h§yn)˘i#yb-πi,z-)I JN»ô]\õé¬àBàYäY]ô[ùò›õŸ^H	âàY]ô[ùõY]RŸ^H	âàY]ô[ùò[Ÿ^H	âà…⁄IÀ	€…À	ﬁ	À	ÿ…À	ﬁâ◊Kö[ò€Y\ Ÿ^JJ^¬à]ô[ùúô]ô[ùYò][

N¬à€€ú›X›[€àHŸ^HOOH	⁄I»»	‹Ÿ]Z[â»àŸ^HOOH	€…»»	‹Ÿ][›]	»àŸ^HOOH	ﬁ	»»	‹ò]KY›€â»àŸ^HOOH	ÿ…»»	‹ò]K]\	»à	‹ò]K\ô\Ÿ]	Œ¬à€€ú›ù]€àHÿ›[Y[ùú]Y\ûTŸ[X›‹äò\‹Ÿ]Y]Z[]öY]Ÿ\àŸ]KY]Z[]öY[ÀXX›[€èHâÿX›[€üHóX
N¬àYäù]€äH[ôQ]Z[öY[–€€ùõ€
ù]€äKòÿ]⁄
\úàOàŸ]›]\ \úãõY\‹ÿYŸH	˙)·∫h§yoÍ˘£m˘§„y/g9i,z-)I JN¬àô]\õé¬àBàBàYä
]ô[ùò›õŸ^H]ô[ùõY]RŸ^JH	âàY]ô[ùú⁄YùŸ^H	âàY]ô[ùò[Ÿ^H	âà]ô[ùöŸ^Kù”›Ÿ\êÿ\ŸJ
HOOH	ÿ…»	âà]Z[YYXPÿ[ùò\“][\ 
Kõ[ô›
^¬à]ô[ùúô]ô[ùYò][

N¬à€‹Q]Z[YYXPÿ[ùò\‘Ÿ[X›[€ï–€\õÿ\ô

Kòÿ]⁄
\úàOàŸ]›]\ \úãõY\‹ÿYŸH	˘fÔπ‚a˘i#yb-πi,z-)I JN¬àô]\õé¬àBàYä]Z[YYXPÿ[ùò\“][\ 
Kõ[ô›]Z[YYXPÿ[ùò\–[õõ›][€ú 
Kõ[ô›]Z[YYXPÿ[ùò\—ò]⁄[ô‹ 
Kõ[ô›
^¬àYä
]ô[ùò›õŸ^H]ô[ùõY]RŸ^JH	âà]ô[ùú⁄YùŸ^H	âà]ô[ùöŸ^Kù”›Ÿ\êÿ\ŸJ
HOOH	Ÿ… ^¬à]ô[ùúô]ô[ùYò][

N»[ô‹õ›\]Z[YYXPÿ[ùò\‘Ÿ[X›[€ä
N»ô]\õé¬àBàYä
]ô[ùò›õŸ^H]ô[ùõY]RŸ^JH	âà]ô[ùöŸ^Kù”›Ÿ\êÿ\ŸJ
HOOH	Ÿ… ^¬à]ô[ùúô]ô[ùYò][

N»‹õ›\]Z[YYXPÿ[ùò\‘Ÿ[X›[€ä
N»ô]\õé¬àBàYä
]ô[ùò›õŸ^H]ô[ùõY]RŸ^JH	âà]ô[ùöŸ^Kù”›Ÿ\êÿ\ŸJ
HOOH	ÿI ^¬à]ô[ùúô]ô[ùYò][

N¬àŸ]]Z[YYXPÿ[ùò\‘Ÿ[X›[€ä]Z[YYXPÿ[ùò\”ÿöôX›Y 
JN¬àﬁ[ò—]Z[YYXPÿ[ùò\‘Ÿ[X›[€ë€J
N¬àŸ]›]\ 9mÏπaj:`"yÂ.˘n ˘a°H	Ÿ]Z[YYXPÿ[ùò\”ÿöôX›Y 
Kõ[ô›H9.*πkÓz,hX
N¬àô]\õé¬àBàYä
]ô[ùò›õŸ^H]ô[ùõY]RŸ^JH	âà]ô[ùöŸ^Kù”›Ÿ\êÿ\ŸJ
HOOH	ﬁâ ^¬à]ô[ùúô]ô[ùYò][

N»[ô—]Z[YYXPÿ[ùò\ 
N»ô]\õé¬àBàYä]ô[ùöŸ^HOOH	—[]I ^¬à]ô[ùúô]ô[ùYò][

N»[]Q]Z[YYXPÿ[ùò\‘Ÿ[X›[€ä
N»ô]\õé¬àBàBàYä]ô[ùöŸ^HOOH	—\ÿÿ\I»	âà]Z[öY]Ÿ\î›]KõYYXPÿ[ùò\–€€ù^Y[ùJ^¬à]ô[ùúô]ô[ùYò][

N¬à€‹ŸQ]Z[YYXPÿ[ùò\–€€ù^Y[ùJ
N¬àô]\õé¬àBàYä]ô[ùöŸ^HOOH	—\ÿÿ\I ^¬à]ô[ùúô]ô[ùYò][

N¬à€‹ŸQ]Z[ô]öY] 
N¬àô]\õé¬àBà€€ú›]Z[\HH]Z[öY]Ÿ\ï\J]Z[öY]Ÿ\î›]Kú€›\òŸK]Z[öY]Ÿ\î›]Kö][\÷Ÿ]Z[öY]Ÿ\î›]Kö[ô^HﬂJN¬à€€ú›õÿ›\ŸYYYXSõŸHH]ô[ùù\ôŸ]ò€‹Ÿ\›Àä	÷Ÿ]KY]Z[[YYXKXÿ[ùò\À[õŸWI N¬àYäõÿ›\ŸYYYXSõŸH	âàZ[ù\òX›]ôU\ôŸ]	âà…—[ù\âÀ	»	◊Kö[ò€Y\ ]ô[ùöŸ^JJ^¬à]ô[ùúô]ô[ùYò][

N¬àX›]ò]Q]Z[YYXPÿ[ùò\“][Jõÿ›\ŸYYYXSõŸKô]\Ÿ]ô]Z[YYXPÿ[ùò\”õŸH	… N¬àô]\õé¬àBàYä]Z[\HOOH	›öY[…»	âàZ[ù\òX›]ôU\ôŸ]	âà…Ÿ	À	Ÿâ◊Kö[ò€Y\ ]ô[ùöŸ^Kù”›Ÿ\êÿ\ŸJ
JJ^¬à]ô[ùúô]ô[ùYò][

N¬à›\]Z[öY[—úò[YJ]ô[ùöŸ^Kù”›Ÿ\êÿ\ŸJ
HOOH	Ÿâ»»HàLJN¬àô]\õé¬àBàYä]Z[\HOOH	›öY[…»	âàZ[ù\òX›]ôU\ôŸ]	âà]ô[ùöŸ^HOOH	—[ù\â ^¬à]ô[ùúô]ô[ùYò][

N¬à[ù\ë]Z[öY[—ù[ÿ‹ôY[ä
N¬àô]\õé¬àBàYä…–\úõ›”Yù	À	–\úõ›‘öY⁄	◊Kö[ò€Y\ ]ô[ùöŸ^JJ^¬à]ô[ùúô]ô[ùYò][

N¬àYä]Z[YYXPÿ[ùò\“][\ 
Kõ[ô›àJHﬁX€Q]Z[YYXPÿ[ùò\“][J]ô[ùöŸ^HOOH	–\úõ›‘öY⁄	»»HàLJN¬à[ŸHò]öYÿ]Q]Z[ô]öY] ]ô[ùöŸ^HOOH	–\úõ›‘öY⁄	»»HàLJN¬àô]\õé¬àBàÀ»8‡$9/Îπi#zeÎ∫h¶Àyhßπo.∏‡$yk£9aj:f.˘´hπÍnπ®/:e+πÊ°:nÊ:+©:(c9..ªÔ#9cÍπg*:)·∫h§K˙gÏ˙h§y§´y•/π•Ìπ¢cyd„yn•9Ínπ®/:e+ÇàYä
]ô[ùöŸ^HOOH	»	»]ô[ùò€ŸHOOH	‘‹XŸI H	âàY]ô[ùù\ôŸ]ò€‹Ÿ\›Àä	ÿù]€ãK[ú]^\ôXKŸ[X›	 J^¬à]ô[ùúô]ô[ùYò][

N»À»9i‚˘Ó‚:f.˘´h∫nÊ:+©9ÆÊπb™:(c9..Çà€€ú›][HH]Z[öY]Ÿ\î›]Kö][\÷Ÿ]Z[öY]Ÿ\î›]Kö[ô^HﬂN¬à€€ú›\HH]Z[öY]Ÿ\ï\J]Z[öY]Ÿ\î›]Kú€›\òŸK][JN¬àÀ»9cÍπß"z)·∫h§y¢%∫gÏ˙h§yÏn˘g¢˘¢cy¢i˙(c9§´y•/ã˘¶†π`g9§„y/gàYä\HOOH	›öY[…»\HOOH	ÿ]Y[… ^¬àŸŸ€Q]Z[ô]öY]‘^XòX⁄ 
N¬àBàÀ»9amπ.ÂπÏn˘g¢˘.#y¢i˙(c9.Ó˘/ey§„y/g;Ô#9Ínπ®/:e+∫(™˘k£9aj9È†yÂ*àô]\õé¬àBàBàYä]ô[ùöŸ^HOOH	—\ÿÿ\I ^¬à€‹ŸQ]Z[ô]öY] 
N¬àYäõÿ\ô‹ôX]T›]Kõ‹[äH€‹ŸP‹ôX]SXúò\ûS[Ÿ[

N¬àYäõÿ\ô€›\òŸT›]Kõ‹[äH€‹ŸPõÿ\ô€›\òŸS[Ÿ[

N¬àYäõÿ\ôY]›]Kõ‹[äH€‹ŸPõÿ\ôY][Ÿ[

N¬àYäõÿ\ôõ€\ê‹ôX]T›]Kõ‹[äH€‹ŸP›\›€Põÿ\ôõ€\ê‹ôX]S[Ÿ[

N¬àYä[XõòZ[Ÿ[ô\ò]‹î›]Kõ‹[äH€‹ŸU[XõòZ[Ÿ[ô\ò]‹ä
N¬àYä›‹òYŸTŸ][ô‹‘›]Kõ‹[äH€‹ŸT›‹òYŸTŸ][ô‹ 
N¬àYäôY⁄\›ûTôXﬁX€Pö[î›]Kõ‹[äH€‹ŸTôY⁄\›ûTôXﬁX€Pö[ä
N¬àBà€€ú›Y][ô»HŸ^Xõÿ\ôY][ôŒ¬àYä
X›]ôUXàOOH	‹ôY⁄\›ûI»\‹Ÿ]õÿ\ôûRY
X›]ôUXäOÀúﬁ\›[HOOHò[ŸJH	âàYY][ô ^¬à€€ú›ö\⁄XõHHX›]ôTôY⁄\›ûUö\⁄XõP\‹Ÿ] 
N¬àYä
]ô[ùò›õŸ^H]ô[ùõY]RŸ^JH	âà]ô[ùöŸ^Kù”›Ÿ\êÿ\ŸJ
HOOH	ÿI ^¬à]ô[ùúô]ô[ùYò][

N¬àö\⁄XõKôõ‹ëXX⁄
][HOàôY⁄\›ûTŸ[X›YYÀòY
][KöY
JN¬àôY⁄\›ûSX[òYŸS[ŸHHùYN¬àô[ô\ä
N¬àô]\õé¬àBàYä…–\úõ›”Yù	À	–\úõ›‘öY⁄	À	–\úõ›’\	À	–\úõ›—›€â◊Kö[ò€Y\ ]ô[ùöŸ^JH	âàö\⁄XõKõ[ô›
^¬à]ô[ùúô]ô[ùYò][

N¬à€€ú››\úô[ùHX]õX^
ö\⁄XõKôö[ô[ô^
][HOà][KöYOOHŸ[X›YôY⁄\›ûP\‹Ÿ]Y
JN¬à€€ú›‹öYHÿ›[Y[ùú]Y\ûTŸ[X›‹ä	ÀúôY⁄\›ûKY‹öY	 N¬à€€ú›ö\ùX[€€[[ú»H‹öYÀò€\‹”\›ò€€ùZ[ú 	‹ôY⁄\›ûKY‹öY]ö\ùX[^ôY	 Bà»ù[Xô\äôY⁄\›ûUö\ùX[›]Kò€€»
Bàà¬à€€ú›€€[[ú»HX]õX^
Kö\ùX[€€[[ú»›ö[ô ‹öY»Ÿ]€€\]Y›[J‹öY
Kô‹öY[\]P€€[[ú»à	… Kú‹]
	»	 Kôö[\äõ€€X[äKõ[ô›JN¬à€€ú›[HH]ô[ùöŸ^HOOH	–\úõ›”Yù	»»LHà]ô[ùöŸ^HOOH	–\úõ›‘öY⁄	»»Hà]ô[ùöŸ^HOOH	–\úõ›’\	»»X€€[[ú»à€€[[úŒ¬àôY⁄\›ûQõÿ›\“[ô^HX]õX^
X]õZ[äö\⁄XõKõ[ô›HK›\úô[ù
»[JJN¬àôY⁄\›ûQõÿ›\‘ô\›‹ôRYHö\⁄XõV‹ôY⁄\›ûQõÿ›\“[ô^KöY¬àŸ[X›YôY⁄\›ûP\‹Ÿ]YHôY⁄\›ûQõÿ›\‘ô\›‹ôRY¬àôY⁄\›ûQ]Z[Hö\⁄XõV‹ôY⁄\›ûQõÿ›\“[ô^N¬àô[ô\ä
N¬àô\]Y\›[ö[X][€ëúò[YJ

HOà¬à€€ú›ÿ\ôHÿ›[Y[ùú]Y\ûTŸ[X›‹äŸ]K\ôY⁄\›ûKXÿ\ôHâ–‘‘Àô\ÿÿ\JôY⁄\›ûQõÿ›\‘ô\›‹ôRY
_HóX
N¬àÿ\ôÀúÿ‹õ€[ù’öY] ÿõÿ⁄Œâ€ôX\ô\›	ﬂJN¬àÿ\ôÀôõÿ›\ ‹ô]ô[ùÿ‹õ€ùùY_JN¬àJN¬àô]\õé¬àBàYä]ô[ùöŸ^HOOH	—[ù\â»	âàZ[ù\òX›]ôU\ôŸ]
^¬à€€ú››\úô[ùHôY⁄\›ûP\‹Ÿ]Àôö[ô
][HOà][KöYOOHŸ[X›YôY⁄\›ûP\‹Ÿ]Y
Bà
ôY⁄\›ûQ]Z[ÀöYOOHŸ[X›YôY⁄\›ûP\‹Ÿ]Y»ôY⁄\›ûQ]Z[àù[
Bàö\⁄XõKôö[ô
][HOà][KöYOOHŸ[X›YôY⁄\›ûP\‹Ÿ]Y][Kò\‹Ÿ]⁄YOOHŸ[X›YôY⁄\›ûP\‹Ÿ]Y
Bàù[¬àYä›\úô[ù	âàôY⁄\›ûP€›ô\ôõ›‘€›\òŸR\’ö\›X[
	‹ôY⁄\›ûIÀ›\úô[ù
J^¬à]ô[ùúô]ô[ùYò][

N¬à‹[îôY⁄\›ûP€›ô\ôõ› ÿ›[Y[ùòX›]ôQ[[Y[ùÿ›\úô[ù\‹Ÿ]Yî›ö[ô ›\úô[ùò\‹Ÿ]⁄Y›\úô[ùöY	… _JN¬àô]\õé¬àBàBàYä]ô[ùöŸ^HOOH	—\ÿÿ\I»	âàôY⁄\›ûTŸ[X›YYÀú⁄^ôJ^¬àôY⁄\›ûTŸ[X›YYÀò€X\ä
N¬àôY⁄\›ûSX[òYŸS[ŸHHò[ŸN¬àô[ô\ä
N¬àô]\õé¬àBàBàYä]ô[ùù\ôŸ]ÀöYOOH	ÿ\‹Ÿ]ôYQY][ú]	 ^¬àYä]ô[ùöŸ^HOOH	—[ù\â ^»]ô[ùúô]ô[ùYò][

N»ÿ]ôP\‹Ÿ]ôYQY]

Kòÿ]⁄
\úàOàŸ]›]\ \úãõY\‹ÿYŸH	˘/Áykf9i,z-)I JN»BàYä]ô[ùöŸ^HOOH	—\ÿÿ\I ^»]ô[ùúô]ô[ùYò][

N»\‹Ÿ]ôYQY]Hù[»ô[ô\ä
N»BàBàYä]ô[ùù\ôŸ]ÀöYOOH	›€‹öŸõ›’ôYQY][ú]	 ^¬àYä]ô[ùöŸ^HOOH	—[ù\â ^»]ô[ùúô]ô[ùYò][

N»ÿ]ôU€‹öŸõ›’ôYQY]

Kòÿ]⁄
\úàOàŸ]›]\ \úãõY\‹ÿYŸH	˘/Áykf9i,z-)I JN»BàYä]ô[ùöŸ^HOOH	—\ÿÿ\I ^»]ô[ùúô]ô[ùYò][

N»€‹öŸõ›’ôYQY]Hù[»ô[ô\ä
N»BàBàYä]ô[ùù\ôŸ]ÀöYOOH	‹õ€\ôYQY][ú]	 ^¬àYä]ô[ùöŸ^HOOH	—[ù\â ^»]ô[ùúô]ô[ùYò][

N»ÿ]ôTõ€\ôYQY]

Kòÿ]⁄
\úàOàŸ]›]\ \úãõY\‹ÿYŸH	˘/Áykf9i,z-)I JN»BàYä]ô[ùöŸ^HOOH	—\ÿÿ\I ^»]ô[ùúô]ô[ùYò][

N»õ€\ôYQY]Hù[»ô[ô\ä
N»BàBüJN¬ôÿ›[Y[ùòY]ô[ù\›[ô\ä	›⁄Y[	Àò]öYÿ]Q]Z[ô]öY]’⁄Y[‹\‹⁄]ôNôò[Ÿ_JN¬õ]ôY⁄\›ûPÿ\ô⁄^ôTÿ]ôU[Y\àH¬ôÿ›[Y[ùòY]ô[ù\›[ô\ä	›⁄Y[	À]ô[ùOà¬àYäY]ô[ùú⁄YùŸ^H]ô[ùò›õŸ^H]ô[ùõY]RŸ^H]ô[ùò[Ÿ^JHô]\õé¬àYäY]ô[ùù\ôŸ]Àò€‹Ÿ\›Àä	÷Ÿ]K\ôY⁄\›ûK^õ€€KX\ôXWI JHô]\õé¬àYäY]ô[ùù\ôŸ]ò€‹Ÿ\›
	ÀúôY⁄\›ûKX€€ù[ùúôY⁄\›ûK[^[›]Y‹öY	 JHô]\õé¬à]ô[ùúô]ô[ùYò][

N¬àôY⁄\›ûPÿ\ô⁄^ôHHX]õX^
ëQ“T’ñW––Të‘“VëKõZ[ãX]õZ[äëQ“T’ñW––Të‘“VëKõX^ôY⁄\›ûPÿ\ô⁄^ôH
»
]ô[ùô[VHà»LLààLäJJN¬àõ€›ú]Y\ûTŸ[X›‹ê[
	ÀúôY⁄\›ûKX€€ù[ù	 Kôõ‹ëXX⁄
õŸHOàõŸKú›[KúŸ]õ‹\ùJ	ÀK\ôY⁄\›ûKXÿ\ô\⁄^ôIÀôY⁄\›ûPÿ\ô⁄^ôH
»	‹	 JN¬à€X\ï[Y[›]
ôY⁄\›ûPÿ\ô⁄^ôTÿ]ôU[Y\äN¬àôY⁄\›ûPÿ\ô⁄^ôTÿ]ôU[Y\àHŸ][Y[›]


HOà¬àÿ]ôTôY⁄\›ûUöY]‘Ÿ][ô‹ 
N¬àŸ]›]\ 	˙h°:)‚9i)˘l#»	»
»ôY⁄\›ûPÿ\ô⁄^ôH
»	‹	 N¬àKç
N¬üK‹\‹⁄]ôNôò[Ÿ_JN¬ôÿ›[Y[ùòY]ô[ù\›[ô\ä	‹ÿ‹õ€	À]ô[ùOà¬àYä]ô[ùù\ôŸ]ÀõX]⁄\œÀä	÷Ÿ]KY]Z[[YYXK[Xúò\ûKY‹öYI JH]Z[öY]Ÿ\î›]KõYYXPÿ[ùò\”Xúò\ûTÿ‹õ€‹H]ô[ùù\ôŸ]úÿ‹õ€‹¬à€€ú›ôY⁄\›ûTÿ‹õ€H]ô[ùù\ôŸ]Àò€‹Ÿ\›Àä	ÀúôY⁄\›ûKX\‹Ÿ]À\ÿ‹õ€	 N¬àYäôY⁄\›ûTÿ‹õ€
^¬àÀ»8‡$9≠c˙)‚:h°9b®:/ox‡$z)Èπc‰y£‰9bcza„˘.„πfÓπk¶àLå9£‰9ca˘b,9Ó©àKå»9`#z)·πcË˙jÊ9n©ªÔ"KåüåKçH9`#yc.∫eÌ9cÂπ.+{Ô"{Ô#àÀ»9."˘. :hmy•l9£k∫h°9cÂπab9.£πÆÊπb™9b,9/c{Ô#:acyd"ôYô]⁄ôY⁄\›ûSô^YŸH9kßπ„¨9•Ë9°'˘ÓÎz+Ó˚Ô&¬àÀ»:e+πÊÊ9cÎ˙/ØπÊ°Ÿ]K\ôY⁄\›ûK[ÿY[[‹ôWH9£"zd´π/ÁyÂfy.#yb™8‡ Çà€€ú›XYHX]õX^
LåX]úõ›[ô
X]õX^
ôY⁄\›ûTÿ‹õ€ò€Y[ùZY⁄⁄[ô›Àö[õô\íZY⁄
H
àKå JN¬àYäôY⁄\›ûTÿ‹õ€úÿ‹õ€‹
»ôY⁄\›ûTÿ‹õ€ò€Y[ùZY⁄èHôY⁄\›ûTÿ‹õ€úÿ‹õ€ZY⁄HXY
^¬àÿY[‹ôTôY⁄\›ûP\‹Ÿ] 
Kòÿ]⁄
\úàOàŸ]›]\ \úãõY\‹ÿYŸH	˘b®:/oy¶Ì9i&∫-a9.©˘i,z-)I JN¬àBàBüKùYJN¬ôÿ›[Y[ùòY]ô[ù\›[ô\ä	⁄[ú]	À]ô[ùOà¬àYä]ô[ùù\ôŸ]ÀöYOOH	ÿõÿ\ô‹ôX]Sò[YI»]ô[ùù\ôŸ]ÀöYOOH	ÿõÿ\ô‹ôX]T]	 ^¬àﬁ[ò–õÿ\ô‹ôX]QòYùúõ€Q€J]ô[ùù\ôŸ]öY
N¬àô]\õé¬àBàYä]ô[ùù\ôŸ]Àò€‹Ÿ\›Àä	÷Ÿ]K\õ⁄ôX›Y\ôX›‹ûK][\]KYY]‹óI J^¬àﬁ[ò‘õ⁄ôX›\ôX›‹ûU[\]QòYù

N¬àô]\õé¬àBà€€ú›]Z[öY[‘õŸ‹ô\‹»H]ô[ùù\ôŸ]Àò€‹Ÿ\›Àä	÷Ÿ]KY]Z[]öY[À\õŸ‹ô\‹◊I N¬àYä]Z[öY[‘õŸ‹ô\‹ ^¬à€€ú›öY[»H]Z[öY]Ÿ\ïöY[ ]Z[öY[‘õŸ‹ô\‹Àò€‹Ÿ\›
	÷Ÿ]KY]Z[]öY[ÀX€€ùõ€◊I OÀô]\Ÿ]ô]Z[öY[—õ‹à	… N¬àYäöY[ ^»öY[Àò›\úô[ù[YHHX]õX^
X]õZ[äù[Xô\äöY[Àô\ò][€à]Z[öY[‘õŸ‹ô\‹ÀõX^
Kù[Xô\ä]Z[öY[‘õŸ‹ô\‹Àùò[YH
JJN»ﬁ[ò—]Z[öY[–€€ùõ€ öY[ N»Bàô]\õé¬àBà€€ú›]Z[öY[’õ€[YHH]ô[ùù\ôŸ]Àò€‹Ÿ\›Àä	÷Ÿ]KY]Z[]öY[À]õ€[YWI N¬àYä]Z[öY[’õ€[YJ^¬à€€ú›öY[»H]Z[öY]Ÿ\ïöY[ ]Z[öY[’õ€[YKò€‹Ÿ\›
	÷Ÿ]KY]Z[]öY[ÀX€€ùõ€◊I OÀô]\Ÿ]ô]Z[öY[—õ‹à	… N¬àYäöY[ ^»öY[Àùõ€[YHHX]õX^
X]õZ[äKù[Xô\ä]Z[öY[’õ€[YKùò[YH
JJN»YäöY[Àùõ€[YHà
HöY[Àõ]]YHò[ŸN»ﬁ[ò—]Z[öY[–€€ùõ€ öY[ N»Bàô]\õé¬àBàYä]ô[ùù\ôŸ]ÀõX]⁄\œÀä	÷Ÿ]KY]Z[\ôYô\ô[òŸK]]WI JH]Z[öY]Ÿ\î›]KúôYô\ô[òŸPÿ[ùò\’]HH›ö[ô ]ô[ùù\ôŸ]ùò[YH	… Kú€XŸJ
N¬à€€ú›[õõ›][€àH]ô[ùù\ôŸ]Àò€‹Ÿ\›Àä	÷Ÿ]KY]Z[[YYXKX[õõ›][€óI N¬àYä[õõ›][€ä^¬à€€ú›õ›HH
]Z[öY]Ÿ\î›]KõYYXPÿ[ùò\–[õõ›][€ú»◊JKôö[ô
ò[YHOàò[YKöYOOH[õõ›][€ãô]\Ÿ]ô]Z[YYXP[õõ›][€äN¬àYäõ›J^¬àõ›Kù^H›ö[ô [õõ›][€ãùò[YH	… Kú€XŸJå
N¬àö]]Z[YYXPÿ[ùò\–[õõ›][€ï’^
õ›K[õõ›][€ãò€‹Ÿ\›
	÷Ÿ]KY]Z[[YYXKX[õõ›][€ã[õŸWI JN¬àBàBà€€ú›Xúò\ûT⁄^ôHH]ô[ùù\ôŸ]Àò€‹Ÿ\›Àä	÷Ÿ]KY]Z[[YYXK[Xúò\ûK\⁄^ôWI N¬àYäXúò\ûT⁄^ôJ^¬à]Z[YYXPÿ[ùò\”Xúò\ûU[Xî⁄^ôHHX]õX^
MãX]õZ[äååù[Xô\äXúò\ûT⁄^ôKùò[YHMMäJJN¬àÿ›[Y[ùú]Y\ûTŸ[X›‹ä	Àò\‹Ÿ]Y]Z[[YYXK[Xúò\ûI OÀú›[KúŸ]õ‹\ùJ	ÀK[YYXK[Xúò\ûK][Xã\⁄^ôIÀ	Ÿ]Z[YYXPÿ[ùò\”Xúò\ûU[Xî⁄^ô_\
N¬à€€ú››]]Hÿ›[Y[ùú]Y\ûTŸ[X›‹ä	÷Ÿ]KY]Z[[YYXK[Xúò\ûK\⁄^ôK[›]]I N¬àYä›]]
H›]]ù^€€ù[ùH	Ÿ]Z[YYXPÿ[ùò\”Xúò\ûU[Xî⁄^ô_\¬àûH»ÿÿ[›‹òYŸKúŸ]][JT‘—U—URS”QQPW”PîêTñW‘“VëW“—VK›ö[ô ]Z[YYXPÿ[ùò\”Xúò\ûU[Xî⁄^ôJJN»Hÿ]⁄
 HﬂBàBà€€ú›Xúò\ûTŸX\ò⁄H]ô[ùù\ôŸ]Àò€‹Ÿ\›Àä	÷Ÿ]KY]Z[[YYXK[Xúò\ûK\ŸX\ò⁄I N¬àYäXúò\ûTŸX\ò⁄
^¬à]Z[öY]Ÿ\î›]KõYYXPÿ[ùò\”Xúò\ûT]Y\ûHH›ö[ô Xúò\ûTŸX\ò⁄ùò[YH	… Kú€XŸJMå
N¬àôYúô\⁄]Z[YYXPÿ[ùò\”Xúò\ûQ‹öY

N¬àBà€€ú›[õõ›][€ê€€‹àH]ô[ùù\ôŸ]Àò€‹Ÿ\›Àä	÷Ÿ]KY]Z[X[õõ›][€ãX€€‹óI N¬àYä[õõ›][€ê€€‹äH\Q]Z[YYXPÿ[ùò\–[õõ›][€ê€€‹ä[õõ›][€ê€€‹ãô]\Ÿ]ô]Z[[õõ›][€ê€€‹à	…À[õõ›][€ê€€‹ãùò[YH	»Ÿôôôôôâ N¬üJN¬ôÿ›[Y[ùòY]ô[ù\›[ô\ä	‹⁄[ù\ô›€âÀ]ô[ùOà¬àYä]ô[ùù\ôŸ]ÀõX]⁄\œÀä	÷Ÿ]KY]Z[X[õõ›][€ãX€€‹óI JH]Z[YYXPÿ[ùò\–€€‹î€ò\⁄›Hÿ\\ôQ]Z[YYXPÿ[ùò\‘€ò\⁄›

N¬üKùYJN¬ôÿ›[Y[ùòY]ô[ù\›[ô\ä	ÿ⁄[ôŸIÀ]ô[ùOà¬àYä]ô[ùù\ôŸ]Àò€‹Ÿ\›Àä	÷Ÿ]K\õ⁄ôX›Y\ôX›‹ûK][\]KYY]‹óI J^¬àﬁ[ò‘õ⁄ôX›\ôX›‹ûU[\]QòYù

N¬àô]\õé¬àBàYä]ô[ùù\ôŸ]ÀõX]⁄\œÀä	÷Ÿ]KY]Z[]öY[À\õ€›I J^¬à]Z[öY]Ÿ\î›]KùöY[—^‹ùõ€›YH›ö[ô ]ô[ùù\ôŸ]ùò[YH	… N¬à€€ú›öY[»H]Z[öY]Ÿ\ïöY[ ]ô[ùù\ôŸ]ò€‹Ÿ\›
	÷Ÿ]KY]Z[]öY[ÀX€€ùõ€◊I OÀô]\Ÿ]ô]Z[öY[—õ‹à	… N¬àYäöY[ Hﬁ[ò—]Z[öY[–€€ùõ€ öY[ N¬àŸ]›]\ :)·∫h§ykÔ9aÓπÌ(9ßd9n§˚Ô&âŸ]ô[ùù\ôŸ]úŸ[X›Y‹[€úœÀñÃOÀù^€€ù[ù	˘ß*∫`"y¢ÍIﬂX
N¬àô]\õé¬àBàYä]ô[ùù\ôŸ]ÀõX]⁄\œÀä	÷Ÿ]KY]Z[[YYXK[Xúò\ûK\ÿ€‹WI J^¬à]Z[öY]Ÿ\î›]KõYYXPÿ[ùò\”Xúò\ûTÿ€‹HH]ô[ùù\ôŸ]ùò[YH	ÿ[	Œ¬àôYúô\⁄]Z[YYXPÿ[ùò\”Xúò\ûQ‹öY

N¬àô]\õé¬àBàYä]ô[ùù\ôŸ]ÀõX]⁄\œÀä	÷Ÿ]KY]Z[[YYXK[Xúò\ûK[Xúò\ûWI J^¬à]Z[öY]Ÿ\î›]KõYYXPÿ[ùò\”Xúò\ûT›‹òYŸTõ€›YH]ô[ùù\ôŸ]ùò[YH	ÿ[	Œ¬à]Z[öY]Ÿ\î›]KõYYXPÿ[ùò\”Xúò\ûTÿ€‹HH	ÿ[	Œ¬à€€ú›ÿ€‹HHÿ›[Y[ùú]Y\ûTŸ[X›‹ä	÷Ÿ]KY]Z[[YYXK[Xúò\ûK\ÿ€‹WI N¬àYäÿ€‹JHÿ€‹Kùò[YHH	ÿ[	Œ¬àôYúô\⁄]Z[YYXPÿ[ùò\”Xúò\ûQ‹öY

N¬àÿY]Z[YYXPÿ[ùò\”Xúò\ûTõ€›
]Z[öY]Ÿ\î›]KõYYXPÿ[ùò\”Xúò\ûT›‹òYŸTõ€›Y
N¬àô]\õé¬àBàYä]ô[ùù\ôŸ]ÀõX]⁄\œÀä	÷Ÿ]KY]Z[[YYXK[Xúò\ûK\€‹ùI J^¬à]Z[öY]Ÿ\î›]KõYYXPÿ[ùò\”Xúò\ûT€‹ùH…€ô]Ÿ\›	À	€€\›	À	€ò[YIÀ	‹⁄^ôWŸ\ÿ…◊Kö[ò€Y\ ]ô[ùù\ôŸ]ùò[YJH»]ô[ùù\ôŸ]ùò[YHà	€ô]Ÿ\›	Œ¬àôYúô\⁄]Z[YYXPÿ[ùò\”Xúò\ûQ‹öY

N¬àô]\õé¬àBàYä]ô[ùù\ôŸ]ÀõX]⁄\œÀä	÷Ÿ]KY]Z[[YYXKY^‹ù\ÿ€‹WI J^¬à]Z[öY]Ÿ\î›]KõYYXPÿ[ùò\—^‹ùÿ€‹HH]ô[ùù\ôŸ]ùò[YHOOH	‹Ÿ[X›[€â»»	‹Ÿ[X›[€â»à	ÿ[	Œ¬àÿ›[Y[ùú]Y\ûTŸ[X›‹ê[
	÷Ÿ]KY]Z[[YYXKY^‹ù\ÿ€‹WI Kôõ‹ëXX⁄
Ÿ[X›Oà»Ÿ[X›ùò[YHH]Z[öY]Ÿ\î›]KõYYXPÿ[ùò\—^‹ùÿ€‹N»JN¬à€€ú›[\àHÿ›[Y[ùú]Y\ûTŸ[X›‹ä	Àò\‹Ÿ]Y]Z[[YYXKY^‹ù]€€»à€X[	 N¬àYä[\äH[\ãù^€€ù[ùH]Z[öY]Ÿ\î›]KõYYXPÿ[ùò\—^‹ùÿ€‹HOOH	‹Ÿ[X›[€â»»
]Z[YYXPÿ[ùò\‘Ÿ[X›YY 
Kú⁄^ôH»:/§˘aÓπmÏ∫`"H	Ÿ]Z[YYXPÿ[ùò\‘Ÿ[X›YY 
Kú⁄^ô_H:hnXà	˙+Ì˘ab:`"y¢ÍykÓz,hI Hà	˙/§˘aÓπaj:`Í9Â.˘n …Œ¬àô]\õé¬àBàYä]ô[ùù\ôŸ]ÀõX]⁄\œÀä	÷Ÿ]KY]Z[[YYXK[õ‹õX[^ôKXò\⁄\◊I J^¬à]Z[öY]Ÿ\î›]KõYYXPÿ[ùò\”õ‹õX[^ôPò\⁄\»H…Ÿö\ú›	À	ÿ]ô\òYŸIÀ	€\›	◊Kö[ò€Y\ ]ô[ùù\ôŸ]ùò[YJH»]ô[ùù\ôŸ]ùò[YHà	Ÿö\ú›	Œ¬àÿ›[Y[ùú]Y\ûTŸ[X›‹ê[
	÷Ÿ]KY]Z[[YYXK[õ‹õX[^ôKXò\⁄\◊I Kôõ‹ëXX⁄
Ÿ[X›Oà»Ÿ[X›ùò[YHH]Z[öY]Ÿ\î›]KõYYXPÿ[ùò\”õ‹õX[^ôPò\⁄\Œ»JN¬àŸ]›]\ :)·9®/9c%πgÓπa·πmÏπb!˘£hπ..âŸ]Z[öY]Ÿ\î›]KõYYXPÿ[ùò\”õ‹õX[^ôPò\⁄\»OOH	ÿ]ô\òYŸI»»	˘nl˘ga˘`/	»à]Z[öY]Ÿ\î›]KõYYXPÿ[ùò\”õ‹õX[^ôPò\⁄\»OOH	€\›	»»	˘ß 9d#π. 9.*â»à	˘Î+9. 9.*âﬂX
N¬àô]\õé¬àBàYäY]ô[ùù\ôŸ]ÀõX]⁄\œÀä	÷Ÿ]KY]Z[X[õõ›][€ãX€€‹óI JHô]\õé¬àYä]Z[YYXPÿ[ùò\–€€‹î€ò\⁄›
H\⁄]Z[YYXPÿ[ùò\“\›‹ûJ]Z[YYXPÿ[ùò\–€€‹î€ò\⁄›
N¬à]Z[YYXPÿ[ùò\–€€‹î€ò\⁄›Hù[¬üJN¬ôÿ›[Y[ùòY]ô[ù\›[ô\ä	Ÿõÿ›\€›]	À]ô[ùOà¬àYä]Z[[XYŸQY]‹î›]H	âà]ô[ùù\ôŸ]ÀõX]⁄\œÀä	÷Ÿ]KZ[XYŸKYY]‹ãZ[õ[ôK]^I J^¬àô\]Y\›[ö[X][€ëúò[YJ

HOà¬àYäY]Z[[XYŸQY]‹î›]Hÿ›[Y[ùòX›]ôQ[[Y[ùÀõX]⁄\œÀä	÷Ÿ]KZ[XYŸKYY]‹ãZ[õ[ôK]^I JHô]\õé¬à]Z[[XYŸQY]‹î›]Kö[õ[ôU^YI…Œ¬àô[ô\ë]Z[[XYŸQY]‹ä
N¬àJN¬àô]\õé¬àBàYäY]ô[ùù\ôŸ]ÀõX]⁄\œÀä	÷Ÿ]KY]Z[[YYXKX[õõ›][€óI JHô]\õé¬àô\]Y\›[ö[X][€ëúò[YJ

HOà¬àYäÿ›[Y[ùòX›]ôQ[[Y[ùÀõX]⁄\œÀä	÷Ÿ]KY]Z[[YYXKX[õõ›][€óI JHô]\õé¬àYäö[ö\⁄]Z[YYXPÿ[ùò\–[õõ›][€ëY]

JHô[ô\ë]Z[öY]Ÿ\ä
N¬àJN¬üJN¬ôÿ›[Y[ùòY]ô[ù\›[ô\ä	ÿ€€ù^Y[ùIÀ‹[ë]Z[YYXPÿ[ùò\–€€ù^Y[ùJN¬ôÿ›[Y[ùòY]ô[ù\›[ô\ä	ÿ€€ù^Y[ùIÀ‹[îôY⁄\›ûT›ùX›\ôP€€ù^Y[ùJN¬ôÿ›[Y[ùòY]ô[ù\›[ô\ä	‹\›IÀ[ôQ]Z[YYXPÿ[ùò\‘\›JN¬ôÿ›[Y[ùòY]ô[ù\›[ô\ä	ŸòY‹›\ù	ÀôY⁄[ë]Z[YYXSXúò\ûQòY N¬ôÿ›[Y[ùòY]ô[ù\›[ô\ä	ŸòY€›ô\âÀ[›—]Z[YYXPÿ[ùò\—õ‹
N¬ôÿ›[Y[ùòY]ô[ù\›[ô\ä	Ÿõ‹	Àõ‹]Z[YYXPÿ[ùò\”Xúò\ûR][JN¬ôÿ›[Y[ùòY]ô[ù\›[ô\ä	ŸòY€X]ôIÀ]ô[ùOà¬à€€ú›öY]‹‹ùH]ô[ùù\ôŸ]ò€‹Ÿ\›Àä	÷Ÿ]KY]Z[[YYXKXÿ[ùò\À]öY]‹‹ùI N¬àYäöY]‹‹ù	âà]öY]‹‹ùò€€ùZ[ú ]ô[ùúô[]Y\ôŸ]
JHöY]‹‹ùò€\‹”\›úô[[›ôJ	⁄\ÀX\‹Ÿ]YòYÀ[›ô\â N¬üJN¬ôÿ›[Y[ùòY]ô[ù\›[ô\ä	ŸòYŸ[ô	À

HOà¬à]Z[YYXSXúò\ûQòY“YH	…Œ¬àÿ›[Y[ùú]Y\ûTŸ[X›‹ä	÷Ÿ]KY]Z[[YYXKXÿ[ùò\À]öY]‹‹ùI OÀò€\‹”\›úô[[›ôJ	⁄\ÀX\‹Ÿ]YòYÀ[›ô\â N¬üJN¬ôÿ›[Y[ùòY]ô[ù\›[ô\ä	ÿ]^€X⁄…À]ô[ùOà¬àYä]ô[ùòù]€àOOHH	âà]ô[ùù\ôŸ]ò€‹Ÿ\›Àä	÷Ÿ]KY]Z[[YYXKXÿ[ùò\À]öY]‹‹ùKò\‹Ÿ]Z[XYŸKYY]‹ã\›YŸI JH]ô[ùúô]ô[ùYò][

N¬üJN¬ôÿ›[Y[ùòY]ô[ù\›[ô\ä	€ÿY	Àÿ\\ôQ]Z[YYXPÿ[ùò\”ò]\ò[⁄^ôKùYJN¬ôÿ›[Y[ùòY]ô[ù\›[ô\ä	€ÿYYY]Y]IÀÿ\\ôQ]Z[YYXPÿ[ùò\”ò]\ò[⁄^ôKùYJN¬ñ…€ÿYYY]Y]IÀ	Ÿ\ò][€ò⁄[ôŸIÀ	›[Y]\]IÀ	‹^IÀ	‹]\ŸIÀ	Ÿ[ôY	À	›õ€[YX⁄[ôŸIÀ	‹ò]X⁄[ôŸI◊Kôõ‹ëXX⁄
\HOàÿ›[Y[ùòY]ô[ù\›[ô\ä\K[ôQ]Z[öY[”YYXQ]ô[ùùYJJN¬ôÿ›[Y[ùòY]ô[ù\›[ô\ä	⁄[ú]	À\]Q]Z[[XYŸQY]‹îõ‹\ùJN¬ôÿ›[Y[ùòY]ô[ù\›[ô\ä	ÿ⁄[ôŸIÀ\]Q]Z[[XYŸQY]‹îõ‹\ùJN¬ôÿ›[Y[ùòY]ô[ù\›[ô\ä	‹⁄[ù\ô›€âÀôY⁄[ë]Z[[XYŸQY]‹î⁄[ù\ãùYJN¬ôÿ›[Y[ùòY]ô[ù\›[ô\ä	‹⁄[ù\õ[›ôIÀ[›ôQ]Z[[XYŸQY]‹î⁄[ù\äN¬ôÿ›[Y[ùòY]ô[ù\›[ô\ä	‹⁄[ù\ù\	À[ô]Z[[XYŸQY]‹î⁄[ù\äN¬ôÿ›[Y[ùòY]ô[ù\›[ô\ä	‹⁄[ù\òÿ[òŸ[	À[ô]Z[[XYŸQY]‹î⁄[ù\äN¬ôÿ›[Y[ùòY]ô[ù\›[ô\ä	‹⁄[ù\ô›€âÀ[ôQ]Z[⁄[ô[XT⁄[ù\ãùYJN¬ôÿ›[Y[ùòY]ô[ù\›[ô\ä	‹⁄[ù\ô›€âÀôY⁄[ë]Z[YYXPÿ[ùò\‘[ãùYJN¬ôÿ›[Y[ùòY]ô[ù\›[ô\ä	‹⁄[ù\ô›€âÀôY⁄[îôY⁄\›ûQ]Z[[ô[ô\⁄^ôJN¬ôÿ›[Y[ùòY]ô[ù\›[ô\ä	‹⁄[ù\ô›€âÀôY⁄[ë]Z[YYXPÿ[ùò\”Xúò\ûTô\⁄^ôJN¬ôÿ›[Y[ùòY]ô[ù\›[ô\ä	‹⁄[ù\õ[›ôIÀ\]Q]Z[YYXPÿ[ùò\‘[äN¬ôÿ›[Y[ùòY]ô[ù\›[ô\ä	‹⁄[ù\õ[›ôIÀ\]TôY⁄\›ûQ]Z[[ô[ô\⁄^ôJN¬ôÿ›[Y[ùòY]ô[ù\›[ô\ä	‹⁄[ù\õ[›ôIÀ\]Q]Z[YYXPÿ[ùò\”Xúò\ûTô\⁄^ôJN¬ôÿ›[Y[ùòY]ô[ù\›[ô\ä	‹⁄[ù\ù\	À[ô]Z[YYXPÿ[ùò\‘[äN¬ôÿ›[Y[ùòY]ô[ù\›[ô\ä	‹⁄[ù\ù\	À[ôôY⁄\›ûQ]Z[[ô[ô\⁄^ôJN¬ôÿ›[Y[ùòY]ô[ù\›[ô\ä	‹⁄[ù\ù\	À[ô]Z[YYXPÿ[ùò\”Xúò\ûTô\⁄^ôJN¬ôÿ›[Y[ùòY]ô[ù\›[ô\ä	‹⁄[ù\òÿ[òŸ[	À[ô]Z[YYXPÿ[ùò\‘[äN¬ôÿ›[Y[ùòY]ô[ù\›[ô\ä	‹⁄[ù\òÿ[òŸ[	À[ôôY⁄\›ûQ]Z[[ô[ô\⁄^ôJN¬ôÿ›[Y[ùòY]ô[ù\›[ô\ä	‹⁄[ù\òÿ[òŸ[	À[ô]Z[YYXPÿ[ùò\”Xúò\ûTô\⁄^ôJN¬ôÿ›[Y[ùòY]ô[ù\›[ô\ä	‹⁄[ù\ô›€âÀôY⁄[ìY⁄õﬁ[äN¬ôÿ›[Y[ùòY]ô[ù\›[ô\ä	‹⁄[ù\õ[›ôIÀ\]SY⁄õﬁ[äN¬ôÿ›[Y[ùòY]ô[ù\›[ô\ä	‹⁄[ù\ù\	À[ôY⁄õﬁ[äN¬ôÿ›[Y[ùòY]ô[ù\›[ô\ä	‹⁄[ù\òÿ[òŸ[	À[ôY⁄õﬁ[äN¬úõ€›òY]ô[ù\›[ô\ä	‹⁄[ù\ô›€âÀôY⁄[ìX\ú]YYTŸ[X›[€äN¬ôÿ›[Y[ùòY]ô[ù\›[ô\ä	‹⁄[ù\õ[›ôIÀ]ô[ùOà\]SX\ú]YYTŸ[X›[€ä]ô[ù
JN¬ôÿ›[Y[ùòY]ô[ù\›[ô\ä	‹⁄[ù\ù\	À[ôX\ú]YYTŸ[X›[€äN¬ôÿ›[Y[ùòY]ô[ù\›[ô\ä	‹⁄[ù\òÿ[òŸ[	À[ôX\ú]YYTŸ[X›[€äN¬úõ€›òY]ô[ù\›[ô\ä	ÿ€€\‹⁄][€ú›\ù	À]ô[ùOà¬àYä—PTê““SîU“QÀö\ ]ô[ùù\ôŸ]ÀöY	… J^¬àŸX\ò⁄€€\‹⁄][€êX›]ôHHùYN¬à€X\ï[Y[›]
ŸX\ò⁄ô[ô\ï[Y\äN¬àBüJN¬úõ€›òY]ô[ù\›[ô\ä	ÿ€€\‹⁄][€ô[ô	À]ô[ùOà¬à€€ú›YH]ô[ùù\ôŸ]ÀöY	…Œ¬àYäT—PTê““SîU“QÀö\ Y
JHô]\õé¬àŸX\ò⁄€€\‹⁄][€êX›]ôHHò[ŸN¬à\›ŸX\ò⁄€€\‹⁄][€ë[ô]H]Kõõ› 
N¬à\]TŸX\ò⁄]Y\ûQúõ€R[ú]
Y]ô[ùù\ôŸ]ùò[YH	… N¬àÿ⁄Y[TŸX\ò⁄ô[ô\äY]ô[ùù\ôŸ]úŸ[X›[€î›\ù›ö[ô ]ô[ùù\ôŸ]ùò[YH	… Kõ[ô›
N¬üJN¬úõ€›òY]ô[ù\›[ô\ä	⁄[ú]	À]ô[ùOà¬àYä]ô[ùù\ôŸ]ÀöYOOH	ÿõÿ\ô‹ôX]Sò[YI Hõÿ\ô‹ôX]T›]Kõò[YHH]ô[ùù\ôŸ]ùò[YH	…Œ¬àYä]ô[ùù\ôŸ]ÀöYOOH	ÿõÿ\ô‹ôX]T]	 Hõÿ\ô‹ôX]T›]Kú]H]ô[ùù\ôŸ]ùò[YH	…Œ¬à€€ú›ŸX\ò⁄YH]ô[ùù\ôŸ]ÀöY	…Œ¬àYä—PTê““SîU“QÀö\ ŸX\ò⁄Y
J^¬à\]TŸX\ò⁄]Y\ûQúõ€R[ú]
ŸX\ò⁄Y]ô[ùù\ôŸ]ùò[YH	… N¬àYä]ô[ùö\–€€\‹⁄[ô»ŸX\ò⁄€€\‹⁄][€êX›]ôJHô]\õé¬àYä]Kõõ› 
HH\›ŸX\ò⁄€€\‹⁄][€ë[ô]
Hô]\õé¬àÿ⁄Y[TŸX\ò⁄ô[ô\äŸX\ò⁄Y]ô[ùù\ôŸ]úŸ[X›[€î›\ù
N¬àô]\õé¬àBàYä]ô[ùù\ôŸ]ÀöYOOH	€ÿÿ[ÿ\[€îõ€\	 ^¬àÿÿ[ÿ\[€îõ€\H]ô[ùù\ôŸ]ùò[YH	…Œ¬à‹ö]Sÿÿ[ÿ\[€îŸ][ô‹ 
N¬àBàYä]ô[ùù\ôŸ]ÀöYOOH	€ÿÿ[€\‹⁄YûTõ€\	 ^¬àÿÿ[€\‹⁄YûTõ€\H]ô[ùù\ôŸ]ùò[YH	…Œ¬à‹ö]Sÿÿ[ÿ\[€îŸ][ô‹ 
N¬àBüJN¬úõ€›òY]ô[ù\›[ô\ä	ÿ⁄[ôŸIÀ]ô[ùOà¬àYä]ô[ùù\ôŸ]ÀöYOOH	‹ôY⁄\›ûT€‹ù	 ^¬àôY⁄\›ûT€‹ùH]ô[ùù\ôŸ]ùò[YH	€ô]Ÿ\›	Œ¬àôYúô\⁄ôY⁄\›ûP\‹Ÿ] 
Kòÿ]⁄
\úàOàŸ]›]\ \úãõY\‹ÿYŸH	˘£§πn£˘i,z-)I JN¬àô]\õé¬àBàYä]ô[ùù\ôŸ]ÀöYOOH	‹ôY⁄\›ûQ‹õ›\[ŸI ^¬à€€ú›ò[YHH]ô[ùù\ôŸ]ùò[YH	€õ€ôIŒ¬àôY⁄\›ûQ‹õ›\[ŸHH…€õ€ôIÀ	‹€›\òŸIÀ	Ÿõ€\âÀ	ÿ›\›€I◊Kö[ò€Y\ ò[YJH»ò[YHà	€õ€ôIŒ¬àÿ]ôTôY⁄\›ûUöY]‘Ÿ][ô‹ 
N¬àô[ô\ä
N¬àô]\õé¬àBà€€ú›[õ[ôSÿÿ[\ÿYò[YHH]ô[ùù\ôŸ]ò€‹Ÿ\›Àä	÷Ÿ]K[ÿÿ[\Z[õ[ôK[ò[YWI N¬àYä[õ[ôSÿÿ[\ÿYò[YJ^¬àÿ]ôSÿÿ[\ÿY[õ[ôSò[YJ[õ[ôSÿÿ[\ÿYò[YKô]\Ÿ]õÿÿ[\[õ[ôSò[YH	…À[õ[ôSÿÿ[\ÿYò[YKùò[YH	… Kòÿ]⁄
\úàOàŸ]›]\ \úãõY\‹ÿYŸH	˘/Áykf9i,z-)I JN¬àô]\õé¬àBà€€ú›[õ[ôP\‹Ÿ]ò[YHH]ô[ùù\ôŸ]ò€‹Ÿ\›Àä	÷Ÿ]KX\‹Ÿ]Z[õ[ôK[ò[YWI N¬àYä[õ[ôP\‹Ÿ]ò[YJ^¬àÿ]ôP\‹Ÿ][õ[ôSò[YJ[õ[ôP\‹Ÿ]ò[YKô]\Ÿ]ò\‹Ÿ][õ[ôSò[YH	…À[õ[ôP\‹Ÿ]ò[YKùò[YH	… Kòÿ]⁄
\úàOàŸ]›]\ \úãõY\‹ÿYŸH	˘/Áykf9i,z-)I JN¬àô]\õé¬àBà€€ú›[õ[ôU€‹öŸõ›”ò[YHH]ô[ùù\ôŸ]ò€‹Ÿ\›Àä	÷Ÿ]K]€‹öŸõ›ÀZ[õ[ôK[ò[YWI N¬àYä[õ[ôU€‹öŸõ›”ò[YJ^¬à€€ú›][RYH[õ[ôU€‹öŸõ›”ò[YKô]\Ÿ]ù€‹öŸõ›“[õ[ôSò[YH	…Œ¬à\Rú€€îô\‹€úŸJ\‹Ÿ]X[òYŸ\ê\Kù\]P\‹Ÿ]Xúò\ûR][J][RY€ò[YNö[õ[ôU€‹öŸõ›”ò[YKùò[YH	…À^X›Y›ô\ú⁄[€éò\‹Ÿ]Xúò\ûUô\ú⁄[€ëõ‹í][J][RY
_JJKù[ä]HOà¬à\‹Ÿ]Xúò\ûHH]KõXúò\ûH\‹Ÿ]Xúò\ûN¬àô[ô\ä
N¬àŸ]›]\ 	˘mÏπ/Áykf9mÈy/g9≠`yd#yÈÏ	 N¬àJKòÿ]⁄
\úàOàŸ]›]\ \úãõY\‹ÿYŸH	˘/Áykf9i,z-)I JN¬àô]\õé¬àBàYä]ô[ùù\ôŸ]ÀöYOOH	ÿÿ[ùò\–\‹Ÿ]€‹ù	 ^¬àÿ[ùò\–\‹Ÿ]€‹ùH]ô[ùù\ôŸ]ùò[YH	ÿÿ[ùò\◊ÿ\ÿ…Œ¬àŸ[X›Yÿ[ùò\–\‹Ÿ]YH	…Œ¬àô[ô\ä
N¬àBàYä]ô[ùù\ôŸ]ÀöYOOH	ÿ\‹Ÿ][›ôU\ôŸ]	 ^¬à\‹Ÿ][›ôU\ôŸ]H]ô[ùù\ôŸ]ùò[YH	…Œ¬à[ô[ô–ò]⁄[]HH	…Œ¬àô[ô\ä
N¬àBàYä]ô[ùù\ôŸ]ÀöYOOH	€ÿÿ[ÿ\[€îõ›öY\â ^¬àÿÿ[ÿ\[€îõ›öY\àH]ô[ùù\ôŸ]ùò[YH	…Œ¬àÿÿ[ÿ\[€ì[Ÿ[H	…Œ¬àõ‹õX[^ôSÿÿ[ÿ\[€îŸ][ô‹ 
N¬à‹ö]Sÿÿ[ÿ\[€îŸ][ô‹ 
N¬àô[ô\ä
N¬àBàYä]ô[ùù\ôŸ]ÀöYOOH	€ÿÿ[ÿ\[€ì[Ÿ[	 ^¬àÿÿ[ÿ\[€ì[Ÿ[H]ô[ùù\ôŸ]ùò[YH	…Œ¬à‹ö]Sÿÿ[ÿ\[€îŸ][ô‹ 
N¬àô[ô\ä
N¬àBà€€ú›]ò]\îõ›öY\àH]ô[ùù\ôŸ]ò€‹Ÿ\›Àä	÷Ÿ]KX]ò]\ã\õ›öY\óI N¬àYä]ò]\îõ›öY\ä^¬à]ò]\îôY⁄\›\îõ›öY\àH]ò]\îõ›öY\ãùò[YH	…Œ¬àô[ô\ä
N¬àBüJN¬ôÿ›[Y[ùòY]ô[ù\›[ô\ä	ÿ⁄[ôŸIÀ]ô[ùOà¬àYä]ô[ùù\ôŸ]ÀöYOOH	›[XõòZ[\ôX›‹ûTô\Ÿ]	 ^¬à[XõòZ[Ÿ[ô\ò]‹î›]Kô\ôX›‹ûHH]ô[ùù\ôŸ]ùò[YH	…Œ¬à€€ú›[ú]Hÿ›[Y[ùôŸ][[Y[ùûRY
	›[XõòZ[\ôX›‹ûI N¬àYä[ú]
H[ú]ùò[YHH[XõòZ[Ÿ[ô\ò]‹î›]Kô\ôX›‹ûN¬àYäY\ôX›‹ûTŸ][ô‹‘YŸH	âà›‹òYŸTŸ][ô‹‘›]Kõ‹[à	âà[XõòZ[Ÿ[ô\ò]‹î›]Kõ[ŸHOOH	ÿ›\›€I»	âà[XõòZ[Ÿ[ô\ò]‹î›]Kô\ôX›‹ûJ^¬à\ú⁄\›[XõòZ[›‹òYŸTŸ][ô‹ 
Kòÿ]⁄
\úàOà¬à[XõòZ[Ÿ[ô\ò]‹î›]Kô\úõ‹àH\úãõY\‹ÿYŸH	˘Ô$˘kf9ÊÎπoeyb&˘nÓπi,z-)IŒ¬àô[ô\î›‹òYŸTŸ][ô‹”[Ÿ[

N¬àJN¬àBàô]\õé¬àBàYä]ô[ùù\ôŸ]ÀöYOOH	ÿõÿ\ô€›\òŸQ^\›[ô… ^¬à€€ú›XàHÿÿ[Xúò\öY\Àôö[ô
][HOà][KöYOOH]ô[ùù\ôŸ]ùò[YJN¬à€€ú›ò[YR[ú]Hÿ›[Y[ùôŸ][[Y[ùûRY
	ÿõÿ\ô€›\òŸSò[YI N¬à€€ú›][ú]Hÿ›[Y[ùôŸ][[Y[ùûRY
	ÿõÿ\ô€›\òŸT]	 N¬àYäXä^»Yäò[YR[ú]
Hò[YR[ú]ùò[YHHXãõò[YH	˘Â*9¢-˘Ì(9ßd	Œ»Yä][ú]
H][ú]ùò[YHHXãú]	…Œ»Bà[ŸHYä][ú]
H][ú]ùò[YHH	…Œ¬àô]\õé¬àBàYäY]ô[ùù\ôŸ]Àò€‹Ÿ\›Àä	»‹›‹òYŸTŸ][ô‹”›ô\õ^I JHô]\õé¬àYä]ô[ùù\ôŸ]ÀöYOOH	›[XõòZ[\ôX›‹ûI ^¬à[XõòZ[Ÿ[ô\ò]‹î›]Kô\ôX›‹ûHH›ö[ô ]ô[ùù\ôŸ]ùò[YH	… Kùö[J
N¬àYäY\ôX›‹ûTŸ][ô‹‘YŸH	âà[XõòZ[Ÿ[ô\ò]‹î›]Kõ[ŸHOOH	ÿ›\›€I»	âà[XõòZ[Ÿ[ô\ò]‹î›]Kô\ôX›‹ûJ^¬à\ú⁄\›[XõòZ[›‹òYŸTŸ][ô‹ 
Kòÿ]⁄
\úàOà¬à[XõòZ[Ÿ[ô\ò]‹î›]Kô\úõ‹àH\úãõY\‹ÿYŸH	˘Ô$˘kf9ÊÎπoeyb&˘nÓπi,z-)IŒ¬àô[ô\î›‹òYŸTŸ][ô‹”[Ÿ[

N¬àJN¬àBàô]\õé¬àBàYä]ô[ùù\ôŸ]ÀöYOOH	›[XõòZ[ò[úÿ€ŸUöY[‹… ^¬à[XõòZ[Ÿ[ô\ò]‹î›]Kùò[úÿ€ŸUöY[‹»Hõ€€X[ä]ô[ùù\ôŸ]ò⁄X⁄ŸY
N¬àô]\õé¬àBàYä]ô[ùù\ôŸ]ÀöYOOH	‹ôYêÿ\[€îõ›öY\â ^¬àﬁ[ò‘›‹òYŸTŸ][ô‹“[ú]’‘›]J
N¬àÿÿ[ÿ\[€îõ›öY\àH]ô[ùù\ôŸ]ùò[YH	…Œ¬àÿÿ[ÿ\[€ì[Ÿ[H	…Œ¬àõ‹õX[^ôSÿÿ[ÿ\[€îŸ][ô‹ 
N¬àô[ô\î›‹òYŸTŸ][ô‹”[Ÿ[

N¬àô]\õé¬àBàYä]ô[ùù\ôŸ]ÀöYOOH	‹ôYêÿ\[€ì[Ÿ[	 ^¬àÿÿ[ÿ\[€ì[Ÿ[H]ô[ùù\ôŸ]ùò[YH	…Œ¬àô]\õé¬àBüJN¬úõ€›òY]ô[ù\›[ô\ä	ŸòY‹›\ù	À]ô[ùOà¬à€€ú›XàH]ô[ùù\ôŸ]ò€‹Ÿ\›Àä	÷Ÿ]K[ÿÿ[[Xúò\ûKYòY◊I N¬àYä]XäHô]\õé¬àÿÿ[Xúò\ûQòY“YHXãô]\Ÿ]õÿÿ[Xúò\ûQòY»	…Œ¬à]ô[ùô]Uò[úŸô\èÀúŸ]]J	›^‹Z[âÀÿÿ[Xúò\ûQòY“Y
N¬à]ô[ùô]Uò[úŸô\ãôYôôX›[›ŸYH	€[›ôIŒ¬àXãò€\‹”\›òY
	ŸòYŸ⁄[ô… N¬üJN¬úõ€›òY]ô[ù\›[ô\ä	ŸòY€›ô\âÀ]ô[ùOà¬à€€ú›Xúò\ûUXàH]ô[ùù\ôŸ]ò€‹Ÿ\›Àä	÷Ÿ]K[ÿÿ[[Xúò\ûK]XóI N¬àYäÿÿ[Xúò\ûQòY“Y	âàXúò\ûUXä^¬à]ô[ùúô]ô[ùYò][

N¬à]ô[ùô]Uò[úŸô\ãôõ‹YôôX›H	€[›ôIŒ¬àô]\õé¬àBà€€ú›õ‹H]ô[ùù\ôŸ]ò€‹Ÿ\›Àä	»ÿ\‹Ÿ]õ‹€ÿÿ[\ÿYõ‹›€‹öŸõ›—õ‹	 N¬àYäYõ‹
Hô]\õé¬à]ô[ùúô]ô[ùYò][

N¬àõ‹ò€\‹”\›òY
	ŸòYÀ[›ô\â N¬üJN¬úõ€›òY]ô[ù\›[ô\ä	ŸòY€X]ôIÀ]ô[ùOà¬à]ô[ùù\ôŸ]ò€‹Ÿ\›Àä	»ÿ\‹Ÿ]õ‹€ÿÿ[\ÿYõ‹›€‹öŸõ›—õ‹	 OÀò€\‹”\›úô[[›ôJ	ŸòYÀ[›ô\â N¬üJN¬úõ€›òY]ô[ù\›[ô\ä	Ÿõ‹	À]ô[ùOà¬à€€ú›Xúò\ûUXàH]ô[ùù\ôŸ]ò€‹Ÿ\›Àä	÷Ÿ]K[ÿÿ[[Xúò\ûK]XóI N¬àYäÿÿ[Xúò\ûQòY“Y	âàXúò\ûUXä^¬à]ô[ùúô]ô[ùYò][

N¬à€€ú›⁄[ôŸYHô[‹ô\ìÿÿ[Xúò\öY\ ÿÿ[Xúò\ûQòY“YXúò\ûUXãô]\Ÿ]õÿÿ[Xúò\ûUXà	… N¬àÿÿ[Xúò\ûQòY–õÿ⁄’[ù[H]Kõõ› 
H
»L¬àÿÿ[Xúò\ûQòY“YH	…Œ¬àYä⁄[ôŸY
Hÿ]ôSÿÿ[Xúò\ûTŸ][ô‹ 
Kù[ä

HOàô[ô\ä
JKòÿ]⁄
\úàOàŸ]›]\ \úãõY\‹ÿYŸH	˘/Áykf9Ì(9ßd9n§˘£§πn£˘i,z-)I JN¬à[ŸHô[ô\ä
N¬àô]\õé¬àBà€€ú›õ‹H]ô[ùù\ôŸ]ò€‹Ÿ\›Àä	»ÿ\‹Ÿ]õ‹€ÿÿ[\ÿYõ‹›€‹öŸõ›—õ‹	 N¬àYäYõ‹
Hô]\õé¬à]ô[ùúô]ô[ùYò][

N¬àõ‹ò€\‹”\›úô[[›ôJ	ŸòYÀ[›ô\â N¬àYäõ‹öYOOH	€ÿÿ[\ÿYõ‹	 H\ÿYÿÿ[\‹Ÿ] ]ô[ùô]Uò[úŸô\ãôö[\ N¬à[ŸHYäõ‹öYOOH	›€‹öŸõ›—õ‹	 H\ÿY€‹öŸõ›—ö[\ ]ô[ùô]Uò[úŸô\ãôö[\ Kòÿ]⁄
\úàOàŸ]›]\ \úãõY\‹ÿYŸH	˘."π/(9i,z-)I JN¬à[ŸH\ÿYö[\ ]ô[ùô]Uò[úŸô\ãôö[\ Kòÿ]⁄
\úàOàŸ]›]\ \úãõY\‹ÿYŸH	˘."π/(9i,z-)I JN¬üJN¬úõ€›òY]ô[ù\›[ô\ä	ŸòYŸ[ô	À

HOà¬àYä[ÿÿ[Xúò\ûQòY“Y
Hô]\õé¬àÿÿ[Xúò\ûQòY“YH	…Œ¬àô[ô\ä
N¬üJN¬ù\ÿY[ú]ÀòY]ô[ù\›[ô\ä	ÿ⁄[ôŸIÀ]ô[ùOà¬à€€ú›ö[\»H]ô[ùù\ôŸ]ôö[\Œ¬àYäö[\œÀõ[ô›
^¬àYäX›]ôUXàOOH	€ÿÿ[	 H\ÿYÿÿ[\‹Ÿ] ö[\ N¬à[ŸHYäX›]ôUXàOOH	›€‹öŸõ›‹… H\ÿY€‹öŸõ›—ö[\ ö[\ Kòÿ]⁄
\úàOàŸ]›]\ \úãõY\‹ÿYŸH	˘."π/(9i,z-)I JN¬à[ŸH\ÿYö[\ ö[\ Kòÿ]⁄
\úàOàŸ]›]\ \úãõY\‹ÿYŸH	˘."π/(9i,z-)I JN¬àBà]ô[ùù\ôŸ]ùò[YHH	…Œ¬üJN¬ò\‹Ÿ]Xú—[ÀòY]ô[ù\›[ô\ä	ÿ€X⁄…À]ô[ùOà¬àYä]ô[ùù\ôŸ]ò€‹Ÿ\›Àä	÷Ÿ]KXõÿ\ôX‹ôX]WI J^»‹[ê‹ôX]SXúò\ûS[Ÿ[

N»ô]\õé»BàYä]ô[ùù\ôŸ]ò€‹Ÿ\›Àä	÷Ÿ]K\ôY⁄\›ûK\ôXﬁX€KXö[ã[‹[óI J^»‹[îôY⁄\›ûTôXﬁX€Pö[ä
Kòÿ]⁄
\úàOàŸ]›]\ \úãõY\‹ÿYŸH	˘¢d˘o 9fÁπ•-πÍÊyi,z-)I JN»ô]\õé»BàYä]ô[ùù\ôŸ]ò€‹Ÿ\›Àä	÷Ÿ]K][XõòZ[YŸ[ô\ò]‹ã[‹[óI J^»‹[ï[XõòZ[Ÿ[ô\ò]‹ä
Kòÿ]⁄
\úàOàŸ]›]\ \úãõY\‹ÿYŸH	˘¢d˘o 9Ô*yÂiyfÔπmÈyam˘i,z-)I JN»ô]\õé»BàYä]ô[ùù\ôŸ]ò€‹Ÿ\›Àä	÷Ÿ]K\ôYúô\⁄X\‹Ÿ]◊I J^»ÿY[

Kòÿ]⁄
\úàOàŸ]›]\ \úãõY\‹ÿYŸH	˘b-˘•¨:-a9.©˘i,z-)I JN»ô]\õé»BàYä]ô[ùù\ôŸ]ò€‹Ÿ\›Àä	÷Ÿ]K\›‹òYŸKY\ôX›‹öY\À[‹[óI J^»‹[î›‹òYŸTŸ][ô‹ 	Ÿ\ôX›‹öY\… Kòÿ]⁄
\úàOàŸ]›]\ \úãõY\‹ÿYŸH	˘¢d˘o 9/Áykf9ÊÎπoeyi,z-)I JN»ô]\õé»BàYä]ô[ùù\ôŸ]ò€‹Ÿ\›Àä	÷Ÿ]KXõÿ\ô\Ÿ][ô‹À[‹[óI J^»‹[î›‹òYŸTŸ][ô‹ 	ÿõÿ\ô… Kòÿ]⁄
\úàOàŸ]›]\ \úãõY\‹ÿYŸH	˘¢d˘o :-a9.©˙+ØπÔkπi,z-)I JN»ô]\õé»Bà€€ú›XàH]ô[ùù\ôŸ]ò€‹Ÿ\›Àä	÷Ÿ]K]XóI N¬àYäXäHX›]ò]P\‹Ÿ]õÿ\ô
Xãô]\Ÿ]ùXà	‹ôY⁄\›ûI Kòÿ]⁄
\úàOàŸ]›]\ \úãõY\‹ÿYŸH	˘¢d˘o 9Á"˘ßo˘i,z-)I JN¬üJN¬úõ€›òY]ô[ù\›[ô\ä	ÿ€X⁄…À\ﬁ[ò»]ô[ùOà¬àYä]ô[ùù\ôŸ]ò€‹Ÿ\›Àä	÷Ÿ]K\ôYúô\⁄X\‹Ÿ]◊I J^»ÿY[

Kòÿ]⁄
\úàOàŸ]›]\ \úãõY\‹ÿYŸH	˘b-˘•¨:-a9.©˘i,z-)I JN»ô]\õé»BàYä]ô[ùù\ôŸ]ò€‹Ÿ\›Àä	÷Ÿ]KX\‹Ÿ]X€€X›[‹[óI J^¬à€€ú›öYŸŸ\àH]ô[ùù\ôŸ]ò€‹Ÿ\›
	÷Ÿ]KX\‹Ÿ]X€€X›[‹[óI N¬àûH¬à€€ú›Z\àH]ÿZ]ô]⁄
	Àÿ\Kÿ\‹Ÿ]X€€X›‹Z\âÀ€Y]Ÿâ‘‘’	ﬂJN¬àYä\Z\ãõ⁄ Hõ›»ô]»\úõ‹ä	˘•Ë9¨Âz/Áπ£©yß+9ß.π•-∫f·πß#yb®I N¬à]‹Z\ó›⁄Ÿ[üHH]ÿZ]Z\ãöú€€ä
N¬à]Z\ë^\ô\»H]Kõõ› 
H
»L
àå
àL¬à€€ú›Y\\àH‹ôX]R€€X›Y\\ä\ﬁ[ò»
ããò\ô‹ HOà¬àYä]Kõõ› 
HèHZ\ë^\ô\ ^¬à€€ú›ô[ô]ŸYH]ÿZ]ô]⁄
	Àÿ\Kÿ\‹Ÿ]X€€X›‹Z\âÀ€Y]Ÿâ‘‘’	ﬂJN¬àYä\ô[ô]ŸYõ⁄ Hõ›»ô]»\úõ‹ä	˙acykÓymÏπi,y•b;Ô#:+Ì˙a„y•¨9Ên˘oeI N¬àZ\ó›⁄Ÿ[àH
]ÿZ]ô[ô]ŸYöú€€ä
JKúZ\ó›⁄Ÿ[é¬àZ\ë^\ô\»H]Kõõ› 
H
»L
àå
àL¬àBà€€ú››\õô\]Y\›HﬂWHH\ô‹Œ¬àYäô\]Y\›òõŸH[ú›[òŸ[Ÿàõ‹õQ]JHô\]Y\›òõŸKúŸ]
	‹Z\ó›⁄Ÿ[âÀZ\ó›⁄Ÿ[äN¬à[ŸHYä\[Ÿàô\]Y\›òõŸHOOH	‹›ö[ô… ^¬à€€ú›^[ÿYHî””ãú\úŸJô\]Y\›òõŸJN¬à^[ÿYúZ\ó›⁄Ÿ[àHZ\ó›⁄Ÿ[é¬àô\]Y\›òõŸHHî””ãú›ö[ô⁄YûJ^[ÿY
N¬àBàô\]Y\›öXY\ú»HÀããúô\]Y\›öXY\úÀ	÷P€€X›U⁄Ÿ[âŒúZ\ó›⁄Ÿ[üN¬à\ô‹»H›\õô\]Y\›N¬à€€ú›ô\‹€úŸHH]ÿZ]ô]⁄
ããò\ô‹ N¬àYäô\‹€úŸKõ⁄ ^¬àYä›ö[ô \õ
Kö[ò€Y\ 	Àÿ\‹Ÿ]X€€X›⁄õÿúÀ… J^¬à€€ú›õŸHH]ÿZ]ô\‹€úŸKöú€€ä
N¬àYäõŸKú›]\»OOH	ŸòZ[Y	»	âàõŸKõY\‹ÿYŸJHõŸKô\úõ‹óÿ€ŸHHõŸKõY\‹ÿYŸN¬àô]\õà€⁄ŒùùYKú€€éò\ﬁ[ò»

HOàõŸ_N¬àBàô]\õàô\‹€úŸN¬àBà€€ú›õŸHH]ÿZ]ô\‹€úŸKöú€€ä
Kòÿ]⁄


HOà
ﬂJJN¬à€€ú›]Z[HõŸKô]Z[ﬂN¬àô]\õà€⁄Œôò[ŸK›]\Œúô\‹€úŸKú›]\Àú€€éò\ﬁ[ò»

HOà
Ÿ\úõ‹óÿ€ŸNô]Z[ò€ŸKY\‹ÿYŸNô]Z[õY\‹ÿYŸ_J_N¬àJN¬à‹[ê\‹Ÿ]€€X››ô\õ^JöYŸŸ\ãÿY\\ãZ\ï⁄Ÿ[éúZ\ó›⁄Ÿ[üJN¬àHÿ]⁄
\úõ‹äH¬à⁄[ô›Àò[\ù
\úõ‹èÀõY\‹ÿYŸH	˘•Ë9¨Âz/Áπ£©yß+9ß.π•-∫f·πß#yb®I N¬àBàô]\õé¬àBà€€ú›‹ôX]HH]ô[ùù\ôŸ]ò€‹Ÿ\›Àä	÷Ÿ]K\ÿ‹ö\X‹ôX]WI N¬àYä‹ôX]J^»‹[ë\\€ŸT\[[ôQúõ€P\‹Ÿ]
‹õ⁄ôX›Yõÿÿ[›‹òYŸKôŸ]][J	›€‹ö‹‹XŸW‹õ⁄ôX›⁄Y	 H	…ﬂJN»ô]\õé»Bà€€ú›‹[àH]ô[ùù\ôŸ]ò€‹Ÿ\›Àä	÷Ÿ]K\ÿ‹ö\[‹[óI N¬à€€ú›€‹HH]ô[ùù\ôŸ]ò€‹Ÿ\›Àä	÷Ÿ]K\ÿ‹ö\X€‹WI N¬àYä[‹[à	âàX€‹JHô]\õé¬à€€ú›][HH
ÿ‹ö\\ò⁄]ôR][\Àõ[ô›»ÿ‹ö\\ò⁄]ôR][\»àôXYÿ‹ö\€‹ö‹‹XŸP\‹Ÿ] 
JKôö[ô
ò[YHOà›ö[ô ò[YKöY	… HOOH›ö[ô 
‹[à€‹JKô]\Ÿ]úÿ‹ö\‹[à
‹[à€‹JKô]\Ÿ]úÿ‹ö\€‹H	… JN¬àYäZ][JHô]\õé¬àYä‹[ä^»‹[ë\\€ŸT\[[ôQúõ€P\‹Ÿ]
][JN»ô]\õé»Bàò]öYÿ]‹ãò€\õÿ\ôÀù‹ö]U^
›ö[ô ][Kò€€ù[ù	… JKù[ä

HOàŸ]›]\ 	˘bi˘ß+9´h˘•°˘mÏπi#yb-â JKòÿ]⁄


HOàŸ]›]\ 	˘i#yb-πi,z-){Ô#:+Ì˘®‡9ßÈybj∫--9ßo˘ß`˙fd	 JN¬üJN¬ò\‹Ÿ]Xú—[ÀòY]ô[ù\›[ô\ä	⁄Ÿ^Y›€âÀ]ô[ùOà¬à€€ú››\úô[ùH]ô[ùù\ôŸ]ò€‹Ÿ\›Àä	÷Ÿ]K]XóI N¬àYäX›\úô[ùV…–\úõ›”Yù	À	–\úõ›‘öY⁄	À	“€YIÀ	—[ô	◊Kö[ò€Y\ ]ô[ùöŸ^JJHô]\õé¬à]ô[ùúô]ô[ùYò][

N¬à€€ú›YH›\úô[ùô]\Ÿ]ùXà	…Œ¬àYä]ô[ùò[Ÿ^H	âà…–\úõ›”Yù	À	–\úõ›‘öY⁄	◊Kö[ò€Y\ ]ô[ùöŸ^JJ^¬à[›ôP\‹Ÿ]õÿ\ôûRŸ^Xõÿ\ô
Y]ô[ùöŸ^HOOH	–\úõ›‘öY⁄	»»HàLJN¬àô]\õé¬àBà€€ú›Xú»HÀããò\‹Ÿ]Xú—[ú]Y\ûTŸ[X›‹ê[
	÷Ÿ]K]XóI WN¬à€€ú›[ô^HXúÀö[ô^Ÿä›\úô[ù
N¬à€€ú›ô^[ô^H]ô[ùöŸ^HOOH	“€YI»»à]ô[ùöŸ^HOOH	—[ô	»»XúÀõ[ô›HHà
[ô^
»
]ô[ùöŸ^HOOH	–\úõ›‘öY⁄	»»HàLJH
»XúÀõ[ô›
H	HXúÀõ[ô›¬àXú÷€ô^[ô^OÀôõÿ›\ 
N¬àXú÷€ô^[ô^OÀò€X⁄ 
N¬üJN¬ò\‹Ÿ]Xú—[ÀòY]ô[ù\›[ô\ä	ŸòY‹›\ù	À]ô[ùOà¬à€€ú›XàH]ô[ùù\ôŸ]ò€‹Ÿ\›Àä	÷Ÿ]KXõÿ\ôYòY◊I N¬àYä]XäHô]\õé¬àõÿ\ôòY“YHXãô]\Ÿ]òõÿ\ôòY»	…Œ¬à]ô[ùô]Uò[úŸô\èÀúŸ]]J	›^‹Z[âÀõÿ\ôòY“Y
N¬àYä]ô[ùô]Uò[úŸô\äH]ô[ùô]Uò[úŸô\ãôYôôX›[›ŸYH	€[›ôIŒ¬àXãò€\‹”\›òY
	ŸòYŸ⁄[ô… N¬üJN¬ò\‹Ÿ]Xú—[ÀòY]ô[ù\›[ô\ä	ŸòY€›ô\âÀ]ô[ùOà¬à€€ú›\ôŸ]H]ô[ùù\ôŸ]ò€‹Ÿ\›Àä	÷Ÿ]K]XóI N¬àYäXõÿ\ôòY“Y]\ôŸ]\ôŸ]ô]\Ÿ]ùXàOOHõÿ\ôòY“Y
Hô]\õé¬à]ô[ùúô]ô[ùYò][

N¬àYä]ô[ùô]Uò[úŸô\äH]ô[ùô]Uò[úŸô\ãôõ‹YôôX›H	€[›ôIŒ¬à\‹Ÿ]Xú—[ú]Y\ûTŸ[X›‹ê[
	ÀôòYÀ]\ôŸ]	 Kôõ‹ëXX⁄
][HOà][Kò€\‹”\›úô[[›ôJ	ŸòYÀ]\ôŸ]	 JN¬à\ôŸ]ò€\‹”\›òY
	ŸòYÀ]\ôŸ]	 N¬üJN¬ò\‹Ÿ]Xú—[ÀòY]ô[ù\›[ô\ä	Ÿõ‹	À]ô[ùOà¬à€€ú›\ôŸ]H]ô[ùù\ôŸ]ò€‹Ÿ\›Àä	÷Ÿ]K]XóI N¬àYäXõÿ\ôòY“Y]\ôŸ]
Hô]\õé¬à]ô[ùúô]ô[ùYò][

N¬à€€ú›[›ôYYHõÿ\ôòY“Y¬à€€ú›⁄[ôŸYHô[‹ô\ê\‹Ÿ]õÿ\ô
[›ôYY\ôŸ]ô]\Ÿ]ùXà	… N¬àõÿ\ôòY“YH	…Œ¬àYä⁄[ôŸY
^»ô[ô\êõÿ\ôò]öYÿ][€ä
N»ô\]Y\›[ö[X][€ëúò[YJ

HOà\‹Ÿ]Xú—[ú]Y\ûTŸ[X›‹äŸ]K]XèHâ–‘‘Àô\ÿÿ\J[›ôYY
_HóX
OÀôõÿ›\ 
JN»Ÿ]›]\ 	˘Á"˘ßo˙hnπn£˘mÏπ/Áykf	 N»BüJN¬ò\‹Ÿ]Xú—[ÀòY]ô[ù\›[ô\ä	ŸòYŸ[ô	À

HOà¬àõÿ\ôòY“YH	…Œ¬à\‹Ÿ]Xú—[ú]Y\ûTŸ[X›‹ê[
	ÀôòYŸ⁄[ôÀôòYÀ]\ôŸ]	 Kôõ‹ëXX⁄
][HOà][Kò€\‹”\›úô[[›ôJ	ŸòYŸ⁄[ô…À	ŸòYÀ]\ôŸ]	 JN¬üJN¬ù⁄[ô›ÀòY]ô[ù\›[ô\ä	€Y\‹ÿYŸIÀ]ô[ùOà¬àYä]ô[ùõ‹öY⁄[à	âà]ô[ùõ‹öY⁄[àOOHÿÿ][€ãõ‹öY⁄[äHô]\õé¬àYä]ô[ùô]OÀù\HOOH	Ÿ›À]›X⁄ò\ãXX›[€â ^¬àYä]ô[ùõ‹öY⁄[àOOHÿÿ][€ãõ‹öY⁄[à]ô[ùú€›\òŸHOOH⁄[ô›Àú\ô[ù]ô[ùô]Kùô\ú⁄[€àOOHJHô]\õé¬àYä]ô[ùô]KòX›[€ó⁄YOOH	ÿ\‹Ÿ]õ‹[ãY]Z[	»	âàŸ[X›YôY⁄\›ûP\‹Ÿ]Y
^¬à‹[îôY⁄\›ûP\‹Ÿ]
Ÿ[X›YôY⁄\›ûP\‹Ÿ]Y
Kòÿ]⁄
\úàOàŸ]›]\ \úãõY\‹ÿYŸH	˘¢d˘o 9Ì(9ßd9i,z-)I JN¬àô]\õé¬àBàYä]ô[ùô]KòX›[€ó⁄YOOH	ÿ\‹Ÿ]òY]ÀXÿ[ùò\…»	âàŸ[X›YôY⁄\›ûP\‹Ÿ]Y
^¬àYäXÿ[êYôY⁄\›ûP\‹Ÿ]–ÿ[ùò\ 
J^¬àŸ]›]\ 	˘od˘bcz-)π¢-˘¨®yß"yl!πÌ(9ßd9b®9aiyÂ.˘n ˘Ê°9ß`˙fd	 N¬àô]\õé¬àBàôY⁄\›ûTŸ[X›YYÀòY
Ÿ[X›YôY⁄\›ûP\‹Ÿ]Y
N¬àôY⁄\›ûSX[òYŸS[ŸHHùYN¬àôY⁄\›ûPù[‘[ô[H	ÿÿ[ùò\…Œ¬àô[ô\ä
N¬àBàô]\õé¬àBàYä]ô[ùô]OÀù\HOOH	Ÿ›À]›X⁄ò\ã\ô\]Y\›	 ^¬àYä]ô[ùõ‹öY⁄[àOOHÿÿ][€ãõ‹öY⁄[à]ô[ùú€›\òŸHOOH⁄[ô›Àú\ô[ù]ô[ùô]Kùô\ú⁄[€àOOHJHô]\õé¬àÀ»H⁄[X^H]ôHZ\‹ŸYH[ö]X[ÿ\Xö[]H⁄[H\»Yúò[YBàÀ»ÿ\»[òX›]ôKàô\‹ùH›\úô[ùÿ[ö]^ôY€€ù^\»H[ô⁄ZŸKÇàÿ⁄Y[P\‹Ÿ]›X⁄ò\îô\‹ù

N¬àô]\õé¬àBàYä]ô[ùô]OÀù\HOOH	‹›Y[À][YI H⁄[ô›Àî›Y[’[YOÀò\OÀä]ô[ùô]Kù[YJN¬àYä]ô[ùô]OÀù\HOOH	ÿ\‹Ÿ]\ô]\õãZ€YI Hô]\õê\‹Ÿ]X[òYŸ\í€YJ
N¬àYä]ô[ùô]OÀù\HOOH	ÿ\‹Ÿ]Y€ÿò[\ŸX\ò⁄	 ^¬à€€ú›\‹Ÿ]YH›X⁄ò\î›XõRY
]ô[ùô]Kò\‹Ÿ]⁄Y
N¬àYä]ô[ùõ‹öY⁄[àOOHÿÿ][€ãõ‹öY⁄[à]ô[ùú€›\òŸHOOH⁄[ô›Àú\ô[ùX\‹Ÿ]Y
Hô]\õé¬àÀ»UîKUªÔ&πhÏ˘l`à€[öXò\à9Ê°:eg9i-9doy.+y/&πn)π."à›\ù‹ŸX˚Ô#9ÊÌ9£©yi#yÂ*ÃNUH:-Ï˘n)¯‡ Çà€€ú›ò]‘›\ùH]ô[ùô]Kú›\ù‹ŸXŒ¬à€€ú››\ùŸX»Hò]‘›\ùOHù[ò]‘›\ùOOH	…»»ù[àù[Xô\äò]‘›\ù
N¬à‹[îôY⁄\›ûP\‹Ÿ]úõ€Q€ÿò[ŸX\ò⁄
\‹Ÿ]Yù[Xô\ãö\—ö[ö]J›\ùŸX H»X]õX^
›\ùŸX Hàù[
Bàòÿ]⁄
\úàOàŸ]›]\ \úãõY\‹ÿYŸH	˘¢d˘o 9Ì(9ßd9i,z-)I JN¬àBàYä]ô[ùô]OÀù\HOOH	ÿ\‹Ÿ]\ôYúô\⁄	 ^¬àÿY[

Kòÿ]⁄
\úàOàŸ]›]\ \úãõY\‹ÿYŸH	˘b-˘•¨:-a9.©˘i,z-)I JN¬àBàYä]ô[ùô]OÀù\HOOH	ÿ\‹Ÿ]\ﬁ[ò… ^¬àÀ»9b!˘fÁ∫hmyÎoèz/n˙a„˘hß∫a„˘d#9´i{Ô&˘•¨9Ì(9ßd:!Íπb™9£‰πaiy/aπ.#yb™9mÏπb®:/oya°ykÆ{Ô"9aj:a„˘b-˘•¨:+Îy.by/ÁyÂfyg*\‹Ÿ]\ôYúô\⁄;Ô"Bàﬁ[ò‘ôY⁄\›ûS€îô]ôX[

Kòÿ]⁄
\úàOàŸ]›]\ \úãõY\‹ÿYŸH	˘hß∫a„˘d#9´iz-a9.©˘i,z-)I JN¬àBüJN¬ù⁄[ô›ÀòY]ô[ù\›[ô\ä	Ÿ›Œõ€[öXò\ã\ô\›[\Ÿ[X›	À
]ô[ù
HOà¬à€€ú›]Z[H]ô[ùÀô]Z[ﬂN¬à€€ú›ô\›[H]Z[úô\›[ﬂN¬à€€ú›\‹Ÿ]YHô\›[ò\‹Ÿ]⁄Yô\›[öY¬à]\ôŸ][YHHù[¬àYà
ô\›[ú›\ù‹ŸX»OHù[
H\ôŸ][YHHù[Xô\äô\›[ú›\ù‹ŸX N¬à[ŸHYà
ô\›[ù[YHOHù[
H\ôŸ][YHHù[Xô\äô\›[ù[YJN¬à[ŸHYà
\[Ÿàô\›[öôYàOOH	‹›ö[ô…»	âàô\›[öôYãö[ò€Y\ 	»›I JH¬à€€ú›X]⁄Hô\›[öôYãõX]⁄
»›J◊óJ K N¬àYà
X]⁄
H\ôŸ][YHHù[Xô\äX]⁄ÃWJN¬àBàYà
\‹Ÿ]Y
H¬à‹[îôY⁄\›ûP\‹Ÿ]úõ€Q€ÿò[ŸX\ò⁄
\‹Ÿ]Y\ôŸ][YJKòÿ]⁄
\úàOà¬à€€ú€€Kô\úõ‹ä\úäN¬àŸ]›]\ \úãõY\‹ÿYŸH	˘¢d˘o 9Ì(9ßd9i,z-)I N¬àJN¬àBüJN¬ãÀ»\‹Ÿ]\ô]öY]Àöú»ô[XZ[ú»H€\‹⁄X»ÿ‹ö\\ö[ô»H[ò‹ô[Y[ù[L»ZY‹ò][€ãÇãÀ»ŸY\HYŸH€ÿò[»]€€ú›[Y\»]òZ[XõHYù\à\‹Ÿ][X[òYŸ\ãöú»ôX€€Y\»H[Ÿ[KÇìÿöôX›ôYö[ôTõ‹\ùY\ ⁄[ô›À¬àôXY›‹ôYõ€€X[éûÿ€€ôöY›\òXõNùùYKò[YNúôXY›‹ôYõ€€X[üKàôXY›‹ôYù[Xô\éûÿ€€ôöY›\òXõNùùYKò[YNúôXY›‹ôYù[Xô\üKàŸ[X›YôY⁄\›ûP\‹Ÿ]ûÿ€€ôöY›\òXõNùùYKò[YNúŸ[X›YôY⁄\›ûP\‹Ÿ]KàôY⁄\›ûTŸ[X›YYŒûÿ€€ôöY›\òXõNùùYKŸ]ä
HOàôY⁄\›ûTŸ[X›YYﬂKàõ‹õX]öY[‘ŸY€Y[ù[YNûÿ€€ôöY›\òXõNùùYKò[YNôõ‹õX]öY[‘ŸY€Y[ù[Y_Kàõ‹õX]öY[‘ŸY€Y[ùò[ôŸNûÿ€€ôöY›\òXõNùùYKò[YNôõ‹õX]öY[‘ŸY€Y[ùò[ôŸ_Kà[Y€ï[YU—úŒûÿ€€ôöY›\òXõNùùYKò[YNò[Y€ï[YU—úﬂKàö[ôX]⁄YöY[‘ŸY€Y[ùûÿ€€ôöY›\òXõNùùYKò[YNôö[ôX]⁄YöY[‘ŸY€Y[ùKà[ú›\ôSYYXTô]öY]‘^Y\éûÿ€€ôöY›\òXõNùùYKò[YNô[ú›\ôSYYXTô]öY]‘^Y\üKàŸYZ—]Z[öY[’Œûÿ€€ôöY›\òXõNùùYKò[YNúŸYZ—]Z[öY[’ﬂKà‹[îôY⁄\›ûP\‹Ÿ]úõ€Q€ÿò[ŸX\ò⁄ûÿ€€ôöY›\òXõNùùYKò[YNõ‹[îôY⁄\›ûP\‹Ÿ]úõ€Q€ÿò[ŸX\ò⁄KüJN¬öYäY\ôX›‹ûTŸ][ô‹‘YŸJHô[ô\êõÿ\ôò]öYÿ][€ä
N¬ôÿ›[Y[ùòY]ô[ù\›[ô\ä	—”P€€ù[ùÿYY	À

HOà¬àYä\ôX›‹ûTŸ][ô‹‘YŸJ^»ÿY€ÿò[\ôX›‹ûTŸ][ô‹ 
N»ô]\õé»BàÿYôY⁄\›ûTö[ò⁄\[

N¬àÿY[

Kù[ä›\ùôY⁄\›ûP]]€X]X“[ô^€[ô Kòÿ]⁄
\úàOàŸ]›]\ \úãõY\‹ÿYŸH	˘b®:/oyi,z-)I JN¬üJN¬