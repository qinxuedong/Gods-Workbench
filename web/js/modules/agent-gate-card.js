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

// MVP2-T7b（issue #416）生产链门禁确认卡。
// 职责边界：只做纯渲染与纯提交。不发框架请求、不引入框架或 CDN、
// 不拼装任何外部 URL；网络入口由宿主注入 fetchImpl，测试注入 mock 即可验证。
// 所有动态文本（门禁摘要、用户理由、服务端错误）一律转义后按纯文本输出。
// 状态机以服务端门禁台账为真源：只有 pending 的卡片可操作，
// 其余状态一律渲染为已处理终态，不提供按钮（fail closed）。

export const GATE_UNKNOWN_TEXT = '未知';

// 门禁类别与后端 GateKind 一一对应（script/storyboard/animatic/i2v_submit/master_output）。
const GATE_KIND_SPECS = [
    { kind: 'script', label: '剧本' },
    { kind: 'storyboard', label: '分镜' },
    { kind: 'animatic', label: '样片' },
    { kind: 'i2v_submit', label: '图生视频提交' },
    { kind: 'master_output', label: '母带输出' },
];

// 状态与后端 GateState 一一对应；tone 全部取自现有黑金 token。
const GATE_STATE_SPECS = [
    { state: 'pending', label: '等待确认', tone: 'var(--gw-warning)' },
    { state: 'approved', label: '已批准', tone: 'var(--gw-success)' },
    { state: 'changes_requested', label: '已要求修改', tone: 'var(--gw-warning)' },
    { state: 'rolled_back', label: '已退回', tone: 'var(--gw-error)' },
];

const GATE_DECISION_SPECS = [
    { decision: 'approve', label: '确认通过', buttonClass: 'hw-btn hw-btn-sm hw-btn-gold' },
    { decision: 'changes_requested', label: '要求修改', buttonClass: 'hw-btn hw-btn-sm' },
    { decision: 'rolled_back', label: '退回', buttonClass: 'hw-btn hw-btn-sm btn-abort-carmine' },
];

export const GATE_KINDS = Object.freeze(GATE_KIND_SPECS.map(spec => spec.kind));
export const GATE_STATES = Object.freeze(GATE_STATE_SPECS.map(spec => spec.state));
export const GATE_DECISIONS = Object.freeze(GATE_DECISION_SPECS.map(spec => spec.decision));
export const GATE_KIND_LABELS = Object.freeze(Object.fromEntries(GATE_KIND_SPECS.map(spec => [spec.kind, spec.label])));
export const GATE_STATE_LABELS = Object.freeze(Object.fromEntries(GATE_STATE_SPECS.map(spec => [spec.state, spec.label])));
export const GATE_DECISION_LABELS = Object.freeze(Object.fromEntries(GATE_DECISION_SPECS.map(spec => [spec.decision, spec.label])));

// 卡片呈现相位：idle 可操作，submitting 等待服务端，error 保留按钮并展示错误。
export const GATE_PHASES = Object.freeze({ idle: 'idle', submitting: 'submitting', error: 'error' });

// 决策理由上限，与后端 GateTransitionRecord.reason 的 512 上限一致。
export const GATE_REASON_MAX_LENGTH = 512;

function escapeHtml(value = '') {
    return String(value)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
}

function firstNonEmptyText(...values) {
    for (const value of values) {
        if (typeof value === 'string' && value.trim()) return value.trim();
        if (typeof value === 'number' && Number.isFinite(value)) return String(value);
    }
    return null;
}

function normalizeVersion(value) {
    return Number.isInteger(value) && value >= 0 ? value : 0;
}

// 未知类别返回 null，由渲染层统一显示「未知」，不猜测、不回退到第一个枚举值。
export function normalizeGateKind(value) {
    return GATE_KINDS.includes(value) ? value : null;
}

export function normalizeGateState(value) {
    return GATE_STATES.includes(value) ? value : null;
}

export function kindLabelOf(kind) {
    return GATE_KIND_LABELS[kind] || GATE_UNKNOWN_TEXT;
}

export function stateLabelOf(state) {
    return GATE_STATE_LABELS[state] || GATE_UNKNOWN_TEXT;
}

function toneOf(state) {
    const spec = GATE_STATE_SPECS.find(item => item.state === state);
    return spec ? spec.tone : 'var(--gw-text-muted)';
}

// 规范化单个门禁：缺 gate_id 一律丢弃；kind/state 非法保留为 null 由渲染层兜底。
export function normalizeGate(raw) {
    if (!raw || typeof raw !== 'object' || Array.isArray(raw)) return null;
    const gateId = firstNonEmptyText(raw.gate_id);
    if (!gateId) return null;
    return {
        gate_id: gateId,
        run_id: firstNonEmptyText(raw.run_id) || '',
        kind: normalizeGateKind(raw.kind),
        state: normalizeGateState(raw.state),
        version: normalizeVersion(raw.version),
        summary: firstNonEmptyText(raw.summary) || '',
    };
}

