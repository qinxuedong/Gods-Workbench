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

  // 1. 内置示例目录（demoProjectCatalog）：**未接入后端**，仅用于本地演示渲染。
  //    它不是真实项目数据，不得写入任何被当作真实数据的 state 字段；
  //    无后端时由渲染层显式降级（data-gw-degradation），绝不伪装成真实数据。
  const demoProjectCatalog = {
    'proj-01': {
      name: '《神谕之地》',
      episodes: [
        {
          id: 'ep-01',
          code: 'EP01',
          title: '《深空信标》',
          status: '制作中',
          totalShots: 12,
          scenes: [
            {
              id: 'sc-01',
              code: 'SC.01',
              name: '暴雨夜天台',
              status: '已完成',
              statusColor: 'emerald',
              shotsCount: 4,
              duration: '00:14.2',
              tags: '外景/冷雨',
              progress: 100,
              expanded: false,
              shots: [
                { id: 'sh-01-01', code: 'SH_01', type: '[全景]', desc: '暴雨冲刷霓虹楼顶', status: 'OK', color: 'emerald', focal: 35, img: 'https://images.unsplash.com/photo-1518709268805-4e9042af9f23?q=80&w=600&auto=format&fit=crop', prompt: 'Cinematic wide angle, cyberpunk city rooftop under torrential acid rain, dark atmosphere, volumetric amber lights.' },
                { id: 'sh-01-02', code: 'SH_02', type: '[中景]', desc: '风衣特工立于边缘', status: 'OK', color: 'emerald', focal: 50, img: 'https://images.unsplash.com/photo-1509198397868-475647b2a1e5?q=80&w=600&auto=format&fit=crop', prompt: 'Medium shot of cyberpunk agent standing at ledge, trenchcoat fluttering in gale, anamorphic flare.' },
                { id: 'sh-01-03', code: 'SH_03', type: '[特写]', desc: '战术目镜雨滴滑落', status: 'OK', color: 'emerald', focal: 85, img: 'https://images.unsplash.com/photo-1534447677768-be436bb09401?q=80&w=600&auto=format&fit=crop', prompt: 'Extreme close up of cybernetic eye lens, raindrops sliding across glass with HUD reflection.' },
                { id: 'sh-01-04', code: 'SH_04', type: '[仰拍]', desc: '巡逻浮空艇掠过天际', status: 'OK', color: 'emerald', focal: 35, img: 'https://images.unsplash.com/photo-1511447333015-45b65e60f6d5?q=80&w=600&auto=format&fit=crop', prompt: 'Low angle shot, heavy police VTOL dropship passing overhead with searchlight beam cutting through fog.' }
              ]
            },
            {
              id: 'sc-02',
              code: 'SC.02',
              name: '神经芯片实验室',
              status: '当前场次',
              statusColor: 'champagne',
              shotsCount: 4,
              duration: '00:18.5',
              tags: '内景/冷光',
              progress: 75,
              expanded: true,
              shots: [
                { id: 'sh-02-01', code: 'SH_01', type: '[全景]', desc: '实验室穹顶', status: 'OK', color: 'emerald', focal: 35, img: 'https://images.unsplash.com/photo-1509198397868-475647b2a1e5?q=80&w=600&auto=format&fit=crop', prompt: 'Wide shot of underground neural biotech laboratory dome, blue cryogenic pods, cables running along floor.' },
                { id: 'sh-02-02', code: 'SH_02', type: '[特写]', desc: '神经探针接入', status: 'EDIT', color: 'champagne', focal: 50, img: 'https://images.unsplash.com/photo-1518709268805-4e9042af9f23?q=80&w=1200&auto=format&fit=crop', prompt: 'Close up, glowing neural interface probe connecting into biomechanical socket at base of skull, spark effects, amber glow.' },
                { id: 'sh-02-03', code: 'SH_03', type: '[中景]', desc: '警报红光闪烁', status: 'SYNC', color: 'cyan', focal: 50, img: 'https://images.unsplash.com/photo-1511447333015-45b65e60f6d5?q=80&w=600&auto=format&fit=crop', prompt: 'Medium shot, warning sirens pulsing red in cleanroom, steam venting from coolant pipes.' },
                { id: 'sh-02-04', code: 'SH_04', type: '[特写]', desc: '机械眼对焦', status: 'WAIT', color: 'slate', focal: 85, img: 'https://images.unsplash.com/photo-1534447677768-be436bb09401?q=80&w=600&auto=format&fit=crop', prompt: 'Macro lens shot, bionic aperture blades iris dilating and focusing under intense fluorescent illumination.' }
              ]
            },
            {
              id: 'sc-03',
              code: 'SC.03',
              name: '气垫飞艇逃逸',
              status: '进行中',
              statusColor: 'cyan',
              shotsCount: 4,
              duration: '00:22.0',
              tags: '动态/追逐',
              progress: 20,
              expanded: false,
              shots: [
                { id: 'sh-03-01', code: 'SH_01', type: '[远景]', desc: '峡谷追逐序幕', status: 'WAIT', color: 'slate', focal: 35, img: 'https://images.unsplash.com/photo-1511447333015-45b65e60f6d5?q=80&w=600&auto=format&fit=crop', prompt: 'Extreme wide shot of canyon skiff chase across glowing electromagnetic desert.' }
              ]
            }
          ]
        },
        { id: 'ep-02', code: 'EP02', title: '《暗网倒影》', status: '排期中', totalShots: 10, scenes: [] },
        { id: 'ep-03', code: 'EP03', title: '《重力失控》', status: '台本中', totalShots: 8, scenes: [] },
        { id: 'ep-04', code: 'EP04', title: '《虚空神谕》', status: '规划中', totalShots: 14, scenes: [] }
      ]
    },
    'proj-02': {
      name: '《赛博修真：重构法则》',
      episodes: [
        {
          id: 'ep-01',
          code: 'EP01',
          title: '《灵脉超频》',
          status: '制作中',
          totalShots: 14,
          scenes: [
            {
              id: 'sc-01',
              code: 'SC.01',
              name: '赛博丹炉点火',
              status: '当前场次',
              statusColor: 'champagne',
              shotsCount: 3,
              duration: '00:15.0',
              tags: '内景/玄幻',
              progress: 60,
              expanded: true,
              shots: [
                { id: 'sh-01-01', code: 'SH_01', type: '[特写]', desc: '液金灵气符文流动', status: 'EDIT', color: 'champagne', focal: 50, img: 'https://images.unsplash.com/photo-1518709268805-4e9042af9f23?q=80&w=1200&auto=format&fit=crop', prompt: 'Cyberpunk alchemy furnace ignition, liquid quantum runes pulsing over titanium cauldron, glowing amber aura.' }
              ]
            }
          ]
        }
      ]
    },
    'proj-05': {
      name: '《霓虹脉冲 2099》',
      episodes: [
        {
          id: 'ep-01',
          code: 'EP01',
          title: '《义体觉醒》',
          status: '制作中',
          totalShots: 16,
          scenes: [
            {
              id: 'sc-01',
              code: 'SC.01',
              name: '贫民窟地下诊所',
              status: '当前场次',
              statusColor: 'champagne',
              shotsCount: 4,
              duration: '00:16.8',
              tags: '内景/暗调',
              progress: 80,
              expanded: true,
              shots: [
                { id: 'sh-01-01', code: 'SH_01', type: '[中景]', desc: '机械臂焊接断裂神经', status: 'OK', color: 'emerald', focal: 50, img: 'https://images.unsplash.com/photo-1518709268805-4e9042af9f23?q=80&w=1200&auto=format&fit=crop', prompt: 'Underground cybernetic clinic, robotic surgical arm welding damaged spinal neural implants, sparks.' }
              ]
            }
          ]
        }
      ]
    }
  };

  const state = {
    projectId: 'proj-01',
    projectName: '《神谕之地》',
    activeEpisodeId: 'ep-01',
    activeSceneId: 'sc-02',
    activeShotId: 'sh-02-02',
    currentEpisode: null,
    currentShot: {
      id: 'sh-02-02',
      name: 'SC.02 · SH_02 [特写] 神经探针接入',
      imageUrl: 'https://images.unsplash.com/photo-1518709268805-4e9042af9f23?q=80&w=1200&auto=format&fit=crop',
      focal: 50,
      prompt: 'Close up, glowing neural interface probe connecting into biomechanical socket at base of skull, spark effects, amber glow, cinematic lighting.'
    },
    shotList: [
      { id: 'sh-01', code: 'SH_01', type: '全景 WIDE', img: 'https://images.unsplash.com/photo-1509198397868-475647b2a1e5?q=80&w=300&auto=format&fit=crop', status: 'not_integrated' },
      { id: 'sh-02', code: 'SH_02', type: '中景 MED', img: 'https://images.unsplash.com/photo-1511447333015-45b65e60f6d5?q=80&w=300&auto=format&fit=crop', status: 'not_integrated' },
      { id: 'sh-03', code: 'SH_03', type: '特写 CLOSE', img: 'https://images.unsplash.com/photo-1534447677768-be436bb09401?q=80&w=300&auto=format&fit=crop', status: 'not_integrated' },
      { id: 'sh-04', code: 'SH_04', type: '微距 MACRO', img: 'https://images.unsplash.com/photo-1518709268805-4e9042af9f23?q=80&w=300&auto=format&fit=crop', status: 'active' },
      { id: 'sh-05', code: 'SH_05', type: '俯拍 TOP', img: 'https://images.unsplash.com/photo-1511447333015-45b65e60f6d5?q=80&w=300&auto=format&fit=crop', status: 'queued' },
      { id: 'sh-06', code: 'SH_06', type: '过肩 OTS', img: 'https://images.unsplash.com/photo-1618005182384-a83a8bd57fbe?q=80&w=300&auto=format&fit=crop', status: 'not_integrated' }
    ]
  };

  const esc = str => String(str ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));

  // 2. 初始化与路由解析
  function initRouting() {
    const params = new URLSearchParams(window.location.search);
    const pId = params.get('project_id') || localStorage.getItem('workspace_project_id') || 'proj-01';
    state.projectId = pId;
    localStorage.setItem('workspace_project_id', pId);

    // 获取对应工程元数据
    const proj = demoProjectCatalog[pId] || {
      name: localStorage.getItem('workspace_project_name') || pId,
      episodes: (demoProjectCatalog['proj-01'] && demoProjectCatalog['proj-01'].episodes) || [{ id: 'ep-01', title: '第一集', status: 'not_integrated', progress: 0 }]
    };
    state.projectName = proj.name;
    state.currentEpisode = (proj.episodes && proj.episodes[0]) || (demoProjectCatalog['proj-01'] && demoProjectCatalog['proj-01'].episodes[0]);
    state.activeEpisodeId = state.currentEpisode ? state.currentEpisode.id : 'ep-01';

    // 更新顶部标题
    const projTitleEl = document.getElementById('currentProjectDisplayTitle');
    if (projTitleEl) {
      projTitleEl.textContent = `${state.projectName} · 剧集制片工坊`;
    }

    // 更新页面导航栏上的链接动态设置 ?project_id
    document.querySelectorAll('#navPillsGroup a').forEach(a => {
      const href = a.getAttribute('href');
      if (href && !href.startsWith('http') && !href.startsWith('#')) {
        const url = new URL(href, window.location.origin);
        url.searchParams.set('project_id', state.projectId);
        a.setAttribute('href', url.pathname.split('/').pop() + url.search);
      }
    });
  }

  function switchProject(targetId) {
    if (!targetId || !demoProjectCatalog[targetId]) return;
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
    // 空目录守卫：内置示例目录为空时不得访问 undefined.id（原实现会抛错）。
    const keys = Object.keys(demoProjectCatalog);
    if (keys.length === 0) return;
    const idx = keys.indexOf(state.projectId);
    const prevIdx = idx <= 0 ? keys.length - 1 : idx - 1;
    switchProject(keys[prevIdx]);
  }

  function nextProject() {
    // 空目录守卫：同上。
    const keys = Object.keys(demoProjectCatalog);
    if (keys.length === 0) return;
    const idx = keys.indexOf(state.projectId);
    const nextIdx = (idx + 1) % keys.length;
    switchProject(keys[nextIdx]);
  }

  // 3. 渲染场次镜头目录 (Scene Catalog Deck)
  function renderSceneCatalog() {
    const listContainer = document.getElementById('sceneCatalogList');
    const tagEl = document.getElementById('catalogEpisodeTag');
    if (!listContainer || !state.currentEpisode) return;

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
                     onclick="event.stopPropagation(); WorkbenchProduction.selectCatalogShot('${sc.id}', '${sh.id}')">
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
          <div class="flex items-start justify-between">
            <div class="flex items-center space-x-1.5 min-w-0">
              <span class="gw-type-body-sm font-mono font-bold px-1.5 py-0.5 rounded bg-black/60 border border-white/10 text-slate-300 shrink-0">${esc(sc.code)}</span>
              <h4 class="gw-type-body-md font-bold text-slate-100 truncate">${esc(sc.name)}</h4>
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

          <!-- 进度微型推子：示例目录值**不是**真实渲染进度，统一显式未接入（不画任何宽度） -->
          <div class="mt-2">
            <div class="hw-fader-track-horizontal w-full h-1">
              <div class="hw-fader-glow-bar" style="width: 0%;"></div>
            </div>
            <div class="gw-type-body-sm font-mono text-amber-300 mt-0.5" data-gw-degradation="not_integrated" title="本切片无真实场次渲染进度数据源，示例目录数值不作为遥测">进度未接入</div>
          </div>

          ${shotsHtml}
        </div>
      `;
    }).join('');

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
    state.activeEpisodeId = epId;
    const proj = demoProjectCatalog[state.projectId] || demoProjectCatalog['proj-01'];
    const ep = proj.episodes.find(e => e.id === epId) || proj.episodes[0];
    state.currentEpisode = ep;

    if (btn && btn.parentElement) {
      btn.parentElement.querySelectorAll('button').forEach(b => {
        b.className = 'pill-capsule-inactive px-2.5 py-0.5 gw-type-body-sm font-mono text-slate-400 cursor-pointer ep-pill-btn';
      });
      btn.className = 'pill-capsule-active px-2.5 py-0.5 gw-type-body-sm font-mono cursor-pointer ep-pill-btn';
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
        { id: `sh-${Date.now()}-1`, code: 'SH_01', type: '[全景]', desc: '镜头起幅建立', status: 'WAIT', color: 'slate', focal: 35, img: 'https://images.unsplash.com/photo-1518709268805-4e9042af9f23?q=80&w=600&auto=format&fit=crop', prompt: 'New cinematic establishing shot.' }
      ]
    };
    state.currentEpisode.scenes.push(newScene);
    renderSceneCatalog();
  }

  function renderShots() {
    const track = document.getElementById('shotTrackList');
    if (!track) return;

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
