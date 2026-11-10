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

// QIN-591：资产库延后域模块（lut）。首屏不加载；经 GW_DEFERRED 注册函数面，
// 由主文件网关（gwCall/gwLoad）按需加载或在空闲时段预热。函数体除跨文件赋值点改写外保持原样。

import {
    DETAIL_LUT_SAMPLE_HEIGHT,
    DETAIL_LUT_SAMPLE_WIDTH,
    DETAIL_MEDIA_LUT_VIDEO_WIDTH,
    LUT_THUMB_CONCURRENCY,
    LUT_THUMB_SAMPLE_HEIGHT,
    LUT_THUMB_SAMPLE_WIDTH,
    activeAssetLibrary,
    apiJsonResponse,
    assetBoardById,
    canvasAssetsData,
    customAssetLibraryBaseItems,
    detailMediaLutCache,
    detailMediaLutDraw,
    detailMediaLutListCache,
    detailMediaLutSamples,
    detailViewerItem,
    detailViewerState,
    detailViewerType,
    detailViewerUrl,
    escapeAttr,
    escapeHtml,
    localAssets,
    localItemMap,
    lutPreviewIsAsset,
    lutPreviewSampleCache,
    lutPreviewSourceCache,
    lutPreviewSourceUrl,
    normalizeRegistryAsset,
    refreshIcons,
    registryAspect,
    registryAssetCategory,
    registryAssets,
    registryCardSize,
    registryLayout,
    registryLoading,
    registryLutThumbQueue,
    registryPageHasMore,
    registrySelectedIds,
    registrySimple,
    registryStatus,
    registryVisibleAssetList,
    renderAssetTreePanel,
    renderRegistryAssetSections,
    renderRegistryFilterChips,
    renderRegistryPagination,
    renderRegistrySelectionBar,
    renderRegistryToolbar,
    root,
    selectedRegistryAssetId,
    setDetailMediaLutSplitDragEndedAt,
    setRegistryAssets,
    setRegistryDetail,
    setRegistryStatus,
    setSelectedRegistryAssetId,
    setStatus,
    showAssetNotice,
} from '../asset-manager.js';
import {
    assetManagerHttp,
} from './http.js';
import {
    LUT_COMPARE_MODES,
    LUT_MEDIA_SAMPLE_MAX_WIDTH,
    LutPreviewError,
    composeLutPreview,
    lutSampleImageData,
    mediaLutSamplable,
    mediaToLutSample,
    paintLutFrame,
    parseCubeLut,
} from './lut-preview.js';
import {
    markAssetFloatingSurface,
    navigateDetailPreview,
    renderRegistryDetailPanel,
    renderRegistryDetailResizer,
} from './preview.js';
import {
    ensureAssetRackDirectoryIndex,
} from './settings.js';
import { GW_DEFERRED } from './state.js';

let registryLutThumbObserver = null;
let registryLutThumbActive = 0;
function lutPreviewSampleFrame(width, height){
    const key = `${width}x${height}`;
    if(!lutPreviewSampleCache.has(key)) lutPreviewSampleCache.set(key, lutSampleImageData(width, height));
    return lutPreviewSampleCache.get(key);
}
async function fetchLutPreviewText(url){
    let response;
    try {
        response = await fetch(url, {credentials:'same-origin'});
    } catch(_) {
        throw new LutPreviewError('LUT_SOURCE_UNAVAILABLE', 'LUT 文件读取失败，请检查素材来源是否可访问');
    }
    if(!response.ok) throw new LutPreviewError('LUT_SOURCE_UNAVAILABLE', `LUT 文件读取失败（HTTP ${response.status}）`);
    return response.text();
}
function loadLutPreview(url){
    const key = String(url || '');
    if(!key) return Promise.reject(new LutPreviewError('LUT_SOURCE_UNAVAILABLE', '该 LUT 没有可读取的文件'));
    const cached = lutPreviewSourceCache.get(key);
    if(cached) return cached;
    const task = fetchLutPreviewText(key).then(text => parseCubeLut(text));
    lutPreviewSourceCache.set(key, task);
    task.catch(() => {
        // 失败不缓存：来源恢复或文件修正后重试应能成功。
        if(lutPreviewSourceCache.get(key) === task) lutPreviewSourceCache.delete(key);
    });
    while(lutPreviewSourceCache.size > 64) lutPreviewSourceCache.delete(lutPreviewSourceCache.keys().next().value);
    return task;
}
function lutPreviewGridLabel(lut){
    if(!lut) return '—';
    return lut.dimension === '3d' ? `${lut.size}³` : `${lut.size} 点`;
}
function failRegistryLutThumb(canvas, message='LUT 解析失败'){
    const wrapper = canvas?.closest?.('[data-lut-thumb]');
    if(!wrapper) return;
    wrapper.classList.add('is-error');
    wrapper.innerHTML = `<span class="registry-lut-thumb-error" title="${escapeAttr(message)}"><i data-lucide="triangle-alert"></i><span>${escapeHtml(message)}</span></span>`;
    refreshIcons();
}
function paintRegistryLutThumb(canvas){
    if(!canvas || !canvas.isConnected) return null;
    if(canvas.dataset.lutThumbState === 'pending' || canvas.dataset.lutThumbState === 'done') return null;
    canvas.dataset.lutThumbState = 'pending';
    const url = String(canvas.dataset.lutThumbUrl || '');
    return loadLutPreview(url).then(lut => {
        if(!canvas.isConnected) return;
        const frame = composeLutPreview(
            lutPreviewSampleFrame(LUT_THUMB_SAMPLE_WIDTH, LUT_THUMB_SAMPLE_HEIGHT),
            lut,
            {strength:1, mode:'applied'},
        );
        if(!paintLutFrame(canvas, frame)){
            canvas.dataset.lutThumbState = 'error';
            failRegistryLutThumb(canvas, '浏览器不支持 LUT 预览');
            return;
        }
        canvas.dataset.lutThumbState = 'done';
        canvas.closest?.('[data-lut-thumb]')?.classList.add('is-ready');
    }).catch(err => {
        canvas.dataset.lutThumbState = 'error';
        failRegistryLutThumb(canvas, err?.code === 'LUT_CUBE_INVALID' ? 'LUT 解析失败' : (err?.message || 'LUT 解析失败'));
    });
}
function pumpRegistryLutThumbQueue(){
    while(registryLutThumbActive < LUT_THUMB_CONCURRENCY && registryLutThumbQueue.length){
        const canvas = registryLutThumbQueue.shift();
        if(!canvas?.isConnected) continue;
        registryLutThumbActive += 1;
        Promise.resolve(paintRegistryLutThumb(canvas)).finally(() => {
            registryLutThumbActive = Math.max(0, registryLutThumbActive - 1);
            pumpRegistryLutThumbQueue();
        });
    }
}
function enqueueRegistryLutThumb(canvas){
    if(!canvas || canvas.dataset.lutThumbState) return;
    canvas.dataset.lutThumbState = 'queued';
    registryLutThumbQueue.push(canvas);
    pumpRegistryLutThumbQueue();
}
function clearRegistryLutThumbObserver(){
    registryLutThumbObserver?.disconnect?.();
    registryLutThumbObserver = null;
}
function scheduleRegistryLutThumbs(){
    clearRegistryLutThumbObserver();
    const canvases = [...root.querySelectorAll('canvas[data-lut-thumb-url]')]
        .filter(canvas => !canvas.dataset.lutThumbState);
    if(!canvases.length) return;
    const scrollRoot = root.querySelector('.registry-assets-scroll');
    if(typeof IntersectionObserver !== 'function' || !scrollRoot){
        canvases.forEach(enqueueRegistryLutThumb);
        return;
    }
    const observer = new IntersectionObserver(entries => {
        if(registryLutThumbObserver !== observer) return;
        entries.forEach(entry => {
            if(!entry.isIntersecting) return;
            observer.unobserve(entry.target);
            enqueueRegistryLutThumb(entry.target);
        });
    }, {root:scrollRoot, rootMargin:'320px'});
    registryLutThumbObserver = observer;
    canvases.forEach(canvas => observer.observe(canvas));
}
/** 【QIN-580】LUT 库子页面的取数口径：索引里所有 .cube。 */
function lutLibraryAssets(){
    return (registryAssets || []).filter(item => registryAssetCategory(item) === 'lut');
}
function renderLutManager(){
    // 与「提示词」「画布资产」同构：机架布局 + 右栏详情，只是取数钉在 kind=lut。
    const embeddedVault = document.documentElement.classList.contains('vault-shell-embedded');
    const items = lutLibraryAssets();
    const visible = registryVisibleAssetList(items);
    if(selectedRegistryAssetId && !items.some(item => item.id === selectedRegistryAssetId)){
        setSelectedRegistryAssetId('');
        setRegistryDetail(null);
    }
    root.innerHTML = `${renderAssetTreePanel()}
    <section class="asset-panel asset-content registry-content registry-layout-${registryLayout} registry-aspect-${registryAspect} ${registrySimple ? 'registry-simple' : ''} ${registrySelectedIds.size ? 'manage-on' : ''}" style="--registry-card-size:${registryCardSize}px">
        ${embeddedVault ? '' : renderRegistryToolbar(null, 'LUT 库', `${items.length} 个色彩查找表（.cube）`)}
        ${renderRegistryFilterChips()}
        <div class="registry-fresh-notice" data-registry-fresh-notice></div>
        <div class="content-scroll registry-assets-scroll" data-registry-zoom-area title="Shift+滚轮调整预览大小">${renderRegistryAssetSections(visible, null, '暂无 LUT。把 .cube 文件放进素材目录后会自动出现在这里。')}${renderRegistryPagination()}</div>
        <div class="registry-vault-footer" aria-label="资产库状态"><span>匹配 LUT <strong>${Number(items.length || 0).toLocaleString('zh-CN')}</strong></span><span>${registryLoading ? '正在读取' : registryPageHasMore ? `已加载 ${visible.length} 项` : `已加载全部 ${visible.length} 项`}</span><span>色彩查找表</span></div>
        ${renderRegistrySelectionBar(null)}
    </section>
    ${renderRegistryDetailResizer()}
    ${renderRegistryDetailPanel()}`;
    void ensureAssetRackDirectoryIndex();
}
let detailLutPreviewState = {lut:null, url:'', token:0};
function detailViewerLutItems(){
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
        if(detailViewerType(detailViewerState.source, value) === 'lut'){
            unique.set(String(value.id || detailViewerUrl(detailViewerState.source, value)), value);
        }
    });
    return [...unique.values()].sort((left,right) => String(left.name || '').localeCompare(String(right.name || ''), 'zh-Hans-CN', {numeric:true, sensitivity:'base'}));
}
function renderDetailLutLibrary(item){
    const luts = detailViewerLutItems();
    return `<aside class="asset-detail-lut-library" data-detail-lut-library>
        <header><div><strong>同库 LUT</strong><span>点击切换套用</span></div><em>${luts.length}</em></header>
        <div class="asset-detail-lut-list">${luts.map(lut => {
            const active = String(lut.id || '') === String(item.id || '');
            const builtin = lut.metadata?.builtin === true || lut.builtin === true;
            const label = lut.metadata?.lut?.title || lut.name || '未命名 LUT';
            return `<button class="asset-detail-lut-item ${active ? 'is-active' : ''}" type="button" data-detail-lut-item="${escapeAttr(lut.id || '')}" ${active ? 'aria-current="true"' : ''} title="${escapeAttr(lut.name || '未命名 LUT')}">
                <i data-lucide="palette"></i>
                <span><strong>${escapeHtml(label)}</strong><small>${escapeHtml(builtin ? '内置' : '用户导入')}</small></span>
            </button>`;
        }).join('') || '<div class="detail-empty"><span>同库没有其他 LUT</span></div>'}</div>
    </aside>`;
}
function detailLutStrengthPercent(){
    const value = Number(detailViewerState.lutStrength);
    const strength = Number.isFinite(value) ? value : 1;
    return Math.max(0, Math.min(100, Math.round(strength * 100)));
}
function detailLutCompareMode(){
    return LUT_COMPARE_MODES.includes(detailViewerState.lutCompare) ? detailViewerState.lutCompare : 'applied';
}
function lutPreviewStage(){
    return document.querySelector('.asset-detail-viewer [data-detail-lut-stage]');
}

