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

// QIN-591：资产库延后域模块（governance）。首屏不加载；经 GW_DEFERRED 注册函数面，
// 由主文件网关（gwCall/gwLoad）按需加载或在空闲时段预热。函数体除跨文件赋值点改写外保持原样。

import {
    GOVERNANCE_INFORMATIONAL_KINDS,
    GOVERNANCE_SUGGESTION_GROUPS,
    GOVERNANCE_SUGGESTION_KIND_LABELS,
    apiJsonResponse,
    dedupConfirmError,
    dedupConfirmJob,
    dedupConfirmLoading,
    escapeAttr,
    escapeHtml,
    governanceDrawerOpen,
    governanceRefreshNotice,
    governanceSelectedSuggestionId,
    governanceSuggestionTotal,
    governanceSuggestionsData,
    governanceSuggestionsError,
    governanceSuggestionsLoading,
    governanceTriggerLabel,
    isRegistryBootstrapPending,
    openRegistryAssetFromGlobalSearch,
    refreshIcons,
    registryAssets,
    registryVisualType,
    root,
    setDedupConfirmError,
    setDedupConfirmJob,
    setDedupConfirmLoading,
    setGovernanceDrawerOpen,
    setGovernanceRefreshNotice,
    setGovernanceSelectedSuggestionId,
    setGovernanceSuggestionsData,
    setGovernanceSuggestionsError,
    setGovernanceSuggestionsLoading,
    setStatus,
    showAssetNotice,
} from '../asset-manager.js';
import assetManagerApi from './api.js';
import {
    markAssetFloatingSurface,
} from './preview.js';
import { GW_DEFERRED } from './state.js';

let governanceDrawerReturnFocus = null;
let dedupConfirmSeq = 0;
let dedupPollTimer = 0;
let dedupPollIntervalMs = 500;

function isAbortError(error){
    return Boolean(error && (error.name === 'AbortError' || error.code === 20));
}

function clearDedupPollTimer(){
    if(dedupPollTimer){
        clearTimeout(dedupPollTimer);
        dedupPollTimer = 0;
    }
}

