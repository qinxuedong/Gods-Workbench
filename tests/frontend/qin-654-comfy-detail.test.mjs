import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import path from 'node:path';
import test from 'node:test';
import {fileURLToPath, pathToFileURL} from 'node:url';
import {createAssetViewHost} from './qin-602-asset-view-harness.mjs';

const HERE = path.dirname(fileURLToPath(import.meta.url));

async function loadPreview(host) {
    const modulePath = path.join(host.tree, 'web/js/modules/asset-manager/preview.js');
    const statePath = path.join(host.tree, 'web/js/modules/asset-manager/state.js');
    const [preview, state] = await Promise.all([import(pathToFileURL(modulePath).href), import(pathToFileURL(statePath).href)]);
    return {...preview, actions: state.GW_DEFERRED.preview};
}

function comfyImage(overrides = {}) {
    return {
        id: 'asset-qin654',
        name: 'generated.png',
        kind: 'image',
        metadata: {generation: {prompt: 'cinematic landscape', comfyui: {source: 'embedded', has_workflow: true}}},
        ...overrides,
    };
}

function click(document, target) {
    document.dispatchEvent({
        type: 'click', target,
        preventDefault() {}, stopPropagation() {}, stopImmediatePropagation() {},
    });
}

test('QIN-654 AC1：两个详情渲染入口仅对带 ComfyUI 标记的图像显示按钮，且在标签上方', async () => {
    const host = await createAssetViewHost({tab: null});
    try {
        const preview = await loadPreview(host);
        const image = comfyImage();
        const viewer = preview.renderDetailViewerInspectorContent(image, 'image', ['tag'], [], '/asset.png', '', 'registry');
        const shared = preview.actions.renderComfyAssetActions(image, 'image');
        assert.match(viewer, /data-comfy-prompt-open="asset-qin654"/);
        assert.match(viewer, /data-comfy-workflow-open="asset-qin654"/);
        assert.ok(viewer.indexOf('asset-detail-comfy-actions') < viewer.indexOf('asset-detail-registry-meta'));
        assert.match(shared, /data-comfy-prompt-open="asset-qin654"/);
        assert.doesNotMatch(preview.actions.renderComfyAssetActions(comfyImage({kind: 'video'}), 'video'), /data-comfy-/);
        assert.doesNotMatch(preview.actions.renderComfyAssetActions({id: 'plain', metadata: {}}, 'image'), /data-comfy-/);

        const assetManager = readFileSync(new URL('../../web/js/modules/asset-manager.js', import.meta.url), 'utf8');
        const registryDetail = assetManager.slice(assetManager.indexOf('function renderRegistryDetail()'), assetManager.indexOf('\nfunction ', assetManager.indexOf('function renderRegistryDetail()') + 1));
        assert.match(registryDetail, /gwCall\('preview', 'renderComfyAssetActions'/);
        assert.ok(registryDetail.indexOf('${comfyActions}') < registryDetail.indexOf("${renderRegistryEditableTags(item, 'side')}"));
    } finally {
        host.dispose();
    }
});

test('QIN-654 AC2：提示词浮窗安全区分正负向、标注截断并复制可用文本', async () => {
    const host = await createAssetViewHost({tab: null});
    try {
        const preview = await loadPreview(host);
        const image = comfyImage({metadata: {generation: {
            prompt: '<img src=x onerror=alert(1)>', negative_prompt: 'blurry', truncated: true,
            comfyui: {source: 'embedded', has_workflow: true},
        }}});
        preview.actions.renderComfyAssetActions(image, 'image');
        const trigger = {dataset: {comfyPromptOpen: image.id}, closest: selector => selector === '[data-comfy-prompt-open]' ? trigger : null};
        click(host.document, trigger);
        const dialog = host.document.querySelectorAll('.hw-modal-backdrop').find(node => node.dataset.floatingSurface === 'comfy-prompt-dialog');
        assert.ok(dialog, '提示词浮窗应显示');
        assert.match(dialog.innerHTML, /正向提示词/);
        assert.match(dialog.innerHTML, /反向提示词/);
        assert.match(dialog.innerHTML, /可能被截断/);
        assert.match(dialog.innerHTML, /&lt;img src=x onerror=alert\(1\)&gt;/);
        assert.doesNotMatch(dialog.innerHTML, /<img src=x/);

        let copied = '';
        navigator.clipboard.writeText = async value => { copied = value; };
        const copy = dialog.querySelector('[data-comfy-prompt-copy]');
        click(host.document, copy);
        await new Promise(resolve => setImmediate(resolve));
        assert.match(copied, /正向提示词：\n<img src=x onerror=alert\(1\)>/);
        assert.match(copied, /反向提示词：\nblurry/);
    } finally {
        host.dispose();
    }
});

test('QIN-654 AC3：无完整工作流时不弹确认；确认后只传资产 ID 与一次性 nonce 并跳转', async () => {
    const host = await createAssetViewHost({tab: null});
    try {
        const session = new Map();
        Object.defineProperty(globalThis, 'sessionStorage', {configurable: true, writable: true, value: {
            getItem: key => session.get(key) ?? null,
            setItem: (key, value) => session.set(key, String(value)),
            removeItem: key => session.delete(key),
        }});
        globalThis.GWAuthGate = {isAuthenticated: () => true};
        globalThis.HardwareDeck = {authState: {authenticated: true, auth_mode: 'local_account', principal: {subject: 'qin-user'}}};
        const preview = await loadPreview(host);

        const noGraph = comfyImage({metadata: {generation: {prompt: 'prompt', comfyui: {source: 'embedded', has_workflow: false, has_prompt_graph: true}}}});
        preview.actions.renderComfyAssetActions(noGraph, 'image');
        const noGraphButton = {dataset: {comfyWorkflowOpen: noGraph.id}, closest: selector => selector === '[data-comfy-workflow-open]' ? noGraphButton : null};
        click(host.document, noGraphButton);
        assert.equal(host.document.querySelectorAll('.hw-modal-backdrop').some(node => node.dataset.floatingSurface === 'comfy-workflow-confirm'), false);

        const image = comfyImage();
        preview.actions.renderComfyAssetActions(image, 'image');
        const workflowButton = {dataset: {comfyWorkflowOpen: image.id}, closest: selector => selector === '[data-comfy-workflow-open]' ? workflowButton : null};
        click(host.document, workflowButton);
        let dialog = host.document.querySelectorAll('.hw-modal-backdrop').find(node => node.dataset.floatingSurface === 'comfy-workflow-confirm');
        assert.ok(dialog, '有完整工作流时应先二次确认');
        assert.match(dialog.innerHTML, /是否加载工作流/);
        const previousUrl = location.href;
        click(host.document, dialog.querySelector('[data-comfy-dialog-close]'));
        assert.equal(location.href, previousUrl, '取消关闭不应导航');
        assert.equal(session.has('gw.workflow.assetImport.pending.v1'), false, '取消关闭不应写入待导入记录');

        click(host.document, workflowButton);
        dialog = host.document.querySelectorAll('.hw-modal-backdrop').find(node => node.dataset.floatingSurface === 'comfy-workflow-confirm');
        const confirm = dialog.querySelector('[data-comfy-workflow-confirm]');
        click(host.document, confirm);

        const url = new URL(location.href);
        assert.equal(url.pathname, '/static/pages/workflow.html');
        assert.equal(url.searchParams.get('import_asset_id'), image.id);
        assert.ok(url.searchParams.get('import_nonce'));
        const pending = JSON.parse(session.get('gw.workflow.assetImport.pending.v1'));
        assert.equal(pending.asset_id, image.id);
        assert.equal(pending.principal_key, JSON.stringify(['local_account', 'qin-user']));
        assert.ok(pending.expires_at > Date.now());
        assert.equal(url.searchParams.has('prompt'), false);
        assert.equal(url.searchParams.has('workflow'), false);
    } finally {
        host.dispose();
    }
});

test('QIN-654 AC3：嵌入页仅从同源顶层读取当前认证主体', async () => {
    const host = await createAssetViewHost({tab: null});
    const saved = new Map(['top', 'GWAuthGate', 'HardwareDeck', 'sessionStorage'].map(key => [key, Object.getOwnPropertyDescriptor(globalThis, key)]));
    const restore = () => {
        for (const [key, descriptor] of saved) {
            if (descriptor) Object.defineProperty(globalThis, key, descriptor);
            else delete globalThis[key];
        }
    };
    try {
        const session = new Map();
        globalThis.sessionStorage = {
            getItem: key => session.get(key) ?? null,
            setItem: (key, value) => session.set(key, String(value)),
            removeItem: key => session.delete(key),
        };
        delete globalThis.GWAuthGate;
        delete globalThis.HardwareDeck;
        const makeTop = origin => ({
            location: {origin, href: `${origin}/static/pages/assets.html`},
            GWAuthGate: {isAuthenticated: () => true},
            HardwareDeck: {authState: {authenticated: true, auth_mode: 'local_account', principal: {subject: 'shell-user'}}},
        });
        const crossOriginTop = makeTop('https://other.example');
        globalThis.top = crossOriginTop;
        const preview = await loadPreview(host);
        const image = comfyImage();
        preview.actions.renderComfyAssetActions(image, 'image');
        const workflowButton = {dataset: {comfyWorkflowOpen: image.id}, closest: selector => selector === '[data-comfy-workflow-open]' ? workflowButton : null};
        click(host.document, workflowButton);
        assert.equal(host.document.querySelectorAll('.hw-modal-backdrop').some(node => node.dataset.floatingSurface === 'comfy-workflow-confirm'), false,
            '跨源顶层认证状态不能授权导入');

        const sameOriginTop = makeTop(location.origin);
        globalThis.top = sameOriginTop;
        click(host.document, workflowButton);
        const dialog = host.document.querySelectorAll('.hw-modal-backdrop').find(node => node.dataset.floatingSurface === 'comfy-workflow-confirm');
        assert.ok(dialog, '同源顶层认证状态应允许显示确认框');
        click(host.document, dialog.querySelector('[data-comfy-workflow-confirm]'));

        const pending = JSON.parse(session.get('gw.workflow.assetImport.pending.v1'));
        assert.equal(pending.principal_key, JSON.stringify(['local_account', 'shell-user']));
        assert.equal(new URL(sameOriginTop.location.href).pathname, '/static/pages/workflow.html');
        assert.equal(new URL(sameOriginTop.location.href).searchParams.get('import_asset_id'), image.id);
    } finally {
        restore();
        host.dispose();
    }
});
