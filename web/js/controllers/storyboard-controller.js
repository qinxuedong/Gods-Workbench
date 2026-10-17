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
    scenes: [],
    shots: []
  };

  const esc = str => String(str ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));

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
      titleEl.textContent = `当前分镜场次 (共 ${state.shots.length} 个分镜)`;
    }

    const focused = grid.contains(document.activeElement) ? document.activeElement : null;
    const focusedShot = focused?.closest('[data-storyboard-shot]')?.dataset.storyboardShot;
    const focusedAction = focused?.dataset.shotAction;

    grid.innerHTML = state.shots.map((sh, idx) => `
      <div class="champagne-card p-2.5 flex flex-col justify-between group transition hover:border-[#dfc384]/50" data-storyboard-shot="${esc(sh.id)}">
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
            <img src="${esc(sh.img || '')}" class="w-full h-full object-cover group-hover:scale-105 transition duration-300" alt="${esc(sh.title || '')}">
            <div class="absolute top-1 right-1 px-1.5 py-0.5 rounded bg-black/70 gw-type-body-sm font-mono text-[#eddab3]">
              2.39:1
            </div>
          </div>

          <!-- 台词对白与叙事意图 -->
          <div class="gw-type-body-md font-bold text-slate-100 mb-1 truncate">${esc(sh.title || '')}</div>
          <div class="gw-type-body-sm text-slate-300 bg-black/40 p-1.5 rounded-lg border border-white/5 leading-relaxed font-body mb-2">
            ${esc(sh.dialogue || '')}
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
    `).join('');

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
        <span>${esc(s.title || '')}</span>
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
    if (!state.shots || state.shots.length === 0) {
      alert('分镜数据尚未接入（未纳入当前切片），无法追加镜头。');
      return;
    }
    const n = state.shots.length + 1;
    state.shots.push({
      id: `sb-${Date.now()}`,
      code: `SC_SH${n < 10 ? '0' + n : n}`,
      title: `追加镜头 #${n}`,
      shotType: '特写 · 50mm',
      dialogue: '补充过渡情节...',
      duration: '3.0s',
      img: '',
      status: 'not_integrated'
    });
    renderGrid();
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

  function init() {
    const params = new URLSearchParams(window.location.search);
    const pId = params.get('project_id') || localStorage.getItem('workspace_project_id') || '';
    state.projectId = pId;
    if (params.get('view') !== 'canvas') {
      renderNav();
      renderGrid();
    }
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }

  return {
    moveShot,
    regenerateShot,
    batchGenerate,
    addNewShot,
    exportPdf,
    setShots,
    setScenes,
    getState: () => ({ ...state }),
    renderNotIntegratedState,
    rebind: init
  };
})();
