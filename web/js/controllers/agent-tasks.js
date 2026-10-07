/**
 * Copyright 2026 Gods-Workbench Authors
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 */
(function () {
  'use strict';

  const state = {
    runId: null,
    run: null,
    candidate: null,
    question: null,
    lastVersion: 0,
    eventVersion: 0,
    eventCursor: null,
    eventIds: new Set(),
    pendingCreate: null,
    busy: false,
    pollTimer: null,
    controller: null
  };

  const byId = id => document.getElementById(id);
  const randomId = () => (window.crypto?.randomUUID ? window.crypto.randomUUID() : `${Date.now()}-${Math.random().toString(36).slice(2)}`);
  const route = (runId, suffix = '') => `/api/agent/runs/${encodeURIComponent(runId)}${suffix}`;

  function setStatus(message, kind = 'info') {
    const node = byId('agentTaskStatus');
    if (!node) return;
    node.textContent = message;
    node.dataset.gwDegradation = kind === 'error' ? 'service_unavailable' : (kind === 'pending' ? 'pending' : 'ready');
  }

  function setBusy(busy) {
    state.busy = busy;
    ['agentTaskCreate', 'agentTaskLoad', 'agentTaskPause', 'agentTaskResume', 'agentTaskCancel', 'agentTaskApprove', 'agentTaskReject', 'agentTaskOverride', 'agentTaskAnswerSend'].forEach(id => {
      const node = byId(id);
      if (node) node.disabled = busy || (id !== 'agentTaskCreate' && id !== 'agentTaskLoad' && !state.runId);
    });
    const freezePayload = busy || Boolean(state.pendingCreate);
    ['agentTaskProject', 'agentTaskGoal', 'agentTaskMode', 'agentProviderSelect', 'agentModelSelect'].forEach(id => {
      const node = byId(id);
      if (node) node.disabled = freezePayload;
    });
  }

  async function request(path, options = {}) {
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
      if (!config.ready) {
        setStatus('服务端没有可用的真实模型配置；创建任务会失败关闭。', 'error');
      } else {
        setStatus('模型配置已就绪；提交任务会产生真实模型调用，费用金额未知。');
      }
    } catch (error) {
      setStatus(`模型配置读取失败：${error.message}`, 'error');
    }
  }

  function createPayload() {
    return {
      project_id: byId('agentTaskProject')?.value || '',
      user_goal: byId('agentTaskGoal')?.value?.trim() || '',
      provider_id: byId('agentProviderSelect')?.value || undefined,
      model: byId('agentModelSelect')?.value || undefined,
      mode: byId('agentTaskMode')?.value || 'approval',
      idempotency_key: randomId(),
      request_id: randomId()
    };
  }

  async function createRun(event) {
    event?.preventDefault();
    if (state.busy) return;
    const retryingSameRequest = Boolean(state.pendingCreate);
    if (!state.pendingCreate) state.pendingCreate = createPayload();
    const payload = state.pendingCreate;
    if (!payload.project_id || !payload.user_goal) {
      setStatus('请先选择项目并填写创作目标。', 'error');
      state.pendingCreate = null;
      return;
    }
    setBusy(true);
    setStatus(retryingSameRequest ? '正在用同一请求键重试；后端会返回原任务。' : '正在提交任务；网络结果不明时可用同一请求键重试。', 'pending');
    try {
      const result = await request('/api/agent/runs', { method: 'POST', body: JSON.stringify(payload) });
      state.pendingCreate = null;
      await adoptRun(result.job_id || result.run_id);
      setStatus(`任务已接受：${state.runId}（版本 ${state.run?.version ?? result.version}），费用金额未知。`);
    } catch (error) {
      if (!error.status || error.status >= 500) {
        setStatus(`创建结果未知：${error.message}。再次提交会重用同一幂等键，不会自动重发模型调用。`, 'pending');
      } else {
        state.pendingCreate = null;
        setStatus(`任务未创建：${error.message}`, 'error');
      }
    } finally {
      setBusy(false);
      syncControls();
    }
  }

  async function adoptRun(runId) {
    if (typeof runId !== 'string' || !runId.trim()) throw new Error('服务端没有返回任务编号');
    state.runId = runId;
    const field = byId('agentTaskRunId');
    if (field) field.value = runId;
    await refreshRun();
    startPolling();
  }

  async function loadRun() {
    if (state.busy) return;
    const runId = byId('agentTaskRunId')?.value?.trim();
    if (!runId) return setStatus('请输入已有任务编号。', 'error');
    setBusy(true);
    try {
      await adoptRun(runId);
      setStatus(`已读取任务 ${runId}；状态：${state.run.status}。`);
    } catch (error) {
      clearActiveRun();
      setStatus(`任务不可用：${error.message}`, 'error');
    } finally {
      setBusy(false);
      syncControls();
    }
  }

  function clearActiveRun() {
    stopPolling();
    state.runId = null;
    state.run = null;
    state.candidate = null;
    state.question = null;
    state.lastVersion = 0;
    state.eventVersion = 0;
    state.eventCursor = null;
    state.eventIds.clear();
    if (byId('agentTaskReview')) byId('agentTaskReview').hidden = true;
    if (byId('agentTaskClarification')) byId('agentTaskClarification').hidden = true;
    if (byId('agentTaskExports')) byId('agentTaskExports').replaceChildren();
    syncControls();
  }

  async function refreshRun() {
    if (!state.runId) return;
    const run = await request(route(state.runId));
    state.run = run;
    state.lastVersion = Number.isInteger(run.version) ? run.version : state.lastVersion;
    const label = `${run.status}${run.stage ? ` · ${run.stage}` : ''} · 版本 ${run.version} · 调用 ${run.cost_ledger?.recorded_model_calls ?? 0} 次 · 费用未知`;
    setStatus(label, run.status === 'failed' ? 'error' : 'info');
    await Promise.all([refreshEvents(), refreshCandidate(), refreshClarifications(), refreshExports()]);
    syncControls();
  }

  async function refreshEvents() {
    if (!state.runId) return;
    const params = new URLSearchParams({ after_version: String(state.eventVersion), limit: '100' });
    if (state.eventCursor) params.set('cursor', state.eventCursor);
    const data = await request(`${route(state.runId, '/events')}?${params}`);
    const stream = byId('agentTraceStream');
    if (!stream || !Array.isArray(data.events)) return;
    for (const event of data.events) {
      if (event.event_id && state.eventIds.has(event.event_id)) continue;
      if (event.event_id) state.eventIds.add(event.event_id);
      if (Number.isInteger(event.version)) state.eventVersion = Math.max(state.eventVersion, event.version);
      const row = document.createElement('div');
      row.className = 'text-[10px] text-slate-300 border-b border-white/5 py-1';
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

  async function refreshCandidate() {
    if (!state.runId) return;
    const data = await request(route(state.runId, '/candidate'));
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

  async function refreshClarifications() {
    if (!state.runId) return;
    const data = await request(route(state.runId, '/clarifications'));
    const question = Array.isArray(data.questions) ? data.questions.find(item => item?.question_id) : null;
    state.question = data.is_pending ? question : null;
    const panel = byId('agentTaskClarification');
    if (!panel) return;
    panel.hidden = !state.question;
    if (state.question) byId('agentTaskQuestion').textContent = state.question.text;
  }

  async function refreshExports() {
    if (!state.runId) return;
    const data = await request(route(state.runId, '/reviews'));
    const container = byId('agentTaskExports');
    if (!container) return;
    container.replaceChildren();
    for (const item of (data.reviews || [])) {
      const review = item.review;
      const approval = item.approval;
      if (!review || !approval || !['approve', 'override'].includes(approval.decision)) continue;
      const group = document.createElement('div');
      group.className = 'flex flex-wrap gap-1 items-center';
      const label = document.createElement('span');
      label.className = 'text-[9px] text-slate-400';
      label.textContent = `已批准版本 ${review.artifact.version_id}`;
      group.appendChild(label);
      for (const [format, title] of [['exact-json', 'JSON'], ['markdown', 'Markdown'], ['csv', 'CSV']]) {
        const button = document.createElement('button');
        button.type = 'button';
        button.className = 'tactile-keycap px-2 py-1 rounded text-[9px]';
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
      const response = await request(`${route(state.runId, '/export')}?${query}`);
      const blob = await response.blob();
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
    if (byId('agentTaskPause')) byId('agentTaskPause').disabled = state.busy || !state.runId || !['queued', 'running', 'waiting_review'].includes(status);
    if (byId('agentTaskResume')) byId('agentTaskResume').disabled = state.busy || !state.runId || status !== 'paused';
    if (byId('agentTaskCancel')) byId('agentTaskCancel').disabled = state.busy || !state.runId || ['cancelled', 'succeeded', 'failed'].includes(status);
    ['agentTaskApprove', 'agentTaskReject', 'agentTaskOverride'].forEach(id => { if (byId(id)) byId(id).disabled = state.busy || !state.candidate; });
    if (byId('agentTaskAnswerSend')) byId('agentTaskAnswerSend').disabled = state.busy || !state.question;
  }

  function startPolling() {
    stopPolling();
    state.pollTimer = window.setInterval(async () => {
      if (!state.runId || !['queued', 'running'].includes(state.run?.status)) return;
      try { await refreshRun(); } catch (error) { setStatus(`状态暂时不可读：${error.message}；没有重发模型请求。`, 'pending'); }
    }, 2000);
  }

  function stopPolling() {
    if (state.pollTimer !== null) window.clearInterval(state.pollTimer);
    state.pollTimer = null;
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

  function bind() {
    byId('agentTaskForm')?.addEventListener('submit', createRun);
    byId('agentTaskLoad')?.addEventListener('click', loadRun);
    byId('agentTaskPause')?.addEventListener('click', () => mutate('pause', mutationBody()));
    byId('agentTaskResume')?.addEventListener('click', () => mutate('resume', mutationBody()));
    byId('agentTaskCancel')?.addEventListener('click', () => {
      if (window.confirm('终止会停止后续阶段；当前已发出的模型请求可能继续完成并计费。确认终止？')) void mutate('cancel', mutationBody());
    });
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
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init, { once: true });
  else void init();
}());