/** 已加载素材里能直接拿到的 LUT；接口失败时的兜底，避免菜单空白。 */
function detailMediaLutFallbackItems(){
    const unique = new Map();
    registryAssets.filter(lutPreviewIsAsset).forEach(item => unique.set(String(item.id || ''), item));
    return [...unique.values()];
}
/** 全部已登记 LUT：按 kind 拉取，不依赖当前视图筛选（看图时列表里并没有 LUT）。 */
function detailMediaLutItems(){
    if(detailMediaLutListCache.items) return Promise.resolve(detailMediaLutListCache.items);
    if(detailMediaLutListCache.inflight) return detailMediaLutListCache.inflight;
    const task = (async () => {
        let items = [];
        try {
            const response = await fetch('/api/asset-registry/assets?kind=lut&limit=200&view=card', {credentials:'same-origin'});
            if(response.ok){
                const data = await response.json();
                items = (Array.isArray(data?.items) ? data.items : []).filter(lutPreviewIsAsset);
            }
        } catch(_) { /* 退回已加载素材 */ }
        if(!items.length) items = detailMediaLutFallbackItems();
        items.sort((left, right) => String(left.name || '').localeCompare(String(right.name || ''), 'zh-Hans-CN', {numeric:true, sensitivity:'base'}));
        detailMediaLutListCache.items = items;
        return items;
    })();
    detailMediaLutListCache.inflight = task;
    task.catch(() => {}).then(() => { detailMediaLutListCache.inflight = null; });
    return task;
}
function detailMediaLutLabel(lut){
    return String(lut?.metadata?.lut?.title || lut?.name || '未命名 LUT');
}
function detailMediaLutIsBuiltin(lut){
    return lut?.metadata?.builtin === true || lut?.builtin === true;
}

/* ---- 节点配置 ---- */

function detailMediaLutMap(){
    const map = detailViewerState.mediaCanvasLut;
    return map && typeof map === 'object' ? map : {};
}
/** 节点当前的 LUT 配置；没套用返回 null。 */
function detailMediaLutEntry(itemId){
    const key = String(itemId || '');
    if(!key) return null;
    const entry = detailMediaLutMap()[key];
    return entry && entry.lutId ? entry : null;
}
/**
 * 强度取 0–1。缺失/非法一律当 100%，
 * 不能用 `Number(x) || 0`——那会把「没取到」和「用户拖到 0%」压成同一个值，
 * 表现为选了 LUT 却看不出任何变化（QIN-580 取证踩过）。
 */
function detailMediaLutStrengthOf(entry){
    const value = Number(entry?.strength);
    return Number.isFinite(value) ? Math.max(0, Math.min(1, value)) : 1;
}
function detailMediaLutCompareOf(entry){
    return LUT_COMPARE_MODES.includes(entry?.compare) ? entry.compare : 'applied';
}
/** 分屏分界位置 0–1：没拖过就居中。 */
function detailMediaLutSplitOf(entry){
    const value = Number(entry?.splitX);
    return Number.isFinite(value) ? Math.max(0, Math.min(1, value)) : 0.5;
}
/** 画布里能套 LUT 的素材（下拉一级菜单的数据源）。 */
function detailMediaLutCanvasTargets(){
    return (detailViewerState.mediaCanvasItems || []).filter(item => {
        const type = detailViewerType(detailViewerState.source, item);
        return type === 'image' || type === 'video';
    });
}
function detailMediaLutTargetById(itemId){
    const key = String(itemId || '');
    return detailMediaLutCanvasTargets().find(item => String(item.id || '') === key) || null;
}
/** 记一次配置变更：没套用就把键删掉，别留 `{lutId:''}` 这种脏数据。 */
function writeDetailMediaLutEntry(itemId, entry){
    const key = String(itemId || '');
    if(!key) return;
    const map = {...detailMediaLutMap()};
    if(entry && entry.lutId) map[key] = entry;
    else delete map[key];
    detailViewerState.mediaCanvasLut = map;
}

