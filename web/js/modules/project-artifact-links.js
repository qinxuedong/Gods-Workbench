// 项目页「查看 vNNN」：每张项目卡片左下角显示该项目产物的当前版本，点击弹出版本切换浮层。
// 数据源：GET /api/agent/projects/{id}/artifacts?limit=200（只读）。
// 产物身份 = artifact_id；同一 artifact_id 的多个版本按 revision 分组，最大 revision 为当前版本。
// 已审核版本带后端给出的 export_uri（精确 review_id + artifact_version_id），可直接打开；
// 未审核版本后端不给 export_uri，只显示版本号文字，不伪造链接。
window.GWProjectArtifactLinks = (function () {
  'use strict';

  const SLOT_SELECTOR = '[data-gw-artifact-version-slot]';
  const TRIGGER_CLASS = 'gw-type-body-sm font-mono text-[#dfc384] underline whitespace-nowrap cursor-pointer shrink-0';
  const POPOVER_ID = 'gwArtifactVersionPopover';
  const cache = new Map();
  let triggerSeq = 0;

  function artifactsUrl(projectId) {
    return `/api/agent/projects/${encodeURIComponent(projectId)}/artifacts?limit=200`;
  }

  function safeUri(uri) {
    return typeof uri === 'string' && uri.startsWith('/api/agent/runs/') ? uri : null;
  }

  function revisionOf(item) {
    const n = Number(item?.revision);
    if (Number.isFinite(n)) return n;
    const m = /^v(\d+)$/.exec(String(item?.version_label || ''));
    return m ? Number(m[1]) : 0;
  }

  // 按 artifact_id 分组；组内按 revision 降序（current 在首位）；组间当前版本可导出者优先，再按 created_at 降序。
  function groupArtifacts(items) {
    const groups = new Map();
    (Array.isArray(items) ? items : []).forEach(item => {
      const key = String(item?.artifact_id || '');
      if (!key) return;
      if (!groups.has(key)) groups.set(key, []);
      groups.get(key).push(item);
    });
    return [...groups.entries()].map(([artifactId, versions]) => {
      versions.sort((a, b) => revisionOf(b) - revisionOf(a));
      return {artifactId, current: versions[0], versions};
    }).sort((a, b) => (Number(Boolean(safeUri(b.current.export_uri))) - Number(Boolean(safeUri(a.current.export_uri))))
      || String(b.current.created_at || '').localeCompare(String(a.current.created_at || '')));
  }

  function fetchProject(projectId, fetchImpl) {
    if (!cache.has(projectId)) {
      const pending = (async () => {
        try {
          const res = await (fetchImpl || fetch)(artifactsUrl(projectId), {credentials: 'same-origin'});
          return res.ok ? ((await res.json())?.items || []) : [];
        } catch (_) {
          return [];
        }
      })();
      cache.set(projectId, pending);
      pending.then(items => { if (!items.length) cache.delete(projectId); });
    }
    return cache.get(projectId);
  }

  function invalidate() { cache.clear(); }

  function versionNode(doc, item, isCurrent) {
    const uri = safeUri(item.export_uri);
    const label = String(item.version_label || '');
    const node = doc.createElement(uri ? 'a' : 'span');
    node.className = uri
      ? 'gw-type-body-sm font-mono text-[#dfc384] underline'
      : 'gw-type-body-sm font-mono text-slate-500';
    node.textContent = isCurrent ? `${label} · 当前` : label;
    node.dataset.versionId = String(item.version_id || '');
    node.dataset.contentHash = String(item.content_hash || '');
    node.title = uri ? `content_hash ${item.content_hash || ''}` : '未审核版本，暂无可导出链接';
    if (uri) { node.href = uri; node.target = '_blank'; node.rel = 'noopener'; }
    return node;
  }

  function renderSlot(slot, items) {
    if (!slot) return;
    const doc = slot.ownerDocument || document;
    const groups = groupArtifacts(items);
    slot._gwArtifactGroups = groups;
    if (!groups.length) { slot.replaceChildren(); slot.hidden = true; return; }
    const current = groups[0].current;
    const trigger = doc.createElement('button');
    trigger.type = 'button';
    trigger.id = `gwArtifactVersionTrigger-${++triggerSeq}`;
    trigger.className = TRIGGER_CLASS;
    trigger.textContent = `查看 ${current.version_label || ''}`;
    trigger.title = `${groups[0].artifactId} · content_hash ${current.content_hash || ''}（点击切换版本）`;
    trigger.dataset.contentHash = String(current.content_hash || '');
    trigger.dataset.versionId = String(current.version_id || '');
    trigger.setAttribute('aria-haspopup', 'dialog');
    trigger.setAttribute('aria-controls', POPOVER_ID);
    trigger.onclick = (event) => { event.stopPropagation(); togglePopover(trigger, groups); };
    trigger.ondblclick = (event) => event.stopPropagation();
    slot.replaceChildren(trigger);
    slot.hidden = false;
  }

  function closePopover(doc) {
    const d = doc || document;
    d.getElementById?.(POPOVER_ID)?.remove();
  }

  function buildPopover(doc, groups) {
    const pop = doc.createElement('div');
    pop.id = POPOVER_ID;
    pop.setAttribute('role', 'dialog');
    pop.setAttribute('aria-label', '产物版本');
    // 接入全局浮层契约（floating-dismissal.js）：点外部/Esc 关闭，浮层内链接可正常打开。
    Object.assign(pop.dataset, {
      floatingSurface: 'project-artifact-versions', floatingKind: 'popover', floatingContent: 'self',
      floatingDismiss: 'outside', floatingEscape: 'close-only', floatingRemoveOnClose: 'true',
    });
    pop.className = 'champagne-card p-2 space-y-1.5 max-h-[320px] overflow-y-auto min-w-[220px]';
    pop.onclick = (event) => event.stopPropagation();
    groups.forEach(group => {
      const section = doc.createElement('div');
      section.dataset.artifactId = group.artifactId;
      const title = doc.createElement('div');
      title.className = 'gw-type-body-sm font-mono text-slate-400 truncate max-w-[260px]';
      title.textContent = group.artifactId;
      title.title = group.artifactId;
      section.appendChild(title);
      const list = doc.createElement('div');
      list.className = 'flex flex-wrap gap-x-2 gap-y-0.5 pl-1';
      group.versions.forEach((item, idx) => list.appendChild(versionNode(doc, item, idx === 0)));
      section.appendChild(list);
      pop.appendChild(section);
    });
    return pop;
  }

  function togglePopover(trigger, groups) {
    const doc = trigger.ownerDocument || document;
    const existing = doc.getElementById(POPOVER_ID);
    const sameOwner = existing && existing._gwTrigger === trigger;
    closePopover(doc);
    if (sameOwner) return null;
    const pop = buildPopover(doc, groups);
    pop._gwTrigger = trigger;
    pop.dataset.floatingTrigger = trigger.id || '';
    doc.body.appendChild(pop);
    const rect = trigger.getBoundingClientRect();
    // champagne-card 自带 position: relative，这里用内联样式确保浮层脱离卡片（卡片 overflow-hidden）。
    pop.style.position = 'fixed';
    pop.style.zIndex = '60';
    pop.style.left = `${Math.round(rect.left)}px`;
    pop.style.top = `${Math.round(rect.bottom + 4)}px`;
    return pop;
  }

  async function hydrate(root, fetchImpl) {
    const scope = root || document;
    const slots = [...(scope.querySelectorAll?.(SLOT_SELECTOR) || [])];
    await Promise.all(slots.map(async slot => {
      const projectId = slot.dataset.projectId;
      if (!projectId) { renderSlot(slot, []); return; }
      const items = await fetchProject(projectId, fetchImpl);
      if (slot.dataset.projectId === projectId) renderSlot(slot, items);
    }));
    return slots;
  }

  if (typeof document !== 'undefined' && document.addEventListener) {
    document.addEventListener('click', () => closePopover(document));
    document.addEventListener('keydown', (event) => { if (event.key === 'Escape') closePopover(document); });
  }

  return {artifactsUrl, groupArtifacts, renderSlot, buildPopover, hydrate, invalidate, closePopover};
})();
