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
  'use strict';

  const {api, escapeHtml:esc, dateTime, timeValue, navigate} = Workspace;
  const q = (selector, root = document) => root.querySelector(selector);
  const initial = new URLSearchParams(location.search);
  const initialHash = location.hash.slice(1);
  const ENDPOINTS = {
    overview: '/api/observability/overview',
    series: '/api/observability/series',
    events: '/api/observability/events',
    tasks: '/api/observability/tasks',
    health: '/api/observability/health',
    sources: '/api/observability/sources',
    assetVolumes: '/api/observability/asset-volumes',
  };
  const STATUS_KEYS = {
    running: 'taskCenter.status.running', queued: 'taskCenter.status.queued', pending: 'taskCenter.status.pending',
    succeeded: 'taskCenter.status.succeeded', completed: 'taskCenter.status.completed', failed: 'taskCenter.status.failed',
    canceled: 'taskCenter.status.canceled', interrupted: 'taskCenter.status.interrupted', recovering: 'taskCenter.status.recovering',
    cancel_requested: 'taskCenter.status.cancelRequested', degraded: 'taskCenter.degraded', warning: 'taskCenter.levelWarning',
  };
  const TYPE_KEYS = {
    generation: 'taskCenter.generation', transcode: 'taskCenter.transcode', review: 'taskCenter.review',
    index: 'taskCenter.index', system: 'taskCenter.system',
  };
  const EVENT_NAME_KEYS = Object.freeze({
    'http.request.completed': 'taskCenter.event.requestCompleted',
    'job.progress': 'taskCenter.event.taskProgress',
    'job.heartbeat': 'taskCenter.event.taskHeartbeat',
    'job.created': 'taskCenter.event.taskCreated',
    'job.completed': 'taskCenter.event.taskCompleted',
    'job.succeeded': 'taskCenter.event.taskCompleted',
    'job.failed': 'taskCenter.event.taskFailed',
    'job.cancel_requested': 'taskCenter.event.cancelRequested',
    'job.interrupted': 'taskCenter.event.taskInterrupted',
    'job.recovering': 'taskCenter.event.taskRecovering',
  });
  const EVENT_TYPE_ALIASES = Object.freeze({
    progress: 'job.progress', heartbeat: 'job.heartbeat', created: 'job.created',
    completed: 'job.completed', succeeded: 'job.succeeded', failed: 'job.failed',
    cancel_requested: 'job.cancel_requested', interrupted: 'job.interrupted', recovering: 'job.recovering',
  });
  const STATUS_GROUPS = {
    running: new Set(['running', 'recovering', 'cancel_requested']), queued: new Set(['queued', 'pending']),
    succeeded: new Set(['succeeded', 'completed']), failed: new Set(['failed', 'interrupted']),
  };

  function parseDeepLinkHash(hash) {
    const taskMatch = /^task-(.+)$/.exec(hash);
    if (taskMatch) return {type: 'task', id: taskMatch[1]};
    const eventMatch = /^event-(.+)$/.exec(hash);
    if (eventMatch) return {type: 'event', id: eventMatch[1]};
    const assetMatch = /^asset-(.+)$/.exec(hash);
    if (assetMatch) return {type: 'asset', id: assetMatch[1]};
    return null;
  }

  const deepLink = parseDeepLinkHash(initialHash);
  const hasTaskContext = ['job_id', 'project_id', 'entity_id', 'asset_id', 'canvas_id']
    .some(key => Boolean(initial.get(key)));
  const state = {
    view: initial.get('view') || (hasTaskContext || deepLink ? 'tasks' : 'overview'), range: initial.get('range') || '15m', source: initial.get('source') || 'all',
    level: initial.get('level') === 'warning' ? 'warn' : (initial.get('level') || 'all'), status: initial.get('status') || 'all', logStatus: initial.get('status') || 'all', stableId: initial.get('stable_id') || initial.get('asset_id') || (deepLink?.type === 'asset' ? deepLink.id : ''),
    projectId: initial.get('project_id') || '', entityId: initial.get('entity_id') || '', assetId: initial.get('asset_id') || '', canvasId: initial.get('canvas_id') || '', jobId: initial.get('job_id') || (deepLink?.type === 'task' ? deepLink.id : ''),
    eventId: initial.get('event_id') || (deepLink?.type === 'event' ? deepLink.id : ''), overview: {}, tasks: [], stalledTasks: [], events: [], series: {}, health: [], sources: [], assetVolumes: [], longTasks: [], eventWindow: null, taskWindow: null,
    nextCursor: '', hasMore: false, taskNextCursor: '', taskHasMore: false, assetVolumeNextCursor: '', assetVolumeHasMore: false, assetVolumeLoading: false, assetVolumeError: '', longTaskLoading: false, longTasksError: '', stalledTasksError: '', stalledTasksDataStatus: 'ok', loading: false, loadError: '', loadErrorKind: '', degraded: [], degradedKinds: [], permissionDenied: false, summaryOnly: false, role: '', lastUpdated: '', detail: null,
    detailActions: {}, detailActionReasons: {}, selectedJobId: '', detailTrigger: null, detailLoading: false, detailError: '', pendingAction: '', actionBusy: '',
    deepLinkPending: Boolean(initial.get('job_id') || initial.get('event_id') || deepLink), toastTimer: 0,
  };
  let touchbarReportTimer = 0;
  function reportTaskTouchbarContext() {
    const task = state.detail || knownTasks().find(item => item.id === state.selectedJobId || item.id === state.jobId) || null;
    const jobId = String(task?.id || state.selectedJobId || state.jobId || '').trim();
    const production = task?.production_context || {};
    const context = {
      project_id: production.project_id || task?.project_id || state.projectId,
      entity_id: production.entity_id || task?.entity_id || state.entityId,
      asset_id: production.asset_id || task?.asset_id || state.assetId,
      canvas_id: production.canvas_id || task?.canvas_id || state.canvasId,
      job_id: jobId,
    };
    const actions = jobId ? ['task.view'] : [];
    if (jobId && canEdit() && state.detailActions?.cancel) actions.push('task.cancel');
    if (jobId && canEdit() && state.detailActions?.retry) actions.push('task.retry');
    Workspace.reportTouchbarCapabilities?.({
      page: 'task-center',
      context,
      selection: jobId ? {type: 'job', id: jobId, label: task?.summary || task?.title || jobId, status: task?.status || ''} : null,
      actions,
    });
  }
  function scheduleTaskTouchbarReport() {
    clearTimeout(touchbarReportTimer);
    touchbarReportTimer = setTimeout(reportTaskTouchbarContext, 0);
  }
  const AUTO_REFRESH_INTERVAL_MS = 5000;
  let autoRefreshTimer = 0;
  let autoRefreshEnabled = true;
  let dataRequestOwner = '';
  let queuedReload = false;

  const tr = key => window.StudioI18n?.t?.(key) || key;
  const trf = (key, values = {}) => Object.entries(values).reduce((text, [name, value]) => text.replaceAll(`{${name}}`, String(value)), tr(key));
  const locale = () => window.StudioI18n?.lang?.() === 'en' ? 'en-US' : 'zh-CN';
  const TASK_UI_TEXT = Object.freeze({
    zh: {
      stalledHeading: '长期任务告警', stalledCount: '{count} 项需核对', queued: '长期排队', recovering: '长期恢复', historical: '历史残留', unknown: '停滞时长未知',
      stalledFor: '已停滞 {age}', updatedAt: '最后更新 {value}', lastEvent: '最后事件 {value}', lastEventUnknown: '事件类型未知', noRecentEvent: '当前窗口无最后事件', unknownTime: '更新时间不可用',
      queuedMessage: '任务已排队 {age}，最后更新于 {updated}；当前窗口无最后事件。', recoveringMessage: '任务已恢复 {age}，最后更新于 {updated}；请先核对外部副作用。',
      historicalMessage: '任务已停滞 {age}，最后更新于 {updated}；按历史残留展示，不计入正在运行。', unknownMessage: '后端未返回有效更新时间，无法判断停滞时长；请刷新或打开详情。',
      degraded: '长期任务候选查询已降级，列表可能不完整。', unavailable: '长期任务候选查询暂不可用。', noStalled: '当前没有检测到长期排队或长期恢复任务。',
    },
    en: {
      stalledHeading: 'Stalled task alerts', stalledCount: '{count} need review', queued: 'Long queued', recovering: 'Long recovering', historical: 'Historical residue', unknown: 'Stall duration unavailable',
      stalledFor: 'Stalled for {age}', updatedAt: 'Last update {value}', lastEvent: 'Last event {value}', lastEventUnknown: 'Event type unavailable', noRecentEvent: 'No latest event in current window', unknownTime: 'Update time unavailable',
      queuedMessage: 'Queued for {age}; last update {updated}. No latest event in the current window.', recoveringMessage: 'Recovering for {age}; last update {updated}. Check external side effects first.',
      historicalMessage: 'Stalled for {age}; last update {updated}. Shown as historical residue, not counted as running.', unknownMessage: 'The backend did not return a valid update time, so stall duration cannot be determined. Refresh or open details.',
      degraded: 'Stalled-task candidate query is degraded; the list may be incomplete.', unavailable: 'Stalled-task candidate query is unavailable.', noStalled: 'No long-queued or long-recovering tasks detected.',
    },
  });
  function taskUiText(key, values = {}) {
    const table = TASK_UI_TEXT[locale() === 'en-US' ? 'en' : 'zh'];
    return Object.entries(values).reduce((text, [name, value]) => text.replaceAll(`{${name}}`, String(value)), table[key] || key);
  }
  const cssToken = value => String(value || '').replace(/[^a-z0-9_-]/gi, '') || 'pending';
  const ASSET_TYPE_KEYS = new Set(['image', 'video', 'audio', 'font', 'model', 'document', 'workflow', 'project_file', 'text', 'other']);
  const ASSET_TYPE_LABEL_KEYS = Object.freeze({
    image: 'taskCenter.assetType.image', video: 'taskCenter.assetType.video', audio: 'taskCenter.assetType.audio',
    font: 'taskCenter.assetType.font', model: 'taskCenter.assetType.model', document: 'taskCenter.assetType.document',
    workflow: 'taskCenter.assetType.workflow', project_file: 'taskCenter.assetType.projectFile', text: 'taskCenter.assetType.text',
    other: 'taskCenter.assetType.other',
  });
  const BUILT_IN_ASSET_TYPE_COLORS = Object.freeze({
    image: 'var(--asset-type-image, #20a35a)', video: 'var(--asset-type-video, #2878d0)', audio: 'var(--sf-amber, #f59e0b)',
    font: 'var(--asset-type-font, #f05a32)', model: 'var(--sf-coral, #ff6b6b)',
    document: 'color-mix(in srgb, var(--asset-type-text, #e3a51a) 72%, var(--ink) 28%)',
    workflow: 'color-mix(in srgb, var(--asset-type-other, #7654b8) 72%, var(--ink) 28%)',
    project_file: 'color-mix(in srgb, var(--asset-type-font, #f05a32) 72%, var(--ink) 28%)',
    text: 'var(--asset-type-text, #e3a51a)',
    other: 'var(--asset-type-other, #7654b8)',
  });
  const canEdit = () => ['editor', 'admin', 'governor'].includes(String(window.__taskCenterPrincipal?.role || ''));
  const LONG_TASK_KEYS = { import: 'taskCenter.longTaskImport', thumbnail: 'taskCenter.longTaskThumbnail', transcode: 'taskCenter.longTaskTranscode' };
  const LONG_TASK_JOB_TYPES = Object.freeze(['asset.index', 'asset.ingest.legacy', 'asset.import', 'asset.thumbnail', 'asset.proxy', 'asset.transcode', 'video.transcode']);
  const LONG_TASK_PATTERNS = [['thumbnail', /thumbnail|storyboard/i], ['transcode', /transcode|convert|proxy|ffmpeg/i], ['import', /import|ingest|index|scan|register/i]];
  const TASK_STALL_THRESHOLDS_MS = Object.freeze({queued: 30 * 60 * 1000, recovering: 15 * 60 * 1000});
  const TASK_HISTORY_THRESHOLD_MS = 24 * 60 * 60 * 1000;
  const LONG_TASK_STATUS_ORDER = Object.freeze({
    running: 0, recovering: 0, cancel_requested: 0, queued: 1, pending: 1,
    succeeded: 2, completed: 2, failed: 3, interrupted: 3, canceled: 4, cancelled: 4,
  });
  function longTaskKind(jobType) { const value = String(jobType || '').toLowerCase(); return (LONG_TASK_PATTERNS.find(([, pattern]) => pattern.test(value)) || [null])[0]; }
  function longTaskStatusRank(status) { return LONG_TASK_STATUS_ORDER[String(status || '').toLowerCase()]; }
  function assetVolumeType(item) {
    const value = String(item?.asset_type || item?.kind || item?.type || '').trim().toLowerCase();
    if (ASSET_TYPE_KEYS.has(value)) return value;
    if (value) return value;
    const mime = String(item?.mime_type || '').trim().toLowerCase();
    const prefix = mime.split('/', 1)[0];
    return ASSET_TYPE_KEYS.has(prefix) ? prefix : 'other';
  }
  function assetVolumeTypeLabel(type) { return ASSET_TYPE_LABEL_KEYS[type] ? tr(ASSET_TYPE_LABEL_KEYS[type]) : (cleanId(type) || tr(ASSET_TYPE_LABEL_KEYS.other)); }
  const unknownAssetTypeColorCache = new Map();
  function normalizeAssetTypeForColor(type) { return String(type || '').trim().toLowerCase(); }
  function assetTypeStableHash(type) {
    let hash = 2166136261;
    for (const character of String(type || '')) {
      hash ^= character.codePointAt(0);
      hash = Math.imul(hash, 16777619);
    }
    return hash >>> 0;
  }
  function assetVolumeColorForType(type, offset = 0) {
    const hash = assetTypeStableHash(type);
    const hue = (hash + (offset * 137)) % 360;
    const saturation = 48 + ((hash >>> 8) % 22);
    const lightness = 42 + ((hash >>> 16) % 18);
    const identity = `${hash.toString(16).padStart(8, '0')}-${offset.toString(16)}`;
    return `hsl(${hue} ${saturation}% ${lightness}%) /*gw-asset-type:${identity}*/`;
  }
  function ensureUnknownAssetTypeColor(type) {
    const value = normalizeAssetTypeForColor(type);
    if (!value || ASSET_TYPE_KEYS.has(value)) return '';
    if (!unknownAssetTypeColorCache.has(value)) {
      const usedColors = new Set(unknownAssetTypeColorCache.values());
      let offset = 0;
      let color = assetVolumeColorForType(value, offset);
      while (usedColors.has(color)) {
        offset += 1;
        color = assetVolumeColorForType(value, offset);
      }
      unknownAssetTypeColorCache.set(value, color);
    }
    return unknownAssetTypeColorCache.get(value) || '';
  }
  function assetVolumeColorMap(types = []) {
    const unknownTypes = [...new Set((Array.isArray(types) ? types : []).map(normalizeAssetTypeForColor)
      .filter(type => type && !ASSET_TYPE_KEYS.has(type)))].sort();
    unknownTypes.forEach(ensureUnknownAssetTypeColor);
    return new Map(unknownTypes.map(type => [type, ensureUnknownAssetTypeColor(type)]));
  }
  function assetVolumeColorStyle(type, colorMap = null) {
    const value = normalizeAssetTypeForColor(type);
    if (!value || ASSET_TYPE_KEYS.has(value)) return '';
    let color = colorMap instanceof Map ? colorMap.get(value) : null;
    if (!color) color = ensureUnknownAssetTypeColor(value);
    // Blend extension colors with the active theme text color so labels and
    // borders remain readable in both light and dark themes.
    return ` style="--asset-type-color:color-mix(in srgb, ${color} 78%, var(--sf-text, var(--ink)) 22%) !important"`;
  }

  function statusText(status) {
    const value = String(status || 'pending').toLowerCase();
    if (value === 'paused') return locale() === 'en-US' ? 'Paused' : '已暂停';
    if (value === 'cancelled') return tr('taskCenter.status.canceled');
    return tr(STATUS_KEYS[value] || 'taskCenter.status.pending');
  }

  function statusMatches(filter, status) {
    if (filter === 'all') return true;
    const value = String(status || '').toLowerCase();
    return STATUS_GROUPS[filter]?.has(value) || value === filter;
  }

  function taskType(jobType) {
    const value = String(jobType || '').toLowerCase();
    if (/transcode|convert/.test(value)) return 'transcode';
    if (/review|approval/.test(value)) return 'review';
    if (/index|ingest|thumbnail|asset\./.test(value)) return 'index';
    if (/generate|generation|canvas|video|image|jimeng|online/.test(value)) return 'generation';
    return 'system';
  }

  const TASK_ACTION_NAMES = Object.freeze([
    [/generation\.(?:online_)?image|online-image|image-generation/i, ['生成图片', 'Generate image']],
    [/generation\.(?:video|tudou)|video-generation|video\.generate/i, ['生成视频', 'Generate video']],
    [/generation\.workflow|workflow/i, ['执行工作流', 'Run workflow']],
    [/transcode|convert/i, ['转码视频', 'Transcode video']],
    [/thumbnail/i, ['生成缩略图', 'Generate thumbnail']],
    [/asset\.(?:index|ingest)|index|ingest/i, ['索引素材', 'Index assets']],
    [/asset\.(?:move|transfer)|file\.(?:move|transfer)/i, ['移动文件', 'Move file']],
    [/asset\.(?:archive)|file\.(?:archive)|archive/i, ['归档文件', 'Archive file']],
    [/asset\.(?:remove|delete)|library\.(?:remove|delete)/i, ['移除素材库', 'Remove asset library']],
    [/review|approval/i, ['审阅资产', 'Review asset']],
    [/canvas\.(?:save|update)|canvas-save/i, ['保存画布', 'Save canvas']],
    [/canvas\.(?:export)|canvas-export/i, ['导出画布', 'Export canvas']],
  ]);

  function taskDisplayName(item) {
    const type = String(item?.job_type || item?.type || '').trim();
    const supplied = String(item?.title || item?.name || '').trim();
    const machineSummary = supplied === type || supplied.startsWith(`${type} ·`) || /^[a-z][a-z0-9_-]*(?:\.[a-z0-9_-]+)+(?:\s·\s|$)/i.test(supplied);
    if (supplied && !machineSummary && !/^[a-z][a-z0-9_-]*(?:\.[a-z0-9_-]+)+$/i.test(supplied)) return supplied;
    const match = TASK_ACTION_NAMES.find(([pattern]) => pattern.test(type));
    if (match) return locale() === 'en-US' ? match[1][1] : match[1][0];
    return locale() === 'en-US' ? 'Workspace task' : '工作台任务';
  }

  function firstObject(data, keys) {
    for (const key of keys) if (data?.[key] && typeof data[key] === 'object' && !Array.isArray(data[key])) return data[key];
    return data && typeof data === 'object' ? data : {};
  }

  function firstArray(data, keys) {
    for (const key of keys) if (Array.isArray(data?.[key])) return data[key];
    return [];
  }

  function cleanId(value) { return String(value || '').trim(); }

  function eventLabelKey(value, source = 'event_name') {
    const raw = cleanId(value).toLowerCase();
    const canonical = EVENT_TYPE_ALIASES[raw] || raw;
    if (EVENT_NAME_KEYS[canonical]) return EVENT_NAME_KEYS[canonical];
    if (canonical.startsWith('job.')) return 'taskCenter.event.taskEvent';
    if (canonical.startsWith('http.')) return 'taskCenter.event.apiRequest';
    if (canonical.startsWith('audit.')) return 'taskCenter.event.auditRecord';
    if (source === 'event_type' && canonical) return 'taskCenter.event.taskEvent';
    return 'taskCenter.event.runtime';
  }

  function eventDisplayName(event) {
    const candidates = [[event?.event_name, 'event_name'], [event?.event_type, 'event_type']];
    for (const [value] of candidates) {
      const raw = cleanId(value).toLowerCase();
      const canonical = EVENT_TYPE_ALIASES[raw] || raw;
      if (EVENT_NAME_KEYS[canonical]) return tr(EVENT_NAME_KEYS[canonical]);
    }
    const candidate = candidates.find(([value]) => cleanId(value));
    return tr(eventLabelKey(candidate?.[0], candidate?.[1]));
  }

  function isMachineEventName(value) {
    return /^[a-z][a-z0-9_-]*(?:\.[a-z0-9_-]+)+$/i.test(cleanId(value));
  }

  function persistentTask(item) {
    const jobType = String(item.job_type || item.type || 'system');
    const id = cleanId(item.job_id || item.id || item.task_id);
    const summary = taskDisplayName(item);
    return {
      ...item, id, job_id: id, jobType, summary, title: summary, source: cleanId(item.source || item.provider || item.origin),
      type: taskType(jobType), status: String(item.status || 'queued'), progress: item.progress ?? null,
      created_at: item.created_at || item.timestamp, updated_at: item.updated_at || item.timestamp || item.created_at,
    };
  }

  function timestampMs(value) {
    if (typeof value === 'number' && Number.isFinite(value)) return Math.abs(value) < 100000000000 ? value * 1000 : value;
    const text = String(value || '').trim();
    if (!text) return 0;
    const numeric = Number(text);
    if (Number.isFinite(numeric)) return Math.abs(numeric) < 100000000000 ? numeric * 1000 : numeric;
    const parsed = Date.parse(text);
    return Number.isFinite(parsed) ? parsed : 0;
  }

  function taskLastUpdatedMs(task) {
    return timestampMs(task?.updated_at || task?.created_at);
  }

  function formatAge(ageMs) {
    if (!Number.isFinite(ageMs) || ageMs < 0) return taskUiText('unknown');
    const minutes = Math.floor(ageMs / 60000);
    if (minutes < 1) return locale() === 'en-US' ? '<1 min' : '不到 1 分钟';
    if (minutes < 60) return locale() === 'en-US' ? `${minutes} min` : `${minutes} 分钟`;
    const hours = Math.floor(minutes / 60);
    if (hours < 24) {
      const remainder = minutes % 60;
      return locale() === 'en-US' ? `${hours}h${remainder ? ` ${remainder}m` : ''}` : `${hours} 小时${remainder ? ` ${remainder} 分钟` : ''}`;
    }
    const days = Math.floor(hours / 24);
    const remainder = hours % 24;
    return locale() === 'en-US' ? `${days}d${remainder ? ` ${remainder}h` : ''}` : `${days} 天${remainder ? ` ${remainder} 小时` : ''}`;
  }

  function taskStallState(task, nowMs = Date.now()) {
    const status = String(task?.status || '').toLowerCase();
    const family = status === 'recovering' ? 'recovering' : ['queued', 'pending'].includes(status) ? 'queued' : '';
    if (!family) return {kind: 'none', family: '', stale: false, ageMs: null, updatedAtMs: taskLastUpdatedMs(task)};
    const projectedKind = String(task?.stall_classification || '').trim().toLowerCase();
    const projectedKinds = new Set(['none', 'queued', 'recovering', 'historical', 'unknown']);
    if (projectedKind) {
      const kind = projectedKinds.has(projectedKind) ? projectedKind : 'unknown';
      const activityAtMs = timestampMs(task?.stall_activity_at || task?.last_event_at || task?.heartbeat_at);
      const projectedAge = Number(task?.stall_age_ms);
      const ageMs = Number.isFinite(projectedAge) && projectedAge >= 0
        ? projectedAge
        : activityAtMs && Number.isFinite(nowMs) && nowMs >= activityAtMs ? nowMs - activityAtMs : null;
      return {
        kind,
        family: kind === 'recovering' ? 'recovering' : kind === 'queued' ? 'queued' : family,
        stale: kind !== 'none',
        ageMs,
        updatedAtMs: activityAtMs || taskLastUpdatedMs(task),
        activityAtMs,
        activitySource: cleanId(task?.stall_activity_source || ''),
        projection: 'server',
      };
    }
    // Older task responses lack the server projection. Keep the legacy
    // calculation explicitly marked so callers can surface a degraded state.
    const updatedAtMs = taskLastUpdatedMs(task);
    if (!updatedAtMs || !Number.isFinite(nowMs) || nowMs < updatedAtMs) {
      return {kind: 'unknown', family, stale: true, ageMs: null, updatedAtMs, projection: 'legacy'};
    }
    const ageMs = Math.max(0, nowMs - updatedAtMs);
    if (ageMs < TASK_STALL_THRESHOLDS_MS[family]) return {kind: 'none', family, stale: false, ageMs, updatedAtMs, projection: 'legacy'};
    return {kind: ageMs >= TASK_HISTORY_THRESHOLD_MS ? 'historical' : family, family, stale: true, ageMs, updatedAtMs, projection: 'legacy'};
  }

  function taskLifecycleSignal(task, nowMs = Date.now()) {
    const stateValue = taskStallState(task, nowMs);
    if (!stateValue.stale) return null;
    const updated = stateValue.updatedAtMs ? dateTime(stateValue.updatedAtMs) : taskUiText('unknownTime');
    const age = formatAge(stateValue.ageMs);
    const label = taskUiText(stateValue.kind === 'historical' ? 'historical' : stateValue.kind);
    const messageKey = stateValue.kind === 'historical' ? 'historicalMessage' : stateValue.kind === 'unknown' ? 'unknownMessage' : `${stateValue.kind}Message`;
    const message = stateValue.kind === 'unknown'
      ? taskUiText(messageKey)
      : taskUiText(messageKey, {age, updated});
    return {...stateValue, label, ageLabel: taskUiText('stalledFor', {age}), message};
  }

  function finiteProgressNumber(value) {
    return typeof value === 'number' && Number.isFinite(value) ? value : null;
  }

  function progressCounts(progress) {
    if (!progress || typeof progress !== 'object' || Array.isArray(progress)) return null;
    const total = finiteProgressNumber(progress.total);
    const processed = finiteProgressNumber(progress.processed);
    if (total === null || total <= 0 || processed === null || processed < 0 || processed > total) return null;
    return {processed, total};
  }

  function progressValue(progress) {
    if (!progress || typeof progress !== 'object' || Array.isArray(progress)) return null;
    const percent = finiteProgressNumber(progress.percent);
    if (percent !== null && percent >= 0 && percent <= 100) return percent;
    const counts = progressCounts(progress);
    return counts ? (counts.processed / counts.total) * 100 : null;
  }

  function progressPercentText(value) {
    return `${String(Number(value.toFixed(1)))}%`;
  }

  function progressCountText(value) {
    return Number.isInteger(value) ? value.toLocaleString(locale()) : String(value);
  }

  function progressDescriptor(task) {
    const value = progressValue(task.progress);
    if (value !== null && Number.isFinite(value)) {
      const percent = Math.max(0, Math.min(100, value));
      const rawPercent = finiteProgressNumber(task.progress?.percent);
      const counts = progressCounts(task.progress);
      const label = rawPercent !== null && rawPercent >= 0 && rawPercent <= 100
        ? progressPercentText(rawPercent)
        : `${progressCountText(counts.processed)}/${progressCountText(counts.total)}`;
      return {kind: 'determinate', value: percent, label};
    }
    const status = String(task.status || '').toLowerCase();
    if (['running', 'recovering', 'cancel_requested'].includes(status)) {
      return {kind: 'indeterminate', value: null, label: tr('taskCenter.progressRunning')};
    }
    if (['queued', 'pending'].includes(status)) {
      return {kind: 'queued', value: null, label: tr('taskCenter.progressQueued')};
    }
    return {kind: 'unavailable', value: null, label: tr('taskCenter.progressUnavailable')};
  }

  function renderTaskProgress(task) {
    const progress = progressDescriptor(task);
    const accessibleLabel = trf('taskCenter.progressAccessibleLabel', {value: progress.label});
    const valueAttrs = `tabindex="0" aria-label="${esc(accessibleLabel)}" aria-valuemin="0" aria-valuemax="100" aria-valuetext="${esc(progress.label)}"${progress.value === null ? '' : ` aria-valuenow="${progress.value}"`}${progress.kind === 'indeterminate' ? ' aria-busy="true"' : ''}`;
    const width = progress.value === null ? '' : ` style="width:${progress.value}%"`;
    return `<div class="task-progress"><div class="task-progress-head"><span>${esc(progress.label)}</span><strong>${esc(progress.value === null ? '—' : progress.label)}</strong></div><div class="task-progress-track ${esc(progress.kind)}" data-progress-state="${esc(progress.kind)}" role="progressbar" ${valueAttrs}><span class="task-progress-fill"${width}></span></div></div>`;
  }

  function assetVolumeQuery(extra = {}) {
    const query = new URLSearchParams({limit: '40'});
    Object.entries(extra).forEach(([key, value]) => {
      if (value !== undefined && value !== null && String(value)) query.set(key, String(value));
    });
    return query;
  }

  function readAutoRefreshPreference() {
    try {
      const preferences = JSON.parse(localStorage.getItem('workspace_preferences') || '{}');
      return preferences?.task_refresh !== false;
    } catch (_) {
      return true;
    }
  }

  function requestRefresh() {
    if (document.hidden) return Promise.resolve();
    return load().catch(error => {
      state.loading = false;
      state.loadError = taskCenterDegradationNotice(error);
      render();
    });
  }

  function acquireDataRequest(owner) {
    if (dataRequestOwner) return false;
    dataRequestOwner = owner;
    return true;
  }

  function finishDataRequest(owner) {
    if (dataRequestOwner !== owner) return false;
    dataRequestOwner = '';
    const shouldReload = queuedReload;
    queuedReload = false;
    return shouldReload;
  }

  function resetPaginationState() {
    state.nextCursor = '';
    state.hasMore = false;
    state.taskNextCursor = '';
    state.taskHasMore = false;
    state.eventWindow = null;
    state.taskWindow = null;
    state.assetVolumeNextCursor = '';
    state.assetVolumeHasMore = false;
    state.assetVolumeError = '';
  }

  function runQueuedReload(shouldReload) {
    if (!shouldReload) return;
    resetPaginationState();
    void load();
  }

  function stopAutoRefresh() {
    if (!autoRefreshTimer) return;
    window.clearInterval(autoRefreshTimer);
    autoRefreshTimer = 0;
  }

  function startAutoRefresh() {
    if (document.hidden || autoRefreshTimer || !autoRefreshEnabled) return;
    autoRefreshTimer = window.setInterval(() => { void requestRefresh(); }, AUTO_REFRESH_INTERVAL_MS);
  }

  function syncAutoRefresh({refreshNow = false} = {}) {
    const enabled = readAutoRefreshPreference();
    const changed = enabled !== autoRefreshEnabled;
    autoRefreshEnabled = enabled;
    if (document.hidden || !enabled) {
      stopAutoRefresh();
      return;
    }
    startAutoRefresh();
    if (refreshNow && changed) void requestRefresh();
  }

  function observationQuery(extra = {}, scope = '') {
    const query = new URLSearchParams({range: state.range});
    if (scope === 'events') {
      query.set('source', state.source === 'all' ? '' : state.source);
      query.set('level', state.level === 'all' ? '' : state.level);
      query.set('status', state.status === 'all' ? '' : state.status);
    }
    if (scope === 'tasks') query.set('status', state.status === 'all' ? '' : state.status);
    if (scope === 'events' || scope === 'tasks') {
      if (state.stableId) query.set('stable_id', state.stableId);
      if (state.projectId) query.set('project_id', state.projectId);
      if (state.entityId) query.set('entity_id', state.entityId);
      if (state.assetId) query.set('asset_id', state.assetId);
      if (state.canvasId) query.set('canvas_id', state.canvasId);
      if (state.jobId && scope === 'events') query.set('job_id', state.jobId);
      if (state.jobId && scope === 'tasks') query.set('job_id', state.jobId);
      if (state.eventId && scope === 'events') query.set('event_id', state.eventId);
    }
    Object.entries(extra).forEach(([key, value]) => {
      if (Array.isArray(value)) {
        query.delete(key);
        value.forEach(item => { if (item !== undefined && item !== null && String(item)) query.append(key, String(item)); });
      } else if (value !== undefined && value !== null && String(value)) query.set(key, String(value));
    });
    return query;
  }

  function setQueryWindow(query, window) {
    if (!window) return query;
    query.set('start_ms', String(window.start_ms));
    query.set('end_ms', String(window.end_ms));
    return query;
  }

  function responseWindow(data) {
    const start = Number(data?.start_ms);
    const end = Number(data?.end_ms);
    return Number.isSafeInteger(start) && start >= 0 && Number.isSafeInteger(end) && end > start
      ? {start_ms: start, end_ms: end}
      : null;
  }

  function syncUrl(extra = {}) {
    const url = new URL(location.href);
    const values = {view: state.view, range: state.range, source: state.source, level: state.level, status: state.status, stable_id: state.stableId, project_id: state.projectId, entity_id: state.entityId, asset_id: state.assetId, canvas_id: state.canvasId, job_id: state.jobId, event_id: state.eventId, ...extra};
    Object.entries(values).forEach(([key, value]) => value && value !== 'all' ? url.searchParams.set(key, String(value)) : url.searchParams.delete(key));
    let hash = '';
    if (state.selectedJobId) hash = `#task-${state.selectedJobId}`;
    else if (state.jobId) hash = `#task-${state.jobId}`;
    else if (state.eventId) hash = `#event-${state.eventId}`;
    else if (state.stableId && state.stableId.startsWith('asset-')) hash = `#asset-${state.stableId}`;
    history.replaceState({}, '', `${url.pathname}${url.search}${hash}`);
  }

  function setText(selector, value) { const node = q(selector); if (node) node.textContent = String(value ?? '—'); }
  function formatNumber(value, suffix = '') { return typeof value === 'number' && Number.isFinite(value) ? `${value.toLocaleString(locale())}${suffix}` : (value || '—'); }
  function formatDuration(value) { return typeof value === 'number' && Number.isFinite(value) ? `${Math.round(value)} ms` : (value || '—'); }
  function statusClass(value) {
    const normalized = String(value || 'pending').toLowerCase();
    return cssToken(normalized === 'warn' ? 'warning' : normalized);
  }
  function matchesContext(item) { return (!state.projectId || cleanId(item.project_id) === state.projectId) && (!state.entityId || cleanId(item.entity_id) === state.entityId) && (!state.assetId || cleanId(item.asset_id) === state.assetId) && (!state.canvasId || cleanId(item.canvas_id) === state.canvasId); }

  function dataGap(message = tr('taskCenter.noData')) { return `<div class="data-gap">${esc(message)}</div>`; }
  function assetVolumeStateRow(message) { return `<div class="asset-volume-state-row" role="row"><div class="data-gap" role="cell" aria-colspan="3">${esc(message)}</div></div>`; }
  function isPermissionError(error) { return /401|403|权限|登录|forbidden|unauthor/i.test(String(error?.message || error || '')); }
  function noteDataStatus(data, label) {
    if (data?.data_status !== 'degraded') return;
    if (Array.isArray(data.degraded_sources) && data.degraded_sources.includes('permission') && state.role === 'reviewer') state.summaryOnly = true;
    pushDegraded(`${label} · ${tr('taskCenter.degradedData')}`, '');
  }

  function renderStatus(status) {
    const token = statusClass(status);
    return `<span class="status-chip ${token}"><i class="status-dot ${token}" aria-hidden="true"></i>${esc(statusText(status))}</span>`;
  }

  // 统一显式降级（裁决第 3 项）：命中 NOT_INTEGRATED 时明说「未接入」，而非泛泛「查询不可用」。
  // 分项降级（裁决第 3 项，Phase 9T）：Promise.allSettled 的**每条**失败都必须区分
  // 「未接入（404/501，未纳入当前切片）」与「服务暂时不可用（503）」，不得退回泛泛的
  // 「XX 数据暂不可用」——后者会在后端未实现时被误读为「后端存在但暂时取不到」。
  const NOT_INTEGRATED_LABEL = '该功能尚未接入后端（未纳入当前切片）';
  function degradeLabel(reason, label) {
    const code = reason && reason.code;
    if (code === 'NOT_INTEGRATED') return label + ' · ' + NOT_INTEGRATED_LABEL;
    if (code === 'SERVICE_UNAVAILABLE') return label + ' · ' + tr('taskCenter.serviceUnavailable');
    return label;
  }
  function degradeKind(reason) {
    const code = reason && reason.code;
    if (code === 'NOT_INTEGRATED') return 'not_integrated';
    if (code === 'SERVICE_UNAVAILABLE') return 'service_unavailable';
    return '';
  }
  // 分项降级登记（审核发现 D2）：每条降级记录携带**自身** kind，
  // 不得在渲染时用全屏汇总标记，否则一页混装 404 与 503 时全部被标成 not_integrated。
  function pushDegraded(text, kind) {
    const normalized = kind || '';
    state.degraded.push({ text: String(text), kind: normalized });
    if (normalized) state.degradedKinds.push(normalized);
  }
  function taskCenterDegradationNotice(error) {
    state.loadErrorKind = error && error.code === 'NOT_INTEGRATED' ? 'not_integrated'
      : (error && error.code === 'SERVICE_UNAVAILABLE' ? 'service_unavailable' : 'error');
    if (state.loadErrorKind !== 'error') return error.message;
    return error && error.message ? error.message : tr('taskCenter.queryUnavailable');
  }
  function renderNotice() {
    const notice = q('#observabilityNotice');
    if (!notice) return;
    const parts = [];
    if (state.permissionDenied) parts.push(`<strong>${esc(tr('taskCenter.permissionDenied'))}</strong>`);
    if (state.summaryOnly) parts.push(`<span class="notice-degraded"><i data-lucide="eye" aria-hidden="true"></i>${esc(tr('taskCenter.summaryOnly'))}</span>`);
    if (state.loadError) parts.push(`<strong data-gw-degradation="${esc(state.loadErrorKind || 'error')}">${esc(state.loadError)}</strong>`);
    // 页面级兜底标记（仅用于未携带自身 kind 的历史条目，如 data_status=degraded 的后端自报降级）。
    const fallbackKind = state.degradedKinds.includes('not_integrated') ? 'not_integrated'
      : (state.degradedKinds.includes('service_unavailable') ? 'service_unavailable' : 'degraded');
    state.degraded.forEach(item => {
      const kind = (item && item.kind) || fallbackKind;
      const text = item && typeof item === 'object' ? item.text : item;
      parts.push(`<span class="notice-degraded" data-gw-degradation="${esc(kind)}"><i data-lucide="triangle-alert" aria-hidden="true"></i>${esc(text)}</span>`);
    });
    notice.innerHTML = parts.length ? parts.join('') : '';
    notice.classList.toggle('visible', parts.length > 0);
    if (window.lucide?.createIcons) window.lucide.createIcons({attrs: {'aria-hidden': 'true'}});
  }

  function renderContext() {
    const scope = q('#observationScope');
    const context = q('#contextList');
    const values = [[tr('taskCenter.timeRange'), state.range], [tr('taskCenter.source'), state.source === 'all' ? tr('taskCenter.allSources') : state.source], [tr('taskCenter.level'), state.level === 'all' ? tr('taskCenter.allLevels') : state.level], [tr('taskCenter.status'), state.status === 'all' ? tr('taskCenter.allStatuses') : statusText(state.status)]];
    if (state.stableId) values.push([tr('taskCenter.stableId'), state.stableId]);
    if (state.projectId) values.push([tr('taskCenter.project'), state.projectId]);
    if (state.entityId) values.push([tr('taskCenter.entity'), state.entityId]);
    if (state.assetId) values.push([tr('taskCenter.asset'), state.assetId]);
    if (state.canvasId) values.push([tr('taskCenter.canvas'), state.canvasId]);
    if (scope) scope.textContent = state.stableId ? `${tr('taskCenter.filteredBy')} ${state.stableId}` : `${tr('taskCenter.rangeLabel')} ${state.range}`;
    if (context) context.innerHTML = values.map(([label, value]) => `<div><dt>${esc(label)}</dt><dd>${esc(value)}</dd></div>`).join('');
  }

  function renderSummary() {
    const summary = firstObject(state.overview, ['summary', 'overview', 'counts']); const jobs = firstObject(summary, ['jobs', 'tasks']); const byStatus = firstObject(jobs, ['by_status', 'statuses']);
    const items = [[tr('taskCenter.summaryAll'), summary.total ?? summary.tasks ?? summary.task_count ?? jobs.total], [tr('taskCenter.summaryFailed'), summary.failed ?? summary.failures ?? summary.failed_count ?? byStatus.failed], [tr('taskCenter.queueDepth'), summary.queue_depth ?? summary.queued ?? summary.queue ?? jobs.queue_depth], [tr('taskCenter.p95Latency'), summary.p95_latency_ms ?? summary.p95 ?? summary.latency_p95_ms]];
    q('#overviewSummary').innerHTML = items.map(([label, value]) => `<div class="summary-item"><span>${esc(label)}</span><strong>${esc(formatNumber(value, label === tr('taskCenter.p95Latency') && typeof value === 'number' ? ' ms' : ''))}</strong><small>${esc(value === undefined || value === null ? tr('taskCenter.noData') : tr('taskCenter.fromObservability'))}</small></div>`).join('');
  }

  function seriesPoints(data, metric) {
    const source = data?.[metric] || data?.series?.[metric] || data?.data?.[metric] || (Array.isArray(data?.series) ? data.series : Array.isArray(data?.data) ? data.data.filter(item => !metric || item.metric === metric || (metric === 'latency' && /duration_ms$/.test(String(item.metric || '')))) : []);
    return (Array.isArray(source) ? source : []).map(item => {
      if (Array.isArray(item)) return {timestamp: item[0], value: Number(item[1])};
      return {timestamp: item.timestamp || item.time || item.ts, value: Number(item.value ?? item.v ?? item[metric])};
    }).filter(item => Number.isFinite(item.value));
  }

  function drawSeries(selector, points, metric) {
    const node = q(selector);
    if (!node) return;
    if (!points.length) { node.innerHTML = dataGap(tr('taskCenter.noSeries')); return; }
    const values = points.map(item => item.value);
    const min = Math.min(...values); const max = Math.max(...values); const span = max - min || 1;
    const width = 520; const height = 150; const pad = 12;
    const coords = points.map((item, index) => `${pad + (index / Math.max(1, points.length - 1)) * (width - pad * 2)},${height - pad - ((item.value - min) / span) * (height - pad * 2)}`).join(' ');
    const label = metric === 'latency' ? tr('taskCenter.latencySubtitle') : tr('taskCenter.throughputSubtitle');
    node.innerHTML = `<svg class="series-svg" viewBox="0 0 ${width} ${height}" role="img" aria-label="${esc(label)}"><line x1="12" y1="138" x2="508" y2="138"></line><polyline points="${coords}"></polyline></svg><div class="series-caption"><span>${esc(formatNumber(min))}</span><span>${esc(formatNumber(max))}</span></div>`;
  }

  function renderSeries() {
    const throughput = seriesPoints(state.series, 'asset_response_bytes');
    const latency = seriesPoints(state.series, 'asset_search_duration_ms');
    const latencyAlt = latency.length ? latency : seriesPoints(state.series, 'latency');
    const latest = values => values.length ? values[values.length - 1].value : undefined;
    setText('#throughputValue', formatNumber(latest(throughput)));
    setText('#latencyValue', formatDuration(latest(latencyAlt)));
    setText('#metricThroughputValue', formatNumber(latest(throughput)));
    setText('#metricLatencyValue', formatDuration(latest(latencyAlt)));
    drawSeries('#throughputChart', throughput, 'throughput'); drawSeries('#metricThroughputChart', throughput, 'throughput');
    drawSeries('#latencyChart', latencyAlt, 'latency'); drawSeries('#metricLatencyChart', latencyAlt, 'latency');
    const gap = state.series?.data_gap || state.series?.gap || (!throughput.length && !latencyAlt.length ? tr('taskCenter.noSeries') : '');
    const availability = q('#metricAvailability');
    if (availability) availability.innerHTML = gap ? dataGap(gap) : `<div class="availability-ok"><i data-lucide="check-circle-2" aria-hidden="true"></i>${esc(tr('taskCenter.seriesAvailable'))}</div>`;
  }

  function formatBytes(value) {
    const bytes = Number(value);
    if (!Number.isFinite(bytes) || bytes < 0) return '—';
    if (bytes === 0) return '0 B';
    const units = ['B', 'KiB', 'MiB', 'GiB', 'TiB'];
    const exponent = Math.min(units.length - 1, Math.floor(Math.log(bytes) / Math.log(1024)));
    const amount = bytes / (1024 ** exponent);
    const precision = amount >= 100 ? 0 : amount >= 10 ? 1 : 2;
    const formatted = String(Number(amount.toFixed(precision)));
    return `${formatted} ${units[exponent]}`;
  }

  function assetVolumeRecord(item) {
    const type = assetVolumeType(item);
    const size = Number(item.size_bytes_total ?? item.size_bytes ?? item.size);
    const count = value => { const parsed = Number(value); return Number.isFinite(parsed) && parsed >= 0 ? Math.floor(parsed) : 0; };
    const positions = count(item.positions_count ?? item.available_location_count);
    const versions = count(item.versions_count);
    const imageVersions = count(item.image_versions_count);
    const proxies = count(item.proxy_records_count);
    const total = item.data_items_total === undefined || item.data_items_total === null ? positions + versions + imageVersions + proxies : count(item.data_items_total);
    return {
      ...item,
      asset_type: type,
      kind: type,
      mime_type: cleanId(item.mime_type),
      size_bytes: Number.isFinite(size) && size >= 0 ? size : 0,
      available_location_count: positions,
      positions_count: positions,
      versions_count: versions,
      image_versions_count: imageVersions,
      proxy_records_count: proxies,
      data_items_total: total,
      asset_count: count(item.asset_count ?? item.assets_count ?? item.count),
    };
  }

  function renderAssetVolumes() {
    const node = q('#assetVolumeChart');
    if (!node) return;
    node.setAttribute('aria-busy', String(state.assetVolumeLoading));
    const items = state.assetVolumes.map(assetVolumeRecord).filter(item => item.asset_type).sort((left, right) => {
      const totalDifference = right.data_items_total - left.data_items_total;
      if (totalDifference) return totalDifference;
      if (left.asset_type === right.asset_type) return 0;
      return left.asset_type < right.asset_type ? 1 : -1;
    });
    if (!items.length) {
      const busy = state.assetVolumeLoading;
      node.innerHTML = assetVolumeStateRow(state.assetVolumeError ? tr('taskCenter.assetVolumesUnavailable') : busy ? tr('taskCenter.refreshing') : tr('taskCenter.noAssetVolumes'));
      setText('#assetVolumeCount', '—');
      setText('#assetVolumeCursorState', '');
      const loadMore = q('#loadMoreAssetVolumes');
      if (loadMore) {
        loadMore.disabled = true;
        loadMore.setAttribute('aria-busy', String(state.assetVolumeLoading));
      }
      return;
    }
    const maximum = Math.max(0, ...items.map(item => item.data_items_total));
    const colorMap = assetVolumeColorMap(items.map(item => item.asset_type));
    node.innerHTML = items.map(item => {
      const width = maximum > 0 ? (item.data_items_total / maximum) * 100 : 0;
      const locationCount = Math.max(0, Math.round(item.available_location_count));
      const breakdown = trf('taskCenter.assetDataBreakdown', {positions: item.positions_count, versions: item.versions_count, images: item.image_versions_count, proxies: item.proxy_records_count});
      const type = assetVolumeTypeLabel(item.asset_type);
      const details = `${trf('taskCenter.assetVolumeLocations', {count: locationCount})} · ${breakdown}`;
      const label = trf('taskCenter.assetVolumeAccessibleLabel', {type, count: item.data_items_total, assets: item.asset_count, details});
      const barLabel = trf('taskCenter.assetVolumeBarLabel', {type, count: item.data_items_total, percent: progressPercentText(width)});
      return `<div class="asset-volume-row type-${esc(cssToken(item.asset_type))}"${assetVolumeColorStyle(item.asset_type, colorMap)} role="row" aria-label="${esc(label)}"><span class="asset-volume-head" role="cell"><span class="asset-type-swatch" aria-hidden="true"></span><strong>${esc(type)}</strong><span class="asset-volume-asset-count">${esc(trf('taskCenter.assetVolumeAssets', {count: item.asset_count}))}</span></span><span class="asset-volume-track" role="cell" tabindex="0" aria-roledescription="${esc(tr('taskCenter.assetVolumeBarAccessibleLabel'))}" aria-label="${esc(barLabel)}"><span class="asset-volume-fill" aria-hidden="true" style="width:${width}%"></span></span><span class="asset-volume-meta" role="cell"><span>${esc(details)}</span><strong class="asset-volume-value">${esc(trf('taskCenter.assetDataItems', {count: item.data_items_total}))}</strong></span></div>`;
    }).join('');
    setText('#assetVolumeCount', `${items.length}${state.assetVolumeHasMore ? '+' : ''}`);
    setText('#assetVolumeCursorState', state.assetVolumeNextCursor ? tr('taskCenter.moreAssetsAvailable') : '');
    const loadMore = q('#loadMoreAssetVolumes');
    if (loadMore) {
      loadMore.disabled = !state.assetVolumeHasMore || state.assetVolumeLoading;
      loadMore.setAttribute('aria-busy', String(state.assetVolumeLoading));
    }
    setupAssetVolumeInfiniteScroll();
  }

  let assetVolumeObserver = null;
  function setupAssetVolumeInfiniteScroll() {
    const sentinel = q('#assetVolumeSentinel');
    if (!sentinel || typeof IntersectionObserver === 'undefined') return;
    if (assetVolumeObserver) assetVolumeObserver.disconnect();
    assetVolumeObserver = new IntersectionObserver((entries) => {
      const entry = entries[0];
      if (entry && entry.isIntersecting) {
        if (state.assetVolumeHasMore && state.assetVolumeNextCursor && !state.assetVolumeLoading && !state.loading) {
          loadMoreAssetVolumes();
        }
      }
    }, {
      root: q('#observabilityContent') || null,
      rootMargin: '160px',
      threshold: 0,
    });
    assetVolumeObserver.observe(sentinel);
  }

  function eventRecord(item) {
    const id = cleanId(item.event_id || item.id);
    const displayName = eventDisplayName(item);
    const candidateMessage = cleanId(item.message_safe || item.message);
    const messageSafe = candidateMessage && !isMachineEventName(candidateMessage) ? candidateMessage : displayName;
    return {...item, event_id: id, id, timestamp: item.timestamp || item.created_at || item.time, level: String(item.level || item.severity || 'info').toLowerCase(), status: String(item.status || 'succeeded'), source: cleanId(item.source || item.origin || '—'), message_safe: messageSafe};
  }

  function latestTaskEvent(task) {
    const jobId = cleanId(task?.id || task?.job_id);
    if (!jobId) return null;
    return state.events.map(eventRecord).filter(event => cleanId(event.job_id) === jobId)
      .sort((left, right) => timestampMs(right.timestamp) - timestampMs(left.timestamp))[0] || null;
  }

  function taskActivityText(task) {
    const updatedMs = timestampMs(task?.stall_activity_at || task?.last_event_at || task?.heartbeat_at || taskLastUpdatedMs(task));
    const updated = updatedMs ? dateTime(updatedMs) : taskUiText('unknownTime');
    const event = latestTaskEvent(task);
    const projectedEventAt = timestampMs(task?.last_event_at);
    const projectedEventType = cleanId(task?.last_event_type || '');
    const lastEvent = event
      ? `${eventDisplayName(event)} · ${dateTime(event.timestamp)}`
      : projectedEventAt
        ? `${projectedEventType || taskUiText('lastEventUnknown')} · ${dateTime(projectedEventAt)}`
        : taskUiText('noRecentEvent');
    return `${taskUiText('updatedAt', {value: updated})} · ${taskUiText('lastEvent', {value: lastEvent})}`;
  }

  function renderTaskLifecycleSignal(task) {
    const signal = taskLifecycleSignal(task);
    if (!signal) return '';
    const icon = signal.kind === 'historical' ? 'archive' : signal.kind === 'unknown' ? 'circle-help' : 'triangle-alert';
    const label = `${signal.label} · ${signal.ageLabel}`;
    return `<span class="task-lifecycle-signal ${esc(cssToken(signal.kind))}" role="status" title="${esc(signal.message)}" aria-label="${esc(signal.message)}"><i data-lucide="${icon}" aria-hidden="true"></i><span>${esc(label)}</span></span>`;
  }

  function renderTaskStatus(task) {
    return `<div class="task-status-stack">${renderStatus(task.status)}${renderTaskLifecycleSignal(task)}</div>`;
  }

  function eventRow(item) {
    const event = eventRecord(item); const related = event.job_id || event.asset_id || event.project_id || event.entity_id;
    return `<button class="event-row ${statusClass(event.level)}" type="button" data-open-event="${esc(event.event_id)}"${event.job_id ? ` data-open-task="${esc(event.job_id)}"` : ''}><span class="event-level">${esc(event.level)}</span><span class="event-main"><strong>${esc(eventDisplayName(event))}</strong><small>${esc(event.message_safe)} · ${esc(event.source)}${related ? ` · ${esc(related)}` : ''}</small></span><time>${esc(dateTime(event.timestamp))}</time><i class="status-dot ${statusClass(event.level)}" aria-hidden="true"></i></button>`;
  }

  function renderEvents() {
    const events = state.events.map(eventRecord).filter(event => {
      const deepLinkMatch = Boolean(state.eventId && event.event_id === state.eventId);
      const stableMatch = !state.stableId || [event.event_id, event.job_id, event.project_id, event.entity_id, event.canvas_id, event.asset_id].map(cleanId).includes(state.stableId) || (event.links || []).some(link => cleanId(link.stable_id) === state.stableId);
      const entry = event; const statusMatch = statusMatches(state.logStatus, entry.status);
      return deepLinkMatch || (stableMatch && matchesContext(event) && (state.source === 'all' || event.source === state.source) && (state.level === 'all' || event.level === state.level.toLowerCase()) && statusMatch);
    });
    const html = events.length ? events.map(item => eventRow(item)).join('') : dataGap(state.loadError ? tr('taskCenter.eventsUnavailable') : tr('taskCenter.emptyLogs'));
    q('#logRows').innerHTML = html; q('#overviewEvents').innerHTML = events.length ? events.slice(0, 8).map(item => eventRow(item, true)).join('') : dataGap(tr('taskCenter.noData'));
    setText('#logCount', events.length ? `${events.length}${state.hasMore ? '+' : ''}` : '—');
    setText('#cursorState', state.nextCursor ? tr('taskCenter.moreAvailable') : '');
    const loadMore = q('#loadMoreEvents'); if (loadMore) loadMore.disabled = !state.hasMore || state.loading;
  }

  function renderHealth() {
    const items = state.health.length ? state.health : state.sources;
    const healthy = items.filter(item => ['ok', 'healthy', 'connected', 'available'].includes(String(item.status || item.health || '').toLowerCase())).length;
    setText('#sourceHealthCount', items.length ? `${healthy}/${items.length}` : '—');
    q('#healthList').innerHTML = items.length ? items.map(item => {
      const name = String(item.name || item.source || item.provider || tr('taskCenter.source'));
      const status = String(item.status || item.health || 'unknown').toLowerCase();
      const message = item.message_safe || item.message || item.reason || (status === 'unknown' ? tr('taskCenter.noHealthData') : status);
      return `<div class="health-row"><span class="health-mark ${cssToken(status)}"><i class="status-dot ${cssToken(status)}" aria-hidden="true"></i></span><span><strong>${esc(name)}</strong><small>${esc(message)}</small></span><code>${esc(status)}</code></div>`;
    }).join('') : dataGap(tr('taskCenter.noHealthData'));
  }

  function renderFailures() {
    const summary = firstObject(state.overview, ['summary', 'overview']);
    const failures = firstArray(state.overview, ['failures', 'active_failures', 'failed_tasks']).concat(state.tasks.filter(item => statusMatches('failed', item.status))).slice(0, 8);
    q('#failureList').innerHTML = failures.length ? failures.map(item => { const task = persistentTask(item); return `<button class="failure-row" type="button" data-open-task="${esc(task.id)}"><span>${renderStatus(task.status)}</span><span><strong>${esc(task.title)}</strong><small>${esc(task.id)}</small></span><i data-lucide="arrow-up-right" aria-hidden="true"></i></button>`; }).join('') : dataGap(summary.failed ? tr('taskCenter.failuresUnavailable') : tr('taskCenter.noFailures'));
  }

  function knownTasks() {
    const byId = new Map();
    [state.tasks, state.stalledTasks, state.longTasks].forEach(items => (items || []).forEach(item => {
      const task = persistentTask(item);
      if (task.id && !byId.has(task.id)) byId.set(task.id, task);
    }));
    return [...byId.values()];
  }

  function taskRow(item) {
    const task = persistentTask(item);
    return `<tr><td class="task-title"><button class="task-detail-trigger" type="button" data-open-task="${esc(task.id)}" title="${esc(tr('taskCenter.openDetails'))}"><strong>${esc(task.summary || task.title)}</strong><span>${esc(task.source || tr('taskCenter.observabilitySource'))} · ${esc(task.id)}</span></button></td><td>${esc(tr(TYPE_KEYS[task.type] || 'taskCenter.system'))}</td><td>${renderTaskStatus(task)}</td><td>${renderTaskProgress(task)}</td><td>${esc(taskActivityText(task))}</td><td><button class="btn small" type="button" data-open-task="${esc(task.id)}">${esc(tr('taskCenter.openDetails'))}</button></td></tr>`;
  }

  function renderTasks() {
    const pool = knownTasks();
    const filtered = pool.filter(item => { const task = persistentTask(item); const deepLinkMatch = Boolean(state.jobId && task.id === state.jobId); const statusMatch = statusMatches(state.status, task.status); const idMatch = !state.stableId || [task.id, task.project_id, task.entity_id, task.asset_id, task.canvas_id].map(cleanId).includes(state.stableId); return deepLinkMatch || (matchesContext(task) && statusMatch && idMatch && (!state.source || state.source === 'all' || task.source === state.source)); });
    setText('#taskCount', filtered.length ? `${filtered.length}${filtered.length !== pool.length ? ` / ${pool.length}` : ''}` : '—');
    q('#taskRows').innerHTML = filtered.length ? filtered.map(taskRow).join('') : dataGap(state.loadError ? tr('taskCenter.tasksUnavailable') : tr('taskCenter.emptyTasks'));
    const loadMore = q('#loadMoreTasks'); if (loadMore) loadMore.disabled = !state.taskHasMore || state.loading;
    setText('#taskCursorState', state.taskNextCursor ? tr('taskCenter.moreAvailable') : '');
  }

  function renderLongTaskProgress() {
    const node = q('#longTaskProgress'); if (!node) return;
    node.setAttribute('aria-busy', String(state.longTaskLoading));
    const tasks = state.longTasks.map(persistentTask).filter(task => task.id && longTaskKind(task.jobType) && longTaskStatusRank(task.status) !== undefined).sort((a, b) => {
      const rankDifference = longTaskStatusRank(a.status) - longTaskStatusRank(b.status);
      if (rankDifference) return rankDifference;
      return String(b.updated_at || '').localeCompare(String(a.updated_at || ''));
    });
    setText('#longTaskCount', tasks.length ? trf('taskCenter.longTaskCount', {count: tasks.length}) : '—');
    const rows = tasks.slice(0, 12);
    if (!rows.length) { node.innerHTML = dataGap(state.longTasksError ? tr('taskCenter.longTasksUnavailable') : tr('taskCenter.noLongTasks')); return; }
    const hidden = Math.max(0, tasks.length - rows.length);
    node.innerHTML = `<ol class="longtask-list">${rows.map(task => { const kind = longTaskKind(task.jobType); return `<li><div class="longtask-row"><button class="longtask-trigger" type="button" data-open-task="${esc(task.id)}"><span class="longtask-kind ${esc(kind)}">${esc(tr(LONG_TASK_KEYS[kind]))}</span><span class="longtask-status">${renderTaskStatus(task)}</span><span class="longtask-main"><strong>${esc(task.summary || task.title)}</strong><small>${esc(task.id)} · ${esc(taskActivityText(task))}</small></span></button>${renderTaskProgress(task)}</div></li>`; }).join('')}</ol>${hidden ? `<p class="longtask-more">${esc(trf('taskCenter.longTasksMore', {count: hidden}))}</p>` : ''}`;
  }

  function renderTaskStallAlerts() {
    const panel = q('#taskStallAlertPanel');
    const list = q('#taskStallAlerts');
    if (!panel || !list) return;
    const candidates = state.stalledTasks.map(persistentTask)
      .map(task => ({task, signal: taskLifecycleSignal(task)}))
      .filter(item => item.signal?.stale)
      .sort((left, right) => {
        const historicalDifference = Number(right.signal.kind === 'historical') - Number(left.signal.kind === 'historical');
        if (historicalDifference) return historicalDifference;
        return (right.signal.ageMs || 0) - (left.signal.ageMs || 0);
      });
    const dataState = state.stalledTasksDataStatus;
    const hasNotice = dataState !== 'ok';
    panel.hidden = !candidates.length && !hasNotice;
    if (panel.hidden) return;
    setText('#taskStallAlertHeading', taskUiText('stalledHeading'));
    setText('#taskStallAlertCount', candidates.length ? taskUiText('stalledCount', {count: candidates.length}) : '—');
    const notice = hasNotice ? `<p class="task-stall-degraded" role="status"><i data-lucide="triangle-alert" aria-hidden="true"></i>${esc(dataState === 'unavailable' ? taskUiText('unavailable') : taskUiText('degraded'))}</p>` : '';
    if (!candidates.length) {
      list.innerHTML = `${notice}<div class="data-gap">${esc(taskUiText('noStalled'))}</div>`;
      return;
    }
    list.innerHTML = `${notice}<ul class="task-stall-alert-list">${candidates.slice(0, 20).map(({task, signal}) => {
      const label = `${task.summary || task.title || task.id} · ${signal.message}`;
      return `<li class="task-stall-alert ${esc(cssToken(signal.kind))}"><button type="button" data-open-task="${esc(task.id)}" aria-label="${esc(label)}"><span class="task-stall-alert-label"><i data-lucide="${signal.kind === 'historical' ? 'archive' : signal.kind === 'unknown' ? 'circle-help' : 'triangle-alert'}" aria-hidden="true"></i>${esc(signal.label)}</span><span class="task-stall-alert-status">${renderStatus(task.status)}</span><span class="task-stall-alert-main"><strong>${esc(task.summary || task.title || task.id)}</strong><small>${esc(task.id)} · ${esc(taskActivityText(task))}</small><span>${esc(signal.message)}</span></span><i data-lucide="arrow-up-right" aria-hidden="true"></i></button></li>`;
    }).join('')}</ul>${candidates.length > 20 ? `<p class="longtask-more">${esc(taskUiText('stalledCount', {count: candidates.length - 20}))}</p>` : ''}`;
    if (window.lucide?.createIcons) window.lucide.createIcons({attrs: {'aria-hidden': 'true'}});
  }

  function render() {
    renderContext(); renderNotice(); renderSummary(); renderSeries(); renderLongTaskProgress(); renderTaskStallAlerts(); renderAssetVolumes(); renderEvents(); renderHealth(); renderFailures(); renderTasks();
    const stamp = state.lastUpdated ? dateTime(state.lastUpdated) : tr('taskCenter.waitingForData');
    setText('#refreshState', state.loading ? tr('taskCenter.refreshing') : `${tr('taskCenter.updatedAt')} ${stamp}`);
    setText('#overviewUpdated', stamp); setText('#metricsUpdated', stamp);
    document.querySelectorAll('[data-view-panel]').forEach(panel => { const active = panel.dataset.viewPanel === state.view; panel.hidden = !active; panel.classList.toggle('active', active); panel.setAttribute('aria-hidden', String(!active)); });
    document.querySelectorAll('[data-view]').forEach(tab => { const active = tab.dataset.view === state.view; tab.classList.toggle('active', active); tab.setAttribute('role', 'tab'); tab.setAttribute('aria-selected', String(active)); tab.setAttribute('tabindex', active ? '0' : '-1'); });
    const viewNav = q('#observationViews'); if (viewNav) viewNav.setAttribute('aria-label', tr('taskCenter.viewNavigation'));
    [['#throughputChart', 'taskCenter.responseBytes'], ['#metricThroughputChart', 'taskCenter.responseBytes'], ['#latencyChart', 'taskCenter.latencySample'], ['#metricLatencyChart', 'taskCenter.latencySample'], ['#longTaskProgress', 'taskCenter.longTasks']].forEach(([selector, key]) => { const chart = q(selector); if (chart) chart.setAttribute('aria-label', tr(key)); });
    const assetVolumeTable = q('#assetVolumeTable'); if (assetVolumeTable) assetVolumeTable.setAttribute('aria-label', tr('taskCenter.assetVolumes'));
    if (window.lucide?.createIcons) window.lucide.createIcons({attrs: {'aria-hidden': 'true'}});
    scheduleTaskTouchbarReport();
  }

  function showToast(message, kind = 'success') {
    const toast = q('#taskToast'); if (!toast) return;
    clearTimeout(state.toastTimer); toast.textContent = String(message || tr('taskCenter.actionAccepted')); toast.className = `production-toast visible ${kind}`;
    state.toastTimer = setTimeout(() => { toast.className = 'production-toast'; }, 4000);
  }

  function detailTask() { return knownTasks().find(task => task.id === state.selectedJobId) || null; }
  function detailRows(job) {
    const rows = [[tr('taskCenter.taskId'), job.job_id], [tr('taskCenter.type'), job.job_type || detailTask()?.title || ''], [tr('taskCenter.status'), statusText(job.status)], [tr('taskCenter.attempt'), `${Number(job.attempt || 1)} / ${Number(job.max_attempts || 1)}`], [tr('taskCenter.created'), dateTime(job.created_at)], [tr('taskCenter.updated'), dateTime(job.updated_at)], [tr('taskCenter.started'), dateTime(job.started_at)], [tr('taskCenter.finished'), dateTime(job.finished_at)]];
    if (job.root_job_id) rows.push([tr('taskCenter.rootTask'), job.root_job_id]); if (job.retry_of_job_id) rows.push([tr('taskCenter.retryOf'), job.retry_of_job_id]);
    return `<dl class="task-detail-grid">${rows.filter(([, value]) => value && value !== '—').map(([label, value]) => `<div><dt>${esc(label)}</dt><dd>${esc(value)}</dd></div>`).join('')}</dl>`;
  }

  function relatedRecords(job) {
    const context = job.production_context || {}; const records = [];
    const add = (kind, id, label) => { const value = cleanId(id); if (value && !records.some(record => record.kind === kind && record.id === value)) records.push({kind, id: value, label}); };
    add('project', context.project_id || job.project_id, tr('taskCenter.project')); add('entity', context.entity_id || job.entity_id, tr('taskCenter.entity')); add('asset', context.asset_id || job.asset_id, tr('taskCenter.asset')); add('canvas', context.canvas_id || job.canvas_id, tr('taskCenter.canvas'));
    (job.links || []).forEach(link => { const type = String(link.entity_type || '').toLowerCase(); const label = type.includes('asset') ? (link.relation === 'output' ? tr('taskCenter.resultAsset') : link.relation === 'input' ? tr('taskCenter.sourceAsset') : tr('taskCenter.asset')) : type.includes('project') ? tr('taskCenter.project') : type.includes('canvas') ? tr('taskCenter.canvas') : tr('taskCenter.entity'); add(type.includes('asset') ? 'asset' : type.includes('project') ? 'project' : type.includes('canvas') ? 'canvas' : 'entity', link.entity_id, label); });
    return records;
  }

  function renderRelated(job) {
    const records = relatedRecords(job); if (!records.length) return `<p class="task-detail-empty">${esc(tr('taskCenter.noRelated'))}</p>`;
    return `<div class="task-related-list">${records.map(record => `<button class="task-related-link" type="button" data-open-related="${esc(record.kind)}" data-related-id="${esc(record.id)}"><span>${esc(record.label)}</span><code>${esc(record.id)}</code></button>`).join('')}</div>`;
  }

  function renderEventsDetail(job) {
    const events = Array.isArray(job.events) ? job.events : []; return `<section class="task-detail-section"><h3>${esc(tr('taskCenter.events'))}</h3>${events.length ? `<ol class="task-event-list">${events.map(event => `<li>${renderStatus(event.status)}<strong>${esc(eventDisplayName(event))}</strong><time>${esc(dateTime(event.created_at || event.timestamp))}</time></li>`).join('')}</ol>` : `<p class="task-detail-empty">${esc(tr('taskCenter.noEvents'))}</p>`}</section>`;
  }

  function renderActions() {
    const actions = state.detailActions || {}; const reasons = Object.entries(state.detailActionReasons || {}).filter(([action, reason]) => !actions[action] && String(reason || '').trim()).map(([, reason]) => `<p class="action-capability-note">${esc(String(reason))}</p>`).join('');
    if (!canEdit()) return '';
    const disabled = state.actionBusy ? ' disabled aria-disabled="true"' : '';
    if (state.pendingAction === 'cancel') return `<section class="task-inline-confirm"><strong>${esc(tr('taskCenter.cancelConfirm'))}</strong><p>${esc(tr('taskCenter.cancelHint'))}</p><div><button class="btn" type="button" data-clear-task-confirm${disabled}>${esc(tr('taskCenter.keepTask'))}</button><button class="btn danger" type="button" data-run-task-action="cancel"${disabled}>${esc(tr('taskCenter.confirmCancel'))}</button></div></section>`;
    const buttons = `${actions.pause ? `<button class="btn" type="button" data-run-task-action="pause"${disabled}>${locale() === 'en-US' ? 'Pause' : '暂停任务'}</button>` : ''}${actions.cancel ? `<button class="btn danger" type="button" data-confirm-task-action="cancel"${disabled}>${esc(tr('taskCenter.cancelTask'))}</button>` : ''}${actions.retry ? `<button class="btn" type="button" data-run-task-action="retry"${disabled}>${esc(tr('taskCenter.retryTask'))}</button>` : ''}${actions.resume ? `<button class="btn primary" type="button" data-run-task-action="resume"${disabled}>${esc(tr('taskCenter.resumeTask'))}</button>` : ''}`;
    return `${buttons ? `<div class="task-detail-actions">${buttons}</div>` : ''}${reasons}`;
  }

  function renderDetail() {
    scheduleTaskTouchbarReport();
    const body = q('#taskDetailBody'); const task = detailTask(); if (!body || !task) return; setText('#taskDetailSubtitle', task.id);
    if (state.detailLoading) { body.innerHTML = `<div class="task-detail-loading skeleton">${esc(tr('taskCenter.loadingDetail'))}</div>`; return; }
    if (state.detailError) { body.innerHTML = `<div class="task-detail-error"><strong>${esc(tr('taskCenter.detailUnavailable'))}</strong><p>${esc(state.detailError)}</p>${detailRows(task)}</div>`; return; }
    if (!state.detail) { body.innerHTML = `<div class="task-detail-readonly"><p>${esc(tr('taskCenter.readOnlyDetail'))}</p>${detailRows(task)}</div>`; return; }
    const job = state.detail; const progress = Object.entries(job.progress || {}).filter(([, value]) => ['number', 'boolean'].includes(typeof value)).slice(0, 8);
    body.innerHTML = `<div class="task-detail-status">${renderStatus(job.status)}<span>${esc(trf('taskCenter.taskTypeLabel', {type: job.job_type || task.title}))}</span></div>${detailRows(job)}${job.has_error ? `<p class="task-safe-error">${esc(tr('taskCenter.safeError'))} · ${esc(job.error_code || '')}</p>` : ''}${job.result ? `<section class="task-detail-section"><h3>实际执行结果</h3><pre>${esc(JSON.stringify(job.result, null, 2))}</pre></section>` : ''}${progress.length ? `<section class="task-detail-section"><h3>${esc(tr('taskCenter.progress'))}</h3><dl class="task-progress-list">${progress.map(([key, value]) => `<div><dt>${esc(key)}</dt><dd>${esc(String(value))}</dd></div>`).join('')}</dl></section>` : ''}<section class="task-detail-section"><h3>${esc(tr('taskCenter.related'))}</h3>${renderRelated(job)}</section>${renderEventsDetail(job)}${renderActions()}`;
    if (window.lucide?.createIcons) window.lucide.createIcons({attrs: {'aria-hidden': 'true'}});
  }

  async function openTaskDetail(jobId, trigger = null) {
    const id = cleanId(jobId); if (!id) return;
    if (!knownTasks().some(item => item.id === id)) {
      try {
        const data = await api(`${ENDPOINTS.tasks}?${observationQuery({limit: 1, job_id: id}, 'tasks')}`);
        state.tasks = state.tasks.concat(firstArray(data, ['items', 'tasks']).map(persistentTask).filter(item => item.id));
      } catch (error) {
        if (isPermissionError(error)) state.permissionDenied = true;
        showToast(isPermissionError(error) ? tr('taskCenter.permissionDenied') : tr('taskCenter.deepLinkNotFound'), 'error');
        render();
        return;
      }
    }
    if (!knownTasks().some(item => item.id === id)) { showToast(tr('taskCenter.deepLinkNotFound'), 'error'); return; }
    const fallbackTrigger = [...document.querySelectorAll('[data-open-task]')].find(element => element.dataset.openTask === id);
    state.detailTrigger = trigger instanceof HTMLElement ? trigger : (fallbackTrigger || null);
    state.selectedJobId = id; state.jobId = id; state.detail = null; state.detailActions = {}; state.detailActionReasons = {}; state.detailError = ''; state.pendingAction = '';
    syncUrl(); const dialog = q('#taskDetail'); if (!dialog.open) { dialog.showModal(); q('[data-close-task-detail]', dialog)?.focus(); }
    state.detailLoading = true; renderDetail();
    if (state.summaryOnly || ['reviewer'].includes(String(state.role || window.__taskCenterPrincipal?.role || ''))) {
      state.detailLoading = false;
      renderDetail();
      return;
    }
    try { const data = await api(`/api/asset-registry/workspace-jobs/${encodeURIComponent(id)}`); if (state.selectedJobId !== id) return; state.detail = data.job || null; state.detailActions = data.actions || {}; state.detailActionReasons = data.action_reasons || {}; }
    catch (error) { if (state.selectedJobId === id) state.detailError = error.message || tr('taskCenter.detailUnavailable'); }
    finally { if (state.selectedJobId === id) { state.detailLoading = false; renderDetail(); } }
  }

  function closeTaskDetail() {
    const selectedJobId = state.selectedJobId;
    const trigger = state.detailTrigger;
    const fallback = selectedJobId ? [...document.querySelectorAll('[data-open-task]')].find(element => element.dataset.openTask === selectedJobId) : null;
    state.selectedJobId = ''; state.jobId = ''; state.detail = null; state.detailActions = {}; state.detailActionReasons = {}; state.pendingAction = ''; state.detailTrigger = null;
    const dialog = q('#taskDetail'); if (dialog.open) dialog.close(); syncUrl();
    const restore = trigger?.isConnected ? trigger : fallback;
    if (restore && !restore.disabled && restore.getClientRects().length) restore.focus();
    scheduleTaskTouchbarReport();
  }

  async function runTaskAction(action) {
    const job = state.detail; if (!job || !state.detailActions?.[action] || state.actionBusy) return; state.actionBusy = action; renderDetail();
    try { const options = {method: 'POST', headers: {'Content-Type': 'application/json'}}; options.body = JSON.stringify({expected_version: Number(job.version || 0)}); const data = await api(`/api/asset-registry/workspace-jobs/${encodeURIComponent(job.job_id)}/${action}`, options); state.pendingAction = ''; showToast(tr('taskCenter.actionAccepted')); await load(); await openTaskDetail(String(data.job?.job_id || job.job_id)); }
    catch (error) { showToast(error.message || tr('taskCenter.actionFailed'), 'error'); }
    finally { state.actionBusy = ''; if (q('#taskDetail')?.open) renderDetail(); }
  }

  function stableAssetId(value) {
    const assetId = String(value || '').trim();
    return /^[A-Za-z0-9][A-Za-z0-9_.:@-]{0,191}$/.test(assetId) ? assetId : '';
  }
  function openAsset(rawAssetId) {
    const assetId = stableAssetId(rawAssetId);
    if (!assetId) return;
    if (window.parent !== window) {
      window.parent.postMessage({type: 'gw-workspace-open-asset', version: 1, asset_id: assetId}, location.origin);
      return;
    }
    navigate('asset-manager', {asset_id: assetId});
  }
  function openRelated(kind, id) { const value = cleanId(id); if (kind === 'asset') return openAsset(value); if (kind === 'project') return navigate('project-board', {project_id: value}); if (kind === 'canvas') return navigate('canvas', {canvas_id: value, project_id: state.projectId, entity_id: state.entityId}); if (kind === 'entity') { const projectId = cleanId(state.detail?.production_context?.project_id || state.projectId); if (projectId) return navigate('project-board', {project_id: projectId, entity_id: value}); } }

  async function loadPrincipal() { try { const data = await api('/api/asset-auth/status'); window.__taskCenterPrincipal = data.principal || null; state.role = cleanId(data.principal?.role); } catch (_) { window.__taskCenterPrincipal = null; state.role = ''; } }

  function updateSources(data) {
    state.sources = firstArray(data, ['sources', 'items', 'data']); const select = q('#observationSource'); if (!select) return;
    const selected = state.source; select.innerHTML = `<option value="all">${esc(tr('taskCenter.allSources'))}</option>` + state.sources.map(item => { const value = cleanId(item.id || item.source || item.name); return value ? `<option value="${esc(value)}">${esc(item.name || item.source || value)}</option>` : ''; }).join(''); select.value = [...select.options].some(option => option.value === selected) ? selected : 'all'; if (select.value !== selected) state.source = select.value;
  }

  async function focusEventDeepLink(eventId) {
    const id = cleanId(eventId); if (!id) return;
    if (!state.events.some(item => eventRecord(item).event_id === id)) {
      try {
        const data = await api(`${ENDPOINTS.events}?${observationQuery({limit: 1, event_id: id}, 'event-deep-link')}`);
        state.events = state.events.concat(firstArray(data, ['items', 'events', 'data']).map(eventRecord));
      } catch (error) {
        if (isPermissionError(error)) state.permissionDenied = true;
        showToast(isPermissionError(error) ? tr('taskCenter.permissionDenied') : tr('taskCenter.deepLinkNotFound'), 'error');
        render();
        return;
      }
    }
    render();
    if (!document.querySelector(`[data-open-event="${CSS.escape(id)}"]`)) showToast(tr('taskCenter.deepLinkNotFound'), 'error');
    focusEvent(id);
  }

  async function load() {
    if (state.loading || !acquireDataRequest('refresh')) return;
    state.loading = true; state.assetVolumeLoading = true; state.longTaskLoading = true; state.loadError = ''; state.loadErrorKind = ''; state.assetVolumeError = ''; state.longTasksError = ''; state.stalledTasksError = ''; state.stalledTasksDataStatus = 'ok'; state.stalledTasks = []; state.assetVolumes = []; state.assetVolumeNextCursor = ''; state.assetVolumeHasMore = false; state.eventWindow = null; state.taskWindow = null; state.degraded = []; state.degradedKinds = []; state.permissionDenied = false; state.summaryOnly = state.role === 'reviewer'; render();
    const eventQuery = observationQuery(state.eventId ? {limit: 1, event_id: state.eventId} : {limit: 50}, 'events'); const taskQuery = observationQuery(state.jobId ? {limit: 1, job_id: state.jobId} : {limit: 50}, 'tasks'); const assetVolumeQueryString = assetVolumeQuery(); const longRunningQuery = observationQuery({range: 'all', limit: '50', status: 'running', job_type: LONG_TASK_JOB_TYPES}, 'tasks'); const longQueuedQuery = observationQuery({range: 'all', limit: '50', status: 'queued', job_type: LONG_TASK_JOB_TYPES}, 'tasks'); const longSucceededQuery = observationQuery({range: 'all', limit: '50', status: 'succeeded', job_type: LONG_TASK_JOB_TYPES}, 'tasks'); const longFailedQuery = observationQuery({range: 'all', limit: '50', status: 'failed', job_type: LONG_TASK_JOB_TYPES}, 'tasks'); const longCanceledQuery = observationQuery({range: 'all', limit: '50', status: 'canceled', job_type: LONG_TASK_JOB_TYPES}, 'tasks'); const stalledQueuedQuery = observationQuery({range: 'all', limit: '100', stall_classification: ['queued', 'historical']}, 'tasks'); const stalledRecoveringQuery = observationQuery({range: 'all', limit: '100', stall_classification: ['recovering', 'historical']}, 'tasks');
    [longRunningQuery, longQueuedQuery, longSucceededQuery, longFailedQuery, longCanceledQuery].forEach(query => ['stable_id', 'project_id', 'entity_id', 'asset_id', 'canvas_id', 'job_id'].forEach(key => query.delete(key)));
    const failed = (result, label) => { if (result.status === 'rejected') { const itemKind = degradeKind(result.reason); if (itemKind) { pushDegraded(degradeLabel(result.reason, label), itemKind); } else if (isPermissionError(result.reason)) { if (['reviewer', 'editor'].includes(String(state.role || ''))) { if (state.role === 'reviewer') state.summaryOnly = true; const notice = state.role === 'reviewer' ? tr('taskCenter.summaryOnly') : tr('taskCenter.degradedData'); pushDegraded(`${label} · ${notice}`, ''); } else state.permissionDenied = true; } else pushDegraded(degradeLabel(result.reason, label), ''); } };
    try {
      const results = await Promise.allSettled([api(`${ENDPOINTS.overview}?${observationQuery()}`), api(`${ENDPOINTS.series}?${observationQuery({metrics: 'asset_response_bytes,asset_search_duration_ms'})}`), api(`${ENDPOINTS.events}?${eventQuery}`), api(`${ENDPOINTS.tasks}?${taskQuery}`), api(`${ENDPOINTS.health}?${observationQuery()}`), api(`${ENDPOINTS.sources}?${observationQuery()}`), api(`${ENDPOINTS.assetVolumes}?${assetVolumeQueryString}`), api(`${ENDPOINTS.tasks}?${longRunningQuery}`), api(`${ENDPOINTS.tasks}?${longQueuedQuery}`), api(`${ENDPOINTS.tasks}?${longSucceededQuery}`), api(`${ENDPOINTS.tasks}?${longFailedQuery}`), api(`${ENDPOINTS.tasks}?${longCanceledQuery}`), api(`${ENDPOINTS.tasks}?${stalledQueuedQuery}`), api(`${ENDPOINTS.tasks}?${stalledRecoveringQuery}`)]);
      const [overview, series, events, tasks, health, sources, assetVolumes, longRunning, longQueued, longSucceeded, longFailed, longCanceled, stalledQueued, stalledRecovering] = results;
      if (overview.status === 'fulfilled') { state.overview = overview.value; noteDataStatus(overview.value, tr('taskCenter.overviewUnavailable')); state.lastUpdated = overview.value.updated_at_ms || overview.value.generated_at_ms || overview.value.updated_at || overview.value.generated_at || overview.value.timestamp || new Date().toISOString(); } else failed(overview, tr('taskCenter.overviewUnavailable'));
      if (series.status === 'fulfilled') { state.series = series.value; noteDataStatus(series.value, tr('taskCenter.seriesUnavailable')); } else failed(series, tr('taskCenter.seriesUnavailable'));
      if (events.status === 'fulfilled') { state.events = firstArray(events.value, ['events', 'items', 'data']).map(eventRecord); state.eventWindow = responseWindow(events.value); noteDataStatus(events.value, tr('taskCenter.eventsUnavailable')); state.nextCursor = cleanId(events.value.next_cursor || events.value.cursor?.next || events.value.meta?.next_cursor); state.hasMore = Boolean(events.value.has_more || state.nextCursor); } else failed(events, tr('taskCenter.eventsUnavailable'));
      if (tasks.status === 'fulfilled') { state.tasks = firstArray(tasks.value, ['items', 'tasks']).map(persistentTask).filter(item => item.id); state.taskWindow = responseWindow(tasks.value); noteDataStatus(tasks.value, tr('taskCenter.tasksUnavailable')); state.taskNextCursor = cleanId(tasks.value.next_cursor || tasks.value.cursor?.next || tasks.value.meta?.next_cursor); state.taskHasMore = Boolean(tasks.value.has_more || state.taskNextCursor); } else failed(tasks, tr('taskCenter.tasksUnavailable'));
      // 后台索引是独立持久任务；发现源不能用观测日志代替。
      try {
        const indexData = await api('/api/asset-registry/status');
        const indexTasks = firstArray(indexData, ['jobs']).map(persistentTask);
        const byId = new Map(state.tasks.map(item => [item.job_id, item]));
        indexTasks.forEach(item => { if (!state.jobId || item.job_id === state.jobId) byId.set(item.job_id, item); });
        state.tasks = [...byId.values()];
      } catch (error) { pushDegraded('索引任务暂不可读取', error.message || ''); }
      const longTaskResults = [longRunning, longQueued, longSucceeded, longFailed, longCanceled];
      longTaskResults.forEach(result => failed(result, tr('taskCenter.longTasksUnavailable')));
      const longTaskDegraded = longTaskResults.some(result => result.status === 'fulfilled' && result.value?.data_status === 'degraded');
      const longTaskFailed = longTaskResults.some(result => result.status === 'rejected');
      if (longTaskDegraded) {
        const degradedResult = longTaskResults.find(result => result.status === 'fulfilled' && result.value?.data_status === 'degraded');
        noteDataStatus(degradedResult?.value, tr('taskCenter.longTasksUnavailable'));
      }
      if (longTaskResults.some(result => result.status === 'fulfilled')) {
        const mergedLongTasks = new Map();
        longTaskResults.forEach(result => { if (result.status !== 'fulfilled') return; firstArray(result.value, ['items', 'tasks']).forEach(item => { const task = persistentTask(item); if (task.id && longTaskKind(task.jobType)) mergedLongTasks.set(task.id, task); }); });
        state.longTasks = [...mergedLongTasks.values()];
        state.longTasksError = !state.longTasks.length && (longTaskDegraded || longTaskFailed) ? 'unavailable' : '';
      } else { state.longTasks = []; state.longTasksError = 'unavailable'; }
      const stalledTaskResults = [stalledQueued, stalledRecovering];
      stalledTaskResults.forEach(result => failed(result, taskUiText('stalledHeading')));
      const stalledTaskDegraded = stalledTaskResults.some(result => result.status === 'fulfilled' && result.value?.data_status === 'degraded');
      const stalledTaskFailed = stalledTaskResults.some(result => result.status === 'rejected');
      if (stalledTaskResults.some(result => result.status === 'fulfilled')) {
        const mergedStalledTasks = new Map();
        stalledTaskResults.forEach(result => {
          if (result.status !== 'fulfilled') return;
          firstArray(result.value, ['items', 'tasks']).forEach(item => {
            const task = persistentTask(item);
            const status = String(task.status || '').toLowerCase();
            if (task.id && ['queued', 'pending', 'recovering'].includes(status)) mergedStalledTasks.set(task.id, task);
          });
        });
        state.stalledTasks = [...mergedStalledTasks.values()];
        state.stalledTasksDataStatus = stalledTaskDegraded || stalledTaskFailed ? 'degraded' : 'ok';
        state.stalledTasksError = stalledTaskFailed && !state.stalledTasks.length ? 'unavailable' : '';
      } else {
        state.stalledTasks = [];
        state.stalledTasksDataStatus = 'unavailable';
        state.stalledTasksError = 'unavailable';
      }
      if (health.status === 'fulfilled') { state.health = firstArray(health.value, ['health', 'items', 'checks', 'data']); if (health.value.status === 'degraded') pushDegraded(`${tr('taskCenter.healthUnavailable')} · ${tr('taskCenter.degradedData')}`, ''); } else failed(health, tr('taskCenter.healthUnavailable'));
      if (sources.status === 'fulfilled') { updateSources(sources.value); if (firstArray(sources.value, ['sources', 'items']).some(item => item.status === 'degraded')) pushDegraded(`${tr('taskCenter.sourcesUnavailable')} · ${tr('taskCenter.degradedData')}`, ''); } else failed(sources, tr('taskCenter.sourcesUnavailable'));
      if (assetVolumes.status === 'fulfilled') {
        state.assetVolumes = firstArray(assetVolumes.value, ['items', 'assets', 'data']).map(assetVolumeRecord).filter(item => item.asset_type);
        state.assetVolumeNextCursor = cleanId(assetVolumes.value.next_cursor || assetVolumes.value.cursor?.next || assetVolumes.value.meta?.next_cursor);
        state.assetVolumeHasMore = Boolean(assetVolumes.value.has_more || state.assetVolumeNextCursor);
        state.assetVolumeError = assetVolumes.value.data_status === 'degraded' ? 'degraded' : '';
        noteDataStatus(assetVolumes.value, tr('taskCenter.assetVolumesUnavailable'));
      } else {
        state.assetVolumeError = assetVolumes.reason?.message || '';
        failed(assetVolumes, tr('taskCenter.assetVolumesUnavailable'));
      }
    } catch (error) { state.loadError = taskCenterDegradationNotice(error); }
    finally { const shouldReload = finishDataRequest('refresh'); state.loading = false; state.assetVolumeLoading = false; state.longTaskLoading = false; render(); runQueuedReload(shouldReload); if (state.deepLinkPending) { state.deepLinkPending = false; if (state.jobId) { state.view = 'tasks'; syncUrl(); render(); await openTaskDetail(state.jobId); } else if (state.eventId) { state.view = 'logs'; syncUrl(); render(); await focusEventDeepLink(state.eventId); } } }
  }

  async function loadMoreEvents() {
    if (!state.nextCursor || state.loading || !acquireDataRequest('events-page')) return; state.loading = true; render();
    try { const query = setQueryWindow(observationQuery({limit: 50, cursor: state.nextCursor}, 'events'), state.eventWindow); const data = await api(`${ENDPOINTS.events}?${query}`); state.events = state.events.concat(firstArray(data, ['events', 'items', 'data']).map(eventRecord)); state.eventWindow = responseWindow(data) || state.eventWindow; state.nextCursor = cleanId(data.next_cursor || data.cursor?.next || data.meta?.next_cursor); state.hasMore = Boolean(data.has_more || state.nextCursor); }
    catch (error) { if (isPermissionError(error)) state.permissionDenied = true; showToast(isPermissionError(error) ? tr('taskCenter.permissionDenied') : (error.message || tr('taskCenter.eventsUnavailable')), 'error'); }
    finally { const shouldReload = finishDataRequest('events-page'); state.loading = false; render(); runQueuedReload(shouldReload); }
  }

  async function loadMoreTasks() {
    if (!state.taskNextCursor || state.loading || !acquireDataRequest('tasks-page')) return; state.loading = true; render();
    try {
      const query = setQueryWindow(observationQuery({limit: 50, cursor: state.taskNextCursor}, 'tasks'), state.taskWindow);
      const data = await api(`${ENDPOINTS.tasks}?${query}`);
      state.tasks = state.tasks.concat(firstArray(data, ['items', 'tasks']).map(persistentTask).filter(item => item.id));
      state.taskWindow = responseWindow(data) || state.taskWindow;
      noteDataStatus(data, tr('taskCenter.tasksUnavailable'));
      state.taskNextCursor = cleanId(data.next_cursor || data.cursor?.next || data.meta?.next_cursor);
      state.taskHasMore = Boolean(data.has_more || state.taskNextCursor);
    } catch (error) { if (isPermissionError(error)) state.permissionDenied = true; showToast(isPermissionError(error) ? tr('taskCenter.permissionDenied') : (error.message || tr('taskCenter.tasksUnavailable')), 'error'); }
    finally { const shouldReload = finishDataRequest('tasks-page'); state.loading = false; render(); runQueuedReload(shouldReload); }
  }

  function focusEvent(eventId) { const node = document.querySelector(`[data-open-event="${CSS.escape(String(eventId))}"]`); if (node) { node.scrollIntoView({block: 'center'}); node.focus(); } }

  async function loadMoreAssetVolumes() {
    if (!state.assetVolumeNextCursor || state.assetVolumeLoading || state.loading || !acquireDataRequest('asset-volumes-page')) return;
    state.assetVolumeLoading = true; render();
    try {
      const data = await api(`${ENDPOINTS.assetVolumes}?${assetVolumeQuery({cursor: state.assetVolumeNextCursor})}`);
      state.assetVolumes = state.assetVolumes.concat(firstArray(data, ['items', 'assets', 'data']).map(assetVolumeRecord).filter(item => item.asset_type));
      state.assetVolumeError = data.data_status === 'degraded' ? 'degraded' : '';
      noteDataStatus(data, tr('taskCenter.assetVolumesUnavailable'));
      state.assetVolumeNextCursor = cleanId(data.next_cursor || data.cursor?.next || data.meta?.next_cursor);
      state.assetVolumeHasMore = Boolean(data.has_more || state.assetVolumeNextCursor);
    } catch (error) {
      showToast(error.message || tr('taskCenter.assetVolumesUnavailable'), 'error');
    } finally { const shouldReload = finishDataRequest('asset-volumes-page'); state.assetVolumeLoading = false; render(); runQueuedReload(shouldReload); }
  }

  let indexRequestKey = '';
  let indexRequestBusy = false;
  async function startIndexJob() {
    if (!canEdit() || indexRequestBusy) return;
    indexRequestBusy = true;
    const button = q('[data-start-index]');
    button.disabled = true;
    // 传输结果未知时保留同一个幂等键，下一次点击只回读原请求。
    if (!indexRequestKey) indexRequestKey = 'task-center-index:' + crypto.randomUUID();
    try {
      const data = await api('/api/asset-registry/reindex', {method: 'POST',
        headers: {'Content-Type': 'application/json', 'Idempotency-Key': indexRequestKey},
        body: JSON.stringify({background: true})});
      indexRequestKey = '';
      state.jobId = data.job_id;
      setView('tasks');
      await load();
      await openTaskDetail(data.job_id);
    } catch (error) { showToast(error.message || '索引提交结果未确认，请用同一按钮重试确认', 'error'); }
    finally { indexRequestBusy = false; button.disabled = false; }
  }

  function setView(view) { if (!['overview', 'tasks', 'logs', 'metrics'].includes(view)) return; state.view = view; syncUrl(); render(); }
  function resetAndLoad() { if (dataRequestOwner) { queuedReload = true; return; } queuedReload = false; resetPaginationState(); void load(); }

  async function handleClick(event) {
    const viewTab = event.target.closest('[data-view]'); if (viewTab) return setView(viewTab.dataset.view);
    const viewTarget = event.target.closest('[data-view-target]'); if (viewTarget) return setView(viewTarget.dataset.viewTarget);
    const target = event.target.closest('button,[data-nav]'); if (!target) return;
    if (target.matches('[data-refresh]')) return load();
    if (target.matches('[data-start-index]')) return startIndexJob();
    if (target.matches('[data-open-event]')) { const id = target.dataset.openEvent; state.eventId = id; state.view = 'logs'; syncUrl(); render(); return focusEventDeepLink(id); }
    if (target.matches('[data-open-task]')) return openTaskDetail(target.dataset.openTask, target);
    if (target.matches('[data-close-task-detail]')) return closeTaskDetail();
    if (target.matches('[data-confirm-task-action]')) { state.pendingAction = target.dataset.confirmTaskAction; return renderDetail(); }
    if (target.matches('[data-clear-task-confirm]')) { state.pendingAction = ''; return renderDetail(); }
    if (target.matches('[data-run-task-action]')) return runTaskAction(target.dataset.runTaskAction);
    if (target.matches('[data-open-related]')) return openRelated(target.dataset.openRelated, target.dataset.relatedId);
    if (target.matches('[data-nav]')) return navigate(target.dataset.nav);
  }

  document.addEventListener('click', event => { handleClick(event).catch(error => showToast(error.message || tr('taskCenter.actionFailed'), 'error')); });
  q('#observationViews').addEventListener('keydown', event => {
    if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
    const tabs = [...q('#observationViews').querySelectorAll('[data-view]')]; if (!tabs.length) return;
    const current = Math.max(0, tabs.indexOf(document.activeElement));
    const next = event.key === 'Home' ? 0 : event.key === 'End' ? tabs.length - 1 : (current + (event.key === 'ArrowRight' ? 1 : -1) + tabs.length) % tabs.length;
    event.preventDefault(); tabs[next].focus(); setView(tabs[next].dataset.view);
  });
  q('#observationRange').addEventListener('change', event => { state.range = event.target.value; syncUrl(); resetAndLoad(); });
  q('#observationSource').addEventListener('change', event => { state.source = event.target.value; syncUrl(); resetAndLoad(); });
  q('#observationLevel').addEventListener('change', event => { state.level = event.target.value; syncUrl(); resetAndLoad(); });
  q('#observationStatus').addEventListener('change', event => { state.status = event.target.value; state.logStatus = state.status; syncUrl(); resetAndLoad(); });
  q('#observationStableId').addEventListener('input', event => { state.stableId = event.target.value.trim(); syncUrl(); clearTimeout(state.stableIdTimer); state.stableIdTimer = setTimeout(resetAndLoad, 260); });
  q('#loadMoreEvents').addEventListener('click', loadMoreEvents);
  q('#loadMoreTasks').addEventListener('click', loadMoreTasks);
  q('#loadMoreAssetVolumes').addEventListener('click', loadMoreAssetVolumes);
  q('#taskDetail').addEventListener('close', () => { if (state.selectedJobId) closeTaskDetail(); });
  window.addEventListener('message', event => {
    if (event.origin && event.origin !== location.origin) return;
    if (event.data?.type === 'gw-touchbar-action') {
      if (event.origin !== location.origin || event.source !== window.parent || event.data.version !== 1) return;
      const jobId = String(state.selectedJobId || state.jobId || '').trim();
      if (!jobId) return;
      if (event.data.action_id === 'task.view') {
        openTaskDetail(jobId).catch(error => showToast(error.message || tr('taskCenter.actionFailed'), 'error'));
        return;
      }
      if (event.data.action_id === 'task.cancel' && canEdit() && state.detailActions?.cancel) {
        state.pendingAction = 'cancel';
        renderDetail();
        return;
      }
      if (event.data.action_id === 'task.retry' && canEdit() && state.detailActions?.retry) {
        runTaskAction('retry').catch(error => showToast(error.message || tr('taskCenter.actionFailed'), 'error'));
      }
      return;
    }
    if (event.data?.type === 'studio-lang' && window.StudioI18n) window.StudioI18n.set(event.data.lang || 'zh');
  });
  window.addEventListener('studio-lang-change', () => { render(); if (q('#taskDetail')?.open) renderDetail(); });
  window.addEventListener('studio-theme-change', event => Workspace.syncTheme?.(event.detail?.theme));
  window.addEventListener('storage', event => {
    if (event.key !== 'workspace_preferences' && event.key !== null) return;
    syncAutoRefresh({refreshNow: true});
  });
  document.addEventListener('visibilitychange', () => {
    if (document.hidden) { stopAutoRefresh(); return; }
    syncAutoRefresh();
    if (autoRefreshEnabled) void requestRefresh();
  });
  window.addEventListener('pageshow', event => { syncAutoRefresh(); if (event.persisted) void requestRefresh(); });
  window.addEventListener('pagehide', stopAutoRefresh);
  window.addEventListener('hashchange', () => {
    const hash = location.hash.slice(1);
    const link = parseDeepLinkHash(hash);
    if (!link) return;
    if (link.type === 'task') {
      state.jobId = link.id;
      state.view = 'tasks';
      syncUrl();
      render();
      openTaskDetail(link.id).catch(error => showToast(error.message || tr('taskCenter.actionFailed'), 'error'));
    } else if (link.type === 'event') {
      state.eventId = link.id;
      state.view = 'logs';
      syncUrl();
      render();
      focusEventDeepLink(link.id).catch(error => showToast(error.message || tr('taskCenter.actionFailed'), 'error'));
    } else if (link.type === 'asset') {
      state.stableId = link.id;
      syncUrl();
      resetAndLoad();
    }
  });

  q('#observationRange').value = state.range; q('#observationLevel').value = state.level; q('#observationStatus').value = state.status; q('#observationStableId').value = state.stableId;
  const contentEl = q('#observabilityContent');
  if (contentEl) {
    contentEl.addEventListener('scroll', () => {
      if (state.view !== 'overview') return;
      if (contentEl.scrollHeight - contentEl.scrollTop - contentEl.clientHeight < 240) {
        if (state.assetVolumeHasMore && state.assetVolumeNextCursor && !state.assetVolumeLoading && !state.loading) {
          loadMoreAssetVolumes();
        }
      }
    }, { passive: true });
  }
  (async function initialize(){ syncAutoRefresh(); await loadPrincipal(); q('[data-start-index]').hidden = !canEdit(); setView(state.view); await load(); })().catch(error => { state.loadError = taskCenterDegradationNotice(error); state.loading = false; render(); });
})();