// 接受 {gates: [...]} 或直接数组；非对象、非法条目一律丢弃，不抛异常。
export function normalizeGates(payload) {
    const list = Array.isArray(payload)
        ? payload
        : (payload && typeof payload === 'object' && Array.isArray(payload.gates) ? payload.gates : []);
    const result = [];
    for (const item of list) {
        const gate = normalizeGate(item);
        if (gate) result.push(gate);
    }
    return result;
}

// 只有明确处于 pending 的门禁可操作；未知状态视为不可操作（fail closed）。
export function gateIsDecidable(gate) {
    const normalized = normalizeGate(gate);
    return Boolean(normalized) && normalized.state === 'pending';
}

// 确认通过可留空（回填固定文案）；要求修改与退回必须给出理由。
export function resolveGateReason(input, decision) {
    const trimmed = typeof input === 'string' ? input.trim() : '';
    if (trimmed) return trimmed.slice(0, GATE_REASON_MAX_LENGTH);
    if (decision === 'approve') return GATE_DECISION_LABELS.approve;
    return null;
}

function readView(views, gateId) {
    if (!views || typeof views !== 'object') return {};
    const view = typeof views.get === 'function' ? views.get(gateId) : views[gateId];
    return view && typeof view === 'object' ? view : {};
}

function renderActions(phase, pendingDecision) {
    const buttons = GATE_DECISION_SPECS.map(spec => {
        const pending = phase === GATE_PHASES.submitting && pendingDecision === spec.decision;
        const label = pending ? `${spec.label}中…` : spec.label;
        const disabled = phase === GATE_PHASES.submitting ? ' disabled' : '';
        return `<button type="button" class="${spec.buttonClass}" data-gate-decision="${spec.decision}"${disabled}>${escapeHtml(label)}</button>`;
    });
    return `<div class="gw-gate-actions" style="display:flex;flex-wrap:wrap;gap:6px;">${buttons.join('')}</div>`;
}

function renderFeedback(phase, view, normalized, decidable) {
    if (phase === GATE_PHASES.submitting) {
        const label = GATE_DECISION_LABELS[view.pendingDecision] || GATE_UNKNOWN_TEXT;
        return `<p class="gw-gate-feedback" style="font-size:var(--gw-type-body-sm);color:var(--gw-warning);">正在提交「${escapeHtml(label)}」…</p>`;
    }
    if (phase === GATE_PHASES.error) {
        const message = typeof view.message === 'string' && view.message.trim() ? view.message : GATE_UNKNOWN_TEXT;
        return `<p class="gw-gate-feedback" style="font-size:var(--gw-type-body-sm);color:var(--gw-error);">提交失败：${escapeHtml(message)}</p>`;
    }
    if (!decidable) {
        return `<p class="gw-gate-feedback" style="font-size:var(--gw-type-body-sm);color:var(--gw-text-muted);">门禁已处理：${escapeHtml(stateLabelOf(normalized.state))}</p>`;
    }
    return '';
}

// 渲染单张门禁确认卡；非法输入返回空串（不占版面、不白屏）。
export function renderGateCard(gate, view = {}) {
    const normalized = normalizeGate(gate);
    if (!normalized) return '';
    const spec = view && typeof view === 'object' ? view : {};
    const phase = GATE_PHASES[spec.phase] || GATE_PHASES.idle;
    const pendingDecision = GATE_DECISIONS.includes(spec.pendingDecision) ? spec.pendingDecision : '';
    const reason = typeof spec.reason === 'string' ? spec.reason.slice(0, GATE_REASON_MAX_LENGTH) : '';
    const decidable = normalized.state === 'pending';
    const kindLabel = kindLabelOf(normalized.kind);
    const stateLabel = stateLabelOf(normalized.state);
    const editor = decidable
        ? `<label class="gw-gate-reason" style="display:block;font-size:var(--gw-type-body-sm);color:var(--gw-text-muted);">决定理由（确认通过可留空，要求修改与退回必填）<textarea data-gate-reason="${escapeHtml(normalized.gate_id)}" rows="2" maxlength="${GATE_REASON_MAX_LENGTH}" class="block w-full mt-1 rounded bg-black/50 border border-white/10 px-2 py-1 text-slate-100">${escapeHtml(reason)}</textarea></label>`
        : '';
    const actions = decidable ? renderActions(phase, pendingDecision) : '';
    const feedback = renderFeedback(phase, spec, normalized, decidable);
    return `<section class="gw-gate-card" data-gate-id="${escapeHtml(normalized.gate_id)}" data-gate-kind="${escapeHtml(normalized.kind || '')}" data-gate-state="${escapeHtml(normalized.state || '')}" data-gate-version="${normalized.version}" data-gate-phase="${escapeHtml(phase)}" style="background:var(--gw-surface-card);border:1px solid var(--gw-border);border-radius:12px;padding:10px;display:flex;flex-direction:column;gap:8px;">`
        + `<div class="gw-gate-card-head" style="display:flex;justify-content:space-between;align-items:center;gap:8px;">`
        + `<strong style="font-size:var(--gw-type-body-sm);color:var(--gold-light);">${escapeHtml(kindLabel)}门禁</strong>`
        + `<span style="font-size:var(--gw-type-body-sm);color:${toneOf(normalized.state)};">${escapeHtml(stateLabel)}</span>`
        + `</div>`
        + `<p class="gw-gate-summary" style="font-size:var(--gw-type-body-sm);color:var(--gw-text);white-space:pre-wrap;word-break:break-word;">${escapeHtml(normalized.summary || `${kindLabel}阶段摘要不可用`)}</p>`
        + editor
        + actions
        + feedback
        + `</section>`;
}

