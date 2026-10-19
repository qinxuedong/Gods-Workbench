/**
 * Copyright 2026 Gods-Workbench Authors
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 */

(function (global) {
  'use strict';

  // Reuse the frozen media-preview entry point; metadata mode returns the
  // structured proxy descriptor without adding a new static API reference.
  const API = '/api/media-preview';
  const READY = 'ready';
  const DEGRADED = new Set(['unavailable', 'not_integrated', 'pending']);

  function text(value, fallback) {
    const result = String(value == null ? '' : value).trim();
    return result || fallback;
  }

  function requestUrl(element) {
    const query = new URLSearchParams();
    const fields = [
      ['metadata', '1'],
      ['asset_id', element.dataset.assetId],
      ['version_id', element.dataset.versionId],
      ['profile', element.dataset.profile || 'preview'],
      ['width', element.dataset.width]
    ];
    fields.forEach(([key, value]) => {
      if (value != null && String(value).trim()) query.set(key, String(value).trim());
    });
    return query.toString() ? `${API}?${query.toString()}` : API;
  }

  function clear(element) {
    while (element.firstChild) element.removeChild(element.firstChild);
  }

  function fallback(element, state, message) {
    clear(element);
    const status = text(state, 'unavailable');
    element.dataset.gwDegradation = status;
    element.dataset.previewAvailability = status;
    const box = element.ownerDocument.createElement('div');
    box.className = 'media-preview-fallback';
    box.setAttribute('role', 'status');
    box.dataset.gwDegradation = status;
    const title = element.ownerDocument.createElement('strong');
    title.textContent = status === 'not_integrated' ? '媒体代理未接入' : '媒体预览暂不可用';
    const detail = element.ownerDocument.createElement('span');
    detail.textContent = text(message, '请检查源文件或稍后重试');
    box.append(title, detail);
    element.appendChild(box);
    return box;
  }

  function mediaNode(element, descriptor) {
    const document = element.ownerDocument;
    const kind = text(element.dataset.mediaKind, '').toLowerCase();
    const type = text(descriptor.media_type, '').toLowerCase();
    const tag = kind === 'video' || type.indexOf('video/') === 0
      ? 'video'
      : kind === 'audio' || type.indexOf('audio/') === 0 ? 'audio' : 'img';
    const node = document.createElement(tag);
    node.src = descriptor.proxy_url;
    node.dataset.proxyArtifactId = text(descriptor.artifact_id, '');
    node.dataset.previewAvailability = READY;
    node.setAttribute('data-preview-source', 'proxy');
    if (tag === 'img') {
      node.alt = text(element.dataset.alt, '媒体预览');
      node.loading = 'eager';
      node.decoding = 'async';
    } else {
      node.controls = true;
      node.preload = 'metadata';
      node.playsInline = true;
    }
    node.addEventListener('error', () => fallback(element, 'unavailable', '代理产物读取失败；请重新生成或检查源文件'));
    return node;
  }

  async function load(element, options) {
    const fetchImpl = options.fetchImpl || global.fetch;
    if (typeof fetchImpl !== 'function') return fallback(element, 'not_integrated', '浏览器未提供媒体请求能力');
    if (element._mediaPreviewController) element._mediaPreviewController.abort();
    const controller = typeof global.AbortController === 'function' ? new global.AbortController() : null;
    element._mediaPreviewController = controller;
    element.dataset.previewState = 'loading';
    try {
      const response = await fetchImpl(requestUrl(element), {
        credentials: 'same-origin',
        signal: controller ? controller.signal : undefined
      });
      const body = await response.json();
      if (!response.ok && !body?.availability) {
        const error = body?.detail || {};
        return fallback(element, response.status === 503 ? 'unavailable' : 'not_integrated', text(error.message, '媒体代理请求失败'));
      }
      if (body.availability !== READY || !body.proxy_url) {
        return fallback(element, DEGRADED.has(body.availability) ? body.availability : 'unavailable', text(body.next_action, '请检查媒体来源'));
      }
      clear(element);
      element.removeAttribute('data-gw-degradation');
      element.dataset.previewAvailability = READY;
      element.dataset.previewState = READY;
      element.appendChild(mediaNode(element, body));
      return body;
    } catch (error) {
      if (error && error.name === 'AbortError') return null;
      return fallback(element, 'unavailable', '媒体代理请求失败，请稍后重试');
    } finally {
      if (element._mediaPreviewController === controller) element._mediaPreviewController = null;
    }
  }

  function mount(element, options) {
    if (!element || !element.dataset) return Promise.resolve(null);
    return load(element, options || {});
  }

  function mountAll(root, options) {
    const scope = root || (global.document && global.document);
    if (!scope || typeof scope.querySelectorAll !== 'function') return Promise.resolve([]);
    const nodes = Array.from(scope.querySelectorAll('[data-media-preview]'));
    return Promise.all(nodes.map((node) => mount(node, options)));
  }

  function destroy(element) {
    if (!element) return;
    if (element._mediaPreviewController) element._mediaPreviewController.abort();
    element._mediaPreviewController = null;
    clear(element);
    delete element.dataset.previewState;
    delete element.dataset.previewAvailability;
    delete element.dataset.gwDegradation;
  }

  global.MediaPreviewPlayer = Object.freeze({ mount, mountAll, fallback, destroy, requestUrl });
  if (global.document) {
    const boot = () => { void mountAll(global.document); };
    if (global.document.readyState === 'loading') global.document.addEventListener('DOMContentLoaded', boot, { once: true });
    else boot();
  }
})(typeof window !== 'undefined' ? window : globalThis);
