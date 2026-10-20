/**
 * Copyright 2026 Gods-Workbench Authors
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 */

(function (global) {
  'use strict';

  function text(value, fallback = '') {
    const result = String(value == null ? '' : value).trim();
    return result || fallback;
  }

  function esc(value) {
    return text(value).replace(/[&<>"']/g, character => ({
      '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
    }[character]));
  }

  function numberFromVersion(value) {
    if (typeof value === 'number' && Number.isFinite(value) && value > 0) return Math.trunc(value);
    const match = /(?:^|[-_])v(\d{1,3})(?:$|[-_])/i.exec(text(value));
    if (match) return Number(match[1]);
    const numeric = Number.parseInt(text(value), 10);
    return Number.isFinite(numeric) && numeric > 0 ? numeric : null;
  }

  function sourceOf(raw) {
    const source = raw && typeof raw.source === 'object' ? raw.source : {};
    const metadata = raw && typeof raw.metadata === 'object' ? raw.metadata : {};
    return text(
      raw?.media_url || raw?.url || raw?.image_url || raw?.imageUrl || raw?.img
      || source.source_url || source.url || metadata.media_url || metadata.source_url
    );
  }

  function mediaTypeOf(raw, source) {
    const declared = text(raw?.media_type || raw?.previewType || raw?.mime_type || raw?.content_type).toLowerCase();
    if (declared) return declared;
    return /\.(?:mp4|webm|mov|m4v)(?:$|[?#])/i.test(source) ? 'video' : 'image';
  }

  function normalizeTake(raw, index = 0) {
    if (!raw || typeof raw !== 'object') return null;
    const asset = raw.asset && typeof raw.asset === 'object' ? raw.asset : raw;
    const assetId = text(raw.asset_id || raw.assetId || asset.asset_id || asset.id);
    const versionId = text(raw.version_id || raw.versionId || raw.asset_version_id || asset.version_id);
    if (!assetId && !versionId) return null;
    const version = numberFromVersion(raw.version ?? raw.asset_version ?? raw.revision)
      || numberFromVersion(versionId)
      || index + 1;
    const takeId = text(raw.take_id || raw.takeId || raw.variant_id || raw.id || versionId || assetId || `take-${version}`);
    const source = sourceOf(raw);
    return {
      ...raw,
      asset_id: assetId,
      version_id: versionId,
      version,
      take_id: takeId,
      versionLabel: `v${String(version).padStart(3, '0')}`,
      source,
      media_url: source,
      media_type: mediaTypeOf(raw, source),
      primary: Boolean(raw.primary || raw.is_primary || raw.isPrimary)
    };
  }

  function collectTakes(shot) {
    if (!shot || typeof shot !== 'object') return [];
    const candidates = [];
    const declared = shot.takes || shot.take_versions || shot.versions || shot.variants || shot.outputs;
    if (Array.isArray(declared)) candidates.push(...declared);
    if (shot.asset_id || shot.version_id) candidates.push(shot);
    const seen = new Set();
    const takes = candidates.map((item, index) => normalizeTake(item, index)).filter(Boolean).filter(item => {
      const key = item.asset_id && item.version_id
        ? `${item.asset_id}|${item.version_id}` : item.take_id;
      if (seen.has(key)) return false;
      seen.add(key);
      return true;
    });
    if (!takes.length) return [];

    const selectedId = text(shot.primary_take_id || shot.selected_take_id || shot.main_take_id || shot.take_id);
    const selectedAsset = text(shot.primary_asset_id || shot.selected_asset_id || shot.asset_id);
    const selectedVersion = text(shot.primary_version_id || shot.selected_version_id || shot.version_id);
    let hasPrimary = false;
    takes.forEach(item => {
      const selected = (selectedId && item.take_id === selectedId)
        || (selectedAsset && item.asset_id === selectedAsset && (!selectedVersion || item.version_id === selectedVersion))
        || (selectedVersion && item.version_id === selectedVersion);
      if (selected) {
        item.primary = true;
        hasPrimary = true;
      }
    });
    if (!hasPrimary && !takes.some(item => item.primary)) takes[0].primary = true;
    return takes.sort((left, right) => left.version - right.version || left.take_id.localeCompare(right.take_id));
  }

  function previewMarkup(take, shotId) {
    const common = `data-take-preview data-take-hover="true" data-take-id="${esc(take.take_id)}" data-version-id="${esc(take.version_id)}"`;
        if (take.media_type === 'video' || take.media_type.startsWith('video/')) {
      return `<video ${common} src="${esc(take.source)}" muted playsinline preload="metadata" aria-label="${esc(take.versionLabel)}"></video>`;
    }
    return `<img ${common} src="${esc(take.source)}" alt="${esc(`${shotId} ${take.versionLabel}`)}" loading="lazy">`;
  }

  function render(takes, shotId) {
    const list = Array.isArray(takes) ? takes : [];
    if (!list.length) return '';
    const safeShotId = text(shotId);
    return `<section class="gw-contact-sheet mt-2" data-contact-sheet data-shot-id="${esc(safeShotId)}" aria-label="${esc(`${safeShotId} 多 Take 接触印样`)}">
      <div class="flex items-center justify-between gap-2 mb-1">
        <span class="gw-type-label-md text-[#dfc384]">多 Take 接触印样</span>
        <span class="gw-type-label-md text-slate-500" data-take-count>${list.length} 个版本</span>
      </div>
      <div class="grid grid-cols-3 gap-1.5" data-take-strip>
        ${list.map(take => `<article class="rounded-lg border ${take.primary ? 'border-[#dfc384]' : 'border-white/10'} bg-black/20 overflow-hidden" data-take-id="${esc(take.take_id)}" data-take-version="${esc(take.versionLabel)}" data-take-primary="${take.primary ? 'true' : 'false'}">
          <div class="aspect-video bg-black/40 overflow-hidden">${previewMarkup(take, safeShotId)}</div>
          <div class="flex items-center justify-between gap-1 px-1.5 py-1">
            <span class="gw-type-label-md text-slate-200" data-take-version-label>${esc(take.versionLabel)}</span>
            ${take.primary ? '<span class="gw-type-label-md text-[#dfc384]" data-take-primary-label>已审核主力</span>' : ''}
          </div>
          <button type="button" class="hw-btn hw-btn-sm w-full rounded-none" data-shot-action="promote-take" data-shot-id="${esc(safeShotId)}" data-take-id="${esc(take.take_id)}" ${take.primary ? 'disabled aria-current="true"' : ''}>${take.primary ? '已审核主力 Take' : '设为主力 Take'}</button>
        </article>`).join('')}
      </div>
    </section>`;
  }

  function previews(root) {
    return root && typeof root.querySelectorAll === 'function'
      ? Array.from(root.querySelectorAll('[data-take-preview]')) : [];
  }

  function pauseAll(root) {
    previews(root).forEach(node => {
      try { node.pause?.(); } catch (_) {}
    });
  }

  function bind(root, { onPromote } = {}) {
    if (!root || typeof root.addEventListener !== 'function' || root.__gwContactSheetBound) return;
    root.__gwContactSheetBound = true;
    root.addEventListener('pointerover', event => {
      const target = event.target?.closest?.('[data-take-hover]');
      if (!target || (typeof root.contains === 'function' && !root.contains(target))) return;
      const position = Number(target.currentTime) || 0;
      previews(root).forEach(node => {
        try {
          node.currentTime = position;
          node.play?.();
        } catch (_) {}
      });
    });
    root.addEventListener('pointerout', event => {
      const target = event.target?.closest?.('[data-take-hover]');
      const related = event.relatedTarget;
      if (target && related && target.contains?.(related)) return;
      if (target) pauseAll(root);
    });
    root.addEventListener('click', event => {
      const button = event.target?.closest?.('[data-shot-action="promote-take"]');
      if (!button || typeof onPromote !== 'function') return;
      onPromote(text(button.dataset?.shotId), text(button.dataset?.takeId));
    });
  }

  global.WorkbenchContactSheet = Object.freeze({ collectTakes, normalizeTake, render, bind });
})(typeof window !== 'undefined' ? window : globalThis);