// 渲染门禁卡片列表；views 支持 Map 或普通对象，按 gate_id 取相位。
export function renderGateCards(gates, views = {}) {
    return normalizeGates(gates).map(gate => renderGateCard(gate, readView(views, gate.gate_id))).join('');
}

// 事件委托解析：命中决策按钮时返回 {gateId, decision}，否则返回 null。
export function resolveGateDecisionClick(event) {
    const button = event?.target?.closest?.('[data-gate-decision]');
    if (!button) return null;
    const card = button.closest?.('[data-gate-id]') || null;
    const gateId = card?.getAttribute?.('data-gate-id') || '';
    const decision = button.getAttribute?.('data-gate-decision') || '';
    if (!gateId || !GATE_DECISIONS.includes(decision)) return null;
    return { gateId, decision };
}

function gateCollectionUrl(runId, apiBase = '') {
    return `${apiBase}/api/agent/runs/${encodeURIComponent(runId)}/gates`;
}

function gateDecisionUrl(runId, gateId, apiBase = '') {
    return `${apiBase}/api/agent/runs/${encodeURIComponent(runId)}/gates/${encodeURIComponent(gateId)}/decision`;
}

// 网络入口一律显式注入：只有完全不传 fetchImpl 时才回退到运行环境自带的 fetch，
// 传入 null 等非函数值表示明确禁用网络，避免测试误触真实请求。
function pickFetch(fetchImpl) {
    if (typeof fetchImpl === 'function') return fetchImpl;
    if (fetchImpl === undefined && typeof globalThis.fetch === 'function') return globalThis.fetch;
    return null;
}

// 统一读取服务端错误包 {"detail": {"code": ..., "message": ...}}，拿不到时退化为 HTTP 状态。
async function readErrorDetail(response) {
    if (!response || typeof response.json !== 'function') return '服务端没有返回结果';
    let body = null;
    try {
        body = await response.json();
    } catch (_) {
        body = null;
    }
    const detail = body?.detail;
    const message = typeof detail === 'string' ? detail : detail?.message;
    if (typeof message === 'string' && message.trim()) return message;
    return `HTTP ${response?.status ?? 0}`;
}

function assertText(value, label) {
    if (typeof value !== 'string' || !value.trim()) throw new Error(`缺少${label}`);
    return value.trim();
}

// 读取任务的门禁列表；失败时抛出带 status 的 Error，由宿主渲染错误态。
export async function loadGates({ runId, apiBase = '', fetchImpl = null } = {}) {
    const runner = pickFetch(fetchImpl);
    if (!runner) throw new Error('当前环境没有可用的网络接口');
    const id = assertText(runId, '任务编号');
    const response = await runner(gateCollectionUrl(id, apiBase), {
        method: 'GET',
        credentials: 'same-origin',
        cache: 'no-store'
    });
    if (!response || !response.ok) {
        const error = new Error(await readErrorDetail(response));
        error.status = response?.status ?? 0;
        throw error;
    }
    let data = null;
    try {
        data = await response.json();
    } catch (_) {
        data = null;
    }
    return normalizeGates(data);
}

// 提交门禁决策；返回服务端回写后的门禁（拿不到时返回 null），失败抛出带 status 的 Error。
export async function submitGateDecision({
    runId,
    gateId,
    decision,
    reason = '',
    expectedVersion,
    idempotencyKey = '',
    requestId = '',
    apiBase = '',
    fetchImpl = null,
} = {}) {
    const id = assertText(runId, '任务编号');
    const gate = assertText(gateId, '门禁编号');
    if (!GATE_DECISIONS.includes(decision)) throw new Error(`不支持的门禁决策：${String(decision ?? '')}`);
    if (!Number.isInteger(expectedVersion) || expectedVersion < 0) throw new Error('门禁版本缺失，请刷新后重试');
    const runner = pickFetch(fetchImpl);
    if (!runner) throw new Error('当前环境没有可用的网络接口');
    const response = await runner(gateDecisionUrl(id, gate, apiBase), {
        method: 'POST',
        credentials: 'same-origin',
        cache: 'no-store',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            expected_version: expectedVersion,
            decision,
            reason: String(reason ?? '').slice(0, GATE_REASON_MAX_LENGTH),
            idempotency_key: idempotencyKey,
            request_id: requestId
        })
    });
    if (!response || !response.ok) {
        const error = new Error(await readErrorDetail(response));
        error.status = response?.status ?? 0;
        throw error;
    }
    let data = null;
    try {
        data = await response.json();
    } catch (_) {
        data = null;
    }
    return normalizeGate(data?.gate ?? data);
}
