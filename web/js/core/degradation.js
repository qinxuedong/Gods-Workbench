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
 * Gods Workbench - 统一「无后端时显式降级」语义（经典脚本，非 ESM）
 *
 * 背景（用户 2026-09-21 裁决第 3 项）：前端必须**统一**在无后端时显式降级——
 * 明说「未接入」，而不是静默坏掉，更不得把 404 伪装成「已就绪 / 已登录」。
 *
 * 本文件与 `http-transport.js` / `workspace-common.js` 保持**同一判定语义**，
 * 供 内嵌页面脚本与 `hardware-telemetry.js`（非模块脚本）复用：
 * - 404 / 501 且**不含标准错误包**（对象型 detail）→ `not_integrated`（未纳入当前切片）；
 * - 401 → `unauthorized`（会话失效/未认证，提示重新登录）；
 * - 403 → `forbidden`（权限不足，切换为只读）；
 * - 409 → `conflict`（CAS 版本冲突，提示刷新并解决并发差异）；
 * - 503 → `service_unavailable`（可恢复，与「未接入」严格区分）；
 * - 其余（含对象型 detail 的业务 404）→ `error`，原样透传，不得降级。
 *
 * 证据边界：本文件只做**语义分类**与**显式占位渲染**，不探测端点是否真的存在；
 * 分类依据仅为 HTTP 状态码与响应体形状。
 */
