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
 * Gods' Workbench - 智能体神经中枢控制器 (agents-controller.js)
 */

window.WorkbenchAgents = (function () {
  'use strict';

  // 内置示例目录仅作本地展示；GET 配置摘要只证明已配置，不代表 Provider 可达。
  // 模型调用只由用户明确提交测试意图触发，并提示可能计费且不自动重试。
  const state = {
    agents: [
      { id: 'aura-01', name: 'AURA·核心调度脑', role: '系统编排与逻辑拆解', status: 'not_integrated', model: '等待服务端配置', active: true },
      { id: 'flux-02', name: 'FLUX-PRO 电影原画师', role: '4K ACEScg 视觉生成', status: 'not_integrated', model: 'FLUX.1-DEV + LoRA', active: false },
      { id: 'script-03', name: 'SCRIPT-ARCH 剧本架构师', role: '文学分镜与冲突提炼', status: 'not_integrated', model: 'DeepSeek-R1 / V3', active: false, route: '/static/pages/workshop.html?step=script&agent=script-03' },
      { id: 'vox-04', name: 'VOX-SYNCLIP 配音合成师', role: '音效唇形与声音克隆', status: 'not_integrated', model: 'Wav2Lip + GPT-SoVITS', active: false }
    ],
    traces: [],
    traceUnsubscribe: null,
    auraScopeHandler: null,
    chatProviders: [],
    selectedProviderId: '',
    configurationStatus: 'unknown',
    requestInFlight: false
  };

  const esc = str => String(str ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));

  function renderAgents() {
    const list = document.getElementById('agentList');
    if (!list) return;

    list.innerHTML = state.agents.map(ag => `
      <div class="tree-node-card ${ag.active ? 'active' : ''} p-2 cursor-pointer transition" role="button" tabindex="0" onclick="WorkbenchAgents.selectAgent('${ag.id}')" onkeydown="if(event.key==='Enter'||event.key===' '){event.preventDefault();WorkbenchAgents.selectAgent('${ag.id}')}" aria-label="选择 ${esc(ag.name)}">
        <div class="flex items-center justify-between mb-1">
          <div class="font-bold text-slate-100 gw-type-body-md truncate">${esc(ag.name)}</div>
          ${ag.route ? `<a href="${esc(ag.route)}" class="text-slate-400 hover:text-[#dfc384] shrink-0" title="打开剧本架构师路由" aria-label="打开剧本架构师路由" onclick="event.stopPropagation()"><i data-lucide="external-link" class="w-3 h-3"></i></a>` : ''}
          <span class="gw-type-body-sm font-mono px-1 py-0.5 rounded-full ${ag.status === 'configured' ? 'bg-cyan-500/15 text-cyan-300 border border-cyan-500/30' : 'bg-amber-500/15 text-amber-300 border border-amber-500/30'}"${ag.status !== 'configured' ? ' data-gw-degradation="' + ag.status + '"' : ''}>
            ${ag.status === 'configured' ? 'Provider 已配置' : (ag.status === 'not_integrated' ? '未配置' : (ag.status === 'service_unavailable' ? '配置读取失败' : '配置读取中'))}
          </span>
        </div>
        <div class="gw-type-body-sm text-slate-400 truncate mb-1">${esc(ag.role)}</div>
        <div class="gw-type-body-sm font-mono text-[#dfc384] truncate">${esc(ag.model)}</div>
      </div>
    `).join('');
  }

  function renderTraces() {
    const container = document.getElementById('agentTraceStream');
    if (!container) return;
    if (!state.traces.length) {
      container.innerHTML = '<div class="aura-trace-empty">等待剧本架构师执行记录…</div>';
      return;
    }
    container.innerHTML = state.traces.map(t => {
      let tagColor = 'text-[#dfc384] bg-[#dfc384]/15 border-[#dfc384]/30';
      if (t.tag === 'TOOL') tagColor = 'text-cyan-300 bg-cyan-500/15 border-cyan-500/30';
      if (t.tag === 'THINK') tagColor = 'text-purple-300 bg-purple-500/15 border-purple-500/30';
      if (t.tag === 'EXEC') tagColor = 'text-emerald-400 bg-emerald-500/15 border-emerald-500/30';

      return `
        <div class="aura-trace-row" role="button" tabindex="0" data-aura-trace-id="${esc(t.id)}" aria-expanded="false" title="点击展开详情">
          <div class="aura-trace-summary gw-type-body-sm">
            <span class="gw-type-body-sm font-mono text-slate-500 shrink-0 pt-0.5">${esc(t.time)}</span>
            <span class="gw-type-body-sm font-mono px-1 py-0.5 rounded border ${tagColor} shrink-0 font-bold">${esc(t.tag)}</span>
            <span class="aura-trace-summary-text text-slate-200 leading-relaxed font-body">${esc(t.summary || t.text || '')}</span>
          </div>
          <pre class="aura-trace-detail" data-aura-trace-detail hidden>${esc(t.detail || t.text || t.summary || '')}</pre>
        </div>
      `;
    }).join('');

    container.querySelectorAll('[data-aura-trace-id]').forEach(row => {
      const detail = row.querySelector('[data-aura-trace-detail]');
      const collapse = () => { row.classList.remove('is-expanded'); row.setAttribute('aria-expanded', 'false'); if (detail) detail.hidden = true; };
      const expand = () => { row.classList.add('is-expanded'); row.setAttribute('aria-expanded', 'true'); if (detail) detail.hidden = false; };
      row.addEventListener('click', () => row.classList.contains('is-expanded') ? collapse() : expand());
      row.addEventListener('mouseleave', collapse);
      row.addEventListener('keydown', event => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); row.classList.contains('is-expanded') ? collapse() : expand(); } });
    });

    container.scrollTop = container.scrollHeight;
  }

  function appendTrace(trace) {
    if (!trace?.id || state.traces.some(item => item.id === trace.id)) return;
    state.traces.push(trace);
    state.traces = state.traces.slice(-80);
    renderTraces();
  }

  function selectAgent(id) {
    const restoreFocus = document.getElementById('agentList')?.contains(document.activeElement);
    state.agents.forEach(a => a.active = (a.id === id));
    renderAgents();
    if (restoreFocus) {
      const selected = document.querySelector('#agentList .active');
      if (selected) { selected.tabIndex = 0; selected.focus(); }
    }
    const found = state.agents.find(a => a.id === id);
    const titleEl = document.getElementById('currentAgentTitle');
    if (titleEl && found) {
      titleEl.textContent = `${found.name} (思维链路实时透视)`;
    }
    if (found?.route) {
      try { localStorage.setItem('gwb_agent_route', found.route); } catch (_) {}
      window.dispatchEvent(new CustomEvent('gw-agent-route', {detail: {agentId: found.id, route: found.route}}));
    }
  }

  let agentProviderStatus = 'unknown';

  function metricSelectionKey() {
    const provider = document.getElementById('agentProviderSelect');
    const model = document.getElementById('agentModelSelect');
    return `${provider?.value || state.selectedProviderId || ''}\u0000${model?.value || ''}`;
  }

  function clearAgentMetrics(reason) {
    const throughput = document.getElementById('agentThroughputReadout');
    const latency = document.getElementById('agentLatencyReadout');
    const label = reason === 'configuration_changed' ? '配置已切换，需重新测量' : '尚未测量';
    if (throughput) {
      throughput.textContent = `吞吐: ${label}`;
      throughput.className = 'px-1.5 py-0.5 rounded bg-black/60 text-amber-300';
      throughput.setAttribute('data-gw-degradation', 'not_measured');
      throughput.title = '仅显示用户主动发送消息后，由上游 usage 与单调时钟计算的端到端输出 tokens/s';
    }
    if (latency) {
      latency.textContent = `时延: ${label}`;
      latency.className = 'px-1.5 py-0.5 rounded bg-black/60 text-amber-300';
      latency.setAttribute('data-gw-degradation', 'not_measured');
      latency.title = '仅显示同一次真实 Chat Completions 请求的服务端端到端耗时';
    }
  }

  function applyAgentMetrics(metrics, failureReason) {
    const throughput = document.getElementById('agentThroughputReadout');
    const latency = document.getElementById('agentLatencyReadout');
    const validKind = metrics && metrics.kind === 'end_to_end_output_tokens_per_second';
    const validTokens = validKind && Number.isSafeInteger(metrics.output_tokens) && metrics.output_tokens >= 0;
    const validElapsed = validKind && typeof metrics.elapsed_seconds === 'number'
      && Number.isFinite(metrics.elapsed_seconds) && metrics.elapsed_seconds > 0;
    const validRate = validKind && typeof metrics.tokens_per_second === 'number'
      && Number.isFinite(metrics.tokens_per_second) && metrics.tokens_per_second >= 0;
    const reason = String((metrics && metrics.unavailable_reason) || failureReason || 'metrics_unavailable');
    if (throughput) {
      if (validTokens && validElapsed && validRate) {
        throughput.textContent = `端到端输出: ${metrics.tokens_per_second.toFixed(2)} tokens/s`;
        throughput.className = 'px-1.5 py-0.5 rounded bg-black/60 text-cyan-300';
        throughput.removeAttribute('data-gw-degradation');
        throughput.title = `上游报告输出 ${metrics.output_tokens} token；来源 ${String(metrics.usage_source || 'unknown')}；Provider ${String(metrics.provider_id || 'unknown')} / 模型 ${String(metrics.model || 'unknown')}。不是解码速度。`;
      } else {
        throughput.textContent = '端到端输出: 不可用';
        throughput.className = 'px-1.5 py-0.5 rounded bg-black/60 text-amber-300';
        throughput.setAttribute('data-gw-degradation', 'metrics_unavailable');
        throughput.title = `本次无可验证吞吐读数（${reason}），未使用文本长度估算。`;
      }
    }
    if (latency) {
      if (validElapsed) {
        latency.textContent = `端到端耗时: ${(metrics.elapsed_seconds * 1000).toFixed(0)} ms`;
        latency.className = 'px-1.5 py-0.5 rounded bg-black/60 text-cyan-300';
        latency.removeAttribute('data-gw-degradation');
        latency.title = '服务端从请求发出到完整响应体收齐的单调时钟耗时；不是浏览器网络延迟';
      } else {
        latency.textContent = '端到端耗时: 不可用';
        latency.className = 'px-1.5 py-0.5 rounded bg-black/60 text-amber-300';
        latency.setAttribute('data-gw-degradation', 'metrics_unavailable');
        latency.title = `本次无有效端到端耗时（${reason}）。`;
      }
    }
  }

  function selectedProvider() {
    const node = document.getElementById('agentProviderSelect');
    const providerId = String(node?.value || state.selectedProviderId || '');
    return state.chatProviders.find(item => item.provider_id === providerId && item.configured) || null;
  }

  function refreshModelOptions(preferredModel) {
    const providerNode = document.getElementById('agentProviderSelect');
    const modelNode = document.getElementById('agentModelSelect');
    const selected = selectedProvider();
    if (!modelNode) return;
    const models = selected && Array.isArray(selected.models) ? selected.models : [];
    modelNode.innerHTML = models.map(model => `<option value="${esc(model)}">${esc(model)}</option>`).join('');
    const desired = models.includes(preferredModel) ? preferredModel
      : (selected && models.includes(selected.default_model) ? selected.default_model : (models[0] || ''));
    modelNode.value = desired;
    modelNode.disabled = !selected || models.length === 0 || state.requestInFlight;
    if (providerNode) providerNode.disabled = !state.chatProviders.some(item => item.configured) || state.requestInFlight;
    const aura = state.agents.find(item => item.id === 'aura-01');
    if (aura) aura.model = desired || '等待服务端配置';
    const mount = document.getElementById('agentModelMount');
    if (mount) mount.textContent = desired || '等待服务端配置';
    renderAgents();
  }

  function applyAgentProviderStatus() {
    state.agents.forEach(agent => {
      // Chat 配置只代表 AURA 对话链可配置，不代表示例目录中的其它工具/模型已接入。
      agent.status = agent.id === 'aura-01' ? agentProviderStatus : 'not_integrated';
    });
    renderAgents();
  }

  async function loadChatConfiguration() {
    try {
      const response = await fetch('/api/chat/config', {
        method: 'GET',
        credentials: 'same-origin',
        headers: {'Accept': 'application/json'},
        signal: AbortSignal.timeout(10000)
      });
      const body = await response.json().catch(() => null);
      if (!response.ok || !body || !Array.isArray(body.providers)) {
        throw new Error(`配置状态读取失败（HTTP ${response.status}）`);
      }
      state.chatProviders = body.providers.filter(item => item && typeof item.provider_id === 'string'
        && Array.isArray(item.models) && typeof item.configured === 'boolean');
      state.configurationStatus = String(body.configuration_status || 'not_configured');
      const usable = state.chatProviders.filter(item => item.configured);
      state.selectedProviderId = usable.some(item => item.provider_id === body.default_provider_id)
        ? body.default_provider_id : (usable.length === 1 ? usable[0].provider_id : '');
      agentProviderStatus = usable.length ? 'configured' : 'not_integrated';
      const providerNode = document.getElementById('agentProviderSelect');
      if (providerNode) {
        providerNode.innerHTML = '<option value="">请选择服务端 Provider</option>' + usable.map(item =>
          `<option value="${esc(item.provider_id)}">${esc(item.provider_id)}${item.source === 'legacy_environment' ? ' · 环境兼容配置' : ''}</option>`
        ).join('');
        providerNode.value = state.selectedProviderId;
      }
      refreshModelOptions();
    } catch (_) {
      state.chatProviders = [];
      state.selectedProviderId = '';
      state.configurationStatus = 'unavailable';
      agentProviderStatus = 'service_unavailable';
      const providerNode = document.getElementById('agentProviderSelect');
      if (providerNode) {
        providerNode.innerHTML = '<option value="">Provider 配置不可读取</option>';
        providerNode.disabled = true;
      }
      refreshModelOptions();
    }
    applyAgentProviderStatus();
    const bus = document.getElementById('agentBusStatus');
    if (bus) {
      if (agentProviderStatus === 'configured') {
        bus.textContent = 'Provider 已配置 · 尚未试请求';
        bus.className = 'gw-type-body-sm font-mono text-cyan-300';
        bus.setAttribute('title', '仅 GET /api/chat/config 读取服务端配置；尚未连接或发送模型请求');
        bus.removeAttribute('data-gw-degradation');
      } else if (agentProviderStatus === 'not_integrated') {
        bus.textContent = 'Provider 未配置';
        bus.className = 'gw-type-body-sm font-mono text-amber-300';
        bus.setAttribute('title', '服务端没有可用的 Chat Provider 配置；页面加载未触发生成');
        bus.setAttribute('data-gw-degradation', 'not_integrated');
      } else {
        bus.textContent = '配置状态不可用';
        bus.className = 'gw-type-body-sm font-mono text-amber-300';
        bus.setAttribute('title', 'Chat 配置状态读取失败；页面加载未触发生成');
        bus.setAttribute('data-gw-degradation', 'service_unavailable');
      }
    }
    clearAgentMetrics('not_measured');
  }

  function bindChatSelection() {
    const providerNode = document.getElementById('agentProviderSelect');
    const modelNode = document.getElementById('agentModelSelect');
    providerNode?.addEventListener('change', () => {
      state.selectedProviderId = String(providerNode.value || '');
      refreshModelOptions();
      clearAgentMetrics('configuration_changed');
    });
    modelNode?.addEventListener('change', () => clearAgentMetrics('configuration_changed'));
  }

  async function sendTestPrompt(e) {
    e?.preventDefault?.();
    if (state.requestInFlight) return;
    const input = document.getElementById('agentPromptInput');
    if (!input || !String(input.value || '').trim()) return;
    const provider = selectedProvider();
    const modelNode = document.getElementById('agentModelSelect');
    const model = String(modelNode?.value || '');
    if (!provider || !provider.models.includes(model)) {
      clearAgentMetrics('configuration_changed');
      alert('请选择一个已由服务端配置的 Provider 与模型；本次未发送请求。');
      return;
    }
    if (!window.confirm('即将向服务端配置的真实模型发送此消息，可能产生费用。本次不会自动重试；是否继续？')) return;

    const selectionKey = metricSelectionKey();
    const val = String(input.value).trim();
    input.value = '';
    state.requestInFlight = true;
    const button = document.getElementById('agentSendButton');
    const providerNode = document.getElementById('agentProviderSelect');
    if (button) button.disabled = true;
    if (providerNode) providerNode.disabled = true;
    if (modelNode) modelNode.disabled = true;
    clearAgentMetrics('not_measured');

    const operation = `AURA 测试执行 · ${val}`;
    const publish = (tag, summary, detail, status) => window.AuraTraceBus?.publish?.({
      tag, summary, detail, agentId: 'aura-01', route: 'aura-test', operation, stage: 'agent-test', status,
    });
    publish('INPUT', `已接收：${val}`, {message_chars: val.length, provider_id: provider.provider_id, model}, 'running');
    try {
      const response = await fetch('/api/chat/agent', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        credentials: 'same-origin',
        body: JSON.stringify({message: val, provider_id: provider.provider_id, model}),
        signal: AbortSignal.timeout(35000)
      });
      const body = await response.json().catch(() => null);
      const currentSelectionKey = metricSelectionKey();
      if (currentSelectionKey === selectionKey) {
        if (response.ok) applyAgentMetrics(body?.metrics, 'metrics_missing');
        else applyAgentMetrics(body?.detail?.metrics, body?.detail?.metrics?.unavailable_reason || 'provider_error');
      }
      if (!response.ok) {
        const code = body?.detail?.code || '';
        const message = body?.detail?.message || `智能体请求失败（HTTP ${response.status}）`;
        const kind = code === 'CHAT_NOT_INTEGRATED' || code === 'CHAT_PROVIDER_NOT_CONFIGURED' ? 'not_integrated' : 'service_unavailable';
        publish('THINK', `本次模型请求未成功：${message}`, {route: '/api/chat/agent', code, http_status: response.status, provider_id: provider.provider_id, model}, kind);
        publish('EXEC', `未产生可用执行结果（${code || response.status}）`, {message_chars: val.length, result: kind}, kind);
        return;
      }
      const text = body && typeof body.reply === 'string' ? body.reply : '';
      publish('THINK', 'AURA 已获得服务端核实 Provider/模型的真实响应', {
        route: '/api/chat/agent', http_status: response.status,
        provider_id: body.provider_id, model: body.model,
        metrics: body.metrics ? {output_tokens: body.metrics.output_tokens, elapsed_seconds: body.metrics.elapsed_seconds, tokens_per_second: body.metrics.tokens_per_second, unavailable_reason: body.metrics.unavailable_reason} : null,
      }, 'running');
      publish('EXEC', text ? text : 'Provider 返回空文本', {message_chars: val.length, result: 'ok'}, 'ok');
    } catch (error) {
      const reason = error && error.name === 'TimeoutError' ? 'request_timeout' : 'network_error';
      if (metricSelectionKey() === selectionKey) applyAgentMetrics(null, reason);
      publish('THINK', '本次模型请求未完成或超时；未自动重试', {route: '/api/chat/agent', reason, provider_id: provider.provider_id, model}, 'service_unavailable');
      publish('EXEC', '未产生可用执行结果（网络/超时）', {message_chars: val.length, result: reason}, 'service_unavailable');
    } finally {
      state.requestInFlight = false;
      if (button) button.disabled = false;
      if (providerNode) providerNode.disabled = !state.chatProviders.some(item => item.configured);
      if (modelNode) modelNode.disabled = !selectedProvider();
    }
  }

  function createAgent() {
    // 后端确实没有智能体管理（挂载/删除）端点，属真实未接入能力，明说且不伪造。
    alert('智能体挂载未接入：后端未提供智能体管理端点，未打开任何向导，也未挂载任何模型。');
  }

  function init() {
    state.traceUnsubscribe?.();
    if (state.auraScopeHandler) window.removeEventListener('gw-aura-scope', state.auraScopeHandler);
    state.traces = window.AuraTraceBus?.history?.() || [];
    applyAgentProviderStatus();
    renderAgents();
    bindChatSelection();
    loadChatConfiguration();
    renderTraces();
    state.traceUnsubscribe = window.AuraTraceBus?.subscribe?.(appendTrace) || null;
    state.auraScopeHandler = () => {
      state.traces = window.AuraTraceBus?.history?.() || [];
      renderTraces();
    };
    window.addEventListener('gw-aura-scope', state.auraScopeHandler);
    window.lucide?.createIcons();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }

  return {
    selectAgent,
    sendTestPrompt,
    createAgent,
    rebind: init
  };
})();
