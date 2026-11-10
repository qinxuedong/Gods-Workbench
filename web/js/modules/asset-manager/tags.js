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

// QIN-591：资产库延后域模块（tags）。首屏不加载；经 GW_DEFERRED 注册函数面，
// 由主文件网关（gwCall/gwLoad）按需加载或在空闲时段预热。函数体除跨文件赋值点改写外保持原样。

import {
    ASSET_CLASS_GROUPS,
    apiJsonResponse,
    detailViewerState,
    escapeAttr,
    escapeHtml,
    mergeRegistryAssetUpdate,
    parseRegistryTagNames,
    refreshRegistryFacetData,
    registryExcludeTags,
    registryFacets,
    registryIncludeTags,
    render,
    setRegistryTagEditorAssetId,
    setStatus,
} from '../asset-manager.js';
import {
    assetManagerApi,
} from './api.js';
import {
    renderDetailViewer,
} from './preview.js';
import { GW_DEFERRED } from './state.js';

function registryTagsByGroup(){
    const groups = new Map(ASSET_CLASS_GROUPS.map(group => [group.id, {...group, tags:[]} ]));
    (registryFacets.tags || []).forEach(tag => {
        const group = assetClassificationEntryGroup({dimension:tag.dimension, tag:tag.name});
        (groups.get(group?.id) || groups.get('tags')).tags.push(tag);
    });
    groups.forEach(group => group.tags.sort((a,b) => Number(b.count || 0) - Number(a.count || 0) || String(a.name).localeCompare(String(b.name),'zh')));
    return [...groups.values()].filter(group => group.tags.length);
}
async function addRegistryAssetTags(assetIds=[], names=[]){
    const cleanIds = [...new Set(assetIds.map(String).filter(Boolean))];
    const cleanNames = parseRegistryTagNames(Array.isArray(names) ? names.join(',') : names);
    if(!cleanIds.length || !cleanNames.length){ setStatus('请输入至少一个标签'); return false; }
    const data = await apiJsonResponse(assetManagerApi.addRegistryAssetTags({asset_ids:cleanIds,names:cleanNames,dimension:'tags'}));
    (data.assets || []).forEach(mergeRegistryAssetUpdate);
    await refreshRegistryFacetData();
    setRegistryTagEditorAssetId('');
    if(detailViewerState.open && detailViewerState.source === 'registry') renderDetailViewer();
    else render();
    setStatus(`已为 ${cleanIds.length} 个素材添加标签：${cleanNames.join('、')}`);
    return true;
}
async function deleteRegistryAssetTag(assetId, tagId){
    if(!assetId || !tagId) return;
    const data = await apiJsonResponse(assetManagerApi.deleteRegistryAssetTag(assetId, tagId));
    if(data.asset) mergeRegistryAssetUpdate(data.asset);
    await refreshRegistryFacetData();
    if(detailViewerState.open && detailViewerState.source === 'registry') renderDetailViewer();
    else render();
    setStatus('标签已删除');
}
function customBoardTagEntries(items=[]){
    const counts = new Map();
    items.forEach(item => (item.tags || []).forEach(tag => {
        const current = counts.get(tag.id) || {...tag, count:0};
        current.count += 1;
        counts.set(tag.id, current);
    }));
    return [...counts.values()].sort((a,b) => b.count - a.count).slice(0, 12);
}
function renderRegistryTagGroups(){
    return registryTagsByGroup().map(group => `<section class="registry-facet-section">
        <div class="registry-facet-title"><strong>${escapeHtml(group.name)}</strong><span>${group.tags.length}</span></div>
        <div class="registry-tag-list">${group.tags.slice(0, 30).map(tag => {
            const include = registryIncludeTags.has(tag.id);
            const exclude = registryExcludeTags.has(tag.id);
            return `<div class="registry-tag-row ${include ? 'include' : ''} ${exclude ? 'exclude' : ''}">
                <button type="button" data-registry-tag="${escapeAttr(tag.id)}" title="包含此标签"><span>${escapeHtml(tag.name)}</span><em>${Number(tag.count || 0)}</em></button>
                <button class="registry-tag-exclude" type="button" data-registry-tag-exclude="${escapeAttr(tag.id)}" title="排除此标签"><i data-lucide="minus"></i></button>
            </div>`;
        }).join('')}</div>
    </section>`).join('');
}
function detailViewerTags(item={}){
    const result = [];
    (Array.isArray(item.tags) ? item.tags : []).forEach(tag => result.push(typeof tag === 'string' ? tag : (tag.name || tag.tag || '')));
    const flat = item.classification?.flat || item.metadata?.classification?.flat || [];
    flat.forEach(entry => result.push(entry.tag || entry.name || ''));
    return [...new Set(result.map(String).map(value => value.trim()).filter(Boolean))].slice(0, 18);
}

// 【QIN-591】域函数面注册：主文件 gwCall('tags', fn) 经由此对象分派。
GW_DEFERRED['tags'] = {
    addRegistryAssetTags,
    deleteRegistryAssetTag,
};

export {
    addRegistryAssetTags,
    deleteRegistryAssetTag,
    detailViewerTags,
    renderRegistryTagGroups,
};
