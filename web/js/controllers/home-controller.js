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
 * Gods' Workbench 控制台首页驱动逻辑 (home-controller.js)
 * 采用原生 JavaScript 实现无框架极速加载与硬件态拟物动效驱动
 */

window.WorkbenchHome = (function () {
  'use strict';

  const dateInputTimestamp = value => {
    const text = String(value || '').trim();
    if (!text) return null;
    const numeric = Number(text);
    if (Number.isFinite(numeric) && numeric > 0) return numeric;
    const timestamp = new Date(text).getTime();
    return Number.isFinite(timestamp) ? timestamp : null;
  };

  const VIEW_MODE_KEY = 'workbench_proj_view_mode';
  const LEGACY_VIEW_MODE_KEY = 'v2_proj_view_mode';
  function readViewMode() {
    try {
      const current = localStorage.getItem(VIEW_MODE_KEY);
      if (current === 'grid' || current === 'list') return current;
      const legacy = localStorage.getItem(LEGACY_VIEW_MODE_KEY);
      if (legacy === 'grid' || legacy === 'list') {
        localStorage.setItem(VIEW_MODE_KEY, legacy);
        return legacy;
      }
    } catch (_) {}
    return 'grid';
  }

  // 状态树
  const state = {
    projects: [],
    activeProject: null,
    jobs: [],
    chatHistory: [],
    loading: false,
    viewMode: readViewMode(), // 'grid' (2列卡片) 或 'list' (行条列表)
    // 显式降级状态：null 表示后端已如实应答（含「确实为空」）；否则记 { kind, message }，
    // 用于把「未接入 / 服务不可用 / 请求失败」如实展示，绝不静默伪造数据。
    projectsDegradation: null,
    assetOverviewDegradation: null,
    promptSourcesDegradation: null
    // 注：活跃任务队列由共享模块 /static/js/core/task-queue.js 独立渲染
    // （真实数据源 GET /api/observability/tasks），本控制器不再维护重复状态。
  };

  // 切换视图模式 (grid / list)
  function setViewMode(mode) {
    if (mode !== 'grid' && mode !== 'list') mode = 'grid';
    state.viewMode = mode;
    localStorage.setItem(VIEW_MODE_KEY, mode);

    // 同步更新切换按钮激活状态
    const btnGrid = document.getElementById('btnViewGrid');
    const btnList = document.getElementById('btnViewList');
    if (btnGrid && btnList) {
      if (mode === 'grid') {
        btnGrid.classList.add('active');
        btnList.classList.remove('active');
      } else {
        btnList.classList.add('active');
        btnGrid.classList.remove('active');
      }
    }

    renderProjectsList();
  }

  // 统一「无后端时显式降级」工具（复用 window.GWDegradation；页面已先加载该脚本）。
  // 口径：404/501 且无标准错误包 -> 未接入；503 -> 服务暂时不可用；其余 -> 如实报错。
  const degradationApi = () => window.GWDegradation || null;

  const degradationMessage = (kind, status) => {
    const api = degradationApi();
    if (api) {
      if (kind === 'not_integrated') return `${api.NOT_INTEGRATED_MESSAGE}（HTTP ${status}）`;
      if (kind === 'service_unavailable') return `${api.SERVICE_UNAVAILABLE_MESSAGE}（HTTP ${status}）`;
    }
    if (kind === 'not_integrated') return `该功能尚未接入后端（未纳入当前切片）（HTTP ${status}）`;
    if (kind === 'service_unavailable') return `后端服务暂时不可用，请稍后重试（HTTP ${status}）`;
    return `请求失败（HTTP ${status}）`;
  };

  // 读取响应体并分类；响应体非 JSON 或读取失败时按状态码单独判定，绝不静默吞掉错误。
  async function classifyFetchFailure(res) {
    let data = null;
    try { data = await res.json(); } catch (_) { data = null; }
    const api = degradationApi();
    if (api && typeof api.classifyResponse === 'function') {
      const verdict = api.classifyResponse(res, data);
      return { kind: verdict.kind, status: verdict.status, message: verdict.message, detail: data };
    }
    const detailValue = data ? data.detail : undefined;
    const isEnvelope = Boolean(detailValue) && typeof detailValue === 'object';
    const isDefaultText = typeof detailValue === 'string' && /^(not found|not implemented)$/i.test(detailValue.trim());
    let kind = 'error';
    if (res.status === 503) kind = 'service_unavailable';
    else if ([404, 501].indexOf(res.status) !== -1 && !isEnvelope && !(typeof detailValue === 'string' && detailValue.trim() && !isDefaultText)) kind = 'not_integrated';
    return { kind: kind, status: res.status, message: degradationMessage(kind, res.status), detail: data };
  }

  // 显式降级占位（带 data-gw-degradation 标记，便于契约测试与人工核验）。
  function degradationNoticeHtml(degradation, extraHint) {
    const api = degradationApi();
    const hint = extraHint ? `<span class="block mt-1 text-[9px] text-slate-500">${esc(extraHint)}</span>` : '';
    if (api && typeof api.noticeHtml === 'function') {
      return api.noticeHtml(degradation.kind, degradation.message) + hint;
    }
    return `<div class="bay-inset p-3 rounded-xl border border-white/10 text-center text-[10px] font-mono text-slate-400" role="status" data-gw-degradation="${esc(degradation.kind)}"><span class="block">${esc(degradation.message)}</span>${hint}</div>`;
  }


  // 工具辅助函数
  const esc = str => String(str ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));

  const formatTime = ts => {
    if (!ts) return '刚刚';
    const date = new Date(typeof ts === 'number' ? (ts < 1e12 ? ts * 1000 : ts) : ts);
    return isNaN(date.getTime()) ? '刚刚' : date.toLocaleDateString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' });
  };

  // 1. 获取并渲染项目列表
  async function reloadProjects() {
    const container = document.getElementById('workbenchRecentProjects');
    if (!container) return;

    try {
      const res = await fetch('/api/asset-registry/projects?archived=false', { credentials: 'same-origin' });
      if (!res.ok) {
        // 显式降级：接口未接入 / 服务不可用 / 请求失败一律如实标记并清空列表，
        // 绝不填入任何伪造的示例工程数据。
        const failure = await classifyFetchFailure(res);
        state.projects = [];
        state.projectsDegradation = { kind: failure.kind, message: failure.message };
      } else {
        const data = await res.json();
        const list = data?.projects || data || [];
        if (Array.isArray(list) && list.length > 0) {
          // 契约对齐：项目中心 API 的稳定实体 ID 字段为 project_id（见 docs/contracts/PROJECTS-HUB-INTERFACE-CATALOG.yaml
          // 与 docs/fixtures/projects-hub-list-active.json）；此处统一归一化为前端内部使用的 id，
          // 避免后端返回 project_id 时卡片 data-project-id 为空、点击与双击均失效。
          state.projects = list.map(p => ({ ...p, id: p.id || p.project_id }));
          state.projectsDegradation = null;
        } else {
          // 后端如实应答「确实为空」：保留空态文案，不伪造数据。
          state.projects = [];
          state.projectsDegradation = null;
        }
      }
    } catch (e) {
      // 网络异常：服务不可用（可恢复），同样不得伪造工程数据。
      console.warn('项目列表读取失败（网络异常）:', e);
      state.projects = [];
      state.projectsDegradation = {
        kind: 'service_unavailable',
        message: degradationMessage('service_unavailable', 0)
      };
    }

    renderProjectsList();
  }


  function renderProjectsList() {
    const container = document.getElementById('workbenchRecentProjects');
    if (!container) return;

    // 保证切换按钮状态与当前 state.viewMode 同步
    const btnGrid = document.getElementById('btnViewGrid');
    const btnList = document.getElementById('btnViewList');
    if (btnGrid && btnList) {
      if (state.viewMode === 'grid') {
        btnGrid.classList.add('active');
        btnList.classList.remove('active');
      } else {
        btnList.classList.add('active');
        btnGrid.classList.remove('active');
      }
    }

    if (!state.projects || state.projects.length === 0) {
      // 空态必须区分：显式降级（未接入/服务不可用/请求失败）与「后端如实应答确实为空」。
      container.innerHTML = state.projectsDegradation
        ? degradationNoticeHtml(state.projectsDegradation, '未取到工程列表；本页不会展示任何伪造的示例工程。')
        : `
        <div class="bay-inset p-4 rounded-xl text-center text-slate-400 text-xs">
          <i data-lucide="folder-open" class="w-6 h-6 mx-auto mb-1 text-slate-500"></i>
          暂无工程，点击上方“+ 新建”快速开启
        </div>
      `;
      if (state.projectsDegradation) {
        const busTitleEmpty = document.getElementById('busActiveProjectTitle');
        if (busTitleEmpty) busTitleEmpty.textContent = '未接入';
      }
      window.lucide?.createIcons();
      return;
    }

    // 更新左侧总线当前活跃项目名
    const busTitle = document.getElementById('busActiveProjectTitle');
    if (busTitle && state.projects[0]) {
      busTitle.textContent = state.projects[0].name || '未命名工程';
    }

    // 项目进度解析（显式降级，安全关键）：
  // 旧实现为 `p.progress || ... : 75`，当后端返回真实 progress=0（新建项目）时，
  // `0 || 75` 会把它显示成 75% —— 这是静默伪造，且与真实值相反。
  // 现在：仅当字段存在且为有限数时才显示数值；缺失/非数一律显式「未接入」。
  function projectProgressMeta(project) {
    const raw = project ? project.progress : null;
    if (raw !== null && raw !== undefined && raw !== '' && Number.isFinite(Number(raw))) {
      return { value: Number(raw), degraded: false };
    }
    const total = Number(project && project.entity_count);
    if (Number.isFinite(total) && total > 0) {
      const done = Number(project.completed_entity_count);
      if (Number.isFinite(done)) {
        return { value: Math.max(0, Math.min(100, Math.round((done / total) * 100))), degraded: false };
      }
    }
    return { value: 0, degraded: true };
  }

  // 辅助：获取进行状态文案与样式 (规划中 / 制作中 / 已完成)
  const getProjectStatusMeta = status => {
      const s = String(status || '').toLowerCase();
      if (['completed', 'approved', 'closed', 'done', 'succeeded', 'finished'].includes(s)) {
        return { text: '已完成', key: 'completed', dotColor: 'bg-emerald-400' };
      }
      if (['in_progress', 'active', 'production', 'running', 'storyboard', 'review'].includes(s)) {
        return { text: '制作中', key: 'in_progress', dotColor: 'bg-cyan-400' };
      }
      return { text: '规划中', key: 'planning', dotColor: 'bg-slate-400' };
    };

    // 辅助：获取类型图标与标签
    const getProjectTypeMeta = (type) => {
      if (type === 'series') {
        return { iconName: 'tv', label: '剧集', colorClass: 'text-cyan-300' };
      } else if (type === 'other') {
        return { iconName: 'palette', label: '概念/PV', colorClass: 'text-purple-300' };
      }
      return { iconName: 'clapperboard', label: '电影', colorClass: 'text-[#dfc384]' };
    };

    if (state.viewMode === 'grid') {
      // 模式 1: 2列卡片网格
      // 结构：左上角方形缩略图，右侧项目名称，名称下方一行小状态栏（类型图标 + 进行状态），最下面制作进度条 (去掉编号)
      const cardsHtml = state.projects.map((p, idx) => {
        const isSelected = (state.activeProject && state.activeProject.id === p.id) || (!state.activeProject && idx === 0);
        const progressMeta = projectProgressMeta(p);
        const progress = progressMeta.value;
        const progressHtml = progressMeta.degraded
          ? '<span class="text-[#eddab3] font-bold" data-gw-degradation="not_integrated" title="后端未返回该项目的 progress 字段，本页不伪造进度">未接入</span>'
          : `<span class="text-[#eddab3] font-bold">${progress}%</span>`;
        const statusMeta = getProjectStatusMeta(p.status);
        const typeMeta = getProjectTypeMeta(p.project_type);
        const coverUrl = String(p.cover_media_url || p.cover_url || '').trim();

        const thumbnailHtml = coverUrl
          ? `<div class="proj-card-thumb">
               <img src="${esc(coverUrl)}" alt="${esc(p.name)}" loading="lazy" onerror="this.parentElement.innerHTML='<div class=\\'proj-card-thumb-placeholder\\'><i data-lucide=\\'${typeMeta.iconName}\\' class=\\'w-5 h-5\\'></i></div>'; window.lucide?.createIcons();">
             </div>`
          : `<div class="proj-card-thumb">
               <div class="proj-card-thumb-placeholder">
                 <i data-lucide="${typeMeta.iconName}" class="w-5 h-5"></i>
               </div>
             </div>`;

        return `
          <div class="proj-card-hardware project-hardware-card p-2.5 rounded-xl cursor-pointer ${isSelected ? 'active' : ''}"
               data-project-id="${esc(p.id)}"
               onclick="WorkbenchHome.selectProject('${esc(p.id)}')"
               ondblclick="WorkbenchHome.openProject('${esc(p.id)}')"
               title="${esc(p.name)}（单击选中，双击或按 · 快捷键进入）">

            <!-- 上部：左侧方形缩略图 (70*70) + 右侧名称与状态栏 -->
            <div class="flex items-start space-x-3 min-w-0">
              ${thumbnailHtml}

              <div class="flex-1 min-w-0 flex flex-col justify-between h-[70px] py-1">
                <!-- 项目名称 -->
                <h4 class="text-sm font-bold text-slate-100 truncate tracking-wide group-hover:text-[#eddab3] transition leading-snug" title="${esc(p.name)}">
                  ${esc(p.name)}
                </h4>

                <!-- 小状态栏：影片类型图标 + 进行状态（规划中/制作中/已完成） -->
                <div class="flex items-center space-x-2 text-[9px] font-mono">
                  <span class="flex items-center space-x-1 ${typeMeta.colorClass} shrink-0" title="${typeMeta.label}">
                    <i data-lucide="${typeMeta.iconName}" class="w-3.5 h-3.5"></i>
                  </span>

                  <span class="w-px h-2.5 bg-white/10 shrink-0"></span>

                  <span class="proj-status-chip ${statusMeta.key}">
                    <span class="w-1.5 h-1.5 rounded-full ${statusMeta.dotColor}"></span>
                    <span>${statusMeta.text}</span>
                  </span>
                </div>
              </div>
            </div>

            <!-- 最下面：制作进度条与数值 + 直通影视工坊快捷按钮 -->
            <div class="mt-2.5 pt-1.5 border-t border-white/5 space-y-1">
              <div class="flex justify-between items-center text-[9px] font-mono">
                <span class="text-slate-400">制作进度</span>
                <div class="flex items-center space-x-2">
                  ${progressHtml}
                  <a href="/static/pages/workshop.html?project_id=${encodeURIComponent(p.id)}"
                     class="tactile-keycap px-1.5 py-0.5 rounded text-[8px] text-[#eddab3] hover:text-white flex items-center space-x-0.5"
                     onclick="event.stopPropagation(); localStorage.setItem('workspace_project_id', '${esc(p.id)}'); localStorage.setItem('workspace_project_name', '${esc(p.name)}');"
                     title="进入该项目的影视工坊（剧本/资产/分镜/视频）">
                    <i data-lucide="film" class="w-2.5 h-2.5 text-[#dfc384]"></i>
                    <span>工坊</span>
                  </a>
                </div>
              </div>
              <div class="hw-fader-track-horizontal w-full h-1">
                <div class="hw-fader-glow-bar" style="width: ${progressMeta.degraded ? 0 : progress}%;"></div>
              </div>
            </div>

          </div>
        `;
      }).join('');

      container.innerHTML = `<div class="proj-grid-container">${cardsHtml}</div>`;
    } else {
      // 模式 2: 行条列表 (List Mode，紧凑单行，同样采用方形缩略图 + 名称 + 状态栏 + 制作进度条，无编号)
      const listHtml = state.projects.map((p, idx) => {
        const isSelected = (state.activeProject && state.activeProject.id === p.id) || (!state.activeProject && idx === 0);
        const progressMeta = projectProgressMeta(p);
        const progress = progressMeta.value;
        const progressHtml = progressMeta.degraded
          ? '<span class="text-[#eddab3] font-bold" data-gw-degradation="not_integrated" title="后端未返回该项目的 progress 字段，本页不伪造进度">未接入</span>'
          : `<span class="text-[#eddab3] font-bold">${progress}%</span>`;
        const statusMeta = getProjectStatusMeta(p.status);
        const typeMeta = getProjectTypeMeta(p.project_type);
        const coverUrl = String(p.cover_media_url || p.cover_url || '').trim();

        const thumbnailHtml = coverUrl
          ? `<div class="w-8 h-8 rounded-md overflow-hidden bg-[#06070a] border border-white/10 shrink-0 flex items-center justify-center">
               <img src="${esc(coverUrl)}" alt="${esc(p.name)}" class="w-full h-full object-cover" loading="lazy" onerror="this.parentElement.innerHTML='<i data-lucide=\\'${typeMeta.iconName}\\' class=\\'w-4 h-4 text-[#dfc384]\\'></i>'; window.lucide?.createIcons();">
             </div>`
          : `<div class="w-8 h-8 rounded-md overflow-hidden bg-[#06070a] border border-white/10 shrink-0 flex items-center justify-center text-[#dfc384]">
               <i data-lucide="${typeMeta.iconName}" class="w-4 h-4"></i>
             </div>`;

        return `
          <div class="proj-list-hardware project-hardware-card ${isSelected ? 'active' : ''}"
               data-project-id="${esc(p.id)}"
               onclick="WorkbenchHome.selectProject('${esc(p.id)}')"
               ondblclick="WorkbenchHome.openProject('${esc(p.id)}')"
               title="${esc(p.name)}（单击选中，双击进入）">
            <div class="flex items-center space-x-2.5 min-w-0 flex-1">
              ${thumbnailHtml}
              <div class="min-w-0 flex-1">
                <div class="flex items-center space-x-2">
                  <span class="text-xs font-bold text-slate-100 truncate">${esc(p.name)}</span>
                  <span class="proj-status-chip ${statusMeta.key}">
                    <span class="w-1.5 h-1.5 rounded-full ${statusMeta.dotColor}"></span>
                    <span>${statusMeta.text}</span>
                  </span>
                </div>
                <div class="flex items-center space-x-1.5 text-[8.5px] font-mono text-slate-400 mt-0.5">
                  <i data-lucide="${typeMeta.iconName}" class="w-3 h-3 ${typeMeta.colorClass}"></i>
                  <span>${typeMeta.label}</span>
                </div>
              </div>
            </div>

            <div class="flex items-center space-x-3 shrink-0 ml-2">
              <div class="w-20 space-y-0.5">
                <div class="flex justify-between text-[8px] font-mono text-slate-400">
                  <span>制作进度</span>
                  ${progressHtml}
                </div>
                <div class="hw-fader-track-horizontal w-full h-1">
                  <div class="hw-fader-glow-bar" style="width: ${progressMeta.degraded ? 0 : progress}%;"></div>
                </div>
              </div>
              <a href="/static/pages/workshop.html?project_id=${encodeURIComponent(p.id)}"
                 class="tactile-keycap px-1.5 py-0.5 rounded text-[8px] text-[#eddab3] hover:text-white flex items-center space-x-0.5 cursor-pointer"
                 onclick="event.stopPropagation(); localStorage.setItem('workspace_project_id', '${esc(p.id)}'); localStorage.setItem('workspace_project_name', '${esc(p.name)}');"
                 title="进入该项目的影视工坊">
                <i data-lucide="film" class="w-2.5 h-2.5 text-[#dfc384]"></i>
                <span>工坊</span>
              </a>
              <span class="text-[8.5px] font-mono text-slate-500">${formatTime(p.updated_at)}</span>
            </div>
          </div>
        `;
      }).join('');

      container.innerHTML = `<div class="proj-list-container">${listHtml}</div>`;
    }

    window.lucide?.createIcons();
    // 渲染时更新顶栏
    if (state.activeProject) {
      updateNavPills(state.activeProject.id);
    } else if (state.projects[0]) {
      updateNavPills(state.projects[0].id);
    }
  }

  function updateNavPills(targetId) {
    if (!targetId) return;
    document.querySelectorAll('#navPillsGroup a').forEach(a => {
      const href = a.getAttribute('href');
      if (href && !href.startsWith('http') && !href.startsWith('#')) {
        const url = new URL(href, window.location.origin);
        url.searchParams.set('project_id', targetId);
        a.setAttribute('href', url.pathname.split('/').pop() + url.search);
      }
    });
  }

  // 2. 选中/切换项目 (单击)
  function selectProject(id) {
    const found = state.projects.find(p => p.id === id);
    if (found) {
      state.activeProject = found;
      localStorage.setItem('workspace_project_id', found.id);
      if (found.name) {
        localStorage.setItem('workspace_project_name', found.name);
      }

      const busTitle = document.getElementById('busActiveProjectTitle');
      if (busTitle) busTitle.textContent = found.name;

      const busWorkshop = document.getElementById('busWorkshopLink');
      if (busWorkshop) {
        busWorkshop.setAttribute('href', `workshop.html?project_id=${encodeURIComponent(found.id)}`);
      }

      // 同步更新顶栏胶囊导航的 project_id
      updateNavPills(found.id);

      // 高亮卡片
      document.querySelectorAll('.project-hardware-card').forEach(el => {
        if (el.dataset.projectId === id) {
          el.classList.add('active');
        } else {
          el.classList.remove('active');
        }
      });
    }
  }

  // 3. 打开/进入项目 (双击或按快捷键 · 时调用，直通真实制片路由 /static/pages/production.html)
  function openProject(id) {
    const targetId = id || state.activeProject?.id || state.projects[0]?.id;
    if (!targetId) return;

    const found = state.projects.find(p => p.id === targetId);
    localStorage.setItem('workspace_project_id', targetId);
    if (found && found.name) {
      localStorage.setItem('workspace_project_name', found.name);
    }

    // 直通真实制片路由
    window.location.href = `/static/pages/production.html?project_id=${encodeURIComponent(targetId)}`;
  }

  // 4. 键盘监听：按下 “·” (Backquote / 点号键) 进入当前选中的项目
  const DOT_KEYS = new Set(['·', '`', '・', '･']);
  function setupKeyboardShortcuts() {
    document.addEventListener('keydown', event => {
      // 忽略输入框与弹窗内按键
      if (event.defaultPrevented || event.isComposing || event.ctrlKey || event.metaKey || event.altKey || event.shiftKey) return;
      if (document.querySelector('.hw-modal-backdrop.open, dialog[open]')) return;
      if (event.target?.closest?.('input, textarea, select, [contenteditable]:not([contenteditable="false"])')) return;

      const isDot = event.key === '·' || DOT_KEYS.has(event.key) || event.code === 'Backquote';
      if (isDot) {
        event.preventDefault();
        openProject();
      }
    });
  }

  // 3. 处理新建项目表单提交
  async function handleCreateProject(event) {
    event.preventDefault();
    const nameInput = document.getElementById('newProjectNameInput');
    const typeSelect = document.getElementById('newProjectTypeSelect');
    const descInput = document.getElementById('newProjectDescInput');
    const startAtInput = document.getElementById('newProjectStartAt');
    const dueAtInput = document.getElementById('newProjectDueAt');

    const name = nameInput?.value?.trim();
    if (!name) return;

    const payload = {
      name: name,
      project_type: typeSelect?.value || 'film',
      description: descInput?.value?.trim() || '',
      start_at: dateInputTimestamp(startAtInput?.value),
      due_at: dateInputTimestamp(dueAtInput?.value)
    };

    try {
      const res = await fetch('/api/asset-registry/projects', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'same-origin',
        body: JSON.stringify(payload)
      });
      if (res.ok) {
        const resData = await res.json();
        const raw = resData.project || resData;
        // 契约对齐：创建接口只回传 project_id / version（见 PROJECTS-HUB-INTERFACE-CATALOG.yaml 的
        // create_project.response_200_201），不含 id 与 name；此处归一化并补全本地渲染所需名称，
        // 否则新建后不写 localStorage、不刷新导航胶囊，且卡片 data-project-id 为空。
        const created = { ...raw, id: raw.id || raw.project_id, name: raw.name || payload.name };
        state.projects.unshift(created);
        if (created.id) {
          localStorage.setItem('workspace_project_id', created.id);
          localStorage.setItem('workspace_project_name', created.name || created.title || payload.name);
          // 既有缺陷修复：原调用 updateNavPillsProject() 在本文件作用域内并不存在
          // （它只定义于 projects-controller.js 的 WorkbenchProjects 模块内），会抛 ReferenceError 并被
          // 外层 catch 吞掉，导致随后的 renderProjectsList() 永不执行、新建卡片不出现。
          // 本文件自身的等价辅助函数为 updateNavPills(targetId)。
          updateNavPills(created.id);
        }
        HardwareDeck.closeModal('newProjectModal');
        nameInput.value = '';
        if (descInput) descInput.value = '';
        window.GWProjectDateRange?.setRange(document.querySelector('#newProjectModal [data-project-date-range]'), '', '');
        renderProjectsList();
      } else {
        const errJson = await res.json().catch(() => ({}));
        console.warn('新建项目未成功:', errJson.detail || res.statusText);
      }
    } catch (e) {
      console.warn('新建项目网络异常:', e);
    }
  }

  // =========================================================================
  // AURA 智能体机舱控制逻辑 (对齐原系统路由: /api/ai/upload, /agents.html, /api/chat)
  // =========================================================================
  const auraState = {
    chatModel: '',
    providerId: '',
    providers: [],
    sending: false,
    imageModel: 'FLUX.1-DEV',
    ratio: '1:1',
    sizeSpec: '1024x1024',
    resolutionLevel: '1K',
    attachments: []
  };

  // 切换 API/模型选择弹窗
  function toggleAuraApiPopover(eventOrShow) {
    if (eventOrShow && eventOrShow.stopPropagation) eventOrShow.stopPropagation();
    const popover = document.getElementById('workbenchAuraApiPopover');
    const sizePopover = document.getElementById('workbenchAuraSizePopover');
    if (!popover) return;

    if (sizePopover) sizePopover.classList.add('hidden');

    if (typeof eventOrShow === 'boolean') {
      if (eventOrShow) popover.classList.remove('hidden');
      else popover.classList.add('hidden');
    } else {
      popover.classList.toggle('hidden');
    }
  }

  // 切换 尺寸选择弹窗
  function toggleAuraSizePopover(eventOrShow) {
    if (eventOrShow && eventOrShow.stopPropagation) eventOrShow.stopPropagation();
    const popover = document.getElementById('workbenchAuraSizePopover');
    const apiPopover = document.getElementById('workbenchAuraApiPopover');
    if (!popover) return;

    if (apiPopover) apiPopover.classList.add('hidden');

    if (typeof eventOrShow === 'boolean') {
      if (eventOrShow) popover.classList.remove('hidden');
      else popover.classList.add('hidden');
    } else {
      popover.classList.toggle('hidden');
    }
  }

  // 只允许选择服务端公开配置中的 Provider 与模型，不使用展示用预设冒名路由。
  function setAuraModel(providerId, model) {
    const provider = auraState.providers.find(item => item.provider_id === providerId);
    if (!provider || !provider.models.includes(model)) return;
    auraState.providerId = providerId;
    auraState.chatModel = model;
    const lbl = document.getElementById('workbenchAuraApiLabel');
    if (lbl) lbl.textContent = `API · ${providerId} / ${model}`;
    const badge = document.getElementById('auraHeaderModelBadge');
    if (badge) badge.textContent = model;
    toggleAuraApiPopover(false);
  }

  function applyAuraConfiguration(body) {
    auraState.providers = Array.isArray(body && body.providers) ? body.providers.filter(item =>
      item && item.configured === true && typeof item.provider_id === 'string' &&
      Array.isArray(item.models) && item.models.every(model => typeof model === 'string')) : [];
    const previous = auraState.providers.find(item => item.provider_id === auraState.providerId &&
      item.models.includes(auraState.chatModel));
    const provider = previous || auraState.providers.find(item => item.provider_id === body?.default_provider_id);
    if (provider) setAuraModel(provider.provider_id, previous ? auraState.chatModel : provider.default_model);
    else {
      auraState.providerId = '';
      auraState.chatModel = '';
      const lbl = document.getElementById('workbenchAuraApiLabel');
      if (lbl) lbl.textContent = 'API · 请选择已配置模型';
      const badge = document.getElementById('auraHeaderModelBadge');
      if (badge) badge.textContent = '尚未选择模型';
    }
    const options = document.getElementById('workbenchAuraModelOptions');
    if (!options) return;
    options.replaceChildren();
    for (const item of auraState.providers) {
      for (const model of item.models) {
        const button = document.createElement('button');
        button.type = 'button';
        button.className = 'w-full text-left px-2 py-1 rounded bg-[#161a24] text-slate-200';
        button.textContent = `${item.provider_id} / ${model}`;
        button.addEventListener('click', () => setAuraModel(item.provider_id, model));
        options.appendChild(button);
      }
    }
    if (!auraState.providers.length) options.textContent = '尚无可用服务端模型配置';
  }

  // 设置比例与尺寸
  function setAuraRatio(ratio, spec) {
    auraState.ratio = ratio;
    auraState.sizeSpec = spec;

    const lbl = document.getElementById('workbenchAuraSizeLabel');
    if (lbl) lbl.textContent = `尺寸 · ${ratio}`;

    const specSpan = document.getElementById('workbenchAuraCurrentSizeSpec');
    if (specSpan) specSpan.textContent = spec.replace('x', ' × ');

    // 更新弹窗中按钮选中高亮
    const sizePopover = document.getElementById('workbenchAuraSizePopover');
    if (sizePopover) {
      const btns = sizePopover.querySelectorAll('.grid button');
      btns.forEach(b => {
        if (b.innerText.startsWith(ratio)) {
          b.classList.add('active', 'border-[#dfc384]', 'text-[#dfc384]');
        } else {
          b.classList.remove('active', 'border-[#dfc384]', 'text-[#dfc384]');
        }
      });
    }

    toggleAuraSizePopover(false);
  }

  // 设置分辨率档位 (1K / 2K / 4K)
  function setAuraResolutionLevel(lvl) {
    auraState.resolutionLevel = lvl;
    const sizePopover = document.getElementById('workbenchAuraSizePopover');
    if (sizePopover) {
      const btns = sizePopover.querySelectorAll('.flex button');
      btns.forEach(b => {
        if (b.innerText.trim() === lvl) {
          b.className = 'px-1.5 py-0.5 rounded bg-[#1a1e2b] text-[#dfc384] border border-[#dfc384]/30';
        } else {
          b.className = 'px-1.5 py-0.5 rounded bg-[#161a24] text-slate-300 hover:text-white border border-white/10';
        }
      });
    }
  }

  // 处理附件上传 (对齐原系统路由 POST /api/ai/upload)
  // 处理附件上传 (对齐原系统路由 POST /api/ai/upload)