/* ---- DOM 定位 ---- */

function detailMediaLutNode(itemId){
    const key = String(itemId || '');
    if(!key) return null;
    return [...document.querySelectorAll('.asset-detail-viewer [data-detail-media-canvas-node]')]
        .find(node => String(node.dataset.detailMediaCanvasNode || '') === key) || null;
}
function detailMediaLutNodeMedia(node){
    return node?.querySelector('.asset-detail-media-node-clip > img.asset-detail-media-node-content, .asset-detail-media-node-clip > video.asset-detail-media-node-content') || null;
}
/** 覆盖 canvas 按需注入媒体容器：节点重建后由绘制逻辑重新挂上。 */
function detailMediaLutCanvas(media){
    const host = media?.parentElement;
    if(!host) return null;
    let canvas = host.querySelector('[data-detail-media-lut-canvas]');
    if(!canvas){
        canvas = document.createElement('canvas');
        canvas.className = 'asset-detail-media-lut-canvas';
        canvas.dataset.detailMediaLutCanvas = '';
        canvas.setAttribute('aria-hidden', 'true');
        canvas.hidden = true;
        host.appendChild(canvas);
    }
    return canvas;
}
/**
 * 覆盖 canvas 的等比内容框：canvas 用 `object-fit:contain` 铺满素材框，
 * 素材比例与节点比例不一致时会留黑边，分割线要贴内容边界而不是节点边界。
 */
function detailMediaLutContentBox(canvas){
    const hostWidth = canvas?.clientWidth || canvas?.width || 0;
    const hostHeight = canvas?.clientHeight || canvas?.height || 0;
    const frameWidth = canvas?.width || hostWidth;
    const frameHeight = canvas?.height || hostHeight;
    if(!hostWidth || !hostHeight) return {left:0, top:0, width:hostWidth || 0, height:hostHeight || 0};
    const scale = Math.min(hostWidth / frameWidth, hostHeight / frameHeight) || 1;
    const width = frameWidth * scale;
    const height = frameHeight * scale;
    return {left:(hostWidth - width) / 2, top:(hostHeight - height) / 2, width, height};
}
/** 分屏分割线按需注入素材框（和 canvas 一样，节点重建后由同步逻辑补回）。 */
function detailMediaLutSplit(media){
    const host = media?.parentElement;
    if(!host) return null;
    let split = host.querySelector('[data-detail-media-lut-split]');
    if(!split){
        split = document.createElement('div');
        split.className = 'asset-detail-media-lut-split';
        split.dataset.detailMediaLutSplit = '';
        split.setAttribute('role', 'separator');
        split.setAttribute('aria-orientation', 'vertical');
        split.setAttribute('aria-label', '左右拖动调整前后对比分界');
        split.innerHTML = '<span class="asset-detail-media-lut-split-handle"><i data-lucide="chevrons-left-right"></i></span>';
        split.hidden = true;
        host.appendChild(split);
        refreshIcons();
    }
    return split;
}
/**
 * 【QIN-580】分屏的左右切分靠 CSS 裁剪完成：canvas 里始终是整幅「套用后」结果，
 * 只把左半边露出来，右半边露出下面那张原图。
 * 拖动时只改这一条 clip-path，不重算像素，所以分割线跟手走。
 */
function applyDetailMediaLutSplitClip(canvas, box, ratio){
    if(!canvas) return;
    const hostWidth = canvas.clientWidth || 0;
    if(!hostWidth){ canvas.style.clipPath = ''; return; }
    const cutX = box.left + box.width * ratio;
    const rightCut = Math.max(0, Math.min(hostWidth, hostWidth - cutX));
    canvas.style.clipPath = rightCut > 0 ? `inset(0 ${(rightCut / hostWidth) * 100}% 0 0)` : '';
}
/** 只有「分屏」且帧已画出时才显示分割线，位置随入口里的 splitX 走。 */
function syncDetailMediaLutSplit(itemId){
    const key = String(itemId || '');
    const node = detailMediaLutNode(key);
    const media = detailMediaLutNodeMedia(node);
    const canvas = media ? detailMediaLutCanvas(media) : null;
    if(!node || !media || !canvas) return;
    const split = detailMediaLutSplit(media);
    if(!split) return;
    const entry = detailMediaLutEntry(key);
    if(!entry || detailMediaLutCompareOf(entry) !== 'split' || canvas.hidden){
        split.hidden = true;
        canvas.style.clipPath = '';
        return;
    }
    const box = detailMediaLutContentBox(canvas);
    const ratio = detailMediaLutSplitOf(entry);
    applyDetailMediaLutSplitClip(canvas, box, ratio);
    split.hidden = false;
    split.style.left = `${Math.round(box.left + box.width * ratio)}px`;
    split.style.top = `${Math.round(box.top)}px`;
    split.style.height = `${Math.round(box.height)}px`;
}
/* 拖动分割线：改 splitX 后重画，抬手即停。 */
let detailMediaLutSplitDrag = null;
function beginDetailMediaLutSplitDrag(event){
    const split = event.target.closest?.('[data-detail-media-lut-split]');
    if(!split || event.button !== 0) return;
    const node = split.closest('[data-detail-media-canvas-node]');
    const canvas = split.parentElement?.querySelector('[data-detail-media-lut-canvas]');
    if(!node || !canvas) return;
    event.preventDefault();
    event.stopPropagation();
    const itemId = String(node.dataset.detailMediaCanvasNode || '');
    if(!detailMediaLutEntry(itemId)) return;
    detailMediaLutSplitDrag = {itemId, split, canvas, pointerId:event.pointerId};
    split.classList.add('is-dragging');
    split.setPointerCapture?.(event.pointerId);
}
function moveDetailMediaLutSplit(event){
    const drag = detailMediaLutSplitDrag;
    if(!drag) return;
    const rect = drag.canvas.getBoundingClientRect();
    const layoutWidth = drag.canvas.clientWidth || 0;
    if(!rect.width || !layoutWidth) return;
    // rect 是视觉尺寸（画布整体 transform 缩放过），换回布局像素再算比例。
    const scale = rect.width / layoutWidth;
    const box = detailMediaLutContentBox(drag.canvas);
    if(!box.width) return;
    const localX = (event.clientX - rect.left) / (scale || 1);
    const fraction = Math.max(0, Math.min(1, (localX - box.left) / box.width));
    const previous = detailMediaLutEntry(drag.itemId);
    if(!previous) return;
    writeDetailMediaLutEntry(drag.itemId, {...previous, splitX:fraction});
    // 只挪分割线与裁剪边，不重算像素：重绘一次全图查表要几十毫秒，拖起来就会「慢半拍」。
    syncDetailMediaLutSplit(drag.itemId);
}
function endDetailMediaLutSplitDrag(){
    const drag = detailMediaLutSplitDrag;
    if(!drag) return;
    detailMediaLutSplitDrag = null;
    drag.split.classList.remove('is-dragging');
    // 拖完抬手时浏览器还可能补一个 click，记下时间让点击分支跳过，别把节点再选一次（会整块重渲染）。
    setDetailMediaLutSplitDragEndedAt(Date.now());
}

/* ---- 绘制 ---- */

