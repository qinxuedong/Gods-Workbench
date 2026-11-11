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

// QIN-591：资产库延后域模块（media-canvas）。首屏不加载；经 GW_DEFERRED 注册函数面，
// 由主文件网关（gwCall/gwLoad）按需加载或在空闲时段预热。函数体除跨文件赋值点改写外保持原样。

import {
    ASSET_DETAIL_MEDIA_LIBRARY_COLLAPSED_KEY,
    ASSET_DETAIL_MEDIA_LIBRARY_WIDTH_KEY,
    ASSET_DETAIL_MEDIA_MINIMAP_VISIBLE_KEY,
    DETAIL_MEDIA_CANVAS_GROUP_COLORS,
    activeRegistryVisibleAssets,
    activeTab,
    alignTimeToFps,
    apiJsonResponse,
    assetBoardById,
    assetPreviewUrl,
    assetThumb,
    assetVideoUrl,
    boardFolderSelection,
    boardGroupSelection,
    boardReferenceCanvasSelection,
    boardSourceSelection,
    canvasAssetsData,
    customBoardFolderTree,
    detailActiveImageItem,
    detailImageEditorState,
    detailImageVersionAssetId,
    detailMediaCanvasAnnotationSnapshot,
    detailMediaCanvasAnnotations,
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
    detailMediaCanvasSelectedIds,
    detailMediaCanvasSelectionObjectId,
    detailMediaCanvasSelectionOrder,
    detailMediaCanvasSpacePressed,
    detailMediaLibraryDragId,
    detailViewerItem,
    detailViewerState,
    detailViewerType,
    detailViewerUrl,
    detailViewerVideo,
    escapeAttr,
    escapeHtml,
    gwSetDetailMediaCanvasLibraryWidth,
    gwSetDetailMediaCanvasMinimapVisible,
    loadDetailImageVersions,
    localAssets,
    localAssetsTotal,
    normalizeRegistryAsset,
    refreshIcons,
    registryAssetExtension,
    registryAssetFolderPath,
    registryAssetMatchesFolder,
    registryAssets,
    registryBoardCustomGroups,
    registryDetail,
    registryFacets,
    registrySelectedIds,
    registryTotal,
    registryTotalKnown,
    root,
    selectedAssetIds,
    selectedCanvasAssetIds,
    selectedLocalIds,
    selectedLocalUploadIds,
    setActiveCanvasAssetCanvasId,
    setActiveCanvasAssetCategory,
    setCanvasAssetsData,
    setDetailMediaCanvasAnnotationSnapshot,
    setDetailMediaCanvasColorSnapshot,
    setDetailMediaCanvasEditState,
    setDetailMediaCanvasLibraryCollapsed,
    setDetailMediaCanvasPanState,
    setDetailMediaLibraryDragId,
    setLocalAssets,
    setLocalAssetsLoaded,
    setLocalAssetsTotal,
    setRegistryAssets,
    setRegistryCoverflowCurrentAssetId,
    setRegistryDetail,
    setRegistryTotal,
    setSelectedRegistryAssetId,
    setStatus,
    showAssetFailure,
    showAssetNotice,
    syncRegistrySelectionDom,
    workflowThumb,
} from '../asset-manager.js';
import assetManagerApi from './api.js';
import {
    formatDate,
} from './formatters.js';
import {
    workflowSourceId,
} from './workflow-assets.js';
import {
    normalizeCanvasAssetState,
} from './canvas-bridge.js';
import {
    detailReferenceCanvasCoverflowAssets,
} from './coverflow-bridge.js';
import {
    detailMediaLutMap,
    renderDetailMediaLutButton,
    scheduleDetailMediaLutPaint,
    syncDetailMediaNodeLutScale,
} from './lut.js';
import {
    customAssetLibraryAllItems,
} from './organize.js';
import {
    detailViewerItems,
    detailViewerMetaRows,
    detailViewerSyncSelection,
    detailViewerTypeLabel,
    hydrateDetailRegistryAsset,
    loadDetailViewerFileInfo,
    positionDetailMediaVideoDock,
    renderDetailImageEditor,
    renderDetailMediaVideoControls,
    renderDetailViewer,
    renderDetailViewerInspectorContent,
    renderMediaNodeParametricSvg,
    scheduleDetailMediaVideoDockPosition,
    showDetailPreview,
    switchDetailImageEditorAsset,
} from './preview.js';
import {
    signalFlowAccentColor,
} from './settings.js';
import {
    detailViewerTags,
} from './tags.js';
import {
    activeLocalLibraryPath,
} from './upload.js';
import { GW_DEFERRED } from './state.js';

let detailMediaCanvasLibraryResizeState = null;
let detailMediaCanvasLibraryRootLoading = '';
let detailMediaCanvasResizeObserver = null;
let detailMediaCanvasObservedViewport = null;
function handleCoverflowChange(event){
    const detail = event.detail || {};
    const mode = String(detail.mode || 'fullscreen');
    const source = String(detail.source || 'registry');
    const id = String(detail.assetId || detail.currentAssetId || '').trim();
    if(!id) return;
    if(mode === 'editor'){
        if(detailImageEditorState && source === detailViewerState.source){
            detailImageEditorState.coverflowAssetId = id;
            switchDetailImageEditorAsset(detail).catch(err => setStatus(err.message || '无法切换 Coverflow 当前图片'));
        } else if(detailViewerState.open && (detailViewerState.referenceCanvasMode || document.querySelector('[data-detail-media-canvas-viewport]')) && (source === detailViewerState.source || source === 'registry')){
            const item = detailMediaCanvasItems().find(value => String(detailMediaCanvasPersistId(value, source) || value.id || '') === id);
            detailViewerState.mediaCanvasLibrarySelectedId = id;
            if(item){
                detailViewerState.mediaCanvasActiveId = item.id;
                detailViewerState.mediaCanvasSelectedIds = new Set([item.id]);
                detailViewerState.mediaCanvasSelectionOrder = [item.id];
                syncDetailMediaCanvasActiveDom();
                refreshDetailMediaCanvasSidebarDetail();
            } else {
                refreshDetailMediaCanvasLibraryGrid();
            }
        }
        return;
    }
    if(source !== 'registry') return;
    setRegistryCoverflowCurrentAssetId(id);
    const item = detailViewerItem('registry', id) || registryAssets.find(value => value.id === id);
    if(item){
        setSelectedRegistryAssetId(id);
        setRegistryDetail(item);
        syncRegistrySelectionDom();
    }
}
function handleCoverflowAddToReferenceCanvas(event){
    const detail = event.detail || {};
    const assetId = String(detail.assetId || detail.currentAssetId || '').trim();
    if(
        !assetId
        || detail.mode !== 'editor'
        || detail.scope !== 'reference-canvas'
        || !detailViewerState.open
        || (!detailViewerState.referenceCanvasMode && !document.querySelector('[data-detail-media-canvas-viewport]'))
        || (String(detail.source || '') !== String(detailViewerState.source || '') && String(detail.source || '') !== 'registry')
    ) return;
    const viewport = document.querySelector('[data-detail-media-canvas-viewport]');
    const rect = viewport?.getBoundingClientRect();
    if(!rect){ setStatus('参考画布不可用，无法加入当前图片'); return; }
    const existing = detailMediaCanvasItems().find(item => (
        String(detailMediaCanvasPersistId(item, detailViewerState.source) || item.id || '') === assetId
    ));
    if(existing){
        activateDetailMediaCanvasItem(existing.id);
        setStatus('当前图片已在参考画布中');
        return;
    }
    // The Coverflow payload is authoritative for the selected asset. Keep it
    // as a fallback when a paged/structured library projection has not yet
    // surfaced the same stable ID in its local candidate list.
    addDetailMediaCanvasLibraryItem(assetId,rect.left+rect.width/2,rect.top+rect.height/2,detail.asset || null);
}
function customBoardReferenceCanvases(board, items=[]){
    const availableIds = new Set(items.map(item => item.id));
    return (canvasAssetsData.canvases || []).filter(canvas => canvas.kind === 'reference').map(canvas => {
        const canvasItems = (canvasAssetsData.items || []).filter(item => item.canvas_id === canvas.id && availableIds.has(String(item.asset_id || '')));
        return {...canvas, boardAssetCount:canvasItems.length};
    }).filter(canvas => canvas.boardAssetCount > 0);
}
function openReferenceCanvasDetail(canvasId){
    const item = (canvasAssetsData.items || []).find(value => value.canvas_id === canvasId && ['image','video'].includes(detailViewerType('canvas-asset',value)));
    if(!item){ setStatus('当前参考画布没有可预览的图片或视频'); return; }
    showDetailPreview('canvas-asset',item.id);
}
function paintAllDetailMediaLut(options = {}){
    Object.keys(detailMediaLutMap()).forEach(itemId => scheduleDetailMediaLutPaint(itemId, options));
}
function detailMediaCanvasSelection(source){
    if(source === 'registry') return registrySelectedIds;
    if(source === 'asset') return selectedAssetIds;
    if(source === 'localup') return selectedLocalUploadIds;
    if(source === 'local') return selectedLocalIds;
    if(source === 'canvas-asset') return selectedCanvasAssetIds;
    return new Set();
}
function initialDetailMediaCanvasItems(source, item){
    if(!['image','video'].includes(detailViewerType(source, item))) return [];
    if(source === 'canvas-asset' && item?.canvas_kind === 'reference' && item?.canvas_id){
        return (canvasAssetsData.items || []).filter(value => value.canvas_id === item.canvas_id && ['image','video'].includes(detailViewerType(source, value)));
    }
    const selectedIds = detailMediaCanvasSelection(source);
    const selected = detailViewerItems(source).filter(value => selectedIds.has(value.id) && ['image','video'].includes(detailViewerType(source, value)));
    const values = selected.length > 1 ? [...selected, item] : [item];
    const unique = new Map();
    values.forEach(value => { if(value?.id) unique.set(value.id, value); });
    return [...unique.values()];
}
function detailMediaCanvasPersistKey(item={}, source=detailViewerState.source){
    return String(
        detailMediaCanvasPersistId(item,source)
        || item.reference_key
        || item.referenceKey
        || item.node_id
        || item.id
        || ''
    ).trim();
}
function setDetailMediaCanvasSelection(ids=[], activeId=''){
    const validIds = new Set(detailMediaCanvasObjectIds());
    const order = [];
    ids.forEach(id => {
        const objectId = detailMediaCanvasSelectionObjectId(id);
        if(validIds.has(objectId) && !order.includes(objectId)) order.push(objectId);
    });
    detailViewerState.mediaCanvasSelectedIds = new Set(order);
    detailViewerState.mediaCanvasSelectionOrder = order;
    if(activeId) detailViewerState.mediaCanvasActiveId = activeId;
}
function detailMediaCanvasNaturalSize(item={}){
    const metadata = item.metadata && typeof item.metadata === 'object' ? item.metadata : {};
    let width = Number(item.natural_w || item.naturalWidth || metadata.width || item.width || 0);
    let height = Number(item.natural_h || item.naturalHeight || metadata.height || item.height || 0);
    if(!(width > 0) && height > 0) width = height * 16 / 9;
    if(!(height > 0) && width > 0) height = width * 9 / 16;
    if(!(width > 0) || !(height > 0)){
        width = detailViewerType(detailViewerState.source, item) === 'video' ? 1920 : 1600;
        height = detailViewerType(detailViewerState.source, item) === 'video' ? 1080 : 1000;
    }
    return {width:Math.max(80, Math.round(width)), height:Math.max(60, Math.round(height))};
}
function initialDetailMediaCanvasLayout(source, items=[]){
    const layout = {};
    if(source !== 'canvas-asset') return layout;
    items.forEach(item => {
        const width = Number(item.canvas_w || item.w || 0);
        const height = Number(item.canvas_h || item.h || 0);
        if(width > 0 && height > 0){
            layout[item.id] = {
                x:Number(item.canvas_x || 0),
                y:Number(item.canvas_y || 0),
                width:Math.max(80, width), height:Math.max(60, height), auto:false,
            };
        }
    });
    return layout;
}
function initialDetailMediaCanvasDrawings(source, item, items=[]){
    if(source !== 'canvas-asset' || item?.canvas_kind !== 'reference' || !Array.isArray(item.reference_drawings)) return [];
    const mediaIdByPersistId = new Map(items.map(value => [detailMediaCanvasPersistKey(value,source),value.id]).filter(([persistId]) => persistId));
    return item.reference_drawings.map(value => {
        const points = (value?.points || []).map(point => ({x:Number(point?.x || 0),y:Number(point?.y || 0)}));
        const standalone = Boolean(value?.standalone);
        let boundTo = mediaIdByPersistId.get(String(value?.boundTo || value?.bound_to || '')) || '';
        if(!boundTo && !standalone && points.length){
            boundTo = [...items].reverse().find(candidate => {
                const x = Number(candidate.canvas_x || 0), y = Number(candidate.canvas_y || 0);
                const width = Number(candidate.canvas_w || candidate.w || 0), height = Number(candidate.canvas_h || candidate.h || 0);
                return width > 0 && height > 0 && points.some(point => point.x >= x && point.x <= x+width && point.y >= y && point.y <= y+height);
            })?.id || '';
        }
        return {
            ...value, boundTo, standalone:standalone && !boundTo,
            color:/^#[0-9a-f]{6}$/i.test(String(value?.color || '')) ? String(value.color).toLowerCase() : signalFlowAccentColor(),
            width:Math.max(1,Math.min(40,Number(value?.width || 3))), points,
        };
    }).filter(value => value.id && value.points.length > 1);
}
function initialDetailMediaCanvasGroups(source, item, items=[], annotations=[], drawings=[]){
    if(source !== 'canvas-asset' || item?.canvas_kind !== 'reference') return [];
    const mediaIdByPersistId = new Map(items.map(value => [detailMediaCanvasPersistKey(value,source),value.id]).filter(([persistId]) => persistId));
    const annotationIds = new Set(annotations.map(value => value.id));
    const drawingIds = new Set(drawings.map(value => value.id));
    const claimed = new Set();
    const groups = (Array.isArray(item.reference_groups) ? item.reference_groups : []).slice(0,100).map((raw,index) => {
        const memberIds = (raw?.member_ids || raw?.memberIds || []).map(value => {
            const id = String(value || '');
            return mediaIdByPersistId.get(id) || (annotationIds.has(id) || drawingIds.has(id) ? id : '');
        }).filter(value => value && !claimed.has(value)).filter((value,memberIndex,values) => values.indexOf(value) === memberIndex);
        memberIds.forEach(value => claimed.add(value));
        return {
            id:String(raw?.id || `group_${index+1}`).slice(0,80),
            memberIds,
            color:/^#[0-9a-f]{6}$/i.test(String(raw?.color || '')) ? String(raw.color).toLowerCase() : DETAIL_MEDIA_CANVAS_GROUP_COLORS[index % DETAIL_MEDIA_CANVAS_GROUP_COLORS.length],
            padding:Math.max(0,Math.min(240,Number(raw?.padding ?? 18))),
            transparent:Boolean(raw?.transparent),
        };
    }).filter(group => group.memberIds.length > 1);
    drawings.filter(drawing => drawing.boundTo).forEach(drawing => {
        if(claimed.has(drawing.id)) return;
        const existing = groups.find(group => group.memberIds.includes(drawing.boundTo));
        if(existing){
            existing.memberIds.push(drawing.id);
            claimed.add(drawing.id);
            return;
        }
        if(claimed.has(drawing.boundTo)) return;
        const group = {
            id:`group_bound_${String(drawing.boundTo).replace(/[^a-z0-9_-]/gi,'').slice(0,40)}_${groups.length+1}`,
            memberIds:[drawing.boundTo,drawing.id], color:'#173a5e', padding:0, transparent:true,
        };
        groups.push(group);
        claimed.add(drawing.boundTo); claimed.add(drawing.id);
    });
    return groups;
}
function detailMediaCanvasAnnotation(id){
    return detailMediaCanvasAnnotations().find(note => note.id === id) || null;
}
function detailMediaCanvasDrawing(id){
    return detailMediaCanvasDrawings().find(drawing => drawing.id === id) || null;
}
function detailMediaCanvasDrawingBox(drawingOrId){
    const drawing = typeof drawingOrId === 'string' ? detailMediaCanvasDrawing(drawingOrId) : drawingOrId;
    if(!drawing || !(drawing.points || []).length) return null;
    const points = drawing.points || [];
    const padding = Math.max(4,Number(drawing.width || 3));
    const minX = Math.min(...points.map(point => Number(point.x || 0))) - padding;
    const minY = Math.min(...points.map(point => Number(point.y || 0))) - padding;
    const maxX = Math.max(...points.map(point => Number(point.x || 0))) + padding;
    const maxY = Math.max(...points.map(point => Number(point.y || 0))) + padding;
    return {x:minX,y:minY,width:Math.max(8,maxX-minX),height:Math.max(8,maxY-minY),auto:false};
}
function detailMediaCanvasRawObjectBox(id){
    const note = detailMediaCanvasAnnotation(id);
    if(note) return {x:Number(note.x || 0),y:Number(note.y || 0),width:Number(note.width || 280),height:Number(note.height || 120),auto:false};
    const drawing = detailMediaCanvasDrawing(id);
    if(drawing) return detailMediaCanvasDrawingBox(drawing);
    return detailViewerState.mediaCanvasLayout?.[id] || null;
}
function detailMediaCanvasGroupBox(groupOrId){
    const group = typeof groupOrId === 'string' ? detailMediaCanvasGroup(groupOrId) : groupOrId;
    if(!group) return null;
    const boxes = (group.memberIds || []).map(detailMediaCanvasRawObjectBox).filter(Boolean);
    if(!boxes.length) return null;
    const padding = Math.max(group.transparent ? 0 : 4,Number(group.padding ?? 18));
    const minX = Math.min(...boxes.map(box => Number(box.x || 0)));
    const minY = Math.min(...boxes.map(box => Number(box.y || 0)));
    const maxX = Math.max(...boxes.map(box => Number(box.x || 0)+Number(box.width || 0)));
    const maxY = Math.max(...boxes.map(box => Number(box.y || 0)+Number(box.height || 0)));
    return {x:minX-padding,y:minY-padding,width:maxX-minX+padding*2,height:maxY-minY+padding*2,auto:false};
}
function detailMediaCanvasObjectBox(id){
    return detailMediaCanvasGroupBox(id) || detailMediaCanvasRawObjectBox(id);
}
function setDetailMediaCanvasRawObjectBox(id, box){
    const note = detailMediaCanvasAnnotation(id);
    if(note){
        const previousWidth = Math.max(1,Number(note.width || 280));
        const previousHeight = Math.max(1,Number(note.height || 120));
        const nextWidth = Math.max(120,Number(box.width || 280));
        const nextHeight = Math.max(64,Number(box.height || 120));
        if(Math.abs(nextWidth-previousWidth) > .01 || Math.abs(nextHeight-previousHeight) > .01){
            const scale = Math.max(.1,Math.sqrt((nextWidth/previousWidth)*(nextHeight/previousHeight)));
            note.fontSize = Math.max(8,Math.min(160,Number(note.fontSize || 15)*scale));
            note.padding = Math.max(2,Math.min(80,Number(note.padding || 10)*scale));
        }
        note.x = Number(box.x || 0); note.y = Number(box.y || 0);
        note.width = nextWidth; note.height = nextHeight;
        return;
    }
    const drawing = detailMediaCanvasDrawing(id);
    if(drawing){
        const previous = detailMediaCanvasDrawingBox(drawing);
        if(!previous) return;
        const nextWidth = Math.max(8,Number(box.width || previous.width));
        const nextHeight = Math.max(8,Number(box.height || previous.height));
        const scaleX = nextWidth / Math.max(1,previous.width);
        const scaleY = nextHeight / Math.max(1,previous.height);
        drawing.points = (drawing.points || []).map(point => ({
            x:Number(box.x || 0)+(Number(point.x || 0)-previous.x)*scaleX,
            y:Number(box.y || 0)+(Number(point.y || 0)-previous.y)*scaleY,
        }));
        drawing.width = Math.max(1,Math.min(40,Number(drawing.width || 3)*Math.sqrt(scaleX*scaleY)));
        return;
    }
    if(detailViewerState.mediaCanvasLayout?.[id]) detailViewerState.mediaCanvasLayout[id] = {...box};
}
function setDetailMediaCanvasObjectBox(id, box){
    const group = detailMediaCanvasGroup(id);
    if(!group){ setDetailMediaCanvasRawObjectBox(id,box); return; }
    const memberBoxes = (group.memberIds || []).map(memberId => ({id:memberId,box:{...(detailMediaCanvasRawObjectBox(memberId) || {})}})).filter(value => value.box.width > 0 && value.box.height > 0);
    if(!memberBoxes.length) return;
    const padding = Math.max(group.transparent ? 0 : 4,Number(group.padding ?? 18));
    const minX = Math.min(...memberBoxes.map(value => Number(value.box.x || 0)));
    const minY = Math.min(...memberBoxes.map(value => Number(value.box.y || 0)));
    const maxX = Math.max(...memberBoxes.map(value => Number(value.box.x || 0)+Number(value.box.width || 0)));
    const maxY = Math.max(...memberBoxes.map(value => Number(value.box.y || 0)+Number(value.box.height || 0)));
    const sourceWidth = Math.max(1,maxX-minX);
    const sourceHeight = Math.max(1,maxY-minY);
    const outerWidth = sourceWidth+padding*2;
    const outerHeight = sourceHeight+padding*2;
    const scaleX = Math.max(.01,Number(box.width || outerWidth)/outerWidth);
    const scaleY = Math.max(.01,Number(box.height || outerHeight)/outerHeight);
    const scale = Math.max(.01,Math.min(scaleX,scaleY));
    const nextPadding = Math.max(group.transparent ? 0 : 4,Math.min(240,padding*scale));
    group.padding = nextPadding;
    const targetX = Number(box.x || 0)+nextPadding;
    const targetY = Number(box.y || 0)+nextPadding;
    memberBoxes.forEach(value => {
        setDetailMediaCanvasRawObjectBox(value.id,{
            ...value.box,
            x:targetX+(Number(value.box.x || 0)-minX)*scale,
            y:targetY+(Number(value.box.y || 0)-minY)*scale,
            width:Number(value.box.width || 0)*scale,
            height:Number(value.box.height || 0)*scale,
            auto:false,
        });
    });
}
function captureDetailMediaCanvasSnapshot(){
    const selectionOrder = detailMediaCanvasSelectionOrder();
    return {
        items:[...detailMediaCanvasItems()],
        layout:JSON.parse(JSON.stringify(detailViewerState.mediaCanvasLayout || {})),
        annotations:JSON.parse(JSON.stringify(detailMediaCanvasAnnotations())),
        drawings:JSON.parse(JSON.stringify(detailViewerState.mediaCanvasDrawings || [])),
        groups:JSON.parse(JSON.stringify(detailMediaCanvasGroups())),
        selectedIds:[...detailMediaCanvasSelectedIds()],
        selectionOrder:[...selectionOrder],
        activeId:detailViewerState.mediaCanvasActiveId || '',
        normalizeBasis:detailViewerState.mediaCanvasNormalizeBasis || 'first',
    };
}
function detailMediaCanvasSnapshotSignature(snapshot){
    return JSON.stringify({
        items:(snapshot.items || []).map(item => item.id), layout:snapshot.layout,
        annotations:snapshot.annotations, drawings:snapshot.drawings, groups:snapshot.groups,
    });
}
function pushDetailMediaCanvasHistory(snapshot=captureDetailMediaCanvasSnapshot()){
    const history = detailViewerState.mediaCanvasHistory || (detailViewerState.mediaCanvasHistory = []);
    const last = history[history.length - 1];
    if(last && detailMediaCanvasSnapshotSignature(last) === detailMediaCanvasSnapshotSignature(snapshot)) return;
    history.push(snapshot);
    if(history.length > 20) history.splice(0, history.length - 20);
}
function undoDetailMediaCanvas(){
    const history = detailViewerState.mediaCanvasHistory || [];
    const snapshot = history.pop();
    if(!snapshot){ setStatus('没有可撤销的画布操作'); return; }
    detailViewerState.mediaCanvasItems = [...(snapshot.items || [])];
    detailViewerState.mediaCanvasLayout = JSON.parse(JSON.stringify(snapshot.layout || {}));
    detailViewerState.mediaCanvasAnnotations = JSON.parse(JSON.stringify(snapshot.annotations || []));
    detailViewerState.mediaCanvasDrawings = JSON.parse(JSON.stringify(snapshot.drawings || []));
    detailViewerState.mediaCanvasGroups = JSON.parse(JSON.stringify(snapshot.groups || []));
    detailViewerState.mediaCanvasSelectedIds = new Set(snapshot.selectedIds || []);
    detailViewerState.mediaCanvasSelectionOrder = [...(snapshot.selectionOrder || snapshot.selectedIds || [])];
    detailViewerState.mediaCanvasActiveId = snapshot.activeId || '';
    detailViewerState.mediaCanvasNormalizeBasis = snapshot.normalizeBasis || 'first';
    detailViewerState.mediaCanvasEditingAnnotationId = '';
    detailViewerState.mediaCanvasFitted = true;
    renderDetailViewer();
    setStatus(`已撤销上一步，还可撤销 ${history.length} 步`);
}
function snapDetailMediaCanvasValue(value){
    return detailViewerState.mediaCanvasSnap ? Math.round(Number(value || 0) / 10) * 10 : Number(value || 0);
}
function arrangeDetailMediaCanvasItems(mode='grid', preserveSizes=true){
    const items = detailMediaCanvasItems();
    if(!items.length) return;
    const gap = 10;
    const layout = detailViewerState.mediaCanvasLayout || {};
    const sizes = items.map(item => {
        const existing = layout[item.id];
        const natural = detailMediaCanvasNaturalSize(item);
        return {
            item,
            width:preserveSizes && existing?.width > 0 ? existing.width : natural.width,
            height:preserveSizes && existing?.height > 0 ? existing.height : natural.height,
        };
    });
    const next = {...layout};
    if(mode === 'horizontal' || mode === 'vertical'){
        let cursor = 0;
        sizes.forEach(value => {
            next[value.item.id] = {
                x:mode === 'horizontal' ? cursor : 0,
                y:mode === 'vertical' ? cursor : 0,
                width:value.width, height:value.height, auto:true,
            };
            cursor += (mode === 'horizontal' ? value.width : value.height) + gap;
        });
    } else {
        const columns = Math.max(1, Math.ceil(Math.sqrt(sizes.length)));
        const rows = [];
        for(let index=0; index<sizes.length; index += columns) rows.push(sizes.slice(index,index + columns));
        const rowWidths = rows.map(row => row.reduce((total,value) => total + value.width, 0) + Math.max(0,row.length - 1) * gap);
        const canvasWidth = Math.max(...rowWidths, 1);
        let y = 0;
        rows.forEach((row,rowIndex) => {
            const rowHeight = Math.max(...row.map(value => value.height), 1);
            let x = (canvasWidth - rowWidths[rowIndex]) / 2;
            row.forEach(value => {
                next[value.item.id] = {x, y, width:value.width, height:value.height, auto:true};
                x += value.width + gap;
            });
            y += rowHeight + gap;
        });
    }
    Object.entries(next).forEach(([id,box]) => {
        if(layout[id]) setDetailMediaCanvasRawObjectBox(id,box);
        else layout[id] = box;
    });
    detailViewerState.mediaCanvasLayout = layout;
    detailViewerState.mediaCanvasFitted = false;
}
function ensureDetailMediaCanvasLayout(){
    const items = detailMediaCanvasItems();
    const layout = detailViewerState.mediaCanvasLayout || {};
    if(items.some(item => !(layout[item.id]?.width > 0 && layout[item.id]?.height > 0))) arrangeDetailMediaCanvasItems('grid', false);
}
function detailMediaCanvasMetrics(items=detailMediaCanvasItems()){
    ensureDetailMediaCanvasLayout();
    const layout = detailViewerState.mediaCanvasLayout || {};
    let minX = Infinity;
    let minY = Infinity;
    let maxX = -Infinity;
    let maxY = -Infinity;
    const includeBox = box => {
        const x = Number(box.x || 0), y = Number(box.y || 0);
        minX = Math.min(minX,x); minY = Math.min(minY,y);
        maxX = Math.max(maxX,x + Number(box.width || 0)); maxY = Math.max(maxY,y + Number(box.height || 0));
    };
    items.forEach(item => {
        const box = layout[item.id] || {};
        includeBox(box);
    });
    (detailViewerState.mediaCanvasAnnotations || []).forEach(note => {
        includeBox({x:note.x,y:note.y,width:note.width || 280,height:note.height || 120});
    });
    detailMediaCanvasGroups().forEach(group => {
        const box = detailMediaCanvasGroupBox(group);
        if(box) includeBox(box);
    });
    (detailViewerState.mediaCanvasDrawings || []).forEach(drawing => (drawing.points || []).forEach(point => {
        const x = Number(point.x || 0), y = Number(point.y || 0);
        minX = Math.min(minX,x - 20); minY = Math.min(minY,y - 20);
        maxX = Math.max(maxX,x + 20); maxY = Math.max(maxY,y + 20);
    }));
    if(!Number.isFinite(minX)){ minX = 0; minY = 0; maxX = 1; maxY = 1; }
    const width = Math.max(1,maxX-minX);
    const height = Math.max(1,maxY-minY);
    return {
        columns:Math.max(1, Math.ceil(Math.sqrt(Math.max(1, items.length)))),
        rows:Math.max(1, Math.ceil(items.length / Math.max(1, Math.ceil(Math.sqrt(Math.max(1, items.length)))))),
        gap:10, minX, minY, maxX, maxY, width, height,
    };
}

