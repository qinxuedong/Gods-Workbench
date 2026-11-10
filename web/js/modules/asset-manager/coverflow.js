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

const OPEN_EVENT = 'gw:asset-coverflow-open';
const CLOSE_EVENT = 'gw:asset-coverflow-close';
const CHANGE_EVENT = 'gw:asset-coverflow-change';
const REQUEST_EDIT_EVENT = 'gw:asset-coverflow-request-edit';
const ADD_TO_REFERENCE_CANVAS_EVENT = 'gw:asset-coverflow-add-to-reference-canvas';
const STATUS_EVENT = 'gw:asset-coverflow-status';
const CLOSED_EVENT = 'gw:asset-coverflow-closed';
const REFERENCE_ASSET_MIME = 'application/x-gods-reference-asset';

const IMAGE_EXTENSIONS = new Set([
    'avif', 'bmp', 'gif', 'heic', 'heif', 'jpeg', 'jpg', 'png', 'svg', 'tif', 'tiff', 'webp',
]);
const VIDEO_EXTENSIONS = new Set([
    '3gp', 'avi', 'm4v', 'mkv', 'mov', 'mp4', 'mpeg', 'mpg', 'ogv', 'webm',
]);
const WINDOW_RADIUS = 3;
// QIN-589：全屏几何口径。中心卡目标为基准宽度的 2 倍，夹紧至舞台宽约 62%，
// 并受 16:9 外框高度约束；两侧从改前紧邻缩放（0.86）起每级外推减半；位置按
// 实际宽度累加，空间不足时等比压缩，保证每侧露出 2 张且不越过左右控件。
const FULLSCREEN_CENTER_WIDTH_SHARE = 0.62;
const FULLSCREEN_ADJACENT_SCALE = 0.86;
// 关闭后焦点归还触发控件时打的静默标记：脚本归还的焦点在 Esc / F 关闭路径仍会
// 命中 :focus-visible，下一次真实键盘 / 指针交互时移除。
const FOCUS_QUIET_ATTR = 'data-gw-coverflow-focus-quiet';
const DRAG_THRESHOLD = 42;
const WHEEL_THRESHOLD = 36;

let fullscreenView = null;
let embeddedView = null;
const EDIT_BACKQUOTE_WINDOW_MS = 500;
let lastEditBackquoteTap = null;

function isBackquoteKey(event) {
    return event.code === 'Backquote' || ['·', '~', '`'].includes(event.key);
}

function isBackquoteShortcut(event) {
    return isBackquoteKey(event)
        && !event.ctrlKey && !event.metaKey && !event.altKey && !event.isComposing;
}

function asText(value, fallback = '') {
    const text = String(value == null ? '' : value).trim();
    return text || fallback;
}

function isElement(value) {
    return typeof Element !== 'undefined' && value instanceof Element;
}

function mediaUrl(value) {
    const raw = asText(value);
    if (!raw) return '';
    if (raw.startsWith('/')) return raw;
    if (raw.startsWith('data:image/') || raw.startsWith('data:video/') || raw.startsWith('blob:')) return raw;
    try {
        const parsed = new URL(raw, window.location.href);
        return ['http:', 'https:'].includes(parsed.protocol) ? parsed.href : '';
    } catch (_) {
        return '';
    }
}

