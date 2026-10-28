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

// QIN-437（issue #386）收集来源溯源展示 + 按来源删除入口。
// 只做纯渲染与事件分发：不发网络请求、不依赖框架、不生成任何 URL；
// 来源 URL 一律作为纯文本转义输出（不输出 href，防 XSS）；
// 删除入口只把 asset_id 交回宿主，由宿主复用既有回收站接口完成删除。

export const COLLECT_UNKNOWN_TEXT = '未知';
export const COLLECT_DELETE_ATTRIBUTE = 'data-collect-delete';

function escapeHtml(value = '') {
    return String(value)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
}

// 仅接受非空字符串或有限数值，统一转为去空白文本；其余一律视为缺失。
function firstText(value) {
    if (typeof value === 'string') {
        const trimmed = value.trim();
        return trimmed || null;
    }
    if (typeof value === 'number' && Number.isFinite(value)) return String(value);
    return null;
}

// 收集时间：后端为 UTC ISO 8601（`2026-10-28T07:18:56Z`），按本地时区展示到分钟；
// 无法解析时原样回显，空值由调用方显示「未知」，任何异常都不抛出。
export function formatCollectedAt(value) {
    const text = firstText(value);
    if (!text) return null;
    const parsed = new Date(text);
    if (Number.isNaN(parsed.getTime())) return text;
    const pad = number => String(number).padStart(2, '0');
    return `${parsed.getFullYear()}-${pad(parsed.getMonth() + 1)}-${pad(parsed.getDate())} ${pad(parsed.getHours())}:${pad(parsed.getMinutes())}`;
}

// 规范化收集来源：读取 metadata.collect_sources（兼容顶层 collect_sources）。
// 非法条目直接跳过；整条来源数组缺失时返回空数组。
export function normalizeCollectSources(item) {
    if (!item || typeof item !== 'object' || Array.isArray(item)) return [];
    const metadata = item.metadata && typeof item.metadata === 'object' && !Array.isArray(item.metadata)
        ? item.metadata
        : null;
    const raw = metadata && Array.isArray(metadata.collect_sources)
        ? metadata.collect_sources
        : (Array.isArray(item.collect_sources) ? item.collect_sources : []);
    const sources = [];
    for (const entry of raw) {
        if (!entry || typeof entry !== 'object' || Array.isArray(entry)) continue;
        sources.push({
            url: firstText(entry.url),
            title: firstText(entry.title),
            capturedAt: firstText(entry.captured_at),
        });
    }
    return sources;
}

// 是否为收集入库资产：有收集来源或有收集内容哈希即视为收集产物。
export function isCollectedAsset(item) {
    if (!item || typeof item !== 'object' || Array.isArray(item)) return false;
    if (normalizeCollectSources(item).length) return true;
    const metadata = item.metadata;
    return Boolean(
        metadata && typeof metadata === 'object' && !Array.isArray(metadata)
        && firstText(metadata.collect_sha256),
    );
}

function renderSourceRow(source, index) {
    const url = source.url
        ? `<span class="collect-source-url" data-collect-url>${escapeHtml(source.url)}</span>`
        : `<span class="collect-source-url collect-source-unknown" data-collect-url>${COLLECT_UNKNOWN_TEXT}</span>`;
    const title = source.title
        ? escapeHtml(source.title)
        : COLLECT_UNKNOWN_TEXT;
    const capturedAt = formatCollectedAt(source.capturedAt) || COLLECT_UNKNOWN_TEXT;
    return `<div class="collect-source-row" data-collect-source-index="${index}"><div class="detail-meta"><span>来源 URL</span><strong>${url}</strong></div><div class="detail-meta"><span>页面标题</span><strong>${title}</strong></div><div class="detail-meta"><span>收集时间</span><strong>${escapeHtml(capturedAt)}</strong></div></div>`;
}

// 渲染收集来源卡片；非收集资产或数据异常时返回空串（不占详情版面）。
// `assetId` 用于「删除本次收集」入口，缺失时不渲染删除按钮（避免无目标操作）。
export function renderCollectProvenance(item) {
    const sources = normalizeCollectSources(item);
    if (!sources.length && !isCollectedAsset(item)) return '';
    const assetId = firstText(item && (item.id || item.asset_id));
    const rows = sources.length
        ? sources.map(renderSourceRow).join('')
        : `<div class="collect-source-row collect-source-unknown">${COLLECT_UNKNOWN_TEXT}</div>`;
    const deleteButton = assetId
        ? `<button class="hw-btn hw-btn-danger" type="button" ${COLLECT_DELETE_ATTRIBUTE}="${escapeHtml(assetId)}" title="通过回收站删除本次收集的素材"><i data-lucide="trash-2"></i><span>删除本次收集</span></button>`
        : '';
    return `<div class="detail-caption-card collect-provenance-card"><div class="detail-caption-head"><strong>收集来源</strong>${deleteButton}</div><div class="detail-classification-body collect-provenance-body">${rows}</div></div>`;
}

// 事件委托入口：命中「删除本次收集」且携带非空 asset_id 时返回该 ID，否则返回空串。
// 本模块不直接发起删除，调用方必须复用既有回收站接口。
export function resolveCollectDeleteAssetId(event) {
    const target = event?.target;
    const element = target && typeof target.closest === 'function'
        ? target.closest(`[${COLLECT_DELETE_ATTRIBUTE}]`)
        : null;
    if (!element) return '';
    const assetId = firstText(element.getAttribute?.(COLLECT_DELETE_ATTRIBUTE));
    return assetId || '';
}
