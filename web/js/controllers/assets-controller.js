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

/** The Workbench shell delegates all asset operations to the canonical asset manager. */
window.WorkbenchAssets = (() => {
  'use strict';
  let frame = null;
  let status = null;
  let categories = null;
  let boundFrame = null;
  let frameLoadHandler = null;
  let observer;
  let revision = 0;
  let categorySnapshot = '';

  function manager() {
    try { return frame.contentDocument; } catch (_) { return null; }
  }

  // 父页状态条必须明说「未接入」并带结构化标记（用户 2026-09-21 裁决第 3 项）。
  function setVaultStatus(text) {
    const value = text || '';
    const kind = /未纳入当前切片/.test(value) ? 'not_integrated'
      : (/暂时不可用/.test(value) ? 'service_unavailable' : '');
    if (!status) return;
    status.textContent = value;
    if (kind) status.setAttribute('data-gw-degradation', kind);
    else status.removeAttribute('data-gw-degradation');
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

  async function selectCategory(id) {
    const current = ++revision;
    try {
      const tab = await waitForControl('[data-tab="registry"]');
      if (tab.getAttribute('aria-selected') !== 'true') tab.click();
      const button = await waitForControl(`[data-registry-stat="${CSS.escape(id)}"]`);
      if (current !== revision) return;
      if (button.getAttribute('aria-pressed') !== 'true') button.click();
      setVaultStatus('');
    } catch (error) { setVaultStatus(error.message); }
  }

  function openRequestedAsset() {
    const assetId = new URLSearchParams(location.search).get('asset_id');
    if (!assetId || !frame?.contentWindow) return;
    frame.contentWindow.postMessage({ type: 'asset-global-search', asset_id: assetId }, location.origin);
  }

  function syncCategories() {
    if (manager()?.querySelector('[data-tab="registry"][aria-selected="true"]')) setVaultStatus('');
    const controls = [...(manager()?.querySelectorAll('[data-registry-stat]') || [])];
    if (!categories) return;
    if (!controls.length) {
      categorySnapshot = '';
      categories.querySelectorAll('[aria-pressed="true"]').forEach(button => {
        button.setAttribute('aria-pressed', 'false');
        button.classList.remove('bg-[#12151e]', 'border', 'border-[#dfc384]/30', 'text-[#eddab3]', 'font-semibold');
        button.classList.add('text-slate-400', 'hover:text-white', 'hover:bg-white/5');
      });
      return;
    }
    const rows = controls.map(button => ({
      id: button.dataset.registryStat,
      name: button.querySelector('small')?.textContent || '',
      count: button.querySelector('strong')?.textContent || '',
      active: button.getAttribute('aria-pressed') === 'true',
    }));
    const snapshot = JSON.stringify(rows);
    if (categorySnapshot === snapshot) return;
    categorySnapshot = snapshot;
    const fragment = document.createDocumentFragment();
    for (const row of rows) {
      const button = document.createElement('button');
      button.type = 'button';
      button.className = `w-full text-left px-2.5 py-1.5 rounded-lg transition flex items-center justify-between ${row.active ? 'bg-[#12151e] border border-[#dfc384]/30 text-[#eddab3] font-semibold' : 'text-slate-400 hover:text-white hover:bg-white/5'}`;
      button.setAttribute('aria-pressed', String(row.active));
      button.dataset.assetCategory = row.id;
      const label = document.createElement('span');
      label.textContent = row.name;
      const count = document.createElement('span');
      count.className = 'gw-type-body-sm font-mono px-1 rounded bg-black/60 text-[#dfc384]';
      count.textContent = row.count;
      button.append(label, count);
      button.addEventListener('click', () => selectCategory(row.id));
      fragment.append(button);
    }
    const focused = categories.contains(document.activeElement) ? document.activeElement.dataset.assetCategory : null;
    categories.replaceChildren(fragment);
    if (focused !== null) categories.querySelector(`[data-asset-category="${CSS.escape(focused)}"]`)?.focus();
    const total = document.getElementById('assetCategoryTotal');
    if (total) total.textContent = rows[0].count;
  }

  async function uploadAsset() {
    try {
      const available = manager()?.querySelector('[data-localup-upload]');
      if (available) { available.click(); return; }
      const tab = await waitForControl('[data-tab="local"]');
      if (tab.getAttribute('aria-selected') !== 'true') tab.click();
      // Async navigation cannot retain a file picker's user activation.
      const upload = await waitForControl('[data-localup-upload]');
      setVaultStatus('上传资源已打开');
      upload.focus();
    } catch (error) { setVaultStatus(error.message); }
  }

  function initFrame() {
    observer?.disconnect();
    categorySnapshot = '';
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
    observer = new MutationObserver(syncCategories);
    observer.observe(doc.getElementById('assetManagerRoot') || doc.body, {childList: true, subtree: true});
    syncCategories();
    waitForControl('[data-tab="registry"]').then(() => { setVaultStatus(''); mirrorFrameDegradation(); openRequestedAsset(); }).catch(error => { setVaultStatus(error.message); });
  }
  function bindElements() {
    frame = document.getElementById('assetVaultFrame');
    status = document.getElementById('assetVaultStatus');
    categories = document.getElementById('assetCategories');
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
    for (const key of ['project_id', 'pipeline_id', 'asset_id']) {
      if (contextParams.has(key)) source.searchParams.set(key, contextParams.get(key));
    }
    source.hash = location.hash;
    if (frame.dataset.gwBoundSrc !== source.href) {
      frame.dataset.gwBoundSrc = source.href;
      frame.src = source.href;
    }
  }

  function rebind() {
    if (!bindElements()) return;
    categorySnapshot = '';
    mirrorFrameDegradation();
    syncFrameSource();
    if (frame.contentDocument?.readyState === 'complete') initFrame();
  }

  // 首屏与壳层部分路由返回时都走同一个幂等绑定入口；不重复执行顶层脚本。
  rebind();
  window.addEventListener('pagehide', () => observer?.disconnect());
  return {uploadAsset, rebind};
})();
