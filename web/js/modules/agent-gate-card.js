/**
 * Copyright 2026 Gods-Workbench Authors
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 *
 * web/js/modules/agent-gate-card.js
 * MVP2-T7 前端对话面板（参考卡 + 门禁卡 + 进度面板）
 */
(function (global) {
  'use strict';

  const MAX_NOTE_LENGTH = 500;

  function escapeHtml(value) {
    return String(value ?? '').replace(/[&<>"']/g, char => ({
      '&': '&amp;',
      '<': '&lt;',
      '>': '&gt;',
      '"': '&quot;',
      "'": '&#39;'
    }[char]));
  }

  function sanitizeText(value) {
    return String(value ?? '');
  }

  function createReferenceCard(refData, options = {}) {
    const data = {
      asset_id: sanitizeText(refData?.asset_id || ''),
      score: refData?.score ?? refData?.overall_score ?? null,
      tags: Array.isArray(refData?.tags) ? refData.tags : [],
      summary: sanitizeText(refData?.summary || ''),
      status: refData?.status || 'pending', // 'selected' | 'rejected' | 'pending'
      note: sanitizeText(refData?.note || '').slice(0, MAX_NOTE_LENGTH),
      order: Number.isInteger(refData?.order) ? refData.order : 0
    };

    const card = document.createElement('div');
    card.className = 'agent-reference-card hw-card p-3 my-2 border border-amber-500/30 bg-slate-900/80 rounded-lg text-slate-100';
    card.dataset.assetId = data.asset_id;
    card.dataset.status = data.status;

    // Header
    const header = document.createElement('div');
    header.className = 'flex justify-between items-center mb-2';

    const title = document.createElement('span');
    title.className = 'font-bold text-amber-300 gw-type-body-md';
    title.textContent = `参考素材: ${data.asset_id}`;
    header.appendChild(title);

    if (options.allowOrder !== false) {
      const orderBox = document.createElement('div');
      orderBox.className = 'flex items-center gap-1';

      const orderLabel = document.createElement('span');
      orderLabel.className = 'text-xs text-slate-400';
      orderLabel.textContent = `顺序: ${data.order}`;
      orderBox.appendChild(orderLabel);

      const upBtn = document.createElement('button');
      upBtn.type = 'button';
      upBtn.className = 'hw-btn hw-btn-xs px-1 py-0.5 text-xs';
      upBtn.textContent = '▲';
      upBtn.dataset.action = 'move-up';
      upBtn.addEventListener('click', () => {
        data.order -= 1;
        orderLabel.textContent = `顺序: ${data.order}`;
        options.onOrderChange?.(data, data.order);
      });

      const downBtn = document.createElement('button');
      downBtn.type = 'button';
      downBtn.className = 'hw-btn hw-btn-xs px-1 py-0.5 text-xs';
      downBtn.textContent = '▼';
      downBtn.dataset.action = 'move-down';
      downBtn.addEventListener('click', () => {
        data.order += 1;
        orderLabel.textContent = `顺序: ${data.order}`;
        options.onOrderChange?.(data, data.order);
      });

      orderBox.appendChild(upBtn);
      orderBox.appendChild(downBtn);
      header.appendChild(orderBox);
    }
    card.appendChild(header);

    // Summary & Score / Tags
    const info = document.createElement('div');
    info.className = 'text-sm text-slate-300 mb-2 space-y-1';

    if (data.score !== null && data.score !== undefined) {
      const scoreBadge = document.createElement('div');
      scoreBadge.className = 'text-xs text-amber-400';
      scoreBadge.textContent = `匹配分值: ${data.score}`;
      info.appendChild(scoreBadge);
    }

    if (data.tags.length > 0) {
      const tagsBox = document.createElement('div');
      tagsBox.className = 'flex flex-wrap gap-1 my-1';
      data.tags.forEach(tag => {
        const tagEl = document.createElement('span');
        tagEl.className = 'bg-amber-950/60 border border-amber-600/40 text-amber-200 text-xs px-1.5 py-0.5 rounded';
        tagEl.textContent = tag;
        tagsBox.appendChild(tagEl);
      });
      info.appendChild(tagsBox);
    }

    if (data.summary) {
      const summaryEl = document.createElement('div');
      summaryEl.className = 'text-xs text-slate-400';
      summaryEl.textContent = data.summary;
      info.appendChild(summaryEl);
    }
    card.appendChild(info);

    // Actions
    const actions = document.createElement('div');
    actions.className = 'flex items-center gap-2 mb-2';

    const selectBtn = document.createElement('button');
    selectBtn.type = 'button';
    selectBtn.className = `hw-btn hw-btn-sm ${data.status === 'selected' ? 'bg-amber-600 text-white font-bold' : 'bg-slate-800 text-slate-300'}`;
    selectBtn.textContent = data.status === 'selected' ? '已选中' : '选中';
    selectBtn.dataset.action = 'select';

    const rejectBtn = document.createElement('button');
    rejectBtn.type = 'button';
    rejectBtn.className = `hw-btn hw-btn-sm ${data.status === 'rejected' ? 'bg-rose-800 text-white font-bold' : 'bg-slate-800 text-slate-300'}`;
    rejectBtn.textContent = data.status === 'rejected' ? '已拒绝' : '拒绝';
    rejectBtn.dataset.action = 'reject';

    selectBtn.addEventListener('click', () => {
      data.status = 'selected';
      card.dataset.status = 'selected';
      selectBtn.className = 'hw-btn hw-btn-sm bg-amber-600 text-white font-bold';
      selectBtn.textContent = '已选中';
      rejectBtn.className = 'hw-btn hw-btn-sm bg-slate-800 text-slate-300';
      rejectBtn.textContent = '拒绝';
      options.onSelect?.(data);
    });

    rejectBtn.addEventListener('click', () => {
      data.status = 'rejected';
      card.dataset.status = 'rejected';
      rejectBtn.className = 'hw-btn hw-btn-sm bg-rose-800 text-white font-bold';
      rejectBtn.textContent = '已拒绝';
      selectBtn.className = 'hw-btn hw-btn-sm bg-slate-800 text-slate-300';
      selectBtn.textContent = '选中';
      options.onReject?.(data);
    });

    actions.appendChild(selectBtn);
    actions.appendChild(rejectBtn);
    card.appendChild(actions);

    // Note Input
    const noteContainer = document.createElement('div');
    noteContainer.className = 'mt-2';

    const noteLabel = document.createElement('div');
    noteLabel.className = 'flex justify-between items-center text-xs text-slate-400 mb-1';

    const labelSpan = document.createElement('span');
    labelSpan.textContent = '备注 (≤500字):';
    const counterSpan = document.createElement('span');
    counterSpan.className = 'note-counter text-slate-500';
    counterSpan.textContent = `${data.note.length} / ${MAX_NOTE_LENGTH}`;

    noteLabel.appendChild(labelSpan);
    noteLabel.appendChild(counterSpan);
    noteContainer.appendChild(noteLabel);

    const noteArea = document.createElement('textarea');
    noteArea.className = 'w-full bg-slate-950 border border-slate-700 rounded p-1 text-xs text-slate-200 focus:outline-none focus:border-amber-500';
    noteArea.rows = 2;
    noteArea.maxLength = MAX_NOTE_LENGTH;
    noteArea.value = data.note;
    noteArea.dataset.field = 'note';

    noteArea.addEventListener('input', () => {
      if (noteArea.value.length > MAX_NOTE_LENGTH) {
        noteArea.value = noteArea.value.slice(0, MAX_NOTE_LENGTH);
      }
      data.note = noteArea.value;
      counterSpan.textContent = `${data.note.length} / ${MAX_NOTE_LENGTH}`;
      options.onNoteChange?.(data, data.note);
    });

    noteContainer.appendChild(noteArea);
    card.appendChild(noteContainer);

    return card;
  }

  function createGateCard(gateData, options = {}) {
    const data = {
      gate_id: sanitizeText(gateData?.gate_id || ''),
      gate_kind: sanitizeText(gateData?.gate_kind || gateData?.GateKind || 'Gate'),
      summary: sanitizeText(gateData?.summary || ''),
      status: gateData?.status || 'pending', // 'pending' | 'waiting' | 'finished'
      decision: gateData?.decision || null // 'approve' | 'changes_requested' | 'rolled_back'
    };

    const card = document.createElement('div');
    card.className = 'agent-gate-card hw-card p-3 my-2 border border-yellow-600/40 bg-slate-900/90 rounded-lg text-slate-100';
    card.dataset.gateId = data.gate_id;
    card.dataset.status = data.status;

    // Gate Title & Kind
    const header = document.createElement('div');
    header.className = 'flex justify-between items-center mb-2';

    const title = document.createElement('span');
    title.className = 'font-bold text-yellow-400 gw-type-body-md';
    title.textContent = `门禁: ${data.gate_kind}`;
    header.appendChild(title);

    const statusBadge = document.createElement('span');
    statusBadge.className = 'text-xs px-2 py-0.5 rounded border';

    function updateStatusUI() {
      if (data.status === 'waiting') {
        statusBadge.className = 'text-xs px-2 py-0.5 rounded bg-amber-950 border-amber-500 text-amber-300';
        statusBadge.textContent = '等待确认中...';
      } else if (data.status === 'finished') {
        statusBadge.className = 'text-xs px-2 py-0.5 rounded bg-emerald-950 border-emerald-500 text-emerald-300';
        const decisionText = {
          approve: '已确认同意',
          changes_requested: '已要求修改',
          rolled_back: '已退回'
        }[data.decision] || '已完成';
        statusBadge.textContent = decisionText;
      } else {
        statusBadge.className = 'text-xs px-2 py-0.5 rounded bg-slate-800 border-slate-600 text-slate-300';
        statusBadge.textContent = '待决策';
      }
    }
    updateStatusUI();
    header.appendChild(statusBadge);
    card.appendChild(header);

    // Summary
    const summaryEl = document.createElement('div');
    summaryEl.className = 'text-sm text-slate-300 mb-3';
    summaryEl.textContent = data.summary;
    card.appendChild(summaryEl);

    // Buttons
    const actions = document.createElement('div');
    actions.className = 'flex flex-wrap items-center gap-2';

    const approveBtn = document.createElement('button');
    approveBtn.type = 'button';
    approveBtn.className = 'hw-btn hw-btn-sm bg-emerald-700 hover:bg-emerald-600 text-white';
    approveBtn.textContent = '确认同意';
    approveBtn.dataset.action = 'approve';

    const changesBtn = document.createElement('button');
    changesBtn.type = 'button';
    changesBtn.className = 'hw-btn hw-btn-sm bg-amber-700 hover:bg-amber-600 text-white';
    changesBtn.textContent = '要求修改';
    changesBtn.dataset.action = 'changes_requested';

    const rollbackBtn = document.createElement('button');
    rollbackBtn.type = 'button';
    rollbackBtn.className = 'hw-btn hw-btn-sm bg-rose-800 hover:bg-rose-700 text-white';
    rollbackBtn.textContent = '退回';
    rollbackBtn.dataset.action = 'rolled_back';

    function setButtonsDisabled(disabled) {
      approveBtn.disabled = disabled;
      changesBtn.disabled = disabled;
      rollbackBtn.disabled = disabled;
    }

    if (data.status === 'waiting' || data.status === 'finished') {
      setButtonsDisabled(true);
    }

    async function handleDecision(decision) {
      data.status = 'waiting';
      data.decision = decision;
      card.dataset.status = 'waiting';
      setButtonsDisabled(true);
      updateStatusUI();

      try {
        if (options.onSubmitDecision) {
          await options.onSubmitDecision(data, decision);
        }
        data.status = 'finished';
        card.dataset.status = 'finished';
        updateStatusUI();
      } catch (error) {
        data.status = 'pending';
        data.decision = null;
        card.dataset.status = 'pending';
        setButtonsDisabled(false);
        updateStatusUI();
        options.onError?.(error);
      }
    }

    approveBtn.addEventListener('click', () => handleDecision('approve'));
    changesBtn.addEventListener('click', () => handleDecision('changes_requested'));
    rollbackBtn.addEventListener('click', () => handleDecision('rolled_back'));

    actions.appendChild(approveBtn);
    actions.appendChild(changesBtn);
    actions.appendChild(rollbackBtn);
    card.appendChild(actions);

    return card;
  }

  function createProgressPanel(progressData, options = {}) {
    const data = {
      current_step: sanitizeText(progressData?.current_step || '初始阶段'),
      total_steps: progressData?.total_steps || null,
      status: progressData?.status || 'running', // 'running' | 'succeeded' | 'failed'
      failure_reason: sanitizeText(progressData?.failure_reason || '')
    };

    const panel = document.createElement('div');
    panel.className = 'agent-progress-panel hw-card p-3 my-2 border border-slate-700 bg-slate-900/90 rounded-lg text-slate-100';

    // Current step
    const stepBox = document.createElement('div');
    stepBox.className = 'flex justify-between items-center mb-1';

    const stepLabel = document.createElement('span');
    stepLabel.className = 'font-semibold text-slate-200 gw-type-body-sm';
    stepLabel.textContent = `当前步骤: ${data.current_step}${data.total_steps ? ` (${data.total_steps})` : ''}`;
    stepBox.appendChild(stepLabel);

    const statusBadge = document.createElement('span');
    statusBadge.className = 'text-xs px-2 py-0.5 rounded';
    if (data.status === 'failed') {
      statusBadge.className += ' bg-rose-950 text-rose-300 border border-rose-600';
      statusBadge.textContent = '执行失败';
    } else if (data.status === 'succeeded') {
      statusBadge.className += ' bg-emerald-950 text-emerald-300 border border-emerald-600';
      statusBadge.textContent = '已完成';
    } else {
      statusBadge.className += ' bg-amber-950 text-amber-300 border border-amber-600';
      statusBadge.textContent = '进行中';
    }
    stepBox.appendChild(statusBadge);
    panel.appendChild(stepBox);

    // Failure reason if any (Plain text rendering to prevent XSS!)
    if (data.status === 'failed' || data.failure_reason) {
      const failBox = document.createElement('div');
      failBox.className = 'mt-2 p-2 bg-rose-950/80 border border-rose-700/60 rounded text-xs text-rose-200';
      failBox.dataset.role = 'failure-reason';

      const failTitle = document.createElement('div');
      failTitle.className = 'font-bold mb-0.5';
      failTitle.textContent = '失败原因:';
      failBox.appendChild(failTitle);

      const failText = document.createElement('div');
      // CRITICAL FOR AC3: Ensure plain text node assignment via textContent
      failText.textContent = data.failure_reason || '未知错误';
      failBox.appendChild(failText);

      panel.appendChild(failBox);
    }

    return panel;
  }

  function renderError(container, errorMessage) {
    if (!container) return;
    const errBox = document.createElement('div');
    errBox.className = 'agent-panel-error hw-card p-3 my-2 border border-rose-600 bg-rose-950/90 text-rose-200 rounded-lg text-sm';
    errBox.dataset.role = 'error-message';

    const title = document.createElement('div');
    title.className = 'font-bold text-rose-300 mb-1';
    title.textContent = '操作失败';
    errBox.appendChild(title);

    const msg = document.createElement('div');
    msg.textContent = sanitizeText(errorMessage || '加载发生错误');
    errBox.appendChild(msg);

    container.appendChild(errBox);
  }

  function restoreSessionState(rawSessionData) {
    if (!rawSessionData || typeof rawSessionData !== 'object') {
      return { references: [], gates: [], progress: null, error: null };
    }
    const references = (Array.isArray(rawSessionData.references) ? rawSessionData.references : []).map(ref => ({
      asset_id: sanitizeText(ref.asset_id || ''),
      score: ref.score ?? ref.overall_score ?? null,
      tags: Array.isArray(ref.tags) ? ref.tags : [],
      summary: sanitizeText(ref.summary || ''),
      status: ref.status || 'pending',
      note: sanitizeText(ref.note || '').slice(0, MAX_NOTE_LENGTH),
      order: Number.isInteger(ref.order) ? ref.order : 0
    }));

    const gates = (Array.isArray(rawSessionData.gates) ? rawSessionData.gates : []).map(gate => ({
      gate_id: sanitizeText(gate.gate_id || ''),
      gate_kind: sanitizeText(gate.gate_kind || gate.GateKind || 'Gate'),
      summary: sanitizeText(gate.summary || ''),
      status: gate.status || 'pending',
      decision: gate.decision || null
    }));

    const progress = rawSessionData.progress ? {
      current_step: sanitizeText(rawSessionData.progress.current_step || ''),
      total_steps: rawSessionData.progress.total_steps || null,
      status: rawSessionData.progress.status || 'running',
      failure_reason: sanitizeText(rawSessionData.progress.failure_reason || '')
    } : null;

    return { references, gates, progress, error: rawSessionData.error || null };
  }

  function createAgentPanel(container, sessionData, options = {}) {
    if (!container) return;
    container.replaceChildren();

    const state = restoreSessionState(sessionData);

    if (state.error) {
      renderError(container, state.error);
      return;
    }

    if (state.progress) {
      const progressEl = createProgressPanel(state.progress, options);
      container.appendChild(progressEl);
    }

    if (state.gates.length > 0) {
      const gatesHeader = document.createElement('div');
      gatesHeader.className = 'font-bold text-slate-300 text-xs mt-3 mb-1 uppercase tracking-wider';
      gatesHeader.textContent = '关键门禁确认';
      container.appendChild(gatesHeader);

      state.gates.forEach(gate => {
        const gateEl = createGateCard(gate, options);
        container.appendChild(gateEl);
      });
    }

    if (state.references.length > 0) {
      const refHeader = document.createElement('div');
      refHeader.className = 'font-bold text-slate-300 text-xs mt-3 mb-1 uppercase tracking-wider';
      refHeader.textContent = '参考素材卡';
      container.appendChild(refHeader);

      state.references.forEach(ref => {
        const refEl = createReferenceCard(ref, options);
        container.appendChild(refEl);
      });
    }
  }

  const GodAgentGateCard = Object.freeze({
    MAX_NOTE_LENGTH,
    escapeHtml,
    createReferenceCard,
    createGateCard,
    createProgressPanel,
    createAgentPanel,
    renderError,
    restoreSessionState
  });

  if (typeof module !== 'undefined' && module.exports) {
    module.exports = GodAgentGateCard;
  }
  global.GodAgentGateCard = GodAgentGateCard;
})(typeof window !== 'undefined' ? window : globalThis);
