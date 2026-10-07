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
 * Pure classification helpers. The page owns selection and rendering state;
 * this module only derives stable filter keys, groups and search text.
 */
export function createAssetClassificationHelpers({groups = [], labels = {}} = {}) {
    const normalizedGroups = groups.map(group => ({
        ...group,
        dims: Array.isArray(group.dims) ? [...group.dims] : [],
    }));
    const groupByDimension = new Map(
        normalizedGroups.flatMap(group => group.dims.map(dimension => [dimension, group]))
    );
    const fallbackGroup = normalizedGroups.find(group => group.id === 'tags') || normalizedGroups[0] || null;
    const modelGroup = normalizedGroups.find(group => group.id === 'model') || fallbackGroup;

    function assetClassFilterKey(entry = {}) {
        const dimension = String(entry?.dimension || '').trim();
        const tag = String(entry?.tag || '').trim();
        return dimension && tag ? `${dimension}::${tag}` : '';
    }

    function parseAssetClassFilterKey(key = '') {
        const text = String(key || '');
        const index = text.indexOf('::');
        if (index < 0) return null;
        return {dimension: text.slice(0, index), tag: text.slice(index + 2)};
    }

    function assetClassificationChips(item = {}, limit = 10) {
        const flat = Array.isArray(item?.classification?.flat) ? item.classification.flat : [];
        return flat.slice(0, limit).map(entry => {
            const label = entry?.label || entry?.dimension || '分类';
            const tag = entry?.tag || '';
            const key = assetClassFilterKey(entry);
            return tag ? {label, tag, key, dimension: entry?.dimension || ''} : null;
        }).filter(Boolean);
    }

    function assetClassificationEntriesForItems(items = []) {
        const entriesByKey = new Map();
        (items || []).forEach(item => {
            const flat = Array.isArray(item?.classification?.flat) ? item.classification.flat : [];
            flat.forEach(entry => {
                const key = assetClassFilterKey(entry);
                if (!key) return;
                const current = entriesByKey.get(key) || {
                    key,
                    dimension: String(entry.dimension || ''),
                    label: String(entry.label || entry.dimension || '分类'),
                    tag: String(entry.tag || ''),
                    count: 0,
                };
                current.count += 1;
                entriesByKey.set(key, current);
            });
        });
        return [...entriesByKey.values()].sort((left, right) => {
            if (left.label !== right.label) {
                return left.label.localeCompare(right.label, 'zh-Hans-CN', {numeric: true, sensitivity: 'base'});
            }
            return right.count - left.count || left.tag.localeCompare(right.tag, 'zh-Hans-CN', {numeric: true, sensitivity: 'base'});
        });
    }

    function assetClassificationEntryGroup(entry = {}) {
        const dimension = String(entry?.dimension || '');
        if (dimension === 'subject' && /人|人物|模特|男|女|儿童|老人|青年|肖像|半身|全身/.test(String(entry?.tag || ''))) {
            return modelGroup;
        }
        return groupByDimension.get(dimension) || fallbackGroup;
    }

    function assetClassificationSearchText(item = {}) {
        const classification = item?.classification || {};
        const flat = Array.isArray(classification.flat) ? classification.flat : [];
        const flatText = flat.map(entry => {
            const group = assetClassificationEntryGroup(entry);
            return [group?.name, entry?.label, entry?.dimension, entry?.tag].filter(Boolean).join(' ');
        }).join(' ');
        const categoryText = classification.categories && typeof classification.categories === 'object'
            ? Object.entries(classification.categories).map(([key, values]) => {
                const group = groupByDimension.get(String(key || ''));
                const label = labels[key] || key;
                const list = Array.isArray(values) ? values : [values];
                return [group?.name, label, key, ...list].filter(Boolean).join(' ');
            }).join(' ')
            : '';
        const tags = Array.isArray(classification.tags) ? classification.tags.join(' ') : '';
        return [classification.summary, flatText, categoryText, tags].filter(Boolean).join(' ');
    }

    function groupedAssetClassificationEntries(entries = []) {
        const grouped = new Map(normalizedGroups.map(group => [group.id, {...group, count: 0, entries: []}]));
        (entries || []).forEach(entry => {
            const group = assetClassificationEntryGroup(entry);
            const bucket = grouped.get(group?.id) || grouped.get(fallbackGroup?.id);
            if (!bucket) return;
            bucket.entries.push(entry);
            bucket.count += Number(entry.count || 0);
        });
        return [...grouped.values()].filter(group => group.entries.length);
    }

    function assetClassificationGroupIdForFilter(key = '', entries = []) {
        const entry = (entries || []).find(value => value.key === key);
        return entry ? assetClassificationEntryGroup(entry)?.id || '' : '';
    }

    return {
        assetClassFilterKey,
        parseAssetClassFilterKey,
        assetClassificationChips,
        assetClassificationEntriesForItems,
        assetClassificationEntryGroup,
        assetClassificationSearchText,
        groupedAssetClassificationEntries,
        assetClassificationGroupIdForFilter,
    };
}
