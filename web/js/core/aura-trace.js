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

(function () {
  'use strict';

  const STORAGE_PREFIX = 'gwb_aura_trace_v2';
  const SCOPE_KEY = 'gwb_aura_scope_v1';
  const CHANNEL_NAME = 'gwb-aura-trace';
  const MAX_EVENTS = 80;
  const MAX_DETAIL_LENGTH = 4000;
  const PRIVATE_KEY_RE = /^(message|prompt|content|text|output|response|raw|body|token|secret|authorization|cookie|media|path|url)$/i;
  let channel = null;

  function oneLine(value, limit = 180) {
    const text = String(value ?? '').replace(/[\u0000-\u001f\u007f]+/g, ' ').replace(/\s+/g, ' ').trim();
    return text.length > limit ? `${text.slice(0, Math.max(1, limit - 1))}…` : text;
  }

  function normalizeScope(input) {
    const value = input && typeof input === 'object' ? input : {};
    return {
      projectId: oneLine(value.projectId ?? value.project_id ?? '', 96),
      pipelineId: oneLine(value.pipelineId ?? value.pipeline_id ?? '', 96),
    };
  }

  function activeScope() {
    let stored = {};
    try { stored = JSON.parse(localStorage.getItem(SCOPE_KEY) || '{}'); } catch (_) {}
    const projectId = stored.projectId || localStorage.getItem('workspace_project_id') || '';
    return normalizeScope({...stored, projectId});
  }

  function scopeKey(scope = activeScope()) {
    const normalized = normalizeScope(scope);
    return `${STORAGE_PREFIX}:${encodeURIComponent(normalized.projectId || 'none')}:${encodeURIComponent(normalized.pipelineId || 'all')}`;
  }

  function safeDetailValue(value, key = '', depth = 0) {
    if (PRIVATE_KEY_RE.test(String(key || ''))) return '[已脱敏]';
    if (value == null || typeof value === 'number' || typeof value === 'boolean') return value;
    if (depth > 3) return '[已截断]';
    if (typeof value === 'string') return oneLine(value, 320);
    if (Array.isArray(value)) return value.slice(0, 20).map(item => safeDetailValue(item, key, depth + 1));
    if (typeof value === 'object') {
      return Object.fromEntries(Object.entries(value).slice(0, 40).map(([childKey, childValue]) => [childKey, safeDetailValue(childValue, childKey, depth + 1)]));
    }
    return oneLine(value, 320);
  }

  function detailText(value) {
    if (value == null) return '';
    if (typeof value === 'string') return '[正文已脱敏，仅保留摘要]';
    try { return JSON.stringify(safeDetailValue(value), null, 2).slice(0, MAX_DETAIL_LENGTH); } catch (_) { return '[详情不可序列化]'; }
  }

  function timestamp() {
    const now = new Date();
    return `${String(now.getHours()).padStart(2, '0')}:${String(now.getMinutes()).padStart(2, '0')}:${String(now.getSeconds()).padStart(2, '0')}`;
  }

  function read(scope = activeScope()) {
    try {
      const value = JSON.parse(localStorage.getItem(scopeKey(scope)) || '[]');
      return Array.isArray(value) ? value.filter(item => item && item.id).slice(-MAX_EVENTS) : [];
    } catch (_) { return []; }
  }

  function write(events, scope = activeScope()) {
    try { localStorage.setItem(scopeKey(scope), JSON.stringify(events.slice(-MAX_EVENTS))); } catch (_) {}
  }

  function scopeMatches(event, scope = activeScope()) {
    const expected = normalizeScope(scope);
    const actual = normalizeScope(event);
    return actual.projectId === expected.projectId && (!expected.pipelineId || actual.pipelineId === expected.pipelineId);
  }

  function normalize(input) {
    const scope = normalizeScope(input?.scope || input);
    const detail = detailText(input?.detail ?? input?.text ?? '');
    const summary = oneLine(input?.summary || detail || input?.text || '执行完成');
    return {
      id: String(input?.id || `aura-${Date.now()}-${Math.random().toString(16).slice(2)}`),
      at: Number(input?.at) || Date.now(),
      time: String(input?.time || timestamp()),
      tag: oneLine(input?.tag || 'EXEC', 16),
      summary,
      detail: detail || summary,
      agentId: oneLine(input?.agentId || 'aura-01', 48),
      route: oneLine(input?.route || '', 120),
      operation: oneLine(input?.operation || '', 120),
      stage: oneLine(input?.stage || '', 48),
      status: oneLine(input?.status || '', 24),
      projectId: scope.projectId,
      pipelineId: scope.pipelineId,
    };
  }

  function ensureChannel() {
    if (channel || typeof BroadcastChannel === 'undefined') return channel;
    try { channel = new BroadcastChannel(CHANNEL_NAME); } catch (_) { channel = null; }
    return channel;
  }

  function publish(input) {
    const event = normalize({...input, scope: input?.scope || activeScope()});
    const events = read(event);
    events.push(event);
    write(events, event);
    try { ensureChannel()?.postMessage({type: 'gw-aura-trace', event}); } catch (_) {}
    try { window.dispatchEvent(new CustomEvent('gw-aura-trace', {detail: event})); } catch (_) {}
    return event;
  }

  function accept(message, callback) {
    if (!message || message.type !== 'gw-aura-trace' || !message.event?.id) return;
    const item = normalize(message.event);
    if (!scopeMatches(item)) return;
    const events = read(item);
    if (events.some(value => value.id === item.id)) return;
    events.push(item);
    write(events, item);
    callback?.(item);
  }

  function setScope(scope) {
    const normalized = normalizeScope(scope);
    try { localStorage.setItem(SCOPE_KEY, JSON.stringify(normalized)); } catch (_) {}
    try { window.dispatchEvent(new CustomEvent('gw-aura-scope', {detail: normalized})); } catch (_) {}
    return normalized;
  }

  function subscribe(callback) {
    const delivered = new Set();
    const deliver = event => {
      const item = normalize(event);
      if (!scopeMatches(item) || delivered.has(item.id)) return;
      delivered.add(item.id);
      if (delivered.size > MAX_EVENTS * 2) delivered.delete(delivered.values().next().value);
      callback?.(item);
    };
    const onWindowEvent = event => deliver(event.detail);
    const onStorage = event => {
      if (!String(event.key || '').startsWith(`${STORAGE_PREFIX}:`) || !event.newValue) return;
      try {
        const events = JSON.parse(event.newValue);
        if (Array.isArray(events) && events.length) deliver(events[events.length - 1]);
      } catch (_) {}
    };
    const channelHandler = event => accept(event.data, deliver);
    window.addEventListener('gw-aura-trace', onWindowEvent);
    window.addEventListener('storage', onStorage);
    ensureChannel()?.addEventListener('message', channelHandler);
    return () => {
      window.removeEventListener('gw-aura-trace', onWindowEvent);
      window.removeEventListener('storage', onStorage);
      try { channel?.removeEventListener('message', channelHandler); } catch (_) {}
    };
  }

  window.AuraTraceBus = Object.freeze({
    publish,
    history: read,
    subscribe,
    setScope,
    oneLine,
  });
})();