async function loadDetailMediaLut(lutId){
    const key = String(lutId || '');
    if(!key) throw new LutPreviewError('LUT_SOURCE_UNAVAILABLE', '还没有选择 LUT');
    if(detailMediaLutCache.has(key)) return detailMediaLutCache.get(key);
    const items = await detailMediaLutItems();
    const selected = items.find(entry => String(entry.id || '') === key);
    if(!selected) throw new LutPreviewError('LUT_SOURCE_UNAVAILABLE', '该 LUT 已不在素材库中');
    const sourceUrl = lutPreviewSourceUrl(selected, 'registry');
    if(!sourceUrl) throw new LutPreviewError('LUT_SOURCE_UNAVAILABLE', '该 LUT 没有可读取的文件');
    const record = {lut: await loadLutPreview(sourceUrl), title: detailMediaLutLabel(selected)};
    detailMediaLutCache.set(key, record);
    return record;
}
/** 单个节点的覆盖绘制；失败只影响该节点，不静默把原图当结果。 */
async function paintDetailMediaLutNode(itemId, options = {}){
    const key = String(itemId || '');
    const entry = detailMediaLutEntry(key);
    const node = detailMediaLutNode(key);
    const media = detailMediaLutNodeMedia(node);
    if(!node || !media) return false;
    const canvas = detailMediaLutCanvas(media);
    if(!canvas) return false;
    if(!entry){
        canvas.hidden = true;
        media.style.visibility = '';
        node.dataset.lutState = 'idle';
        syncDetailMediaLutSplit(key);
        return false;
    }
    if(!mediaLutSamplable(media)){
        // 视频还没元数据、图片还没解码，等加载事件回来再补画。
        node.dataset.lutState = 'pending';
        return false;
    }
    try {
        const record = await loadDetailMediaLut(entry.lutId);
        const isVideo = media.tagName === 'VIDEO';
        let sample = isVideo ? null : detailMediaLutSamples.get(media);
        if(!sample){
            sample = mediaToLutSample(media, {maxWidth:isVideo ? DETAIL_MEDIA_LUT_VIDEO_WIDTH : LUT_MEDIA_SAMPLE_MAX_WIDTH});
            if(!isVideo) detailMediaLutSamples.set(media, sample);
        }
        const compareMode = detailMediaLutCompareOf(entry);
        const frame = composeLutPreview(sample, record.lut, {
            // 【QIN-580】分屏不再把原图拼进右半边：整幅套一次 LUT，左右对比交给 CSS 裁剪实时切分。
            // 否则拖分割线时每个 pointermove 都要重算全图查表，画面明显跟不上手。
            mode:compareMode === 'original' ? 'original' : 'applied',
            strength:detailMediaLutStrengthOf(entry),
        });
        if(!paintLutFrame(canvas, frame)) throw new LutPreviewError('LUT_SOURCE_UNAVAILABLE', '画布绘制失败');
        canvas.hidden = false;
        // 分屏时右半边要靠画布下面那张真实媒体露出来，这时不能把媒体藏掉。
        media.style.visibility = compareMode === 'split' ? '' : 'hidden';
        node.dataset.lutState = 'ready';
        syncDetailMediaLutSplit(key);
        // 首次套用时 LUT 清单还没加载完，角标标题是占位的「LUT」，这里补成真实名字。
        const badge = node.querySelector('[data-detail-media-node-lut]');
        if(badge){
            badge.title = `LUT · ${record.title}`;
            badge.setAttribute('aria-label', `LUT ${record.title}`);
        }
        return true;
    } catch(error){
        const message = error instanceof LutPreviewError ? error.message : `LUT 套用失败：${error?.message || error}`;
        node.dataset.lutState = 'error';
        canvas.hidden = true;
        media.style.visibility = '';
        syncDetailMediaLutSplit(key);
        if(options.notify) setStatus(message);
        return false;
    }
}
/** 同一节点的合成串行化，期间的请求合并成一次补绘（视频逐帧回调很密）。 */
function scheduleDetailMediaLutPaint(itemId, options = {}){
    const key = String(itemId || '');
    if(!key) return;
    if(detailMediaLutDraw.busy.has(key)){
        detailMediaLutDraw.pending.add(key);
        return;
    }
    detailMediaLutDraw.busy.add(key);
    Promise.resolve(paintDetailMediaLutNode(key, options)).finally(() => {
        detailMediaLutDraw.busy.delete(key);
        if(detailMediaLutDraw.pending.delete(key)) scheduleDetailMediaLutPaint(key);
    });
}
/** 视频按帧补画；暂停时 rVFC 不再回调，靠 pause/seeked 补一帧。 */
function bindDetailMediaLutMedia(itemId, media){
    const key = String(itemId || '');
    if(!media || !key || media.dataset.mediaLutBound === key) return;
    media.dataset.mediaLutBound = key;
    if(media.tagName === 'VIDEO'){
        const repaint = () => scheduleDetailMediaLutPaint(key);
        media.addEventListener('seeked', repaint);
        media.addEventListener('pause', repaint);
        media.addEventListener('loadeddata', repaint);
        if(typeof media.requestVideoFrameCallback === 'function'){
            const pump = () => { repaint(); media.requestVideoFrameCallback(pump); };
            media.requestVideoFrameCallback(pump);
        } else {
            media.addEventListener('timeupdate', repaint);
        }
        return;
    }
    if(!media.complete){
        media.addEventListener('load', () => scheduleDetailMediaLutPaint(key), {once:true});
    }
}
/** 画布渲染后的接线入口：节点重建会丢掉 canvas 与角标，这里按需补挂并重画。 */
function hydrateDetailMediaCanvasLut(){
    const entries = detailMediaLutMap();
    Object.keys(entries).forEach(itemId => {
        const node = detailMediaLutNode(itemId);
        const media = detailMediaLutNodeMedia(node);
        if(!node || !media) return;
        refreshDetailMediaNodeLutDom(itemId);
        bindDetailMediaLutMedia(itemId, media);
        scheduleDetailMediaLutPaint(itemId);
        positionDetailMediaNodeLutPanel(itemId);
    });
}

/* ---- 渲染：工具条入口 + 两级菜单 ---- */

