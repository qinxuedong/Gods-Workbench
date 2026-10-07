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

/* Optional, shell-configured graph prefetch. No routes or credentials live here. */
(() => {
  'use strict';
  const TTL = 30000;
  const cache = new Map();
  const pending = new Map();
  // Aborted transports still count until settled, including across configure().
  const running = new Set();
  let config = null;
  let generation = 0;
  let tree = null;
  let hovered = null;
  let hoverTimer = null;
  let idleHandle = null;
  const object = value => value !== null && typeof value === 'object' && !Array.isArray(value);
  const text = (value, limit) => typeof value === 'string' && value.length <= limit && !/[\u0000-\u001f\u007f]/.test(value);
  const id = value => text(value, 200) && value.length > 0;
  const types = new Set(['project', 'entity', 'gate', 'canvas', 'asset', 'job', 'task', 'pipeline', 'workspace']);
  const contextKeys = ['project_id', 'entity_id', 'canvas_id', 'job_id', 'asset_id'];

  function project(payload) {
    if (!object(payload)) throw new Error('非法图响应');
    const data = object(payload.data) ? payload.data : payload;
    const features = data.features ?? payload.features;
    if (!Array.isArray(data.nodes) || data.nodes.length > 500 || !Array.isArray(data.edges)
      || data.edges.length > 1000 || !object(features)
      || typeof features.toolbar_enabled !== 'boolean' || typeof features.tree_enabled !== 'boolean'
      || !features.tree_enabled
      || (object(payload.features) && payload.features.tree_enabled === false)) throw new Error('图响应越界或已禁用');
    const seen = new Set();
    const nodes = data.nodes.map(raw => {
      if (!object(raw) || !id(raw.id) || !types.has(raw.type) || seen.has(raw.id)) throw new Error('非法节点');
      seen.add(raw.id);
      const node = {id: raw.id, type: raw.type};
      for (const [key, limit] of [['stable_id', 200], ['label', 120], ['name', 120], ['status', 36]]) {
        if (raw[key] !== undefined) {
          if (!text(raw[key], limit)) throw new Error('非法节点字段');
          node[key] = raw[key];
        }
      }
      if (raw.has_children !== undefined) {
        if (typeof raw.has_children !== 'boolean') throw new Error('非法子节点标志');
        node.has_children = raw.has_children;
      }
      if (raw.context !== undefined) {
        if (!object(raw.context)) throw new Error('非法上下文');
        node.context = {};
        contextKeys.forEach(key => {
          if (raw.context[key] !== undefined) {
            if (!text(raw.context[key], 200)) throw new Error('非法上下文标识');
            node.context[key] = raw.context[key];
          }
        });
      }
      return node;
    });
    const edges = data.edges.map(raw => {
      if (!object(raw) || !id(raw.source) || !id(raw.target) || raw.source === raw.target
        || (raw.relation !== undefined && raw.relation !== 'contains')
        || (raw.type !== undefined && raw.type !== 'contains')) throw new Error('非法关系');
      const edge = {source: raw.source, target: raw.target, relation: 'contains'};
      if (raw.id !== undefined) {
        if (!id(raw.id)) throw new Error('非法关系标识');
        edge.id = raw.id;
      }
      return edge;
    });
    const result = {nodes, edges, features: {toolbar_enabled: features.toolbar_enabled, tree_enabled: features.tree_enabled}};
    // Keep bounded pagination for the shell, never arbitrary response metadata.
    const cursor = data.next_cursor ?? data.cursor?.next ?? payload.next_cursor ?? payload.cursor?.next;
    if (cursor !== undefined && cursor !== null) {
      if (!text(cursor, 512)) throw new Error('非法游标');
      result.next_cursor = cursor;
    }
    const truncated = data.truncated ?? payload.truncated;
    if (truncated !== undefined) {
      if (typeof truncated !== 'boolean') throw new Error('非法分页标志');
      result.truncated = truncated;
    }
    return result;
  }

  function validURL(url) {
    if (!text(url, 8192) || !url) return false;
    try {
      const parsed = new URL(url, window.location.origin);
      return /^https?:$/.test(parsed.protocol) && parsed.origin === window.location.origin
        && !parsed.username && !parsed.password && !parsed.hash;
    } catch (_) { return false; }
  }

  function getCached(url) {
    if (!config) return null;
    const entry = cache.get(url);
    if (!entry) return null;
    if (Date.now() - entry.time >= TTL || entry.scope !== config.scopeKey) {
      cache.delete(url);
      return null;
    }
    return JSON.parse(entry.json);
  }

  function clear() {
    generation++;
    clearTimeout(hoverTimer);
    hovered = null;
    if (idleHandle !== null) window.cancelIdleCallback?.(idleHandle);
    idleHandle = null;
    cache.clear();
    pending.forEach(job => { job.controller.abort(); job.resolve(null); });
    pending.clear();
  }

  function destroy() {
    config = null;
    clear();
    tree?.removeEventListener('mouseover', over);
    tree?.removeEventListener('mouseout', out);
    tree = null;
  }

  function pump() {
    if (!config) return;
    for (const job of pending.values()) {
      if (running.size >= 2) break;
      if (job.started) continue;
      job.started = true;
      running.add(job);
      const activeConfig = config;
      (async () => {
        try {
          const response = await activeConfig.fetchRequest(job.url, job.controller.signal);
          if (job.generation !== generation) return;
          if (!response || response.ok !== true || typeof response.json !== 'function') throw new Error('预取请求失败');
          const payload = await response.json();
          if (job.generation !== generation) return;
          const data = project(payload);
          cache.set(job.url, {scope: activeConfig.scopeKey, time: Date.now(), json: JSON.stringify(data)});
          while (cache.size > 20) cache.delete(cache.keys().next().value);
          job.resolve(getCached(job.url));
        } catch (_) {
          if (job.generation === generation) destroy();
        } finally {
          job.resolve(null);
          running.delete(job);
          if (pending.get(job.url) === job) pending.delete(job.url);
          pump();
        }
      })();
    }
  }

  function prefetchNode(nodeId) {
    if (!config || !id(nodeId)) return Promise.resolve(null);
    let url;
    try { url = config.resolveRequest(nodeId); } catch (_) { destroy(); return Promise.resolve(null); }
    if (url === null || url === undefined || url === '') return Promise.resolve(null);
    if (!validURL(url)) { destroy(); return Promise.resolve(null); }
    const cached = getCached(url);
    if (cached) return Promise.resolve(cached);
    if (pending.has(url)) return pending.get(url).promise;
    if (pending.size >= 20) return Promise.resolve(null);
    const job = {url, generation, controller: new AbortController(), started: false};
    job.promise = new Promise(resolve => { job.resolve = resolve; });
    pending.set(url, job);
    pump();
    return job.promise;
  }

  function nodeFor(target) {
    const node = target?.closest?.('[data-context-tree-node]');
    return node && tree?.contains(node) ? node : null;
  }

  function over(event) {
    const node = nodeFor(event.target);
    if (!node || node === nodeFor(event.relatedTarget) || node === hovered) return;
    clearTimeout(hoverTimer);
    hovered = node;
    hoverTimer = setTimeout(() => {
      if (hovered === node && tree?.contains(node)) prefetchNode(node.dataset.contextTreeNode);
    }, 300);
  }

  function out(event) {
    if (nodeFor(event.target) === nodeFor(event.relatedTarget)) return;
    clearTimeout(hoverTimer);
    hovered = null;
  }

  window.ContextPrefetch = {
    configure(options = {}) {
      destroy();
      if (!options || typeof options.resolveRequest !== 'function' || typeof options.fetchRequest !== 'function'
        || !text(options.scopeKey, 2048) || !options.scopeKey) return false;
      config = {resolveRequest: options.resolveRequest, fetchRequest: options.fetchRequest, scopeKey: options.scopeKey};
      tree = document.getElementById('workspaceContextTree');
      tree?.addEventListener('mouseover', over);
      tree?.addEventListener('mouseout', out);
      if (tree && typeof window.requestIdleCallback === 'function') {
        const token = generation;
        idleHandle = window.requestIdleCallback(deadline => {
          idleHandle = null;
          if (token !== generation || !config) return;
          let count = 0;
          for (const node of tree.querySelectorAll('[data-context-tree-node]')) {
            if (count >= 5 || deadline.timeRemaining() <= 0) break;
            prefetchNode(node.dataset.contextTreeNode);
            count++;
            if (!config) break;
          }
        });
      }
      return true;
    },
    getCached, prefetchNode, clear, destroy,
  };
})();