function fileExtension(value) {
    const source = asText(value).split(/[?#]/, 1)[0].toLowerCase();
    const match = source.match(/\.([a-z0-9]+)$/);
    return match ? match[1] : '';
}

function mediaKind(asset, source) {
    const kind = asText(asset?.kind || asset?.type || asset?.media_type || asset?.mediaType).toLowerCase();
    // A declared media kind is authoritative. A video/audio record may use a
    // generic image-looking preview URL, but its primary slide must still use
    // the correct original media element.
    if (['image', 'photo', 'picture'].includes(kind)) return 'image';
    if (['video', 'movie', 'film'].includes(kind)) return 'video';
    if (['audio', 'document', 'file', 'model', 'text'].includes(kind)) return '';
    const mime = asText(asset?.mime_type || asset?.mimeType).toLowerCase();
    if (mime.startsWith('image/')) return 'image';
    if (mime.startsWith('video/')) return 'video';
    const extension = fileExtension(source || asset?.name || asset?.url || asset?.public_url);
    if (IMAGE_EXTENSIONS.has(extension)) return 'image';
    if (VIDEO_EXTENSIONS.has(extension)) return 'video';
    return '';
}

function isImageAsset(asset, source) {
    return mediaKind(asset, source) === 'image';
}

function isVideoAsset(asset, source) {
    return mediaKind(asset, source) === 'video';
}

function normalizeAssets(values) {
    const seen = new Set();
    return (Array.isArray(values) ? values : []).map((asset, index) => {
        if (!asset || typeof asset !== 'object') return null;
        const id = asText(asset.id || asset.asset_id || asset.assetId);
        const mediaSrc = mediaUrl(
            asset.mediaSrc || asset.media_src || asset.fullSrc || asset.full_src || asset.fullUrl || asset.full_url
            || asset.mediaUrl || asset.media_url || asset.url || asset.public_url || asset.src || asset.previewUrl || asset.preview_url,
        );
        const kind = mediaKind(asset, mediaSrc);
        if (!id || !mediaSrc || !kind || seen.has(id)) return null;
        seen.add(id);
        const candidateBackdrop = mediaUrl(
            asset.backdropSrc || asset.backdrop_src || asset.thumbnailSrc || asset.thumbnail_url || asset.thumbnailUrl
            || asset.previewUrl || asset.preview_url || asset.src,
        );
        // A video needs an image poster for the slide preview. Do not
        // assign the video URL to an <img> when no preview is available.
        const backdropSrc = candidateBackdrop && (kind === 'image' || candidateBackdrop !== mediaSrc)
            ? candidateBackdrop
            : (kind === 'image' ? mediaSrc : '');
        return {
            id,
            mediaSrc,
            backdropSrc,
            mediaKind: kind,
            name: asText(asset.name || asset.title || asset.filename, `素材 ${index + 1}`),
            raw: asset,
        };
    }).filter(Boolean);
}

function positiveModulo(value, length) {
    return ((value % length) + length) % length;
}

function emit(type, detail) {
    window.dispatchEvent(new CustomEvent(type, {detail}));
}

function status(view, message) {
    emit(STATUS_EVENT, {
        mode: view?.mode || 'fullscreen',
        source: view?.source || '',
        scope: view?.scope || '',
        structureId: view?.structureId || '',
        message,
    });
}

function resolveHost(value) {
    if (isElement(value)) return value;
    if (typeof value === 'string' && value.trim()) {
        try {
            return document.querySelector(value);
        } catch (_) {
            return null;
        }
    }
    return document.querySelector('[data-image-editor-overlay] .asset-image-editor-shell');
}

function createIcon(name) {
    const icon = document.createElement('i');
    icon.setAttribute('data-lucide', name);
    icon.setAttribute('aria-hidden', 'true');
    return icon;
}

function createButton({className = '', label, title = label, icon, action, index} = {}) {
    const button = document.createElement('button');
    button.type = 'button';
    button.className = className;
    button.setAttribute('aria-label', label || '操作');
    if (title) button.title = title;
    if (action) button.dataset.gwCoverflowAction = action;
    if (index !== undefined) button.dataset.gwCoverflowIndex = String(index);
    if (icon) button.append(createIcon(icon));
    return button;
}

function refreshIcons(container) {
    if (window.lucide?.createIcons) window.lucide.createIcons({nodes: [container]});
}

function viewItem(view) {
    return view.items[view.currentIndex] || null;
}

function announceChange(view, reason) {
    const item = viewItem(view);
    if (!item) return;
    emit(CHANGE_EVENT, {
        mode: view.mode,
        source: view.source,
        scope: view.scope,
        structureId: view.structureId,
        assetId: item.id,
        currentAssetId: item.id,
        asset: item.raw,
        reason,
    });
}

function requestEdit(view) {
    const item = viewItem(view);
    if (!item || item.mediaKind !== 'image') return;
    emit(REQUEST_EDIT_EVENT, {
        mode: view.mode,
        source: view.source,
        scope: view.scope,
        structureId: view.structureId,
        assetId: item.id,
        currentAssetId: item.id,
        asset: item.raw,
        assets: view.items.map(value => value.raw),
        total: view.total,
        hasMore: view.hasMore,
        loadMore: view.loadMore,
        trigger: view.trigger,
        parent: view.parent,
    });
}

function requestAddToReferenceCanvas(view) {
    const item = viewItem(view);
    if (!item || view.mode !== 'editor' || view.scope !== 'reference-canvas') return;
    emit(ADD_TO_REFERENCE_CANVAS_EVENT, {
        mode: view.mode,
        source: view.source,
        scope: view.scope,
        structureId: view.structureId,
        assetId: item.id,
        currentAssetId: item.id,
        asset: item.raw,
    });
}

function stableTriggerSelector(trigger) {
    if (!isElement(trigger)) return '';
    const structureCard = trigger.closest?.('[data-registry-structure-card]');
    const structureId = asText(structureCard?.dataset?.registryStructureCard);
    if (structureId) {
        return `[data-registry-structure-card="${CSS.escape(structureId)}"]`;
    }
    const registryCard = trigger.closest?.('[data-registry-card]');
    const assetId = asText(registryCard?.dataset?.registryCard);
    if (assetId) {
        return `[data-registry-card="${CSS.escape(assetId)}"]`;
    }
    return '';
}

function resolveTrigger(view) {
    if (view?.triggerSelector) {
        const current = document.querySelector(view.triggerSelector);
        if (current) return current;
    }
    return view?.trigger?.isConnected ? view.trigger : null;
}

function createView(detail, mode) {
    const items = normalizeAssets(detail?.assets);
    const requestedId = asText(detail?.currentAssetId || detail?.assetId);
    const requestedIndex = items.findIndex(item => item.id === requestedId);
    const trigger = isElement(detail?.trigger) ? detail.trigger : (isElement(document.activeElement) ? document.activeElement : null);
    const parent = isElement(detail?.parent) ? detail.parent : asText(detail?.parent);
    const reportedTotal = Number(detail?.total);
    const loadMore = typeof detail?.loadMore === 'function' ? detail.loadMore : null;
    return {
        mode,
        source: asText(detail?.source),
        scope: asText(detail?.scope),
        structureId: asText(detail?.structureId),
        items,
        currentIndex: requestedIndex >= 0 ? requestedIndex : 0,
        trigger,
        // Registry cards are rebuilt by render/reconciliation while a
        // Coverflow is open. Keep their stable identity and resolve the
        // current DOM node when focus is restored after close.
        triggerSelector: stableTriggerSelector(trigger),
        parent: parent || null,
        host: mode === 'editor' ? resolveHost(detail?.host) : null,
        elements: {},
        drag: null,
        wheelDistance: 0,
        lastWheelAt: 0,
        previousBodyOverflow: '',
        total: Number.isFinite(reportedTotal) ? Math.max(items.length, Math.floor(reportedTotal)) : items.length,
        hasMore: Boolean(detail?.hasMore && loadMore),
        loadMore,
        loadingMore: false,
        closed: false,
        suppressSlideClickUntil: 0,
    };
}

function offsetIndexes(view) {
    if (!view.items.length) return [];
    const offsets = [];
    const seen = new Set();
    const candidates = [0];
    for (let distance = 1; distance <= WINDOW_RADIUS; distance += 1) {
        candidates.push(-distance, distance);
    }
    candidates.forEach(offset => {
        const index = positiveModulo(view.currentIndex + offset, view.items.length);
        if (seen.has(index)) return;
        seen.add(index);
        offsets.push({index, offset});
    });
    return offsets.sort((left, right) => left.offset - right.offset);
}

/**
 * QIN-589：全屏 Coverflow 布局计算。以舞台/轨道实测几何与卡片基准宽度（CSS
 * min(58vw, 880px, 高度约束)）为输入，输出中心缩放、两侧缩放序列与按实际宽度
 * 累加（空间不足时等比压缩）的位移序列；只作用于全屏模式，嵌入式保持原样。
 */
function computeFullscreenLayout(view) {
    const stage = view?.elements?.stage;
    const track = view?.elements?.track;
    if (!isElement(stage) || !isElement(track) || !viewItem(view)) return null;
    const stageWidth = stage.clientWidth || 0;
    const trackWidth = track.clientWidth || 0;
    const trackHeight = track.clientHeight || 0;
    const baseSlide = track.querySelector('.gw-coverflow-slide.is-current') || track.querySelector('.gw-coverflow-slide');
    const baseWidth = baseSlide?.offsetWidth || 0;
    if (!stageWidth || !trackWidth || !baseWidth) return null;
    const centerWidth = Math.min(
        baseWidth * 2,
        stageWidth * FULLSCREEN_CENTER_WIDTH_SHARE,
        trackHeight * 16 / 9,
    );
    const sideWidths = [];
    for (let level = 1; level <= WINDOW_RADIUS; level += 1) {
        sideWidths.push(FULLSCREEN_ADJACENT_SCALE * baseWidth / 2 ** (level - 1));
    }
    const available = Math.max(0, trackWidth / 2 - centerWidth / 2);
    const needed = sideWidths.reduce((sum, width) => sum + width, 0);
    const fit = needed > 0 && needed > available ? Math.max(0, available / needed) : 1;
    const positions = [];
    let outerEdge = centerWidth / 2;
    for (let level = 1; level <= WINDOW_RADIUS; level += 1) {
        outerEdge += fit * sideWidths[level - 1];
        positions.push(outerEdge - sideWidths[level - 1] / 2);
    }
    return {centerScale: centerWidth / baseWidth, sideScales: sideWidths.map(width => width / baseWidth), positions};
}

function applySlidePosition(slide, offset, layout = null) {
    const distance = Math.min(Math.abs(offset), WINDOW_RADIUS);
    if (layout) {
        // QIN-589 全屏：中心按夹紧后的 2 倍目标宽度；两侧从改前紧邻缩放起每级减半；
        // x 取按实际宽度累加（空间不足 fit<1 时外层收入内层之下）后的位置。
        const level = distance;
        const scale = level === 0 ? layout.centerScale : layout.sideScales[level - 1];
        const x = level === 0 ? 0 : layout.positions[level - 1] * (offset < 0 ? -1 : 1);
        slide.style.setProperty('--gw-coverflow-scale', String(scale));
        slide.style.setProperty('--gw-coverflow-x', `${x}px`);
        slide.style.setProperty('--gw-coverflow-z', `${-level * 94}px`);
        slide.style.setProperty('--gw-coverflow-rotate', `${offset * -15}deg`);
        slide.style.setProperty('--gw-coverflow-opacity', String(Math.max(0.2, 1 - level * 0.26)));
        slide.style.setProperty('--gw-coverflow-gray', String(Math.min(0.88, level * 0.31)));
        slide.style.zIndex = String(40 - level);
        return;
    }
    const scale = Math.max(0.58, 1 - distance * 0.14);
    const opacity = Math.max(0.2, 1 - distance * 0.26);
    slide.style.setProperty('--gw-coverflow-x', `${offset * 20}vw`);
    slide.style.setProperty('--gw-coverflow-z', `${-distance * 94}px`);
    slide.style.setProperty('--gw-coverflow-rotate', `${offset * -15}deg`);
    slide.style.setProperty('--gw-coverflow-scale', String(scale));
    slide.style.setProperty('--gw-coverflow-opacity', String(opacity));
    slide.style.setProperty('--gw-coverflow-gray', String(Math.min(0.88, distance * 0.31)));
    slide.style.zIndex = String(40 - distance);
}

function renderSlides(view, {focusCurrent = false} = {}) {
    const {track, count, title} = view.elements;
    if (!track) return;
    const current = viewItem(view);
    const total = Math.max(view.items.length, Number(view.total) || 0);
    if (count) count.textContent = current ? `${view.currentIndex + 1} / ${total}` : `0 / ${total}`;
    if (title) title.textContent = current?.name || '暂无可展示图片';
    track.replaceChildren();

    if (!current) {
        const empty = document.createElement('p');
        empty.className = 'gw-coverflow-empty';
        empty.textContent = '当前素材范围没有可展示的图片';
        track.append(empty);
        return;
    }

    const positioned = [];
    offsetIndexes(view).forEach(({index, offset}) => {
        const item = view.items[index];
        const slide = createButton({
            className: `gw-coverflow-slide${index === view.currentIndex ? ' is-current' : ''}`,
            label: `切换到 ${item.name}`,
            title: item.name,
            index,
        });
        slide.dataset.gwCoverflowSlide = '';
        slide.dataset.gwCoverflowMediaKind = item.mediaKind;
        slide.setAttribute('aria-current', index === view.currentIndex ? 'true' : 'false');
        slide.tabIndex = index === view.currentIndex ? 0 : -1;
        if (view.mode === 'editor') {
            slide.dataset.gwCoverflowDragAsset = item.id;
            slide.addEventListener('click', event => {
                if (performance.now() < view.suppressSlideClickUntil) {
                    event.preventDefault();
                    event.stopPropagation();
                    return;
                }
                selectIndex(view, index, 'pointer', {focusCurrent: true});
            });
            slide.addEventListener('dragstart', event => {
                const transfer = event.dataTransfer;
                if (!transfer) return;
                transfer.effectAllowed = 'copy';
                transfer.setData(REFERENCE_ASSET_MIME, item.id);
                transfer.setData('text/plain', item.id);
                emit('gw:asset-coverflow-dragstart', {
                    mode: view.mode,
                    source: view.source,
                    scope: view.scope,
                    structureId: view.structureId,
                    assetId: item.id,
                });
            });

            // The image area owns swipe navigation. Keep native HTML5 drag
            // on a dedicated handle so it can still publish a stable asset ID
            // to the reference canvas without swallowing pointer gestures.
            const dragHandle = document.createElement('span');
            dragHandle.className = 'gw-coverflow-drag-handle';
            dragHandle.dataset.gwCoverflowDragHandle = '';
            dragHandle.draggable = true;
            dragHandle.tabIndex = -1;
            dragHandle.setAttribute('aria-hidden', 'true');
            dragHandle.title = '拖动图片到参考画布';
            dragHandle.append(createIcon('grip-vertical'));
            slide.append(dragHandle);
        }

        const media = document.createElement(item.mediaKind === 'video' ? 'video' : 'img');
        if (item.mediaKind === 'video') {
            media.src = item.mediaSrc;
            media.muted = true;
            media.playsInline = true;
            media.preload = index === view.currentIndex ? 'metadata' : 'none';
            if (item.backdropSrc) media.poster = item.backdropSrc;
            media.setAttribute('aria-label', item.name);
        } else {
            // The focused image is always the canonical source, including in
            // the compact editor/reference rail. Keep neighboring slides on
            // their bounded backdrop preview to avoid loading several large
            // originals while browsing.
            media.src = view.mode === 'fullscreen' || index === view.currentIndex
                ? item.mediaSrc
                : (item.backdropSrc || item.mediaSrc);
            media.alt = item.name;
            media.decoding = 'async';
            media.loading = index === view.currentIndex ? 'eager' : 'lazy';
        }
        media.dataset.gwCoverflowMedia = item.mediaKind;
        media.draggable = false;
        media.addEventListener('error', () => slide.classList.add('is-unavailable'), {once: true});
        slide.append(media);
        track.append(slide);
        positioned.push({slide, offset});
    });
    refreshIcons(track);
    // QIN-589：全屏先落 DOM 再按舞台实测几何定位——位移计算依赖卡片基准宽度
    // （CSS 基准宽 min(58vw, 880px, 高度约束)）与轨道实测尺寸。
    if (view.mode === 'fullscreen') {
        const layout = computeFullscreenLayout(view);
        positioned.forEach(({slide, offset}) => applySlidePosition(slide, offset, layout));
    } else {
        positioned.forEach(({slide, offset}) => applySlidePosition(slide, offset));
    }
    if (focusCurrent) requestAnimationFrame(() => track.querySelector('.gw-coverflow-slide.is-current')?.focus({preventScroll: true}));
}

function selectIndex(view, nextIndex, reason, {focusCurrent = false} = {}) {
    if (!view.items.length) return;
    const normalized = positiveModulo(nextIndex, view.items.length);
    if (normalized === view.currentIndex && reason !== 'initial') return;
    view.currentIndex = normalized;
    renderSlides(view, {focusCurrent});
    announceChange(view, reason);
}

async function loadMoreItems(view) {
    if (!view?.hasMore || !view.loadMore || view.loadingMore || view.closed) return false;
    view.loadingMore = true;
    status(view, '正在加载更多图片…');
    try {
        const payload = await view.loadMore();
        if (view.closed) return false;
        const additions = normalizeAssets(payload?.assets || payload?.items);
        const known = new Set(view.items.map(item => item.id));
        const appended = additions.filter(item => !known.has(item.id));
        if (appended.length) view.items.push(...appended);
        const reportedTotal = Number(payload?.total);
        view.total = Number.isFinite(reportedTotal)
            ? Math.max(view.items.length, Math.floor(reportedTotal))
            : Math.max(view.total, view.items.length);
        view.hasMore = Boolean(payload?.hasMore);
        if (appended.length) renderSlides(view);
        return appended.length > 0;
    } catch (_) {
        if (!view.closed) status(view, '继续加载 Coverflow 图片失败');
        view.hasMore = false;
        return false;
    } finally {
        view.loadingMore = false;
    }
}

async function move(view, delta, reason, {focusCurrent = false} = {}) {
    if (!delta || !view.items.length) return;
    const atEnd = delta > 0 && view.currentIndex >= view.items.length - 1;
    if (atEnd && view.hasMore) {
        const appended = await loadMoreItems(view);
        if (!appended && view.hasMore) {
            status(view, '继续切换以加载下一页图片');
            return;
        }
    }
    if (view.items.length < 2) return;
    selectIndex(view, view.currentIndex + delta, reason, {focusCurrent});
}

function stagePointerDown(view, event) {
    if (event.button !== 0 || event.target.closest?.('[data-gw-coverflow-action]')) return;
    if (event.target.closest?.('[data-gw-coverflow-drag-handle]')) {
        event.stopPropagation();
        return;
    }
    const slide = event.target.closest?.('[data-gw-coverflow-slide]');
    event.stopPropagation();
    view.drag = {
        pointerId: event.pointerId,
        startX: event.clientX,
        currentX: event.clientX,
        index: Number(slide?.dataset?.gwCoverflowIndex),
        hasMoved: false,
    };
    view.elements.stage?.setPointerCapture?.(event.pointerId);
    view.elements.stage?.classList.add('is-dragging');
}

function stagePointerMove(view, event) {
    if (!view.drag || view.drag.pointerId !== event.pointerId) return;
    event.stopPropagation();
    view.drag.currentX = event.clientX;
    const offset = Math.max(-180, Math.min(180, event.clientX - view.drag.startX));
    if (Math.abs(offset) > 5) view.drag.hasMoved = true;
    view.elements.stage?.style.setProperty('--gw-coverflow-drag', `${offset}px`);
}

function stagePointerEnd(view, event) {
    if (!view.drag || view.drag.pointerId !== event.pointerId) return;
    event.stopPropagation();
    const drag = view.drag;
    view.drag = null;
    view.elements.stage?.releasePointerCapture?.(event.pointerId);
    view.elements.stage?.classList.remove('is-dragging');
    view.elements.stage?.style.removeProperty('--gw-coverflow-drag');
    const delta = drag.currentX - drag.startX;
    if (Math.abs(delta) >= DRAG_THRESHOLD) {
        view.suppressSlideClickUntil = performance.now() + 350;
        void move(view, delta < 0 ? 1 : -1, 'drag');
    } else if (!drag.hasMoved && Number.isInteger(drag.index) && drag.index >= 0) {
        selectIndex(view, drag.index, 'pointer', {focusCurrent: true});
    }
}

function stageWheel(view, event) {
    const delta = Math.abs(event.deltaX) > Math.abs(event.deltaY) ? event.deltaX : event.deltaY;
    if (!delta) return;
    event.preventDefault();
    event.stopPropagation();
    const now = performance.now();
    if (now - view.lastWheelAt > 260) view.wheelDistance = 0;
    view.lastWheelAt = now;
    view.wheelDistance += delta;
    if (Math.abs(view.wheelDistance) < WHEEL_THRESHOLD) return;
    void move(view, view.wheelDistance > 0 ? 1 : -1, 'wheel');
    view.wheelDistance = 0;
}

/**
 * QIN-589 AC1：关闭 Coverflow 焦点归还触发控件时，脚本归还的焦点在 Esc / F 关闭
 * 路径仍会命中金色 :focus-visible。归还瞬间在触发控件上打静默标记，下一次真实
 * 键盘 / 指针交互时移除——焦点归还（AC2）与 Tab 可见焦点框都不受影响。
 */
function markTriggerFocusQuiet(trigger) {
    if (!isElement(trigger)) return;
    trigger.setAttribute(FOCUS_QUIET_ATTR, '');
    let done = false;
    const revert = () => {
        if (done) return;
        done = true;
        if (trigger.isConnected) trigger.removeAttribute(FOCUS_QUIET_ATTR);
        document.removeEventListener('keydown', revert, true);
        document.removeEventListener('pointerdown', revert, true);
    };
    document.addEventListener('keydown', revert, true);
    document.addEventListener('pointerdown', revert, true);
}

function closeFullscreen({restoreFocus = false, notify = true} = {}) {
    const view = fullscreenView;
    if (!view) return false;
    fullscreenView = null;
    view.closed = true;
    view.stageResizeObserver?.disconnect();
    view.stageResizeObserver = null;
    view.removeNestedBackdropDismiss?.();
    if (view.overlay?.isConnected) view.overlay.remove();
    document.body.style.overflow = view.previousBodyOverflow;
    if (notify) emit(CLOSED_EVENT, {mode: 'fullscreen', source: view.source});
    if (restoreFocus) {
        requestAnimationFrame(() => {
            const trigger = resolveTrigger(view);
            trigger?.focus?.({preventScroll: true});
            if (trigger && document.activeElement === trigger) markTriggerFocusQuiet(trigger);
        });
    }
    return true;
}

function requestFullscreenClose() {
    const overlay = fullscreenView?.overlay;
    if (!overlay) return;
    if (window.FloatingDismissal?.closeSurface?.(overlay)) return;
    closeFullscreen({restoreFocus: true});
}

function destroyEmbedded({notify = true} = {}) {
    const view = embeddedView;
    if (!view) return false;
    embeddedView = null;
    view.closed = true;
    view.container?.remove();
    view.host?.classList.remove('gw-coverflow-embedded-host');
    if (notify) emit(CLOSED_EVENT, {mode: 'editor', source: view.source});
    return true;
}

function handleKeydown(view, event) {
    if (event.defaultPrevented) return;
    if (!isBackquoteShortcut(event)) lastEditBackquoteTap = null;
    if (event.key === 'ArrowLeft') {
        event.preventDefault();
        event.stopPropagation();
        void move(view, -1, 'keyboard', {focusCurrent: true});
    } else if (event.key === 'ArrowRight') {
        event.preventDefault();
        event.stopPropagation();
        void move(view, 1, 'keyboard', {focusCurrent: true});
    } else if (view.mode === 'fullscreen'
        && !event.ctrlKey && !event.metaKey && !event.altKey
        && String(event.key || '').toLowerCase() === 'f') {
        event.preventDefault();
        event.stopPropagation();
        lastEditBackquoteTap = null;
        requestFullscreenClose();
    } else if (view.mode === 'fullscreen' && isBackquoteShortcut(event)) {
        // Coverflow reserves Backquote for edit entry only after a deliberate
        // double tap. Embedded rails let the owning editor handle the key.
        event.preventDefault();
        event.stopPropagation();
        if (event.repeat) return;
        const item = viewItem(view);
        const context = `${view.source}:${view.scope}:${item?.id || ''}`;
        const now = Date.now();
        if (lastEditBackquoteTap?.context === context
            && now - lastEditBackquoteTap.at <= EDIT_BACKQUOTE_WINDOW_MS) {
            lastEditBackquoteTap = null;
            requestEdit(view);
        } else {
            lastEditBackquoteTap = {at: now, context};
        }
    } else if (event.key === 'Escape' && view.mode === 'fullscreen' && !window.FloatingDismissal) {
        event.preventDefault();
        event.stopPropagation();
        lastEditBackquoteTap = null;
        requestFullscreenClose();
    }
}

function createSurface(view) {
    const container = document.createElement('section');
    container.className = `gw-coverflow gw-coverflow--${view.mode === 'editor' ? 'embedded' : 'fullscreen'}`;
    container.dataset.gwCoverflow = view.mode;
    if (view.mode === 'fullscreen') {
        container.setAttribute('role', 'dialog');
        container.setAttribute('aria-modal', 'true');
        container.setAttribute('aria-label', 'Coverflow 图片轮播');
        container.setAttribute('data-floating-content', '');
    } else {
        container.setAttribute('role', 'region');
        container.setAttribute('aria-label', '素材 Coverflow 图片轮播');
    }

    const header = document.createElement('header');
    header.className = 'gw-coverflow-header';
    const heading = document.createElement('div');
    heading.className = 'gw-coverflow-heading';
    heading.append(createIcon('images'));
    const headingText = document.createElement('strong');
    headingText.textContent = 'Coverflow';
    const count = document.createElement('span');
    count.className = 'gw-coverflow-count';
    heading.append(headingText, count);
    header.append(heading);
    const headerActions = document.createElement('div');
    headerActions.className = 'gw-coverflow-header-actions';
    if (view.mode === 'editor' && view.scope === 'reference-canvas') {
        headerActions.append(createButton({
            className: 'gw-coverflow-control gw-coverflow-add-to-reference-canvas',
            label: '将当前图片加入参考画布',
            icon: 'plus',
            action: 'add-to-reference-canvas',
        }));
    }
    if (view.mode === 'fullscreen') {
        const close = createButton({
            className: 'gw-coverflow-control gw-coverflow-close',
            label: '关闭 Coverflow',
            icon: 'x',
            action: 'close',
        });
        headerActions.append(close);
    }
    if (headerActions.childElementCount) header.append(headerActions);

    const stage = document.createElement('div');
    stage.className = 'gw-coverflow-stage';
    stage.dataset.gwCoverflowStage = '';
    stage.setAttribute('aria-roledescription', 'carousel');
    const previous = createButton({
        className: 'gw-coverflow-control gw-coverflow-previous',
        label: '上一张图片',
        icon: 'chevron-left',
        action: 'previous',
    });
    const track = document.createElement('div');
    track.className = 'gw-coverflow-track';
    const next = createButton({
        className: 'gw-coverflow-control gw-coverflow-next',
        label: '下一张图片',
        icon: 'chevron-right',
        action: 'next',
    });
    stage.append(previous, track, next);

    const footer = document.createElement('footer');
    footer.className = 'gw-coverflow-caption';
    const title = document.createElement('strong');
    title.setAttribute('aria-live', 'polite');
    footer.append(title);
    container.append(header, stage, footer);

    view.container = container;
    view.elements = {container, stage, track, count, title};
    stage.addEventListener('pointerdown', event => stagePointerDown(view, event));
    stage.addEventListener('pointermove', event => stagePointerMove(view, event));
    stage.addEventListener('pointerup', event => stagePointerEnd(view, event));
    stage.addEventListener('pointercancel', event => stagePointerEnd(view, event));
    stage.addEventListener('wheel', event => stageWheel(view, event), {passive: false});
    stage.addEventListener('dblclick', event => {
        // Pointer capture retargets the native dblclick to the stage. Resolve
        // the actual slide from the pointer coordinates before giving up.
        const slide = event.target.closest?.('[data-gw-coverflow-slide]')
            || (Number.isFinite(event.clientX) && Number.isFinite(event.clientY)
                ? document.elementFromPoint(event.clientX, event.clientY)?.closest?.('[data-gw-coverflow-slide]')
                : null);
        if (!slide) return;
        selectIndex(view, Number(slide.dataset.gwCoverflowIndex), 'double-click', {focusCurrent: true});
        requestEdit(view);
    });
    container.addEventListener('click', event => {
        if (view.mode === 'editor') event.stopPropagation();
        const action = event.target.closest?.('[data-gw-coverflow-action]')?.dataset.gwCoverflowAction;
        if (action === 'previous') void move(view, -1, 'button');
        else if (action === 'next') void move(view, 1, 'button');
        else if (action === 'add-to-reference-canvas') requestAddToReferenceCanvas(view);
        else if (action === 'close') requestFullscreenClose();
    });
    if (view.mode === 'editor') {
        // Keep carousel controls and swipe gestures out of the parent editor.
        ['pointerdown', 'pointermove', 'pointerup', 'pointercancel', 'dragstart', 'dragend'].forEach(type => {
            container.addEventListener(type, event => event.stopPropagation());
        });
        container.addEventListener('dblclick', event => event.stopPropagation());
    }
    container.addEventListener('keydown', event => {
        const parentEditorShortcut = event.key === 'Escape'
            || event.key === '·'
            || event.code === 'Backquote';
        if (view.mode === 'editor' && !parentEditorShortcut) event.stopPropagation();
        handleKeydown(view, event);
    });
    return container;
}

function openFullscreen(view) {
    closeFullscreen({notify: false});
    const overlay = document.createElement('div');
    overlay.className = 'gw-coverflow-overlay';
    overlay.dataset.gwCoverflowOverlay = '';
    // QIN-589：舞台改纯深色实底，不再挂模糊大图与 screen 混合遮罩。
    const surface = createSurface(view);
    overlay.append(surface);
    view.overlay = overlay;
    view.previousBodyOverflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    document.body.append(overlay);
    fullscreenView = view;
    if (view.parent) {
        // FloatingDismissal's document capture listener normally owns
        // backdrop dismissal. A full-screen child is a body sibling of its
        // group dialog, though, so stop the child backdrop click at window
        // capture and close it through the same registered surface API. This
        // preserves the parent modal instead of treating one click as outside
        // both sibling surfaces.
        const closeNestedBackdrop = event => {
            if (event.target !== overlay) return;
            event.preventDefault();
            event.stopImmediatePropagation();
            requestFullscreenClose();
        };
        window.addEventListener('click', closeNestedBackdrop, true);
        view.removeNestedBackdropDismiss = () => {
            window.removeEventListener('click', closeNestedBackdrop, true);
        };
    }
    overlay.addEventListener('click', event => {
        if (event.target === overlay && !window.FloatingDismissal) requestFullscreenClose();
    });
    window.FloatingDismissal?.mark?.(overlay, {
        surface: 'asset-coverflow',
        kind: 'modal',
        content: surface,
        close: () => closeFullscreen({restoreFocus: true}),
        trigger: view.trigger,
        parent: view.parent,
    });
    renderSlides(view);
    refreshIcons(overlay);
    requestAnimationFrame(() => surface.querySelector('[data-gw-coverflow-action="close"]')?.focus({preventScroll: true}));
    // QIN-589：舞台尺寸变化（窗口缩放、断点切换基准宽）时重算布局。
    const stage = view.elements.stage;
    view.lastStageSize = `${stage.clientWidth}x${stage.clientHeight}`;
    view.stageResizeObserver = new ResizeObserver(() => {
        const size = `${stage.clientWidth}x${stage.clientHeight}`;
        if (view.closed || fullscreenView !== view || size === view.lastStageSize) return;
        view.lastStageSize = size;
        renderSlides(view);
    });
    view.stageResizeObserver.observe(stage);
}

function mountEmbedded(view) {
    destroyEmbedded({notify: false});
    if (!view.host?.isConnected) {
        status(view, '无法找到素材编辑器的 Coverflow 挂载位置');
        return;
    }
    const surface = createSurface(view);
    surface.dataset.gwCoverflowEmbedded = '';
    view.host.classList.add('gw-coverflow-embedded-host');
    view.host.append(surface);
    embeddedView = view;
    renderSlides(view);
    refreshIcons(surface);
}

function open(detail) {
    const mode = detail?.mode === 'editor' ? 'editor' : 'fullscreen';
    const view = createView(detail, mode);
    if (!view.items.length) status(view, '当前素材范围没有可展示的图片');
    if (mode === 'editor') {
        closeFullscreen({notify: false});
        mountEmbedded(view);
    } else {
        openFullscreen(view);
    }
}

window.addEventListener(OPEN_EVENT, event => open(event.detail || {}));
window.addEventListener(CLOSE_EVENT, event => {
    const mode = event.detail?.mode;
    if (!mode || mode === 'fullscreen') requestFullscreenClose();
    if (!mode || mode === 'editor') destroyEmbedded();
});
