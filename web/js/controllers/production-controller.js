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
    pipelineId: '',
    pipelineVersion: 0,
    pipeline: null,
    episodes: [],
    activeEpisodeId: '',
    activeSceneId: '',
    activeShotId: '',
    currentEpisode: null,
    currentShot: {
      id: '',
      name: '镜头数据未接入',
      imageUrl: '',
      focal: 50,
      prompt: '',
      project_id: '',
      entity_id: '',
      episode_id: '',
      scene_id: '',
      canvas_id: '',
      shot_id: '',
      job_id: '',
      asset_id: '',
      version_id: '',
      version: null,
      status: 'not_integrated',
      next_action: 'review_context',
      source: null,
      data_gaps: []
    },
    shotList: [],
    review: {
      sessionId: '',
      reviewId: '',
      assetId: '',
      versionId: '',
      version: null,
      expectedVersion: null,
      status: 'not_integrated',
      nextAction: 'review_context',
      comments: [],
      approvals: [],
      delivery: null,
      dataStatus: 'degraded',
      dataGaps: []
    },
    reviewError: ''
  };

  const esc = str => String(str ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));

  function notIntegratedMarkup(message) {
    return `<div class="bay-inset p-4 text-center text-slate-500 font-mono gw-type-body-sm rounded-xl" data-gw-degradation="not_integrated"><i data-lucide="unlink" class="w-6 h-6 mx-auto mb-1.5 text-slate-600"></i><div>${esc(message)}</div></div>`;
  }

  async function requestJson(url, options = {}) {
    const fetchImpl = (typeof window !== 'undefined' && window.fetch)
      ? window.fetch.bind(window)
      : (typeof fetch !== 'undefined' ? fetch : null);
    if (!fetchImpl) throw new Error('fetch unavailable');
    const requestOptions = { credentials: 'same-origin', ...options };
    requestOptions.headers = { Accept: 'application/json', ...(options.headers || {}) };
    if (requestOptions.body && !requestOptions.headers['Content-Type']) {
      requestOptions.headers['Content-Type'] = 'application/json';
    }
    const response = await fetchImpl(url, requestOptions);
    let payload = null;
    try { payload = await response.json(); } catch (_) { payload = null; }
    if (!response.ok) {
      const error = new Error(payload?.detail?.message || `request failed: ${response.status}`);
      error.status = response.status;
      error.code = payload?.detail?.code || '';
      error.payload = payload;
      throw error;
    }
    return payload || {};
  }

  function resetReviewState(status = 'not_integrated', nextAction = 'review_context', dataGaps = []) {
    state.review = {
      sessionId: '',
      reviewId: '',
      assetId: state.currentShot.asset_id || '',
      versionId: state.currentShot.version_id || '',
      version: state.currentShot.version ?? null,
      expectedVersion: null,
      status,
      nextAction,
      comments: [],
      approvals: [],
      delivery: null,
      dataStatus: dataGaps.length ? 'degraded' : 'ok',
      dataGaps: [...dataGaps]
    };
    state.reviewError = '';
  }

  function reviewStatusForSession(session = {}) {
    const explicit = String(session.review_status || '').trim();
    if (explicit) return explicit;
    const status = String(session.status || '').trim();
    if (status === 'approved') return 'approved';
    if (status === 'rejected') return 'changes_requested';
    if (status === 'superseded') return 'superseded';
    return status === 'delivered' || status === 'open' ? 'pending_review' : 'not_integrated';
  }

  function nextActionForReview(status, fallback = '') {
    if (fallback) return fallback;
    return {
      pending_review: 'review',
      approved: 'deliver',
      changes_requested: 'regenerate_version',
      superseded: 'review_new_version',
      failed: 'retry_generation',
      waiting: 'configure_executor',
      unknown: 'reconcile_provider',
      queued: 'poll_job',
      running: 'poll_job',
      completed: 'review_source'
    }[status] || 'review_context';
  }

  function renderNotIntegratedState() {
    state.pipelineId = '';
    state.pipelineVersion = 0;
    state.pipeline = null;
    state.episodes = [];
    state.currentEpisode = null;
    state.activeEpisodeId = '';
    state.activeSceneId = '';
    state.activeShotId = '';
    state.currentShot = {
      id: '', name: '镜头数据未接入', imageUrl: '', focal: 50, prompt: '',
      project_id: state.projectId || '', status: 'not_integrated', next_action: 'review_context', data_gaps: []
    };
    state.shotList = [];
    resetReviewState('not_integrated', 'review_context');

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

    const reviewPanel = document.getElementById('productionReviewPanel');
    if (reviewPanel) {
      reviewPanel.setAttribute('data-gw-degradation', 'not_integrated');
      reviewPanel.innerHTML = notIntegratedMarkup('镜头审阅数据未接入');
    }
  }

  // 更新页面导航栏上的链接动态设置 ?project_id；没有上下文时不伪造参数。
  function updateNavLinks(projectId) {
    const origin = (typeof window !== 'undefined' && window.location && window.location.origin && window.location.origin !== 'null')
      ? window.location.origin
      : 'http://localhost';
    document.querySelectorAll('#navPillsGroup a').forEach(a => {
      const href = a.getAttribute('href');
      if (href && !href.startsWith('http') && !href.startsWith('#')) {
        const url = new URL(href, origin);
        if (projectId) {
          url.searchParams.set('project_id', projectId);
        } else {
          url.searchParams.delete('project_id');
        }
        a.setAttribute('href', url.pathname.split('/').pop() + url.search);
      }
    });
  }

  function normalizeShot(raw, sceneId = '', episodeId = '') {
    if (!raw || typeof raw !== 'object') return null;
    const id = String(raw.shot_id || raw.id || '').trim();
    if (!id) return null;
    const source = raw.source && typeof raw.source === 'object' ? { ...raw.source } : null;
    const assetId = String(raw.asset_id || '').trim();
    const imageUrl = String(raw.imageUrl || raw.image_url || raw.img || raw.media_url || source?.source_url || '').trim();
    const status = String(raw.status || raw.review_status || 'unknown').trim().toLowerCase();
    const version = raw.version ?? raw.asset_version ?? null;
    return {
      ...raw,
      id,
      shot_id: id,
      code: String(raw.code || id).trim(),
      name: String(raw.name || raw.title || id).trim(),
      type: String(raw.type || raw.shot_type || raw.media_type || '镜头').trim(),
      desc: String(raw.desc || raw.description || raw.prompt || '').trim(),
      prompt: String(raw.prompt || '').trim(),
      imageUrl,
      img: imageUrl,
      focal: Number(raw.focal) || 50,
      episode_id: String(raw.episode_id || episodeId || '').trim(),
      scene_id: String(raw.scene_id || sceneId || '').trim(),
      project_id: String(raw.project_id || state.projectId || '').trim(),
      asset_id: assetId,
      version_id: String(raw.version_id || '').trim(),
      version,
      status,
      next_action: String(raw.next_action || nextActionForReview(status)).trim(),
      source,
      data_gaps: Array.isArray(raw.data_gaps) ? raw.data_gaps.map(item => String(item)) : []
    };
  }

  function normalizeScene(raw, episodeId = '') {
    if (!raw || typeof raw !== 'object') return null;
    const id = String(raw.scene_id || raw.id || raw.code || '').trim();
    if (!id) return null;
    const shots = Array.isArray(raw.shots)
      ? raw.shots.map(item => normalizeShot(item, id, episodeId)).filter(Boolean)
      : [];
    return {
      ...raw,
      id,
      code: String(raw.code || id).trim(),
      name: String(raw.name || raw.title || id).trim(),
      status: String(raw.status || '未接入').trim(),
      statusColor: String(raw.statusColor || 'slate').trim(),
      shotsCount: raw.shotsCount ?? shots.length,
      shots,
      expanded: raw.expanded !== false
    };
  }

  function normalizeEpisode(raw) {
    if (!raw || typeof raw !== 'object') return null;
    const id = String(raw.episode_id || raw.id || raw.code || '').trim();
    if (!id) return null;
    const scenes = Array.isArray(raw.scenes)
      ? raw.scenes.map(item => normalizeScene(item, id)).filter(Boolean)
      : [];
    return {
      ...raw,
      id,
      code: String(raw.code || id).trim(),
      name: String(raw.name || raw.title || id).trim(),
      totalShots: raw.totalShots ?? scenes.reduce((count, scene) => count + scene.shots.length, 0),
      scenes,
      episodes: Array.isArray(raw.episodes) ? raw.episodes : undefined
    };
  }

  function contextFromPipeline(pipeline) {
    const context = pipeline?.production_context && typeof pipeline.production_context === 'object'
      ? pipeline.production_context : {};
    const episodeMap = new Map();
    const rawEpisodes = Array.isArray(context.episodes)
      ? context.episodes
      : (Array.isArray(context.episode_list) ? context.episode_list : []);
    rawEpisodes.map(normalizeEpisode).filter(Boolean).forEach(episode => episodeMap.set(episode.id, episode));

    const rawShots = Array.isArray(pipeline?.shots)
      ? pipeline.shots
      : (Array.isArray(context.shots) ? context.shots : []);
    rawShots.forEach(rawShot => {
      const episodeId = String(rawShot?.episode_id || 'EP01').trim();
      const sceneId = String(rawShot?.scene_id || 'SC01').trim();
      const episode = episodeMap.get(episodeId) || normalizeEpisode({ id: episodeId, code: episodeId, name: episodeId, scenes: [] });
      if (!episode) return;
      let scene = episode.scenes.find(item => item.id === sceneId);
      if (!scene) {
        scene = normalizeScene({ id: sceneId, code: sceneId, name: sceneId, shots: [] }, episodeId);
        episode.scenes.push(scene);
      }
      const normalized = normalizeShot(rawShot, sceneId, episodeId);
      if (normalized) {
        const index = scene.shots.findIndex(item => item.id === normalized.id);
        if (index >= 0) scene.shots[index] = { ...scene.shots[index], ...normalized };
        else scene.shots.push(normalized);
      }
      scene.shotsCount = scene.shots.length;
      episode.totalShots = episode.scenes.reduce((count, item) => count + item.shots.length, 0);
      episodeMap.set(episodeId, episode);
    });

    const episodes = [...episodeMap.values()];
    const shots = episodes.flatMap(episode => episode.scenes.flatMap(scene => scene.shots));
    return { episodes, shots };
  }

  function bindPipelineContext(pipeline) {
    if (!pipeline || typeof pipeline !== 'object') return null;
    const { episodes, shots } = contextFromPipeline(pipeline);
    state.pipeline = pipeline;
    state.pipelineId = String(pipeline.pipeline_id || pipeline.id || '').trim();
    state.pipelineVersion = Number(pipeline.version) || 1;
    state.shotList = shots;
    state.episodes = episodes;
    state.currentEpisode = episodes[0] || null;
    state.activeEpisodeId = state.currentEpisode?.id || '';
    state.activeSceneId = state.currentEpisode?.scenes?.[0]?.id || '';
    state.activeShotId = state.currentEpisode?.scenes?.[0]?.shots?.[0]?.id || '';
    if (state.activeSceneId && state.activeShotId) {
      const firstShot = state.currentEpisode.scenes[0].shots[0];
      bindCurrentShot(firstShot);
    } else {
      state.currentShot = { ...state.currentShot, id: '', name: '镜头数据未接入', status: 'not_integrated', next_action: 'review_context' };
      resetReviewState('not_integrated', 'review_context');
    }
    renderSceneCatalog();
    renderContextBus();
    renderShots();
    return { episodes, shots };
  }

  function renderContextBus() {
    const container = document.querySelector('#contextBusContainer > .flex-1');
    if (!container) return;
    if (!state.episodes.length) {
      container.innerHTML = notIntegratedMarkup('项目制片数据未接入');
      return;
    }
    container.innerHTML = state.episodes.map(episode => `
      <div class="relative pl-5" data-production-context-episode="${esc(episode.id)}">
        <div class="tree-node-card ${episode.id === state.activeEpisodeId ? 'active' : ''} px-2.5 py-1.5 flex items-center justify-between group cursor-pointer" data-production-episode="${esc(episode.id)}" aria-pressed="${episode.id === state.activeEpisodeId}" onclick="WorkbenchProduction.selectEpisode('${esc(episode.id)}', this)">
          <div class="flex items-center space-x-1.5 min-w-0">
            <span class="gw-type-body-sm font-mono text-[#dfc384] font-bold px-1 py-0.5 rounded bg-[#dfc384]/15 border border-[#dfc384]/30 shrink-0">${esc(episode.code)}</span>
            <span class="gw-type-body-md font-semibold text-slate-100 truncate">${esc(episode.name)}</span>
          </div>
          <span class="gw-type-body-sm font-mono text-slate-400 shrink-0">${Number(episode.totalShots || 0)} 镜</span>
        </div>
        <div class="ml-3 pl-3 border-l border-[#dfc384]/35 space-y-2 mt-2">
          ${(episode.scenes || []).map(scene => `
            <div class="tree-node-card px-2.5 py-1.5 flex items-center justify-between gw-type-body-sm cursor-pointer ${scene.id === state.activeSceneId ? 'active' : ''}" data-production-scene="${esc(scene.id)}" aria-pressed="${scene.id === state.activeSceneId}" onclick="WorkbenchProduction.toggleSceneCard('${esc(scene.id)}')">
              <span class="text-slate-300 truncate">${esc(scene.code)} ${esc(scene.name)}</span>
              <span class="gw-type-body-sm font-mono text-slate-400 shrink-0">${Number(scene.shotsCount || 0)} 镜</span>
            </div>`).join('') || '<div class="gw-type-body-sm text-slate-500">场次数据未接入</div>'}
        </div>
      </div>`).join('');
    window.lucide?.createIcons();
  }

  async function loadShotDetail(shotId) {
    if (!state.pipelineId || !shotId) return null;
    try {
      const shotEndpoint = `/api/episode-pipelines/${encodeURIComponent(state.pipelineId)}`
        + `/shots/${encodeURIComponent(shotId)}`;
      const body = await requestJson(shotEndpoint);
      const shot = body?.shot;
      if (!shot || state.activeShotId !== shotId) return shot || null;
      const merged = normalizeShot({ ...state.currentShot, ...shot }, state.activeSceneId, state.activeEpisodeId);
      bindCurrentShot(merged);
      state.shotList = state.shotList.map(item => item.id === shotId ? { ...item, ...merged } : item);
      renderSceneCatalog();
      renderShots();
      return merged;
    } catch (error) {
      if (state.activeShotId === shotId) {
        state.currentShot = { ...state.currentShot, status: 'unknown', next_action: 'reconcile_provider', data_gaps: [error.code || 'SHOT_CONTEXT_UNAVAILABLE'] };
        resetReviewState('unknown', 'reconcile_provider', state.currentShot.data_gaps);
        renderReviewPanel();
      }
      return null;
    }
  }

  function reviewPanelElement() {
    let panel = document.getElementById('productionReviewPanel');
    if (panel) return panel;
    const title = document.getElementById('currentShotTitle');
    const host = title?.parentElement || document.getElementById('shotPromptInput')?.parentElement;
    if (!host?.insertAdjacentHTML) return null;
    host.insertAdjacentHTML('afterend', '<div id="productionReviewPanel" data-production-review-panel></div>');
    return document.getElementById('productionReviewPanel');
  }

  function reviewBinding(expectedVersion = state.review.expectedVersion) {
    const shot = state.currentShot || {};
    const binding = {
      project_id: shot.project_id || state.projectId,
      entity_id: shot.entity_id || '',
      episode_id: shot.episode_id || state.activeEpisodeId || '',
      scene_id: shot.scene_id || state.activeSceneId || '',
      shot_id: shot.shot_id || shot.id || state.activeShotId,
      canvas_id: shot.canvas_id || '',
      job_id: shot.job_id || '',
      asset_id: shot.asset_id || '',
      version_id: shot.version_id || '',
      version: shot.version ?? null,
      media_type: shot.media_type || shot.previewType || 'video',
      source: shot.source || undefined,
      version_media_url: shot.imageUrl || shot.img || shot.source?.source_url || undefined,
      expected_version: Number(expectedVersion || 1)
    };
    Object.keys(binding).forEach(key => {
      if (binding[key] === undefined || binding[key] === null || binding[key] === '') delete binding[key];
    });
    return binding;
  }

  function hydrateReview(detail = {}) {
    const session = detail.session || detail.review || {};
    const status = reviewStatusForSession(session);
    const approvalList = Array.isArray(detail.approvals) ? detail.approvals : [];
    const currentApproval = approvalList[approvalList.length - 1] || null;
    state.review = {
      sessionId: String(session.session_id || session.id || '').trim(),
      reviewId: String(session.review_id || session.session_id || session.id || '').trim(),
      assetId: String(session.asset_id || state.currentShot.asset_id || '').trim(),
      versionId: String(session.version_id || state.currentShot.version_id || '').trim(),
      version: session.asset_version ?? session.version ?? state.currentShot.version ?? null,
      expectedVersion: Number(session.expected_version ?? session.revision ?? session.version ?? detail.revision ?? 1),
      status,
      nextAction: nextActionForReview(status, String(session.next_action || currentApproval?.next_action || '').trim()),
      comments: Array.isArray(detail.comments) ? detail.comments : [],
      approvals: approvalList,
      delivery: detail.delivery || (Array.isArray(detail.deliveries) ? detail.deliveries.at(-1) : null),
      dataStatus: detail.data_status || 'ok',
      dataGaps: Array.isArray(detail.data_gaps) ? detail.data_gaps : []
    };
    state.reviewError = '';
    renderReviewPanel();
    return state.review;
  }

  async function loadReviewDetail(sessionId) {
    if (!sessionId) return null;
    const detail = await requestJson(`/api/asset-reviews/sessions/${encodeURIComponent(sessionId)}`);
    return hydrateReview(detail);
  }

  async function loadReviewForCurrentShot() {
    const shot = state.currentShot || {};
    if (!shot.asset_id || !shot.version_id) {
      const status = String(shot.status || 'not_integrated').toLowerCase();
      resetReviewState(status === 'completed' ? 'waiting' : status, nextActionForReview(status, shot.next_action || ''), shot.data_gaps || ['VERSION_NOT_READY']);
      renderReviewPanel();
      return null;
    }
    try {
      const query = new URLSearchParams({
        project_id: shot.project_id || state.projectId,
        shot_id: shot.shot_id || shot.id,
        version_id: shot.version_id
      });
      const list = await requestJson(`/api/asset-reviews/sessions?${query.toString()}`);
      const sessions = Array.isArray(list?.sessions) ? list.sessions : [];
      const matching = sessions.find(item =>
        String(item.asset_id || '') === String(shot.asset_id) &&
        (!item.version_id || String(item.version_id) === String(shot.version_id))
      );
      if (!matching) {
        state.review = {
          ...state.review,
          assetId: shot.asset_id,
          versionId: shot.version_id,
          version: shot.version ?? null,
          expectedVersion: Number(list?.revision) || 1,
          status: 'pending_review',
          nextAction: 'create_review',
          dataStatus: 'ok',
          dataGaps: []
        };
        renderReviewPanel();
        return null;
      }
      return await loadReviewDetail(matching.session_id || matching.id);
    } catch (error) {
      state.reviewError = error.code || 'ASSET_REVIEW_UNAVAILABLE';
      resetReviewState('waiting', 'configure_review', [state.reviewError]);
      renderReviewPanel();
      return null;
    }
  }

  async function ensureReviewSession() {
    if (state.review.sessionId) return state.review;
    const shot = state.currentShot || {};
    if (!shot.asset_id || !shot.version_id) {
      const error = new Error('当前镜头没有可审阅的精确版本');
      error.code = 'VERSION_NOT_READY';
      throw error;
    }
    const payload = {
      title: state.currentShot.name || shot.shot_id || shot.id,
      delivery_eligible: true,
      ...reviewBinding(state.review.expectedVersion || 1)
    };
    const created = await requestJson('/api/asset-reviews/sessions', {
      method: 'POST',
      body: JSON.stringify(payload)
    });
    const session = created?.session || {};
    hydrateReview({ ...created, session: { ...session, asset_id: session.asset_id || shot.asset_id, version_id: session.version_id || shot.version_id, version: session.version ?? shot.version } });
    return state.review;
  }

  function renderReviewPanel() {
    const panel = reviewPanelElement();
    if (!panel) return;
    const review = state.review || {};
    const shot = state.currentShot || {};
    const hasVersion = Boolean(shot.asset_id && shot.version_id);
    const blocked = !hasVersion || ['waiting', 'unknown', 'failed', 'not_integrated', 'queued', 'running'].includes(review.status);
    const terminal = ['approved', 'changes_requested', 'superseded'].includes(review.status);
    const statusLabel = {
      pending_review: '待审阅', approved: '已通过', changes_requested: '已退回修改', superseded: '旧版本已失效',
      waiting: '等待处理', unknown: '结果未知', failed: '生成失败', queued: '排队中', running: '生成中',
      not_integrated: '未接入'
    }[review.status] || review.status || '未接入';
    const nextLabel = {
      review: '请检查当前精确版本', create_review: '首次操作时建立审阅记录', deliver: '可进入交付记录',
      regenerate_version: '请重新生成新版本', configure_review: '审阅服务不可用，请检查配置', review_new_version: '请选择新版本',
      configure_executor: '请配置执行器', reconcile_provider: '请人工核对远端任务', poll_job: '请刷新读取任务状态',
      review_source: '请进入审阅', retry_generation: '可重试生成'
    }[review.nextAction] || review.nextAction || '请刷新读取';
    const comments = Array.isArray(review.comments) ? review.comments : [];
    const commentRows = comments.length
      ? comments.map(item => `<li class="gw-type-body-sm text-slate-300 border-t border-white/5 pt-1.5 mt-1.5"><span class="text-[#dfc384]">${esc(item.author_name || '审阅者')}</span>：${esc(item.body || '')}</li>`).join('')
      : '<li class="gw-type-body-sm text-slate-500">暂无评论</li>';
    panel.removeAttribute('data-gw-degradation');
    if (blocked) panel.setAttribute('data-gw-degradation', 'blocked');
    panel.innerHTML = `
      <div class="bay-inset p-3 rounded-lg border border-white/10" data-production-review-state="${esc(review.status || 'not_integrated')}">
        <div class="flex items-center justify-between gap-2">
          <span class="gw-type-body-sm font-mono text-slate-400">审阅状态</span>
          <strong class="gw-type-body-sm font-mono text-[#dfc384]">${esc(statusLabel)}</strong>
        </div>
        <div class="gw-type-body-sm font-mono text-slate-500 mt-1">版本：${esc(shot.version_id || '未接入')} · 下一步：${esc(nextLabel)}</div>
        ${state.reviewError ? `<div class="gw-type-body-sm text-amber-300 mt-1" data-gw-degradation="blocked">${esc(state.reviewError)}</div>` : ''}
        ${hasVersion && !blocked ? `
          <textarea id="productionReviewComment" rows="2" class="w-full mt-2 bg-[#0a0c10] border border-white/10 rounded-lg p-2 gw-type-body-sm font-mono text-slate-100" placeholder="记录当前精确版本的意见"></textarea>
          <div class="flex gap-1.5 mt-2">
            <button type="button" class="hw-btn btn-size-sm" onclick="WorkbenchProduction.addReviewComment()">评论</button>
            <button type="button" class="hw-btn btn-size-sm" ${terminal ? 'disabled aria-disabled="true"' : ''} onclick="WorkbenchProduction.submitReviewDecision('changes_requested')">退回修改</button>
            <button type="button" class="hw-btn btn-size-sm hw-btn-gold" ${terminal ? 'disabled aria-disabled="true"' : ''} onclick="WorkbenchProduction.submitReviewDecision('approved')">通过</button>
          </div>` : ''}
        <ul class="mt-2" data-production-review-comments>${commentRows}</ul>
      </div>`;
    window.lucide?.createIcons();
  }

  async function addReviewComment(body) {
    const text = String(body ?? document.getElementById('productionReviewComment')?.value ?? '').trim();
    if (!text) return null;
    try {
      await ensureReviewSession();
      const payload = { body: text, author_name: '制片页审阅者', reviewer_id: 'production-page', ...reviewBinding(state.review.expectedVersion) };
      const result = await requestJson(`/api/asset-reviews/sessions/${encodeURIComponent(state.review.sessionId)}/comments`, {
        method: 'POST',
        body: JSON.stringify(payload)
      });
      state.review.expectedVersion = Number(result?.comment?.expected_version) || state.review.expectedVersion;
      await loadReviewDetail(state.review.sessionId);
      return result?.comment || null;
    } catch (error) {
      state.reviewError = error.code || 'COMMENT_FAILED';
      renderReviewPanel();
      return null;
    }
  }

  async function submitReviewDecision(decision, note) {
    const normalized = decision === 'approved' ? 'approved' : (decision === 'changes_requested' ? 'changes_requested' : '');
    if (!normalized) return null;
    try {
      await ensureReviewSession();
      const payload = {
        decision: normalized,
        status: normalized,
        next_action: normalized === 'approved' ? 'deliver' : 'regenerate_version',
        note: String(note ?? document.getElementById('productionReviewComment')?.value ?? '').trim(),
        reviewer_id: 'production-page',
        ...reviewBinding(state.review.expectedVersion)
      };
      const result = await requestJson(`/api/asset-reviews/sessions/${encodeURIComponent(state.review.sessionId)}/approval`, {
        method: 'PUT',
        body: JSON.stringify(payload)
      });
      const session = result?.session || {};
      const approval = result?.approval || {};
      hydrateReview({
        session: { ...session, session_id: session.session_id || state.review.sessionId, review_id: session.review_id || state.review.reviewId, asset_id: session.asset_id || state.currentShot.asset_id, version_id: session.version_id || state.currentShot.version_id, version: session.version ?? state.currentShot.version },
        comments: state.review.comments,
        approvals: [...state.review.approvals, approval],
        delivery: state.review.delivery,
        data_status: result?.data_status || 'ok'
      });
      return result;
    } catch (error) {
      state.reviewError = error.code || 'REVIEW_DECISION_FAILED';
      renderReviewPanel();
      return null;
    }
  }

  // 2. 初始化与路由解析
  async function initRouting() {
    const params = new URLSearchParams(window.location.search);
    const pId = params.get('project_id') || localStorage.getItem('workspace_project_id') || '';
    state.projectId = pId;

    if (!pId) {
      state.projectName = '项目数据未接入';
      renderNotIntegratedState();
      updateNavLinks('');
      return;
    }

    try {
      // 先清掉 production.html 中的旧占位目录，再由真实项目接口填充。
      renderNotIntegratedState();
      const body = await requestJson(`/api/projects/${encodeURIComponent(pId)}`);
      const proj = body?.project || body;
      if (!proj || !proj.project_id) {
        state.projectName = '项目数据未接入';
        renderNotIntegratedState();
        updateNavLinks('');
        return;
      }

      state.projectId = proj.project_id;
      state.projectName = proj.name || '未命名项目';
      localStorage.setItem('workspace_project_id', proj.project_id);

      const title = document.getElementById('currentProjectDisplayTitle');
      if (title) {
        const cleanName = state.projectName.replace(/^《|》$/g, '');
        title.textContent = `《${cleanName}》· 剧集制片工坊`;
      }

      updateNavLinks(state.projectId);
      const pipelinePayload = await requestJson(`/api/episode-pipelines?project_id=${encodeURIComponent(state.projectId)}`);
      const pipelines = Array.isArray(pipelinePayload?.pipelines) ? pipelinePayload.pipelines : [];
      const pipeline = pipelines.find(item => String(item.project_id || '') === String(state.projectId)) || pipelines[0];
      if (!pipeline) {
        // 兼容仍只提供项目详情的旧页面适配器：再次读取项目资源，确保诊断面仍指向项目端点。
        // 不创建本地场次或镜头，页面继续保持明确未接入态。
        try { await requestJson(`/api/projects/${encodeURIComponent(state.projectId)}`); } catch (_) { /* 已在下方呈现降级 */ }
        resetReviewState('not_integrated', 'review_context', ['EPISODE_PIPELINE_NOT_FOUND']);
        renderReviewPanel();
        return;
      }
      const context = bindPipelineContext(pipeline);
      if (!context?.shots?.length || !state.activeShotId) {
        resetReviewState('not_integrated', 'review_context', ['SHOT_CONTEXT_NOT_FOUND']);
        renderReviewPanel();
        return;
      }
      await loadShotDetail(state.activeShotId);
      await loadReviewForCurrentShot();
    } catch (error) {
      state.projectName = '项目数据未接入';
      renderNotIntegratedState();
      updateNavLinks('');
    }
  }

  async function switchProject(targetId) {
    if (!targetId) return;
    state.projectId = targetId;
    localStorage.setItem('workspace_project_id', targetId);

    // 更新 URL
    if (typeof window !== 'undefined' && window.location) {
      const url = new URL(window.location.href);
      url.searchParams.set('project_id', targetId);
      window.history?.pushState?.({}, '', url.toString());
    }

    await initRouting();
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
    const episodePills = document.getElementById('catalogEpisodePills');
    if (episodePills) {
      episodePills.innerHTML = state.episodes.length
        ? state.episodes.map(episode => `
            <button type="button" class="hw-btn btn-size-sm ${episode.id === state.activeEpisodeId ? 'engaged' : ''} font-mono cursor-pointer ep-pill-btn" data-production-episode="${esc(episode.id)}" aria-pressed="${episode.id === state.activeEpisodeId}" onclick="WorkbenchProduction.selectEpisodeTab('${esc(episode.id)}', this)">${esc(episode.code)}</button>
          `).join('')
        : notIntegratedMarkup('剧集数据未接入');
    }
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
  async function selectCatalogShot(scId, shId) {
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
      bindCurrentShot({
        ...targetShot,
        id: targetShot.id,
        shot_id: targetShot.shot_id || targetShot.id,
        name: `${targetScene.code} · ${targetShot.code} ${targetShot.type} ${targetShot.desc}`,
        imageUrl: targetShot.imageUrl || targetShot.img || '',
        focal: targetShot.focal || 50,
        prompt: targetShot.prompt || ''
      });
    }

    renderSceneCatalog();
    if (targetShot) {
      await loadShotDetail(shId);
      await loadReviewForCurrentShot();
    }
    return state.currentShot;
  }

  // 6. 剧集分段标签切换 (EP01, EP02, EP03, EP04)
  function selectEpisodeTab(epId, btn) {
    if (!Array.isArray(state.episodes) || state.episodes.length === 0) return;
    state.activeEpisodeId = epId;
    const ep = state.episodes.find(e => e.id === epId) || state.episodes[0];
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
      const focalVal = Number(button.dataset.productionFocal ?? button.getAttribute('data-production-focal'));
      button.setAttribute('aria-pressed', String(focalVal === mm));
    });
    const imgEl = document.getElementById('mainMonitorImage');
    if (imgEl) {
      imgEl.style.transform = mm === 85 ? 'scale(1.15)' : (mm === 35 ? 'scale(0.95)' : 'scale(1)');
      imgEl.style.transition = 'transform 0.4s cubic-bezier(0.16, 1, 0.3, 1)';
    }
  }

  // 8. 触发渲染生成
  // 当前页面只消费 T2 已登记的真实镜头；缺少工作流参数时明确阻塞，绝不显示演示进度。
  function triggerGenerate() {
    // 制片页的生成能力当前未接入真实 node_info_list，因此必须显式阻塞。
    const shot = state.currentShot || {};
    const errorCode = !state.pipelineId ? 'EPISODE_PIPELINE_NOT_FOUND'
      : (!shot.id ? 'SHOT_CONTEXT_NOT_FOUND' : (!shot.workflow_id ? 'WORKFLOW_CONTEXT_NOT_READY' : ''));
    if (errorCode) {
      state.currentShot = { ...shot, status: 'waiting', next_action: 'configure_executor', data_gaps: [errorCode] };
      resetReviewState('waiting', 'configure_executor', [errorCode]);
      renderReviewPanel();
      window.alert(`镜头生成已阻塞：${errorCode}。未提交任务，也未伪造进度。`);
      return Promise.resolve(null);
    }
    // T2 的请求参数由工作流编辑器提供；制片页没有完整 node_info_list 时不猜测节点 schema。
    const message = '镜头生成参数未接入制片页：缺少真实 node_info_list，未提交任务，也未伪造进度。';
    state.currentShot = { ...shot, status: 'waiting', next_action: 'configure_executor', data_gaps: ['NODE_SCHEMA_REQUIRED'] };
    resetReviewState('waiting', 'configure_executor', ['NODE_SCHEMA_REQUIRED']);
    renderReviewPanel();
    window.alert(message);
    return Promise.resolve(null);
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
    const target = state.shotList.find(item => item.id === shId);
    if (!target) return Promise.resolve(null);
    const sceneId = target.scene_id || state.activeSceneId;
    if (sceneId) return selectCatalogShot(sceneId, shId);
    bindCurrentShot(target);
    renderShots();
    return Promise.resolve(state.currentShot);
  }

  function selectEpisode(epId, el) {
    if (el && el.parentElement && el.parentElement.parentElement) {
      el.parentElement.parentElement.querySelectorAll('.tree-node-card').forEach(card => card.classList.remove('active'));
      el.classList.add('active');
    }
    selectEpisodeTab(epId);
  }

  function bindCurrentShot(shot) {
    if (!shot) return;
    state.currentShot = {
      id: shot.id || '',
      shot_id: shot.shot_id || shot.id || '',
      name: shot.name || '未命名镜头',
      imageUrl: shot.imageUrl || shot.img || '',
      focal: Number(shot.focal) || 50,
      prompt: shot.prompt || '',
      project_id: shot.project_id || state.projectId || '',
      entity_id: shot.entity_id || '',
      episode_id: shot.episode_id || state.activeEpisodeId || '',
      scene_id: shot.scene_id || state.activeSceneId || '',
      canvas_id: shot.canvas_id || '',
      job_id: shot.job_id || '',
      workflow_id: shot.workflow_id || '',
      target: shot.target || '',
      asset_id: shot.asset_id || '',
      version_id: shot.version_id || '',
      version: shot.version ?? null,
      media_type: shot.media_type || shot.previewType || '',
      status: String(shot.status || 'unknown').toLowerCase(),
      next_action: shot.next_action || nextActionForReview(String(shot.status || '').toLowerCase()),
      source: shot.source && typeof shot.source === 'object' ? { ...shot.source } : null,
      data_gaps: Array.isArray(shot.data_gaps) ? [...shot.data_gaps] : []
    };
    resetReviewState(state.currentShot.status, state.currentShot.next_action, state.currentShot.data_gaps);

    const titleEl = document.getElementById('currentShotTitle');
    if (titleEl) titleEl.textContent = state.currentShot.name;

    const imgEl = document.getElementById('mainMonitorImage');
    if (imgEl) {
      if (state.currentShot.imageUrl) {
        imgEl.src = state.currentShot.imageUrl;
        imgEl.alt = state.currentShot.name;
        imgEl.style.display = 'block';
        imgEl.removeAttribute('data-gw-degradation');
        if (state.currentShot.asset_id) {
          imgEl.setAttribute('data-asset-id', state.currentShot.asset_id);
        } else {
          imgEl.removeAttribute('data-asset-id');
        }
        const monitorFrame = imgEl.parentElement;
        const emptyPlaceholder = monitorFrame?.querySelector?.('[data-production-empty-state]');
        if (emptyPlaceholder) {
          if (typeof emptyPlaceholder.remove === 'function') {
            emptyPlaceholder.remove();
          } else if (emptyPlaceholder.parentElement) {
            emptyPlaceholder.parentElement.removeChild(emptyPlaceholder);
          }
        }
      } else {
        imgEl.removeAttribute('src');
        imgEl.style.display = 'none';
        imgEl.setAttribute('data-gw-degradation', 'not_integrated');
        imgEl.alt = '镜头预览未接入';
        const monitorFrame = imgEl.parentElement;
        if (monitorFrame && !monitorFrame.querySelector('[data-production-empty-state]')) {
          monitorFrame.insertAdjacentHTML('beforeend', `<div data-production-empty-state>${notIntegratedMarkup('镜头预览未接入')}</div>`);
        }
      }
    }

    const promptInput = document.getElementById('shotPromptInput');
    if (promptInput) {
      promptInput.value = state.currentShot.prompt;
    }

    setFocal(state.currentShot.focal);
    renderReviewPanel();
  }

  function bindProductionContext(data) {
    if (!data) return;
    if (data.pipeline) {
      bindPipelineContext(data.pipeline);
    }
    if (data.project) {
      const proj = data.project;
      state.projectId = proj.project_id || proj.id || state.projectId;
      state.projectName = proj.name || state.projectName;
      if (state.projectId) localStorage.setItem('workspace_project_id', state.projectId);
      const title = document.getElementById('currentProjectDisplayTitle');
      if (title) {
        const cleanName = state.projectName.replace(/^《|》$/g, '');
        title.textContent = `《${cleanName}》· 剧集制片工坊`;
      }
      updateNavLinks(state.projectId);
    }
    if (Array.isArray(data.episodes)) {
      state.episodes = data.episodes.map(normalizeEpisode).filter(Boolean);
      state.currentEpisode = state.episodes[0] || null;
      state.activeEpisodeId = state.currentEpisode?.id || '';
      renderSceneCatalog();
      renderContextBus();
    }
    if (Array.isArray(data.shots)) {
      state.shotList = data.shots.map(item => normalizeShot(item)).filter(Boolean);
      renderShots();
    }
    if (data.currentShot) {
      bindCurrentShot(data.currentShot);
    }
    window.lucide?.createIcons();
  }

  async function reloadBus() {
    await initRouting();
    renderSceneCatalog();
    renderShots();
    window.lucide?.createIcons();
  }

  async function init() {
    await initRouting();
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
    renderShots,
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
    bindCurrentShot,
    bindProductionContext,
    loadReviewForCurrentShot,
    addReviewComment,
    submitReviewDecision,
    renderReviewPanel,
    renderNotIntegratedState,
    getState: () => ({ ...state }),
    rebind: reloadBus
  };
})();
