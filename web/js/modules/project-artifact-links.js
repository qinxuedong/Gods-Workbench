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

  // 文字分镜导出只允许使用后端给出的精确 Agent 导出地址。
  function latestApprovedStoryboard(items) {
    return (Array.isArray(items) ? items : [])
      .filter(item => Array.isArray(item?.export_formats)
        && item.export_formats.includes('csv')
        && item.review_id
        && safeUri(item.export_uri))
      .sort((a, b) => String(b.created_at || '').localeCompare(String(a.created_at || ''))
        || revisionOf(b) - revisionOf(a))[0] || null;
  }

  // 仅支持 RFC 4180 兼容的逗号分隔文本；不把任意响应内容当作分镜。
  function parseStoryboardCsv(text) {
    const input = String(text ?? '').replace(/^\uFEFF/, '');
    const records = [];
    let row = [];
    let cell = '';
    let quoted = false;
    for (let index = 0; index < input.length; index += 1) {
      const char = input[index];
      if (quoted) {
        if (char === '"' && input[index + 1] === '"') {
          cell += '"';
          index += 1;
        } else if (char === '"') {
          quoted = false;
        } else {
          cell += char;
        }
      } else if (char === '"' && cell === '') {
        quoted = true;
      } else if (char === ',') {
        row.push(cell);
        cell = '';
      } else if (char === '\n' || char === '\r') {
        if (char === '\r' && input[index + 1] === '\n') index += 1;
        row.push(cell);
        if (row.some(value => String(value).trim())) records.push(row);
        row = [];
        cell = '';
      } else {
        cell += char;
      }
    }
    if (cell !== '' || row.length) {
      row.push(cell);
      if (row.some(value => String(value).trim())) records.push(row);
    }
    if (records.length < 2) return [];
    const headers = records.shift().map(value => String(value).trim());
    return records.map(values => headers.reduce((result, header, index) => {
      if (header) result[header] = String(values[index] ?? '').trim();
      return result;
    }, {}));
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

  function agentStatus(items) {
    if (!Array.isArray(items) || !items.length) return {label: '未接入', kind: 'not_integrated'};
    const latest = items.slice().sort((a, b) => String(b.created_at || '').localeCompare(String(a.created_at || '')))[0];
    if (latest?.review_id && safeUri(latest.export_uri)) return {label: '已审核', kind: 'approved'};
    if (latest?.lifecycle === 'trashed') return {label: '已回收', kind: 'trashed'};
    return {label: '待审核', kind: 'pending'};
  }

  // 项目卡片 footer 由 projects-controller 动态生成；在已有版本槽位旁挂载真实 AI 编剧入口。
  function renderAgentProjectEntry(slot, items) {
    const projectId = String(slot?.dataset?.projectId || '').trim();
    const parent = slot?.parentElement;
    if (!projectId || !parent || typeof parent.appendChild !== 'function') return null;
    const doc = slot.ownerDocument || document;
    let link = parent.querySelector?.('[data-gw-agent-project-link]');
    if (!link) {
      link = doc.createElement('a');
      link.setAttribute('data-gw-agent-project-link', projectId);
      link.className = 'gw-type-body-sm font-mono text-cyan-300 underline whitespace-nowrap cursor-pointer shrink-0';
      link.textContent = 'AI 编剧';
      link.title = '打开当前项目的 AI 编剧工作台';
      link.addEventListener?.('click', event => event.stopPropagation());
      if (typeof parent.insertBefore === 'function') parent.insertBefore(link, slot);
      else parent.appendChild(link);
    }
    link.href = `/static/pages/agents.html?project_id=${encodeURIComponent(projectId)}`;
    let badge = parent.querySelector?.('[data-gw-agent-status]');
    if (!badge) {
      badge = doc.createElement('span');
      badge.setAttribute('data-gw-agent-status', projectId);
      badge.className = 'gw-type-body-sm font-mono px-1 py-0.5 rounded bg-black/50 border border-white/10 whitespace-nowrap shrink-0';
      if (typeof parent.insertBefore === 'function') parent.insertBefore(badge, slot);
      else parent.appendChild(badge);
    }
    const status = agentStatus(items);
    badge.textContent = status.label;
    badge.dataset.gwDegradation = status.kind === 'not_integrated' ? 'not_integrated' : '';
    badge.title = `AI 编剧状态：${status.label}`;
    return {link, badge, status};
  }

  async function loadLatestApprovedStoryboard(projectId, fetchImpl) {
    const id = String(projectId || '').trim();
    if (!id) return null;
    const items = await fetchProject(id, fetchImpl);
    const artifact = latestApprovedStoryboard(items);
    if (!artifact) return null;
    const uri = safeUri(artifact.export_uri);
    const response = await (fetchImpl || fetch)(uri, {credentials: 'same-origin', headers: {Accept: 'text/csv'}});
    if (!response.ok) {
      const error = new Error(`文字分镜导出读取失败（HTTP ${response.status}）`);
      error.status = response.status;
      throw error;
    }
    const csv = await response.text();
    return {artifact, rows: parseStoryboardCsv(csv)};
  }

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
    renderAgentProjectEntry(slot, items);
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

  function closePopover(doc, options) {
    const d = doc || document;
    const pop = d.getElementById?.(POPOVER_ID);
    if (!pop) return;
    const trigger = pop._gwTrigger;
    pop.remove();
    // Esc 关闭时把焦点还给「查看 vNNN」触发按钮，键盘用户不丢失位置。
    if (options?.restoreFocus && trigger?.isConnected !== false && typeof trigger?.focus === 'function') {
      trigger.focus({preventScroll: true});
    }
  }

  function handleKeydown(event, doc) {
    if (event?.key === 'Escape') closePopover(doc || document, {restoreFocus: true});
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
    document.addEventListener('keydown', (event) => handleKeydown(event, document));
  }

  return {
    artifactsUrl, groupArtifacts, latestApprovedStoryboard, parseStoryboardCsv,
    renderAgentProjectEntry, loadLatestApprovedStoryboard, renderSlot, buildPopover,
    hydrate, invalidate, closePopover, handleKeydown,
  };
})();