function renderDetailMediaCanvasNode(source, item){
    const type = detailViewerType(source, item);
    const currentUrl = detailViewerUrl(source, item);
    const assetId = detailMediaCanvasPersistId(item, source);
    const explicitVersion = /\/image-versions\/[^/?#]+\/media(?:\?|#|$)/i.test(currentUrl);
    const url = type === 'image' && assetId && !explicitVersion
        ? `/api/asset-registry/assets/${encodeURIComponent(assetId)}/media`
        : String(item.assetUrl || item.asset_url || item.original_url || currentUrl || '');
    if(!detailViewerState.mediaCanvasLayout[item.id]){
        detailViewerState.mediaCanvasLayout[item.id] = {x:0,y:0,...detailMediaCanvasNaturalSize(item)};
    }
    const box = detailViewerState.mediaCanvasLayout[item.id];
    const active = item.id === detailViewerState.mediaCanvasActiveId;
    const group = detailMediaCanvasGroupForMember(item.id);
    const objectId = group?.id || item.id;
    const grouped = Boolean(group);
    const selected = !grouped && detailMediaCanvasSelectedIds().has(item.id);
    const selectedViaGroup = Boolean(group && detailMediaCanvasSelectedIds().has(group.id));
    const previewFallback = type === 'workflow' ? '' : assetPreviewUrl(url, 2048, item);
    const videoSource = type === 'video' ? assetVideoUrl(url, item) : '';
    const jumpTarget = active && detailViewerState.startTime != null
        && Number.isFinite(Number(detailViewerState.startTime))
        ? alignTimeToFps(Number(detailViewerState.startTime), Number(item.frame_rate || item.metadata?.frame_rate || 0))
        : null;
    // 跳帧目标同时写进媒体地址：节点重建不会丢时间点，浏览器加载后直接从该帧起播。
    const videoSrcWithJump = jumpTarget != null && videoSource && !videoSource.includes('#t=')
        ? `${videoSource}#t=${jumpTarget}`
        : videoSource;
    const edit = (item.reference_edit && typeof item.reference_edit === 'object')
        ? item.reference_edit
        : (item.edit && typeof item.edit === 'object' ? item.edit : null);
    const media = type === 'workflow'
        ? workflowThumb(item)
        : type === 'video'
            ? `<video class="asset-detail-media-node-content asset-detail-player" src="${escapeAttr(videoSrcWithJump)}" playsinline preload="auto" data-detail-media-natural="${escapeAttr(item.id)}"></video>`
            : (edit
                ? renderMediaNodeParametricSvg(item, edit, url, previewFallback)
                : `<img class="asset-detail-media-node-content" src="${escapeAttr(url)}" data-detail-media-preview-fallback="${escapeAttr(previewFallback)}" data-preview-fallback="${escapeAttr(item.name || '图片')}" data-detail-media-natural="${escapeAttr(item.id)}" alt="${escapeAttr(item.name || '素材')}" draggable="false" loading="eager" decoding="async">`);
    const centerPlay = type === 'video'
        ? `<div class="asset-detail-video-center-play-socket" aria-hidden="true"><button class="asset-detail-video-center-play hw-play-capsule" type="button" data-detail-video-action="toggle" data-detail-video-for="${escapeAttr(item.id)}" aria-label="播放视频" title="播放 / 暂停（Space）"><i class="asset-detail-video-icon-play" data-lucide="play"></i><i class="asset-detail-video-icon-pause" data-lucide="pause"></i></button></div>`
        : '';
    const videoControls = type === 'video' ? renderDetailMediaVideoControls(item) : '';
    const handles = ['nw','n','ne','e','se','s','sw','w'].map(direction => `<span class="asset-detail-media-resize-handle handle-${direction}" data-detail-media-resize="${direction}" aria-hidden="true"></span>`).join('');
    return `<article class="asset-detail-media-node ${type === 'video' ? 'is-video' : ''} ${active ? 'is-active' : ''} ${selected ? 'is-selected' : ''} ${selectedViaGroup ? 'is-selected-via-group' : ''} ${grouped ? 'is-group-member' : ''}" data-detail-media-canvas-node="${escapeAttr(item.id)}" data-detail-media-object="${escapeAttr(objectId)}" ${grouped ? `data-detail-media-group-member="${escapeAttr(item.id)}"` : ''} style="left:${box.x}px;top:${box.y}px;width:${box.width}px;height:${box.height}px" tabindex="0" aria-label="选择 ${escapeAttr(item.name || '画布素材')}" aria-selected="${selected || selectedViaGroup}">
        <div class="asset-detail-media-node-clip ${type === 'video' ? 'viewport-bezel' : ''}">${media}${centerPlay}</div>${videoControls}${handles}
    </article>`;
}
function renderDetailMediaCanvasGroup(group){
    const box = detailMediaCanvasGroupBox(group);
    if(!box) return '';
    const selected = detailMediaCanvasSelectedIds().has(group.id);
    const handles = ['nw','n','ne','e','se','s','sw','w'].map(direction => `<span class="asset-detail-media-resize-handle handle-${direction}" data-detail-media-resize="${direction}" aria-hidden="true"></span>`).join('');
    return `<article class="asset-detail-media-group ${group.transparent ? 'is-transparent' : ''} ${selected ? 'is-selected' : ''}" data-detail-media-group-node="${escapeAttr(group.id)}" data-detail-media-object="${escapeAttr(group.id)}" style="left:${box.x}px;top:${box.y}px;width:${box.width}px;height:${box.height}px;--media-group-color:${escapeAttr(group.color || '#173a5e')}" tabindex="0" aria-label="图片组，包含 ${(group.memberIds || []).length} 个对象" aria-selected="${selected}">${handles}</article>`;
}
function detailMediaCanvasDrawingPath(points=[]){
    return points.map((point,index) => `${index ? 'L' : 'M'}${Number(point.x || 0).toFixed(1)},${Number(point.y || 0).toFixed(1)}`).join(' ');
}
function renderDetailMediaCanvasDrawing(drawing){
    const path = detailMediaCanvasDrawingPath(drawing.points || []);
    const color = drawing.color || signalFlowAccentColor();
    const width = Math.max(1,Number(drawing.width || 3));
    const box = detailMediaCanvasDrawingBox(drawing);
    if(!box) return '';
    return `<div class="asset-detail-media-drawing-layer" data-detail-media-drawing-node="${escapeAttr(drawing.id)}" style="left:${box.x}px;top:${box.y}px;width:${box.width}px;height:${box.height}px" aria-hidden="true"><svg width="100%" height="100%" viewBox="${box.x} ${box.y} ${box.width} ${box.height}" overflow="visible"><path d="${escapeAttr(path)}" style="--drawing-color:${escapeAttr(color)};--drawing-width:${width}"></path></svg></div>`;
}
function renderDetailMediaCanvasAnnotation(note){
    const objectId = detailMediaCanvasSelectionObjectId(note.id);
    const grouped = objectId !== note.id;
    const selected = !grouped && detailMediaCanvasSelectedIds().has(note.id);
    const editing = detailViewerState.mediaCanvasEditingAnnotationId === note.id;
    const backgroundColor = note.backgroundColor || '#202622';
    const textColor = note.textColor || '#ffffff';
    const fontSize = Math.max(8,Number(note.fontSize || 15));
    const padding = Math.max(2,Number(note.padding || 10));
    const handles = ['nw','n','ne','e','se','s','sw','w'].map(direction => `<span class="asset-detail-media-resize-handle handle-${direction}" data-detail-media-resize="${direction}" aria-hidden="true"></span>`).join('');
    return `<article class="asset-detail-media-annotation ${selected ? 'is-selected' : ''} ${editing ? 'is-editing' : ''} ${grouped ? 'is-group-member' : ''}" data-detail-media-annotation-node="${escapeAttr(note.id)}" data-detail-media-object="${escapeAttr(objectId)}" ${grouped ? `data-detail-media-group-member="${escapeAttr(note.id)}"` : ''} style="left:${Number(note.x || 0)}px;top:${Number(note.y || 0)}px;width:${Number(note.width || 280)}px;height:${Number(note.height || 120)}px;--annotation-bg:${escapeAttr(backgroundColor)};--annotation-text:${escapeAttr(textColor)};--annotation-font-size:${fontSize}px;--annotation-padding:${padding}px" tabindex="0" aria-label="画布注释" aria-selected="${selected}">
        ${editing ? `<textarea class="asset-detail-media-annotation-editor" data-detail-media-annotation="${escapeAttr(note.id)}" maxlength="2000" aria-label="编辑画布注释" placeholder="输入注释">${escapeHtml(note.text || '')}</textarea>` : `<div class="asset-detail-media-annotation-copy">${escapeHtml(note.text || '双击输入注释')}</div>`}
        ${handles}
    </article>`;
}
function renderDetailMediaCanvasToolButtons(){
    const selectedCount = detailMediaCanvasSelectedIds().size;
    const selectedGroupCount = detailMediaCanvasSelectionOrder().filter(id => detailMediaCanvasGroup(id)).length;
    const normalizeBasis = detailViewerState.mediaCanvasNormalizeBasis || 'first';
    return `<div class="asset-detail-media-tool-group" role="group" aria-label="画布创建工具">
        <button class="hw-btn" type="button" data-detail-media-context-action="annotation"><i data-lucide="message-square-text"></i><span>注释</span></button>
        <button class="hw-btn ${detailViewerState.mediaCanvasSnap ? 'is-active' : ''}" type="button" data-detail-media-context-action="snap" aria-pressed="${detailViewerState.mediaCanvasSnap}"><i data-lucide="magnet"></i><span>吸附网格</span></button>
    </div>
    <div class="asset-detail-media-tool-group" role="group" aria-label="排列">
        <button class="hw-btn" type="button" data-detail-media-context-action="arrange-grid" title="网格排列"><i data-lucide="grid-2x2"></i><span>网格</span></button>
        <button class="hw-btn" type="button" data-detail-media-context-action="arrange-horizontal" title="水平排列"><i data-lucide="rows-3"></i><span>横排</span></button>
        <button class="hw-btn" type="button" data-detail-media-context-action="arrange-vertical" title="垂直排列"><i data-lucide="columns-3"></i><span>竖排</span></button>
    </div>
    <div class="asset-detail-media-tool-group" role="group" aria-label="分组">
        <button class="hw-btn" type="button" data-detail-media-context-action="group" title="打组 Ctrl+G" ${selectedCount < 2 ? 'disabled' : ''}><i data-lucide="group"></i><span>打组</span></button>
        <button class="hw-btn" type="button" data-detail-media-context-action="ungroup" title="解组 Ctrl+Shift+G" ${!selectedGroupCount ? 'disabled' : ''}><i data-lucide="ungroup"></i><span>解组</span></button>
    </div>
    <div class="asset-detail-media-tool-group" role="group" aria-label="对齐">
        <button class="hw-btn" type="button" data-detail-media-context-action="align-left" title="左对齐"><i data-lucide="align-start-vertical"></i></button>
        <button class="hw-btn" type="button" data-detail-media-context-action="align-center-x" title="水平居中"><i data-lucide="align-center-vertical"></i></button>
        <button class="hw-btn" type="button" data-detail-media-context-action="align-right" title="右对齐"><i data-lucide="align-end-vertical"></i></button>
        <button class="hw-btn" type="button" data-detail-media-context-action="align-top" title="顶部对齐"><i data-lucide="align-start-horizontal"></i></button>
        <button class="hw-btn" type="button" data-detail-media-context-action="align-center-y" title="垂直居中"><i data-lucide="align-center-horizontal"></i></button>
    </div>
    <div class="asset-detail-media-tool-group" role="group" aria-label="规格化">
        <label class="asset-detail-media-normalize-basis" title="规格化尺寸基准"><span>基准</span><select data-detail-media-normalize-basis aria-label="规格化尺寸基准"><option value="first" ${normalizeBasis === 'first' ? 'selected' : ''}>第一个</option><option value="average" ${normalizeBasis === 'average' ? 'selected' : ''}>平均</option><option value="last" ${normalizeBasis === 'last' ? 'selected' : ''}>最后一个</option></select></label>
        <button class="hw-btn" type="button" data-detail-media-context-action="normalize-width" title="统一宽度"><i data-lucide="move-horizontal"></i></button>
        <button class="hw-btn" type="button" data-detail-media-context-action="normalize-height" title="统一高度"><i data-lucide="move-vertical"></i></button>
        <button class="hw-btn" type="button" data-detail-media-context-action="normalize-original" title="恢复原始尺寸"><i data-lucide="maximize"></i></button>
    </div>
    <div class="asset-detail-media-note-colors" aria-label="注释颜色">
        <label title="注释背景色"><span>背景</span><input type="color" data-detail-annotation-color="background" value="${escapeAttr(detailMediaCanvasSelectedAnnotationColor('background'))}"></label>
    </div>
    <!-- 【QIN-580】原「文字」控件（注释文字色）位置改为 LUT 入口。 -->
    ${renderDetailMediaLutButton()}
    <div class="asset-detail-media-tool-group asset-detail-media-export-tools" role="group" aria-label="输出">
        <label class="asset-detail-media-export-scope" title="PNG 输出范围"><span>范围</span><select data-detail-media-export-scope aria-label="PNG 输出范围"><option value="all" ${detailViewerState.mediaCanvasExportScope !== 'selection' ? 'selected' : ''}>全部画布</option><option value="selection" ${detailViewerState.mediaCanvasExportScope === 'selection' ? 'selected' : ''}>当前选中</option></select></label>
        <button class="hw-btn" type="button" data-detail-media-context-action="export"><i data-lucide="download"></i><span>导出</span></button>
        <button class="hw-btn" type="button" data-detail-media-context-action="save-image"><i data-lucide="image-down"></i><span>保存图片</span></button>
        <small>${detailViewerState.mediaCanvasExportScope === 'selection' ? (selectedCount ? `输出已选 ${selectedCount} 项` : '请先选择对象') : '输出全部画布'}</small>
    </div>`;
}
function detailMediaCanvasSelectedAnnotationColor(kind){
    const note = [...detailMediaCanvasSelectedIds()].map(detailMediaCanvasAnnotation).find(Boolean) || detailMediaCanvasAnnotation(detailViewerState.mediaCanvasEditingAnnotationId);
    return kind === 'text' ? (note?.textColor || '#ffffff') : (note?.backgroundColor || '#202622');
}
function renderDetailMediaCanvasContextMenu(){
    const menu = detailViewerState.mediaCanvasContextMenu;
    if(!menu?.open) return '';
    const selectedCount = detailMediaCanvasSelectedIds().size || detailMediaCanvasObjectIds().length;
    return `<div class="asset-detail-media-context-menu" data-detail-media-context-menu style="left:${Number(menu.x || 0)}px;top:${Number(menu.y || 0)}px" role="menu" aria-label="参考画布菜单">
        <button type="button" data-detail-media-context-action="annotation" role="menuitem"><i data-lucide="message-square-text"></i><span>注释</span><small>增加输入文本</small></button>
        <button type="button" data-detail-media-context-action="export" role="menuitem"><i data-lucide="download"></i><span>导出</span><small>选择保存位置</small></button>
        <button type="button" data-detail-media-context-action="save-image" role="menuitem"><i data-lucide="image-down"></i><span>保存图片</span><small>保存到当前库</small></button>
        <section><header><span>排列</span><small>当前摆放顺序</small></header><div><button type="button" data-detail-media-context-action="arrange-grid" title="网格排列"><i data-lucide="grid-2x2"></i></button><button type="button" data-detail-media-context-action="arrange-horizontal" title="水平排列"><i data-lucide="rows-3"></i></button><button type="button" data-detail-media-context-action="arrange-vertical" title="垂直排列"><i data-lucide="columns-3"></i></button></div></section>
        <section><header><span>分组</span><small>整体移动缩放</small></header><div><button type="button" data-detail-media-context-action="group" title="打组 Ctrl+G" ${selectedCount < 2 ? 'disabled' : ''}><i data-lucide="group"></i></button><button type="button" data-detail-media-context-action="ungroup" title="解组 Ctrl+Shift+G" ${!detailMediaCanvasSelectionOrder().some(id => detailMediaCanvasGroup(id)) ? 'disabled' : ''}><i data-lucide="ungroup"></i></button></div></section>
        <section class="${selectedCount < 2 ? 'is-disabled' : ''}"><header><span>对齐</span><small>最后选择为基准</small></header><div><button type="button" data-detail-media-context-action="align-left" title="左对齐" ${selectedCount < 2 ? 'disabled' : ''}><i data-lucide="align-start-vertical"></i></button><button type="button" data-detail-media-context-action="align-center-x" title="水平居中" ${selectedCount < 2 ? 'disabled' : ''}><i data-lucide="align-center-vertical"></i></button><button type="button" data-detail-media-context-action="align-right" title="右对齐" ${selectedCount < 2 ? 'disabled' : ''}><i data-lucide="align-end-vertical"></i></button><button type="button" data-detail-media-context-action="align-top" title="顶部对齐" ${selectedCount < 2 ? 'disabled' : ''}><i data-lucide="align-start-horizontal"></i></button><button type="button" data-detail-media-context-action="align-center-y" title="垂直居中" ${selectedCount < 2 ? 'disabled' : ''}><i data-lucide="align-center-horizontal"></i></button></div></section>
        <section><header><span>规格化</span><select data-detail-media-normalize-basis aria-label="规格化尺寸基准"><option value="first" ${(detailViewerState.mediaCanvasNormalizeBasis || 'first') === 'first' ? 'selected' : ''}>第一个</option><option value="average" ${detailViewerState.mediaCanvasNormalizeBasis === 'average' ? 'selected' : ''}>平均</option><option value="last" ${detailViewerState.mediaCanvasNormalizeBasis === 'last' ? 'selected' : ''}>最后一个</option></select></header><div><button type="button" data-detail-media-context-action="normalize-width" title="统一宽度"><i data-lucide="move-horizontal"></i></button><button type="button" data-detail-media-context-action="normalize-height" title="统一高度"><i data-lucide="move-vertical"></i></button><button type="button" data-detail-media-context-action="normalize-original" title="恢复原始尺寸"><i data-lucide="maximize"></i></button></div></section>
    </div>`;
}
function renderDetailMediaCanvas(source, currentItem){
    const items = detailMediaCanvasItems();
    const metrics = detailMediaCanvasMetrics(items);
    const scale = Math.max(.005, Math.min(4, Number(detailViewerState.mediaCanvasScale || 1)));
    const x = Number(detailViewerState.mediaCanvasX || 0);
    const y = Number(detailViewerState.mediaCanvasY || 0);
    const activeId = detailViewerState.mediaCanvasActiveId || items[0]?.id || '';
    const activeItem = items.find(item => item.id === activeId) || items[0] || null;
    const activeVideo = activeItem && detailViewerType(source,activeItem) === 'video' ? activeItem : null;
    const canPersist = items.length > 0 || Boolean(detailViewerState.referenceCanvasId);
    const minimapScale = Math.min(196 / Math.max(1, metrics.width), 106 / Math.max(1, metrics.height));
    const item = currentItem || detailViewerState.items[detailViewerState.index] || detailViewerItem(source, detailViewerState.id) || activeItem || {};
    return `<div class="asset-detail-media-canvas-shell ${activeVideo ? 'has-video-controls' : ''}" data-detail-media-canvas-shell>
        <div class="asset-detail-media-canvas-toolbar">
            <div class="asset-detail-media-toolbar-primary">
                <label><i data-lucide="map"></i><input type="text" data-detail-reference-title value="${escapeAttr(detailViewerState.referenceCanvasTitle || '参考画布')}" maxlength="80" aria-label="参考画布"></label>
                <span class="asset-detail-media-canvas-filename" title="${escapeAttr(item.name || '')}"><strong>${escapeHtml(item.name || '未命名素材')}</strong></span>
                <span><b>${items.length}</b> 个素材</span>
                <div>
                <button class="hw-btn" type="button" data-detail-reference-add-selection title="将当前多选图片或视频加入画布"><i data-lucide="images"></i><span>加选</span></button>
                <button class="hw-btn" type="button" data-detail-media-canvas-remove="${escapeAttr(activeId)}" ${!detailMediaCanvasSelectedIds().size ? 'disabled' : ''}><i data-lucide="trash-2"></i><span>移除选中</span></button>
                <button class="hw-btn" type="button" data-detail-media-canvas-zoom="0"><i data-lucide="scan"></i><span>自适应</span></button>
                <button class="hw-btn asset-detail-media-coverflow-toggle ${detailViewerState.mediaCanvasCoverflowVisible ? 'is-active' : ''}" type="button" data-detail-media-coverflow-toggle aria-pressed="${Boolean(detailViewerState.mediaCanvasCoverflowVisible)}" title="${detailViewerState.mediaCanvasCoverflowVisible ? '隐藏 Coverflow 预览' : '显示 Coverflow 预览'}"><i data-lucide="gallery-horizontal"></i><span>Coverflow</span></button>
                <button class="hw-btn asset-detail-media-minimap-toggle ${detailMediaCanvasMinimapVisible ? 'is-active' : ''}" type="button" data-detail-media-minimap-toggle aria-pressed="${detailMediaCanvasMinimapVisible}" title="${detailMediaCanvasMinimapVisible ? '隐藏小地图' : '显示小地图'}"><i class="asset-detail-minimap-visible" data-lucide="map"></i><i class="asset-detail-minimap-hidden" data-lucide="map-off"></i><span class="asset-detail-minimap-visible">小地图</span><span class="asset-detail-minimap-hidden">显示小地图</span></button>
                <button class="hw-btn hw-btn-gold" type="button" data-detail-reference-save ${!canPersist || detailViewerState.referenceCanvasSaving ? 'disabled' : ''}><i data-lucide="${detailViewerState.referenceCanvasSaving ? 'loader-2' : 'save'}"></i><span>${detailViewerState.referenceCanvasId ? '更新参考画布' : '保存参考画布'}</span></button>
                <button class="hw-btn" type="button" data-detail-reference-share title="分享素材 / 复制链接" aria-label="分享素材"><i data-lucide="share-2"></i><span>分享</span></button>
                <button class="hw-btn" type="button" data-detail-viewer-close title="关闭 (Esc)" aria-label="关闭详情"><i data-lucide="x"></i><span>关闭</span></button>
                </div>
            </div>
            <div class="asset-detail-media-toolbar-tools">${renderDetailMediaCanvasToolButtons()}</div>
        </div>
        <div class="asset-detail-media-canvas-viewport ${activeVideo ? 'has-video-dock' : ''}" data-detail-media-canvas-viewport tabindex="0" aria-label="无限画布，左键选择或框选，Shift 加选，空格/中键拖动画布，滚轮缩放">
            <div class="asset-detail-media-canvas-grid ${detailViewerState.mediaCanvasSnap ? '' : 'is-snap-off'}" style="background-position:${x}px ${y}px,${x}px ${y}px,${x}px ${y}px,${x}px ${y}px;background-size:${80*scale}px ${80*scale}px,${80*scale}px ${80*scale}px,${20*scale}px ${20*scale}px,${20*scale}px ${20*scale}px" aria-hidden="true"></div>
            <div class="asset-detail-media-canvas-world" data-detail-media-canvas-world style="width:${metrics.width}px;height:${metrics.height}px;--media-handle-size:${10/scale}px;--media-handle-half:${-5/scale}px;--media-outline-size:${2/scale}px;transform:translate(${x}px,${y}px) scale(${scale})">
                ${detailMediaCanvasGroups().map(renderDetailMediaCanvasGroup).join('')}
                <svg class="asset-detail-media-drawings" width="${metrics.width}" height="${metrics.height}" viewBox="0 0 ${metrics.width} ${metrics.height}" overflow="visible" aria-hidden="true"></svg>
                ${items.map(value => renderDetailMediaCanvasNode(source, value)).join('')}
                ${detailMediaCanvasDrawings().map(renderDetailMediaCanvasDrawing).join('')}
                ${(detailViewerState.mediaCanvasAnnotations || []).map(renderDetailMediaCanvasAnnotation).join('')}
            </div>
            <div class="asset-detail-media-canvas-help"><span>左键选择/框选</span><span><kbd>Shift</kbd> 加选</span><span><kbd>Space</kbd>/中键抓手</span><span><kbd>Ctrl+A</kbd> 全选</span><span><kbd>Ctrl+C/V</kbd> 复制/粘贴</span><span><kbd>Ctrl+Z</kbd> 撤销</span><b data-detail-media-canvas-scale>${Math.round(scale * 100)}%</b></div>
            <aside class="asset-detail-media-minimap ${detailMediaCanvasMinimapVisible ? '' : 'is-hidden'}" aria-label="画布缩略图" aria-hidden="${!detailMediaCanvasMinimapVisible}">
                <header><span>画布缩略图</span><b>${items.length}</b><button class="asset-detail-media-minimap-toggle is-active" type="button" data-detail-media-minimap-toggle aria-pressed="true" title="隐藏画布缩略图" aria-label="隐藏画布缩略图"><i data-lucide="eye-off"></i></button></header>
                <div class="asset-detail-media-minimap-world" style="width:${Math.max(1,metrics.width * minimapScale)}px;height:${Math.max(1,metrics.height * minimapScale)}px">${items.map(value => {
                    const type = detailViewerType(source, value);
                    const url = detailViewerUrl(source, value);
                    const box = detailViewerState.mediaCanvasLayout?.[value.id] || {};
                    const edit = (value.reference_edit && typeof value.reference_edit === 'object') ? value.reference_edit : (value.edit && typeof value.edit === 'object' ? value.edit : null);
                    const preview = type === 'workflow'
                        ? workflowThumb(value)
                        : (edit
                            ? renderMediaNodeParametricSvg(value, edit, url, '')
                            : `<img src="${escapeAttr(assetPreviewUrl(url, 320, value))}" alt="">`);
                    return `<button class="${value.id === activeId ? 'is-active' : ''}" type="button" data-detail-media-canvas-node="${escapeAttr(value.id)}" title="${escapeAttr(value.name || '画布素材')}" style="left:${(Number(box.x || 0)-metrics.minX) * minimapScale}px;top:${(Number(box.y || 0)-metrics.minY) * minimapScale}px;width:${Math.max(2,Number(box.width || 0) * minimapScale)}px;height:${Math.max(2,Number(box.height || 0) * minimapScale)}px">${preview}</button>`;
                }).join('')}</div>
            </aside>
            ${renderDetailMediaCanvasContextMenu()}
        </div>
    </div>`;
}
function captureDetailMediaCanvasNaturalSize(event){
    const media = event.target?.closest?.('[data-detail-media-natural]');
    if(!media || !detailViewerState.open) return;
    const id = media.dataset.detailMediaNatural || '';
    const item = detailMediaCanvasItems().find(value => value.id === id);
    const width = Number(media.naturalWidth || media.videoWidth || 0);
    const height = Number(media.naturalHeight || media.videoHeight || 0);
    if(!item || !(width > 0) || !(height > 0)) return;
    const naturalChanged = Number(item.natural_w) !== width || Number(item.natural_h) !== height;
    item.natural_w = width;
    item.natural_h = height;
    const currentBox = detailViewerState.mediaCanvasLayout?.[id];
    if(currentBox){
        const naturalRatio = width / height;
        const currentRatio = (Number(currentBox.width) || 1) / (Number(currentBox.height) || 1);
        // 若未经用户手动缩放，或者宽高比与自然比例失真超过 3%，立即按真实自然比例重设宽高并贴合
        if(!currentBox.userResized || Math.abs(currentRatio - naturalRatio) > 0.03){
            const maxDim = Math.max(360, Math.min(1600, Math.max(Number(currentBox.width) || 0, Number(currentBox.height) || 0, 800)));
            let newWidth, newHeight;
            if(width >= height){
                newWidth = snapDetailMediaCanvasValue(maxDim);
                newHeight = snapDetailMediaCanvasValue(maxDim / naturalRatio);
            } else {
                newHeight = snapDetailMediaCanvasValue(maxDim);
                newWidth = snapDetailMediaCanvasValue(maxDim * naturalRatio);
            }
            const centerX = Number(currentBox.x || 0) + (Number(currentBox.width) || 0) / 2;
            const centerY = Number(currentBox.y || 0) + (Number(currentBox.height) || 0) / 2;
            currentBox.x = snapDetailMediaCanvasValue(centerX - newWidth / 2);
            currentBox.y = snapDetailMediaCanvasValue(centerY - newHeight / 2);
            currentBox.width = Math.max(80, newWidth);
            currentBox.height = Math.max(60, newHeight);
            syncDetailMediaCanvasGeometryDom();
        }
    }
    if(detailViewerState.mediaCanvasLayout?.[id]?.auto && naturalChanged){
        arrangeDetailMediaCanvasItems('grid', false);
        renderDetailViewer();
        requestAnimationFrame(fitDetailMediaCanvas);
    }
}
function detailMediaCanvasInitialLibraryScope(originTab=activeTab){
    const board = assetBoardById(originTab);
    if(!board || board.system !== false) return 'all';
    const referenceId = boardReferenceCanvasSelection[board.id] || '';
    const groupId = boardGroupSelection[board.id] || '';
    const folder = boardFolderSelection[board.id] || '';
    const source = board.sourceBoard ? String(board.rootId || '') : (boardSourceSelection[board.id] || '');
    if(referenceId) return `reference:${referenceId}`;
    if(groupId) return `group:${groupId}`;
    if(folder) return `folder:${folder}`;
    if(source) return `source:${source}`;
    return 'all';
}
function detailMediaCanvasLibraryBaseItems(){
    const board = assetBoardById(detailViewerState.originTab);
    let values = detailViewerItems(detailViewerState.source);
    if(board?.system === false) values = customAssetLibraryAllItems(board);
    else if(detailViewerState.source === 'registry') values = activeRegistryVisibleAssets();
    else if(detailViewerState.source === 'canvas-asset' && registryAssets.length) values = registryAssets;
    const unique = new Map();
    values.filter(item => ['image','video'].includes(detailViewerType(detailViewerState.source,item))).forEach(item => unique.set(item.id,item));
    return [...unique.values()].slice(0,500);
}
function detailMediaCanvasLibraryScopeOptions(baseItems=detailMediaCanvasLibraryBaseItems()){
    const board = assetBoardById(detailViewerState.originTab);
    const options = [{value:'all',label:'全部素材',count:baseItems.length}];
    if(!board || board.system !== false) return options;
    const knownSourceIds = new Set([...(board.sources || []).map(source => source.id), ...(board.excludedSourceIds || [])]);
    if(!board.sourceBoard && !board.automaticHidden){
        const count = baseItems.filter(item => !knownSourceIds.has(String(item.storage_root_id || ''))).length;
        options.push({value:'source:__automatic__',label:'来源 / 自动归档',count});
    }
    (board.sources || []).filter(source => !source.hidden).forEach(source => {
        const count = baseItems.filter(item => String(item.storage_root_id || '') === source.id).length;
        options.push({value:`source:${source.id}`,label:`来源 / ${source.name}`,count});
    });
    const flattenFolders = (nodes,depth=0) => nodes.flatMap(node => [{value:`folder:${node.path}`,label:`文件夹 / ${'　'.repeat(depth)}${node.name}`,count:node.count},...flattenFolders(node.children || [],depth+1)]);
    options.push(...flattenFolders(customBoardFolderTree(baseItems)));
    const availableIds = new Set(baseItems.map(item => item.id));
    registryBoardCustomGroups(board).forEach(group => options.push({
        value:`group:${group.id}`, label:`分组 / ${group.name}`,
        count:(group.assetIds || []).filter(id => availableIds.has(id)).length,
    }));
    customBoardReferenceCanvases(board,baseItems).forEach(canvas => options.push({
        value:`reference:${canvas.id}`, label:`参考画布 / ${canvas.title || '未命名'}`, count:canvas.boardAssetCount,
    }));
    return options;
}
function detailMediaCanvasLibraryItems(){
    const board = assetBoardById(detailViewerState.originTab);
    const baseItems = detailMediaCanvasLibraryBaseItems();
    const storageOptions = detailMediaCanvasLibraryStorageOptions(baseItems);
    if(!storageOptions.some(option => option.value === detailViewerState.mediaCanvasLibraryStorageRootId)) detailViewerState.mediaCanvasLibraryStorageRootId = 'all';
    const libraryId = String(detailViewerState.mediaCanvasLibraryStorageRootId || '');
    let values = libraryId !== 'all' && detailMediaCanvasLibraryRootItemsCache.has(libraryId)
        ? [...detailMediaCanvasLibraryRootItemsCache.get(libraryId)]
        : baseItems;
    if(libraryId && libraryId !== 'all' && !detailMediaCanvasLibraryRootItemsCache.has(libraryId)) values = values.filter(item => String(item.storage_root_id || (item.storage_root_name ? `name:${item.storage_root_name}` : '')) === libraryId);
    const scope = String(detailViewerState.mediaCanvasLibraryScope || 'all');
    const [scopeType,...scopeParts] = scope.split(':');
    const scopeValue = scopeParts.join(':');
    if(board?.system === false && scope !== 'all'){
        if(scopeType === 'source'){
            const knownSourceIds = new Set([...(board.sources || []).map(source => source.id), ...(board.excludedSourceIds || [])]);
            values = values.filter(item => scopeValue === '__automatic__' ? !knownSourceIds.has(String(item.storage_root_id || '')) : String(item.storage_root_id || '') === scopeValue);
        } else if(scopeType === 'folder') values = values.filter(item => registryAssetMatchesFolder(item,scopeValue));
        else if(scopeType === 'group'){
            const ids = new Set((registryBoardCustomGroups(board).find(group => group.id === scopeValue)?.assetIds || []).map(String));
            values = values.filter(item => ids.has(item.id));
        } else if(scopeType === 'reference'){
            const ids = new Set((canvasAssetsData.items || []).filter(item => item.canvas_id === scopeValue).map(item => String(item.asset_id || '')).filter(Boolean));
            values = values.filter(item => ids.has(String(item.id || item.asset_id || '')));
        }
    }
    const query = String(detailViewerState.mediaCanvasLibraryQuery || '').trim().toLocaleLowerCase('zh-CN');
    if(query) values = values.filter(item => [item.name,registryAssetExtension(item),registryAssetFolderPath(item),item.storage_root_name,...(item.tags || []).map(tag => tag.name)].some(value => String(value || '').toLocaleLowerCase('zh-CN').includes(query)));
    const sort = detailViewerState.mediaCanvasLibrarySort || 'newest';
    values.sort((left,right) => {
        if(sort === 'name') return String(left.name || '').localeCompare(String(right.name || ''),'zh-Hans-CN',{numeric:true,sensitivity:'base'});
        if(sort === 'size_desc') return Number(right.size || 0)-Number(left.size || 0);
        const delta = Number(right.created_at || right.updated_at || 0)-Number(left.created_at || left.updated_at || 0);
        return sort === 'oldest' ? -delta : delta;
    });
    return values;
}
function detailMediaCanvasLibraryStorageOptions(baseItems=detailMediaCanvasLibraryBaseItems()){
    const counts = new Map();
    baseItems.forEach(item => {
        const id = String(item.storage_root_id || (item.storage_root_name ? `name:${item.storage_root_name}` : '')).trim();
        if(id) counts.set(id, (counts.get(id) || 0) + 1);
    });
    const options = [{value:'all',label:'全部素材库',count:registryTotal || baseItems.length}];
    const roots = new Map();
    (registryFacets.roots || []).forEach(root => {
        const id = String(root?.id || '').trim();
        if(!id) return;
        roots.set(id, {value:id,label:String(root.name || id),count:Number(root.count || counts.get(id) || 0)});
    });
    baseItems.forEach(item => {
        const name = String(item.storage_root_name || '').trim();
        const id = String(item.storage_root_id || (name ? `name:${name}` : '')).trim();
        if(!id) return;
        const current = roots.get(id) || {value:id,label:name || id,count:counts.get(id) || 0};
        if(!current.label || current.label === id) current.label = name || id;
        roots.set(id,current);
    });
    return options.concat([...roots.values()].sort((left,right) => left.label.localeCompare(right.label,'zh-Hans-CN',{numeric:true})));
}
function renderDetailMediaCanvasLibraryCards(values){
    const persisted = new Set(detailMediaCanvasItems().map(detailMediaCanvasPersistId).filter(Boolean));
    return values.map(item => {
        const persistId = String(item.asset_id || item.assetId || item.id || '');
        const onCanvas = persisted.has(persistId);
        const selected = detailViewerState.mediaCanvasLibrarySelectedId === item.id;
        const format = registryAssetExtension(item).toUpperCase() || detailViewerTypeLabel(detailViewerType(detailViewerState.source,item));
        return `<article class="${onCanvas ? 'is-on-canvas' : ''} ${selected ? 'is-selected' : ''}" draggable="true" data-detail-media-library-item="${escapeAttr(item.id)}" tabindex="0" title="单击选择，双击加入画布：${escapeAttr(item.name || '素材')}" aria-selected="${selected}"><div>${assetThumb(item)}</div><b>${escapeHtml(format)}</b><span>${escapeHtml(item.name || '未命名素材')}</span>${onCanvas ? '<small>画布内</small>' : ''}</article>`;
    }).join('') || '<div class="detail-empty"><span>当前范围没有匹配的图片或视频</span></div>';
}
function refreshDetailMediaCanvasLibraryGrid(){
    const values = detailMediaCanvasLibraryItems();
    const grid = document.querySelector('[data-detail-media-library-grid]');
    if(grid){
        const loading = detailViewerState.mediaCanvasLibraryStorageRootId !== 'all'
            && detailMediaCanvasLibraryRootLoading === detailViewerState.mediaCanvasLibraryStorageRootId;
        grid.innerHTML = loading
            ? '<div class="detail-empty detail-media-library-loading"><i data-lucide="loader-2"></i><span>正在加载素材库…</span></div>'
            : renderDetailMediaCanvasLibraryCards(values);
    }
    const count = document.querySelector('[data-detail-media-library-count]');
    if(count) count.textContent = String(values.length);
    refreshIcons();
}
async function loadDetailMediaCanvasLibraryRoot(rootId){
    const id = String(rootId || '').trim();
    if(!id || id === 'all' || detailMediaCanvasLibraryRootItemsCache.has(id)) return;
    detailMediaCanvasLibraryRootLoading = id;
    refreshDetailMediaCanvasLibraryGrid();
    try {
        const params = new URLSearchParams({limit:'500',sort:detailViewerState.mediaCanvasLibrarySort || 'newest',root_id:id});
        const data = await apiJsonResponse(assetManagerApi.getRegistryAssets(params.toString()));
        const items = (data.items || []).map(normalizeRegistryAsset).filter(item => ['image','video'].includes(detailViewerType('registry', item)));
        detailMediaCanvasLibraryRootItemsCache.set(id, items);
    } catch(err) {
        setStatus(err.message || '素材库加载失败');
    } finally {
        if(detailMediaCanvasLibraryRootLoading === id) detailMediaCanvasLibraryRootLoading = '';
        if(detailViewerState.open && detailViewerState.mediaCanvasLibraryStorageRootId === id) refreshDetailMediaCanvasLibraryGrid();
    }
}
function renderDetailMediaCanvasLibraryContent(values, baseItems, options, thumbSize){
    const libraryOptions = detailMediaCanvasLibraryStorageOptions(baseItems);
    if(!libraryOptions.some(option => option.value === detailViewerState.mediaCanvasLibraryStorageRootId)) detailViewerState.mediaCanvasLibraryStorageRootId = 'all';
    return `<div class="asset-detail-media-library-controls">
        <label class="asset-detail-media-library-search"><i data-lucide="search"></i><input type="search" value="${escapeAttr(detailViewerState.mediaCanvasLibraryQuery || '')}" data-detail-media-library-search placeholder="搜索文件名、标签或格式" aria-label="搜索当前图库"></label>
        <div><label><span>素材库</span><select data-detail-media-library-library aria-label="切换素材库">${libraryOptions.map(option => `<option value="${escapeAttr(option.value)}" ${detailViewerState.mediaCanvasLibraryStorageRootId === option.value ? 'selected' : ''}>${escapeHtml(option.label)} · ${option.count}</option>`).join('')}</select></label><label><span>浏览范围</span><select data-detail-media-library-scope aria-label="图库浏览范围">${options.map(option => `<option value="${escapeAttr(option.value)}" ${detailViewerState.mediaCanvasLibraryScope === option.value ? 'selected' : ''}>${escapeHtml(option.label)} · ${option.count}</option>`).join('')}</select></label><label><span>排序</span><select data-detail-media-library-sort aria-label="图库排序"><option value="newest" ${detailViewerState.mediaCanvasLibrarySort === 'newest' ? 'selected' : ''}>最近新增</option><option value="oldest" ${detailViewerState.mediaCanvasLibrarySort === 'oldest' ? 'selected' : ''}>最早新增</option><option value="name" ${detailViewerState.mediaCanvasLibrarySort === 'name' ? 'selected' : ''}>名称</option><option value="size_desc" ${detailViewerState.mediaCanvasLibrarySort === 'size_desc' ? 'selected' : ''}>大小</option></select></label></div>
    </div>
    <label class="asset-detail-media-library-size"><span>缩略图大小</span><input type="range" min="96" max="220" step="4" value="${thumbSize}" data-detail-media-library-size aria-label="调整图库缩略图大小"><output data-detail-media-library-size-output>${thumbSize}px</output></label>
    <div class="asset-detail-media-library-summary"><span><b data-detail-media-library-count>${values.length}</b> 个图库素材</span><small>单击选择 · 双击加入 · 可拖拽</small></div>
    <div class="asset-detail-media-library-grid" data-detail-media-library-grid>${renderDetailMediaCanvasLibraryCards(values)}</div>`;
}
function renderDetailMediaCanvasImageVersionHistory(){
    const item=detailActiveImageItem(),assetId=detailImageVersionAssetId(item);
    if(!assetId) return '';
    const versions=detailViewerState.imageVersionsAssetId===assetId ? detailViewerState.imageVersions : [];
    const content=detailViewerState.imageVersionsLoading
        ? '<p class="asset-detail-media-version-status">正在读取版本…</p>'
        : versions.map(version=>{
            const number=Number(version.version_number||0),selected=String(detailViewerState.imageVersionSelectedId)===String(version.id);
            const label=version.is_original?'原始素材':`V${number}`;
            return `<div class="registry-detail-image-version ${selected?'is-selected':''} ${version.is_hidden?'is-hidden':''}"><button type="button" data-detail-image-version-select="${escapeAttr(version.id)}" title="打开${escapeAttr(label)}"><span><b>${escapeHtml(label)}${version.is_hidden?' · 已隐藏':''}</b><small>${escapeHtml(formatDate(version.created_at))}</small></span><i data-lucide="chevron-right"></i></button><button type="button" data-detail-image-version-visibility="${escapeAttr(version.id)}" data-hidden="${version.is_hidden?'1':'0'}" title="${version.is_original?'原始素材不能隐藏':version.is_hidden?'显示此版本':'隐藏此版本'}" aria-label="${version.is_hidden?'显示':'隐藏'}${escapeAttr(label)}" ${version.is_original?'disabled':''}><i data-lucide="${version.is_hidden?'eye-off':'eye'}"></i></button><button type="button" data-detail-image-version-edit="${escapeAttr(version.id)}" title="编辑${escapeAttr(label)}" aria-label="编辑${escapeAttr(label)}"><i data-lucide="pencil"></i></button><button type="button" data-detail-image-version-delete="${escapeAttr(version.id)}" title="${version.is_original?'原始素材不能删除':`删除${label}`}" aria-label="删除${escapeAttr(label)}" ${version.is_original?'disabled':''}><i data-lucide="trash-2"></i></button></div>`;
        }).join('') || '<p class="asset-detail-media-version-status">当前仅有原始素材</p>';
    return `<section class="registry-detail-block registry-image-versions asset-detail-media-version-history"><div class="registry-detail-heading"><strong>版本历史</strong><span>${versions.length || 0}</span></div>${content}</section>`;
}
function renderDetailMediaCanvasSidebarDetail(){
    const canvasItem=detailActiveImageItem();
    if(!canvasItem) return '<div class="detail-empty"><i data-lucide="library-big"></i><span>选择画布素材查看资产详情</span></div>';
    const assetId=detailMediaCanvasPersistId(canvasItem);
    const registryItem=(registryDetail?.id===assetId ? registryDetail : registryAssets.find(value=>String(value.id)===String(assetId))) || null;
    const source=registryItem ? 'registry' : detailViewerState.source;
    const item=registryItem ? {...registryItem,...canvasItem,id:registryItem.id,tags:registryItem.tags,related_assets:registryItem.related_assets,storage_root_name:registryItem.storage_root_name,absolute_path:registryItem.absolute_path,locations:registryItem.locations,versions:registryItem.versions,location_count:registryItem.location_count,version_count:registryItem.version_count} : canvasItem;
    const type=detailViewerType(source,item);
    const tags=detailViewerTags(item);
    const rows=detailViewerMetaRows(source,item,type);
    const url=detailViewerUrl(source,item);
    const history=type==='image' ? renderDetailMediaCanvasImageVersionHistory() : '';
    return `<div class="asset-detail-media-sidebar-detail asset-detail-inspector-content" data-detail-media-sidebar-detail>${renderDetailViewerInspectorContent(item,type,tags,rows,url,history,source)}</div>`;
}
function refreshDetailMediaCanvasSidebarDetail(){
    const current=document.querySelector('[data-detail-media-sidebar-detail]');
    if(!current || detailViewerState.mediaCanvasSidebarView!=='detail') return;
    current.outerHTML=renderDetailMediaCanvasSidebarDetail();
    refreshIcons();
}
function captureDetailViewerVideoDock(overlay){
    const dock = overlay?.querySelector?.('[data-detail-video-dock]');
    return dock ? {dock, videoId:String(dock.dataset.detailVideoFor || '')} : null;
}
function restoreDetailViewerVideoDock(overlay,snapshot){
    if(!overlay || !snapshot?.dock) return;
    const current = overlay.querySelector?.('[data-detail-video-dock]');
    if(!current || !snapshot.videoId || String(current.dataset.detailVideoFor || '') !== snapshot.videoId) return;
    current.replaceWith(snapshot.dock);
}
function renderDetailMediaCanvasLibrary(){
    const values = detailMediaCanvasLibraryItems();
    const baseItems = detailMediaCanvasLibraryBaseItems();
    const options = detailMediaCanvasLibraryScopeOptions(baseItems);
    if(!options.some(option => option.value === detailViewerState.mediaCanvasLibraryScope)) detailViewerState.mediaCanvasLibraryScope = 'all';
    const collapsed = detailMediaCanvasLibraryCollapsed;
    const thumbSize = detailMediaCanvasLibraryThumbSize;
    const view=detailViewerState.mediaCanvasSidebarView==='detail' ? 'detail' : 'library';
    const activeItem=detailActiveImageItem();
    const title=view==='detail' ? '资产详情' : '素材库';
    const subtitle=view==='detail' ? (activeItem?.name || '选择画布素材查看') : (assetBoardById(detailViewerState.originTab)?.name || '当前图库');
    return `<aside class="asset-detail-media-library asset-detail-media-sidebar ${collapsed ? 'is-collapsed' : ''} ${view==='detail'?'is-detail-view':'is-library-view'}" style="--media-library-thumb-size:${thumbSize}px" aria-label="素材库和资产详情">
        ${collapsed ? '' : `<span class="asset-detail-media-library-resizer" data-detail-media-library-resizer role="separator" tabindex="0" aria-label="拖动调整图库宽度" aria-orientation="vertical" aria-valuemin="280" aria-valuemax="720" aria-valuenow="${detailMediaCanvasLibraryWidth}"></span>`}
        <header><button type="button" data-detail-media-library-toggle aria-expanded="${!collapsed}" aria-label="${collapsed ? '展开右侧栏' : '折叠右侧栏'}" title="${collapsed ? '展开右侧栏' : '折叠右侧栏'}"><i data-lucide="${collapsed ? 'panel-right-open' : 'panel-right-close'}"></i></button><div><strong>${escapeHtml(title)}</strong><span>${escapeHtml(subtitle)}</span></div>${view==='library'?`<em data-detail-media-library-count>${values.length}</em>`:'<i data-lucide="info"></i>'}</header>
        ${collapsed ? '' : `<nav class="asset-detail-media-sidebar-tabs" role="tablist" aria-label="右侧栏内容"><button type="button" role="tab" data-detail-media-sidebar-view="detail" aria-selected="${view==='detail'}" class="${view==='detail'?'is-active':''}"><i data-lucide="info"></i><span>资产详情</span></button><button type="button" role="tab" data-detail-media-sidebar-view="library" aria-selected="${view==='library'}" class="${view==='library'?'is-active':''}"><i data-lucide="library-big"></i><span>素材库</span></button></nav>${view==='library' ? renderDetailMediaCanvasLibraryContent(values,baseItems,options,thumbSize) : renderDetailMediaCanvasSidebarDetail()}`}
    </aside>`;
}
function mergeResolvedReferenceAsset(item={},rawAsset={}){
    const asset = normalizeRegistryAsset(rawAsset);
    if(!asset.id) return item;
    Object.assign(item,{
        asset_id:asset.id, assetId:asset.id,
        assetSha256:String(asset.sha256 || item.assetSha256 || ''),
        assetStorageRootId:String(asset.storage_root_id || item.assetStorageRootId || ''),
        assetRelativePath:String(asset.relative_path || item.assetRelativePath || ''),
        sha256:String(asset.sha256 || item.sha256 || ''),
        storage_root_id:String(asset.storage_root_id || item.storage_root_id || ''),
        relative_path:String(asset.relative_path || item.relative_path || ''),
        absolute_path:String(asset.absolute_path || ''),
        storage_root_name:asset.storage_root_name || item.storage_root_name || '',
        public_url:asset.public_url || item.public_url || '',
        locations:Array.isArray(asset.locations) ? asset.locations : (item.locations || []),
        versions:Array.isArray(asset.versions) ? asset.versions : (item.versions || []),
        location_count:Number(asset.location_count || 0),
        version_count:Number(asset.version_count || 0),
        size:Number(asset.size || item.size || 0),
        metadata:{...(asset.metadata || {}),...(item.metadata || {})},
    });
    if(!item.url) item.url = asset.url || asset.public_url || '';
    const registryIndex = registryAssets.findIndex(value => value.id === asset.id);
    if(registryIndex >= 0) registryAssets[registryIndex] = {...registryAssets[registryIndex],...asset};
    else registryAssets.unshift(asset);
    return item;
}
async function ensureDetailReferenceAssetIds(items=detailMediaCanvasItems(),{silent=false}={}){
    const failures = [];
    for(const item of items){
        if(!item || detailViewerState.source === 'registry') continue;
        const natural = detailMediaCanvasNaturalSize(item);
        const sourceCandidates = [...new Set([
            item.assetUrl, item.asset_url, item.public_url, item.url, item.src, item.path, item.file, item.source_url,
            item.reference_edit?.source_url, item.referenceEdit?.source_url,
            detailViewerUrl(detailViewerState.source,item),
        ].map(value => String(value || '').trim()).filter(Boolean))];
        const relativeCandidates = [...new Set([
            item.assetRelativePath, item.asset_relative_path, item.relative_path, item.relativePath,
        ].map(value => String(value || '').trim()).filter(Boolean))];
        try {
            const basePayload = {
                asset_id:detailMediaCanvasPersistId(item),
                sha256:String(item.assetSha256 || item.asset_sha256 || item.sha256 || ''),
                storage_root_id:String(item.assetStorageRootId || item.asset_storage_root_id || item.storage_root_id || ''),
                relative_path:relativeCandidates[0] || '',
                name:String(item.name || '').slice(0,500),
                width:Number(item.natural_w || item.naturalWidth || item.metadata?.width || natural.width || 0),
                height:Number(item.natural_h || item.naturalHeight || item.metadata?.height || natural.height || 0),
            };
            let resolved = null;
            for(const sourceUrl of sourceCandidates.length ? sourceCandidates : ['']){
                try {
                    const data = await apiJsonResponse(assetManagerApi.resolveRegistryAssetReference({...basePayload,source_url:sourceUrl.slice(0,4000)}));
                    if(data.asset?.id){ resolved = data.asset; break; }
                } catch(err){
                    if(!sourceCandidates.length || sourceUrl === sourceCandidates[sourceCandidates.length - 1]) throw err;
                }
            }
            if(!resolved) throw new Error('鏈壘鍒颁笌褰撳墠绱犳潗瀵瑰簲鐨勭粺涓€璧勪骇绱㈠紩');
            mergeResolvedReferenceAsset(item,resolved);
        } catch(err){
            failures.push(`${item.name || item.id || '未命名素材'}：${err.message || '未找到统一资产索引'}`);
        }
    }
    if(failures.length && !silent) throw new Error(failures.join('\n'));
    return items;
}

function setDetailMediaCanvasMinimapVisible(visible){
    gwSetDetailMediaCanvasMinimapVisible(Boolean(visible));
    try { localStorage.setItem(ASSET_DETAIL_MEDIA_MINIMAP_VISIBLE_KEY,String(detailMediaCanvasMinimapVisible)); } catch(_) {}
    const minimap = document.querySelector('.asset-detail-viewer .asset-detail-media-minimap');
    minimap?.classList.toggle('is-hidden',!detailMediaCanvasMinimapVisible);
    minimap?.setAttribute('aria-hidden',String(!detailMediaCanvasMinimapVisible));
    document.querySelectorAll('.asset-detail-viewer [data-detail-media-minimap-toggle]').forEach(button => {
        button.classList.toggle('is-active',detailMediaCanvasMinimapVisible);
        button.setAttribute('aria-pressed',String(detailMediaCanvasMinimapVisible));
        button.title = detailMediaCanvasMinimapVisible ? '隐藏画布缩略图' : '显示画布缩略图';
        button.setAttribute('aria-label',button.title);
    });
}
function syncDetailReferenceTitleFromDom(){
    const input = document.querySelector('.asset-detail-viewer [data-detail-reference-title]');
    if(input) detailViewerState.referenceCanvasTitle = String(input.value || '').trim().slice(0, 80) || '参考画布';
}
function applyDetailMediaCanvasTransform(){
    const world = document.querySelector('.asset-detail-viewer [data-detail-media-canvas-world]');
    if(!world) return;
    const scale = Math.max(.005, Math.min(4, Number(detailViewerState.mediaCanvasScale || 1)));
    const x = Number(detailViewerState.mediaCanvasX || 0);
    const y = Number(detailViewerState.mediaCanvasY || 0);
    world.style.transform = `translate(${x}px,${y}px) scale(${scale})`;
    world.style.setProperty('--media-handle-size',`${10/scale}px`);
    world.style.setProperty('--media-handle-half',`${-5/scale}px`);
    world.style.setProperty('--media-outline-size',`${2/scale}px`);
    const grid = document.querySelector('.asset-detail-viewer .asset-detail-media-canvas-grid');
    if(grid){
        grid.style.backgroundPosition = `${x}px ${y}px,${x}px ${y}px,${x}px ${y}px,${x}px ${y}px`;
        grid.style.backgroundSize = `${80*scale}px ${80*scale}px,${80*scale}px ${80*scale}px,${20*scale}px ${20*scale}px,${20*scale}px ${20*scale}px`;
    }
    document.querySelectorAll('.asset-detail-viewer [data-detail-media-canvas-scale]').forEach(label => { label.textContent = `${Math.round(scale * 100)}%`; });
    positionDetailMediaVideoDock();
}
function observeDetailMediaCanvasViewport(){
    const viewport = document.querySelector('.asset-detail-viewer [data-detail-media-canvas-viewport]');
    if(!viewport || viewport === detailMediaCanvasObservedViewport) return;
    detailMediaCanvasResizeObserver?.disconnect?.();
    detailMediaCanvasObservedViewport = viewport;
    if(typeof ResizeObserver === 'function'){
        detailMediaCanvasResizeObserver = new ResizeObserver(() => scheduleDetailMediaVideoDockPosition());
        detailMediaCanvasResizeObserver.observe(viewport);
    }
    scheduleDetailMediaVideoDockPosition();
}
function fitDetailMediaCanvas(){
    const viewport = document.querySelector('.asset-detail-viewer [data-detail-media-canvas-viewport]');
    if(!viewport) return;
    const metrics = detailMediaCanvasMetrics();
    positionDetailMediaVideoDock();
    const contentHeight = Math.max(120,viewport.clientHeight);
    const insetX = viewport.clientWidth < 720 ? 44 : 100;
    const insetY = contentHeight < 520 ? 52 : 88;
    const scale = Math.max(.005, Math.min(1.35, Math.min((viewport.clientWidth - insetX) / metrics.width, (contentHeight - insetY) / metrics.height)));
    detailViewerState.mediaCanvasScale = Number.isFinite(scale) ? scale : 1;
    detailViewerState.mediaCanvasX = (viewport.clientWidth - metrics.width * detailViewerState.mediaCanvasScale) / 2 - metrics.minX * detailViewerState.mediaCanvasScale;
    detailViewerState.mediaCanvasY = (contentHeight - metrics.height * detailViewerState.mediaCanvasScale) / 2 - metrics.minY * detailViewerState.mediaCanvasScale;
    detailViewerState.mediaCanvasFitted = true;
    applyDetailMediaCanvasTransform();
}
function adjustDetailMediaCanvasZoom(direction, clientX=null, clientY=null){
    const viewport = document.querySelector('.asset-detail-viewer [data-detail-media-canvas-viewport]');
    if(!viewport) return false;
    if(Number(direction) === 0){ fitDetailMediaCanvas(); return true; }
    const rect = viewport.getBoundingClientRect();
    const current = Math.max(.005, Math.min(4, Number(detailViewerState.mediaCanvasScale || 1)));
    const next = Math.max(.005, Math.min(4, current * (Number(direction) > 0 ? 1.16 : .86)));
    if(next === current) return true;
    const anchorX = clientX === null ? rect.left + rect.width / 2 : clientX;
    const anchorY = clientY === null ? rect.top + rect.height / 2 : clientY;
    const localX = anchorX - rect.left;
    const localY = anchorY - rect.top;
    const worldX = (localX - Number(detailViewerState.mediaCanvasX || 0)) / current;
    const worldY = (localY - Number(detailViewerState.mediaCanvasY || 0)) / current;
    detailViewerState.mediaCanvasScale = next;
    detailViewerState.mediaCanvasX = localX - worldX * next;
    detailViewerState.mediaCanvasY = localY - worldY * next;
    detailViewerState.mediaCanvasFitted = true;
    applyDetailMediaCanvasTransform();
    return true;
}
function detailMediaCanvasWorldPoint(clientX, clientY, viewport=null){
    const target = viewport || document.querySelector('.asset-detail-viewer [data-detail-media-canvas-viewport]');
    if(!target) return {x:0,y:0};
    const rect = target.getBoundingClientRect();
    const scale = Math.max(.005, Number(detailViewerState.mediaCanvasScale || 1));
    return {
        x:(Number(clientX || 0) - rect.left - Number(detailViewerState.mediaCanvasX || 0)) / scale,
        y:(Number(clientY || 0) - rect.top - Number(detailViewerState.mediaCanvasY || 0)) / scale,
    };
}
function syncDetailMediaCanvasSelectionDom(){
    const selected = detailMediaCanvasSelectedIds();
    document.querySelectorAll('.asset-detail-viewer [data-detail-media-object]').forEach(node => {
        const id = node.dataset.detailMediaObject || '';
        const mediaId = node.dataset.detailMediaCanvasNode || '';
        const active = (mediaId || id) === detailViewerState.mediaCanvasActiveId;
        const isSelected = selected.has(id) && !node.hasAttribute('data-detail-media-group-member');
        const memberId = node.dataset.detailMediaGroupMember || '';
        const memberGroup = memberId ? detailMediaCanvasGroupForMember(memberId) : null;
        const selectedViaGroup = Boolean(node.hasAttribute('data-detail-media-canvas-node') && memberGroup && selected.has(memberGroup.id));
        node.classList.toggle('is-active', active);
        node.classList.toggle('is-selected', isSelected);
        node.classList.toggle('is-selected-via-group', selectedViaGroup);
        node.setAttribute('aria-selected', String(isSelected || selectedViaGroup));
    });
}
function syncDetailMediaCanvasActiveDom(){
    const items = detailMediaCanvasItems();
    const activeId = detailViewerState.mediaCanvasActiveId || '';
    const activeItem = items.find(item => item.id === activeId) || null;
    syncDetailMediaCanvasSelectionDom();
    document.querySelectorAll('.asset-detail-viewer .asset-detail-media-minimap [data-detail-media-canvas-node]').forEach(node => {
        node.classList.toggle('is-active',(node.dataset.detailMediaCanvasNode || '') === activeId);
    });
    const breadcrumb = document.querySelector('.asset-detail-viewer .asset-detail-breadcrumb strong');
    if(breadcrumb && activeItem) breadcrumb.textContent = activeItem.name || '未命名素材';
    const position = document.querySelector('.asset-detail-viewer [data-detail-position-label]');
    if(position){
        const index = Math.max(0,items.findIndex(item => item.id === activeId));
        position.textContent = items.length ? `${index + 1} / ${items.length}` : '空画布';
    }
    const activeVideo = activeItem && detailViewerType(detailViewerState.source,activeItem) === 'video';
    document.querySelector('.asset-detail-viewer [data-detail-media-canvas-shell]')?.classList.toggle('has-video-controls',Boolean(activeVideo));
    document.querySelector('.asset-detail-viewer [data-detail-media-canvas-viewport]')?.classList.toggle('has-video-dock',Boolean(activeVideo));
    positionDetailMediaVideoDock();
}
function syncDetailMediaCanvasGeometryDom(){
    const metrics = detailMediaCanvasMetrics();
    const world = document.querySelector('.asset-detail-viewer [data-detail-media-canvas-world]');
    if(world){ world.style.width = `${metrics.width}px`; world.style.height = `${metrics.height}px`; }
    document.querySelectorAll('.asset-detail-viewer .asset-detail-media-node').forEach(node => {
        const box = detailViewerState.mediaCanvasLayout?.[node.dataset.detailMediaCanvasNode || ''];
        if(!box) return;
        node.style.left = `${box.x}px`;
        node.style.top = `${box.y}px`;
        node.style.width = `${box.width}px`;
        node.style.height = `${box.height}px`;
    });
    // 【QIN-580】缩放/平移后角标与浮窗要跟着换回恒定视觉尺寸。
    syncDetailMediaNodeLutScale();
    document.querySelectorAll('.asset-detail-viewer [data-detail-media-annotation-node]').forEach(node => {
        const note = detailMediaCanvasAnnotation(node.dataset.detailMediaAnnotationNode || '');
        if(!note) return;
        node.style.left = `${note.x}px`; node.style.top = `${note.y}px`;
        node.style.width = `${note.width}px`; node.style.height = `${note.height}px`;
    });
    document.querySelectorAll('.asset-detail-viewer [data-detail-media-group-node]').forEach(node => {
        const box = detailMediaCanvasGroupBox(node.dataset.detailMediaGroupNode || '');
        if(!box) return;
        node.style.left = `${box.x}px`; node.style.top = `${box.y}px`;
        node.style.width = `${box.width}px`; node.style.height = `${box.height}px`;
    });
    document.querySelectorAll('.asset-detail-viewer [data-detail-media-drawing-node]').forEach(node => {
        const drawing = detailMediaCanvasDrawing(node.dataset.detailMediaDrawingNode || '');
        const box = detailMediaCanvasDrawingBox(drawing);
        const svg = node.querySelector('svg');
        const path = svg?.querySelector('path');
        if(!drawing || !box) return;
        node.style.left = `${box.x}px`; node.style.top = `${box.y}px`;
        node.style.width = `${box.width}px`; node.style.height = `${box.height}px`;
        svg?.setAttribute('viewBox',`${box.x} ${box.y} ${box.width} ${box.height}`);
        path?.setAttribute('d',detailMediaCanvasDrawingPath(drawing.points || []));
        path?.style.setProperty('--drawing-width',String(Math.max(1,Number(drawing.width || 3))));
    });
    const svg = document.querySelector('.asset-detail-viewer .asset-detail-media-drawings');
    if(svg){
        svg.setAttribute('width', String(metrics.width));
        svg.setAttribute('height', String(metrics.height));
        svg.setAttribute('viewBox', `0 0 ${metrics.width} ${metrics.height}`);
    }
    positionDetailMediaVideoDock();
}
function closeDetailMediaCanvasContextMenu(){
    detailViewerState.mediaCanvasContextMenu = null;
    document.querySelector('.asset-detail-viewer [data-detail-media-context-menu]')?.remove();
}
function selectDetailMediaCanvasItem(id, event={}){
    if(!id) return false;
    const previousActiveId = detailViewerState.mediaCanvasActiveId || '';
    const objectId = detailMediaCanvasSelectionObjectId(id);
    let order = detailMediaCanvasSelectionOrder();
    const selected = detailMediaCanvasSelectedIds();
    if(event.shiftKey){
        if(!selected.has(objectId)){
            selected.add(objectId);
            order.push(objectId);
        }
    } else if(event.ctrlKey || event.metaKey){
        if(selected.has(objectId)){
            selected.delete(objectId);
            order = order.filter(value => value !== objectId);
        } else {
            selected.add(objectId);
            order.push(objectId);
        }
    } else if(!selected.has(objectId) || selected.size !== 1){
        selected.clear();
        selected.add(objectId);
        order = [objectId];
    }
    detailViewerState.mediaCanvasSelectionOrder = order;
    const mediaItem = detailMediaCanvasItems().find(value => value.id === id);
    if(selected.has(objectId) && mediaItem) detailViewerState.mediaCanvasActiveId = id;
    else if(!selected.has(objectId) && detailViewerState.mediaCanvasActiveId === id){
        detailViewerState.mediaCanvasActiveId = [...order].reverse().map(value => {
            if(detailMediaCanvasItems().some(item => item.id === value)) return value;
            const group = detailMediaCanvasGroup(value);
            return (group?.memberIds || []).find(memberId => detailMediaCanvasItems().some(item => item.id === memberId)) || '';
        }).find(Boolean) || '';
    }
    const nextMediaId = selected.has(objectId) && mediaItem ? id : detailViewerState.mediaCanvasActiveId;
    if(previousActiveId && nextMediaId !== previousActiveId){
        const previousVideo = detailViewerVideo(previousActiveId);
        previousVideo?.pause?.();
    }
    const nextIndex = detailViewerState.items.findIndex(value => value.id === nextMediaId);
    if(nextIndex >= 0){ detailViewerState.id = nextMediaId; detailViewerState.index = nextIndex; }
    syncDetailMediaCanvasSelectionDom();
    refreshDetailMediaCanvasSidebarDetail();
    return selected.has(objectId);
}
function resizeDetailMediaCanvasBox(origin, handle, dx, dy, preserveAspect=true){
    if(!preserveAspect){
        let x = Number(origin.x || 0), y = Number(origin.y || 0);
        let width = Number(origin.width || 280), height = Number(origin.height || 120);
        if(handle.includes('w')){ x += dx; width -= dx; }
        if(handle.includes('e')) width += dx;
        if(handle.includes('n')){ y += dy; height -= dy; }
        if(handle.includes('s')) height += dy;
        if(width < 120){ if(handle.includes('w')) x -= 120-width; width=120; }
        if(height < 64){ if(handle.includes('n')) y -= 64-height; height=64; }
        return {x,y,width,height,auto:false};
    }
    const aspect = Math.max(.01, origin.width / Math.max(1, origin.height));
    const horizontal = handle.includes('e') || handle.includes('w');
    const vertical = handle.includes('n') || handle.includes('s');
    const rawWidth = handle.includes('w') ? origin.width - dx : handle.includes('e') ? origin.width + dx : origin.width;
    const rawHeight = handle.includes('n') ? origin.height - dy : handle.includes('s') ? origin.height + dy : origin.height;
    const widthScale = rawWidth / Math.max(1, origin.width);
    const heightScale = rawHeight / Math.max(1, origin.height);
    let scale = horizontal && vertical
        ? (Math.abs(widthScale - 1) >= Math.abs(heightScale - 1) ? widthScale : heightScale)
        : horizontal ? widthScale : heightScale;
    scale = Math.max(Math.max(80 / origin.width, 60 / origin.height), scale);
    const width = origin.width * scale;
    const height = width / aspect;
    let x = origin.x;
    let y = origin.y;
    if(handle.includes('w')) x = origin.x + origin.width - width;
    else if(!handle.includes('e')) x = origin.x + (origin.width - width) / 2;
    if(handle.includes('n')) y = origin.y + origin.height - height;
    else if(!handle.includes('s')) y = origin.y + (origin.height - height) / 2;
    return {x, y, width, height, auto:false};
}
function fitDetailMediaCanvasAnnotationToText(note, node=null){
    if(!note) return;
    const fontSize = Math.max(8,Math.min(160,Number(note.fontSize || 15)));
    const padding = Math.max(2,Math.min(80,Number(note.padding || 10)));
    const context = document.createElement('canvas').getContext('2d');
    if(!context) return;
    context.font = `700 ${fontSize}px "Source Han Sans CN", "Microsoft YaHei", sans-serif`;
    const paragraphs = String(note.text || '').split('\n');
    const measuredWidths = paragraphs.map(line => context.measureText(line || ' ').width);
    const innerWidth = Math.max(72,Math.min(560,Math.max(...measuredWidths,72)));
    const width = Math.max(120,Math.min(640,innerWidth+padding*2));
    const availableWidth = Math.max(20,width-padding*2);
    let lineCount = 0;
    paragraphs.forEach(paragraph => {
        if(!paragraph){ lineCount += 1; return; }
        let line = '';
        [...paragraph].forEach(character => {
            const next = line+character;
            if(line && context.measureText(next).width > availableWidth){ lineCount += 1; line = character; }
            else line = next;
        });
        if(line) lineCount += 1;
    });
    note.width = width;
    note.height = Math.max(64,lineCount*fontSize*1.5+padding*2);
    const target = node || document.querySelector(`[data-detail-media-annotation-node="${CSS.escape(note.id)}"]`);
    if(target){ target.style.width=`${note.width}px`; target.style.height=`${note.height}px`; }
    syncDetailMediaCanvasGeometryDom();
}
function beginDetailMediaCanvasAnnotationEdit(id){
    const note = detailMediaCanvasAnnotation(id);
    if(!note) return;
    if(detailViewerState.mediaCanvasEditingAnnotationId === id){
        document.querySelector(`[data-detail-media-annotation="${CSS.escape(id)}"]`)?.focus();
        return;
    }
    finishDetailMediaCanvasAnnotationEdit();
    selectDetailMediaCanvasItem(id,{});
    setDetailMediaCanvasAnnotationSnapshot(captureDetailMediaCanvasSnapshot());
    detailViewerState.mediaCanvasEditingAnnotationId = id;
    renderDetailViewer();
    requestAnimationFrame(() => {
        const editor = document.querySelector(`[data-detail-media-annotation="${CSS.escape(id)}"]`);
        editor?.focus(); editor?.setSelectionRange?.(editor.value.length,editor.value.length);
    });
}
function finishDetailMediaCanvasAnnotationEdit(){
    const id = detailViewerState.mediaCanvasEditingAnnotationId || '';
    if(!id) return false;
    const note = detailMediaCanvasAnnotation(id);
    const before = detailMediaCanvasAnnotationSnapshot;
    if(note) fitDetailMediaCanvasAnnotationToText(note);
    detailViewerState.mediaCanvasEditingAnnotationId = '';
    setDetailMediaCanvasAnnotationSnapshot(null);
    if(note && before && detailMediaCanvasSnapshotSignature(before) !== detailMediaCanvasSnapshotSignature(captureDetailMediaCanvasSnapshot())) pushDetailMediaCanvasHistory(before);
    return true;
}
function beginDetailMediaCanvasPan(event){
    const viewport = event.target.closest?.('[data-detail-media-canvas-viewport]');
    if(!viewport || event.target.closest?.('.asset-detail-media-minimap,.asset-detail-media-context-menu,.asset-detail-video-dock,.asset-detail-media-annotation-editor,[data-detail-media-lut-split]')) return;
    if(event.button === 1 || (event.button === 0 && detailMediaCanvasSpacePressed)){
        event.preventDefault();
        closeDetailMediaCanvasContextMenu();
        setDetailMediaCanvasPanState({
            viewport, pointerId:event.pointerId, startX:event.clientX, startY:event.clientY,
            originX:Number(detailViewerState.mediaCanvasX || 0), originY:Number(detailViewerState.mediaCanvasY || 0),
        });
        viewport.setPointerCapture?.(event.pointerId);
        viewport.classList.add('is-panning');
        return;
    }
    if(event.button !== 0 || event.target.closest?.('button,a,input,textarea,select')) return;
    event.preventDefault();
    viewport.focus({preventScroll:true});
    const targetAnnotationId = event.target.closest?.('[data-detail-media-annotation-node]')?.dataset.detailMediaAnnotationNode || '';
    const annotationFinished = targetAnnotationId !== detailViewerState.mediaCanvasEditingAnnotationId && finishDetailMediaCanvasAnnotationEdit();
    closeDetailMediaCanvasContextMenu();
    const startWorld = detailMediaCanvasWorldPoint(event.clientX,event.clientY,viewport);
    const handle = event.target.closest?.('[data-detail-media-resize]');
    const node = event.target.closest?.('[data-detail-media-object]');
    if(handle && node){
        const id = node.dataset.detailMediaObject || '';
        selectDetailMediaCanvasItem(id,event);
        const origin = {...(detailMediaCanvasObjectBox(id) || {})};
        setDetailMediaCanvasEditState({mode:'resize',viewport,pointerId:event.pointerId,id,handle:handle.dataset.detailMediaResize || 'se',startWorld,origin,moved:false,before:captureDetailMediaCanvasSnapshot()});
        node.classList.add('is-resizing');
    } else if(node){
        const id = node.dataset.detailMediaObject || '';
        const selected = detailMediaCanvasSelectedIds();
        const keepMultiSelection = !event.shiftKey && !event.ctrlKey && !event.metaKey && selected.size > 1 && selected.has(id);
        const remainsSelected = keepMultiSelection || selectDetailMediaCanvasItem(id,event);
        if(!remainsSelected){
            if(annotationFinished) requestAnimationFrame(renderDetailViewer);
            return;
        }
        const origins = new Map([...detailMediaCanvasSelectedIds()].map(selectedId => [selectedId,{...(detailMediaCanvasObjectBox(selectedId) || {})}]).filter(([,box]) => box.width > 0));
        setDetailMediaCanvasEditState({mode:'move',viewport,pointerId:event.pointerId,id,startWorld,origins,moved:false,before:captureDetailMediaCanvasSnapshot(),collapseTo:keepMultiSelection ? id : ''});
        node.classList.add('is-moving');
    } else {
        const rect = viewport.getBoundingClientRect();
        const marquee = document.createElement('div');
        marquee.className = 'asset-detail-media-marquee';
        viewport.appendChild(marquee);
        setDetailMediaCanvasEditState({
            mode:'marquee',viewport,pointerId:event.pointerId,startWorld,
            startLocal:{x:event.clientX-rect.left,y:event.clientY-rect.top},
            additive:event.ctrlKey || event.metaKey || event.shiftKey,
            base:new Set(detailMediaCanvasSelectedIds()),baseOrder:[...detailMediaCanvasSelectionOrder()],marquee,moved:false,
        });
    }
    if(detailMediaCanvasEditState) detailMediaCanvasEditState.annotationFinished = annotationFinished;
    viewport.setPointerCapture?.(event.pointerId);
}
function updateDetailMediaCanvasPan(event){
    if(detailMediaCanvasPanState && event.pointerId === detailMediaCanvasPanState.pointerId){
        detailViewerState.mediaCanvasX = detailMediaCanvasPanState.originX + event.clientX - detailMediaCanvasPanState.startX;
        detailViewerState.mediaCanvasY = detailMediaCanvasPanState.originY + event.clientY - detailMediaCanvasPanState.startY;
        detailViewerState.mediaCanvasFitted = true;
        applyDetailMediaCanvasTransform();
        return;
    }
    const state = detailMediaCanvasEditState;
    if(!state || event.pointerId !== state.pointerId) return;
    const current = detailMediaCanvasWorldPoint(event.clientX,event.clientY,state.viewport);
    const dx = current.x - state.startWorld.x;
    const dy = current.y - state.startWorld.y;
    if(Math.hypot(dx,dy) > 2) state.moved = true;
    if(state.mode === 'move'){
        const anchor = state.origins.get(state.id) || state.origins.values().next().value || {x:0,y:0};
        const safeDx = snapDetailMediaCanvasValue(Number(anchor.x || 0)+dx)-Number(anchor.x || 0);
        const safeDy = snapDetailMediaCanvasValue(Number(anchor.y || 0)+dy)-Number(anchor.y || 0);
        state.origins.forEach((origin,id) => {
            setDetailMediaCanvasObjectBox(id,{...origin,x:Number(origin.x || 0)+safeDx,y:Number(origin.y || 0)+safeDy,auto:false});
        });
        syncDetailMediaCanvasGeometryDom();
    } else if(state.mode === 'resize'){
        const snappedDx = detailViewerState.mediaCanvasSnap ? Math.round(dx/10)*10 : dx;
        const snappedDy = detailViewerState.mediaCanvasSnap ? Math.round(dy/10)*10 : dy;
        setDetailMediaCanvasObjectBox(state.id,{...resizeDetailMediaCanvasBox(state.origin,state.handle,snappedDx,snappedDy,!detailMediaCanvasAnnotation(state.id)),userResized:true});
        syncDetailMediaCanvasGeometryDom();
    } else if(state.mode === 'marquee'){
        const rect = state.viewport.getBoundingClientRect();
        const local = {x:event.clientX-rect.left,y:event.clientY-rect.top};
        state.marquee.style.left = `${Math.min(state.startLocal.x,local.x)}px`;
        state.marquee.style.top = `${Math.min(state.startLocal.y,local.y)}px`;
        state.marquee.style.width = `${Math.abs(local.x-state.startLocal.x)}px`;
        state.marquee.style.height = `${Math.abs(local.y-state.startLocal.y)}px`;
    }
}
function endDetailMediaCanvasPan(event){
    if(detailMediaCanvasPanState && event.pointerId === detailMediaCanvasPanState.pointerId){
        detailMediaCanvasPanState.viewport?.classList.remove('is-panning');
        setDetailMediaCanvasPanState(null);
        return;
    }
    const state = detailMediaCanvasEditState;
    if(!state || event.pointerId !== state.pointerId) return;
    if(state.mode === 'marquee'){
        const end = detailMediaCanvasWorldPoint(event.clientX,event.clientY,state.viewport);
        const bounds = {left:Math.min(state.startWorld.x,end.x),right:Math.max(state.startWorld.x,end.x),top:Math.min(state.startWorld.y,end.y),bottom:Math.max(state.startWorld.y,end.y)};
        const selected = state.additive ? new Set(state.base) : new Set();
        if(state.moved){
            detailMediaCanvasItems().forEach(item => {
                const box = detailViewerState.mediaCanvasLayout?.[item.id] || {};
                if(Number(box.x||0) < bounds.right && Number(box.x||0)+Number(box.width||0) > bounds.left && Number(box.y||0) < bounds.bottom && Number(box.y||0)+Number(box.height||0) > bounds.top) selected.add(detailMediaCanvasSelectionObjectId(item.id));
            });
            detailMediaCanvasAnnotations().forEach(note => {
                const box = detailMediaCanvasObjectBox(note.id) || {};
                if(Number(box.x||0) < bounds.right && Number(box.x||0)+Number(box.width||0) > bounds.left && Number(box.y||0) < bounds.bottom && Number(box.y||0)+Number(box.height||0) > bounds.top) selected.add(detailMediaCanvasSelectionObjectId(note.id));
            });
        }
        detailViewerState.mediaCanvasSelectedIds = selected;
        detailViewerState.mediaCanvasSelectionOrder = [...(state.additive ? state.baseOrder : []),...selected].filter((id,index,values) => selected.has(id) && values.indexOf(id) === index);
        state.marquee?.remove();
        syncDetailMediaCanvasSelectionDom();
    }
    if(['move','resize'].includes(state.mode)){
        document.querySelectorAll('.asset-detail-viewer .is-moving,.asset-detail-viewer .is-resizing').forEach(node => node.classList.remove('is-moving','is-resizing'));
        if(state.moved) pushDetailMediaCanvasHistory(state.before);
        else if(state.mode === 'move' && state.collapseTo) selectDetailMediaCanvasItem(state.collapseTo,{});
    }
    detailViewerState.mediaCanvasFitted = true;
    setDetailMediaCanvasEditState(null);
    if(state.annotationFinished) requestAnimationFrame(renderDetailViewer);
}
function activateDetailMediaCanvasItem(id, rerender=true, preserveSelection=false){
    if(!id) return;
    const item = detailMediaCanvasItems().find(value => value.id === id);
    if(!item) return;
    if(detailViewerState.mediaCanvasActiveId && detailViewerState.mediaCanvasActiveId !== id){
        document.querySelectorAll('.asset-detail-viewer video.asset-detail-player').forEach(video => { if(video.closest('[data-detail-media-canvas-node]')?.dataset.detailMediaCanvasNode !== id) video.pause?.(); });
    }
    syncDetailReferenceTitleFromDom();
    const next = detailViewerState.items.findIndex(value => value.id === id);
    if(next >= 0) detailViewerState.index = next;
    detailViewerState.id = id;
    detailViewerState.mediaCanvasActiveId = id;
    const objectId = detailMediaCanvasSelectionObjectId(id);
    if(!preserveSelection && !detailMediaCanvasSelectedIds().has(objectId)) setDetailMediaCanvasSelection([objectId],id);
    detailViewerSyncSelection(detailViewerState.source, item);
    if(rerender) renderDetailViewer();
    else { syncDetailMediaCanvasActiveDom(); refreshDetailMediaCanvasSidebarDetail(); }
    loadDetailViewerFileInfo();
    loadDetailImageVersions(item);
    if(detailViewerState.source === 'registry' || detailMediaCanvasPersistId(item)) hydrateDetailRegistryAsset(id);
}
function cycleDetailMediaCanvasItem(delta){
    const items = detailMediaCanvasItems();
    if(items.length < 2) return;
    const current = Math.max(0, items.findIndex(value => value.id === detailViewerState.mediaCanvasActiveId));
    activateDetailMediaCanvasItem(items[(current + Number(delta) + items.length) % items.length]?.id || '',false,true);
}
function detailMediaCanvasActionIds(){
    const objectIds = new Set(detailMediaCanvasObjectIds());
    const selected = detailMediaCanvasSelectionOrder().filter(id => objectIds.has(id));
    if(selected.length) return selected;
    return [...objectIds];
}
function detailMediaCanvasVisualOrder(ids, mode='grid'){
    return ids.map((id,index) => ({id,index,box:detailMediaCanvasObjectBox(id)})).filter(value => value.box).sort((left,right) => {
        const primary = mode === 'horizontal'
            ? Number(left.box.x || 0)-Number(right.box.x || 0)
            : Number(left.box.y || 0)-Number(right.box.y || 0);
        const secondary = mode === 'horizontal'
            ? Number(left.box.y || 0)-Number(right.box.y || 0)
            : Number(left.box.x || 0)-Number(right.box.x || 0);
        return primary || secondary || left.index-right.index;
    });
}
function arrangeDetailMediaCanvasSelection(mode='grid'){
    const ids = detailMediaCanvasActionIds();
    if(!ids.length) return;
    pushDetailMediaCanvasHistory();
    const boxes = detailMediaCanvasVisualOrder(ids,mode).map(value => ({id:value.id,box:{...value.box}})).filter(value => value.box.width > 0 && value.box.height > 0);
    if(!boxes.length) return;
    const gap = 10;
    const startX = Math.min(...boxes.map(value => Number(value.box.x || 0)));
    const startY = Math.min(...boxes.map(value => Number(value.box.y || 0)));
    if(mode === 'horizontal' || mode === 'vertical'){
        let cursor = mode === 'horizontal' ? startX : startY;
        boxes.forEach(value => {
            setDetailMediaCanvasObjectBox(value.id,{...value.box,x:mode === 'horizontal' ? cursor : startX,y:mode === 'vertical' ? cursor : startY,auto:false});
            cursor += (mode === 'horizontal' ? value.box.width : value.box.height) + gap;
        });
    } else {
        const columns = Math.max(1,Math.ceil(Math.sqrt(boxes.length)));
        let y = startY;
        for(let index=0;index<boxes.length;index+=columns){
            const row = boxes.slice(index,index+columns);
            let x = startX;
            const rowHeight = Math.max(...row.map(value => value.box.height));
            row.forEach(value => {
                setDetailMediaCanvasObjectBox(value.id,{...value.box,x,y,auto:false});
                x += value.box.width + gap;
            });
            y += rowHeight + gap;
        }
    }
    detailViewerState.mediaCanvasFitted = true;
}
function alignDetailMediaCanvasItems(axis){
    const ids = detailMediaCanvasActionIds();
    if(ids.length < 2){ setStatus('至少需要两个画布对象才能对齐'); return; }
    const entries = ids.map(id => ({id,box:{...(detailMediaCanvasObjectBox(id) || {})}})).filter(value => value.box.width > 0 && value.box.height > 0);
    if(entries.length < 2) return;
    const selectionOrder = detailMediaCanvasSelectionOrder().filter(id => entries.some(value => value.id === id));
    const targetId = selectionOrder.at(-1) || entries.at(-1).id;
    const targetBox = entries.find(value => value.id === targetId)?.box || entries.at(-1).box;
    const horizontalEdge = ['left','center-x','right'].includes(axis);
    const ordered = detailMediaCanvasVisualOrder(ids,horizontalEdge ? 'vertical' : 'horizontal');
    const gap = 10;
    pushDetailMediaCanvasHistory();
    let cursor = horizontalEdge
        ? Math.min(...entries.map(value => Number(value.box.y || 0)))
        : Math.min(...entries.map(value => Number(value.box.x || 0)));
    ordered.forEach(value => {
        const box = {...value.box};
        if(axis === 'left') box.x = targetBox.x;
        else if(axis === 'center-x') box.x = targetBox.x+targetBox.width/2-box.width/2;
        else if(axis === 'right') box.x = targetBox.x+targetBox.width-box.width;
        else if(axis === 'top') box.y = targetBox.y;
        else box.y = targetBox.y+targetBox.height/2-box.height/2;
        if(horizontalEdge){ box.y = cursor; cursor += box.height+gap; }
        else { box.x = cursor; cursor += box.width+gap; }
        box.auto = false;
        setDetailMediaCanvasObjectBox(value.id,box);
    });
    detailViewerState.mediaCanvasFitted = true;
    renderDetailViewer();
    setStatus(`已按最后选择对象对齐 ${ids.length} 项，并保持 10 像素无重叠间距`);
}
function normalizeDetailMediaCanvasItems(mode){
    const ids = detailMediaCanvasActionIds();
    if(!ids.length){ setStatus('请先选择要规格化的素材'); return; }
    const selectionOrder = detailMediaCanvasSelectionOrder().filter(id => ids.includes(id));
    const orderedIds = selectionOrder.length ? selectionOrder : detailMediaCanvasVisualOrder(ids,'grid').map(value => value.id);
    const boxes = orderedIds.map(detailMediaCanvasObjectBox).filter(Boolean);
    const basis = detailViewerState.mediaCanvasNormalizeBasis || 'first';
    const basisBox = basis === 'last' ? boxes.at(-1) : boxes[0];
    const targetWidth = basis === 'average' ? boxes.reduce((total,box) => total+Number(box.width || 0),0)/boxes.length : Number(basisBox?.width || 0);
    const targetHeight = basis === 'average' ? boxes.reduce((total,box) => total+Number(box.height || 0),0)/boxes.length : Number(basisBox?.height || 0);
    pushDetailMediaCanvasHistory();
    ids.forEach(id => {
        const group = detailMediaCanvasGroup(id);
        if(mode === 'original' && group){
            (group.memberIds || []).forEach(memberId => {
                const memberItem = detailMediaCanvasItems().find(value => value.id === memberId);
                const memberNote = detailMediaCanvasAnnotation(memberId);
                const memberBox = detailMediaCanvasRawObjectBox(memberId);
                if(!memberBox || (!memberItem && !memberNote)) return;
                const natural = memberItem ? detailMediaCanvasNaturalSize(memberItem) : {width:280,height:120};
                setDetailMediaCanvasRawObjectBox(memberId,{x:memberBox.x+(memberBox.width-natural.width)/2,y:memberBox.y+(memberBox.height-natural.height)/2,width:natural.width,height:natural.height,auto:false});
            });
            return;
        }
        const item = detailMediaCanvasItems().find(value => value.id === id);
        const note = detailMediaCanvasAnnotation(id);
        const box = detailMediaCanvasObjectBox(id);
        if((!item && !note && !group) || !box) return;
        const natural = item ? detailMediaCanvasNaturalSize(item) : {width:280,height:120};
        const centerX = box.x + box.width / 2;
        const centerY = box.y + box.height / 2;
        let width = natural.width;
        let height = natural.height;
        if(mode === 'width'){
            width = targetWidth || box.width;
            height = width * box.height / Math.max(1,box.width);
        } else if(mode === 'height'){
            height = targetHeight || box.height;
            width = height * box.width / Math.max(1,box.height);
        }
        setDetailMediaCanvasObjectBox(id,{x:centerX-width/2,y:centerY-height/2,width,height,auto:false});
    });
    detailViewerState.mediaCanvasFitted = true;
    renderDetailViewer();
    const basisLabel = basis === 'average' ? '平均值' : basis === 'last' ? '最后一个' : '第一个';
    setStatus(`已按${basisLabel}规格化 ${ids.length} 项`);
}
function groupDetailMediaCanvasSelection(){
    const selectedIds = detailMediaCanvasSelectionOrder();
    if(selectedIds.length < 2){ setStatus('请至少按顺序选择两个画布对象再打组'); return; }
    const memberIds = detailMediaCanvasExpandedObjectIds(selectedIds);
    if(memberIds.length < 2){ setStatus('当前选择不足以打组'); return; }
    const before = captureDetailMediaCanvasSnapshot();
    const selectedGroups = new Set(selectedIds.filter(id => detailMediaCanvasGroup(id)));
    const retainedGroups = detailMediaCanvasGroups().filter(group => !selectedGroups.has(group.id) && !(group.memberIds || []).some(id => memberIds.includes(id)));
    const usedColors = new Set(retainedGroups.map(group => group.color));
    const color = DETAIL_MEDIA_CANVAS_GROUP_COLORS.find(value => !usedColors.has(value)) || DETAIL_MEDIA_CANVAS_GROUP_COLORS[retainedGroups.length % DETAIL_MEDIA_CANVAS_GROUP_COLORS.length];
    const group = {id:`group_${Date.now()}_${Math.random().toString(16).slice(2,8)}`,memberIds,color,padding:18,transparent:false};
    detailViewerState.mediaCanvasGroups = [...retainedGroups,group];
    setDetailMediaCanvasSelection([group.id]);
    pushDetailMediaCanvasHistory(before);
    detailViewerState.mediaCanvasFitted = true;
    renderDetailViewer();
    setStatus(`已将 ${memberIds.length} 个对象打组，可整体移动和缩放`);
}
function ungroupDetailMediaCanvasSelection(){
    const selectedIds = detailMediaCanvasSelectionOrder();
    const groups = selectedIds.map(detailMediaCanvasGroup).filter(Boolean);
    if(!groups.length){ setStatus('请先选择要解组的图片组'); return; }
    const before = captureDetailMediaCanvasSnapshot();
    const groupIds = new Set(groups.map(group => group.id));
    const memberIds = groups.flatMap(group => group.memberIds || []).filter((id,index,values) => values.indexOf(id) === index);
    const memberSet = new Set(memberIds);
    detailMediaCanvasDrawings().forEach(drawing => {
        if(memberSet.has(drawing.id) && memberSet.has(drawing.boundTo)){
            drawing.boundTo = '';
            drawing.standalone = true;
        }
    });
    detailViewerState.mediaCanvasGroups = detailMediaCanvasGroups().filter(group => !groupIds.has(group.id));
    const remainingSelection = selectedIds.filter(id => !groupIds.has(id));
    setDetailMediaCanvasSelection([...remainingSelection,...memberIds]);
    pushDetailMediaCanvasHistory(before);
    renderDetailViewer();
    setStatus(`已解组 ${groups.length} 个图片组`);
}
function addDetailMediaCanvasAnnotation(){
    const menu = detailViewerState.mediaCanvasContextMenu || {};
    const viewport = document.querySelector('[data-detail-media-canvas-viewport]');
    const center = viewport ? detailMediaCanvasWorldPoint(viewport.getBoundingClientRect().left + viewport.clientWidth/2,viewport.getBoundingClientRect().top + viewport.clientHeight/2,viewport) : {x:0,y:0};
    const note = {
        id:`note_${Date.now()}_${Math.random().toString(16).slice(2,8)}`,
        x:snapDetailMediaCanvasValue(Number(menu.open ? menu.worldX : center.x)), y:snapDetailMediaCanvasValue(Number(menu.open ? menu.worldY : center.y)),
        width:280, height:120, text:'', backgroundColor:'#202622', textColor:'#ffffff', fontSize:15, padding:10,
    };
    pushDetailMediaCanvasHistory();
    detailViewerState.mediaCanvasAnnotations.push(note);
    setDetailMediaCanvasSelection([note.id],note.id);
    detailViewerState.mediaCanvasEditingAnnotationId = note.id;
    closeDetailMediaCanvasContextMenu();
    renderDetailViewer();
    requestAnimationFrame(() => {
        setDetailMediaCanvasAnnotationSnapshot(captureDetailMediaCanvasSnapshot());
        document.querySelector(`[data-detail-media-annotation="${CSS.escape(note.id)}"]`)?.focus();
    });
}
function applyDetailMediaCanvasAnnotationColor(kind, color){
    const ids = [...detailMediaCanvasSelectedIds()].filter(id => detailMediaCanvasAnnotation(id));
    if(!ids.length && detailViewerState.mediaCanvasEditingAnnotationId) ids.push(detailViewerState.mediaCanvasEditingAnnotationId);
    ids.forEach(id => {
        const note = detailMediaCanvasAnnotation(id);
        if(note) note[kind === 'text' ? 'textColor' : 'backgroundColor'] = color;
    });
    document.querySelectorAll('.asset-detail-viewer [data-detail-media-annotation-node]').forEach(node => {
        const note = detailMediaCanvasAnnotation(node.dataset.detailMediaAnnotationNode || '');
        if(note){ node.style.setProperty('--annotation-bg',note.backgroundColor || '#202622'); node.style.setProperty('--annotation-text',note.textColor || '#ffffff'); }
    });
}
function detailMediaCanvasExportContent(scopeOverride=''){
    const scope = (scopeOverride || detailViewerState.mediaCanvasExportScope) === 'selection' ? 'selection' : 'all';
    const selectedObjectIds = detailMediaCanvasSelectionOrder().filter(id => detailMediaCanvasObjectIds().includes(id));
    if(scope === 'selection' && !selectedObjectIds.length) throw new Error('请先选择要输出的画布对象');
    const rawIds = scope === 'selection' ? new Set(detailMediaCanvasExpandedObjectIds(selectedObjectIds)) : new Set(detailMediaCanvasRawObjectIds());
    return {
        scope,
        label:scope === 'selection' ? '当前选中' : '全部画布',
        items:detailMediaCanvasItems().filter(item => rawIds.has(item.id)),
        annotations:detailMediaCanvasAnnotations().filter(note => rawIds.has(note.id)),
        groups:scope === 'selection' ? selectedObjectIds.map(detailMediaCanvasGroup).filter(Boolean) : detailMediaCanvasGroups(),
        drawings:detailMediaCanvasDrawings().filter(drawing => scope !== 'selection' || rawIds.has(drawing.id) || (drawing.boundTo && rawIds.has(drawing.boundTo))),
    };
}
function detailMediaCanvasExportMetrics(content){
    let minX=Infinity,minY=Infinity,maxX=-Infinity,maxY=-Infinity;
    const includeBox = box => {
        if(!box) return;
        const x=Number(box.x||0),y=Number(box.y||0),width=Number(box.width||0),height=Number(box.height||0);
        minX=Math.min(minX,x); minY=Math.min(minY,y); maxX=Math.max(maxX,x+width); maxY=Math.max(maxY,y+height);
    };
    content.items.forEach(item => includeBox(detailViewerState.mediaCanvasLayout?.[item.id]));
    content.annotations.forEach(note => includeBox({x:note.x,y:note.y,width:note.width||280,height:note.height||120}));
    content.groups.forEach(group => includeBox(detailMediaCanvasGroupBox(group)));
    content.drawings.forEach(drawing => (drawing.points || []).forEach(point => {
        const radius=Math.max(2,Number(drawing.width||3));
        includeBox({x:Number(point.x||0)-radius,y:Number(point.y||0)-radius,width:radius*2,height:radius*2});
    }));
    if(!Number.isFinite(minX)){ minX=0; minY=0; maxX=1; maxY=1; }
    return {minX,minY,maxX,maxY,width:Math.max(1,maxX-minX),height:Math.max(1,maxY-minY)};
}
function detailReferenceCanvasFilename(scope=detailViewerState.mediaCanvasExportScope){
    const title = String(detailViewerState.referenceCanvasTitle || '参考画布').replace(/[<>:"/\\|?*\x00-\x1f]/g,'_').trim() || '参考画布';
    return `${title}${scope === 'selection' ? '-当前选中' : ''}.png`;
}
async function loadDetailMediaCanvasBitmap(url){
    if(!url) return null;
    const response = await assetManagerApi.getMedia(url);
    if(!response.ok) throw new Error(`素材读取失败：${response.status}`);
    const blob = await response.blob();
    if(typeof createImageBitmap === 'function') return createImageBitmap(blob);
    const objectUrl = URL.createObjectURL(blob);
    try {
        return await new Promise((resolve,reject) => {
            const image = new Image(); image.onload=()=>resolve(image); image.onerror=()=>reject(new Error('素材解码失败')); image.src=objectUrl;
        });
    } finally { setTimeout(() => URL.revokeObjectURL(objectUrl),0); }
}
async function convertDetailMediaCanvasImageToPng(url){
    const response = await assetManagerApi.getMedia(url);
    if(!response.ok) throw new Error(`图片读取失败：${response.status}`);
    const sourceBlob = await response.blob();
    if(sourceBlob.type === 'image/png') return sourceBlob;
    const bitmap = typeof createImageBitmap === 'function' ? await createImageBitmap(sourceBlob) : await loadDetailMediaCanvasBitmap(url);
    if(!bitmap) throw new Error('图片解码失败');
    const sourceWidth = Number(bitmap.width || bitmap.naturalWidth || 1);
    const sourceHeight = Number(bitmap.height || bitmap.naturalHeight || 1);
    const scale = Math.min(1,8192/Math.max(sourceWidth,sourceHeight),Math.sqrt(48_000_000/Math.max(1,sourceWidth*sourceHeight)));
    const canvas = document.createElement('canvas');
    canvas.width = Math.max(1,Math.round(sourceWidth*scale));
    canvas.height = Math.max(1,Math.round(sourceHeight*scale));
    const context = canvas.getContext('2d',{alpha:true});
    if(!context) throw new Error('当前浏览器无法转换剪贴板图片');
    context.drawImage(bitmap,0,0,canvas.width,canvas.height);
    bitmap.close?.();
    return new Promise((resolve,reject) => canvas.toBlob(blob => blob ? resolve(blob) : reject(new Error('PNG 转换失败')),'image/png'));
}
function detailMediaCanvasSelectedImageItems(){
    const selectedIds = new Set(detailMediaCanvasExpandedObjectIds(detailMediaCanvasSelectionOrder()));
    return detailMediaCanvasItems().filter(item => selectedIds.has(item.id) && detailViewerType(detailViewerState.source,item) === 'image');
}
async function copyDetailMediaCanvasSelectionToClipboard(){
    const images = detailMediaCanvasSelectedImageItems();
    if(!images.length) throw new Error('请先选择要复制的图片');
    if(!navigator.clipboard?.write || typeof ClipboardItem === 'undefined') throw new Error('当前浏览器不支持图片剪贴板，请使用最新版 Chrome');
    setStatus(`正在复制${images.length > 1 ? ` ${images.length} 张已选图片` : '已选图片'}…`);
    const pngPromise = images.length === 1
        ? convertDetailMediaCanvasImageToPng(detailViewerUrl(detailViewerState.source,images[0]))
        : renderDetailMediaCanvasPng('selection').then(result => result.blob);
    await navigator.clipboard.write([new ClipboardItem({'image/png':pngPromise})]);
    await pngPromise;
    setStatus(images.length > 1 ? `已将 ${images.length} 张图片合成为 PNG 并复制，可粘贴到画布或其他软件` : '已复制原尺寸 PNG，可粘贴到画布、微信等软件');
}
function drawDetailMediaCanvasText(context, note){
    const fontSize = Math.max(8,Math.min(160,Number(note.fontSize || 15)));
    const padding = Math.max(2,Math.min(80,Number(note.padding || 10)));
    const horizontalPadding = padding;
    const maxWidth = Math.max(20,Number(note.width || 280)-horizontalPadding*2);
    const lineHeight = fontSize*1.5;
    context.save();
    context.fillStyle = note.backgroundColor || '#202622';
    context.fillRect(Number(note.x || 0),Number(note.y || 0),Number(note.width || 280),Number(note.height || 120));
    context.fillStyle = note.textColor || '#ffffff';
    context.font = `700 ${fontSize}px "Source Han Sans CN", "Microsoft YaHei", sans-serif`;
    context.textBaseline = 'top';
    let y = Number(note.y || 0)+padding;
    const bottom = Number(note.y || 0)+Number(note.height || 120)-padding;
    String(note.text || '').split('\n').forEach(paragraph => {
        let line = '';
        [...paragraph].forEach(character => {
            const next = line + character;
            if(line && context.measureText(next).width > maxWidth){
                if(y+lineHeight <= bottom) context.fillText(line,Number(note.x || 0)+horizontalPadding,y,maxWidth);
                y += lineHeight; line = character;
            } else line = next;
        });
        if(line && y+lineHeight <= bottom) context.fillText(line,Number(note.x || 0)+horizontalPadding,y,maxWidth);
        y += lineHeight;
    });
    context.restore();
}
async function renderDetailMediaCanvasPng(scopeOverride=''){
    const content = detailMediaCanvasExportContent(scopeOverride);
    const metrics = detailMediaCanvasExportMetrics(content);
    const maxDimension = 8192;
    const maxPixels = 48_000_000;
    const outputScale = Math.min(1,maxDimension/Math.max(metrics.width,metrics.height),Math.sqrt(maxPixels/Math.max(1,metrics.width*metrics.height)));
    const canvas = document.createElement('canvas');
    canvas.width = Math.max(1,Math.ceil(metrics.width*outputScale));
    canvas.height = Math.max(1,Math.ceil(metrics.height*outputScale));
    const context = canvas.getContext('2d',{alpha:true});
    if(!context) throw new Error('当前浏览器无法合成画布图片');
    context.setTransform(outputScale,0,0,outputScale,-metrics.minX*outputScale,-metrics.minY*outputScale);
    content.groups.forEach(group => {
        const box = detailMediaCanvasGroupBox(group);
        if(!box) return;
        context.fillStyle = group.color || '#173a5e';
        context.fillRect(box.x,box.y,box.width,box.height);
    });
    let skipped = 0;
    for(const item of content.items){
        const box = detailViewerState.mediaCanvasLayout?.[item.id];
        if(!box) continue;
        const type = detailViewerType(detailViewerState.source,item);
        if(type === 'workflow' || workflowSourceId(item)) { skipped += 1; continue; }
        const sourceUrl = type === 'video' ? assetPreviewUrl(detailViewerUrl(detailViewerState.source,item),2048,item) : detailViewerUrl(detailViewerState.source,item);
        try {
            const bitmap = await loadDetailMediaCanvasBitmap(sourceUrl);
            if(!bitmap) { skipped += 1; continue; }
            const sourceWidth = Number(bitmap.width || bitmap.naturalWidth || box.width);
            const sourceHeight = Number(bitmap.height || bitmap.naturalHeight || box.height);
            const ratio = Math.min(box.width/sourceWidth,box.height/sourceHeight);
            const width = sourceWidth*ratio, height = sourceHeight*ratio;
            context.drawImage(bitmap,box.x+(box.width-width)/2,box.y+(box.height-height)/2,width,height);
            bitmap.close?.();
        } catch(_) { skipped += 1; }
    }
    content.drawings.forEach(drawing => {
        const points = drawing.points || [];
        if(points.length < 2) return;
        context.beginPath(); context.moveTo(points[0].x,points[0].y);
        points.slice(1).forEach(point => context.lineTo(point.x,point.y));
        context.strokeStyle = drawing.color || signalFlowAccentColor(); context.lineWidth = Math.max(1,Number(drawing.width || 3));
        context.lineCap='round'; context.lineJoin='round'; context.stroke();
    });
    content.annotations.forEach(note => drawDetailMediaCanvasText(context,note));
    const blob = await new Promise((resolve,reject) => canvas.toBlob(value => value ? resolve(value) : reject(new Error('画布图片生成失败')),'image/png'));
    return {blob,scaled:outputScale<.999,skipped,scope:content.scope,label:content.label};
}
async function exportDetailMediaCanvas(){
    syncDetailReferenceTitleFromDom();
    closeDetailMediaCanvasContextMenu();
    const scope = detailViewerState.mediaCanvasExportScope === 'selection' ? 'selection' : 'all';
    detailMediaCanvasExportContent(scope);
    let handle = null;
    if(typeof window.showSaveFilePicker === 'function'){
        try { handle = await window.showSaveFilePicker({suggestedName:detailReferenceCanvasFilename(scope),types:[{description:'PNG 图片',accept:{'image/png':['.png']}}]}); }
        catch(err){ if(err?.name === 'AbortError') return; throw err; }
    }
    setStatus('正在合成参考画布图片…');
    const result = await renderDetailMediaCanvasPng(scope);
    if(handle){ const writable = await handle.createWritable(); await writable.write(result.blob); await writable.close(); }
    else {
        const href = URL.createObjectURL(result.blob); const link = document.createElement('a');
        link.href=href; link.download=detailReferenceCanvasFilename(scope); document.body.appendChild(link); link.click(); link.remove();
        setTimeout(() => URL.revokeObjectURL(href),1200);
    }
    setStatus(`${result.label}已导出${result.scaled ? '（超大画布已等比缩放）' : ''}${result.skipped ? `，${result.skipped} 项素材未能读取` : ''}`);
}
function detailReferenceCurrentRootId(){
    const board = assetBoardById(detailViewerState.originTab);
    if(board?.sourceBoard) return String(board.rootId || '');
    const selectedSource = board ? String(boardSourceSelection[board.id] || '') : '';
    if(selectedSource && selectedSource !== '__automatic__') return selectedSource;
    const canvasAssetRoot = detailMediaCanvasItems().map(detailMediaCanvasPersistId).map(id => registryAssets.find(item => item.id === id)?.storage_root_id).find(Boolean);
    const onlyVisibleSource = board && !board.sourceBoard ? (board.sources || []).filter(source => !source.hidden) : [];
    return String(detailMediaCanvasItems().find(item => item.storage_root_id)?.storage_root_id || canvasAssetRoot || (onlyVisibleSource.length === 1 ? onlyVisibleSource[0].id : '') || registryDetail?.storage_root_id || '');
}
async function pasteImageBlobToDetailMediaCanvas(blob,sourceName=''){
    if(!blob || !String(blob.type || '').startsWith('image/')) throw new Error('剪贴板中没有可粘贴的图片');
    setStatus('正在把剪贴板图片保存到上传素材库/god-images…');
    let width=1600,height=900;
    try {
        const bitmap = typeof createImageBitmap === 'function' ? await createImageBitmap(blob) : null;
        if(bitmap){ width=Number(bitmap.width||width); height=Number(bitmap.height||height); bitmap.close?.(); }
    } catch(_) {}
    const extension = {'image/jpeg':'jpg','image/webp':'webp','image/gif':'gif','image/png':'png'}[String(blob.type || '').toLowerCase()] || 'png';
    const baseName = String(sourceName || '').replace(/\.[^.]+$/,'').replace(/[<>:"/\\|?*\x00-\x1f]/g,'_').trim() || '粘贴图片';
    const filename = `${baseName}-${new Date().toISOString().replace(/[-:TZ.]/g,'').slice(0,14)}.${extension}`;
    const form = new FormData();
    form.append('path',activeLocalLibraryPath());
    form.append('files',new File([blob],filename,{type:blob.type || 'image/png'}));
    form.append('folder','god-images');
    form.append('kind','local');
    const data = await apiJsonResponse(assetManagerApi.uploadLocalAssets(form));
    const uploaded = Array.isArray(data.files) ? data.files[0] : null;
    if(!uploaded?.id) throw new Error('剪贴板图片导入失败');
    const existed = localAssets.some(item => item.id === uploaded.id);
    setLocalAssets([uploaded,...localAssets.filter(item => item.id !== uploaded.id)]);
    if(!existed) setLocalAssetsTotal(localAssetsTotal + 1);
    setLocalAssetsLoaded(true);
    syncDetailReferenceTitleFromDom();
    pushDetailMediaCanvasHistory();
    const target = {...uploaded,natural_w:width,natural_h:height};
    detailViewerState.mediaCanvasItems.push(target);
    const viewport = document.querySelector('[data-detail-media-canvas-viewport]');
    const rect = viewport?.getBoundingClientRect();
    const center = rect ? detailMediaCanvasWorldPoint(rect.left+rect.width/2,rect.top+rect.height/2,viewport) : {x:0,y:0};
    detailViewerState.mediaCanvasLayout[target.id] = {x:snapDetailMediaCanvasValue(center.x-width/2),y:snapDetailMediaCanvasValue(center.y-height/2),width,height,auto:false};
    detailViewerState.mediaCanvasActiveId = target.id;
    detailViewerState.mediaCanvasLibrarySelectedId = uploaded.id;
    detailViewerState.mediaCanvasLibraryVisible = true;
    setDetailMediaCanvasSelection([target.id],target.id);
    detailViewerState.mediaCanvasFitted = true;
    renderDetailViewer();
    setStatus(`已粘贴并保存到 god-images：${uploaded.name || filename}`);
}
function handleDetailMediaCanvasPaste(event){
    if(!detailViewerState.open || !document.querySelector('[data-detail-media-canvas-viewport]')) return;
    if(event.target?.closest?.('input,textarea,[contenteditable="true"]')) return;
    const file = [...(event.clipboardData?.items || [])].find(item => String(item.type || '').startsWith('image/'))?.getAsFile?.()
        || [...(event.clipboardData?.files || [])].find(value => String(value.type || '').startsWith('image/'));
    if(!file) return;
    event.preventDefault();
    pasteImageBlobToDetailMediaCanvas(file,file.name || '').catch(err => setStatus(err.message || '剪贴板图片粘贴失败'));
}
async function saveDetailMediaCanvasImage(){
    syncDetailReferenceTitleFromDom();
    closeDetailMediaCanvasContextMenu();
    const scope = detailViewerState.mediaCanvasExportScope === 'selection' ? 'selection' : 'all';
    detailMediaCanvasExportContent(scope);
    if(!window.confirm(`将把${scope === 'selection' ? '当前选中内容' : '全部参考画布'}合成为 PNG，保存后自动导入当前素材库。`)) return;
    const rootId = detailReferenceCurrentRootId();
    if(!rootId) throw new Error('当前素材库没有可写的存储来源，请先选择一个本地素材来源');
    setStatus('正在合成并保存到当前素材库…');
    const result = await renderDetailMediaCanvasPng(scope);
    const form = new FormData();
    form.append('file',new File([result.blob],detailReferenceCanvasFilename(scope),{type:'image/png'}));
    form.append('root_id',rootId);
    const board = assetBoardById(detailViewerState.originTab);
    const folder = board ? boardFolderSelection[board.id] || '' : '';
    if(folder && folder !== '__root__') form.append('folder',folder);
    const data = await apiJsonResponse(assetManagerApi.importRegistryAsset(form));
    const asset = normalizeRegistryAsset(data.asset || {});
    if(asset.id){
        const alreadyKnown = registryAssets.some(item => item.id === asset.id);
        setRegistryAssets([asset,...registryAssets.filter(item => item.id !== asset.id)]);
        if(!alreadyKnown && registryTotalKnown) setRegistryTotal(registryTotal + 1);
        detailViewerState.mediaCanvasLibrarySelectedId = asset.id;
        renderDetailViewer();
    }
    setStatus(`${result.label}已保存并导入当前素材库：${asset.name || detailReferenceCanvasFilename(scope)}${result.scaled ? '（已等比缩放）' : ''}`);
}
function handleDetailMediaCanvasContextAction(action){
    if(!action) return;
    syncDetailReferenceTitleFromDom();
    if(action === 'annotation'){ addDetailMediaCanvasAnnotation(); return; }
    if(action === 'snap'){
        detailViewerState.mediaCanvasSnap = !detailViewerState.mediaCanvasSnap;
        closeDetailMediaCanvasContextMenu();
        renderDetailViewer();
        setStatus(detailViewerState.mediaCanvasSnap ? '吸附网格已开启，移动和缩放按 10 像素吸附' : '吸附网格已关闭');
        return;
    }
    if(action === 'export'){
        exportDetailMediaCanvas().catch(err => setStatus(err.message || '参考画布导出失败'));
        return;
    }
    if(action === 'save-image'){
        saveDetailMediaCanvasImage().catch(err => setStatus(err.message || '画布图片保存失败'));
        return;
    }
    if(action === 'group'){
        closeDetailMediaCanvasContextMenu();
        groupDetailMediaCanvasSelection();
        return;
    }
    if(action === 'ungroup'){
        closeDetailMediaCanvasContextMenu();
        ungroupDetailMediaCanvasSelection();
        return;
    }
    if(action.startsWith('arrange-')){
        arrangeDetailMediaCanvasSelection(action.replace('arrange-',''));
        closeDetailMediaCanvasContextMenu();
        renderDetailViewer();
        setStatus('画布对象已按 10 像素间距重新排列');
        return;
    }
    if(action.startsWith('align-')){
        closeDetailMediaCanvasContextMenu();
        alignDetailMediaCanvasItems(action.replace('align-',''));
        return;
    }
    if(action.startsWith('normalize-')){
        closeDetailMediaCanvasContextMenu();
        normalizeDetailMediaCanvasItems(action.replace('normalize-',''));
    }
}
function openDetailMediaCanvasContextMenu(event){
    const viewport = event.target.closest?.('[data-detail-media-canvas-viewport]');
    if(!viewport || event.target.closest?.('.asset-detail-media-minimap')) return;
    event.preventDefault();
    const node = event.target.closest?.('[data-detail-media-object]');
    if(node && !detailMediaCanvasSelectedIds().has(node.dataset.detailMediaObject || '')) selectDetailMediaCanvasItem(node.dataset.detailMediaObject || '',{});
    const rect = viewport.getBoundingClientRect();
    const world = detailMediaCanvasWorldPoint(event.clientX,event.clientY,viewport);
    detailViewerState.mediaCanvasContextMenu = {
        open:true,
        x:Math.max(8,Math.min(viewport.clientWidth-248,event.clientX-rect.left)),
        y:Math.max(8,Math.min(viewport.clientHeight-420,event.clientY-rect.top)),
        worldX:world.x, worldY:world.y,
    };
    renderDetailViewer();
    requestAnimationFrame(() => document.querySelector('[data-detail-media-context-menu] button')?.focus());
}
function addSelectedAssetsToDetailMediaCanvas(){
    const selectedIds = detailMediaCanvasSelection(detailViewerState.source);
    const candidates = detailViewerItems(detailViewerState.source).filter(value => selectedIds.has(value.id) && ['image','video'].includes(detailViewerType(detailViewerState.source, value)));
    if(!candidates.length){ setStatus('请先在素材库选择要加入的图片或视频'); return; }
    syncDetailReferenceTitleFromDom();
    const unique = new Map(detailMediaCanvasItems().map(value => [value.id, value]));
    candidates.forEach(value => unique.set(value.id, value));
    const added = unique.size - detailMediaCanvasItems().length;
    if(added) pushDetailMediaCanvasHistory();
    detailViewerState.mediaCanvasItems = [...unique.values()];
    if(added){
        if(detailViewerState.mediaCanvasItems.length > 1) detailViewerState.mediaCanvasLibraryVisible = true;
        setDetailMediaCanvasSelection(candidates.map(value => value.id),candidates[candidates.length-1]?.id || '');
        detailViewerState.mediaCanvasActiveId = candidates[candidates.length-1]?.id || detailViewerState.mediaCanvasActiveId;
    }
    detailViewerState.mediaCanvasFitted = false;
    renderDetailViewer();
    setStatus(added ? `已向当前画布增加 ${added} 个素材` : '所选素材已在当前画布中');
}
function addDetailMediaCanvasLibraryItem(id,clientX,clientY,candidateOverride=null){
    const stableId = String(id || '').trim();
    const candidate = (candidateOverride && String(candidateOverride.id || candidateOverride.asset_id || '') === stableId
        ? {...candidateOverride, id:stableId, asset_id:stableId, kind:candidateOverride.kind || 'image'} : null)
        || detailMediaCanvasLibraryItems().find(item => (
        String(item.id || '') === stableId || detailMediaCanvasPersistId(item) === stableId
    )) || detailReferenceCanvasCoverflowAssets().find(item => String(item.id || '') === stableId);
    const viewport = document.querySelector('[data-detail-media-canvas-viewport]');
    if(!candidate || !viewport) return;
    const persistId = String(detailMediaCanvasPersistId(candidate) || candidate.asset_id || candidate.assetId || candidate.id || '');
    const existing = detailMediaCanvasItems().find(item => detailMediaCanvasPersistId(item) === persistId);
    const point = detailMediaCanvasWorldPoint(clientX,clientY,viewport);
    pushDetailMediaCanvasHistory();
    let target = existing;
    if(!target){
        target = detailViewerState.source === 'registry' ? candidate : {...candidate,asset_id:persistId};
        detailViewerState.mediaCanvasItems.push(target);
    }
    if(!target.natural_w || !target.natural_h){
        const previewEl = document.querySelector(`.gw-coverflow-slide[data-gw-coverflow-id="${CSS.escape(stableId)}"] :is(img,video)`)
            || document.querySelector(`[data-detail-media-canvas-node="${CSS.escape(stableId)}"] :is(img,video)`)
            || document.querySelector(`img[src*="${CSS.escape(stableId)}"]`);
        const nw = Number(previewEl?.naturalWidth || previewEl?.videoWidth || 0);
        const nh = Number(previewEl?.naturalHeight || previewEl?.videoHeight || 0);
        if(nw > 0 && nh > 0){
            target.natural_w = nw;
            target.natural_h = nh;
        }
    }
    const natural = detailMediaCanvasNaturalSize(target);
    const nextBox = {
        x:snapDetailMediaCanvasValue(point.x-natural.width/2), y:snapDetailMediaCanvasValue(point.y-natural.height/2),
        width:natural.width,height:natural.height,auto:false,
    };
    if(detailViewerState.mediaCanvasLayout[target.id]) setDetailMediaCanvasRawObjectBox(target.id,nextBox);
    else detailViewerState.mediaCanvasLayout[target.id] = nextBox;
    setDetailMediaCanvasSelection([target.id],target.id);
    detailViewerState.mediaCanvasFitted = true;
    renderDetailViewer();
    setStatus(existing ? '素材已移动到拖放位置' : '素材已加入当前参考画布');
}
function allowDetailMediaCanvasDrop(event){
    const viewport = event.target.closest?.('[data-detail-media-canvas-viewport]');
    if(!viewport) return;
    const types = Array.from(event.dataTransfer?.types || []);
    if(!detailMediaLibraryDragId && !types.includes('application/x-gods-reference-asset')) return;
    event.preventDefault();
    if(event.dataTransfer) event.dataTransfer.dropEffect='copy';
    viewport.classList.add('is-asset-drag-over');
}
function dropDetailMediaCanvasLibraryItem(event){
    const viewport = event.target.closest?.('[data-detail-media-canvas-viewport]');
    if(!viewport) return;
    const id = event.dataTransfer?.getData?.('application/x-gods-reference-asset') || detailMediaLibraryDragId || '';
    if(!id) return;
    event.preventDefault();
    viewport.classList.remove('is-asset-drag-over');
    setDetailMediaLibraryDragId('');
    addDetailMediaCanvasLibraryItem(id,event.clientX,event.clientY);
}
function selectDetailMediaCanvasLibraryItem(id){
    const stableId = String(id || '').trim();
    const item = detailMediaCanvasLibraryItems().find(value => (
        String(value.id || '') === stableId || detailMediaCanvasPersistId(value) === stableId
    ));
    if(!item) return;
    detailViewerState.mediaCanvasLibrarySelectedId = id;
    const persistId = String(item.asset_id || item.assetId || item.id || '');
    const canvasItem = detailMediaCanvasItems().find(value => detailMediaCanvasPersistId(value) === persistId);
    if(canvasItem){
        activateDetailMediaCanvasItem(canvasItem.id);
        setStatus(`已在画布中选中：${item.name || '素材'}`);
        return;
    }
    document.querySelectorAll('[data-detail-media-library-item]').forEach(node => {
        const selected = node.dataset.detailMediaLibraryItem === id;
        node.classList.toggle('is-selected',selected);
        node.setAttribute('aria-selected',String(selected));
    });
    setStatus(`已选择图库素材：${item.name || '素材'}，可拖入画布`);
}
function toggleDetailMediaCanvasLibrary(){
    setDetailMediaCanvasLibraryCollapsed(!detailMediaCanvasLibraryCollapsed);
    try { localStorage.setItem(ASSET_DETAIL_MEDIA_LIBRARY_COLLAPSED_KEY,String(detailMediaCanvasLibraryCollapsed)); } catch(_) {}
    renderDetailViewer();
}
function setDetailMediaCanvasSidebarView(view){
    const next=view==='detail' ? 'detail' : 'library';
    detailViewerState.mediaCanvasSidebarView=next;
    if(detailMediaCanvasLibraryCollapsed){
        setDetailMediaCanvasLibraryCollapsed(false);
        try { localStorage.setItem(ASSET_DETAIL_MEDIA_LIBRARY_COLLAPSED_KEY,'false'); } catch(_) {}
    }
    renderDetailViewer();
}
function setDetailMediaCanvasLibraryWidth(width,persist=false){
    gwSetDetailMediaCanvasLibraryWidth(Math.max(280,Math.min(720,Math.round(Number(width || 420)))));
    const body = document.querySelector('.asset-detail-viewer-body.asset-detail-media-library-layout');
    body?.style.setProperty('--media-library-width',`${detailMediaCanvasLibraryWidth}px`);
    const handle = document.querySelector('[data-detail-media-library-resizer]');
    handle?.setAttribute('aria-valuenow',String(detailMediaCanvasLibraryWidth));
    scheduleDetailMediaVideoDockPosition();
    if(persist){
        try { localStorage.setItem(ASSET_DETAIL_MEDIA_LIBRARY_WIDTH_KEY,String(detailMediaCanvasLibraryWidth)); } catch(_) {}
    }
}
function beginDetailMediaCanvasLibraryResize(event){
    const handle = event.target.closest?.('[data-detail-media-library-resizer]');
    if(!handle || event.button !== 0 || window.matchMedia?.('(max-width: 960px)').matches) return;
    event.preventDefault();
    detailMediaCanvasLibraryResizeState = {pointerId:event.pointerId,startX:event.clientX,startWidth:detailMediaCanvasLibraryWidth,handle};
    handle.setPointerCapture?.(event.pointerId);
    handle.classList.add('is-resizing');
}
function updateDetailMediaCanvasLibraryResize(event){
    const state = detailMediaCanvasLibraryResizeState;
    if(!state || state.pointerId !== event.pointerId) return;
    setDetailMediaCanvasLibraryWidth(state.startWidth+state.startX-event.clientX);
}
function endDetailMediaCanvasLibraryResize(event){
    const state = detailMediaCanvasLibraryResizeState;
    if(!state || state.pointerId !== event.pointerId) return;
    state.handle?.classList.remove('is-resizing');
    detailMediaCanvasLibraryResizeState = null;
    setDetailMediaCanvasLibraryWidth(detailMediaCanvasLibraryWidth,true);
}
function removeDetailMediaCanvasItem(id){
    if(!id) return;
    setDetailMediaCanvasSelection([id],id);
    deleteDetailMediaCanvasSelection();
}
function deleteDetailMediaCanvasSelection(){
    const selectedIds = detailMediaCanvasSelectionOrder();
    const rawIds = new Set(detailMediaCanvasExpandedObjectIds(selectedIds));
    const mediaIds = detailMediaCanvasItems().map(item => item.id).filter(id => rawIds.has(id));
    const noteIds = detailMediaCanvasAnnotations().map(note => note.id).filter(id => rawIds.has(id));
    const drawingIds = detailMediaCanvasDrawings().filter(drawing => rawIds.has(drawing.id) || (drawing.boundTo && rawIds.has(drawing.boundTo))).map(drawing => drawing.id);
    if(!mediaIds.length && !noteIds.length && !drawingIds.length) return;
    syncDetailReferenceTitleFromDom();
    pushDetailMediaCanvasHistory();
    const previousActiveId = detailViewerState.mediaCanvasActiveId;
    const nextItems = detailMediaCanvasItems().filter(value => !rawIds.has(value.id));
    detailViewerState.mediaCanvasItems = nextItems;
    detailViewerState.mediaCanvasAnnotations = detailMediaCanvasAnnotations().filter(note => !rawIds.has(note.id));
    detailViewerState.mediaCanvasDrawings = detailMediaCanvasDrawings().filter(drawing => !drawingIds.includes(drawing.id));
    rawIds.forEach(id => { delete detailViewerState.mediaCanvasLayout?.[id]; });
    detailViewerState.mediaCanvasGroups = detailMediaCanvasGroups().map(group => ({...group,memberIds:(group.memberIds || []).filter(id => !rawIds.has(id))})).filter(group => group.memberIds.length > 1);
    detailViewerState.mediaCanvasEditingAnnotationId = '';
    const next = nextItems[0];
    setDetailMediaCanvasSelection(next ? [detailMediaCanvasSelectionObjectId(next.id)] : [],next?.id || '');
    if(mediaIds.includes(previousActiveId)){
        detailViewerState.mediaCanvasActiveId = next?.id || '';
        if(next){
            detailViewerState.id = next.id;
            const nextIndex = detailViewerState.items.findIndex(value => value.id === next.id);
            if(nextIndex >= 0) detailViewerState.index = nextIndex;
            detailViewerSyncSelection(detailViewerState.source,next);
        }
    }
    detailViewerState.mediaCanvasFitted = false;
    renderDetailViewer();
    if(nextItems.length) loadDetailViewerFileInfo();
    setStatus(`已从当前画布移除 ${mediaIds.length+noteIds.length+drawingIds.length} 个对象，素材库原文件不受影响`);
}
async function saveDetailReferenceCanvas(){
    const items = detailMediaCanvasItems();
    if(detailViewerState.referenceCanvasSaving) return;
    syncDetailReferenceTitleFromDom();
    const existingId = detailViewerState.referenceCanvasId || '';
    const actionLabel = existingId ? '更新参考画布' : '保存参考画布';
    const canvasTitle = detailViewerState.referenceCanvasTitle || '未命名参考画布';
    if(!items.length && !existingId){
        const reason='参考画布中没有可保存的素材';
        const error=new Error(reason);
        const failureMessage=`${actionLabel}：${canvasTitle} 失败！`;
        setStatus(failureMessage);
        showAssetNotice(failureMessage,'error',8000);
        showAssetFailure(failureMessage,error);
        throw error;
    }
    detailViewerState.referenceCanvasSaving = true;
    renderDetailViewer();
    try {
        const itemPayload = items.map(item => {
            const box = detailViewerState.mediaCanvasLayout?.[item.id] || {};
            const natural = detailMediaCanvasNaturalSize(item);
            const assetId = detailMediaCanvasPersistId(item);
            const referenceKey = detailMediaCanvasPersistKey(item);
            const sourceUrl = String(item.assetUrl || item.asset_url || item.public_url || item.source_url || item.url || detailViewerUrl(detailViewerState.source,item) || '').trim();
            return {
                id:referenceKey,
                node_id:item.node_id || '',
                reference_key:referenceKey,
                asset_id:assetId,
                sha256:String(item.assetSha256 || item.asset_sha256 || item.sha256 || ''),
                storage_root_id:String(item.assetStorageRootId || item.asset_storage_root_id || item.storage_root_id || ''),
                relative_path:String(item.assetRelativePath || item.asset_relative_path || item.relative_path || ''),
                name:String(item.name || '未命名素材').slice(0,500),
                media_kind:detailViewerType(detailViewerState.source,item),
                source_url:sourceUrl,
                url:String(detailViewerUrl(detailViewerState.source,item) || sourceUrl),
                x:Number(box.x || 0), y:Number(box.y || 0),
                w:Number(box.width || 0), h:Number(box.height || 0),
                natural_w:Number(item.natural_w || item.naturalWidth || item.metadata?.width || natural.width || 0),
                natural_h:Number(item.natural_h || item.naturalHeight || item.metadata?.height || natural.height || 0),
                reference_edit:item.reference_edit && detailViewerType(detailViewerState.source,item) === 'image' ? item.reference_edit : null,
            };
        });
        const data = await apiJsonResponse(assetManagerApi.saveReferenceCanvas({
            canvas_id:existingId,
            title:detailViewerState.referenceCanvasTitle,
            items:itemPayload,
            annotations:(detailViewerState.mediaCanvasAnnotations || []).map(note => ({id:note.id,x:Number(note.x||0),y:Number(note.y||0),w:Number(note.width||280),h:Number(note.height||120),text:String(note.text||'').slice(0,2000),background_color:note.backgroundColor||'#202622',text_color:note.textColor||'#ffffff',font_size:Number(note.fontSize||15),padding:Number(note.padding||10)})),
            drawings:detailMediaCanvasDrawings().map(drawing => {
                const boundItem = items.find(value => value.id === drawing.boundTo);
                return {id:drawing.id,color:drawing.color||signalFlowAccentColor(),width:Number(drawing.width||3),bound_to:boundItem ? detailMediaCanvasPersistKey(boundItem) : '',standalone:Boolean(drawing.standalone || !boundItem),points:(drawing.points||[]).slice(0,5000).map(point => ({x:Number(point.x||0),y:Number(point.y||0)}))};
            }),
            groups:detailMediaCanvasGroups().map(group => ({
                id:group.id,
                member_ids:(group.memberIds || []).map(memberId => {
                    const memberItem = items.find(value => value.id === memberId);
                    return memberItem ? detailMediaCanvasPersistKey(memberItem) : memberId;
                }).filter(Boolean),
                color:group.color || '#173a5e',
                padding:Number(group.padding ?? 18),
                transparent:Boolean(group.transparent),
            })),
            viewport:{
                x:Number(detailViewerState.mediaCanvasX || 0),
                y:Number(detailViewerState.mediaCanvasY || 0),
                scale:Number(detailViewerState.mediaCanvasScale || 1),
            },
        }));
        const savedId = String(data.canvas?.id || existingId || '').trim();
        if(!savedId) throw new Error('服务端没有返回参考画布 ID，保存结果无法确认');
        detailViewerState.referenceCanvasId = savedId;
        const index = await apiJsonResponse(assetManagerApi.getCanvasAssets()).catch(() => null);
        if(!index) throw new Error('画布已写入，但资产索引刷新失败；请刷新后检查参考画布分类');
        setCanvasAssetsData({
            categories:Array.isArray(index.categories) ? index.categories : [],
            canvases:Array.isArray(index.canvases) ? index.canvases : [],
            items:Array.isArray(index.items) ? index.items : [],
        });
        const savedCanvas = canvasAssetsData.canvases.find(canvas => String(canvas.id || '') === savedId);
        if(!savedCanvas || (savedCanvas.kind || 'classic') !== 'reference'){
            throw new Error('画布已写入，但未进入“参考画布”索引；请刷新后重试');
        }
        detailViewerState.referenceCanvasId = savedId;
        setActiveCanvasAssetCategory('reference');
        setActiveCanvasAssetCanvasId(savedId);
        normalizeCanvasAssetState();
        const successMessage = `${actionLabel}：${canvasTitle} 成功！已保存为 .godmap`;
        setStatus(successMessage);
        showAssetNotice(successMessage,'success',8000);
        return data;
    } catch(err) {
        const failureMessage = `${actionLabel}：${canvasTitle} 失败！`;
        setStatus(failureMessage);
        showAssetNotice(failureMessage,'error',8000);
        showAssetFailure(failureMessage,err);
        throw err;
    } finally {
        detailViewerState.referenceCanvasSaving = false;
        renderDetailViewer();
    }
}
function replaceDetailImageEditorCanvasItem(asset,referenceEdit){
    const state = detailImageEditorState;
    const oldId = state.itemId;
    const oldItem = detailMediaCanvasItems().find(item => item.id === oldId) || {};
    const target = {...oldItem,...asset,id:asset.id,asset_id:asset.id,reference_edit:referenceEdit,natural_w:state.crop.w,natural_h:state.crop.h};
    detailViewerState.mediaCanvasItems = detailViewerState.mediaCanvasItems.map(item => item.id === oldId ? target : item);
    const oldBox = detailViewerState.mediaCanvasLayout?.[oldId];
    if(oldBox){ detailViewerState.mediaCanvasLayout[target.id] = oldBox; delete detailViewerState.mediaCanvasLayout[oldId]; }
    detailViewerState.mediaCanvasGroups = detailMediaCanvasGroups().map(group => ({...group,memberIds:(group.memberIds || []).map(id => id === oldId ? target.id : id)}));
    detailViewerState.mediaCanvasDrawings = detailMediaCanvasDrawings().map(drawing => drawing.boundTo === oldId ? {...drawing,boundTo:target.id} : drawing);
    detailViewerState.items = detailViewerState.items.map(item => item.id === oldId ? target : item);
    if(!detailViewerState.items.some(item => item.id === target.id)) detailViewerState.items.push(target);
    detailViewerState.id = target.id;
    detailViewerState.index = Math.max(0,detailViewerState.items.findIndex(item => item.id === target.id));
    detailViewerState.mediaCanvasActiveId = target.id;
    setDetailMediaCanvasSelection([detailMediaCanvasSelectionObjectId(target.id)],target.id);
    state.itemId = target.id;
    state.name = target.name || state.name;
    const known = registryAssets.some(item => item.id === target.id);
    setRegistryAssets([target,...registryAssets.filter(item => item.id !== target.id)]);
    if(!known && registryTotalKnown) setRegistryTotal(registryTotal + 1);
}
document.addEventListener('pointerdown', event => {
    if(event.target?.matches?.('[data-detail-annotation-color]')) setDetailMediaCanvasColorSnapshot(captureDetailMediaCanvasSnapshot());
}, true);
document.addEventListener('focusout', event => {
    if(detailImageEditorState && event.target?.matches?.('[data-image-editor-inline-text]')){
        requestAnimationFrame(() => {
            if(!detailImageEditorState || document.activeElement?.matches?.('[data-image-editor-inline-text]')) return;
            detailImageEditorState.inlineTextId='';
            renderDetailImageEditor();
        });
        return;
    }
    if(!event.target?.matches?.('[data-detail-media-annotation]')) return;
    requestAnimationFrame(() => {
        if(document.activeElement?.matches?.('[data-detail-media-annotation]')) return;
        if(finishDetailMediaCanvasAnnotationEdit()) renderDetailViewer();
    });
});
document.addEventListener('contextmenu', openDetailMediaCanvasContextMenu);
document.addEventListener('paste', handleDetailMediaCanvasPaste);
document.addEventListener('dragover', allowDetailMediaCanvasDrop);
document.addEventListener('drop', dropDetailMediaCanvasLibraryItem);
document.addEventListener('load', captureDetailMediaCanvasNaturalSize, true);
document.addEventListener('loadedmetadata', captureDetailMediaCanvasNaturalSize, true);
document.addEventListener('pointerdown', beginDetailMediaCanvasPan, true);
document.addEventListener('pointerdown', beginDetailMediaCanvasLibraryResize);
document.addEventListener('pointermove', updateDetailMediaCanvasPan);
document.addEventListener('pointermove', updateDetailMediaCanvasLibraryResize);
document.addEventListener('pointerup', endDetailMediaCanvasPan);
document.addEventListener('pointercancel', endDetailMediaCanvasPan);
document.addEventListener('pointerup', endDetailMediaCanvasLibraryResize);
document.addEventListener('pointercancel', endDetailMediaCanvasPan);
document.addEventListener('pointercancel', endDetailMediaCanvasLibraryResize);

// 【QIN-591】域函数面注册：主文件 gwCall('media-canvas', fn) 经由此对象分派。
GW_DEFERRED['media-canvas'] = {
    activateDetailMediaCanvasItem,
    closeDetailMediaCanvasContextMenu,
    copyDetailMediaCanvasSelectionToClipboard,
    cycleDetailMediaCanvasItem,
    deleteDetailMediaCanvasSelection,
    groupDetailMediaCanvasSelection,
    // AC4：主文件首屏绑定的 gw:asset-coverflow-* 监听器经 gwCall 转发到这两个 handler，
    // 必须注册进函数面，否则事件派发时只会报「未注册」。
    handleCoverflowChange,
    handleCoverflowAddToReferenceCanvas,
    selectDetailMediaCanvasLibraryItem,
    setDetailMediaCanvasLibraryWidth,
    setDetailMediaCanvasSelection,
    syncDetailMediaCanvasSelectionDom,
    undoDetailMediaCanvas,
    ungroupDetailMediaCanvasSelection,
};

export {
    activateDetailMediaCanvasItem,
    addDetailMediaCanvasLibraryItem,
    addSelectedAssetsToDetailMediaCanvas,
    adjustDetailMediaCanvasZoom,
    applyDetailMediaCanvasAnnotationColor,
    beginDetailMediaCanvasAnnotationEdit,
    closeDetailMediaCanvasContextMenu,
    customBoardReferenceCanvases,
    deleteDetailMediaCanvasSelection,
    detailMediaCanvasInitialLibraryScope,
    detailMediaCanvasLibraryItems,
    detailReferenceCurrentRootId,
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
    handleCoverflowChange,
    handleCoverflowAddToReferenceCanvas,
};
