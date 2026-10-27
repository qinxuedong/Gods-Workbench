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

export function escapeHtml(value = '') {
    return String(value ?? '').replace(/[&<>"']/g, match => ({
        '&': '&amp;',
        '<': '&lt;',
        '>': '&gt;',
        '"': '&quot;',
        "'": '&#39;'
    }[match]));
}

export function escapeAttr(value = '') {
    return escapeHtml(value);
}

export function parseProvenance(item) {
    try {
        if (!item || typeof item !== 'object') {
            return {
                asset_id: '',
                version: '',
                task_id: '',
                shot_id: '',
                reference_asset_ids: [],
            };
        }

        const provenance = (item.provenance && typeof item.provenance === 'object')
            ? item.provenance
            : ((item.metadata?.provenance && typeof item.metadata.provenance === 'object') ? item.metadata.provenance : {});

        const assetId = String(item.asset_id || item.id || item.assetId || provenance.asset_id || '').trim();
        const version = String(provenance.version || item.version || '').trim();
        const taskId = String(provenance.task_id || provenance.taskId || item.task_id || '').trim();
        const shotId = String(provenance.shot_id || provenance.shotId || item.shot_id || '').trim();

        let rawRefs = provenance.reference_asset_ids || provenance.referenceAssetIds || item.reference_asset_ids;
        if (!Array.isArray(rawRefs)) {
            rawRefs = [];
        }
        const referenceAssetIds = rawRefs
            .map(id => String(id || '').trim())
            .filter(Boolean);

        return {
            asset_id: assetId,
            version: version,
            task_id: taskId,
            shot_id: shotId,
            reference_asset_ids: referenceAssetIds,
        };
    } catch (_) {
        return {
            asset_id: '',
            version: '',
            task_id: '',
            shot_id: '',
            reference_asset_ids: [],
        };
    }
}

export function sanitizeProvenanceUrl(type, id) {
    if (!id || typeof id !== 'string') id = String(id || '');
    const trimmed = id.trim();
    if (!trimmed) return '#';

    // Prevent unsafe URL schemes if id starts with pseudo protocols
    if (/^(?:javascript|data|vbscript):/i.test(trimmed)) {
        return '#';
    }

    const safeId = encodeURIComponent(trimmed);
    switch (type) {
        case 'asset':
            return `#/asset/${safeId}`;
        case 'version':
            return `#/version/${safeId}`;
        case 'task':
            return `#/task/${safeId}`;
        case 'shot':
            return `#/shot/${safeId}`;
        case 'reference':
            return `#/asset/${safeId}`;
        default:
            return '#';
    }
}

export function renderProvenanceChain(item, options = {}) {
    try {
        const parsed = parseProvenance(item);
        const UNKNOWN = '未知';

        const nodes = [
            {
                label: '成片',
                type: 'asset',
                values: parsed.asset_id ? [{ id: parsed.asset_id, text: parsed.asset_id }] : [],
            },
            {
                label: '版本',
                type: 'version',
                values: parsed.version ? [{ id: parsed.version, text: parsed.version }] : [],
            },
            {
                label: '任务',
                type: 'task',
                values: parsed.task_id ? [{ id: parsed.task_id, text: parsed.task_id }] : [],
            },
            {
                label: '镜头',
                type: 'shot',
                values: parsed.shot_id ? [{ id: parsed.shot_id, text: parsed.shot_id }] : [],
            },
            {
                label: '参考素材',
                type: 'reference',
                values: parsed.reference_asset_ids.length > 0
                    ? parsed.reference_asset_ids.map(id => ({ id, text: id }))
                    : [],
            },
        ];

        const renderedNodes = nodes.map((node, index) => {
            const labelHtml = `<span class="provenance-node-label">${escapeHtml(node.label)}</span>`;
            let valueHtml = '';

            if (node.values.length === 0) {
                valueHtml = `<span class="provenance-node-value is-unknown" data-provenance-type="${escapeAttr(node.type)}">${UNKNOWN}</span>`;
            } else {
                const valItems = node.values.map(val => {
                    const url = typeof options.buildUrl === 'function'
                        ? options.buildUrl(node.type, val.id)
                        : sanitizeProvenanceUrl(node.type, val.id);
                    const safeHref = escapeAttr(url);
                    return `<a class="provenance-node-link" href="${safeHref}" data-provenance-type="${escapeAttr(node.type)}" data-provenance-id="${escapeAttr(val.id)}">${escapeHtml(val.text)}</a>`;
                });
                valueHtml = `<span class="provenance-node-value">${valItems.join(', ')}</span>`;
            }

            const arrow = index < nodes.length - 1 ? `<span class="provenance-chain-arrow">→</span>` : '';
            return `<div class="provenance-node" data-provenance-step="${escapeAttr(node.type)}">${labelHtml}: ${valueHtml}</div>${arrow}`;
        });

        return `<div class="registry-detail-block provenance-chain-block" data-provenance-chain>
            <strong>Provenance 溯源链</strong>
            <div class="provenance-chain-list">
                ${renderedNodes.join('\n                ')}
            </div>
        </div>`;
    } catch (_) {
        return `<div class="registry-detail-block provenance-chain-block" data-provenance-chain>
            <strong>Provenance 溯源链</strong>
            <div class="provenance-chain-list">
                <span class="provenance-node-value is-unknown">未知</span>
            </div>
        </div>`;
    }
}

export function handleProvenanceClick(event, onNavigate) {
    try {
        const link = event?.target?.closest?.('[data-provenance-type][data-provenance-id]');
        if (!link) return false;
        const type = link.getAttribute('data-provenance-type');
        const id = link.getAttribute('data-provenance-id');
        if (!type || !id) return false;

        if (typeof onNavigate === 'function') {
            event.preventDefault?.();
            onNavigate({ type, id, element: link });
            return true;
        }
        return false;
    } catch (_) {
        return false;
    }
}
