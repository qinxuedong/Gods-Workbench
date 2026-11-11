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

// QIN-591：资产库延后域模块（legacy）。首屏不加载；经 GW_DEFERRED 注册函数面，
// 由主文件网关（gwCall/gwLoad）按需加载或在空闲时段预热。函数体除跨文件赋值点改写外保持原样。

import {
    ASSET_RACK_KNOWN_ROOTS_KEY,
    ASSET_RACK_ROOT_KEY,
    apiJsonResponse,
    assetRackAutomaticItems,
    assetRackBoard,
    assetRackBoardPool,
    assetRackCollapsed,
    assetRackExpandedFolders,
    assetRackFolderTree,
    assetRackRoots,
    boardFolderSelection,
    boardGroupSelection,
    boardHiddenFolders,
    boardReferenceCanvasSelection,
    boardSourceSelection,
    customAssetLibraryBaseItems,
    customBoardFolderIsHidden,
    escapeAttr,
    escapeHtml,
    registryAssetFolderPath,
    registryAssets,
    registryBoardCustomGroups,
    registryCollectionId,
    registryDiskFolderCache,
    registryDiskFolderPermissionDenied,
    registryFacetKindCount,
    registryFacets,
    registryGroupSimilar,
    registryHasMoreAssets,
    registryKind,
    registryNavFolder,
    registryNavFolderActive,
    registryRecentDays,
    registryRootId,
    registryStatus,
    render,
    renderRegistryFacetRow,
    renderRegistryProjectFacetSection,
    root,
    setStatus,
    storageSettingsState,
} from '../asset-manager.js';
import assetManagerApi from './api.js';
import {
    customBoardReferenceCanvases,
} from './media-canvas.js';
import {
    renderStorageSettingsModal,
    saveAssetRackExpandedFolders,
} from './settings.js';
import {
    renderRegistryTagGroups,
} from './tags.js';
import { GW_DEFERRED } from './state.js';

