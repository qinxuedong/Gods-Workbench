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
    reviewError: ''
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

  function normalizeShot(asset) {
    if (!asset || typeof asset !== 'object') return null;
    const metadata = asset.metadata && typeof asset.metadata === 'object' ? asset.metadata : {};
    const metadataSource = metadata.source_context && typeof metadata.source_context === 'object'
      ? metadata.source_context : {};
    const source = asset.source && typeof asset.source === 'object' ? { ...asset.source } : metadataSource;
    const assetId = String(asset.asset_id || asset.id || '').trim();
    const shotId = String(asset.shot_id || source.shot_id || asset.id || asset.asset_id || '').trim();
    if (!assetId || !shotId) return null;
    const sceneId = String(asset.scene_id || source.scene_id || metadata.scene_id || '').trim();
    const episodeId = String(asset.episode_id || source.episode_id || metadata.episode_id || '').trim();
    const name = String(asset.name || asset.display_name || asset.title || asset.code || shotId).trim();
    const kind = String(asset.kind || asset.asset_type || asset.shot_type || metadata.media_type || '媒体资产').trim();
    const mediaType = String(asset.media_type || metadata.media_type || kind).trim().toLowerCase();
    const isImage = mediaType === 'image' || mediaType.startsWith('image/');
    const isVideo = mediaType === 'video' || mediaType.startsWith('video/');
    const directMediaUrl = String(asset.url || asset.media_url || source.source_url || '').trim();
    const imageUrl = (isImage || isVideo)
      ? (directMediaUrl || `/api/asset-registry/assets/${encodeURIComponent(assetId)}/media`)
      : '';
    const prompt = String(asset.prompt || metadata.prompt || '').trim();
    const status = String(asset.status || metadata.status || 'unknown').trim().toLowerCase();
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
      media_type: String(asset.media_type || metadata.media_type || '').trim(),
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
    });
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
      const review = state.reviews[sh.id] || defaultReviewForShot(sh);
      const hasVersion = Boolean(sh.asset_id && sh.version_id && sh.version);
      const terminal = ['approved', 'changes_requested', 'superseded'].includes(review.status);
      const blocked = !hasVersion || ['waiting', 'unknown', 'failed', 'not_integrated', 'queued', 'running'].includes(review.status);
      const reviewLabel = {
        pending_review: '待审阅', approved: '已通过', changes_requested: '已退回修改', superseded: '旧版本已失效',
        waiting: '等待处理', unknown: '结果未知', failed: '生成失败', queued: '排队中', running: '生成中',
        not_integrated: '未接入'
      }[review.status] || review.status || '未接入';
      const nextLabel = {
        review: '检查当前版本', create_review: '首次操作时建立审阅', deliver: '可进入交付记录',
        regenerate_version: '请重新生成新版本', review_new_version: '请审阅新版本', configure_review: '检查审阅服务',
        reconcile_provider: '人工核对任务', review_source: '进入审阅'
      }[review.nextAction] || review.nextAction || '刷新读取';
      const preview = imageUrl && sh.previewType === 'video'
        ? `<video src="${esc(imageUrl)}" class="w-full h-full object-cover" controls preload="metadata" aria-label="${esc(title)}"></video>`
        : imageUrl
          ? `<img src="${esc(imageUrl)}" class="w-full h-full object-cover group-hover:scale-105 transition duration-300" alt="${esc(title)}" loading="lazy">`
        : `<div class="w-full h-full flex items-center justify-center text-slate-500 font-mono gw-type-body-sm" data-gw-degradation="not_integrated">媒体预览未接入</div>`;
      return `
      <div class="champagne-card p-2.5 flex flex-col justify-between group transition hover:border-[#dfc384]/50" data-storyboard-shot="${esc(sh.id)}" data-asset-id="${esc(sh.asset_id || '')}" data-shot-version-id="${esc(sh.version_id || '')}" data-review-status="${esc(review.status || 'not_integrated')}">
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
    state.shots = shots;
    state.scenes = scenesFromShots(shots);
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
    getState: () => ({ ...state, shots: [...state.shots], scenes: [...state.scenes], reviews: { ...state.reviews } }),
    renderNotIntegratedState,
    get ready() { return ready; },
    rebind: () => { ready = init(); return ready; }
  };
})();