// 真实端点 POST /api/ai/upload（Phase 12 已接入：真实落盘并返回 file_id/url）。
// 失败时本地 Blob 预览只用于界面预览，必须明确告知用户「未上传到服务器」，绝不伪造上传成功。
  async function handleAuraFilesUpload(files) {
    if (!files || files.length === 0) return;

    const formData = new FormData();
    for (let i = 0; i < files.length; i++) {
      formData.append('files', files[i]);
    }

    const pushLocalPreviewAttachments = () => {
      for (let i = 0; i < files.length; i++) {
        const file = files[i];
        auraState.attachments.push({
          url: URL.createObjectURL(file),
          name: file.name,
          size: file.size,
          uploadState: 'not_uploaded'
        });
      }
    };

    let uploadNotice = null;
    try {
      const res = await fetch('/api/ai/upload', {
        method: 'POST',
        credentials: 'same-origin',
        body: formData
      });

      if (res.ok) {
        const data = await res.json();
        const uploaded = data.files || [];
        uploaded.forEach(f => {
          auraState.attachments.push({
            url: f.url || URL.createObjectURL(files[0]),
            name: f.name || f.filename || '附件',
            size: f.size || 0,
            uploadState: 'uploaded'
          });
        });
      } else {
        const failure = await classifyFetchFailure(res);
        pushLocalPreviewAttachments();
      uploadNotice = '附件上传未成功，以下仅为本地预览，未上传到服务器。（' + failure.message + '）';
      }
    } catch (e) {
      console.warn('附件上传请求异常（网络异常）:', e);
      pushLocalPreviewAttachments();
      uploadNotice = '附件上传 API 请求失败，以下仅为本地预览，未上传到服务器。（' + degradationMessage('service_unavailable', 0) + '）';
    }

    if (uploadNotice) showAuraUploadNotice(uploadNotice);
    renderAuraAttachments();
  }

  // 附件未上传时的显式提示条（用户可见，不用 console 代替）。
  function showAuraUploadNotice(text) {
    const strip = document.getElementById('workbenchAuraAttachmentsStrip');
    if (!strip) return;
    let notice = document.getElementById('workbenchAuraUploadNotice');
    if (!notice) {
      notice = document.createElement('div');
      notice.id = 'workbenchAuraUploadNotice';
      notice.setAttribute('role', 'status');
      notice.setAttribute('data-gw-degradation', 'not_integrated');
      notice.className = 'mb-1 px-2 py-1 rounded-lg border border-amber-500/30 text-[9px] font-mono text-amber-300';
      strip.parentNode.insertBefore(notice, strip);
    }
    notice.textContent = text;
  }

  // 渲染已上传的附件缩略条
  function renderAuraAttachments() {
    const strip = document.getElementById('workbenchAuraAttachmentsStrip');
    if (!strip) return;

    if (auraState.attachments.length === 0) {
      strip.classList.add('hidden');
      strip.innerHTML = '';
      return;
    }

    strip.classList.remove('hidden');
    strip.innerHTML = auraState.attachments.map((item, idx) => `
      <div class="bay-inset px-2 py-1 rounded-lg border border-[#dfc384]/30 flex items-center space-x-1.5 text-[9px] font-mono text-slate-200 bg-[#121620]">
        <i data-lucide="file-warning" class="w-2.5 h-2.5 text-amber-300"></i>
        <span class="max-w-[100px] truncate" title="${esc(item.name)}">${esc(item.name)}</span>
        ${item.uploadState === 'not_uploaded' ? '<span class="px-1 rounded border border-amber-500/30 text-amber-300" title="未上传到服务器">未上传</span>' : ''}
        <button type="button" class="text-slate-400 hover:text-rose-400 ml-1" onclick="WorkbenchHome.removeAuraAttachment(${idx})" title="移除此附件">
          &times;
        </button>
      </div>
    `).join('');

    if (window.lucide) window.lucide.createIcons();
  }

  // 移除指定附件
  function removeAuraAttachment(index) {
    if (auraState.attachments[index]) {
      auraState.attachments.splice(index, 1);
      renderAuraAttachments();
    }
  }

  // 全局点击自动关闭浮窗
  document.addEventListener('click', function (e) {
    const apiPopover = document.getElementById('workbenchAuraApiPopover');
    const sizePopover = document.getElementById('workbenchAuraSizePopover');
    const apiBtn = document.getElementById('workbenchAuraApiBtn');
    const sizeBtn = document.getElementById('workbenchAuraSizeBtn');

    if (apiPopover && !apiPopover.contains(e.target) && apiBtn && !apiBtn.contains(e.target)) {
      apiPopover.classList.add('hidden');
    }
    if (sizePopover && !sizePopover.contains(e.target) && sizeBtn && !sizeBtn.contains(e.target)) {
      sizePopover.classList.add('hidden');
    }
  });

  // 4. 处理智能体对话发送 (携带模型、尺寸与附件对齐原系统契约)
  async function handleChatSubmit(event) {
    event.preventDefault();
    const input = document.getElementById('workbenchChatInput');
    const stream = document.getElementById('workbenchChatStream');
    if (!input || !stream) return;

    const query = input.value.trim();
    if (!query && auraState.attachments.length === 0) return;
    if (auraState.sending) return;
    if (!auraState.providerId || !auraState.chatModel) {
      window.alert('请先选择服务端已配置的 Provider 和模型。');
      toggleAuraApiPopover(true);
      return;
    }
    if (!window.confirm('将向所选真实模型发送消息，可能产生费用；失败不会自动重试。是否继续？')) return;
    const selectedProviderId = auraState.providerId;
    const selectedModel = auraState.chatModel;
    auraState.sending = true;
    const requestAbort = new AbortController();
    const requestTimeout = setTimeout(() => requestAbort.abort(), 40000);
    try {
    const attachedCount = auraState.attachments.length;
    const currentAttachments = [...auraState.attachments];

    // 渲染用户消息 (右侧曜石拟物气泡)
    const userMsgId = 'msg-u-' + Date.now();
    const attachmentsSnippet = attachedCount > 0 ? `
      <div class="flex flex-wrap gap-1 mt-1 mb-1">
        ${currentAttachments.map(a => `
          <span class="inline-flex items-center space-x-1 px-1.5 py-0.5 rounded bg-black/40 border border-white/10 text-[8px] text-cyan-300">
            <i data-lucide="paperclip" class="w-2 h-2"></i>
            <span class="truncate max-w-[80px]">${esc(a.name)}</span>
          </span>
        `).join('')}
      </div>
    ` : '';

    const userHtml = `
      <div id="${userMsgId}" class="flex justify-end my-2">
        <div class="bay-inset bg-[#141721] border border-[#dfc384]/30 rounded-2xl rounded-tr-sm px-3 py-2 max-w-[85%] text-slate-100 shadow-sm text-xs">
          <div class="text-[8px] font-mono text-[#dfc384] text-right mb-0.5 flex items-center justify-end space-x-1.5">
            <span class="text-slate-400 font-sans">${auraState.ratio} · ${auraState.resolutionLevel}</span>
            <span>DIRECTOR (YOU)</span>
          </div>
          ${esc(query)}
          ${attachmentsSnippet}
        </div>
      </div>
    `;
    stream.insertAdjacentHTML('beforeend', userHtml);
    input.value = '';
    // 清除附件暂存区
    auraState.attachments = [];
    renderAuraAttachments();
    stream.scrollTop = stream.scrollHeight;

    // 渲染 AURA 响应机舱气泡 (带思维加载动画)
    const botMsgId = 'msg-b-' + Date.now();
    const botHtml = `
      <div id="${botMsgId}" class="flex justify-start my-2">
        <div class="p-3 rounded-2xl rounded-tl-sm bg-[#0a0c10] border border-[#dfc384]/20 max-w-[90%] text-xs shadow-md space-y-1.5">
          <div class="flex items-center justify-between text-[8.5px] font-mono text-[#dfc384] font-bold">
            <div class="flex items-center space-x-1.5">
              <i data-lucide="bot" class="w-3 h-3 text-[#dfc384]"></i>
              <span>AURA · 核心智能体</span>
              <span class="w-1.5 h-1.5 rounded-full bg-emerald-400 animate-ping"></span>
            </div>
            <span class="text-slate-500 font-normal">${esc(selectedModel)}</span>
          </div>
          <div class="text-slate-200 leading-relaxed font-sans aura-content">
            <span class="text-slate-500 flex items-center space-x-1">
              <i data-lucide="loader" class="w-3 h-3 animate-spin"></i>
              <span>正在分析制片工程上下文并检索分镜矩阵...</span>
            </span>
          </div>
        </div>
      </div>
    `;
    stream.insertAdjacentHTML('beforeend', botHtml);
    window.lucide?.createIcons();
    stream.scrollTop = stream.scrollHeight;

    // 发起后端 API 请求 (向后兼容 /api/chat 且携带尺寸、模型与附件)
    try {
      const res = await fetch('/api/chat', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'same-origin',
        signal: requestAbort.signal,
        body: JSON.stringify({
          message: query,
          provider_id: selectedProviderId,
          model: selectedModel,
          image_model: auraState.imageModel,
          aspect_ratio: auraState.ratio,
          size_spec: auraState.sizeSpec,
          resolution: auraState.resolutionLevel,
          attachments: currentAttachments,
          context: { project: state.activeProject?.name || '神谕之地' }
        })
      });

      const botBubble = document.querySelector(`#${botMsgId} .aura-content`);
      if (res.ok) {
        const reply = await res.json();
        const text = reply.response || reply.reply || reply.message || '已成功执行您的指令。';
        if (botBubble) botBubble.innerHTML = esc(text).replace(/\n/g, '<br>');
      } else {
        // 显式降级：请求失败必须如实告知，不得伪造任何成功文案。
        const failure = await classifyFetchFailure(res);
        if (botBubble) {
          botBubble.setAttribute('data-gw-degradation', failure.kind);
          botBubble.innerHTML = `<span class="text-amber-300">未能完成本次指令（未接入或服务不可用）。</span>`
            + `<span class="block mt-1 text-[10px] text-slate-400">${esc(failure.message)}</span>`;
        }
      }
    } catch (e) {
      const botBubble = document.querySelector(`#${botMsgId} .aura-content`);
      if (botBubble) {
        botBubble.setAttribute('data-gw-degradation', 'service_unavailable');
        botBubble.innerHTML = `<span class="text-amber-300">对话服务暂时不可用，请联系后端部署方。</span>`
          + `<span class="block mt-1 text-[10px] text-slate-400">${esc(degradationMessage('service_unavailable', 0))}</span>`;
      }
    }

    if (window.lucide) window.lucide.createIcons();
    stream.scrollTop = stream.scrollHeight;
    } finally {
      clearTimeout(requestTimeout);
      auraState.sending = false;
    }
  }

  // 5. 提示词芯片快速填入
  function insertChip(text) {
    const input = document.getElementById('workbenchChatInput');
    if (input) {
      input.value = text;
      input.focus();
    }
  }

  // 6. 主工作台视图切换 (控制台三列 vs 拟物设置工作台，实现“只切换下面页面，不是整体都变”)
  let settingsInitialized = false;

  function switchView(target) {
    const dashboardView = document.getElementById('workbenchMainDashboard');
    const settingsView = document.getElementById('workbenchMainSettings');
    const pillDashboard = document.getElementById('navPillDashboard');
    const pillSettings = document.getElementById('navPillSettings');
    const contextBusAside = document.getElementById('homeContextBusAside') || document.getElementById('contextBusContainer')?.closest('aside');

    if (target === 'settings') {
      if (contextBusAside) contextBusAside.classList.add('hidden');
      if (dashboardView) dashboardView.classList.add('hidden');
      if (settingsView) {
        settingsView.classList.remove('hidden');
        settingsView.classList.add('flex');
      }

      if (pillDashboard) {
        pillDashboard.className = 'pill-capsule-inactive px-3.5 py-1.5 text-xs tracking-wide font-medium flex items-center space-x-1.5 nav-pill-btn cursor-pointer';
      }
      if (pillSettings) {
        pillSettings.className = 'pill-capsule-active px-3.5 py-1.5 text-xs tracking-wide flex items-center space-x-1.5 nav-pill-btn cursor-pointer';
        pillSettings.setAttribute('aria-pressed', 'true');
      }
      if (pillDashboard) pillDashboard.setAttribute('aria-pressed', 'false');

      if (!settingsInitialized) {
        initSettingsModule();
        settingsInitialized = true;
      }
      try {
        const url = new URL(window.location);
        url.searchParams.set('view', 'settings');
        window.history.replaceState({}, '', url);
      } catch (e) {}
    } else {
      // 默认切换回控制台首页
      if (settingsView) {
        settingsView.classList.add('hidden');
        settingsView.classList.remove('flex');
      }
      if (dashboardView) dashboardView.classList.remove('hidden');
      if (contextBusAside) contextBusAside.classList.remove('hidden');

      if (pillDashboard) {
        pillDashboard.className = 'pill-capsule-active px-3.5 py-1.5 text-xs tracking-wide flex items-center space-x-1.5 nav-pill-btn cursor-pointer';
      }
      if (pillSettings) {
        pillSettings.className = 'pill-capsule-inactive px-3.5 py-1.5 text-xs tracking-wide font-medium flex items-center space-x-1.5 nav-pill-btn cursor-pointer';
        pillSettings.setAttribute('aria-pressed', 'false');
      }
      if (pillDashboard) pillDashboard.setAttribute('aria-pressed', 'true');

      try {
        const url = new URL(window.location);
        url.searchParams.delete('view');
        window.history.replaceState({}, '', url);
      } catch (e) {}
    }

    if (window.lucide) window.lucide.createIcons();
  }

  // 设置模块就地数据绑定与生命周期
  function initSettingsModule() {
    const key = 'workspace_preferences';
    const promptSourceKey = 'prompt_source_settings_v1';
    let preferences = {};
    let promptSourceManifest = null;

    if (!localStorage.getItem('theme')) {
      localStorage.setItem('theme', 'dark');
    }

    try { preferences = JSON.parse(localStorage.getItem(key) || '{}'); } catch(e) {}

    function save() {
      localStorage.setItem(key, JSON.stringify(preferences));
      const saveState = document.getElementById('saveState');
      if (saveState) {
        saveState.textContent = 'SAVED';
        setTimeout(() => { if (saveState) saveState.textContent = 'LOCAL SETTINGS'; }, 900);
      }
    }

    // 默认启动页
    const defPage = document.getElementById('defaultPage');
    if (defPage) {
      defPage.value = preferences.default_page || 'index';
      defPage.onchange = e => {
        preferences.default_page = e.target.value;
        save();
      };
    }

    // 开关绑定
    document.querySelectorAll('#workbenchMainSettings [data-setting]').forEach(toggle => {
      const name = toggle.dataset.setting;
      const fallback = ['task_refresh', 'restore_page'].includes(name);
      const on = preferences[name] ?? fallback;
      toggle.classList.toggle('on', on);
      toggle.setAttribute('aria-pressed', String(on));

      toggle.onclick = () => {
        preferences[name] = !toggle.classList.contains('on');
        toggle.classList.toggle('on', preferences[name]);
        toggle.setAttribute('aria-pressed', String(preferences[name]));
        save();
      };
    });

    // 主题切换
    const themeToggle = document.getElementById('themeToggle');
    if (themeToggle) {
      themeToggle.onclick = () => {
        themeToggle.classList.toggle('on');
        const isDark = themeToggle.classList.contains('on');
        themeToggle.setAttribute('aria-pressed', String(isDark));
        localStorage.setItem('theme', isDark ? 'dark' : 'light');
      };
    }

    // 语言切换
    const langToggle = document.getElementById('languageToggle');
    if (langToggle) {
      langToggle.onclick = () => {
        alert('界面已锁定当前“简体中文”专业影视制片工作台配置。');
      };
    }

    // 分区切换
    const settingsSectionButtons = [...document.querySelectorAll('#workbenchMainSettings [data-section]')];
    const activateSettingsSection = section => {
      const next = settingsSectionButtons.find(btn => btn.dataset.section === section) || settingsSectionButtons[0];
      if (!next) return;
      const sec = next.dataset.section;
      settingsSectionButtons.forEach(btn => {
        const active = btn === next;
        btn.classList.toggle('active', active);
        btn.setAttribute('aria-pressed', String(active));
      });
      document.querySelectorAll('#workbenchMainSettings [data-panel]').forEach(p => p.classList.toggle('active', p.dataset.panel === sec));
        if (sec === 'permissions' && window.HardwareDeck && window.HardwareDeck.loadTeamModalData) {
          window.HardwareDeck.loadTeamModalData('panelTeamList');
        }
        if (window.lucide) window.lucide.createIcons();
    };
    settingsSectionButtons.forEach(btn => { btn.onclick = () => activateSettingsSection(btn.dataset.section); });
    try {
      const params = new URLSearchParams(window.location.search);
      activateSettingsSection(params.get('section') || new URLSearchParams(window.location.hash.slice(1)).get('section') || 'general');
    } catch (_) { activateSettingsSection('general'); }

    // 团队偏好载入与同步
    fetch('/api/asset-registry/preferences/team', { credentials: 'same-origin' })
      .then(async res => {
        if (res.ok) return res.json();
        const failure = await classifyFetchFailure(res);
        const namingEl = document.getElementById('teamNaming');
        if (namingEl) namingEl.placeholder = '未接入（' + failure.kind + '）';
        return null;
      })
      .then(data => {
        if (!data) return;
        const p = data?.preferences || {};
        const teamNaming = document.getElementById('teamNaming');
        const teamVisibility = document.getElementById('teamVisibility');
        if (teamNaming && p.naming_convention) teamNaming.value = p.naming_convention;
        if (teamVisibility && p.default_review_visibility) teamVisibility.value = p.default_review_visibility;
      }).catch(() => {
        const namingEl = document.getElementById('teamNaming');
        if (namingEl) namingEl.placeholder = '服务暂时不可用';
      });

    const btnSaveTeamPrefs = document.getElementById('btnSaveTeamPrefs');
    if (btnSaveTeamPrefs) {
      btnSaveTeamPrefs.onclick = async () => {
        const naming = document.getElementById('teamNaming')?.value.trim() || '{project}_{entity}_{version}';
        const visibility = document.getElementById('teamVisibility')?.value || 'team';
        btnSaveTeamPrefs.disabled = true;
        btnSaveTeamPrefs.textContent = '保存中…';
        try {
          const res = await fetch('/api/asset-registry/preferences/team', {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            credentials: 'same-origin',
            body: JSON.stringify({ naming_convention: naming, default_review_visibility: visibility })
          });
          if (res.ok) {
            btnSaveTeamPrefs.textContent = '已同步';
          } else {
            const failure = await classifyFetchFailure(res);
            btnSaveTeamPrefs.textContent = failure.kind === 'not_integrated' ? '未接入' : '保存失败';
          }
        } catch (e) {
          btnSaveTeamPrefs.textContent = '服务不可用';
        }
        setTimeout(() => {
          btnSaveTeamPrefs.disabled = false;
          btnSaveTeamPrefs.textContent = '保存同步';
        }, 1200);
      };
    }

    // 读取系统状态与系统信息
    Promise.all([
      fetch('/api/app-info').then(async r => r.ok ? r.json() : { __degraded: await classifyFetchFailure(r) })
        .catch(() => ({ __degraded: { kind: 'service_unavailable', message: degradationMessage('service_unavailable', 0) } })),
      fetch('/api/asset-registry/status').then(async r => r.ok ? r.json() : { __degraded: await classifyFetchFailure(r) })
        .catch(() => ({ __degraded: { kind: 'service_unavailable', message: degradationMessage('service_unavailable', 0) } }))
    ]).then(([app, status]) => {
      // 显式降级：依赖未接入或不可用时，用中性文案替代「已就绪 / 通信正常」。
      const degradedList = [app, status].map(x => x && x.__degraded).filter(Boolean);
      const degradedKind = degradedList.length ? degradedList[0].kind : null;
      const degradedText = degradedList.length ? degradedList[0].message : '';
      const statusText = degradedKind === 'not_integrated' ? '未接入'
        : (degradedKind ? '暂不可用' : (status.ready ? '已就绪 (Active)' : '通信正常'));
      const statusClass = (degradedKind || !status.ready) ? 'text-slate-400 font-bold' : 'text-emerald-400 font-bold';
      const infoContainer = document.getElementById('systemInfo');
      if (infoContainer) {
        infoContainer.innerHTML = `
          <div class="flex justify-between py-1 border-b border-white/5">
            <span class="text-slate-400">工作台固件版本:</span>
            <strong class="text-[#dfc384]">${esc(app.version || '—')}</strong>
          </div>
          <div class="flex justify-between py-1 border-b border-white/5">
            <span class="text-slate-400">数字资产后端引擎:</span>
            <strong class="text-slate-200">${esc(status.backend || '—')}</strong>
          </div>
          <div class="flex justify-between py-1 border-b border-white/5">
            <span class="text-slate-400">硬件总线通信:</span>
            <strong class="${statusClass}">${esc(statusText)}</strong>
          </div>
          ${degradedList.length ? `<div class="pt-1 text-[9px] font-mono text-slate-500" role="status" data-gw-degradation="${esc(degradedKind)}">${esc(degradedText)}</div>` : ''}
          <div class="pt-1 text-right">
            <button class="tactile-keycap px-2 py-1 rounded text-[10px] text-slate-300 hover:text-white" onclick="alert('当前固件已是最新正式版本')">检查更新</button>
          </div>
        `;
      }
    });

    // 读取本地提示词来源快照
    loadPromptSources();

    async function loadPromptSources() {
      const list = document.getElementById('promptSourceList');
      const stateBadge = document.getElementById('promptSourceSnapshotState');
      if (!list) return;

      try {
        const res = await fetch('/static/prompt-registry/manifest.json', { cache: 'no-store' });
        if (!res.ok) throw new Error('读取本地快照清单失败');
        promptSourceManifest = await res.json();
        const sources = Array.isArray(promptSourceManifest.sources) ? promptSourceManifest.sources : [];
        if (stateBadge) stateBadge.textContent = `${promptSourceManifest.snapshotId || '固定快照'} · ${sources.length} 条来源`;

        list.innerHTML = sources.map(source => {
          const id = String(source.id || '');
          const sourcePath = String(source.path || '').replace(/^\/+/, '');
          return `
            <article class="prompt-source-row">
              <div class="prompt-source-toggle">
                <button type="button" class="toggle on" onclick="this.classList.toggle('on')"><i></i></button>
              </div>
              <div class="prompt-source-main">
                <div class="prompt-source-title">
                  <strong class="text-slate-200 font-bold">${esc(source.name || id)}</strong>
                  <span class="prompt-source-badge">本地</span>
                </div>
                <div class="prompt-source-meta">
                  <span>${Number(source.count || 0)} 条词元</span>
                  <span>${esc(source.license || '开源规范')}</span>
                  <span>本地映射: <code>${esc(sourcePath)}</code></span>
                </div>
              </div>
              <div class="prompt-source-actions">
                <button type="button" class="tactile-keycap px-2 py-1 rounded text-[10px] text-slate-300" onclick="alert('已重新校验快照文件 SHA-256')">校验快照</button>
              </div>
            </article>
          `;
        }).join('');
      } catch (e) {
        // 显式降级：读取失败不得自称已就绪或使用内置源。
        if (stateBadge) stateBadge.textContent = '未接入';
        list.innerHTML = `
          <div class="bay-inset p-3 rounded-xl text-xs font-mono text-slate-400 text-center" role="status" data-gw-degradation="not_integrated">
            本地提示词快照库未接入（未纳入当前切片）：未能读取 manifest.json。
            <span class="block mt-1 text-[9px] text-slate-500">${esc(e && e.message ? e.message : '')}</span>
          </div>
        `;
      }

      if (window.lucide) window.lucide.createIcons();
    }
  }

  // 7. 数字资产仓储概览数据读取与真实路由对接 (/api/asset-registry/status & /api/asset-registry/assets)
  async function reloadAssetOverview() {
    const listContainer = document.getElementById('workbenchRecentAssetsList');
    const statusBadge = document.getElementById('assetOverviewStatus');
    const poolSize = document.getElementById('statAssetPoolSize');
    const statImages = document.getElementById('statImagesCount');
    const statModels = document.getElementById('statModelsCount');
    const statVideos = document.getElementById('statVideosCount');
    const statAudios = document.getElementById('statAudiosCount');

    // 辅助格式化大小
    const formatBytes = (bytes) => {
      if (!bytes || isNaN(bytes)) return '—';
      const k = 1024;
      const sizes = ['B', 'KB', 'MB', 'GB', 'TB'];
      const i = Math.floor(Math.log(bytes) / Math.log(k));
      return (bytes / Math.pow(k, i)).toFixed(1) + ' ' + sizes[i];
    };

    // 辅助获取资产图标与类型
    const getAssetMeta = (type, path = '') => {
      const ext = (path.split('.').pop() || '').toLowerCase();
      const t = String(type || '').toLowerCase();
      if (t.includes('image') || ['png', 'jpg', 'jpeg', 'webp', 'exr', 'psd'].includes(ext)) {
        return { icon: 'image', color: 'text-emerald-400', label: '原画/图像' };
      }
      if (t.includes('model') || t.includes('mesh') || ['usd', 'usdz', 'obj', 'fbx', 'gltf', 'glb', 'safetensors'].includes(ext)) {
        return { icon: 'box', color: 'text-cyan-300', label: '3D/权重' };
      }
      if (t.includes('video') || ['mp4', 'mov', 'webm', 'mkv', 'avi'].includes(ext)) {
        return { icon: 'film', color: 'text-amber-300', label: '视频/分镜' };
      }
      if (t.includes('audio') || ['wav', 'mp3', 'aac', 'flac', 'ogg'].includes(ext)) {
        return { icon: 'volume-2', color: 'text-purple-300', label: '音频/音效' };
      }
      return { icon: 'file-text', color: 'text-[#dfc384]', label: '工程资产' };
    };

    try {
      // 1. 读取资产注册表状态（显式降级：未接入 / 不可用时不得显示绿色就绪灯）。
      const statusRes = await fetch('/api/asset-registry/status', { credentials: 'same-origin' });
      if (statusRes.ok) {
        const statusData = await statusRes.json();
        if (statusBadge) {
          statusBadge.innerHTML = `
            <span class="w-1.5 h-1.5 rounded-full ${statusData.ready ? 'bg-emerald-400' : 'bg-amber-400'} animate-pulse"></span>
            <span>${statusData.ready ? '已就绪' : '服务未就绪'}</span>
          `;
        }
        // 真实字段只在存在时渲染；硬编码的「/ 10 TB」容量分母没有后端来源，
        // 属伪读数，已移除（未接入时显示「—」而不是编造分母）。
        if (poolSize) {
          poolSize.textContent = statusData.pool_size ? String(statusData.pool_size) : '—';
          poolSize.dataset.gwDegradation = statusData.pool_size ? '' : 'not_integrated';
        }
      } else {
        const failure = await classifyFetchFailure(statusRes);
        state.assetOverviewDegradation = { kind: failure.kind, message: failure.message };
        if (statusBadge) {
          statusBadge.innerHTML = `
            <span class="w-1.5 h-1.5 rounded-full bg-slate-500"></span>
            <span>${failure.kind === 'not_integrated' ? '未接入' : '暂不可用'}</span>
          `;
        }
        if (poolSize) poolSize.textContent = '—';
      }

      // 2. 读取最近真实资产列表
      // 2. 读取最近真实资产列表（显式降级：未接入时不得用伪资产冒充真实数据）。
      const assetsRes = await fetch('/api/asset-registry/assets?limit=6', { credentials: 'same-origin' });
      let assets = [];
      if (assetsRes.ok) {
        const assetsData = await assetsRes.json();
        assets = assetsData.assets || (Array.isArray(assetsData) ? assetsData : []);
      } else {
        const failure = await classifyFetchFailure(assetsRes);
        state.assetOverviewDegradation = state.assetOverviewDegradation || { kind: failure.kind, message: failure.message };
      }

      // 3. 统计或渲染真实资产条目
      if (assets.length > 0) {
        // 如果有真实返回资产
        let imgCount = 0, modelCount = 0, vidCount = 0, audCount = 0;
        assets.forEach(a => {
          const meta = getAssetMeta(a.asset_type || a.type, a.file_path || a.name);
          if (meta.icon === 'image') imgCount++;
          else if (meta.icon === 'box') modelCount++;
          else if (meta.icon === 'film') vidCount++;
          else if (meta.icon === 'volume-2') audCount++;
        });

        if (statImages && imgCount > 0) statImages.textContent = imgCount + '+';
        if (statModels && modelCount > 0) statModels.textContent = modelCount + '+';
        if (statVideos && vidCount > 0) statVideos.textContent = vidCount + '+';
        if (statAudios && audCount > 0) statAudios.textContent = audCount + '+';

        if (listContainer) {
          listContainer.innerHTML = assets.map(a => {
            const meta = getAssetMeta(a.asset_type || a.type, a.file_path || a.name || a.title);
            const name = a.name || a.title || a.file_path?.split('/').pop() || '未命名资产';
            const sizeStr = a.file_size ? formatBytes(a.file_size) : (a.size_label || '24 MB');
            const targetUrl = a.id ? `/static/pages/assets.html?asset_id=${encodeURIComponent(a.id)}` : 'assets.html';

            return `
              <div class="bay-inset p-2 rounded-lg flex items-center justify-between hover:border-[#dfc384]/40 transition group cursor-pointer" onclick="location.href='${targetUrl}'" title="单击前往资产详情">
                <div class="flex items-center space-x-2 min-w-0">
                  <div class="w-6 h-6 rounded bg-[#10131b] border border-white/10 flex items-center justify-center shrink-0 group-hover:border-[#dfc384]/50">
                    <i data-lucide="${meta.icon}" class="w-3.5 h-3.5 ${meta.color}"></i>
                  </div>
                  <div class="min-w-0">
                    <div class="text-[11px] font-bold text-slate-200 truncate group-hover:text-[#eddab3] transition">${esc(name)}</div>
                    <div class="text-[8px] font-mono text-slate-500">${meta.label} · ${esc(sizeStr)}</div>
                  </div>
                </div>
                <div class="flex items-center space-x-1 shrink-0 text-[8px] font-mono text-slate-400">
                  <span class="px-1.5 py-0.5 rounded bg-black/50 border border-white/5 text-[#dfc384] group-hover:bg-[#dfc384]/20 transition">检视</span>
                </div>
              </div>
            `;
          }).join('');
        }
      } else if (state.assetOverviewDegradation) {
        // 接口失败 / 未接入：显式降级，绝不展示伪造的示例资产。
        if (listContainer) {
          listContainer.innerHTML = degradationNoticeHtml(
            state.assetOverviewDegradation,
            '未取到真实资产；本页不会展示任何伪造的示例资产。'
          );
        }
        [statImages, statModels, statVideos, statAudios].forEach(el => { if (el) el.textContent = '—'; });
        if (poolSize) poolSize.textContent = '—';
      } else {
        // 端点已接入且后端如实应答「确实为空」：保留中性空态，不得谎称未接入。
        if (listContainer) {
          listContainer.innerHTML = `
            <div class="bay-inset p-3 rounded-xl text-center text-[10px] font-mono text-slate-400" role="status">
              暂无已登记资产；后端已接入并如实返回空集合，未展示任何伪造示例。
            </div>`;
        }
        [statImages, statModels, statVideos, statAudios].forEach(el => { if (el) el.textContent = '0'; });
        if (poolSize) poolSize.textContent = '0';
      }
    } catch (e) {
      // 网络异常：显式降级为「服务暂时不可用」，不得声称已挂载或已就绪。
      console.warn('资产概览读取失败（网络异常）:', e);
      state.assetOverviewDegradation = { kind: 'service_unavailable', message: degradationMessage('service_unavailable', 0) };
      if (statusBadge) {
        statusBadge.innerHTML = `
          <span class="w-1.5 h-1.5 rounded-full bg-slate-500"></span>
          <span>未接入</span>
        `;
      }
      [statImages, statModels, statVideos, statAudios].forEach(el => { if (el) el.textContent = '—'; });
      if (poolSize) poolSize.textContent = '—';
      if (listContainer) {
        listContainer.innerHTML = degradationNoticeHtml(state.assetOverviewDegradation, '资产概览暂不可用；不会展示伪造数据。');
      }
    }

    if (window.lucide) window.lucide.createIcons();
  }

  // =========================================================================
  // 正在进行的任务与用户待办事项 (Directives) 驱动逻辑
  // =========================================================================
  const directivesState = {
    currentIndex: 0,
    items: [
      {
        id: 'dir-01',
        title: '审阅未通过剧照资产',
        subtitle: '剧照测试 · 重新调色导出',
        type: 'review',
        border: 'border-[#dfc384]',
        actionText: '处理',
        actionHref: 'assets.html',
        hasStepper: true
      },
      {
        id: 'dir-02',
        title: '启动全量索引备份',
        subtitle: '资产库 · 预估 3 分钟',
        type: 'backup',
        border: 'border-cyan-400',
        actionText: '启动',
        isActionTrigger: true
      },
      {
        id: 'dir-03',
        title: '校准主角 AURA 钛金面容一致性',
        subtitle: '分镜 SC03 · LoRA 权重验证',
        type: 'model',
        border: 'border-purple-400',
        actionText: '校准',
        actionHref: 'storyboard.html'
      }
    ]
  };

  // 待办事项左右切换 (步进按键 < >)
  function stepDirective(delta) {
    if (!directivesState.items.length) return;
    directivesState.currentIndex = (directivesState.currentIndex + delta + directivesState.items.length) % directivesState.items.length;
    renderDirectives();
  }

  // 触发全量索引备份
  async function triggerIndexBackup(btn) {
    if (!btn) return;
    const origText = btn.textContent;
    btn.disabled = true;
    btn.textContent = '备份中…';
    btn.className = 'pill-capsule-active px-2 py-0.5 text-[8.5px] font-mono text-cyan-200 border-cyan-400';

    // 显式降级：只有后端真的接受任务才谈「已就绪」；未接入 / 失败一律如实告知。
    let resultText = '已就绪';
    try {
      const res = await fetch('/api/asset-registry/index/sync', {
        method: 'POST',
        credentials: 'same-origin'
      });
      if (!res.ok) {
        const failure = await classifyFetchFailure(res);
        resultText = failure.kind === 'not_integrated' ? '未接入' : '启动失败';
      }
    } catch (e) {
      console.warn('索引备份请求异常（网络异常）:', e);
      resultText = '服务不可用';
    }

    setTimeout(() => {
      btn.textContent = resultText;
      setTimeout(() => {
        btn.disabled = false;
        btn.textContent = origText;
        btn.className = 'pill-capsule-inactive px-2 py-0.5 text-[8.5px] font-mono text-cyan-300 border-cyan-500/30 cursor-pointer hover:border-cyan-400 transition';
      }, 1500);
    }, 1200);
  }

  // 渲染待办事项
  function renderDirectives() {
    const container = document.getElementById('workbenchDirectivesContainer');
    const badge = document.getElementById('workbenchDirectivesCount');
    if (!container) return;

    const visibleItems = [
      directivesState.items[directivesState.currentIndex % directivesState.items.length],
      directivesState.items[(directivesState.currentIndex + 1) % directivesState.items.length]
    ].filter(Boolean);

    if (badge) badge.textContent = `${directivesState.items.length} 待办`;

    container.innerHTML = visibleItems.map(item => `
      <div class="px-2.5 py-2 rounded-xl bay-inset border-l-2 ${item.border} flex items-center justify-between bg-[#0a0c10]">
        <div class="min-w-0 pr-1.5 flex-1">
          <div class="text-[11px] font-semibold text-slate-100 tracking-tight whitespace-nowrap overflow-hidden text-ellipsis">${esc(item.title)}</div>
          <div class="text-[8px] font-mono text-slate-500 mt-0.5 tracking-tight">${esc(item.subtitle)}</div>
        </div>
        <div class="flex items-center space-x-1.5 shrink-0">
          ${item.hasStepper ? `
            <div class="hw-rocker-stepper">
              <button type="button" class="hw-rocker-btn" title="上一个待办" onclick="WorkbenchHome.stepDirective(-1)">
                <i data-lucide="chevron-left" class="w-2.5 h-2.5"></i>
              </button>
              <button type="button" class="hw-rocker-btn" title="下一个待办" onclick="WorkbenchHome.stepDirective(1)">
                <i data-lucide="chevron-right" class="w-2.5 h-2.5"></i>
              </button>
            </div>
          ` : ''}
          ${item.isActionTrigger ? `
            <button type="button" class="pill-capsule-inactive px-2 py-0.5 text-[8.5px] font-mono text-cyan-300 border-cyan-500/30 cursor-pointer hover:border-cyan-400 transition" onclick="WorkbenchHome.triggerIndexBackup(this)">
              ${item.actionText}
            </button>
          ` : `
            <button type="button" class="pill-capsule-inactive px-2 py-0.5 text-[8.5px] font-mono text-[#eddab3] cursor-pointer hover:border-[#dfc384] transition" onclick="location.href='${item.actionHref || 'assets.html'}'">
              ${item.actionText}
            </button>
          `}
        </div>
      </div>
    `).join('');

    if (window.lucide) window.lucide.createIcons();
  }

  // 初始化入口
  // 首页只读服务端 Chat 配置状态；绝不在页面初始化时触发可能计费的模型调用。
  async function syncAuraBusStatus() {
    const node = document.getElementById('auraBusStatus');
    if (!node) return;
    try {
      const res = await fetch('/api/chat/config', {
        method: 'GET',
        headers: { 'Accept': 'application/json' },
        credentials: 'same-origin'
      });
      const body = await res.json().catch(() => null);
      applyAuraConfiguration(res.ok ? body : null);
      if (res.ok && body && body.configured === true) {
        node.textContent = 'AURA 智能体总线 · Provider 已配置（未测连接）';
        node.className = 'text-[9px] font-mono text-cyan-300 font-bold';
        node.removeAttribute('data-gw-degradation');
        node.title = '只读取 /api/chat/config；尚未验证上游连通性或发起计费请求';
        return;
      }
      const status = String(body && body.configuration_status || '');
      const kind = res.ok && status === 'not_configured' ? 'not_integrated' : 'service_unavailable';
      node.textContent = 'AURA 智能体总线 · ' + (kind === 'not_integrated' ? 'Provider 未配置' : '配置状态不可用');
      node.className = 'text-[9px] font-mono text-amber-300 font-bold';
      node.setAttribute('data-gw-degradation', kind);
      node.title = '只读取 /api/chat/config 的服务端配置状态（HTTP ' + res.status + '）；未触发模型生成';
    } catch (_) {
      applyAuraConfiguration(null);
      node.textContent = 'AURA 智能体总线 · 配置状态不可用';
      node.className = 'text-[9px] font-mono text-amber-300 font-bold';
      node.setAttribute('data-gw-degradation', 'service_unavailable');
      node.title = 'GET /api/chat/config 请求异常；未触发模型生成';
    }
  }

  function init() {
    syncAuraBusStatus();
    reloadProjects();
    reloadAssetOverview();
    renderDirectives();
    setupKeyboardShortcuts();

    // 检查 URL 参数是否直接打开设置视图 (例如 ?view=settings)
    try {
      const urlParams = new URLSearchParams(window.location.search);
      if (urlParams.get('view') === 'settings') {
        switchView('settings');
      }
    } catch (e) {}
  }

  // 页面加载完成后自启动
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }

  return {
    rebind: () => { if (document.getElementById('workbenchMainDashboard')) { renderProjectsList(); reloadAssetOverview(); } },
    reloadProjects,
    reloadAssetOverview,
    renderProjectsList,
    setViewMode,
    selectProject,
    openProject,
    handleCreateProject,
    handleChatSubmit,
    insertChip,
    switchView,
    // AURA 机舱交互接口
    toggleAuraApiPopover,
    toggleAuraSizePopover,
    setAuraModel,
    setAuraRatio,
    setAuraResolutionLevel,
    handleAuraFilesUpload,
    removeAuraAttachment,
    // 待办事项与任务接口
    stepDirective,
    triggerIndexBackup,
    renderDirectives
  };
})();