(function (window) {
  'use strict';

  var NOT_INTEGRATED_MESSAGE = '该功能尚未接入后端（未纳入当前切片）';
  var SERVICE_UNAVAILABLE_MESSAGE = '后端服务暂时不可用，请稍后重试';
  // 以下三类状态语义来自冻结行为规范，不得与「未接入」或 503 混用：
  // 401=会话失效/未认证；403=权限不足，只读降级；409=CAS 版本冲突。
  var UNAUTHORIZED_MESSAGE = '会话失效或未认证，请重新登录';
  var FORBIDDEN_MESSAGE = '权限不足，已切换为只读模式';
  var CONFLICT_MESSAGE = '版本冲突，请刷新并解决并发差异后重试';
  var NOT_INTEGRATED_STATUSES = [404, 501];

  function escapeHtml(value) {
    return String(value === undefined || value === null ? '' : value).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }

  /** 标准错误包（对象型 detail）说明路由存在，属真实业务错误，不得当作未接入。 */
  function isStandardEnvelope(detailValue) {
    return Boolean(detailValue) && typeof detailValue === 'object';
  }

  /** FastAPI 默认路由缺失文案（无标准错误包时才可能是「未接入」）。 */
  function isDefaultRouteMissingText(detailValue) {
    return typeof detailValue === 'string' && /^(not found|not implemented)$/i.test(detailValue.trim());
  }

  /**
   * 与 `http-transport.js:isNotIntegratedResponse` 完全同源的判定：
   * - 对象型 detail = 标准错误包，路由存在 → `error`（原样透传）；
   * - 非默认文案的字符串 detail = 业务错误 → `error`（不得误报为未接入）；
   * - 无 detail / 空字符串 / FastAPI 默认文案 → `not_integrated`。
   */
  function statusKind(status, detailValue) {
    var code = Number(status);
    if (code === 401) return 'unauthorized';
    if (code === 403) return 'forbidden';
    if (code === 409) return 'conflict';
    if (code === 503) return 'service_unavailable';
    if (NOT_INTEGRATED_STATUSES.indexOf(code) === -1) return 'error';
    if (isStandardEnvelope(detailValue)) return 'error';
    if (typeof detailValue === 'string' && detailValue.trim() && !isDefaultRouteMissingText(detailValue)) return 'error';
    return 'not_integrated';
  }

  function messageFor(kind, status) {
    if (kind === 'not_integrated') return NOT_INTEGRATED_MESSAGE + '（HTTP ' + status + '）';
    if (kind === 'service_unavailable') return SERVICE_UNAVAILABLE_MESSAGE + '（HTTP ' + status + '）';
    if (kind === 'unauthorized') return UNAUTHORIZED_MESSAGE + '（HTTP ' + status + '）';
    if (kind === 'forbidden') return FORBIDDEN_MESSAGE + '（HTTP ' + status + '）';
    if (kind === 'conflict') return CONFLICT_MESSAGE + '（HTTP ' + status + '）';
    return '请求失败（HTTP ' + status + '）';
  }

  /** 由响应 + 已解析响应体得出降级判定，供调用方决定渲染分支。 */
  function classifyResponse(response, data) {
    var status = response && typeof response.status === 'number' ? response.status : 0;
    var detailValue = data ? data.detail : undefined;
    var kind = statusKind(status, detailValue);
    return { status: status, kind: kind, message: messageFor(kind, status) };
  }

  /** 带结构化标记的错误对象，禁止静默吞掉。 */
  function createError(kind, status, detail) {
    var error = new Error(messageFor(kind, status));
    error.name = kind === 'not_integrated' ? 'NotIntegratedError'
      : (kind === 'service_unavailable' ? 'ServiceUnavailableError'
        : (kind === 'unauthorized' ? 'UnauthorizedError'
          : (kind === 'forbidden' ? 'ForbiddenError'
            : (kind === 'conflict' ? 'ConflictError' : 'Error'))));
    var detailCode = detail && typeof detail === 'object' && typeof detail.code === 'string'
      ? detail.code : undefined;
    error.code = kind === 'not_integrated' ? 'NOT_INTEGRATED'
      : (kind === 'service_unavailable' ? 'SERVICE_UNAVAILABLE'
        : (kind === 'unauthorized' ? 'UNAUTHORIZED'
          : (kind === 'forbidden' ? 'FORBIDDEN'
            : (kind === 'conflict' ? (detailCode || 'VERSION_CONFLICT') : 'REQUEST_FAILED'))));
    error.unavailable = kind === 'not_integrated';
    error.retryable = kind === 'service_unavailable' || kind === 'conflict';
    error.readOnly = kind === 'forbidden';
    error.refreshRequired = kind === 'conflict';
    error.status = status;
    if (detail !== undefined) error.detail = detail;
    return error;
  }

  function isNotIntegrated(value) {
    if (!value) return false;
    if (value.code === 'NOT_INTEGRATED') return true;
    var nested = value.detail && value.detail.detail ? value.detail.detail : undefined;
    return statusKind(value.status, nested) === 'not_integrated';
  }

  function isServiceUnavailable(value) {
    return Boolean(value) && value.code === 'SERVICE_UNAVAILABLE';
  }

  function isUnauthorized(value) {
    return Boolean(value) && value.code === 'UNAUTHORIZED';
  }

  function isForbidden(value) {
    return Boolean(value) && value.code === 'FORBIDDEN';
  }

  function isConflict(value) {
    return Boolean(value) && (value.code === 'VERSION_CONFLICT'
      || value.code === 'CANVAS_VERSION_CONFLICT'
      || value.code === 'STRUCTURE_VERSION_CONFLICT'
      || value.code === 'CONFLICT');
  }

  /** 统一「未接入 / 暂不可用」显式占位 HTML（明说未接入，不使用绿色就绪灯）。 */
  function noticeHtml(kind, detail) {
    var isNotIntegratedKind = kind === 'not_integrated';
    var isServiceKind = kind === 'service_unavailable';
    var isUnauthorizedKind = kind === 'unauthorized';
    var isForbiddenKind = kind === 'forbidden';
    var isConflictKind = kind === 'conflict';
    var tone = isNotIntegratedKind ? 'text-slate-400 border-white/10'
      : (isServiceKind ? 'text-amber-300 border-amber-500/30'
        : (isConflictKind ? 'text-orange-300 border-orange-500/30' : 'text-red-300 border-red-500/30'));
    var icon = isNotIntegratedKind ? 'plug-zap'
      : (isServiceKind ? 'cloud-off'
        : (isUnauthorizedKind ? 'log-in'
          : (isForbiddenKind ? 'lock-keyhole'
            : (isConflictKind ? 'refresh-cw' : 'triangle-alert'))));
    var title = isNotIntegratedKind ? NOT_INTEGRATED_MESSAGE
      : (isServiceKind ? SERVICE_UNAVAILABLE_MESSAGE
        : (isUnauthorizedKind ? UNAUTHORIZED_MESSAGE
          : (isForbiddenKind ? FORBIDDEN_MESSAGE
            : (isConflictKind ? CONFLICT_MESSAGE : '请求失败'))));
    var extra = detail ? '<span class="block mt-0.5 text-[9px] text-slate-500">' + escapeHtml(String(detail)) + '</span>' : '';
    return '<div class="bay-inset p-3 rounded-xl border ' + tone + ' text-center text-[10px] font-mono" '
      + 'role="status" data-gw-degradation="' + kind + '">'
      + '<i data-lucide="' + icon + '" class="w-4 h-4 mx-auto mb-1 opacity-70" aria-hidden="true"></i>'
      + '<span class="block">' + title + '</span>' + extra + '</div>';
  }

  window.GWDegradation = {
    NOT_INTEGRATED_MESSAGE: NOT_INTEGRATED_MESSAGE,
    SERVICE_UNAVAILABLE_MESSAGE: SERVICE_UNAVAILABLE_MESSAGE,
    UNAUTHORIZED_MESSAGE: UNAUTHORIZED_MESSAGE,
    FORBIDDEN_MESSAGE: FORBIDDEN_MESSAGE,
    CONFLICT_MESSAGE: CONFLICT_MESSAGE,
    NOT_INTEGRATED_STATUSES: NOT_INTEGRATED_STATUSES,
    statusKind: statusKind,
    classifyResponse: classifyResponse,
    createError: createError,
    isNotIntegrated: isNotIntegrated,
    isServiceUnavailable: isServiceUnavailable,
    isUnauthorized: isUnauthorized,
    isForbidden: isForbidden,
    isConflict: isConflict,
    noticeHtml: noticeHtml,
    escapeHtml: escapeHtml
  };
})(window);