function renderDetailMediaLutButton(){
    const open = Boolean(detailViewerState.mediaLutMenu?.open);
    const targets = detailMediaLutCanvasTargets();
    const disabled = !targets.length;
    // 菜单本身挂 body（工具条 overflow:hidden 会裁掉），这里只出按钮。
    return `<span class="asset-detail-media-lut-entry">
        <button class="hw-btn ${open ? 'is-active' : ''}" type="button" data-detail-media-lut-menu aria-haspopup="menu" aria-expanded="${open}" ${disabled ? 'disabled' : ''} title="${disabled ? '画布上没有可套用 LUT 的素材' : '给画布素材套用 LUT'}"><i data-lucide="palette"></i><span>套用 LUT</span><i data-lucide="chevron-down"></i></button>
    </span>`;
}
function renderDetailMediaLutMenu(){
    const menu = detailViewerState.mediaLutMenu || {};
    if(menu.step === 'lut'){
        const assetId = String(menu.assetId || '');
        const asset = detailMediaLutTargetById(assetId);
        const current = String(detailMediaLutEntry(assetId)?.lutId || '');
        const items = detailMediaLutListCache.items || [];
        const list = items.length
            ? items.map(lut => {
                const id = String(lut.id || '');
                const active = id === current;
                return `<button class="asset-detail-media-lut-menu-item ${active ? 'is-active' : ''}" type="button" role="menuitemradio" aria-checked="${active}" data-detail-media-lut-apply="${escapeAttr(id)}">
                    <i data-lucide="palette"></i><span><strong>${escapeHtml(detailMediaLutLabel(lut))}</strong>${detailMediaLutIsBuiltin(lut) ? '' : '<small>用户导入</small>'}</span>${active ? '<i data-lucide="check"></i>' : '<i data-lucide="chevron-right"></i>'}
                </button>`;
            }).join('')
            : '<span class="asset-detail-media-lut-menu-hint"><i data-lucide="loader-2"></i>正在读取 LUT 库…</span>';
        return `<div class="asset-detail-media-lut-menu" role="menu" aria-label="选择 LUT">
            <header><button type="button" data-detail-media-lut-back aria-label="返回素材列表" title="返回素材列表"><i data-lucide="chevron-left"></i></button><span>${escapeHtml(asset?.name || '选择 LUT')}</span></header>
            <div class="asset-detail-media-lut-menu-list">${list}</div>
        </div>`;
    }
    const targets = detailMediaLutCanvasTargets();
    const body = targets.map(item => {
        const entry = detailMediaLutEntry(item.id);
        const type = detailViewerType(detailViewerState.source, item);
        return `<button class="asset-detail-media-lut-menu-item" type="button" role="menuitem" data-detail-media-lut-target="${escapeAttr(item.id)}">
            <i data-lucide="${type === 'video' ? 'film' : 'image'}"></i>
            <span><strong>${escapeHtml(item.name || '未命名素材')}</strong><small>${entry ? '已套用 LUT' : '未套用 LUT'}</small></span>
            <i data-lucide="chevron-right"></i>
        </button>`;
    }).join('') || '<span class="asset-detail-media-lut-menu-hint">画布上没有图片或视频素材</span>';
    return `<div class="asset-detail-media-lut-menu" role="menu" aria-label="选择素材">
        <header><span>选择素材</span></header>
        <div class="asset-detail-media-lut-menu-list">${body}</div>
    </div>`;
}
function refreshDetailMediaLutEntryDom(){
    const host = document.querySelector('.asset-detail-viewer .asset-detail-media-lut-entry');
    if(!host) return;
    host.outerHTML = renderDetailMediaLutButton();
    syncDetailMediaLutMenuDom();
    refreshIcons();
}
/** 菜单挂 body：`[data-detail-media-lut-layer]` 是唯一容器，关菜单就整块摘掉。 */
function detailMediaLutLayer(){
    let layer = document.querySelector('[data-detail-media-lut-layer]');
    if(layer) return layer;
    layer = document.createElement('div');
    layer.dataset.detailMediaLutLayer = '';
    // 必须走统一的浮层登记：详情遮罩是 kind:modal 的浮层，FloatingDismissal 在
    // capture 阶段会把「落在遮罩外」的点击判成外部点击并 stopImmediatePropagation，
    // 菜单里的按钮就永远收不到 click。登记成更高优先级的 popover 后才算「自己人」。
    layer.dataset.floatingPriority = '6';
    document.body.appendChild(layer);
    markAssetFloatingSurface(layer,{
        surface:'asset-detail-media-lut-menu',
        kind:'popover',
        content:'self',
        close:closeDetailMediaLutMenu,
        trigger:'[data-detail-media-lut-menu]',
    });
    layer.addEventListener('click', event => { handleDetailMediaLutMenuClick(event); });
    return layer;
}
function closeDetailMediaLutMenu(){
    document.querySelector('[data-detail-media-lut-layer]')?.remove();
    if(detailViewerState.mediaLutMenu?.open){
        detailViewerState.mediaLutMenu = {open:false, step:'asset', assetId:''};
        refreshDetailMediaLutEntryDom();
    }
}
function syncDetailMediaLutMenuDom(){
    const trigger = document.querySelector('.asset-detail-viewer [data-detail-media-lut-menu]');
    const open = Boolean(detailViewerState.mediaLutMenu?.open) && Boolean(trigger);
    if(!open){
        document.querySelector('[data-detail-media-lut-layer]')?.remove();
        return;
    }
    const layer = detailMediaLutLayer();
    layer.innerHTML = renderDetailMediaLutMenu();
    bindDetailMediaLutMenuTracking();
    positionDetailMediaLutMenu();
    refreshIcons();
}
/** 菜单右下对齐入口按钮；下方放不下就翻到按钮上方，左右都 clamp 在窗口内。 */
function positionDetailMediaLutMenu(){
    const menu = document.querySelector('[data-detail-media-lut-layer] > .asset-detail-media-lut-menu');
    const trigger = document.querySelector('.asset-detail-viewer [data-detail-media-lut-menu]');
    if(!menu || !trigger) return;
    const rect = trigger.getBoundingClientRect();
    const width = menu.offsetWidth || 240;
    const height = menu.offsetHeight || 280;
    const margin = 8;
    let left = Math.round(rect.right - width);
    if(left + width > window.innerWidth - margin) left = window.innerWidth - margin - width;
    if(left < margin) left = margin;
    let top = Math.round(rect.bottom + 6);
    if(top + height > window.innerHeight - margin) top = Math.round(rect.top - 6 - height);
    if(top < margin) top = margin;
    menu.style.left = `${left}px`;
    menu.style.top = `${top}px`;
}
let detailMediaLutMenuTracking = false;
/** 滚动 / 缩放会让按钮跑位，菜单跟着重算一次；只绑一次。 */
function bindDetailMediaLutMenuTracking(){
    if(detailMediaLutMenuTracking) return;
    detailMediaLutMenuTracking = true;
    const follow = () => {
        if(!detailViewerState.mediaLutMenu?.open) return;
        positionDetailMediaLutMenu();
    };
    window.addEventListener('resize', follow);
    window.addEventListener('scroll', follow, true);
}
/**
 * 「套用 LUT」下拉菜单的点击处理：入口按钮在工具条里（走 root 委托），
 * 菜单项挂在 body 浮层里（走浮层自身的监听），两处共用这一份逻辑。
 * 返回 true 表示这次点击已被处理。
 */
function handleDetailMediaLutMenuClick(event){
    if(event.target.closest?.('[data-detail-media-lut-menu]')){ toggleDetailMediaLutMenu().catch(() => {}); return true; }
    if(event.target.closest?.('[data-detail-media-lut-back]')){
        detailViewerState.mediaLutMenu = {open:true, step:'asset', assetId:''};
        refreshDetailMediaLutEntryDom();
        return true;
    }
    const target = event.target.closest?.('[data-detail-media-lut-target]');
    if(target){ pickDetailMediaLutTarget(target.dataset.detailMediaLutTarget || '').catch(() => {}); return true; }
    const apply = event.target.closest?.('[data-detail-media-lut-apply]');
    if(apply){ applyDetailMediaLutToTarget(apply.dataset.detailMediaLutApply || '').catch(() => {}); return true; }
    return false;
}

/* ---- 渲染：节点角标 + 右侧浮窗 ---- */

function renderDetailMediaNodeLut(item, type){
    const entry = detailMediaLutEntry(item.id);
    if(!entry) return '';
    const open = String(detailViewerState.mediaCanvasLutPanel || '') === String(item.id);
    const title = detailMediaLutCache.get(String(entry.lutId || ''))?.title || 'LUT';
    return `<button class="asset-detail-media-node-lut" type="button" data-detail-media-node-lut="${escapeAttr(item.id)}" aria-expanded="${open}" aria-label="LUT ${escapeAttr(title)}" title="LUT · ${escapeAttr(title)}"><i data-lucide="palette"></i></button>
        ${open ? renderDetailMediaNodeLutPanel(item, entry, title, type) : ''}`;
}
function renderDetailMediaNodeLutPanel(item, entry, title, type){
    const strength = Math.round(detailMediaLutStrengthOf(entry) * 100);
    const mode = detailMediaLutCompareOf(entry);
    const current = String(entry.lutId || '');
    const items = detailMediaLutListCache.items || [];
    const list = items.length
        ? items.map(lut => {
            const id = String(lut.id || '');
            const active = id === current;
            return `<button class="asset-detail-media-node-lut-item ${active ? 'is-active' : ''}" type="button" aria-pressed="${active}" data-detail-media-node-lut-item="${escapeAttr(id)}">
                <i data-lucide="palette"></i><span><strong>${escapeHtml(detailMediaLutLabel(lut))}</strong>${detailMediaLutIsBuiltin(lut) ? '' : '<small>用户导入</small>'}</span>
            </button>`;
        }).join('')
        : '<span class="asset-detail-media-lut-menu-hint">正在读取 LUT 库…</span>';
    // 不再给「套用后」按钮：套完 LUT 本来就是套用后状态，再摆一个默认态按钮只会让人以为要按一下才生效。
    // 「分屏」「原图」都是再点一次回到套用后的开关。
    return `<div class="asset-detail-media-node-lut-panel" data-detail-media-node-lut-panel="${escapeAttr(item.id)}" role="dialog" aria-label="LUT 调节">
        <header><strong><i data-lucide="palette"></i><span data-detail-media-node-lut-title>${escapeHtml(title)}</span></strong><button type="button" data-detail-media-node-lut-close aria-label="关闭" title="关闭"><i data-lucide="x"></i></button></header>
        <label class="asset-detail-media-node-lut-strength"><span>强度</span><input type="range" min="0" max="100" step="1" value="${strength}" data-detail-media-node-lut-strength aria-label="LUT 强度"><b data-detail-media-node-lut-strength-label>${strength}%</b></label>
        <div class="asset-detail-lut-compare" role="group" aria-label="前后对比">
            <button type="button" data-detail-media-node-lut-compare="split" aria-pressed="${mode === 'split'}" class="${mode === 'split' ? 'is-active' : ''}" title="左右分屏对比，可拖动素材中间的分割线">分屏</button>
            <button type="button" data-detail-media-node-lut-compare="original" aria-pressed="${mode === 'original'}" class="${mode === 'original' ? 'is-active' : ''}" title="只看原图，再点一次回到套用后">原图</button>
        </div>
        <div class="asset-detail-media-node-lut-list" data-detail-media-node-lut-list>${list}</div>
        <button class="asset-detail-media-node-lut-save" type="button" data-detail-media-node-lut-save><i data-lucide="save"></i><span>保存为版本</span></button>
        <button class="asset-detail-media-node-lut-clear" type="button" data-detail-media-node-lut-clear><i data-lucide="eraser"></i><span>清除 LUT</span></button>
    </div>`;
}
function refreshDetailMediaNodeLutDom(itemId){
    const key = String(itemId || '');
    const node = detailMediaLutNode(key);
    const item = detailMediaLutTargetById(key);
    if(!node || !item) return;
    node.querySelectorAll('[data-detail-media-node-lut], [data-detail-media-node-lut-panel]').forEach(el => el.remove());
    node.insertAdjacentHTML('beforeend', renderDetailMediaNodeLut(item, detailViewerType(detailViewerState.source, item)));
    refreshIcons();
    // 画布缩放时角标要反向缩放回来，刚插入的节点同样适用。
    syncDetailMediaNodeLutScale();
}

