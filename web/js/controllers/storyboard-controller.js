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
    pipelineId: '',
    pipelineVersion: 0,
    pipeline: null,
    scenes: [],
    shots: [],
    reviews: {},
    reviewRevision: 1,
    reviewError: '',
    actionBlocker: null,
    timelineManifest: null
  };

  const esc = str => String(str ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));

  async function requestJson(url, options = {}) {
    const requestOptions = { credentials: 'same-origin', ...options };
    requestOptions.headers = { Accept: 'application/json', ...(options.headers || {}) };
    if (requestOptions.body && !requestOptions.headers['Content-Type']) {
      requestOptions.headers['Content-Type'] = 'application/json';
    }
    const response = await fetch(url, requestOptions);
    let payload = null;
    try { payload = await response.json(); } catch (_) { payload = null; }
    if (!response || !response.ok) {
      const error = new Error(payload?.detail?.message || `storyboard request failed: ${response?.status || 'network'}`);
      error.status = response?.status || 0;
      error.code = payload?.detail?.code || '';
      error.payload = payload;
      throw error;
    }
    return payload || {};
  }

  function resolveProjectId() {
    const params = new URLSearchParams(window.location.search);
    const fromUrl = String(params.get('project_id') || '').trim();
    const fromStorage = String(window.localStorage?.getItem('workspace_project_id') || '').trim();
    const projectId = fromUrl || fromStorage;
    if (projectId && fromUrl) window.localStorage?.setItem('workspace_project_id', projectId);
    return projectId;
  }

  function normalizeShot(asset, { allowMissingAsset = false } = {}) {
    if (!asset || typeof asset !== 'object') return null;
    const metadata = asset.metadata && typeof asset.metadata === 'object' ? asset.metadata : {};
    const metadataSource = metadata.source_context && typeof metadata.source_context === 'object'
      ? metadata.source_context : {};
    const source = asset.source && typeof asset.source === 'object' ? { ...asset.source } : metadataSource;
    const assetId = String(asset.asset_id || (!allowMissingAsset ? asset.id : '') || '').trim();
    const shotId = String(asset.shot_id || source.shot_id || asset.id || asset.asset_id || '').trim();
    if ((!allowMissingAsset && !assetId) || !shotId) return null;
    const sceneId = String(asset.scene_id || source.scene_id || metadata.scene_id || '').trim();
    const episodeId = String(asset.episode_id || source.episode_id || metadata.episode_id || '').trim();
    const name = String(asset.name || asset.display_name || asset.title || asset.code || shotId).trim();
    const directMediaUrl = String(asset.url || asset.media_url || asset.imageUrl || asset.img || source.source_url || '').trim();
    const declaredMediaType = String(asset.media_type || asset.previewType || metadata.media_type || '').trim();
    const kind = String(asset.kind || asset.asset_type || asset.shot_type || asset.shotType || metadata.media_type || '媒体资产').trim();
    const mediaType = String(declaredMediaType || (directMediaUrl ? 'image' : kind)).trim().toLowerCase();
    const isImage = mediaType === 'image' || mediaType.startsWith('image/');
    const isVideo = mediaType === 'video' || mediaType.startsWith('video/');
    const imageUrl = (isImage || isVideo)
      ? (directMediaUrl || (assetId ? `/api/asset-registry/assets/${encodeURIComponent(assetId)}/media` : ''))
      : '';
    const prompt = String(asset.prompt || asset.dialogue || metadata.prompt || '').trim();
    const status = String(asset.status || metadata.status || 'unknown').trim().toLowerCase();
    const blocker = asset.blocker && typeof asset.blocker === 'object'
      ? { ...asset.blocker } : (metadata.blocker && typeof metadata.blocker === 'object' ? { ...metadata.blocker } : null);
    const recovery = asset.recovery && typeof asset.recovery === 'object'
      ? { ...asset.recovery } : (metadata.recovery && typeof metadata.recovery === 'object' ? { ...metadata.recovery } : null);
    return {
      ...asset,
      id: shotId,
      asset_id: assetId,
      shot_id: shotId,
      code: String(asset.code || shotId).trim(),
      scene_id: sceneId,
      episode_id: episodeId,
      project_id: String(asset.project_id || source.project_id || state.projectId || '').trim(),
      name,
      title: name,
      imageUrl,
      img: imageUrl,
      previewType: isVideo ? 'video' : (isImage ? 'image' : ''),
      prompt,
      dialogue: prompt,
      shotType: kind,
      duration: asset.duration ?? metadata.duration ?? '',
      status,
      next_action: String(asset.next_action || metadata.next_action || 'review_context').trim(),
      job_id: String(asset.job_id || asset.task_id || metadata.job_id || '').trim(),
      version_id: String(asset.version_id || metadata.version_id || '').trim(),
      version: asset.version ?? asset.asset_version ?? metadata.version ?? null,
      media_type: String(asset.media_type || asset.previewType || metadata.media_type || (isImage ? 'image' : (isVideo ? 'video' : ''))).trim(),
      data_status: String(asset.data_status || metadata.data_status || '').trim(),
      closure_status: String(asset.closure_status || metadata.closure_status || '').trim(),
      blocker,
      recovery,
      expected_version: asset.expected_version ?? metadata.expected_version ?? null,
      source,
      data_gaps: Array.isArray(asset.data_gaps) ? asset.data_gaps.map(item => String(item)) : []
    };
  }

  function normalizePipelineShot(raw) {
    if (!raw || typeof raw !== 'object') return null;
    return normalizeShot({
      ...raw,
      id: raw.shot_id || raw.id,
      asset_id: raw.asset_id || '',
      shot_id: raw.shot_id || raw.id,
      name: raw.name || raw.title || raw.shot_id || raw.id,
      media_url: raw.media_url || raw.image_url || raw.source?.source_url || '',
      media_type: raw.media_type || raw.type || '',
      source: raw.source || {}
    }, { allowMissingAsset: true });
  }

  function contactSheetApi() {
    return window.WorkbenchContactSheet && typeof window.WorkbenchContactSheet === 'object'
      ? window.WorkbenchContactSheet : {};
  }

  function takesForShot(shot) {
    const collect = contactSheetApi().collectTakes;
    if (typeof collect === 'function') return collect(shot);
    if (!shot || typeof shot !== 'object' || (!shot.asset_id && !shot.version_id)) return [];
    return [{
      ...shot,
      asset_id: String(shot.asset_id || '').trim(),
      version_id: String(shot.version_id || '').trim(),
      version: Number(shot.version) || 1,
      take_id: String(shot.take_id || shot.version_id || shot.asset_id || shot.id || '').trim(),
      versionLabel: `v${String(Number(shot.version) || 1).padStart(3, '0')}`,
      source: String(shot.imageUrl || shot.img || shot.source?.source_url || '').trim(),
      media_url: String(shot.imageUrl || shot.img || shot.source?.source_url || '').trim(),
      primary: true
    }];
  }

  function primaryTakeForShot(shot) {
    const takes = takesForShot(shot);
    return takes.find(take => take.primary) || takes[0] || null;
  }

  function takeSource(take, duration) {
    const source = String(take?.source || take?.media_url || take?.url || '').trim();
    if (!source) return '';
    return source.includes('#t=') ? source : `${source}#t=0,${duration}`;
  }

  function applyPrimaryTake(shot, take) {
    if (!shot || !take) return shot;
    const takeId = String(take.take_id || '').trim();
    const normalizedTakes = takesForShot(shot).map(item => ({
      ...item,
      primary: String(item.take_id || '') === takeId
    }));
    shot.takes = normalizedTakes;
    shot.primary_take_id = takeId;
    shot.asset_id = String(take.asset_id || '').trim();
    shot.version_id = String(take.version_id || '').trim();
    shot.version = take.version ?? shot.version;
    shot.imageUrl = String(take.source || take.media_url || '').trim();
    shot.img = shot.imageUrl;
    shot.media_url = shot.imageUrl;
    shot.media_type = String(take.media_type || shot.media_type || 'video').trim();
    shot.previewType = shot.media_type.startsWith('video') ? 'video' : shot.previewType;
    const review = state.reviews[shot.id];
    if (review) {
      review.assetId = shot.asset_id;
      review.versionId = shot.version_id;
      review.version = shot.version;
      review.status = 'approved';
      review.nextAction = 'deliver';
    }
    return shot;
  }

  function takeById(shot, takeId) {
    const requested = String(takeId || '').trim();
    if (!requested) return null;
    return takesForShot(shot).find(take => take.take_id === requested
      || take.asset_id === requested || take.version_id === requested) || null;
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

  function scenesFromPipeline(pipeline, shots) {
    const grouped = new Map();
    const episodes = Array.isArray(pipeline?.production_context?.episodes)
      ? pipeline.production_context.episodes : [];
    episodes.forEach(episode => {
      const scenes = Array.isArray(episode?.scenes) ? episode.scenes : [];
      scenes.forEach(raw => {
        const id = String(raw?.scene_id || raw?.id || '').trim();
        if (!id) return;
        const contextShots = Array.isArray(raw.shots) ? raw.shots : [];
        grouped.set(id, {
          id,
          episode_id: String(raw.episode_id || episode.id || '').trim(),
          title: String(raw.title || raw.name || raw.code || id).trim(),
          code: String(raw.code || id).trim(),
          shots: contextShots.length,
          shotIds: new Set(contextShots.map(item => String(item?.shot_id || item?.id || '').trim()).filter(Boolean)),
          active: grouped.size === 0
        });
      });
    });
    shots.forEach(shot => {
      const id = String(shot.scene_id || '').trim();
      if (!id) return;
      const scene = grouped.get(id) || {
        id,
        episode_id: String(shot.episode_id || '').trim(),
        title: id,
        code: id,
        shots: 0,
        shotIds: new Set(),
        active: grouped.size === 0
      };
      const shotId = String(shot.shot_id || shot.id || '').trim();
      if (!scene.shotIds.has(shotId)) {
        scene.shots += 1;
        if (shotId) scene.shotIds.add(shotId);
      }
      grouped.set(id, scene);
    });
    return [...grouped.values()].map(({ shotIds, ...scene }) => scene);
  }

  // 只有真正消费项目上下文的页面才携带参数（对齐 BEHAVIOR-SPEC-PRODUCTION-PROJECTS-HUB.md 4.5）。
  const PROJECT_CONTEXT_PAGES = new Set([
    'workshop.html', 'production.html', 'storyboard.html', 'canvas.html',
    'assets.html', 'agents.html', 'workflow.html', 'collab.html'
  ]);

  // 地址栏只校正、不注入：已有 project_id 时保持与页面实际项目一致。
  function syncAddressBarProject(projectId) {
    try {
      const url = new URL(window.location.href);
      if (!url.searchParams.has('project_id')) return;
      const current = url.searchParams.get('project_id') || '';
      if (current === projectId) return;
      if (projectId) url.searchParams.set('project_id', projectId);
      else url.searchParams.delete('project_id');
      window.history.replaceState({}, '', url);
    } catch (_) {}
  }

  function updateProjectLinks() {
    syncAddressBarProject(state.projectId);
    document.querySelectorAll?.('a[href]').forEach(link => {
      const href = link.getAttribute('href');
      if (!href || href.startsWith('http') || href.startsWith('#')) return;
      const url = new URL(href, window.location.origin);
      const page = url.pathname.split('/').pop();
      if (state.projectId && PROJECT_CONTEXT_PAGES.has(page)) {
        url.searchParams.set('project_id', state.projectId);
      } else {
        url.searchParams.delete('project_id');
      }
      link.setAttribute('href', `${page}${url.search}`);
    });
  }

  function notIntegratedMarkup(message) {
    return `<div class="col-span-full bay-inset p-8 text-center text-slate-500 font-mono gw-type-body-sm rounded-xl" data-gw-degradation="not_integrated"><i data-lucide="unlink" class="w-6 h-6 mx-auto mb-1.5 text-slate-600"></i><div>${esc(message)}</div></div>`;
  }

  function actionBlockerMarkup() {
    const blocker = state.actionBlocker;
    if (!blocker) return '';
    return `<div class="col-span-full bay-inset p-3 mb-2 rounded-xl border border-amber-300/30 text-amber-200 font-mono gw-type-body-sm" data-gw-degradation="blocked" data-storyboard-blocker="${esc(blocker.code)}"><strong>动作已阻塞：${esc(blocker.code)}</strong><div class="mt-1">${esc(blocker.message)}</div><div class="mt-1 text-slate-400">下一步：${esc(blocker.next_action)}</div></div>`;
  }

  function blockStoryboardAction(code, message, nextAction = 'review_context', shot = null) {
    const subject = {};
    const current = shot || state.shots[0] || {};
    if (state.projectId) subject.project_id = state.projectId;
    if (current.entity_id) subject.entity_id = current.entity_id;
    if (current.scene_id) subject.scene_id = current.scene_id;
    if (current.shot_id || current.id) subject.shot_id = current.shot_id || current.id;
    if (current.asset_id) subject.asset_id = current.asset_id;
    if (current.version_id) subject.version_id = current.version_id;
    state.actionBlocker = {
      closure_status: 'blocked',
      blocker: {
        code,
        scope: current.asset_id ? 'asset' : 'shot',
        subject,
        message,
        retryable: false,
        observed_at: new Date().toISOString()
      },
      code,
      message,
      next_action: nextAction
    };
    renderGrid();
    window.alert?.(`分镜动作已阻塞：${code}。${message} 下一步：${nextAction}。`);
    return null;
  }

  function shotStatusPresentation(shot) {
    const status = String(shot?.status || '').trim().toLowerCase();
    const labels = {
      submitting: '提交中', queued: '排队中', running: '生成中', generating: '渲染中', ready: '已读取',
      completed: '已完成', succeeded: '已完成', success: '已完成', failed: '生成失败',
      waiting: '等待处理', unknown: '结果未知', cancelled: '已取消', canceled: '已取消',
      not_integrated: '未接入'
    };
    if (shot?.data_status === 'not_integrated' || status === 'not_integrated') {
      return { label: '未接入', degraded: true };
    }
    if (labels[status]) return { label: labels[status], degraded: false };
    return { label: '状态未接入', degraded: true };
  }

  function reviewStatusForSession(session = {}) {
    const explicit = String(session.review_status || '').trim();
    if (explicit) return explicit;
    const status = String(session.status || '').trim();
    if (status === 'approved') return 'approved';
    if (status === 'rejected') return 'changes_requested';
    if (status === 'superseded') return 'superseded';
    return status === 'open' || status === 'delivered' ? 'pending_review' : 'not_integrated';
  }

  function nextActionForReview(status, fallback = '') {
    if (fallback) return fallback;
    return {
      pending_review: 'review',
      approved: 'deliver',
      changes_requested: 'regenerate_version',
      superseded: 'review_new_version',
      failed: 'retry_generation',
      waiting: 'configure_review',
      unknown: 'reconcile_provider',
      queued: 'poll_job',
      running: 'poll_job',
      completed: 'review_source'
    }[status] || 'review_context';
  }

  function defaultReviewForShot(shot) {
    const hasVersion = Boolean(shot?.asset_id && shot?.version_id);
    return {
      sessionId: '',
      reviewId: '',
      assetId: String(shot?.asset_id || '').trim(),
      shotId: String(shot?.shot_id || shot?.id || '').trim(),
      versionId: String(shot?.version_id || '').trim(),
      version: shot?.version ?? null,
      expectedVersion: Number(state.reviewRevision) || 1,
      status: hasVersion ? 'pending_review' : 'not_integrated',
      nextAction: hasVersion ? 'create_review' : 'review_context',
      comments: [],
      approvals: [],
      delivery: null,
      dataStatus: 'ok',
      dataGaps: hasVersion ? [] : ['VERSION_NOT_READY'],
      supersededVersionId: ''
    };
  }

  function hydrateReview(shot, detail = {}, fallback = {}) {
    const session = detail.session || detail.review || detail;
    const status = reviewStatusForSession(session);
    const approvalList = Array.isArray(detail.approvals) ? detail.approvals : [];
    const review = {
      ...defaultReviewForShot(shot),
      ...fallback,
      sessionId: String(session.session_id || session.id || fallback.sessionId || '').trim(),
      reviewId: String(session.review_id || session.session_id || session.id || fallback.reviewId || '').trim(),
      assetId: String(session.asset_id || shot?.asset_id || '').trim(),
      shotId: String(session.shot_id || shot?.shot_id || shot?.id || '').trim(),
      versionId: String(session.version_id || shot?.version_id || '').trim(),
      version: session.asset_version ?? session.version ?? shot?.version ?? null,
      expectedVersion: Number(session.expected_version ?? session.revision ?? detail.revision ?? fallback.expectedVersion ?? state.reviewRevision ?? 1),
      status,
      nextAction: nextActionForReview(status, String(session.next_action || fallback.nextAction || '').trim()),
      comments: Array.isArray(detail.comments) ? detail.comments : (fallback.comments || []),
      approvals: approvalList.length ? approvalList : (fallback.approvals || []),
      delivery: detail.delivery || fallback.delivery || null,
      dataStatus: detail.data_status || fallback.dataStatus || 'ok',
      dataGaps: Array.isArray(detail.data_gaps) ? detail.data_gaps : (fallback.dataGaps || []),
      supersededVersionId: fallback.supersededVersionId || ''
    };
    return review;
  }

  function reviewBinding(shot, expectedVersion) {
    const binding = {
      project_id: shot.project_id || state.projectId,
      entity_id: shot.entity_id || '',
      episode_id: shot.episode_id || '',
      scene_id: shot.scene_id || '',
      shot_id: shot.shot_id || shot.id,
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

  function shotForId(shotId) {
    return state.shots.find(item => String(item.id) === String(shotId) || String(item.shot_id) === String(shotId)) || null;
  }

  async function loadReviewDetail(shotId, sessionId, fallback = {}) {
    const shot = shotForId(shotId);
    if (!shot || !sessionId) return null;
    const detail = await requestJson(`/api/asset-reviews/sessions/${encodeURIComponent(sessionId)}`);
    const review = hydrateReview(shot, detail, fallback);
    state.reviews[shot.id] = review;
    renderGrid();
    return review;
  }

  async function loadReviewSessions(projectId = state.projectId) {
    state.reviews = {};
    state.reviewError = '';
    state.shots.forEach(shot => { state.reviews[shot.id] = defaultReviewForShot(shot); });
    if (!projectId || state.shots.length === 0) {
      renderGrid();
      return state.reviews;
    }
    try {
      const query = new URLSearchParams({ project_id: projectId });
      const payload = await requestJson(`/api/asset-reviews/sessions?${query.toString()}`);
      state.reviewRevision = Number(payload?.revision) || 1;
      const sessions = Array.isArray(payload?.sessions) ? payload.sessions : [];
      const detailLoads = [];
      state.shots.forEach(shot => {
        const matching = sessions.find(item =>
          String(item.asset_id || '') === String(shot.asset_id || '') &&
          String(item.shot_id || '') === String(shot.shot_id || shot.id) &&
          String(item.version_id || '') === String(shot.version_id || '')
        );
        if (matching) {
          state.reviews[shot.id] = hydrateReview(shot, matching);
          if (matching.session_id || matching.id) {
            detailLoads.push(loadReviewDetail(shot.id, matching.session_id || matching.id, state.reviews[shot.id]));
          }
          return;
        }
        const previous = sessions.find(item =>
          String(item.asset_id || '') === String(shot.asset_id || '') &&
          String(item.shot_id || '') === String(shot.shot_id || shot.id) &&
          String(item.version_id || '') !== String(shot.version_id || '')
        );
        if (previous) {
          state.reviews[shot.id] = {
            ...defaultReviewForShot(shot),
            nextAction: 'review_new_version',
            supersededVersionId: String(previous.version_id || '').trim()
          };
        }
      });
      await Promise.all(detailLoads);
    } catch (error) {
      state.reviewError = error.code || 'ASSET_REVIEW_UNAVAILABLE';
      state.shots.forEach(shot => {
        state.reviews[shot.id] = {
          ...defaultReviewForShot(shot),
          status: shot.version_id ? 'waiting' : 'not_integrated',
          nextAction: shot.version_id ? 'configure_review' : 'review_context',
          dataStatus: 'degraded',
          dataGaps: [state.reviewError]
        };
      });
    }
    renderGrid();
    return state.reviews;
  }

  async function ensureReviewSession(shotId) {
    const shot = shotForId(shotId);
    if (!shot) throw Object.assign(new Error('镜头不存在'), { code: 'SHOT_NOT_FOUND' });
    const review = state.reviews[shot.id] || defaultReviewForShot(shot);
    if (review.sessionId) return review;
    if (!shot.asset_id || !shot.version_id || !shot.version) {
      throw Object.assign(new Error('当前镜头没有可审阅的精确版本'), { code: 'VERSION_NOT_READY' });
    }
    const created = await requestJson('/api/asset-reviews/sessions', {
      method: 'POST',
      body: JSON.stringify({
        title: shot.name || shot.shot_id || shot.id,
        delivery_eligible: true,
        ...reviewBinding(shot, review.expectedVersion || state.reviewRevision || 1)
      })
    });
    const hydrated = hydrateReview(shot, created, review);
    state.reviews[shot.id] = hydrated;
    renderGrid();
    return hydrated;
  }

  async function addReviewComment(shotId, body) {
    const shot = shotForId(shotId);
    const text = String(body ?? document.getElementById(`storyboardReviewComment-${shotId}`)?.value ?? '').trim();
    if (!shot || !text) return null;
    try {
      const review = await ensureReviewSession(shot.id);
      const result = await requestJson(`/api/asset-reviews/sessions/${encodeURIComponent(review.sessionId)}/comments`, {
        method: 'POST',
        body: JSON.stringify({
          body: text,
          author_name: '分镜页审阅者',
          reviewer_id: 'storyboard-page',
          ...reviewBinding(shot, review.expectedVersion)
        })
      });
      state.reviews[shot.id].expectedVersion = Number(result?.comment?.expected_version) || review.expectedVersion;
      await loadReviewDetail(shot.id, review.sessionId, state.reviews[shot.id]);
      return result?.comment || null;
    } catch (error) {
      state.reviewError = error.code || 'COMMENT_FAILED';
      renderGrid();
      return null;
    }
  }

  async function submitReviewDecision(shotId, decision, note) {
    const normalized = decision === 'approved' ? 'approved' : (decision === 'changes_requested' ? 'changes_requested' : '');
    if (!normalized) return null;
    const shot = shotForId(shotId);
    if (!shot) return null;
    try {
      const review = await ensureReviewSession(shot.id);
      const result = await requestJson(`/api/asset-reviews/sessions/${encodeURIComponent(review.sessionId)}/approval`, {
        method: 'PUT',
        body: JSON.stringify({
          decision: normalized,
          status: normalized,
          next_action: normalized === 'approved' ? 'deliver' : 'regenerate_version',
          note: String(note ?? document.getElementById(`storyboardReviewComment-${shotId}`)?.value ?? '').trim(),
          author_name: '分镜页审阅者',
          reviewer_id: 'storyboard-page',
          ...reviewBinding(shot, review.expectedVersion)
        })
      });
      const session = result?.session || {};
      state.reviews[shot.id] = hydrateReview(shot, {
        ...result,
        session: {
          ...session,
          session_id: session.session_id || review.sessionId,
          review_id: session.review_id || review.reviewId,
          asset_id: session.asset_id || shot.asset_id,
          shot_id: session.shot_id || shot.shot_id || shot.id,
          version_id: session.version_id || shot.version_id,
          version: session.version ?? shot.version
        },
        comments: review.comments,
        approvals: [...review.approvals, result?.approval].filter(Boolean),
        delivery: review.delivery,
        data_status: result?.data_status || 'ok'
      });
      renderGrid();
      return result;
    } catch (error) {
      state.reviewError = error.code || 'REVIEW_DECISION_FAILED';
      renderGrid();
      return null;
    }
  }

  function renderNotIntegratedState() {
    state.pipelineId = '';
    state.pipelineVersion = 0;
    state.pipeline = null;
    state.shots = [];
    state.scenes = [];
    state.reviews = {};
    state.reviewError = '';
    state.actionBlocker = null;
    state.timelineManifest = null;
    setTimelineSyncStatus('时间线未接入', 'not_integrated');
    renderNav();
    renderGrid();
  }

  function renderGrid() {
    const grid = document.getElementById('storyboardGrid');
    if (!grid) return;
    const titleEl = document.getElementById('storyboardTitle');

    if (!state.shots || state.shots.length === 0) {
      if (titleEl) titleEl.textContent = '分镜数据未接入 (共 0 个分镜)';
      grid.innerHTML = `${actionBlockerMarkup()}${notIntegratedMarkup('分镜数据未接入')}`;
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

    grid.innerHTML = `${actionBlockerMarkup()}${state.shots.map((sh, idx) => {
      const imageUrl = String(sh.imageUrl || sh.img || '').trim();
      const takes = takesForShot(sh);
      const title = sh.name || sh.title || sh.id;
      const description = sh.prompt || sh.dialogue || '文本提示未接入';
      const review = state.reviews[sh.id] || defaultReviewForShot(sh);
      const hasVersion = Boolean(sh.asset_id && sh.version_id && sh.version);
      const terminal = ['approved', 'changes_requested', 'superseded'].includes(review.status);
      const blocked = !hasVersion || ['waiting', 'unknown', 'failed', 'not_integrated', 'queued', 'running'].includes(review.status);
      const statusView = shotStatusPresentation(sh);
      const nextActionKey = (!hasVersion || review.status === 'not_integrated')
        ? (String(sh.next_action || '').trim() || review.nextAction)
        : review.nextAction;
      const reviewLabel = {
        pending_review: '待审阅', approved: '已通过', changes_requested: '已退回修改', superseded: '旧版本已失效',
        waiting: '等待处理', unknown: '结果未知', failed: '生成失败', queued: '排队中', running: '生成中',
        not_integrated: '未接入'
      }[review.status] || review.status || '未接入';
      const nextLabel = {
        review: '检查当前版本', create_review: '首次操作时建立审阅', deliver: '可进入交付记录',
        regenerate_version: '请重新生成新版本', review_new_version: '请审阅新版本', configure_review: '检查审阅服务',
        reconcile_provider: '人工核对任务', review_source: '进入审阅', poll_job: '读取任务状态',
        collect_asset: '等待资产登记', configure_executor: '配置真实执行器', review_context: '检查真实上下文',
        inspect_persisted_state: '核对持久化状态', wait_for_provider: '等待 Provider',
        reconcile_unknown_job: '人工核对未知任务', resolve_permission: '修复权限后重试'
      }[nextActionKey] || nextActionKey || '刷新读取';
      const preview = imageUrl && sh.previewType === 'video'
        ? `<video src="${esc(imageUrl)}" class="w-full h-full object-cover" controls preload="metadata" aria-label="${esc(title)}"></video>`
        : imageUrl
          ? `<img src="${esc(imageUrl)}" class="w-full h-full object-cover group-hover:scale-105 transition duration-300" alt="${esc(title)}" loading="lazy">`
        : `<div class="w-full h-full flex items-center justify-center text-slate-500 font-mono gw-type-body-sm" data-gw-degradation="not_integrated">媒体预览未接入</div>`;
      const contactSheetMarkup = takes.length > 1 && typeof contactSheetApi().render === 'function'
         ? contactSheetApi().render(takes, sh.id) : '';
       const blockerMarkup = sh.blocker && typeof sh.blocker === 'object'
        ? `<div class="text-amber-300 mt-1" data-gw-degradation="blocked">阻塞：${esc(sh.blocker.code || 'UNKNOWN_BLOCKER')} · ${esc(sh.blocker.message || '请核对当前事实')}<br>下一步：${esc(sh.next_action || 'review_context')}</div>` : '';
      return `
      <div class="champagne-card p-2.5 flex flex-col justify-between group transition hover:border-[#dfc384]/50" data-storyboard-shot="${esc(sh.id)}" data-asset-id="${esc(sh.asset_id || '')}" data-shot-version-id="${esc(sh.version_id || '')}" data-closure-status="${esc(sh.closure_status || '')}" data-review-status="${esc(review.status || 'not_integrated')}">
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
              <span class="${['generating', 'running'].includes(String(sh.status || '').toLowerCase()) ? 'text-amber-300 animate-pulse' : (statusView.degraded ? 'text-slate-400' : 'text-emerald-400')}"${statusView.degraded ? ' data-gw-degradation="not_integrated"' : ''}>
                ${esc(statusView.label)}
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

          ${contactSheetMarkup}

           <!-- 台词对白与叙事意图 -->
          <div class="gw-type-body-md font-bold text-slate-100 mb-1 truncate">${esc(title)}</div>
          <div class="gw-type-body-sm text-slate-300 bg-black/40 p-1.5 rounded-lg border border-white/5 leading-relaxed font-body mb-2">
            ${esc(description)}
          </div>
          <div class="bay-inset p-2 rounded-lg border border-white/10 font-mono gw-type-body-sm" data-storyboard-review-state="${esc(review.status || 'not_integrated')}">
            <div class="flex items-center justify-between gap-2">
              <span class="text-slate-400">精确版本</span>
              <strong class="text-[#dfc384]" data-storyboard-version>${esc(sh.version_id || '未接入')}</strong>
            </div>
            <div class="flex items-center justify-between gap-2 mt-1">
              <span class="text-slate-400">审阅状态</span>
              <strong class="text-cyan-300" data-storyboard-review-status>${esc(reviewLabel)}</strong>
            </div>
            <div class="text-slate-500 mt-1">下一步：${esc(nextLabel)}</div>
            ${blockerMarkup}
            ${review.supersededVersionId ? `<div class="text-amber-300 mt-1" data-gw-degradation="blocked">旧版本 ${esc(review.supersededVersionId)} 已失效</div>` : ''}
            ${state.reviewError ? `<div class="text-amber-300 mt-1" data-gw-degradation="blocked">${esc(state.reviewError)}</div>` : ''}
            ${hasVersion && !blocked && !terminal ? `
              <textarea id="storyboardReviewComment-${esc(sh.id)}" rows="2" class="w-full mt-2 bg-[#0a0c10] border border-white/10 rounded-lg p-2 text-slate-100" placeholder="记录当前精确版本意见"></textarea>
              <div class="flex gap-1.5 mt-2">
                <button type="button" class="hw-btn hw-btn-sm" onclick="WorkbenchStoryboard.addReviewComment('${esc(sh.id)}')">评论</button>
                <button type="button" class="hw-btn hw-btn-sm" onclick="WorkbenchStoryboard.submitReviewDecision('${esc(sh.id)}', 'changes_requested')">退回修改</button>
                <button type="button" class="hw-btn hw-btn-sm hw-btn-gold" onclick="WorkbenchStoryboard.submitReviewDecision('${esc(sh.id)}', 'approved')">通过</button>
              </div>` : ''}
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
    }).join('')}`;

    const bindContactSheet = contactSheetApi().bind;
    if (typeof bindContactSheet === 'function') {
      bindContactSheet(grid, { onPromote: (shotId, takeId) => { void promoteTake(shotId, takeId); } });
    }

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

  // 显式降级：本仓当前切片无分镜渲染端点，禁止用本地状态假装生成完成。
  function regenerateShot(idx) {
    const shot = state.shots[idx];
    if (!shot) return blockStoryboardAction('SHOT_CONTEXT_NOT_FOUND', '缺少真实镜头上下文，未提交重绘任务。');
    return blockStoryboardAction(
      'RENDER_NOT_INTEGRATED',
      '分镜重绘端点尚未接入，未提交任务，也未修改当前镜头状态。',
      'configure_executor',
      shot
    );
  }

  function batchGenerate() {
    return blockStoryboardAction(
      'BATCH_RENDER_OUT_OF_SCOPE',
      '批量渲染不在本期/未接入（属于批量制作）；未提交任何任务。',
      'review_context'
    );
  }

  function addNewShot() {
    return blockStoryboardAction(
      'SHOT_APPEND_NOT_INTEGRATED',
      state.shots.length === 0
        ? '分镜数据尚未接入，无法追加镜头。'
        : '新增镜头端点尚未接入，未创建未持久化镜头。',
      'review_context'
    );
  }

  function exportPdf() {
    return blockStoryboardAction(
      'PDF_EXPORT_NOT_INTEGRATED',
      '分镜 PDF 导出本期明确阻塞，未生成任何文件。',
      'review_context'
    );
  }

  function setShots(shots) {
    state.actionBlocker = null;
    state.shots = Array.isArray(shots)
      ? shots.map(item => normalizeShot(item, { allowMissingAsset: true })).filter(Boolean)
      : [];
    renderGrid();
  }

  function setAgentSyncStatus(text, kind = '') {
    const status = document.getElementById('agentStoryboardSyncStatus');
    if (!status) return;
    status.textContent = String(text || '');
    if (kind) status.setAttribute('data-gw-degradation', kind);
    else status.removeAttribute('data-gw-degradation');
  }

  function setTimelineSyncStatus(text, kind = '') {
    const status = document.getElementById('timelineSyncStatus');
    if (!status) return;
    status.textContent = String(text || '');
    if (kind) status.setAttribute('data-gw-degradation', kind);
    else status.removeAttribute('data-gw-degradation');
  }

  function timelineShotOrder(shot, index) {
    const shotId = String(shot?.shot_id || shot?.id || '').trim();
    const match = /^SC(\d+)_SH(\d+)$/.exec(shotId);
    if (match) return [0, Number(match[1]), Number(match[2]), index];
    return [1, shotId, index];
  }

  function timelineDuration(shot) {
    const raw = shot?.duration ?? shot?.duration_seconds ?? shot?.metadata?.duration;
    const value = typeof raw === 'number' ? raw : Number.parseFloat(String(raw ?? ''));
    return Number.isFinite(value) && value > 0 ? value : 1;
  }

  function timelineSource(shot, duration) {
    const source = String(shot?.imageUrl || shot?.img || shot?.source?.source_url || '').trim();
    if (!source) return '';
    return source.includes('#t=') ? source : `${source}#t=0,${duration}`;
  }

  async function syncTimeline() {
    const projectId = String(state.projectId || resolveProjectId() || '').trim();
    const pipelineId = String(state.pipelineId || '').trim();
    const button = document.getElementById('btnSyncTimeline');
    if (!projectId || !pipelineId) {
      setTimelineSyncStatus('缺少流水线上下文', 'not_integrated');
      return null;
    }
    const ordered = (Array.isArray(state.shots) ? state.shots : [])
      .map((shot, index) => ({ shot, index }))
      .sort((a, b) => {
        const left = timelineShotOrder(a.shot, a.index);
        const right = timelineShotOrder(b.shot, b.index);
        for (let i = 0; i < Math.max(left.length, right.length); i += 1) {
          if (left[i] === right[i]) continue;
          return left[i] < right[i] ? -1 : 1;
        }
        return 0;
      })
      .map(item => item.shot);
    if (!ordered.length || ordered.some(shot => !String(shot?.shot_id || shot?.id || '').trim() || !String(shot?.asset_id || '').trim())) {
      setTimelineSyncStatus('缺少镜头资产上下文', 'not_integrated');
      return null;
    }

    const clips = [];
    let at = 0;
    ordered.forEach(shot => {
      const shotId = String(shot.shot_id || shot.id).trim();
      const duration = timelineDuration(shot);
      const primaryTake = primaryTakeForShot(shot);
      const assetId = String(primaryTake?.asset_id || shot.asset_id || '').trim();
      const overrides = { ...(shot.overrides && typeof shot.overrides === 'object' ? shot.overrides : {}) };
      const src = takeSource(primaryTake, duration) || timelineSource(shot, duration);
      if (src) overrides.src = src;
      const camera = String(shot.camera || shot.shotType || shot.shot_type || shot.type || '').trim();
      const movement = String(shot.movement || shot.camera_movement || '').trim();
      if (camera) overrides.camera = camera;
      if (movement) overrides.movement = movement;
      clips.push({
        shot_id: shotId,
        asset_id: assetId,
        version_id: String(primaryTake?.version_id || shot.version_id || '').trim(),
        take_id: String(primaryTake?.take_id || shot.primary_take_id || '').trim(),
        version: primaryTake?.version ?? shot.version ?? null,
        at,
        duration,
        overrides
      });
      at += duration;
    });

    if (button) button.disabled = true;
    setTimelineSyncStatus('正在写入时间线…');
    try {
      const result = await requestJson(`/api/episode-pipelines/${encodeURIComponent(pipelineId)}/timeline-manifest`, {
        method: 'POST',
        body: JSON.stringify({ tracks: [{ track_id: 'video-01', kind: 'video', clips }] })
      });
      state.timelineManifest = result?.timeline_manifest || result?.manifest || result;
      setTimelineSyncStatus(`已同步 ${clips.length} 镜至时间线`);
      return result;
    } catch (error) {
      setTimelineSyncStatus(error?.message || '时间线同步失败', 'service_unavailable');
      return null;
    } finally {
      if (button) button.disabled = false;
    }
  }

  async function readTimelineManifest(pipelineId) {
    if (state.timelineManifest && Array.isArray(state.timelineManifest.tracks)) return state.timelineManifest;
    const result = await requestJson(`/api/episode-pipelines/${encodeURIComponent(pipelineId)}/timeline-manifest`);
    const manifest = result?.timeline_manifest || result?.manifest || result;
    if (!manifest || !Array.isArray(manifest.tracks)) {
      throw Object.assign(new Error('时间线清单结构未接入'), { code: 'TIMELINE_MANIFEST_INVALID' });
    }
    state.timelineManifest = manifest;
    return manifest;
  }

  function cloneTimeline(value) {
    return JSON.parse(JSON.stringify(value));
  }

  async function promoteTake(shotId, takeId) {
    const shot = shotForId(shotId);
    const pipelineId = String(state.pipelineId || '').trim();
    if (!shot) {
      setTimelineSyncStatus('缺少真实镜头上下文，未回选 Take。', 'not_integrated');
      return null;
    }
    const take = takeById(shot, takeId);
    if (!take || !take.asset_id || !take.version_id) {
      setTimelineSyncStatus('缺少 Take 的资产或版本绑定，未回选。', 'not_integrated');
      return null;
    }
    if (!pipelineId) {
      setTimelineSyncStatus('缺少流水线上下文，未回选 Take。', 'not_integrated');
      return null;
    }

    setTimelineSyncStatus(`正在回选主力 Take ${take.versionLabel || ''}…`);
    try {
      const manifest = await readTimelineManifest(pipelineId);
      const tracks = cloneTimeline(manifest.tracks);
      const clip = tracks.flatMap(track => Array.isArray(track.clips) ? track.clips : [])
        .find(item => String(item?.shot_id || '') === String(shot.id || shot.shot_id || shotId));
      if (!clip) {
        throw Object.assign(new Error('时间线中未找到对应镜头片段'), { code: 'TIMELINE_CLIP_NOT_FOUND' });
      }
      const duration = Number(clip.duration) > 0 ? Number(clip.duration) : timelineDuration(shot);
      const overrides = { ...(clip.overrides && typeof clip.overrides === 'object' ? clip.overrides : {}) };
      const source = takeSource(take, duration);
      if (source) overrides.src = source;
      clip.asset_id = String(take.asset_id).trim();
      clip.version_id = String(take.version_id).trim();
      clip.take_id = String(take.take_id || '').trim();
      clip.version = take.version ?? null;
      clip.overrides = overrides;

      const metadata = manifest.metadata && typeof manifest.metadata === 'object' ? cloneTimeline(manifest.metadata) : {};
      const bindings = metadata.primary_take_by_shot && typeof metadata.primary_take_by_shot === 'object'
        ? { ...metadata.primary_take_by_shot } : {};
      bindings[String(shot.id || shot.shot_id || shotId)] = {
        take_id: String(take.take_id || '').trim(),
        asset_id: String(take.asset_id).trim(),
        version_id: String(take.version_id).trim(),
        version: take.version ?? null
      };
      metadata.primary_take_by_shot = bindings;
      const expectedVersion = Number(manifest.version || 1);
      const result = await requestJson(`/api/episode-pipelines/${encodeURIComponent(pipelineId)}/timeline-manifest`, {
        method: 'PUT',
        body: JSON.stringify({
          expected_version: expectedVersion,
          manifest_id: manifest.manifest_id,
          project_id: manifest.project_id || state.projectId,
          tracks,
          metadata
        })
      });
      state.timelineManifest = result?.timeline_manifest || result?.manifest || result;
      applyPrimaryTake(shot, take);
      renderGrid();
      setTimelineSyncStatus(`已回选主力 Take ${take.versionLabel || ''}，时间线已更新`);
      return result;
    } catch (error) {
      setTimelineSyncStatus(error?.message || '主力 Take 回选失败', error?.code === 'TIMELINE_CLIP_NOT_FOUND' ? 'not_integrated' : 'service_unavailable');
      return null;
    }
  }

  function shotsFromAgentStoryboard(rows, artifact, projectId = state.projectId) {
    const versionId = String(artifact?.version_id || '').trim();
    const revision = Number(artifact?.revision);
    return (Array.isArray(rows) ? rows : []).map((row, index) => {
      const shotId = String(row?.shot_id || '').trim();
      if (!shotId) return null;
      const camera = String(row?.camera || '').trim();
      const movement = String(row?.movement || '').trim();
      const shotType = [camera, movement].filter(Boolean).join(' · ') || '文字分镜';
      const duration = String(row?.duration_seconds || '').trim();
      return normalizeShot({
        id: shotId,
        shot_id: shotId,
        code: shotId,
        scene_id: String(row?.scene_id || '').trim(),
        project_id: projectId,
        name: shotId,
        title: shotId,
        prompt: String(row?.description || '').trim(),
        dialogue: String(row?.description || '').trim(),
        shot_type: shotType,
        duration: duration ? `${duration}s` : '',
        status: 'completed',
        data_status: 'ok',
        closure_status: 'approved_text',
        source_kind: 'agent_storyboard',
        source: {
          project_id: projectId,
          artifact_id: String(artifact?.artifact_id || '').trim(),
          artifact_version_id: versionId,
          source: 'approved_storyboard_text',
          row_index: index,
        },
        version_id: versionId,
        version: Number.isFinite(revision) ? revision : null,
        media_type: 'text',
      }, { allowMissingAsset: true });
    }).filter(Boolean);
  }

  async function syncLatestAgentStoryboard() {
    const projectId = String(state.projectId || resolveProjectId() || '').trim();
    const button = document.getElementById('btnSyncAgentStoryboard');
    if (!projectId) {
      setAgentSyncStatus('缺少项目上下文', 'not_integrated');
      return null;
    }
    if (button) button.disabled = true;
    setAgentSyncStatus('读取最新已审核产物…');
    try {
      const loader = window.GWProjectArtifactLinks?.loadLatestApprovedStoryboard;
      if (typeof loader !== 'function') {
        throw Object.assign(new Error('智能体产物读取模块未接入'), { code: 'AGENT_ARTIFACTS_NOT_INTEGRATED' });
      }
      const result = await loader(projectId);
      if (!result?.artifact) {
        setAgentSyncStatus('暂无已审核文字分镜', 'not_integrated');
        return null;
      }
      const shots = shotsFromAgentStoryboard(result.rows, result.artifact, projectId);
      if (!shots.length) {
        setAgentSyncStatus('已审核产物没有有效镜头', 'not_integrated');
        return null;
      }
      if (state.projectId !== projectId) return null;
      state.pipelineId = '';
      state.pipelineVersion = 0;
      state.pipeline = null;
      state.timelineManifest = null;
      setTimelineSyncStatus('时间线未接入', 'not_integrated');
      state.actionBlocker = null;
      state.reviewError = '';
      state.shots = shots;
      state.scenes = scenesFromShots(shots);
      // 文字分镜已由 Agent 审核；它们是占位卡片，不创建伪造的媒体审阅会话。
      state.reviews = Object.fromEntries(shots.map(shot => [shot.id, {
        sessionId: '', reviewId: String(result.artifact.review_id || ''), assetId: '', shotId: shot.id,
        versionId: shot.version_id, version: shot.version, expectedVersion: shot.version || 1,
        status: 'approved', nextAction: 'review_source', comments: [], approvals: [], delivery: null,
        dataStatus: 'ok', dataGaps: [], supersededVersionId: ''
      }]));
      renderNav();
      renderGrid();
      setAgentSyncStatus(`已同步 ${shots.length} 镜 · ${result.artifact.version_label || result.artifact.version_id}`, '');
      return {artifact: result.artifact, shots};
    } catch (error) {
      setAgentSyncStatus(error?.message || '智能体产物读取失败', 'service_unavailable');
      return null;
    } finally {
      if (button) button.disabled = false;
    }
  }

  function setScenes(scenes) {
    state.actionBlocker = null;
    state.scenes = Array.isArray(scenes) ? [...scenes] : [];
    renderNav();
  }

  async function loadPipelineContext(projectId = state.projectId) {
    if (!projectId) return null;
    const payload = await requestJson(`/api/episode-pipelines?project_id=${encodeURIComponent(projectId)}`);
    const pipelines = Array.isArray(payload?.pipelines) ? payload.pipelines : [];
    const pipeline = pipelines.find(item => String(item.project_id || '') === String(projectId)) || pipelines[0];
    if (!pipeline || typeof pipeline !== 'object') return null;
    const rawShots = Array.isArray(pipeline.shots) ? pipeline.shots : [];
    const shots = rawShots.map(normalizePipelineShot).filter(Boolean);
    if (state.projectId !== projectId) return null;
    state.pipeline = pipeline;
    state.pipelineId = String(pipeline.pipeline_id || pipeline.id || '').trim();
    state.pipelineVersion = Number(pipeline.version) || 1;
    state.timelineManifest = null;
    setTimelineSyncStatus('未同步', 'not_integrated');
    state.shots = shots;
    state.actionBlocker = null;
    state.scenes = scenesFromPipeline(pipeline, shots);
    renderNav();
    renderGrid();
    await loadReviewSessions(projectId);
    return { pipeline, shots };
  }

  async function loadProjectContext(projectId = state.projectId) {
    if (!projectId) {
      renderNotIntegratedState();
      return null;
    }
    state.actionBlocker = null;
    try {
      const projectPayload = await requestJson(`/api/projects/${encodeURIComponent(projectId)}`);
      const project = projectPayload?.project || projectPayload;
      if (!project || typeof project !== 'object') throw new Error('project unavailable');
      state.projectName = String(project.name || project.title || projectId);
      if (new URLSearchParams(window.location.search).get('view') === 'storyboard') {
        try {
          const pipelineResult = await loadPipelineContext(projectId);
          if (pipelineResult) return { project, ...pipelineResult };
        } catch (error) {
          state.reviewError = error.code || 'EPISODE_PIPELINE_UNAVAILABLE';
        }
      }
      const assetsPayload = await requestJson(`/api/asset-registry/assets?project_id=${encodeURIComponent(projectId)}&limit=500`);
      const rawAssets = Array.isArray(assetsPayload?.assets) ? assetsPayload.assets
        : (Array.isArray(assetsPayload?.items) ? assetsPayload.items : []);
      const shots = rawAssets.map(normalizeShot).filter(Boolean);
      if (state.projectId !== projectId) return null;
      state.pipelineId = '';
      state.pipelineVersion = 0;
      state.pipeline = null;
    state.timelineManifest = null;
    setTimelineSyncStatus('时间线未接入', 'not_integrated');
      state.shots = shots;
      state.scenes = scenesFromShots(shots);
      renderNav();
      renderGrid();
      if (shots.some(shot => shot.version_id)) await loadReviewSessions(projectId);
      return {project, shots};
    } catch (error) {
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
    loadPipelineContext,
    loadProjectContext,
    loadReviewSessions,
    addReviewComment,
    submitReviewDecision,
    syncLatestAgentStoryboard,
    syncTimeline,
    promoteTake,
    shotsFromAgentStoryboard,
    getState: () => ({ ...state, shots: [...state.shots], scenes: [...state.scenes], reviews: { ...state.reviews } }),
    renderNotIntegratedState,
    get ready() { return ready; },
    rebind: () => { ready = init(); return ready; }
  };
})();
