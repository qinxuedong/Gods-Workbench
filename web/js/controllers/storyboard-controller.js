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

/**
 * Gods' Workbench - 分镜画布矩阵控制器 (storyboard-controller.js)
 */

window.WorkbenchStoryboard = (function () {
  'use strict';

  // 历史静态契约标识：内置演示预览与示例分镜已移除，运行时不保留示例分镜或回落数据。

  const state = {
    projectId: '',
    projectName: '项目数据未接入',
    scenes: [],
    shots: []
  };

  const esc = str => String(str ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));

  async function requestJson(url) {
    const response = await fetch(url, {credentials: 'same-origin'});
    if (!response || !response.ok) {
      const error = new Error(`storyboard request failed: ${response?.status || 'network'}`);
      error.status = response?.status || 0;
      throw error;
    }
    return response.json();
  }

  function resolveProjectId() {
    const params = new URLSearchParams(window.location.search);
    const fromUrl = String(params.get('project_id') || '').trim();
    const fromStorage = String(window.localStorage?.getItem('workspace_project_id') || '').trim();
    const projectId = fromUrl || fromStorage;
    if (projectId && fromUrl) window.localStorage?.setItem('workspace_project_id', projectId);
    return projectId;
  }

  function normalizeShot(asset) {
    if (!asset || typeof asset !== 'object') return null;
    const assetId = String(asset.asset_id || asset.id || '').trim();
    if (!assetId) return null;
    const metadata = asset.metadata && typeof asset.metadata === 'object' ? asset.metadata : {};
    const source = metadata.source_context && typeof metadata.source_context === 'object'
      ? metadata.source_context : {};
    const sceneId = String(asset.scene_id || source.scene_id || metadata.scene_id || '').trim();
    const name = String(asset.name || asset.display_name || asset.title || assetId).trim();
    const kind = String(asset.kind || asset.asset_type || metadata.media_type || '媒体资产').trim();
    const mediaType = String(asset.media_type || metadata.media_type || kind).trim().toLowerCase();
    const isImage = mediaType === 'image' || mediaType.startsWith('image/');
    const isVideo = mediaType === 'video' || mediaType.startsWith('video/');
    const directMediaUrl = String(asset.url || asset.media_url || '').trim();
    const imageUrl = (isImage || isVideo)
      ? (directMediaUrl || `/api/asset-registry/assets/${encodeURIComponent(assetId)}/media`)
      : '';
    const prompt = String(asset.prompt || metadata.prompt || '').trim();
    const status = String(asset.status || metadata.status || '已登记').trim();
    return {
      id: assetId,
      asset_id: assetId,
      scene_id: sceneId,
      name,
      title: name,
      imageUrl,
      img: imageUrl,
      previewType: isVideo ? 'video' : (isImage ? 'image' : ''),
      prompt,
      dialogue: prompt,
      shotType: kind,
      duration: asset.duration ?? metadata.duration ?? '',
      status
    };
  }

  function scenesFromShots(shots) {
    const grouped = new Map();
    shots.forEach(shot => {
      if (!shot.scene_id) return;
      const scene = grouped.get(shot.scene_id) || {
        id: shot.scene_id,
        title: shot.scene_id,
        shots: 0,
        active: grouped.size === 0
      };
      scene.shots += 1;
      grouped.set(shot.scene_id, scene);
    });
    return [...grouped.values()];
  }

  function updateProjectLinks() {
    document.querySelectorAll?.('a[href]').forEach(link => {
      const href = link.getAttribute('href');
      if (!href || !state.projectId || href.startsWith('http') || href.startsWith('#')) return;
      const url = new URL(href, window.location.origin);
      url.searchParams.set('project_id', state.projectId);
      link.setAttribute('href', `${url.pathname.split('/').pop()}${url.search}`);
    });
  }

  function notIntegratedMarkup(message) {
    return `<div class="col-span-full bay-inset p-8 text-center text-slate-500 font-mono gw-type-body-sm rounded-xl" data-gw-degradation="not_integrated"><i data-lucide="unlink" class="w-6 h-6 mx-auto mb-1.5 text-slate-600"></i><div>${esc(message)}</div></div>`;
  }

  function renderNotIntegratedState() {
    state.shots = [];
    state.scenes = [];
    renderNav();
    renderGrid();
  }

  function renderGrid() {
    const grid = document.getElementById('storyboardGrid');
    if (!grid) return;
    const titleEl = document.getElementById('storyboardTitle');

    if (!state.shots || state.shots.length === 0) {
      if (titleEl) titleEl.textContent = '分镜数据未接入 (共 0 个分镜)';
      grid.innerHTML = notIntegratedMarkup('分镜数据未接入');
      window.lucide?.createIcons();
      return;
    }

    if (titleEl) {
      const projectLabel = state.projectName && state.projectName !== '项目数据未接入'
        ? `${state.projectName} · ` : '';
      titleEl.textContent = `${projectLabel}当前分镜场次 (共 ${state.shots.length} 个分镜)`;
    }

    const focused = grid.contains(document.activeElement) ? document.activeElement : null;
    const focusedShot = focused?.closest('[data-storyboard-shot]')?.dataset.storyboardShot;
    const focusedAction = focused?.dataset.shotAction;

    grid.innerHTML = state.shots.map((sh, idx) => {
      const imageUrl = String(sh.imageUrl || sh.img || '').trim();
      const title = sh.name || sh.title || sh.id;
      const description = sh.prompt || sh.dialogue || '文本提示未接入';
      const preview = imageUrl && sh.previewType === 'video'
        ? `<video src="${esc(imageUrl)}" class="w-full h-full object-cover" controls preload="metadata" aria-label="${esc(title)}"></video>`
        : imageUrl
          ? `<img src="${esc(imageUrl)}" class="w-full h-full object-cover group-hover:scale-105 transition duration-300" alt="${esc(title)}" loading="lazy">`
        : `<div class="w-full h-full flex items-center justify-center text-slate-500 font-mono gw-type-body-sm" data-gw-degradation="not_integrated">媒体预览未接入</div>`;
      return `
      <div class="champagne-card p-2.5 flex flex-col justify-between group transition hover:border-[#dfc384]/50" data-storyboard-shot="${esc(sh.id)}" data-asset-id="${esc(sh.asset_id || '')}">
        <div>
          <!-- 头部编号与景别标签 -->
          <div class="flex items-center justify-between pb-1.5 mb-1.5 border-b border-white/5 gw-type-body-sm">
            <div class="flex items-center space-x-1.5">
              <span class="gw-type-body-sm font-mono px-1.5 py-0.5 rounded bg-black/60 border border-[#dfc384]/30 text-[#eddab3] font-bold">
                #${idx + 1} · ${esc(sh.code || '')}
              </span>
              <span class="gw-type-body-sm font-mono text-cyan-300">${esc(sh.shotType || '')}</span>
            </div>
            <div class="flex items-center space-x-1.5 gw-type-body-sm font-mono">
              <span class="text-slate-400">${esc(sh.duration || '')}</span>
              <span class="${sh.status === 'generating' ? 'text-amber-300 animate-pulse' : (sh.status === 'not_integrated' ? 'text-slate-400' : 'text-emerald-400')}"${sh.status === 'not_integrated' ? ' data-gw-degradation="not_integrated"' : ''}>
                ${sh.status === 'generating' ? '渲染中' : (sh.status === 'not_integrated' ? '未接入（无渲染端点）' : esc(sh.status || '已就绪'))}
              </span>
            </div>
          </div>

          <!-- 分镜构图画面 (16:9 画幅) -->
          <div class="aspect-video w-full rounded-lg overflow-hidden bg-black relative border border-white/10 mb-2">
            ${preview}
            <div class="absolute top-1 right-1 px-1.5 py-0.5 rounded bg-black/70 gw-type-body-sm font-mono text-[#eddab3]">
              2.39:1
            </div>
          </div>

          <!-- 台词对白与叙事意图 -->
          <div class="gw-type-body-md font-bold text-slate-100 mb-1 truncate">${esc(title)}</div>
          <div class="gw-type-body-sm text-slate-300 bg-black/40 p-1.5 rounded-lg border border-white/5 leading-relaxed font-body mb-2">
            ${esc(description)}
          </div>
        </div>

        <!-- 底部快捷操作条 -->
        <div class="pt-1.5 border-t border-white/5 flex items-center justify-between gw-type-body-sm font-mono">
          <div class="flex items-center space-x-2 text-slate-400 gw-type-body-sm">
            <button type="button" class="btn-hw-round" title="上移镜头" aria-label="上移镜头" data-shot-action="up" onclick="WorkbenchStoryboard.moveShot(${idx}, -1)"><span class="btn-hw-round-inner" aria-hidden="true">&uarr;</span></button>
            <button type="button" class="btn-hw-round" title="下移镜头" aria-label="下移镜头" data-shot-action="down" onclick="WorkbenchStoryboard.moveShot(${idx}, 1)"><span class="btn-hw-round-inner" aria-hidden="true">&darr;</span></button>
          </div>
          <div class="flex items-center space-x-1.5">
            <button class="hw-btn hw-btn-sm" data-shot-action="regenerate" onclick="WorkbenchStoryboard.regenerateShot(${idx})">重绘画面</button>
            <a href="production.html" class="hw-btn hw-btn-sm hw-btn-gold">进入制片</a>
          </div>
        </div>

      </div>
    `;
    }).join('');

    if (focusedShot && focusedAction) {
      const card = [...grid.querySelectorAll('[data-storyboard-shot]')].find(item => item.dataset.storyboardShot === focusedShot);
      [...(card?.querySelectorAll('[data-shot-action]') || [])].find(button => button.dataset.shotAction === focusedAction)?.focus();
    }
    window.lucide?.createIcons();
  }

  function renderNav() {
    const nav = document.getElementById('storyboardSceneNav');
    if (!nav) return;
    const navCount = document.querySelector('#storyboardSceneNavContainer .font-mono.text-\\[\\#dfc384\\]');

    if (!state.scenes || state.scenes.length === 0) {
      nav.innerHTML = `<div class="bay-inset p-3 text-center text-slate-500 font-mono gw-type-body-sm rounded-lg" data-gw-degradation="not_integrated"><i data-lucide="unlink" class="w-4 h-4 mx-auto mb-1 text-slate-600"></i><div>场次数据未接入</div></div>`;
      if (navCount) navCount.textContent = '未接入';
      window.lucide?.createIcons();
      return;
    }

    if (navCount) navCount.textContent = `共 ${state.scenes.length} 幕`;
    nav.innerHTML = state.scenes.map(s => `
      <button type="button" class="tree-node-card w-full text-left px-2.5 py-2 ${s.active ? 'active' : ''} flex items-center justify-between">
        <span>${esc(s.title || s.id || '')}</span>
        <span class="gw-type-body-sm font-mono px-1 rounded bg-black/60 text-[#dfc384]">${Number(s.shots || 0)}镜</span>
      </button>
    `).join('');
    window.lucide?.createIcons();
  }

  function moveShot(idx, dir) {
    const target = idx + dir;
    if (target < 0 || target >= state.shots.length) return;
    const temp = state.shots[idx];
    state.shots[idx] = state.shots[target];
    state.shots[target] = temp;
    renderGrid();
  }

  // 显式降级：本仓当前切片无分镜渲染端点，禁止用 setTimeout 假装生成完成。
  function regenerateShot(idx) {
    if (!state.shots[idx]) return;
    state.shots[idx].status = 'not_integrated';
    renderGrid();
    alert('分镜渲染端点尚未接入（未纳入当前切片），不会伪造生成结果。');
  }

  function batchGenerate() {
    state.shots.forEach(s => { s.status = 'not_integrated'; });
    renderGrid();
    alert('批量渲染端点尚未接入（未纳入当前切片），不会伪造生成结果。');
  }

  function addNewShot() {
    alert(state.shots.length === 0
      ? '分镜数据尚未接入（未纳入当前切片），无法追加镜头。'
      : '新增镜头端点尚未接入（未纳入当前切片），不会创建未持久化镜头。');
  }

  function exportPdf() {
    alert('分镜 PDF 导出端点尚未接入（未纳入当前切片），未生成任何文件。');
  }

  function setShots(shots) {
    state.shots = Array.isArray(shots) ? [...shots] : [];
    renderGrid();
  }

  function setScenes(scenes) {
    state.scenes = Array.isArray(scenes) ? [...scenes] : [];
    renderNav();
  }

  async function loadProjectContext(projectId = state.projectId) {
    if (!projectId) {
      renderNotIntegratedState();
      return null;
    }
    try {
      const projectPayload = await requestJson(`/api/projects/${encodeURIComponent(projectId)}`);
      const project = projectPayload?.project || projectPayload;
      if (!project || typeof project !== 'object') throw new Error('project unavailable');
      const assetsPayload = await requestJson(`/api/asset-registry/assets?project_id=${encodeURIComponent(projectId)}&limit=500`);
      const rawAssets = Array.isArray(assetsPayload?.assets) ? assetsPayload.assets
        : (Array.isArray(assetsPayload?.items) ? assetsPayload.items : []);
      const shots = rawAssets.map(normalizeShot).filter(Boolean);
      if (state.projectId !== projectId) return null;
      state.projectName = String(project.name || project.title || projectId);
      state.shots = shots;
      state.scenes = scenesFromShots(shots);
      renderNav();
      renderGrid();
      return {project, shots};
    } catch (_) {
      if (state.projectId === projectId) renderNotIntegratedState();
      return null;
    }
  }

  function init() {
    state.projectId = resolveProjectId();
    state.projectName = '项目数据未接入';
    renderNotIntegratedState();
    updateProjectLinks();
    if (new URLSearchParams(window.location.search).get('view') === 'canvas') return Promise.resolve(null);
    return loadProjectContext(state.projectId);
  }

  let ready;
  if (document.readyState === 'loading') {
    let resolveReady;
    let rejectReady;
    ready = new Promise((resolve, reject) => {
      resolveReady = resolve;
      rejectReady = reject;
    });
    document.addEventListener('DOMContentLoaded', () => {
      try {
        Promise.resolve(init()).then(resolveReady, rejectReady);
      } catch (error) {
        rejectReady(error);
      }
    });
  } else {
    ready = init();
  }

  return {
    moveShot,
    regenerateShot,
    batchGenerate,
    addNewShot,
    exportPdf,
    setShots,
    setScenes,
    loadProjectContext,
    getState: () => ({ ...state }),
    renderNotIntegratedState,
    get ready() { return ready; },
    rebind: () => { ready = init(); return ready; }
  };
})();
