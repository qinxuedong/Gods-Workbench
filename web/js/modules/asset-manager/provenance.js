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

// MVP2-T8（issue #383）资产详情 provenance 链展示。
// 只做纯渲染与导航分发：不发网络请求、不依赖框架、不生成任何 URL；
// 所有动态文本一律转义后输出，节点点击只把 (kind, id) 分发给宿主注入的
// 导航回调，由宿主决定路由去向（测试注入 mock 回调即可验证）。

export const PROVENANCE_UNKNOWN_TEXT = '未知';

// 链条五环定义：kind 用于导航分发与 DOM 标记，label 为固定中文文案。
const PROVENANCE_NODE_KINDS = [
    { kind: 'asset', label: '成片' },
    { kind: 'version', label: '版本' },
    { kind: 'task', label: '任务' },
    { kind: 'shot', label: '镜头' },
    { kind: 'reference', label: '参考素材' },
];

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

// 规范化来源链：仅接受对象；version 兼容 QIN-440 契约字段 artifact_version_id；
// reference_asset_ids 只保留非空字符串。链整体缺失时返回 null。
export function normalizeProvenance(item) {
    const provenance = item && typeof item === 'object' && !Array.isArray(item) ? item.provenance : null;
    if (!provenance || typeof provenance !== 'object' || Array.isArray(provenance)) return null;
    return {
        assetId: firstNonEmptyText(item.asset_id, item.id),
        version: firstNonEmptyText(provenance.version, provenance.artifact_version_id),
        taskId: firstNonEmptyText(provenance.task_id),
        shotId: firstNonEmptyText(provenance.shot_id),
        referenceAssetIds: Array.isArray(provenance.reference_asset_ids)
            ? provenance.reference_asset_ids.filter(id => typeof id === 'string' && id.trim()).map(id => id.trim())
            : [],
    };
}

// 展开为按链序排列的节点；缺失字段 known=false，渲染为「未知」且不可导航。
export function provenanceNodes(item) {
    const provenance = normalizeProvenance(item);
    if (!provenance) return [];
    const labels = Object.fromEntries(PROVENANCE_NODE_KINDS.map(spec => [spec.kind, spec.label]));
    const nodes = [
        { kind: 'asset', label: labels.asset, id: provenance.assetId, known: Boolean(provenance.assetId) },
        { kind: 'version', label: labels.version, id: provenance.version, known: Boolean(provenance.version) },
        { kind: 'task', label: labels.task, id: provenance.taskId, known: Boolean(provenance.taskId) },
        { kind: 'shot', label: labels.shot, id: provenance.shotId, known: Boolean(provenance.shotId) },
    ];
    if (provenance.referenceAssetIds.length) {
        for (const id of provenance.referenceAssetIds) {
            nodes.push({ kind: 'reference', label: labels.reference, id, known: true });
        }
    } else {
        nodes.push({ kind: 'reference', label: labels.reference, id: null, known: false });
    }
    return nodes;
}

function renderKnownNode(node) {
    return `<button class="prov-node" type="button" data-prov-kind="${escapeHtml(node.kind)}" data-prov-id="${escapeHtml(node.id)}" title="跳转到${escapeHtml(node.label)} ${escapeHtml(node.id)}" style="background:none;border:1px solid var(--line);border-radius:8px;padding:2px 8px;font:inherit;color:inherit;cursor:pointer;">${escapeHtml(node.label)}：${escapeHtml(node.id)}</button>`;
}

function renderUnknownNode(node) {
    return `<span class="prov-node prov-node-unknown" data-prov-kind="${escapeHtml(node.kind)}" title="${escapeHtml(node.label)}未知" style="border:1px dashed var(--line);border-radius:8px;padding:2px 8px;opacity:.65;">${escapeHtml(node.label)}：${PROVENANCE_UNKNOWN_TEXT}</span>`;
}

// 渲染来源链区块；资产没有来源链登记时返回空串（不占详情版面）。
export function renderProvenanceChain(item) {
    const nodes = provenanceNodes(item);
    if (!nodes.length) return '';
    const parts = nodes.map(node => (node.known ? renderKnownNode(node) : renderUnknownNode(node)));
    const body = parts.join('<span class="prov-arrow" aria-hidden="true" style="opacity:.5;">→</span>');
    return `<div class="detail-caption-card provenance-chain-card"><div class="detail-caption-head"><strong>来源链</strong></div><div class="detail-classification-body provenance-chain-body" style="flex-direction:row;flex-wrap:wrap;align-items:center;gap:6px;">${body}</div></div>`;
}

// 导航回调注册：宿主页面注入真实路由，测试注入 mock 回调；传非函数即清除。
let provenanceNavigateHandler = null;

export function setProvenanceNavigateHandler(handler) {
    provenanceNavigateHandler = typeof handler === 'function' ? handler : null;
}

export function getProvenanceNavigateHandler() {
    return provenanceNavigateHandler;
}

// 事件委托入口：命中已知节点时分发 (kind, id) 并返回 true；
// 未命中、未知节点或未注册回调一律返回 false，交回宿主继续处理。
export function handleProvenanceNodeClick(event) {
    const node = event?.target?.closest?.('[data-prov-kind]');
    if (!node) return false;
    const id = node.getAttribute('data-prov-id') || '';
    if (!id) return false;
    if (typeof provenanceNavigateHandler !== 'function') return false;
    provenanceNavigateHandler(node.getAttribute('data-prov-kind') || '', id);
    return true;
}
