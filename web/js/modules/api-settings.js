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

let providers = [];
let providerRevision = null;
let providerSavePending = false;
let savedProvidersJson = '[]';
let selectedId = '';
const providerList = document.getElementById('providerList');
const editorTitle = document.getElementById('editorTitle');
const statusEl = document.getElementById('status');
const nameInput = document.getElementById('nameInput');
const idInput = document.getElementById('idInput');
const baseInput = document.getElementById('baseInput');
const protocolInput = document.getElementById('protocolInput');
const imageRequestModeInput = document.getElementById('imageRequestModeInput');
const imageEditRouteInput = document.getElementById('imageEditRouteInput');
const keyInput = document.getElementById('keyInput');
const keyHint = document.getElementById('keyHint');
const volcArkKeyHint = document.getElementById('volcArkKeyHint');
const volcAkInput = document.getElementById('volcAkInput');
const volcSkInput = document.getElementById('volcSkInput');
const volcAssetKeyHint = document.getElementById('volcAssetKeyHint');
const volcProjectInput = document.getElementById('volcProjectInput');
const volcRegionInput = document.getElementById('volcRegionInput');
const jimengCliPanel = document.getElementById('jimengCliPanel');
const jimengCliStatus = document.getElementById('jimengCliStatus');
const jimengCredit = document.getElementById('jimengCredit');
const jimengLoginBox = document.getElementById('jimengLoginBox');
const jimengHelpOverlay = document.getElementById('jimengHelpOverlay');
const jimengHelpCommand = document.getElementById('jimengHelpCommand');
const jimengHelpOutput = document.getElementById('jimengHelpOutput');
const codexCliPanel = document.getElementById('codexCliPanel');
const codexCliStatus = document.getElementById('codexCliStatus');
const codexCliInfo = document.getElementById('codexCliInfo');
const codexHelpOverlay = document.getElementById('codexHelpOverlay');
const codexHelpCommand = document.getElementById('codexHelpCommand');
const codexHelpOutput = document.getElementById('codexHelpOutput');
const geminiCliPanel = document.getElementById('geminiCliPanel');
const geminiCliStatus = document.getElementById('geminiCliStatus');
const geminiCliInfo = document.getElementById('geminiCliInfo');
const geminiCliHelpOverlay = document.getElementById('geminiCliHelpOverlay');
const geminiCliHelpCommand = document.getElementById('geminiCliHelpCommand');
const geminiCliHelpOutput = document.getElementById('geminiCliHelpOutput');
const settingsContent = document.getElementById('settingsContent');
const imageModelList = document.getElementById('imageModelList');
const chatModelList = document.getElementById('chatModelList');
const videoModelList = document.getElementById('videoModelList');
const videoProtocolInput = document.getElementById('videoProtocolInput');
const msLoraBlock = document.getElementById('msLoraBlock');
const msLoraList = document.getElementById('msLoraList');
const VOLCENGINE_DEFAULT_BASE_URL = 'https://ark.cn-beijing.volces.com/api/v3';
const VOLCENGINE_DEFAULT_PROJECT_NAME = 'default';
const VOLCENGINE_DEFAULT_REGION = 'cn-beijing';
const MS_BUILTIN_IMAGE_MODELS = [
    'Tongyi-MAI/Z-Image-Turbo',
    'Qwen/Qwen-Image-2512',
    'Qwen/Qwen-Image-Edit-2511',
    'black-forest-labs/FLUX.2-klein-9B'
];
const MS_DEFAULT_BASE_URL = 'https://api-inference.modelscope.cn/v1';
const EXAMPLE_BASE_URL = 'https://api.example.com/v1';
const JIMENG_DEFAULT_IMAGE_MODELS = ['5.0Pro', '5.0', '4.7', '4.6', '4.5', '4.1', '4.0', '3.1', '3.0'];
const JIMENG_DEFAULT_VIDEO_MODELS = ['seedance2.0fast_vip', 'seedance2.0_vip', 'seedance2.0', 'seedance2.0fast', 'seedance2.0mini'];
const JIMENG_LEGACY_IMAGE_MODELS = new Set(['jimeng-image-2k', 'jimeng-image-4k']);
const JIMENG_LEGACY_VIDEO_MODELS = new Set(['jimeng-video-720p', 'jimeng-video-1080p']);
const CODEX_DEFAULT_IMAGE_MODELS = ['gpt-image-2'];
const CODEX_DEFAULT_CHAT_MODELS = ['gpt-5.5'];
const GEMINI_CLI_DEFAULT_IMAGE_MODELS = ['auto'];
const GEMINI_CLI_DEFAULT_CHAT_MODELS = ['auto'];
const CLI_PROTOCOLS = new Set(['jimeng', 'codex', 'gemini-cli']);
const API_PROTOCOLS = ['openai', 'apimart', 'gemini', 'grok', 'volcengine', 'jimeng', 'codex', 'gemini-cli'];
const CLI_PROVIDER_PRESETS = {
    jimeng:{id:'jimeng', name:'即梦 CLI', protocol:'jimeng'},
    codex:{id:'codex', name:'GPT CLI', protocol:'codex'},
    'gemini-cli':{id:'gemini-cli', name:'Gemini / Antigravity CLI', protocol:'gemini-cli'}
};
function applyCliProtocolDefaults(item, protocol){
    if(!item) return;
    const value = String(protocol || item.protocol || '').toLowerCase();
    if(!CLI_PROTOCOLS.has(value)) return;
    item.base_url = '';
    item.protocol = value;
    if(value === 'jimeng'){
        item.image_models = unique([...(item.image_models || []).filter(model => !JIMENG_LEGACY_IMAGE_MODELS.has(String(model || '').trim())), ...JIMENG_DEFAULT_IMAGE_MODELS]);
        item.video_models = unique([...(item.video_models || []).filter(model => !JIMENG_LEGACY_VIDEO_MODELS.has(String(model || '').trim())), ...JIMENG_DEFAULT_VIDEO_MODELS]);
        item.chat_models = unique(item.chat_models || []);
    } else if(value === 'codex'){
        item.image_models = unique([...(item.image_models || []).filter(model => String(model || '').trim().toLowerCase() !== '$imagegen'), ...CODEX_DEFAULT_IMAGE_MODELS]);
        item.chat_models = unique([...(item.chat_models || []), ...CODEX_DEFAULT_CHAT_MODELS]);
        item.video_models = [];
    } else if(value === 'gemini-cli'){
        item.image_models = unique([...(item.image_models || []), ...GEMINI_CLI_DEFAULT_IMAGE_MODELS]);
        item.chat_models = unique([...(item.chat_models || []), ...GEMINI_CLI_DEFAULT_CHAT_MODELS]);
        item.video_models = [];
    }
}
let providerDragId = '';

