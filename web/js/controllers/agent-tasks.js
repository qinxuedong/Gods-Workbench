/**
 * Copyright 2026 Gods-Workbench Authors
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 */
(function () {
  'use strict';

  const state = {
    jobId: null,
    runId: null,
    run: null,
    candidate: null,
    question: null,
    lastVersion: 0,
    eventVersion: 0,
    eventCursor: null,
    eventIds: new Set(),
    runGeneration: 0,
    refreshSequence: 0,
    pendingCreate: null,
    pendingCreateJobId: null,
    pendingCreateAccepted: false,
    profiles: [],
    configReady: false,
    busy: false,
    pollTimer: null,
    controller: null
  };

  const byId = id => document.getElementById(id);
  const randomId = () => (window.crypto?.randomUUID ? window.crypto.randomUUID() : `${Date.now()}-${Math.random().toString(36).slice(2)}`);
  const route = (runId, suffix = '') => `/api/agent/runs/${encodeURIComponent(runId)}${suffix}`;
  const jobRoute = jobId => `/api/jobs/${encodeURIComponent(jobId)}`;
  const lastJobStorageKey = 'gw.agentTasks.lastJobId';
  const exportOptions = [['exact-json', 'JSON'], ['markdown', 'Markdown'], ['csv', 'CSV']];

  function readSavedJobId() {
    try {
      const value = window.localStorage?.getItem(lastJobStorageKey);
      return typeof value === 'string' && value.trim() ? value : null;
    } catch (_) {
      return null;
    }
  }

  function rememberJobId(jobId) {
    try { window.localStorage?.setItem(lastJobStorageKey, jobId); } catch (_) {}
  }

  function forgetJobId(jobId) {
    try {
      const storage = window.localStorage;
      if (!storage || storage.getItem(lastJobStorageKey) !== jobId) return;
      storage.removeItem(lastJobStorageKey);
    } catch (_) {}
  }

  function setStatus(message, kind = 'info') {
    const node = byId('agentTaskStatus');
    if (!node) return;
    node.textContent = message;
    node.dataset.gwDegradation = kind === 'error' ? 'service_unavailable' : (kind === 'pending' ? 'pending' : 'ready');
  }

  function setBusy(busy) {
    state.busy = busy;
    ['agentTaskCreate', 'agentTaskLoad', 'agentTaskPause', 'agentTaskResume', 'agentTaskCancel', 'agentTaskApplyConfiguration', 'agentTaskApprove', 'agentTaskReject', 'agentTaskOverride', 'agentTaskAnswerSend'].forEach(id => {
      const node = byId(id);
      if (node) node.disabled = busy || (id !== 'agentTaskCreate' && id !== 'agentTaskLoad' && !state.runId);
    });
    const freezePayload = busy || Boolean(state.pendingCreate || state.pendingCreateJobId);
    ['agentTaskProject', 'agentTaskGoal', 'agentTaskConfigProfile'].forEach(id => {
      const node = byId(id);
      if (node) node.disabled = freezePayload;
    });
  }

  async function request(path, options = {}, responseType = 'auto') {
    const response = await fetch(path, {
      credentials: 'same-origin',
      cache: 'no-store',
      ...options,
      headers: { ...(options.body ? { 'Content-Type': 'application/json' } : {}), ...(options.headers || {}) }
    });
    if (!response.ok) {
      let detail = `HTTP ${response.status}`;
      try {
        const body = await response.json();
        detail = body?.detail?.message || body?.detail || detail;
      } catch (_) {}
      const error = new Error(String(detail));
      error.status = response.status;
      throw error;
    }
    if (response.status === 204) return null;
    if (responseType === 'blob') return response.blob();
    const contentType = response.headers.get('content-type') || '';
    if (contentType.includes('application/json')) return response.json();
    return response;
  }

  async function loadProjects() {
    const select = byId('agentTaskProject');
    if (!select) return;
    try {
      const data = await request('/api/asset-registry/projects?archived=false&deleted=false');
      const projects = Array.isArray(data?.projects) ? data.projects.filter(item => item && item.project_id && !item.archived_at && !item.deleted_at) : [];
      select.innerHTML = '<option value="">选择所属项目</option>' + projects.map(item => `<option value="${escapeHtml(item.project_id)}">${escapeHtml(item.name || item.project_id)}</option>`).join('');
      if (projects.length === 1) select.value = projects[0].project_id;
      if (!projects.length) setStatus('没有可选择的项目；任务创建仍会由服务端复核项目所有权。', 'error');
    } catch (error) {
      select.innerHTML = '<option value="">项目读取失败</option>';
      setStatus(`项目列表不可用：${error.message}`, 'error');
    }
  }

  function escapeHtml(value) {
    return String(value ?? '').replace(/[&<>"']/g, char => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[char]));
  }

  async function loadAgentConfig() {
    try {
      const config = await request('/api/agent/config');
      state.profiles = Array.isArray(config?.profiles)
        ? config.profiles.filter(profile => profile
          && typeof profile.config_snapshot_id === 'string' && profile.config_snapshot_id.trim()
          && typeof profile.provider_id === 'string' && profile.provider_id.trim()
          && typeof profile.model === 'string' && profile.model.trim()
          && typeof profile.mode === 'string' && profile.mode.trim())
        : [];
      const profileSelect = byId('agentTaskConfigProfile');
      if (profileSelect) {
        const uniqueProfiles = state.profiles.filter((profile, index, profiles) =>
          profiles.findIndex(item => item.config_snapshot_id === profile.config_snapshot_id) === index);
        state.profiles = uniqueProfiles;
        profileSelect.innerHTML = uniqueProfiles.length
          ? uniqueProfiles.map(profile => `<option value="${escapeHtml(profile.config_snapshot_id)}">${escapeHtml(profile.provider_id)} · ${escapeHtml(profile.model)} · ${escapeHtml(profile.mode)}</option>`).join('')
          : '<option value="">没有可用的服务端任务配置</option>';
        profileSelect.value = uniqueProfiles[0]?.config_snapshot_id || '';
      }
      state.configReady = config?.ready === true && state.profiles.length > 0;
      if (!state.configReady) {
        setStatus('模型配置或单任务调用上限未就绪；创建任务会失败关闭。', 'error');
      } else {
        setStatus('服务端任务配置已就绪；提交会启动真实模型任务，费用金额未知。');
      }
      syncControls();
    } catch (error) {
      state.profiles = [];
      state.configReady = false;
      const profileSelect = byId('agentTaskConfigProfile');
      if (profileSelect) {
        profileSelect.innerHTML = '<option value="">服务端任务配置读取失败</option>';
        profileSelect.value = '';
      }
      setStatus(`模型配置读取失败：${error.message}`, 'error');
      syncControls();
    }
  }

  function selectedProfile() {
    const snapshotId = byId('agentTaskConfigProfile')?.value;
    return state.profiles.find(profile => profile.config_snapshot_id === snapshotId) || null;
  }

  function createPayload() {
    const profile = selectedProfile();
    return {
      project_id: byId('agentTaskProject')?.value || '',
      user_goal: byId('agentTaskGoal')?.value?.trim() || '',
      config_snapshot_id: profile?.config_snapshot_id || '',
      provider_id: profile?.provider_id || '',
      model: profile?.model || '',
      mode: profile?.mode || '',
      input_artifact_refs: [],
      idempotency_key: randomId(),
      request_id: randomId()
    };
  }

  async function createRun(event) {
    event?.preventDefault();
    if (state.busy) return;
    const retryingSameRequest = Boolean(state.pendingCreate);
    let acceptedJobId = state.pendingCreateJobId;
    if (!state.pendingCreate && !acceptedJobId) state.pendingCreate = createPayload();
    const payload = state.pendingCreate;
    if (!acceptedJobId && (!payload?.project_id || !payload?.user_goal || !payload?.config_snapshot_id || !state.configReady)) {
      setStatus(!state.configReady || !payload?.config_snapshot_id
        ? '没有可用的服务端任务配置，无法创建任务。'
        : '请先选择项目并填写创作目标。', 'error');
      state.pendingCreate = null;
      state.pendingCreateAccepted = false;
      return;
    }
    setBusy(true);
    setStatus(
      acceptedJobId
        ? `正在恢复已受理任务 ${acceptedJobId}；不会再次创建任务。`
        : (state.pendingCreateAccepted
          ? '正在用同一幂等键确认已受理请求。'
          : (retryingSameRequest ? '正在用同一请求键重试；后端会返回原任务。' : '正在提交任务；网络结果不明时可用同一请求键重试。')),
      'pending'
    );
    let postAccepted = state.pendingCreateAccepted;
    let acceptedByThisPost = false;
    try {
      let result = null;
      if (!acceptedJobId) {
        result = await request('/api/agent/runs', { method: 'POST', body: JSON.stringify(payload) });
        postAccepted = true;
        acceptedByThisPost = true;
        state.pendingCreateAccepted = true;
        acceptedJobId = result.job_id || null;
        if (acceptedJobId) state.pendingCreateJobId = acceptedJobId;
      }
      if (!acceptedJobId) throw new Error('服务端已受理请求，但没有返回稳定任务编号');
      await adoptJob(acceptedJobId, acceptedByThisPost);
      state.pendingCreate = null;
      state.pendingCreateJobId = null;
      state.pendingCreateAccepted = false;
      const version = state.run?.version ?? result?.version;
      setStatus(
        acceptedByThisPost
          ? `任务已接受：${state.jobId}（版本 ${version}），费用金额未知。`
          : `任务状态已恢复：${state.jobId}（版本 ${version}）。`,
        'info'
      );
    } catch (error) {
      if (error.status === 404 && acceptedJobId) {
        forgetJobId(acceptedJobId);
        if (state.jobId === acceptedJobId) clearActiveRun();
        const field = byId('agentTaskRunId');
        if (field?.value === acceptedJobId) field.value = '';
      }
      if (acceptedJobId) {
        setStatus(`任务已受理：${acceptedJobId}，但状态读取失败：${error.message}。再次提交只会恢复此任务，不会创建新任务。`, 'pending');
      } else if (postAccepted) {
        setStatus(`服务端已受理请求，但未能确认任务编号：${error.message}。再次提交会复用同一幂等键。`, 'pending');
      } else if (!error.status || error.status >= 500) {
        setStatus(`创建结果未知：${error.message}。再次提交会重用同一幂等键，不会自动重发模型调用。`, 'pending');
      } else {
        state.pendingCreate = null;
        state.pendingCreateJobId = null;
        state.pendingCreateAccepted = false;
        setStatus(`任务未创建：${error.message}`, 'error');
      }
    } finally {
      setBusy(false);
      syncControls();
    }
  }

  async function resolveAgentJob(jobId) {
    const job = await request(jobRoute(jobId));
    if (job?.job_id !== jobId || typeof job.run_id !== 'string' || !job.run_id.trim()) {
      throw new Error('任务状态响应缺少有效的 run_id');
    }
    return {job, runId: job.run_id};
  }

  async function adoptJob(jobId, persistImmediately = false) {
    if (typeof jobId !== 'string' || !jobId.trim()) throw new Error('服务端没有返回稳定任务编号');
    if (state.jobId !== jobId) clearActiveRun();
    state.jobId = jobId;
    const field = byId('agentTaskRunId');
    if (field) field.value = jobId;
    if (persistImmediately) rememberJobId(jobId);
    try {
      const resolved = await resolveAgentJob(jobId);
      if (state.jobId !== jobId) return;
      if (state.runId && state.runId !== resolved.runId) clearActiveRun();
      state.jobId = jobId;
      state.runId = resolved.runId;
      if (field) field.value = jobId;
      await refreshRun();
    } catch (error) {
      if (error.status === 404) forgetJobId(jobId);
      throw error;
    }
    if (state.jobId !== jobId) return;
    rememberJobId(jobId);
    if (state.pendingCreateJobId === jobId) {
      state.pendingCreate = null;
      state.pendingCreateJobId = null;
      state.pendingCreateAccepted = false;
    }
    startPolling();
  }

  async function loadRun() {
    if (state.busy) return;
    const jobId = byId('agentTaskRunId')?.value?.trim();
    if (!jobId) return setStatus('请输入已有任务编号。', 'error');
    setBusy(true);
    try {
      await adoptJob(jobId);
      setStatus(`已读取任务 ${jobId}；状态：${state.run.status}。`);
    } catch (error) {
      if (error.status === 404) {
        forgetJobId(jobId);
        const field = byId('agentTaskRunId');
        if (field?.value === jobId) field.value = '';
      }
      clearActiveRun();
      setStatus(`任务不可用：${error.message}`, 'error');
    } finally {
      setBusy(false);
      syncControls();
    }
  }

  function clearActiveRun() {
    stopPolling();
    state.runGeneration += 1;
    state.refreshSequence += 1;
    state.jobId = null;
    state.runId = null;
    state.run = null;
    state.candidate = null;
    state.question = null;
    state.lastVersion = 0;
    state.eventVersion = 0;
    state.eventCursor = null;
    state.eventIds.clear();
    if (byId('agentTraceStream')) byId('agentTraceStream').replaceChildren();
    if (byId('agentTaskReview')) byId('agentTaskReview').hidden = true;
    if (byId('agentTaskClarification')) byId('agentTaskClarification').hidden = true;
    if (byId('agentTaskExports')) byId('agentTaskExports').replaceChildren();
    syncControls();
  }

  function isCurrentRefresh(runId, generation, sequence) {
    return state.runId === runId && state.runGeneration === generation && state.refreshSequence === sequence;
  }

  async function refreshRun(sequence = ++state.refreshSequence) {
    if (!state.jobId || !state.runId) return;
    const jobId = state.jobId;
    const runId = state.runId;
    const generation = state.runGeneration;
    const resolved = await resolveAgentJob(jobId);
    if (!isCurrentRefresh(runId, generation, sequence)) return;
    if (resolved.runId !== runId) throw new Error('任务映射已变化，请重新读取任务');
    const run = await request(route(runId));
    if (!isCurrentRefresh(runId, generation, sequence)) return;
    state.run = run;
    state.lastVersion = Number.isInteger(run.version) ? run.version : state.lastVersion;
    const label = `${run.status}${run.stage ? ` · ${run.stage}` : ''} · 版本 ${run.version} · 调用 ${run.cost_ledger?.recorded_model_calls ?? 0} 次 · 费用未知`;
    setStatus(label, run.status === 'failed' ? 'error' : 'info');
    await Promise.all([
      refreshEvents(runId, generation, sequence),
      refreshCandidate(runId, generation, sequence),
      refreshClarifications(runId, generation, sequence),
      refreshExports(runId, generation, sequence)
    ]);
    if (!isCurrentRefresh(runId, generation, sequence)) return;
    syncControls();
  }

  async function refreshEvents(runId, generation, sequence) {
    if (!runId) return;
    const params = new URLSearchParams({ after_version: String(state.eventVersion), limit: '100' });
    if (state.eventCursor) params.set('cursor', state.eventCursor);
    const data = await request(`${route(runId, '/events')}?${params}`);
    if (!isCurrentRefresh(runId, generation, sequence)) return;
    const stream = byId('agentTraceStream');
    if (!stream || !Array.isArray(data.events)) return;
    for (const event of data.events) {
      if (event.event_id && state.eventIds.has(event.event_id)) continue;
      if (event.event_id) state.eventIds.add(event.event_id);
      if (Number.isInteger(event.version)) state.eventVersion = Math.max(state.eventVersion, event.version);
      const row = document.createElement('div');
      row.className = 'gw-type-body-md text-slate-300 border-b border-white/5 py-1';
      if (event.event_id) row.dataset.eventId = event.event_id;
      row.textContent = `${event.stage || '任务'} · ${event.summary || event.event_type || '状态更新'}`;
      stream.appendChild(row);
    }
    if (typeof data.cursor === 'string' && data.cursor) state.eventCursor = data.cursor;
    while (stream.children.length > 80) {
      const first = stream.firstElementChild;
      if (first?.dataset.eventId) state.eventIds.delete(first.dataset.eventId);
      first.remove();
    }
  }

  async function refreshCandidate(runId, generation, sequence) {
    if (!runId) return;
    const data = await request(route(runId, '/candidate'));
    if (!isCurrentRefresh(runId, generation, sequence)) return;
    const candidate = data?.candidate;
    state.candidate = candidate?.is_current && candidate?.can_decide ? candidate : null;
    const panel = byId('agentTaskReview');
    if (!panel) return;
    panel.hidden = !state.candidate;
    if (state.candidate) {
      const score = state.candidate.review?.overall_score;
      byId('agentTaskReviewScore').textContent = `当前审核候选 · ${Number.isFinite(score) ? `${score}/10` : '评分不可用'}`;
      byId('agentTaskCandidate').textContent = state.candidate.content || '';
    }
  }

  async function refreshClarifications(runId, generation, sequence) {
    if (!runId) return;
    const data = await request(route(runId, '/clarifications'));
    if (!isCurrentRefresh(runId, generation, sequence)) return;
    const question = Array.isArray(data.questions) ? data.questions.find(item => item?.question_id) : null;
    state.question = data.is_pending ? question : null;
    const panel = byId('agentTaskClarification');
    if (!panel) return;
    panel.hidden = !state.question;
    if (state.question) byId('agentTaskQuestion').textContent = state.question.text;
  }

  async function refreshExports(runId, generation, sequence) {
    if (!runId) return;
    const data = await request(route(runId, '/reviews'));
    if (!isCurrentRefresh(runId, generation, sequence)) return;
    const container = byId('agentTaskExports');
    if (!container) return;
    container.replaceChildren();
    for (const item of (data.reviews || [])) {
      const review = item?.review;
      const formats = Array.isArray(item?.export_formats)
        ? exportOptions.filter(([format]) => item.export_formats.includes(format))
        : [];
      if (!review?.review_id || !review.artifact?.version_id || !formats.length) continue;
      const group = document.createElement('div');
      group.className = 'flex flex-wrap gap-1 items-center';
      const label = document.createElement('span');
      label.className = 'gw-type-body-sm text-slate-400';
      label.textContent = `可导出版本 ${review.artifact.version_id}`;
      group.appendChild(label);
      for (const [format, title] of formats) {
        const button = document.createElement('button');
        button.type = 'button';
        button.className = 'tactile-keycap px-2 py-1 rounded gw-type-body-md';
        button.textContent = `导出 ${title}`;
        button.addEventListener('click', () => downloadExport(review, format));
        group.appendChild(button);
      }
      container.appendChild(group);
    }
  }

  async function downloadExport(review, format) {
    if (!state.runId || !review?.review_id || !review?.artifact?.version_id) return;
    try {
      const query = new URLSearchParams({ format, review_id: review.review_id, artifact_version_id: review.artifact.version_id });
      const blob = await request(`${route(state.runId, '/export')}?${query}`, {}, 'blob');
      const url = URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = url;
      link.download = `agent-${review.artifact.version_id}.${format === 'exact-json' ? 'json' : format}`;
      document.body.appendChild(link);
      link.click();
      link.remove();
      URL.revokeObjectURL(url);
    } catch (error) {
      setStatus(`导出失败：${error.message}`, 'error');
    }
  }

  function syncControls() {
    const status = state.run?.status;
    if (byId('agentTaskCreate')) byId('agentTaskCreate').disabled = state.busy || !state.configReady || !selectedProfile();
    if (byId('agentTaskPause')) byId('agentTaskPause').disabled = state.busy || !state.runId || !['queued', 'running', 'waiting_review'].includes(status);
    if (byId('agentTaskResume')) byId('agentTaskResume').disabled = state.busy || !state.runId || status !== 'paused';
    if (byId('agentTaskCancel')) byId('agentTaskCancel').disabled = state.busy || !state.runId || ['cancelled', 'succeeded', 'failed'].includes(status);
    if (byId('agentTaskApplyConfiguration')) byId('agentTaskApplyConfiguration').disabled = state.busy || !state.runId || status !== 'paused' || !selectedProfile();
    ['agentTaskApprove', 'agentTaskReject', 'agentTaskOverride'].forEach(id => { if (byId(id)) byId(id).disabled = state.busy || !state.candidate; });
    if (byId('agentTaskAnswerSend')) byId('agentTaskAnswerSend').disabled = state.busy || !state.question;
  }

  function startPolling() {
    stopPolling();
    state.pollTimer = window.setInterval(async () => {
      if (!state.jobId || !state.runId || !['queued', 'running', 'waiting_review', 'paused'].includes(state.run?.status)) return;
      const runId = state.runId;
      const generation = state.runGeneration;
      const sequence = ++state.refreshSequence;
      try {
        await refreshRun(sequence);
      } catch (error) {
        if (isCurrentRefresh(runId, generation, sequence)) {
          setStatus(`状态暂时不可读：${error.message}；没有重发模型请求。`, 'pending');
        }
      }
    }, 2000);
  }

  function stopPolling() {
    if (state.pollTimer !== null) window.clearInterval(state.pollTimer);
    state.pollTimer = null;
  }

  async function restoreSavedRun() {
    if (state.runId || state.busy) return;
    const jobId = readSavedJobId();
    if (!jobId) return;
    state.pendingCreateJobId = jobId;
    const field = byId('agentTaskRunId');
    if (field) field.value = jobId;
    setBusy(true);
    setStatus('正在读取上次任务状态…', 'pending');
    try {
      await adoptJob(jobId);
      if (state.jobId === jobId) setStatus(`已恢复任务 ${jobId}；状态：${state.run.status}。`);
    } catch (error) {
      if (error.status === 404) {
        forgetJobId(jobId);
        if (state.pendingCreateJobId === jobId) state.pendingCreateJobId = null;
        if (field?.value === jobId) field.value = '';
      }
      if (state.jobId === jobId) clearActiveRun();
      setStatus(
        error.status === 404 ? `上次任务已不存在，已清除失效编号：${error.message}` : `上次任务状态暂时不可读：${error.message}`,
        error.status === 404 ? 'error' : 'pending'
      );
    } finally {
      setBusy(false);
      syncControls();
    }
  }

  async function mutate(action, payload) {
    if (!state.runId || state.busy) return;
    setBusy(true);
    try {
      await request(route(state.runId, `/${action}`), { method: 'POST', body: JSON.stringify(payload) });
      await refreshRun();
    } catch (error) {
      setStatus(`${error.message}；请刷新当前版本后再操作。`, 'error');
    } finally {
      setBusy(false);
      syncControls();
    }
  }

  function mutationBody() {
    return {
      expected_version: state.run?.version,
      idempotency_key: randomId(),
      request_id: randomId()
    };
  }

  function review(decision) {
    if (!state.candidate || !state.run) return;
    const reason = byId('agentTaskReason')?.value?.trim() || '';
    if (!reason) {
      setStatus('请填写决定理由。', 'error');
      byId('agentTaskReason')?.focus();
      return;
    }
    void mutate('review', {
      ...mutationBody(),
      decision,
      reason,
      review_id: state.candidate.review.review_id,
      artifact_version_id: state.candidate.artifact.version_id
    });
  }

  function answerQuestion() {
    if (!state.question || !state.run) return;
    const answer = byId('agentTaskAnswer')?.value?.trim() || '';
    if (!answer) return setStatus('请填写澄清答复。', 'error');
    void mutate('clarifications', { ...mutationBody(), question_id: state.question.question_id, answer });
  }

  function applyConfiguration() {
    const profile = selectedProfile();
    if (!state.runId || state.run?.status !== 'paused' || !profile) {
      setStatus('仅暂停中的任务可应用有效的服务端配置。', 'error');
      return;
    }
    void mutate('configuration', {
      ...mutationBody(),
      config_snapshot_id: profile.config_snapshot_id
    });
  }

  function bind() {
    byId('agentTaskForm')?.addEventListener('submit', createRun);
    byId('agentTaskLoad')?.addEventListener('click', loadRun);
    byId('agentTaskPause')?.addEventListener('click', () => mutate('pause', mutationBody()));
    byId('agentTaskResume')?.addEventListener('click', () => mutate('resume', mutationBody()));
    byId('agentTaskCancel')?.addEventListener('click', () => {
      if (window.confirm('终止会停止后续阶段；当前已发出的模型请求可能继续完成并计费。确认终止？')) void mutate('cancel', mutationBody());
    });
    byId('agentTaskApplyConfiguration')?.addEventListener('click', applyConfiguration);
    byId('agentTaskApprove')?.addEventListener('click', () => review('approve'));
    byId('agentTaskReject')?.addEventListener('click', () => review('revise'));
    byId('agentTaskOverride')?.addEventListener('click', () => review('override'));
    byId('agentTaskAnswerSend')?.addEventListener('click', answerQuestion);
  }

  async function init() {
    if (!byId('agentTaskForm')) return;
    bind();
    setBusy(false);
    await Promise.all([loadProjects(), loadAgentConfig()]);
    await restoreSavedRun();
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init, { once: true });
  else void init();
}());