/**
 * 浮窗默认贴节点右侧弹出；右侧放不下就整体左移，保证整块留在舞台内。
 * 画布用 transform 缩放，节点的 rect 是视觉尺寸、offsetWidth 才是布局尺寸，
 * 所以这里按 scale 换算，并给浮窗反向缩放，避免 65% 画布里浮窗只剩 145px 宽。
 */
function positionDetailMediaNodeLutPanel(itemId){
    const node = detailMediaLutNode(itemId);
    const panel = node?.querySelector('[data-detail-media-node-lut-panel]');
    const stage = document.querySelector('.asset-detail-viewer .asset-detail-stage');
    if(!node || !panel || !stage) return;
    const nodeRect = node.getBoundingClientRect();
    const stageRect = stage.getBoundingClientRect();
    const layoutWidth = node.offsetWidth || nodeRect.width;
    const scale = layoutWidth ? (nodeRect.width / layoutWidth) || 1 : 1;
    panel.style.transform = scale === 1 ? '' : `scale(${1 / scale})`;
    panel.style.transformOrigin = 'top left';
    const panelWidth = 224;
    const panelHeight = Math.min(panel.offsetHeight || 300, 340);
    let leftCss = layoutWidth + 8 / scale;
    const overflowRight = (nodeRect.left + leftCss * scale + panelWidth) - (stageRect.right - 8);
    if(overflowRight > 0) leftCss -= overflowRight / scale;
    if(leftCss < 8 / scale) leftCss = 8 / scale;
    let topCss = 0;
    const overflowBottom = (nodeRect.top + topCss * scale + panelHeight) - (stageRect.bottom - 8);
    if(overflowBottom > 0) topCss -= overflowBottom / scale;
    if(nodeRect.top + topCss * scale < stageRect.top + 8) topCss = (stageRect.top + 8 - nodeRect.top) / scale;
    panel.style.left = `${Math.round(leftCss)}px`;
    panel.style.top = `${Math.round(topCss)}px`;
    panel.style.right = 'auto';
}
/** 角标同样反向缩放：22px 在 65% 画布里只剩 14px，点起来太费劲。 */
function syncDetailMediaNodeLutScale(){
    document.querySelectorAll('.asset-detail-viewer [data-detail-media-canvas-node]').forEach(node => {
        const badge = node.querySelector('[data-detail-media-node-lut]');
        const knob = node.querySelector('[data-detail-media-lut-split-handle]');
        if(!badge && !knob) return;
        const rect = node.getBoundingClientRect();
        const scale = node.offsetWidth ? (rect.width / node.offsetWidth) || 1 : 1;
        const inverse = scale === 1 ? '' : `scale(${1 / scale})`;
        if(badge) badge.style.transform = inverse;
        // 分屏分割线上的圆钮同样是视觉件，跟着反向缩放，缩到 65% 也还抓得住。
        if(knob) knob.style.transform = inverse;
    });
    const openId = String(detailViewerState.mediaCanvasLutPanel || '');
    if(openId) positionDetailMediaNodeLutPanel(openId);
    // 画布缩放时工具条也会跟着位移，body 上的下拉菜单要重贴一次。
    if(detailViewerState.mediaLutMenu?.open) positionDetailMediaLutMenu();
}

/* ---- 操作 ---- */

/**
 * 【QIN-580】画布上已选中的素材：点「套用 LUT」时如果只选中了唯一一个可套 LUT 的素材，
 * 就直接进 LUT 预设列表，不再让用户把「我刚选的素材」再选一遍；多选或无选中时仍列素材。
 */
function detailMediaLutSelectedTarget(){
    const targets = detailMediaLutCanvasTargets();
    const selected = [...(detailViewerState.mediaCanvasSelectedIds || [])].map(String);
    if(selected.length === 1){
        const hit = targets.find(item => String(item.id || '') === selected[0]);
        if(hit) return hit;
    }
    if(selected.length) return null;
    const activeId = String(detailViewerState.mediaCanvasActiveId || '');
    if(!activeId) return null;
    return targets.find(item => String(item.id || '') === activeId) || null;
}
async function toggleDetailMediaLutMenu(){
    if(detailViewerState.mediaLutMenu?.open){
        detailViewerState.mediaLutMenu = {open:false, step:'asset', assetId:''};
        refreshDetailMediaLutEntryDom();
        return;
    }
    // 先取清单再开菜单：打开就是可用状态，避免闪一次「读取中」。
    await detailMediaLutItems();
    const selected = detailMediaLutSelectedTarget();
    detailViewerState.mediaLutMenu = selected
        ? {open:true, step:'lut', assetId:String(selected.id || '')}
        : {open:true, step:'asset', assetId:''};
    refreshDetailMediaLutEntryDom();
}
async function pickDetailMediaLutTarget(itemId){
    await detailMediaLutItems();
    detailViewerState.mediaLutMenu = {open:true, step:'lut', assetId:String(itemId || '')};
    refreshDetailMediaLutEntryDom();
}
/** 选中 LUT：应用到目标素材，收起菜单，顺手把该节点的调节浮窗打开。 */
async function applyDetailMediaLutToTarget(lutId){
    const assetId = String(detailViewerState.mediaLutMenu?.assetId || '');
    if(!assetId) return;
    const previous = detailMediaLutEntry(assetId);
    writeDetailMediaLutEntry(assetId, {
        lutId:String(lutId || ''),
        strength:previous ? detailMediaLutStrengthOf(previous) : 1,
        compare:previous ? detailMediaLutCompareOf(previous) : 'applied',
    });
    // 先把 LUT 读进缓存，浮窗标题和角标才不会先闪一个占位的「LUT」。
    try { await loadDetailMediaLut(lutId); } catch(_) { /* 读取失败留给绘制阶段统一提示 */ }
    detailViewerState.mediaLutMenu = {open:false, step:'asset', assetId:''};
    detailViewerState.mediaCanvasLutPanel = assetId;
    refreshDetailMediaLutEntryDom();
    refreshDetailMediaNodeLutDom(assetId);
    scheduleDetailMediaLutPaint(assetId, {notify:true});
    positionDetailMediaNodeLutPanel(assetId);
}
async function toggleDetailMediaNodeLutPanel(itemId){
    const key = String(itemId || '');
    if(!key || !detailMediaLutEntry(key)) return;
    if(String(detailViewerState.mediaCanvasLutPanel || '') === key){
        detailViewerState.mediaCanvasLutPanel = '';
        refreshDetailMediaNodeLutDom(key);
        return;
    }
    await detailMediaLutItems();
    detailViewerState.mediaCanvasLutPanel = key;
    refreshDetailMediaNodeLutDom(key);
    positionDetailMediaNodeLutPanel(key);
}
function closeDetailMediaNodeLutPanel(){
    const key = String(detailViewerState.mediaCanvasLutPanel || '');
    if(!key) return;
    detailViewerState.mediaCanvasLutPanel = '';
    refreshDetailMediaNodeLutDom(key);
}
/** 只改标题文本：浮窗不重建，列表滚动位置和浮窗高度都不动。 */
function setDetailMediaNodeLutTitle(itemId, title){
    const node = detailMediaLutNode(itemId);
    if(!node || !title) return;
    const label = node.querySelector('[data-detail-media-node-lut-title]');
    if(label) label.textContent = title;
    const badge = node.querySelector('[data-detail-media-node-lut]');
    if(badge){
        badge.title = `LUT · ${title}`;
        badge.setAttribute('aria-label', `LUT ${title}`);
    }
}
/**
 * 切换浮窗里的 LUT：整块重建会让列表滚回顶部、浮窗高度一变还会上下位移（看着像「跳一下」），
 * 所以这里只就地改选中态与标题，绘制照旧走补绘队列。
 */
