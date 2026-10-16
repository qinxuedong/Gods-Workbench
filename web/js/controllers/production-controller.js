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
 * Gods' Workbench - 剧集制片控制器 (production-controller.js)
 * 具备“剧集工作流总线”、“场次镜头目录”与“宽银幕视窗/调参推子台”三级联动，直通真实路由
 */

window.WorkbenchProduction = (function () {
  'use strict';

  // 历史静态契约标识：demoProjectCatalog 已移除，运行时不保留示例目录或回落数据。

  const state = {
    projectId: '',
    projectName: '项目数据未接入',
    activeEpisodeId: '',
    activeSceneId: '',
    activeShotId: '',
    currentEpisode: null,
    currentShot: {
      id: '',
      name: '镜头数据未接入',
      imageUrl: '',
      focal: 50,
      prompt: ''
    },
    shotList: []
  };

  const esc = str => String(str ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));

  function notIntegratedMarkup(message) {
    return `<div class="bay-inset p-4 text-center text-slate-500 font-mono gw-type-body-sm rounded-xl" data-gw-degradation="not_integrated"><i data-lucide="unlink" class="w-6 h-6 mx-auto mb-1.5 text-slate-600"></i><div>${esc(message)}</div></div>`;
  }

  function renderNotIntegratedState() {
    state.currentEpisode = null;
    state.activeEpisodeId = '';
    state.activeSceneId = '';
    state.activeShotId = '';
    state.currentShot = { id: '', name: '镜头数据未接入', imageUrl: '', focal: 50, prompt: '' };
    state.shotList = [];

    const title = document.getElementById('currentProjectDisplayTitle');
    if (title) title.textContent = '项目数据未接入 · 剧集制片工坊';

    const contextBus = document.querySelector('#contextBusContainer > .flex-1');
    if (contextBus) contextBus.innerHTML = notIntegratedMarkup('项目制片数据未接入');

    const episodePills = document.getElementById('catalogEpisodePills');
    if (episodePills) episodePills.innerHTML = notIntegratedMarkup('剧集数据未接入');

    const episodeTag = document.getElementById('catalogEpisodeTag');
    if (episodeTag) episodeTag.textContent = '未接入';

    const sceneList = document.getElementById('sceneCatalogList');
    if (sceneList) sceneList.innerHTML = notIntegratedMarkup('场次与镜头数据未接入');

    const shotTitle = document.getElementById('currentShotTitle');
    if (shotTitle) shotTitle.textContent = state.currentShot.name;

    const prompt = document.getElementById('shotPromptInput');
    if (prompt) prompt.value = '';

    const monitor = document.getElementById('mainMonitorImage');
    if (monitor) {
      monitor.removeAttribute('src');
      monitor.alt = '镜头预览未接入';
      monitor.style.display = 'none';
      monitor.setAttribute('data-gw-degradation', 'not_integrated');
      const monitorFrame = monitor.parentElement;
      if (monitorFrame && !monitorFrame.querySelector('[data-production-empty-state]')) {
        monitorFrame.insertAdjacentHTML('beforeend', `<div data-production-empty-state>${notIntegratedMarkup('镜头预览未接入')}</div>`);
      }
    }

    const addSceneButton = document.querySelector('button[aria-label="新建场次"]');
    if (addSceneButton) {
      addSceneButton.disabled = true;
      addSceneButton.setAttribute('aria-disabled', 'true');
    }

    const trackLabel = document.querySelector('#shotTrackList')?.previousElementSibling?.querySelector('span');
    if (trackLabel) trackLabel.textContent = '镜头轨道未接入';
  }

  // 2. 初始化与路由解析
  function initRouting() {
    const params = new URLSearchParams(window.location.search);
    const pId = params.get('project_id') || localStorage.getItem('workspace_project_id') || '';
    state.projectId = pId;
    if (pId) localStorage.setItem('workspace_project_id', pId);
    state.projectName = '项目数据未接入';
    renderNotIntegratedState();

    // 更新页面导航栏上的链接动态设置 ?project_id；没有上下文时不伪造参数。
    document.querySelectorAll('#navPillsGroup a').forEach(a => {
      const href = a.getAttribute('href');
      if (href && state.projectId && !href.startsWith('http') && !href.startsWith('#')) {
        const url = new URL(href, window.location.origin);
        url.searchParams.set('project_id', state.projectId);
        a.setAttribute('href', url.pathname.split('/').pop() + url.search);
      }
    });
  }

  function switchProject(targetId) {
    if (!targetId) return;
    state.projectId = targetId;
    localStorage.setItem('workspace_project_id', targetId);

    // 更新 URL
    const url = new URL(window.location.href);
    url.searchParams.set('project_id', targetId);
    window.history.pushState({}, '', url.toString());

    initRouting();
    renderSceneCatalog();
    renderShots();
    window.lucide?.createIcons();
  }

  function prevProject() {
    return;
  }

  function nextProject() {
    return;
  }

  // 侧栏与分段选择共用当前目录状态，避免只同步外观。
  function syncEpisodeControls() {
    document.querySelectorAll('[data-production-episode]').forEach(button => {
      const selected = button.dataset.productionEpisode === state.activeEpisodeId;
      button.setAttribute('aria-pressed', String(selected));
      if (button.classList.contains('ep-pill-btn')) {
        button.classList.toggle('engaged', selected);
      } else {
        button.classList.toggle('active', selected);
      }
    });
    document.querySelectorAll('[data-production-scene]').forEach(button => {
      const scene = state.currentEpisode?.scenes?.find(item => item.id === button.dataset.productionScene);
      const selected = scene?.id === state.activeSceneId;
      button.classList.toggle('active', selected);
      button.setAttribute('aria-pressed', String(selected));
      button.setAttribute('aria-expanded', String(Boolean(scene && scene.expanded !== false)));
    });
  }

  // 3. 渲染场次镜头目录 (Scene Catalog Deck)
  function renderSceneCatalog() {
    syncEpisodeControls();
    const listContainer = document.getElementById('sceneCatalogList');
    const tagEl = document.getElementById('catalogEpisodeTag');
    if (!listContainer) return;
    if (!state.currentEpisode) {
      if (tagEl) tagEl.textContent = '未接入';
      listContainer.innerHTML = notIntegratedMarkup('场次与镜头数据未接入');
      window.lucide?.createIcons();
      return;
    }
    const focused = listContainer.contains(document.activeElement) ? document.activeElement : null;
    const focusShot = focused?.dataset.productionCatalogShot;
    const focusScene = focused?.dataset.productionCatalogScene;

    if (tagEl) {
      const epShotCount = Number(state.currentEpisode.totalShots);
      tagEl.textContent = `${state.currentEpisode.code} · ` +
        (Number.isFinite(epShotCount) && state.currentEpisode.totalShots !== null
          ? `${epShotCount} 镜`
          : '镜头数未接入');
    }

    const scenes = state.currentEpisode.scenes || [];
    if (scenes.length === 0) {
      listContainer.innerHTML = `
        <div class="bay-inset p-4 text-center text-slate-500 font-mono gw-type-body-sm rounded-xl">
          <i data-lucide="layers" class="w-6 h-6 mx-auto mb-1.5 text-slate-600"></i>
          <div>本剧集暂未配置场次</div>
        </div>
      `;
      window.lucide?.createIcons();
      return;
    }

    listContainer.innerHTML = scenes.map(sc => {
      const isCurrent = sc.id === state.activeSceneId;
      const isExpanded = sc.expanded !== false;

      // 状态徽章颜色与样式
      let statusBadgeClass = 'bg-white/10 text-slate-400 border border-white/10';
      if (sc.statusColor === 'champagne' || isCurrent) {
        statusBadgeClass = 'bg-[#dfc384]/20 text-[#dfc384] border border-[#dfc384]/40 font-bold';
      } else if (sc.statusColor === 'emerald') {
        statusBadgeClass = 'bg-emerald-500/15 text-emerald-400 border border-emerald-500/30';
      } else if (sc.statusColor === 'cyan') {
        statusBadgeClass = 'bg-cyan-500/15 text-cyan-400 border border-cyan-500/30';
      }

      // 场次内部镜头条目
      let shotsHtml = '';
      if (isExpanded && sc.shots && sc.shots.length > 0) {
        shotsHtml = `
          <div class="mt-2.5 pt-2 border-t border-white/5 space-y-1.5">
            ${sc.shots.map(sh => {
              const isShotActive = sh.id === state.activeShotId;
              let badgeColor = 'bg-slate-800 text-slate-400 border-slate-700';
              if (sh.status === 'OK') badgeColor = 'bg-emerald-500/20 text-emerald-400 border-emerald-500/40';
              else if (sh.status === 'EDIT') badgeColor = 'bg-[#dfc384]/25 text-[#dfc384] border-[#dfc384]/50 font-bold';
              else if (sh.status === 'SYNC') badgeColor = 'bg-cyan-500/20 text-cyan-300 border-cyan-500/40';
              else if (sh.status === 'WAIT') badgeColor = 'bg-slate-800 text-slate-500 border-slate-700';

              return `
                <div class="shot-row-item group flex items-center justify-between px-2 py-1.5 rounded-lg cursor-pointer transition ${isShotActive ? 'bg-[#dfc384]/15 border border-[#dfc384]/50 shadow-[0_0_8px_rgba(223,195,132,0.15)]' : 'bg-black/40 hover:bg-white/5 border border-white/5'}"
                     role="button" tabindex="0" data-production-catalog-shot="${esc(sh.id)}" aria-pressed="${isShotActive}" onkeydown="if(event.target===this&&(event.key==='Enter'||event.key===' ')){event.preventDefault();event.stopPropagation();this.click()}" onclick="event.stopPropagation(); WorkbenchProduction.selectCatalogShot('${sc.id}', '${sh.id}')">
                  <div class="flex items-center space-x-2 min-w-0">
                    <span class="gw-type-body-sm font-mono font-bold ${isShotActive ? 'text-[#dfc384]' : 'text-slate-400'} shrink-0">${esc(sh.code)}</span>
                    <span class="gw-type-body-sm text-slate-300 truncate">${esc(sh.type)} ${esc(sh.desc)}</span>
                  </div>
                  <span class="gw-type-body-sm font-mono px-1.5 py-0.5 rounded border shrink-0 ${badgeColor}">${esc(sh.status)}</span>
                </div>
              `;
            }).join('')}
          </div>
        `;
      }

      return `
        <!-- 场次卡片: ${sc.code} -->
        <div class="champagne-card p-2.5 transition relative cursor-pointer group ${isCurrent ? 'border-[#dfc384]/55 shadow-[0_0_12px_rgba(223,195,132,0.12)]' : 'border-white/5 hover:border-white/20'}"
             onclick="WorkbenchProduction.toggleSceneCard('${sc.id}')">
          <div class="flex items-start justify-between" role="button" tabindex="0" data-production-catalog-scene="${esc(sc.id)}" aria-pressed="${isCurrent}" aria-expanded="${isExpanded}" onkeydown="if(event.target===this&&(event.key==='Enter'||event.key===' ')){event.preventDefault();this.click()}">
            <div class="flex items-center space-x-1.5 min-w-0">
              <span class="gw-type-body-sm font-mono font-bold px-1.5 py-0.5 rounded bg-black/60 border border-white/10 text-slate-300 shrink-0">${esc(sc.code)}</span>
              <h4 class="gw-type-section text-slate-100 truncate">${esc(sc.name)}</h4>
            </div>
            <span class="gw-type-body-sm font-mono px-1.5 py-0.5 rounded shrink-0 ${statusBadgeClass}">${esc(sc.status)}</span>
          </div>

          <!-- 场次属性元数据 -->
          <div class="flex items-center space-x-3 mt-1.5 gw-type-body-sm font-mono text-slate-400">
            <span>${Number.isFinite(Number(sc.shotsCount)) && sc.shotsCount !== null && sc.shotsCount !== '' ? `${Number(sc.shotsCount)} 镜头` : '镜头数未接入'}</span>
            <span>·</span>
            <span>${(sc.duration === null || sc.duration === undefined || sc.duration === '') ? '时长未接入' : sc.duration}</span>
            <span>·</span>
            <span class="text-slate-300">${sc.tags || '常规场景'}</span>
          </div>

          <!-- 进度微型推子：真实渲染进度未接入，统一显式未接入（不画任何宽度） -->
          <div class="mt-2">
            <div class="hw-fader-track-horizontal w-full h-1">
              <div class="hw-fader-glow-bar" style="width: 0%;"></div>
            </div>
            <div class="gw-type-body-sm font-mono text-amber-300 mt-0.5" data-gw-degradation="not_integrated" title="本切片无真实场次渲染进度数据源">进度未接入</div>
          </div>

          ${shotsHtml}
        </div>
      `;
    }).join('');

    if (focusShot || focusScene) {
      [...listContainer.querySelectorAll('[data-production-catalog-shot], [data-production-catalog-scene]')]
        .find(button => focusShot ? button.dataset.productionCatalogShot === focusShot : button.dataset.productionCatalogScene === focusScene)?.focus();
    }
    window.lucide?.createIcons();
  }

  // 4. 切换场次卡片展开/激活
  function toggleSceneCard(scId) {
    if (!state.currentEpisode || !state.currentEpisode.scenes) return;
    state.currentEpisode.scenes.forEach(sc => {
      if (sc.id === scId) {
        sc.expanded = !sc.expanded;
        state.activeSceneId = sc.id;
        // 如果内部有镜头，默认激活其第一个
        if (sc.shots && sc.shots.length > 0) {
          selectCatalogShot(sc.id, sc.shots[0].id);
        }
      }
    });
    renderSceneCatalog();
  }

  // 5. 点击选中某个具体镜头，联动更新主视窗与右侧提示词/推子
  function selectCatalogShot(scId, shId) {
    state.activeSceneId = scId;
    state.activeShotId = shId;

    let targetShot = null;
    let targetScene = null;

    if (state.currentEpisode && state.currentEpisode.scenes) {
      targetScene = state.currentEpisode.scenes.find(s => s.id === scId);
      if (targetScene && targetScene.shots) {
        targetShot = targetScene.shots.find(sh => sh.id === shId);
      }
    }

    if (targetShot) {
      state.currentShot = {
        id: targetShot.id,
        name: `${targetScene.code} · ${targetShot.code} ${targetShot.type} ${targetShot.desc}`,
        imageUrl: targetShot.img,
        focal: targetShot.focal || 50,
        prompt: targetShot.prompt || ''
      };

      // 更新主监视器
      const titleEl = document.getElementById('currentShotTitle');
      if (titleEl) titleEl.textContent = state.currentShot.name;

      const imgEl = document.getElementById('mainMonitorImage');
      if (imgEl) imgEl.src = targetShot.img;

      // 更新提示词
      const promptInput = document.getElementById('shotPromptInput');
      if (promptInput && targetShot.prompt) promptInput.value = targetShot.prompt;

      // 视窗变焦与重置
      setFocal(state.currentShot.focal);
    }

    renderSceneCatalog();
  }

  // 6. 剧集分段标签切换 (EP01, EP02, EP03, EP04)
  function selectEpisodeTab(epId, btn) {
    if (!state.currentEpisode || !Array.isArray(state.currentEpisode.episodes)) return;
    state.activeEpisodeId = epId;
    const ep = state.currentEpisode.episodes.find(e => e.id === epId) || state.currentEpisode.episodes[0];
    state.currentEpisode = ep;

    if (btn && btn.parentElement) {
      btn.parentElement.querySelectorAll('button').forEach(b => {
        b.className = 'hw-btn btn-size-sm font-mono cursor-pointer ep-pill-btn';
        b.setAttribute('aria-pressed', 'false');
      });
      btn.className = 'hw-btn btn-size-sm engaged font-mono cursor-pointer ep-pill-btn';
      btn.setAttribute('aria-pressed', 'true');
    }

    if (ep.scenes && ep.scenes.length > 0) {
      state.activeSceneId = ep.scenes[0].id;
      if (ep.scenes[0].shots && ep.scenes[0].shots.length > 0) {
        selectCatalogShot(ep.scenes[0].id, ep.scenes[0].shots[0].id);
      }
    }

    renderSceneCatalog();
  }

  // 7. 监视器焦距推子控制
  function setFocal(mm) {
    state.currentShot.focal = mm;
    document.querySelectorAll('[data-production-focal]').forEach(button => {
      button.setAttribute('aria-pressed', String(Number(button.dataset.productionFocal) === mm));
    });
    const imgEl = document.getElementById('mainMonitorImage');
    if (imgEl) {
      imgEl.style.transform = mm === 85 ? 'scale(1.15)' : (mm === 35 ? 'scale(0.95)' : 'scale(1)');
      imgEl.style.transition = 'transform 0.4s cubic-bezier(0.16, 1, 0.3, 1)';
    }
  }

  // 8. 触发渲染生成
  //    注意：本切片**未接入任何渲染端点**。此处仅保留视觉降级演示，
  //    必须先显式告知用户「未接入」，不得让用户以为镜头已经真的生成完成。
  function triggerGenerate() {
    window.alert('渲染未接入：后端未提供镜头渲染端点，未生成任何镜头，也未伪造渲染结果。');
    const imgEl = document.getElementById('mainMonitorImage');
    if (imgEl) {
      imgEl.style.filter = 'brightness(0.65) blur(1.5px)';
      setTimeout(() => {
        imgEl.style.filter = 'none';
      }, 1000);
    }

    if (window.HardwareDeck) {
      HardwareDeck.setVUMeter('cpu', 82);
      HardwareDeck.setVUMeter('ram', 90);
      setTimeout(() => {
        HardwareDeck.setVUMeter('cpu', 48);
        HardwareDeck.setVUMeter('ram', 65);
      }, 2000);
    }
  }

  // 9. 添加新场次
  function addScene() {
    if (!state.currentEpisode) return;
    if (!state.currentEpisode.scenes) state.currentEpisode.scenes = [];
    const idx = state.currentEpisode.scenes.length + 1;
    const code = `SC.${idx < 10 ? '0' + idx : idx}`;
    const newScene = {
      id: `sc-${Date.now()}`,
      code: code,
      name: `${code} 新分镜场次`,
      status: '待编排',
      statusColor: 'slate',
      shotsCount: 1,
      duration: '00:10.0',
      tags: '新分镜',
      progress: 0,
      expanded: true,
      shots: [
        { id: `sh-${Date.now()}-1`, code: 'SH_01', type: '[全景]', desc: '', status: 'WAIT', color: 'slate', focal: 35, img: '', prompt: '' }
      ]
    };
    state.currentEpisode.scenes.push(newScene);
    renderSceneCatalog();
  }

  function renderShots() {
    const track = document.getElementById('shotTrackList');
    if (!track) return;
    if (!state.shotList.length) {
      track.innerHTML = notIntegratedMarkup('镜头数据未接入');
      window.lucide?.createIcons();
      return;
    }

    track.innerHTML = state.shotList.map(sh => {
      const isActive = sh.status === 'active';
      return `
        <div class="bay-inset p-1 rounded-lg cursor-pointer transition ${isActive ? 'border-2 border-[#dfc384]' : 'border border-white/5 hover:border-white/20'}"
             onclick="WorkbenchProduction.selectShot('${sh.id}')">
          <div class="h-12 w-full rounded overflow-hidden mb-1 relative bg-black">
            <img src="${sh.img}" class="w-full h-full object-cover">
            <span class="absolute bottom-0.5 right-0.5 gw-type-body-sm font-mono px-1 rounded bg-black/80 text-[#eddab3]">${sh.type}</span>
          </div>
          <div class="flex items-center justify-between gw-type-body-sm font-mono">
            <span class="${isActive ? 'text-[#dfc384] font-bold' : 'text-slate-400'}">${sh.code}</span>
            <span class="${sh.status === 'queued' ? 'text-amber-300' : 'text-slate-400'}"${sh.status === 'not_integrated' ? ' data-gw-degradation="not_integrated"' : ''}>${sh.status === 'queued' ? '排队' : (sh.status === 'not_integrated' ? '未接入' : sh.status)}</span>
          </div>
        </div>
      `;
    }).join('');
  }

  function selectShot(shId) {
    state.shotList.forEach(sh => {
      if (sh.id === shId) {
        sh.status = 'active';
        state.currentShot.id = sh.id;
        state.currentShot.name = `SC_03 · ${sh.code} [当前选定镜头]`;
        state.currentShot.imageUrl = sh.img;

        const titleEl = document.getElementById('currentShotTitle');
        if (titleEl) titleEl.textContent = state.currentShot.name;
        const imgEl = document.getElementById('mainMonitorImage');
        if (imgEl) imgEl.src = sh.img;
      } else if (sh.status === 'active') {
        // 取消选中后回写为「未接入」，不得伪造「就绪」。
        sh.status = 'not_integrated';
      }
    });
    renderShots();
  }

  function selectEpisode(epId, el) {
    if (el && el.parentElement && el.parentElement.parentElement) {
      el.parentElement.parentElement.querySelectorAll('.tree-node-card').forEach(card => card.classList.remove('active'));
      el.classList.add('active');
    }
    selectEpisodeTab(epId);
  }

  function reloadBus() {
    initRouting();
    renderSceneCatalog();
    renderShots();
    window.lucide?.createIcons();
  }

  function init() {
    initRouting();
    renderSceneCatalog();
    renderShots();
    setFocal(state.currentShot.focal);
    window.lucide?.createIcons();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }

  return {
    initRouting,
    renderSceneCatalog,
    selectEpisodeTab,
    toggleSceneCard,
    selectCatalogShot,
    selectEpisode,
    selectScene: toggleSceneCard,
    selectShot,
    reloadBus,
    setFocal,
    triggerGenerate,
    regenerateCurrentShot: triggerGenerate,
    addScene,
    switchProject,
    prevProject,
    nextProject,
    rebind: reloadBus
  };
})();
