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

(function(){
  const { api, escapeHtml: esc, relativeTime, dateTime } = Workspace;
  const q = selector => document.querySelector(selector);
  const qAll = selector => document.querySelectorAll(selector);
  const tr = key => window.StudioI18n?.t?.(key) || key;
  const trf = (key, values = {}) => Object.entries(values).reduce(
    (text, [name, value]) => text.replaceAll(`{${name}}`, String(value)),
    tr(key),
  );
  const targetTypeLabels = {
    asset: 'governance.targetAsset', project: 'governance.targetProject',
    entity: 'governance.targetEntity', canvas: 'governance.targetCanvas',
  };
  const targetTypeLabel = value => tr(targetTypeLabels[String(value || '')] || 'governance.targetTypeLabel');

  let currentOverview = { assets: [], projects: [], canvases: [], outbox: {} };
  let toastTimer = 0;
  let purgePreviewVersion = 0;

  function showToast(message, isError = false) {
    const toast = q('#govToast');
    if (!toast) return;
    toast.textContent = message;
    toast.style.borderColor = isError ? 'var(--color-danger, #e53935)' : 'var(--color-primary, #4f7cff)';
    toast.classList.add('visible');
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => toast.classList.remove('visible'), 3200);
  }

  function setPurgeStatus(message) {
    const status = q('#govConfirmStatus');
    if (status) status.textContent = message;
  }

  function resetPurgeDialog() {
    const okButton = q('#btnGovConfirmOk');
    if (!okButton) return;
    okButton.style.display = 'none';
    okButton.disabled = true;
    okButton.onclick = null;
    okButton.textContent = tr('governance.confirmExecute');
  }

  function setTab(tabName) {
    let activeTab = null;
    qAll('.gov-tab').forEach(tab => {
      const active = tab.dataset.tab === tabName;
      tab.classList.toggle('active', active);
      tab.setAttribute('aria-selected', String(active));
      tab.tabIndex = active ? 0 : -1;
      if (active) activeTab = tab;
    });
    qAll('.gov-panel').forEach(panel => {
      panel.classList.toggle('active', panel.id === `panel${tabName.charAt(0).toUpperCase() + tabName.slice(1)}`);
    });
    return activeTab;
  }

  // 统一显式降级（裁决第 3 项）：NOT_INTEGRATED / SERVICE_UNAVAILABLE 不得退回泛泛「载入失败」。
  function governanceDegradationMessage(err) {
    if (err && err.code === 'NOT_INTEGRATED') return err.message;
    if (err && err.code === 'SERVICE_UNAVAILABLE') return err.message;
    return err && err.message ? err.message : tr('common.requestFailed');
  }
  function setTableFailure(selector, colSpan, message) {
    const table = q(selector);
    if (table) table.innerHTML = `<tr><td colspan="${colSpan}" class="empty-td">${esc(trf('governance.loadFailedTable', { message }))}</td></tr>`;
  }

  function renderAuditOutbox() {
    const outbox = currentOverview.outbox || {};
    const pending = Number(outbox.pending || 0);
    const failed = Number(outbox.failed || 0);
    q('#statPendingOutboxCount').textContent = pending;
    const area = q('#auditOutboxStatusArea');
    if (area) area.textContent = trf('governance.auditStatus', { pending, failed });
  }

  async function loadOverview() {
    try {
      const data = await api('/api/asset-registry/governance/overview');
      currentOverview = { assets: [], projects: [], canvases: [], outbox: {}, ...(data || {}) };
      renderOverview();
      renderTrash();
    } catch (err) {
      const message = governanceDegradationMessage(err);
      showToast(trf('governance.loadFailed', { message }), true);
      setTableFailure('#assetTrashRows', 6, message);
      setTableFailure('#projectTrashRows', 5, message);
      setTableFailure('#canvasTrashRows', 5, message);
      const area = q('#auditOutboxStatusArea');
      if (area) area.textContent = trf('governance.loadFailedTable', { message });
    }
  }

  function renderOverview() {
    const assets = currentOverview.assets || [];
    const projects = currentOverview.projects || [];
    const canvases = currentOverview.canvases || [];

    q('#statAssetsCount').textContent = assets.length;
    q('#statProjectsCount').textContent = projects.length;
    q('#statCanvasesCount').textContent = canvases.length;
    q('#assetTrashCount').textContent = trf('governance.countItems', { count: assets.length });
    q('#projectTrashCount').textContent = trf('governance.countItems', { count: projects.length });
    q('#canvasTrashCount').textContent = trf('governance.countItems', { count: canvases.length });
    renderAuditOutbox();
  }

  function renderTrash() {
    const assetTbody = q('#assetTrashRows');
    const assets = currentOverview.assets || [];
    if (!assets.length) {
      assetTbody.innerHTML = `<tr><td colspan="6" class="empty-td">${esc(tr('governance.emptyAssetTrash'))}</td></tr>`;
    } else {
      assetTbody.innerHTML = assets.map(item => `
        <tr>
          <td>
            <div class="mono" style="font-size:var(--gw-type-body-sm);">${esc(item.asset_id || item.id)}</div>
            <span class="gov-badge asset">${esc(tr('governance.assetType'))}</span>
          </td>
          <td><strong>${esc(item.original_filename || item.asset_id || tr('governance.unnamedAsset'))}</strong></td>
          <td class="mono muted">${esc(item.storage_root_id || '—')}</td>
          <td>${dateTime(item.recycled_at || item.created_at)}</td>
          <td class="mono muted" style="font-size:var(--gw-type-body-sm);">${esc((item.sha256 || '').slice(0, 12))}…</td>
          <td>
            <button class="btn small primary btn-restore-asset" data-asset-id="${esc(item.asset_id)}" data-entry-id="${esc(item.id)}" type="button">${esc(tr('governance.restore'))}</button>
          </td>
        </tr>
      `).join('');
    }

    const projectTbody = q('#projectTrashRows');
    const projects = currentOverview.projects || [];
    if (!projects.length) {
      projectTbody.innerHTML = `<tr><td colspan="5" class="empty-td">${esc(tr('governance.emptyArchivedProjects'))}</td></tr>`;
    } else {
      projectTbody.innerHTML = projects.map(item => `
        <tr>
          <td>
            <div class="mono" style="font-size:var(--gw-type-body-sm);">${esc(item.id)}</div>
            <span class="gov-badge project">${esc(tr('governance.projectType'))}</span>
          </td>
          <td><strong>${esc(item.name || tr('governance.unnamedProject'))}</strong></td>
          <td class="mono">${item.entity_count ?? '—'}</td>
          <td class="mono">${esc(trf('governance.versionValue', { version: item.version || 1 }))}</td>
          <td>
            <button class="btn small primary btn-restore-project" data-project-id="${esc(item.id)}" data-version="${item.version || 1}" type="button">${esc(tr('governance.restoreProject'))}</button>
          </td>
        </tr>
      `).join('');
    }

    const canvasTbody = q('#canvasTrashRows');
    const canvases = currentOverview.canvases || [];
    if (!canvases.length) {
      canvasTbody.innerHTML = `<tr><td colspan="5" class="empty-td">${esc(tr('governance.emptyCanvasTrash'))}</td></tr>`;
    } else {
      canvasTbody.innerHTML = canvases.map(item => `
        <tr>
          <td>
            <div class="mono" style="font-size:var(--gw-type-body-sm);">${esc(item.id)}</div>
            <span class="gov-badge canvas">${esc(tr('governance.canvasType'))}</span>
          </td>
          <td><strong>${esc(item.title || item.id || tr('governance.unnamedCanvas'))}</strong></td>
          <td>${dateTime(item.deleted_at)}</td>
          <td class="muted">${item.deleted_at ? dateTime(Number(item.deleted_at) + 30 * 86400000) : '—'}</td>
          <td>
            <button class="btn small primary btn-restore-canvas" data-canvas-id="${esc(item.id)}" data-version="${Number(item.governance_version || 1)}" type="button">${esc(tr('governance.restore'))}</button>
          </td>
        </tr>
      `).join('');
    }
  }

  async function restoreAsset(button) {
    const { assetId, entryId } = button.dataset;
    try {
      button.disabled = true;
      button.textContent = tr('governance.restoring');
      await api(`/api/asset-registry/governance/assets/${encodeURIComponent(assetId)}/restore`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ recycle_entry_id: entryId }),
      });
      showToast(tr('governance.assetRestored'));
      loadOverview();
    } catch (err) {
      showToast(trf('governance.restoreFailed', { message: err.message }), true);
      button.disabled = false;
      button.textContent = tr('governance.restore');
    }
  }

  async function restoreProject(button) {
    const { projectId, version } = button.dataset;
    try {
      button.disabled = true;
      button.textContent = tr('governance.restoringProject');
      await api(`/api/asset-registry/governance/projects/${encodeURIComponent(projectId)}/restore`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ expected_version: Number(version) }),
      });
      showToast(tr('governance.projectRestored'));
      loadOverview();
    } catch (err) {
      showToast(trf('governance.projectRestoreFailed', { message: err.message }), true);
      button.disabled = false;
      button.textContent = tr('governance.restoreProject');
    }
  }

  async function restoreCanvas(button) {
    const { canvasId, version } = button.dataset;
    try {
      button.disabled = true;
      button.textContent = tr('governance.restoring');
      await api(`/api/asset-registry/governance/canvases/${encodeURIComponent(canvasId)}/restore`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ expected_version: Number(version) }),
      });
      showToast(tr('governance.canvasRestored'));
      loadOverview();
    } catch (err) {
      showToast(trf('governance.restoreFailed', { message: err.message }), true);
      button.disabled = false;
      button.textContent = tr('governance.restore');
    }
  }

  document.addEventListener('click', event => {
    const restoreAssetButton = event.target.closest('.btn-restore-asset');
    if (restoreAssetButton) return void restoreAsset(restoreAssetButton);
    const restoreProjectButton = event.target.closest('.btn-restore-project');
    if (restoreProjectButton) return void restoreProject(restoreProjectButton);
    const restoreCanvasButton = event.target.closest('.btn-restore-canvas');
    if (restoreCanvasButton) return void restoreCanvas(restoreCanvasButton);
    const tabButton = event.target.closest('.gov-tab');
    if (tabButton) setTab(tabButton.dataset.tab);
  });

  q('#govTabs')?.addEventListener('keydown', event => {
    const currentTab = event.target.closest('.gov-tab');
    if (!currentTab || !event.currentTarget.contains(currentTab)) return;
    const tabs = [...qAll('.gov-tab')];
    const currentIndex = tabs.indexOf(currentTab);
    if (currentIndex < 0) return;

    let nextIndex = currentIndex;
    if (event.key === 'ArrowLeft') nextIndex = (currentIndex - 1 + tabs.length) % tabs.length;
    else if (event.key === 'ArrowRight') nextIndex = (currentIndex + 1) % tabs.length;
    else if (event.key === 'Home') nextIndex = 0;
    else if (event.key === 'End') nextIndex = tabs.length - 1;
    else return;

    event.preventDefault();
    setTab(tabs[nextIndex].dataset.tab)?.focus();
  });

  q('#btnPreviewImpact')?.addEventListener('click', async () => {
    const targetType = q('#opTargetType').value;
    const targetId = q('#opTargetId').value.trim();
    if (!targetId) {
      showToast(tr('governance.targetIdRequired'), true);
      return;
    }
    const previewBox = q('#opPreviewBox');
    previewBox.style.display = 'block';
    previewBox.innerHTML = `<span class="muted">${esc(tr('governance.analyzingImpact'))}</span>`;
    try {
      const res = await api(`/api/asset-registry/governance/cascade-preview?target_type=${encodeURIComponent(targetType)}&target_id=${encodeURIComponent(targetId)}`);
      const impact = res.impact || {};
      previewBox.innerHTML = `
        <div style="font-weight:600;margin-bottom:6px;">📊 ${esc(tr('governance.cascadeImpactSummary'))}</div>
        <div>${esc(impact.summary || tr('governance.noSpecialCascadeImpact'))}</div>
        <div class="mono muted" style="font-size:var(--gw-type-body-sm);margin-top:6px;">
          ${Object.entries(impact).filter(([key]) => !['summary', 'ok'].includes(key)).map(([key, value]) => `${esc(key)}: ${esc(value)}`).join(' | ')}
        </div>
      `;
    } catch (err) {
      previewBox.innerHTML = `<span style="color:var(--color-danger,#e53935);">${esc(trf('governance.impactAnalysisFailed', { message: err.message }))}</span>`;
    }
  });

  q('#govOpForm')?.addEventListener('submit', async event => {
    event.preventDefault();
    const targetType = q('#opTargetType').value;
    const operation = q('#opOperation').value;
    const targetId = q('#opTargetId').value.trim();
    const rawParams = q('#opParamsJson').value.trim();
    let params = {};
    if (rawParams) {
      try {
        params = JSON.parse(rawParams);
      } catch (error) {
        showToast(tr('governance.invalidParamsJson'), true);
        return;
      }
    }
    const resultBox = q('#opResultBox');
    resultBox.style.display = 'block';
    resultBox.innerHTML = `<span class="muted">${esc(tr('governance.executingOperation'))}</span>`;
    try {
      const res = await api('/api/asset-registry/governance/operations', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ version: 1, operation, target_type: targetType, target_id: targetId, params }),
      });
      resultBox.innerHTML = `
        <div style="font-weight:600;color:var(--color-primary,#4f7cff);margin-bottom:6px;">✅ ${esc(tr('governance.operationSucceeded'))}</div>
        <div class="mono" style="font-size:var(--gw-type-body-sm);">${esc(trf('governance.operationResult', { action: res.action, entityType: targetTypeLabel(res.target_type), entityId: res.target_id }))}</div>
        <pre class="mono" style="margin-top:8px;font-size:var(--gw-type-body-sm);background:rgba(0,0,0,0.3);padding:8px;border-radius:4px;">${esc(JSON.stringify(res.result, null, 2))}</pre>
      `;
      showToast(tr('governance.operationAudited'));
      loadOverview();
    } catch (err) {
      const message = trf('governance.operationFailed', { message: err.message });
      resultBox.innerHTML = `<span style="color:var(--color-danger,#e53935);">${esc(message)}</span>`;
      showToast(message, true);
    }
  });

  q('#btnReconcileAudit')?.addEventListener('click', async () => {
    const button = q('#btnReconcileAudit');
    if (button?.disabled) return;
    if (button) button.disabled = true;
    try {
      const res = await api('/api/asset-registry/governance/audit-outbox/reconcile', { method: 'POST' });
      // 计数来自实际ACK结果；重新读回概览，不把failed误写成pending或伪报已全部送达。
      await loadOverview();
      const replayed = Number(res.count || 0);
      const remaining = Number(res.remaining || 0);
      const partial = res.data_status === 'partial';
      showToast(partial ? `本地审计收据部分失败：已确认 ${replayed} 项，剩余 ${remaining} 项；可重试。`
        : trf('governance.auditReconciled', { replayed, remaining }), partial);
    } catch (err) {
      showToast(trf('governance.auditReplayFailed', { message: err.message }), true);
    } finally {
      if (button) button.disabled = false;
    }
  });

  q('#btnPurgeExpiredCanvases')?.addEventListener('click', async () => {
    const previewVersion = ++purgePreviewVersion;
    const dialog = q('#govConfirmDialog');
    const title = q('#govConfirmTitle');
    const subtitle = q('#govConfirmSubtitle');
    const body = q('#govConfirmBody');
    const okButton = q('#btnGovConfirmOk');

    resetPurgeDialog();
    title.textContent = tr('governance.purgeExpiredTitle');
    subtitle.textContent = tr('governance.purgeExpiredSubtitle');
    body.innerHTML = `<span class="muted">${esc(tr('governance.previewingPurge'))}</span>`;
    setPurgeStatus(tr('governance.previewingPurge'));
    dialog.showModal();

    try {
      const preview = await api('/api/asset-registry/governance/canvases/purge-expired', {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ dry_run: true, retention_days: 30 }),
      });
      if (previewVersion !== purgePreviewVersion || !dialog.open) return;
      const expired = preview.expired || [];
      if (!expired.length) {
        body.innerHTML = `<p>${esc(tr('governance.noExpiredCanvases'))}</p>`;
        setPurgeStatus(tr('governance.noExpiredCanvases'));
      } else {
        body.innerHTML = `
          <p>${esc(trf('governance.expiredCanvasCount', { count: expired.length }))}</p>
          <ul style="margin:8px 0 16px 20px;font-size:var(--gw-type-body-md);">
            ${expired.map(canvas => `<li><span class="mono">${esc(canvas.id)}</span> - ${esc(canvas.title || tr('governance.untitled'))} (${esc(trf('governance.deletedOn', { date: dateTime(canvas.deleted_at) }))})</li>`).join('')}
          </ul>
          <label style="display:flex;align-items:center;gap:8px;font-weight:600;font-size:var(--gw-type-body-md);cursor:pointer;">
            <input type="checkbox" id="chkConfirmPurge"> ${esc(tr('governance.purgeAcknowledgement'))}
          </label>
        `;
        okButton.style.display = 'inline-block';
        okButton.disabled = true;
        setPurgeStatus(trf('governance.expiredCanvasCount', { count: expired.length }));
        q('#chkConfirmPurge')?.addEventListener('change', event => {
          okButton.disabled = previewVersion !== purgePreviewVersion || !dialog.open || !event.target.checked;
        });
        okButton.onclick = async () => {
          if (previewVersion !== purgePreviewVersion || !dialog.open || okButton.disabled) return;
          try {
            okButton.disabled = true;
            okButton.textContent = tr('governance.purging');
            const purgeRes = await api('/api/asset-registry/governance/canvases/purge-expired', {
              method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ confirm: true, retention_days: 30 }),
            });
            showToast(trf('governance.purgedCanvases', { count: (purgeRes.purged || []).length }));
            dialog.close();
            loadOverview();
          } catch (err) {
            showToast(trf('governance.purgeFailed', { message: err.message }), true);
            okButton.disabled = false;
            okButton.textContent = tr('governance.confirmExecute');
          }
        };
      }
    } catch (err) {
      if (previewVersion !== purgePreviewVersion || !dialog.open) return;
      body.innerHTML = `<p style="color:var(--color-danger,#e53935);">${esc(trf('governance.purgePreviewFailed', { message: err.message }))}</p>`;
      setPurgeStatus(trf('governance.purgePreviewFailed', { message: err.message }));
    }
  });

  q('#btnGovConfirmClose')?.addEventListener('click', () => q('#govConfirmDialog').close());
  q('#btnGovConfirmCancel')?.addEventListener('click', () => q('#govConfirmDialog').close());
  q('#govConfirmDialog')?.addEventListener('close', () => {
    purgePreviewVersion += 1;
    resetPurgeDialog();
    setPurgeStatus('');
  });

  function rerenderForLanguage() {
    renderOverview();
    renderTrash();
  }

  window.addEventListener('message', event => {
    if (event.origin && event.origin !== location.origin) return;
    if (event.data?.type === 'studio-lang' && window.StudioI18n) window.StudioI18n.set(event.data.lang || 'zh');
  });
  window.addEventListener('studio-lang-change', rerenderForLanguage);

  loadOverview();
})();
