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

// canvas-list.js — Project Workspace.
// Two-pane: LEFT project list, RIGHT finite/zoomable board of canvas cards.
// Self-contained; relies only on global HTTP facade / StudioI18n / lucide.

function canvasListApi(){
    const api = window.GodsWorkbenchCanvasListApi;
    if(!api) throw new Error('Canvas-list HTTP facade unavailable');
    return api;
}

/* ===== Small helpers (copied from the previous gate file) ===== */
function refreshIcons(){ if(window.lucide) lucide.createIcons(); }
function tr(key){ return window.StudioI18n ? StudioI18n.t(key) : key; }
function langIsEn(){ return window.StudioI18n?.lang?.() === 'en'; }
function escapeHtml(str){ return String(str == null ? '' : str).replace(/[&<>"']/g, s => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[s])); }
function escapeAttr(str){ return escapeHtml(str); }
function L(zh, en){ return langIsEn() ? en : zh; }
function compactLabel(fullZh, compactZh, en){ return window.innerWidth <= 760 ? L(compactZh, en) : L(fullZh, en); }
const CANVAS_LIST_PROJECT_KEY = 'canvasListCurrentProjectId';
document.documentElement.dataset.canvasListReady = 'loading';

function notifyCanvasLibrary(){
    window.parent?.postMessage({type:'canvas-library'}, location.origin);
    window.parent?.postMessage({type:'canvas-context', title:'无限画布'}, location.origin);
}

function requestedCanvasContext(){
    try {
        const params = new URLSearchParams(window.location.search);
        return {
            projectId: String(params.get('project_id') || params.get('project') || '').trim(),
            entityId: String(params.get('entity_id') || '').trim()
        };
    } catch(e){
        return {projectId:'', entityId:''};
    }
}

const initialCanvasContext = requestedCanvasContext();

function rememberedProjectId(){
    try {
        return initialCanvasContext.projectId || localStorage.getItem(CANVAS_LIST_PROJECT_KEY) || 'default';
    } catch(e){
        return 'default';
    }
}

function rememberProjectId(pid){
    if(!pid) return;
    try { localStorage.setItem(CANVAS_LIST_PROJECT_KEY, pid); } catch(e){}
}

function projectSortOrder(project){
    return Number(project?.sort_order ?? project?.order ?? 0) || 0;
}

function normalizeProject(project){
    const value = project && typeof project === 'object' ? project : {};
    // 契约对齐：项目中心 API 的稳定实体 ID 字段为 project_id（见 docs/contracts/PROJECTS-HUB-INTERFACE-CATALOG.yaml，
    // 黄金夹具 docs/fixtures/projects-hub-list-active.json 不含 id）；此处归一化为内部 id，
    // 否则项目行 data-project-id 变成字符串 "undefined"，项目切换与画布归属全部错乱。
    const id = value.id || value.project_id;
    return {...value, id, order:projectSortOrder(value)};
}

function normalizeCanvas(canvas){
    const value = canvas && typeof canvas === 'object' ? canvas : {};
    // 契约对齐：docs/contracts/CANVAS-INTERFACE-CATALOG.yaml 的 list_canvases.response_200
    // 稳定实体 ID 为 canvas_id、所属项目为 project_id、模式字段为 mode；
    // docs/fixtures/canvas-workflow-minimal.json 同样只含 canvas_id。
    // 不在摄取边界归一化时，卡片 data-canvas-id 会退化为字符串 "undefined"。
    const id = value.id || value.canvas_id;
    const project = value.project || value.project_id;
    const kind = value.kind || value.mode;
    return {...value, id, project, kind};
}

// Keep canvas tones aligned with the project-board type palette.  Scope and the
// current project are the source of truth; a stale canvas color must not change
// the visual identity of a project canvas.
function projectTone(project){
    const type = String(project?.project_type || '').trim().toLowerCase();
    if(type === 'series') return {key:'green', surface:'rgba(32,163,90,.16)', border:'rgba(99,226,176,.52)', accent:'#63e2b0'};
    if(type === 'other') return {key:'orange', surface:'rgba(240,90,50,.16)', border:'rgba(255,127,120,.52)', accent:'#ff7f78'};
    return {key:'violet', surface:'rgba(118,84,184,.18)', border:'rgba(154,167,255,.5)', accent:'#9aa7ff'};
}
function standaloneTone(){
    return {key:'amber', surface:'rgba(242,198,109,.18)', border:'rgba(242,198,109,.58)', accent:'#f2c66d'};
}
function canvasTone(canvas){
    if(!isProjectCanvas(canvas)) return standaloneTone();
    const project = projects.find(item => item.id === String(canvas?.project || '').trim());
    return projectTone(project);
}

function syncCanvasListContext(){
    try {
        const url = new URL(window.location.href);
        if(currentProjectId) {
            url.searchParams.set('project_id', currentProjectId);
            url.searchParams.set('project', currentProjectId);
        } else {
            url.searchParams.delete('project_id');
            url.searchParams.delete('project');
        }
        if(currentEntityId) url.searchParams.set('entity_id', currentEntityId);
        else url.searchParams.delete('entity_id');
        history.replaceState({}, '', `${url.pathname}${url.search}${url.hash}`);
    } catch(e){}
}

function formatCanvasTime(value){
    if(!value) return '--';
    const raw = Number(value);
    const time = raw < 10000000000 ? raw * 1000 : raw;
    const date = new Date(time);
    if(Number.isNaN(date.getTime())) return '--';
    return date.toLocaleString(langIsEn() ? 'en-US' : 'zh-CN', { month:'2-digit', day:'2-digit', hour:'2-digit', minute:'2-digit' });
}

function renderCanvasIcon(icon, size = 16){
    if(!icon || icon === '🧩') return `<i data-lucide="layers" style="width:${size}px;height:${size}px"></i>`;
    if(/[^\x00-\x7F]/.test(icon)) return escapeHtml(icon);
    return `<i data-lucide="${escapeHtml(icon)}" style="width:${size}px;height:${size}px"></i>`;
}

window.addEventListener('message', function(e) {
    if (e.origin !== location.origin || !e.data) return;
    if (e.data.type === 'select-project') {
        if (e.data.projectId && typeof selectProject === 'function') {
            selectProject(e.data.projectId, true);
        }
    } else if (e.data.type === 'canvas-action') {
        if (e.data.action === 'new-project' || e.data.action === 'new-canvas') {
            const btn = document.getElementById('newCanvasBtn');
            if (btn) btn.click();
        } else if (e.data.action === 'trash') {
            const trashBtn = document.getElementById('trashEntry');
            if (trashBtn) trashBtn.click();
        }
    } else if (e.data.type === 'gw-touchbar-action') {
        if (e.source !== window.parent || e.data.version !== 1) return;
        if (e.data.action_id === 'canvas.create') {
            document.getElementById('newCanvasBtn')?.click();
            return;
        }
        if (e.data.action_id === 'canvas.open') {
            const canvas = canvases.find(item => item.id === selectedCanvasId);
            if (canvas) openCanvas(canvas);
        }
    }
});

/* ===== DOM refs ===== */
const board = document.getElementById('board');
const boardWorld = document.getElementById('boardWorld');
const boardEmptyHint = document.getElementById('boardEmptyHint');
const projectListEl = document.getElementById('projectList');
const trashEntryBtn = document.getElementById('trashEntry');
const trashBadge = document.getElementById('trashBadge');
const trashBackdrop = document.getElementById('trashBackdrop');
const trashPanel = document.getElementById('trashPanel');
const trashListEl = document.getElementById('trashList');
const trashCloseBtn = document.getElementById('trashClose');
let trashReturnFocusEl = null;
const archiveEntryBtn = document.getElementById('archiveEntry');
const archiveBadge = document.getElementById('archiveBadge');
const archiveBackdrop = document.getElementById('archiveBackdrop');
const archivePanel = document.getElementById('archivePanel');
const archiveListEl = document.getElementById('archiveList');
const archiveCloseBtn = document.getElementById('archiveClose');
const newProjectBtn = document.getElementById('newProjectBtn');
const newProjectRow = document.getElementById('newProjectRow');
const newProjectInput = document.getElementById('newProjectInput');
const newProjectConfirm = document.getElementById('newProjectConfirm');
const newProjectCancel = document.getElementById('newProjectCancel');
const newCanvasBtn = document.getElementById('newCanvasBtn');
const newCanvasLabel = document.getElementById('newCanvasLabel');
const boardRefreshBtn = document.getElementById('boardRefresh');
const boardResetViewBtn = document.getElementById('boardResetView');
const pasteCanvasBtn = document.getElementById('pasteCanvasBtn');
const emptyCreateCanvasBtn = document.getElementById('emptyCreateCanvasBtn');
const canvasFilterGroupEl = document.getElementById('canvasFilterGroup');
const statusEl = document.getElementById('boardStatus');
const canvasOverviewSearchEl = document.getElementById('canvasOverviewSearch');
const canvasOverviewSortEl = document.getElementById('canvasOverviewSort');
const canvasOverviewDensityEl = document.querySelector('.canvas-overview-density');
const canvasOverviewRefreshBtn = document.getElementById('canvasOverviewRefresh');
const canvasOverviewSourceEl = document.getElementById('canvasOverviewSource');
const overviewTotalCountEl = document.getElementById('overviewTotalCount');
const overviewNormalCountEl = document.getElementById('overviewNormalCount');
const overviewSmartCountEl = document.getElementById('overviewSmartCount');
const overviewProjectCountEl = document.getElementById('overviewProjectCount');
const overviewReferenceCountEl = document.getElementById('overviewReferenceCount');
const overviewNormalDetailEl = document.getElementById('overviewNormalDetail');
const overviewApiStateEl = document.getElementById('overviewApiState');
const filterCountClassicDetailEl = document.getElementById('filterCountClassicDetail');
const CANVAS_LIST_OVERVIEW = document.documentElement.dataset.canvasListLayout === 'overview';

/* ===== State ===== */
let projects = [];
let canvases = [];          // all canvases across projects
let deletedCanvases = [];
let archivedCanvases = [];
let currentProjectId = rememberedProjectId();
let currentEntityId = initialCanvasContext.entityId;
let currentCanvasFilter = (() => {
    try {
        const value = new URLSearchParams(window.location.search).get('filter');
        return ['all', 'classic', 'project', 'reference'].includes(value) ? value : 'all';
    } catch(e) { return 'all'; }
})(); // 'all' | 'classic' | 'project' | 'reference'
let selectedCanvasId = '';
let overviewSearchTerm = '';
let overviewSort = 'updated';
let overviewDensity = 'grid';
if(CANVAS_LIST_OVERVIEW){
    try {
        overviewSort = ['updated', 'created', 'name'].includes(localStorage.getItem('canvas_overview_sort'))
            ? localStorage.getItem('canvas_overview_sort')
            : 'updated';
        overviewDensity = localStorage.getItem('canvas_overview_density') === 'compact' ? 'compact' : 'grid';
    } catch(e) {}
}
let touchbarReportRevision = 0;
let touchbarReportTimer = 0;
function touchbarStableId(value){
    const id = String(value == null ? '' : value).trim();
    return /^[A-Za-z0-9][A-Za-z0-9_.:@-]{0,191}$/.test(id) ? id : '';
}
function reportCanvasTouchbarContext(){
    if(window.parent === window) return;
    const canvas = canvases.find(item => item.id === selectedCanvasId) || null;
    const projectId = touchbarStableId(currentProjectId);
    const entityId = touchbarStableId(currentEntityId);
    const canvasId = touchbarStableId(canvas?.id);
    try {
        window.parent.postMessage({
            type:'gw-touchbar-capabilities',
            version:1,
            revision:++touchbarReportRevision,
            page:'canvas',
            context:{...(projectId ? {project_id:projectId} : {}), ...(entityId ? {entity_id:entityId} : {}), ...(canvasId ? {canvas_id:canvasId} : {})},
            selection:canvasId ? {type:'canvas', id:canvasId, label:String(canvasDisplayTitle(canvas) || '').slice(0,100), status:String(canvas.kind || '').slice(0,36)} : null,
            actions:[{action_id:'canvas.create'}, ...(canvasId ? [{action_id:'canvas.open'}] : [])],
        }, location.origin);
    } catch(_) {}
}
function scheduleCanvasTouchbarReport(){
    clearTimeout(touchbarReportTimer);
    touchbarReportTimer = setTimeout(reportCanvasTouchbarContext, 0);
}
let pendingDeleteProjectId = null;
let statusTimer = null;
let clipboardCanvasId = null;   // 剪切的画布（切到别的项目后粘贴）
let canvasListLoadSequence = 0;
let boardResizeTimer = 0;
let archiveReturnFocusEl = null;

// Board card zoom (aligned with project-board overview semantics).
const CANVAS_CARD_GAP = 10;
const MIN_CARD_SCALE = 0.6;
const MAX_CARD_SCALE = 1.6;
const CARD_SCALE_STEP = 0.1;
let cardScale = Number(localStorage.getItem('canvas_card_scale') || 1);
if (!Number.isFinite(cardScale) || cardScale < MIN_CARD_SCALE || cardScale > MAX_CARD_SCALE) cardScale = 1;

/* ===== Status toast ===== */
// 统一「无后端时显式降级」（用户…裁决第 3 项）：与 http-transport.js 同源判定。
function canvasListDegradationLabel(error, fallback){
    if(error && error.code === 'NOT_INTEGRATED') return '该功能尚未接入后端（未纳入当前切片）';
    if(error && error.code === 'SERVICE_UNAVAILABLE') return '后端服务暂时不可用，请稍后重试';
    return fallback;
}
function setStatus(text){
    if(!statusEl) return;
    if(!text){ statusEl.classList.remove('show'); return; }
    statusEl.textContent = text;
    statusEl.classList.add('show');
    clearTimeout(statusTimer);
    statusTimer = setTimeout(() => statusEl.classList.remove('show'), 2200);
}

/* ===== Card scale & viewport math ===== */
function canvasViewportIsCompact(){
    try {
        return typeof window.matchMedia === 'function'
            ? window.matchMedia('(max-width: 760px)').matches
            : (window.innerWidth || 1200) <= 760;
    } catch(e){
        return (window.innerWidth || 1200) <= 760;
    }
}
function canvasCardMetrics(){
    const compact = canvasViewportIsCompact();
    const baseWidth = compact ? 230 : 248;
    const baseHeight = compact ? 146 : 150;
    const cardWidth = Number((baseWidth * cardScale).toFixed(2));
    const cardHeight = Number((baseHeight * cardScale).toFixed(2));
    return {
        cardWidth,
        cardHeight,
        xStride: cardWidth + CANVAS_CARD_GAP,
        yStride: cardHeight + CANVAS_CARD_GAP,
    };
}
function applyCardScale(){
    board.style.setProperty('--canvas-card-scale', String(cardScale));
    boardWorld.style.transform = 'none';
}
function setCanvasCardScale(next){
    const clamped = Math.min(MAX_CARD_SCALE, Math.max(MIN_CARD_SCALE, Math.round(next * 100) / 100));
    if(clamped === cardScale) return;
    cardScale = clamped;
    try { localStorage.setItem('canvas_card_scale', String(clamped)); } catch(e){}
    applyCardScale();
    renderBoard();
}
function applyViewport(){
    applyCardScale();
}
function boardCreateWorldPoint(projectId = currentProjectId){
    const items = canvasesInProject(String(projectId || currentProjectId || 'default'));
    const columns = canvasGridColumns();
    const index = items.length;
    const {xStride, yStride} = canvasCardMetrics();
    return {x:24 + (index % columns) * xStride, y:24 + Math.floor(index / columns) * yStride};
}
function resetView(){
    setCanvasCardScale(1);
    board.scrollTo({left:0, top:0, behavior:'auto'});
}

/* ===== Board zoom (Shift+wheel aligns with project-board) ===== */
function onBoardWheel(e){
    if(!e.shiftKey) return;
    if(e.defaultPrevented) return;
    if(document.querySelector('dialog[open]') || canvasListNavigationOverlayOpen()) return;
    e.preventDefault();
    // Legacy pointer-anchor terms are intentionally not used by the finite
    // catalog, but retain the scroll fields for compatibility with old hooks:
    // const px = e.clientX - board.getBoundingClientRect().left + board.scrollLeft;
    // const py = e.clientY - board.getBoundingClientRect().top + board.scrollTop;
    const delta = e.deltaY < 0 ? CARD_SCALE_STEP : -CARD_SCALE_STEP;
    setCanvasCardScale(cardScale + delta);
}

/* ===== Data loading ===== */
function currentProject(){ return projects.find(p => p.id === currentProjectId) || projects[0] || null; }
function canvasesInProject(pid){ return canvases.filter(c => (c.project || 'default') === pid); }
function canvasGridColumns(){
    const width = board?.clientWidth || window.innerWidth || 1200;
    if(canvasViewportIsCompact()) return 1;
    const {xStride} = canvasCardMetrics();
    return Math.max(1, Math.floor((width - 48 + CANVAS_CARD_GAP) / xStride));
}

async function loadAll(){
    const requestSequence = ++canvasListLoadSequence;
    document.documentElement.dataset.canvasListReady = 'loading';
    updateOverviewFooter('loading');
    try {
        // 契约要求先确定 project_id（list_canvases.request_query.project_id 为必填），
        // 因此项目列表必须先行读取；此前两个请求并发发出，画布请求恒定缺参 400。
        const pRes = await canvasListApi().listProjects();
        if(requestSequence !== canvasListLoadSequence) return false;
        const pData = pRes.ok ? await pRes.json() : { projects: [] };
        if(requestSequence !== canvasListLoadSequence) return false;
        projects = (pData.projects || []).map(normalizeProject).sort((a, b) => projectSortOrder(a) - projectSortOrder(b));
        if(!projects.length) projects = [{ id: 'default', name: L('默认项目','Default'), order: 0, canvas_count: 0 }];
        // pick active project (prefer current or remembered)
        const targetPid = currentProjectId || rememberedProjectId();
        if(projects.find(p => p.id === targetPid)){
            currentProjectId = targetPid;
        } else {
            const def = projects.find(p => p.id === 'default') || projects.slice().sort((a, b) => (a.order || 0) - (b.order || 0))[0];
            currentProjectId = def ? def.id : (projects[0]?.id || 'default');
            currentEntityId = '';
        }
        rememberProjectId(currentProjectId);
        const cRes = await canvasListApi().listCanvases(currentProjectId);
        if(requestSequence !== canvasListLoadSequence) return false;
        const cData = cRes.ok ? await cRes.json() : { canvases: [] };
        if(requestSequence !== canvasListLoadSequence) return false;
        canvases = (cData.canvases || []).map(normalizeCanvas);
        const loaded = pRes.ok && cRes.ok;
        syncCanvasListContext();
        renderProjects();
        renderBoard({
            preserveCreateCard: Boolean(
                createCardEl?.isConnected && createCardViewProjectId === currentProjectId
            )
        });
        refreshTrashCount();
        document.documentElement.dataset.canvasListReady = loaded ? 'ready' : 'error';
        updateOverviewFooter(loaded ? 'ready' : 'error');
        return loaded;
    } catch(e){
        if(requestSequence !== canvasListLoadSequence) return false;
        console.error(e);
        setStatus(canvasListDegradationLabel(e, L('加载失败','Load failed')));
        document.documentElement.dataset.canvasListReady = 'error';
        updateOverviewFooter('error');
        return false;
    }
}

function projectCanvasCount(pid){
    const p = projects.find(x => x.id === pid);
    // prefer live count from canvases array; fall back to server count
    const live = canvasesInProject(pid).length;
    return canvases.length ? live : (p?.canvas_count || 0);
}

/* ===== Project sidebar rendering ===== */
function renderProjects(){
    projectListEl.innerHTML = '';
    projects.forEach(p => {
        if(pendingDeleteProjectId === p.id){
            const box = document.createElement('div');
            box.className = 'ws-project-confirm';
            box.innerHTML = `
                <div class="ws-project-confirm-title">${L('删除项目','Delete project')}「${escapeHtml(p.name)}」？${L('其画布将移回默认项目。','Canvases move back to Default.')}</div>
                <div class="ws-project-confirm-actions">
                    <button class="ws-confirm-btn" type="button">${L('删除','Delete')}</button>
                    <button class="ws-cancel-btn" type="button">${L('取消','Cancel')}</button>
                </div>`;
            box.querySelector('.ws-confirm-btn').onclick = () => deleteProject(p.id);
            box.querySelector('.ws-cancel-btn').onclick = () => { pendingDeleteProjectId = null; renderProjects(); };
            projectListEl.appendChild(box);
            return;
        }
        const row = document.createElement('div');
        row.className = 'ws-project-row' + (p.id === currentProjectId ? ' active' : '');
        row.dataset.projectId = p.id;
        const count = projectCanvasCount(p.id);
        const isDefault = p.id === 'default';
        row.innerHTML = `
            <span class="ws-project-icon"><i data-lucide="${isDefault ? 'folder' : 'folder-open'}" class="w-4 h-4"></i></span>
            <span class="ws-project-name">${escapeHtml(p.name)}</span>
            <span class="ws-project-count">${count}</span>
            <span class="ws-project-actions">
                <button class="ws-proj-act rename" type="button" title="${L('重命名','Rename')}" aria-label="${L('重命名','Rename')}"><i data-lucide="pencil" class="w-3.5 h-3.5"></i></button>
                ${isDefault ? '' : `<button class="ws-proj-act del" type="button" title="${L('删除','Delete')}" aria-label="${L('删除','Delete')}"><i data-lucide="trash-2" class="w-3.5 h-3.5"></i></button>`}
            </span>`;
        row.onclick = e => {
            if(e.target.closest('.ws-proj-act')) return;
            selectProject(p.id);
        };
        const renameBtn = row.querySelector('.ws-proj-act.rename');
        if(renameBtn) renameBtn.onclick = e => { e.stopPropagation(); startProjectRename(p.id, row); };
        const delBtn = row.querySelector('.ws-proj-act.del');
        if(delBtn) delBtn.onclick = e => { e.stopPropagation(); pendingDeleteProjectId = p.id; renderProjects(); };
        projectListEl.appendChild(row);
    });
    refreshIcons();
}

function selectProject(pid, force = false){
    if(!force && pid === currentProjectId && !trashPanel?.classList.contains('active')) return;
    const changed = pid !== currentProjectId;
    currentProjectId = pid;
    if(changed) currentEntityId = '';
    rememberProjectId(pid);
    syncCanvasListContext();
    notifyCanvasLibrary();
    closeTrashView();
    renderProjects();
    if(changed){
        // Keep the previous board out of the interaction surface until the
        // selected project's refresh has completed.
        document.documentElement.dataset.canvasListReady = 'loading';
        void loadAll();
        return;
    }
    renderBoard();
    board.scrollTo({left:0, top:0, behavior:'auto'});
}

function startProjectRename(pid, row){
    const p = projects.find(x => x.id === pid);
    if(!p) return;
    const nameEl = row.querySelector('.ws-project-name');
    if(!nameEl || nameEl.querySelector('input')) return;
    const input = document.createElement('input');
    input.type = 'text'; input.maxLength = 60; input.value = p.name;
    input.className = 'ws-project-name-input';
    nameEl.replaceWith(input);
    input.focus(); input.select();
    input.onclick = e => e.stopPropagation();
    let done = false;
    const finish = commit => {
        if(done) return; done = true;
        const v = input.value.trim();
        if(commit && v && v !== p.name) renameProject(pid, v);
        else renderProjects();
    };
    input.onblur = () => finish(true);
    input.onkeydown = e => {
        e.stopPropagation();
        if(e.key === 'Enter'){ e.preventDefault(); finish(true); }
        if(e.key === 'Escape'){ e.preventDefault(); finish(false); }
    };
}

/* ===== Project CRUD ===== */
function openNewProject(){
    newProjectRow.classList.add('active');
    newProjectInput.value = '';
    newProjectInput.focus();
}
function closeNewProject(){
    newProjectRow.classList.remove('active');
    newProjectInput.value = '';
}
async function createProject(){
    const name = newProjectInput.value.trim() || L('新项目','New project');
    closeNewProject();
    try {
        const sortOrder = Math.max(0, ...projects.map(projectSortOrder)) + 1;
        const res = await canvasListApi().createProject({ name, sort_order: sortOrder });
        if(!res.ok) throw new Error('create project failed');
        const data = await res.json();
        const proj = data.project ? normalizeProject(data.project) : null;
        if(proj){
            projects.push(proj);
            projects.sort((a, b) => projectSortOrder(a) - projectSortOrder(b));
            selectProject(proj.id);
            renderProjects();
        }
    } catch(e){
        console.error(e); setStatus(L('创建项目失败','Create project failed'));
    }
}
async function renameProject(pid, name){
    const p = projects.find(x => x.id === pid);
    const previousName = p?.name || '';
    if(p) p.name = name;
    renderProjects();
    if(pid === currentProjectId) renderBoard();
    try {
        const payload = {name};
        if(Number(p?.version || 0) > 0) payload.expected_version = Number(p.version);
        const res = await canvasListApi().updateProject(pid, payload);
        if(!res.ok) throw new Error('rename project failed');
        const data = await res.json();
        if(data.project && p) Object.assign(p, normalizeProject(data.project));
    } catch(e){
        if(p) p.name = previousName;
        console.error(e); setStatus(L('重命名失败','Rename failed')); loadAll();
    }
}
async function deleteProject(pid){
    pendingDeleteProjectId = null;
    const movedCanvases = [];
    try {
        const project = projects.find(item => item.id === pid);
        const affectedCanvases = canvases.filter(canvas => (canvas.project || 'default') === pid);
        for(const canvas of affectedCanvases){
            const original = {
                project: canvas.project || pid,
                entity_id: canvas.entity_id || ''
            };
            const response = await canvasListApi().updateCanvasMeta(
                canvas.id,
                canvasMetaWritePayload(canvas, {project:'default'})
            );
            if(!response.ok) throw new Error('move canvas failed');
            const result = await response.json();
            // Keep the server-returned CAS pair for a compensating write if
            // deleting the project itself fails after this move succeeds.
            movedCanvases.push({
                original,
                result,
                canvas: result.canvas ? {...canvas, ...result.canvas} : canvas
            });
        }
        const payload = Number(project?.version || 0) > 0 ? {expected_version:Number(project.version)} : {};
        const res = await canvasListApi().deleteProject(pid, payload);
        if(!res.ok) throw new Error('delete project failed');
        movedCanvases.forEach(({result}) => {
            const updated = result.canvas;
            const index = canvases.findIndex(canvas => canvas.id === updated?.id);
            if(index >= 0 && updated) canvases[index] = {...canvases[index], ...updated};
        });
        projects = projects.filter(p => p.id !== pid);
        if(currentProjectId === pid){
            currentProjectId = 'default';
            currentEntityId = '';
        }
        rememberProjectId(currentProjectId);
        syncCanvasListContext();
        renderProjects();
        renderBoard();
    } catch(e){
        for(const {canvas, original} of movedCanvases.reverse()){
            const body = {project:original.project};
            if(original.entity_id) body.entity_id = original.entity_id;
            try {
                await canvasListApi().updateCanvasMeta(
                    canvas.id,
                    canvasMetaWritePayload(canvas, body)
                );
            } catch(rollbackError){ console.error(rollbackError); }
        }
        console.error(e); setStatus(L('删除项目失败','Delete project failed')); loadAll();
    }
}

/* ===== Board rendering ===== */
function updateBoardHeader(){
    if(newCanvasLabel){
        const label = L('新建画布','New canvas');
        newCanvasLabel.textContent = label;
        newCanvasBtn.title = label;
        newCanvasBtn.setAttribute('aria-label', label);
    }
}

function isProjectCanvas(canvas){
    return String(canvas?.scope || '') === 'project';
}

function projectCanvasTitle(canvas){
    const projectId = String(canvas?.project || currentProjectId || 'default').trim();
    const project = projects.find(item => item.id === projectId);
    return String(project?.name || canvas?.title || L('未命名项目','Untitled project')).trim();
}

function canvasDisplayTitle(canvas){
    const stored = String(canvas?.title || '').trim();
    if(stored) return stored;
    if(isProjectCanvas(canvas)){
        const projectId = String(canvas?.project || currentProjectId || 'default').trim();
        return projectCanvasTitle({project:projectId}) || stored || L('未命名项目','Untitled project');
    }
    return L('未命名画布','Untitled canvas');
}

function selectCanvasCard(canvasId){
    selectedCanvasId = canvases.some(item => item.id === canvasId) ? canvasId : '';
    boardWorld.querySelectorAll('.ws-card').forEach(card => {
        const selected = card.dataset.canvasId === selectedCanvasId;
        card.classList.toggle('selected', selected);
        card.setAttribute('aria-pressed', selected ? 'true' : 'false');
    });
    scheduleCanvasTouchbarReport();
}

function finiteBoardOrder(items){
    return items.slice().sort((a, b) => {
        const created = Number(a.created_at || 0) - Number(b.created_at || 0);
        return created || String(a.id || '').localeCompare(String(b.id || ''));
    });
}

function finiteBoardLayout(items){
    // The catalog is a finite, left-aligned list. Legacy board coordinates
    // remain server-side compatibility data only; never use or persist them
    // here, otherwise an old infinite-board position would leak back into the
    // finite catalog and break narrow-screen reflow.
    const X0 = 24, Y0 = 24;
    const {cardWidth, cardHeight, xStride: XSTRIDE, yStride: YSTRIDE} = canvasCardMetrics();
    const COLS = canvasGridColumns();
    const ordered = finiteBoardOrder(items);
    const positions = new Map();
    let maxX = board.clientWidth;
    let maxY = board.clientHeight;
    ordered.forEach((c, i) => {
        const nextX = X0 + (i % COLS) * XSTRIDE;
        const nextY = Y0 + Math.floor(i / COLS) * YSTRIDE;
        const position = {x:nextX, y:nextY};
        positions.set(c.id, position);
        maxX = Math.max(maxX, position.x + cardWidth + X0);
        maxY = Math.max(maxY, position.y + cardHeight + Y0);
    });
    boardWorld.style.width = `${Math.max(board.clientWidth, maxX)}px`;
    boardWorld.style.height = `${Math.max(board.clientHeight, maxY)}px`;
    return positions;
}

function isReferenceCanvas(c){
    return String(c?.kind || '').toLowerCase() === 'reference';
}

function isProjectScopedCanvas(c){
    return String(c?.scope || '').toLowerCase() === 'project' || (Boolean(c?.project) && c.project !== 'default');
}

function isClassicCanvas(c){
    return !isReferenceCanvas(c) && !isProjectScopedCanvas(c);
}

function getCandidateCanvases(){
    // The top-level canvas catalog is a workspace view.  It only narrows to a
    // project when the route explicitly carries a project context (for
    // example, an embedded project-board view).
    if(CANVAS_LIST_OVERVIEW && !initialCanvasContext.projectId){
        return canvases;
    }
    if(!currentProjectId || currentProjectId === 'default'){
        return canvases;
    }
    return canvases.filter(c => (c.project || 'default') === currentProjectId);
}

function getFilteredCanvases(){
    const candidates = getCandidateCanvases();
    if(currentCanvasFilter === 'reference'){
        const refs = candidates.filter(isReferenceCanvas);
        if(refs.length > 0) return refs;
        return canvases.filter(isReferenceCanvas);
    }
    if(currentCanvasFilter === 'project'){
        const projs = candidates.filter(isProjectScopedCanvas);
        if(projs.length > 0) return projs;
        return canvases.filter(isProjectScopedCanvas);
    }
    if(currentCanvasFilter === 'classic'){
        return candidates.filter(isClassicCanvas);
    }
    return candidates;
}

function updateCanvasFilterCounts(){
    const candidates = getCandidateCanvases();
    const allCount = candidates.length;
    const classicCount = candidates.filter(isClassicCanvas).length;
    const smartCount = candidates.filter(c => isClassicCanvas(c) && String(c?.kind || '').toLowerCase() === 'smart').length;
    const projectCount = candidates.filter(isProjectScopedCanvas).length || canvases.filter(isProjectScopedCanvas).length;
    const referenceCount = candidates.filter(isReferenceCanvas).length || canvases.filter(isReferenceCanvas).length;

    const countAllEl = document.getElementById('filterCountAll');
    const countClassicEl = document.getElementById('filterCountClassic');
    const countProjectEl = document.getElementById('filterCountProject');
    const countReferenceEl = document.getElementById('filterCountReference');

    if(countAllEl) countAllEl.textContent = String(allCount);
    if(countClassicEl) countClassicEl.textContent = String(classicCount);
    if(countProjectEl) countProjectEl.textContent = String(projectCount);
    if(countReferenceEl) countReferenceEl.textContent = String(referenceCount);
    if(filterCountClassicDetailEl) filterCountClassicDetailEl.textContent = '';
}

function syncCanvasFilterControls(){
    canvasFilterGroupEl?.querySelectorAll('[data-canvas-filter]').forEach(button => {
        const active = button.dataset.canvasFilter === currentCanvasFilter;
        button.classList.toggle('active', active);
        button.setAttribute('aria-pressed', active ? 'true' : 'false');
    });
}

function canvasOverviewProjectName(canvas){
    const projectId = String(canvas?.project || 'default').trim();
    const project = projects.find(item => String(item.id || '') === projectId);
    return String(project?.name || (projectId === 'default' ? L('默认项目', 'Default') : projectId)).trim();
}

function canvasOverviewSubtitle(canvas){
    const explicit = String(canvas?.subtitle || canvas?.description || '').trim();
    if(explicit) return explicit;
    if(isReferenceCanvas(canvas)){
        return canvasOverviewProjectName(canvas) === L('默认项目', 'Default')
            ? L('资产参考源', 'Asset reference source')
            : `${canvasOverviewProjectName(canvas)} · ${L('参考源', 'Reference source')}`;
    }
    if(isProjectScopedCanvas(canvas)){
        const entityId = String(canvas?.entity_id || '').trim();
        return entityId
            ? `${canvasOverviewProjectName(canvas)} · ${entityId}`
            : `${canvasOverviewProjectName(canvas)} · ${L('项目工作区', 'Project workspace')}`;
    }
    if(String(canvas?.kind || '').toLowerCase() === 'smart'){
        return String(canvas?.entity_id || '').trim() || L('智能工作流', 'Smart workflow');
    }
    return String(canvas?.entity_id || '').trim() || L('独立工作区', 'Standalone workspace');
}

function overviewSearchValue(canvas){
    return [
        canvasDisplayTitle(canvas),
        canvas?.id,
        canvas?.entity_id,
        canvasOverviewProjectName(canvas),
        canvas?.kind,
        canvas?.scope,
    ].map(value => String(value || '').toLocaleLowerCase()).join(' ');
}

function overviewSortItems(items){
    return items.slice().sort((a, b) => {
        if(overviewSort === 'name'){
            const byName = canvasDisplayTitle(a).localeCompare(canvasDisplayTitle(b), langIsEn() ? 'en' : 'zh-CN', {sensitivity:'base'});
            return byName || String(a.id || '').localeCompare(String(b.id || ''));
        }
        const field = overviewSort === 'created' ? 'created_at' : 'updated_at';
        const byTime = Number(b?.[field] || 0) - Number(a?.[field] || 0);
        return byTime || String(a.id || '').localeCompare(String(b.id || ''));
    });
}

function overviewVisibleItems(){
    const term = overviewSearchTerm.trim().toLocaleLowerCase();
    const filtered = getFilteredCanvases().filter(canvas => !term || overviewSearchValue(canvas).includes(term));
    return overviewSortItems(filtered);
}

function updateOverviewFooter(sourceState = ''){
    if(!CANVAS_LIST_OVERVIEW) return;
    const source = getCandidateCanvases();
    const normal = source.filter(isClassicCanvas);
    const smart = source.filter(c => String(c?.kind || '').toLowerCase() === 'smart' && !isReferenceCanvas(c) && !isProjectScopedCanvas(c));
    const project = source.filter(c => isProjectScopedCanvas(c) && !isReferenceCanvas(c));
    const reference = source.filter(isReferenceCanvas);
    if(overviewTotalCountEl) overviewTotalCountEl.textContent = String(source.length);
    if(overviewNormalCountEl) overviewNormalCountEl.textContent = String(normal.length);
    if(overviewSmartCountEl) overviewSmartCountEl.textContent = String(smart.length);
    if(overviewProjectCountEl) overviewProjectCountEl.textContent = String(project.length);
    if(overviewReferenceCountEl) overviewReferenceCountEl.textContent = String(reference.length);
    if(overviewNormalDetailEl) overviewNormalDetailEl.textContent = '';
    if(canvasOverviewSourceEl){
        const state = sourceState || (document.documentElement.dataset.canvasListReady === 'error' ? 'error' : 'ready');
        const labels = {
            loading: L('正在加载画布', 'Loading canvases'),
            error: L('画布加载失败', 'Canvas load failed'),
            ready: L('CANVAS CLUSTER READY', 'CANVAS CLUSTER READY'),
        };
        canvasOverviewSourceEl.dataset.state = state;
        const label = canvasOverviewSourceEl.querySelector('span');
        if(label) label.textContent = labels[state] || labels.ready;
        const icon = canvasOverviewSourceEl.querySelector('[data-lucide]');
        if(icon) icon.setAttribute('data-lucide', state === 'error' ? 'circle-alert' : state === 'loading' ? 'loader-circle' : 'check-circle-2');
        if(overviewApiStateEl){
            overviewApiStateEl.textContent = state === 'error' ? L('失败','ERROR') : state === 'loading' ? L('连接中','CONNECTING') : L('就绪','READY');
            overviewApiStateEl.dataset.state = state;
        }
    }
    refreshIcons();
}

function syncOverviewDensity(){
    if(!CANVAS_LIST_OVERVIEW) return;
    document.documentElement.classList.toggle('canvas-overview-compact', overviewDensity === 'compact');
    canvasOverviewDensityEl?.querySelectorAll('[data-canvas-overview-density]').forEach(button => {
        const active = button.dataset.canvasOverviewDensity === overviewDensity;
        button.setAttribute('aria-pressed', active ? 'true' : 'false');
    });
}

function canvasOverviewIcon(canvas, type){
    const supplied = String(canvas?.icon || '').trim();
    if(/^[A-Za-z][A-Za-z0-9-]{1,32}$/.test(supplied)) return supplied;
    return type === 'smart' ? 'sparkles' : type === 'project' ? 'network' : type === 'reference' ? 'image' : 'film';
}

function canvasOverviewType(canvas){
    if(isReferenceCanvas(canvas)) return 'reference';
    if(isProjectScopedCanvas(canvas)) return 'project';
    if(String(canvas?.kind || '').toLowerCase() === 'smart') return 'smart';
    return 'normal';
}

function canvasOverviewMetric(canvas, type){
    const count = Math.max(0, Number(canvas?.node_count || 0));
    const label = type === 'smart' ? L('智能节点','smart nodes') : type === 'project' ? L('项目节点','project nodes') : type === 'reference' ? L('资源节点','resource nodes') : L('节点','nodes');
    return `${count} ${label}`;
}

function canvasOverviewBadge(canvas, type){
    const dot = `<span class="canvas-overview-card-dot ${type}"></span>`;
    if(type === 'smart') return `<span class="badge-canvas-type badge-type-smart">${dot}<span>✦ AI·SMART</span></span>`;
    if(type === 'project') return `<span class="badge-canvas-type badge-type-project">${dot}<span>PROJECT·COLLAB</span></span>`;
    if(type === 'reference') return `<span class="badge-canvas-type badge-type-ref">${dot}<span>REF·CANVAS</span></span>`;
    return `<span class="badge-canvas-type badge-type-normal">${dot}<span>STANDARD</span></span>`;
}

function renderOverviewGroup(group){
    const groupEl = document.createElement('section');
    groupEl.className = 'canvas-overview-group';
    groupEl.dataset.group = group.id;
    const countChips = group.chips.map(chip => `
        <span class="canvas-overview-count-chip${chip.className ? ` ${chip.className}` : ''}">${escapeHtml(chip.label)} ${chip.count}</span>`).join('');
    groupEl.innerHTML = `
        <header class="canvas-overview-group-header">
            <div class="canvas-overview-group-title"><span class="canvas-overview-group-dot"></span><h2>${escapeHtml(group.title)}</h2></div>
            <div class="canvas-overview-group-counts">${countChips}</div>
        </header>
        <div class="canvas-overview-cards"></div>`;
    const cardsEl = groupEl.querySelector('.canvas-overview-cards');
    group.items.forEach(canvas => cardsEl.appendChild(buildCard(canvas)));
    return groupEl;
}

function renderOverviewBoard(){
    const items = overviewVisibleItems();
    if(!items.some(item => item.id === selectedCanvasId)) selectedCanvasId = '';
    boardWorld.style.width = '100%';
    boardWorld.style.height = 'auto';
    boardWorld.style.minHeight = '100%';
    const normalItems = items.filter(c => isClassicCanvas(c));
    const smartItems = normalItems.filter(c => String(c?.kind || '').toLowerCase() === 'smart');
    const projectItems = items.filter(c => isProjectScopedCanvas(c) && !isReferenceCanvas(c));
    const referenceItems = items.filter(isReferenceCanvas);
    const columns = document.createElement('div');
    columns.className = 'canvas-overview-columns';
    const groups = [
        {
            id:'normal', title:L('智能画布 (Smart Canvas)', 'Smart canvases'), items:normalItems,
            chips:[{label:`✦ ${L('智能', 'Smart')}`, count:normalItems.length, className:'smart'}]
        },
        {
            id:'project', title:L('项目画布 (Project Canvas)', 'Project Canvas'), items:projectItems,
            chips:[{label:L('协作画布', 'Collaboration'), count:projectItems.length, className:'project'}]
        },
        {
            id:'reference', title:L('参考画布 (Reference Canvas)', 'Reference Canvas'), items:referenceItems,
            chips:[{label:L('参考源', 'Sources'), count:referenceItems.length, className:'reference'}]
        },
    ];
    groups.forEach(group => {
        if(currentCanvasFilter !== 'all' && ((currentCanvasFilter === 'classic' && group.id !== 'normal') || (currentCanvasFilter === 'project' && group.id !== 'project') || (currentCanvasFilter === 'reference' && group.id !== 'reference'))) return;
        columns.appendChild(renderOverviewGroup(group));
    });
    boardWorld.replaceChildren(columns);
    const hasItems = items.length > 0;
    boardEmptyHint.classList.toggle('hidden', hasItems);
    const emptyTextEl = boardEmptyHint.querySelector('.ws-board-empty-text');
    const emptySubEl = boardEmptyHint.querySelector('.ws-board-empty-sub');
    const emptyActionsEl = boardEmptyHint.querySelector('.ws-board-empty-actions');
    if(!hasItems){
        const hasSearch = Boolean(overviewSearchTerm.trim());
        if(emptyTextEl) emptyTextEl.textContent = hasSearch ? L('未找到匹配画布', 'No matching canvases') : L('暂无画布', 'No canvas');
        if(emptySubEl) emptySubEl.textContent = hasSearch ? L('调整搜索词或切换画布类型', 'Try another search or canvas type') : L('为当前工作区创建第一块画布', 'Create the first canvas for this workspace');
        if(emptyActionsEl) emptyActionsEl.hidden = hasSearch;
    } else if(emptyActionsEl){
        emptyActionsEl.hidden = false;
    }
    updateOverviewFooter();
    updatePasteBtn();
    refreshIcons();
    scheduleCanvasTouchbarReport();
}

function renderBoard({preserveCreateCard=false} = {}){
    applyCardScale();
    if(CANVAS_LIST_OVERVIEW){
        updateBoardHeader();
        syncCanvasFilterControls();
        updateCanvasFilterCounts();
        syncOverviewDensity();
        renderOverviewBoard();
        return;
    }
    updateBoardHeader();
    syncCanvasFilterControls();
    updateCanvasFilterCounts();
    const items = finiteBoardOrder(getFilteredCanvases());
    if(!items.some(item => item.id === selectedCanvasId)) selectedCanvasId = '';
    const cardPositions = finiteBoardLayout(items);
    const openCreateCard = preserveCreateCard
        && createCardEl?.isConnected
        && createCardViewProjectId === currentProjectId
        ? createCardEl
        : null;
    const activeCreateControl = openCreateCard?.contains(document.activeElement)
        ? document.activeElement
        : null;
    const activeCreateSelection = activeCreateControl instanceof HTMLInputElement
        ? [activeCreateControl.selectionStart, activeCreateControl.selectionEnd]
        : null;
    boardWorld.replaceChildren(...items.map(canvas => buildCard(canvas, cardPositions.get(canvas.id))));
    if(activeCreateControl){
        activeCreateControl.focus();
        if(activeCreateSelection) activeCreateControl.setSelectionRange(...activeCreateSelection);
    }
    if(!openCreateCard){
        // A project switch or refresh can invalidate the surface while it is
        // mounted outside the board. Use the same cleanup path so its backdrop
        // cannot remain above the new board content.
        if(createCardEl || createBackdropEl) closeCreateCard({restoreFocus:false});
    }
    boardEmptyHint.classList.toggle('hidden', items.length > 0);
    if(items.length === 0){
        const emptyTextEl = boardEmptyHint.querySelector('.ws-board-empty-text');
        const emptySubEl = boardEmptyHint.querySelector('.ws-board-empty-sub');
        if(currentCanvasFilter === 'reference'){
            if(emptyTextEl) emptyTextEl.textContent = L('暂无参考画布', 'No reference canvas');
            if(emptySubEl) emptySubEl.textContent = L('当前工作区暂无参考画布', 'No reference canvas in current workspace');
        } else if(currentCanvasFilter === 'project'){
            if(emptyTextEl) emptyTextEl.textContent = L('暂无项目画布', 'No project canvas');
            if(emptySubEl) emptySubEl.textContent = L('当前项目暂无专属画布', 'No project canvas in current project');
        } else if(currentCanvasFilter === 'classic'){
            if(emptyTextEl) emptyTextEl.textContent = L('暂无智能画布', 'No smart canvas');
            if(emptySubEl) emptySubEl.textContent = L('当前项目暂无智能画布', 'No smart canvas in current project');
        } else {
            if(emptyTextEl) emptyTextEl.textContent = L('暂无画布', 'No canvas');
            if(emptySubEl) emptySubEl.textContent = L('为当前项目创建第一块智能画布', 'Create the first smart canvas for current project');
        }
    }
    updatePasteBtn();
    refreshIcons();
    scheduleCanvasTouchbarReport();
}

function scheduleBoardLayout(){
    clearTimeout(boardResizeTimer);
    boardResizeTimer = setTimeout(() => {
        boardResizeTimer = 0;
        renderBoard({
            preserveCreateCard: Boolean(
                createCardEl?.isConnected && createCardViewProjectId === currentProjectId
            )
        });
    }, 60);
}

function buildCard(c, position){
    const isSmart = (c.kind || 'classic') === 'smart';
    const isReference = (c.kind || 'classic') === 'reference';
    const kindClass = isSmart ? 'smart' : isReference ? 'reference' : 'classic';
    const projectScoped = isProjectCanvas(c);
    const scopeClass = projectScoped ? 'scope-project' : '';
    const tone = canvasTone(c);
    const kindLabel = isReference ? compactLabel('参考画布','参考','Reference') : compactLabel('智能画布','智能','Smart');
    const overviewType = canvasOverviewType(c);
    const card = document.createElement('div');
    card.className = `ws-card ${scopeClass}`
        + (String(c.color || '').trim() ? ' cc-marked' : '')
        + (clipboardCanvasId === c.id ? ' cut' : '')
        + (selectedCanvasId === c.id ? ' selected' : '')
        + (CANVAS_LIST_OVERVIEW ? ` canvas-overview-card tactile-canvas-card card-${overviewType}` : '');
    card.dataset.canvasId = c.id;
    card.dataset.canvasScope = projectScoped ? 'project' : 'standalone';
    card.dataset.projectColor = tone.key;
    card.tabIndex = 0;
    card.setAttribute('role', 'button');
    card.setAttribute('aria-pressed', selectedCanvasId === c.id ? 'true' : 'false');
    card.setAttribute('aria-label', canvasDisplayTitle(c));
    if(!CANVAS_LIST_OVERVIEW){
        card.style.left = `${Number(position?.x || 0)}px`;
        card.style.top = `${Number(position?.y || 0)}px`;
    }
    card.style.setProperty('--canvas-surface', tone.surface);
    card.style.setProperty('--canvas-border', tone.border);
    card.style.setProperty('--canvas-accent', tone.accent);
    const overviewCard = CANVAS_LIST_OVERVIEW ? `
        <div class="canvas-overview-card-head">
            ${canvasOverviewBadge(c, overviewType)}
            <button class="ws-card-child-btn canvas-overview-child-btn" type="button" title="${L('新建画布','New canvas')}" aria-label="${L('新建画布','New canvas')}"><i data-lucide="plus-square" class="w-3 h-3"></i></button>
            <button class="ws-card-menu tactile-micro-btn" type="button" title="${L('更多','More')}" aria-label="${L('更多','More')}"><i data-lucide="more-horizontal" class="w-3 h-3"></i></button>
        </div>
        <div class="canvas-overview-card-copy">
            <h3 class="ws-card-title" title="${escapeAttr(canvasDisplayTitle(c))}">${escapeHtml(canvasDisplayTitle(c))}</h3>
            <div class="canvas-overview-card-subtitle" title="${escapeAttr(canvasOverviewSubtitle(c))}"><i data-lucide="${escapeAttr(canvasOverviewIcon(c, overviewType))}" class="w-3 h-3"></i><span>${escapeHtml(canvasOverviewSubtitle(c))}</span></div>
        </div>
        <div class="bay-inset canvas-overview-card-meta">
            <span class="canvas-overview-card-metric"><span class="canvas-overview-card-dot ${overviewType}"></span>${escapeHtml(canvasOverviewMetric(c, overviewType))}</span>
            <span class="ws-card-time">${formatCanvasTime(c.updated_at || c.created_at)}</span>
        </div>` : '';
    const overviewCopy = CANVAS_LIST_OVERVIEW ? `
        <div class="canvas-overview-card-copy">
            <div class="ws-card-title">${escapeHtml(canvasDisplayTitle(c))}</div>
            <div class="canvas-overview-card-subtitle" title="${escapeAttr(canvasOverviewSubtitle(c))}">${escapeHtml(canvasOverviewSubtitle(c))}</div>
        </div>` : `<div class="ws-card-title">${escapeHtml(canvasDisplayTitle(c))}</div>`;
    card.innerHTML = CANVAS_LIST_OVERVIEW ? `${overviewCard}
        <div class="ws-card-delete-confirm">
            <div class="ws-card-delete-title">${L('移入回收站？','Move to trash?')}</div>
            <div class="ws-card-delete-actions">
                <button class="ws-card-delete-yes" type="button">${L('删除','Delete')}</button>
                <button class="ws-card-delete-no" type="button">${L('取消','Cancel')}</button>
            </div>
        </div>` : `
        <div class="ws-card-top">
            <span class="ws-card-kind ${kindClass}">${kindLabel}</span>
            ${projectScoped ? `<span class="ws-card-scope scope-project">${compactLabel('项目','项目','Project')}</span>` : ''}
            <button class="ws-card-child-btn" type="button" title="${L('新建画布','New canvas')}" aria-label="${L('新建画布','New canvas')}"><i data-lucide="plus-square" class="w-3.5 h-3.5"></i></button>
            <button class="ws-card-menu" type="button" title="${L('更多','More')}" aria-label="${L('更多','More')}"><i data-lucide="more-horizontal" class="w-4 h-4"></i></button>
        </div>
        ${overviewCopy}
        <div class="ws-card-meta">
            <span class="ws-card-nodes">${(c.node_count != null ? c.node_count : 0)} ${L('节点','nodes')}</span>
            <span class="ws-card-meta-dot"></span>
            <span class="ws-card-time">${formatCanvasTime(c.updated_at || c.created_at)}</span>
        </div>
        <div class="ws-card-delete-confirm">
            <div class="ws-card-delete-title">${L('移入回收站？','Move to trash?')}</div>
            <div class="ws-card-delete-actions">
                <button class="ws-card-delete-yes" type="button">${L('删除','Delete')}</button>
                <button class="ws-card-delete-no" type="button">${L('取消','Cancel')}</button>
            </div>
        </div>`;
    const menuBtn = card.querySelector('.ws-card-menu');
    const childBtn = card.querySelector('.ws-card-child-btn');
    childBtn.onmousedown = e => e.stopPropagation();
    childBtn.onclick = e => {
        e.stopPropagation();
        openCreateCard({x:Number(c.board_x || 24) + 28, y:Number(c.board_y || 24) + 188}, c.id, childBtn);
    };
    menuBtn.onmousedown = e => e.stopPropagation();
    menuBtn.onclick = e => { e.stopPropagation(); openCardMenu(c.id, menuBtn); };
    card.querySelector('.ws-card-delete-confirm').onmousedown = e => e.stopPropagation();
    card.querySelector('.ws-card-delete-yes').onclick = e => { e.stopPropagation(); deleteCanvas(c.id); };
    card.querySelector('.ws-card-delete-no').onclick = e => { e.stopPropagation(); card.classList.remove('confirming-delete'); };
    card.addEventListener('click', e => {
        if(e.target.closest('button,input,textarea,select')) return;
        selectCanvasCard(c.id);
    });
    card.addEventListener('dblclick', e => {
        if(e.target.closest('button,input,textarea,select')) return;
        e.preventDefault();
        e.stopPropagation();
        selectCanvasCard(c.id);
        void openCanvas(c);
    });
    card.addEventListener('keydown', e => {
        if(e.key === 'Enter'){
            e.preventDefault();
            selectCanvasCard(c.id);
            void openCanvas(c);
        } else if(e.key === ' '){
            e.preventDefault();
            selectCanvasCard(c.id);
        }
    });
    return card;
}

async function bindCanvasEntityContext(canvas){
    const projectId = String(canvas?.project || currentProjectId || 'default').trim();
    const entityId = String(currentEntityId || '').trim();
    const storedEntityId = String(canvas?.entity_id || '').trim();
    if(!entityId || storedEntityId || projectId !== currentProjectId) return canvas;
    const response = await canvasListApi().updateCanvasMeta(
        canvas.id,
        canvasMetaWritePayload(canvas, {entity_id:entityId})
    );
    if(!response.ok) throw new Error('bind canvas entity failed');
    const data = await response.json();
    const updated = data.canvas ? {...canvas, ...data.canvas} : {...canvas, entity_id: entityId};
    const index = canvases.findIndex(item => item.id === canvas.id);
    if(index >= 0) canvases[index] = updated;
    return updated;
}

async function openCanvas(c){
    try {
        const canvas = await bindCanvasEntityContext(c);
        const projectId = String(canvas.project || currentProjectId || 'default').trim();
        const entityId = String(canvas.entity_id || '').trim();
        const query = new URLSearchParams({
            id: String(canvas.id || '').trim(),
            project_id: projectId,
            canvas_id: String(canvas.id || '').trim(),
            v: 'v0.0.1-alpha-2026'
        });
        if(entityId) query.set('entity_id', entityId);
        rememberProjectId(projectId);
        window.parent?.postMessage({
            type:'canvas-open',
            canvas:{
                id:String(canvas.id || '').trim(),
                title:canvasDisplayTitle(canvas),
                project:projectId,
                kind:canvas.kind || 'classic',
                version:canvas.version || 1
            }
        }, location.origin);
        // 画布内页（canvas.html / smart-canvas.html）已按用户裁决移除；
        // 仅向宿主 /static/pages/storyboard.html派发 canvas-open 事件，由宿主决定后续处理。
    } catch(e){
        console.error(e);
        setStatus(L('无法关联项目条目','Unable to link project entity'));
    }
}

/* ===== Card create flow ===== */
let createCardEl = null;
let createBackdropEl = null;
let createCardProjectId = '';
let createKind = 'classic';
let createScope = 'project';
let createParentCanvasId = '';
let createCardLabel = '';
let createCardTriggerEl = null;
// The board project that owns the open dialog. This is separate from the
// selected target project in the dialog, which may be changed by the user.
let createCardViewProjectId = '';
let createCardTriggerSequence = 0;
function createCardTriggerId(trigger){
    if(!(trigger instanceof HTMLElement) || !trigger.isConnected) return '';
    if(trigger.id) return trigger.id;
    let id = '';
    do {
        id = `canvas-create-trigger-${++createCardTriggerSequence}`;
    } while(document.getElementById(id));
    trigger.id = id;
    return id;
}
function closeCreateCard({restoreFocus = true} = {}){
    const trigger = createCardTriggerEl;
    const dialog = createCardEl;
    // Outside clicks can move focus to the backdrop before the global
    // dismissal handler runs.  The opener remains the authoritative return
    // target, so do not gate restoration on the transient active element.
    const shouldRestoreFocus = Boolean(
        restoreFocus
        && trigger?.isConnected
        && typeof trigger.focus === 'function'
    );
    createBackdropEl?.remove();
    dialog?.remove();
    board?.classList.remove('create-dialog-open');
    createBackdropEl = null;
    createCardEl = null;
    createCardTriggerEl = null;
    createCardViewProjectId = '';
    createCardProjectId = '';
    createParentCanvasId = '';
    createCardLabel = '';
    if(shouldRestoreFocus && trigger?.isConnected && typeof trigger.focus === 'function'){
        const restore = () => {
            if(trigger.isConnected && !trigger.hidden) trigger.focus({preventScroll:true});
        };
        // Restore immediately for pointer dismissal, then once more after the
        // DOM removal has settled so the shared dismissal manager cannot leave
        // focus on the transient backdrop/body.
        restore();
        window.requestAnimationFrame?.(restore);
    }
}
function openCreateCard(worldPt, parentCanvasId = '', trigger = document.activeElement){
    // Opening a replacement surface must not return focus to the old trigger.
    closeCreateCard({restoreFocus:false});
    closeCardMenu();
    createKind = 'smart';
    createScope = 'project';
    createParentCanvasId = String(parentCanvasId || '').trim();
    const parentCanvas = createParentCanvasId
        ? canvases.find(item => item.id === createParentCanvasId)
        : null;
    if(parentCanvas){
        createScope = String(parentCanvas.scope || 'standalone');
    }
    createCardLabel = L('新建智能画布', 'New smart canvas');
    const createProjectId = parentCanvas?.project || currentProjectId || 'default';
    const createEntityId = currentEntityId;
    const inheritedEntityId = parentCanvas?.entity_id || createEntityId;
    const floatingTrigger = trigger instanceof HTMLElement && trigger.isConnected ? trigger : newCanvasBtn;
    const floatingTriggerId = createCardTriggerId(floatingTrigger);
    const el = document.createElement('div');
    el.className = 'ws-create-card ws-create-dialog';
    el.setAttribute('role', 'dialog');
    el.setAttribute('aria-modal', 'true');
    el.setAttribute('aria-labelledby', 'ws-create-title');
    el.setAttribute('tabindex', '-1');
    el.dataset.floatingClose = 'closeCreateCard';
    el.dataset.floatingContent = 'self';
    el.dataset.floatingKind = 'modal';
    el.dataset.floatingDismiss = 'outside';
    el.dataset.floatingEscape = 'close-only';
    if(floatingTriggerId) el.dataset.floatingTrigger = floatingTriggerId;
    el.style.left = '50%';
    el.style.top = '50%';
    el.innerHTML = `
        <div class="ws-create-head">
            <div id="ws-create-title" class="ws-create-title">${escapeHtml(createCardLabel)}</div>
            <button class="ws-create-close" type="button" data-floating-close title="${L('关闭','Close')}" aria-label="${L('关闭','Close')}"><i data-lucide="x" class="w-4 h-4"></i></button>
        </div>
        <label class="ws-create-field"><span>${L('画布名称','Canvas name')}</span><input class="ws-create-input" type="text" maxlength="80" placeholder="${L('可不填，自动命名','Optional, auto-generated if empty')}"></label>
        <div class="ws-create-toggle ws-create-scope-toggle">
            <button class="ws-create-toggle-btn active" type="button" data-scope="project" aria-pressed="true">${L('项目画布','Project canvas')}</button>
            <button class="ws-create-toggle-btn" type="button" data-scope="standalone" aria-pressed="false">${L('独立画布','Standalone')}</button>
        </div>
        <label class="ws-create-field ws-create-project-field"><span>${L('所属项目','Project')}</span><select class="ws-create-project" aria-label="${L('选择所属项目','Select project')}">${projects.map(project => `<option value="${escapeAttr(project.id)}"${project.id === createProjectId ? ' selected' : ''}>${escapeHtml(project.name || project.id)}</option>`).join('')}</select></label>
        <div class="p-2 rounded-lg bg-[#0e1118] border border-[#dfc384]/20 text-[#eddab3] flex items-center justify-between font-mono" style="font-size:var(--gw-type-label-md);">
            <div class="flex items-center space-x-1.5">
                <i data-lucide="sparkles" class="w-3.5 h-3.5 text-[#dfc384]"></i>
                <span>${L('模式：智能画布 (统一模式)','Mode: Smart Canvas')}</span>
            </div>
            <span class="text-amber-300 font-bold">SMART</span>
        </div>
        <div class="ws-create-actions">
            <button class="ws-create-confirm" type="button">${L('创建画布','Create canvas')}</button>
            <button class="ws-create-cancel" type="button" data-floating-close>${L('取消','Cancel')}</button>
        </div>`;
    const backdrop = document.createElement('div');
    backdrop.className = 'ws-create-backdrop';
    backdrop.setAttribute('aria-hidden', 'true');
    backdrop.dataset.canvasCreateBackdrop = 'true';
    document.body.appendChild(backdrop);
    document.body.appendChild(el);
    board.classList.add('create-dialog-open');
    window.FloatingDismissal?.mark?.(el, {
        surface:'canvas-create-card',
        kind:'modal',
        content:'self',
        close:closeCreateCard,
        trigger:floatingTriggerId || undefined
    });
    const dismissCreateCard = () => window.FloatingDismissal?.closeSurface(el) || closeCreateCard();
    backdrop.addEventListener('pointerdown', e => {
        e.preventDefault();
        e.stopPropagation();
    });
    backdrop.addEventListener('click', e => {
        e.preventDefault();
        e.stopPropagation();
        dismissCreateCard();
    });
    createCardEl = el;
    createBackdropEl = backdrop;
    createCardTriggerEl = floatingTrigger;
    createCardViewProjectId = String(currentProjectId || 'default');
    createCardProjectId = createProjectId;
    el.addEventListener('mousedown', e => e.stopPropagation());
    const input = el.querySelector('.ws-create-input');
    const projectField = el.querySelector('.ws-create-project-field');
    const projectSelect = el.querySelector('.ws-create-project');
    const syncCreateTitleField = () => {
        const projectScoped = createScope === 'project';
        projectField.hidden = !projectScoped;
        projectSelect.disabled = !projectScoped;
        createCardProjectId = projectScoped ? String(projectSelect.value || createProjectId) : 'default';
    };
    syncCreateTitleField();
    el.querySelectorAll('[data-scope]').forEach(btn => {
        const active = btn.dataset.scope === createScope;
        btn.classList.toggle('active', active);
        btn.setAttribute('aria-pressed', active ? 'true' : 'false');
    });
    input.focus();
    el.querySelectorAll('.ws-create-toggle-btn').forEach(btn => {
        btn.onclick = () => {
            if(btn.dataset.kind) createKind = btn.dataset.kind;
            if(btn.dataset.scope){
                createScope = btn.dataset.scope;
                syncCreateTitleField();
            }
            const group = btn.parentElement;
            group?.querySelectorAll('.ws-create-toggle-btn').forEach(b => {
                const active = b === btn;
                b.classList.toggle('active', active);
                b.setAttribute('aria-pressed', active ? 'true' : 'false');
            });
        };
    });
    projectSelect.onchange = () => {
        createCardProjectId = String(projectSelect.value || createProjectId);
    };
    const confirm = () => {
        // Standalone canvases live in the default project bucket so they are
        // not accidentally ACL-bound to whichever project happens to be open.
        const targetProjectId = createScope === 'project'
            ? String(projectSelect.value || createProjectId)
            : 'default';
        const targetEntityId = createScope === 'project'
            && targetProjectId === String(createProjectId)
            ? inheritedEntityId
            : '';
        const parentForCreate = parentCanvas
            && String(parentCanvas.scope || 'standalone') === createScope
            ? createParentCanvasId
            : '';
        return createCanvasOnBoard(
            input.value.trim(),
            createKind,
            boardCreateWorldPoint(targetProjectId),
            targetProjectId,
            targetEntityId,
            createScope,
            parentForCreate
        );
    };
    el.querySelector('.ws-create-confirm').onclick = confirm;
    el.querySelector('.ws-create-cancel').onclick = dismissCreateCard;
    el.querySelector('.ws-create-close').onclick = dismissCreateCard;
    refreshIcons();
    input.onkeydown = e => {
        e.stopPropagation();
        if(e.key === 'Enter'){ e.preventDefault(); confirm(); }
        if(e.key === 'Escape'){ e.preventDefault(); dismissCreateCard(); }
    };
}

async function createCanvasOnBoard(title, kind = 'smart', worldPt, projectId = currentProjectId, entityId = currentEntityId, scope = 'project', parentCanvasId = ''){
    const canvasScope = String(scope || 'project').trim().toLowerCase() === 'standalone'
        ? 'standalone'
        : 'project';
    const canvasProjectId = canvasScope === 'standalone'
        ? 'default'
        : String(projectId || currentProjectId || 'default');
    const canvasEntityId = canvasScope === 'standalone' ? '' : String(entityId || '');
    const name = String(title || '').trim();
    closeCreateCard();
    try {
        const res = await canvasListApi().createCanvas({
            title: name,
            project_id: canvasProjectId,
            project: canvasProjectId,
            mode: 'smart',
            kind: 'smart',
            icon: 'sparkles',
            title_auto: true,
            entity_id: canvasEntityId,
            scope: canvasScope,
            parent_canvas_id: String(parentCanvasId || '').trim(),
            board_x: Math.round(worldPt.x),
            board_y: Math.round(worldPt.y)
        });
        if(!res.ok) {
            const err = await res.json().catch(() => ({}));
            console.error('create canvas failed:', res.status, err);
            throw new Error(err?.detail?.message || 'create canvas failed');
        }
        const data = await res.json();
        const nc = data.canvas;
        if(nc){
            const newId = nc.canvas_id || nc.id;
            const newCanvas = {
                id: newId,
                canvas_id: newId,
                title: name || '未命名智能画布',
                project: canvasProjectId,
                project_id: canvasProjectId,
                kind: 'smart',
                mode: 'smart',
                version: nc.version || 1,
                board_x: Math.round(worldPt.x),
                board_y: Math.round(worldPt.y),
                created_at: Math.floor(Date.now() / 1000)
            };
            canvases.push(newCanvas);
            if(currentProjectId !== canvasProjectId){
                currentProjectId = canvasProjectId;
                rememberProjectId(currentProjectId);
                syncCanvasListContext();
            }
            selectedCanvasId = newId;
            renderProjects();
            renderBoard();
            setStatus(L('创建成功','Canvas created'));
            loadAll().catch(() => {});
        }
    } catch(e){ console.error(e); setStatus(L('创建失败','Create failed')); }
}

/* ===== Card context menu (rename / delete / move) ===== */
function closeCardMenu(){ document.querySelector('.ws-card-pop')?.remove(); }
function openCardMenu(canvasId, anchorBtn){
    closeCardMenu();
    const c = canvases.find(x => x.id === canvasId);
    if(!c) return;
    const pop = document.createElement('div');
    pop.className = 'ws-card-pop';
    if(CANVAS_LIST_OVERVIEW){
        pop.dataset.floatingRemoveOnClose = 'true';
        pop.dataset.floatingTrigger = createCardTriggerId(anchorBtn);
    }
    pop.innerHTML = `
        ${isProjectCanvas(c) ? '' : `<button class="ws-pop-item" data-act="rename"><i data-lucide="pencil" class="w-4 h-4"></i><span>${L('重命名','Rename')}</span></button>`}
        <button class="ws-pop-item" data-act="new-child"><i data-lucide="git-branch" class="w-4 h-4"></i><span>${L('新建画布','New canvas')}</span></button>
        <button class="ws-pop-item" data-act="export"><i data-lucide="download" class="w-4 h-4"></i><span>${L('导出画布','Export canvas')}</span></button>
        <button class="ws-pop-item" data-act="export-assets"><i data-lucide="archive" class="w-4 h-4"></i><span>${L('导出画布 + 资源','Export with assets')}</span></button>
        <button class="ws-pop-item" data-act="cut"><i data-lucide="scissors" class="w-4 h-4"></i><span>${L('剪切到其他项目','Cut to project')}</span></button>
        <div class="ws-pop-sep"></div>
        <button class="ws-pop-item" data-act="archive"><i data-lucide="archive" class="w-4 h-4"></i><span>${L('归档画布','Archive canvas')}</span></button>
        <button class="ws-pop-item danger" data-act="delete"><i data-lucide="trash-2" class="w-4 h-4"></i><span>${L('删除','Delete')}</span></button>`;
    document.body.appendChild(pop);
    const r = anchorBtn.getBoundingClientRect();
    const w = pop.offsetWidth || 188, h = pop.offsetHeight || 120;
    let left = Math.min(r.left, window.innerWidth - w - 12);
    let top = r.bottom + 6;
    if(top + h > window.innerHeight - 12) top = r.top - h - 6;
    pop.style.left = Math.round(Math.max(12, left)) + 'px';
    pop.style.top = Math.round(Math.max(12, top)) + 'px';
    pop.querySelector('[data-act="rename"]')?.addEventListener('click', () => { closeCardMenu(); startCardRename(canvasId); });
    pop.querySelector('[data-act="new-child"]').onclick = () => {
        closeCardMenu();
        const parent = canvases.find(item => item.id === canvasId);
        openCreateCard({x:Number(parent?.board_x || 24) + 28, y:Number(parent?.board_y || 24) + 188}, canvasId, anchorBtn);
    };
    pop.querySelector('[data-act="export"]').onclick = () => { closeCardMenu(); exportCanvas(canvasId); };
    pop.querySelector('[data-act="export-assets"]').onclick = () => { closeCardMenu(); exportCanvasWithResources(canvasId); };
    pop.querySelector('[data-act="cut"]').onclick = () => { closeCardMenu(); cutCanvas(canvasId); };
    pop.querySelector('[data-act="archive"]').onclick = () => { closeCardMenu(); archiveCanvas(canvasId); };
    pop.querySelector('[data-act="delete"]')?.addEventListener('click', () => { closeCardMenu(); showCardDeleteConfirm(canvasId); });
    refreshIcons();
}

function showCardDeleteConfirm(canvasId){
    const canvas = canvases.find(item => item.id === canvasId);
    const card = boardWorld.querySelector(`.ws-card[data-canvas-id="${CSS.escape(canvasId)}"]`);
    if(!card) return;
    boardWorld.querySelectorAll('.ws-card.confirming-delete').forEach(el => {
        if(el !== card) el.classList.remove('confirming-delete');
    });
    card.classList.add('confirming-delete');
}

/* ===== Export canvas (download the full canvas JSON) ===== */
async function exportCanvas(id){
    const c = canvases.find(x => x.id === id);
    setStatus(L('正在导出...','Exporting...'));
    try {
        const res = await canvasListApi().getCanvas(id);
        if(!res.ok) throw new Error('export failed');
        const data = await res.json();
        const cv = data.canvas || data;
        const base = String((c?.title) || cv.title || 'canvas').replace(/[\\/:*?"<>|]+/g, '_').trim().slice(0, 60) || 'canvas';
        const blob = new Blob([JSON.stringify(cv, null, 2)], { type: 'application/json' });
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
         a.href = url; a.download = base + '.godmap';
        document.body.appendChild(a); a.click(); a.remove();
        setTimeout(() => URL.revokeObjectURL(url), 1500);
        setStatus(L('已导出','Exported'));
    } catch(e){ console.error(e); setStatus(L('导出失败','Export failed')); }
}

/* ===== Export canvas with referenced resources ===== */
const ZIP_ENCODER = new TextEncoder();
let ZIP_CRC_TABLE = null;

function safeExportBase(name, fallback = 'canvas'){
    return String(name || fallback).replace(/[\\/:*?"<>|]+/g, '_').trim().slice(0, 60) || fallback;
}

function collectCanvasResourceUrls(value, out = [], seen = new Set()){
    if(value == null) return out;
    if(typeof value === 'string'){
        const text = value.trim();
        if(isCanvasResourceUrl(text) && !seen.has(text)){
            seen.add(text);
            out.push(text);
        }
        return out;
    }
    if(Array.isArray(value)){
        value.forEach(item => collectCanvasResourceUrls(item, out, seen));
        return out;
    }
    if(typeof value === 'object'){
        Object.values(value).forEach(item => collectCanvasResourceUrls(item, out, seen));
    }
    return out;
}

function isCanvasResourceUrl(url){
    return url.startsWith('/assets/') || url.startsWith('/output/') || /^https?:\/\//i.test(url);
}

function exportResourceName(url, index, used){
    let name = '';
    try {
        const parsed = new URL(url, location.origin);
        name = decodeURIComponent(parsed.pathname.split('/').filter(Boolean).pop() || '');
    } catch(e) {
        name = String(url || '').split(/[?#]/)[0].split('/').pop() || '';
    }
    name = safeExportBase(name || `resource-${String(index + 1).padStart(3, '0')}`, `resource-${index + 1}`);
    if(!/\.[a-z0-9]{1,8}$/i.test(name)) name += '.bin';
    let finalName = `resources/${name}`;
    const dot = finalName.lastIndexOf('.');
    const stem = dot > 0 ? finalName.slice(0, dot) : finalName;
    const ext = dot > 0 ? finalName.slice(dot) : '';
    let suffix = 2;
    while(used.has(finalName)){
        finalName = `${stem}-${suffix}${ext}`;
        suffix++;
    }
    used.add(finalName);
    return finalName;
}

async function fetchResourceBytes(url){
    const res = await canvasListApi().getResource(url);
    if(!res.ok) throw new Error(`HTTP ${res.status}`);
    return new Uint8Array(await res.arrayBuffer());
}

function zipCrc32(bytes){
    if(!ZIP_CRC_TABLE){
        ZIP_CRC_TABLE = new Uint32Array(256);
        for(let i = 0; i < 256; i++){
            let c = i;
            for(let k = 0; k < 8; k++) c = (c & 1) ? (0xedb88320 ^ (c >>> 1)) : (c >>> 1);
            ZIP_CRC_TABLE[i] = c >>> 0;
        }
    }
    let crc = 0xffffffff;
    for(let i = 0; i < bytes.length; i++) crc = ZIP_CRC_TABLE[(crc ^ bytes[i]) & 0xff] ^ (crc >>> 8);
    return (crc ^ 0xffffffff) >>> 0;
}

function zipDosTime(date = new Date()){
    const time = (date.getHours() << 11) | (date.getMinutes() << 5) | Math.floor(date.getSeconds() / 2);
    const year = Math.max(1980, date.getFullYear());
    const day = ((year - 1980) << 9) | ((date.getMonth() + 1) << 5) | date.getDate();
    return { time, day };
}

function zipHeader(signature, size){
    const bytes = new Uint8Array(size);
    const view = new DataView(bytes.buffer);
    view.setUint32(0, signature, true);
    return { bytes, view };
}

function createZipBlob(entries){
    const now = zipDosTime();
    const files = [];
    const central = [];
    let offset = 0;
    entries.forEach(entry => {
        const nameBytes = ZIP_ENCODER.encode(entry.name);
        const data = entry.bytes instanceof Uint8Array ? entry.bytes : ZIP_ENCODER.encode(String(entry.bytes || ''));
        const crc = zipCrc32(data);
        const local = zipHeader(0x04034b50, 30 + nameBytes.length);
        local.view.setUint16(4, 20, true);
        local.view.setUint16(6, 0x0800, true);
        local.view.setUint16(8, 0, true);
        local.view.setUint16(10, now.time, true);
        local.view.setUint16(12, now.day, true);
        local.view.setUint32(14, crc, true);
        local.view.setUint32(18, data.length, true);
        local.view.setUint32(22, data.length, true);
        local.view.setUint16(26, nameBytes.length, true);
        local.bytes.set(nameBytes, 30);
        files.push(local.bytes, data);

        const cd = zipHeader(0x02014b50, 46 + nameBytes.length);
        cd.view.setUint16(4, 20, true);
        cd.view.setUint16(6, 20, true);
        cd.view.setUint16(8, 0x0800, true);
        cd.view.setUint16(10, 0, true);
        cd.view.setUint16(12, now.time, true);
        cd.view.setUint16(14, now.day, true);
        cd.view.setUint32(16, crc, true);
        cd.view.setUint32(20, data.length, true);
        cd.view.setUint32(24, data.length, true);
        cd.view.setUint16(28, nameBytes.length, true);
        cd.view.setUint32(42, offset, true);
        cd.bytes.set(nameBytes, 46);
        central.push(cd.bytes);
        offset += local.bytes.length + data.length;
    });
    const centralSize = central.reduce((sum, bytes) => sum + bytes.length, 0);
    const end = zipHeader(0x06054b50, 22);
    end.view.setUint16(8, entries.length, true);
    end.view.setUint16(10, entries.length, true);
    end.view.setUint32(12, centralSize, true);
    end.view.setUint32(16, offset, true);
    return new Blob([...files, ...central, end.bytes], { type:'application/zip' });
}

async function exportCanvasWithResources(id){
    const c = canvases.find(x => x.id === id);
    setStatus(L('正在收集资源...','Collecting assets...'));
    try {
        const res = await canvasListApi().getCanvas(id);
        if(!res.ok) throw new Error('export failed');
        const data = await res.json();
        const cv = data.canvas || data;
        const base = safeExportBase((c?.title) || cv.title || 'canvas');
        const urls = collectCanvasResourceUrls(cv).slice(0, 1000);
        const usedNames = new Set(['canvas.json', 'resources-manifest.json']);
        const entries = [{ name:'canvas.json', bytes:ZIP_ENCODER.encode(JSON.stringify(cv, null, 2)) }];
        const manifest = [];
        let skipped = 0;
        for(let i = 0; i < urls.length; i++){
            const url = urls[i];
            try {
                const bytes = await fetchResourceBytes(url);
                const name = exportResourceName(url, i, usedNames);
                entries.push({ name, bytes });
                manifest.push({ url, file:name, size:bytes.length });
            } catch(e) {
                skipped++;
                manifest.push({ url, skipped:true, reason:String(e?.message || e || 'fetch failed').slice(0, 120) });
            }
        }
        entries.push({ name:'resources-manifest.json', bytes:ZIP_ENCODER.encode(JSON.stringify({ canvas_id:id, resources:manifest }, null, 2)) });
        const blob = createZipBlob(entries);
        const href = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = href;
        a.download = `${base}.zip`;
        document.body.appendChild(a); a.click(); a.remove();
        setTimeout(() => URL.revokeObjectURL(href), 1500);
        const included = Math.max(0, entries.length - 2);
        setStatus(skipped
            ? L(`已导出，跳过 ${skipped} 个资源`, `Exported, skipped ${skipped} assets`)
            : L(`已导出 ${included} 个资源`, `Exported ${included} assets`));
    } catch(e){ console.error(e); setStatus(L('导出失败','Export failed')); }
}

/* ===== Cut / paste a canvas across projects ===== */
function cutCanvas(id){
    clipboardCanvasId = id;
    setStatus(L('已剪切，切换到目标项目后点“粘贴到此项目”','Cut — open another project, then Paste'));
    renderBoard();
}
function updatePasteBtn(){
    if(!pasteCanvasBtn) return;
    const show = !!clipboardCanvasId && canvases.some(x => x.id === clipboardCanvasId);
    pasteCanvasBtn.style.display = show ? 'inline-flex' : 'none';
}
async function pasteCanvas(){
    if(!clipboardCanvasId) return;
    const c = canvases.find(x => x.id === clipboardCanvasId);
    const targetPid = currentProjectId;
    clipboardCanvasId = null;
    if(!c){ updatePasteBtn(); renderBoard(); return; }
    if((c.project || 'default') === targetPid){ renderBoard(); setStatus(L('已在当前项目','Already in this project')); return; }
    await moveCanvasToProject(c.id, targetPid);
}

function startCardRename(canvasId){
    const card = boardWorld.querySelector(`.ws-card[data-canvas-id="${CSS.escape(canvasId)}"]`);
    const c = canvases.find(x => x.id === canvasId);
    if(!card || !c || isProjectCanvas(c)) return;
    const titleEl = card.querySelector('.ws-card-title');
    if(!titleEl || titleEl.querySelector('input')) return;
    const input = document.createElement('input');
    input.type = 'text'; input.maxLength = 80; input.value = c.title || '';
    input.className = 'ws-card-title-input';
    titleEl.innerHTML = ''; titleEl.appendChild(input);
    input.onmousedown = e => e.stopPropagation();
    input.onclick = e => e.stopPropagation();
    input.focus(); input.select();
    let done = false;
    const finish = commit => {
        if(done) return; done = true;
        const v = input.value.trim();
        if(commit && v && v !== c.title) setCanvasTitle(canvasId, v);
        else renderBoard();
    };
    input.onblur = () => finish(true);
    input.onkeydown = e => {
        e.stopPropagation();
        if(e.key === 'Enter'){ e.preventDefault(); finish(true); }
        if(e.key === 'Escape'){ e.preventDefault(); finish(false); }
    };
}

async function setCanvasTitle(id, title){
    const c = canvases.find(x => x.id === id);
    if(isProjectCanvas(c)) return;
    if(c) c.title = title;
    renderBoard();
    await persistMeta(id, { title });
}

async function moveCanvasToProject(id, projectId){
    const c = canvases.find(x => x.id === id);
    if(c) c.project = projectId;
    renderBoard();
    renderProjects();
    setStatus(L('已移动','Moved'));
    await persistMeta(id, { project: projectId });
}

/* ===== Card meta persist (POST /meta) ===== */
function canvasMetaWritePayload(canvas, patch){
    const payload = {
        entity_id: entityId,
        ...patch
    };
    const version = Number(canvas?.governance_version || 0);
    if(Number.isInteger(version) && version > 0) payload.expected_version = version;
    const updatedAt = Number(canvas?.updated_at || 0);
    if(Number.isFinite(updatedAt) && updatedAt > 0) payload.base_updated_at = updatedAt;
    return payload;
}

async function persistMeta(id, patch){
    const current = canvases.find(canvas => canvas.id === id) || null;
    try {
        const res = await canvasListApi().updateCanvasMeta(
            id,
            canvasMetaWritePayload(current, patch)
        );
        if(res.status === 409){
            setStatus(L('画布已被其他用户更新，已刷新','Canvas changed elsewhere; refreshed'));
            await loadAll();
            return null;
        }
        if(!res.ok) throw new Error('meta save failed');
        const data = await res.json();
        if(data.canvas){
            const idx = canvases.findIndex(x => x.id === id);
            if(idx >= 0) canvases[idx] = { ...canvases[idx], ...data.canvas };
        }
        return data.canvas || null;
    } catch(e){ console.error(e); setStatus(L('保存失败','Save failed')); }
    return null;
}

/* ===== Delete canvas (soft -> trash, with confirm) ===== */
async function deleteCanvas(id){
    const c = canvases.find(x => x.id === id);
    if(!c) return;
    try {
        const res = await canvasListApi().deleteCanvas(id);
        if(!res.ok) throw new Error('delete failed');
        canvases = canvases.filter(x => x.id !== id);
        renderBoard();
        renderProjects();
        refreshTrashCount();
        setStatus(L('已移入回收站','Moved to trash'));
    } catch(e){ console.error(e); setStatus(L('删除失败','Delete failed')); }
}

/* ===== Trash / recycle bin ===== */
async function refreshTrashCount(){
    try {
        const res = await canvasListApi().listTrashedCanvases();
        if(!res.ok) return;
        const data = await res.json();
        deletedCanvases = data.canvases || [];
        const n = deletedCanvases.length;
        if (trashBadge) { trashBadge.textContent = String(n); trashBadge.classList.toggle('visible', n > 0); }
    } catch(e){
        // 未接入 / 不可用时不得静默：列表器直接标明未接入。
        if(canvasListDegradationLabel(e, '')) { if(trashBadge){ trashBadge.textContent = '未接入'; trashBadge.classList.add('visible'); trashBadge.dataset.gwDegradation = e.code === 'SERVICE_UNAVAILABLE' ? 'service_unavailable' : 'not_integrated'; } }
    }
    try {
        const res = await canvasListApi().listArchivedCanvases();
        if(!res.ok) return;
        const data = await res.json();
        archivedCanvases = data.canvases || [];
        archiveBadge.textContent = String(archivedCanvases.length);
        archiveBadge.classList.toggle('visible', archivedCanvases.length > 0);
    } catch(e){
        if(canvasListDegradationLabel(e, '')) { archiveBadge.textContent = '未接入'; archiveBadge.classList.add('visible'); archiveBadge.dataset.gwDegradation = e.code === 'SERVICE_UNAVAILABLE' ? 'service_unavailable' : 'not_integrated'; }
    }
}
async function openTrashView(){
    closeArchiveView({restoreFocus:false});
    trashReturnFocusEl = document.activeElement instanceof HTMLElement
        && document.activeElement !== document.body
        ? document.activeElement
        : trashEntryBtn;
    trashEntryBtn?.classList.add('active');
    trashEntryBtn?.setAttribute('aria-expanded', 'true');
    trashPanel?.classList.add('active');
    if(trashBackdrop) trashBackdrop.style.display = 'block';
    closeCardMenu(); closeCreateCard();
    const focusTarget = trashCloseBtn || trashPanel;
    window.requestAnimationFrame?.(() => {
        if(trashPanel?.classList.contains('active')) focusTarget?.focus({preventScroll:true});
    });
    await loadTrash();
}
function closeTrashView({restoreFocus = true} = {}){
    const returnFocus = trashReturnFocusEl || trashEntryBtn;
    trashEntryBtn?.classList.remove('active');
    trashEntryBtn?.setAttribute('aria-expanded', 'false');
    trashPanel?.classList.remove('active');
    if(trashBackdrop) trashBackdrop.style.display = 'none';
    trashReturnFocusEl = null;
    if(restoreFocus && returnFocus?.isConnected && !returnFocus.hidden){
        const restore = () => returnFocus.focus({preventScroll:true});
        restore();
        window.requestAnimationFrame?.(restore);
    }
}
async function openArchiveView(){
    archiveReturnFocusEl = document.activeElement instanceof HTMLElement
        && document.activeElement !== document.body
        ? document.activeElement
        : archiveEntryBtn;
    archiveEntryBtn.classList.add('active');
    archiveEntryBtn.setAttribute('aria-expanded', 'true');
    archivePanel.classList.add('active');
    if(archiveBackdrop) archiveBackdrop.style.display = 'block';
    closeCardMenu(); closeCreateCard();
    // A modal must own focus while open. The shared dismissal manager also
    // applies this rule, but focusing explicitly keeps this static panel
    // deterministic when it is opened by script or a fast pointer click.
    const focusTarget = archiveCloseBtn || archivePanel;
    window.requestAnimationFrame?.(() => {
        if(archivePanel.classList.contains('active')) focusTarget.focus({preventScroll:true});
    });
    await loadArchive();
}
function closeArchiveView({restoreFocus = true} = {}){
    const returnFocus = archiveReturnFocusEl || archiveEntryBtn;
    archiveEntryBtn.classList.remove('active');
    archiveEntryBtn.setAttribute('aria-expanded', 'false');
    archivePanel.classList.remove('active');
    if(archiveBackdrop) archiveBackdrop.style.display = 'none';
    archiveReturnFocusEl = null;
    if(restoreFocus && returnFocus?.isConnected && !returnFocus.hidden){
        const restore = () => returnFocus.focus({preventScroll:true});
        restore();
        window.requestAnimationFrame?.(restore);
    }
}
async function loadArchive(){
    try {
        const res = await canvasListApi().listArchivedCanvases();
        if(!res.ok) throw new Error('archive load failed');
        const data = await res.json();
        archivedCanvases = data.canvases || [];
        renderArchive();
        archiveBadge.textContent = String(archivedCanvases.length);
        archiveBadge.classList.toggle('visible', archivedCanvases.length > 0);
    } catch(e){ console.error(e); setStatus(L('加载画布归档失败','Load archive failed')); }
}
function renderArchive(){
    if(!archiveListEl) return;
    archiveListEl.innerHTML = '';
    if(!archivedCanvases.length){
        const empty = document.createElement('div');
        empty.className = 'ws-trash-empty';
        empty.textContent = L('画布归档为空','Canvas archive is empty');
        archiveListEl.appendChild(empty);
        return;
    }
    archivedCanvases.forEach(c => {
        const card = document.createElement('div');
        card.className = 'ws-trash-card';
        card.dataset.canvasId = c.id;
        const projName = (projects.find(p => p.id === (c.project || 'default')) || {}).name || L('默认项目','Default');
        card.innerHTML = `
            <div class="ws-card-top"><span class="ws-card-icon">${renderCanvasIcon(c.icon, 17)}</span><span class="ws-card-kind">${L('已归档','Archived')}</span></div>
            <div class="ws-card-title">${escapeHtml(c.title)}</div>
            <div class="ws-card-meta"><span class="ws-card-nodes">${escapeHtml(projName)}</span><span class="ws-card-meta-dot"></span><span class="ws-card-time">${formatCanvasTime(c.archived_at)}</span></div>
            <div class="ws-card-actions"><button class="ws-trash-act restore" type="button"><i data-lucide="archive-restore" class="w-3.5 h-3.5"></i><span>${L('召回','Restore')}</span></button></div>`;
        card.querySelector('.restore').onclick = () => unarchiveCanvas(c.id);
        archiveListEl.appendChild(card);
    });
    refreshIcons();
}
async function archiveCanvas(id){
    try {
        const res = await canvasListApi().archiveCanvas(id);
        if(!res.ok) throw new Error('archive failed');
        canvases = canvases.filter(c => c.id !== id);
        renderBoard(); renderProjects(); await refreshTrashCount();
        setStatus(L('已归档','Archived'));
    } catch(e){ console.error(e); setStatus(L('归档失败','Archive failed')); }
}
async function unarchiveCanvas(id){
    try {
        const res = await canvasListApi().unarchiveCanvas(id);
        if(!res.ok) throw new Error('unarchive failed');
        archivedCanvases = archivedCanvases.filter(c => c.id !== id);
        await loadAll();
        renderArchive();
        setStatus(L('已召回','Restored'));
    } catch(e){ console.error(e); setStatus(L('召回失败','Restore failed')); }
}
async function loadTrash(){
    try {
        const res = await canvasListApi().listTrashedCanvases();
        if(!res.ok) throw new Error('trash load failed');
        const data = await res.json();
        deletedCanvases = data.canvases || [];
        renderTrash();
        const n = deletedCanvases.length;
        if (trashBadge) { trashBadge.textContent = String(n); trashBadge.classList.toggle('visible', n > 0); }
    } catch(e){ console.error(e); setStatus(L('加载回收站失败','Load trash failed')); }
}
function renderTrash(){
    trashListEl.innerHTML = '';
    if(!deletedCanvases.length){
        const empty = document.createElement('div');
        empty.className = 'ws-trash-empty';
        empty.textContent = L('回收站为空','Trash is empty');
        trashListEl.appendChild(empty);
        return;
    }
    deletedCanvases.forEach(c => {
        const isSmart = (c.kind || 'smart') === 'smart';
        const isReference = (c.kind || 'smart') === 'reference';
        const kindClass = isReference ? 'reference' : 'smart';
        const kindLabel = isReference ? L('参考','Reference') : L('智能','Smart');
        const projName = (projects.find(p => p.id === (c.project || 'default')) || {}).name || L('默认项目','Default');
        const card = document.createElement('div');
        card.className = 'ws-trash-card';
        card.dataset.canvasId = c.id;
        card.innerHTML = `
            <div class="ws-card-top">
                <span class="ws-card-icon">${renderCanvasIcon(isSmart && /[^\x00-\x7F]/.test(c.icon || '') ? 'sparkles' : c.icon, 17)}</span>
                <span class="ws-card-kind ${kindClass}">${kindLabel}</span>
            </div>
            <div class="ws-card-title">${escapeHtml(c.title)}</div>
            <div class="ws-card-meta"><span class="ws-card-nodes">${escapeHtml(projName)}</span><span class="ws-card-meta-dot"></span><span class="ws-card-time">${formatCanvasTime(c.deleted_at)}</span></div>
            <div class="ws-card-actions">
                <button class="ws-trash-act restore" type="button"><i data-lucide="rotate-ccw" class="w-3.5 h-3.5"></i><span>${L('恢复','Restore')}</span></button>
                <button class="ws-trash-act purge" type="button"><i data-lucide="trash-2" class="w-3.5 h-3.5"></i><span>${L('彻底删除','Delete')}</span></button>
            </div>
            <div class="ws-trash-confirm">
                <div class="ws-trash-confirm-title">${L('彻底删除？不可恢复','Delete permanently?')}</div>
                <div class="ws-trash-confirm-actions">
                    <button class="ws-trash-confirm-yes" type="button">${L('删除','Delete')}</button>
                    <button class="ws-trash-confirm-no" type="button">${L('取消','Cancel')}</button>
                </div>
            </div>`;
        card.querySelector('.ws-trash-act.restore').onclick = () => restoreCanvas(c.id);
        card.querySelector('.ws-trash-act.purge').onclick = () => card.classList.add('confirming');
        card.querySelector('.ws-trash-confirm-yes').onclick = () => purgeCanvas(c.id);
        card.querySelector('.ws-trash-confirm-no').onclick = () => card.classList.remove('confirming');
        trashListEl.appendChild(card);
    });
    refreshIcons();
}
async function restoreCanvas(id){
    try {
        const res = await canvasListApi().restoreCanvas(id);
        if(!res.ok) throw new Error('restore failed');
        deletedCanvases = deletedCanvases.filter(c => c.id !== id);
        await loadAll();           // restored canvas returns to its stored project
        renderTrash();
        setStatus(L('已恢复','Restored'));
    } catch(e){ console.error(e); setStatus(L('恢复失败','Restore failed')); }
}
async function purgeCanvas(id){
    try {
        const res = await canvasListApi().purgeCanvas(id);
        if(!res.ok) throw new Error('purge failed');
        deletedCanvases = deletedCanvases.filter(c => c.id !== id);
        renderTrash();
        const n = deletedCanvases.length;
        if (trashBadge) { trashBadge.textContent = String(n); trashBadge.classList.toggle('visible', n > 0); }
        setStatus(L('已彻底删除','Deleted'));
    } catch(e){ console.error(e); setStatus(L('删除失败','Delete failed')); }
}

/* ===== Event bindings ===== */
// The catalog no longer pans as an infinite world. Normal wheel events scroll
// the finite board; Shift+wheel is handled by onBoardWheel below.
board.addEventListener('wheel', onBoardWheel, { passive: false });
board.addEventListener('dblclick', e => {
    if(e.target.closest('.ws-card') || e.target.closest('.ws-create-card')) return;
    openCreateCard(boardCreateWorldPoint(), '', newCanvasBtn);
});

newCanvasBtn.addEventListener('click', e => {
    openCreateCard(boardCreateWorldPoint(), '', e.currentTarget);
});
emptyCreateCanvasBtn?.addEventListener('mousedown', e => e.stopPropagation());
emptyCreateCanvasBtn?.addEventListener('click', e => {
    e.stopPropagation();
    openCreateCard(boardCreateWorldPoint(), '', e.currentTarget);
});
pasteCanvasBtn?.addEventListener('click', pasteCanvas);

canvasOverviewSearchEl?.addEventListener('input', e => {
    overviewSearchTerm = String(e.target?.value || '');
    if(CANVAS_LIST_OVERVIEW) renderBoard();
});
canvasOverviewSortEl?.addEventListener('change', e => {
    const value = String(e.target?.value || 'updated');
    overviewSort = ['updated', 'created', 'name'].includes(value) ? value : 'updated';
    try { localStorage.setItem('canvas_overview_sort', overviewSort); } catch(err) {}
    if(CANVAS_LIST_OVERVIEW) renderBoard();
});
canvasOverviewDensityEl?.addEventListener('click', e => {
    const button = e.target.closest('[data-canvas-overview-density]');
    if(!button) return;
    const value = button.dataset.canvasOverviewDensity === 'compact' ? 'compact' : 'grid';
    overviewDensity = value;
    try { localStorage.setItem('canvas_overview_density', overviewDensity); } catch(err) {}
    syncOverviewDensity();
});
canvasOverviewRefreshBtn?.addEventListener('click', () => { void loadAll(); });

canvasFilterGroupEl?.addEventListener('click', e => {
    const btn = e.target.closest('[data-canvas-filter]');
    if(!btn) return;
    const filter = btn.dataset.canvasFilter;
    if(filter === currentCanvasFilter) return;
    currentCanvasFilter = filter;
    syncCanvasFilterControls();
    renderBoard();
    if(CANVAS_LIST_OVERVIEW){
        window.parent?.postMessage({ type: 'canvas-filter', filter }, location.origin);
    }
});

boardRefreshBtn?.addEventListener('click', () => { void loadAll(); });
boardResetViewBtn?.addEventListener('click', resetView);

// The finite canvas catalog creates canvases here. Project creation stays on
// the project-board page; the legacy DOM id remains for shell compatibility.
newProjectBtn.addEventListener('click', e => openCreateCard(boardCreateWorldPoint(), '', e.currentTarget));
newProjectConfirm.addEventListener('click', createProject);
newProjectCancel.addEventListener('click', closeNewProject);
newProjectInput.addEventListener('keydown', e => {
    if(e.key === 'Enter'){ e.preventDefault(); createProject(); }
    if(e.key === 'Escape'){ e.preventDefault(); closeNewProject(); }
});

trashEntryBtn?.addEventListener('click', () => {
    if(trashPanel?.classList.contains('active')) closeTrashView();
    else openTrashView();
});
trashCloseBtn?.addEventListener('click', closeTrashView);
trashBackdrop?.addEventListener('click', closeTrashView);
archiveEntryBtn?.addEventListener('click', () => {
    closeTrashView();
    if(archivePanel.classList.contains('active')) closeArchiveView();
    else openArchiveView();
});
archiveCloseBtn?.addEventListener('click', closeArchiveView);
archiveBackdrop?.addEventListener('click', closeArchiveView);

// close card menu, archive dialog and trash dialog when clicking outside
document.addEventListener('mousedown', e => {
    if(document.querySelector('.ws-card-pop') && !e.target.closest('.ws-card-pop') && !e.target.closest('.ws-card-menu')){
        closeCardMenu();
    }
    if(document.querySelector('.ws-card.confirming-delete') && !e.target.closest('.ws-card.confirming-delete')){
        boardWorld.querySelectorAll('.ws-card.confirming-delete').forEach(el => el.classList.remove('confirming-delete'));
    }
});
document.addEventListener('pointerdown', e => {
    if(archivePanel?.classList.contains('active')){
        if(!archivePanel.contains(e.target) && !archiveEntryBtn?.contains(e.target)){
            closeArchiveView();
        }
    }
    if(trashPanel?.classList.contains('active')){
        if(!trashPanel.contains(e.target) && !trashEntryBtn?.contains(e.target)){
            closeTrashView();
        }
    }
});

function canvasListNavigationOverlayOpen(){
    return Boolean(
        createCardEl
        || document.querySelector('.ws-card-pop')
        || document.querySelector('.ws-card.confirming-delete')
        || trashPanel?.classList.contains('active')
        || archivePanel?.classList.contains('active')
    );
}

document.addEventListener('keydown', e => {
    const editable = e.target instanceof HTMLInputElement
        || e.target instanceof HTMLTextAreaElement
        || e.target instanceof HTMLSelectElement
        || e.target?.isContentEditable;
    if(editable) return;
    if(e.key === 'Escape'){
        closeCardMenu();
        closeCreateCard();
        boardWorld.querySelectorAll('.ws-card.confirming-delete').forEach(el => el.classList.remove('confirming-delete'));
        if(trashPanel?.classList.contains('active')) closeTrashView();
        if(archivePanel.classList.contains('active')) closeArchiveView();
        return;
    }
    const isDotKey = e.key === '·' || e.key === '`' || e.code === 'Backquote';
    const dotShortcut = e.key === '·' && !e.isComposing && !e.ctrlKey && !e.metaKey && !e.altKey;
    if((dotShortcut || (isDotKey && !e.isComposing)) && !e.shiftKey && !e.ctrlKey && !e.metaKey && !e.altKey && !canvasListNavigationOverlayOpen() && selectedCanvasId){
        const canvas = canvases.find(item => item.id === selectedCanvasId);
        if(canvas){
            e.preventDefault();
            void openCanvas(canvas);
        }
    }
});

// language switch from parent (Workbench shell) via postMessage
window.addEventListener('message', event => {
    if(event.origin && event.origin !== location.origin) return;
    if(event.data?.type === 'studio-lang'){
        if(event.data.lang && window.StudioI18n) StudioI18n.set(event.data.lang);
        window.StudioI18n?.apply?.();
        renderProjects();
        renderBoard();
        if(trashPanel?.classList.contains('active')) renderTrash();
        if(archivePanel.classList.contains('active')) renderArchive();
        refreshIcons();
    }
    if(event.data?.type === 'canvas-filter' && CANVAS_LIST_OVERVIEW){
        const filter = ['all','classic','project','reference'].includes(event.data.filter) ? event.data.filter : 'all';
        currentCanvasFilter = filter;
        syncCanvasFilterControls();
        renderBoard();
    }
});

window.addEventListener('resize', scheduleBoardLayout, {passive:true});
if(typeof ResizeObserver === 'function' && board){
    const boardResizeObserver = new ResizeObserver(scheduleBoardLayout);
    boardResizeObserver.observe(board);
}

/* ===== Boot ===== */
window.StudioI18n?.apply?.();
applyViewport();
if(CANVAS_LIST_OVERVIEW){
    if(canvasOverviewSortEl) canvasOverviewSortEl.value = overviewSort;
    syncOverviewDensity();
    updateOverviewFooter('loading');
}
notifyCanvasLibrary();
loadAll();
refreshIcons();
