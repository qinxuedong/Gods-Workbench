// 项目页「查看 vNNN」：为项目产物列表每项追加一个纯文字链接。
// 数据源：GET /api/agent/projects/{id}/artifacts?limit=200（只读）。
// 链接目标为后端给出的 export_uri（精确 review_id + artifact_version_id）；
// 无审核记录时后端不给 export_uri，此时只显示版本号文字，不伪造链接。
window.GWProjectArtifactLinks = (function () {
  'use strict';

  const LINK_CLASS = 'gw-type-body-sm font-mono text-[#dfc384] underline shrink-0';
  let seq = 0;

  function artifactsUrl(projectId) {
    return `/api/agent/projects/${encodeURIComponent(projectId)}/artifacts?limit=200`;
  }

  function safeUri(uri) {
    return typeof uri === 'string' && uri.startsWith('/api/agent/runs/') ? uri : null;
  }

  function renderItems(container, items) {
    if (!container) return;
    const doc = container.ownerDocument || document;
    const rows = (Array.isArray(items) ? items : []).map(item => {
      const row = doc.createElement('li');
      row.className = 'gw-type-body-sm font-mono text-slate-300 flex items-center gap-1 min-w-0';
      row.dataset.artifactVersionId = String(item.version_id || '');
      const name = doc.createElement('span');
      name.className = 'truncate min-w-0';
      name.title = String(item.artifact_id || '');
      name.textContent = `${item.artifact_id || ''} `;
      row.appendChild(name);
      const label = String(item.version_label || '');
      const uri = safeUri(item.export_uri);
      if (uri) {
        const link = doc.createElement('a');
        link.className = LINK_CLASS;
        link.href = uri;
        link.target = '_blank';
        link.rel = 'noopener';
        link.textContent = `查看 ${label}`;
        link.title = `content_hash ${item.content_hash || ''}`;
        link.dataset.contentHash = String(item.content_hash || '');
        row.appendChild(link);
      } else {
        const text = doc.createElement('span');
        text.className = 'text-slate-500 shrink-0';
        text.textContent = label;
        text.title = '未审核版本，暂无可导出链接';
        row.appendChild(text);
      }
      return row;
    });
    container.replaceChildren(...rows);
    container.hidden = rows.length === 0;
  }

  async function load(projectId, container, fetchImpl) {
    const target = container || document.getElementById('projectArtifactList');
    if (!target) return [];
    const ticket = ++seq;
    if (!projectId) { renderItems(target, []); return []; }
    try {
      const res = await (fetchImpl || fetch)(artifactsUrl(projectId), { credentials: 'same-origin' });
      if (ticket !== seq) return [];
      const items = res.ok ? ((await res.json())?.items || []) : [];
      if (ticket !== seq) return [];
      renderItems(target, items);
      return items;
    } catch (_) {
      if (ticket === seq) renderItems(target, []);
      return [];
    }
  }

  return { artifactsUrl, renderItems, load };
})();
