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
 * C4-AC5b「未归属」收件箱：GET /api/assets/unassigned 只返回当前账户自己的无项目资产；
 * 无主资产只出现在管理员收件箱，后端标 source_unknown/origin_label。
 * 「移入项目」POST /api/assets/{asset_id}/move-to-project {project_id, expected_asset_version}。
 * expected_asset_version 取该条目的 asset_version（资产自身版本 CAS，其他资产变动不再冲突）。
 * 409 VERSION_CONFLICT（detail.asset_id）：重新读取该条目最新 asset_version，仍未归属则自动重试一次；
 * 409 ASSET_ALREADY_ASSIGNED：刷新列表并提示。匿名/无权（401/403）不显示入口。
 */
window.GWAssetJobStatus = (() => {
  'use strict';
  const NEXT_ACTION_LABEL = Object.freeze({
    poll_job: '等待任务状态更新',
    resume_index: '恢复索引任务',
    review_result: '查看索引结果',
    retry_index: '修复来源后重试索引',
    reconcile_index_job: '人工核对索引任务',
  });

  function escapeText(value) {
    return String(value ?? '').replace(/[&<>"']/g, ch => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[ch]));
  }

  function view(job = {}) {
    const status = String(job.status || 'unknown').trim().toLowerCase();
    const errorCode = String(job.error_code || '').trim();
    if (status === 'succeeded') return {status, kind: '', dataStatus: 'ok', label: '索引已完成', nextAction: 'review_result', resultUnknown: false, errorCode, message: '索引已完成'};
    if (status === 'queued' || status === 'running') return {status, kind: 'pending', dataStatus: 'degraded', label: status === 'queued' ? '索引排队中' : '索引执行中', nextAction: 'poll_job', resultUnknown: false, errorCode, message: status === 'queued' ? '索引排队中' : '索引执行中'};
    if (status === 'paused') return {status, kind: 'blocked', dataStatus: 'degraded', label: '索引已暂停', nextAction: 'resume_index', resultUnknown: false, errorCode, message: '索引已暂停'};
    if (status === 'failed') {
      const message = errorCode ? `索引失败：${errorCode}` : '索引失败';
      return {status, kind: 'degraded', dataStatus: 'degraded', label: '索引失败', nextAction: 'retry_index', resultUnknown: false, errorCode, message};
    }
    if (status === 'cancelled') return {status, kind: 'degraded', dataStatus: 'degraded', label: '索引已取消', nextAction: 'retry_index', resultUnknown: false, errorCode, message: '索引已取消'};
    if (status === 'interrupted') {
      const message = errorCode ? `索引中断，结果未知：${errorCode}` : '索引中断，结果未知';
      return {status, kind: 'unknown', dataStatus: 'degraded', label: '索引中断，结果未知', nextAction: 'reconcile_index_job', resultUnknown: true, errorCode, message};
    }
    return {status: 'unknown', kind: 'unknown', dataStatus: 'degraded', label: '索引状态未知', nextAction: 'reconcile_index_job', resultUnknown: true, errorCode, message: '索引状态未知'};
  }

  function render(job) {
    const result = view(job);
    const degradation = result.kind ? ` data-gw-degradation="${result.kind}"` : '';
    return `<div role="status" data-asset-job-status="${escapeText(result.status)}"${degradation}><strong>${escapeText(result.message)}</strong><span>下一步：${escapeText(NEXT_ACTION_LABEL[result.nextAction] || result.nextAction)}</span></div>`;
  }

  return Object.freeze({view, render});
})();

window.GWUnassignedInbox = (() => {
  'use strict';
  const INBOX = '/api/assets/unassigned';
  let snapshot = {assets: [], revision: null, total: 0};
  let projects = [];
  let busy = false;

  async function request(url, options = {}) {
    const res = await fetch(url, {credentials: 'same-origin', ...options});
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      const detail = data?.detail || {};
      const error = new Error(detail.message || `请求失败 (${res.status})`);
      error.status = res.status; error.code = detail.code;
      throw error;
    }
    return data;
  }

  /** 来源未知只信后端 source_unknown（无主资产，仅管理员收件箱可见），前端不做推断。 */
  function sourceUnknown(asset) { return asset?.source_unknown === true; }

  async function load() {
    const data = await request(`${INBOX}?limit=500`);
    snapshot = {assets: Array.isArray(data.assets) ? data.assets : (data.items || []), revision: data.revision, total: Number(data.total) || 0};
    return snapshot;
  }

  async function loadProjects() {
    const data = await request('/api/asset-registry/projects?archived=false');
    projects = (data.projects || []).filter(p => p && p.project_id && !p.archived_at && !p.deleted_at);
    return projects;
  }

  function entry() { return document.getElementById('btnUnassignedInbox'); }

  function syncToolbarEntry(button = entry()) {
    const frame = document.getElementById('assetVaultFrame');
    let toolbarButton = null;
    try { toolbarButton = frame?.contentDocument?.querySelector('[data-vault-shell-unassigned]') || null; }
    catch (_) { return; }
    if (!button || !toolbarButton) return;
    toolbarButton.style.display = button.style.display === 'none' ? 'none' : '';
    const count = button.querySelector('[data-unassigned-count]')?.textContent || '';
    const toolbarCount = toolbarButton.querySelector('[data-unassigned-count]');
    if (toolbarCount) toolbarCount.textContent = count;
    const failed = button.dataset?.unassignedLoadState === 'error';
    toolbarButton.title = failed ? '未归属资产暂时无法读取，点击重试' : '尚未归属任何项目的资产（仅本人可见）';
    if (button.dataset?.unassignedLoadState) toolbarButton.dataset.unassignedLoadState = button.dataset.unassignedLoadState;
    else delete toolbarButton.dataset.unassignedLoadState;
  }

  function clearEntryError(button) {
    document.getElementById('unassignedInboxEntryStatus')?.remove();
    button?.removeAttribute('aria-describedby');
    if (button?.dataset) delete button.dataset.unassignedLoadState;
  }

  function showEntryError(button) {
    let status = document.getElementById('unassignedInboxEntryStatus');
    if (!status) {
      status = document.createElement('div');
      status.id = 'unassignedInboxEntryStatus';
      status.dataset.unassignedEntryStatus = 'true';
      status.setAttribute('role', 'status');
      status.setAttribute('aria-live', 'polite');
      status.style.cssText = 'display:flex;align-items:center;justify-content:space-between;gap:8px;padding:4px 8px;color:#fbbf24;font-size:var(--gw-type-body-sm, 12px);';
      button.insertAdjacentElement('afterend', status);
    }
    status.innerHTML = '<span>未归属资产暂时无法读取。</span><button type="button" class="hw-btn btn-size-sm" data-unassigned-retry>重试</button>';
    status.querySelector('[data-unassigned-retry]').addEventListener('click', async event => {
      event.currentTarget.disabled = true;
      await refreshEntry();
    });
    button.setAttribute('aria-describedby', status.id);
    if (button.dataset) button.dataset.unassignedLoadState = 'error';
  }

  async function refreshEntry() {
    const button = entry();
    if (!button) return 'missing';
    try {
      await load();
      clearEntryError(button);
      button.style.display = '';
      const count = button.querySelector('[data-unassigned-count]');
      if (count) count.textContent = String(snapshot.total);
      if (button.dataset) button.dataset.unassignedLoadState = 'ready';
      syncToolbarEntry(button);
      return 'shown';
    } catch (error) {
      if (error.status === 401 || error.status === 403) {
        clearEntryError(button);
        button.style.display = 'none';
        syncToolbarEntry(button);
        return 'hidden';
      }
      button.style.display = '';
      showEntryError(button);
      syncToolbarEntry(button);
      return 'error';
    }
  }

  function notify(message, isError = false) {
    const status = document.getElementById('unassignedInboxStatus');
    if (status) { status.textContent = message; status.dataset.error = isError ? 'true' : 'false'; }
    if (typeof window.showWorkbenchToast === 'function') window.showWorkbenchToast(message, isError, '', null, 5000);
  }

  function escapeText(value) {
    return String(value ?? '').replace(/[&<>"']/g, ch => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[ch]));
  }

  function render() {
    const dialog = document.getElementById('unassignedInboxDialog');
    const list = dialog?.querySelector('[data-unassigned-list]');
    if (!list) return;
    const options = projects.map(p => `<option value="${escapeText(p.project_id)}">${escapeText(p.name || p.project_id)}</option>`).join('');
    list.innerHTML = snapshot.assets.length ? snapshot.assets.map(asset => `
      <li class="flex items-center gap-2 py-1.5 border-b border-white/5" data-asset-id="${escapeText(asset.asset_id)}">
        <span class="flex-1 truncate" title="${escapeText(asset.asset_id)}">${escapeText(asset.name || asset.asset_id)}</span>
        ${sourceUnknown(asset) ? `<span class="text-amber-300 gw-type-body-sm" data-source-unknown="true" title="无可证明归属的资产，仅管理员可见">${escapeText(asset.origin_label || '来源未知')}</span>`
          : (asset.origin_label ? `<span class="text-slate-400 gw-type-body-sm" data-origin-label>${escapeText(asset.origin_label)}</span>` : '')}
        <select data-move-project style="background:#141720;color:#eddab3;border:1px solid rgba(223,195,132,.35);border-radius:6px;padding:2px 6px;" aria-label="目标项目" ${projects.length ? '' : 'disabled'}>${options || '<option value="">暂无可写项目</option>'}</select>
        <button type="button" class="hw-btn btn-size-sm" data-move-asset ${projects.length ? '' : 'disabled'}>移入项目</button>
      </li>`).join('') : '<li class="py-3 text-slate-400">没有未归属资产</li>';
    list.querySelectorAll('[data-move-asset]').forEach(button => button.addEventListener('click', () => {
      const row = button.closest('[data-asset-id]');
      moveToProject(row.dataset.assetId, row.querySelector('[data-move-project]')?.value);
    }));
    const count = entry()?.querySelector('[data-unassigned-count]');
    if (count) count.textContent = String(snapshot.total);
  }

  function ensureDialog() {
    let dialog = document.getElementById('unassignedInboxDialog');
    if (dialog) return dialog;
    dialog = document.createElement('div');
    dialog.id = 'unassignedInboxDialog';
    dialog.setAttribute('data-floating-surface', 'unassigned-inbox-dialog');
    dialog.setAttribute('role', 'dialog');
    dialog.setAttribute('aria-label', '未归属资产');
    dialog.style.cssText = 'position:fixed;inset:0;z-index:80;display:flex;align-items:center;justify-content:center;background:rgba(0,0,0,.6);';
    dialog.innerHTML = `<div data-floating-content class="bg-[#0b0d12] border border-[#dfc384]/35 rounded-xl p-4 text-[#eddab3] gw-type-body-sm" style="width:min(640px,92vw);max-height:80vh;overflow:auto;">
      <div class="flex items-center justify-between mb-2"><strong>未归属资产</strong>
        <button type="button" class="hw-btn btn-size-sm" data-floating-close data-close-inbox>关闭</button></div>
      <p class="text-slate-400 mb-2">这些资产尚未归属任何项目；系统不会自动归属，请手动选择项目移入。</p>
      <p id="unassignedInboxStatus" role="status" aria-live="polite" class="mb-1"></p>
      <ul data-unassigned-list></ul></div>`;
    dialog.querySelector('[data-close-inbox]').addEventListener('click', close);
    dialog.addEventListener('click', event => { if (event.target === dialog) close(); });
    document.body.appendChild(dialog);
    return dialog;
  }

  async function open() {
    ensureDialog();
    try {
      await Promise.all([load(), loadProjects()]);
      render();
      return 'open';
    } catch (error) {
      notify(`无法读取未归属资产：${error.message}`, true);
      return 'error';
    }
  }

  function close() { document.getElementById('unassignedInboxDialog')?.remove(); }

  async function moveToProject(assetId, projectId) {
    if (busy) return 'busy';
    if (!projectId) { notify('请先选择目标项目', true); return 'no_project'; }
    busy = true;
    const versionOf = () => snapshot.assets.find(a => a.asset_id === assetId)?.asset_version;
    const post = assetVersion => request(`/api/assets/${encodeURIComponent(assetId)}/move-to-project`, {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({project_id: projectId, expected_asset_version: assetVersion})});
    try {
      try {
        await post(versionOf());
      } catch (error) {
        if (error.status !== 409) throw error;
        await load();
        render();
        if (error.code === 'ASSET_ALREADY_ASSIGNED' || !snapshot.assets.some(a => a.asset_id === assetId)) {
          notify('该资产已被归属到项目，列表已刷新', true);
          return 'already_assigned';
        }
        try { await post(versionOf()); }
        catch (retryError) {
          if (retryError.status === 409) { await load().catch(() => {}); render(); notify('移入失败：该资产刚被其他操作修改，请稍后重试', true); return 'conflict'; }
          throw retryError;
        }
      }
      await load();
      render();
      const project = projects.find(p => p.project_id === projectId);
      notify(`已移入项目「${project?.name || projectId}」`);
      return 'moved';
    } catch (error) {
      notify(`移入项目失败：${error.message}`, true);
      return 'error';
    } finally {
      busy = false;
    }
  }

  return {open, close, refreshEntry, moveToProject, sourceUnknown, load, get snapshot() { return snapshot; }};
})();

/** The Workbench shell delegates all asset operations to the canonical asset manager. */
window.WorkbenchAssets = (() => {
  'use strict';
  let frame = null;
  let status = null;
  let boundFrame = null;
  let frameLoadHandler = null;
  // 【QIN-566】外壳侧栏状态：数据由内嵌页推送（同源直调），外壳只负责渲染与选中联动。
  let categories = null;
  let createButton = null;
  let createButtonClickHandler = null;
  let sidebarSnapshot = '';
  // 【QIN-603 AC3】结构签名（不含选中态与计数）：用来区分「只换选中项 / 只变计数 / 真的换树」。
  let sidebarStructure = '';

  function manager() {
    try { return frame.contentDocument; } catch (_) { return null; }
  }

  // 父页状态条必须明说「未接入」并带结构化标记（用户 2026-09-21 裁决第 3 项）。
  function setVaultStatus(text) {
    const projected = text && typeof text === 'object' ? text : null;
    const value = projected?.message || text || '准备就绪';
    const kind = projected?.kind || (/未纳入当前切片/.test(value) ? 'not_integrated'
      : (/暂时不可用/.test(value) ? 'service_unavailable' : ''));
    if (!status) return;
    status.textContent = value;
    status.title = value;
    if (kind) status.setAttribute('data-gw-degradation', kind);
    else status.removeAttribute('data-gw-degradation');
    if (status.dataset) {
      if (projected?.nextAction) status.dataset.nextAction = projected.nextAction;
      else delete status.dataset.nextAction;
    }
  }

  // 把 iframe 内的显式降级同步到父页状态条，避免父页看起来「一切正常」。
  function mirrorFrameDegradation() {
    const doc = manager();
    if (!doc) return;
    const marked = doc.querySelector('[data-gw-degradation]');
    if (!marked) return;
    const kind = marked.getAttribute('data-gw-degradation') || 'not_integrated';
    const text = (marked.textContent || '').trim();
    status.textContent = text;
    status.title = text;
    status.setAttribute('data-gw-degradation', kind);
  }
  function waitForControl(selector) {
    const doc = manager();
    if (!doc) return Promise.reject(new Error('资产库暂不可访问，请刷新页面。'));
    const found = doc.querySelector(selector);
    if (found) return Promise.resolve(found);
    return new Promise((resolve, reject) => {
      const watch = new MutationObserver(() => {
        const control = doc.querySelector(selector);
        if (control) { clearTimeout(timer); watch.disconnect(); resolve(control); }
      });
      const timer = setTimeout(() => {
        watch.disconnect();
        reject(new Error('资产库尚未就绪，请检查登录状态或刷新页面。'));
      }, 10000);
      watch.observe(doc.body, {childList: true, subtree: true});
    });
  }

  // 【QIN-566】外壳侧栏唯一导航：内嵌页推送树快照，外壳渲染并联动选中态（同源直调，无新增 API）。
  function updateSidebarSelection(id) {
    const selectedId = String(id || '');
    categories?.querySelectorAll('[data-asset-tree-entry]').forEach(button => {
      const active = button.dataset.assetTreeEntry === selectedId;
      button.classList.toggle('is-active', active);
      button.classList.toggle('active', active);
      if (active) button.setAttribute('aria-current', 'page');
      else button.removeAttribute('aria-current');
    });
  }

  // 【QIN-579】整理能力桥接对象获取与侧栏交互支撑
  function organizeBridge() {
    try { return frame?.contentWindow?.GWAssetOrganizeBridge || null; } catch(_) { return null; }
  }

  let shellOrganizeDrag = null;
  let shellDropHint = null;

  function clearShellDropHint() {
    if (shellDropHint?.button) {
      shellDropHint.button.classList.remove('is-organize-drop-into', 'is-organize-drop-before', 'is-organize-drop-after');
    }
    shellDropHint = null;
  }

  function closeShellOrganizeMenu() {
    document.getElementById('assetOrganizeMenu')?.remove();
  }

  function showShellOrganizeToast(text, undoable = false) {
    document.getElementById('assetOrganizeToast')?.remove();
    const toast = document.createElement('div');
    toast.id = 'assetOrganizeToast';
    toast.className = 'asset-organize-toast';
    toast.setAttribute('role', 'status');
    toast.innerHTML = `<i data-lucide="folder-cog"></i><span>${text}</span>${undoable ? '<button type="button" class="asset-organize-toast-undo" data-asset-organize-undo-now><i data-lucide="undo-2"></i><span>撤销</span></button>' : ''}`;
    document.body.appendChild(toast);
    window.lucide?.createIcons();
    setTimeout(() => toast.remove(), 4200);
  }

  function openShellOrganizeMenu(target, clientX, clientY) {
    closeShellOrganizeMenu();
    const menu = document.createElement('div');
    menu.id = 'assetOrganizeMenu';
    menu.className = 'asset-organize-menu';
    menu.setAttribute('role', 'menu');
    menu.setAttribute('aria-label', '文件夹整理');
    const title = document.createElement('div');
    title.className = 'asset-organize-menu-title';
    title.innerHTML = `<i data-lucide="folder-cog"></i><span>${target.name || '文件夹整理'}</span>`;
    menu.appendChild(title);

    const isRoot = Boolean(target.isRoot);
    const items = [
      { id: 'new', attr: 'data-asset-organize-new', label: '新建子文件夹', icon: 'folder-plus', disabled: false },
      { id: 'rename', attr: 'data-asset-organize-rename', label: '重命名', icon: 'pencil', disabled: isRoot },
      { id: 'open', attr: 'data-asset-organize-open', label: '在系统中打开', icon: 'folder-open', disabled: false },
      { id: 'delete', attr: 'data-asset-organize-delete', label: '删除（进回收站）', icon: 'trash-2', disabled: isRoot, danger: true },
    ];

    items.forEach(item => {
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.setAttribute('role', 'menuitem');
      btn.setAttribute(item.attr, '');
      if (item.disabled) btn.disabled = true;
      if (item.danger) btn.className = 'is-danger';
      btn.innerHTML = `<i data-lucide="${item.icon}"></i><span>${item.label}</span>`;
      btn.addEventListener('click', async (e) => {
        e.stopPropagation();
        closeShellOrganizeMenu();
        const bridge = organizeBridge();
        if (!bridge) return;
        if (item.id === 'new') openShellCreateDialog(target.key);
        else if (item.id === 'rename') beginShellRename(target.key);
        else if (item.id === 'open') {
          const res = await bridge.openInSystem(target.key);
          if (res?.status === 'opened') showShellOrganizeToast('已在系统中打开文件夹');
          else showShellOrganizeToast(res?.message || '无法在系统中打开该文件夹');
        } else if (item.id === 'delete') {
          openShellDeleteDialog(target.key, target.name);
        }
      });
      menu.appendChild(btn);
    });

    menu.style.left = `${Math.min(clientX, window.innerWidth - 230)}px`;
    menu.style.top = `${Math.min(clientY, window.innerHeight - 180)}px`;
    document.body.appendChild(menu);
    window.lucide?.createIcons();
  }

  function openShellCreateDialog(parentKey) {
    document.querySelectorAll('#assetOrganizeDialogOverlay, .asset-organize-dialog-overlay').forEach(el => el.remove());
    const overlay = document.createElement('div');
    overlay.id = 'assetOrganizeDialogOverlay';
    overlay.className = 'asset-organize-dialog-overlay';
    overlay.innerHTML = `
      <div class="asset-organize-dialog" id="assetOrganizeDialog" role="dialog" aria-modal="true" aria-label="新建子文件夹">
        <header>
          <strong>新建子文件夹</strong>
          <button type="button" class="asset-organize-dialog-close" data-asset-organize-dialog-cancel aria-label="关闭">
            <i data-lucide="x"></i>
          </button>
        </header>
        <input class="asset-organize-dialog-input" data-asset-organize-dialog-input placeholder="文件夹名称" maxlength="120" aria-label="新文件夹名称">
        <p class="asset-organize-dialog-error" data-asset-organize-dialog-error style="color:#ef4444;font-size:var(--gw-type-body-sm);padding:4px 14px 0;margin:0;min-height:20px;"></p>
        <footer>
          <button class="hw-btn" type="button" data-asset-organize-dialog-cancel>取消</button>
          <button class="hw-btn hw-btn-gold" type="button" data-asset-organize-dialog-confirm>创建</button>
        </footer>
      </div>
    `;
    document.body.appendChild(overlay);
    window.lucide?.createIcons();

    const input = overlay.querySelector('input');
    const errorEl = overlay.querySelector('[data-asset-organize-dialog-error]');
    const confirmBtn = overlay.querySelector('[data-asset-organize-dialog-confirm]');
    input?.focus();

    let isSubmitting = false;
    const confirm = async () => {
      if (isSubmitting) return;
      const name = String(input?.value || '').trim();
      if (!name) {
        if (errorEl) errorEl.textContent = '请输入文件夹名称';
        input?.focus();
        return;
      }
      isSubmitting = true;
      if (confirmBtn) {
        confirmBtn.disabled = true;
        confirmBtn.textContent = '创建中...';
      }
      if (errorEl) errorEl.textContent = '';
      try {
        const bridge = organizeBridge();
        if (!bridge?.createFolder) throw new Error('资产库尚未就绪，请稍后重试');
        await bridge.createFolder(parentKey, name);
        overlay.remove();
        showShellOrganizeToast(`已新建文件夹「${name}」`, true);
      } catch (err) {
        if (errorEl) errorEl.textContent = err.message || '创建文件夹失败';
        isSubmitting = false;
        if (confirmBtn) {
          confirmBtn.disabled = false;
          confirmBtn.textContent = '创建';
        }
      }
    };

    overlay.addEventListener('click', (e) => {
      const cancelEl = e.target.closest('[data-asset-organize-dialog-cancel]');
      if (cancelEl || e.target === overlay) {
        e.preventDefault();
        e.stopPropagation();
        overlay.remove();
        return;
      }
      const confirmEl = e.target.closest('[data-asset-organize-dialog-confirm]');
      if (confirmEl) {
        e.preventDefault();
        e.stopPropagation();
        void confirm();
        return;
      }
    });

    input?.addEventListener('keydown', (e) => {
      if (e.key === 'Enter') {
        e.preventDefault();
        e.stopPropagation();
        void confirm();
      } else if (e.key === 'Escape') {
        e.preventDefault();
        e.stopPropagation();
        overlay.remove();
      }
    });
  }

  function openShellDeleteDialog(key, name) {
    document.querySelectorAll('#assetOrganizeDialogOverlay, .asset-organize-dialog-overlay').forEach(el => el.remove());
    const overlay = document.createElement('div');
    overlay.id = 'assetOrganizeDialogOverlay';
    overlay.className = 'asset-organize-dialog-overlay';
    overlay.innerHTML = `
      <div class="asset-organize-dialog" id="assetOrganizeDialog" role="dialog" aria-modal="true" aria-label="删除文件夹">
        <header>
          <strong>删除文件夹</strong>
          <button type="button" class="asset-organize-dialog-close" data-asset-organize-dialog-cancel aria-label="关闭">
            <i data-lucide="x"></i>
          </button>
        </header>
        <p class="asset-organize-dialog-hint">将文件夹「${name || '文件夹'}」移入应用回收站？（源文件与历史索引均保留，可用 Ctrl+Z 撤销）</p>
        <p class="asset-organize-dialog-error" data-asset-organize-dialog-error style="color:#ef4444;font-size:var(--gw-type-body-sm);padding:4px 14px 0;margin:0;min-height:20px;"></p>
        <footer>
          <button class="hw-btn" type="button" data-asset-organize-dialog-cancel>取消</button>
          <button class="hw-btn hw-btn-danger" type="button" data-asset-organize-dialog-confirm>删除</button>
        </footer>
      </div>
    `;
    document.body.appendChild(overlay);
    window.lucide?.createIcons();

    const errorEl = overlay.querySelector('[data-asset-organize-dialog-error]');
    const confirmBtn = overlay.querySelector('[data-asset-organize-dialog-confirm]');

    let isSubmitting = false;
    const confirm = async () => {
      if (isSubmitting) return;
      isSubmitting = true;
      if (confirmBtn) {
        confirmBtn.disabled = true;
        confirmBtn.textContent = '删除中...';
      }
      try {
        const bridge = organizeBridge();
        if (!bridge?.deleteFolder) throw new Error('资产库尚未就绪，请稍后重试');
        await bridge.deleteFolder(key);
        overlay.remove();
        showShellOrganizeToast(`已移入回收站：${name}`, true);
      } catch (err) {
        if (errorEl) errorEl.textContent = err.message || '删除文件夹失败';
        isSubmitting = false;
        if (confirmBtn) {
          confirmBtn.disabled = false;
          confirmBtn.textContent = '删除';
        }
      }
    };

    overlay.addEventListener('click', (e) => {
      const cancelEl = e.target.closest('[data-asset-organize-dialog-cancel]');
      if (cancelEl || e.target === overlay) {
        e.preventDefault();
        e.stopPropagation();
        overlay.remove();
        return;
      }
      const confirmEl = e.target.closest('[data-asset-organize-dialog-confirm]');
      if (confirmEl) {
        e.preventDefault();
        e.stopPropagation();
        void confirm();
        return;
      }
    });

    overlay.addEventListener('keydown', (e) => {
      if (e.key === 'Escape') {
        e.preventDefault();
        e.stopPropagation();
        overlay.remove();
      }
    });
  }

  function beginShellRename(key) {
    const button = categories.querySelector(`[data-asset-tree-entry="${CSS.escape(key)}"]`);
    if (!button) return;
    const labelSpan = button.querySelector('.asset-shell-nav-label');
    if (!labelSpan || button.querySelector('input')) return;
    const oldName = labelSpan.textContent;
    const input = document.createElement('input');
    input.type = 'text';
    input.className = 'asset-organize-rename-input';
    input.value = oldName;
    input.setAttribute('data-asset-organize-rename-input', key);
    labelSpan.style.display = 'none';
    labelSpan.after(input);
    input.focus();
    input.select();

    let committed = false;
    const commit = async (save) => {
      if (committed) return;
      committed = true;
      const newName = input.value.trim();
      input.remove();
      labelSpan.style.display = '';
      if (save && newName && newName !== oldName) {
        labelSpan.textContent = newName;
        await organizeBridge()?.commitRename(key, newName);
      }
    };

    input.onkeydown = (e) => {
      if (e.key === 'Enter') { e.stopPropagation(); commit(true); }
      else if (e.key === 'Escape') { e.stopPropagation(); commit(false); }
    };
    input.onblur = () => commit(true);
  }

  function bindShellDragEvents(button, row) {
    if (row.draggable) {
      button.ondragstart = (e) => {
        shellOrganizeDrag = { key: row.key };
        e.dataTransfer?.setData('application/x-gw-organize', row.key);
        if (e.dataTransfer) e.dataTransfer.effectAllowed = 'move';
        button.classList.add('is-organize-dragging');
      };
      button.ondragend = () => {
        shellOrganizeDrag = null;
        clearShellDropHint();
        button.classList.remove('is-organize-dragging');
      };
    }

    button.ondragover = (e) => {
      const bridge = organizeBridge();
      const canOrganize = bridge?.canOrganize(row.key);
      if (!canOrganize) return;
      const isFolderDrag = Boolean(shellOrganizeDrag?.key);
      const isAssetDrag = Boolean(window.GWAssetDragState?.assetIds?.length);
      if (!isFolderDrag && !isAssetDrag) return;
      if (isFolderDrag && shellOrganizeDrag.key === row.key) return;

      e.preventDefault();
      if (e.dataTransfer) e.dataTransfer.dropEffect = 'move';
      const rect = button.getBoundingClientRect();
      const relY = (e.clientY - rect.top) / rect.height;
      const isRoot = Boolean(row.isRoot || (row.key.startsWith('mine:root:') && !row.key.includes('::')));
      let zone = 'into';
      if (!isRoot && isFolderDrag) {
        if (relY < 0.25) zone = 'before';
        else if (relY > 0.75) zone = 'after';
      }
      if (shellDropHint?.button !== button || shellDropHint?.zone !== zone) {
        clearShellDropHint();
        shellDropHint = { button, zone };
        if (zone === 'into') button.classList.add('is-organize-drop-into');
        else if (zone === 'before') button.classList.add('is-organize-drop-before');
        else button.classList.add('is-organize-drop-after');
      }
    };

    button.ondragleave = () => {
      if (shellDropHint?.button === button) clearShellDropHint();
    };

    button.ondrop = async (e) => {
      const bridge = organizeBridge();
      if (!bridge?.canOrganize(row.key)) return;
      e.preventDefault();
      const zone = shellDropHint?.zone || 'into';
      clearShellDropHint();

      if (shellOrganizeDrag?.key) {
        const dragKey = shellOrganizeDrag.key;
        shellOrganizeDrag = null;
        await bridge.moveFolder(dragKey, row.key, zone);
      } else if (window.GWAssetDragState?.assetIds?.length) {
        const assetIds = window.GWAssetDragState.assetIds;
        window.GWAssetDragState = null;
        await bridge.moveAssets(assetIds, row.key);
      }
    };
  }

  // 侧栏卡片渲染：支持折叠箭头、子目录树、拖拽与右键整理
  function shellNavButton(row, isSub = false) {
    const button = document.createElement('button');
    button.type = 'button';
    button.className = `asset-shell-nav-button tree-node-card${row.active ? ' is-active active' : ''}${isSub ? ' asset-shell-nav-button-sub' : ''}`;
    if (row.active) button.setAttribute('aria-current', 'page');
    button.title = row.name;
    button.dataset.assetTreeEntry = row.key;
    if (row.draggable) {
      button.setAttribute('draggable', 'true');
      button.setAttribute('data-asset-tree-drag', row.key);
    }

    const pin = document.createElement('div');
    pin.className = 'card-connector-pin';
    pin.setAttribute('aria-hidden', 'true');

    button.append(pin);

    // 展开/折叠箭头
    if (row.hasChildren) {
      const twisty = document.createElement('span');
      twisty.className = 'asset-shell-twisty';
      twisty.setAttribute('data-asset-tree-toggle', row.key);
      twisty.title = row.expanded ? '折叠' : '展开';
      twisty.innerHTML = `<i data-lucide="${row.expanded ? 'chevron-down' : 'chevron-right'}"></i>`;
      twisty.addEventListener('click', (e) => {
        e.stopPropagation();
        organizeBridge()?.toggleExpand(row.key);
      });
      button.append(twisty);
    }

    const icon = document.createElement('i');
    icon.setAttribute('data-lucide', row.icon || 'folder');

    const label = document.createElement('span');
    label.className = 'asset-shell-nav-label';
    label.textContent = row.name;

    button.append(icon, label);

    if (row.badge > 0) {
      const badge = document.createElement('span');
      badge.className = 'asset-shell-nav-badge';
      badge.textContent = row.badge > 99 ? '99+' : row.badge;
      button.append(badge);
    } else if (row.loading) {
      const count = document.createElement('span');
      count.className = 'asset-shell-nav-count is-loading';
      count.textContent = '…';
      button.append(count);
    } else if (row.count !== '' && row.count !== undefined && row.count !== null && Number(row.count) >= 0) {
      const count = document.createElement('span');
      count.className = 'asset-shell-nav-count';
      count.textContent = Number(row.count).toLocaleString('zh-CN');
      button.append(count);
    }

    if (row.offline) {
      const offline = document.createElement('span');
      offline.className = 'asset-shell-nav-offline';
      offline.textContent = '离线';
      const countEl = button.querySelector('.asset-shell-nav-count, .asset-shell-nav-badge');
      if (countEl) button.insertBefore(offline, countEl);
      else button.append(offline);
    }

    button.addEventListener('click', () => activateTreeEntry(row.key));

    // 双击内联改名
    button.addEventListener('click', (e) => {
      if (Number(e.detail) >= 2 && !row.isRoot && organizeBridge()?.canOrganize(row.key)) {
        e.stopPropagation();
        beginShellRename(row.key);
      }
    });

    // 右键整理菜单
    button.addEventListener('contextmenu', (e) => {
      if (organizeBridge()?.canOrganize(row.key)) {
        e.preventDefault();
        e.stopPropagation();
        openShellOrganizeMenu(row, e.clientX, e.clientY);
      }
    });

    // 拖拽移动交互
    bindShellDragEvents(button, row);

    // 展开子目录递归输出
    if (row.expanded && Array.isArray(row.children) && row.children.length > 0) {
      const wrapper = document.createElement('div');
      wrapper.className = 'asset-shell-nav-node-wrapper';
      wrapper.append(button);
      const sublist = document.createElement('div');
      sublist.className = 'asset-shell-nav-subtree';
      sublist.setAttribute('role', 'group');
      row.children.forEach(child => {
        sublist.append(shellNavButton(child, true));
      });
      wrapper.append(sublist);
      return wrapper;
    }

    return button;
  }

  // 「资产总览」标题卡高亮随选中入口同步（registry = 总览态）。
  function syncOverviewSelection(selected) {
    const overview = document.getElementById('assetSidebarOverview');
    const registryActive = selected === 'registry';
    overview?.classList.toggle('is-active', registryActive);
    overview?.classList.toggle('active', registryActive);
    overview?.setAttribute('aria-pressed', String(registryActive));
    if (registryActive) overview?.setAttribute('aria-current', 'page');
    else overview?.removeAttribute('aria-current');
    overview?.closest('.asset-shell-overview-card')?.classList.toggle('is-active', registryActive);
    overview?.closest('.asset-shell-overview-card')?.classList.toggle('active', registryActive);
  }

  // 【QIN-603 AC3】结构没变、只有计数/徽标变了：就地改文本节点。
  // 返回 false 表示某一行与现有 DOM 不匹配（例如多了子目录节点），交回整树重建，不硬改。
  function patchSidebarCounts(rows) {
    for (const row of rows) {
      if (Array.isArray(row.children) && row.children.length) return false;
      const button = categories.querySelector(`[data-asset-tree-entry="${CSS.escape(String(row.key || ''))}"]`);
      if (!button) return false;
      const badgeEl = button.querySelector('.asset-shell-nav-badge');
      const countEl = button.querySelector('.asset-shell-nav-count');
      const wantBadge = Number(row.badge) > 0 ? (Number(row.badge) > 99 ? '99+' : String(Number(row.badge))) : '';
      if (wantBadge) {
        if (!badgeEl || countEl) return false;
        if (badgeEl.textContent !== wantBadge) badgeEl.textContent = wantBadge;
        continue;
      }
      const hasCount = row.count !== '' && row.count !== undefined && row.count !== null && Number(row.count) >= 0;
      if (hasCount) {
        if (badgeEl) return false;
        if (!countEl) return false;
        const want = Number(row.count).toLocaleString('zh-CN');
        if (countEl.textContent !== want) countEl.textContent = want;
        continue;
      }
      if (badgeEl || countEl) return false;
    }
    return true;
  }

  // 应用内嵌页推送的树快照：渲染收集箱 + 挂载资源（统一资源库总文件夹与物理子目录树）。
  function applyTreeSnapshot(snapshot) {
    if (!categories || !snapshot || typeof snapshot !== 'object') return;
    const selected = String(snapshot.selected_key || '');
    const badge = Math.max(0, Number(snapshot.inbox_badge || 0));
    const rawRoots = Array.isArray(snapshot.roots) ? snapshot.roots : [];
    const roots = rawRoots.filter(root => {
      const id = String(root?.id || '').toLowerCase();
      const name = String(root?.name || '').trim();
      return id !== 'video' && id !== 'image' && name !== '视频库素材' && name !== '图片库素材';
    });
    const seenRootPaths = new Map();
    const dedupedRoots = [];
    roots.forEach(root => {
      const path = String(root?.path || '').replace(/\\/g, '/').toLowerCase().trim();
      if(path){
        if(seenRootPaths.has(path)){
          const existing = dedupedRoots[seenRootPaths.get(path)];
          const count = Number(root?.count || 0);
          const existingCount = Number(existing?.count || 0);
          if(!existingCount && count > 0){
            existing.count = count;
          }
          if(root.name && (!existing.name || existing.name.endsWith('素材') || existing.name.startsWith('local_extra_'))){
            existing.name = root.name;
          }
          return;
        }
        seenRootPaths.set(path, dedupedRoots.length);
      }
      dedupedRoots.push(root);
    });
    const knownRootIds = new Set(dedupedRoots.map(r => String(r.id || '')));
    const rawBoards = Array.isArray(snapshot.boards) ? snapshot.boards : [];
    const independentBoards = rawBoards.filter(board => {
      const boardRootId = String(board?.rootId || board?.sources?.[0]?.id || '');
      return !knownRootIds.has(boardRootId);
    });

    const rows = [
      {key:'mine:inbox', name:'收集箱', icon:'inbox', badge, count:'', hasChildren:false},
      ...dedupedRoots.map(root => ({...root, key:`mine:root:${root.id}`, name:String(root.name || '未命名来源'), icon:root.icon || 'hard-drive', count:root.count, isRoot:true})),
      ...independentBoards.map(board => ({key:`mine:board:${board.id}`, name:String(board.name || '未命名资源库'), icon:String(board.icon || 'library'), count:'', hasChildren:false})),
    ];
    // 【QIN-603 AC3】selected 不再掺进快照字符串：只改选中项时结构完全没变，
    // 不该整树 replaceChildren + 全文档重扫图标。选中态由 updateSidebarSelection 就地改 class。
    const rowShape = row => ({
      key: row.key,
      name: row.name,
      icon: row.icon || '',
      offline: Boolean(row.offline),
      loading: Boolean(row.loading),
      hasChildren: Boolean(row.hasChildren),
      childKeys: (Array.isArray(row.children) ? row.children : []).map(child => child?.key || ''),
    });
    const structureKey = JSON.stringify(rows.map(rowShape));
    const serialized = JSON.stringify(rows);
    updateSidebarSelection(selected);
    syncOverviewSelection(selected);
    if (serialized === sidebarSnapshot) return;
    // 结构没变、只有计数/徽标变了：就地改文本，不重建 DOM（选中态与焦点都不动）
    if (structureKey === sidebarStructure && patchSidebarCounts(rows)) {
      sidebarSnapshot = serialized;
      return;
    }
    sidebarStructure = structureKey;
    sidebarSnapshot = serialized;
    const focused = categories.contains(document.activeElement)
      ? document.activeElement.dataset.assetTreeEntry || null
      : null;
    const fragment = document.createDocumentFragment();

    // 主容器：保持「用户资源」属性兼容
    const mainGroup = document.createElement('div');
    mainGroup.className = 'asset-shell-nav-group';
    mainGroup.setAttribute('role', 'group');
    mainGroup.setAttribute('aria-label', '用户资源');

    const mainHeading = document.createElement('h3');
    mainHeading.className = 'asset-shell-nav-group-heading';
    mainHeading.textContent = '用户资源';
    const mainItems = document.createElement('div');
    mainItems.className = 'asset-shell-nav-group-items';

    // 1. 收集箱固定置顶
    mainItems.append(shellNavButton({
      key: 'mine:inbox',
      name: '收集箱',
      icon: 'inbox',
      badge,
      count: '',
      active: selected === 'mine:inbox',
      hasChildren: false,
    }));

    // 2. 挂载资源（统一资源库树，去掉「资源入口」「挂载文件夹」分段与子文件夹重复）
    const mountSection = document.createElement('div');
    mountSection.className = 'asset-shell-mount-section mt-2';
    const mountLabel = document.createElement('div');
    mountLabel.className = 'asset-tree-group-label';
    mountLabel.innerHTML = `<span>挂载资源</span><button class="asset-tree-group-add" type="button" data-asset-organize-group-new aria-label="新建文件夹" title="新建文件夹"><i data-lucide="plus"></i></button>`;
    mountLabel.querySelector('[data-asset-organize-group-new]')?.addEventListener('click', (e) => {
      e.stopPropagation();
      openShellCreateDialog('');
    });
    mountSection.append(mountLabel);

    if (!roots.length && !independentBoards.length) {
      const empty = document.createElement('span');
      empty.className = 'asset-shell-nav-empty';
      empty.textContent = '尚未挂载文件夹';
      mountSection.append(empty);
    } else {
      roots.forEach(root => {
        mountSection.append(shellNavButton({
          ...root,
          key: `mine:root:${root.id}`,
          name: String(root.name || '未命名来源'),
          icon: root.icon || 'hard-drive',
          count: root.count,
          loading: Boolean(root.loading),
          offline: Boolean(root.offline),
          isRoot: true,
          active: selected === `mine:root:${root.id}`,
        }));
      });
      independentBoards.forEach(board => {
        mountSection.append(shellNavButton({
          key: `mine:board:${board.id}`,
          name: String(board.name || '未命名资源库'),
          icon: String(board.icon || 'library'),
          count: '',
          active: selected === `mine:board:${board.id}`,
          hasChildren: false,
        }));
      });
    }
    mainItems.append(mountSection);

    mainGroup.append(mainHeading, mainItems);
    fragment.append(mainGroup);
    categories.replaceChildren(fragment);
    // 【QIN-603 AC3】图标替换只扫刚重建的侧栏容器：createIcons() 不带 root 会遍历整个文档，
    // 外壳上每一行表格/按钮的 [data-lucide] 都被重扫一遍，是这次点击里最贵的一段。
    window.lucide?.createIcons({root: categories});
    if (focused !== null) {
      categories.querySelector(`[data-asset-tree-entry="${CSS.escape(focused)}"]`)?.focus();
    }
  }

  // 点击外壳侧栏入口：直接调用内嵌页桥接，选中态先本地乐观更新。
  async function activateTreeEntry(key) {
    const selectedId = String(key || '');
    updateSidebarSelection(selectedId);
    syncOverviewSelection(selectedId);
    try {
      const bridge = frame?.contentWindow?.GWAssetTreeBridge;
      if (!bridge?.activate) throw new Error('资产库尚未就绪，请刷新页面。');
      bridge.activate(selectedId);
    } catch (error) {
      updateSidebarSelection('');
      setVaultStatus(error.message);
    }
  }

  // 初始与兜底拉取：iframe 加载完成后主动取一次快照，后续以推送为准。
  function requestSidebarSnapshot() {
    try {
      const bridge = frame?.contentWindow?.GWAssetTreeBridge;
      if (bridge?.snapshot) applyTreeSnapshot(bridge.snapshot());
    } catch (_) {}
  }

  function openRequestedAsset() {
    const assetId = new URLSearchParams(location.search).get('asset_id');
    if (!assetId || !frame?.contentWindow) return;
    frame.contentWindow.postMessage({ type: 'asset-global-search', asset_id: assetId }, location.origin);
  }
  async function openCreateAssetLibrary() {
    try {
      const button = await waitForControl('#assetBoardTabs [data-board-create]');
      button.click();
    } catch (error) { setVaultStatus(error.message); }
  }

  async function openAssetSettings() {
    try {
      const button = await waitForControl('#assetBoardTabs [data-board-settings-open]');
      button.click();
    } catch (error) { setVaultStatus(error.message); }
  }

  // 【QIN-533】中栏智能筛选页下线，本入口只负责开关「目录层级」机架（保留对外名称）
  function toggleSmartFilter() {
    const doc = manager();
    const panel = doc?.getElementById('registryFacetPopover');
    const root = doc?.getElementById('assetManagerRoot');
    if (!panel || !root) { setVaultStatus('目录层级尚未就绪，请刷新页面。'); return; }
    if (panel.hasAttribute('popover')) {
      if (panel.matches(':popover-open')) panel.hidePopover();
      else panel.showPopover();
      return;
    }
    root.classList.toggle('asset-registry-filters-collapsed');
  }

  // 【QIN-547】「上传资源」看板已并入收集箱：外壳上传入口改为进入收集箱再触发其上传按钮。
  async function uploadAsset() {
    try {
      const available = manager()?.querySelector('[data-inbox-upload]');
      if (available) { available.click(); return; }
      const node = await waitForControl('[data-asset-tree-node="mine:inbox"]');
      node.click();
      const upload = await waitForControl('[data-inbox-upload]');
      setVaultStatus('收集箱已打开');
      upload.focus();
    } catch (error) { setVaultStatus(error.message); }
  }

  function initFrame() {
    const doc = manager();
    if (!doc?.body) { setVaultStatus('资产库加载失败，请刷新页面。'); return; }
    const current = new URL(doc.URL);
    if (current.protocol === 'about:') return;
    if (current.origin === location.origin && current.pathname === '/static/episode-pipeline.html') {
      const target = new URL('/static/pages/workshop.html', location.origin);
      for (const key of ['project_id', 'pipeline_id']) {
        if (current.searchParams.has(key)) target.searchParams.set(key, current.searchParams.get(key));
      }
      target.searchParams.set('step', 'script');
      location.assign(target.href);
      return;
    }
    waitForControl('#assetBoardTabs [data-tab="registry"]').then(() => {
      setVaultStatus('');
      mirrorFrameDegradation();
      openRequestedAsset();
      requestSidebarSnapshot();
    }).catch(error => { setVaultStatus(error.message); });
  }
  function bindElements() {
    frame = document.getElementById('assetVaultFrame');
    status = document.getElementById('assetVaultStatus');
    categories = document.getElementById('assetCategories');
    const nextCreateButton = document.getElementById('assetSidebarCreate');
    if (nextCreateButton !== createButton) {
      createButton?.removeEventListener('click', createButtonClickHandler);
      createButton = nextCreateButton;
      createButtonClickHandler = createButton ? event => event.stopPropagation() : null;
      createButton?.addEventListener('click', createButtonClickHandler);
    }
    if (!frame) return false;
    if (boundFrame !== frame) {
      if (boundFrame && frameLoadHandler) boundFrame.removeEventListener('load', frameLoadHandler);
      frameLoadHandler = initFrame;
      frame.addEventListener('load', frameLoadHandler);
      boundFrame = frame;
    }
    return true;
  }

  function syncFrameSource() {
    if (!frame) return;
    const source = new URL(frame.dataset.src || '/static/asset-manager.html?embedded=1&vault=1', location.origin);
    const contextParams = new URLSearchParams(location.search);
    source.searchParams.delete('project_id');
    for (const key of ['pipeline_id', 'asset_id']) {
      if (contextParams.has(key)) source.searchParams.set(key, contextParams.get(key));
    }
    source.hash = location.hash;
    if (frame.dataset.gwBoundSrc !== source.href) {
      frame.dataset.gwBoundSrc = source.href;
      // 【QIN-573 AC1】幂等赋值：若已有相符的初始 src，不重复赋值避免子帧二次重载
      try {
        const currentHref = frame.getAttribute('src') || frame.src || '';
        const currentUrl = new URL(currentHref, location.origin);
        if (currentUrl.href !== source.href) {
          frame.src = source.href;
        }
      } catch (_) {
        frame.src = source.href;
      }
    }
  }

  // 资产库是全局入口，清除遗留工程参数，不读取或改写项目记忆。
  function settleProjectContext() {
    try {
      const url = new URL(location.href);
      if (!url.searchParams.has('project_id')) return;
      url.searchParams.delete('project_id');
      history.replaceState({}, '', url.toString());
    } catch (_) {}
  }

  function rebind() {
    window.GWUnassignedInbox?.refreshEntry();
    initSidebarCollapseState();
    if (!bindElements()) return;
    mirrorFrameDegradation();
    syncFrameSource();
    void settleProjectContext();
    if (frame.contentDocument?.readyState === 'complete') initFrame();
  }

  function saveBrowseStateBeforeRoute(event) {
    const currentFrame = document.getElementById('assetVaultFrame');
    if (!currentFrame) return;
    if (event?.type === 'click') {
      const link = event.target?.closest?.('.nav-pill-btn[href]');
      if (!link) return;
      const target = new URL(link.getAttribute('href') || '', location.href);
      if (target.origin !== location.origin || target.pathname === location.pathname) return;
    }
    try {
      const browseSession = currentFrame.contentWindow?.GWAssetBrowseSession;
      if (event?.type === 'pagehide') browseSession?.save?.();
      else browseSession?.saveForRoute?.();
    } catch (_) {}
  }
  if (!window.__gwAssetBrowseRouteSaveBound
      && typeof document.addEventListener === 'function'
      && typeof window.addEventListener === 'function') {
    document.addEventListener('click', saveBrowseStateBeforeRoute, true);
    window.addEventListener('popstate', saveBrowseStateBeforeRoute, true);
    window.addEventListener('pagehide', saveBrowseStateBeforeRoute);
    window.__gwAssetBrowseRouteSaveBound = true;
  }

  // 【QIN-579】外壳全局整理交互：点击外部关闭右键菜单、Esc 退出浮层、Ctrl+Z 撤销
  if (typeof document !== 'undefined' && typeof document.addEventListener === 'function') {
    document.addEventListener('click', (e) => {
      if (!e.target.closest?.('#assetOrganizeMenu')) closeShellOrganizeMenu();
      if (e.target.closest?.('[data-asset-organize-undo-now]')) {
        document.getElementById('assetOrganizeToast')?.remove();
        organizeBridge()?.undoLast();
      }
    });
    document.addEventListener('keydown', (e) => {
      if (e.key === 'Escape') {
        closeShellOrganizeMenu();
        document.getElementById('assetOrganizeDialogOverlay')?.remove();
      }
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'z') {
        const tag = document.activeElement?.tagName;
        if (tag !== 'INPUT' && tag !== 'TEXTAREA') {
          const bridge = organizeBridge();
          if (bridge?.hasUndo?.()) {
            e.preventDefault();
            bridge.undoLast();
          }
        }
      }
    });
  }

  function showIndexJobStatus(job) {
    const view = window.GWAssetJobStatus.view(job);
    setVaultStatus(view);
    return view;
  }

  // 【QIN-590】资产库左侧栏展开/收起与 Coverflow 自动收起铺满
  const SIDEBAR_COLLAPSED_KEY = 'gw:asset-sidebar-collapsed';
  let sidebarCollapsed = false;
  let previousStateBeforeCoverflow = false;
  let userManuallyToggledDuringCoverflow = false;
  let isCoverflowActive = false;

  function isSidebarCollapsed() {
    return Boolean(sidebarCollapsed);
  }

  function setSidebarCollapsed(collapsed, options = {}) {
    const next = Boolean(collapsed);
    sidebarCollapsed = next;
    const sidebar = document.getElementById('assetVaultSidebar');
    const toggleBtn = document.getElementById('assetSidebarToggle');

    sidebar?.classList.toggle('is-collapsed', next);
    if (toggleBtn) {
      toggleBtn.setAttribute('aria-expanded', String(!next));
      const title = next ? '展开侧栏' : '收起侧栏';
      toggleBtn.title = title;
      toggleBtn.setAttribute('aria-label', title);
      const iconName = next ? 'panel-left-open' : 'panel-left-close';
      const existingIcon = toggleBtn.querySelector('i, svg');
      if (existingIcon) {
        existingIcon.setAttribute('data-lucide', iconName);
        if (typeof lucide !== 'undefined' && typeof lucide.createIcons === 'function') {
          lucide.createIcons({root: toggleBtn});
        }
      }
    }

    if (isCoverflowActive && options.manual) {
      userManuallyToggledDuringCoverflow = true;
    }

    if (!options.isCoverflowAuto) {
      try {
        localStorage.setItem(SIDEBAR_COLLAPSED_KEY, String(next));
      } catch (_) {}
    }

    // 【QIN-594 AC3】侧栏折叠/展开通知内嵌页重排网格，折叠过渡动画结束后强制重测一次
    const notifyGridRelayout = () => {
      try {
        const frameWin = frame?.contentWindow;
        if (frameWin) {
          if (typeof frameWin.GWAssetVirtualGridHost?.relayout === 'function') {
            frameWin.GWAssetVirtualGridHost?.relayout();
          }
          if (typeof frameWin.dispatchEvent === 'function') {
            frameWin.dispatchEvent(new CustomEvent('gw:asset-sidebar-collapsed', { detail: { collapsed: next } }));
          }
        }
      } catch (_) {}
    };
    notifyGridRelayout();
    if (typeof setTimeout === 'function') {
      setTimeout(notifyGridRelayout, 200);
    }
  }

  function toggleSidebarCollapse() {
    setSidebarCollapsed(!sidebarCollapsed, { manual: true });
  }

  function initSidebarCollapseState() {
    let saved = false;
    try {
      saved = localStorage.getItem(SIDEBAR_COLLAPSED_KEY) === 'true';
    } catch (_) {}
    setSidebarCollapsed(saved, { isInitial: true });
  }

  function handleCoverflowOpen(detail) {
    const mode = String(detail?.mode || 'fullscreen').toLowerCase();
    if (mode !== 'fullscreen') return;
    previousStateBeforeCoverflow = sidebarCollapsed;
    userManuallyToggledDuringCoverflow = false;
    isCoverflowActive = true;
    if (!sidebarCollapsed) {
      setSidebarCollapsed(true, { isCoverflowAuto: true });
    }
  }

  function handleCoverflowClose(detail) {
    const mode = String(detail?.mode || 'fullscreen').toLowerCase();
    if (mode !== 'fullscreen') return;
    isCoverflowActive = false;
    if (userManuallyToggledDuringCoverflow) {
      return;
    }
    setSidebarCollapsed(previousStateBeforeCoverflow, { isCoverflowAuto: true });
  }

  // 【QIN-566 / QIN-590】内嵌页每次树状态与 Coverflow 变化调用宿主回调，外壳侧栏据此渲染与联动。
  window.GWAssetTreeHost = Object.freeze({
    onTreeSnapshot: applyTreeSnapshot,
    onCoverflowOpen: handleCoverflowOpen,
    onCoverflowClose: handleCoverflowClose,
  });

  // 首屏与壳层部分路由返回时都走同一个幂等绑定入口；不重复执行顶层脚本。
  rebind();
  return {
    uploadAsset,
    openAssetSettings,
    openCreateAssetLibrary,
    activateAssetBoard: activateTreeEntry,
    toggleSmartFilter,
    rebind,
    showIndexJobStatus,
    indexJobStatusView: window.GWAssetJobStatus.view,
    toggleSidebarCollapse,
    setSidebarCollapsed,
    isSidebarCollapsed,
  };
})();
