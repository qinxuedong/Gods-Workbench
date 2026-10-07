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

/** 现有协作接口的只读面板与具备服务端权限检查的团队消息。 */
window.WorkbenchCollab = (() => {
  'use strict';
  const projectId = new URLSearchParams(location.search).get('project_id') || '';
  const pageState = () => ({items: [], status: 'loading', cursor: '', window: null, busy: false});
  const state = {members: pageState(), teams: pageState(), tasks: pageState(), logs: pageState(), audit: pageState(), approvals: pageState(), view: 'tasks'};
  const messageState = {
    bound: false, canSend: false, identityStatus: 'loading', teamId: new URLSearchParams(location.search).get('team_id') || '',
    items: [], status: 'loading', nextAfterSequence: 0, historyCursor: '', hasMore: false,
    busy: false, sending: false, pendingSend: null, timer: null, generation: 0,
    teamAbort: null,
  };
  let lifecycle = null;
  let requestSequence = 0;
  const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;'}[c]));
  const el = id => document.getElementById(id);
  const dateText = value => {
    if (!value) return '时间未知';
    const n = Number(value);
    const date = Number.isFinite(n) ? new Date(n < 100000000000 ? n * 1000 : n) : new Date(value);
    return Number.isNaN(date.getTime()) ? '时间未知' : date.toLocaleString('zh-CN', {month:'2-digit', day:'2-digit', hour:'2-digit', minute:'2-digit'});
  };
  const statusText = status => ({loading:'读取中', ready:'已读取', unauthorized:'请先登录', forbidden:'无读取权限', error:'读取失败，请刷新重试', not_integrated:'未接入（未纳入当前切片）', service_unavailable:'后端服务暂时不可用', degraded:'数据源降级，结果可能不完整'}[status] || status);
  const empty = (page, message) => { const kind = page.status === 'not_integrated' ? 'not_integrated' : (page.status === 'service_unavailable' ? 'service_unavailable' : ''); return '<div class="collab-empty"' + (kind ? ' data-gw-degradation="' + kind + '"' : '') + '>' + esc(page.status === 'ready' ? message : statusText(page.status)) + '</div>'; };
  const roles = {admin:'管理员', editor:'编辑者', reviewer:'审阅者'};

  function renderMembers() {
    const page = state.members;
    el('collabOnlineCount').textContent = page.status === 'ready' ? page.items.length + ' 个账户' : statusText(page.status);
    el('collabMemberList').innerHTML = page.items.length ? page.items.map(member => {
      const teams = state.teams.items.filter(team => (team.members || []).some(m => m.user_id === member.id));
      const teamText = state.teams.status === 'ready' ? (teams.map(t => t.name).join('、') || '未加入团队') : '团队：' + statusText(state.teams.status);
      return '<article class="collab-task-item"><strong>' + esc(member.display_name || member.username || member.id) + '</strong><p>' + esc(roles[member.role] || member.role || '未分配角色') + ' · ' + esc(member.status === 'active' ? '账户启用' : member.status === 'disabled' ? '账户停用' : member.status || '状态未知') + '</p><p>' + esc(teamText) + '</p></article>';
    }).join('') : empty(page, '暂无可见协同席位。');
    if (page.status === 'ready') el('collabMemberList').innerHTML += '<p class="collab-empty">在线状态未接入。</p>';
  }

  function updateMore(id, page) {
    const button = el(id);
    button.hidden = !page.cursor;
    button.disabled = page.busy;
    button.textContent = page.busy ? '读取中…' : page.status === 'error' ? '重试加载' : '加载更多';
  }

  function renderTimeline() {
    const page = state.audit;
    el('collabAuditScope').textContent = (projectId ? '当前工程' : '全局') + '变更审计 · 最近 24 小时';
    el('collabTimeline').innerHTML = page.items.length ? page.items.map(event => '<article class="collab-task-item"><div class="flex justify-between gap-2"><strong>' + esc(event.event_name || '变更审计') + '</strong><time>' + esc(dateText(event.timestamp)) + '</time></div><p>' + esc(event.actor_id || '系统') + ' · ' + esc(event.message_safe || '审计活动已记录') + '</p></article>').join('') : empty(page, '最近 24 小时暂无可见变更审计。');
    el('collabTimelineState').textContent = page.status === 'ready' ? page.items.length + ' 条审计' : statusText(page.status);
    updateMore('collabAuditMore', page);
  }

  function renderApprovals() {
    const page = state.approvals;
    el('collabApprovalCount').textContent = page.status === 'ready' ? '最近 ' + page.items.length + ' 条 · ' + page.items.filter(a => a.status === 'pending').length + ' 待处理' : statusText(page.status);
    el('collabApprovalList').innerHTML = page.items.length ? page.items.map(a => '<article class="collab-approval-item"><div class="flex justify-between gap-2"><strong>' + esc(a.action || '操作申请') + '</strong><span class="text-xs">' + esc(({pending:'待审批', approved:'已通过', rejected:'已拒绝'})[a.status] || a.status) + '</span></div><p>' + esc(a.requester_name || a.requester_username || a.requested_by || '未知账户') + ' · ' + esc(dateText(a.operation_created_at || a.created_at)) + '</p></article>').join('') : empty(page, '暂无审批记录。');
  }

  function renderTaskFeed() {
    const page = state[state.view];
    const tasks = state.view === 'tasks';
    el('collabTaskFeed').innerHTML = page.items.length ? page.items.map(item => {
      const title = tasks ? (item.summary || item.title || item.job_type || item.job_id) : (item.message_safe || item.event_name || '运行事件');
      const meta = tasks ? (item.status || 'unknown') + ' · ' + (item.job_id || '') : (item.level || 'INFO') + ' · ' + (item.source || '');
      return '<article class="collab-task-item"><div class="flex justify-between gap-2"><strong>' + esc(title) + '</strong><time>' + esc(dateText(item.updated_at || item.timestamp || item.created_at)) + '</time></div><p>' + esc(meta) + '</p></article>';
    }).join('') : empty(page, tasks ? '暂无可见任务。' : '最近 24 小时暂无可见日志。');
    el('collabTaskState').textContent = (tasks ? '全部时间任务' : '全部来源日志 · 最近 24 小时') + ' · ' + (page.status === 'ready' ? '已加载 ' + page.items.length + ' 条' : statusText(page.status));
    updateMore('collabTaskMore', page);
  }

  function render(key) {
    if (key === 'members' || key === 'teams') renderMembers();
    else if (key === 'audit') renderTimeline();
    else if (key === 'approvals') renderApprovals();
    else renderTaskFeed();
  }

  function requestUrl(key, page) {
    if (key === 'members') return '/api/asset-auth/users';
    if (key === 'teams') return '/api/asset-auth/teams';
    if (key === 'approvals') return '/api/asset-auth/operation-approvals?status=all&limit=100';
    const params = new URLSearchParams({range:key === 'tasks' ? 'all' : '24h', limit:'100'});
    if (key === 'audit') {
      params.set('source', 'audit_logs');
      if (projectId) params.set('project_id', projectId);
    }
    if (page.cursor) params.set('cursor', page.cursor);
    // Event cursors are scoped to an exact window, which must survive pagination.
    if (page.window) {
      params.set('start_ms', String(page.window.start));
      params.set('end_ms', String(page.window.end));
    }
    return '/api/observability/' + (key === 'tasks' ? 'tasks' : 'events') + '?' + params;
  }

  async function loadPage(key, more = false) {
    const page = state[key];
    if (page.busy) return;
    if (!more) Object.assign(page, pageState());
    page.busy = true;
    render(key);
    try {
      const response = await fetch(requestUrl(key, page), {credentials:'same-origin', cache:'no-store', signal:AbortSignal.timeout(15000)});
      if (!response.ok) {
        // 统一判定：路由不存在（404/501，且不含标准错误包）→ 未接入；503 → 暂不可用；其余才是一般错误。
        const gwd = window.GWDegradation;
        const body = await response.json().catch(() => ({}));
        const kind = gwd ? gwd.statusKind(response.status, body && body.detail)
          : (response.status === 503 ? 'service_unavailable' : 'error');
        page.status = response.status === 401 ? 'unauthorized'
          : response.status === 403 ? 'forbidden'
          : (kind === 'not_integrated' ? 'not_integrated' : (kind === 'service_unavailable' ? 'service_unavailable' : 'error'));
        if (response.status === 401 || response.status === 403) { page.items = []; page.cursor = ''; }
        render(key);
        return;
      }
      const data = await response.json();
      const field = {members:'users', teams:'teams', approvals:'approvals'}[key] || 'items';
      if (!Array.isArray(data[field])) throw new Error('Invalid API response');
      page.items = more ? page.items.concat(data[field]) : data[field];
      page.status = data.data_status === 'degraded' ? 'degraded' : 'ready';
      page.cursor = data.has_more ? data.next_cursor || '' : '';
      if (data.start_ms != null && data.end_ms != null) page.window = {start:data.start_ms, end:data.end_ms};
    } catch (_) {
      page.status = 'error';
    } finally {
      page.busy = false;
      render(key);
    }
  }

  // 头部状态位只反映真实读取结果：全部 ready/degraded 才显示已接入。
  function updateDeckStatus() {
    const node = el('collabDeckStatus');
    if (!node) return;
    const keys = ['members', 'teams', 'tasks', 'logs', 'audit', 'approvals'];
    const statuses = keys.map(k => state[k].status);
    const readyCount = statuses.filter(s => s === 'ready' || s === 'degraded').length;
    const bad = statuses.filter(s => s !== 'ready' && s !== 'degraded');
    if (!bad.length) {
      node.textContent = 'TEAM COLLABORATION & AUDIT DECK · 已接入（' + readyCount + '/' + keys.length + ' 数据源）';
      node.removeAttribute('data-gw-degradation');
    } else {
      const kind = bad.includes('unauthorized') ? 'unauthorized' : (bad.every(s => s === 'not_integrated') ? 'not_integrated' : 'service_unavailable');
      node.textContent = 'TEAM COLLABORATION & AUDIT DECK · ' + statusText(bad[0]) + '（' + readyCount + '/' + keys.length + ' 可用）';
      node.setAttribute('data-gw-degradation', kind);
    }
  }

  async function refresh() {
    const ownerLifecycle = lifecycle;
    if (!ownerLifecycle || ownerLifecycle.signal.aborted) return;
    const button = el('collabRefresh');
    button.disabled = true;
    try {
      await Promise.all(['members', 'teams', 'tasks', 'logs', 'audit', 'approvals'].map(key => loadPage(key)));
      if (lifecycle !== ownerLifecycle || ownerLifecycle.signal.aborted) return;
      await loadMessageContext(ownerLifecycle);
    } finally {
      if (lifecycle === ownerLifecycle && !ownerLifecycle.signal.aborted) {
        button.disabled = false;
        updateDeckStatus();
      }
    }
  }

  function bindTabs(signal) {
    const tabs = [...document.querySelectorAll('[data-collab-view]')];
    tabs.forEach((button, index) => {
      button.tabIndex = index ? -1 : 0;
      button.addEventListener('click', () => {
        state.view = button.dataset.collabView === 'logs' ? 'logs' : 'tasks';
        tabs.forEach(item => {
          const active = item === button;
          item.classList.toggle('active', active);
          item.setAttribute('aria-selected', String(active));
          item.tabIndex = active ? 0 : -1;
        });
        document.querySelector('[data-collab-route-link]').href = '/static/pages/collab.html?view=' + encodeURIComponent(state.view);
        renderTaskFeed();
      }, {signal});
      button.addEventListener('keydown', event => {
        if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
        event.preventDefault();
        const target = tabs[event.key === 'Home' ? 0 : event.key === 'End' ? tabs.length - 1 : (index + (event.key === 'ArrowRight' ? 1 : -1) + tabs.length) % tabs.length];
        target.focus();
        target.click();
      }, {signal});
    });
  }

  async function openTeamManagement(tab = '') {
    try {
      if (!window.AssetReview?.openTeamManagement) throw new Error('团队管理模块尚未加载，请刷新页面后重试。');
      await window.AssetReview.openTeamManagement(tab);
    } catch (error) { window.alert(error.message || '团队管理读取失败，请稍后重试。'); }
  }

  function clearMessageTimer() {
    if (messageState.timer) window.clearTimeout(messageState.timer);
    messageState.timer = null;
  }

  function setMessageStatus(text, stateName = '') {
    const status = el('collabMessageStatus');
    status.textContent = text;
    if (stateName) status.dataset.state = stateName;
    else delete status.dataset.state;
  }

  function updateMessageControls() {
    const hasTeam = messageState.bound && state.teams.status === 'ready'
      && state.teams.items.some(team => team.team_id === messageState.teamId);
    const canWrite = hasTeam && messageState.identityStatus === 'ready' && messageState.canSend;
    const input = el('collabMessageInput');
    const send = el('collabMessageSend');
    input.disabled = !canWrite;
    send.disabled = !canWrite || messageState.sending || !input.value.trim();
    send.title = canWrite ? '发送到当前所选团队' : '绑定协作身份并选择有权限的团队后可发送';
    const more = el('collabMessageHistoryMore');
    more.hidden = !messageState.hasMore || !hasTeam;
    more.disabled = messageState.busy;
    more.textContent = messageState.busy ? '读取中…' : '加载更多消息';
  }

  function renderTeamOptions() {
    const select = el('collabTeamSelect');
    const teams = state.teams.status === 'ready' ? state.teams.items : [];
    select.replaceChildren();
    const placeholder = document.createElement('option');
    placeholder.value = '';
    placeholder.textContent = teams.length ? '选择团队…' : '暂无可见团队';
    select.appendChild(placeholder);
    teams.forEach(team => {
      const option = document.createElement('option');
      option.value = team.team_id;
      option.textContent = team.name || team.team_id;
      select.appendChild(option);
    });
    const selected = teams.some(team => team.team_id === messageState.teamId) ? messageState.teamId : '';
    select.value = selected;
    select.disabled = !messageState.bound || state.teams.status !== 'ready' || !teams.length;
    el('collabIdentityBind').hidden = messageState.identityStatus !== 'unbound';
    if (messageState.identityStatus === 'unbound') {
      el('collabTeamNotice').textContent = '当前登录身份尚未启用团队协作。绑定只建立身份引用，不会自动加入任何团队。';
    } else if (!messageState.bound) {
      el('collabTeamNotice').textContent = statusText(messageState.identityStatus);
    } else if (state.teams.status !== 'ready') {
      el('collabTeamNotice').textContent = '团队列表：' + statusText(state.teams.status);
    } else if (!teams.length) {
      el('collabTeamNotice').textContent = '当前没有可见团队；请由团队管理员创建团队或添加你的成员身份。';
    } else {
      el('collabTeamNotice').textContent = '消息仅对当前团队成员及明确治理角色可见。';
    }
    updateMessageControls();
  }

  function renderMessages() {
    const feed = el('collabReviewFeed');
    const keepAtBottom = feed.scrollHeight - feed.scrollTop - feed.clientHeight < 32;
    feed.replaceChildren();
    if (!messageState.teamId) {
      const notice = document.createElement('div');
      notice.className = 'collab-empty';
      notice.textContent = messageState.bound ? '选择一个有权限的团队以读取消息。' : '请先启用团队协作身份。';
      feed.appendChild(notice);
      el('collabMessageState').textContent = messageState.bound ? '等待选择团队' : '需要身份绑定';
      updateMessageControls();
      return;
    }
    if (!messageState.items.length) {
      const notice = document.createElement('div');
      notice.className = 'collab-empty';
      notice.textContent = messageState.status === 'ready' ? '该团队暂无可见消息。' : statusText(messageState.status);
      feed.appendChild(notice);
    } else {
      messageState.items.forEach(message => {
        const article = document.createElement('article');
        article.className = 'collab-feed-item collab-task-item';
        const heading = document.createElement('div');
        heading.className = 'flex justify-between gap-2';
        const author = document.createElement('strong');
        author.textContent = message.author_user_id || '未知作者';
        const time = document.createElement('time');
        time.textContent = dateText(message.created_at);
        heading.append(author, time);
        const body = document.createElement('p');
        body.textContent = message.text || '';
        article.append(heading, body);
        feed.appendChild(article);
      });
      if (keepAtBottom) feed.scrollTop = feed.scrollHeight;
    }
    const label = messageState.status === 'ready' ? `${messageState.items.length} 条 · 序号 ${messageState.nextAfterSequence}` : statusText(messageState.status);
    el('collabMessageState').textContent = label;
    updateMessageControls();
  }

  function describeMessageError(error) {
    if (error.status === 401) return '登录已失效，请重新登录后刷新。';
    if (error.status === 403) return '当前身份没有团队消息读写权限。';
    if (error.status === 404) return '团队不存在、不可见或你已被移出团队。';
    if (error.status === 409) return '消息幂等冲突，请检查内容后重试。';
    if (error.status === 503) return '团队消息服务暂不可用，请稍后重试。';
    return error.message || '团队消息读取失败，请稍后重试。';
  }

  async function fetchForCollab(url, init = {}, extraSignals = []) {
    const request = new AbortController();
    const signals = [lifecycle?.signal, ...extraSignals].filter(Boolean);
    const listeners = [];
    for (const signal of signals) {
      if (signal.aborted) request.abort();
      else {
        const abort = () => request.abort();
        signal.addEventListener('abort', abort, {once: true});
        listeners.push([signal, abort]);
      }
    }
    const timeout = window.setTimeout(() => request.abort(), 15000);
    try {
      return await fetch(url, {
        credentials: 'same-origin', cache: 'no-store', ...init, signal: request.signal,
      });
    } finally {
      window.clearTimeout(timeout);
      listeners.forEach(([signal, abort]) => signal.removeEventListener('abort', abort));
    }
  }

  async function responseJson(response) {
    const body = await response.json().catch(() => ({}));
    if (!response.ok) {
      const detail = body && body.detail;
      const error = new Error(detail && typeof detail === 'object' ? detail.message || '请求失败' : '请求失败');
      error.status = response.status;
      error.code = detail && typeof detail === 'object' ? detail.code : '';
      throw error;
    }
    return body;
  }

  function scheduleMessagePoll(delay = 5000) {
    clearMessageTimer();
    if (!lifecycle || lifecycle.signal.aborted || document.hidden || !messageState.teamId || !messageState.bound) return;
    messageState.timer = window.setTimeout(async () => {
      messageState.timer = null;
      await loadMessages('incremental');
      scheduleMessagePoll(5000);
    }, delay);
  }

  function updateTeamUrl(teamId) {
    const url = new URL(location.href);
    if (teamId) url.searchParams.set('team_id', teamId);
    else url.searchParams.delete('team_id');
    history.replaceState(history.state, '', url.href);
  }

  async function loadMessages(mode = 'initial') {
    const teamId = messageState.teamId;
    if (!teamId || !messageState.bound || messageState.busy || !lifecycle) return;
    const generation = messageState.generation;
    const signal = messageState.teamAbort?.signal;
    messageState.busy = true;
    if (mode === 'initial' || (!messageState.items.length && mode === 'incremental')) messageState.status = 'loading';
    renderMessages();
    const params = new URLSearchParams({limit: mode === 'history' ? '50' : mode === 'initial' ? '50' : '100'});
    if (mode === 'history' && messageState.historyCursor) params.set('cursor', messageState.historyCursor);
    if (mode === 'incremental') params.set('after_sequence', String(messageState.nextAfterSequence));
    try {
      const response = await fetchForCollab(`/api/asset-auth/teams/${encodeURIComponent(teamId)}/messages?${params}`, {}, [signal]);
      const data = await responseJson(response);
      if (generation !== messageState.generation || teamId !== messageState.teamId) return;
      if (data.team_id !== teamId || !Array.isArray(data.messages) || !Number.isInteger(data.next_after_sequence)) throw new Error('团队消息响应不符合契约');
      const permissionChanged = messageState.canSend !== (data.can_send === true);
      messageState.canSend = data.can_send === true;
      if (mode === 'initial' || permissionChanged) {
        setMessageStatus(messageState.canSend ? '可发送纯文本团队消息' : '当前团队消息权限只读，不能发送');
      }
      if (mode === 'initial') {
        messageState.items = data.messages;
        messageState.nextAfterSequence = data.next_after_sequence;
        messageState.historyCursor = data.next_cursor || '';
        messageState.hasMore = Boolean(data.next_cursor);
      } else {
        if (data.messages.length) {
          const bySequence = new Map(messageState.items.map(message => [message.sequence, message]));
          data.messages.forEach(message => bySequence.set(message.sequence, message));
          messageState.items = [...bySequence.values()].sort((left, right) => left.sequence - right.sequence);
        }
        if (mode === 'incremental') {
          messageState.nextAfterSequence = Math.max(messageState.nextAfterSequence, data.next_after_sequence);
        } else if (mode === 'history') {
          messageState.historyCursor = data.next_cursor || '';
          messageState.hasMore = Boolean(data.next_cursor);
        }
      }
      messageState.status = 'ready';
    } catch (error) {
      if (generation !== messageState.generation || error.name === 'AbortError') return;
      messageState.canSend = false;
      messageState.status = error.status === 401 ? 'unauthorized' : error.status === 403 ? 'forbidden' : error.status === 503 ? 'service_unavailable' : 'error';
      if ([401, 403, 404].includes(error.status)) {
        messageState.items = [];
        messageState.hasMore = false;
        messageState.historyCursor = '';
      }
      setMessageStatus(describeMessageError(error), 'error');
    } finally {
      if (generation === messageState.generation) {
        messageState.busy = false;
        renderMessages();
      }
    }
  }

  async function loadMessageIdentity(ownerLifecycle = lifecycle) {
    try {
      const response = await fetchForCollab('/api/asset-auth/identity-binding');
      const binding = await responseJson(response);
      if (lifecycle !== ownerLifecycle || ownerLifecycle.signal.aborted) return;
      messageState.bound = Boolean(binding.bound);
      messageState.identityStatus = messageState.bound ? 'ready' : 'unbound';
      el('collabMessageState').textContent = messageState.bound ? '身份已绑定' : '需要启用团队协作身份';
    } catch (error) {
      if (lifecycle !== ownerLifecycle || ownerLifecycle.signal.aborted || error.name === 'AbortError') return;
      messageState.bound = false;
      messageState.identityStatus = error.status === 401 ? 'unauthorized' : error.status === 503 ? 'service_unavailable' : 'error';
      el('collabMessageState').textContent = describeMessageError(error);
    }
    if (lifecycle === ownerLifecycle && !ownerLifecycle.signal.aborted) renderTeamOptions();
  }

  async function loadMessageContext(ownerLifecycle = lifecycle) {
    if (!ownerLifecycle || lifecycle !== ownerLifecycle || ownerLifecycle.signal.aborted) return;
    await loadMessageIdentity(ownerLifecycle);
    if (lifecycle !== ownerLifecycle || ownerLifecycle.signal.aborted) return;
    renderTeamOptions();
    if (!messageState.bound) {
      clearMessageTimer();
      messageState.teamAbort?.abort();
      messageState.teamAbort = null;
      messageState.teamId = '';
      updateTeamUrl('');
      messageState.items = [];
      messageState.status = 'unbound';
      messageState.nextAfterSequence = 0;
      messageState.historyCursor = '';
      messageState.hasMore = false;
      renderMessages();
      return;
    }
    const teams = state.teams.status === 'ready' ? state.teams.items : [];
    const selected = teams.some(team => team.team_id === messageState.teamId)
      ? messageState.teamId
      : (teams[0]?.team_id || '');
    if (selected !== messageState.teamId) {
      selectMessageTeam(selected, false);
      return;
    }
    renderMessages();
    if (selected) {
      // URL恢复到同一团队也需要独立取消域，切队时才能中止在途读取与发送。
      if (!messageState.teamAbort || messageState.teamAbort.signal.aborted) messageState.teamAbort = new AbortController();
      await loadMessages('initial');
      scheduleMessagePoll();
    }
  }

  function selectMessageTeam(teamId, updateUrl = true) {
    const next = String(teamId || '');
    if (next === messageState.teamId && messageState.status !== 'error') return;
    clearMessageTimer();
    messageState.teamAbort?.abort();
    messageState.teamAbort = next ? new AbortController() : null;
    messageState.generation += 1;
    messageState.teamId = next;
    el('collabTeamSelect').value = next;
    messageState.items = [];
    messageState.status = next ? 'loading' : 'ready';
    messageState.nextAfterSequence = 0;
    messageState.historyCursor = '';
    messageState.hasMore = false;
    messageState.busy = false;
    messageState.sending = false;
    messageState.canSend = false;
    setMessageStatus(next ? '正在读取团队消息权限…' : '请选择团队');
    if (updateUrl) updateTeamUrl(next);
    renderMessages();
    if (next) {
      loadMessages('initial').finally(() => scheduleMessagePoll());
    }
  }

  function createRequestId() {
    if (window.crypto?.randomUUID) return `ui-${window.crypto.randomUUID()}`;
    return `ui-${Date.now()}-${++requestSequence}`;
  }

  async function sendMessage() {
    const ownerLifecycle = lifecycle;
    if (!ownerLifecycle || ownerLifecycle.signal.aborted) return;
    const input = el('collabMessageInput');
    const text = input.value;
    if (!messageState.teamId || !messageState.bound || !messageState.canSend || !text.trim() || messageState.sending) return;
    if (!messageState.pendingSend || messageState.pendingSend.text !== text) {
      messageState.pendingSend = {text, requestId: createRequestId()};
    }
    const teamId = messageState.teamId;
    const generation = messageState.generation;
    messageState.sending = true;
    updateMessageControls();
    try {
      const response = await fetchForCollab(`/api/asset-auth/teams/${encodeURIComponent(teamId)}/messages`, {
        method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({text, client_request_id: messageState.pendingSend.requestId}),
      }, [messageState.teamAbort?.signal]);
      const message = await responseJson(response);
      if (generation !== messageState.generation) return;
      if (message.team_id !== teamId || !Number.isInteger(message.sequence)) throw new Error('发送响应不符合契约');
      input.value = '';
      messageState.pendingSend = null;
      setMessageStatus(message.replayed ? '消息已确认（幂等重放）' : '消息已发送');
      await loadMessages('incremental');
    } catch (error) {
      if (lifecycle === ownerLifecycle && generation === messageState.generation && error.name !== 'AbortError') {
        setMessageStatus(describeMessageError(error) + '；再次发送将沿用同一幂等键。', 'error');
      }
    } finally {
      if (lifecycle === ownerLifecycle && generation === messageState.generation) {
        messageState.sending = false;
        updateMessageControls();
      }
    }
  }

  async function bindIdentity() {
    const ownerLifecycle = lifecycle;
    if (!ownerLifecycle || ownerLifecycle.signal.aborted) return;
    const button = el('collabIdentityBind');
    button.disabled = true;
    try {
      const response = await fetchForCollab('/api/asset-auth/identity-binding', {
        method: 'POST', headers: {'Content-Type': 'application/json'}, body: '{}',
      });
      await responseJson(response);
      if (lifecycle !== ownerLifecycle || ownerLifecycle.signal.aborted) return;
      setMessageStatus('身份已绑定；还需团队成员授权后才能查看或发送。');
      await refresh();
    } catch (error) {
      if (lifecycle === ownerLifecycle && !ownerLifecycle.signal.aborted && error.name !== 'AbortError') {
        setMessageStatus(describeMessageError(error), 'error');
      }
    } finally {
      if (lifecycle === ownerLifecycle && !ownerLifecycle.signal.aborted) button.disabled = false;
    }
  }

  function dispose() {
    clearMessageTimer();
    messageState.teamAbort?.abort();
    messageState.teamAbort = null;
    messageState.generation += 1;
    // 代次递增后，旧请求的 finally 不再释放界面锁；离页时在此显式复位。
    messageState.busy = false;
    messageState.sending = false;
    // pendingSend 与输入文本保留；不确定是否已提交时，恢复后必须复用幂等键。
    if (lifecycle && !lifecycle.signal.aborted) lifecycle.abort();
    lifecycle = null;
  }

  function init() {
    dispose();
    lifecycle = new AbortController();
    const signal = lifecycle.signal;
    bindTabs(signal);
    el('collabRefresh').addEventListener('click', refresh, {signal});
    el('collabTaskMore').addEventListener('click', () => loadPage(state.view, true), {signal});
    el('collabAuditMore').addEventListener('click', () => loadPage('audit', true), {signal});
    el('collabTeamSelect').addEventListener('change', event => selectMessageTeam(event.target.value), {signal});
    el('collabIdentityBind').addEventListener('click', bindIdentity, {signal});
    el('collabTeamManage').addEventListener('click', () => openTeamManagement('teams'), {signal});
    el('collabMessageHistoryMore').addEventListener('click', () => loadMessages('history'), {signal});
    el('collabMessageSend').addEventListener('click', sendMessage, {signal});
    el('collabMessageInput').addEventListener('input', updateMessageControls, {signal});
    document.addEventListener('visibilitychange', () => {
      if (document.hidden) clearMessageTimer();
      else if (messageState.teamId && messageState.bound) {
        loadMessages('incremental').finally(() => scheduleMessagePoll(5000));
      }
    }, {signal});
    refresh();
    window.lucide?.createIcons();
  }
  window.addEventListener('pagehide', dispose);
  window.addEventListener('pageshow', () => {
    if (!lifecycle && document.getElementById('collabReviewFeed')) init();
  });
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init, {once: true});
  else init();
  return {openTeamManagement, rebind: init, dispose};
})();