async function loadGovernanceSuggestions(force=false, options={}){
    const { preserveExistingOnError = false } = options || {};
    if(governanceSuggestionsLoading && !force) return;
    if(!force && (governanceSuggestionsData || governanceSuggestionsError)) return;
    // 【QIN-585 AC2】首屏列表未落地前不发请求：治理建议不参与第一屏渲染，
    // 千级素材库上单次要跑数秒，会把首屏列表排在单进程服务端之后。
    // 排队后由 scheduleDeferredBootRequests 在列表终态后的空闲时段补发；
    // force（用户显式刷新）不受此闸门限制。
    // 按代际判定：首屏在途时用户刷新开了新一代，同样要等新一代落地才发。
    if(!force && isRegistryBootstrapPending()) return;
    const seq = (loadGovernanceSuggestions._seq = (loadGovernanceSuggestions._seq || 0) + 1);
    if(loadGovernanceSuggestions._controller){
        try { loadGovernanceSuggestions._controller.abort(); } catch(_) {}
    }
    const controller = typeof AbortController === 'function' ? new AbortController() : null;
    loadGovernanceSuggestions._controller = controller;
    const setNotice = typeof setGovernanceRefreshNotice === 'function' ? setGovernanceRefreshNotice : null;
    if(force && !preserveExistingOnError){
        setGovernanceSuggestionsData(null);
        setGovernanceSuggestionsError(null);
    }
    if(force && setNotice){
        setNotice('');
    }
    setGovernanceSuggestionsLoading(true);
    updateGovernanceTrigger();
    if(governanceDrawerOpen) renderGovernanceDrawer();
    try {
        const nextData = await apiJsonResponse(fetch('/api/asset-registry/governance/suggestions', {
            headers:{'Accept':'application/json'},
            signal: controller?.signal,
        }));
        if(seq !== loadGovernanceSuggestions._seq) return;
        setGovernanceSuggestionsData(nextData);
        setGovernanceSuggestionsError(null);
        if(setNotice) setNotice('');
    } catch(error) {
        const aborted = Boolean(error && (error.name === 'AbortError' || error.code === 20));
        if(seq !== loadGovernanceSuggestions._seq || aborted) return;
        if(preserveExistingOnError && governanceSuggestionsData){
            // AC12 写后刷新保护：保留已成功写入的结果，不以旧缓存覆盖或否定已成功写入
            if(setNotice) setNotice('写入已提交，但刷新建议清单失败：' + (error.message || '请稍后重试'));
        } else {
            setGovernanceSuggestionsData(null);
            setGovernanceSuggestionsError(error);
        }
    } finally {
        if(seq === loadGovernanceSuggestions._seq){
            loadGovernanceSuggestions._controller = null;
            setGovernanceSuggestionsLoading(false);
            updateGovernanceTrigger();
            if(governanceDrawerOpen) renderGovernanceDrawer();
        }
    }
}
async function refreshGovernanceSuggestions(){
    await loadGovernanceSuggestions(true);
    if(governanceSuggestionsError){
        setStatus(governanceSuggestionsError.status === 403 ? '需要治理权限' : (governanceSuggestionsError.message || '治理建议读取失败'));
    } else {
        setStatus('治理建议已刷新');
    }
}
function governanceSuggestionAssetIds(item={}){
    const memberIds = (Array.isArray(item.members) ? item.members : []).map(member => (
        typeof member === 'string' ? '' : String(member?.asset_id || '')
    ));
    const ids = [
        item.asset_id,
        ...(Array.isArray(item.asset_ids) ? item.asset_ids : []),
        item.left_asset_id,
        item.right_asset_id,
        ...memberIds,
    ].map(value => String(value || '').trim()).filter(Boolean);
    return [...new Set(ids)];
}
function governanceAssetType(assetId){
    const asset = registryAssets.find(item => String(item.id || item.asset_id || '') === String(assetId || ''));
    return asset ? registryVisualType(asset) : 'other';
}
function governanceAssetTypeForIds(assetIds=[]){
    const known = assetIds.map(governanceAssetType).find(type => type && type !== 'other');
    return known || 'other';
}
function updateGovernanceTrigger(){
    const total = governanceSuggestionTotal();
    const badgeText = governanceSuggestionsError ? '!' : governanceSuggestionsLoading ? '…' : governanceSuggestionsData ? String(total) : '—';
    root?.querySelectorAll?.('[data-governance-open]').forEach(button => {
        const badge = button.querySelector('[data-governance-total]');
        if(badge) badge.textContent = badgeText;
        button.classList.toggle('has-error', Boolean(governanceSuggestionsError));
        button.setAttribute('aria-label', governanceTriggerLabel());
        button.setAttribute('title', governanceTriggerLabel());
        button.setAttribute('aria-expanded', String(governanceDrawerOpen));
    });
}
function duplicateStatusBadge(item){
    const confirmed = item.dedup_status === 'confirmed';
    const cls = confirmed ? 'is-confirmed' : 'is-suspected';
    const label = confirmed ? '完整 SHA 已确认' : '疑似重复 · 待核验';
    return '<span class="governance-dedup-badge ' + cls + '" data-dedup-status="' + escapeAttr(confirmed ? 'confirmed' : 'suspected') + '">'
        + escapeHtml(label) + '</span>';
}
function renderDuplicateSummaryBar(entries){
    const confirmedCount = entries.filter(entry => entry.dedup_status === 'confirmed').length;
    const suspectedCount = entries.length - confirmedCount;
    const jobStatus = String(dedupConfirmJob?.status || '').toLowerCase();
    const isRunning = dedupConfirmLoading || jobStatus === 'queued' || jobStatus === 'running';
    const isPaused = jobStatus === 'paused';
    const isFailed = jobStatus === 'failed' || jobStatus === 'interrupted' || jobStatus === 'cancelled';
    const progress = dedupConfirmJob?.progress || {};
    const processed = Number(progress.processed ?? progress.scanned ?? 0);
    const total = Number(progress.total ?? 0);
    let statusText = '先按文件大小筛候选，再计算完整 SHA 确认内容一致；不会自动删除文件。';
    if(dedupConfirmError){
        statusText = '核验失败：' + dedupConfirmError;
    } else if(isRunning){
        statusText = total > 0
            ? '正在后台限速核验完整 SHA（' + processed + '/' + total + '）…'
            : '正在后台限速核验完整 SHA…';
    } else if(isPaused){
        statusText = '核验任务已暂停（已完成 ' + processed + '/' + total + '），可在任务中心恢复。';
    } else if(isFailed){
        statusText = '核验任务未完成（状态：' + jobStatus + '，' + String(dedupConfirmJob?.error_code || '可重试') + '）。';
    } else if(jobStatus === 'succeeded'){
        statusText = '本轮完整 SHA 核验已完成（已确认 ' + confirmedCount + ' 组，疑似 ' + suspectedCount + ' 组）。';
    }
    const jobId = String(dedupConfirmJob?.job_id || '');
    const openTaskLink = jobId
        ? '<button class="hw-btn hw-btn-sm" type="button" data-dedup-open-task="' + escapeAttr(jobId) + '"><i data-lucide="activity"></i><span>任务中心</span></button>'
        : '';
    const disabled = (isRunning || suspectedCount === 0) ? ' disabled aria-disabled="true"' : '';
    const triggerLabel = isRunning ? '核验中…' : '核验完整 SHA';
    return '<div class="governance-dedup-bar" data-dedup-bar>'
        + '<div class="governance-dedup-bar-copy">'
        + '<div class="governance-dedup-counts">'
        + '<span class="governance-dedup-badge is-confirmed">已确认 ' + confirmedCount + ' 组</span>'
        + '<span class="governance-dedup-badge is-suspected">疑似 ' + suspectedCount + ' 组</span>'
        + '</div>'
        + '<p class="governance-dedup-status" role="status">' + escapeHtml(statusText) + '</p>'
        + '</div>'
        + '<div class="governance-dedup-bar-actions">'
        + openTaskLink
        + '<button class="hw-btn hw-btn-sm hw-btn-gold" type="button" data-dedup-start' + disabled + '>'
        + '<i data-lucide="shield-check"></i><span>' + escapeHtml(triggerLabel) + '</span></button>'
        + '</div></div>';
}
function renderGovernanceSuggestionCard(item){
    const kind = String(item.kind || '');
    const kindLabel = GOVERNANCE_SUGGESTION_KIND_LABELS[kind] || kind;
    const assetIds = governanceSuggestionAssetIds(item);
    const assetType = governanceAssetTypeForIds(assetIds);
    const informational = GOVERNANCE_INFORMATIONAL_KINDS.has(kind);
    const suggestionId = String(item.suggestion_id || '');
    const isSelected = Boolean(suggestionId && governanceSelectedSuggestionId === suggestionId);
    let detail = '';
    if(kind === 'naming'){
        detail = '<p>资产 <code>' + escapeHtml(item.name || '') + '</code>：'
            + ((item.violations || []).map(v => escapeHtml(v.message || v.code || '')).join('；') || '命名不符合规范')
            + '</p><p>建议名称：<code>' + escapeHtml(item.suggested_name || '') + '</code></p>';
    } else if(kind === 'tagging'){
        detail = '<p>来源标签 ' + (item.source_tags || []).map(tag => '<code>' + escapeHtml(tag) + '</code>').join('、')
            + ' 可归入受控词表：' + (item.suggested_tags || []).map(tag => '<strong>' + escapeHtml(tag) + '</strong>').join('、') + '</p>';
    } else if(kind === 'duplicate_files'){
        const hashLabel = item.content_hash
            ? (item.dedup_status === 'confirmed' ? '已确认完整 SHA <code>' : '疑似指纹 <code>')
                + escapeHtml(String(item.content_hash).slice(0, 16)) + '…</code>'
            : '同文件大小候选（<code>' + Number(item.size_bytes || 0) + ' 字节</code>，待计算完整 SHA）';
        detail = '<p>' + hashLabel + '：</p><ul>' + (item.members || []).map(member => '<li>' + escapeHtml(member.path || '')
            + '（' + Number(member.size_bytes || 0) + ' 字节）</li>').join('') + '</ul>';
    } else if(kind === 'duplicate_shots'){
        detail = '<p>相同镜头内容哈希 <code>' + escapeHtml(String(item.content_hash || '').slice(0, 16))
            + '…</code>：</p><ul>' + (item.members || []).map(member => '<li>' + escapeHtml(member.asset_id || '')
            + ' #' + escapeHtml(String(member.shot_index ?? '')) + '（' + Number(member.start_sec || 0).toFixed(2)
            + 's ~ ' + Number(member.end_sec || 0).toFixed(2) + 's）</li>').join('') + '</ul>';
    } else if(kind === 'vector_duplicates'){
        detail = '<p>嵌入相似度 <strong>' + escapeHtml(String(item.similarity ?? '')) + '</strong>：<code>'
            + escapeHtml(item.left_asset_id || '') + '</code> 与 <code>' + escapeHtml(item.right_asset_id || '') + '</code></p>';
    } else if(kind === 'similar_names'){
        detail = '<p>规范化名称 <code>' + escapeHtml(item.normalized_name || '') + '</code>：</p><ul>'
            + (item.members || []).map(member => '<li>' + escapeHtml(typeof member === 'string' ? member : (member?.path || member?.name || '')) + '</li>').join('')
            + '</ul>';
    }
    const openAsset = assetIds.length
        ? '<button class="hw-btn" type="button" data-governance-open-asset="' + escapeAttr(assetIds[0]) + '"><i data-lucide="folder-open"></i><span>打开素材</span></button>'
        : '';
    // 方案 A：duplicate_files 仅在 dedup_status === 'confirmed' 时允许确认建议；suspected 状态隐藏确认按钮并引导核验
    const isSuspectedDuplicate = kind === 'duplicate_files' && item.dedup_status !== 'confirmed';
    const confirm = informational || !suggestionId
        ? '<span class="governance-suggestion-readonly">仅提示，不提供写入动作</span>'
        : isSuspectedDuplicate
            ? '<span class="governance-suggestion-readonly" data-dedup-suspected-hint>疑似重复需先完成完整 SHA 核验后才可确认</span>'
            : '<button class="hw-btn hw-btn-gold" type="button" data-governance-confirm="' + escapeAttr(suggestionId) + '"><i data-lucide="check"></i><span>确认建议</span></button>';
    const group = GOVERNANCE_SUGGESTION_GROUPS.find(entry => entry.id === kind);
    const statusBadge = kind === 'duplicate_files' ? duplicateStatusBadge(item) : '';
    return '<article class="governance-suggestion-card' + (isSelected ? ' is-selected' : '') + '" data-governance-suggestion="' + escapeAttr(suggestionId)
        + '" data-governance-kind="' + escapeAttr(kind) + '" data-governance-asset-type="' + escapeAttr(assetType)
        + '" aria-selected="' + String(isSelected) + '" role="listitem">'
        + '<div class="governance-suggestion-card-head"><strong>' + escapeHtml(kindLabel) + '</strong>' + statusBadge
        + '<span>' + escapeHtml(suggestionId) + '</span></div>'
        + detail
        + '<div class="governance-suggestion-card-meta"><span>' + assetIds.length + ' 个相关素材</span><span>'
        + escapeHtml(group?.action || '') + '</span></div>'
        + '<footer class="governance-suggestion-card-actions">' + openAsset + confirm + '</footer></article>';
}
function renderGovernanceSuggestionGroup(group){
    const source = governanceSuggestionsData?.suggestions?.[group.id];
    const entries = Array.isArray(source) ? source.map(entry => ({...entry,kind:group.id})) : [];
    const ids = [...new Set(entries.flatMap(governanceSuggestionAssetIds))];
    const assetType = governanceAssetTypeForIds(ids);
    const empty = entries.length === 0;
    const dedupBar = group.id === 'duplicate_files' ? renderDuplicateSummaryBar(entries) : '';
    return '<details class="governance-suggestion-group" data-governance-group="' + escapeAttr(group.id)
        + '" data-governance-asset-type="' + escapeAttr(assetType) + '"' + (empty ? '' : ' open') + '>'
        + '<summary><span class="governance-suggestion-group-icon"><i data-lucide="' + escapeAttr(group.icon) + '"></i></span>'
        + '<span class="governance-suggestion-group-copy"><strong>' + escapeHtml(group.label) + '</strong>'
        + '<span>' + escapeHtml(group.description) + '</span><small>可以怎么处理：' + escapeHtml(group.action) + '</small></span>'
        + '<span class="governance-suggestion-group-count">' + entries.length + ' 条建议</span>'
        + '<span class="governance-suggestion-group-assets">' + ids.length + ' 个素材</span>'
        + (empty ? '<em class="governance-suggestion-no-issue">无问题</em>' : '')
        + '</summary>'
        + dedupBar
        + (empty ? '' : '<div class="governance-suggestion-group-items" role="list" aria-label="' + escapeAttr(group.label) + '">'
            + entries.map(renderGovernanceSuggestionCard).join('') + '</div>')
        + '</details>';
}
function renderGovernanceDrawerBody(){
    if(governanceSuggestionsLoading && !governanceSuggestionsData){
        return '<div class="governance-drawer-state" role="status" data-governance-loading><i data-lucide="loader-circle"></i><span>正在加载治理建议…</span></div>';
    }
    if(governanceSuggestionsError){
        const forbidden = governanceSuggestionsError.status === 403;
        const title = forbidden ? '需要治理权限' : '治理建议读取失败';
        const message = forbidden ? '当前账户需要 admin 或 governor 角色才能查看治理建议。'
            : (governanceSuggestionsError.message || '服务暂时无法读取建议清单。');
        return '<section class="governance-drawer-state is-error" role="alert" data-governance-error>'
            + '<i data-lucide="' + (forbidden ? 'shield-alert' : 'triangle-alert') + '"></i><div><strong>'
            + title + '</strong><p>' + escapeHtml(message) + '</p></div>'
            + '<button class="hw-btn" type="button" data-governance-retry><i data-lucide="refresh-cw"></i><span>重试</span></button></section>';
    }
    if(!governanceSuggestionsData){
        return '<section class="governance-drawer-state" role="status" data-governance-empty><p>治理建议尚未加载。</p>'
            + '<button class="hw-btn" type="button" data-governance-retry><i data-lucide="refresh-cw"></i><span>重试</span></button></section>';
    }
    const total = governanceSuggestionTotal();
    const gaps = Array.isArray(governanceSuggestionsData.data_gaps) ? governanceSuggestionsData.data_gaps : [];
    const refreshBanner = governanceRefreshNotice
        ? '<p class="governance-drawer-warning governance-refresh-notice" role="status" data-governance-refresh-notice>'
            + escapeHtml(governanceRefreshNotice)
            + ' <button class="hw-btn hw-btn-sm" type="button" data-governance-retry><span>刷新清单</span></button></p>'
        : '';
    return '<div class="governance-suggestion-groups" role="list" aria-label="治理建议清单">'
        + refreshBanner
        + (total === 0 ? '<p class="governance-drawer-empty" data-governance-empty>暂无待处理建议；系统永不自动删除，也不改源文件。</p>' : '')
        + (gaps.length ? '<p class="governance-drawer-warning" data-governance-degraded>数据降级：'
            + escapeHtml(gaps.join('、')) + '，查重候选可能不完整。</p>' : '')
        + GOVERNANCE_SUGGESTION_GROUPS.map(renderGovernanceSuggestionGroup).join('')
        + '</div>';
}
function closeGovernanceDrawerFromDismissal(){
    loadGovernanceSuggestions._seq = (loadGovernanceSuggestions._seq || 0) + 1;
    dedupConfirmSeq += 1;
    clearDedupPollTimer();
    if(loadGovernanceSuggestions._controller){
        try { loadGovernanceSuggestions._controller.abort(); } catch(_) {}
        loadGovernanceSuggestions._controller = null;
    }
    setGovernanceSuggestionsLoading(false);
    setDedupConfirmLoading(false);
    const host = document.querySelector('[data-governance-drawer-host]');
    host?.remove();
    setGovernanceDrawerOpen(false);
    const trigger = governanceDrawerReturnFocus;
    governanceDrawerReturnFocus = null;
    if(trigger?.isConnected) requestAnimationFrame(() => trigger.focus({preventScroll:true}));
    updateGovernanceTrigger();
}
function closeGovernanceDrawer(){
    const host = document.querySelector('[data-governance-drawer-host]');
    if(host && window.FloatingDismissal?.closeSurface?.(host)) return;
    closeGovernanceDrawerFromDismissal();
}
function selectGovernanceSuggestion(suggestionId){
    const id = String(suggestionId || '').trim();
    if(!id) return;
    setGovernanceSelectedSuggestionId(governanceSelectedSuggestionId === id ? '' : id);
    if(governanceDrawerOpen) renderGovernanceDrawer();
}
async function pollDedupConfirmJob(jobId, seq, pollIntervalMs=500){
    if(!jobId || seq !== dedupConfirmSeq) return;
    try {
        const detail = await apiJsonResponse(assetManagerApi.getWorkspaceJob(jobId));
        if(seq !== dedupConfirmSeq) return;
        const job = detail?.job || detail;
        setDedupConfirmJob(job || null);
        const status = String(job?.status || '').toLowerCase();
        if(['queued', 'running'].includes(status)){
            if(governanceDrawerOpen) renderGovernanceDrawer();
            dedupPollTimer = setTimeout(() => { void pollDedupConfirmJob(jobId, seq, pollIntervalMs); }, pollIntervalMs);
            return;
        }
        setDedupConfirmLoading(false);
        if(status === 'succeeded'){
            showAssetNotice('完整 SHA 查重确认已完成');
            await loadGovernanceSuggestions(true, { preserveExistingOnError: true });
        } else {
            setDedupConfirmError(String(job?.error_code || ('任务结束于状态：' + status)));
            if(governanceDrawerOpen) renderGovernanceDrawer();
        }
    } catch(error) {
        if(seq !== dedupConfirmSeq || isAbortError(error)) return;
        setDedupConfirmLoading(false);
        setDedupConfirmError(error?.message || '查询核验任务失败');
        if(governanceDrawerOpen) renderGovernanceDrawer();
    }
}
async function startDedupConfirm(options={}){
    if(dedupConfirmLoading) return;
    const seq = ++dedupConfirmSeq;
    clearDedupPollTimer();
    setDedupConfirmLoading(true);
    setDedupConfirmError('');
    if(governanceDrawerOpen) renderGovernanceDrawer();
    const idempotencyKey = options.idempotencyKey || ('dedup-confirm-' + Date.now() + '-' + seq);
    try {
        const accepted = await apiJsonResponse(assetManagerApi.startDedupConfirmJob(options.payload || {}, { idempotencyKey }));
        if(seq !== dedupConfirmSeq) return;
        const job = accepted?.job || null;
        const jobId = String(accepted?.job_id || job?.job_id || '');
        setDedupConfirmJob(job || (jobId ? { job_id: jobId, status: 'queued' } : null));
        if(governanceDrawerOpen) renderGovernanceDrawer();
        if(jobId){
            const intervalMs = Number(accepted?.poll_hint?.interval_ms || options.pollIntervalMs || 500);
            dedupPollIntervalMs = Number.isFinite(intervalMs) && intervalMs > 0 ? intervalMs : 500;
            await pollDedupConfirmJob(jobId, seq, dedupPollIntervalMs);
        } else {
            setDedupConfirmLoading(false);
            if(governanceDrawerOpen) renderGovernanceDrawer();
        }
    } catch(error) {
        if(seq !== dedupConfirmSeq || isAbortError(error)) return;
        setDedupConfirmLoading(false);
        setDedupConfirmError(error?.message || '提交完整 SHA 核验任务失败');
        setStatus(dedupConfirmError);
        if(governanceDrawerOpen) renderGovernanceDrawer();
    }
}
function renderGovernanceDrawer(){
    if(!governanceDrawerOpen) return;
    let host = document.querySelector('[data-governance-drawer-host]');
    let created = false;
    if(!host){
        host = document.createElement('div');
        host.className = 'governance-drawer-overlay';
        host.dataset.governanceDrawerHost = '';
        host.innerHTML = '<section class="governance-suggestion-drawer" data-governance-drawer-dialog role="dialog" aria-modal="true" aria-labelledby="governanceDrawerTitle" tabindex="-1">'
            + '<header><div><span class="governance-drawer-kicker">ASSET GOVERNANCE</span><h2 id="governanceDrawerTitle">治理建议</h2>'
            + '<p>六类清单仅供复核；确认操作按注册表版本校验。</p></div>'
            + '<button class="hw-btn hw-btn-icon" type="button" data-governance-drawer-close aria-label="关闭治理建议" title="关闭"><i data-lucide="x"></i></button></header>'
            + '<div class="governance-suggestion-drawer-body" data-governance-drawer-body></div></section>';
        document.body.appendChild(host);
        markAssetFloatingSurface(host, {
            surface:'asset-governance-drawer',
            kind:'drawer',
            content:'[data-governance-drawer-dialog]',
            close:closeGovernanceDrawerFromDismissal,
            trigger:governanceDrawerReturnFocus,
        });
        host.addEventListener('click', async event => {
            if(event.target === host || event.target.closest?.('[data-governance-drawer-close]')){
                closeGovernanceDrawer();
                return;
            }
            if(event.target.closest?.('[data-governance-retry]')){
                await refreshGovernanceSuggestions();
                renderGovernanceDrawer();
                return;
            }
            if(event.target.closest?.('[data-dedup-start]')){
                await startDedupConfirm();
                return;
            }
            const taskButton = event.target.closest?.('[data-dedup-open-task]');
            if(taskButton){
                const jobId = taskButton.dataset.dedupOpenTask || '';
                closeGovernanceDrawer();
                window.Workspace?.navigate?.('task-center', jobId ? { job_id: jobId } : {});
                return;
            }
            const assetButton = event.target.closest?.('[data-governance-open-asset]');
            if(assetButton){
                const assetId = assetButton.dataset.governanceOpenAsset || '';
                closeGovernanceDrawer();
                openRegistryAssetFromGlobalSearch(assetId).catch(error => setStatus(error.message || '无法打开素材'));
                return;
            }
            const confirmButton = event.target.closest?.('[data-governance-confirm]');
            if(confirmButton){
                await confirmGovernanceSuggestion(confirmButton.dataset.governanceConfirm || '');
                return;
            }
            const card = event.target.closest?.('[data-governance-suggestion]');
            if(card && card.dataset.governanceSuggestion){
                selectGovernanceSuggestion(card.dataset.governanceSuggestion);
            }
        });
        created = true;
    }
    const body = host.querySelector('[data-governance-drawer-body]');
    if(body) body.innerHTML = renderGovernanceDrawerBody();
    refreshIcons();
    if(created) requestAnimationFrame(() => host.querySelector('[data-governance-drawer-close]')?.focus());
}
function openGovernanceDrawer(trigger){
    governanceDrawerReturnFocus = trigger || null;
    setGovernanceDrawerOpen(true);
    updateGovernanceTrigger();
    renderGovernanceDrawer();
    if(!governanceSuggestionsData && !governanceSuggestionsError && !governanceSuggestionsLoading){
        loadGovernanceSuggestions();
    }
    const activeJobId = String(dedupConfirmJob?.job_id || '');
    const activeStatus = String(dedupConfirmJob?.status || '').toLowerCase();
    if(activeJobId && ['queued', 'running'].includes(activeStatus) && !dedupPollTimer && !dedupConfirmLoading){
        const resumeSeq = ++dedupConfirmSeq;
        setDedupConfirmLoading(true);
        renderGovernanceDrawer();
        void pollDedupConfirmJob(activeJobId, resumeSeq, dedupPollIntervalMs);
    }
}
async function confirmGovernanceSuggestion(suggestionId){
    const id = String(suggestionId || '').trim();
    if(!id) return;
    const revision = Number(governanceSuggestionsData?.revision || 0);
    if(!Number.isInteger(revision) || revision < 1){
        setStatus('建议清单尚未加载，无法确认');
        return;
    }
    try {
        await apiJsonResponse(fetch('/api/asset-registry/governance/suggestions/confirm', {
            method:'POST',
            headers:{'Content-Type':'application/json', 'Accept':'application/json'},
            body:JSON.stringify({suggestion_id:id, expected_version:revision}),
        }));
        showAssetNotice('建议已确认并写入注册表');
    } catch(error) {
        setStatus(error.message || '确认失败');
        return;
    }
    await loadGovernanceSuggestions(true, { preserveExistingOnError: true });
}

// 【QIN-591】域函数面注册：主文件 gwCall('governance', fn) 经由此对象分派。
GW_DEFERRED['governance'] = {
    confirmGovernanceSuggestion,
    loadGovernanceSuggestions,
    openGovernanceDrawer,
    pollDedupConfirmJob,
    renderGovernanceDrawer,
    selectGovernanceSuggestion,
    startDedupConfirm,
};