// 统一「无后端时显式降级」（用户 2026-09-21 裁决第 3 项）。
// 与 http-transport.js / workspace-common.js 同源：仅当 404 / 501 且响应**不含标准错误包**（对象型 detail）时判定「未接入」；
// 503 单独归类「服务暂不可用」；已实现接口的真实业务 404 原样透传。
const NOT_INTEGRATED_MESSAGE = '该功能尚未接入后端（未纳入当前切片）';
const SERVICE_UNAVAILABLE_MESSAGE = '后端服务暂时不可用，请稍后重试';
function degradationKind(status, data){
    const shared = window.GWDegradation;
    if(shared && typeof shared.statusKind === 'function') return shared.statusKind(status, data && data.detail);
    if(status === 503) return 'service_unavailable';
    if(status !== 404 && status !== 501) return '';
    const detail = data && data.detail;
    if(detail && typeof detail === 'object') return '';
    if(typeof detail === 'string' && detail.trim() && !/^(not found|not implemented)$/i.test(detail.trim())) return '';
    return 'not_integrated';
}
function degradationMessage(data, fallbackMessage){
    const detail = data && data.detail;
    if(detail && typeof detail === 'object') return detail.message || detail.msg || JSON.stringify(detail);
    return detail || (data && (data.error || data.message)) || fallbackMessage || '请求失败';
}
async function requestJson(url, options, fallbackMessage, {allowStatuses = []} = {}){
    const response = await fetch(url, options);
    const data = await response.json().catch(() => ({}));
    if(!response.ok && !allowStatuses.includes(response.status)){
        const kind = degradationKind(response.status, data);
        const error = new Error(kind === 'not_integrated'
            ? `${NOT_INTEGRATED_MESSAGE}（HTTP ${response.status}）`
            : (kind === 'service_unavailable'
                ? `${SERVICE_UNAVAILABLE_MESSAGE}（HTTP ${response.status}）`
                : degradationMessage(data, fallbackMessage)));
        error.name = kind === 'not_integrated' ? 'NotIntegratedError'
            : (kind === 'service_unavailable' ? 'ServiceUnavailableError' : 'Error');
        error.code = kind === 'not_integrated' ? 'NOT_INTEGRATED'
            : (kind === 'service_unavailable' ? 'SERVICE_UNAVAILABLE' : (data?.detail?.code || 'REQUEST_FAILED'));
        error.unavailable = kind === 'not_integrated';
        error.retryable = kind === 'service_unavailable';
        error.status = response.status;
        error.data = data;
        throw error;
    }
    return {response, data};
}
// category: 'allround'（全能）| 'value'（性价比）| 'free'（免费），推荐面板按分组分节展示
function refreshIcons(){ if(window.lucide) lucide.createIcons(); }
function tr(key){ return window.StudioI18n ? window.StudioI18n.t(key) : key; }
function trf(key, vars={}){
    let text = tr(key);
    Object.entries(vars).forEach(([name, value]) => {
        text = text.replaceAll(`{${name}}`, String(value ?? ''));
    });
    return text;
}
function setStatus(text, kind='info'){
    statusEl.textContent = text || '';
    statusEl.dataset.kind = kind;
}
function rememberSavedProviders(){
    syncEditor();
    savedProvidersJson = JSON.stringify(providers);
}
function hasUnsavedChanges(){
    if(providerRevision === null) return false;
    if(providerSavePending) return true;
    syncEditor();
    return JSON.stringify(providers) !== savedProvidersJson;
}
function confirmLeaveEditor(){
    if(providerSavePending){ setStatus(tr('api.waitForSave'), 'warning'); return false; }
    if(!hasUnsavedChanges()) return true;
    if(!window.confirm(tr('api.discardChanges'))) return false;
    providers = JSON.parse(savedProvidersJson);
    selectedId = providers.some(item => item.id === selectedId) ? selectedId : (providers[0]?.id || '');
    renderEditor();
    return true;
}
function validateEditor(){
    const item = provider();
    const error = document.getElementById('baseError');
    if(error) error.textContent = '';
    baseInput.removeAttribute('aria-invalid');
    if(!item || CLI_PROTOCOLS.has(item.protocol) || !baseInput.value.trim()) return true;
    try {
        const value = baseInput.value.trim();
        const url = new URL(value);
        if(!/^https?:\/\//i.test(value) || !url.hostname || url.username || url.password || url.search || url.hash) throw new Error();
    } catch(_) {
        baseInput.setAttribute('aria-invalid', 'true');
        if(error) error.textContent = tr('api.invalidUrl');
        setStatus(tr('api.fixFields'), 'error');
        baseInput.focus();
        return false;
    }
    return true;
}
function checkAutofillReview(){
    const inputs = [nameInput, baseInput, keyInput, volcAkInput, volcSkInput];
    const autofilled = inputs.some(input => input?.offsetParent !== null && input
        && (input.matches(':-webkit-autofill') || getComputedStyle(input).boxShadow.includes('100px')));
    const review = document.getElementById('autofillReview');
    const ack = document.getElementById('autofillAck');
    if(!autofilled){ review.hidden = true; return true; }
    review.hidden = false;
    if(ack.checked) return true;
    setStatus(tr('api.autofillUnconfirmed'), 'warning');
    ack.focus();
    return false;
}
// 显式降级判定：命中未接入 / 不可用时不得退回泛泛的「失败」文案。
function degradationLabel(error, fallback){
    if(error && error.code === 'NOT_INTEGRATED') return NOT_INTEGRATED_MESSAGE;
    if(error && error.code === 'SERVICE_UNAVAILABLE') return SERVICE_UNAVAILABLE_MESSAGE;
    return fallback;
}
let studioApiBroadcastChannel = null;
let studioApiBroadcastTimer = 0;
let studioApiBroadcastTypes = new Set();
const studioApiBroadcastSource = `api-settings-${Date.now()}-${Math.random().toString(36).slice(2)}`;
function emitStudioApiChange(type){
    const message = { type, updated_at:Date.now(), source:studioApiBroadcastSource };
    try {
        studioApiBroadcastChannel = studioApiBroadcastChannel || new BroadcastChannel('studio-api');
        studioApiBroadcastChannel.postMessage(message);
    } catch(e) {}
    try { window.parent?.postMessage(message, '*'); } catch(e) {}
    if(window.top && window.top !== window.parent) {
        try { window.top.postMessage(message, '*'); } catch(e) {}
    }
}
function broadcastStudioApiChange(type='providers-changed'){
    studioApiBroadcastTypes.add(type);
    if(studioApiBroadcastTimer) clearTimeout(studioApiBroadcastTimer);
    studioApiBroadcastTimer = setTimeout(() => {
        const types = Array.from(studioApiBroadcastTypes);
        studioApiBroadcastTypes.clear();
        studioApiBroadcastTimer = 0;
        types.forEach(emitStudioApiChange);
    }, 120);
}
function normalizeId(value){
    return String(value || '').trim().toLowerCase().replace(/[^a-z0-9_-]/g, '-').replace(/^-+|-+$/g, '').replace(/-+/g, '-').slice(0, 40);
}
// 平台凭据按稳定ID加密保存；改名不改变凭据归属。
function deriveIdFromName(name, existingId){
    if(existingId) return existingId;
    let id = normalizeId(name);
    if(!id){
        id = 'api-' + Math.random().toString(36).slice(2, 8);
    }
    let candidate = id, i = 2;
    while(providers.some(p => p.id === candidate)){
        candidate = `${id}-${i++}`;
    }
    return candidate;
}
function updateIdPreview(){
    const item = provider();
    if(!item) return;
    const isBuiltin = item.id === 'comfly' || item.id === 'modelscope' || item.id === 'volcengine' || item.id === 'jimeng';
    const idPreview = document.getElementById('idPreview');
    if(!idPreview) return;
    if(isBuiltin){
        idPreview.textContent = item.id;
        return;
    }
    idPreview.textContent = deriveIdFromName(nameInput.value, item.id);
}
function provider(){
    return visibleProviders().find(item => item.id === selectedId) || visibleProviders()[0] || providers[0];
}
function isProviderTemporarilyHidden(item){
    return false;
}
function visibleProviders(){
    return (providers || []).filter(item => !isProviderTemporarilyHidden(item));
}
function isFixedProvider(itemOrId){
    const id = typeof itemOrId === 'string' ? itemOrId : itemOrId?.id;
    // 即梦 CLI 不再是固定平台：可删除、可排序，未添加则不存在。
    return id === 'modelscope' || id === 'volcengine';
}
function unique(values){
    const seen = new Set();
    return values.map(v => String(v || '').trim()).filter(v => v && !seen.has(v) && seen.add(v));
}
function workflowNodeTitle(node){
    return (node?._meta?.title || node?.class_type || node?._class || node?.type || 'Node').toString();
}
function workflowNodeClass(node){
    return (node?.class_type || node?._class || node?.type || '').toString();
}
function workflowNodeCategory(node){
    const text = `${workflowNodeTitle(node)} ${workflowNodeClass(node)}`.toLowerCase();
    if(/text|prompt|clip/.test(text)) return 'prompt';
    if(/lora/.test(text)) return 'lora';
    if(/ksampler|k sampler|sampler|scheduler|guid|cfg/.test(text)) return 'sampler';
    if(/video|movie|mp4|webm|frame/.test(text)) return 'video';
    if(/audio|sound|voice|music|wav|mp3/.test(text)) return 'audio';
    if(/image|mask|resize|scale|crop|photo|picture|preview|save/.test(text)) return 'image';
    return 'misc';
}
function volcengineArkKeyHintText(item){
    return item?.has_key ? '方舟 API Key 已加密保存。' : '还没有保存方舟 API Key。';
}
function volcengineAssetKeyHintText(item){
    const ak = item?.has_volcengine_access_key ? 'AK 已加密保存' : 'AK 未保存';
    const sk = item?.has_volcengine_secret_key ? 'SK 已加密保存' : 'SK 未保存';
    return `${ak} · ${sk}`;
}
function isApimartProviderContext(item){
    const baseUrl = String(baseInput?.value || item?.base_url || '').trim().toLowerCase();
    return baseUrl.includes('apimart.ai');
}
function updateApimartDomesticHint(item=provider()){
    const hasKey = Boolean(item?.has_key || (keyInput?.value || '').trim());
    document.body.classList.toggle('show-apimart-domestic-hint', Boolean(isApimartProviderContext(item) && hasKey));
}
function normalizeProviderBaseInput(){
    const protocol = String(protocolInput?.value || provider()?.protocol || 'openai');
    const value = String(baseInput?.value || '').trim();
    if(!value || !['openai', 'apimart', 'grok'].includes(protocol)) return value;
    try {
        const url = new URL(value);
        if(!['http:', 'https:'].includes(url.protocol) || url.username || url.password || url.search || url.hash) return value;
        let path = url.pathname.replace(/\/+$/, '');
        if(!path.split('/').includes('v1')) path += '/v1';
        url.pathname = path;
        baseInput.value = url.href;
    } catch(_) { return value; }
    return baseInput.value;
}
function syncEditor(){
    const item = provider();
    if(!item) return;
    const oldId = item.id;
    const isBuiltin = item.id === 'comfly' || item.id === 'modelscope' || item.id === 'volcengine' || item.id === 'jimeng';
    // 内置和自定义平台的 ID 都保持稳定；新建时若没有 ID 才生成一次。
    const nextId = isBuiltin ? item.id : deriveIdFromName(nameInput.value, item.id);
    item.id = nextId;
    if(oldId !== item.id) selectedId = item.id;
    item.name = nameInput.value.trim() || item.id;
    const selectedProtocol = item.id === 'modelscope'
        ? 'openai'
        : item.id === 'volcengine'
        ? 'volcengine'
        : (protocolInput?.value || 'openai');
    item.base_url = CLI_PROTOCOLS.has(selectedProtocol) ? '' : normalizeProviderBaseInput();
    // 固定平台不从协议下拉读取
    item.protocol = selectedProtocol;
    item.video_protocol = videoProtocolInput?.value === 'newapi_video' ? 'newapi_video' : null;
    item.image_request_mode = normalizeImageRequestMode(
        item.id === 'modelscope' || item.id === 'volcengine' || CLI_PROTOCOLS.has(selectedProtocol)
            ? 'openai'
            : (imageRequestModeInput?.value || item.image_request_mode)
    );
    item.image_edit_route = normalizeImageEditRoute(
        item.id === 'modelscope' || item.id === 'volcengine' || CLI_PROTOCOLS.has(selectedProtocol)
            ? 'general'
            : (imageEditRouteInput?.value || item.image_edit_route)
    );
    item.image_generation_endpoint = '';
    item.image_edit_endpoint = '';
    const key = keyInput.value.trim();
    if(key){ item.api_key = key; delete item._clearKey; }
    else delete item.api_key;
    if(item.id === 'volcengine'){
        const ak = volcAkInput?.value.trim() || '';
        const sk = volcSkInput?.value.trim() || '';
        if(ak){ item.volcengine_access_key_id = ak; delete item._clearVolcengineAccessKey; }
        else delete item.volcengine_access_key_id;
        if(sk){ item.volcengine_secret_access_key = sk; delete item._clearVolcengineSecretKey; }
        else delete item.volcengine_secret_access_key;
        item.volcengine_project_name = (volcProjectInput?.value.trim() || VOLCENGINE_DEFAULT_PROJECT_NAME);
        item.volcengine_region = (volcRegionInput?.value.trim() || VOLCENGINE_DEFAULT_REGION);
    }
}
function updateProtocolFromInput(){
    const item = provider();
    if(!item || !protocolInput || item.id === 'modelscope' || item.id === 'volcengine') return;
    const value = String(protocolInput.value || 'openai').toLowerCase();
    item.protocol = API_PROTOCOLS.includes(value) ? value : 'openai';
    if(CLI_PROTOCOLS.has(item.protocol)) item.base_url = '';
    applyCliProtocolDefaults(item, item.protocol);
    document.body.classList.toggle('show-jimeng', item.protocol === 'jimeng');
    document.body.classList.toggle('show-codex', item.protocol === 'codex');
    document.body.classList.toggle('show-gemini-cli', item.protocol === 'gemini-cli');
    clearVerifyResult();
    // 协议会改变整个表单（如即梦 CLI 账户面板、默认模型、Key 占位）。renderEditor 是唯一切换这些的入口，
    // 这里复跑一次让面板立即出现；保存并恢复 Key 输入框，避免已填写的 Key 被 renderEditor 清空。
    const savedKey = keyInput ? keyInput.value : '';
    renderEditor();
    if(keyInput) keyInput.value = savedKey;
    updateApimartDomesticHint(item);
}
function isVolcengineProvider(item){
    return String(item?.protocol || '').toLowerCase() === 'volcengine';
}
function readFileAsDataUrl(file){
    return new Promise((resolve, reject) => {
        const reader = new FileReader();
        reader.onload = () => resolve(String(reader.result || ''));
        reader.onerror = () => reject(reader.error || new Error('读取图片失败'));
        reader.readAsDataURL(file);
    });
}
function loadImageForThumbnail(src){
    return new Promise((resolve, reject) => {
        const img = new Image();
        img.onload = () => resolve(img);
        img.onerror = () => reject(new Error('图片解析失败'));
        img.src = src;
    });
}
function sortedProviders(){
    const order = ['modelscope', 'volcengine'];
    return visibleProviders().sort((a, b) => {
        const ai = order.indexOf(a.id);
        const bi = order.indexOf(b.id);
        if(ai === -1 && bi === -1) return 0;
        if(ai === -1) return 1;
        if(bi === -1) return -1;
        return ai - bi;
    });
}
function providerDragAttrs(item){
    if(isFixedProvider(item)) return '';
    const id = escapeAttr(item.id);
    return ` draggable="true" data-provider-id="${id}" ondragstart="handleProviderDragStart(event,'${id}')" ondragover="handleProviderDragOver(event,'${id}')" ondrop="handleProviderDrop(event,'${id}')" ondragend="handleProviderDragEnd()"`;
}
function renderProviderList(){
    if(!visibleProviders().length){
        providerList.innerHTML = `<div class="empty">${escapeHtml(tr('api.noProviders'))}</div>`;
        return;
    }
    providerList.innerHTML = sortedProviders().map(item => {
        const active = item.id === selectedId ? 'active' : '';
        const itemProtocol = String(item.protocol || 'openai').toLowerCase();
        const stateClass = item.enabled === false ? 'is-disabled' : (item.has_key || item.has_wallet_key || CLI_PROTOCOLS.has(itemProtocol) ? 'has-key' : 'missing-key');
        const protocolLabel = String(item.protocol || 'openai').toUpperCase();
        if(item.id === 'modelscope'){
            return `
                <button class="provider-card provider-card-banner ${active} ${stateClass}" type="button" onclick="selectProvider('${escapeHtml(item.id)}')">
                    <span class="provider-banner-inner">
                        <span class="provider-logo-wrap">
                            <span class="provider-logo-fallback">ModelScope</span>
                        </span>
                        <span class="provider-protocol-pill">OpenAI</span>
                    </span>
                </button>
            `;
        }
        if(item.id === 'volcengine'){
            return `
                <button class="provider-card provider-card-banner ${active} ${stateClass}" type="button" onclick="selectProvider('${escapeHtml(item.id)}')">
                    <span class="provider-banner-inner">
                        <span class="provider-logo-wrap">
                            <span class="provider-logo-fallback">火山引擎</span>
                        </span>
                        <span class="provider-protocol-pill">Ark</span>
                    </span>
                </button>
            `;
        }
        return `
            <button class="provider-card provider-card-sortable ${active} ${stateClass}" type="button" onclick="selectProvider('${escapeHtml(item.id)}')"${providerDragAttrs(item)}>
                <span class="provider-drag-handle" aria-hidden="true"><i data-lucide="grip-vertical" class="w-3.5 h-3.5"></i></span>
                <span class="provider-mark"><i data-lucide="${item.has_key ? 'key-round' : 'key'}" class="w-4 h-4"></i></span>
                <span class="provider-info">
                    <div class="provider-name">${escapeHtml(item.name || item.id)}</div>
                    <div class="provider-meta">${escapeHtml(item.base_url || '未配置地址')}</div>
                </span>
                <span class="provider-side-meta">
                    <span class="provider-status-dot"></span>
                    <span class="provider-protocol-pill">${escapeHtml(protocolLabel)}</span>
                </span>
            </button>
        `;
    }).join('');
    refreshIcons();
}
function handleProviderDragStart(event, id){
    const item = providers.find(provider => provider.id === id);
    if(!item || isFixedProvider(item)){
        event.preventDefault();
        return;
    }
    providerDragId = id;
    event.currentTarget.classList.add('is-dragging');
    event.dataTransfer.effectAllowed = 'move';
    event.dataTransfer.setData('text/plain', id);
}
function handleProviderDragOver(event, id){
    if(!providerDragId || providerDragId === id || isFixedProvider(id)) return;
    event.preventDefault();
    event.dataTransfer.dropEffect = 'move';
    providerList?.querySelectorAll('.provider-card-drop-target').forEach(el => el.classList.remove('provider-card-drop-target'));
    event.currentTarget.classList.add('provider-card-drop-target');
}
function handleProviderDrop(event, targetId){
    event.preventDefault();
    providerList?.querySelectorAll('.provider-card-drop-target').forEach(el => el.classList.remove('provider-card-drop-target'));
    const sourceId = providerDragId || event.dataTransfer.getData('text/plain');
    providerDragId = '';
    if(!sourceId || sourceId === targetId || isFixedProvider(sourceId) || isFixedProvider(targetId)) return;
    const sourceIndex = providers.findIndex(item => item.id === sourceId);
    const targetIndex = providers.findIndex(item => item.id === targetId);
    if(sourceIndex < 0 || targetIndex < 0) return;
    const [moved] = providers.splice(sourceIndex, 1);
    const adjustedTargetIndex = providers.findIndex(item => item.id === targetId);
    providers.splice(adjustedTargetIndex, 0, moved);
    renderProviderList();
    saveProviders();
}
function handleProviderDragEnd(){
    providerDragId = '';
    providerList?.querySelectorAll('.is-dragging,.provider-card-drop-target').forEach(el => {
        el.classList.remove('is-dragging', 'provider-card-drop-target');
    });
}
function renderEditor(){
    const item = provider();
    document.getElementById('providerEmptyState').hidden = Boolean(item);
    document.getElementById('providerEditor').hidden = !item;
    if(!item){
        editorTitle.textContent = tr('api.provider');
        [nameInput, idInput, baseInput, keyInput].forEach(input => { if(input) input.value = ''; });
        [imageModelList, chatModelList, videoModelList].forEach(list => {
            if(list) list.innerHTML = `<div class="empty">${escapeHtml(tr('api.noModels'))}</div>`;
        });
        renderProviderList();
        return;
    }
    editorTitle.textContent = item.name || item.id;
    nameInput.value = item.name || '';
    idInput.value = item.id || '';
    updateIdPreview();
    clearVerifyResult();
    baseInput.placeholder = EXAMPLE_BASE_URL;
    baseInput.value = item.base_url || '';
    if(protocolInput){
        protocolInput.value = item.id === 'volcengine' ? 'volcengine' : (item.protocol || 'openai');
        protocolInput.disabled = FIXED_PROTOCOL_PROVIDER_IDS.has(item.id);
        protocolInput.title = protocolInput.disabled ? '内置平台使用固定协议' : '';
    }
    if(imageRequestModeInput){
        imageRequestModeInput.value = normalizeImageRequestMode(item.image_request_mode);
        imageRequestModeInput.disabled = item.id === 'modelscope' || item.id === 'volcengine' || CLI_PROTOCOLS.has(String(protocolInput?.value || item.protocol || '').toLowerCase());
        imageRequestModeInput.title = '';
    }
    if(imageEditRouteInput){
        imageEditRouteInput.value = normalizeImageEditRoute(item.image_edit_route);
        imageEditRouteInput.disabled = item.id === 'modelscope' || item.id === 'volcengine' || CLI_PROTOCOLS.has(String(protocolInput?.value || item.protocol || '').toLowerCase());
    }
    // 仅恢复本页尚未保存的草稿；服务端快照从不提供密钥原文。
    keyInput.value = item.api_key || '';
    keyInput.placeholder = item.has_key ? `${tr('api.keepCurrentKey')} ${item.key_preview || ''}` : tr('api.enterKey');
    keyHint.textContent = item.has_key ? tr('api.keySaved') : tr('api.noKey');
    if(videoProtocolInput) videoProtocolInput.value = item.video_protocol || '';
    const isModelScope = item.id === 'modelscope';
    const isVolcengine = item.id === 'volcengine' || String(protocolInput?.value || item.protocol || '').toLowerCase() === 'volcengine';
    const isStandaloneVolcengine = item.id === 'volcengine';
    const isJimeng = String(protocolInput?.value || item.protocol || '').toLowerCase() === 'jimeng';
    const isCodex = String(protocolInput?.value || item.protocol || '').toLowerCase() === 'codex';
    const isGeminiCli = String(protocolInput?.value || item.protocol || '').toLowerCase() === 'gemini-cli';
    if(isVolcengine){
        item.base_url = item.base_url || VOLCENGINE_DEFAULT_BASE_URL;
        item.protocol = 'volcengine';
        item.volcengine_project_name = item.volcengine_project_name || VOLCENGINE_DEFAULT_PROJECT_NAME;
        item.volcengine_region = item.volcengine_region || VOLCENGINE_DEFAULT_REGION;
        keyInput.placeholder = item.has_key ? `保持当前方舟 API Key ${item.key_preview || ''}` : '输入方舟 API Key';
        keyHint.textContent = volcengineArkKeyHintText(item);
        if(volcArkKeyHint) volcArkKeyHint.textContent = volcengineArkKeyHintText(item);
        if(volcAkInput){
            volcAkInput.value = item.volcengine_access_key_id || '';
            volcAkInput.placeholder = item.has_volcengine_access_key ? `保持当前 AK ${item.volcengine_access_key_preview || ''}` : 'Access Key ID';
        }
        if(volcSkInput){
            volcSkInput.value = item.volcengine_secret_access_key || '';
            volcSkInput.placeholder = item.has_volcengine_secret_key ? `保持当前 SK ${item.volcengine_secret_key_preview || ''}` : 'Secret Access Key';
        }
        if(volcAssetKeyHint) volcAssetKeyHint.textContent = volcengineAssetKeyHintText(item);
        if(volcProjectInput) volcProjectInput.value = item.volcengine_project_name || VOLCENGINE_DEFAULT_PROJECT_NAME;
        if(volcRegionInput) volcRegionInput.value = item.volcengine_region || VOLCENGINE_DEFAULT_REGION;
    }
    if(isJimeng){
        item.base_url = '';
        item.protocol = 'jimeng';
        item.image_models = unique([...(item.image_models || []).filter(model => !JIMENG_LEGACY_IMAGE_MODELS.has(String(model || '').trim())), ...JIMENG_DEFAULT_IMAGE_MODELS]);
        item.video_models = unique([...(item.video_models || []).filter(model => !JIMENG_LEGACY_VIDEO_MODELS.has(String(model || '').trim())), ...JIMENG_DEFAULT_VIDEO_MODELS]);
        keyInput.placeholder = '即梦 CLI 使用本机 dreamina login，无需 API Key';
        keyHint.textContent = '请先在终端安装 dreamina CLI，并执行 dreamina login';
    }
    if(isCodex){
        applyCliProtocolDefaults(item, 'codex');
        keyInput.placeholder = 'Codex CLI 不使用 HTTP API Key';
        keyHint.textContent = '本机命令路径、执行开关、登录观察与生成能力分别核对。';
    }
    if(isGeminiCli){
        applyCliProtocolDefaults(item, 'gemini-cli');
        keyInput.placeholder = 'Gemini / Antigravity CLI 不使用 HTTP API Key';
        keyHint.textContent = '命令候选为 gemini、gemini-cli、antigravity；路径存在不代表登录或生成就绪。';
    }
    document.body.classList.toggle('show-ms', isModelScope);
    document.body.classList.toggle('show-volcengine', isVolcengine);
    document.body.classList.toggle('show-volcengine-standalone', isStandaloneVolcengine);
    const volcKeys = document.querySelector('.volcengine-key-stack');
    if(volcKeys) volcKeys.hidden = !isStandaloneVolcengine;
    document.body.classList.toggle('show-jimeng', isJimeng);
    document.body.classList.toggle('show-codex', isCodex);
    document.body.classList.toggle('show-gemini-cli', isGeminiCli);
    updateApimartDomesticHint(item);
    updateProbeAvailability();
    if(msLoraBlock) msLoraBlock.style.display = isModelScope ? 'flex' : 'none';
    if(jimengCliPanel){
        jimengCliPanel.hidden = !isJimeng;
        jimengCliPanel.style.display = isJimeng ? 'flex' : 'none';
        if(isJimeng) refreshJimengStatus();
        else stopJimengWaitingOnPageLeave();
    }
    if(codexCliPanel){
        codexCliPanel.hidden = !isCodex;
        codexCliPanel.style.display = isCodex ? 'flex' : 'none';
        if(isCodex) refreshCodexStatus(false);
    }
    if(geminiCliPanel){
        geminiCliPanel.hidden = !isGeminiCli;
        geminiCliPanel.style.display = isGeminiCli ? 'flex' : 'none';
        if(isGeminiCli) refreshGeminiCliStatus(false);
    }
    const deleteBtn = document.getElementById('deleteBtn');
    if(deleteBtn) deleteBtn.style.display = 'inline-flex';
    renderModels('image');
    renderModels('chat');
    renderModels('video');
    if(isModelScope) renderMsLoras();
    else if(msLoraList) msLoraList.innerHTML = '';
    renderProviderList();
}
function showVerifyResult(html){ const el = document.getElementById('verifyResult'); if(el){ el.style.display = 'block'; el.innerHTML = html; } }
function clearVerifyResult(){ const el = document.getElementById('verifyResult'); if(el){ el.style.display = 'none'; el.innerHTML = ''; } }
function prettyJson(value){
    try { return JSON.stringify(value, null, 2); } catch(_) { return String(value || ''); }
}
function jimengCreditText(raw){
    if(!raw) return '';
    const parts = [];
    const seen = new Set();
    const visit = value => {
        if(!value || typeof value !== 'object') return;
        Object.entries(value).forEach(([key, item]) => {
            const low = key.toLowerCase();
            if(/credit|balance|quota|point|coin|积分|余额/.test(low) && item !== null && typeof item !== 'object'){
                const label = `${key}: ${item}`;
                if(!seen.has(label)){ seen.add(label); parts.push(label); }
            }
            if(item && typeof item === 'object') visit(item);
        });
    };
    visit(raw);
    return parts.join(' · ') || prettyJson(raw);
}
function setJimengStatus(text, ok=null){
    if(!jimengCliStatus) return;
    jimengCliStatus.textContent = text || '状态未知';
    jimengCliStatus.classList.toggle('ok', ok === true);
    jimengCliStatus.classList.toggle('bad', ok === false);
}
let jimengLoginTimer = null;
let jimengFlowGeneration = 0;
let jimengPollingEnabled = false;
let jimengPollAbort = null;
let jimengPollScheduled = false;
let jimengActionBusy = false;
function setJimengActionBusy(busy){
    jimengActionBusy = Boolean(busy);
    ['jimengLoginBtn', 'jimengCreditBtn', 'jimengLogoutBtn'].forEach(id => {
        const button = document.getElementById(id);
        if(button) button.disabled = jimengActionBusy;
    });
}
function clearJimengPoll({invalidate=false, clearBox=false}={}){
    if(jimengLoginTimer) clearTimeout(jimengLoginTimer);
    jimengLoginTimer = null;
    jimengPollScheduled = false;
    jimengPollingEnabled = false;
    if(invalidate){
        jimengFlowGeneration += 1;
        if(jimengPollAbort) jimengPollAbort.abort();
        jimengPollAbort = null;
    }
    if(clearBox && jimengLoginBox){
        jimengLoginBox.replaceChildren();
        jimengLoginBox.hidden = true;
    }
}
function renderJimengLoginBox(data){
    if(!jimengLoginBox) return;
    jimengLoginBox.replaceChildren();
    if(!data?.running){
        jimengLoginBox.hidden = true;
        return;
    }
    jimengLoginBox.hidden = false;
    const uri = typeof data.verification_uri === 'string' ? data.verification_uri : '';
    const userCode = typeof data.user_code === 'string' ? data.user_code : '';
    if(uri && userCode){
        try {
            const parsed = new URL(uri);
            if(parsed.protocol === 'https:' && !parsed.username && !parsed.password && !parsed.search && !parsed.hash){
                const note = document.createElement('p');
                note.textContent = '请在新标签页打开 Dreamina 授权页面，并输入下方用户代码。不要将代码分享给他人。';
                const link = document.createElement('a');
                link.href = parsed.href;
                link.target = '_blank';
                link.rel = 'noopener noreferrer';
                link.textContent = '打开 Dreamina 授权页面';
                const codeLabel = document.createElement('strong');
                codeLabel.textContent = `用户代码：${userCode}`;
                jimengLoginBox.append(note, link, codeLabel);
                return;
            }
        } catch(_) {}
    }
    const note = document.createElement('p');
    note.textContent = data.output_seen
        ? 'CLI 输出格式无法识别，请在服务器本机终端完成登录。'
        : '登录进程已启动，正在等待授权材料；不会伪造二维码或链接。';
    jimengLoginBox.appendChild(note);
}
function applyJimengObservation(data){
    renderJimengLoginBox(data);
    const executionInfo = document.getElementById('jimengExecutionInfo');
    if(executionInfo) executionInfo.textContent = cliObservationText(data);
    if(data?.running){
        setJimengStatus(data.state === 'awaiting_authorization' ? '等待本人授权...' : '登录操作运行中...', null);
    } else if(data?.logged_in === true){
        setJimengStatus('最近一次 login 成功（非实时状态）', true);
    } else if(data?.logged_in === false && data?.last_operation === 'logout'){
        setJimengStatus('最近一次 logout 成功（非实时状态）', false);
    } else if(data?.last_operation_failed || data?.result_unknown){
        setJimengStatus('最近操作失败或结果未知', null);
    } else {
        setJimengStatus('登录状态未知（仅观察本进程最近操作）', null);
    }
}
async function refreshJimengStatus(){
    if(!jimengCliPanel || jimengCliPanel.hidden || jimengPollAbort) return;
    const generation = jimengFlowGeneration;
    const abortController = new AbortController();
    jimengPollAbort = abortController;
    try {
        const {data} = await requestJson('/api/jimeng/status', {signal:abortController.signal}, '读取 Dreamina CLI 状态失败');
        if(generation === jimengFlowGeneration) applyJimengObservation(data);
    } catch(e){
        if(generation === jimengFlowGeneration && e.name !== 'AbortError'){
            setJimengStatus(`${degradationMessage(e.data, e.message || '读取状态失败')}；账户状态未知`, null);
        }
    } finally {
        if(jimengPollAbort === abortController) jimengPollAbort = null;
    }
}
function jimengSharedActionConfirmed(action){
    return window.confirm(`${action}会修改服务器本机的 Dreamina 共享账户状态，影响同一服务器上的其他使用者。继续吗？`);
}
async function startJimengLogin(){
    if(jimengActionBusy || !jimengSharedActionConfirmed('登录')) return;
    clearJimengPoll({invalidate:true, clearBox:true});
    const generation = jimengFlowGeneration;
    jimengPollingEnabled = true;
    setJimengActionBusy(true);
    setJimengStatus('正在启动本机登录操作...', null);
    if(jimengCredit) jimengCredit.textContent = '';
    try {
        const {data} = await requestJson('/api/jimeng/login/start', {method:'POST'}, '启动登录失败');
        if(generation !== jimengFlowGeneration) return;
        applyJimengObservation(data);
        if(data.running) scheduleJimengPoll(generation);
        else jimengPollingEnabled = false;
    } catch(e){
        if(generation === jimengFlowGeneration){
            jimengPollingEnabled = false;
            setJimengStatus(`${degradationMessage(e.data, e.message || '登录操作失败')}；账户状态未知`, null);
            if(jimengLoginBox){
                jimengLoginBox.replaceChildren();
                jimengLoginBox.hidden = true;
            }
        }
    } finally {
        setJimengActionBusy(false);
    }
}
function scheduleJimengPoll(generation){
    if(!jimengPollingEnabled || generation !== jimengFlowGeneration || jimengPollScheduled) return;
    jimengPollScheduled = true;
    jimengLoginTimer = setTimeout(() => {
        jimengPollScheduled = false;
        jimengLoginTimer = null;
        pollJimengLogin(generation);
    }, 2500);
}
async function pollJimengLogin(generation= jimengFlowGeneration){
    if(!jimengPollingEnabled || generation !== jimengFlowGeneration || jimengPollAbort) return;
    const abortController = new AbortController();
    jimengPollAbort = abortController;
    let shouldContinue = false;
    try {
        const {data} = await requestJson('/api/jimeng/login/status', {signal:abortController.signal}, '读取登录状态失败');
        if(generation !== jimengFlowGeneration) return;
        applyJimengObservation(data);
        shouldContinue = data.running === true;
        if(!shouldContinue) jimengPollingEnabled = false;
    } catch(e){
        if(generation === jimengFlowGeneration && e.name !== 'AbortError'){
            jimengPollingEnabled = false;
            setJimengStatus(`${degradationMessage(e.data, e.message || '登录检测失败')}；账户状态未知`, null);
            if(jimengLoginBox){
                jimengLoginBox.replaceChildren();
                jimengLoginBox.hidden = true;
            }
        }
    } finally {
        if(jimengPollAbort === abortController) jimengPollAbort = null;
        if(shouldContinue && jimengPollingEnabled && generation === jimengFlowGeneration){
            scheduleJimengPoll(generation);
        }
    }
}
async function refreshJimengCredit(){
    if(jimengActionBusy) return;
    setJimengActionBusy(true);
    setJimengStatus('正在查询余额...', null);
    try {
        const {data} = await requestJson('/api/jimeng/credit', undefined, '查询余额失败');
        if(jimengCredit) jimengCredit.textContent = `余额查询完成（不代表实时登录状态）：${jimengCreditText(data.raw)}`;
        setJimengStatus('余额查询完成；登录状态仍以操作观察为准', null);
    } catch(e){
        setJimengStatus(`${degradationMessage(e.data, e.message || '查询余额失败')}；登录状态未知`, null);
        if(jimengCredit) jimengCredit.textContent = '';
    } finally {
        setJimengActionBusy(false);
    }
}
async function logoutJimeng(){
    if(jimengActionBusy || !jimengSharedActionConfirmed('退出登录')) return;
    clearJimengPoll({invalidate:true, clearBox:true});
    setJimengActionBusy(true);
    setJimengStatus('正在执行本机 logout...', null);
    try {
        const {data} = await requestJson('/api/jimeng/logout', {method:'POST'}, '退出登录失败');
        if(data.logged_in === false && data.last_operation === 'logout'){
            setJimengStatus('最近一次 logout 成功（不代表其他终端状态）', false);
        } else {
            setJimengStatus('退出命令已结束，账户状态未知', null);
        }
        if(jimengCredit) jimengCredit.textContent = '';
    } catch(e){
        setJimengStatus(`${degradationMessage(e.data, e.message || '退出操作失败')}；账户状态未知`, null);
        if(jimengCredit) jimengCredit.textContent = '';
    } finally {
        setJimengActionBusy(false);
    }
}
function stopJimengWaitingOnPageLeave(){
    // 只取消本页等待和轮询，不会隐式终止服务器已接管的登录进程。
    clearJimengPoll({invalidate:true, clearBox:true});
}
window.addEventListener('pagehide', stopJimengWaitingOnPageLeave);
function openJimengHelp(){
    if(!jimengHelpOverlay) return;
    jimengHelpOverlay.style.display = 'flex';
    loadJimengHelp();
}
function closeJimengHelp(){
    if(jimengHelpOverlay) jimengHelpOverlay.style.display = 'none';
}
async function loadJimengHelp(){
    if(!jimengHelpOutput) return;
    jimengHelpOutput.textContent = '加载中...';
    try {
        const command = jimengHelpCommand?.value || '';
        const {data} = await requestJson('/api/jimeng/help', {
            method:'POST',
            headers:{'Content-Type':'application/json'},
            body:JSON.stringify({command})
        }, '加载帮助失败');
        jimengHelpOutput.textContent = data.text || prettyJson(data.raw);
    } catch(e){
        jimengHelpOutput.textContent = e.message || String(e);
    }
}
function setCodexStatus(text, ok=null){
    if(!codexCliStatus) return;
    codexCliStatus.textContent = text || '未检测';
    codexCliStatus.classList.toggle('ok', ok === true);
    codexCliStatus.classList.toggle('bad', ok === false);
}
function cliObservationText(data){
    return `${data.installed === true ? '路径已发现' : data.installed === false ? '路径未发现' : '路径未观察'} · 执行${data.execution_enabled === true ? '已启用' : data.execution_enabled === false ? '未启用' : '开关未观察'} · 登录${data.logged_in === true ? '已观察' : data.logged_in === false ? '未登录' : '未知'} · 生成${data.generation_ready === true ? '已验证' : '未接入'}`;
}
async function refreshCodexStatus(showInfo=true){
    if(!codexCliPanel || codexCliPanel.hidden) return;
    setCodexStatus('检测中...');
    try {
        const {data} = await requestJson('/api/codex/status', undefined, '读取 GPT CLI 状态失败');
        setCodexStatus(data.installed ? '路径已发现' : '路径未发现', null);
        if(codexCliInfo){
            const parts = [];
            parts.push(cliObservationText(data));
            if(data.version) parts.push(data.version);
            if(data.path) parts.push(data.path);
            if(data.message) parts.push(data.message);
            codexCliInfo.textContent = parts.join(' · ');
        }
    } catch(e){
        setCodexStatus(degradationLabel(e, '检测失败'), false);
        if(codexCliInfo) codexCliInfo.textContent = e.message || String(e);
    }
}
function openCodexHelp(){
    if(!codexHelpOverlay) return;
    codexHelpOverlay.style.display = 'flex';
    loadCodexHelp();
}
function closeCodexHelp(){
    if(codexHelpOverlay) codexHelpOverlay.style.display = 'none';
}
async function loadCodexHelp(){
    if(!codexHelpOutput) return;
    codexHelpOutput.textContent = '加载中...';
    try {
        const command = codexHelpCommand?.value || '';
        const {data} = await requestJson('/api/codex/help', {
            method:'POST',
            headers:{'Content-Type':'application/json'},
            body:JSON.stringify({command})
        }, '加载帮助失败');
        codexHelpOutput.textContent = data.text || prettyJson(data.raw);
    } catch(e){
        codexHelpOutput.textContent = e.message || String(e);
    }
}
function setGeminiCliStatus(text, ok=null){
    if(!geminiCliStatus) return;
    geminiCliStatus.textContent = text || '未检测';
    geminiCliStatus.classList.toggle('ok', ok === true);
    geminiCliStatus.classList.toggle('bad', ok === false);
}
async function refreshGeminiCliStatus(showInfo=true){
    if(!geminiCliPanel || geminiCliPanel.hidden) return;
    setGeminiCliStatus('检测中...');
    try {
        const {data} = await requestJson('/api/gemini-cli/status', undefined, '读取 Gemini / Antigravity CLI 状态失败');
        setGeminiCliStatus(data.installed ? '路径已发现' : '路径未发现', null);
        if(geminiCliInfo){
            const parts = [];
            parts.push(cliObservationText(data));
            if(data.command_candidates?.length) parts.push(`命令候选：${data.command_candidates.join(' / ')}`);
            if(data.version) parts.push(data.version);
            if(data.path) parts.push(data.path);
            if(data.message) parts.push(data.message);
            geminiCliInfo.textContent = parts.join(' · ');
        }
    } catch(e){
        setGeminiCliStatus(degradationLabel(e, '检测失败'), false);
        if(geminiCliInfo) geminiCliInfo.textContent = e.message || String(e);
    }
}
function openGeminiCliHelp(){
    if(!geminiCliHelpOverlay) return;
    geminiCliHelpOverlay.style.display = 'flex';
    loadGeminiCliHelp();
}
function closeGeminiCliHelp(){
    if(geminiCliHelpOverlay) geminiCliHelpOverlay.style.display = 'none';
}
async function loadGeminiCliHelp(){
    if(!geminiCliHelpOutput) return;
    geminiCliHelpOutput.textContent = '加载中...';
    try {
        const command = geminiCliHelpCommand?.value || '';
        const {data} = await requestJson('/api/gemini-cli/help', {
            method:'POST',
            headers:{'Content-Type':'application/json'},
            body:JSON.stringify({command})
        }, '加载帮助失败');
        geminiCliHelpOutput.textContent = data.text || prettyJson(data.raw);
    } catch(e){
        geminiCliHelpOutput.textContent = e.message || String(e);
    }
}
function currentProviderApiKey(item){
    return keyInput.value.trim();
}
function normalizeImageRequestMode(value){
    const mode = String(value || '').trim().toLowerCase();
    return ['openai', 'openai-json', 'openai-video-proxy', 'openai-responses', 'openai-async-image', 'tudou-async', 'newapi-sync-image'].includes(mode) ? mode : 'openai';
}
function normalizeImageEditRoute(value){
    const route = String(value || '').trim().toLowerCase();
    return ['general', 'auto', 'chat'].includes(route) ? route : 'general';
}
function imageRequestModeLabel(mode){
    const normalized = normalizeImageRequestMode(mode);
    if(normalized === 'openai-json') return 'OpenAI JSON';
    if(normalized === 'openai-video-proxy') return 'OpenAI 中转';
    if(normalized === 'openai-responses') return 'OpenAI RS';
    if(normalized === 'openai-async-image') return 'OpenAI 异步图片';
    if(normalized === 'tudou-async') return '土豆 GPT-Image-2 异步';
    if(normalized === 'newapi-sync-image') return '中转 / New API同步';
    return 'OpenAI 标准';
}
function applyDetectedImageRequestMode(mode){
    const item = provider();
    if(!item || !imageRequestModeInput) return false;
    const detected = normalizeImageRequestMode(mode);
    const changed = normalizeImageRequestMode(item.image_request_mode) !== detected || normalizeImageRequestMode(imageRequestModeInput.value) !== detected;
    imageRequestModeInput.value = detected;
    item.image_request_mode = detected;
    return changed;
}
function applyDetectedProtocol(protocol){
    const item = provider();
    const detected = String(protocol || '').toLowerCase();
    if(!item || !protocolInput || !API_PROTOCOLS.includes(detected)) return false;
    if(String(protocolInput.value || '').toLowerCase() === detected && String(item.protocol || '').toLowerCase() === detected) return false;
    protocolInput.value = detected;
    item.protocol = detected;
    item.base_url = CLI_PROTOCOLS.has(detected) ? '' : (baseInput?.value.trim() || item.base_url || '');
    if(detected === 'volcengine'){
        item.video_models = unique(item.video_models || []);
        item.volcengine_project_name = item.volcengine_project_name || VOLCENGINE_DEFAULT_PROJECT_NAME;
        item.volcengine_region = item.volcengine_region || VOLCENGINE_DEFAULT_REGION;
    }
    applyCliProtocolDefaults(item, detected);
    protocolInput.dispatchEvent(new Event('change'));
    return true;
}


let providerProbePending = false;
const modelProbeEndpoints = Object.freeze({
    'fetch-models': '/api/providers/fetch-models',
    'test-connection': '/api/providers/test-connection',
    'probe-async': '/api/providers/probe-async',
});
function supportsHttpModelDiscovery(item){
    return ['openai','apimart','grok','gemini'].includes(String(item?.protocol || 'openai').toLowerCase());
}
function updateProbeAvailability(){
    const item = provider();
    const supported = supportsHttpModelDiscovery(item);
    ['testUrlBtn','probeAsyncBtn','fetchModelsBtn'].forEach(id => {
        const button = document.getElementById(id);
        if(button) button.disabled = !supported || providerProbePending || providerSavePending;
    });
    const notice = document.getElementById('probeSupportNote');
    if(notice) notice.textContent = supported
        ? '目录探测只验证模型可见性；分类为建议，图片、聊天和视频生成能力仍需实际验证。'
        : CLI_PROTOCOLS.has(item?.protocol)
        ? 'CLI 使用专用状态面板；添加平台或默认模型不代表已登录或生成就绪。'
        : '当前未接入该协议的模型目录探测；请手动配置控制台模型 ID，生成能力另行验证。';
    const pickerButton = document.getElementById('openPickerBtn');
    if(pickerButton){
        pickerButton.disabled = !item || lastFetchedProviderId !== item.id || !lastFetchedAll.length;
        pickerButton.style.opacity = pickerButton.disabled ? '.5' : '1';
    }
}
async function performModelProbe(kind){
    normalizeProviderBaseInput();
    const item = provider();
    if(!item || providerProbePending || providerSavePending) return;
    syncEditor();
    if(!supportsHttpModelDiscovery(item)){
        setStatus('该协议的HTTP模型目录探测未接入，请查看专用面板或手动配置模型。', 'warning');
        return;
    }
    if(!baseInput.value.trim()){ setStatus('请先填写请求地址', 'warning'); baseInput.focus(); return; }
    if(!validateEditor() || !checkAutofillReview()) return;
    const payload = {base_url:baseInput.value.trim(), api_key:currentProviderApiKey(item),
        provider_id:item.id, protocol:item.protocol || 'openai',
        image_request_mode:imageRequestModeInput?.value || item.image_request_mode || 'openai'};
    const revision = providerRevision;
    const isCurrentProbe = () => selectedId === payload.provider_id && providerRevision === revision
        && baseInput.value.trim() === payload.base_url && protocolInput.value === payload.protocol
        && currentProviderApiKey(provider()) === payload.api_key;
    providerProbePending = true;
    updateProbeAvailability();
    setStatus('正在同步读取上游模型目录…');
    showVerifyResult('<span class="verify-detail">正在同步读取模型目录…</span>');
    try {
        const {data} = await requestJson(modelProbeEndpoints[kind], {method:'POST',
            headers:{'Content-Type':'application/json'}, body:JSON.stringify(payload)}, '模型目录探测失败');
        if(!isCurrentProbe()){
            setStatus('配置已变化，请重新探测当前平台。', 'warning');
            return;
        }
        const ok = data.ok === true;
        const className = ok ? 'verify-success' : 'verify-error';
        const message = data.message || (ok ? '模型目录已可见，生成能力尚未验证。' : '未取得可用模型目录。');
        showVerifyResult(`<div class="${className}">${escapeHtml(message)}</div>
            <div class="verify-detail">同步探测已结束 · 目录适配器：${escapeHtml(payload.protocol)} · 生成能力尚未验证</div>
            ${kind === 'probe-async' ? `<details class="verify-response"><summary>查看已完成探测的摘要</summary><pre>${escapeHtml(JSON.stringify(data.raw || {},null,2))}</pre></details>` : ''}`);
        setStatus(message, ok ? 'success' : 'error');
        if(ok && kind !== 'probe-async'){
            setFetchedModelState(data);
            const openButton = document.getElementById('openPickerBtn');
            if(openButton){ openButton.disabled = lastFetchedAll.length === 0; openButton.style.opacity = lastFetchedAll.length ? '1' : '.5'; }
            setStatus(`已读取 ${lastFetchedAll.length} 个模型；分类为建议，选择后保存配置，生成能力尚未验证。`, 'success');
            if(kind === 'fetch-models' && lastFetchedAll.length) openModelPicker();
        }
    } catch(error){
        if(!isCurrentProbe()) return;
        const message = degradationLabel(error, error.message || '模型目录探测失败');
        setStatus(message, 'error');
        showVerifyResult(`<div class="verify-error">${escapeHtml(message)}；已保留当前协议与图片接口配置。</div>`);
    } finally {
        providerProbePending = false;
        updateProbeAvailability();
    }
}
async function probeAsync(){ return performModelProbe('probe-async'); }
async function testConnection(){ return performModelProbe('test-connection'); }
let lastFetchedAll = [];          // 全部模型 id 列表
let lastFetchedProviderId = '';
let lastFetchedSuggestion = null; // 后端自动分类建议
let lastFetchedModelNames = {};   // {模型 id: 展示名}

function setFetchedModelState(data){
    lastFetchedProviderId = selectedId;
    lastFetchedAll = Array.isArray(data?.all) ? data.all : [];
    lastFetchedSuggestion = {
        image: new Set(data?.image_models || []),
        chat: new Set(data?.chat_models || []),
        video: new Set(data?.video_models || []),
    };
    lastFetchedModelNames = (data?.model_names && typeof data.model_names === 'object') ? {...data.model_names} : {};
}
function modelDisplayName(model, item){
    return String(model || '');
}
function providerModelBadge(model, label){
    const text = `${model || ''} ${label || ''}`.toLowerCase();
    if(text.includes('gpt-image')) return 'G';
    if(text.includes('nano')) return 'N';
    if(text.includes('qwen')) return 'Q';
    if(text.includes('seedream')) return 'S';
    if(text.includes('seedance')) return 'SD';
    if(text.includes('wan') || text.includes('万相')) return 'W';
    if(text.includes('jimeng') || text.includes('即梦')) return 'J';
    if(text.includes('luma')) return 'L';
    if(text.includes('vidu')) return 'V';
    if(text.includes('alibaba') || text.includes('阿里')) return 'A';
    if(text.includes('bytedance') || text.includes('字节')) return 'B';
    return 'M';
}

async function fetchModels(){ return performModelProbe('fetch-models'); }

// —— 模型选择器浮层 ——
// 每个模型只归一类（根据用户已配置 或 关键字猜测）；勾选 = 纳入该分类
let pickerState = { category: {}, selected: {} };
let pickerInitialStateJson = '';
let pickerVisibleIds = [];
function openModelPicker(){
    const item = provider();
    if(!item || lastFetchedProviderId !== item.id || !lastFetchedAll.length){ alert('请先读取当前平台的模型目录'); return; }
    const existing = { image: new Set(item.image_models||[]), chat: new Set(item.chat_models||[]), video: new Set(item.video_models||[]) };
    const allIds = new Set([...lastFetchedAll, ...(item.image_models||[]), ...(item.chat_models||[]), ...(item.video_models||[])]);
    pickerState = { category: {}, selected: {} };
    allIds.forEach(id => {
        // 类别归属：用户已配置 > 关键字建议 > 默认 chat
        let cat;
        if(existing.image.has(id)) cat = 'image';
        else if(existing.video.has(id)) cat = 'video';
        else if(existing.chat.has(id)) cat = 'chat';
        else if(lastFetchedSuggestion?.image?.has(id)) cat = 'image';
        else if(lastFetchedSuggestion?.video?.has(id)) cat = 'video';
        else cat = 'chat';
        pickerState.category[id] = cat;
        // 默认勾选状态：已在用户配置里的 = 勾选；新拉的 = 不勾选（让用户主动选）
        pickerState.selected[id] = existing.image.has(id) || existing.chat.has(id) || existing.video.has(id);
    });
    pickerInitialStateJson = JSON.stringify(pickerState);
    // 默认 tab 切回「全部」
    document.querySelectorAll('.picker-cat-tab').forEach(t => t.classList.toggle('active', t.dataset.cat === 'all'));
    document.getElementById('modelPickerOverlay').style.display = 'flex';
    renderModelPicker();
}
function closeModelPicker(){
    if(JSON.stringify(pickerState) !== pickerInitialStateJson && !window.confirm('放弃尚未应用的模型选择？')) return;
    document.getElementById('modelPickerOverlay').style.display = 'none';
}
function renderModelPicker(){
    const item = provider();
    const filter = (document.getElementById('pickerFilter')?.value || '').toLowerCase();
    const currentTab = document.querySelector('.picker-cat-tab.active')?.dataset.cat || 'all';
    const ids = Object.keys(pickerState.category).sort();
    // 各分类总数 / 已选数
    const totals = { all: ids.length, image:0, chat:0, video:0 };
    const selecteds = { all:0, image:0, chat:0, video:0 };
    ids.forEach(id => {
        const cat = pickerState.category[id];
        totals[cat]++;
        if(pickerState.selected[id]){ selecteds[cat]++; selecteds.all++; }
    });
    // 过滤显示
    const list = ids.filter(id => {
        const label = modelDisplayName(id, item);
        if(filter && !id.toLowerCase().includes(filter) && !label.toLowerCase().includes(filter)) return false;
        if(currentTab === 'all') return true;
        return pickerState.category[id] === currentTab;
    });
    pickerVisibleIds = list;
    document.getElementById('pickerCount').textContent = `共 ${totals.all} 个模型 · 当前显示 ${list.length} 个`;
    document.querySelectorAll('.picker-cat-tab').forEach(tab => {
        const cat = tab.dataset.cat;
        tab.querySelector('.cat-count').textContent = `${selecteds[cat]}/${totals[cat]}`;
    });
    // 列表
    const html = list.map((id, index) => {
        const checked = pickerState.selected[id];
        const label = modelDisplayName(id, item);
        const badge = providerModelBadge(id, label);
        return `
            <div class="picker-row ${checked?'has-sel':''}" onclick="togglePickerRowByIndex(${index})">
                <div class="picker-checkbox ${checked?'checked':''}">
                    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"></polyline></svg>
                </div>
                <div class="picker-model-badge">${escapeHtml(badge)}</div>
                <div class="picker-model-name" title="${escapeAttr(id)}">
                    <div class="picker-model-label">${escapeHtml(label || id)}</div>
                    ${label && label !== id ? `<div class="picker-model-id">${escapeHtml(id)}</div>` : ''}
                </div>
            </div>
        `;
    }).join('');
    document.getElementById('pickerList').innerHTML = html || `<div style="padding:32px;text-align:center;color:var(--faint);font-size:var(--gw-type-body-sm)">无匹配</div>`;
    // 底部汇总
    const sumImage = document.getElementById('sumImage');
    const sumChat = document.getElementById('sumChat');
    const sumVideo = document.getElementById('sumVideo');
    const sumUnsel = document.getElementById('sumUnsel');
    if(sumImage){ sumImage.textContent = `生图 ${selecteds.image}`; sumImage.classList.toggle('picker-sum-chip-empty', selecteds.image === 0); }
    if(sumChat){ sumChat.textContent = `LLM ${selecteds.chat}`; sumChat.classList.toggle('picker-sum-chip-empty', selecteds.chat === 0); }
    if(sumVideo){ sumVideo.textContent = `视频 ${selecteds.video}`; sumVideo.classList.toggle('picker-sum-chip-empty', selecteds.video === 0); }
    if(sumUnsel){ sumUnsel.textContent = `未选 ${totals.all - selecteds.all}`; }
}
function togglePickerRow(id){
    pickerState.selected[id] = !pickerState.selected[id];
    renderModelPicker();
}
function togglePickerRowByIndex(index){
    const id = pickerVisibleIds[index];
    if(typeof id !== 'string') return;
    togglePickerRow(id);
}
function selectPickerCat(cat){
    document.querySelectorAll('.picker-cat-tab').forEach(t => t.classList.toggle('active', t.dataset.cat === cat));
    renderModelPicker();
}
function applyModelPicker(){
    const item = provider(); if(!item) return;
    const image = [], chat = [], video = [];
    const modelNames = {};
    Object.entries(pickerState.selected).forEach(([id, sel]) => {
        if(!sel) return;
        const cat = pickerState.category[id];
        if(cat === 'image') image.push(id);
        else if(cat === 'video') video.push(id);
        else chat.push(id);
        const label = modelDisplayName(id, item);
        if(label && label !== id) modelNames[id] = label;
    });
    item.image_models = image;
    item.chat_models = chat;
    item.video_models = video;
    item.model_names = modelNames;
    renderModels('image'); renderModels('chat'); renderModels('video');
    renderMsLoras();
    setStatus(`已应用 · 生图 ${image.length} / LLM ${chat.length} / 视频 ${video.length}，点保存生效`);
    pickerInitialStateJson = JSON.stringify(pickerState);
    window.FloatingDismissal.closeSurface(document.getElementById('modelPickerOverlay'));
}
async function saveKeyOnly(){
    const item = provider();
    if(!item) return;
    const key = keyInput.value.trim();
    if(!key){ alert(tr('api.enterKeyAlert') || '请输入 Key'); return; }
    item.api_key = key;
    const ok = await saveProviders();
    if(ok) keyInput.value = '';
}
async function clearKeyOnly(){
    const item = provider();
    if(!item) return;
    if(!item.has_key && !keyInput.value){ return; }
    if(!confirm(tr('api.confirmClearKey') || '确认清除当前 Key？')) return;
    keyInput.value = '';
    delete item.api_key;
    item._clearKey = true;
    const ok = await saveProviders();
    if(ok) keyInput.value = '';
}
const FIXED_PROTOCOL_PROVIDER_IDS = new Set(['modelscope', 'volcengine']);
function providerSupportsModelProtocol(item){
    return Boolean(item) && !FIXED_PROTOCOL_PROVIDER_IDS.has(item.id);
}
function modelProtocolSelectHtml(kind, index, model, item){
    if(!providerSupportsModelProtocol(item)) return '';
    if(kind === 'video') return '';
    const map = (item.model_protocols && typeof item.model_protocols === 'object') ? item.model_protocols : {};
    let current = String(map[String(model || '').trim()] || '').toLowerCase();
    const opt = (val, label) => `<option value="${val}" ${current === val ? 'selected' : ''}>${label}</option>`;
    return `<select class="model-protocol-select" aria-label="${escapeAttr(model || '新模型')} 使用协议" title="该模型使用的协议，默认跟随平台全局协议" onchange="updateModelProtocol('${kind}', ${index}, this.value)">
        <option value="" ${current === '' ? 'selected' : ''}>默认</option>
        ${opt('openai', 'OpenAI')}
        ${opt('gemini', 'Gemini')}
    </select>`;
}
function renderModels(kind){
    const item = provider();
    const key = kind === 'image' ? 'image_models' : kind === 'video' ? 'video_models' : 'chat_models';
    const list = kind === 'image' ? imageModelList : kind === 'video' ? videoModelList : chatModelList;
    const models = item?.[key] || [];
    if(!models.length){
        list.innerHTML = `<div class="empty">${tr('api.noModels')}</div>`;
        return;
    }
    const showProtocol = kind !== 'video' && providerSupportsModelProtocol(item);
    list.innerHTML = models.map((model, index) => {
        const label = modelDisplayName(model, item);
        return `
            <div class="model-row${showProtocol ? ' has-protocol' : ''}">
                <div class="model-id-field">
                    ${label && label !== model ? `<div class="model-display-name">${escapeHtml(label)}</div>` : ''}
                    <input aria-label="${kind === 'image' ? '生图' : kind === 'video' ? '视频' : '聊天'}模型 ID ${index + 1}" spellcheck="false" autocomplete="off" value="${escapeAttr(model)}" oninput="updateModel('${kind}', ${index}, this.value)">
                </div>
                ${modelProtocolSelectHtml(kind, index, model, item)}
                <button class="icon-btn" type="button" onclick="removeModel('${kind}', ${index})" aria-label="删除模型 ${escapeAttr(model || String(index + 1))}" title="删除"><i data-lucide="trash-2" class="w-4 h-4"></i></button>
            </div>
        `;
    }).join('');
    refreshIcons();
}
function msLoraTargetOptions(selected){
    const item = provider();
    const models = unique([selected, ...MS_BUILTIN_IMAGE_MODELS, ...((item?.image_models) || [])]);
    return models.filter(Boolean).map(model => `<option value="${escapeAttr(model)}" ${model === selected ? 'selected' : ''}>${escapeHtml(model)}</option>`).join('');
}
function normalizeLoraStrength(value){
    const n = Number(value);
    if(!Number.isFinite(n)) return 0.8;
    return Math.max(0, Math.min(2, n));
}
function renderMsLoras(){
    const item = provider();
    if(!msLoraList || !item || item.id !== 'modelscope') return;
    item.ms_loras = Array.isArray(item.ms_loras) ? item.ms_loras : [];
    if(!item.ms_loras.length){
        msLoraList.innerHTML = `<div class="lora-empty">${tr('api.loraEmpty')}</div>`;
        return;
    }
    msLoraList.innerHTML = item.ms_loras.map((lora, index) => {
        const target = lora.target_model || lora.model || MS_BUILTIN_IMAGE_MODELS[0];
        const strength = normalizeLoraStrength(lora.strength ?? lora.default_strength ?? 0.8);
        return `
            <div class="lora-row">
                <label class="lora-field">
                    <span>${tr('api.loraId')}</span>
                    <input value="${escapeAttr(lora.id || '')}" placeholder="${escapeAttr(tr('api.loraIdPlaceholder'))}" oninput="updateMsLora(${index}, 'id', this.value)">
                </label>
                <label class="lora-field">
                    <span>${tr('api.loraTargetModel')}</span>
                    <select onchange="updateMsLora(${index}, 'target_model', this.value)">${msLoraTargetOptions(target)}</select>
                </label>
                <label class="lora-field">
                    <span>${tr('api.loraDefaultStrength')}</span>
                    <input type="number" min="0" max="2" step="0.05" value="${strength}" oninput="updateMsLora(${index}, 'strength', this.value)">
                </label>
                <button class="icon-btn" type="button" onclick="removeMsLora(${index})" aria-label="删除 LoRA ${escapeAttr(lora.id || String(index + 1))}" title="${escapeAttr(tr('common.delete'))}"><i data-lucide="trash-2" class="w-4 h-4"></i></button>
            </div>
        `;
    }).join('');
    refreshIcons();
}
function addMsLora(){
    const item = provider();
    if(!item || item.id !== 'modelscope') return;
    item.ms_loras = Array.isArray(item.ms_loras) ? item.ms_loras : [];
    item.ms_loras.push({
        id:'',
        name:'',
        target_model: (item.image_models || [])[0] || MS_BUILTIN_IMAGE_MODELS[0],
        strength:0.8,
        enabled:true,
        note:''
    });
    renderMsLoras();
}
function updateMsLora(index, field, value){
    const item = provider();
    if(!item || item.id !== 'modelscope') return;
    item.ms_loras = Array.isArray(item.ms_loras) ? item.ms_loras : [];
    const lora = item.ms_loras[index];
    if(!lora) return;
    if(field === 'strength') lora.strength = normalizeLoraStrength(value);
    else lora[field] = value;
}
function removeMsLora(index){
    const item = provider();
    if(!item || item.id !== 'modelscope') return;
    item.ms_loras = Array.isArray(item.ms_loras) ? item.ms_loras : [];
    item.ms_loras.splice(index, 1);
    renderMsLoras();
}
function selectProvider(id){
    if(id !== selectedId && !confirmLeaveEditor()) return;
    if(isProviderTemporarilyHidden(providers.find(item => item.id === id))) return;
    syncEditor();
    selectedId = id;
    renderEditor();
}
function addProvider(){
    if(!confirmLeaveEditor()) return;
    syncEditor();
    let id = 'custom-api';
    let index = 2;
    while(providers.some(item => item.id === id)) id = `custom-api-${index++}`;
    providers.push({id, name:'API', base_url:'', protocol:'openai', image_request_mode:'openai', image_edit_route:'general', image_generation_endpoint:'', image_edit_endpoint:'', enabled:true, primary:false, image_models:[], chat_models:[], video_models:[], has_key:false, key_preview:''});
    selectedId = id;
    renderEditor();
}
async function addCliProvider(kind){
    if(!confirmLeaveEditor()) return;
    const preset = CLI_PROVIDER_PRESETS[kind];
    if(!preset) return;
    syncEditor();
    let item = providers.find(provider => provider.id === preset.id);
    if(!item) item = providers.find(provider => String(provider.protocol || '').toLowerCase() === preset.protocol);
    if(!item){
        item = {
            id:preset.id,
            name:preset.name,
            base_url:'',
            protocol:preset.protocol,
            image_request_mode:'openai',
            image_edit_route:'general',
            image_generation_endpoint:'',
            image_edit_endpoint:'',
            enabled:true,
            primary:false,
            image_models:[],
            chat_models:[],
            video_models:[],
            model_protocols:{},
            has_key:false,
            key_preview:''
        };
        providers.push(item);
    }
    item.id = preset.id;
    item.name = item.name || preset.name;
    item.base_url = '';
    item.protocol = preset.protocol;
    if(preset.protocol === 'jimeng'){
        item.image_models = unique([...(item.image_models || []).filter(model => !JIMENG_LEGACY_IMAGE_MODELS.has(String(model || '').trim())), ...JIMENG_DEFAULT_IMAGE_MODELS]);
        item.video_models = unique([...(item.video_models || []).filter(model => !JIMENG_LEGACY_VIDEO_MODELS.has(String(model || '').trim())), ...JIMENG_DEFAULT_VIDEO_MODELS]);
        item.chat_models = unique(item.chat_models || []);
    } else {
        applyCliProtocolDefaults(item, preset.protocol);
    }
    selectedId = item.id;
    renderProviderList();
    renderEditor();
    if(protocolInput) protocolInput.value = preset.protocol;
    const ok = await saveProviders();
    if(ok){
        selectedId = item.id;
        renderEditor();
        if(protocolInput) protocolInput.value = preset.protocol;
        setStatus(`${preset.name} 配置已保存；路径、登录与生成能力仍需分别验证。`, 'warning');
    }
}
async function deleteProvider(){
    if(providerSavePending) return;
    const item = provider();
    if(!item) return;
    if(!window.confirm(tr('api.confirmDeleteProvider'))) return;
    syncEditor();
    const previous = providers;
    const previousId = selectedId;
    const previousKeys = [keyInput, volcAkInput, volcSkInput].filter(Boolean).map(input => [input, input.value]);
    providers = providers.filter(p => p.id !== item.id);
    selectedId = providers[0]?.id || '';
    renderEditor();
    const ok = await saveProviders();
    if(!ok){
        const errorText = statusEl.textContent;
        providers = previous;
        selectedId = previousId;
        renderEditor();
        previousKeys.forEach(([input, value]) => { input.value = value; });
        setStatus(errorText, 'error');
    }
}
async function saveVolcengineAssetKeys(){
    const item = provider();
    if(!item || item.id !== 'volcengine') return;
    const ak = volcAkInput?.value.trim() || '';
    const sk = volcSkInput?.value.trim() || '';
    if(!ak && !sk){ alert('请输入火山素材库 AK 或 SK'); return; }
    syncEditor();
    const ok = await saveProviders();
    if(ok){
        if(volcAkInput) volcAkInput.value = '';
        if(volcSkInput) volcSkInput.value = '';
    }
}
async function clearVolcengineAssetKeys(){
    const item = provider();
    if(!item || item.id !== 'volcengine') return;
    if(!confirm('确认清除火山素材库 AK/SK？')) return;
    if(volcAkInput) volcAkInput.value = '';
    if(volcSkInput) volcSkInput.value = '';
    delete item.volcengine_access_key_id;
    delete item.volcengine_secret_access_key;
    item._clearVolcengineAccessKey = true;
    item._clearVolcengineSecretKey = true;
    const ok = await saveProviders();
    if(ok){
        if(volcAkInput) volcAkInput.value = '';
        if(volcSkInput) volcSkInput.value = '';
    }
}
function addModel(kind){
    const item = provider();
    const key = kind === 'image' ? 'image_models' : kind === 'video' ? 'video_models' : 'chat_models';
    item[key] = [...(item[key] || []), ''];
    renderModels(kind);
    if(kind === 'image') renderMsLoras();
}
function modelProtocolStillUsed(item, name){
    if(!item || !name) return false;
    const lists = ['image_models', 'chat_models', 'video_models'];
    return lists.some(k => Array.isArray(item[k]) && item[k].includes(name));
}
function sanitizeModelProtocols(item){
    const source = (item?.model_protocols && typeof item.model_protocols === 'object') ? item.model_protocols : {};
    const imageChatModels = new Set([...(item?.image_models || []), ...(item?.chat_models || [])].map(model => String(model || '').trim()).filter(Boolean));
    const out = {};
    Object.entries(source).forEach(([rawName, rawProto]) => {
        const name = String(rawName || '').trim();
        const proto = String(rawProto || '').trim().toLowerCase();
        if(!name) return;
        if(imageChatModels.has(name) && (proto === 'openai' || proto === 'gemini')){
            out[name] = proto;
        }
    });
    return out;
}
function applyAutoModelProtocols(item){
    if(!providerSupportsModelProtocol(item)) return;
    if(!item.model_protocols || typeof item.model_protocols !== 'object') item.model_protocols = {};
    [...(item.image_models || []), ...(item.chat_models || [])].forEach(model => {
        const name = String(model || '').trim();
        if(!name) return;
        if(name.toLowerCase().includes('gemini') && !item.model_protocols[name]){
            item.model_protocols[name] = 'gemini';
        }
    });
}
function updateModel(kind, index, value){
    const item = provider();
    const key = kind === 'image' ? 'image_models' : kind === 'video' ? 'video_models' : 'chat_models';
    const oldName = String(item[key][index] || '').trim();
    const newName = String(value || '').trim();
    item[key][index] = value;
    // 重命名时迁移该模型的协议覆盖
    if(item.model_protocols && typeof item.model_protocols === 'object' && oldName && oldName !== newName){
        if(Object.prototype.hasOwnProperty.call(item.model_protocols, oldName)){
            const proto = item.model_protocols[oldName];
            // 旧名称在其他列表里不再使用时才删除旧键
            const stillUsedElsewhere = (() => {
                const lists = ['image_models', 'chat_models', 'video_models'];
                return lists.some(k => Array.isArray(item[k]) && item[k].some((m, i) => !(k === key && i === index) && String(m || '').trim() === oldName));
            })();
            if(!stillUsedElsewhere) delete item.model_protocols[oldName];
            if(newName) item.model_protocols[newName] = proto;
        }
    }
    if(item.model_names && typeof item.model_names === 'object' && oldName && oldName !== newName){
        if(Object.prototype.hasOwnProperty.call(item.model_names, oldName)){
            const label = item.model_names[oldName];
            if(!modelProtocolStillUsed(item, oldName)) delete item.model_names[oldName];
            if(newName && label && label !== newName) item.model_names[newName] = label;
        }
    }
    if(kind === 'image') renderMsLoras();
}
function updateModelProtocol(kind, index, value){
    const item = provider();
    const key = kind === 'image' ? 'image_models' : kind === 'video' ? 'video_models' : 'chat_models';
    const name = String(item[key]?.[index] || '').trim();
    if(!name) return;
    if(!item.model_protocols || typeof item.model_protocols !== 'object') item.model_protocols = {};
    const proto = String(value || '').trim().toLowerCase();
    if(kind !== 'video' && (proto === 'openai' || proto === 'gemini')){
        item.model_protocols[name] = proto;
    } else {
        delete item.model_protocols[name];
    }
}
function removeModel(kind, index){
    const item = provider();
    const key = kind === 'image' ? 'image_models' : kind === 'video' ? 'video_models' : 'chat_models';
    const removed = String(item[key][index] || '').trim();
    item[key].splice(index, 1);
    // 清理不再使用的协议覆盖
    if(removed && item.model_protocols && typeof item.model_protocols === 'object' && !modelProtocolStillUsed(item, removed)){
        delete item.model_protocols[removed];
    }
    if(removed && item.model_names && typeof item.model_names === 'object' && !modelProtocolStillUsed(item, removed)){
        delete item.model_names[removed];
    }
    renderModels(kind);
    if(kind === 'image') renderMsLoras();
}
async function loadProviders(){
    setStatus(tr('api.loading'));
    try {
        const {data} = await requestJson('/api/providers', undefined, tr('api.loadFailed'));
        const previousId = selectedId;
        providers = Array.isArray(data.providers) ? data.providers : [];
        providerRevision = Number.isInteger(data.revision) ? data.revision : null;
        selectedId = visibleProviders().some(item => item.id === previousId)
            ? previousId : (sortedProviders()[0]?.id || '');
        renderEditor();
        rememberSavedProviders();
        setStatus('');
    } catch(err) {
        setStatus(degradationLabel(err, tr('api.loadFailed')));
    }
}
async function saveProviders(){
    if(providerSavePending) return false;
    if(!Number.isInteger(providerRevision)){
        setStatus(tr('api.reloadRequired'));
        return false;
    }
    syncEditor();
    if(!validateEditor() || !checkAutofillReview()) return false;
    providers.forEach(item => {
        // 已登记ID不再重新标准化，否则大小写变化会改变凭据归属。
        item.id = String(item.id || '');
        item.protocol = item.id === 'volcengine'
            ? 'volcengine'
            : API_PROTOCOLS.includes(String(item.protocol || '').toLowerCase()) ? String(item.protocol).toLowerCase() : 'openai';
        const isCliProtocol = CLI_PROTOCOLS.has(item.protocol);
        item.image_request_mode = normalizeImageRequestMode(
            item.id === 'modelscope' || item.id === 'volcengine' || isCliProtocol
                ? 'openai'
                : item.image_request_mode
        );
        item.image_edit_route = normalizeImageEditRoute(
            item.id === 'modelscope' || item.id === 'volcengine' || isCliProtocol
                ? 'general'
                : item.image_edit_route
        );
        if(isCliProtocol) applyCliProtocolDefaults(item, item.protocol);
        item.image_generation_endpoint = '';
        item.image_edit_endpoint = '';
        item.image_models = unique(item.image_models || []);
        item.chat_models = unique(item.chat_models || []);
        item.video_models = unique(item.video_models || []);
        applyAutoModelProtocols(item);
        item.model_protocols = sanitizeModelProtocols(item);
        const modelNameSource = (item.model_names && typeof item.model_names === 'object') ? item.model_names : {};
        const modelNameMap = {};
        [...item.image_models, ...item.chat_models, ...item.video_models].forEach(model => {
            const raw = String(model || '').trim();
            const label = String(modelNameSource[raw] || modelDisplayName(raw, item) || '').trim();
            if(raw && label && label !== raw) modelNameMap[raw] = label;
        });
        item.model_names = modelNameMap;
        item.ms_loras = (Array.isArray(item.ms_loras) ? item.ms_loras : []).map(lora => ({
            id:String(lora.id || '').trim(),
            name:String(lora.name || lora.id || '').trim(),
            target_model:String(lora.target_model || '').trim(),
            strength:normalizeLoraStrength(lora.strength ?? 0.8),
            enabled:lora.enabled !== false,
            note:String(lora.note || '').trim()
        })).filter(lora => lora.id && lora.target_model);
    });
    if(new Set(providers.map(item => item.id)).size !== providers.length){
        alert(tr('api.duplicateId'));
        return false;
    }
    setStatus(tr('api.saving'));
    providerSavePending = true;
    const heldControls = Array.from(document.querySelectorAll('.layout input, .layout select, .layout button'))
        .map(control => [control, control.disabled]);
    heldControls.forEach(([control]) => { control.disabled = true; });
    try {
        const {data} = await requestJson(`/api/providers?expected_version=${providerRevision}`, {
            method:'PUT',
            headers:{'Content-Type':'application/json'},
            body:JSON.stringify(providers.map(item => ({
                id:item.id,
                name:item.name,
                base_url:item.base_url,
                protocol:(item.id === 'modelscope') ? 'openai' : item.id === 'volcengine' ? 'volcengine' : (item.protocol || 'openai'),
                image_request_mode:item.image_request_mode || 'openai',
                image_edit_route:item.image_edit_route || 'general',
                image_generation_endpoint:item.image_generation_endpoint || '',
                image_edit_endpoint:item.image_edit_endpoint || '',
                enabled:item.enabled !== false,
                primary:false,
                image_models:item.image_models || [],
                chat_models:item.chat_models || [],
                video_models:item.video_models || [],
                video_protocol:item.video_protocol || null,
                model_names:(item.model_names && typeof item.model_names === 'object') ? item.model_names : {},
                model_protocols:(item.model_protocols && typeof item.model_protocols === 'object') ? item.model_protocols : {},
                ms_loras:item.id === 'modelscope' ? (item.ms_loras || []) : [],
                ms_defaults_version:item.id === 'modelscope' ? (item.ms_defaults_version || 1) : 0,
                volcengine_project_name:item.id === 'volcengine' ? (item.volcengine_project_name || VOLCENGINE_DEFAULT_PROJECT_NAME) : '',
                volcengine_region:item.id === 'volcengine' ? (item.volcengine_region || VOLCENGINE_DEFAULT_REGION) : '',
                volcengine_access_key_id:item.volcengine_access_key_id || undefined,
                volcengine_secret_access_key:item.volcengine_secret_access_key || undefined,
                api_key:item.api_key || undefined,
                wallet_api_key:item.wallet_api_key || undefined,
                clear_key:item._clearKey === true,
                clear_wallet_key:item._clearWalletKey === true,
                clear_volcengine_access_key_id:item._clearVolcengineAccessKey === true,
                clear_volcengine_secret_access_key:item._clearVolcengineSecretKey === true
            })))
        }, tr('api.saveFailed'));
        providers = data.providers || providers;
        providerRevision = data.revision;
        providers.forEach(item => {
            delete item.api_key;
            delete item.wallet_api_key;
            delete item.volcengine_access_key_id;
            delete item.volcengine_secret_access_key;
            delete item._clearKey;
            delete item._clearWalletKey;
            delete item._clearVolcengineAccessKey;
            delete item._clearVolcengineSecretKey;
        });
        selectedId = provider()?.id || providers[0]?.id || '';
        renderEditor();
        rememberSavedProviders();
        document.getElementById('autofillAck').checked = false;
        document.getElementById('autofillReview').hidden = true;
        setStatus(tr('api.saved'), 'success');
        // 广播变更，画布等其他 iframe 立即重新拉取最新平台/模型列表
        broadcastStudioApiChange('providers-changed');
        return true;
    } catch(err) {
        setStatus(err.code === 'VERSION_CONFLICT' ? tr('api.versionConflict') : (err.message || tr('api.saveFailed')), 'error');
        return false;
    } finally {
        providerSavePending = false;
        heldControls.forEach(([control, disabled]) => { control.disabled = disabled; });
    }
}
function escapeHtml(str){
    return String(str || '').replace(/[&<>"']/g, s => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[s]));
}
function escapeAttr(str){ return escapeHtml(str).replace(/`/g, '&#96;'); }
const pickerFloatingSurfaces = Object.freeze([
    [document.getElementById('modelPickerOverlay'), document.getElementById('openPickerBtn'), closeModelPicker],
    [jimengHelpOverlay, document.getElementById('openJimengHelpBtn'), closeJimengHelp],
    [codexHelpOverlay, document.getElementById('openCodexHelpBtn'), closeCodexHelp],
    [geminiCliHelpOverlay, document.getElementById('openGeminiHelpBtn'), closeGeminiCliHelp],
]);
function registerPickerFloatingSurfaces(){
    const dismissal = window.FloatingDismissal;
    if(!dismissal) return;
    pickerFloatingSurfaces.forEach(([overlay, trigger, close]) => {
        if(!overlay || !trigger) return;
        dismissal.register(overlay, {
            kind: 'modal',
            content: '[data-floating-content]',
            trigger,
            close,
        });
    });
}
function closePickerFromControl(event){
    if(!(event.target instanceof Element)) return;
    const control = event.target.closest('button[data-picker-close]');
    if(!control) return;
    const overlay = control.closest('[data-floating-surface]');
    if(!overlay) return;
    window.FloatingDismissal?.closeSurface(overlay);
}
document.addEventListener('click', closePickerFromControl);
document.addEventListener('input', event => {
    if(event.target.matches('input:not([type="checkbox"])')) document.getElementById('autofillAck').checked = false;
});
window.addEventListener('beforeunload', event => {
    if(!hasUnsavedChanges()) return;
    event.preventDefault();
    event.returnValue = '';
});
// 父壳用Ajax切页不会触发iframe的beforeunload；同源导航前同样保护当前草稿。
try {
    if(window.parent !== window){
        const parentDocument = window.parent.document;
        const protectParentNavigation = event => {
            if(!window.frameElement?.isConnected){ parentDocument.removeEventListener('click', protectParentNavigation, true); return; }
            const section = event.target.closest('button[data-section]');
            if(section && section.dataset.section !== 'api-settings'){
                if(!confirmLeaveEditor()){ event.preventDefault(); event.stopImmediatePropagation(); }
                return;
            }
            const link = event.target.closest('a[href]');
            if(!link || link.target === '_blank' || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
            const url = new URL(link.href, parentDocument.location.href);
            if(url.origin !== location.origin || !url.pathname.startsWith('/static/pages/')) return;
            if(!confirmLeaveEditor()){ event.preventDefault(); event.stopImmediatePropagation(); }
        };
        parentDocument.addEventListener('click', protectParentNavigation, true);
        window.addEventListener('pagehide', () => parentDocument.removeEventListener('click', protectParentNavigation, true));
    }
} catch(_) { /* 非同源宿主只使用浏览器退出保护。 */ }
registerPickerFloatingSurfaces();
window.addEventListener('message', event => {
    if(event.data?.type === 'studio-theme' && window.StudioTheme) window.StudioTheme.set(event.data.theme);
    if(event.data?.type === 'studio-lang' && window.StudioI18n) {
        window.StudioI18n.set(event.data.lang);
        renderEditor();
    }
});
window.addEventListener('studio-lang-change', () => {
    renderEditor();
});
window.onload = () => {
    if(window.StudioTheme) window.StudioTheme.apply();
    if(window.StudioI18n) window.StudioI18n.apply();
    loadProviders();
    // 平台名输入时实时预览生成的 ID
    if(nameInput) nameInput.addEventListener('input', updateIdPreview);
    if(protocolInput) protocolInput.addEventListener('change', updateProtocolFromInput);
    if(baseInput) baseInput.addEventListener('input', () => updateApimartDomesticHint());
    if(baseInput) baseInput.addEventListener('blur', normalizeProviderBaseInput);
    if(imageRequestModeInput) imageRequestModeInput.addEventListener('change', () => {
        const item = provider();
        if(!item) return;
        item.image_request_mode = normalizeImageRequestMode(imageRequestModeInput.value);
    });
    if(imageEditRouteInput) imageEditRouteInput.addEventListener('change', () => {
        const item = provider();
        if(!item) return;
        item.image_edit_route = normalizeImageEditRoute(imageEditRouteInput.value);
    });
    [keyInput].forEach(input => {
        if(input) input.addEventListener('input', () => {
            if(input === keyInput) updateApimartDomesticHint();
        });
    });
    // 首屏静态图标渲染：loadProviders() 为异步，此处统一刷新一次以替换初始 HTML 中的 data-lucide 占位
    refreshIcons();
};