let registryFeatureBusyId = '';
function renderRegistryFeatureControl(feature){
    const iconName = feature.id === 'thumbnail' ? 'image' : feature.id === 'video_storyboard' ? 'grid-3x3' : feature.id === 'metadata' ? 'file-scan' : feature.id === 'fulltext' ? 'text-search' : feature.id === 'font' ? 'type' : feature.id === 'model' ? 'box' : 'refresh-cw';
    return `<button class="board-feature-control" type="button" role="switch" aria-checked="${Boolean(feature.enabled)}" data-registry-feature-toggle="${escapeAttr(feature.id || '')}" ${registryFeatureBusyId === feature.id ? 'disabled' : ''}><span class="registry-feature-switch ${feature.enabled ? 'is-on' : ''}"><i></i></span><span class="registry-feature-label"><i data-lucide="${iconName}"></i>${escapeHtml(feature.name || '')}</span></button>`;
}
function registryStackCount(item){
    if(!registryGroupSimilar || registryHasMoreAssets()) return 1;
    return registryAssets.filter(value => (value.similar_key || value.id) === (item.similar_key || item.id)).length;
}
async function toggleRegistryFeature(featureId){
    if(!featureId || registryFeatureBusyId) return;
    const features = Array.isArray(registryStatus?.overview?.features) ? registryStatus.overview.features : [];
    const current = features.find(feature => feature.id === featureId);
    if(!current) return;
    registryFeatureBusyId = featureId;
    render();
    if(storageSettingsState.open) renderStorageSettingsModal();
    try {
        const data = await apiJsonResponse(assetManagerApi.updateRegistryFeature(featureId, {enabled:!current.enabled}));
        const updated = data.feature || {...current, enabled:!current.enabled};
        registryStatus.overview.features = features.map(feature => feature.id === featureId ? {...feature,...updated} : feature);
        setStatus(`${updated.name || current.name}${updated.enabled ? '已开启' : '已关闭'}`);
    } finally {
        registryFeatureBusyId = '';
        render();
        if(storageSettingsState.open) renderStorageSettingsModal();
    }
}
function renderCustomBoardGroups(board, items=[]){
    const active = boardGroupSelection[board.id] || '';
    const availableIds = new Set(items.map(item => item.id));
    const groups = registryBoardCustomGroups(board);
    // 【QIN-527】无分组时不再输出整段灰字说明，入口由工具栏「加入分组」承担
    if(!groups.length) return '';
    return `<section class="registry-facet-section library-group-section">
        <div class="registry-facet-title"><strong>分组目录</strong><span>${groups.length}</span></div>
        <div class="library-group-list">
            <button class="library-group-row ${active ? '' : 'active'}" type="button" data-board-group="" data-board-id="${escapeAttr(board.id)}"><i data-lucide="layers-3"></i><span>全部分组</span><small>${items.length}</small></button>
            ${groups.map(group => {
                const count = (group.assetIds || []).filter(id => availableIds.has(id)).length;
                return `<button class="library-group-row ${active === group.id ? 'active' : ''}" type="button" data-board-group="${escapeAttr(group.id)}" data-board-id="${escapeAttr(board.id)}" title="${escapeAttr(group.name)}"><i data-lucide="bookmark"></i><span>${escapeHtml(group.name)}</span><small>${count}</small></button>`;
            }).join('')}
        </div>
    </section>`;
}
function renderCustomBoardReferenceCanvases(board, items=[]){
    const active = boardReferenceCanvasSelection[board.id] || '';
    const canvases = customBoardReferenceCanvases(board, items);
    // 【QIN-527】无参考画布时同样不再输出占位灰字
    if(!canvases.length) return '';
    return `<section class="registry-facet-section library-reference-section">
        <div class="registry-facet-title"><strong>参考画布</strong><span>${canvases.length}</span></div>
        <div class="library-group-list">
            ${canvases.map(canvas => `<button class="library-group-row ${active === canvas.id ? 'active' : ''}" type="button" data-board-reference-canvas="${escapeAttr(canvas.id)}" data-board-id="${escapeAttr(board.id)}" title="单击查看素材，双击进入完整画布"><i data-lucide="map"></i><span>${escapeHtml(canvas.title || '未命名参考画布')}</span><small>${canvas.boardAssetCount}</small></button>`).join('')}
        </div>
    </section>`;
}
function renderAssetSmartFilterContents(contextLabel='当前看板'){
    const classifiedCount = registryAssets.filter(item => (item.tags || []).length || item.metadata?.classification || item.classification?.summary).length;
    const fallbackKinds = [
        {id:'image', name:'image', count:registryFacetKindCount('image')},
        {id:'video', name:'video', count:registryFacetKindCount('video')},
        {id:'audio', name:'audio', count:registryFacetKindCount('audio')},
        {id:'document', name:'document', count:registryFacetKindCount('document')},
        {id:'model', name:'model', count:registryFacetKindCount('model')},
    ];
    const kinds = (registryFacets.kinds || []).length ? registryFacets.kinds : fallbackKinds;
    const roots = (registryFacets.roots || []).filter(item => item.availability !== 'removed');
    const collections = registryFacets.collections || [];
    return `
        <section class="registry-facet-section asset-smart-filter-section">
            <div class="registry-facet-title"><strong>快捷入口</strong></div>
            ${renderRegistryFacetRow({id:'7',name:'最近 7 天',count:''}, 'recent', String(registryRecentDays || ''))}
            ${renderRegistryFacetRow({id:'30',name:'最近 30 天',count:''}, 'recent', String(registryRecentDays || ''))}
        </section>
        ${renderRegistryProjectFacetSection()}
        <section class="registry-ai-overview asset-smart-filter-ai"><span><i data-lucide="sparkles"></i></span><div><strong>AI 分类与标签</strong><small>${classifiedCount} 个结果已包含智能信息</small></div></section>
        <section class="registry-facet-section asset-smart-filter-section"><div class="registry-facet-title"><strong>素材类型</strong></div>
            ${kinds.map(item => renderRegistryFacetRow(item,'kind',registryKind)).join('')}
        </section>
        <section class="registry-facet-section asset-smart-filter-section"><div class="registry-facet-title"><strong>存储来源</strong></div>
            ${roots.length ? roots.map(item => renderRegistryFacetRow(item,'root',registryRootId)).join('') : '<div class="tree-empty">暂无已登记来源</div>'}
        </section>
        <section class="registry-facet-section asset-smart-filter-section"><div class="registry-facet-title"><strong>集合</strong></div>
            ${collections.length ? collections.map(item => renderRegistryFacetRow(item,'collection',registryCollectionId)).join('') : '<div class="tree-empty">暂无集合</div>'}
        </section>
        <div class="registry-tag-facet-grid asset-smart-filter-tags">${renderRegistryTagGroups()}</div>
        <section class="registry-facet-section asset-smart-filter-context"><div class="registry-facet-title"><strong>当前页面</strong></div><div class="library-rule-row"><i data-lucide="panel-left"></i><span>${escapeHtml(contextLabel)}</span></div></section>
        <button type="button" class="hw-btn asset-smart-filter-clear" data-registry-clear="all"><i data-lucide="x-circle"></i><span>清空智能筛选</span></button>
    `;
}
function renderAssetSmartFilterPanel(contextLabel='当前看板'){
    return `<aside class="asset-panel asset-nav asset-smart-filter-panel" aria-label="智能筛选">
        <div class="panel-head"><div class="panel-title"><strong>智能筛选</strong><span>点击包含，减号排除</span></div><span class="library-board-mark"><i data-lucide="filter"></i></span></div>
        <div class="nav-scroll registry-facets asset-smart-filter-scroll">${renderAssetSmartFilterContents(contextLabel)}</div>
    </aside>`;
}
function assetRackRootOnlyCount(rootId){
    // 看板态保留「根目录」节点：只筛选未落在任何子目录的素材
    const board = assetRackBoard();
    if(!board) return 0;
    const root = String(rootId || '');
    const pool = root === '__automatic__' ? assetRackAutomaticItems(board) : assetRackBoardPool(board).filter(item => String(item.storage_root_id || '') === root);
    return pool.filter(item => !registryAssetFolderPath(item)).length;
}
function ensureAssetRackRootsExpanded(){
    // 【QIN-527 修复】新登记的根目录（刚建的看板来源、新索引的库）必须默认展开：
    // 旧逻辑只在 localStorage 为空时 seed 一次，此后 facets/看板晚于首次渲染返回的
    // 新根节点永远折叠，整棵树看起来「只有一个根目录」。
    // 已知根目录维持用户上次的手动折叠选择，不受影响。
    let known;
    try { known = new Set(JSON.parse(localStorage.getItem(ASSET_RACK_KNOWN_ROOTS_KEY) || '[]')); }
    catch(_) { known = new Set(); }
    let changed = false;
    assetRackRoots().forEach(root => {
        const id = String(root.id || '');
        if(!id || known.has(id)) return;
        known.add(id);
        assetRackExpandedFolders.add(`${id}::${ASSET_RACK_ROOT_KEY}`);
        changed = true;
    });
    if(!changed) return;
    try { localStorage.setItem(ASSET_RACK_KNOWN_ROOTS_KEY, JSON.stringify([...known])); } catch(_) {}
    saveAssetRackExpandedFolders();
}
function renderAssetRackTitle(){
    // 【QIN-533】智能筛选页下线：中栏头部只保留「目录层级」标题与收起按钮
    return `<div class="asset-rack-title"><i data-lucide="folder-tree"></i><strong>目录层级</strong></div>`;
}
function renderAssetRackRail(){
    const railButton = (attrs, icon, label, active=false) => `<button class="asset-rack-rail-btn ${active ? 'is-active' : ''}" type="button" ${attrs} title="${label}" aria-label="${label}"><i data-lucide="${icon}"></i></button>`;
    return `<div class="asset-rack-rail" aria-label="中栏图标条">
        ${railButton('data-asset-rack-expand', 'chevrons-right', '展开中栏')}
        ${railButton('data-asset-rack-expand', 'folder-tree', '目录层级', true)}
        ${railButton('data-registry-clear="all"', 'rotate-ccw', '清空筛选')}
    </div>`;
}
function renderAssetRackFolderToolbar(){
    const roots = assetRackRoots();
    const board = assetRackBoard();
    const selectedRoot = board ? String(boardSourceSelection[board.id] || '') : registryNavFolder.rootId;
    const targetRoot = selectedRoot || (roots.length === 1 ? roots[0].id : '');
    // 看板态复用既有「新建文件夹」弹窗（可选来源、创建实体目录），保持唯一写入链路
    const createAttrs = board
        ? `data-board-folder-create="${escapeAttr(board.id)}"`
        : `data-asset-rack-folder-create="${escapeAttr(targetRoot)}"`;
    const createTitle = board ? '新建文件夹' : '在根目录新建文件夹';
    return `<div class="asset-rack-tools" role="toolbar" aria-label="目录操作">
        <button class="asset-rack-tool" type="button" ${createAttrs} title="${createTitle}" aria-label="${createTitle}"><i data-lucide="folder-plus"></i></button>
        ${board ? `<button class="asset-rack-tool" type="button" data-board-source-settings="${escapeAttr(board.id)}" title="设置素材来源" aria-label="设置素材来源"><i data-lucide="settings-2"></i></button>` : ''}
        <button class="asset-rack-tool" type="button" data-asset-rack-expand-all title="展开或折叠全部文件夹" aria-label="展开或折叠全部文件夹"><i data-lucide="unfold-vertical"></i></button>
        <button class="asset-rack-tool" type="button" data-asset-rack-clear-folder ${registryNavFolderActive() ? '' : 'disabled'} title="清除目录过滤" aria-label="清除目录过滤"><i data-lucide="rotate-ccw"></i></button>
    </div>`;
}
function renderAssetRackFolderNode(rootId, node, depth=0){
    const root = String(rootId || '');
    const key = `${root}::${node.path}`;
    const board = assetRackBoard();
    const active = board
        ? String(boardSourceSelection[board.id] || '') === root && String(boardFolderSelection[board.id] || '') === node.path
        : registryNavFolder.rootId === root && registryNavFolder.path === node.path;
    const expanded = assetRackExpandedFolders.has(key);
    // QIN-542 AC1/AC2：全部节点保留展开入口（叶子展开触发逐层懒加载）；
    // 已确认的空目录（已加载且无子目录、无权限问题）标明「空」徽标
    const diskEmpty = registryDiskFolderCache.has(`${root}::${node.path}`)
        && !(registryDiskFolderCache.get(`${root}::${node.path}`) || []).length
        && !node.children.length
        && !node.hasIndexedAssets
        && !registryDiskFolderPermissionDenied;
    const emptyBadge = diskEmpty ? '<em title="空目录：无已索引素材与子目录">空</em>' : '';
    const selfBadge = Number(node.selfCount || 0) > 0 ? `<em title="仅本层文件数">本层${Number(node.selfCount)}</em>` : '';
    const expandable = true;
    // 看板态保留既有「隐藏此文件夹」能力，压成行内单图标，不新增工具栏占位
    const hidden = board ? customBoardFolderIsHidden(board.id, node.path) : false;
    const directlyHidden = board ? (boardHiddenFolders[board.id] || []).includes(node.path) : false;
    const eye = board && node.path !== '__root__'
        ? `<button class="asset-rack-folder-eye ${hidden ? 'is-hidden' : ''}" type="button" data-board-folder-visibility="${escapeAttr(node.path)}" data-board-id="${escapeAttr(board.id)}" aria-pressed="${directlyHidden}" title="${directlyHidden ? '显示此文件夹内素材' : hidden ? '上级文件夹已隐藏' : '隐藏此文件夹内素材'}"><i data-lucide="${hidden ? 'eye-off' : 'eye'}"></i></button>`
        : '';
    return `<div class="asset-rack-branch">
        <div class="asset-rack-node ${active ? 'is-active' : ''}" style="--rack-depth:${Math.min(depth, 6)}">
            <button class="asset-rack-twisty" type="button" data-asset-rack-toggle="${escapeAttr(key)}" aria-expanded="${expanded}" title="${expanded ? '折叠文件夹' : '展开文件夹'}"><i data-lucide="${expanded ? 'chevron-down' : 'chevron-right'}"></i></button>
            <button class="asset-rack-folder" type="button" data-asset-rack-folder-root="${escapeAttr(root)}" data-asset-rack-folder-path="${escapeAttr(node.path)}" aria-pressed="${active}" title="${escapeAttr(node.path)}"><i data-lucide="${active ? 'folder-open' : 'folder'}"></i><span>${escapeHtml(node.name)}</span><em>${Number(node.count || 0)}</em>${selfBadge}${emptyBadge}</button>
            ${eye}
        </div>
        ${expandable && expanded ? `<div class="asset-rack-children">${node.children.map(child => renderAssetRackFolderNode(root, child, depth + 1)).join('')}</div>` : ''}
    </div>`;
}
function renderAssetRackFolderRoots(){
    const roots = assetRackRoots();
    const board = assetRackBoard();
    if(!roots.length){
        // AC4：空状态不只给一行灰字，直接给可点的登记入口，闭环新手引导
        return `<div class="asset-rack-empty-guide"><p>尚未登记素材库根目录</p><small>登记后即可按真实磁盘子目录浏览</small><button class="hw-btn hw-btn-gold asset-rack-empty-action" type="button" data-asset-rack-register-root><i data-lucide="folder-plus"></i><span>登记素材根目录</span></button></div>`;
    }
    return roots.map(root => {
        const key = `${root.id}::${ASSET_RACK_ROOT_KEY}`;
        const expanded = assetRackExpandedFolders.has(key);
        const active = board
            ? String(boardSourceSelection[board.id] || '') === root.id && !String(boardFolderSelection[board.id] || '')
            : registryNavFolder.rootId === root.id && !registryNavFolder.path;
        const tree = expanded ? assetRackFolderTree(root.id) : [];
        const rootOnly = board ? assetRackRootOnlyCount(root.id) : 0;
        return `<section class="asset-rack-root ${active ? 'is-active' : ''}">
            <div class="asset-rack-root-row">
                <button class="asset-rack-twisty" type="button" data-asset-rack-toggle="${escapeAttr(key)}" aria-expanded="${expanded}" title="${expanded ? '折叠来源' : '展开来源'}"><i data-lucide="${expanded ? 'chevron-down' : 'chevron-right'}"></i></button>
                <button class="asset-rack-root-btn" type="button" data-asset-rack-folder-root="${escapeAttr(root.id)}" data-asset-rack-folder-path="" aria-pressed="${active}" title="${escapeAttr(root.name)}"><i data-lucide="hard-drive"></i><span>${escapeHtml(root.name)}</span><em>${Number(root.count || 0)}</em></button>
            </div>
            ${root.availability === 'offline' ? '<div class="asset-rack-root-offline">来源离线</div>' : ''}
            ${(tree.length || rootOnly) ? `<div class="asset-rack-children">
                ${rootOnly ? renderAssetRackFolderNode(root.id, {path:'__root__', name:'根目录', count:rootOnly, children:[]}) : ''}
                ${tree.map(node => renderAssetRackFolderNode(root.id, node)).join('')}
            </div>` : ''}
        </section>`;
    }).join('');
}
function renderAssetRackBoardExtras(board){
    // QIN-533：看板既有能力（分组目录 / 参考画布）随智能筛选页下线迁到目录层级页底部
    const items = customAssetLibraryBaseItems(board);
    const extras = `${renderCustomBoardGroups(board, items)}${renderCustomBoardReferenceCanvases(board, items)}`;
    return extras ? `<div class="asset-rack-board-extras">${extras}</div>` : '';
}
function renderAssetRackFolders(){
    ensureAssetRackRootsExpanded();
    const board = assetRackBoard();
    // QIN-542 AC4：非 admin 浏览磁盘子目录被拒（403）时显式提示，不再静默
    const permissionHint = registryDiskFolderPermissionDenied
        ? '<div class="asset-rack-root-offline" role="note" title="磁盘子目录浏览接口仅管理员会话可读；目录树以已索引素材与登记目录为准">磁盘子目录浏览需要管理员权限，部分层级以已索引素材与登记目录为准</div>'
        : '';
    return `<div class="asset-rack-pane" role="tabpanel" aria-label="目录层级">
        ${renderAssetRackFolderToolbar()}
        <div class="asset-rack-tree" role="tree" aria-label="素材库目录层级">${renderAssetRackFolderRoots()}</div>
        ${permissionHint}
        ${board ? renderAssetRackBoardExtras(board) : ''}
    </div>`;
}
function renderAssetRack(extraAttrs='', extraClass=''){
    const collapsed = assetRackCollapsed;
    return `<aside class="asset-panel asset-nav registry-nav asset-rack ${extraClass} ${collapsed ? 'is-collapsed' : ''}" ${extraAttrs}>
        <header class="asset-rack-head">
            ${renderAssetRackTitle()}
            <button class="asset-rack-collapse" type="button" data-asset-rack-collapse title="收起中栏为图标条" aria-label="收起中栏为图标条"><i data-lucide="chevrons-left"></i></button>
        </header>
        ${renderAssetRackRail()}
        <div class="nav-scroll registry-facets asset-rack-body">${collapsed ? '' : renderAssetRackFolders()}</div>
    </aside>`;
}
function renderSystemBoardRack(tools='', body=''){
    const collapsed = assetRackCollapsed;
    return `<aside class="asset-panel asset-nav asset-rack system-board-rack ${collapsed ? 'is-collapsed' : ''}">
        <header class="asset-rack-head">
            ${renderAssetRackTitle()}
            <button class="asset-rack-collapse" type="button" data-asset-rack-collapse title="收起中栏为图标条" aria-label="收起中栏为图标条"><i data-lucide="chevrons-left"></i></button>
        </header>
        ${renderAssetRackRail()}
        <div class="nav-scroll registry-facets asset-rack-body">${collapsed ? '' : `${tools ? `<div class="system-rack-tools" role="toolbar">${tools}</div>` : ''}${body}`}</div>
    </aside>`;
}

// 【QIN-591】域函数面注册：主文件 gwCall('legacy', fn) 经由此对象分派。
GW_DEFERRED['legacy'] = {
    toggleRegistryFeature,
};

export {
    renderRegistryFeatureControl,
};