async function setDetailMediaNodeLut(itemId, lutId){
    const key = String(itemId || '');
    const previous = detailMediaLutEntry(key);
    if(!previous) return;
    const nextId = String(lutId || '');
    writeDetailMediaLutEntry(key, {...previous, lutId:nextId});
    detailMediaLutNode(key)?.querySelectorAll('[data-detail-media-node-lut-item]').forEach(button => {
        const active = button.dataset.detailMediaNodeLutItem === nextId;
        button.classList.toggle('is-active', active);
        button.setAttribute('aria-pressed', active ? 'true' : 'false');
    });
    let title = detailMediaLutCache.get(nextId)?.title || '';
    if(!title){
        try { title = (await loadDetailMediaLut(nextId))?.title || ''; } catch(_) { /* 失败留给绘制阶段提示 */ }
    }
    setDetailMediaNodeLutTitle(key, title);
    scheduleDetailMediaLutPaint(key, {notify:true});
}
function setDetailMediaNodeLutStrength(itemId, value){
    const key = String(itemId || '');
    const previous = detailMediaLutEntry(key);
    if(!previous) return;
    const percent = Math.max(0, Math.min(100, Math.round(Number(value) || 0)));
    writeDetailMediaLutEntry(key, {...previous, strength:percent / 100});
    const label = detailMediaLutNode(key)?.querySelector('[data-detail-media-node-lut-strength-label]');
    if(label) label.textContent = `${percent}%`;
    scheduleDetailMediaLutPaint(key);
}
function setDetailMediaNodeLutCompare(itemId, mode){
    // 「套用后」没有按钮：默认就是套用后，分屏/原图再点一次即回到套用后。
    if(!LUT_COMPARE_MODES.includes(mode) || mode === 'applied') return;
    const key = String(itemId || '');
    const previous = detailMediaLutEntry(key);
    if(!previous) return;
    const next = detailMediaLutCompareOf(previous) === mode ? 'applied' : mode;
    writeDetailMediaLutEntry(key, {...previous, compare:next});
    const panel = detailMediaLutNode(key)?.querySelector('[data-detail-media-node-lut-panel]');
    panel?.querySelectorAll('[data-detail-media-node-lut-compare]').forEach(button => {
        const active = button.dataset.detailMediaNodeLutCompare === next;
        button.classList.toggle('is-active', active);
        button.setAttribute('aria-pressed', active ? 'true' : 'false');
    });
    syncDetailMediaLutSplit(key);
    scheduleDetailMediaLutPaint(key);
}
function clearDetailMediaNodeLut(itemId){
    const key = String(itemId || '');
    if(!detailMediaLutEntry(key)) return;
    writeDetailMediaLutEntry(key, null);
    detailViewerState.mediaCanvasLutPanel = '';
    const node = detailMediaLutNode(key);
    const media = detailMediaLutNodeMedia(node);
    const canvas = media?.parentElement?.querySelector('[data-detail-media-lut-canvas]');
    if(canvas) canvas.hidden = true;
    if(media) media.style.visibility = '';
    if(node) node.dataset.lutState = 'idle';
    syncDetailMediaLutSplit(key);
    refreshDetailMediaNodeLutDom(key);
}

function renderDetailLutWorkspace(source, item){
    const strength = detailLutStrengthPercent();
    const mode = detailLutCompareMode();
    const meta = item.metadata?.lut || {};
    const builtin = item.metadata?.builtin === true || item.builtin === true;
    const grid = meta.lut_3d_size ? `${Number(meta.lut_3d_size)}³`
        : (meta.lut_1d_size ? `${Number(meta.lut_1d_size)} 点` : '—');
    const samples = Number(meta.sample_count) > 0 ? String(Number(meta.sample_count)) : '—';
    return `<div class="asset-detail-lut-workspace" data-detail-lut-workspace>
        ${renderDetailLutLibrary(item)}
        <section class="asset-detail-lut-stage" data-detail-lut-stage data-lut-state="loading" data-compare="${escapeAttr(mode)}">
            <header>
                <div><i data-lucide="palette"></i><strong>LUT 效果预览</strong><span data-detail-lut-title title="${escapeAttr(item.name || 'LUT')}">${escapeHtml(meta.title || item.name || 'LUT')}</span></div>
                <div class="asset-detail-lut-compare" role="group" aria-label="前后对比">
                    <button type="button" data-detail-lut-compare="applied" aria-pressed="${mode === 'applied'}" class="${mode === 'applied' ? 'is-active' : ''}">套用后</button>
                    <button type="button" data-detail-lut-compare="split" aria-pressed="${mode === 'split'}" class="${mode === 'split' ? 'is-active' : ''}">分屏对比</button>
                    <button type="button" data-detail-lut-compare="original" aria-pressed="${mode === 'original'}" class="${mode === 'original' ? 'is-active' : ''}">原图</button>
                </div>
            </header>
            <div class="asset-detail-lut-canvas-shell">
                <div class="asset-detail-lut-frame" data-detail-lut-frame>
                    <canvas class="asset-detail-lut-canvas" data-detail-lut-canvas width="${DETAIL_LUT_SAMPLE_WIDTH}" height="${DETAIL_LUT_SAMPLE_HEIGHT}" role="img" aria-label="标板样片套用当前 LUT 的预览"></canvas>
                    <span class="asset-detail-lut-tag is-left" aria-hidden="true">原图</span>
                    <span class="asset-detail-lut-tag is-right" aria-hidden="true">LUT</span>
                    <span class="asset-detail-lut-status" data-detail-lut-status aria-live="polite">正在解析 LUT…</span>
                    <span class="asset-detail-lut-error" data-detail-lut-error hidden></span>
                </div>
            </div>
            <footer class="asset-detail-lut-controls">
                <label class="asset-detail-lut-strength"><span>强度</span><input type="range" min="0" max="100" step="1" value="${strength}" data-detail-lut-strength aria-label="LUT 强度"><b data-detail-lut-strength-value>${strength}%</b></label>
                <div class="asset-detail-lut-facts">
                    <span>网格 <b data-detail-lut-fact="grid">${escapeHtml(grid)}</b></span>
                    <span>采样点 <b data-detail-lut-fact="samples">${escapeHtml(samples)}</b></span>
                    <span>来源 <b>${escapeHtml(builtin ? '内置预置' : '用户导入')}</b></span>
                </div>
            </footer>
        </section>
    </div>`;
}
/* ---- QIN-581：把套用结果保存为新的资产版本 ---- */

/**
 * 保存当前节点套用后的画面：图片走服务端无损 PNG 直出，视频走 ffmpeg 转码，
 * 产物作为**新资产**入库（原素材不变）。渲染在服务端完成，前端只提交 LUT 与强度，
 * 保证保存结果与 QIN-580 预览使用同一套查表语义，而不是两套近似实现。
 *
 * 保存成功只把新资产并入当前列表数据，不整树重绘：不打断正在看的预览。
 */
