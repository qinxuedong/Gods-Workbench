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
 * Gods' Workbench - 任务队列共享接线模块 (workbench-task-queue.js)
 *
 * 唯一真实数据源：GET /api/observability/tasks（见 docs/contracts/OBSERVABILITY-INTERFACE-CATALOG.yaml）。
 * 只渲染后端返回的 job_id / status / poll_hint / has_result；
 * 后端不可达时显式降级，绝不生成示例任务、伪造进度百分比或伪造在线绿灯。
 */
(function () {
  'use strict';

  const ENDPOINT = '/api/observability/tasks';
  // 达到终态之外的状态都视为「进行中」，用于计数徽标。
  const ACTIVE_STATES = ['accepted', 'queued', 'pending', 'running', 'recovering', 'cancel_requested'];
  const STATUS_LABELS = {
    accepted: '已受理', queued: '排队中', pending: '待处理', running: '执行中',
    recovering: '恢复中', cancel_requested: '取消中', succeeded: '已完成',
    completed: '已完成', failed: '失败', canceled: '已取消', interrupted: '已中断'
  };

  const esc = value => String(value ?? '').replace(/[&<>"']/g, c => (
    { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]
  ));

  const state = { items: [], total: 0, degradation: null, loaded: false };

  function kindFor(status, body) {
    const gwd = window.GWDegradation;
    if (gwd && typeof gwd.statusKind === 'function') {
      return gwd.statusKind(status, body && body.detail);
    }
    if (status === 503) return 'service_unavailable';
    if (status === 401) return 'unauthorized';
    if (status === 403) return 'forbidden';
    if (status === 404 || status === 501) return 'not_integrated';
    return 'error';
  }

  function messageFor(kind, status) {
    const gwd = window.GWDegradation;
    if (gwd && typeof gwd.noticeMessage === 'function') {
      const text = gwd.noticeMessage(kind);
      if (text) return text;
    }
    if (kind === 'not_integrated') return '任务队列端点尚未接入（未纳入当前切片）';
    if (kind === 'service_unavailable') return '任务队列服务暂时不可用，请稍后重试';
    if (kind === 'unauthorized') return '请先登录后再读取任务队列';
    if (kind === 'forbidden') return '当前角色无任务队列读取权限';
    return '任务队列读取失败（HTTP ' + status + '）';
  }

  function cardHtml(task) {
    const status = String(task && task.status ? task.status : '');
    const label = STATUS_LABELS[status] || (status || '状态未知');
    const isActive = ACTIVE_STATES.indexOf(status) !== -1;
    const dot = isActive ? 'bg-cyan-400 shadow-[0_0_6px_#38bdf8]' : 'bg-slate-500';
    const border = isActive ? 'border-[#dfc384]' : 'border-slate-600';
    return '<div class="bay-inset p-2 rounded-xl border-l-2 ' + border + ' space-y-1 bg-[#0a0c10]" data-job-id="' + esc(task && task.job_id) + '">' +
      '<div class="flex items-center justify-between">' +
        '<div class="flex items-center space-x-1.5 min-w-0">' +
          '<span class="w-1.5 h-1.5 rounded-full ' + dot + ' shrink-0"></span>' +
          '<span class="gw-type-body-md font-bold text-slate-200 truncate font-mono">' + esc((task && task.job_id) || '未知 job_id') + '</span>' +
        '</div>' +
        '<span class="gw-type-body-sm font-mono shrink-0 font-semibold text-slate-300">' + esc(label) + '</span>' +
      '</div>' +
      '<div class="flex items-center justify-between gw-type-body-sm font-mono text-slate-500">' +
        '<span class="truncate">' + esc((task && task.poll_hint) || '无轮询地址') + '</span>' +
        '<span class="shrink-0">' + (task && task.has_result ? '已产出结果' : '暂无结果') + '</span>' +
      '</div>' +
    '</div>';
  }

  function degradationHtml(failure) {
    return '<div class="bay-inset p-3 rounded-xl border border-white/10 text-center gw-type-body-sm font-mono text-slate-400" role="status" data-gw-degradation="' + esc(failure.kind) + '">' +
      '<span class="block">' + esc(failure.message) + '</span>' +
      '<span class="block mt-1 gw-type-body-sm text-slate-500">未取到真实作业队列；本页不展示任何伪造任务。</span>' +
    '</div>';
  }

  function render() {
    const targets = [
      document.getElementById('workbenchActiveTasksContainer'),
      document.getElementById('workbenchTaskCenterDrawerList')
    ].filter(Boolean);
    const badge = document.getElementById('activeTasksCountBadge');

    if (badge) {
      if (state.degradation) {
        badge.textContent = '队列读取失败';
        badge.className = 'gw-type-body-sm font-mono font-bold text-amber-300';
        badge.setAttribute('data-gw-degradation', state.degradation.kind);
        badge.setAttribute('title', state.degradation.message);
      } else if (state.loaded) {
        const activeCount = state.items.filter(t => ACTIVE_STATES.indexOf(String((t && t.status) || '')) !== -1).length;
        badge.textContent = '队列 ' + activeCount + ' / 共 ' + state.total;
        badge.className = 'gw-type-body-sm font-mono font-bold ' + (activeCount ? 'text-cyan-300' : 'text-slate-400');
        badge.removeAttribute('data-gw-degradation');
        badge.setAttribute('title', '真实作业队列：进行中 ' + activeCount + '，总计 ' + state.total);
      }
    }

    // 首页「渲染总线」状态位：与任务队列同一次真实结果同步，绝不静态谎称未接入。
    const busNode = document.getElementById('renderBusStatus');
    if (busNode) {
      const led = document.getElementById('renderBusLed');
      if (state.degradation) {
        const kind = state.degradation.kind;
        busNode.textContent = '渲染总线 · ' + (kind === 'service_unavailable' ? '暂不可用' : '未接入');
        busNode.className = 'gw-type-body-sm font-bold text-amber-300';
        busNode.setAttribute('data-gw-degradation', kind);
        busNode.setAttribute('title', state.degradation.message);
        if (led) led.className = 'w-1.5 h-1.5 rounded-full bg-amber-400 animate-pulse';
      } else if (state.loaded) {
        const active = state.items.filter(t => ACTIVE_STATES.indexOf(String((t && t.status) || '')) !== -1).length;
        busNode.textContent = '渲染总线 · ' + active + ' 进行中 / 共 ' + state.total;
        busNode.className = 'gw-type-body-sm font-bold ' + (active ? 'text-cyan-300' : 'text-slate-300');
        busNode.removeAttribute('data-gw-degradation');
        busNode.setAttribute('title', '真实作业队列：进行中 ' + active + '，总计 ' + state.total);
        if (led) led.className = 'w-1.5 h-1.5 rounded-full ' + (active ? 'bg-cyan-400 shadow-[0_0_6px_#38bdf8]' : 'bg-slate-500');
      }
    }

    if (!targets.length) return;
    let body;
    if (state.degradation) body = degradationHtml(state.degradation);
    else if (state.items.length) body = state.items.map(cardHtml).join('');
    else body = '<div class="bay-inset p-3 rounded-xl border border-white/10 text-center gw-type-body-sm font-mono text-slate-400" role="status"><span class="block">当前没有真实作业。</span></div>';
    targets.forEach(node => { node.innerHTML = body; });
    if (window.lucide) window.lucide.createIcons();
  }

  let reloadSequence = 0;
  async function reload() {
    const sequence = ++reloadSequence;
    if (!window.GWAuthGate?.isAuthenticated()) {
      state.items = []; state.total = 0;
      state.degradation = {kind:'unauthorized', message:'请先登录后再读取任务队列'}; render(); return;
    }
    const authRevision = window.GWAuthGate?.revision;
    try {
      const response = await fetch(ENDPOINT + '?range=all&limit=20', {
        credentials: 'same-origin',
        cache: 'no-store',
        signal: AbortSignal.timeout(15000)
      });
      if (sequence !== reloadSequence || authRevision !== window.GWAuthGate?.revision || !window.GWAuthGate?.isAuthenticated()) return;
      if (!response.ok) {
        if (response.status === 401) window.GWAuthGate?.invalidate(401, authRevision);
        const body = await response.json().catch(() => null);
        const kind = kindFor(response.status, body);
        state.items = [];
        state.total = 0;
        state.degradation = { kind: kind, message: messageFor(kind, response.status) };
      } else {
        const data = await response.json();
        if (sequence !== reloadSequence || authRevision !== window.GWAuthGate?.revision || !window.GWAuthGate?.isAuthenticated()) return;
        if (Array.isArray(data && data.items)) {
          state.items = data.items;
          state.total = Number.isFinite(Number(data.total)) ? Number(data.total) : data.items.length;
          state.degradation = null;
        } else {
          state.items = [];
          state.total = 0;
          state.degradation = { kind: 'error', message: '任务队列响应不符合契约（缺少 items 数组）' };
        }
      }
    } catch (_) {
      if (sequence !== reloadSequence || authRevision !== window.GWAuthGate?.revision) return;
      state.items = [];
      state.total = 0;
      state.degradation = { kind: 'service_unavailable', message: messageFor('service_unavailable', 0) };
    }
    state.loaded = true;
    render();
  }

  window.addEventListener('gw-auth-state', reload);
  function init() {
    render();
    if (document.getElementById('workbenchActiveTasksContainer')
        || document.getElementById('workbenchTaskCenterDrawerList')
        || document.getElementById('activeTasksCountBadge')) {
      reload();
    }
  }

  window.WorkbenchTaskQueue = { reload: reload, render: render, state: state };

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
  else init();
})();