async function saveDetailMediaNodeLutVersion(itemId){
    const key = String(itemId || '');
    const entry = detailMediaLutEntry(key);
    const lutId = String(entry?.lutId || '');
    if(!key || !lutId) return;
    const node = detailMediaLutNode(key);
    const button = node?.querySelector('[data-detail-media-node-lut-save]') || null;
    if(button?.disabled) return;
    const label = button?.querySelector('span');
    const labelText = label?.textContent || '保存为版本';
    if(button){ button.disabled = true; button.dataset.state = 'busy'; }
    if(label) label.textContent = '保存中…';
    try {
        const result = await apiJsonResponse(assetManagerHttp.request(
            `/api/asset-registry/assets/${encodeURIComponent(key)}/lut-renders`,
            {
                method:'POST',
                credentials:'same-origin',
                headers:{'Content-Type':'application/json'},
                body:JSON.stringify({
                    lut_asset_id:lutId,
                    strength:detailMediaLutStrengthOf(entry),
                }),
            },
        ));
        const saved = result?.asset || {};
        const normalized = saved.id ? normalizeRegistryAsset(saved) : null;
        if(normalized?.id && !registryAssets.some(item => item.id === normalized.id)){
            setRegistryAssets([normalized, ...registryAssets]);
            setRegistryStatus({
                ...(registryStatus || {}),
                assets_count:Number(registryStatus?.assets_count || 0) + 1,
            });
        }
        showAssetNotice(`已保存为新资产${saved.name ? `：${saved.name}` : ''}（原素材未改动）`);
    } catch(error){
        const prefix = error?.code ? `${error.code}：` : '';
        showAssetNotice(`${prefix}${error?.message || '保存 LUT 版本失败'}`, 'error', 8000);
    } finally {
        if(button){ button.disabled = false; delete button.dataset.state; }
        if(label) label.textContent = labelText;
    }
}

function setDetailLutPreviewError(message){
    const stage = lutPreviewStage();
    if(!stage) return;
    stage.dataset.lutState = 'error';
    const status = stage.querySelector('[data-detail-lut-status]');
    if(status) status.textContent = '';
    const error = stage.querySelector('[data-detail-lut-error]');
    if(!error) return;
    error.hidden = false;
    error.textContent = message || 'LUT 解析失败';
}
function paintDetailLutPreview(){
    const stage = lutPreviewStage();
    const canvas = stage?.querySelector?.('[data-detail-lut-canvas]');
    if(!stage || !canvas) return;
    const lut = detailLutPreviewState.lut;
    if(!lut) return;
    const mode = detailLutCompareMode();
    const strength = detailLutStrengthPercent();
    const frame = composeLutPreview(
        lutPreviewSampleFrame(DETAIL_LUT_SAMPLE_WIDTH, DETAIL_LUT_SAMPLE_HEIGHT),
        lut,
        {mode, strength:strength / 100},
    );
    if(!paintLutFrame(canvas, frame)){
        setDetailLutPreviewError('浏览器不支持 LUT 预览');
        return;
    }
    stage.dataset.lutState = 'ready';
    stage.dataset.compare = mode;
    const error = stage.querySelector('[data-detail-lut-error]');
    if(error){ error.hidden = true; error.textContent = ''; }
    stage.querySelectorAll('[data-detail-lut-compare]').forEach(button => {
        const active = button.dataset.detailLutCompare === mode;
        button.classList.toggle('is-active', active);
        button.setAttribute('aria-pressed', String(active));
    });
    const strengthLabel = stage.querySelector('[data-detail-lut-strength-value]');
    if(strengthLabel) strengthLabel.textContent = `${strength}%`;
    const slider = stage.querySelector('[data-detail-lut-strength]');
    if(slider && Number(slider.value) !== strength) slider.value = String(strength);
}
function initializeDetailLutPreview(overlay, item){
    const stage = overlay?.querySelector?.('[data-detail-lut-stage]');
    if(!stage) return;
    const token = detailLutPreviewState.token + 1;
    detailLutPreviewState = {lut:null, url:'', token};
    stage.dataset.lutState = 'loading';
    const url = lutPreviewSourceUrl(item, detailViewerState.source);
    const status = stage.querySelector('[data-detail-lut-status]');
    loadLutPreview(url).then(lut => {
        if(token !== detailLutPreviewState.token || !stage.isConnected) return;
        detailLutPreviewState.lut = lut;
        detailLutPreviewState.url = url;
        const grid = stage.querySelector('[data-detail-lut-fact="grid"]');
        if(grid) grid.textContent = lutPreviewGridLabel(lut);
        const samples = stage.querySelector('[data-detail-lut-fact="samples"]');
        if(samples) samples.textContent = String(lut.sampleCount);
        const title = stage.querySelector('[data-detail-lut-title]');
        if(title) title.textContent = lut.title || item.name || 'LUT';
        paintDetailLutPreview();
        if(status) status.textContent = `${lutPreviewGridLabel(lut)} · ${lut.sampleCount} 采样点`;
    }).catch(err => {
        if(token !== detailLutPreviewState.token || !stage.isConnected) return;
        setDetailLutPreviewError(err?.message || 'LUT 解析失败');
    });
}
function setDetailLutStrength(value){
    const percent = Math.max(0, Math.min(100, Math.round(Number(value) || 0)));
    detailViewerState.lutStrength = percent / 100;
    paintDetailLutPreview();
}
function setDetailLutCompare(mode){
    if(!LUT_COMPARE_MODES.includes(mode)) return;
    detailViewerState.lutCompare = mode;
    paintDetailLutPreview();
}
function restoreDetailLutListScroll(){
    const list = document.querySelector('.asset-detail-viewer .asset-detail-lut-list');
    if(list && detailViewerState.lutListScrollTop) list.scrollTop = Math.max(0, Number(detailViewerState.lutListScrollTop || 0));
}
function navigateDetailLutToId(id){
    if(!detailViewerState.open || !id) return;
    detailViewerState.lutListScrollTop = document.querySelector('.asset-detail-viewer .asset-detail-lut-list')?.scrollTop || 0;
    const currentId = detailViewerState.items[detailViewerState.index]?.id || detailViewerState.id;
    const luts = detailViewerLutItems();
    const currentIndex = luts.findIndex(item => item.id === currentId);
    const nextIndex = luts.findIndex(item => item.id === id);
    if(currentIndex < 0 || nextIndex < 0 || currentIndex === nextIndex) return;
    detailViewerState.items = luts;
    detailViewerState.index = currentIndex;
    detailViewerState.id = currentId;
    navigateDetailPreview(nextIndex - currentIndex);
}
// 【QIN-580】分屏分割线要自己吃 pointerdown，必须排在画布平移之前注册（capture 按注册顺序走）。
document.addEventListener('pointerdown', beginDetailMediaLutSplitDrag, true);
document.addEventListener('pointermove', moveDetailMediaLutSplit);
document.addEventListener('pointerup', endDetailMediaLutSplitDrag);
document.addEventListener('pointercancel', endDetailMediaLutSplitDrag);

// 【QIN-591】域函数面注册：主文件 gwCall('lut', fn) 经由此对象分派。
GW_DEFERRED['lut'] = {
    clearRegistryLutThumbObserver,
    closeDetailMediaLutMenu,
    renderLutManager,
    scheduleRegistryLutThumbs,
};

export {
    clearDetailMediaNodeLut,
    closeDetailMediaNodeLutPanel,
    detailMediaLutMap,
    handleDetailMediaLutMenuClick,
    hydrateDetailMediaCanvasLut,
    initializeDetailLutPreview,
    navigateDetailLutToId,
    refreshDetailMediaLutEntryDom,
    renderDetailLutWorkspace,
    renderDetailMediaLutButton,
    restoreDetailLutListScroll,
    saveDetailMediaNodeLutVersion,
    scheduleDetailMediaLutPaint,
    setDetailLutCompare,
    setDetailLutStrength,
    setDetailMediaNodeLut,
    setDetailMediaNodeLutCompare,
    setDetailMediaNodeLutStrength,
    syncDetailMediaNodeLutScale,
    toggleDetailMediaNodeLutPanel,
};
